"""F5.A1: no artificial count admission. Saved ledger/settings caps and declared D6 worker counts are read, never
enforced; actual funds, quota windows, physical ownership and finite task authority still refuse.

Real disposable ledgers, settings and delegation stores through the actual entry points (`routing.admit`,
`Ledger.begin`, the routing command's `status`, `delegation.prepare_assignment`); no admission validator is bypassed.
"""
import json
import tempfile
import unittest
from pathlib import Path

from runtime import routing, state, usage_budget as budget
from scripts import routing as command
from tests import test_routing_admission as admission
from tests.fixture_delegation import AdmissionFixture

ONE = dict(total=1, per_account=1)


class LedgerAndRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'private' / 'ledger.sqlite3'
        self.ledger = budget.Ledger(path, budget.initialize(path))
        self.addCleanup(self.ledger.close)
        admission.fund(self.ledger)
        self.ledger.set_caps(1, 1)  # a saved legacy cap of one: readable, never enforced

    def admit(self, attempt, route, pools, **cfg):
        return routing.admit(admission.assignment(attempt), admission.ranking(route['strategy_id']),
                             admission.settings(**cfg), admission.observations([route], pools), ledger=self.ledger)

    def test_six_tasks_three_on_one_account_are_admitted_despite_saved_caps_of_one(self):
        pools = {'p%d' % i: admission.sub_pool() for i in range(4)}
        accounts = ['A', 'A', 'A', 'B', 'C', 'D']
        for i, account in enumerate(accounts):
            route = admission.sub_route('r%d' % i, pool='p%d' % (i % 4), strategy='s%d' % i, account=account)
            result = self.admit('t%d' % i, route, pools, caps=ONE)
            self.assertEqual(result['status'], 'admitted', result)
        self.assertEqual(sorted(budget.Ledger.open_accounts(self.ledger.snapshot())), sorted(accounts))
        self.assertEqual(self.ledger.snapshot()['caps'], ONE)  # still stored as it was, not rewritten

    def test_s1_runs_ignore_saved_caps_but_funds_still_refuse(self):
        for i in range(6):  # one organization is the S1 account: six concurrent runs
            self.ledger.begin('api-org', 'task-%d' % i, 'run-%d' % i, 'digest', budget_amount(), 1, admission.NOW,
                              dict(account='api-org', task='task-%d' % i, windows=[]))
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.ledger.begin('api-org', 'task-x', 'run-x', 'digest', 10**15, 1, admission.NOW,
                              dict(account='api-org', task='task-x', windows=[]))

    def test_quota_windows_still_refuse_without_a_count_reason(self):
        full = admission.sub_route('full', strategy='s-full', demand={'5h': 2000})
        result = self.admit('quota', full, dict(sub=admission.sub_pool()), caps=ONE)
        self.assertEqual(result['status'], 'pending')
        self.assertIn('insufficient_headroom', result['reason_codes'])
        self.assertNotIn('concurrency_cap', json.dumps(result))


def budget_amount():
    return 1_000_000


class StatusTests(unittest.TestCase):
    def test_status_reports_saved_caps_as_legacy_and_not_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = command.Service(root / 'routing' / 'settings.json', root / 'usage' / 'ledger.sqlite3')
            self.addCleanup(service.close)
            call = lambda request: service.handle(json.dumps(request).encode())  # noqa: E731
            self.assertEqual(call(dict(v=1, op='set', expected_revision=0, change=dict(caps=ONE)))['state'], 'updated')
            budget.initialize(root / 'usage' / 'ledger.sqlite3')
            status = call(dict(v=1, op='status'))
            self.assertNotIn('effective_caps', status)
            self.assertEqual(status['legacy_caps'], dict(settings=ONE, ledger=None, enforced=False))
            self.assertIn(dict(code='caps_not_enforced', key='caps'), status['settings_warnings'])
            self.assertNotIn('caps', status['ledger'])


class DelegationCountTests(AdmissionFixture, unittest.TestCase):
    """D6: declared `max_parallel_workers`/`host_capacity` (legacy plans) are not admission."""

    def legacy_limits(self):
        return dict(max_parallel_workers=1, host_capacity=1)

    def test_legacy_worker_counts_of_one_admit_independent_owners(self):
        value = self.plan_value()
        value['limits'].update(self.legacy_limits())
        self.plan(value)
        self.assertTrue(self.prepare()['launch_admitted'])
        for n in range(1, 6):  # five more owners, each with a declared count of one
            other = self.second_owner(n, [self.path_claim('file-%d.py' % n)], self.legacy_limits())
            self.assertTrue(self.api.prepare_assignment(self.scope, other)['launch_admitted'], n)

    def test_three_jobs_of_one_owner_run_with_a_declared_count_of_one(self):
        value = self.plan_value()
        value['limits'].update(self.legacy_limits())
        value['jobs'] = [dict(value['jobs'][0], id='job-%d' % n, claims=[self.path_claim('job-%d.py' % n)])
                         for n in range(3)]
        self.plan(value)
        for n in range(3):
            request = self.prepare_request(job_id='job-%d' % n, dispatch_id='dispatch-%d' % n)
            request['brief'].update(files=['job-%d.py' % n], resources=[self.path_claim('job-%d.py' % n)])
            self.assertTrue(self.api.prepare_assignment(self.scope, request)['launch_admitted'], n)
        status = self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']
        self.assertEqual(status['legacy_declared'], dict(max_parallel_workers=1, host_capacity=1, enforced=False))

    def test_a_new_plan_without_count_fields_is_accepted(self):
        value = self.plan_value()
        for field in ('max_parallel_workers', 'host_capacity', 'capacity_ref'):
            value['limits'].pop(field)
        self.plan(value)
        prepared = self.prepare()
        self.assertTrue(prepared['launch_admitted'])
        self.assertIsNone(self.raw_assignment(prepared['attempt_id'])['host_capacity'])
        self.assertIsNone(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['legacy_declared'])

    def test_malformed_or_partial_limits_are_controlled_refusals(self):
        """F5.A1 regression: limits are shape-checked before any count-field membership test."""
        cases = dict(none=None, list=[], text='limits')
        legacy = self.plan_value()['limits']
        cases.update(workers_only={k: v for k, v in legacy.items() if k not in ('host_capacity', 'capacity_ref')},
                     host_only={k: v for k, v in legacy.items() if k != 'max_parallel_workers'})
        for name, limits in cases.items():
            with self.subTest(name), self.assertRaises(state.StateError):
                self.plan(dict(self.plan_value(), limits=limits))
        self.assertIsNone(self.api.delegation_status(self.scope, 'codex', 'parent')['plan'])  # nothing was planned

    def test_physical_alias_and_finite_authority_still_refuse(self):
        value = self.plan_value()
        value['limits'].update(max_attempts=1, **self.legacy_limits())
        value['jobs'][0]['max_attempts'] = 1
        self.plan(value)
        self.assertTrue(self.prepare()['launch_admitted'])
        conflict = self.second_owner(1, [self.path_claim()])  # the same file: canonical ownership conflict
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, conflict)
        with self.assertRaises(state.StateError):  # the plan's single attempt is spent
            self.prepare(dispatch_id='request-2')


if __name__ == '__main__':
    unittest.main()
