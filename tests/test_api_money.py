"""F5.A2: API-money storage guards, the whole-task envelope, unpriced-work recovery and typed balance coverage.

Disposable ledgers and fake transports only; nothing here qualifies a coverage producer or a live route.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from runtime import usage_budget as budget, usage_routing as routing
from tests.test_smart_balance import KEYCHAIN_BINDING, POLICY, task
from tests.test_usage_routing import PROFILE


def body_size(path):
    with closing(sqlite3.connect(path)) as db:
        return len(db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0])


class Store(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'ledger'
        self.ledger = budget.Ledger(self.path, budget.initialize(self.path))
        self.addCleanup(self.ledger.close)
        self.controller = routing.Controller(self.ledger, 'scope', clock=lambda: 101)
        self.controller.configure(dict(version=2, expected_revision=0, expected_org_revision=0,
                                       binding=KEYCHAIN_BINDING, policy=POLICY, profile=PROFILE,
                                       class_name='investigation', mode='smart'))
        self.ledger.observe('org', 800, 100)


def native_of(task_id, account='acct', windows=()):
    return dict(account=account, task=task_id, windows=list(windows))


class MetadataGrowthTests(Store):
    """Handoff records and handoff claims add table rows: they use the growing-write admission (SOFT_RECORD), so
    queued metadata can never consume the headroom that settlement of already-admitted work needs."""

    def handoff(self, task_id, payload=0, account='acct'):
        windows = ['w' * payload] if payload else []
        return self.controller.request_handoff(task(task_id=task_id, run_id=task_id,
                                                    native=native_of(task_id, account, windows)),
                                               'insufficient_headroom')

    def fill(self):
        """Fill with valid handoffs, large then small, until even a small one is refused."""
        for payload in (60000, 8000, 1000, 0):
            for i in range(400):
                try:
                    self.handoff('fill-%d-%d' % (payload, i), payload)
                except budget.BudgetError as error:
                    self.assertEqual(str(error), 'ledger_capacity_reached')
                    break
            else:
                self.fail('handoffs never reached the growing-write limit')
            self.assertLessEqual(body_size(self.path), budget.SOFT_RECORD)

    def test_handoff_metadata_stops_at_the_soft_limit_and_admitted_work_still_settles(self):
        self.ledger.begin('org', 'task', 'run', 'packet', 10, 1, 101, native_of('task'))
        self.ledger.dispatch('run', 'count', 1, 101)
        target = self.handoff('claimable', account='a' * 256)
        self.assertEqual(target['state'], 'ready')
        self.fill()
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
            self.controller.claim_handoff('claimable', 'c' * 256)  # the permit would add native authority
        self.assertEqual(self.ledger.snapshot(), before)  # atomic: no claim, no native key, no permit
        self.assertIsNone(before['handoffs'][target['handoff_id']]['claim_attempt'])
        self.ledger.complete('run', 'count', 7)  # settlement of admitted work still commits past the fill
        self.ledger.close_run('run')
        self.assertTrue(self.ledger.snapshot()['runs']['run']['closed'])
        replay = self.handoff('claimable', account='a' * 256)  # an unchanged replay writes nothing and succeeds
        self.assertEqual(replay['handoff_id'], target['handoff_id'])

    def test_small_handoff_replay_and_claim_still_work(self):
        first = self.handoff('task')
        self.assertEqual(self.handoff('task')['handoff_id'], first['handoff_id'])
        self.assertTrue(self.controller.claim_handoff('task', 'child')['new_claim'])
        self.assertFalse(self.controller.claim_handoff('task', 'child')['new_claim'])

    def test_native_metadata_is_bounded_by_the_input_byte_contract(self):
        with self.assertRaisesRegex(budget.BudgetError, 'invalid_handoff_native'):
            self.handoff('huge', routing.MAX_INPUT)
        self.assertNotIn('handoffs', self.ledger.snapshot())
        self.assertEqual(self.handoff('fits', routing.MAX_INPUT - 200)['state'], 'ready')



M = 1_000_000


def plain_native(task_id):
    return dict(account='account', task=task_id, windows=[])


class Money(unittest.TestCase):
    """A ledger with $50 available after the default 2% protected allowance (issued $100, balance $52)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private' / 'ledger'
        self.identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, self.identity)
        self.addCleanup(self.ledger.close)
        self.ledger.configure('org', dict(cycle='c1', start=0, end=10000, expiry=10000, stop_at=9000, ceiling='none',
                                          amount=None, grant_amount=0, pilot_limit=None, task_limit=None), 0)
        self.ledger.observe('org', 52 * M, 100, issued=100 * M)

    def envelope(self, run='run', estimate=10 * M, count=M, **options):
        return self.ledger.reserve_task('org', run, run, 'packet', estimate, count, 1, 101, plain_native(run), **options)

    def held(self):
        return budget.Ledger._total(self.ledger.snapshot(), 'org')

    def calls(self, run='run'):
        return self.ledger.snapshot()['runs'][run]['calls']


class EnvelopeTests(Money):
    def test_whole_task_envelope_holds_twelve_of_fifty_and_leaves_thirty_eight(self):
        self.assertEqual(self.ledger.credit_headroom('org', 101), 50 * M)
        self.envelope()
        self.assertEqual((self.held(), self.ledger.credit_headroom('org', 101)), (12 * M, 38 * M))
        calls = self.calls()
        self.assertEqual((calls['count']['maximum'], calls['generation']['maximum']), (M, 11 * M))
        self.assertEqual((calls['generation']['state'], calls['generation']['cost'], calls['generation']['digest']),
                         ('reserved', None, None))
        self.assertIs(self.ledger.snapshot()['runs']['run']['envelope'], True)

    def test_one_micro_unit_rounds_up(self):
        self.envelope(estimate=1, count=1)
        self.assertEqual(self.held(), 2)

    def test_the_generation_grant_keeps_the_buffer_and_refuses_an_underestimate(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        with self.assertRaisesRegex(budget.BudgetError, 'envelope_exceeded'):
            self.ledger.reserve_call('run', 'generation', 'packet2', 10 * M, 1, 101)  # 12M buffered > 11M
        self.ledger.reserve_call('run', 'generation', 'packet2', 9 * M, 1, 101)  # 10.8M buffered fits
        self.assertEqual((self.held(), self.calls()['generation']['maximum']), (12 * M, 11 * M))  # never lowered
        self.ledger.reserve_call('run', 'generation', 'packet2', 9 * M, 1, 101)  # the same grant: no change
        self.assertEqual(self.held(), 12 * M)
        self.ledger.dispatch('run', 'generation', 1, 101)
        self.ledger.complete('run', 'generation', 8 * M)  # a known actual charges once and releases the rest
        self.assertEqual(self.held(), M + 8 * M)  # the count stays unpriced at its maximum

    def test_unsent_count_cancels_both_phases(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.reject_unsent('run', 'count')
        calls = self.calls()
        self.assertEqual({c['state'] for c in calls.values()}, {'rejected-before-send'})
        self.assertEqual(self.held(), 0)
        self.assertTrue(self.ledger.snapshot()['runs']['run']['closed'])

    def test_count_done_and_generation_never_sent_keeps_only_the_count(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        self.ledger.close_run('run')
        self.assertEqual(self.calls()['generation']['state'], 'rejected-before-send')
        self.assertEqual(self.held(), M)  # unknown count billing stays at its full bound

    def test_placeholder_never_dispatches_and_uncertainty_keeps_everything(self):
        self.envelope()
        with self.assertRaisesRegex(budget.BudgetError, 'call_not_granted'):
            self.ledger.dispatch('run', 'generation', 1, 101)
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.uncertain('run', 'count')
        with self.assertRaisesRegex(budget.BudgetError, 'request_may_be_running'):
            self.ledger.close_run('run')
        self.assertEqual(self.held(), 12 * M)

    def test_duplicate_returns_the_same_envelope_and_a_legacy_run_never_becomes_one(self):
        self.envelope()
        self.envelope()
        self.assertEqual(self.held(), 12 * M)
        self.ledger.begin('org', 'legacy', 'legacy', 'packet', M, 1, 101, plain_native('legacy'))
        with self.assertRaisesRegex(budget.BudgetError, 'run_identity_changed'):
            self.envelope('legacy')
        with self.assertRaisesRegex(budget.BudgetError, 'estimate_below_count_bound'):
            self.envelope('small', estimate=M, count=2 * M)

    def test_funds_still_refuse_and_old_generation_reservation_cannot_reach_the_placeholder(self):
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.envelope(estimate=42 * M)  # 50.4M buffered > 50M
        self.assertEqual(self.ledger.snapshot()['runs'], {})
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        with self.assertRaisesRegex(budget.BudgetError, 'attempt_exists'):  # the pre-A2 runner's own call
            self.ledger.reserve_generation('run', 'packet2', M, 1, 101)

    def test_top_up_and_transfer_follow_the_current_phase(self):
        self.envelope()
        self.ledger.top_up('run', M, 1, 101)  # count reserved: the count is current
        self.assertEqual(self.calls()['count']['maximum'], 2 * M)
        with self.assertRaisesRegex(budget.BudgetError, 'transfer_forbidden'):
            self.ledger.begin('org', 'child', 'child', 'packet', M, 1, 101, plain_native('child'),
                              transfer_from=('run', 1))
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.uncertain('run', 'count')
        with self.assertRaisesRegex(budget.BudgetError, 'top_up_forbidden'):
            self.ledger.top_up('run', M, 1, 101)  # uncertain count: no current phase
        self.ledger.complete_unknown('run', 'count')
        with self.assertRaisesRegex(budget.BudgetError, 'top_up_forbidden'):
            self.ledger.top_up('run', M, 1, 101)  # ungranted placeholder: no increase of review funds
        self.ledger.reserve_call('run', 'generation', 'packet2', M, 1, 101)
        self.ledger.top_up('run', M, 1, 101)
        self.assertEqual(self.calls()['generation']['maximum'], 12 * M)

    def test_synchronized_contenders_share_one_authority(self):
        import threading
        outcomes, barrier = [], threading.Barrier(6)

        def contend(index):
            with budget.Ledger(self.path, self.identity) as handle:  # one connection per worktree/client
                barrier.wait(timeout=10)
                try:
                    handle.reserve_task('org', 't%d' % index, 'r%d' % index, 'packet', 10 * M, M, 1, 101,
                                        plain_native('t%d' % index))
                    outcomes.append('admitted')
                except budget.BudgetError as error:
                    outcomes.append(str(error))
        threads = [threading.Thread(target=contend, args=(i,)) for i in range(6)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(outcomes), ['admitted'] * 4 + ['insufficient_headroom'] * 2)
        self.assertEqual(self.held(), 48 * M)


class UnpricedRecoveryTests(Money):
    def settle(self, event_id, phase, evidence):
        from runtime import ledger_versions
        return ledger_versions.settle(self.ledger, dict(event_id=event_id, run_id='run', phase=phase, evidence=evidence,
                                                        kind=ledger_versions.KIND), 200)

    def test_pricing_the_count_while_generation_runs_keeps_the_generation_and_the_run(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        self.ledger.reserve_call('run', 'generation', 'packet2', M, 1, 101)
        self.ledger.dispatch('run', 'generation', 1, 101)
        receipts = dict(kind='receipts', receipts=['r1'], cost_usd_micros=300_000)
        outcome = self.settle('e1', 'count', receipts)
        self.assertEqual((outcome['state'], outcome['charged']), ('recorded', 300_000))
        run = self.ledger.snapshot()['runs']['run']
        self.assertFalse(run['closed'])
        self.assertEqual((run['calls']['count']['cost'], run['calls']['generation']['state']), (300_000, 'dispatching'))
        self.assertTrue(self.settle('e1', 'count', receipts)['duplicate'])
        self.assertEqual(self.settle('e2', 'count', dict(kind='never_dispatched'))['reason'], 'call_closed')
        self.assertEqual(self.held(), 300_000 + 11 * M)

    def test_never_dispatched_cannot_price_completed_work(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        self.assertEqual(self.settle('e1', 'count', dict(kind='never_dispatched'))['reason'], 'call_state_forbidden')
        outcome = self.settle('e2', 'count', dict(kind='charge_reservation'))
        self.assertEqual(outcome['charged'], M)
        from runtime import ledger_versions
        listed = ledger_versions.outstanding(self.ledger.snapshot())['calls']['items']
        self.assertEqual([(i['phase'], i['state']) for i in listed], [('generation', 'reserved')])  # still open

    def test_outstanding_lists_unpriced_work_even_on_a_closed_run(self):
        from runtime import ledger_versions
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        self.ledger.close_run('run')
        items = ledger_versions.outstanding(self.ledger.snapshot())['calls']['items']
        self.assertEqual(items, [dict(run_id='run', phase='count', state='completed', maximum=M, over_bound=False,
                                      unpriced=True)])

    def test_terminal_recovery_of_an_uncertain_count_cancels_only_the_unsent_placeholder(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.uncertain('run', 'count')
        self.settle('e1', 'count', dict(kind='charge_reservation'))
        calls = self.calls()
        self.assertEqual((calls['count']['cost'], calls['generation']['state']), (M, 'rejected-before-send'))
        self.assertEqual(self.held(), M)


class CoverageTests(Money):
    """Synthetic: the test stages the observation itself; no producer is qualified by these tests."""

    def stage(self, balance, observed_at=150):
        from dataclasses import asdict
        from runtime import usage_admission
        binding = dict(organization='org', project='project', credential_ref='OPENAI_API_KEY')
        grant = usage_admission.Grant(organization='org', project='project', balance=balance, expiry=10000,
                                      observed_at=observed_at, source='platform-credit-grants', pool='prepaid')
        with self.ledger.transaction() as data:
            data.setdefault('routing', {})['scope'] = dict(revision=3, binding=binding)
            org = data['organizations']['org']
            self.ledger._note_balance(org, balance)
            org['observed_at'] = observed_at
            org['live_funding'] = dict(observed_at=observed_at, digest=usage_admission.digest(asdict(grant)))
        return grant, usage_admission.digest(binding)

    def coverage(self, grant, binding_digest, *charges, scope='scope', revision=3):
        from runtime import usage_admission
        return usage_admission.Coverage(scope=scope, config_revision=revision, binding_digest=binding_digest,
                                        grant=grant, charges=tuple(usage_admission.ChargeRef(*c) for c in charges))

    def spend(self, run, actual):
        self.ledger.begin('org', run, run, 'packet', 10 * M, 1, 101, plain_native(run))
        self.ledger.dispatch(run, 'count', 1, 101)
        self.ledger.complete(run, 'count', actual)

    def test_actual_eight_then_provider_forty_two_covering_it_stays_forty_two(self):
        self.spend('a', 8 * M)
        self.assertEqual(self.ledger.credit_headroom('org', 150), 42 * M)
        grant, digest = self.stage(44 * M)  # the provider balance now reflects the $8
        self.assertEqual(self.ledger.credit_headroom('org', 150), 34 * M)  # without coverage it counts twice
        total = self.held()
        self.assertEqual(self.ledger.apply_coverage(self.coverage(grant, digest, ('run', 'a', 'count', 8 * M)), 150),
                         dict(state='recorded', marked=1, duplicate=0))
        self.assertEqual(self.ledger.credit_headroom('org', 150), 42 * M)
        self.assertEqual(self.held(), total)  # task, cycle, pilot and live authority still count the spend
        replay = self.ledger.apply_coverage(self.coverage(grant, digest, ('run', 'a', 'count', 8 * M)), 150)
        self.assertEqual((replay['duplicate'], self.ledger.credit_headroom('org', 150)), (1, 42 * M))

    def test_settled_hold_coverage_persists_in_the_subtotal(self):
        self.ledger.admit('h', lambda data: dict(record=dict(attempt_id='h', task='t', bucket=[], shares={}),
                                                 vector={'org:org': 10 * M}, account='acct', now=101, revision=1))
        with self.ledger.transaction() as data:  # the S3.6 settlement owner, as record_event uses it
            self.ledger._settle_cost(data, 'h', data['holds']['h'], 8 * M, 8 * M, 'provider_receipt')
        grant, digest = self.stage(44 * M)
        self.assertEqual(self.ledger.credit_headroom('org', 150), 34 * M)
        self.ledger.apply_coverage(self.coverage(grant, digest, ('hold', 'h', None, 8 * M)), 150)
        state = self.ledger.snapshot()
        self.assertEqual((state['organizations']['org']['settled']['covered'],
                          self.ledger.credit_headroom('org', 150)), (8 * M, 42 * M))
        self.assertTrue(state['holds']['h']['settlement']['covered'])
        self.ledger.apply_coverage(self.coverage(grant, digest, ('hold', 'h', None, 8 * M)), 150)  # replay
        self.assertEqual(self.ledger.snapshot()['organizations']['org']['settled']['covered'], 8 * M)

    def test_partial_subset_mismatch_staleness_and_untrusted_input(self):
        from runtime import usage_admission
        self.spend('a', 8 * M); self.spend('b', 2 * M)
        grant, digest = self.stage(42 * M)
        cases = dict(
            amount=(self.coverage(grant, digest, ('run', 'a', 'count', 7 * M)), 'coverage_charge_mismatch'),
            binding=(self.coverage(grant, 'f' * 64, ('run', 'a', 'count', 8 * M)), 'coverage_binding_changed'),
            revision=(self.coverage(grant, digest, ('run', 'a', 'count', 8 * M), revision=4), 'coverage_config_changed'),
            observation=(self.coverage(usage_admission.Grant(organization='org', project='project', balance=1,
                         expiry=10000, observed_at=150, source='platform-credit-grants', pool='prepaid'), digest,
                         ('run', 'a', 'count', 8 * M)), 'coverage_observation_changed'),
            json=(dict(scope='scope', charges=[]), 'trusted_coverage_required'))
        before = self.ledger.snapshot()
        for label, (value, reason) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(budget.BudgetError, reason):
                self.ledger.apply_coverage(value, 150)
        with self.assertRaisesRegex(budget.BudgetError, 'observation_stale'):
            self.ledger.apply_coverage(self.coverage(grant, digest, ('run', 'a', 'count', 8 * M)), 150 + 901)
        self.assertEqual(self.ledger.snapshot(), before)  # every refusal wrote nothing
        self.ledger.apply_coverage(self.coverage(grant, digest, ('run', 'a', 'count', 8 * M)), 150)  # a subset
        self.assertEqual(self.ledger.credit_headroom('org', 150), 40 * M - 2 * M)  # b's $2 still counted

    def test_unknown_uncertain_and_saturated_charges_cover_nothing(self):
        self.envelope('u')
        self.ledger.dispatch('u', 'count', 1, 101)
        self.ledger.complete_unknown('u', 'count')
        self.ledger.begin('org', 'q', 'q', 'packet', M, 1, 101, plain_native('q'))
        self.ledger.dispatch('q', 'count', 1, 101)
        self.ledger.uncertain('q', 'count')
        self.ledger.admit('h', lambda data: dict(record=dict(attempt_id='h', task='t', bucket=[], shares={}),
                                                 vector={'org:org': M}, account='acct', now=101, revision=1))
        grant, digest = self.stage(30 * M)
        for charge in (('run', 'u', 'count', M), ('run', 'q', 'count', M)):
            with self.subTest(charge), self.assertRaisesRegex(budget.BudgetError, 'coverage_charge_mismatch'):
                self.ledger.apply_coverage(self.coverage(grant, digest, charge), 150)
        with self.ledger.transaction() as data:
            self.ledger._settle_cost(data, 'h', data['holds']['h'], M, M, 'provider_receipt')
            data['organizations']['org']['settled']['total'] = budget.MAX_INTEGER  # saturated: proves nothing
        with self.assertRaisesRegex(budget.BudgetError, 'coverage_ambiguous'):
            self.ledger.apply_coverage(self.coverage(grant, digest, ('hold', 'h', None, M)), 150)



class RoutingBufferTests(unittest.TestCase):
    """Funded constraints carry the fixed 20% API-money buffer; subscription windows keep the user's margin_bp."""

    def test_funded_claim_is_buffered_while_a_custom_window_margin_is_kept(self):
        from runtime import routing as router
        from tests import test_routing_admission as admission
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'private' / 'ledger'
            ledger = budget.Ledger(path, budget.initialize(path))
            admission.fund(ledger)
            cfg = admission.settings(margin_bp=500)
            api = admission.api_route('api', demand={'credit': 10 * M})
            choice = dict({key: api[key] for key in router.ROUTE_IDENTITY}, funding='api')
            paid = router.admit(admission.assignment('a', choice=choice), admission.ranking('s-api'), cfg,
                                admission.observations([api], dict(api=admission.api_pool(limit=100 * M))),
                                ledger=ledger)
            sub = admission.sub_route('sub', demand={'5h': 100})
            window = router.admit(admission.assignment('b'), admission.ranking('s-sub'), cfg,
                                  admission.observations([sub], dict(sub=admission.sub_pool())), ledger=ledger)
            ledger.close()
        self.assertEqual(paid['reserved'], {'org:api-org': 12 * M})  # ceil(10M x 1.2), not 10.5M
        self.assertEqual(list(window['reserved'].values()), [105])  # 100 x 1.05 under the custom margin

class DormantRunnerTests(Store):
    def test_the_fake_runner_reserves_the_envelope_and_never_records_an_estimate_as_actual(self):
        from unittest.mock import patch
        from tests.test_openai_api import Connection, Response, completed
        from tests.test_smart_balance import resolver
        response = completed(); response['model'] = PROFILE['model']
        response['usage'].update(input_tokens=1200, total_tokens=1217)
        queue = [Response(dict(object='response.input_tokens', input_tokens=1200)), Response(response)]
        seen = []

        def factory(host, timeout):
            seen.append(budget.Ledger._total(self.ledger.snapshot(), 'org'))
            return Connection(queue.pop(0))
        decision = self.controller.evaluate(task())
        expected = routing.buffered(decision['maximum_usd_micros'] + PROFILE['count_maximum'])
        with patch('runtime.openai_api.time.time', return_value=101):
            result = self.controller._run(task(), factory, resolver)
        self.assertEqual(seen[0], expected)  # the whole buffered task was held before the count was sent
        run = self.ledger.snapshot()['runs']['run']
        self.assertTrue(run['envelope'] and run['closed'])
        self.assertEqual([(c['state'], c['cost']) for c in run['calls'].values()],
                         [('completed', None), ('completed', None)])
        self.assertEqual((result['cost_basis'], type(result['reported_cost_usd_micros'])), ('estimate', int))
        self.assertEqual(budget.Ledger._total(self.ledger.snapshot(), 'org'), expected)  # held until settled



class CrashBoundaryTests(Money):
    """A reopened ledger after each committed boundary: no duplicate permission, only provably unsent work released."""

    def reopen(self):
        self.ledger.close()
        self.ledger = budget.Ledger(self.path, self.identity)
        self.addCleanup(self.ledger.close)

    def test_count_boundaries(self):
        self.envelope()
        self.reopen()  # crash after the reservation, before dispatch
        self.envelope()  # the replay returns the same envelope: no second reservation
        self.assertEqual(self.held(), 12 * M)
        self.ledger.dispatch('run', 'count', 1, 101)
        self.reopen()  # crash after dispatch committed, before the transport sent
        with self.assertRaisesRegex(budget.BudgetError, 'dispatch_forbidden'):
            self.ledger.dispatch('run', 'count', 1, 101)  # no duplicate permission
        self.ledger.uncertain('run', 'count')  # no proof of non-send: exposure persists
        with self.assertRaisesRegex(budget.BudgetError, 'request_may_be_running'):
            self.ledger.close_run('run')
        self.assertEqual(self.held(), 12 * M)

    def test_count_boundary_with_unsent_proof_releases_everything(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.reopen()
        self.ledger.reject_unsent('run', 'count')  # the transport established that nothing was sent
        self.assertEqual(self.held(), 0)

    def test_generation_boundaries(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        self.ledger.reserve_call('run', 'generation', 'packet2', M, 1, 101)
        self.reopen()  # crash after the grant, before dispatch
        self.ledger.reserve_call('run', 'generation', 'packet2', M, 1, 101)  # replay: no new grant
        with self.assertRaisesRegex(budget.BudgetError, 'attempt_exists'):
            self.ledger.reserve_call('run', 'generation', 'other', M, 1, 101)
        self.ledger.dispatch('run', 'generation', 1, 101)
        self.reopen()  # crash after dispatch, before send
        with self.assertRaisesRegex(budget.BudgetError, 'dispatch_forbidden'):
            self.ledger.dispatch('run', 'generation', 1, 101)
        self.ledger.reject_unsent('run', 'generation')
        self.assertEqual(self.held(), M)  # the unpriced count stays; only the unsent generation is released

    def test_uncertain_generation_keeps_both_exposures(self):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        self.ledger.reserve_call('run', 'generation', 'packet2', M, 1, 101)
        self.ledger.dispatch('run', 'generation', 1, 101)
        self.reopen()
        self.ledger.uncertain('run', 'generation')
        self.assertEqual(self.held(), 12 * M)


class InterleavingTests(UnpricedRecoveryTests):
    def prepare(self, generation):
        self.envelope()
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        if generation in ('reserved', 'uncertain'):
            self.ledger.reserve_call('run', 'generation', 'packet2', M, 1, 101)
        if generation == 'uncertain':
            self.ledger.dispatch('run', 'generation', 1, 101)
            self.ledger.uncertain('run', 'generation')

    def test_pricing_the_count_never_touches_a_placeholder_granted_or_uncertain_generation(self):
        for generation, state in (('placeholder', 'reserved'), ('reserved', 'reserved'), ('uncertain', 'unresolved')):
            with self.subTest(generation):
                self.setUp()
                self.prepare(generation)
                before = self.calls()['generation']
                outcome = self.settle('price-' + generation, 'count', dict(kind='receipts', receipts=['r'],
                                                                          cost_usd_micros=400_000))
                self.assertEqual(outcome['charged'], 400_000)
                run = self.ledger.snapshot()['runs']['run']
                self.assertFalse(run['closed'])
                self.assertEqual(run['calls']['generation'], before)
                self.assertEqual(run['calls']['generation']['state'], state)
                self.assertEqual(self.held(), 400_000 + 11 * M)
                conflict = self.settle('price-' + generation, 'count', dict(kind='charge_reservation'))
                self.assertEqual(conflict['reason'], 'event_conflict')


class ScopedReconcileTests(Store):
    def consent(self):
        revision = self.ledger.snapshot()['routing']['scope']['revision']
        return dict(scope='scope', revision=revision, qualification_revision=None)

    def test_unpriced_work_is_reported_in_its_scope_even_after_close_and_leaves_after_recovery(self):
        from runtime import ledger_versions
        self.ledger.reserve_task('org', 'task', 'run', 'packet', 100, 10, 1, 101, native_of('task'),
                                 consent=self.consent())
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.complete_unknown('run', 'count')
        report = self.ledger.reconcile(scope='scope')
        self.assertEqual(report['unpriced_attempts'], [dict(attempt_id='run:count', reserved_usd_micros=10,
                                                            cost='unknown', run_closed=False)])
        self.assertNotIn('pending', report)
        self.ledger.close_run('run')
        self.assertTrue(self.ledger.reconcile(scope='scope')['unpriced_attempts'][0]['run_closed'])
        self.assertEqual(self.ledger.reconcile(scope='other'), dict(state='manual_recovery_required'))
        self.assertEqual(self.ledger.reconcile(), dict(state='manual_recovery_required'))
        settled = ledger_versions.settle(self.ledger, dict(event_id='e1', run_id='run', phase='count',
                                                           evidence=dict(kind='charge_reservation'),
                                                           kind=ledger_versions.KIND), 200)
        self.assertEqual(settled['state'], 'recorded')
        self.assertNotIn('unpriced_attempts', self.ledger.reconcile(scope='scope'))

    def test_uncertain_work_keeps_the_existing_pending_fields(self):
        self.ledger.reserve_task('org', 'task', 'run', 'packet', 100, 10, 1, 101, native_of('task'),
                                 consent=self.consent())
        self.ledger.dispatch('run', 'count', 1, 101)
        self.ledger.uncertain('run', 'count', incomplete=dict(reason='timeout'))
        report = self.ledger.reconcile(scope='scope')
        expected = dict(attempt_id='run:count', reserved_usd_micros=10, incomplete=dict(reason='timeout'))
        self.assertEqual((report['pending'], report['pending_attempts']), (expected, [expected]))
        self.assertNotIn('unpriced_attempts', report)


class CoverageAuthorityTests(CoverageTests):
    def test_covered_spend_still_consumes_task_cycle_and_live_authority(self):
        with self.ledger.transaction() as data:  # finite task, custom cycle cap and pilot limit
            data['organizations']['org']['policy'].update(task_limit=10 * M, ceiling='custom', amount=20 * M,
                                                         pilot_limit=20 * M)
        self.spend('t', 8 * M)
        grant, digest = self.stage(44 * M)
        self.ledger.apply_coverage(self.coverage(grant, digest, ('run', 't', 'count', 8 * M)), 150)
        self.assertEqual(self.ledger.credit_headroom('org', 150), 42 * M)  # the provider headroom is restored
        native = plain_native('t')
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):  # task limit: 8 + 3 > 10
            self.ledger.begin('org', 't', 't2', 'packet', 3 * M, 1, 150, dict(native, task='t'))
        self.ledger.begin('org', 'u', 'u', 'packet', 10 * M, 1, 150, plain_native('u'))  # 18 of the 20 cycle cap
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):  # cycle cap and pilot limit
            self.ledger.begin('org', 'v', 'v', 'packet', 3 * M, 1, 150, plain_native('v'))
        gate = lambda data, now: dict(pin='p', maximum=20 * M)  # noqa: E731  a live cap at the same total
        with self.assertRaisesRegex(budget.BudgetError, 'live_cap_exceeded'):
            self.ledger.begin('org', 'w', 'w', 'packet', 3 * M, 1, 150, plain_native('w'), live_gate=gate)

    def test_a_pruned_hold_never_reapplies_its_covered_subtotal(self):
        self.ledger.admit('h', lambda data: dict(record=dict(attempt_id='h', task='t', bucket=[], shares={}),
                                                 vector={'org:org': 10 * M}, account='acct', now=101, revision=1))
        with self.ledger.transaction() as data:
            self.ledger._settle_cost(data, 'h', data['holds']['h'], 8 * M, 8 * M, 'provider_receipt')
        for i in range(budget.MAX_HOLDS):  # fill the hold table; admission prunes released holds at the bound
            attempt = 'f%d' % i
            self.ledger.admit(attempt, lambda data, a=attempt: dict(
                record=dict(attempt_id=a, task='t', bucket=[], shares={}), vector={}, account='acct'))
            self.ledger.close_attempt(attempt, 'not_dispatched')
        grant, digest = self.stage(44 * M)
        self.assertNotIn('h', self.ledger.snapshot()['holds'])  # pruned by the real admission owner
        with self.assertRaisesRegex(budget.BudgetError, 'coverage_charge_mismatch'):  # the owner is gone
            self.ledger.apply_coverage(self.coverage(grant, digest, ('hold', 'h', None, 8 * M)), 150)
        settled = self.ledger.snapshot()['organizations']['org']['settled']
        self.assertEqual((settled['total'], settled.get('covered', 0)), (8 * M, 0))
        self.assertEqual(self.ledger.credit_headroom('org', 150), 34 * M)  # never credit without a retained owner

    def test_a_covered_pruned_hold_keeps_its_subtotal_once(self):
        self.ledger.admit('h', lambda data: dict(record=dict(attempt_id='h', task='t', bucket=[], shares={}),
                                                 vector={'org:org': 10 * M}, account='acct', now=101, revision=1))
        with self.ledger.transaction() as data:
            self.ledger._settle_cost(data, 'h', data['holds']['h'], 8 * M, 8 * M, 'provider_receipt')
        grant, digest = self.stage(44 * M)
        self.ledger.apply_coverage(self.coverage(grant, digest, ('hold', 'h', None, 8 * M)), 150)
        for i in range(budget.MAX_HOLDS):
            attempt = 'f%d' % i
            self.ledger.admit(attempt, lambda data, a=attempt: dict(
                record=dict(attempt_id=a, task='t', bucket=[], shares={}), vector={}, account='acct'))
            self.ledger.close_attempt(attempt, 'not_dispatched')
        self.assertNotIn('h', self.ledger.snapshot()['holds'])
        with self.assertRaisesRegex(budget.BudgetError, 'coverage_charge_mismatch'):
            self.ledger.apply_coverage(self.coverage(grant, digest, ('hold', 'h', None, 8 * M)), 150)
        settled = self.ledger.snapshot()['organizations']['org']['settled']
        self.assertEqual((settled['total'], settled['covered']), (8 * M, 8 * M))  # persists with total, once
        self.assertEqual(self.ledger.credit_headroom('org', 150), 42 * M)


class SharedCheckoutTests(unittest.TestCase):
    """Two distinct checkout paths resolve, through usage_settings, to one ledger identity and one organization."""

    def test_two_checkouts_and_account_aliases_share_one_funding_authority(self):
        import threading
        from unittest.mock import patch
        from runtime import usage_settings
        with tempfile.TemporaryDirectory() as folder:
            home, roots = Path(folder) / 'home', [Path(folder) / 'checkout-a', Path(folder) / 'checkout-b']
            for root in roots:
                root.mkdir()
            keys = {str(root): ('key-' + root.name, root) for root in roots}
            request = dict(version=2, expected_revision=0, expected_org_revision=0, binding=KEYCHAIN_BINDING,
                           policy=dict(POLICY, task_limit=None, pilot_limit=500), profile=PROFILE,
                           class_name='investigation', mode='smart')
            with patch.object(usage_settings, 'directory', return_value=home / 'usage'), \
                    patch.object(usage_settings, 'scope', side_effect=lambda cwd: keys[str(cwd)]):
                with usage_settings.controller(roots[0], create=True) as first:
                    first.clock = lambda: 101  # the synthetic price card's validity window
                    first.configure(request)
                    first.ledger.observe('org', 800, 100)
                    identity = first.ledger.identity
                with usage_settings.controller(roots[1], create=True) as second:
                    second.clock = lambda: 101
                    second.configure(dict(request, expected_org_revision=1))
                    self.assertEqual(second.ledger.identity, identity)  # the same ledger behind both paths
                outcomes, barrier = [], threading.Barrier(6)

                def contend(index):
                    root = roots[index % 2]
                    with usage_settings.controller(root) as control:
                        barrier.wait(timeout=10)
                        alias = ('acct', 'ACCT', 'acct-alias')[index % 3]  # provider account aliases
                        try:
                            control.ledger.reserve_task('org', 't%d' % index, 'r%d' % index, 'packet', 100, 10, 1,
                                                        101, dict(account=alias, task='t%d' % index, windows=[]))
                            outcomes.append('admitted')
                        except budget.BudgetError as error:
                            outcomes.append(str(error))
                threads = [threading.Thread(target=contend, args=(i,)) for i in range(6)]
                for thread in threads: thread.start()
                for thread in threads: thread.join()
                with usage_settings.controller(roots[1]) as control:
                    total = budget.Ledger._total(control.ledger.snapshot(), 'org')
        self.assertEqual(sorted(outcomes), ['admitted'] * 4 + ['insufficient_headroom'] * 2)  # pilot limit 500
        self.assertEqual(total, 4 * 120)


if __name__ == '__main__':
    unittest.main()
