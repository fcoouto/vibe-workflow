"""Scale fixtures exercise bounded plans and admissions, not native execution."""
import sys
import unittest

from unittest.mock import patch
from runtime import delegation, evidence, state
from tests import test_delegation


class DelegationScaleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_delegation.AdmissionTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope

    def plan(self, count, *, chain=False):
        plan = self.fixture.plan_value()
        plan['limits'].update(max_attempts=count + 2, max_resource_units=count + 2)
        plan['jobs'] = [dict(id='job' + str(i), role='worker',
            claims=[self.fixture.path_claim()], dependencies=['job' + str(i - 1)] if chain and i else [],
            max_attempts=2) for i in range(count)]
        return plan

    def validate(self, plan):
        return delegation._delegation_plan(self.scope, dict(plan, git_directory=delegation._git_directory(self.scope)))

    def test_large_plan_dependencies_and_attempt_budget(self):
        plan = self.plan(60)
        plan['jobs'][-1]['dependencies'] = [job['id'] for job in plan['jobs'][:-1]]
        self.assertEqual(self.validate(plan)['limits']['max_attempts'], 62)
        self.fixture.plan(plan)
        self.assertEqual(delegation.delegation_status(self.scope, 'codex', self.fixture.identity['session_id'])['capacity']['max_parallel_workers'], 4)

    def test_deep_valid_plan_is_iterative_and_cycles_and_unknowns_reject(self):
        plan = self.plan(180, chain=True)
        plan['jobs'].reverse()
        old = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(140)
            self.validate(plan)
        finally:
            sys.setrecursionlimit(old)
        plan['jobs'][-1]['dependencies'] = [plan['jobs'][0]['id']]
        with self.assertRaisesRegex(state.StateError, 'cyclic'):
            self.validate(plan)
        plan['jobs'][-1]['dependencies'] = ['missing']
        with self.assertRaisesRegex(state.StateError, 'Unknown'):
            self.validate(plan)

    def test_byte_bounds_and_budget_integrity_remain(self):
        plan = self.plan(60)
        plan['selection_constraints']['rationale'] = 'x' * (128 * 1024)
        with self.assertRaises(state.StateError):
            self.validate(plan)
        plan = self.plan(60)
        plan['limits']['max_attempts'] = 0
        with self.assertRaises(state.StateError):
            self.validate(plan)
        plan = self.plan(60)
        plan['jobs'][-1]['dependencies'] = ['job0', 'job0']
        with self.assertRaises(state.StateError):
            self.validate(plan)

    def test_verification_closure_scales_with_artifact_bytes(self):
        resources = [dict(kind='service', key='shared-' + str(i), mode='read', host='local')
                     for i in range(4300)]
        evidence._bounded(resources, evidence.MAX_ARTIFACT)
        claims = delegation._claims(resources, maximum=delegation.MAX_VERIFICATION_CLAIMS)
        self.assertEqual(len(delegation._stored_claims(claims,
            maximum=delegation.MAX_VERIFICATION_CLAIMS)), 4300)

    def test_fifty_one_completed_admissions_reconcile_and_preserve_pins(self):
        count = 51
        plan = self.plan(count, chain=True)
        plan['limits'].update(max_attempts=count + 2, max_resource_units=count + 2)
        plan['jobs'].append(dict(id='scale-review',role='review',claims=[self.fixture.path_claim(mode='read')],dependencies=['job50'],max_attempts=1))
        self.fixture.plan(plan)
        pins = state.task_status(self.scope, 'codex', self.fixture.identity['session_id'])['task']['pins']
        for i in range(count):
            request = self.fixture.prepare_request(dispatch_id='scale-' + str(i), job_id='job' + str(i))
            request['brief']['dependencies'] = ['job' + str(i - 1)] if i else []
            prepared = delegation.prepare_assignment(self.scope, request)
            self.fixture.observe_worker(prepared)
            proof = self.fixture.worker_reconciliation_request(prepared, outcome='failed' if i == count - 1 else 'completed')
            delegation.reconcile_assignment(self.scope, proof)
        retry = self.fixture.prepare_request(dispatch_id='scale-retry', job_id='job50',
            retry_of=prepared['attempt_id'], diagnostic=dict(prior_attempt_id=prepared['attempt_id'],
                cause='Independent verification found incomplete output', evidence=['fixture:retry-check'],
                change='Complete the original final context'))
        retry['brief']['dependencies'] = ['job49']
        prepared = delegation.prepare_assignment(self.scope, retry)
        self.fixture.observe_worker(prepared)
        delegation.reconcile_assignment(self.scope, self.fixture.worker_reconciliation_request(prepared))
        review=test_delegation.Stage4NativeReviewTests
        packet=review.review_request(self.fixture,job_id='scale-review',dispatch_id='scale-review')
        reviewed=evidence.prepare_review(self.scope,packet)
        observed=review.observed(self.fixture,reviewed)
        with patch('runtime.adapters.codex.observe_native_review_assignment',return_value=observed):
            lifecycle=delegation.observe_assignment(self.scope,self.fixture.request(attempt_id=reviewed['attempt_id'],parent_turn_id='parent-turn',spawn_item_id='spawn-item'))
            self.assertEqual(lifecycle['status'],'observed',lifecycle)
        collection=review.collect(self.fixture,reviewed,review.collection(self.fixture,reviewed,observed))
        self.assertEqual(collection['attempt']['status'],'collected')
        evidence.assess_review(self.scope,self.fixture.request(attempt_id=reviewed['attempt_id'],assessment=dict(
            coverage=['general','security'],criteria=['works'],qualification='Independent fixture expertise',
            independence='Separate observed reviewer',semantic_coverage='Read requirements and code',dispositions=[],decision_refs=['user:accepted'])))
        delegation.reconcile_assignment(self.scope,self.fixture.worker_reconciliation_request(reviewed,changed_paths=[]))
        task = state.task_status(self.scope, 'codex', self.fixture.identity['session_id'])['task']
        self.assertEqual(task['delegation']['attempts_consumed'], count + 2)
        self.assertEqual(task['pins'], pins)
        with state._database(self.scope) as db:
            rows = delegation._ledger(self.scope, db)
        self.assertEqual(len(rows), count + 2)
        self.assertTrue(all(r['data']['status'] == 'settled' for r in rows))
        self.assertEqual(sum(r['data']['result']['outcome'] == 'completed' for r in rows), count + 1)
        self.assertEqual(task['delegation']['resource_units_consumed'], count + 2)
        extra = self.fixture.prepare_request(dispatch_id='over-budget', job_id='job50')
        extra['brief']['dependencies'] = ['job49']
        with self.assertRaisesRegex(state.StateError, 'budget is exhausted'):
            delegation.prepare_assignment(self.scope, extra)
