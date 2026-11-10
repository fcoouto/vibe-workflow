"""C3-0b: the routing service's project pin, managed projects, activation gate, observations and factory.

The contract is the C.3 design (docs/superpowers/specs/2026-11-10-smart-routing-c3-wiring-design.md) 3.3, 4.2 and
6.3 C3-0b. The collector (C3-1) is stubbed through `sys.modules`, its only interface being
`collect(project, assignment) -> {observations, seen}`; no model, network or host call is made.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from runtime import routing_gates, routing_settings, state
from scripts import routing as command
from tests import test_routing_gates as gate_tests
from tests.test_routing_command import Base, observations, prepare

COLLECTOR = 'runtime.routing_observations'
SEEN = {'openrouter_revision': 7}


@contextmanager
def collector(result=None, error=None):
    """Install a stub collector module; it records each call and returns `result` (or raises `error`)."""
    calls = []

    def collect(project, assignment, *, source=None):
        calls.append(dict(project=project, assignment=assignment, source=source))
        if error is not None:
            raise error
        return result
    module = types.ModuleType(COLLECTOR)
    module.collect = collect
    with mock.patch.dict(sys.modules, {COLLECTOR: module}):
        yield calls


def without(request, key):
    return {name: value for name, value in request.items() if name != key}


class ManagedBase(Base):
    """A pinned service whose project is on `managed_projects` (written directly, bypassing the gate)."""
    def setUp(self):
        super().setUp()
        self.project = Path(self.temp.name).resolve() / 'checkout'
        self.project.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, project=self.project)


class ProjectPinTests(gate_tests.GateBase):
    def unpinned(self):
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, workflow=self.workflow)

    def test_an_unpinned_workflow_prepare_is_project_required_before_any_workflow_read(self):
        self.unpinned()
        views = self.workflow.views
        response = self.call(prepare('p1', workflow=gate_tests.locator()))
        self.assertEqual(response, dict(v=1, state='blocked', reason='project_required'))
        self.assertEqual(self.workflow.views, views)
        self.assertFalse(self.ledger.exists())

    def test_an_unpinned_prepare_without_workflow_still_admits(self):
        self.unpinned()
        self.assertEqual(self.call(prepare('p2'))['state'], 'admitted')

    def test_settle_without_workflow_on_an_unpinned_service_still_works(self):
        self.unpinned()
        self.assertEqual(self.call(prepare('p3'))['state'], 'admitted')
        response = self.call(dict(v=1, op='settle', root_task_id='task-p3'))
        self.assertNotEqual(response.get('reason'), 'project_required', response)
        self.assertNotEqual(response['state'], 'blocked', response)

    def test_the_gate_base_service_is_pinned(self):
        self.assertIs(self.call(dict(v=1, op='status'))['project_pinned'], True)


class ObservationTests(ManagedBase):
    def test_a_managed_prepare_carrying_observations_is_refused(self):
        with collector(dict(observations=observations(), seen=SEEN)) as calls:
            response = self.call(prepare('m1'))
        self.assertEqual(response, dict(v=1, state='blocked', reason='observations_not_accepted'))
        self.assertEqual(calls, [])
        self.assertFalse(self.ledger.exists())

    def test_without_a_collector_a_managed_prepare_is_observations_unavailable(self):
        with mock.patch.dict(sys.modules, {COLLECTOR: None}):
            response = self.call(without(prepare('m2'), 'observations'))
        self.assertEqual(response, dict(v=1, state='blocked', reason='observations_unavailable'))
        self.assertFalse(self.ledger.exists())

    def test_a_failing_or_malformed_collector_is_observations_unavailable(self):
        for result, error in ((None, RuntimeError('boom')), (observations(), None),
                              (dict(observations=observations()), None), (dict(observations=observations(), seen=[]),
                                                                          None)):
            with self.subTest(result=result, error=error), collector(result, error):
                response = self.call(without(prepare('m3'), 'observations'))
                self.assertEqual(response, dict(v=1, state='blocked', reason='observations_unavailable'))

    def test_the_collector_is_called_with_the_pinned_project_and_seen_is_echoed_when_admitted(self):
        with collector(dict(observations=observations(), seen=dict(SEEN))) as calls:
            response = self.call(without(prepare('m4'), 'observations'))
        self.assertEqual(response['state'], 'admitted', response)
        self.assertEqual(response['seen'], SEEN)
        self.assertEqual([call['project'] for call in calls], [str(self.project)])
        self.assertEqual(calls[0]['assignment']['attempt_id'], 'm4')
        self.assertIsNone(calls[0]['source'])

    def test_seen_is_echoed_unchanged_when_pending(self):
        spent = observations()
        spent['pools']['sub']['windows'][0]['used'] = 1000
        with collector(dict(observations=spent, seen=dict(SEEN))):
            response = self.call(without(prepare('m5'), 'observations'))
        self.assertEqual(response['state'], 'pending', response)
        self.assertEqual(response['seen'], SEEN)

    def test_seen_is_opaque(self):
        opaque = {'openrouter_revision': None, 'later_revision': 3}
        with collector(dict(observations=observations(), seen=opaque)):
            response = self.call(without(prepare('m6'), 'observations'))
        self.assertEqual(response['seen'], opaque)

    def test_an_unmanaged_project_keeps_caller_observations_and_gets_no_seen(self):
        routing_settings.update(self.settings, dict(managed_projects=[]), 1)
        with collector(dict(observations=observations(), seen=SEEN)) as calls:
            response = self.call(prepare('u1'))
            missing = self.call(without(prepare('u2'), 'observations'))
        self.assertEqual(response['state'], 'admitted', response)
        self.assertNotIn('seen', response)
        self.assertEqual(missing, dict(v=1, state='blocked', reason='invalid_request'))
        self.assertEqual(calls, [])

    def test_test_inputs_still_accept_caller_observations(self):
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, project=self.project, test_inputs=True)
        self.assertEqual(self.call(prepare('t1'))['state'], 'admitted')

    def test_unreadable_managed_state_fails_closed_as_managed(self):
        with mock.patch.object(routing_settings, 'managed_project', side_effect=RuntimeError('unreadable')):
            response = self.call(prepare('f1'))
        self.assertEqual(response['reason'], 'observations_not_accepted')


def write_checks(folder):
    """Self-check modules for a stand-in package root: pass, fail, sleep, environment probe."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'gate_pass.py').write_text('def managed_self_check(workdir):\n    return []\n')
    (folder / 'gate_fail.py').write_text("def managed_self_check(workdir):\n    return ['fixture_failed']\n")
    (folder / 'gate_sleep.py').write_text('import time\ndef managed_self_check(workdir):\n    time.sleep(30)\n'
                                          '    return []\n')
    (folder / 'gate_noisy.py').write_text("def managed_self_check(workdir):\n    print('x' * 10)\n"
                                          "    return ['Not A Code!']\n")
    (folder / 'gate_env.py').write_text('''import os, sys
def managed_self_check(workdir):
    problems = []
    env = dict(os.environ)
    env.pop('__CF_USER_TEXT_ENCODING', None)  # macOS adds this to every process
    if set(env) != {'PATH', 'HOME', 'TMPDIR', 'LANG'} or env['PATH'] != '/usr/bin:/bin' or env['LANG'] != 'C.UTF-8':
        problems.append('environment_leaked')
    base = os.path.dirname(workdir)
    if env.get('HOME') != os.path.join(base, 'home') or env.get('TMPDIR') != os.path.join(base, 'tmp'):
        problems.append('sandbox_paths')
    if os.listdir(workdir) or os.stat(workdir).st_mode & 0o777 != 0o700 or os.getcwd() != os.path.realpath(workdir):
        problems.append('workdir_not_private')
    if not sys.flags.isolated or sys.path[0] != os.path.dirname(os.path.abspath(__file__)):
        problems.append('import_path')
    return problems
''')
    (folder / 'data').mkdir(exist_ok=True)
    (folder / 'data' / 'routes.json').write_text(json.dumps(dict(routes=[dict(id='r', status='qualified')])))
    (folder / 'data' / 'empty.json').write_text(json.dumps(dict(routes=[dict(id='r', status='unverified')])))
    (folder / 'data' / 'policy.md').write_text('# Policy\n\n## Managed projects\n\nText.\n')
    (folder / 'data' / 'other.md').write_text('# Policy\n\nManaged projects are mentioned, not a heading.\n')
    script = folder / 'scripts'
    script.mkdir(exist_ok=True)
    (script / 'gate_script.py').write_text("def managed_self_check(workdir):\n    return []\n"
                                           "if __name__ == '__main__':\n    raise SystemExit(3)\n")


class ActivationGateTests(Base):
    def setUp(self):
        super().setUp()
        self.project = Path(self.temp.name).resolve() / 'checkout'
        self.project.mkdir()
        self.package = Path(self.temp.name).resolve() / 'package'
        write_checks(self.package)

    def add(self, projects=None, revision=0, service=None):
        change = dict(managed_projects=[str(self.project)] if projects is None else projects)
        return (service or self.service).handle(json.dumps(dict(v=1, op='set', expected_revision=revision,
                                                                change=change)).encode())

    def gate(self, *capabilities):
        return mock.patch.multiple(command, CAPABILITIES=tuple(capabilities), _package=lambda: self.package)

    def test_the_gate_lists_every_phase_w_capability(self):
        self.assertEqual([name for name, _, _ in command.CAPABILITIES],
                         ['collector_and_launcher', 'route_manifest', 'native_delegation', 'opencode_launcher',
                          'openrouter_proposals', 'openrouter_workers', 'api_usage', 'instructions'])
        self.assertEqual(command._package(), state.PACKAGE)

    def test_adding_a_project_is_refused_under_serve(self):
        serving = command.Service(self.settings, self.ledger, serving=True)
        self.addCleanup(serving.close)
        with self.gate(('ok', 'module', 'gate_pass:managed_self_check')):
            response = self.add(service=serving)
            removal = self.add([], service=serving)
        self.assertEqual(response['reason'], 'operator_op_not_served')
        self.assertEqual(removal['reason'], 'operator_op_not_served')
        self.assertEqual(routing_settings.revision(self.service.settings_path), 0)

    def test_the_real_gate_refuses_while_capabilities_are_missing(self):
        """At C3-0b's base no phase W capability exists yet: the real checks run in children and fail."""
        spawned = []
        real = subprocess.Popen

        def spy(argv, **kwargs):
            spawned.append((argv, kwargs))
            return real(argv, **kwargs)
        with mock.patch.object(command.subprocess, 'Popen', spy):
            response = self.add()
        self.assertEqual((response['state'], response['reason']), ('blocked', 'managed_path_incomplete'), response)
        self.assertIn('collector_and_launcher', response['failing'])
        self.assertEqual(sorted(response['failing']), sorted(response['codes']))
        self.assertEqual(routing_settings.revision(self.service.settings_path), 0)
        self.assertEqual(len(spawned), len(command.CAPABILITIES))
        for argv, kwargs in spawned:
            self.assertEqual(argv[:4], [sys.executable, '-I', '-B', '-c'])
            self.assertEqual(argv[5], str(state.PACKAGE))
            self.assertEqual(set(kwargs['env']), {'PATH', 'HOME', 'TMPDIR', 'LANG'})
            self.assertEqual(kwargs['env']['PATH'], '/usr/bin:/bin')
            self.assertIs(kwargs['start_new_session'], True)

    def test_a_passing_gate_adds_the_realpath(self):
        link = Path(self.temp.name).resolve() / 'link'
        link.symlink_to(self.project)
        with self.gate(('module', 'module', 'gate_pass:managed_self_check'), ('script', 'script', 'scripts/gate_script.py'),
                       ('manifest', 'manifest', 'data/routes.json'), ('heading', 'heading', 'data/policy.md'),
                       ('env', 'module', 'gate_env:managed_self_check')):
            response = self.add([str(link)])
        self.assertEqual(response['state'], 'updated', response)
        self.assertEqual(routing_settings.read(self.service.settings_path)['managed_projects'], [str(self.project)])
        self.assertIs(routing_settings.managed_project(self.project, settings_path=self.service.settings_path), True)

    def test_a_failing_or_missing_check_refuses_and_writes_nothing(self):
        cases = [(('bad', 'module', 'gate_fail:managed_self_check'), ['fixture_failed']),
                 (('gone', 'module', 'gate_absent:managed_self_check'), ['self_check_missing']),
                 (('gone', 'script', 'scripts/absent.py'), ['self_check_missing']),
                 (('noisy', 'module', 'gate_noisy:managed_self_check'), ['self_check_invalid']),
                 (('manifest', 'manifest', 'data/empty.json'), ['no_qualified_route']),
                 (('heading', 'heading', 'data/other.md'), ['heading_missing'])]
        for capability, codes in cases:
            with self.subTest(capability), self.gate(('ok', 'module', 'gate_pass:managed_self_check'), capability):
                response = self.add()
                self.assertEqual(response, dict(v=1, state='blocked', reason='managed_path_incomplete',
                                                failing=[capability[0]], codes={capability[0]: codes}))
                self.assertEqual(routing_settings.revision(self.service.settings_path), 0)

    def test_a_slow_check_times_out_and_its_sandbox_is_deleted(self):
        made = []
        real = tempfile.mkdtemp

        def record(*args, **kwargs):
            made.append(real(*args, **kwargs))
            return made[-1]
        with self.gate(('slow', 'module', 'gate_sleep:managed_self_check')), \
                mock.patch.object(command, 'SELF_CHECK_TIMEOUT', 1), mock.patch.object(command.tempfile, 'mkdtemp',
                                                                                        record):
            response = self.add()
        self.assertEqual(response['codes'], {'slow': ['self_check_timeout']})
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))

    def test_removal_is_always_allowed_one_shot(self):
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        with self.gate(('bad', 'module', 'gate_fail:managed_self_check')):
            response = self.add([], revision=1)
        self.assertEqual(response['state'], 'updated', response)
        self.assertEqual(routing_settings.read(self.service.settings_path)['managed_projects'], [])

    def test_an_invalid_list_is_refused_before_the_gate(self):
        with self.gate(('bad', 'module', 'gate_sleep:managed_self_check')):
            for bad in (['relative/path'], 'not-a-list', [7]):
                with self.subTest(bad):
                    self.assertEqual(self.add(bad)['reason'], 'invalid_managed_projects')
        self.assertEqual(routing_settings.revision(self.service.settings_path), 0)


class ManagedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.checkout = root / 'checkout'
        self.checkout.mkdir()
        self.link = root / 'link'
        self.link.symlink_to(self.checkout)
        self.paths = mock.patch.multiple(command, default_settings=lambda: root / 'routing' / 'settings.json',
                                         default_ledger=lambda: root / 'usage' / 'organizations.sqlite3')

    def test_managed_service_pins_the_realpath_and_reads_the_default_paths_at_call_time(self):
        with self.paths:
            service = command.managed_service(self.link)
        self.addCleanup(service.close)
        root = Path(self.temp.name).resolve()
        self.assertEqual(service.project, self.checkout)
        self.assertEqual(service.settings_path, root / 'routing' / 'settings.json')
        self.assertEqual(service.ledger_path, root / 'usage' / 'organizations.sqlite3')
        self.assertIsInstance(service.workflow, routing_gates.D6Workflow)
        self.assertEqual(service.workflow.project, self.checkout)
        self.assertIs(service.workflow._resolve, routing_gates._resolve_scope)
        self.assertFalse(service.test_inputs or service.serving)
        self.assertIs(service.handle(b'{"v":1,"op":"status"}')['project_pinned'], True)

    def test_a_supplied_scope_resolves_every_locator_and_is_still_checked_against_the_pin(self):
        scope = types.SimpleNamespace(repository_root=self.checkout)
        with self.paths:
            service = command.managed_service(self.checkout, scope=scope)
        self.addCleanup(service.close)
        self.assertIs(service.workflow._resolve(gate_tests.locator()), scope)
        other = types.SimpleNamespace(repository_root=Path(self.temp.name).resolve())
        with self.paths:
            elsewhere = command.managed_service(self.checkout, scope=other)
        self.addCleanup(elsewhere.close)
        with self.assertRaises(routing_gates.ProjectMismatch):
            elsewhere.workflow.view(gate_tests.locator(cwd=str(self.checkout)))


if __name__ == '__main__':
    unittest.main()
