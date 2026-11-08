"""S3.6a: lifecycle event deduplication, completion settlement, root-task settlement and retention.

Contract: docs/superpowers/plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention
"""
from contextlib import closing
import json
from pathlib import Path
import random
import socket
import sqlite3
import subprocess
import unittest
from unittest import mock
import urllib.request

from runtime import routing, routing_gates, usage_budget as budget
from scripts import routing as command
from tests.test_routing_command import API, M, POLICY, SECRET, api_observations, facts, now, observations, prepare
from tests.test_routing_dispatch import DispatchBase, dispatch, record
from tests.test_routing_gates import FakeWorkflow, entry

DAY = 86400
TOKENS = dict(input=10, output=5, reasoning=1, cache_read=2, cache_write=0)


def usage(*costs, source='provider_receipt', receipts=None, units=None, kind='primary'):
    requests = []
    for i, cost in enumerate(costs):
        item = dict(kind=kind, model='m', variant='high', tokens=dict(TOKENS), cost_usd_micros=cost)
        if receipts:
            item['receipt'] = receipts[i]
        if units:
            item['units'] = dict(units)
        requests.append(item)
    return dict(source=source, requests=requests)


def complete(attempt, event_id=None, **extra):
    return record(attempt, 'complete', event_id, **extra)


def settle(root, **extra):
    request = dict(v=1, op='settle', root_task_id=root)
    request.update(extra)
    return request


def where(root, session='session-1'):
    """A root task's locator; roots are keyed by all four fields (review S4)."""
    return dict(client='codex', cwd='/checkout', session_id=session, task_id=root)


def bound(root, session='session-1'):
    return dict(where(root, session), job_id='job-x')


class Reader(FakeWorkflow):
    """Canonical workflow stand-in: one ready job binds every attempt, and acceptances come from here only."""
    def __init__(self, path, accepted=()):
        super().__init__(path)
        self.write({'job-x': entry()})
        self.accepted = list(accepted)
        self.calls = []

    def acceptances(self, locator):
        self.calls.append(dict(locator))
        if self.accepted is None:
            raise RuntimeError('canonical state unreadable')
        return list(self.accepted)


class Accounting(DispatchBase):
    """A dollar organization at `BALANCE` with no protected share, API Usage on and room for many attempts."""
    BALANCE = 1000 * M
    POLICY = dict(POLICY, allowance_bp=budget.BASIS_POINTS)

    def setUp(self):
        super().setUp()
        self.reader = Reader(Path(self.temp.name) / 'workflow.json')
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, workflow=self.reader)
        self.call(dict(v=1, op='set', expected_revision=0,
                       change=dict(api_usage_enabled=True, caps=dict(total=16, per_account=16))))
        budget.initialize(self.ledger)
        with self.open_ledger() as ledger:
            ledger.set_caps(16, 16)
            ledger.enable_organization('personal', self.POLICY, 0)
            ledger.observe('personal', self.BALANCE, now(), issued=self.BALANCE)

    def admit(self, attempt, demand=5 * M, root=None, session='session-1'):
        obs = api_observations()
        obs['routes'][-1].update(demand={'credit': demand}, account='acct-' + attempt)
        obs['pools']['api']['windows'][0]['limit'] = 10**12
        request = prepare(attempt, observations=obs, constraints=dict(allowed_strategy_ids=['s-api']))
        if root is not None:
            request.update(workflow=bound(root, session))
        return self.call(request)

    def admit_sub(self, attempt, root=None, at=None):
        request = prepare(attempt, observations=observations(at=at, account='acct-' + attempt),
                          constraints=dict(allowed_strategy_ids=['s-sub']))
        if root is not None:
            request.update(workflow=bound(root))
        response = self.call(request)
        self.assertEqual(response['state'], 'admitted', response)
        return response

    def send(self, attempt, sub=False):
        response = self.call(dispatch(attempt, request=facts() if sub else facts(**API)))
        self.assertEqual(response['state'], 'dispatched', response)
        return response

    def funded(self, attempt, demand=5 * M, root=None, session='session-1'):
        response = self.admit(attempt, demand, root, session)
        self.assertEqual(response['state'], 'admitted', response)
        self.send(attempt)

    def credit(self):
        with self.open_ledger() as ledger:
            return ledger.credit_headroom('personal')

    def hold(self, attempt):
        return self.snapshot()['holds'][attempt]

    def settled(self):
        return budget.Ledger._total(self.snapshot(), 'personal') - sum(
            hold['amount'] for hold in budget.Ledger.open_holds(self.snapshot()).values())

    def events(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            return [(row[0], json.loads(row[1])) for row in db.execute('SELECT event_id, body FROM events')]


class EventTests(Accounting):
    def test_a_duplicate_event_returns_its_original_outcome_and_changes_nothing(self):
        self.funded('a1')
        first = self.call(complete('a1', 'c1', usage=usage(4 * M)))
        self.assertEqual((first['state'], first['duplicate'], first['outcome']), ('recorded', False, 'complete'))
        before = self.snapshot()
        again = self.call(complete('a1', 'c1', usage=usage(4 * M)))
        self.assertEqual(again, dict(first, duplicate=True))
        self.assertEqual(self.snapshot(), before)
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


class SpecExampleTests(Accounting):
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


class SettlementTests(Accounting):
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

    def test_an_overrun_is_counted_never_dropped_and_never_topped_up(self):
        self.funded('a1')
        result = self.call(complete('a1', 'c1', usage=usage(10 * M)))
        self.assertEqual(result['outcome'], 'overrun')
        self.assertEqual({k: result['settlement'][k] for k in ('reserved', 'charged', 'released', 'overrun')},
                         dict(reserved=6 * M, charged=10 * M, released=0, overrun=4 * M))
        hold = self.hold('a1')
        self.assertEqual((hold['closed'], hold['amount'], hold['state']), (True, 0, 'released'))
        self.assertEqual(self.settled(), 10 * M)
        self.assertEqual(self.credit(), self.BALANCE - 10 * M)

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

    def observed(self, event_id, attempt, receipt, cost=4 * M, **extra):
        event = dict(event_id=event_id, attempt_id=attempt, kind='observed', stage='coordinator', funding='api',
                     usage=usage(cost, receipts=[receipt]))
        event.update(extra)
        return self.call(dict(v=1, op='record', event=event))

    def test_an_observed_receipt_never_reduces_a_charge(self):
        """Review B1 probe (a): report-only usage seen first must not cancel the measured dollar charge."""
        self.funded('a1', root='root')
        self.assertEqual(self.observed('o1', 'coord', 'r1', root_attempt_id='a1')['state'], 'recorded')
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

    def test_one_attempt_repeating_a_receipt_is_charged_once(self):
        self.funded('a1', root='root')
        result = self.call(complete('a1', 'c1', usage=usage(2 * M, 2 * M, M, receipts=['r1', 'r1', 'r2'])))
        self.assertEqual(result['settlement']['charged'], 3 * M)
        self.assertEqual(self.settled(), 3 * M)

    def test_observed_usage_must_join_a_known_attempt(self):
        """Review S4: `observed` cannot be aimed at an arbitrary root."""
        self.funded('a1', root='root')
        self.assertEqual(self.observed('o1', 'coord', 'r1'), dict(v=1, state='blocked', reason='unknown_attempt'))
        self.assertEqual(self.observed('o2', 'coord', 'r1', root_attempt_id='nope'),
                         dict(v=1, state='blocked', reason='unknown_attempt'))
        self.assertEqual(self.observed('o3', 'a1', 'r1')['state'], 'recorded')  # an attempt with a hold
        self.assertEqual(self.call(dict(v=1, op='record', event=dict(
            event_id='o4', attempt_id='coord', kind='observed', stage='coordinator', usage=usage(1),
            root_task_id='root'))), dict(v=1, state='blocked', reason='invalid_request'))


class OverrunTests(Accounting):
    """Review S1: an overrun means the pre-send guard failed; it is counted in full and blocks the organization,
    exactly as S1's run path (`complete` above the bound) does."""
    BALANCE = 8 * M

    def test_an_overrun_counts_the_charge_and_blocks_only_its_organization(self):
        with self.open_ledger() as ledger:
            ledger.enable_organization('other', self.POLICY, 0)
            ledger.observe('other', 100 * M, now(), issued=100 * M)
        self.funded('a1')
        result = self.call(complete('a1', 'c1', usage=usage(10 * M)))
        self.assertEqual((result['outcome'], result['settlement']['overrun']), ('overrun', 4 * M))
        data = self.snapshot()
        self.assertTrue(data['organizations']['personal']['blocked'])
        self.assertEqual(budget.Ledger._credit_raw(data, 'personal'), -2 * M)
        self.assertEqual(self.admit('a2')['state'], 'pending')
        self.assertFalse(data['organizations']['other']['blocked'])
        obs = api_observations(organization='other')
        obs['routes'][-1]['account'] = 'acct-o'
        other = self.call(prepare('o1', observations=obs, constraints=dict(allowed_strategy_ids=['s-api'])))
        self.assertEqual(other['state'], 'admitted', other)


class ResolveCostUnknownTests(Accounting):
    """Review S2: an operator settles a `cost_unknown` hold at max(supplied cost, admitted estimate)."""
    def resolve(self, attempt, cost, event_id='r1'):
        return self.call(dict(v=1, op='resolve_cost_unknown', attempt_id=attempt, cost_usd_micros=cost,
                              event_id=event_id))

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
        result = self.resolve('a1', 2 * M)
        self.assertEqual((result['state'], result['duplicate'], result['outcome']), ('recorded', False, 'complete'))
        self.assertEqual({k: result['settlement'][k] for k in ('charged', 'released', 'cost_source')},
                         dict(charged=5 * M, released=M, cost_source='operator'))  # never below the estimate
        self.assertEqual(self.credit(), before + M)
        self.assertEqual(self.resolve('a1', 2 * M), dict(result, duplicate=True))
        self.assertEqual(self.resolve('a1', 3 * M), dict(v=1, state='blocked', reason='event_conflict'))
        self.assertEqual(self.settled(), 5 * M)
        self.assertEqual(self.admit('a3')['state'], 'admitted')
        high = self.resolve('a2', 7 * M, 'r2')  # a2 is still dispatched, not cost_unknown
        self.assertEqual(high, dict(v=1, state='blocked', reason='hold_state_forbidden'))

    def test_only_a_cost_unknown_hold_is_resolved(self):
        self.funded('a1')
        self.assertEqual(self.resolve('a1', M), dict(v=1, state='blocked', reason='hold_state_forbidden'))
        self.assertEqual(self.resolve('nope', M), dict(v=1, state='blocked', reason='unknown_attempt'))
        for bad in (-1, 1.5, True, None, budget.MAX_INTEGER + 1, '5'):
            with self.subTest(bad=bad):
                self.assertEqual(self.resolve('a1', bad), dict(v=1, state='blocked', reason='invalid_request'))


class VersionTests(Accounting):
    """Review S3: settled charges need version 4, so older code fails closed instead of dropping them."""
    def test_a_v3_ledger_migrates_to_v4_keeping_every_claim(self):
        self.funded('a1')
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['version'] = 3
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()
        before = self.snapshot()
        self.assertEqual(before['version'], 4)  # migrated on read
        self.call(complete('a1', 'c1', usage=usage(M)))
        with closing(sqlite3.connect(self.ledger)) as db:
            stored = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
        self.assertEqual(stored['version'], 4)
        self.assertEqual(stored['admissions'], before['admissions'])
        self.assertEqual(budget.STATE_VERSION, 4)

    def test_older_code_refuses_a_v4_ledger(self):
        self.funded('a1')
        with mock.patch.object(budget, 'STATE_VERSION', 3), self.assertRaisesRegex(budget.BudgetError,
                                                                                  'ledger_identity_changed'):
            self.snapshot()


class RootSettlementTests(Accounting):
    def settle(self, root='root', **extra):
        return self.call(settle(root, workflow=where(root), **extra))

    def test_one_accepted_at_100_and_one_rejected_at_90_cost_190_per_acceptance(self):
        self.funded('a', root='root')
        self.funded('b', root='root')
        self.call(complete('a', 'ea', usage=usage(100)))
        self.call(complete('b', 'eb', usage=usage(90)))
        self.reader.accepted = ['job-a']
        result = self.settle()
        self.assertEqual((result['state'], result['accepted']), ('settled', 1))
        self.assertEqual(result['cost_to_date']['usd_micros'], 190)
        self.assertEqual(result['ratio']['usd_micros'], 190)
        self.assertEqual(self.reader.calls, [where('root')])

    def test_the_ratio_carries_its_exact_fraction(self):
        for attempt, cost in (('a', 100), ('b', 91)):
            self.funded(attempt, root='root')
            self.call(complete(attempt, 'e' + attempt, usage=usage(cost)))
        self.reader.accepted = ['job-a', 'job-b', 'job-c']
        result = self.settle()
        self.assertEqual((result['ratio']['usd_micros'], result['ratio_num']['usd_micros'], result['ratio_den']),
                         (64, 191, 3))

    def test_roots_with_the_same_task_id_in_other_sessions_never_mix(self):
        self.funded('mine', root='root')
        self.funded('theirs', root='root', session='session-2')
        self.call(complete('mine', 'e1', usage=usage(10)))
        self.call(complete('theirs', 'e2', usage=usage(70)))
        self.assertEqual(self.settle()['cost_to_date']['usd_micros'], 10)
        other = self.call(settle('root', workflow=where('root', 'session-2')))
        self.assertEqual(other['cost_to_date']['usd_micros'], 70)

    def test_an_open_holds_root_survives_losing_its_admission_record(self):
        self.funded('a', root='root')
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['admissions'] = []
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()
        self.assertEqual(self.settle()['unresolved']['attempts'], 1)

    def test_zero_acceptances_has_no_finite_ratio(self):
        self.funded('a', root='root')
        self.call(complete('a', 'ea', usage=usage(190)))
        result = self.settle()
        self.assertEqual((result['accepted'], result['ratio'], result['cost_to_date']['usd_micros']), (0, None, 190))
        self.reader.accepted = None  # unreadable canonical state: acceptance unknown, never assumed
        unread = self.settle()
        self.assertEqual((unread['acceptance'], unread['accepted'], unread['ratio']), ('unavailable', None, None))
        self.assertEqual(unread['cost_to_date']['usd_micros'], 190)

    def test_self_reported_acceptance_never_counts(self):
        self.funded('a', root='root')
        self.assertEqual(self.call(complete('a', 'ea', usage=usage(100), accepted=True)),
                         dict(v=1, state='blocked', reason='invalid_request'))
        self.call(complete('a', 'ea', usage=usage(100), stage='review'))
        self.assertEqual(self.call(settle('root', workflow=where('root'), accepted=1)),
                         dict(v=1, state='blocked', reason='invalid_request'))
        result = self.settle()
        self.assertEqual((result['accepted'], result['ratio']), (0, None))

    def test_duplicate_child_receipts_count_once_and_every_attempt_counts(self):
        self.funded('parent', root='root')
        self.funded('child', root='root')
        self.funded('failed', root='root')
        self.assertEqual(self.admit('never', root='root')['state'], 'admitted')
        self.call(record('never', 'not_dispatched'))
        self.call(complete('child', 'e1', usage=usage(30, receipts=['child-1'])))
        self.call(complete('parent', 'e2', usage=usage(30, 50, receipts=['child-1', 'parent-1'])))
        self.call(complete('failed', 'e3', usage=usage(20), stage='worker'))
        self.call(dict(v=1, op='record', event=dict(event_id='o1', attempt_id='coordinator-1', kind='observed',
                                                   root_attempt_id='parent', stage='coordinator', funding='api',
                                                   usage=usage(5, receipts=['parent-1']))))
        result = self.settle()
        self.assertEqual(result['cost_to_date']['usd_micros'], 100)
        self.assertEqual(result['attempts'], 5)
        self.assertEqual(result['cost_to_date']['tokens']['input'], 30)  # three distinct receipts or requests
        self.assertEqual(result['by_stage']['coordinator']['usd_micros'], 0)

    def test_reviewer_rescue_is_incremental_rework(self):
        self.funded('w', root='root')
        self.funded('r', root='root')
        self.funded('x', root='root')
        self.call(complete('w', 'e1', usage=usage(40), stage='worker'))
        self.call(complete('r', 'e2', usage=usage(10), stage='review'))
        self.call(complete('x', 'e3', usage=usage(25), stage='rescue'))
        result = self.settle()
        self.assertEqual(result['rework']['usd_micros'], 25)
        self.assertEqual(result['by_stage']['review']['usd_micros'], 10)
        self.assertEqual(result['by_stage']['worker']['usd_micros'], 40)
        self.assertEqual(result['cost_to_date']['usd_micros'], 75)
        self.assertEqual(result['executor_quality'], 'not_scored')

    def test_unresolved_exposure_is_reported_separately(self):
        self.funded('done', root='root')
        self.funded('lost', root='root')
        self.funded('open', root='root')
        self.call(complete('done', 'e1', usage=usage(70)))
        self.call(record('lost', 'uncertain'))
        self.call(complete('open', 'e2'))  # unknown cost
        result = self.settle()
        self.assertEqual(result['cost_to_date']['usd_micros'], 70)
        self.assertEqual(result['unresolved'], dict(attempts=2, usd_micros_held=12 * M, units_held={},
                                                    unknown_cost_attempts=1))

    def test_settle_is_read_only_and_needs_a_matching_locator(self):
        self.funded('a', root='root')
        self.call(complete('a', 'e1', usage=usage(10)))
        with closing(sqlite3.connect(self.ledger)) as db:
            before = db.execute('SELECT body FROM ledger').fetchone()[0]
            rows = db.execute('SELECT * FROM roots').fetchall()
        self.settle()
        self.assertEqual(self.call(settle('root', workflow=where('other'))),
                         dict(v=1, state='blocked', reason='invalid_request'))
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute('SELECT body FROM ledger').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT * FROM roots').fetchall(), rows)


class CanonicalAcceptanceTests(unittest.TestCase):
    """Acceptance comes only from the canonical delegation state (runtime/delegation.py), read-only."""
    def setUp(self):
        from tests.test_routing_gates import CanonicalWorkflowTests
        self.case = CanonicalWorkflowTests(methodName='test_ready_job_dispatches_and_unready_dependent_waits')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_an_accepted_job_is_counted_only_after_canonical_reconciliation(self):
        locator = dict(self.case.where('a'))
        del locator['job_id']
        self.assertEqual(self.case.reader.acceptances(locator), [])
        files = self.case.state_files()
        child = self.case.native('a', 'a-done')
        self.case.settle(child, 'a-done')
        self.assertEqual(self.case.reader.acceptances(locator), ['a'])
        files = self.case.state_files()
        self.case.reader.acceptances(locator)
        self.assertEqual(self.case.state_files(), files)


class RetentionTests(Accounting):
    def at(self, moment):
        return mock.patch.object(command.Service, 'clock', staticmethod(lambda: moment))

    def expire(self, moment):
        with self.open_ledger() as ledger:
            return ledger.expire_evidence(moment)

    def build(self, start):
        with self.at(start):
            self.admit_sub('closed', root='root', at=start)
            self.call(record('closed', 'dispatched'))
            self.call(complete('closed', 'c1', usage=usage(1, units={'win:sub/5h': 2})))
            self.admit_sub('lost', root='root', at=start)
            self.call(record('lost', 'dispatched'))
            self.call(record('lost', 'uncertain'))
            self.admit_sub('waiting', root='other', at=start)
            self.admit_sub('finished', root='done', at=start)
            self.call(record('finished', 'dispatched'))
            self.call(complete('finished', 'c2'))

    def test_expiry_removes_diagnostics_and_keeps_safety_records(self):
        start = now()
        self.build(start)
        early = self.expire(start + DAY + 1)  # only the closed attempts' snapshot detail is due
        self.assertEqual(early['removed']['snapshots'], 2)
        self.assertNotIn('pressure', budget.Ledger.admission(self.snapshot(), 'closed'))
        self.assertIn('pressure', budget.Ledger.admission(self.snapshot(), 'waiting'))
        late = self.expire(start + 15 * DAY)
        self.assertEqual(late['removed']['events'], 4)  # the closed attempts' dispatched and complete events
        self.assertEqual(late['removed']['admissions'], 2)
        safety = late['retained_safety_records']
        self.assertEqual((safety['open_holds'], safety['uncertain_holds']), (2, 1))
        self.assertGreaterEqual(safety['idempotency_events'], 2)  # lost: dispatched + uncertain
        self.assertEqual(safety['seen_attempts'], 4)
        data = self.snapshot()
        self.assertEqual(set(budget.Ledger.open_holds(data)), {'lost', 'waiting'})
        self.assertIsNotNone(budget.Ledger.admission(data, 'lost'))
        again = self.expire(start + 15 * DAY)
        self.assertEqual(sum(again['removed'].values()), 0)  # idempotent
        self.assertEqual(self.snapshot(), data)
        self.assertEqual(self.expire(start + 61 * DAY)['removed']['aggregates'], 1)  # `done`; `root` has an open hold
        self.assertEqual(set(budget.Ledger.open_holds(self.snapshot())), {'lost', 'waiting'})

    def test_every_read_filters_expired_evidence(self):
        start = now()
        self.build(start)
        with self.open_ledger() as ledger:  # nothing deleted yet: reads alone must hide expired evidence
            self.assertIsNotNone(ledger.lookup_event('c1', start + 13 * DAY))
            self.assertIsNone(ledger.lookup_event('c1', start + 15 * DAY))
            self.assertEqual(ledger.root_summary(budget.root_key(where('root')), start + 61 * DAY)['attempts'], 2)  # an open hold keeps it
            self.assertIsNone(ledger.root_summary(budget.root_key(where('done')), start + 61 * DAY))
            self.assertIsNotNone(ledger.root_summary(budget.root_key(where('done')), start + 59 * DAY))
        with self.at(start + 61 * DAY):
            self.assertEqual(self.call(complete('closed', 'c1', usage=usage(1, units={'win:sub/5h': 2}))),
                             dict(v=1, state='blocked', reason='attempt_closed'))  # never applied again

    def test_expired_admission_rows_never_steer_a_decision(self):
        start = now()
        data = self.snapshot()
        data['admissions'] = [dict(attempt_id='old%d' % i, bucket=['implementer', 'medium'], shares=dict(api=1.0),
                                   **{'class': 'api'}, at=start - 15 * DAY, settings_revision=1) for i in range(5)]
        rows = routing.decision_rows(data, 1, start)
        self.assertEqual(rows, [])
        data['admissions'][0]['at'] = start - DAY
        self.assertEqual([r['attempt_id'] for r in routing.decision_rows(data, 1, start)], ['old0'])

    def test_an_expired_snapshot_is_ignored_by_admit(self):
        observed = now() + 10
        with self.open_ledger() as ledger:
            ledger.observe('personal', self.BALANCE, observed)
        settings = dict(command.routing_settings.resolve({}), api_usage_enabled=True, revision=1)
        obs = api_observations(at=observed + DAY + 1)  # the balance snapshot is past its 24-hour retention
        with self.open_ledger() as ledger:
            result = routing.admit(dict(attempt_id='x', task='t', role='implementer', complexity_band='medium',
                                        scope='scope1'), dict(strategy_ids=['s-api'], reason_codes=[]), settings,
                                   obs, ledger=ledger)
        self.assertEqual(result['status'], 'pending')  # an expired snapshot never funds a decision

    def test_prolonged_inactivity_keeps_active_caps_and_unresolved_claims(self):
        with self.open_ledger() as ledger:
            ledger.configure('personal', dict(self.POLICY, ceiling='custom', amount=50 * M), 1)
            revision = ledger.snapshot()['organizations']['personal']['revision']
        for attempt in ('a1', 'a2'):
            request = api_observations()
            request['pools']['api']['windows'][0]['organization_revision'] = revision
            request['routes'][-1]['account'] = 'acct-' + attempt
            response = self.call(prepare(attempt, observations=request, constraints=dict(allowed_strategy_ids=['s-api'])))
            self.assertEqual(response['state'], 'admitted', response)
            self.send(attempt)
        self.call(complete('a1', 'c1', usage=usage(4 * M)))
        self.call(record('a2', 'uncertain'))
        before = self.snapshot()
        cycle_total = budget.Ledger._total(before, 'personal', cycle='c')
        self.assertEqual(cycle_total, 4 * M + 6 * M)
        months = now() + 120 * DAY
        result = self.expire(months)
        data = self.snapshot()
        self.assertEqual(budget.Ledger._total(data, 'personal', cycle='c'), cycle_total)  # never reset
        self.assertEqual(budget.Ledger._headroom(data, 'personal'), min(
            budget.Ledger._credit(data, 'personal'), 50 * M - cycle_total))
        self.assertEqual(self.hold('a2')['state'], 'uncertain')
        self.assertEqual(result['retained_safety_records']['uncertain_holds'], 1)
        self.assertEqual(data['organizations'], before['organizations'])

    def test_cleanup_runs_at_command_start_once_per_interval(self):
        start = now()
        self.build(start)
        self.service.close()
        with self.at(start + 15 * DAY), mock.patch.object(budget.Ledger, 'expire_evidence',
                                                          autospec=True, side_effect=budget.Ledger.expire_evidence) as spy:
            fresh = command.Service(self.settings, self.ledger, workflow=self.reader)
            self.addCleanup(fresh.close)
            fresh.handle(json.dumps(record('waiting', 'not_dispatched')).encode())
            fresh.handle(json.dumps(record('nothing', 'not_dispatched')).encode())
            fresh.handle(json.dumps(settle('root')).encode())  # reads never write, so never clean
        self.assertEqual(spy.call_count, 1)
        self.assertEqual([body['attempt_id'] for _, body in self.events() if body['attempt_id'] == 'closed'], [])

    def test_observability_off_exports_nothing_and_keeps_local_accounting(self):
        self.funded('a1', root='root')
        denied = mock.Mock(side_effect=AssertionError('no export or network call is allowed'))
        with mock.patch.object(socket, 'socket', denied), mock.patch.object(socket, 'create_connection', denied), \
                mock.patch.object(urllib.request, 'urlopen', denied), mock.patch.object(subprocess, 'Popen', denied):
            self.assertEqual(self.call(complete('a1', 'c1', usage=usage(2 * M)))['state'], 'recorded')
            self.assertEqual(self.call(settle('root', workflow=where('root')))['cost_to_date']['usd_micros'], 2 * M)
            with self.open_ledger() as ledger:
                ledger.expire_evidence(now() + 30 * DAY)
        self.assertEqual(self.settled(), 2 * M)
        denied.assert_not_called()


class BoundsTests(Accounting):
    def test_the_event_table_is_bounded_and_keeps_needed_idempotency_records(self):
        self.funded('open')
        self.call(record('open', 'dispatched', 'needed'))  # a duplicate: no effect, so not protected
        self.call(record('open', 'uncertain', 'needed-2'))
        with self.open_ledger() as ledger:
            with ledger.transaction() as data:
                for i in range(budget.MAX_EVENTS + 20):
                    ledger._store_event(data, 'filler-%d' % i, '0' * 64, dict(attempt_id='gone', kind='observed',
                                                                         recorded_at=now()), now(), effect=True)
            count = ledger.db.execute('SELECT count(*) FROM events').fetchone()[0]
        self.assertEqual(count, budget.MAX_EVENTS)
        with self.open_ledger() as ledger:
            self.assertIsNotNone(ledger.lookup_event('needed-2', now()))

    def test_an_open_hold_keeps_at_most_three_idempotency_records(self):
        self.funded('open', root='root')
        for i in range(5):
            self.call(complete('open', 'u%d' % i))  # repeated unknown costs: the first takes effect
            self.call(dict(v=1, op='record', event=dict(event_id='o%d' % i, attempt_id='open', kind='observed',
                                                       stage='worker', usage=usage(1))))
        with closing(sqlite3.connect(self.ledger)) as db:
            effects = db.execute("SELECT count(*) FROM events WHERE attempt_id='open' AND effect=1").fetchone()[0]
        self.assertEqual(effects, 1)
        self.assertEqual(self.call(complete('open', 'u9'))['duplicate'], True)

    def test_a_long_running_root_keeps_its_aggregate(self):
        self.funded('early', root='root')
        self.funded('late', root='root')
        self.call(complete('early', 'e1', usage=usage(10)))
        with self.open_ledger() as ledger:
            ledger.record_event(dict(event_id='e2', attempt_id='late', kind='complete',
                                     usage=usage(20)), 'd' * 64, now() + 90 * DAY)
            summary = ledger.root_summary(budget.root_key(where('root')), now() + 90 * DAY)
        self.assertEqual(summary['stages']['worker']['usd_micros'], 30)  # aged, but an open hold kept it

    def test_root_aggregates_are_bounded_per_row_and_in_count(self):
        self.funded('a', root='r' * 128)
        receipts = ['%0128d' % i for i in range(budget.MAX_USAGE_RECEIPTS)]
        for start in range(0, len(receipts), routing.MAX_USAGE_REQUESTS):
            chunk = receipts[start:start + routing.MAX_USAGE_REQUESTS]
            units = {'win:%s/%d-%d' % ('p' * 200, start, i): 1 for i in range(routing.MAX_USAGE_UNITS)}
            self.call(dict(v=1, op='record', event=dict(
                event_id='o%d' % start, attempt_id='coord', kind='observed', root_attempt_id='a',
                stage='coordinator', usage=dict(usage(*[1] * len(chunk), receipts=chunk, units=units)))))
        with closing(sqlite3.connect(self.ledger)) as db:
            sizes = [len(row[0]) for row in db.execute('SELECT body FROM roots')]
        self.assertTrue(sizes and max(sizes) <= budget.MAX_ROOT_BYTES, sizes)


class PropertyTests(Accounting):
    """Random admit/dispatch/record (with duplicates and conflicts)/complete/expire sequences keep the books."""
    def test_random_sequences_keep_the_invariants(self):
        rng = random.Random(3606)
        attempts, sent, applied, remembered, events, overrun = [], set(), {}, {}, [], False
        for step in range(160):
            choice = rng.random()
            if choice < 0.25 or not attempts:
                attempt = 'p%d' % step
                if self.admit(attempt, rng.choice([M, 2 * M, 5 * M]))['state'] == 'admitted':
                    attempts.append(attempt)
            elif choice < 0.45:
                attempt = rng.choice(attempts)
                if self.call(dispatch(attempt, request=facts(**API)))['state'] == 'dispatched':
                    sent.add(attempt)
            elif choice < 0.8:
                attempt = rng.choice(attempts)
                kind = rng.choice(['complete', 'complete', 'uncertain', 'not_dispatched', 'dispatched'])
                extra = {}
                if kind == 'complete' and rng.random() < 0.8:
                    extra['usage'] = usage(rng.choice([0, 1, M, 3 * M, 7 * M]),
                                           source=rng.choice(['provider_receipt', 'estimate', 'host_message_totals']))
                request = record(attempt, kind, 'e%d' % step, **extra)
                if events and rng.random() < 0.3:
                    request = rng.choice(events)  # an exact duplicate
                events.append(request)
                before = self.snapshot()
                result = self.call(request)
                key = request['event']['event_id']
                if key in remembered and result['state'] == 'recorded':
                    self.assertEqual(result, dict(remembered[key], duplicate=True))  # the original outcome
                    self.assertEqual(self.snapshot(), before)
                elif result['state'] == 'recorded':
                    remembered[key] = result
                    if result['duplicate']:  # a new id for an effect that already happened (S3.1): no change
                        self.assertEqual(self.snapshot(), before)
                    else:
                        self.assertNotIn(key, applied)  # no event applied twice
                        applied[key] = result
                        overrun = overrun or result.get('outcome') == 'overrun'
                else:
                    self.assertEqual(self.snapshot(), before)  # a refusal changes nothing
            else:
                with self.open_ledger() as ledger:
                    ledger.expire_evidence(now() + rng.choice([0, DAY, 20 * DAY]))
            data = self.snapshot()
            self.assertGreaterEqual(budget.Ledger._headroom(data, 'personal'), 0)
            if not overrun:
                self.assertGreaterEqual(budget.Ledger._credit_raw(data, 'personal'), 0)
            charged = sum(r['settlement']['charged'] for r in applied.values()
                          if r.get('settlement', {}).get('kind') == 'usd_micros')
            self.assertEqual(data['organizations']['personal'].get('settled', {}).get('total', 0), charged)
            for attempt, hold in data['holds'].items():
                if hold['closed'] and 'settlement' in hold:
                    s = hold['settlement']
                    self.assertEqual(s['charged'] - s['overrun'] + s['released'] + hold['amount'], s['reserved'])
        outcomes = {r.get('outcome') for r in applied.values()}
        self.assertLessEqual({'complete', 'cost_unknown'}, outcomes)  # the sequence really settled dollar holds
        self.assertTrue(any(key in remembered for key in (e['event']['event_id'] for e in events[:-1])))


if __name__ == '__main__':
    unittest.main()
