"""The S1 run phase lifecycle: generation reservation, dispatch, completion, uncertainty, close and unsent rejection.

Moved unchanged out of `usage_budget.Ledger` (F5.A2 foundation). Each function takes the `Ledger` and keeps its
method's exact checks, their order, its transaction and its exceptions; the `Ledger` methods of the same names are thin
calls into this module. Admission, conversion, top-up and transfer (`begin`, `_begin`, `convert_hold`, `top_up`) and
`_close_run` stay in `Ledger`.
"""
import json

from runtime import usage_budget as budget  # attribute access only at call time: usage_budget imports this module


def reserve_generation(ledger, run_id, digest, maximum, revision, now, *, live_gate=None):
    budget.name(digest)
    with ledger.transaction(grows=True) as data:
        run = data['runs'][run_id]
        budget.require(not run['closed'] and run['calls']['count']['state'] == 'completed', 'count_not_completed')
        budget.require('generation' not in run['calls'], 'attempt_exists')
        budget.require(run['revision'] == revision, 'policy_revision_changed')
        ledger._live_admit(data, run['organization'], maximum, now, live_gate, run)
        ledger._admit(data, run['organization'], run['task'], maximum, revision, now)
        run['calls']['generation'] = ledger._call(data, run, maximum, digest)


def dispatch(ledger, run_id, phase, revision, now, *, live_gate=None):
    with ledger.transaction() as data:
        run = data['runs'][run_id]; call = run['calls'][phase]
        if run.get('consent') is not None:
            ledger._consent(data, run['consent'])
        budget.require(not run['closed'] and call['state'] == 'reserved', 'dispatch_forbidden')
        # F5.A2: an ungranted placeholder never sends; an envelope's generation follows only completed count work.
        budget.require(call['digest'] is not None and (phase != 'generation' or not run.get('envelope')
                       or run['calls']['count']['state'] == 'completed'), 'call_not_granted')
        budget.require(run['revision'] == revision, 'policy_revision_changed')
        # The call's own reservation has already reduced headroom.
        ledger._live_admit(data, run['organization'], 0, now, live_gate, run)
        ledger._admit(data, run['organization'], run['task'], 0, revision, now)
        budget.require(ledger._headroom(data, run['organization'], run['task']) >= 0, 'insufficient_headroom')
        policy = data['organizations'][run['organization']]['policy']
        budget.require((policy['pilot_limit'] is None or ledger._total(data, run['organization']) <= policy['pilot_limit'])
                       and (policy['task_limit'] is None
                            or ledger._total(data, run['organization'], run['task']) <= policy['task_limit']),
                       'limits_changed')
        cap = policy['amount'] if policy['ceiling'] == 'custom' else policy['grant_amount']
        budget.require(policy['ceiling'] == 'none' or ledger._total(data, run['organization'], cycle=policy['cycle']) <= cap,
                       'limits_changed')
        budget.require(ledger._credit_raw(data, run['organization'], now) >= 0, 'credit_changed')
        call['state'] = 'dispatching'
        data['native'][run['native']]['latest_at'] = now


def complete(ledger, run_id, phase, actual):
    budget.integer(actual)
    overrun = False
    with ledger.transaction() as data:
        run = data['runs'][run_id]; call = run['calls'][phase]
        budget.require(call['state'] in ('dispatching', 'unresolved'), 'completion_forbidden')
        # Over-bound is sticky: a later (lower) report never settles the call or unblocks its organization.
        budget.require('over_bound' not in call, 'over_bound_unresolved')
        if actual > call['maximum']:
            call.update(state='unresolved', over_bound=actual); overrun = True  # blocks via block_reasons
        else:
            call.update(cost=actual, state='completed')  # Charges actual; unused padding is released.
            call.pop('provision', None)
            if phase == 'generation':
                ledger._close_run(data, run_id)
    budget.require(not overrun, 'cost_exceeds_bound')


def uncertain(ledger, run_id, phase, *, incomplete=None):
    with ledger.transaction() as data:
        call = data['runs'][run_id]['calls'][phase]
        budget.require(call['state'] == 'dispatching', 'uncertainty_forbidden')
        call['state'] = 'unresolved'
        if incomplete is not None:
            try:
                size = len(json.dumps(incomplete, allow_nan=False, separators=(',', ':')))
            except (TypeError, ValueError, RecursionError):
                size = budget.MAX_INCOMPLETE_BYTES + 1
            call['incomplete'] = incomplete if size <= budget.MAX_INCOMPLETE_BYTES else dict(omitted='too_large')


def close_run(ledger, run_id):
    with ledger.transaction() as data:
        run = data['runs'][run_id]
        budget.require(not any(c['state'] in ('dispatching', 'unresolved') for c in run['calls'].values()),
                       'request_may_be_running')
        for call in run['calls'].values():
            if call['state'] == 'reserved': call.update(cost=0, state='rejected-before-send')
        ledger._close_run(data, run_id)


def reject_unsent(ledger, run_id, phase):
    with ledger.transaction() as data:
        run = data['runs'][run_id]; call = run['calls'][phase]
        budget.require(call['state'] == 'dispatching', 'rejection_forbidden')
        call.update(cost=0, state='rejected-before-send')
        for other in run['calls'].values():  # F5.A2: a never-granted or unsent sibling is cancelled with it
            if other['state'] == 'reserved': other.update(cost=0, state='rejected-before-send')
        ledger._close_run(data, run_id)
