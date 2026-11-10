"""C3-0b: the routing service's project pin, managed projects, activation gate, observations and factory.

The contract is the C.3 design (docs/superpowers/specs/2026-11-10-smart-routing-c3-wiring-design.md) 3.3, 4.2 and
6.3 C3-0b. The collector (C3-1) is stubbed through `sys.modules`, its only interface being
`collect(project, assignment) -> {observations, seen}`; no model, network or host call is made.
"""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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

    def collect(project, assignment, *, settings_path, ledger_path, source=None):
        calls.append(dict(project=project, assignment=assignment, source=source, settings_path=settings_path,
                          ledger_path=ledger_path))
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

    def test_the_collector_reads_the_services_own_stores(self):
        with collector(dict(observations=observations(), seen=dict(SEEN))) as calls:
            self.call(without(prepare('s1'), 'observations'))
        self.assertEqual((calls[0]['settings_path'], calls[0]['ledger_path']),
                         (self.service.settings_path, self.service.ledger_path))

    def test_the_cli_passes_non_default_store_paths_to_the_collector(self):
        """`--settings`/`--ledger` reach the collector unchanged; it never falls back to the default stores."""
        self.assertNotEqual(self.service.settings_path, command._resolved(command.default_settings()))
        stdout = io.BytesIO()
        with collector(dict(observations=observations(), seen=dict(SEEN))) as calls:
            code = command.main(['prepare', '--settings', str(self.settings), '--ledger', str(self.ledger),
                                 '--project', str(self.project)],
                                stdin=io.BytesIO(json.dumps(without(prepare('s2'), 'observations')).encode()),
                                stdout=stdout)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())['seen'], SEEN)
        self.assertEqual((calls[0]['settings_path'], calls[0]['ledger_path']),
                         (command._resolved(self.settings), command._resolved(self.ledger)))

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
    (folder / 'data' / 'routes.json').write_text(json.dumps(dict(version=1, routes=[dict(id='r', status='qualified')])))
    (folder / 'data' / 'empty.json').write_text(json.dumps(dict(version=1, routes=[dict(id='r', status='unverified')])))
    (folder / 'data' / 'bare.json').write_text(json.dumps([dict(id='r', status='qualified')]))
    (folder / 'data' / 'unversioned.json').write_text(json.dumps(dict(routes=[dict(id='r', status='qualified')])))
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
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, one_shot=True)  # as only `main()` builds it
        from runtime import delegation_routing  # the host check has its own tests (ActivationHostTests)
        hosts = mock.patch.object(delegation_routing, 'enrolled_hosts', return_value=['codex'])
        hosts.start()
        self.addCleanup(hosts.stop)

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

    def test_an_in_process_service_refuses_add_and_remove(self):
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        plain = command.Service(self.settings, self.ledger)
        self.addCleanup(plain.close)
        with mock.patch.multiple(command, default_settings=lambda: self.settings, default_ledger=lambda: self.ledger):
            factory = command.managed_service(self.project)
        self.addCleanup(factory.close)
        with self.gate(('ok', 'module', 'gate_pass:managed_self_check')):
            for service in (plain, factory):
                for projects in ([], [str(self.project), str(Path(self.temp.name).resolve())]):
                    with self.subTest(service=service, projects=projects):
                        response = self.add(projects, revision=1, service=service)
                        self.assertEqual((response['state'], response['reason']),
                                         ('blocked', 'operator_op_not_served'))
        self.assertEqual(routing_settings.revision(self.service.settings_path), 1)
        self.assertEqual(routing_settings.read(self.service.settings_path)['managed_projects'], [str(self.project)])

    def test_the_one_shot_cli_changes_managed_projects(self):
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        stdout = io.BytesIO()
        request = dict(v=1, op='set', expected_revision=1, change=dict(managed_projects=[]))
        code = command.main(['set', '--settings', str(self.settings), '--ledger', str(self.ledger)],
                            stdin=io.BytesIO(json.dumps(request).encode()), stdout=stdout)
        self.assertEqual((code, json.loads(stdout.getvalue())['state']), (0, 'updated'))
        self.assertEqual(routing_settings.read(self.service.settings_path)['managed_projects'], [])

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
        """The real checks run in children against the real package, whichever phase W slices are merged. One
        capability is made missing through the gate's own list; `route_manifest` fails until user decision 5."""
        spawned = []
        real = subprocess.Popen

        def spy(argv, **kwargs):
            spawned.append((argv, kwargs))
            return real(argv, **kwargs)
        capabilities = command.CAPABILITIES + (('simulated_missing', 'module', 'runtime.c3_0b_absent:managed_self_check'),)
        with mock.patch.object(subprocess, 'Popen', spy), \
                mock.patch.object(command, 'CAPABILITIES', capabilities):
            response = self.add()
        self.assertEqual((response['state'], response['reason']), ('blocked', 'managed_path_incomplete'), response)
        self.assertEqual(response['codes']['simulated_missing'], ['self_check_missing'])
        self.assertIn('route_manifest', response['failing'])
        self.assertEqual(len(spawned), len(capabilities))
        self.assertEqual(sorted(response['failing']), sorted(response['codes']))
        self.assertEqual(routing_settings.revision(self.service.settings_path), 0)
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
                 (('manifest', 'manifest', 'data/bare.json'), ['manifest_invalid']),
                 (('manifest', 'manifest', 'data/unversioned.json'), ['manifest_invalid']),
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
                mock.patch.object(command, 'SELF_CHECK_TIMEOUT', 1), mock.patch.object(tempfile, 'mkdtemp',
                                                                                        record):
            response = self.add()
        self.assertEqual(response['codes'], {'slow': ['self_check_timeout']})
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))

    def test_a_grandchild_holding_the_pipe_is_killed_with_its_process_group(self):
        """The timeout kills the check's whole process group; killing only the child would leave the grandchild
        holding stdout, alive, and (without the bounded second wait) the gate blocked."""
        record = Path(self.temp.name).resolve() / 'grandchild.pid'
        (self.package / 'gate_grandchild.py').write_text(f"""import subprocess, sys, time
def managed_self_check(workdir):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    with open({str(record)!r}, 'w') as handle:
        handle.write(str(child.pid))
    time.sleep(60)
    return []
""")
        started = time.monotonic()
        with self.gate(('slow', 'module', 'gate_grandchild:managed_self_check')), \
                mock.patch.object(command, 'SELF_CHECK_TIMEOUT', 2), mock.patch.object(command, 'SELF_CHECK_REAP', 2):
            response = self.add()
        self.assertEqual(response['codes'], {'slow': ['self_check_timeout']})
        self.assertLess(time.monotonic() - started, 15)
        pid = int(record.read_text())
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(pid, 9)
            self.fail('the grandchild survived the gate timeout')

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


class ActivationHostTests(Base):
    """#222 follow-up F3: policy 03's Claude Code rule in code. A project joins only when one of its enrolled clients
    has a managed delegation path; the enrolment itself is stubbed (the real record is in test_delegation_routing)."""

    def setUp(self):
        super().setUp()
        self.project = Path(self.temp.name).resolve() / 'checkout'
        self.project.mkdir()
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, one_shot=True)

    def add(self, hosts, projects=None, revision=0):
        from runtime import delegation_routing
        enrolled = mock.patch.object(delegation_routing, 'enrolled_hosts', **(
            dict(side_effect=hosts) if isinstance(hosts, Exception) or callable(hosts) else dict(return_value=hosts)))
        change = dict(managed_projects=[str(self.project)] if projects is None else projects)
        with enrolled, mock.patch.object(command, 'activation_gate', return_value={}) as gate, \
                mock.patch.object(delegation_routing, 'open_unrouted_assignments', return_value=[]):
            response = self.service.handle(json.dumps(dict(v=1, op='set', expected_revision=revision,
                                                           change=change)).encode())
        return response, gate.call_count

    def test_the_managed_hosts_are_codex_and_opencode(self):
        self.assertEqual(command.MANAGED_HOSTS, ('codex', 'opencode'))

    def test_a_claude_code_only_project_is_refused_before_the_gate(self):
        response, gate = self.add(['claude-code'])
        self.assertEqual(response, dict(v=1, state='blocked', reason='advisory_host_unmanaged',
                                        projects=[str(self.project)]))
        self.assertEqual(gate, 0)
        self.assertEqual(routing_settings.revision(self.service.settings_path), 0)

    def test_a_project_without_any_enrolled_client_is_refused(self):
        response, _ = self.add([])
        self.assertEqual(response['reason'], 'advisory_host_unmanaged')

    def test_codex_opencode_and_a_mix_pass_this_check(self):
        for hosts in (['codex'], ['opencode'], ['claude-code', 'codex']):
            with self.subTest(hosts):
                response, gate = self.add(hosts, revision=routing_settings.revision(self.service.settings_path))
                self.assertEqual((response['state'], gate), ('updated', 1), response)
                self.assertEqual(routing_settings.read(self.service.settings_path)['managed_projects'], [str(self.project)])
                removal, _ = self.add(hosts, projects=[], revision=response['revision'])
                self.assertEqual(removal['state'], 'updated', removal)

    def test_an_unenrolled_project_is_refused_as_not_enrolled(self):
        from runtime import delegation_routing
        response, gate = self.add(delegation_routing.NotEnrolled('not_enrolled'))
        self.assertEqual(response, dict(v=1, state='blocked', reason='not_enrolled', projects=[str(self.project)]))
        self.assertEqual(gate, 0)

    def test_an_unreadable_enrolment_fails_closed(self):
        from runtime.startup import RuntimeScopeError
        for error in (RuntimeScopeError('qualification'), OSError('unreadable'), KeyError('repositories')):
            with self.subTest(error):
                response, gate = self.add(error)
                self.assertEqual(response, dict(v=1, state='blocked', reason='enrolment_unavailable',
                                                projects=[str(self.project)]))
                self.assertEqual(gate, 0)
        self.assertEqual(routing_settings.revision(self.service.settings_path), 0)

    def test_every_added_project_is_checked_and_an_existing_one_is_not(self):
        other = Path(self.temp.name).resolve() / 'other'
        other.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(other)]), 0)
        seen = []

        def hosts(checkout):
            seen.append(str(checkout))
            return ['claude-code']
        response, _ = self.add(hosts, projects=[str(other), str(self.project)], revision=1)
        self.assertEqual((response['reason'], response['projects']), ('advisory_host_unmanaged', [str(self.project)]))
        self.assertEqual(seen, [str(self.project)])
        response, _ = self.add(hosts, projects=[], revision=1)  # a Claude Code project can always be removed
        self.assertEqual(response['state'], 'updated', response)


class RollbackTests(Base):
    """#222 follow-up F3: the post-write re-check's rollback (`_recheck_activation`)."""

    def setUp(self):
        super().setUp()
        self.project = str(Path(self.temp.name).resolve() / 'checkout')
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, one_shot=True)
        routing_settings.update(self.settings, dict(managed_projects=[self.project]), 0)

    def recheck(self, write):
        from runtime import delegation_routing
        answer = dict(state='updated', revision=1, invalidated_attempts=['a1', 'a2'])
        with mock.patch.object(delegation_routing, 'open_unrouted_assignments', return_value=['open']), \
                mock.patch.object(self.service, '_write_settings', side_effect=write) as written:
            try:
                self.service._recheck_activation(answer, {self.project})
            except command.Blocked as error:
                return error, written.call_count
        self.fail('the re-check did not refuse')

    def test_a_failed_rollback_write_is_a_refusal_with_its_cause(self):
        for cause in (command.Blocked('settings_unavailable'), OSError('disk full')):
            with self.subTest(cause):
                error, writes = self.recheck(cause)
                self.assertEqual((error.reason, error.detail), ('open_unrouted_assignments', dict(
                    rolled_back=False, invalidated_attempts=['a1', 'a2'])))
                self.assertIs(error.__cause__, cause)
                self.assertEqual(writes, 1)

    def test_a_stale_revision_is_retried_then_reported(self):
        error, writes = self.recheck(command.Blocked('stale_revision'))
        self.assertEqual(error.detail, dict(rolled_back=False, invalidated_attempts=['a1', 'a2']))
        self.assertIsNone(error.__cause__)
        self.assertEqual(writes, command.MAX_ROLLBACK)

    def test_a_rollback_reports_the_first_writes_invalidated_attempts(self):
        error, writes = self.recheck(lambda body: dict(state='updated'))
        self.assertEqual((error.reason, error.detail), ('open_unrouted_assignments', dict(
            rolled_back=True, invalidated_attempts=['a1', 'a2'])))
        self.assertEqual(writes, 1)


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
        self.assertFalse(service.test_inputs or service.serving or service.one_shot)
        self.assertIs(service.handle(b'{"v":1,"op":"status"}')['project_pinned'], True)
        for projects in ([], [str(self.checkout)]):
            change = dict(v=1, op='set', expected_revision=0, change=dict(managed_projects=projects))
            self.assertEqual(service.handle(json.dumps(change).encode())['reason'], 'operator_op_not_served')

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
