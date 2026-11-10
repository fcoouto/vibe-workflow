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
        self.written_sessions = []
        self.lose_ack = False

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, session): return self.title
    def write(self, session, title):
        self.title = title
        self.writes += 1
        self.written_sessions.append(session)
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
        self.assertEqual(self.native.title, '\uFFFB▰▱▱▱▱ Thread naming')
        self.call()
        self.assertEqual(self.native.writes, 1)
        self.task.update(state='REVIEW', user_attention=True, revision=self.task['revision']+1)
        self.call()
        self.assertIn(' REVIEW · ', self.native.title)

    def test_unchanged_sync_reads_saved_status_without_revalidating_or_native_transport(self):
        self.call('configure', topic='Thread naming')
        with patch.object(title_sync, '_task', return_value=deepcopy(self.task)) as task_read, \
                patch.object(self.native, 'read', side_effect=AssertionError('Unneeded native read')):
            self.call()
        self.assertFalse(task_read.call_args.kwargs['revalidate'])

    def test_configure_rejects_member_and_parent(self):
        for extra in (dict(member='C'), dict(parent_session_id='parent')):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.call('configure', topic='Title', **extra)
            self.assertEqual(self.native.writes, 0)
            self.assertFalse(title_sync._path(self.scope, 'codex', 'session').exists())

    def test_record_in_another_shape_is_unmanaged(self):
        import json
        self.call('configure', topic='Thread naming')
        path = title_sync._path(self.scope, 'codex', 'session')
        path.write_text(json.dumps(json.loads(path.read_text()) | dict(family='W1', member='C')))
        before = self.native.title
        self.task.update(state='REVIEW', user_attention=True, revision=self.task['revision'] + 1)
        self.assertEqual(self.call(), dict(ok=True, managed=False))
        self.assertFalse(title_sync.repair(self.scope, 'codex', 'session')['managed'])
        self.assertEqual(self.native.title, before)
        self.assertEqual(self.native.writes, 1)

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

    def test_configure_never_touches_the_register(self):
        store = self.home / '.vibe-workflow' / 'workstreams-families.sqlite3'
        store.parent.mkdir(mode=0o700)
        store.write_bytes(b'corrupt family register')
        self.call('configure', topic='Thread naming')
        self.assertEqual(self.native.title, '\uFFFB▰▱▱▱▱ Thread naming')
        self.assertEqual(store.read_bytes(), b'corrupt family register')

    def test_topic_survives_next_status_update_and_persistence(self):
        self.call('configure', topic='Thread naming')
        self.native.title = self.native.title.replace('Thread naming','Better naming')
        self.task.update(state='REVIEW', user_attention=True, revision=self.task['revision']+1)
        self.call()
        self.assertEqual(self.native.title, '\uFFFB▱▱▱▱▱ REVIEW · Better naming')
        self.task.update(state='WORKING', user_attention=False, revision=self.task['revision']+1)
        self.call()
        self.assertEqual(self.native.title, '\uFFFB▰▱▱▱▱ Better naming')

    def test_manual_done_does_not_complete_task_and_notice_only_once(self):
        self.call('configure', topic='Thread naming')
        manual = self.native.title.replace(' Thread naming', ' DONE · Thread naming')
        self.native.title = manual
        self.task.update(revision=self.task['revision']+1)
        self.assertIsNotNone(self.call()['notification'])
        self.task.update(revision=self.task['revision']+1)
        self.assertIsNone(self.call()['notification'])
        self.assertEqual(self.native.title, manual)
        self.assertEqual(self.task['state'], 'WORKING')
        self.call('resume')
        self.assertEqual(self.native.title, '\uFFFB▰▱▱▱▱ Thread naming')

    def test_failed_acknowledgment_repairs_without_duplicate_write(self):
        self.call('configure', topic='Thread naming')
        self.task.update(state='REVIEW', revision=self.task['revision']+1)
        self.native.lose_ack = True
        with self.assertRaises(OSError): self.call()
        self.call()
        self.assertEqual(self.native.writes, 2)
        presentation = self.call('status')['presentation']
        self.assertFalse(presentation['paused'])
        self.assertEqual(self.native.title, '\uFFFB▱▱▱▱▱ Thread naming')

    def test_stale_revision_cannot_adopt_or_rename(self):
        with self.assertRaises(ValueError):
            self.call('configure', topic='Thread naming', expected_revision=0)
        self.assertEqual(self.native.writes, 0)

    def test_done_meter_uses_authoritative_status_only(self):
        self.call('configure', topic='Thread naming')
        self.task.update(state='DONE', revision=self.task['revision']+1)
        self.call()
        self.assertTrue(self.native.title.startswith('\uFFFB▰▰▰▰▰ '))

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
        self.task.update(state='REVIEW', user_attention=True, revision=self.task['revision']+1)
        output = self.hook('PostToolUse', HookResult('PostToolUse', 'context', 'Existing context'))
        self.assertIn(' REVIEW · ', self.native.title)
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

    def test_unsent_label_removal_does_not_claim_a_manual_edit(self):
        self.task.update(state='WAITING', user_attention=True)
        self.call('configure', topic='Thread naming')
        applied = self.native.title
        manual = applied.replace(' WAITING · ', ' ').replace('Thread naming', 'Owner topic')
        self.task.update(user_attention=False, revision=self.task['revision'] + 1)
        with patch.object(self.native, 'read', side_effect=[applied, manual]):
            self.assertTrue(self.call()['pending'])
        self.native.title = manual
        self.assertTrue(self.call()['paused'])
        self.task.update(user_attention=True, revision=self.task['revision'] + 1)
        self.call()
        self.assertEqual(self.native.title, manual)
        self.assertEqual(self.native.writes, 1)


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

    def command(self, arguments, request, *, expected_code=0):
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
        self.assertEqual(code, expected_code, result)
        return result

    def configure(self, **topic_fields):
        task = self.fixture.status()['task']
        return self.command(['task', 'title', 'configure'], {
            key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')} |
            dict(expected_revision=task['revision'], reference='user:adopt') |
            (topic_fields or dict(topic='Real title')))

    def start_new_objective(self):
        old = self.fixture.status()['task']
        task = self.fixture.start(task_id='next-task', replaces={
            'task_id': old['task_id'], 'expected_revision': old['revision']})['task']
        self.assertEqual(task.get('milestones', []), [])

    def assert_topic_rebind(self, expected_title):
        self.start_new_objective()
        result = self.configure(workflow_topic='New objective')
        self.assertEqual(self.native.title, expected_title)
        self.assertEqual(len(list(self.fixture.scope.runtime.state_dir.glob('title-history-*.json'))), 1)
        return result

    def test_workflow_topic_rebind_respects_ownership(self):
        self.configure(workflow_topic='Old objective')
        self.assert_topic_rebind('\uFFFB▱▱▱▱▱ New objective')

    def test_distinct_sessions_keep_separate_records_and_writes(self):
        contexts = {session: NativeFixture() for session in ('session', 'owner', 'worker')}

        class SessionTitles(NativeFixture):
            def read(self, session): return contexts[session].read(session)
            def write(self, session, title): contexts[session].write(session, title)

        self.native = SessionTitles()
        self.configure(topic='Root')
        self.assertEqual(contexts['session'].title, '\uFFFB▱▱▱▱▱ Root')
        tasks = {}
        for session, topic in (('owner', 'Owner'), ('worker', 'Nested worker')):
            task = self.fixture.start(session_id=session, task_id='task-' + session)['task']
            tasks[session] = task
            self.command(['task', 'title', 'configure'],
                {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')} |
                dict(expected_revision=task['revision'], reference='user:adopt', topic=topic))
            self.assertEqual(contexts[session].title, f'\uFFFB▱▱▱▱▱ {topic}')
            report = self.command(['task', 'title', 'status'], dict(client='codex', session_id=session))
            self.assertEqual(report['presentation']['identity']['session_id'], session)
        owner_path = title_sync._path(self.fixture.scope, 'codex', 'owner')
        worker_path = title_sync._path(self.fixture.scope, 'codex', 'worker')
        self.assertNotEqual(owner_path, worker_path)
        worker_before = worker_path.read_bytes()
        task = tasks['owner']
        self.command(['task', 'update'],
            {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')} |
            dict(expected_revision=task['revision'], state='WORKING', reason='Owner work', reference='user:work'))
        self.assertEqual(contexts['owner'].title, '\uFFFB▰▱▱▱▱ Owner')
        self.assertEqual(contexts['owner'].written_sessions, ['owner', 'owner'])
        self.assertEqual(contexts['worker'].written_sessions, ['worker'])
        self.assertEqual(contexts['worker'].title, '\uFFFB▱▱▱▱▱ Nested worker')
        self.assertEqual(worker_path.read_bytes(), worker_before)
    def test_workflow_topic_rebind_preserves_explicit_topic(self):
        self.configure(topic='User wording')
        self.assert_topic_rebind('\uFFFB▱▱▱▱▱ User wording')

    def test_workflow_topic_rebind_preserves_legacy_topic(self):
        import json
        self.configure(workflow_topic='Old objective')
        path = title_sync._path(self.fixture.scope, 'codex', 'session')
        saved = json.loads(path.read_text())
        del saved['topic_workflow_owned']
        path.write_text(json.dumps(saved))
        self.assert_topic_rebind('\uFFFB▱▱▱▱▱ Old objective')

    def test_workflow_topic_rebind_preserves_previously_reconciled_edit(self):
        self.configure(workflow_topic='Old objective')
        self.native.title = self.native.title.replace('Old objective', 'My research')
        self.command(['task', 'title', 'sync'], dict(client='codex', session_id='session'))
        self.assert_topic_rebind('\uFFFB▱▱▱▱▱ My research')

    def test_workflow_topic_rebind_reconciles_edit_before_choosing_topic(self):
        self.configure(workflow_topic='Old objective')
        self.native.title = self.native.title.replace('Old objective', 'My research')
        self.assert_topic_rebind('\uFFFB▱▱▱▱▱ My research')

    def test_workflow_topic_rebind_preserves_paused_title(self):
        self.configure(workflow_topic='Old objective')
        self.native.title = 'My custom title'
        self.command(['task', 'title', 'sync'], dict(client='codex', session_id='session'))
        before = self.native.writes
        self.assertTrue(self.assert_topic_rebind('My custom title')['paused'])
        self.assertEqual(self.native.writes, before)

    def test_workflow_topic_rebind_recovers_lost_acknowledgment(self):
        self.configure(workflow_topic='Old objective')
        self.start_new_objective()
        before = self.native.writes
        self.native.lose_ack = True
        task = self.fixture.status()['task']
        identity = {key: task[key] for key in ('client', 'session_id')}
        request = identity | {key: task[key] for key in ('task_id', 'scope_ref')} | dict(
            expected_revision=task['revision'], reference='user:new', workflow_topic='New objective')
        result = self.command(['task', 'title', 'configure'], request, expected_code=1)
        self.assertFalse(result['ok'])
        status = self.command(['task', 'title', 'status'], identity)
        self.assertEqual(status['presentation']['pending'], '\uFFFB▱▱▱▱▱ New objective')
        self.command(['task', 'title', 'sync'], identity)
        self.assertEqual(self.native.title, '\uFFFB▱▱▱▱▱ New objective')
        self.assertEqual(self.native.writes, before + 1)
        self.assertIsNone(self.command(['task', 'title', 'status'], identity)['presentation']['pending'])

    def test_invalid_workflow_topic_requests_leave_native_title_untouched(self):
        task = self.fixture.status()['task']
        request = {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')} | dict(
            expected_revision=task['revision'], reference='user:adopt', workflow_topic='Generated')
        cases = [('configure', dict(topic='Explicit')), ('configure', dict(workflow_topic='')),
                 ('configure', dict(workflow_topic=None)), ('resume', {})]
        for action, extra in cases:
            with self.subTest(action=action, extra=extra):
                self.command(['task', 'title', action], request | extra, expected_code=1)
        self.assertEqual(self.native.writes, 0)

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
        baseline = self.native.title.split(' ', 1)[1]
        original = self.fixture.status()['task']
        identity = {key: original[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')}
        for status, attention, cells in (('WORKING', False, '▰▱▱▱▱'), ('REVIEW', False, '▱▱▱▱▱'),
                ('REVIEW', True, '▱▱▱▱▱'), ('WAITING', True, '▱▱▱▱▱'), ('WAITING', False, '▱▱▱▱▱'),
                ('WAITING', True, '▱▱▱▱▱'), ('WORKING', False, '▰▱▱▱▱')):
            with self.subTest(status=status, attention=attention):
                task = self.fixture.status()['task']
                self.command(['task', 'update'], identity | dict(expected_revision=task['revision'],
                    state=status, reason='Current next action', reference='fixture:action',
                    resolution_ref='fixture:resolution', user_attention=attention))
                label = f'{status} · ' if attention else ''
                self.assertEqual(self.native.title, f'\uFFFB{cells} {label}{baseline}')
                task = self.fixture.status()['task']
                self.assertEqual(task['state'], status)
                self.assertEqual({key: task[key] for key in (*identity, 'pins')}, identity | dict(pins=original['pins']))
        self.assertEqual(set(self.native.written_sessions), {identity['session_id']})

    def test_real_milestone_acceptance_updates_meter_on_next_command(self):
        self.configure()
        task = self.fixture.status()['task']
        self.command(['task','update'], {key: task[key] for key in ('client','session_id','task_id','scope_ref')} |
            dict(expected_revision=task['revision'], state='WORKING', reason='Accepted design', reference='user:approve',
                 milestones=[dict(id='design',weight=2,accepted=True,reference='user:approve'),
                             dict(id='implementation',weight=3,accepted=False,reference='plan:next')]))
        self.assertTrue(self.native.title.startswith('\uFFFB▰▰▱▱▱ '))

    def test_explicit_new_task_adoption_rebinds_presentation_preserving_history(self):
        self.configure()
        old = self.fixture.status()['task']
        self.fixture.start(task_id='next-task', replaces=dict(task_id=old['task_id'], expected_revision=old['revision']))
        self.configure()
        self.assertTrue(self.native.title.startswith('\uFFFB▱▱▱▱▱ '))
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
    def test_verified_task_can_reopen_title_with_retained_milestones(self):
        import io
        import json
        from types import SimpleNamespace
        from runtime import state
        from scripts import workflow
        from tests import test_evidence

        fixture = test_evidence.EvidenceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        accepted = [dict(id=name, weight=1, accepted=True, reference='proof:accepted')
                    for name in ('collect', 'normalize', 'analyze', 'summarize')]
        state.update_task(fixture.scope, fixture.request(
            state='WORKING', reason='Record accepted work', reference='proof:accepted', milestones=accepted))
        fixture.plan()
        fixture.checked()
        fixture.review()
        done = fixture.verify()['task']
        self.assertEqual(done['state'], 'DONE')
        native = NativeFixture()
        native_config = fixture.request(reference='user:adopt', workflow_topic='Completed task')
        with patch.object(Path, 'home', return_value=fixture.root):
            title_sync.operate(fixture.scope, 'configure', native_config, transport=lambda: native)
        self.assertEqual(native.title, '\uFFFB▰▰▰▰▰ Completed task')
        milestones = accepted + [dict(id='next-data', weight=1, accepted=False, reference='user:continue')]
        request = fixture.request(state='WORKING', reason='Check another dataset',
                                  reference='user:continue', milestones=milestones)
        output = io.StringIO()
        with patch.object(Path, 'home', return_value=fixture.root), \
             patch('sys.argv', ['workflow.py', 'task', 'update', '--cwd', str(fixture.repo)]), \
             patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
             patch('sys.stdout', output), \
             patch('runtime.startup.acquire_project_entry', return_value=test_acquisition(fixture.scope)), \
             patch.object(title_sync, 'NativeTitles', return_value=native):
            self.assertEqual(workflow.main(), 0, output.getvalue())
        reopened = json.loads(output.getvalue())['task']
        self.assertEqual(reopened['task_id'], done['task_id'])
        self.assertEqual(reopened['milestones'], milestones)
        self.assertEqual(native.title, '\uFFFB▰▰▰▰▱ Completed task')

    def test_explicit_refresh_revalidates_done_before_title_fast_path(self):
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
        self.assertEqual(native.title, '\uFFFB▰▰▰▰▰ Completed task')
        (fixture.repo / 'app.py').write_text('print("Changed after completion")\n')
        title_sync.operate(fixture.scope, 'sync', dict(client='codex',session_id='parent'), transport=lambda: native, force=True)
        self.assertEqual(native.title, '\uFFFB▱▱▱▱▱ Completed task')

    def test_completion_verification_cannot_resolve_wait_states(self):
        from tests import test_evidence
        from runtime import state
        fixture = test_evidence.EvidenceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plan()
        fixture.checked()
        fixture.review()
        for status in ('WAITING', 'PAUSED'):
            task = state.task_status(fixture.scope, 'codex', 'parent')['task']
            state.update_task(fixture.scope, fixture.request(state=status, reason='Waiting for user', reference='user:wait'))
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, '[Rr]esol'):
                fixture.api.verify_completion(fixture.scope, fixture.request(purpose='completion'))
            self.assertEqual(state.task_status(fixture.scope, 'codex', 'parent')['task']['state'], status)
            state.update_task(fixture.scope, fixture.request(state='WORKING', reason='User resumed', reference='user:resume', resolution_ref='user:resume'))
