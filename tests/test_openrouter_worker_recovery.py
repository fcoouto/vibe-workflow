"""Recovery of managed OpenRouter worker runs (runtime/openrouter_worker_routing.py; lane F4 items 4 and 5).

Recovery settles only what a dead process left: a write-ahead entry or a started run whose lock is free. A live
entry or a held execution lock is never touched (mutation M8). A run prepared but never executed is released once its
admission is older than ADMISSION_TTL, under the same per-run lock rule. A spy stands in for routing; no store.
"""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from runtime import openrouter_worker_routing as state, routing


class Spy:
    """`settle_at` that records each call and answers `reason` (None: the hold is closed)."""

    def __init__(self, reason=None):
        self.calls, self.reason = [], reason

    def __call__(self, checkout, attempt):
        self.calls.append((checkout, attempt))
        return self.reason


class RecoveryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.checkout = self.root / 'checkout'
        self.expired = []

    def run_folder(self, name, *, result='prepared', routing_state='admitted', age=0):
        run = self.root / name
        run.mkdir(mode=0o700)
        (run / 'result.json').write_text(json.dumps(dict(state=result, task_id=name)))
        state.admitted(run, 'worker-' + name, self.checkout)
        if routing_state != 'admitted':
            state.set_state(run, routing_state)
        if age:
            path = run / state.ROUTING
            path.write_text(json.dumps(dict(json.loads(path.read_text()), admitted_at=int(time.time()) - age)))
        return run

    def hold(self, path):
        """Hold `path`'s lock from another open file description, as a live process would."""
        handle = open(os.open(path, os.O_RDWR | os.O_CREAT, 0o600), 'r+')
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle

    def recover(self, spy):
        state.recover(self.root, spy, expired=self.expired.append)

    def result(self, run):
        return json.loads((run / 'result.json').read_text())['state']


class LiveEntriesAreSkipped(RecoveryCase):
    """M8: recovery must leave a live `prepare`'s write-ahead entry and a live `execute`'s run alone."""

    def test_a_live_write_ahead_entry_is_skipped_until_its_lock_is_free(self):
        handle, entry = state.write_ahead(self.root, 'worker-live', self.checkout)
        spy = Spy()
        self.recover(spy)
        self.assertEqual((spy.calls, entry.exists()), ([], True))
        handle.close()  # the `prepare` died
        self.recover(spy)
        self.assertEqual((spy.calls, entry.exists()), ([(str(self.checkout), 'worker-live')], False))

    def test_a_started_run_with_a_held_execution_lock_is_skipped(self):
        for routing_state in ('admitted', 'dispatching', 'dispatched'):
            with self.subTest(routing_state=routing_state):
                run = self.run_folder('run-' + routing_state, result='running', routing_state=routing_state)
                lock = self.hold(run / 'execution.lock')
                spy = Spy()
                self.recover(spy)
                self.assertEqual((spy.calls, state.state(run)), ([], routing_state))
                lock.close()  # the `execute` died
                self.recover(spy)
                self.assertEqual(spy.calls, [(str(self.checkout), 'worker-run-' + routing_state)])
                self.assertEqual(state.state(run), 'settled')

    def test_a_held_execution_lock_also_protects_an_expired_prepared_run(self):
        run = self.run_folder('pilot', age=routing.ADMISSION_TTL + 60)
        lock = self.hold(run / 'execution.lock')
        spy = Spy()
        self.recover(spy)
        self.assertEqual((spy.calls, state.state(run), self.result(run), self.expired), ([], 'admitted', 'prepared', []))
        lock.close()
        self.recover(spy)
        self.assertEqual((len(spy.calls), state.state(run), self.expired), (1, 'settled', [run]))


class ExpiredPreparedRuns(RecoveryCase):
    def test_a_prepared_run_past_the_admission_ttl_is_released(self):
        run = self.run_folder('pilot', age=routing.ADMISSION_TTL + 1)
        spy = Spy()
        self.recover(spy)
        self.assertEqual(spy.calls, [(str(self.checkout), 'worker-pilot')])
        self.assertEqual((state.state(run), self.expired), ('settled', [run]))
        self.recover(spy)  # settling again changes nothing
        self.assertEqual(len(spy.calls), 1)

    def test_a_prepared_run_within_the_admission_ttl_keeps_its_hold(self):
        for age in (0, routing.ADMISSION_TTL - 5):
            with self.subTest(age=age):
                run = self.run_folder('pilot-%d' % age, age=age)
                spy = Spy()
                self.recover(spy)
                self.assertEqual((spy.calls, state.state(run), self.expired), ([], 'admitted', []))

    def test_an_admission_time_in_the_future_is_never_expired(self):
        run = self.run_folder('pilot', age=-3600)  # a clock that went back
        spy = Spy()
        self.recover(spy)
        self.assertEqual((spy.calls, state.state(run)), ([], 'admitted'))

    def test_a_run_without_an_admission_time_ages_from_its_routing_file(self):
        run = self.run_folder('pilot')
        path = run / state.ROUTING
        path.write_text(json.dumps({key: value for key, value in json.loads(path.read_text()).items()
                                    if key != 'admitted_at'}))
        spy = Spy()
        self.recover(spy)
        self.assertEqual(spy.calls, [])
        old = time.time() - routing.ADMISSION_TTL - 60
        os.utime(path, (old, old))
        self.recover(spy)
        self.assertEqual((len(spy.calls), state.state(run)), (1, 'settled'))

    def test_a_refused_release_keeps_the_run_for_the_next_recovery(self):
        run = self.run_folder('pilot', age=routing.ADMISSION_TTL + 1)
        self.recover(Spy('routing_unavailable'))
        self.assertEqual((state.state(run), self.result(run), self.expired), ('admitted', 'prepared', []))
        self.recover(Spy())
        self.assertEqual((state.state(run), self.expired), ('settled', [run]))

    def test_only_prepared_admitted_runs_expire(self):
        runs = [self.run_folder('settled', routing_state='settled', age=routing.ADMISSION_TTL + 1),
                self.run_folder('uncertain', routing_state='uncertain', age=routing.ADMISSION_TTL + 1),
                self.run_folder('accepted', result='accepted', routing_state='settled', age=routing.ADMISSION_TTL + 1)]
        spy = Spy()
        self.recover(spy)
        self.assertEqual((spy.calls, self.expired), ([], []))
        self.assertEqual([state.state(run) for run in runs], ['settled', 'uncertain', 'settled'])

    def test_an_execute_that_finished_between_the_read_and_the_lock_is_left_alone(self):
        """Review S1: recovery read `prepared`, then an `execute` took the lock, finished and released it before
        recovery's own lock attempt. The re-read under the lock sees it started: no settlement, no expiry mark."""
        run = self.run_folder('pilot', age=routing.ADMISSION_TTL + 1)
        real = state._unlocked

        def raced(path):
            if path == run / 'execution.lock':
                (run / 'result.json').write_text(json.dumps(dict(state='awaiting_verification', task_id='pilot')))
                state.set_state(run, 'settled')
            return real(path)
        spy = Spy()
        with mock.patch.object(state, '_unlocked', raced):
            self.recover(spy)
        self.assertEqual((spy.calls, self.expired, self.result(run)), ([], [], 'awaiting_verification'))

    def test_a_failing_expiry_mark_is_recorded_on_its_run_and_recovery_continues(self):
        """Review NIT 2: one stale run whose mark fails never stops recovery of the others or its caller."""
        first = self.run_folder('first', age=routing.ADMISSION_TTL + 1)
        second = self.run_folder('second', age=routing.ADMISSION_TTL + 1)
        marked = []

        def expired(run):
            if run == first:
                raise OSError('disk full')
            marked.append(run)
        spy = Spy()
        state.recover(self.root, spy, expired=expired)  # never raises
        self.assertEqual(len(spy.calls), 2)
        self.assertEqual(marked, [second])
        recorded = json.loads((first / state.ROUTING).read_text())
        self.assertEqual((recorded['state'], recorded['reason']), ('settled', state.EXPIRY_MARK_FAILED))
        self.assertNotIn('disk full', json.dumps(recorded))
        self.assertEqual(self.result(first), 'prepared')  # its hold is closed: a later execute cannot send

    def test_admitted_records_the_admission_time(self):
        before = int(time.time())
        run = self.run_folder('pilot')
        recorded = json.loads((run / state.ROUTING).read_text())
        self.assertTrue(before <= recorded['admitted_at'] <= int(time.time()))
        self.assertEqual(recorded['state'], 'admitted')


if __name__ == '__main__':
    unittest.main()
