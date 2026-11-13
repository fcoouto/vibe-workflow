"""Offline regressions: time advances deterministically; no native service calls."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.qualification import form_gate_cleanup as cleanup


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.now += seconds


class Native:
    """Selected metadata transport, including count and latency at its boundary."""
    def __init__(self, clock, unload_at=0, used=2, error=None, latency=0):
        self.clock, self.unload_at, self.used = clock, unload_at, used
        self.error, self.latency = error, latency
        self.requests = []

    def __call__(self, method, path, body=None, expected_status=None, timeout=None):
        if self.used >= 10 or self.clock() >= 30:
            raise RuntimeError('Allocation exhausted')
        self.used += 1
        self.requests.append((method, path.split('?')[0], self.clock(), timeout))
        if self.latency > timeout:
            self.clock.sleep(timeout)
            raise subprocess.TimeoutExpired('metadata', timeout)
        if self.latency:
            self.clock.sleep(self.latency)
        if self.error and '/api/plugin?' in path:
            raise self.error
        absent = self.clock() >= self.unload_at
        if '/api/plugin?' in path:
            return {'location': {'directory': '/fixture'}, 'data': [] if absent else [
                {'id': 'question-box-gating', 'state': {'status': 'active'},
                 'features': {'server': True}, 'source': {'type': 'config'}}]}
        if '/hello?' in path:
            if not absent:
                raise RuntimeError('Expected HTTP 400, RPC still available')
            return {'returncode': 1, 'stdout': json.dumps({'_tag': 'RpcError', 'type': 'rpc.unavailable',
                    'message': 'RPC is unavailable: question-box-gating'}), 'stderr': 'HTTP 400 Bad Request\n'}
        if path == '/api/info':
            return {'pid': 123, 'version': '2.2.7', 'hostname': 'fixture', 'urls': [],
                    'paths': {'tmp': '/fixture'}, 'capabilities': {'persistentPty': True}}
        raise AssertionError((method, path))


class ObservationTests(unittest.TestCase):
    def run_observation(self, *, unload_at=0, used=2, error=None, latency=0):
        clock = Clock()
        native = Native(clock, unload_at, used, error, latency)
        result = cleanup.observe_unload(native, lambda: 10 - native.used, 30, 'fixture',
                                        {'pid': 123, 'version': '2.2.7'}, clock=clock, sleep=clock.sleep)
        self.assertLessEqual(native.used, 10)
        self.assertLessEqual(clock(), 30)
        return result, native, clock

    def test_delayed_unload_does_not_fail_after_four_rapid_reads(self):
        result, native, _ = self.run_observation(unload_at=0.4)
        self.assertTrue(result['passed'], result)
        self.assertGreater(native.requests[1][2], native.requests[0][2])
        self.assertEqual(native.requests[-2][1], '/api/rpc/question-box-gating/hello')
        self.assertEqual(native.requests[-1][1], '/api/info')

    def test_immediate_unload_needs_only_three_metadata_calls(self):
        result, native, clock = self.run_observation()
        self.assertTrue(result['passed'], result)
        self.assertEqual(len(native.requests), 3)
        self.assertEqual(clock(), 0)

    def test_late_unload_uses_observation_time_and_reserves_final_calls(self):
        result, native, _ = self.run_observation(unload_at=23)
        self.assertTrue(result['passed'], result)
        self.assertEqual(native.used, 10)
        self.assertGreaterEqual(native.requests[-3][2], 23)

    def test_never_unloads_is_failed_with_process_check_retained(self):
        result, native, clock = self.run_observation(unload_at=float('inf'))
        self.assertFalse(result['passed'])
        self.assertFalse(result['inventory_subject_absent'])
        self.assertTrue(result['shared_process_unchanged'])
        self.assertGreater(clock(), 20)
        self.assertEqual(native.used, 10)

    def test_inventory_error_stays_unknown_not_success(self):
        result, native, _ = self.run_observation(error=OSError('offline failure'))
        self.assertFalse(result['passed'])
        self.assertIsNone(result['inventory_subject_absent'])
        self.assertTrue(result['errors'])
        self.assertTrue(result['shared_process_unchanged'])
        self.assertEqual(len(native.requests), 3)

    def test_transport_timeout_never_extends_deadline_or_resets_calls(self):
        result, native, clock = self.run_observation(latency=40)
        self.assertFalse(result['passed'])
        self.assertTrue(result['errors'])
        self.assertLessEqual(clock(), 30)
        self.assertLessEqual(len(native.requests), 3)

    def test_exhausted_operation_budget_makes_no_calls(self):
        result, native, _ = self.run_observation(used=10)
        self.assertFalse(result['passed'])
        self.assertIsNone(result['inventory_subject_absent'])
        self.assertEqual(native.requests, [])

    def test_only_final_verification_budget_cannot_manufacture_inventory_absence(self):
        result, native, _ = self.run_observation(used=8)
        self.assertFalse(result['passed'])
        self.assertIsNone(result['inventory_subject_absent'])
        self.assertTrue(result['rpc_unavailable'])
        self.assertTrue(result['shared_process_unchanged'])
        self.assertEqual(native.used, 10)

    def test_expired_deadline_makes_no_calls(self):
        clock = Clock()
        native = Native(clock)
        result = cleanup.observe_unload(native, lambda: 8, 0, 'fixture',
                                        {'pid': 123, 'version': '2.2.7'}, clock=clock, sleep=clock.sleep)
        self.assertFalse(result['passed'])
        self.assertEqual(native.requests, [])

    def test_changed_process_fails_even_when_plugin_disappeared(self):
        clock = Clock()
        native = Native(clock)
        result = cleanup.observe_unload(native, lambda: 10 - native.used, 30, 'fixture',
                                        {'pid': 456, 'version': '2.2.7'}, clock=clock, sleep=clock.sleep)
        self.assertFalse(result['passed'])
        self.assertFalse(result['shared_process_unchanged'])

    def test_malformed_inventory_never_counts_as_absence(self):
        for data in [None, {}, [None], [{'state': {'status': 'active'}}]]:
            with self.subTest(data=data):
                clock = Clock()
                native = Native(clock)

                def call(method, path, *args, **kwargs):
                    value = native(method, path, *args, **kwargs)
                    return {'data': data} if '/api/plugin?' in path else value

                result = cleanup.observe_unload(call, lambda: 10 - native.used, 30, 'fixture',
                    {'pid': 123, 'version': '2.2.7'}, clock=clock, sleep=clock.sleep)
                self.assertFalse(result['passed'])
                self.assertIsNone(result['inventory_subject_absent'])
                self.assertTrue(result['errors'])

    def test_rpc_unavailability_requires_exact_error_type_and_http_status(self):
        for status, body in [('HTTP 500 Internal Server Error\n', {'_tag': 'RpcError', 'type': 'rpc.unavailable'}),
                             ('HTTP 400 Bad Request\n', {'_tag': 'RpcError', 'type': 'rpc.internal',
                                                        'message': 'rpc.unavailable'})]:
            with self.subTest(status=status, body=body):
                clock = Clock()
                native = Native(clock)

                def call(method, path, *args, **kwargs):
                    value = native(method, path, *args, **kwargs)
                    if '/hello?' in path:
                        value.update(stderr=status, stdout=json.dumps(body))
                    return value

                result = cleanup.observe_unload(call, lambda: 10 - native.used, 30, 'fixture',
                    {'pid': 123, 'version': '2.2.7'}, clock=clock, sleep=clock.sleep)
                self.assertFalse(result['passed'])
                self.assertFalse(result['rpc_unavailable'])

    def test_slow_response_after_deadline_is_not_positive_proof(self):
        clock = Clock()
        native = Native(clock)

        def call(method, path, *args, **kwargs):
            value = native(method, path, *args, **kwargs)
            clock.sleep(31)  # Transport violated its timeout contract; still reject the receipt.
            return value

        result = cleanup.observe_unload(call, lambda: 10 - native.used, 30, 'fixture',
            {'pid': 123, 'version': '2.2.7'}, clock=clock, sleep=clock.sleep)
        self.assertFalse(result['passed'])
        self.assertIsNone(result['inventory_subject_absent'])
        self.assertEqual(len(native.requests), 1)

    def test_short_window_shrinks_timeouts_and_preserves_all_three_checks(self):
        clock = Clock()
        native = Native(clock)
        result = cleanup.observe_unload(native, lambda: 10 - native.used, 0.3, 'fixture',
            {'pid': 123, 'version': '2.2.7'}, clock=clock, sleep=clock.sleep)
        self.assertTrue(result['passed'])
        self.assertLessEqual(native.requests[0][3], 0.1)
        self.assertLessEqual(native.requests[1][3], 0.15)
        self.assertLessEqual(native.requests[2][3], 0.3)

    def test_one_remaining_call_is_reserved_for_process_identity(self):
        result, native, _ = self.run_observation(used=9)
        self.assertFalse(result['passed'])
        self.assertIsNone(result['rpc_unavailable'])
        self.assertTrue(result['shared_process_unchanged'])
        self.assertEqual([item[1] for item in native.requests], ['/api/info'])


class ReportingTests(unittest.TestCase):
    def finish(self, case_result, case_error, recovery):
        with tempfile.TemporaryDirectory() as directory:
            def save(name, data):
                with (Path(directory) / name).open('x') as out:
                    json.dump(data, out)

            def teardown():
                if isinstance(recovery, BaseException):
                    raise recovery
                return recovery

            result = cleanup.finalize(case_result, case_error, teardown, save)
            self.assertEqual(result, json.loads((Path(directory) / 'result.json').read_text()))
            return result

    def test_cleanup_exception_does_not_lose_successful_case_evidence(self):
        result = self.finish({'verdict': 'passed', 'case_count': 13}, None, AssertionError('unload failed'))
        self.assertEqual(result['verdict'], 'failed')
        self.assertEqual(result['cases']['status'], 'passed')
        self.assertEqual(result['cases']['result']['case_count'], 13)
        self.assertIn('unload failed', result['recovery']['errors'][0])

    def test_simultaneous_failures_survive_separately(self):
        result = self.finish(None, 'AssertionError(case failed)', OSError('cleanup failed'))
        self.assertEqual(result['verdict'], 'failed')
        self.assertEqual(result['cases']['error'], 'AssertionError(case failed)')
        self.assertEqual(result['cases']['status'], 'failed')
        self.assertIn('cleanup failed', result['recovery']['errors'][0])

    def test_case_failure_survives_successful_cleanup(self):
        result = self.finish(None, 'case failed', {'passed': True, 'errors': []})
        self.assertEqual(result['verdict'], 'failed')
        self.assertTrue(result['recovery']['passed'])

    def test_unknown_case_outcome_is_not_success(self):
        result = self.finish(None, None, {'passed': True, 'errors': []})
        self.assertEqual(result['verdict'], 'failed')
        self.assertEqual(result['cases']['status'], 'unknown')

    def test_both_successes_are_required(self):
        result = self.finish({'verdict': 'passed'}, None, {'passed': True, 'errors': []})
        self.assertEqual(result['verdict'], 'passed')

    def test_failed_cleanup_receipt_is_not_relabelled(self):
        receipt = {'passed': False, 'inventory_subject_absent': False, 'errors': []}
        result = self.finish({'verdict': 'passed'}, None, receipt)
        self.assertEqual(result['verdict'], 'failed')
        self.assertEqual(result['recovery'], receipt)
