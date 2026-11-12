"""F5.A2 capacity: the largest VALID producer state, with envelope and coverage fields, fits under 90% of MAX_RECORD.

The conservative legacy control (`test_routing_funding_capacity.CapacityTests`, every optional field on every call)
stays unchanged. This proof replaces only the per-run shapes with the largest shapes the writers can produce, and
asserts each exclusion it relies on through the real writers first.
"""
import copy
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from runtime import usage_budget as budget
from tests import test_routing_funding_capacity as cap

MAX, L = budget.MAX_INTEGER, cap.long
INCOMPLETE = cap.padded(dict(reason='unknown'), budget.MAX_INCOMPLETE_BYTES)


def dumps(value):
    return json.dumps(value, allow_nan=False, separators=(',', ':'))


def call(state, *, cost, digest=L('digest'), **extra):
    return dict(maximum=MAX, cost=cost, digest=digest, state=state, cycle=L('cycle'), **extra)


def run_shape(calls, **extra):
    return dict(consent=dict(scope=L('scope'), revision=MAX, qualification_revision=MAX), organization=L('org'),
                task=L('task'), digest=L('digest'), revision=MAX, native='N', closed=False, calls=calls,
                live_admission=dict(pin=L('pin'), maximum=MAX), **extra)


def valid_shapes():
    """Every per-run shape the writers can leave, each field at its bound."""
    extras = (dict(account=L('acct')), dict(transferred_from=dict(run=L('parent'), amount=MAX)))
    unresolved = call('unresolved', cost=None, incomplete=INCOMPLETE, over_bound=MAX)
    done = call('completed', cost=MAX, incomplete=INCOMPLETE, covered=True)
    for extra in extras:  # legacy: no generation before a completed count
        yield run_shape(dict(count=dict(unresolved, provision=MAX)), **extra)
        yield run_shape(dict(count=call('rejected-before-send', cost=0, provision=MAX)), **extra)
        for generation in (unresolved, done):
            yield run_shape(dict(count=done, generation=generation), **extra)
    placeholder = call('reserved', cost=None, digest=None)
    yield run_shape(dict(count=unresolved, generation=placeholder), envelope=True)
    for count in (call('completed', cost=None, incomplete=INCOMPLETE), done):
        for generation in (unresolved, done):
            yield run_shape(dict(count=count, generation=generation), envelope=True)


class WriterExclusionTests(unittest.TestCase):
    """The exclusions `valid_shapes` relies on, through the real writers."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'private' / 'ledger'
        self.ledger = budget.Ledger(path, budget.initialize(path))
        self.addCleanup(self.ledger.close)
        self.ledger.configure('org', dict(cycle='c1', start=0, end=10000, expiry=10000, stop_at=9000, ceiling='none',
                                          amount=None, grant_amount=0, pilot_limit=None, task_limit=None), 0)
        self.ledger.observe('org', 100 * 10**6, 100, issued=100 * 10**6)

    def begin(self, run, **options):
        return self.ledger.begin('org', run, run, 'p', 10, 1, 101, dict(account='a', task=run, windows=[]), **options)

    def test_provision_leaves_on_completion_and_never_reaches_a_generation_call(self):
        self.begin('r', provision=5)
        self.ledger.dispatch('r', 'count', 1, 101)
        self.ledger.complete('r', 'count', 1)
        self.ledger.reserve_generation('r', 'g', 10, 1, 101)
        calls = self.ledger.snapshot()['runs']['r']['calls']
        self.assertNotIn('provision', calls['count'])
        self.assertNotIn('provision', calls['generation'])

    def test_over_bound_keeps_cost_null_and_blocks_completion(self):
        self.begin('r')
        self.ledger.dispatch('r', 'count', 1, 101)
        with self.assertRaisesRegex(budget.BudgetError, 'cost_exceeds_bound'):
            self.ledger.complete('r', 'count', 11)
        count = self.ledger.snapshot()['runs']['r']['calls']['count']
        self.assertEqual((count['cost'], count['over_bound'], count['state']), (None, 11, 'unresolved'))
        with self.assertRaisesRegex(budget.BudgetError, 'over_bound_unresolved'):
            self.ledger.complete('r', 'count', 1)
        with self.assertRaisesRegex(budget.BudgetError, 'count_not_completed'):
            self.ledger.reserve_generation('r', 'g', 1, 1, 101)

    def test_a_transfer_run_has_no_account_and_an_envelope_neither(self):
        self.begin('parent', provision=5)
        child = self.begin('child', transfer_from=('parent', 5))
        self.assertIn('transferred_from', child)
        self.assertNotIn('account', child)  # `account` is written only by convert_hold, which takes no transfer
        run = self.ledger.reserve_task('org', 'e', 'e', 'p', 10, 1, 1, 101, dict(account='a', task='e', windows=[]))
        self.assertTrue(run['envelope'])
        self.assertFalse({'account', 'transferred_from', 'provision'} & (set(run) | set(run['calls']['count'])))


class ValidStateCapacityTests(unittest.TestCase):
    def capture_control(self):
        captured = {}
        real = cap.json

        class Spy:
            def __getattr__(self, attribute):
                return getattr(json, attribute)

            def dumps(self, value, *args, **kwargs):
                if kwargs.get('allow_nan') is False and isinstance(value, dict) \
                        and len(value.get('runs', {})) == budget.MAX_RUNS:
                    captured.setdefault('data', copy.deepcopy(value))
                return json.dumps(value, *args, **kwargs)
        cap.json = Spy()
        try:
            result = unittest.TestResult()
            cap.CapacityTests('test_the_worst_case_ledger_fits_under_the_hard_limit_with_margin').run(result)
        finally:
            cap.json = real
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)  # the legacy control still holds
        return captured['data']

    def stored(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private' / 'ledger'
            identity = budget.initialize(path)
            body = dict(data, identity=identity)
            with closing(sqlite3.connect(path)) as db:
                db.execute('UPDATE ledger SET body=? WHERE id=1', (dumps(body),)); db.commit()
            with budget.Ledger(path, identity) as ledger:
                ledger.set_caps(7, 3)  # one non-growing write re-serializes the full body
            with closing(sqlite3.connect(path)) as db:
                return len(db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0])

    def test_the_largest_valid_producer_states_fit_with_envelope_and_coverage_fields(self):
        data = self.capture_control()
        largest = max(valid_shapes(), key=lambda shape: len(dumps(shape)))
        for key, old in data['runs'].items():
            data['runs'][key] = dict(copy.deepcopy(largest), native=old['native'])
        org = data['organizations'][L('org')]
        org['settled']['covered'] = MAX
        org['live_funding'] = dict(observed_at=MAX, digest='f' * 64)
        size = self.stored(data)
        self.assertLessEqual(size, budget.MAX_RECORD * 0.9)  # every slot at the approved F5.S bounds
        self.assertGreater(size, 6_000_000)  # beyond the legacy soft limit: the settlement-only regime
        from runtime import usage_budget_storage as storage  # F5.S: the same state plus R fits the real soft budget
        self.assertLessEqual(storage.encoded_size(data) + storage.remaining_growth(data), budget.SOFT_RECORD)
        # A closed hold's settlement with a coverage mark stays inside the existing padded settlement allowance.
        settlement = dict(reserved=MAX, charged=MAX, released=MAX, overrun=MAX, cost=MAX, cost_source=max(budget.COST_SOURCES, key=len),
                          resolution=dict(basis='reservation_floor', receipts=32, receipts_digest='f' * 32),
                          overrun_state='unconfirmed', covered=True)
        # Measured as test_routing_accounting_roots measures the allowance: the key counts, the braces do not.
        self.assertLessEqual(len(dumps(dict(settlement=settlement))) - 2, budget.SETTLEMENT_HOLD_BYTES)


if __name__ == '__main__':
    unittest.main()
