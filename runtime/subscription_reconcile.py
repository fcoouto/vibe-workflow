"""S3.6c operator reconciliation of uncertain SUBSCRIPTION (window) holds, and the window debit it can leave.

Contract: clients/opencode.md#uncertain-subscription-holds-s36c. `reconcile_subscription{attempt_id, evidence,
event_id}` is a one-shot operator op for an open `uncertain` hold with window claims only; it never charges dollars.
`host_usage` (the launcher's idle re-read of the derived session) settles like a subscription `complete`; `abandon`
frees the concurrency place and turns the window claims into a debit that admission keeps counting (`held`) until
a `prepare` snapshot shows a fixed window that began after the abandon (`_qualifies`). Time alone never releases.
Only a `prepare` outside `--serve` clears a debit (C3-0a); a debit that never clears (a rolling window) is removed by the
one-shot operator op `drop_debit{key, marker, event_id}` against the marker `status` lists.
"""
import hashlib
import hmac
import json
import re
import secrets

from runtime import usage_budget
from runtime.usage_budget import MAX_INTEGER, WIN, BudgetError, Ledger, integer, require

KIND = 'reconcile_subscription'
EVIDENCE_KINDS = ('host_usage', 'abandon')
HOST_SOURCE = 'host_message_totals'
DEBITS = 'window_debits'
CLEARED = 'window_debit_cleared'
DROP_KIND = 'drop_debit'
DEBIT_MARKER = re.compile(r'dd_[0-9a-f]{32}')
MAX_WINDOW_DEBITS = 32
# A provider's window start is compared with the command's clock, which `prepare` keeps within this drift of the
# collector (routing.MAX_CLOCK_DRIFT): a window must begin at least this long after the abandon mark.
CLOCK_MARGIN = 60
HINT = ('Uncertain subscription holds (one-shot reconcile_subscription; stop the worker first): host_usage settles '
        "from the launcher's re-read of the idle derived session; abandon frees the concurrency place and keeps the "
        'window claim as a debit until telemetry shows a window that began after the abandon.')


def reconcile_event(value):
    """The operator's request as a ledger event: (event, None) or (None, reason). Shape errors are
    `invalid_request`, another evidence kind `evidence_unsupported`, a usage that is not a host re-read
    `evidence_invalid`."""
    from runtime import routing  # routing imports this module; validation reuses its bounded checks
    fields = ('event_id', 'attempt_id', 'evidence')
    if not routing._operator_ids(value, fields) or set(value) != set(fields):
        return None, 'invalid_request'
    evidence = value['evidence']
    if type(evidence) is not dict or not routing._text(evidence.get('kind')):
        return None, 'invalid_request'
    if evidence['kind'] not in EVIDENCE_KINDS:
        return None, 'evidence_unsupported'
    if evidence['kind'] == 'abandon':
        return (dict(value, kind=KIND), None) if set(evidence) == {'kind'} else (None, 'invalid_request')
    if set(evidence) != {'kind', 'session_id', 'usage'} or not routing._text(evidence['session_id']) or \
            routing._usage_error(evidence['usage']) is not None:
        return None, 'invalid_request'
    if evidence['usage']['source'] != HOST_SOURCE:
        return None, 'evidence_invalid'
    return dict(value, kind=KIND), None


def reconcile(ledger, event, now):
    """Apply one `reconcile_event` in one write transaction; refusals are fixed reasons and store nothing."""
    from runtime import routing
    try:
        return _reconcile(ledger, event, routing.event_digest(event), now)
    except BudgetError as error:
        return dict(state='blocked', reason=str(error))


def _reconcile(ledger, event, digest, now):
    usage_budget.integer(now)
    with ledger.transaction() as data:
        usage_budget._create_tables(ledger.db)
        known = ledger._event(data, event['event_id'], now)
        if known is not None:
            require(known[0] == digest, 'event_conflict')
            return dict(json.loads(known[1])['outcome'], duplicate=True)
        attempt, evidence = event['attempt_id'], event['evidence']
        hold = data['holds'].get(attempt)
        require(hold is not None, 'unknown_attempt')
        require(not hold['closed'], 'attempt_closed')
        require(hold['state'] == 'uncertain' and not hold['amount'] and hold.get('organization') is None,
                'hold_state_forbidden')
        require(evidence.get('kind') in EVIDENCE_KINDS, 'evidence_unsupported')
        if evidence['kind'] == 'host_usage':
            outcome, body = _host_usage(ledger, data, attempt, hold, event, now)
        else:
            outcome, body = _abandon(data, attempt, hold, now)
        body.update(kind=KIND, recorded_at=now, outcome=dict(outcome))
        ledger._store_event(data, event['event_id'], digest, body, now, effect=True)
        return outcome


def _host_usage(ledger, data, attempt, hold, event, now):
    """Settle through the S3.6a `complete` path (release, units observed, root aggregate). The uncertain mark is
    lifted only inside this transaction, so the refusal of a client's bare `complete` is unchanged."""
    evidence = event['evidence']
    issued = hold.get('dispatch')
    require(issued is not None, 'dispatch_required')
    require(evidence['session_id'] == issued['session_id'], 'evidence_mismatch')
    hold['state'] = 'dispatched'
    outcome, _, body = ledger._apply_event(data, dict(event_id=event['event_id'], attempt_id=attempt, kind='complete',
                                                      usage=evidence['usage']), now)
    require(hold['closed'] and outcome.get('outcome') == 'complete', 'internal_error')
    outcome['settlement'] = dict(outcome['settlement'], resolution=dict(basis='host_usage'))
    return outcome, body


def _abandon(data, attempt, hold, now):
    claims = dict(hold['windows'])
    debits = data.setdefault(DEBITS, {})
    require(len(set(debits) | set(claims)) <= MAX_WINDOW_DEBITS, 'ledger_capacity_reached')
    for key, units in claims.items():
        entry = debits.setdefault(key, dict(units=0, at=now))
        entry.update(units=min(MAX_INTEGER, entry['units'] + units), at=max(entry['at'], now))
    if not debits:
        del data[DEBITS]
    hold.update(closed=True, state='released')
    root, stage = Ledger._root_of(data, attempt, hold)
    outcome = dict(state='recorded', duplicate=False, outcome='abandoned', settlement=dict(
        kind='window', released={}, debited=claims, observed_units=None, resolution=dict(basis='abandon')))
    return outcome, dict(attempt_id=attempt, stage=stage, root=root, funding='subscription')


def held(data):
    """`Ledger.held` plus the window debits: what admission counts against each window's headroom."""
    total = Ledger.held(data)
    for key, entry in data.get(DEBITS, {}).items():
        total[key] = min(MAX_INTEGER, total.get(key, 0) + entry['units'])
    return total


def _qualifies(window, entry, now):
    """True only for an observation of a FIXED window that is fresh, known, current and began at least
    CLOCK_MARGIN after the abandon mark: the abandoned request's units cannot be in it. Rolling windows, an unknown
    reset or length, and a window that has not begun or has already reset never qualify. Clearing is a release, so
    `rolling` must be exactly False: a missing key, None or 0 never qualifies (admission's own default is unchanged)."""
    numbers = all(type(window.get(field)) in (int, float) for field in ('reset_at', 'length', 'used'))
    if not numbers or window.get('rolling') is not False or window.get('fresh') is not True:
        return False
    start = window['reset_at'] - window['length']
    return window['length'] > 0 and window['reset_at'] > now and entry['at'] + CLOCK_MARGIN <= start <= now


def clear_debits(ledger, data, observations):
    """Remove each debit a validated `prepare` snapshot shows a qualifying window for, once, with an audit row.

    Runs inside the admission transaction before the decision, so the decision sees the cleared table; the
    clearing commits whatever the admission outcome (a qualifying window is evidence on its own)."""
    debits, now = data.get(DEBITS), observations['now']
    if not debits:
        return data
    for key in sorted(debits):
        pool, _, window_id = key[len(WIN):].partition('/')
        windows = (observations['pools'].get(pool) or {}).get('windows') or []
        found = [window for window in windows if window.get('id') == window_id]
        entry = debits[key]
        # The snapshot shape check allows duplicate window ids; an ambiguous window never clears (fail closed).
        if len(found) != 1 or not _qualifies(found[0], entry, now):
            continue
        del debits[key]
        usage_budget._create_tables(ledger.db)
        tag = hashlib.sha256(key.encode()).hexdigest()[:16]
        body = dict(attempt_id='debit:' + tag, kind=CLEARED, recorded_at=now, outcome=dict(
            state='cleared', key=key, units=entry['units'], at=entry['at'],
            window_start=found[0]['reset_at'] - found[0]['length']))
        raw = usage_budget._compact(body)
        # Reserved id: longer than any client `event_id` (MAX_EVENT_ID) and unique per clearing, inserted without
        # replacement, so it can never overwrite a client event or an earlier audit row. Not pruned here: a clearing
        # follows one abandon's event, and the next `_store_event` trims the table back to MAX_EVENTS.
        event_id = '%s:%s:%d:%s' % (CLEARED, hashlib.sha256(key.encode()).hexdigest(), entry['at'],
                                    secrets.token_hex(32))
        require(len(event_id) > usage_budget.MAX_EVENT_ID and len(raw) <= usage_budget.MAX_EVENT_BYTES,
                'record_too_large')
        ledger.db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)', (
            event_id, hashlib.sha256(raw.encode()).hexdigest(), now, body['attempt_id'], 0, raw))
    if not debits:
        del data[DEBITS]
    return data


def debit_marker(data, key):
    """The handshake value `drop_debit` needs: a digest of the ledger identity, the window key and the debit's units
    and mark, shown only by `status`. A debit that grew or moved after the operator reviewed it makes it stale."""
    entry = data[DEBITS][key]
    raw = json.dumps([data['identity'], key, entry['units'], entry['at']], separators=(',', ':'), ensure_ascii=True)
    return 'dd_' + hashlib.sha256(raw.encode()).hexdigest()[:32]


def drop_event(value):
    """The operator's `drop_debit{event_id, key, marker}` as a ledger event: (event, None) or (None, 'invalid_request')."""
    from runtime import routing
    fields = ('event_id', 'key', 'marker')
    if type(value) is not dict or set(value) != set(fields) or \
            not routing._bounded_id(value['event_id'], usage_budget.MAX_EVENT_ID):
        return None, 'invalid_request'
    key, marker = value['key'], value['marker']
    if type(key) is not str or not key.startswith(WIN) or len(key) > usage_budget.MAX_KEY or not key.isprintable() \
            or type(marker) is not str or DEBIT_MARKER.fullmatch(marker) is None:
        return None, 'invalid_request'
    return dict(value, kind=DROP_KIND), None


def drop(ledger, event, now):
    """Remove one window debit in one write transaction, idempotent by event id: the key must still be debited
    (`debit_not_pending`) and the marker current (`marker_stale`). It never charges or releases dollars or holds.
    The audit is the event row: the key and its units only."""
    from runtime import routing
    try:
        integer(now)
        digest = routing.event_digest(event)
        with ledger.transaction() as data:
            usage_budget._create_tables(ledger.db)
            known = ledger._event(data, event['event_id'], now)
            if known is not None:
                require(known[0] == digest, 'event_conflict')
                return dict(json.loads(known[1])['outcome'], duplicate=True)
            key = event['key']
            require(key in data.get(DEBITS, {}), 'debit_not_pending')
            require(hmac.compare_digest(event['marker'].encode(), debit_marker(data, key).encode()), 'marker_stale')
            entry = data[DEBITS].pop(key)
            if not data[DEBITS]:
                del data[DEBITS]
            outcome = dict(state='recorded', duplicate=False, outcome='debit_dropped', key=key, units=entry['units'])
            body = dict(attempt_id='debit:' + hashlib.sha256(key.encode()).hexdigest()[:16], kind=DROP_KIND,
                        recorded_at=now, outcome=dict(outcome))
            ledger._store_event(data, event['event_id'], digest, body, now, effect=True)
            return outcome
    except BudgetError as error:
        return dict(state='blocked', reason=str(error))


def _counted(items, field='ids'):
    items = sorted(items)
    limit = usage_budget.SUMMARY_IDS
    return {'count': len(items), field: items[:limit], 'more': max(0, len(items) - limit)}


def summary(data, listed, hint):
    """Add the uncertain subscription holds and the window debits, each with its `drop_debit` marker, to the
    operator's reconcile listing. The hint gains HINT while such a hold is open; a debit alone needs no operator action
    (telemetry clears a fixed window's debit; `drop_debit` is for one that never clears)."""
    uncertain = [attempt for attempt, hold in Ledger.open_holds(data).items()
                 if hold['state'] == 'uncertain' and not hold.get('amount') and hold.get('organization') is None]
    debits = data.get(DEBITS, {})
    shown = [dict(key=key, units=debits[key]['units'], at=debits[key]['at'], marker=debit_marker(data, key))
             for key in sorted(debits)[:usage_budget.SUMMARY_IDS]]
    listed = dict(listed, reconcile_subscription=_counted(uncertain), window_debits=_counted(debits, 'keys'),
                  drop_debit=dict(debits=shown, more=max(0, len(debits) - len(shown))))
    if uncertain:
        hint = HINT if hint is None else hint + ' ' + HINT
    return listed, hint
