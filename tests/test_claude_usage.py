"""Account-based Claude routing (15 November amendment): the bounded native account/usage reader
(`runtime/claude_usage.py`), its account-bound facts in `LocalSource`, the native catalog binding it supplies and
provider-managed fallback at admission.

The native client is a synthetic executable written here: it answers only the two control requests the reader may
send and records what it received. Every account, model and window below is invented. Passing these tests proves the
package's handling of that synthetic transport, not that an installed client or a real account behaves this way.
"""
import json
import os
from pathlib import Path
import stat
import sys
import time
import unittest
from unittest import mock

from runtime import claude_usage, routing, routing_funding, routing_launch, routing_observations as collector, routing_paths, \
    subscription_billing
from runtime.adapters import native_catalog
from tests import test_native_routing as native_tests
from tests.test_routing_observations import Stores

FAKE = '''#!%s
import json, sys, time
script = json.load(open(sys.argv[1]))
log = open(sys.argv[2], 'a')
log.write(json.dumps(dict(argv=sys.argv[3:])) + '\\n'); log.flush()
if script.get('notice', True):
    print(json.dumps(dict(type='system', subtype='ui_invalidate', event='ui.render', uuid='u', session_id='s')),
          flush=True)
for line in sys.stdin:
    log.write(line); log.flush()
    request = json.loads(line)
    subtype = request['request']['subtype']
    if script.get('hang') == subtype:
        time.sleep(30)
    answer = script[subtype]
    if answer is None:
        continue
    print(json.dumps(dict(type='control_response', response=dict(subtype='success', request_id=request['request_id'],
                                                                 response=answer))), flush=True)
'''


def initialize(email='person@example.invalid', organization='Invented Org', **account):
    value = dict(email=email, subscriptionType='max', apiProvider='firstParty')  # claude.ai: no tokenSource
    if organization is not None:
        value['organization'] = organization
    value.update(account)
    return dict(commands=[], agents=[], claude_code_version='9.9.9', account=value,
                models=[dict(value='invented-small', resolvedModel='invented-small-1', supportsEffort=True,
                             supportedEffortLevels=['low', 'high']),
                        dict(value='invented-large', resolvedModel='invented-large-1', supportsEffort=True,
                             supportedEffortLevels=['low', 'high'])])


def iso(at):
    return time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(at))


def usage(now, five=40, seven=20, limits=(), extra=None, **rate):
    """The installed client's successful endpoint answer (status ok): raw `limits` kept, `model_scoped: []`.
    `limits=None` is its header-seeded fallback, which strips `limits` and adds no `model_scoped`."""
    value = dict(five_hour=dict(utilization=five, resets_at=iso(now + 3600)),
                 seven_day=dict(utilization=seven, resets_at=iso(now + 86400)))
    if limits is not None:
        value.update(limits=list(limits), model_scoped=[])
    value.update(rate)
    if extra is not None:
        value['extra_usage'] = extra
    return dict(session=dict(total_cost_usd=0, total_api_duration_ms=0, model_usage={}), subscription_type='max',
                rate_limits_available=True, rate_limits=value, behaviors=None)


class Reader(Stores):
    """A synthetic native client on a private PATH-less command; the real client is never found or started."""

    def setUp(self):
        super().setUp()
        self.fake = self.root / 'fake-claude'
        self.fake.write_text(FAKE % sys.executable)
        self.fake.chmod(stat.S_IRWXU)
        self.script, self.log = self.root / 'script.json', self.root / 'stdin.log'
        self.answer(initialize(), usage(self.now))
        self.enterContext(mock.patch.object(claude_usage, 'default_command',
                                            lambda environ: [str(self.fake), str(self.script), str(self.log)]))

    def answer(self, init, use, **extra):
        self.script.write_text(json.dumps(dict(initialize=init, get_usage=use, **extra)))

    def sent(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def spawns(self):
        return [item for item in self.sent() if 'argv' in item]

    def refresh(self, **options):
        return claude_usage.refresh(self.settings, environ=dict(PATH='/usr/bin:/bin', HOME=str(self.root)), **options)


class ExchangeTest(Reader):
    def test_sends_only_initialize_and_usage_without_behaviors_or_prompt(self):
        self.assertEqual(self.refresh()['state'], 'stored')
        requests = [item for item in self.sent() if 'argv' not in item]
        self.assertEqual([item['request']['subtype'] for item in requests], ['initialize', 'get_usage'])
        self.assertIs(requests[1]['request']['skip_behaviors'], True)
        self.assertTrue(all(item['type'] == 'control_request' for item in requests))
        argv = self.spawns()[0]['argv']
        self.assertIn('--safe-mode', argv)
        self.assertNotIn('--bare', argv)  # the account's own sign-in is used, never a bare unauthenticated client

    def test_success_stores_conservative_windows_account_and_catalog(self):
        before = int(time.time())
        self.assertEqual(self.refresh()['state'], 'stored')
        windows = {item['id']: item for item in collector.LocalSource(self.settings).windows(claude_usage.POOL, 0)}
        self.assertEqual(set(windows), {'5h', '7d'})
        self.assertEqual(windows['5h']['used'], 400)
        self.assertEqual(windows['7d']['length'], 7 * 86400)
        self.assertLessEqual(windows['5h']['observed_at'], before - claude_usage.CACHE_CEILING + 1)
        account = claude_usage.account(self.settings, int(time.time()))
        self.assertEqual(len(account['key']), 32)
        self.assertNotIn('example.invalid', json.dumps(account))  # a derived key, never the raw identity
        snapshot = native_catalog.stored(self.settings, 'claude-code')
        self.assertEqual([item['alias'] for item in snapshot['models']], ['invented-small', 'invented-large'])
        self.assertEqual(snapshot['binding']['connection'], 'account:' + account['key'])
        raw = claude_usage._store(self.settings).read_bytes()
        self.assertNotIn(b'person@', raw)
        self.assertEqual(stat.S_IMODE(os.stat(claude_usage._store(self.settings)).st_mode), 0o600)

    def test_fresh_reading_is_reused_without_another_process(self):
        self.assertEqual(self.refresh()['state'], 'stored')
        self.assertEqual(self.refresh()['state'], 'fresh')
        self.assertEqual(len(self.spawns()), 1)

    def test_fallback_without_raw_limits_leaves_usage_unknown_and_backs_off(self):
        self.answer(initialize(), usage(self.now, limits=None))  # seeded fallback: aggregates kept, `limits` gone
        outcome = self.refresh()
        self.assertEqual(outcome, dict(state='failed', reason='claude_usage_unconfirmed'))
        self.assertEqual(collector.LocalSource(self.settings).windows(claude_usage.POOL, 0), [])
        self.assertIsNotNone(claude_usage.account(self.settings, int(time.time())))  # the account itself is known
        self.assertEqual(self.refresh()['state'], 'backoff')
        self.assertEqual(len(self.spawns()), 1)

    def test_raw_limits_without_source_evidenced_semantics_are_refused(self):
        """Installed-source evidence: the one raw kind with known meaning is `weekly_scoped` under
        `scope.model` (model-scoped); no unscoped raw kind has evidenced semantics, so none is mapped or ignored."""
        reset = iso(self.now + 86400)
        cases = dict(
            model=([dict(kind='weekly_scoped', group='weekly', percent=20, resets_at=reset,
                         scope=dict(model=dict(display_name='invented')))], {}, 'claude_limits_scoped'),
            surface=([dict(kind='weekly', group='weekly', percent=20, resets_at=reset,
                           scope=dict(surface=dict(display_name='invented')))], {}, 'claude_limits_unsupported'),
            same_as_meter=([dict(kind='session', group='g', percent=40, resets_at=iso(self.now + 3600))], {},
                           'claude_limits_unsupported'),
            model_scoped=([], dict(model_scoped=[dict(display_name='x', utilization=1, resets_at=reset)]),
                          'claude_limits_scoped'),
            no_marker=([], dict(model_scoped=None), 'claude_usage_unconfirmed'),
            unknown_meter=([], dict(cinder_cove=dict(utilization=1, resets_at=reset)), 'claude_limits_unsupported'),
            unknown_key=([], dict(invented_meter=None), 'claude_limits_unsupported'))
        for name, (limits, rate, reason) in cases.items():
            with self.subTest(name):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                value = usage(self.now, limits=limits, **rate)
                if rate.get('model_scoped', 0) is None:
                    del value['rate_limits']['model_scoped']
                self.answer(initialize(), value)
                self.assertEqual(self.refresh(force=True), dict(state='failed', reason=reason))
                self.assertFalse(claude_usage.usable(self.settings, int(time.time())))

    def test_zero_inference_telemetry_is_required(self):
        base = usage(self.now)
        cases = dict(cost=dict(total_cost_usd=0.01), boolean=dict(total_cost_usd=False),
                     duration=dict(total_api_duration_ms=5), models=dict(model_usage={'invented': {}}))
        for name, session in cases.items():
            with self.subTest(name):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(initialize(), dict(base, session=dict(base['session'], **session)))
                self.assertEqual(self.refresh(force=True), dict(state='failed', reason='claude_inference_observed'))
                self.assertIsNone(claude_usage.account(self.settings, int(time.time())))
        for name, change in dict(behaviors=dict(behaviors=dict(day={})), available=dict(rate_limits_available=1),
                                 plan=dict(subscription_type='invented')).items():
            with self.subTest(name):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(initialize(), dict(base, **change))
                self.assertEqual(self.refresh(force=True)['state'], 'failed')
                self.assertIsNone(claude_usage.account(self.settings, int(time.time())))
        self.assertNotIn(b'invented', claude_usage._store(self.settings).read_bytes()
                         if claude_usage._store(self.settings).exists() else b'')

    def test_a_failed_or_partial_read_never_reuses_the_earlier_windows(self):
        self.assertEqual(self.refresh()['state'], 'stored')
        self.assertTrue(claude_usage.usable(self.settings, int(time.time())))
        partial = usage(self.now)
        del partial['rate_limits']['seven_day']
        for value, reason in ((partial, 'claude_window_missing'), (usage(self.now, limits=None),
                                                                    'claude_usage_unconfirmed')):
            with self.subTest(reason):
                self.answer(initialize(), value)
                self.assertEqual(self.refresh(force=True), dict(state='failed', reason=reason))
                self.assertFalse(claude_usage.usable(self.settings, int(time.time())))  # old 7d + new 5h: never
                self.answer(initialize(), usage(self.now))
                self.assertEqual(self.refresh(force=True)['state'], 'stored')

    def test_malformed_future_and_partial_readings(self):
        cases = dict(boolean=usage(self.now, five=True), high=usage(self.now, five=101),
                     past=dict(usage(self.now), rate_limits=dict(usage(self.now)['rate_limits'],
                                                                 five_hour=dict(utilization=1, resets_at=iso(1)))),
                     future=dict(usage(self.now), rate_limits=dict(usage(self.now)['rate_limits'],
                                                                   five_hour=dict(utilization=1, resets_at=iso(
                                                                       self.now + 30 * 86400)))),
                     scoped_meter=usage(self.now, seven_day_opus=dict(utilization=1, resets_at=iso(self.now + 60))),
                     extra=usage(self.now, extra=dict(is_enabled='yes')))
        for name, value in cases.items():
            with self.subTest(name):
                self.answer(initialize(), value)
                outcome = self.refresh(force=True)
                self.assertEqual(outcome['state'], 'failed', outcome)
        partial = usage(self.now)
        del partial['rate_limits']['seven_day']
        self.answer(initialize(), partial)
        self.assertEqual(self.refresh(force=True, need=('5h', '7d')),
                         dict(state='failed', reason='claude_window_missing'))

    def test_overflow_state_is_recorded_but_never_required(self):
        for extra, state in ((dict(is_enabled=True), 'enabled'), (dict(is_enabled=False), 'disabled'),
                             (None, 'unknown')):
            with self.subTest(state):
                self.answer(initialize(), usage(self.now, extra=extra))
                self.assertEqual(self.refresh(force=True)['state'], 'stored')
                self.assertEqual(claude_usage.account(self.settings, int(time.time()))['overflow'], state)

    def test_key_backed_or_third_party_sign_in_is_not_a_subscription_account(self):
        for account in (dict(apiKeySource='ANTHROPIC_API_KEY'), dict(apiKeySource='/login managed key'),
                        dict(apiProvider='bedrock'), dict(subscriptionType=None), dict(email=None),
                        dict(tokenSource='CLAUDE_CODE_OAUTH_TOKEN'), dict(tokenSource='claude.ai'),
                        dict(tokenSource=''), dict(organization=7)):
            with self.subTest(account):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(initialize(**account), usage(self.now))
                self.assertEqual(self.refresh(force=True), dict(state='failed', reason='claude_account_unsupported'))
                self.assertIsNone(claude_usage.account(self.settings, int(time.time())))

    def test_account_change_invalidates_earlier_readings_and_catalog(self):
        self.assertEqual(self.refresh()['state'], 'stored')
        first = claude_usage.account(self.settings, int(time.time()))['key']
        self.answer(initialize(email='other@example.invalid'), usage(self.now, limits=None))
        self.assertEqual(self.refresh(force=True)['reason'], 'claude_usage_unconfirmed')
        second = claude_usage.account(self.settings, int(time.time()))['key']
        self.assertNotEqual(first, second)
        self.assertFalse(claude_usage.usable(self.settings, int(time.time())))  # the first account's readings
        self.assertEqual(collector.consumed_at(self.settings, claude_usage.POOL), 0)  # nothing re-dated
        self.assertEqual(native_catalog.stored(self.settings, 'claude-code')['binding']['connection'],
                         'account:' + second)
        self.assertNotEqual(claude_usage.account_key(dict(email='a@x', organization='One')),
                            claude_usage.account_key(dict(email='a@x', organization='Two')))
        self.assertNotEqual(claude_usage.account_key(dict(email='a@x')),
                            claude_usage.account_key(dict(email='a@x', organization='One')))

    def test_timeout_stops_the_client_and_backs_off(self):
        self.answer(initialize(), usage(self.now), hang='get_usage')
        started = time.monotonic()
        self.assertEqual(self.refresh(timeout=0.5), dict(state='failed', reason='claude_timeout'))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.refresh()['state'], 'backoff')

    def test_unexpected_message_fails_closed(self):
        with mock.patch.object(claude_usage, 'NOTICE', dict(type='system', subtype='init')):
            self.assertEqual(self.refresh()['reason'], 'claude_protocol_unexpected')

    def test_busy_reader_waits_then_reports_busy(self):
        with claude_usage._lock(self.settings, 0) as held:
            self.assertTrue(held)
            with mock.patch.object(claude_usage, 'LOCK_EXTRA', 0), mock.patch.object(claude_usage, 'TIMEOUT', 0.1):
                self.assertEqual(self.refresh(timeout=0.1), dict(state='busy', reason='reader_busy'))
        self.assertEqual(self.spawns(), [])

    def test_test_home_never_starts_the_installed_client(self):
        with mock.patch.dict(os.environ, VCW_TEST_HOME='1'):
            with self.assertRaises(claude_usage.ReadError):
                claude_usage.__dict__['_installed'](dict(PATH='/usr/bin', VCW_TEST_HOME='1'))


class AccountFactTest(Reader):
    ROUTE = dict(route='claude-native-subscription:choice:0123456789abcdef', account='claude-plan',
                 pool='claude-subscription', billing_class='included_subscription')

    def test_local_source_answers_an_account_bound_fact_only_for_the_claude_native_path(self):
        source = collector.LocalSource(self.settings)
        self.assertIsNone(source.billing(self.ROUTE, int(time.time())))
        self.assertEqual(self.refresh()['state'], 'stored')
        fact = source.billing(self.ROUTE, int(time.time()))
        self.assertIsNone(subscription_billing.problem(fact, self.ROUTE, int(time.time())))
        self.assertIsNone(source.billing(dict(self.ROUTE, route='opencode-claude-subscription:choice:0123456789abcdef'),
                                         int(time.time())))

    def test_enabled_disabled_or_unknown_overflow_never_disqualifies(self):
        for state in ('provider_blocked', 'provider_managed', 'unknown'):
            fact = dict(subscription_billing.double('x')(self.ROUTE, self.now), paid_crossover=state)
            self.assertIsNone(subscription_billing.problem(fact, self.ROUTE, self.now))
        fact = dict(subscription_billing.double('x')(self.ROUTE, self.now), paid_crossover='purchased')
        self.assertEqual(subscription_billing.problem(fact, self.ROUTE, self.now), 'billing_malformed')

    def test_collect_acquires_once_for_a_demanded_claude_route(self):
        routing_paths.qualify(self.settings, 'claude-native-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=self.now)
        identity = routing_launch.route_identity('claude_native', model='invented-small', effort='high')
        choice = dict(identity, funding='subscription')
        for _ in range(2):
            found = collector.collect(str(self.root), dict(scope='s'), settings_path=self.settings,
                                      ledger_path=self.ledger_path, refresh_windows=True, choices=[choice],
                                      harnesses=['claude-native'])
        self.assertEqual(len(found['observations']['routes']), 1, found['observations']['excluded'])
        self.assertIs(found['observations']['routes'][0]['provider_fallback'], True)
        self.assertEqual(len(self.spawns()), 1)
        collector.shortlist(str(self.root), dict(scope='s'), settings_path=self.settings, choices=[choice],
                            harnesses=['claude-native'])  # a pure preview starts nothing
        self.assertEqual(len(self.spawns()), 1)


def candidate_inputs(used, fallback=True):
    now = int(time.time())
    route = dict(route='r1', pool='p', account='a', billing_class='included_subscription', capable=True,
                 permitted=True, binding_enforceable=True, demand={'5h': 50}, provider_fallback=fallback,
                 provider='anthropic', model='m', effort='high', harness='claude-native')
    pool = dict(windows=[dict(id='5h', limit=1000, used=used, fresh=True, reset_at=now + 3600)])
    return route, dict(now=now, routes=[route], pools=dict(p=pool))


class FallbackTest(unittest.TestCase):
    """`routing._candidate`: exhausted included quota on an enabled account is provider-managed fallback."""

    def candidate(self, used, fallback=True, held=None):
        route, observations = candidate_inputs(used, fallback)
        settings = dict(api_usage_enabled=False, margin_bp=0)
        return routing._candidate(dict(organizations={}), route, observations, dict(scope='s'), settings, held or {},
                                  observations['now'])

    def test_exhausted_window_admits_as_fallback_without_included_claim(self):
        item, reasons = self.candidate(1000)
        self.assertEqual(reasons, [])
        self.assertIs(item['fallback'], True)
        self.assertEqual(item['vector'], {})
        self.assertEqual(item['constraints'], [])

    def test_without_account_fallback_exhaustion_still_refuses(self):
        self.assertEqual(self.candidate(1000, fallback=False), (None, ['insufficient_headroom']))

    def test_included_headroom_is_reserved_normally(self):
        item, _ = self.candidate(100)
        self.assertIs(item['fallback'], False)
        self.assertEqual(set(item['vector']), {routing.WIN + 'p/5h'})

    def test_stale_readings_are_never_fallback(self):
        route, observations = candidate_inputs(1000)
        observations['pools']['p']['windows'][0]['fresh'] = False
        item = routing._candidate(dict(organizations={}), route, observations, dict(scope='s'),
                                  dict(api_usage_enabled=False, margin_bp=0), {}, observations['now'])
        self.assertEqual(item, (None, ['stale_observation']))

    def test_included_candidate_is_preferred_over_fallback(self):
        candidates = [dict(route='fb', pool='p', constraints=[], fallback=True),
                      dict(route='inc', pool='q', constraints=[dict(pool='q', window='5h', headroom=500, cost=5,
                                                                    resets_at=None)])]
        self.assertEqual([item['route'] for item in routing_funding.prefer_included(candidates)], ['inc'])
        self.assertEqual([item['route'] for item in routing_funding.prefer_included(candidates[:1])], ['fb'])


class BridgeTest(native_tests.NativeBase, Reader):
    """The real one-shot `native_prepare` on LocalSource with the synthetic client and no binding double."""

    def setUp(self):
        super().setUp()
        self.guard.stop()  # the synthetic client is the only process; it is not the installed one
        self.enterContext(mock.patch.object(native_catalog, 'current_binding', lambda client: None))
        native_catalog._path(self.settings).unlink()
        claude_usage._store(self.settings).unlink(missing_ok=True)
        collector.window_path(self.settings).unlink()  # cold: no reading, account or catalog
        self.enterContext(mock.patch.dict(os.environ, PATH='/usr/bin:/bin'))

    def test_cold_prepare_bootstraps_catalog_and_usage_then_dispatches_exact_controls(self):
        permit = self.prepare('claude-code')
        self.assertEqual(permit['state'], 'dispatched', permit)
        self.assertEqual((permit['model'], permit['effort']), ('invented-small', 'high'))
        self.assertEqual(len(self.spawns()), 1)
        done = self.complete('n1', dict(kind='complete', model='invented-small-1'))
        self.assertEqual(done['served']['substituted'], False)
        self.assertEqual(self.prepare('claude-code', attempt='n2')['reason'], 'no_feasible_route')
        self.assertEqual(len(self.spawns()), 2)  # a cached reply may predate the completion: not usable, backs off
        self.assertEqual(self.prepare('claude-code', attempt='n3')['reason'], 'no_feasible_route')
        self.assertEqual(len(self.spawns()), 2)
        self.offset += claude_usage.CACHE_CEILING * 2
        again = self.prepare('claude-code', attempt='n4')
        self.assertEqual(again['state'], 'dispatched', again)
        self.assertEqual(len(self.spawns()), 3)

    def test_exhausted_included_quota_dispatches_on_provider_managed_fallback(self):
        self.answer(initialize(), usage(self.now, five=100, seven=100, extra=dict(is_enabled=False)))
        permit = self.prepare('claude-code')
        self.assertEqual(permit['state'], 'dispatched', permit)
        record = next(item for item in self.data()['admissions'] if item['attempt_id'] == 'n1')
        self.assertIn('provider_managed_fallback', record['reason_codes'])
        self.assertEqual(record['reserved'], {})
        self.assertEqual(self.prepare('claude-code')['reason'], 'already_prepared')  # one permit only

    def test_provider_refusal_is_uncertain_and_never_retried(self):
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        refused = self.complete('n1', dict(kind='not_started'))
        self.assertEqual(refused['disposition'], 'uncertain')
        self.assertEqual(self.prepare('claude-code')['reason'], 'already_prepared')


class ProvenanceTest(BridgeTest):
    """The shared `claude-subscription` pool: only readings bound to the current native account (and its latest
    confirmed exchange) count for the native consumer; nothing else is assumed to be the same login."""

    def opencode_reading(self, used=0):
        at = int(time.time())
        for window, length in (('5h', 18000), ('7d', 604800)):
            collector.store_window(self.settings, claude_usage.POOL, dict(
                id=window, limit=1000, used=used, length=length, reset_at=at + 3600, rolling=False, observed_at=at,
                unit=collector.WINDOW_UNIT))

    def test_an_unbound_reading_never_restores_native_headroom(self):
        self.answer(initialize(), usage(self.now, limits=None))  # this account's usage stays unknown
        self.opencode_reading(used=0)  # another client's newer reading with ample room, unbound
        self.assertEqual(self.prepare('claude-code')['state'], 'pending')
        self.assertEqual(self.data()['admissions'], [])

    def test_another_accounts_reading_is_unknown(self):
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        self.complete('n1', dict(kind='complete'))
        self.offset += claude_usage.CACHE_CEILING * 2 + claude_usage.FAILURE_BACKOFF
        self.answer(initialize(email='other@example.invalid'), usage(self.now, limits=None))
        self.assertEqual(self.prepare('claude-code', 'n2')['state'], 'pending')

    def test_the_opencode_consumer_never_uses_native_bound_readings(self):
        self.assertEqual(claude_usage.refresh(self.settings)['state'], 'stored')
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=int(time.time()))
        choice = dict(provider='claude-agent', model='invented-claude-small', effort='low',
                      harness='claude-agent-sdk via opencode', funding='subscription')
        self.billing()  # the OpenCode path has no account producer; a labelled double stands in for it
        local = collector.collect(str(self.checkout), dict(scope='s'), settings_path=self.settings,
                                  ledger_path=self.ledger_path, choices=[choice],
                                  harnesses=['claude-agent-sdk via opencode'])
        self.assertEqual({item['reason'] for item in local['observations']['excluded']}, {'window_unobserved'})
        self.assertEqual(local['observations']['routes'], [])

    def test_same_account_aliases_share_one_included_allowance(self):
        self.answer(initialize(), usage(self.now, five=99.3))  # room for one reservation only
        self.assertEqual(self.prepare('claude-code', 'a1')['state'], 'dispatched')
        self.assertEqual(self.prepare('claude-code', 'a2', band='high')['state'], 'dispatched')
        records = {item['attempt_id']: item for item in self.data()['admissions']}
        self.assertTrue(records['a1']['reserved'])
        self.assertEqual(records['a2']['reserved'], {})  # never a second included claim for the other alias
        self.assertEqual(len(self.spawns()), 1)


class FallbackReplayTest(BridgeTest):
    """Strict replay (`tests/qualification/replay_strict.py`) over real fallback admissions."""

    def replay(self):
        from tests.qualification import replay_strict
        return replay_strict.replay_records(self.data()['admissions'])

    def exhausted(self):
        self.answer(initialize(), usage(self.now, five=100, seven=100))

    def test_ranked_fallback_replays_and_tampering_fails(self):
        self.exhausted()
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        self.assertTrue(self.replay()['passed'], self.replay())
        from tests.qualification import replay_strict
        record = self.data()['admissions'][0]
        self.assertTrue(all(item['fallback'] for item in record['pressure']['candidates']))
        for change in (lambda r: r['pressure']['candidates'][0].update(fallback=False),
                       lambda r: r.update(reserved={routing.WIN + 'claude-subscription/5h': 6}),
                       lambda r: r.update(reason_codes=[])):
            tampered = json.loads(json.dumps(record))
            change(tampered)
            self.assertFalse(replay_strict.replay_records([tampered])['passed'])

    def test_explicit_choice_fallback_and_lifecycle_replay(self):
        self.exhausted()
        identity = routing_launch.route_identity('claude_native', model='invented-large', effort='low')
        permit = self.prepare('claude-code', choice=dict(identity, funding='subscription'))
        self.assertEqual(permit['state'], 'dispatched', permit)
        self.assertEqual(self.prepare('claude-code', choice=dict(identity, funding='subscription'))['reason'],
                         'already_prepared')
        self.assertTrue(self.replay()['passed'], self.replay())
        self.assertEqual(self.complete('n1', dict(kind='complete'))['state'], 'recorded')
        self.assertTrue(self.replay()['passed'])
        self.offset += claude_usage.CACHE_CEILING * 2 + claude_usage.FAILURE_BACKOFF
        self.assertEqual(self.prepare('claude-code', 'n2')['state'], 'dispatched')
        uncertain = self.complete('n2', dict(kind='unknown'))
        self.assertEqual((uncertain['disposition'], uncertain['recovery']), ('uncertain', 'reconcile_subscription'))
        self.assertEqual(self.holds()['n2']['state'], 'uncertain')
        self.assertTrue(self.replay()['passed'])
        self.assertNotEqual(self.prepare('claude-code', 'n2')['state'], 'dispatched')


if __name__ == '__main__':
    unittest.main()
