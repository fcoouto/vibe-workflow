"""Compact observations preserve private evidence and ownership semantics."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime import delegation

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/openrouter_workers.py'
spec = importlib.util.spec_from_file_location('compact_workers', SCRIPT)
workers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workers)


class CompactDelegationTests(unittest.TestCase):
    def test_omission_preserves_all_uncertainty_and_conflicts(self):
        rows = [dict(attempt_id=str(n), owner=dict(task_id='task'), role='worker',
                     status='uncertain', result=None,
                     observation=None, reconciliation=None, claims=['PRIVATE']) for n in range(21)]
        report = dict(ok=True, assignments=rows, conflicts=[{'private': 'PRIVATE'}],
                      blockers=['Unsettled'] * 20, limitations=['Observation only'])
        result = delegation._compact_delegation_status(report)
        self.assertEqual(result['assignment_count'], 21)
        self.assertEqual(result['unsettled_count'], 21)
        self.assertEqual(result['conflict_count'], 1)
        self.assertEqual(len(result['assignments']), 20)
        self.assertTrue(result['details_required'])
        self.assertTrue(result['observation_only'])
        self.assertEqual(result['blockers'], ['Unsettled'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['assignments'][0]['status'], 'uncertain')
        self.assertEqual(len(report['assignments']), 21)

    def test_completed_outcome_does_not_erase_ownership(self):
        report = dict(ok=True, assignments=[dict(attempt_id='a', owner={'task_id': 'old'}, role='review',
            status='uncertain', result={'outcome': 'completed'}, observation={'digest':'abc'}, reconciliation=None)],
            conflicts=[], blockers=['Unsettled'], limitations=[])
        result = delegation._compact_delegation_status(report)
        self.assertEqual(result['unsettled_count'], 1)
        self.assertEqual(result['assignments'][0]['status'], 'uncertain')
        self.assertEqual(result['assignments'][0]['task_id'], 'old')

    def test_populated_status_never_observes_or_verifies(self):
        from tests.test_delegation import AdmissionTests
        from runtime import evidence, state
        from runtime.adapters import codex
        fixture = AdmissionTests(methodName='runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.plan()
        prepared = fixture.prepare()
        before = state.task_status(fixture.scope, 'codex', fixture.identity['session_id'])
        held = fixture.raw_assignment(prepared['attempt_id'])
        artifacts = {p: p.read_bytes() for p in fixture.root.rglob('*.json')}
        with patch.object(evidence, 'verify_completion', side_effect=AssertionError('verification')), \
             patch.object(evidence, 'revalidate_ready', side_effect=AssertionError('readiness')), \
             patch.object(codex, 'observe_native_worker', side_effect=AssertionError('observation')), \
             patch.object(codex, 'discover_native_workers', side_effect=AssertionError('discovery')):
            summary = delegation.delegation_status(fixture.scope, 'codex', fixture.identity['session_id'], compact=True)
        row = next(r for r in summary['assignments'] if r['attempt_id'] == prepared['attempt_id'])
        self.assertEqual(row['status'], held['status'])
        self.assertEqual(summary['revision'], before['task']['revision'])
        self.assertGreater(summary['unsettled_count'], 0)
        self.assertEqual(fixture.raw_assignment(prepared['attempt_id']), held)
        with state._database(fixture.scope) as db:
            after = state._task(db, state._scope_id(fixture.scope), 'codex', fixture.identity['session_id'])
        self.assertEqual(after, before['task'])
        self.assertEqual({p: p.read_bytes() for p in fixture.root.rglob('*.json')}, artifacts)

    def test_missing_runtime_has_compact_view_without_creating_state(self):
        with patch.object(delegation.state, '_database') as database:
            database.return_value.__enter__.return_value = None
            result = delegation.delegation_status(None, 'codex', 'session', compact=True)
        self.assertEqual(result['assignment_count'], 0)
        self.assertTrue(result['blockers'])
        self.assertNotIn('task', result)


class CompactWorkerTests(unittest.TestCase):
    def test_status_preserves_bytes_and_failure_uncertainty(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "a"
            run.mkdir()
            result = dict(task_id='a', state='failed', response='PRIVATE_RESPONSE', tools=['PRIVATE_TOOL'],
                          failure={'category':'integration','reason':'PRIVATE_REASON'})
            raw = json.dumps(result).encode()
            (run / 'result.json').write_bytes(raw)
            with patch.object(workers.subprocess, 'Popen', side_effect=AssertionError('launch')):
                self.assertEqual(workers.status(run), result)
                compact = workers.status(run, compact=True)
            self.assertNotIn('PRIVATE', json.dumps(compact))
            self.assertIsNone(compact['local_execution_stopped'])
            self.assertTrue(compact['response_available'])
            self.assertEqual(compact['failure']['category'], 'integration')
            self.assertEqual(compact['result_ref']['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual((run / 'result.json').read_bytes(), raw)
            self.assertEqual(len(list(run.iterdir())), 1)
            result['state'] = 'unresolved'
            (run / 'result.json').write_text(json.dumps(result))
            self.assertNotEqual(workers.status(run, compact=True)['result_ref'], compact['result_ref'])

    def test_failure_projection_keeps_machine_facts_without_raw_provider_text(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / 'a'
            run.mkdir()
            failure = dict(category='budget', status_code=403, retry_after_seconds=2.5,
                           limit_source='openrouter_key_limit', reason='PRIVATE', message='PRIVATE')
            value = dict(task_id='a', state='failed', failure=failure)
            (run / 'result.json').write_text(json.dumps(value))
            result = workers.status(run, compact=True)['failure']
            self.assertEqual(result, {key: failure[key] for key in
                                     ('category', 'status_code', 'retry_after_seconds', 'limit_source')})
            value['failure'] = dict(category='PRIVATE', status_code=True,
                                    retry_after_seconds=float('nan'), limit_source='PRIVATE')
            (run / 'result.json').write_text(json.dumps(value))
            self.assertEqual(workers.status(run, compact=True)['failure'], {'category': 'unknown'})

    def test_malformed_results_never_look_healthy(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / "a"
            run.mkdir()
            with self.assertRaises(OSError): workers.status(run, compact=True)
            for value in ({}, [], {'task_id':'a','state':'unknown-state'},
                          {'task_id':'other','state':'running'},
                          {'task_id':'a','state':'running','local_execution_stopped':'true'},
                          {'task_id':'a','state':'running','native_fallback_ready':1},
                          {'task_id':'a','state':'running','response':[]},
                          {'task_id':'a','state':'running','failure':'failed'}):
                (run / 'result.json').write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaises(ValueError):
                    workers.status(run, compact=True)
            (run / 'result.json').write_text('{')
            with self.assertRaises(ValueError): workers.status(run, compact=True)

    def test_all_recorded_worker_states_preserve_explicit_stop_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / 'a'
            run.mkdir()
            for recorded in ('prepared', 'running', 'awaiting_verification', 'accepted',
                             'rejected', 'failed', 'unresolved'):
                for stopped in (False, True, None):
                    with self.subTest(state=recorded, stopped=stopped):
                        value = dict(task_id='a', state=recorded, response='',
                                     local_execution_stopped=stopped, native_fallback_ready=stopped)
                        raw = json.dumps(value).encode()
                        (run / 'result.json').write_bytes(raw)
                        result = workers.status(run, compact=True)
                        self.assertEqual(result['state'], recorded)
                        self.assertIs(result['local_execution_stopped'], stopped)
                        self.assertIs(result['native_fallback_ready'], stopped)
                        self.assertFalse(result['response_available'])
                        self.assertEqual((run / 'result.json').read_bytes(), raw)

    def test_missing_executable_restoration_reuses_existing_prerequisites(self):
        from tests.test_openrouter_workers import WorkerTests
        fixture = WorkerTests(methodName='runTest')
        WorkerTests.setUpClass()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        executable = fixture.root / 'worker'
        saved = fixture.root / 'worker.saved'
        executable.rename(saved)
        spawn = subprocess.Popen
        def guarded(argv, *args, **kwargs):
            self.assertNotEqual(str(argv[0]), str(executable))
            return spawn(argv, *args, **kwargs)
        with patch.object(workers.subprocess, 'Popen', side_effect=guarded):
            with self.assertRaises((ValueError, OSError)):
                fixture.api.prepare(fixture.runs, fixture.brief)
            saved.rename(executable)
            run = fixture.api.prepare(fixture.runs, fixture.brief)
        self.assertEqual(json.loads((run / 'result.json').read_text())['state'], 'prepared')

    def test_flag_cannot_mutate_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            for action in ('run', 'verify'):
                result = subprocess.run([sys.executable, '-B', str(SCRIPT), action, '--root', folder, '--compact'],
                                        input='{}', text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(Path(folder).iterdir()), [])

class CompactCliTests(unittest.TestCase):
    def test_compact_status_does_not_repair_titles_or_revalidate_completed_task(self):
        from tests.test_evidence import EvidenceTests
        from tests.test_title_sync_integration import NativeFixture
        from runtime import state, title_sync
        from scripts import workflow
        fixture = EvidenceTests('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.plan()
        fixture.checked()
        fixture.review()
        fixture.verify()
        task = state.task_status(fixture.scope, 'codex', 'parent')['task']
        self.assertEqual(task['state'], 'DONE')
        native = NativeFixture()
        request = {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')}
        request.update(expected_revision=task['revision'], reference='user:adopt', topic='Completed task')
        with patch.object(Path, 'home', return_value=fixture.root):
            title_sync.operate(fixture.scope, 'configure', request, transport=lambda: native)
        (fixture.repo / 'app.py').write_text('print("Changed after completion")\n')
        directory = fixture.scope.runtime.state_dir
        before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        title, writes = native.title, native.writes

        def status(compact):
            output = io.StringIO()
            arguments = ['workflow.py', 'delegation', 'status', '--cwd', str(fixture.repo),
                         '--client', 'codex', '--session', 'parent']
            with patch('sys.argv', arguments + (['--compact'] if compact else [])), \
                 patch('sys.stdout', output), \
                 patch('runtime.startup.acquire_project_entry', return_value=fixture.acquisition), \
                 patch.object(title_sync, 'NativeTitles', return_value=native):
                code = workflow.main()
            self.assertEqual(code, 0, output.getvalue())
            return json.loads(output.getvalue())

        report = status(True)
        self.assertTrue(report['observation_only'])
        self.assertNotIn('title_sync', report)
        self.assertEqual({p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}, before)
        self.assertEqual((native.title, native.writes), (title, writes))
        with state._database(fixture.scope) as db:
            self.assertEqual(state._task(db, state._scope_id(fixture.scope), 'codex', 'parent'), task)
        detailed = status(False)
        self.assertTrue(detailed['title_sync']['managed'])
        self.assertEqual((native.title, native.writes), (title, writes))
        self.assertEqual(state.task_status(fixture.scope, 'codex', 'parent')['task']['state'], 'REVIEW')
        status(False)
        self.assertEqual(native.title, '\uFFFB▱▱▱▱▱ Completed task')

    def test_native_flag_uses_selected_scope_without_mutating_state(self):
        from tests.test_delegation_dispatcher import DelegationDispatcherTests
        fixture = DelegationDispatcherTests(methodName='runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        database = fixture.fixture.scope.runtime.state_dir / 'runtime.sqlite3'
        before = database.read_bytes()
        code, report = fixture.cli(['status'], flags=('--client', 'codex', '--session', 'session-1', '--compact'))
        self.assertEqual(code, 0, report)
        self.assertEqual(report['view'], 'compact')
        self.assertEqual(database.read_bytes(), before)
        self.assertNotIn('task', report)

    def test_worker_flag_returns_summary_without_publishing(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder) / 'a'
            run.mkdir()
            path = run / 'result.json'
            raw = b'{"task_id":"a","state":"prepared","response":"PRIVATE"}'
            path.write_bytes(raw)
            result = subprocess.run([sys.executable, '-B', str(SCRIPT), 'status', '--root', folder, '--compact'],
                input='{"task_id":"a"}', text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('PRIVATE', result.stdout)
            self.assertTrue(json.loads(result.stdout)['observation_only'])
            self.assertEqual(path.read_bytes(), raw)
