import copy
import tempfile
import threading
import unittest
from pathlib import Path

from runtime import routing, routing_settings, usage_budget as budget

NOW = 1_000_000
M = 1_000_000
SCOPE = 'scope1'
READY = dict(ref='keychain:opaque-ref', scope=SCOPE, state='ready')


def sub_pool(used=100, extra=None, **window):
    """Rolling windows keep the arithmetic exact: projected utilization is (used + reserve) / limit."""
    windows = [dict(id='5h', limit=1000, used=used, length=18000, reset_at=None, rolling=True, **window)]
    return dict(windows=windows + (extra or []))


def api_pool(limit=30 * M, expires=NOW + 86400, remaining=None, kind='api_grant', grant=True):
    pool = dict(billing_class=kind, windows=[dict(id='credit', limit=limit, used=0, length=None, reset_at=None)])
    if grant:
        pool['grant'] = dict(remaining=remaining or limit, expected_spend=0, expires_at=expires)
    return pool


def sub_route(rid, provider='anthropic', pool='sub', account=None, strategy='s-sub', demand=None, **extra):
    route = dict(route=rid, strategy_id=strategy, provider=provider, model='m', effort='high', harness='h',
                 account=account or provider + ':acct', billing_class='included_subscription', pool=pool,
                 demand={'5h': 10} if demand is None else demand)
    return dict(route, **extra)


def api_route(rid, provider='anthropic', pool='api', strategy='s-api', demand=None, kind='api_grant', **extra):
    route = dict(route=rid, strategy_id=strategy, provider=provider, model='m', effort='high', harness='api',
                 account=provider + ':org', billing_class=kind, pool=pool, credential=dict(READY),
                 funding_verified=True, demand={'credit': 5 * M} if demand is None else demand)
    return dict(route, **extra)


def ranking(*ids):
    return dict(strategy_ids=list(ids), reason_codes=['ranking_exact'] if ids else ['no_recommendation'],
                table_version='t1', profile_digest='d1')


def settings(**updates):
    base = dict(api_usage_enabled=True, caps=dict(total=40, per_account=40))
    base.update(updates)
    return dict(routing_settings.resolve(base), revision=3)


def assignment(attempt, role='implementer', band='medium', **extra):
    return dict(attempt_id=attempt, task=attempt, role=role, complexity_band=band, scope=SCOPE, **extra)


def observations(routes, pools, **extra):
    return dict(now=NOW, routes=routes, pools=pools, **extra)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'ledger.sqlite3'
        self.identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, self.identity)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def admit(self, attempt, ids, routes, pools, cfg=None, **kwargs):
        extra = {key: kwargs.pop(key) for key in ('epoch_at',) if key in kwargs}
        return routing.admit(assignment(attempt, **kwargs), ranking(*ids), cfg or settings(),
                             observations(routes, pools, **extra), ledger=self.ledger)


class EligibilityTests(Base):
    def both(self, **api):
        routes = [sub_route('sub'), api_route('api', **api)]
        return routes, dict(sub=sub_pool(), api=api_pool())

    def test_api_usage_off_excludes_direct_api_and_its_expiry_pressure(self):
        routes, pools = self.both()
        pools['api'] = api_pool(expires=NOW + 3600)
        on = self.admit('on', ['s-sub', 's-api'], routes, pools)
        off = self.admit('off', ['s-sub', 's-api'], routes, pools, cfg=settings(api_usage_enabled=False))
        self.assertGreater(on['shares']['api'], 0.9)
        self.assertEqual((off['class'], off['route'], off['shares']['api'], off['pressure']['e']), ('subscription', 'sub', 0.0, None))

    def test_smart_routing_on_does_not_enable_api_usage(self):
        routes, pools = self.both()
        cfg = routing_settings.resolve({})
        self.assertTrue(cfg['smart_routing'] and not cfg['api_usage_enabled'])
        result = self.admit('a', ['s-api'], routes, pools, cfg=dict(cfg, revision=1))
        self.assertEqual(result['status'], 'pending')

    def test_unavailable_credentials_exclude_the_route_only(self):
        for label, change in dict(missing={'credential': None}, revoked={'credential': dict(READY, state='revoked')},
                                  locked={'credential': dict(READY, state='locked')},
                                  wrong_scope={'credential': dict(READY, scope='other')},
                                  unverified={'funding_verified': False}, forbidden={'permitted': False}).items():
            with self.subTest(label):
                routes, pools = self.both()
                routes[1].update(change)
                result = self.admit(label, ['s-sub', 's-api'], routes, pools)
                self.assertEqual((result['status'], result['route']), ('admitted', 'sub'))
                self.assertEqual(self.admit(label + '2', ['s-api'], routes, pools)['status'], 'pending')

    def test_openrouter_uses_only_its_own_gate(self):
        gateway = api_route('gw', kind='gateway', pool='gw', strategy='s-gw', integration_enabled=True)
        pools = dict(gw=api_pool(kind='gateway', grant=False))
        off = settings(api_usage_enabled=False)
        self.assertEqual(self.admit('a', ['s-gw'], [gateway], pools, cfg=off)['route'], 'gw')
        gateway['integration_enabled'] = False
        self.assertEqual(self.admit('b', ['s-gw'], [gateway], pools)['status'], 'pending')

    def test_a_direct_grant_never_funds_a_gateway(self):
        gateway = api_route('gw', kind='gateway', pool='api', strategy='s-gw', integration_enabled=True)
        self.assertEqual(self.admit('a', ['s-gw'], [gateway], dict(api=api_pool()))['status'], 'pending')

    def test_gateway_pools_do_not_create_expiry_pressure(self):
        routes = [sub_route('sub'), api_route('gw', kind='gateway', pool='gw', strategy='s-gw', integration_enabled=True)]
        pools = dict(sub=sub_pool(), gw=api_pool(kind='gateway', expires=NOW + 3600))
        result = self.admit('a', ['s-sub', 's-gw'], routes, pools)
        self.assertEqual((result['class'], result['pressure']['e']), ('subscription', 0.0))

    def test_purchased_usage_is_never_subscription_headroom(self):
        purchased = sub_route('buy', strategy='s-buy', billing_class='purchased_usage')
        result = self.admit('a', ['s-buy'], [purchased], dict(sub=sub_pool(used=0)))
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.admit('b', ['s-buy'], [dict(purchased, billing_class='ineligible')],
                                    dict(sub=sub_pool(used=0)))['status'], 'pending')
        mixed = self.admit('c', ['s-buy', 's-sub'], [purchased, sub_route('sub')], dict(sub=sub_pool()))
        self.assertEqual(mixed['route'], 'sub')

    def test_providers_get_identical_kinds_and_decisions(self):
        outcomes = []
        for provider in ('anthropic', 'openai'):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'private' / 'l.sqlite3'
                identity = budget.initialize(path)
                with budget.Ledger(path, identity) as ledger:
                    routes = [sub_route('sub', provider), api_route('api', provider)]
                    pools = dict(sub=sub_pool(used=740), api=api_pool())
                    run = []
                    for i in range(4):
                        for cfg in (settings(), settings(api_usage_enabled=False)):
                            cfg = dict(cfg, revision=cfg['revision'] + i)
                            result = routing.admit(assignment('%d%s' % (i, cfg['api_usage_enabled'])),
                                                   ranking('s-sub', 's-api'), cfg, observations(routes, pools),
                                                   ledger=ledger)
                            run.append((result['class'], result['shares']['api'], result['billing_class']))
                    outcomes.append(run)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual({kind for _, _, kind in outcomes[0]}, {'api_grant', 'included_subscription'})

    def test_subscription_stays_available_when_api_usage_is_off(self):
        for provider in ('anthropic', 'openai'):
            routes = [sub_route(provider + '-sub', provider), api_route(provider + '-api', provider)]
            result = self.admit(provider, ['s-sub', 's-api'], routes, dict(sub=sub_pool(), api=api_pool()),
                                cfg=settings(api_usage_enabled=False))
            self.assertEqual(result['route'], provider + '-sub')

    def test_secret_values_never_reach_the_record_or_ledger(self):
        routes, pools = self.both()
        routes[1]['credential']['secret'] = 'sk-live-should-never-persist'
        routes[1]['secret'] = 'sk-live-should-never-persist'
        self.admit('a', ['s-api'], routes, pools)
        self.assertNotIn('sk-live', str(self.ledger.snapshot()))
        self.assertEqual(self.ledger.snapshot()['admissions'][0]['credential_ref'], 'keychain:opaque-ref')


class ModeAndFallbackTests(Base):
    CHOICE = dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')

    def test_off_mode_admits_first_feasible_worker_choice(self):
        missing = dict(self.CHOICE, model='absent')
        cfg = settings(smart_routing=False, worker_choices=dict(implementer=[missing, self.CHOICE]))
        result = self.admit('a', ['s-api'], [sub_route('sub')], dict(sub=sub_pool()), cfg=cfg)
        self.assertEqual((result['route'], result['mode'], result['class']), ('sub', 'worker_choice', 'subscription'))

    def test_off_mode_without_a_feasible_choice_is_pending(self):
        for choices in ([], [dict(self.CHOICE, model='absent')]):
            cfg = settings(smart_routing=False, worker_choices=dict(implementer=choices))
            result = self.admit('a', ['s-sub'], [sub_route('sub')], dict(sub=sub_pool()), cfg=cfg)
            self.assertEqual((result['status'], result['reason_code']), ('pending', 'worker_choice_unavailable'))
        self.assertEqual(self.ledger.snapshot()['admissions'], [])

    def test_off_mode_api_choice_needs_api_usage(self):
        entry = dict(self.CHOICE, harness='api', funding='api')
        for enabled, expected in ((True, 'admitted'), (False, 'pending')):
            cfg = settings(smart_routing=False, api_usage_enabled=enabled, worker_choices=dict(implementer=[entry]))
            result = self.admit('a%s' % enabled, [], [api_route('api')], dict(api=api_pool()), cfg=cfg)
            self.assertEqual(result['status'], expected)

    def test_no_recommendation_falls_back_instead_of_blocking(self):
        cfg = settings(worker_choices=dict(implementer=[self.CHOICE]))
        result = self.admit('a', [], [sub_route('sub')], dict(sub=sub_pool()), cfg=cfg)
        self.assertEqual((result['status'], result['mode'], result['route']), ('admitted', 'worker_choice', 'sub'))
        none = self.admit('b', [], [sub_route('sub')], dict(sub=sub_pool()))
        self.assertEqual(none['reason_code'], 'worker_choice_unavailable')

    def test_explicit_choice_overrides_ranking_and_never_substitutes(self):
        routes = [sub_route('sub'), sub_route('other', strategy='s-other', pool='sub2', account='b')]
        routes[1]['model'] = 'x'
        pools = dict(sub=sub_pool(), sub2=sub_pool())
        explicit = dict(self.CHOICE, model='x')
        result = self.admit('a', ['s-sub'], routes, pools, choice=explicit)
        self.assertEqual(result['route'], 'other')
        bad = self.admit('b', ['s-sub'], routes, pools, choice=dict(self.CHOICE, model='nope'))
        self.assertEqual(bad['reason_code'], 'explicit_choice_unavailable')

    def test_empty_ranking_still_obeys_headroom(self):
        cfg = settings(worker_choices=dict(implementer=[self.CHOICE]))
        result = self.admit('a', [], [sub_route('sub', demand={'5h': 2000})], dict(sub=sub_pool()), cfg=cfg)
        self.assertEqual(result['reason_code'], 'worker_choice_unavailable')


class AllocationTests(Base):
    def test_class_choice_follows_persisted_records_and_survives_reopen(self):
        routes = [sub_route('sub', demand={'5h': 0}), api_route('api', demand={'credit': 1})]
        pools = dict(sub=sub_pool(used=740), api=api_pool(grant=False))  # u = 0.74: shares 0.45 API / 0.55
        classes = []
        for i in range(20):
            if i == 10:
                self.ledger.close()
                self.ledger = budget.Ledger(self.path, self.identity)
            classes.append(self.admit('a%d' % i, ['s-sub', 's-api'], routes, pools)['class'])
        self.assertEqual((classes.count('api'), classes.count('subscription')), (9, 11))
        records = self.ledger.snapshot()['admissions']
        self.assertEqual(len(records), 20)
        self.assertAlmostEqual(records[0]['shares']['api'], 0.45)
        self.assertEqual(records[0]['bucket'], ['implementer', 'medium'])

    def test_new_settings_revision_starts_a_new_epoch(self):
        routes = [sub_route('sub', demand={'5h': 0}), api_route('api', demand={'credit': 1})]
        pools = dict(sub=sub_pool(used=750), api=api_pool(grant=False))  # 50/50
        for i in range(3):
            self.admit('a%d' % i, ['s-sub', 's-api'], routes, pools)
        same = routing.admit(assignment('y'), ranking('s-sub', 's-api'), settings(), observations(routes, pools),
                             ledger=self.ledger)
        later = routing.admit(assignment('z'), ranking('s-sub', 's-api'), dict(settings(), revision=4),
                              observations(routes, pools), ledger=self.ledger)
        self.assertEqual(same['class'], 'subscription')  # four admissions so far: API, sub, API, sub
        self.assertEqual(later['class'], 'api')  # a fresh epoch: the tie favors API

    def test_hard_overrides_and_pending(self):
        routes = [sub_route('sub'), api_route('api')]
        stale = dict(sub=sub_pool(fresh=False), api=api_pool())
        self.assertEqual(self.admit('a', ['s-sub', 's-api'], routes, stale)['class'], 'api')
        no_api = self.admit('b', ['s-sub', 's-api'], routes, dict(sub=sub_pool(used=990), api=api_pool()),
                            cfg=settings(api_usage_enabled=False))
        self.assertEqual(no_api['status'], 'pending')
        both = self.admit('c', ['s-sub', 's-api'], routes, dict(sub=sub_pool(fresh=False), api=api_pool(limit=1)))
        self.assertEqual((both['status'], both['reason_code']), ('pending', 'no_feasible_route'))
        self.assertEqual(len(self.ledger.snapshot()['admissions']), 1)

    def test_unknown_utilization_is_conservative_not_healthy(self):
        extra = [dict(id='wk', limit=1000, used=None, length=604800, reset_at=NOW + 1000)]
        routes = [sub_route('sub'), api_route('api')]
        result = self.admit('a', ['s-sub', 's-api'], routes, dict(sub=sub_pool(extra=extra), api=api_pool()))
        self.assertIn('utilization_unknown', result['reason_codes'])
        self.assertEqual((result['class'], result['shares']['api']), ('api', 1.0))

    def test_expiry_pressure_prefers_the_expiring_grant_route(self):
        routes = [sub_route('sub'), api_route('api')]
        pools = dict(sub=sub_pool(), api=api_pool(expires=NOW + 3 * 3600))
        result = self.admit('a', ['s-sub', 's-api'], routes, pools)
        self.assertGreater(result['pressure']['e'], 0.9)
        self.assertEqual(result['class'], 'api')

    def test_zero_demand_candidates_are_finite(self):
        routes = [sub_route('sub', demand={})]
        self.assertEqual(self.admit('a', ['s-sub'], routes, dict(sub=sub_pool()))['status'], 'admitted')

    def test_pools_spread_by_pressure_within_a_class(self):
        def pool(limit, used):
            return dict(windows=[dict(id='5h', limit=limit, used=used, length=18000, reset_at=NOW + 9000)])
        routes = [sub_route('a', pool='pa', strategy='s1', account='x'), sub_route('b', pool='pb', strategy='s2', account='y')]
        pools = dict(pa=pool(100, 0), pb=pool(1000, 100))  # 100 versus 900 units of headroom at equal cost
        chosen = [self.admit('t%d' % i, ['s1', 's2'], routes, pools)['route'] for i in range(3)]
        self.assertEqual(chosen, ['b', 'b', 'b'])


class ReservationTests(Base):
    def test_spec_example_three_padded_tasks_run_concurrently(self):
        routes = lambda cost: [api_route('api', demand={'credit': cost})]
        pools = dict(api=api_pool(grant=False))
        for task, cost, expected in (('t5', 5 * M, 6 * M), ('t10', 10 * M, 12 * M), ('t8', 8 * M, 9_600_000)):
            result = self.admit(task, ['s-api'], routes(cost), pools)
            self.assertEqual((result['status'], result['reserved']['api/credit']), ('admitted', expected))
        fourth = self.admit('t3', ['s-api'], routes(3 * M), pools)
        self.assertEqual((fourth['status'], fourth['reason_code']), ('pending', 'no_feasible_route'))
        self.ledger.close_attempt('t5')
        self.assertEqual(self.admit('t3', ['s-api'], routes(3 * M), pools)['status'], 'admitted')
        self.assertEqual(sum(self.ledger.held(self.ledger.snapshot()).values()), 12 * M + 9_600_000 + 3_600_000)

    def test_duplicate_attempt_neither_reserves_nor_records_twice(self):
        routes, pools = [api_route('api')], dict(api=api_pool(grant=False))
        first = self.admit('a', ['s-api'], routes, pools)
        before = copy.deepcopy(self.ledger.snapshot())
        again = self.admit('a', ['s-api'], routes, pools)
        self.assertTrue(again['duplicate'])
        self.assertEqual({k: v for k, v in again.items() if k not in ('duplicate', 'status')},
                         {k: v for k, v in first.items() if k != 'status'})
        self.assertEqual(self.ledger.snapshot(), before)

    def test_rejected_decisions_leave_no_hold_or_record(self):
        before = copy.deepcopy(self.ledger.snapshot())
        result = self.admit('a', ['s-api'], [api_route('api', demand={'credit': 40 * M})], dict(api=api_pool(grant=False)))
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.ledger.snapshot(), before)

    def test_instances_racing_for_the_last_headroom_admit_what_fits(self):
        routes, pools = [api_route('api', demand={'credit': 7 * M})], dict(api=api_pool(limit=10 * M, grant=False))
        results = []

        def race(name):
            with budget.Ledger(self.path, self.identity) as ledger:
                results.append(routing.admit(assignment(name), ranking('s-api'), settings(),
                                             observations(routes, pools), ledger=ledger)['status'])

        threads = [threading.Thread(target=race, args=(name,)) for name in ('a', 'b')]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertEqual(sorted(results), ['admitted', 'pending'])
        self.assertEqual(len(self.ledger.snapshot()['holds']), 1)

    def test_organization_headroom_caps_the_pool(self):
        policy = dict(cycle='c', start=0, end=2_000_000, expiry=2_000_000, stop_at=1_900_000, ceiling='none',
                      amount=None, grant_amount=0, pilot_limit=None, task_limit=None)
        self.ledger.configure('org', policy, 0)
        self.ledger.observe('org', 10 * M, 100, issued=10 * M)  # 98% leaves 9.8M
        pool = api_pool(grant=False)
        pool['windows'][0].update(organization='org')
        fits = self.admit('a', ['s-api'], [api_route('api', demand={'credit': 8 * M})], dict(api=pool))  # 9.6M
        self.assertEqual(fits['status'], 'admitted')
        self.assertEqual(self.admit('b', ['s-api'], [api_route('api', demand={'credit': 1 * M})], dict(api=pool))['status'], 'pending')

    def test_subscription_windows_hold_atomically_across_pools(self):
        pools = dict(sub=sub_pool(used=0, extra=[dict(id='wk', limit=20, used=0, length=604800, reset_at=None, rolling=True)]))
        routes = [sub_route('sub', demand={'5h': 10, 'wk': 10})]
        first = self.admit('a', ['s-sub'], routes, pools)
        self.assertEqual(first['reserved'], {'sub/5h': 12, 'sub/wk': 12})
        second = self.admit('b', ['s-sub'], routes, pools)
        self.assertEqual(second['status'], 'pending')  # the weekly window cannot hold a second 12
        self.assertEqual(list(self.ledger.snapshot()['holds']), ['a'])


class ConcurrencyTests(Base):
    def test_third_attempt_on_one_account_waits_and_closing_frees_a_place(self):
        cfg = dict(settings(), caps=dict(total=4, per_account=2))
        routes = [sub_route('a', strategy='s-a', pool='pa', account='A'), sub_route('b', strategy='s-b', pool='pb', account='B')]
        pools = dict(pa=sub_pool(), pb=sub_pool())
        self.assertEqual(self.admit('1', ['s-a'], routes, pools, cfg=cfg)['status'], 'admitted')
        self.assertEqual(self.admit('2', ['s-a'], routes, pools, cfg=cfg)['status'], 'admitted')
        third = self.admit('3', ['s-a'], routes, pools, cfg=cfg)
        self.assertEqual((third['status'], third['reason_code']), ('pending', 'concurrency_cap'))
        self.assertEqual(self.admit('4', ['s-a', 's-b'], routes, pools, cfg=cfg)['route'], 'b')
        self.ledger.close_attempt('1')
        self.assertEqual(self.admit('3', ['s-a'], routes, pools, cfg=cfg)['status'], 'admitted')

    def test_global_cap_blocks_with_reason(self):
        cfg = dict(settings(), caps=dict(total=1, per_account=1))
        routes, pools = [sub_route('a')], dict(sub=sub_pool())
        self.admit('1', ['s-sub'], routes, pools, cfg=cfg)
        self.assertEqual(self.admit('2', ['s-sub'], routes, pools, cfg=cfg)['reason_code'], 'concurrency_cap')

    def test_open_runs_count_toward_the_same_caps(self):
        self.assertEqual(budget.Ledger.open_accounts(self.ledger.snapshot()), [])


class InputTests(Base):
    def test_invalid_assignment_is_pending_not_an_exception(self):
        result = routing.admit({}, ranking(), settings(), observations([], {}), ledger=self.ledger)
        self.assertEqual(result['reason_code'], 'invalid_assignment')


if __name__ == '__main__':
    unittest.main()
