"""F4.5/F4.6: the shared subscription-only billing boundary.

Every `included_subscription` route needs a current, account-scoped fact that the provider blocks paid crossover,
at the collector's static eligibility (before the cut and any window read) and again in `admitted_check` just
before dispatch. No production producer is qualified, so the real `LocalSource` answers none and every subscription
route is `billing_unverified`. Positive facts here come only from `subscription_billing.double`, a labelled
code-level double; nothing is read from an account, client, file outside the test stores or the network.
"""
from unittest import mock
import unittest

from runtime import routing, routing_launch as launcher, routing_observations as collector, routing_paths, \
    routing_settings, subscription_billing as billing
from tests import test_routing_launch as launch_tests
from tests.test_routing_observations import SCOPE, Stores, excluded, kept, ready, sub_route, sub_window

NOW = 1_800_000_000
ROUTE = dict(route='opencode-claude-subscription:s1', account='claude-plan', pool='claude-subscription')


def fact(**change):
    value = billing.double('unit')(ROUTE, NOW)
    value.update(change)
    return value


class FactTests(unittest.TestCase):
    def test_a_current_provider_blocked_fact_for_this_scope_passes(self):
        self.assertIsNone(billing.problem(fact(), ROUTE, NOW))
        self.assertEqual(fact()['producer'], 'synthetic:unit')  # the double names itself
        self.assertEqual((fact()['path'], fact()['account'], fact()['pool']),
                         ('opencode-claude-subscription', 'claude-plan', 'claude-subscription'))

    def test_missing_or_malformed_facts_fail_closed(self):
        self.assertEqual(billing.problem(None, ROUTE, NOW), 'billing_missing')
        good = fact()
        cases = [[], 'verified', True, dict(good, extra=1), {k: v for k, v in good.items() if k != 'pool'},
                 dict(good, producer=''), dict(good, account=None), dict(good, observed_at='1'),
                 dict(good, observed_at=True), dict(good, valid_until=-1), dict(good, valid_until=1.5),
                 dict(good, paid_crossover=None), dict(good, valid_until=good['observed_at'] - 1)]
        for value in cases:
            with self.subTest(fact=value):
                self.assertEqual(billing.problem(value, ROUTE, NOW), 'billing_malformed')

    def test_stale_expired_and_future_facts_fail_closed(self):
        self.assertEqual(billing.problem(fact(observed_at=NOW - billing.FRESH_SECONDS - 1), ROUTE, NOW),
                         'billing_stale')
        self.assertEqual(billing.problem(fact(valid_until=NOW), ROUTE, NOW), 'billing_stale')
        self.assertEqual(billing.problem(fact(observed_at=NOW + billing.CLOCK_MARGIN + 1,
                                              valid_until=NOW + 10**4), ROUTE, NOW), 'billing_future')
        self.assertIsNone(billing.problem(fact(observed_at=NOW - billing.FRESH_SECONDS), ROUTE, NOW))

    def test_a_fact_for_another_path_account_or_pool_fails_closed(self):
        for change in (dict(path='codex-chatgpt-subscription'), dict(account='other-plan'),
                       dict(pool='chatgpt-subscription')):
            with self.subTest(change=change):
                self.assertEqual(billing.problem(fact(**change), ROUTE, NOW), 'billing_scope_mismatch')

    def test_enabled_or_unknown_paid_crossover_fails_closed(self):
        for state in ('enabled', 'unknown', 'disabled_by_user', 'budget_zero'):
            with self.subTest(paid_crossover=state):
                self.assertEqual(billing.problem(fact(paid_crossover=state), ROUTE, NOW), 'billing_crossover_possible')

    def test_a_failing_or_absent_source_is_unverified(self):
        class Broken:
            def billing(self, route, now):
                raise OSError('unreadable')
        for source in (Broken(), object(), collector.LocalSource(None)):
            with self.subTest(source=type(source).__name__):
                self.assertFalse(billing.verified(source, ROUTE, NOW))
        asked = []
        source = collector.StaticSource([], billing=lambda route, now: asked.append(route) or fact())
        route = dict(ROUTE)
        self.assertTrue(billing.verified(source, route, NOW))
        asked[0]['account'] = 'changed'
        self.assertEqual(route, ROUTE)  # the source gets a copy


def family_routes():
    """One invented subscription route per family: OpenCode Claude, OpenCode ChatGPT, native Codex, native Claude and
    an OpenCode Go-style connection, each on its own pool."""
    families = (('oc-claude', 'claude-agent', 'claude-agent-sdk via opencode', 'claude-subscription'),
                ('oc-chatgpt', 'openai', 'opencode provider openai', 'chatgpt-subscription'),
                ('codex', 'openai', 'codex-native', 'codex-pool'),
                ('claude-native', 'anthropic', 'claude-native', 'claude-native-pool'),
                ('oc-go', 'opencode-go', 'opencode provider opencode-go', 'go-pool'))
    return [sub_route(route=name + ':s', provider=provider, harness=harness, pool=pool, account=pool + '-account',
                      model='invented-' + name) for name, provider, harness, pool in families]


class CollectorBoundaryTests(Stores):
    def collect_static(self, routes, billing_source=None, harnesses=None):
        windows = {route['pool']: [sub_window(self.now)] for route in routes}
        read = []
        source = collector.StaticSource(routes, lambda pool, at: read.append(pool) or windows.get(pool, []), ready,
                                        billing=billing_source)
        choices = [dict({key: route[key] for key in routing.ROUTE_IDENTITY}, funding='subscription')
                   for route in routes]
        result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                   ledger_path=self.ledger_path, source=source, choices=choices,
                                   harnesses=harnesses or [route['harness'] for route in routes])
        return result, read

    def test_every_subscription_family_is_unverified_without_a_fact(self):
        routes = family_routes()
        result, read = self.collect_static(routes)
        self.assertEqual(excluded(result), {route['route']: 'billing_unverified' for route in routes})
        self.assertEqual(kept(result), {})
        self.assertEqual(read, [])  # refused before any window read: unknown billing is neither full nor spare

    def test_a_labelled_double_admits_each_family_and_is_asked_after_the_host_filter(self):
        routes = family_routes()
        asked = []
        double = billing.double('collector-test')

        def produce(route, now):
            asked.append(route['route'])
            return double(route, now)
        host = [routes[2]['harness']]  # the native Codex host only
        result, read = self.collect_static(routes, produce, harnesses=host)
        self.assertEqual(asked, [routes[2]['route']])  # no foreign connection is queried
        self.assertEqual(set(kept(result)), {routes[2]['route']})
        self.assertEqual(read, [routes[2]['pool']])
        result, _ = self.collect_static(routes, double)
        self.assertEqual(set(kept(result)), {route['route'] for route in routes})

    def test_a_wrong_scope_or_crossover_fact_is_refused_before_the_cut(self):
        routes = family_routes()[:2]
        for change in (dict(account='someone-else'), dict(paid_crossover='enabled'), dict(path='other')):
            with self.subTest(change=change):
                result, read = self.collect_static(
                    routes, lambda route, now, change=change: dict(billing.double('t')(route, now), **change))
                self.assertEqual(set(excluded(result).values()), {'billing_unverified'})
                self.assertEqual(read, [])

    def test_gateway_routes_are_not_subject_to_the_subscription_fact(self):
        from tests.test_routing_observations import gateway_route
        source = collector.StaticSource([gateway_route()], {}, ready)
        cut = collector.shortlist(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                  choices=[dict({k: gateway_route()[k] for k in routing.ROUTE_IDENTITY},
                                                funding='api')],
                                  harnesses=['openrouter-proposal'], source=source)
        self.assertNotIn('billing_unverified', {item['reason'] for item in cut['excluded']})

    def test_the_real_local_source_refuses_qualified_and_seeded_paths(self):
        """Qualification, a fresh window and a table are not billing proof: every shipped subscription path stays out
        on the production source, which reads nothing for billing."""
        strategies = []
        for path in routing_paths.load_rules()['paths']:
            if path['billing_class'] != routing_paths.SUBSCRIPTION or path.get('status') == 'test_only':
                continue
            routing_paths.qualify(self.settings, path['path'], demand={window: 5 for window in path['windows']},
                                  evidence='live-1', now=self.now)
            for window in path['windows']:
                collector.store_window(self.settings, path['pool'], dict(sub_window(self.now), id=window))
            strategies.append(dict(strategy_id='s-' + path['path'], model='invented-model', effort='high',
                                   harness=path['harness'], provider=path['provider'], evidence_refs=['g']))
        table = dict(schema=2, version='tv', rows={'implementation|medium|medium': strategies})
        windows = mock.Mock(side_effect=AssertionError('a window was read'))
        with mock.patch.object(collector.LocalSource, 'windows', windows):
            result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                       ledger_path=self.ledger_path, table=table,
                                       strategy_ids=[item['strategy_id'] for item in strategies],
                                       harnesses=[item['harness'] for item in strategies])
            preview = collector.shortlist(str(self.root), dict(scope=SCOPE), settings_path=self.settings, table=table,
                                          strategy_ids=[item['strategy_id'] for item in strategies],
                                          harnesses=[item['harness'] for item in strategies])
        self.assertEqual(len(strategies), 5)  # OpenCode Claude and ChatGPT, Codex, native Claude, OpenCode Go
        self.assertEqual(set(excluded(result).values()), {'billing_unverified'})
        self.assertEqual(len(excluded(result)), 5)
        self.assertEqual(preview['routes'], [])
        self.assertIsNone(collector.LocalSource(self.settings).billing(kept_route(), self.now))


def kept_route():
    return dict(route='codex-chatgpt-subscription:s1', account='chatgpt-plan', pool='chatgpt-subscription')


class DispatchBoundaryTests(Stores):
    """`admitted_check` rechecks the fact just before dispatch; a change after admission refuses and sends nothing."""

    def setUp(self):
        super().setUp()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        self.service.close()
        self.service = launch_tests.ManagedStub(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(self.service.close)
        self.state = dict(change={})
        double = billing.double('dispatch-test')
        self.source = collector.StaticSource([sub_route()], dict(sub=[sub_window(self.now)]), ready,
                                             billing=lambda route, now: dict(double(route, now),
                                                                             **self.state['change']))
        for patch in (mock.patch.object(collector, 'default_source', lambda settings_path=None: self.source),
                      mock.patch.object(routing_settings, 'managed_project', create=True,
                                        new=lambda path, settings_path=None: True)):
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=True))['state'], 'updated')

    def admission(self, **change):
        value = dict(route='sub', billing_class='included_subscription', pool='sub', account='sub-account',
                     route_identity=routing.route_identity(*launch_tests.IDENTITY.values()))
        value.update(change)
        return value

    def test_dispatch_needs_a_current_fact_for_the_admitted_scope(self):
        check = lambda admission: launcher.admitted_check(  # noqa: E731
            admission, host='codex_native', catalog=[launch_tests.IDENTITY], settings_path=self.settings,
            source=self.source)
        facts, reason = check(self.admission())
        self.assertIsNone(reason)
        self.assertEqual(facts['funding_pool'], 'sub')
        self.assertIsNone(check({k: v for k, v in self.admission().items() if k != 'account'})[1])  # public view
        self.assertEqual(check(self.admission(account='other-account')), (None, 'billing_unverified'))  # two accounts
        for change in (dict(valid_until=0), dict(paid_crossover='enabled'), dict(account='changed')):
            with self.subTest(change=change):
                self.state['change'] = change
                self.assertEqual(check(self.admission()), (None, 'billing_unverified'))
        self.state['change'] = {}
        plain = collector.StaticSource([sub_route()], dict(sub=[sub_window(self.now)]), ready)  # no producer
        self.assertEqual(launcher.admitted_check(self.admission(), host='codex_native',
                                                 catalog=[launch_tests.IDENTITY], source=plain),
                         (None, 'billing_unverified'))

    def test_a_fact_that_changes_after_admission_refuses_dispatch_and_releases_the_hold(self):
        transport = launch_tests.Transport()
        real = launcher.admitted_check

        def expire_then_check(admission, **kwargs):
            self.state['change'] = dict(paid_crossover='enabled')  # changed between admission and dispatch
            return real(admission, **kwargs)
        with mock.patch.object(launcher, 'admitted_check', expire_then_check):
            result = launcher.launch(self.service, host='codex_native', catalog=[launch_tests.IDENTITY],
                                     transport=transport, settings_path=self.settings,
                                     assignment=dict(attempt_id='a1', task='task-a1', scope=SCOPE,
                                                     choice=dict(launch_tests.IDENTITY, funding='subscription')),
                                     job=launch_tests.job())
        self.assertEqual((result['state'], result['reason']), ('blocked', 'billing_unverified'), result)
        self.assertEqual(transport.calls, [])  # nothing sent, no second attempt
        self.assertEqual(self.service._ledger(False).snapshot()['holds']['a1']['state'], 'released')

    def test_a_current_fact_still_launches_exactly_once(self):
        transport = launch_tests.Transport()
        result = launcher.launch(self.service, host='codex_native', catalog=[launch_tests.IDENTITY],
                                 transport=transport, settings_path=self.settings,
                                 assignment=dict(attempt_id='a2', task='task-a2', scope=SCOPE,
                                                 choice=dict(launch_tests.IDENTITY, funding='subscription')),
                                 job=launch_tests.job())
        self.assertEqual(result['state'], 'recorded', result)
        self.assertEqual(len(transport.calls), 1)


if __name__ == '__main__':
    unittest.main()
