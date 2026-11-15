"""Admission-bound account continuity at the shared permit boundary (account-continuity design §3 and §5, Task 3B).

Every subscription admission, included or provider-managed fallback, keeps a marker of the account identity its
decision used (`subscription_billing.marker`, in the existing `binding.credential` slot), and the shared dispatch
transaction refuses a permit when that identity is missing, unverifiable or different now
(`subscription_billing.dispatch_problem`). The collector keeps the fact it verified and refuses a route whose identity
changed while it read. It never borrows the legacy pool reader or another login's unbound rows for an included claim.

Real routing services, `LocalSource` and temporary stores. Account facts come only from the labelled code-level
double (`subscription_billing.double`) patched onto `LocalSource.billing`, whose producer each test switches to
model an observed account or generation change. No client, account, credential or network is reached.
"""
import json
import time
import unittest
from unittest import mock

from runtime import codex_rate_limits, native_routing, routing, routing_gates, routing_observations as collector, \
    routing_paths, subscription_billing
from tests import test_native_routing as native_tests, test_routing_command as command_tests

ROUTE = dict(route='claude-native-subscription:s1', account='claude-plan', pool='claude-subscription',
             billing_class='included_subscription')
A1, B1, A2 = 'synthetic:account-a:generation-1', 'synthetic:account-b:generation-1', 'synthetic:account-a:generation-2'
SNAPSHOT_FACT = subscription_billing.double('caller-snapshot')


def trusted_snapshots():
    """Patches for modules whose tests admit caller snapshots: a test-only CODE seam standing in for trusted collection
    AFTER the request boundary. `Service._observations` first drops any caller marker (the request never carries
    identity); then each caller subscription route gets the marker of the labelled synthetic fact `LocalSource.billing`
    answers while the patches are active, as trusted collection would have supplied it."""
    from scripts import routing as command
    real = command.Service._observations

    def observations(service, value, caller=False):
        found = real(service, value, caller=caller)
        if not caller or type(found) is not dict or type(found.get('routes')) is not list:
            return found
        at = int(time.time())
        return dict(found, routes=[dict(route, account_marker=subscription_billing.marker(SNAPSHOT_FACT(route, at),
                                                                                         route, at))
                                   if type(route) is dict and route.get('billing_class') == 'included_subscription'
                                   else route for route in found['routes']])
    return [mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now: SNAPSHOT_FACT(route, now)),
            mock.patch.object(command.Service, '_observations', observations)]


class MarkerTest(unittest.TestCase):
    def test_the_marker_names_the_validated_identity_only(self):
        now = int(time.time())
        fact = subscription_billing.double('a')(ROUTE, now)
        marker = subscription_billing.marker(fact, ROUTE, now)
        self.assertRegex(marker, '^[0-9a-f]{64}$')
        later = dict(fact, observed_at=now - 5, valid_until=now + 60, paid_crossover='unknown')
        self.assertEqual(subscription_billing.marker(later, ROUTE, now), marker)  # no time, expiry or overflow
        self.assertNotEqual(subscription_billing.marker(dict(fact, producer='synthetic:b'), ROUTE, now), marker)
        for invalid in (None, dict(fact, observed_at=now - 10_000), dict(fact, pool='other'), dict(fact, extra=1)):
            self.assertIsNone(subscription_billing.marker(invalid, ROUTE, now))
        source = type('Source', (), dict(billing=lambda self, route, at: fact))()
        self.assertEqual(subscription_billing.fact(source, ROUTE, now), fact)
        self.assertIsNone(subscription_billing.fact(object(), ROUTE, now))
        self.assertTrue(subscription_billing.verified(source, ROUTE, now))

    def test_dispatch_problem_names_missing_unverified_and_changed_identity(self):
        now = int(time.time())
        record = dict(route=ROUTE['route'], account='claude-plan', pool='claude-subscription',
                      billing_class='included_subscription', binding=dict(v=1, job=None, credential=None))
        double = subscription_billing.double('a')
        marker = subscription_billing.marker(double(ROUTE, now), ROUTE, now)
        with mock.patch.object(collector.LocalSource, 'billing', lambda source, route, at: double(route, at)):
            self.assertEqual(subscription_billing.dispatch_problem(record, None, now), 'account_binding_missing')
            marked = dict(record, binding=dict(record['binding'], credential=marker))
            self.assertEqual(subscription_billing.dispatch_problem(marked, None, now), None)
            changed = dict(record, binding=dict(record['binding'], credential='0' * 64))
            self.assertEqual(subscription_billing.dispatch_problem(changed, None, now), 'account_changed')
        with mock.patch.object(collector.LocalSource, 'billing', lambda source, route, at: None):
            self.assertEqual(subscription_billing.dispatch_problem(marked, None, now), 'billing_unverified')
        funded = dict(record, billing_class='direct_api')
        self.assertIsNone(subscription_billing.dispatch_problem(funded, None, now))  # funded: its own credential gate


class DispatchContinuityTest(native_tests.NativeBase):
    """The real one-shot bridge (`native_prepare`) on LocalSource: admission and the shared dispatch transaction."""

    def setUp(self):
        super().setUp()
        self.producer, double = A1, subscription_billing.double('continuity')
        self.enterContext(mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now: (
            None if self.producer is None else dict(double(route, now), producer=self.producer))))

    def switched(self, producer):
        """Change the account identity after the ledger admission, before the bridge asks for the permit."""
        real, done = native_routing._call, []

        def call(service, request):
            answer = real(service, request)
            if answer.get('state') == 'admitted' and not done:
                done.append(True)
                self.producer = producer
            return answer
        return mock.patch.object(native_routing, '_call', call)

    def record(self, attempt):
        return next(item for item in self.service._ledger(False).snapshot()['admissions']
                    if item['attempt_id'] == attempt)

    def refused(self, attempt, producer, reason):
        tool = native_tests.NativeTool()
        with self.switched(producer):
            permit, done = self.run_native('claude-code', tool, attempt=attempt)
        self.assertEqual((permit['state'], permit.get('reason'), tool.calls, done), ('blocked', reason, [], None),
                         permit)
        self.assertNotEqual(self.holds()[attempt]['state'], 'dispatched')
        self.assertRegex(self.record(attempt)['binding']['credential'], '^[0-9a-f]{64}$')

    def test_a_marked_admission_dispatches_on_the_same_identity(self):
        tool = native_tests.NativeTool()
        permit, done = self.run_native('claude-code', tool)
        self.assertEqual((permit['state'], len(tool.calls)), ('dispatched', 1), permit)
        now = int(time.time())
        route = dict(route=self.record('n1')['route'], account='claude-plan', pool='claude-subscription')
        expected = subscription_billing.marker(subscription_billing.double('x')(route, now) | dict(producer=A1),
                                               route, now)
        self.assertEqual(self.record('n1')['binding']['credential'], expected)

    def test_same_second_a_b_and_a_b_a_issue_no_permit_and_a_new_attempt_qualifies(self):
        self.enterContext(mock.patch('time.time', return_value=float(int(time.time()))))
        self.refused('b1', B1, 'account_changed')
        self.producer = A1
        self.refused('a2', A2, 'account_changed')  # A again, a new generation: never the first A's identity
        self.producer = B1
        tool = native_tests.NativeTool()
        self.assertEqual(self.run_native('claude-code', tool, attempt='fresh')[0]['state'], 'dispatched')

    def test_an_account_switch_never_blocks_settling_issued_or_uncertain_exposure(self):
        self.assertEqual(self.prepare('claude-code', 'd1')['state'], 'dispatched')
        self.producer = B1  # identity governs new permits only
        self.assertEqual(self.complete('d1', dict(kind='complete'))['state'], 'recorded')
        self.assertEqual(self.prepare('claude-code', 'd2')['state'], 'dispatched')  # a new attempt on B
        self.producer = A2
        done = self.complete('d2', dict(kind='unknown'))
        self.assertEqual((done['state'], done['disposition']), ('recorded', 'uncertain'))  # its exposure stays held
        self.assertEqual(self.holds()['d2']['state'], 'uncertain')

    def test_a_missing_fact_issues_no_permit(self):
        self.refused('m1', None, 'billing_unverified')

    def test_a_fallback_admission_is_checked_before_its_quota_fence(self):
        for window, length in (('5h', 18000), ('7d', 604800)):
            self.window('claude-subscription', window, length, used=1000, later=5)  # exhausted: provider fallback
        self.refused('f1', B1, 'account_changed')
        self.assertEqual(self.record('f1')['reason_codes'][-1], routing.FALLBACK)

    def test_an_unmarked_subscription_admission_is_refused_unsent(self):
        real = routing_gates.binding
        with mock.patch.object(routing_gates, 'binding', lambda job, credential: real(job, None)):  # an old writer
            tool = native_tests.NativeTool()
            permit, done = self.run_native('claude-code', tool, attempt='legacy')
        self.assertEqual((permit['state'], permit.get('reason'), tool.calls), ('blocked', 'account_binding_missing', []))
        self.assertIsNone(self.record('legacy')['binding']['credential'])

    def test_collection_refuses_an_identity_that_changed_while_it_read(self):
        from runtime import claude_usage
        real = claude_usage.usable_windows

        def read(*args, **kwargs):
            self.producer = B1  # another identity is current by the time the readings are read
            return real(*args, **kwargs)
        tool = native_tests.NativeTool()
        with mock.patch.object(claude_usage, 'usable_windows', read):
            permit, done = self.run_native('claude-code', tool, attempt='c1')
        self.assertEqual((permit['state'], tool.calls), ('pending', []), permit)
        self.producer = A1
        choice = dict(provider='anthropic', model='invented-small', effort='high', harness='claude-native',
                      funding='subscription')
        with mock.patch.object(claude_usage, 'usable_windows', read):
            found = collector.collect(str(self.checkout), dict(scope='s'), settings_path=self.settings,
                                      ledger_path=self.ledger_path, choices=[choice], harnesses=['claude-native'])
        self.assertEqual(found['observations']['routes'], [])
        self.assertEqual([item['reason'] for item in found['observations']['excluded']], ['account_changed'])


    def test_a_codex_route_without_windows_is_still_rechecked(self):
        from runtime import codex_account
        self.enterContext(mock.patch.object(codex_account, 'usable_windows', native_tests.REAL_CODEX_WINDOWS))

        def read(*args, **kwargs):  # no window at all; the identity still changes while the snapshot is read
            self.producer = B1
            return []
        choice = dict(provider='openai', model='invented-codex-small', effort='high', harness='codex-native',
                      funding='subscription')
        with mock.patch.object(codex_account, 'usable_windows', read):
            found = collector.collect(str(self.checkout), dict(scope='s'), settings_path=self.settings,
                                      ledger_path=self.ledger_path, choices=[choice], harnesses=['codex-native'])
        self.assertEqual(found['observations']['routes'], [])
        self.assertEqual([item['reason'] for item in found['observations']['excluded']], ['account_changed'])


class CallerBoundaryTest(native_tests.NativeBase):
    """Ruling 2: only trusted collection supplies admission identity. A caller snapshot's `account_marker` is dropped
    at the request boundary in every mode, even when it equals the current fact's marker."""

    def snapshot(self, at):
        route = dict(route='sub', strategy_id='s-sub', provider='anthropic', model='m', effort='high', harness='h',
                     account='anthropic:acct', billing_class='included_subscription', pool='sub', demand={'5h': 10},
                     capable=True, permitted=True, binding_enforceable=True)
        route['account_marker'] = subscription_billing.marker(SNAPSHOT_FACT(route, at), route, at)
        window = dict(id='5h', limit=1000, used=100, length=18000, reset_at=None, rolling=True, fresh=True)
        return dict(now=at, routes=[route], pools=dict(sub=dict(windows=[window])))

    def test_the_boundary_strips_caller_markers_in_both_modes_and_keeps_trusted_ones(self):
        from scripts import routing as command
        at = int(time.time())
        for test_inputs in (False, True):
            with self.subTest(test_inputs=test_inputs):
                service = command.Service(self.settings, self.ledger_path, test_inputs=test_inputs)
                self.addCleanup(service.close)
                supplied = self.snapshot(at)
                found = service._observations(supplied, caller=True)
                self.assertNotIn('account_marker', found['routes'][0])
                self.assertIn('account_marker', supplied['routes'][0])  # the supplied object is unchanged
                self.assertIn('account_marker', service._observations(self.snapshot(at))['routes'][0])  # trusted


class SmuggledMarkerTest(command_tests.Base):
    """A raw caller request carrying the exact marker of the current labelled fact still gets no trusted binding."""

    def test_a_smuggled_matching_marker_gets_no_binding_and_no_permit(self):
        from tests.test_routing_dispatch import dispatch
        self.enterContext(mock.patch.object(collector.LocalSource, 'billing',
                                            lambda source, route, now: SNAPSHOT_FACT(route, now)))
        obs = command_tests.observations()
        route = obs['routes'][0]
        route['account_marker'] = subscription_billing.marker(SNAPSHOT_FACT(route, obs['now']), route, obs['now'])
        admitted = self.call(command_tests.prepare('smuggled', observations=obs,
                                                   constraints=dict(allowed_strategy_ids=['s-sub'])))
        self.assertEqual(admitted['state'], 'admitted', admitted)
        record = next(item for item in self.service._ledger(False).snapshot()['admissions']
                      if item['attempt_id'] == 'smuggled')
        self.assertIsNone(record['binding']['credential'])  # never a trusted account binding
        sent = self.call(dispatch('smuggled', request=command_tests.facts()))
        self.assertEqual((sent['state'], sent.get('reason')), ('blocked', 'account_binding_missing'), sent)


class GoConstraintAllocatorTest(unittest.TestCase):
    """Ruling 1, preserved proof: the Go three-constraint and shared-allowance accounting on CODE-SELECTED synthetic
    observations (the consumer contract a future account-bound Go source would feed), separate from the real
    LocalSource negative (`ProvenanceTest`). Synthetic consumer proof only, never a real Go meter or account."""
    POOL = 'opencode-go-subscription'

    def candidate(self, used, held=None):
        now = int(time.time())
        route = dict(route=self.POOL + ':s-go1', pool=self.POOL, account='opencode-go-workspace',
                     billing_class='included_subscription', capable=True, permitted=True, binding_enforceable=True,
                     demand=dict(rolling=5, weekly=5, monthly=5), provider_fallback=True, provider='opencode-go',
                     model='invented-go-one', effort='high', harness='opencode provider opencode-go')
        windows = [dict(id=name, limit=1000, used=used.get(name, 10), fresh=True, reset_at=now + 3600)
                   for name in ('rolling', 'weekly', 'monthly')]
        observations = dict(now=now, routes=[route], pools={self.POOL: dict(windows=windows)})
        return routing._candidate(dict(organizations={}), route, observations, dict(scope='s'),
                                  dict(api_usage_enabled=False, margin_bp=0), held or {}, now)

    def test_each_of_the_three_constraints_limits_the_included_claim(self):
        item, reasons = self.candidate({})
        self.assertEqual((reasons, item['fallback']), ([], False))
        self.assertEqual(set(item['vector']), {routing.WIN + self.POOL + '/' + name
                                               for name in ('rolling', 'weekly', 'monthly')})
        for name in ('rolling', 'weekly', 'monthly'):
            with self.subTest(full=name):
                item, reasons = self.candidate({name: 999})  # one window too full for the demand
                self.assertEqual((reasons, item['fallback'], item['vector']), ([], True, {}))

    def test_the_shared_allowance_admits_one_claim_then_only_fallback(self):
        first, _ = self.candidate(dict(rolling=992))  # room for one reservation of 5, not two
        self.assertFalse(first['fallback'])
        second, _ = self.candidate(dict(rolling=992), held=dict(first['vector']))  # the first alias still holds it
        self.assertEqual((second['fallback'], second['vector']), (True, {}))


class ProvenanceTest(native_tests.NativeBase):
    """Confirmed collector defects: no legacy pool reader and no unbound row for an OpenCode included claim."""

    def setUp(self):
        super().setUp()
        self.billing()  # a labelled synthetic fact for the OpenCode paths: proves the account, never its readings

    def collect(self, path, choice, harness, demand):
        routing_paths.qualify(self.settings, path, demand=demand, evidence='synthetic-1', now=int(time.time()))
        return collector.collect(str(self.checkout), dict(scope='s'), settings_path=self.settings,
                                 ledger_path=self.ledger_path, refresh_windows=True, choices=[choice],
                                 harnesses=[harness])['observations']

    def test_no_legacy_codex_reader_runs_for_an_opencode_alias(self):
        legacy = mock.Mock(return_value=dict(state='fresh'))
        with mock.patch.object(codex_rate_limits, 'refresh', legacy):
            found = self.collect('opencode-chatgpt-subscription', dict(
                provider='openai', model='invented-gpt', effort='high', harness='opencode provider openai',
                funding='subscription'), 'opencode provider openai', {'7d': 5})
        legacy.assert_not_called()
        self.assertEqual([route['demand'] for route in found['routes']], [None])  # account fallback only

    def test_unbound_rows_never_make_an_opencode_included_claim(self):
        found = self.collect('opencode-claude-subscription', dict(
            provider='claude-agent', model='invented-claude-small', effort='low',
            harness='claude-agent-sdk via opencode', funding='subscription'), 'claude-agent-sdk via opencode',
            {'5h': 5, '7d': 5})
        self.opencode_reading()  # fresh unbound readings of the shared pool, reported by no proved account
        found = self.collect('opencode-claude-subscription', dict(
            provider='claude-agent', model='invented-claude-small', effort='low',
            harness='claude-agent-sdk via opencode', funding='subscription'), 'claude-agent-sdk via opencode',
            {'5h': 5, '7d': 5})
        self.assertEqual([route['demand'] for route in found['routes']], [None])  # eligible, fallback only
        self.assertTrue(all('account_marker' in route for route in found['routes']))


if __name__ == '__main__':
    unittest.main()
