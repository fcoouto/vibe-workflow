"""Shared heavy-phase admission; fixtures never create host pressure."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import tempfile
from pathlib import Path
import unittest

from runtime import state


class ResourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / 'host'
        self.api = importlib.import_module('runtime.resource_admission')

    def request(self, identity='one', **updates):
        return dict(id=identity, owner=dict(task_id=identity), cpu=1, memory_bytes=10,
                    capacity_cpu=2, capacity_memory_bytes=100, deadline=1000, ready=True) | updates

    def call(self, action, request, now=100, probe=None):
        return self.api.control(self.directory, action, request, now=now,
            probe=probe or (lambda: dict(sampled_at=now, available_memory_bytes=1000, pressure=False)))

    def release(self, request):
        return self.call('release', dict(id=request['id'], owner=request['owner'],
            stopped_proof=dict(path='/trusted/proof', sha256='a' * 64)))

    def test_status_does_not_create_state(self):
        self.assertEqual(self.call('status', {})['reservations'], {})
        self.assertFalse(self.directory.exists())

    def test_admission_is_idempotent_but_not_a_second_launch(self):
        request = self.request()
        first = self.call('request', request)
        self.assertTrue(first['newly_admitted'])
        second = self.call('request', request)
        self.assertTrue(second['admitted'])
        self.assertFalse(second['newly_admitted'])
        with self.assertRaises(state.StateError):
            self.call('request', request | dict(cpu=2))
        self.assertEqual(self.release(request)['state'], 'released')
        self.assertFalse(self.call('request', request)['admitted'])

    def test_simultaneous_parents_cannot_overreserve(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: self.call('request', self.request(str(n))), range(8)))
        self.assertEqual(sum(r['admitted'] for r in results), 2)

    def test_old_ready_heavy_job_prevents_repeated_small_overtake(self):
        one = self.request('one'); self.call('request', one)
        old = self.request('old', cpu=2)
        self.assertEqual(self.call('request', old)['reason'], 'occupied-reservations')
        self.assertEqual(self.call('request', self.request('new'))['reason'], 'older-request')
        self.release(one)
        self.assertTrue(self.call('request', old)['admitted'])

    def test_dependency_blocked_request_does_not_hold_capacity(self):
        old = self.request('old', ready=False)
        self.assertEqual(self.call('request', old)['reason'], 'dependencies')
        self.assertTrue(self.call('request', self.request('new'))['admitted'])
        self.assertEqual(self.call('request', old | dict(ready=True))['state'], 'active')

    def test_telemetry_failure_permits_but_reservations_still_bind(self):
        def broken(): raise OSError('unavailable')
        first = self.call('request', self.request(), probe=broken)
        self.assertTrue(first['admitted']); self.assertIsNotNone(first['warning'])
        other = self.call('request', self.request('two', cpu=2), probe=broken)
        self.assertFalse(other['admitted']); self.assertEqual(other['reason'], 'occupied-reservations')

    def test_pressure_wait_and_timer_recheck_without_workflow_event(self):
        request = self.request()
        pressure = lambda: dict(sampled_at=100, available_memory_bytes=0, pressure=True)
        waiting = self.call('request', request, probe=pressure)
        self.assertEqual(waiting['reason'], 'host-pressure'); self.assertEqual(waiting['next_recheck'], 105)
        self.assertTrue(self.call('request', request, now=106)['admitted'])

    def test_pressure_wait_recovers_when_measurements_fail(self):
        base = self.directory
        for index, bad in enumerate([None, {}, dict(sampled_at=0, available_memory_bytes=0, pressure=True)]):
            with self.subTest(bad=bad):
                self.directory = base / str(index)
                request = self.request()
                pressure = lambda: dict(sampled_at=100, available_memory_bytes=0, pressure=True)
                self.call('request', request, probe=pressure)
                result = self.call('request', request, now=106, probe=lambda: bad)
                self.assertTrue(result['admitted'])
                self.assertIsNotNone(result['warning'])

    def test_cached_measurement_avoids_duplicate_collection(self):
        calls = []
        def probe():
            calls.append(1)
            return dict(sampled_at=100, available_memory_bytes=1000, pressure=False)
        self.call('request', self.request(), probe=probe)
        self.call('request', self.request('two'), probe=probe)
        self.assertEqual(len(calls), 1)

    def test_expiry_never_releases_running_owner(self):
        request = self.request(deadline=101, cpu=2)
        self.call('request', request)
        other = self.call('request', self.request('two'), now=102)
        self.assertFalse(other['admitted'])
        with self.assertRaises(state.StateError):
            self.call('release', dict(id='one', owner=dict(task_id='foreign'),
                stopped_proof=dict(path='/proof', sha256='a' * 64)))

    def test_cancel_waiting_needs_no_execution_proof(self):
        request = self.request(ready=False)
        self.call('request', request)
        self.assertEqual(self.call('cancel', dict(id='one', owner=request['owner']))['state'], 'cancelled')

    def test_corruption_and_symlink_are_not_telemetry_failures(self):
        self.call('request', self.request())
        path = self.directory/'admission.json'
        path.write_text('{}')
        with self.assertRaises(state.StateError): self.call('request', self.request('two'))
        path.unlink(); path.symlink_to(self.directory/'other')
        with self.assertRaises(state.StateError): self.call('request', self.request('two'))

    def test_cached_sample_processing_failure_permits_admission(self):
        self.call('request', self.request())
        path = self.directory/'admission.json'
        saved = json.loads(path.read_text())
        saved['telemetry']['sample'] = 'malformed'
        path.write_text(json.dumps(saved))
        result = self.call('request', self.request('two'), now=101)
        self.assertTrue(result['admitted'])
        self.assertIsNotNone(result['warning'])

    def test_approved_unstarted_memory_is_counted_against_observed_headroom(self):
        first = self.request(memory_bytes=60)
        self.call('request', first)
        observed = lambda: dict(sampled_at=106, available_memory_bytes=70, pressure=False)
        second = self.call('request', self.request('two', memory_bytes=30), now=106, probe=observed)
        self.assertEqual(second['reason'], 'host-pressure')

    def test_expired_terminal_rows_archive_but_live_rows_retain_ownership(self):
        request = self.request(deadline=101)
        self.call('request', request); self.release(request)
        live = self.request('live', deadline=101)
        self.call('request', live)
        self.call('request', self.request('later'), now=102)
        status = self.call('status', {}, now=102)
        self.assertNotIn('one', status['reservations'])
        self.assertEqual(status['reservations']['live']['state'], 'active')
        histories = list(self.directory.glob('history-*.json'))
        self.assertEqual(len(histories), 1)
        self.assertEqual(json.loads(histories[0].read_text())['reservation']['request']['id'], 'one')
        with self.assertRaises(state.StateError): self.call('request', request, now=102)

    def test_active_cancel_and_release_require_stopped_proof(self):
        request = self.request(); self.call('request', request)
        for action in ('cancel', 'release'):
            with self.subTest(action=action), self.assertRaises(state.StateError):
                self.call(action, dict(id='one', owner=request['owner']))
        self.assertEqual(self.call('status', {})['reservations']['one']['state'], 'active')

    def test_fifty_contexts_have_no_row_count_ceiling(self):
        for index in range(50):
            result = self.call('request', self.request(str(index), capacity_cpu=50,
                capacity_memory_bytes=10000))
            self.assertTrue(result['admitted'])
        self.assertEqual(len(self.call('status', {})['reservations']), 50)

    def test_serial_expired_owners_do_not_fill_live_registry(self):
        for index in range(110):
            now = 100 + index * 2
            request = self.request(str(index), deadline=now + 1)
            self.call('request', request, now=now)
            self.call('release', dict(id=request['id'], owner=request['owner'],
                stopped_proof=dict(path='/trusted/proof', sha256='a' * 64)), now=now)
        self.assertEqual(len(self.call('status', {}, now=400)['reservations']), 1)
        self.assertEqual(len(list(self.directory.glob('history-*.json'))), 109)

    def test_pressure_cache_key_preserves_veto_within_ttl(self):
        request = self.request()
        pressure = lambda: dict(sampled_at=100, available_memory_bytes=0, pressure=True)
        first = self.api.control(self.directory, 'request', request, now=100, probe=pressure, probe_key='disk-a')
        self.assertEqual(first['reason'], 'host-pressure')
        second = self.api.control(self.directory, 'request', request, now=101,
            probe=lambda: self.fail('cached probe must be reused'), probe_key='disk-a')
        self.assertEqual(second['reason'], 'host-pressure')
        self.assertFalse(second['admitted']); self.assertIsNone(second['warning'])

    def test_known_ineligible_older_waiter_is_skipped_but_active_owner_remains(self):
        one = self.request('one'); self.call('request', one)
        old = self.request('old', cpu=2); self.call('request', old)
        new = self.request('new')
        calls = []
        def eligible(owner):
            calls.append(owner['task_id'])
            return owner['task_id'] != 'old'
        result = self.api.control(self.directory, 'request', new, now=100,
            probe=lambda: dict(sampled_at=100, available_memory_bytes=1000, pressure=False), eligible=eligible)
        self.assertTrue(result['admitted'])
        rows = self.call('status', {})['reservations']
        self.assertFalse(rows['old']['request']['ready'])
        self.assertEqual(rows['one']['state'], 'active')
        self.assertEqual(calls, ['old'])

    def test_unknown_older_owner_retains_queue_guard(self):
        self.call('request', self.request('one'))
        self.call('request', self.request('old', cpu=2))
        def unknown(owner): raise OSError('authority unreadable')
        result = self.api.control(self.directory, 'request', self.request('new'), now=100, eligible=unknown)
        self.assertFalse(result['admitted']); self.assertEqual(result['reason'], 'older-request')

    def test_status_retains_pressure_wait_reason_and_recheck(self):
        pressure = lambda: dict(sampled_at=100, available_memory_bytes=0, pressure=True)
        self.call('request', self.request(), probe=pressure)
        row = self.call('status', {})['reservations']['one']
        self.assertEqual(row['last_status']['reason'], 'host-pressure')
        self.assertEqual(row['last_status']['next_recheck'], 105)
        self.assertIsNone(row['last_status']['warning'])

    def test_serial_expired_unlaunched_waiters_archive_without_releasing_active(self):
        pressure = lambda: dict(sampled_at=100, available_memory_bytes=0, pressure=True)
        active = self.request('active', deadline=10000)
        self.call('request', active)
        for index in range(240):
            now = 100 + index * 2
            request = self.request('wait-' + str(index), deadline=now + 1, cpu=2)
            waiting = self.call('request', request, now=now, probe=pressure)
            self.assertFalse(waiting['admitted'])
        rows = self.call('status', {}, now=600)['reservations']
        self.assertEqual(rows['active']['state'], 'active')
        self.assertEqual(len(rows), 2)
        histories = list(self.directory.glob('history-*.json'))
        self.assertEqual(len(histories), 239)
        retired = json.loads(histories[0].read_text())['reservation']
        self.assertEqual(retired['state'], 'cancelled')
        self.assertEqual(retired['last_status']['reason'], 'deadline')
        self.assertIsNone(retired['proof'])
