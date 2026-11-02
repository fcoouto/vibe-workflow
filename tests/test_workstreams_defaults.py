"""Default rollout preserves legacy intent and grants no dispatch authority."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest
from unittest.mock import patch
from runtime import state, workstreams
from runtime.startup import RuntimeSettings
from tests import test_delegation


class WorkstreamsDefaultTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        self.fixture, self.acquisition, _ = fresh_canonical(self)
        self.fixture.root=self.fixture.base
        self.scope=self.acquisition.resolution.scope
        self.identity=dict(client='codex',session_id='default-session',task_id='default-task',scope_ref='user:task')

    def start(self,**updates):
        return state.start_task(self.scope,self.identity|dict(mode='editable',decision_ref='user:accepted')|updates)

    def status(self):
        return workstreams.control(self.scope,'status',dict(client='codex'))

    def test_new_supported_scope_starts_enabled_without_launch_authority(self):
        self.start()
        result=self.status()
        self.assertTrue(result['enabled'])
        self.assertEqual(result['revision'],1)
        self.assertEqual(result['unresolved_assignments'],0)
        with self.assertRaises(state.StateError):
            workstreams.consume_dispatch(self.scope,self.identity|dict(expected_revision=1,attempt_id='0'*32,workstream_revision=1))

    def test_existing_missing_canonical_preference_is_effective_default_without_send_authority(self):
        self.start()
        # A missing canonical setting remains preference information, never send authority.
        workstreams._path(self.scope).unlink(missing_ok=True)
        self.start(session_id='later',task_id='later')
        self.assertTrue(self.status()['enabled'])
        self.assertEqual(self.status()['revision'],0)
        with self.assertRaises(state.StateError):
            workstreams.consume_dispatch(self.scope,self.identity|dict(expected_revision=1,attempt_id='0'*32,workstream_revision=0))

    def test_explicit_disable_survives_new_tasks_and_idempotent_controls(self):
        self.start()
        mode=self.status()
        off=workstreams.control(self.scope,'disable',dict(client='codex',expected_revision=mode['revision']))
        self.start(session_id='later',task_id='later')
        self.assertFalse(self.status()['enabled'])
        self.assertEqual(self.status()['revision'],off['revision'])
        again=workstreams.control(self.scope,'disable',dict(client='codex',expected_revision=off['revision']))
        self.assertEqual(again['revision'],off['revision'])

    def test_first_legacy_disable_is_saved_even_if_already_off(self):
        self.start()
        workstreams._path(self.scope).unlink(missing_ok=True)
        off=workstreams.control(self.scope,'disable',dict(client='codex',expected_revision=0))
        self.assertFalse(off['enabled'])
        self.assertEqual(off['revision'],1)
        self.assertTrue(workstreams._path(self.scope).is_file())

    def test_born_scope_status_uses_effective_default_without_writes(self):
        before={str(p):p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}
        self.assertTrue(self.status()['enabled'])
        self.assertEqual(self.status()['revision'],0)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()})

    def test_failed_bootstrap_cannot_dispatch(self):
        with patch.object(state,'_save',side_effect=state.StateError('simulated transaction failure')):
            with self.assertRaises(state.StateError):self.start()
        self.assertIsNone(state.task_status(self.scope,'codex',self.identity['session_id'])['task'])
        with self.assertRaises(state.StateError):
            workstreams.consume_dispatch(self.scope,self.identity|dict(expected_revision=1,attempt_id='0'*32,workstream_revision=1))

    def test_concurrent_first_tasks_share_one_default_revision(self):
        # Actual D4 birth has registered storage; concurrent first tasks materialize one default.
        def start(n):
            import time
            for attempt in range(20):
                try: return self.start(session_id='parallel-'+str(n),task_id='parallel-'+str(n))
                except state.StateError as error:
                    if 'locked or incompatible' not in str(error) or attempt == 19: raise
                    time.sleep(0.01)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(start,range(2)))
        self.assertEqual({result['task']['task_id'] for result in results},{'parallel-0','parallel-1'})
        self.assertTrue(self.status()['enabled'])
        self.assertEqual(self.status()['revision'],1)

    def test_publication_before_failed_commit_grants_no_task_authority(self):
        with patch.object(state,'_status',side_effect=state.StateError('simulated post-publication failure')):
            with self.assertRaises(state.StateError):self.start()
        self.assertIsNone(state.task_status(self.scope,'codex',self.identity['session_id'])['task'])
        with self.assertRaises(state.StateError):
            workstreams.consume_dispatch(self.scope,self.identity|dict(expected_revision=1,attempt_id='0'*32,workstream_revision=1))
        self.start()
        self.assertTrue(self.status()['enabled'])
        self.assertEqual(self.status()['revision'],1)

    def test_unsupported_client_cannot_seed_mode(self):
        with self.assertRaises(state.StateError):self.start(client='claude-code')
        self.assertFalse(workstreams._path(self.scope).exists())

    def test_unsupported_canonical_scope_does_not_seed_preferences(self):
        self.scope=replace(self.scope,runtime=RuntimeSettings(3,self.fixture.root/'unsupported-authority'))
        with self.assertRaises(state.StateError):self.start()
        self.assertFalse(self.scope.runtime.state_dir.exists())
