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
import types
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
    value = dict(email=email, subscriptionType='Claude Max', apiProvider='firstParty')  # display label; no tokenSource
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
        windows = {item['id']: item for item in claude_usage.usable_windows(self.settings, int(time.time()))}
        self.assertEqual(set(windows), {'5h', '7d'})
        self.assertEqual(windows['5h']['used'], 400)
        self.assertEqual(windows['7d']['length'], 7 * 86400)
        self.assertLessEqual(windows['5h']['observed_at'], before - claude_usage.CACHE_CEILING + 1)
        account = claude_usage.account(self.settings, int(time.time()))
        self.assertEqual(len(account['key']), 32)
        self.assertNotIn('example.invalid', json.dumps(account))  # a derived key, never the raw identity
        snapshot = native_catalog.stored(self.settings, 'claude-code')
        self.assertEqual([item['alias'] for item in snapshot['models']], ['invented-small', 'invented-large'])
        self.assertEqual(snapshot['binding']['connection'], 'account:%s:%s' % (account['key'], account['generation']))
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
                        dict(apiProvider='bedrock'), dict(email=None),
                        dict(tokenSource='CLAUDE_CODE_OAUTH_TOKEN'), dict(tokenSource='claude.ai'),
                        dict(tokenSource=''), dict(organization=7)):
            with self.subTest(account):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(initialize(**account), usage(self.now))
                self.assertEqual(self.refresh(force=True), dict(state='failed', reason='claude_account_unsupported'))
                self.assertIsNone(claude_usage.account(self.settings, int(time.time())))

    def test_display_plan_label_is_not_the_subscription_classifier(self):
        """Installed-source evidence (2.1.311): initialize reports the user-visible plan label ("Claude Max", or a
        dynamic "Claude <plan>") in `account.subscriptionType`, while `get_usage` reports the normalized machine
        type. The machine type classifies the sign-in; a friendly label is never matched or parsed."""
        for label, machine in (('Claude Max', 'max'), ('Claude Pro', 'pro'), ('Claude Team', 'team'),
                               ('Claude Enterprise', 'enterprise'), ('Claude Max 20x', 'max')):
            with self.subTest(label):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(initialize(subscriptionType=label), dict(usage(self.now), subscription_type=machine))
                self.assertEqual(self.refresh(force=True)['state'], 'stored')
                self.assertEqual({item['id'] for item in claude_usage.usable_windows(self.settings, int(time.time()))},
                                 {'5h', '7d'})
                self.assertIsNotNone(claude_usage.account(self.settings, int(time.time())))
                current = claude_usage.account(self.settings, int(time.time()))
                self.assertEqual(native_catalog.stored(self.settings, 'claude-code')['binding']['connection'],
                                 'account:%s:%s' % (current['key'], current['generation']))

    def test_missing_or_unknown_machine_subscription_fails_closed_whatever_the_display_label(self):
        """A friendly label, even one equal to a machine value, never stands in for the usage answer's type."""
        base = usage(self.now)
        del_type = {key: value for key, value in base.items() if key != 'subscription_type'}
        cases = dict(missing=del_type, null=dict(base, subscription_type=None),
                     unknown=dict(base, subscription_type='free'), label=dict(base, subscription_type='Claude Max'),
                     case=dict(base, subscription_type='MAX'), kind=dict(base, subscription_type=['max']))
        for label in ('Claude Max', 'max'):
            for name, value in cases.items():
                with self.subTest(label=label, case=name):
                    claude_usage._store(self.settings).unlink(missing_ok=True)
                    self.answer(initialize(subscriptionType=label), value)
                    self.assertEqual(self.refresh(force=True),
                                     dict(state='failed', reason='claude_account_unsupported'))
                    self.assertIsNone(claude_usage.account(self.settings, int(time.time())))
                    self.assertFalse(claude_usage.usable(self.settings, int(time.time())))
                    self.assertIsNone(native_catalog.stored(self.settings, 'claude-code'))

    def test_display_label_does_not_weaken_usage_or_account_refusals(self):
        label = dict(subscriptionType='Claude Max')
        for name, account in dict(key=dict(apiKeySource='ANTHROPIC_API_KEY'), provider=dict(apiProvider='bedrock'),
                                  token=dict(tokenSource='CLAUDE_CODE_OAUTH_TOKEN')).items():
            with self.subTest(name):
                claude_usage._store(self.settings).unlink(missing_ok=True)
                self.answer(initialize(**label, **account), usage(self.now))
                self.assertEqual(self.refresh(force=True), dict(state='failed', reason='claude_account_unsupported'))
        claude_usage._store(self.settings).unlink(missing_ok=True)
        unknown = usage(self.now, **{'unrecognized_%d' % index: None for index in range(24)})
        self.answer(initialize(**label), unknown)
        self.assertEqual(self.refresh(force=True), dict(state='failed', reason='claude_limits_unsupported'))
        self.assertFalse(claude_usage.usable(self.settings, int(time.time())))

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
                         claude_usage.binding(self.settings)['connection'])
        self.assertEqual(claude_usage.binding(self.settings)['connection'].split(':')[1], second)
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
        for attempt in ('n2', 'n3'):  # a cached reply may predate the completion: not usable, backs off
            self.assertEqual(self.prepare('claude-code', attempt=attempt)['state'], 'dispatched')  # Task 2: fallback
            self.assertEqual(self.disposition(attempt), ({}, ['quota_unknown', 'provider_managed_fallback']))
            self.assertEqual(len(self.spawns()), 2)
        self.offset += claude_usage.CACHE_CEILING * 2
        again = self.prepare('claude-code', attempt='n4')
        self.assertEqual(again['state'], 'dispatched', again)
        self.assertEqual(len(self.spawns()), 3)
        # A fresh covering reading arrived, but n2 and n3 are still open provider-managed attempts on this pool: the
        # included optimization stays fenced (a real exposure, not a mocked one), and no included claim is made.
        self.assertEqual(self.disposition('n4'), ({}, ['quota_fenced', 'provider_managed_fallback']))

    def disposition(self, attempt):
        record = next(item for item in self.data()['admissions'] if item['attempt_id'] == attempt)
        return record['reserved'], record['reason_codes'][-2:]

    def test_exhausted_included_quota_dispatches_on_provider_managed_fallback(self):
        self.answer(initialize(), usage(self.now, five=100, seven=100, extra=dict(is_enabled=False)))
        permit = self.prepare('claude-code')
        self.assertEqual(permit['state'], 'dispatched', permit)
        record = next(item for item in self.data()['admissions'] if item['attempt_id'] == 'n1')
        self.assertIn('provider_managed_fallback', record['reason_codes'])
        self.assertEqual(record['reserved'], {})
        self.assertEqual(self.prepare('claude-code')['reason'], 'already_prepared')  # one permit only

    def test_native_metadata_without_quota_reaches_fallback(self):
        """Task 3: an exchange's account and catalog survive its unknown usage and reach provider-managed fallback
        with one permit and no included claim; unreported served values stay unknown. No catalog sends nothing."""
        self.answer(dict(initialize(), models=[]), usage(self.now))
        self.assertEqual(self.prepare('claude-code', 'm0'),
                         dict(v=1, state='blocked', reason='native_binding_unverified'))
        self.assertEqual(self.holds(), {})
        self.offset += claude_usage.FAILURE_BACKOFF
        self.answer(initialize(), usage(self.now, limits=None))
        tool = native_tests.NativeTool()
        permit, done = self.run_native('claude-code', tool, attempt='m1')
        self.assertEqual((permit['state'], tool.calls), ('dispatched', [dict(model='invented-small', effort='high')]))
        self.assertEqual(self.disposition('m1'), ({}, ['quota_unknown', 'provider_managed_fallback']))
        self.assertEqual(done['served'], dict(model='unknown', effort='unknown'))
        self.assertEqual(self.prepare('claude-code', 'm1')['reason'], 'already_prepared')
        self.assertEqual(len(self.spawns()), 2)

    def test_failed_quota_does_not_repeat_fresh_metadata_exchange(self):
        """Task 3: required metadata is reused while current, after the optional quota read failed and its backoff
        ended; only demanded quota may start another exchange."""
        routing_paths.qualify(self.settings, claude_usage.PATH, demand=None, evidence='synthetic-1',
                              now=int(time.time()))
        self.answer(initialize(), usage(self.now, limits=None))
        self.assertEqual(self.prepare('claude-code', 'q1')['state'], 'dispatched')
        self.offset += claude_usage.FAILURE_BACKOFF + 1  # the quota backoff is over; account and catalog are current
        route = dict(route=claude_usage.PATH + ':activation', account='claude-plan', pool=claude_usage.POOL,
                     billing_class='included_subscription')
        collector.LocalSource(self.settings).acquire_billing([route], int(time.time()))
        self.assertEqual(claude_usage.refresh(self.settings, need=()), dict(state='fresh'))
        self.assertEqual(self.prepare('claude-code', 'q2')['state'], 'dispatched')
        self.assertEqual(self.disposition('q2')[0], {})
        self.assertEqual(len(self.spawns()), 1)

    def test_a_missing_catalog_is_restored_once_with_current_account_and_windows(self):
        """A current account with fresh windows but no bound catalog is read once at once, not after expiry; the
        exchange's incidental quota is kept and later preparation reuses the metadata."""
        self.assertEqual(claude_usage.refresh(self.settings)['state'], 'stored')
        native_catalog._path(self.settings).unlink()  # only the catalog snapshot is lost
        self.assertTrue(claude_usage.usable(self.settings, int(time.time())))
        permit = self.prepare('claude-code', 'c1')
        self.assertEqual(permit['state'], 'dispatched', permit)
        self.assertEqual(len(self.spawns()), 2)
        self.assertTrue(self.disposition('c1')[0])  # an included reservation on the incidental reading
        self.assertEqual(self.prepare('claude-code', 'c2')['state'], 'dispatched')
        self.assertEqual(len(self.spawns()), 2)

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
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')  # Task 2: fallback, never that room
        self.assertEqual([(item['reserved'], item['reason_codes'][-2:]) for item in self.data()['admissions']],
                         [({}, ['quota_unknown', 'provider_managed_fallback'])])

    def test_another_accounts_reading_is_unknown(self):
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        self.complete('n1', dict(kind='complete'))
        self.offset += claude_usage.CACHE_CEILING * 2 + claude_usage.FAILURE_BACKOFF
        self.answer(initialize(email='other@example.invalid'), usage(self.now, limits=None))
        refused = self.prepare('claude-code', 'n2')  # its admission's own read found another account: never sent
        self.assertEqual((refused['state'], refused['reason']), ('blocked', 'native_binding_changed'))
        self.assertNotEqual(self.holds().get('n2', {}).get('state'), 'dispatched')
        self.assertEqual(self.prepare('claude-code', 'n3')['state'], 'dispatched')  # the current account, fallback only
        record = next(item for item in self.data()['admissions'] if item['attempt_id'] == 'n3')
        self.assertEqual((record['reserved'], record['reason_codes'][-2:]), ({}, ['quota_unknown',
                                                                                  'provider_managed_fallback']))

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
        self.assertEqual(local['observations']['excluded'], [])  # Task 2: its own (synthetic) account stays eligible
        self.assertEqual([route['demand'] for route in local['observations']['routes']], [None])  # fallback only
        self.assertEqual(local['observations']['pools']['claude-subscription']['windows'], [])  # no native row borrowed

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


class SharedPoolBoundaryTest(Reader):
    """Parent ruling (15 November): an account-ambiguous Claude pool debit is never released automatically, and a
    collected native snapshot is rechecked against its account confirmation as well as the window store."""

    ROUTE = dict(provider='anthropic', model='invented-small', effort='high', harness='claude-native',
                 funding='subscription')

    def collect(self):
        routing_paths.qualify(self.settings, 'claude-native-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=self.now)
        found = collector.collect(str(self.root), dict(scope='s'), settings_path=self.settings,
                                  ledger_path=self.ledger_path, refresh_windows=True, choices=[self.ROUTE],
                                  harnesses=['claude-native'])
        self.assertEqual(len(found['observations']['routes']), 1, found['observations']['excluded'])
        return found

    def recheck(self, found):
        class Ledger:
            def admit(self, attempt, decide):
                return decide({})
        observations = found['observations']
        collector.rechecked_ledger(Ledger(), self.settings, observations, found['boundaries']).admit('a', lambda d: d)
        return [window['fresh'] for window in observations['pools'][claude_usage.POOL]['windows']]

    def test_an_unchanged_account_keeps_the_snapshot(self):
        self.assertEqual(self.recheck(self.collect()), [True, True])

    def test_invalidated_or_changed_account_confirmation_makes_the_snapshot_stale(self):
        key = lambda: claude_usage._state(self.settings)['account']['key']  # noqa: E731
        cases = dict(
            failed=lambda: (self.answer(initialize(), usage(self.now, limits=None)), self.refresh(force=True)),
            partial=lambda: (self.answer(initialize(), dict(usage(self.now), rate_limits=dict(
                usage(self.now)['rate_limits'], seven_day=None))), self.refresh(force=True)),
            changed=lambda: claude_usage._save(self.settings, account=dict(
                claude_usage._state(self.settings)['account'], key='b' * 32)),
            switched_back=lambda: claude_usage._save(self.settings, account=dict(
                claude_usage._state(self.settings)['account'], usage_at=0)))
        for name, invalidate in cases.items():
            with self.subTest(name):
                self.answer(initialize(), usage(self.now))
                self.assertEqual(self.refresh(force=True)['state'], 'stored')
                found = self.collect()
                rows = collector.window_path(self.settings).read_bytes()
                before = key()
                invalidate()
                if name in ('failed', 'partial'):
                    self.assertEqual(collector.window_path(self.settings).read_bytes(), rows)  # store unchanged
                    self.assertEqual(key(), before)
                self.assertEqual(self.recheck(found), [False, False])

    def test_no_claude_pool_debit_is_released_by_an_observation(self):
        from runtime import subscription_reconcile as reconcile
        now = int(time.time())
        mark = now - 7200
        window = dict(id='5h', limit=1000, used=0, length=18000, reset_at=now + 3600 * 4, rolling=False, fresh=True)
        other = {reconcile.DEBITS: {routing.WIN + 'sub/5h': dict(units=6, at=mark, marker='dd_' + '1' * 32)}}
        with self.assertRaises(AttributeError):  # the same window still qualifies for an unaffected pool
            reconcile.clear_debits(object(), other, dict(now=now, pools=dict(sub=dict(windows=[dict(window)]))))
        for name in ('native', 'opencode', 'after_account_change'):
            with self.subTest(name):
                data = {reconcile.DEBITS: {routing.WIN + 'claude-subscription/5h': dict(units=6, at=mark,
                                                                                        marker='dd_' + '0' * 32)}}
                observations = dict(now=now, pools={'claude-subscription': dict(windows=[dict(window)])})
                kept = reconcile.clear_debits(object(), data, observations)  # never reaches the ledger
                self.assertEqual(set(kept[reconcile.DEBITS]), {routing.WIN + 'claude-subscription/5h'})


def signal_module():
    import signal
    return signal


class ReviewFixTest(Reader):
    """Independent review (e437ad97): F1 exception boundary, F2 operator guidance, F4 account-level refusals."""

    def test_an_unsafe_state_folder_fails_with_a_fixed_reason_and_starts_nothing(self):
        folder = collector.window_path(self.settings).parent
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o777)
        self.addCleanup(folder.chmod, 0o700)
        self.assertEqual(self.refresh(), dict(state='failed', reason='observation_store_unsafe'))
        self.assertEqual(collector.refresh_needed([dict(
            route='claude-native-subscription:choice:0123456789abcdef', status='qualified', account='claude-plan',
            pool='claude-subscription', billing_class='included_subscription', demand={'5h': 5, '7d': 5},
            provider='anthropic', model='m', effort='high', harness='claude-native')], self.settings),
            {'claude-subscription': 'observation_store_unsafe'})
        self.assertEqual(self.spawns(), [])

    def test_a_refused_sign_in_or_observed_inference_ends_the_earlier_account_confirmation(self):
        route = AccountFactTest.ROUTE
        base = usage(self.now)
        for name, init, use in (
                ('sign_in', initialize(tokenSource='CLAUDE_CODE_OAUTH_TOKEN'), base),
                ('inference', initialize(), dict(base, session=dict(base['session'], total_cost_usd=0.5)))):
            with self.subTest(name):
                self.answer(initialize(), usage(self.now))
                self.assertEqual(self.refresh(force=True)['state'], 'stored')
                self.assertIsNotNone(claude_usage.billing_fact(self.settings, route, int(time.time())))
                rows = collector.window_path(self.settings).read_bytes()
                self.answer(init, use)
                self.assertEqual(self.refresh(force=True)['state'], 'failed')
                self.assertIsNone(claude_usage.billing_fact(self.settings, route, int(time.time())))
                self.assertIsNone(claude_usage.binding(self.settings))
                self.assertFalse(claude_usage.usable(self.settings, int(time.time())))
                self.assertEqual(collector.window_path(self.settings).read_bytes(), rows)  # observations kept

    def test_claude_pool_debits_carry_drop_debit_guidance(self):
        from runtime import subscription_reconcile as reconcile
        claude = {routing.WIN + 'claude-subscription/5h': dict(units=6, at=self.now, marker='m')}
        other = {routing.WIN + 'sub/5h': dict(units=6, at=self.now, marker='m')}
        uncertain = dict(u1=dict(state='uncertain', closed=False, windows={routing.WIN + 'sub/5h': 6}))

        def summary(debits, holds=None, given=None):
            return reconcile.summary(dict(identity='ledger-invented', holds=holds or {}, window_debits=debits), {},
                                     given)

        def hint(*args):
            return summary(*args)[1]
        self.assertEqual(reconcile.SHARED_DEBIT_POOL, claude_usage.POOL)
        self.assertIn('drop_debit', reconcile.SHARED_DEBIT_HINT)
        self.assertLessEqual(len(reconcile.SHARED_DEBIT_HINT), 512)
        # Guidance lives in the drop_debit listing; the blocking reconcile hint is unchanged (review N1).
        self.assertEqual(summary(claude)[0]['drop_debit']['guidance'], reconcile.SHARED_DEBIT_HINT)
        self.assertNotIn('guidance', summary(other)[0]['drop_debit'])
        self.assertIsNone(hint(claude))
        self.assertEqual(hint(claude, None, routing.RECONCILE_HINT), routing.RECONCILE_HINT)
        self.assertEqual(hint(claude, uncertain), reconcile.HINT)
        self.assertIsNone(hint(other))
        self.assertEqual(hint(other, uncertain, routing.RECONCILE_HINT), routing.RECONCILE_HINT + ' ' + reconcile.HINT)
        status = dict(state='ok', project_pinned=True, ledger=dict(authority_required=True, unstamped_open_holds=0,
                                                                   reconcile_hint=hint(claude)))
        self.assertIsNone(routing_launch.preflight(status, dollar=True))  # a Claude debit alone never blocks dollars

    def test_cleanup_without_non_reaping_waitid_signals_the_group_before_reaping(self):
        from runtime import codex_rate_limits as lifecycle
        order = []
        real_killpg = os.killpg
        without = types.SimpleNamespace(**{name: getattr(os, name) for name in dir(os) if name != 'waitid'})
        without.killpg = lambda pid, sig: (order.append(sig), real_killpg(pid, sig))
        with mock.patch.object(lifecycle, 'os', without):
            self.assertFalse(hasattr(lifecycle.os, 'waitid'))
            self.assertEqual(self.refresh()['state'], 'stored')  # a successful exchange still completes
            self.answer(initialize(), usage(self.now), hang='get_usage')
            self.assertEqual(self.refresh(force=True, timeout=0.5)['reason'], 'claude_timeout')
        self.assertIn(signal_module().SIGKILL, order)  # the owned group was signalled while its leader was unreaped


if __name__ == '__main__':
    unittest.main()
