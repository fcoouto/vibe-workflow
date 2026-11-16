"""F5.A3: route-local refusal in order, through the real routing command (`scripts/routing.Service`).

A paid API route that is off, unfunded, stale, unauthorized or unqualified is refused for its own reason while an
eligible subscription beside it is admitted. A ranked request never falls back to a paid route. An explicit paid
choice is never substituted. A running or uncertain attempt never gets a second send or an alternate permit, even when
budget for an alternative exists. Disposable stores and caller snapshots only; nothing here is live.
"""
import json
import unittest
from unittest import mock

from runtime import routing, routing_observations as collector, subscription_billing, subscription_fallback, \
    usage_budget as budget
from tests.test_routing_command import (API, API_CHOICE, Base, M, POLICY, api_observations, check, facts, now, prepare,
                                        prepare_legacy_paid)
from tests.test_routing_dispatch import dispatch, record
from tests.test_routing_observations import (SCOPE, Stores, choice_of, excluded, ranked_of, ready, request_of,
                                             sub_route, sub_window)


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


class QuotaOptionalAdmissionTests(Stores):
    """Task 2: an eligible subscription account is admitted as provider-managed fallback when its included quota or
    demand is unknown, through the real collector (synthetic source doubles) and `routing.admit`."""

    def admit(self, routes, windows, attempt='q1', choice=None, source=None, **request):
        if source is None:
            result = self.collect(routes, windows, **request)
        else:
            result = collector.collect(str(self.root), dict(scope=SCOPE), settings_path=self.settings,
                                       ledger_path=self.ledger_path, source=source, **request)
        ranked = 'strategy_ids' in request and choice is None
        choice = choice if ranked or choice else choice_of(routes[0])
        settings = dict(routing.routing_settings.resolve(dict(smart_routing=ranked)), revision=0)
        ranking = dict(strategy_ids=request.get('strategy_ids', []), reason_codes=[])
        assignment = dict(attempt_id=attempt, task='t-' + attempt, role='software-engineer',
                          work_kind='implementation', complexity_band='medium', scope=SCOPE,
                          **({'choice': choice} if choice else {}))
        return result, routing.admit(assignment, ranking, settings, result['observations'],
                                     ledger=self.service._ledger(True, True))

    def assert_fallback(self, answer, disposition='quota_unknown'):
        self.assertEqual(answer['status'], 'admitted', answer)
        self.assertEqual(answer['reserved'], {})
        self.assertIn(routing.FALLBACK, answer['reason_codes'])
        self.assertIn(disposition, answer['reason_codes'])
        stored = next(item for item in self.data()['admissions'] if item['attempt_id'] == answer['attempt_id'])
        self.assertIn(disposition, stored['reason_codes'])
        self.assertEqual(stored['reserved'], {})

    def test_unknown_quota_is_fallback_not_headroom(self):
        unitless = {key: value for key, value in sub_window(self.now).items() if key != 'unit'}
        cases = dict(
            absent=(sub_route(), {}),
            stale=(sub_route(), dict(sub=[sub_window(self.now - collector.FRESH_SECONDS - 1)])),
            partial=(sub_route(demand={'5h': 10, '7d': 5}), dict(sub=[sub_window(self.now)])),
            malformed=(sub_route(), dict(sub=[dict(sub_window(self.now), limit='lots')])),
            unread_used=(sub_route(), dict(sub=[dict(sub_window(self.now), used=None)])),
            null_demand=(sub_route(demand=None), dict(sub=[sub_window(self.now)])))
        for name, (route, windows) in cases.items():
            with self.subTest(name):
                result, answer = self.admit([route], windows, attempt='q-' + name)
                self.assertNotIn('sub', excluded(result))
                self.assert_fallback(answer)

    def test_unverified_model_demand_is_fallback_not_an_included_claim(self):
        route = sub_route(demand_scope='per_model')
        for verified, expect in ((None, 'quota_unknown'), (True, None)):
            with self.subTest(verified=verified):
                source = collector.StaticSource([route], dict(sub=[sub_window(self.now)]), ready,
                                                billing=subscription_billing.double('q'),
                                                model_demand=None if verified is None else lambda spec: verified)
                _, answer = self.admit([route], None, attempt='m-%s' % verified, source=source, **request_of([route]))
                if expect is None:
                    self.assertEqual((answer['status'], bool(answer['reserved'])), ('admitted', True))
                else:
                    self.assert_fallback(answer)

    def test_empty_demand_never_becomes_an_included_claim(self):
        refused, answer = self.admit([sub_route(demand={})], dict(sub=[sub_window(self.now)]), attempt='z1')
        self.assertEqual((excluded(refused), answer['status']), (dict(sub='route_invalid'), 'pending'))  # collector
        observations = self.collect([sub_route()], dict(sub=[sub_window(self.now)]))['observations']
        observations['routes'][0]['demand'] = {}  # a caller snapshot: an eligible account with an empty demand
        assignment = dict(attempt_id='z2', task='t-z2', role='software-engineer', work_kind='implementation',
                          complexity_band='medium', scope=SCOPE, choice=choice_of(sub_route()))
        answer = routing.admit(assignment, dict(strategy_ids=[], reason_codes=[]), dict(
            routing.routing_settings.resolve({}), revision=0), observations, ledger=self.service._ledger(True, True))
        self.assert_fallback(answer)  # admitted as provider-managed fallback with reserved={}, never a zero-cost claim

    def test_known_included_wins_unless_explicit_choice_excludes_it(self):
        known = sub_route('known', model='m-known', pool='known')
        unknown = sub_route('unknown', model='m-unknown', pool='unknown')
        windows = dict(known=[sub_window(self.now)], unknown=[])
        routes, request = ranked_of([unknown, known])  # the unknown one ranks first
        _, ranked = self.admit(routes, windows, attempt='r1', **request)
        self.assertEqual((ranked['status'], ranked['route']), ('admitted', 'known'), ranked)
        self.assertNotIn(routing.FALLBACK, ranked['reason_codes'])
        _, ranked_unknown = self.admit(routes, dict(known=[], unknown=[]), attempt='r2', **request)
        self.assertEqual(ranked_unknown['route'], 'unknown')  # value order when none is included
        self.assert_fallback(ranked_unknown)
        _, explicit = self.admit([unknown, known], windows, attempt='e1', choice=choice_of(unknown))
        self.assertEqual(explicit['route'], 'unknown', explicit)  # known headroom never overrides it
        self.assert_fallback(explicit)

    def test_insufficient_and_fenced_known_quota_record_their_disposition(self):
        _, short = self.admit([sub_route()], dict(sub=[sub_window(self.now, used=999)]), attempt='s1')
        self.assert_fallback(short, 'quota_insufficient')
        with mock.patch.object(subscription_fallback, 'active_pools', return_value={'sub'}):
            _, fenced = self.admit([sub_route()], dict(sub=[sub_window(self.now)]), attempt='f1')
        self.assert_fallback(fenced, 'quota_fenced')

    def test_unknown_overflow_is_eligible_but_provider_refusal_and_identity_failures_launch_nothing(self):
        unknown = lambda route, at: dict(subscription_billing.double('q')(route, at), paid_crossover='unknown')
        source = collector.StaticSource([sub_route()], {}, ready, billing=unknown)
        _, answer = self.admit([sub_route()], None, attempt='o1', source=source, **request_of([sub_route()]))
        self.assert_fallback(answer)
        collector.mark_not_entitled(self.settings, 'sub', 'sub-account', self.now)
        refused, answer = self.admit([sub_route()], {}, attempt='o2')
        self.assertEqual(excluded(refused), dict(sub='not_entitled'))
        self.assertEqual(answer['status'], 'pending')
        collector.clear_not_entitled(self.settings, 'sub', 'sub-account')
        unbilled = collector.StaticSource([sub_route()], {}, ready)  # no account fact: identity unverified
        missing, answer = self.admit([sub_route()], None, attempt='o3', source=unbilled, **request_of([sub_route()]))
        self.assertEqual(excluded(missing), dict(sub=subscription_billing.UNVERIFIED))
        self.assertEqual(answer['status'], 'pending')
        unbound, answer = self.admit([sub_route(binding_enforceable=False)], {}, attempt='o4')
        self.assertEqual(answer['status'], 'pending')
        self.assertEqual(self.data()['holds'].keys(), {'o1'})

    def test_null_demand_is_never_valid_for_a_gateway_or_paid_route(self):
        routes = [dict(sub_route(), billing_class='gateway', demand=None)]
        self.assertFalse(routing._valid_route(routes[0]))
        self.assertTrue(routing._valid_route(sub_route(demand=None)))
        self.assertFalse(routing._valid_route(dict(sub_route(demand=None), billing_class='api_prepaid')))

    def test_new_fallback_records_replay_strictly(self):
        from tests.qualification import replay_strict
        routes, request = ranked_of([sub_route('unknown', pool='unknown'), sub_route('short', model='m2', pool='short')])
        _, answer = self.admit(routes, dict(unknown=[], short=[sub_window(self.now, used=999)]), attempt='p1',
                               **request)
        self.assert_fallback(answer)
        report = replay_strict.replay_records(self.data()['admissions'])
        self.assertTrue(report['passed'], report)


if __name__ == '__main__':
    unittest.main()
