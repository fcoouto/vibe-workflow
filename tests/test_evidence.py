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
from tests.fixture_evidence import EvidenceFixture


class EvidenceTests(EvidenceFixture, unittest.TestCase):

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

    def test_writer_first_prevents_verification_without_model_guidance(self):
        from runtime import delegation
        self.ready_proof_fixture()
        writer = delegation._reserve_operation(self.scope, self.readiness_owner(), role='orchestrator')
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
                    delegation._reserve_operation(scope, writer, role='orchestrator')
            return original(scope, plan)
        with patch.object(self.api, 'capture_candidate', side_effect=capture):
            result = self.verify()
        self.assertTrue(result['verified'])
        with state._database(self.scope) as db:
            records = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'orchestrator']
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
        first = delegation._reserve_operation(self.scope, writer, role='orchestrator')
        with state._database(self.scope) as db:
            current = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
        self.assertEqual(current['state'], 'REVIEW')
        self.assertEqual(current['revision'], ready['revision'] + 1)
        self.assertTrue(delegation._reserve_operation(self.scope, writer, role='orchestrator')['replayed'])
        failed = self.readiness_owner(session='third-parent')
        with self.assertRaises(state.StateError):
            delegation._reserve_operation(self.scope, failed, role='orchestrator')
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
            retained = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'orchestrator']
        self.assertEqual(len(retained), 1)
        self.assertNotEqual(retained[0]['data']['status'], 'settled')
        before = retained[0]['data']
        retry = self.verify()
        self.assertFalse(retry['verified'])
        self.assertNotEqual(retry['task']['state'], 'DONE')
        with state._database(self.scope) as db:
            self.assertEqual(next(r for r in delegation._ledger(self.scope, db) if r['id'] == retained[0]['id'])['data'], before)
        with self.assertRaises(state.StateError):
            delegation._reserve_operation(self.scope, self.readiness_owner(), role='orchestrator')
        current = state.task_status(self.scope, 'codex', 'parent')['task']
        with self.assertRaises(state.StateError):
            state.start_task(self.scope, self.identity | dict(task_id='replacement', mode='editable', decision_ref='user:accepted',
                replaces=dict(task_id='task', expected_revision=current['revision'])))

    def test_known_failed_verification_settles_only_own_read_barrier_and_same_task_reader_blocks(self):
        from runtime import delegation
        self.plan()
        unrelated = delegation._reserve_operation(self.scope, self.readiness_owner(claims=[
            dict(kind='service', key='fixture:independent', host='local', mode='read')]), role='orchestrator')
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
        delegation._reserve_operation(self.scope, reader, role='orchestrator')
        result = self.verify()
        self.assertFalse(result['verified'])
        self.assertTrue(any('ownership' in item for item in result['blockers']))
        with state._database(self.scope) as db:
            self.assertEqual(len(delegation._ledger(self.scope, db)), 3)
        self.assertNotIn('delegation', result['task'])

    def test_ready_shared_service_closure_preserves_independence_but_status_blocks_overlapping_reader(self):
        from runtime import delegation
        self.ready_proof_fixture()
        service = dict(kind='service', key='fixture:used-db', host='local', mode='write')
        plan = self.coordinator_resource_plan([dict(id='coordinator-service', role='orchestrator', claims=[service], dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        independent = self.readiness_owner(claims=[service | dict(key='fixture:independent-db')])
        delegation._reserve_operation(self.scope, independent, role='orchestrator')
        ready = self.verify()['task']
        self.assertEqual(ready['state'], 'DONE')
        verification = self.api._read(self.scope, ready['evidence']['verification'])
        self.assertIn(service | dict(mode='read'), verification['resource_closure'])
        self.assertFalse(self.api.evidence_status(self.scope, 'codex', 'parent')['evidence']['blockers'])
        reader = self.readiness_owner(session='reader', claims=[service | dict(mode='read')])
        delegation._reserve_operation(self.scope, reader, role='orchestrator')
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
        plan = self.coordinator_resource_plan([dict(id='new-dependency', role='orchestrator', claims=[dependency],
            dependencies=[], max_attempts=1)])
        delegation.plan_delegation(self.scope, self.request(plan=plan))
        owner = delegation._reserve_operation(self.scope, self.readiness_owner(claims=[dependency]), role='orchestrator')
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
        plan = self.coordinator_resource_plan([dict(id='dependency', role='orchestrator', claims=[dependency],
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
        plan = self.coordinator_resource_plan([dict(id='dependency', role='orchestrator', claims=[dependency],
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
        delegation._reserve_operation(foreign, request, role='orchestrator')
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
            records = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'orchestrator']
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
                admitted.append(delegation._reserve_operation(self.scope, reader, role='orchestrator'))
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
        delegation._reserve_operation(self.scope, independent, role='orchestrator')
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
        delegation._reserve_operation(self.scope, reader, role='orchestrator')
        result = self.verify()
        self.assertFalse(result['verified'])
        self.assertNotEqual(result['task']['state'], 'DONE')
        self.assertTrue(any('ownership' in item for item in result['blockers']))

    def test_verifier_deduplicates_accepted_root_alias_without_relaxing_external_claims(self):
        from runtime import delegation
        self.ready_proof_fixture()
        root = dict(kind='path', key=str(self.repo), mode='read')
        alias = root | dict(key=str(self.repo) + '/')
        plan = self.coordinator_resource_plan([dict(id='coordinator-root', role='orchestrator', claims=[alias], dependencies=[], max_attempts=1)])
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
        jobs = [dict(id='j' + str(job), role='orchestrator', dependencies=[], max_attempts=1,
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
            rows = [r for r in delegation._ledger(self.scope, db) if r['data']['role'] == 'orchestrator']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['data']['status'], 'settled')


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
            dict(id='edit', role='software-engineer', claims=claims, dependencies=[], max_attempts=1),
            dict(id='unit', role='check', claims=[native.path_claim(mode='read')], dependencies=['edit'], max_attempts=1),
            dict(id='review', role='code-reviewer', claims=[native.path_claim(mode='read')], dependencies=['unit'], max_attempts=1)])
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

    def _request(self, *, with_check=False, fixture=None):
        if fixture is None:
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
        # Rejections leave the task unchanged (asserted per case), so one fixture serves every case.
        fixture, _ = self._request(with_check=True)
        initial = fixture.request()['expected_revision']
        for target, field, value in (
                ('envelope', 'expected_revision', None), ('plan', 'criteria', None),
                ('envelope', 'unknown', True), ('plan', 'unknown', True), ('check', 'unknown', True)):
            with self.subTest(target=target, field=field):
                _, request = self._request(with_check=True, fixture=fixture)
                before = request['expected_revision']
                self.assertEqual(before, initial)
                with self.assertRaisesRegex(state.StateError, 'Evidence plan is missing'):
                    fixture.api._load_plan(fixture.scope, fixture.identity)
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
