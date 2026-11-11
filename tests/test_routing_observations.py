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

from runtime import openrouter_settings, routing, routing_observations as collector, subscription_billing, \
    usage_credentials
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
                 binding_enforceable=True, window_unit=collector.WINDOW_UNIT)
    value.update(extra)
    return value


def gateway_route(route='gw', integration_route='proposals', **extra):
    value = dict(route=route, status='qualified', provider='openrouter', model='model/v1', effort='none',
                 harness='openrouter-proposal', endpoint='p', billing_class='gateway', pool='gw',
                 account='gw-account', demand={'credit': M}, integration_route=integration_route)
    value.update(extra)
    return value


def sub_window(at, **extra):
    value = dict(id='5h', limit=1000, used=100, length=18000, reset_at=at + 3600, rolling=False, observed_at=at,
                 unit=collector.WINDOW_UNIT)
    value.update(extra)
    return value


def gateway_window(at, organization='or-org'):
    return dict(id='credit', limit=10**12, used=0, length=None, reset_at=None, rolling=False, observed_at=at,
                organization=organization)


def ready(route, scope):
    return dict(state='ready', scope=scope, ref='ref-' + route['route'])


def choice_of(route, **extra):
    """The explicit choice that selects a fixed route by identity and funding (never by its id)."""
    return dict({key: route[key] for key in routing.ROUTE_IDENTITY},
                funding=routing.FUNDING_OF.get(route.get('billing_class'), 'subscription'), **extra)


def request_of(routes):
    """F4's request interface for fixed routes: one choice per well-formed route and the harnesses they name."""
    usable = [route for route in routes if type(route) is dict and all(routing._text(route.get(key))
                                                                       for key in routing.ROUTE_IDENTITY)]
    return dict(choices=[choice_of(route) for route in usable],
                harnesses=sorted({route['harness'] for route in usable}))


def ranked_of(routes):
    """A ranked request over fixed routes: each route's own `strategy_id` (its id when absent), in list order."""
    routes = [dict(route, strategy_id=route.get('strategy_id', route.get('route'))) if type(route) is dict else route
              for route in routes]
    harnesses = sorted({route.get('harness') for route in routes if type(route) is dict
                        and type(route.get('harness')) is str})
    return routes, dict(strategy_ids=[route['strategy_id'] for route in routes if type(route) is dict
                                      and routing._text(route.get('strategy_id'))], harnesses=harnesses)


ACTIVATION = 'activation:openrouter:scope-id:r4'
SECRET = 'sk-or-v1-never-in-observations'  # stands in for any settings content the collector must not copy


def report(root, **extra):
    """An enabled, ready `openrouter_settings.status` report for `checkout`, with secret-like content in the fields
    the collector must never copy."""
    value = dict(enabled=True, routes=['proposals'], local_configuration='ready', revision=4, checkout=str(root),
                 activation=dict(ref=ACTIVATION, funding_ref='openrouter-provider-limits'), scope_id='scope-id',
                 auth_data='/private/' + SECRET, opencode='/bin/' + SECRET, proposal_store='/data/' + SECRET)
    value.update(extra)
    return value


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

    def collect(self, routes, windows, project=None, **request):
        # A labelled code-level double supplies positive billing; no production producer exists.
        source = collector.StaticSource(routes, windows, ready, billing=subscription_billing.double('stores'))
        return collector.collect(str(project or self.root), dict(scope=SCOPE), settings_path=self.settings,
                                 ledger_path=self.ledger_path, source=source, **(request or request_of(routes)))


def excluded(result):
    return {item['route']: item['reason'] for item in result['observations']['excluded']}


def kept(result):
    return {route['route']: route for route in result['observations']['routes']}


class CollectorTests(Stores):
    def test_a_qualified_subscription_route_with_fresh_telemetry_is_a_candidate(self):
        result = self.collect([sub_route()], dict(sub=[sub_window(self.now)]))
        self.assertEqual(set(result), {'observations', 'seen', 'boundaries'})
        self.assertEqual(result['seen'], dict(openrouter_revision=None))  # no gateway route needed OpenRouter
        store = collector.window_path(self.settings)  # F4R-09: the snapshot's own read (none: a static source)
        stored = json.loads(store.read_bytes()) if store.exists() else {}
        self.assertEqual(result['boundaries'], {'sub': collector.pool_state(stored, 'sub')})
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
        assignment = dict(attempt_id='a1', task='t1', role='software-engineer', work_kind='implementation', complexity_band='medium', scope=SCOPE,
                          choice=dict(provider='openai', model='m', effort='high', harness='codex-native',
                                      funding='subscription'))
        admitted = routing.admit(assignment, ranking, settings, observations, ledger=self.service._ledger(False, True))
        self.assertEqual(admitted['status'], 'admitted', admitted)

    def test_an_unknown_unqualified_or_malformed_manifest_route_is_excluded(self):
        routes, request = ranked_of([
            sub_route('unverified', status='unverified'), sub_route('unsupported', status='unsupported'),
            sub_route('nostatus', status=None), sub_route('purchased', billing_class='purchased_usage'),
            dict(route='broken', status='qualified', harness='codex-native'), sub_route('ok')])
        result = self.collect(routes, dict(sub=[sub_window(self.now)]), **request)
        self.assertEqual(excluded(result), dict(unverified='route_unqualified', unsupported='route_unqualified',
                                                nostatus='route_unqualified', purchased='billing_class_excluded',
                                                broken='route_invalid'))
        self.assertEqual(set(kept(result)), {'ok'})

    def test_missing_or_old_window_telemetry_is_never_fresh(self):
        result = self.collect([sub_route('old'), sub_route('none', pool='other')],
                              dict(sub=[sub_window(self.now - collector.FRESH_SECONDS - 1)]))
        self.assertIs(result['observations']['pools']['sub']['windows'][0]['fresh'], False)
        self.assertEqual(excluded(result), dict(none='window_unobserved'))  # enabled, but blocked with a reason

    def test_subscription_windows_must_carry_the_manifest_window_unit(self):
        """C3-1b: a route's `demand` is in its manifest's window unit, so a stored window in another unit, or in none,
        would misadmit silently: the route is left out instead. A route that declares no unit fails closed."""
        self.assertEqual(collector.WINDOW_UNIT, 'utilization_thousandths')
        unitless = {key: value for key, value in sub_window(self.now).items() if key != 'unit'}
        cases = (('matching', sub_route(), sub_window(self.now), None),
                 ('mismatch', sub_route(), sub_window(self.now, unit='percent'), 'window_unit_mismatch'),
                 ('window_without_unit', sub_route(), unitless, 'window_unit_mismatch'),
                 ('route_without_unit', sub_route(window_unit=None), sub_window(self.now), 'window_unit_undeclared'),
                 ('route_in_other_unit', sub_route(window_unit='percent'), sub_window(self.now), 'window_unit_mismatch'))
        for name, route, window, reason in cases:
            with self.subTest(name):
                result = self.collect([route], dict(sub=[window]))
                self.assertEqual(excluded(result), {} if reason is None else dict(sub=reason))
                self.assertEqual(set(kept(result)), {'sub'} if reason is None else set())
                if reason is None:  # the stored field passes through; the manifest declaration does not
                    self.assertEqual(result['observations']['pools']['sub']['windows'][0]['unit'], collector.WINDOW_UNIT)
                    self.assertNotIn('window_unit', kept(result)['sub'])
        self.assertIn('unit', collector.WINDOW_FIELDS)  # C3-3 detects the stored-window schema by this name
        # One unitless window among the demanded ones is enough to leave the route out.
        result = self.collect([sub_route(demand={'5h': 10, '7d': 1})],
                              dict(sub=[sub_window(self.now), dict(unitless, id='7d')]))
        self.assertEqual(excluded(result), dict(sub='window_unit_mismatch'))

    def test_the_local_source_declares_the_shipped_window_unit_and_stores_it(self):
        from runtime import routing_paths
        rules = routing_paths.load_rules()
        self.assertTrue(all(item['window_unit'] == collector.WINDOW_UNIT for item in rules['paths']
                            if item['billing_class'] == 'included_subscription'))
        local = collector.default_source(self.settings)
        collector.store_window(self.settings, 'sub', sub_window(self.now))
        self.assertEqual([window['unit'] for window in local.windows('sub', self.now)], [collector.WINDOW_UNIT])
        result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                   ledger_path=self.ledger_path,
                                   source=collector.StaticSource([sub_route()], local.windows, ready,
                                                                 billing=subscription_billing.double('local-windows')),
                                   **request_of([sub_route()]))
        self.assertEqual(set(kept(result)), {'sub'})
        for bad in (dict(sub_window(self.now), unit=''), dict(sub_window(self.now), unit=5)):
            with self.assertRaisesRegex(ValueError, 'invalid_window_observation'):
                collector.store_window(self.settings, 'sub', bad)

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
        status = report(self.root)
        with ExitStack() as stack:
            for target, name in ((usage_credentials, 'resolve_key'), (usage_credentials, '_read_key'),
                                 (socket.socket, 'connect'), (socket, 'create_connection'),
                                 (urllib.request, 'urlopen'), (subprocess, 'Popen'),
                                 (routing, 'ensure_profile'), (routing, 'rank'), (routing, 'admit')):
                stack.enter_context(mock.patch.object(target, name, refuse(name)))
            stack.enter_context(mock.patch.object(openrouter_settings, 'status', lambda cwd: dict(status)))
            result = self.collect([sub_route(), gateway_route()],
                                  dict(sub=[sub_window(self.now)], gw=[gateway_window(self.now)]))
        self.assertEqual(set(kept(result)), {'sub', 'gw'})
        # C3-1b: the gateway credential comes from the status report (the source's `ref-gw` is ignored).
        self.assertEqual(kept(result)['gw']['credential'],
                         dict(state='ready', scope=collector.assignment_scope(self.root), ref=ACTIVATION))
        self.assertEqual(result['seen'], dict(openrouter_revision=4))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_the_local_source_reads_the_manifest_and_private_window_store(self):
        collector.store_window(self.settings, 'sub', sub_window(self.now))
        collector.store_window(self.settings, 'sub', sub_window(self.now - 10, used=900))  # older: ignored
        path = collector.window_path(self.settings)
        self.assertEqual(path.parent, self.settings.parent)
        self.assertEqual(oct(path.stat().st_mode & 0o777), '0o600')
        source = collector.default_source(self.settings)
        self.assertEqual([window['used'] for window in source.windows('sub', self.now)], [100])
        self.assertIsNone(source.credential(gateway_route(), SCOPE))  # a secret is never resolved
        self.assertEqual(source.manifest(), [])  # no path is qualified: nothing is generated
        with self.assertRaises(ValueError):
            collector.store_window(self.settings, 'sub', dict(sub_window(self.now), limit=0))

    def test_without_a_qualified_path_the_local_source_generates_nothing(self):
        from runtime import routing_paths
        self.assertEqual(routing_paths.gate(self.settings), ['no_qualified_path'])
        choice = dict(provider='openai', model='m', effort='high', harness='codex-native', funding='subscription')
        result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                   ledger_path=self.ledger_path, choices=[choice], harnesses=['codex-native'])
        self.assertEqual((result['observations']['routes'], result['observations']['excluded']), ([], []))
        self.assertRegex(result['observations']['routes_version'], r'^[0-9a-f]{16}$')

    def test_no_request_field_or_setting_selects_the_source(self):
        """Only code replaces `default_source` (C.3 design 3.3): `collect` takes no request input naming one."""
        seen = []
        with mock.patch.object(collector, 'default_source',
                               lambda settings_path=None: seen.append(settings_path) or
                               collector.StaticSource([sub_route()], dict(sub=[sub_window(self.now)]), ready,
                                                      billing=subscription_billing.double('default-source'))):
            result = collector.collect(str(self.root), dict(scope=SCOPE, source='local'),
                                       settings_path=self.settings, ledger_path=self.ledger_path,
                                       **request_of([sub_route()]))
        self.assertEqual(seen, [self.settings])
        self.assertEqual(set(kept(result)), {'sub'})


    def test_the_shipped_test_only_path_generates_nothing_with_fresh_telemetry_and_funding(self):
        """The shipped `test_only` path is never qualified in production, so no route is generated for it, however
        good its observations are; `qualify_path` refuses it outside test inputs."""
        from runtime import routing_paths
        self.fund()
        test_only = [item for item in routing_paths.load_rules()['paths'] if item.get('status') == 'test_only']
        self.assertTrue(test_only)
        for item in test_only:
            for window_id in item['windows']:
                collector.store_window(self.settings, item['pool'], sub_window(self.now, id=window_id))
            with self.assertRaisesRegex(ValueError, 'path_test_only'):
                routing_paths.qualify(self.settings, item['path'], demand={key: 1 for key in item['windows']},
                                      evidence='e', now=self.now)
        choices = [dict(provider=item['provider'], model='test-model', effort='none', harness=item['harness'],
                        funding='subscription') for item in test_only]
        result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                   ledger_path=self.ledger_path, choices=choices,
                                   harnesses=[item['harness'] for item in test_only])
        self.assertEqual((result['observations']['routes'], result['observations']['pools']), ([], {}))
        self.assertEqual(excluded(result), {})

    def test_a_future_dated_window_observation_is_refused(self):
        """A record dated beyond now plus the clock margin could shadow every later observation."""
        with self.assertRaisesRegex(ValueError, 'invalid_window_observation'):
            collector.store_window(self.settings, 'sub', sub_window(now() + collector.CLOCK_MARGIN + 3600))
        self.assertFalse(collector.window_path(self.settings).exists())
        collector.store_window(self.settings, 'sub', sub_window(now() + collector.CLOCK_MARGIN // 2))  # drift
        collector.store_window(self.settings, 'sub', sub_window(now() + collector.CLOCK_MARGIN, used=5))
        self.assertEqual([window['used'] for window in collector.default_source(self.settings).windows('sub', now())],
                         [5])

    def test_only_the_given_stores_are_read_and_never_the_defaults(self):
        """Coordinator decision (C3-0b review): the collector reads the service's own settings and ledger paths, so
        the two can never disagree; a default path is never opened."""
        self.fund()
        collector.store_window(self.settings, 'gw', gateway_window(self.now))
        status = report(self.root, revision=2)
        local = collector.default_source(self.settings)
        source = collector.StaticSource([gateway_route()], local.windows, ready)
        from runtime import usage_settings
        with mock.patch.object(command, 'default_settings', refuse('default_settings')), \
                mock.patch.object(command, 'default_ledger', refuse('default_ledger')), \
                mock.patch.object(usage_settings, 'directory', refuse('usage_settings.directory')), \
                mock.patch.object(openrouter_settings, 'status', lambda cwd: dict(status)):
            result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                       ledger_path=self.ledger_path, source=source, **request_of([gateway_route()]))
            elsewhere = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.root / 'other' / 's.json',
                                          ledger_path=self.root / 'other' / 'l.sqlite3', source=source,
                                          **request_of([gateway_route()]))
        self.assertEqual(kept(result)['gw']['funding_verified'], True)  # this ledger's organization, this window
        self.assertEqual(excluded(elsewhere), dict(gw='funding_unverified'))  # another ledger funds nothing
        self.assertEqual(collector.default_source(self.root / 'other' / 's.json').windows('gw', self.now), [])
        with self.assertRaises(TypeError):
            collector.collect(str(self.root), dict(scope=SCOPE), source=source)  # both paths are required


class ManifestEndpointTests(Stores):
    """C3-1b: a fixed route list qualifying one identity at two endpoints is refused as a whole (fail closed) rather
    than letting either route stand for the other. Generated routes skip such strategies instead (test_routing_paths)."""

    def test_two_qualified_routes_differing_only_by_endpoint_refuse_the_manifest(self):
        ambiguous = [gateway_route('gw-p', endpoint='p'), gateway_route('gw-q', endpoint='q', account='other')]
        missing = [gateway_route('gw-p', endpoint='p'), {k: v for k, v in gateway_route('gw-x').items()
                                                        if k != 'endpoint'}]
        for routes in (ambiguous, missing):
            with self.subTest(routes=[route['route'] for route in routes]):
                with self.assertRaisesRegex(ValueError, 'route_manifest_endpoint_ambiguous'):
                    collector.validate_routes(routes)
                with self.assertRaisesRegex(ValueError, 'route_manifest_endpoint_ambiguous'):  # prepare fails closed
                    self.collect(routes, dict(gw=[gateway_window(self.now)]))

    def test_one_endpoint_per_identity_is_accepted(self):
        routes = [gateway_route('gw-p', endpoint='p'), gateway_route('gw-p2', endpoint='p', account='other'),
                  gateway_route('gw-q', endpoint='q', status='unverified'),  # not qualified: never confusable
                  gateway_route('gw-m2', endpoint='q', model='model/v2')]  # another identity
        self.assertEqual(collector.validate_routes(routes), routes)


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
        # C3-1b: the credential is the real report's checkout scope and activation reference, nothing else.
        self.assertEqual(route['credential'], dict(state='ready', scope=collector.assignment_scope(self.repo),
                                                   ref=status['activation']['ref']))
        self.assertEqual(route['credential']['scope'], collector.assignment_scope(status['checkout']))
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

    def test_a_gateway_credential_is_ready_only_from_an_enabled_ready_report(self):
        """C3-1b: each not-ready report leaves the gateway route out with its fixed reason; a source credential never
        stands in, and no settings content reaches observations or seen."""
        self.fund()
        cases = (('disabled', report(self.repo, enabled=False, activation=None), 'integration_disabled'),
                 ('route_not_listed', report(self.repo, routes=['workers']), 'integration_disabled'),
                 ('local_unavailable', report(self.repo, local_configuration='unavailable'), 'integration_disabled'),
                 ('not_configured', report(self.repo, local_configuration='not_configured'), 'integration_disabled'),
                 ('no_activation', report(self.repo, activation=None), 'credential_unavailable'),
                 ('activation_without_ref', report(self.repo, activation=dict(ref='')), 'credential_unavailable'),
                 ('no_checkout', report(self.repo, checkout=None), 'credential_unavailable'))
        for name, status, reason in cases:
            with self.subTest(name), mock.patch.object(openrouter_settings, 'status', lambda cwd, s=status: dict(s)):
                result = self.collect([self.gateway(credential=dict(state='ready', scope=SCOPE, ref='x'))],
                                      self.windows, project=self.repo)
                self.assertEqual(excluded(result), dict(gw=reason))
                self.assertEqual(result['seen'], dict(openrouter_revision=4))
                self.assertNotIn(SECRET, json.dumps(result))
        with mock.patch.object(openrouter_settings, 'status', lambda cwd: report(self.repo)):
            result = self.collect([self.gateway()], self.windows, project=self.repo)
        self.assertEqual(kept(result)['gw']['credential'],
                         dict(state='ready', scope=collector.assignment_scope(self.repo), ref=ACTIVATION))
        self.assertEqual(set(kept(result)['gw']['credential']), {'state', 'scope', 'ref'})
        self.assertNotIn(SECRET, json.dumps(result))

    def test_a_busy_lock_leaves_the_gateway_without_a_credential(self):
        busy = mock.Mock(side_effect=openrouter_settings.SettingsError('openrouter_operation_busy'))
        with mock.patch.object(openrouter_settings, 'status', busy):
            result = self.collect([self.gateway()], self.windows, project=self.repo)
        self.assertEqual(excluded(result), dict(gw='openrouter_operation_busy'))
        self.assertEqual((result['observations']['routes'], result['seen']), ([], dict(openrouter_revision=None)))
        self.assertEqual(busy.call_count, 1)

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
        self.assertNotIn('credential', kept(result)['sub'])
        self.assertEqual(result['seen'], dict(openrouter_revision=None))
        self.assertEqual((admission.call_count, status.call_count), (0, 1))  # no retry loop
        self.assertLess(elapsed, 5)


CLAUDE_PATH, CHATGPT_PATH = 'opencode-claude-subscription', 'opencode-chatgpt-subscription'
CLAUDE_HARNESS, CHATGPT_HARNESS = 'claude-agent-sdk via opencode', 'opencode provider openai'


def strategy(sid, harness=CLAUDE_HARNESS, provider='claude-agent', model=None, effort='high', **extra):
    return dict(strategy_id=sid, model=model or 'invented-' + sid, effort=effort, harness=harness, provider=provider,
                evidence_refs=['g'], **extra)


def table_of(*strategies):
    return dict(schema=2, version='tv1', rows={'implementation|high|high': list(strategies)})


class LocalSourceTests(Stores):
    """F4.2/F4.4 on the production source: routes generated in memory from the shipped rules, qualifications in this
    test's private store and the request's table or choices. These pin behavior implemented before they were
    written (no test-first claim)."""

    def setUp(self):
        super().setUp()
        # No production billing producer exists: a labelled code-level double stands in for this class's flows.
        billing = mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now:
                                    subscription_billing.double('local-source-tests')(route, now))
        billing.start()
        self.addCleanup(billing.stop)

    def qualify(self, path=CLAUDE_PATH, demand=None):
        from runtime import routing_paths
        demand = demand or ({'5h': 10, '7d': 2} if path == CLAUDE_PATH else {'7d': 3})
        routing_paths.qualify(self.settings, path, demand=demand, evidence='e', now=self.now)

    def store(self, pool, *ids, at=None):
        for window in ids:
            collector.store_window(self.settings, pool, sub_window(self.now if at is None else at, id=window))

    def collect(self, routes=None, windows=None, project=None, **request):
        request.setdefault('harnesses', [CLAUDE_HARNESS, CHATGPT_HARNESS])
        return collector.collect(str(project or self.root), dict(scope=SCOPE), settings_path=self.settings,
                                 ledger_path=self.ledger_path, **request)

    def test_not_entitled_mark_still_excludes_a_generated_route(self):
        self.qualify()
        self.store('claude-subscription', '5h', '7d')
        table = table_of(strategy('s1'))
        route = 'opencode-claude-subscription:s1'
        self.assertEqual(set(kept(self.collect(table=table, strategy_ids=['s1']))), {route})
        collector.mark_not_entitled(self.settings, route, 'claude-plan', self.now)
        self.assertEqual(excluded(self.collect(table=table, strategy_ids=['s1'])), {route: 'not_entitled'})
        choice = dict(provider='claude-agent', model='invented-s1', effort='high', harness=CLAUDE_HARNESS,
                      funding='subscription')
        chosen = self.collect(choices=[choice])  # the choice route of the same model has its own id: no carry-over
        self.assertEqual(len(kept(chosen)), 1)
        self.assertRegex(next(iter(kept(chosen))), r'^opencode-claude-subscription:choice:[0-9a-f]{16}$')

    def test_local_source_without_a_settings_path_has_no_route(self):
        self.qualify()
        source = collector.LocalSource(None)
        self.assertEqual(source.manifest(table_of(strategy('s1'))), [])
        self.assertIsNone(source.admitted_route('opencode-claude-subscription:s1'))
        self.assertIs(source.seedable('claude-subscription', '5h'), False)
        self.assertEqual(source.windows('claude-subscription', self.now), [])

    def test_collect_reads_only_the_ranked_strategies(self):
        self.qualify()
        self.store('claude-subscription', '5h', '7d')
        strategies = [strategy('s%02d' % n) for n in range(80)]
        ranked = ['s07', 's03', 's70', 's11', 's42']
        result = self.collect(table=table_of(*strategies), strategy_ids=ranked)
        self.assertEqual([route['strategy_id'] for route in result['observations']['routes']], ranked)
        self.assertEqual(result['observations']['excluded'], [])  # the 75 others are never considered

    def test_collector_route_count_bound(self):
        self.qualify()
        self.store('claude-subscription', '5h', '7d')
        strategies = [strategy('s%03d' % n) for n in range(100)]
        cut = collector.shortlist(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                  table=table_of(*strategies), strategy_ids=[item['strategy_id'] for item in strategies],
                                  harnesses=[CLAUDE_HARNESS])
        self.assertEqual(len(cut['routes']), routing.MAX_ROUTES)
        self.assertEqual(cut['shortlist'], ['s%03d' % n for n in range(routing.MAX_ROUTES)])  # ranking order kept

    def test_static_source_answers_the_whole_interface(self):
        static = collector.StaticSource([sub_route(), gateway_route(pool='gw')], dict(sub=[sub_window(self.now)]))
        for name in ('manifest', 'admitted_route', 'pins_endpoint', 'seedable', 'routes_version', 'windows',
                     'credential', 'billing'):
            self.assertTrue(callable(getattr(static, name)), name)
            self.assertTrue(callable(getattr(collector.LocalSource(self.settings), name)), name)
        self.assertEqual([route['route'] for route in static.manifest(table_of(strategy('x')), [{}])], ['sub', 'gw'])
        self.assertEqual(static.admitted_route('sub')['account'], 'sub-account')
        self.assertIsNone(static.admitted_route('sub', dict(choice_of(sub_route()), model='other')))
        self.assertIs(static.pins_endpoint('gw', choice_of(gateway_route()), 'p'), True)
        self.assertIs(static.pins_endpoint('gw', choice_of(gateway_route()), 'q'), False)
        self.assertIs(static.seedable('sub', '5h'), True)
        self.assertIs(static.seedable('gw', 'credit'), False)
        self.assertIsNone(static.routes_version(table_of()))

    def test_reader_runs_only_for_a_kept_chatgpt_route(self):
        from runtime import codex_rate_limits
        self.qualify()
        self.qualify(CHATGPT_PATH)
        self.store('claude-subscription', '5h', '7d')
        self.store('chatgpt-subscription', '7d', at=self.now - collector.FRESH_SECONDS - 60)  # stale
        table = table_of(strategy('c1'), strategy('o1', harness=CHATGPT_HARNESS, provider='openai'))
        with mock.patch.object(codex_rate_limits, 'refresh', side_effect=Refused('codex reader')):
            claude_only = self.collect(table=table, strategy_ids=['o1', 'c1'], harnesses=[CLAUDE_HARNESS],
                                       refresh_windows=True)
        self.assertEqual(set(kept(claude_only)), {'opencode-claude-subscription:c1'})
        reader = mock.Mock(return_value=dict(state='failed', reason='codex_unavailable'))
        with mock.patch.object(codex_rate_limits, 'refresh', reader):
            both = self.collect(table=table, strategy_ids=['o1', 'c1'], refresh_windows=True)
        self.assertEqual(reader.call_count, 1)  # once, for the kept ChatGPT route's pool only
        self.assertEqual(reader.call_args.kwargs['need'], ('7d',))
        self.assertEqual(excluded(both), {'opencode-chatgpt-subscription:o1': 'codex_unavailable'})

    def test_choice_filter_keeps_static_source_ids(self):
        routes = [sub_route('fixed-a'), sub_route('fixed-b', model='other'),
                  sub_route('ranked', strategy_id='s-ranked')]
        result = self.collect_static(routes, choices=[choice_of(routes[0])])
        self.assertEqual(set(kept(result)), {'fixed-a'})  # by identity and funding; a strategy route never matches
        wrong_funding = self.collect_static(routes, choices=[dict(choice_of(routes[0]), funding='api')])
        self.assertEqual(kept(wrong_funding), {})

    def collect_static(self, routes, **request):
        source = collector.StaticSource(routes, dict(sub=[sub_window(self.now)]), ready,
                                        billing=subscription_billing.double('collect-static'))
        return collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                 ledger_path=self.ledger_path, source=source, harnesses=['codex-native'], **request)

    def test_shortlist_and_collect_share_the_pre_window_cut(self):
        self.qualify()
        self.qualify(CHATGPT_PATH)
        self.store('claude-subscription', '5h', '7d')
        table = table_of(strategy('c1'), strategy('c2'), strategy('o1', harness=CHATGPT_HARNESS, provider='openai'))
        collector.mark_not_entitled(self.settings, 'opencode-claude-subscription:c2', 'claude-plan', self.now)
        request = dict(table=table, strategy_ids=['c2', 'o1', 'c1'], harnesses=[CLAUDE_HARNESS, CHATGPT_HARNESS])
        cut = collector.shortlist(str(self.root), dict(scope=SCOPE), settings_path=self.settings, **request)
        with mock.patch.object(collector, 'shortlist', wraps=collector.shortlist) as shared:
            result = self.collect(**request)
        self.assertEqual(shared.call_count, 1)
        self.assertEqual(cut['shortlist'], ['o1', 'c1'])
        self.assertEqual(cut['excluded'], [dict(route='opencode-claude-subscription:c2', reason='not_entitled')])
        self.assertEqual(result['observations']['excluded'][:1], cut['excluded'])  # the same static exclusions first
        self.assertEqual(excluded(result)['opencode-chatgpt-subscription:o1'], 'window_unobserved')  # then windows
        self.assertEqual(result['observations']['routes_version'], cut['routes_version'])

    def test_shortlist_never_reads_windows_or_ledger_or_refreshes(self):
        self.qualify()
        self.store('claude-subscription', '5h', '7d')
        before = sorted((path.name, path.read_bytes()) for path in self.settings.parent.iterdir() if path.is_file())
        with mock.patch.object(collector.LocalSource, 'windows', side_effect=Refused('windows')), \
                mock.patch.object(collector, '_ledger_data', side_effect=Refused('ledger')), \
                mock.patch.object(collector, 'refresh_needed', side_effect=Refused('refresh')), \
                mock.patch.object(collector, '_openrouter', side_effect=Refused('openrouter')), \
                mock.patch.object(collector, 'store_window', side_effect=Refused('write')):
            cut = collector.shortlist(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                      table=table_of(strategy('s1')), strategy_ids=['s1'], harnesses=[CLAUDE_HARNESS])
        self.assertEqual(cut['shortlist'], ['s1'])
        after = sorted((path.name, path.read_bytes()) for path in self.settings.parent.iterdir() if path.is_file())
        self.assertEqual(after, before)
        self.assertFalse(self.ledger_path.exists())

    def test_ranked_paid_route_never_reads_openrouter_status(self):
        """A synthetic qualified gateway path survives the host filter but a ranked dollar route is excluded before
        any funding or OpenRouter status read (decision B)."""
        from runtime import routing_paths
        rules = routing_paths.load_rules()
        routing_paths.qualify(self.settings, 'openrouter-proposals', demand={'credit': 5}, evidence='e', now=self.now,
                              rules=rules)
        gateway = strategy('g1', harness='openrouter-proposal', provider='openrouter', effort=None, endpoint='ep')
        with mock.patch.object(collector, '_openrouter', side_effect=Refused('openrouter')), \
                mock.patch.object(openrouter_settings, 'status', side_effect=Refused('status')):
            result = self.collect(table=table_of(gateway), strategy_ids=['g1'], harnesses=['openrouter-proposal'])
        self.assertEqual(excluded(result), {'openrouter-proposals:g1': 'billing_class_excluded'})
        self.assertEqual(result['seen'], dict(openrouter_revision=None))

    def test_host_filter_drops_routes_silently_before_every_other_step(self):
        self.qualify()
        self.qualify(CHATGPT_PATH)
        table = table_of(strategy('c1'), strategy('o1', harness=CHATGPT_HARNESS, provider='openai'))
        collector.mark_not_entitled(self.settings, 'opencode-chatgpt-subscription:o1', 'chatgpt-plan', self.now)
        result = self.collect(table=table, strategy_ids=['o1', 'c1'], harnesses=[CLAUDE_HARNESS])
        self.assertNotIn('opencode-chatgpt-subscription:o1', json.dumps(result))  # not even its not_entitled mark
        self.assertEqual(excluded(result), {'opencode-claude-subscription:c1': 'window_unobserved'})
        nothing = self.collect(table=table, strategy_ids=['o1', 'c1'], harnesses=None)
        self.assertEqual((nothing['observations']['routes'], nothing['observations']['excluded']), ([], []))

    def test_pool_windows_are_read_only_for_paths_the_host_can_send(self):
        self.qualify()
        self.qualify(CHATGPT_PATH)
        self.store('claude-subscription', '5h', '7d')
        self.store('chatgpt-subscription', '7d')
        read = []
        real = collector.LocalSource.windows

        def spy(source, pool, now, **read_once):  # `stored`: the collector's one store read (F4R-09)
            read.append(pool)
            return real(source, pool, now, **read_once)
        table = table_of(strategy('c1'), strategy('o1', harness=CHATGPT_HARNESS, provider='openai'))
        with mock.patch.object(collector.LocalSource, 'windows', spy):
            result = self.collect(table=table, strategy_ids=['o1', 'c1'], harnesses=[CHATGPT_HARNESS])
        self.assertEqual(read, ['chatgpt-subscription'])
        self.assertEqual(set(result['observations']['pools']), {'chatgpt-subscription'})


class ManagedFixtureTests(unittest.TestCase):
    """The managed-project fixture past its slice check (C.3 design 6.4 A4 rule 3), with C3-0b stood in while it is
    absent: every default path the fixture patches resolves inside the fixture's temporary directory."""

    def stand_in(self):
        from runtime import routing_settings

        def managed_service(checkout, scope=None):  # C.3 design 3.3: default paths, read at call time
            return command.Service(command.default_settings(), command.default_ledger(),
                                   project=os.path.realpath(checkout))

        for target, name, value in ((routing_settings, 'managed_project', lambda path, settings_path=None: False),
                                    (command, 'managed_service', managed_service)):
            if not callable(getattr(target, name, None)):
                patcher = mock.patch.object(target, name, create=True, new=value)
                patcher.start()
                self.addCleanup(patcher.stop)

    def test_the_fixture_redirects_every_default_path(self):
        from runtime import usage_settings
        from tests import test_smart_routing_features as features
        self.stand_in()
        with tempfile.TemporaryDirectory() as temp:
            checkout = Path(temp).resolve()
            routes = (features.fixture_route('native:m#high', 'included_subscription'),
                      features.fixture_route('openrouter:v/m@p', 'gateway'))
            with features.managed_project_fixture(self, checkout, routes=routes) as fixture:
                base = Path(fixture['settings']).parents[1]
                self.assertEqual((command.default_settings(), command.default_ledger()),
                                 (fixture['settings'], fixture['ledger']))
                for name, path in (('default_settings', command.default_settings()),
                                   ('default_ledger', command.default_ledger()),
                                   ('usage_settings.directory', usage_settings.directory()),
                                   ('BASIC_STORAGE', openrouter_settings.BASIC_STORAGE)):
                    with self.subTest(default=name):
                        self.assertIn(base, Path(path).resolve().parents, name)
                service = command.managed_service(checkout)
                try:
                    status = service.handle(b'{"v":1,"op":"status"}')
                finally:
                    service.close()
                self.assertEqual((status['ledger']['state'], status['ledger']['authority_required']), ('ready', True))


class ManagedFixtureHomeTests(unittest.TestCase):
    """C.3 design 6.4 A4 rule 3: the four managed-fixture contracts and the fixture past its slice check, under plain
    unittest with a fresh HOME and no test_sharded wrapper, leave HOME empty."""

    CONTRACTS = ('test_smart_routing_features.C2bNativeDelegationThroughRouting'
                 '.test_native_worker_admission_calls_routing_prepare_once',
                 'test_smart_routing_features.C2bNativeDelegationThroughRouting'
                 '.test_native_worker_admission_is_bound_to_its_workflow_job',
                 'test_smart_routing_features.C2cProposalThroughRouting.test_proposal_begin_obtains_one_routing_admission',
                 'test_smart_routing_features.C2cWorkerThroughRouting.test_worker_prepare_obtains_one_routing_admission',
                 'test_routing_observations.ManagedFixtureTests.test_the_fixture_redirects_every_default_path')

    def test_the_managed_contracts_leave_a_fresh_home_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'home'
            home.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if key not in ('HOME', 'CODEX_HOME') and not key.startswith(('XDG_', 'PYTHON'))}
            env['HOME'] = str(home)
            names = ['tests.' + name for name in self.CONTRACTS]
            # Plain unittest: `-I` keeps the checkout off sys.path, so only the package root is put back.
            runner = 'import sys, unittest; sys.path.insert(0, sys.argv[1]); unittest.main(module=None, argv=sys.argv[1:])'
            done = subprocess.run([sys.executable, '-I', '-B', '-c', runner, str(ROOT), *names], cwd=temp, env=env,
                                  capture_output=True, timeout=600)
            output = done.stderr.decode()[-3000:]
            self.assertEqual(done.returncode, 0, output)
            self.assertIn('Ran %d tests' % len(names), output)
            self.assertEqual(sorted(str(path.relative_to(home)) for path in home.rglob('*')), [], output)


if __name__ == '__main__':
    unittest.main()
