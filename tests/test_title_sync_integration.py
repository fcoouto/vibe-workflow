"""Durable title presentation against a disposable native-client boundary."""
from tests.fixture_canonical_birth import test_acquisition
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime import title_sync
from runtime.startup import ProjectScope, RuntimeSettings


class NativeFixture:
    def __init__(self):
        self.title = 'Original title'
        self.writes = 0
        self.lose_ack = False

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, session): return self.title
    def write(self, session, title):
        self.title = title
        self.writes += 1
        if self.lose_ack:
            self.lose_ack = False
            raise OSError('Lost write acknowledgment')


class NativeTitleTransportTests(unittest.TestCase):
    def test_protocol_failure_uses_control_error_boundary_and_closes_process(self):
        from runtime import doctor
        initialized = dict(codexHome='/fixture', platformFamily='unix', platformOs='macos', userAgent='fixture')
        for failing in ('initialize', 'thread/read', 'malformed-initialize'):
            class Process:
                closed = False

                def request(self, identity, method, params):
                    if method == failing:
                        raise doctor.ObservationError('private native detail')
                    return {} if failing == 'malformed-initialize' else initialized

                def send(self, message):
                    pass

                def close(self):
                    self.closed = True

            process = Process()
            with self.subTest(failing=failing):
                with patch('shutil.which', return_value='/fixture/codex'), \
                        patch.object(doctor, 'NativeProcess', return_value=process), self.assertRaises(OSError) as error:
                    with title_sync.NativeTitles() as native:
                        native.read('session')
                self.assertTrue(process.closed)
                self.assertNotIn('private native detail', str(error.exception))


class TitleIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        from runtime import state
        fixture, self.acquisition, _ = fresh_canonical(self)
        self.home, self.scope = fixture.base, self.acquisition.resolution.scope
        identity=dict(client='codex',session_id='session',task_id='task',scope_ref='user:task')
        task=state.start_task(self.scope,identity|dict(mode='editable',decision_ref='user:accepted'))['task']
        self.task=state.update_task(self.scope,identity|dict(expected_revision=task['revision'],state='WORKING',
            reason='Title fixture work',reference='user:accepted'))['task']
        authority = patch.object(title_sync, '_task', side_effect=lambda *args, **kwargs: deepcopy(self.task))
        authority.start()
        self.addCleanup(authority.stop)
        home = patch.object(Path, 'home', return_value=self.home)
        home.start()
        self.addCleanup(home.stop)
        self.native = NativeFixture()

    def call(self, action='sync', **updates):
        request = dict(client='codex', session_id='session')
        if action in {'configure','resume'}:
            request.update(task_id='task', scope_ref='user:task', expected_revision=self.task['revision'], reference='user:approved')
        return title_sync.operate(self.scope, action, request | updates, transport=lambda: self.native)

    def test_explicit_adoption_then_same_revision_does_not_rewrite(self):
        self.call('configure', topic='Thread naming')
        self.assertEqual(self.native.title, '▱▱▱▱▱ [W1] P · Thread naming · WORKING')
        self.call()
        self.assertEqual(self.native.writes, 1)
        self.task.update(state='REVIEW', revision=self.task['revision']+1)
        self.call()
        self.assertTrue(self.native.title.endswith(' · REVIEW'))

    def test_oversized_member_cannot_create_unreadable_presentation(self):
        before = self.native.title
        with self.assertRaises(ValueError):
            self.call('configure', topic='Title', parent_session_id='parent', member='C' + '1' * 5000)
        self.assertEqual(self.native.title, before)
        self.assertEqual(self.native.writes, 0)
        self.assertFalse(title_sync._path(self.scope, 'codex', 'session').exists())

    def test_exact_current_limit_reserves_space_for_future_statuses(self):
        self.task['state'] = 'TRIAGE'
        with self.assertRaises(ValueError):
            self.call('configure', topic='Title', parent_session_id='parent', member='C' + '1' * 4067)
        self.assertEqual(self.native.title, 'Original title')
        self.assertEqual(self.native.writes, 0)
        self.assertFalse(title_sync._path(self.scope, 'codex', 'session').exists())

    def test_longest_safe_member_survives_status_transitions_and_resume(self):
        self.task['state'] = 'TRIAGE'
        self.call('configure', topic='Title', parent_session_id='parent', member='C' + '1' * 4064)
        self.assertEqual(len(self.native.title), 4093)
        for status in ('READY', 'WORKING', 'REVIEW', 'WAITING', 'BLOCKED', 'PAUSED', 'DONE', 'CANCELLED'):
            self.task.update(state=status, revision=self.task['revision'] + 1)
            self.call()
            self.assertTrue(self.native.title.endswith(' · ' + status))
            self.assertTrue(self.call('status')['managed'])
        self.assertEqual(len(self.native.title), 4096)
        self.assertFalse(self.call('resume')['paused'])

    def test_oversized_manual_title_is_preserved_and_pauses_once(self):
        self.call('configure', topic='Title')
        self.native.title = 'x' * 5000
        self.task['revision'] += 1
        result = self.call()
        self.assertTrue(result['paused'])
        self.assertIsNotNone(result['notification'])
        self.assertIsNone(self.call()['notification'])
        self.assertEqual(self.native.title, 'x' * 5000)
        self.assertEqual(self.native.writes, 1)

    def test_oversized_pending_metadata_is_refused_before_native_write(self):
        self.task['scope_ref'] = '💡' * 1024
        self.native.title = '💡' * 4096
        before = self.native.title
        with self.assertRaises(ValueError):
            self.call('configure', topic='💡' * 160, scope_ref=self.task['scope_ref'])
        self.assertEqual(self.native.title, before)
        self.assertEqual(self.native.writes, 0)
        self.assertFalse(title_sync._path(self.scope, 'codex', 'session').exists())

    def test_corrupt_family_register_preserves_bytes_and_uses_control_error_boundary(self):
        store = self.home / '.vibe-coding-workflow' / 'workstreams-families.sqlite3'
        store.parent.mkdir(mode=0o700)
        store.write_bytes(b'corrupt family register')
        before = self.native.title
        with self.assertRaises(OSError):
            self.call('configure', topic='Title')
        self.assertEqual(store.read_bytes(), b'corrupt family register')
        self.assertEqual(self.native.title, before)
        self.assertEqual(self.native.writes, 0)
        self.assertFalse(title_sync._path(self.scope, 'codex', 'session').exists())

    def test_topic_survives_next_status_update_and_persistence(self):
        self.call('configure', topic='Thread naming')
        self.native.title = self.native.title.replace('Thread naming','Better naming')
        self.task.update(state='REVIEW', revision=self.task['revision']+1)
        self.call()
        self.assertIn(' · Better naming · REVIEW', self.native.title)
        self.task.update(state='WORKING', revision=self.task['revision']+1)
        self.call()
        self.assertIn(' · Better naming · WORKING', self.native.title)

    def test_manual_done_does_not_complete_task_and_notice_only_once(self):
        self.call('configure', topic='Thread naming')
        manual = self.native.title.replace('WORKING','DONE')
        self.native.title = manual
        self.task.update(revision=self.task['revision']+1)
        self.assertIsNotNone(self.call()['notification'])
        self.task.update(revision=self.task['revision']+1)
        self.assertIsNone(self.call()['notification'])
        self.assertEqual(self.native.title, manual)
        self.assertEqual(self.task['state'], 'WORKING')
        self.call('resume')
        self.assertTrue(self.native.title.endswith(' · WORKING'))

    def test_failed_acknowledgment_repairs_without_duplicate_write(self):
        self.call('configure', topic='Thread naming')
        self.task.update(state='REVIEW', revision=self.task['revision']+1)
        self.native.lose_ack = True
        with self.assertRaises(OSError): self.call()
        self.call()
        self.assertEqual(self.native.writes, 2)
        self.assertFalse(self.call('status')['presentation']['paused'])

    def test_stale_revision_cannot_adopt_or_rename(self):
        with self.assertRaises(ValueError):
            self.call('configure', topic='Thread naming', expected_revision=0)
        self.assertEqual(self.native.writes, 0)

    def test_done_meter_uses_authoritative_status_only(self):
        self.call('configure', topic='Thread naming')
        self.task.update(state='DONE', revision=self.task['revision']+1)
        self.call()
        self.assertTrue(self.native.title.startswith('▰▰▰▰▰ '))

    def hook(self, kind, result):
        import io
        import json
        from types import SimpleNamespace
        from scripts import workflow
        payload = dict(hook_event_name=kind, session_id='session', cwd=str(self.scope.repository_root),
            turn_id='turn', tool_name='exec_command', tool_use_id='call', stop_hook_active=False)
        output = io.StringIO()
        with patch('sys.argv', ['workflow.py', 'codex', kind]), \
             patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))), \
             patch('sys.stdout', output), \
             patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.scope)), \
             patch('runtime.state.process_event', return_value=result), \
             patch.object(title_sync, 'NativeTitles', return_value=self.native):
            self.assertEqual(workflow.main(), 0)
        return json.loads(output.getvalue()) if output.getvalue().strip() else None

    def test_post_tool_hook_updates_title_preserving_existing_output(self):
        from runtime.engine import HookResult
        self.call('configure', topic='Thread naming')
        self.task.update(state='REVIEW', revision=self.task['revision']+1)
        output = self.hook('PostToolUse', HookResult('PostToolUse', 'context', 'Existing context'))
        self.assertTrue(self.native.title.endswith(' · REVIEW'))
        self.assertEqual(output['hookSpecificOutput']['additionalContext'], 'Existing context')

    def test_stop_hook_preserves_continuation_and_reports_manual_override_once(self):
        from runtime.engine import HookResult
        self.call('configure', topic='Thread naming')
        self.native.title = 'My manually chosen title'
        self.task.update(revision=self.task['revision']+1)
        output = self.hook('Stop', HookResult('Stop', 'continue', 'Existing continuation'))
        self.assertEqual(output['decision'], 'block')
        self.assertEqual(output['reason'], 'Existing continuation')
        self.assertIn('Automatic title updates are paused', output['systemMessage'])
        self.assertEqual(self.native.title, 'My manually chosen title')

    def test_transport_failure_preserves_hook_result(self):
        from runtime.engine import HookResult
        self.call('configure', topic='Thread naming')
        self.task.update(state='REVIEW', revision=self.task['revision']+1)
        with patch.object(self.native, 'read', side_effect=OSError('Unavailable')):
            output = self.hook('PostToolUse', HookResult('PostToolUse', 'context', 'Existing context'))
        self.assertEqual(output['hookSpecificOutput']['additionalContext'], 'Existing context')


    def test_resume_hook_delivers_manual_override_notice(self):
        import io
        import json
        from types import SimpleNamespace
        from scripts import workflow
        self.call('configure', topic='Thread naming')
        self.native.title = 'My title'
        payload = dict(hook_event_name='SessionStart', source='resume', session_id='session', cwd=str(self.scope.repository_root))
        output = io.StringIO()
        with patch('sys.argv', ['workflow.py','codex','SessionStart']), \
             patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))), \
             patch('sys.stdout', output), \
             patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.scope)), \
             patch('runtime.startup.orient'), \
             patch('runtime.adapters.codex.render', return_value={'systemMessage':'Existing startup'}), \
             patch.object(title_sync, 'NativeTitles', return_value=self.native):
            self.assertEqual(workflow.main(), 0)
        self.assertIn('Automatic title updates are paused', json.loads(output.getvalue())['systemMessage'])


    def test_resume_race_announces_pause_at_unchanged_revision(self):
        self.call('configure', topic='Thread naming')
        self.native.title = 'Manual title'
        with patch.object(self.native, 'write', side_effect=lambda *args: setattr(self.native, 'title', 'New manual choice during resume')):
            result = self.call('resume')
        self.assertTrue(result['paused'])
        self.assertIsNotNone(result['notification'])
        for _ in range(2):
            self.assertIsNone(self.call()['notification'])
        self.assertEqual(self.native.title, 'New manual choice during resume')


class BoundTaskTitleTests(unittest.TestCase):
    def setUp(self):
        from tests import test_state
        self.fixture = test_state.StateTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.start()
        self.native = NativeFixture()
        home = patch.object(Path, 'home', return_value=self.fixture.base)
        home.start()
        self.addCleanup(home.stop)

    def command(self, arguments, request):
        import io
        import json
        from types import SimpleNamespace
        from scripts import workflow
        output = io.StringIO()
        with patch('sys.argv', ['workflow.py', *arguments, '--cwd', str(self.fixture.scope.repository_root)]), \
             patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
             patch('sys.stdout', output), \
             patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.fixture.scope)), \
             patch.object(title_sync, 'NativeTitles', return_value=self.native):
            code = workflow.main()
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0, result)
        return result

    def configure(self):
        task = self.fixture.status()['task']
        return self.command(['task', 'title', 'configure'], {
            key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')} |
            dict(expected_revision=task['revision'], reference='user:adopt', topic='Real title'))

    def test_malformed_native_data_and_constructor_failure_return_json(self):
        import io
        import json
        from types import SimpleNamespace
        from runtime import doctor
        from scripts import workflow
        task = self.fixture.status()['task']
        request = {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')} | dict(
            expected_revision=task['revision'], reference='user:adopt', topic='Real title')
        initialized = dict(codexHome='/fixture', platformFamily='unix', platformOs='macos', userAgent='fixture')
        database = self.fixture.scope.runtime.state_dir / 'runtime.sqlite3'
        before = database.read_bytes()
        for response in ({'thread': None}, {'thread': []}, None, 'constructor-timeout'):
            class Process:
                closed = False

                def request(self, identity, method, params):
                    return initialized if method == 'initialize' else response

                def send(self, message):
                    pass

                def close(self):
                    self.closed = True

            process = Process()
            def factory(*args):
                if response == 'constructor-timeout':
                    raise doctor.ObservationError('private native detail')
                return process
            output = io.StringIO()
            with self.subTest(response=response), patch('sys.argv', ['workflow.py', 'task', 'title', 'configure', '--cwd', str(self.fixture.scope.repository_root)]), \
                    patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
                    patch('sys.stdout', output), patch('runtime.startup.acquire_project_entry',side_effect=lambda *args,**kwargs:test_acquisition(self.fixture.scope)), \
                    patch('shutil.which', return_value='/fixture/codex'), patch.object(doctor, 'NativeProcess', side_effect=factory):
                self.assertEqual(workflow.main(), 1)
                self.assertFalse(json.loads(output.getvalue())['ok'])
                self.assertNotIn('private native detail', output.getvalue())
            if response != 'constructor-timeout':
                self.assertTrue(process.closed)
            self.assertEqual(database.read_bytes(), before)
            self.assertFalse(title_sync._path(self.fixture.scope, 'codex', 'session').exists())

    def test_real_bound_task_commands_update_same_authoritative_status(self):
        self.configure()
        self.assertTrue(self.native.title.endswith(' · TRIAGE'))
        task = self.fixture.status()['task']
        self.command(['task', 'update'], {key: task[key] for key in ('client','session_id','task_id','scope_ref')} |
            dict(expected_revision=task['revision'], state='WORKING', reason='Design approved', reference='user:approve'))
        self.assertTrue(self.native.title.endswith(' · WORKING'))
        self.assertEqual(self.fixture.status()['task']['state'], 'WORKING')

    def test_real_milestone_acceptance_updates_meter_on_next_command(self):
        self.configure()
        task = self.fixture.status()['task']
        self.command(['task','update'], {key: task[key] for key in ('client','session_id','task_id','scope_ref')} |
            dict(expected_revision=task['revision'], state='WORKING', reason='Accepted design', reference='user:approve',
                 milestones=[dict(id='design',weight=2,accepted=True,reference='user:approve'),
                             dict(id='implementation',weight=3,accepted=False,reference='plan:next')]))
        self.assertTrue(self.native.title.startswith('▰▰▱▱▱ '))

    def test_explicit_new_task_adoption_rebinds_presentation_preserving_history(self):
        self.configure()
        old = self.fixture.status()['task']
        self.fixture.start(task_id='next-task', replaces=dict(task_id=old['task_id'], expected_revision=old['revision']))
        self.configure()
        self.assertTrue(self.native.title.endswith(' · TRIAGE'))
        histories = list(self.fixture.scope.runtime.state_dir.glob('title-history-*.json'))
        self.assertEqual(len(histories), 1)


    def test_rebind_preserves_manual_edits_before_any_hook_detected_them(self):
        self.configure()
        old = self.fixture.status()['task']
        self.native.title = 'My manually chosen title'
        self.fixture.start(task_id='next-task', replaces=dict(task_id=old['task_id'], expected_revision=old['revision']))
        self.configure()
        self.assertEqual(self.native.title, 'My manually chosen title')
        report = title_sync.operate(self.fixture.scope, 'status', dict(client='codex',session_id='session'))
        self.assertTrue(report['presentation']['paused'])


class CompletedTaskTitleTests(unittest.TestCase):
    def test_changed_candidate_revalidates_done_before_title_fast_path(self):
        from tests import test_evidence
        fixture = test_evidence.EvidenceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plan()
        fixture.checked()
        fixture.review()
        fixture.verify()
        from runtime import state
        task = state.task_status(fixture.scope, 'codex', 'parent')['task']
        self.assertEqual(task['state'], 'DONE')
        native = NativeFixture()
        request = {key: task[key] for key in ('client','session_id','task_id','scope_ref')} | dict(
            expected_revision=task['revision'], reference='user:adopt', topic='Completed task')
        with patch.object(Path, 'home', return_value=fixture.root):
            title_sync.operate(fixture.scope, 'configure', request, transport=lambda: native)
        self.assertTrue(native.title.endswith(' · DONE'))
        (fixture.repo / 'app.py').write_text('print("Changed after completion")\n')
        title_sync.operate(fixture.scope, 'sync', dict(client='codex',session_id='parent'), transport=lambda: native)
        self.assertTrue(native.title.endswith(' · REVIEW'))
        self.assertFalse(native.title.startswith('▰▰▰▰▰ '))

    def test_completion_verification_cannot_resolve_wait_states(self):
        from tests import test_evidence
        from runtime import state
        fixture = test_evidence.EvidenceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plan()
        fixture.checked()
        fixture.review()
        for status in ('WAITING', 'BLOCKED', 'PAUSED'):
            task = state.task_status(fixture.scope, 'codex', 'parent')['task']
            state.update_task(fixture.scope, fixture.request(state=status, reason='Waiting for user', reference='user:wait'))
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, '[Rr]esol'):
                fixture.api.verify_completion(fixture.scope, fixture.request(purpose='completion'))
            self.assertEqual(state.task_status(fixture.scope, 'codex', 'parent')['task']['state'], status)
            state.update_task(fixture.scope, fixture.request(state='WORKING', reason='User resumed', reference='user:resume', resolution_ref='user:resume'))
