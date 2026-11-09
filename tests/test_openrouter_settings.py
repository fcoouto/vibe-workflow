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
from tests.openrouter_fixtures import PACKAGE, enroll, fixture_command


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((PACKAGE / 'runtime/openrouter_settings.py').exists(), 'saved OpenRouter controls must exist')
        self.settings = importlib.import_module('runtime.openrouter_settings')
        self.tmp = tempfile.TemporaryDirectory(prefix='openrouter management ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self._canonical_peer = self._testMethodName == 'test_copied_settings_do_not_enable_another_enrolled_checkout'
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
        return self.settings.enable(self.repo, dict(expected_revision=result['revision'], consent_version=2))

    def legacy_worker_consent(self):
        self.configured()
        record = json.loads(self.path.read_text())
        record.update(version=1, revision=2, enabled=True)
        self.path.write_text(json.dumps(record))

    def test_optional_permission_preserves_omitted_legacy_and_explicit_no_expiry(self):
        from tests.test_external_delegation import permission
        consent = permission('openrouter', 'worker')
        first = self.settings.configure(self.repo, dict(self.request, external_permission=consent))
        self.assertEqual(first['external_permission'], consent)
        retained = self.settings.configure(self.repo, dict(self.request, expected_revision=first['revision']))
        self.assertEqual(retained['external_permission'], consent)
        self.assertIsNone(retained['external_permission']['valid_until'])
        removed = self.settings.configure(self.repo, dict(self.request, expected_revision=retained['revision'],
                                                          external_permission=None))
        self.assertIsNone(removed['external_permission'])
        self.assertFalse(removed['enabled'])

    def test_legacy_worker_consent_is_readable_and_admits_read_only(self):
        self.legacy_worker_consent()
        before = self.snapshot()
        with self.settings.admission(self.repo, 'workers') as admitted:
            self.assertEqual(admitted['consent_version'], 1)
        report = self.settings.status(self.repo)
        self.assertTrue(report['enabled'])
        self.assertFalse(report['enablement_renewal_required'])
        self.assertEqual(report['worker_capability'], 'read_only')
        self.assertEqual(report['execution_qualification'], 'not_checked')
        with self.settings.admission(self.repo, 'proposals'):
            pass
        self.assertEqual(self.snapshot(), before)

    def test_enable_records_worker_activation_without_extra_acknowledgement(self):
        report = self.configured()
        enabled = self.settings.enable(self.repo, {'expected_revision': report['revision']})
        self.assertTrue(enabled['enabled'])
        self.assertEqual(enabled['consent_version'], 2)
        self.assertEqual(enabled['worker_capability'], 'read_only')

    def test_enable_preserves_enabled_legacy_consent_and_history(self):
        self.legacy_worker_consent()
        sentinel = Path(self.settings.status(self.repo)['proposal_store'])
        sentinel.write_text('existing task history')
        before = self.path.read_bytes()
        report = self.settings.enable(self.repo, dict(expected_revision=2, consent_version=2))
        self.assertEqual(report['revision'], 2)
        self.assertEqual(report.get('consent_version'), 1)
        self.assertEqual(report.get('worker_capability'), 'read_only')
        self.assertFalse(report.get('enablement_renewal_required'))
        self.assertEqual(sentinel.read_text(), 'existing task history')
        self.assertEqual(report['opencode'], str(self.exe))
        self.assertEqual(report['auth_data'], str(self.auth))
        self.assertEqual(self.settings.enable(self.repo, dict(expected_revision=2)), report)
        self.assertEqual(self.path.read_bytes(), before)
        off = self.settings.disable(self.repo, dict(expected_revision=2))
        enabled = self.settings.enable(self.repo, dict(expected_revision=off['revision'], consent_version=2))
        self.assertEqual(enabled['consent_version'], 2)
        with self.assertRaises(self.settings.SettingsError):
            with self.settings.admission(self.repo, 'workers', revision=2): self.fail('stale preparation admitted')
        with self.settings.admission(self.repo, 'workers', revision=enabled['revision']) as admitted:
            self.assertEqual(admitted['consent_version'], 2)
        self.assertFalse(self.settings.disable(self.repo, dict(expected_revision=enabled['revision']))['enabled'])

    def test_legacy_enabled_read_only_worker_needs_no_editing_renewal(self):
        self.legacy_worker_consent()
        with self.settings.admission(self.repo, 'workers', revision=2) as report:
            self.assertEqual(report['worker_capability'], 'read_only')
            self.assertEqual(report['consent_version'], 1)
        self.settings.disable(self.repo, dict(expected_revision=2))
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_disabled'):
            with self.settings.admission(self.repo, 'workers'): self.fail('disabled route admitted')
        self.settings.enable(self.repo, dict(expected_revision=3))
        with self.assertRaisesRegex(self.settings.SettingsError, 'openrouter_preparation_stale'):
            with self.settings.admission(self.repo, 'workers', revision=2): self.fail('stale preparation admitted')

    def test_status_activation_ref_follows_revision(self):
        self.assertIsNone(self.settings.status(self.repo)['activation'])
        self.assertIsNone(self.configured()['activation'])
        first = self.settings.enable(self.repo, dict(expected_revision=1, consent_version=2))
        canonical = json.loads(self.path.read_text())['binding']['scope_id']
        self.assertEqual(first['activation'], dict(ref=f'activation:openrouter:{canonical}:r2',
            funding_ref='openrouter-provider-limits', classes=None, destinations=None))
        self.assertEqual(self.settings.status(self.repo)['activation'], first['activation'])
        off = self.settings.disable(self.repo, dict(expected_revision=2))
        self.assertIsNone(off['activation'])
        self.assertEqual(off['revision'], 3)
        configured = self.settings.configure(self.repo, dict(self.request, expected_revision=3))
        self.assertIsNone(configured['activation'])
        self.assertEqual(configured['revision'], 4)
        enabled = self.settings.enable(self.repo, dict(expected_revision=4, consent_version=2))
        self.assertEqual(enabled['activation']['ref'], f'activation:openrouter:{canonical}:r5')
        self.assertNotEqual(enabled['activation'], first['activation'])

    def test_worker_consent_version_rejects_wrong_types_without_mutation(self):
        self.legacy_worker_consent()
        before = self.path.read_bytes()
        for value in (True, '2', 2.0, 1, None):
            with self.subTest(value=value), self.assertRaises(self.settings.SettingsError):
                self.settings.enable(self.repo, dict(expected_revision=2, consent_version=value))
            self.assertEqual(self.path.read_bytes(), before)

    def test_legacy_proposal_only_enable_remains_independent(self):
        self.settings.configure(self.repo, dict(expected_revision=0, routes=['proposals'], opencode=None, auth_data=None))
        record = json.loads(self.path.read_text())
        record['version'] = 1
        self.path.write_text(json.dumps(record))
        report = self.settings.enable(self.repo, dict(expected_revision=1))
        self.assertTrue(report['enabled'])
        with self.settings.admission(self.repo, 'proposals'):
            pass
        self.assertFalse(report.get('enablement_renewal_required'))

    def test_unknown_control_cannot_disable_saved_consent(self):
        self.legacy_worker_consent()
        before = self.path.read_bytes()
        with self.assertRaises(self.settings.SettingsError):
            self.settings.operate(self.repo, 'unknown', dict(expected_revision=2))
        self.assertEqual(self.path.read_bytes(), before)

    def test_default_status_is_off_without_writes_or_client_execution(self):
        before = self.snapshot()
        with patch.object(startup, 'acquire_project_entry', side_effect=startup.StartupError('qualification unavailable')):
            report = self.settings.status(self.repo)
        self.assertFalse(report['configured'])
        self.assertFalse(report['enabled'])
        self.assertEqual(report['revision'], 0)
        self.assertEqual(report['control_scope'], 'checkout')
        self.assertEqual(report['connection'], 'not_checked')
        self.assertFalse(self.locks.exists())
        self.assertEqual(self.snapshot(), before)

    def test_local_recovery_identity_cannot_authorize_runtime_execution(self):
        from runtime import local_ownership
        owner = local_ownership.acquire(self.repo)
        self.assertNotIsInstance(owner, startup.AcceptedScope)
        with self.assertRaises(state.StateError):
            with state._database(owner): self.fail('recovery authorized execution')

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

    def test_status_and_disable_recover_saved_consent_without_runtime_qualification(self):
        self.enabled()
        binding = json.loads(self.path.read_bytes())['binding']
        authority = self.repo / startup.PROJECT_FILE
        project = json.loads(authority.read_bytes())
        project['repositories'][0]['checkouts'][0]['clients'][0]['configuration']['runtime_digest'] = 'b' * 64
        authority.write_text(json.dumps(project))
        database = self.scope.runtime.state_dir / 'runtime.sqlite3'
        database.write_bytes(b'broken runtime database')
        before = database.read_bytes()
        with patch.object(startup, 'acquire_project_entry', side_effect=startup.StartupError('qualification unavailable')):
            self.assertTrue(self.settings.status(self.repo)['enabled'])
            with self.assertRaises(self.settings.SettingsError):
                self.settings.disable(self.repo, dict(expected_revision=1))
            report = self.settings.disable(self.repo, dict(expected_revision=2))
            self.assertFalse(report['enabled'])
            self.assertEqual(report['revision'], 3)
            self.assertEqual(json.loads(self.path.read_bytes())['binding'], binding)
            with self.assertRaises(self.settings.SettingsError):
                self.settings.enable(self.repo, dict(expected_revision=3))
            with self.assertRaises(self.settings.SettingsError):
                self.settings.configure(self.repo, dict(self.request, expected_revision=3))
        self.assertEqual(database.read_bytes(), before)

    def test_basic_checkout_worker_consent_without_runtime(self):
        authority = self.repo / startup.PROJECT_FILE
        project = json.loads(authority.read_bytes())
        entry = project['repositories'][0]['checkouts'][0]['clients'][0]
        entry['configuration']['runtime'] = None
        entry['activation'].update(status='configured', qualification='pending',
            accepted_scope_digest=None, transaction_id=None, evidence=[], qualification_binding=None)
        authority.write_text(json.dumps(project))
        original = authority.read_bytes()
        storage = self.root / 'basic-openrouter'
        with patch.object(self.settings, 'BASIC_STORAGE', storage, create=True):
            self.assertFalse(self.settings.status(self.repo)['configured'])
            self.assertFalse(storage.exists())
            with self.assertRaises(self.settings.SettingsError):
                self.settings.configure(self.repo, self.request)
            configured = self.settings.configure(self.repo, dict(self.request, routes=['workers']))
            self.assertFalse(configured['enabled'])
            self.settings.enable(self.repo, dict(expected_revision=1))
            with self.settings.admission(self.repo, 'workers', revision=2) as report:
                self.assertEqual(report['worker_capability'], 'read_only')
            with self.assertRaises(self.settings.SettingsError):
                with self.settings.admission(self.repo, 'proposals'):
                    pass
            self.assertEqual(authority.read_bytes(), original)
            replacement = authority.with_name('replacement.json')
            replacement.write_bytes(original)
            replacement.replace(authority)
            with self.assertRaises(self.settings.SettingsError):
                with self.settings.admission(self.repo, 'workers', revision=2):
                    pass
            self.assertTrue(self.settings.status(self.repo)['enabled'])
            self.assertFalse(self.settings.disable(self.repo, dict(expected_revision=2))['enabled'])
            renewed = self.settings.configure(self.repo, dict(self.request, routes=['workers'], expected_revision=3))
            self.assertFalse(renewed['enabled'])
            self.assertTrue(self.settings.enable(self.repo, dict(expected_revision=4, consent_version=2))['enabled'])
            self.assertFalse(any(storage.rglob('runtime.sqlite3')))

    def test_file_only_status_and_disable_require_no_runtime(self):
        authority = self.repo / startup.PROJECT_FILE
        project = json.loads(authority.read_bytes())
        project['repositories'][0]['checkouts'][0]['clients'][0]['configuration']['runtime'] = None
        authority.write_text(json.dumps(project))
        self.assertFalse(self.settings.status(self.repo)['configured'])
        self.assertFalse(self.settings.disable(self.repo, dict(expected_revision=0))['configured'])
        self.assertFalse(self.path.parent.exists())

    def test_recovery_rechecks_namespace_before_atomic_disable(self):
        self.enabled()
        before = self.path.read_bytes()
        atomic = changes._atomic
        def replace_database(*args, **kwargs):
            folder = self.path.parent
            folder.rename(folder.with_name('retired-openrouter'))
            folder.mkdir(mode=0o700)
            return atomic(*args, **kwargs)
        with patch.object(changes, '_atomic', side_effect=replace_database):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.disable(self.repo, dict(expected_revision=2))
        self.assertEqual((self.path.parent.with_name('retired-openrouter') / self.path.name).read_bytes(), before)
        self.assertFalse(self.path.exists())

    def test_recovery_cli_needs_no_qualification_or_valid_runtime_database(self):
        self.enabled()
        self._openrouter_observations.clear()
        database = self.scope.runtime.state_dir / 'runtime.sqlite3'
        database.write_bytes(b'broken runtime database')
        for action, request in (('status', {}), ('disable', dict(expected_revision=2))):
            with self.subTest(action=action):
                result = subprocess.run(fixture_command(self, PACKAGE / 'scripts/openrouter.py', action, '--cwd', self.repo),
                    input=json.dumps(request), text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, {'stdout': result.stdout, 'stderr': result.stderr})
                self.assertEqual(json.loads(result.stdout)['enabled'], action == 'status')

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

    def test_missing_or_replaced_database_does_not_block_opt_out_or_repin(self):
        self.enabled()
        old = self.path.read_bytes()
        database = self.scope.runtime.state_dir / 'runtime.sqlite3'
        saved = database.with_suffix('.saved')
        database.rename(saved)
        self.assertTrue(self.settings.status(self.repo)['enabled'])
        self.assertFalse(database.exists())
        database.write_bytes(saved.read_bytes())
        database.chmod(0o600)
        self.assertTrue(self.settings.status(self.repo)['enabled'])
        self.assertEqual(self.path.read_bytes(), old)
        self.assertFalse(self.settings.disable(self.repo, dict(expected_revision=2))['enabled'])
        self.assertEqual(json.loads(self.path.read_bytes())['binding'], json.loads(old)['binding'])

    def test_benign_enrollment_and_database_updates_preserve_consent(self):
        self.enabled()
        enrollment = self.repo / startup.PROJECT_FILE
        record = json.loads(enrollment.read_text())
        enrollment.write_text(json.dumps(record, indent=2))
        scope = self.settings.resolve(self.repo)
        state.start_task(scope, dict(client='codex', session_id='other-session', task_id='other-task',
            scope_ref='test:scope', mode='read-only', decision_ref='test:scope'))
        self.assertTrue(self.settings.status(self.repo)['enabled'])
        self.assertEqual(self.settings.status(self.repo)['revision'], 2)

    def test_copied_settings_do_not_enable_another_enrolled_checkout(self):
        self.enabled()
        other_scope = next(acquired.resolution.scope for root, acquired in self._openrouter_members.items()
                           if root != str(self.repo))
        other = other_scope.repository_root
        folder = other_scope.runtime.state_dir / 'openrouter'
        folder.mkdir(mode=0o700, exist_ok=True)
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
        script = PACKAGE / 'scripts/openrouter.py'
        self.assertTrue(script.exists(), 'management CLI must exist')
        configured = self.configured()
        result = subprocess.run(fixture_command(self, script, 'enable', '--cwd', self.repo),
            input=json.dumps({'expected_revision': configured['revision'], 'consent_version': 2}), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, {'stdout': result.stdout, 'stderr': result.stderr})
        self.assertTrue(json.loads(result.stdout)['enabled'])
        result = subprocess.run(fixture_command(self, script, 'status', '--cwd', self.repo),
            input='', text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, {'stdout': result.stdout, 'stderr': result.stderr})
        self.assertTrue(json.loads(result.stdout)['enabled'])


if __name__ == '__main__': unittest.main()
