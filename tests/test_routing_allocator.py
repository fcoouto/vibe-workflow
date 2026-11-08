import copy
import unittest

from runtime import routing_allocator as a

BUCKET = ('implementer', 'medium')


def window(used, length, to_reset, **extra):
    return dict(id=extra.pop('id', 'w'), used=used, reserved=0, length=length, reset_at=1000 + to_reset, **extra)


class UtilizationTests(unittest.TestCase):
    def test_weekly_window_early_in_cycle_projects_above_bare_use(self):
        u, reasons = a.projected_utilization([window(0.30, 100, 90)], 0, 1000, 0.10)
        self.assertGreater(u, 0.30)
        self.assertAlmostEqual(u, 3.0)
        self.assertEqual(reasons, [])

    def test_short_window_close_to_reset(self):
        u, _ = a.projected_utilization([window(0.80, 300, 10)], 0, 1000, 0.10)  # minutes
        self.assertAlmostEqual(u, 0.8276, places=3)
        self.assertAlmostEqual(round(u, 2), 0.83)

    def test_pace_omitted_before_minimum_elapsed_fraction(self):
        u, reasons = a.projected_utilization([window(0.05, 100, 95)], 0.02, 1000, 0.10)
        self.assertAlmostEqual(u, 0.07)
        self.assertEqual(reasons, ['pace_unavailable'])

    def test_reservations_count(self):
        w = dict(window(0.5, 100, 50), reserved=0.1)
        u, _ = a.projected_utilization([w], 0.05, 1000, 0.10)
        self.assertAlmostEqual(u, 0.5 + 0.1 + 0.05 + 1.0 * 0.5)

    def test_maximum_over_windows(self):
        windows = [window(0.2, 100, 50, id='a'), window(0.9, 100, 1, id='b')]
        u, _ = a.projected_utilization(windows, 0, 1000, 0.10)
        self.assertAlmostEqual(u, 0.9 + 0.9 / 0.99 * 0.01)

    def test_per_window_reserve(self):
        windows = [window(0.2, 100, 50, id='a'), window(0.2, 100, 50, id='b')]
        u, _ = a.projected_utilization(windows, dict(a=0.0, b=0.3), 1000, 0.10)
        self.assertAlmostEqual(u, 0.2 + 0.3 + 0.2)

    def test_healthier_alternative_subscription_sets_u(self):
        routes = dict(hot=[window(0.9, 100, 50)], cool=[window(0.1, 100, 50)])
        u, _ = a.projected_utilization(routes, 0, 1000, 0.10)
        self.assertAlmostEqual(u, 0.2)

    def test_stale_or_unknown_is_not_zero(self):
        self.assertEqual(a.projected_utilization([window(0.1, 100, 50, fresh=False)], 0, 1000, 0.1),
                         (None, ['stale_observation']))
        self.assertEqual(a.projected_utilization([window(None, 100, 50)], 0, 1000, 0.1)[0], None)
        self.assertIsNone(a.projected_utilization([dict(window(0.1, 100, 50), reset_at=900)], 0, 1000, 0.1)[0])
        self.assertIsNone(a.projected_utilization([], 0, 1000, 0.1)[0])

    def test_stale_route_is_skipped_when_another_is_usable(self):
        routes = dict(old=[window(0.0, 100, 50, fresh=False)], ok=[window(0.4, 100, 50)])
        u, reasons = a.projected_utilization(routes, 0, 1000, 0.10)
        self.assertAlmostEqual(u, 0.8)
        self.assertIn('stale_observation', reasons)

    def test_invalid_observations_are_unknown_not_numbers(self):
        nan, inf = float('nan'), float('inf')
        for bad in (dict(used=nan), dict(used=-0.1), dict(used=inf), dict(reserved=nan), dict(reserved=-1),
                    dict(length=nan), dict(length=0), dict(reset_at=nan)):
            with self.subTest(bad):
                u, reasons = a.projected_utilization([dict(window(0.3, 100, 50), **bad)], 0, 1000, 0.1)
                self.assertIsNone(u)
                self.assertEqual(reasons, ['invalid_observation'])

    def test_invalid_arguments_are_refused(self):
        for reserve in (float('nan'), float('inf'), -0.1, 'x', dict(w=float('nan')), dict(w=-1)):
            with self.subTest(reserve), self.assertRaises(ValueError):
                a.projected_utilization([window(0.3, 100, 50)], reserve, 1000, 0.1)
        for fraction in (float('nan'), -0.1, 1.5, None):
            with self.subTest(fraction), self.assertRaises(ValueError):
                a.projected_utilization([window(0.3, 100, 50)], 0, 1000, fraction)
        with self.assertRaises(ValueError):
            a.projected_utilization([window(0.3, 100, 50)], 0, float('nan'), 0.1)

    def test_rolling_window_without_reset_omits_pace(self):
        u, reasons = a.projected_utilization([dict(id='r', used=0.4, reserved=0, length=100, reset_at=None)],
                                             0.1, 1000, 0.10)
        self.assertAlmostEqual(u, 0.5)
        self.assertEqual(reasons, ['pace_unavailable'])


class ShareTests(unittest.TestCase):
    def test_api_share_anchors(self):
        self.assertEqual(a.api_share(0.40, 0), 0)
        self.assertAlmostEqual(a.api_share(0.75, 0), 0.5)
        self.assertAlmostEqual(a.api_share(0.80, 0), 0.75)
        self.assertEqual(a.api_share(0.90, 0), 1)
        self.assertAlmostEqual(a.api_share(0.40, 2 / 3), 2 / 3)
        self.assertAlmostEqual(a.api_share(0.75, 2 / 3), 0.8333, places=3)

    def test_api_share_uses_configured_thresholds(self):
        self.assertAlmostEqual(a.api_share(0.5, 0, L=0.4, H=0.6), 0.5)

    def test_unknown_utilization_is_refused(self):
        with self.assertRaises(ValueError):
            a.api_share(None, 0)

    def test_api_share_refuses_bad_numbers(self):
        nan = float('nan')
        for args, kwargs in (((0.5, nan), {}), ((0.5, -0.1), {}), ((0.5, 1.5), {}), ((nan, 0), {}),
                             ((float('inf'), 0), {}), ((0.5, 0), dict(L=0.8, H=0.8)),
                             ((0.5, 0), dict(L=0.9, H=0.8)), ((0.5, 0), dict(L=-0.1, H=0.8)),
                             ((0.5, 0), dict(L=0.1, H=1.2)), ((0.5, 0), dict(L=nan, H=0.8)),
                             ((0.5, None), {})):
            with self.subTest(args, **kwargs), self.assertRaises(ValueError):
                a.api_share(*args, **kwargs)

    def test_expiry_pressure_refuses_bad_horizon(self):
        for horizon in (0, -72, float('nan'), None, float('inf'), True):
            with self.subTest(horizon), self.assertRaises(ValueError):
                a.expiry_pressure(50, 0, 24, horizon)

    def test_expiry_pressure(self):
        self.assertAlmostEqual(a.expiry_pressure(50, 0, 24, 72), 2 / 3)
        self.assertEqual(a.expiry_pressure(50, 50, 24, 72), 0)
        self.assertEqual(a.expiry_pressure(50, 80, 24, 72), 0)
        self.assertEqual(a.expiry_pressure(0, 0, 24, 72), 0)
        self.assertEqual(a.expiry_pressure(None, 0, 24, 72), 0)
        self.assertEqual(a.expiry_pressure(50, 0, None, 72), 0)
        self.assertEqual(a.expiry_pressure(50, 0, 0, 72), 0)   # expired
        self.assertEqual(a.expiry_pressure(50, 0, 100, 72), 0)  # distant

    def test_partial_expected_spend(self):
        self.assertAlmostEqual(a.expiry_pressure(100, 25, 36, 72), 0.5 * 0.75)


class ClassTests(unittest.TestCase):
    def run_decisions(self, shares, count, feasible=('api', 'subscription')):
        records = []
        for step in range(count):
            chosen = a.choose_class(shares, records, BUCKET, 0, feasible)
            records.append(dict(bucket=BUCKET, at=step, shares=dict(shares), **{'class': chosen}))
        return records

    def test_class_choice_matches_target_shares(self):
        records = self.run_decisions(dict(api=0.25, subscription=0.75), 20)
        self.assertEqual(sum(r['class'] == 'api' for r in records), 5)
        for n in range(1, 21):
            api = sum(r['class'] == 'api' for r in records[:n])
            self.assertLessEqual(abs(api - 0.25 * n), 1)

    def test_non_api_ties_are_deterministic(self):
        shares = dict(zeta=0.5, alpha=0.5)
        for order in (shares, dict(reversed(list(shares.items())))):
            self.assertEqual(a.choose_class(order, [], BUCKET, 0, ['zeta', 'alpha']), 'zeta')

    def test_feasible_may_be_a_generator(self):
        shares = dict(api=0.5, subscription=0.5)
        self.assertEqual(a.choose_class(shares, [], BUCKET, 0, (c for c in ('subscription',))), 'subscription')

    def test_api_wins_ties(self):
        self.assertEqual(a.choose_class(dict(api=0.5, subscription=0.5), [], BUCKET, 0, ('api', 'subscription')),
                         'api')

    def test_only_feasible_positive_share_classes_compete(self):
        shares = dict(api=0.0, subscription=1.0)
        self.assertEqual(a.choose_class(shares, [], BUCKET, 0, ('api', 'subscription')), 'subscription')
        self.assertEqual(a.choose_class(dict(api=0.9, subscription=0.1), [], BUCKET, 0, ('subscription',)),
                         'subscription')
        self.assertIsNone(a.choose_class(dict(api=0.9, subscription=0.1), [], BUCKET, 0, ()))
        self.assertIsNone(a.choose_class(dict(api=0.0, subscription=0.0), [], BUCKET, 0, ('api', 'subscription')))

    def test_rejected_decision_leaves_no_record(self):
        records = self.run_decisions(dict(api=0.4, subscription=0.6), 5)
        before = copy.deepcopy(records)
        first = a.choose_class(dict(api=0.4, subscription=0.6), records, BUCKET, 0, ('api', 'subscription'))
        second = a.choose_class(dict(api=0.4, subscription=0.6), records, BUCKET, 0, ('api', 'subscription'))
        self.assertEqual(first, second)
        self.assertEqual(records, before)

    def test_other_buckets_and_earlier_epochs_do_not_count(self):
        shares = dict(api=0.5, subscription=0.5)
        noise = [dict(bucket=('reviewer', 'high'), at=5, shares=shares, **{'class': 'api'}) for _ in range(4)]
        old = [dict(bucket=BUCKET, at=1, shares=shares, **{'class': 'api'}) for _ in range(4)]
        self.assertEqual(a.choose_class(shares, noise + old, BUCKET, 3, ('api', 'subscription')), 'api')

    def test_unavailable_class_deficit_builds_for_the_other(self):
        shares = dict(api=0.5, subscription=0.5)
        records = [dict(bucket=BUCKET, at=i, shares=shares, **{'class': 'subscription'}) for i in range(3)]
        self.assertEqual(a.choose_class(shares, records, BUCKET, 0, ('api', 'subscription')), 'api')


def candidate(route, pool, headroom, resets_at, cost=1):
    return dict(route=route, pool=pool,
                constraints=[dict(pool=pool, window='w', headroom=headroom, cost=cost, resets_at=resets_at)])


class RouteTests(unittest.TestCase):
    def admit(self, remaining, horizons, count, costs=None, minimum=0.25):
        """Sequentially admit `count` tasks; remaining maps pool to comparable tasks of unit cost."""
        costs = costs or {}
        chosen = []
        for _ in range(count):
            options = [candidate('r-' + p, p, remaining[p] * costs.get(p, 1), horizons[p], costs.get(p, 1))
                       for p in sorted(remaining)]
            route = a.choose_route(options, 0, minimum)
            pool = route[2:]
            remaining[pool] -= 1
            chosen.append(pool)
        return chosen

    def test_case_7_equalizes_remaining_capacity(self):
        remaining = dict(a=56, b=75)
        chosen = self.admit(remaining, dict(a=10, b=10), 21)
        self.assertEqual(chosen[:19], ['b'] * 19)
        self.assertEqual(chosen[19:], ['a', 'b'])  # equal at 56 each; the pool id breaks the tie

    def test_case_7_as_stated_in_percentages(self):
        remaining = dict(a=20, b=75)
        chosen = self.admit(remaining, dict(a=10, b=10), 55)
        self.assertEqual(chosen, ['b'] * 55)
        self.assertEqual(remaining, dict(a=20, b=20))
        self.assertEqual(self.admit(remaining, dict(a=10, b=10), 4), ['a', 'b', 'a', 'b'])

    def test_case_7_capacity_scales_percentages(self):
        # 20% of 1,000 is 200 comparable tasks; 75% of 100 is 75, not the reverse.
        remaining = dict(a=200, b=75)
        chosen = self.admit(remaining, dict(a=10, b=10), 125)
        self.assertEqual(chosen, ['a'] * 125)
        self.assertEqual(remaining, dict(a=75, b=75))

    def test_case_8_near_expiry_outranks_for_17_admissions(self):
        remaining = dict(s=20, l=75)
        chosen = self.admit(remaining, dict(s=1, l=24), 18)
        self.assertEqual(chosen[:17], ['s'] * 17)
        self.assertEqual(chosen[17], 'l')

    def test_case_8_exhausted_short_window_vetoes(self):
        options = [candidate('r-s', 's', 0, 1), candidate('r-l', 'l', 75, 24)]
        self.assertEqual(a.choose_route(options, 0, 0.25), 'r-l')
        self.assertIsNone(a.choose_route([candidate('r-s', 's', 0, 1)], 0, 0.25))

    def test_minimum_horizon_bounds_pressure(self):
        near = candidate('near', 'n', 10, 0.01)
        far = candidate('far', 'f', 20, 0.5)
        self.assertEqual(a.choose_route([near, far], 0, 1), 'far')

    def test_binding_constraint_is_the_minimum_over_windows(self):
        both = dict(route='r', pool='p', constraints=[
            dict(pool='p', window='5h', headroom=100, cost=1, resets_at=5),
            dict(pool='p', window='week', headroom=3, cost=1, resets_at=100)])
        other = candidate('o', 'q', 10, 100)
        self.assertEqual(a.choose_route([both, other], 0, 1), 'o')

    def test_aliases_sharing_a_pool_do_not_multiply_weight(self):
        a1 = candidate('alias-1', 'shared', 10, 10)
        a2 = candidate('alias-2', 'shared', 10, 10)
        a3 = candidate('alias-3', 'shared', 10, 10)
        other = candidate('other', 'solo', 11, 10)
        self.assertEqual(a.choose_route([a1, a2, a3, other], 0, 1), 'other')
        self.assertEqual(a.choose_route([a1, a2, a3], 0, 1), 'alias-1')

    def test_ties_use_earliest_reset_then_pool_id(self):
        early = candidate('e', 'z', 10, 10)
        late = candidate('l', 'a', 20, 20)
        self.assertEqual(a.choose_route([late, early], 0, 1), 'e')
        self.assertEqual(a.choose_route([candidate('b', 'pb', 10, 10), candidate('a', 'pa', 10, 10)], 0, 1), 'a')

    def test_zero_priced_routes_never_divide_by_zero(self):
        free = dict(route='free', pool='f', constraints=[dict(pool='f', window='w', headroom=0, cost=0, resets_at=10)])
        none = dict(route='none', pool='n', constraints=[])
        self.assertEqual(a.choose_route([free, none], 0, 1), 'free')  # ranking order decides
        paid = candidate('paid', 'p', 5, 10)
        self.assertEqual(a.choose_route([free, paid], 0, 1), 'paid')

    def test_rolling_window_without_reset_has_no_invented_weight(self):
        rolling = candidate('rolling', 'r', 100, None)
        dated = candidate('dated', 'd', 1, 10)
        self.assertEqual(a.choose_route([rolling, dated], 0, 1), 'dated')

    def test_exhausted_constraint_without_reset_is_vetoed(self):
        self.assertIsNone(a.choose_route([candidate('r', 'p', 0, None)], 0, 1))
        self.assertEqual(a.choose_route([candidate('r', 'p', 0, None), candidate('ok', 'q', 5, 10)], 0, 1), 'ok')

    def test_infeasible_best_candidate_does_not_hide_lower_ranked_in_pool(self):
        broken = candidate('best', 'shared', 0, 10)
        fallback = candidate('second', 'shared', 5, 10)
        self.assertEqual(a.choose_route([broken, fallback], 0, 1), 'second')
        self.assertEqual(a.choose_route([broken, fallback, candidate('x', 'x', 1, 10)], 0, 1), 'second')

    def test_rolling_unscored_best_does_not_hide_scored_lower_ranked_in_pool(self):
        rolling = candidate('rolling', 'shared', 100, None)
        dated = candidate('dated', 'shared', 5, 10)
        self.assertEqual(a.choose_route([rolling, dated], 0, 1), 'rolling')  # first eligible per pool wins
        self.assertEqual(a.choose_route([candidate('dead', 'shared', 0, None), dated], 0, 1), 'dated')

    def test_bad_numbers_are_refused_by_choose_route(self):
        good = [candidate('r', 'p', 5, 10)]
        for horizon in (0, -1, float('nan'), float('inf'), None, True):
            with self.subTest(horizon), self.assertRaises(ValueError):
                a.choose_route(good, 0, horizon)
        with self.assertRaises(ValueError):
            a.choose_route(good, float('nan'), 1)
        self.assertEqual(a.choose_route([candidate('r', 'p', 5, 0)], 0, 1), 'r')  # reset == now stays finite
        for bad in (candidate('r', 'p', float('nan'), 10), candidate('r', 'p', -1, 10),
                    candidate('r', 'p', 5, 10, cost=float('inf')), candidate('r', 'p', 5, 10, cost=-1),
                    candidate('r', 'p', 5, float('nan'))):
            with self.subTest(bad), self.assertRaises(ValueError):
                a.choose_route([bad], 0, 1)

    def test_no_candidates(self):
        self.assertIsNone(a.choose_route([], 0, 1))

    def test_functions_are_pure(self):
        options = [candidate('r', 'p', 10, 10)]
        snapshot = copy.deepcopy(options)
        a.choose_route(options, 0, 1)
        self.assertEqual(options, snapshot)


class PublicBindingTests(unittest.TestCase):
    def test_binding_constraint_is_the_public_name(self):
        self.assertIs(a.binding_constraint, a._binding)
        found = a.binding_constraint([dict(pool='p', window='w', headroom=10, cost=2, resets_at=1100)], 1000, 900)
        self.assertEqual(found[0], 5)


if __name__ == '__main__':
    unittest.main()
