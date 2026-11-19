"""Pure classification and strategy ranking; no task roster or execution authority."""
import hashlib
import json
import math

CONTRACT_VERSION = 2
TABLE_SCHEMA = 2
COMPLEXITY_BANDS = ('low', 'medium', 'high', 'critical')
RISK_BANDS = COMPLEXITY_BANDS
REVIEW_POLICIES = ('none', 'self', 'independent', 'strict')
WORK_KINDS = ('implementation', 'review', 'investigation', 'planning', 'testing', 'writing')
FLOORS = dict(complexity_band='high', risk_band='high', review_policy='independent')
MAX_INTEGER = 2**53 - 1
MAX_ID = 256
MAX_CLOCK_DRIFT = 60
MAX_WINDOWS = 8
MAX_ROUTES = 64
MAX_SHORTLIST = 10
ROUTE_IDENTITY = ('provider', 'model', 'effort', 'harness')
CONSTRAINT_FIELDS = ('allowed_models', 'excluded_models', 'allowed_efforts',
                     'allowed_harnesses', 'allowed_strategy_ids')


def _text(value):
    return type(value) is str and 0 < len(value) <= MAX_ID and value.isprintable()


def _part(value):
    return _text(value) and '/' not in value


def _amount(value):
    return type(value) is int and 0 <= value <= MAX_INTEGER


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= MAX_INTEGER


def _optional(value, check):
    return value is None or check(value)


class ProfileUnavailable(ValueError):
    """A fixed classification error, never caller text."""


def ensure_profile(job):
    if type(job) is not dict or job.get('work_kind') not in WORK_KINDS:
        raise ProfileUnavailable('invalid_request')
    supplied = job.get('profile', {})
    enums = dict(complexity_band=COMPLEXITY_BANDS, risk_band=RISK_BANDS,
                 review_policy=REVIEW_POLICIES, uncertainty=('low', 'medium', 'high'))
    allowed = {*enums, 'context_tokens', 'tools'}
    if type(supplied) is not dict or set(supplied) - allowed:
        raise ProfileUnavailable('invalid_request')
    for key, values in enums.items():
        if key in supplied and supplied[key] not in values:
            raise ProfileUnavailable('invalid_request')
    tokens, tools = supplied.get('context_tokens', 0), supplied.get('tools', [])
    if type(tokens) is not int or not 0 <= tokens <= 10**9 or type(tools) is not list \
            or len(tools) > 64 or not all(_text(tool) and len(tool) <= 64 for tool in tools):
        raise ProfileUnavailable('invalid_request')
    values = {key: supplied.get(key, value) for key, value in FLOORS.items()}
    digest = hashlib.sha256(json.dumps([job['work_kind'], supplied, job.get('scope_ref')],
                                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(contract_version=CONTRACT_VERSION, work_kind=job['work_kind'], **values,
                context=dict(tokens=tokens, kind='estimated'), tools=sorted(set(tools)),
                uncertainty=supplied.get('uncertainty', 'high' if any(k not in supplied for k in FLOORS) else 'low'),
                semantic_digest=digest, scope_ref=job.get('scope_ref'))


def row_key(work_kind, complexity_band, risk_band):
    return '|'.join((work_kind, complexity_band, risk_band))


def resolve_row(table, profile):
    rows = table.get('rows')
    if type(rows) is not dict or not rows or table.get('schema') != TABLE_SCHEMA:
        return [], 'no_recommendation'
    kind = profile['work_kind']
    c = COMPLEXITY_BANDS.index(profile['complexity_band'])
    r = RISK_BANDS.index(profile['risk_band'])
    exact = rows.get(row_key(kind, profile['complexity_band'], profile['risk_band']))
    if exact:
        return exact, 'ranking_exact'
    stricter = []
    for name, strategies in rows.items():
        parts = name.split('|')
        if len(parts) != 3 or parts[0] != kind or not strategies \
                or parts[1] not in COMPLEXITY_BANDS or parts[2] not in RISK_BANDS:
            continue
        ci, ri = COMPLEXITY_BANDS.index(parts[1]), RISK_BANDS.index(parts[2])
        if ci >= c and ri >= r:
            stricter.append((ci + ri, ci, ri, name, strategies))
    return (min(stricter, key=lambda item: item[:4])[4], 'ranking_stricter_band') if stricter \
        else ([], 'no_recommendation')


def rank(profile, table, constraints):
    if type(constraints) is not dict or set(constraints) - set(CONSTRAINT_FIELDS):
        raise ValueError('invalid_request')
    for value in constraints.values():
        if type(value) is not list or len(value) > 256 or not all(_text(item) for item in value):
            raise ValueError('invalid_request')
    strategies, reason = resolve_row(table, profile)
    reasons = [reason]
    strategies = [item for item in strategies if item.get('context_limit') is None
                  or item['context_limit'] >= profile['context']['tokens']]
    keys = dict(allowed_models='model', allowed_efforts='effort', allowed_harnesses='harness',
                allowed_strategy_ids='strategy_id')
    strategies = [item for item in strategies if item['model'] not in constraints.get('excluded_models', [])
                  and all(item[field] in constraints[key] for key, field in keys.items() if key in constraints)]
    if not strategies and 'no_recommendation' not in reasons:
        reasons.append('no_recommendation')
    return dict(strategy_ids=[item['strategy_id'] for item in strategies], reason_codes=reasons,
                table_version=table.get('version'), profile_digest=profile['semantic_digest'],
                review_policy=profile['review_policy'], scope_ref=profile['scope_ref'])
