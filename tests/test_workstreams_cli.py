"""Isolated CLI integration; native schedules and observations stay synthetic."""
from tests.fixture_canonical_birth import test_acquisition
import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from runtime import delegation, parent_recovery, state, workstreams
from scripts import workflow, workstreams_recovery
from tests import test_parent_recovery


class WorkstreamsCliTests(unittest.TestCase):
    def setUp(self):
        self.recovery = test_parent_recovery.IndependentRecoveryTests('runTest')
        self.recovery.setUp()
        self.addCleanup(self.recovery.doCleanups)
        self.f = self.recovery.fixture
        self.scope = self.f.scope
        self.home = self.recovery.directory.parent
        self.directory = self.home / 'workstreams-recovery'
        self.environment = patch.dict(os.environ, {'CODEX_HOME': str(self.home)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def cli(self, action, request, *, recovery=False, ordinary=False):
        output = io.StringIO()
        argv = (['workstreams_recovery.py', action] if recovery else
            ['workflow.py', 'delegation', *([] if ordinary else ['workstreams']), action])
        if ordinary and action == 'coordinator':
            argv.append('reserve')
        argv.extend(['--cwd', str(self.scope.repository_root)])
        with patch('sys.argv', argv), patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
             patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.scope)), redirect_stdout(output):
            code = workstreams_recovery.main() if recovery else workflow.control_main()
        return code, json.loads(output.getvalue())

    def feature(self, name, enabled=None, **extra):
        request = dict(client='codex', feature=name)
        if enabled is None:
            return self.cli('status', request)
        revision = workstreams._feature(self.scope, name)['revision']
        return self.cli('enable' if enabled else 'disable', request | dict(expected_revision=revision) | extra)

    def register(self):
        revision = workstreams._feature(self.scope, 'capacity_recovery')['revision']
        return self.cli('register', self.f.request(recovery_revision=revision), recovery=True)

    def registry(self, action='status', **request):
        return parent_recovery.control(self.directory, action, request)

    def test_ordinary_worker_context_marker_survives_native_lifecycle_with_features_off(self):
        self.feature('resource_admission', False)
        self.feature('capacity_recovery', False)
        for invalid in ('C12', 'WS42.00', 'WS42/01', 'WS42.01.BUILD'):
            code, rejected = self.cli('prepare', self.f.prepare_request(workstream_context_id=invalid), ordinary=True)
            self.assertEqual(code, 1, rejected)
        self.assertEqual(delegation.delegation_status(self.scope, 'codex', self.recovery.thread)['assignments'], [])
        request = self.f.prepare_request(workstream_context_id='WS42.01')
        self.assertFalse({'workstream_revision', 'workstream_limits', 'continuation_limits'} & request.keys())
        code, child = self.cli('prepare', request, ordinary=True)
        self.assertEqual(code, 0, child)
        self.assertEqual(child['task_name'], 'worker_ws42_01_build_' + child['attempt_id'])
        binding = delegation.prepared_native_assignment(self.scope, child['attempt_id'])
        self.assertEqual(binding['workstream_context_id'], 'WS42.01')
        observed = self.f.lifecycle_observation(child)
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed):
            code, observation = self.cli('observe', self.f.request(attempt_id=child['attempt_id'],
                parent_turn_id=observed['receipt']['parent_turn_id'], spawn_item_id=observed['receipt']['spawn_item_id']), ordinary=True)
        self.assertEqual(code, 0, observation)
        code, settled = self.cli('reconcile', self.f.worker_reconciliation_request(child), ordinary=True)
        self.assertEqual(code, 0, settled)
        self.assertEqual(settled['status'], 'settled')
        self.assertEqual(delegation.prepared_native_assignment(self.scope, child['attempt_id'])['task_name'], binding['task_name'])

    def test_new_assignment_commands_reject_combined_mode_fields(self):
        limits = dict(context_id='WS42.01', rationale='Original bounded fixture', output_tokens=1000,
            context_tokens=8000, max_steps=10, max_seconds=60, max_continuations=1, backoff_seconds=0)
        old = dict(workstream_revision=workstreams._load(self.scope)['revision'], workstream_limits=limits)
        for action, request in (('prepare', self.f.prepare_request()),
                ('coordinator', self.f.operation_request())):
            with self.subTest(action=action):
                before = delegation.delegation_status(self.scope, 'codex', self.recovery.thread)['assignments']
                code, result = self.cli(action, request | old, ordinary=True)
                self.assertEqual(code, 1, result)
                self.assertIn('combined', result['error'].lower())
                self.assertEqual(delegation.delegation_status(self.scope, 'codex', self.recovery.thread)['assignments'], before)

    def test_explicit_features_four_combinations_and_read_only_status(self):
        self.assertEqual(self.cli('status', dict(client='codex'))[0], 1)
        for resource, recovery in ((False, False), (False, True), (True, False), (True, True)):
            self.assertEqual(self.feature('resource_admission', resource)[0], 0)
            self.assertEqual(self.feature('capacity_recovery', recovery)[0], 0)
            for name, wanted in (('resource_admission', resource), ('capacity_recovery', recovery)):
                code, report = self.feature(name)
                self.assertEqual(code, 0)
                self.assertEqual(report['enabled'], wanted)
                self.assertFalse({'pending', 'schedule_id', 'drained', 'activity', 'recovery'} & report.keys())
        self.assertFalse(self.directory.exists())

    def test_recovery_preflight_rejection_leaves_preferences_unchanged(self):
        parent = self.recovery.register()['parents'][self.recovery.thread]['parent']
        parent['workstream_revision'] = parent.pop('recovery_revision')
        self.registry('register', parent=parent)
        before = workstreams._feature(self.scope, 'capacity_recovery')
        code, result = self.feature('capacity_recovery', False)
        self.assertEqual(code, 1)
        self.assertFalse(result['preference_changed'])
        self.assertFalse(result['recovery']['ready'])
        self.assertEqual(workstreams._feature(self.scope, 'capacity_recovery'), before)

    def test_unrelated_recovery_uncertainty_does_not_block_resource_or_ordinary_delegation(self):
        self.directory.mkdir(mode=0o700)
        path = self.directory / 'monitor.json'
        path.write_text('corrupt registry')
        path.chmod(0o600)
        self.assertEqual(self.feature('resource_admission', False)[0], 0)
        self.assertEqual(self.feature('capacity_recovery')[0], 0)
        code, prepared = self.cli('prepare', self.f.prepare_request(continuation_limits=dict(max_continuations=1, backoff_seconds=0)), ordinary=True)
        self.assertEqual(code, 0, prepared)
        self.assertTrue(prepared['launch_admitted'])
        request = delegation.prepared_native_assignment(self.scope, prepared['attempt_id'])
        self.assertNotIn('workstream_revision', request)

    def test_recovery_only_registration_and_stale_permissions(self):
        self.feature('resource_admission', False)
        revision = workstreams._feature(self.scope, 'capacity_recovery')['revision']
        self.assertEqual(self.register()[0], 0)
        row = self.registry()['parents'][self.recovery.thread]
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', self.recovery.thread)
            deadline = delegation._task_plan(self.scope, task)['limits']['deadline']
        self.assertEqual(row['parent']['deadline'], deadline)
        self.assertIn('recovery_revision', row['parent'])
        self.assertNotIn('workstream_revision', row['parent'])
        resource = workstreams._feature(self.scope, 'resource_admission')['revision']
        self.assertEqual(self.feature('capacity_recovery', False)[0], 0)
        self.assertEqual(workstreams._feature(self.scope, 'resource_admission')['revision'], resource)
        self.assertEqual(self.cli('disable', dict(client='codex', feature='capacity_recovery', expected_revision=revision))[0], 1)
        self.assertEqual(self.cli('register', self.f.request(recovery_revision=revision), recovery=True)[0], 1)

    def test_last_disable_exposes_pending_delete_until_native_acknowledgement(self):
        self.assertEqual(self.register()[0], 0)
        create = self.registry('schedule', observed='absent')
        self.registry('record-schedule', token=create['token'], result='created', schedule_id='fixture-shared')
        now = int(time.time())
        observation = dict(thread_status='idle', turn_status='failed', turn_id='fixture-overload',
            error_code='serverOverloaded', will_retry=None, authority='eligible')
        parent_recovery.control(self.directory, 'observe', dict(thread_id=self.recovery.thread, observation=observation), now=now)
        sent = parent_recovery.control(self.directory, 'observe', dict(thread_id=self.recovery.thread, observation=observation), now=now+60)
        self.registry('record-send', thread_id=self.recovery.thread, token=sent['token'], result='uncertain')
        before = self.registry()['parents'][self.recovery.thread]
        self.assertEqual(before['consumed_turns'], ['fixture-overload'])
        code, result = self.feature('capacity_recovery', False)
        self.assertEqual(code, 0, result)
        self.assertIsNone(result['recovery']['pending'])
        self.assertEqual(result['recovery']['schedule_id'], 'fixture-shared')
        self.assertIn('schedule', result['recovery']['next_action'])
        code, lifecycle = self.cli('schedule', dict(observed='active', schedule_id='fixture-shared'), recovery=True)
        self.assertEqual(code, 0, lifecycle)
        self.assertEqual(lifecycle['pending']['operation'], 'delete')
        self.assertEqual(lifecycle['schedule_id'], 'fixture-shared')
        self.assertIn('record-schedule', result['recovery']['next_action'])
        row = self.registry()['parents'][self.recovery.thread]
        self.assertFalse(row['eligible'])
        for key in ('parent', 'episode', 'intent', 'last_send', 'consumed_turns'):
            self.assertEqual(row[key], before[key])
        self.registry('record-schedule', token=lifecycle['pending']['token'], result='uncertain', schedule_id='fixture-shared')
        code, pending = self.feature('capacity_recovery', False)
        self.assertEqual(pending['recovery']['pending'], lifecycle['pending'])
        self.registry('record-schedule', token=lifecycle['pending']['token'], result='deleted', schedule_id='fixture-shared')
        code, acknowledged = self.feature('capacity_recovery', False)
        self.assertIsNone(acknowledged['recovery']['schedule_id'])
        self.assertIsNone(acknowledged['recovery']['pending'])
        self.assertEqual(self.feature('capacity_recovery', True)[0], 0)
        resumed = self.registry()['parents'][self.recovery.thread]
        self.assertFalse(resumed['eligible'])
        for key in ('parent', 'episode', 'intent', 'last_send', 'consumed_turns'):
            self.assertEqual(resumed[key], before[key])

    def test_resource_run_uses_resource_revision_independently_of_recovery(self):
        self.feature('resource_admission', True)
        revision = workstreams._feature(self.scope, 'resource_admission')['revision']
        self.feature('capacity_recovery', False)
        code, child = self.cli('prepare', self.f.prepare_request(), ordinary=True)
        self.assertEqual(code, 0, child)
        request = self.f.request(attempt_id=child['attempt_id'], resource_revision=revision, phase_id='cli-check',
            cpu=1, memory_bytes=1024, capacity_cpu=2, capacity_memory_bytes=1024*1024,
            argv=[sys.executable, '-c', 'print("cli verified")'], wait_seconds=0)
        code, result = self.cli('resource-run', request)
        self.assertEqual(code, 0, result)
        self.assertEqual(result['returncode'], 0)
        self.assertIn('cli verified', result['stdout'])
        self.assertFalse(result['released'])
        self.assertEqual(self.feature('resource_admission', False)[0], 0)
        code, stale = self.cli('resource-run', request | dict(phase_id='stale-phase'))
        self.assertEqual(code, 1, stale)

    def test_ordinary_continuation_cli_uses_base_allowance_with_both_features_off(self):
        self.feature('resource_admission', False)
        self.feature('capacity_recovery', False)
        code, child = self.cli('prepare', self.f.prepare_request(continuation_limits=dict(max_continuations=1, backoff_seconds=0)), ordinary=True)
        self.assertEqual(code, 0, child)
        self.f.observe_worker(child, self.f.lifecycle_observation(child))
        proof = self.f.worker_reconciliation_request(child, outcome='failed')
        request = self.f.request(attempt_id=child['attempt_id'], reason='follow-up', prompt='Finish original task', human_continue=False, host_proof=proof['host_proof'])
        code, intent = self.cli('prepare-continuation', request)
        self.assertEqual(code, 0, intent)
        code, admitted = self.cli('consume-continuation', self.f.request(attempt_id=child['attempt_id'], token=intent['token']))
        self.assertEqual(code, 0, admitted)
        self.assertEqual(admitted['remaining_continuations'], 0)
        self.assertEqual(self.cli('record-continuation', self.f.request(attempt_id=child['attempt_id'], token=intent['token'], delivery='uncertain', delivery_ref='fixture:lost-ack'))[0], 0)
