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
CARD = {key: PROFILE[key] for key in ('model', 'input_rate', 'cached_input_rate', 'cache_write_rate', 'output_rate')}


class UsageCanaryTests(unittest.TestCase):
    def setUp(self):
        from runtime import usage_canary
        self.canary = usage_canary
        self.native = native()
        self.native.pop('task')
        self.task = usage_canary.task_for('approved-canary', BINDING, self.native, CARD)
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
        self.assertEqual(self.canary.task_for('approved-canary', rotated, self.native, CARD), self.task)
        for approval, binding in (('new-approval', BINDING), ('approved-canary', dict(BINDING, project='proj_other'))):
            other = self.canary.task_for(approval, binding, self.native, CARD)
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
        self.assertEqual(self.canary.profile_for(101, 999, CARD), dict(PROFILE, verified_at=101, valid_until=999))
        with self.assertRaises(BudgetError):
            self.canary.profile_for(101, 101, CARD)

    def table_home(self, table):
        """A private HOME whose routing state holds `table` beside the settings."""
        home = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, home, True)
        state = home / '.local/state/vibe-workflow/routing'
        state.mkdir(parents=True, mode=0o700)
        for parent in (home / '.local/state/vibe-workflow', home / '.local/state', home / '.local'):
            parent.chmod(0o700)
        path = state / 'strategy-table.json'
        path.write_text(json.dumps(table))
        path.chmod(0o600)
        return patch.dict(os.environ, HOME=str(home.resolve()))

    @staticmethod
    def compiled(*entries):
        """A table compiled from compiler-shaped catalog entries (one guidance row cites each)."""
        from runtime import routing_evidence
        catalog = [dict(model=model, harnesses=[harness], supported_efforts=['medium'], provider=provider,
                        **({} if price is None else dict(list_price=price))) for model, provider, harness, price in entries]
        guidance = [dict(id='g', sha256='1' * 64, checked_at='2026-11-08T00:00:00Z', rows=[dict(
            work_kind='implementation', complexity_band='low', risk_band='low', strategies=[
                dict(model=model, effort='medium', harness=harness, evidence_refs=['g'])
                for model, _, harness, _ in entries])])]
        return routing_evidence.compile_table(guidance, catalog, [], now='2026-11-08T00:00:00Z')

    def test_canary_takes_the_cheapest_priced_model_for_its_provider(self):
        table = self.compiled(('vendor-free', 'other', 'api', dict(input=0.01, output=0.01)),
                              ('vendor-native', 'openai', 'codex-native', dict(input=0.02, output=0.02)),
                              ('vendor-unpriced', 'openai', 'api', None),
                              ('vendor-heavy', 'openai', 'api', dict(input=9, output=30)),
                              ('vendor-light', 'openai', 'api', dict(input=0.4, output=1.6)))
        with self.table_home(table):
            # The API host identity (provider openai, harness api), never the service selector or a native row.
            self.assertEqual(self.canary.candidate(), dict(model='vendor-light', list_input_rate=400_000,
                                                           list_output_rate=1_600_000))
        self.assertEqual(self.canary.SERVICE, 'openai-api')  # the task's service selector is unchanged
        self.assertEqual(self.task['provider'], 'openai-api')
        self.assertFalse(hasattr(self.canary, '_MODEL'))
        self.assertFalse(hasattr(self.canary, 'model_for'))  # execution never re-selects from the table

    def test_canary_refuses_without_a_priced_model(self):
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, empty, True)
        unpriced = self.compiled(('vendor-unpriced', 'openai', 'api', None),
                                 ('vendor-native', 'openai', 'codex-native', dict(input=0.1, output=0.1)))
        for home in (self.table_home(unpriced), self.table_home({}), patch.dict(os.environ, HOME=str(empty))):
            with home, self.assertRaisesRegex(BudgetError, '^canary_model_unavailable$'):
                self.canary.candidate()
        blended = self.compiled(('vendor-light', 'openai', 'api', dict(input=0.4, output=1.6)))
        blended.pop('list_prices')  # a blended 3:1 price alone is no per-side evidence
        with self.table_home(blended), self.assertRaisesRegex(BudgetError, '^canary_price_evidence_missing$'):
            self.canary.candidate()

    def test_profile_comes_only_from_the_approved_card(self):
        card = dict(model=MODEL, input_rate=2_000_000, cached_input_rate=100_000, cache_write_rate=5_000_000,
                    output_rate=10_000_000)
        self.assertEqual(self.canary.profile_for(101, 999, card), dict(PROFILE, verified_at=101, valid_until=999))
        for bad in (dict(card, input_rate=0), dict(card, output_rate=-1), dict(card, model=''), dict(card, extra=1),
                    {k: v for k, v in card.items() if k != 'cache_write_rate'}, None):
            with self.subTest(bad=bad), self.assertRaises(BudgetError):
                self.canary.profile_for(101, 999, bad)

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
