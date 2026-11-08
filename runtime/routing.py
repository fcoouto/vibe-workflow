"""Smart Routing task profiles and deterministic ranking.

An empty ranking (``no_recommendation``) is never a refusal: callers fall through to the
deterministic admission path or the user's explicit worker choices. Only admission can block.

Profiling and ranking are pure: no network, subprocess or model call happens here except through
the caller-supplied, optional ``jev`` enrichment callable. ``admit`` is the one exception: it
reserves and records through the local ledger in one short transaction of pure arithmetic. Nothing
here grants single-use dispatch authority; that is a later layer.
"""
import hashlib
import json

import math

from runtime import routing_allocator as allocator, routing_settings
from runtime.usage_budget import MAX_INTEGER, ORG, WIN, BudgetError, Ledger

CONTRACT_VERSION = 1
ROLES = ('worker', 'review', 'coordinator', 'check')
COMPLEXITY_BANDS = ('low', 'medium', 'high', 'critical')
RISK_BANDS = ('low', 'medium', 'high', 'critical')
REVIEW_POLICIES = ('none', 'self', 'independent', 'strict')
UNCERTAINTY = ('low', 'medium', 'high')
PROVENANCE = ('coordinator', 'metadata', 'floor', 'jev')
# Conservative values for fields nobody supplied: higher bands, stricter review.
FLOORS = dict(task_family='general', complexity_band='high', risk_band='high', review_policy='independent')
# Requirement floors implied by a write claim, independent of any supplied value.
WRITE_FLOORS = dict(complexity_band='medium', risk_band='medium')
SEMANTIC_FIELDS = ('task_family', 'complexity_band', 'risk_band')
SUPPLIED_FIELDS = ('task_family', 'complexity_band', 'risk_band', 'context_tokens', 'tools', 'review_policy',
                   'uncertainty')
SUPPLIED_REFERENCES = ('scope_ref', 'contract_version', 'semantic_digest')
_ORDERS = dict(complexity_band=COMPLEXITY_BANDS, risk_band=RISK_BANDS, review_policy=REVIEW_POLICIES)
MAX_TOKENS = 10**9
# Same bounds as the worker brief and claim limits in runtime/delegation.py.
MAX_TEXT = 8192
MAX_CLAIMS = 64


class ProfileUnavailable(ValueError):
    """Floors cannot be established, for example an unknown role or missing scope."""


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= 64 and value.isprintable() and '|' not in value


def _valid(field, value):
    if field == 'task_family':
        return _name(value)
    if field in _ORDERS:
        return value in _ORDERS[field]
    if field == 'context_tokens':
        return type(value) is int and 0 <= value <= MAX_TOKENS
    if field == 'tools':
        return isinstance(value, list) and len(value) <= 64 and all(_name(item) for item in value)
    if field == 'uncertainty':
        return value in UNCERTAINTY
    return False


def validate_supplied_profile(value):
    """Structure check for the optional coordinator-written profile; invalid values raise ValueError."""
    if not isinstance(value, dict) or set(value) - set(SUPPLIED_FIELDS) - set(SUPPLIED_REFERENCES):
        raise ValueError('Invalid task profile fields')
    for field in SUPPLIED_FIELDS:
        if value.get(field) is not None and not _valid(field, value[field]):
            raise ValueError('Invalid task profile ' + field)
    for field in ('scope_ref', 'semantic_digest'):
        if value.get(field) is not None and not (isinstance(value[field], str) and 0 < len(value[field]) <= 1024):
            raise ValueError('Invalid task profile ' + field)
    if value.get('contract_version') is not None and type(value['contract_version']) is not int:
        raise ValueError('Invalid task profile contract_version')
    return value


def _digest(value):
    try:
        data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    except (TypeError, ValueError, RecursionError):
        raise ProfileUnavailable('profile_unavailable: unhashable job content') from None
    return hashlib.sha256(data).hexdigest()


def _claims(job):
    claims = job.get('claims')
    if claims is None:
        return []
    if not isinstance(claims, list) or len(claims) > MAX_CLAIMS or not all(isinstance(c, dict) for c in claims):
        raise ProfileUnavailable('profile_unavailable: invalid scope claims')
    return sorted((str(c.get('kind')), str(c.get('key')), str(c.get('mode'))) for c in claims)


def _bounded_text(job):
    for field in ('objective', 'context'):
        value = job.get(field)
        if value is not None and not (isinstance(value, str) and len(value) <= MAX_TEXT):
            raise ProfileUnavailable('profile_unavailable: invalid ' + field)


def _floors(job):
    """Requirement floors: explicit job floors plus those implied by write scope. Never lowered."""
    floors = {}
    explicit = job.get('requirement_floors') or {}
    if not isinstance(explicit, dict) or set(explicit) - set(_ORDERS) or \
            any(explicit[field] not in _ORDERS[field] for field in explicit):
        raise ProfileUnavailable('profile_unavailable: unreadable requirement floors')
    floors.update(explicit)
    if any(mode == 'write' for _, _, mode in _claims(job)):
        for field, band in WRITE_FLOORS.items():
            floors[field] = _higher(field, floors.get(field), band)
    return floors


def _higher(field, left, right):
    order = _ORDERS[field]
    candidates = [item for item in (left, right) if item in order]
    return max(candidates, key=order.index) if candidates else None


def _tools(job):
    tools = job.get('tools')
    return sorted({item for item in tools if _name(item)}) if isinstance(tools, list) else []


def semantic_digest(job):
    """Digest of objective, role, scope/context, tools, requirement floors and contract version only."""
    _bounded_text(job)
    return _digest(dict(version=CONTRACT_VERSION, objective=job.get('objective'), role=job.get('role'),
                        scope_ref=job.get('scope_ref'), claims=_claims(job), context=job.get('context'),
                        tools=_tools(job), floors=_floors(job)))


def row_key(role, task_family, complexity_band, risk_band):
    """Stable string key for a strategy-table row."""
    parts = (role, task_family, complexity_band, risk_band)
    if not all(isinstance(part, str) and part and '|' not in part for part in parts):
        raise ValueError('Invalid strategy row key')
    return '|'.join(parts)


def _estimate_tokens(job):
    return (len(str(job.get('objective') or '')) + len(str(job.get('context') or ''))) // 4


def _usable_supplied(job, digest, reasons):
    supplied = job.get('profile')
    if supplied is not None and not isinstance(supplied, dict):
        reasons.append('profile_invalid')
    if not isinstance(supplied, dict):
        return {}
    if set(supplied) - set(SUPPLIED_FIELDS) - set(SUPPLIED_REFERENCES):
        reasons.append('profile_invalid')
        return {}
    # Individually invalid fields are dropped later and take floors; they do not discard valid siblings.
    if supplied.get('semantic_digest') is not None and supplied.get('scope_ref') is None:
        reasons.append('profile_unbound')
        return {}
    if supplied.get('scope_ref') not in (None, job.get('scope_ref')):
        reasons.append('profile_scope_mismatch')
        return {}
    if supplied.get('contract_version') not in (None, CONTRACT_VERSION):
        reasons.append('profile_contract_mismatch')
        return {}
    if supplied.get('semantic_digest') not in (None, digest):
        reasons.append('profile_stale')
        return {}
    return supplied


def _jev_view(job, fields):
    return dict(job_id=job.get('id'), role=job.get('role'), objective=job.get('objective'),
                context=job.get('context'), scope=_claims(job), tools=_tools(job), unresolved=list(fields))


def ensure_profile(job, *, jev=None):
    """Return a TaskProfile for ``job`` without mutating it.

    Complete coordinator-supplied profiles never call ``jev``. Gaps take
    deterministic floors; ``jev`` (optional) is asked once for unresolved
    semantic fields. No supplied or enriched value can lower a requirement floor.
    """
    if not isinstance(job, dict) or not isinstance(job.get('role'), str) or job.get('role') not in ROLES:
        raise ProfileUnavailable('profile_unavailable: unknown role')
    if not isinstance(job.get('scope_ref'), str) or not job['scope_ref']:
        raise ProfileUnavailable('profile_unavailable: no scope')
    digest = semantic_digest(job)
    reasons = []
    supplied = _usable_supplied(job, digest, reasons)
    values, provenance = {}, {}
    for field in ('task_family', *_ORDERS):
        if _valid(field, supplied.get(field)):
            values[field], provenance[field] = supplied[field], 'coordinator'
    unresolved = [field for field in SEMANTIC_FIELDS if field not in values]
    enriched = False
    if unresolved and callable(jev):
        try:
            answer = jev(_jev_view(job, unresolved), tuple(unresolved))
        except Exception:
            answer = None
        if isinstance(answer, dict):
            for field in unresolved:
                if _valid(field, answer.get(field)):
                    values[field], provenance[field] = answer[field], 'jev'
                    enriched = True
    gaps = [field for field in ('task_family', *_ORDERS) if field not in values]
    for field in gaps:
        values[field], provenance[field] = FLOORS[field], 'floor'
    for field, floor in _floors(job).items():
        raised = _higher(field, values[field], floor)
        if raised != values[field]:
            values[field], provenance[field] = raised, 'floor'
    supplied_tools = supplied.get('tools') if _valid('tools', supplied.get('tools')) else []
    tools = sorted(set(_tools(job)) | set(supplied_tools))
    tools_source = 'metadata' if _tools(job) else 'coordinator' if supplied_tools else 'floor'
    measured = job.get('context_tokens_measured')
    if type(measured) is int and 0 <= measured <= MAX_TOKENS:
        context, context_source = dict(tokens=measured, kind='measured'), 'metadata'
    elif _valid('context_tokens', supplied.get('context_tokens')):
        context, context_source = dict(tokens=supplied['context_tokens'], kind='estimated'), 'coordinator'
    else:
        context, context_source = dict(tokens=_estimate_tokens(job), kind='estimated'), 'floor'
    uncertainty = 'high' if gaps else 'medium' if enriched else supplied.get('uncertainty') if _valid('uncertainty', supplied.get('uncertainty')) else 'low'
    return dict(contract_version=CONTRACT_VERSION, job_id=job.get('id'), role=job['role'], scope_ref=job['scope_ref'],
                task_family=values['task_family'], complexity_band=values['complexity_band'],
                risk_band=values['risk_band'], review_policy=values['review_policy'], context=context,
                tools=tools, uncertainty=uncertainty, uncertain_fields=gaps, reasons=reasons,
                provenance=dict(provenance, context=context_source, tools=tools_source),
                semantic_digest=digest)


CONSTRAINT_FIELDS = ('allowed_models', 'excluded_models', 'allowed_efforts', 'allowed_harnesses',
                     'allowed_strategy_ids')


def resolve_row(table, profile):
    """Pick the table row for a profile: exact, then the nearest stricter band, then the strongest row.

    Returns ``(strategies, reason)``. An empty list (``no_recommendation``, never a refusal) means no
    qualified row at or above the profile's bands exists for the role. Bands only rise: a weaker row is never used, even as the conservative fallback.
    """
    rows = table.get('rows') if isinstance(table, dict) else None
    if not isinstance(rows, dict) or not rows:
        return [], 'no_recommendation'
    role, family = profile['role'], profile['task_family']
    c, r = COMPLEXITY_BANDS.index(profile['complexity_band']), RISK_BANDS.index(profile['risk_band'])
    exact = rows.get(row_key(role, family, profile['complexity_band'], profile['risk_band']))
    if exact:
        return exact, 'ranking_exact'
    stricter, strongest = [], []
    for name, strategies in rows.items():
        parts = name.split('|')
        if len(parts) != 4 or parts[0] != role or not strategies or \
                parts[2] not in COMPLEXITY_BANDS or parts[3] not in RISK_BANDS:
            continue
        ci, ri = COMPLEXITY_BANDS.index(parts[2]), RISK_BANDS.index(parts[3])
        if ci < c or ri < r:
            continue  # A cheaper row below the task's floors is never eligible.
        strongest.append((ci + ri, name, strategies))
        if parts[1] == family:
            stricter.append((ci + ri, ci, ri, name, strategies))
    if stricter:
        return min(stricter, key=lambda item: item[:4])[4], 'ranking_stricter_band'
    if strongest:
        return max(strongest, key=lambda item: (item[0], [-ord(ch) for ch in item[1]]))[2], 'ranking_conservative'
    return [], 'no_recommendation'


def _narrow(strategies, constraints):
    allowed = {field: set(constraints[field]) for field in ('allowed_models', 'allowed_efforts', 'allowed_harnesses',
                                                            'allowed_strategy_ids') if field in constraints}
    keys = dict(allowed_models='model', allowed_efforts='effort', allowed_harnesses='harness',
                allowed_strategy_ids='strategy_id')
    excluded = set(constraints.get('excluded_models', ()))
    return [item for item in strategies if item['model'] not in excluded and
            all(item[keys[field]] in values for field, values in allowed.items())]


def rank(profile, table, constraints):
    """Order qualified strategies for a profile from the compiled table.

    Pure lookup: no network, subprocess or model call, no funding or launch
    authority. Constraints can only remove candidates; they never add one or
    trigger a wider search. Empty ``strategy_ids`` carry ``no_recommendation``: it is a missing
    suggestion, not a block, and callers continue on the deterministic path or explicit choices.
    ``review_policy`` and ``scope_ref`` are echoed so callers can form the cache key
    (profile digest, table version, review policy, route scope).
    """
    constraints = constraints or {}
    if not isinstance(constraints, dict) or set(constraints) - set(CONSTRAINT_FIELDS):
        raise ValueError('Unsupported ranking constraint')
    for field in CONSTRAINT_FIELDS:
        if field in constraints and not (isinstance(constraints[field], (list, tuple)) and
                                         all(isinstance(item, str) for item in constraints[field])):
            raise ValueError('Invalid ranking constraint ' + field)
    strategies, reason = resolve_row(table, profile)
    reasons = [reason]
    if strategies:
        tokens = (profile.get('context') or {}).get('tokens')
        if type(tokens) is int:
            fitting = [item for item in strategies if item.get('context_limit') is None or item['context_limit'] >= tokens]
            if len(fitting) != len(strategies):
                reasons.append('context_exceeds_limit' if not fitting else 'context_filtered')
            strategies = fitting
    if strategies and constraints:
        narrowed = _narrow(strategies, constraints)
        reasons.append('constraints_applied' if narrowed else 'constraints_exclude_all')
        strategies = narrowed
    if not strategies and 'no_recommendation' not in reasons:
        reasons.append('no_recommendation')
    if not isinstance(table, dict) or not table.get('rows'):
        reasons.append('table_empty')
    return dict(strategy_ids=[item['strategy_id'] for item in strategies], reason_codes=reasons,
                table_version=table.get('version') if isinstance(table, dict) else None,
                profile_digest=profile.get('semantic_digest'), review_policy=profile.get('review_policy'),
                scope_ref=profile.get('scope_ref'))


# ---------------------------------------------------------------------------------------------------
# Admission (spec 6.4, 7.1, 7.2, 7.3, 7.4 and 8)
# ---------------------------------------------------------------------------------------------------
# Billing class -> funding class (A12). purchased_usage and ineligible routes never become candidates.
FUNDING_OF = dict(included_subscription='subscription', api_grant='api', api_prepaid='api', gateway='api')
DIRECT_API = ('api_grant', 'api_prepaid')
FUNDED = DIRECT_API + ('gateway',)  # dollar routes: funded only by an S1 organization in the ledger
ROUTE_IDENTITY = ('provider', 'model', 'effort', 'harness')
SECONDS_PER_HOUR = 3600
DEFAULT_MINIMUM_HORIZON = 900
# Snapshot bounds checked before any arithmetic. Identifiers share the ledger's name limit; numbers are finite
# and within MAX_INTEGER (the ledger's micro-unit bound); demands and request bounds are integers.
MAX_ID = 256
MAX_ROUTES = 64
MAX_POOLS = 64
MAX_WINDOWS = 8
MAX_STRATEGIES = 256
MAX_REASONS = 16


def _pending(reason, reasons=()):
    return dict(status='pending', reason_code=reason, reason_codes=list(dict.fromkeys([reason, *reasons])))


def _window_key(pool, window):
    return '%s%s/%s' % (WIN, pool, window)


def _funding(data, pool):
    """(organization, revision) funding a dollar pool, or None when no enabled organization funds it.

    Every window must name the same organization and revision, and the user must have enabled that organization
    in the ledger: collector numbers alone never fund dollars, and routing never creates an organization."""
    named = {(window.get('organization'), window.get('organization_revision')) for window in pool['windows']}
    if len(named) != 1:
        return None
    organization, revision = named.pop()
    org = data['organizations'].get(organization)
    if org is None or org.get('disabled', False) or type(revision) is not int:
        return None
    return organization, revision


def _is_bool(value):
    return type(value) is bool


def _number(value):
    return type(value) in (int, float) and value == value and abs(value) != float('inf')


def _incomplete(route, pool):
    """Missing or non-boolean health keys make a route unknown, and unknown is never healthy."""
    cls = route.get('billing_class')
    keys = ['capable', 'permitted'] + (['binding_enforceable'] if cls == 'included_subscription' else [])
    if cls == 'gateway':
        keys.append('integration_enabled')
    if cls in DIRECT_API or cls == 'gateway':
        keys.append('funding_verified')
    if not all(_is_bool(route.get(key)) for key in keys):
        return True
    if not isinstance(route.get('demand'), dict) or not all(
            isinstance(route.get(key), str) and route[key] for key in ('route', 'pool', 'account')):
        return True
    credential = route.get('credential')
    if (cls in DIRECT_API or cls == 'gateway') and credential is not None and \
            (not isinstance(credential, dict) or not isinstance(credential.get('state'), str)):
        return True
    for window in pool['windows']:
        if 'id' not in window or 'limit' not in window or 'used' not in window or not _is_bool(window.get('fresh')):
            return True
    return False


def _invalid(pool):
    """Present-but-unusable numbers: a zero or non-numeric limit, negative or non-numeric use."""
    for window in pool['windows']:
        limit, used = window['limit'], window['used']
        if not (_number(limit) and limit > 0) or not (used is None or (_number(used) and used >= 0)):
            return True
        if not (_number(window.get('protected', 0)) and window.get('protected', 0) >= 0):
            return True
    return False


def _credential_reasons(route, scope, settings):
    """Spec 6.4: enablement, secure binding readiness, scope match. Gateways use only their own gate."""
    reasons = []
    cls = route['billing_class']
    if cls in DIRECT_API and settings['api_usage_enabled'] is not True:
        reasons.append('api_usage_disabled')
    if cls == 'gateway' and route.get('integration_enabled') is not True:
        reasons.append('integration_disabled')
    if cls in DIRECT_API or cls == 'gateway':
        credential = route.get('credential')
        if credential is None or credential.get('state') != 'ready':
            reasons.append('credential_unavailable')
        elif credential.get('scope') != scope:
            reasons.append('credential_scope_mismatch')
        if route.get('funding_verified') is not True:
            reasons.append('funding_unverified')
    elif route.get('binding_enforceable', True) is not True:
        reasons.append('binding_unenforceable')
    return reasons


def _caps(data, settings):
    """(total, per_account): the stricter of the settings caps and the ledger-stored caps."""
    stored_total, stored_account = Ledger._caps(data)
    return min(settings['caps']['total'], stored_total), min(settings['caps']['per_account'], stored_account)


def _candidate(data, route, observations, assignment, settings, held, accounts, now):
    """Return (candidate or None, reasons) for one route; eligibility, billing class, then feasibility."""
    cls = FUNDING_OF.get(route.get('billing_class')) if isinstance(route, dict) else None
    if cls is None:
        return None, ['billing_class_excluded']
    pool = observations.get('pools', {}).get(route.get('pool'))
    if pool is None:
        return None, ['pool_unknown']
    if _incomplete(route, pool):
        return None, ['observation_incomplete']
    if _invalid(pool):
        return None, ['invalid_observation']
    reasons = _credential_reasons(route, assignment['scope'], settings)
    if pool.get('billing_class', route['billing_class']) != route['billing_class']:
        reasons.append('pool_funding_mismatch')  # a direct grant never funds a gateway, nor the reverse
    if route.get('capable', True) is not True:
        reasons.append('capability_missing')
    if route.get('permitted', True) is not True:
        reasons.append('not_permitted')
    if accounts.count(route['account']) >= _caps(data, settings)[1]:
        reasons.append('concurrency_cap')
    if reasons:
        return None, reasons
    funding = revision = None
    if route['billing_class'] in FUNDED:
        funding = _funding(data, pool)
        if funding is None:
            return None, ['no_funded_organization']
        funding, revision = funding
        try:  # the same gates `begin` applies: blocked, stale balance, cycle boundary, policy revision
            org_room = Ledger.org_headroom(data, funding, assignment['task'], revision, now)
        except BudgetError:
            return None, ['funding_unverified']
    windows = {window['id']: window for window in pool['windows']}
    vector, constraints = {}, []
    bounds = route.get('next_request_bound') or {}
    for window_id, estimate in route['demand'].items():
        window = windows.get(window_id)
        if window is None:
            return None, ['pool_unknown']
        stale = (window.get('used') is None or window.get('fresh', True) is not True
                 or (window.get('reset_at') is not None and window['reset_at'] <= now))
        if stale:
            return None, ['stale_observation']
        try:
            reserve = Ledger._reserve(estimate, bounds.get(window_id), settings['margin_bp'])
        except BudgetError:
            return None, ['invalid_observation']
        key = ORG + funding if funding else _window_key(route['pool'], window_id)
        # The collector's own limit is a stricter minimum only; for dollars the organization total decides.
        room = window['limit'] - window['used'] - window.get('protected', 0) - held.get(key, 0)
        if funding:
            # Windows of one dollar pool measure the same spend: the organization is charged the largest once.
            room = min(room, org_room)
            vector[key] = max(vector.get(key, 0), reserve)
        else:
            vector[key] = reserve
        if reserve > room:
            return None, ['insufficient_headroom']
        constraints.append(dict(pool=route['pool'], window=window_id, headroom=room, cost=estimate,
                                resets_at=window.get('reset_at')))
    if funding and any(vector[ORG + funding] > item['headroom'] for item in constraints):
        return None, ['insufficient_headroom']  # the one dollar charge must fit every window it is counted in
    return dict(route=route['route'], pool=route['pool'], constraints=constraints, vector=vector, cls=cls,
                spec=route, windows=pool['windows'], revision=revision), []


def _utilization(candidates, held, settings, now):
    """Lowest projected maximum over feasible subscription routes; unknown is conservative, never zero."""
    best, reasons = None, []
    for item in candidates:
        windows, reserve_new = [], {}
        for window in item['windows']:
            limit = window['limit']
            windows.append(dict(id=window['id'], used=None if window.get('used') is None else window['used'] / limit,
                                reserved=held.get(_window_key(item['pool'], window['id']), 0) / limit,
                                length=window.get('length'), reset_at=window.get('reset_at'),
                                fresh=window.get('fresh', True), rolling=window.get('rolling', False)))
            reserve_new[window['id']] = item['vector'].get(_window_key(item['pool'], window['id']), 0) / limit
        value, why = allocator.projected_utilization(windows, reserve_new, now, settings['min_elapsed_fraction'])
        reasons += why
        best = value if best is None or (value is not None and value < best) else best
    if best is None:
        reasons.append('utilization_unknown')
        best = 1.0
    return best, list(dict.fromkeys(reasons))


def _expiry(candidates, observations, settings, now):
    """E from the earliest-expiring grant of a feasible, eligible direct route; gateways never contribute."""
    grants = [observations['pools'][item['pool']]['grant'] for item in candidates
              if item['spec']['billing_class'] == 'api_grant' and 'grant' in observations['pools'][item['pool']]]
    grants = [grant for grant in grants if grant['expires_at'] > now]
    if not grants:
        return 0.0
    grant = min(grants, key=lambda item: item['expires_at'])
    return allocator.expiry_pressure(grant['remaining'], grant.get('expected_spend', 0),
                                     (grant['expires_at'] - now) / SECONDS_PER_HOUR, settings['T_hours'])


def _snapshot(item, now, horizon):
    found = allocator.binding_constraint(item['constraints'], now, horizon)
    pressure = None if found is None or found[1] is None else float(found[0] / found[1])
    return dict(pressure=pressure, constraints=[dict(c) for c in item['constraints']])


def _choice_matches(entry, item):
    spec = item['spec']
    return all(spec.get(field) == entry[field] for field in ROUTE_IDENTITY) and item['cls'] == entry['funding']


def _decide(data, assignment, ranking, settings, observations):
    now, horizon = observations['now'], observations.get('minimum_horizon', DEFAULT_MINIMUM_HORIZON)
    accounts, held = Ledger.open_accounts(data), Ledger.held(data)
    if len(accounts) >= _caps(data, settings)[0]:
        return _pending('concurrency_cap')
    candidates, excluded = [], []
    for route in observations['routes']:
        item, why = _candidate(data, route, observations, assignment, settings, held, accounts, now)
        if item is not None:
            candidates.append(item)
        excluded += why
    bucket = [assignment['role'], assignment['complexity_band']]
    explicit = assignment.get('choice')
    ranked = settings['smart_routing'] and not explicit and bool(ranking.get('strategy_ids'))
    reasons = list(ranking.get('reason_codes', [])) if ranked else []
    u = e = None
    if ranked:
        order = {sid: i for i, sid in enumerate(ranking['strategy_ids'])}
        pool = sorted((c for c in candidates if c['spec'].get('strategy_id') in order),
                      key=lambda c: order[c['spec']['strategy_id']])
        by_class = {cls: [c for c in pool if c['cls'] == cls] for cls in ('api', 'subscription')}
        feasible = [cls for cls, items in by_class.items() if items]
        if not feasible:
            return _pending(_dominant(excluded), excluded)
        if len(feasible) == 2:
            u, why = _utilization(by_class['subscription'], held, settings, now)
            e = _expiry(by_class['api'], observations, settings, now)
            reasons += why
            api = allocator.api_share(u, e, settings['L'], settings['H'])
        else:
            api = 1.0 if feasible == ['api'] else 0.0  # hard overrides
        shares = dict(api=api, subscription=1 - api)
        rows = [r for r in data['admissions'] if r.get('settings_revision') == settings.get('revision', 0)]
        cls = allocator.choose_class(shares, rows, bucket, observations.get('epoch_at', 0), feasible)
        chosen = allocator.choose_route(by_class[cls], now, horizon) if cls else None
        mode = 'ranked'
    else:
        entries = [explicit] if explicit else settings['worker_choices'].get(assignment['role'], [])
        chosen, shares, mode = None, None, 'worker_choice'
        for entry in entries:
            matching = [c for c in candidates if _choice_matches(entry, c)]
            chosen = allocator.choose_route(matching, now, horizon) if matching else None
            if chosen is not None:
                cls = entry['funding']
                shares = dict(api=float(cls == 'api'), subscription=float(cls == 'subscription'))
                break
        if chosen is None:
            return _pending('explicit_choice_unavailable' if explicit else 'worker_choice_unavailable', excluded)
    if chosen is None:
        return _pending(_dominant(excluded), excluded)
    item = next(c for c in candidates if c['route'] == chosen)
    spec = item['spec']
    credential = spec.get('credential') or {}
    record = dict(attempt_id=assignment['attempt_id'], task=assignment['task'], bucket=bucket, shares=shares,
                  **{'class': item['cls']}, route=item['route'], pool=item['pool'], account=spec['account'],
                  route_identity=route_identity(*(spec.get(field) for field in ROUTE_IDENTITY)),
                  billing_class=spec['billing_class'], credential_ref=credential.get('ref'),
                  pressure=dict(u=u, e=e, **_snapshot(item, now, horizon)), reserved=dict(item['vector']),
                  settings_revision=settings.get('revision', 0), organization_revision=item['revision'], at=now,
                  mode=mode,
                  snapshot_id=observations.get('snapshot_id'),
                  reason_codes=list(dict.fromkeys(reasons)),
                  ranking=dict(table_version=ranking.get('table_version'), profile_digest=ranking.get('profile_digest')))
    return dict(record=record, vector=item['vector'], account=spec['account'], now=now, revision=item['revision'])


def _dominant(excluded):
    """The reason a pending assignment reports: a concurrency place first, then the commonest exclusion."""
    if 'concurrency_cap' in excluded:
        return 'concurrency_cap'
    if excluded and set(excluded) == {'no_funded_organization'}:
        return 'no_funded_organization'  # the user enabled no organization for these routes; not an error
    if 'credential_unavailable' in excluded:
        return 'credential_unavailable'  # actionable: the key binding is not ready
    return 'no_feasible_route'


def _text(value):
    return type(value) is str and 0 < len(value) <= MAX_ID and value.isprintable()


def _part(value):
    """A pool or window id: `/` separates them in `win:<pool>/<window>` keys, so it cannot appear in either."""
    return _text(value) and '/' not in value


def _amount(value):
    return type(value) is int and 0 <= value <= MAX_INTEGER


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= MAX_INTEGER


def _optional(value, check):
    return value is None or check(value)


def _amounts(value, allow_none=False):
    return type(value) is dict and len(value) <= MAX_WINDOWS and all(
        _text(key) and (_amount(item) or (allow_none and item is None)) for key, item in value.items())


def _valid_assignment(assignment):
    if not isinstance(assignment, dict) or not all(_text(assignment.get(key)) for key in
                                                   ('attempt_id', 'task', 'role', 'complexity_band', 'scope')):
        return False
    choice = assignment.get('choice')
    return choice is None or (type(choice) is dict and all(_text(choice.get(key)) for key in (*ROUTE_IDENTITY, 'funding')))


def _valid_route(route):
    if type(route) is not dict:
        return False
    for key in ('route', 'pool', 'account', 'strategy_id', 'billing_class', *ROUTE_IDENTITY):
        if key in route and not _text(route[key]):
            return False
    if 'demand' in route and not _amounts(route['demand']):
        return False
    if route.get('next_request_bound') is not None and not _amounts(route['next_request_bound'], allow_none=True):
        return False
    credential = route.get('credential')
    return credential is None or (type(credential) is dict and all(
        _optional(credential.get(key), _text) for key in ('ref', 'scope', 'state')))


def _valid_window(window):
    return (type(window) is dict and _part(window.get('id'))
            and all(_optional(window.get(key), _finite) for key in ('limit', 'used', 'length', 'reset_at'))
            and _finite(window.get('protected', 0)) and _optional(window.get('organization'), _text)
            and _optional(window.get('organization_revision'), _amount))


def _valid_pool(pool):
    if type(pool) is not dict or not _optional(pool.get('billing_class'), _text):
        return False
    windows = pool.get('windows')
    if type(windows) is not list or not 0 < len(windows) <= MAX_WINDOWS or not all(_valid_window(w) for w in windows):
        return False
    grant = pool.get('grant')
    return grant is None or (type(grant) is dict and _finite(grant.get('remaining'))
                             and _finite(grant.get('expires_at')) and _finite(grant.get('expected_spend', 0)))


def _valid_observations(observations):
    if type(observations) is not dict or not _amount(observations.get('now')):
        return False
    if not (_optional(observations.get('snapshot_id'), _text) and _optional(observations.get('epoch_at'), _finite)
            and _optional(observations.get('minimum_horizon'), lambda v: _finite(v) and v > 0)):
        return False
    routes, pools = observations.get('routes'), observations.get('pools')
    return (type(routes) is list and len(routes) <= MAX_ROUTES and all(_valid_route(r) for r in routes)
            and type(pools) is dict and len(pools) <= MAX_POOLS
            and all(_part(key) and _valid_pool(pool) for key, pool in pools.items()))


def _valid_ranking(ranking):
    if type(ranking) is not dict:
        return False
    ids, codes = ranking.get('strategy_ids', []), ranking.get('reason_codes', [])
    return (type(ids) is list and len(ids) <= MAX_STRATEGIES and all(_text(item) for item in ids)
            and type(codes) is list and len(codes) <= MAX_REASONS and all(_text(item) for item in codes)
            and all(_optional(ranking.get(key), lambda v: _text(v) or _amount(v))
                    for key in ('table_version', 'profile_digest')))


def _valid_settings(settings):
    if type(settings) is not dict or not _amount(settings.get('revision', 0)):
        return False
    try:
        routing_settings.resolve({key: value for key, value in settings.items() if key != 'revision'})
    except ValueError:
        return False
    return set(routing_settings.DEFAULTS) <= set(settings)


def _shape_error(assignment, ranking, settings, observations):
    """The first malformed input, checked whole before any arithmetic or ledger access."""
    for reason, valid, value in (('invalid_assignment', _valid_assignment, assignment),
                                 ('invalid_ranking', _valid_ranking, ranking),
                                 ('invalid_settings', _valid_settings, settings),
                                 ('invalid_observation', _valid_observations, observations)):
        if not valid(value):
            return reason
    return None


def admit(assignment, ranking, settings, observations, *, ledger):
    """Choose a funding class and route, reserve the padded claim and record the decision atomically.

    ``assignment`` is ``{attempt_id, task, role, complexity_band, scope, choice?}``; ``ranking`` the
    result of ``rank`` (empty ``strategy_ids`` falls back to worker choices, never blocks); ``settings`` a
    resolved ``routing_settings`` dict plus ``revision``.

    ``observations`` is the collector's cached snapshot ``{now, routes, pools, snapshot_id?, epoch_at?,
    minimum_horizon?}``; ``snapshot_id`` is stamped into the record for audit. Health keys are REQUIRED and
    never default: per route ``capable``, ``permitted`` (bool), ``demand`` (dict), ``route``, ``pool``,
    ``account``; subscription routes ``binding_enforceable``; direct API and gateway routes
    ``funding_verified`` and a ``credential`` ``{ref, scope, state}`` (None means unavailable; gateways also
    ``integration_enabled``); per pool window ``id``, ``limit``, ``used`` (None is unknown) and ``fresh``.
    A missing or non-boolean key excludes that route with ``observation_incomplete`` (unusable numbers:
    ``invalid_observation``).

    Every claim is keyed by what funds it: a direct API or gateway route needs every pool window to name one
    ``organization`` (with ``organization_revision``) the user has enabled in the ledger (``personal`` or a
    named one; a gateway uses its own), else (none, unknown or disabled) it is excluded with ``no_funded_organization`` while
    subscription and worker-choice routes proceed. Its reservation is an ``org:<organization>`` claim
    against the ledger's one total R for that organization, whichever pool, key or gateway reaches it; a
    subscription window is a ``win:<pool>/<window>`` claim in its own units. A funded route the ledger's
    gates refuse (blocked, stale balance, cycle boundary, revision) is excluded with
    ``funding_unverified``; the collector's ``limit``/``used``/``protected`` can only make a dollar claim
    stricter. The concurrency caps are the stricter of ``settings.caps``
    and the ledger-stored caps. Unknown utilization (no usable subscription window) deliberately counts as
    u = 1.0 with ``utilization_unknown``: it pushes toward API, which stays gated by enablement, headroom
    and funding. Returns the record with ``status='admitted'`` (``duplicate=True`` for a repeated attempt)
    or ``status='pending'`` with a ``reason_code``. Pending and duplicate decisions consume nothing, create
    no record and leave the ledger unwritten. A reused attempt id is rejected (``attempt_id_in_use``) while
    it is among the ledger's last ``MAX_SEEN`` ids. This is not dispatch authority; S3 issues phase
    authority inside the hold, calls ``mark_dispatched``/``mark_uncertain``/``close_attempt``, and uses
    ``Ledger.convert_hold`` to turn a dollar hold into the attempt's run claim without changing R.

    The whole input shape is validated first (types, finite numbers within ``MAX_INTEGER``, integer
    demands and bounds, identifiers within the ledger's 256-character name limit, table sizes); a malformed
    input returns ``pending`` with ``invalid_assignment``, ``invalid_ranking``, ``invalid_settings`` or
    ``invalid_observation``. Any other failure inside the decision becomes ``pending`` with
    ``admission_error``: ``admit`` never raises on its inputs.
    """
    try:
        reason = _shape_error(assignment, ranking, settings, observations)
    except Exception:  # noqa: BLE001 - e.g. RecursionError on a pathological structure
        reason = 'invalid_observation'
    if reason is not None:
        return _pending(reason)
    try:
        outcome = ledger.admit(assignment['attempt_id'],
                               lambda data: _decide(data, assignment, ranking, settings, observations))
    except BudgetError as error:
        return _pending(str(error))
    except Exception:  # noqa: BLE001 - the transaction rolled back; nothing was reserved or recorded
        return _pending('admission_error')
    return outcome if outcome.get('status') == 'pending' else dict(outcome, status='admitted')


# ---------------------------------------------------------------------------------------------------
# Command support (S3.1): pre-transmission check, lifecycle intake and local summaries
# ---------------------------------------------------------------------------------------------------
# An admitted-but-unsent decision is usable for this long, in seconds of the command's own clock; an age of
# exactly ADMISSION_TTL is still allowed and a negative age (a clock that went back) is refused.
ADMISSION_TTL = 900
# The largest difference between a collector snapshot's `now` and the command's clock that `prepare` accepts.
MAX_CLOCK_DRIFT = 60
# `check` covers model requests and subagent spawns only. Tool, MCP and webfetch calls carry no model facts; S3.3
# governs them with policy deny rules, and `check` refuses any other kind with `unsupported_kind`.
REQUEST_KINDS = ('primary', 'subagent', 'title', 'compaction', 'generate')
REQUEST_FIELDS = ('provider', 'model', 'variant', 'harness', 'kind', 'funding_pool')
# Lifecycle events S3.1 accepts, each an existing ledger hold transition; S3.6 adds usage and settlement.
RECORD_KINDS = ('dispatched', 'uncertain', 'not_dispatched', 'complete')
# Hold states that mean a repeated event already took effect.
_ALREADY = dict(dispatched=('dispatched', 'uncertain'), uncertain=('uncertain',), not_dispatched=(), complete=())


def route_identity(provider, model, effort, harness):
    """Compact digest of the admitted route identity (alias + requested variant + harness), or None if unknown."""
    parts = [provider, model, effort, harness]
    if not all(_text(part) for part in parts):
        return None
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def _blocked(reason):
    return dict(state='blocked', reason=reason)


def remaining_envelope(hold):
    """The open claims of a hold by typed key. S3.1 records no consumption, so this is the full reservation."""
    claims = dict(hold['windows'])
    if hold.get('amount'):
        claims[ORG + hold['organization']] = hold['amount']
    return claims


def _funding_block(data, hold, record, settings, now):
    """Why a dollar-funded hold may not send now, or None. Re-read on every check, dispatched or not.

    Uses the ledger's own admission gates (enabled, not blocked, policy revision, fresh balance, cycle), the
    hold's cycle and the reconciled credit, plus API Usage enablement for direct API routes. Subscription-only
    holds have no organization claim and are not affected."""
    organization = hold.get('organization')
    if not organization or not hold.get('amount'):
        return None
    if record.get('billing_class') in DIRECT_API and settings['api_usage_enabled'] is not True:
        return 'api_usage_disabled'
    try:
        Ledger._admission_checks(data, organization, record.get('organization_revision'), now)
        org = data['organizations'][organization]
        if hold.get('cycle') != org['policy']['cycle'] or Ledger._credit_raw(data, organization, now) < 0:
            return 'funding_unverified'
    except BudgetError as error:
        unavailable = ('organization_unconfigured', 'organization_disabled', 'manual_recovery_required')
        return 'organization_unavailable' if str(error) in unavailable else 'funding_unverified'
    return None


def check_request(data, attempt_id, facts, settings, now):
    """Pre-transmission check over a ledger snapshot; reads only, never writes or consumes.

    ``facts`` is ``{provider, model, variant, harness, kind, funding_pool, bound?}`` already shape-checked by the
    caller; ``bound`` maps typed claim keys to the request's upper bound. ``settings`` is the resolved settings
    with ``revision``; ``now`` is the command's clock. Returns ``state='allow'`` or a fixed blocked reason.

    An unsent (``admitted``) attempt must match the current settings revision and be at most ``ADMISSION_TTL``
    old. A ``dispatched`` attempt skips both, so turning Smart Routing OFF never stops started subscription work,
    but every send on a dollar-funded hold (dispatched or not) re-checks API Usage and its organization. The
    envelope applies to both. S3.4 adds single-use dispatch authority to the allow result; nothing here grants it.
    """
    hold, record = data['holds'].get(attempt_id), Ledger.admission(data, attempt_id)
    if hold is None or record is None:
        return _blocked('unknown_attempt')
    if hold['closed']:
        return _blocked('attempt_closed')
    if hold['state'] == 'uncertain':
        return _blocked('attempt_uncertain')
    if hold['state'] not in ('admitted', 'dispatched'):
        return _blocked('hold_state_unsupported')
    if hold['state'] == 'admitted':
        if record.get('settings_revision') != settings['revision']:
            return _blocked('settings_revision_changed')
        if type(record.get('at')) is not int:
            return _blocked('admission_expired')
        if now < record['at']:
            return _blocked('clock_invalid')
        if now - record['at'] > ADMISSION_TTL:
            return _blocked('admission_expired')
    funding = _funding_block(data, hold, record, settings, now)
    if funding is not None:
        return _blocked(funding)
    identity = route_identity(facts['provider'], facts['model'], facts['variant'], facts['harness'])
    if record.get('pool') != facts['funding_pool'] or identity is None or identity != record.get('route_identity'):
        return _blocked('route_mismatch')
    remaining = remaining_envelope(hold)
    bound = facts.get('bound')
    if bound:
        if any(key not in remaining or amount > remaining[key] for key, amount in bound.items()):
            return _blocked('envelope_exhausted')
    elif not any(amount > 0 for amount in remaining.values()):
        return _blocked('envelope_exhausted')
    return dict(state='allow', attempt_id=attempt_id, route=record['route'], pool=record['pool'],
                settings_revision=record['settings_revision'], remaining=remaining)


def record_event(ledger, attempt_id, kind):
    """Apply one lifecycle event as its ledger hold transition; a repeat that already took effect is a duplicate.

    Only established outcomes release a claim: ``not_dispatched`` before dispatch, ``complete`` after it. A bare
    ``complete`` never releases an uncertain hold (``reconciliation_required``): S3.6 adds evidence-based reconciliation.
    A closed attempt refuses every later event (``attempt_closed``): S3.1 keeps no event store, so it cannot tell
    a late duplicate from a conflicting report. S3.6 adds durable event-id deduplication, usage and settlement.
    """
    if kind not in RECORD_KINDS:
        return _blocked('record_unsupported')
    if attempt_id not in ledger.snapshot()['holds']:
        return _blocked('unknown_attempt')
    try:
        if kind == 'dispatched':
            ledger.mark_dispatched(attempt_id)
        elif kind == 'uncertain':
            ledger.mark_uncertain(attempt_id)
        else:
            ledger.close_attempt(attempt_id, kind, reconciled=False)
        return dict(state='recorded', duplicate=False)
    except BudgetError as error:
        hold = ledger.snapshot()['holds'].get(attempt_id)
        if hold is None:
            return _blocked('unknown_attempt')
        if hold['closed']:
            return _blocked('attempt_closed')
        if hold['state'] in _ALREADY[kind]:
            return dict(state='recorded', duplicate=True)
        return _blocked('hold_state_forbidden' if str(error) == 'close_forbidden' else str(error))


def unsent_admissions(data, settings_revision):
    """Open, never-dispatched attempts admitted under another settings revision: `check` refuses them.

    They keep their claims (counted until a `not_dispatched` record or reconciliation releases them)."""
    stale = []
    for record in data['admissions']:
        hold = data['holds'].get(record['attempt_id'])
        if hold is not None and not hold['closed'] and hold['state'] == 'admitted' \
                and record.get('settings_revision') != settings_revision:
            stale.append(record['attempt_id'])
    return stale


def ledger_summary(data):
    """Counts and nonsecret labels only: open holds by state, caps and the user's organizations."""
    holds = {}
    for hold in Ledger.open_holds(data).values():
        holds[hold['state']] = holds.get(hold['state'], 0) + 1
    total, per_account = Ledger._caps(data)
    organizations = [dict(id=key, enabled=not org.get('disabled', False))
                     for key, org in sorted(data['organizations'].items())]
    return dict(holds=holds, open_attempts=len(Ledger.open_attempts(data)),
                caps=dict(total=total, per_account=per_account), organizations=organizations)
