import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import uuid
import os
import selectors
import signal
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
from runtime import openrouter_settings as settings
from tests.openrouter_fixtures import enroll, configure, fixture_command

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/openrouter_workers.py'


class FakeClock:
    """Runner-local monotonic time that passes only when a test or an idle wait says so."""
    def __init__(self):
        self.now, self.idle = 1000.0, False

    def monotonic(self): return self.now
    def time(self): return time.time()
    def advance(self, seconds): self.now += seconds

    def sleep(self, seconds):
        # Process-retirement polling still waits for the real process group.
        time.sleep(seconds)
        self.advance(seconds)

    def selectors(self):
        clock = self

        class Selector(selectors.DefaultSelector):
            def select(self, timeout=None):
                events = super().select(timeout)
                if not events and clock.idle and timeout: clock.advance(timeout)
                return events
        return SimpleNamespace(DefaultSelector=Selector, EVENT_READ=selectors.EVENT_READ,
                               EVENT_WRITE=selectors.EVENT_WRITE)


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
        self.clock = FakeClock()
        for name, value in (('time', self.clock), ('selectors', self.clock.selectors())):
            clock_patch = patch.object(self.api, name, value)
            clock_patch.start()
            self.addCleanup(clock_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.scope = enroll(self)
        self.source = self.scope.repository_root
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

    def scoped_brief(self):
        from runtime import external_delegation as external
        from tests.test_external_delegation import permission
        consent = permission('openrouter', 'worker', model='vendor/model', provider='provider', effort='low')
        consent['sources'] = ['input.txt']
        disabled = settings.disable(self.source, dict(expected_revision=self.settings['revision']))
        configured = settings.configure(self.source, dict(expected_revision=disabled['revision'], routes=['workers'],
            opencode=str(self.root / 'worker'), auth_data=str(self.root / 'auth'), external_permission=consent))
        self.settings = settings.enable(self.source, dict(expected_revision=configured['revision'], consent_version=2))
        packet = external.prepare(self.source, consent,
            [dict(kind='text', text=self.brief['objective'], sources=['input.txt'])])
        brief = dict(self.brief, external=dict(packet['binding'], automatic=True, local_preferred=False,
            class_name='investigation', coordinator=self.brief['coordinator_chat_id'], reviewer='native-reviewer',
            integration_available=True))
        return brief

    def test_scoped_worker_rechecks_source_and_copied_bytes_before_launch(self):
        brief = self.scoped_brief()
        run = self.api.prepare(self.runs, brief)
        (self.source / 'input.txt').write_text('changed after preparation')
        original = self.api.subprocess.Popen
        launched = []
        def launch(command, **kwargs):
            if command[0] == str(self.root / 'worker'):
                launched.append(command)
                raise AssertionError('worker must not launch')
            return original(command, **kwargs)
        with patch.object(self.api.subprocess, 'Popen', side_effect=launch):
            result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
        self.assertEqual(launched, [])
        self.assertTrue(result['local_execution_stopped'])
        self.assertFalse(result['accounting_complete'])

    def test_scoped_worker_rejects_subroot_but_keeps_legacy_manual_subroot(self):
        from runtime import external_delegation as external
        brief = self.scoped_brief()
        subroot = self.source / 'excluded'
        subroot.mkdir()
        (subroot / 'input.txt').write_text('unapproved subdirectory bytes')
        packet = external.prepare(subroot, self.settings['external_permission'],
            [dict(kind='text', text=brief['objective'], sources=['input.txt'])])
        nested = dict(brief, source_root=str(subroot),
            external=dict(brief['external'], **packet['binding']))
        with self.assertRaisesRegex(ValueError, 'checkout'):
            self.api.prepare(self.runs, nested)
        legacy = {key: value for key, value in nested.items() if key != 'external'}
        run = self.api.prepare(self.runs, legacy)
        self.assertEqual((run / 'workspace/input.txt').read_text(), 'unapproved subdirectory bytes')

    def test_scoped_worker_preserves_supported_80_kib_source_with_short_objective(self):
        (self.source / 'input.txt').write_text('x' * (80 * 1024))
        brief = self.scoped_brief()
        run = self.api.prepare(self.runs, brief)
        self.assertEqual((run / 'workspace/input.txt').stat().st_size, 80 * 1024)
        self.assertEqual(brief['objective'], self.brief['objective'])
        from runtime import external_delegation as external
        with self.assertRaisesRegex(ValueError, 'packet_size'):
            external.prepare(self.source, self.settings['external_permission'],
                [dict(kind='source', path='input.txt')])

    def test_disable_blocks_preparation_and_launch_without_replay(self):
        run = self.api.prepare(self.runs, self.brief)
        settings.disable(self.source, {'expected_revision': self.settings['revision']})
        with self.assertRaisesRegex(settings.SettingsError, 'openrouter_disabled'):
            self.api.prepare(self.runs, dict(self.brief, task_id='blocked'))
        phases = []
        result = self.api.execute(run, self.root / 'worker', self.root / 'auth', phases.append)
        self.assertEqual(result['failure'], {'category': 'integration', 'reason': 'openrouter_disabled'})
        self.assertEqual(phases, [], 'Refused launch must not publish a worker character')
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
        child = subprocess.Popen(fixture_command(self, SCRIPT, 'run', '--root', self.runs,
            '--opencode', exe, '--auth-data', self.root / 'auth'), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # The CLI runs in its own process on the real clock; keep its deadline out of reach.
            child.stdin.write(json.dumps(dict(self.brief, timeout_seconds=300))); child.stdin.close(); child.stdin = None
            deadline = time.monotonic() + 30
            while not started.exists() and child.poll() is None and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(started.exists(), 'worker must launch before disable')
            report = settings.disable(self.source, {'expected_revision': self.settings['revision']})
            self.assertFalse(report['enabled'])
            self.assertIsNone(child.poll(), 'disable must not wait for or stop admitted work')
            release.touch()
            output, error = child.communicate(timeout=30)
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

    def test_custom_role_survives_preparation_without_changing_execution(self):
        try:
            run = self.api.prepare(self.runs, dict(self.brief,
                custom_role_name='Security investigator · 東京'))
        except ValueError as error:
            self.fail(f'Explicit custom role name must be supported: {error}')
        saved = json.loads((run / 'brief.json').read_text())
        self.assertEqual(saved['role'], 'Scout')
        self.assertEqual(saved['specialty'], 'File analysis')
        self.assertEqual(saved['custom_role_name'], 'Security investigator · 東京')
        self.assertEqual(saved['model'], 'vendor/model')
        self.assertEqual(saved['provider'], 'provider')
        self.assertEqual(self.api.configuration(saved), self.api.configuration(self.brief))

    def test_worker_without_role_gets_a_badge_and_can_execute(self):
        from runtime import habblaud, worker_visibility
        from tests.fixture_worker_visibility import fixture_transport
        client, transport = fixture_transport()
        brief = {key: value for key, value in self.brief.items() if key not in {'role', 'specialty'}}
        run = self.api.prepare(self.runs, brief)
        exe = self.fixture("sys.stdin.read()\n"
                           "print(json.dumps({'type':'text','part':{'text':'Fixture answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        with patch.object(habblaud, 'transport', return_value=transport):
            publisher = self.api.habblaud_publisher(self.root, run, 'http://127.0.0.1:1', 'account')
            result = self.api.execute(run, exe, self.root / 'auth', publisher)
        self.assertEqual(result['state'], 'awaiting_verification')
        self.assertEqual(client.events[0]['agent_type'], 'Subagente (Codex)')

    def test_openrouter_reuses_external_display_identity_through_verification(self):
        from runtime import habblaud, worker_visibility
        from tests.fixture_worker_visibility import fixture_transport
        client, transport = fixture_transport()
        run = self.api.prepare(self.runs, dict(self.brief, custom_role_name='Evidence scout'))
        exe = self.fixture("sys.stdin.read()\n"
                           "print(json.dumps({'type':'text','part':{'text':'Fixture answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        with patch.object(habblaud, 'transport', return_value=transport):
            args = (self.root, run, 'http://127.0.0.1:1', 'account')
            publisher = self.api.habblaud_publisher(*args)
            self.assertEqual(client.events, [])
            result = self.api.execute(run, exe, self.root / 'auth', publisher)
            self.assertEqual(result['state'], 'awaiting_verification')
            result = self.api.verify(run, 'accepted', ['Native fixture verification'])
            self.api.habblaud_publisher(*args)(result['state'])
        external = str(uuid.uuid5(uuid.NAMESPACE_URL, str(run)))
        self.assertNotEqual(external, self.brief['coordinator_chat_id'])
        self.assertEqual({event['agent_id'] for event in client.events}, {external})
        self.assertEqual({event['session_id'] for event in client.events}, {self.brief['coordinator_chat_id']})
        self.assertEqual({event['agent_type'] for event in client.events}, {'Scout · Model'})
        self.assertEqual([event['hook_event_name'] for event in client.events].count('SubagentStart'), 1)
        self.assertEqual([event['hook_event_name'] for event in client.events].count('SubagentStop'), 1)

    def test_existing_openrouter_receipt_keeps_its_original_badge(self):
        from runtime import habblaud, worker_visibility
        from tests.fixture_worker_visibility import fixture_transport
        client, transport = fixture_transport()
        run = self.api.prepare(self.runs, dict(self.brief, custom_role_name='Evidence scout'))
        endpoint, account = 'http://127.0.0.1:1', 'account'
        metadata = self.api.visibility_declaration(dict(self.brief, custom_role_name='Evidence scout'))
        external = str(uuid.uuid5(uuid.NAMESPACE_URL, str(run)))
        binding = dict(parent=self.brief['coordinator_chat_id'], agent_id=external,
            label='Evidence scout · Model', cwd=self.brief['source_root'])
        legacy = habblaud.publisher(transport, endpoint, account, binding, metadata, run / 'visibility.json')
        self.assertTrue(legacy('Working')['delivered'])
        with patch.object(habblaud, 'transport', return_value=transport):
            publish = self.api.habblaud_publisher(self.root, run, endpoint, account)
            self.assertTrue(publish('accepted')['delivered'])
        self.assertEqual({event['agent_type'] for event in client.events}, {'Evidence scout · Model'})

    def test_approved_native_fallback_preserves_frozen_name_with_a_real_child_id(self):
        from runtime import habblaud, worker_visibility
        from runtime.adapters import codex
        from scripts import worker_visibility as native_helper
        from tests.fixture_worker_visibility import fixture_transport

        client, transport = fixture_transport()
        run = self.api.prepare(self.runs, dict(self.brief, custom_role_name='Evidence scout'))
        exe = self.fixture("sys.stdin.read()\nprint(json.dumps({'type':'error','error':{}}))\n")
        endpoint, account = 'http://127.0.0.1:1', 'account'
        plugin = self.root / 'transport'
        plugin.mkdir(mode=0o700)
        with patch.object(habblaud, 'transport', return_value=transport):
            result = self.api.execute(run, exe, self.root / 'auth',
                self.api.habblaud_publisher(plugin, run, endpoint, account))
            self.assertTrue(result['native_fallback_ready'])
            self.assertTrue(result['local_execution_stopped'])
            frozen = self.api.visibility_declaration(json.loads((run / 'brief.json').read_text()))
            child = str(uuid.uuid4())
            parent, task_name = self.brief['coordinator_chat_id'], 'scout_fallback'
            context = self.root / 'native-fallback.json'
            native_helper.prepare_native(context, dict(parent_session_id=parent, checkout=str(self.source),
                task_name=task_name, visibility=frozen))
            observed = dict(version=1, parent_session_id=parent, worker_id=child, checkout=str(self.source),
                client_surface='codex-cli', native_version='0.163.0', agent_path='/root/' + task_name,
                task_name=task_name, configured_model='Native model', configured_provider='native-account',
                execution_model=None, execution_effort=None)
            with patch.object(codex, 'observe_worker_visibility', return_value=observed):
                native = native_helper.publish_native(context, child, 'Working', plugin, endpoint, account)
        self.assertTrue(native['delivered'])
        self.assertEqual(native['visibility'], dict(version=1, role='Scout', specialty='File analysis',
                                                   custom_role_name='Evidence scout'))
        external = str(uuid.uuid5(uuid.NAMESPACE_URL, str(run)))
        self.assertNotEqual(child, external)
        starts = {event['agent_id']: event['agent_type'] for event in client.events if event['hook_event_name'] == 'SubagentStart'}
        self.assertEqual(starts, {external: 'Scout · Model', child: 'Evidence scout · Native model'})

    def test_slow_visibility_does_not_consume_worker_deadline(self):
        run = self.api.prepare(self.runs, dict(self.brief, timeout_seconds=1))
        exe = self.fixture("sys.stdin.read()\n"
                           "print(json.dumps({'type':'text','part':{'text':'Fixture answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        finished, progress = threading.Event(), self.api.progress
        def observed(path, phase):
            progress(path, phase)
            if phase in ('Awaiting verification', 'failed', 'unresolved'): finished.set()
        def slow(phase):
            # Delivery outlasts the run; on the worker's thread it would spend 1.2 s of its 1 s deadline.
            if not finished.wait(30): self.clock.advance(1.2)
        with patch.object(self.api, 'progress', observed):
            result = self.api.execute(run, exe, self.root / 'auth', slow)
        self.assertEqual(result['state'], 'awaiting_verification')
        self.assertTrue(result['local_execution_stopped'])

    def test_verify_returns_saved_acceptance_even_if_visibility_decoder_fails(self):
        from contextlib import redirect_stdout
        import io
        from types import SimpleNamespace

        run = self.api.prepare(self.runs, self.brief)
        exe = self.fixture("sys.stdin.read()\n"
                           "print(json.dumps({'type':'text','part':{'text':'Fixture answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        self.assertEqual(self.api.execute(run, exe, self.root / 'auth')['state'], 'awaiting_verification')
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(dict(task_id='pilot-1',
            disposition='accepted', checks=['Native fixture verification'])).encode()))
        output = io.StringIO()
        args = [str(SCRIPT), 'verify', '--root', str(self.runs), '--visibility-plugin', str(self.root)]

        def broken(phase):
            raise RecursionError('Malformed snapshot')

        with patch.object(self.api, 'habblaud_publisher', return_value=broken), patch.object(sys, 'stdin', stdin), \
                patch.object(sys, 'argv', args), redirect_stdout(output):
            try:
                self.api.main()
            except RecursionError:
                self.fail('Optional visibility must not break the verification response')
        returned = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(returned['state'], 'accepted')
        self.assertEqual(returned, json.loads((run / 'result.json').read_text()))

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
        permissions = config['permission']
        self.assertEqual(permissions['*'], 'deny')
        self.assertEqual(permissions['read']['*'], 'deny')
        self.assertEqual(permissions['read']['input.txt'], 'allow')
        self.assertNotIn('/absolute', permissions['read'])
        for tool in ['bash', 'edit', 'task', 'webfetch', 'external_directory']:
            self.assertNotEqual(permissions.get(tool), 'allow')
        self.assertFalse(config['provider']['openrouter']['models']['vendor/model']['options']['provider']['allow_fallbacks'])

    def test_worker_reuses_qualified_catalog_without_a_background_refresh(self):
        exe = self.fixture("sys.stdin.read()\nimport os\nassert os.environ.get('OPENCODE_DISABLE_MODELS_FETCH') == 'true'\n"
                           "print(json.dumps({'type':'text','part':{'text':'Fixture answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, self.brief)
        self.assertEqual(self.api.execute(run, exe, self.root / 'auth')['state'], 'awaiting_verification')

    def test_missing_visibility_adapter_does_not_prevent_worker_execution(self):
        import subprocess
        brief = dict(self.brief, timeout_seconds=300)
        exe = self.fixture("sys.stdin.read()\n"
                           "print(json.dumps({'type':'error','error':{'message':'provider unavailable'}}))\n")
        result = subprocess.run(fixture_command(self, SCRIPT, 'run', '--root', self.runs,
                                 '--opencode', exe, '--auth-data', self.root/'auth',
                                 '--visibility-plugin', self.root/'missing'), input=json.dumps(brief),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.runs/'pilot-1/result.json').read_text())['state'], 'failed')
        self.assertFalse(json.loads((self.runs/'pilot-1/visibility.json').read_text())['delivered'])

    def fixture(self, body):
        p = self.root / 'worker'
        p.write_text('#!/usr/bin/env python3\nimport json, sys, time\n' + body)
        p.chmod(0o700)
        return p

    def sized_brief(self, count=17):
        spec = importlib.util.spec_from_file_location('fixture_catalog', SCRIPT.with_name('openrouter_proposals.py'))
        catalog = importlib.util.module_from_spec(spec); spec.loader.exec_module(catalog)
        store = Path(self.settings['proposal_store'])
        metadata = dict(models=[dict(id='vendor/model', context_length=64000, max_output_tokens=16000,
            supported_reasoning_efforts=['low'])], endpoints=[dict(model='vendor/model', provider='provider',
            context_length=64000, max_output_tokens=16000, modalities=['text'],
            supported_parameters=['tools', 'reasoning'])], benchmarks=[], task_usage=[], coverage={'source':'fixture'})
        token = catalog.run('claim', store, {'force':True})['refresh_token']
        status = catalog.run('publish', store, dict(refresh_token=token, metadata=metadata))
        files = [f'input-{n}.txt' for n in range(count)]
        for name in files: (self.source / name).write_text('evidence\n')
        sizing = dict(profile=dict(family='analysis', language='python', framework=None,
            difficulty='small', scope='files', verification='native inspection'),
            snapshot_digest=status['snapshot_digest'], requirements=dict(context_tokens=1000,
            output_tokens=8192, modalities=['text'], parameters=['tools']), max_files=32, max_input_bytes=4096)
        return dict(self.brief, files=files, sizing=sizing), catalog, store

    def test_sized_preparation_and_child_receive_larger_finite_budget(self):
        brief, _, _ = self.sized_brief()
        exe = self.fixture("sys.stdin.read()\nimport os\nconfig=json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])\n"
            "assert config['provider']['openrouter']['models']['vendor/model']['options']['maxOutputTokens']==8192\n"
            "assert config['permission']['*']=='deny'\n"
            "print(json.dumps({'type':'text','part':{'text':'sized'}}))\n"
            "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        try: run = self.api.prepare(self.runs, brief)
        except ValueError as error: self.fail(f'Compatible sized preparation rejected: {error}')
        self.assertEqual(len(list((run/'workspace').glob('input-*.txt'))), 17)
        self.assertEqual(self.api.execute(run, exe, self.root/'auth')['state'], 'awaiting_verification')

    def test_sizing_rejects_incompatible_or_unbounded_inputs_before_run(self):
        import copy
        brief, _, _ = self.sized_brief()
        for key, value in [('max_files', True), ('max_input_bytes', 2**60),
                           ('max_input_bytes', 1), ('snapshot_digest', 'wrong')]:
            request=copy.deepcopy(brief);request['sizing'][key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError): self.api.prepare(self.runs,request)
            self.assertFalse((self.runs/'pilot-1').exists())
        brief['sizing']['requirements']['output_tokens']=64000
        with self.assertRaises(ValueError): self.api.prepare(self.runs,brief)
        self.assertFalse((self.runs/'pilot-1').exists())

    def test_sized_launch_rejects_tampering_and_stale_catalog(self):
        for mode in ('tamper','stale'):
            brief,catalog,store=self.sized_brief(1);brief['task_id']=mode
            run=self.api.prepare(self.runs,brief)
            if mode=='tamper':
                data=json.loads((run/'brief.json').read_text());data['sizing']['requirements']['output_tokens']=12000
                (run/'brief.json').write_text(json.dumps(data))
            else:
                token=catalog.run('claim',store,{'force':True})['refresh_token']
                catalog.run('publish',store,dict(refresh_token=token,failure='fixture unavailable'))
            result=self.api.execute(run,self.root/'worker',self.root/'auth')
            self.assertEqual(result['state'],'failed')
            self.assertIs(result['native_fallback_ready'],True)

    def test_sized_input_manifest_and_growth_are_bounded(self):
        brief,_,_=self.sized_brief(1)
        brief['files'] *= 2
        with self.assertRaises(ValueError): self.api.prepare(self.runs,brief)
        brief['files'] = ['input-0.txt']
        (self.source/'input-0.txt').write_text('')
        brief['sizing']['max_input_bytes']=1
        with self.assertRaises(ValueError): self.api.prepare(self.runs,brief)
        brief['sizing']['max_input_bytes']=4096
        read=self.api.onboarding._read_file
        def growing(path,*args,**kwargs):
            if Path(path)==self.source/'input-0.txt': Path(path).write_bytes(b'x'*(128*1024+1))
            return read(path,*args,**kwargs)
        with patch.object(self.api.onboarding,'_read_file',side_effect=growing):
            with self.assertRaises(ValueError): self.api.prepare(self.runs,brief)
        self.assertFalse((self.runs/'pilot-1').exists())

    def test_sizing_requires_text_before_preparation_and_launch(self):
        for absent in (True, False):
            brief,catalog,store=self.sized_brief(1)
            brief['task_id']='missing-text' if absent else 'empty-text'
            metadata=catalog.run('status',store,{'include_metadata':True})['metadata']
            if absent: metadata['endpoints'][0].pop('modalities')
            else: metadata['endpoints'][0]['modalities']=[]
            token=catalog.run('claim',store,{'force':True})['refresh_token']
            snapshot=catalog.run('publish',store,dict(refresh_token=token,metadata=metadata))
            brief['sizing']['snapshot_digest']=snapshot['snapshot_digest']
            brief['sizing']['requirements']['modalities']=[]
            with self.subTest(absent=absent), self.assertRaises(ValueError): self.api.prepare(self.runs,brief)
            self.assertFalse((self.runs/brief['task_id']).exists())
        brief,_,_=self.sized_brief(1)
        run=self.api.prepare(self.runs,brief)
        data=json.loads((run/'brief.json').read_text())
        data['sizing']['requirements']['modalities']=[]
        data['sizing_digest']=self.api.sizing_digest(data)
        (run/'brief.json').write_text(json.dumps(data))
        marker=self.root/'launched'
        exe=self.fixture(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        result=self.api.execute(run,exe,self.root/'auth')
        self.assertEqual(result['state'],'failed')
        self.assertIs(result['native_fallback_ready'],True)
        self.assertFalse(marker.exists())

    def test_sized_preparation_has_a_deadline(self):
        brief,_,_=self.sized_brief(1);brief['timeout_seconds']=1
        read=self.api.onboarding._read_file
        def slow(path,*args,**kwargs):
            if Path(path)==self.source/'input-0.txt': self.clock.advance(1.1)
            return read(path,*args,**kwargs)
        with patch.object(self.api.onboarding,'_read_file',side_effect=slow):
            with self.assertRaises(TimeoutError): self.api.prepare(self.runs,brief)
        self.assertFalse((self.runs/'pilot-1').exists())

    def test_legacy_limits_and_cli_input_bound_remain(self):
        import subprocess
        self.assertEqual(self.api.configuration(self.brief)['provider']['openrouter']['models']['vendor/model']['options']['maxOutputTokens'],2048)
        with self.assertRaises(ValueError): self.api.prepare(self.runs,dict(self.brief,files=['input.txt']*17))
        result=subprocess.run([sys.executable,'-I','-B',str(SCRIPT),'status','--root',str(self.runs)],
            input=' '*(1024*1024+1),text=True,capture_output=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.runs/'pilot-1').exists())


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

    def test_error_even_with_zero_exit_never_becomes_success(self):
        exe = self.fixture("sys.stdin.read()\nprint(json.dumps({'type':'error','error':{'message':'SECRET'}}))\n")
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
                         'data': {'message': 'SECRET'}}), {'category': 'context'})
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
        # Consume the objective so the runner's write cannot race the worker's exit.
        exe = self.fixture("sys.stdin.read()\nwith open(%r, 'a') as stream: stream.write('launch\\n')\n" % str(launches) +
                           "print(json.dumps({'type':'error','error':%r}), flush=True)\n" % error)
        run = self.api.prepare(self.runs, self.brief)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(launches.read_text().splitlines(), ['launch'])
        self.assertEqual(result.get('failure'), {'category': 'service', 'status_code': 503})
        self.assertIs(result.get('local_execution_stopped'), True)
        self.assertIs(result.get('native_fallback_ready'), True)
        self.assertEqual(result['state'], 'failed')
        self.assertFalse(result['upstream_cancellation_confirmed'])
        self.assertFalse(result['accounting_complete'])
        self.assertIsNone(result['reported_cost_usd'])
        self.assertNotIn('SECRET', (run / 'result.json').read_text())
        self.assertNotIn('SECRET', (run / 'progress.jsonl').read_text())

    def test_partial_usage_survives_terminal_error_without_becoming_a_complete_bill(self):
        exe = self.fixture("""sys.stdin.read()
for event in [
 {'type':'text','part':{'text':'Partial finding'}},
 {'type':'step_finish','part':{'reason':'tool-calls','cost':0.02,'tokens':{'input':20}}},
 {'type':'error','error':{'name':'APIError','data':{'statusCode':402}}}
]: print(json.dumps(event), flush=True)
""")
        run = self.api.prepare(self.runs, self.brief)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result.get('failure'), {'category': 'budget', 'status_code': 402})
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
        progress = self.api.progress
        def observed(path, phase):
            progress(path, phase)
            if phase == 'Generating': self.clock.advance(.3)
        started = self.clock.monotonic()
        with patch.object(self.api, 'progress', observed):
            result = self.api.execute(run, exe, self.root / 'auth')
        self.assertLess(self.clock.monotonic() - started, 5)
        events = [json.loads(line)['phase'] for line in (run / 'progress.jsonl').read_text().splitlines()]
        self.assertLess(events.count('Generating'), 100, 'deadline must stop the worker before its own end')
        self.assertEqual(launches.read_text().splitlines(), ['launch'])
        self.assertEqual(result.get('failure'), {'category': 'deadline'})
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
        self.clock.idle = True
        started = self.clock.monotonic()
        result, processes = self.execute_with_backpressure(run, exe)
        self.assertLess(self.clock.monotonic() - started, 2.5)
        self.assertEqual(result.get('failure'), {'category': 'deadline'})
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertLess(processes[0].returncode, 0, 'deadline must stop the worker before its own end')
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
        started = self.clock.monotonic()
        result, processes = self.execute_with_backpressure(run, exe)
        self.assertLess(self.clock.monotonic() - started, 4)
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
            if command[0] == str(exe): processes.append(process); self.clock.advance(1.2)
            return process
        with patch.object(self.api.subprocess, 'Popen', side_effect=delayed):
            result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result['failure'], {'category': 'deadline'})
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
                self.clock.advance(1.2)
                yield report
        def observed(command, **kwargs):
            process = launch(command, **kwargs)
            if command[0] == str(self.root / 'worker'): processes.append(process)
            return process
        with patch.object(self.api.settings, 'admission', slow), patch.object(self.api.subprocess, 'Popen', observed):
            result = self.api.execute(run, self.root / 'worker', self.root / 'auth')
        self.assertEqual(processes, [])
        self.assertEqual(result['failure'], {'category': 'deadline'})
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
        self.assertEqual(result.get('failure'), {'category': 'runtime_or_output'})
        self.assertIs(result.get('native_fallback_ready'), True)

    def test_cli_interruption_retires_the_worker_before_native_takeover(self):
        import subprocess
        pidfile = self.root / 'worker.pid'
        exe = self.fixture("import os\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'tool-calls','cost':0.02,'tokens':{'input':20}}}), flush=True)\n"
                           "with open(%r, 'w') as stream: stream.write(str(os.getpid()))\ntime.sleep(20)\n" % str(pidfile))
        process = subprocess.Popen(fixture_command(self, SCRIPT, 'run', '--root', self.runs,
                                    '--opencode', exe, '--auth-data', self.root / 'auth'),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # The CLI runs in its own process on the real clock; only the interruption may stop it.
            process.stdin.write(json.dumps(dict(self.brief, timeout_seconds=300)))
            process.stdin.close()
            process.stdin = None
            until = time.monotonic() + 30
            while not pidfile.exists() and time.monotonic() < until: time.sleep(.01)
            self.assertTrue(pidfile.exists(), 'fixture worker must start before interruption')
            result_path = self.runs / 'pilot-1/result.json'
            while time.monotonic() < until:
                if result_path.exists() and json.loads(result_path.read_text()).get('known_cost_subtotal_usd') == 0.02:
                    break
                time.sleep(.01)
            self.assertEqual(json.loads(result_path.read_text()).get('known_cost_subtotal_usd'), 0.02)
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, error)
            result = json.loads(output.splitlines()[-1])
            self.assertEqual(result['state'], 'unresolved')
            self.assertEqual(result.get('failure'), {'category': 'interrupted'})
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
        exe = self.fixture("sys.stdin.read()\nprint(json.dumps({'type':'text','part':{'text':'Wrong answer'}}))\n"
                           "print(json.dumps({'type':'step_finish','part':{'reason':'stop','cost':0,'tokens':{}}}))\n")
        run = self.api.prepare(self.runs, self.brief)
        result = self.api.execute(run, exe, self.root / 'auth')
        self.assertEqual(result['state'], 'awaiting_verification')
        self.assertIs(result.get('native_fallback_ready'), False)
        rejected = self.api.verify(run, 'rejected', ['Native inspection found the answer incorrect'])
        self.assertIs(rejected.get('native_fallback_ready'), True)

    def test_timeout_and_missing_finish_remain_unresolved(self):
        self.clock.idle = True
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
        exe = self.fixture("""sys.stdin.read()
for part in [{'reason':'tool-calls','cost':0.1}, {'reason':'stop'}]:
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
            # Wait only for reaping; a surviving descendant would live for 20 s.
            until = time.monotonic() + 5
            while time.monotonic() < until:
                try: os.kill(pid, 0)
                except ProcessLookupError: break
                time.sleep(.01)
            with self.assertRaises(ProcessLookupError): os.kill(pid, 0)
        finally:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass


if __name__ == '__main__': unittest.main()
