"""Bounded outputs and operation-local reuse; no real authority or native calls. Remaining efficiency boundaries over disposable authorities; no native execution."""
import copy
import io
import json
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from runtime import evidence, parent_recovery, resource_admission as host, resource_execution, state, workstreams
from tests import test_parent_recovery, test_resource_execution, test_workstreams


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


class ResourceEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.first = self.execution()

    def execution(self):
        fixture = test_resource_execution.ResourceExecutionTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(shutil.rmtree, fixture.directory.parent)
        return fixture

    @contextmanager
    def telemetry(self, probe=None):
        clock = [int(time.time())]
        control = host.control

        def current(directory, action, request, **options):
            return control(directory, action, request, now=clock[0], **options)

        def sample():
            return dict(sampled_at=clock[0], available_memory_bytes=1024 * 1024 * 1024, pressure=False)

        with patch.object(host, 'control', side_effect=current), \
             patch.object(host, 'measure', side_effect=probe or sample) as measured:
            yield clock, measured

    def test_immediate_run_fingerprints_once_at_each_fresh_authority_boundary(self):
        """Three fresh stages each retain one entry and one post-ledger capture."""
        stages, unassigned = [], []
        current, phase = [None], [None]
        actual_authority = resource_execution._authority
        actual_fingerprint = state.runtime_fingerprint

        def observe_authority(*args, **kwargs):
            events = []
            stages.append(events)
            current[0] = events
            try:
                result = actual_authority(*args, **kwargs)
                events.append('return')
                return result
            finally:
                current[0] = None

        def observe_phase(name, original):
            def observe(*args, **kwargs):
                events = current[0] if current[0] is not None else unassigned
                events.append(name + ':begin')
                previous, phase[0] = phase[0], name
                try:
                    return original(*args, **kwargs)
                finally:
                    events.append(name + ':end')
                    phase[0] = previous
            return observe

        def observe_fingerprint(*args, **kwargs):
            result = actual_fingerprint(*args, **kwargs)
            events = current[0] if current[0] is not None else unassigned
            events.append('hash:' + str(phase[0]))
            return result

        with self.telemetry(), \
             patch.object(state, 'runtime_fingerprint', side_effect=observe_fingerprint) as fingerprint, \
             patch.object(resource_execution, '_authority', side_effect=observe_authority) as authority, \
             patch.object(state, '_check_current_authority', side_effect=observe_phase('entry', state._check_current_authority)), \
             patch.object(resource_execution.d6, '_ledger', side_effect=observe_phase('ledger', resource_execution.d6._ledger)), \
             patch.object(state, '_compatible', side_effect=observe_phase('compat', state._compatible)):
            result = self.first.call()
        self.assertEqual(result['returncode'], 0)
        self.assertEqual(authority.call_count, 3)
        self.assertEqual(stages, [['entry:begin', 'hash:entry', 'entry:end', 'ledger:begin', 'ledger:end',
            'compat:begin', 'hash:compat', 'compat:end', 'return']] * 3)
        self.assertEqual(unassigned, [])
        self.assertEqual(fingerprint.call_count, sum(event.startswith('hash:') for stage in stages for event in stage))

    @contextmanager
    def copied_installed_sources(self):
        temporary = tempfile.TemporaryDirectory(prefix='vcw-resource-package-')
        self.addCleanup(temporary.cleanup)
        package = Path(temporary.name).resolve() / 'package'
        installed = state.PACKAGE.resolve()
        original = installed / 'runtime/engine.py'
        original_bytes = original.read_bytes()
        self.assertFalse(package.is_relative_to(installed))
        shutil.copytree(installed, package, ignore=shutil.ignore_patterns('.git', '.superpowers', '__pycache__'))
        try:
            with patch.object(state, 'PACKAGE', package):
                self.assertEqual(state.runtime_fingerprint(), self.first.fixture.scope.runtime_digest)
                yield package / 'runtime/engine.py'
        finally:
            self.assertEqual(original.read_bytes(), original_bytes)

    def authority_snapshot(self):
        scope = self.first.fixture.scope
        with state._database(scope) as db:
            rows = list(db.iterdump())
            baseline = state._source_baseline(scope, db)
        files = {}
        for path in [scope.authority_path, *scope.runtime.state_dir.rglob('*')]:
            if path.is_file():
                info = path.stat()
                files[str(path)] = (path.read_bytes(), info.st_dev, info.st_ino, info.st_mode,
                                   info.st_mtime_ns, info.st_ctime_ns)
        return dict(rows=rows, baseline=baseline, files=files)

    def test_changed_installed_sources_reject_before_ledger_admission_and_launch(self):
        before = self.authority_snapshot()
        with self.copied_installed_sources() as source:
            source.write_bytes(source.read_bytes() + b'\n# Disposable early source drift.\n')
            with self.telemetry(), \
                    patch.object(resource_execution.d6, '_ledger', wraps=resource_execution.d6._ledger) as ledger, \
                    patch.object(host, 'control', wraps=host.control) as control, \
                    patch.object(resource_execution.subprocess, 'Popen', wraps=resource_execution.subprocess.Popen) as launch:
                with self.assertRaisesRegex(state.StateError, 'Installed runtime differs'):
                    self.first.call()
            self.assertEqual(ledger.call_count, 0)
            self.assertEqual(control.call_count, 0)
            self.assertEqual(launch.call_count, 0)
        self.assertFalse(self.first.directory.exists())
        self.assertEqual(self.authority_snapshot(), before)

    def test_installed_source_drift_after_prelaunch_ledger_retains_active_reservation(self):
        before = self.authority_snapshot()
        actual_authority = resource_execution._authority
        actual_ledger = resource_execution.d6._ledger
        stages, reads, admitted = [], [], []
        current = [None]
        with self.copied_installed_sources() as source:
            def observe_authority(*args, **kwargs):
                stages.append(len(stages) + 1)
                current[0] = stages[-1]
                try:
                    return actual_authority(*args, **kwargs)
                finally:
                    current[0] = None

            def mutate_after_actual_ledger(*args, **kwargs):
                records = actual_ledger(*args, **kwargs)
                reads.append(current[0])
                if current[0] == 3:
                    admitted.append(host.control(self.first.directory, 'status', {})['reservations'])
                    self.assertEqual(len(admitted[0]), 1)
                    reservation = next(iter(admitted[0].values()))
                    self.assertEqual(reservation['state'], 'active')
                    self.assertEqual(reservation['request']['owner']['attempt_id'], self.first.prepared['attempt_id'])
                    source.write_bytes(source.read_bytes() + b'\n# Disposable post-ledger source drift.\n')
                return records

            with self.telemetry(), \
                    patch.object(resource_execution, '_authority', side_effect=observe_authority), \
                    patch.object(resource_execution.d6, '_ledger', side_effect=mutate_after_actual_ledger), \
                    patch.object(host, 'control', wraps=host.control) as control, \
                    patch.object(resource_execution.subprocess, 'Popen', wraps=resource_execution.subprocess.Popen) as launch:
                with self.assertRaisesRegex(state.StateError, 'Installed runtime differs'):
                    self.first.call()
            self.assertEqual(stages, [1, 2, 3])
            self.assertEqual(reads, [1, 2, 3])
            self.assertEqual([call.args[1] for call in control.call_args_list], ['request', 'status'])
            self.assertEqual(launch.call_count, 0)
        self.assertEqual(host.control(self.first.directory, 'status', {})['reservations'], admitted[0])
        self.assertEqual(self.authority_snapshot(), before)

    def test_run_uses_locked_request_without_an_unused_status_read(self):
        with self.telemetry(), patch.object(host, 'control', wraps=host.control) as control:
            result = self.first.call()
        self.assertEqual(result['returncode'], 0)
        self.assertEqual([call.args[1] for call in control.call_args_list], ['request', 'complete'])

    def test_run_still_rejects_corrupt_registry_before_launch(self):
        self.first.directory.mkdir(mode=0o700)
        registry = self.first.directory / 'admission.json'
        registry.write_text('{}')
        registry.chmod(0o600)
        marker = self.first.fixture.fixture.repo / 'must-not-launch'
        with self.assertRaises(state.StateError):
            self.first.call(argv=[self.first.request['argv'][0], '-c',
                'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()'])
        self.assertFalse(marker.exists())

    def test_memory_cache_spans_projects_and_expires_at_the_existing_boundary(self):
        second = self.execution()
        second.directory = self.first.directory
        with self.telemetry() as (clock, measured):
            for fixture, phase, seconds, expected in [
                (self.first, 'a-one', 0, 1), (second, 'b-one', 4, 1), (self.first, 'a-two', 1, 2)
            ]:
                with self.subTest(phase=phase):
                    clock[0] += seconds
                    result = fixture.call(phase_id=phase, capacity_cpu=8)
                    self.assertEqual(result['returncode'], 0)
                    self.assertEqual(measured.call_count, expected)

    def test_shared_memory_pressure_still_blocks_another_project(self):
        second = self.execution()
        second.directory = self.first.directory
        sample = dict(sampled_at=int(time.time()), available_memory_bytes=0, pressure=True)
        with self.telemetry(lambda: sample) as (_, measured):
            self.assertEqual(self.first.call()['reason'], 'host-pressure')
            self.assertEqual(self.first.call('cancel')['state'], 'cancelled')
            result = second.call()
        self.assertEqual(result['reason'], 'host-pressure')
        self.assertFalse(result['admitted'])
        self.assertEqual(measured.call_count, 1)

    def test_shared_telemetry_failure_retains_reservation_capacity(self):
        second = self.execution()
        second.directory = self.first.directory
        with self.telemetry(lambda: (_ for _ in ()).throw(OSError('unavailable'))) as (_, measured):
            first = self.first.call(background=True)
            other = second.call(background=True)
            full = self.first.call(phase_id='no-capacity')
        self.assertEqual(first['returncode'], 0)
        self.assertIsNotNone(first['warning'])
        self.assertEqual(other['returncode'], 0)
        self.assertIsNotNone(other['warning'])
        self.assertEqual(full['reason'], 'occupied-reservations')
        self.assertEqual(measured.call_count, 1)

    def test_memory_cache_cannot_bypass_checkout_and_demand_specific_disk_checks(self):
        second = self.execution()
        second.directory = self.first.directory

        def disk_usage(path):
            return SimpleNamespace(free=1 if path == self.first.fixture.scope.repository_root else 0)

        with self.telemetry() as (_, measured), \
             patch.object(resource_execution.shutil, 'disk_usage', side_effect=disk_usage) as disk:
            self.assertEqual(self.first.call(phase_id='memory', capacity_cpu=8)['returncode'], 0)
            self.assertEqual(self.first.call(phase_id='disk-a', disk_bytes=1, capacity_cpu=8)['returncode'], 0)
            self.assertEqual(second.call(phase_id='disk-b', disk_bytes=1, capacity_cpu=8)['reason'], 'host-pressure')
            second.call('cancel', phase_id='disk-b', disk_bytes=1, capacity_cpu=8)
            demand = self.first.call(phase_id='disk-demand', disk_bytes=2, capacity_cpu=8)
            self.assertEqual(demand['reason'], 'host-pressure')
            self.assertEqual(self.first.call(phase_id='disk-demand', disk_bytes=2, capacity_cpu=8)['reason'], 'host-pressure')
        self.assertEqual(measured.call_count, 1)
        self.assertEqual(disk.call_count, 4)

    def test_disable_during_wait_is_revalidated_before_a_new_admission(self):
        sample = dict(sampled_at=int(time.time()), available_memory_bytes=0, pressure=True)
        marker = self.first.fixture.fixture.repo / 'must-not-launch'

        def disable(_):
            fixture = self.first.fixture
            fixture.control('disable', fixture.control('status')['revision'])

        with self.telemetry(lambda: sample), patch.object(resource_execution.time, 'sleep', side_effect=disable):
            with self.assertRaises(state.StateError):
                self.first.call(wait_seconds=1, argv=[self.first.request['argv'][0], '-c',
                    'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()'])
        self.assertFalse(marker.exists())
        row = next(iter(host.control(self.first.directory, 'status', {})['reservations'].values()))
        self.assertEqual(row['state'], 'waiting')

    def test_runtime_pin_change_at_launch_retains_the_active_reservation(self):
        authority = resource_execution._authority
        calls = 0
        marker = self.first.fixture.fixture.repo / 'must-not-launch'

        def changed(scope, request, **options):
            nonlocal calls
            calls += 1
            if calls == 3:
                with patch.object(state, 'runtime_fingerprint', return_value='0' * 64):
                    return authority(scope, request, **options)
            return authority(scope, request, **options)

        with self.telemetry(), patch.object(resource_execution, '_authority', side_effect=changed):
            with self.assertRaises(state.StateError):
                self.first.call(argv=[self.first.request['argv'][0], '-c',
                    'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()'])
        self.assertEqual(calls, 3)
        self.assertFalse(marker.exists())
        row = next(iter(host.control(self.first.directory, 'status', {})['reservations'].values()))
        self.assertEqual(row['state'], 'active')

    def test_reconciled_cleanup_survives_disable_and_a_closed_task(self):
        self.first.call()
        fixture = self.first.fixture
        fixture.observe(self.first.prepared)
        proof = fixture.fixture.worker_reconciliation_request(self.first.prepared, outcome='failed')
        fixture.fixture.api.reconcile_assignment(fixture.scope, proof)
        fixture.control('disable', fixture.control('status')['revision'])
        with state._database(fixture.scope, write=True) as db:
            identity = fixture.fixture.identity
            task = state._task(db, state._scope_id(fixture.scope), identity['client'], identity['session_id'], identity['task_id'])
            task['state'] = 'CANCELLED'
            state._save(db, state._scope_id(fixture.scope), task)
        self.assertEqual(self.first.call('release')['state'], 'released')


class ContinuationProjectionTests(unittest.TestCase):
    def test_only_consumption_returns_the_exact_large_stored_prompt(self):
        fixture = test_workstreams.WorkstreamsTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        prepared = fixture.prepare()
        fixture.observe(prepared)
        prompt = 'Fix café and 漢字; preserve 🐍 and e\u0301. ' * 600
        intent = workstreams.prepare_continuation(fixture.scope, fixture.continuation(prepared, prompt=prompt))
        self.assertNotIn('prompt', intent)
        self.assertFalse(intent['send_admitted'])
        self.assertEqual(intent['remaining_continuations'], 2)
        reference = fixture.fixture.raw_assignment(prepared['attempt_id'])['workstream_continuation']
        stored = evidence._read(fixture.scope, reference)
        self.assertEqual(stored['prompt'], prompt)
        request = fixture.fixture.request(attempt_id=prepared['attempt_id'],
            workstream_revision=fixture.control('status')['revision'], token=intent['token'])
        consumed = workstreams.consume_continuation(fixture.scope, request)
        self.assertTrue(consumed['send_admitted'])
        self.assertEqual(consumed['prompt'], prompt)
        self.assertEqual(consumed['worker_id'], intent['worker_id'])
        self.assertEqual(consumed['token'], intent['token'])
        delivered = workstreams.record_continuation(fixture.scope, fixture.fixture.request(
            attempt_id=prepared['attempt_id'], token=intent['token'], delivery='sent', delivery_ref='fixture:exact'))
        self.assertNotIn('prompt', delivered)
        self.assertEqual(delivered['delivery'], 'sent')
        self.assertEqual(evidence._read(fixture.scope, reference), stored)
        current = fixture.fixture.raw_assignment(prepared['attempt_id'])['workstream_continuation']
        self.assertEqual(evidence._read(fixture.scope, current)['prompt'], prompt)


if __name__ == '__main__':
    unittest.main()
