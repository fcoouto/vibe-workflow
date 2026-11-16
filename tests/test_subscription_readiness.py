"""Task 5: source readiness for all five subscription paths, separate from included readiness and live authority.

Regression proof on the real `LocalSource` and real stores (the readiness interfaces already met the contract, so
these are not test-first): every path needs its own current account fact (and a native one also its bound catalog);
the three OpenCode paths stay refused even when both native sources are ready, until the OpenCode producer's own fresh
fact exists (Task 6, `runtime/opencode_account.py`, read here through its labelled synthetic double); account
and launch readiness never needs quota, while included optimization still does (an account with unknown quota is
admitted only as provider-managed fallback, elsewhere); and status/readiness reads start no reader, process, provider
or model call. Native logins are labelled synthetic producer states; nothing here is live qualification.
"""
import json
import subprocess
import time
import unittest
from unittest import mock

from runtime import claude_usage, codex_account, codex_rate_limits, native_routing, opencode_account, routing_launch, \
    routing_observations as collector, routing_paths, subscription_billing
from runtime.adapters import native_catalog
from tests.test_routing_observations import Stores, native_reading

PATHS = {  # every shipped subscription path name: demand (for qualification only) and its native client, if any
    'claude-native-subscription': ({'5h': 5, '7d': 5}, 'claude-code'),
    'codex-chatgpt-subscription': ({'7d': 5}, 'codex'),
    'opencode-claude-subscription': ({'5h': 5, '7d': 5}, None),
    'opencode-chatgpt-subscription': ({'7d': 5}, None),
    'opencode-go-subscription': ({'rolling': 5, 'weekly': 5, 'monthly': 5}, None)}
OPENCODE = tuple(name for name in PATHS if name.startswith('opencode-'))
READ, POPEN = opencode_account.refresh, subprocess.Popen  # kept before the guards below replace them


class ReadinessTest(Stores):
    def setUp(self):
        super().setUp()
        now = int(time.time())
        for path, (demand, _client) in PATHS.items():
            routing_paths.qualify(self.settings, path, demand=demand, evidence='synthetic-1', now=now)
        self.readers = [mock.Mock(side_effect=AssertionError('acquisition started')) for _ in range(5)]
        for target, name, reader in ((claude_usage, 'refresh', self.readers[0]),
                                     (codex_account, 'refresh', self.readers[1]),
                                     (codex_rate_limits, 'refresh', self.readers[2]),
                                     (codex_rate_limits, 'read_windows', self.readers[3]),
                                     (opencode_account, 'refresh', self.readers[4])):
            self.enterContext(mock.patch.object(target, name, reader))
        self.enterContext(mock.patch('subprocess.Popen', side_effect=AssertionError('process started')))

    def paths(self, name):
        rules = routing_paths.load_rules()
        return [item for item in routing_paths.resolved(rules, routing_paths.qualifications(self.settings))
                if item['path'] == name]

    def usable(self, name, now=None):
        _demand, client = PATHS[name]
        return native_routing._usable(self.paths(name), self.settings, client, client is not None,
                                      int(time.time()) if now is None else now)

    def seed_opencode_sources(self):
        """The OpenCode producer's own bounded read through its labelled synthetic `opencode` double."""
        from tests.test_opencode_account import FakeOpenCode
        fake = FakeOpenCode(self.root / 'fake-opencode')
        with mock.patch('subprocess.Popen', POPEN):
            self.assertEqual(READ(self.settings, command=fake.command)['state'], 'stored')

    def seed_native_sources(self, catalogs=True):
        """Labelled synthetic native producer states: a generation-bound Claude account (with rows) and a Codex one,
        and, when `catalogs`, each client's catalog bound to its current account binding."""
        now = int(time.time())
        native_reading(self.settings, [dict(id=window, limit=1000, used=100, length=length, reset_at=now + 3600,
                                            rolling=False, observed_at=now - 5, unit=collector.WINDOW_UNIT)
                                       for window, length in (('5h', 18000), ('7d', 604800))], now)
        codex_account._save(self.settings, identity=dict(key='b' * 32, generation='d' * 32), account=dict(
            key='b' * 32, generation='d' * 32, observed_at=now, client_version='unknown',
            usage_reason=codex_account.USAGE_UNCONFIRMED))
        if catalogs:
            for client, producer in (('claude-code', claude_usage), ('codex', codex_account)):
                native_catalog.store_snapshot(self.settings, client, dict(binding=producer.binding(self.settings),
                                                                          models=[dict(alias='invented-model',
                                                                                       resolved=None,
                                                                                       efforts=['high'])]))

    def test_all_five_paths_need_their_own_source_and_only_supported_ones_can_be_ready(self):
        cold = {name: self.usable(name) for name in PATHS}
        self.assertEqual(cold, dict.fromkeys(PATHS, subscription_billing.UNVERIFIED))  # nothing is ready cold
        self.seed_native_sources(catalogs=False)
        self.assertEqual({name: self.usable(name) for name in PATHS}, dict(
            dict.fromkeys(PATHS, subscription_billing.UNVERIFIED),
            **{'claude-native-subscription': 'native_catalog_unavailable',
               'codex-chatgpt-subscription': 'native_catalog_unavailable'}))  # an account without its catalog
        self.seed_native_sources()
        ready = {name: self.usable(name) for name in PATHS}
        self.assertEqual(ready, dict(dict.fromkeys(PATHS, subscription_billing.UNVERIFIED),
                                     **{'claude-native-subscription': None, 'codex-chatgpt-subscription': None}))
        self.assertEqual(codex_account.usable_windows(self.settings, int(time.time())), [])  # ready, quota unknown
        for reader in self.readers:
            reader.assert_not_called()

    def test_the_opencode_paths_are_ready_only_with_the_producers_fresh_fact(self):
        self.seed_native_sources()
        self.assertEqual({name: self.usable(name) for name in OPENCODE},
                         dict.fromkeys(OPENCODE, subscription_billing.UNVERIFIED))
        self.seed_opencode_sources()
        self.assertEqual({name: self.usable(name) for name in PATHS}, dict.fromkeys(PATHS))  # every path ready
        stale = int(time.time()) + subscription_billing.FRESH_SECONDS + 1
        self.assertEqual({name: self.usable(name, stale) for name in OPENCODE},
                         dict.fromkeys(OPENCODE, subscription_billing.UNVERIFIED))  # a stale fact proves nothing
        for reader in self.readers:
            reader.assert_not_called()  # readiness reads never acquire

    def test_account_readiness_needs_no_quota_while_included_readiness_still_does(self):
        self.seed_native_sources()
        self.assertIsNone(self.usable('codex-chatgpt-subscription'))  # account and catalog: launch-ready
        choice = dict(provider='openai', model='invented-model', effort='high', harness='codex-native',
                      funding='subscription')
        found = collector.collect(str(self.root), dict(scope='s'), settings_path=self.settings,
                                  ledger_path=self.ledger_path, choices=[choice], harnesses=['codex-native'])
        routes = found['observations']['routes']
        self.assertEqual([(route['route'].split(':')[0], route['demand']) for route in routes],
                         [('codex-chatgpt-subscription', None)])  # no qualified quota source: fallback only
        self.assertTrue(all(route['provider_fallback'] for route in routes))

    def test_status_and_readiness_reads_are_pure(self):
        from scripts import routing as command
        self.seed_native_sources()
        service = command.Service(self.settings, self.ledger_path)
        self.addCleanup(service.close)
        status = service.handle(json.dumps(dict(v=1, op='status')).encode())
        readiness = routing_launch.readiness(status)
        self.assertEqual(set(readiness), {'enabled', 'ready', 'reason'})  # enabled and ready, never inferred
        self.assertEqual(native_routing.activation_readiness([], self.settings), [])  # no project: nothing to grant
        for name in PATHS:
            self.usable(name)
        for reader in self.readers:
            reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
