"""Workstreams protocol fixtures; no actual native dispatch or execution qualification."""
from tests.fixture_canonical_birth import test_acquisition
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib
import hashlib
import io
from contextlib import redirect_stdout
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import delegation, evidence, state
from tests import test_delegation


class WorkstreamsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_delegation.AdmissionTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope
        self.assertIsNotNone(importlib.util.find_spec('runtime.workstreams'), 'Workstreams runtime missing')
        self.api = importlib.import_module('runtime.workstreams')
        # Opt-in protocol cases retain an explicit saved disabled choice.
        current=self.api.control(self.scope,'status',dict(client='codex'))
        self.api.control(self.scope,'disable',dict(client='codex',expected_revision=current['revision']))
        self.limits = dict(context_id='WS42.01', rationale='One bounded parser outcome with review fixes',
            output_tokens=12000, context_tokens=64000, max_steps=40, max_seconds=1200,
            max_continuations=3, backoff_seconds=0)

    def control(self, action, revision=None):
        request = dict(client='codex')
        if revision is not None:
            request['expected_revision'] = revision
        return self.api.control(self.scope, action, request)

    def enable(self):
        status = self.control('status')
        return self.control('enable', status['revision'])

    def prepare(self):
        enabled = self.enable()
        self.fixture.plan()
        return self.fixture.prepare(workstream_revision=enabled['revision'], workstream_limits=self.limits)

    def continuation(self, prepared, **updates):
        proof = self.fixture.worker_reconciliation_request(prepared, outcome='failed')
        return self.fixture.request(attempt_id=prepared['attempt_id'], workstream_revision=self.control('status')['revision'],
            reason='follow-up', prompt='Finish the original accepted scope', human_continue=False,
            host_proof=proof['host_proof']) | updates

    def observe(self, prepared, status='completed', **updates):
        return self.fixture.observe_worker(prepared,
            self.fixture.lifecycle_observation(prepared, turn_status=status, **updates))

    def test_status_is_read_only_and_controls_are_scoped_idempotent_cas(self):
        before = {str(p): p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}
        self.assertEqual(self.control('status')['enabled'], False)
        after = {str(p): p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        first = self.enable()
        self.assertTrue(first['enabled'])
        self.assertEqual(self.control('enable', first['revision'])['revision'], first['revision'])
        with self.assertRaises(state.StateError):
            self.control('disable', 0)
        from tests.fixture_canonical_birth import fresh_canonical
        _,acquired,_=fresh_canonical(self)
        other=acquired.resolution.scope
        separate=self.api.control(other,'status',dict(client='codex'))
        self.assertTrue(separate['enabled'])
        self.api.control(other,'disable',dict(client='codex',expected_revision=separate['revision']))
        self.assertFalse(self.api.control(other,'status',dict(client='codex'))['enabled'])
        self.assertFalse(self.control('disable', first['revision'])['enabled'])

    def test_off_preserves_ordinary_d6_but_workstreams_require_opt_in(self):
        self.fixture.plan()
        with self.assertRaises(state.StateError):
            self.fixture.prepare(workstream_revision=0, workstream_limits=self.limits)
        ordinary = self.fixture.prepare()
        self.assertTrue(ordinary['launch_admitted'])
        self.assertNotIn('workstream_dispatch', self.fixture.raw_assignment(ordinary['attempt_id']))

    def test_workstream_prepare_reserves_and_disable_reenable_invalidates_unsent_gate(self):
        prepared = self.prepare()
        self.assertFalse(prepared['launch_admitted'])
        self.assertTrue(prepared['dispatch_prepared'])
        mode = self.control('status')
        self.assertEqual(mode['unresolved_assignments'], 1)
        disabled = self.control('disable', mode['revision'])
        self.assertFalse(disabled['drained'])
        self.enable()
        with self.assertRaises(state.StateError):
            self.api.consume_dispatch(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=mode['revision']))

    def test_dispatch_gate_is_durable_one_use_and_never_claims_native_delivery(self):
        prepared = self.prepare()
        request = self.fixture.request(attempt_id=prepared['attempt_id'], workstream_revision=self.control('status')['revision'])
        result = self.api.consume_dispatch(self.scope, request)
        self.assertTrue(result['send_admitted'])
        self.assertEqual(result['native_delivery'], 'unobserved')
        with self.assertRaises(state.StateError):
            self.api.consume_dispatch(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=request['workstream_revision']))

    def test_continuation_is_same_identity_one_use_durable_and_ownership_stays_held(self):
        prepared = self.prepare()
        self.observe(prepared)
        request = self.continuation(prepared)
        intent = self.api.prepare_continuation(self.scope, request)
        self.assertFalse(intent['send_admitted'])
        self.assertEqual(intent['worker_id'], 'child')
        self.assertEqual(intent['remaining_continuations'], 2)
        result = self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=request['workstream_revision'], token=intent['token']))
        self.assertTrue(result['send_admitted'])
        self.assertEqual(result['worker_id'], 'child')
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=request['workstream_revision'], token=intent['token']))
        recorded = self.api.record_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            token=intent['token'], delivery='uncertain', delivery_ref='native:response-lost'))
        self.assertEqual(recorded['delivery'], 'uncertain')
        with self.assertRaises(state.StateError):
            self.api.prepare_continuation(self.scope, self.continuation(prepared))
        raw = self.fixture.raw_assignment(prepared['attempt_id'])
        self.assertNotEqual(raw['status'], 'settled')
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        self.assertEqual(task['delegation']['attempts_consumed'], 1)
        self.assertEqual(task['delegation']['resource_units_consumed'], 1)

    def test_host_retry_and_unknown_failures_block_automatic_continuation(self):
        prepared = self.prepare()
        for fields in (dict(failure_code='serverOverloaded', will_retry=True),
                       dict(failure_code='serverOverloaded', will_retry=None),
                       dict(failure_code=None, will_retry=False)):
            self.observe(prepared, 'failed', **fields)
            with self.assertRaises(state.StateError):
                self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='transient'))

    def test_limits_require_human_continue_and_transient_requires_explicit_retry_finished(self):
        prepared = self.prepare()
        self.observe(prepared, 'failed', failure_code='rateLimitExceeded', will_retry=False)
        with self.assertRaises(state.StateError):
            self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='transient'))
        result = self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='transient', human_continue=True))
        self.assertEqual(result['worker_id'], 'child')

    def test_pending_continuation_invalidated_by_mode_candidate_or_ownership_change(self):
        prepared = self.prepare()
        self.observe(prepared)
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        (self.fixture.repo / 'app.py').write_text('candidate changed\n')
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=self.control('status')['revision'], token=intent['token']))

    def test_cli_control_uses_existing_scope_and_json_request(self):
        from scripts import workflow
        output = io.StringIO()
        with patch('sys.argv', ['workflow.py', 'delegation', 'workstreams', 'status', '--cwd', str(self.fixture.repo)]), \
                patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'{"client":"codex","feature":"resource_admission"}'))), \
                patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.scope)), redirect_stdout(output):
            exit_code = workflow.control_main()
        self.assertEqual(exit_code, 0, output.getvalue())
        self.assertFalse(json.loads(output.getvalue())['enabled'])

    def test_continuation_requires_stopped_descendants_and_preserves_original_allocation(self):
        prepared = self.prepare()
        self.observe(prepared)
        proof = self.fixture.worker_reconciliation_request(prepared, proof_delta=dict(descendants_stopped=False))
        with self.assertRaises(state.StateError):
            self.api.prepare_continuation(self.scope, self.continuation(prepared, host_proof=proof['host_proof']))
        self.limits['max_continuations'] = 200
        for action in ('disable', 'enable'):
            self.control(action, self.control('status')['revision'])
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        self.assertEqual(intent['remaining_continuations'], 2)

    def test_disable_blocks_pending_continuation_but_allows_delivery_recording(self):
        prepared = self.prepare()
        self.observe(prepared)
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        revision = self.control('status')['revision']
        self.control('disable', revision)
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=revision, token=intent['token']))
        new_mode = self.enable()
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=new_mode['revision'], token=intent['token']))
        replacement = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=new_mode['revision'], token=replacement['token']))
        self.control('disable', new_mode['revision'])
        result = self.api.record_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            token=replacement['token'], delivery='sent', delivery_ref='native:followup-item'))
        self.assertEqual(result['delivery'], 'sent')

    def test_retry_backoff_and_original_continuation_allowance_are_binding(self):
        self.limits['max_continuations'] = 1
        self.limits['backoff_seconds'] = 20
        prepared = self.prepare()
        self.observe(prepared, 'failed', failure_code='serverOverloaded', will_retry=False)
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='transient'))
        self.assertEqual(intent['remaining_continuations'], 0)
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=self.control('status')['revision'], token=intent['token']))
        with patch.object(delegation, '_now', return_value=intent['retry_at'] + 1):
            self.assertTrue(self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=self.control('status')['revision'], token=intent['token']))['send_admitted'])
        self.api.record_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            token=intent['token'], delivery='sent', delivery_ref='native:sent'))
        self.observe(prepared, 'failed', failure_code='serverOverloaded', will_retry=False, worker_turn_id='next-child-turn')
        with self.assertRaises(state.StateError):
            self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='transient', human_continue=True))

    def test_current_shared_ownership_and_task_pause_block_consumption(self):
        prepared = self.prepare()
        self.observe(prepared)
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        current = state.task_status(self.scope, 'codex', 'parent')['task']
        state.update_task(self.scope, self.fixture.identity | dict(expected_revision=current['revision'],
            state='WAITING', reason='Human paused the assignment', reference='user:pause'))
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=self.control('status')['revision'], token=intent['token']))

    def test_lost_delivery_record_recovers_from_new_stopped_native_turn_without_resending(self):
        prepared = self.prepare()
        self.observe(prepared)
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        consume = self.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=self.control('status')['revision'], token=intent['token'])
        self.api.consume_continuation(self.scope, consume)
        with self.assertRaises(state.StateError):
            self.api.prepare_continuation(self.scope, self.continuation(prepared, human_continue=True))
        self.observe(prepared, worker_turn_id='confirmed-next-native-turn')
        recovery = self.api.prepare_continuation(self.scope, self.continuation(prepared, human_continue=True))
        self.assertEqual(recovery['worker_id'], 'child')
        self.assertNotEqual(recovery['token'], intent['token'])
        self.assertEqual(recovery['remaining_continuations'], 1)
        self.assertFalse(recovery['send_admitted'])
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=self.control('status')['revision'], token=intent['token']))
        self.assertNotEqual(self.fixture.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_reassessment_does_not_spend_a_second_allowance_for_an_unsent_intent(self):
        self.limits['max_continuations'] = 1
        prepared = self.prepare()
        self.observe(prepared)
        first = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        self.control('disable', self.control('status')['revision'])
        self.enable()
        replacement = self.api.prepare_continuation(self.scope, self.continuation(prepared, human_continue=True))
        self.assertNotEqual(first['token'], replacement['token'])
        self.assertEqual(replacement['remaining_continuations'], 0)
        self.assertTrue(self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=self.control('status')['revision'], token=replacement['token']))['send_admitted'])

    def test_disable_during_preparation_prevents_reservation_commit(self):
        enabled = self.enable()
        self.fixture.plan()
        request = self.fixture.prepare_request(workstream_revision=enabled['revision'], workstream_limits=self.limits)
        preparing = threading.Event()
        released = threading.Event()
        original = delegation._discovery
        def discover(*args):
            result = original(*args)
            preparing.set()
            if not released.wait(10):
                raise AssertionError('Fixture dispatch was never released')
            return result
        with patch.object(delegation, '_discovery', side_effect=discover), ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(delegation.prepare_assignment, self.scope, request)
            try:
                self.assertTrue(preparing.wait(10))
                self.control('disable', enabled['revision'])
            finally:
                released.set()
            with self.assertRaises(state.StateError):
                pending.result(timeout=10)
        self.assertEqual(delegation.delegation_status(self.scope, 'codex', 'parent')['assignments'], [])

    def test_new_shared_owner_invalidates_prepared_continuation(self):
        prepared = self.prepare()
        self.observe(prepared)
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        other_request = self.fixture.second_owner(2, claims=[self.fixture.path_claim('other.py')])
        other = delegation.prepare_assignment(self.scope, other_request)
        self.assertTrue(other['launch_admitted'])
        with self.assertRaises(state.StateError):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
                workstream_revision=self.control('status')['revision'], token=intent['token']))
        retained = self.fixture.raw_assignment(prepared['attempt_id'])
        self.assertEqual(retained['status'], 'observed')
        self.assertIn('workstream_continuation', retained)

    def test_fresh_process_keeps_one_use_permission_and_original_native_identity(self):
        prepared = self.prepare()
        self.observe(prepared)
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared))
        request = self.fixture.request(attempt_id=prepared['attempt_id'], workstream_revision=self.control('status')['revision'],
            token=intent['token'])
        payload = dict(acquisition=str(self.fixture.fixture.test_acquisition),
            guards=str(self.fixture.root / 'authority-guards'), request=request)
        program = """
import json, sys
from pathlib import Path
from runtime import changes, state, workstreams
from runtime.startup import ProjectScope, RuntimeSettings
payload = json.load(sys.stdin)
from tests.fixture_canonical_birth import acquire_test_scope
scope = acquire_test_scope(payload['acquisition'])
changes.LOCK_ROOT = Path(payload['guards'])
try:
    result = workstreams.consume_continuation(scope, payload['request'])
except state.StateError:
    result = dict(ok=False, send_admitted=False)
print(json.dumps(result))
"""
        def consume():
            process = subprocess.run([sys.executable, '-B', '-c', program], input=json.dumps(payload),
                text=True, capture_output=True, cwd=state.PACKAGE, timeout=15, check=True)
            return json.loads(process.stdout)
        first = consume()
        self.assertEqual(first['worker_id'], 'child')
        self.assertTrue(first['send_admitted'])
        payload['request']['expected_revision'] = first['revision']
        self.assertFalse(consume()['send_admitted'])
        self.assertEqual(self.control('status')['unresolved_assignments'], 1)


    def reviewed_worker(self):
        f = self.fixture
        enabled = self.enable()
        plan = f.plan_value()
        plan['jobs'].extend([
            dict(id='review', role='review', claims=[f.path_claim(mode='read')], dependencies=['edit'], max_attempts=3),
            dict(id='consumer', role='worker', claims=[f.path_claim('consumer.py')], dependencies=['review'], max_attempts=1)])
        f.plan(plan)
        prepared = f.prepare(workstream_revision=enabled['revision'], workstream_limits=self.limits)
        self.observe(prepared)
        f.api.reconcile_assignment(self.scope, f.worker_reconciliation_request(prepared))
        helper = test_delegation.Stage4NativeReviewTests
        review = evidence.prepare_review(self.scope, helper.review_request(f))
        with self.assertRaises(state.StateError):
            self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='review-fixes'))
        observed = helper.observed(f, review, worker_id='independent-reviewer')
        report = json.loads(observed['result'])
        report['findings'] = [dict(id='fix', severity='high', scope='general', status='confirmed',
            location='app.py:1', scenario='Original behavior needs the reviewed correction',
            evidence='fixture:independent-code-inspection', counterevidence='fixture:reviewed-callers')]
        observed['result'] = json.dumps(report)
        observed['receipt']['result_digest'] = hashlib.sha256(observed['result'].encode()).hexdigest()
        helper.observe(f, review, observed)
        helper.collect(f, review, helper.collection(f, review, observed))
        evidence.assess_review(self.scope, f.request(attempt_id=review['attempt_id'], assessment=dict(
            coverage=['general', 'security'], criteria=['works'], qualification='Independent fixture expertise',
            independence='Separate native child', semantic_coverage='Inspected source and behavior',
            dispositions=[dict(finding_id='fix', status='open', reference='fixture:requires-original-implementer-fix')],
            decision_refs=['user:accepted'])))
        f.api.reconcile_assignment(self.scope, f.worker_reconciliation_request(review, changed_paths=[]))
        return prepared, review

    def consumer_request(self):
        request = self.fixture.prepare_request(job_id='consumer', dispatch_id='consumer-dispatch')
        request['brief']['files'] = ['consumer.py']
        request['brief']['resources'] = [self.fixture.path_claim('consumer.py')]
        request['brief']['dependencies'] = ['review']
        return request

    def test_independent_review_releases_claims_and_same_child_fixes_reacquire_them(self):
        prepared, review = self.reviewed_worker()
        original = self.fixture.raw_assignment(prepared['attempt_id'])
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='review-fixes'))
        self.assertEqual(self.fixture.raw_assignment(prepared['attempt_id'])['status'], 'settled')
        consumed = self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=self.control('status')['revision'], token=intent['token']))
        self.assertEqual(consumed['worker_id'], 'child')
        resumed = self.fixture.raw_assignment(prepared['attempt_id'])
        self.assertEqual(resumed['status'], 'observed')
        self.assertIsNone(resumed['reconciliation'])
        self.assertIsNone(resumed['result'])
        retained = evidence._read(self.scope, resumed['workstream_continuation'])
        self.assertEqual(retained['prior_reconciliation'], original['reconciliation'])
        with self.assertRaises(state.StateError):
            delegation.prepare_assignment(self.scope, self.consumer_request())
        with self.assertRaises(state.StateError):
            delegation.reconcile_assignment(self.scope, self.fixture.worker_reconciliation_request(prepared))
        self.api.record_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            token=intent['token'], delivery='sent', delivery_ref='native:review-fixes-sent'))
        (self.fixture.repo / 'app.py').write_text('value = 2\n')
        self.observe(prepared, worker_turn_id='review-fix-turn')
        delegation.reconcile_assignment(self.scope, self.fixture.worker_reconciliation_request(prepared))
        with self.assertRaises(state.StateError):
            delegation.prepare_assignment(self.scope, self.consumer_request())
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        self.assertEqual(task['delegation']['attempts_consumed'], 2)
        self.assertEqual(task['delegation']['resource_units_consumed'], 2)
        self.assertFalse(evidence.verify_completion(self.scope, self.fixture.request(purpose='completion'))['verified'])

    def test_active_transitive_consumer_prevents_reopening_settled_implementer(self):
        prepared, review = self.reviewed_worker()
        consumer = delegation.prepare_assignment(self.scope, self.consumer_request())
        self.assertTrue(consumer['launch_admitted'])
        with self.assertRaises(state.StateError):
            self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='review-fixes'))
        self.assertEqual(self.fixture.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_reacquired_claims_cover_atomic_replacement_and_current_hardlink_alias(self):
        prepared, review = self.reviewed_worker()
        replacement = self.fixture.repo / 'replacement.tmp'
        replacement.write_text('value = 2\n')
        os.replace(replacement, self.fixture.repo / 'app.py')
        intent = self.api.prepare_continuation(self.scope, self.continuation(prepared, reason='review-fixes'))
        self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=self.control('status')['revision'], token=intent['token']))
        os.link(self.fixture.repo / 'app.py', self.fixture.repo / 'alias.py')
        alias_request = self.fixture.second_owner(3, claims=[self.fixture.path_claim('alias.py')])
        with self.assertRaises(state.StateError):
            delegation.prepare_assignment(self.scope, alias_request)
        claims = delegation._claims([self.fixture.path_claim('alias.py', mode='read')])
        with state._database(self.scope) as db:
            blockers = evidence._ledger_blockers(self.scope, db, claims)
        self.assertTrue(any(prepared['attempt_id'] in blocker for blocker in blockers))

    def test_worker_creation_name_contains_family_context_and_build_role(self):
        prepared = self.prepare()
        self.assertEqual(prepared['task_name'], 'worker_ws42_01_build_' + prepared['attempt_id'])
        binding = delegation.prepared_native_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['workstream_context_id'], 'WS42.01')
        for invalid in ('C12', 'WS42.00', 'WS42/01', 'WS42.01.BUILD'):
            limits = self.limits | dict(context_id=invalid)
            with self.subTest(invalid=invalid), self.assertRaises(state.StateError):
                self.fixture.prepare(dispatch_id='invalid-' + str(len(invalid)), workstream_revision=self.control('status')['revision'],
                    workstream_limits=limits)

    def test_grouped_d5_review_creation_name_and_collection_work_while_mode_is_off(self):
        helper = test_delegation.Stage4NativeReviewTests
        f = self.fixture
        f.plan(f.plan_value(jobs=[dict(id='review', role='review', claims=[f.path_claim(mode='read')],
            dependencies=[], max_attempts=2)]))
        prepared = evidence.prepare_review(self.scope, helper.review_request(f, workstream_context_id='WS42.03'))
        self.assertEqual(prepared['task_name'], 'review_ws42_03_review_' + prepared['attempt_id'])
        self.assertFalse(self.control('status')['enabled'])
        self.assertEqual(delegation.prepared_native_review_assignment(self.scope, prepared['attempt_id'])['workstream_context_id'], 'WS42.03')
        self.assertEqual(evidence.prepared_native_review(self.scope, prepared['attempt_id'])['workstream_context_id'], 'WS42.03')
        observed = helper.observed(f, prepared)
        helper.observe(f, prepared, observed)
        helper.collect(f, prepared, helper.collection(f, prepared, observed))
        helper.assess_current_review(f, prepared)
        settled = delegation.reconcile_assignment(self.scope, f.worker_reconciliation_request(prepared, changed_paths=[]))
        self.assertEqual(settled['status'], 'settled')

    def test_malformed_workstream_limits_fail_with_sanitized_control_error(self):
        enabled = self.enable()
        self.fixture.plan()
        for value in ([], None, 'unexpected'):
            with self.subTest(value=value), self.assertRaises(state.StateError):
                self.fixture.prepare(workstream_revision=enabled['revision'], workstream_limits=value)

    def settled_worker_chain(self, length):
        enabled = self.enable()
        names = ['a', 'b', 'c'][:length]
        jobs = [dict(id=name, role='worker', claims=[self.fixture.path_claim(name + '.py')],
            dependencies=[names[index - 1]] if index else [], max_attempts=2)
            for index, name in enumerate(names)]
        self.fixture.plan(self.fixture.plan_value(jobs=jobs))
        prepared = []
        for index, job in enumerate(jobs):
            request = self.fixture.prepare_request(job_id=job['id'], dispatch_id=job['id'] + '-initial',
                workstream_revision=enabled['revision'], workstream_limits=self.limits | dict(context_id='WS42.0' + str(index + 1)))
            request['brief'].update(files=[job['id'] + '.py'], resources=job['claims'], dependencies=job['dependencies'])
            child = delegation.prepare_assignment(self.scope, request)
            observed = self.fixture.lifecycle_observation(child, worker_id='child-' + job['id'])
            report = json.loads(observed['result'])
            report['changed_paths'] = []
            observed['result'] = json.dumps(report)
            observed['receipt']['result_digest'] = hashlib.sha256(observed['result'].encode()).hexdigest()
            self.fixture.observe_worker(child, observed)
            delegation.reconcile_assignment(self.scope, self.fixture.worker_reconciliation_request(child, changed_paths=[]))
            prepared.append(child)
        return prepared

    def assert_reopened_prerequisite_blocks_continuation(self, length):
        children = self.settled_worker_chain(length)
        target = children[-1]
        pending = self.api.prepare_continuation(self.scope, self.continuation(target))
        prerequisite = self.api.prepare_continuation(self.scope, self.continuation(children[0]))
        self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=children[0]['attempt_id'],
            workstream_revision=self.control('status')['revision'], token=prerequisite['token']))
        with self.assertRaisesRegex(state.StateError, 'prerequisite'):
            self.api.prepare_continuation(self.scope, self.continuation(target))
        with self.assertRaisesRegex(state.StateError, 'prerequisite'):
            self.api.consume_continuation(self.scope, self.fixture.request(attempt_id=target['attempt_id'],
                workstream_revision=self.control('status')['revision'], token=pending['token']))
        self.assertEqual(self.fixture.raw_assignment(target['attempt_id'])['status'], 'settled')
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        self.assertEqual(task['delegation']['attempts_consumed'], length)
        self.assertEqual(task['delegation']['resource_units_consumed'], length)

    def test_direct_own_prerequisite_must_remain_accepted_at_prepare_and_consume(self):
        self.assert_reopened_prerequisite_blocks_continuation(2)

    def test_transitive_own_prerequisite_must_remain_accepted_at_prepare_and_consume(self):
        self.assert_reopened_prerequisite_blocks_continuation(3)

    def test_coordinator_rejects_unresolved_and_stale_transitive_prerequisites(self):
        f = self.fixture
        original = f.plan_value
        def plan_value(**updates):
            plan = original(**updates)
            plan['jobs'] = [*plan['jobs'], dict(id='integrate', role='coordinator',
                claims=[f.path_claim('integration.py')], dependencies=['b'], max_attempts=1)]
            return plan
        with patch.object(f, 'plan_value', side_effect=plan_value):
            children = self.settled_worker_chain(2)
        intent = self.api.prepare_continuation(self.scope, self.continuation(children[0]))
        self.api.consume_continuation(self.scope, f.request(attempt_id=children[0]['attempt_id'],
            workstream_revision=self.control('status')['revision'], token=intent['token']))
        def reject():
            before = delegation.delegation_status(self.scope, 'codex', 'parent')
            with self.assertRaisesRegex(state.StateError, 'prerequisite'):
                delegation.reserve_coordinator(self.scope, f.request(dispatch_id='integrate-after-reopen',
                    claims=[f.path_claim('integration.py')], operation=dict(id='integrate', kind='coordinator-work'),
                    reason='Integrate accepted output', ownership_ref='fixture:original-plan',
                    stop_conditions=['Stop if a prerequisite is stale']))
            self.assertEqual(delegation.delegation_status(self.scope, 'codex', 'parent'), before)
        reject()
        observed = f.lifecycle_observation(children[0], worker_id='child-a', worker_turn_id='a-repaired-turn')
        report = json.loads(observed['result'])
        report['changed_paths'] = []
        observed['result'] = json.dumps(report)
        observed['receipt']['result_digest'] = hashlib.sha256(observed['result'].encode()).hexdigest()
        f.observe_worker(children[0], observed)
        delegation.reconcile_assignment(self.scope, f.worker_reconciliation_request(children[0], changed_paths=[]))
        reject()

    def test_newer_same_job_attempt_supersedes_the_old_child_without_resetting_budgets(self):
        original = self.prepare()
        self.observe(original)
        delegation.reconcile_assignment(self.scope, self.fixture.worker_reconciliation_request(original))
        replacement = self.fixture.prepare(dispatch_id='replacement', retry_of=original['attempt_id'],
            diagnostic=dict(prior_attempt_id=original['attempt_id'], cause='Original context superseded for recovery',
                evidence=['fixture:independent-recovery-evidence'], change='Accepted replacement child'),
            workstream_revision=self.control('status')['revision'], workstream_limits=self.limits)
        self.observe(replacement, worker_id='replacement-child')
        delegation.reconcile_assignment(self.scope, self.fixture.worker_reconciliation_request(replacement))
        with self.assertRaisesRegex(state.StateError, '[Ss]uperseded'):
            self.api.prepare_continuation(self.scope, self.continuation(original))
        current = self.api.prepare_continuation(self.scope, self.continuation(replacement))
        self.assertEqual(current['worker_id'], 'replacement-child')
        task = state.task_status(self.scope, 'codex', 'parent')['task']
        self.assertEqual(task['delegation']['attempts_consumed'], 2)
        self.assertEqual(task['delegation']['resource_units_consumed'], 2)

    def test_updated_reader_provenance_preserves_original_native_binding_and_history(self):
        prepared = self.prepare()
        self.observe(prepared)
        observed = self.fixture.lifecycle_observation(prepared, current_observer_version='0.165.0',
            parent_creation_version='0.164.0-alpha.11.1', worker_creation_version='0.164.0-alpha.11.1')
        reply = self.fixture.observe_worker(prepared, observed)
        self.assertEqual(reply['status'], 'observed')
        with state._database(self.scope) as db:
            record = next(r for r in delegation._ledger(self.scope, db) if r['id'] == prepared['attempt_id'])
        receipt = delegation._worker_history(self.scope, record['data']['observation'])[0][1]['receipt']
        self.assertEqual(record['data']['binding']['native_version'], '0.163.0')
        self.assertEqual(receipt['native_version'], '0.163.0')
        self.assertEqual(receipt['current_observer_version'], '0.165.0')
        request = self.fixture.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        for key in ('current_observer_version', 'parent_creation_version', 'worker_creation_version'):
            malformed = dict(observed, receipt=observed['receipt'] | {key: None})
            with self.subTest(key=key), self.assertRaises(state.StateError):
                delegation._worker_receipt(self.scope, record, request, malformed)

    def test_d5_updated_reader_collection_retains_admitted_version_and_truthful_provenance(self):
        helper = test_delegation.Stage4NativeReviewTests
        f = self.fixture
        f.plan(f.plan_value(jobs=[dict(id='review', role='review', claims=[f.path_claim(mode='read')],
            dependencies=[], max_attempts=2)]))
        prepared = evidence.prepare_review(self.scope, helper.review_request(f))
        binding = evidence.prepared_native_review(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['native_version'], '0.163.0')
        observed = helper.observed(f, prepared, current_observer_version='0.165.0',
            parent_creation_version='0.164.0-alpha.11.1', worker_creation_version='0.164.0-alpha.11.1')
        self.assertEqual(helper.observe(f, prepared, observed)['status'], 'observed')
        collected = helper.collection(f, prepared, observed)
        collected['receipt'].update(current_observer_version='0.166.0', parent_creation_version='0.164.0-alpha.11.1',
            worker_creation_version='0.164.0-alpha.11.1')
        result = helper.collect(f, prepared, collected)
        receipt = evidence._read(self.scope, result['attempt']['collected'])['receipt']
        self.assertEqual(receipt['native_version'], '0.163.0')
        self.assertEqual(receipt['current_observer_version'], '0.166.0')

    def test_private_control_symlink_is_rejected(self):
        self.enable()
        path = self.api._path(self.scope)
        original = path.read_bytes()
        path.unlink()
        outside = self.fixture.root / 'outside.json'
        outside.write_bytes(original)
        path.symlink_to(outside)
        with self.assertRaises(state.StateError):
            self.control('status')


if __name__ == '__main__':
    unittest.main()
