"""API money (F5.A2): one whole-task envelope per S1 run, unpriced completion, and typed balance coverage.

`Ledger` exposes `reserve_task`, `reserve_call`, `complete_unknown` and `apply_coverage` from here. The envelope
holds V = ceil(estimate x 1.2) before the first paid send, split between the count call and a generation
placeholder (`reserved`, `cost: null`, `digest: null`). The generation grant assigns only the packet digest and keeps
the buffered maximum until a known actual settles it or an unsent cancellation releases it. Unknown billing keeps
`cost: null`, which `Ledger._cost` counts at the full maximum. Coverage marks a settled charge that a trusted
in-process observation already reflects, so provider headroom stops subtracting it twice; `_total` and every
task, cycle, pilot and live limit still count it. No production producer of `Coverage` is qualified.
"""
API_MONEY_BUFFER_BP = 2000  # the user's 20% API-money buffer; separate from subscription window `margin_bp`


def _budget():
    from runtime import usage_budget  # imported at call time: usage_budget imports this module
    return usage_budget


def buffered(amount):
    """ceil(amount x 1.2) in micro-units, with `Ledger._reserve`'s upward integer arithmetic."""
    return _budget().Ledger._reserve(amount, None, API_MONEY_BUFFER_BP)


def active_call(run):
    """The call that may be topped up or transferred from. Legacy runs: the last reserved or in-flight call. An
    envelope: the count until its work completes, then only a granted generation; never the placeholder."""
    calls = run['calls']
    if not run.get('envelope'):
        active = [call for call in calls.values() if call['state'] in ('reserved', 'dispatching')]
        return active[-1] if active else None
    if calls['count']['state'] in ('reserved', 'dispatching'):
        return calls['count']
    generation = calls.get('generation')
    if calls['count']['state'] == 'completed' and generation is not None and generation['digest'] is not None \
            and generation['state'] in ('reserved', 'dispatching'):
        return generation
    return None


def reserve_task(ledger, organization, task, run_id, digest, estimate, count_maximum, revision, now, native, *,
                 consent=None, live_gate=None):
    budget = _budget()
    for value in (organization, task, run_id, digest): budget.name(value)
    budget.integer(estimate); budget.integer(count_maximum)
    budget.require(count_maximum <= estimate, 'estimate_below_count_bound')  # the whole estimate includes the count
    envelope = buffered(estimate)
    with ledger.transaction(grows=True) as data:
        existing = data['runs'].get(run_id)
        budget.require(existing is None or existing.get('envelope') is True, 'run_identity_changed')
        run = ledger._begin(data, organization, task, run_id, digest, envelope, revision, now, native, 0, None,
                            consent, live_gate)
        if existing is None:  # `_begin` reserved V on the count call: partition it, the total held stays V
            count = run['calls']['count']
            count['maximum'] = count_maximum
            placeholder = ledger._call(data, run, envelope - count_maximum, None)
            run['calls']['generation'] = placeholder
            run['envelope'] = True
        return run


def reserve_call(ledger, run_id, phase, digest, request_bound, revision, now, *, live_gate=None):
    budget = _budget()
    budget.name(digest); budget.integer(request_bound)
    budget.require(phase == 'generation', 'invalid_phase')
    with ledger.transaction(grows=True) as data:
        run = data['runs'][run_id]
        budget.require(run.get('envelope') is True and not run['closed'], 'not_envelope')
        count, call = run['calls']['count'], run['calls']['generation']
        budget.require(count['state'] == 'completed', 'count_not_completed')
        if call['state'] == 'reserved' and call['digest'] == digest:
            return  # the same grant again: no second reservation or permit
        budget.require(call['state'] == 'reserved' and call['digest'] is None, 'attempt_exists')
        budget.require(run['revision'] == revision, 'policy_revision_changed')
        budget.require(buffered(request_bound) <= call['maximum'], 'envelope_exceeded')
        ledger._live_admit(data, run['organization'], 0, now, live_gate, run)  # authority rechecked, no money added
        ledger._admit(data, run['organization'], run['task'], 0, revision, now)
        call['digest'] = digest  # the buffered maximum stays held


def complete_unknown(ledger, run_id, phase):
    """Known work completion with unknown billing: `cost` stays null and counts at its full maximum until the
    operator settles it (`ledger_versions.settle_legacy_call`). Never records a bound or an estimate as actual."""
    budget = _budget()
    with ledger.transaction() as data:
        call = data['runs'][run_id]['calls'][phase]
        budget.require(call['state'] in ('dispatching', 'unresolved'), 'completion_forbidden')
        budget.require('over_bound' not in call, 'over_bound_unresolved')
        call['state'] = 'completed'
        call.pop('provision', None)
        if phase == 'generation':
            ledger._close_run(data, run_id)



def reconcile(ledger, *, scope=None):
    """Report scoped recovery evidence without altering reservations. `pending`/`pending_attempts` list unresolved
    (possibly sent) calls as before; `unpriced_attempts` lists completed work whose cost is still unknown, closed runs
    included: each keeps its full reservation until `settle_legacy_call` prices it (F5.A2)."""
    result = {'state': 'manual_recovery_required'}
    if scope is None:
        return result
    runs = [(run_id, run) for run_id, run in sorted(ledger.snapshot()['runs'].items())
            if (run.get('consent') or {}).get('scope') == scope]
    pending = [dict(attempt_id=run_id + ':' + phase, reserved_usd_micros=call['maximum'],
                    incomplete=call.get('incomplete'))
               for run_id, run in runs for phase, call in run['calls'].items() if call['state'] == 'unresolved']
    if pending:
        result['pending'] = pending[0]
        result['pending_attempts'] = pending
    unpriced = [dict(attempt_id=run_id + ':' + phase, reserved_usd_micros=call['maximum'], cost='unknown',
                     run_closed=run['closed'])
                for run_id, run in runs for phase, call in run['calls'].items()
                if call['state'] == 'completed' and call['cost'] is None]
    if unpriced:
        result['unpriced_attempts'] = unpriced
    return result


def covered(data, organization):
    """Settled charges of `organization` that a trusted observation already reflects in its balance. A subtotal
    that does not fit inside its retained, unsaturated settled total counts as nothing (conservative)."""
    budget = _budget()
    runs = sum(call['cost'] for run in data['runs'].values() if run['organization'] == organization
               for call in run['calls'].values()
               if call.get('covered') and call['state'] == 'completed' and type(call['cost']) is int)
    settled = (data['organizations'].get(organization) or {}).get('settled') or {}
    total, holds = settled.get('total', 0), settled.get('covered', 0)
    if not (type(holds) is int and 0 <= holds <= total < budget.MAX_INTEGER):
        holds = 0
    return runs + holds


def apply_coverage(ledger, coverage, now):
    with ledger.transaction() as data:
        return stage_coverage(ledger, data, coverage, now)


def stage_coverage(ledger, data, coverage, now):
    """Same-transaction form of `apply_coverage`, for a qualified observer that stages its balance in `data` first.
    Every check precedes the first mark; a refusal raises and the caller's transaction writes nothing."""
    from dataclasses import asdict
    from runtime import usage_admission
    budget = _budget(); require = budget.require
    require(type(coverage) is usage_admission.Coverage and type(coverage.grant) is usage_admission.Grant,
            'trusted_coverage_required')
    budget.integer(now)
    config = (data.get('routing') or {}).get(coverage.scope)
    require(config is not None and config['revision'] == coverage.config_revision, 'coverage_config_changed')
    require(usage_admission.digest(config['binding']) == coverage.binding_digest, 'coverage_binding_changed')
    grant, binding = coverage.grant, config['binding']
    require((grant.organization, grant.project) == (binding['organization'], binding['project']),
            'coverage_identity_changed')
    org = data['organizations'][grant.organization]
    require(org.get('live_funding') == dict(observed_at=grant.observed_at, digest=usage_admission.digest(asdict(grant))),
            'coverage_observation_changed')
    require(0 <= now - grant.observed_at <= budget.FRESH_SECONDS, 'observation_stale')
    charges = coverage.charges
    require(type(charges) is tuple and 0 < len(charges) <= 2 * budget.MAX_RUNS + budget.MAX_HOLDS
            and all(type(c) is usage_admission.ChargeRef for c in charges)
            and len({(c.kind, c.id, c.phase) for c in charges}) == len(charges), 'invalid_coverage')
    settled = org.get('settled') or dict(total=0)
    marks, held = [], 0
    for charge in charges:
        require(type(charge.amount) is int and 0 < charge.amount <= budget.MAX_INTEGER, 'invalid_coverage')
        if charge.kind == 'run':
            run = data['runs'].get(charge.id)
            call = None if run is None or run['organization'] != grant.organization else run['calls'].get(charge.phase)
            require(call is not None and call['state'] == 'completed' and call['cost'] == charge.amount
                    and type(call['cost']) is int and 'over_bound' not in call, 'coverage_charge_mismatch')
            owner = call
        else:
            require(charge.kind == 'hold' and charge.phase is None, 'invalid_coverage')
            hold = data['holds'].get(charge.id)
            settlement = None if hold is None else hold.get('settlement')
            require(hold is not None and hold['closed'] and hold.get('organization') == grant.organization
                    and settlement is not None and settlement.get('charged') == charge.amount
                    and settlement.get('overrun_state') != 'unconfirmed', 'coverage_charge_mismatch')
            owner = settlement
            if not owner.get('covered'):
                held += charge.amount
        if not owner.get('covered'):
            marks.append(owner)
    total, already = settled.get('total', 0), settled.get('covered', 0)
    require(not held or (total < budget.MAX_INTEGER and type(already) is int and already + held <= total),
            'coverage_ambiguous')  # a saturated or inconsistent total is no evidence that a charge was reflected
    for owner in marks:
        owner['covered'] = True
    if held:
        org['settled']['covered'] = already + held
    return dict(state='recorded', marked=len(marks), duplicate=len(charges) - len(marks))
