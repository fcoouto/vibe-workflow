"""Parent guard proof uses real owners and fake native transport, never a child."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from runtime import doctor, evidence, setup_observation as source, startup, state
from runtime.adapters import codex
from tests import test_native_behavior_owner as fixtures
from tests.fixture_native_behavior import PATCH
from tests.fixture_codex_trace import write_bundle, write_events


class ParentObservationTests(unittest.TestCase):
    def setUp(self):
        self.owner = fixtures.NativeBehaviorOwnerTests('runTest')
        self.addCleanup(self.owner.doCleanups)
        self.owner.setUp()
        self.prepared = self.owner._qualified_context(managed_pre=True, parent_only=True)
        self.scope = self.prepared['published']['scope']
        self.original = self.prepared['original']
        self.allocation = self.prepared['managed']
        self.identity = self.allocation['task'] | dict(scope_ref='fixture:parent-only')
        self.start = self.identity | dict(mode='editable', decision_ref='fixture:approved',
            attempt_ref=self.allocation['reference'])
        data = json.loads(self.original['settings'].read_bytes())
        parent = self.identity['session_id']
        data['threads'] = {parent: dict(data['responses']['thread/read']['thread'], id=parent)}
        self.original['settings'].write_text(json.dumps(data))
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, CODEX_HOME=str(self.original['home'])).start()
        which = codex.shutil.which
        patch.object(codex.shutil, 'which', side_effect=lambda name, *args, **kwargs:
            str(self.original['executable']) if name == 'codex' else which(name, *args, **kwargs)).start()

    def begin(self):
        return state.start_task(self.scope, self.start)

    def before(self, task):
        with state._database(self.scope) as db:
            return evidence._read(self.scope, task['qualification_before'], db=db)

    def deny(self, *, wrapped=False, same_id=False):
        native_call_id = 'call-parent-outer' if wrapped else 'parent-only-call'
        payload = dict(hook_event_name='PreToolUse', session_id=self.identity['session_id'],
            cwd=str(self.owner.fixture.repo), turn_id='parent-only-turn', tool_name='apply_patch',
            tool_use_id=native_call_id if same_id or not wrapped else 'exec-parent-inner',
            tool_input=dict(command=PATCH))
        data = json.loads(self.original['settings'].read_bytes())
        data.update(tool_hook_command=[sys.executable, '-I', '-B', str(state.PACKAGE/'scripts/workflow.py'),
            'codex', 'PreToolUse'], tool_hook_payload=payload, wrapped_pre=wrapped, native_call_id=native_call_id)
        self.original['settings'].write_text(json.dumps(data))
        subprocess.run([str(self.original['executable']), 'exec', '--json', 'software-parent-only'],
            cwd=self.owner.fixture.repo, check=True, capture_output=True, timeout=10)
        task = state.task_status(self.scope, 'codex', self.identity['session_id'])['task']
        return self.identity | dict(expected_revision=task['revision'], stream='parent',
            turn_id=payload['turn_id'], call_id=native_call_id)

    def test_task_start_captures_immutable_before_state_without_delegation(self):
        with state._database(self.scope) as db:
            watermark = db.execute('select coalesce(max(rowid),0) from receipts').fetchone()[0]
        result = self.begin()
        task = result['task']
        self.assertIn('qualification_before', task)
        before = self.before(task)
        self.assertEqual(before['revision'], 1)
        self.assertEqual(before['generation'], result['generation'])
        self.assertEqual(before['receipt_watermark'], watermark)
        self.assertEqual(before['pins'], task['pins'])
        self.assertEqual(before['probe']['before']['readback'], 'before\n')
        self.assertEqual(self.begin()['task']['qualification_before'], task['qualification_before'])
        with state._database(self.scope) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0], 0)
            changed = deepcopy(task)
            changed['qualification_before']['digest'] = 'a' * 64
            with self.assertRaises(state.StateError):
                state._save(db, state._scope_id(self.scope), changed)
        with self.assertRaises(state.StateError):
            state.start_task(self.scope, self.start | dict(qualification_before=before))

    def test_parent_denial_and_retained_consumption_need_no_worker(self):
        started = self.begin()['task']
        selected = self.deny()
        self.assertEqual(selected['expected_revision'], started['revision'] + 1)
        witness = codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')
        content = doctor._native_witness_content(witness)
        self.assertEqual(len(content['observations']['pre-tool-enforcement']), 4)
        assessment = startup._canonical_plain(self.prepared['published']['source'].content['assessment'])
        case = next(row for row in assessment['cases'] if row['id'] == 'pre-tool-enforcement')
        case.update(status='verified', evidence=content['observations']['pre-tool-enforcement'])
        observed = source.collect_retained_setup(self.prepared['published']['binding']['receipt'],
            self.owner.identity, 'fixture:parent-only-observed', assessment, witness=witness)
        self.assertIn('pre-tool-enforcement', source._coverage(startup._canonical_plain(observed.content))[0])
        self.assertEqual(self.original['canary'].read_text(), 'before\n')
        with state._database(self.scope) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0], 0)

    def test_wrapped_denial_without_native_relation_keeps_the_real_receipt_unjoined(self):
        before = self.before(self.begin()['task'])
        selected = self.deny(wrapped=True)
        self.assertEqual(selected['call_id'], 'call-parent-outer')
        with state._database(self.scope) as db:
            receipts = db.execute('select rowid,tool_call_id,fingerprint,conflicted from receipts where rowid>?',
                (before['receipt_watermark'],)).fetchall()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0][1:], ('exec-parent-inner', codex.patch_input_fingerprint(PATCH), 0))
        raw = self.original['rollout'].read_bytes()
        self.assertNotIn(b'exec-parent-inner', raw)
        self.assertNotIn(b'item_completed', raw)
        with self.assertRaisesRegex(codex.ObservationError, '^native_hook_correlation_unavailable$'):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')
        self.assertEqual(self.original['canary'].read_text(), 'before\n')
        with self.assertRaises(codex.ObservationError):
            codex.observe_managed_case(self.scope, selected | dict(call_id='exec-parent-inner'),
                self.allocation['reference'], 'pre-tool-enforcement')

    def trace_denial(self):
        started = self.begin()['task']
        selected = self.deny(wrapped=True)
        bundle, rows, _ = write_bundle(self.original['home'], self.owner.fixture.repo,
            self.original['rollout'], thread=self.identity['session_id'], turn=selected['turn_id'],
            outer=selected['call_id'], inner='exec-parent-inner')
        witness = codex.observe_managed_case(self.scope, selected, self.allocation['reference'],
            'pre-tool-enforcement', trace_bundle=str(bundle))
        return started, selected, bundle, rows, witness

    def consume_trace(self, witness):
        content = doctor._native_witness_content(witness)
        assessment = startup._canonical_plain(self.prepared['published']['source'].content['assessment'])
        case = next(row for row in assessment['cases'] if row['id'] == 'pre-tool-enforcement')
        case.update(status='verified', evidence=content['observations']['pre-tool-enforcement'])
        return source.collect_retained_setup(self.prepared['published']['binding']['receipt'],
            self.owner.identity, 'fixture:trace-parent-observed', assessment, witness=witness)

    def test_trace_parent_denial_retains_exact_core_and_both_source_identities(self):
        started, selected, bundle, _, witness = self.trace_denial()
        proofs = doctor._native_witness_content(witness)['observations']['pre-tool-enforcement']
        self.assertEqual(len(proofs), 11)
        self.assertIn('call-parent-outer'.encode().hex(), proofs[2]['reference'])
        self.assertTrue(proofs[-1]['reference'].endswith(':exec-parent-inner'))
        with patch.dict(os.environ, CODEX_ROLLOUT_TRACE_ROOT='/unrelated/changed-location'):
            observed = startup._canonical_plain(self.consume_trace(witness).content)
        retained = observed['source']['managed']['pre-tool-enforcement']
        native = retained['native']
        self.assertEqual(native['call_id'], 'exec-parent-inner')
        self.assertEqual(native['native_call_id'], 'call-parent-outer')
        self.assertEqual(native['trace_relation']['runtime_cell_id'], 'cell-1')
        self.assertEqual(native['sources'], startup._canonical_plain(proofs[:-2]))
        before = self.before(started)
        self.assertGreater(retained['after']['receipt']['rowid'], before['receipt_watermark'])
        self.assertEqual(retained['after']['receipt']['fingerprint'], codex.patch_input_fingerprint(PATCH))
        self.assertEqual(self.original['canary'].read_text(), 'before\n')
        task = state.task_status(self.scope, 'codex', self.identity['session_id'])['task']
        self.assertEqual(task['pins'], started['pins'])
        self.assertEqual(task['qualification_attempt'], started['qualification_attempt'])
        self.assertEqual(task['revision'], selected['expected_revision'])
        with state._database(self.scope) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0], 0)

    def test_retained_trace_rechecks_every_hash_and_physical_source(self):
        _, _, bundle, _, witness = self.trace_denial()
        self.consume_trace(witness)
        for relative in ('manifest.json', 'trace.jsonl', 'payloads/1.json', 'payloads/2.json',
                         'payloads/3.json', 'payloads/4.json', 'payloads/5.json'):
            path = bundle/relative; raw = path.read_bytes()
            try:
                path.write_bytes(raw+b' ')
                with self.subTest(relative=relative), self.assertRaises(startup.StartupError):
                    self.consume_trace(witness)
            finally:
                path.write_bytes(raw)
        path = bundle/'payloads/2.json'
        replacement = bundle/'payloads/replacement.json'
        replacement.write_bytes(path.read_bytes()); replacement.chmod(0o600)
        os.replace(replacement, path)
        with self.assertRaises(startup.StartupError):
            self.consume_trace(witness)

    def test_trace_cannot_select_missing_wrong_or_old_core_receipt(self):
        started, selected, bundle, rows, _ = self.trace_denial()
        rows[4]['payload']['tool_call_id'] = 'nonexistent-hook'
        rows[5]['payload']['tool_call_id'] = 'nonexistent-hook'
        write_events(bundle, rows)
        with self.assertRaises((startup.StartupError, codex.ObservationError)):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'],
                'pre-tool-enforcement', trace_bundle=str(bundle))
        rows[4]['payload']['tool_call_id'] = rows[5]['payload']['tool_call_id'] = 'exec-parent-inner'
        write_events(bundle, rows)
        with state._database(self.scope, write=True) as db:
            db.execute("update receipts set conflicted=1 where tool_call_id='exec-parent-inner'")
        with self.assertRaises((startup.StartupError, codex.ObservationError)):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'],
                'pre-tool-enforcement', trace_bundle=str(bundle))

    def test_wrapped_denial_cannot_borrow_a_same_id_core_receipt(self):
        self.begin()
        selected = self.deny(wrapped=True, same_id=True)
        with self.assertRaisesRegex(codex.ObservationError, '^native_hook_correlation_unavailable$'):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')

    def test_parent_observation_rejects_wrong_selectors_and_post_tool_claim(self):
        self.begin()
        selected = self.deny()
        for changes in (dict(session_id='foreign'), dict(turn_id='foreign'), dict(call_id='foreign'), dict(scope_ref='foreign'),
                dict(expected_revision=selected['expected_revision'] + 1)):
            with self.subTest(changes=changes), self.assertRaises((startup.StartupError, codex.ObservationError)):
                codex.observe_managed_case(self.scope, selected | changes, self.allocation['reference'], 'pre-tool-enforcement')
        with self.assertRaises((startup.StartupError, codex.ObservationError)):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'post-tool-handling')
        self.original['canary'].write_text('different\n')
        with self.assertRaises((startup.StartupError, codex.ObservationError)):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')

    def test_parent_observation_rejects_conflicted_receipt(self):
        task = self.begin()['task']
        self.assertIn('qualification_before', task)
        selected = self.deny()
        with state._database(self.scope, write=True) as db:
            db.execute("update receipts set conflicted=1 where tool_call_id='parent-only-call'")
        with self.assertRaises((startup.StartupError, codex.ObservationError)):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')

    def test_parent_receipt_must_match_the_exact_patch(self):
        self.begin()
        selected = self.deny()
        codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')
        with state._database(self.scope, write=True) as db:
            db.execute("update receipts set fingerprint=? where tool_call_id='parent-only-call'",
                (codex.patch_input_fingerprint(PATCH.replace('+after', '+unapproved')),))
        with self.assertRaises(codex.ObservationError):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')

    def test_parent_receipt_must_be_newer_than_task_start(self):
        with state._database(self.scope, write=True) as db:
            db.execute('insert into receipts values(?,?,?,?,?,?,?,0)', (state._scope_id(self.scope),
                'codex', self.identity['session_id'], 'parent-only-turn', 'parent-only-call',
                'PreToolUse', codex.patch_input_fingerprint(PATCH)))
        self.begin()
        selected = self.deny()
        with self.assertRaises(codex.ObservationError):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')

    def test_parent_source_must_keep_its_surface_and_bytes(self):
        self.begin()
        selected = self.deny()
        codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')
        data = json.loads(self.original['settings'].read_bytes())
        thread = data['threads'][self.identity['session_id']]
        original_originator = thread['originator']
        thread['originator'] = 'codex_desktop'
        self.original['settings'].write_text(json.dumps(data))
        with self.assertRaises(codex.ObservationError):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')
        thread['originator'] = original_originator
        self.original['settings'].write_text(json.dumps(data))
        read = source.d3._read_file
        changed = False
        def changing(path, *args, **kwargs):
            nonlocal changed
            raw = read(path, *args, **kwargs)
            if Path(path) == self.original['rollout'] and not changed:
                changed = True
                with Path(path).open('ab') as stream:
                    stream.write(b'\n')
            return raw
        with patch.object(source.d3, '_read_file', side_effect=changing), self.assertRaises(codex.ObservationError):
            codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')

    def test_parent_observation_needs_original_before_pins_attempt_and_deadline(self):
        self.begin()
        selected = self.deny()
        codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')
        with state._database(self.scope) as db:
            original = db.execute('select data from tasks').fetchone()[0]
        for field in ('qualification_before', 'pins', 'qualification_attempt'):
            changed = json.loads(original)
            if field == 'qualification_before':
                changed.pop(field)
            elif field == 'pins':
                changed[field]['runtime_digest'] = 'a' * 64
            else:
                changed[field]['sha256'] = 'a' * 64
            with self.subTest(field=field):
                try:
                    with state._database(self.scope, write=True) as db:
                        db.execute('update tasks set data=?', (json.dumps(changed),))
                    with self.assertRaises((startup.StartupError, codex.ObservationError)):
                        codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')
                finally:
                    with state._database(self.scope, write=True) as db:
                        db.execute('update tasks set data=?', (original,))
        with patch.object(source.time, 'time', return_value=self.allocation['limits']['deadline'] + 1):
            with self.assertRaises((startup.StartupError, codex.ObservationError)):
                codex.observe_managed_case(self.scope, selected, self.allocation['reference'], 'pre-tool-enforcement')

    def test_failed_before_capture_rolls_back_task_birth(self):
        self.original['canary'].write_text('unapproved\n')
        with self.assertRaises((startup.StartupError, state.StateError)):
            self.begin()
        self.assertIsNone(state.task_status(self.scope, 'codex', self.identity['session_id'])['task'])


if __name__ == '__main__':
    unittest.main()
