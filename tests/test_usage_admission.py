"""Trusted admission rejects stale or mismatched inputs without provider calls."""
from dataclasses import asdict, replace
import unittest

from runtime import usage_admission
from runtime.usage_budget import BudgetError
from tests.test_smart_balance import BINDING, POLICY, task
from tests.test_usage_budget import native
from tests.test_usage_routing import PROFILE


class UsageAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.policy = dict(POLICY, stop_at=2200)
        self.config = dict(revision=3, org_revision=2, binding=dict(
            BINDING, storage_kind='macos_keychain', credential_ref='vcw_' + '0' * 32),
            profile=dict(PROFILE, verified_at=900, valid_until=1800, count_maximum=7),
            class_name='investigation', enabled=True, disabled=False, requested_mode='manual', live_consent=True)
        self.task = task(provider='openai-api')
        self.task['native']['windows'][0].update(observed_at=980, reset_at=2000)
        self.grant = usage_admission.Grant('org', 'project', 800, 10000, 950, 'platform-credit-grants', 'benefit')

    def evidence(self, **changes):
        digest = usage_admission.digest
        data = dict(scope='checkout', config_revision=3, org_revision=2, policy_digest=digest(self.policy),
                    binding_digest=digest(self.config['binding']), profile_digest=digest(self.config['profile']),
                    task_digest=digest(self.task), grant=self.grant, price_observed_at=900, price_valid_until=1800,
                    approval_id='accepted-once', approved_at=990, approval_until=1750, maximum_usd_micros=150,
                    coordinator='coordinator-session', reviewer='independent-session')
        return usage_admission.Admission(**(data | changes))

    def validate(self, evidence, **changes):
        arguments = dict(scope='checkout', config=self.config, policy=self.policy, task=self.task, now=self.now)
        return usage_admission.validate(evidence, **(arguments | changes))

    def test_deadline_uses_earliest_current_evidence(self):
        self.assertEqual(self.validate(self.evidence()), 1750)
        self.assertEqual(self.validate(self.evidence(grant=replace(self.grant, observed_at=250))), 1150)
        self.config['profile']['valid_until'] = 1100
        self.assertEqual(self.validate(self.evidence(price_valid_until=1100)), 1100)
        self.policy['stop_at'] = 1090
        self.assertEqual(self.validate(self.evidence(price_valid_until=1100)), 1090)
        self.task['native']['windows'][0].update(reset_at=1050)
        self.assertEqual(self.validate(self.evidence(price_valid_until=1100)), 1090)
        self.task['native']['windows'][0].update(reset_at=2000, observed_at=101)
        self.assertEqual(self.validate(self.evidence(price_valid_until=1100)), 1090)

    def test_json_and_changed_digest_inputs_are_rejected(self):
        evidence = self.evidence()
        cases = [(None, {}), (asdict(evidence), {}), (replace(evidence, grant=asdict(self.grant)), {}),
                 (evidence, {'scope': 'other'}), (evidence, {'config': dict(self.config, revision=4)}),
                 (evidence, {'config': dict(self.config, org_revision=3)}),
                 (evidence, {'policy': dict(self.policy, pilot_limit=400)}),
                 (evidence, {'config': dict(self.config, binding=dict(self.config['binding'], project='other'))}),
                 (evidence, {'config': dict(self.config, profile=dict(self.config['profile'], count_maximum=0))}),
                 (evidence, {'task': dict(self.task, packet=dict(self.task['packet'], text='changed'))}),
                 (evidence, {'task': dict(self.task, native=native(remaining=80))})]
        for candidate, changes in cases:
            with self.subTest(changes=tuple(changes)), self.assertRaises(BudgetError):
                self.validate(candidate, **changes)

    def test_active_legacy_mode_allows_auto_and_requires_independent_reviewer(self):
        self.task['provider'] = 'auto'
        self.assertEqual(self.validate(self.evidence()), 1750)
        for change in (dict(enabled=False), dict(disabled=True), dict(live_consent=False), dict(binding=BINDING)):
            with self.subTest(config=tuple(change)), self.assertRaises(BudgetError):
                self.validate(self.evidence(), config=dict(self.config, **change))
        original = self.task.copy()
        for change in (dict(provider='openrouter'), dict(local_preferred=True),
                       dict(reviewer_available=False), dict(reviewer_available=1), dict(api_only='true')):
            self.task = dict(original, **change)
            with self.subTest(task=change), self.assertRaises(BudgetError):
                self.validate(self.evidence())
        self.task = original
        for change in (dict(reviewer='coordinator-session'), dict(reviewer=' '), dict(coordinator=True)):
            with self.subTest(identity=change), self.assertRaises(BudgetError):
                self.validate(self.evidence(**change))

    def test_approval_requires_current_times_and_a_finite_positive_pilot_bound(self):
        for change in (dict(approval_id=''), dict(approved_at=1001), dict(approval_until=1000),
                       dict(maximum_usd_micros=0), dict(maximum_usd_micros=True),
                       dict(maximum_usd_micros=501), dict(maximum_usd_micros=float('inf'))):
            with self.subTest(approval=change), self.assertRaises(BudgetError):
                self.validate(self.evidence(**change))

    def test_live_smart_ignores_optional_quota_measurements(self):
        self.config.update(requested_mode='smart', live_consent=True)
        self.task['provider'] = 'auto'
        for windows in ([], None, [dict(remaining_pp=0, reset_at=0)]):
            self.task['native']['windows'] = windows
            self.assertEqual(self.validate(self.evidence()), 1750)

    def test_grant_must_match_platform_row_and_selected_pool(self):
        self.policy.update(ceiling='subscription_benefit', amount=None)
        for change in (dict(organization='other'), dict(project='other'), dict(expiry=9999),
                       dict(source='codex-dashboard'), dict(pool='mixed'), dict(pool='prepaid'),
                       dict(balance=True), dict(balance=-1), dict(observed_at=1001), dict(observed_at=100)):
            with self.subTest(grant=change), self.assertRaises(BudgetError):
                self.validate(self.evidence(grant=replace(self.grant, **change)))
        self.assertEqual(self.validate(self.evidence()), 1750)

    def test_stale_or_unpinned_price_rejected_and_quota_measurements_ignored(self):
        for change in (dict(price_observed_at=901), dict(price_valid_until=1799)):
            with self.subTest(price=change), self.assertRaises(BudgetError):
                self.validate(self.evidence(**change))
        self.config['profile']['valid_until'] = self.now
        with self.assertRaisesRegex(BudgetError, '^pricing_stale$'):
            self.validate(self.evidence(price_valid_until=self.now))
        self.config['profile']['valid_until'] = 1800
        for fields in (dict(observed_at=1001), dict(observed_at=100), dict(reset_at=1000)):
            self.task['native'] = native()
            self.task['native']['windows'][0].update(observed_at=980, reset_at=2000)
            self.task['native']['windows'][0].update(fields)
            with self.subTest(window=fields):
                self.assertGreater(self.validate(self.evidence()), 1000)

    def test_malformed_input_errors_do_not_echo_values(self):
        with self.assertRaisesRegex(BudgetError, '^invalid_live_admission$'):
            self.validate(object.__new__(usage_admission.Admission))
        with self.assertRaisesRegex(BudgetError, '^invalid_live_admission$'):
            self.validate(self.evidence(), config={'secret-marker': 'value'})
        with self.assertRaisesRegex(BudgetError, '^invalid_live_admission$'):
            usage_admission.digest({'secret-marker': object()})
