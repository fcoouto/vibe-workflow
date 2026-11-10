"""S3.6b-2: evidence-based reconciliation of uncertain dollar holds and operator recovery of admission blocks.

Contract: docs/superpowers/plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention (S3.6b-2)
"""
from contextlib import closing
import hashlib
import itertools
import json
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import time
import unittest
from unittest import mock

from runtime import ledger_versions, routing, usage_budget as budget
from scripts import routing as command
from tests.fixture_routing_accounting import Accounting, complete, usage
from tests.test_routing_command import API, M, SCRIPT, SECRET, facts, now
from tests.test_routing_dispatch import record

CHARGE = dict(kind='charge_reservation')
NO_SPEND = dict(kind='no_spend_proof', provider='openai', organization='personal', window_start=0,
                window_end=1, reported_cost_usd_micros=0, report_digest='a' * 64)


def receipts(cost, *ids):
    return dict(kind='receipts', receipts=list(ids or ['req-1']), cost_usd_micros=cost)


def reconcile(attempt, evidence, event_id='u1', **extra):
    return dict(v=1, op='reconcile_uncertain', attempt_id=attempt, evidence=evidence, event_id=event_id, **extra)


def resolve_block(organization, reason, decision='confirm', event_id='b1', **extra):
    return dict(v=1, op='resolve_block', organization=organization, reason=reason, decision=decision,
                event_id=event_id, **extra)


def digest32(ids):
    return hashlib.sha256(json.dumps(sorted(ids), separators=(',', ':')).encode()).hexdigest()[:32]


class Reconcile(Accounting):
    """Dollar organization `personal`; a funded attempt reserves 6M (5M estimate plus 20% padding)."""
    def uncertain(self, attempt, demand=5 * M):
        self.funded(attempt, demand)
        self.assertEqual(self.call(record(attempt, 'uncertain'))['state'], 'recorded')
        self.assertEqual(self.hold(attempt)['state'], 'uncertain')

    def over_bound(self, run, organization='personal', actual=2 * M):
        with self.open_ledger() as ledger, self.assertRaisesRegex(budget.BudgetError, 'cost_exceeds_bound'):
            ledger.complete(run, 'count', actual)

    def set_legacy(self, organization='personal'):
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data['organizations'][organization]['legacy_block'] = True

    def status(self):
        return self.call(dict(v=1, op='status'))['ledger']

    def marker(self, organization='personal'):
        entry = [item for item in self.status()['reconcile']['resolve_block']['legacy']['organizations']
                 if item['organization'] == organization]
        self.assertEqual(len(entry), 1, self.status()['reconcile'])
        return entry[0]['marker']

    def assert_identity(self, data=None):
        """Per hold: charged + released - overrun_confirmed + amount == reserved. Per organization: the settled
        total is exactly the sum of the charges its closed holds carry (no lost and no double charge)."""
        data = data or self.snapshot()
        for attempt, hold in data['holds'].items():
            s = hold.get('settlement')
            if s is not None:
                confirmed = s['overrun'] if s.get('overrun_state') == 'confirmed' else 0
                self.assertEqual(s['charged'] + s['released'] - confirmed + hold['amount'], s['reserved'], attempt)
        for organization, org in data['organizations'].items():
            charged = sum(h['settlement']['charged'] for h in data['holds'].values()
                          if h.get('organization') == organization and h.get('settlement'))
            self.assertEqual((org.get('settled') or {}).get('total', 0), charged, organization)


class ReconcileUncertainTests(Reconcile):
    def test_receipts_settle_like_a_measured_completion(self):
        self.uncertain('a1')
        before = self.credit()
        result = self.call(reconcile('a1', receipts(2 * M, 'req-2', 'req-1')))
        self.assertEqual((result['state'], result['duplicate'], result['outcome'], result['event_id']),
                         ('recorded', False, 'complete', 'u1'))
        s = result['settlement']
        self.assertEqual({k: s[k] for k in ('kind', 'reserved', 'charged', 'released', 'overrun', 'remaining', 'cost')},
                         dict(kind='usd_micros', reserved=6 * M, charged=2 * M, released=4 * M, overrun=0, remaining=0,
                              cost=2 * M))
        self.assertEqual(s['resolution'], dict(basis='receipts', receipts=2, receipts_digest=digest32(['req-1', 'req-2'])))
        hold = self.hold('a1')
        self.assertEqual((hold['closed'], hold['state'], hold['amount']), (True, 'released', 0))
        self.assertEqual(self.settled(), 2 * M)
        self.assertEqual(self.credit(), before + 4 * M)
        self.assertEqual(self.status()['uncertain_dollar'], dict(ids=[], more=0))
        self.assert_identity()
        # A closed attempt can never send again: the shim's every check is refused.
        checked = self.call(dict(v=1, op='check', attempt_id='a1', request=facts(**API), authority=self.tokens['a1']))
        self.assertEqual(checked, dict(v=1, state='blocked', reason='attempt_closed'))

    def test_receipt_arithmetic_is_exact_at_every_boundary(self):
        for i, cost in enumerate((1, 6 * M - 1, 6 * M, 6 * M + 1, budget.MAX_INTEGER)):
            attempt = 'b%d' % i
            with self.subTest(cost=cost):
                self.uncertain(attempt)
                s = self.call(reconcile(attempt, receipts(cost), 'u%d' % i))['settlement']
                self.assertEqual((s['charged'], s['released'], s['overrun']),
                                 (min(cost, 6 * M), max(0, 6 * M - cost), max(0, cost - 6 * M)))
                self.assert_identity()
                if cost > 6 * M:
                    self.call(dict(v=1, op='resolve_overrun', attempt_id=attempt, decision='dismiss',
                                   event_id='d%d' % i))

    def test_receipts_above_the_reservation_are_an_overrun_the_operator_resolves(self):
        self.uncertain('a1')
        result = self.call(reconcile('a1', receipts(9 * M)))
        self.assertEqual((result['outcome'], result['settlement']['charged'], result['settlement']['overrun'],
                          result['settlement']['overrun_state']), ('overrun', 6 * M, 3 * M, 'unconfirmed'))
        self.assertEqual(self.reasons(), ['overrun'])
        self.assertTrue(self.admission_refused())
        self.assertEqual(self.settled(), 6 * M)  # the unconfirmed excess stays outside every total
        confirmed = self.call(dict(v=1, op='resolve_overrun', attempt_id='a1', decision='confirm', event_id='r1'))
        self.assertEqual((confirmed['outcome'], confirmed['settlement']['charged']), ('overrun_confirmed', 9 * M))
        self.assertEqual(self.reasons(), [])
        self.assertEqual(self.settled(), 9 * M)
        self.assert_identity()

    def test_a_zero_receipt_cost_is_refused_because_it_would_release_without_spend(self):
        """Review round 1: receipts at cost 0 would release the whole reservation, i.e. the refused no-spend proof."""
        self.uncertain('a1')
        before, stored = self.snapshot(), self.ledger_bytes()
        self.assertEqual(self.call(reconcile('a1', receipts(0, 'fake'))),
                         dict(v=1, state='blocked', reason='evidence_invalid'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.call(reconcile('a1', receipts(1, 'real')))['settlement']['charged'], 1)  # not remembered

    def test_a_receipt_backed_cost_unknown_resolution_needs_a_cost_too(self):
        self.funded('a1')
        self.call(complete('a1', 'c1'))
        self.assertEqual(self.hold('a1')['state'], 'cost_unknown')
        before, stored = self.snapshot(), self.ledger_bytes()
        backed = dict(v=1, op='resolve_cost_unknown', attempt_id='a1', cost_usd_micros=0, event_id='r1',
                      receipts=['fake'], source='provider_receipt')
        self.assertEqual(self.call(backed), dict(v=1, state='blocked', reason='evidence_invalid'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        floor = dict(v=1, op='resolve_cost_unknown', attempt_id='a1', cost_usd_micros=0, event_id='r1')
        self.assertEqual(self.call(floor)['settlement']['charged'], 6 * M)  # no receipts: the reservation floor

    def test_charge_reservation_charges_the_full_reservation_and_releases_nothing(self):
        self.uncertain('a1')
        before = self.credit()
        result = self.call(reconcile('a1', CHARGE))
        s = result['settlement']
        self.assertEqual((result['outcome'], s['reserved'], s['charged'], s['released'], s['overrun'], s['cost']),
                         ('complete', 6 * M, 6 * M, 0, 0, None))
        self.assertEqual(s['resolution'], dict(basis='reservation_floor', receipts=0, receipts_digest=None))
        self.assertEqual(self.credit(), before)  # the worst case was already held: nothing is freed
        self.assertEqual(self.settled(), 6 * M)
        self.assertEqual(self.reasons(), [])
        self.assert_identity()

    def test_a_repeat_is_idempotent_and_byte_identical(self):
        for evidence in (receipts(2 * M), CHARGE):
            with self.subTest(evidence=evidence['kind']):
                attempt = 'i-' + evidence['kind']
                self.uncertain(attempt)
                first = self.call(reconcile(attempt, evidence, 'u-' + attempt))
                before, stored = self.snapshot(), self.ledger_bytes()
                self.assertEqual(self.call(reconcile(attempt, evidence, 'u-' + attempt)), dict(first, duplicate=True))
                self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_a_conflicting_repeat_or_a_second_resolution_is_refused(self):
        self.uncertain('a1')
        self.call(reconcile('a1', receipts(2 * M)))
        before, stored = self.snapshot(), self.ledger_bytes()
        for request, reason in ((reconcile('a1', receipts(3 * M)), 'event_conflict'),
                                (reconcile('a1', CHARGE), 'event_conflict'),
                                (reconcile('a1', receipts(2 * M, 'other')), 'event_conflict'),
                                (reconcile('a1', CHARGE, 'u2'), 'attempt_closed'),
                                (complete('a1', 'u1', usage=usage(M)), 'event_conflict')):
            with self.subTest(request=request):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason=reason))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.settled(), 2 * M)

    def test_only_an_open_uncertain_dollar_hold_is_reconciled(self):
        self.assertEqual(self.admit('adm')['state'], 'admitted')
        self.funded('disp')
        self.funded('unk')
        self.call(complete('unk', 'c-unk'))  # cost_unknown
        self.admit_sub('sub')
        self.send('sub', sub=True)
        self.call(record('sub', 'uncertain'))  # an uncertain subscription hold carries no dollars
        self.uncertain('done')
        self.call(reconcile('done', CHARGE, 'u-done'))
        before, stored = self.snapshot(), self.ledger_bytes()
        for attempt, reason in (('adm', 'hold_state_forbidden'), ('disp', 'hold_state_forbidden'),
                                ('unk', 'hold_state_forbidden'), ('sub', 'hold_state_forbidden'),
                                ('done', 'attempt_closed'), ('nope', 'unknown_attempt')):
            for evidence in (receipts(M), CHARGE):
                with self.subTest(attempt=attempt, evidence=evidence['kind']):
                    self.assertEqual(self.call(reconcile(attempt, evidence, 'x-' + attempt)),
                                     dict(v=1, state='blocked', reason=reason))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_no_offline_evidence_releases_a_hold_without_spend(self):
        """`no_spend_proof` is refused even when perfectly shaped: nothing offline can check an operator-typed
        usage report, the ledger stores no uncertain-mark time, and a worker may still spend after the mark."""
        self.uncertain('a1')
        dispatched = self.hold('a1')['dispatch']['at']
        perfect = dict(NO_SPEND, window_start=dispatched - 60, window_end=now() + 60,
                       observed_at=now() + 120)
        before, stored = self.snapshot(), self.ledger_bytes()
        for evidence in (NO_SPEND, perfect, dict(kind='lease_expired'), dict(kind='host_not_found'),
                         dict(kind='timeout'), dict(kind='operator_says_no_spend')):
            with self.subTest(kind=evidence['kind']):
                self.assertEqual(self.call(reconcile('a1', evidence)),
                                 dict(v=1, state='blocked', reason='evidence_unsupported'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.hold('a1')['state'], 'uncertain')

    def test_malformed_evidence_is_refused_and_changes_nothing(self):
        self.uncertain('a1')
        good = receipts(M)
        bad = [None, [], 'receipts', {}, dict(kind=7), dict(kind=None), dict(kind=''), dict(kind='x' * 257),
               dict(good, receipts=[]), dict(good, receipts=['r%d' % i for i in range(33)]),
               dict(good, receipts=[7]), dict(good, receipts=[None]), dict(good, receipts=[['r']]),
               dict(good, receipts=['x' * 129]), dict(good, receipts=['a\x00b']), dict(good, receipts=['tab\t']),
               dict(good, receipts=['']), dict(good, receipts=['r', 'r']), dict(good, receipts='r'),
               dict(good, cost_usd_micros=True), dict(good, cost_usd_micros=1.0), dict(good, cost_usd_micros=-1),
               dict(good, cost_usd_micros=budget.MAX_INTEGER + 1), dict(good, cost_usd_micros=10**30),
               dict(good, cost_usd_micros='1'), dict(good, cost_usd_micros=None),
               {k: v for k, v in good.items() if k != 'cost_usd_micros'},
               {k: v for k, v in good.items() if k != 'receipts'},
               dict(good, source='provider_receipt'), dict(good, prompt=SECRET),
               dict(CHARGE, cost_usd_micros=M), dict(CHARGE, receipts=['r'])]
        before, stored = self.snapshot(), self.ledger_bytes()
        for evidence in bad:
            with self.subTest(evidence=json.dumps(evidence)[:100]):
                self.assertEqual(self.call(reconcile('a1', evidence)),
                                 dict(v=1, state='blocked', reason='invalid_request'))
        for request in (reconcile('a1', good, 'x' * 129), reconcile('a1', good, ''), reconcile('a1', good, 7),
                        reconcile('a\x00', good), reconcile('x' * 257, good), reconcile('', good),
                        reconcile('a1', good, prompt=SECRET), reconcile('a1', good, now=now()),
                        dict(v=1, op='reconcile_uncertain', attempt_id='a1', event_id='u1')):
            with self.subTest(request=json.dumps(request)[:100]):
                self.assertEqual(self.call(request)['state'], 'blocked')
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.call(reconcile('a1', good))['state'], 'recorded')  # refusals were not remembered

    def test_a_late_complete_never_double_charges_and_never_loses_the_charge(self):
        self.uncertain('a1')
        # Before reconciliation a client's `complete` is refused (S3.6a, unchanged) and not remembered.
        self.assertEqual(self.call(complete('a1', 'late', usage=usage(3 * M))),
                         dict(v=1, state='blocked', reason='reconciliation_required'))
        self.assertEqual(self.hold('a1')['amount'], 6 * M)  # the whole exposure stays held
        self.call(reconcile('a1', receipts(3 * M)))
        self.assertEqual(self.call(complete('a1', 'late', usage=usage(3 * M))),
                         dict(v=1, state='blocked', reason='attempt_closed'))
        self.assertEqual(self.settled(), 3 * M)
        # charge_reservation first: a later real (lower) cost cannot reduce or add to the worst-case charge.
        self.uncertain('a2')
        self.call(reconcile('a2', CHARGE, 'u2'))
        self.assertEqual(self.call(complete('a2', 'late2', usage=usage(M))),
                         dict(v=1, state='blocked', reason='attempt_closed'))
        self.assertEqual(self.settled(), 9 * M)
        self.assert_identity()

    def test_fifteen_racing_processes_apply_one_reconciliation(self):
        self.uncertain('race')
        self.service.close()
        same = reconcile('race', receipts(2 * M), 'u-race')
        results = self.collect([self.spawn('reconcile_uncertain', same) for _ in range(15)])
        self.assertEqual(sorted(r['duplicate'] for r in results), [False] + [True] * 14, results)
        self.assertEqual(self.settled(), 2 * M)
        self.uncertain('race2')
        self.service.close()
        children = []
        for i in range(15):
            if i % 3 == 0:
                children.append(self.spawn('record', complete('race2', 'c%d' % i, usage=usage(M))))
            else:
                evidence = receipts(4 * M) if i % 3 == 1 else CHARGE
                children.append(self.spawn('reconcile_uncertain', reconcile('race2', evidence, 'v%d' % i)))
        results = self.collect(children)
        effects = [r for r in results if r['state'] == 'recorded']
        self.assertEqual(len(effects), 1, results)
        self.assertTrue(all(r['reason'] in ('attempt_closed', 'reconciliation_required') for r in results
                            if r not in effects), results)
        self.assertEqual(self.settled(), 2 * M + effects[0]['settlement']['charged'])
        self.assert_identity()

    def test_the_audit_event_is_stored_without_receipts_tokens_or_content(self):
        self.uncertain('a1')
        self.call(reconcile('a1', receipts(2 * M, 'req-secret-id-1', 'req-secret-id-2')))
        rows = dict(self.events())
        body = rows['u1']
        self.assertEqual((body['kind'], body['attempt_id']), ('reconcile_uncertain', 'a1'))
        self.assertEqual(body['outcome']['settlement']['resolution']['basis'], 'receipts')
        self.assertEqual(set(body) - set(budget.EVENT_BODY_FIELDS), set())
        stored = json.dumps(self.ledger_bytes())
        for absent in ('req-secret-id-1', 'req-secret-id-2', self.tokens['a1'], SECRET):
            self.assertNotIn(absent, stored)


class OperatorOpsAreNotLifecycleEvents(Reconcile):
    def test_record_cannot_carry_a_reconciliation_or_a_block_resolution(self):
        self.uncertain('a1')
        self.start_run('s1')
        self.over_bound('s1')
        self.set_legacy()
        before, stored = self.snapshot(), self.ledger_bytes()
        for event in (dict(event_id='x1', attempt_id='a1', kind='reconcile_uncertain', evidence=CHARGE),
                      dict(event_id='x2', attempt_id='a1', kind='reconcile_uncertain'),
                      dict(event_id='x3', attempt_id='s1', kind='resolve_block'),
                      dict(event_id='x4', attempt_id='personal', kind='resolve_block', reason='legacy',
                           decision='confirm'),
                      dict(event_id='x5', attempt_id='a1', kind='complete', evidence=CHARGE)):
            with self.subTest(event=event):
                self.assertEqual(self.call(dict(v=1, op='record', event=event))['state'], 'blocked')
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))


class ResolveBlockTests(Reconcile):
    def test_confirming_an_over_bound_call_charges_the_excess_and_closes_it(self):
        self.start_run('s1')
        self.over_bound('s1')
        self.assertEqual(budget.Ledger._total(self.snapshot(), 'personal'), M)  # the books held the maximum
        result = self.call(resolve_block('personal', 'over_bound', ids=['s1']))
        self.assertEqual({k: result[k] for k in ('state', 'duplicate', 'outcome', 'organization', 'reason',
                                                 'decision', 'charged', 'dropped', 'remaining_reasons', 'event_id')},
                         dict(state='recorded', duplicate=False, outcome='block_resolved', organization='personal',
                              reason='over_bound', decision='confirm', charged=M, dropped=0, remaining_reasons=[],
                              event_id='b1'))
        self.assertEqual(result['resolved'], dict(ids=['s1'], count=1))
        run = self.snapshot()['runs']['s1']
        call = run['calls']['count']
        self.assertEqual((call['state'], call['cost'], 'over_bound' in call, run['closed']), ('completed', 2 * M, False, True))
        self.assertEqual(budget.Ledger._total(self.snapshot(), 'personal'), 2 * M)
        self.assertEqual(self.reasons(), [])
        self.assertFalse(self.admission_refused())
        self.assertEqual(self.admit('a1')['state'], 'admitted')

    def test_dismissing_an_over_bound_call_drops_the_excess(self):
        self.start_run('s1')
        self.over_bound('s1', actual=budget.MAX_INTEGER)
        result = self.call(resolve_block('personal', 'over_bound', 'dismiss', ids=['s1']))
        self.assertEqual((result['charged'], result['dropped']), (0, budget.MAX_INTEGER - M))
        call = self.snapshot()['runs']['s1']['calls']['count']
        self.assertEqual((call['state'], call['cost']), ('completed', M))
        self.assertEqual(budget.Ledger._total(self.snapshot(), 'personal'), M)
        self.assertEqual(self.reasons(), [])

    def test_an_over_bound_resolution_changes_only_its_own_runs(self):
        self.funded('a1')
        for run in ('s1', 's2'):
            self.start_run(run)
        self.over_bound('s1')
        self.over_bound('s2')
        before = self.snapshot()
        self.call(resolve_block('personal', 'over_bound', ids=['s1']))
        after = self.snapshot()
        call = before['runs']['s1']['calls']['count']
        call.update(state='completed', cost=call.pop('over_bound'))
        before['runs']['s1']['closed'] = True
        self.assertEqual(after, before)  # no flag, no other run, hold or organization field moved
        self.assertEqual(self.reasons(), ['over_bound'])  # s2 still blocks

    def test_over_bound_ids_must_all_be_pending_over_bound_runs_of_the_organization(self):
        with self.open_ledger() as ledger:
            ledger.enable_organization('other', self.POLICY, 0)
            ledger.observe('other', self.BALANCE, now(), issued=self.BALANCE)
        for run in ('s1', 's2'):
            self.start_run(run)
        self.start_run('o1', 'other')
        self.over_bound('s1')
        self.over_bound('o1')
        self.finish_run('s2', M // 2)  # in bound: completed, nothing pending
        before, stored = self.snapshot(), self.ledger_bytes()
        for ids in (['s2'], ['nope'], ['o1'], ['s1', 's2'], ['s1', 'o1'], ['s1', 'nope']):
            with self.subTest(ids=ids):
                self.assertEqual(self.call(resolve_block('personal', 'over_bound', ids=ids, event_id='b-' + ids[-1])),
                                 dict(v=1, state='blocked', reason='block_not_pending'))
        self.assertEqual(self.call(resolve_block('ghost', 'over_bound', ids=['s1'])),
                         dict(v=1, state='blocked', reason='organization_unconfigured'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))  # all or nothing

    def test_a_reordered_retry_of_the_same_ids_is_a_duplicate(self):
        for run in ('s1', 's2'):
            self.start_run(run)  # both start while the organization is admissible
        for run in ('s1', 's2'):
            self.over_bound(run)
        first = self.call(resolve_block('personal', 'over_bound', ids=['s2', 's1']))
        self.assertEqual(first['resolved'], dict(ids=['s1', 's2'], count=2))
        stored = self.ledger_bytes()
        self.assertEqual(self.call(resolve_block('personal', 'over_bound', ids=['s1', 's2'])),
                         dict(first, duplicate=True))
        self.assertEqual(self.ledger_bytes(), stored)

    def test_a_block_resolution_is_idempotent_and_conflicts_are_refused(self):
        self.start_run('s1')
        self.over_bound('s1')
        first = self.call(resolve_block('personal', 'over_bound', ids=['s1']))
        before, stored = self.snapshot(), self.ledger_bytes()
        self.assertEqual(self.call(resolve_block('personal', 'over_bound', ids=['s1'])), dict(first, duplicate=True))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        for request, reason in ((resolve_block('personal', 'over_bound', 'dismiss', ids=['s1']), 'event_conflict'),
                                (reconcile('a1', CHARGE, 'b1'), 'event_conflict'),
                                (resolve_block('personal', 'over_bound', ids=['s1'], event_id='b2'),
                                 'block_not_pending')):
            with self.subTest(request=request):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason=reason))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_malformed_block_requests_are_refused(self):
        self.start_run('s1')
        self.over_bound('s1')
        self.set_legacy()
        marker = self.marker()
        before, stored = self.snapshot(), self.ledger_bytes()
        for request in (resolve_block('personal', 'overrun', ids=['s1']),        # resolve_overrun owns overruns
                        resolve_block('personal', 'cost_unknown', ids=['s1']),
                        resolve_block('personal', 'over_bound', 'maybe', ids=['s1']),
                        resolve_block('personal', 'over_bound'),
                        resolve_block('personal', 'over_bound', ids=[]),
                        resolve_block('personal', 'over_bound', ids='s1'),
                        resolve_block('personal', 'over_bound', ids=['r%d' % i for i in range(17)]),
                        resolve_block('personal', 'over_bound', ids=['s1', 's1']),
                        resolve_block('personal', 'over_bound', ids=[7]),
                        resolve_block('personal', 'over_bound', ids=['x' * 257]),
                        resolve_block('personal', 'over_bound', ids=['s\x001']),
                        resolve_block('personal', 'over_bound', ids=['s1'], marker=marker),
                        resolve_block('personal', 'legacy'),
                        resolve_block('personal', 'legacy', marker=True),
                        resolve_block('personal', 'legacy', marker='lb_' + 'g' * 32),
                        resolve_block('personal', 'legacy', marker=marker.upper()),
                        resolve_block('personal', 'legacy', marker=marker + '0'),
                        resolve_block('personal', 'legacy', 'dismiss', marker=marker),
                        resolve_block('personal', 'legacy', marker=marker, ids=['s1']),
                        resolve_block('perso\x00nal', 'legacy', marker=marker),
                        resolve_block('x' * 257, 'legacy', marker=marker),
                        resolve_block(7, 'legacy', marker=marker),
                        resolve_block('personal', 'legacy', marker=marker, event_id='x' * 129),
                        resolve_block('personal', 'legacy', marker=marker, prompt=SECRET),
                        dict(v=1, op='resolve_block', organization='personal', reason='legacy', marker=marker,
                             event_id='b1')):
            with self.subTest(request=json.dumps(request)[:120]):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason='invalid_request'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_the_ledger_refuses_unvalidated_block_events_itself(self):
        self.start_run('s1')
        self.over_bound('s1')
        self.set_legacy()
        marker = self.marker()
        before = self.snapshot()
        for event in (dict(event_id='z1', kind='resolve_block', organization='personal', reason='legacy',
                           decision='dismiss', marker=marker),
                      dict(event_id='z2', kind='resolve_block', organization='personal', reason='overrun',
                           decision='confirm', ids=['s1']),
                      dict(event_id='z3', kind='resolve_block', organization='personal', reason='over_bound',
                           decision='maybe', ids=['s1'])):
            with self.subTest(event=event), self.open_ledger() as ledger:
                self.assertEqual(routing.resolve_block(ledger, event, now()),
                                 dict(state='blocked', reason='invalid_request'))
        self.assertEqual(self.snapshot(), before)

    def test_fifteen_racing_operators_apply_one_block_resolution(self):
        self.start_run('s1')
        self.over_bound('s1')
        self.service.close()
        same = resolve_block('personal', 'over_bound', ids=['s1'])
        results = self.collect([self.spawn('resolve_block', same) for _ in range(15)])
        self.assertEqual(sorted(r['duplicate'] for r in results), [False] + [True] * 14, results)
        self.start_run('s2')
        self.over_bound('s2')
        self.service.close()
        distinct = [resolve_block('personal', 'over_bound', ('confirm', 'dismiss')[i % 2], ids=['s2'],
                                  event_id='c%d' % i) for i in range(15)]
        results = self.collect([self.spawn('resolve_block', request) for request in distinct])
        effects = [r for r in results if r['state'] == 'recorded']
        self.assertEqual(len(effects), 1, results)
        self.assertTrue(all(r == dict(v=1, state='blocked', reason='block_not_pending') for r in results
                            if r not in effects), results)
        cost = self.snapshot()['runs']['s2']['calls']['count']['cost']
        self.assertEqual(cost, 2 * M if effects[0]['decision'] == 'confirm' else M)


class LegacyHandshakeTests(Reconcile):
    def test_a_legacy_block_clears_only_with_the_current_status_marker(self):
        self.funded('a1')
        self.set_legacy()
        self.assertEqual(self.reasons(), ['legacy'])
        listed = self.status()['reconcile']['resolve_block']['legacy']['organizations']
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['unresolved'], dict(count=1, ids=['a1'], more=0))
        marker = listed[0]['marker']
        self.assertRegex(marker, r'^lb_[0-9a-f]{32}$')
        self.assertEqual(marker, budget.Ledger.legacy_marker(self.snapshot(), 'personal'))
        before, stored = self.snapshot(), self.ledger_bytes()
        blind = 'lb_' + '0' * 32
        self.assertEqual(self.call(resolve_block('personal', 'legacy', marker=blind)),
                         dict(v=1, state='blocked', reason='marker_stale'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        result = self.call(resolve_block('personal', 'legacy', marker=marker))
        self.assertEqual((result['outcome'], result['reason'], result['remaining_reasons']),
                         ('block_resolved', 'legacy', []))
        after = self.snapshot()
        del before['organizations']['personal']['legacy_block']
        self.assertEqual(after, before)  # only the legacy marker went
        self.assertFalse(self.admission_refused())
        self.assertEqual(self.call(resolve_block('personal', 'legacy', marker=marker, event_id='b2')),
                         dict(v=1, state='blocked', reason='block_not_pending'))

    def test_a_marker_goes_stale_when_the_listed_items_change(self):
        self.funded('a1')
        self.set_legacy()
        marker = self.marker()
        self.call(complete('a1', 'c1', usage=usage(M)))  # the listing the operator reviewed changed
        self.assertEqual(self.call(resolve_block('personal', 'legacy', marker=marker)),
                         dict(v=1, state='blocked', reason='marker_stale'))
        self.assertEqual(self.reasons(), ['legacy'])
        fresh = self.marker()
        self.assertNotEqual(fresh, marker)
        self.assertEqual(self.call(resolve_block('personal', 'legacy', marker=fresh, event_id='b2'))['state'],
                         'recorded')

    def test_markers_differ_per_organization_and_ledger(self):
        with self.open_ledger() as ledger:
            ledger.enable_organization('other', self.POLICY, 0)
        self.set_legacy()
        self.set_legacy('other')
        data = self.snapshot()
        self.assertNotEqual(budget.Ledger.legacy_marker(data, 'personal'), budget.Ledger.legacy_marker(data, 'other'))
        self.assertNotEqual(budget.Ledger.legacy_marker(data, 'personal'),
                            budget.Ledger.legacy_marker(dict(data, identity='another-ledger'), 'personal'))
        self.assertEqual(self.call(resolve_block('other', 'legacy', marker=self.marker('personal'))),
                         dict(v=1, state='blocked', reason='marker_stale'))


class DerivedUnblockTests(Reconcile):
    """B1-style: an organization is admissible only once every derived reason is gone, in any resolution order."""
    def setUp(self):
        super().setUp()
        with self.open_ledger() as ledger:
            ledger.set_caps(64, 64)

    def org(self, name):
        with self.open_ledger() as ledger:
            ledger.enable_organization(name, self.POLICY, 0)
            ledger.observe(name, self.BALANCE, now(), issued=self.BALANCE)

    def ledger_hold(self, ledger, attempt, organization):
        revision = ledger.snapshot()['organizations'][organization]['revision']
        moment = now()
        ledger.admit(attempt, lambda data: dict(record=dict(attempt_id=attempt, task='t-' + attempt, bucket=[],
                                                            shares={}), vector={'org:' + organization: M},
                                                account='acct-' + attempt, now=moment, revision=revision))
        ledger.mark_dispatched(attempt)

    def three_reasons(self, organization):
        with self.open_ledger() as ledger:
            self.ledger_hold(ledger, organization + '-h', organization)
        self.start_run(organization + '-r', organization)
        with self.open_ledger() as ledger:
            result = routing.record_event(ledger, dict(event_id=organization + '-c', attempt_id=organization + '-h',
                                                       kind='complete', usage=usage(3 * M)), now())
            self.assertEqual(result['outcome'], 'overrun')
        self.over_bound(organization + '-r', organization)
        self.set_legacy(organization)
        self.assertEqual(self.reasons(organization), ['legacy', 'over_bound', 'overrun'])

    def test_every_resolution_order_unblocks_only_at_the_last_reason(self):
        for i, order in enumerate(itertools.permutations(('overrun', 'over_bound', 'legacy'))):
            organization = 'org%d' % i
            with self.subTest(order=order):
                self.org(organization)
                self.three_reasons(organization)
                remaining = {'overrun', 'over_bound', 'legacy'}
                for step, reason in enumerate(order):
                    if reason == 'overrun':
                        request = dict(v=1, op='resolve_overrun', attempt_id=organization + '-h',
                                       decision=('confirm', 'dismiss')[i % 2], event_id='%s-%d' % (organization, step))
                    elif reason == 'over_bound':
                        request = resolve_block(organization, 'over_bound', ('confirm', 'dismiss')[i % 2],
                                                ids=[organization + '-r'], event_id='%s-%d' % (organization, step))
                    else:
                        request = resolve_block(organization, 'legacy', marker=self.marker(organization),
                                                event_id='%s-%d' % (organization, step))
                    result = self.call(request)
                    self.assertEqual(result['state'], 'recorded', result)
                    remaining.discard(reason)
                    self.assertEqual(self.reasons(organization), sorted(remaining), (order, step))
                    self.assertEqual(self.admission_refused(organization), bool(remaining), (order, step))
                self.assert_identity()

    def test_random_interleavings_keep_the_block_derived_and_the_books_exact(self):
        rng = random.Random(36062)
        orgs = ('personal', 'other', 'third')
        for organization in orgs[1:]:
            self.org(organization)
        holds = {org: [] for org in orgs}
        uncertain = {org: [] for org in orgs}
        runs = {org: [] for org in orgs}
        with self.open_ledger() as ledger:
            for org in orgs:
                for i in range(4):
                    self.ledger_hold(ledger, '%s-h%d' % (org, i), org)
                    holds[org].append('%s-h%d' % (org, i))
        for org in orgs:
            for i in range(3):
                self.start_run('%s-r%d' % (org, i), org)
                runs[org].append('%s-r%d' % (org, i))
        expected = {org: dict(overrun=set(), over_bound=set(), legacy=False) for org in orgs}
        seen, actions = set(), set()
        for step in range(220):
            org = rng.choice(orgs)
            action = rng.choice(['overrun', 'over_bound', 'legacy', 'uncertain', 'receipts', 'charge', 'confirm',
                                 'dismiss', 'block_confirm', 'block_dismiss', 'legacy_clear', 'admit'])
            with self.open_ledger() as ledger:
                if action == 'overrun' and holds[org]:
                    attempt = holds[org].pop()
                    routing.record_event(ledger, dict(event_id='e%d' % step, attempt_id=attempt, kind='complete',
                                                      usage=usage(3 * M)), now())
                    expected[org]['overrun'].add(attempt)
                elif action == 'over_bound' and runs[org]:
                    run = runs[org].pop()
                    with self.assertRaisesRegex(budget.BudgetError, 'cost_exceeds_bound'):
                        ledger.complete(run, 'count', 2 * M)
                    expected[org]['over_bound'].add(run)
                elif action == 'legacy' and not expected[org]['legacy'] and rng.random() < 0.3:
                    with ledger.transaction() as data:
                        data['organizations'][org]['legacy_block'] = True
                    expected[org]['legacy'] = True
                elif action == 'uncertain' and holds[org]:
                    attempt = holds[org].pop()
                    ledger.mark_uncertain(attempt)
                    uncertain[org].append(attempt)
                elif action in ('receipts', 'charge') and uncertain[org]:
                    attempt = uncertain[org].pop()
                    cost = rng.choice([1, M // 3, M, 2 * M])
                    event, reason = routing.reconcile_event(dict(
                        event_id='u%d' % step, attempt_id=attempt,
                        evidence=receipts(cost, 'q%d' % step) if action == 'receipts' else CHARGE))
                    self.assertIsNone(reason)
                    result = routing.record_event(ledger, event, now())
                    self.assertEqual(result['state'], 'recorded', result)
                    if result['outcome'] == 'overrun':
                        expected[org]['overrun'].add(attempt)
                elif action in ('confirm', 'dismiss') and expected[org]['overrun']:
                    attempt = sorted(expected[org]['overrun'])[0]
                    result = routing.record_event(ledger, routing.overrun_event(dict(
                        event_id='r%d' % step, attempt_id=attempt, decision=action)), now())
                    self.assertEqual(result['state'], 'recorded', result)
                    expected[org]['overrun'].discard(attempt)
                elif action in ('block_confirm', 'block_dismiss') and expected[org]['over_bound']:
                    chosen = sorted(expected[org]['over_bound'])[:rng.randint(1, 2)]
                    event, reason = routing.block_event(dict(event_id='b%d' % step, organization=org,
                                                             reason='over_bound', decision=action[6:], ids=chosen))
                    self.assertIsNone(reason)
                    self.assertEqual(routing.resolve_block(ledger, event, now())['state'], 'recorded')
                    expected[org]['over_bound'] -= set(chosen)
                elif action == 'legacy_clear' and expected[org]['legacy']:
                    # A blind marker is refused first; only the current one clears the block.
                    for marker, cleared in (('lb_' + '%032x' % step, False),
                                            (budget.Ledger.legacy_marker(ledger.snapshot(), org), True)):
                        event, reason = routing.block_event(dict(event_id='l%d-%d' % (step, cleared),
                                                                 organization=org, reason='legacy',
                                                                 decision='confirm', marker=marker))
                        self.assertIsNone(reason)
                        result = routing.resolve_block(ledger, event, now())
                        if cleared:
                            self.assertEqual(result['state'], 'recorded', result)
                        else:
                            self.assertEqual(result, dict(state='blocked', reason='marker_stale'))
                    expected[org]['legacy'] = False
                else:
                    action = 'noop' if action != 'admit' else action
            actions.add(action)
            data = self.snapshot()
            self.assert_identity(data)
            for name in orgs:
                seen |= budget.Ledger.block_reasons(data, name)
                want = {reason for reason, present in (('overrun', expected[name]['overrun']),
                                                       ('over_bound', expected[name]['over_bound']),
                                                       ('legacy', expected[name]['legacy'])) if present}
                self.assertEqual(budget.Ledger.block_reasons(data, name), want, (step, action, name))
                self.assertEqual(self.admission_refused(name), bool(want), (step, action, name))
            if action == 'admit' and not self.admission_refused(org):
                with self.open_ledger() as ledger:
                    self.ledger_hold(ledger, 'new%d' % step, org)
                    holds[org].append('new%d' % step)
        self.assertEqual(seen, {'overrun', 'over_bound', 'legacy'})
        self.assertLessEqual({'receipts', 'charge', 'block_confirm', 'block_dismiss', 'legacy_clear'}, actions)


class StatusTests(Reconcile):
    def test_status_lists_what_each_operator_op_can_resolve(self):
        with self.open_ledger() as ledger:
            ledger.set_caps(32, 32)
            ledger.enable_organization('other', self.POLICY, 0)
            ledger.observe('other', self.BALANCE, now(), issued=self.BALANCE)
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=32, per_account=32))))
        self.start_run('s1')
        self.start_run('o1', 'other')
        for i in range(18):
            self.uncertain('d%02d' % i)
        self.over_bound('s1')
        self.over_bound('o1', 'other')
        self.set_legacy('other')
        ledger = self.status()
        listed = ledger['reconcile']
        self.assertEqual(listed['reconcile_uncertain'], dict(count=18, ids=['d%02d' % i for i in range(16)], more=2))
        self.assertEqual(listed['resolve_block']['over_bound'], dict(organizations=[
            dict(organization='other', count=1, ids=['o1'], more=0),
            dict(organization='personal', count=1, ids=['s1'], more=0)], more=0))
        legacy = listed['resolve_block']['legacy']
        self.assertEqual(legacy['more'], 0)
        self.assertEqual([(e['organization'], e['unresolved']) for e in legacy['organizations']],
                         [('other', dict(count=1, ids=['o1'], more=0))])
        self.assertEqual(legacy['organizations'][0]['marker'], budget.Ledger.legacy_marker(self.snapshot(), 'other'))
        hint = ledger['reconcile_hint']
        self.assertIsInstance(hint, str)
        self.assertLessEqual(len(hint), 512)
        self.assertIn('reconcile_uncertain', hint)
        self.assertIn("operator's word", hint)  # receipts release the unspent rest on the operator's word
        self.assertNotIn('nothing releases', hint)
        self.assertIn('resolve_block', hint)
        # Everything the S3.6a status already reported is unchanged.
        self.assertEqual(ledger['uncertain_dollar'], dict(ids=['d%02d' % i for i in range(16)], more=2))
        self.assertEqual([entry['id'] for entry in ledger['blocked_organizations']], ['other', 'personal'])

    def test_a_clean_ledger_has_nothing_to_reconcile(self):
        self.funded('a1')
        ledger = self.status()
        self.assertEqual(ledger['reconcile'], dict(
            reconcile_uncertain=dict(count=0, ids=[], more=0),
            resolve_block=dict(over_bound=dict(organizations=[], more=0), legacy=dict(organizations=[], more=0)),
            reconcile_subscription=dict(count=0, ids=[], more=0), window_debits=dict(count=0, keys=[], more=0),
            drop_debit=dict(debits=[], more=0)))
        self.assertIsNone(ledger['reconcile_hint'])


class CompatibilityTests(Reconcile):
    def seed_v3(self):
        """A v3 ledger with a blocked organization and an unresolved S1 call (migrated to legacy + over_bound)."""
        self.start_run('s1')
        with self.open_ledger() as ledger:
            ledger.uncertain('s1', 'count')
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['version'] = 3
            data['organizations']['personal']['blocked'] = True
            data['runs']['s1']['calls']['count'].pop('over_bound', None)
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),))
            db.execute('DROP TABLE events')  # a ledger that never had the S3.6a tables
            db.execute('DROP TABLE roots')
            db.commit()

    def test_a_migrated_ledger_is_recovered_by_the_operator_ops(self):
        self.seed_v3()
        self.assertEqual(self.reasons(), ['legacy', 'over_bound'])
        result = self.call(resolve_block('personal', 'over_bound', ids=['s1']))
        self.assertEqual((result['state'], result['charged']), ('recorded', 0))  # migrated at its maximum
        self.assertEqual(self.reasons(), ['legacy'])
        self.assertEqual(self.call(resolve_block('personal', 'legacy', marker=self.marker(), event_id='b2'))['state'],
                         'recorded')
        self.assertEqual(self.reasons(), [])
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])['version'], 5)
        self.assertEqual(ledger_versions.STATE_VERSION, 5)

    def test_a_bare_newer_observation_retires_no_settled_charge(self):
        """Reflected-debit retirement is deferred: no observation shape carries a watermark, so a newer
        `observed_at` (even with a balance that already reflects the charge) never removes a settled charge."""
        self.funded('a1')
        self.call(complete('a1', 'c1', usage=usage(4 * M)))
        before = self.snapshot()['organizations']['personal']['settled']
        with self.open_ledger() as ledger:
            ledger.observe('personal', self.BALANCE - 4 * M, now() + 1)
        data = self.snapshot()
        self.assertEqual(data['organizations']['personal']['settled'], before)
        self.assertEqual(budget.Ledger._credit_raw(data, 'personal'), self.BALANCE - 8 * M)  # headroom errs low


class ProtocolTests(Reconcile):
    def test_oversized_and_non_json_requests_are_refused_without_echo(self):
        self.uncertain('a1')
        huge = json.dumps(reconcile('a1', receipts(M, 'x' * (1 << 20)))).encode()
        self.assertEqual(self.call(huge), dict(v=1, state='blocked', reason='request_too_large'))
        self.assertEqual(self.call(b'{"v":1,"op":"reconcile_uncertain",'), dict(v=1, state='blocked',
                                                                               reason='invalid_json'))
        self.assertEqual(self.call(json.dumps(reconcile('a1', receipts(M)))[:-1].encode() + b',"evidence":1}'),
                         dict(v=1, state='blocked', reason='invalid_json'))  # a duplicate key
        self.assertEqual(self.hold('a1')['state'], 'uncertain')

    def operator_state(self):
        """One pending item for each older operator op, and their requests (C3-0a review S1 reuses it)."""
        self.uncertain('a1')
        self.funded('a2')
        self.call(complete('a2', 'c2'))  # cost_unknown
        self.funded('a3')
        self.start_run('s1')  # every claim starts while the organization is admissible
        self.call(complete('a3', 'c3', usage=usage(9 * M)))  # overrun
        self.over_bound('s1')
        self.service.close()
        return [reconcile('a1', receipts(M)), dict(reconcile('a1', CHARGE, 'u2'), id=7),
                resolve_block('personal', 'over_bound', ids=['s1']),
                dict(v=1, op='resolve_overrun', attempt_id='a3', decision='dismiss', event_id='r1'),
                dict(v=1, op='resolve_cost_unknown', attempt_id='a2', cost_usd_micros=0, event_id='r2')]

    def test_in_process_services_refuse_every_operator_op(self):
        """C3-0a review S1 (as #212 B1 for `set managed_projects`): only `main()`'s one-shot command runs an operator
        op. Every in-process service refuses each one before reading anything, `managed_service` included."""
        operator = self.operator_state()
        others = [dict(v=1, op='reconcile_subscription', attempt_id='a1', evidence=dict(kind='abandon'),
                       event_id='o1'),
                  dict(v=1, op='drop_debit', key='win:sub/5h', marker='dd_' + '0' * 32, event_id='o2'),
                  dict(v=1, op='settle_legacy_call', run_id='s1', phase='count', evidence=CHARGE, event_id='o3'),
                  dict(v=1, op='seed_window', pool='sub', window='5h', used=0, limit=1000, length=18000,
                       reset_at=int(time.time()) + 60, event_id='o4')]
        requests = {request['op']: request for request in operator + others}
        self.assertEqual(set(requests), set(command.OPERATOR_OPS))
        before, stored = self.snapshot(), self.ledger_bytes()
        project = Path(self.temp.name).resolve() / 'checkout'
        project.mkdir()
        with mock.patch.object(command, 'default_settings', return_value=self.settings), \
                mock.patch.object(command, 'default_ledger', return_value=self.ledger):
            managed = command.managed_service(project)
        services = dict(in_process=command.Service(self.settings, self.ledger, workflow=self.reader),
                        pinned=command.Service(self.settings, self.ledger, workflow=self.reader, project='/checkout'),
                        managed_service=managed,
                        serving=command.Service(self.settings, self.ledger, serving=True, one_shot=True))
        for name, service in services.items():
            self.addCleanup(service.close)
            for op, request in requests.items():
                with self.subTest(service=name, op=op):
                    response = service.handle(json.dumps(request).encode())
                    self.assertEqual({k: v for k, v in response.items() if k != 'id'},
                                     dict(v=1, state='blocked', reason='operator_op_not_served'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        one_shot = command.Service(self.settings, self.ledger, workflow=self.reader, one_shot=True)
        self.addCleanup(one_shot.close)
        self.assertEqual(one_shot.handle(json.dumps(operator[0]).encode())['state'], 'recorded')

    def test_serve_refuses_every_operator_op_and_one_shot_still_applies_it(self):
        """Review round 1: the shim's persistent child (`--serve`) never exposes operator ops; they stay one-shot."""
        operator = self.operator_state()
        before, stored = self.snapshot(), self.ledger_bytes()
        lines = operator + [b'not json', reconcile('a\u0000', CHARGE), dict(v=1, op='status')]
        raw = b''.join((line if isinstance(line, bytes) else json.dumps(line).encode()) + b'\n' for line in lines)
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), '--serve', '--settings', str(self.settings),
                               '--ledger', str(self.ledger)], input=raw, capture_output=True, timeout=60)
        answers = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual((done.returncode, done.stderr, len(answers)), (0, b'', len(lines)))
        for answer in answers[:5]:
            self.assertEqual({k: v for k, v in answer.items() if k != 'id'},
                             dict(v=1, state='blocked', reason='operator_op_not_served'))
        self.assertEqual(answers[1]['id'], 7)
        self.assertEqual([a.get('reason') for a in answers[5:7]], ['invalid_json', 'operator_op_not_served'])
        self.assertEqual(answers[7]['ledger']['reconcile']['reconcile_uncertain']['count'], 1)
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        for op, request in (('reconcile_uncertain', operator[0]), ('resolve_block', operator[2]),
                            ('resolve_overrun', operator[3]), ('resolve_cost_unknown', operator[4])):
            with self.subTest(op=op):
                done = self.run_cli([op], request)
                self.assertEqual(json.loads(done.stdout)['state'], 'recorded', done.stdout)
        self.assertEqual(self.reasons(), [])

    def test_a_closed_stdout_exits_quietly(self):
        self.uncertain('a1')
        self.service.close()
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'reconcile_uncertain', '--settings',
                                  str(self.settings), '--ledger', str(self.ledger)], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child.stdout.close()
        child.stdout = None
        _, err = child.communicate(json.dumps(reconcile('a1', CHARGE)).encode(), timeout=60)
        self.assertEqual((child.returncode, err), (0, b''))
        self.assertEqual(self.hold('a1')['state'], 'released')  # applied before the write failed quietly


if __name__ == '__main__':
    unittest.main()
