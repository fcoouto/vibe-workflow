"""Private guidance-cache fixtures; no network, models or live client changes."""
from tests.fixture_canonical_birth import fixture_artifact
from copy import deepcopy
import json
import hashlib
import sys
import os
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import delegation, evidence, state
from tests import test_evidence
from tests.fixture_delegation import AdmissionFixture


class ModelFreeKernelAdmissionTests(AdmissionFixture, unittest.TestCase):
    def test_task_only_coordinator_kernel_has_no_invented_native_or_plan_facts(self):
        from runtime import evidence
        identity = self.identity | dict(session_id='coordinator-only', task_id='coordinator-only')
        state.start_task(self.scope, identity | dict(mode='editable', decision_ref='user:accepted'))
        request = self.operation_request(identity)
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('invented candidate')):
            with patch.object(self.api, 'guidance_status', side_effect=AssertionError('invented model guidance')):
                result = self.api._reserve_operation(self.scope, request, role='coordinator')
                replay = self.api._reserve_operation(self.scope, request, role='coordinator')
        self.assertTrue(result['reservation_admitted'])
        self.assertFalse(replay['reservation_admitted'])
        self.assertNotIn('launch_admitted', result)
        data = self.raw_assignment(result['attempt_id'])
        for key in ('candidate', 'binding', 'plan_digest', 'host_capacity'):
            self.assertIsNone(data[key])
        self.assertEqual(data['resource_units'], 0)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), identity['client'], identity['session_id'], identity['task_id'])
        self.assertNotIn('delegation', task)
        self.assertNotIn('evidence', task)

    def test_model_free_rows_do_not_consume_or_reset_native_counters(self):
        self.plan()
        request = self.operation_request()
        result = self.api._reserve_operation(self.scope, request, role='coordinator')
        report = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(report['budgets']['attempts_consumed'], 0)
        self.assertEqual(report['capacity']['unresolved_managed_workers'], 0)
        self.assertTrue(self.prepare()['launch_admitted'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)
        corrupted = self.raw_assignment(result['attempt_id'])
        corrupted['binding'] = dict(worker_id='fabricated')
        with state._database(self.scope, write=True) as db:
            db.execute('update assignments set data=? where id=?', (json.dumps(corrupted), result['attempt_id']))
        with self.assertRaises(state.StateError):
            self.api.delegation_status(self.scope, 'codex', 'parent')

    def test_task_only_coordinator_proof_binds_operation_claims_and_quiescence(self):
        from runtime import evidence
        identity = self.identity | dict(session_id='proof-coordinator', task_id='proof-coordinator')
        state.start_task(self.scope, identity | dict(mode='editable', decision_ref='user:accepted'))
        result = self.api._reserve_operation(self.scope, self.operation_request(identity), role='coordinator')
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), identity['client'], identity['session_id'], identity['task_id'])
        bindings = dict(operation_id='operation-1', operation_digest=record['body']['operation']['digest'],
                        claims_digest=evidence._digest(record['data']['claims']))
        observed = fixture_artifact(self.scope,self.identity, bindings | dict(observation='fixture:independent-operation-effects'))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='operation-1', assignment_digest=record['data']['assignment']['digest'],
            decision_ref=task['decision_ref'], operation_evidence=observed, resource_observations=observed,
            operation_stopped=True, operation_descendants_stopped=True, observation_ref='host:operation-tree',
            coordinator_ref='coordinator:independently-checked', **bindings)
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof), proof)
        for delta in (dict(operation_id='another-operation'), dict(claims_digest='f' * 64),
                      dict(operation_descendants_stopped=False), dict(candidate_digest='e' * 64), dict(expected_revision=True),
                      dict(expected_pins=task['pins'] | dict(schema_version=2.0))):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.api._validate_operation_proof(self.scope, record, task, proof | delta)
        other = dict(task, session_id='other-session', task_id='other-task')
        with self.assertRaises(state.StateError):
            self.api._validate_operation_proof(self.scope, record, other,
                proof | dict(identity={key: other[key] for key in self.identity}))

    def test_coordinator_retains_actual_plan_context_and_requires_matching_proof(self):
        from runtime import evidence
        self.plan()
        result = self.api._reserve_operation(self.scope, self.operation_request(), role='coordinator')
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(record['data']['candidate'], task['evidence']['candidate'])
        self.assertEqual(record['body']['d5_plan'], task['evidence']['plan'])
        self.assertEqual(record['body']['d6_plan'], task['delegation']['plan'])
        bindings = dict(operation_id='operation-1', operation_digest=record['body']['operation']['digest'],
                        claims_digest=evidence._digest(record['data']['claims']))
        observed = fixture_artifact(self.scope,self.identity, bindings | dict(observation='fixture:resource-effects'))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='operation-1', assignment_digest=record['data']['assignment']['digest'],
            decision_ref=task['decision_ref'], operation_evidence=observed, resource_observations=observed,
            operation_stopped=True, operation_descendants_stopped=True, observation_ref='host:operation-tree',
            coordinator_ref='coordinator:verified', d5_plan_digest=task['evidence']['plan']['digest'],
            d6_plan_digest=task['delegation']['plan']['digest'], candidate_digest=task['evidence']['candidate']['digest'],
            current_candidate_digest=task['evidence']['candidate']['digest'], **bindings)
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof,
            current_candidate_digest=proof['current_candidate_digest']), proof)
        missing = dict(proof)
        del missing['d6_plan_digest']
        with self.assertRaises(state.StateError):
            self.api._validate_operation_proof(self.scope, record, task, missing,
                current_candidate_digest=proof['current_candidate_digest'])

    def test_check_kernel_retains_real_d5_bindings_without_native_guidance(self):
        from runtime import evidence
        request = self.operation_request(claims=[self.path_claim(mode='read')],
            operation=dict(id='unit', kind='check'))
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('kernel invented capture')):
            with patch.object(self.api, 'guidance_status', side_effect=AssertionError('model-free check queried guidance')):
                result = self.api._reserve_operation(self.scope, request, role='check')
        data = self.raw_assignment(result['attempt_id'])
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        self.assertEqual(data['plan_digest'], task['evidence']['plan']['digest'])
        self.assertEqual(data['candidate'], task['evidence']['candidate'])
        self.assertIsNone(data['binding'])
        self.assertIsNone(data['host_capacity'])
        self.assertEqual(data['resource_units'], 0)
        self.assertNotIn('delegation', task)
        with self.assertRaises(state.StateError):
            self.api._reserve_operation(self.scope, request | dict(dispatch_id='wrong',
                operation=dict(id='missing', kind='check'), expected_revision=task['revision']), role='check')

    def test_check_kernel_proof_requires_actual_plan_execution_and_candidate_context(self):
        from runtime import evidence
        request = self.operation_request(claims=[self.path_claim(mode='read')], operation=dict(id='unit', kind='check'))
        result = self.api._reserve_operation(self.scope, request, role='check')
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        proof = dict(version=2, kind='check-writer-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='operation-1', assignment_digest=record['data']['assignment']['digest'],
            claims_digest=evidence._digest(record['data']['claims']), candidate_digest=record['data']['candidate']['digest'],
            current_candidate_digest=record['data']['candidate']['digest'], plan_digest=record['data']['plan_digest'],
            execution_digest=None, stopped=True, descendants_stopped=True,
            observation_ref='host:check-never-started', coordinator_ref='coordinator:inspected')
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof,
            current_candidate_digest=proof['current_candidate_digest']), proof)
        for delta in (dict(execution_digest='f' * 64), dict(plan_digest='e' * 64),
                      dict(stopped=False), dict(native_identity={}), dict(current_candidate_digest='d' * 64)):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.api._validate_operation_proof(self.scope, record, task, proof | delta,
                    current_candidate_digest=proof['current_candidate_digest'])

    def test_model_free_kernel_conflicts_and_changed_replay_cannot_bypass_native_owner(self):
        self.plan()
        first = self.prepare()
        request = self.operation_request(claims=[self.path_claim()])
        with self.assertRaises(state.StateError):
            self.api._reserve_operation(self.scope, request, role='coordinator')
        other = self.operation_request(claims=[self.path_claim('independent.txt')])
        reserved = self.api._reserve_operation(self.scope, other, role='coordinator')
        with self.assertRaises(state.StateError):
            self.api._reserve_operation(self.scope, other | dict(reason='changed payload'), role='coordinator')
        self.assertEqual(self.raw_assignment(first['attempt_id'])['status'], 'prepared')
        self.assertEqual(self.raw_assignment(reserved['attempt_id'])['status'], 'prepared')

    def test_native_kernel_proof_rejects_fake_observation_and_missing_candidate_binding(self):
        from runtime import evidence
        self.plan()
        result = self.prepare()
        with state._database(self.scope) as db:
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == result['attempt_id'])
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
        binding = record['data']['binding']
        native = {key: binding[key] for key in ('client_surface', 'native_version', 'parent_session_id', 'task_name')}
        native.update(parent_turn_id=None, spawn_item_id=None, worker_id=None, worker_turn_id=None, result_item_id=None)
        proof = dict(version=2, kind='assignment-writer-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=record['id'], dispatch_id='request-1', assignment_digest=record['data']['assignment']['digest'],
            candidate_digest=record['data']['candidate']['digest'], current_candidate_digest=record['data']['candidate']['digest'],
            d6_plan_digest=record['data']['plan_digest'], claims_digest=evidence._digest(record['data']['claims']),
            observation_digest=None, result_digest=None, native_identity=native, launch_state='not-started',
            stopped=True, descendants_stopped=True, observation_ref='host:searched-exact-parent-marker',
            coordinator_ref='coordinator:verified-not-launched')
        self.assertEqual(self.api._validate_operation_proof(self.scope, record, task, proof,
            current_candidate_digest=proof['current_candidate_digest']), proof)
        with self.assertRaises(state.StateError):
            self.api._validate_operation_proof(self.scope, record, task, proof)
        for delta in (dict(launch_state='observed'), dict(descendants_stopped=False),
                      dict(native_identity=native | dict(worker_id='unobserved-child')), dict(d6_plan_digest='f' * 64)):
            with self.subTest(delta=delta), self.assertRaises(state.StateError):
                self.api._validate_operation_proof(self.scope, record, task, proof | delta,
                    current_candidate_digest=proof['current_candidate_digest'])

    def test_history_bound_rejects_fresh_reservation_but_preserves_full_ledger_replay(self):
        self.plan()
        with patch.object(self.api, 'MAX_ASSIGNMENTS', 1):
            request = self.operation_request(claims=[self.path_claim(mode='read')])
            first = self.api._reserve_operation(self.scope, request, role='coordinator')
            with state._database(self.scope) as db:
                before = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
                original = db.execute('select * from assignments').fetchall()
            other = self.operation_request(dispatch_id='operation-2', claims=[self.path_claim(mode='read')],
                operation=dict(id='operation-2', kind='coordinator-work'))
            with self.assertRaisesRegex(state.StateError, 'history.*bound'):
                self.api._reserve_operation(self.scope, other, role='coordinator')
            replay = self.api._reserve_operation(self.scope, request | dict(expected_revision=before['revision']), role='coordinator')
            self.assertEqual(replay['attempt_id'], first['attempt_id'])
            self.assertTrue(replay['replayed'])
            self.assertFalse(replay['reservation_admitted'])
            with self.assertRaises(state.StateError):
                self.api._reserve_operation(self.scope, request | dict(reason='changed'), role='coordinator')
            with state._database(self.scope) as db:
                self.assertEqual(state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task'), before)
                self.assertEqual(db.execute('select * from assignments').fetchall(), original)
            status = self.api.delegation_status(self.scope, 'codex', 'parent')
            self.assertEqual(len(status['assignments']), 1)
            self.assertEqual(status['budgets']['attempts_consumed'], 0)
            self.assertEqual(status['budgets']['resource_units_consumed'], 0)

    def test_completed_check_dependency_admits_native_without_consuming_native_budget(self):
        value = self.plan_value()
        value['jobs'].append(dict(id='unit', role='check', claims=[self.path_claim(mode='read')],
            dependencies=[], max_attempts=1))
        value['jobs'][0]['dependencies'] = ['unit']
        self.plan(value)
        check = self.api._reserve_operation(self.scope, self.operation_request(claims=[self.path_claim(mode='read')],
            operation=dict(id='unit', kind='check')), role='check')
        request = self.prepare_request()
        request['brief']['dependencies'] = ['unit']
        with self.assertRaisesRegex(state.StateError, 'dependency.*complete'):
            self.api.prepare_assignment(self.scope, request)
        self.settle_fixture(check['attempt_id'], status='failed')
        with self.assertRaisesRegex(state.StateError, 'dependency.*complete'):
            self.api.prepare_assignment(self.scope, request)
        self.settle_fixture(check['attempt_id'], status='completed')
        native = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(native['launch_admitted'])
        self.assertFalse(self.api.prepare_assignment(self.scope, request)['launch_admitted'])
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 1)
        self.assertEqual(len(status['assignments']), 2)

    def test_check_with_same_name_cannot_satisfy_a_planned_native_dependency(self):
        value = self.plan_value()
        value['jobs'].append(dict(id='unit', role='worker', claims=[self.path_claim(mode='read')],
            dependencies=[], max_attempts=1))
        value['jobs'][0]['dependencies'] = ['unit']
        self.plan(value)
        check = self.api._reserve_operation(self.scope, self.operation_request(claims=[self.path_claim(mode='read')],
            operation=dict(id='unit', kind='check')), role='check')
        self.settle_fixture(check['attempt_id'], status='completed')
        request = self.prepare_request()
        request['brief']['dependencies'] = ['unit']
        with self.assertRaisesRegex(state.StateError, 'dependency.*complete'):
            self.api.prepare_assignment(self.scope, request)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 0)

    def test_role_records_reject_invented_fields_and_mismatched_original_bindings(self):
        from copy import deepcopy
        from runtime import evidence
        reserved = self.api._reserve_operation(self.scope, self.operation_request(), role='coordinator')
        with state._database(self.scope) as db:
            original = next(r for r in self.api._ledger(self.scope, db) if r['id'] == reserved['attempt_id'])
        for mutation in ('native-fact', 'owner', 'schema-type'):
            with self.subTest(mutation=mutation):
                data, body = deepcopy(original['data']), deepcopy(original['body'])
                if mutation == 'native-fact':
                    body['discovery'] = dict(model='invented-model')
                elif mutation == 'owner':
                    body['request']['task_id'] = 'another-task'
                    data['request_digest'] = evidence._digest(body['request'])
                else:
                    body['pins']['schema_version'] = 2.0
                data['assignment'] = fixture_artifact(self.scope,self.identity, body)
                owner = original['owner']
                row = (original['id'], *(owner[k] for k in ('scope_id', 'client', 'session_id', 'task_id', 'dispatch_id')),
                       evidence._raw(data).decode())
                with self.assertRaises(state.StateError):
                    self.api._record(self.scope, row)

    def test_worker_check_and_coordinator_mutations_share_the_same_ownership_ledger(self):
        from runtime import evidence
        self.plan()
        worker = self.prepare()
        with patch.object(evidence, '_execute', side_effect=AssertionError('conflicting check executed')):
            with self.assertRaises(state.StateError):
                evidence.run_check(self.scope, self.request(check_id='unit'))
        self.settle_fixture(worker['attempt_id'], status='completed')
        checked = evidence.run_check(self.scope, self.request(check_id='unit'))
        self.assertEqual(checked['attempt']['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.reserve_coordinator(self.scope, self.operation_request(claims=[self.path_claim()]))
        another = self.second_owner(1, [self.path_claim()])
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, another)


class Stage3ModelFreeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_evidence.EvidenceTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.repo = self.fixture.root, self.fixture.repo
        self.scope, self.identity = self.fixture.scope, self.fixture.identity
        for target in (patch.object(delegation, 'guidance_status', side_effect=AssertionError('model-free guidance lookup')),
                patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('model-free native discovery'))):
            target.start()
            self.addCleanup(target.stop)
        self.counter = self.root / 'check-runs.txt'
        self.counter.write_text('')

    def request(self, **fields):
        return self.fixture.request(**fields)

    def task(self):
        with state._database(self.scope) as db:
            return deepcopy(state._task(db, state._scope_id(self.scope), self.identity['client'],
                self.identity['session_id'], self.identity['task_id']))

    def records(self):
        with state._database(self.scope) as db:
            return delegation._ledger(self.scope, db)

    def record(self, attempt_id):
        found = [row for row in self.records() if row['id'] == attempt_id]
        self.assertEqual(len(found), 1, 'Direct D5 attempt must have exactly one same-ID ledger row')
        return found[0]

    def check_plan(self, code='pass', *, mutable=False):
        self.fixture.plan()
        plan = evidence._load_plan(self.scope, self.identity)
        check = plan['checks'][0]
        check['argv'] = [sys.executable, '-c', code]
        if mutable:
            check['effects']['kind'] = 'disposable'
            check['resources'] = [dict(kind='path', key=str(self.counter), mode='write')]
        try:
            evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:fixture-command'))
        except state.StateError as error:
            if mutable and str(error) == 'Invalid task control fields':
                self.fail('Stage3 declared check.resources admission is absent: ' + str(error))
            raise
        return plan

    def operation_request(self, **fields):
        return self.request(dispatch_id='operation-1',
            claims=[dict(kind='path', key=str(self.root / 'coordinator.txt'), mode='write')],
            operation=dict(id='operation-1', kind='coordinator-work'),
            reason='Complete one accepted disposable fixture operation',
            ownership_ref='fixture:authorized-resource-owner', stop_conditions=['Stop after writing the fixture']) | fields

    def reserve_coordinator(self, request):
        public = getattr(delegation, 'reserve_coordinator', None)
        self.assertTrue(callable(public), 'Stage3 public reserve_coordinator is absent; do not substitute the private kernel')
        with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('task-only candidate capture')):
            return public(self.scope, request)

    def private_proof(self, proof):
        raw = json.dumps(proof).encode()
        path = self.root / ('host-' + hashlib.sha256(raw).hexdigest() + '.json')
        if not path.exists():
            path.write_bytes(raw)
            path.chmod(0o400)
        else:
            self.assertEqual(path.read_bytes(), raw)
        return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())

    def coordinator_proof(self, attempt_id, *, write_output=True):
        row, task = self.record(attempt_id), self.task()
        operation = evidence._read(self.scope, row['body']['operation'])
        output = self.root / 'coordinator.txt'
        if write_output:
            output.write_text('accepted operation completed\n')
        bindings = dict(operation_id=operation['id'], operation_digest=row['body']['operation']['digest'],
            claims_digest=evidence._digest(row['data']['claims']))
        observation = ('Fixture synchronous write returned; no descendants; output SHA-256 ' +
            hashlib.sha256(output.read_bytes()).hexdigest()) if write_output else 'Fixture verifier/Git operation and descendants independently inspected stopped'
        observed = fixture_artifact(self.scope,self.identity, bindings | dict(observation=observation))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=self.identity.copy(),
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=attempt_id, dispatch_id=row['owner']['dispatch_id'], assignment_digest=row['data']['assignment']['digest'],
            decision_ref=task['decision_ref'], operation_evidence=observed, resource_observations=observed,
            operation_stopped=True, operation_descendants_stopped=True,
            observation_ref='fixture:completed-synchronous-operation', coordinator_ref='fixture:independent-file-inspection',
            **bindings)
        for key in ('d5_plan', 'd6_plan'):
            if row['body'][key] is not None:
                proof[key + '_digest'] = row['body'][key]['digest']
        if row['data']['candidate'] is not None:
            proof['candidate_digest'] = row['data']['candidate']['digest']
            proof['current_candidate_digest'] = evidence._digest(evidence.capture_candidate(self.scope,
                evidence._read(self.scope, row['body']['d5_plan'])))
        return proof

    def reconcile_operation(self, proof):
        request = self.request(attempt_id=proof['attempt_id'],
            host_proof=self.private_proof(proof), assessment=dict(outcome='completed', changed_paths=[],
                result_assessment='Observed the exact accepted fixture output',
                partial_change_assessment='Only the declared private file changed; synchronous operation returned without descendants',
                evidence=['fixture:independent-file-inspection'], coordinator_ref='fixture:assessed'))
        if self.record(proof['attempt_id'])['data']['candidate'] is None:
            with patch.object(evidence, 'capture_candidate', side_effect=AssertionError('task-only candidate capture')):
                return delegation.reconcile_assignment(self.scope, request)
        return delegation.reconcile_assignment(self.scope, request)

    def test_explicit_check_replay_does_not_repeat_command_or_add_attempt(self):
        # Catches a replay lookup that happens after subprocess execution/reservation.
        code = "from pathlib import Path; p=Path(%r); p.write_text(p.read_text() + 'run\\n')" % str(self.counter)
        self.check_plan(code, mutable=True)
        request = self.request(check_id='unit', dispatch_id='stable-check-1')
        first = evidence.run_check(self.scope, request)
        original, original_rows = self.task(), self.records()
        replay = evidence.run_check(self.scope, request)
        self.assertEqual(self.counter.read_text(), 'run\n')
        self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertEqual(replay['attempt'], first['attempt'])
        self.assertEqual(self.task(), original)
        self.assertEqual(self.records(), original_rows)
        self.assertEqual(len(original['evidence']['attempts']), 1)
        self.record(first['attempt_id'])

    def test_original_legacy_check_request_replays_without_a_new_attempt(self):
        # Catches stale-CAS rejection before deterministic replay for existing callers.
        self.check_plan('import os; print(os.getpid())')
        request = self.request(check_id='unit')
        first = evidence.run_check(self.scope, request)
        original = self.task()
        try:
            replay = evidence.run_check(self.scope, request)
        except state.StateError as error:
            if str(error) == 'Task revision conflict':
                self.fail('Stage3 deterministic legacy replay is absent: ' + str(error))
            raise
        self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertEqual(replay['attempt']['execution'], first['attempt']['execution'])
        self.assertEqual(self.task(), original)
        self.assertEqual(len(original['evidence']['attempts']), 1)

    def test_model_free_check_links_and_settles_actual_failure_without_native_budget(self):
        # Catches invented native admission and loss of actual exit/output during settlement.
        self.check_plan("print('fixture failed'); raise SystemExit(7)")
        result = evidence.run_check(self.scope, self.request(check_id='unit'))
        attempt, task = result['attempt'], self.task()
        self.assertEqual(attempt['exit_code'], 7)
        self.assertEqual(attempt['observed_status'], 'failed')
        self.assertNotIn('delegation', task)
        status = delegation.delegation_status(self.scope, 'codex', 'parent')
        self.assertIsNone(status['budgets'])
        self.assertEqual(status['capacity']['unresolved_managed_workers'], 0)
        row = self.record(result['attempt_id'])
        self.assertEqual(row['data']['role'], 'check')
        self.assertEqual(row['data']['resource_units'], 0)
        self.assertIsNone(row['body']['d6_plan'])
        self.assertIsNone(row['data']['binding'])
        execution = evidence._read(self.scope, attempt['execution'])
        self.assertEqual(evidence._read(self.scope, execution['output'])['text'], 'fixture failed')
        plan = evidence._load_plan(self.scope, self.identity)
        proof = dict(version=2, kind='check-writer-reconciliation', identity=self.identity.copy(),
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=attempt['id'], dispatch_id=row['owner']['dispatch_id'], assignment_digest=row['data']['assignment']['digest'],
            claims_digest=evidence._digest(row['data']['claims']), candidate_digest=attempt['candidate_digest'],
            current_candidate_digest=evidence._digest(evidence.capture_candidate(self.scope, plan)),
            plan_digest=attempt['plan_digest'], execution_digest=attempt['execution']['digest'], stopped=True,
            descendants_stopped=True, observation_ref='fixture:synchronous-child-reaped-no-descendants',
            coordinator_ref='fixture:independent-exit-and-output-inspection')
        settled = evidence.reconcile_check(self.scope, self.request(attempt_id=attempt['id'], host_proof=self.private_proof(proof)))
        self.assertEqual(settled['attempt']['status'], 'failed')
        self.assertEqual(settled['attempt']['exit_code'], 7)
        self.assertEqual(settled['attempt']['execution'], attempt['execution'])
        self.assertEqual(self.record(attempt['id'])['data']['status'], 'settled')
        self.assertEqual(len(self.task()['evidence']['attempts']), 1)
        self.assertNotIn('delegation', self.task())

    def test_declared_mutable_check_resource_blocks_another_check_before_execution(self):
        # Catches a check admission that ignores another unresolved check's mutable resource.
        code = "from pathlib import Path; p=Path(%r); p.write_text(p.read_text() + 'run\\n')" % str(self.counter)
        plan = self.check_plan(code, mutable=True)
        other = self.identity | dict(session_id='other-parent', task_id='other-task')
        state.start_task(self.scope, other | dict(mode='editable', decision_ref='user:accepted'))
        evidence.plan_evidence(self.scope, other | dict(expected_revision=1, plan=plan))
        first = evidence.run_check(self.scope, self.request(check_id='unit'))
        before = self.records()
        with self.assertRaises(state.StateError):
            evidence.run_check(self.scope, other | dict(expected_revision=2, check_id='unit'))
        self.assertEqual(self.counter.read_text(), 'run\n')
        self.assertEqual(self.records(), before)
        self.assertNotEqual(self.record(first['attempt_id'])['data']['status'], 'settled')
        other_task = state.task_status(self.scope, 'codex', 'other-parent')['task']
        self.assertEqual(other_task['evidence']['attempts'], [])

    def test_task_only_coordinator_replay_and_exact_proof_settle_without_candidate(self):
        # Catches fake plan/native requirements and reservation replay consuming ownership twice.
        request = self.operation_request()
        first = self.reserve_coordinator(request)
        original = self.task()
        replay = self.reserve_coordinator(request)
        self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertFalse(replay['reservation_admitted'])
        self.assertNotIn('launch_admitted', first)
        self.assertEqual(self.task(), original)
        row = self.record(first['attempt_id'])
        for key in ('candidate', 'binding', 'plan_digest', 'host_capacity'):
            self.assertIsNone(row['data'][key])
        self.assertEqual(row['data']['resource_units'], 0)
        self.assertNotIn('evidence', original)
        self.assertNotIn('delegation', original)
        proof = self.coordinator_proof(first['attempt_id'])
        settled = self.reconcile_operation(proof)
        self.assertEqual(settled['outcome'], 'completed')
        self.assertEqual(self.record(first['attempt_id'])['data']['status'], 'settled')
        self.assertNotIn('evidence', self.task())
        self.assertNotIn('delegation', self.task())
        self.assertTrue(self.reserve_coordinator(self.operation_request(dispatch_id='operation-2',
            operation=dict(id='operation-2', kind='coordinator-work')))['reservation_admitted'])

    def test_unresolved_exact_check_cannot_execute_again_under_a_new_dispatch(self):
        self.check_plan('pass')
        original = self.request(check_id='unit', dispatch_id='first-check')
        with patch.object(evidence, '_execute', wraps=evidence._execute) as executed:
            first = evidence.run_check(self.scope, original)
            self.assertEqual(evidence.run_check(self.scope, original)['attempt_id'], first['attempt_id'])
            with self.assertRaises(state.StateError):
                evidence.run_check(self.scope, self.request(check_id='unit', dispatch_id='second-check'))
            with self.assertRaises(state.StateError):
                evidence.run_check(self.scope, original | dict(check_id='another-check'))
        self.assertEqual(executed.call_count, 1)
        self.assertEqual(len(self.task()['evidence']['attempts']), 1)

    def test_matching_coordinator_job_retains_original_attempt_limit_without_native_consumption(self):
        self.check_plan()
        request = self.operation_request(operation=dict(id='scoped-operation', kind='coordinator-work'))
        plan = self.fixture.coordinator_resource_plan([dict(id='scoped-operation', role='coordinator', claims=request['claims'],
            dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        request = self.operation_request(operation=dict(id='scoped-operation', kind='coordinator-work'))
        first = self.reserve_coordinator(request)
        self.assertEqual(self.record(first['attempt_id'])['data']['job_id'], 'scoped-operation')
        proof = self.coordinator_proof(first['attempt_id'])
        self.reconcile_operation(proof)
        with self.assertRaises(state.StateError):
            self.reserve_coordinator(self.operation_request(dispatch_id='new-dispatch', operation=request['operation']))
        self.assertEqual(self.task()['delegation']['attempts_consumed'], 0)
        self.assertEqual(self.task()['delegation']['resource_units_consumed'], 0)

    def test_uncertain_verifier_barrier_recovers_without_inventing_ready_or_native_facts(self):
        self.fixture.ready_proof_fixture()
        with patch.object(evidence, 'capture_candidate', side_effect=RuntimeError('fixture verifier crash')):
            with self.assertRaises(RuntimeError):
                self.fixture.verify()
        barrier = next(r for r in self.records() if r['data']['role'] == 'coordinator')
        proof = self.coordinator_proof(barrier['id'], write_output=False)
        with self.assertRaises(state.StateError):
            self.reconcile_operation(proof | dict(operation_descendants_stopped=False))
        self.assertNotEqual(self.record(barrier['id'])['data']['status'], 'settled')
        recovered = self.reconcile_operation(proof)
        self.assertEqual(recovered['outcome'], 'completed')
        self.assertNotEqual(self.task()['state'], 'DONE')
        self.assertNotIn('verification', self.task()['evidence'])
        self.assertNotIn('delegation', self.task())
        self.assertTrue(self.reconcile_operation(proof)['replayed'])
        self.assertTrue(self.fixture.verify()['verified'])

    def test_coordinator_settlement_replay_cannot_change_payload_or_release_again(self):
        first = self.reserve_coordinator(self.operation_request())
        proof = self.coordinator_proof(first['attempt_id'])
        self.reconcile_operation(proof)
        original = self.task()
        self.assertTrue(self.reconcile_operation(proof)['replayed'])
        self.assertEqual(self.task(), original)
        request = self.request(attempt_id=first['attempt_id'], host_proof=self.private_proof(proof), assessment=dict(
            outcome='failed', changed_paths=[], result_assessment='Changed outcome', partial_change_assessment='No extra effects',
            evidence=['fixture:changed'], coordinator_ref='fixture:changed'))
        with self.assertRaises(state.StateError):
            delegation.reconcile_assignment(self.scope, request)
        self.assertEqual(self.task(), original)

    def test_unknown_readonly_check_footprint_reserves_conservative_checkout_scope(self):
        self.check_plan()
        plan = evidence._load_plan(self.scope, self.identity)
        plan['checks'][0]['dependencies'] = []
        evidence.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:unknown-footprint'))
        result = evidence.run_check(self.scope, self.request(check_id='unit'))
        claims = self.record(result['attempt_id'])['data']['claims']
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['path'], str(self.repo))
        self.assertEqual(claims[0]['mode'], 'write')
        self.assertTrue(claims[0]['directory'])

    def test_readonly_task_can_run_readonly_command_with_conservative_exclusive_ownership(self):
        self.check_plan()
        plan = evidence._load_plan(self.scope, self.identity)
        plan['checks'][0]['dependencies'] = []
        readonly = self.identity | dict(session_id='readonly', task_id='readonly-task')
        state.start_task(self.scope, readonly | dict(mode='read-only', decision_ref='user:accepted'))
        evidence.plan_evidence(self.scope, readonly | dict(expected_revision=1, plan=plan))
        result = evidence.run_check(self.scope, readonly | dict(expected_revision=2, check_id='unit'))
        self.assertEqual(result['attempt']['exit_code'], 0)
        self.assertEqual(result['task']['mode'], 'read-only')
        record = self.record(result['attempt_id'])
        self.assertEqual(record['data']['claims'][0]['mode'], 'write')
        self.assertEqual(record['data']['claims'][0]['path'], str(self.repo))
        self.assertIsNone(record['data']['binding'])

    def test_actual_check_diagnostics_survive_unavailable_post_execution_candidate(self):
        self.check_plan("print('actual exit seven'); raise SystemExit(7)")
        original = evidence.capture_candidate
        calls = []
        def capture(*args):
            calls.append(True)
            if len(calls) > 1:
                raise state.StateError('fixture candidate unavailable')
            return original(*args)
        request = self.request(check_id='unit', dispatch_id='diagnostic-check')
        with patch.object(evidence, 'capture_candidate', side_effect=capture):
            result = evidence.run_check(self.scope, request)
        self.assertEqual(result['attempt']['exit_code'], 7)
        execution = evidence._read(self.scope, result['attempt']['execution'])
        self.assertEqual(evidence._read(self.scope, execution['output'])['text'], 'actual exit seven')
        self.assertEqual(self.record(result['attempt_id'])['data']['status'], 'uncertain')
        with patch.object(evidence, '_execute', side_effect=AssertionError('replay executed again')):
            self.assertEqual(evidence.run_check(self.scope, request)['attempt']['execution'], result['attempt']['execution'])

    def test_wrong_operation_or_resource_proof_retains_coordinator_ownership(self):
        # Catches release on a true stop flag attached to a different operation/resource.
        first = self.reserve_coordinator(self.operation_request())
        proof = self.coordinator_proof(first['attempt_id'])
        wrong_resources = evidence._read(self.scope, proof['resource_observations']) | dict(claims_digest='f' * 64)
        changes_to_proof = [dict(operation_id='another-operation'), dict(claims_digest='f' * 64),
            dict(resource_observations=fixture_artifact(self.scope,self.identity, wrong_resources))]
        original, original_row = self.task(), self.record(first['attempt_id'])
        for delta in changes_to_proof:
            with self.subTest(delta=delta):
                with self.assertRaises(state.StateError):
                    self.reconcile_operation(proof | delta)
                self.assertEqual(self.task(), original)
                self.assertEqual(self.record(first['attempt_id']), original_row)
                with self.assertRaises(state.StateError):
                    self.reserve_coordinator(self.operation_request(dispatch_id='conflicting-operation',
                        operation=dict(id='conflicting-operation', kind='coordinator-work')))
        self.assertEqual(self.reconcile_operation(proof)['outcome'], 'completed')


if __name__ == '__main__':
    unittest.main()
