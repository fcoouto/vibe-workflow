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
        manifest = json.loads(collector.MANIFEST.read_text())
        self.assertEqual(manifest['window_unit'], collector.WINDOW_UNIT)
        local = collector.default_source(self.settings)
        self.assertTrue(all(route['window_unit'] == collector.WINDOW_UNIT for route in local.manifest()))
        collector.store_window(self.settings, 'sub', sub_window(self.now))
        self.assertEqual([window['unit'] for window in local.windows('sub', self.now)], [collector.WINDOW_UNIT])
        result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                   ledger_path=self.ledger_path,
                                   source=collector.StaticSource([sub_route()], local.windows, ready))
        self.assertEqual(set(kept(result)), {'sub'})
        for bad in (dict(sub_window(self.now), unit=''), dict(sub_window(self.now), unit=5)):
            with self.assertRaisesRegex(ValueError, 'invalid_window_observation'):
                collector.store_window(self.settings, 'sub', bad)
        path = self.root / 'routes.json'
        path.write_text(json.dumps(dict(version=1, window_unit=7, routes=[])))
        with self.assertRaisesRegex(ValueError, 'route_manifest_invalid'):
            collector.load_manifest(path)

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


    def test_the_shipped_test_only_route_stays_excluded_with_fresh_telemetry_and_funding(self):
        """The shipped manifest's `test_only` route is never a candidate, however good its observations are."""
        self.fund()
        test_only = [route for route in collector.load_manifest()['routes'] if route['status'] == 'test_only']
        self.assertTrue(test_only)
        for route in test_only:
            for window_id in route['demand']:
                collector.store_window(self.settings, route['pool'], sub_window(self.now, id=window_id))
        result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                   ledger_path=self.ledger_path)
        self.assertEqual(result['observations']['routes'], [])
        self.assertEqual(result['observations']['pools'], {})
        self.assertEqual(excluded(result), {route['route']: 'route_unqualified' for route in test_only})

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
                                       ledger_path=self.ledger_path, source=source)
            elsewhere = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.root / 'other' / 's.json',
                                          ledger_path=self.root / 'other' / 'l.sqlite3', source=source)
        self.assertEqual(kept(result)['gw']['funding_verified'], True)  # this ledger's organization, this window
        self.assertEqual(excluded(elsewhere), dict(gw='funding_unverified'))  # another ledger funds nothing
        self.assertEqual(collector.default_source(self.root / 'other' / 's.json').windows('gw', self.now), [])
        with self.assertRaises(TypeError):
            collector.collect(str(self.root), dict(scope=SCOPE), source=source)  # both paths are required


class ManifestEndpointTests(Stores):
    """C3-1b: the route identity has no endpoint, so a manifest qualifying one identity at two endpoints is refused
    as a whole (fail closed) rather than letting either route stand for the other."""

    def write(self, routes):
        path = self.root / 'routes.json'
        path.write_text(json.dumps(dict(version=1, routes=routes)))
        return path

    def test_two_qualified_routes_differing_only_by_endpoint_refuse_the_manifest(self):
        ambiguous = [gateway_route('gw-p', endpoint='p'), gateway_route('gw-q', endpoint='q', account='other')]
        missing = [gateway_route('gw-p', endpoint='p'), {k: v for k, v in gateway_route('gw-x').items()
                                                        if k != 'endpoint'}]
        for routes in (ambiguous, missing):
            with self.subTest(routes=[route['route'] for route in routes]):
                with self.assertRaisesRegex(ValueError, 'route_manifest_endpoint_ambiguous'):
                    collector.load_manifest(self.write(routes))
                source = collector.StaticSource(routes, dict(gw=[gateway_window(self.now)]), ready)
                self.assertEqual(collector.manifest_check(source), ['route_manifest_invalid'])
                with self.assertRaisesRegex(ValueError, 'route_manifest_endpoint_ambiguous'):  # prepare fails closed
                    self.collect(routes, dict(gw=[gateway_window(self.now)]))

    def test_one_endpoint_per_identity_is_accepted(self):
        routes = [gateway_route('gw-p', endpoint='p'), gateway_route('gw-p2', endpoint='p', account='other'),
                  gateway_route('gw-q', endpoint='q', status='unverified'),  # not qualified: never confusable
                  gateway_route('gw-m2', endpoint='q', model='model/v2')]  # another identity
        self.assertEqual(collector.load_manifest(self.write(routes))['routes'], routes)
        self.assertEqual(collector.manifest_check(collector.StaticSource(routes)), [])
        self.assertEqual(collector.load_manifest(), json.loads(collector.MANIFEST.read_text()))  # shipped one is valid


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
