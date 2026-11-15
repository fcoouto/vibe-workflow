"""Provider-managed fallback exposure (design 4.1): the included-quota fence and the terminal consumption boundary.

A fallback admission (`routing.FALLBACK`, no included claim) that may have been sent can still consume an unknown
amount of its pool. While any such attempt is dispatched or uncertain, its pool takes no included reservation and no
included admission of it dispatches. Each supported terminal disposition writes the pool's consumed boundary
(`routing_observations.mark_consumed`) before the hold closes, and an included admission whose snapshot is no newer
than that boundary never dispatches. Exposure comes from the ledger's own open holds and admission records (records
of open holds are never pruned): no second registry, send-time mark or timestamp field. Reads only; no process.
"""
from runtime.usage_budget import Ledger

EXPOSED_STATES = ('dispatched', 'uncertain')
FENCED = 'fallback_exposure_open'


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
    route's provider-managed fallback when the route has one, and is excluded otherwise."""
    item, reasons = found
    if item is None or item.get('fallback') or item['cls'] != 'subscription' or item['pool'] not in active_pools(data):
        return found
    if item['spec'].get('provider_fallback') is not True:
        return None, [FENCED]
    return dict(item, constraints=[], fallback=True, vector={}), reasons


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


def before_record(ledger, event, settings_path, now):
    """`routing.record_event`'s guard: a `complete` that would release a dispatched fallback writes its terminal
    boundary first; a missing store or failed write refuses (`observation_store_unavailable`) and records nothing.
    Replays, conflicts and other transitions reach this with a closed or non-dispatched hold and mark nothing;
    the ledger's own validation still decides the event. Returns None."""
    from runtime.usage_budget import BudgetError
    if event.get('kind') != 'complete':
        return None
    data = ledger.snapshot()
    hold, record = data['holds'].get(event.get('attempt_id')), Ledger.admission(data, event.get('attempt_id'))
    if hold is None or hold['closed'] or hold['state'] != 'dispatched' or not is_fallback(record):
        return None
    try:
        mark_terminal(settings_path, record, now, event['attempt_id'])
    except (OSError, ValueError):
        raise BudgetError('observation_store_unavailable') from None
    return None


def mark_terminal(settings_path, record, at, attempt):
    """Write a fallback's terminal consumption boundary before its hold closes; refuses without the actual store."""
    from runtime import routing_observations
    if settings_path is None:
        raise ValueError('observation_store_required')
    routing_observations.mark_consumed(settings_path, record['pool'], at, attempt)
