"""Money invariants for one claim model: holds and runs share one organization total (spec 6.2, 7.4, 8).

I1 per organization: B - protected - R >= 0, where R sums every run call and every open hold.
I2 pilot, task and cycle limits hold with holds included.
I3 every dollar hold names an existing organization and carries an integer micro-unit amount.
I4 conversion keeps reserved dollars and the organization unchanged.
I5 each open attempt appears exactly once in the account inventory (F5.A1: an inventory, never a count limit).
I6 a closed hold contributes 0; an uncertain hold contributes its full amount.
I7 the record stays under MAX_RECORD at maximum table sizes.
"""
import json
import unittest
from runtime import routing, routing_settings, usage_budget as budget
from tests.fixture_routing_funding import Funded, M, NOW, assignment, funded_pool, observations, policy, ranking, reserved, route, settings, sub_pool, window


class SharedOrganizationTests(Funded, unittest.TestCase):
    def test_b1_funded_window_without_organization_is_excluded(self):
        for kind in ('api_grant', 'api_prepaid', 'gateway'):
            with self.subTest(kind):
                pools = dict(p=dict(billing_class=kind, windows=[window()]))
                result = self.admit('a-' + kind, [route('r', 'p', kind, 'acct', {'credit': M})], pools)
                self.assertEqual(result['status'], 'pending')
                self.assertIn('no_funded_organization', result['reason_codes'])  # F5.3: the legacy choice's cause
        self.assertEqual(self.ledger.snapshot()['holds'], {})

    def test_b1_unknown_organization_is_excluded(self):
        pools = dict(p=funded_pool('ghost'))
        result = self.admit('a', [route('r', 'p', 'api_grant', 'acct', {'credit': M})], pools)
        self.assertIn('no_funded_organization', result['reason_codes'])
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


class OptionalOrganizationTests(Funded, unittest.TestCase):
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
        self.assertEqual(only_api['status'], 'pending')
        self.assertIn('no_funded_organization', only_api['reason_codes'])
        both = self.admit('b', routes, pools)
        self.assertEqual((both['status'], both['class'], both['route']), ('admitted', 'subscription', 's'))
        cfg = settings(smart_routing=False, worker_choices={'software-engineer': [self.CHOICE]})
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
        # F5.A1: a saved per-account cap of one is not enforced; the second personal attempt draws on its own money
        self.assertEqual(self.admit('b', mine[:1], pools)['status'], 'admitted')
        self.assertEqual(self.admit('c', team[:1], pools)['reserved'], {'org:team': 1_200_000})
        self.assertEqual((self.ledger.headroom(budget.PERSONAL), self.ledger.headroom('team')),
                         (9_800_000 - 2 * 1_200_000, 9_800_000 - 1_200_000))
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


class MultiWindowFundingTests(Funded, unittest.TestCase):
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


class ObservationNitTests(Funded, unittest.TestCase):
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
        self.assertEqual(result['status'], 'pending')
        self.assertIn('credential_unavailable', result['reason_codes'])


if __name__ == '__main__':
    unittest.main()
