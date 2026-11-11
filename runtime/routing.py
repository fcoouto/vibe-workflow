"""Smart Routing task profiles and deterministic ranking.

An empty ranking (``no_recommendation``) is never a refusal: callers fall through to the
deterministic admission path or the user's explicit worker choices. Only admission can block.

Profiling and ranking are pure: no network, subprocess or model call happens here. ``admit`` and
``dispatch`` are the exceptions: each writes the local ledger in one short transaction of pure
arithmetic. ``dispatch`` issues the single-use dispatch authority (S3.4).
"""
import copy
import functools
import hashlib
import hmac
import json
import math
import re
import secrets

from runtime import routing_allocator as allocator, routing_gates, routing_settings, task_profile
from runtime import subscription_reconcile as sub_reconcile
from runtime import usage_budget
from runtime.usage_budget import MAX_INTEGER, ORG, WIN, BudgetError, Ledger

CONTRACT_VERSION = 2  # the template's contract_version; tests/test_task_profile.py pins the constants below to it
COMPLEXITY_BANDS = ('low', 'medium', 'high', 'critical')
RISK_BANDS = ('low', 'medium', 'high', 'critical')
REVIEW_POLICIES = ('none', 'self', 'independent', 'strict')
UNCERTAINTY = ('low', 'medium', 'high')
PROVENANCE = ('coordinator', 'metadata', 'floor')
# Conservative values for fields nobody supplied: higher bands, stricter review.
FLOORS = dict(complexity_band='high', risk_band='high', review_policy='independent')
# Requirement floors implied by a write claim, independent of any supplied value.
WRITE_FLOORS = dict(complexity_band='medium', risk_band='medium')
SEMANTIC_FIELDS = ('complexity_band', 'risk_band')
SUPPLIED_FIELDS = ('complexity_band', 'risk_band', 'context_tokens', 'tools', 'review_policy', 'uncertainty')
SUPPLIED_REFERENCES = ('scope_ref', 'contract_version', 'semantic_digest', 'task_family')  # task_family: retired, ignored
_ORDERS = dict(complexity_band=COMPLEXITY_BANDS, risk_band=RISK_BANDS, review_policy=REVIEW_POLICIES)
MAX_TOKENS = 10**9
# Same bounds as the worker brief and claim limits in runtime/delegation.py.
MAX_TEXT = 8192
MAX_CLAIMS = 64


class ProfileUnavailable(ValueError):
    """Floors cannot be established, for example a role that is no roster specialist or a missing scope."""


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= 64 and value.isprintable() and '|' not in value


def _valid(field, value):
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


def row_key(work_kind, complexity_band, risk_band):
    """Stable string key for a strategy-table row."""
    parts = (work_kind, complexity_band, risk_band)
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


def ensure_profile(job):
    """Return a TaskProfile for ``job`` without mutating it.

    Complete coordinator-supplied profiles are used as supplied. Gaps take
    deterministic floors. No supplied value can lower a requirement floor.
    """
    try:
        specialist = task_profile.specialist(job.get('role')) if isinstance(job, dict) else None
    except task_profile.TemplateUnavailable:
        raise ProfileUnavailable('profile_unavailable: task profile template unavailable') from None
    if specialist is None:
        raise ProfileUnavailable('profile_unavailable: not a roster specialist')
    if not isinstance(job.get('scope_ref'), str) or not job['scope_ref']:
        raise ProfileUnavailable('profile_unavailable: no scope')
    digest = semantic_digest(job)
    reasons = []
    supplied = _usable_supplied(job, digest, reasons)
    values, provenance = {}, {}
    for field in _ORDERS:
        if _valid(field, supplied.get(field)):
            values[field], provenance[field] = supplied[field], 'coordinator'
    gaps = [field for field in _ORDERS if field not in values]
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
    uncertainty = 'high' if gaps else supplied.get('uncertainty') if _valid('uncertainty', supplied.get('uncertainty')) else 'low'
    return dict(contract_version=CONTRACT_VERSION, job_id=job.get('id'), role=job['role'], scope_ref=job['scope_ref'],
                work_kind=specialist['work_kind'], complexity_band=values['complexity_band'],
                risk_band=values['risk_band'], review_policy=values['review_policy'], context=context,
                tools=tools, uncertainty=uncertainty, uncertain_fields=gaps, reasons=reasons,
                provenance=dict(provenance, context=context_source, tools=tools_source),
                semantic_digest=digest)


TABLE_SCHEMA = 2  # rows keyed `work_kind|complexity_band|risk_band`; routing_evidence compiles this schema
CONSTRAINT_FIELDS = ('allowed_models', 'excluded_models', 'allowed_efforts', 'allowed_harnesses',
                     'allowed_strategy_ids')


def resolve_row(table, profile):
    """Pick the table row for a profile: the exact row, then the nearest stricter band for the same work kind.

    Returns ``(strategies, reason)``. An empty list (``no_recommendation``, never a refusal) means no qualified row
    at or above the profile's bands exists for its work kind. A weaker row or another work kind's is never used.
    """
    rows = table.get('rows') if isinstance(table, dict) else None
    if not isinstance(rows, dict) or not rows:
        return [], 'no_recommendation'
    kind = profile['work_kind']
    c, r = COMPLEXITY_BANDS.index(profile['complexity_band']), RISK_BANDS.index(profile['risk_band'])
    exact = rows.get(row_key(kind, profile['complexity_band'], profile['risk_band']))
    if exact:
        return exact, 'ranking_exact'
    stricter = []
    for name, strategies in rows.items():
        parts = name.split('|')
        if len(parts) != 3 or parts[0] != kind or not strategies or \
                parts[1] not in COMPLEXITY_BANDS or parts[2] not in RISK_BANDS:
            continue
        ci, ri = COMPLEXITY_BANDS.index(parts[1]), RISK_BANDS.index(parts[2])
        if ci >= c and ri >= r:  # A cheaper row below the task's floors is never eligible.
            stricter.append((ci + ri, ci, ri, name, strategies))
    if stricter:
        return min(stricter, key=lambda item: item[:4])[4], 'ranking_stricter_band'
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
    outdated = isinstance(table, dict) and bool(table.get('rows')) and table.get('schema') != TABLE_SCHEMA
    strategies, reason = ([], 'no_recommendation') if outdated else resolve_row(table, profile)
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
    elif outdated:
        reasons.append('table_outdated')  # another schema, e.g. role rows: no strategy until a table refresh
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


def _decide(data, assignment, ranking, settings, observations, workflow=None):
    now, horizon = observations['now'], observations.get('minimum_horizon', DEFAULT_MINIMUM_HORIZON)
    accounts, held = Ledger.open_accounts(data), sub_reconcile.held(data)  # S3.6c: plus window debits
    if len(accounts) >= _caps(data, settings)[0]:
        return _pending('concurrency_cap')
    candidates, excluded = [], []
    for route in observations['routes']:
        item, why = _candidate(data, route, observations, assignment, settings, held, accounts, now)
        if item is not None:
            candidates.append(item)
        excluded += why
    bucket = [assignment['work_kind'], assignment['complexity_band']]
    explicit = assignment.get('choice')
    ranked = settings['smart_routing'] and not explicit and bool(ranking.get('strategy_ids'))
    reasons = list(ranking.get('reason_codes', [])) if ranked else []
    u = e = None
    from runtime import routing_funding  # the ranked decision and the record (F5.2); never at module import time
    if ranked:
        pending, chosen, shares, u, e = routing_funding.ranked(data, candidates, excluded, ranking, settings,
                                                               observations, held, bucket, reasons)
        if pending is not None:
            return pending
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
    return routing_funding.record(data, assignment, candidates, chosen, bucket, shares, mode, u, e, reasons, ranking,
                                  settings, observations, workflow)


def decision_rows(data, revision, now):
    """Admission records that may steer the class choice: this settings revision, within the 14-day diagnostic
    retention (spec 11). Expired records never influence a decision, whether or not cleanup has run."""
    oldest = now - usage_budget.RETENTION['diagnostic']
    return [r for r in data['admissions'] if r.get('settings_revision') == revision
            and type(r.get('at')) in (int, float) and r['at'] >= oldest]


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
    choice = assignment.get('choice') if isinstance(assignment, dict) else None  # with an optional pinned `endpoint`
    return isinstance(assignment, dict) and all(_text(assignment.get(key)) for key in (
        'attempt_id', 'task', 'role', 'work_kind', 'complexity_band', 'scope')) and (choice is None or (
            type(choice) is dict and all(_text(choice.get(key)) for key in (*ROUTE_IDENTITY, 'funding'))
            and _optional(choice.get('endpoint'), _text)))


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
    if type(observations) is not dict or not _amount(observations.get('now')) or not (
            all(_optional(observations.get(key), _text) for key in ('snapshot_id', 'routes_version'))
            and _optional(observations.get('epoch_at'), _finite)
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


def admit(assignment, ranking, settings, observations, *, ledger, workflow=None, clear_debits=False):
    """Choose a funding class and route, reserve the padded claim and record the decision atomically.

    ``assignment`` is ``{attempt_id, task, role, work_kind, complexity_band, scope, choice?}``; ``ranking`` the
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
    it is among the ledger's last ``MAX_SEEN`` ids. This is not dispatch authority: ``dispatch`` issues it
    for the hold, and lifecycle events go through ``record_event``. ``Ledger.convert_hold`` (which turns a
    dollar hold into the attempt's run claim without changing R) still requires an admitted hold.

    The whole input shape is validated first (types, finite numbers within ``MAX_INTEGER``, integer
    demands and bounds, identifiers within the ledger's 256-character name limit, table sizes); a malformed
    input returns ``pending`` with ``invalid_assignment``, ``invalid_ranking``, ``invalid_settings`` or
    ``invalid_observation``. Any other failure inside the decision becomes ``pending`` with
    ``admission_error``: ``admit`` never raises on its inputs.

    ``workflow`` is the job part of the S3.5 binding (``routing_gates.job_binding``) or None for an assignment
    with no workflow job; the record stores it with the credential marker as ``binding`` (``invalid_binding``
    when malformed). ``dispatch`` re-validates it. ``clear_debits`` lets windows clear debits (C3-0a: never ``--serve``).
    """
    try:
        reason = _shape_error(assignment, ranking, settings, observations)
        if reason is None and not routing_gates.binding_fits(workflow):
            reason = 'invalid_binding'
    except Exception:  # noqa: BLE001 - e.g. RecursionError on a pathological structure
        reason = 'invalid_observation'
    if reason is not None:
        return _pending(reason)
    try:
        outcome = ledger.admit(assignment['attempt_id'], lambda data: _decide(sub_reconcile.clear_debits(
            ledger, data, observations) if clear_debits else data, assignment, ranking, settings, observations, workflow))
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
# Lifecycle events: each S3.1 kind is a ledger hold transition; S3.6a adds usage, settlement and `observed` (usage
# with no transition, e.g. the coordinator's own requests, reported under a known attempt's root and never charged).
RECORD_KINDS = ('dispatched', 'uncertain', 'not_dispatched', 'complete')
EVENT_KINDS = RECORD_KINDS + ('observed',)
EVENT_REQUIRED = ('event_id', 'attempt_id', 'kind')
EVENT_OPTIONAL = ('stage', 'request_kind', 'at', 'usage', 'root_attempt_id', 'funding', 'authority')
MAX_USAGE_REQUESTS = 32
MAX_USAGE_UNITS = 8  # distinct window keys in one report; one hold claims at most eight windows
MAX_RECEIPT = 128
MAX_ROOT_ID = routing_gates.LOCATOR_LIMITS['task_id']
RESOLVE_KIND = 'resolve_cost_unknown'  # operator recovery ops; never client lifecycle events
OVERRUN_KIND = 'resolve_overrun'
USAGE_REQUEST_FIELDS = ('kind', 'model', 'variant', 'tokens', 'cost_usd_micros', 'units', 'receipt')


def route_identity(provider, model, effort, harness):
    """Compact digest of the admitted route identity (alias + requested variant + harness), or None if unknown."""
    parts = [provider, model, effort, harness]
    if not all(_text(part) for part in parts):
        return None
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def _blocked(reason):
    return dict(state='blocked', reason=reason)


def remaining_envelope(hold):
    """The open claims of a hold by typed key: the full reservation. S3.6a settles at completion only, so nothing is
    consumed while the attempt runs (S3.6b owns progress accounting from host message totals)."""
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


def credential_marker(data, record):
    """Digest of the trusted credential binding a dollar-funded admission relies on, or None for other routes.

    The trusted record is the existing secure key binding in the ledger (`routing` entries written by Usage
    Routing's `configure`: opaque `credential_ref`, scope, revision, enabled/disabled), matched by the admission's
    `credential_ref`; no secret is read. Revoking, disabling, rebinding or reconfiguring it changes the marker,
    and so does adding or removing an entry for the reference."""
    if record.get('billing_class') not in FUNDED:
        return None
    ref = record.get('credential_ref')
    entries = sorted(json.dumps([scope, config.get('revision'), config.get('binding'), config.get('enabled'),
                                 config.get('disabled')], sort_keys=True, separators=(',', ':'))
                     for scope, config in (data.get('routing') or {}).items()
                     if isinstance(config, dict) and isinstance(config.get('binding'), dict)
                     and config['binding'].get('credential_ref') == ref)
    return routing_gates.digest([ref, entries])


def check_request(data, attempt_id, facts, settings, now, *, authority=None, strict=False, dispatching=False):
    """Pre-transmission check over a ledger snapshot; reads only, never writes or consumes.

    ``facts`` is ``{provider, model, variant, harness, kind, funding_pool, bound?}`` already shape-checked by the
    caller; ``bound`` maps typed claim keys to the request's upper bound. ``settings`` is the resolved settings
    with ``revision``; ``now`` is the command's clock. Returns ``state='allow'`` or a fixed blocked reason.

    An unsent (``admitted``) attempt must match the current settings revision and be at most ``ADMISSION_TTL``
    old. A ``dispatched`` attempt skips both, so turning Smart Routing OFF never stops started subscription work,
    but every send on a dollar-funded hold (dispatched or not) re-checks API Usage and its organization. The
    envelope applies to both.

    Authority (S3.4): a hold dispatched through ``dispatch`` allows only a request carrying its token
    (``authority_required`` / ``authority_invalid``), and the allow result then says ``authority='verified'``.
    ``strict`` (the caller sent a session id or a token), or an admission stamped ``authority_required``, also
    refuses a hold that has no issued authority (``dispatch_required``): an admitted attempt, or one dispatched by
    a bare S3.1 ``record dispatched``. ``dispatching`` is set only by ``dispatch``'s own gate, which is how such a
    hold obtains its authority.
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
    issued = hold.get('dispatch')
    if issued is None and not dispatching and (strict or record.get('authority_required')):
        return _blocked('dispatch_required')
    if issued is not None:
        if authority is None:
            return _blocked('authority_required')
        if not hmac.compare_digest(authority_hash(authority), issued['authority']):
            return _blocked('authority_invalid')
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
    result = dict(state='allow', attempt_id=attempt_id, route=record['route'], pool=record['pool'],
                  settings_revision=record['settings_revision'], remaining=remaining)
    if issued is not None:
        result['authority'] = 'verified'
    return result


# ---------------------------------------------------------------------------------------------------
# Single-use dispatch authority (S3.4). The handshake, id derivation and uncertainty rules are defined once in
# the plan's S3.1 contract (docs/superpowers/plans/2026-11-08-smart-routing.md#task-s31-single-routing-command).
# ---------------------------------------------------------------------------------------------------
AUTHORITY_BYTES = 32  # secrets.token_urlsafe(32): 43 URL-safe characters, 256 random bits
MAX_AUTHORITY = 128
_ID_DOMAIN = 'vibe-routing/%s/v3\0'


def authority_hash(token):
    """The only form of an authority the ledger stores: SHA-256 hex of the token."""
    return hashlib.sha256(token.encode()).hexdigest()


@functools.lru_cache(maxsize=4096)
def _derived(ledger_identity, attempt_id, nonce):
    key = json.dumps([ledger_identity, attempt_id, nonce], ensure_ascii=True, separators=(',', ':'))
    session, message = (hashlib.sha256((_ID_DOMAIN % kind + key).encode()).hexdigest()[:32]
                        for kind in ('session', 'message'))
    return 'ses_' + session, 'msg_' + message


def derive_ids(attempt_id, ledger_identity, nonce):
    """The OpenCode session and message ids a managed launch of one admission must use.

    Pure and deterministic: domain-separated SHA-256 over the ledger identity, the attempt id and the
    admission's random ``nonce`` (JSON-encoded, so the parts cannot run into each other; ``None`` for an
    admission recorded before nonces), 128 bits each, written as ``ses_<32 lowercase hex>`` and
    ``msg_<32 lowercase hex>``. Two ledgers never share ids, and an attempt id reused in one ledger gets a new
    nonce and new ids; a duplicate ``prepare`` returns the same admission, so the same ids. That is a
    conservative subset of the adapter's ``SESSION`` and ``MESSAGE`` patterns; the probe fixtures are sanitized,
    so the server's own id body format is not on record. Hashing is not injective in principle, so ``dispatch``
    refuses an id another remembered attempt already owns (``id_collision``)."""
    if not _text(attempt_id) or not _text(ledger_identity) or not (nonce is None or _text(nonce)):
        raise ValueError('invalid attempt id, ledger identity or nonce')
    session, message = _derived(ledger_identity, attempt_id, nonce)
    return dict(session_id=session, message_id=message)


def admission_ids(data, attempt_id):
    """The derived ids of the attempt's recorded admission, or None when the ledger has no record of it."""
    record = Ledger.admission(data, attempt_id)
    return None if record is None else derive_ids(attempt_id, data['identity'], record.get('nonce'))


def reconcile_hint(ids):
    """The host read an operator can make about a lost acknowledgment. Informational only (``authoritative``
    false): an absent message can also mean a deleted or reverted message or another server or directory, so
    nothing accepts its result as proof of non-launch; the operator's `reconcile_uncertain` settles the hold from
    receipts or charges its full reservation (S3.6b-2). Routing makes no host call itself."""
    return dict(read='message', method='GET', authoritative=False, session_id=ids['session_id'],
                message_id=ids['message_id'],
                path='/api/session/%s/message/%s' % (ids['session_id'], ids['message_id']))


def _owner(data, kind, value):
    """The attempt whose ``kind`` id (``session_id`` or ``message_id``) is ``value``, or None.

    Looks at ids stored on dispatched holds and ids derived from admission records (they need the record's
    nonce). Each kind matches only its own kind, so a message id sent as a session id resolves to nothing."""
    for attempt, hold in data['holds'].items():
        issued = hold.get('dispatch')
        if issued and issued[kind] == value:
            return attempt
    for record in data['admissions']:
        ids = admission_ids(data, record['attempt_id']) if _text(record.get('attempt_id')) else None
        if ids and ids[kind] == value:
            return record['attempt_id']
    return None


def check(data, facts, settings, now, *, attempt_id=None, session_id=None, authority=None):
    """``check`` with S3.4 session resolution; reads only.

    With only ``session_id``: a session that no hold or admission record of this ledger derives (never admitted
    here, or aged out of both tables) is ``{'state': 'unmanaged'}``: interactive sessions stay unguarded, and the
    shim blocks this answer for a session it attached. A known attempt whose hold or record is gone is
    ``attempt_closed``. With both ids, a session
    that belongs to another attempt is ``session_mismatch``; an unknown one (a child session) is checked under
    ``attempt_id``. A session id or token makes the check strict (see ``check_request``)."""
    if session_id is not None:
        owner = _owner(data, 'session_id', session_id)
        if attempt_id is None:
            if owner is None:
                return dict(state='unmanaged')
            if owner not in data['holds'] or Ledger.admission(data, owner) is None:
                return _blocked('attempt_closed')
            attempt_id = owner
        elif owner is not None and owner != attempt_id:
            return _blocked('session_mismatch')
    return check_request(data, attempt_id, facts, settings, now, authority=authority,
                         strict=session_id is not None or authority is not None)


def _fresh_inputs(ledger, attempt_id, workflow):
    """Read the admission's binding and, for a bound job, the workflow view. Runs BEFORE the write transaction.

    Returns what the in-transaction gate compares against: the record's nonce and binding as read here, plus the
    view or a fixed error. A hold that is not admitted needs no view: the transaction answers it."""
    data = ledger.snapshot()
    hold, record = data['holds'].get(attempt_id), Ledger.admission(data, attempt_id)
    if hold is None or record is None or hold['closed'] or hold['state'] != 'admitted':
        return dict(skip=True)
    fresh = dict(nonce=record.get('nonce'), binding=copy.deepcopy(record.get('binding')))
    job = (fresh['binding'] or {}).get('job') if isinstance(fresh['binding'], dict) else None
    if job is not None:
        try:
            view = workflow.view(copy.deepcopy(job['locator']))
            fresh['view'] = copy.deepcopy(view) if routing_gates.valid_view(view) else None
            fresh['error'] = None if fresh['view'] is not None else 'gate_error'
        except routing_gates.WorkflowChanged:
            fresh['view'], fresh['error'] = None, 'workflow_changed'  # retried like an etag mismatch
        except routing_gates.ProjectMismatch:
            fresh['view'], fresh['error'] = None, 'project_mismatch'
        except Exception:  # noqa: BLE001 - an unreadable workflow fails closed; the hold stays admitted
            fresh['view'], fresh['error'] = None, 'gate_error'
        if fresh['error'] is None:
            del fresh['error']
    return fresh


def _fresh_gate(data, attempt_id, fresh, workflow):
    """S3.5's fixed gate set, run inside the write transaction after the ``check`` gates. Reads only.

    The record must still be the one read before the transaction (else ``workflow_changed``: retry). Then the
    binding (``binding_missing`` for admissions recorded before S3.5), the trusted credential binding
    (``credential_changed``), and for a bound job the workflow etag (``workflow_changed`` when the state moved
    since the view) and the view's verdict. Any exception is ``gate_error``."""
    try:
        record = Ledger.admission(data, attempt_id)
        if fresh.get('skip') or record is None or record.get('nonce') != fresh['nonce'] or \
                record.get('binding') != fresh['binding']:
            return 'workflow_changed'
        stored = record.get('binding')
        if stored is None:
            return 'binding_missing'
        if not routing_gates.valid_binding(stored):
            return 'gate_error'
        if credential_marker(data, record) != stored['credential']:
            return 'credential_changed'
        job = stored['job']
        if job is None:
            return None  # no workflow job: the funding, settings and route gates above are the whole set
        if 'error' in fresh:
            return fresh['error']
        current = workflow.etag(copy.deepcopy(job['locator']))
        if current is None or current != fresh['view']['etag']:
            return 'workflow_changed'
        return routing_gates.verdict(job, fresh['view'])
    except Exception:  # noqa: BLE001 - fail closed with a fixed reason
        return 'gate_error'


def dispatch(ledger, attempt_id, facts, settings, now, *, gates=(), workflow=None):
    """Issue the single-use authority for an admitted attempt, or return the existing decision.

    ``settings`` (resolved settings with ``revision``) and ``now`` may be callables; they are read INSIDE the
    write transaction, so a settings change that holds the same lock (the command's ``set``) is ordered wholly
    before or after the dispatch. The transaction re-runs every ``check`` gate on the unsent admission (settings
    revision, age, dollar funding, route identity, envelope), refuses ids another remembered attempt owns, then
    runs S3.5's fixed fresh gates on the stored binding (``_fresh_gate``; ``workflow`` is the trusted reader,
    never request input) and finally each ``gates`` entry ``gate(data, record) -> reason | None`` (a test seam)
    on a deep copy, so a gate cannot change the ledger. It then moves the hold to ``dispatched`` with the
    token's hash and the derived ids. A refusal writes nothing and the hold stays ``admitted``.

    Snapshot rule (S3.5): the workflow view is read before the transaction; inside it only the reader's cheap
    etag is compared. A changed etag aborts that round without writing and the whole round is retried, at
    most ``routing_gates.ROUNDS`` times, then refused with ``workflow_changed``. The token is generated before
    the transaction and returned only in this ``dispatched`` result; repeated or racing callers get
    ``already_dispatched`` without one. No host or network call happens here or inside the transaction.
    Not excluded: a workflow commit between the in-transaction etag read and the ledger commit (the two stores
    commit separately)."""
    if not _text(attempt_id):
        return _blocked('invalid_request')
    token = secrets.token_urlsafe(AUTHORITY_BYTES)
    admitted = {}

    def derived(data):
        found = admission_ids(data, attempt_id)
        if found is None:
            raise BudgetError('unknown_attempt')
        return found

    def gate(data, moment, fresh):
        current = settings() if callable(settings) else settings
        found = check_request(data, attempt_id, facts, current, moment, dispatching=True)
        if found['state'] != 'allow':
            return found['reason']
        ids = derived(data)
        if any(_owner(data, kind, ids[kind]) not in (None, attempt_id) for kind in ('session_id', 'message_id')):
            return 'id_collision'
        record = Ledger.admission(data, attempt_id)
        admitted.update(route=record['route'], pool=record['pool'])
        reason = _fresh_gate(data, attempt_id, fresh, workflow)
        if reason is not None:
            return reason
        if gates:
            copied = copy.deepcopy(data)
            for extra in gates:
                reason = extra(copied, Ledger.admission(copied, attempt_id))
                if reason is not None:
                    return reason if _text(reason) else 'gate_invalid'
        return None
    for round_ in range(routing_gates.ROUNDS):
        try:
            fresh = _fresh_inputs(ledger, attempt_id, workflow)
            outcome = ledger.dispatch_hold(attempt_id, authority_hash(token), derived,
                                           lambda data, moment: gate(data, moment, fresh), now)
        except BudgetError as error:
            if str(error) == 'workflow_changed' and round_ + 1 < routing_gates.ROUNDS:
                continue
            return _blocked(str(error))
        break
    ids = outcome['ids']
    if not outcome['issued']:
        return dict(state='already_dispatched', attempt_id=attempt_id, hold_state=outcome['state'], **ids,
                    reconcile_hint=reconcile_hint(ids))
    return dict(state='dispatched', attempt_id=attempt_id, authority=token, **ids, **admitted,
                reconcile_hint=reconcile_hint(ids))


def _bounded_id(value, limit):
    return type(value) is str and 0 < len(value) <= limit and value.isprintable()


def _usage_error(usage):
    if type(usage) is not dict or set(usage) != {'source', 'requests'}:
        return 'invalid_request'
    requests = usage['requests']
    if usage['source'] not in usage_budget.COST_SOURCES or type(requests) is not list or \
            not 0 < len(requests) <= MAX_USAGE_REQUESTS:
        return 'invalid_request'
    keys = set()
    for request in requests:
        if type(request) is not dict or not {'kind', 'model', 'tokens'} <= set(request) <= set(USAGE_REQUEST_FIELDS):
            return 'invalid_request'
        tokens, units = request['tokens'], request.get('units', {})
        if request['kind'] not in REQUEST_KINDS or not _text(request['model']) or \
                not _optional(request.get('variant'), _text) or not _optional(request.get('cost_usd_micros'), _amount):
            return 'invalid_request'
        if type(tokens) is not dict or set(tokens) != set(usage_budget.TOKEN_FIELDS) or \
                not all(_amount(amount) for amount in tokens.values()):
            return 'invalid_request'
        if type(units) is not dict or not all(
                type(key) is str and key.startswith(WIN) and 4 < len(key) <= usage_budget.MAX_KEY and key.isprintable()
                and _amount(amount) for key, amount in units.items()):
            return 'invalid_request'
        keys |= set(units)
        if 'receipt' in request and not _bounded_id(request['receipt'], MAX_RECEIPT):
            return 'invalid_request'
    return None if len(keys) <= MAX_USAGE_UNITS else 'invalid_request'


def event_error(event):
    """The fixed reason a lifecycle event is refused before any ledger access, or None.

    Bounded ids, labels and integers only (no prompt, body or secret can pass): `event_id` (128), `attempt_id`,
    `kind`; optional `stage`, `request_kind`, `at` (the client's time, informational and outside the digest),
    `usage` (on `complete` and `observed`), and on `observed` the required `stage`, `usage` and `authority` (the
    anchor hold's dispatch token; never stored) plus optional `funding` and `root_attempt_id` (the open ledger
    attempt whose root the usage joins, when `attempt_id` has no hold)."""
    if type(event) is not dict or not set(EVENT_REQUIRED) <= set(event) <= {*EVENT_REQUIRED, *EVENT_OPTIONAL}:
        return 'invalid_request'
    if not _bounded_id(event['event_id'], usage_budget.MAX_EVENT_ID) or not _text(event['attempt_id']) \
            or not _text(event['kind']):
        return 'invalid_request'
    if 'stage' in event and event['stage'] not in usage_budget.STAGES or \
            'request_kind' in event and event['request_kind'] not in REQUEST_KINDS or \
            'at' in event and not _amount(event['at']):
        return 'invalid_request'
    kind = event['kind']
    if kind not in EVENT_KINDS:
        return 'record_unsupported'
    if 'usage' in event and (kind not in ('complete', 'observed') or _usage_error(event['usage'])):
        return 'invalid_request'
    observed = kind == 'observed'
    if not observed and ('funding' in event or 'root_attempt_id' in event or 'authority' in event):
        return 'invalid_request'
    if observed and not ('stage' in event and 'usage' in event and event.get('funding', 'api') in ('api', 'subscription')
                         and _optional(event.get('root_attempt_id'), _text) and _token(event.get('authority'))):
        return 'invalid_request'
    return None


def _token(value):
    return type(value) is str and 0 < len(value) <= MAX_AUTHORITY and value.isascii() and value.isprintable()


def _operator_ids(value, fields):
    return type(value) is dict and set(fields) <= set(value) and \
        _bounded_id(value['event_id'], usage_budget.MAX_EVENT_ID) and _text(value['attempt_id'])


def resolve_event(value):
    """The operator's `resolve_cost_unknown` request as a ledger event, or None when malformed. `receipts` (1 to
    32 receipt ids) and `source: provider_receipt` come together or not at all; only they let padding go."""
    required = ('event_id', 'attempt_id', 'cost_usd_micros')
    if not _operator_ids(value, required) or not _amount(value['cost_usd_micros']):
        return None
    extra = set(value) - set(required)
    if extra not in (set(), {'receipts', 'source'}):
        return None
    if extra and not (value['source'] == 'provider_receipt' and type(value['receipts']) is list
                      and 0 < len(value['receipts']) <= MAX_USAGE_REQUESTS
                      and all(_bounded_id(item, MAX_RECEIPT) for item in value['receipts'])):
        return None
    return dict(value, kind=RESOLVE_KIND)


def overrun_event(value):
    """The operator's `resolve_overrun` request (`decision`: confirm or dismiss) as a ledger event, or None."""
    fields = ('event_id', 'attempt_id', 'decision')
    if not _operator_ids(value, fields) or set(value) != set(fields) or value['decision'] not in ('confirm', 'dismiss'):
        return None
    return dict(value, kind=OVERRUN_KIND)


# S3.6b-2 operator ops (never client lifecycle events; `record` refuses their kinds and fields).
RECONCILE_KIND = 'reconcile_uncertain'
BLOCK_KIND = 'resolve_block'
# Evidence that settles an uncertain dollar hold. Both charge (receipts at least 1 micro-USD, enforced by the ledger
# as `evidence_invalid`); receipts release the unspent rest on the operator's word. A no-spend
# proof (a provider usage report covering the attempt with zero cost) is refused as `evidence_unsupported`: nothing
# offline can check an operator-typed report, the ledger keeps no uncertain-mark time, a worker can still spend after
# the mark, and a later `observed_at` is not proof the report reflects the window (spec 8).
EVIDENCE_KINDS = ('receipts', 'charge_reservation')
LEGACY_MARKER = re.compile(r'lb_[0-9a-f]{32}')


def reconcile_event(value):
    """The operator's `reconcile_uncertain{event_id, attempt_id, evidence}` as a ledger event: (event, None), or
    (None, reason). `evidence` is `{kind: receipts, receipts: [1 to 32 distinct receipt ids], cost_usd_micros}` or
    `{kind: charge_reservation}`; another kind is `evidence_unsupported`, any other shape `invalid_request`."""
    fields = ('event_id', 'attempt_id', 'evidence')
    if not _operator_ids(value, fields) or set(value) != set(fields):
        return None, 'invalid_request'
    evidence = value['evidence']
    if type(evidence) is not dict or not _text(evidence.get('kind')):
        return None, 'invalid_request'
    if evidence['kind'] not in EVIDENCE_KINDS:
        return None, 'evidence_unsupported'
    if evidence['kind'] == 'receipts':
        items = evidence.get('receipts')
        valid = (set(evidence) == {'kind', 'receipts', 'cost_usd_micros'} and _amount(evidence['cost_usd_micros'])
                 and type(items) is list and 0 < len(items) <= MAX_USAGE_REQUESTS
                 and all(_bounded_id(item, MAX_RECEIPT) for item in items) and len(set(items)) == len(items))
    else:
        valid = set(evidence) == {'kind'}
    return (dict(value, kind=RECONCILE_KIND), None) if valid else (None, 'invalid_request')


def block_event(value):
    """The operator's `resolve_block{event_id, organization, reason, decision, ids? | marker?}` as a ledger event:
    (event, None) or (None, 'invalid_request'). `over_bound` needs `ids` (1 to 16 distinct run ids, as `status`
    lists them) and `decision` confirm or dismiss; `legacy` needs `decision: confirm` and the `marker` `status` shows.
    Overruns keep their own op (`resolve_overrun`)."""
    required = ('event_id', 'organization', 'reason', 'decision')
    if type(value) is not dict or not set(required) <= set(value) or \
            not _bounded_id(value['event_id'], usage_budget.MAX_EVENT_ID) or not _text(value['organization']):
        return None, 'invalid_request'
    extra = set(value) - set(required)
    if value['reason'] == 'over_bound':
        ids = value.get('ids')
        valid = (extra == {'ids'} and value['decision'] in ('confirm', 'dismiss') and type(ids) is list
                 and 0 < len(ids) <= usage_budget.SUMMARY_IDS and all(_text(item) for item in ids)
                 and len(set(ids)) == len(ids))
    elif value['reason'] == 'legacy':
        marker = value.get('marker')
        valid = (extra == {'marker'} and value['decision'] == 'confirm' and type(marker) is str
                 and LEGACY_MARKER.fullmatch(marker) is not None)
    else:
        valid = False
    if not valid:
        return None, 'invalid_request'
    event = dict(value, kind=BLOCK_KIND)
    if 'ids' in event:
        event['ids'] = sorted(event['ids'])  # canonical: a reordered retry is a duplicate
    return event, None


def resolve_block(ledger, event, now):
    """Apply one `block_event` in one ledger transaction (``Ledger.resolve_block``); refusals are fixed reasons."""
    try:
        return ledger.resolve_block(event, event_digest(event), now)
    except BudgetError as error:
        return _blocked(str(error))


def event_digest(event):
    """Canonical SHA-256 of an event without the client's `at` (a retry with a new timestamp is a duplicate) and
    without an `authority` token (never stored, not even hashed with the payload)."""
    payload = {key: value for key, value in event.items() if key not in ('at', 'authority')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def record_event(ledger, event, now):
    """Apply one lifecycle event (already checked by ``event_error``) in one ledger transaction.

    S3.1/S3.4 transitions are unchanged: only established outcomes release a claim (``not_dispatched`` before
    dispatch, ``complete`` after it); a bare ``complete`` never releases an uncertain hold
    (``reconciliation_required``); ``not_dispatched`` after dispatch is ``hold_state_forbidden``; a bare
    ``dispatched`` on a stamped admission is ``dispatch_required``; a repeat that already took effect is a duplicate.
    S3.6a: the ledger remembers event ids, so the same event returns its original outcome and a different payload
    under the same id is ``event_conflict``; ``complete`` settles (``Ledger._settle``). A closed attempt refuses
    every new event (``attempt_closed``)."""
    try:
        return ledger.record_event(event, event_digest(event), now)
    except BudgetError as error:
        return _blocked(str(error))


def _zero_tokens():
    return dict.fromkeys(usage_budget.TOKEN_FIELDS, 0)


def _ceil_ratio(total, accepted):
    return -(-total // accepted)


def settle_root(report, root_task_id, accepted):
    """Root-task settlement over one read of the ledger (``Ledger.root_report``): cost to date per unit, by stage,
    reviewer rescue as incremental rework, unresolved exposure separately, and cost per independently accepted
    delivery. ``accepted`` is the number of canonical acceptances (``None`` when unknown); with none there is no
    finite ratio. USD, tokens and window units are never added or compared across units."""
    summary, holds = report
    stages, tokens, usd, estimated = {}, _zero_tokens(), 0, 0
    counts = (summary or {}).get('stage_attempts', {})
    for stage, entry in sorted(((summary or {}).get('stages') or {}).items()):
        stages[stage] = dict(entry, attempts=counts.get(stage, 0))
    for stage in counts:
        stages.setdefault(stage, dict(usd_micros=0, usd_micros_estimated=0, tokens=_zero_tokens(),
                                      attempts=counts[stage]))
    for entry in stages.values():
        usd += entry['usd_micros']
        estimated += entry['usd_micros_estimated']
        for field in tokens:
            tokens[field] += entry['tokens'][field]
    units = dict((summary or {}).get('units') or {})
    cost = dict(usd_micros=usd, usd_micros_estimated=estimated, tokens=tokens, units=units)
    held_units = {}
    for hold in holds:
        for key, amount in hold['windows'].items():
            held_units[key] = held_units.get(key, 0) + amount
    unresolved = dict(attempts=len(holds), usd_micros_held=sum(hold['amount'] for hold in holds),
                      units_held=held_units,
                      unknown_cost_attempts=sum(hold['state'] == 'cost_unknown' for hold in holds))
    ratio = numerator = None
    if accepted:  # rounded up per unit, plus the exact fraction so totals reconcile
        ratio = dict(usd_micros=_ceil_ratio(usd, accepted),
                     tokens={field: _ceil_ratio(value, accepted) for field, value in tokens.items()},
                     units={key: _ceil_ratio(value, accepted) for key, value in units.items()})
        numerator = dict(usd_micros=usd, tokens=dict(tokens), units=dict(units))
    rescue = stages.get('rescue') or dict(usd_micros=0, usd_micros_estimated=0, tokens=_zero_tokens(), attempts=0)
    return dict(state='settled', root_task_id=root_task_id, attempts=(summary or {}).get('attempts', 0),
                cost_to_date=cost, by_stage=stages, rework=rescue, executor_quality='not_scored',
                unresolved=unresolved, acceptance='unavailable' if accepted is None else 'canonical',
                accepted=accepted, ratio=ratio, ratio_num=numerator, ratio_den=accepted if ratio else None,
                units_dropped=(summary or {}).get('units_dropped', 0))


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


def _capped(ids):
    ids = sorted(ids)
    return dict(ids=ids[:usage_budget.SUMMARY_IDS], more=max(0, len(ids) - usage_budget.SUMMARY_IDS))


def _counted(ids):
    return dict(count=len(ids), **_capped(ids))


def _by_organization(entries):
    entries = sorted(entries, key=lambda entry: entry['organization'])
    return dict(organizations=entries[:usage_budget.SUMMARY_IDS], more=max(0, len(entries) - usage_budget.SUMMARY_IDS))


RECONCILE_HINT = ('Operator recovery (one-shot commands): stop the worker first. reconcile_uncertain settles an uncertain '
                  'dollar hold from provider receipts (charged as reported, at least 1 micro-USD; the unspent rest is '
                  "released on the operator's word) or charge_reservation (the full reservation, nothing released). "
                  'resolve_block over_bound confirms or dismisses the listed run ids. '
                  'resolve_block legacy needs the marker listed here, after reviewing the unresolved items; a stale '
                  'marker is refused.')


def reconcile_summary(data):
    """What each S3.6b-2 operator op can resolve, with counts and capped ids, plus a fixed hint (None when there is
    nothing). A legacy entry carries the `marker` `resolve_block` needs and the unresolved items it covers."""
    uncertain = [attempt for attempt, hold in Ledger.open_holds(data).items()
                 if hold['state'] == 'uncertain' and hold.get('amount')]
    over_bound, legacy = [], []
    for organization, org in data['organizations'].items():
        runs = Ledger.over_bound_runs(data, organization)
        if runs:
            over_bound.append(dict(organization=organization, **_counted(runs)))
        if org.get('legacy_block'):
            items = sorted({item[1] for item in Ledger.unresolved_items(data, organization)})
            legacy.append(dict(organization=organization, marker=Ledger.legacy_marker(data, organization),
                               unresolved=_counted(items)))
    summary = dict(reconcile_uncertain=_counted(uncertain),
                   resolve_block=dict(over_bound=_by_organization(over_bound), legacy=_by_organization(legacy)))
    return sub_reconcile.summary(data, summary, RECONCILE_HINT if uncertain or over_bound or legacy else None)


def ledger_summary(data):
    """Counts and nonsecret labels only: open holds by state and unstamped, caps and the user's organizations."""
    holds = {}
    for hold in Ledger.open_holds(data).values():
        holds[hold['state']] = holds.get(hold['state'], 0) + 1
    total, per_account = Ledger._caps(data)
    organizations = [dict(id=key, enabled=not org.get('disabled', False))
                     for key, org in sorted(data['organizations'].items())]
    # Holds admitted before `authority_required` keep the old rules; the canary needs this count at zero.
    unstamped = sum(1 for attempt in Ledger.open_holds(data)
                    if not (Ledger.admission(data, attempt) or {}).get('authority_required'))
    # Open holds with no workflow job (S3.5): fine for explicit delegation, never for a planned job.
    unbound = sum(1 for attempt in Ledger.open_holds(data)
                  if not ((Ledger.admission(data, attempt) or {}).get('binding') or {}).get('job'))
    blocked = []
    for organization in sorted(data['organizations']):
        reasons = Ledger.block_reasons(data, organization)
        if reasons:
            sources = dict(overrun=Ledger.pending_overruns, over_bound=Ledger.over_bound_runs)
            blocked.append(dict(id=organization, reasons={
                reason: _capped(sources[reason](data, organization) if reason in sources else [])
                for reason in sorted(reasons)}))
    unknown = [attempt for attempt, hold in Ledger.open_holds(data).items() if hold['state'] == 'cost_unknown']
    # Uncertain dollar holds leave only through the operator's `reconcile_uncertain`; the operator must see them.
    uncertain = [attempt for attempt, hold in Ledger.open_holds(data).items()
                 if hold['state'] == 'uncertain' and hold.get('amount')]
    reconcile, hint = reconcile_summary(data)
    return dict(holds=holds, open_attempts=len(Ledger.open_attempts(data)), reconcile=reconcile, reconcile_hint=hint,
                blocked_organizations=blocked, cost_unknown=_capped(unknown), uncertain_dollar=_capped(uncertain),
                overrun_backlog=Ledger.overrun_backlog(data),
                authority_required=bool(data.get('authority_required')), unstamped_open_holds=unstamped,
                unbound_open_holds=unbound,
                caps=dict(total=total, per_account=per_account), organizations=organizations)
