"""C3-1: the host-neutral launcher core (C.3 design 3.1 steps 1-6, 3.4, 3.6, 4.2 self-check)."""
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from runtime import routing, routing_launch as launcher, routing_observations as collector, routing_settings, state
from scripts import routing as command
from tests.test_routing_observations import SCOPE, Stores, ready, sub_route, sub_window

TOKEN_FIELDS = ('authority',)
IDENTITY = dict(provider='openai', model='m', effort='high', harness='codex-native')


def status(**ledger):
    value = dict(authority_required=True, unstamped_open_holds=0, blocked_organizations=[],
                 uncertain_dollar=dict(ids=[], more=0), cost_unknown=dict(ids=[], more=0), reconcile_hint=None)
    value.update(ledger)
    return dict(v=1, state='ok', settings=dict(smart_routing=True), settings_state='ok', project_pinned=True,
                ledger=dict(state='ready', **value))


# C3-0b's routing service collects for a managed project itself; before it lands, ManagedStub stands in for it.
C3_0B = callable(getattr(command, 'managed_service', None))


class ManagedStub(command.Service):
    """Test-only stand-in for C3-0b's managed-project `prepare` (C.3 design 3.3): the service, never the caller,
    collects observations, refuses caller ones and echoes `seen` unchanged. With C3-0b present it is the real one."""

    def handle(self, raw, op=None):
        value = json.loads(raw)
        if C3_0B or value.get('op') != 'prepare':
            return super().handle(raw, op)
        if 'observations' in value:
            return dict(v=1, state='blocked', reason='observations_not_accepted')
        collected = collector.collect(str(self.project), value['assignment'], settings_path=self.settings_path,
                                      ledger_path=self.ledger_path)
        response = super().handle(json.dumps(dict(value, observations=collected['observations'])).encode(), op)
        return dict(response, seen=collected['seen'])


class Reconciling(ManagedStub):
    """A ledger whose `status` asks for operator reconciliation of dollar holds."""

    def handle(self, raw, op=None):
        response = super().handle(raw, op)
        if json.loads(raw).get('op') == 'status' and type(response.get('ledger')) is dict:
            response['ledger']['reconcile_hint'] = 'use reconcile_uncertain'
        return response


class Transport:
    """A host double: records what it was given and answers with the host's re-read outcome."""

    def __init__(self, outcome=None, error=None):
        self.calls, self.outcome, self.error = [], outcome, error

    def __call__(self, route, *, authority, session_id, message_id):
        self.calls.append(dict(route=route, authority=authority, session_id=session_id, message_id=message_id))
        if self.error is not None:
            raise self.error
        return self.outcome if self.outcome is not None else dict(kind='complete', usage=usage())


def usage():
    tokens = dict(input=10, output=5, reasoning=0, cache_read=0, cache_write=0)
    return dict(source='host_message_totals', requests=[dict(kind='primary', model='m', tokens=tokens)])


def job(role='worker'):
    return launcher.unplanned_job('implementer' if role == 'worker' else 'reviewer', SCOPE, 'Do the fixture task')


class IdentityTests(unittest.TestCase):
    def test_route_identity_maps_each_host(self):
        self.assertEqual(launcher.route_identity('codex_native', model='gpt', effort='high', extra='ignored'),
                         dict(provider='openai', model='gpt', effort='high', harness='codex-native'))
        self.assertEqual(launcher.route_identity('openrouter_proposal', model='model/v1', provider='p', effort=None),
                         dict(provider='openrouter', model='model/v1', effort='none', harness='openrouter-proposal'))
        self.assertEqual(launcher.route_identity('openrouter_worker', model='v/m', provider='p', effort='low'),
                         dict(provider='openrouter', model='v/m', effort='low', harness='opencode'))
        self.assertEqual(launcher.route_identity('opencode', provider='claude-agent', model='haiku'),
                         dict(provider='claude-agent', model='haiku', effort='none', harness='opencode'))
        self.assertEqual(launcher.route_identity('api_usage', model='gpt', effort='low'),
                         dict(provider='openai', model='gpt', effort='low', harness='api'))
        self.assertEqual(launcher.pinned_endpoint('openrouter_worker', provider='p'), 'p')
        self.assertIsNone(launcher.pinned_endpoint('codex_native', provider='p'))
        for host, fields in (('unknown', dict(model='m')), ('codex_native', {}), ('opencode', dict(model='m'))):
            with self.subTest(host=host), self.assertRaises(ValueError):
                launcher.route_identity(host, **fields)

    def test_choice_is_the_one_assignment_shape_admit_accepts(self):
        choice = launcher.choice('openrouter_proposal', 'api', model='model/v1', provider='p', effort=None)
        self.assertEqual(choice, dict(provider='openrouter', model='model/v1', effort='none',
                                      harness='openrouter-proposal', funding='api'))
        assignment = dict(attempt_id='a', task='t', role='researcher', complexity_band='high', scope=SCOPE, choice=choice)
        self.assertTrue(routing._valid_assignment(assignment))
        with self.assertRaises(ValueError):
            launcher.choice('codex_native', 'gateway', model='m')

    def test_assignment_scope_is_stable_bounded_and_carries_no_path(self):
        with tempfile.TemporaryDirectory() as temp:
            scope = launcher.assignment_scope(temp)
            self.assertEqual(scope, launcher.assignment_scope(Path(temp) / '.'))
            self.assertLessEqual(len(scope), 256)
            self.assertNotIn(Path(temp).name, scope)

    def test_unplanned_job_is_a_conservative_digest_only_profile(self):
        value = launcher.unplanned_job('researcher', SCOPE, 'Secret objective text')
        self.assertNotIn('Secret objective text', json.dumps(value))
        profile = routing.ensure_profile(value)
        self.assertEqual((profile['role'], profile['complexity_band'], profile['risk_band'], profile['review_policy']),
                         ('worker', 'high', 'high', 'independent'))
        self.assertEqual(routing_settings.assignment_role(profile['role'], 'researcher'), 'researcher')
        self.assertEqual(launcher.unplanned_job('reviewer', SCOPE, 'x')['role'], 'review')
        with self.assertRaises(ValueError):
            launcher.unplanned_job('coordinator', SCOPE, 'x')


class PreflightTests(unittest.TestCase):
    def test_every_preflight_item_is_refused(self):
        cases = dict(project_required=(status() | dict(project_pinned=False), False),
                     authority_not_required=(status(authority_required=False), False),
                     unstamped_holds_open=(status(unstamped_open_holds=2), False))
        for reason, (value, dollar) in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(launcher.preflight(value, dollar=dollar), reason)
        absent = dict(status(), ledger=dict(state='absent'))
        self.assertEqual(launcher.preflight(absent), 'authority_not_required')
        self.assertEqual(launcher.preflight(dict(v=1, state='blocked', reason='ledger_busy')), 'ledger_busy')
        dollar_cases = dict(blocked_organizations=[dict(id='o', reasons={})], uncertain_dollar=dict(ids=['a'], more=0),
                            cost_unknown=dict(ids=[], more=1), reconcile_hint='use reconcile_uncertain')
        for key, value in dollar_cases.items():
            with self.subTest(dollar=key):
                self.assertEqual(launcher.preflight(status(**{key: value}), dollar=True), 'ledger_reconcile_pending')
                self.assertIsNone(launcher.preflight(status(**{key: value}), dollar=False))  # subscription proceeds
        self.assertIsNone(launcher.preflight(status(), dollar=True))

    def test_recovery_hints_name_the_operator_op_and_never_run_it(self):
        value = status(uncertain_dollar=dict(ids=['a'], more=0), cost_unknown=dict(ids=['b'], more=0),
                       blocked_organizations=[dict(id='o', reasons=dict(overrun={}, over_bound={}))])
        self.assertEqual(launcher.recovery_hints(value),
                         ['reconcile_uncertain', 'resolve_cost_unknown', 'resolve_overrun', 'resolve_block'])
        self.assertEqual(launcher.recovery_hints(status()), [])

    def test_readiness_reports_enabled_and_ready_separately_without_inference(self):
        with mock.patch.object(routing, 'ensure_profile', side_effect=AssertionError('inference')), \
                mock.patch.object(routing, 'admit', side_effect=AssertionError('admission')):
            waiting = launcher.readiness(status(authority_required=False))
            off = launcher.readiness(dict(status(), settings=dict(smart_routing=False)))
            fine = launcher.readiness(status())
            unknown = launcher.readiness(dict(status(), settings=None, settings_state='unavailable'))
        self.assertEqual(waiting, dict(enabled=True, ready=False, reason='authority_not_required'))
        self.assertEqual(off, dict(enabled=False, ready=True, reason=None))  # OFF still has worker_choices
        self.assertEqual(fine, dict(enabled=True, ready=True, reason=None))
        self.assertEqual(unknown, dict(enabled=None, ready=False, reason='settings_unavailable'))


class RequestTests(unittest.TestCase):
    def test_a_planned_job_without_workflow_is_refused(self):
        assignment = dict(attempt_id='a', task='t', scope=SCOPE)
        with self.assertRaises(launcher.Refused) as caught:
            launcher.prepare_request(assignment, job(), planned=True)
        self.assertEqual(caught.exception.reason, 'workflow_required')
        locator = dict(client='codex', cwd='/work/repo', session_id='s', task_id='t', job_id='j', attempt_id='a')
        bound = launcher.prepare_request(assignment, job(), workflow=locator, planned=True)
        self.assertEqual(bound['workflow'], locator)
        explicit = launcher.prepare_request(assignment, job())
        self.assertEqual(set(explicit), {'v', 'op', 'assignment', 'job'})  # never observations

    def test_the_job_is_canonical_and_never_widened(self):
        with self.assertRaises(launcher.Refused) as caught:
            launcher.prepare_request(dict(attempt_id='a', task='t', scope=SCOPE), None)
        self.assertEqual(caught.exception.reason, 'profile_unavailable')
        canonical = job()
        request = launcher.prepare_request(dict(attempt_id='a', task='t', scope=SCOPE), canonical, profile=dict(
            complexity_band='critical'))
        self.assertEqual(request['job'], dict(canonical, profile=dict(complexity_band='critical')))
        self.assertNotIn('profile', canonical)  # the caller's canonical job is never changed in place
        self.assertNotIn('observations', request)


class LaunchTests(Stores):
    def setUp(self):
        super().setUp()
        self.checkout = (self.root / 'checkout')
        self.checkout.mkdir()
        self.service.close()
        self.service = ManagedStub(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(self.service.close)
        self.source = collector.StaticSource([sub_route()], dict(sub=[sub_window(self.now)]), ready)
        self.patches = [mock.patch.object(collector, 'default_source', lambda settings_path=None: self.source),
                        mock.patch.object(routing_settings, 'managed_project', create=True,
                                          new=self.managed_project)]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=True))['state'], 'updated')

    def managed_project(self, path, settings_path=None):
        self.assertEqual(settings_path, self.service.settings_path)  # the service's own settings, never a default
        return Path(path).resolve() == self.checkout.resolve()

    def launch(self, service=None, catalog=(IDENTITY,), transport=None, **options):
        self.transport = transport or Transport()
        options.setdefault('assignment', dict(attempt_id='a1', task='task-a1', scope=SCOPE,
                                              choice=dict(IDENTITY, funding='subscription')))
        options.setdefault('job', job())
        return launcher.launch(service or self.service, host='codex_native', catalog=list(catalog),
                               transport=self.transport, settings_path=self.settings, **options)

    def holds(self):
        return self.service._ledger(False).snapshot()['holds']

    def test_a_managed_launch_admits_through_the_collector_and_never_exposes_the_token(self):
        result = self.launch()
        self.assertEqual(result['state'], 'recorded', result)
        self.assertEqual(result['admission']['route'], 'sub')
        self.assertEqual(len(self.transport.calls), 1)
        token = self.transport.calls[0]['authority']
        self.assertTrue(token)
        self.assertNotIn(token, json.dumps(result))
        self.assertEqual(self.transport.calls[0]['route']['funding_pool'], 'sub')
        self.assertNotIn(token, self.ledger_path.read_bytes().decode('latin-1'))

    def test_an_unpinned_service_is_refused_before_any_call(self):
        unpinned = mock.Mock(project=None)
        result = self.launch(service=unpinned)
        self.assertEqual(result, dict(state='blocked', reason='project_required'))
        self.assertEqual(unpinned.handle.call_count, 0)

    def test_preflight_and_unmanaged_projects_stop_before_prepare(self):
        other = ManagedStub(self.settings, self.ledger_path, project=self.root)  # pinned, not managed
        self.addCleanup(other.close)
        self.assertEqual(self.launch(service=other), dict(state='unmanaged', reason='project_not_managed'))
        fresh = self.root / 'fresh'  # stores where the one-way authority switch was never set
        unauthorized = ManagedStub(fresh / 'routing' / 'settings.json', fresh / 'usage' / 'organizations.sqlite3',
                                   project=self.checkout)
        self.addCleanup(unauthorized.close)
        self.assertEqual(self.launch(service=unauthorized), dict(state='blocked', reason='authority_not_required'))
        self.assertEqual(self.transport.calls, [])

    def test_a_catalog_mismatch_closes_the_hold_and_sends_nothing(self):
        result = self.launch(catalog=[dict(IDENTITY, model='other')])
        self.assertEqual((result['state'], result['reason']), ('blocked', 'catalog_mismatch'))
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.holds()['a1']['state'], 'released')  # `not_dispatched` closed the admitted hold

    def test_an_advisory_host_never_gets_a_dollar_route(self):
        admission = dict(route='gw', billing_class='gateway', route_identity=routing.route_identity(*IDENTITY.values()))
        self.assertEqual(launcher.admitted_check(admission, host='codex_native', catalog=[IDENTITY]),
                         (None, 'dollar_route_needs_enforced_host'))
        facts, reason = launcher.admitted_check(admission, host='openrouter_proposal', catalog=[IDENTITY])
        self.assertIsNone(reason)
        self.assertEqual(facts['model'], 'm')

    def test_a_not_entitled_route_is_refused_by_the_launcher(self):
        prepared = self.launch(transport=Transport(error=RuntimeError('never reached')), catalog=[])
        self.assertEqual(prepared['reason'], 'catalog_mismatch')  # admitted, then closed
        collector.mark_not_entitled(self.settings, 'sub', 'sub-account', self.now)
        admission = dict(route='sub', billing_class='included_subscription',
                         route_identity=routing.route_identity(*IDENTITY.values()))
        self.assertEqual(launcher.admitted_check(admission, host='codex_native', catalog=[IDENTITY],
                                                 settings_path=self.settings, source=self.source),
                         (None, 'route_not_entitled'))
        unlisted = dict(admission, route='not-in-manifest')
        self.assertEqual(launcher.admitted_check(unlisted, host='codex_native', catalog=[IDENTITY],
                                                 settings_path=self.settings, source=self.source),
                         (None, 'route_not_entitled'))

    def test_a_dollar_admission_always_runs_the_dollar_preflight(self):
        """S4: the dollar checks follow the admitted billing class; no caller flag can skip them."""
        from runtime import openrouter_settings
        from tests.test_routing_observations import gateway_route, gateway_window
        self.assertNotIn('dollar', inspect.signature(launcher.launch).parameters)
        self.fund()
        self.source = collector.StaticSource([gateway_route(), sub_route()],
                                             dict(gw=[gateway_window(self.now)], sub=[sub_window(self.now)]), ready)
        report = dict(enabled=True, routes=['proposals'], local_configuration='ready', revision=1)
        gateway = launcher.route_identity('openrouter_proposal', model='model/v1', provider='p', effort=None)
        service = Reconciling(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(service.close)
        with mock.patch.object(openrouter_settings, 'status', lambda cwd: dict(report)):
            refused = launcher.launch(service, host='openrouter_proposal', catalog=[gateway], transport=Transport(),
                                      assignment=dict(attempt_id='g1', task='task-g1', scope=SCOPE,
                                                      choice=dict(gateway, funding='api')),
                                      job=launcher.unplanned_job('researcher', SCOPE, 'proposal'))
        self.assertEqual(refused, dict(state='blocked', reason='ledger_reconcile_pending', attempt_id='g1'))
        self.assertEqual(self.holds()['g1']['state'], 'released')  # the admitted dollar hold sent nothing
        self.assertEqual(self.launch(service=service)['state'], 'recorded')  # a subscription admission proceeds

    def test_a_pending_assignment_holds_nothing_and_never_falls_back(self):
        self.source = collector.StaticSource([sub_route()], {}, ready)  # no telemetry yet
        result = self.launch()
        self.assertEqual(result['state'], 'pending', result)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.holds(), {})

    def test_a_lost_acknowledgment_records_uncertain_and_names_the_operator_op(self):
        result = self.launch(transport=Transport(error=TimeoutError('no acknowledgment')))
        self.assertEqual(result, dict(state='uncertain', attempt_id='a1', recovery='reconcile_subscription'))
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')


class SelfCheckTests(unittest.TestCase):
    """C.3 design 4.2: the gate runs the check in a child started with sys.executable and the package root first on
    its import path, with exactly PATH, HOME, TMPDIR and LANG, a 10 s budget and its own process group."""

    def run_check(self):
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            workdir.chmod(0o700)
            for name in ('home', 'tmp'):
                (workdir / name).mkdir(mode=0o700)
            code = ('import json, sys; sys.path.insert(0, sys.argv[1]); from runtime import routing_launch; '
                    'print(json.dumps(routing_launch.managed_self_check(sys.argv[2])))')
            env = dict(PATH='/usr/bin:/bin', HOME=str(workdir / 'home'), TMPDIR=str(workdir / 'tmp'), LANG='C.UTF-8')
            started = time.monotonic()
            done = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(state.PACKAGE), str(workdir / 'check')],
                                  env=env, cwd=workdir, capture_output=True, timeout=10, start_new_session=True)
            elapsed = time.monotonic() - started
            home = sorted(str(path) for path in (workdir / 'home').rglob('*'))
        return done, elapsed, home

    def test_the_self_check_runs_in_the_gates_child_process(self):
        done, elapsed, home = self.run_check()
        self.assertEqual(done.returncode, 0, done.stderr.decode()[-2000:])
        codes = json.loads(done.stdout)
        self.assertTrue(type(codes) is list and all(type(code) is str for code in codes), codes)
        if hasattr(routing_settings, 'managed_project') and hasattr(command, 'managed_service'):
            self.assertEqual(codes, [])  # C3-0b present: the whole managed path holds
        else:
            self.assertEqual(codes, ['managed_projects_unavailable'])  # fails closed until C3-0b lands
        self.assertEqual(home, [])
        self.assertLess(elapsed, 10)

    def test_the_self_check_proves_the_managed_path_with_c3_0b_stood_in(self):
        """The same check body in-process, as its child entry point runs it, with the stand-in for C3-0b."""
        with tempfile.TemporaryDirectory() as temp, \
                mock.patch.object(launcher, '_SELF_CHECK_CHILD', True), \
                mock.patch.object(command, 'Service', ManagedStub), \
                mock.patch.object(routing_settings, 'managed_project', create=True,
                                  new=lambda path, settings_path=None: False):
            self.assertEqual(launcher._self_check_in_child(Path(temp) / 'check'), [])

    def test_the_self_check_body_refuses_to_run_outside_its_child(self):
        """S5: the body swaps module globals, so a long-lived process can never run it; the public entry point runs
        it in a child process of its own and leaves the caller's globals untouched."""
        predicate = getattr(routing_settings, 'managed_project', None)
        source = collector.default_source
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(launcher._self_check_in_child(Path(temp) / 'body'), ['self_check_not_in_child'])
            self.assertFalse((Path(temp) / 'body').exists())  # refused before it created anything
            with mock.patch.object(launcher, '_self_check_in_child', side_effect=AssertionError('ran in-process')):
                codes = launcher.managed_self_check(Path(temp) / 'check')
        self.assertTrue(type(codes) is list and codes != ['self_check_not_in_child'], codes)
        self.assertIs(getattr(routing_settings, 'managed_project', None), predicate)
        self.assertIs(collector.default_source, source)
        self.assertIs(launcher._SELF_CHECK_CHILD, False)

    def test_the_self_check_refuses_a_workdir_it_did_not_create(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'check').mkdir()
            (Path(temp) / 'check' / 'file').write_text('x')
            self.assertEqual(launcher.managed_self_check(Path(temp) / 'check'), ['self_check_workdir_unsafe'])


if __name__ == '__main__':
    unittest.main()
