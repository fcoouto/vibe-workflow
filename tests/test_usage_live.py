"""Admission integration uses disposable ledgers and explicit fake I/O."""
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from runtime import usage_admission as admission, usage_budget as budget, usage_routing as routing
from tests.test_openai_api import Connection, Response, completed
from tests.test_smart_balance import BINDING, POLICY, qualification, task
from tests.test_usage_routing import PROFILE


class LiveAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        path = root / 'private' / 'ledger.sqlite3'
        self.ledger = budget.Ledger(path, budget.initialize(path))
        self.addCleanup(self.ledger.close)
        self.clock = 101
        self.controller = routing.Controller(self.ledger, 'scope', root=root, clock=lambda: self.clock)
        self.binding = dict(BINDING, storage_kind='macos_keychain', credential_ref='vcw_' + '0' * 32)
        self.profile = dict(PROFILE, count_maximum=7)
        self.controller.configure(dict(version=2, expected_revision=0, expected_org_revision=0,
                                       binding=self.binding, policy=POLICY, profile=self.profile,
                                       class_name='investigation', mode='manual'))
        self.task = task(provider='openai-api')
        self.proof = admission.Admission(
            scope='scope', config_revision=2, org_revision=1,
            policy_digest=admission.digest(POLICY), binding_digest=admission.digest(self.binding),
            profile_digest=admission.digest(self.profile), task_digest=admission.digest(self.task),
            grant=admission.Grant('org', 'project', 800, 10000, 100, 'platform-credit-grants', 'benefit'),
            price_observed_at=100, price_valid_until=1000, approval_id='pilot',
            approved_at=100, approval_until=200, maximum_usd_micros=100,
            coordinator='coordinator', reviewer='independent-reviewer')
        self.resolver = Mock(return_value=b'synthetic-key')
        self.factory = Mock()

    def run_candidate(self, proof, task_override=None):
        with patch('runtime.openai_api.time.time', side_effect=lambda: self.clock):
            return self.controller._run_live(
                self.task if task_override is None else task_override, proof,
                connection_factory=self.factory, credential_resolver=self.resolver)

    def assert_generation_route(self, result, connections):
        self.assertIs(result['accepted'], False)
        self.assertIs(result['requires_native_review'], True)
        self.assertEqual(result['run_id'], 'run')
        self.assertEqual([conn.requests[0][3]['X-Client-Request-Id'] for conn in connections],
                         ['run:count', 'run:generation'])
        self.assertEqual(result.get('execution_route'), 'openai-api')

    def test_off_and_json_evidence_never_resolve_a_credential(self):
        before = self.ledger.snapshot()
        with self.assertRaises(budget.BudgetError):
            self.run_candidate(self.proof)
        self.assertEqual(self.ledger.snapshot(), before)
        self.controller.enable(1)
        before = self.ledger.snapshot()
        with self.assertRaises(budget.BudgetError):
            self.run_candidate(asdict(self.proof))
        self.assertEqual(self.ledger.snapshot(), before)
        self.resolver.assert_not_called()
        self.factory.assert_not_called()

    def test_real_transport_requires_enrollment_check_before_state_changes(self):
        self.controller.enable(1)
        before = self.ledger.snapshot()
        with patch('runtime.usage_credentials.resolve_key') as resolver, self.assertRaisesRegex(
                budget.BudgetError, 'live_scope_check_required'):
            self.controller._run_live(self.task, self.proof)
        resolver.assert_not_called()
        self.assertEqual(self.ledger.snapshot(), before)

    def test_legacy_enabled_canary_budget_cannot_admit_regular_live_task(self):
        self.controller.enable(1)
        policy = dict(POLICY, cycle='canary-old')
        with self.ledger.transaction() as state:
            state['organizations']['org']['policy'] = policy
        proof = replace(self.proof, policy_digest=admission.digest(policy))
        with self.assertRaisesRegex(budget.BudgetError, 'routing_budget_required'):
            self.run_candidate(proof)
        self.resolver.assert_not_called()
        self.factory.assert_not_called()
        self.assertEqual(self.ledger.snapshot()['runs'], {})

    def test_two_sends_share_review_reserve_and_cannot_replay(self):
        self.controller.enable(1)
        counted = Connection(Response(dict(object='response.input_tokens', input_tokens=1200)))
        reply = completed()
        reply['model'] = self.profile['model']
        reply['usage'].update(input_tokens=1200, total_tokens=1217)
        generated = Connection(Response(reply))
        self.factory.side_effect = [counted, generated]

        result = self.run_candidate(self.proof)
        self.assertTrue(result['simulation_only'])
        self.assert_generation_route(result, (counted, generated))
        self.assertEqual(self.resolver.call_count, 2)
        self.assertEqual([conn.requests[0][1] for conn in (counted, generated)],
                         ['/v1/responses/input_tokens', '/v1/responses'])
        data = self.ledger.snapshot()
        self.assertEqual(len(data['native']), 1)
        self.assertNotIn('slot', data)
        self.assertEqual(data['runs']['run']['calls']['count']['cost'], 7)
        with self.assertRaisesRegex(budget.BudgetError, 'live_run_id_already_used'):
            self.run_candidate(self.proof)
        self.assertEqual(self.factory.call_count, 2)

    def test_incomplete_generation_retains_evidence_without_releasing_reservations(self):
        self.controller.enable(1)
        counted = Connection(Response(dict(object='response.input_tokens', input_tokens=1200)))
        reply = completed()
        reply.update(model=self.profile['model'], status='incomplete',
                     incomplete_details=dict(reason='max_output_tokens'))
        reply['usage'].update(input_tokens=1200, total_tokens=1217)
        generated = Connection(Response(reply))
        self.factory.side_effect = [counted, generated]

        with self.assertRaisesRegex(budget.BudgetError, 'incomplete_response'):
            self.run_candidate(self.proof)
        before = self.ledger.snapshot()
        self.assertFalse(before['runs']['run']['closed'])
        self.assertEqual(len(before['native']), 1)
        generation = before['runs']['run']['calls']['generation']
        self.assertEqual(generation['state'], 'unresolved')
        self.assertIsNone(generation['cost'])
        diagnostic = dict(reason='max_output_tokens', response_id='resp_synthetic', request_id='req_synthetic',
                          usage=dict(input_tokens=1200, output_tokens=17, cached_tokens=3,
                                     cache_write_tokens=2, reasoning_tokens=7))
        self.assertEqual(generation.get('incomplete'), diagnostic)
        with patch('runtime.usage_settings.controller') as controller:
            controller.return_value.__enter__.return_value = self.controller
            result = routing.reconcile(self.controller.root, {})
        pending = dict(attempt_id='run:generation', reserved_usd_micros=generation['maximum'],
                       incomplete=diagnostic)
        self.assertEqual(result, dict(state='manual_recovery_required', pending=pending,
                                      pending_attempts=[pending]))
        self.assertEqual(self.ledger.reconcile(scope='other'), {'state': 'manual_recovery_required'})
        self.assertEqual(self.ledger.snapshot(), before)
        with self.assertRaisesRegex(budget.BudgetError, 'live_run_id_already_used'):
            self.run_candidate(self.proof)
        self.assertEqual(self.factory.call_count, 2)

    def smart_candidate(self, *, calibrated=True):
        self.controller.configure(dict(version=2, expected_revision=1, expected_org_revision=1,
                                       binding=self.binding, policy=POLICY, profile=self.profile,
                                       class_name='investigation', mode='smart'))
        record = dict(qualification(), profile_digest=routing.profile_digest(self.profile))
        if calibrated:
            record['native_allowance_pp'] = 10
        self.controller.record_qualification(record)
        self.task = task()
        proof = replace(self.proof, config_revision=3, org_revision=1,
                        task_digest=admission.digest(self.task), qualification_digest=admission.digest([1, record]))
        return record, proof

    def enable_legacy_smart(self):
        self.controller.enable(2)
        with self.ledger.transaction() as state:
            self.controller._config(state).pop('live_consent')

    def test_smart_host_dispatch_requires_enabled_mode_and_pinned_qualification(self):
        _, proof = self.smart_candidate()
        with self.assertRaisesRegex(budget.BudgetError, 'live_mode_unqualified'):
            self.run_candidate(proof)
        self.enable_legacy_smart()
        with self.assertRaisesRegex(budget.BudgetError, 'live_consent_required'):
            self.run_candidate(replace(proof, qualification_digest=None))
        self.factory.assert_not_called()
        self.resolver.assert_not_called()

        counted = Connection(Response(dict(object='response.input_tokens', input_tokens=1200)))
        reply = completed()
        reply['model'] = self.profile['model']
        reply['usage'].update(input_tokens=1200, total_tokens=1217)
        generated = Connection(Response(reply))
        self.factory.side_effect = [counted, generated]
        result = self.run_candidate(proof)
        self.assert_generation_route(result, (counted, generated))
        self.assertEqual([conn.requests[0][1] for conn in (counted, generated)],
                         ['/v1/responses/input_tokens', '/v1/responses'])
        state = self.ledger.snapshot()
        self.assertEqual(len(state['native']), 1)
        self.assertEqual(state['runs']['run']['consent']['qualification_revision'], 1)

    def test_first_smart_task_runs_without_qualification_and_survives_measurement(self):
        self.controller.enable(1, mode='smart')
        self.task = task()
        self.task['native']['windows'][0]['allowance_pp'] = 5
        proof = replace(self.proof, task_digest=admission.digest(self.task))
        counted = Connection(Response(dict(object='response.input_tokens', input_tokens=1200)))
        reply = completed()
        reply['model'] = self.profile['model']
        reply['usage'].update(input_tokens=1200, total_tokens=1217)
        generated = Connection(Response(reply))
        respond = counted.getresponse

        def receive():
            record = dict(qualification(), profile_digest=routing.profile_digest(self.profile),
                          quality_passed=False, valid_until=102)
            self.controller.record_qualification(record)
            self.clock = 102
            return respond()

        counted.getresponse = receive
        self.factory.side_effect = [counted, generated]
        result = self.run_candidate(proof)
        self.assert_generation_route(result, (counted, generated))
        state = self.ledger.snapshot()
        self.assertEqual(len(state['native']), 1)
        self.assertIsNone(state['runs']['run']['consent']['qualification_revision'])

    def test_legacy_smart_enable_is_not_standing_consent(self):
        self.controller.enable(1, mode='smart')
        with self.ledger.transaction() as state:
            self.controller._config(state).pop('live_consent', None)
        self.task = task()
        self.task['native']['windows'][0]['allowance_pp'] = 5
        proof = replace(self.proof, task_digest=admission.digest(self.task))
        with self.assertRaisesRegex(budget.BudgetError, 'live_consent_required'):
            self.run_candidate(proof)
        self.factory.assert_not_called()
        self.resolver.assert_not_called()

    def test_smart_qualification_change_after_count_prevents_generation(self):
        record, proof = self.smart_candidate()
        self.enable_legacy_smart()
        counted = Connection(Response(dict(object='response.input_tokens', input_tokens=1200)))
        respond = counted.getresponse

        def receive():
            self.controller.record_qualification(dict(record, net_native_saved=6))
            return respond()

        counted.getresponse = receive
        self.factory.return_value = counted
        with self.assertRaisesRegex(budget.BudgetError, 'qualification_changed'):
            self.run_candidate(proof)
        self.factory.assert_called_once()
        self.assertEqual(len(counted.requests), 1)
        self.assertEqual(self.ledger.snapshot()['runs']['run']['calls']['count']['cost'], 7)

    def test_smart_live_uses_verified_profile_without_quota_calibration(self):
        _, proof = self.smart_candidate(calibrated=False)
        self.enable_legacy_smart()
        with self.assertRaisesRegex(budget.BudgetError, 'provider_request_rejected'):
            self.run_candidate(proof)
        self.factory.assert_called_once()
        self.resolver.assert_called_once()

    def test_exact_count_cannot_increase_the_approved_cap(self):
        self.controller.enable(1)
        self.factory.return_value = Connection(Response(dict(object='response.input_tokens', input_tokens=9000)))
        profile = dict(self.profile, input_rate=1_000_000)
        request = dict(version=2, expected_revision=2, expected_org_revision=1, binding=self.binding,
                       policy=dict(POLICY, task_limit=100_000, pilot_limit=100_000, amount=100_000),
                       profile=profile, class_name='investigation', mode='manual')
        self.controller.configure(request)
        self.controller.enable(3)
        candidate = dict(self.task, packet=dict(self.task['packet'], input_tokens=1))
        proof = replace(self.proof, config_revision=4, org_revision=2,
                        policy_digest=admission.digest(request['policy']), profile_digest=admission.digest(profile),
                        task_digest=admission.digest(candidate), grant=replace(self.proof.grant, balance=100_000))
        with self.assertRaisesRegex(budget.BudgetError, 'live_cap_exceeded'):
            self.run_candidate(proof, candidate)
        self.assertEqual(len(self.factory.return_value.requests), 1)
        self.assertNotIn('slot', self.ledger.snapshot())
        self.assertEqual(self.ledger.snapshot()['runs']['run']['calls']['count']['cost'], 7)

    def test_disable_while_transport_prepares_prevents_count_post(self):
        self.controller.enable(1)
        connection = Connection(Response({}))

        def disable(*args):
            self.controller.disable(2)
            return connection

        self.factory.side_effect = disable
        with self.assertRaises(budget.BudgetError):
            self.run_candidate(self.proof)
        self.assertEqual(connection.requests, [])
        self.assertNotIn('slot', self.ledger.snapshot())
        self.assertEqual(self.ledger.snapshot()['runs']['run']['calls']['count']['state'],
                         'rejected-before-send')

    def test_newer_depleted_observation_retires_older_proof_without_sending(self):
        self.controller.enable(1)
        depleted = replace(self.proof, grant=replace(self.proof.grant, balance=0, observed_at=101))
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.run_candidate(depleted)
        with self.assertRaisesRegex(budget.BudgetError, 'live_grant_observation_changed'):
            self.run_candidate(self.proof)
        self.assertEqual(self.ledger.snapshot()['organizations']['org']['balance'], 0)
        self.resolver.assert_not_called()
        self.factory.assert_not_called()

    def test_changed_grant_expiry_invalidates_an_older_approval(self):
        self.controller.enable(1)
        conflict = replace(self.proof, grant=replace(self.proof.grant, expiry=9999, observed_at=101))
        with self.assertRaisesRegex(budget.BudgetError, 'expiry_changed'):
            self.run_candidate(conflict)
        with self.assertRaisesRegex(budget.BudgetError, 'live_grant_observation_changed'):
            self.run_candidate(self.proof)
        self.clock = 102
        restored = replace(self.proof, grant=replace(self.proof.grant, balance=1, observed_at=102))
        with self.assertRaisesRegex(budget.BudgetError, 'insufficient_headroom'):
            self.run_candidate(restored)
        self.assertEqual(self.ledger.snapshot()['organizations']['org']['issued'], 1)
        self.resolver.assert_not_called()
        self.factory.assert_not_called()

    def test_scope_change_while_waiting_for_dispatch_prevents_post(self):
        self.controller.enable(1)
        connection = Connection(Response(dict(object='response.input_tokens', input_tokens=1200)))
        self.factory.return_value = connection
        scope_valid = True
        dispatch = self.ledger.dispatch

        def check_scope():
            budget.require(scope_valid, 'scope_changed')

        def dispatch_after_revocation(*args, **kwargs):
            nonlocal scope_valid
            scope_valid = False
            return dispatch(*args, **kwargs)

        with patch.object(self.ledger, 'dispatch', side_effect=dispatch_after_revocation), patch(
                'runtime.openai_api.time.time', return_value=self.clock), self.assertRaises(budget.BudgetError):
            self.controller._run_live(self.task, self.proof, scope_check=check_scope,
                                      connection_factory=self.factory, credential_resolver=self.resolver)
        self.assertEqual(connection.requests, [])
        self.assertNotIn('slot', self.ledger.snapshot())


if __name__ == '__main__':
    unittest.main()
