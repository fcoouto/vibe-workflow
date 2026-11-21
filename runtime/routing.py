"""Pure classification and strategy ranking; no task roster or execution authority."""
import math

WORK_KINDS = ('implementation', 'review', 'investigation', 'planning', 'testing', 'writing')
MAX_INTEGER = 2**53 - 1
MAX_ID = 256
MAX_CLOCK_DRIFT = 60
CONSTRAINT_FIELDS = ('allowed_models', 'excluded_models', 'allowed_efforts', 'allowed_harnesses')
FACTS = {'files': ('1-2', '3+', 'cross-system'), 'spec': ('exact', 'prose', 'open'),
         'risk': ('reversible', 'shared', 'critical')}
BASE = dict(planning=3, review=2, investigation=2, implementation=1, testing=1, writing=1)
RATIO = (0.55, 0.82, 0.90)
NEAR_SCORE = 0.97
NEAR_PRICE = 0.5
EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')


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


def task_level(work_kind, task):
    if work_kind not in BASE or type(task) is not dict or set(task) - {*FACTS, 'context_tokens'}:
        raise ValueError('invalid_request')
    tokens = task.get('context_tokens', 0)
    if type(tokens) is not int or not 0 <= tokens <= 10**9:
        raise ValueError('invalid_request')
    levels = [BASE[work_kind]]
    for fact, values in FACTS.items():
        if task.get(fact) not in values:
            raise ValueError('invalid_request')
        levels.append(values.index(task[fact]) + 1)
    return max(levels)


def check_constraints(constraints):
    if type(constraints) is not dict or set(constraints) - set(CONSTRAINT_FIELDS):
        raise ValueError('invalid_request')
    for value in constraints.values():
        if type(value) is not list or len(value) > 256 or not all(_text(item) for item in value):
            raise ValueError('invalid_request')
    return {key: list(value) for key, value in constraints.items()}


def price(input_per_m, output_per_m):
    return (3 * input_per_m + output_per_m) / 4


def effort_for(level, variants):
    target = EFFORTS.index(('low', 'medium', 'high')[level - 1])
    known = sorted(EFFORTS.index(v) for v in variants if v in EFFORTS)
    if not known:
        return None
    above = [i for i in known if i >= target]
    return EFFORTS[above[0] if above else known[-1]]


def order(candidates, level):
    floor = max((item['score'] for item in candidates), default=0) * RATIO[level - 1]
    ok = [item for item in candidates if item['score'] >= floor]
    cheapest = min((item['price'] for item in ok), default=0)
    near = [item for item in candidates if item['score'] >= NEAR_SCORE * floor
            and item['price'] <= NEAR_PRICE * cheapest]
    picked = {item['route']: item for item in ok + near}.values()
    return sorted(picked, key=_cheapest_first)


def _cheapest_first(item):
    # At equal price and score, an own score ranks before a family-borrowed one.
    return item['price'], -item['score'], item.get('score_source') == 'family', -item['released'], item['route']


def _identity(item, bases):
    creator = item['creator']
    key = '-'.join(part for part in item['key'].split('-') if not (part.isdigit() and len(part) >= 8))
    # A tier of a model (`-fast`, `-free`) is that model: same lab, a suffix without digits and the same score.
    # A sibling with its own score (`-mini`, `-pro`) or a step in digits is a different model.
    tiers = [base for (owner, base), scores in bases.items() if owner == creator and key.startswith(base + '-')
             and not any(ch.isdigit() for ch in key[len(base) + 1:]) and item['score'] in scores]
    return creator, min(tiers, key=len) if tiers else key


def _strongest_first(item):
    return -item['score'], *_cheapest_first(item)


def seats(candidates):
    """Council seat order: frontier models one per lab, then the rest of the frontier, then the strongest below."""
    bases = {}
    for item in candidates:
        bases.setdefault(_identity(item, {}), set()).add(item['score'])
    kept = {}
    for item in sorted(candidates, key=_strongest_first):
        kept.setdefault(_identity(item, bases), item)
    ranked = sorted(kept.values(), key=_strongest_first)
    floor = RATIO[2] * max((item['score'] for item in ranked), default=0)
    first, labs = [], set()
    for item in ranked:
        if item['score'] >= floor and item['creator'] is not None and item['creator'] not in labs:
            first.append(item)
            labs.add(item['creator'])
    return first + [item for item in ranked if all(item is not seated for seated in first)]
