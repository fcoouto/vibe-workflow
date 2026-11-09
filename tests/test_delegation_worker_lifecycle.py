"""Private guidance-cache fixtures; no network, models or live client changes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import hashlib
import sys
import sqlite3
from contextlib import closing
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from runtime import changes, delegation, evidence, state
from tests.fixture_delegation import AdmissionFixture


class WorkerLifecycleAdmissionTests(AdmissionFixture, unittest.TestCase):
    def test_worker_reconcile_binds_changed_candidate_and_quiescence_without_passing_d5(self):
        from runtime import evidence, changes
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        (self.repo / 'app.py').write_text('changed by fixture worker\n')
        bad = self.worker_reconciliation_request(prepared, proof_delta=dict(descendants_stopped=False))
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, bad)
        omitted = self.worker_reconciliation_request(prepared, changed_paths=[])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, omitted)
        request = self.worker_reconciliation_request(prepared)
        original_capture = evidence.capture_candidate
        def capture(*args):
            with state._database(self.scope, write=True), changes._authority_guards([self.scope.runtime.state_dir]):
                pass
            return original_capture(*args)
        with patch.object(evidence, 'capture_candidate', side_effect=capture):
            result = self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(result['status'], 'settled')
        self.assertEqual(result['outcome'], 'completed')
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('replay recaptured')):
            self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request | dict(assessment=request['assessment'] | dict(outcome='failed')))
        data = self.raw_assignment(prepared['attempt_id'])
        self.assertEqual(data['result']['outcome'], 'completed')
        self.assertIsNotNone(data['reconciliation'])
        report = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(report['capacity']['unresolved_managed_workers'], 0)
        self.assertEqual(report['budgets']['attempts_consumed'], 1)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(task['evidence']['attempts'], [])
        self.assertNotEqual(task['state'], 'DONE')

    def test_foreground_wait_is_bound_to_prepared_worker_and_replay(self):
        import sys
        directory = self.root / 'observer'
        directory.mkdir(mode=0o700)
        marker = directory / 'ready'
        wait = dict(marker=str(marker), python=str(Path(sys.executable).resolve()), seconds=15)
        plan = self.plan_value()
        claims = plan['jobs'][0]['claims']
        claims.append(dict(kind='path', key=str(marker), mode='read'))
        self.plan(plan)
        request = self.prepare_request()
        request['brief'].update(resources=claims, foreground_wait=wait)
        prepared = self.api.prepare_assignment(self.scope, request)
        self.assertEqual(self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])['foreground_wait'], wait)
        self.assertEqual(json.loads(prepared['prompt'])['assignment']['request']['brief']['foreground_wait'], wait)
        self.assertFalse(self.api.prepare_assignment(self.scope, request)['launch_admitted'])
        another = request | dict(dispatch_id='other-wait', expected_revision=self.request()['expected_revision'])
        with self.assertRaisesRegex(state.StateError, 'already belongs'):
            self.api.prepare_assignment(self.scope, another)
        request['brief']['foreground_wait'] = wait | dict(seconds=16)
        with self.assertRaisesRegex(state.StateError, 'changed payload'):
            self.api.prepare_assignment(self.scope, request)

    def test_foreground_wait_rejects_unreserved_or_preexisting_marker(self):
        import sys
        directory = self.root / 'observer'
        directory.mkdir(mode=0o700)
        marker = directory / 'ready'
        wait = dict(marker=str(marker), python=str(Path(sys.executable).resolve()), seconds=15)
        plan = self.plan_value()
        claims = plan['jobs'][0]['claims']
        claims.append(dict(kind='path', key=str(marker), mode='read'))
        self.plan(plan)
        request = self.prepare_request()
        request['brief'].update(resources=claims, foreground_wait=wait | dict(marker=str(directory / 'other')))
        with self.assertRaisesRegex(state.StateError, 'exact read claim'):
            self.api.prepare_assignment(self.scope, request)
        request['brief']['foreground_wait'] = wait
        marker.write_text('stale marker')
        with self.assertRaisesRegex(state.StateError, 'fresh marker'):
            self.api.prepare_assignment(self.scope, request)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 0)

    def test_cooperative_completion_releases_scoped_claims_and_preserves_strict_blockers(self):
        from runtime import evidence
        plan = self.plan_value()
        plan['limits']['max_parallel_workers'] = 1
        plan['jobs'].append(dict(plan['jobs'][0], id='followup', dependencies=['edit']))
        self.plan(plan)
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared)
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed) as observer:
            result = self.api.reconcile_assignment(self.scope, request)
            observer.assert_called_once_with(self.scope, prepared['attempt_id'], 'parent-turn', 'spawn-item', cooperative=True)
        self.assertEqual(result['status'], 'cooperative-completed')
        self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request | dict(assessment=request['assessment'] |
                dict(result_assessment='Different assessment for the completed assignment')))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
            records = self.api._ledger(self.scope, db, task=task)
            claims = records[0]['data']['claims']
            self.api._resources_available(self.scope, db, claims, records, cooperative_task=task)
            with self.assertRaisesRegex(state.StateError, 'unresolved assignment'):
                self.api._resources_available(self.scope, db, claims, records)
            self.assertTrue(evidence._ledger_blockers(self.scope, db, claims, records=records))
            artifact = evidence._read(self.scope, records[0]['data']['reconciliation'])
            self.assertNotIn('no_pending_continuation', artifact['proof'])
            self.assertNotIn('descendants_stopped', artifact['proof'])
        self.assertEqual(self.fixture.checked()['attempt']['status'], 'passed')
        later = self.prepare_request(dispatch_id='followup-request', job_id='followup')
        later['brief']['dependencies'] = ['edit']
        self.assertTrue(self.api.prepare_assignment(self.scope, later)['launch_admitted'])

    def test_cooperative_completion_can_be_upgraded_only_with_original_strict_proof(self):
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared, version=2)
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed):
            self.api.reconcile_assignment(self.scope, request)
        before = self.raw_assignment(prepared['attempt_id'])
        for proof_delta in (dict(descendants_stopped=False), dict(version=3)):
            with self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, proof_delta=proof_delta))
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), before)
        with state._database(self.scope) as db:
            continuation = self.request(attempt_id=prepared['attempt_id'])
            records = self.api._ledger(self.scope, db, task=continuation)
            with self.assertRaisesRegex(state.StateError, 'new admission'):
                self.api._continuation_owned(self.scope, db, continuation, records, allow_settled=True)
        strict = self.worker_reconciliation_request(prepared)
        self.assertEqual(self.api.reconcile_assignment(self.scope, strict)['status'], 'settled')
        after = self.raw_assignment(prepared['attempt_id'])
        self.assertEqual(evidence._read(self.scope, after['reconciliation'])['prior_cooperative'], before['reconciliation'])
        self.assertTrue(self.api.reconcile_assignment(self.scope, request)['replayed'])

    def test_cooperative_completion_rejects_pending_or_unbound_coordinator_assessment(self):
        self.plan()
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared)
        path = Path(request['host_proof']['path'])
        proof = json.loads(path.read_bytes())
        before = self.raw_assignment(prepared['attempt_id'])
        invalid = {
            'follow-up outstanding': proof | dict(no_outstanding_coordinator_followups=False),
            'submissions still open': proof | dict(coordinator_submissions_paused=False),
            'missing pause attestation': {key: value for key, value in proof.items() if key != 'coordinator_submissions_paused'},
            'non-boolean pause attestation': proof | dict(coordinator_submissions_paused=1),
            'legacy attestation mixed in': proof | dict(no_pending_continuation=True),
            'legacy version with new attestations': proof | dict(version=2),
            'wrong native identity': proof | dict(native_identity=proof['native_identity'] | dict(worker_id='another-child')),
            'different assessment': proof | dict(coordinator_ref='fixture:unbound-coordinator'),
            'different observation': proof | dict(observation_ref='fixture:unbound-observation'),
        }
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed):
            for case, candidate in invalid.items():
                with self.subTest(case=case):
                    raw = evidence._raw(candidate)
                    path.write_bytes(raw)
                    request['host_proof']['sha256'] = hashlib.sha256(raw).hexdigest()
                    with self.assertRaises(state.StateError):
                        self.api.reconcile_assignment(self.scope, request)
                    self.assertEqual(self.raw_assignment(prepared['attempt_id']), before)
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        self.assertTrue(status['conflicts'])

    def test_cooperative_completion_rejects_fresh_activity_or_candidate_changes(self):
        from runtime.startup import StartupError
        self.plan()
        prepared = self.prepare()
        request, observed = self.cooperative_worker(prepared)
        for changed in (observed | dict(activity=observed['activity'] | dict(source_digest='c' * 64)),
                        observed | dict(receipt=observed['receipt'] | dict(will_retry=True)),
                        observed | dict(receipt=observed['receipt'] | dict(turn_status='interrupted'), result=None)):
            with patch('runtime.adapters.codex.observe_native_worker', return_value=changed):
                with self.assertRaises(state.StateError):
                    self.api.reconcile_assignment(self.scope, request)
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('Missing foreground result')):
            with self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, request)
        (self.repo / 'app.py').write_text('unreviewed change\n')
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed):
            with self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'observed')
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertTrue(status['conflicts'])

    def test_never_started_worker_requires_exact_proof_and_cancellation_retains_budget(self):
        self.plan()
        prepared = self.prepare()
        bad = self.worker_reconciliation_request(prepared, outcome='cancelled', proof_delta=dict(candidate_digest='f' * 64))
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, bad)
        bad = self.worker_reconciliation_request(prepared, outcome='cancelled', partial_change_assessment='')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, bad)
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        state.update_task(self.scope, self.identity | dict(expected_revision=task['revision'], state='CANCELLED',
            reason='Cancel intent', reference='user:cancel'))
        result = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, outcome='cancelled'))
        self.assertEqual(result['outcome'], 'cancelled')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        current = state.task_status(self.scope, 'codex', 'parent')['task']
        replacement = state.start_task(self.scope, self.identity | dict(task_id='replacement', mode='editable',
            decision_ref='user:accepted', replaces=dict(task_id='task', expected_revision=current['revision'])))
        self.assertEqual(replacement['task']['task_id'], 'replacement')
        with state._database(self.scope) as db:
            historical = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(historical['pins'], current['pins'])
        self.assertEqual(historical['delegation']['attempts_consumed'], 1)
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_failed_observation_and_invalid_report_keep_ownership_until_exact_failed_reconciliation(self):
        from runtime.startup import StartupError
        self.plan()
        prepared = self.prepare()
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('fixture unavailable')):
            result = self.api.observe_assignment(self.scope, request)
        self.assertEqual(result['status'], 'uncertain')
        no_child = self.worker_reconciliation_request(prepared, outcome='CANCELLED', proof_delta=dict(launch_state='not-started'))
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, no_child)
        malformed = self.lifecycle_observation(prepared)
        malformed['result'] = '{"status":"completed"}'
        malformed['receipt']['result_digest'] = __import__('hashlib').sha256(malformed['result'].encode()).hexdigest()
        self.assertEqual(self.observe_worker(prepared, malformed)['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        result = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, outcome='failed'))
        self.assertEqual(result['outcome'], 'failed')
        retry = self.api.prepare_assignment(self.scope, self.prepare_request(dispatch_id='retry-2', retry_of=prepared['attempt_id'],
            diagnostic=dict(prior_attempt_id=prepared['attempt_id'], cause='Invalid result format',
                evidence=['fixture:new-independent-diagnosis'], change='Restate exact report contract')))
        self.assertTrue(retry['launch_admitted'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 2)

    def test_reconciliation_reads_ledger_once_per_transaction_around_capture(self):
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        request = self.worker_reconciliation_request(prepared)
        actual_ledger, actual_capture = self.api._ledger, evidence.capture_candidate
        captured, reads = [], []

        def ledger(scope, db, **options):
            reads.append((bool(captured), db.in_transaction))
            return actual_ledger(scope, db, **options)

        def capture(*args):
            candidate = actual_capture(*args)
            captured.append(True)
            return candidate

        with patch.object(self.api, '_ledger', side_effect=ledger), \
                patch.object(evidence, 'capture_candidate', side_effect=capture):
            result = self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(result['status'], 'settled')
        self.assertEqual(reads, [(False, True), (True, True)])

    def test_reconciliation_race_cannot_release_changed_revision(self):
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        request = self.worker_reconciliation_request(prepared)
        original_capture = evidence.capture_candidate
        def capture(*args):
            candidate = original_capture(*args)
            task = state.task_status(self.scope, 'codex', 'parent')['task']
            state.update_task(self.scope, self.identity | dict(expected_revision=task['revision'], state='REVIEW',
                reason='Intervening coordinator update', reference='fixture:race'))
            return candidate
        with patch.object(evidence, 'capture_candidate', side_effect=capture), self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request)
        self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_retirement_cannot_forget_unresolved_historical_worker(self):
        import hashlib
        self.plan()
        prepared = self.prepare()
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        proof = dict(version=1, kind='stopped-writer-reconciliation', identity=self.identity, expected_revision=task['revision'],
            expected_pins=task['pins'], observed_turn_id=task['turn_id'], stopped=True, descendants_stopped=True,
            attempt_ids=[], observation_ref='host:coordinator-stopped', coordinator_ref='coordinator:checked')
        path = self.root / 'retire-proof.json'; raw = json.dumps(proof).encode(); path.write_bytes(raw); path.chmod(0o600)
        request = self.identity | dict(expected_revision=task['revision'], expected_pins=task['pins'], reason='Retire',
            reference='host:inspected', stop_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()))
        with self.assertRaisesRegex(state.StateError, 'assignment'):
            state.retire_task(self.scope, request)
        # Construct only a historical binding fixture; supported replacement is separately blocked above.
        with state._database(self.scope, write=True) as db:
            db.execute('delete from bindings where scope_id=?', (state._scope_id(self.scope),))
        with self.assertRaisesRegex(state.StateError, 'assignment'):
            state.start_task(self.scope, self.identity | dict(task_id='new', mode='editable', decision_ref='user:accepted'))
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'prepared')

    def test_worker_observation_is_immutable_idempotent_and_outside_locks(self):
        from runtime import evidence, changes
        self.plan()
        prepared = self.prepare()
        observed = self.lifecycle_observation(prepared)
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        def observe(*args):
            with state._database(self.scope, write=True), changes._authority_guards([self.scope.runtime.state_dir]):
                pass
            return observed
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=observe):
            first = self.api.observe_assignment(self.scope, request)
            duplicate = self.api.observe_assignment(self.scope, request)
        self.assertEqual(first['revision'], duplicate['revision'])
        self.assertEqual(first['observation_digest'], duplicate['observation_digest'])
        self.assertTrue(duplicate['replayed'])
        from runtime.startup import StartupError
        failure_request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('fixture unavailable')):
            self.assertEqual(self.api.observe_assignment(self.scope, failure_request)['status'], 'uncertain')
        self.assertEqual(self.observe_worker(prepared, observed)['status'], 'observed')
        data = self.raw_assignment(prepared['attempt_id'])
        self.assertEqual(data['status'], 'observed')
        self.assertIsNone(data['reconciliation'])
        self.assertIsNone(data['result'])
        artifact = evidence._read(self.scope, data['observation'])
        self.assertEqual(artifact['receipt'], observed['receipt'])
        self.assertEqual(artifact['result'], observed['result'])
        self.assertEqual(artifact['report']['status'], 'completed')
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(task['evidence']['attempts'], [])
        self.assertEqual(task['delegation']['attempts_consumed'], 1)

    def test_worker_progress_retains_history_and_conflicting_child_is_uncertain(self):
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        first = self.observe_worker(prepared, self.lifecycle_observation(prepared, turn_status='inProgress'))
        second = self.observe_worker(prepared, self.lifecycle_observation(prepared, worker_turn_id='child-turn-2',
            result_item_id='final-item-2', configured_model='observed-different-model'))
        self.assertGreater(second['revision'], first['revision'])
        latest = self.raw_assignment(prepared['attempt_id'])['observation']
        artifact = evidence._read(self.scope, latest)
        self.assertEqual(evidence._read(self.scope, artifact['previous'])['receipt']['turn_status'], 'inProgress')
        self.assertEqual(artifact['receipt']['worker_turn_id'], 'child-turn-2')
        self.assertEqual(artifact['receipt']['configured_model'], 'observed-different-model')
        self.assertEqual(artifact['receipt']['requested_model'], 'new-native-model')
        self.assertIsNone(artifact['receipt']['execution_model'])
        self.assertIsNone(artifact['receipt']['worker_model_provider'])
        conflict = self.observe_worker(prepared, self.lifecycle_observation(prepared, worker_id='another-child'))
        self.assertEqual(conflict['status'], 'uncertain')
        stored = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])
        self.assertEqual(stored['receipt']['worker_id'], 'child')
        self.assertEqual(evidence._read(self.scope, stored['conflict'])['receipt']['worker_id'], 'another-child')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)

    def test_duplicate_uncertain_observation_keeps_original_response_and_history(self):
        from runtime.startup import StartupError
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        conflicting = self.lifecycle_observation(prepared, worker_id='another-child')
        first = self.observe_worker(prepared, conflicting, request)
        duplicate = self.observe_worker(prepared, conflicting, request)
        self.assertTrue(duplicate['replayed'])
        self.assertEqual(duplicate['revision'], first['revision'])
        self.assertEqual(duplicate['observation_digest'], first['observation_digest'])
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', side_effect=StartupError('fixture unavailable')):
            first = self.api.observe_assignment(self.scope, request)
            duplicate = self.api.observe_assignment(self.scope, request)
        self.assertTrue(duplicate['replayed'])
        self.assertEqual(duplicate['revision'], first['revision'])
        self.assertEqual(duplicate['observation_digest'], first['observation_digest'])

    def test_worker_lifecycle_guard_and_history_bound_preserve_unresolved_ownership(self):
        from runtime import changes
        self.plan()
        prepared = self.prepare()
        original = self.raw_assignment(prepared['attempt_id'])
        with changes._authority_guards([self.scope.runtime.state_dir]), self.assertRaises(state.StateError):
            self.observe_worker(prepared)
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), original)
        with patch.object(self.api, 'MAX_OBSERVATIONS', 1):
            observed = self.lifecycle_observation(prepared, turn_status='inProgress')
            first = self.observe_worker(prepared, observed)
            retained = self.raw_assignment(prepared['attempt_id'])
            self.assertTrue(self.observe_worker(prepared, observed)['replayed'])
            with self.assertRaisesRegex(state.StateError, 'history bound'):
                self.observe_worker(prepared)
            self.assertEqual(self.raw_assignment(prepared['attempt_id']), retained)
        request = self.worker_reconciliation_request(prepared, outcome='cancelled')
        with changes._authority_guards([self.scope.runtime.state_dir]), self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, request)
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), retained)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)

    def test_same_turn_replacement_final_item_retains_conflict_after_valid_new_turn(self):
        import hashlib
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        next_turn = self.lifecycle_observation(prepared, worker_turn_id='next-turn', result_item_id='next-final')
        self.assertEqual(self.observe_worker(prepared, next_turn)['status'], 'observed')
        replacement = deepcopy(next_turn)
        report = json.loads(replacement['result'])
        report['limitations'] = ['Replacement final message contradicts immutable turn history']
        replacement['result'] = json.dumps(report)
        replacement['receipt']['result_item_id'] = 'replacement-final'
        replacement['receipt']['result_digest'] = hashlib.sha256(replacement['result'].encode()).hexdigest()
        result = self.observe_worker(prepared, replacement)
        self.assertEqual(result['status'], 'uncertain')
        data = self.raw_assignment(prepared['attempt_id'])
        retained = evidence._read(self.scope, data['observation'])
        self.assertEqual(retained['receipt']['result_item_id'], 'next-final')
        self.assertEqual(retained['receipt']['result_digest'], next_turn['receipt']['result_digest'])
        self.assertEqual(evidence._read(self.scope, retained['conflict'])['receipt']['result_item_id'], 'replacement-final')
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)

    def test_new_turn_cannot_reuse_a_final_item_with_changed_content(self):
        import hashlib
        from runtime import evidence
        self.plan()
        prepared = self.prepare()
        original = self.lifecycle_observation(prepared)
        self.observe_worker(prepared, original)
        replacement = self.lifecycle_observation(prepared, worker_turn_id='new-turn-same-final-item')
        report = json.loads(replacement['result'])
        report['limitations'] = ['Changed content under the previously observed final-item identity']
        replacement['result'] = json.dumps(report)
        replacement['receipt']['result_digest'] = hashlib.sha256(replacement['result'].encode()).hexdigest()
        self.assertEqual(self.observe_worker(prepared, replacement)['status'], 'uncertain')
        retained = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])
        self.assertEqual(retained['receipt'], original['receipt'])
        self.assertEqual(evidence._read(self.scope, retained['conflict'])['receipt'], replacement['receipt'])
        with self.assertRaises(state.StateError):
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(status['budgets']['attempts_consumed'], 1)

    def test_foreign_reservation_during_capture_refuses_stale_candidate_settlement(self):
        from runtime import evidence
        (self.repo / 'other.py').write_text('before foreign worker\n')
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        foreign = self.second_owner(1, [self.path_claim('other.py')])
        request = self.worker_reconciliation_request(prepared)
        original = self.raw_assignment(prepared['attempt_id'])
        original_capture = evidence.capture_candidate
        foreign_admission = []
        def capture(scope, plan):
            candidate = original_capture(scope, plan)
            with patch.object(evidence, 'capture_candidate', side_effect=original_capture):
                foreign_admission.append(self.api.prepare_assignment(self.scope, foreign))
            (self.repo / 'other.py').write_text('changed by foreign admitted worker\n')
            return candidate
        with patch.object(evidence, 'capture_candidate', side_effect=capture):
            with self.assertRaisesRegex(state.StateError, 'reservation.*changed'):
                self.api.reconcile_assignment(self.scope, request)
        self.assertTrue(foreign_admission[0]['launch_admitted'])
        self.assertEqual(self.raw_assignment(prepared['attempt_id']), original)
        # Recapture/proof for the real candidate may settle the original worker; the foreign owner remains held.
        fresh = self.worker_reconciliation_request(prepared, changed_paths=['app.py', 'other.py'])
        self.assertEqual(self.api.reconcile_assignment(self.scope, fresh)['outcome'], 'completed')
        self.assertEqual(self.raw_assignment(foreign_admission[0]['attempt_id'])['status'], 'prepared')
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        with state._database(self.scope) as db:
            self.assertEqual(len(self.api._ledger(self.scope, db)), 2)

    def test_replacement_cannot_forget_unresolved_worker_even_after_cancellation(self):
        self.plan()
        prepared = self.prepare()
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        state.update_task(self.scope, self.identity | dict(expected_revision=task['revision'], state='CANCELLED',
            reason='Cancel intent retains ownership', reference='user:cancel'))
        current = state.task_status(self.scope, 'codex', 'parent')['task']
        with self.assertRaisesRegex(state.StateError, 'assignment'):
            state.start_task(self.scope, self.identity | dict(task_id='replacement', mode='editable', decision_ref='user:accepted',
                replaces=dict(task_id='task', expected_revision=current['revision'])))
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'prepared')


class RetainedAuthorityAdmissionTests(AdmissionFixture, unittest.TestCase):
    def test_retained_d4_shared_authority_excludes_owner_after_scan(self):
        from runtime import changes
        d4,request,directory=self.retained_d4()
        before=d4.retained_inventory(directory)
        binding=dict(transaction_id=request['manifest']['transaction_id'],manifest_digest=request['approval']['manifest_digest'],
            manifest=request['manifest'],approval=request['approval'],plan=d4.plan())
        rules=d4.repo/'AGENTS.md'
        scanned,finish=threading.Event(),threading.Event()
        original=changes._guard_writers
        def scan(*args,**kwargs):
            original(*args,**kwargs)
            if not scanned.is_set():
                scanned.set();self.assertTrue(finish.wait(timeout=10))
        with patch.object(changes,'_guard_writers',side_effect=scan):
            with ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(changes.apply_plan,d4.directory,request)
                try:
                    self.assertTrue(scanned.wait(timeout=5),'Actual retained D4 writer guard was not reached')
                    self.assert_retained_guard_blocked(directory)
                finally:finish.set()
                result=future.result(timeout=10)
        self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(result['completed'],['write-rules'],result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Collect adoption qualification/reviews and bind observations to current inputs before final enrollment')],result)
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),(b'product rules\nnew process\n',0o644))
        self.assertFalse((d4.repo/changes.PROJECT_FILE).exists())
        self.assertIsNone(d4.journal()['enrollment']['backup'])
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        self.assertEqual(before,d4.retained_inventory(directory))
        result=changes.resume_plan(d4.directory,d4.observe(request))
        d4.assert_activation_incompatible(result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Enrollment runtime state/pins are incompatible; reconcile them before activation')],result)
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        self.assertEqual(before,d4.retained_inventory(directory))
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),(b'product rules\nnew process\n',0o644))

    def test_uncertain_retained_d4_command_owner_holds_authority_until_reconciled(self):
        from runtime import changes
        d4,request,directory=self.retained_d4(command=True)
        before=d4.retained_inventory(directory)
        result=changes.apply_plan(d4.directory,request)
        self.assertEqual(result['exit_code'],2,result)
        self.assertFalse(d4.journal()['receipts']['command']['command']['stopped'])
        self.assert_retained_guard_blocked(directory)
        result=changes.resume_plan(d4.directory,d4.observe(request))
        d4.assert_activation_incompatible(result)
        self.assertTrue(d4.journal()['receipts']['command']['command']['stopped'])
        with changes._authority_guards([directory]):pass
        self.assertEqual(before,d4.retained_inventory(directory))

    def test_existing_retained_foreign_assignment_blocks_d4_before_mutation(self):
        from runtime import changes
        from contextlib import closing
        d4,request,directory=self.retained_d4()
        # Unsupported original assignment history is a protected unknown owner.
        with closing(sqlite3.connect(directory/'runtime.sqlite3')) as db,db:
            db.execute('insert into assignments values(?,?,?,?,?,?,?)',('f'*32,'original-foreign-scope','codex',
                'original-session','original-task','original-dispatch','{}'))
        before=d4.retained_inventory(directory)
        rules=(d4.repo/'AGENTS.md').read_bytes()
        reached=[];original=changes._writers
        def inspect(*args,**kwargs):reached.append(True);return original(*args,**kwargs)
        with patch.object(changes,'_writers',side_effect=inspect):result=changes.apply_plan(d4.directory,request)
        self.assertTrue(reached)
        self.assertEqual(result['exit_code'],2,result)
        self.assertIn('assignment history',result['blocked'][0]['reason'])
        self.assertEqual((d4.repo/'AGENTS.md').read_bytes(),rules)
        self.assertEqual(before,d4.retained_inventory(directory))

    def test_retained_d4_rollback_guards_proposed_actual_and_backup_authorities(self):
        from runtime import changes
        d4,request,directory=self.retained_d4(existing=True)
        old_directory=d4.base/'task-state'
        before={path:d4.retained_inventory(path) for path in (old_directory,directory)}
        pointer=d4.repo/changes.PROJECT_FILE;original_enrollment=pointer.read_bytes()
        rules=d4.repo/'AGENTS.md';original_rules=(rules.read_bytes(),rules.stat().st_mode&0o777)
        binding=dict(transaction_id=request['manifest']['transaction_id'],manifest_digest=request['approval']['manifest_digest'],
            manifest=request['manifest'],approval=request['approval'],plan=d4.plan())
        result=changes.apply_plan(d4.directory,request)
        self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(result['completed'],['write-rules'],result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Collect adoption qualification/reviews and bind observations to current inputs before final enrollment')],result)
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),(b'product rules\nnew process\n',0o644))
        self.assertFalse(pointer.exists())
        self.assertEqual((d4.directory/d4.journal()['enrollment']['backup']).read_bytes(),original_enrollment)
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        for path in before:self.assertEqual(before[path],d4.retained_inventory(path))
        result=changes.resume_plan(d4.directory,d4.observe(request))
        d4.assert_activation_incompatible(result)
        self.assertEqual(result['blocked'],[dict(id='transaction',
            reason='Enrollment runtime state/pins are incompatible; reconcile them before activation')],result)
        seen=[];held=[];original=changes._guard_writers;original_writers=changes._writers
        def inspect(*args,**kwargs):
            result=original_writers(*args,**kwargs)
            if kwargs.get('scope') is not None:
                self.assertTrue(changes._held_authority_guards([old_directory,directory]))
                held.append(kwargs['scope'].runtime.state_dir)
            return result
        def scan(*args,**kwargs):
            original(*args,**kwargs)
            for path in (old_directory,directory):
                with self.assertRaisesRegex(changes.Pending,
                        '^Shared state authority has an active or uncertain D4 operation; resume its bound journal$'):
                    with changes._authority_guards([path]):self.fail('Retained rollback authority unexpectedly released')
                seen.append(path)
        with patch.object(changes,'_writers',side_effect=inspect),patch.object(changes,'_guard_writers',side_effect=scan):
            result=changes.rollback_plan(d4.directory,d4.observe(request))
            self.assertEqual(set(held),{old_directory,directory})
            self.assertEqual(set(seen),{old_directory,directory})
            self.assertEqual(result['exit_code'],2,result)
            self.assertEqual(result['blocked'],[dict(id='enrollment',
                reason='Prior enrollment stays inactive until client/runtime compatibility is qualified')],result)
            self.assertEqual(d4.journal()['receipts']['write-rules']['status'],'rolled_back')
            self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),original_rules)
            self.assertFalse(pointer.exists())
            self.assertEqual((d4.directory/d4.journal()['enrollment']['backup']).read_bytes(),original_enrollment)
            self.assertEqual({key:d4.journal()[key] for key in binding},binding)
            for path in before:self.assertEqual(before[path],d4.retained_inventory(path))
            result=changes.rollback_plan(d4.directory,d4.observe(request))
        self.assertEqual(set(held),{old_directory,directory})
        self.assertEqual(set(seen),{old_directory,directory})
        self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(result['blocked'],[dict(id='enrollment',
            reason='Prior enrollment is stale or incompatible and remains inactive')],result)
        self.assertFalse(pointer.exists())
        self.assertEqual((rules.read_bytes(),rules.stat().st_mode&0o777),original_rules)
        self.assertEqual((d4.directory/d4.journal()['enrollment']['backup']).read_bytes(),original_enrollment)
        self.assertEqual({key:d4.journal()[key] for key in binding},binding)
        for path in before:self.assertEqual(before[path],d4.retained_inventory(path))

    def test_missing_retained_d4_journal_does_not_release_uncertain_owner(self):
        from runtime import changes
        d4,request,directory=self.retained_d4(command=True)
        before=d4.retained_inventory(directory)
        self.assertEqual(changes.apply_plan(d4.directory,request)['exit_code'],2)
        journal=d4.directory/'transaction.json';saved=journal.read_bytes();journal.unlink()
        self.assert_retained_guard_blocked(directory)
        self.assertEqual(changes.resume_plan(d4.directory,request)['exit_code'],1)
        self.assert_retained_guard_blocked(directory)
        journal.write_bytes(saved);journal.chmod(0o600)
        d4.assert_activation_incompatible(changes.resume_plan(d4.directory,d4.observe(request)))
        with changes._authority_guards([directory]):pass
        self.assertEqual(before,d4.retained_inventory(directory))


if __name__ == '__main__':
    unittest.main()
