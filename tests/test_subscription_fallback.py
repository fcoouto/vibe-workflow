"""Provider-managed fallback exposure: dispatch fence and terminal consumption boundary (design 4.1).

A dispatched or uncertain fallback attempt can consume an unknown amount of its pool, so included admissions of that
pool stay fenced until every such attempt has a terminal disposition that wrote the pool's consumed boundary, and an
included admission whose readings predate that boundary never dispatches. Disposable stores and fixed fixtures only.
"""
import subprocess
import time
import unittest
from unittest import mock

from runtime import routing, routing_observations as collector, subscription_reconcile as sub, usage_budget as budget
from runtime.subscription_fallback import active_pools, dispatch_problem
from tests.test_routing_command import facts, now, observations, prepare
from tests.test_routing_dispatch import dispatch, record
from tests.test_routing_subscription_reconcile import ABANDON, Subscription, reconcile

DAY = 86400


class FallbackBase(Subscription):
    def admit_on(self, attempt, used, at=None, fallback=True):
        """Prepare on pool `sub` from a caller snapshot whose 5h window shows `used` of 1000."""
        obs = observations(at=at, account='acct-' + attempt, **({'provider_fallback': True} if fallback else {}))
        obs['pools']['sub']['windows'][0]['used'] = used
        return self.call(prepare(attempt, observations=obs, constraints=dict(allowed_strategy_ids=['s-sub'])))

    def fallback(self, attempt, at=None):
        response = self.admit_on(attempt, used=995, at=at)
        self.assertEqual(response['state'], 'admitted', response)
        self.assertEqual(response['admission']['reserved'], {})
        self.assertIn(routing.FALLBACK, response['admission']['reason_codes'])
        return response['admission']

    def included(self, attempt, at=None):
        response = self.admit_on(attempt, used=100, at=at, fallback=False)
        self.assertEqual(response['state'], 'admitted', response)
        self.assertEqual(response['admission']['reserved'], {'win:sub/5h': response['admission']['reserved']
                                                             ['win:sub/5h']})
        return response['admission']

    def record_of(self, attempt):
        return budget.Ledger.admission(self.snapshot(), attempt)

    def boundary(self):
        return collector.consumed_at(self.settings, 'sub')

    def uncertain(self, attempt):
        self.assertEqual(self.call(record(attempt, 'uncertain'))['state'], 'recorded')

    def try_dispatch(self, attempt, **extra):
        return self.call(dispatch(attempt, request=facts(), **extra))


class ExposureTests(FallbackBase):
    def test_read_during_running_or_uncertain_fallback_does_not_restore_included(self):
        self.included('i1')  # prepared before the fallback's send
        self.fallback('f1')
        self.assertNotIn('sub', active_pools(self.snapshot()))  # an unsent fallback consumes nothing yet
        self.send('f1', sub=True)
        data = self.snapshot()
        self.assertIn('sub', active_pools(data))
        included_record = budget.Ledger.admission(data, 'i1')
        self.assertIsNotNone(dispatch_problem(data, included_record, self.settings))
        refused = self.try_dispatch('i1')
        self.assertEqual(refused['state'], 'blocked', refused)
        self.assertEqual(self.hold('i1')['state'], 'admitted')  # nothing written; the reservation stays held
        # A newer, roomy reading during the exposure claims no included capacity.
        self.assertEqual(self.admit_on('i2', used=100, fallback=False)['state'], 'pending')
        overflow = self.admit_on('i3', used=100)
        self.assertEqual((overflow['state'], overflow['admission']['reserved']), ('admitted', {}), overflow)
        self.assertIn(routing.FALLBACK, overflow['admission']['reason_codes'])
        self.uncertain('f1')
        data = self.snapshot()
        self.assertIn('sub', active_pools(data))  # an uncertain send keeps its unknown exposure
        self.assertIsNotNone(dispatch_problem(data, budget.Ledger.admission(data, 'i1'), self.settings))

    def test_concurrent_fallbacks_need_terminal_coverage_for_every_attempt(self):
        fallback_admission = self.fallback('f1')
        self.fallback('f2')
        self.assertEqual(fallback_admission["reserved"], {})
        for attempt in ('f1', 'f2'):
            self.send(attempt, sub=True)
            self.uncertain(attempt)
        self.assertEqual(self.call(reconcile('f1', self.usage_of('f1'), 'r1'))['state'], 'recorded')
        first = self.boundary()
        self.assertGreater(first, 0)  # written before f1 closed
        self.assertIn('sub', active_pools(self.snapshot()))  # f2 can still consume
        included_allowed_while_either_attempt_is_open = self.admit_on('i1', used=100, fallback=False)['state'] \
            == 'admitted'
        self.assertFalse(included_allowed_while_either_attempt_is_open)
        self.assertEqual(self.call(reconcile('f2', ABANDON, 'r2'))['state'], 'recorded')
        last = self.boundary()
        self.assertGreaterEqual(last, first)
        self.assertEqual(active_pools(self.snapshot()), set())
        self.included('i2', at=last)  # a reading no newer than the last terminal consumption
        refused = self.try_dispatch('i2', now=last + 2)
        included_allowed_from_read_before_last_terminal_consumption = refused['state'] == 'dispatched'
        self.assertFalse(included_allowed_from_read_before_last_terminal_consumption)
        self.included('i3', at=last + 1)  # a reading observed after every boundary restores the pool
        self.assertEqual(self.try_dispatch('i3', now=last + 2)['state'], 'dispatched')

    def test_an_included_admission_prepared_before_the_fallback_completed_never_dispatches(self):
        self.included('i1', at=now() - 5)
        self.fallback('f1')
        self.send('f1', sub=True)
        self.uncertain('f1')
        self.assertEqual(self.call(reconcile('f1', self.usage_of('f1'), 'r1'))['state'], 'recorded')
        self.assertEqual(active_pools(self.snapshot()), set())
        self.assertGreaterEqual(self.boundary(), self.record_of('i1')['at'])
        refused = self.try_dispatch('i1')
        self.assertEqual(refused['state'], 'blocked', refused)
        self.assertEqual(self.hold('i1')['state'], 'admitted')

    def test_a_provably_unsent_fallback_adds_no_usage(self):
        self.fallback('f1')
        self.assertEqual(self.call(record('f1', 'not_dispatched'))['state'], 'recorded')
        self.assertEqual((active_pools(self.snapshot()), self.boundary()), (set(), 0))

    def test_exposure_survives_pruning(self):
        self.fallback('f1')
        self.send('f1', sub=True)
        with self.open_ledger() as ledger:
            ledger.expire_evidence(now() + 30 * DAY)
        self.assertIn('sub', active_pools(self.snapshot()))

    def test_read_only_checks_start_no_process(self):
        self.included('i1')
        self.fallback('f1')
        self.send('f1', sub=True)
        data = self.snapshot()
        with mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('no process')):
            self.assertEqual(active_pools(data), {'sub'})
            self.assertIsNotNone(dispatch_problem(data, budget.Ledger.admission(data, 'i1'), self.settings))
            self.assertIsNone(dispatch_problem(data, budget.Ledger.admission(data, 'f1'), self.settings))


class TerminalBoundaryTests(FallbackBase):
    def sent_uncertain(self, attempt):
        self.fallback(attempt)
        self.send(attempt, sub=True)
        self.uncertain(attempt)

    def test_a_failed_terminal_write_keeps_the_attempt_and_its_exposure(self):
        self.sent_uncertain('f1')
        for evidence in (self.usage_of('f1'), ABANDON):
            with self.subTest(evidence=evidence['kind']), \
                    mock.patch.object(collector, 'mark_consumed', side_effect=OSError('store unwritable')):
                answer = self.call(reconcile('f1', evidence, 'r-' + evidence['kind']))
                self.assertEqual(answer['state'], 'blocked', answer)
            self.assertEqual(self.hold('f1')['state'], 'uncertain')
            self.assertIn('sub', active_pools(self.snapshot()))
        self.assertEqual(self.boundary(), 0)
        self.assertEqual(self.call(reconcile('f1', ABANDON, 'r-abandon'))['state'], 'recorded')  # a retry settles it
        self.assertGreater(self.boundary(), 0)

    def test_a_repeated_event_never_moves_the_boundary_again(self):
        self.sent_uncertain('f1')
        self.assertEqual(self.call(reconcile('f1', ABANDON, 'r1'))['state'], 'recorded')
        first = self.boundary()
        time.sleep(1.1)
        again = self.call(reconcile('f1', ABANDON, 'r1'))
        self.assertIs(again.get('duplicate'), True, again)
        self.assertEqual(self.boundary(), first)

    def test_empty_reservation_reconciliation_and_abandonment_mark_before_closing(self):
        for attempt, evidence in (('f1', 'host_usage'), ('f2', 'abandon')):
            with self.subTest(evidence=evidence):
                self.sent_uncertain(attempt)
                self.assertEqual(self.hold(attempt)['windows'], {})
                before = self.boundary()
                body = self.usage_of(attempt) if evidence == 'host_usage' else ABANDON
                self.assertEqual(self.call(reconcile(attempt, body, 'r-' + attempt))['state'], 'recorded')
                self.assertTrue(self.hold(attempt)['closed'])
                self.assertGreaterEqual(self.boundary(), max(before, 1))
                self.assertIn(attempt, collector._read(collector.window_path(self.settings))['consumed_by']['sub'])
        self.assertEqual(self.debits(), {})  # an empty reservation leaves no window debit

    def test_a_fallback_cannot_close_without_the_actual_store(self):
        self.sent_uncertain('f1')
        event, _ = sub.reconcile_event(dict(event_id='r1', attempt_id='f1', evidence=ABANDON))
        with self.open_ledger() as ledger:
            answer = sub.reconcile(ledger, event, now())
        self.assertEqual(answer['state'], 'blocked', answer)
        self.assertEqual(self.hold('f1')['state'], 'uncertain')
        with self.open_ledger() as ledger:
            self.assertEqual(sub.reconcile(ledger, event, now(), settings_path=self.settings)['state'], 'recorded')
        self.assertGreater(self.boundary(), 0)

    def test_the_service_reconciles_with_its_own_settings_store(self):
        self.sent_uncertain('f1')
        seen = []
        real = collector.mark_consumed

        def spy(settings_path, *args, **options):
            seen.append((settings_path, options))
            return real(settings_path, *args, **options)
        with mock.patch.object(collector, 'mark_consumed', spy):
            self.assertEqual(self.call(reconcile('f1', ABANDON, 'r1'))['state'], 'recorded')
        self.assertEqual(seen, [(self.service.settings_path, dict(once=False))])  # a terminal retry moves forward


class RawRecordClosureTests(FallbackBase):
    """A client's raw `record complete` is a supported closure: a dispatched fallback releases only after its pool's
    boundary is written to the service's own store; event validation, replay and conflict handling are unchanged."""

    def sent(self, attempt):
        self.fallback(attempt)
        self.send(attempt, sub=True)

    def test_a_raw_completion_marks_the_pool_before_release(self):
        self.sent('f1')
        self.assertEqual(self.call(record('f1', 'complete'))['state'], 'recorded')
        self.assertTrue(self.hold('f1')['closed'])
        self.assertGreater(self.boundary(), 0)
        self.assertEqual(active_pools(self.snapshot()), set())

    def test_a_failed_write_records_nothing_and_keeps_the_exposure(self):
        self.sent('f1')
        with mock.patch.object(collector, 'mark_consumed', side_effect=OSError('store unwritable')):
            answer = self.call(record('f1', 'complete'))
        self.assertEqual((answer['state'], answer['reason']), ('blocked', 'observation_store_unavailable'), answer)
        self.assertEqual(self.hold('f1')['state'], 'dispatched')
        self.assertIn('sub', active_pools(self.snapshot()))
        self.assertEqual(self.call(record('f1', 'complete'))['state'], 'recorded')  # the same event id retries

    def test_replay_and_conflict_keep_the_first_boundary(self):
        self.sent('f1')
        self.assertEqual(self.call(record('f1', 'complete'))['state'], 'recorded')
        first = self.boundary()
        time.sleep(1.1)
        self.assertIs(self.call(record('f1', 'complete')).get('duplicate'), True)
        conflict = self.call(record('f1', 'complete', usage=self.usage_of('f1')['usage']))
        self.assertEqual(conflict['state'], 'blocked', conflict)
        self.assertEqual(self.boundary(), first)

    def test_a_library_closure_without_the_store_is_refused(self):
        self.sent('f1')
        event = dict(event_id='c1', attempt_id='f1', kind='complete')
        with self.open_ledger() as ledger:
            refused = routing.record_event(ledger, event, now())
            self.assertEqual(refused, dict(state='blocked', reason='observation_store_unavailable'))
            self.assertEqual(ledger.snapshot()['holds']['f1']['state'], 'dispatched')
            self.assertEqual(routing.record_event(ledger, event, now(), settings_path=self.settings)['state'],
                             'recorded')
        self.assertGreater(self.boundary(), 0)

    def test_included_and_unsent_closures_are_unchanged(self):
        self.included('i1')
        self.send('i1', sub=True)
        self.fallback('f1')
        with self.open_ledger() as ledger:  # no store needed: nothing to mark for either
            self.assertEqual(routing.record_event(ledger, dict(event_id='c1', attempt_id='i1', kind='complete'),
                                                  now())['state'], 'recorded')
            self.assertEqual(routing.record_event(ledger, dict(event_id='n1', attempt_id='f1',
                                                               kind='not_dispatched'), now())['state'], 'recorded')
        self.assertEqual(self.boundary(), 0)


class TransactionBoundaryTests(FallbackBase):
    """Review fix round 1 (T1-F1, T1-F2): a fallback's terminal boundary is written inside the write transaction that
    commits its closure, after every validation, and a terminal retry moves it forward. Deterministic: the service
    clock is fixed and the interleaving or failure is injected at a ledger seam; no sleep, thread or process."""

    def at(self, moment):
        self.service.clock = lambda: moment  # the command clock of every following request

    def mid_flight_unusable(self, attempt, observed):
        """An included admission from a reading observed while the fallback could consume never dispatches."""
        self.included(attempt, at=observed)
        refused = self.try_dispatch(attempt)
        self.assertEqual((refused['state'], refused.get('reason')), ('blocked', 'stale_observation'), refused)

    def failing_commit(self, attempt):
        """The transaction that closes `attempt` fails once after its body is built (after the mark, before COMMIT)."""
        real, failed = budget.usage_budget_storage.body_for_write, []

        def body(data, **options):
            if not failed and data['holds'].get(attempt, {}).get('closed'):
                failed.append(attempt)
                raise budget.BudgetError('ledger_busy')
            return real(data, **options)
        return mock.patch.object(budget.usage_budget_storage, 'body_for_write', body)

    def stored(self, event_id):
        return any(stored == event_id for stored, _ in self.events())

    def test_a_dispatch_committed_inside_the_completion_never_releases_unmarked(self):
        t0 = now()
        self.at(t0)
        self.fallback('f1', at=t0)
        real, raced = budget.Ledger.record_event, []

        def interleaved(ledger, event, *args, **options):
            if event['attempt_id'] == 'f1' and event['kind'] == 'complete' and not raced:
                raced.append(True)  # the client's own dispatch commits after the completion began
                self.send('f1', sub=True)
            return real(ledger, event, *args, **options)
        self.at(t0 + 10)
        with mock.patch.object(budget.Ledger, 'record_event', interleaved):
            answer = self.call(record('f1', 'complete'))
        self.assertEqual(raced, [True])
        data = self.snapshot()
        if not data['holds']['f1']['closed']:
            self.assertIn('sub', active_pools(data))  # refused: the exposure stays fenced
            return
        self.assertEqual(answer['state'], 'recorded', answer)
        self.assertGreaterEqual(self.boundary(), t0 + 10)  # closed only with its terminal boundary
        self.mid_flight_unusable('i1', t0 - 5)  # pre-fallback quota never dispatches as included

    def test_a_conflicting_completion_marks_nothing_and_the_genuine_closure_moves_the_boundary(self):
        t0 = now()
        self.at(t0)
        self.fallback('f0', at=t0)
        self.assertEqual(self.call(record('f0', 'not_dispatched', 'e1'))['state'], 'recorded')
        self.fallback('f1', at=t0)
        self.send('f1', sub=True)
        self.at(t0 + 10)
        conflict = self.call(record('f1', 'complete', 'e1'))  # another event's id with another digest
        self.assertEqual((conflict['state'], conflict.get('reason')), ('blocked', 'event_conflict'), conflict)
        self.assertEqual(self.boundary(), 0)  # a refused event writes no mark
        self.assertIn('sub', active_pools(self.snapshot()))
        self.at(t0 + 30)
        self.assertEqual(self.call(record('f1', 'complete', 'e2'))['state'], 'recorded')
        self.assertGreaterEqual(self.boundary(), t0 + 30)
        self.mid_flight_unusable('i1', t0 + 20)

    def closes_after_a_failed_commit(self, path):
        t0 = now()
        self.at(t0)
        self.fallback('f1', at=t0)
        self.send('f1', sub=True)
        if path != 'complete':
            self.uncertain('f1')
        state = self.hold('f1')['state']
        request = record('f1', 'complete', 'c1') if path == 'complete' else reconcile(
            'f1', self.usage_of('f1') if path == 'host_usage' else ABANDON, 'c1')
        self.at(t0 + 10)
        with self.failing_commit('f1'):
            failed = self.call(request)
        self.assertEqual((failed['state'], failed.get('reason')), ('blocked', 'ledger_busy'), failed)
        self.assertEqual((self.hold('f1')['closed'], self.hold('f1')['state']), (False, state))
        self.assertFalse(self.stored('c1'))  # no committed closure event
        self.assertIn('sub', active_pools(self.snapshot()))
        self.at(t0 + 30)
        self.assertEqual(self.call(request)['state'], 'recorded')  # the same event retries
        boundary = self.boundary()
        self.assertGreaterEqual(boundary, t0 + 30)
        self.at(t0 + 40)
        self.assertIs(self.call(request).get('duplicate'), True)
        self.assertEqual(self.boundary(), boundary)  # an accepted replay leaves it unchanged
        self.mid_flight_unusable('i1', t0 + 20)

    def test_a_raw_completion_that_fails_after_its_mark_retries_forward(self):
        self.closes_after_a_failed_commit('complete')

    def test_a_host_usage_reconciliation_that_fails_after_its_mark_retries_forward(self):
        self.closes_after_a_failed_commit('host_usage')

    def test_an_abandon_that_fails_after_its_mark_retries_forward(self):
        self.closes_after_a_failed_commit('abandon')


if __name__ == '__main__':
    unittest.main()
