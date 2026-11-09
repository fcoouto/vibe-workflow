"""Shared fixtures for worker-visibility tests; contains no test cases."""
import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid
from unittest.mock import patch

from runtime.adapters import codex


def fixture_transport(provider='codex'):
    client = SimpleNamespace(agents=[], events=[], unavailable=False)

    def snapshot(account):
        if client.unavailable:
            raise OSError('offline')
        return dict(accounts=[dict(id=account, provider=provider)],
                    agents=copy.deepcopy(client.agents), meta={})

    def event(account, value):
        client.events.append(copy.deepcopy(value))
        if value['hook_event_name'] == 'SubagentStart':
            client.agents.append(dict(id=account + ':' + value['agent_id'],
                parentId=account + ':' + value['session_id'], role=value['agent_type'], status='working'))

    def wire(binding, name, phase=None):
        value = dict(hook_event_name=name, session_id=binding['parent'],
                     agent_id=binding['agent_id'], agent_type=binding['label'], cwd=binding['cwd'])
        if phase is not None:
            value.update(tool_name=phase, tool_use_id='fixture-activity', tool_input={})
        return value

    return client, SimpleNamespace(Client=lambda endpoint: SimpleNamespace(snapshot=snapshot, event=event), _event=wire)


class NativeVisibilityFixture:
    """Mixin: disposable native checkout, thread catalog and reader stub."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name).resolve()
        self.root = home / 'checkout'
        self.root.mkdir(mode=0o700)
        self.task_context = home / 'task'
        self.task_context.mkdir(mode=0o700)
        self.parent, self.child = str(uuid.uuid4()), str(uuid.uuid4())
        self.parent_thread = dict(id=self.parent, cwd=str(self.root), cliVersion='0.163.0',
                                  source='exec', originator='codex_exec')
        self.child_thread = dict(id=self.child, parentThreadId=self.parent, cwd=str(self.root),
            cliVersion='0.163.0', model='Native model', modelProvider='native-account',
            source={'subAgent': {'thread_spawn': {'parent_thread_id': self.parent, 'agent_path': '/root/review_original'}}})
        self.spawn_prompt = None
        self.model_pages = {None: dict(data=[], nextCursor=None)}
        self.ancestors = {}

    def reader(self, methods, **options):
        from contextlib import contextmanager

        @contextmanager
        def reader():
            self.assertLessEqual(set(options), {'deadline', 'executable'})
            self.assertEqual(set(methods), {'initialize', 'thread/read', 'model/list'} |
                ({'thread/items/read'} if self.spawn_prompt is not None else set()))
            def request(method, params):
                if method == 'model/list':
                    self.assertEqual(params['limit'], 100)
                    self.assertIs(params['includeHidden'], True)
                    return copy.deepcopy(self.model_pages[params.get('cursor')])
                if method == 'thread/items/read':
                    self.assertEqual(params, {'threadId': self.parent, 'turnId': 'parent-turn', 'itemIds': ['spawn']})
                    return dict(data=[dict(turnId='parent-turn', item=dict(id='spawn', type='collabAgentToolCall',
                        tool='spawnAgent', status='completed', senderThreadId=self.parent,
                        receiverThreadIds=[self.child], prompt=self.spawn_prompt))])
                self.assertEqual(method, 'thread/read')
                threads = self.ancestors | {self.parent: self.parent_thread, self.child: self.child_thread}
                return dict(thread=copy.deepcopy(threads[params['threadId']]))
            yield '0.163.0', request
        return reader()

    def observe(self):
        with patch.object(codex, '_native_reader', self.reader):
            return codex.observe_worker_visibility(self.root, self.parent, self.child)

    def native_helper(self):
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'worker_visibility.py'
        self.assertTrue(path.is_file(), 'Ordinary native visibility helper is missing')
        spec = importlib.util.spec_from_file_location('native_visibility_helper', path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        return helper

    def native_request(self):
        return dict(parent_session_id=self.parent, checkout=str(self.root), task_name='review_original',
                    visibility=dict(version=1, role='Reviewer', specialty='Read only', custom_role_name='Audit lead'))
