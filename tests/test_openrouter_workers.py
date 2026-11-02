import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid
import os
import signal
import sys
import time
from contextlib import contextmanager
from unittest.mock import patch
from runtime import openrouter_settings as settings
from runtime import council_snapshot
from tests.council_fixtures import CouncilCase
import hashlib
from tests.openrouter_fixtures import enroll, configure

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/openrouter_workers.py'


class WorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = None
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('workers', SCRIPT)
            cls.api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.api)

    def setUp(self):
        self.assertIsNotNone(self.api, 'worker runner must exist')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = enroll(self).repository_root
        (self.root / 'auth').mkdir(mode=0o700)
        executable = self.root / 'worker'
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o700)
        self.settings = configure(self.source, executable=executable, auth=self.root / 'auth', routes=('workers',))
        self.runs = Path(self.settings['worker_root'])
        (self.source / 'input.txt').write_text('three lines\nof evidence\nfor review\n')
        self.brief = dict(task_id='pilot-1', role='Scout', specialty='File analysis',
                          coordinator_chat_id=str(uuid.uuid4()), model='vendor/model',
                          model_display_name='Model', provider='provider', objective='Read input.txt and count lines.',
                          source_root=str(self.source), files=['input.txt'], timeout_seconds=5,
                          steps=3, approval_ref='user-approved', effort='low')

    def test_disable_blocks_preparation_and_launch_without_replay(self):
        run = self.api.prepare(self.runs, self.brief)
        settings.disable(self.source, {'expected_revision': self.settings['revision']})
        with self.assertRaisesRegex(settings.SettingsError, 'openrouter_disabled'):
            self.api.prepare(self.runs, dict(self.brief, task_id='blocked'))
        result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
        self.assertEqual(result['failure'], {'category': 'integration', 'reason': 'openrouter_disabled', 'launch_stage': 'before_spawn'})
        self.assertIs(result['native_fallback_ready'], True)
        self.assertIs(result['local_execution_stopped'], True)
        with self.assertRaisesRegex(ValueError, 'already_started'):
            self.api.execute(run, self.root / 'worker', self.root / 'auth')

    def test_reenable_does_not_admit_old_preparation(self):
        run = self.api.prepare(self.runs, self.brief)
        report = settings.disable(self.source, {'expected_revision': self.settings['revision']})
        settings.enable(self.source, {'expected_revision': report['revision']})
        result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
        self.assertEqual(result['failure']['reason'], 'openrouter_preparation_stale')

    def test_busy_admission_retries_without_launch_and_resumes_original_deadline(self):
        exe = self.fixture("print(json.dumps({'type':'text','part':{'text':'Finding'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, self.brief)
        real_admission = self.api.settings.admission
        @contextmanager
        def busy(*args, **kwargs):
            raise settings.SettingsError('openrouter_operation_busy')
            yield
        with patch.object(self.api.settings, 'admission', busy), patch.object(self.api.time, 'sleep') as wait, patch.object(self.api.subprocess, 'Popen') as spawn:
            result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result['state'], 'not_launched')
        self.assertTrue(result['local_execution_stopped'])
        self.assertFalse(result['native_fallback_ready'])
        self.assertTrue(self.api.resumable(result))
        self.assertEqual(wait.call_count, 2)
        spawn.assert_not_called()
        deadline = result['deadline_monotonic']
        with patch.object(self.api.settings, 'admission', real_admission), patch.object(self.api.subprocess, 'Popen', wraps=self.api.subprocess.Popen) as spawn:
            resumed = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(resumed['state'], 'awaiting_verification')
        self.assertEqual(resumed['deadline_monotonic'], deadline)
        self.assertEqual(len([call for call in spawn.call_args_list if call.args[0][0] == str(exe)]), 1)
        with self.assertRaisesRegex(ValueError, 'already_started'):
            self.api.execute(run, exe, self.root / 'auth')

    def test_short_busy_releases_admission_before_backoff_and_launches_once(self):
        run = self.api.prepare(self.runs, self.brief)
        real_admission = self.api.settings.admission
        held, calls = [], []
        @contextmanager
        def admission(*args, **kwargs):
            with real_admission(*args, **kwargs) as report:
                held.append(True)
                try: yield report
                finally: held.pop()
        @contextmanager
        def guard():
            calls.append(True)
            if len(calls) < 3:
                from runtime.startup import StartupError
                raise StartupError('Onboarding plan busy; retry the same expected revision')
            yield
        def wait(_): self.assertFalse(held)
        with patch.object(self.api.settings, 'admission', admission), patch.object(self.api.time, 'sleep', side_effect=wait), patch.object(self.api.subprocess, 'Popen', wraps=self.api.subprocess.Popen) as spawn:
            result = self.api.execute(run, self.root / 'worker', self.root / 'auth', launch_guard=guard)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len([call for call in spawn.call_args_list if call.args[0][0] == str(self.root / 'worker')]), 1)
        self.assertNotEqual(result['state'], 'not_launched')

    def test_interrupted_backoff_and_ambiguous_spawn_cannot_resume(self):
        for index, interrupted in enumerate((True, False)):
            run = self.api.prepare(self.runs, dict(self.brief, task_id='interrupted-' + str(index)))
            launch = self.api.subprocess.Popen
            def ambiguous(command, **kwargs):
                if command[0] == str(self.root / 'worker'): raise OSError('SECRET')
                return launch(command, **kwargs)
            with patch.object(self.api.settings, 'admission', side_effect=settings.SettingsError('openrouter_operation_busy')) if interrupted else patch.object(self.api.subprocess, 'Popen', side_effect=ambiguous), patch.object(self.api.time, 'sleep', side_effect=KeyboardInterrupt):
                result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
            self.assertEqual(result['state'], 'unresolved')
            with self.assertRaisesRegex(ValueError, 'already_started'):
                self.api.execute(run, self.root / 'worker', self.root / 'auth')

    def test_ambiguous_spawn_retains_unknown_stop_without_replay(self):
        for error, category in ((TimeoutError, 'deadline'), (KeyboardInterrupt, 'interrupted')):
            with self.subTest(error=error.__name__):
                run = self.api.prepare(self.runs, dict(self.brief, task_id='spawn-' + category))
                launch = self.api.subprocess.Popen
                def interrupted(command, **kwargs):
                    if command[0] == str(self.root / 'worker'): raise error()
                    return launch(command, **kwargs)
                with patch.object(self.api.subprocess, 'Popen', side_effect=interrupted):
                    result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
                self.assertEqual(result['state'], 'unresolved')
                self.assertEqual(result['failure'], dict(category=category, launch_stage='spawn'))
                self.assertIsNone(result['session_id'])
                self.assertEqual(json.loads((run / 'result.json').read_text()), result)
                self.assertFalse(result['local_execution_stopped'])
                self.assertFalse(result['native_fallback_ready'])
                self.assertFalse(self.api.resumable(result))
                with self.assertRaisesRegex(ValueError, 'already_started'):
                    self.api.execute(run, self.root / 'worker', self.root / 'auth')

    def test_allowlisted_policy_context_and_router_diagnostics(self):
        for name, category in [('ContentFilterError', 'policy'), ('ContextOverflowError', 'context')]:
            result = self.api.terminal_failure({'name': name, 'data': {'message': 'SECRET'}})
            self.assertEqual(result, dict(category=category, error_class=name))
        for code in ('content_policy_violation', 'refusal'):
            result = self.api.terminal_failure({'data': {'responseBody': json.dumps({'error': {'code': code, 'message': 'SECRET'}})}})
            self.assertEqual(result, dict(category='policy', router_code=code))
        for error_type in ('content_policy_violation', 'refusal'):
            result = self.api.terminal_failure({'name': 'APIError', 'data': {'statusCode': 403,
                'responseBody': json.dumps({'error': {'code': 403, 'message': 'SECRET',
                    'metadata': {'error_type': error_type, 'raw': 'SECRET', 'provider_name': 'SECRET'}}})}})
            self.assertEqual(result, dict(category='policy', router_code=error_type, status_code=403))
            self.assertNotIn('SECRET', json.dumps(result))

    def test_terminal_finish_reasons_are_safe_and_do_not_accept_truncated_answers(self):
        for index, reason in enumerate(('length', 'content-filter', 'SECRET')):
            exe = self.fixture("print(json.dumps({'type':'text','part':{'text':'Partial finding'}}))\n"
                + "print(json.dumps({'type':'step_finish','part':{'reason':%r,'cost':0,'tokens':{}}}))\n" % reason)
            run = self.api.prepare(self.runs, dict(self.brief, task_id='finish-' + str(index)))
            result = self.api.execute(run, exe, self.root / 'auth')
            self.assertEqual(result['state'], 'unresolved')
            self.assertEqual(result['failure'].get('terminal_reason'), reason if reason != 'SECRET' else None)
            self.assertNotIn('SECRET', (run / 'result.json').read_text() + (run / 'progress.jsonl').read_text())

    def test_resumption_never_renews_expired_deadline_or_accepts_unsafe_preparation(self):
        for index, change in enumerate(('deadline', 'clock', 'symlink', 'permissions', 'unexpected_file')):
            run = self.api.prepare(self.runs, dict(self.brief, task_id='resume-check-' + str(index)))
            with patch.object(self.api.settings, 'admission', side_effect=settings.SettingsError('openrouter_operation_busy')), patch.object(self.api.time, 'sleep'):
                before = self.api.execute(run, self.root / 'worker', self.root / 'auth')
            self.assertEqual(before['state'], 'not_launched')
            if change == 'symlink':
                (run / 'cache').rmdir(); (run / 'cache').symlink_to(self.root / 'auth', target_is_directory=True)
            elif change == 'permissions': (run / 'cache').chmod(0o755)
            elif change == 'unexpected_file': (run / 'cache/stranger').write_text('preserve')
            launch = self.api.subprocess.Popen
            clock = patch.object(self.api.time, 'monotonic', return_value=before['deadline_monotonic'] + 1) if change == 'deadline' else patch.object(self.api.time, 'monotonic', wraps=time.monotonic)
            wall = patch.object(self.api.time, 'time', return_value=time.time() + 100) if change == 'clock' else patch.object(self.api.time, 'time', wraps=time.time)
            with clock, wall, patch.object(self.api.subprocess, 'Popen', wraps=launch) as spawn:
                result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
            self.assertEqual(result['state'], 'unresolved')
            self.assertEqual(result['deadline_monotonic'], before['deadline_monotonic'])
            self.assertEqual([call for call in spawn.call_args_list if call.args[0][0] == str(self.root / 'worker')], [])
            if change == 'unexpected_file': self.assertEqual((run / 'cache/stranger').read_text(), 'preserve')
            if change in ('permissions', 'unexpected_file'):
                self.assertEqual(result['failure'].get('reason'), 'unsafe_worker_preparation_directory')

    def test_safe_projection_ignores_malformed_nested_and_unrecognized_local_errors(self):
        for failure in ({'category': ['SECRET'], 'reason': {'SECRET': 'SECRET'}},
                        {'category': 'policy', 'error_class': ['SECRET'], 'status_code': True, 'retry_after_seconds': float('nan')}):
            projected = self.api.safe_failure(failure)
            self.assertNotIn('SECRET', json.dumps(projected))
        run = self.api.prepare(self.runs, self.brief)
        with patch.object(self.api.settings, 'admission', side_effect=settings.SettingsError('SECRET')):
            self.api.execute(run, self.root / 'worker', self.root / 'auth')
        self.assertNotIn('SECRET', (run / 'result.json').read_text() + (run / 'progress.jsonl').read_text())

    def test_admission_never_reacquires_after_five_second_control_budget(self):
        run = self.api.prepare(self.runs, self.brief)
        clock = [100.0]
        def busy(*args, **kwargs):
            clock[0] += 4
            raise settings.SettingsError('openrouter_operation_busy')
        with patch.object(self.api.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(self.api.time, 'time', return_value=200), patch.object(self.api.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), patch.object(self.api.settings, 'admission', side_effect=busy) as admission:
            result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
        self.assertEqual(result['state'], 'not_launched')
        self.assertEqual(admission.call_count, 1)
        self.assertEqual(clock[0], 105)

    def test_not_launched_settles_existing_visibility_owner(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        run = self.api.prepare(self.runs, self.brief)
        client = Mock()
        module = SimpleNamespace(Client=lambda _: client, _event=lambda _, event, *args: event)
        with patch.object(self.api.importlib, 'import_module', return_value=module):
            publish = self.api.habblaud_publisher(self.root, run, 'fixture', 'fixture')
            publish('not_launched')
        self.assertIn('SubagentStop', [call.args[1] for call in client.event.call_args_list])

    def test_output_limits_and_malformed_streams_retain_fixed_safe_reasons(self):
        for index, reason in enumerate(('output_limit', 'malformed_event', 'unterminated_event')):
            body = ("print('x' * 100)\n" if reason == 'output_limit' else
                    "print('{SECRET')\n" if reason == 'malformed_event' else "sys.stdout.write('{SECRET')\n")
            exe = self.fixture(body)
            run = self.api.prepare(self.runs, dict(self.brief, task_id='output-reason-' + str(index)))
            with patch.object(self.api, 'LIMIT', 64 if reason == 'output_limit' else 4 * 1024 * 1024):
                result = self.api.execute(run, exe, self.root / 'auth')
            self.assertEqual(result['failure'].get('reason'), reason)
            self.assertNotIn('SECRET', (run / 'result.json').read_text() + (run / 'progress.jsonl').read_text())

    def test_worker_rejects_alternate_runtime_and_unmanaged_preparation(self):
        for index, change in enumerate(('executable', 'auth', 'stamp')):
            with self.subTest(change=change):
                run = self.api.prepare(self.runs, dict(self.brief, task_id=f'mismatch-{index}'))
                executable, auth = self.root / 'worker', self.root / 'auth'
                if change == 'executable': executable = self.root / 'other-worker'
                elif change == 'auth': auth = self.root / 'other-auth'
                else:
                    brief = json.loads((run / 'brief.json').read_text())
                    del brief['management']
                    (run / 'brief.json').write_text(json.dumps(brief))
                result = self.api.execute(run, executable, auth)
                self.assertEqual(result['failure']['reason'], 'openrouter_preparation_unmanaged'
                                 if change == 'stamp' else 'openrouter_configuration_mismatch')
                self.assertIs(result['native_fallback_ready'], True)
        with self.assertRaisesRegex(settings.SettingsError, 'openrouter_configuration_mismatch'):
            self.api.prepare(self.root / 'other-runs', self.brief)

    def test_launched_worker_finishes_and_verifies_after_disable(self):
        import subprocess
        started, release = self.root / 'started', self.root / 'release'
        exe = self.fixture(f'''from pathlib import Path
Path({str(started)!r}).touch()
while not Path({str(release)!r}).exists(): time.sleep(.01)
print(json.dumps({{'type':'text','part':{{'text':'There are three lines.'}}}}))
print(json.dumps({{'type':'step_finish','part':{{'reason':'stop','cost':0,'tokens':{{}}}}}}))
''')
        child = subprocess.Popen([sys.executable, '-I', '-B', str(SCRIPT), 'run', '--root', str(self.runs),
            '--opencode', str(exe), '--auth-data', str(self.root / 'auth')], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            child.stdin.write(json.dumps(self.brief)); child.stdin.close(); child.stdin = None
            deadline = time.monotonic() + 10
            while not started.exists() and child.poll() is None and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(started.exists(), 'worker must launch before disable')
            report = settings.disable(self.source, {'expected_revision': self.settings['revision']})
            self.assertFalse(report['enabled'])
            self.assertIsNone(child.poll(), 'disable must not wait for or stop admitted work')
            release.touch()
            output, error = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, error)
            result = json.loads(output.splitlines()[-1])
            self.assertEqual(result['state'], 'awaiting_verification')
            self.assertEqual(self.api.verify(self.runs / 'pilot-1', 'accepted', ['Native line count equals three'])['state'], 'accepted')
            self.assertFalse(settings.status(self.source)['enabled'])
        finally:
            if child.poll() is None: child.kill(); child.wait()
            for stream in (child.stdout, child.stderr): stream.close()

    def test_snapshot_excludes_unselected_inputs_and_rejects_replay(self):
        (self.source / 'secret.txt').write_text('not selected')
        run = self.api.prepare(self.runs, self.brief, now=100)
        self.assertEqual((run / 'workspace/input.txt').read_text(), 'three lines\nof evidence\nfor review\n')
        self.assertFalse((run / 'workspace/secret.txt').exists())
        import subprocess
        actual_root = subprocess.check_output(['git', '-C', str(run/'workspace'), 'rev-parse', '--show-toplevel'], text=True).strip()
        self.assertEqual(actual_root, str(run/'workspace'))
        with self.assertRaises(FileExistsError):
            self.api.prepare(self.runs, self.brief, now=100)

    def test_unsafe_inputs_fail_before_run_creation(self):
        (self.source / 'link').symlink_to(self.source / 'input.txt')
        for files in [['../escape'], ['link'], ['/absolute'], ['.env']]:
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.api.prepare(self.runs, dict(self.brief, files=files), now=100)
        self.assertFalse((self.runs / 'pilot-1').exists())

    def test_prepare_without_credit_keeps_execution_and_replay_bounds(self):
        try:
            run = self.api.prepare(self.runs, self.brief)
        except ValueError as error:
            self.fail(f'Credit lookup must not be required: {error}')
        self.assertEqual(json.loads((run / 'result.json').read_text())['state'], 'prepared')
        with self.assertRaises(FileExistsError):
            self.api.prepare(self.runs, self.brief)

    def test_legacy_credit_is_ignored_and_not_saved(self):
        try:
            run = self.api.prepare(self.runs, dict(self.brief, credit={'remaining': 0}), now=999)
        except ValueError as error:
            self.fail(f'Legacy credit must not gate dispatch: {error}')
        self.assertNotIn('credit', json.loads((run / 'brief.json').read_text()))

    def test_permission_patterns_cannot_be_injected_through_filenames(self):
        for name in ['*', 'file?', '[a]', './input.txt', 'folder//file.txt']:
            if '/' not in name: (self.source/name).write_text('fixture')
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.api.prepare(self.runs, dict(self.brief, files=[name]), now=100)

    def test_permissions_deny_shell_writes_network_and_other_agents(self):
        config = self.api.configuration(self.brief)
        self.assertNotIn('.council-snapshot.json', config['agent']['pilot']['prompt'])
        self.assertNotIn('__council__/', config['agent']['pilot']['prompt'])
        permissions = config['permission']
        self.assertEqual(permissions['*'], 'deny')
        self.assertEqual(permissions['read']['*'], 'deny')
        self.assertEqual(permissions['read']['input.txt'], 'allow')
        self.assertNotIn('/absolute', permissions['read'])
        for tool in ['bash', 'edit', 'task', 'webfetch', 'external_directory']:
            self.assertNotEqual(permissions.get(tool), 'allow')
        self.assertFalse(config['provider']['openrouter']['models']['vendor/model']['options']['provider']['allow_fallbacks'])

    def test_shared_tool_output_exception_is_explicitly_denied_without_auth_changes(self):
        expected = str(self.root / 'auth/opencode/tool-output/*')
        executable = self.fixture('import os\n'
            + 'config = json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])\n'
            + f'assert config["permission"]["external_directory"] == {{"*": "deny", {expected!r}: "deny"}}\n'
            + f'assert os.environ["XDG_DATA_HOME"] == {str(self.root / 'auth')!r}\n'
            + 'print(json.dumps({"type":"text","part":{"text":"Runtime configuration verified"}}))\n'
            + 'print(json.dumps({"type":"step_finish","part":{"reason":"stop","cost":0,"tokens":{}}}))\n')
        for index, auth in enumerate((self.root / 'auth', self.root / 'auth/../auth')):
            with self.subTest(auth=auth):
                run = self.api.prepare(self.runs, dict(self.brief, task_id=f'cache-boundary-{index}'))
                result = self.api.execute(run, executable, auth)
                self.assertEqual(result['state'], 'awaiting_verification')

    def test_worker_reuses_qualified_catalog_without_a_background_refresh(self):
        exe = self.fixture("import os\nassert os.environ.get('OPENCODE_DISABLE_MODELS_FETCH') == 'true'\n"
                           "print(json.dumps({'type':'text','part':{'text':'Fixture answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, self.brief)
        self.assertEqual(self.api.execute(run, exe, self.root / 'auth')['state'], 'awaiting_verification')

    def test_missing_visibility_adapter_does_not_prevent_worker_execution(self):
        import subprocess
        brief = dict(self.brief)
        exe = self.fixture("print(json.dumps({'type':'error','error':{'message':'provider unavailable'}}))\n")
        result = subprocess.run([sys.executable, str(SCRIPT), 'run', '--root', str(self.runs),
                                 '--opencode', str(exe), '--auth-data', str(self.root/'auth'),
                                 '--visibility-plugin', str(self.root/'missing')], input=json.dumps(brief),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.runs/'pilot-1/result.json').read_text())['state'], 'failed')
        self.assertFalse(json.loads((self.runs/'pilot-1/visibility.json').read_text())['delivered'])

    def fixture(self, body):
        p = self.root / 'worker'
        p.write_text('#!/usr/bin/env python3\nimport json, sys, time\n' + body)
        p.chmod(0o700)
        return p

    def test_real_subprocess_records_tools_usage_and_requires_native_verification(self):
        exe = self.fixture('''sys.stdin.read()
for event in [
 {'type':'step_start','sessionID':'ses_1','part':{}},
 {'type':'tool_use','sessionID':'ses_1','part':{'tool':'read','state':{'status':'completed','output':'PRIVATE CONTENT'}}},
 {'type':'text','sessionID':'ses_1','part':{'text':'There are three lines.'}},
 {'type':'step_finish','sessionID':'ses_1','part':{'reason':'stop','cost':0.001,'tokens':{'input':10,'output':5,'reasoning':2,'cache':{'read':0,'write':0}}}}
]: print(json.dumps(event), flush=True)
''')
        run = self.api.prepare(self.runs, self.brief, now=100)
        result = self.api.execute(run, exe, self.root / 'auth', publish=lambda phase: None)
        self.assertEqual(result['state'], 'awaiting_verification')
        self.assertEqual(result['tools'], ['read'])
        self.assertAlmostEqual(result['reported_cost_usd'], 0.001)
        self.assertEqual(result['reported_tokens']['input'], 10)
        self.assertNotIn('PRIVATE CONTENT', (run / 'progress.jsonl').read_text())
        with self.assertRaises(ValueError): self.api.verify(run, 'accepted', [])
        self.assertEqual(self.api.verify(run, 'accepted', ['Native line count: 3; matches response'])['state'], 'accepted')

    def test_transport_machine_code_survives_without_secret_message(self):
        result = self.api.terminal_failure({'name': 'UnknownError', 'data': {
            'message': 'EPROTO: TLS handshake failed at https://SECRET/?token=SECRET'}})
        self.assertEqual(result, {'category': 'transport', 'error_code': 'EPROTO', 'failure_layer': 'transport'})
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(self.api.safe_failure(result), result)

    def test_error_even_with_zero_exit_never_becomes_success(self):
        exe = self.fixture("print(json.dumps({'type':'error','error':{'message':'SECRET'}}))\n")
        run = self.api.prepare(self.runs, self.brief, now=100)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result['state'], 'failed')
        self.assertNotIn('SECRET', (run / 'progress.jsonl').read_text())

    def test_structured_terminal_failures_retain_only_recognized_facts(self):
        self.assertTrue(callable(getattr(self.api, 'terminal_failure', None)), 'terminal failure evidence is required')
        for status, source, category in [(402, None, 'budget'),
                (402, 'openrouter_in_flight_budget', 'budget'),
                (403, 'openrouter_workspace_budget', 'budget'), (403, None, 'permission'),
                (401, None, 'authentication'), (429, None, 'rate_limit'),
                (503, None, 'service'), (400, None, 'invalid_request')]:
            with self.subTest(status=status, source=source):
                error = {'name': 'APIError', 'data': {'statusCode': status,
                    'message': 'SECRET', 'isRetryable': True,
                    'responseHeaders': {'Authorization': 'SECRET', 'retry-after-ms': '1500'},
                    'responseBody': json.dumps({'error': {'message': 'SECRET',
                        'metadata': {'limit_source': source}}})}}
                result = self.api.terminal_failure(error)
                expected = {'category': category, 'status_code': status, 'retry_after_seconds': 1.5}
                if source: expected['limit_source'] = source
                self.assertEqual(result, expected)
                self.assertNotIn('SECRET', json.dumps(result))

    def test_malformed_terminal_evidence_never_invents_budget_or_retry_count(self):
        self.assertTrue(callable(getattr(self.api, 'terminal_failure', None)), 'terminal failure evidence is required')
        for error in [None, [], 'SECRET', {'message': 'SECRET'},
                {'name': 'SECRET', 'data': ['SECRET']},
                {'data': {'statusCode': True, 'responseBody': 'SECRET'}},
                {'data': {'statusCode': '402', 'responseHeaders': {'retry-after': 'NaN'}}},
                {'data': {'responseBody': '[' * 2000}},
                {'data': {'responseBody': 'SECRET' * 10000}}]:
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(self.api.terminal_failure(error), {'category': 'unknown'})
        self.assertEqual(self.api.terminal_failure({'name': 'ContextOverflowError',
                         'data': {'message': 'SECRET'}}), {'category': 'context', 'error_class': 'ContextOverflowError'})
        result = self.api.terminal_failure({'data': {'statusCode': 403,
            'responseBody': json.dumps({'error': {'metadata': {'limit_source': 'SECRET'}}})}})
        self.assertEqual(result, {'category': 'permission', 'status_code': 403})

    def test_retry_hint_dates_and_invalid_values_are_advisory_only(self):
        self.assertTrue(callable(getattr(self.api, 'terminal_failure', None)), 'terminal failure evidence is required')
        from email.utils import formatdate
        for headers, expected in [({'Retry-After': '2'}, 2),
                ({'retry-after': formatdate(130, usegmt=True)}, 30),
                ({'retry-after': '-1'}, None), ({'retry-after-ms': 'inf'}, None),
                ({'retry-after': True}, None), ({'retry-after': []}, None)]:
            with self.subTest(headers=headers), patch.object(self.api.time, 'time', return_value=100):
                result = self.api.terminal_failure({'data': {'statusCode': 503, 'responseHeaders': headers}})
                self.assertEqual(result.get('retry_after_seconds'), expected)

    def test_terminal_failure_launches_once_and_allows_native_takeover_after_stop(self):
        launches = self.root / 'launches.txt'
        error = {'name': 'APIError', 'data': {'statusCode': 503,
                 'message': 'SECRET', 'responseBody': 'SECRET'}}
        exe = self.fixture("with open(%r, 'a') as stream: stream.write('launch\\n')\n" % str(launches) +
                           "print(json.dumps({'type':'error','error':%r}), flush=True)\n" % error)
        run = self.api.prepare(self.runs, self.brief)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(launches.read_text().splitlines(), ['launch'])
        self.assertEqual(result.get('failure'), {'category': 'service', 'status_code': 503, 'launch_stage': 'launched'})
        self.assertIs(result.get('local_execution_stopped'), True)
        self.assertIs(result.get('native_fallback_ready'), True)
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['upstream_cancellation_confirmed'])
        self.assertFalse(result['accounting_complete'])
        self.assertIsNone(result['reported_cost_usd'])
        self.assertNotIn('SECRET', (run / 'result.json').read_text())
        self.assertNotIn('SECRET', (run / 'progress.jsonl').read_text())

    def test_partial_usage_survives_terminal_error_without_becoming_a_complete_bill(self):
        exe = self.fixture("""for event in [
 {'type':'text','part':{'text':'Partial finding'}},
 {'type':'step_finish','part':{'reason':'tool-calls','cost':0.02,'tokens':{'input':20}}},
 {'type':'error','error':{'name':'APIError','data':{'statusCode':402}}}
]: print(json.dumps(event), flush=True)
""")
        run = self.api.prepare(self.runs, self.brief)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result.get('failure'), {'category': 'budget', 'status_code': 402, 'launch_stage': 'launched'})
        self.assertEqual(result['known_cost_subtotal_usd'], 0.02)
        self.assertEqual(result['reported_tokens']['input'], 20)
        self.assertIsNone(result['reported_cost_usd'])
        self.assertFalse(result['accounting_complete'])
        self.assertIs(result.get('native_fallback_ready'), True)
        with self.assertRaises(ValueError): self.api.verify(run, 'accepted', ['Partial output is not completion'])

    def test_deadline_stays_global_across_events_and_does_not_relaunch(self):
        launches = self.root / 'launches.txt'
        exe = self.fixture("with open(%r, 'a') as stream: stream.write('launch\\n')\n" % str(launches) +
                           "for _ in range(100):\n print(json.dumps({'type':'step_start','part':{}}), flush=True)\n time.sleep(.1)\n")
        run = self.api.prepare(self.runs, dict(self.brief, timeout_seconds=1))
        started = time.monotonic()
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(launches.read_text().splitlines(), ['launch'])
        self.assertEqual(result.get('failure'), {'category': 'deadline', 'launch_stage': 'launched'})
        self.assertEqual(result['state'], 'unresolved')
        self.assertIs(result.get('local_execution_stopped'), True)
        self.assertIs(result.get('native_fallback_ready'), True)

    def test_unconfirmed_process_group_stop_withholds_native_takeover(self):
        exe = self.fixture("print(json.dumps({'type':'error','error':{}}), flush=True)\n")
        run = self.api.prepare(self.runs, self.brief)
        with patch.object(self.api.os, 'killpg', side_effect=PermissionError('fixture refusal')):
            try:
                result = self.api.execute(run, exe, self.root / 'auth')
            except OSError as error:
                self.fail(f'Failed cleanup must remain explicit recovery evidence: {error}')
        self.assertEqual(result['state'], 'unresolved')
        self.assertIs(result.get('local_execution_stopped'), False)
        self.assertIs(result.get('native_fallback_ready'), False)

    def test_stalled_stdin_cannot_bypass_the_worker_deadline(self):
        exe = self.fixture('time.sleep(4)\n')
        run = self.api.prepare(self.runs, dict(self.brief, objective='😀' * 15000,
                                                       timeout_seconds=1))
        started = time.monotonic()
        result, processes = self.execute_with_backpressure(run, exe)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual(result.get('failure'), {'category': 'deadline', 'launch_stage': 'launched'})
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertIs(result.get('local_execution_stopped'), True)
        self.assertIs(result.get('native_fallback_ready'), True)

    def execute_with_backpressure(self, run, executable):
        launch = self.api.subprocess.Popen
        processes = []
        def start(command, **kwargs):
            process = launch(command, **kwargs)
            if command[0] == str(executable):
                processes.append(process)
                descriptor = process.stdin.fileno()
                os.set_blocking(descriptor, False)
                try:
                    # Fill the real pipe so the test does not depend on host capacity.
                    for _ in range(1024): os.write(descriptor, b'!' * 4096)
                    self.fail('fixture pipe did not reach backpressure')
                except BlockingIOError: pass
                finally: os.set_blocking(descriptor, True)
            return process
        with patch.object(self.api.subprocess, 'Popen', side_effect=start):
            result = self.api.execute(run, executable, self.root / 'auth')
        return result, processes

    def test_output_before_input_is_drained_without_losing_utf8_objective(self):
        import hashlib
        objective = '😀' * 16000
        expected = hashlib.sha256(objective.encode()).hexdigest()
        exe = self.fixture("import hashlib\n"
            "print(json.dumps({'type':'text','part':{'text':'x' * (1024 * 1024)}}), flush=True)\n"
            "payload = sys.stdin.buffer.read().lstrip(b'!')\n"
            "print(json.dumps({'type':'text','part':{'text':hashlib.sha256(payload).hexdigest()}}))\n"
            "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, dict(self.brief, objective=objective, timeout_seconds=2))
        started = time.monotonic()
        result, processes = self.execute_with_backpressure(run, exe)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(len(processes), 1)
        self.assertEqual(result['state'], 'awaiting_verification')
        self.assertEqual(result['response'].splitlines()[-1], expected)
        self.assertIs(result['local_execution_stopped'], True)
        self.assertIs(result['native_fallback_ready'], False)

    def test_launch_time_uses_the_original_deadline(self):
        exe = self.fixture("sys.stdin.read()\n"
            "print(json.dumps({'type':'text','part':{'text':'answer'}}))\n"
            "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, dict(self.brief, timeout_seconds=1))
        launch = self.api.subprocess.Popen
        processes = []
        def delayed(command, **kwargs):
            process = launch(command, **kwargs)
            if command[0] == str(exe): processes.append(process); time.sleep(1.2)
            return process
        with patch.object(self.api.subprocess, 'Popen', side_effect=delayed):
            result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result['failure'], {'category': 'deadline', 'launch_stage': 'launched'})
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertIs(result['local_execution_stopped'], True)
        self.assertIs(result['native_fallback_ready'], True)

    def test_expired_admission_does_not_launch_a_worker(self):
        from contextlib import contextmanager
        run = self.api.prepare(self.runs, dict(self.brief, timeout_seconds=1))
        admission, launch = self.api.settings.admission, self.api.subprocess.Popen
        processes = []
        @contextmanager
        def slow(*args, **kwargs):
            with admission(*args, **kwargs) as report:
                time.sleep(1.2)
                yield report
        def observed(command, **kwargs):
            process = launch(command, **kwargs)
            if command[0] == str(self.root / 'worker'): processes.append(process)
            return process
        with patch.object(self.api.settings, 'admission', slow), patch.object(self.api.subprocess, 'Popen', observed):
            result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
        self.assertEqual(processes, [])
        self.assertEqual(result['failure'], {'category': 'deadline', 'launch_stage': 'before_spawn'})
        self.assertIs(result['local_execution_stopped'], True)
        self.assertIs(result['native_fallback_ready'], True)

    def test_deeply_nested_event_remains_recoverable_without_replay(self):
        exe = self.fixture("sys.stdout.write('[' * 2000 + '0' + ']' * 2000 + '\\n')\n")
        run = self.api.prepare(self.runs, self.brief)
        decode = self.api.json.loads
        def older_decoder(value, *args, **kwargs):
            # Older supported Python decoders raise RecursionError for nested JSON.
            if isinstance(value, bytes) and value.startswith(b'['): raise RecursionError()
            return decode(value, *args, **kwargs)
        with patch.object(self.api.json, 'loads', side_effect=older_decoder):
            try:
                result = self.api.execute(run, exe, self.root / 'auth')
            except RecursionError:
                self.fail('Malformed nested output must remain an unresolved recoverable result')
        self.assertEqual(result['state'], 'unresolved')
        self.assertEqual(result.get('failure'), {'category': 'runtime_or_output', 'launch_stage': 'launched', 'reason': 'malformed_event'})
        self.assertIs(result.get('native_fallback_ready'), True)

    def test_cli_interruption_retires_the_worker_before_native_takeover(self):
        import subprocess
        pidfile = self.root / 'worker.pid'
        exe = self.fixture("import os\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'tool-calls','cost':0.02,'tokens':{'input':20}}}), flush=True)\n"
                           "with open(%r, 'w') as stream: stream.write(str(os.getpid()))\ntime.sleep(20)\n" % str(pidfile))
        process = subprocess.Popen([sys.executable, str(SCRIPT), 'run', '--root', str(self.runs),
                                    '--opencode', str(exe), '--auth-data', str(self.root / 'auth')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            process.stdin.write(json.dumps(self.brief))
            process.stdin.close()
            process.stdin = None
            until = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < until: time.sleep(.01)
            self.assertTrue(pidfile.exists(), 'fixture worker must start before interruption')
            result_path = self.runs / 'pilot-1/result.json'
            while time.monotonic() < until:
                if result_path.exists() and json.loads(result_path.read_text()).get('known_cost_subtotal_usd') == 0.02:
                    break
                time.sleep(.01)
            self.assertEqual(json.loads(result_path.read_text()).get('known_cost_subtotal_usd'), 0.02)
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, error)
            result = json.loads(output.splitlines()[-1])
            self.assertEqual(result['state'], 'unresolved')
            self.assertEqual(result.get('failure'), {'category': 'interrupted', 'launch_stage': 'launched'})
            self.assertIs(result.get('local_execution_stopped'), True)
            self.assertIs(result.get('native_fallback_ready'), True)
            self.assertEqual(result['known_cost_subtotal_usd'], 0.02)
            self.assertEqual(result['reported_tokens']['input'], 20)
            self.assertIsNone(result['reported_cost_usd'])
            self.assertFalse(result['accounting_complete'])
            with self.assertRaises(ProcessLookupError): os.kill(int(pidfile.read_text()), 0)
        finally:
            if process.poll() is None: process.kill(); process.wait()

    def test_native_rejection_allows_takeover_without_an_external_replay(self):
        exe = self.fixture("print(json.dumps({'type':'text','part':{'text':'Wrong answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, self.brief)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result['state'], 'awaiting_verification')
        self.assertIs(result.get('native_fallback_ready'), False)
        rejected = self.api.verify(run, 'rejected', ['Native inspection found the answer incorrect'])
        self.assertIs(rejected.get('native_fallback_ready'), True)

    def test_timeout_and_missing_finish_remain_unresolved(self):
        for index, body in enumerate(['time.sleep(5)\n', "print(json.dumps({'type':'text','part':{'text':'unfinished'}}))\n"]):
            brief = dict(self.brief, task_id=f'timeout-{index}', timeout_seconds=1)
            run = self.api.prepare(self.runs, brief, now=100)
            result = self.api.execute(run, self.fixture(body), self.root / 'auth')
            self.assertEqual(result['state'], 'unresolved')
            with self.assertRaises(ValueError): self.api.verify(run, 'accepted', ['self-reported success'])

    def test_malformed_part_is_persisted_as_unresolved(self):
        exe = self.fixture("print(json.dumps({'type':'step_finish','part':[]}))\n")
        run = self.api.prepare(self.runs, self.brief, now=100)
        self.assertEqual(self.api.execute(run, exe, self.root / 'auth')['state'], 'unresolved')
        self.assertEqual(json.loads((run / 'result.json').read_text())['state'], 'unresolved')

    def test_missing_step_cost_is_not_a_complete_total(self):
        exe = self.fixture("""for part in [{'reason':'tool-calls','cost':0.1}, {'reason':'stop'}]:
 print(json.dumps({'type':'step_finish','part':part}))
print(json.dumps({'type':'text','part':{'text':'answer'}}))
""")
        run = self.api.prepare(self.runs, self.brief, now=100)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertIsNone(result['reported_cost_usd'])
        self.assertEqual(result['known_cost_subtotal_usd'], 0.1)
        self.assertFalse(result['accounting_complete'])

    def test_descendant_cannot_outlive_exited_leader(self):
        pidfile = self.root / 'descendant.pid'
        exe = self.fixture("""import os, signal
pid=os.fork()
if pid == 0:
 signal.signal(signal.SIGTERM, signal.SIG_IGN)
 os.close(1)
 time.sleep(20)
else:
 with open(%r,'w') as f: f.write(str(pid))
 time.sleep(.1)
""" % str(pidfile))
        run = self.api.prepare(self.runs, self.brief, now=100)
        self.api.execute(run, exe, self.root / 'auth')
        pid = int(pidfile.read_text())
        try:
            time.sleep(.1)
            with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        finally:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass


    def test_real_authority_contention_keeps_original_prelaunch_owner_and_deadline(self):
        from runtime import changes, openrouter_settings
        exe = self.fixture("print(json.dumps({'type':'text','part':{'text':'Synthetic finding'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, self.brief)
        scope = openrouter_settings.resolve(self.source)
        with changes._authority_guards([scope.runtime.state_dir]), patch.object(self.api.time, 'sleep') as wait:
            result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result['state'], 'not_launched')
        self.assertEqual(result['failure']['reason'], 'openrouter_operation_busy')
        self.assertEqual(result['failure']['launch_stage'], 'before_spawn')
        self.assertEqual(wait.call_count, 2)
        self.assertTrue(self.api.resumable(result))
        self.assertTrue(result['local_execution_stopped'])
        deadline = result['deadline_monotonic']
        with patch.object(self.api.subprocess, 'Popen', wraps=self.api.subprocess.Popen) as spawn:
            resumed = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(resumed['state'], 'awaiting_verification')
        self.assertEqual(resumed['deadline_monotonic'], deadline)
        self.assertEqual(len([call for call in spawn.call_args_list if call.args[0][0] == str(exe)]), 1)
        with self.assertRaisesRegex(ValueError, 'already_started'):
            self.api.execute(run, exe, self.root / 'auth')

    def test_uncertain_authority_and_storage_errors_never_retry_or_resume(self):
        from runtime import changes
        for index, error in enumerate((changes.Pending('Shared state authority has an active or uncertain D4 operation; resume its bound journal'),
                                       OSError('unrelated storage failure'))):
            run = self.api.prepare(self.runs, dict(self.brief, task_id='blocked-authority-' + str(index)))
            with patch.object(changes, '_authority_guards', side_effect=error), patch.object(self.api.time, 'sleep') as wait:
                result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
            self.assertEqual(result['state'], 'failed')
            self.assertEqual(result['failure']['reason'], 'openrouter_storage_or_scope_unavailable')
            self.assertEqual(wait.call_count, 0)
            self.assertTrue(result['local_execution_stopped'])
            self.assertFalse(self.api.resumable(result))


class CouncilWorkerTests(CouncilCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('council_workers', SCRIPT)
        cls.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.api)

    def setUp(self):
        super().setUp()
        (self.repo / 'evidence.txt').write_text('approved evidence')
        self.snapshot = self.root / 'evidence'
        self.manifest = council_snapshot.capture(self.repo, self.snapshot, self.approved(['evidence.txt']))
        self.brief = dict(task_id='council-one', role='Investigator', specialty='Independent analysis',
            coordinator_chat_id=str(uuid.uuid4()), model='vendor/model', model_display_name='Model',
            provider='provider', objective='Analyze evidence.txt', source_root=str(self.repo),
            files=['evidence.txt'], timeout_seconds=5, steps=4, approval_ref='approved', effort='high',
            council=dict(run_id='round-one', phase='investigate', member_id='external-one'),
            input_tokens=10000, output_tokens=4096,
            capabilities=dict(model='vendor/model', provider='provider', context_length=20000,
                max_output_tokens=8000, max_input_tokens=12000, tools=True,
                reasoning={'efforts': ['low', 'high']}, observed_at='2026-10-29T00:00:00Z',
                source='controlled endpoint fixture'),
            permission_qualification=dict(opencode_sha256=hashlib.sha256(self.executable.read_bytes()).hexdigest(),
                version='1.19.5', profile='council-readonly-v2', evidence='local controlled qualification'))

    def prepare(self, **changes):
        self.assertTrue(callable(getattr(self.api, 'prepare_council', None)), 'Council preparation is required')
        return self.api.prepare_council(self.root, dict(self.brief, **changes), self.snapshot, self.manifest)

    def prepare_current(self, **changes):
        from runtime import council
        brief = dict(self.brief, **changes)
        self.api._validate_council_brief(brief)
        council_snapshot.verify(self.snapshot, self.manifest)
        seat = {key: brief[key] for key in ('model', 'model_display_name', 'provider', 'effort',
            'input_tokens', 'output_tokens', 'steps', 'capabilities', 'permission_qualification')}
        seat.update(kind='external', selection='Controlled current Council route')
        request = dict(mode='repository', run_id=brief['task_id'], question=brief['objective'],
            constraints='Read only', coordinator_chat_id=brief['coordinator_chat_id'], approval_ref=brief['approval_ref'],
            approved_files=self.manifest['files'], research='Fixture research',
            phase_seconds=dict(investigate=600, critique=360, synthesis=240),
            roster={'native': self.native_seat(), 'external-one': seat, 'external-two': dict(seat)})
        run = Path(council.prepare(self.repo, request)['run'])
        return Path(council.packet(run, 'investigate', 'external-one')['worker_run'])

    def test_two_isolated_council_runs_keep_endpoint_allowances_and_pin(self):
        first = self.prepare()
        second = self.prepare(task_id='council-two', output_tokens=6000,
            capabilities=dict(self.brief['capabilities'], reasoning={'fixed': 'high'}))
        for run, fixed in [(first, False), (second, True)]:
            brief = json.loads((run / 'brief.json').read_text())
            model = self.api.configuration(brief)['provider']['openrouter']['models']['vendor/model']
            self.assertEqual(model['options']['maxOutputTokens'], 8000)
            self.assertEqual(model['options']['provider'], {'only': ['provider'], 'allow_fallbacks': False})
            self.assertEqual('reasoning' in model['options'], not fixed)
            self.assertEqual(model['limit']['input'], 12000)
            self.api._verify_council_copy(run / 'workspace', brief['evidence']['workspace_manifest'])
            self.assertTrue((run / 'workspace/.git').is_dir())
        self.assertNotEqual(first / 'workspace', second / 'workspace')
        pilot = {k: v for k, v in self.brief.items() if k not in (
            'council', 'capabilities', 'input_tokens', 'output_tokens', 'permission_qualification')}
        self.assertEqual(self.api.configuration(pilot)['provider']['openrouter']['models']['vendor/model']['options']['maxOutputTokens'], 2048)

    def test_council_rejects_unverified_limits_effort_and_markers(self):
        cases = [dict(output_tokens=0), dict(input_tokens=True), dict(input_tokens=2**31),
            dict(effort='low'), dict(capabilities=dict(self.brief['capabilities'], tools=False)),
            dict(capabilities=dict(self.brief['capabilities'], provider='other')),
            dict(council=dict(self.brief['council'], phase='synthesis')),
            dict(permission_qualification={}), dict(evidence={'route': 'council'})]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.prepare(**changes)
        self.assertFalse((self.root / self.brief['task_id']).exists())
        with self.assertRaises(ValueError): self.api.prepare(self.root, self.brief)

    def test_council_requires_manifest_files_and_immutable_source(self):
        with self.assertRaises(ValueError): self.prepare(files=['not-approved'])
        (self.snapshot / 'evidence.txt').chmod(0o600)
        (self.snapshot / 'evidence.txt').write_text('tampered')
        with self.assertRaises(ValueError): self.prepare()

    def test_council_disable_before_spawn_and_replay(self):
        run = self.prepare()
        settings.disable(self.repo, {'expected_revision': self.settings['revision']})
        with patch.object(self.api.subprocess, 'Popen', wraps=self.api.subprocess.Popen) as spawn:
            result = self.api.execute(run, self.executable, self.auth)
        self.assertEqual(sum(call.args[0][0] == str(self.executable) for call in spawn.call_args_list), 0)
        self.assertEqual(result['failure']['reason'], 'openrouter_disabled')
        with self.assertRaisesRegex(ValueError, 'already_started'): self.api.execute(run, self.executable, self.auth)

    def test_council_preparation_checks_supplied_original_management_binding(self):
        stamp = dict(scope_id=self.settings['scope_id'], revision=self.settings['revision'])
        with self.assertRaisesRegex(settings.SettingsError, 'openrouter_scope_changed'):
            self.api.prepare_council(self.root, self.brief, self.snapshot, self.manifest,
                                     **dict(stamp, scope_id='another-checkout'))
        disabled = settings.disable(self.repo, {'expected_revision': stamp['revision']})
        settings.enable(self.repo, {'expected_revision': disabled['revision']})
        with patch.object(self.api.subprocess, 'Popen', wraps=self.api.subprocess.Popen) as spawn:
            with self.assertRaisesRegex(settings.SettingsError, 'openrouter_preparation_stale'):
                self.api.prepare_council(self.root, self.brief, self.snapshot, self.manifest, **stamp)
        self.assertFalse((self.root / self.brief['task_id']).exists())
        self.assertEqual([call for call in spawn.call_args_list if call.args[0][0] == str(self.executable)], [])

    def test_council_prepared_worker_cannot_launch_under_changed_auth(self):
        run = self.prepare()
        disabled = settings.disable(self.repo, {'expected_revision': self.settings['revision']})
        auth = self.temporary / 'changed-auth'
        auth.mkdir(mode=0o700)
        configured = settings.configure(self.repo, dict(expected_revision=disabled['revision'],
            routes=['council'], opencode=str(self.executable), auth_data=str(auth)))
        settings.enable(self.repo, {'expected_revision': configured['revision']})
        with patch.object(self.api.subprocess, 'Popen', wraps=self.api.subprocess.Popen) as spawn:
            result = self.api.execute(run, self.executable, auth)
        self.assertEqual(result['failure']['reason'], 'openrouter_preparation_stale')
        self.assertEqual([call for call in spawn.call_args_list if call.args[0][0] == str(self.executable)], [])
        self.assertTrue(result['local_execution_stopped'])

    def test_council_real_fixture_single_spawn_and_qualified_binary_binding(self):
        count = self.temporary / 'spawns'
        self.executable.write_text('#!/usr/bin/env python3\nimport json\nfrom pathlib import Path\n'
            + f'with Path({str(count)!r}).open("a") as f: f.write("spawn\\n")\n'
            + 'print(json.dumps({"type":"text","part":{"text":"fixture result"}}))\n'
            + 'print(json.dumps({"type":"step_finish","part":{"reason":"stop","cost":0,"tokens":{"input":1,"output":1}}}))\n')
        self.brief['permission_qualification']['opencode_sha256'] = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        run = self.prepare_current()
        result = self.api.execute(run, self.executable, self.auth)
        self.assertEqual(result['state'], 'awaiting_verification')
        self.assertTrue(result['local_execution_stopped'])
        with self.assertRaisesRegex(ValueError, 'already_started'): self.api.execute(run, self.executable, self.auth)
        self.assertEqual(count.read_text().splitlines(), ['spawn'])
        other = self.prepare_current(task_id='second-run', output_tokens=1024)
        self.assertEqual(self.api.execute(other, self.executable, self.auth)['state'], 'awaiting_verification')
        self.assertNotEqual(run / 'workspace', other / 'workspace')
        second = self.prepare_current(task_id='changed-runtime')
        self.executable.write_text('#!/bin/sh\nexit 0\n')
        result = self.api.execute(second, self.executable, self.auth)
        self.assertEqual(result['state'], 'unresolved')
        self.assertEqual(count.read_text().splitlines(), ['spawn', 'spawn'])

    def test_council_changed_copy_and_forged_route_never_launch(self):
        for index, change in enumerate(('copy', 'marker')):
            run = self.prepare(task_id=f'invalid-{index}')
            if change == 'copy':
                (run / 'workspace/evidence.txt').chmod(0o600)
                (run / 'workspace/evidence.txt').write_text('changed')
            else:
                brief = json.loads((run / 'brief.json').read_text())
                del brief['council']
                (run / 'brief.json').write_text(json.dumps(brief))
            with patch.object(self.api.subprocess, 'Popen', wraps=self.api.subprocess.Popen) as spawn:
                result = self.api.execute(run, self.executable, self.auth)
            self.assertEqual(sum(call.args[0][0] == str(self.executable) for call in spawn.call_args_list), 0)
            self.assertEqual(result['state'], 'unresolved')


    def test_council_rejects_superseded_permission_qualification(self):
        qualification = dict(self.brief['permission_qualification'], profile='council-readonly-v1')
        with self.assertRaisesRegex(ValueError, 'council_permission_qualification_required'):
            self.prepare(permission_qualification=qualification)

    def test_council_honors_small_endpoint_and_larger_finite_step_budget(self):
        run = self.prepare(input_tokens=2000, output_tokens=1024, steps=9, timeout_seconds=301,
            capabilities=dict(self.brief['capabilities'], context_length=4096,
                max_output_tokens=1024, max_input_tokens=2000))
        model = self.api.configuration(json.loads((run / 'brief.json').read_text()))['provider']['openrouter']['models']['vendor/model']
        self.assertEqual(model['options']['maxOutputTokens'], 1024)


    def test_council_oversized_report_stops_without_persisting_excess(self):
        self.executable.write_text('#!/usr/bin/env python3\nimport json\n'
            + 'print(json.dumps({"type":"text","part":{"text":"x" * (128 * 1024)}}))\n')
        self.brief['permission_qualification']['opencode_sha256'] = hashlib.sha256(self.executable.read_bytes()).hexdigest()
        run = self.prepare_current()
        result = self.api.execute(run, self.executable, self.auth)
        self.assertEqual(result['state'], 'unresolved')
        self.assertLessEqual(len(result['response'].encode()), 128 * 1024)
        self.assertTrue(result['local_execution_stopped'])
        self.assertEqual(result['failure'].get('reason'), 'council_report_limit')

    def test_grouped_external_chair_contract_preserves_free_variant_and_prices(self):
        brief = dict(self.brief, model='vendor/model:free', canonical_model='vendor/model',
            pricing=dict(prompt_usd_per_million=0, completion_usd_per_million=0,
                source='observed endpoint', observed_at='2026-10-30'),
            council=dict(run_id='round-one', phase='synthesis', member_id='member-three', group='Free', chair='member-three'),
            capabilities=dict(self.brief['capabilities'], model='vendor/model:free'))
        self.api._validate_council_brief(brief)
        model = self.api.configuration(brief)['provider']['openrouter']['models']['vendor/model:free']
        self.assertFalse(model['options']['provider']['allow_fallbacks'])
        for change in ('price', 'variant', 'canonical', 'chair'):
            mutated = json.loads(json.dumps(brief))
            if change == 'price': mutated['pricing']['completion_usd_per_million'] = .1
            elif change == 'variant':
                mutated['model'] = 'vendor/model'; mutated['capabilities']['model'] = mutated['model']
            elif change == 'canonical': mutated['canonical_model'] = ''
            else: mutated['council']['chair'] = 'member-one'
            with self.subTest(change=change), self.assertRaises(ValueError): self.api._validate_council_brief(mutated)

    def default_reasoning_brief(self):
        return dict(self.brief, model='vendor/model:free', canonical_model='vendor/model', effort='default',
            pricing=dict(prompt_usd_per_million=0, completion_usd_per_million=0,
                source='observed endpoint', observed_at='2026-10-30'),
            council=dict(run_id='round-one', phase='investigate', member_id='member-one', group='Free', chair='member-one'),
            capabilities=dict(self.brief['capabilities'], model='vendor/model:free',
                reasoning={'default_enabled': True}, source='Exact free route documentation: reasoning enabled by default'))

    def test_free_documented_default_reasoning_omits_every_override(self):
        brief = self.default_reasoning_brief()
        for phase in ('investigate', 'critique', 'synthesis'):
            with self.subTest(phase=phase):
                brief['council']['phase'] = phase
                self.api._validate_council_brief(brief)
                config = self.api.configuration(brief)
                model = config['provider']['openrouter']['models'][brief['model']]
                self.assertEqual(set(model['options']), {'maxOutputTokens', 'provider'})
                self.assertEqual(model['options']['maxOutputTokens'], 8000)
                self.assertEqual(model['options']['provider'], {'only': ['provider'], 'allow_fallbacks': False})
                self.assertTrue(model['tool_call'])
                self.assertEqual(brief['effort'], 'default')

    def test_documented_default_rejects_paid_legacy_and_malformed_declarations(self):
        base = self.default_reasoning_brief()
        cases = []
        for group in ('Frontier', 'Balanced'):
            cases.append(dict(base, council=dict(base['council'], group=group)))
        legacy = {key: value for key, value in base.items() if key not in ('canonical_model', 'pricing')}
        legacy['council'] = self.brief['council']; cases.append(legacy)
        for reasoning in ({'default_enabled': False}, {'default_enabled': 1}, {'default_enabled': 'true'},
                          {'default_enabled': True, 'efforts': ['high']}, {'fixed': 'default'}, {'efforts': ['default']}):
            cases.append(dict(base, capabilities=dict(base['capabilities'], reasoning=reasoning)))
        cases.append(dict(base, effort='high'))
        for field in ('source', 'observed_at'):
            cases.append(dict(base, capabilities=dict(base['capabilities'], **{field: ''})))
        for brief in cases:
            with self.subTest(brief=brief), self.assertRaises(ValueError): self.api._validate_council_brief(brief)
        with self.assertRaisesRegex(ValueError, 'council_maximum_effort_required'):
            self.api._validate_council_brief(dict(self.brief, effort='low'))

    def test_council_guidance_is_bounded_without_truncating_approved_paths(self):
        files = ['approved/' + 'source-' * 10 + str(number) + '.txt' for number in range(1000)]
        for phase in ('investigate', 'critique', 'synthesis'):
            brief = self.default_reasoning_brief()
            brief['council']['phase'] = phase
            original = self.api.configuration(brief)
            expanded = self.api.configuration(dict(brief, files=files))
            self.assertEqual(expanded['agent']['pilot']['prompt'], original['agent']['pilot']['prompt'])
            self.assertLessEqual(len(expanded['agent']['pilot']['prompt']), 1500)
            self.assertEqual(expanded['permission']['read'], dict({'*': 'deny'}, **{name: 'allow' for name in files}))
            self.assertNotIn('.council-snapshot.json', expanded['permission']['read'])
            self.assertEqual(expanded['agent']['pilot']['steps'], brief['steps'])


if __name__ == '__main__': unittest.main()
