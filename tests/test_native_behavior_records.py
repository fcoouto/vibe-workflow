"""Raw native protocol fixtures; these software checks never qualify a real client."""

import hashlib
import json
from pathlib import Path
import unittest

from runtime import doctor, startup
from runtime.adapters import codex
from tests.fixture_native_behavior import (
    REJECTION, PATCH, CONTEXT, row, encoded, session, started, call, output,
    bootstrap_reads, wrapped_patch_denial, hook)


class NativeBehaviorRecordTests(unittest.TestCase):
    def test_darwin_system_config_uses_only_the_builtin_etc_alias(self):
        import stat
        from types import SimpleNamespace
        from unittest.mock import patch
        from runtime import setup_observation

        path = Path('/etc/codex/config.toml')
        canonical = Path('/private/etc/codex/config.toml')

        def stat_path(entry):
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK if entry == Path('/etc') else stat.S_IFDIR)

        with patch('sys.platform', 'darwin'), patch.object(Path, 'lstat', stat_path):
            with patch.object(Path, 'readlink', return_value=Path('private/etc')):
                self.assertEqual(setup_observation._system_layer_path(path), canonical)
                alternate = Path('/etc/other/config.toml')
                self.assertEqual(setup_observation._system_layer_path(alternate), alternate)
            with patch.object(Path, 'readlink', return_value=Path('/tmp/alternate-etc')):
                with self.assertRaises(startup.StartupError):
                    setup_observation._system_layer_path(path)
        with patch('sys.platform', 'linux'):
            self.assertEqual(setup_observation._system_layer_path(path), path)

    def test_canary_rejects_a_path_codex_would_trim(self):
        from runtime.setup_observation import _public_canary_patch
        root = Path('/public/disposable')
        for suffix in (' ', '\t', '\u00a0'):
            with self.subTest(suffix=suffix), self.assertRaises(startup.StartupError):
                _public_canary_patch(dict(canary=str(root / ('public-canary.txt' + suffix)),
                    before_sha256='a'*64, patch=PATCH.replace('public-canary.txt', 'public-canary.txt' + suffix)), root)

    def test_canary_readback_uses_the_first_native_line_ending(self):
        from runtime.setup_observation import _public_canary_patch
        root = Path('/public/disposable')
        descriptor = dict(canary=str(root / 'public-canary.txt'), before_sha256='a'*64, patch=PATCH)
        self.assertEqual(_public_canary_patch(descriptor, root, readback='keep\r\nbefore\nlast'),
            'keep\r\nafter\r\nlast\r\n')
        self.assertEqual(_public_canary_patch(descriptor, root, readback='before\r'),
            'after\r')
        context_patch = descriptor | dict(patch=PATCH.replace('+after\n', '+after\n context\n'))
        self.assertEqual(_public_canary_patch(context_patch, root, readback='keep\r\nbefore\ncontext\n'),
            'keep\r\nafter\r\ncontext\n')

    def api(self, module, name):
        value = getattr(module, name, None)
        self.assertIsNotNone(value, f'Native record API missing: {name}')
        return value

    def records(self, rows):
        return self.api(doctor, 'native_behavior_records')(encoded(rows), thread_id='native-thread',
            source_reference='/public/native-rollout.jsonl')

    def rejection(self, rows, patch=PATCH):
        return self.api(codex, '_native_policy_rejection')(self.records(rows), submitted_patch=patch,
            turn_id='native-turn')

    def test_real_custom_call_pair_uses_native_ids_and_exact_policy_text(self):
        records = self.records([session(), started(), call(), output(),
            row('event_msg', dict(type='task_complete', turn_id='native-turn'))])
        fact = self.api(codex, '_native_policy_rejection')(records, submitted_patch=PATCH, turn_id='native-turn')
        self.assertEqual(fact['call_id'], 'native-call')
        self.assertEqual(fact['turn_id'], 'native-turn')
        self.assertEqual(fact['thread_id'], 'native-thread')
        self.assertNotIn('success', fact)
        self.assertNotIn('item_id', fact)
        self.assertNotIn('input', fact)
        self.assertEqual(fact['submitted_patch_sha256'], hashlib.sha256(PATCH.encode()).hexdigest())
        self.assertEqual(len(fact['sources']), 2)
        with self.assertRaises(TypeError):
            fact['sources'][0]['sha256'] = 'a'*64

    def test_exec_wrapper_binds_only_one_exact_patch_denial(self):
        script = 'text(await tools.apply_patch(' + json.dumps(PATCH) + '));\n'
        wrapper = row('response_item', dict(type='custom_tool_call', name='exec',
            call_id='native-call', input=script))
        text = [dict(type='input_text', text='Script failed\nWall time 0.5 seconds\nOutput:\n'),
            dict(type='input_text', text='Script error:\n' + REJECTION)]
        fact = self.rejection([session(), started(), wrapper, output(text)])
        self.assertEqual(fact['call_id'], 'native-call')
        self.assertEqual(len(fact['sources']), 2)
        for input_text, result in (
            (script + 'text(1);', text),
            (script.replace('after', 'different'), text),
            (script, text + [dict(type='input_text', text='extra')]),
            (script, [text[0], dict(type='input_text', text='Script error:\nEPERM')]),
        ):
            changed = row('response_item', dict(wrapper['payload'], input=input_text))
            with self.subTest(input_text=input_text, result=result), self.assertRaises(doctor.ObservationError):
                self.rejection([session(), started(), changed, output(result)])
        with self.assertRaises(doctor.ObservationError):
            self.rejection([session(), started(), call(), output(), wrapper, output(text)])

    def test_policy_denial_with_approved_startup_reads(self):
        from copy import deepcopy

        reads, hashes = bootstrap_reads()
        patch, denial = wrapped_patch_denial()
        rows = [session(), started(), *reads, patch, denial]

        def verify(rows, **options):
            return codex._native_policy_rejection(self.records(rows), submitted_patch=PATCH,
                turn_id='native-turn', **options)

        fact = verify(rows, startup_read_sha256=hashes)
        self.assertEqual(fact['call_id'], 'native-call')
        self.assertEqual(fact['sources'], tuple(record['source'] for record in self.records(rows)['records'][-2:]))
        self.assertEqual(verify([session(), started(), *reads[:-1], patch, denial],
            startup_read_sha256=hashes)['call_id'], 'native-call')
        with self.assertRaises(doctor.ObservationError):
            verify(rows)
        with self.assertRaises(doctor.ObservationError):
            verify(rows, startup_read_sha256=hashes[::-1])

        for index, key, value in (
            (2, 'input', reads[0]['payload']['input'] + 'text(1);'),
            (3, 'call_id', 'another-call'),
            (3, 'output', 'process still running'),
            (8, 'name', 'apply_patch'),
            (8, 'call_id', 'native-call'),
        ):
            changed = deepcopy(rows)
            changed[index]['payload'][key] = value
            with self.subTest(index=index, key=key), self.assertRaises(doctor.ObservationError):
                verify(changed, startup_read_sha256=hashes)
        unexpected = row('response_item', dict(type='web_search_call', id='unapproved'))
        for extra in ([unexpected], [patch, denial]):
            with self.assertRaises(doctor.ObservationError):
                verify([*rows, *extra], startup_read_sha256=hashes)

    def test_retained_journal_reuse_rechecks_identity_and_preserves_bounds(self):
        import os
        import tempfile
        from runtime import setup_observation as source

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'transaction.json'
            path.write_bytes(b'{}')
            path.chmod(0o600)
            budget = source._budget()
            read = source._retained_journal_bytes
            self.assertEqual(read(path, budget, 100), b'{}')
            self.assertEqual(read(path, budget, 100), b'{}')
            self.assertEqual((budget['paths'], budget['bytes']), (2, 2))
            with self.assertRaisesRegex(startup.StartupError, 'budget exhausted'):
                read(path, budget | {'paths': 4096}, 100)
            with self.assertRaises(startup.StartupError):
                read(path, budget | {'deadline': 0}, 100)
            with self.assertRaisesRegex(startup.StartupError, 'file size limit'):
                read(path, source._budget() | {'bytes': source.package_manifest.PACKAGE_SCAN_MAX_BYTES-2}, 100)
            replacement = path.with_suffix('.new')
            replacement.write_bytes(b'[]')
            replacement.chmod(0o600)
            os.replace(replacement, path)
            with self.assertRaisesRegex(startup.StartupError, 'journal changed'):
                read(path, budget, 100)
            fresh = source._budget()
            self.assertEqual(read(path, fresh, 100), b'[]')
            path.chmod(0o644)
            with self.assertRaisesRegex(startup.StartupError, 'must be private'):
                read(path, fresh, 100)

    def test_no_generic_error_refusal_or_serialized_success_is_required(self):
        for value in ('EPERM', 'I cannot edit files', REJECTION+'\n', 'SandboxDenied: '+REJECTION):
            with self.subTest(value=value), self.assertRaises(doctor.ObservationError):
                self.rejection([session(), started(), call(), output(value)])

    def test_other_namespace_calls_mismatched_input_and_ambiguous_calls_fail(self):
        variants = ([session(), started(), call(namespace='mcp'), output()],
            [session(), started(), call(), output(), call(), output()],
            [session(), started(), call(), row('response_item', dict(type='custom_tool_call',
                name='shell', call_id='other', input='PRIVATE')), output()],
            [session(), started(), output(), call()],
            [session(), started(), call(), output(), row('event_msg', dict(type='task_started', turn_id='other'))])
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(doctor.ObservationError):
                self.rejection(rows)
        with self.assertRaises(doctor.ObservationError):
            self.rejection([session(), started(), call(), output()], patch=PATCH.replace('after', 'different'))
        for empty in ('', '*** Begin Patch\n*** End Patch'):
            with self.subTest(empty=empty), self.assertRaises(doctor.ObservationError):
                self.rejection([session(), started(), call(), output()], patch=empty)

    def test_context_requires_indexed_harness_hook_provenance_and_wrapper(self):
        context = self.api(codex, '_native_hook_context')(self.records([session(), started(), hook()]),
            expected_context=CONTEXT, turn_id='native-turn')
        self.assertEqual(context['hook_run_id'], 'native-hook')
        self.assertEqual(context['turn_id'], 'native-turn')
        self.assertNotIn('event', context)
        self.assertNotIn('source_path', context)
        for changed in (hook(provenance=dict(type='user')), hook(provenance=dict(type='harness', hook=False)),
                hook(text=CONTEXT+' altered')):
            with self.subTest(changed=changed), self.assertRaises(doctor.ObservationError):
                self.api(codex, '_native_hook_context')(self.records([session(), started(), changed]),
                    expected_context=CONTEXT, turn_id='native-turn')

    def developer_hook(self):
        return row('response_item', dict(type='message', id='native-message', role='developer',
            content=[dict(type='input_text', text=CONTEXT)],
            internal_chat_message_metadata_passthrough=dict(turn_id='native-turn',
                content_item_kinds=['hooks.additional_context'],
                content_item_metadata=[dict(provenance=dict(type='harness', hook=True))])))

    def test_developer_hook_context_preserves_actual_identity_and_source(self):
        delivered = self.developer_hook()
        rows = [session(), started(), delivered]
        fact = codex._native_hook_context(self.records(rows), expected_context=CONTEXT, turn_id='native-turn')
        self.assertIsNone(fact['hook_run_id'])
        self.assertEqual(fact['message_id'], 'native-message')
        self.assertEqual(fact['turn_id'], 'native-turn')
        self.assertEqual(fact['content_index'], 0)
        line = encoded([delivered])
        self.assertEqual(startup._canonical_plain(fact['sources']), [dict(
            reference=f'/public/native-rollout.jsonl#bytes:{len(encoded(rows[:2]))}:{len(line)}',
            sha256=hashlib.sha256(line).hexdigest())])

    def test_developer_hook_requires_indexed_provenance_kind_turn_and_message(self):
        from copy import deepcopy
        original = self.developer_hook()
        variants = []
        for key, values in (('role', ['user', 'assistant', 'system']), ('id', [None, '', 1]),
                ('content', [[dict(type='input_text', text=CONTEXT+' altered')]])):
            for value in values:
                changed = deepcopy(original); changed['payload'][key] = value; variants.append(changed)
        for key, values in (
                ('turn_id', [None, 'other']),
                ('content_item_kinds', [None, [], ['ordinary'], ['hooks.additional_context', 'ordinary']]),
                ('content_item_metadata', [None, [], [dict(provenance=dict(type='user', hook=True))],
                    [dict(provenance=dict(type='harness', hook=False))],
                    [dict(provenance=dict(type='harness', hook=1))]])):
            for value in values:
                changed = deepcopy(original)
                changed['payload']['internal_chat_message_metadata_passthrough'][key] = value
                variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(doctor.ObservationError):
                codex._native_hook_context(self.records([session(), started(), changed]),
                    expected_context=CONTEXT, turn_id='native-turn')
        for rows, turn in (([session(), original, started()], None),
                ([session(), started(), original], None),
                ([session(), started(), original, original], 'native-turn'),
                ([session(), started(), original, hook()], 'native-turn')):
            with self.subTest(turn=turn, rows=rows), self.assertRaises(doctor.ObservationError):
                codex._native_hook_context(self.records(rows), expected_context=CONTEXT, turn_id=turn)

    def test_developer_hook_uses_matching_content_index_only(self):
        delivered = self.developer_hook()
        delivered['payload']['content'].insert(0, dict(type='input_text', text='Ordinary context'))
        metadata = delivered['payload']['internal_chat_message_metadata_passthrough']
        metadata['content_item_kinds'].insert(0, 'ordinary')
        metadata['content_item_metadata'].insert(0, dict(provenance=dict(type='user')))
        fact = codex._native_hook_context(self.records([session(), started(), delivered]),
            expected_context=CONTEXT, turn_id='native-turn')
        self.assertEqual(fact['content_index'], 1)
        metadata['content_item_kinds'].reverse()
        with self.assertRaises(doctor.ObservationError):
            codex._native_hook_context(self.records([session(), started(), delivered]),
                expected_context=CONTEXT, turn_id='native-turn')

    def test_startup_context_before_first_turn_retains_missing_turn(self):
        value = self.api(codex, '_native_hook_context')(self.records([session(), hook(), started()]),
            expected_context=CONTEXT, turn_id=None)
        self.assertIsNone(value['turn_id'])
        with self.assertRaises(doctor.ObservationError):
            self.api(codex, '_native_hook_context')(self.records([session(), hook(), started()]),
                expected_context=CONTEXT, turn_id='native-turn')

    def test_sources_bind_complete_original_line_ranges(self):
        rows = [session(), started(), call(), output()]
        raw = encoded(rows)
        records = self.records(rows)
        fact = self.api(codex, '_native_policy_rejection')(records, submitted_patch=PATCH, turn_id='native-turn')
        offset = len(encoded(rows[:2]))
        for index, source in enumerate(fact['sources']):
            line = encoded([rows[index+2]])
            self.assertEqual(source, dict(reference=f'/public/native-rollout.jsonl#bytes:{offset}:{len(line)}',
                sha256=hashlib.sha256(line).hexdigest()))
            self.assertEqual(raw[offset:offset+len(line)], line)
            offset += len(line)

    def test_hook_xml_rejects_declarations_entities_nested_and_unknown_attributes(self):
        texts = ('<!DOCTYPE hook_prompt [<!ENTITY injected "PRIVATE">]><hook_prompt hook_run_id="native-hook">&injected;</hook_prompt>',
            '<hook_prompt hook_run_id="native-hook"><child>'+CONTEXT+'</child></hook_prompt>',
            '<hook_prompt hook_run_id="native-hook" unknown="PRIVATE">'+CONTEXT+'</hook_prompt>',
            '<hook_prompt hook_run_id="native-hook">'+CONTEXT+'</hook_prompt><hook_prompt hook_run_id="other"/>')
        for text in texts:
            changed = hook()
            changed['payload']['content'][0]['text'] = text
            with self.subTest(text=text), self.assertRaises(doctor.ObservationError):
                self.api(codex, '_native_hook_context')(self.records([session(), started(), changed]),
                    expected_context=CONTEXT, turn_id='native-turn')

    def test_native_escaped_hook_context_hashes_decoded_original_text(self):
        expected = 'Restricted target /public/a&b: compare <before> and >after<.'
        changed = hook()
        wrapped = '<hook_prompt hook_run_id="native-hook">Restricted target /public/a&amp;b: compare &lt;before&gt; and &#62;after&#60;.</hook_prompt>'
        changed['payload']['content'][0]['text'] = wrapped
        fact = self.api(codex, '_native_hook_context')(self.records([session(), started(), changed]),
            expected_context=expected, turn_id='native-turn')
        self.assertEqual(fact['context_sha256'], hashlib.sha256(expected.encode()).hexdigest())
        self.assertNotEqual(fact['context_sha256'], hashlib.sha256(wrapped.encode()).hexdigest())

    def test_actual_optional_rollout_envelope_and_context_selector(self):
        changed = hook()
        changed['ordinal'] = 2**64-1
        changed['metadata'] = dict(fixture_only='PRIVATE_NOT_AUTHORITY')
        changed['payload']['id'] = 'actual-native-message'
        changed['payload']['content'].insert(0, dict(type='input_text', text='Ordinary user context'))
        changed['payload']['internal_chat_message_metadata_passthrough']['content_item_metadata'].insert(0,
            dict(provenance=dict(type='user')))
        meta = session(); meta['ordinal'] = None
        fact = self.api(codex, '_native_hook_context')(self.records([meta, started(), changed]),
            expected_context=CONTEXT, turn_id='native-turn')
        self.assertEqual(fact['message_id'], 'actual-native-message')
        self.assertEqual(fact['content_index'], 1)
        self.assertNotIn('PRIVATE_NOT_AUTHORITY', json.dumps(startup._canonical_plain(fact)))
        nullable = hook(); nullable['ordinal'] = 0; nullable['metadata'] = None
        self.records([session(), nullable])
        for ordinal in (True, -1, 2**64, '1'):
            invalid = hook(); invalid['ordinal'] = ordinal
            with self.subTest(ordinal=ordinal), self.assertRaises(doctor.ObservationError):
                self.records([session(), invalid])
        for invalid in (dict(hook(), metadata='PRIVATE'), dict(session(), metadata={}),
                dict(hook(), unexpected=True)):
            with self.subTest(invalid=invalid), self.assertRaises(doctor.ObservationError):
                self.records([session(), invalid] if invalid['type'] != 'session_meta' else [invalid])

        for passthrough in (None, [], 'PRIVATE', dict(content_item_metadata=None),
                dict(content_item_metadata='PRIVATE'), dict(content_item_metadata={})):
            malformed = hook()
            malformed['payload']['internal_chat_message_metadata_passthrough'] = passthrough
            with self.subTest(passthrough=passthrough), self.assertRaises(doctor.ObservationError) as caught:
                self.api(codex, '_native_hook_context')(self.records([session(), started(), malformed]),
                    expected_context=CONTEXT, turn_id='native-turn')
            self.assertNotIn('PRIVATE', str(caught.exception))
        oversized = '*** Begin Patch\n*** Update File: public-canary.txt\n@@\n+'+'x'*startup.MAX_INPUT+'\n*** End Patch'
        with self.assertRaises(doctor.ObservationError):
            self.rejection([session(), started(), call(), output()], patch=oversized)

    def test_managed_pair_distinguishes_pre_hook_denial_and_post_feedback(self):
        observe = self.api(codex, '_native_managed_tool')
        reason = 'Patching requires a WORKING editable bound task without applicable blockers'
        rejected = 'Command blocked by PreToolUse hook: '+reason+'. Command: '+PATCH
        pre = observe(self.records([session(), started(), call(), output(rejected)]), submitted_patch=PATCH,
            turn_id='native-turn', call_id='native-call', case_id='pre-tool-enforcement')
        self.assertEqual(pre['call_id'], 'native-call')
        self.assertEqual(len(pre['sources']), 2)
        for text in (REJECTION, 'Tool call blocked by PreToolUse hook: '+reason+'. Tool: apply_patch', rejected+' altered'):
            with self.subTest(text=text), self.assertRaises(doctor.ObservationError):
                observe(self.records([session(), started(), call(), output(text)]), submitted_patch=PATCH,
                    turn_id='native-turn', call_id='native-call', case_id='pre-tool-enforcement')
        for text in ('PostToolUse feedback after the actual successful patch.',
                [dict(type='input_text', text='Native post-hook feedback')]):
            with self.subTest(text=text):
                post = observe(self.records([session(), started(), call(), output(text)]), submitted_patch=PATCH,
                    turn_id='native-turn', call_id='native-call', case_id='post-tool-handling')
                self.assertNotIn('success', post)
                self.assertEqual(post['submitted_patch_sha256'], hashlib.sha256(PATCH.encode()).hexdigest())
        with self.assertRaises(doctor.ObservationError):
            observe(self.records([session(), started(), call(), output()]), submitted_patch=PATCH,
                turn_id='native-turn', call_id='foreign', case_id='post-tool-handling')

    def test_managed_wrapped_child_patch_binds_the_nested_completion(self):
        patch = row('response_item', dict(type='custom_tool_call', name='exec', call_id='outer',
            input='text(await tools.apply_patch(' + json.dumps(PATCH) + '))\n'))
        response = 'Success. Updated the following files:\nM public-canary.txt\n'
        completed = row('event_msg', dict(type='item_completed', thread_id='native-thread', turn_id='native-turn',
            item=dict(type='FileChange', id='inner', status='completed', changes={'public-canary.txt': {'type': 'update'}},
                stdout=response, stderr='')))
        result = row('response_item', dict(type='custom_tool_call_output', call_id='outer', output=[
            dict(type='input_text', text='Script completed\nWall time 0.7 seconds\nOutput:\n'),
            dict(type='input_text', text='{}'),
        ]))
        rows = [session(), started(), patch, completed, result]

        def observe(rows, call_id='inner'):
            return codex._native_managed_tool(self.records(rows), raw=encoded(rows), submitted_patch=PATCH,
                turn_id='native-turn', call_id=call_id, case_id='post-tool-handling', child_patch=True)

        fact = observe(rows)
        self.assertEqual(fact['call_id'], 'inner')
        self.assertEqual(fact['post_hook_fingerprint'], codex._tool_fingerprint(dict(tool_name='apply_patch',
            tool_input=dict(command=PATCH), tool_response=response)))
        self.assertEqual(len(fact['sources']), 2)
        with self.assertRaises(doctor.ObservationError):
            observe(rows, call_id='outer')
        for changed in ([session(), started(), patch, result], [*rows[:4], completed, result],
                        [*rows[:2], completed, patch, result], [*rows, completed],
                        [*rows[:2], completed, *rows[2:]]):
            with self.assertRaises(doctor.ObservationError):
                observe(changed)

    def managed_wrapped_denial(self):
        patch, result = wrapped_patch_denial()
        reason = 'Patching requires a WORKING editable bound task without applicable blockers'
        result['payload']['output'][1]['text'] = (
            'Script error:\nCommand blocked by PreToolUse hook: '+reason+'. Command: '+PATCH)
        return [session(), started(), patch,
            row('token_usage_record', dict(usage={})), result]

    def test_managed_parent_wrapped_denial_keeps_outer_identity_and_original_sources(self):
        rows = self.managed_wrapped_denial()
        raw = encoded(rows)
        fact = codex._native_managed_tool(self.records(rows), raw=raw, submitted_patch=PATCH,
            turn_id='native-turn', call_id='native-call', case_id='pre-tool-enforcement')
        self.assertEqual(fact['call_id'], 'native-call')
        self.assertEqual(fact['turn_id'], 'native-turn')
        self.assertEqual(fact['submitted_patch_sha256'], hashlib.sha256(PATCH.encode()).hexdigest())
        expected = []
        for index in (2, 4):
            line = encoded([rows[index]])
            offset = len(encoded(rows[:index]))
            expected.append(dict(reference=f'/public/native-rollout.jsonl#bytes:{offset}:{len(line)}',
                sha256=hashlib.sha256(line).hexdigest()))
            self.assertEqual(raw[offset:offset+len(line)], line)
        self.assertEqual(startup._canonical_plain(fact['sources']), expected)
        self.assertNotIn('post_hook_fingerprint', fact)

    def test_managed_parent_wrapped_denial_rejects_substitution_and_ambiguous_pairs(self):
        from copy import deepcopy
        rows = self.managed_wrapped_denial()
        variants = []
        for index, key, values in (
                (2, 'input', [rows[2]['payload']['input']+'text(1);',
                    rows[2]['payload']['input'].replace('after', 'different'),
                    'text(await tools.apply_patch(PATCH));\n']),
                (2, 'namespace', ['mcp']),
                (4, 'namespace', ['mcp']),
                (4, 'name', ['apply_patch', 'shell']),
                (4, 'call_id', ['another-call']),
                (4, 'output', [
                    wrapped_patch_denial()[1]['payload']['output'],
                    rows[4]['payload']['output']+[dict(type='input_text', text='extra')],
                    [rows[4]['payload']['output'][0],
                        dict(type='input_text', text=rows[4]['payload']['output'][1]['text']+' altered')],
                    [dict(type='input_text', text='Script completed\nWall time 0.5 seconds\nOutput:\n'),
                        rows[4]['payload']['output'][1]],
                    rows[4]['payload']['output'][1]['text'],
                ])):
            for value in values:
                changed = deepcopy(rows)
                changed[index]['payload'][key] = value
                variants.append(changed)
        variants.extend(([rows[0], rows[1], rows[4], rows[2]], [*rows, rows[4]],
            [*rows, rows[2], rows[4]],
            [*rows[:4], row('event_msg', dict(type='task_complete', turn_id='native-turn')),
                row('event_msg', dict(type='task_started', turn_id='other-turn')), rows[4]]))
        for changed in variants:
            with self.subTest(rows=changed), self.assertRaises(doctor.ObservationError):
                codex._native_managed_tool(self.records(changed), submitted_patch=PATCH,
                    turn_id='native-turn', call_id='native-call', case_id='pre-tool-enforcement')
        for selection in (dict(call_id='unrecorded-inner'), dict(case_id='post-tool-handling'),
                dict(child_patch=True)):
            with self.subTest(selection=selection), self.assertRaises(doctor.ObservationError):
                codex._native_managed_tool(self.records(rows), submitted_patch=PATCH, **(
                    dict(turn_id='native-turn', call_id='native-call', case_id='pre-tool-enforcement') | selection))
        # Toolkit denial is not the native sandbox-safety claim in either direction.
        with self.assertRaises(doctor.ObservationError):
            self.rejection(rows)

    def test_managed_selected_parent_turn_keeps_actual_prior_history(self):
        observe = self.api(codex, '_native_managed_tool')
        reason = 'Patching requires a WORKING editable bound task without applicable blockers'
        prior = [session(), row('event_msg', dict(type='task_started', turn_id='past-turn')),
            row('response_item', dict(type='custom_tool_call', name='shell', call_id='past-call', input='read-only fixture')),
            row('response_item', dict(type='custom_tool_call_output', call_id='past-call', output='prior result')),
            row('event_msg', dict(type='task_complete', turn_id='past-turn'))]
        selected = [started(), call(), output('Command blocked by PreToolUse hook: '+reason+'. Command: '+PATCH)]
        fact = observe(self.records([*prior, *selected]), submitted_patch=PATCH, turn_id='native-turn',
            call_id='native-call', case_id='pre-tool-enforcement')
        self.assertEqual(fact['turn_id'], 'native-turn')
        self.assertEqual(fact['call_id'], 'native-call')
        spawn = row('response_item', dict(type='function_call', name='spawn_agent', call_id='actual-spawn-call', arguments='{}'))
        with_spawn = [*prior, started(), spawn, call(), selected[-1]]
        self.assertEqual(observe(self.records(with_spawn), submitted_patch=PATCH, turn_id='native-turn',
            call_id='native-call', case_id='pre-tool-enforcement')['call_id'], 'native-call')
        ambiguous = [*prior]
        ambiguous[2]['payload']['call_id'] = 'native-call'
        with self.assertRaises(doctor.ObservationError):
            observe(self.records([*ambiguous, *selected]), submitted_patch=PATCH, turn_id='native-turn',
                call_id='native-call', case_id='pre-tool-enforcement')

    def test_expired_original_native_reader_deadline_blocks_all_transport(self):
        import inspect
        import time
        from unittest.mock import patch
        self.assertIn('deadline', inspect.signature(codex._native_reader).parameters,
            'Native reader cannot preserve the original operation deadline')
        with patch.object(codex, 'version', side_effect=AssertionError('Version transport started')) as version, \
                patch.object(codex, 'NativeProcess', side_effect=AssertionError('Native child started')) as process, \
                patch.object(codex.shutil, 'which', side_effect=AssertionError('Native executable discovery started')) as discover:
            with self.assertRaisesRegex(doctor.ObservationError, '^timeout$'):
                with codex._native_reader(codex.REVIEW_METHODS, deadline=time.monotonic()-1):
                    self.fail('Expired original allocation reached the observer')
        version.assert_not_called()
        process.assert_not_called()
        discover.assert_not_called()

    def test_duplicate_and_wrong_thread_oversized_private_records_fail_closed(self):
        parser = self.api(doctor, 'native_behavior_records')
        for raw in (b'{"timestamp":"now","type":"session_meta","payload":{"id":"native-thread","id":"PRIVATE"}}\n',
                encoded([row('session_meta', dict(id='foreign')), started(), call(), output()]),
                encoded([session(), session(), started()]), b'PRIVATE\n', b'x'*(doctor.MAX_OUTPUT+1)):
            with self.subTest(size=len(raw)), self.assertRaises(doctor.ObservationError) as caught:
                parser(raw, thread_id='native-thread', source_reference='/public/native-rollout.jsonl')
            self.assertNotIn('PRIVATE', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
