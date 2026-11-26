"""Expiry priority for subscription quota, and the comparable slot order it may reorder.

Pure: the caller supplies the meters, the clock and the route maps. A meter is one whole-second reading of a plan
window. Only a fresh, verified 5-hour or weekly window with a discrete reset earns a boost, scored from the reading's
earliest time. Headroom, pace and expected longer siblings suppress a boost. Reordering moves only the slots of
comparable, known candidates, and only when one from another subscription clears the minimum gain. Inputs are never
mutated. Account binding, storage and selection belong to the caller.
"""
import math
import re

from runtime import routing, storage

BOOST_WINDOWS = ('5h', '7d')
LENGTHS = {window: length for window, length in storage.RATE_WINDOWS.values()}  # the purported duration of 5h and 7d
SIBLINGS = {  # longer windows a boost needs fresh and consistent; a protection-only sibling satisfies presence
    'claude': {'5h': ('7d',)},
    'chatgpt': {'5h': ('7d',)},
    'go': {'5h': ('7d', 'monthly'), '7d': ('monthly',)},
}
FIELDS = frozenset(('used_percent', 'earliest', 'latest', 'reset_at', 'duration', 'discrete'))
KEY = re.compile(r'5h|7d|monthly|(?:family|model):.+|duration:[1-9][0-9]*')
HEADROOM_USED = 90  # more than 90% used leaves under 10 points, and blocks every boost on the route
SCORE_FLOOR = 0.01  # 1% of the window length, so a score tops out at 100
MIN_GAIN = 0.25
PRICE_RATIO = 1.5
SCORE_RATIO = 0.97
TOLERANCE = routing.TOLERANCE


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _percent(value):
    return _number(value) and 0 <= value <= 100


def _shaped(value):
    if type(value) is not dict or set(value) != FIELDS:
        return False
    if not _percent(value['used_percent']) or type(value['discrete']) is not bool:
        return False
    if any(type(value[name]) is not int for name in ('earliest', 'latest', 'reset_at')):
        return False
    duration = value['duration']
    return duration is None or (type(duration) is int and duration > 0)


def fresh_meter(value, *, now):
    """True for a well-formed reading that was received within the freshness window and has not reset yet."""
    if not _shaped(value) or type(now) is not int:
        return False
    earliest, latest, reset = value['earliest'], value['latest'], value['reset_at']
    if not (earliest <= latest <= now + 1 and earliest <= now < reset and reset >= latest
            and now - earliest < storage.FRESH_SECONDS):
        return False
    return value['duration'] is None or reset <= latest + value['duration'] + storage.CLOCK_MARGIN


def _verified(value):
    """A discrete reset with a known length: the only kind of meter that may score or pace-veto."""
    return value['discrete'] and value['duration'] is not None


def _time_left(value):
    return value['reset_at'] - value['earliest']  # from the earliest reading time, never from the pick clock


def _remaining(value):
    return (100 - value['used_percent']) / 100


def _fraction(value):
    """Time left as a share of the window, clamped to 1: a backdated earliest may run past a fresh reset's start."""
    return min(_time_left(value) / value['duration'], 1)


def _pace(value):
    return _remaining(value) / _fraction(value)


def _recognized(key):
    return type(key) is str and KEY.fullmatch(key) is not None


def _valid(key, value, now):
    """A fresh reading whose purported length matches its 5h or 7d key; anything else is unknown by itself."""
    return fresh_meter(value, now=now) and (key not in LENGTHS or value['duration'] in (None, LENGTHS[key]))


def _missing_sibling(key, fresh, siblings):
    return any(name not in fresh for name in siblings.get(key, ()))


def _paces_out(meter, name, other):
    """Only a strictly longer, later-resetting, verified meter can veto a boost; a duration key only protects headroom."""
    if name.startswith('duration:') or not _verified(other) or other['duration'] <= meter['duration'] \
            or other['reset_at'] <= meter['reset_at']:
        return False
    return _pace(other) < 1 - TOLERANCE


def expiry_score(family, meters, *, now):
    """The route's expiry priority: 1.0 when nothing earns a boost, the largest unsuppressed boost otherwise, None
    when no valid, fresh, verified 5-hour or weekly window exists. An unknown or invalid meter is ignored alone."""
    siblings = SIBLINGS.get(family)
    if siblings is None or type(meters) is not dict:
        return None
    fresh = {key: value for key, value in meters.items() if _recognized(key) and _valid(key, value, now)}
    boosts = [key for key in BOOST_WINDOWS if key in fresh and _verified(fresh[key])]
    if not boosts:
        return None
    if any(value['used_percent'] > HEADROOM_USED for value in fresh.values()):
        return 1.0
    best = 1.0
    for key in boosts:
        if _missing_sibling(key, fresh, siblings):
            continue
        if any(_paces_out(fresh[key], name, other) for name, other in fresh.items()):
            continue
        best = max(best, _remaining(fresh[key]) / max(_fraction(fresh[key]), SCORE_FLOOR))
    return best


def comparable(candidate, baseline):
    """Within the price proxy and adjusted score bounds of the baseline winner; the score bound alone uses the tolerance."""
    if not all(_number(item.get(field)) for item in (candidate, baseline) for field in ('price', 'score')):
        return False
    return candidate['price'] <= PRICE_RATIO * baseline['price'] \
        and candidate['score'] >= SCORE_RATIO * baseline['score'] - TOLERANCE


def reorder(ordered, *, families, expiry):
    """The baseline order, with the slots of comparable, known candidates refilled by expiry priority when a candidate
    from another subscription beats the baseline winner by the minimum gain. Ties keep baseline order."""
    result = list(ordered)
    if not ordered:
        return result
    base = ordered[0]
    route = base.get('route')
    if route not in families or route not in expiry or not _number(expiry[route]) or not comparable(base, base):
        return result
    base_family, base_expiry = families[route], expiry[route]
    slots = []
    for index, item in enumerate(ordered):
        name = item.get('route')
        if name in families and name in expiry and _number(expiry[name]) and comparable(item, base):
            slots.append(index)
    qualifies = any(families[ordered[index]['route']] != base_family
                    and expiry[ordered[index]['route']] >= base_expiry + MIN_GAIN - TOLERANCE for index in slots)
    if not qualifies:
        return result
    ranked = sorted(slots, key=lambda index: -expiry[ordered[index]['route']])
    for position, index in zip(slots, ranked):
        result[position] = ordered[index]
    return result
