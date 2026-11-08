"""Offline spending invariants; all state is disposable and no network is used."""
from contextlib import closing
import json
import multiprocessing
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest

from runtime import usage_budget as budget


def competing(path, identity, name, queue, maximum=10):
    try:
        with budget.Ledger(path, identity) as ledger:
            ledger.begin('org', name, name, 'packet', maximum, 1, 101, native(name))
        queue.put('accepted')
    except budget.BudgetError:
        queue.put('blocked')


def competing_live(path, identity, queue):
    try:
        with budget.Ledger(path, identity) as ledger:
            ledger.begin('org', 'task', 'same-run', 'packet', 10, 1, 101, native(),
                         live_gate=lambda data, now: dict(pin='a' * 64, maximum=20))
        queue.put('accepted')
    except budget.BudgetError as error:
        queue.put(str(error))


def native(task='task', remaining=90):
    return {'account': 'account', 'task': task, 'windows': [
        {'id': 'weekly:1', 'remaining_pp': remaining, 'resolution_pp': 1,
         'allowance_pp': 10, 'observed_at': 100, 'reset_at': 10000}]}


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'organizations.sqlite3'
        self.identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, self.identity)
        self.configure()

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def configure(self, **updates):
        policy = dict(cycle='cycle1', start=0, end=10000, expiry=10000,
                      stop_at=9000, ceiling='custom', amount=500,
                      grant_amount=800, pilot_limit=500, task_limit=200)
        policy.update(updates)
        return self.ledger.configure('org', policy, 0)

    def observe(self, amount=800, at=100):
        self.ledger.observe('org', amount, at)

    def begin(self, run='run', task='task', maximum=10, now=101, evidence=None, **options):
        return self.ledger.begin('org', task, run, 'packet', maximum, 1, now,
                                 native(task) if evidence is None else evidence, **options)

    def count_done(self, run='run', cost=1):
        self.ledger.dispatch(run, 'count', 1, 102)
        self.ledger.complete(run, 'count', cost)

    def test_quota_records_do_not_block_api_accounting_or_dispatch(self):
        self.observe()
        with self.ledger.transaction() as data:
            data['native_paused'] = {'account': 'native_allowance_overrun'}
        for index, windows in enumerate((native(remaining=15)['windows'], native(remaining=0)['windows'], [])):
            task = 'quota-' + str(index)
            self.begin(task, task, evidence=dict(account='account', task=task, windows=windows))
            self.count_done(task)
            self.ledger.close_run(task)
        saved = self.ledger.snapshot()
        self.assertEqual(len(saved['runs']), 3)
        self.assertEqual(saved['native_paused'], {'account': 'native_allowance_overrun'})
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.begin('unfunded', 'unfunded', maximum=1000, evidence=dict(account='account', task='unfunded', windows=[]))

    def test_initialization_never_overwrites_and_identity_is_required(self):
        with self.assertRaises(budget.BudgetError): budget.initialize(self.path)
        with self.assertRaises(budget.BudgetError): budget.Ledger(self.path, 'wrong')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_missing_observation_blocks_without_creating_a_claim(self):
        with self.assertRaises(budget.BudgetError): self.begin()
        self.assertEqual(self.ledger.snapshot()['runs'], {})

    def test_count_and_generation_settle_separately_without_retiring_credit_cost(self):
        self.observe()
        self.begin()
        self.count_done()
        self.ledger.reserve_generation('run', 'generation-packet', 30, 1, 103)
        self.ledger.dispatch('run', 'generation', 1, 104)
        self.ledger.complete('run', 'generation', 12)
        state = self.ledger.snapshot()
        self.assertNotIn('slot', state)
        self.assertTrue(state['runs']['run']['closed'])
        self.assertEqual(self.ledger.headroom('org'), 487)
        self.assertEqual(len(state['runs']['run']['calls']), 2)
        self.ledger.observe('org', 787, 110)
        self.assertEqual(self.ledger.headroom('org'), 487)
        self.assertEqual(self.ledger.credit_headroom('org'), 758)
        self.assertEqual(len(state['native']), 1)  # Task identity remains for integration/review.

    def test_unknown_count_blocks_generation_but_keeps_only_its_own_exposure_after_restart(self):
        self.observe(); self.begin(); self.ledger.dispatch('run', 'count', 1, 102)
        self.ledger.uncertain('run', 'count')
        self.ledger.close(); self.ledger = budget.Ledger(self.path, self.identity)
        with self.assertRaises(budget.BudgetError): self.ledger.reserve_generation('run', 'g', 10, 1, 103)
        before = self.ledger.headroom('org')
        self.begin('other', 'other')  # Unrelated work that fits is not serialized behind the unknown.
        self.assertEqual(self.ledger.headroom('org'), before - 10)
        state = self.ledger.snapshot()
        self.assertNotIn('slot', state)
        self.assertEqual(state['runs']['run']['calls']['count']['state'], 'unresolved')
        self.assertFalse(state['runs']['run']['closed'])

    def test_generation_denied_preserves_count_and_close_releases_the_claim(self):
        self.observe(); self.begin(); self.count_done()
        with self.assertRaises(budget.BudgetError): self.ledger.reserve_generation('run', 'g', 201, 1, 103)
        self.ledger.close_run('run')
        self.assertTrue(self.ledger.snapshot()['runs']['run']['closed'])
        self.assertEqual(self.ledger.headroom('org'), 499)
        with self.assertRaises(budget.BudgetError): self.ledger.dispatch('run', 'count', 1, 104)

    def test_crash_between_calls_never_replays_or_auto_continues(self):
        self.observe(); self.begin(); self.count_done()
        self.ledger.close(); self.ledger = budget.Ledger(self.path, self.identity)
        before = self.ledger.snapshot()
        self.begin()  # Same ID returns record only.
        self.assertEqual(self.ledger.snapshot(), before)
        self.ledger.close_run('run')
        with self.assertRaises(budget.BudgetError): self.ledger.reserve_generation('run', 'g', 20, 1, 103)

    def test_policy_change_revokes_prepared_dispatch_without_erasing_cost(self):
        self.observe(); self.begin()
        policy = self.ledger.snapshot()['organizations']['org']['policy'].copy()
        policy['amount'] = 5
        self.ledger.configure('org', policy, 1)
        with self.assertRaises(budget.BudgetError): self.ledger.dispatch('run', 'count', 1, 102)
        self.assertEqual(self.ledger.headroom('org'), 0)

    def test_unchanged_validated_policy_preserves_revision_and_all_accounting(self):
        self.observe(); self.begin()
        before = self.ledger.snapshot()
        policy = before['organizations']['org']['policy'].copy()
        self.assertEqual(self.ledger.configure('org', policy, 1), 1)
        self.assertEqual(self.ledger.snapshot(), before)
        with self.assertRaisesRegex(budget.BudgetError, 'policy_revision_changed'):
            self.ledger.configure('org', policy, 0)
        with self.assertRaisesRegex(budget.BudgetError, 'invalid_integer'):
            self.ledger.configure('org', dict(policy, amount=True), 1)
        self.ledger.dispatch('run', 'count', 1, 102)

    def test_same_task_correction_reuses_identity_but_retains_call_cost(self):
        self.observe(); self.begin(); self.count_done(); self.ledger.close_run('run')
        self.begin('correction')
        self.assertEqual(len(self.ledger.snapshot()['native']), 1)
        self.assertEqual(self.ledger.headroom('org'), 489)

    def test_native_completion_requires_stopped_task_and_preserves_records(self):
        self.observe(); self.begin()
        with self.assertRaises(budget.BudgetError): self.ledger.release_native('account', 'task', native(), 101)
        self.ledger.close_run('run')
        before = self.ledger.snapshot()
        self.ledger.release_native('account', 'task', native(), 1001)
        after = self.ledger.snapshot()
        self.assertEqual(after['native'], before['native'])
        self.assertEqual(after['runs'], before['runs'])
        self.assertIn('["account", "task"]', after['finished_native'])

    def test_lowest_balance_is_monotonic_and_refresh_never_clears_deductions(self):
        self.observe(); self.begin(); self.count_done(); self.ledger.close_run('run')
        self.ledger.observe('org', 12, 110)
        self.assertEqual(self.ledger.headroom('org'), 0)
        self.ledger.observe('org', 1000, 120)
        self.assertEqual(self.ledger.headroom('org'), 0)
        self.assertEqual(self.ledger.reconcile(), {'state': 'manual_recovery_required'})

    def test_expiry_freshness_and_future_observations_block(self):
        self.observe()
        for now in (99, 1001, 9000, 10000):
            with self.assertRaises(budget.BudgetError): self.begin(now=now)

    def test_unexpected_actual_cost_keeps_maximum_and_blocks_org(self):
        self.observe(); self.begin(); self.ledger.dispatch('run', 'count', 1, 102)
        with self.assertRaises(budget.BudgetError): self.ledger.complete('run', 'count', 11)
        self.assertEqual(self.ledger.snapshot()['runs']['run']['calls']['count']['maximum'], 10)
        with self.assertRaises(budget.BudgetError): self.begin('other', 'other')

    def test_bool_and_negative_money_rejected(self):
        self.observe()
        for value in (True, -1, 1.2, 2**63):
            with self.assertRaises(budget.BudgetError): self.begin(maximum=value)

    def test_two_processes_share_the_last_headroom(self):
        self.observe(400)  # Credit 392 after the protected share: only one 200 fits.
        ctx = multiprocessing.get_context('spawn'); queue = ctx.Queue()
        processes = [ctx.Process(target=competing, args=(self.path, self.identity, name, queue, 200)) for name in ('a','b')]
        for process in processes: process.start()
        for process in processes: process.join(10); self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(queue.get(timeout=2) for _ in processes), ['accepted', 'blocked'])
        queue.close(); queue.join_thread()

    def test_live_gate_caps_retained_cost_and_reservations_across_runs(self):
        self.observe(); self.begin('old'); self.count_done('old', cost=4); self.ledger.close_run('old')
        gate = lambda data, now: dict(pin='a' * 64, maximum=13)
        with self.assertRaisesRegex(budget.BudgetError, 'live_cap_exceeded'):
            self.begin(maximum=10, live_gate=gate)
        self.begin(maximum=9, live_gate=gate)
        self.ledger.dispatch('run', 'count', 1, 102, live_gate=gate)
        self.ledger.complete('run', 'count', 2)
        with self.assertRaisesRegex(budget.BudgetError, 'live_cap_exceeded'):
            self.ledger.reserve_generation('run', 'g', 8, 1, 103, live_gate=gate)
        self.ledger.reserve_generation('run', 'g', 7, 1, 103, live_gate=gate)
        self.ledger.dispatch('run', 'generation', 1, 104, live_gate=gate)
        self.assertEqual(self.ledger.snapshot()['runs']['run']['live_admission'],
                         dict(pin='a' * 64, maximum=13))

    def test_live_gate_credit_staging_rolls_back_on_failure(self):
        def gate(data, now):
            data['organizations']['org'].update(balance=300, issued=300, observed_at=now)
            return dict(pin='a' * 64, maximum=20)

        def reject(data, now):
            gate(data, now)
            raise budget.BudgetError('evidence_changed')

        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'evidence_changed'):
            self.begin(live_gate=reject)
        self.assertEqual(self.ledger.snapshot(), before)
        self.begin(live_gate=gate)
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'evidence_changed'):
            self.ledger.dispatch('run', 'count', 1, 102, live_gate=reject)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_live_gate_requires_same_pin_and_cap_at_each_phase(self):
        self.observe()
        gate = lambda data, now: dict(pin='a' * 64, maximum=20)
        self.begin(live_gate=gate)
        for phase in ('dispatch', 'reserve'):
            before = self.ledger.snapshot()
            for changed in (dict(pin='b' * 64, maximum=20), dict(pin='a' * 64, maximum=21)):
                with self.assertRaisesRegex(budget.BudgetError, 'live_admission_mismatch'):
                    if phase == 'dispatch':
                        self.ledger.dispatch('run', 'count', 1, 102, live_gate=lambda *_: changed)
                    else:
                        self.ledger.reserve_generation('run', 'g', 5, 1, 103, live_gate=lambda *_: changed)
                self.assertEqual(self.ledger.snapshot(), before)
            if phase == 'dispatch':
                self.ledger.dispatch('run', 'count', 1, 102, live_gate=gate)
                self.ledger.complete('run', 'count', 1)

    def test_ungated_and_live_runs_cannot_reuse_each_others_admission(self):
        self.observe()
        gate = lambda data, now: dict(pin='a' * 64, maximum=20)
        self.begin('ungated')
        with self.assertRaisesRegex(budget.BudgetError, 'live_run_id_already_used'):
            self.begin('ungated', live_gate=gate)
        with self.assertRaisesRegex(budget.BudgetError, 'live_admission_mismatch'):
            self.ledger.dispatch('ungated', 'count', 1, 102, live_gate=gate)
        self.count_done('ungated')
        with self.assertRaisesRegex(budget.BudgetError, 'live_admission_mismatch'):
            self.ledger.reserve_generation('ungated', 'g', 5, 1, 103, live_gate=gate)
        self.ledger.close_run('ungated')
        self.begin(live_gate=gate)
        with self.assertRaisesRegex(budget.BudgetError, 'live_run_id_already_used'):
            self.begin()
        with self.assertRaisesRegex(budget.BudgetError, 'live_admission_mismatch'):
            self.ledger.dispatch('run', 'count', 1, 102)
        self.ledger.dispatch('run', 'count', 1, 102, live_gate=gate)
        self.ledger.complete('run', 'count', 1)
        with self.assertRaisesRegex(budget.BudgetError, 'live_admission_mismatch'):
            self.ledger.reserve_generation('run', 'g', 5, 1, 103)

    def test_two_processes_cannot_begin_same_live_id(self):
        self.observe()
        ctx = multiprocessing.get_context('spawn'); queue = ctx.Queue()
        processes = [ctx.Process(target=competing_live, args=(self.path, self.identity, queue)) for _ in range(2)]
        for process in processes: process.start()
        for process in processes: process.join(10); self.assertEqual(process.exitcode, 0)
        self.assertEqual(sorted(queue.get(timeout=2) for _ in processes), ['accepted', 'live_run_id_already_used'])
        queue.close(); queue.join_thread()

    def test_all_ceiling_choices_and_no_accounting_reset_on_mode_change(self):
        self.observe(); self.begin(); self.count_done(); self.ledger.close_run('run')
        policy = self.ledger.snapshot()['organizations']['org']['policy'].copy()
        policy.update(ceiling='none', amount=None)
        self.ledger.configure('org', policy, 1)
        self.assertEqual(self.ledger.headroom('org'), 499)  # Pilot cap still applies.
        policy.update(ceiling='subscription_benefit', grant_amount=20)
        self.ledger.configure('org', policy, 2)
        self.assertEqual(self.ledger.headroom('org'), 19)
        with self.assertRaises(budget.BudgetError): self.ledger.configure('org', policy, 2)

    def test_optional_caps_are_independent_and_benefit_ceiling_still_applies(self):
        self.observe()
        policy = dict(self.ledger.snapshot()['organizations']['org']['policy'],
                      ceiling='subscription_benefit', amount=None, grant_amount=400)
        for revision, caps, maximum in ((1, dict(pilot_limit=None, task_limit=200), 201),
                                        (2, dict(pilot_limit=300, task_limit=None), 301)):
            self.ledger.configure('org', dict(policy, **caps), revision)
            with self.subTest(caps=caps), self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
                self.ledger.begin('org', 'task', 'run', 'packet', maximum, revision + 1, 101, native())

        self.ledger.configure('org', dict(policy, pilot_limit=None, task_limit=None), 3)
        self.ledger.begin('org', 'task', 'run', 'packet', 350, 4, 101, native())
        self.ledger.dispatch('run', 'count', 4, 102)
        self.ledger.complete('run', 'count', 350)
        self.ledger.close_run('run')
        self.assertEqual(self.ledger.headroom('org'), 50)
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.ledger.begin('org', 'task', 'next', 'packet', 51, 4, 103, native())

    def test_lower_grant_balance_revokes_a_reservation_with_both_optional_caps_omitted(self):
        self.observe()
        policy = dict(self.ledger.snapshot()['organizations']['org']['policy'], ceiling='subscription_benefit',
                      amount=None, pilot_limit=None, task_limit=None, allowance_bp=8000)  # protected 160
        self.ledger.configure('org', policy, 1)
        self.ledger.begin('org', 'task', 'run', 'packet', 300, 2, 101, native())
        self.ledger.observe('org', 459, 102)
        with self.assertRaisesRegex(budget.BudgetError, 'credit_changed'):
            self.ledger.dispatch('run', 'count', 2, 103)
        call = self.ledger.snapshot()['runs']['run']['calls']['count']
        self.assertEqual((call['state'], call['maximum']), ('reserved', 300))

    def test_new_cycle_retains_prior_cost_and_unknown_exposure(self):
        self.observe(); self.begin(); self.count_done(); self.ledger.close_run('run')
        policy = self.ledger.snapshot()['organizations']['org']['policy'].copy()
        policy.update(cycle='cycle2', start=10000, end=20000, expiry=20000, stop_at=19000)
        self.ledger.configure('org', policy, 1)
        self.assertEqual(self.ledger.snapshot()['runs']['run']['calls']['count']['cycle'], 'cycle1')
        self.assertEqual(self.ledger.credit_headroom('org'), 783)
        self.assertEqual(self.ledger.headroom('org'), 499)  # Pilot accounting does not reset.

    def test_refresh_can_revoke_an_existing_reservation_before_dispatch(self):
        self.observe(); self.begin()
        self.ledger.observe('org', 20, 102)  # Below the 2% protected share (16) plus the 10 held.
        with self.assertRaises(budget.BudgetError): self.ledger.dispatch('run', 'count', 1, 103)

    def test_missing_replaced_and_permissive_ledger_fail_closed(self):
        self.ledger.close()
        replacement = self.path.with_suffix('.old')
        self.path.rename(replacement)
        with self.assertRaises(budget.BudgetError): budget.Ledger(self.path, self.identity)
        new_identity = budget.initialize(self.path)
        with self.assertRaises(budget.BudgetError): budget.Ledger(self.path, self.identity)
        self.path.chmod(0o644)
        with self.assertRaises(budget.BudgetError): budget.Ledger(self.path, new_identity)
        self.path.chmod(0o600)
        self.ledger = budget.Ledger(self.path, new_identity)

    def test_correction_cannot_continue_after_task_completion(self):
        self.observe(); self.begin(); self.count_done(); self.ledger.close_run('run')
        evidence = native(); evidence['windows'][0]['observed_at'] = 105
        self.ledger.release_native('account', 'task', evidence, 106)
        with self.assertRaises(budget.BudgetError): self.begin('correction')

    def test_symlink_database_rejected(self):
        alias = self.path.parent / 'alias'; alias.symlink_to(self.path)
        with self.assertRaises(budget.BudgetError): budget.Ledger(alias, self.identity)


M = 1_000_000


class ParallelLedgerTests(unittest.TestCase):
    """Allowance policy, per-task padded claims and concurrency caps in integer micro-units."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'organizations.sqlite3'
        self.identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, self.identity)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def policy(self, **updates):
        policy = dict(cycle='cycle1', start=0, end=10000, expiry=10000, stop_at=9000, ceiling='none',
                      amount=None, grant_amount=0, pilot_limit=None, task_limit=None)
        policy.update(updates)
        return policy

    def org(self, name='org', balance=30 * M, issued=100 * M, revision=0, **policy):
        self.ledger.configure(name, self.policy(**policy), revision)
        self.ledger.observe(name, balance, 100, issued=issued)

    def begin(self, run, estimate, *, org='org', revision=1, now=101, **options):
        return self.ledger.begin(org, run, run, 'packet', estimate, revision, now, native(run), **options)

    def finish(self, run, actual, phase='count'):
        self.ledger.dispatch(run, phase, 1, 102)
        self.ledger.complete(run, phase, actual)

    # S1.1 allowance policy
    def test_allowance_policy_replaces_the_twenty_percent_floor(self):
        self.org()
        self.begin('held', 5 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 23 * M)  # 30 - ceil(2% of 100) - 5
        for bp, expected in ((0, 0), (10000, 25 * M), (9800, 23 * M)):
            with self.subTest(allowance_bp=bp):
                policy = self.ledger.snapshot()['organizations']['org']['policy']
                revision = self.ledger.snapshot()['organizations']['org']['revision']
                self.ledger.configure('org', dict(policy, allowance_bp=bp), revision)
                self.assertEqual(self.ledger.credit_headroom('org'), expected)

    def test_allowance_defaults_to_98_percent_and_rejects_invalid_values(self):
        self.org()
        self.assertEqual(self.ledger.credit_headroom('org'), 28 * M)
        for bad in (dict(allowance_bp=-1), dict(allowance_bp=10001), dict(allowance_bp=True),
                    dict(allowance='unlimited'), dict(allowance='no-limit', allowance_bp=9800)):
            with self.subTest(bad=bad), self.assertRaises(budget.BudgetError):
                self.ledger.configure('org', self.policy(**bad), 1)

    def test_protected_share_rounds_up_in_integers(self):
        self.org(balance=10 * M, issued=333_333, allowance_bp=9999)
        self.assertEqual(self.ledger.credit_headroom('org'), 10 * M - 34)  # ceil(1 * 333_333 / 10_000)

    def test_no_limit_uses_only_verified_eligible_lots(self):
        self.org(allowance='no-limit')
        self.ledger.observe('org', 30 * M, 101, lots=[
            dict(id='grant', balance=20 * M, expiry=10000, verified=True),
            dict(id='unknown', balance=10 * M, expiry=10000, verified=False)])
        self.assertEqual(self.ledger.credit_headroom('org'), 20 * M)
        self.begin('held', 5 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 15 * M)

    def test_no_limit_without_verified_lots_has_no_headroom(self):
        self.org(allowance='no-limit')
        self.assertEqual(self.ledger.credit_headroom('org'), 0)

    def test_billing_date_change_without_observed_issuance_mints_nothing(self):
        self.org()
        self.begin('held', 5 * M)
        before = self.ledger.credit_headroom('org')
        self.ledger.configure('org', self.policy(cycle='cycle2', start=10000, end=20000, expiry=20000,
                                                 stop_at=19000, grant_amount=500 * M), 1)
        data = self.ledger.snapshot()
        self.assertEqual(data['organizations']['org']['issued'], 100 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), before)
        self.assertEqual(data['runs']['held']['calls']['count']['state'], 'reserved')
        self.ledger.observe('org', 30 * M, 101, issued=120 * M)  # Only an observed issuance changes G.
        self.assertEqual(self.ledger.credit_headroom('org'), before - 400_000)

    # S1.2 per-task claims
    def test_new_state_has_no_slot_and_two_affordable_runs_both_begin(self):
        self.org()
        state = self.ledger.snapshot()
        self.assertNotIn('slot', state)
        self.assertEqual(state['version'], 3)
        self.begin('first', 5 * M); self.begin('second', 5 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 18 * M)

    def test_repeated_run_id_returns_existing_run_without_another_hold(self):
        self.org()
        first = self.begin('first', 5 * M)
        before = self.ledger.snapshot()
        again = self.begin('first', 5 * M)
        self.assertEqual(first, again)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_ledger_instances_racing_for_the_last_headroom_admit_what_fits(self):
        self.org(balance=500 * M, issued=500 * M, allowance_bp=10000, pilot_limit=500 * M)
        results = []

        def race(name):
            try:
                with budget.Ledger(self.path, self.identity) as ledger:
                    ledger.begin('org', name, name, 'packet', 300 * M, 1, 101, native(name))
                results.append('accepted')
            except budget.BudgetError as error:
                results.append(str(error))

        threads = [threading.Thread(target=race, args=(name,)) for name in ('a', 'b')]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertEqual(sorted(results), ['accepted', 'insufficient_headroom'])
        self.assertEqual(len(self.ledger.snapshot()['runs']), 1)

    def test_legacy_state_migrates_without_losing_exposure(self):
        self.org(balance=800, issued=800)
        self.begin('open', 10)
        with closing(sqlite3.connect(self.path)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data.update(version=1, slot='open')
            for org in data['organizations'].values():
                org['floor'] = 160; del org['issued']
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()
        migrated = self.ledger.snapshot()
        self.assertEqual(migrated['version'], 3)
        self.assertNotIn('slot', migrated)
        self.assertNotIn('floor', migrated['organizations']['org'])
        self.assertEqual(migrated['organizations']['org']['issued'], 800)
        self.assertEqual(migrated['runs']['open']['calls']['count']['maximum'], 10)
        self.ledger.observe('org', 790, 110)  # A write persists the migration.
        with closing(sqlite3.connect(self.path)) as db:
            stored = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
        self.assertEqual(stored['version'], 3)
        self.assertNotIn('slot', stored)

    # S1.3 padded reservations
    def test_reservations_are_padded_and_release_padding_on_completion(self):
        self.org(balance=30 * M, issued=30 * M, allowance_bp=10000)
        self.ledger.set_caps(total=10, per_account=10)
        for run, estimate, held in (('a', 5, 6), ('b', 10, 12), ('c', 8, 9.6)):
            self.begin(run, estimate * M, next_request_bound=0)
            self.assertEqual(self.ledger.snapshot()['runs'][run]['calls']['count']['maximum'], int(held * M))
        self.assertEqual(self.ledger.credit_headroom('org'), 2_400_000)
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.begin('d', 3 * M, next_request_bound=0)
        self.assertNotIn('d', self.ledger.snapshot()['runs'])
        self.finish('a', 4 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 4_400_000)
        self.begin('d', 3 * M, next_request_bound=0)
        self.assertEqual(self.ledger.snapshot()['runs']['d']['calls']['count']['maximum'], 3_600_000)

    def test_request_bound_can_exceed_the_padded_estimate_and_margin_is_configurable(self):
        self.org(allowance_bp=10000)
        self.begin('a', 5 * M, next_request_bound=7 * M)
        self.begin('b', 5 * M, next_request_bound=1, margin_bp=500)
        calls = self.ledger.snapshot()['runs']
        self.assertEqual(calls['a']['calls']['count']['maximum'], 7 * M)
        self.assertEqual(calls['b']['calls']['count']['maximum'], 5_250_000)

    def test_unpadded_legacy_bound_and_free_pricing_never_divide_or_hold(self):
        self.org(allowance_bp=10000)
        self.begin('bounded', 3 * M)
        self.assertEqual(self.ledger.snapshot()['runs']['bounded']['calls']['count']['maximum'], 3 * M)
        self.begin('free', 0, next_request_bound=0)
        self.assertEqual(self.ledger.snapshot()['runs']['free']['calls']['count']['maximum'], 0)
        self.finish('free', 0)
        self.assertEqual(self.ledger.credit_headroom('org'), 27 * M)
        self.org('empty', balance=0, issued=0)
        self.assertEqual(self.ledger.credit_headroom('empty'), 0)

    def test_timeout_keeps_the_full_hold_and_late_completion_charges_actual(self):
        self.org(allowance_bp=10000)
        self.begin('slow', 5 * M, next_request_bound=0)
        self.ledger.dispatch('slow', 'count', 1, 102)
        self.ledger.uncertain('slow', 'count')
        self.assertEqual(self.ledger.credit_headroom('org'), 24 * M)
        self.assertEqual(self.ledger.reconcile(scope='x'), {'state': 'manual_recovery_required'})
        self.ledger.complete('slow', 'count', 4 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 26 * M)

    def test_top_up_is_atomic_and_fails_without_changing_state(self):
        self.org(balance=10 * M, issued=10 * M, allowance_bp=10000)
        self.begin('a', 4 * M, next_request_bound=0)
        self.assertEqual(self.ledger.snapshot()['runs']['a']['calls']['count']['maximum'], 4_800_000)
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.ledger.top_up('a', 6 * M, 1, 102)
        self.assertEqual(self.ledger.snapshot(), before)
        self.ledger.top_up('a', 5 * M, 1, 102)
        self.assertEqual(self.ledger.snapshot()['runs']['a']['calls']['count']['maximum'], 9_800_000)
        self.assertEqual(self.ledger.credit_headroom('org'), 200_000)
        for run, revision in (('missing', 1), ('a', 2)):
            with self.assertRaises((budget.BudgetError, KeyError)):
                self.ledger.top_up(run, 1, revision, 102)

    def test_top_up_requires_an_open_reservation(self):
        self.org(allowance_bp=10000)
        self.begin('a', 1 * M); self.finish('a', 1 * M); self.ledger.close_run('a')
        with self.assertRaisesRegex(budget.BudgetError, 'top_up_forbidden'):
            self.ledger.top_up('a', 1, 1, 103)

    def test_parent_provision_transferred_to_a_child_is_counted_once(self):
        self.org(allowance_bp=10000)
        self.ledger.set_caps(total=10, per_account=10)
        self.begin('parent', 5 * M, next_request_bound=0, provision=2 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 24 * M)
        self.begin('child', 1 * M, next_request_bound=0, transfer_from=('parent', M))
        self.assertEqual(self.ledger.credit_headroom('org'), 23_800_000)  # 6 + 1.2 - 1, not 7.2
        calls = self.ledger.snapshot()['runs']
        self.assertEqual(calls['parent']['calls']['count']['maximum'], 5 * M)
        self.assertEqual(calls['parent']['calls']['count']['provision'], M)
        self.begin('child', 1 * M, next_request_bound=0, transfer_from=('parent', M))  # Replay moves nothing.
        self.assertEqual(self.ledger.credit_headroom('org'), 23_800_000)
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'provision_exceeded'):
            self.begin('greedy', 1 * M, transfer_from=('parent', 2 * M))
        self.assertEqual(self.ledger.snapshot(), before)

    # S1.4 concurrency caps
    def test_default_caps_limit_open_runs_per_account_and_in_total(self):
        self.org('org'); self.org('other', revision=0)
        self.begin('a', M); self.begin('b', M)
        with self.assertRaisesRegex(budget.BudgetError, 'concurrency_cap'):
            self.begin('c', M)
        self.begin('d', M, org='other')
        self.ledger.close_run('a')
        self.begin('c', M)
        self.begin('e', M, org='other')
        with self.assertRaisesRegex(budget.BudgetError, 'concurrency_cap'):
            self.begin('f', M, org='other')

    def test_total_cap_counts_every_account_and_caps_are_configurable(self):
        for name in ('o1', 'o2', 'o3'): self.org(name)
        self.ledger.set_caps(total=2, per_account=2)
        self.begin('a', M, org='o1'); self.begin('b', M, org='o2')
        with self.assertRaisesRegex(budget.BudgetError, 'concurrency_cap'):
            self.begin('c', M, org='o3')
        self.ledger.set_caps(total=3, per_account=1)
        self.begin('c', M, org='o3')
        with self.assertRaises(budget.BudgetError): self.ledger.set_caps(total=0, per_account=1)

    def test_unresolved_runs_keep_their_place_until_closed(self):
        self.org()
        self.ledger.set_caps(total=1, per_account=1)
        self.begin('a', M); self.ledger.dispatch('a', 'count', 1, 102); self.ledger.uncertain('a', 'count')
        with self.assertRaisesRegex(budget.BudgetError, 'concurrency_cap'):
            self.begin('b', M)
        self.assertEqual(len(self.ledger.snapshot()['runs']), 1)

    def test_replay_of_an_open_run_is_not_blocked_by_the_caps(self):
        self.org()
        self.ledger.set_caps(total=1, per_account=1)
        self.begin('a', M)
        self.assertEqual(self.begin('a', M)['task'], 'a')

    # S2.6 multi-pool holds and persisted admission records
    def raw(self, mutate):
        with closing(sqlite3.connect(self.path)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            mutate(data)
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()

    def record(self, attempt, **extra):
        return dict(attempt_id=attempt, task=attempt, bucket=['implementer', 'medium'], shares=dict(api=1.0, subscription=0.0),
                    route='r', **extra)

    def decide(self, attempt, vector, account='acct'):
        return lambda data: dict(record=self.record(attempt), vector=vector, account=account)

    def test_version_two_state_migrates_and_keeps_in_flight_claims(self):
        self.org()
        self.begin('open', 5 * M)
        self.raw(lambda data: (data.update(version=2), data.pop('holds'), data.pop('admissions')))
        migrated = self.ledger.snapshot()
        self.assertEqual((migrated['version'], migrated['holds'], migrated['admissions']), (3, {}, []))
        self.assertEqual(migrated['runs']['open']['calls']['count']['maximum'], 5 * M)
        self.assertEqual(self.ledger.credit_headroom('org'), 23 * M)  # 30 - 2% protected - 5 held
        self.ledger.admit('new', self.decide('new', {'win:pool/w': 5}))  # A write persists the migration.
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])['version'], 3)

    def test_admit_writes_hold_and_record_together_and_duplicates_return_the_record(self):
        first = self.ledger.admit('a', self.decide('a', {'win:p/w': 7}))
        before = self.ledger.snapshot()
        self.assertEqual(before['holds']['a']['windows'], {'win:p/w': 7})
        self.assertEqual(self.ledger.held(before), {'win:p/w': 7})
        again = self.ledger.admit('a', self.decide('a', {'win:p/w': 7}))
        self.assertEqual(again, dict(first, duplicate=True))
        self.assertEqual(self.ledger.snapshot(), before)

    def test_blocked_and_failed_decisions_change_nothing(self):
        before = self.ledger.snapshot()
        self.assertEqual(self.ledger.admit('a', lambda data: dict(status='pending')), dict(status='pending'))
        def boom(data): raise budget.BudgetError('boom')
        with self.assertRaisesRegex(budget.BudgetError, 'boom'):
            self.ledger.admit('b', boom)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_closing_an_attempt_releases_its_hold_and_its_concurrency_place(self):
        self.ledger.set_caps(total=1, per_account=1)
        self.ledger.admit('a', self.decide('a', {'win:p/w': 7}, 'org'))
        self.org()
        with self.assertRaisesRegex(budget.BudgetError, 'concurrency_cap'):
            self.begin('run', M)  # a hold counts against the same caps as a run
        self.ledger.close_attempt('a', 'not_dispatched')
        self.assertEqual(self.ledger.held(self.ledger.snapshot()), {})
        self.begin('run', M)
        with self.assertRaisesRegex(budget.BudgetError, 'attempt_missing'):
            self.ledger.close_attempt('nope', 'complete')

    def test_admission_records_are_bounded_and_never_prune_open_holds(self):
        for i in range(budget.MAX_ADMISSIONS + 5):
            self.ledger.admit('a%d' % i, self.decide('a%d' % i, {}))
            self.ledger.close_attempt('a%d' % i, 'not_dispatched')
        state = self.ledger.snapshot()
        self.assertEqual(len(state['admissions']), budget.MAX_ADMISSIONS)
        self.assertEqual(state['admissions'][-1]['attempt_id'], 'a%d' % (budget.MAX_ADMISSIONS + 4))

    # S2.6 review follow-up: hold states, reuse and hold-to-run conversion
    def test_only_established_outcomes_release_a_hold(self):
        self.ledger.admit('a', self.decide('a', {'win:p/w': 7}))
        for bad in ('timeout', None, 'released', 3):
            with self.subTest(bad=bad), self.assertRaisesRegex(budget.BudgetError, 'invalid_outcome'):
                self.ledger.close_attempt('a', bad)
        self.ledger.mark_dispatched('a')
        with self.assertRaisesRegex(budget.BudgetError, 'close_forbidden'):
            self.ledger.close_attempt('a', 'not_dispatched')  # a dispatched attempt did run
        self.ledger.close_attempt('a', 'uncertain')
        self.assertEqual(self.ledger.snapshot()['holds']['a']['state'], 'uncertain')
        self.assertEqual(self.ledger.held(self.ledger.snapshot()), {'win:p/w': 7})
        self.ledger.close_attempt('a', 'complete')
        self.assertEqual(self.ledger.held(self.ledger.snapshot()), {})
        with self.assertRaisesRegex(budget.BudgetError, 'close_forbidden'):
            self.ledger.close_attempt('a', 'complete')  # already released

    def test_hold_state_transitions_are_ordered(self):
        self.ledger.admit('a', self.decide('a', {}))
        with self.assertRaisesRegex(budget.BudgetError, 'close_forbidden'):
            self.ledger.close_attempt('a', 'complete')  # never dispatched
        self.ledger.mark_uncertain('a')
        with self.assertRaisesRegex(budget.BudgetError, 'hold_state_forbidden'):
            self.ledger.mark_dispatched('a')

    def test_recent_attempt_ids_stay_rejected_after_their_records_are_pruned(self):
        for i in range(budget.MAX_ADMISSIONS + 3):
            self.ledger.admit('a%d' % i, self.decide('a%d' % i, {}))
            self.ledger.close_attempt('a%d' % i, 'not_dispatched')
        self.assertIsNone(self.ledger.admission(self.ledger.snapshot(), 'a0'))
        with self.assertRaisesRegex(budget.BudgetError, 'attempt_id_in_use'):
            self.ledger.admit('a0', self.decide('a0', {}))
        self.assertLessEqual(len(self.ledger.snapshot()['seen_attempts']), budget.MAX_SEEN)

    def test_hold_converts_to_a_run_claim_counted_once(self):
        self.org(balance=30 * M, issued=30 * M, allowance_bp=10000)
        self.ledger.set_caps(total=1, per_account=1)
        self.ledger.admit('a', lambda data: dict(record=self.record('a'), vector={'org:org': 6 * M, 'win:sub/5h': 12},
                                                 account='acct', now=101, revision=1))
        self.assertEqual(self.ledger.credit_headroom('org'), 24 * M)  # the hold is part of R before conversion
        self.assertEqual(budget.Ledger.open_accounts(self.ledger.snapshot()), ['acct'])
        run = self.ledger.convert_hold('a', 'org', 'packet', 5 * M, 1, 101, native('a'), next_request_bound=0)
        self.assertEqual((run['calls']['count']['maximum'], run['account']), (6 * M, 'acct'))
        state = self.ledger.snapshot()
        self.assertEqual(budget.Ledger.open_accounts(state), ['acct'])  # one place: the attempt, not run plus hold
        self.assertEqual(self.ledger.held(state), {'win:sub/5h': 12})  # the dollar amount moved to the run
        self.assertEqual(self.ledger.credit_headroom('org'), 24 * M)
        self.assertEqual((state['holds']['a']['state'], state['holds']['a']['amount']), ('admitted', 0))
        with self.assertRaisesRegex(budget.BudgetError, 'convert_forbidden'):
            self.ledger.convert_hold('a', 'org', 'packet', 5 * M, 1, 101, native('a'))

    def test_dollar_holds_pass_the_same_gates_as_begin(self):
        self.org(balance=30 * M, issued=30 * M, allowance_bp=10000)
        decide = lambda vector, revision=1, now=101: (lambda data: dict(  # noqa: E731
            record=self.record('a'), vector=vector, account='acct', now=now, revision=revision))
        for vector, revision, now, reason in (({'org:org': 31 * M}, 1, 101, 'insufficient_headroom'),
                                              ({'org:ghost': M}, 1, 101, 'organization_unconfigured'),
                                              ({'org:org': M}, 2, 101, 'policy_revision_changed'),
                                              ({'org:org': M}, 1, 5000, 'balance_stale_or_missing'),
                                              ({'org:org': M, 'org:other': M}, 1, 101, 'invalid_admission'),
                                              ({'pool/w': 1}, 1, 101, 'invalid_admission'),
                                              ({'win:p/w': 10**30}, 1, 101, 'invalid_integer')):
            with self.subTest(vector=vector, reason=reason), self.assertRaisesRegex(budget.BudgetError, reason):
                self.ledger.admit('a', decide(vector, revision, now))
        self.assertEqual(self.ledger.snapshot()['holds'], {})
        self.ledger.admit('a', decide({'org:org': 30 * M}))
        self.assertEqual(self.ledger.headroom('org'), 0)
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.begin('run', 1)  # S1 begin sees the hold (holds are part of R)

    def test_faulty_decisions_are_refused_by_the_ledger_itself(self):
        """Ledger.admit re-checks a decision; each case below fails if its check is removed."""
        self.org(balance=30 * M, issued=30 * M, allowance_bp=10000)

        def decide(vector, account='acct', **outcome):
            values = dict(now=101, revision=1)
            values.update(outcome)
            return lambda data: dict(record=self.record('x'), vector=vector, account=account,
                                     **{k: v for k, v in values.items() if v is not None})
        cases = dict(overdraw=(decide({'org:org': 30 * M + 1}), 'insufficient_headroom'),
                     stale=(decide({'org:org': M}, now=100 + budget.FRESH_SECONDS + 1), 'balance_stale_or_missing'),
                     revision=(decide({'org:org': M}, revision=7), 'policy_revision_changed'),
                     missing_now=(decide({'org:org': M}, now=None), 'invalid_integer'),
                     two_orgs=(decide({'org:org': M, 'org:org2': M}), 'invalid_admission'),
                     negative_window=(decide({'win:p/w': -1}), 'invalid_integer'))
        for label, (faulty, reason) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(budget.BudgetError, reason):
                self.ledger.admit('x', faulty)
        self.ledger.set_caps(total=2, per_account=1)
        self.begin('run', M)  # counts under its organization
        for label, account in (('per_account', 'org'), ('total', 'other')):
            if label == 'total':
                self.ledger.admit('y', lambda data: dict(record=self.record('y'), vector={}, account='third'))
            with self.subTest(label), self.assertRaisesRegex(budget.BudgetError, 'concurrency_cap'):
                self.ledger.admit('x', decide({}, account=account))
        self.assertEqual(sorted(self.ledger.snapshot()['holds']), ['y'])

    def test_written_ledgers_with_the_old_hold_shape_are_repaired(self):
        def old(data):
            data['holds'] = {'x': dict(account='acct', task='t', vector={'p/w': 7}, closed=False)}
            data.pop('seen_attempts')
        self.raw(old)
        state = self.ledger.snapshot()
        self.assertEqual(state['holds']['x']['state'], 'admitted')
        self.ledger.close_attempt('x', 'not_dispatched')  # no raw KeyError on a pre-fix hold
        self.assertEqual(state['seen_attempts'], [])
        self.assertEqual(state['holds']['x']['windows'], {'win:p/w': 7})
        self.assertEqual((state['holds']['x']['organization'], state['holds']['x']['amount']), (None, 0))

    def test_pruning_never_drops_open_or_uncertain_holds(self):
        with self.ledger.transaction() as data:
            for i in range(budget.MAX_HOLDS - 1):
                data['holds']['c%d' % i] = dict(account='a', task='t', organization=None, amount=0, cycle=None,
                                                windows={}, closed=True, state='released')
            data['holds']['u'] = dict(account='a', task='t', organization=None, amount=0, cycle=None,
                                      windows={'win:p/w': 1}, closed=False, state='uncertain')
        self.ledger.admit('n', self.decide('n', {}))
        holds = self.ledger.snapshot()['holds']
        self.assertIn('u', holds)
        self.assertIn('n', holds)
        self.assertLess(len(holds), budget.MAX_HOLDS)

if __name__ == '__main__': unittest.main()
