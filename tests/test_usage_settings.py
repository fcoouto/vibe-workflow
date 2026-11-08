"""Private local settings never acquire live funding or change enrollment."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import usage_routing as routing, usage_settings as settings
from runtime import usage_budget
from runtime.usage_budget import BudgetError
from tests.test_smart_balance import BINDING, POLICY, qualification, task
from tests.test_usage_budget import native
from tests.test_usage_routing import PROFILE


class UsageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / 'private'
        self.addCleanup(self.temp.cleanup)
        for mock in (patch.object(settings,'scope',return_value=('scope',self.root)),
                     patch.object(settings,'directory',return_value=self.folder),
                     patch('time.time',return_value=101)):
            mock.start(); self.addCleanup(mock.stop)
        self.request = dict(version=1,expected_revision=0,expected_org_revision=0,
                            binding=BINDING,policy=POLICY,profile=PROFILE,class_name='investigation',mode='smart')

    def test_read_only_lookup_never_initializes_missing_state(self):
        result = routing.evaluate(self.root,{})
        self.assertEqual(result['decision'],'paused')
        self.assertFalse(self.folder.exists())

    def test_keychain_binding_persists_only_metadata_without_access(self):
        binding = dict(organization='org-example', project='proj_example',
                       credential_ref='vcw_' + '0' * 32, storage_kind='macos_keychain')
        with patch('runtime.usage_credentials._keychain') as keychain:
            result = routing.configure(self.root, dict(self.request, version=2, binding=binding))
            self.assertEqual(result['mode'], 'off')
            with settings.controller(self.root) as controller:
                data = controller.ledger.snapshot()
                self.assertEqual(controller._config(data)['binding'], binding)
                self.assertFalse(controller.status()['live_dispatch'])
            with self.assertRaisesRegex(BudgetError, 'invalid_credential_reference'):
                routing.configure(self.root, dict(self.request, version=2, binding=BINDING))
            keychain.assert_not_called()

    def test_configure_persists_off_and_disable_only_changes_local_consent(self):
        result = routing.configure(self.root,self.request)
        self.assertEqual(result['mode'],'off');self.assertEqual(result['requested_mode'],'smart')
        with settings.controller(self.root) as controller:
            self.assertFalse(controller.status()['live_dispatch'])
            self.assertTrue(controller.enable(1)['enabled'])
        self.assertEqual(routing.disable(self.root,dict(expected_revision=2))['reason'],'disabled')
        with self.assertRaises(BudgetError): routing.disable(self.root,dict(expected_revision=2))
        self.assertFalse((self.root / '.vibe-workflow').exists())
        self.assertFalse((self.root / '.env').exists())

    def test_live_consent_requires_explicit_keychain_enable_and_can_be_revoked(self):
        binding = dict(organization='org', project='project',
                       credential_ref='vcw_' + '0' * 32, storage_kind='macos_keychain')
        request = dict(self.request, version=2, binding=binding)
        self.assertFalse(routing.configure(self.root, request)['live_enabled'])
        with settings.controller(self.root) as control, control.ledger.transaction() as state:
            config = control._config(state)
            config.update(enabled=True)
            config.pop('live_consent', None)
        self.assertFalse(routing.status(self.root)['live_enabled'])
        for revision, mode in ((1, 'manual'), (2, 'smart')):
            result = routing.enable(self.root, dict(expected_revision=revision, mode=mode))
            self.assertEqual(result['mode'], mode)
            self.assertTrue(result['live_enabled'])
            self.assertFalse(result['live_dispatch'])
        with patch('time.time', return_value=POLICY['stop_at']):
            self.assertFalse(routing.status(self.root)['live_enabled'])
            with self.assertRaisesRegex(BudgetError, 'expiry_or_cycle_boundary'):
                routing.enable(self.root, dict(expected_revision=3))
        self.assertFalse(routing.disable(self.root, dict(expected_revision=3))['live_enabled'])
        self.assertTrue(routing.enable(self.root, dict(expected_revision=4))['live_enabled'])
        result = routing.configure(self.root, dict(request, expected_revision=5, expected_org_revision=1))
        self.assertFalse(result['live_enabled'])

    def test_explicit_mode_enablement_preserves_shared_policy_and_off_recovery(self):
        configured = routing.configure(self.root, self.request)
        self.assertEqual(configured['mode'], 'off')
        with settings.controller(self.root) as controller:
            before = controller.ledger.snapshot()['organizations']
        manual = routing.enable(self.root, dict(expected_revision=1, mode='manual'))
        self.assertEqual(manual['mode'], 'manual')
        self.assertFalse(manual['live_dispatch'])
        self.assertFalse(manual['live_enabled'])
        self.assertTrue(manual['warnings'])
        with self.assertRaisesRegex(BudgetError, 'consent_revision_changed'):
            routing.enable(self.root, dict(expected_revision=1, mode='smart'))
        with self.assertRaisesRegex(BudgetError, 'invalid_mode'):
            routing.enable(self.root, dict(expected_revision=2, mode='paid-fallback'))
        smart = routing.enable(self.root, dict(expected_revision=2, mode='smart'))
        self.assertEqual(smart['mode'], 'smart')
        self.assertFalse(smart['live_dispatch'])
        self.assertEqual(routing.disable(self.root, dict(expected_revision=3))['mode'], 'off')
        with settings.controller(self.root) as controller:
            self.assertEqual(controller.ledger.snapshot()['organizations'], before)

    def test_canary_budget_cannot_enable_or_be_cleared_by_a_price_refresh(self):
        binding = dict(organization='org', project='project',
                       credential_ref='vcw_' + '0' * 32, storage_kind='macos_keychain')
        policy = dict(POLICY, cycle='canary-old')
        request = dict(self.request, version=2, binding=binding, policy=policy)
        routing.configure(self.root, request)
        with settings.controller(self.root) as control, control.ledger.transaction() as state:
            control._config(state).update(enabled=True, live_consent=True)
        state = routing.status(self.root)
        self.assertEqual((state['mode'], state['live_enabled'], state['reason']),
                         ('off', False, 'routing_budget_required'))
        self.assertEqual(routing.evaluate(self.root, task())['reason'], 'routing_budget_required')
        self.assertEqual(routing.evaluate(self.root, task(local_preferred=True))['decision'], 'native')
        request.update(expected_revision=1, expected_org_revision=1,
                       profile=dict(PROFILE, verified_at=101))
        self.assertEqual(routing.configure(self.root, request)['reason'], 'routing_budget_required')
        for mode in ('manual', 'smart'):
            with self.subTest(mode=mode), self.assertRaisesRegex(BudgetError, 'routing_budget_required'):
                routing.enable(self.root, dict(expected_revision=2, mode=mode))
        self.assertEqual(routing.status(self.root)['revision'], 2)

    def test_canary_budget_conversion_requires_the_existing_organization_and_cycle(self):
        binding = dict(organization='org', project='project',
                       credential_ref='vcw_' + '0' * 32, storage_kind='macos_keychain')
        policy = dict(POLICY, cycle='canary-old')
        request = dict(self.request, version=2, binding=binding, policy=policy, production_budget=True)
        with self.assertRaisesRegex(BudgetError, 'routing_budget_required'):
            routing.configure(self.root, request)
        original = dict(request)
        del original['production_budget']
        routing.configure(self.root, original)
        with settings.controller(self.root) as control:
            before = control.ledger.snapshot()
            with self.assertRaisesRegex(BudgetError, 'routing_budget_required'):
                control.configure(dict(request, expected_revision=1, binding=dict(binding, organization='other')))
            with self.assertRaisesRegex(BudgetError, 'overlapping_cycle'):
                control.configure(dict(request, expected_revision=1, expected_org_revision=1,
                                       policy=dict(policy, cycle='ordinary')))
            self.assertEqual(control.ledger.snapshot(), before)
            control.configure(dict(request, expected_revision=1, expected_org_revision=1))
            self.assertTrue(control.enable(2)['live_enabled'])

    def test_missing_database_with_saved_checkout_pin_never_recreates_allowance(self):
        routing.configure(self.root,self.request)
        (self.folder/'organizations.sqlite3').unlink()
        self.assertEqual(routing.status(self.root)['reason'], 'configuration_or_scope_unavailable')
        with self.assertRaises(BudgetError): routing.configure(self.root,self.request)
        self.assertFalse((self.folder/'organizations.sqlite3').exists())

    def test_another_checkout_cannot_initialize_over_lost_existing_authority(self):
        routing.configure(self.root,self.request)
        (self.folder/'organizations.sqlite3').unlink()
        with patch.object(settings,'scope',return_value=('other',self.root)), self.assertRaises(BudgetError):
            routing.configure(self.root,self.request)
        self.assertFalse((self.folder/'organizations.sqlite3').exists())

    def test_stale_checkout_revision_does_not_modify_shared_policy(self):
        routing.configure(self.root,self.request)
        changed=copy.deepcopy(self.request);changed['expected_org_revision']=1;changed['policy']['amount']=999
        with self.assertRaises(BudgetError): routing.configure(self.root,changed)
        with settings.controller(self.root) as controller:
            self.assertEqual(controller.ledger.snapshot()['organizations']['org']['policy']['amount'],500)

    def configure_source(self, scope='source', **updates):
        binding = dict(organization='org', project='api-project',
                       credential_ref='vcw_' + '0' * 32, storage_kind='macos_keychain')
        request = dict(self.request, version=2, binding=binding)
        request.update(updates)
        with patch.object(settings, 'scope', return_value=(scope, self.root)):
            return routing.configure(self.root, request)

    def reuse_request(self, source='source', **updates):
        candidate = next(item for item in routing.status(self.root)['reusable_setups'] if item['scope'] == source)
        return dict(expected_revision=0, reuse_scope=source, reuse_pin=candidate['setup_pin'], **updates)

    def test_initial_reuse_requires_the_discovered_setup_pin(self):
        self.configure_source()
        with self.assertRaisesRegex(BudgetError, 'reuse_pin_required'):
            routing.enable(self.root, dict(expected_revision=0))
        with self.assertRaisesRegex(BudgetError, 'reusable_setup_changed'):
            routing.enable(self.root, dict(expected_revision=0, reuse_pin='0' * 64))
        self.assertFalse((self.folder / 'checkout-scope.json').exists())

    def test_reuse_pin_rejects_changes_to_selected_setup_and_source_state(self):
        self.configure_source()
        request = self.reuse_request(mode='smart')
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control:
                before = control.ledger.snapshot()
        changes = (
            dict(policy=dict(POLICY, amount=900)),
            dict(profile=dict(PROFILE, max_output_tokens=999)),
            dict(binding=dict(before['routing']['source']['binding'], credential_ref='vcw_' + '1' * 32)),
            dict(class_name='summary'),
            {},
        )
        for change in changes:
            with self.subTest(change=change):
                self.configure_source(expected_revision=1, expected_org_revision=1, **change)
                with self.assertRaisesRegex(BudgetError, 'reusable_setup_changed'):
                    routing.enable(self.root, request)
                self.assertFalse((self.folder / 'checkout-scope.json').exists())
                with patch.object(settings, 'scope', return_value=('source', self.root)):
                    with settings.controller(self.root) as control, control.ledger.transaction() as data:
                        self.assertNotIn('scope', data['routing'])
                        data.clear(); data.update(copy.deepcopy(before))
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            routing.disable(self.root, dict(expected_revision=1))
        with self.assertRaisesRegex(BudgetError, 'reusable_setup_changed'):
            routing.enable(self.root, request)

    def test_selected_pin_survives_unrelated_checkout_changes(self):
        self.configure_source()
        request = self.reuse_request()
        self.configure_source('unrelated', expected_org_revision=1, class_name='summary')
        self.assertTrue(routing.enable(self.root, request)['live_enabled'])

    def test_reuse_pin_covers_budget_conversion_without_a_source_revision_change(self):
        self.configure_source()
        request = self.reuse_request()
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control, control.ledger.transaction() as data:
                data['routing']['source']['production_budget'] = True
        self.assertTrue(routing.status(self.root)['reusable_setups'][0]['production_budget'])
        with self.assertRaisesRegex(BudgetError, 'reusable_setup_changed'):
            routing.enable(self.root, request)
        self.assertFalse((self.folder / 'checkout-scope.json').exists())

    def test_replaced_ledger_cannot_supply_new_binding_authority(self):
        self.configure_source()
        original_pin = (self.folder / 'checkout-source.json').read_bytes()
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control:
                control.ledger.observe('org', 800, 100)
                control.ledger.begin('org', 'pending', 'pending', 'packet', 10, 1, 101, native('pending'))
                before = control.ledger.snapshot()
        database = self.folder / 'organizations.sqlite3'
        database.unlink()
        identity = usage_budget.initialize(database)
        with usage_budget.Ledger(database, identity) as ledger, ledger.transaction() as data:
            data.update(organizations=before['organizations'], routing=before['routing'])
        result = routing.status(self.root)
        self.assertEqual(result['reason'], 'configuration_or_scope_unavailable')
        self.assertEqual(result['reusable_setups'], [])
        for operation in (lambda: routing.enable(self.root, dict(expected_revision=0)),
                          lambda: routing.configure(self.root, dict(self.request, expected_org_revision=1))):
            with self.assertRaisesRegex(BudgetError, 'manual_recovery_required'):
                operation()
        self.assertEqual((self.folder / 'checkout-source.json').read_bytes(), original_pin)
        self.assertFalse((self.folder / 'checkout-scope.json').exists())
        with usage_budget.Ledger(database, identity) as ledger:
            self.assertEqual(ledger.snapshot()['runs'], {})
            self.assertNotIn('scope', ledger.snapshot()['routing'])

    def test_missing_source_pin_blocks_discovery_enable_and_new_configuration(self):
        self.configure_source()
        (self.folder / 'checkout-source.json').unlink()
        self.assertEqual(routing.status(self.root)['reusable_setups'], [])
        for operation in (lambda: routing.enable(self.root, dict(expected_revision=0)),
                          lambda: routing.configure(self.root, dict(self.request, expected_org_revision=1))):
            with self.assertRaisesRegex(BudgetError, 'manual_recovery_required'):
                operation()
        self.assertFalse((self.folder / 'checkout-scope.json').exists())

    def test_source_authority_is_rechecked_inside_the_enable_transaction(self):
        self.configure_source()
        request = self.reuse_request()
        with settings.controller(self.root, attach=True) as control:
            (self.folder / 'checkout-source.json').unlink()
            with self.assertRaisesRegex(BudgetError, 'manual_recovery_required'):
                control.enable(0, reuse_scope='source', reuse_pin=request['reuse_pin'])
            self.assertNotIn('scope', control.ledger.snapshot()['routing'])
        self.assertFalse((self.folder / 'checkout-scope.json').exists())

    def test_explicit_mode_resolves_otherwise_equivalent_saved_modes(self):
        self.configure_source()
        self.configure_source('manual-source', expected_org_revision=1, mode='manual')
        state = routing.status(self.root)
        self.assertEqual(state['reuse_reason'], 'reuse_selection_required')
        self.assertEqual(state['reuse_reason_by_mode'], {'manual': 'reuse_available', 'smart': 'reuse_available'})
        pin = next(item['setup_pin'] for item in state['reusable_setups'] if item['scope'] == 'source')
        with self.assertRaisesRegex(BudgetError, 'reuse_selection_required'):
            routing.enable(self.root, dict(expected_revision=0, reuse_pin=pin))
        for mode in ('manual', 'smart'):
            with patch.object(settings, 'scope', return_value=('target-' + mode, self.root)):
                result = routing.enable(self.root, dict(expected_revision=0, mode=mode, reuse_pin=pin))
                self.assertEqual(result['mode'], mode)

    def test_status_explains_profile_conversion_and_connection_differences(self):
        self.configure_source()
        self.configure_source('different', expected_org_revision=1,
            profile=dict(PROFILE, max_output_tokens=999, output_rate=30, valid_until=999),
            binding=dict(organization='org', project='api-project',
                         credential_ref='vcw_' + '1' * 32, storage_kind='macos_keychain'))
        self.configure_source('connection-only', expected_org_revision=1,
            binding=dict(organization='org', project='api-project',
                         credential_ref='vcw_' + '2' * 32, storage_kind='macos_keychain'))
        candidates = {item['scope']: item for item in routing.status(self.root)['reusable_setups']}
        self.assertEqual(candidates['source']['profile']['max_output_tokens'], 1000)
        self.assertEqual(candidates['different']['profile']['max_output_tokens'], 999)
        self.assertEqual(candidates['different']['profile']['output_rate'], 30)
        self.assertEqual(candidates['different']['profile']['valid_until'], 999)
        self.assertFalse(candidates['source']['production_budget'])
        self.assertNotEqual(candidates['source']['connection'], candidates['different']['connection'])
        self.assertEqual(candidates['source']['profile'], candidates['connection-only']['profile'])
        self.assertNotEqual(candidates['source']['connection'], candidates['connection-only']['connection'])
        self.assertEqual(candidates['connection-only']['connection']['storage_kind'], 'macos_keychain')
        self.assertIsNone(candidates['connection-only']['connection']['key_name'])
        self.assertNotIn('credential_ref', json.dumps(candidates))
        self.assertNotIn('vcw_', json.dumps(candidates))

    def test_second_checkout_configuration_keeps_first_live_under_identical_policy(self):
        self.configure_source()
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            routing.enable(self.root, dict(expected_revision=1))
        self.configure_source('second', expected_org_revision=1)
        with patch.object(settings, 'scope', return_value=('second', self.root)):
            self.assertTrue(routing.enable(self.root, dict(expected_revision=1))['live_enabled'])
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            self.assertTrue(routing.status(self.root)['live_enabled'])
        routing.configure(self.root, dict(self.request, expected_org_revision=1, policy=dict(POLICY, amount=400)))
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            self.assertFalse(routing.status(self.root)['live_enabled'])

    def test_reuse_enables_target_without_copying_permission_qualification_or_accounting(self):
        self.configure_source()
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control:
                control.record_qualification(qualification())
                with control.ledger.transaction() as state:
                    control._config(state)['external_permission'] = {'source-only': True}
                control.ledger.observe('org', 800, 100)
                control.ledger.begin('org', 'settled', 'settled', 'packet', 10, 1, 101, native('settled'))
                control.ledger.dispatch('settled', 'count', 1, 102)
                control.ledger.complete('settled', 'count', 3)
                control.ledger.close_run('settled')
                control.ledger.begin('org', 'pending', 'pending', 'packet', 10, 1, 103, native('pending'))
                before = control.ledger.snapshot()
        files = set(self.folder.iterdir())
        state = routing.status(self.root)
        self.assertEqual([item['scope'] for item in state['reusable_setups']], ['source'])
        self.assertEqual(state['reusable_setups'][0]['api_project'], 'api-project')
        self.assertNotIn('credential_ref', json.dumps(state))
        self.assertEqual(set(self.folder.iterdir()), files)
        self.assertEqual(state['revision'], 0)
        self.assertFalse(state['live_enabled'])
        with patch('runtime.usage_credentials._keychain', side_effect=AssertionError('credential access forbidden')), \
             patch('socket.socket', side_effect=AssertionError('network forbidden')):
            result = routing.enable(self.root, self.reuse_request(mode='manual'))
        self.assertTrue(result['live_enabled'])
        self.assertEqual((result['revision'], result['mode']), (1, 'manual'))
        with settings.controller(self.root) as control:
            after = control.ledger.snapshot()
            for key in ('identity', 'organizations', 'runs', 'native'):
                self.assertEqual(after[key], before[key])
            target = control._config(after)
            self.assertIsNone(target['qualification'])
            self.assertEqual(target['qualification_revision'], 0)
            self.assertNotIn('external_permission', target)
            self.assertEqual(after['routing']['source'], before['routing']['source'])
        self.assertFalse(routing.disable(self.root, dict(expected_revision=1))['live_enabled'])
        with self.assertRaisesRegex(BudgetError, 'already_configured'):
            routing.enable(self.root, dict(expected_revision=2, reuse_scope='source'))
        with self.assertRaisesRegex(BudgetError, 'consent_revision_changed'):
            routing.enable(self.root, dict(expected_revision=0, reuse_scope='source'))
        self.assertEqual(routing.enable(self.root, dict(expected_revision=2))['revision'], 3)

    def test_reuse_ambiguity_requires_explicit_selection_and_equivalents_collapse(self):
        self.configure_source()
        self.configure_source('same', expected_org_revision=1)
        request = self.reuse_request()
        del request['reuse_scope']
        self.assertTrue(routing.enable(self.root, request)['live_enabled'])
        with patch.object(settings, 'scope', return_value=('unconfigured', self.root)):
            self.configure_source('different', expected_org_revision=1, class_name='summary')
            with self.assertRaisesRegex(BudgetError, 'reuse_selection_required'):
                routing.enable(self.root, dict(expected_revision=0))
            self.assertFalse((self.folder / 'checkout-unconfigured.json').exists())
            result = routing.enable(self.root, self.reuse_request('different'))
            self.assertTrue(result['live_enabled'])
            with settings.controller(self.root) as control:
                self.assertEqual(control._config(control.ledger.snapshot())['class_name'], 'summary')

    def test_failed_reuse_preserves_ledger_and_does_not_pin_target(self):
        self.configure_source()
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control:
                before = control.ledger.snapshot()
        for request, reason in ((dict(expected_revision=1), 'consent_revision_changed'),
                                (dict(expected_revision=0, reuse_scope='unknown'), 'reusable_setup_unavailable'),
                                (dict(expected_revision=0, reuse_scope=4), 'invalid_identity'),
                                (dict(expected_revision=0, surprise=True), 'invalid_fields')):
            with self.subTest(request=request), self.assertRaisesRegex(BudgetError, reason):
                routing.enable(self.root, request)
        self.assertFalse((self.folder / 'checkout-scope.json').exists())
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control:
                self.assertEqual(control.ledger.snapshot(), before)

    def test_binding_write_failure_rolls_back_reused_consent(self):
        self.configure_source()
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control:
                before = control.ledger.snapshot()
        request = self.reuse_request()
        with patch('runtime.usage_settings.os.open', side_effect=OSError('disposable write failure')):
            with self.assertRaises(OSError):
                routing.enable(self.root, request)
        with patch.object(settings, 'scope', return_value=('source', self.root)):
            with settings.controller(self.root) as control:
                self.assertEqual(control.ledger.snapshot(), before)

    def test_reuse_rejects_stale_expired_smoke_and_simulation_setups(self):
        self.configure_source()
        with settings.controller(self.root, create=True) as control:
            before = control.ledger.snapshot()
        changes = (
            ('stale-policy', lambda config, org: config.update(org_revision=0)),
            ('expired-profile', lambda config, org: config['profile'].update(valid_until=101)),
            ('future-profile', lambda config, org: config['profile'].update(verified_at=102)),
            ('expired-policy', lambda config, org: org['policy'].update(stop_at=101)),
            ('smoke', lambda config, org: org['policy'].update(cycle='canary-test')),
            ('simulation', lambda config, org: config.update(binding=BINDING.copy())),
            ('blocked', lambda config, org: org.update(blocked=True)),
            ('canary-owner', lambda config, org: config.update(canary_owner='active')),
            ('manual-test-owner', lambda config, org: config.update(manual_test_owner='active')),
        )
        for label, change in changes:
            with self.subTest(label=label):
                with settings.controller(self.root) as control, control.ledger.transaction() as state:
                    state.clear(); state.update(copy.deepcopy(before))
                    change(state['routing']['source'], state['organizations']['org'])
                self.assertEqual(routing.status(self.root)['reusable_setups'], [])
                with self.assertRaisesRegex(BudgetError, 'reusable_setup_unavailable'):
                    routing.enable(self.root, dict(expected_revision=0, reuse_scope='source'))
        with settings.controller(self.root) as control, control.ledger.transaction() as state:
            state.clear(); state.update(before)
        with self.assertRaisesRegex(BudgetError, 'reusable_setup_unavailable'):
            routing.enable(self.root, dict(expected_revision=0, reuse_scope='missing'))
        with self.assertRaisesRegex(BudgetError, 'consent_revision_changed'):
            routing.enable(self.root, dict(expected_revision=1))

    def test_enable_without_existing_state_never_creates_an_allowance(self):
        with self.assertRaisesRegex(BudgetError, 'not_configured'):
            routing.enable(self.root, dict(expected_revision=0))
        self.assertFalse(self.folder.exists())

    def test_reuse_does_not_recover_a_lost_ledger_or_bypass_enrollment(self):
        self.configure_source()
        database = self.folder / 'organizations.sqlite3'
        database.unlink()
        with self.assertRaises(BudgetError):
            routing.enable(self.root, dict(expected_revision=0))
        self.assertFalse(database.exists())
        with patch.object(settings, 'scope', side_effect=BudgetError('enrollment_unavailable_or_changed')):
            with self.assertRaisesRegex(BudgetError, 'enrollment_unavailable_or_changed'):
                routing.enable(self.root, dict(expected_revision=0))

    def test_corrupt_private_database_is_reported_as_recovery_required(self):
        routing.configure(self.root,self.request)
        (self.folder/'organizations.sqlite3').write_bytes(b'not a database')
        with self.assertRaisesRegex(BudgetError,'manual_recovery_required'):
            with settings.controller(self.root): pass

    def test_symlink_marker_is_rejected(self):
        routing.configure(self.root,self.request)
        marker=self.folder/'checkout-scope.json'
        other=self.folder/'other.json';marker.rename(other);marker.symlink_to(other)
        with self.assertRaises(BudgetError):
            with settings.controller(self.root): pass

if __name__ == '__main__': unittest.main()
