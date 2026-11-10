"""Startup deadlines keep their own diagnostics, and development worktrees do not change setup fingerprints."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

# changes binds startup helpers at import; import it before any test patches them.
from runtime import changes, local_ownership, package_manifest, review_storage, setup_observation, startup, state  # noqa: F401


def expired():
    return dict(paths=0, bytes=0, deadline=time.monotonic() - 1)


class DeadlineTypeTests(unittest.TestCase):
    def test_expired_budget_raises_a_distinct_startup_error(self):
        with self.assertRaises(startup.ObservationDeadline) as raised:
            startup._check_observation_deadline(expired())
        self.assertIsInstance(raised.exception, startup.StartupError)
        self.assertEqual(str(raised.exception), 'Canonical acquisition deadline exceeded')

    def test_one_constant_sets_every_observation_cap(self):
        self.assertEqual(startup.OBSERVATION_SECONDS, 4)
        with patch.object(startup, 'OBSERVATION_SECONDS', 60):
            now = time.monotonic()
            self.assertGreater(setup_observation._budget()['deadline'], now + 30)
            self.assertGreater(review_storage._budget()['deadline'], now + 30)
            seen = {}

            def membership(cwd, *, authority_path, budget):
                seen.update(budget)
                return SimpleNamespace(status='conflict', observation=None)
            with patch.object(startup, '_acquire_canonical_membership', side_effect=membership), \
                    self.assertRaises(startup.StartupError):
                local_ownership._acquire(Path('/nonexistent'))
            self.assertGreater(seen['deadline'], now + 30)
            budget = dict(paths=0, bytes=0, deadline=now + 50)
            startup.acquire_canonical_project(SimpleNamespace(client='!', cwd=Path('/nonexistent')), state.PACKAGE,
                                              budget=budget)
            self.assertEqual(budget['deadline'], now + 50)


class AcquisitionReasonTests(unittest.TestCase):
    def test_membership_deadline_is_reported_as_a_deadline(self):
        with tempfile.TemporaryDirectory() as base:
            result = startup._acquire_canonical_membership(Path(base).resolve(), authority_path=None, budget=expired())
        self.assertEqual((result.status, result.reason), ('conflict', 'observation_deadline'))

    def test_canonical_project_deadline_is_reported_as_a_deadline(self):
        event = SimpleNamespace(client='codex', cwd=Path('/nonexistent'))
        result = startup.acquire_canonical_project(event, state.PACKAGE, deadline=time.monotonic() - 1)
        self.assertEqual((result.resolution.status, result.resolution.reason), ('conflict', 'observation_deadline'))

    def entry(self, error):
        with tempfile.TemporaryDirectory() as base, \
                patch.object(startup, 'repository_root', return_value=Path(base)), \
                patch.object(startup, 'read_record', return_value=dict(schema_version=2)), \
                patch.object(startup, '_read_record_snapshot', side_effect=error):
            return startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(base)), state.PACKAGE)

    def test_project_entry_deadline_is_reported_as_a_deadline(self):
        result = self.entry(startup.ObservationDeadline('Canonical acquisition deadline exceeded'))
        self.assertEqual((result.resolution.status, result.resolution.reason), ('conflict', 'observation_deadline'))
        result = self.entry(startup.StartupError('Unrelated observation failure'))
        self.assertEqual((result.resolution.status, result.resolution.reason), ('conflict', 'observation_unavailable'))


class QualificationDeadlineTests(unittest.TestCase):
    def require(self, reason):
        scope = Mock(spec=startup.AcceptedScope)
        scope.qualification, scope.client, scope.repository_root = dict(reference={}), 'codex', Path('/nonexistent')
        acquisition = startup.CanonicalAcquisition(startup.ScopeResolution('conflict', None, reason), None)
        with patch.object(startup, 'acquire_project_entry', return_value=acquisition):
            setup_observation.require_capability(scope, ['startup-context'], task={}, operation='observe-cli-basic')

    def test_require_capability_reports_an_observation_deadline(self):
        with self.assertRaises(startup.ObservationDeadline) as raised:
            self.require('observation_deadline')
        self.assertEqual(str(raised.exception), 'Qualification observation deadline exceeded')
        with self.assertRaises(startup.StartupError) as raised:
            self.require('observation_unavailable')
        self.assertNotIsInstance(raised.exception, startup.ObservationDeadline)
        self.assertEqual(str(raised.exception), 'Qualification scope or observable configuration changed')

    def test_retained_qualification_read_lets_only_deadlines_through(self):
        error = startup.ObservationDeadline('Canonical acquisition deadline exceeded')
        with patch.object(startup, '_canonical_plain', side_effect=error), \
                self.assertRaises(startup.ObservationDeadline) as raised:
            setup_observation.read_qualification({}, {})
        self.assertIs(raised.exception, error)
        with patch.object(startup, '_canonical_plain', side_effect=startup.StartupError('other')):
            self.assertIsNone(setup_observation.read_qualification({}, {}))


class ManifestDeadlineTests(unittest.TestCase):
    def test_a_git_read_that_runs_out_of_time_reports_the_deadline(self):
        clock = iter([0.0] + [100.0] * 1000)
        with patch.object(package_manifest, 'time', SimpleNamespace(monotonic=lambda: next(clock))), \
                self.assertRaises(package_manifest.PackageManifestError) as raised:
            package_manifest._git(Path(__file__).resolve().parents[1], 'rev-parse', '--show-toplevel', deadline=1.0)
        self.assertEqual(str(raised.exception), 'Package membership deadline exceeded')


class StateFailureTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        _, acquisition, _ = fresh_canonical(self)
        self.scope = acquisition.resolution.scope
        state.start_task(self.scope, dict(client='codex', session_id='session', task_id='task', scope_ref='request:1',
                                          mode='editable', decision_ref='user:1'))

    def failure(self, error, **options):
        with self.assertRaises(state.StateError) as raised:
            with state._database(self.scope, **options):
                raise error
        return raised.exception

    def test_startup_errors_keep_their_message_and_cause(self):
        error = startup.StartupError('Original qualification operation is unavailable or expired')
        failure = self.failure(error)
        self.assertEqual(str(failure), str(error))
        self.assertIs(failure.__cause__, error)

    def test_a_locked_database_is_busy_not_corrupt(self):
        with closing(sqlite3.connect(state._path(self.scope), timeout=0, isolation_level=None)) as other:
            other.execute('begin immediate')
            with self.assertRaises(state.StateBusy) as raised:
                with state._database(self.scope, write=True):
                    pass
            other.execute('rollback')
        self.assertIsInstance(raised.exception, state.StateError)
        self.assertEqual(str(raised.exception), 'Runtime state is busy')
        self.assertIsInstance(raised.exception.__cause__, sqlite3.OperationalError)

    def test_deadlines_are_reported_as_deadlines(self):
        interrupted = sqlite3.OperationalError('interrupted')
        failure = self.failure(interrupted, deadline=time.monotonic() + 60)
        self.assertIsInstance(failure, state.StateDeadline)
        self.assertIsInstance(failure, startup.ObservationDeadline)
        self.assertEqual(str(failure), 'Runtime state deadline exceeded')
        self.assertIs(failure.__cause__, interrupted)
        failure = self.failure(startup.ObservationDeadline('Canonical acquisition deadline exceeded'))
        self.assertIsInstance(failure, state.StateDeadline)
        self.assertEqual(str(failure), 'Canonical acquisition deadline exceeded')

    def test_the_progress_handler_deadline_is_a_deadline(self):
        query = 'with recursive c(x) as (select 1 union all select x+1 from c) select count(*) from c'
        with self.assertRaises(state.StateDeadline):
            with state._database(self.scope, deadline=time.monotonic() + 2) as db:
                db.execute(query).fetchone()

    def test_other_failures_keep_the_generic_sanitized_diagnostic(self):
        failure = self.failure(sqlite3.DatabaseError('file is not a database'))
        self.assertEqual(str(failure), 'Runtime state is unavailable, corrupt, locked or incompatible')
        self.assertNotIsInstance(failure, (state.StateBusy, state.StateDeadline))


class SiblingFingerprintTests(unittest.TestCase):
    def package(self, root, *, git=False):
        (root / 'hooks').mkdir(parents=True)
        (root / 'hooks/codex.json').write_text('{}\n')
        if git:
            subprocess.run(['git', '-c', 'init.defaultBranch=main', 'init', '-q', str(root)], check=True,
                           env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})
        return root

    def digest(self, root):
        config = dict(files=[], roots=[str(root)], package_root=str(root), plugin_id='vibe-workflow@local', keys=[])
        return setup_observation.current_configuration(config, root)['digest']

    def assert_sibling(self, root, *, changes):
        before = self.digest(root)
        (root.parent / 'added-sibling').mkdir()
        (self.assertNotEqual if changes else self.assertEqual)(self.digest(root), before)

    def test_installed_version_beside_an_installed_copy_still_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as base:
            cache = Path(base).resolve().joinpath('.codex/plugins/cache/local/vibe-workflow')
            self.assert_sibling(self.package(cache / '0.1.0'), changes=True)

    def test_installed_copy_with_a_copied_git_folder_still_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as base:
            cache = Path(base).resolve().joinpath('.codex/plugins/cache/local/vibe-workflow')
            self.assert_sibling(self.package(cache / '0.1.0', git=True), changes=True)

    def test_unrecognized_package_location_still_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as base:
            self.assert_sibling(self.package(Path(base).resolve().joinpath('exports/vibe-workflow')), changes=True)

    def test_worktree_churn_beside_a_development_checkout_does_not_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as base:
            self.assert_sibling(self.package(Path(base).resolve().joinpath('vibe-workflow.worktrees/lane'), git=True), changes=False)


if __name__ == '__main__':
    unittest.main()
