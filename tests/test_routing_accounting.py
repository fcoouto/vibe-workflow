"""S3.6a: lifecycle event deduplication, completion settlement, root-task settlement and retention.

Contract: docs/superpowers/plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention
"""
import hashlib
import json
import unittest
from runtime import routing, usage_budget as budget
from tests.test_routing_command import API, M, SECRET, api_observations, facts, now, prepare
from tests.test_routing_dispatch import record
from tests import fixture_routing_accounting as fixture
from tests.fixture_routing_accounting import TOKENS, complete, settle, usage, where


class StageTests(unittest.TestCase):
    def test_stage_follows_the_buckets_work_kind(self):
        def stage(bucket):
            return budget.Ledger._record_root(dict(bucket=bucket), 't')[1]
        self.assertEqual(stage(['review', 'low']), 'review')
        self.assertEqual((stage(['implementation', 'low']), stage(['investigation', 'low'])), ('worker', 'worker'))
        self.assertEqual(stage(['reviewer', 'low']), 'review')  # a stored bucket keeps its old role name
        self.assertEqual(budget.STAGES, ('coordinator', 'profiling', 'routing', 'worker', 'review', 'rescue', 'repair',
                                         'check', 'escalation', 'auxiliary'))


class EventTests(fixture.Accounting):
    def test_a_duplicate_event_returns_its_original_outcome_and_changes_nothing(self):
        self.funded('a1')
        first = self.call(complete('a1', 'c1', usage=usage(4 * M)))
        self.assertEqual((first['state'], first['duplicate'], first['outcome']), ('recorded', False, 'complete'))
        before = self.snapshot()
        stored = self.ledger_bytes()
        again = self.call(complete('a1', 'c1', usage=usage(4 * M)))
        self.assertEqual(again, dict(first, duplicate=True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.ledger_bytes(), stored)
        self.assertEqual(self.settled(), 4 * M)

    def test_a_conflicting_repeat_is_refused_and_changes_nothing(self):
        self.funded('a1')
        self.call(complete('a1', 'c1', usage=usage(4 * M)))
        before, events = self.snapshot(), self.events()
        for conflicting in (complete('a1', 'c1', usage=usage(3 * M)), record('a1', 'uncertain', 'c1'),
                            complete('a1', 'c1')):
            with self.subTest(event=conflicting['event']):
                self.assertEqual(self.call(conflicting), dict(v=1, state='blocked', reason='event_conflict'))
        self.assertEqual((self.snapshot(), self.events()), (before, events))

    def test_a_retry_with_a_new_client_timestamp_is_a_duplicate(self):
        self.funded('a1')
        first = self.call(complete('a1', 'c1', usage=usage(M), at=100))
        self.assertEqual(self.call(complete('a1', 'c1', usage=usage(M), at=200)), dict(first, duplicate=True))

    def test_a_refused_event_is_not_remembered(self):
        self.assertEqual(self.admit('a1')['state'], 'admitted')
        self.assertEqual(self.call(complete('a1', 'c1', usage=usage(M))),
                         dict(v=1, state='blocked', reason='hold_state_forbidden'))  # complete before dispatch
        self.send('a1')
        self.assertEqual(self.call(complete('a1', 'c1', usage=usage(M)))['duplicate'], False)

    def test_event_shapes_are_bounded_and_carry_no_content(self):
        self.funded('a1')
        good = complete('a1', 'c1', usage=usage(M))
        cases = [record('a1', 'complete', 'x' * 129),
                 record('a1', 'dispatched', usage=usage(M)),            # usage only on complete or observed
                 complete('a1', prompt=SECRET),
                 complete('a1', usage=dict(usage(M), body=SECRET)),
                 complete('a1', usage=dict(source='guess', requests=[])),
                 complete('a1', usage=usage(*[1] * (routing.MAX_USAGE_REQUESTS + 1))),
                 complete('a1', usage=dict(source='estimate', requests=[dict(usage(1)['requests'][0], text=SECRET)])),
                 complete('a1', usage=dict(source='estimate', requests=[dict(usage(1)['requests'][0], cost_usd_micros=-1)])),
                 complete('a1', usage=usage(budget.MAX_INTEGER + 1)),
                 complete('a1', stage='narrator'), complete('a1', request_kind='teleport'),
                 complete('a1', at='yesterday'), record('a1', 'observed', usage=usage(1))]  # observed needs a root
        for request in cases:
            with self.subTest(event=json.dumps(request['event'])[:120]):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason='invalid_request'))
        self.assertEqual(self.call(good)['state'], 'recorded')
        stored = json.dumps(self.events())
        self.assertNotIn(SECRET, stored)
        for name, body in self.events():
            self.assertLessEqual(len(json.dumps(body, separators=(',', ':'))), budget.MAX_EVENT_BYTES)
            self.assertEqual(set(body) - set(budget.EVENT_BODY_FIELDS), set())

    def test_fifteen_racing_processes_apply_one_completion(self):
        self.funded('race')
        self.service.close()
        same = complete('race', 'c-race', usage=usage(4 * M))
        results = self.collect([self.spawn('record', same) for _ in range(15)])
        self.assertEqual(sorted(r['duplicate'] for r in results), [False] + [True] * 14, results)
        self.assertEqual(len({json.dumps(r['settlement'], sort_keys=True) for r in results}), 1)
        self.assertEqual(self.settled(), 4 * M)
        self.funded('race2')
        self.service.close()
        distinct = [complete('race2', 'c-%d' % i, usage=usage(3 * M)) for i in range(15)]
        results = self.collect([self.spawn('record', request) for request in distinct])
        effects = [r for r in results if r['state'] == 'recorded' and not r['duplicate']]
        self.assertEqual(len(effects), 1, results)
        self.assertTrue(all(r == dict(v=1, state='blocked', reason='attempt_closed') for r in results
                            if r not in effects), results)
        self.assertEqual(self.settled(), 7 * M)


class SpecExampleTests(fixture.Accounting):
    BALANCE = 30 * M  # $30 available before new claims (no protected share)

    def test_the_spec_example_releases_padding_through_dispatch_and_complete(self):
        """Spec 7.4: $30 available; $5, $10 and $8 reserve $6, $12 and $9.60; the first finishing at $4
        releases its unused $2, raising room to $4.40, which admits a fourth task needing $3.60."""
        for attempt, demand in (('t1', 5 * M), ('t2', 10 * M), ('t3', 8 * M)):
            self.assertEqual(self.admit(attempt, demand)['state'], 'admitted')
        self.assertEqual([self.hold(a)['amount'] for a in ('t1', 't2', 't3')], [6 * M, 12 * M, 9_600_000])
        self.assertEqual(self.credit(), 2_400_000)
        self.assertEqual(self.admit('t4', 3 * M)['state'], 'pending')
        self.send('t1')
        result = self.call(complete('t1', 'c1', usage=usage(4 * M)))
        self.assertEqual(result['settlement'], dict(kind='usd_micros', reserved=6 * M, charged=4 * M,
                                                    released=2 * M, overrun=0, remaining=0, cost=4 * M,
                                                    cost_source='provider_receipt'))
        self.assertEqual(self.credit(), 4_400_000)
        self.assertEqual(self.admit('t4', 3 * M)['state'], 'admitted')
        self.assertEqual(self.credit(), 800_000)


class SettlementTests(fixture.Accounting):
    def test_the_charge_and_the_reservation_move_together_and_padding_is_released_once(self):
        self.funded('a1')
        total = budget.Ledger._total(self.snapshot(), 'personal')
        self.call(complete('a1', 'c1', usage=usage(1_000_001, 999_999, 1)))
        hold = self.hold('a1')
        self.assertEqual((hold['closed'], hold['amount']), (True, 0))
        settlement = hold['settlement']
        self.assertEqual(settlement['charged'] + settlement['released'] - settlement['overrun'], settlement['reserved'])
        self.assertEqual(settlement['charged'], 2_000_001)
        self.assertEqual(budget.Ledger._total(self.snapshot(), 'personal'), total - settlement['released'])
        for event_id in ('c1', 'c2'):
            self.call(complete('a1', event_id, usage=usage(1_000_001, 999_999, 1)))
        self.assertEqual(self.hold('a1')['settlement'], settlement)
        self.assertEqual(self.settled(), 2_000_001)

    def test_an_overrun_charges_the_reservation_and_holds_the_excess_unconfirmed(self):
        self.funded('a1')
        result = self.call(complete('a1', 'c1', usage=usage(10 * M)))
        self.assertEqual(result['outcome'], 'overrun')
        self.assertEqual({k: result['settlement'][k] for k in ('reserved', 'charged', 'released', 'overrun',
                                                               'overrun_state')},
                         dict(reserved=6 * M, charged=6 * M, released=0, overrun=4 * M, overrun_state='unconfirmed'))
        hold = self.hold('a1')
        self.assertEqual((hold['closed'], hold['amount'], hold['state']), (True, 0, 'released'))
        self.assertEqual(self.settled(), 6 * M)  # the excess never enters R or the settled totals
        self.assertEqual(self.credit(), 0)  # but headroom reads zero until an operator resolves it

    def test_an_estimate_is_labelled_and_never_releases_more_than_the_padding(self):
        self.funded('m1')
        self.funded('e1')
        measured = self.call(complete('m1', 'c1', usage=usage(3 * M)))['settlement']
        estimated = self.call(complete('e1', 'c2', usage=usage(3 * M, source='estimate')))['settlement']
        self.assertEqual((measured['charged'], measured['released'], measured['cost_source']),
                         (3 * M, 3 * M, 'provider_receipt'))
        # The admitted estimate was $5 padded to $6: an estimate below it releases only the $1 padding.
        self.assertEqual((estimated['charged'], estimated['released'], estimated['cost_source']),
                         (5 * M, M, 'estimate'))
        high = 'e2'
        self.funded(high)
        self.assertEqual(self.call(complete(high, 'c3', usage=usage(11 * M // 2, source='estimate')))['settlement']
                         ['released'], M // 2)

    def test_an_unknown_cost_keeps_the_exposure_until_a_cost_is_known(self):
        self.funded('a1')
        held = budget.Ledger.held(self.snapshot())
        bare = self.call(complete('a1', 'c1'))
        self.assertEqual((bare['outcome'], bare['settlement']['cost']), ('cost_unknown', None))
        partial = dict(usage(M), requests=usage(M)['requests'] + [dict(usage(1)['requests'][0], cost_usd_micros=None)])
        self.assertEqual(self.call(complete('a1', 'c2', usage=partial))['outcome'], 'cost_unknown')
        hold = self.hold('a1')
        self.assertEqual((hold['closed'], hold['state'], hold['amount']), (False, 'cost_unknown', 6 * M))
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)
        self.assertEqual(self.call(dict(v=1, op='check', attempt_id='a1', request=facts(**API)))['state'], 'blocked')
        self.assertEqual(self.call(complete('a1', 'c3', usage=usage(2 * M)))['settlement']['released'], 4 * M)
        self.assertEqual(self.settled(), 2 * M)

    def test_completion_needs_dispatch_and_an_uncertain_hold_needs_reconciliation(self):
        self.assertEqual(self.admit('a1')['state'], 'admitted')
        self.assertEqual(self.call(complete('a1', 'c1', usage=usage(M))),
                         dict(v=1, state='blocked', reason='hold_state_forbidden'))
        self.send('a1')
        self.call(record('a1', 'uncertain'))
        self.assertEqual(self.call(complete('a1', 'c2', usage=usage(M))),
                         dict(v=1, state='blocked', reason='reconciliation_required'))
        self.assertEqual((self.hold('a1')['state'], self.hold('a1')['amount']), ('uncertain', 6 * M))

    def test_a_subscription_completion_releases_its_window_and_records_units_separately(self):
        self.admit_sub('s1')
        self.send('s1', sub=True)
        result = self.call(complete('s1', 'c1', usage=usage(7 * M, source='host_message_totals',
                                                            units={'win:sub/5h': 3})))
        self.assertEqual(result['settlement'], dict(kind='window', released={'win:sub/5h': 12},
                                                    observed_units={'win:sub/5h': 3}))
        self.assertTrue(self.hold('s1')['closed'])
        self.assertEqual(self.settled(), 0)  # a subscription token price is never charged as dollars
        body = [b for _, b in self.events() if b['attempt_id'] == 's1'][0]
        self.assertEqual(body['usage']['units'], {'win:sub/5h': 3})
        self.assertEqual(body['usage']['tokens'], TOKENS)

    def observed(self, event_id, attempt, receipt, cost=4 * M, anchor=None, **extra):
        return self.observe_usage(event_id, attempt, anchor, usage=usage(cost, receipts=[receipt]), **extra)

    def test_an_observed_receipt_never_reduces_a_charge(self):
        """Review B1 probe (a): report-only usage seen first must not cancel the measured dollar charge."""
        self.funded('a1', root='root')
        self.assertEqual(self.observed('o1', 'coord', 'r1', anchor='a1')['state'], 'recorded')
        result = self.call(complete('a1', 'c1', usage=usage(4 * M, receipts=['r1'])))
        self.assertEqual((result['settlement']['charged'], result['settlement']['released']), (4 * M, 2 * M))
        self.assertEqual(self.settled(), 4 * M)

    def test_sibling_attempts_reporting_the_same_receipt_are_each_charged(self):
        """Review B1 probe (b): per-session message ids repeat across siblings; each attempt pays its own cost."""
        self.funded('s1', root='root')
        self.funded('s2', root='root')
        for attempt, event_id in (('s1', 'e1'), ('s2', 'e2')):
            result = self.call(complete(attempt, event_id, usage=usage(4 * M, receipts=['msg-1'])))
            self.assertEqual(result['settlement']['charged'], 4 * M)
        self.assertEqual(self.settled(), 8 * M)
        report = self.call(settle('root', workflow=where('root')))
        self.assertEqual(report['cost_to_date']['usd_micros'], 8 * M)  # round 2 S-A: report dedup is per attempt

    def test_a_receipt_repeated_with_different_costs_is_charged_at_the_higher(self):
        self.funded('a1', root='root')
        result = self.call(complete('a1', 'c1', usage=usage(M, 3 * M, receipts=['r1', 'r1'])))
        self.assertEqual(result['settlement']['charged'], 3 * M)

    def test_one_attempt_repeating_a_receipt_is_charged_once(self):
        self.funded('a1', root='root')
        result = self.call(complete('a1', 'c1', usage=usage(2 * M, 2 * M, M, receipts=['r1', 'r1', 'r2'])))
        self.assertEqual(result['settlement']['charged'], 3 * M)
        self.assertEqual(self.settled(), 3 * M)

    def test_a_replayed_observed_event_is_reauthorized_before_deduplication(self):
        """Round 3: a replay with a wrong token or a closed anchor gets that answer, never `duplicate: true`."""
        self.funded('a1', root='root')
        self.assertEqual(self.observed('o1', 'coord', 'r1', anchor='a1')['state'], 'recorded')
        before = self.ledger_bytes()
        self.assertEqual(self.observed('o1', 'coord', 'r1', anchor='a1', token='x' * 43),
                         dict(v=1, state='blocked', reason='authority_invalid'))
        self.assertEqual(self.observed('o1', 'coord', 'r1', anchor='a1')['duplicate'], True)
        self.assertEqual(self.ledger_bytes(), before)  # neither the refusal nor the duplicate changed anything
        self.call(complete('a1', 'c1', usage=usage(M)))
        self.assertEqual(self.observed('o1', 'coord', 'r1', anchor='a1'),
                         dict(v=1, state='blocked', reason='attempt_closed'))

    def test_observed_usage_must_join_an_open_hold_with_its_authority(self):
        """Review S4 and round-2 S-A: `observed` joins only an open hold whose dispatch authority it carries."""
        self.funded('a1', root='root')
        self.funded('victim', root='root', session='session-2')
        self.call(complete('victim', 'v1', usage=usage(4 * M, receipts=['r1'])))  # closed now
        cases = [(self.observed('o1', 'coord', 'r1', anchor='nope', token='x' * 43), 'unknown_attempt'),
                 (self.observed('o2', 'coord', 'r1', anchor='victim', cost=999 * M), 'attempt_closed'),
                 (self.observed('o3', 'coord', 'r1', anchor='a1', token='x' * 43), 'authority_invalid'),
                 (self.observed('o4', 'coord', 'r1', anchor='a1', token=self.tokens['victim']), 'authority_invalid')]
        for result, reason in cases:
            self.assertEqual(result, dict(v=1, state='blocked', reason=reason))
        self.assertEqual(self.observed('o5', 'a1', 'r1')['state'], 'recorded')  # its own open hold and token
        self.assertEqual(self.observed('o6', 'coord', 'r1', anchor='a1')['state'], 'recorded')
        tokenless = dict(event_id='o7', attempt_id='coord', kind='observed', stage='coordinator', usage=usage(1),
                         root_attempt_id='a1')
        self.assertEqual(self.call(dict(v=1, op='record', event=tokenless)),
                         dict(v=1, state='blocked', reason='invalid_request'))
        victim = self.call(settle('root', workflow=where('root', 'session-2')))
        self.assertEqual(victim['cost_to_date']['usd_micros'], 4 * M)
        stored = json.dumps(self.events())
        self.assertNotIn(self.tokens['a1'], stored)  # the token is never stored


class OverrunTests(fixture.Accounting):
    """Round-2 S-B: an overrun charges the reservation (certain), keeps the excess `unconfirmed` outside every
    total, reads the organization's headroom as zero and blocks it until an operator confirms or dismisses."""
    BALANCE = 8 * M

    def setUp(self):
        super().setUp()
        with self.open_ledger() as ledger:
            ledger.enable_organization('other', self.POLICY, 0)
            ledger.observe('other', 100 * M, now(), issued=100 * M)

    def resolve(self, attempt, decision, event_id='r1'):
        return self.call(dict(v=1, op='resolve_overrun', attempt_id=attempt, decision=decision, event_id=event_id))

    def other_admits(self, attempt):
        obs = api_observations(organization='other')
        obs['routes'][-1]['account'] = 'acct-' + attempt
        return self.call(prepare(attempt, observations=obs, constraints=dict(allowed_strategy_ids=['s-api'])))

    def test_an_inflated_report_cannot_inflate_the_books(self):
        self.funded('a1')
        result = self.call(complete('a1', 'c1', usage=usage(budget.MAX_INTEGER)))
        self.assertEqual((result['outcome'], result['settlement']['charged'], result['settlement']['overrun']),
                         ('overrun', 6 * M, budget.MAX_INTEGER - 6 * M))
        data = self.snapshot()
        self.assertEqual(data['organizations']['personal']['settled']['total'], 6 * M)
        self.assertEqual(budget.Ledger._total(data, 'personal'), 6 * M)
        self.assertEqual(budget.Ledger._credit_raw(data, 'personal'), 2 * M)
        self.assertEqual(budget.Ledger._headroom(data, 'personal'), 0)
        self.assertEqual(sorted(budget.Ledger.block_reasons(data, 'personal')), ['overrun'])
        self.assertEqual(self.admit('a2')['state'], 'pending')
        self.assertEqual(budget.Ledger.block_reasons(data, 'other'), set())
        self.assertEqual(self.other_admits('o1')['state'], 'admitted')

    def test_dismissing_drops_the_excess_and_unblocks(self):
        self.funded('a1')
        self.call(complete('a1', 'c1', usage=usage(budget.MAX_INTEGER)))
        result = self.resolve('a1', 'dismiss')
        self.assertEqual((result['state'], result['outcome']), ('recorded', 'overrun_dismissed'))
        data = self.snapshot()
        self.assertEqual(self.reasons(), [])
        self.assertFalse(self.admission_refused())
        self.assertEqual(budget.Ledger._total(data, 'personal'), 6 * M)
        self.assertEqual(self.credit(), 2 * M)
        self.assertEqual(self.resolve('a1', 'dismiss'), dict(result, duplicate=True))
        self.assertEqual(self.resolve('a1', 'confirm', 'r2'),
                         dict(v=1, state='blocked', reason='overrun_not_pending'))

    def test_confirming_charges_the_excess(self):
        self.funded('a1')
        self.call(complete('a1', 'c1', usage=usage(7 * M)))
        result = self.resolve('a1', 'confirm')
        self.assertEqual((result['outcome'], result['settlement']['charged']), ('overrun_confirmed', 7 * M))
        data = self.snapshot()
        self.assertEqual(data['organizations']['personal']['settled']['total'], 7 * M)
        self.assertEqual(self.reasons(), [])
        self.assertEqual(self.credit(), M)
        s = self.hold('a1')['settlement']
        self.assertEqual(s['charged'] - s['overrun'] + s['released'], s['reserved'])

    def test_the_organization_stays_blocked_while_another_overrun_is_pending(self):
        self.funded('a1', demand=M)
        self.funded('a2', demand=M)
        self.call(complete('a1', 'c1', usage=usage(2 * M)))
        self.call(complete('a2', 'c2', usage=usage(2 * M)))
        self.resolve('a1', 'dismiss')
        self.assertEqual(self.reasons(), ['overrun'])
        self.assertTrue(self.admission_refused())
        self.resolve('a2', 'dismiss', 'r2')
        self.assertEqual(self.reasons(), [])
        self.assertFalse(self.admission_refused())

    def test_a_block_the_overrun_did_not_set_is_never_cleared(self):
        self.funded('a1')
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data['organizations']['personal']['legacy_block'] = True  # a block from before v4
        self.call(complete('a1', 'c1', usage=usage(7 * M)))
        self.resolve('a1', 'dismiss')
        self.assertEqual(self.reasons(), ['legacy'])
        self.assertTrue(self.admission_refused())

    def test_only_a_pending_overrun_is_resolved_and_only_by_the_operator_op(self):
        self.funded('a1')
        self.assertEqual(self.resolve('a1', 'dismiss'), dict(v=1, state='blocked', reason='overrun_not_pending'))
        self.assertEqual(self.resolve('nope', 'dismiss'), dict(v=1, state='blocked', reason='unknown_attempt'))
        self.assertEqual(self.resolve('a1', 'maybe'), dict(v=1, state='blocked', reason='invalid_request'))
        spoofed = record('a1', 'resolve_overrun', 'x1', decision='dismiss')
        self.assertEqual(self.call(spoofed), dict(v=1, state='blocked', reason='invalid_request'))

    def test_concurrent_operators_apply_one_resolution(self):
        self.funded('a1')
        self.call(complete('a1', 'c1', usage=usage(7 * M)))
        self.service.close()
        requests = [dict(v=1, op='resolve_overrun', attempt_id='a1', decision=('confirm', 'dismiss')[i % 2],
                         event_id='r%d' % i) for i in range(8)]
        results = self.collect([self.spawn('resolve_overrun', request) for request in requests])
        effects = [r for r in results if r['state'] == 'recorded']
        self.assertEqual(len(effects), 1, results)
        self.assertTrue(all(r == dict(v=1, state='blocked', reason='overrun_not_pending') for r in results
                            if r not in effects))
        total = self.snapshot()['organizations']['personal']['settled']['total']
        self.assertEqual(total, 7 * M if effects[0]['outcome'] == 'overrun_confirmed' else 6 * M)


class ResolveCostUnknownTests(fixture.Accounting):
    """Review S2 and round-2 S-C: an operator settles a `cost_unknown` hold at the full reservation, releasing
    padding only for a receipt-backed cost."""
    def resolve(self, attempt, cost, event_id='r1', **extra):
        return self.call(dict(v=1, op='resolve_cost_unknown', attempt_id=attempt, cost_usd_micros=cost,
                              event_id=event_id, **extra))

    def unknown(self, attempt):
        self.funded(attempt)
        self.call(complete(attempt, 'c-' + attempt))
        self.assertEqual(self.hold(attempt)['state'], 'cost_unknown')

    def test_without_receipts_the_full_reservation_is_charged(self):
        self.unknown('a1')
        result = self.resolve('a1', 0)
        self.assertEqual({k: result['settlement'].get(k) for k in ('charged', 'released', 'cost', 'cost_source')},
                         dict(charged=6 * M, released=0, cost=0, cost_source=None))
        self.assertEqual(result['settlement']['resolution'],
                         dict(basis='reservation_floor', receipts=0, receipts_digest=None))
        self.assertEqual(self.settled(), 6 * M)

    def test_a_receipt_backed_cost_releases_the_padding(self):
        self.unknown('a1')
        result = self.resolve('a1', 2 * M, receipts=['req-2', 'req-1'], source='provider_receipt')
        self.assertEqual((result['settlement']['charged'], result['settlement']['released']), (2 * M, 4 * M))
        digest = hashlib.sha256(json.dumps(['req-1', 'req-2'], separators=(',', ':')).encode()).hexdigest()[:32]
        self.assertEqual(result['settlement']['resolution'], dict(basis='receipts', receipts=2, receipts_digest=digest))
        self.assertEqual(self.hold('a1')['settlement']['resolution'], result['settlement']['resolution'])
        self.assertEqual(self.settled(), 2 * M)

    def test_a_resolved_cost_above_the_reservation_is_an_overrun(self):
        self.unknown('a1')
        result = self.resolve('a1', 9 * M)
        self.assertEqual((result['outcome'], result['settlement']['charged'], result['settlement']['overrun']),
                         ('overrun', 6 * M, 3 * M))
        self.assertEqual(self.reasons(), ['overrun'])

    def test_resolution_and_a_late_complete_never_double_charge(self):
        self.unknown('a1')
        self.resolve('a1', 0)
        self.assertEqual(self.call(complete('a1', 'late', usage=usage(M))),
                         dict(v=1, state='blocked', reason='attempt_closed'))
        self.unknown('a2')
        self.call(complete('a2', 'known', usage=usage(M)))
        self.assertEqual(self.resolve('a2', 0, 'r2'), dict(v=1, state='blocked', reason='attempt_closed'))  # first wins
        self.assertEqual(self.settled(), 7 * M)

    def test_receipts_need_the_provider_receipt_source_and_bounds(self):
        self.unknown('a1')
        for extra in (dict(receipts=['r']), dict(source='provider_receipt'), dict(receipts=[], source='provider_receipt'),
                      dict(receipts=['r'], source='estimate'), dict(receipts=['x' * 129], source='provider_receipt'),
                      dict(receipts=['r'] * 33, source='provider_receipt')):
            with self.subTest(extra=extra):
                self.assertEqual(self.resolve('a1', M, **extra), dict(v=1, state='blocked', reason='invalid_request'))

    def test_resolving_frees_the_slot_and_settles_exactly(self):
        with self.open_ledger() as ledger:
            ledger.set_caps(2, 2)
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=2, per_account=2))))
        self.funded('a1')
        self.funded('a2')
        self.call(complete('a1', 'c1'))
        self.assertEqual(self.hold('a1')['state'], 'cost_unknown')
        self.assertEqual(self.admit('a3')['state'], 'pending')  # the unknown cost keeps its concurrency place
        before = self.credit()
        result = self.resolve('a1', 2 * M, receipts=['req-1'], source='provider_receipt')
        self.assertEqual((result['state'], result['duplicate'], result['outcome']), ('recorded', False, 'complete'))
        self.assertEqual({k: result['settlement'].get(k) for k in ('charged', 'released', 'cost_source')},
                         dict(charged=2 * M, released=4 * M, cost_source=None))
        self.assertEqual(self.credit(), before + 4 * M)
        self.assertEqual(self.resolve('a1', 2 * M, receipts=['req-1'], source='provider_receipt'),
                         dict(result, duplicate=True))
        self.assertEqual(self.resolve('a1', 3 * M), dict(v=1, state='blocked', reason='event_conflict'))
        self.assertEqual(self.settled(), 2 * M)
        self.assertEqual(self.admit('a3')['state'], 'admitted')
        high = self.resolve('a2', 7 * M, 'r2')  # a2 is still dispatched, not cost_unknown
        self.assertEqual(high, dict(v=1, state='blocked', reason='hold_state_forbidden'))

    def test_only_a_cost_unknown_hold_is_resolved(self):
        self.funded('a1')
        self.assertEqual(self.resolve('a1', M), dict(v=1, state='blocked', reason='hold_state_forbidden'))
        self.assertEqual(self.resolve('nope', M), dict(v=1, state='blocked', reason='unknown_attempt'))
        spoofed = record('a1', 'resolve_cost_unknown', 'x1', cost_usd_micros=0)  # never a client lifecycle event
        self.assertEqual(self.call(spoofed), dict(v=1, state='blocked', reason='invalid_request'))
        for bad in (-1, 1.5, True, None, budget.MAX_INTEGER + 1, '5'):
            with self.subTest(bad=bad):
                self.assertEqual(self.resolve('a1', bad), dict(v=1, state='blocked', reason='invalid_request'))


if __name__ == '__main__':
    unittest.main()
