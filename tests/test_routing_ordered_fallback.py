"""F5.A3: route-local refusal in order, through the real routing command (`scripts/routing.Service`).

A paid API route that is off, unfunded, stale, unauthorized or unqualified is refused for its own reason while an
eligible subscription beside it is admitted. A ranked request never falls back to a paid route. An explicit paid
choice is never substituted. A running or uncertain attempt never gets a second send or an alternate permit, even when
budget for an alternative exists. Disposable stores and caller snapshots only; nothing here is live.
"""
import json
import unittest

from runtime import routing, usage_budget as budget
from tests.test_routing_command import (API, API_CHOICE, Base, M, POLICY, api_observations, check, facts, now, prepare,
                                        prepare_legacy_paid)
from tests.test_routing_dispatch import dispatch, record


def ordered_table(*strategies):
    """The paid strategy ranked FIRST, so every subscription admission below is a real skip of it."""
    entries = dict(api=dict(strategy_id='s-api', model='m', effort='high', harness='api', evidence_refs=['g1']),
                   sub=dict(strategy_id='s-sub', model='m', effort='high', harness='h', evidence_refs=['g1']),
                   sub2=dict(strategy_id='s-sub2', model='m2', effort='high', harness='h', evidence_refs=['g1']))
    return dict(schema=routing.TABLE_SCHEMA, version='t-order',
                rows={'implementation|medium|medium': [entries[name] for name in strategies]})


def snapshot(**api):
    value = api_observations()
    value['routes'][1].update(api)
    second = dict(value['routes'][0], route='sub2', strategy_id='s-sub2', model='m2', pool='sub2')
    value['routes'].append(second)
    value['pools']['sub2'] = json.loads(json.dumps(value['pools']['sub']))
    return value


class Ordered(Base):
    def setUp(self):
        super().setUp()
        self.store_table(ordered_table('api', 'sub', 'sub2'))
        budget.initialize(self.ledger)

    def enable_api(self):
        revision = self.call(dict(v=1, op='status'))['settings_revision']
        self.assertEqual(self.call(dict(v=1, op='set', expected_revision=revision,
                                        change=dict(api_usage_enabled=True)))['state'], 'updated')

    def fund(self, organization='personal', at=None):
        with self.open_ledger() as ledger:
            ledger.enable_organization(organization, POLICY, 0)
            ledger.observe(organization, 1000 * M, at or now(), issued=1000 * M)

    def holds(self):
        with self.open_ledger() as ledger:
            return ledger.snapshot()['holds']

    def ranked(self, attempt, observations):
        return self.call(prepare(attempt, observations=observations))

    def explicit(self, attempt, observations):
        return self.call(prepare_legacy_paid(attempt, observations=observations))


class RouteLocalRefusalTests(Ordered):
    CASES = dict(  # condition -> (setup, snapshot change, the reason the explicit paid choice reports)
        api_off=(lambda self: self.fund(), {}, 'api_usage_disabled'),
        no_organization=(lambda self: self.enable_api(), {}, 'no_funded_organization'),
        insufficient=(lambda self: (self.enable_api(), self.fund()), dict(demand={'credit': 5000 * M}),
                      'insufficient_headroom'),
        stale_balance=(lambda self: (self.enable_api(), self.fund(at=now() - 100_000)), {}, 'funding_unverified'),
        credential_missing=(lambda self: (self.enable_api(), self.fund()),
                            dict(credential=dict(ref='keychain:x', scope='scope1', state='missing')),
                            'credential_unavailable'),
        unauthorized=(lambda self: (self.enable_api(), self.fund()), dict(permitted=False), 'not_permitted'),
        unqualified=(lambda self: (self.enable_api(), self.fund()), dict(funding_verified=False), 'funding_unverified'),
    )

    def test_each_refused_paid_route_leaves_the_eligible_subscription_admitted_in_ranked_order(self):
        for condition, (setup, change, reason) in self.CASES.items():
            with self.subTest(condition):
                self.tearDown(); self.setUp()
                setup(self)
                ranked = self.ranked('r-' + condition, snapshot(**change))
                self.assertEqual(ranked['state'], 'admitted', ranked)
                admission = ranked['admission']
                self.assertEqual((admission['route'], admission['mode'], admission['class']),
                                 ('sub', 'ranked', 'subscription'))  # s-api ranked first, skipped; s-sub next
                explicit = self.explicit('e-' + condition, snapshot(**change))
                self.assertEqual(explicit['state'], 'pending', explicit)  # never substituted by the subscription
                self.assertEqual(explicit['reason'], 'explicit_choice_unavailable')
                self.assertIn(reason, explicit['reasons'])
                self.assertEqual(sorted(self.holds()), ['r-' + condition])  # the refusal reserved nothing

    def test_a_funded_paid_route_is_still_never_a_ranked_choice(self):
        self.enable_api(); self.fund()
        self.assertEqual(self.ranked('r1', snapshot())['admission']['route'], 'sub')
        paid = self.explicit('e1', snapshot())
        self.assertEqual((paid['state'], paid['admission']['route'], paid['admission']['class']),
                         ('admitted', 'api', 'api'))

    def test_without_an_eligible_alternative_the_actual_cause_is_reported(self):
        self.enable_api(); self.fund()
        full = snapshot()
        for pool in ('sub', 'sub2'):
            full['pools'][pool]['windows'][0]['used'] = 1000  # both subscriptions out of window
        answer = self.ranked('r1', full)
        self.assertEqual(answer['state'], 'pending', answer)
        self.assertIn('insufficient_headroom', answer['reasons'])
        self.assertEqual(self.holds(), {})  # the funded paid route is never the fallback

    def test_worker_choices_keep_their_order_and_skip_only_a_refused_choice(self):
        self.store_table(None)
        self.enable_api(); self.fund()
        sub = dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')
        revision = self.call(dict(v=1, op='status'))['settings_revision']
        self.assertEqual(self.call(dict(v=1, op='set', expected_revision=revision, change=dict(
            worker_choices={'software-engineer': [dict(API_CHOICE), sub]})))['state'], 'updated')
        first = self.ranked('w1', snapshot())
        self.assertEqual((first['admission']['route'], first['admission']['mode']), ('api', 'worker_choice'))
        refused = self.ranked('w2', snapshot(credential=dict(ref='keychain:x', scope='scope1', state='missing')))
        self.assertEqual((refused['admission']['route'], refused['admission']['mode']), ('sub', 'worker_choice'))


class NoDuplicateTests(Ordered):
    def test_a_lost_response_replay_of_an_uncertain_subscription_attempt_issues_nothing_new(self):
        first = self.ranked('a1', snapshot())
        self.assertEqual(first['admission']['route'], 'sub')
        token = self.call(dispatch('a1'))['authority']
        self.assertEqual(self.call(record('a1', 'uncertain'))['state'], 'recorded')
        replay = self.ranked('a1', snapshot())  # the coordinator lost the answer and asks again
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['admission']['route'], 'sub')  # never moved to the free `sub2` alternative
        again = self.call(dispatch('a1'))
        self.assertEqual((again['state'], again['hold_state']), ('already_dispatched', 'uncertain'))
        self.assertNotIn('authority', again)  # no second send permit
        self.assertEqual(self.call(check('a1', authority=token))['reason'], 'attempt_uncertain')
        self.assertEqual(sorted(self.holds()), ['a1'])
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')
        other = self.ranked('a2', snapshot())  # budget for alternatives exists: the replay above was not vacuous
        self.assertEqual(other['state'], 'admitted')

    def test_an_uncertain_paid_attempt_keeps_its_money_and_never_gets_a_second_permit(self):
        self.enable_api(); self.fund()
        paid = self.explicit('p1', snapshot())
        self.assertEqual(paid['admission']['route'], 'api')
        with self.open_ledger() as ledger:
            before = ledger.credit_headroom('personal')
        token = self.call(dispatch('p1', request=facts(**API)))['authority']
        self.assertEqual(self.call(record('p1', 'uncertain'))['state'], 'recorded')
        replay = self.explicit('p1', snapshot())
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['admission']['route'], 'api')  # never substituted by an eligible subscription
        again = self.call(dispatch('p1', request=facts(**API)))
        self.assertEqual((again['state'], again['hold_state']), ('already_dispatched', 'uncertain'))
        self.assertNotIn('authority', again)
        self.assertEqual(self.call(check('p1', authority=token, request=facts(**API)))['reason'], 'attempt_uncertain')
        with self.open_ledger() as ledger:
            self.assertEqual(ledger.credit_headroom('personal'), before)  # uncertain is never unsent proof
            self.assertEqual(ledger.snapshot()['holds']['p1']['state'], 'uncertain')
        self.assertEqual(self.explicit('p2', snapshot())['state'], 'admitted')  # a sufficient-budget alternative


if __name__ == '__main__':
    unittest.main()
