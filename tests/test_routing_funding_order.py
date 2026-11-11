"""F5.2: one subscription shortlist of at most ten ranked ids, cut after the static checks and before any window.
F5.3: a ranked decision funds only subscription routes (`routing_funding.choose`); paid routes never compete.

`routing_funding.subscription_shortlist` is pure. A caller's own snapshot (no `shortlist`) is cut in admission after the
same non-window checks; a supplied `shortlist` may only narrow the ranking, never widen it or name a missing route.
"""
import unittest

from runtime import routing, routing_allocator as allocator, routing_funding
from tests.fixture_routing_funding import M, NOW, Funded, assignment, funded_pool, observations, ranking, route, \
    settings, sub_pool

SUB = 'included_subscription'


def ids(*numbers):
    return ['s%d' % n for n in numbers]


def shortlist_route(n, kind=SUB):
    return dict(strategy_id='s%d' % n, billing_class=kind, route='r%d' % n)


def sub(n, **extra):
    return dict(route('r%d' % n, 'p%d' % n, SUB, 'a%d' % n, {'5h': 10}, strategy='s%d' % n), **extra)


def full():
    return sub_pool(used=995)  # 5 of 1000 left: a demand of 10 does not fit


class SubscriptionShortlistTests(unittest.TestCase):
    def test_subscription_shortlist_takes_first_ten_eligible_ids(self):
        routes = [shortlist_route(n, 'api_grant') for n in (1, 2, 3)] + [shortlist_route(n) for n in range(4, 16)]
        self.assertEqual(routing_funding.SHORTLIST_MAX, 10)
        self.assertEqual(routing_funding.subscription_shortlist(ids(*range(1, 16)), routes), ids(*range(4, 14)))

    def test_a_not_entitled_route_takes_no_place(self):
        routes = [shortlist_route(n) for n in range(1, 13) if n != 2]  # the static cut already left s2 out
        self.assertEqual(routing_funding.subscription_shortlist(ids(*range(1, 13)), routes),
                         ids(1, *range(3, 12)))

    def test_shortlist_is_never_padded(self):
        routes = [shortlist_route(2), shortlist_route(4), shortlist_route(5, 'gateway'), shortlist_route(9)]
        self.assertEqual(routing_funding.subscription_shortlist(ids(1, 2, 2, 3, 4, 5, 9), routes), ids(2, 4, 9))
        self.assertEqual(routing_funding.subscription_shortlist([], routes), [])
        self.assertEqual(routing_funding.subscription_shortlist(ids(1, 2), []), [])


class CallerSnapshotTests(Funded, unittest.TestCase):
    def decide(self, routes, pools, ranked, shortlist=None, attempt='a1'):
        snapshot = observations(routes, pools)
        if shortlist is not None:
            snapshot['shortlist'] = shortlist
        return routing.admit(assignment(attempt), ranking(*ranked), settings(), snapshot, ledger=self.ledger)

    def test_caller_snapshot_is_cut_before_window_feasibility(self):
        routes = [sub(n) for n in range(1, 13)]
        pools = {'p%d' % n: full() if n <= 10 else sub_pool() for n in range(1, 13)}
        late = self.decide(routes, pools, ids(*range(1, 13)))
        self.assertEqual(late['status'], 'pending', late)  # s11 and s12 fit, but are past the ten-id cut
        self.assertEqual(self.ledger.snapshot()['admissions'], [])
        early = self.decide(routes, pools, ids(11, *range(1, 11), 12), attempt='a2')
        self.assertEqual((early['status'], early['route']), ('admitted', 'r11'), early)

    def test_a_statically_ineligible_caller_route_takes_no_place(self):
        routes = [sub(1, permitted=False)] + [sub(n) for n in range(2, 12)]
        pools = {'p%d' % n: full() if n <= 10 else sub_pool() for n in range(1, 12)}
        found = self.decide(routes, pools, ids(*range(1, 12)))
        self.assertEqual((found['status'], found['route']), ('admitted', 'r11'), found)

    def test_an_unobserved_route_keeps_its_place(self):
        routes = [sub(n) for n in range(1, 12)]
        pools = {'p%d' % n: full() if n <= 10 else sub_pool() for n in range(2, 12)}  # p1 never observed
        found = self.decide(routes, pools, ids(*range(1, 12)))
        self.assertEqual(found['status'], 'pending', found)  # s1 kept its place, so s11 is past the cut

    def test_paid_routes_take_no_place_and_stay_candidates(self):
        paid = route('api', 'org-pool', 'api_grant', 'org-account', {'credit': 1}, strategy='s0')
        routes = [paid] + [sub(n) for n in range(1, 11)]
        pools = {'p%d' % n: full() if n <= 9 else sub_pool() for n in range(1, 11)}
        found = self.decide(routes, pools, ids(0, *range(1, 11)))  # s0 holding a place would cut s10
        self.assertEqual((found['status'], found['route']), ('admitted', 'r10'), found)
        evaluated = routing_funding.ranked_routes(observations(routes, pools), ranking(*ids(0, *range(1, 11))))
        self.assertNotIn(paid, evaluated)  # F5.3: a ranked paid route is left out before `_candidate`

    def test_invalid_shortlist_cannot_expand_the_ranking(self):
        routes = [sub(1), sub(2), sub(3)]
        pools = {'p1': sub_pool(), 'p2': sub_pool(), 'p3': sub_pool()}
        ranked = ids(1, 2)
        for name, value in (('unranked', ids(3)), ('reordered', ids(2, 1)), ('repeated', ids(1, 1)),
                            ('too many', ids(*range(1, 12))), ('not text', [1]), ('not a list', 's1'),
                            ('empty id', [''])):
            with self.subTest(name):
                answer = self.decide(routes, pools, ranked, shortlist=value)
                self.assertEqual((answer['status'], answer['reason_code']), ('pending', 'invalid_observation'))
        self.assertEqual(self.ledger.snapshot()['admissions'], [])
        narrowed = self.decide(routes, pools, ranked, shortlist=ids(2))
        self.assertEqual((narrowed['status'], narrowed['route']), ('admitted', 'r2'), narrowed)

    def test_a_shortlisted_id_needs_its_own_eligible_route(self):
        pools = {'p1': sub_pool(), 'p2': sub_pool()}
        missing = self.decide([sub(1)], pools, ids(1, 2), shortlist=ids(2))
        self.assertEqual(missing['status'], 'pending', missing)  # never reconstructed, never s1 instead
        refused = self.decide([sub(1), sub(2, permitted=False)], pools, ids(1, 2), shortlist=ids(2), attempt='a2')
        self.assertEqual(refused['status'], 'pending', refused)  # the shortlist never widens eligibility
        self.assertEqual(self.ledger.snapshot()['admissions'], [])

    def test_the_observation_validator_owns_the_shortlist_shape(self):
        base = observations([sub(1)], {'p1': sub_pool()})
        self.assertTrue(routing._valid_observations(base))  # optional
        self.assertTrue(routing._valid_observations(dict(base, shortlist=ids(9, 1))))  # shape only, no ranking
        for value in ('s1', None, [1], [''], ids(1, 1), ids(*range(11)), ['x' * 257], {'s1': 1}):
            with self.subTest(value=value):
                self.assertFalse(routing._valid_observations(dict(base, shortlist=value)))

    def test_a_choice_admission_ignores_a_well_shaped_shortlist(self):
        routes = [sub(1), sub(2)]
        pools = {'p1': sub_pool(), 'p2': sub_pool()}
        choice = dict({key: routes[1][key] for key in routing.ROUTE_IDENTITY}, funding='subscription')
        snapshot = dict(observations(routes, pools), shortlist=ids(1))  # names another id, unranked here
        explicit = routing.admit(dict(assignment('a1'), choice=choice), ranking(), settings(), snapshot,
                                 ledger=self.ledger)
        self.assertEqual((explicit['status'], explicit['route']), ('admitted', 'r2'), explicit)
        chosen = settings(worker_choices={'software-engineer': [choice]})
        configured = routing.admit(assignment('a2'), ranking(), chosen, snapshot, ledger=self.ledger)
        self.assertEqual((configured['status'], configured['route'], configured['mode']),
                         ('admitted', 'r2', 'worker_choice'), configured)
        malformed = routing.admit(dict(assignment('a3'), choice=choice), ranking(), settings(),
                                  dict(snapshot, shortlist='s1'), ledger=self.ledger)
        self.assertEqual((malformed['status'], malformed['reason_code']), ('pending', 'invalid_observation'))

    def test_choice_modes_ignore_the_ranking_cut(self):
        routes = [sub(n) for n in range(1, 13)]
        pools = {'p%d' % n: full() if n <= 11 else sub_pool() for n in range(1, 13)}
        choice = {key: routes[11][key] for key in routing.ROUTE_IDENTITY}
        snapshot = observations(routes, pools)
        found = routing.admit(dict(assignment('a1'), choice=dict(choice, funding='subscription')), ranking(),
                              settings(), snapshot, ledger=self.ledger)
        self.assertEqual((found['status'], found['route']), ('admitted', 'r12'), found)


def window(wid, limit=1000, used=0, reset_at=None, fresh=True):
    return dict(id=wid, limit=limit, used=used, length=18000, reset_at=reset_at, rolling=reset_at is None, fresh=fresh)


def paid_routes():
    """Feasible synthetic paid routes: a funded grant and a funded gateway, each with its own organization."""
    return [route('api', 'api', 'api_grant', 'api-acct', {'credit': M}, strategy='s-api'),
            route('gw', 'gw', 'gateway', 'gw-acct', {'credit': M}, strategy='s-gw')], \
        dict(api=funded_pool('org'), gw=funded_pool('gw-org', kind='gateway'))


def candidate(name, pool, headroom, cost, resets_at=None, cls='subscription'):
    return dict(route=name, pool=pool, cls=cls,
                constraints=[dict(pool=pool, window='5h', headroom=headroom, cost=cost, resets_at=resets_at)])


class SubscriptionFundingTests(Funded, unittest.TestCase):
    """F5.3: the funding order. Subscription capacity alone decides a ranked admission; with none that fits it waits."""

    def setUp(self):
        super().setUp()
        self.fund('org')
        self.fund('gw-org')

    def decide(self, routes, pools, ranked=None, attempt='a1', **cfg):
        ranked = ranked or [item['strategy_id'] for item in routes]
        return routing.admit(assignment(attempt), ranking(*ranked), settings(**cfg), observations(routes, pools),
                             ledger=self.ledger)

    def choose(self, candidates):
        return routing_funding.choose(candidates, dict(api=0.0, subscription=1.0), [], ['implementation', 'medium'], 0,
                                      ['subscription'], NOW, 900)

    def assert_waits(self, answer):
        self.assertEqual(answer['status'], 'pending', answer)
        data = self.ledger.snapshot()
        self.assertEqual((data['holds'], data['admissions']), ({}, []))  # no dollar hold, no record

    def test_subscription_allocation_preserves_window_pressure(self):
        candidates = [candidate('tight', 'p1', 100, 10, NOW + 3600), candidate('roomy', 'p2', 1000, 10, NOW + 3600),
                      candidate('soon', 'p3', 200, 10, NOW + 600)]  # its short reset counts as the 900 s minimum
        expected = allocator.choose_route(candidates, NOW, 900)
        self.assertEqual(self.choose(candidates), (expected, 'subscription'))
        self.assertEqual(expected, 'roomy')  # the highest fundable work per binding horizon
        costs = [candidate('big', 'p1', 1000, 400, NOW + 3600), candidate('small', 'p1', 1000, 10, NOW + 3600),
                 candidate('other', 'p2', 1000, 100, NOW + 3600)]
        self.assertEqual(self.choose(costs), (allocator.choose_route(costs, NOW, 900), 'subscription'))
        tight = dict(sub_pool(), windows=[window('5h', used=900, reset_at=NOW + 3600)])
        roomy = dict(sub_pool(), windows=[window('5h', used=0, reset_at=NOW + 3600)])
        routes = [route('r1', 'p1', SUB, 'a1', {'5h': 10}), route('r2', 'p2', SUB, 'a2', {'5h': 10})]
        found = self.decide(routes, dict(p1=tight, p2=roomy))
        self.assertEqual((found['status'], found['route'], found['class'], found['shares']),
                         ('admitted', 'r2', 'subscription', dict(api=0.0, subscription=1.0)), found)
        self.assertEqual((found['pressure']['u'], found['pressure']['e']), (None, None))

    def test_multiple_models_do_not_multiply_pool_weight(self):
        many = [candidate('p1-m%d' % n, 'p1', 200, 10, NOW + 3600) for n in range(3)]
        one = [candidate('p2-m', 'p2', 300, 10, NOW + 3600)]
        self.assertEqual(self.choose(many + one), (allocator.choose_route(many + one, NOW, 900), 'subscription'))
        self.assertEqual(self.choose(many + one)[0], 'p2-m')  # three models of p1 add no weight to p1
        pools = dict(p1=dict(windows=[window('5h', used=800, reset_at=NOW + 3600)]),
                     p2=dict(windows=[window('5h', used=700, reset_at=NOW + 3600)]))
        routes = [route('m%d' % n, 'p1', SUB, 'a%d' % n, {'5h': 10}) for n in range(3)] + \
            [route('q', 'p2', SUB, 'b', {'5h': 10})]
        self.assertEqual(self.decide(routes, pools)['route'], 'q')

    def test_all_demanded_windows_constrain_admission(self):
        paid, paid_pools = paid_routes()
        week = dict(windows=[window('5h'), window('7d', used=995)])
        both = route('s', 's', SUB, 's-acct', {'5h': 10, '7d': 10})
        self.assert_waits(self.decide([both] + paid, dict(paid_pools, s=week)))
        fits = self.decide([route('s', 's', SUB, 's-acct', {'5h': 10, '7d': 1})] + paid, dict(paid_pools, s=week),
                           attempt='a2')
        self.assertEqual((fits['status'], fits['route'], fits['reserved']),
                         ('admitted', 's', {'win:s/5h': 12, 'win:s/7d': 2}), fits)

    def test_stale_or_missing_subscription_window_waits(self):
        paid, paid_pools = paid_routes()
        demand = {'5h': 10}
        for name, pools in (('stale', dict(s=dict(windows=[window('5h', fresh=False)]))),
                            ('expired', dict(s=dict(windows=[window('5h', reset_at=NOW - 1)]))),
                            ('missing window', dict(s=dict(windows=[window('7d')]))),
                            ('unobserved pool', {})):
            with self.subTest(name):
                self.assert_waits(self.decide([route('s', 's', SUB, 's-acct', demand)] + paid,
                                              dict(paid_pools, **pools)))

    def test_no_feasible_subscription_never_falls_back_to_paid(self):
        paid, paid_pools = paid_routes()
        for name, used, demand in (('exhausted', 1000, 1), ('does not fit', 995, 10)):
            with self.subTest(name):
                pools = dict(paid_pools, s=dict(windows=[window('5h', used=used)]))
                answer = self.decide([route('s', 's', SUB, 's-acct', {'5h': demand})] + paid, pools)
                self.assert_waits(answer)
                self.assertEqual(answer['reason_codes'][:2], ['no_feasible_route', 'insufficient_headroom'])

    def test_ranked_api_route_is_excluded_even_with_legacy_enablement(self):
        paid, paid_pools = paid_routes()
        self.assertEqual(routing_funding.ranked_routes(observations(paid, paid_pools), ranking('s-api', 's-gw')), [])
        self.assert_waits(self.decide(paid, paid_pools, api_usage_enabled=True))
        legacy = dict({key: paid[0][key] for key in routing.ROUTE_IDENTITY}, funding='api')  # synthetic coverage only
        chosen = routing.admit(dict(assignment('legacy'), choice=legacy), ranking(), settings(api_usage_enabled=True),
                               observations(paid, paid_pools), ledger=self.ledger)
        self.assertEqual((chosen['status'], chosen['mode'], chosen['reserved']),
                         ('admitted', 'worker_choice', {'org:org': 1_200_000}), chosen)

    def test_no_route_at_all_waits_with_a_reason(self):
        answer = self.decide([], {}, ranked=['s1'])
        self.assert_waits(answer)
        self.assertEqual(answer['reason_code'], 'no_feasible_route')

    def test_choose_never_replays_a_paid_candidate_as_subscription(self):
        for candidates, feasible in (([candidate('api', 'p', 100, 1, cls='api')], ['subscription']),
                                     ([candidate('s', 'p', 100, 1)], ['api', 'subscription']),
                                     ([candidate('s', 'p', 100, 1)], ['api'])):
            with self.subTest(feasible=feasible), self.assertRaisesRegex(ValueError, 'billing_class_excluded'):
                routing_funding.choose(candidates, dict(api=0.0, subscription=1.0), [], ['k', 'b'], 0, feasible,
                                       NOW, 900)
        self.assertEqual(self.choose([]), (None, None))


if __name__ == '__main__':
    unittest.main()
