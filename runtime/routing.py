"""Smart Routing task profiles and deterministic ranking.

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


class ProfileUnavailable(ValueError):
    """Floors cannot be established, for example an unknown role or missing scope."""


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= 64 and value.isprintable()


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
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return hashlib.sha256(data).hexdigest()


def _claims(job):
    claims = job.get('claims') or []
    return sorted((str(c.get('kind')), str(c.get('key')), str(c.get('mode'))) for c in claims if isinstance(c, dict))


def _floors(job):
    """Requirement floors: explicit job floors plus those implied by write scope. Never lowered."""
    floors = {}
    explicit = job.get('requirement_floors') or {}
    for field in _ORDERS:
        if explicit.get(field) in _ORDERS[field]:
            floors[field] = explicit[field]
    if any(mode == 'write' for _, _, mode in _claims(job)):
        for field, band in WRITE_FLOORS.items():
            floors[field] = _higher(field, floors.get(field), band)
    return floors


def _higher(field, left, right):
    order = _ORDERS[field]
    candidates = [item for item in (left, right) if item in order]
    return max(candidates, key=order.index) if candidates else None


def _tools(job):
    return sorted({item for item in (job.get('tools') or []) if _name(item)})


def semantic_digest(job):
    """Digest of objective, role, scope/context, tools, requirement floors and contract version only."""
    return _digest(dict(version=CONTRACT_VERSION, objective=job.get('objective'), role=job.get('role'),
                        scope_ref=job.get('scope_ref'), claims=_claims(job), context=job.get('context'),
                        tools=_tools(job), floors=_floors(job)))


def _estimate_tokens(job):
    return (len(str(job.get('objective') or '')) + len(str(job.get('context') or ''))) // 4


def _usable_supplied(job, digest, reasons):
    supplied = job.get('profile')
    if not isinstance(supplied, dict):
        return {}
    if set(supplied) - set(SUPPLIED_FIELDS) - set(SUPPLIED_REFERENCES):
        reasons.append('profile_invalid')
        return {}
    # Individually invalid fields are dropped later and take floors; they do not discard valid siblings.
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
    if not isinstance(job, dict) or job.get('role') not in ROLES:
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
    tools = sorted(set(_tools(job)) | set(supplied.get('tools') or []))
    tools_source = 'metadata' if _tools(job) else 'coordinator'
    measured = job.get('context_tokens_measured')
    if type(measured) is int and 0 <= measured <= MAX_TOKENS:
        context, context_source = dict(tokens=measured, kind='measured'), 'metadata'
    elif _valid('context_tokens', supplied.get('context_tokens')):
        context, context_source = dict(tokens=supplied['context_tokens'], kind='estimated'), 'coordinator'
    else:
        context, context_source = dict(tokens=_estimate_tokens(job), kind='estimated'), 'floor'
    uncertainty = 'high' if gaps else 'medium' if enriched else supplied.get('uncertainty') or 'low'
    return dict(contract_version=CONTRACT_VERSION, job_id=job.get('id'), role=job['role'], scope_ref=job['scope_ref'],
                task_family=values['task_family'], complexity_band=values['complexity_band'],
                risk_band=values['risk_band'], review_policy=values['review_policy'], context=context,
                tools=tools, uncertainty=uncertainty, uncertain_fields=gaps, reasons=reasons,
                provenance=dict(provenance, context=context_source, tools=tools_source),
                semantic_digest=digest)
