"""The canary boundary uses approved synthetic inputs and a mocked live runner."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import usage_admission
from runtime.usage_budget import BudgetError
from tests.test_usage_budget import native


BINDING = dict(organization='org-canary', project='proj_canary', storage_kind='macos_keychain',
               credential_ref='vcw_' + '0' * 32)
MODEL = 'vendor-light'  # invented: the compiled table's cheapest priced model for the canary's provider
PROFILE = dict(model=MODEL, reasoning='medium', tier='default', input_rate=2_000_000,
               cached_input_rate=100_000, cache_write_rate=5_000_000, output_rate=10_000_000,
               max_input_tokens=10_000, max_output_tokens=64, verified_at=100, valid_until=1000,
               count_maximum=0)


class UsageCanaryTests(unittest.TestCase):
    def setUp(self):
        from runtime import usage_canary
        self.canary = usage_canary
        self.native = native()
        self.native.pop('task')
        self.task = usage_canary.task_for('approved-canary', BINDING, self.native, MODEL)
        self.proof = usage_admission.Admission(
            scope='checkout', config_revision=2, org_revision=1, policy_digest='0' * 64,
            binding_digest=usage_admission.digest(BINDING), profile_digest=usage_admission.digest(PROFILE),
            task_digest=usage_admission.digest(self.task),
            grant=usage_admission.Grant('org-canary', 'proj_canary', 800_000, 2000, 100,
                                        'platform-credit-grants', 'benefit'),
            price_observed_at=100, price_valid_until=1000, approval_id='approved-canary', approved_at=100,
            approval_until=400, maximum_usd_micros=50_640, coordinator='coordinator', reviewer='reviewer')

    def validate(self, proof=None, **changes):
        return self.canary.validate(self.proof if proof is None else proof, **(
            dict(binding=BINDING, profile=PROFILE, task=self.task, now=101) | changes))

    def test_exact_payload_has_no_source_and_ids_ignore_credential_rotation(self):
        self.assertEqual(self.task['packet'], dict(version=1, model=MODEL, reasoning='medium',
                                                  text='Reply with the word READY.', input_tokens=10_000,
                                                  max_output_tokens=64))
        self.assertEqual((self.task['class_name'], self.task['provider'], self.task['snapshots']),
                         ('canary', 'openai-api', []))
        self.assertIs(self.task['api_only'], True)
        self.assertIs(self.task['local_preferred'], False)
        self.assertEqual(self.task['native']['task'], self.task['task_id'])
        self.assertNotIn('task', self.native)
        rotated = dict(BINDING, credential_ref='vcw_' + '1' * 32)
        self.assertEqual(self.canary.task_for('approved-canary', rotated, self.native, MODEL), self.task)
        for approval, binding in (('new-approval', BINDING), ('approved-canary', dict(BINDING, project='proj_other'))):
            other = self.canary.task_for(approval, binding, self.native, MODEL)
            self.assertNotEqual(self.task['run_id'], other['run_id'])

    def test_source_or_payload_changes_are_rejected_even_if_repinned(self):
        for task in (dict(self.task, snapshots=[{'path': 'secret-marker', 'sha256': '0' * 64}]),
                     dict(self.task, packet=dict(self.task['packet'], text='secret-marker')),
                     dict(self.task, packet=dict(self.task['packet'], input_tokens=1)),
                     dict(self.task, api_only=False), dict(self.task, run_id='alternate-attempt')):
            proof = replace(self.proof, task_digest=usage_admission.digest(task))
            with self.subTest(changed=task.keys()), self.assertRaisesRegex(BudgetError, '^canary_task_changed$'):
                self.validate(proof, task=task)

    def test_approval_price_and_grant_expiry_bind_the_cap(self):
        self.validate()
        for changes in (dict(approval_until=401), dict(approved_at=102), dict(maximum_usd_micros=50_641),
                        dict(maximum_usd_micros=50_639), dict(grant=replace(self.proof.grant, expiry=101)),
                        dict(price_valid_until=999), dict(approval_id='alternate-approval')):
            with self.subTest(changes=tuple(changes)), self.assertRaises(BudgetError):
                self.validate(replace(self.proof, **changes))
        with self.assertRaises(BudgetError):
            self.validate(now=400)
        for changes in (dict(count_maximum=1), dict(max_input_tokens=10_001), dict(max_output_tokens=65),
                        dict(model='other-model'), dict(tier='priority'), dict(cache_write_rate=5_001_000),
                        dict(cached_input_rate=99_000)):
            profile = dict(PROFILE, **changes)
            with self.subTest(profile=changes), self.assertRaises(BudgetError):
                self.validate(replace(self.proof, profile_digest=usage_admission.digest(profile)), profile=profile)

    def test_profile_for_keeps_the_verified_rate_card_and_observation_window(self):
        self.assertEqual(self.canary.profile_for(101, 999, MODEL), dict(PROFILE, verified_at=101, valid_until=999))
        with self.assertRaises(BudgetError):
            self.canary.profile_for(101, 101, MODEL)

    def table_home(self, rows):
        """A private HOME whose routing state holds a compiled table with `rows`."""
        home = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, home, True)
        state = home / '.local/state/vibe-workflow/routing'
        state.mkdir(parents=True, mode=0o700)
        for parent in (home / '.local/state/vibe-workflow', home / '.local/state', home / '.local'):
            parent.chmod(0o700)
        table = state / 'strategy-table.json'
        table.write_text(json.dumps(dict(schema=2, version='t', rows=rows, display_names={})))
        table.chmod(0o600)
        return patch.dict(os.environ, HOME=str(home.resolve()))

    def test_canary_takes_the_cheapest_priced_model_for_its_provider(self):
        def strategy(model, provider, price):
            return dict(strategy_id=model, model=model, effort=None, harness='h', provider=provider,
                        evidence_refs=['g'], list_price_micros=price)
        rows = {'a|low|low': [strategy('vendor-free', 'other-api', 1), strategy('vendor-unpriced', 'openai-api', None),
                              strategy('vendor-heavy', 'openai-api', 9_000_000)],
                'b|low|low': [strategy('vendor-light', 'openai-api', 400_000), strategy('vendor-mid', 'openai-api', 2_000_000)]}
        with self.table_home(rows):
            self.assertEqual(self.canary.model_for(), 'vendor-light')
            self.assertEqual(self.canary.profile_for(101, 999), dict(PROFILE, verified_at=101, valid_until=999))
            self.assertEqual(self.canary.task_for('approved-canary', BINDING, self.native), self.task)
        self.assertFalse(hasattr(self.canary, '_MODEL'))

    def test_canary_refuses_without_a_priced_model(self):
        unpriced = dict(strategy_id='u', model='vendor-unpriced', effort=None, harness='h', provider='openai-api',
                        evidence_refs=['g'], list_price_micros=None)
        other = dict(unpriced, model='vendor-other', provider='other-api', list_price_micros=1)
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, empty, True)
        for home in (self.table_home({'a|low|low': [unpriced, other]}), self.table_home({}),
                     patch.dict(os.environ, HOME=str(empty))):
            with home, self.assertRaisesRegex(BudgetError, '^canary_model_unavailable$'):
                self.canary.profile_for(101, 999)
            with home, self.assertRaisesRegex(BudgetError, '^canary_model_unavailable$'):
                self.canary.task_for('approved-canary', BINDING, self.native)

    def test_run_uses_one_admitted_attempt_and_never_retries_a_failure(self):
        with patch('runtime.usage_canary.time.time', return_value=101), patch(
            'runtime.usage_canary.usage_routing._run_live', side_effect=BudgetError('transport_failure')
        ) as runner:
            with self.assertRaisesRegex(BudgetError, '^transport_failure$'):
                self.canary.run('/checkout', self.proof, binding=BINDING, profile=PROFILE, native=self.native)
            runner.assert_called_once_with('/checkout', self.task, self.proof)

    def test_bad_evidence_never_reaches_runner(self):
        with patch('runtime.usage_canary.time.time', return_value=101), patch(
            'runtime.usage_canary.usage_routing._run_live'
        ) as runner:
            for proof in (asdict(self.proof), replace(self.proof, binding_digest='0' * 64),
                          replace(self.proof, task_digest='0' * 64), replace(self.proof, approval_until=101)):
                with self.assertRaises(BudgetError):
                    self.canary.run('/checkout', proof, binding=BINDING, profile=PROFILE, native=self.native)
            runner.assert_not_called()
