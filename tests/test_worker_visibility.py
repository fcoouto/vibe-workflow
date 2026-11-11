import copy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from runtime import worker_visibility
from runtime.adapters import codex
from runtime.startup import StartupError
from tests.fixture_worker_visibility import NativeVisibilityFixture, fixture_transport


class RosterDeclarationTests(unittest.TestCase):
    """F2.4: new declarations name a roster specialist (version 2); stored version 1 declarations read as written."""
    def test_declaration_takes_a_roster_id_and_shows_its_label(self):
        from runtime import habblaud
        value = dict(version=2, role='code-reviewer', specialty='Parser safety')
        self.assertEqual(worker_visibility.declaration(value), value)
        self.assertEqual(worker_visibility.display_role(value), 'Code Reviewer')
        self.assertEqual(habblaud.display_label(value, 'Model X'), 'Code Reviewer · Model X')
        custom = dict(value, custom_role_name='Safety reviewer')
        self.assertEqual(habblaud.display_label(custom, 'Model X'), 'Safety reviewer · Model X')
        self.assertFalse(hasattr(worker_visibility, 'ROLES'))

    def test_new_version_1_declaration_is_refused(self):
        for bad in (dict(version=1, role='Reviewer', specialty='s'),
                    dict(version=2, role='Reviewer', specialty='s'), dict(version=2, role='review', specialty='s'),
                    dict(version=2, role='check', specialty='s'), dict(version=2, role='code-reviewer', specialty=''),
                    dict(version=2, role='code-reviewer', specialty='s', extra=1), dict(version=True, role='debugger',
                                                                                         specialty='s')):
            with self.subTest(bad), self.assertRaisesRegex(ValueError, 'invalid_visibility'):
                worker_visibility.declaration(bad)
            self.assertIsNone(worker_visibility.optional_declaration(bad))

    def test_stored_version_1_declaration_is_shown_as_written(self):
        from runtime import habblaud
        stored = dict(version=1, role='Reviewer', specialty='Security assessment')
        self.assertEqual(worker_visibility.stored_declaration(stored), stored)
        self.assertEqual(habblaud.display_label(stored, 'Model X'), 'Reviewer · Model X')
        custom = dict(stored, custom_role_name='Threat reviewer · 東京')
        self.assertEqual(habblaud.display_label(custom, 'Model X'), 'Threat reviewer · 東京 · Model X')
        for bad in (dict(stored, role=''), dict(stored, version=3), dict(stored, extra=1), 'Reviewer'):
            with self.subTest(bad), self.assertRaisesRegex(ValueError, 'invalid_visibility'):
                worker_visibility.stored_declaration(bad)


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        try:
            from runtime import habblaud, worker_visibility
        except ImportError:
            self.fail('Shared native/OpenRouter visibility implementation is missing')
        self.api = habblaud
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.metadata = dict(version=2, role='code-reviewer', specialty='Security assessment',
                             custom_role_name='Threat reviewer · 東京')
        self.parent, self.child = str(uuid.uuid4()), str(uuid.uuid4())
        self.binding = dict(parent=self.parent, agent_id=self.child,
                            label='Threat reviewer · 東京 · Native model', cwd=str(self.root))
        self.client, self.transport = fixture_transport()

    def publisher(self, binding=None, metadata=None):
        return self.api.publisher(self.transport, 'http://127.0.0.1:1', 'account',
            binding or self.binding, metadata or self.metadata, self.root / 'delivery.json')

    def external_args(self):
        return dict(module=self.transport, endpoint='http://127.0.0.1:1', account='account',
                    parent='018e5cb4-73ab-7bed-ae5a-c912132405db', checkout=str(self.root),
                    run_id='fixture-run', execution_route='openai-api',
                    model_display_name='GPT-6.1 Sol', visibility=self.metadata | dict(role='explorer-agent'),
                    state_directory=self.root)

    def test_external_registration_replays_exact_legacy_identity_and_canonical_label(self):
        args = self.external_args()
        external_id = 'e16ee46f-5dff-57a5-a771-6e5147660779'
        receipt = self.root / f'habblaud-{external_id}.json'
        for _ in range(2):
            self.assertEqual(self.api.register_external(**args), dict(delivered=True))
        self.assertEqual(self.client.events, [
            dict(hook_event_name=name, session_id=args['parent'], agent_id=external_id,
                 agent_type='Explorer Agent · GPT-6.1 Sol', cwd=str(self.root), **activity)
            for name, activity in (
                ('SubagentStart', {}),
                ('PreToolUse', dict(tool_name='completed', tool_use_id='fixture-activity', tool_input={})),
                ('SubagentStop', {}),
            )
        ])
        saved = json.loads(receipt.read_text())
        self.assertEqual(saved['visibility'], args['visibility'])
        self.assertTrue(saved['stopped'])
        self.client.agents.clear()
        self.assertEqual(self.api.register_external(**args),
                         dict(delivered=False, reason='visibility_unconfirmed'))
        self.assertEqual(len(self.client.events), 3)

    def test_external_registration_replays_flat_legacy_receipt_without_start(self):
        args = self.external_args()
        external_id = 'e16ee46f-5dff-57a5-a771-6e5147660779'
        receipt = self.root / f'habblaud-{external_id}.json'
        self.api.save(receipt, dict(delivered=True, agent_id=external_id, parent=args['parent'],
                                    account=args['account'], phase='completed', observed_at=1))
        self.assertEqual(self.api.register_external(**args),
                         dict(delivered=False, reason='visibility_unconfirmed'))
        self.assertEqual(self.client.events, [])
        self.client.agents.append(dict(id='account:' + external_id, parentId='account:' + args['parent'],
                                       role='Explorer Agent · GPT-6.1 Sol'))
        self.assertEqual(self.api.register_external(**args), dict(delivered=True))
        self.assertEqual([event['hook_event_name'] for event in self.client.events],
                         ['PreToolUse', 'SubagentStop'])

    def test_external_registration_reuses_unreported_receipt_after_fallback_restoration(self):
        args = self.external_args() | dict(visibility=None)
        external_id = 'e16ee46f-5dff-57a5-a771-6e5147660779'
        receipt = self.root / f'habblaud-{external_id}.json'
        binding = dict(parent=args['parent'], agent_id=external_id,
                       label='Role not reported · GPT-6.1 Sol', cwd=str(self.root))
        self.api.save(receipt, dict(version=1, binding=binding, visibility=None,
                                   account=args['account'], delivered=True,
                                   started=True, stopped=False, phase='Working'))
        self.client.agents.append(dict(id='account:' + external_id,
                                       parentId='account:' + args['parent'], role='Subagent (Codex)'))
        self.assertEqual(self.api.register_external(**args), dict(delivered=True))
        self.assertEqual([event['hook_event_name'] for event in self.client.events],
                         ['PreToolUse', 'SubagentStop'])
        self.assertTrue(self.api.read(receipt)['stopped'])

    def test_unreported_registration_confirms_only_standard_localized_fallbacks(self):
        args = self.external_args() | dict(visibility=None)
        external_id = 'e16ee46f-5dff-57a5-a771-6e5147660779'
        row = dict(id='account:' + external_id, parentId='account:' + args['parent'])
        for role in ('Role not reported · GPT-6.1 Sol', 'Default', 'Reviewer · Wrong model'):
            with self.subTest(role=role):
                self.client.agents = [row | dict(role=role)]
                self.assertEqual(self.api.register_external(**args),
                                 dict(delivered=False, reason='habblaud_binding_mismatch'))
                self.assertEqual(self.client.events, [])
        for role in ('Subagente (Codex)', 'Subagent (Codex)', 'Sous-agent (Codex)'):
            with self.subTest(role=role):
                self.client.agents = [row | dict(role=role)]
                self.assertEqual(self.api.register_external(**args), dict(delivered=True))

    def test_external_registration_preserves_conflicting_frozen_binding(self):
        args = self.external_args()
        external_id = 'e16ee46f-5dff-57a5-a771-6e5147660779'
        receipt = self.root / f'habblaud-{external_id}.json'
        binding = dict(parent=args['parent'], agent_id=external_id,
                       label='Explorer Agent · GPT-6.1 Sol', cwd=str(self.root))
        frozen = dict(version=1, binding=binding, visibility=args['visibility'], account=args['account'],
                      delivered=True, started=True, stopped=True, phase='completed')
        for change in (dict(label='Historical alias · GPT-6.1 Sol'), dict(parent=self.parent)):
            with self.subTest(change=change):
                self.api.save(receipt, frozen | dict(binding=binding | change))
                before = receipt.read_bytes()
                self.assertEqual(self.api.register_external(**args),
                                 dict(delivered=False, reason='visibility_binding_changed'))
                self.assertEqual(receipt.read_bytes(), before)
        self.api.save(receipt, frozen | dict(account='other-account'))
        before = receipt.read_bytes()
        self.assertEqual(self.api.register_external(**args),
                         dict(delivered=False, reason='visibility_binding_changed'))
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(self.client.events, [])

    def test_external_registration_never_relabels_conflicting_receiver_binding(self):
        args = self.external_args()
        external_id = 'e16ee46f-5dff-57a5-a771-6e5147660779'
        observed = dict(id='account:' + external_id, parentId='account:' + args['parent'],
                        role='Explorer Agent · GPT-6.1 Sol')
        for change in (dict(role='Historical alias · GPT-6.1 Sol'), dict(parentId='account:' + self.parent)):
            with self.subTest(change=change):
                self.client.agents = [observed | change]
                self.assertEqual(self.api.register_external(**args),
                                 dict(delivered=False, reason='habblaud_binding_mismatch'))
                self.assertEqual(self.client.agents, [observed | change])
        self.assertEqual(self.client.events, [])

    def test_external_registration_is_independent_of_host_and_namespaces_actual_route(self):
        client, module = fixture_transport(provider='second-host')
        args = self.external_args()
        account = 'second-account'
        main = dict(id=account + ':' + args['parent'], kind='main', account=account, provider='second-host')
        client.agents.append(main)
        observed, = module.Client(args['endpoint']).snapshot(account)['agents']
        self.assertEqual((observed['kind'], observed['account'], observed['provider']),
                         ('main', account, 'second-host'))
        args.update(module=module, account=account, parent=observed['id'].split(':', 1)[1])
        for route in ('openai-api', 'second-api'):
            self.assertEqual(self.api.register_external(**(args | dict(execution_route=route))),
                             dict(delivered=True))
        children = client.agents[1:]
        self.assertEqual({agent['id'] for agent in children}, {
            'second-account:e16ee46f-5dff-57a5-a771-6e5147660779',
            'second-account:5e9ba79b-2747-5a01-a931-c2947ccff669',
        })
        self.assertTrue(all(agent['parentId'] == main['id'] for agent in children))

    def test_external_registration_rejects_unsafe_inputs_without_delivery_or_receipt(self):
        args = self.external_args()
        unsafe = self.root / 'public'
        unsafe.mkdir()
        unsafe.chmod(0o755)
        for change in (dict(parent=args['parent'] + ':other'), dict(parent=object()),
                       dict(execution_route='openai-api:other'), dict(execution_route='../escape'),
                       dict(execution_route='x' * 257), dict(run_id='x' * 257), dict(run_id='line\nbreak'),
                       dict(run_id=' '), dict(model_display_name='x' * 161),
                       dict(model_display_name='visible\nhidden'), dict(checkout='x\x00y'),
                       dict(visibility=args['visibility'] | dict(source='private input')),
                       dict(state_directory=unsafe)):
            with self.subTest(change=change):
                self.assertEqual(self.api.register_external(**(args | change)),
                                 dict(delivered=False, reason='visibility_unavailable'))
        self.assertEqual(self.client.events, [])
        self.assertEqual(list(self.root.iterdir()), [unsafe])
        self.assertEqual(list(unsafe.iterdir()), [])

    def test_explicit_roles_and_custom_names_are_model_independent(self):
        cases = [
            (dict(version=1, role='Scout', specialty='Read'), 'Same model', 'Scout · Same model'),
            (dict(version=1, role='Reviewer', specialty='Review'), 'Same model', 'Reviewer · Same model'),
            (dict(version=1, role='Reviewer', specialty='Review'), 'Other model', 'Reviewer · Other model'),
            (self.metadata, 'Native model', 'Threat reviewer · 東京 · Native model'),
            (dict(version=1, role='Builder', specialty='Fix'), None, 'Builder · Model unknown'),
        ]
        for metadata, model, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.api.display_label(metadata, model), expected)
                self.assertEqual(self.api.stored_declaration(metadata), metadata)

    def test_invalid_declarations_reject_without_silent_label_replacement(self):
        for change in (dict(role='Worker'), dict(version=True), dict(version=1),
                       dict(custom_role_name=''), dict(custom_role_name='x\ny'),
                       dict(custom_role_name='x' * 4097), dict(specialty='\t'), dict(extra='field')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.api.declaration(self.metadata | change)

    def test_maximum_length_custom_name_reaches_transport_in_full(self):
        name = '界' * 4096
        model = '🌙' * 4096
        metadata = self.metadata | dict(custom_role_name=name, specialty='界' * 4096)
        try:
            label = self.api.display_label(metadata, model)
            result = self.publisher(binding=self.binding | dict(label=label), metadata=metadata)('Working')
        except ValueError as error:
            self.fail(f'Valid full label must fit the transport: {error}')
        self.assertTrue(result['delivered'])
        self.assertEqual(self.client.events[0]['agent_type'], name + ' · ' + model)

    def test_native_identity_and_full_custom_label_reach_first_event(self):
        result = self.publisher()('Working')
        self.assertTrue(result['delivered'])
        self.assertEqual(self.client.events[0], dict(hook_event_name='SubagentStart',
            session_id=self.parent, agent_id=self.child,
            agent_type='Threat reviewer · 東京 · Native model', cwd=str(self.root)))
        saved = json.loads((self.root / 'delivery.json').read_text())
        self.assertEqual(saved['visibility'], self.metadata)
        self.assertEqual(saved['binding']['agent_id'], self.child)

    def test_replay_and_continuation_do_not_create_another_character(self):
        self.publisher()('Working')
        self.publisher()('Reviewing')
        self.publisher()('accepted')
        self.publisher()('accepted')
        names = [event['hook_event_name'] for event in self.client.events]
        self.assertEqual(names.count('SubagentStart'), 1)
        self.assertEqual(names.count('SubagentStop'), 1)
        self.assertEqual({event['agent_id'] for event in self.client.events}, {self.child})

    def test_conflicting_first_binding_is_reported_without_relabel_or_duplicate(self):
        self.client.agents = [dict(id='account:' + self.child,
            parentId='account:' + self.parent, role='Worker', status='working')]
        result = self.publisher()('Working')
        self.assertFalse(result['delivered'])
        self.assertEqual(result['reason'], 'habblaud_binding_mismatch')
        self.assertEqual(self.client.events, [])

    def test_failed_delivery_freezes_labels_before_retry(self):
        self.client.unavailable = True
        self.assertFalse(self.publisher()('Working')['delivered'])
        self.client.unavailable = False
        changed = self.binding | dict(label='Different role · Native model')
        result = self.publisher(binding=changed)('Working')
        self.assertFalse(result['delivered'])
        self.assertEqual(result['reason'], 'visibility_binding_changed')
        self.assertEqual(self.client.events, [])
        self.assertTrue(self.publisher()('Working')['delivered'])

    def test_receiver_rejected_name_remains_explicit(self):
        metadata = self.metadata | dict(custom_role_name='Guardian reviewer')
        binding = self.binding | dict(label='Guardian reviewer · Native model')
        result = self.publisher(binding=binding, metadata=metadata)('Working')
        self.assertFalse(result['delivered'])
        self.assertEqual(result['reason'], 'unsupported_display_label')
        self.assertEqual(self.client.events, [])

    def test_unrelated_receipt_is_never_overwritten(self):
        path = self.root / 'delivery.json'
        frozen = dict(version=1, binding=self.binding, visibility=self.metadata,
                      account='account', delivered=True, started=True, stopped=False, phase='Working')
        for value in (dict(unrelated='keep'), frozen | dict(unrelated='keep'), frozen | dict(started=1)):
            with self.subTest(value=value):
                raw = json.dumps(value)
                path.write_text(raw)
                path.chmod(0o600)
                self.assertFalse(self.publisher()('Working')['delivered'])
                self.assertEqual(path.read_text(), raw)
        self.assertEqual(self.client.events, [])

    def test_receiver_reset_restores_active_binding_with_same_identity(self):
        self.assertTrue(self.publisher()('Working')['delivered'])
        self.client.agents.clear()
        self.assertTrue(self.publisher()('Continuing')['delivered'])
        starts = [event for event in self.client.events if event['hook_event_name'] == 'SubagentStart']
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0], starts[1])


class NativeVisibilityObservationTests(NativeVisibilityFixture, unittest.TestCase):
    def test_ordinary_native_observation_needs_no_enrollment_or_database(self):
        self.assertTrue(hasattr(codex, 'observe_worker_visibility'), 'Native visibility observer is missing')
        value = self.observe()
        self.assertEqual(value['worker_id'], self.child)
        self.assertEqual(value['parent_session_id'], self.parent)
        self.assertEqual(value['configured_model'], 'Native model')
        self.assertIsNone(value['execution_model'])
        self.assertEqual(value['agent_path'], '/root/review_original')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_native_observation_uses_the_catalog_display_name(self):
        self.child_thread['model'] = 'gpt-6-astra'
        self.model_pages = {
            None: dict(data=[], nextCursor='page-two'),
            'page-two': dict(data=[dict(model='gpt-6-astra', displayName='GPT-6-Astra')], nextCursor=None),
        }
        value = self.observe()
        self.assertEqual(value['configured_model'], 'gpt-6-astra')
        self.assertEqual(value['model_display_name'], 'GPT-6-Astra')

    def test_nested_native_observation_preserves_immediate_parent(self):
        root = str(uuid.uuid4())
        self.ancestors[root] = self.parent_thread | dict(id=root)
        self.parent_thread.update(parentThreadId=root,
            source={'subAgent': {'thread_spawn': {'parent_thread_id': root,
                                                  'agent_path': '/root/coordinator'}}})
        self.child_thread['source']['subAgent']['thread_spawn']['agent_path'] = '/root/coordinator/review_original'
        value = self.observe()
        self.assertEqual(value['parent_session_id'], self.parent)
        self.assertEqual(value['task_name'], 'review_original')
        self.assertEqual(value['client_surface'], 'codex-cli')
        for changes in (dict(originator='unqualified'), dict(cwd='/other'),
                        dict(source={'subAgent': {'thread_spawn': {'parent_thread_id': root}}})):
            original = copy.deepcopy(self.ancestors[root])
            self.ancestors[root].update(changes)
            with self.subTest(changes=changes), self.assertRaises(StartupError):
                self.observe()
            self.ancestors[root] = original

    def test_native_display_accepts_parent_worktree_and_original_child_checkout(self):
        parent_checkout = self.root.parent / 'parent-worktree'
        parent_checkout.mkdir()
        self.parent_thread['cwd'] = str(parent_checkout)
        with patch.object(codex, '_native_reader', self.reader):
            for checkout in (parent_checkout, self.root):
                with self.subTest(checkout=checkout):
                    value = codex.observe_worker_visibility(checkout, self.parent, self.child)
                    self.assertEqual(value['checkout'], str(checkout))
                    self.assertEqual(value['parent_checkout'], str(parent_checkout))
                    self.assertEqual(value['worker_checkout'], str(self.root))
                    self.assertEqual(value['task_name'], 'review_original')
            with self.reader({'initialize', 'thread/read', 'model/list'}) as (version, request):
                with self.assertRaises(codex.ObservationError):
                    codex._native_metadata(SimpleNamespace(repository_root=parent_checkout), request,
                                           self.child, version)

    def test_nested_native_display_validates_distinct_ancestor_worktrees(self):
        parent_checkout = self.root.parent / 'parent-worktree'
        parent_checkout.mkdir()
        ancestor_checkout = self.root.parent / 'ancestor-worktree'
        ancestor_checkout.mkdir()
        root = str(uuid.uuid4())
        self.ancestors[root] = self.parent_thread | dict(id=root, cwd=str(ancestor_checkout))
        self.parent_thread.update(cwd=str(parent_checkout), parentThreadId=root,
            source={'subAgent': {'thread_spawn': {'parent_thread_id': root}}})
        self.child_thread['source']['subAgent']['thread_spawn']['agent_path'] = '/root/nested/review_original'
        with patch.object(codex, '_native_reader', self.reader):
            value = codex.observe_worker_visibility(self.root, root, self.child, resolve_hook_parent=True)
            self.assertEqual(value['parent_session_id'], self.parent)
            self.assertEqual(value['hook_session_id'], root)
            self.assertEqual(value['parent_checkout'], str(parent_checkout))
            self.assertEqual(value['worker_checkout'], str(self.root))
            self.assertEqual(value['agent_path'], '/root/nested/review_original')
            with self.assertRaises(StartupError):
                codex.observe_worker_visibility(ancestor_checkout, root, self.child, resolve_hook_parent=True)

    def test_native_display_rejects_unrelated_checkout_and_mismatched_parents(self):
        unrelated = self.root.parent / 'unrelated'
        unrelated.mkdir()
        with patch.object(codex, '_native_reader', self.reader):
            with self.assertRaises(StartupError):
                codex.observe_worker_visibility(unrelated, self.parent, self.child)
            for change in (dict(parentThreadId=str(uuid.uuid4())),
                           dict(source={'subAgent': {'thread_spawn': {'parent_thread_id': str(uuid.uuid4())}}})):
                original = copy.deepcopy(self.child_thread)
                self.child_thread.update(change)
                with self.subTest(change=change), self.assertRaises(StartupError):
                    codex.observe_worker_visibility(self.root, self.parent, self.child)
                self.child_thread = original

    def test_native_display_rejects_noncanonical_native_metadata(self):
        alias = self.root.parent / 'checkout-alias'
        alias.symlink_to(self.root, target_is_directory=True)
        for thread in (self.parent_thread, self.child_thread):
            for change in (dict(id=str(uuid.uuid4())), dict(cwd='checkout'), dict(cwd=str(alias)),
                           dict(cwd=str(self.root) + '/'), dict(cliVersion=None)):
                original = copy.deepcopy(thread)
                thread.update(change)
                with self.subTest(thread=original['id'], change=change), self.assertRaises(StartupError):
                    self.observe()
                thread.clear()
                thread.update(original)

    def test_nested_native_observation_rejects_cycle_and_mismatched_parent(self):
        for ancestor in (self.parent, self.child):
            self.parent_thread.update(parentThreadId=ancestor,
                source={'subAgent': {'thread_spawn': {'parent_thread_id': ancestor}}})
            with self.subTest(ancestor=ancestor), self.assertRaises(StartupError):
                self.observe()

    def test_hook_root_resolves_verified_immediate_parent_only(self):
        root = str(uuid.uuid4())
        self.ancestors[root] = self.parent_thread | dict(id=root)
        self.parent_thread.update(parentThreadId=root,
            source={'subAgent': {'thread_spawn': {'parent_thread_id': root}}})
        with patch.object(codex, '_native_reader', self.reader):
            value = codex.observe_worker_visibility(self.root, root, self.child, resolve_hook_parent=True)
            self.assertEqual(value['parent_session_id'], self.parent)
            self.assertEqual(value['hook_session_id'], root)
            with self.assertRaises(StartupError):
                codex.observe_worker_visibility(self.root, root, self.child)
            with self.assertRaises(StartupError):
                codex.observe_worker_visibility(self.root, str(uuid.uuid4()), self.child, resolve_hook_parent=True)

    def test_hook_root_uses_immediate_parent_context_and_event_binding(self):
        helper = self.native_helper()
        root = str(uuid.uuid4())
        path = helper.hook_context_path(self.task_context, self.parent, 'review_original')
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, self.native_request())
        event = dict(hook_event_name='SubagentStart', session_id=root,
                     agent_id=self.child, cwd=str(self.root), agent_type='default')
        observed = self.observe() | dict(hook_session_id=root)
        observed.pop('parent_checkout', None)
        observed.pop('worker_checkout', None)
        with patch.object(codex, 'observe_worker_visibility', return_value=observed):
            actual = helper.enrich_start(event, self.task_context, self.root, Path(sys.executable).resolve())
        self.assertEqual(actual['session_id'], self.parent)
        self.assertEqual(actual['vcw_display_label'], 'Audit lead · Native model')
        self.assertEqual(event['session_id'], root)
        self.assertEqual(helper.read_context(path)['observed']['checkout'], str(self.root))

    def test_hook_child_checkout_uses_parent_worktree_declaration_and_preserves_event(self):
        from runtime import habblaud
        helper = self.native_helper()
        parent_checkout = self.root.parent / 'parent-worktree'
        parent_checkout.mkdir()
        self.parent_thread['cwd'] = str(parent_checkout)
        request = self.native_request() | dict(checkout=str(parent_checkout),
            visibility=dict(version=2, role='test-engineer', specialty='Verify the change'))
        path = helper.hook_context_path(self.task_context, self.parent, 'review_original')
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, request)
        event = dict(hook_event_name='SubagentStart', session_id=self.parent,
                     agent_id=self.child, cwd=str(self.root), agent_type='default', extra='preserve')
        original = copy.deepcopy(event)
        with patch.object(codex, '_native_reader', self.reader):
            first = helper.enrich_start(event, self.task_context, None, Path(sys.executable).resolve())
            for checkout in (self.root, parent_checkout):
                with self.subTest(checkout=checkout):
                    replay = helper.enrich_start(event, self.task_context, checkout, Path(sys.executable).resolve())
                    self.assertEqual(replay, first)
        self.assertEqual(first, original | dict(agent_type='Test Engineer · Native model',
            agent_role='Test Engineer · Native model', vcw_display_label='Test Engineer · Native model'))
        self.assertEqual(event, original)
        saved = helper.read_context(path)
        self.assertEqual(saved['observed']['checkout'], str(parent_checkout))
        self.assertEqual(saved['observed']['worker_checkout'], str(self.root))
        self.assertEqual(habblaud._saved_native_binding(saved)['worker_id'], self.child)

    def test_hook_rejects_explicit_unrelated_checkout_scope(self):
        helper = self.native_helper()
        unrelated = self.root.parent / 'unrelated'
        unrelated.mkdir()
        path = helper.hook_context_path(self.task_context, self.parent, 'review_original')
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, self.native_request())
        before = path.read_bytes()
        event = dict(hook_event_name='SubagentStart', session_id=self.parent,
                     agent_id=self.child, cwd=str(self.root), agent_type='default')
        with patch.object(codex, '_native_reader', self.reader):
            self.assertEqual(helper.enrich_start(event, self.task_context, unrelated,
                                                Path(sys.executable).resolve()), event)
        self.assertEqual(path.read_bytes(), before)

    def test_hook_rejects_declaration_from_unrelated_third_checkout(self):
        helper = self.native_helper()
        unrelated = self.root.parent / 'unrelated'
        unrelated.mkdir()
        path = helper.hook_context_path(self.task_context, self.parent, 'review_original')
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, self.native_request() | dict(checkout=str(unrelated)))
        before = path.read_bytes()
        event = dict(hook_event_name='SubagentStart', session_id=self.parent,
                     agent_id=self.child, cwd=str(self.root), agent_type='default')
        with patch.object(codex, '_native_reader', self.reader):
            self.assertEqual(helper.enrich_start(event, self.task_context, None,
                                                Path(sys.executable).resolve()), event)
        self.assertEqual(path.read_bytes(), before)

    def test_catalog_failure_or_ambiguity_preserves_native_identity(self):
        for page in (dict(data=None), dict(data=[dict(model='Native model', displayName='First'),
                                              dict(model='Native model', displayName='Second')])):
            with self.subTest(page=page):
                self.model_pages = {None: page}
                value = self.observe()
                self.assertEqual(value['worker_id'], self.child)
                self.assertEqual(value['configured_model'], 'Native model')
                self.assertIsNone(value['model_display_name'])
        request = unittest.mock.Mock(side_effect=OSError('catalog unavailable'))
        self.assertIsNone(codex._visibility_model_name(request, 'Native model'))
        request = unittest.mock.Mock(return_value=dict(data=[], nextCursor='same'))
        self.assertIsNone(codex._visibility_model_name(request, 'Native model'))
        self.assertEqual(request.call_count, 4)

    def test_native_observation_rejects_unrelated_or_uncertain_lineage(self):
        self.assertTrue(hasattr(codex, 'observe_worker_visibility'), 'Native visibility observer is missing')
        for field, value in [('parentThreadId', str(uuid.uuid4())), ('cwd', '/other'), ('source', 'exec')]:
            original = copy.deepcopy(self.child_thread)
            self.child_thread[field] = value
            with self.subTest(field=field), self.assertRaises(StartupError):
                self.observe()
            self.child_thread = original

    def test_original_start_event_gets_only_bound_private_label(self):
        helper = self.native_helper()
        request = self.native_request()
        path = helper.hook_context_path(self.task_context, self.parent, 'review_original')
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, request)
        executable = Path(sys.executable).resolve()
        event = dict(hook_event_name='SubagentStart', session_id=self.parent,
                     agent_id=self.child, cwd=str(self.root), agent_type='default', extra='preserve')
        with patch.object(codex, 'observe_worker_visibility', return_value=self.observe()):
            actual = helper.enrich_start(event | dict(agent_role='Default'), self.task_context, self.root, executable)
            self.assertEqual(actual, event | dict(agent_role='Audit lead · Native model',
                                                   agent_type='Audit lead · Native model',
                                                   vcw_display_label='Audit lead · Native model'))
            self.assertEqual(event['agent_type'], 'default')
            self.assertEqual(helper.enrich_start(event | dict(agent_role='Native reviewer'),
                                                 self.task_context, self.root, executable)['agent_role'], 'Native reviewer')
            other = event | dict(agent_id=str(uuid.uuid4()))
            self.assertEqual(helper.enrich_start(other, self.task_context, self.root, executable), other)
            unrelated = event | dict(cwd=str(self.task_context))
            self.assertEqual(helper.enrich_start(unrelated, self.task_context, self.root, executable), unrelated)

    def test_first_declared_badge_uses_catalog_and_preserves_native_role(self):
        helper = self.native_helper()
        request = self.native_request()
        path = helper.hook_context_path(self.task_context, self.parent, request['task_name'])
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, request)
        observed = self.observe() | dict(model_display_name='Catalog Name', native_role='Native reviewer')
        event = dict(hook_event_name='SubagentStart', session_id=self.parent, agent_id=self.child,
                     cwd=str(self.root), agent_type='default')
        label = 'Audit lead · Catalog Name'
        with patch.object(codex, 'observe_worker_visibility', return_value=observed):
            for role in ('Native reviewer', None, '', ' Default ', {}, 42):
                with self.subTest(role=role):
                    enriched = helper.enrich_start(event | dict(agent_role=role), self.task_context,
                                                   None, Path(sys.executable).resolve())
                    self.assertEqual(enriched['agent_type'], label)
                    self.assertEqual(enriched['vcw_display_label'], label)
                    self.assertEqual(enriched['agent_role'], role if role == 'Native reviewer' else label)
        self.assertEqual(helper.read_context(path)['observed']['forwarded_label'], label)

    def test_historical_forwarded_label_is_not_rewritten_by_catalog_lookup(self):
        from runtime import habblaud, worker_visibility
        helper = self.native_helper()
        request = self.native_request()
        path = helper.hook_context_path(self.task_context, self.parent, request['task_name'])
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, request)
        observed = self.observe() | dict(model_display_name='Catalog Name',
                                        forwarded_label='Audit lead · Legacy name')
        worker_visibility.save(path, helper.read_context(path) | dict(observed=observed))
        event = dict(hook_event_name='SubagentStart', session_id=self.parent, agent_id=self.child,
                     cwd=str(self.root), agent_type='default')
        with patch.object(codex, 'observe_worker_visibility', return_value=self.observe()):
            enriched = helper.enrich_start(event, self.task_context, self.root, Path(sys.executable).resolve())
        self.assertEqual(enriched['agent_type'], 'Audit lead · Legacy name')
        self.assertNotIn('vcw_display_label', enriched)
        self.assertEqual(helper.read_context(path)['observed'], observed)

    def test_missing_catalog_formats_actual_model_and_invalid_saved_label_passes_through(self):
        from runtime import habblaud, worker_visibility
        helper = self.native_helper()
        request = self.native_request()
        path = helper.hook_context_path(self.task_context, self.parent, request['task_name'])
        path.parent.mkdir(mode=0o700)
        helper.prepare_native(path, request)
        observed = self.observe() | dict(configured_model='gpt-6-astra', model_display_name=None)
        event = dict(hook_event_name='SubagentStart', session_id=self.parent, agent_id=self.child,
                     cwd=str(self.root), agent_type='default')
        with patch.object(codex, 'observe_worker_visibility', return_value=observed):
            enriched = helper.enrich_start(event, self.task_context, self.root, Path(sys.executable).resolve())
            self.assertEqual(enriched['vcw_display_label'], 'Audit lead · GPT-6 Astra')
            saved = helper.read_context(path)
            worker_visibility.save(path, saved | dict(observed=observed | dict(forwarded_label='')))
            self.assertEqual(helper.enrich_start(event, self.task_context, self.root,
                                                Path(sys.executable).resolve()), event)

    def test_legacy_observation_keeps_raw_model_for_receipt_continuation(self):
        from runtime import habblaud, worker_visibility
        helper = self.native_helper()
        client, transport = fixture_transport()
        request = self.native_request()
        path = self.task_context / 'legacy.json'
        helper.prepare_native(path, request)
        legacy = self.observe() | dict(configured_model='gpt-6-astra')
        legacy.pop('model_display_name', None)
        worker_visibility.save(path, helper.read_context(path) | dict(observed=legacy))
        binding = dict(parent=self.parent, agent_id=self.child, cwd=str(self.root),
                       label='Audit lead · gpt-6-astra')
        receipt = path.with_name(path.name + '.delivery.json')
        publisher = habblaud.publisher(transport, 'http://127.0.0.1:1',
                                                'account', binding, request['visibility'], receipt)
        self.assertTrue(publisher('Working')['delivered'])
        with patch.object(codex, 'observe_worker_visibility', return_value=self.observe()), \
                patch.object(habblaud, 'transport', return_value=transport):
            result = helper.publish_native(path, self.child, 'Continuing', self.root,
                                           'http://127.0.0.1:1', 'account')
        self.assertTrue(result['delivered'])
        self.assertEqual(result['binding']['label'], binding['label'])

    def test_start_wrapper_preserves_original_bytes_and_exit_status(self):
        helper = self.native_helper()
        raw = b'not JSON\n' + b'x' * (130 * 1024)
        result = subprocess.run([sys.executable, '-I', '-B', helper.__file__, 'forward-start',
            '--context-directory', str(self.task_context), '--checkout', str(self.root),
            '--codex-executable', str(Path(sys.executable).resolve()), '--',
            sys.executable, '-c',
            'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(7)'],
            input=raw, capture_output=True)
        self.assertEqual(result.stdout, raw)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(result.returncode, 7)

    def test_native_binding_preserves_context_and_child_across_continuation(self):
        from runtime import habblaud, worker_visibility
        helper = self.native_helper()
        client, transport = fixture_transport()
        path = self.task_context / 'assignment.json'
        request = dict(parent_session_id=self.parent, checkout=str(self.root), task_name='review_original',
                       visibility=dict(version=2, role='code-reviewer', specialty='Read only', custom_role_name='Audit lead'))
        helper.prepare_native(path, request)
        self.assertEqual(client.events, [])
        with self.assertRaises(FileExistsError):
            helper.prepare_native(path, request | dict(visibility=request['visibility'] | dict(role='software-engineer')))
        with patch.object(codex, '_native_reader', self.reader), patch.object(habblaud, 'transport', return_value=transport):
            first = helper.publish_native(path, self.child, 'Reviewing', self.root, 'http://127.0.0.1:1', 'account')
            second = helper.publish_native(path, self.child, 'Continuing', self.root, 'http://127.0.0.1:1', 'account')
            replaced = helper.publish_native(path, str(uuid.uuid4()), 'Continuing', self.root, 'http://127.0.0.1:1', 'account')
        self.assertTrue(first['delivered'])
        self.assertTrue(second['delivered'])
        self.assertFalse(replaced['delivered'])
        self.assertEqual(replaced['reason'], 'native_visibility_identity_changed')
        self.assertEqual([e['hook_event_name'] for e in client.events].count('SubagentStart'), 1)
        self.assertEqual({e['agent_id'] for e in client.events}, {self.child})
        self.assertEqual({e['agent_type'] for e in client.events}, {'Audit lead · Native model'})
        starts = [e for e in client.events if e['hook_event_name'] == 'SubagentStart']
        self.assertEqual(starts[0]['vcw_display_label'], 'Audit lead · Native model')
        self.assertTrue(all('vcw_display_label' not in e for e in client.events if e not in starts))
        saved = worker_visibility.read(path)
        self.assertEqual(saved['task_name'], 'review_original')
        self.assertEqual(saved['visibility'], request['visibility'])
        self.assertFalse(any(p.suffix in {'.db', '.sqlite', '.sqlite3'} for p in self.task_context.iterdir()))


if __name__ == '__main__':
    unittest.main()
