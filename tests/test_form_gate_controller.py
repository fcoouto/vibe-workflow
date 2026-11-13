"""Offline checks of the actual revised controller, not the native server."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.qualification import form_gate_cleanup as cleanup, form_gate_controller


class ControllerTests(unittest.TestCase):
    def setUp(self):
        source = Path(form_gate_controller.__file__)
        spec = importlib.util.spec_from_file_location('controller', source)
        self.controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.controller)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        c = self.controller
        c.ROOT = Path(self.temp.name)
        c.DIRECTORY = c.ROOT / 'fixture'
        c.DIRECTORY.mkdir()
        c.CLI = Path('/not-executed/opencode')
        c.GENERATION = 'fixture-generation'
        c.DEADLINE = 1300
        c.COUNTS = {'test': 0, 'recovery': 0}
        c.RECOVERY_DEADLINE = 0
        self.now = 0
        self.time = SimpleNamespace(time=lambda: 1000 + self.now, monotonic=lambda: 100 + self.now, sleep=self.sleep)
        self.before = {'pid': 123, 'version': '2.2.7'}
        self.requests = []
        self.unload_at = 0.4
        self.fault = None
        self.config = {'permissions': [{'effect': 'deny'}], 'plugins': [{'package': str(c.ROOT / 'plugin'), 'options': {
            'log': str(c.ROOT / 'events.jsonl'), 'deadline': int(c.DEADLINE * 1000), 'cli': str(c.CLI), 'serverPID': 123}}]}
        (c.DIRECTORY / 'opencode.json').write_text(json.dumps(self.config))
        (c.ROOT / 'events.jsonl').write_text('')
        self.addCleanup(patch.stopall)
        patch.object(c, 'time', self.time).start()
        patch.object(c.subprocess, 'run', self.transport).start()
        if hasattr(c, 'observe_unload'):
            patch.object(c, 'observe_unload', lambda *args: cleanup.observe_unload(
                *args, clock=self.time.monotonic, sleep=self.time.sleep)).start()

    def sleep(self, seconds):
        self.now += seconds

    def transport(self, argv, **options):
        path = argv[3].split('?')[0]
        self.requests.append((path, options['timeout'], self.now))
        if self.fault:
            self.sleep(options['timeout'])
            raise self.fault
        if path.endswith('/form'):
            value = {'data': []}
        elif path == '/api/plugin':
            absent = self.now >= self.unload_at
            value = {'data': [] if absent else [{'id': 'question-box-gating'}]}
            if absent:
                (self.controller.ROOT / 'events.jsonl').write_text(json.dumps({
                    'kind': 'closed', 'generation': 'fixture-generation'}) + '\n')
        elif path.endswith('/hello'):
            if self.now < self.unload_at:
                return SimpleNamespace(returncode=0, stdout='{"output":{"active":true}}', stderr='')
            return SimpleNamespace(returncode=1, stdout=json.dumps({'_tag': 'RpcError', 'type': 'rpc.unavailable'}),
                                   stderr='HTTP 400 Bad Request\n')
        elif path == '/api/info':
            value = self.before
        else:
            raise AssertionError(path)
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')

    def test_actual_teardown_waits_and_keeps_owned_config_preimage(self):
        result = self.controller.teardown(['owner', 'foreign'], self.before)
        self.assertTrue(result.get('passed'), result)
        self.assertTrue(result['disposal_completed'])
        self.assertEqual(result['recovery_api_calls'], 6)
        self.assertEqual(json.loads((self.controller.ROOT / 'fixture-config-before-teardown.json').read_text()), self.config)
        self.config['plugins'] = []
        self.assertEqual(json.loads((self.controller.DIRECTORY / 'opencode.json').read_text()), self.config)

    def test_actual_call_does_not_round_timeout_past_remaining_deadline(self):
        c = self.controller
        c.RECOVERY_DEADLINE = self.time.monotonic() + 0.01
        c.call('get', '/api/info', recovery=True)
        self.assertLessEqual(self.requests[0][1], 0.010001)

    def test_actual_call_records_timeout_and_consumes_one_operation(self):
        c = self.controller
        c.RECOVERY_DEADLINE = self.time.monotonic() + 30
        self.fault = subprocess.TimeoutExpired('metadata', 20)
        with self.assertRaises(subprocess.TimeoutExpired):
            c.call('get', '/api/info', recovery=True)
        receipt = json.loads((c.ROOT / 'api.jsonl').read_text())
        self.assertIn('TimeoutExpired', receipt['error'])
        self.assertEqual(c.COUNTS['recovery'], 1)

    def test_configuration_error_still_saves_failed_teardown(self):
        c = self.controller
        (c.DIRECTORY / 'opencode.json').write_text('{invalid')
        result = c.teardown([], self.before)
        self.assertFalse(result['passed'])
        self.assertEqual(result, json.loads((c.ROOT / 'teardown.json').read_text()))
        self.assertEqual((c.DIRECTORY / 'opencode.json').read_text(), '{invalid')

    def test_deadline_cannot_extend_beyond_original_overall_allocation(self):
        c = self.controller
        c.DEADLINE = 1001
        result = c.teardown([], self.before)
        self.assertLessEqual(c.RECOVERY_DEADLINE, 101)
        self.assertFalse(result['passed'])

    def test_repeated_teardown_cannot_reset_budget_or_replay_mutation(self):
        c = self.controller
        c.teardown([], self.before)
        deadline, calls = c.RECOVERY_DEADLINE, c.COUNTS['recovery']
        config = (c.DIRECTORY / 'opencode.json').read_bytes()
        with self.assertRaisesRegex(AssertionError, 'Do not replay'):
            c.teardown([], self.before)
        self.assertEqual(c.RECOVERY_DEADLINE, deadline)
        self.assertEqual(c.COUNTS['recovery'], calls)
        self.assertEqual((c.DIRECTORY / 'opencode.json').read_bytes(), config)

    def test_pending_form_cancellations_reserve_other_list_and_three_final_checks(self):
        c = self.controller
        self.unload_at = 0
        transport = self.transport

        def pending(argv, **options):
            if argv[3] == '/api/session/owner/form':
                self.requests.append((argv[3], options['timeout'], self.now))
                return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'data': [
                    {'id': 'form-' + str(n)} for n in range(8)]}))
            if argv[2] == 'delete':
                self.requests.append((argv[3], options['timeout'], self.now))
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            return transport(argv, **options)

        with patch.object(c.subprocess, 'run', pending):
            result = c.teardown(['owner', 'foreign'], self.before)
        self.assertFalse(result['passed'])  # Not all pending forms could be cancelled.
        self.assertTrue(result['inventory_subject_absent'])
        self.assertTrue(result['rpc_unavailable'])
        self.assertTrue(result['shared_process_unchanged'])
        self.assertEqual(c.COUNTS['recovery'], 10)

    def test_disposal_evidence_from_another_generation_cannot_pass(self):
        c = self.controller
        transport = self.transport

        def wrong_generation(argv, **options):
            response = transport(argv, **options)
            (c.ROOT / 'events.jsonl').write_text(json.dumps({'kind': 'closed', 'generation': 'foreign'}) + '\n')
            return response

        with patch.object(c.subprocess, 'run', wrong_generation):
            result = c.teardown([], self.before)
        self.assertFalse(result['passed'])
        self.assertFalse(result['disposal_completed'])


if __name__ == '__main__':
    unittest.main()
