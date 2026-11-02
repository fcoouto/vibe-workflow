"""Owned heavy-command integration; fixtures are not native client qualification."""
from tests.fixture_canonical_birth import test_acquisition
import importlib
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from tests import test_workstreams


class ResourceExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workstreams.WorkstreamsTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.enable()
        self.fixture.fixture.plan()
        self.prepared = self.fixture.fixture.prepare()
        self.directory = Path(tempfile.mkdtemp()).resolve() / 'host'
        self.request = self.fixture.fixture.request(attempt_id=self.prepared['attempt_id'],
            resource_revision=self.feature('status')['revision'], phase_id='unit-check',
            cpu=1, memory_bytes=1024, capacity_cpu=2, capacity_memory_bytes=1024*1024,
            argv=[sys.executable, '-c', 'print("verified")'], wait_seconds=0)

    def feature(self, action, feature='resource_admission', scope=None):
        scope = scope or self.fixture.scope
        request = dict(client='codex', feature=feature)
        if action != 'status':
            request['expected_revision'] = self.fixture.api.control(scope, 'status', request)['revision']
        return self.fixture.api.control(scope, action, request)

    def call(self, action='run', **updates):
        api=importlib.import_module('runtime.resource_execution')
        return api.operate(self.fixture.scope, self.directory, action, self.request | updates)

    def test_owned_command_runs_once_and_retains_until_reconciliation(self):
        result=self.call()
        self.assertEqual(result['returncode'],0)
        self.assertIn('verified',result['stdout'])
        self.assertFalse(result['released'])
        with self.assertRaises(ValueError):
            self.call()
        self.fixture.observe(self.prepared)
        fixture=self.fixture.fixture
        proof=fixture.worker_reconciliation_request(self.prepared,outcome='failed')
        fixture.api.reconcile_assignment(fixture.scope,proof)
        released=self.call('release')
        self.assertEqual(released['state'],'released')

    def test_disabled_mode_never_launches(self):
        self.feature('disable')
        with self.assertRaises(ValueError): self.call()
        self.assertFalse(self.directory.exists())

    def test_timeout_retains_reservation(self):
        result=self.call(argv=[sys.executable,'-c','import time;time.sleep(1)'], timeout_seconds=0.05)
        self.assertTrue(result['uncertain'])
        self.assertFalse(result['released'])

    def test_telemetry_failure_does_not_block_command(self):
        with patch('runtime.resource_admission.measure',side_effect=OSError('probe unavailable')):
            result=self.call()
        self.assertEqual(result['returncode'],0)
        self.assertFalse(result['released'])

    def test_disk_shortage_still_blocks_when_memory_probe_fails(self):
        from types import SimpleNamespace
        with patch('runtime.resource_admission.measure', side_effect=OSError('memory unavailable')), \
             patch('runtime.resource_execution.shutil.disk_usage', return_value=SimpleNamespace(free=0)) as disk, \
             patch('runtime.resource_execution.subprocess.Popen') as launch:
            result = self.call(disk_bytes=1_000_000)
        self.assertEqual(result['state'], 'waiting')
        self.assertEqual(result['reason'], 'host-pressure')
        disk.assert_called_once()
        launch.assert_not_called()

    def test_disk_probe_failure_preserves_known_memory_pressure(self):
        import time
        sample = dict(sampled_at=int(time.time()), available_memory_bytes=0, pressure=True)
        with patch('runtime.resource_admission.measure', return_value=sample), \
             patch('runtime.resource_execution.shutil.disk_usage', side_effect=OSError('disk unavailable')), \
             patch('runtime.resource_execution.subprocess.Popen') as launch:
            result = self.call(disk_bytes=1_000_000)
        self.assertEqual(result['state'], 'waiting')
        self.assertEqual(result['reason'], 'host-pressure')
        launch.assert_not_called()

    def test_disk_probe_failure_warns_and_persists_status(self):
        with patch('runtime.resource_execution.shutil.disk_usage', side_effect=OSError('disk unavailable')):
            result = self.call(disk_bytes=1_000_000)
        self.assertEqual(result['returncode'], 0)
        self.assertIn('Disk telemetry unavailable', result['warning'])
        saved = next(iter(self.call('status')['reservations'].values()))
        self.assertEqual(saved['last_status']['warning'], result['warning'])

    def test_unavailable_measurements_still_admit_within_reservations(self):
        with patch('runtime.resource_admission.measure', side_effect=OSError('memory unavailable')), \
             patch('runtime.resource_execution.shutil.disk_usage', side_effect=OSError('disk unavailable')):
            result = self.call(disk_bytes=1_000_000)
        self.assertEqual(result['returncode'], 0)
        self.assertIn('Host telemetry unavailable', result['warning'])
        self.assertIn('Disk telemetry unavailable', result['warning'])

    def test_actual_cli_dispatches_owned_foreground_command(self):
        import io
        import json
        from contextlib import redirect_stdout
        from types import SimpleNamespace
        from scripts import workflow
        output=io.StringIO()
        with patch('sys.argv',['workflow.py','delegation','workstreams','resource-run','--cwd',str(self.fixture.fixture.repo)]), \
             patch('sys.stdin',SimpleNamespace(buffer=io.BytesIO(json.dumps(self.request).encode()))), \
             patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.fixture.scope)), \
             patch('runtime.resource_execution.host_directory',return_value=self.directory), redirect_stdout(output):
            code=workflow.control_main()
        self.assertEqual(code,0,output.getvalue())
        result=json.loads(output.getvalue())
        self.assertEqual(result['returncode'],0,result)
        self.assertFalse(result['released'])

    def test_declared_persistent_service_retains_and_status_is_bound(self):
        result=self.call()
        self.assertFalse(result['released'])
        status=self.call('status')
        self.assertEqual(len(status['reservations']),1)
        self.assertEqual(next(iter(status['reservations'].values()))['state'],'active')
        with self.assertRaises(ValueError): self.call('release')

    def test_wait_can_be_cancelled_after_disable(self):
        import time
        sample=dict(sampled_at=int(time.time()),available_memory_bytes=1024,pressure=True)
        with patch('runtime.resource_admission.measure',return_value=sample):
            result=self.call()
        self.assertFalse(result['admitted'])
        self.feature('disable')
        result=self.call('cancel')
        self.assertEqual(result['state'],'cancelled')

    def test_toggle_cannot_duplicate_original_phase(self):
        self.call()
        self.feature('disable')
        enabled=self.feature('enable')
        with self.assertRaises(ValueError):
            self.call(resource_revision=enabled['revision'])

    def test_invalid_disk_demand_is_not_a_telemetry_failure(self):
        with self.assertRaises(ValueError): self.call(disk_bytes=-1)
        self.assertFalse(self.directory.exists())

    def test_detached_descendant_does_not_release_capacity(self):
        command='import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(0.2)"], start_new_session=True); print(p.pid)'
        result=self.call(argv=[sys.executable,'-c',command])
        self.assertFalse(result['released'])
        self.assertTrue(result['uncertain'])

    def test_feature_combinations_apply_to_every_role(self):
        from tests import test_delegation
        from runtime import resource_execution, delegation
        for role in ('worker', 'review', 'coordinator'):
            for resource, recovery in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(role=role, resource=resource, recovery=recovery):
                    stage = (test_delegation.Stage4NativeReviewTests if role == 'review' else
                        test_delegation.AdmissionTests)('runTest')
                    stage.setUp()
                    self.addCleanup(stage.doCleanups)
                    stage.plan(stage.review_plan() if role == 'review' else stage.plan_value())
                    if role == 'review':
                        prepared = stage.prepared()
                    elif role == 'worker':
                        prepared = stage.prepare()
                    else:
                        prepared = delegation.reserve_coordinator(stage.scope, stage.operation_request())
                    self.feature('enable' if recovery else 'disable', 'capacity_recovery', stage.scope)
                    setting = self.feature('enable' if resource else 'disable', scope=stage.scope)
                    request = stage.request(**{k:v for k,v in self.request.items() if k not in stage.identity})
                    request.update(attempt_id=prepared['attempt_id'], resource_revision=setting['revision'])
                    directory = self.directory / role / str(resource) / str(recovery)
                    if resource:
                        self.assertEqual(resource_execution.operate(stage.scope, directory, 'run', request)['returncode'], 0)
                    else:
                        with self.assertRaisesRegex(ValueError, 'Feature is disabled'):
                            resource_execution.operate(stage.scope, directory, 'run', request)
                        self.assertFalse(directory.exists())

    def test_recovery_change_does_not_stale_resource_but_resource_change_does(self):
        self.feature('disable', 'capacity_recovery')
        self.assertEqual(self.call()['returncode'], 0)
        self.feature('disable')
        self.feature('enable')
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            self.call(phase_id='fresh-phase')
        self.assertEqual(len(self.call('status')['reservations']), 1)

    def test_disable_keeps_occupied_owner_and_cleanup_uses_original_identity(self):
        self.call()
        self.feature('disable')
        self.assertEqual(next(iter(self.call('status')['reservations'].values()))['state'], 'active')
        with self.assertRaises(ValueError):
            self.call('cancel')
        with self.assertRaises(ValueError):
            self.call('release')
        self.fixture.observe(self.prepared)
        fixture = self.fixture.fixture
        fixture.api.reconcile_assignment(fixture.scope, fixture.worker_reconciliation_request(self.prepared, outcome='failed'))
        self.assertEqual(self.call('release')['state'], 'released')

    def test_disabled_wait_does_not_block_other_checkout(self):
        self.assert_ineligible_wait_does_not_block()

    def test_stale_wait_after_reenable_does_not_block_other_checkout(self):
        self.assert_ineligible_wait_does_not_block(reenable=True)

    def test_legacy_stale_wait_after_reenable_does_not_block_other_checkout(self):
        self.assert_ineligible_wait_does_not_block(reenable=True, legacy=True)

    def assert_ineligible_wait_does_not_block(self, reenable=False, legacy=False):
        import time
        from runtime import resource_admission, resource_execution
        sample = dict(sampled_at=int(time.time()), available_memory_bytes=0, pressure=True)
        with patch('runtime.resource_admission.measure', return_value=sample):
            if legacy:
                request = dict(self.request)
                request['workstream_revision'] = request.pop('resource_revision')
                first = resource_execution.operate(self.fixture.scope, self.directory, 'run', request)
            else:
                first = self.call()
        self.feature('disable')
        if reenable:
            self.feature('enable')
        request = dict(id='another-checkout', owner=dict(task_id='other'), cpu=1, memory_bytes=1,
            capacity_cpu=2, capacity_memory_bytes=1024*1024, deadline=int(time.time())+60, ready=True)
        with patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.fixture.scope)):
            result = resource_admission.control(self.directory, 'request', request,
                eligible=resource_execution._eligible, probe_key='fresh',
                probe=lambda: dict(sampled_at=int(time.time()), available_memory_bytes=1024*1024, pressure=False))
        self.assertTrue(result['admitted'])
        saved = self.call('status')['reservations'][first['id']]
        self.assertEqual(saved['state'], 'waiting')
        self.assertFalse(saved['request']['ready'])
        self.assertEqual(self.call('cancel')['state'], 'cancelled')

    def test_legacy_request_still_reads_original_revision_and_cannot_bypass_disable(self):
        legacy = dict(self.request)
        legacy['workstream_revision'] = legacy.pop('resource_revision')
        api = importlib.import_module('runtime.resource_execution')
        self.assertEqual(api.operate(self.fixture.scope, self.directory, 'run', legacy)['returncode'], 0)
        self.feature('disable')
        with self.assertRaises(ValueError):
            api.operate(self.fixture.scope, self.directory, 'run', legacy | dict(phase_id='legacy-new'))
        self.assertEqual(len(api.operate(self.fixture.scope, self.directory, 'status', legacy)['reservations']), 1)
        self.feature('enable')
        with self.assertRaisesRegex(ValueError, 'revision changed'):
            api.operate(self.fixture.scope, self.directory, 'run', legacy | dict(phase_id='stale-legacy'))
