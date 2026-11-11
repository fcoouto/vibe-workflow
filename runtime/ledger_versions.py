"""Ledger state versions, and the operator settlement of legacy (S1) run calls before C3-7 retires their writer.

Contract: docs/superpowers/specs/2026-11-10-smart-routing-c3-wiring-design.md sections 4.4 and 4.6 (slice C3-0a).
`usage_budget.Ledger.transaction` calls `upgrade` on every read. `settle_legacy_call{run_id, phase, evidence,
event_id}` is a one-shot operator op (`scripts/routing.py` refuses it in `--serve` and in-process services), idempotent
by event id in the S3.6a event table like the other operator ops; `outstanding` is `status.ledger.legacy_outstanding`.
"""
import json

from runtime import usage_budget as budget  # attribute access only at call time: usage_budget imports this module

STATE_VERSION = 5  # 5 (C3-0a): S3.6c window debits; code that would ignore them must refuse the ledger
KIND = 'settle_legacy_call'
PHASES = ('count', 'generation')
OPEN = ('reserved', 'dispatching', 'unresolved')
EVIDENCE_KINDS = ('never_dispatched', 'receipts', 'charge_reservation')


def upgrade(data, identity):
    """Refuse a record this code cannot read (another ledger, or a version it does not know, such as a newer one:
    `ledger_identity_changed`), then upgrade it in place to STATE_VERSION. The upgrade persists with the next write."""
    version = data.get('version')
    budget.require(type(version) is int and 1 <= version <= STATE_VERSION and data['identity'] == identity,
                   'ledger_identity_changed')
    migrate(data)


def migrate(data):
    """Upgrade older state in place, version by version.

    1 -> 2 drops the global slot and the 20% floor: open runs keep their exposure through their own
    calls, and the old floor seeds the issued allowance (a floor was one fifth of the first balance).
    2 -> 3 adds the multi-pool `holds` and the bounded `admissions` records; both start empty, so
    every in-flight run claim is preserved untouched. Version 3 never shipped with its first hold shape, so
    written v3 ledgers are repaired in place: missing tables are added and a `vector` hold becomes a
    window-only hold (it named no organization, so it carries no dollar amount).
    3 -> 4 (S3.6a) changes no claim, hold or admission: it marks the ledger as one whose R may include settled
    charges (`organizations.*.settled`, `settled_tasks`), so code that would ignore them refuses it
    (`ledger_identity_changed`) instead of resetting caps and credit. A stored `blocked: true` becomes
    `legacy_block` (the block is derived from now on); every unresolved S1 call of such an organization is
    marked `over_bound` at its maximum (v3 recorded no marker, so an over-bound call cannot be told from an
    uncertain one: fail closed); every hold gets its accounting root. The event and root tables are created
    idempotently on the first accounting write.
    4 -> 5 (C3-0a) changes no run, hold, handoff or debit: it marks the ledger as one that may carry S3.6c
    `window_debits`, so code from before them refuses it instead of dropping their protection.
    """
    Ledger = budget.Ledger
    if data['version'] == 1:
        data.pop('slot', None)
        for org in data['organizations'].values():
            floor = org.pop('floor', None)
            if org.get('issued') is None:
                org['issued'] = floor * 5 if floor is not None else None
        data['version'] = 2
    if data['version'] == 2:
        data['version'] = 3
    upgraded = data['version'] == 3
    if upgraded:
        data['version'] = 4
    if data['version'] == 4:
        data['version'] = 5
    data.setdefault('holds', {})
    data.setdefault('admissions', [])
    data.setdefault('seen_attempts', [])
    for hold in data['holds'].values():
        hold.setdefault('state', 'admitted')
        if 'windows' not in hold:
            vector = hold.pop('vector', {})
            hold.update(organization=None, amount=0, cycle=None,
                        windows={budget.WIN + k: v for k, v in vector.items()})
    if upgraded:  # 3 -> 4, once per migrated ledger (the upgrade is persisted by its first write)
        for organization, org in data['organizations'].items():
            if not org.get('blocked'):
                continue
            # v3's run path left an over-bound call `unresolved` with no marker; it cannot be told apart from an
            # uncertain one, so every unresolved call of a blocked organization is treated as over-bound at its
            # maximum: a later lower report can never settle it (case C) and the books keep the maximum.
            for run in data['runs'].values():
                if run['organization'] == organization:
                    for call in run['calls'].values():
                        if call['state'] == 'unresolved':
                            call.setdefault('over_bound', call['maximum'])
        # Every hold admitted before v4 gets its accounting root, while its admission record still exists.
        for attempt, hold in data['holds'].items():
            if 'root' not in hold and 'task' in hold:
                hold.update(zip(('root', 'stage'), Ledger._record_root(Ledger.admission(data, attempt) or {},
                                                                       hold['task'])))
    for org in data['organizations'].values():
        # v4 derives the admission block (`block_reasons`) and stores no flag. A stored `blocked: true` (written
        # by v3, or by S1's run path before v4) has no recorded source, so it becomes the sticky `legacy_block`.
        if org.pop('blocked', False):
            org['legacy_block'] = True
        org.pop('overrun_block', None)


# -- Legacy (S1) settlement (design 4.4) ------------------------------------------------------------------------
def _legacy(run):
    """A run begun by S1 `Ledger.begin`. A run converted from a routing hold (`convert_hold`) carries the hold's
    provider `account`; it is not legacy work and is never listed or settled here."""
    return 'account' not in run


def _unpriced(call):
    """F5.A2: completed work whose billing is unknown (`cost: null`); it holds its full maximum until settled."""
    return call['state'] == 'completed' and call['cost'] is None


def outstanding(data):
    """`status.ledger.legacy_outstanding`: every open legacy run call (reserved, in flight, unresolved or over-bound),
    every completed call whose cost is still unknown (`unpriced`, even on a closed run) and every `legacy_block`
    organization, which must all be settled before C3-7; `ready` handoffs are counted for
    information only (they hold no exposure). Ids, states and integer amounts only."""
    items = [dict(run_id=run_id, phase=phase, state=call['state'], maximum=call['maximum'],
                  over_bound='over_bound' in call, **({'unpriced': True} if _unpriced(call) else {}))
             for run_id, run in sorted(data['runs'].items()) if _legacy(run)
             for phase, call in sorted(run['calls'].items(), key=lambda item: PHASES.index(item[0]))
             if call['state'] in OPEN or 'over_bound' in call or _unpriced(call)]
    blocks = sorted(org_id for org_id, org in data['organizations'].items() if org.get('legacy_block'))
    ready = sum(1 for record in (data.get('handoffs') or {}).values()
                if isinstance(record, dict) and record.get('state') == 'ready')
    limit = budget.SUMMARY_IDS
    return dict(empty=not items and not blocks,
                calls=dict(count=len(items), items=items[:limit], more=max(0, len(items) - limit)),
                legacy_blocks=dict(count=len(blocks), ids=blocks[:limit], more=max(0, len(blocks) - limit)),
                ready_handoffs=ready)


def settle_event(value):
    """The operator's request as a ledger event: (event, None) or (None, reason). `evidence` is
    `{kind: never_dispatched}`, `{kind: receipts, receipts: [1 to 32 distinct ids], cost_usd_micros}` or
    `{kind: charge_reservation}`. Another kind is `evidence_unsupported`, a zero receipts cost `evidence_invalid`
    and any other shape `invalid_request`."""
    from runtime import routing  # routing imports usage_budget; validation reuses its bounded checks
    fields = ('event_id', 'run_id', 'phase', 'evidence')
    if type(value) is not dict or set(value) != set(fields) or \
            not routing._bounded_id(value['event_id'], budget.MAX_EVENT_ID) or not routing._text(value['run_id']) \
            or type(value['phase']) is not str or value['phase'] not in PHASES:
        return None, 'invalid_request'
    evidence = value['evidence']
    if type(evidence) is not dict or not routing._text(evidence.get('kind')):
        return None, 'invalid_request'
    if evidence['kind'] not in EVIDENCE_KINDS:
        return None, 'evidence_unsupported'
    if evidence['kind'] != 'receipts':
        return (dict(value, kind=KIND), None) if set(evidence) == {'kind'} else (None, 'invalid_request')
    items, cost = evidence.get('receipts'), evidence.get('cost_usd_micros')
    if not (set(evidence) == {'kind', 'receipts', 'cost_usd_micros'} and routing._amount(cost)
            and type(items) is list and 0 < len(items) <= routing.MAX_USAGE_REQUESTS
            and all(routing._bounded_id(item, routing.MAX_RECEIPT) for item in items) and len(set(items)) == len(items)):
        return None, 'invalid_request'
    return (dict(value, kind=KIND), None) if cost >= 1 else (None, 'evidence_invalid')


def settle(ledger, event, now):
    """Apply one `settle_event` in one write transaction; refusals are fixed reasons and store nothing."""
    from runtime import routing
    try:
        return _settle(ledger, event, routing.event_digest(event), now)
    except budget.BudgetError as error:
        return dict(state='blocked', reason=str(error))


def _settle(ledger, event, digest, now):
    """Design 4.4. A `reserved` call proves nothing was sent (S1 `dispatch` writes `dispatching` in its transaction
    before any send), so only it is released, at cost 0, and its run closes. A `dispatching` or `unresolved` call
    is charged as the receipts report (above its maximum it becomes over-bound, as `Ledger.complete` does, and
    `resolve_block` settles it) or charged its full maximum; its run closes, so it never continues. An over-bound
    call is `block_not_pending` (use `resolve_block`). The audit is the event row: ids, labels and integer totals,
    and a receipt count and digest, never the receipt ids. Once the event table prunes that row, a replay is no
    longer a duplicate: it finds the call settled (`call_closed`, or `block_not_pending` if left over-bound)."""
    require, Ledger = budget.require, budget.Ledger
    budget.integer(now)
    with ledger.transaction() as data:
        budget._create_tables(ledger.db)
        known = ledger._event(data, event['event_id'], now)
        if known is not None:
            require(known[0] == digest, 'event_conflict')
            return dict(json.loads(known[1])['outcome'], duplicate=True)
        run_id, phase, evidence = event['run_id'], event['phase'], event['evidence']
        run = data['runs'].get(run_id)
        call = None if run is None else run['calls'].get(phase)
        require(call is not None, 'unknown_call')
        require(_legacy(run), 'not_legacy_call')
        require('over_bound' not in call, 'block_not_pending')
        unpriced = _unpriced(call)  # F5.A2: completed work, unknown cost: receipts or charge_reservation only
        require(call['state'] in OPEN or unpriced, 'call_closed')
        kind, maximum = evidence['kind'], call['maximum']
        require((kind == 'never_dispatched') == (call['state'] == 'reserved'), 'call_state_forbidden')
        over = False
        if kind == 'never_dispatched':
            call.update(cost=0, state='rejected-before-send')
            charged, released, resolution = 0, maximum, dict(basis='never_dispatched')
        elif kind == 'receipts':
            cost = evidence['cost_usd_micros']
            require(cost >= 1, 'evidence_invalid')
            resolution = Ledger._resolution(evidence['receipts'])
            over = cost > maximum
            if over:  # as `Ledger.complete`: sticky, blocks the organization until `resolve_block`
                call.update(state='unresolved', over_bound=cost)
                charged, released = 0, 0
            else:
                call.update(cost=cost, state='completed')
                charged, released = cost, maximum - cost
        else:
            call.update(cost=maximum, state='completed')
            charged, released, resolution = maximum, 0, dict(basis='charge_reservation')
        if not over:
            call.pop('provision', None)
        if not over and not unpriced:  # terminal recovery: cancel only provably unsent siblings, then close
            for other in run['calls'].values():
                if other['state'] == 'reserved': other.update(cost=0, state='rejected-before-send')
            Ledger._close_run(data, run_id)
        # Pricing completed work is a partial cost reconciliation: another phase and the run's lifetime are untouched.
        outcome = dict(state='recorded', duplicate=False, outcome='over_bound' if over else
                       'released' if kind == 'never_dispatched' else 'charged', run_id=run_id, phase=phase,
                       charged=charged, released=released, resolution=resolution,
                       remaining_reasons=sorted(Ledger.block_reasons(data, run['organization'])))
        body = dict(attempt_id=run_id, kind=KIND, recorded_at=now, outcome=dict(outcome))
        ledger._store_event(data, event['event_id'], digest, body, now, effect=True)
        return outcome
