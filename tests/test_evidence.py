"""D5 proofs use disposable Git repositories and explicit mocked native observations."""
from tests.fixture_canonical_birth import fixture_artifact
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import state
from tests.fixture_canonical_birth import native_receipt_fields
from runtime.startup import ProjectScope, RuntimeSettings, policy_digest


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical, save_test_acquisition
        self._canonical_common_git = self._testMethodName in {
            'test_linked_checkout_shared_git_reservation_invalidates_ready_in_same_transaction',
            'test_candidate_binds_modes_untracked_index_base_environment_and_linked_checkout'}
        fixture, self.acquisition, observation = fresh_canonical(self)
        self.root, self.repo, self.scope = fixture.base, fixture.repo, self.acquisition.resolution.scope
        self.test_acquisition = save_test_acquisition(self.root / 'test-acquisition.json', fixture, observation)
        if getattr(self,'_canonical_peer',False) or self._canonical_common_git:
            from types import SimpleNamespace
            from runtime import startup
            peer=next(root for root in fixture.test_observations if root != str(self.repo))
            self.peer_scope=startup._entry_scope(startup.acquire_project_entry(
                SimpleNamespace(client='codex',cwd=Path(peer)),state.PACKAGE,
                qualifier=lambda record,facts:fixture.test_observations[peer]))

        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        (self.repo / 'app.py').write_text('value = 1\n')
        self.git('add', 'app.py')
        self.git('commit', '-qm', 'fixture application')
        if getattr(self,'_canonical_peer',False) or self._canonical_common_git:
            peer=self.peer_scope.repository_root
            (peer/'app.py').write_text('value = 1\n')
            subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-C',str(peer),'add','app.py'],check=True,capture_output=True,timeout=5)
            subprocess.run(['git','-c','core.hooksPath='+os.devnull,'-C',str(peer),'commit','-qm','fixture peer application'],check=True,capture_output=True,timeout=5)

        self.identity = dict(client='codex', session_id='parent', task_id='task', scope_ref='user:task')
        state.start_task(self.scope, self.identity | dict(mode='editable', decision_ref='user:accepted'))
        self.api = importlib.import_module('runtime.evidence')
        from runtime import changes
        guard_patch = patch.object(changes, 'LOCK_ROOT', self.root / 'authority-guards')
        guard_patch.start()
        self.addCleanup(guard_patch.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def request(self, **fields):
        return self.identity | dict(expected_revision=state.task_status(self.scope, 'codex', 'parent')['task']['revision']) | fields

    def plan(self, **updates):
        review = dict(applicability='required', reason='Actual change', independent=True)
        plan = dict(version=1, decision_ref='user:accepted', criteria=[dict(id='works', checks=['unit'], behavior=False)],
            checks=[dict(id='unit', argv=[sys.executable, '-c', 'pass'], cwd='.', timeout=3,
                env={'PATH': os.defpath}, effects=dict(kind='read-only', inspection_ref='code:inspected', network=False,
                    production=False, publish=False, migrations=False), baseline=dict(status='passed', reference='baseline:1'),
                dependencies=['app.py'])], reviews={'general': review, 'security': review},
            security_impact=dict(status='assessed', reason='Local runtime boundary', reference='code:impact'),
            base='HEAD', comparison='endpoint', nontrivial=True, instructions=[], exclusions=[], review_limit=3)
        return self.api.plan_evidence(self.scope, self.request(plan=plan | updates))

    def reconcile(self, result, **proof_updates):
        # Synthetic fixture host observations are explicit; they do not qualify real native execution.
        attempt = result['attempt']
        request = self.request(attempt_id=attempt['id'])
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        plan = self.api._load_plan(self.scope, self.identity)
        proof = dict(version=1, kind='check-writer-reconciliation', identity=self.identity.copy(), expected_pins=task['pins'],
            expected_revision=task['revision'], scope_id=state._scope_id(self.scope), attempt_id=attempt['id'],
            candidate_digest=attempt['candidate_digest'], current_candidate_digest=self.api._digest(self.api.capture_candidate(self.scope, plan)),
            plan_digest=attempt['plan_digest'], execution_digest=attempt.get('execution', {}).get('digest'),
            stopped=True, descendants_stopped=True, observation_ref='fixture:explicit-host-lifecycle-observation',
            coordinator_ref='fixture:independently-checked') | proof_updates
        if 'ledger_assignment_digest' in attempt:
            from runtime import delegation
            with state._database(self.scope) as db:
                row = next(r for r in delegation._ledger(self.scope, db) if r['id'] == attempt['id'])
            proof = proof | dict(version=2, dispatch_id=row['owner']['dispatch_id'],
                assignment_digest=attempt['ledger_assignment_digest'], claims_digest=attempt['ledger_claims_digest']) | proof_updates
        raw = json.dumps(proof).encode()
        path = self.root / (attempt['id'] + '-host-' + str(time.time_ns()) + '.json')
        path.write_bytes(raw)
        path.chmod(0o400)
        return self.api.reconcile_check(self.scope, request | dict(host_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())))

    def check(self):
        return self.api.run_check(self.scope, self.request(check_id='unit'))

    def check_clock(self, ready):
        """Check timeouts expire on a fake clock once ``ready()`` holds, never from host slowness."""
        import selectors
        from contextlib import ExitStack
        from types import SimpleNamespace
        clock = SimpleNamespace(now=0.0)

        class Time:
            def __getattr__(self, name): return getattr(time, name)
            def monotonic(self): return clock.now

        class Selector(selectors.DefaultSelector):
            def select(self, timeout=None):
                until = time.monotonic() + 60
                while True:
                    events = super().select(.05)
                    if events: return events
                    if ready() or time.monotonic() >= until:
                        clock.now += timeout
                        return []

        stack = ExitStack()
        stack.enter_context(patch.object(self.api, 'time', Time()))
        stack.enter_context(patch.object(self.api, 'selectors', SimpleNamespace(DefaultSelector=Selector,
            EVENT_READ=selectors.EVENT_READ, EVENT_WRITE=selectors.EVENT_WRITE)))
        return stack

    def checked(self):
        result = self.check()
        if result['attempt'].get('process_started') and result['attempt'].get('observed_status') != 'uncertain':
            result = self.reconcile(result)
        return result

    def test_executor_launch_failure_releases_check_without_handwritten_stop_proof(self):
        from runtime import delegation
        self.plan()
        execute = self.api._execute
        def fail_launch(scope, check, tool):
            with patch.object(self.api.subprocess, 'Popen', side_effect=OSError('launch unavailable')):
                return execute(scope, check, tool)
        with patch.object(self.api, '_execute', side_effect=fail_launch):
            result = self.check()
        self.assertEqual(result['attempt']['status'], 'not-run')
        self.assertFalse(result['attempt']['process_started'])
        with state._database(self.scope) as db:
            record = next(row for row in delegation._ledger(self.scope, db) if row['id'] == result['attempt_id'])
        self.assertEqual(record['data']['status'], 'settled')
        self.assertEqual(record['data']['result']['outcome'], 'failed')

    def test_artifact_publication_does_not_scan_unrelated_retention_history(self):
        self.plan()
        with patch.object(self.api, '_cleanup_closed_details', side_effect=AssertionError('Unrelated cleanup scan')):
            reference = fixture_artifact(self.scope, self.identity, dict(observation='bounded fixture'))
        self.assertEqual(self.api._read(self.scope, reference), dict(observation='bounded fixture'))

    def test_completion_reuses_one_ownership_observation_in_its_transaction(self):
        from runtime import delegation
        self.plan()
        plan = self.api._load_plan(self.scope, self.identity)
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        candidate = self.api.capture_candidate(self.scope, plan)
        with patch.object(delegation, '_ledger', wraps=delegation._ledger) as ledger:
            blockers = self.api._blockers(self.scope, task, plan, candidate, 'completion', [])
        self.assertTrue(blockers)
        self.assertEqual(ledger.call_count, 1)

    def native_review_request(self, *, coverage, criteria, assignment):
        from runtime import delegation
        if not hasattr(self, 'native_review_catalog'):
            self.native_review_catalog = dict(version=1, client_surface='codex-cli', native_version='0.163.0',
                parent_session_id='parent', originator='fixture-cli', parent_model_provider='native-provider',
                catalog=[dict(model='fixture-review-model', supported_efforts=['high'], default_effort='high')],
                dispatch_controls=dict(spawn=True, explicit_model=True, explicit_effort=True, fork_turns_none=True),
                capability_digest='a' * 64, limitations=['Synthetic native fixture; no actual native call'])
            self.native_review_cache = dict(client_surface='codex-cli', applicability=dict(client_version='0.163.0', capability_fingerprint='a' * 64))
            claim = delegation.claim_guidance(self.scope, self.native_review_cache | dict(trigger=dict(kind='routine', id='fixture-review')))
            if claim.get('token'):
                guidance = dict(schema_version=1, client_surface='codex-cli', checked_at='2026-01-01T00:00:00Z',
                    applicability=self.native_review_cache['applicability'], sources=[dict(id='official',
                        url='https://example.org/official', sha256='b' * 64)], recommendations=[dict(id='review-fixture',
                        work='Fixture native review', risk='Read-only fixture', models=[], conditions=['Independent review'],
                        limitations=['Synthetic catalog'], source_ids=['official'])])
                delegation.record_guidance(self.scope, self.native_review_cache | dict(token=claim['token'],
                    generation=claim['generation'], coordinator_ref='fixture:verified', guidance=guidance))
            discover = patch('runtime.adapters.codex.discover_native_workers', return_value=self.native_review_catalog)
            discover.start(); self.addCleanup(discover.stop)
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        if 'delegation' not in task:
            plan = self.coordinator_resource_plan([dict(id='review', role='review', claims=[dict(kind='path',
                key=str(self.repo), mode='read')], dependencies=[], max_attempts=3)])
            plan['limits'].update(max_attempts=3, max_resource_units=3, max_parallel_workers=3, host_capacity=3)
            delegation.plan_delegation(self.scope, self.request(plan=plan))
        with state._database(self.scope) as db:
            records = [r for r in delegation._ledger(self.scope, db) if r['owner']['session_id'] == 'parent' and
                r['owner']['task_id'] == self.identity['task_id'] and r['data']['role'] == 'review']
        guide = delegation.guidance_status(self.scope, self.native_review_cache)
        request = self.request(coverage=coverage, criteria=criteria, assignment=assignment, job_id='review',
            dispatch_id='review-' + str(len(records)), resource_units=1,
            selection=dict(model='fixture-review-model', effort='high', rationale='Explicit qualified fixture model',
                profile_ids=['review-fixture'], mode='explicit'),
            control_observation={key:self.native_review_catalog[key] for key in ('version', 'client_surface',
                'native_version', 'parent_session_id', 'dispatch_controls')},
            discovery_expectation={key:self.native_review_catalog[key] for key in ('client_surface', 'native_version',
                'parent_session_id', 'parent_model_provider', 'capability_digest')},
            guidance={key:guide[key] for key in ('snapshot_ref', 'snapshot_digest', 'checked_at', 'freshness')})
        if records:
            previous = max(records, key=lambda r:r['data']['revision'])
            request.update(retry_of=previous['id'], diagnostic=dict(prior_attempt_id=previous['id'],
                cause='Review after previous independently settled round', change='Fresh bounded review',
                evidence=['fixture:diagnosis-' + previous['id']]))
        return request

    def reconcile_review(self, prepared, activity=None):
        from runtime import delegation
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        with state._database(self.scope) as db:
            row = next(r for r in delegation._ledger(self.scope, db) if r['id'] == prepared['attempt_id'])
        data, binding = row['data'], row['data']['binding']
        receipt = self.api._read(self.scope, data['observation'])['receipt']
        native = {key:binding[key] for key in ('client_surface', 'native_version', 'parent_session_id', 'task_name')}
        native.update({key:receipt[key] for key in ('parent_turn_id', 'spawn_item_id', 'worker_id', 'worker_turn_id', 'result_item_id')})
        proof = dict(version=2, kind='assignment-writer-reconciliation', identity=self.identity.copy(), expected_pins=task['pins'],
            expected_revision=task['revision'], scope_id=state._scope_id(self.scope), attempt_id=row['id'],
            dispatch_id=row['owner']['dispatch_id'], assignment_digest=data['assignment']['digest'], claims_digest=self.api._digest(data['claims']),
            candidate_digest=data['candidate']['digest'], current_candidate_digest=self.api._digest(self.api.capture_candidate(self.scope,
                self.api._read(self.scope, row['body']['review']['d5_plan']))), d6_plan_digest=data['plan_digest'],
            observation_digest=data['observation']['digest'], result_digest=receipt['result_digest'], native_identity=native,
            launch_state='observed', stopped=True, descendants_stopped=True, observation_ref='fixture:host-lifecycle-checked',
            coordinator_ref='fixture:independent-host-inspection')
        if activity is not None:
            del proof['stopped'], proof['descendants_stopped']
            proof.update(version=3, kind='native-cooperative-completion', activity_digest=self.api._digest(activity),
                         no_outstanding_coordinator_followups=True, coordinator_submissions_paused=True,
                         observation_ref='fixture:effect-inspection',
                         coordinator_ref='fixture:independent-assessment')
        raw=json.dumps(proof).encode(); path=self.root/('review-proof-'+row['id']+'.json');path.write_bytes(raw);path.chmod(0o400)
        return delegation.reconcile_assignment(self.scope, self.request(attempt_id=row['id'], host_proof=dict(path=str(path),
            sha256=hashlib.sha256(raw).hexdigest()), assessment=dict(outcome='completed', changed_paths=[],
            result_assessment='Fixture report independently checked', partial_change_assessment='No candidate/shared-resource effects',
            evidence=['fixture:effect-inspection'], coordinator_ref='fixture:independent-assessment')))

    def review(self, findings=None, coverage=None, *, cooperative=False):
        from runtime import delegation
        coverage = coverage or ['general', 'security']
        request = self.native_review_request(coverage=coverage, criteria=['works'],
            assignment='Review read-only; inspect app.py and callers. Return the bound report.')
        prepared = self.api.prepare_review(self.scope, request)
        attempt = prepared['attempt_id']
        binding = delegation.prepared_native_review_assignment(self.scope, attempt)
        report = dict(version=1, attempt_id=attempt, assignment_digest=prepared['assignment_digest'],
            candidate_digest=prepared['candidate_digest'], coverage=coverage, findings=findings or [], limitations=[])
        raw = json.dumps(report)
        actual = dict(version=2, mechanism='codex-native-agent-path', **native_receipt_fields(binding), agent_path='/root/'+binding['task_name'],
            originator='codex_exec', parent_model_provider=None, worker_model_provider=None, parent_turn_id='turn', spawn_item_id='spawn',
            worker_id='reviewer', worker_turn_id='child-turn', result_item_id='final', turn_status='completed', checkout=str(self.repo),
            configured_model='fixture-review-model', configured_effort='high', spawn_observed_model='fixture-review-model',
            spawn_observed_effort='high', execution_model=None, execution_effort=None, descendants_stopped=False,
            prompt_delivery='unverified', result_digest=hashlib.sha256(raw.encode()).hexdigest())
        activity = dict(source_digest='a' * 64, calls=[]) if cooperative else None
        observation = dict(receipt=actual, result=raw)
        observation_request = self.request(attempt_id=attempt, parent_turn_id='turn', spawn_item_id='spawn')
        if cooperative:
            observation['activity'] = activity
            observation_request['cooperative'] = True
        with patch('runtime.adapters.codex.observe_native_review_assignment', return_value=observation):
            observed = delegation.observe_assignment(self.scope, observation_request)
            self.assertEqual(observed['status'],'observed',observed)
        receipt = dict(version=1, mechanism='codex-native-agent-path', native_version='0.163.0', parent_session_id='parent',
            parent_turn_id='turn', spawn_item_id='spawn', reviewer_id='reviewer', review_turn_id='child-turn',
            report_item_id='final', assignment_digest=prepared['assignment_digest'], candidate_digest=prepared['candidate_digest'],
            report_digest=actual['result_digest'], requested_model='fixture-review-model', requested_effort='high',
            task_name=binding['task_name'],agent_path=actual['agent_path'])
        with patch('runtime.adapters.codex.collect_native_review', return_value={'receipt': receipt, 'report': report}, create=True):
            self.api.collect_review(self.scope, self.request(attempt_id=attempt, parent_turn_id='turn', spawn_item_id='spawn'))
        assessed = self.api.assess_review(self.scope, self.request(attempt_id=attempt, assessment=dict(coverage=coverage,
            criteria=['works'], qualification='Qualified in runtime/security review', independence='Different observed native child',
            semantic_coverage='Inspected requirements, code and failure paths', dispositions=[], decision_refs=['user:accepted'])))
        with patch('runtime.adapters.codex.observe_native_review_assignment', return_value=observation):
            self.reconcile_review(prepared, activity)
        return assessed

    def verify(self, **fields):
        return self.api.verify_completion(self.scope, self.request(purpose='completion', **fields))

    def ready_proof_fixture(self):
        reviews = {name:dict(applicability='inapplicable', reason='Trivial model-free fixture', independent=False)
            for name in ('general', 'security')}
        self.plan(nontrivial=False, reviews=reviews)
        self.checked()

    def readiness_owner(self, session='other-parent', claims=None, *, scope=None, identity=None, dispatch='operation-1'):
        scope = scope or self.scope
        identity = identity or self.identity | dict(session_id=session, task_id=session + '-task')
        with state._database(scope) as db:
            task = state._task(db, state._scope_id(scope), identity['client'], identity['session_id'])
        if task is None:
            state.start_task(scope, identity | dict(mode='editable', decision_ref='user:accepted'))
            with state._database(scope) as db:
                task = state._task(db, state._scope_id(scope), identity['client'], identity['session_id'])
        return identity | dict(expected_revision=task['revision'], dispatch_id=dispatch,
            claims=claims or [dict(kind='path', key=str(self.repo / 'app.py'), mode='write')],
            operation=dict(id=dispatch, kind='coordinator-work'), reason='Explicit fixture work',
            ownership_ref='fixture:independent-owner', stop_conditions=['Stop after fixture operation'])

    def test_writer_first_prevents_verification_without_model_guidance(self):
        from runtime import delegation
        self.ready_proof_fixture()
        writer = delegation._reserve_operation(self.scope, self.readiness_owner(), role='coordinator')
        with patch.object(delegation, 'guidance_status', side_effect=AssertionError('model-free verify queried guidance')):
            result = self.verify()
        self.assertFalse(result['verified'])
        self.assertNotEqual(result['task']['state'], 'DONE')
        with state._database(self.scope) as db:
            self.assertEqual(next(r for r in delegation._ledger(self.scope, db) if r['id'] == writer['attempt_id'])['data']['status'], 'prepared')
        self.assertNotIn('delegation', result['task'])

    def test_verification_barrier_prevents_writer_during_capture(self):
        from runtime import delegation
        self.ready_proof_fixture()
        writer = self.readiness_owner()
        original = self.api.capture_candidate
        attempts = []
        def capture(scope, plan):
            if not attempts:
                attempts.append(True)
                with self.assertRaises(state.StateError):
                    delegation._reserve_operation(scope, writer, role='coordinator')
            return original(scope, plan)
        with patch.object(self.api, 'capture_candidate', side_effect=capture):
            result = self.verify()
        self.assertTrue(result['verified'])
        with state._database(self.scope) as db:
            records = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'coordinator']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['data']['status'], 'settled')
        operation = self.api._read(self.scope, records[0]['body']['operation'])
        self.assertEqual(operation['kind'], 'completion-verification')
        self.assertTrue(all(c['mode'] == 'read' for c in records[0]['data']['claims']))
        self.assertEqual(records[0]['data']['resource_units'], 0)
        self.assertIsNone(records[0]['data']['binding'])
        verification = self.api._read(self.scope, result['task']['evidence']['verification'])
        self.assertEqual(verification['resource_closure'], records[0]['data']['claims'])

    def test_reservation_atomically_invalidates_foreign_ready_once_and_replay_is_noop(self):
        from runtime import delegation
        self.ready_proof_fixture()
        ready = self.verify()['task']
        writer = self.readiness_owner()
        first = delegation._reserve_operation(self.scope, writer, role='coordinator')
        with state._database(self.scope) as db:
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(current['state'], 'REVIEW')
        self.assertEqual(current['revision'], ready['revision'] + 1)
        self.assertTrue(delegation._reserve_operation(self.scope, writer, role='coordinator')['replayed'])
        failed = self.readiness_owner(session='third-parent')
        with self.assertRaises(state.StateError):
            delegation._reserve_operation(self.scope, failed, role='coordinator')
        with state._database(self.scope) as db:
            self.assertEqual(state._task(db, state._scope_id(self.scope), 'codex', 'parent'), current)
            owner = state._task(db, state._scope_id(self.scope), 'codex', 'other-parent')
        self.assertEqual(owner['revision'], first['revision'])

    def test_crashed_verifier_keeps_read_claim_and_cannot_be_bypassed_by_another_verifier(self):
        from runtime import delegation
        self.ready_proof_fixture()
        with patch.object(self.api, 'capture_candidate', side_effect=RuntimeError('fixture crash after barrier')):
            with self.assertRaises(RuntimeError):
                self.verify()
        with state._database(self.scope) as db:
            retained = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'coordinator']
        self.assertEqual(len(retained), 1)
        self.assertNotEqual(retained[0]['data']['status'], 'settled')
        before = retained[0]['data']
        retry = self.verify()
        self.assertFalse(retry['verified'])
        self.assertNotEqual(retry['task']['state'], 'DONE')
        with state._database(self.scope) as db:
            self.assertEqual(next(r for r in delegation._ledger(self.scope, db) if r['id'] == retained[0]['id'])['data'], before)
        with self.assertRaises(state.StateError):
            delegation._reserve_operation(self.scope, self.readiness_owner(), role='coordinator')
        current = state.task_status(self.scope, 'codex', 'parent')['task']
        with self.assertRaises(state.StateError):
            state.start_task(self.scope, self.identity | dict(task_id='replacement', mode='editable', decision_ref='user:accepted',
                replaces=dict(task_id='task', expected_revision=current['revision'])))

    def test_known_failed_verification_settles_only_own_read_barrier_and_same_task_reader_blocks(self):
        from runtime import delegation
        self.plan()
        unrelated = delegation._reserve_operation(self.scope, self.readiness_owner(claims=[
            dict(kind='service', key='fixture:independent', host='local', mode='read')]), role='coordinator')
        failed = self.verify()
        self.assertFalse(failed['verified'])
        with state._database(self.scope) as db:
            records = delegation._ledger(self.scope, db)
        self.assertEqual(next(r for r in records if r['id'] == unrelated['attempt_id'])['data']['status'], 'prepared')
        verifier = next(r for r in records if r['id'] != unrelated['attempt_id'])
        self.assertEqual(verifier['data']['status'], 'settled')
        self.assertEqual(verifier['data']['result']['outcome'], 'failed')
        reader = self.readiness_owner(identity=self.identity, dispatch='same-task-reader', claims=[
            dict(kind='path', key=str(self.repo / 'app.py'), mode='read')])
        delegation._reserve_operation(self.scope, reader, role='coordinator')
        result = self.verify()
        self.assertFalse(result['verified'])
        self.assertTrue(any('ownership' in item for item in result['blockers']))
        with state._database(self.scope) as db:
            self.assertEqual(len(delegation._ledger(self.scope, db)), 3)
        self.assertNotIn('delegation', result['task'])

    def coordinator_resource_plan(self, jobs):
        return dict(version=1, route=dict(mechanism='codex-native-subagent', client_surface='codex-cli', parent_session_id='parent',
            authority_ref='fixture:accepted-route', controls_ref='fixture:known-controls'),
            authority=dict(state_dir=str(self.scope.runtime.state_dir), participants_ref='fixture:shared-owners', ownership_known=True),
            selection_constraints=dict(complexity='Fixture', risk='Bounded', rationale='Explicit fixture plan', required_controls=['spawn']),
            limits=dict(max_attempts=1, max_resource_units=1, deadline=int(time.time()) + 600, max_parallel_workers=1,
                host_capacity=1, capacity_ref='fixture:capacity'), fallback=dict(action='stop', reference='user:accepted'),
            jobs=jobs)

    def test_ready_shared_service_closure_preserves_independence_but_status_blocks_overlapping_reader(self):
        from runtime import delegation
        self.ready_proof_fixture()
        service = dict(kind='service', key='fixture:used-db', host='local', mode='write')
        plan = self.coordinator_resource_plan([dict(id='coordinator-service', role='coordinator', claims=[service], dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        independent = self.readiness_owner(claims=[service | dict(key='fixture:independent-db')])
        delegation._reserve_operation(self.scope, independent, role='coordinator')
        ready = self.verify()['task']
        self.assertEqual(ready['state'], 'DONE')
        verification = self.api._read(self.scope, ready['evidence']['verification'])
        self.assertIn(service | dict(mode='read'), verification['resource_closure'])
        self.assertFalse(self.api.evidence_status(self.scope, 'codex', 'parent')['evidence']['blockers'])
        reader = self.readiness_owner(session='reader', claims=[service | dict(mode='read')])
        delegation._reserve_operation(self.scope, reader, role='coordinator')
        with state._database(self.scope) as db:
            before_status = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(before_status['state'], 'DONE')
        self.assertEqual(before_status['revision'], ready['revision'])
        self.api.revalidate_ready(self.scope, 'codex', 'parent')
        with state._database(self.scope) as db:
            after = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(after['state'], 'REVIEW')
        self.assertEqual(after['revision'], ready['revision'] + 1)

    def test_evidence_status_recaptures_file_edit_after_readiness_capture(self):
        self.ready_proof_fixture()
        ready = self.verify()['task']
        verified_digest = self.api._read(self.scope, ready['evidence']['verification'])['candidate_digest']
        original = self.api._blockers
        edited = []
        def blockers(*args, **kwargs):
            result = original(*args, **kwargs)
            if not edited:
                (self.repo / 'app.py').write_text('value = 2\n')
                edited.append(True)
            return result
        with patch.object(self.api, '_blockers', side_effect=blockers):
            status = self.api.evidence_status(self.scope, 'codex', 'parent')
        self.assertTrue(edited)
        self.assertNotEqual(status['evidence']['candidate_digest'], verified_digest)
        self.assertTrue(status['evidence']['blockers'])
        current = self.api.capture_candidate(self.scope, self.api._load_plan(self.scope, self.identity))
        self.assertEqual(status['evidence']['candidate_digest'], self.api._digest(current))

    def test_status_uses_newly_accepted_d6_resources_when_candidate_is_unchanged(self):
        from runtime import delegation
        self.ready_proof_fixture()
        ready = self.verify()['task']
        original = self.api._read(self.scope, ready['evidence']['verification'])
        dependency = dict(kind='service', key='fixture:new-dependency', host='local', mode='read')
        plan = self.coordinator_resource_plan([dict(id='new-dependency', role='coordinator', claims=[dependency],
            dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        owner = delegation._reserve_operation(self.scope, self.readiness_owner(claims=[dependency]), role='coordinator')
        status = self.api.evidence_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['evidence']['candidate_digest'], original['candidate_digest'])
        self.assertTrue(any(owner['attempt_id'] in blocker for blocker in status['evidence']['blockers']))
        verified = self.verify()
        self.assertFalse(verified['verified'])
        self.assertEqual(status['evidence']['blockers'], verified['blockers'])

    def test_first_delegation_plan_invalidates_ready_once_and_reuse_preserves_fresh_ready(self):
        from runtime import delegation
        self.ready_proof_fixture()
        ready = self.verify()['task']
        before = json.loads(json.dumps(ready))
        dependency = dict(kind='service', key='fixture:first-plan-dependency', host='local', mode='read')
        plan = self.coordinator_resource_plan([dict(id='dependency', role='coordinator', claims=[dependency],
            dependencies=[], max_attempts=1)])
        request = self.request(plan=plan)
        accepted = delegation.plan_delegation(self.scope, request)
        with state._database(self.scope) as db:
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(current['state'], 'REVIEW')
        self.assertEqual(current['revision'], before['revision'] + 1)
        self.assertEqual(accepted['revision'], current['revision'])
        self.assertEqual(current['pins'], before['pins'])
        self.assertEqual(current['evidence'], before['evidence'])
        self.assertEqual(current['delegation']['attempts_consumed'], 0)
        self.assertEqual(current['delegation']['resource_units_consumed'], 0)
        with self.assertRaisesRegex(state.StateError, 'revision conflict'):
            delegation.plan_delegation(self.scope, request)
        reuse = delegation.plan_delegation(self.scope, self.request(plan=plan))
        self.assertEqual(reuse, accepted)
        altered = json.loads(json.dumps(plan))
        altered['limits']['max_attempts'] += 1
        with self.assertRaisesRegex(state.StateError, 'immutable'):
            delegation.plan_delegation(self.scope, self.request(plan=altered))
        renewed = self.verify()
        self.assertTrue(renewed['verified'])
        fresh = renewed['task']
        proof = self.api._read(self.scope, fresh['evidence']['verification'])
        self.assertIn(dependency, proof['resource_closure'])
        reuse = delegation.plan_delegation(self.scope, self.request(plan=plan))
        self.assertEqual(reuse['revision'], fresh['revision'])
        with state._database(self.scope) as db:
            unchanged = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(unchanged, fresh)
        writer = delegation.reserve_coordinator(self.scope,
            self.readiness_owner(claims=[dependency | dict(mode='write')]))
        with state._database(self.scope) as db:
            invalidated = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(invalidated['state'], 'REVIEW')
        self.assertEqual(invalidated['revision'], fresh['revision'] + 1)
        self.assertEqual(writer['status'], 'prepared')

    def test_first_plan_dependency_reader_blocks_status_after_fresh_verification(self):
        from runtime import delegation
        self.ready_proof_fixture()
        self.assertTrue(self.verify()['verified'])
        dependency = dict(kind='service', key='fixture:first-plan-reader', host='local', mode='read')
        plan = self.coordinator_resource_plan([dict(id='dependency', role='coordinator', claims=[dependency],
            dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        with state._database(self.scope) as db:
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(current['state'], 'REVIEW')
        self.assertTrue(self.verify()['verified'])
        owner = delegation.reserve_coordinator(self.scope, self.readiness_owner(claims=[dependency]))
        status = state.task_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['task']['state'], 'REVIEW')
        blockers = self.api.evidence_status(self.scope, 'codex', 'parent')['evidence']['blockers']
        self.assertTrue(any(owner['attempt_id'] in item for item in blockers))

    def test_linked_checkout_shared_git_reservation_invalidates_ready_in_same_transaction(self):
        from runtime import delegation
        self.ready_proof_fixture()
        ready = self.verify()['task']
        foreign = self.peer_scope
        self.assertEqual(foreign.git_common_dir,self.scope.git_common_dir)
        self.assertEqual(foreign.repository_id,self.scope.repository_id)
        self.assertNotEqual(foreign.checkout_id,self.scope.checkout_id)
        request = self.readiness_owner(scope=foreign, claims=[dict(kind='git', key=str(self.scope.git_common_dir), mode='write')])
        delegation._reserve_operation(foreign, request, role='coordinator')
        with state._database(self.scope) as db:
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(current['state'], 'REVIEW')
        self.assertEqual(current['revision'], ready['revision'] + 1)

    def test_stale_verification_generation_retains_exact_barrier_and_cannot_publish_ready(self):
        from runtime import delegation
        self.ready_proof_fixture()
        original = self.api.capture_candidate
        def capture(scope, plan):
            candidate = original(scope, plan)
            with state._database(scope, write=True) as db:
                db.execute('update checkouts set generation=generation+1 where scope_id=?', (state._scope_id(scope),))
            return candidate
        with patch.object(self.api, 'capture_candidate', side_effect=capture), self.assertRaises(state.StateError):
            self.verify()
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
            records = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'coordinator']
        self.assertNotEqual(task['state'], 'DONE')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['data']['status'], 'prepared')
        self.assertIsNone(records[0]['data']['reconciliation'])

    def test_ready_status_final_scan_sees_reader_admitted_after_analysis(self):
        from runtime import delegation
        self.ready_proof_fixture()
        ready = self.verify()['task']
        reader = self.readiness_owner(claims=[dict(kind='path', key=str(self.repo / 'app.py'), mode='read')])
        original = self.api._blockers
        admitted = []
        def blockers(*args, **kwargs):
            result = original(*args, **kwargs)
            if not admitted:
                admitted.append(delegation._reserve_operation(self.scope, reader, role='coordinator'))
            return result
        with patch.object(self.api, '_blockers', side_effect=blockers):
            self.api.revalidate_ready(self.scope, 'codex', 'parent')
        with state._database(self.scope) as db:
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(current['state'], 'REVIEW')
        self.assertEqual(current['revision'], ready['revision'] + 1)

    def test_missing_ready_resource_closure_cannot_establish_independence(self):
        from runtime import delegation
        self.ready_proof_fixture()
        ready = self.verify()['task']
        verification = self.api._read(self.scope, ready['evidence']['verification'])
        verification.pop('resource_closure', None)
        with state._database(self.scope, write=True) as db:
            ready['evidence']['verification'] = fixture_artifact(self.scope,self.identity, verification,db=db)
            state._save(db, state._scope_id(self.scope), ready)
        independent = self.readiness_owner(claims=[dict(kind='service', key='fixture:independent', host='local', mode='read')])
        delegation._reserve_operation(self.scope, independent, role='coordinator')
        with state._database(self.scope) as db:
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(current['state'], 'REVIEW')
        self.assertEqual(current['revision'], ready['revision'] + 1)

    def test_outside_readonly_hardlink_owner_remains_relevant_to_directory_closure(self):
        from runtime import delegation
        self.ready_proof_fixture()
        outside = self.root / 'outside-alias.py'
        os.link(self.repo / 'app.py', outside)
        reader = self.readiness_owner(claims=[dict(kind='path', key=str(outside), mode='read')])
        delegation._reserve_operation(self.scope, reader, role='coordinator')
        result = self.verify()
        self.assertFalse(result['verified'])
        self.assertNotEqual(result['task']['state'], 'DONE')
        self.assertTrue(any('ownership' in item for item in result['blockers']))

    def test_verifier_deduplicates_accepted_root_alias_without_relaxing_external_claims(self):
        from runtime import delegation
        self.ready_proof_fixture()
        root = dict(kind='path', key=str(self.repo), mode='read')
        alias = root | dict(key=str(self.repo) + '/')
        plan = self.coordinator_resource_plan([dict(id='coordinator-root', role='coordinator', claims=[alias], dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        result = self.verify()
        self.assertTrue(result['verified'])
        verification = self.api._read(self.scope, result['task']['evidence']['verification'])
        self.assertEqual(sum(c['kind'] == 'path' and c.get('path') == str(self.repo) for c in verification['resource_closure']), 1)
        with self.assertRaises(state.StateError):
            delegation._claims([root, alias])

    def test_verifier_preserves_all_64_accepted_instruction_paths(self):
        from runtime import delegation
        paths = []
        prefix = self.root
        for depth in range(8):
            prefix = prefix / ('layer-' + str(depth) + '-' + 'x' * 92)
            prefix.mkdir()
        for number in range(64):
            directory = prefix / ('instruction-' + str(number))
            directory.mkdir()
            path = directory / 'AGENTS.md'
            path.write_text('Fixture instruction input\n')
            paths.append(str(path))
        self.plan(instructions=paths)
        result = self.verify()
        self.assertFalse(result['verified'])
        verification = self.api._read(self.scope, result['task']['evidence']['verification'])
        self.assertTrue(set(paths) <= {c.get('path') for c in verification['resource_closure']})
        self.assertGreater(len(verification['resource_closure']), 64)
        with state._database(self.scope) as db:
            self.assertEqual(delegation._ledger(self.scope, db)[0]['data']['status'], 'settled')
            raw = db.execute('select data from assignments').fetchone()[0]
            self.assertGreater(len(raw.encode()), 128 * 1024)

    def test_verifier_preserves_maximum_accepted_d6_job_resource_union(self):
        from runtime import delegation
        self.ready_proof_fixture()
        jobs = [dict(id='j' + str(job), role='coordinator', dependencies=[], max_attempts=1,
            claims=[dict(kind='service', key='s' + format(job * 64 + item, 'x'), host='local', mode='read') for item in range(64)])
            for job in range(32)]
        plan = self.coordinator_resource_plan(jobs)
        self.assertLessEqual(len(self.api._raw(plan)), 128 * 1024)
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        result = self.verify()
        self.assertTrue(result['verified'])
        expected = {claim['key'] for job in jobs for claim in job['claims']}
        verification = self.api._read(self.scope, result['task']['evidence']['verification'])
        self.assertEqual({c['key'] for c in verification['resource_closure'] if c['kind'] == 'service'}, expected)
        self.assertGreater(len(verification['resource_closure']), 32 * 64)
        with state._database(self.scope) as db:
            rows = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'coordinator']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['data']['status'], 'settled')

    def test_external_operations_keep_individual_claim_and_record_byte_limits(self):
        from runtime import delegation
        too_many = self.readiness_owner(claims=[dict(kind='service', key='s' + str(i), host='local', mode='read') for i in range(65)])
        with self.assertRaises(state.StateError):
            delegation._reserve_operation(self.scope, too_many, role='coordinator')
        request = self.readiness_owner()
        admitted = delegation._reserve_operation(self.scope, request, role='coordinator')
        with state._database(self.scope) as db:
            record = next(r for r in delegation._ledger(self.scope, db) if r['id'] == admitted['attempt_id'])
        data = dict(record['data'], result=dict(padding='x' * (128 * 1024)))
        owner = record['owner']
        row = (record['id'], *(owner[k] for k in ('scope_id', 'client', 'session_id', 'task_id', 'dispatch_id')),
               self.api._raw(data).decode())
        with self.assertRaises(state.StateError):
            delegation._record(self.scope, row)

    def test_final_commit_sees_another_session_reservation_after_blocker_scan(self):
        self.plan()
        self.checked()
        self.review()
        plan = self.api._load_plan(self.scope, self.identity)
        other = self.identity | dict(session_id='other-parent', task_id='other-task')
        state.start_task(self.scope, other | dict(mode='editable', decision_ref='user:accepted'))
        self.api.plan_evidence(self.scope, other | dict(expected_revision=1, plan=plan))
        original = self.api._blockers
        inserted = []
        def blockers(*args, **kwargs):
            result = original(*args, **kwargs)
            if not inserted:
                from runtime import delegation
                owner = self.readiness_owner(identity=other, claims=[dict(kind='path', key=str(self.repo / 'app.py'), mode='read')])
                prepared = delegation.reserve_coordinator(self.scope, owner)
                inserted.append(prepared['attempt_id'])
            return result
        with patch.object(self.api, '_blockers', side_effect=blockers):
            result = self.verify()
        self.assertFalse(result['verified'])
        self.assertNotEqual(result['task']['state'], 'DONE')
        self.assertTrue(any(inserted[0] in b for b in result['blockers']))

    def test_detached_writer_cannot_pass_without_exact_current_host_reconciliation(self):
        applicability = dict(applicability='inapplicable', reason='Trivial local synthetic fixture', independent=False)
        self.plan(nontrivial=False, reviews={'general': applicability, 'security': applicability})
        plan = self.api._load_plan(self.scope, self.identity)
        # The detached writer waits for the test's release instead of a fixed delay.
        release = self.root / 'release-detached-writer'
        self.addCleanup(release.touch)
        child = ("import time; from pathlib import Path\n"
                 "until = time.monotonic() + 60\n"
                 "while not Path(%r).exists() and time.monotonic() < until: time.sleep(.01)\n"
                 "Path('app.py').write_text('late detached write')" % str(release))
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r], start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)" % child
        plan['checks'][0]['argv'] = [sys.executable, '-c', parent]
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:detached-fixture'))
        result = self.api.run_check(self.scope, self.request(check_id='unit'))
        self.assertEqual(result['attempt']['status'], 'uncertain')
        self.assertEqual(result['attempt']['observed_status'], 'passed')
        self.assertFalse(self.verify()['verified'])
        release.touch()
        until = time.monotonic() + 30
        while (self.repo / 'app.py').read_text() != 'late detached write' and time.monotonic() < until:
            time.sleep(.01)
        self.assertEqual((self.repo / 'app.py').read_text(), 'late detached write')

    def test_reconciliation_rejects_bare_assertions_and_mismatched_bindings(self):
        self.plan()
        result = self.api.run_check(self.scope, self.request(check_id='unit'))
        self.assertEqual(result['attempt']['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.reconcile_check(self.scope, self.request(attempt_id=result['attempt_id'], host_proof={'stopped': True}))
        for changes in ({'attempt_id': '0' * 32}, {'expected_revision': 1}, {'execution_digest': '0' * 64},
                        {'current_candidate_digest': '0' * 64}, {'expected_pins': {}}, {'descendants_stopped': False}):
            with self.subTest(changes=changes), self.assertRaises(state.StateError):
                self.reconcile(result, **changes)
        self.assertEqual(self.reconcile(result)['attempt']['status'], 'passed')

    def test_public_status_exposes_scope_binding_for_host_reconciliation(self):
        self.plan()
        result = self.check()
        status = self.api.evidence_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['scope_id'], state._scope_id(self.scope))
        self.assertEqual(result['scope_id'], status['scope_id'])
        self.assertEqual(status['evidence']['candidate_digest'], result['attempt']['candidate_digest'])

    def test_check_host_proof_rejects_bare_repository_location(self):
        bare = self.root / 'bare.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(bare)], check=True, capture_output=True)
        path = bare / 'host-proof.json'
        raw = b'{}'
        path.write_bytes(raw)
        path.chmod(0o400)
        with self.assertRaises(state.StateError):
            self.api._read_host_proof(self.scope, dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()))

    def test_reconciled_failure_and_cancelled_reservation_never_become_pass(self):
        self.plan()
        plan = self.api._load_plan(self.scope, self.identity)
        plan['checks'][0]['argv'] = [sys.executable, '-c', 'raise SystemExit(1)']
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:failure-fixture'))
        result = self.api.run_check(self.scope, self.request(check_id='unit'))
        self.assertEqual(result['attempt']['status'], 'uncertain')
        self.assertEqual(self.reconcile(result)['attempt']['status'], 'failed')
        _, _, attempt, _ = self.api._reserve(self.scope, self.request(), 'check', dict(check_id='unit'))
        result = self.reconcile({'attempt': attempt})
        self.assertEqual(result['attempt']['status'], 'cancelled')
        self.assertFalse(self.verify()['verified'])

    def test_required_proof_and_ready_only_from_current_verifier(self):
        self.plan()
        self.assertFalse(self.verify()['verified'])
        self.checked()
        self.assertFalse(self.verify()['verified'])
        self.review()
        result = self.verify()
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['task']['state'], 'DONE')
        with self.assertRaises(state.StateError):
            state.update_task(self.scope, self.request(state='DONE', reason='claim', reference='claim'))
        (self.repo / 'app.py').write_text('value = 2\n')
        result = state.task_status(self.scope, 'codex', 'parent')
        self.assertEqual(result['task']['state'], 'REVIEW')

    def test_candidate_binds_modes_untracked_index_base_environment_and_linked_checkout(self):
        self.plan()
        candidate = self.api.capture_candidate(self.scope, self.api._load_plan(self.scope, self.identity))
        (self.repo / 'app.py').chmod(0o755)
        self.assertNotEqual(candidate, self.api.capture_candidate(self.scope, self.api._load_plan(self.scope, self.identity)))
        (self.repo / 'app.py').chmod(0o644)
        (self.repo / 'untracked').write_text('one')
        first = self.api.capture_candidate(self.scope, self.api._load_plan(self.scope, self.identity))
        (self.repo / 'untracked').write_text('two')
        self.assertNotEqual(first, self.api.capture_candidate(self.scope, self.api._load_plan(self.scope, self.identity)))
        other = self.peer_scope
        self.assertEqual(other.git_common_dir,self.scope.git_common_dir)
        self.assertEqual(other.repository_id,self.scope.repository_id)
        self.assertNotEqual(other.checkout_id,self.scope.checkout_id)
        self.assertNotEqual(candidate['repository_root'], self.api.capture_candidate(other, self.api._load_plan(self.scope, self.identity))['repository_root'])

    def test_failed_baseline_does_not_satisfy_criterion_and_effects_cannot_expand(self):
        self.plan()
        plan = self.api._load_plan(self.scope, self.identity)
        plan['checks'][0]['argv'] = [sys.executable, '-c', 'raise SystemExit(1)']
        plan['checks'][0]['baseline'].update(status='failed', exit_code=1, output_digest=hashlib.sha256(b'').hexdigest())
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:changed-command'))
        result = self.checked()
        self.assertEqual(result['attempt']['status'], 'failed')
        self.assertEqual(result['attempt']['baseline_comparison'], 'pre-existing-failure')
        self.assertFalse(self.verify()['verified'])
        plan['checks'][0]['effects']['network'] = True
        with self.assertRaises(state.StateError):
            self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:claimed'))

    def test_forged_report_identity_and_missing_applicability_remain_blocked(self):
        self.plan(reviews={'general': dict(applicability='unresolved', reason='No capacity', independent=True),
                           'security': dict(applicability='required', reason='Runtime', independent=True)})
        self.checked()
        self.assertFalse(self.verify()['verified'])
        with self.assertRaises(state.StateError):
            self.api.collect_review(self.scope, self.request(attempt_id='forged', receipt={'reviewer_id': 'other'}, report={}))

    def test_check_mutation_timeout_and_running_attempt_block_prior_success(self):
        self.plan()
        self.checked()
        plan = self.api._load_plan(self.scope, self.identity)
        plan['checks'][0]['argv'] = [sys.executable, '-c', "from pathlib import Path; Path('app.py').write_text('changed')"]
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:check-change'))
        self.assertEqual(self.checked()['attempt']['status'], 'changed-inputs')
        self.assertFalse(self.verify()['verified'])

    def test_private_immutable_artifacts_and_unsafe_or_oversized_inputs(self):
        self.plan()
        folder = self.scope.runtime.state_dir / 'evidence'
        for path in folder.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        with self.assertRaises(state.StateError):
            self.api.plan_evidence(self.scope, self.request(plan={'x': 'a' * (128 * 1024)}))
        (self.repo / 'link').symlink_to(self.repo / 'app.py')
        with self.assertRaises(state.StateError):
            self.checked()

    def test_ready_has_no_stop_continuation_and_harmless_generation_does_not_erase_proof(self):
        from runtime.engine import RuntimeEvent
        self.plan()
        self.checked()
        self.review()
        self.assertTrue(self.verify()['verified'])
        event = RuntimeEvent('PostToolUse', 'codex', 'parent', self.repo, 'turn', 'call', 'read_file', False, 'receipt')
        state.process_event(self.scope, event)
        self.assertEqual(state.task_status(self.scope, 'codex', 'parent')['task']['state'], 'DONE')
        self.assertEqual(state.process_event(self.scope, replace(event, kind='Stop')).action, 'pass')

    def test_security_impact_must_be_reassessed_after_candidate_change(self):
        self.plan()
        (self.repo / 'app.py').write_text('value = 2\n')
        self.checked()
        self.review()
        result = self.verify()
        self.assertFalse(result['verified'])
        self.assertTrue(any('impact' in b for b in result['blockers']))
        impact = dict(status='assessed', reason='Rechecked the changed runtime boundary', reference='code:new-impact')
        self.assertTrue(self.verify(security_impact=impact)['verified'])

    def assert_interleaved_check_diagnostics(self, cancel):
        from runtime import delegation
        self.plan()
        plan = self.api._load_plan(self.scope, self.identity)
        plan['checks'][0]['argv'] = [sys.executable, '-c', "print('actual failure'); raise SystemExit(7)"]
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:failure-fixture'))
        request = self.request(check_id='unit', dispatch_id='interleaved-check')
        execute = self.api._execute
        interleaved = []
        def execute_with_operation(*args):
            if cancel:
                state.update_task(self.scope, self.request(state='CANCELLED', reason='User cancelled during check',
                    reference='user:cancelled'))
            else:
                delegation.reserve_coordinator(self.scope, self.readiness_owner(identity=self.identity,
                    claims=[dict(kind='path', key=str(self.repo / 'app.py'), mode='read')]))
            with state._database(self.scope) as db:
                interleaved.append(state._task(db, state._scope_id(self.scope), 'codex', 'parent'))
            return execute(*args)
        with patch.object(self.api, '_execute', side_effect=execute_with_operation):
            result = self.api.run_check(self.scope, request)
        attempt = result['attempt']
        self.assertEqual(attempt['exit_code'], 7)
        self.assertEqual(attempt['observed_status'], 'failed')
        self.assertEqual(attempt['status'], 'uncertain')
        execution = self.api._read(self.scope, attempt['execution'])
        self.assertEqual(self.api._read(self.scope, execution['output'])['text'], 'actual failure')
        self.assertEqual(execution['result']['exit_code'], 7)
        with state._database(self.scope) as db:
            record = next(r for r in delegation._ledger(self.scope, db) if r['id'] == attempt['id'])
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(record['data']['observation'], attempt['execution'])
        self.assertEqual(record['data']['status'], 'uncertain')
        for field in ('state', 'reason', 'reference', 'pins', 'conditions'):
            self.assertEqual(current[field], interleaved[0][field])
        self.assertEqual(current['revision'], interleaved[0]['revision'] + 1)
        with patch.object(self.api, '_execute', side_effect=AssertionError('stable replay reran command')):
            replay = self.api.run_check(self.scope, request)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['attempt'], attempt)
        self.assertEqual(replay['task'], current)
        if not cancel:
            with self.assertRaisesRegex(state.StateError, 'revision conflict'):
                delegation.reserve_coordinator(self.scope, self.readiness_owner(identity=self.identity,
                    dispatch='stale', claims=[dict(kind='path', key=str(self.repo / 'app.py'), mode='read')]) |
                    dict(expected_revision=request['expected_revision']))
        with self.assertRaises(state.StateError):
            self.reconcile(result, expected_revision=request['expected_revision'])
        settled = self.reconcile(result)
        self.assertEqual(settled['attempt']['status'], 'failed')
        self.assertEqual(settled['attempt']['exit_code'], 7)
        self.assertEqual(settled['task']['state'], current['state'])

    def test_check_result_survives_compatible_same_task_reservation_during_execution(self):
        self.assert_interleaved_check_diagnostics(cancel=False)

    def test_check_result_survives_task_cancellation_during_execution(self):
        self.assert_interleaved_check_diagnostics(cancel=True)

    def test_check_keeps_private_redacted_inspectable_output_and_unknown_failure_comparison(self):
        self.plan()
        plan = self.api._load_plan(self.scope, self.identity)
        plan['checks'][0]['argv'] = [sys.executable, '-c', "print('API_KEY=never-save-this'); print('failure at app.py:1'); raise SystemExit(1)"]
        plan['checks'][0]['baseline']['status'] = 'failed'
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:diagnostic'))
        attempt = self.checked()['attempt']
        self.assertEqual(attempt['baseline_comparison'], 'unresolved-failure-comparison')
        execution = self.api._read(self.scope, attempt['execution'])
        output = self.api._read(self.scope, execution['output'])
        self.assertIn('failure at app.py:1', output['text'])
        self.assertNotIn('never-save-this', output['text'])
        self.assertNotIn('never-save-this', json.dumps(attempt))

    def test_timeout_with_surviving_descendant_remains_uncertain_after_new_pass(self):
        self.plan()
        plan = self.api._load_plan(self.scope, self.identity)
        # The child drops stdout, so parent exit/pipe EOF cannot establish its lifecycle.
        # It survives the timeout until released; the timeout expires only after it ignores SIGTERM.
        ready, release = self.root / 'descendant-ready', self.root / 'release-descendant'
        self.addCleanup(release.touch)
        child = ("import signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                 "Path(%r).touch(); until=time.monotonic()+60\n"
                 "while not Path(%r).exists() and time.monotonic()<until: time.sleep(.01)" % (str(ready), str(release)))
        plan['checks'][0]['argv'] = [sys.executable, '-c',
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(60)" % child]
        plan['checks'][0]['timeout'] = 1
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:timeout'))
        with self.check_clock(ready.exists):
            outcome = self.checked()['attempt']
        self.assertEqual(outcome['status'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:retry'))
        self.assertFalse(self.verify()['verified'])

    def test_corrupt_check_receipt_cannot_establish_readiness(self):
        self.plan()
        checked = self.checked()['attempt']
        self.review()
        path = self.scope.runtime.state_dir / 'evidence' / checked['execution']['name']
        path.chmod(0o600)
        path.write_text('{}')
        path.chmod(0o400)
        with self.assertRaises(state.StateError):
            self.verify()

    def test_relevant_ignored_input_is_captured_and_timeout_is_not_a_pass(self):
        (self.repo / '.gitignore').write_text('ignored.txt\n')
        (self.repo / 'ignored.txt').write_text('first')
        self.plan(relevant_untracked=['ignored.txt'])
        plan = self.api._load_plan(self.scope, self.identity)
        first = self.api.capture_candidate(self.scope, plan)
        (self.repo / 'ignored.txt').write_text('second')
        self.assertNotEqual(first, self.api.capture_candidate(self.scope, plan))
        plan['checks'][0]['argv'] = [sys.executable, '-c', 'import time; time.sleep(60)']
        plan['checks'][0]['timeout'] = 1
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:timeout-proof'))
        with self.check_clock(lambda: True):
            self.assertIn(self.checked()['attempt']['status'], {'timed-out', 'uncertain'})
        self.assertFalse(self.verify()['verified'])

    def test_changed_content_cannot_erase_surviving_writer_uncertainty(self):
        self.plan()
        def uncertain(scope, check, tool):
            (self.repo / 'app.py').write_text('changed while an unknown writer remains')
            return dict(status='uncertain', exit_code=None, output_digest=hashlib.sha256(b'').hexdigest(),
                        private_output={'text': '', 'truncated': False})
        with patch.object(self.api, '_execute', side_effect=uncertain):
            result = self.checked()
        self.assertEqual(result['attempt']['status'], 'uncertain')
        self.assertTrue(result['attempt']['inputs_changed'])
        self.assertFalse(self.verify()['verified'])

    def test_cancel_and_replace_cannot_forget_uncertain_checkout_writer(self):
        self.plan()
        self.api._reserve(self.scope, self.request(), 'check', dict(check_id='unit'))
        result = state.update_task(self.scope, self.request(state='CANCELLED', reason='Interrupted parent', reference='user:cancel'))
        old_revision = result['task']['revision']
        with self.assertRaises(state.StateError):
            state.start_task(self.scope, self.identity | dict(task_id='replacement', mode='editable', decision_ref='user:accepted',
                replaces=dict(task_id='task', expected_revision=old_revision)))
        self.assertEqual(state.task_status(self.scope, 'codex', 'parent')['task']['task_id'], 'task')

    def test_latest_unresolved_attempt_cannot_hide_behind_prior_pass(self):
        self.plan()
        self.checked()
        self.review()
        prepared = self.api.prepare_review(self.scope, self.native_review_request(coverage=['general'], criteria=['works'],
            assignment='Review pending latest change read-only.'))
        result = self.verify()
        self.assertFalse(result['verified'])
        self.assertTrue(any('unresolved' in b for b in result['blockers']))
        self.assertEqual(self.api.prepared_native_review(self.scope, prepared['attempt_id'])['parent_session_id'], 'parent')

    def test_scoped_evidence_reuse_requires_unchanged_declared_dependencies(self):
        self.plan()
        check = self.checked()['attempt_id']
        review = self.review()['attempt_id']
        (self.repo / 'unrelated.txt').write_text('unrelated documentation')
        self.assertFalse(self.verify()['verified'])
        reuse = [dict(attempt_id=a, reason='Only unrelated text changed; app.py and its dependencies unchanged',
                      dependencies=['app.py']) for a in (check, review)]
        impact = dict(status='assessed', reason='Unrelated text has no security boundary change', reference='code:impact-recheck')
        self.assertTrue(self.verify(reuse=reuse, security_impact=impact)['verified'])
        (self.repo / 'app.py').write_text('value = 2\n')
        self.assertFalse(self.verify(reuse=reuse)['verified'])

    def test_concurrent_update_during_verification_is_not_overwritten(self):
        self.plan()
        self.checked()
        self.review()
        request = self.request(purpose='completion')
        original = self.api.capture_candidate
        def capture(scope, plan):
            candidate = original(scope, plan)
            state.update_task(scope, self.request(state='BLOCKED', reason='Intervening decision', reference='user:hold'))
            return candidate
        with patch.object(self.api, 'capture_candidate', side_effect=capture), self.assertRaises(state.StateError):
            self.api.verify_completion(self.scope, request)
        self.assertEqual(state.task_status(self.scope, 'codex', 'parent')['task']['state'], 'BLOCKED')

    def test_confirmed_security_finding_survives_edits_and_needs_scoped_fresh_review(self):
        self.plan()
        self.checked()
        finding = dict(id='security-1', severity='high', scope='security', status='confirmed',
                       location='app.py:1 working', scenario='Reachable unsafe path', evidence='Code tracing',
                       counterevidence='No applicable guard')
        prior = self.review(findings=[finding])['attempt_id']
        self.assertFalse(self.verify()['verified'])
        (self.repo / 'app.py').write_text('value = 2\n')
        self.plan()
        self.checked()
        fresh = self.review()['attempt_id']
        self.assertFalse(self.verify()['verified'])
        # A fresh assessment explicitly links code verification to the old immutable finding.
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
            attempt = next(a for a in task['evidence']['attempts'] if a['id'] == fresh)
        assessment = self.api._read(self.scope, attempt['assessment'])['assessment']
        assessment['rechecked_findings'] = [dict(attempt_id=prior, finding_id='security-1', status='resolved',
                                                reference='fresh:code-and-caller-proof')]
        self.api.assess_review(self.scope, self.request(attempt_id=fresh, assessment=assessment))
        self.assertTrue(self.verify()['verified'])

    def test_tool_and_base_ref_changes_invalidate_previous_proof(self):
        self.plan()
        plan = self.api._load_plan(self.scope, self.identity)
        before = self.api.capture_candidate(self.scope, plan)
        plan['checks'][0]['env']['LANG'] = 'C'
        self.assertNotEqual(before, self.api.capture_candidate(self.scope, plan))
        self.git('commit', '--allow-empty', '-qm', 'moved base')
        after = self.api.capture_candidate(self.scope, self.api._load_plan(self.scope, self.identity))
        self.assertNotEqual(before['base'], after['base'])

    def test_merge_evidence_never_grants_merge_authority(self):
        self.plan()
        self.checked()
        self.review()
        result = self.api.verify_completion(self.scope, self.request(purpose='merge-evidence'))
        self.assertTrue(result['verified'], result)
        self.assertFalse(result['merge_authority'])
        self.assertNotEqual(result['task']['state'], 'DONE')

    def test_cooperative_review_cannot_issue_ordinary_readiness_or_merge_evidence(self):
        self.plan()
        self.checked()
        self.review(cooperative=True)
        for purpose in ('completion', 'merge-evidence'):
            with self.subTest(purpose=purpose):
                result = self.api.verify_completion(self.scope, self.request(purpose=purpose))
                self.assertFalse(result['verified'])
                self.assertFalse(result['merge_authority'])
                self.assertNotEqual(result['task']['state'], 'DONE')
                self.assertTrue(any('ownership remains unresolved' in blocker for blocker in result['blockers']))


class CooperativeQualificationTests(unittest.TestCase):
    def test_guarded_worker_check_and_review_complete_only_the_qualification(self):
        from runtime import delegation, evidence, setup_readiness
        from runtime.adapters import codex
        from tests.test_delegation import AdmissionTests
        from tests.test_staged_admission import GenuineStagedAdmissionTests

        owner = GenuineStagedAdmissionTests('runTest')
        self.addCleanup(owner.doCleanups)
        owner.setUp()
        owner.identity.update(session_id='parent', task_id='task')
        attempt = owner.journal['qualification_scope']['attempts'][0]
        attempt['task'].update(session_id='parent', task_id='task')
        attempt['native_sessions'] = ['parent', 'child', 'reviewer']
        d5 = EvidenceTests('runTest')
        self.addCleanup(d5.doCleanups)
        d5.root, d5.repo = owner.fixture.base, owner.fixture.repo
        d5.identity, d5.api = owner.identity, evidence
        d5.git('config', 'user.email', 'fixture@example.invalid')
        d5.git('config', 'user.name', 'Fixture')
        canary = d5.repo / 'app.py'
        canary.write_text('value = 1\n')
        d5.git('add', 'app.py')
        d5.git('commit', '-qm', 'public fixture')
        command = '*** Begin Patch\n*** Update File: app.py\n@@\n-value = 1\n+value = 42\n*** End Patch'
        attempt['probe'] = dict(canary=str(canary), patch=command, before_sha256=hashlib.sha256(canary.read_bytes()).hexdigest())
        d5.scope = scope = owner.acquire(
            operations=('task-start', 'task-patch', 'native-worker', 'check-run', 'native-review', 'verify-completion'),
            keys=('pre-tool-enforcement', 'post-tool-handling', 'stop-ownership',
                  'interrupt-ownership', 'review-readiness'),
            max_attempts=3, max_resource_units=2,
        )
        owner.start(decision_ref='user:accepted')
        state.update_task(scope, d5.request(state='WORKING', reason='Edit the approved public fixture', reference='user:accepted'))
        d5.plan()
        definition = evidence._load_plan(scope, d5.identity)
        # Noninteractive shell builtins read the fixture as data and start no commands.
        definition['checks'][0]['argv'] = ['/bin/sh', '-c',
            'IFS= read -r line < app.py || exit 1\ncase "$line" in "value = 42") exit 0;; *) exit 1;; esac']
        evidence.plan_evidence(scope, d5.request(plan=definition, change_decision_ref='user:fixture-command'))

        native = AdmissionTests('runTest')
        native.api, native.scope, native.identity = delegation, scope, d5.identity
        native.root, native.repo = d5.root, d5.repo
        claims = [native.path_claim()]
        plan = native.plan_value(jobs=[
            dict(id='edit', role='worker', claims=claims, dependencies=[], max_attempts=1),
            dict(id='unit', role='check', claims=[native.path_claim(mode='read')], dependencies=['edit'], max_attempts=1),
            dict(id='review', role='review', claims=[native.path_claim(mode='read')], dependencies=['unit'], max_attempts=1)])
        plan['limits'].update(max_attempts=2, max_resource_units=2, max_parallel_workers=1, host_capacity=1)
        native.plan(plan)
        review_request = d5.native_review_request(coverage=['general', 'security'], criteria=['works'],
            assignment='Inspect the approved candidate and retained check read-only.')
        native.catalog, native.cache_request = d5.native_review_catalog, d5.native_review_cache
        request = native.prepare_request(selection=review_request['selection'])
        request['brief'].update(resources=claims)
        prepared = delegation.prepare_assignment(scope, request)
        self.assertTrue(prepared['launch_admitted'])
        self.assertFalse(delegation.prepare_assignment(scope, request)['launch_admitted'])
        payload = dict(hook_event_name='PreToolUse', session_id='parent', agent_id='child', agent_type='default',
            cwd=str(d5.repo), turn_id='child-turn', tool_use_id='patch-1', tool_name='apply_patch',
            tool_input=dict(command=command))
        for malformed in (None, '', 'parent'):
            with self.assertRaises(state.StartupError):
                codex.normalize_runtime(payload | dict(agent_id=malformed))
        self.assertEqual(state.process_event(scope, codex.normalize_runtime(payload)).action, 'deny')
        payload['transcript_path'] = str(d5.root / 'rollout.jsonl')
        lineage = dict(parent_session_id='parent', task_name=prepared['task_name'],
            native_version=native.catalog['native_version'], model=prepared['requested_model'], effort=prepared['requested_effort'])
        with patch.object(codex, 'guarded_child_lineage', return_value=lineage | dict(parent_session_id='foreign'), create=True):
            self.assertEqual(state.process_event(scope, codex.normalize_runtime(payload)).action, 'deny')
        with patch.object(codex, 'guarded_child_lineage', return_value=lineage, create=True):
            wrong_parent = payload | dict(session_id='foreign')
            self.assertEqual(state.process_event(scope, codex.normalize_runtime(wrong_parent)).action, 'deny')
            self.assertEqual(state.process_event(scope, codex.normalize_runtime(payload)).action, 'pass')
            repeated = payload | dict(tool_use_id='another-patch-call')
            self.assertEqual(state.process_event(scope, codex.normalize_runtime(repeated)).action, 'deny')
        canary.write_text('value = 42\n')
        post = payload | dict(hook_event_name='PostToolUse', tool_response='Success. Updated the following files: M app.py')
        post_event = codex.normalize_runtime(post)
        self.assertEqual(state.process_event(scope, post_event).action, 'pass')
        pre = delegation._child_patch_pre_fingerprint(prepared['assignment_digest'], post_event.patch_input_fingerprint)
        with state._database(scope) as db:
            self.assertEqual(db.execute("select kind,fingerprint,conflicted from receipts where scope_id=? "
                "and session_id='child' and turn_id='child-turn' and tool_call_id='patch-1' order by kind",
                (state._scope_id(scope),)).fetchall(), [
                ('PostToolUse', delegation._child_patch_fingerprint(pre, post_event.receipt_fingerprint), 0),
                ('PreToolUse', pre, 0),
            ])
        assignment, = delegation.delegation_status(scope, 'codex', 'parent')['assignments']
        self.assertEqual((assignment['status'], assignment['observation']), ('prepared', None))
        activity = dict(source_digest='a' * 64, calls=[
            dict(id='patch-1', tool='apply_patch', input_digest=codex.patch_input_fingerprint(command))])
        proof, observed = native.cooperative_worker(prepared, activity=activity)
        with patch.object(codex, 'observe_native_worker', return_value=observed):
            with state._database(scope, write=True) as db:
                db.execute("update receipts set conflicted=1 where kind='PostToolUse' and tool_call_id='patch-1'")
            with self.assertRaisesRegex(state.StateError, 'exact paired hook receipts'):
                delegation.reconcile_assignment(scope, proof)
            with state._database(scope, write=True) as db:
                db.execute("update receipts set conflicted=0 where kind='PostToolUse' and tool_call_id='patch-1'")
            result = delegation.reconcile_assignment(scope, proof)
        self.assertEqual(result['status'], 'cooperative-completed')

        check_request = d5.request(check_id='unit', dispatch_id='canary-check')
        check = evidence.run_check(scope, check_request)
        self.assertEqual(check['attempt']['status'], 'uncertain')
        self.assertEqual(check['attempt']['exit_code'], 0)
        self.assertTrue(check['attempt']['process_started'])
        with self.assertRaisesRegex(state.StateError, 'dependency prerequisite'):
            evidence.prepare_review(scope, review_request | d5.request())
        with self.assertRaisesRegex(state.StateError, 'independent stopped-descendant evidence'):
            evidence.reconcile_check(scope, d5.request(attempt_id=check['attempt_id']))
        # The command ran locally. The independent external lifecycle proof is a fixture.
        check = d5.reconcile(check, observation_ref='fixture:synthetic-inspection-of-exact-builtin-check-and-exit')
        self.assertEqual(check['attempt']['status'], 'passed')
        replay = evidence.run_check(scope, check_request)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['attempt_id'], check['attempt_id'])
        canary.write_text('value = 43\n')
        with self.assertRaisesRegex(state.StateError, 'Original qualification public probe changed'):
            evidence.prepare_review(scope, review_request | d5.request())
        canary.write_text('value = 42\n')
        review = d5.review(cooperative=True)
        candidate = check['attempt']['candidate_digest']
        self.assertEqual(review['attempt']['candidate_digest'], candidate)
        with state._database(scope) as db:
            records = {record['id']: record for record in delegation._ledger(scope, db, task=d5.identity)}
            worker_before = evidence._read(scope, records[prepared['attempt_id']]['body']['qualification_before'], db=db)
            review_before = evidence._read(scope, records[review['attempt']['id']]['body']['qualification_before'], db=db)
        self.assertEqual(worker_before['probe']['descriptor'], attempt['probe'])
        self.assertIsNone(review_before['probe'])
        self.assertEqual(review_before['candidate']['digest'], candidate)
        result = d5.verify(security_impact=dict(status='assessed', reason='Inspected unchanged fixture candidate',
            reference='fixture:security-impact'))
        self.assertEqual(result['candidate_digest'], candidate)
        self.assertTrue(result['qualification_verified'], result['blockers'])
        self.assertFalse(result['verified'])
        self.assertFalse(result['implementation_verified'])
        self.assertFalse(result['merge_authority'])
        self.assertNotEqual(result['task']['state'], 'DONE')
        status = evidence.evidence_status(scope, 'codex', 'parent')['evidence']
        self.assertTrue(status['qualification_verified'], status['blockers'])
        self.assertFalse(status['implementation_verified'])
        with self.assertRaises(state.StateError):
            setup_readiness.observe(scope, owner.request(check_attempt_id=check['attempt_id'],
                review_attempt_id=review['attempt']['id']))


class DocumentedEvidencePlanTests(unittest.TestCase):
    def _example(self, name, fixture):
        text = (Path(__file__).resolve().parents[1] / 'clients/evidence-plan.md').read_text()
        block = re.search(r'<!-- example:' + re.escape(name) + r' -->\s*```json\n(.*?)\n```', text, re.S)
        self.assertIsNotNone(block, 'Missing documented example: ' + name)
        task = state.task_status(fixture.scope, 'codex', fixture.identity['session_id'])['task']
        replacements = {
            '<ACCEPTED_DECISION_REF>': task['decision_ref'],
            '<BASE_COMMIT>': fixture.git('rev-parse', 'HEAD'),
            '<GENERAL_REVIEW_REASON>': 'Review the fixture application change',
            '<SECURITY_REVIEW_REASON>': 'Review the fixture local execution boundary',
            '<SECURITY_IMPACT_REASON>': 'Fixture impact assessment has not run',
            '<SECURITY_IMPACT_REFERENCE>': 'fixture:pending-impact',
            '<ACCEPTED_ARGV>': [sys.executable, '-c',
                "from pathlib import Path; assert Path('app.py').read_text() == 'value = 1\\n'"],
            '<CHECK_PATH>': os.defpath,
            '<INSPECTION_REFERENCE>': 'fixture:inspected-read-only-command',
            '<BASELINE_REFERENCE>': 'fixture:baseline-not-run',
        }
        def resolve(value):
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if isinstance(value, str):
                return replacements.get(value, value)
            return value
        return resolve(json.loads(block.group(1)))

    def _request(self, *, with_check=False):
        fixture = EvidenceTests('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        payload = self._example('evidence-plan-minimal', fixture)
        if with_check:
            payload['plan']['checks'] = [self._example('evidence-plan-read-only-check', fixture)]
            payload['plan']['criteria'] = [dict(id='acceptance', checks=['unit'], behavior=False)]
        return fixture, fixture.request(**payload)

    def test_minimal_plan_example_executes_against_runtime_validator(self):
        fixture, request = self._request()
        before = request['expected_revision']
        result = fixture.api.plan_evidence(fixture.scope, request)
        self.assertTrue(result['ok'])
        self.assertEqual(result['task']['revision'], before + 1)
        self.assertEqual(fixture.api._load_plan(fixture.scope, fixture.identity), request['plan'])
        self.assertEqual(request['plan']['security_impact']['status'], 'unresolved')
        self.assertNotEqual(result['task']['state'], 'DONE')

    def test_read_only_check_example_preserves_thresholds(self):
        fixture, request = self._request(with_check=True)
        result = fixture.api.plan_evidence(fixture.scope, request)
        self.assertTrue(result['ok'])
        self.assertEqual(result['task']['revision'], request['expected_revision'] + 1)
        stored = fixture.api._load_plan(fixture.scope, fixture.identity)
        self.assertEqual(stored, request['plan'])
        self.assertEqual(stored['checks'][0]['thresholds'], {'exit_codes': [0]})
        self.assertEqual(stored['checks'][0]['baseline']['status'], 'not-run')

    def test_plan_examples_reject_missing_and_unknown_fields(self):
        for target, field, value in (
                ('envelope', 'expected_revision', None), ('plan', 'criteria', None),
                ('envelope', 'unknown', True), ('plan', 'unknown', True), ('check', 'unknown', True)):
            with self.subTest(target=target, field=field):
                fixture, request = self._request(with_check=True)
                before = request['expected_revision']
                fields = {'envelope': request, 'plan': request['plan'], 'check': request['plan']['checks'][0]}[target]
                if value is None:
                    del fields[field]
                else:
                    fields[field] = value
                with self.assertRaises(state.StateError):
                    fixture.api.plan_evidence(fixture.scope, request)
                self.assertEqual(fixture.request()['expected_revision'], before)

    def test_plan_example_rejects_stale_revision(self):
        fixture, request = self._request()
        result = fixture.api.plan_evidence(fixture.scope, request)
        self.assertTrue(result['ok'])
        self.assertEqual(result['task']['revision'], request['expected_revision'] + 1)
        with self.assertRaisesRegex(state.StateError, 'Task revision conflict'):
            fixture.api.plan_evidence(fixture.scope, request)
        self.assertEqual(fixture.request()['expected_revision'], result['task']['revision'])


if __name__ == '__main__':
    unittest.main()
