"""Expiry priority for subscription quota, and the comparable slot order it may reorder.

Pure: the caller supplies the meters, the clock and the route maps. A meter is one whole-second reading of a plan
window. Only a fresh, verified 5-hour or weekly window with a discrete reset earns a boost, scored from the reading's
earliest time. Headroom, pace and expected longer siblings suppress a boost. Reordering moves only the slots of
comparable, known candidates, and only when one from another subscription clears the minimum gain. Inputs are never
mutated. Account binding, storage and selection belong to the caller.

A provider's stored ranking batch holds the meters of its latest recognized usage reply and the interval they span.
`batch_after` applies the one interval rule every provider shares; `batch_meters` re-checks a stored batch on read.
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
SAFE_INT = 2 ** 53  # larger integers are invalid input: the scorer never converts them to floats
KEY = re.compile(r'5h|7d|monthly|(?:family|model):.+|duration:[1-9][0-9]*')
HEADROOM_USED = 90  # more than 90% used leaves under 10 points, and blocks every boost on the route
SCORE_FLOOR = 0.01  # 1% of the window length, so a score tops out at 100
MIN_GAIN = 0.25
PRICE_RATIO = 1.5
SCORE_RATIO = 0.97
TOLERANCE = routing.TOLERANCE


def _safe_int(value):
    """An integer that is exact as a float, so mixed arithmetic cannot overflow or round."""
    return type(value) is int and abs(value) <= SAFE_INT


def _number(value):
    if type(value) is int:
        return _safe_int(value)
    return type(value) is float and math.isfinite(value)


def _percent(value):
    return _number(value) and 0 <= value <= 100


def _shaped(value):
    if type(value) is not dict or set(value) != FIELDS:
        return False
    if not _percent(value['used_percent']) or type(value['discrete']) is not bool:
        return False
    if any(not _safe_int(value[name]) for name in ('earliest', 'latest', 'reset_at')):
        return False
    duration = value['duration']
    return duration is None or (_safe_int(duration) and duration > 0)


def fresh_meter(value, *, now):
    """True for a well-formed reading that was received within the freshness window and has not reset yet."""
    if not _shaped(value) or not _safe_int(now):
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


def _batch_shape(value):
    return type(value) is dict and set(value) == {'earliest', 'latest', 'meters'} and type(value['meters']) is dict \
        and _safe_int(value['earliest']) and _safe_int(value['latest']) and 0 <= value['earliest'] <= value['latest']


def batch_after(previous, *, earliest, latest, meters):
    """The ranking batch after one recognized usage reply spanning `earliest` to `latest` (epoch seconds) with `meters`.

    A reply that starts strictly after the stored batch's latest replaces it, even with no valid meters. A reply that
    ends before the stored batch's earliest is provably older and changes nothing. Any other reply overlaps: its meters
    are discarded, and the combined interval becomes the boundary that a later reply must clear. A malformed stored
    batch counts as absent. Inputs are never mutated."""
    old = previous if _batch_shape(previous) else None
    if old is None or earliest > old['latest']:
        return dict(earliest=earliest, latest=latest, meters=dict(meters))
    if latest < old['earliest']:
        return old  # retained as stored: its contents are never traversed, so a corrupt meter cannot fail here
    return dict(earliest=min(old['earliest'], earliest), latest=max(old['latest'], latest), meters={})


def batch_meters(batch, *, now, keep):
    """The fresh, well-formed meters of a stored batch whose key `keep` accepts, each with timestamps equal to the
    batch's own interval. A malformed, stale or mismatched meter is dropped alone, and a malformed batch yields none.
    The result is a copy."""
    if not _batch_shape(batch):
        return {}
    return {key: dict(value) for key, value in batch['meters'].items()
            if keep(key) and fresh_meter(value, now=now)
            and (value['earliest'], value['latest']) == (batch['earliest'], batch['latest'])}


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


def _identity(subscriptions, route):
    """A route's subscription path ID: a nonempty string. Absent, null, empty or non-string values are None."""
    value = subscriptions.get(route) if type(route) is str else None
    return value if type(value) is str and value else None


def _expiry_of(expiry, route):
    value = expiry.get(route) if type(route) is str else None
    return value if _number(value) else None


def reorder(ordered, *, subscriptions, expiry):
    """The baseline order, with the slots of comparable, known candidates refilled by expiry priority when a candidate
    on a different subscription path beats the baseline winner by the minimum gain. Ties keep baseline order.
    A candidate without a valid path ID or expiry stays in its slot; a baseline without them returns the order."""
    result = list(ordered)
    if not ordered:
        return result
    base = ordered[0]
    base_identity = _identity(subscriptions, base.get('route'))
    base_expiry = _expiry_of(expiry, base.get('route'))
    if base_identity is None or base_expiry is None or not comparable(base, base):
        return result
    slots, lanes = [], {}
    for index, item in enumerate(ordered):
        route = item.get('route')
        identity, value = _identity(subscriptions, route), _expiry_of(expiry, route)
        if identity is not None and value is not None and comparable(item, base):
            slots.append(index)
            lanes[index] = (identity, value)
    qualifies = any(lanes[index][0] != base_identity and lanes[index][1] >= base_expiry + MIN_GAIN - TOLERANCE
                    for index in slots)
    if not qualifies:
        return result
    ranked = sorted(slots, key=lambda index: -lanes[index][1])
    for position, index in zip(slots, ranked):
        result[position] = ordered[index]
    return result
