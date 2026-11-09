"""Staged boundary placement; denial fixtures never issue qualification authority."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from runtime import delegation, evidence, repository_gate, setup_observation, state
from tests import test_evidence


class StagedAdmissionTests(TestCase):
    def setUp(self):
        self.fixture = test_evidence.EvidenceTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.scope = self.fixture.scope

    def deny(self):
        return patch.object(setup_observation, 'require_capability', create=True,
            side_effect=state.StateError('staged capability denied'))

    def test_check_denied_before_candidate_or_reservation(self):
        self.fixture.plan()
        with self.deny(), patch.object(evidence, 'capture_candidate',
                side_effect=AssertionError('candidate captured before admission')):
            with self.assertRaisesRegex(state.StateError, 'staged capability denied'):
                self.fixture.check()
        with state._database(self.scope) as db:
            self.assertEqual(delegation._ledger(self.scope, db), [])

    def test_completion_denied_before_verifier_reservation(self):
        self.fixture.plan()
        with self.deny(), patch.object(delegation, '_reserve_operation',
                side_effect=AssertionError('verifier reserved before admission')):
            with self.assertRaisesRegex(state.StateError, 'staged capability denied'):
                evidence.verify_completion(self.scope, self.fixture.request(purpose='completion'))

    def test_shared_operation_denied_before_artifacts(self):
        request = self.fixture.readiness_owner(session='parent', identity=self.fixture.identity)
        with self.deny(), patch.object(delegation, '_operation_record',
                side_effect=AssertionError('operation artifacts published before admission')):
            with self.assertRaisesRegex(state.StateError, 'staged capability denied'):
                delegation.reserve_coordinator(self.scope, request)

    def test_retired_ready_is_refused_and_preserves_task(self):
        before = state.task_status(self.scope, 'codex', 'parent')['task']
        with self.assertRaisesRegex(state.StateError, 'retired'):
            state.update_task(self.scope, self.fixture.request(state='READY',
                reason='Accepted next step', reference='user:accepted'))
        self.assertEqual(state.task_status(self.scope, 'codex', 'parent')['task'], before)

    def test_gate_denied_before_closed_source_factory(self):
        reference = dict(digest='a' * 64, name='a' * 32 + '.json')
        binding = dict(id='publication', host='github.com', repository='owner/repo',
            target='main', branch='feature', pull_request=1, head='b' * 40,
            snapshot=reference, repository_id=1)
        request = self.fixture.request(action='ready', publication_id='publication')
        with self.deny(), patch.object(repository_gate, '_acquire_qualified_readiness_source',
                side_effect=AssertionError('source acquired before admission')):
            with self.assertRaisesRegex(repository_gate.GateError, 'capability is unavailable'):
                with repository_gate.acquire_readiness(self.scope, request, binding):
                    self.fail('Denied readiness yielded an owner')

    def test_delivery_owner_denied_before_positive_authority(self):
        from scripts.delivery_coordinator import DeliveryOperationOwner
        request = self.fixture.request(operation_id='operation', publication_id='publication')
        owner = DeliveryOperationOwner(dict(action='ready', request_digest=evidence._digest(request),
            accepted_scope_digest=self.scope.accepted_scope_digest, reference='user:delivery'),
            native=None, repository=None, gate=None)
        with self.deny():
            with self.assertRaisesRegex(state.StateError, 'staged capability denied'):
                owner.authorize(self.scope, 'ready', request)

    def test_patch_event_denial_prevents_managed_edit(self):
        from runtime.engine import RuntimeEvent
        state.update_task(self.scope, self.fixture.request(state='WORKING',
            reason='Accepted implementation', reference='user:accepted'))
        event = RuntimeEvent('PreToolUse', 'codex', 'parent', self.scope.repository_root,
            'turn', 'call', 'apply_patch', False, 'fingerprint')
        with self.deny():
            result = state.process_event(self.scope, event)
        self.assertEqual(result.action, 'deny')

    def test_stop_denial_does_not_consume_continuation(self):
        from runtime.engine import RuntimeEvent
        state.update_task(self.scope, self.fixture.request(state='WORKING',
            reason='Accepted implementation', reference='user:accepted'))
        event = RuntimeEvent('PreToolUse', 'codex', 'parent', self.scope.repository_root,
            'turn', 'call', 'apply_patch', False, 'fingerprint')
        state.process_event(self.scope, event)
        condition = dict(id='next', kind='runnable', action='task', reason='Finish accepted step',
            reference='user:accepted', continuation=dict(turn_id='turn', budget=1))
        state.update_task(self.scope, self.fixture.request(state='WORKING',
            reason='Accepted implementation', reference='user:accepted', conditions=[condition]))
        with self.deny():
            result = state.process_event(self.scope, RuntimeEvent('Stop', 'codex', 'parent',
                self.scope.repository_root, 'turn', None, None, False))
        self.assertNotEqual(result.action, 'continue')
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        self.assertEqual(task['conditions'][0]['continuation']['consumed'], 0)

    def test_fully_qualified_legacy_task_keeps_ordinary_behavior(self):
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        admission = state._capability(self.scope, task, 'task-start')
        self.assertEqual(admission.mode, 'ordinary')
        self.assertIsNone(admission.limits)
        self.assertNotIn('qualification_attempt', task)
        state._ordinary_capability(self.scope, task, 'task-ready')
        result = state.update_task(self.scope, self.fixture.request(state='WORKING',
            reason='Accepted ordinary next step', reference='user:accepted'))
        self.assertEqual(result['task']['state'], 'WORKING')


class GenuineStagedAdmissionTests(TestCase):
    def setUp(self):
        from tests.test_staged_setup import StagedJournalTests
        self.owner = StagedJournalTests('runTest')
        self.addCleanup(self.owner.doCleanups)
        self.owner.setUp()
        self.fixture, self.journal, task = self.owner.staged()
        self.identity = dict(task, scope_ref='fixture:guard-probe')
        env = patch.dict(os.environ, HOME=str(self.owner.home.parent), CODEX_HOME=str(self.owner.home))
        env.start()
        self.addCleanup(env.stop)

    def acquire(self, *, operations=('task-start',), keys=('pre-tool-enforcement',), **limits):
        from runtime import changes, startup
        attempt = self.journal['qualification_scope']['attempts'][0]
        attempt['operations'] = list(operations)
        attempt['capability_keys'] = list(keys)
        attempt['limits'].update(limits)
        changes._save(self.fixture.directory, self.journal)
        acquired = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=self.fixture.repo), state.PACKAGE)
        self.assertEqual(acquired.resolution.status, 'active', acquired.resolution)
        self.scope = acquired.resolution.scope
        return self.scope

    def start(self, **updates):
        return state.start_task(self.scope, self.identity | dict(mode='editable',
            decision_ref='user:qualification', attempt_ref=self.owner.attempt_ref) | updates)

    def task(self):
        return state.task_status(self.scope, 'codex', self.identity['session_id'])['task']

    def request(self, **updates):
        return self.identity | dict(expected_revision=self.task()['revision']) | updates

    def records(self):
        with state._database(self.scope) as db:
            return delegation._ledger(self.scope, db, task=self.identity)

    def operation(self, dispatch='probe-operation'):
        return self.request(dispatch_id=dispatch,
            claims=[dict(kind='path', key=str(self.fixture.repo / 'probe-output.txt'), mode='write')],
            operation=dict(id=dispatch, kind='coordinator-work'), reason='Bounded disposable qualification',
            ownership_ref='fixture:approved-operation', stop_conditions=['Stop after this original reservation'])

    def working(self):
        from runtime.engine import RuntimeEvent
        state.update_task(self.scope, self.request(state='WORKING', reason='Observe accepted guard',
            reference='user:qualification'))
        event = RuntimeEvent('PreToolUse', 'codex', self.identity['session_id'], self.fixture.repo,
            'guard-turn', 'guard-call', 'apply_patch', False, 'guard-fingerprint')
        self.assertEqual(state.process_event(self.scope, event).action, 'pass')
        condition = dict(id='next', kind='runnable', action='task', reason='Finish original bounded observation',
            reference='user:qualification', continuation=dict(turn_id=event.turn_id, budget=1))
        state.update_task(self.scope, self.request(state='WORKING', reason='Retain observed next step',
            reference='user:qualification', conditions=[condition]))
        return event

    def reconcile_unstarted(self, attempt_id):
        from tests.fixture_canonical_birth import fixture_artifact
        record = next(row for row in self.records() if row['id'] == attempt_id)
        task = self.task()
        operation = evidence._read(self.scope, record['body']['operation'])
        binding = dict(operation_id=operation['id'], operation_digest=record['body']['operation']['digest'],
            claims_digest=evidence._digest(record['data']['claims']))
        observed = fixture_artifact(self.scope, self.identity, binding | dict(
            observation='Fixture reserved only: no operation or child was launched'))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=attempt_id, dispatch_id=record['owner']['dispatch_id'],
            assignment_digest=record['data']['assignment']['digest'], decision_ref=task['decision_ref'],
            operation_evidence=observed, resource_observations=observed, operation_stopped=True,
            operation_descendants_stopped=True, observation_ref='fixture:observed-no-launch',
            coordinator_ref='fixture:independent-lifecycle', **binding)
        raw = json.dumps(proof).encode()
        path = self.fixture.base / 'qualification-stop.json'
        path.write_bytes(raw)
        path.chmod(0o400)
        return delegation.reconcile_assignment(self.scope, self.request(attempt_id=attempt_id,
            host_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()),
            assessment=dict(outcome='cancelled', changed_paths=[], result_assessment='No execution started',
                partial_change_assessment='No operation effects or descendants exist',
                evidence=['fixture:observed-no-launch'], coordinator_ref='fixture:independent-lifecycle')))

    def test_actual_owner_issues_and_persists_only_exact_task_attempt(self):
        self.acquire()
        plain = self.identity | dict(mode='editable', decision_ref='user:qualification')
        for change in ({}, dict(attempt_ref=dict(reference='fixture:forged', sha256='f' * 64)),
                dict(attempt_ref=self.owner.attempt_ref, task_id='wrong-task'),
                dict(attempt_ref=self.owner.attempt_ref, session_id='wrong-native-session'),
                dict(qualification_attempt=self.owner.attempt_ref)):
            with self.subTest(change=change), self.assertRaises(state.StateError):
                state.start_task(self.scope, plain | change)
        self.assertIsNone(self.task())
        task = self.start()['task']
        self.assertEqual(task['qualification_attempt'], self.owner.attempt_ref)
        admission = state._capability(self.scope, task, 'task-start')
        self.assertEqual(admission.mode, 'qualification')
        self.assertEqual(state._canonical_plain(admission.original_attempt)['attempt']['task'],
            {key:self.identity[key] for key in ('client', 'session_id', 'task_id')})
        with self.assertRaises(TypeError):
            admission.limits['max_attempts'] = 99
        for replacement in (None, dict(reference='fixture:replacement', sha256='d' * 64)):
            changed = deepcopy(task)
            if replacement is None:
                changed.pop('qualification_attempt')
            else:
                changed['qualification_attempt'] = replacement
            with self.subTest(replacement=replacement), state._database(self.scope, write=True) as db:
                with self.assertRaisesRegex(state.StateError, 'purpose is immutable'):
                    state._save(db, state._scope_id(self.scope), changed)
        self.assertEqual(self.task()['qualification_attempt'], self.owner.attempt_ref)
        for operation in ('task-ready', 'gate-ready', 'delivery-ready', 'verify-merge'):
            with self.subTest(operation=operation), self.assertRaises(state.StateError):
                state._ordinary_capability(self.scope, task, operation)
        with self.assertRaises(state.StateError):
            state._capability(replace(self.scope, repository_root=self.fixture.repo.parent), task, 'task-start')

    def test_expired_original_attempt_cannot_start_task(self):
        self.acquire(deadline=time.time() - 1)
        with self.assertRaises(state.StateError):
            self.start()
        self.assertIsNone(self.task())

    def test_expiry_blocks_patch_and_stop_but_keeps_negative_observations(self):
        from runtime.engine import RuntimeEvent
        self.acquire(operations=('task-start', 'task-patch', 'task-continue'),
            keys=('pre-tool-enforcement', 'stop-ownership'))
        self.start()
        event = self.working()
        deadline = self.journal['qualification_scope']['attempts'][0]['limits']['deadline']
        with patch.object(setup_observation.time, 'time', return_value=deadline + 1):
            denied = state.process_event(self.scope, replace(event, tool_call_id='late-call'))
            self.assertEqual(denied.action, 'deny')
            stop = state.process_event(self.scope, RuntimeEvent('Stop', 'codex', self.identity['session_id'],
                self.fixture.repo, event.turn_id, None, None, False))
            self.assertNotEqual(stop.action, 'continue')
            self.assertEqual(self.task()['conditions'][0]['continuation']['consumed'], 0)
            state.process_event(self.scope, replace(event, kind='Interrupt', tool_call_id=None, tool_name=None))
            self.assertEqual(self.task()['state'], 'PAUSED')
            before = state.task_status(self.scope, 'codex', self.identity['session_id'])['generation']
            state.process_event(self.scope, replace(event, kind='PostToolUse', tool_call_id='late-call'))
            after = state.task_status(self.scope, 'codex', self.identity['session_id'])
            self.assertGreater(after['generation'], before)
            self.assertEqual(after['task']['state'], 'PAUSED')
            self.assertFalse(after['task']['turn_uncertain'])

    def test_unmatched_live_reservation_cannot_fund_new_patch_or_stop(self):
        from runtime.engine import RuntimeEvent
        self.acquire(operations=('task-start', 'task-patch', 'task-continue', 'operation-coordinator'),
            keys=('pre-tool-enforcement', 'stop-ownership'))
        self.start()
        event = self.working()
        result = delegation.reserve_coordinator(self.scope, self.operation())
        self.assertTrue(result['reservation_admitted'])
        self.assertEqual(state.process_event(self.scope, replace(event, tool_call_id='new-call')).action, 'deny')
        self.assertNotEqual(state.process_event(self.scope, RuntimeEvent('Stop', 'codex',
            self.identity['session_id'], self.fixture.repo, event.turn_id, None, None, False)).action, 'continue')
        self.assertEqual(self.task()['conditions'][0]['continuation']['consumed'], 0)

    def test_exhausted_settled_allocation_stays_spent_after_exact_recovery(self):
        from runtime.engine import RuntimeEvent
        from tests.fixture_staged_successor import publish_successor
        self.acquire(operations=('task-start', 'task-patch', 'task-continue', 'operation-coordinator'),
            keys=('pre-tool-enforcement', 'stop-ownership'))
        self.start()
        event = self.working()
        request = self.operation()
        first = delegation.reserve_coordinator(self.scope, request)
        replay = delegation.reserve_coordinator(self.scope, request)
        self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertFalse(replay['reservation_admitted'])
        deadline = self.journal['qualification_scope']['attempts'][0]['limits']['deadline']
        with patch.object(setup_observation.time, 'time', return_value=deadline + 1):
            self.assertEqual(self.reconcile_unstarted(first['attempt_id'])['status'], 'settled')
        self.assertEqual(self.records()[0]['data']['status'], 'settled')
        previous_ref = state._canonical_plain(self.scope.qualification['reference'])
        self.scope = publish_successor(self.owner, self.fixture, self.journal)
        self.assertNotEqual(state._canonical_plain(self.scope.qualification['reference']), previous_ref)
        self.assertEqual(state._capability(self.scope, self.task(), 'operation-coordinator').limits['max_attempts'], 1)
        with self.assertRaisesRegex(state.StateError, 'allocation'):
            delegation.reserve_coordinator(self.scope, self.operation('replacement-operation'))
        self.assertEqual(state.process_event(self.scope, replace(event, tool_call_id='after-settlement')).action, 'deny')
        self.assertNotEqual(state.process_event(self.scope, RuntimeEvent('Stop', 'codex',
            self.identity['session_id'], self.fixture.repo, event.turn_id, None, None, False)).action, 'continue')
        self.assertEqual(len(self.records()), 1)

    def test_full_successor_keeps_original_qualification_purpose_and_denies_ready(self):
        from tests.fixture_staged_successor import publish_successor
        self.acquire()
        original = self.start()['task']
        content = next(iter(self.journal['setup_receipts'].values()))
        assessment = deepcopy(content['assessment'])
        for row in assessment['cases']:
            observed = content['source']['observations'].get(row['id'], [])
            self.assertTrue(observed, row['id'])
            proofs = {json.dumps(proof, sort_keys=True):proof for proof in [*row['evidence'], *observed]}
            row.update(status='verified', evidence=list(proofs.values()))
        retained = {row['id']:row for row in self.owner.assessment['requirements']}
        for row in assessment['requirements']:
            self.assertEqual(retained[row['id']]['status'], 'verified')
            self.assertTrue(retained[row['id']]['evidence'])
            row.update(status='verified', evidence=deepcopy(retained[row['id']]['evidence']))
        self.scope = publish_successor(self.owner, self.fixture, self.journal, assessment=assessment)
        self.assertTrue(self.scope.qualification['complete'])
        current = self.task()
        self.assertEqual(current['qualification_attempt'], original['qualification_attempt'])
        admission = state._capability(self.scope, current, 'task-start')
        self.assertEqual(admission.mode, 'qualification')
        self.assertEqual(state._canonical_plain(admission.attempt_ref), self.owner.attempt_ref)
        self.assertEqual(admission.limits['max_attempts'], 1)
        with self.assertRaises(state.StateError):
            state.update_task(self.scope, self.request(state='READY', reason='Attempt ordinary promotion',
                reference='user:qualified-successor'))
        response = evidence._verification_response(current, [])
        self.assertFalse(response['verified'])
        self.assertFalse(response['implementation_verified'])
        self.assertFalse(response['delivery_complete'])
        self.assertEqual(self.task()['state'], 'TRIAGE')

    def test_actual_admission_rejects_excess_original_resource_allocation(self):
        self.acquire(operations=('task-start', 'operation-coordinator'))
        self.start()
        task = self.task()
        admission = state._capability(self.scope, task, 'operation-coordinator')
        with self.assertRaisesRegex(state.StateError, 'allocation'):
            delegation._qualification_allocation(self.scope, task, admission, self.records(),
                additional_attempts=1, resource_units=2)
        self.assertEqual(self.records(), [])

    def test_original_plan_binding_rejects_patch_and_stop_after_real_plan_change(self):
        from runtime import changes
        from runtime.engine import RuntimeEvent
        operations = ('task-start', 'task-patch', 'task-continue')
        keys = ('pre-tool-enforcement', 'stop-ownership')
        self.acquire(operations=operations, keys=keys)
        self.start()
        (self.fixture.repo / 'app.py').write_text('value = 1\n')
        proxy = SimpleNamespace(api=evidence, scope=self.scope, request=self.request)
        first = test_evidence.EvidenceTests.plan(proxy, decision_ref='user:qualification')
        original_plan = first['task']['evidence']['plan']
        # Synthetic host authorization binds an actual owned plan before the tested effects.
        self.journal['qualification_scope']['attempts'][0]['plan_reference'] = original_plan
        changes._save(self.fixture.directory, self.journal)
        self.acquire(operations=operations, keys=keys)
        event = self.working()
        updated = evidence._load_plan(self.scope, self.identity)
        updated['criteria'][0]['id'] = 'changed-acceptance'
        second = evidence.plan_evidence(self.scope, self.request(plan=updated,
            change_decision_ref='user:approved-plan-change'))
        self.assertNotEqual(second['task']['evidence']['plan'], original_plan)
        self.assertEqual(self.records(), [])
        self.assertEqual(state.process_event(self.scope, replace(event, tool_call_id='changed-plan')).action, 'deny')
        self.assertNotEqual(state.process_event(self.scope, RuntimeEvent('Stop', 'codex',
            self.identity['session_id'], self.fixture.repo, event.turn_id, None, None, False)).action, 'continue')
        self.assertEqual(self.task()['conditions'][0]['continuation']['consumed'], 0)

    def test_actual_local_check_uses_original_deadline_instead_of_longer_command_timeout(self):
        self.acquire(operations=('task-start', 'check-run'),
            keys=('pre-tool-enforcement', 'review-readiness'), deadline=time.time() + 5)
        self.start()
        admission = state._capability(self.scope, self.task(), 'check-run')
        check = dict(cwd='.', timeout=20, env={'PATH':os.defpath},
            argv=[sys.executable, '-c', 'import time; time.sleep(20)'])
        remaining = admission.limits['deadline'] - time.time()
        self.assertGreater(remaining, 0)
        began = time.monotonic()
        result = evidence._execute(self.scope, check, dict(path=str(Path(sys.executable).resolve())),
            qualification_deadline=admission.limits['deadline'])
        self.assertTrue(result['process_started'])
        self.assertEqual(result['status'], 'timed-out')
        self.assertLess(time.monotonic() - began, remaining + 1)


class QualificationProjectionTests(TestCase):
    def test_qualification_predicate_cannot_report_ordinary_success(self):
        task = dict(qualification_attempt=dict(reference='fixture:selector', sha256='a' * 64))
        result = evidence._verification_response(task, [])
        self.assertFalse(result['verified'])
        self.assertFalse(result['implementation_verified'])

    def test_qualification_task_cannot_be_promoted_by_completion_projection(self):
        from runtime import delivery
        task = dict(state='REVIEW', conditions=[],
            qualification_attempt=dict(reference='fixture:selector', sha256='a' * 64))
        requirements = dict(blockers=[], delivery_complete=True, waiting_for_merge=False)
        delivery.apply_completion(task, requirements, True)
        self.assertEqual(task['state'], 'REVIEW')


class StagedNativeAdmissionTests(TestCase):
    def test_worker_denied_before_discovery(self):
        from tests.test_delegation import AdmissionTests
        fixture = AdmissionTests(methodName='runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.plan()
        with patch.object(setup_observation, 'require_capability', create=True,
                side_effect=state.StateError('staged capability denied')):
            with patch.object(delegation, '_discovery',
                    side_effect=AssertionError('native discovery before admission')):
                with self.assertRaisesRegex(state.StateError, 'staged capability denied'):
                    fixture.prepare()
