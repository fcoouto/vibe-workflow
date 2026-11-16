"""Task 11: refuse-before-send quota gates (`quota_exhausted`) and scoped Claude limits, synthetic doubles only.

The gate refuses only on positive evidence: a fresh, account-bound reading with a limited or full window whose reset
is still ahead. It never creates headroom or an included claim; a stale, missing, passed or unknown reading never
gates. Native Claude keeps its account-wide windows and records model-scoped weekly limits, which apply only to routes
whose model they name (an unmatched scope makes every Claude route quota-unknown).
"""
import json
import os
import time
import unittest
from unittest import mock

from runtime import claude_usage, codex_account, opencode_account, opencode_launcher, routing_observations as collector, \
    routing_paths, subscription_billing, subscription_fallback
from tests import test_claude_usage as claude_tests
from tests.test_opencode_account import ORG, Base, credentials, go_usage, provider, route_of
from tests.test_routing_observations import Stores, native_reading

GO = route_of('go')


def native_route(model):
    return dict(route='claude-native-subscription:choice:0123456789abcdef', account='claude-plan',
                pool='claude-subscription', billing_class='included_subscription', model=model)


class GoGateTest(Base):
    def gate(self, now=None):
        return opencode_account.quota_problem(self.settings, GO, int(time.time()) if now is None else now)

    def test_a_fresh_bound_limited_or_full_meter_gates_until_its_reset(self):
        for meters, expected in ((dict(weekly=dict(limited=True)), 'quota_exhausted'),
                                 (dict(monthly=dict(percent=100)), 'quota_exhausted'),
                                 (dict(rolling=dict(percent=100, ahead=-5)), None),  # its reset has passed
                                 (dict(weekly=dict(percent=99)), None), ({}, None)):
            with self.subTest(meters=meters):
                self.fake.answer(credentials(), provider(), usage=go_usage(**meters))
                self.assertEqual(self.refresh(force=True)['families']['go'], None)
                self.assertIsNotNone(self.fact('go'))  # the account fact never depends on the meters
                self.assertEqual(self.gate(), expected)
        self.assertEqual(self.gate(int(time.time()) + 901), None)  # the reading is stale with its acquisition
        self.assertEqual(self.fake.calls().count('api --standalone GET /api/experimental/usage/limits'), 5)
        self.assert_clean()

    def test_an_unknown_shape_or_another_workspace_never_gates_and_keeps_the_account(self):
        full = go_usage(weekly=dict(limited=True, percent=100))
        extra = json.loads(json.dumps(full))
        extra['data']['go']['daily'] = extra['data']['go']['weekly']
        text = json.loads(json.dumps(full))
        text['data']['go']['weekly']['resetsAt'] = 'soon'
        for answer in (go_usage(org='org_' + 'Y' * 26, weekly=dict(limited=True)), extra, text,
                       dict(data=None), [full]):
            with self.subTest(answer=json.dumps(answer)[:60]):
                self.fake.answer(credentials(), provider(), usage=answer)
                self.refresh(force=True)
                self.assertIsNotNone(self.fact('go'))
                self.assertIsNone(self.gate())
        self.assert_clean()

    def test_only_the_go_family_reads_usage_and_other_families_never_gate(self):
        self.fake.answer(credentials(), provider(), usage=go_usage(weekly=dict(limited=True)))
        self.refresh(families=('chatgpt',))
        self.assertNotIn('api --standalone GET /api/experimental/usage/limits', self.fake.calls())
        self.refresh(force=True)
        self.assertEqual(opencode_account.quota_problem(self.settings, route_of('chatgpt'), int(time.time())), None)

    def test_a_listing_glitch_still_records_the_new_key_pending(self):
        """N3: the key derived from the credential rows is recorded (pending) before the listing read problem."""
        self.refresh()
        first = opencode_account._part(opencode_account.observations._read(opencode_account._store(self.settings)),
                                       'go')['identity']['key']
        self.fake.answer(credentials(go=dict(value=dict(type='key', key='SENTINEL-GO-KEY-2'))), provider(go=False))
        self.assertEqual(self.refresh(force=True)['families']['go'], 'opencode_go_provider_unlisted')
        part = opencode_account._part(opencode_account.observations._read(opencode_account._store(self.settings)),
                                      'go')
        self.assertNotEqual(part['identity']['key'], first)  # the observed change is kept, pending
        self.assertTrue(part['account'].get('pending'))
        self.assert_clean()

    def test_the_native_read_asks_for_windows_only_when_they_are_demanded(self):
        """D: a cold OpenCode Claude route asks the native reader for `5h`/`7d` only when it demands them."""
        source, calls = collector.LocalSource(self.settings), []

        def native(settings, **options):
            calls.append(options.get('need'))
            self.native(int(time.time()), 'a' * 32)
            return dict(state='stored', windows=[])
        with mock.patch.object(claude_usage, 'refresh', side_effect=native), \
                mock.patch.object(opencode_account, 'default_command', lambda environ: self.fake.command):
            for demand, need in ((None, ()), ({'5h': 5, '7d': 5}, ('5h', '7d'))):
                claude_usage._save(self.settings, account=dict(pending=True, generation='c' * 32), identity=None)
                opencode_account._transition(self.settings, 'claude', lambda part: dict(account=None, identity=None))
                subscription_billing.acquire(source, [dict(route_of('claude'), demand=demand)], int(time.time()))
                self.assertEqual(calls[-1], need)

    def test_the_reader_environment_matches_the_launcher(self):
        """N2: only XDG_DATA_HOME passes (launcher parity), and the flags are the launcher's own."""
        env = opencode_account.child_env(dict(PATH='/usr/bin', HOME='/h', XDG_DATA_HOME='/d', XDG_CONFIG_HOME='/c',
                                              XDG_STATE_HOME='/s'))
        self.assertEqual(env, dict(PATH='/usr/bin', HOME='/h', XDG_DATA_HOME='/d'))
        self.assertEqual(opencode_account.LAUNCH_FLAGS, opencode_launcher.SERVER_FLAGS)


class ClaudeScopeTest(claude_tests.Reader):
    """The real 2.1.311 shape with an exhausted model-scoped weekly limit (Task 10: "Fable" at 100%)."""

    def init(self):
        value = claude_tests.initialize()
        value['models'] = [dict(value='haiku', resolvedModel='claude-haiku-9', supportsEffort=True,
                                supportedEffortLevels=['low', 'high']),
                           dict(value='fable', resolvedModel='claude-fable-9-1', supportsEffort=True,
                                supportedEffortLevels=['low', 'high'])]
        return value

    def scoped(self, name='Fable', used=100, ahead=86400):
        reset = claude_tests.iso(self.now + ahead)
        value = claude_tests.real_usage(self.now)
        value['rate_limits']['model_scoped'] = [dict(display_name=name, utilization=used, resets_at=reset)]
        value['rate_limits']['limits'].append(dict(kind='weekly_scoped', group='weekly', percent=used, resets_at=reset,
                                                   severity='critical', is_active=True,
                                                   scope=dict(model=dict(display_name=name, id=None), surface=None)))
        value['rate_limits']['weekly_scoped_shares'] = [dict(allowance_percent_of_weekly=50, limit_index=2,
                                                            used_percent_of_weekly=50)]
        return value

    def state(self, model):
        return claude_usage.scope_state(self.settings, native_route(model), int(time.time()))

    def gate(self, model):
        return claude_usage.quota_problem(self.settings, native_route(model), int(time.time()))

    def test_a_versioned_scope_gates_its_whole_family(self):
        """H3: numbers are not family words, so an "Opus 4" scope gates every Opus route (a broader, refuse-only
        effect: at worst a false refusal), never Haiku or Sonnet."""
        value = self.init()
        value['models'] += [dict(value='opus', resolvedModel='claude-opus-4-1', supportsEffort=True,
                                 supportedEffortLevels=['low']),
                            dict(value='sonnet', resolvedModel='claude-sonnet-9', supportsEffort=True,
                                 supportedEffortLevels=['low'])]
        self.answer(value, self.scoped(name='Opus 4'))
        self.assertEqual(self.refresh()['state'], 'stored')
        self.assertEqual([self.gate(model) for model in ('opus', 'haiku', 'sonnet', 'fable')],
                         ['quota_exhausted', None, None, None])

    def test_a_fable_scope_gates_fable_routes_only_and_keeps_the_account_wide_windows(self):
        self.answer(self.init(), self.scoped())
        self.assertEqual(self.refresh(), dict(state='stored', windows=['5h', '7d']))
        self.assertTrue(claude_usage.usable(self.settings, int(time.time())))
        self.assertEqual((self.gate('fable'), self.gate('haiku')), ('quota_exhausted', None))
        self.assertEqual((self.state('fable'), self.state('haiku')), ('exhausted', None))
        self.assertIsNotNone(claude_usage.billing_fact(self.settings, native_route('haiku'), int(time.time())))

    def test_a_scope_below_its_limit_or_past_its_reset_only_withholds_its_model(self):
        for used, ahead in ((40, 86400), (100, -60)):
            with self.subTest(used=used, ahead=ahead):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(self.init(), self.scoped(used=used, ahead=ahead))
                self.refresh(force=True)
                self.assertEqual((self.state('fable'), self.state('haiku')), ('scoped', None))
                self.assertEqual(self.gate('fable'), None)  # never a gate without positive evidence

    def test_generic_or_too_many_scopes_are_unmatched_and_a_malformed_one_refuses(self):
        """H2 and the cap: a scope named only by generic words (`Claude 4`) or a list past the cap names no model, so
        every route is quota-unknown and nothing is refused; a malformed name refuses the reading."""
        for name in ('Claude 4', 'claude'):
            with self.subTest(name=name):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(self.init(), self.scoped(name=name))
                self.refresh(force=True)
                self.assertEqual((self.state('fable'), self.gate('fable'), self.gate('haiku')), ('scoped', None, None))
        many = self.scoped()
        many['rate_limits']['model_scoped'] = [dict(display_name='Model%s' % chr(97 + index), utilization=100,
                                                    resets_at=claude_tests.iso(self.now + 600)) for index in range(9)]
        claude_usage._store(self.settings).unlink(missing_ok=True)
        self.answer(self.init(), many)
        self.refresh(force=True)
        self.assertEqual((self.state('haiku'), self.gate('fable')), ('scoped', None))
        bad = self.scoped()
        bad['rate_limits']['model_scoped'] = [dict(display_name='', utilization=1,
                                                   resets_at=claude_tests.iso(self.now))]
        self.answer(self.init(), bad)
        self.assertEqual(self.refresh(force=True), dict(state='failed', reason='claude_limits_scoped'))

    def test_an_unmatched_scope_makes_every_claude_route_quota_unknown(self):
        self.answer(self.init(), self.scoped(name='Nimbus'))
        self.refresh()
        self.assertEqual((self.state('fable'), self.state('haiku')), ('scoped', 'scoped'))
        self.assertEqual((self.gate('fable'), self.gate('haiku')), (None, None))


class CodexGateTest(Stores):
    def seed(self, used_percent=50, reset_at=None, **quota):
        now = int(time.time())
        key, generation = 'b' * 32, 'd' * 32
        windows = [dict(used_percent=used_percent, reset_at=now + 3600 if reset_at is None else reset_at)]
        quota = dict(dict(windows=windows, ordinary_allowed=True, read_at=now), **quota)
        codex_account._save(self.settings, identity=dict(key=key, generation=generation), account=dict(
            key=key, generation=generation, observed_at=now, client_version='unknown',
            usage_reason=codex_account.USAGE_UNCONFIRMED, quota=quota))

    def gate(self):
        route = dict(route='codex-chatgpt-subscription:choice:0123456789abcdef', account='chatgpt-plan',
                     pool='chatgpt-subscription', billing_class='included_subscription')
        return codex_account.quota_problem(self.settings, route, int(time.time()))

    def test_spend_control_false_is_ordinary_and_true_is_refused(self):
        snapshot = dict(limitId='codex', primary=dict(usedPercent=99, windowDurationMins=10080,
                                                      resetsAt=int(time.time()) + 3600),
                        secondary=None, individualLimit=None, normalModelSlug=None, spendControlReached=False)
        limits = dict(rateLimits=snapshot, rateLimitsByLimitId=dict(codex=snapshot), ordinaryUsageAllowed=True)
        self.assertEqual(codex_account._usage_problem(limits), codex_account.USAGE_UNCONFIRMED)
        for value in (True, 'no'):
            changed = dict(snapshot, spendControlReached=value)
            self.assertEqual(codex_account._usage_problem(dict(limits, rateLimitsByLimitId=dict(codex=changed))),
                             'codex_limits_unsupported')
        self.assertEqual(codex_account._quota(limits, 7)['windows'][0]['used_percent'], 99)

    def test_a_malformed_window_never_hides_a_valid_full_one(self):
        """H4: the gate reads the well-formed windows; a malformed one alone never gates; an older flat stored
        reading (no `windows`) is no reading."""
        now = int(time.time())
        full, broken = dict(usedPercent=100, resetsAt=now + 600), dict(usedPercent='full', resetsAt=None)
        for primary, secondary, expected in ((full, broken, 'quota_exhausted'), (broken, broken, None),
                                             (broken, dict(usedPercent=10, resetsAt=now + 600), None)):
            with self.subTest(primary=primary, secondary=secondary):
                snapshot = dict(limitId='codex', primary=primary, secondary=secondary, spendControlReached=False)
                found = codex_account._quota(dict(rateLimitsByLimitId=dict(codex=snapshot)), now)
                if found is None:
                    self.assertIsNone(expected)
                    continue
                self.seed(**{key: value for key, value in found.items() if key != 'read_at'})
                self.assertEqual(self.gate(), expected)
        now = int(time.time())
        codex_account._save(self.settings, identity=dict(key='b' * 32, generation='d' * 32), account=dict(
            key='b' * 32, generation='d' * 32, observed_at=now, client_version='unknown',
            usage_reason=codex_account.USAGE_UNCONFIRMED,
            quota=dict(used_percent=100, reset_at=now + 600, ordinary_allowed=True, read_at=now)))
        self.assertIsNone(self.gate())  # the Task 11 flat shape: no `windows`, no reading, no KeyError

    def test_any_present_window_can_exhaust_and_an_unknown_shape_never_gates(self):
        """H1: the secondary window counts too, and a weekly-only plan may report it with no primary."""
        now = int(time.time())
        full, light = dict(usedPercent=100, resetsAt=now + 600), dict(usedPercent=10, resetsAt=now + 600)
        for primary, secondary, exhausted in ((light, full, True), (None, full, True), (light, None, False),
                                              (None, None, None)):
            with self.subTest(primary=primary, secondary=secondary):
                snapshot = dict(limitId='codex', primary=primary, secondary=secondary, spendControlReached=False)
                found = codex_account._quota(dict(rateLimitsByLimitId=dict(codex=snapshot)), now)
                if exhausted is None:
                    self.assertIsNone(found)
                    continue
                self.seed(**{key: value for key, value in found.items() if key != 'read_at'})
                self.assertEqual(self.gate(), 'quota_exhausted' if exhausted else None)

    def test_a_fresh_full_or_disallowed_reading_gates_for_fifteen_minutes(self):
        for quota, expected in ((dict(used_percent=100), 'quota_exhausted'),
                                (dict(ordinary_allowed=False), 'quota_exhausted'),
                                (dict(used_percent=100, reset_at=int(time.time()) - 5), None),
                                (dict(used_percent=100, read_at=int(time.time()) - 901), None),
                                (dict(used_percent=99), None)):
            with self.subTest(quota=quota):
                self.seed(**quota)
                self.assertEqual(self.gate(), expected)
        self.assertEqual(codex_account.QUOTA_FRESH_SECONDS, 900)
        self.assertEqual(codex_account.usable_windows(self.settings, int(time.time())), [])  # never a claim


class CollectorTest(Stores):
    """The collector refuses an exhausted route before admission; any other answer never refuses."""

    def collect(self, provider, model, harness):
        return collector.collect(str(self.root), dict(scope='s'), settings_path=self.settings,
                                 ledger_path=self.ledger_path, choices=[dict(
                                     provider=provider, model=model, effort='high', harness=harness,
                                     funding='subscription')], harnesses=[harness])['observations']

    def test_an_exhausted_route_is_excluded_and_a_failing_gate_never_refuses(self):
        double = subscription_billing.double('gate')
        routing_paths.qualify(self.settings, 'opencode-chatgpt-subscription', demand={'7d': 5}, evidence='synthetic-1',
                              now=int(time.time()))
        with mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now: double(route, now)):
            for answer, excluded in ((lambda source, route, now: 'quota_exhausted', ['quota_exhausted']),
                                     (mock.Mock(side_effect=RuntimeError('unreadable')), []),
                                     (lambda source, route, now: 'something_else', [])):
                with self.subTest(excluded=excluded), mock.patch.object(collector.LocalSource, 'quota', answer):
                    found = self.collect('openai', 'invented-chatgpt-model', 'opencode provider openai')
                    self.assertEqual([item['reason'] for item in found['excluded']], excluded)
                    self.assertEqual(len(found['routes']), 1 - len(excluded))

    def test_an_opencode_claude_completion_leaves_the_shared_native_rows_stale_for_every_alias(self):
        """Task 12 (F1): the boundary the closing `complete` writes for an OpenCode Claude claim (`terminal`) makes the
        native rows it drew on stale for the next claim; OpenCode and native Claude share this pool entry until a
        reading observed after it arrives."""
        now = int(time.time())
        rows = [dict(id=window, limit=1000, used=100, length=length, reset_at=now + 3600, rolling=False,
                     observed_at=now - 10, unit=collector.WINDOW_UNIT)
                for window, length in (('5h', 18000), ('7d', 604800))]
        native_reading(self.settings, rows, now)
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=now)
        double, record = subscription_billing.double('oc'), dict(
            route='opencode-claude-subscription:s', billing_class='included_subscription', pool='claude-subscription',
            reason_codes=['native_rows'])  # Task 14: the basis the collector records for this claim
        fresh = []
        with mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now: double(route, now)), \
                mock.patch.object(opencode_account, 'native_current', lambda settings, now: True):
            for step in ('claimable', 'completed', 'newer reading'):
                if step == 'completed':
                    self.assertTrue(subscription_fallback.terminal(record))
                    subscription_fallback.mark_terminal(self.settings, record, now - 5, 'oc-1')
                if step == 'newer reading':
                    native_reading(self.settings, [dict(row, observed_at=now - 2) for row in rows], now)
                found = self.collect('claude-agent', 'invented-claude-small', 'claude-agent-sdk via opencode')
                fresh.append([window['fresh'] for window in found['pools']['claude-subscription']['windows']])
        self.assertEqual(fresh, [[True, True], [False, False], [True, True]])

    def test_only_the_production_source_with_a_current_native_login_records_the_native_basis(self):
        """Task 14: the claim's basis is decided at collection, never at completion: `native_rows` only on the exact
        LocalSource with a current native login; a LocalSource subclass (a code-selected source) never sets it."""
        now = int(time.time())
        native_reading(self.settings, [dict(id=window, limit=1000, used=100, length=length, reset_at=now + 3600,
                                            rolling=False, observed_at=now - 5, unit=collector.WINDOW_UNIT)
                                       for window, length in (('5h', 18000), ('7d', 604800))], now)
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=now)
        double, subclass = subscription_billing.double('oc'), type('Covering', (collector.LocalSource,), {})
        with mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now: double(route, now)):
            for current, source, basis in ((True, collector.LocalSource, True), (False, collector.LocalSource, None),
                                           (True, subclass, None)):
                with self.subTest(current=current, source=source.__name__), \
                        mock.patch.object(opencode_account, 'native_current', lambda settings, now: current):
                    found = collector.collect(str(self.root), dict(scope='s'), settings_path=self.settings,
                                              ledger_path=self.ledger_path, source=source(self.settings), choices=[dict(
                                                  provider='claude-agent', model='invented-claude-small', effort='high',
                                                  harness='claude-agent-sdk via opencode', funding='subscription')],
                                              harnesses=['claude-agent-sdk via opencode'])['observations']
                    self.assertEqual([route.get('native_rows') for route in found['routes']], [basis])

    def test_the_ledger_close_marks_only_a_native_basis_claim(self):
        """Task 14: the shared `complete` finalization (launcher, served `record`, reconcile) reads the recorded
        basis: a native-row claim writes the boundary, a stored-row claim of the same path does not."""
        now = int(time.time())
        for codes, marked in ((['native_rows'], True), ([], False)):
            with self.subTest(codes=codes):
                record = dict(attempt_id='a-%d' % len(codes), route='opencode-claude-subscription:s',
                              reason_codes=codes, billing_class='included_subscription',
                              pool='claude-subscription-%d' % len(codes))
                data = dict(admissions=[record], holds={record['attempt_id']: dict(closed=True)})
                subscription_fallback.after_record(data, dict(kind='complete', attempt_id=record['attempt_id']),
                                                   self.settings, now)
                self.assertEqual(collector.consumed_at(self.settings, record['pool']) > 0, marked)

    def test_opencode_claude_uses_native_rows_only_while_its_native_login_is_current(self):
        now = int(time.time())
        native_reading(self.settings, [dict(id=window, limit=1000, used=100, length=length, reset_at=now + 3600,
                                            rolling=False, observed_at=now - 5, unit=collector.WINDOW_UNIT)
                                       for window, length in (('5h', 18000), ('7d', 604800))], now)
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=now)
        double = subscription_billing.double('oc')
        with mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now: double(route, now)):
            for current, demand in ((True, {'5h': 5, '7d': 5}), (False, None)):
                with self.subTest(current=current), \
                        mock.patch.object(opencode_account, 'native_current', lambda settings, now: current):
                    found = self.collect('claude-agent', 'invented-claude-small', 'claude-agent-sdk via opencode')
                    self.assertEqual([route['demand'] for route in found['routes']], [demand])


if __name__ == '__main__':
    unittest.main()
