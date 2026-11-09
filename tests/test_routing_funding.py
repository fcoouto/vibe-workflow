"""Money invariants for one claim model: holds and runs share one organization total (spec 6.2, 7.4, 8).

I1 per organization: B - protected - R >= 0, where R sums every run call and every open hold.
I2 pilot, task and cycle limits hold with holds included.
I3 every dollar hold names an existing organization and carries an integer micro-unit amount.
I4 conversion keeps reserved dollars and the organization unchanged.
I5 each open attempt takes exactly one concurrency place.
I6 a closed hold contributes 0; an uncertain hold contributes its full amount.
I7 the record stays under MAX_RECORD at maximum table sizes.
"""
from contextlib import closing
import copy
import json
import math
import multiprocessing
import os
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from runtime import routing, routing_settings, usage_budget as budget

M = 1_000_000
NOW = 1_000_000
SCOPE = 'scope1'
READY = dict(ref='keychain:opaque-ref', scope=SCOPE, state='ready')
ITERATIONS = int(os.environ.get('ROUTING_PROPERTY_ITERATIONS', '300'))
STEPS = 30


def policy(**updates):
    base = dict(cycle='c1', start=0, end=10 * NOW, expiry=10 * NOW, stop_at=9 * NOW, ceiling='none', amount=None,
                grant_amount=0, pilot_limit=None, task_limit=None)
    base.update(updates)
    return base


def native(task):
    return {'account': 'account', 'task': task, 'windows': []}


def window(wid='credit', limit=10**12, used=0, organization=None, revision=1, **extra):
    item = dict(id=wid, limit=limit, used=used, length=None, reset_at=None, fresh=True, **extra)
    if organization is not None:
        item.update(organization=organization, organization_revision=revision)
    return item


def funded_pool(organization, kind='api_grant', **window_options):
    return dict(billing_class=kind, windows=[window(organization=organization, **window_options)])


def sub_pool(limit=1000, used=0):
    return dict(windows=[dict(id='5h', limit=limit, used=used, length=18000, reset_at=None, rolling=True, fresh=True)])


def route(rid, pool, kind, account, demand, strategy=None):
    item = dict(route=rid, strategy_id=strategy or 's-' + rid, provider='p', model='m', effort='high', harness=rid,
                account=account, billing_class=kind, pool=pool, demand=demand, capable=True, permitted=True)
    if kind == 'included_subscription':
        item['binding_enforceable'] = True
    else:
        item.update(credential=dict(READY), funding_verified=True)
    if kind == 'gateway':
        item['integration_enabled'] = True
    return item


def settings(**updates):
    base = dict(api_usage_enabled=True, caps=dict(total=40, per_account=40))
    base.update(updates)
    return dict(routing_settings.resolve(base), revision=1)


def assignment(attempt, task=None):
    return dict(attempt_id=attempt, task=task or attempt, role='implementer', complexity_band='medium', scope=SCOPE)


def ranking(*ids):
    return dict(strategy_ids=list(ids), reason_codes=['ranking_exact'], table_version='t1', profile_digest='d1')


def observations(routes, pools, now=NOW):
    return dict(now=now, routes=routes, pools=pools)


def reserved(data, organization, task=None, cycle=None):
    """R computed independently of the ledger's own helpers."""
    total = 0
    for run in data['runs'].values():
        if run['organization'] == organization and (task is None or run['task'] == task):
            for call in run['calls'].values():
                if cycle is None or call['cycle'] == cycle:
                    total += call['cost'] if call['cost'] is not None else call['maximum']
    for hold in data['holds'].values():
        if not hold['closed'] and hold['organization'] == organization and (task is None or hold['task'] == task) \
                and (cycle is None or hold['cycle'] == cycle):
            total += hold['amount']
    return total


def protected(org):
    if org['policy'].get('allowance') == 'no-limit':
        return 0
    bp = org['policy'].get('allowance_bp', budget.DEFAULT_ALLOWANCE_BP)
    return math.ceil((budget.BASIS_POINTS - bp) * (org.get('issued') or 0) / budget.BASIS_POINTS)


class Funded(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'ledger.sqlite3'
        self.identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, self.identity)
        self.ledger.set_caps(40, 40)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def fund(self, organization='org', balance=10 * M, at=NOW, **updates):
        revision = self.ledger.configure(organization, policy(**updates), 0)
        self.ledger.observe(organization, balance, at, issued=balance)
        return revision

    def admit(self, attempt, routes, pools, ids=None, task=None, cfg=None):
        ids = ids or [item['strategy_id'] for item in routes]
        return routing.admit(assignment(attempt, task), ranking(*ids), cfg or settings(), observations(routes, pools),
                             ledger=self.ledger)

    def begin(self, run, estimate, organization='org', task=None, now=NOW, revision=1):
        return self.ledger.begin(organization, task or run, run, 'packet', estimate, revision, now, native(task or run))


class SharedOrganizationTests(Funded):
    def test_b1_funded_window_without_organization_is_excluded(self):
        for kind in ('api_grant', 'api_prepaid', 'gateway'):
            with self.subTest(kind):
                pools = dict(p=dict(billing_class=kind, windows=[window()]))
                result = self.admit('a-' + kind, [route('r', 'p', kind, 'acct', {'credit': M})], pools)
                self.assertEqual((result['status'], result['reason_code']), ('pending', 'no_funded_organization'))
        self.assertEqual(self.ledger.snapshot()['holds'], {})

    def test_b1_unknown_organization_is_excluded(self):
        pools = dict(p=funded_pool('ghost'))
        result = self.admit('a', [route('r', 'p', 'api_grant', 'acct', {'credit': M})], pools)
        self.assertEqual(result['reason_code'], 'no_funded_organization')
        self.assertEqual(self.ledger.snapshot()['organizations'], {})  # routing never creates one

    def test_blocked_organization_cannot_hide_behind_an_unlabelled_pool(self):
        self.fund()
        with self.ledger.transaction() as data:
            data['organizations']['org']['legacy_block'] = True  # derived block (S3.6a)
        pools = dict(labelled=funded_pool('org'), bare=dict(billing_class='api_grant', windows=[window()]))
        for pool in ('labelled', 'bare'):
            result = self.admit('a-' + pool, [route('r', pool, 'api_grant', 'acct', {'credit': M})], pools)
            self.assertEqual(result['status'], 'pending', pool)

    def test_b2_two_pools_on_one_organization_share_one_headroom(self):
        self.fund(balance=10 * M)  # 98%: 9.8M usable
        pools = dict(g1=funded_pool('org'), g2=funded_pool('org', kind='api_prepaid'))
        first = self.admit('a', [route('r1', 'g1', 'api_grant', 'acct1', {'credit': 7 * M})], pools)
        second = self.admit('b', [route('r2', 'g2', 'api_prepaid', 'acct2', {'credit': 7 * M})], pools)
        self.assertEqual((first['status'], first['reserved']), ('admitted', {'org:org': 8_400_000}))
        self.assertEqual(second['status'], 'pending')
        self.assertEqual(self.ledger.headroom('org'), 9_800_000 - 8_400_000)

    def test_a_funded_pool_with_several_windows_reserves_its_dollars_once(self):
        """Windows of one dollar pool measure the same spend: the organization is charged the largest, not the sum."""
        self.fund(balance=10 * M)
        pools = dict(g=dict(billing_class='api_grant', windows=[window('a', organization='org'),
                                                                window('b', organization='org', limit=6 * M)]))
        ok = self.admit('a', [route('r', 'g', 'api_grant', 'acct', {'a': 4_500_000, 'b': 3 * M})], pools)
        self.assertEqual(ok['reserved'], {'org:org': 5_400_000})
        self.assertEqual(self.ledger.headroom('org'), 9_800_000 - 5_400_000)
        strict = self.admit('b', [route('r', 'g', 'api_grant', 'acct', {'a': M, 'b': 4 * M})], pools)
        self.assertEqual(strict['status'], 'pending')  # window b: 4.8M > 6M - 5.4M already held

    def test_collector_numbers_only_add_a_stricter_minimum(self):
        self.fund(balance=10 * M)
        generous = dict(g=funded_pool('org', limit=10**12, used=0))
        self.assertEqual(self.admit('a', [route('r', 'g', 'api_grant', 'acct', {'credit': 9 * M})], generous)['status'],
                         'pending')  # 10.8M > 9.8M despite a collector limit of 10**12
        strict = dict(g=funded_pool('org', limit=2 * M, used=0))
        self.assertEqual(self.admit('b', [route('r', 'g', 'api_grant', 'acct', {'credit': 2 * M})], strict)['status'],
                         'pending')  # 2.4M > the collector's 2M

    def test_b3_hold_then_begin_sees_the_hold(self):
        self.fund(balance=10 * M)
        pools = dict(g=funded_pool('org'))
        self.assertEqual(self.admit('a', [route('r', 'g', 'api_grant', 'acct', {'credit': 7 * M})], pools)['status'],
                         'admitted')
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.begin('run', 8 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 1_400_000)
        self.begin('small', 1_400_000)

    def test_hold_counts_toward_task_pilot_and_cycle_limits(self):
        self.fund(balance=100 * M, task_limit=5 * M, pilot_limit=8 * M, ceiling='custom', amount=7 * M)
        pools = dict(g=funded_pool('org'))
        self.assertEqual(self.admit('a', [route('r', 'g', 'api_grant', 'acct', {'credit': 3 * M})], pools, task='t')['status'],
                         'admitted')  # 3.6M held for task t
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.begin('run', 2 * M, task='t')  # task limit 5M: 3.6M + 2M
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.begin('other', 3_500_000, task='u')  # cycle cap 7M: 3.6M + 3.5M
        self.begin('fits', 1_400_000, task='t')

    def test_closed_hold_contributes_nothing_and_uncertain_hold_everything(self):
        self.fund(balance=10 * M)
        pools = dict(g=funded_pool('org'))
        self.admit('a', [route('r', 'g', 'api_grant', 'acct', {'credit': 5 * M})], pools)
        self.ledger.mark_dispatched('a')
        self.ledger.close_attempt('a', 'uncertain')
        self.assertEqual(self.ledger.credit_headroom('org'), 3_800_000)
        self.assertIn('a', budget.Ledger.open_holds(self.ledger.snapshot()))
        # S3.6a: a dollar hold never closes without settlement; an uncertain one keeps everything until S3.6b.
        with self.assertRaisesRegex(budget.BudgetError, 'settlement_required'):
            self.ledger.close_attempt('a', 'complete')
        self.assertEqual(self.ledger.credit_headroom('org'), 3_800_000)
        self.assertIn('a', budget.Ledger.open_holds(self.ledger.snapshot()))

    def test_gateway_needs_its_own_organization(self):
        self.fund('grant-org', balance=10 * M)
        self.fund('gw-org', balance=2 * M)
        gw = route('gw', 'gw', 'gateway', 'gw', {'credit': M})
        pools = dict(gw=funded_pool('gw-org', kind='gateway'))
        self.assertEqual(self.admit('a', [gw], pools)['reserved'], {'org:gw-org': 1_200_000})
        self.assertEqual(self.ledger.headroom('grant-org'), 9_800_000)  # a gateway never draws on the grant


class OptionalOrganizationTests(Funded):
    """Organizations are user-defined and optional; `personal` is the built-in id the user may enable."""
    CHOICE = dict(provider='p', model='m', effort='high', harness='s', funding='subscription')

    def enable(self, organization=budget.PERSONAL, balance=10 * M):
        revision = self.ledger.enable_organization(organization, policy(), 0)
        self.ledger.observe(organization, balance, NOW, issued=balance)
        return revision

    def routes(self, organization=budget.PERSONAL, state='ready', account='personal-acct'):
        api = route('api', 'api', 'api_grant', account, {'credit': M})
        api['credential']['state'] = state
        return [api, route('s', 's', 'included_subscription', 'sub-acct', {'5h': 1})], \
            dict(api=funded_pool(organization), s=sub_pool())

    def test_no_organization_leaves_subscription_and_worker_choices_working(self):
        routes, pools = self.routes()
        only_api = self.admit('a', routes[:1], pools)
        self.assertEqual((only_api['status'], only_api['reason_code']), ('pending', 'no_funded_organization'))
        both = self.admit('b', routes, pools)
        self.assertEqual((both['status'], both['class'], both['route']), ('admitted', 'subscription', 's'))
        cfg = settings(smart_routing=False, worker_choices=dict(implementer=[self.CHOICE]))
        chosen = routing.admit(assignment('c'), ranking(), cfg, observations(routes, pools), ledger=self.ledger)
        self.assertEqual((chosen['status'], chosen['mode']), ('admitted', 'worker_choice'))
        self.assertEqual(self.ledger.snapshot()['organizations'], {})

    def test_routing_on_with_api_usage_never_creates_an_organization(self):
        routes, pools = self.routes()
        for i, cfg in enumerate((settings(), routing_settings.resolve(dict(smart_routing=True)))):
            routing.admit(assignment('a%d' % i), ranking('s-api'), dict(cfg, revision=1), observations(routes, pools),
                          ledger=self.ledger)
        self.assertEqual(self.ledger.snapshot()['organizations'], {})

    def test_enabled_personal_with_a_ready_key_funds_its_route(self):
        self.enable()
        routes, pools = self.routes()
        result = self.admit('a', routes[:1], pools)
        self.assertEqual((result['status'], result['reserved']), ('admitted', {'org:personal': 1_200_000}))
        self.assertNotIn('sk-', json.dumps(self.ledger.snapshot()))

    def test_enabled_personal_with_a_key_not_ready_is_unavailable_with_a_reason(self):
        self.enable()
        for state in ('pending', 'locked', 'revoked'):
            routes, pools = self.routes(state=state)
            result = self.admit('a-' + state, routes[:1], pools)
            self.assertEqual(result['status'], 'pending')
            self.assertIn('credential_unavailable', result['reason_codes'])

    def test_a_named_organization_is_independent_of_personal(self):
        self.enable(balance=10 * M)
        self.enable('team', balance=10 * M)
        self.ledger.set_caps(40, 1)
        mine, my_pools = self.routes()
        team, team_pools = self.routes('team', account='team-acct')
        team[0].update(route='team-api', strategy_id='s-team', pool='team')
        pools = dict(my_pools, team=team_pools['api'])
        self.assertEqual(self.admit('a', mine[:1], pools)['status'], 'admitted')
        self.assertEqual(self.admit('b', mine[:1], pools)['reason_code'], 'concurrency_cap')  # personal account full
        self.assertEqual(self.admit('c', team[:1], pools)['reserved'], {'org:team': 1_200_000})
        self.assertEqual((self.ledger.headroom(budget.PERSONAL), self.ledger.headroom('team')),
                         (9_800_000 - 1_200_000, 9_800_000 - 1_200_000))
        data = self.ledger.snapshot()
        self.assertEqual((data['holds']['a']['organization'], data['holds']['c']['organization']), ('personal', 'team'))

    def test_disabling_keeps_in_flight_holds_counted_and_excludes_new_work(self):
        self.enable()
        routes, pools = self.routes()
        pools['api']['grant'] = dict(remaining=5 * M, expected_spend=0, expires_at=NOW + 3600)
        self.admit('a', routes[:1], pools)
        self.ledger.mark_dispatched('a')
        self.ledger.disable_organization(budget.PERSONAL)
        data = self.ledger.snapshot()
        self.assertIn('a', budget.Ledger.open_holds(data))
        self.assertEqual(reserved(data, budget.PERSONAL), 1_200_000)
        self.assertEqual(self.ledger.credit_headroom(budget.PERSONAL), 9_800_000 - 1_200_000)
        result = self.admit('b', routes, pools)
        self.assertEqual((result['route'], result['pressure']['e']), ('s', None))  # no API route, no expiry pressure
        with self.assertRaisesRegex(budget.BudgetError, 'organization_disabled'):
            self.begin('run', M, organization=budget.PERSONAL)
        # S3.6a: a dispatched dollar hold closes only through settlement (here a measured zero cost).
        usage = dict(source='provider_receipt', requests=[dict(kind='primary', model='m', cost_usd_micros=0, tokens=dict(
            input=0, output=0, reasoning=0, cache_read=0, cache_write=0))])
        self.assertEqual(routing.record_event(self.ledger, dict(event_id='c', attempt_id='a', kind='complete',
                                                                usage=usage), NOW)['outcome'], 'complete')
        self.ledger.enable_organization(budget.PERSONAL, policy(), 1)
        self.assertEqual(self.admit('c', routes[:1], pools)['status'], 'admitted')


class MultiWindowFundingTests(Funded):
    def test_the_charged_amount_must_fit_every_window(self):
        """Reviewer probe: window A allows 2M; a 7.2M charge driven by window B must not be admitted."""
        self.fund(balance=100 * M)
        pools = dict(g=dict(billing_class='api_grant', windows=[window('A', organization='org', limit=2 * M),
                                                                window('B', organization='org')]))
        result = self.admit('a', [route('r', 'g', 'api_grant', 'acct', {'A': M, 'B': 6 * M})], pools)
        self.assertEqual((result['status'], result['reason_codes'][1:]), ('pending', ['insufficient_headroom']))
        self.assertEqual(self.ledger.snapshot()['holds'], {})
        fits = self.admit('b', [route('r', 'g', 'api_grant', 'acct', {'A': M, 'B': M})], pools)
        self.assertEqual(fits['reserved'], {'org:org': 1_200_000})


class ObservationNitTests(Funded):
    def test_negative_protected_cannot_inflate_subscription_headroom(self):
        pools = dict(s=dict(windows=[dict(id='5h', limit=100, used=100, protected=-1e9, length=18000, reset_at=None,
                                          rolling=True, fresh=True)]))
        result = self.admit('a', [route('s', 's', 'included_subscription', 'B', {'5h': 10_000})], pools)
        self.assertEqual(result['status'], 'pending')
        self.assertIn('invalid_observation', result['reason_codes'])
        self.assertEqual(self.ledger.snapshot()['holds'], {})

    def test_slash_in_pool_or_window_ids_is_rejected_so_keys_cannot_collide(self):
        for label, pools, demand in (('pool', {'a/b': sub_pool()}, {'5h': 1}),
                                     ('window', dict(s=dict(windows=[dict(id='b/5h', limit=1000, used=0, length=1,
                                                                          reset_at=None, rolling=True, fresh=True)])),
                                      {'b/5h': 1})):
            with self.subTest(label):
                pool = next(iter(pools))
                result = self.admit('a-' + label, [route('s', pool, 'included_subscription', 'B', demand)], pools)
                self.assertEqual((result['status'], result['reason_code']), ('pending', 'invalid_observation'))

    def test_a_key_that_is_not_ready_is_the_pending_reason(self):
        self.fund()
        api = route('r', 'g', 'api_grant', 'acct', {'credit': M})
        api['credential']['state'] = 'locked'
        result = self.admit('a', [api], dict(g=funded_pool('org')))
        self.assertEqual((result['status'], result['reason_code']), ('pending', 'credential_unavailable'))


def authorize(ledger, attempt, now=None):
    """Dispatch a hold with an issued authority (S3.4); `convert_hold` accepts only such holds."""
    ids = dict(session_id='ses_' + attempt, message_id='msg_' + attempt)
    return ledger.dispatch_hold(attempt, '0' * 64, ids, lambda data, moment: None, NOW if now is None else now)


class ConversionTests(Funded):
    def setUp(self):
        super().setUp()
        self.fund('org', balance=10 * M)
        self.fund('org2', balance=10 * M)
        pools = dict(g=funded_pool('org'), s=sub_pool())
        routes = [route('r', 'g', 'api_grant', 'acct-A', {'credit': M})]
        self.assertEqual(self.admit('a', routes, pools)['reserved'], {'org:org': 1_200_000})
        authorize(self.ledger, 'a')

    def convert(self, estimate, organization='org', **options):
        return self.ledger.convert_hold('a', organization, 'packet', estimate, 1, NOW, native('a'), **options)

    def test_a_request_above_the_hold_is_rejected_and_changes_nothing(self):
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'hold_exceeded'):
            self.convert(3 * M)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_another_organization_is_rejected_and_changes_nothing(self):
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'hold_organization_mismatch'):
            self.convert(M, organization='org2')
        self.assertEqual(self.ledger.snapshot(), before)

    def test_conversion_moves_the_hold_one_to_one_and_keeps_one_place(self):
        before = self.ledger.snapshot()
        run = self.convert(M)  # the run's own request (1M) fits the 1.2M hold
        after = self.ledger.snapshot()
        self.assertEqual((run['organization'], run['account']), ('org', 'acct-A'))
        self.assertEqual(run['calls']['count']['maximum'], 1_200_000)
        self.assertEqual(reserved(after, 'org'), reserved(before, 'org'))
        self.assertEqual(reserved(after, 'org2'), 0)
        self.assertEqual(budget.Ledger.open_accounts(after), ['acct-A'])
        self.assertEqual((after['holds']['a']['closed'], after['holds']['a']['amount']), (True, 0))
        with self.assertRaisesRegex(budget.BudgetError, 'convert_forbidden'):
            self.convert(M)

    def test_top_up_after_conversion_goes_through_headroom(self):
        self.convert(M)
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.ledger.top_up('a', 9 * M, 1, NOW)
        self.ledger.top_up('a', 8_600_000, 1, NOW)
        self.assertEqual(self.ledger.headroom('org'), 0)

    def test_a_mixed_hold_keeps_its_window_claim_until_the_run_closes(self):
        pools = dict(g=funded_pool('org'), s=sub_pool())
        mixed = self.admit('m', [route('r', 'g', 'api_grant', 'acct-M', {'credit': M})], pools)
        self.assertEqual(mixed['status'], 'admitted')
        with self.ledger.transaction() as data:  # one attempt holding both a dollar and a window claim
            data['holds']['m']['windows'] = {'win:s/5h': 12}
        authorize(self.ledger, 'm')
        self.ledger.convert_hold('m', 'org', 'packet', M, 1, NOW, native('m'))
        state = self.ledger.snapshot()
        self.assertEqual(state['holds']['m']['state'], 'converted')
        a = {'org:org': 1_200_000}  # setUp's hold `a` stays open throughout
        self.assertEqual(budget.Ledger.held(state), dict(a, **{'win:s/5h': 12}))
        self.ledger.dispatch('m', 'count', 1, NOW)
        for outcome in budget.HOLD_OUTCOMES:
            with self.subTest(outcome), self.assertRaisesRegex(budget.BudgetError, 'close_forbidden'):
                self.ledger.close_attempt('m', outcome)  # the run owns the attempt now
        self.assertEqual(budget.Ledger.held(self.ledger.snapshot()), dict(a, **{'win:s/5h': 12}))
        self.assertEqual(budget.Ledger.open_accounts(self.ledger.snapshot()), ['acct-A', 'acct-M'])
        self.ledger.complete('m', 'count', 1)
        self.ledger.close_run('m')
        state = self.ledger.snapshot()
        self.assertEqual(budget.Ledger.held(state), a)  # the window claim is released together with the run
        self.assertEqual(budget.Ledger.open_accounts(state), ['acct-A'])

    def test_only_a_hold_dispatched_with_an_authority_converts(self):
        """S3.4 inverts the S2 rule: the dispatched hold converts; admitted, bare-dispatched and uncertain ones do not."""
        issued = self.ledger.snapshot()['holds']['a'].pop('dispatch')
        for label, change in (('admitted', dict(state='admitted')), ('bare dispatch', dict(state='dispatched')),
                              ('uncertain', dict(state='uncertain', dispatch=issued))):
            with self.subTest(label):
                with self.ledger.transaction() as data:
                    data['holds']['a'].pop('dispatch', None)
                    data['holds']['a'].update(change)
                before = self.ledger.snapshot()
                with self.assertRaisesRegex(budget.BudgetError, 'convert_forbidden'):
                    self.convert(M)
                self.assertEqual(self.ledger.snapshot(), before)

    def test_per_account_cap_counts_provider_accounts_across_runs_and_holds(self):
        self.ledger.set_caps(40, 1)
        self.convert(M)
        pools = dict(g=funded_pool('org'))
        again = self.admit('b', [route('r', 'g', 'api_grant', 'acct-A', {'credit': M})], pools)
        self.assertEqual(again['reason_code'], 'concurrency_cap')


class ExceptionFuzzTests(Funded):
    """`admit` never raises on a malformed snapshot; it returns pending and writes nothing."""
    def setUp(self):
        super().setUp()
        self.fund(balance=10 * M)

    @staticmethod
    def base():
        return ([route('r', 'g', 'api_grant', 'acct', {'credit': M}), route('s', 's', 'included_subscription', 'B', {'5h': 1})],
                dict(g=funded_pool('org'), s=sub_pool()))

    def probes(self):
        def window_set(**change):
            return lambda routes, pools, obs, cfg: pools['s']['windows'][0].update(change)

        def route_set(**change):
            return lambda routes, pools, obs, cfg: routes[0].update(change)

        def obs_set(**change):
            return lambda routes, pools, obs, cfg: obs.update(change)
        return dict(
            reset_text=window_set(reset_at='x'), reset_nan=window_set(reset_at=float('nan')),
            limit_inf=window_set(limit=float('inf')), used_huge=window_set(used=10**30),
            limit_huge=window_set(limit=10**30), demand_huge=route_set(demand={'credit': 10**30}),
            demand_float=route_set(demand={'credit': 1.5}), demand_list=route_set(demand=[1]),
            bound_list=route_set(next_request_bound=[1]), bound_text=route_set(next_request_bound='x'),
            bound_huge=route_set(next_request_bound={'credit': 10**30}),
            pools_none=obs_set(pools=None), routes_none=obs_set(routes=None), now_none=obs_set(now=None),
            now_text=obs_set(now='x'), now_nan=obs_set(now=float('nan')),
            no_windows=lambda routes, pools, obs, cfg: pools['s'].pop('windows'),
            windows_none=lambda routes, pools, obs, cfg: pools['s'].update(windows=None),
            empty_windows=lambda routes, pools, obs, cfg: pools['s'].update(windows=[]),
            window_text=lambda routes, pools, obs, cfg: pools['s'].update(windows=['x']),
            route_text=lambda routes, pools, obs, cfg: routes.append('x'),
            pool_text=lambda routes, pools, obs, cfg: pools.update(s='x'),
            huge_id=route_set(route='r' * 100_000), huge_account=route_set(account='a' * 100_000),
            huge_window_id=window_set(id='w' * 100_000), org_revision_huge=lambda routes, pools, obs, cfg:
                pools['g']['windows'][0].update(organization_revision=10**30),
            org_text=lambda routes, pools, obs, cfg: pools['g']['windows'][0].update(organization=['org']),
            grant_text=lambda routes, pools, obs, cfg: pools['g'].update(grant='x'),
            grant_nan=lambda routes, pools, obs, cfg: pools['g'].update(grant=dict(remaining=float('nan'),
                                                                                    expected_spend=0, expires_at=NOW + 10)),
            caps_junk=lambda routes, pools, obs, cfg: cfg.update(caps='x'),
            caps_zero=lambda routes, pools, obs, cfg: cfg.update(caps=dict(total=0, per_account=0)),
            margin_text=lambda routes, pools, obs, cfg: cfg.update(margin_bp='x'),
            settings_extra=lambda routes, pools, obs, cfg: cfg.update(junk=1),
            revision_text=lambda routes, pools, obs, cfg: cfg.update(revision='x'),
            horizon_text=obs_set(minimum_horizon='x'), epoch_nan=obs_set(epoch_at=float('nan')),
            snapshot_huge=obs_set(snapshot_id='s' * 100_000),
            many_routes=lambda routes, pools, obs, cfg: routes.extend([copy.deepcopy(routes[1])] * 10_000),
        )

    def test_malformed_inputs_are_pending_and_write_nothing(self):
        for label, mutate in self.probes().items():
            with self.subTest(label):
                routes, pools = self.base()
                obs, cfg = observations(routes, pools), settings()
                mutate(routes, pools, obs, cfg)
                before = self.ledger.snapshot()
                try:
                    result = routing.admit(assignment('a-' + label), ranking('s-r', 's-s'), cfg, obs, ledger=self.ledger)
                except Exception as error:  # noqa: BLE001 - the contract is "never raises"
                    self.fail('%s raised %r' % (label, error))
                self.assertEqual(result['status'], 'pending', label)
                self.assertIsInstance(result['reason_code'], str)
                self.assertLess(len(result['reason_code']), 64)
                self.assertEqual(self.ledger.snapshot(), before)

    def test_malformed_ranking_and_assignment_are_pending(self):
        routes, pools = self.base()
        for label, (assign, rank) in dict(
                ranking_none=(assignment('a'), None), ranking_text=(assignment('a'), 'x'),
                ids_text=(assignment('a'), dict(strategy_ids='x')), codes_huge=(assignment('a'), dict(
                    strategy_ids=['s-r'], reason_codes=['x' * 100_000])),
                attempt_huge=(assignment('a' * 100_000), ranking('s-r')),
                attempt_control=(assignment('a\n'), ranking('s-r'))).items():
            with self.subTest(label):
                result = routing.admit(assign, rank, settings(), observations(routes, pools), ledger=self.ledger)
                self.assertEqual(result['status'], 'pending', label)

    def test_decide_errors_become_pending(self):
        routes, pools = self.base()
        original = routing._decide

        def broken(*args):
            raise ZeroDivisionError('boom')
        routing._decide = broken
        try:
            result = routing.admit(assignment('a'), ranking('s-r'), settings(), observations(routes, pools),
                                   ledger=self.ledger)
        finally:
            routing._decide = original
        self.assertEqual((result['status'], result['reason_code']), ('pending', 'admission_error'))


# ---------------------------------------------------------------------------------------------------------
# Seeded property test
# ---------------------------------------------------------------------------------------------------------
ORGS = ('o1', 'o2')
CAPS = (6, 3)


class Property:
    """One random sequence over 2 organizations x {subscription, api_grant, gateway} with a shared organization."""
    def __init__(self, test, seed):
        self.test, self.rng = test, random.Random(seed)
        self.seed = seed
        self.clock = NOW
        self.ids = 0
        temp = Path(test.temp.name) / ('s%d' % seed)
        self.path = temp / 'ledger.sqlite3'
        identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, identity)
        self.ledger.set_caps(*CAPS)
        # One sequence in four has no organization at all: funded routes are simply unavailable.
        self.orgs = () if seed % 4 == 0 else ORGS
        if self.orgs:
            self.ledger.enable_organization('o1', policy(allowance_bp=9800, ceiling='custom', amount=40 * M,
                                                         task_limit=15 * M, pilot_limit=50 * M), 0)
            self.ledger.enable_organization('o2', policy(allowance_bp=10000), 0)
            self.ledger.observe('o1', 30 * M, self.clock, issued=30 * M)
            self.ledger.observe('o2', 20 * M, self.clock, issued=20 * M)
        self.pools = dict(sub=dict(windows=[dict(id='5h', limit=1000, used=0, length=18000, reset_at=None, rolling=True,
                                                 fresh=True)]),
                          g1=funded_pool('o1'), g1b=funded_pool('o1', kind='api_prepaid'),
                          gw=funded_pool('o2', kind='gateway'))
        self.kinds = dict(sub='included_subscription', g1='api_grant', g1b='api_prepaid', gw='gateway')

    def close(self):
        self.ledger.close()

    def fresh_id(self, prefix):
        self.ids += 1
        return '%s%d' % (prefix, self.ids)

    def check(self, step):
        data = self.ledger.snapshot()
        where = 'seed %d step %s' % (self.seed, step)
        self.test.assertEqual(sorted(data['organizations']), sorted(self.orgs), 'routing created an organization')
        for name in self.orgs:
            org = data['organizations'][name]
            pol = org['policy']
            r = reserved(data, name)
            self.test.assertGreaterEqual(org['balance'] - protected(org) - r, 0, 'I1 ' + where)
            if pol['pilot_limit'] is not None:
                self.test.assertLessEqual(r, pol['pilot_limit'], 'I2 pilot ' + where)
            if pol['task_limit'] is not None:
                for task in {run['task'] for run in data['runs'].values()} | {h['task'] for h in data['holds'].values()}:
                    self.test.assertLessEqual(reserved(data, name, task), pol['task_limit'], 'I2 task ' + where)
            if pol['ceiling'] == 'custom':
                self.test.assertLessEqual(reserved(data, name, cycle=pol['cycle']), pol['amount'], 'I2 cycle ' + where)
        for attempt, hold in data['holds'].items():
            self.test.assertIs(type(hold['amount']), int, 'I3 ' + where)
            if hold['amount']:
                self.test.assertIn(hold['organization'], data['organizations'], 'I3 ' + where)
            if hold['closed']:
                self.test.assertNotIn(attempt, budget.Ledger.open_holds(data), 'I6 ' + where)
            if hold['state'] == 'uncertain':
                self.test.assertFalse(hold['closed'], 'I6 ' + where)
        attempts = {i for i, run in data['runs'].items() if not run['closed']} | \
            {i for i, hold in data['holds'].items() if not hold['closed']}
        accounts = budget.Ledger.open_accounts(data)
        self.test.assertEqual(len(accounts), len(attempts), 'I5 ' + where)
        self.test.assertLessEqual(len(accounts), CAPS[0], 'I5 ' + where)
        for account in set(accounts):
            self.test.assertLessEqual(accounts.count(account), CAPS[1], 'I5 ' + where)

    def step(self, index):
        op = self.rng.choice(('admit', 'admit', 'admit', 'begin', 'begin', 'top_up', 'authorize', 'authorize', 'convert', 'convert',
                              'dispatched', 'uncertain', 'close', 'run_dispatch', 'complete', 'observe', 'toggle'))
        self.clock += 1
        before = self.ledger.snapshot()
        try:
            getattr(self, 'op_' + op)(before)
        except budget.BudgetError:
            self.test.assertEqual(self.ledger.snapshot(), before, 'refused %s changed state, seed %d' % (op, self.seed))
        self.check('%d:%s' % (index, op))

    def amount(self):
        return self.rng.choice((self.rng.randrange(1, 3 * M), self.rng.randrange(3 * M, 12 * M), 0))

    def op_admit(self, before):
        chosen = self.rng.sample(sorted(self.pools), self.rng.randint(1, 4))
        routes = []
        for pool in chosen:
            demand = {'5h': self.rng.randrange(1, 400)} if pool == 'sub' else {'credit': self.amount()}
            account = self.rng.choice(('A', 'B', 'C'))
            routes.append(route('r-' + pool, pool, self.kinds[pool], account, demand))
        task = self.rng.choice(('t1', 't2', 't3'))
        cfg = settings(caps=dict(total=CAPS[0], per_account=CAPS[1]))
        result = routing.admit(assignment(self.fresh_id('h'), task), ranking(*[r['strategy_id'] for r in routes]),
                               cfg, observations(routes, self.pools, self.clock), ledger=self.ledger)
        self.test.assertIn(result['status'], ('admitted', 'pending'))
        if result['status'] == 'pending':
            self.test.assertEqual(self.ledger.snapshot(), before)
        if not self.orgs:
            self.test.assertTrue(result['status'] == 'pending' or result['class'] == 'subscription')

    def op_begin(self, before):
        task = self.rng.choice(('t1', 't2', 't3'))
        run = self.fresh_id('r')
        self.ledger.begin(self.rng.choice(ORGS), task, run, 'packet', self.amount(), 1, self.clock, native(task))

    def open_runs(self, before):
        return sorted(i for i, run in before['runs'].items() if not run['closed'])

    def op_top_up(self, before):
        runs = self.open_runs(before)
        if runs:
            self.ledger.top_up(self.rng.choice(runs), self.amount(), 1, self.clock)

    def op_authorize(self, before):
        funded = sorted(i for i, h in before['holds'].items() if not h['closed'] and h['state'] == 'admitted'
                        and h['amount'])
        attempt = self.rng.choice(funded) if funded else self.pick_hold(before, ('admitted',))
        if attempt:
            authorize(self.ledger, attempt, self.clock)

    def op_convert(self, before):
        holds = sorted(i for i, h in before['holds'].items() if not h['closed'] and h['amount'])
        authorized = [i for i in holds if 'dispatch' in before['holds'][i] and before['holds'][i]['state'] == 'dispatched']
        if authorized and self.rng.random() < 0.8:  # S3.4: only these convert; the rest exercise the refusal
            holds = authorized
        if not holds:
            return
        attempt = self.rng.choice(holds)
        hold = before['holds'][attempt]
        organization = hold['organization'] if self.rng.random() < 0.8 else self.rng.choice(ORGS)
        estimate = self.rng.choice((hold['amount'], hold['amount'] // 2, hold['amount'] + 1))
        run = self.ledger.convert_hold(attempt, organization, 'packet', estimate, 1, self.clock, native(hold['task']))
        after = self.ledger.snapshot()
        self.test.assertEqual(run['organization'], hold['organization'], 'I4 seed %d' % self.seed)
        self.test.assertEqual(run['account'], hold['account'], 'I5 seed %d' % self.seed)
        for name in ORGS:
            self.test.assertEqual(reserved(after, name), reserved(before, name), 'I4 seed %d' % self.seed)

    def pick_hold(self, before, states):
        holds = sorted(i for i, h in before['holds'].items() if not h['closed'] and h['state'] in states)
        return self.rng.choice(holds) if holds else None

    def op_dispatched(self, before):
        attempt = self.pick_hold(before, ('admitted',))
        if attempt:
            self.ledger.mark_dispatched(attempt)

    def op_uncertain(self, before):
        attempt = self.pick_hold(before, ('admitted', 'dispatched'))
        if attempt:
            self.ledger.mark_uncertain(attempt)

    def op_close(self, before):
        attempt = self.pick_hold(before, ('admitted', 'dispatched', 'uncertain'))
        if attempt:
            self.ledger.close_attempt(attempt, self.rng.choice(budget.HOLD_OUTCOMES))

    def op_run_dispatch(self, before):
        runs = [i for i in self.open_runs(before) if before['runs'][i]['calls']['count']['state'] == 'reserved']
        if runs:
            self.ledger.dispatch(self.rng.choice(runs), 'count', 1, self.clock)

    def op_complete(self, before):
        runs = [i for i in self.open_runs(before) if before['runs'][i]['calls']['count']['state'] == 'dispatching']
        if runs:
            run = self.rng.choice(runs)
            self.ledger.complete(run, 'count', self.rng.randrange(0, before['runs'][run]['calls']['count']['maximum'] + 1))

    def op_toggle(self, before):
        """The user disables or re-enables an organization; open claims stay counted either way."""
        if self.orgs:
            name = self.rng.choice(self.orgs)
            org = before['organizations'][name]
            if org.get('disabled'):
                self.ledger.enable_organization(name, org['policy'], org['revision'])
            else:
                self.ledger.disable_organization(name)

    def op_observe(self, before):
        """External spend lowers B, but a well-behaved provider never reports below what is reserved."""
        if not self.orgs:
            return
        name = self.rng.choice(self.orgs)
        org = before['organizations'][name]
        floor = protected(org) + reserved(before, name)
        if org['balance'] > floor:
            self.ledger.observe(name, self.rng.randrange(floor, org['balance'] + 1), self.clock)


class PropertyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_invariants_hold_after_every_step_of_random_sequences(self):
        admitted = converted = funded = 0
        for seed in range(ITERATIONS):
            run = Property(self, seed)
            try:
                for index in range(STEPS):
                    run.step(index)
                state = run.ledger.snapshot()
                admitted += len(state['admissions'])
                funded += sum(1 for record in state['admissions'] if record['class'] == 'api')
                converted += sum(1 for attempt in state['runs'] if attempt in state['holds'])
            finally:
                run.close()
        self.assertGreater(admitted, ITERATIONS)  # the sequences exercise admission, not only refusals
        self.assertGreater(funded, ITERATIONS // 4)
        self.assertGreater(converted, ITERATIONS // 10)


# ---------------------------------------------------------------------------------------------------------
# Multi-process race
# ---------------------------------------------------------------------------------------------------------
def racer(path, identity, index, queue):
    try:
        with budget.Ledger(path, identity) as ledger:
            if index % 2:
                routes = [route('r', 'g', 'api_grant', 'acct%d' % index, {'credit': 3 * M})]
                result = routing.admit(assignment('p%d' % index), ranking('s-r'), settings(),
                                       observations(routes, dict(g=funded_pool('org'))), ledger=ledger)
                queue.put(result['status'])
            else:
                ledger.begin('org', 'b%d' % index, 'b%d' % index, 'packet', 3_600_000, 1, NOW, native('b%d' % index))
                queue.put('admitted')
    except budget.BudgetError as error:
        queue.put(str(error))
    except Exception as error:  # noqa: BLE001 - reported to the parent
        queue.put('raised %r' % error)


class RaceTests(Funded):
    def test_eight_processes_mixing_admit_and_begin_never_overdraw(self):
        self.fund(balance=15 * M, allowance_bp=10000)  # four 3.6M claims fit, the other four do not
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        processes = [ctx.Process(target=racer, args=(self.path, self.identity, i, queue)) for i in range(8)]
        for process in processes: process.start()
        results = [queue.get(timeout=60) for _ in processes]
        for process in processes: process.join(60)
        self.assertFalse([r for r in results if r.startswith('raised')], results)
        self.assertEqual(results.count('admitted'), 4, results)
        data = self.ledger.snapshot()
        self.assertEqual(reserved(data, 'org'), 4 * 3_600_000)
        self.assertGreaterEqual(15 * M - reserved(data, 'org'), 0)


# ---------------------------------------------------------------------------------------------------------
# Capacity (I7)
# ---------------------------------------------------------------------------------------------------------
def long(tag):
    return (tag + 'x' * 256)[:256]


def unique(index, value):
    return ('%05d' % index + value)[:256]


def padded(item, size):
    """`item` with a `pad` field making its compact JSON exactly `size` characters."""
    item = dict(item, pad='')
    item['pad'] = 'p' * (size - len(json.dumps(item, separators=(',', ':'))))
    return item


class CapacityTests(Funded):
    def long_admission(self, attempt, windows):
        """A real admission with 256-character identifiers everywhere and `windows` demanded windows."""
        organization = long('org')
        if organization not in self.ledger.snapshot()['organizations']:
            self.ledger.configure(organization, policy(cycle=long('cycle')), 0)
            self.ledger.observe(organization, 10**12, NOW, issued=10**12)
        ids = [long('w%d' % i) for i in range(windows)]
        pools = {long('pool'): dict(billing_class='api_grant', windows=[window(i, organization=organization)
                                                                        for i in ids])}
        api = route(long('r'), long('pool'), 'api_grant', long('acct%s' % attempt), {i: M for i in ids})
        api.update(strategy_id=long('sid'), model=long('m'), harness=long('h'), provider=long('p'))
        api['credential']['ref'] = long('ref')
        obs = dict(observations([api], pools), snapshot_id=long('snap'))
        rank = dict(strategy_ids=[long('sid')], reason_codes=[long('code')] * routing.MAX_REASONS,
                    table_version=long('t'), profile_digest=long('d'))
        return routing.admit(assignment(long(attempt), long('task')), rank, settings(), obs, ledger=self.ledger)

    def test_an_oversized_admission_is_pending_and_writes_nothing(self):
        before = self.ledger.snapshot()
        result = self.long_admission('big', routing.MAX_WINDOWS)
        self.assertEqual((result['status'], result['reason_code']), ('pending', 'record_too_large'))
        self.assertEqual(self.ledger.snapshot()['holds'], before['holds'])
        self.assertEqual(self.long_admission('one', 1)['status'], 'admitted')  # long ids alone still fit

    def test_a_full_admissions_table_never_blocks_and_keeps_open_attempts(self):
        record = lambda attempt: dict(attempt_id=attempt, task='t', bucket=[], shares={})  # noqa: E731
        decide = lambda attempt: (lambda data: dict(record=record(attempt), vector={}, account='a%s' % attempt))  # noqa: E731
        self.ledger.set_caps(budget.MAX_HOLDS, budget.MAX_HOLDS)
        self.ledger.admit('open', decide('open'))  # the oldest record belongs to an attempt that stays open
        for i in range(budget.MAX_ADMISSIONS + 3):
            self.ledger.admit('c%d' % i, decide('c%d' % i))
            self.ledger.close_attempt('c%d' % i, 'not_dispatched')
        data = self.ledger.snapshot()
        self.assertEqual(len(data['admissions']), budget.MAX_ADMISSIONS)
        self.assertIsNotNone(budget.Ledger.admission(data, 'open'))  # closed attempts' records go first
        self.assertIsNone(budget.Ledger.admission(data, 'c0'))
        self.assertTrue(self.ledger.admit('open', decide('open'))['duplicate'])

    def test_the_worst_case_ledger_fits_under_the_hard_limit_with_margin(self):
        """Every table at its bound, every entry at its size bound, 256-character identifiers, real native keys,
        every native task finished and every call carrying a maximal `incomplete` record."""
        organization = long('org')
        self.ledger.configure(organization, policy(cycle=long('cycle'), pilot_limit=budget.MAX_INTEGER,
                                                   task_limit=budget.MAX_INTEGER), 0)
        self.ledger.observe(organization, budget.MAX_INTEGER, NOW, issued=budget.MAX_INTEGER,
                            lots=[dict(id=unique(i, long('lot')), balance=budget.MAX_INTEGER,
                                       expiry=budget.MAX_INTEGER, verified=True) for i in range(64)])
        run_id = long('run')
        gate = lambda data, now: dict(pin=long('pin'), maximum=budget.MAX_INTEGER)  # noqa: E731
        self.ledger.begin(organization, long('task'), run_id, long('digest'), M, 1, NOW,
                          dict(account=long('na'), task=long('task'), windows=[]), provision=M, live_gate=gate)
        self.ledger.dispatch(run_id, 'count', 1, NOW, live_gate=gate)
        self.ledger.complete(run_id, 'count', 1)
        self.ledger.reserve_generation(run_id, long('digest2'), M, 1, NOW, live_gate=gate)
        data = self.ledger.snapshot()
        run = data['runs'][run_id]
        run.update(consent=dict(scope=long('scope'), revision=budget.MAX_INTEGER,
                                qualification_revision=budget.MAX_INTEGER),
                   transferred_from=dict(run=long('parent'), amount=budget.MAX_INTEGER), account=long('acct'),
                   revision=budget.MAX_INTEGER)
        incomplete = padded(dict(reason='unknown'), budget.MAX_INCOMPLETE_BYTES)
        for call in run['calls'].values():
            call.update(maximum=budget.MAX_INTEGER, cost=budget.MAX_INTEGER, provision=budget.MAX_INTEGER,
                        incomplete=incomplete)
        # An over-bound marker (S3.6a) on at most one call per run: a count call that is over-bound never gets a
        # generation call, and a generation call follows only a completed count. `cost` stays at its bound too.
        run['calls']['count']['over_bound'] = budget.MAX_INTEGER
        data['organizations'][organization]['revision'] = budget.MAX_INTEGER
        # S3.6 settlement: per-cycle settled charges, per-task charges and the settlement each closed hold carries.
        data['organizations'][organization]['settled'] = dict(total=budget.MAX_INTEGER, cycles={
            unique(i, long('cycle')): budget.MAX_INTEGER for i in range(budget.MAX_SETTLED_CYCLES)})
        data['settled_tasks'] = {'%064x' % i: budget.MAX_INTEGER for i in range(budget.MAX_SETTLED_TASKS)}
        data['organizations'][organization]['legacy_block'] = True  # the only stored block source (v4)
        data['admissions'] = [padded(dict(attempt_id=unique(i, long('a'))), budget.MAX_ADMISSION_BYTES)
                              for i in range(budget.MAX_ADMISSIONS)]
        # An admitted hold at its bound plus the fixed fields dispatch, uncertainty and proof add later (S3.4).
        data['holds'] = {unique(i, long('h')): padded(dict(closed=False), budget.MAX_HOLD_BYTES + budget.DISPATCH_HOLD_BYTES
                                                     + budget.SETTLEMENT_HOLD_BYTES)
                         for i in range(budget.MAX_HOLDS)}
        data['seen_attempts'] = [unique(i, long('s')) for i in range(budget.MAX_SEEN)]
        runs, native = {}, {}
        for i in range(budget.MAX_RUNS):  # one native task per run, keyed exactly as `_native` keys it
            account = unique(i, long('na'))
            key = json.dumps([account, long('task')])
            native[key] = dict(account=account, task=long('task'), latest_at=budget.MAX_INTEGER)
            runs[unique(i, run_id)] = dict(copy.deepcopy(run), native=key)
        data['runs'], data['native'] = runs, native
        data['finished_native'] = list(native)  # bounded by native entries, which are bounded by runs
        data['caps'] = dict(total=budget.MAX_INTEGER, per_account=budget.MAX_INTEGER)
        raw = json.dumps(data, allow_nan=False, separators=(',', ':'))
        sizes = {key: len(json.dumps(value, separators=(',', ':'))) for key, value in data.items()}
        self.assertLessEqual(len(raw), budget.MAX_RECORD * 0.9, sizes)  # at least 10% margin
        self.assertGreater(len(raw), budget.SOFT_RECORD)  # beyond the soft limit: only settlement continues
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE ledger SET body=?', (raw,)); db.commit()
        self.ledger.set_caps(5, 2)  # a full ledger still reads and commits a non-growing write
        with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
            self.ledger.configure(long('another'), policy(), 0)

    def test_claim_and_hold_table_bounds_refuse_new_admissions(self):
        decide = lambda attempt, vector: (lambda data: dict(  # noqa: E731
            record=dict(attempt_id=attempt, task='t', bucket=[], shares={}), vector=vector, account=attempt))
        too_many = {'win:p/w%d' % i: 1 for i in range(budget.MAX_CLAIMS + 1)}
        with self.assertRaisesRegex(budget.BudgetError, 'record_too_large'):
            self.ledger.admit('wide', decide('wide', too_many))
        self.ledger.set_caps(budget.MAX_HOLDS * 2, budget.MAX_HOLDS * 2)
        for i in range(budget.MAX_HOLDS):
            self.ledger.admit('h%d' % i, decide('h%d' % i, {}))
        with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
            self.ledger.admit('one-more', decide('one-more', {}))  # open holds are never pruned
        self.assertEqual(len(self.ledger.snapshot()['holds']), budget.MAX_HOLDS)


class SoftLimitTests(Funded):
    """Above SOFT_RECORD nothing adds exposure; everything that settles or reduces it still commits."""
    def setUp(self):
        super().setUp()
        self.fund(balance=100 * M)
        pools = dict(g=funded_pool('org'))
        self.routes = [route('r', 'g', 'api_grant', 'acct', {'credit': M})]
        self.pools = pools
        for attempt in ('h1', 'h2', 'h3', 'h4'):
            self.assertEqual(self.admit(attempt, self.routes, pools)['status'], 'admitted')
        self.ledger.mark_dispatched('h2')
        for run in ('r1', 'r2', 'r3'):
            self.begin(run, M)
            self.ledger.dispatch(run, 'count', 1, NOW)
        self.inflate()

    def inflate(self):
        """Grow the record just past the soft limit with additional configured organizations."""
        with closing(sqlite3.connect(self.path)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            org = data['organizations']['org']
            filler = len(json.dumps({long('filler'): org}, separators=(',', ':')))
            count = (budget.SOFT_RECORD - len(json.dumps(data, separators=(',', ':')))) // filler
            for i in range(count + 50):  # a little past the soft limit
                data['organizations'][unique(i, long('filler'))] = copy.deepcopy(org)
            raw = json.dumps(data, separators=(',', ':'))
            db.execute('UPDATE ledger SET body=?', (raw,)); db.commit()
        self.assertGreater(len(raw), budget.SOFT_RECORD)
        self.assertLess(len(raw), budget.MAX_RECORD)

    def test_nothing_that_adds_exposure_commits(self):
        before = self.ledger.snapshot()
        result = self.admit('new', self.routes, self.pools)
        self.assertEqual((result['status'], result['reason_code']), ('pending', 'ledger_capacity_reached'))
        refused = dict(begin=lambda: self.begin('r9', M), top_up=lambda: self.ledger.top_up('r1', 1, 1, NOW),
                       configure=lambda: self.ledger.configure('new-org', policy(), 0),
                       enable=lambda: self.ledger.enable_organization(budget.PERSONAL, policy(), 0))
        for label, call in refused.items():
            with self.subTest(label), self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
                call()
        self.assertEqual(self.ledger.snapshot(), before)

    def test_settlement_always_commits(self):
        self.ledger.complete('r1', 'count', M // 2)
        self.ledger.uncertain('r2', 'count', incomplete=dict(reason='unknown', response_id='resp_x'))
        self.ledger.close_run('r1')
        self.ledger.reject_unsent('r3', 'count')
        self.ledger.close_attempt('h1', 'not_dispatched')
        self.ledger.close_attempt('h2', 'uncertain')
        self.ledger.mark_uncertain('h3')
        authorize(self.ledger, 'h4')
        self.ledger.convert_hold('h4', 'org', 'packet', M, 1, NOW, native('h4'))
        self.ledger.dispatch('h4', 'count', 1, NOW)
        self.ledger.complete('h4', 'count', M)
        self.ledger.observe('org', 90 * M, NOW + 1)
        self.ledger.disable_organization('org')
        data = self.ledger.snapshot()
        self.assertEqual(data['runs']['r2']['calls']['count']['state'], 'unresolved')
        self.assertEqual([data['holds'][h]['state'] for h in ('h1', 'h2', 'h3', 'h4')],
                         ['released', 'uncertain', 'uncertain', 'converted'])
        self.assertNotIn('dispatching', [c['state'] for r in data['runs'].values() for c in r['calls'].values()])


class IncompleteBoundTests(Funded):
    def test_an_oversized_incomplete_record_is_replaced_and_never_blocks_uncertainty(self):
        self.fund(balance=100 * M)
        self.begin('r', M)
        self.ledger.dispatch('r', 'count', 1, NOW)
        self.ledger.uncertain('r', 'count', incomplete=dict(reason='x' * (budget.MAX_INCOMPLETE_BYTES * 2)))
        call = self.ledger.snapshot()['runs']['r']['calls']['count']
        self.assertEqual((call['state'], call['incomplete']), ('unresolved', dict(omitted='too_large')))

if __name__ == '__main__':
    unittest.main()
