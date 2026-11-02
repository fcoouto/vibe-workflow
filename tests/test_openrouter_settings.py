"""Saved consent is local, checkout-bound and separate from connection health."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime import changes, startup, state
from tests.openrouter_fixtures import PACKAGE, enroll, cli_driver


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PACKAGE / 'runtime/openrouter_settings.py').exists(), 'saved OpenRouter controls must exist')
        self.settings = importlib.import_module('runtime.openrouter_settings')
        self.tmp = tempfile.TemporaryDirectory(prefix='openrouter management ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.scope = enroll(self)
        self.repo = self.scope.repository_root
        self.locks = self.root / 'locks'
        guard = patch.object(changes, 'LOCK_ROOT', self.locks)
        guard.start()
        self.addCleanup(guard.stop)
        self.exe = self.root / 'opencode'
        self.exe.write_text('#!/bin/sh\nexit 0\n')
        self.exe.chmod(0o700)
        self.auth = self.root / 'auth'
        self.auth.mkdir(mode=0o700)
        self.request = dict(expected_revision=0, routes=['proposals', 'workers'],
                            opencode=str(self.exe), auth_data=str(self.auth))
        self.path = self.scope.runtime.state_dir / 'openrouter' / (state._scope_id(self.scope) + '.json')

    def snapshot(self):
        return {str(path): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
            for root in (self.root, self.repo, self.scope.runtime.state_dir) for path in root.rglob('*')}

    def configured(self):
        return self.settings.configure(self.repo, self.request)

    def enabled(self):
        result = self.configured()
        return self.settings.enable(self.repo, dict(expected_revision=result['revision']))

    def test_default_status_is_off_without_writes_or_client_execution(self):
        before = self.snapshot()
        report = self.settings.status(self.repo)
        self.assertFalse(report['configured'])
        self.assertFalse(report['enabled'])
        self.assertEqual(report['revision'], 0)
        self.assertEqual(report['control_scope'], 'checkout')
        self.assertEqual(report['connection'], 'not_checked')
        self.assertFalse(self.locks.exists())
        self.assertEqual(self.snapshot(), before)

    def test_saved_active_flags_without_owner_qualification_cannot_enable_settings(self):
        from tests.fixture_canonical_birth import _ACTUAL_ACQUIRE
        self.enabled()
        before = self.snapshot()
        with patch.object(startup, 'acquire_project_entry', _ACTUAL_ACQUIRE):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.status(self.repo)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_runtime_is_left_untouched(self):
        repo = self.root / 'legacy'
        repo.mkdir()
        environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        subprocess.run(['git', 'init', '-q', '--template=', str(repo)],
                       env=environment, check=True, capture_output=True)
        root, common = startup.checkout(repo)
        directory = self.root / 'legacy-state'
        directory.mkdir(mode=0o700)
        (directory / 'runtime.sqlite3').write_bytes(b'original pinned runtime only')
        record = dict(schema_version=1, project_id='legacy-fixture', repository_root=str(root),
            git_common_dir=str(common), clients=['codex'], policy_digest=startup.policy_digest(PACKAGE),
            runtime=dict(schema_version=2, state_dir=str(directory)))
        path = repo / startup.PROJECT_FILE
        path.parent.mkdir()
        path.write_text(json.dumps(record))
        before = self.snapshot()
        for operation in (lambda: self.settings.status(repo), lambda: self.settings.configure(repo, self.request)):
            with self.assertRaises(self.settings.SettingsError):
                operation()
        self.assertEqual(self.snapshot(), before)

    def test_configure_is_private_off_and_does_not_mutate_native_state(self):
        database = self.scope.runtime.state_dir / 'runtime.sqlite3'
        before = database.read_bytes()
        report = self.configured()
        self.assertFalse(report['enabled'])
        self.assertEqual(report['revision'], 1)
        self.assertEqual(report['routes'], ['proposals', 'workers'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(database.read_bytes(), before)

    def test_council_requires_its_own_enabled_route(self):
        self.enabled()
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_route_disabled'):
            with self.settings.admission(self.repo, 'council'): self.fail('must not admit')
        self.settings.disable(self.repo, dict(expected_revision=2))
        configured = self.settings.configure(self.repo,
            dict(self.request, expected_revision=3, routes=['council']))
        self.assertFalse(configured['enabled'])
        self.assertEqual(configured['routes'], ['council'])
        self.assertEqual(Path(configured['council_root']), self.path.with_suffix('') / 'council')
        self.assertNotEqual(configured['council_root'], configured['worker_root'])
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_disabled'):
            with self.settings.admission(self.repo, 'council'): self.fail('must not admit')
        enabled = self.settings.enable(self.repo, dict(expected_revision=configured['revision']))
        before = self.snapshot()
        self.assertEqual(self.settings.status(self.repo), enabled)
        self.assertEqual(self.snapshot(), before)
        with self.settings.admission(self.repo, 'council', root=enabled['council_root'],
                opencode=self.exe, auth_data=self.auth, revision=enabled['revision'],
                scope_id=enabled['scope_id']) as admitted:
            self.assertEqual(admitted, enabled)
        for arguments, reason in ((dict(root=enabled['worker_root']), 'openrouter_configuration_mismatch'),
                (dict(revision=configured['revision']), 'openrouter_preparation_stale'),
                (dict(scope_id='other-checkout'), 'openrouter_scope_changed')):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(self.settings.SettingsError, reason):
                with self.settings.admission(self.repo, 'council', **arguments): self.fail('must not admit')
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_route_disabled'):
            with self.settings.admission(self.repo, 'workers'): self.fail('must not admit')

    def test_council_requires_runtime_without_workers(self):
        with self.assertRaisesRegex(self.settings.SettingsError, 'invalid_openrouter_runtime_references'):
            self.settings.configure(self.repo,
                dict(expected_revision=0, routes=['council'], opencode=None, auth_data=None))
        configured = self.settings.configure(self.repo, dict(self.request, routes=['council']))
        previous = self.path.read_bytes()
        self.exe.chmod(0o600)
        self.assertEqual(self.settings.status(self.repo)['local_configuration'], 'unavailable')
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_runtime_unavailable'):
            self.settings.enable(self.repo, dict(expected_revision=configured['revision']))
        self.assertEqual(self.path.read_bytes(), previous)
        self.exe.chmod(0o700)
        enabled = self.settings.enable(self.repo, dict(expected_revision=configured['revision']))
        self.auth.rmdir()
        self.assertEqual(self.settings.status(self.repo)['local_configuration'], 'unavailable')
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_configuration_unavailable'):
            with self.settings.admission(self.repo, 'council'): self.fail('must not admit')
        self.auth.mkdir(mode=0o700)
        self.settings.disable(self.repo, dict(expected_revision=enabled['revision']))
        all_routes = self.settings.configure(self.repo,
            dict(self.request, expected_revision=3, routes=['workers', 'council', 'proposals']))
        self.assertEqual(all_routes['routes'], ['council', 'proposals', 'workers'])

    def test_existing_settings_remain_valid(self):
        for routes in (['proposals', 'workers'], ['workers'], ['proposals']):
            with self.subTest(routes=routes):
                revision = self.settings.status(self.repo)['revision']
                configured = self.settings.configure(self.repo, dict(expected_revision=revision, routes=routes,
                    opencode=str(self.exe) if 'workers' in routes else None,
                    auth_data=str(self.auth) if 'workers' in routes else None))
                enabled = self.settings.enable(self.repo, dict(expected_revision=configured['revision']))
                previous_record_before_read = self.path.read_bytes()
                report = self.settings.status(self.repo)
                previous_record_after_read = self.path.read_bytes()
                self.assertEqual(previous_record_after_read, previous_record_before_read)
                self.assertEqual(report['routes'], routes)
                self.assertTrue(report['enabled'])
                self.assertEqual(report['local_configuration'], 'ready')
                record = json.loads(previous_record_before_read)
                self.assertEqual(record['version'], 1)
                self.assertEqual(set(record),
                    {'version', 'revision', 'binding', 'routes', 'opencode', 'auth_data', 'enabled'})
                self.settings.disable(self.repo, dict(expected_revision=enabled['revision']))

    def test_saved_enable_disable_and_stale_revisions(self):
        self.enabled()
        self.assertTrue(self.settings.status(self.repo)['enabled'])
        raw = self.path.read_bytes()
        for request in ({'expected_revision': 1}, {'expected_revision': True},
                        {'expected_revision': 2, 'api_key': 'SECRET'}):
            with self.subTest(request=request), self.assertRaises(self.settings.SettingsError):
                self.settings.disable(self.repo, request)
            self.assertEqual(self.path.read_bytes(), raw)
        off = self.settings.disable(self.repo, dict(expected_revision=2))
        self.assertFalse(off['enabled'])
        self.assertEqual(off['revision'], 3)
        self.assertEqual(self.settings.disable(self.repo, dict(expected_revision=3)), off)
        self.assertTrue(self.settings.enable(self.repo, dict(expected_revision=3))['enabled'])

    def test_status_does_not_read_credentials_or_invoke_the_configured_runtime(self):
        self.enabled()
        secret = self.auth / 'credential-sentinel'
        secret.write_text('SECRET_MUST_NOT_BE_READ')
        before = self.snapshot()
        original = os.open
        def opened(path, *args, **kwargs):
            if str(path) == str(secret): raise AssertionError('credential read')
            return original(path, *args, **kwargs)
        launch = subprocess.Popen
        def spawned(command, *args, **kwargs):
            self.assertEqual(Path(command[0]).name, 'git', 'status may only resolve local Git scope')
            return launch(command, *args, **kwargs)
        with patch('os.open', side_effect=opened), patch('subprocess.Popen', side_effect=spawned):
            report = self.settings.status(self.repo)
        self.assertTrue(report['enabled'])
        self.assertEqual(report['connection'], 'not_checked')
        self.assertNotIn('SECRET', json.dumps(report))
        self.assertEqual(self.snapshot(), before)

    def test_missing_or_replaced_authority_cannot_bootstrap_or_repin(self):
        self.enabled()
        old = self.path.read_bytes()
        database = self.scope.runtime.state_dir / 'runtime.sqlite3'
        saved = database.with_suffix('.saved')
        database.rename(saved)
        with self.assertRaises(self.settings.SettingsError): self.settings.status(self.repo)
        self.assertFalse(database.exists())
        database.write_bytes(saved.read_bytes())
        database.chmod(0o600)
        with self.assertRaises(self.settings.SettingsError): self.settings.status(self.repo)
        self.assertEqual(self.path.read_bytes(), old)

    def test_database_task_updates_preserve_consent(self):
        self.enabled()
        state.start_task(self.scope, dict(client='codex', session_id='other-session', task_id='other-task',
            scope_ref='test:scope', mode='read-only', decision_ref='test:scope'))
        self.assertTrue(self.settings.status(self.repo)['enabled'])
        self.assertEqual(self.settings.status(self.repo)['revision'], 2)

    def test_copied_settings_do_not_enable_another_enrolled_checkout(self):
        self.enabled()
        other_scope = enroll(self)
        other = other_scope.repository_root
        folder = other_scope.runtime.state_dir / 'openrouter'
        folder.mkdir(mode=0o700)
        copied = folder / (state._scope_id(other_scope) + '.json')
        copied.write_bytes(self.path.read_bytes())
        copied.chmod(0o600)
        with self.assertRaises(self.settings.SettingsError): self.settings.status(other)
        self.assertTrue(self.settings.status(self.repo)['enabled'])

    def test_symlink_and_hardlink_settings_are_rejected(self):
        self.configured()
        saved = self.path.with_suffix('.saved')
        self.path.rename(saved)
        self.path.symlink_to(saved)
        with self.assertRaises(self.settings.SettingsError): self.settings.status(self.repo)
        self.path.unlink()
        os.link(saved, self.path)
        with self.assertRaises(self.settings.SettingsError): self.settings.status(self.repo)

    def test_missing_private_work_directory_is_unavailable_and_blocks_admission(self):
        report = self.enabled()
        data = Path(report['proposal_store']).parent
        data.rmdir()
        report = self.settings.status(self.repo)
        self.assertTrue(report['enabled'])
        self.assertEqual(report['local_configuration'], 'unavailable')
        self.assertFalse(data.exists())
        with self.assertRaises(self.settings.SettingsError):
            with self.settings.admission(self.repo, 'proposals'): self.fail('must not admit')

    def test_replaced_namespace_and_unselected_route_are_rejected(self):
        self.settings.configure(self.repo, dict(expected_revision=0, routes=['proposals'], opencode=None, auth_data=None))
        self.settings.enable(self.repo, dict(expected_revision=1))
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_route_disabled'):
            with self.settings.admission(self.repo, 'workers'): self.fail('must not admit')
        original = self.path.parent
        retired = original.with_name('retired')
        original.rename(retired)
        original.mkdir(mode=0o700)
        copied = original / self.path.name
        copied.write_bytes((retired / self.path.name).read_bytes())
        copied.chmod(0o600)
        with self.assertRaises(self.settings.SettingsError): self.settings.status(self.repo)

    def test_reconfiguration_requires_disabled_and_explicit_valid_routes(self):
        self.enabled()
        with self.assertRaises(self.settings.SettingsError):
            self.settings.configure(self.repo, dict(self.request, expected_revision=2))
        self.settings.disable(self.repo, dict(expected_revision=2))
        for routes in ([], ['workers', 'workers'], ['native'], ['workers', 'write']):
            with self.subTest(routes=routes), self.assertRaises(self.settings.SettingsError):
                self.settings.configure(self.repo, dict(self.request, expected_revision=3, routes=routes))
        report = self.settings.configure(self.repo, dict(expected_revision=3, routes=['proposals'], opencode=None, auth_data=None))
        self.assertFalse(report['enabled'])
        self.assertEqual(report['routes'], ['proposals'])

    def test_isolated_cli_persists_controls_without_project_imports(self):
        script = cli_driver(self, self.scope, PACKAGE / 'scripts/openrouter.py')
        self.assertTrue(script.exists(), 'management CLI must exist')
        configured = self.configured()
        result = subprocess.run([sys.executable, '-I', '-B', str(script), 'enable', '--cwd', str(self.repo)],
            input=json.dumps({'expected_revision': configured['revision']}), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['enabled'])
        result = subprocess.run([sys.executable, '-I', '-B', str(script), 'status', '--cwd', str(self.repo)],
            input='', text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['enabled'])

    def test_held_authority_lock_is_safe_control_contention(self):
        self.enabled()
        locations = changes._authority_locations([self.scope.runtime.state_dir])
        with changes._authority_guards([self.scope.runtime.state_dir]):
            with self.assertRaisesRegex(self.settings.SettingsError, '^openrouter_operation_busy$'):
                with self.settings.admission(self.repo, 'workers'): self.fail('must not admit')
        with self.settings.admission(self.repo, 'workers') as admitted:
            self.assertEqual(admitted['revision'], 2)

    def test_active_uncertain_authority_and_unrelated_storage_failure_stay_blocked(self):
        self.enabled()
        location = changes._authority_locations([self.scope.runtime.state_dir])[0]
        plan = self.root / 'active-plan'; plan.mkdir(mode=0o700)
        owner = Path(str(location) + '.json')
        owner.write_text(json.dumps(dict(schema_version=1, plan_dir=str(plan),
            transaction_id='fixture-active-operation', manifest_digest='0' * 64)))
        owner.chmod(0o600)
        with self.assertRaisesRegex(self.settings.SettingsError, '^openrouter_storage_or_scope_unavailable$'):
            with self.settings.admission(self.repo, 'workers'): self.fail('must not admit')
        owner.unlink()
        for error in (OSError('private failure'), startup.StartupError('other busy storage failure'),
                      changes.Pending('D4 transaction busy; retry the same bound request')):
            with self.subTest(error=type(error).__name__), patch.object(changes, '_authority_guards', side_effect=error):
                with self.assertRaisesRegex(self.settings.SettingsError, '^openrouter_storage_or_scope_unavailable$'):
                    with self.settings.admission(self.repo, 'workers'): self.fail('must not admit')


if __name__ == '__main__': unittest.main()
