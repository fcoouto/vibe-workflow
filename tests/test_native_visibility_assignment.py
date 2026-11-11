import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from runtime import worker_visibility
from runtime.adapters import codex
from tests.fixture_worker_visibility import NativeVisibilityFixture, fixture_transport


class NativeVisibilityAssignmentTests(NativeVisibilityFixture, unittest.TestCase):
    def test_no_native_character_when_child_lineage_is_unobserved(self):
        from runtime import habblaud, worker_visibility
        helper = self.native_helper()
        client, transport = fixture_transport()
        path = self.task_context / 'assignment.json'
        helper.prepare_native(path, dict(parent_session_id=self.parent, checkout=str(self.root),
            task_name='review_original', visibility=dict(version=2, role='code-reviewer', specialty='Read only')))
        self.child_thread['parentThreadId'] = str(uuid.uuid4())
        with patch.object(codex, '_native_reader', self.reader), patch.object(habblaud, 'transport', return_value=transport):
            result = helper.publish_native(path, self.child, 'Reviewing', self.root, 'http://127.0.0.1:1', 'account')
        self.assertFalse(result['delivered'])
        self.assertEqual(result['reason'], 'native_visibility_identity_unobserved')
        self.assertEqual(client.events, [])

    def test_assignment_preparation_returns_bound_brief_and_first_badge(self):
        helper = self.native_helper()
        self.assertTrue(hasattr(helper, 'prepare_assignment'), 'Assignment preparation must capture its role')
        request = self.native_request() | dict(message='Review the changed permission check.\nDo not edit.')
        original = copy.deepcopy(request)
        result = helper.prepare_assignment(request, context_directory=self.task_context)
        self.assertTrue(result['prepared'])
        self.assertEqual(result['task_name'], 'review_original')
        self.assertEqual(result['message'], result['prompt_marker'] + '\n' + request['message'])
        self.assertEqual(request, original)
        context = helper.read_context(Path(result['context']))
        self.assertEqual(context['visibility'], request['visibility'])
        self.assertNotIn('message', context)
        event = dict(hook_event_name='SubagentStart', session_id=self.parent,
                     agent_id=self.child, cwd=str(self.root), agent_type='default')
        with patch.object(codex, 'observe_worker_visibility', return_value=self.observe()):
            actual = helper.enrich_start(event, self.task_context, self.root, Path(sys.executable).resolve())
        self.assertEqual(actual['vcw_display_label'], 'Audit lead · Native model')

    def test_assignment_display_failure_returns_original_spawn_inputs(self):
        helper = self.native_helper()
        self.assertTrue(hasattr(helper, 'prepare_assignment'), 'Assignment preparation must remain nonblocking')
        request = self.native_request() | dict(message='Review this file without editing it.')
        for metadata in (None, {}, dict(version=1, role='Guess', specialty='Read only')):
            with self.subTest(metadata=metadata):
                result = helper.prepare_assignment(request | dict(visibility=metadata), context_directory=self.task_context)
                self.assertFalse(result['prepared'])
                self.assertEqual(result['task_name'], request['task_name'])
                self.assertEqual(result['message'], request['message'])
        with patch.object(worker_visibility, 'prepare_native', side_effect=OSError('private storage unavailable')):
            result = helper.prepare_assignment(request, context_directory=self.task_context)
        self.assertFalse(result['prepared'])
        self.assertEqual(result['message'], request['message'])
        self.assertNotIn('permissionDecision', result)

    def test_assignment_preparation_preserves_existing_bound_context(self):
        helper = self.native_helper()
        self.assertTrue(hasattr(helper, 'prepare_assignment'))
        request = self.native_request() | dict(message='Review the original change.')
        first = helper.prepare_assignment(request, context_directory=self.task_context)
        path = Path(first['context'])
        original = path.read_bytes()
        second = helper.prepare_assignment(request | dict(visibility=dict(version=1, role='Builder', specialty='Edit')),
                                           context_directory=self.task_context)
        self.assertFalse(second['prepared'])
        self.assertEqual(second['message'], request['message'])
        self.assertEqual(path.read_bytes(), original)
        replay = helper.prepare_assignment(request, context_directory=self.task_context)
        self.assertTrue(replay['prepared'])
        self.assertEqual(replay['message'], first['message'])
        self.assertEqual(path.read_bytes(), original)
        malformed = helper.prepare_assignment(request | dict(unexpected=None), context_directory=self.task_context)
        self.assertFalse(malformed['prepared'])
        self.assertEqual(path.read_bytes(), original)

    def test_assignment_retry_reuses_saved_role_when_visibility_is_omitted(self):
        helper = self.native_helper()
        request = self.native_request() | dict(message='Review the original change.')
        first = helper.prepare_assignment(request, context_directory=self.task_context)
        path = Path(first['context'])
        original = path.read_bytes()
        retry = {key: value for key, value in request.items() if key != 'visibility'}
        for message in (request['message'], 'Review the clarified assignment.'):
            with self.subTest(message=message):
                result = helper.prepare_assignment(retry | dict(message=message), context_directory=self.task_context)
                self.assertTrue(result['prepared'])
                self.assertEqual(result['visibility'], request['visibility'])
                self.assertEqual(result['message'], first['prompt_marker'] + '\n' + message)
                self.assertEqual(path.read_bytes(), original)

    def test_assignment_retry_does_not_duplicate_exact_saved_marker(self):
        helper = self.native_helper()
        request = self.native_request() | dict(message='Review this change.\nKeep the second line.')
        first = helper.prepare_assignment(request, context_directory=self.task_context)
        for omit_role in (False, True):
            retry = {key: value for key, value in request.items() if not (omit_role and key == 'visibility')}
            with self.subTest(omit_role=omit_role):
                result = helper.prepare_assignment(retry | dict(message=first['message']),
                                                   context_directory=self.task_context)
                self.assertTrue(result['prepared'])
                self.assertEqual(result['message'], first['message'])
        foreign = first['prompt_marker'] + ' other\nKeep this text.'
        result = helper.prepare_assignment(request | dict(message=foreign), context_directory=self.task_context)
        self.assertEqual(result['message'], first['prompt_marker'] + '\n' + foreign)

    def test_saved_assignment_conflicts_keep_original_inputs_and_context(self):
        helper = self.native_helper()
        request = self.native_request() | dict(message='Review this change.')
        first = helper.prepare_assignment(request, context_directory=self.task_context)
        path = Path(first['context'])
        original = path.read_bytes()
        unrelated = self.root.parent / 'other-checkout'
        unrelated.mkdir()
        retry = {key: value for key, value in request.items() if key != 'visibility'}
        for change in (dict(parent_session_id=str(uuid.uuid4())), dict(checkout=str(unrelated)),
                       dict(task_name='other_assignment'), dict(visibility=None), dict(visibility={}),
                       dict(visibility=request['visibility'] | dict(role='Tester'))):
            incoming = retry | change
            with self.subTest(change=change):
                result = helper.prepare_assignment(incoming, context=path)
                self.assertFalse(result['prepared'])
                self.assertEqual(result['task_name'], incoming['task_name'])
                self.assertEqual(result['message'], incoming['message'])
                self.assertEqual(path.read_bytes(), original)

    def test_assignment_cli_keeps_missing_role_nonblocking(self):
        helper = self.native_helper()
        request = self.native_request() | dict(message='Inspect one file.')
        request.pop('visibility')
        result = subprocess.run([sys.executable, '-I', '-B', helper.__file__, 'prepare',
            '--context-directory', str(self.task_context)], input=json.dumps(request),
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertFalse(output['prepared'])
        self.assertEqual(output['task_name'], request['task_name'])
        self.assertEqual(output['message'], request['message'])
        self.assertEqual(list(self.task_context.iterdir()), [])

    def test_reused_child_binds_once_and_keeps_followup_message(self):
        helper = self.native_helper()
        request = self.native_request() | dict(worker_id=self.child, message='Review the follow-up change.')
        with patch.object(codex, '_native_reader', self.reader):
            first = helper.prepare_assignment(request, context_directory=self.task_context)
            self.assertTrue(first['prepared'])
            self.assertEqual(first['message'], request['message'])
            path = Path(first['context'])
            original = path.read_bytes()
            self.assertEqual(helper.read_context(path)['observed']['worker_id'], self.child)
            followup = {k: v for k, v in request.items() if k != 'visibility'} | dict(message='Check one more detail.')
            second = helper.prepare_assignment(followup, context_directory=self.task_context)
            self.assertTrue(second['prepared'])
            self.assertEqual(second['context'], first['context'])
            self.assertEqual(second['message'], followup['message'])
            self.assertEqual(path.read_bytes(), original)
            wrong = helper.prepare_assignment(followup | dict(worker_id=str(uuid.uuid4())),
                                              context_directory=self.task_context)
            self.assertFalse(wrong['prepared'])
            self.assertEqual(wrong['message'], followup['message'])
            self.assertEqual(path.read_bytes(), original)

    def test_restore_previously_observed_context_sends_badge_only(self):
        helper = self.native_helper()
        path = self.task_context / 'legacy.json'
        helper.prepare_native(path, self.native_request())
        from runtime import habblaud, worker_visibility
        saved = helper.read_context(path)
        worker_visibility.save(path, saved | dict(observed=self.observe() | dict(forwarded_label='Audit lead · Native model')))
        original = path.read_bytes()
        from runtime import habblaud
        client, transport = fixture_transport()
        with patch.object(habblaud, 'transport', return_value=transport):
            self.assertEqual(habblaud.restore_badge(path, self.root, 'http://127.0.0.1:1', 'account'),
                             dict(delivered=True))
        self.assertEqual(client.events, [dict(hook_event_name='SubagentBadge', session_id=self.parent,
                                              agent_id=self.child, vcw_display_label='Audit lead · Native model')])
        self.assertEqual(path.read_bytes(), original)

    def restore(self, path):
        from runtime import habblaud
        client, transport = fixture_transport()
        with patch.object(habblaud, 'transport', return_value=transport):
            return habblaud.restore_badge(path, self.root, 'http://127.0.0.1:1', 'account'), client.events

    def test_restore_shows_the_roster_label_without_a_custom_name(self):
        path = self.task_context / 'roster.json'
        request = self.native_request()
        request['visibility'] = {key: value for key, value in request['visibility'].items() if key != 'custom_role_name'}
        worker_visibility.prepare_native(path, request)
        saved = worker_visibility.read_context(path)
        worker_visibility.save(path, saved | dict(observed=self.observe() | dict(
            forwarded_label='Code Reviewer · Native model')))
        original = path.read_bytes()
        result, events = self.restore(path)
        self.assertEqual(result, dict(delivered=True))
        self.assertEqual(events[0]['vcw_display_label'], 'Code Reviewer · Native model')
        self.assertEqual(path.read_bytes(), original)

    def test_restore_shows_a_stored_version_1_role_as_written(self):
        path = self.task_context / 'stored.json'
        worker_visibility.prepare_native(path, self.native_request())
        saved = worker_visibility.read_context(path)
        stored = dict(version=1, role='Reviewer', specialty='Read only')  # written before the roster
        worker_visibility.save(path, saved | dict(visibility=stored, observed=self.observe() | dict(
            forwarded_label='Reviewer · Native model')))
        result, events = self.restore(path)
        self.assertEqual(result, dict(delivered=True))
        self.assertEqual(events[0]['vcw_display_label'], 'Reviewer · Native model')

    def test_assignment_retry_reuses_a_stored_version_1_context(self):
        helper = self.native_helper()
        request = self.native_request() | dict(message='Review the original change.')
        first = helper.prepare_assignment(request, context_directory=self.task_context)
        path = Path(first['context'])
        stored = dict(version=1, role='Reviewer', specialty='Read only', custom_role_name='Audit lead')
        worker_visibility.save(path, worker_visibility.read_context(path) | dict(visibility=stored))
        original = path.read_bytes()
        retry = {key: value for key, value in request.items() if key != 'visibility'}
        result = helper.prepare_assignment(retry, context_directory=self.task_context)
        self.assertTrue(result['prepared'], result)
        self.assertEqual(result['visibility'], stored)
        self.assertEqual(result['message'], first['prompt_marker'] + '\n' + request['message'])
        self.assertEqual(path.read_bytes(), original)
        for refused in (retry | dict(visibility=stored),  # a supplied version 1 declaration is new input
                        retry | dict(visibility=request['visibility'])):  # differs from the stored context
            with self.subTest(refused=refused['visibility']):
                answer = helper.prepare_assignment(refused, context_directory=self.task_context)
                self.assertFalse(answer['prepared'])
                self.assertEqual(answer['message'], request['message'])
                self.assertEqual(path.read_bytes(), original)
        unknown = helper.prepare_assignment(retry | dict(task_name='review_unknown'), context_directory=self.task_context)
        self.assertFalse(unknown['prepared'])

    def test_restore_keeps_frozen_legacy_model_name(self):
        from runtime import habblaud
        path = self.task_context / 'legacy.json'
        worker_visibility.prepare_native(path, self.native_request())
        saved = worker_visibility.read_context(path)
        label = 'Audit lead · ' + 'Legacy model name ' * 12 + 'edition'
        self.assertGreater(len(label.encode('utf-16-le')), 400)
        observed = self.observe() | dict(model_display_name='Catalog Name', forwarded_label=label)
        worker_visibility.save(path, saved | dict(observed=observed))
        client, transport = fixture_transport()
        with patch.object(habblaud, 'transport', return_value=transport):
            result = habblaud.restore_badge(path, self.root, 'http://127.0.0.1:1', 'account')
        self.assertTrue(result['delivered'])
        self.assertEqual(client.events[0]['vcw_display_label'], label)

    def test_managed_preparation_reuses_role_without_changing_frozen_prompt(self):
        helper = self.native_helper()
        self.assertTrue(hasattr(helper, 'prepare_managed'), 'Managed assignment should register its existing role')
        request = self.native_request()
        body = dict(binding=dict(task_name=request['task_name']), request=dict(visibility=request['visibility']))
        for reviewer in (False, True):
            with self.subTest(reviewer=reviewer):
                directory = self.task_context / str(reviewer)
                directory.mkdir(mode=0o700)
                digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                report = dict(ok=True, launch_admitted=True, task_name=request['task_name'],
                              assignment_digest=digest, visibility=request['visibility'])
                envelope = dict(assignment=body, assignment_digest=digest)
                if reviewer:
                    report.update(assignment_digest='a' * 64, ledger_assignment_digest=digest)
                    envelope = dict(ownership=body, ledger_assignment_digest=digest, assignment='Read only.')
                report['prompt'] = json.dumps(envelope)
                original = copy.deepcopy(report)
                actual = helper.prepare_managed(self.root, self.parent, report, context_directory=directory)
                self.assertEqual({key: actual[key] for key in original}, original)
                self.assertEqual(report, original)
                preparation = actual['visibility_preparation']
                self.assertTrue(preparation['prepared'])
                context = helper.read_context(Path(preparation['context']))
                self.assertEqual(context['ledger_assignment_digest'], digest)
                self.assertNotIn('prompt_marker', context)
                self.assertEqual(context['visibility'], request['visibility'])

    def test_managed_replay_or_missing_role_does_not_change_launch_result(self):
        helper = self.native_helper()
        self.assertTrue(hasattr(helper, 'prepare_managed'))
        report = dict(ok=True, launch_admitted=False, replayed=True, task_name='review_original',
                      prompt='Keep this immutable prompt.', assignment_digest='a' * 64)
        actual = helper.prepare_managed(self.root, self.parent, report, context_directory=self.task_context)
        self.assertEqual({key: actual[key] for key in report}, report)
        self.assertFalse(actual['visibility_preparation']['prepared'])
        self.assertEqual(list(self.task_context.iterdir()), [])

    def test_managed_continuation_binds_same_child_without_exposing_private_prompt(self):
        report = dict(ok=True, revision=7, token='one-use-token', worker_id=self.child,
                      task_name='review_original', state='prepared', send_admitted=False,
                      visibility=self.native_request()['visibility'])
        original = copy.deepcopy(report)
        prompt = 'Continue the original assignment.\nKeep this private prompt exact.'
        with patch.object(codex, '_native_reader', self.reader):
            actual = worker_visibility.prepare_managed(self.root, self.parent, report, prompt=prompt,
                                                       context_directory=self.task_context)
        self.assertEqual({key: actual[key] for key in original}, original)
        self.assertEqual(report, original)
        self.assertNotIn('prompt', actual)
        preparation = actual['visibility_preparation']
        self.assertTrue(preparation['prepared'])
        path = Path(preparation['context'])
        saved = worker_visibility.read_context(path)
        self.assertEqual(saved['observed']['worker_id'], self.child)
        self.assertEqual(saved['visibility'], report['visibility'])
        before = path.read_bytes()
        without_role = {key: value for key, value in report.items() if key != 'visibility'}
        replay = worker_visibility.prepare_managed(self.root, self.parent, without_role, prompt=prompt,
                                                   context_directory=self.task_context)
        self.assertTrue(replay['visibility_preparation']['prepared'])
        self.assertEqual(path.read_bytes(), before)

    def test_managed_continuation_missing_or_invalid_role_preserves_intent(self):
        report = dict(ok=True, revision=7, token='one-use-token', worker_id=self.child,
                      task_name='review_original', state='prepared', send_admitted=False)
        prompt = 'Exact admitted follow-up.\nKeep this prompt private.'
        for index, metadata in enumerate(({}, dict(visibility=None), dict(visibility={}),
                dict(visibility=dict(version=1, role='Guess', specialty='Read only')))):
            with self.subTest(metadata=metadata):
                directory = self.task_context / str(index)
                directory.mkdir(mode=0o700)
                incoming = report | metadata
                original = copy.deepcopy(incoming)
                actual = worker_visibility.prepare_managed(self.root, self.parent, incoming, prompt=prompt,
                                                           context_directory=directory)
                self.assertEqual({key: actual[key] for key in original}, original)
                self.assertEqual(incoming, original)
                self.assertNotIn('prompt', actual)
                self.assertFalse(actual['visibility_preparation']['prepared'])

    def test_managed_continuation_cli_prepares_once_after_operation_and_never_at_consume(self):
        import io
        import os
        from contextlib import redirect_stdout
        from runtime import delegation, workstreams  # Load forwarding aliases before patching their source.
        from scripts import workflow
        scope = SimpleNamespace(repository_root=self.root, runtime=object())
        prepare = worker_visibility.prepare_managed
        for action in ('prepare-continuation', 'consume-continuation'):
            for unavailable in (False, True):
                with self.subTest(action=action, unavailable=unavailable):
                    home = self.task_context / f'{action}-{unavailable}'
                    home.mkdir(mode=0o700)
                    prompt = 'Exact admitted follow-up.\nDo not rewrite this prompt.'
                    report = dict(ok=True, revision=7, token='one-use-token', worker_id=self.child,
                        task_name='review_original', send_admitted=action == 'consume-continuation',
                        visibility=self.native_request()['visibility'])
                    if action == 'consume-continuation':
                        report['prompt'] = prompt
                    request = dict(client='codex', session_id=self.parent, prompt=prompt, token='one-use-token')
                    order = []
                    def operation(*args):
                        order.append('operation')
                        return copy.deepcopy(report)
                    def presentation(*args, **kwargs):
                        order.append('display')
                        return prepare(*args, **kwargs)
                    output = io.StringIO()
                    with patch('sys.argv', ['workflow.py', 'delegation', 'workstreams', action, '--cwd', str(self.root)]), \
                            patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
                            patch.dict(os.environ, {'CODEX_HOME': str(home)}), \
                            patch('runtime.startup.acquire_project_entry', return_value=object()), \
                            patch('runtime.startup._entry_scope', return_value=scope), \
                            patch.object(delegation, action.replace('-', '_'), side_effect=operation), \
                            patch.object(worker_visibility, 'prepare_managed', side_effect=presentation), \
                            patch.object(worker_visibility, 'prepare_native',
                                         side_effect=OSError('unavailable') if unavailable else worker_visibility.prepare_native), \
                            patch.object(codex, '_native_reader', self.reader), \
                            patch.object(workflow, 'title_report', side_effect=lambda s, c, p, value: value), \
                            redirect_stdout(output):
                        code = workflow.control_main()
                    actual = json.loads(output.getvalue())
                    self.assertEqual(code, 0, actual)
                    self.assertEqual({key: actual[key] for key in report}, report)
                    if action == 'prepare-continuation':
                        self.assertEqual(order, ['operation', 'display'])
                        self.assertNotIn('prompt', actual)
                        self.assertEqual(actual['visibility_preparation']['prepared'], not unavailable)
                    else:
                        self.assertEqual(order, ['operation'])
                        self.assertEqual(actual, report)
                        self.assertEqual(list(home.iterdir()), [])

    def test_managed_cli_captures_both_routes_after_admission_and_keeps_display_failure_optional(self):
        import io
        import os
        from contextlib import redirect_stdout
        from scripts import workflow
        from runtime import delegation, evidence
        scope = SimpleNamespace(repository_root=self.root, runtime=object())
        metadata = self.native_request()['visibility']
        for review in (False, True):
            for unavailable in (False, True):
                with self.subTest(review=review, unavailable=unavailable):
                    task_name = f'review_{review}_{unavailable}'
                    report = dict(ok=True, task_name=task_name, prompt='Frozen native envelope',
                                  launch_admitted=True, assignment_digest='a' * 64, visibility=metadata)
                    if review:
                        report['ledger_assignment_digest'] = 'b' * 64
                    operation = (evidence, 'prepare_review') if review else (delegation, 'prepare_assignment')
                    argv = ['workflow.py', *(['evidence', 'review'] if review else ['delegation']),
                            'prepare', '--cwd', str(self.root)]
                    output = io.StringIO()
                    request = dict(client='codex', session_id=self.parent)
                    with patch('sys.argv', argv), \
                            patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), \
                            patch.dict(os.environ, {'CODEX_HOME': str(self.task_context)}), \
                            patch('runtime.startup.acquire_project_entry', return_value=object()), \
                            patch('runtime.startup._entry_scope', return_value=scope), \
                            patch.object(*operation, return_value=report), \
                            patch.object(workflow, 'title_report', side_effect=lambda s, c, p, value: value), \
                            patch.object(worker_visibility, 'prepare_native',
                                         side_effect=OSError('unavailable') if unavailable else worker_visibility.prepare_native), \
                            redirect_stdout(output):
                        code = workflow.control_main()
                    actual = json.loads(output.getvalue())
                    self.assertEqual(code, 0, actual)
                    self.assertEqual({key: actual[key] for key in report}, report)
                    preparation = actual['visibility_preparation']
                    self.assertEqual(preparation['prepared'], not unavailable)
                    if not unavailable:
                        context = worker_visibility.read_context(Path(preparation['context']))
                        self.assertEqual(context['visibility'], metadata)
                        self.assertEqual(context['ledger_assignment_digest'], ('b' if review else 'a') * 64)

    def test_checkout_context_is_rejected_before_any_file_or_lock_write(self):
        helper = self.native_helper()
        path = self.root / 'assignment.json'
        with self.assertRaises(ValueError):
            helper.prepare_native(path, self.native_request())
        self.assertEqual(list(self.root.iterdir()), [])
        outside = self.task_context / 'assignment.json'
        helper.prepare_native(outside, self.native_request())
        path.write_bytes(outside.read_bytes())
        path.chmod(0o600)
        result = helper.publish_native(path, self.child, 'Working', self.root, 'http://127.0.0.1:1', 'account')
        self.assertFalse(result['delivered'])
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_same_parent_is_insufficient_without_original_task_marker(self):
        from runtime import habblaud, worker_visibility
        helper = self.native_helper()
        client, transport = fixture_transport()
        path = self.task_context / 'assignment.json'
        helper.prepare_native(path, self.native_request())
        del self.child_thread['source']['subAgent']['thread_spawn']['agent_path']
        with patch.object(codex, '_native_reader', self.reader), patch.object(habblaud, 'transport', return_value=transport):
            result = helper.publish_native(path, self.child, 'Reviewing', self.root, 'http://127.0.0.1:1', 'account')
        self.assertFalse(result['delivered'])
        self.assertEqual(result['reason'], 'native_visibility_identity_unobserved')
        self.assertEqual(client.events, [])

    def test_missing_native_path_can_use_exact_observed_spawn_binding(self):
        from runtime import habblaud, worker_visibility
        helper = self.native_helper()
        del self.child_thread['source']['subAgent']['thread_spawn']['agent_path']
        for managed in (False, True):
            with self.subTest(managed=managed):
                client, transport = fixture_transport()
                path = self.task_context / f'assignment-{managed}.json'
                request = self.native_request()
                if managed:
                    body = dict(binding=dict(task_name=request['task_name']),
                                request=dict(visibility=request['visibility']))
                    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    request['ledger_assignment_digest'] = digest
                    prepared = helper.prepare_native(path, request)
                    self.spawn_prompt = json.dumps(dict(ownership=body, ledger_assignment_digest=digest))
                else:
                    prepared = helper.prepare_native(path, request)
                    self.assertIn('prompt_marker', prepared)
                    self.spawn_prompt = prepared['prompt_marker'] + '\nRead only.'
                with patch.object(codex, '_native_reader', self.reader), patch.object(habblaud, 'transport', return_value=transport):
                    valid_prompt = self.spawn_prompt
                    self.spawn_prompt = 'Different assignment'
                    wrong = helper.publish_native(path, self.child, 'Reviewing', self.root, 'http://127.0.0.1:1', 'account',
                                                  parent_turn_id='parent-turn', spawn_item_id='spawn')
                    self.assertFalse(wrong['delivered'])
                    self.assertEqual(client.events, [])
                    followup = {key: request[key] for key in ('parent_session_id', 'checkout', 'task_name')}
                    followup.update(worker_id=self.child, message='Continue the review.',
                                    parent_turn_id='parent-turn', spawn_item_id='spawn')
                    before = path.read_bytes()
                    self.assertFalse(helper.prepare_assignment(followup, context=path)['prepared'])
                    self.assertEqual(path.read_bytes(), before)
                    self.spawn_prompt = valid_prompt
                    continued = helper.prepare_assignment(followup, context=path)
                    self.assertTrue(continued['prepared'])
                    self.assertEqual(continued['message'], followup['message'])
                    self.assertEqual(helper.read_context(path)['observed']['worker_id'], self.child)
                    self.assertEqual(client.events, [])
                    result = helper.publish_native(path, self.child, 'Reviewing', self.root, 'http://127.0.0.1:1', 'account',
                                                   parent_turn_id='parent-turn', spawn_item_id='spawn')
                self.assertTrue(result['delivered'])
                self.assertEqual(client.events[0]['agent_id'], self.child)
                self.assertEqual(client.events[0]['agent_type'], 'Audit lead · Native model')


if __name__ == '__main__':
    unittest.main()
