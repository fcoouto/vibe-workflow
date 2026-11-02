"""D5 proofs use disposable Git repositories and explicit mocked native observations."""
from tests.fixture_canonical_birth import fixture_artifact
from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import state
from tests.fixture_canonical_birth import native_receipt_fields
from runtime.startup import ProjectScope, RuntimeSettings, StartupError, policy_digest

def _run_d4_fixture(fixture, request, operation):
    """Issue fresh internal test-host authority; this does not qualify a native provider."""
    changes = fixture.changes
    class Host:
        def acquire_d4_bookkeeping(self, facts, issue):return issue(facts)
    with changes._bootstrap_d4_host(Host(), issuer=changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
        return changes._run_scoped(fixture.directory, request, operation, host=host)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical, save_test_acquisition
        self._canonical_common_git = self._testMethodName in {
            'test_linked_checkout_shared_git_reservation_invalidates_ready_in_same_transaction',
            'test_candidate_binds_modes_untracked_index_base_environment_and_linked_checkout'}
        fixture, self.acquisition, observation = fresh_canonical(self)
        self.birth_fixture = fixture
        self.birth_observation = observation
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

    def checked(self):
        result = self.check()
        if result['attempt'].get('process_started') and result['attempt'].get('observed_status') != 'uncertain':
            result = self.reconcile(result)
        return result

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

    def reconcile_review(self, prepared):
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
        raw=json.dumps(proof).encode(); path=self.root/('review-proof-'+row['id']+'.json');path.write_bytes(raw);path.chmod(0o400)
        return delegation.reconcile_assignment(self.scope, self.request(attempt_id=row['id'], host_proof=dict(path=str(path),
            sha256=hashlib.sha256(raw).hexdigest()), assessment=dict(outcome='completed', changed_paths=[],
            result_assessment='Fixture report independently checked', partial_change_assessment='No candidate/shared-resource effects',
            evidence=['fixture:effect-inspection'], coordinator_ref='fixture:independent-assessment')))

    def review(self, findings=None, coverage=None):
        from runtime import delegation
        coverage = coverage or ['general', 'security']
        request = self.native_review_request(coverage=coverage, criteria=['works'],
            assignment='Review read-only; inspect app.py and callers. Return the bound report.')
        prepared = self.api.prepare_review(self.scope, request)
        attempt = prepared['attempt_id']
        binding = delegation.prepared_native_review_assignment(self.scope, attempt)
        # Native receipts do not echo the internal historical runtime locator metadata.
        binding = {key:value for key,value in binding.items() if key not in {'pins','accepted_identity','scope_id'}}
        report = dict(version=1, attempt_id=attempt, assignment_digest=prepared['assignment_digest'],
            candidate_digest=prepared['candidate_digest'], coverage=coverage, findings=findings or [], limitations=[])
        raw = json.dumps(report)
        actual = dict(version=2, mechanism='codex-native-agent-path', **native_receipt_fields(binding), agent_path='/root/'+binding['task_name'],
            originator='codex_exec', parent_model_provider=None, worker_model_provider=None, parent_turn_id='turn', spawn_item_id='spawn',
            worker_id='reviewer', worker_turn_id='child-turn', result_item_id='final', turn_status='completed', checkout=str(self.repo),
            configured_model='fixture-review-model', configured_effort='high', spawn_observed_model='fixture-review-model',
            spawn_observed_effort='high', execution_model=None, execution_effort=None, descendants_stopped=False,
            prompt_delivery='unverified', result_digest=hashlib.sha256(raw.encode()).hexdigest())
        with patch('runtime.adapters.codex.observe_native_review_assignment', return_value=dict(receipt=actual,result=raw)):
            observed=delegation.observe_assignment(self.scope, self.request(attempt_id=attempt,parent_turn_id='turn',spawn_item_id='spawn'))
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
        self.reconcile_review(prepared)
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
        child = "import time; from pathlib import Path; time.sleep(2); Path('app.py').write_text('late detached write')"
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r], start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)" % child
        plan['checks'][0]['argv'] = [sys.executable, '-c', parent]
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:detached-fixture'))
        result = self.api.run_check(self.scope, self.request(check_id='unit'))
        self.assertEqual(result['attempt']['status'], 'uncertain')
        self.assertEqual(result['attempt']['observed_status'], 'passed')
        self.assertFalse(self.verify()['verified'])
        time.sleep(2.2)
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
        plan['checks'][0]['argv'] = [sys.executable, '-c',
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(1.5)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(3)"]
        plan['checks'][0]['timeout'] = 1
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:timeout'))
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
        plan['checks'][0]['argv'] = [sys.executable, '-c', 'import time; time.sleep(3)']
        plan['checks'][0]['timeout'] = 1
        self.api.plan_evidence(self.scope, self.request(plan=plan, change_decision_ref='user:timeout-proof'))
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


if __name__ == '__main__':
    unittest.main()


class D4RecoveryFixture:
    def enroll_recovery(self, *, shared=False):
        from runtime import changes, onboarding
        if not shared and hasattr(self.fixture, 'birth_fixture'):
            fixture = self.fixture.birth_fixture
            journal = changes._load_journal(fixture.directory)
            request = dict(schema_version=2, manifest=journal['manifest'], approval=journal['approval'])
            self.recovery_fixtures = [(fixture, request)]
            self.recovery_index = 0
            return self.fixture.scope
        from tests.test_changes import ScopedChangesTests
        from runtime.startup import AcceptedScope, canonical_scope_projection, decode_project_record, _canonical_freeze, _canonical_plain
        storage = self.fixture.root / 'recovery-authority'
        self.recovery_fixtures = []

        def fixture():
            d4 = ScopedChangesTests(methodName='runTest');d4.setUp();self.addCleanup(d4.doCleanups)
            return d4

        def apply(d4, request):
            for repository in request['manifest']['enrollment']['record']['repositories']:
                for checkout in repository['checkouts']:
                    checkout['clients'][0]['configuration']['runtime'] = dict(schema_version=3, state_dir=str(storage))
            request['approval']['manifest_digest'] = onboarding._digest(request['manifest'])
            self.assertFalse((storage / 'runtime.sqlite3').exists())
            result = _run_d4_fixture(d4, request, 'apply')
            self.assertEqual(result['exit_code'], 0, result)
            return request

        dependency = None
        if shared:
            other = fixture()
            other_request = apply(other, other.scoped_request())
            dependency = other.repo / 'AGENTS.md'

        d4 = fixture();d4.repo = self.fixture.repo
        pointer = '@' + str(dependency) + '\n' if dependency else ''
        (d4.repo / 'AGENTS.md').write_text('product rules\nlegacy process\n' + pointer)
        d4.scoped_inspect()
        proposal = d4.scoped_proposal()
        action = proposal['assessments']['repositories'][0]['actions'][0]
        action['text'] += pointer
        action['after'] = hashlib.sha256(action['text'].encode()).hexdigest()
        onboarding.record_plan(d4.directory, proposal)
        request = d4.scoped_request(prepared=True)
        record = request['manifest']['enrollment']['record']
        if dependency:
            record['shared_policy'] = [dict(path=str(dependency), sha256=hashlib.sha256(dependency.read_bytes()).hexdigest())]
            record['repositories'][0]['checkouts'][0]['clients'][0]['configuration']['shared_dependencies'] = [str(dependency)]
        apply(d4, request)
        authority = d4.repo / '.vibe-coding-workflow/project.json'
        published = json.loads(authority.read_text())
        self.assertEqual(published, record)
        repository = published['repositories'][0];checkout = repository['checkouts'][0]
        identity = canonical_scope_projection(decode_project_record(published),
            repository_id=repository['repository_id'], checkout_id=checkout['checkout_id'], client='codex')
        self.recovery_fixtures.append((d4, request))
        if shared:
            self.assertNotEqual(published['project_id'], other_request['manifest']['enrollment']['record']['project_id'])
            self.recovery_fixtures.append((other, other_request))
        self.recovery_index = 0
        # Internal scope from actual configured publication; no native activation is claimed.
        return AcceptedScope(published['project_id'], repository['repository_id'], checkout['checkout_id'],
            Path(published['project_root']), authority, _canonical_freeze(changes._scoped_target(d4.repo, str(authority.relative_to(d4.repo)))),
            published['revision'], d4.repo, Path(repository['primary_root']), Path(checkout['git_dir']),
            Path(repository['git_common_dir']), _canonical_freeze(repository['binding'] | checkout['binding']), 'codex',
            identity['configuration']['policy_digest'], identity['configuration']['runtime_digest'], RuntimeSettings(3, storage),
            identity, onboarding._digest(_canonical_plain(identity)),
            _canonical_freeze(dict(kind='trusted-coordinator', **request['approval']['evidence'][0])))

    def recovery(self,task):
        from runtime import changes
        api=self.fixture.api;context=api._evidence_context(self.scope)
        ref=api._canonical_plain(api._artifact_owner(self.scope,task).task)
        d4,request=self.recovery_fixtures[self.recovery_index];self.recovery_index+=1
        candidate=api.capture_candidate(self.scope,api._load_plan(self.scope,self.fixture.identity))
        result=_run_d4_fixture(d4,request,'resume');self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(api.capture_candidate(self.scope,api._load_plan(self.scope,self.fixture.identity)),candidate)
        old,source=self.recovery_head(d4,context)
        self.assertTrue(any(item['reference']==ref for item in source['tasks']))
        with self.assertRaises(StartupError):api._observe_closure((context,),ref)
        return d4,request,context,ref,source,old

    def recovery_head(self,d4,context):
        from runtime import changes
        _,heads=changes._recovery_sources(context)
        selected=[(pointer,content) for pointer,content in heads if content['identity']['plan_dir']==str(d4.directory)]
        self.assertEqual(len(selected),1)
        return selected[0]


class CanonicalEvidenceTests(D4RecoveryFixture, unittest.TestCase):
    def setUp(self, *, d4_recovery=False, shared=False):
        from tests.test_state import canonical_scope
        self.fixture = EvidenceTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.scope = self.enroll_recovery(shared=shared) if d4_recovery else self.fixture.scope
        self.scope = self.fixture.scope
        state.start_task(self.scope, self.fixture.identity | dict(mode='editable', decision_ref='user:accepted'))
        self.fixture.plan()

    def test_canonical_candidate_carries_immutable_selected_identity_and_rejects_replaced_git(self):
        import shutil
        plan = self.fixture.api._load_plan(self.scope, self.fixture.identity)
        candidate = self.fixture.api.capture_candidate(self.scope, plan)
        self.assertEqual(candidate['accepted_identity']['checkout_id'], self.scope.checkout_id)
        self.assertEqual(candidate['pins']['accepted_scope_digest'], self.scope.accepted_scope_digest)
        old = self.fixture.root / 'old-git'
        self.scope.git_common_dir.rename(old)
        shutil.copytree(old, self.scope.git_common_dir)
        with self.assertRaises(state.StateError): self.fixture.api.capture_candidate(self.scope, plan)

    def test_canonical_check_reconciliation_uses_original_four_key_pins(self):
        result = self.fixture.checked()
        self.assertEqual(result['attempt']['status'], 'passed')
        with state._database(self.scope) as db:
            from runtime import delegation
            record = delegation._ledger(self.scope, db)[0]
        self.assertEqual(record['body']['accepted_identity']['checkout_id'], self.scope.checkout_id)
        self.assertEqual(record['body']['pins']['schema_version'], 3)

    def test_canonical_selected_dependency_is_reobserved_without_pinning_authority_revision(self):
        from runtime.startup import _canonical_plain, _canonical_freeze
        scope = self.scope
        shared = self.fixture.root / 'shared-guide.md'
        shared.write_text('accepted workflow\n')
        identity = _canonical_plain(scope.accepted_identity)
        identity['shared_policy'] = [dict(path=str(shared), sha256=hashlib.sha256(shared.read_bytes()).hexdigest())]
        scope = replace(scope, accepted_identity=_canonical_freeze(identity), accepted_scope_digest=self.fixture.api._digest(identity))
        plan = self.fixture.api._load_plan(self.scope, self.fixture.identity)
        before = self.fixture.api.capture_candidate(scope, plan)
        self.assertEqual(before, self.fixture.api.capture_candidate(replace(scope, authority_revision=scope.authority_revision+1), plan))
        shared.write_text('changed workflow\n')
        with self.assertRaises(state.StateError): self.fixture.api.capture_candidate(scope, plan)


class LinkedPlanTests(unittest.TestCase):
    setUp = CanonicalEvidenceTests.setUp

    def reference(self, task_id):
        directory = self.scope.runtime.state_dir
        def binding(path):
            info = path.stat()
            return dict(device=info.st_dev, inode=info.st_ino)
        return dict(authority=dict(directory=str(directory), database=str(directory / 'runtime.sqlite3'),
            directory_binding=binding(directory), database_binding=binding(directory / 'runtime.sqlite3')),
            scope_id=state._scope_id(self.scope), client='codex', session_id='parent', task_id=task_id)

    def linked_plan(self, own_delivery=None, companions=()):
        from copy import deepcopy
        plan = deepcopy(self.fixture.api._load_plan(self.scope, self.fixture.identity))
        plan.update(version=2, linked=dict(acceptance=[dict(id='integrated', text='Both changes work together')],
            delivery=own_delivery, companions=list(companions)))
        return plan


class ArtifactLifecycleTests(unittest.TestCase):
    setUp = CanonicalEvidenceTests.setUp

    def owner(self):
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        return self.fixture.api._artifact_owner(self.scope, task)


    def test_arbitrary_owner_mapping_and_foreign_connection_cannot_register(self):
        api = self.fixture.api
        with self.assertRaises(state.StateError):
            api._artifact(self.scope, dict(result='unowned'), owner=dict(kind='project', project_id='fixture', task=None))
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(':memory:')) as foreign:
            with self.assertRaises(state.StateError):
                api._artifact(self.scope, dict(result='foreign'), owner=self.owner(), db=foreign)


class RecoveryContextTests(unittest.TestCase):
    setUp = CanonicalEvidenceTests.setUp
    owner = ArtifactLifecycleTests.owner

    def test_existing_context_reads_managed_artifact_and_baseline_without_scope(self):
        api=self.fixture.api
        reference=api._artifact(self.scope,dict(result='observed'),owner=self.owner())
        with state._database(self.scope) as db:
            observed=api._evidence_context(self.scope)
            context=api._existing_authority_context(dict(observed.authority_set[0]),db=db)
            self.assertEqual(api._read(None,reference,context=context,db=db),dict(result='observed'))
            baseline=state._source_baseline(None,db,context=context)
            self.assertEqual(baseline['content']['source_contract'],2)
            with self.assertRaises(state.StateError): api._read(self.scope,reference,context=context,db=db)
        path=self.scope.runtime.state_dir/'evidence'/reference['name']
        raw=path.read_bytes(); replacement=path.with_name('replacement'); replacement.write_bytes(raw); replacement.chmod(0o400); replacement.replace(path)
        with self.assertRaises(state.StateError): api._read(None,reference,context=context)

    def test_existing_context_rejects_database_binding_substitution(self):
        api=self.fixture.api
        with state._database(self.scope) as db:
            authority=dict(api._evidence_context(self.scope).authority_set[0]); authority['database_binding']=dict(device=0,inode=0)
            with self.assertRaises(state.StateError): api._existing_authority_context(authority,db=db)

    def test_historical_ledger_and_linked_attempt_route_every_read_through_context(self):
        from runtime import delegation
        self.fixture.checked()
        api=self.fixture.api; original=api._read; calls=[]
        with state._database(self.scope) as db:
            context=api._existing_authority_context(state._authority_identity(state._path(self.scope)),db=db)
            def read(scope,reference,**kwargs):
                self.assertIsNone(scope); self.assertIs(kwargs['context'],context); self.assertIs(kwargs['db'],db)
                calls.append(reference)
                return original(scope,reference,**kwargs)
            with patch.object(api,'_read',side_effect=read):
                records=delegation._ledger(None,db,context=context)
                task=state._task(db,state._scope_id(self.scope),'codex','parent')
                for attempt in task['evidence']['attempts']:
                    delegation._linked_attempt(None,state._scope_id(self.scope),task,attempt,records,context=context,db=db)
        self.assertTrue(calls)


class AssignedCancelledClosureTests(unittest.TestCase):
    """Real local check/reconciliation artifacts; synthetic coordinator stop authority."""
    setUp = CanonicalEvidenceTests.setUp

    def stopped(self):
        api=self.fixture.api
        result=self.fixture.checked()
        task=state.task_status(self.scope,'codex','parent')['task']
        proof=dict(version=1,kind='stopped-writer-reconciliation',identity=self.fixture.identity,
            expected_revision=task['revision'],expected_pins=task['pins'],observed_turn_id=task['turn_id'],
            stopped=True,descendants_stopped=True,attempt_ids=[],observation_ref='fixture:checked-stop',coordinator_ref='fixture:independent')
        raw=json.dumps(proof).encode();path=self.fixture.root/'cancel-proof.json';path.write_bytes(raw);path.chmod(0o400)
        task=state.retire_task(self.scope,self.fixture.request(expected_pins=task['pins'],reason='Cancelled after independent stop',
            reference='fixture:cancel',stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())))['task']
        context=api._evidence_context(self.scope)
        ref=api._canonical_plain(api._artifact_owner(self.scope,task).task)
        return task,context,ref,result['attempt']

    def close(self,task,context,ref):
        api=self.fixture.api
        return api.close_task(self.scope,self.fixture.request(expected_pins=task['pins']),api._observe_closure((context,),ref))['task']

    def test_actual_check_values_and_original_ages_survive_atomic_assignment_retirement(self):
        api=self.fixture.api;task,context,ref,attempt=self.stopped()
        closed=self.close(task,context,ref);compact=api._read(self.scope,closed['lifecycle']['compact'])
        self.assertEqual(compact['outcome'],'CANCELLED');self.assertEqual(compact['usage'],'unknown')
        self.assertEqual(len(compact['stopped']['assignments']),1)
        numbers={item['field']:item for item in compact['numerical']}
        for field,unit in (('exit_code','exit-code'),('output_bytes','bytes')):
            self.assertEqual(numbers[field]['value'],attempt[field]);self.assertEqual(numbers[field]['unit'],unit)
            self.assertEqual(numbers[field]['source'],attempt['execution'])
        with api._context_database(context) as db:
            age=state._metadata(db,attempt['execution']['name'])['created_at']
        self.assertEqual(numbers['output_bytes']['created_at'],age)
        api._retire_closed_task(context,ref)
        with api._context_database(context) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0],0)
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0],0)
            marker=state._retired_assignment(db,attempt['id'])
            certificate=compact['stopped']['assignments'][0]
            self.assertEqual(marker['created_at'],certificate['created_at'])
            self.assertEqual(marker['request_digest'],certificate['request_digest'])
            self.assertEqual(marker['outcome'],'completed')
        with self.assertRaises(state.StateError):
            api.run_check(self.scope,self.fixture.identity|dict(expected_revision=task['revision'],check_id='unit'))


    def test_failure_after_assignment_markers_rolls_back_assignments_and_task(self):
        api=self.fixture.api;task,context,ref,attempt=self.stopped();self.close(task,context,ref)
        original=state._retired_record
        def fail(kind,*args):
            if kind=='task':raise state.StateError('fixture:after assignment marker')
            return original(kind,*args)
        with patch.object(state,'_retired_record',side_effect=fail):
            with self.assertRaises(state.StateError):api._retire_closed_task(context,ref)
        with api._context_database(context) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from retired_identities').fetchone()[0],0)


class ReadyClosureTests(D4RecoveryFixture, unittest.TestCase):
    """Real ready verification and owner stop; fixture host authority is unqualified."""
    def setUp(self):
        CanonicalEvidenceTests.setUp(self, d4_recovery=
            self._testMethodName == 'test_actual_ready_d4_cut_precedes_closure_and_retired_raw_removal')

    def ready(self):
        from runtime.engine import RuntimeEvent
        f=self.fixture
        for kind in ('PreToolUse','PostToolUse'):
            state.process_event(self.scope,RuntimeEvent(kind,'codex','parent',f.repo,turn_id='turn',tool_call_id='fixture-read',
                tool_name='read_file',receipt_fingerprint='a'*64))
        f.checked();f.review()
        result=f.verify();self.assertEqual(result['task']['state'],'DONE',result)
        return result['task']

    def stop_request(self,task,**proof_updates):
        api=self.fixture.api;context=api._evidence_context(self.scope)
        with api._context_database(context) as db:bindings=state._ready_stop_bindings(context,db,task)
        verification=api._read(self.scope,task['evidence']['verification'])
        proof=dict(version=2,kind='stopped-writer-reconciliation',identity=self.fixture.identity,
            expected_revision=task['revision'],expected_pins=task['pins'],observed_turn_id=task['turn_id'],
            stopped=True,descendants_stopped=True,attempt_ids=[a['id'] for a in task['evidence']['attempts']],
            observation_ref='fixture:independent-complete-stop',coordinator_ref='fixture:host-checked',
            candidate_digest=verification['candidate_digest'],verification_digest=task['evidence']['verification']['digest'],**bindings)
        proof.update(proof_updates);raw=json.dumps(proof).encode();path=self.fixture.root/('ready-stop-'+hashlib.sha256(raw).hexdigest()+'.json')
        path.write_bytes(raw);path.chmod(0o400)
        return self.fixture.identity|dict(expected_revision=task['revision'],expected_pins=task['pins'],stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()))

    def stopped(self):
        task=self.ready();request=self.stop_request(task)
        result=state._retain_ready_stop(self.scope,request)
        self.assertEqual(result['task']['state'],'DONE');self.assertEqual(result['task']['revision'],task['revision'])
        self.assertEqual(result['task']['lifecycle'],task['lifecycle'])
        return result['task'],request

    def test_real_ready_stop_closes_and_atomically_retires_with_original_outcome(self):
        api=self.fixture.api;task,request=self.stopped();context=api._evidence_context(self.scope)
        ref=api._canonical_plain(api._artifact_owner(self.scope,task).task)
        closed=api.close_task(self.scope,self.fixture.request(expected_pins=task['pins']),api._observe_closure((context,),ref))['task']
        self.assertEqual(closed['state'],'DONE');self.assertEqual(closed['revision'],task['revision'])
        compact=api._read(self.scope,closed['lifecycle']['compact'])
        self.assertEqual(compact['outcome'],'DONE');self.assertEqual(compact['usage'],'unknown')
        self.assertEqual(len(compact['stopped']['assignments']),3)
        self.assertTrue(compact['reviews']);self.assertTrue(compact['numerical']);self.assertTrue(compact['verifications'])
        api._retire_closed_task(context,ref)
        with api._context_database(context) as db:
            self.assertEqual(state._metadata(db,closed['lifecycle']['compact']['name'])['closed_at'],closed['lifecycle']['safe_closed_at'])
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0],0)
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0],0)
            self.assertEqual(db.execute('select count(*) from turns').fetchone()[0],0)
            self.assertEqual(db.execute('select count(*) from receipts').fetchone()[0],0)
            self.assertIsNotNone(state._retired(db,'turn',dict(scope_id=ref['scope_id'],client='codex',session_id='parent',turn_id='turn')))
            self.assertEqual(state._retired(db,'task',{k:ref[k] for k in ('scope_id','client','session_id','task_id')})['outcome'],'DONE')
            from runtime import changes
            changes._check_stopped_certificate(context,db,compact['stopped'])


        # Disposable failed registration leaves its artifact an unknown protected orphan.


    def source_scenario(self,*,shared=False):
        from tests.test_state import canonical_scope
        from runtime.startup import _canonical_plain,_canonical_freeze
        import shutil
        scenario=ReadyClosureTests(methodName='runTest');scenario.fixture=EvidenceTests(methodName='runTest')
        scenario.fixture.setUp();scenario.addCleanup(scenario.fixture.doCleanups)
        original_package=state.PACKAGE;package=scenario.fixture.root/'installed'
        for source in [*state._runtime_sources(),*(original_package/'policies').glob('*.md')]:
            target=package/source.relative_to(original_package);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        patcher=patch.object(state,'PACKAGE',package);patcher.start();scenario.addCleanup(patcher.stop)
        scope=canonical_scope(scenario.fixture.scope)
        dependency=scenario.fixture.root/'shared-guide.md';dependency.write_text('accepted workflow')
        if shared:
            identity=_canonical_plain(scope.accepted_identity)
            identity['shared_policy']=[dict(path=str(dependency),sha256=hashlib.sha256(dependency.read_bytes()).hexdigest())]
            scope=replace(scope,accepted_identity=_canonical_freeze(identity),accepted_scope_digest=scenario.fixture.api._digest(identity))
        scenario.scope=scope;scenario.fixture.scope=scope
        state.start_task(scope,scenario.fixture.identity|dict(mode='editable',decision_ref='user:accepted'));scenario.fixture.plan()
        return scenario,package,dependency


    def test_ready_stop_rejects_incomplete_or_forged_independent_proof(self):
        task=self.ready()
        for delta in (dict(version=1),dict(stopped=False),dict(descendants_stopped=False),dict(attempt_ids=[]),
            dict(assignments_digest='f'*64),dict(attempts_digest='f'*64),dict(task_digest='f'*64),dict(candidate_digest='f'*64),
            dict(verification_digest='f'*64),dict(generation=True),dict(observed_turn_id='foreign-turn'),dict(expected_revision=task['revision']-1),
            dict(expected_pins=task['pins']|dict(policy_digest='f'*64)),dict(native_authority=True)):
            with self.subTest(delta=delta),self.assertRaises(StartupError):state._retain_ready_stop(self.scope,self.stop_request(task,**delta))
        with state._database(self.scope) as db:
            self.assertNotIn('writer_stop',state._task(db,state._scope_id(self.scope),'codex','parent','task'))

    def test_ready_source_mutation_during_stop_rolls_back(self):
        api=self.fixture.api;task=self.ready();request=self.stop_request(task);original=api._artifact
        def move_stop(scope,value,**kwargs):
            result=original(scope,value,**kwargs)
            if value.get('kind')=='stopped-writer-reconciliation':(self.fixture.repo/'app.py').write_text('changed during stop\n')
            return result
        with patch.object(api,'_artifact',side_effect=move_stop),self.assertRaises(StartupError):state._retain_ready_stop(self.scope,request)
        with state._database(self.scope) as db:self.assertNotIn('writer_stop',state._task(db,state._scope_id(self.scope),'codex','parent','task'))

    def test_ready_candidate_mutation_during_compact_registration_rolls_back(self):
        api=self.fixture.api;task,_=self.stopped();context=api._evidence_context(self.scope)
        ref=api._canonical_plain(api._artifact_owner(self.scope,task).task);observation=api._observe_closure((context,),ref)
        original=api._artifact
        def move_compact(scope,value,**kwargs):
            result=original(scope,value,**kwargs)
            if value.get('kind')=='task-closure':(self.fixture.repo/'app.py').write_text('changed during closure\n')
            return result
        with patch.object(api,'_artifact',side_effect=move_compact),self.assertRaises(StartupError):
            api.close_task(self.scope,self.fixture.identity|dict(expected_revision=task['revision'],expected_pins=task['pins']),observation)
        with api._context_database(context) as db:
            current=state._task(db,state._scope_id(self.scope),'codex','parent','task')
            self.assertEqual(current['state'],'DONE');self.assertEqual(current['revision'],task['revision'])
            self.assertIsNone(current['lifecycle']['safe_closed_at'])


class CancelledBarrierClosureTests(D4RecoveryFixture, unittest.TestCase):
    """Actual synchronous barriers; independent fixture host authority remains unqualified."""
    def setUp(self):
        consumers = {
            'test_cancelled_barrier_complete_stop_d4_cut_closure_and_retired_recovery',
            'test_independent_raw_consumer_protects_cancelled_task_after_d4_cut',
            'test_cancelled_source_mutation_during_d4_cut_keeps_raw_support',
            'test_cancelled_new_input_during_d4_cut_keeps_raw_support',
        }
        CanonicalEvidenceTests.setUp(self, d4_recovery=self._testMethodName in consumers,
            shared=self._testMethodName == 'test_independent_raw_consumer_protects_cancelled_task_after_d4_cut')

    ready = ReadyClosureTests.ready
    stop_request = ReadyClosureTests.stop_request

    def cancelled(self, *, failed=False, stale=False, older_failed=False):
        api=self.fixture.api
        if older_failed:
            self.fixture.checked();self.fixture.verify();self.fixture.review();task=self.fixture.verify()['task']
            self.assertEqual(task['state'],'DONE')
        elif failed:
            self.fixture.checked()
            task=self.fixture.verify()['task']
            self.assertNotEqual(task['state'],'DONE')
        else:task=self.ready()
        proof=dict(version=1,kind='stopped-writer-reconciliation',identity=self.fixture.identity,
            expected_revision=task['revision'],expected_pins=task['pins'],observed_turn_id=task['turn_id'],
            stopped=True,descendants_stopped=True,attempt_ids=[],observation_ref='fixture:legacy-stop',coordinator_ref='fixture:independent')
        raw=json.dumps(proof).encode();path=self.fixture.root/'legacy-cancel.json';path.write_bytes(raw);path.chmod(0o400)
        task=state.retire_task(self.scope,self.fixture.request(expected_pins=task['pins'],reason='Cancelled',reference='fixture:cancel',
            stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())))['task']
        if stale:(self.fixture.repo/'app.py').write_text('changed after historical verification\n')
        return task

    def stopped(self, **options):
        api=self.fixture.api;task=self.cancelled(**options)
        candidate=api.capture_candidate(self.scope,api._load_plan(self.scope,self.fixture.identity))
        request=self.stop_request(task,candidate_digest=api._digest(candidate))
        stopped=state._retain_ready_stop(self.scope,request)['task']
        for key in ('state','revision','retirement','pins','lifecycle'):self.assertEqual(stopped[key],task[key])
        return stopped,request


class CancelledClosureTests(unittest.TestCase):
    """Disposable owner-proof fixtures; no native stop authority is qualified here."""
    def setUp(self):
        from tests.test_state import StateTests,canonical_scope
        self.f=StateTests(methodName='runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.scope=canonical_scope(self.f.scope);self.scope=self.f.scope
        from runtime import evidence
        self.api=evidence
        with patch.object(state,'_owner_time',return_value=100):self.f.start()
        self.f.update(state='WORKING')
        self.f.event(kind='UserPromptSubmit',tool_call_id=None)
        self.f.event()
        task=self.f.status()['task']
        identity={k:task[k] for k in ('client','session_id','task_id','scope_ref')}
        proof=dict(version=1,kind='stopped-writer-reconciliation',identity=identity,expected_revision=task['revision'],
            expected_pins=task['pins'],observed_turn_id=task['turn_id'],stopped=True,descendants_stopped=True,
            attempt_ids=[],observation_ref='fixture:independent-stop',coordinator_ref='fixture:checked')
        raw=json.dumps(proof).encode();path=self.f.base/'stop.json';path.write_bytes(raw);path.chmod(0o400)
        self.task=state.retire_task(self.scope,identity|dict(expected_revision=task['revision'],expected_pins=task['pins'],
            reason='Stopped',reference='fixture:cancel',stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())))['task']
        self.context=self.api._evidence_context(self.scope)
        self.ref=self.api._canonical_plain(self.api._artifact_owner(self.scope,self.task).task)
        self.request=identity|dict(expected_revision=self.task['revision'],expected_pins=self.task['pins'])

    def close(self):
        observed=self.api._observe_closure((self.context,),self.ref)
        return self.api.close_task(self.scope,self.request,observed)

    def test_cancelled_dated_owner_closes_without_changing_revision_or_age(self):
        with patch.object(state,'_owner_time',return_value=200):result=self.close()
        task=result['task'];self.assertEqual(task['revision'],self.task['revision'])
        self.assertEqual(task['retirement'],self.task['retirement'])
        self.assertEqual(task['lifecycle']['created_at'],100)
        self.assertEqual(task['lifecycle']['safe_closed_at'],200)
        compact=self.api._read(self.scope,task['lifecycle']['compact'])
        self.assertEqual(compact['outcome'],'CANCELLED');self.assertEqual(compact['numerical'],[])
        self.assertEqual(compact['usage'],'unknown')
        with patch.object(state,'_owner_time',return_value=300):self.assertEqual(self.close()['task'],task)


class LinkedVerificationTests(D4RecoveryFixture, unittest.TestCase):
    """Synthetic trusted coordinator sources; actual disposable check receipts, no native qualification."""
    def setUp(self):
        from types import SimpleNamespace
        from tests.test_state import canonical_scope
        from tests.test_startup import CanonicalAcquisitionTests
        from runtime import evidence
        self.api = evidence
        self.members = []
        d4_recovery = self._testMethodName in {
            'test_linked_non_success_mixed_required_merged_history_survives_d4_retirement',
            'test_linked_no_verifier_native_history_survives_d4_retirement_and_guidance',
        }
        for number in range(2):
            f = EvidenceTests(methodName='runTest'); f.setUp(); self.addCleanup(f.doCleanups)
            f.git('add', '--', 'AGENTS.md', '.vibe-coding-workflow')
            f.git('commit', '-qm', 'fixture accepted enrollment')
            if d4_recovery and number == 0:
                self.current_record(f)
                self.fixture = f
                f.scope = self.enroll_recovery()
                if self._testMethodName == 'test_linked_non_success_mixed_required_merged_history_survives_d4_retirement':
                    with (f.scope.git_dir / 'info/exclude').open('a') as exclude:
                        exclude.write('\n/AGENTS.md\n')
                self.scope = f.scope
            state.start_task(f.scope, f.identity | dict(mode='editable', decision_ref='user:accepted'))
            if not (d4_recovery and number == 0):self.current_record(f)
            self.members.append(f)
        self.selected = self.members[0]
        self.establish()

    def current_record(self,f):
        if f.scope.authority_path.exists():
            return f.scope.authority_path
        from types import SimpleNamespace
        from tests.test_startup import CanonicalAcquisitionTests
        record,path=CanonicalAcquisitionTests.record(SimpleNamespace(repo=f.repo,base=f.root,assertIsNone=self.assertIsNone))
        identity=self.api._canonical_plain(f.scope.accepted_identity)
        record['project_id']=identity['project_id'];record['repositories'][0]['repository_id']=identity['repository_id']
        record['repositories'][0]['checkouts'][0]['checkout_id']=identity['checkout_id']
        record['repositories'][0]['checkouts'][0]['clients'][0]['configuration'].update(identity['configuration'])
        path.write_text(json.dumps(record))
        return path

    def task(self, f):
        with state._database(f.scope) as db:
            return state._task(db, state._scope_id(f.scope), 'codex', 'parent')

    def reference(self, f):
        return self.api._canonical_plain(self.api._artifact_owner(f.scope, self.task(f)).task)

    def establish(self, delivery=None):
        review = dict(applicability='inapplicable', reason='Trivial fixed accepted unit command', independent=False)
        for index, f in enumerate(self.members):
            peers = [dict(task=self.reference(peer), delivery=delivery if peer is self.selected else None)
                     for peer in self.members if peer is not f]
            updates=dict(version=2, nontrivial=False, reviews=dict(general=review, security=review),
                linked=dict(acceptance=[dict(id='together', text='Accepted changes work together')],
                    delivery=delivery if f is self.selected else None, companions=peers))
            task=self.task(f)
            if 'evidence' in task:
                plan=self.api._read(f.scope,task['evidence']['plan']);plan.update(updates)
                self.api.plan_evidence(f.scope,f.request(plan=plan,change_decision_ref='fixture:approved-linked-revision'))
            else:
                f.plan(**updates)
            result = f.checked()
            self.assertEqual(result['attempt']['status'], 'passed')

    def retained(self, f, value):
        return dict(authority=self.reference(f)['authority'],
            reference=self.api._artifact(f.scope, value, owner=self.api._artifact_owner(f.scope, self.task(f))))

    def input_rows(self):
        rows=[]
        for f in self.members:
            task=self.task(f); plan=self.api._read(f.scope,task['evidence']['plan'])
            rows.append(dict(task=self.reference(f),pins=task['pins'],plan_digest=self.api._digest(plan),
                candidate_digest=self.api._digest(self.api.capture_candidate(f.scope,plan))))
        return sorted(rows,key=lambda row:self.api._raw(row['task']))

    def observations(self, *, delivery=None, delivery_status='unknown', shared_status='passed'):
        inputs=self.input_rows(); digest=self.api._digest(inputs)
        plan=self.api._read(self.selected.scope,self.task(self.selected)['evidence']['plan'])
        projection=self.api._linked_projection(self.reference(self.selected),plan)
        decision=self.retained(self.selected,dict(version=1,kind='linked-scope-decision',
            projection_digest=self.api._digest(projection),decision_ref='user:accepted',
            reference='fixture:independently checked complete user scope',observed_at=100))
        integration=self.retained(self.selected,dict(version=1,kind='linked-integration-observation',
            inputs_digest=digest,criteria=dict(together=shared_status),observed_at=101,
            reference='fixture:synthetic independently observed integration for exact versions'))
        deliveries=[]
        if delivery is not None:
            candidate=self.api.capture_candidate(self.selected.scope,plan)
            source=self.retained(self.selected,dict(version=1,kind='linked-delivery-observation',
                delivery=delivery,status=delivery_status,revision=candidate['head'],observed_at=102,
                reference='fixture:synthetic coordinator-verified provider observation'))
            deliveries=[dict(delivery=delivery,status=delivery_status,source=source,
                observed_at=102,revision=candidate['head'])]
        def observe(facts):
            self.assertEqual(facts['inputs_digest'],digest)
            return dict(scope_decision=decision,shared=[dict(id='together',status=shared_status,
                evidence=[integration],reference='fixture:semantic acceptance independently checked')],deliveries=deliveries)
        return dict(members=[dict(scope=f.scope,request=f.request(purpose='completion')) for f in self.members],observe=observe)

    def verify(self, observations=None):
        return self.api.verify_linked(self.selected.scope,self.selected.request(purpose='completion'),
            observations or self.observations())

    def stop_request(self,f,pointer,**updates):
        task=self.task(f);context=self.api._evidence_context(f.scope)
        with self.api._context_database(context) as db:bindings=state._ready_stop_bindings(context,db,task)
        plan=self.api._read(f.scope,task['evidence']['plan']);candidate=self.api.capture_candidate(f.scope,plan)
        proof=dict(version=2,kind='stopped-writer-reconciliation',identity=f.identity,expected_revision=task['revision'],
            expected_pins=task['pins'],observed_turn_id=task['turn_id'],stopped=True,descendants_stopped=True,
            attempt_ids=[a['id'] for a in task['evidence']['attempts']],observation_ref='fixture:independent-linked-stop',
            coordinator_ref='fixture:host-checked',candidate_digest=self.api._digest(candidate),
            verification_digest=task['evidence']['verification']['digest'],**bindings)
        with self.api._linked_stop_guards(pointer,self.api._canonical_plain(context.authority_set)) as authorities:
            proof['linked_digest']=self.api._digest(self.api._linked_stop_basis(pointer,authorities))
        proof.update(updates)
        raw=json.dumps(proof).encode();path=f.root/('linked-stop-'+hashlib.sha256(raw).hexdigest()+'.json');path.write_bytes(raw);path.chmod(0o400)
        return f.identity|dict(expected_revision=task['revision'],expected_pins=task['pins'],linked_verification=pointer,
            stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()))

    def cancel_linked_members(self,cancel=(0,1)):
        originals=[]
        for index,f in enumerate(self.members):
            task=self.task(f)
            if index in cancel:
                proof=dict(version=1,kind='stopped-writer-reconciliation',identity=f.identity,expected_revision=task['revision'],expected_pins=task['pins'],
                    observed_turn_id=task['turn_id'],stopped=True,descendants_stopped=True,attempt_ids=[],observation_ref='fixture:independent-non-success-cancellation',coordinator_ref='fixture:host-checked')
                raw=json.dumps(proof).encode();path=f.root/'non-success-cancel.json';path.write_bytes(raw);path.chmod(0o400)
                task=state.retire_task(f.scope,f.request(expected_pins=task['pins'],reason='Settled failed linked work',reference='fixture:cancelled-non-success',
                    stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())))['task']
            originals.append(task)
        return originals

    def non_success_cancelled_component(self,*,shared_status='failed',cancel=(0,1)):
        result=self.verify(self.observations(shared_status=shared_status));pointer=result['verification']
        self.assertFalse(result['implementation_verified']);self.assertFalse(result['merge_authority']);self.assertTrue(result['delivery_complete'])
        self.assertTrue(result['blockers']);self.assertTrue(all(self.task(f)['state']!='DONE' for f in self.members))
        return pointer,self.cancel_linked_members(cancel)


    def no_verifier_capture(self,observations=None):
        observations=observations or self.observations(shared_status='unknown');inputs=self.input_rows()
        plan=self.api._read(self.selected.scope,self.task(self.selected)['evidence']['plan'])
        projection=self.api._linked_projection(self.reference(self.selected),plan)
        acquired=observations['observe'](dict(projection=projection,projection_digest=self.api._digest(projection),inputs=inputs,inputs_digest=self.api._digest(inputs)))
        self.assertEqual(acquired['shared'][0]['status'],'unknown')
        originals=self.cancel_linked_members(tuple(range(len(self.members))))
        observations=dict(members=[dict(scope=f.scope,request=f.request(purpose='completion')) for f in self.members],observe=observations['observe'])
        from runtime import delegation
        for name,target in (('_verify_member',self.api),('_reserve_operation',delegation)):
            guard=patch.object(target,name,side_effect=AssertionError('No-verifier lifecycle executed verifier/barrier'));guard.start();self.addCleanup(guard.stop)
        captured=self.api._acquire_linked_basis(self.selected.scope,self.selected.request(purpose='completion'),observations)
        return captured,originals,observations

    def no_verifier_stop_request(self,f,captured,**updates):
        task=self.task(f);self.assertNotIn('verification',task['evidence']);context=self.api._evidence_context(f.scope)
        with self.api._context_database(context) as db:bindings=state._ready_stop_bindings(context,db,task)
        plan=self.api._read(f.scope,task['evidence']['plan']);candidate=self.api.capture_candidate(f.scope,plan)
        proof=dict(version=2,kind='stopped-writer-reconciliation',identity=f.identity,expected_revision=task['revision'],expected_pins=task['pins'],
            observed_turn_id=task['turn_id'],stopped=True,descendants_stopped=True,attempt_ids=[a['id'] for a in task['evidence']['attempts']],
            observation_ref='fixture:independent-no-verifier-stop',coordinator_ref='fixture:host-checked',candidate_digest=self.api._digest(candidate),
            verification_digest=None,linked_digest=self.api._digest(captured['basis']),**bindings)
        proof.update(updates)
        raw=json.dumps(proof).encode();path=f.root/('no-verifier-stop-'+hashlib.sha256(raw).hexdigest()+'.json');path.write_bytes(raw);path.chmod(0o400)
        return f.identity|dict(expected_revision=task['revision'],expected_pins=task['pins'],stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()))

    def no_verifier_stops(self,observations=None):
        captured,originals,_=self.no_verifier_capture(observations)
        for index,f in enumerate(self.members):
            stopped=state._retain_ready_stop(f.scope,self.no_verifier_stop_request(f,captured),linked_basis=captured['reference'])['task']
            self.assertEqual(stopped['state'],originals[index]['state'])
        contexts=tuple(self.api._evidence_context(f.scope) for f in self.members);refs=tuple(self.reference(f) for f in self.members)
        return captured,originals,contexts,refs


    def existing_aggregate_cannot_become_absent(self,*,historical=False):
        result=self.verify(self.observations(shared_status='failed'));pointer=result['verification'];original=self.api._linked_source(pointer)
        self.assertFalse(result['implementation_verified'])
        if historical:
            f=self.selected;plan=self.api._read(f.scope,self.task(f)['evidence']['plan'])
            self.api.plan_evidence(f.scope,f.request(plan=plan,change_decision_ref='user:actual-plan-history'))
            self.assertNotIn('linked_verification',self.task(f)['evidence'])
            self.assertEqual(self.api._linked_source(pointer),original)
        self.cancel_linked_members();observations=self.observations(shared_status='unknown')
        before=[self.task(f) for f in self.members]
        with self.assertRaisesRegex(StartupError,'aggregate requires its actual v1 lifecycle source'):
            self.api._acquire_linked_basis(self.selected.scope,self.selected.request(purpose='completion'),observations)
        self.assertEqual([self.task(f) for f in self.members],before);self.assertEqual(self.api._linked_source(pointer),original)


    def preparation_fixture(self, *, delivery=None, delivery_status='merged', stopped=True):
        from runtime import changes,onboarding
        from tests.test_changes import ScopedChangesTests
        if delivery is not None:self.establish(delivery)
        from runtime.engine import RuntimeEvent
        for f in self.members:
            for kind in ('PreToolUse','PostToolUse'):
                state.process_event(f.scope,RuntimeEvent(kind,'codex','parent',f.repo,turn_id='turn',tool_call_id='preparation-read',tool_name='read_file',receipt_fingerprint='a'*64))
        pointer=self.verify(self.observations(delivery=delivery,delivery_status=delivery_status))['verification']
        contexts=tuple(self.api._evidence_context(f.scope) for f in self.members);refs=tuple(self.reference(f) for f in self.members)
        d4=ScopedChangesTests(methodName='runTest');d4.setUp();self.addCleanup(d4.doCleanups)
        request=d4.scoped_request()
        for repository in request['manifest']['enrollment']['record']['repositories']:
            for checkout in repository['checkouts']:checkout['clients'][0]['configuration']['runtime']=dict(schema_version=3,state_dir=str(contexts[0].directory))
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        self.assertEqual(changes._run_scoped(d4.directory,request,'apply')['exit_code'],0)
        source=changes._observe_recovery_source(d4.directory,changes._load_journal(d4.directory),contexts,refs)
        for context in contexts:
            with self.api._context_database(context,write=True) as db:self.api._register_recovery_source(None,db,source,context=context)
        if stopped:
            for f in self.members:state._retain_ready_stop(f.scope,self.stop_request(f,pointer))
        return pointer,d4,source,contexts,refs

    def raw_rows(self,contexts):
        result=[]
        for context in contexts:
            with self.api._context_database(context) as db:
                result.append({name:db.execute('select * from '+name+' order by rowid').fetchall() for name in ('tasks','assignments','turns','receipts','bindings','retired_identities')})
        return result


    def preparation_stops(self,pointer,skip=None):
        for index,f in enumerate(self.members):
            if index!=skip:state._retain_ready_stop(f.scope,self.stop_request(f,pointer))
        return list({self.api._raw(self.reference(f)['authority']):self.reference(f)['authority'] for f in self.members}.values())

    def preparation_receipts(self,contexts):
        receipts=[]
        for context in contexts:
            with self.api._context_database(context) as db:
                for row in db.execute("select name,digest,owner_key,class,created_at,closed_at,status,bytes,data from evidence_metadata where class='compact'"):
                    ref=dict(name=row[0],digest=row[1]);body=self.api._read(None,ref,context=context,db=db)
                    if body.get('kind')=='linked-closure-preparation':receipts.append(dict(reference=ref,metadata=state._metadata_record(row),body=body))
        return receipts


    def linked_closure_fixture(self):
        from runtime import changes
        pointer,d4,source,contexts,refs=self.preparation_fixture()
        observed=self.api._observe_linked_preparation(pointer,self.api._canonical_plain(source.content['authorities']))
        self.assertTrue(self.api._prepare_linked_closure(observed)['complete'])
        released=changes._register_recovery_release(changes._observe_recovery_release(source.content['authorities'],dict(source.content['identity'])))
        self.assertTrue(released['complete'],released)
        return pointer,d4,source,contexts,refs

    def linked_closure_request(self,index):
        task=self.task(self.members[index])
        return {key:task[key] for key in ('client','session_id','task_id','scope_ref')}|dict(expected_revision=task['revision'],expected_pins=task['pins'])

    def linked_closure_candidates(self,context,reference):
        found=[]
        with self.api._context_database(context) as db:
            for row in db.execute("select name,digest,owner_key,class,created_at,closed_at,status,bytes,data from evidence_metadata where class='compact'"):
                metadata=state._metadata_record(row)
                if metadata['owner'].get('task')!=reference:continue
                ref=dict(name=row[0],digest=row[1]);body=self.api._read(None,ref,context=context,db=db)
                if body.get('kind')=='task-closure':found.append(dict(reference=ref,metadata=metadata,content=body))
        return found


    def linked_closure_late_change(self,kind):
        _,_,_,contexts,refs=self.linked_closure_fixture()
        first=self.api.close_task(self.members[0].scope,self.linked_closure_request(0),self.api._observe_closure(contexts,refs[0]))['task']
        peer=self.task(self.members[1]);before=self.raw_rows(contexts);observed=self.api._observe_closure(contexts,refs[1]);close=state._close_lifecycle;changed=[]
        def move(db,ref,observation,compact):
            if kind=='peer':(self.members[0].repo/'app.py').write_text('changed after closure observation\n')
            else:
                basis=self.api._read(self.members[0].scope,first['writer_stop']['linked'])
                source=next(row for row in basis['sources'] if self.api._linked_source(row['reference']).get('kind')=='linked-integration-observation')
                path=Path(source['metadata']['managed']['path']);replacement=path.with_name(path.name+'.replacement')
                replacement.write_bytes(path.read_bytes());replacement.chmod(0o400);os.replace(replacement,path)
            changed.append(True);return close(db,ref,observation,compact)
        with patch.object(state,'_close_lifecycle',side_effect=move),self.assertRaises(StartupError):
            self.api.close_task(self.members[1].scope,self.linked_closure_request(1),observed)
        self.assertEqual(changed,[True]);self.assertEqual(self.raw_rows(contexts),before)
        self.assertEqual(self.task(self.members[0]),first);self.assertEqual(self.task(self.members[1]),peer)
        self.assertEqual(len(self.linked_closure_candidates(contexts[1],refs[1])),1)
        with self.assertRaises(StartupError):self.api._observe_closure(contexts,refs[1])
        with patch.object(self.api,'_capture',side_effect=AssertionError('Historical replay captured current readiness')):
            status=state.task_status(self.members[0].scope,'codex','parent')
            replay=self.api.close_task(self.members[0].scope,self.linked_closure_request(0),self.api._observe_closure(contexts,refs[0]))['task']
        self.assertEqual(status['task'],first);self.assertTrue(status['historical']['historical']);self.assertFalse(status['historical']['current'])
        self.assertEqual(replay,first)


    def linked_closure_pending_protection(self,kind):
        _,_,_,contexts,refs=self.linked_closure_fixture();before=[self.task(f) for f in self.members]
        observed=self.api._observe_closure(contexts,refs[0])
        with patch.object(state,'_close_lifecycle',side_effect=RuntimeError('fixture: before lifecycle publication')),self.assertRaises(RuntimeError):
            self.api.close_task(self.members[0].scope,self.linked_closure_request(0),observed)
        candidate=self.linked_closure_candidates(contexts[0],refs[0])[0];path=Path(candidate['metadata']['managed']['path'])
        if kind=='stale':
            f=self.members[0];state.start_task(f.scope,dict(client='codex',session_id='later-owner',task_id='later-task',scope_ref='user:later',mode='editable',decision_ref='user:accepted'))
        elif kind=='substituted':
            replacement=path.with_name(path.name+'.replacement');replacement.write_bytes(path.read_bytes());replacement.chmod(0o400);os.replace(replacement,path)
        else:
            lookalike=path.with_name('0'*32+'.json');lookalike.write_bytes(path.read_bytes());lookalike.chmod(0o400)
        with self.assertRaises(StartupError):self.api._observe_closure(contexts,refs[0])
        self.assertEqual([self.task(f) for f in self.members],before)
        with self.api._context_database(contexts[0]) as db:
            self.assertEqual(state._metadata(db,candidate['reference']['name']),candidate['metadata'])
        self.assertEqual(candidate['metadata']['created_at'],before[0]['lifecycle']['created_at']);self.assertIsNone(candidate['metadata']['closed_at'])


    def preparation_reader_fixture(self):
        pointer=self.verify()['verification'];authorities=self.preparation_stops(pointer)
        observed=self.api._observe_linked_preparation(pointer,authorities)
        prepared=self.api._prepare_linked_closure(observed);self.assertTrue(prepared['complete'])
        facts=self.api._canonical_plain(observed.facts);member=facts['members'][0]
        receipt=self.api._preparation_receipt(facts,member['reference'])
        certificate=dict(member['certificate'],preparation=dict(reference=receipt['artifact'],
            component_digest=self.api._digest(facts),authorities=authorities))
        return pointer,authorities,facts,self.api._reference_context(member['reference']['authority']),certificate


    def preparation_reader_late_change(self,kind):
        from runtime import changes
        pointer,authorities,facts,context,certificate=self.preparation_reader_fixture()
        with self.api._linked_stop_guards(pointer,authorities),self.api._context_database(context) as db, \
                self.api._linked_preparation_reader(authorities) as reader:
            changes._check_stopped_certificate(context,db,certificate,preparation_reader=reader)
            if kind=='peer':(self.members[1].repo/'late-reader-input.py').write_text('changed = True\n')
            elif kind=='source':
                path=Path(facts['sources'][0]['metadata']['managed']['path'])
            else:
                receipt=self.api._preparation_receipt(facts,facts['members'][1]['reference'])
                peer_context=self.api._reference_context(receipt['authority'])
                path=peer_context.directory/'evidence'/receipt['artifact']['name']
            if kind!='peer':
                replacement=path.with_name(path.name+'.replacement');replacement.write_bytes(path.read_bytes()+b'\n');os.replace(replacement,path)
            with self.assertRaises((state.StateError,StartupError)):reader.finish()
        self.assertFalse(reader.active);self.assertEqual(reader.entries,{})


    def test_multi_member_current_proof_sets_only_selected_implementation_ready(self):
        peer_before=self.task(self.members[1])
        result=self.verify()
        self.assertTrue(result['implementation_verified'])
        self.assertTrue(result['delivery_complete'])
        self.assertEqual(result['task']['state'],'DONE')
        self.assertIn('linked_verification',result['task']['evidence'])
        peer_after=self.task(self.members[1])
        self.assertNotIn('linked_verification',peer_after['evidence'])
        self.assertNotEqual(peer_after['state'],'DONE')
        self.assertEqual(peer_before['pins'],peer_after['pins'])
        self.assertEqual(state.task_status(self.selected.scope,'codex','parent')['task']['state'],'DONE')

    def test_open_delivery_does_not_block_current_implementation(self):
        delivery=dict(provider='fixture',repository='owner/repo',target='main',pull_request='fixture:pull1')
        self.establish(delivery)
        result=self.verify(self.observations(delivery=delivery,delivery_status='open'))
        self.assertTrue(result['implementation_verified'])
        self.assertFalse(result['delivery_complete'])
        self.assertFalse(result['merge_authority'])
        self.assertEqual(result['task']['state'],'DONE')

    def test_raw_passing_flags_cannot_supply_trusted_acquisition(self):
        observations=self.observations();observations['observe']=dict(complete=True,passed=True)
        with self.assertRaises(state.StateError):self.verify(observations)

    def test_missing_member_and_divergent_plan_reject(self):
        observations=self.observations();observations['members'].pop()
        with self.assertRaises(state.StateError):self.verify(observations)
        peer=self.members[1]
        plan=self.api._read(peer.scope,self.task(peer)['evidence']['plan'])
        plan['linked']['acceptance'][0]['text']='A different shared agreement'
        self.api.plan_evidence(peer.scope,peer.request(plan=plan,change_decision_ref='fixture:changed'))
        with self.assertRaises(state.StateError):self.verify()

    def test_peer_change_invalidates_selected_ready_without_mutating_peer(self):
        self.verify()
        peer=self.members[1];before=self.task(peer)
        (peer.repo/'app.py').write_text('value = 2\n')
        result=state.task_status(self.selected.scope,'codex','parent')
        self.assertEqual(result['task']['state'],'REVIEW')
        self.assertEqual(self.task(peer),before)


    def test_foreign_retained_source_is_guarded_before_consumption(self):
        from tests.test_state import canonical_scope
        from runtime import delegation
        extra=EvidenceTests(methodName='runTest');extra.setUp();self.addCleanup(extra.doCleanups)
        extra.scope=canonical_scope(extra.scope)
        state.start_task(extra.scope,extra.identity | dict(mode='editable',decision_ref='user:accepted'))
        self.current_record(extra)
        observations=self.observations();original_observe=observations['observe']
        source=self.retained(extra,dict(version=1,kind='linked-integration-observation',
            inputs_digest=self.api._digest(self.input_rows()),criteria=dict(together='passed'),observed_at=101,
            reference='fixture:existing independent source authority'))
        def observe(facts):
            acquired=original_observe(facts);acquired['shared'][0]['evidence']=[source];return acquired
        observations['observe']=observe
        original=self.api._linked_source
        def read(reference,**kwargs):
            if reference==source:
                with self.assertRaises(state.StateError):
                    with delegation._reservation_write(extra.scope):pass
            return original(reference,**kwargs)
        with patch.object(self.api,'_linked_source',side_effect=read):result=self.verify(observations)
        self.assertTrue(result['implementation_verified'])

    def test_observer_cannot_rewrite_original_linked_producer_request(self):
        observations=self.observations();original=observations['observe']
        request=self.selected.request(purpose='completion')
        def observe(facts):
            request['purpose']='merge-evidence'
            return original(facts)
        observations['observe']=observe
        result=self.api.verify_linked(self.selected.scope,request,observations)
        self.assertEqual(request['purpose'],'merge-evidence')
        self.assertTrue(result['implementation_verified'])
        self.assertEqual(result['task']['state'],'DONE')
        retained=self.api._linked_source(result['verification'])
        self.assertEqual(retained['purpose'],'completion')
        selected=next(row for row in retained['members'] if row['task']==retained['selected'])
        verification=self.api._read(self.selected.scope,selected['verification'])
        self.assertEqual(verification['purpose'],'completion')

    def test_callback_result_is_snapshotted_before_retained_source_reads(self):
        observations=self.observations();observe=observations['observe'];held=[]
        def acquire(facts):
            result=observe(facts);held.append(result);return result
        observations['observe']=acquire;original=self.api._linked_source
        def read(reference,**kwargs):
            if held:held[0]['shared'][0]['status']='failed'
            return original(reference,**kwargs)
        with patch.object(self.api,'_linked_source',side_effect=read):result=self.verify(observations)
        self.assertTrue(result['implementation_verified'])

    def test_peer_generation_change_during_publication_rejects_without_aggregate(self):
        from runtime.engine import RuntimeEvent
        observations=self.observations();original=self.api._artifact;changed=[];peer=self.members[1]
        def publish(scope,value,**kwargs):
            if value.get('kind')=='linked-verification' and not changed:
                changed.append(True)
                event=RuntimeEvent('PostToolUse','codex','parent',peer.repo,'fixture-turn','fixture-call','apply_patch',False,'fixture')
                state.process_event(peer.scope,event)
            return original(scope,value,**kwargs)
        with patch.object(self.api,'_artifact',side_effect=publish):
            with self.assertRaises(state.StateError):self.verify(observations)
        self.assertTrue(changed)
        self.assertNotIn('linked_verification',self.task(self.selected)['evidence'])

    def test_unknown_integration_source_cannot_pass_shared_criterion(self):
        observations=self.observations();observe=observations['observe']
        unknown=self.retained(self.selected,dict(version=1,kind='caller-passed-flags',passed=True))
        def acquire(facts):
            result=observe(facts);result['shared'][0]['evidence']=[unknown];return result
        observations['observe']=acquire
        result=self.verify(observations)
        self.assertFalse(result['implementation_verified'])
        self.assertEqual(result['task']['state'],'REVIEW')

    def test_merged_and_unknown_delivery_keep_independent_phase_results(self):
        delivery=dict(provider='fixture',repository='owner/repo',target='main',pull_request='fixture:pull1')
        self.establish(delivery)
        result=self.verify(self.observations(delivery=delivery,delivery_status='merged'))
        self.assertTrue(result['implementation_verified']);self.assertTrue(result['delivery_complete'])
        status=self.api.evidence_status(self.selected.scope,'codex','parent')['evidence']
        self.assertTrue(status['implementation_verified']);self.assertTrue(status['delivery_complete'])

    def test_failed_actual_member_check_blocks_implementation(self):
        peer=self.members[1];plan=self.api._read(peer.scope,self.task(peer)['evidence']['plan'])
        plan['checks'][0]['argv']=[sys.executable,'-c','raise SystemExit(1)']
        self.api.plan_evidence(peer.scope,peer.request(plan=plan,change_decision_ref='fixture:accepted-failing-command'))
        self.assertEqual(peer.checked()['attempt']['status'],'failed')
        result=self.verify()
        self.assertFalse(result['implementation_verified'])
        self.assertEqual(result['task']['state'],'REVIEW')


    def test_peer_generation_race_during_ready_publication_downgrades_claimant(self):
        from contextlib import contextmanager
        from runtime.engine import RuntimeEvent
        self.verify();original=state._database;peer=self.members[1];changed=[]
        @contextmanager
        def database(scope,write=False,initialize=False):
            if scope is self.selected.scope and write and not changed:
                changed.append(True)
                state.process_event(peer.scope,RuntimeEvent('PostToolUse','codex','parent',peer.repo,
                    'status-race-turn','status-race-call','apply_patch',False,'status-race'))
            with original(scope,write=write,initialize=initialize) as db:yield db
        with patch.object(state,'_database',side_effect=database):
            result=state.task_status(self.selected.scope,'codex','parent')
        self.assertTrue(changed)
        self.assertEqual(result['task']['state'],'REVIEW')


    def test_missing_selected_local_proof_downgrades_without_passing_history(self):
        self.verify();task=self.task(self.selected)
        reference=task['evidence']['verification']
        (self.selected.scope.runtime.state_dir/'evidence'/reference['name']).unlink()
        result=state.task_status(self.selected.scope,'codex','parent')
        self.assertEqual(result['task']['state'],'REVIEW')

    def test_missing_delivery_source_changes_only_delivery_phase(self):
        delivery=dict(provider='fixture',repository='owner/repo',target='main',pull_request='fixture:pull1')
        self.establish(delivery)
        observations=self.observations(delivery=delivery,delivery_status='merged')
        held=[];original=observations['observe']
        def observe(facts):
            result=original(facts);held.append(result['deliveries'][0]['source']);return result
        observations['observe']=observe
        self.assertTrue(self.verify(observations)['delivery_complete'])
        source=held[0]
        (Path(source['authority']['directory'])/'evidence'/source['reference']['name']).unlink()
        result=self.api.evidence_status(self.selected.scope,'codex','parent')
        self.assertEqual(result['task']['state'],'DONE')
        self.assertTrue(result['evidence']['implementation_verified'])
        self.assertFalse(result['evidence']['delivery_complete'])

    def test_unpublished_relevant_change_cannot_be_delivered_by_an_old_merged_revision(self):
        delivery=dict(provider='fixture',repository='owner/repo',target='main',pull_request='fixture:pull1')
        (self.selected.repo/'app.py').write_text('value = 2\n')
        self.establish(delivery)
        result=self.verify(self.observations(delivery=delivery,delivery_status='merged'))
        self.assertTrue(result['implementation_verified'])
        self.assertFalse(result['delivery_complete'])


    def test_actual_qualified_review_chains_revalidate_through_foreign_contexts(self):
        review=dict(applicability='required',reason='Independent fixture review',independent=True)
        for f in self.members:
            plan=self.api._read(f.scope,self.task(f)['evidence']['plan'])
            plan.update(nontrivial=True,reviews=dict(general=review,security=review))
            self.api.plan_evidence(f.scope,f.request(plan=plan,change_decision_ref='fixture:required-qualified-review'))
            self.assertEqual(f.checked()['attempt']['status'],'passed')
            f.review()
        result=self.verify()
        self.assertTrue(result['implementation_verified'])
        self.assertEqual(state.task_status(self.selected.scope,'codex','parent')['task']['state'],'DONE')


    def test_complete_scope_source_cannot_hide_an_omitted_third_member(self):
        observations=self.observations();observe=observations['observe']
        plan=self.api._read(self.selected.scope,self.task(self.selected)['evidence']['plan'])
        projection=self.api._linked_projection(self.reference(self.selected),plan)
        omitted=dict(self.reference(self.selected),task_id='approved-third-task')
        projection['participants'].append(dict(task=omitted,delivery=None))
        decision=self.retained(self.selected,dict(version=1,kind='linked-scope-decision',
            projection_digest=self.api._digest(projection),decision_ref='user:accepted',observed_at=100,
            reference='fixture:independent complete scope includes the omitted third task'))
        def acquire(facts):
            result=observe(facts);result['scope_decision']=decision;return result
        observations['observe']=acquire
        with self.assertRaises(state.StateError):self.verify(observations)
        self.assertNotIn('linked_verification',self.task(self.selected)['evidence'])

    def test_confirmed_foreign_security_finding_blocks_aggregate_implementation(self):
        peer=self.members[1]
        plan=self.api._read(peer.scope,self.task(peer)['evidence']['plan'])
        review=dict(applicability='required',reason='Independent security fixture review',independent=True)
        plan.update(nontrivial=True,reviews=dict(general=review,security=review))
        self.api.plan_evidence(peer.scope,peer.request(plan=plan,change_decision_ref='fixture:security-required'))
        self.assertEqual(peer.checked()['attempt']['status'],'passed')
        peer.review(findings=[dict(id='linked-security',severity='high',scope='security',status='confirmed',
            location='app.py:1',scenario='Reachable unsafe fixture path',evidence='Independent tracing',
            counterevidence='No applicable guard')])
        result=self.verify()
        self.assertFalse(result['implementation_verified'])
        self.assertTrue(any('security finding' in message for message in result['blockers']))


    def assert_final_source_loss_rejected(self, *, merge_evidence=False):
        if merge_evidence:
            for f in self.members:
                f.review()
        observations=self.observations();observe=observations['observe'];held=[]
        def acquire(facts):
            result=observe(facts);held.append(result['shared'][0]['evidence'][0]);return result
        observations['observe']=acquire
        if merge_evidence:
            for row in observations['members']:row['request']['purpose']='merge-evidence'
            result=self.api.verify_linked(self.selected.scope,self.selected.request(purpose='merge-evidence'),observations)
        else:
            result=self.verify(observations)
        self.assertTrue(result['implementation_verified'])
        before=self.task(self.selected)
        if merge_evidence:self.assertNotEqual(before['state'],'DONE')
        original=self.api._linked_outcomes;changed=[]
        def consume(*args,**kwargs):
            result=original(*args,**kwargs)
            if not changed:
                source=held[0]
                (Path(source['authority']['directory'])/'evidence'/source['reference']['name']).unlink()
                changed.append(True)
            return result
        with patch.object(self.api,'_linked_outcomes',side_effect=consume):
            if merge_evidence:
                result=self.api.evidence_status(self.selected.scope,'codex','parent')['evidence']
            else:
                result=self.api._revalidate_linked(self.selected.scope,before,update_ready=False)
        self.assertTrue(changed)
        self.assertFalse(result['implementation_verified'])
        self.assertEqual(self.task(self.selected),before)

    def test_read_only_revalidation_rechecks_sources_after_consumption(self):
        self.assert_final_source_loss_rejected()

    def test_non_ready_merge_evidence_status_rechecks_sources_after_consumption(self):
        self.assert_final_source_loss_rejected(merge_evidence=True)

    def test_implementation_source_bytes_change_during_registration_rejects(self):
        observations=self.observations();observe=observations['observe'];held=[]
        def acquire(facts):
            result=observe(facts);held.append(result['shared'][0]['evidence'][0]);return result
        observations['observe']=acquire;original=self.api._artifact;changed=[]
        def publish(scope,value,**kwargs):
            result=original(scope,value,**kwargs)
            if value.get('kind')=='linked-verification' and not changed:
                source=held[0];path=Path(source['authority']['directory'])/'evidence'/source['reference']['name']
                path.chmod(0o600);path.write_text('{"tampered":true}');path.chmod(0o400);changed.append(True)
            return result
        with patch.object(self.api,'_artifact',side_effect=publish):
            with self.assertRaises(state.StateError):self.verify(observations)
        self.assertTrue(changed);self.assertNotIn('linked_verification',self.task(self.selected)['evidence'])

    def test_scope_source_metadata_change_during_registration_rejects(self):
        observations=self.observations();observe=observations['observe'];held=[]
        def acquire(facts):
            result=observe(facts);held.append(result['scope_decision']);return result
        observations['observe']=acquire;original=self.api._artifact;changed=[]
        def publish(scope,value,**kwargs):
            result=original(scope,value,**kwargs)
            if value.get('kind')=='linked-verification' and not changed:
                source=held[0];db=kwargs['db'];metadata=state._metadata(db,source['reference']['name'])
                metadata['created_at']+=1
                db.execute('update evidence_metadata set created_at=?,data=? where name=?',
                    (metadata['created_at'],self.api._raw(metadata).decode(),source['reference']['name']))
                changed.append(True)
            return result
        with patch.object(self.api,'_artifact',side_effect=publish):
            with self.assertRaises(state.StateError):self.verify(observations)
        self.assertTrue(changed);self.assertNotIn('linked_verification',self.task(self.selected)['evidence'])

    def test_delivery_source_change_after_intent_prevents_publication(self):
        delivery=dict(provider='fixture',repository='owner/repo',target='main',pull_request='fixture:pull1')
        self.establish(delivery)
        observations=self.observations(delivery=delivery,delivery_status='merged');observe=observations['observe'];held=[]
        def acquire(facts):
            result=observe(facts);held.append(result['deliveries'][0]['source']);return result
        observations['observe']=acquire;original=self.api._artifact;changed=[]
        def publish(scope,value,**kwargs):
            result=original(scope,value,**kwargs)
            if value.get('kind')=='linked-verification' and not changed:
                source=held[0];path=Path(source['authority']['directory'])/'evidence'/source['reference']['name']
                path.chmod(0o600);path.write_text('{"tampered":true}');path.chmod(0o400);changed.append(True)
            return result
        with patch.object(self.api,'_artifact',side_effect=publish):
            with self.assertRaisesRegex(state.StateError, 'publication remains incomplete'):
                self.verify(observations)
        self.assertTrue(changed)
        self.assertNotIn('linked_verification', self.task(self.selected)['evidence'])
