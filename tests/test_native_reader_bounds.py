"""Native transcript capacity is separate from hook and transport envelopes."""

import hashlib
import unittest

from runtime import doctor, startup
from runtime.adapters import codex
from tests.fixture_native_behavior import PATCH, call, encoded, output, row, session, started


class NativeReaderBoundsTests(unittest.TestCase):
    def decode(self, raw, thread='native-thread'):
        return doctor.native_behavior_records(raw, thread_id=thread,
            source_reference='/public/native-rollout.jsonl')

    def test_large_framed_record_preserves_policy_proof_and_original_sources(self):
        prefix = encoded([session(), started()])
        large = encoded([row('world_state', dict(state='x' * (64 * 1024 * 1024)))])
        tail = encoded([call(), output()])
        raw = prefix + large + tail
        del large
        records = self.decode(raw)
        fact = codex._native_policy_rejection(records, submitted_patch=PATCH, turn_id='native-turn')
        offset = len(raw) - len(tail)
        for source, line in zip(fact['sources'], tail.splitlines(keepends=True)):
            self.assertEqual(source, dict(reference=f'/public/native-rollout.jsonl#bytes:{offset}:{len(line)}',
                sha256=hashlib.sha256(line).hexdigest()))
            self.assertEqual(raw[offset:offset+len(line)], line)
            offset += len(line)

    def test_long_history_keeps_first_seen_turn_order_and_late_tool_validation(self):
        rows = [session()]
        for index in range(10000):
            turn = f'turn-{index}'
            rows.extend([row('event_msg', dict(type='task_started', turn_id=turn)),
                row('turn_context', dict(turn_id=turn)),
                row('event_msg', dict(type='task_complete', turn_id=turn))])
        rows.extend([started(), call(), output()])
        records = self.decode(encoded(rows))
        self.assertEqual(tuple(records['turn_ids']), tuple(f'turn-{i}' for i in range(10000)) + ('native-turn',))
        self.assertEqual(codex._native_policy_rejection(records, submitted_patch=PATCH,
            turn_id='native-turn')['call_id'], 'native-call')
        rows.append(row('response_item', dict(type='web_search_call', id='unexpected')))
        with self.assertRaises(doctor.ObservationError):
            codex._native_policy_rejection(self.decode(encoded(rows)), submitted_patch=PATCH, turn_id='native-turn')

    def test_large_valid_prefix_does_not_hide_wrong_identity_strict_json_or_partial_eof(self):
        raw = encoded([session(), row('world_state', dict(state='x' * (1024 * 1024)))])
        self.assertEqual(self.decode(raw)['thread_id'], 'native-thread')
        with self.assertRaises(doctor.ObservationError):
            self.decode(raw, thread='independently-expected-other-thread')
        for suffix in (b'{"timestamp":"now","type":"world_state","payload":{"x":1,"x":2}}\n',
                b'{"timestamp":"now","type":"world_state","payload":{"x":NaN}}\n',
                b'{"timestamp":"now","type":"world_state","payload":{}}'):
            with self.subTest(suffix=suffix), self.assertRaises(doctor.ObservationError):
                self.decode(raw + suffix)
        with self.assertRaises(startup.StartupError):
            startup.json_object(encoded([row('world_state', dict(state='x' * startup.MAX_INPUT))]))

    def test_record_limit_counts_complete_framing(self):
        limit = 72 * 1024 * 1024
        small = encoded([row('world_state', dict(state=''))])
        line = small.replace(b'"state":""', b'"state":"' + b'x' * (limit-len(small)) + b'"')
        self.assertEqual(len(line), limit)
        prefix = encoded([session()])
        self.assertEqual(self.decode(prefix + line)['thread_id'], 'native-thread')
        with self.assertRaises(doctor.ObservationError):
            self.decode(prefix + line[:-2] + b' ' + line[-2:])

    def test_file_and_row_boundaries_still_fail_closed(self):
        prefix = encoded([session()])
        ignored = encoded([row('world_state', {})])
        raw = prefix + ignored * (131072-1)
        self.assertEqual(self.decode(raw)['thread_id'], 'native-thread')
        with self.assertRaises(doctor.ObservationError):
            self.decode(raw + ignored)
        del raw
        # Use few large ignored records to isolate the file boundary from the row boundary.
        limit = 128 * 1024 * 1024
        chunk = encoded([row('world_state', dict(state='x' * (1024 * 1024)))])
        count, remainder = divmod(limit-len(prefix), len(chunk))
        last = encoded([row('world_state', dict(state='x' * (remainder-len(encoded([row('world_state', dict(state=''))])))))])
        raw = prefix + chunk * count + last
        self.assertEqual(len(raw), limit)
        self.assertEqual(self.decode(raw)['thread_id'], 'native-thread')
        with self.assertRaises(doctor.ObservationError):
            self.decode(raw[:-2] + b' ' + raw[-2:])


if __name__ == '__main__':
    unittest.main()
