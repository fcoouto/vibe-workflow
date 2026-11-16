"""Task 4: no OpenCode selected-account identity is invented (all three families, real `LocalSource`).

The accepted source notes found no supported selected-account source for any OpenCode family: no secret-free
selected Claude subscriber/account projection (OpenCode 2.2.6), no projection binding the selected OpenCode `openai`
connection to a subscriber, and for Go no proven member/sign-in identity or provider-to-meter-connection linkage. So
the production `LocalSource` must refuse every OpenCode subscription route, even when the provider, account and pool
labels match a native login seeded on the same pool, the environment matches and stored rows exist. No fact, no
marker, no admission candidate, and no acquisition, process, model or network operation may turn those equalities
into account proof.

Real private stores and the real `LocalSource`; the native logins are labelled synthetic producer states. The separate
consumer proof at the end uses a labelled code-level fact, which is never a production producer.
"""
import json
import os
import time
import unittest
from unittest import mock

from runtime import claude_usage, codex_account, codex_rate_limits, routing_observations as collector, routing_paths, \
    subscription_billing
from tests import test_native_routing as native_tests
from tests.test_routing_observations import Stores, native_reading

FAMILIES = (  # (path, provider, harness, pool, account, demand, model)
    ('opencode-claude-subscription', 'claude-agent', 'claude-agent-sdk via opencode', 'claude-subscription',
     'claude-plan', {'5h': 5, '7d': 5}, 'invented-claude-small'),
    ('opencode-chatgpt-subscription', 'openai', 'opencode provider openai', 'chatgpt-subscription', 'chatgpt-plan',
     {'7d': 5}, 'invented-chatgpt-model'),
    ('opencode-go-subscription', 'opencode-go', 'opencode provider opencode-go', 'opencode-go-subscription',
     'opencode-go-workspace', {'rolling': 5, 'weekly': 5, 'monthly': 5}, 'invented-go-one'))


class RealSourceRefusalTest(Stores):
    """Every OpenCode family on the real `LocalSource`, with every equality a guess could lean on."""

    def setUp(self):
        super().setUp()
        now = int(time.time())
        native_reading(self.settings, [dict(id=window, limit=1000, used=100, length=length, reset_at=now + 3600,
                                            rolling=False, observed_at=now - 5, unit=collector.WINDOW_UNIT)
                                       for window, length in (('5h', 18000), ('7d', 604800))], now)  # native Claude
        key, generation = 'b' * 32, 'd' * 32  # a synthetic native Codex login on the shared ChatGPT pool
        codex_account._save(self.settings, identity=dict(key=key, generation=generation), account=dict(
            key=key, generation=generation, observed_at=now, client_version='unknown',
            usage_reason=codex_account.USAGE_UNCONFIRMED))
        for pool, windows in (('chatgpt-subscription', ('7d',)),
                              ('opencode-go-subscription', ('rolling', 'weekly', 'monthly'))):
            for window in windows:  # fresh unbound rows no producer links to a selected account
                collector.store_window(self.settings, pool, dict(id=window, limit=1000, used=100, length=604800,
                                                                 reset_at=now + 3600, rolling=False,
                                                                 observed_at=now - 5, unit=collector.WINDOW_UNIT))
        for path, *_rest, demand, _model in FAMILIES:
            routing_paths.qualify(self.settings, path, demand=demand, evidence='synthetic-1', now=now)
        home = str(self.root)
        self.enterContext(mock.patch.dict(os.environ, HOME=home, CLAUDE_CONFIG_DIR=home, CODEX_HOME=home,
                                          OPENCODE_CONFIG_DIR=home))  # matching invocation inputs, synthetic
        self.readers = [mock.Mock(side_effect=AssertionError('acquisition started')) for _ in range(4)]
        self.enterContext(mock.patch.object(claude_usage, 'refresh', self.readers[0]))
        self.enterContext(mock.patch.object(codex_account, 'refresh', self.readers[1]))
        self.enterContext(mock.patch.object(codex_rate_limits, 'refresh', self.readers[2]))
        self.enterContext(mock.patch.object(codex_rate_limits, 'read_windows', self.readers[3]))
        self.enterContext(mock.patch('subprocess.Popen', side_effect=AssertionError('process started')))

    def test_matching_labels_native_logins_and_environment_never_prove_an_opencode_account(self):
        source, now = collector.LocalSource(self.settings), int(time.time())
        self.assertIsNotNone(claude_usage.billing_fact(self.settings, dict(
            route='claude-native-subscription:s', account='claude-plan', pool='claude-subscription'), now))
        self.assertIsNotNone(codex_account.billing_fact(self.settings, dict(
            route='codex-chatgpt-subscription:s', account='chatgpt-plan', pool='chatgpt-subscription'), now))
        for path, provider, harness, pool, account, _demand, model in FAMILIES:
            with self.subTest(path=path):
                route = dict(route=path + ':choice:0123456789abcdef', account=account, pool=pool,
                             billing_class='included_subscription')
                self.assertIsNone(source.billing(route, now))
                fact = subscription_billing.fact(source, route, now)
                self.assertEqual((fact, subscription_billing.marker(fact, route, now)), (None, None))
                subscription_billing.acquire(source, [route], now)  # the source has no producer for it
                record = dict(route, binding=dict(v=1, job=None, credential='0' * 64))
                self.assertEqual(subscription_billing.dispatch_problem(record, self.settings, now),
                                 'billing_unverified')  # even a stored marked admission gets no permit
                found = collector.collect(str(self.root), dict(scope='s'), settings_path=self.settings,
                                          ledger_path=self.ledger_path, refresh_windows=True, choices=[dict(
                                              provider=provider, model=model, effort='high', harness=harness,
                                              funding='subscription')], harnesses=[harness])['observations']
                self.assertEqual(found['routes'], [])  # no candidate: no admission, permit or included claim
                self.assertEqual([item['reason'] for item in found['excluded']], ['billing_unverified'])
        for reader in self.readers:
            reader.assert_not_called()


class SyntheticConsumerTest(native_tests.NativeBase):
    """CONSUMER proof only, with a labelled code-level selected-connection fact (never a production producer):
    unknown quota/demand is provider-managed fallback, not an identity failure; a changed connection or account
    generation after admission refuses the unsent dispatch."""

    def setUp(self):
        super().setUp()
        self.producer, double = 'synthetic:opencode-connection-a:generation-1', subscription_billing.double('oc')
        self.enterContext(mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now: dict(
            double(route, now), producer=self.producer)))
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand=None, evidence='synthetic-1',
                              now=self.now)  # unknown demand

    def prepare(self, attempt):
        from scripts import routing as command
        service = command.Service(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(service.close)
        request = native_tests.routing_launch.prepare_request(
            dict(attempt_id=attempt, task='task-' + attempt,
                 scope=native_tests.routing_launch.assignment_scope(self.checkout),
                 choice=dict(provider='claude-agent', model='invented-claude-small', effort='low',
                             harness='claude-agent-sdk via opencode', funding='subscription')), self.job(),
            harnesses=['claude-agent-sdk via opencode'])
        return service, service.handle(json.dumps(request).encode())

    def test_unknown_quota_is_fallback_and_a_changed_connection_refuses_the_unsent_dispatch(self):
        service, held = self.prepare('oc1')
        self.assertEqual(held['state'], 'admitted', held)
        self.assert_no_claim('oc1')  # account-eligible, quota unknown: provider-managed fallback, no included claim
        facts = dict(provider='claude-agent', model='invented-claude-small', variant='low',
                     harness='claude-agent-sdk via opencode', kind='primary', funding_pool='claude-subscription')
        for producer in ('synthetic:opencode-connection-b:generation-1',
                         'synthetic:opencode-connection-a:generation-2'):
            with self.subTest(producer=producer):
                self.producer = producer  # another selected connection, or the same one in a new generation
                sent = service.handle(json.dumps(dict(v=1, op='dispatch', attempt_id='oc1', request=facts)).encode())
                self.assertEqual((sent['state'], sent.get('reason')), ('blocked', 'account_changed'), sent)
                self.assertNotIn('authority', sent)
        self.assertEqual(self.holds()['oc1']['state'], 'admitted')  # unsent: no permit was issued
        self.producer = 'synthetic:opencode-connection-a:generation-1'
        sent = service.handle(json.dumps(dict(v=1, op='dispatch', attempt_id='oc1', request=facts)).encode())
        self.assertEqual(sent['state'], 'dispatched', sent)  # the same connection: exactly one permit


if __name__ == '__main__':
    unittest.main()
