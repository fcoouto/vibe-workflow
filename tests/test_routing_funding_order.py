"""F5.2: one subscription shortlist of at most ten ranked ids, cut after the static checks and before any window.

`routing_funding.subscription_shortlist` is pure. A caller's own snapshot (no `shortlist`) is cut in admission after the
same non-window checks; a supplied `shortlist` may only narrow the ranking, never widen it or name a missing route.
"""
import unittest

from runtime import routing, routing_funding
from tests.fixture_routing_funding import Funded, assignment, observations, ranking, route, settings, sub_pool

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
        self.assertIn(paid, evaluated)  # the paid route is still evaluated (its pool is unknown here)

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


if __name__ == '__main__':
    unittest.main()
