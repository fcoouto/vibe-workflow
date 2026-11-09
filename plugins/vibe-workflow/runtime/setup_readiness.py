"""Point-in-time review and readiness evidence from retained D5/D6 owners."""

from dataclasses import dataclass
import hashlib
import time

from runtime import delegation, evidence as d5, startup, state

_ISSUER = object()
_require = d5._require


@dataclass(frozen=True)
class _ReadinessEvidence:
    request: object
    facts: object
    issuer: object


def _current(scope, request):
    d5._request(request, ('check_attempt_id', 'review_attempt_id'))
    state._text(request['check_attempt_id'], 'check attempt id', 128)
    state._text(request['review_attempt_id'], 'review attempt id', 128)
    _require(isinstance(scope, startup.AcceptedScope) and scope.qualification is not None,
        'Review-readiness requires an accepted qualification scope')
    with state._database(scope) as db:
        task = d5._task(db, scope, request)
        admission = state._capability(scope, task, 'verify-completion')
        _require(admission.mode == 'qualification' and task['state'] != 'DONE',
            'Review-readiness needs a qualification-only verification')
        references = task.get('evidence', {})
        _require('verification' in references, 'Original D5 verification is unavailable')
        read = lambda ref: d5._read(scope, ref, db=db)
        plan = read(references['plan'])
        candidate = d5.capture_candidate(scope, plan)
        digest = d5._digest(candidate)
        verification = read(references['verification'])
        scope_id = state._scope_id(scope)
        generation = db.execute('select generation from checkouts where scope_id=?', (scope_id,)).fetchone()[0]
        _, closure, routing = d5._verification_resources(scope, task, plan)
        _require(verification['purpose'] == 'completion' and verification['blockers'] == []
            and verification['candidate_digest'] == digest and verification['plan_digest'] == references['plan']['digest']
            and verification['generation'] == generation and verification['resource_closure'] == closure,
            'D5 qualification verification is stale or blocked')

        records = delegation._ledger(scope, db, task=task)
        delegation._resources_available(scope, db, closure, records)
        delegation._qualification_allocation(scope, task, admission, records)
        barrier = next((row for row in records if row['id'] == verification['barrier_id']), None)
        _require(barrier is not None and barrier['data']['role'] == 'coordinator'
            and all(barrier['owner'][key] == task[key] for key in ('client', 'session_id', 'task_id'))
            and barrier['owner']['scope_id'] == scope_id and barrier['body']['d5_plan'] == references['plan']
            and barrier['body']['request']['operation']['kind'] == 'completion-verification'
            and barrier['body']['request']['verification'] == dict(purpose='completion', **routing)
            and barrier['data']['claims'] == closure and barrier['data']['status'] == 'settled'
            and barrier['data']['result'] == {'outcome': 'completed'},
            'Exact settled D6 verification barrier is unavailable')
        _require(read(barrier['data']['reconciliation']) == dict(version=1, kind='synchronous-verification',
            barrier_id=barrier['id'], verification=references['verification'], revision=task['revision']),
            'D6 verifier reconciliation changed')

        check = next((item for item in references['attempts'] if item['id'] == request['check_attempt_id']), None)
        _require(check is not None and check['status'] == 'passed'
            and check['candidate_digest'] == digest and check['plan_digest'] == references['plan']['digest']
            and check.get('execution') and check.get('reconciliation'),
            'Exact reconciled current-candidate check is unavailable')
        check_record = delegation._linked_check(scope, scope_id, task, check, records, db=db)
        _require(check_record is not None and check_record['data']['status'] == 'settled'
            and check_record['data']['result'] == {'outcome': 'completed'}
            and check_record['data']['reconciliation'] == check['reconciliation']
            and read(check['execution'])['result']['exit_code'] == 0,
            'Exact check execution or its settlement is unavailable')

        review = next((item for item in references['attempts'] if item['id'] == request['review_attempt_id']), None)
        _require(review is not None and review['kind'] == 'review' and review['status'] == 'assessed'
            and review['candidate_digest'] == digest and review['plan_digest'] == references['plan']['digest'],
            'Exact assessed current-candidate review is unavailable')
        record = delegation._linked_review(scope, scope_id, task, review, records, db=db)
        _require(record is not None and record['data']['status'] == 'settled'
            and record['data']['result'] == {'outcome': 'completed'}
            and record['data']['reconciliation'] == review.get('ownership_reconciliation')
            and d5._review_quality_matches(scope, review, record, db=db),
            'Exact reviewed native assignment or its settlement is unavailable')
        latest = delegation._worker_history(scope, record['data']['observation'], db=db)[0][1]['receipt']
        native = {key: latest[key] for key in ('parent_session_id', 'parent_turn_id', 'spawn_item_id',
            'worker_id', 'worker_turn_id', 'result_item_id', 'result_digest', 'turn_status',
            'assignment_digest', 'candidate_digest')}
        reconciliation = read(record['data']['reconciliation'])
        _require(reconciliation['assessment']['outcome'] == 'completed'
            and review['id'] == reconciliation['proof']['attempt_id']
            and 0 < reconciliation['proof']['expected_revision'] == reconciliation['revision'] - 1 < task['revision'],
            'Native review stop and effect assessment is unavailable')
        delegation._validate_role_proof(record, task, reconciliation['proof'], scope_id=scope_id,
            revision=reconciliation['revision'] - 1, read=read, linked_review=lambda attempt: record,
            current_candidate_digest=digest)
        _require(not d5._blockers(scope, task, plan, candidate, 'completion',
            verification['reuse'], verification['security_impact'], resource_closure=closure,
            barrier_id=barrier['id'])
            and not d5._checkout_attempt_blockers(db, scope, task, records=records)
            and not d5._ledger_blockers(scope, db, closure, barrier['id'], records=records),
            'Current qualification review evidence or writer state is blocked')
        _require(admission.limits['deadline'] > time.time(), 'Original qualification deadline has elapsed')
        observations = []
        for reference in (references['verification'], barrier['data']['reconciliation'],
                check['execution'], check['reconciliation'], review['collected'], review['assessment'],
                record['data']['reconciliation']):
            managed = d5._managed_metadata(scope, reference, db=db)['managed']
            observations.append(dict(reference=managed['path'], sha256=managed['sha256']))
        return dict(accepted_scope_digest=scope.accepted_scope_digest,
            attempt_ref=startup._canonical_plain(admission.attempt_ref),
            setup_reference=startup._canonical_plain(admission.qualification_ref),
            check_attempt_id=check['id'], review_attempt_id=review['id'], native=native, observations=observations)


def _observe_current(scope, request):
    from runtime.adapters import codex

    before = _current(scope, request)
    native = before['native']
    observed = codex.observe_native_review_assignment(scope, before['review_attempt_id'],
        native['parent_turn_id'], native['spawn_item_id'])
    receipt, result = observed['receipt'], observed['result']
    _require(all(receipt.get(key) == value for key, value in native.items())
        and isinstance(result, str)
        and hashlib.sha256(result.encode()).hexdigest() == native['result_digest'],
        'The latest native review no longer matches its retained assessment')
    _require(_current(scope, request) == before, 'Review-readiness owner evidence changed during native observation')
    return before


def observe(scope, request):
    """Read an existing qualification verifier result and its actual native review."""
    request = startup._canonical_copy(request)
    facts = _observe_current(scope, request)
    return _ReadinessEvidence(startup._canonical_freeze(request), startup._canonical_freeze(facts), _ISSUER)


def evidence(observation, scope):
    """Only accept facts from a genuine observer that still match the owning scope."""
    _require(isinstance(observation, _ReadinessEvidence) and observation.issuer is _ISSUER,
        'Actual owner-issued review-readiness evidence is required')
    current = _observe_current(scope, startup._canonical_plain(observation.request))
    _require(current == startup._canonical_plain(observation.facts), 'Review-readiness owner evidence changed')
    return current
