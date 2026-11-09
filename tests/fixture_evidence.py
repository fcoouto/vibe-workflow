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


class EvidenceFixture:
    """Shared evidence fixture mixin; test classes supply the unittest base."""
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

    def coordinator_resource_plan(self, jobs):
        return dict(version=1, route=dict(mechanism='codex-native-subagent', client_surface='codex-cli', parent_session_id='parent',
            authority_ref='fixture:accepted-route', controls_ref='fixture:known-controls'),
            authority=dict(state_dir=str(self.scope.runtime.state_dir), participants_ref='fixture:shared-owners', ownership_known=True),
            selection_constraints=dict(complexity='Fixture', risk='Bounded', rationale='Explicit fixture plan', required_controls=['spawn']),
            limits=dict(max_attempts=1, max_resource_units=1, deadline=int(time.time()) + 600, max_parallel_workers=1,
                host_capacity=1, capacity_ref='fixture:capacity'), fallback=dict(action='stop', reference='user:accepted'),
            jobs=jobs)

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
