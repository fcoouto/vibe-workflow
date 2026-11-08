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

from runtime import routing_allocator as allocator
from runtime.usage_budget import BudgetError, Ledger

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
ROUTE_IDENTITY = ('provider', 'model', 'effort', 'harness')
SECONDS_PER_HOUR = 3600
DEFAULT_MINIMUM_HORIZON = 900


def _pending(reason, reasons=()):
    return dict(status='pending', reason_code=reason, reason_codes=list(dict.fromkeys([reason, *reasons])))


def _key(pool, window):
    return '%s/%s' % (pool, window)


def _headroom(data, window, pool, held, task, now):
    """Usable units in one window after caps, outstanding holds and (for a funded pool) the organization."""
    if window.get('organization') is not None:
        base = Ledger._headroom(data, window['organization'], task, int(now))
    else:
        base = window['limit'] - window['used'] - window.get('protected', 0)
    return base - held.get(_key(pool, window['id']), 0)


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
        if not isinstance(credential, dict) or credential.get('state') != 'ready':
            reasons.append('credential_unavailable')
        elif credential.get('scope') != scope:
            reasons.append('credential_scope_mismatch')
        if route.get('funding_verified') is not True:
            reasons.append('funding_unverified')
    elif route.get('binding_enforceable', True) is not True:
        reasons.append('binding_unenforceable')
    return reasons


def _candidate(data, route, observations, assignment, settings, held, accounts, now):
    """Return (candidate or None, reasons) for one route; eligibility, billing class, then feasibility."""
    cls = FUNDING_OF.get(route.get('billing_class'))
    if cls is None:
        return None, ['billing_class_excluded']
    reasons = _credential_reasons(route, assignment['scope'], settings)
    pool = observations.get('pools', {}).get(route['pool'])
    if pool is None:
        reasons.append('pool_unknown')
    elif pool.get('billing_class', route['billing_class']) != route['billing_class']:
        reasons.append('pool_funding_mismatch')  # a direct grant never funds a gateway, nor the reverse
    if route.get('capable', True) is not True:
        reasons.append('capability_missing')
    if route.get('permitted', True) is not True:
        reasons.append('not_permitted')
    if accounts.count(route['account']) >= settings['caps']['per_account']:
        reasons.append('concurrency_cap')
    if reasons:
        return None, reasons
    windows = {window['id']: window for window in pool['windows']}
    vector, constraints = {}, []
    bounds = route.get('next_request_bound', {})
    for window_id, estimate in route.get('demand', {}).items():
        window = windows.get(window_id)
        if window is None:
            return None, ['pool_unknown']
        stale = (window.get('used') is None or window.get('fresh', True) is not True
                 or (window.get('reset_at') is not None and window['reset_at'] <= now))
        if stale:
            return None, ['stale_observation']
        try:
            reserve = Ledger._reserve(estimate, bounds.get(window_id), settings['margin_bp'])
            room = _headroom(data, window, route['pool'], held, assignment['task'], now)
        except BudgetError:
            return None, ['funding_unverified']
        if reserve > room:
            return None, ['insufficient_headroom']
        vector[_key(route['pool'], window_id)] = reserve
        constraints.append(dict(pool=route['pool'], window=window_id, headroom=room, cost=estimate,
                                resets_at=window.get('reset_at')))
    return dict(route=route['route'], pool=route['pool'], constraints=constraints, vector=vector, cls=cls,
                spec=route, windows=pool['windows']), []


def _utilization(candidates, held, settings, now):
    """Lowest projected maximum over feasible subscription routes; unknown is conservative, never zero."""
    best, reasons = None, []
    for item in candidates:
        windows, reserve_new = [], {}
        for window in item['windows']:
            limit = window['limit']
            windows.append(dict(id=window['id'], used=None if window.get('used') is None else window['used'] / limit,
                                reserved=held.get(_key(item['pool'], window['id']), 0) / limit,
                                length=window.get('length'), reset_at=window.get('reset_at'),
                                fresh=window.get('fresh', True), rolling=window.get('rolling', False)))
            reserve_new[window['id']] = item['vector'].get(_key(item['pool'], window['id']), 0) / limit
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
    found = allocator._binding(item['constraints'], now, horizon)
    pressure = None if found is None or found[1] is None else float(found[0] / found[1])
    return dict(pressure=pressure, constraints=[dict(c) for c in item['constraints']])


def _choice_matches(entry, item):
    spec = item['spec']
    return all(spec.get(field) == entry[field] for field in ROUTE_IDENTITY) and item['cls'] == entry['funding']


def _decide(data, assignment, ranking, settings, observations):
    now, horizon = observations['now'], observations.get('minimum_horizon', DEFAULT_MINIMUM_HORIZON)
    accounts, held = Ledger.open_accounts(data), Ledger.held(data)
    if len(accounts) >= settings['caps']['total']:
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
                  billing_class=spec['billing_class'], credential_ref=credential.get('ref'),
                  pressure=dict(u=u, e=e, **_snapshot(item, now, horizon)), reserved=dict(item['vector']),
                  settings_revision=settings.get('revision', 0), at=now, mode=mode,
                  reason_codes=list(dict.fromkeys(reasons)),
                  ranking=dict(table_version=ranking.get('table_version'), profile_digest=ranking.get('profile_digest')))
    return dict(record=record, vector=item['vector'], account=spec['account'])


def _dominant(excluded):
    """The reason a pending assignment reports: a concurrency place first, then the commonest exclusion."""
    if 'concurrency_cap' in excluded:
        return 'concurrency_cap'
    return 'no_feasible_route'


def _valid_assignment(assignment):
    return (isinstance(assignment, dict) and all(isinstance(assignment.get(key), str) and assignment[key]
                                                 for key in ('attempt_id', 'task', 'role', 'complexity_band', 'scope')))


def admit(assignment, ranking, settings, observations, *, ledger):
    """Choose a funding class and route, reserve the padded claim and record the decision atomically.

    ``assignment`` is ``{attempt_id, task, role, complexity_band, scope, choice?}``; ``ranking`` the
    result of ``rank`` (empty ``strategy_ids`` falls back to worker choices, never blocks); ``settings`` a
    resolved ``routing_settings`` dict plus ``revision``; ``observations`` the cached, already-collected
    ``{now, routes, pools, epoch_at?, minimum_horizon?}``. Returns the admission record with
    ``status='admitted'`` (``duplicate=True`` for a repeated attempt) or ``status='pending'`` with a
    ``reason_code``. Pending and duplicate decisions consume nothing and create no record. This is not
    dispatch authority.
    """
    if not _valid_assignment(assignment):
        return _pending('invalid_assignment')
    try:
        outcome = ledger.admit(assignment['attempt_id'],
                               lambda data: _decide(data, assignment, ranking, settings, observations))
    except BudgetError as error:
        return _pending(str(error))
    return outcome if outcome.get('status') == 'pending' else dict(outcome, status='admitted')
