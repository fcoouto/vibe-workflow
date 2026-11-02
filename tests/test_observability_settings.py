"""Optional settings use the existing private authority without changing its data."""

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import startup, state
from tests.openrouter_fixtures import enroll
from tests.test_langfuse import protocol_server


PACKAGE = Path(__file__).resolve().parents[1]
CONFIGURE = dict(expected_revision=0, destination="https://cloud.langfuse.com", langfuse_project_id="lf-project")


class ObservabilitySettingsTests(unittest.TestCase):
    def setUp(self):
        from runtime import observability_settings
        self.settings = observability_settings
        self.scope = enroll(self)
        self.fixture = self._optional_fixtures[-1]
        self.directory = self.scope.runtime.state_dir / "observability"
        self.path = self.directory / (state._scope_id(self.scope) + ".json")

    def core_digest(self):
        return hashlib.sha256((self.scope.runtime.state_dir / "runtime.sqlite3").read_bytes()).hexdigest()

    def test_configure_is_off_private_and_does_not_mutate_core_authority(self):
        before = self.core_digest()
        report = self.settings.configure(self.scope, CONFIGURE)
        self.assertTrue(report["configured"])
        self.assertFalse(report["desired_opt_in"])
        self.assertEqual(report["revision"], 1)
        self.assertEqual(report["control_scope"], "checkout")
        self.assertEqual(report["collection"], "unavailable")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.core_digest(), before)
        raw = self.path.read_text()
        self.assertNotIn("secret", raw.lower())
        self.assertNotIn("measurement", raw)
        self.assertNotIn("probe", raw)
        self.assertEqual(set(json.loads(raw)), {"version", "revision", "binding", "destination",
            "langfuse_project_id", "desired_opt_in", "connection_verified_at"})

    def test_saved_active_flags_without_owner_qualification_cannot_access_settings(self):
        from tests.fixture_canonical_birth import _ACTUAL_ACQUIRE
        self.settings.configure(self.scope, CONFIGURE)
        before = self.path.read_bytes(), self.core_digest()
        with patch.object(startup, 'acquire_project_entry', _ACTUAL_ACQUIRE):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.status(self.scope)
        self.assertEqual((self.path.read_bytes(), self.core_digest()), before)

    def test_sibling_activation_change_preserves_selected_checkout_consent(self):
        self._canonical_peer = True
        scope = enroll(self)
        self.settings.configure(scope, CONFIGURE)
        with patch("runtime.langfuse.verify_project", return_value=None):
            self.settings.enable(scope, dict(expected_revision=1))
        path = scope.runtime.state_dir / "observability" / (state._scope_id(scope) + ".json")
        before = path.read_bytes()
        record = json.loads(scope.authority_path.read_bytes())
        sibling = next(repository for repository in record['repositories']
                       if repository['repository_id'] != scope.repository_id)
        sibling['checkouts'][0]['clients'][0]['activation'].update(status='configured',
            qualification='pending', accepted_scope_digest=None, transaction_id=None,
            evidence=[], qualification_binding=None)
        record['revision'] += 1
        scope.authority_path.write_text(json.dumps(record))
        current = startup._entry_scope(startup.acquire_project_entry(
            SimpleNamespace(client='codex', cwd=scope.repository_root), PACKAGE))
        self.assertEqual(current.accepted_scope_digest, scope.accepted_scope_digest)
        self.assertTrue(self.settings.status(current)['desired_opt_in'])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.settings.disable(current, dict(expected_revision=2))['desired_opt_in'])

    def test_selected_membership_change_rejects_saved_consent_without_writes(self):
        self.settings.configure(self.scope, CONFIGURE)
        before = self.path.read_bytes(), self.core_digest()
        record = json.loads(self.scope.authority_path.read_bytes())
        selected = next(repository for repository in record['repositories']
                        if repository['repository_id'] == self.scope.repository_id)
        selected['membership'] = 'removed'
        record['revision'] += 1
        self.scope.authority_path.write_text(json.dumps(record))
        for operation in (lambda: self.settings.status(self.scope),
                          lambda: self.settings.enable(self.scope, dict(expected_revision=1))):
            with self.assertRaises(self.settings.SettingsError):
                operation()
        self.assertEqual((self.path.read_bytes(), self.core_digest()), before)

    def test_uninitialized_authority_is_never_bootstrapped(self):
        uninitialized = replace(self.scope, runtime=startup.RuntimeSettings(3, self.fixture.base / "missing-state"))
        for operation, request in ((self.settings.configure, CONFIGURE), (self.settings.status, None)):
            with self.subTest(operation=operation.__name__), self.assertRaises(self.settings.SettingsError):
                operation(uninitialized, request) if request else operation(uninitialized)
        self.assertFalse(uninitialized.runtime.state_dir.exists())

    def test_invalid_or_stale_requests_cannot_change_settings_or_contact_provider(self):
        self.settings.configure(self.scope, CONFIGURE)
        before = self.path.read_bytes()
        invalid = ({}, dict(expected_revision=True), dict(expected_revision=-1),
                   dict(expected_revision=0), dict(expected_revision=1, extra="PRIVATE"))
        with patch("runtime.langfuse.verify_project", side_effect=AssertionError("network")):
            for request in invalid:
                with self.subTest(request=request), self.assertRaises(self.settings.SettingsError):
                    self.settings.enable(self.scope, request)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_credentials_leave_desired_opt_in_off(self):
        self.settings.configure(self.scope, CONFIGURE)
        with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}):
            with self.assertRaises(startup.StartupError):
                self.settings.enable(self.scope, dict(expected_revision=1))
        self.assertFalse(self.settings.status(self.scope)["desired_opt_in"])
        self.assertEqual(self.settings.status(self.scope)["revision"], 1)

    def test_disabled_probe_never_reads_credentials_or_contacts_provider(self):
        self.settings.configure(self.scope, CONFIGURE)
        with patch("runtime.langfuse.synthetic_probe", side_effect=AssertionError("network")):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.probe(self.scope, dict(expected_revision=1))

    def test_enable_is_fresh_verification_and_status_never_refreshes_it(self):
        self.settings.configure(self.scope, CONFIGURE)
        with patch("runtime.langfuse.verify_project", return_value=None) as verify:
            report = self.settings.enable(self.scope, dict(expected_revision=1))
            verify.assert_called_once_with(CONFIGURE["destination"], CONFIGURE["langfuse_project_id"])
        self.assertTrue(report["desired_opt_in"])
        self.assertEqual(report["collection"], "unavailable")
        before = self.path.read_bytes()
        with patch("runtime.langfuse.verify_project", side_effect=AssertionError("network")):
            report = self.settings.status(self.scope)
        self.assertEqual(report["connection"], "not_checked")
        self.assertIsInstance(report["connection_verified_at"], int)
        self.assertEqual(self.path.read_bytes(), before)

    def test_disable_uses_no_credentials_or_network_and_prevents_further_probe(self):
        self.settings.configure(self.scope, CONFIGURE)
        with patch("runtime.langfuse.verify_project", return_value=None):
            self.settings.enable(self.scope, dict(expected_revision=1))
        with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}), \
                patch("runtime.langfuse.synthetic_probe", side_effect=AssertionError("network")), \
                patch("runtime.langfuse.verify_project", side_effect=AssertionError("network")):
            report = self.settings.disable(self.scope, dict(expected_revision=2))
            self.assertFalse(report["desired_opt_in"])
            self.assertEqual(report["independent_forwarding"], "unknown_unmanaged")
            self.assertEqual(report["remote_history"], "unchanged")
            with self.assertRaises(self.settings.SettingsError):
                self.settings.probe(self.scope, dict(expected_revision=3))

    def test_live_destination_cannot_change_before_disable(self):
        self.settings.configure(self.scope, CONFIGURE)
        with patch("runtime.langfuse.verify_project", return_value=None):
            self.settings.enable(self.scope, dict(expected_revision=1))
        before = self.path.read_bytes()
        with self.assertRaises(self.settings.SettingsError):
            self.settings.configure(self.scope, dict(CONFIGURE, expected_revision=2, destination="https://example.com"))
        self.assertEqual(self.path.read_bytes(), before)

    def test_changed_policy_or_physical_checkout_invalidates_stored_consent(self):
        self.settings.configure(self.scope, CONFIGURE)
        for scope in (replace(self.scope, policy_digest="a" * 64),):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.status(scope)
        moved = self.fixture.repo.with_name("original-project")
        self.fixture.repo.rename(moved)
        self.fixture.repo.mkdir()
        (moved / ".git").rename(self.fixture.repo / ".git")
        (moved / ".vibe-coding-workflow").rename(self.fixture.repo / ".vibe-coding-workflow")
        with self.assertRaises(self.settings.SettingsError):
            self.settings.status(self.scope)

    def test_unsafe_config_files_and_private_parent_are_rejected(self):
        self.settings.configure(self.scope, CONFIGURE)
        original = self.path.read_bytes()
        foreign = self.fixture.base / "foreign"
        foreign.write_bytes(original)
        foreign.chmod(0o600)
        self.path.unlink()
        self.path.symlink_to(foreign)
        with self.assertRaises(self.settings.SettingsError):
            self.settings.status(self.scope)
        self.path.unlink()
        os.link(foreign, self.path)
        with self.assertRaises(self.settings.SettingsError):
            self.settings.status(self.scope)
        self.path.unlink()
        self.path.write_bytes(original)
        self.path.chmod(0o644)
        with self.assertRaises(self.settings.SettingsError):
            self.settings.status(self.scope)
        self.path.chmod(0o600)
        self.directory.chmod(0o755)
        with self.assertRaises(self.settings.SettingsError):
            self.settings.status(self.scope)
        self.assertEqual(foreign.read_bytes(), original)

    def test_corrupt_unknown_and_oversized_records_fail_closed(self):
        self.settings.configure(self.scope, CONFIGURE)
        original = json.loads(self.path.read_text())
        for raw in ('{"version":1,"version":1}', json.dumps(dict(original, unknown="PRIVATE")),
                    " " * (16 * 1024), json.dumps(dict(original, revision=True))):
            self.path.write_text(raw)
            with self.subTest(size=len(raw)), self.assertRaises(self.settings.SettingsError):
                self.settings.status(self.scope)

    def test_busy_lock_does_not_write_or_wait_for_a_network_action(self):
        import fcntl
        self.settings.configure(self.scope, CONFIGURE)
        lock_path = self.path.with_suffix(".lock")
        before = self.path.read_bytes()
        with lock_path.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(self.settings.SettingsError):
                self.settings.disable(self.scope, dict(expected_revision=1))
        self.assertEqual(self.path.read_bytes(), before)

    def test_atomic_write_syncs_file_and_parent_without_leaving_temporary_files(self):
        original = os.fsync
        descriptors = []
        def sync(descriptor):
            descriptors.append(os.fstat(descriptor).st_mode)
            return original(descriptor)
        with patch("os.fsync", side_effect=sync):
            self.settings.configure(self.scope, CONFIGURE)
        import stat
        self.assertTrue(any(stat.S_ISREG(mode) for mode in descriptors))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in descriptors))
        self.assertEqual({path.suffix for path in self.directory.iterdir()}, {".json", ".lock"})

    def test_replaced_lock_during_connection_check_cannot_commit_consent(self):
        self.settings.configure(self.scope, CONFIGURE)
        before = self.path.read_bytes()
        def replace_lock(*args):
            lock = self.path.with_suffix(".lock")
            lock.unlink()
            lock.write_bytes(b"")
            lock.chmod(0o600)
        with patch("runtime.langfuse.verify_project", side_effect=replace_lock):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.enable(self.scope, dict(expected_revision=1))
        self.assertEqual(self.path.read_bytes(), before)

    def test_binding_requires_exact_json_types(self):
        self.settings.configure(self.scope, CONFIGURE)
        record = json.loads(self.path.read_text())
        record["binding"]["authority_directory"]["device"] = float(record["binding"]["authority_directory"]["device"])
        self.path.write_text(json.dumps(record))
        with self.assertRaises(self.settings.SettingsError):
            self.settings.status(self.scope)

    def test_changed_config_during_verification_is_never_overwritten(self):
        self.settings.configure(self.scope, CONFIGURE)
        replacement = json.loads(self.path.read_text())
        replacement["revision"] = 700
        raw = json.dumps(replacement).encode()
        def replace_config(*args):
            foreign = self.directory / "replacement"
            foreign.write_bytes(raw)
            foreign.chmod(0o600)
            foreign.replace(self.path)
        with patch("runtime.langfuse.verify_project", side_effect=replace_config):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.enable(self.scope, dict(expected_revision=1))
        self.assertEqual(self.path.read_bytes(), raw)

    def test_replaced_parent_during_verification_preserves_both_directories(self):
        self.settings.configure(self.scope, CONFIGURE)
        original = self.path.read_bytes()
        previous = self.directory.with_name("previous-observability")
        def replace_parent(*args):
            self.directory.rename(previous)
            self.directory.mkdir(mode=0o700)
            (self.directory / "foreign").write_text("PRIVATE")
        with patch("runtime.langfuse.verify_project", side_effect=replace_parent):
            with self.assertRaises(self.settings.SettingsError):
                self.settings.enable(self.scope, dict(expected_revision=1))
        self.assertEqual((previous / self.path.name).read_bytes(), original)
        self.assertEqual((self.directory / "foreign").read_text(), "PRIVATE")
        self.assertEqual(list(self.directory.iterdir()), [self.directory / "foreign"])

    def test_failed_replacement_preserves_consent_and_cleans_own_temporary(self):
        self.settings.configure(self.scope, CONFIGURE)
        original = self.path.read_bytes()
        with patch("runtime.langfuse.verify_project", return_value=None), \
                patch("os.replace", side_effect=OSError("PRIVATE")):
            with self.assertRaises(self.settings.SettingsError) as caught:
                self.settings.enable(self.scope, dict(expected_revision=1))
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual({path.suffix for path in self.directory.iterdir()}, {".json", ".lock"})

    def _probe_with_changed_owner(self, change):
        with protocol_server() as server:
            self.settings.configure(self.scope, dict(CONFIGURE, destination=server.url))
            self.settings.enable(self.scope, dict(expected_revision=1))
            changes = []
            def during_verification():
                change()
                changes.append(True)
            server.on_projects = during_verification
            with patch("runtime.langfuse.READ_ATTEMPTS", 1), self.assertRaises(self.settings.SettingsError):
                self.settings.probe(self.scope, dict(expected_revision=2))
            self.assertEqual(changes, [True])
            self.assertFalse(any(request["method"] == "POST" for request in server.requests))

    def test_probe_cannot_post_if_configuration_is_disabled_during_verification(self):
        def disable_configuration():
            record = json.loads(self.path.read_text())
            self.path.write_text(json.dumps(dict(record, desired_opt_in=False, revision=3)))
        self._probe_with_changed_owner(disable_configuration)

    def test_probe_cannot_post_if_lock_is_replaced_during_verification(self):
        def replace_lock():
            lock = self.path.with_suffix(".lock")
            lock.unlink()
            lock.write_bytes(b"")
            lock.chmod(0o600)
        self._probe_with_changed_owner(replace_lock)

    def test_probe_cannot_post_if_authority_is_replaced_during_verification(self):
        def replace_authority():
            authority = self.scope.runtime.state_dir / "runtime.sqlite3"
            replacement = authority.with_name("replacement.sqlite3")
            replacement.write_bytes(authority.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(authority)
        self._probe_with_changed_owner(replace_authority)

    def test_probe_cannot_post_if_parent_is_replaced_during_verification(self):
        def replace_parent():
            self.directory.rename(self.directory.with_name("previous-observability"))
            self.directory.mkdir(mode=0o700)
        self._probe_with_changed_owner(replace_parent)

    def _select_os_store(self):
        try:
            return self.settings.configure(self.scope, dict(CONFIGURE, credential_source="os-store"))
        except self.settings.SettingsError:
            self.fail("Explicit OS credential source is not supported")

    def test_legacy_records_keep_environment_mode_without_any_store_query(self):
        self.settings.configure(self.scope, CONFIGURE)
        before = self.path.read_bytes()
        with patch("runtime.observability_credentials.load", side_effect=AssertionError("store")), \
                patch("runtime.observability_credentials.check_available", side_effect=AssertionError("store")):
            report = self.settings.status(self.scope)
            self.settings.disable(self.scope, dict(expected_revision=1))
        self.assertEqual(report.get("credential_source"), "environment")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(json.loads(before)["version"], 1)

    def test_os_source_is_explicit_and_status_disable_do_not_touch_it(self):
        report = self._select_os_store()
        self.assertEqual(report["credential_source"], "os-store")
        self.assertEqual(json.loads(self.path.read_text())["version"], 2)
        with patch("runtime.observability_credentials.load", side_effect=AssertionError("store")), \
                patch("runtime.observability_credentials.check_available", side_effect=AssertionError("store")):
            self.assertEqual(self.settings.status(self.scope)["credential_source"], "os-store")
            self.settings.disable(self.scope, dict(expected_revision=1))

    def test_os_lookup_failure_never_uses_environment_credentials(self):
        from runtime.observability_credentials import CredentialError
        self._select_os_store()
        with patch.dict(os.environ, LANGFUSE_PUBLIC_KEY="pk-lf-unrelated", LANGFUSE_SECRET_KEY="sk-lf-unrelated"), \
                patch("runtime.observability_credentials.load", side_effect=CredentialError("saved_credentials_unavailable")), \
                patch("runtime.langfuse.verify_project") as verify:
            with self.assertRaises(CredentialError):
                self.settings.enable(self.scope, dict(expected_revision=1))
            verify.assert_not_called()
        self.assertFalse(self.settings.status(self.scope)["desired_opt_in"])

    def test_selected_pair_is_supplied_only_after_the_owner_is_rechecked(self):
        self._select_os_store()
        pair = ("pk-lf-synthetic", "sk-lf-synthetic")
        with patch("runtime.observability_credentials.load", return_value=pair), \
                patch("runtime.langfuse.verify_project", return_value=None) as verify:
            report = self.settings.enable(self.scope, dict(expected_revision=1))
        verify.assert_called_once_with(CONFIGURE["destination"], CONFIGURE["langfuse_project_id"], credentials=pair)
        self.assertTrue(report["desired_opt_in"])
        self.assertEqual(report["credential_source"], "os-store")

    def test_scope_change_during_key_lookup_prevents_any_account_request(self):
        self._select_os_store()
        def changed_during_lookup(*args):
            record = json.loads(self.path.read_text())
            self.path.write_text(json.dumps(dict(record, revision=70)))
            return ("pk-lf-synthetic", "sk-lf-synthetic")
        with patch("runtime.observability_credentials.load", side_effect=changed_during_lookup), \
                patch("runtime.langfuse.verify_project") as verify:
            with self.assertRaises(self.settings.SettingsError):
                self.settings.enable(self.scope, dict(expected_revision=1))
            verify.assert_not_called()

    def test_store_verifies_pair_then_saves_without_enabling_or_sending_a_span(self):
        self._select_os_store()
        pair = ("pk-lf-synthetic", "sk-lf-synthetic")
        events = []
        with patch("runtime.observability_credentials.check_available", return_value=None), \
                patch("runtime.langfuse.verify_project", side_effect=lambda *a, **k: events.append("verify")), \
                patch("runtime.observability_credentials.store", side_effect=lambda *a, **k: events.append("store")) as save, \
                patch("runtime.langfuse.synthetic_probe", side_effect=AssertionError("POST")):
            report = self.settings.store_credentials(self.scope, dict(expected_revision=1), lambda: pair)
        self.assertEqual(events, ["verify", "store"])
        self.assertFalse(report["desired_opt_in"])
        self.assertEqual(report["revision"], 2)
        self.assertIn("before_change", save.call_args.kwargs)
        self.assertNotIn(pair[1], json.dumps(report))

    def test_wrong_project_verification_cannot_save_a_pair(self):
        from runtime.langfuse import LangfuseError
        self._select_os_store()
        before = self.path.read_bytes()
        with patch("runtime.observability_credentials.check_available", return_value=None), \
                patch("runtime.langfuse.verify_project", side_effect=LangfuseError("langfuse_project_mismatch")), \
                patch("runtime.observability_credentials.store") as save:
            with self.assertRaises(LangfuseError):
                self.settings.store_credentials(self.scope, dict(expected_revision=1),
                    lambda: ("pk-lf-synthetic", "sk-lf-synthetic"))
            save.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_stale_store_request_does_not_prompt_or_check_a_backend(self):
        self._select_os_store()
        with patch("runtime.observability_credentials.check_available") as check, \
                patch("runtime.observability_credentials.store") as save:
            def forbidden():
                raise AssertionError("secret prompt")
            with self.assertRaises(self.settings.SettingsError):
                self.settings.store_credentials(self.scope, dict(expected_revision=0), forbidden)
            check.assert_not_called()
            save.assert_not_called()

    def test_forget_is_explicit_keeps_os_mode_and_does_not_verify_or_load_keys(self):
        self._select_os_store()
        with patch("runtime.observability_credentials.forget", return_value=None) as forget, \
                patch("runtime.observability_credentials.load", side_effect=AssertionError("lookup")), \
                patch("runtime.langfuse.verify_project", side_effect=AssertionError("network")):
            report = self.settings.forget_credentials(self.scope, dict(expected_revision=1))
        self.assertIn("before_change", forget.call_args.kwargs)
        self.assertEqual(report["credential_source"], "os-store")
        self.assertFalse(report["desired_opt_in"])
        self.assertEqual(report["revision"], 2)

    def test_reconfiguration_cannot_silently_downgrade_the_selected_source(self):
        self._select_os_store()
        report = self.settings.configure(self.scope, dict(CONFIGURE, expected_revision=1))
        self.assertEqual(report["credential_source"], "os-store")
        self.assertEqual(json.loads(self.path.read_text())["version"], 2)

    def test_active_consent_blocks_store_and_forget_before_prompt_or_backend(self):
        self._select_os_store()
        with patch("runtime.observability_credentials.load", return_value=("pk-lf-synthetic", "sk-lf-synthetic")), \
                patch("runtime.langfuse.verify_project", return_value=None):
            self.settings.enable(self.scope, dict(expected_revision=1))
        with patch("runtime.observability_credentials.check_available") as check, \
                patch("runtime.observability_credentials.forget") as forget:
            def forbidden():
                raise AssertionError("secret prompt")
            with self.assertRaises(self.settings.SettingsError):
                self.settings.store_credentials(self.scope, dict(expected_revision=2), forbidden)
            with self.assertRaises(self.settings.SettingsError):
                self.settings.forget_credentials(self.scope, dict(expected_revision=2))
            check.assert_not_called()
            forget.assert_not_called()

    def test_versioned_source_fields_remain_strict(self):
        self._select_os_store()
        original = json.loads(self.path.read_text())
        invalid = [dict(original, credential_source=value) for value in (None, True, [], "default")]
        invalid += [dict(original, version=1), {key: value for key, value in original.items() if key != "credential_source"}]
        for record in invalid:
            self.path.write_text(json.dumps(record))
            with self.assertRaises(self.settings.SettingsError):
                self.settings.status(self.scope)

    def test_scope_change_during_secret_entry_prevents_verification_and_write(self):
        self._select_os_store()
        def changed_during_entry():
            record = json.loads(self.path.read_text())
            self.path.write_text(json.dumps(dict(record, revision=700)))
            return ("pk-lf-synthetic", "sk-lf-synthetic")
        with patch("runtime.observability_credentials.check_available", return_value=None), \
                patch("runtime.langfuse.verify_project") as verify, \
                patch("runtime.observability_credentials.store") as save:
            with self.assertRaises(self.settings.SettingsError):
                self.settings.store_credentials(self.scope, dict(expected_revision=1), changed_during_entry)
            verify.assert_not_called()
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
