"""Smart Routing task profiles and deterministic ranking.

An empty ranking (``no_recommendation``) is never a refusal: callers fall through to the
deterministic admission path or the user's explicit worker choices. Only admission can block.

Pure functions: no network, subprocess or model call happens here except through
the caller-supplied, optional ``jev`` enrichment callable. Nothing in this module
grants spending, launch or review authority.
"""
import hashlib
import json

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
