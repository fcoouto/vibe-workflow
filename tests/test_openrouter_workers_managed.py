"""C3-4b (C.3 design 3.1, 3.3, 4.3, 6.3): in a managed project an OpenRouter worker's `prepare` obtains one routing
admission on the `gateway` route before the OpenRouter admission lock, and `execute` launches it through the OpenCode
managed launcher (runtime/opencode_launcher.py) with the read-only profile; the V1 launch refuses. Every hold is
closed on every path. A fake OpenCode server and the real routing command on temporary stores: no model call, no
OpenCode server, no network."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from runtime import opencode_launcher as launcher, openrouter_settings as settings, routing_settings
from scripts import routing as command
from tests.openrouter_fixtures import ManagedRouting, configure, enroll
from tests.test_opencode_launcher import FakeHost

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/openrouter_workers.py'
READ_ONLY = ('read', 'glob', 'grep')


class TextHost(FakeHost):
    """The C3-3 fake server, whose assistant messages also carry a text part and, when `cost` is set, an
    OpenCode-reported USD cost (an estimate: no provider receipt)."""
    cost = None

    def _assistant(self, sid, held, now):
        super()._assistant(sid, held, now)
        message = self.sessions[sid]['messages'][-1]
        message['content'] = [dict(type='text', text='Three lines')]
        if self.cost is not None:
            message['cost'] = self.sessions[sid]['record']['cost'] = self.cost


class ManagedWorkerCase(ManagedRouting, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('workers_managed', SCRIPT)
        cls.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.api)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        folder = self.root / 'routing-defaults'
        self.enterContext(patch.object(command, 'default_settings', lambda: folder / 'settings.json'))
        for name, value in (('IDLE_POLL', 0), ('REREAD_WAIT', 0)):
            self.enterContext(patch.object(launcher, name, value))
        self.scope = enroll(self)
        self.repo = self.scope.repository_root
        (self.root / 'auth').mkdir(mode=0o700)
        self.executable, self.marker = self.root / 'worker', self.root / 'v1-launched'
        self.executable.write_text('#!/bin/sh\ntouch %s\nexit 0\n' % self.marker)
        self.executable.chmod(0o700)
        self.settings = configure(self.repo, executable=self.executable, auth=self.root / 'auth', routes=('workers',))
        self.runs = Path(self.settings['worker_root'])
        (self.repo / 'input.txt').write_text('three lines\nof evidence\nfor review\n')
        self.hosts = []

    def manage(self, *endpoints):
        super().manage(*(endpoints or [('vendor/model', 'provider')]))

    def brief(self, task='pilot-1'):
        from runtime import external_delegation as external
        brief = dict(task_id=task, coordinator_chat_id=str(uuid.uuid4()), model='vendor/model', provider='provider',
                     objective='Read input.txt and count lines.', source_root=str(self.repo), files=['input.txt'],
                     timeout_seconds=5, steps=3, effort='low')
        consent = external.effective_permission(
            self.settings['external_permission'], self.settings['activation'], service='openrouter', mode='worker',
            model=brief['model'], provider=brief['provider'], effort=brief['effort'], class_name='investigation')
        packet = external.prepare(self.repo, consent, [dict(kind='text', text=brief['objective'],
                                                            sources=brief['files'])])
        return dict(brief, external=dict(packet['binding'], automatic=True, local_preferred=False,
                                         class_name='investigation', coordinator=brief['coordinator_chat_id'],
                                         reviewer='native-reviewer', integration_available=True))

    def serve(self, **knobs):
        """Every managed launch gets a fake OpenCode server in the run's private workspace copy."""
        shim = command.Service(self.stores['settings'], self.stores['ledger'], serving=True)
        self.addCleanup(shim.close)

        @contextmanager
        def server(run, executable, auth_data, service):
            host = TextHost(shim, str(Path(run) / 'workspace'), [], **knobs)
            self.hosts.append(host)
            yield host
        self.enterContext(patch.object(self.api, '_server', server))

    def qualified(self):
        """User decision 4 lifted for the test only: the launcher may send on an API-funded OpenCode route."""
        self.enterContext(patch.object(launcher, 'API_FUNDING_QUALIFIED', True))

    def execute(self, run):
        return self.api.execute(run, self.executable, self.root / 'auth')

    def routing_state(self, run):
        return json.loads((run / 'routing.json').read_text())['state']

    def files(self):
        """Every file under the test's folder and the worker runs (`worker_root`, in the scope's own state folder)."""
        folders = [self.root] + [folder for folder in (self.runs,) if self.root not in folder.parents]
        return b''.join(path.read_bytes() for folder in folders for path in folder.rglob('*') if path.is_file())


class ManagedPrepareTests(ManagedWorkerCase):
    def test_prepare_obtains_one_admission_on_the_gateway_route_before_writing_the_run(self):
        self.manage()
        calls, bodies = self.admissions(), self.prepared()
        run = self.api.prepare(self.runs, self.brief())
        [(assignment, result)] = calls
        self.assertEqual((result['status'], result['billing_class']), ('admitted', 'gateway'))
        self.assertEqual(assignment['role'], 'researcher')
        self.assertEqual(assignment['choice'], dict(provider='openrouter', model='vendor/model', effort='low',
                                                    harness='opencode provider openrouter', funding='api'))
        self.assertTrue(result['route'].startswith('openrouter:vendor/model@provider|'), result['route'])
        self.assertFalse({'workflow', 'observations'} & set(bodies[0]))
        self.assertEqual(sorted(bodies[0]['job']['tools']), sorted(READ_ONLY))
        retained = json.loads((run / 'brief.json').read_text())
        self.assertEqual(retained['routing']['attempt_id'], assignment['attempt_id'])
        self.assertEqual(self.routing_state(run), 'admitted')
        self.assertEqual(self.holds(), {'admitted': 1})
        self.assertEqual(list((self.runs / '.routing').iterdir()), [])  # the write-ahead entry is dropped

    def test_prepare_runs_before_the_openrouter_admission_with_the_seen_revision(self):
        self.manage()
        order, real = [], settings.admission

        def admission(*args, **kwargs):
            order.append(('admission', kwargs.get('revision')))
            return real(*args, **kwargs)
        self.prepared(then=lambda answer: order.append(('prepare', answer['state'], answer.get('seen'))))
        with patch.object(settings, 'admission', admission):
            self.api.prepare(self.runs, self.brief())
        revision = self.settings['revision']
        self.assertEqual(order, [('prepare', 'admitted', dict(openrouter_revision=revision)), ('admission', revision)])

    def test_a_stale_revision_fails_closed_and_closes_the_hold(self):
        self.manage()
        brief = self.brief()

        def change(answer):
            disabled = settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
            settings.enable(self.repo, dict(expected_revision=disabled['revision'], consent_version=2))
        self.prepared(then=change)
        events = self.recorded()
        with self.assertRaisesRegex(ValueError, 'openrouter_preparation_stale'):
            self.api.prepare(self.runs, brief)
        self.assertEqual([event['kind'] for event, _ in events], ['not_dispatched'])
        self.assertEqual(self.holds(), {})
        self.assertFalse((self.runs / brief['task_id']).exists())

    def test_local_refusals_are_checked_before_routing(self):
        self.manage()
        calls = self.admissions()
        brief = self.brief()
        for bad, reason in ((dict(brief, effort='extreme'), 'invalid_effort'),
                            (dict(brief, files=['README.md']), 'packet_changed')):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.api.prepare(self.runs, bad)
        self.api.prepare(self.runs, brief)
        with self.assertRaises(FileExistsError):
            self.api.prepare(self.runs, self.brief())  # the task id is already used
        disabled = settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
        with self.assertRaisesRegex(ValueError, 'openrouter_disabled'):
            self.api.prepare(self.runs, self.brief('pilot-2'))
        settings.enable(self.repo, dict(expected_revision=disabled['revision'], consent_version=2))
        self.assertEqual(len(calls), 1)  # only the one prepare that passed every local check reached routing

    def test_a_crash_after_prepare_leaves_a_write_ahead_entry_that_recovery_closes(self):
        self.manage()
        events = self.recorded()
        with patch.object(self.api, '_release', lambda *args, **kwargs: None), \
                patch.object(settings, 'admission', side_effect=KeyboardInterrupt):  # the process dies here
            with self.assertRaises(KeyboardInterrupt):
                self.api.prepare(self.runs, self.brief())
        [entry] = (self.runs / '.routing').iterdir()
        attempt = json.loads(entry.read_text())['attempt_id']
        self.assertEqual(self.holds(), {'admitted': 1})
        run = self.api.prepare(self.runs, self.brief('pilot-2'))
        self.assertIn((attempt, 'not_dispatched'), [(event['attempt_id'], event['kind']) for event, _ in events])
        self.assertEqual(list((self.runs / '.routing').iterdir()), [])
        self.assertEqual(self.holds(), {'admitted': 1})  # the new run's hold only
        self.assertTrue((run / 'brief.json').exists())

    def test_the_cli_prints_routings_fixed_refusal_reason(self):
        import contextlib
        import io
        import runpy
        self.manage()
        self.source(organization='unfunded-organization')
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(self.brief()).encode()))
        out = io.StringIO()
        argv = [str(SCRIPT), 'run', '--root', str(self.runs), '--opencode', str(self.executable),
                '--auth-data', str(self.root / 'auth')]
        with patch.object(sys, 'argv', argv), patch.object(sys, 'stdin', stdin), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit) as done:
            runpy.run_path(str(SCRIPT), run_name='__main__')
        self.assertEqual(done.exception.code, 1)
        self.assertEqual(json.loads(out.getvalue()), dict(error='routing_refused', reason='explicit_choice_unavailable'))
        self.assertFalse((self.runs / 'pilot-1').exists())
        self.assertEqual(self.holds(), {})

    def test_an_unmanaged_project_never_reaches_routing(self):
        with patch.object(command, 'managed_service', side_effect=AssertionError('routing reached')), \
                patch.object(self.api, '_server', side_effect=AssertionError('managed launcher reached')):
            run = self.api.prepare(self.runs, self.brief())
            self.execute(run)
        self.assertNotIn('routing', json.loads((run / 'brief.json').read_text()))
        self.assertTrue(self.marker.exists())  # the V1 launch is unchanged outside managed projects


class ManagedExecuteTests(ManagedWorkerCase):
    def test_the_v1_launch_is_refused_for_a_managed_project(self):
        run = self.api.prepare(self.runs, self.brief())  # prepared before the project was managed: a V1 run
        self.manage()
        calls = self.admissions()
        result = self.execute(run)
        self.assertEqual((result['state'], result['failure']),
                         ('failed', dict(category='integration', reason='legacy_route_retired')))
        self.assertEqual(routing_settings.LEGACY_ROUTE_RETIRED, 'legacy_route_retired')
        self.assertFalse(self.marker.exists())
        self.assertEqual(calls, [])

    def test_the_read_only_profile_is_kept(self):
        self.manage()
        self.qualified()
        self.serve(cost=0.000002)
        run = self.api.prepare(self.runs, self.brief())
        result = self.execute(run)
        self.assertEqual(result['state'], 'awaiting_verification', result)
        self.assertEqual(result['response'], 'Three lines\n')
        [host] = self.hosts
        self.assertEqual(host.directory, str(run / 'workspace'))  # the private copy, never the checkout
        self.assertEqual(host.created['location'], dict(directory=str(run / 'workspace')))
        rules = host.created['permissions']
        allowed = {(rule['action'], rule['resource']) for rule in rules if rule['effect'] == 'allow'}
        self.assertEqual(allowed, {('read', 'input.txt'), ('glob', '*'), ('grep', '*')})
        for action in ('read', 'edit', 'write', 'list', 'bash', 'shell', 'external_directory', 'webfetch',
                       'websearch', 'task', 'subagent', 'skill', 'execute', 'todowrite'):
            with self.subTest(action=action):
                self.assertEqual(launcher.rule_effect(rules, action), 'deny')
        self.assertFalse(self.marker.exists())  # the V1 runtime never ran
        self.assertEqual(self.holds(), {})
        verified = self.api.verify(run, 'accepted', ['Native line count'], reviewer='native-reviewer',
                                   native_review_ref='review')
        self.assertEqual(verified['state'], 'accepted')

    def test_a_v1_qualification_record_does_not_authorize_a_v2_editing_profile(self):
        """The V1 worker's qualified runtime and consent stay read-only: the V2 session never gets the launcher's
        editing profile, and they never stand in for the V2 shim's enforcement probe."""
        self.manage()
        self.assertEqual(self.settings['activation']['ref'].split(':')[0], 'activation')
        self.serve(denies=[])  # a host whose shim does not enforce the managed denies
        run = self.api.prepare(self.runs, self.brief())
        result = self.execute(run)
        self.assertEqual((result['state'], result['failure']['reason']), ('failed', 'profile_not_enforced'))
        self.assertFalse(hasattr(self.hosts[0], 'created'))
        self.assertEqual(self.holds(), {})
        self.assertEqual(launcher.rule_effect(self.api.session_rules(json.loads((run / 'brief.json').read_text())),
                                              'edit'), 'deny')
        self.assertEqual(launcher.rule_effect(launcher.WORKER_RULES, 'edit'), 'allow')  # never the worker's
        self.qualified()
        self.serve(cost=0.000002)
        run = self.api.prepare(self.runs, self.brief('pilot-2'))
        self.assertEqual(self.execute(run)['state'], 'awaiting_verification')
        self.assertNotEqual(self.hosts[-1].created['permissions'], launcher.WORKER_RULES)
        self.assertEqual(launcher.rule_effect(self.hosts[-1].created['permissions'], 'edit'), 'deny')

    def test_every_exit_before_dispatch_closes_the_hold(self):
        self.manage()

        def unhealthy():
            self.serve(health='down')

        def stale():
            self.serve()
            disabled = settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
            self.settings = settings.enable(self.repo, dict(expected_revision=disabled['revision'],
                                                            consent_version=2))

        def removed():
            self.serve()
            self.enterContext(patch.object(routing_settings, 'managed_project', lambda *args, **kwargs: False))
        cases = ((self.serve, 'api_funding_unqualified'), (unhealthy, 'host_unhealthy'),
                 (stale, 'openrouter_preparation_stale'), (removed, 'project_not_managed'))
        for number, (setup, reason) in enumerate(cases):
            with self.subTest(reason=reason):
                brief = self.brief('pilot-%d' % number)
                run = self.api.prepare(self.runs, brief)
                setup()
                result = self.execute(run)
                self.assertEqual((result['state'], result['failure']['reason']), ('failed', reason))
                self.assertFalse(any(hasattr(host, 'created') for host in self.hosts))
                self.assertEqual(self.holds(), {})
                self.assertEqual(self.routing_state(run), 'settled')
                self.assertNotIn(brief['objective'], json.dumps(result))  # no packet on a refusal path
                self.assertFalse(result['response'])

    def test_the_dollar_preflight_reruns_at_dispatch_and_closes_the_hold(self):
        self.manage()
        self.qualified()
        self.serve()
        run = self.api.prepare(self.runs, self.brief())
        real = command.Service._status

        def status(service, body):
            answer = real(service, body)
            answer['ledger']['reconcile_hint'] = 'reconcile_uncertain'
            return answer
        with patch.object(command.Service, '_status', status):
            result = self.execute(run)
        self.assertEqual((result['state'], result['failure']['reason']), ('failed', 'ledger_reconcile_pending'))
        self.assertFalse(hasattr(self.hosts[0], 'created'))
        self.assertEqual(self.holds(), {})

    def test_a_reported_cost_is_charged_the_full_reservation(self):
        self.manage()
        self.qualified()
        self.serve(cost=0.000002)
        calls, events = self.admissions(), self.recorded()
        self.execute(self.api.prepare(self.runs, self.brief()))
        [(_, answer)] = [item for item in events if item[0]['kind'] == 'complete']
        reserved = max(value for key, value in calls[-1][1]['reserved'].items() if key.startswith('org:'))
        settlement = answer['settlement']
        self.assertEqual((answer['outcome'], settlement['charged'], settlement['released']),
                         ('complete', reserved, 0))
        self.assertEqual(settlement['cost_source'], 'estimate')  # never recorded as a provider receipt
        self.assertEqual(self.holds(), {})

    def test_a_missing_cost_leaves_the_cost_unknown(self):
        self.manage()
        self.qualified()
        self.serve()  # tokens with a zero cost: OpenCode's figure is no evidence
        events = self.recorded()
        run = self.api.prepare(self.runs, self.brief())
        self.execute(run)
        [(_, answer)] = [item for item in events if item[0]['kind'] == 'complete']
        self.assertEqual(answer['outcome'], 'cost_unknown')
        self.assertEqual(self.holds(), {'cost_unknown': 1})
        self.assertEqual(self.routing_state(run), 'settled')
        with self.assertRaisesRegex(ValueError, 'ledger_reconcile_pending'):
            self.api.prepare(self.runs, self.brief('pilot-2'))

    def test_the_token_never_appears_in_output_or_files(self):
        self.manage()
        self.qualified()
        self.serve(cost=0.000002)
        run = self.api.prepare(self.runs, self.brief())
        result = self.execute(run)
        self.assertEqual(result['state'], 'awaiting_verification')
        tokens = {token for token in self.hosts[0].tokens_seen if token}
        self.assertEqual(len(tokens), 1)
        token = tokens.pop()
        self.assertNotIn(token, json.dumps(result))
        self.assertTrue(any(path.is_file() for path in run.rglob('*')))  # the run folder itself is scanned
        self.assertNotIn(token.encode(), self.files())

    def test_an_openrouter_model_id_is_accounted_and_settles(self):
        """F4 item 1: the adapter accepts `vendor/model`, so the message on the admitted OpenRouter id is accounted and
        the hold settles `complete` (before, the launch stayed `uncertain` with `launch_unconfirmed`)."""
        self.manage()
        self.qualified()
        self.serve(cost=0.000002, message_model=dict(providerID='openrouter', id='vendor/model', variant='low'))
        events = self.recorded()
        run = self.api.prepare(self.runs, self.brief())
        result = self.execute(run)
        self.assertEqual((result['state'], result['failure']), ('awaiting_verification', None), result)
        [(event, answer)] = [item for item in events if item[0]['kind'] == 'complete']
        self.assertEqual([request['model'] for request in event['usage']['requests']], ['vendor/model'])
        self.assertEqual(answer['outcome'], 'complete')
        self.assertEqual((self.routing_state(run), self.holds()), ('settled', {}))

    def test_a_kill_after_the_routing_dispatch_is_recovered_keeping_the_exposure(self):
        self.manage()
        self.qualified()
        self.serve(prompt_raises=KeyboardInterrupt())
        run = self.api.prepare(self.runs, self.brief())
        with patch.object(launcher, '_interrupted', lambda *args: None), \
                patch.object(self.api, '_settle_run', lambda *args, **kwargs: None):  # the process dies here
            self.assertEqual(self.execute(run)['state'], 'unresolved')
        self.assertEqual((self.routing_state(run), self.holds()), ('dispatched', {'dispatched': 1}))
        with self.assertRaisesRegex(ValueError, 'ledger_reconcile_pending'):  # recovery ran first, then the preflight
            self.api.prepare(self.runs, self.brief('pilot-2'))
        self.assertEqual((self.routing_state(run), self.holds()), ('settled', {'cost_unknown': 1}))
        with self.assertRaisesRegex(ValueError, 'ledger_reconcile_pending'):  # settling again changes nothing
            self.api.prepare(self.runs, self.brief('pilot-3'))
        self.assertEqual(self.holds(), {'cost_unknown': 1})

    def test_a_kill_during_the_routing_dispatch_is_released_by_recovery(self):
        self.manage()
        self.qualified()
        self.serve()
        run = self.api.prepare(self.runs, self.brief())
        with patch.object(launcher, '_interrupted', lambda *args: None), \
                patch.object(self.api, '_settle_run', lambda *args, **kwargs: None), \
                patch.object(command.Service, '_dispatch', side_effect=KeyboardInterrupt):
            self.assertEqual(self.execute(run)['state'], 'unresolved')
        self.assertEqual((self.routing_state(run), self.holds()), ('dispatching', {'admitted': 1}))
        self.api.prepare(self.runs, self.brief('pilot-2'))  # recovery releases the never-dispatched hold first
        self.assertEqual(self.routing_state(run), 'settled')
        self.assertEqual(self.holds(), {'admitted': 1})  # the new run's hold only


class ManagedSelfCheckTests(ManagedWorkerCase):
    def test_managed_self_check_admits_one_gateway_worker_and_refuses_the_v1_launch(self):
        outside = tempfile.TemporaryDirectory()  # self.tmp holds this class's patched default routing state
        self.addCleanup(outside.cleanup)
        self.assertEqual(self.api.managed_self_check(Path(outside.name).resolve() / 'self-check'), [])
        self.assertEqual(command._self_check('script', 'scripts/openrouter_workers.py'), [])

    def test_the_self_check_flag_refuses_an_unsafe_workdir(self):
        crowded = self.root / 'crowded'
        crowded.mkdir(mode=0o700)
        (crowded / 'file').write_text('x')
        for unsafe in (crowded, self.repo / 'inside-repository'):
            with self.subTest(workdir=unsafe):
                done = subprocess.run([sys.executable, '-I', '-B', str(SCRIPT), '--managed-self-check', str(unsafe)],
                                      capture_output=True, text=True, timeout=30)
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(json.loads(done.stdout), dict(codes=['self_check_workdir_unsafe']))
                self.assertEqual(self.api.managed_self_check(unsafe), ['self_check_workdir_unsafe'])
        self.assertFalse((self.repo / 'inside-repository').exists())
        self.assertEqual(sorted(path.name for path in crowded.iterdir()), ['file'])


if __name__ == '__main__':
    unittest.main()
