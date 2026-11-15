"""Raw native trace correlation, using only synthetic private local fixtures."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import doctor, startup
from runtime.adapters import codex
from tests.fixture_codex_trace import DENIAL, write_bundle, write_events, write_json
from tests.fixture_native_behavior import PATCH, encoded, row


class CodexTraceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base/'home'; self.home.mkdir(mode=0o700)
        self.root = self.base/'checkout'; self.root.mkdir()
        self.rollout = self.home/'rollout.jsonl'
        self.bundle, self.events, self.script = write_bundle(self.home, self.root, self.rollout)
        rows = [
            row('session_meta', dict(id='native-thread', cwd=str(self.root))),
            row('event_msg', dict(type='task_started', turn_id='native-turn')),
            row('response_item', dict(type='custom_tool_call', name='exec', call_id='native-call', input=self.script)),
            row('response_item', dict(type='custom_tool_call_output', call_id='native-call', output=[
                dict(type='input_text', text='Script failed\nWall time 0.5 seconds\nOutput:\n'),
                dict(type='input_text', text='Script error:\n'+DENIAL)])),
            row('event_msg', dict(type='task_complete', turn_id='native-turn')),
        ]
        self.raw = encoded(rows)
        self.rollout.write_bytes(self.raw); self.rollout.chmod(0o600)

    def fact(self, **changes):
        options = dict(bundle=str(self.bundle), native_home=str(self.home), repository_root=str(self.root),
            rollout_path=str(self.rollout), deadline=time.monotonic()+5)
        options.update(changes)
        records = doctor.native_behavior_records(self.raw, thread_id='native-thread', source_reference=str(self.rollout))
        return startup._canonical_plain(codex._native_managed_tool(records, submitted_patch=PATCH,
            turn_id='native-turn', call_id='native-call', case_id='pre-tool-enforcement',
            raw=self.raw, trace_source=options))

    def test_trace_rejection_derives_distinct_hook_identity(self):
        fact = self.fact()
        self.assertEqual(fact['call_id'], 'exec-inner')
        self.assertEqual(fact['native_call_id'], 'native-call')
        self.assertEqual(len(fact['sources']), 9)
        self.assertEqual(fact['trace_relation']['runtime_cell_id'], 'cell-1')
        self.assertEqual(fact['trace_relation']['code_mode_runtime_tool_id'], '1')
        paths = {source['reference'].split('#')[0] for source in fact['sources'][2:]}
        self.assertEqual(paths, {str(self.bundle/name) for name in (
            'manifest.json', 'trace.jsonl', 'payloads/1.json', 'payloads/2.json',
            'payloads/3.json', 'payloads/4.json', 'payloads/5.json')})
        for source in fact['sources'][2:]:
            self.assertEqual(source['sha256'], hashlib.sha256(Path(source['reference'].split('#')[0]).read_bytes()).hexdigest())

    def test_wrong_duplicate_conflicting_and_partial_relations_fail_closed(self):
        variants = []
        for index, key, value in (
            (0, 'root_thread_id', 'other'), (1, 'thread_id', 'other'),
            (2, 'codex_turn_id', 'other'), (3, 'model_visible_call_id', 'printed-forgery'),
            (3, 'runtime_cell_id', 'other'), (4, 'requester', dict(type='model')),
            (4, 'kind', dict(type='exec_command')), (5, 'status', 'completed'),
            (5, 'tool_call_id', 'other'), (6, 'status', 'yielded'), (7, 'status', 'completed')):
            rows = deepcopy(self.events); rows[index]['payload'][key] = value; variants.append(rows)
        for index in range(9):
            variants.append(self.events[:index]+self.events[index+1:])
        variants += [self.events + [deepcopy(self.events[3])], self.events + [deepcopy(self.events[4])]]
        for rows in variants:
            with self.subTest(rows=rows):
                # Keep contiguous sequences so missing lifecycle edges, not only gaps, are tested.
                rows = deepcopy(rows)
                for seq, event in enumerate(rows, 1):
                    event['seq'] = seq
                write_events(self.bundle, rows)
                with self.assertRaises(codex.ObservationError):
                    self.fact()

    def test_wrong_payload_scope_and_forged_printed_mappings_fail_closed(self):
        for name, value in (
            ('1', dict(thread_id='native-thread', cwd=str(self.root), rollout_path='/wrong')),
            ('2', dict(tool_name='apply_patch', tool_namespace=None, payload=dict(type='custom', input=PATCH+' '))),
            ('3', dict(type='error', error='printed mapping native-call = exec-inner')),
            ('4', dict(response={'result':dict(cell_id='cell-1', error_text=DENIAL)})),
            ('5', dict(response={'Result':dict(cell_id='other', content_items=[], stored_values={}, error_text=DENIAL)})),
        ):
            path = self.bundle/f'payloads/{name}.json'; original = path.read_bytes()
            try:
                write_json(path, value)
                with self.subTest(name=name), self.assertRaises(codex.ObservationError):
                    self.fact()
            finally:
                path.write_bytes(original)
        path = self.bundle/'payloads/2.json'; path.unlink()
        with self.assertRaises(codex.ObservationError):
            self.fact()

    def test_trace_reader_rejects_unsafe_or_over_budget_sources(self):
        from runtime.adapters import codex_trace
        with self.assertRaises(codex.ObservationError):
            self.fact(deadline=0)
        for name, limit in (('MAX_EVENTS', 3), ('MAX_ENTRIES', 2), ('MAX_FILE', 100),
                            ('MAX_RECORD', 100), ('MAX_PAYLOAD', 10), ('MAX_BYTES', 100), ('MAX_FILES', 2)):
            with self.subTest(name=name), patch.object(codex_trace, name, limit), self.assertRaises(codex.ObservationError):
                self.fact()
        path = self.bundle/'payloads/2.json'; original = path.read_bytes()
        path.unlink(); path.symlink_to(self.bundle/'payloads/3.json')
        with self.assertRaises(codex.ObservationError):
            self.fact()
        path.unlink(); path.write_bytes(original); path.chmod(0o600)
        for key, value in (('raw_event_log', '../trace.jsonl'), ('payloads_dir', '/payloads')):
            manifest = json.loads((self.bundle/'manifest.json').read_bytes()); manifest[key] = value
            write_json(self.bundle/'manifest.json', manifest)
            with self.subTest(key=key), self.assertRaises(codex.ObservationError):
                self.fact()
            manifest[key] = 'trace.jsonl' if key == 'raw_event_log' else 'payloads'
            write_json(self.bundle/'manifest.json', manifest)
        rows = deepcopy(self.events); rows[4]['payload']['invocation_payload']['path'] = 'payloads/../manifest.json'
        write_events(self.bundle, rows)
        with self.assertRaises(codex.ObservationError):
            self.fact()

    def test_json_duplicates_depth_partial_records_and_custody_fail_closed(self):
        path = self.bundle/'manifest.json'; original = path.read_bytes()
        for raw in (b'{"schema_version":1,"schema_version":1}', b'{"deep":'+b'['*40+b'0'+b']'*40+b'}'):
            path.write_bytes(raw)
            with self.assertRaises(codex.ObservationError):
                self.fact()
        path.write_bytes(original)
        path.chmod(0o644)
        with self.assertRaises(codex.ObservationError):
            self.fact()
        path.chmod(0o600)
        with self.assertRaises(codex.ObservationError):
            self.fact(native_home=str(self.root))
        with self.assertRaises(codex.ObservationError):
            self.fact(repository_root=str(self.home))
        path = self.bundle/'trace.jsonl'
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        with self.assertRaises(codex.ObservationError):
            self.fact()

    def test_source_replacement_links_and_deadline_during_read_fail_closed(self):
        from runtime.adapters import codex_trace
        path = self.bundle/'payloads/2.json'
        held = self.bundle/'payloads/original.json'
        path.rename(held); path.hardlink_to(held)
        with self.assertRaises(codex.ObservationError):
            self.fact()
        path.unlink(); held.rename(path)
        original = codex_trace.d3._read_file
        changed = False
        def replacing(target, *args, **kwargs):
            nonlocal changed
            raw = original(target, *args, **kwargs)
            if Path(target) == path and not changed:
                changed = True
                temporary = path.with_suffix('.replacement')
                temporary.write_bytes(raw); temporary.chmod(0o600)
                temporary.replace(path)
            return raw
        with patch.object(codex_trace.d3, '_read_file', side_effect=replacing), self.assertRaises(codex.ObservationError):
            self.fact()
        bundle_alias = self.home/'alias'
        bundle_alias.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(codex.ObservationError):
            self.fact(bundle=str(bundle_alias))
        reads = 0
        def expiring(target, *args, **kwargs):
            nonlocal reads
            reads += 1
            raw = original(target, *args, **kwargs)
            if reads == 3:
                clock.return_value = 10
            return raw
        with patch.object(codex_trace.time, 'monotonic', return_value=0) as clock:
            with patch.object(codex_trace.d3, '_read_file', side_effect=expiring), self.assertRaisesRegex(codex.ObservationError, 'timeout'):
                self.fact(deadline=5)

    def test_nonfinite_deadline_cannot_remove_the_operation_bound(self):
        for deadline in (float('inf'), float('-inf'), float('nan')):
            with self.subTest(deadline=deadline), self.assertRaises(codex.ObservationError):
                self.fact(deadline=deadline)


if __name__ == '__main__':
    unittest.main()
