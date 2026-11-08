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


def _route_windows(windows):
    return windows if isinstance(windows, dict) else {None: windows}


def _reserve_for(reserve_new, window):
    if isinstance(reserve_new, dict):
        return reserve_new.get(window.get('id'), 0)
    return reserve_new


def _projected_window(window, reserve_new, now, min_elapsed_fraction, reasons):
    """Project one window to its reset as a fraction of its limit, or None when unknown."""
    used, length, reset_at = window.get('used'), window.get('length'), window.get('reset_at')
    if (window.get('fresh', True) is not True or not _number(used) or not _number(length) or length <= 0
            or (reset_at is not None and (not _number(reset_at) or reset_at <= now))):
        reasons.append('stale_observation')
        return None
    total = used + window.get('reserved', 0) + _reserve_for(reserve_new, window)
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
    limit. `reserve_new` is a fraction for every window or a dict keyed by window id. A route with any
    stale or unknown window is skipped; with no usable route the result is None, never zero.
    """
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
    if not (_number(remaining) and remaining > 0 and _number(hours_to_expiry) and hours_to_expiry > 0):
        return 0.0
    stranded = max(0, remaining - max(0, expected_spend or 0))
    return float(clamp(1 - hours_to_expiry / T_hours) * clamp(stranded / remaining))


def api_share(u, e, L=0.65, H=0.85):
    """API_share = 1 - (1 - S)(1 - E) with S = clamp((u - L)/(H - L)); u must be known."""
    if not _number(u):
        raise ValueError('utilization_unknown')
    s = clamp((u - L) / (H - L))
    return float(1 - (1 - s) * (1 - clamp(e)))


def choose_class(shares, admissions, bucket, epoch, feasible):
    """Pick the funding class with the largest deficit, or None when no class is eligible.

    `admissions` are recorded {bucket, at, shares, class} rows. Only rows in `bucket` at or after `epoch`
    count. deficit_c = sum(recorded share_c) + current share_c - count(admitted to c). Only feasible classes
    with a positive current share compete; ties favor 'api'. Nothing is recorded here.
    """
    eligible = [c for c in shares if c in set(feasible) and shares[c] > 0]
    if not eligible:
        return None
    rows = [r for r in admissions if r['bucket'] == bucket and r['at'] >= epoch]
    deficit = {c: round(sum(r['shares'].get(c, 0) for r in rows) + shares[c]
                        - sum(r['class'] == c for r in rows), 9) for c in eligible}
    return max(eligible, key=lambda c: (deficit[c], c == 'api'))


def _binding(constraints, now, minimum_horizon):
    """Return (fundable_work, horizon, reset time) over positive-demand constraints."""
    best = None
    for item in constraints:
        cost = item['cost']
        if cost <= 0:
            continue
        work = Fraction(item['headroom']) / Fraction(cost)
        resets_at = item.get('resets_at')
        key = (work, float('inf') if resets_at is None else resets_at, (item['pool'], item.get('window', '')))
        if best is None or key < best:
            best = key
    if best is None:
        return None
    work, resets_at, _ = best
    if resets_at == float('inf'):
        return work, None, None
    return work, max(resets_at - now, minimum_horizon), resets_at


def choose_route(candidates, now, minimum_horizon):
    """Admit the feasible candidate with the highest pressure = fundable_work / max(horizon, minimum).

    `candidates` are in evidence-rank order: {route, pool, constraints: [{pool, window, headroom, cost,
    resets_at}]}. Only the best-ranked candidate of each execution pool takes part, so aliases and variants
    sharing a pool add no weight. Zero-cost constraints are skipped; with no finite pressure the ranking
    order decides. Ties go to the earliest binding reset, then the pool id. Returns None when nothing can
    be funded.
    """
    seen, scored, unscored = set(), [], []
    for rank, candidate in enumerate(candidates):
        if candidate['pool'] in seen:
            continue
        seen.add(candidate['pool'])
        found = _binding(candidate['constraints'], now, minimum_horizon)
        if found is None or found[1] is None:
            unscored.append((rank, candidate['pool'], candidate['route']))
            continue
        work, horizon, resets_at = found
        if work <= 0:
            continue  # an exhausted window vetoes the candidate
        scored.append((-(work / Fraction(horizon)), resets_at, candidate['pool'], candidate['route']))
    if scored:
        return min(scored)[3]
    if unscored:
        return min(unscored, key=lambda item: (item[0], item[2]))[2]
    return None
