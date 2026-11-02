"""Bounded outputs and operation-local reuse; no real authority or native calls."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import parent_recovery, workstreams
from tests import test_parent_recovery, test_workstreams


class CompactOutputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_parent_recovery.ParentRecoveryTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        for n in range(50):
            parent = self.fixture.parent
            self.fixture.register(parent | dict(thread_id=str(n),
                identity=parent['identity'] | dict(session_id=str(n))))

    def test_mutation_ack_does_not_repeat_other_parents_or_change_storage(self):
        raw = self.fixture.call('withdraw', thread_id='0')
        before = copy.deepcopy(raw)
        result = parent_recovery.compact_result(raw, 'withdraw', '0')
        self.assertNotIn('parents', result)
        self.assertFalse(result['parent']['eligible'])
        self.assertEqual(result['parent']['thread_id'], '0')
        self.assertEqual(result['wanted'], 'present')
        self.assertLess(len(json.dumps(result)), 500)
        self.assertEqual(raw, before)

    def test_status_preserves_membership_and_pending_delivery_without_private_history(self):
        raw = self.fixture.call('status')
        raw['parents']['0']['intent'] = dict(token='private-token', turn_id='turn', result='uncertain')
        result = parent_recovery.compact_result(raw, 'status')
        self.assertEqual(set(result['parents']), set(raw['parents']))
        self.assertTrue(result['parents']['0']['pending_delivery'])
        self.assertNotIn('private-token', json.dumps(result))
        self.assertNotIn('pins', json.dumps(result))
        self.assertLess(len(json.dumps(result)), len(json.dumps(raw)) // 2)

    def test_compact_status_can_select_only_the_disabled_checkout(self):
        self.fixture.call('register', parent=self.fixture.parent | dict(thread_id='other',
            cwd='/other-checkout', identity=self.fixture.parent['identity'] | dict(session_id='other')))
        result = parent_recovery.compact_result(self.fixture.call('status'), 'status')
        selected = {identity for identity, row in result['parents'].items() if row['cwd'] == '/fixture'}
        self.assertEqual(selected, {str(n) for n in range(50)})
        self.assertEqual(result['parents']['other']['cwd'], '/other-checkout')

    def test_send_and_schedule_authority_survive_projection(self):
        raw = self.fixture.call('status') | dict(send=True, token='send-token', thread_id='0',
            prompt='EXACT SEND', deadline=4000, pending=dict(operation='delete', token='lifecycle-token'))
        result = parent_recovery.compact_result(raw, 'observe', '0')
        for key in ('send', 'token', 'thread_id', 'prompt', 'deadline', 'pending', 'wanted', 'schedule_id', 'suspended'):
            self.assertEqual(result[key], raw[key])

    def test_cli_defaults_compact_with_explicit_full_diagnostics(self):
        from scripts import workstreams_recovery as cli
        raw = self.fixture.call('status')
        for option in ([], ['--full']):
            output = io.StringIO()
            with patch('sys.argv', ['workstreams_recovery.py', 'status', *option]), \
                 patch('sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'{}'))), \
                 patch.object(parent_recovery, 'control', return_value=raw), redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual('parent' in result['parents']['0'], bool(option))

    def test_delivery_ack_omits_prompt_but_send_response_keeps_it(self):
        fixture = test_workstreams.WorkstreamsTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        prepared = fixture.prepare()
        fixture.observe(prepared)
        continuation = fixture.continuation(prepared)
        intent = workstreams.prepare_continuation(fixture.scope, continuation)
        request = fixture.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=fixture.control('status')['revision'], token=intent['token'])
        sent = workstreams.consume_continuation(fixture.scope, request)
        self.assertEqual(sent['prompt'], continuation['prompt'])
        request.pop('workstream_revision')
        request['expected_revision'] = sent['revision']
        ack = workstreams.record_continuation(fixture.scope, request | dict(delivery='sent', delivery_ref='fixture:sent'))
        self.assertNotIn('prompt', ack)
        self.assertEqual(ack['token'], intent['token'])
        self.assertEqual(ack['delivery'], 'sent')


class ValidationReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_workstreams.WorkstreamsTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.prepared = self.fixture.prepare()

    def test_resource_run_keeps_three_fresh_checks_with_one_ledger_walk_each(self):
        import sys
        import tempfile
        from runtime import delegation, resource_execution
        request = self.fixture.fixture.request(attempt_id=self.prepared['attempt_id'],
            workstream_revision=self.fixture.control('status')['revision'], phase_id='reuse',
            cpu=1, memory_bytes=1024, capacity_cpu=2, capacity_memory_bytes=104857600,
            argv=[sys.executable, '-c', 'pass'], wait_seconds=0)
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(delegation, '_ledger', wraps=delegation._ledger) as ledger, \
             patch.object(resource_execution, '_authority', wraps=resource_execution._authority) as authority:
            result = resource_execution.operate(self.fixture.scope, Path(temp).resolve() / 'host', 'run', request)
        self.assertEqual(result['returncode'], 0)
        self.assertEqual(authority.call_count, 3)
        self.assertEqual(ledger.call_count, authority.call_count)

    def test_continuation_boundaries_each_load_their_own_validated_ledger(self):
        from runtime import delegation
        self.fixture.observe(self.prepared)
        continuation = self.fixture.continuation(self.prepared)
        with patch.object(delegation, '_ledger', wraps=delegation._ledger) as ledger:
            intent = workstreams.prepare_continuation(self.fixture.scope, continuation)
        self.assertEqual(ledger.call_count, 1)
        request = self.fixture.fixture.request(attempt_id=self.prepared['attempt_id'],
            workstream_revision=self.fixture.control('status')['revision'], token=intent['token'])
        with patch.object(delegation, '_ledger', wraps=delegation._ledger) as ledger:
            self.assertTrue(workstreams.consume_continuation(self.fixture.scope, request)['send_admitted'])
        self.assertEqual(ledger.call_count, 1)

    def test_mode_change_at_launch_boundary_still_prevents_execution(self):
        import sys
        import tempfile
        from runtime import resource_execution, state
        fixture = self.fixture
        marker = fixture.fixture.root / 'should-not-launch'
        request = fixture.fixture.request(attempt_id=self.prepared['attempt_id'],
            workstream_revision=fixture.control('status')['revision'], phase_id='fresh-boundary',
            cpu=1, memory_bytes=1, capacity_cpu=2, capacity_memory_bytes=104857600,
            argv=[sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()'])
        original = resource_execution._authority
        calls = 0
        def changed(scope, payload, **options):
            nonlocal calls
            calls += 1
            if calls == 3:
                fixture.control('disable', fixture.control('status')['revision'])
            return original(scope, payload, **options)
        with tempfile.TemporaryDirectory() as temp, patch.object(resource_execution, '_authority', side_effect=changed):
            with self.assertRaises(state.StateError):
                resource_execution.operate(fixture.scope, Path(temp).resolve() / 'host', 'run', request)
        self.assertEqual(calls, 3)
        self.assertFalse(marker.exists())

    def test_missing_authority_retains_the_existing_sanitized_failure(self):
        from dataclasses import replace
        from runtime import resource_execution, state
        scope = replace(self.fixture.scope, runtime=replace(self.fixture.scope.runtime,
            state_dir=self.fixture.fixture.root / 'missing-authority'))
        request = self.fixture.fixture.request(attempt_id=self.prepared['attempt_id'])
        with self.assertRaisesRegex(state.StateError, 'accepted projection'):
            resource_execution._authority(scope, request)


class DocumentedRequestTests(unittest.TestCase):
    def test_plan_and_prepare_examples_execute_against_runtime_validators(self):
        import re
        from runtime import delegation, state
        from tests import test_delegation
        fixture = test_delegation.AdmissionTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        status = delegation.guidance_status(fixture.scope, fixture.cache_request)
        replacements = dict(zip(
            ['<SESSION>', '<STATE_DIR>', '<DEADLINE>', '<MODEL>', '<PROFILE_ID>',
             '<NATIVE_VERSION>', '<CAPABILITY_DIGEST>', '<PROVIDER>', '<GUIDANCE_NAME>',
             '<GUIDANCE_DIGEST>', '<GUIDANCE_TIME>', '<GUIDANCE_FRESHNESS>'],
            [fixture.identity['session_id'], str(fixture.scope.runtime.state_dir), int(delegation._now()) + 600,
             fixture.catalog['catalog'][0]['model'], 'bounded', fixture.catalog['native_version'],
             fixture.catalog['capability_digest'], fixture.catalog['parent_model_provider'],
             status['snapshot_ref']['name'], status['snapshot_digest'], status['checked_at'], status['freshness']]))
        def resolve(value):
            if isinstance(value, dict):return {k:resolve(v) for k,v in value.items()}
            if isinstance(value, list):return [resolve(v) for v in value]
            if isinstance(value, str):return replacements.get(value, value.replace('<CHECKOUT>', str(fixture.repo)))
            return value
        text = (Path(__file__).resolve().parents[1] / 'clients/delegation-requests.md').read_text()
        def example(name):
            block = re.search(r'<!-- example:' + name + r' -->\s*```json\n(.*?)\n```', text, re.S)
            self.assertIsNotNone(block)
            return resolve(json.loads(block.group(1)))
        planned = delegation.plan_delegation(fixture.scope, fixture.request(**example('plan')))
        self.assertTrue(planned['ok'])
        payload = example('prepare')
        prepared = delegation.prepare_assignment(fixture.scope, fixture.request(**payload))
        self.assertTrue(prepared['launch_admitted'])
        self.assertEqual(prepared['requested_model'], replacements['<MODEL>'])
        with self.assertRaises(state.StateError):
            delegation.prepare_assignment(fixture.scope, fixture.request(**payload, extra_authority=True))
