"""C3-1: the observation collector, its source seam and the route manifest (C.3 design 3.3, 3.4, 6.3, 6.4)."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

from runtime import openrouter_settings, routing, routing_observations as collector, usage_credentials
from scripts import routing as command

ROOT = Path(__file__).resolve().parents[1]
SCOPE = 'scope1'
M = 1_000_000
# A real-clock organization policy (tests/test_routing_command.py POLICY): its cycle spans the present.
POLICY = dict(cycle='c', start=0, end=2 * 10**10, expiry=2 * 10**10, stop_at=10**10, ceiling='none', amount=None,
              grant_amount=0, pilot_limit=None, task_limit=None)


def now():
    return int(time.time())


def sub_route(route='sub', status='qualified', **extra):
    value = dict(route=route, status=status, provider='openai', model='m', effort='high', harness='codex-native',
                 billing_class='included_subscription', pool='sub', account='sub-account', demand={'5h': 10},
                 binding_enforceable=True)
    value.update(extra)
    return value


def gateway_route(route='gw', integration_route='proposals', **extra):
    value = dict(route=route, status='qualified', provider='openrouter', model='model/v1', effort='none',
                 harness='openrouter-proposal', endpoint='p', billing_class='gateway', pool='gw',
                 account='gw-account', demand={'credit': M}, integration_route=integration_route)
    value.update(extra)
    return value


def sub_window(at, **extra):
    value = dict(id='5h', limit=1000, used=100, length=18000, reset_at=at + 3600, rolling=False, observed_at=at)
    value.update(extra)
    return value


def gateway_window(at, organization='or-org'):
    return dict(id='credit', limit=10**12, used=0, length=None, reset_at=None, rolling=False, observed_at=at,
                organization=organization)


def ready(route, scope):
    return dict(state='ready', scope=scope, ref='ref-' + route['route'])


class Refused(Exception):
    pass


def refuse(name):
    def call(*args, **kwargs):
        raise Refused(name)
    return call


class Stores(unittest.TestCase):
    """Temporary routing settings and ledger, both away from every default path."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.settings = self.root / 'routing' / 'settings.json'
        self.ledger_path = self.root / 'usage' / 'organizations.sqlite3'
        self.service = command.Service(self.settings, self.ledger_path)
        self.addCleanup(self.service.close)
        self.now = now()

    def call(self, request):
        return self.service.handle(json.dumps(request).encode())

    def fund(self, organization='or-org', observed_at=None):
        enabled = self.call(dict(v=1, op='set', organization=dict(id=organization, enabled=True, policy=POLICY,
                                                                   expected_revision=0)))
        self.assertEqual(enabled['state'], 'updated', enabled)
        self.service._ledger(False, write=True).observe(organization, 10 * M, observed_at or self.now,
                                                        issued=10 * M)

    def data(self):
        ledger = self.service._ledger(True, write=True)
        return ledger.snapshot()

    def collect(self, routes, windows, project=None):
        source = collector.StaticSource(routes, windows, ready)
        return collector.collect(str(project or self.root), dict(scope=SCOPE), settings_path=self.settings,
                                 ledger_path=self.ledger_path, source=source)


def excluded(result):
    return {item['route']: item['reason'] for item in result['observations']['excluded']}


def kept(result):
    return {route['route']: route for route in result['observations']['routes']}


class CollectorTests(Stores):
    def test_a_qualified_subscription_route_with_fresh_telemetry_is_a_candidate(self):
        result = self.collect([sub_route()], dict(sub=[sub_window(self.now)]))
        self.assertEqual(set(result), {'observations', 'seen'})
        self.assertEqual(result['seen'], dict(openrouter_revision=None))  # no gateway route needed OpenRouter
        observations = result['observations']
        self.assertLessEqual(abs(observations['now'] - self.now), 5)
        self.data()  # the ledger admit writes to
        route = kept(result)['sub']
        self.assertEqual((route['capable'], route['permitted'], route['binding_enforceable']), (True, True, True))
        self.assertNotIn('status', route)
        window = observations['pools']['sub']['windows'][0]
        self.assertIs(window['fresh'], True)
        self.assertNotIn('observed_at', window)
        ranking = dict(strategy_ids=[], reason_codes=[])
        settings = dict(routing.routing_settings.resolve({}), revision=0)
        assignment = dict(attempt_id='a1', task='t1', role='implementer', complexity_band='medium', scope=SCOPE,
                          choice=dict(provider='openai', model='m', effort='high', harness='codex-native',
                                      funding='subscription'))
        admitted = routing.admit(assignment, ranking, settings, observations, ledger=self.service._ledger(False, True))
        self.assertEqual(admitted['status'], 'admitted', admitted)

    def test_an_unknown_unqualified_or_malformed_manifest_route_is_excluded(self):
        routes = [sub_route('unverified', status='unverified'), sub_route('unsupported', status='unsupported'),
                  sub_route('nostatus', status=None), sub_route('purchased', billing_class='purchased_usage'),
                  dict(route='broken', status='qualified'), sub_route('ok')]
        result = self.collect(routes, dict(sub=[sub_window(self.now)]))
        self.assertEqual(excluded(result), dict(unverified='route_unqualified', unsupported='route_unqualified',
                                                nostatus='route_unqualified', purchased='billing_class_excluded',
                                                broken='route_invalid'))
        self.assertEqual(set(kept(result)), {'ok'})

    def test_missing_or_old_window_telemetry_is_never_fresh(self):
        result = self.collect([sub_route('old'), sub_route('none', pool='other')],
                              dict(sub=[sub_window(self.now - collector.FRESH_SECONDS - 1)]))
        self.assertIs(result['observations']['pools']['sub']['windows'][0]['fresh'], False)
        self.assertEqual(excluded(result), dict(none='window_unobserved'))  # enabled, but blocked with a reason

    def test_a_not_entitled_route_stays_excluded_until_cleared(self):
        windows = dict(sub=[sub_window(self.now)])
        collector.mark_not_entitled(self.settings, 'sub', 'sub-account', self.now)
        for _ in range(2):  # it stays: a later snapshot never forgets the mark by itself
            self.assertEqual(excluded(self.collect([sub_route()], windows)), dict(sub='not_entitled'))
        self.assertEqual(oct(collector.entitlement_path(self.settings).stat().st_mode & 0o777), '0o600')
        self.assertFalse(collector.clear_not_entitled(self.settings, 'sub', 'another-account'))
        self.assertEqual(excluded(self.collect([sub_route()], windows)), dict(sub='not_entitled'))
        self.assertTrue(collector.clear_not_entitled(self.settings, 'sub', 'sub-account'))
        self.assertEqual(excluded(self.collect([sub_route()], windows)), {})

    def test_an_unreadable_entitlement_record_excludes_every_route(self):
        path = collector.entitlement_path(self.settings)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text('{not json')
        path.chmod(0o600)
        self.assertEqual(excluded(self.collect([sub_route()], dict(sub=[sub_window(self.now)]))),
                         dict(sub='entitlement_unavailable'))

    def test_the_collector_reads_no_secret_and_makes_no_network_call(self):
        self.fund()
        report = dict(enabled=True, routes=['proposals'], local_configuration='ready', revision=4)
        with ExitStack() as stack:
            for target, name in ((usage_credentials, 'resolve_key'), (usage_credentials, '_read_key'),
                                 (socket.socket, 'connect'), (socket, 'create_connection'),
                                 (urllib.request, 'urlopen'), (subprocess, 'Popen'),
                                 (routing, 'ensure_profile'), (routing, 'rank'), (routing, 'admit')):
                stack.enter_context(mock.patch.object(target, name, refuse(name)))
            stack.enter_context(mock.patch.object(openrouter_settings, 'status', lambda cwd: dict(report)))
            result = self.collect([sub_route(), gateway_route()],
                                  dict(sub=[sub_window(self.now)], gw=[gateway_window(self.now)]))
        self.assertEqual(set(kept(result)), {'sub', 'gw'})
        self.assertEqual(kept(result)['gw']['credential'], dict(state='ready', scope=SCOPE, ref='ref-gw'))
        self.assertEqual(result['seen'], dict(openrouter_revision=4))

    def test_the_local_source_reads_the_manifest_and_private_window_store(self):
        collector.store_window(self.settings, 'sub', sub_window(self.now))
        collector.store_window(self.settings, 'sub', sub_window(self.now - 10, used=900))  # older: ignored
        path = collector.window_path(self.settings)
        self.assertEqual(path.parent, self.settings.parent)
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        source = collector.default_source(self.settings)
        self.assertEqual([window['used'] for window in source.windows('sub', self.now)], [100])
        self.assertIsNone(source.credential(gateway_route(), SCOPE))  # a secret is never resolved
        shipped = source.manifest()
        self.assertTrue(shipped)
        self.assertFalse([route for route in shipped if route.get('status') == 'qualified'])
        with self.assertRaises(ValueError):
            collector.store_window(self.settings, 'sub', dict(sub_window(self.now), limit=0))

    def test_the_shipped_manifest_qualifies_no_route_until_user_decision_5(self):
        manifest = json.loads(collector.MANIFEST.read_text())
        self.assertEqual(manifest['decision'], 'pending: C.3 design 7.1 user decision 5')
        self.assertEqual(collector.manifest_check(), ['route_manifest_unqualified'])
        result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                   ledger_path=self.ledger_path)
        self.assertEqual(result['observations']['routes'], [])
        self.assertTrue(result['observations']['excluded'])
        qualified = collector.StaticSource([sub_route()], {}, ready)
        self.assertEqual(collector.manifest_check(qualified), [])

    def test_no_request_field_or_setting_selects_the_source(self):
        """Only code replaces `default_source` (C.3 design 3.3): `collect` takes no request input naming one."""
        seen = []
        with mock.patch.object(collector, 'default_source',
                               lambda settings_path=None: seen.append(settings_path) or
                               collector.StaticSource([sub_route()], dict(sub=[sub_window(self.now)]), ready)):
            result = collector.collect(str(self.root), dict(scope=SCOPE, source='local'),
                                       settings_path=self.settings, ledger_path=self.ledger_path)
        self.assertEqual(seen, [self.settings])
        self.assertEqual(set(kept(result)), {'sub'})


    def test_only_the_given_stores_are_read_and_never_the_defaults(self):
        """Coordinator decision (C3-0b review): the collector reads the service's own settings and ledger paths, so
        the two can never disagree; a default path is never opened."""
        self.fund()
        collector.store_window(self.settings, 'gw', gateway_window(self.now))
        report = dict(enabled=True, routes=['proposals'], local_configuration='ready', revision=2)
        local = collector.default_source(self.settings)
        source = collector.StaticSource([gateway_route()], local.windows, ready)
        from runtime import usage_settings
        with mock.patch.object(command, 'default_settings', refuse('default_settings')), \
                mock.patch.object(command, 'default_ledger', refuse('default_ledger')), \
                mock.patch.object(usage_settings, 'directory', refuse('usage_settings.directory')), \
                mock.patch.object(openrouter_settings, 'status', lambda cwd: dict(report)):
            result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                       ledger_path=self.ledger_path, source=source)
            elsewhere = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.root / 'other' / 's.json',
                                          ledger_path=self.root / 'other' / 'l.sqlite3', source=source)
        self.assertEqual(kept(result)['gw']['funding_verified'], True)  # this ledger's organization, this window
        self.assertEqual(excluded(elsewhere), dict(gw='funding_unverified'))  # another ledger funds nothing
        self.assertEqual(collector.default_source(self.root / 'other' / 's.json').windows('gw', self.now), [])
        with self.assertRaises(TypeError):
            collector.collect(str(self.root), dict(scope=SCOPE), source=source)  # both paths are required


class GatewayDerivationTests(Stores):
    """Round 5: `integration_enabled` from the real `openrouter_settings.status`, `funding_verified` from the ledger,
    never from the source; the collector never takes the admission lock."""

    def setUp(self):
        super().setUp()
        from tests.openrouter_fixtures import configure, enroll
        self.repo = enroll(self).repository_root
        configure(self.repo, routes=('proposals',))
        self.windows = dict(gw=[gateway_window(self.now)])

    def gateway(self, **extra):
        # A source that claims every gate is open: the collector must ignore it.
        return gateway_route(integration_enabled=True, funding_verified=True, **extra)

    def test_real_settings_and_ledger_derive_both_gates(self):
        self.fund()
        status = openrouter_settings.status(self.repo)
        result = self.collect([self.gateway()], self.windows, project=self.repo)
        route = kept(result)['gw']
        self.assertEqual((route['integration_enabled'], route['funding_verified']), (True, True))
        window = result['observations']['pools']['gw']['windows'][0]
        self.assertEqual((window['organization'], window['organization_revision']), ('or-org', 1))
        self.assertEqual(result['observations']['pools']['gw']['billing_class'], 'gateway')
        self.assertEqual(result['seen'], dict(openrouter_revision=status['revision']))
        # The host's own route must be enabled: a worker route on a proposals-only setting is not.
        self.assertEqual(excluded(self.collect([self.gateway(route='w', integration_route='workers')], self.windows,
                                               project=self.repo)), dict(w='integration_disabled'))
        openrouter_settings.disable(self.repo, dict(expected_revision=status['revision']))
        self.assertEqual(excluded(self.collect([self.gateway()], self.windows, project=self.repo)),
                         dict(gw='integration_disabled'))

    def test_a_disabled_or_unfunded_organization_is_funding_unverified(self):
        self.assertEqual(excluded(self.collect([self.gateway()], self.windows, project=self.repo)),
                         dict(gw='funding_unverified'))  # no organization in the ledger at all
        self.fund()
        self.call(dict(v=1, op='set', organization=dict(id='or-org', enabled=False)))
        self.assertEqual(excluded(self.collect([self.gateway()], self.windows, project=self.repo)),
                         dict(gw='funding_unverified'))

    def test_a_stale_balance_is_funding_unverified(self):
        self.fund(observed_at=self.now - collector.FRESH_SECONDS - 1)
        self.assertEqual(excluded(self.collect([self.gateway()], self.windows, project=self.repo)),
                         dict(gw='funding_unverified'))

    def test_the_collector_never_takes_or_waits_for_the_admission_lock(self):
        self.fund()
        status = mock.Mock(wraps=openrouter_settings.status)
        with openrouter_settings.admission(self.repo, 'proposals'):  # another handle holds the lock
            admission = mock.Mock(side_effect=Refused('admission'))
            with mock.patch.object(openrouter_settings, 'admission', admission), \
                    mock.patch.object(openrouter_settings, 'status', status):
                started = time.monotonic()
                result = self.collect([self.gateway(), sub_route()],
                                      dict(self.windows, sub=[sub_window(self.now)]), project=self.repo)
                elapsed = time.monotonic() - started
        self.assertEqual(excluded(result), dict(gw='openrouter_operation_busy'))
        self.assertEqual(set(kept(result)), {'sub'})  # subscription routes are unaffected
        self.assertEqual(result['seen'], dict(openrouter_revision=None))
        self.assertEqual((admission.call_count, status.call_count), (0, 1))  # no retry loop
        self.assertLess(elapsed, 5)


class ManagedFixtureHomeTests(unittest.TestCase):
    """C.3 design 6.4 A4 rule 3: the four managed-fixture contracts, under plain unittest with a fresh HOME and no
    test_sharded wrapper, leave HOME empty."""

    CONTRACTS = ('C2bNativeDelegationThroughRouting.test_native_worker_admission_calls_routing_prepare_once',
                 'C2bNativeDelegationThroughRouting.test_native_worker_admission_is_bound_to_its_workflow_job',
                 'C2cProposalThroughRouting.test_proposal_begin_obtains_one_routing_admission',
                 'C2cWorkerThroughRouting.test_worker_prepare_obtains_one_routing_admission')

    def test_the_managed_contracts_leave_a_fresh_home_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            home.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if key not in ('HOME', 'CODEX_HOME') and not key.startswith(('XDG_', 'PYTHON'))}
            env['HOME'] = str(home)
            names = ['tests.test_smart_routing_features.' + name for name in self.CONTRACTS]
            # Plain unittest: `-I` keeps the checkout off sys.path, so only the package root is put back.
            runner = 'import sys, unittest; sys.path.insert(0, sys.argv[1]); unittest.main(module=None, argv=sys.argv[1:])'
            done = subprocess.run([sys.executable, '-I', '-B', '-c', runner, str(ROOT), *names], cwd=temp, env=env,
                                  capture_output=True, timeout=600)
            output = done.stderr.decode()[-3000:]
            self.assertEqual(done.returncode, 0, output)
            self.assertIn('Ran 4 tests', output)
            self.assertEqual(sorted(str(path.relative_to(home)) for path in home.rglob('*')), [], output)


if __name__ == '__main__':
    unittest.main()
