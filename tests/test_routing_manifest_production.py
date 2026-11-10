"""The production route manifest (C.3 design 3.3 manifest shape, 7.1 user decision 5): its invariants.

Decision 5 (2026-11-10): routes come from the capability matrix, subscription-window billing only. These tests read
the shipped `runtime/routing_routes.json`; they make no network or model call and touch no store.
"""
import json
import unittest

from runtime import routing, routing_launch, routing_observations as collector

# Models the committed evidence shows refused for the account (400 model_not_found): never listed.
REFUSED_MODELS = {'gpt-5.5', 'gpt-5.5-fast'}
# Routes that draw on one provider usage pool (user confirmation, 2026-11-10; capability matrix).
SHARED = {'claude-subscription': {'opencode-claude-agent-haiku-low'},
          'chatgpt-subscription': {'opencode-openai-gpt-6-luna-none', 'codex-native'}}
WINDOW_LENGTHS = {'5h': 5 * 3600, '7d': 7 * 86400}
QUALIFIED_STATUS = 'qualified'


def manifest():
    return json.loads(collector.MANIFEST.read_text())


def production(value=None):
    return [route for route in (value or manifest())['routes'] if route['status'] != 'test_only']


class ProductionManifestTests(unittest.TestCase):
    def test_shape_is_the_manifest_object_with_its_window_unit(self):
        value = manifest()
        self.assertIs(type(value), dict)
        self.assertEqual(value['version'], 1)
        self.assertEqual(value['window_unit'], collector.WINDOW_UNIT)
        self.assertEqual(collector.load_manifest(), value)  # the reader accepts it, endpoint rule included
        names = [route['route'] for route in value['routes']]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(value['decision'].startswith('accepted: C.3 design 7.1 user decision 5'))

    def test_the_test_only_route_is_kept_and_never_qualified(self):
        test_only = [route for route in manifest()['routes'] if route['status'] == 'test_only']
        self.assertEqual([route['route'] for route in test_only], ['test-only-subscription'])

    def test_only_subscription_window_routes_are_listed(self):
        """No dollar, direct API or gateway route, qualified or not, until a live qualification with real costs."""
        for route in manifest()['routes']:
            self.assertEqual(route['billing_class'], 'included_subscription', route['route'])
            self.assertNotIn(route['billing_class'], routing.FUNDED)
            self.assertNotIn('endpoint', route)
            self.assertNotIn(route.get('host'), ('openrouter_proposal', 'openrouter_worker', 'api_usage'))

    def test_endpoint_uniqueness_holds(self):
        self.assertEqual(collector.validate_routes(manifest()['routes']), manifest()['routes'])

    def test_identities_use_the_shim_and_host_names(self):
        for route in production():
            self.assertIn(route['host'], routing_launch.HOSTS)
            if route['model'] is None:
                self.assertEqual(route['reason'], 'model_unobserved')
                continue
            identity = routing_launch.route_identity(route['host'], provider=route['provider'], model=route['model'],
                                                     effort=route['effort'])
            self.assertEqual({key: route[key] for key in routing.ROUTE_IDENTITY}, identity)

    def test_shared_pool_routes_name_the_same_pool_and_account(self):
        value = manifest()
        for pool, members in SHARED.items():
            routes = [route for route in value['routes'] if route['route'] in members]
            self.assertEqual({route['route'] for route in routes}, members)
            self.assertEqual({route['pool'] for route in routes}, {pool})
            self.assertEqual({route['account'] for route in routes}, {value['pools'][pool]['account']})
        for route in production(value):  # one account per pool, so per-account caps count a pool once
            self.assertEqual(route['account'], value['pools'][route['pool']]['account'])

    def test_pool_windows_carry_their_real_lengths(self):
        for name, pool in manifest()['pools'].items():
            ids = [window['id'] for window in pool['windows']]
            self.assertTrue(ids, name)
            self.assertEqual(len(ids), len(set(ids)))
            for window in pool['windows']:
                self.assertEqual(window['length'], WINDOW_LENGTHS[window['id']], name)

    def test_no_refused_model_is_listed(self):
        for route in manifest()['routes']:
            self.assertNotIn(route['model'], REFUSED_MODELS)

    def test_codex_native_is_an_advisory_host(self):
        for route in production():
            self.assertEqual(route.get('advisory', False), route['host'] in routing_launch.ADVISORY_HOSTS,
                             route['route'])

    def test_every_route_is_qualified_with_a_justified_demand_or_names_a_fixed_reason(self):
        value = manifest()
        for route in production(value):
            if route['status'] == QUALIFIED_STATUS:
                self.assertIsNone(collector._manifest_reason(dict(route, window_unit=value['window_unit'])))
                windows = {window['id'] for window in value['pools'][route['pool']]['windows']}
                self.assertTrue(route['demand'] and set(route['demand']) <= windows, route['route'])
                self.assertTrue(all(type(item) is int and item > 0 for item in route['demand'].values()))
                self.assertNotIn('reason', route)
            else:
                self.assertEqual(route['status'], 'unverified', route['route'])
                self.assertIn(route['reason'], value['reasons'])
                self.assertIsNone(route['demand'])

    def test_manifest_check_agrees_with_the_statuses(self):
        qualified = any(route['status'] == QUALIFIED_STATUS for route in manifest()['routes'])
        self.assertEqual(collector.manifest_check(), [] if qualified else ['route_manifest_unqualified'])


if __name__ == '__main__':
    unittest.main()
