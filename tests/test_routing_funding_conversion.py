"""Funding: hold-to-run conversion (I4) and exception fuzz. See test_routing_funding.py for the invariants."""
import copy
import unittest
from runtime import routing, usage_budget as budget
from tests.fixture_routing_funding import Funded, M, NOW, assignment, authorize, funded_pool, native, observations, ranking, reserved, route, settings, sub_pool


class ConversionTests(Funded, unittest.TestCase):
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

    def test_a_converted_run_and_a_new_hold_share_one_provider_account_without_a_count(self):
        """F5.A1 (was: the per-account cap counts runs and holds): the account inventory keeps both; money decides."""
        self.ledger.set_caps(40, 1)
        self.convert(M)
        pools = dict(g=funded_pool('org'))
        again = self.admit('b', [route('r', 'g', 'api_grant', 'acct-A', {'credit': M})], pools)
        self.assertEqual(again['reserved'], {'org:org': 1_200_000})  # the legacy explicit choice, funded by headroom
        self.assertEqual(budget.Ledger.open_accounts(self.ledger.snapshot()), ['acct-A', 'acct-A'])


class ExceptionFuzzTests(Funded, unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
