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

    def test_delegation_caps_child_lineage_with_the_shared_constant(self):
        import re
        from runtime import delegation
        source = Path(delegation.__file__).read_text()
        self.assertIsNone(re.search(r'monotonic\(\) \+ 4\b', source))  # PR #219 follow-up: no inline cap
        self.assertIn('time.monotonic() + startup.OBSERVATION_SECONDS', source)


class SourceReadBudgetTests(unittest.TestCase):
    def test_revalidated_source_reuse_fits_one_content_read_near_the_cap(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / 'source.py'
            path.write_bytes(b'first\n')
            budget = dict(paths=0, bytes=8 * 1024 * 1024 - 7, deadline=time.monotonic() + 10)
            self.assertEqual(startup._budgeted_source_read(path, budget), b'first\n')
            self.assertEqual(startup._budgeted_source_read(path, budget), b'first\n')
            self.assertEqual(budget['bytes'], 8 * 1024 * 1024 - 1)
            self.assertEqual(budget['paths'], 2)

    def test_same_size_edit_with_restored_mtime_changes_policy_digest(self):
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            (root / 'policies').mkdir()
            path = root / 'policies/00-index.md'
            path.write_bytes(b'first\n')
            budget = setup_observation._budget()
            first = startup.policy_digest(root, budget=budget)
            before = path.stat()
            path.write_bytes(b'other\n')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertNotEqual(startup.policy_digest(root, budget=budget), first)
            self.assertEqual(budget['bytes'], 12)

    def test_replacement_is_reread_and_deletion_never_reuses_cached_bytes(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / 'source.py'
            path.write_bytes(b'first\n')
            budget = setup_observation._budget()
            startup._budgeted_source_read(path, budget)
            before = path.stat()
            replacement = path.with_name('replacement.py')
            replacement.write_bytes(b'other\n')
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            replacement.replace(path)
            self.assertEqual(startup._budgeted_source_read(path, budget), b'other\n')
            self.assertEqual(budget['bytes'], 12)
            path.unlink()
            with self.assertRaises(FileNotFoundError):
                startup._budgeted_source_read(path, budget)
            self.assertGreaterEqual(budget['bytes'], 8 * 1024 * 1024)

    def test_cached_sources_still_enforce_deadline_path_and_byte_limits(self):
        for exhausted, value, error in (('deadline', time.monotonic() - 1, startup.ObservationDeadline),
                ('paths', 4096, startup.StartupError), ('bytes', 8 * 1024 * 1024, startup.StartupError)):
            with self.subTest(exhausted=exhausted), tempfile.TemporaryDirectory() as base:
                path = Path(base) / 'source.py'
                path.write_bytes(b'first\n')
                budget = setup_observation._budget()
                startup._budgeted_source_read(path, budget)
                budget[exhausted] = value
                with self.assertRaises(error):
                    startup._budgeted_source_read(path, budget)

    def test_source_changed_during_read_is_not_accepted_or_cached(self):
        with tempfile.TemporaryDirectory() as base:
            path = Path(base) / 'source.py'
            path.write_bytes(b'first\n')
            real_open = Path.open

            class ChangingReader:
                def __enter__(self):
                    self.stream = real_open(path, 'rb')
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def fileno(self):
                    return self.stream.fileno()

                def read(self, limit):
                    raw = self.stream.read(limit)
                    with real_open(path, 'wb') as changed:
                        changed.write(b'other\n')
                    return raw

            budget = setup_observation._budget()
            with patch.object(Path, 'open', return_value=ChangingReader()), self.assertRaises(startup.StartupError):
                startup._budgeted_source_read(path, budget)
            self.assertGreaterEqual(budget['bytes'], 8 * 1024 * 1024)


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

    def test_d4_transaction_contention_is_busy_with_its_own_message(self):
        error = startup.StartupError('D4 transaction busy; retry the same bound request')
        failure = self.failure(error)
        self.assertIsInstance(failure, state.StateBusy)
        self.assertEqual(str(failure), str(error))
        self.assertIs(failure.__cause__, error)

    def test_d4_busy_matches_only_the_exact_constant(self):
        # PR #219 follow-up: text around the D4 constant may carry input, so it never surfaces as busy.
        from runtime.state_errors import D4_BUSY, UNAVAILABLE
        for error in (startup.StartupError(D4_BUSY + ': /Users/someone/private-input'),
                      startup.StartupError('private-input ' + D4_BUSY), startup.StartupError(D4_BUSY + ' '),
                      startup.RuntimeScopeError(D4_BUSY)):
            with self.subTest(str(error)):
                failure = self.failure(error)
                self.assertNotIsInstance(failure, (state.StateBusy, state.StateDeadline))
                self.assertEqual(str(failure), UNAVAILABLE)

    def test_other_startup_errors_never_surface_their_text(self):
        # Many StartupError messages embed input (setting keys, client names, joined blockers).
        from runtime import openrouter_settings
        for error in (startup.StartupError('Invalid /Users/someone/private-input field'),
                      changes.Pending('Published authority failed current qualification: private-input'),
                      openrouter_settings.SettingsError('private-input'),
                      startup.RuntimeScopeError('requires qualification: private-input')):
            with self.subTest(type(error).__name__):
                failure = self.failure(error)
                self.assertEqual(str(failure), 'Runtime state is unavailable, corrupt, locked or incompatible')
                self.assertNotIsInstance(failure, (state.StateBusy, state.StateDeadline))

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


class SurfacedMessageTests(unittest.TestCase):
    """database_failure surfaces a deadline's own text, so every deadline must be raised with a literal message."""

    def test_every_deadline_or_busy_error_is_raised_with_a_literal_message(self):
        import ast
        names, found = {'ObservationDeadline', 'StateDeadline', 'StateBusy'}, 0
        root = Path(__file__).resolve().parents[1]
        for path in sorted(root.glob('runtime/**/*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            # Module-level names bound once to a string literal count as literals.
            constants = {target.id for node in tree.body if isinstance(node, ast.Assign)
                         and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                         for target in node.targets if isinstance(target, ast.Name)}
            for node in ast.walk(tree):
                callee = getattr(node, 'func', None)
                name = getattr(callee, 'id', None) or getattr(callee, 'attr', None)
                if not isinstance(node, ast.Call) or name not in names:
                    continue
                found += 1
                literal = len(node.args) == 1 and (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                    or isinstance(node.args[0], ast.Name) and node.args[0].id in constants)
                # The one pass-through re-raises an ObservationDeadline, whose text this rule already bounds.
                passthrough = path.name == 'state_errors.py' and ast.unparse(node) == 'StateDeadline(str(error))'
                self.assertTrue(literal or passthrough, f'{path.relative_to(root)}:{node.lineno}: {ast.unparse(node)}')
        self.assertGreaterEqual(found, 5)


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

    def test_the_suites_package_root_never_lists_its_parent(self):
        # Fixtures use this checkout as the package root while other agents add and remove worktrees beside it;
        # #198's test-side listing freeze is gone because a development checkout never lists that parent.
        root, listed = state.PACKAGE.resolve(), []
        real = setup_observation._children

        def children(path, budget):
            listed.append(Path(path).resolve())
            return real(path, budget)
        config = dict(files=[], roots=[str(root)], package_root=str(root), plugin_id='vibe-workflow@local', keys=[])
        with patch.object(setup_observation, '_children', children):
            versions = setup_observation.current_configuration(config, root)['material']['installation_versions']
        self.assertIsNone(versions)
        self.assertNotIn(root.parent, listed)


if __name__ == '__main__':
    unittest.main()
