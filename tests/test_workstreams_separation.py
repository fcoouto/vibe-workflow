"""Independent settings and base continuation use existing task authority only."""
import hashlib
import json
import unittest

from runtime import delegation, state, workstreams
from tests.test_workstreams import WorkstreamsTests
from tests.test_workstreams_defaults import WorkstreamsDefaultTests


class SeparationTests(unittest.TestCase):
    def setUp(self):
        self.protocol = WorkstreamsTests('runTest')
        self.protocol.setUp()
        self.addCleanup(self.protocol.doCleanups)
        self.f = self.protocol.fixture
        self.scope = self.f.scope

    def feature(self, feature, action='status', revision=None):
        request = dict(client='codex', feature=feature)
        if revision is not None:
            request['expected_revision'] = revision
        return workstreams.control(self.scope, action, request)

    def test_feature_replies_do_not_claim_lifecycle_state(self):
        status = self.feature('resource_admission')
        changed = self.feature('resource_admission', 'enable', status['revision'])
        for reply in (status, changed):
            self.assertFalse({'unresolved_assignments', 'drained', 'activity'} & reply.keys())
            self.assertEqual(reply['feature'], 'resource_admission')
            self.assertEqual(reply['scope_id'], state._scope_id(self.scope))
        self.assertTrue({'unresolved_assignments', 'drained', 'activity'} <= self.protocol.control('status').keys())

    def test_feature_choices_and_permissions_are_independent(self):
        legacy = self.protocol.enable()
        resource = self.feature('resource_admission')
        recovery = self.feature('capacity_recovery')
        self.assertTrue(resource['enabled'])
        self.assertTrue(recovery['enabled'])
        off = self.feature('resource_admission', 'disable', resource['revision'])
        self.assertFalse(off['enabled'])
        self.assertEqual(self.feature('capacity_recovery')['revision'], recovery['revision'])
        workstreams._feature_permission(self.scope, 'capacity_recovery', recovery['revision'])
        with self.assertRaises(state.StateError):
            workstreams._feature_permission(self.scope, 'resource_admission', resource['revision'])
        self.protocol.control('disable', legacy['revision'])
        self.assertTrue(self.feature('capacity_recovery')['enabled'])
        self.assertFalse(self.feature('resource_admission')['enabled'])
        for resource_on, recovery_on in ((False, False), (False, True), (True, False), (True, True)):
            for name, enabled in (('resource_admission', resource_on), ('capacity_recovery', recovery_on)):
                status = self.feature(name)
                self.feature(name, 'enable' if enabled else 'disable', status['revision'])
            self.assertEqual(self.feature('resource_admission')['enabled'], resource_on)
            self.assertEqual(self.feature('capacity_recovery')['enabled'], recovery_on)
            for name, enabled in (('resource_admission', resource_on), ('capacity_recovery', recovery_on)):
                choice = self.feature(name)
                if enabled:
                    workstreams._feature_permission(self.scope, name, choice['revision'])
                else:
                    with self.assertRaises(state.StateError):
                        workstreams._feature_permission(self.scope, name, choice['revision'])

    def test_legacy_disable_is_used_only_for_missing_feature_choice(self):
        self.protocol.control('disable',self.protocol.control('status')['revision'])
        self.assertFalse(self.feature('resource_admission')['enabled'])
        self.assertFalse(self.feature('capacity_recovery')['enabled'])
        self.feature('capacity_recovery','enable',self.feature('capacity_recovery')['revision'])
        self.assertFalse(self.feature('resource_admission')['enabled'])
        self.assertTrue(self.feature('capacity_recovery')['enabled'])

    def test_base_continuation_with_both_features_off_keeps_identity_and_fresh_result(self):
        self.f.plan()
        child = self.f.prepare(continuation_limits=dict(max_continuations=1, backoff_seconds=0))
        self.protocol.observe(child)
        delegation.reconcile_assignment(self.scope, self.f.worker_reconciliation_request(child))
        request = self.protocol.continuation(child)
        request.pop('workstream_revision')
        pending = delegation.prepare_continuation(self.scope, request)
        consumed = delegation.consume_continuation(self.scope, self.f.request(attempt_id=child['attempt_id'], token=pending['token']))
        self.assertEqual(consumed['worker_id'], 'child')
        self.assertEqual(consumed['remaining_continuations'], 0)
        delegation.record_continuation(self.scope, self.f.request(attempt_id=child['attempt_id'],
            token=pending['token'], delivery='sent', delivery_ref='fixture:continued'))
        with self.assertRaises(state.StateError):
            delegation.consume_continuation(self.scope, self.f.request(attempt_id=child['attempt_id'], token=pending['token']))
        with self.assertRaisesRegex(state.StateError, 'newly observed'):
            delegation.reconcile_assignment(self.scope, self.f.worker_reconciliation_request(child))
        self.protocol.observe(child, worker_turn_id='continued-turn')
        delegation.reconcile_assignment(self.scope, self.f.worker_reconciliation_request(child))
        exhausted = self.protocol.continuation(child)
        exhausted.pop('workstream_revision')
        with self.assertRaisesRegex(state.StateError, 'exhausted'):
            delegation.prepare_continuation(self.scope, exhausted)

    def test_new_scope_defaults_both_features_without_side_effects(self):
        default = WorkstreamsDefaultTests('runTest')
        default.setUp()
        self.addCleanup(default.doCleanups)
        default.start()
        before = {str(p): p.read_bytes() for p in default.scope.runtime.state_dir.rglob('*') if p.is_file()}
        for feature in ('resource_admission', 'capacity_recovery'):
            self.assertTrue(workstreams.control(default.scope, 'status', dict(client='codex', feature=feature))['enabled'])
        after = {str(p): p.read_bytes() for p in default.scope.runtime.state_dir.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_both_off_reopened_prerequisite_stales_transitive_worker_and_coordinator(self):
        jobs = [dict(id='a', role='worker', claims=[self.f.path_claim('a.py')], dependencies=[], max_attempts=2),
            dict(id='b', role='worker', claims=[self.f.path_claim('b.py')], dependencies=['a'], max_attempts=1),
            dict(id='c', role='worker', claims=[self.f.path_claim('c.py')], dependencies=['b'], max_attempts=1),
            dict(id='integrate', role='coordinator', claims=[self.f.path_claim('integration.py')], dependencies=['b'], max_attempts=1)]
        self.f.plan(self.f.plan_value(jobs=jobs))
        def prepare(name, dispatch, **updates):
            job = next(j for j in jobs if j['id'] == name)
            request = self.f.prepare_request(job_id=name, dispatch_id=dispatch, **updates)
            request['brief'].update(files=[name + '.py'], resources=job['claims'], dependencies=job['dependencies'])
            return delegation.prepare_assignment(self.scope, request)
        def settle(child, worker):
            observed = self.f.lifecycle_observation(child, worker_id=worker)
            report = json.loads(observed['result'])
            report['changed_paths'] = []
            observed['result'] = json.dumps(report)
            observed['receipt']['result_digest'] = hashlib.sha256(observed['result'].encode()).hexdigest()
            self.f.observe_worker(child, observed)
            delegation.reconcile_assignment(self.scope, self.f.worker_reconciliation_request(child, changed_paths=[]))
        a = prepare('a', 'a-original')
        settle(a, 'a-original')
        b = prepare('b', 'b-original')
        settle(b, 'b-original')
        replacement = prepare('a', 'a-reopened', retry_of=a['attempt_id'], diagnostic=dict(
            prior_attempt_id=a['attempt_id'], cause='Reopened prerequisite',
            evidence=['fixture:changed-prerequisite'], change='New accepted prerequisite result'))
        settle(replacement, 'a-reopened')
        with self.assertRaisesRegex(state.StateError, 'prerequisite'):
            prepare('c', 'c-after-reopen')
        with self.assertRaisesRegex(state.StateError, 'prerequisite'):
            delegation.reserve_coordinator(self.scope, self.f.request(dispatch_id='integrate-after-reopen',
                claims=[self.f.path_claim('integration.py')], operation=dict(id='integrate', kind='coordinator-work'),
                reason='Integrate accepted output', ownership_ref='fixture:original-plan',
                stop_conditions=['Stop if prerequisite stale']))
