"""Pure allocation arithmetic for Smart Routing (spec 7.2 and 7.3).

Nothing here reads a clock, a file or the network. Times are plain numbers in one caller-chosen unit
(hours in the examples) and `now` is always passed in. Class choice is derived from admission records;
no counter is stored.
"""
from fractions import Fraction

EPSILON = 1e-9


def clamp(x):
    return min(1, max(0, x))


def _number(value):
    return type(value) in (int, float) and value == value and abs(value) != float('inf')


def _non_negative(value):
    return _number(value) and value >= 0


def _fixed(reason):
    raise ValueError(reason)


def _route_windows(windows):
    return windows if isinstance(windows, dict) else {None: windows}


def _reserve_for(reserve_new, window):
    if isinstance(reserve_new, dict):
        return reserve_new.get(window.get('id'), 0)
    return reserve_new


def _projected_window(window, reserve_new, now, min_elapsed_fraction, reasons):
    """Project one window to its reset as a fraction of its limit, or None when unknown."""
    used, length, reset_at = window.get('used'), window.get('length'), window.get('reset_at')
    reserved = window.get('reserved', 0)
    if used is not None and not (_non_negative(used) and _non_negative(reserved) and _number(length)
                                 and length > 0 and (reset_at is None or _number(reset_at))):
        reasons.append('invalid_observation')
        return None
    if window.get('fresh', True) is not True or used is None or (reset_at is not None and reset_at <= now):
        reasons.append('stale_observation')
        return None
    total = used + reserved + _reserve_for(reserve_new, window)
    if reset_at is None or window.get('rolling'):
        reasons.append('pace_unavailable')
        return total
    to_reset = reset_at - now
    elapsed = length - to_reset
    if elapsed <= 0 or elapsed + EPSILON < min_elapsed_fraction * length:
        reasons.append('pace_unavailable')
        return total
    pace = used / (elapsed / length)
    return total + pace * (to_reset / length)


def projected_utilization(windows, reserve_new, now, min_elapsed_fraction):
    """Return (u, reasons): the lowest over routes of the highest projected utilization over windows.

    `windows` is one route's window list or a dict of route id to window list. Each window is
    {id, used, reserved, length, reset_at, fresh?, rolling?} with used and reserved as fractions of the
    limit; a malformed window is unknown with reason invalid_observation. `reserve_new` is a fraction for every window or a dict keyed by window id. A route with any
    stale or unknown window is skipped; with no usable route the result is None, never zero.
    """
    if not _number(now) or not (_number(min_elapsed_fraction) and 0 <= min_elapsed_fraction <= 1):
        _fixed('invalid_input')
    for amount in (reserve_new.values() if isinstance(reserve_new, dict) else [reserve_new]):
        if not _non_negative(amount):
            _fixed('invalid_input')
    reasons, best = [], None
    for route_windows in _route_windows(windows).values():
        if not route_windows:
            reasons.append('no_windows')
            continue
        values = [_projected_window(w, reserve_new, now, min_elapsed_fraction, reasons) for w in route_windows]
        if any(v is None for v in values):
            continue
        high = max(values)
        best = high if best is None else min(best, high)
    return best, list(dict.fromkeys(reasons))


def expiry_pressure(remaining, expected_spend, hours_to_expiry, T_hours):
    """E = clamp(1 - t/T) * clamp(stranded/r); zero without a spendable, unexpired grant."""
    if not (_number(T_hours) and T_hours > 0):
        _fixed('invalid_horizon')
    if not (_number(remaining) and remaining > 0 and _number(hours_to_expiry) and hours_to_expiry > 0):
        return 0.0
    stranded = max(0, remaining - max(0, expected_spend or 0))
    return float(clamp(1 - hours_to_expiry / T_hours) * clamp(stranded / remaining))


def api_share(u, e, L=0.65, H=0.85):
    """API_share = 1 - (1 - S)(1 - E) with S = clamp((u - L)/(H - L)); u must be known."""
    if not _number(u):
        raise ValueError('utilization_unknown')
    if not (_number(L) and _number(H) and 0 <= L < H <= 1):
        _fixed('invalid_thresholds')
    if not (_number(e) and 0 <= e <= 1):
        _fixed('invalid_expiry_pressure')
    s = clamp((u - L) / (H - L))
    return float(1 - (1 - s) * (1 - e))


def choose_class(shares, admissions, bucket, epoch, feasible):
    """Pick the funding class with the largest deficit, or None when no class is eligible.

    `admissions` are recorded {bucket, at, shares, class} rows. Only rows in `bucket` at or after `epoch`
    count. deficit_c = sum(recorded share_c) + current share_c - count(admitted to c). Only feasible classes
    with a positive current share compete; ties favor 'api'. Nothing is recorded here.
    """
    allowed = set(feasible)
    eligible = [c for c in shares if c in allowed and shares[c] > 0]
    if not eligible:
        return None
    rows = [r for r in admissions if r['bucket'] == bucket and r['at'] >= epoch]
    deficit = {c: round(sum(r['shares'].get(c, 0) for r in rows) + shares[c]
                        - sum(r['class'] == c for r in rows), 9) for c in eligible}
    return max(eligible, key=lambda c: (deficit[c], c == 'api', c))


def _binding(constraints, now, minimum_horizon):
    """Return (fundable_work, horizon, reset time) over positive-demand constraints."""
    best = None
    for item in constraints:
        cost, headroom, resets_at = item['cost'], item['headroom'], item.get('resets_at')
        if not (_non_negative(cost) and _non_negative(headroom) and (resets_at is None or _number(resets_at))):
            _fixed('invalid_candidate')
        if cost == 0:
            continue
        work = Fraction(headroom) / Fraction(cost)
        key = (work, float('inf') if resets_at is None else resets_at, (item['pool'], item.get('window', '')))
        if best is None or key < best:
            best = key
    if best is None:
        return None
    work, resets_at, _ = best
    if resets_at == float('inf'):
        return work, None, None
    return work, max(resets_at - now, minimum_horizon), resets_at


binding_constraint = _binding  # public name for callers that need the binding pressure terms


def choose_route(candidates, now, minimum_horizon):
    """Admit the feasible candidate with the highest pressure = fundable_work / max(horizon, minimum).

    `candidates` are in evidence-rank order: {route, pool, constraints: [{pool, window, headroom, cost,
    resets_at}]}. Each execution pool contributes its best-ranked currently feasible candidate, so aliases
    and variants sharing a pool add no weight and an exhausted best candidate does not hide the next one.
    An exhausted constraint vetoes a candidate, with or without a reset time. Zero-cost constraints are
    skipped; with no finite pressure the ranking order decides. Ties go to the earliest binding reset, then
    the pool id. Returns None when nothing can be funded; bad numbers raise ValueError.
    """
    if not (_number(now) and _number(minimum_horizon) and minimum_horizon > 0):
        _fixed('invalid_input')
    seen, scored, unscored = set(), [], []
    for rank, candidate in enumerate(candidates):
        if candidate['pool'] in seen:
            continue
        found = _binding(candidate['constraints'], now, minimum_horizon)
        if found is not None and found[0] <= 0:
            continue  # an exhausted window vetoes the candidate; a lower-ranked one in the pool may still serve
        seen.add(candidate['pool'])
        if found is None or found[1] is None:
            unscored.append((rank, candidate['pool'], candidate['route']))
            continue
        work, horizon, resets_at = found
        scored.append((-(work / Fraction(horizon)), resets_at, candidate['pool'], candidate['route']))
    if scored:
        return min(scored)[3]
    if unscored:
        return min(unscored, key=lambda item: (item[0], item[2]))[2]
    return None
