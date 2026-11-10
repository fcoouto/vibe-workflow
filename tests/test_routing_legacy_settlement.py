"""C3-0a: ledger state v5 and its downgrade guard, `settle_legacy_call` and `status.ledger.legacy_outstanding`.

Contract: docs/superpowers/specs/2026-11-10-smart-routing-c3-wiring-design.md sections 4.4 and 4.6 (slice C3-0a).
"""
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

from runtime import ledger_versions, usage_budget as budget
from scripts import routing as command
from tests.fixture_routing_accounting import Accounting
from tests.test_routing_command import M, SCRIPT, SECRET, now

NEVER = dict(kind='never_dispatched')
RESERVATION = dict(kind='charge_reservation')
RECEIPT = 'receipt-' + SECRET


def receipts(cost, ids=(RECEIPT,)):
    return dict(kind='receipts', receipts=list(ids), cost_usd_micros=cost)


def settle_call(run, evidence, event_id='l1', phase='count', **extra):
    return dict(v=1, op='settle_legacy_call', run_id=run, phase=phase, evidence=evidence, event_id=event_id, **extra)


def blocked(reason):
    return dict(v=1, state='blocked', reason=reason)


class Legacy(Accounting):
    """S1 runs begun through `Ledger.begin` on the dollar organization `personal`, each with maximum M."""
    def setUp(self):
        super().setUp()
        # Operator ops run only in a one-shot command; only `main()` sets the flag in production.
        self.operator = command.Service(self.settings, self.ledger, workflow=self.reader, one_shot=True)
        self.addCleanup(self.operator.close)

    def op(self, request):
        return self.operator.handle(json.dumps(request).encode())

    def revision(self):
        return self.snapshot()['organizations']['personal']['revision']

    def reserved(self, run):
        with self.open_ledger() as ledger:
            ledger.begin('personal', 'task-' + run, run, 'digest', M, self.revision(), now(),
                         dict(account='na-' + run, task='task-' + run, windows=[]))

    def dispatching(self, run):
        self.start_run(run)

    def unresolved(self, run):
        self.start_run(run)
        with self.open_ledger() as ledger:
            ledger.uncertain(run, 'count', incomplete=dict(reason='crash'))

    def over_bound(self, run):
        self.start_run(run)
        with self.open_ledger() as ledger, self.assertRaisesRegex(budget.BudgetError, 'cost_exceeds_bound'):
            ledger.complete(run, 'count', 2 * M)

    def generation(self, run, dispatch=True):
        """A run whose count completed at M // 4 and whose generation call is reserved (or in flight)."""
        self.start_run(run)
        with self.open_ledger() as ledger:
            ledger.complete(run, 'count', M // 4)
            ledger.reserve_generation(run, 'digest-g', M, self.revision(), now())
            if dispatch:
                ledger.dispatch(run, 'generation', self.revision(), now())

    def call_of(self, run, phase='count'):
        return self.snapshot()['runs'][run]['calls'][phase]

    def total(self):
        return budget.Ledger._total(self.snapshot(), 'personal')

    def outstanding(self):
        return self.call(dict(v=1, op='status'))['ledger']['legacy_outstanding']


class VersionTests(Legacy):
    def stored(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            return json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])

    def rewrite(self, **change):
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data.update(change)
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()

    def test_a_new_ledger_is_version_five(self):
        self.assertEqual(ledger_versions.STATE_VERSION, 5)
        self.assertEqual(self.stored()['version'], 5)
        self.assertFalse(hasattr(budget, 'STATE_VERSION'))  # defined once, in ledger_versions

    def test_a_v4_ledger_migrates_to_v5_keeping_every_run_hold_and_debit(self):
        self.funded('a1')
        self.unresolved('s1')
        debits = {'win:sub/5h': dict(units=3, at=now())}
        self.rewrite(version=4, window_debits=debits)
        before = self.stored()
        migrated = self.snapshot()
        self.assertEqual(migrated['version'], 5)  # migrated on read
        self.assertEqual(dict(migrated, version=4), before)  # 4 -> 5 changes nothing else
        self.assertEqual(self.op(settle_call('s1', RESERVATION))['state'], 'recorded')  # the first write persists it
        stored = self.stored()
        self.assertEqual((stored['version'], stored['window_debits'], stored['holds']),
                         (5, debits, before['holds']))

    def test_v4_code_refuses_a_v5_ledger(self):
        """Code from before this slice accepted versions 1 to 4 only, so a v5 ledger is `ledger_identity_changed`:
        a downgraded package cannot ignore the window debits a v5 ledger may carry."""
        self.funded('a1')
        with mock.patch.object(ledger_versions, 'STATE_VERSION', 4), \
                self.assertRaisesRegex(budget.BudgetError, 'ledger_identity_changed'):
            self.snapshot()
        self.assertEqual(self.call(dict(v=1, op='status'))['ledger']['state'], 'ready')
        with mock.patch.object(ledger_versions, 'STATE_VERSION', 4):
            self.service.close()
            self.assertEqual(self.call(dict(v=1, op='status'))['ledger'],
                             dict(state='unavailable', reason='ledger_identity_changed'))

    def test_v5_code_refuses_a_newer_or_unknown_version(self):
        self.funded('a1')
        for version in (6, 0, '5', None, True):
            with self.subTest(version=version):
                self.rewrite(version=version)
                with self.assertRaisesRegex(budget.BudgetError, 'ledger_identity_changed'):
                    self.snapshot()


class SettleTests(Legacy):
    def test_a_reserved_call_is_released_and_its_run_closes(self):
        self.reserved('r1')
        total = self.total()
        result = self.op(settle_call('r1', NEVER))
        self.assertEqual(result, dict(v=1, state='recorded', duplicate=False, outcome='released', run_id='r1',
                                      phase='count', charged=0, released=M, remaining_reasons=[],
                                      resolution=dict(basis='never_dispatched'), event_id='l1'))
        call = self.call_of('r1')
        self.assertEqual((call['state'], call['cost']), ('rejected-before-send', 0))
        self.assertTrue(self.snapshot()['runs']['r1']['closed'])
        self.assertEqual(self.total(), total - M)
        self.assertNotIn('r1', budget.Ledger.open_attempts(self.snapshot()))  # the concurrency place is free
        with self.open_ledger() as ledger, self.assertRaisesRegex(budget.BudgetError, 'dispatch_forbidden'):
            ledger.dispatch('r1', 'count', self.revision(), now())  # a released run never sends

    def test_a_reserved_generation_keeps_the_completed_counts_charge(self):
        self.generation('g1', dispatch=False)
        result = self.op(settle_call('g1', NEVER, phase='generation'))
        self.assertEqual((result['outcome'], result['charged'], result['released']), ('released', 0, M))
        run = self.snapshot()['runs']['g1']
        self.assertEqual((run['calls']['count']['cost'], run['calls']['generation']['cost'], run['closed']),
                         (M // 4, 0, True))

    def test_receipts_charge_as_reported_and_release_the_rest(self):
        for state in ('dispatching', 'unresolved'):
            with self.subTest(state=state):
                run = 'x-' + state
                getattr(self, state)(run)
                total = self.total()
                result = self.op(settle_call(run, receipts(M // 2), 'e-' + run))
                self.assertEqual((result['state'], result['outcome'], result['charged'], result['released']),
                                 ('recorded', 'charged', M // 2, M - M // 2))
                self.assertEqual(result['resolution'], budget.Ledger._resolution([RECEIPT]))
                call = self.call_of(run)
                self.assertEqual((call['state'], call['cost']), ('completed', M // 2))
                self.assertTrue(self.snapshot()['runs'][run]['closed'])
                self.assertEqual(self.total(), total - (M - M // 2))

    def test_a_generation_in_flight_settles_on_its_own_phase(self):
        self.generation('g1')
        result = self.op(settle_call('g1', receipts(M), phase='generation'))
        self.assertEqual((result['outcome'], result['charged'], result['released']), ('charged', M, 0))
        self.assertEqual(self.call_of('g1')['cost'], M // 4)  # the completed count is untouched

    def test_charge_reservation_charges_the_maximum_and_releases_nothing(self):
        for state in ('dispatching', 'unresolved'):
            with self.subTest(state=state):
                run = 'c-' + state
                getattr(self, state)(run)
                total = self.total()
                result = self.op(settle_call(run, RESERVATION, 'e-' + run))
                self.assertEqual((result['outcome'], result['charged'], result['released'], result['resolution']),
                                 ('charged', M, 0, dict(basis='charge_reservation')))
                self.assertEqual((self.call_of(run)['state'], self.call_of(run)['cost']), ('completed', M))
                self.assertEqual(self.total(), total)  # the maximum stays in R

    def test_a_cost_above_the_maximum_becomes_over_bound_for_resolve_block(self):
        self.unresolved('s1')
        result = self.op(settle_call('s1', receipts(3 * M)))
        self.assertEqual((result['state'], result['outcome'], result['charged'], result['released'],
                          result['remaining_reasons']), ('recorded', 'over_bound', 0, 0, ['over_bound']))
        call = self.call_of('s1')
        self.assertEqual((call['state'], call['over_bound']), ('unresolved', 3 * M))
        self.assertEqual(self.reasons(), ['over_bound'])
        self.assertEqual(self.op(settle_call('s1', RESERVATION, 'l2')), blocked('block_not_pending'))
        resolved = self.op(dict(v=1, op='resolve_block', organization='personal', reason='over_bound',
                                decision='confirm', ids=['s1'], event_id='b1'))
        self.assertEqual((resolved['state'], resolved['charged']), ('recorded', 2 * M))
        self.assertEqual(self.reasons(), [])

    def test_zero_cost_is_evidence_invalid(self):
        self.unresolved('s1')
        before, stored = self.snapshot(), self.ledger_bytes()
        self.assertEqual(self.op(settle_call('s1', receipts(0))), blocked('evidence_invalid'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_every_state_and_evidence_mismatch_is_refused_and_changes_nothing(self):
        self.reserved('res')
        self.dispatching('dis')
        self.unresolved('unr')
        self.start_run('done')
        self.finish_run('done', M // 2)
        self.funded('conv')
        with self.open_ledger() as ledger:
            ledger.convert_hold('conv', 'personal', 'packet', 5 * M, self.revision(), now(),
                                dict(account='na', task=self.hold('conv')['task'], windows=[]))
        self.over_bound('ob')  # last: an over-bound call blocks the organization's admissions
        before, stored = self.snapshot(), self.ledger_bytes()
        cases = [('res', receipts(1), 'call_state_forbidden'), ('res', RESERVATION, 'call_state_forbidden'),
                 ('dis', NEVER, 'call_state_forbidden'), ('unr', NEVER, 'call_state_forbidden'),
                 ('ob', NEVER, 'block_not_pending'), ('ob', receipts(1), 'block_not_pending'),
                 ('ob', RESERVATION, 'block_not_pending'), ('done', RESERVATION, 'call_closed'),
                 ('nope', NEVER, 'unknown_call'), ('conv', RESERVATION, 'not_legacy_call')]
        for i, (run, evidence, reason) in enumerate(cases):
            with self.subTest(run=run, evidence=evidence['kind']):
                self.assertEqual(self.op(settle_call(run, evidence, 'x%d' % i)), blocked(reason))
        self.assertEqual(self.op(settle_call('dis', NEVER, 'x-g', phase='generation')), blocked('unknown_call'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))


class IdempotencyTests(Legacy):
    def test_a_repeat_is_a_byte_identical_duplicate(self):
        self.unresolved('s1')
        first = self.op(settle_call('s1', receipts(M // 2)))
        before, stored = self.snapshot(), self.ledger_bytes()
        self.assertEqual(self.op(settle_call('s1', receipts(M // 2))), dict(first, duplicate=True))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_a_conflicting_repeat_or_a_second_settlement_is_refused(self):
        self.unresolved('s1')
        self.op(settle_call('s1', receipts(M // 2)))
        before, stored = self.snapshot(), self.ledger_bytes()
        for request, reason in ((settle_call('s1', RESERVATION), 'event_conflict'),
                                (settle_call('s1', RESERVATION, 'l2'), 'call_closed'),
                                (dict(v=1, op='reconcile_uncertain', attempt_id='s1', event_id='l1',
                                      evidence=RESERVATION), 'event_conflict')):
            with self.subTest(request=request):
                self.assertEqual(self.op(request), blocked(reason))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_refusals_are_not_remembered(self):
        self.unresolved('s1')
        self.assertEqual(self.op(settle_call('s1', NEVER)), blocked('call_state_forbidden'))
        self.assertEqual(self.op(settle_call('s1', RESERVATION))['state'], 'recorded')


class MalformedTests(Legacy):
    def test_malformed_requests_are_refused_with_fixed_reasons(self):
        self.unresolved('s1')
        before, stored = self.snapshot(), self.ledger_bytes()
        unsupported = [dict(kind=kind) for kind in ('abandon', 'host_usage', 'no_spend', 'release', 'dismiss')]
        invalid = [None, [], 'never_dispatched', {}, dict(kind=7), dict(kind=''), dict(kind='x' * 257),
                   dict(NEVER, note=SECRET), dict(RESERVATION, cost_usd_micros=1), receipts(-1), receipts(1.5),
                   receipts(True), receipts(budget.MAX_INTEGER + 1), receipts(M, ids=()), receipts(M, ids=['a', 'a']),
                   receipts(M, ids=['x' * 129]), receipts(M, ids=[7]), receipts(M, ids=['r%d' % i for i in range(33)]),
                   {k: v for k, v in receipts(M).items() if k != 'receipts'},
                   {k: v for k, v in receipts(M).items() if k != 'cost_usd_micros'}]
        for evidence in unsupported:
            with self.subTest(evidence=evidence['kind']):
                self.assertEqual(self.op(settle_call('s1', evidence)), blocked('evidence_unsupported'))
        for evidence in invalid:
            with self.subTest(evidence=json.dumps(evidence)[:80]):
                self.assertEqual(self.op(settle_call('s1', evidence)), blocked('invalid_request'))
        for request in (settle_call('s1', NEVER, 'x' * 129), settle_call('s1', NEVER, ''), settle_call('s1', NEVER, 7),
                        settle_call('s1', NEVER, phase='other'), settle_call('s1', NEVER, phase=None),
                        settle_call('x' * 257, NEVER), settle_call('', NEVER), settle_call('s\x00', NEVER),
                        settle_call('s1', NEVER, prompt=SECRET), settle_call('s1', NEVER, now=now()),
                        dict(v=1, op='settle_legacy_call', run_id='s1', phase='count', event_id='l1')):
            with self.subTest(request=json.dumps(request)[:80]):
                self.assertEqual(self.op(request)['state'], 'blocked')
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_record_cannot_carry_a_legacy_settlement(self):
        self.unresolved('s1')
        before = self.snapshot()
        for event in (dict(event_id='r1', attempt_id='s1', kind='settle_legacy_call'),
                      dict(event_id='r1', attempt_id='s1', kind='complete', evidence=RESERVATION)):
            with self.subTest(event=event):
                self.assertEqual(self.op(dict(v=1, op='record', event=event))['state'], 'blocked')
        self.assertEqual(self.snapshot(), before)

    def test_the_audit_row_carries_no_receipts_or_content(self):
        self.unresolved('s1')
        self.op(settle_call('s1', receipts(M // 2)))
        body = dict(self.events())['l1']
        self.assertEqual((body['kind'], body['attempt_id']), ('settle_legacy_call', 's1'))
        self.assertEqual(set(body) - set(budget.EVENT_BODY_FIELDS), set())
        self.assertNotIn(RECEIPT, json.dumps(self.ledger_bytes()))
        self.assertNotIn(SECRET, json.dumps(self.ledger_bytes()))


class OneShotTests(Legacy):
    def test_only_a_one_shot_command_runs_the_op(self):
        self.unresolved('s1')
        before, stored = self.snapshot(), self.ledger_bytes()
        serving = command.Service(self.settings, self.ledger, serving=True, one_shot=True)
        self.addCleanup(serving.close)
        with mock.patch.object(command, 'default_settings', return_value=self.settings), \
                mock.patch.object(command, 'default_ledger', return_value=self.ledger):
            managed = command.managed_service(self.temp.name)
        self.addCleanup(managed.close)
        for name, service in (('in_process', self.service), ('serving', serving), ('managed_service', managed)):
            with self.subTest(service=name):
                self.assertEqual(service.handle(json.dumps(settle_call('s1', RESERVATION)).encode()),
                                 blocked('operator_op_not_served'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_serve_refuses_the_op_and_one_shot_applies_it(self):
        self.unresolved('s1')
        self.service.close()
        before, stored = self.snapshot(), self.ledger_bytes()
        lines = [settle_call('s1', RESERVATION), dict(settle_call('s1', RESERVATION, 'l2'), id=7)]
        raw = b''.join(json.dumps(line).encode() + b'\n' for line in lines)
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), '--serve', '--settings', str(self.settings),
                               '--ledger', str(self.ledger)], input=raw, capture_output=True, timeout=60)
        answers = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual((done.returncode, done.stderr), (0, b''))
        self.assertEqual(answers, [blocked('operator_op_not_served'), dict(blocked('operator_op_not_served'), id=7)])
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        done = self.run_cli(['settle_legacy_call'], settle_call('s1', RESERVATION))
        self.assertEqual(json.loads(done.stdout)['outcome'], 'charged', done.stdout)

    def test_fifteen_racing_processes_apply_one_effect(self):
        self.unresolved('race')
        self.service.close()
        results = self.collect([self.spawn('settle_legacy_call', settle_call('race', RESERVATION, 'same'))
                                for _ in range(15)])
        self.assertEqual(sorted(r['duplicate'] for r in results), [False] + [True] * 14, results)
        self.unresolved('race2')
        self.service.close()
        total = self.total()
        children = [self.spawn('settle_legacy_call', settle_call('race2', receipts(M // 2) if i % 2 else RESERVATION,
                                                                 'v%d' % i)) for i in range(15)]
        results = self.collect(children)
        effects = [r for r in results if r['state'] == 'recorded']
        self.assertEqual(len(effects), 1, results)
        self.assertTrue(all(r == dict(v=1, state='blocked', reason='call_closed') for r in results
                            if r not in effects), results)
        self.assertEqual(self.total(), total - effects[0]['released'])


class OutstandingTests(Legacy):
    def test_status_lists_every_open_legacy_call_and_block(self):
        self.assertEqual(self.outstanding(), dict(empty=True, calls=dict(count=0, items=[], more=0),
                                                  legacy_blocks=dict(count=0, ids=[], more=0), ready_handoffs=0))
        self.reserved('a-res')
        self.dispatching('b-dis')
        self.unresolved('c-unr')
        self.start_run('e-done')
        self.finish_run('e-done', M)
        self.funded('conv')
        with self.open_ledger() as ledger:
            ledger.convert_hold('conv', 'personal', 'packet', 5 * M, self.revision(), now(),
                                dict(account='na', task=self.hold('conv')['task'], windows=[]))
        self.over_bound('d-ob')  # last: an over-bound call blocks the organization's admissions
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data['organizations']['personal']['legacy_block'] = True
            data['handoffs'] = {'h1': dict(state='ready'), 'h2': dict(state='claimed'), 'h3': dict(state='ready')}
        listed = self.outstanding()
        self.assertEqual(listed['calls'], dict(count=4, more=0, items=[
            dict(run_id='a-res', phase='count', state='reserved', maximum=M, over_bound=False),
            dict(run_id='b-dis', phase='count', state='dispatching', maximum=M, over_bound=False),
            dict(run_id='c-unr', phase='count', state='unresolved', maximum=M, over_bound=False),
            dict(run_id='d-ob', phase='count', state='unresolved', maximum=M, over_bound=True)]))
        self.assertEqual((listed['legacy_blocks'], listed['ready_handoffs'], listed['empty']),
                         (dict(count=1, ids=['personal'], more=0), 2, False))
        self.assertNotIn(SECRET, json.dumps(listed))
        # Settling every listed item empties it; ready handoffs are listed for information and never block.
        self.op(settle_call('a-res', NEVER, 'o1'))
        self.op(settle_call('b-dis', RESERVATION, 'o2'))
        self.op(settle_call('c-unr', receipts(1), 'o3'))
        self.op(dict(v=1, op='resolve_block', organization='personal', reason='over_bound', decision='dismiss',
                     ids=['d-ob'], event_id='o4'))
        marker = self.call(dict(v=1, op='status'))['ledger']['reconcile']['resolve_block']['legacy'][
            'organizations'][0]['marker']
        self.op(dict(v=1, op='resolve_block', organization='personal', reason='legacy', decision='confirm',
                     marker=marker, event_id='o5'))
        self.assertEqual(self.outstanding(), dict(empty=True, calls=dict(count=0, items=[], more=0),
                                                  legacy_blocks=dict(count=0, ids=[], more=0), ready_handoffs=2))

    def test_the_listing_is_capped(self):
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=32, per_account=32))))
        with self.open_ledger() as ledger:
            ledger.set_caps(32, 32)
        for i in range(18):
            self.reserved('r%02d' % i)
        calls = self.outstanding()['calls']
        self.assertEqual((calls['count'], len(calls['items']), calls['more']), (18, budget.SUMMARY_IDS, 2))


if __name__ == '__main__':
    unittest.main()
