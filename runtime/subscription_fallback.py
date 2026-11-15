"""Provider-managed fallback exposure (design 4.1): the included-quota fence and the terminal consumption boundary.

A fallback admission (`routing.FALLBACK`, no included claim) that may have been sent can still consume an unknown
amount of its pool. While any such attempt is dispatched or uncertain, its pool takes no included reservation and no
included admission of it dispatches. Each supported terminal disposition writes the pool's consumed boundary
(`routing_observations.mark_consumed`) before its closure commits, and an included admission whose snapshot is no newer
than that boundary never dispatches. Exposure comes from the ledger's own open holds and admission records (records
of open holds are never pruned): no second registry, send-time mark or timestamp field. Reads only; no process.
"""
from runtime.usage_budget import Ledger

EXPOSED_STATES = ('dispatched', 'uncertain')
FENCED = 'fallback_exposure_open'
# The quota disposition of a fallback admission, stored in its `reason_codes` beside `routing.FALLBACK`.
QUOTA_UNKNOWN, QUOTA_INSUFFICIENT, QUOTA_FENCED = 'quota_unknown', 'quota_insufficient', 'quota_fenced'
SUBSCRIPTION = 'included_subscription'


def unmeasured(route):
    """A subscription route whose demand is explicitly unknown (None): only ever provider-managed fallback."""
    return isinstance(route, dict) and route.get('demand') is None and route.get('billing_class') == SUBSCRIPTION


def _unknown(route, pool, now):
    """Whether an eligible subscription route's included quota or demand is unknown: no or empty demand, or a demanded
    window missing, unread, not fresh or past its reset. Malformed numbers are left to `routing._candidate`."""
    if not (isinstance(route, dict) and route.get('billing_class') == SUBSCRIPTION
            and route.get('provider_fallback') is True and isinstance(pool, dict) and 'demand' in route):
        return False  # a missing health key stays `observation_incomplete`
    if not route.get('demand'):
        return True
    windows = {window.get('id'): window for window in pool.get('windows', []) if isinstance(window, dict)}
    return any(window is None or window.get('used') is None or window.get('fresh') is not True
               or (window.get('reset_at') is not None and window['reset_at'] <= now)
               for window in map(windows.get, route['demand']))


def candidate(data, route, observations, assignment, settings, held, now):
    """`routing._candidate` with unknown quota classified after every non-quota check: an eligible subscription route
    (`provider_fallback`) whose quota/demand is unknown becomes its provider-managed fallback (no included claim,
    `QUOTA_UNKNOWN`); identity, binding, capability, permission and observation-integrity refusals stand. An empty
    demand never becomes an included claim."""
    from runtime import routing  # routing imports this module
    pool = observations.get('pools', {}).get(route.get('pool')) if isinstance(route, dict) else None
    if not _unknown(route, pool, now):
        if isinstance(route, dict) and route.get('billing_class') == SUBSCRIPTION and route.get('demand') == {}:
            return None, ['observation_incomplete']
        return routing._candidate(data, route, observations, assignment, settings, held, now)
    item, reasons = routing._candidate(data, dict(route, demand={}), observations, assignment, settings, held, now)
    if item is None:
        return item, reasons
    return dict(item, spec=route, constraints=[], fallback=True, vector={}, quota=QUOTA_UNKNOWN), reasons


def is_fallback(record):
    from runtime import routing  # routing imports this module
    return isinstance(record, dict) and routing.FALLBACK in (record.get('reason_codes') or ())


def active_pools(data):
    """The pools with a dispatched or uncertain fallback attempt (an empty included reservation still counts)."""
    records = {record.get('attempt_id'): record for record in data.get('admissions', [])}
    return {records[attempt]['pool'] for attempt, hold in Ledger.open_holds(data).items()
            if hold['state'] in EXPOSED_STATES and is_fallback(records.get(attempt))}


def fence(found, data):
    """`routing._candidate`'s `(item, reasons)` under the fence: an included candidate on an active pool becomes the
    route's provider-managed fallback (`QUOTA_FENCED`) when the route has one, and is excluded otherwise."""
    item, reasons = found
    if item is None or item.get('fallback') or item['cls'] != 'subscription' or item['pool'] not in active_pools(data):
        return found
    if item['spec'].get('provider_fallback') is not True:
        return None, [FENCED]
    return dict(item, constraints=[], fallback=True, vector={}, quota=QUOTA_FENCED), reasons


def dispatch_problem(data, record, settings_path):
    """Why an included admission may not dispatch now, or None: its pool has open fallback exposure, or a terminal
    consumption boundary at or after its snapshot time (every reading it used predates or equals that time)."""
    from runtime import routing_observations
    if is_fallback(record) or record.get('billing_class') != 'included_subscription':
        return None
    if record.get('pool') in active_pools(data):
        return FENCED
    if type(record.get('at')) is not int or routing_observations.consumed_at(settings_path, record['pool']) >= record['at']:
        return 'stale_observation'
    return None


def after_record(data, event, settings_path, now):
    """`Ledger.record_event`'s fixed finalization, in its write transaction after the event applied and was stored and
    before COMMIT: a `complete` that has just closed a provider-managed fallback hold writes the pool's terminal
    boundary. The ledger refuses an already-closed hold (`attempt_closed`) and answers a replay or conflict before this
    runs, so a closed hold here was closed by this event. A missing store or failed write raises
    `observation_store_unavailable`, rolling back the closure and the event row so the same event can be retried.
    Returns None."""
    if event.get('kind') != 'complete':
        return None
    hold = data['holds'].get(event['attempt_id'])
    record = Ledger.admission(data, event['attempt_id']) if hold is not None and hold['closed'] else None
    if record is None or not is_fallback(record):
        return None
    from runtime.usage_budget import BudgetError
    try:
        mark_terminal(settings_path, record, now, event['attempt_id'])
    except (OSError, ValueError):
        raise BudgetError('observation_store_unavailable') from None
    return None


def mark_terminal(settings_path, record, at, attempt):
    """Write a fallback's terminal consumption boundary inside the transaction that commits its closure, moving it
    forward even when a rolled-back attempt at that closure already wrote one; refuses without the actual store."""
    from runtime import routing_observations
    if settings_path is None:
        raise ValueError('observation_store_required')
    routing_observations.mark_consumed(settings_path, record['pool'], at, attempt, once=False)
