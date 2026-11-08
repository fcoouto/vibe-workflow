"""Money invariants for one claim model: holds and runs share one organization total (spec 6.2, 7.4, 8).

I1 per organization: B - protected - R >= 0, where R sums every run call and every open hold.
I2 pilot, task and cycle limits hold with holds included.
I3 every dollar hold names an existing organization and carries an integer micro-unit amount.
I4 conversion keeps reserved dollars and the organization unchanged.
I5 each open attempt takes exactly one concurrency place.
I6 a closed hold contributes 0; an uncertain hold contributes its full amount.
I7 the record stays under MAX_RECORD at maximum table sizes.
"""
import copy
import json
import math
import multiprocessing
import os
import random
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
                self.assertEqual(result['status'], 'pending')
                self.assertIn('funding_unverified', result['reason_codes'])
        self.assertEqual(self.ledger.snapshot()['holds'], {})

    def test_b1_unknown_organization_is_excluded(self):
        pools = dict(p=funded_pool('ghost'))
        result = self.admit('a', [route('r', 'p', 'api_grant', 'acct', {'credit': M})], pools)
        self.assertIn('funding_unverified', result['reason_codes'])

    def test_blocked_organization_cannot_hide_behind_an_unlabelled_pool(self):
        self.fund()
        with self.ledger.transaction() as data:
            data['organizations']['org']['blocked'] = True
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

    def test_one_route_demanding_two_windows_on_one_organization_sums_into_one_key(self):
        self.fund(balance=10 * M)
        pools = dict(g=dict(billing_class='api_grant', windows=[window('a', organization='org'),
                                                                window('b', organization='org')]))
        result = self.admit('a', [route('r', 'g', 'api_grant', 'acct', {'a': 4 * M, 'b': 4 * M})], pools)
        self.assertEqual(result['status'], 'pending')  # 2 x 4.8M > 9.8M
        ok = self.admit('b', [route('r', 'g', 'api_grant', 'acct', {'a': 3 * M, 'b': 3 * M})], pools)
        self.assertEqual(ok['reserved'], {'org:org': 7_200_000})

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
        self.ledger.close_attempt('a', 'complete')
        self.assertEqual(self.ledger.credit_headroom('org'), 9_800_000)
        self.assertEqual(budget.Ledger.open_holds(self.ledger.snapshot()), {})

    def test_gateway_needs_its_own_organization(self):
        self.fund('grant-org', balance=10 * M)
        self.fund('gw-org', balance=1 * M)
        gw = route('gw', 'gw', 'gateway', 'gw', {'credit': M})
        pools = dict(gw=funded_pool('gw-org', kind='gateway'))
        self.assertEqual(self.admit('a', [gw], pools)['reserved'], {'org:gw-org': 1_200_000})
        self.assertEqual(self.ledger.headroom('grant-org'), 9_800_000)  # a gateway never draws on the grant


class ConversionTests(Funded):
    def setUp(self):
        super().setUp()
        self.fund('org', balance=10 * M)
        self.fund('org2', balance=10 * M)
        pools = dict(g=funded_pool('org'), s=sub_pool())
        routes = [route('r', 'g', 'api_grant', 'acct-A', {'credit': M})]
        self.assertEqual(self.admit('a', routes, pools)['reserved'], {'org:org': 1_200_000})

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

    def test_dispatched_hold_cannot_convert(self):
        self.ledger.mark_dispatched('a')
        with self.assertRaisesRegex(budget.BudgetError, 'convert_forbidden'):
            self.convert(M)

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
        self.ledger.configure('o1', policy(allowance_bp=9800, ceiling='custom', amount=40 * M, task_limit=15 * M,
                                           pilot_limit=50 * M), 0)
        self.ledger.configure('o2', policy(allowance_bp=10000), 0)
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
        for name in ORGS:
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
        op = self.rng.choice(('admit', 'admit', 'admit', 'begin', 'begin', 'top_up', 'convert', 'convert',
                              'dispatched', 'uncertain', 'close', 'run_dispatch', 'complete', 'observe'))
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

    def op_convert(self, before):
        holds = sorted(i for i, h in before['holds'].items() if not h['closed'] and h['amount'])
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

    def op_observe(self, before):
        """External spend lowers B, but a well-behaved provider never reports below what is reserved."""
        name = self.rng.choice(ORGS)
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
        admitted = converted = 0
        for seed in range(ITERATIONS):
            run = Property(self, seed)
            try:
                for index in range(STEPS):
                    run.step(index)
                state = run.ledger.snapshot()
                admitted += len(state['admissions'])
                converted += sum(1 for attempt in state['runs'] if attempt in state['holds'])
            finally:
                run.close()
        self.assertGreater(admitted, ITERATIONS)  # the sequences exercise admission, not only refusals
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
class CapacityTests(Funded):
    def test_maximum_table_sizes_stay_under_the_record_limit(self):
        long = lambda tag: (tag + 'x' * 256)[:256]  # noqa: E731
        organization = long('org')
        self.ledger.configure(organization, policy(cycle=long('cycle')), 0)
        self.ledger.observe(organization, 10**12, NOW, issued=10**12)
        pools = dict(g=funded_pool(organization, wid=long('w')), s=dict(windows=[dict(
            id=long('5h'), limit=10**9, used=0, length=18000, reset_at=None, rolling=True, fresh=True)]))
        routes = [route(long('r'), 'g', 'api_grant', long('acct'), {long('w'): M}),
                  route(long('s'), 's', 'included_subscription', long('b'), {long('5h'): 1})]
        for item in routes:
            item.update(strategy_id=long('sid'), model=long('m'), harness=long('h'), provider=long('p'))
            if 'credential' in item:
                item['credential']['ref'] = long('ref')
        obs = dict(observations(routes, pools), snapshot_id=long('snap'))
        rank = dict(strategy_ids=[long('sid')], reason_codes=[long('code')] * 16, table_version=long('t'),
                    profile_digest=long('d'))
        hold_id, run_id = long('attempt'), long('run')
        admitted = routing.admit(assignment(hold_id, long('task')), rank, settings(), obs, ledger=self.ledger)
        self.assertEqual(admitted['status'], 'admitted', admitted)
        self.ledger.begin(organization, long('task'), run_id, long('digest'), M, 1, NOW,
                          dict(account=long('na'), task=long('task'), windows=[]))
        self.ledger.dispatch(run_id, 'count', 1, NOW)
        self.ledger.complete(run_id, 'count', 1)
        self.ledger.reserve_generation(run_id, long('digest2'), M, 1, NOW)
        data = self.ledger.snapshot()
        record, hold, run = data['admissions'][0], data['holds'][hold_id], data['runs'][run_id]

        def unique(index, value):
            return ('%05d' % index + value)[:256]
        data['admissions'] = [dict(record, attempt_id=unique(i, hold_id)) for i in range(budget.MAX_ADMISSIONS)]
        data['holds'] = {unique(i, hold_id): copy.deepcopy(hold) for i in range(budget.MAX_HOLDS)}
        data['seen_attempts'] = [unique(i, hold_id) for i in range(budget.MAX_SEEN)]
        data['runs'] = {unique(i, run_id): copy.deepcopy(run) for i in range(budget.MAX_RUNS)}
        native_key = run['native']
        data['native'] = {unique(i, native_key): dict(data['native'][native_key]) for i in range(budget.MAX_RUNS)}
        raw = json.dumps(data, allow_nan=False, separators=(',', ':'))
        self.assertLessEqual(len(raw), budget.MAX_RECORD)


if __name__ == '__main__':
    unittest.main()
