"""Local task/status caller wiring; disposable reports, no live observability."""

import builtins
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import runpy
import sys
import subprocess
import unittest
from unittest.mock import patch

from runtime import observability, startup, state
from tests.openrouter_fixtures import enroll, cli_driver


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/workflow.py'


class ObservabilityCallerTests(unittest.TestCase):
    def setUp(self):
        self.scope = enroll(self)
        self.fixture = self._optional_fixtures[-1]
        self.driver = cli_driver(self, self.scope, SCRIPT)
        state.start_task(self.scope, dict(client='codex', session_id='session-1', task_id='caller-task',
            scope_ref='user:fixture', mode='read-only', decision_ref='user:fixture'))
        self.report = state.task_status(self.scope, 'codex', 'session-1')

    def invoke(self, *, enabled=False, command=('task', 'status'), source_error=None):
        args = [str(SCRIPT), *command, '--cwd', str(self.fixture.repo)]
        if command in (('task', 'status'), ('evidence', 'status')):
            args += ['--client', 'codex', '--session', 'session-1']
        if enabled:
            args.append('--prepare-observability')
        output = io.StringIO()
        with patch.object(sys, 'path', list(sys.path)), patch.object(sys, 'argv', args), redirect_stdout(output), \
                patch.object(state, 'task_status', return_value=self.report, side_effect=source_error) as source:
            namespace = runpy.run_path(str(SCRIPT))
            code = namespace['control_main']()
        return code, json.loads(output.getvalue()), source

    def test_disabled_preserves_original_status_without_importing_helper(self):
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name == 'runtime.observability':
                raise AssertionError('Disabled status imported observability')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=guarded_import), \
                patch.object(observability, 'prepare_task_status', side_effect=AssertionError('Unexpected preparation')) as prepare:
            code, result, source = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(result, self.report)
        self.assertNotIn('observability_preparation', result)
        source.assert_called_once_with(self.scope, 'codex', 'session-1')
        prepare.assert_not_called()

    def test_enabled_prepares_exact_existing_report_once_without_mutation(self):
        before = deepcopy(self.report)
        actual_prepare = observability.prepare_task_status
        events = []
        def prepare(report, *, enabled=False):
            events.append('prepare')
            self.assertIs(report, self.report)
            self.assertTrue(enabled)
            return actual_prepare(report, enabled=enabled)
        def status(*args):
            events.append('status')
            return self.report
        with patch.object(observability, 'prepare_task_status', side_effect=prepare) as prepared:
            code, result, source = self.invoke(enabled=True, source_error=status)
        self.assertEqual(code, 0)
        self.assertEqual(events, ['status', 'prepare'])
        source.assert_called_once_with(self.scope, 'codex', 'session-1')
        prepared.assert_called_once()
        extra = result.pop('observability_preparation')
        self.assertEqual(result, before)
        self.assertEqual(extra['status'], 'prepared')
        self.assertTrue(extra['data']['preparation_only'])
        self.assertEqual(extra['data']['task']['task_id'], 'caller-task')
        self.assertIsNone(extra['data']['usage'])
        self.assertIn('current_readiness_unverified', extra['data']['gaps'])
        self.assertEqual(self.report, before)

    def test_optional_failures_preserve_owner_report_and_hide_exception_content(self):
        for failure in (ValueError('PRIVATE secret'), RuntimeError('PRIVATE secret'), OSError('PRIVATE path')):
            with self.subTest(error_type=type(failure).__name__), \
                    patch.object(observability, 'prepare_task_status', side_effect=failure):
                code, result, source = self.invoke(enabled=True)
            self.assertEqual(code, 0)
            self.assertEqual(result.pop('observability_preparation'), {'status': 'unavailable'})
            self.assertEqual(result, self.report)
            self.assertNotIn('PRIVATE', json.dumps(result))
            source.assert_called_once()

    def test_optional_import_failure_is_local_unavailability(self):
        original_import = builtins.__import__
        def missing_helper(name, *args, **kwargs):
            if name == 'runtime.observability':
                raise ImportError('PRIVATE installation details')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing_helper):
            code, result, source = self.invoke(enabled=True)
        self.assertEqual(code, 0)
        self.assertEqual(result.pop('observability_preparation'), {'status': 'unavailable'})
        self.assertEqual(result, self.report)
        self.assertNotIn('PRIVATE', json.dumps(result))
        source.assert_called_once()

    def test_status_failure_is_not_hidden_as_optional_unavailability(self):
        with patch.object(observability, 'prepare_task_status') as prepare:
            code, result, source = self.invoke(enabled=True, source_error=startup.StartupError('Owner status unavailable'))
        self.assertEqual(code, 1)
        self.assertEqual(result, {'ok': False, 'error': 'Owner status unavailable'})
        source.assert_called_once()
        prepare.assert_not_called()

    def test_keyboard_interrupt_and_system_exit_are_not_swallowed(self):
        for exception in (KeyboardInterrupt(), SystemExit(23)):
            with self.subTest(exception_type=type(exception).__name__), \
                    patch.object(observability, 'prepare_task_status', side_effect=exception), \
                    self.assertRaises(type(exception)):
                self.invoke(enabled=True)

    def test_flag_is_rejected_on_other_controls_before_owner_operations(self):
        for command in (('task', 'start'), ('task', 'update'), ('task', 'retire'), ('evidence', 'status')):
            with self.subTest(command=command), patch.object(observability, 'prepare_task_status') as prepare:
                code, result, source = self.invoke(enabled=True, command=command)
            self.assertEqual(code, 1)
            self.assertFalse(result['ok'])
            source.assert_not_called()
            prepare.assert_not_called()

    def test_real_cli_preparation_preserves_state_and_ordinary_status(self):
        state_dir = self.scope.runtime.state_dir
        before = {p.relative_to(state_dir): p.read_bytes() for p in state_dir.rglob('*') if p.is_file()}
        flags = ('--client', 'codex', '--session', 'session-1')
        def status(*options):
            result = subprocess.run([sys.executable, '-I', '-B', str(self.driver), 'task', 'status',
                '--cwd', str(self.fixture.repo), *options], input=b'', capture_output=True, timeout=20)
            self.assertEqual(result.stderr, b'')
            return result.returncode, json.loads(result.stdout)
        code, normal = status(*flags)
        self.assertEqual(code, 0)
        code, prepared = status(*flags, '--prepare-observability')
        self.assertEqual(code, 0, prepared)
        extra = prepared.pop('observability_preparation')
        self.assertEqual(prepared, normal)
        self.assertEqual(extra['status'], 'prepared')
        self.assertEqual(extra['data']['task']['task_id'], 'caller-task')
        self.assertTrue(extra['data']['preparation_only'])
        self.assertEqual({p.relative_to(state_dir): p.read_bytes() for p in state_dir.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    unittest.main()
