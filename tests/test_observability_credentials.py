"""Native store contracts use isolated fakes; no OS entry or live account access."""

import hashlib
from importlib.metadata import PackageNotFoundError, distribution
import importlib.util
import json
import os
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


DESTINATION = "https://cloud.langfuse.com"
PROJECT = "lf-project"
PAIR = ("pk-lf-synthetic", "sk-lf-synthetic")


class ObservabilityCredentialsTests(unittest.TestCase):
    def setUp(self):
        try:
            from runtime import observability_credentials
        except ImportError:
            self.fail("OS credential storage is not implemented")
        self.credentials = observability_credentials
        self.entries, self.calls = {}, []
        entries, calls = self.entries, self.calls
        errors, api = ModuleType("keyring.errors"), ModuleType("keyring.backends.macOS.api")

        class PasswordDeleteError(Exception):
            pass

        class Error(Exception):
            pass

        class NotFound(Error):
            pass

        errors.PasswordDeleteError, api.Error, api.NotFound = PasswordDeleteError, Error, NotFound
        self.errors, self.api = errors, api

        class NativeBackend:
            priority = 5
            keychain = None
            name = None

            def __init__(self):
                calls.append(("construct", self.name))

            def get_password(self, service, username):
                calls.append(("get", service, username))
                return entries.get((service, username))

            def set_password(self, service, username, password):
                calls.append(("set", service, username))
                entries[(service, username)] = password

            def delete_password(self, service, username):
                calls.append(("delete", service, username))
                if (service, username) not in entries:
                    # The pinned macOS backend wraps the item-not-found status this way.
                    try:
                        raise NotFound(-25300, "PRIVATE missing record")
                    except NotFound as error:
                        raise PasswordDeleteError("PRIVATE " + str(error)) from error
                del entries[(service, username)]

        class MacBackend(NativeBackend):
            name = "macos"

        class WindowsBackend(NativeBackend):
            name = "windows"

        self.mac, self.windows = MacBackend, WindowsBackend
        root, backends = ModuleType("keyring"), ModuleType("keyring.backends")
        root.__path__, backends.__path__ = [], []
        def forbidden(*args):
            raise AssertionError("Default keyring or configuration discovery was used")
        for name in ("get_keyring", "set_keyring", "get_password", "set_password", "delete_password"):
            setattr(root, name, forbidden)
        mac, windows = ModuleType("keyring.backends.macOS"), ModuleType("keyring.backends.Windows")
        mac.Keyring, windows.WinVaultKeyring, mac.api = MacBackend, WindowsBackend, api
        modules = {"keyring": root, "keyring.backends": backends, "keyring.errors": errors,
                   "keyring.backends.macOS": mac, "keyring.backends.macOS.api": api,
                   "keyring.backends.Windows": windows}
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, modules).start()
        patch.object(self.credentials, "version", return_value="25.7.0").start()
        patch.object(sys, "platform", "darwin").start()
        clean = {key: value for key, value in os.environ.items()
                 if "KEYRING_PROPERTY_" not in key and key != "KEYCHAIN_PATH"}
        patch.dict(os.environ, clean, clear=True).start()

    def test_selects_only_the_explicit_native_platform_backend(self):
        for platform, selected in (("darwin", "macos"), ("win32", "windows")):
            self.calls.clear()
            with patch.object(sys, "platform", platform):
                self.assertIsNone(self.credentials.check_available())
            self.assertEqual(self.calls, [("construct", selected)])

    def test_distinct_connections_have_distinct_targets_without_private_identifiers(self):
        reference = self.credentials.reference(DESTINATION, PROJECT)
        self.assertEqual(reference, self.credentials.reference("https://CLOUD.LANGFUSE.COM:443/", PROJECT))
        self.assertNotEqual(reference, self.credentials.reference(DESTINATION, "other-project"))
        self.assertNotEqual(reference, self.credentials.reference("https://us.cloud.langfuse.com", PROJECT))
        self.assertTrue(reference.startswith("vibe-coding-workflow.langfuse."))
        self.assertNotIn(PROJECT, reference)
        self.assertNotIn("cloud.langfuse.com", reference)
        self.assertEqual(self.calls, [])

    def test_one_bounded_blob_round_trips_and_forgets_only_selected_connection(self):
        self.credentials.store(DESTINATION, PROJECT, PAIR, before_change=lambda: None)
        self.credentials.store(DESTINATION, "other-project", PAIR, before_change=lambda: None)
        self.assertEqual(len(self.entries), 2)
        for raw in self.entries.values():
            self.assertLessEqual(len(raw.encode()), 2048)
            self.assertEqual(json.loads(raw), dict(version=1, public_key=PAIR[0], secret_key=PAIR[1]))
        self.assertEqual(self.credentials.load(DESTINATION, PROJECT), PAIR)
        self.credentials.forget(DESTINATION, PROJECT, before_change=lambda: None)
        self.assertEqual(self.credentials.load(DESTINATION, "other-project"), PAIR)
        self.assertEqual(len(self.entries), 1)

    def test_missing_saved_pair_never_falls_back_to_environment_keys(self):
        with patch.dict(os.environ, LANGFUSE_PUBLIC_KEY=PAIR[0], LANGFUSE_SECRET_KEY=PAIR[1]):
            with self.assertRaises(self.credentials.CredentialError) as caught:
                self.credentials.load(DESTINATION, PROJECT)
        self.assertEqual(str(caught.exception), "saved_credentials_unavailable")

    def test_unsupported_platform_does_not_construct_a_backend(self):
        with patch.object(sys, "platform", "linux"), self.assertRaises(self.credentials.CredentialError):
            self.credentials.check_available()
        self.assertEqual(self.calls, [])

    def test_missing_or_below_floor_dependency_fails_before_backend_construction(self):
        for effect in (PackageNotFoundError("keyring"), None):
            with patch.object(self.credentials, "version", side_effect=effect, return_value="25.6.9"):
                with self.assertRaises(self.credentials.CredentialError):
                    self.credentials.check_available()
        self.assertEqual(self.calls, [])

    def test_newer_keyring_releases_keep_the_explicit_native_backend(self):
        for installed in ("25.7.1", "25.8.0", "26.0.0", "99.0", "unknown"):
            with self.subTest(version=installed), patch.object(self.credentials, "version", return_value=installed):
                self.credentials.check_available()
        self.assertEqual(self.calls, [("construct", "macos")] * 5)

    def test_actual_missing_native_backend_api_is_still_rejected(self):
        with patch.object(self.mac, "get_password", None), self.assertRaises(self.credentials.CredentialError):
            self.credentials.check_available()

    def test_backend_configuration_is_not_selected_and_property_redirection_is_rejected(self):
        with patch.dict(os.environ, PYTHON_KEYRING_BACKEND="PRIVATE.UnsafeBackend"):
            self.credentials.check_available()
        self.assertEqual(self.calls, [("construct", "macos")])
        for name in ("KEYRING_PROPERTY_KEYCHAIN", "PREFIX_KEYRING_PROPERTY_KEYCHAIN", "KEYCHAIN_PATH"):
            self.calls.clear()
            with patch.dict(os.environ, {name: "PRIVATE"}), self.assertRaises(self.credentials.CredentialError):
                self.credentials.check_available()
            self.assertEqual(self.calls, [])

    def test_cached_nondefault_keychain_is_not_used(self):
        with patch.object(self.mac, "keychain", "PRIVATE"), self.assertRaises(self.credentials.CredentialError):
            self.credentials.load(DESTINATION, PROJECT)
        self.assertFalse(any(call[0] == "get" for call in self.calls))

    def test_corrupt_oversized_or_extra_secret_data_is_rejected_without_echo(self):
        target = self.credentials.reference(DESTINATION, PROJECT)
        for raw in ("PRIVATE", " " * 2049, '{"version":1,"version":1}',
                    json.dumps(dict(version=True, public_key=PAIR[0], secret_key=PAIR[1])),
                    json.dumps(dict(version=1, public_key=PAIR[0], secret_key=PAIR[1], private="PRIVATE"))):
            self.entries[(target, "project-keys")] = raw
            with self.subTest(size=len(raw)), self.assertRaises(self.credentials.CredentialError) as caught:
                self.credentials.load(DESTINATION, PROJECT)
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertNotIn(PAIR[1], str(caught.exception))

    def test_invalid_target_or_pair_is_rejected_before_store_access(self):
        for pair in ((PAIR[0], "PRIVATE\n"), (PAIR[0], ""), PAIR + ("extra",), "PRIVATE"):
            with self.assertRaises(self.credentials.CredentialError):
                self.credentials.store(DESTINATION, PROJECT, pair, before_change=lambda: None)
        with self.assertRaises(self.credentials.CredentialError):
            self.credentials.load("https://user:PRIVATE@example.com", PROJECT)
        self.assertEqual(self.calls, [])

    def test_native_errors_are_fixed_and_never_echo_private_library_messages(self):
        for operation, method, arguments in ((self.credentials.load, "get_password", (DESTINATION, PROJECT)),
                (self.credentials.store, "set_password", (DESTINATION, PROJECT, PAIR)),
                (self.credentials.forget, "delete_password", (DESTINATION, PROJECT))):
            with patch.object(self.mac, method, side_effect=RuntimeError("PRIVATE " + PAIR[1])):
                with self.assertRaises(self.credentials.CredentialError) as caught:
                    operation(*arguments, **({} if method == "get_password" else {"before_change": lambda: None}))
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertNotIn(PAIR[1], str(caught.exception))

    def test_native_mutations_recheck_owner_after_backend_selection(self):
        def changed_owner():
            self.calls.append(("guard",))
            raise self.credentials.CredentialError("credential_owner_changed")
        for operation, arguments in ((self.credentials.store, (DESTINATION, PROJECT, PAIR)),
                                     (self.credentials.forget, (DESTINATION, PROJECT))):
            self.calls.clear()
            try:
                with self.assertRaises(self.credentials.CredentialError):
                    operation(*arguments, before_change=changed_owner)
            except TypeError:
                self.fail("Native writes do not accept their held-owner guard")
            self.assertEqual(self.calls, [("construct", "macos"), ("guard",)])

    def test_dependency_metadata_errors_are_redacted(self):
        with patch.object(self.credentials, "version", side_effect=RuntimeError("PRIVATE " + PAIR[1])):
            with self.assertRaises(self.credentials.CredentialError) as caught:
                self.credentials.check_available()
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertNotIn(PAIR[1], str(caught.exception))

    def forget_error(self):
        guard = lambda: self.calls.append(("guard",))
        with self.assertRaises(self.credentials.CredentialError) as caught:
            self.credentials.forget(DESTINATION, PROJECT, before_change=guard)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        return str(caught.exception)

    def test_forget_without_saved_entry_reports_it_unavailable_after_guard_and_without_a_read(self):
        self.credentials.store(DESTINATION, "other-project", PAIR, before_change=lambda: None)
        self.calls.clear()
        self.assertEqual(self.forget_error(), "saved_credentials_unavailable")
        target = self.credentials.reference(DESTINATION, PROJECT)
        self.assertEqual(self.calls, [("construct", "macos"), ("guard",), ("delete", target, "project-keys")])
        self.assertEqual(len(self.entries), 1)

    def test_other_delete_failures_stay_uncertain(self):
        errors, api = self.errors, self.api

        def wrapped(cause):
            def delete(backend, service, username):
                try:
                    raise cause
                except Exception as error:
                    raise errors.PasswordDeleteError("PRIVATE") from error
            return delete

        def bare(backend, service, username):
            raise errors.PasswordDeleteError("PRIVATE")

        def unwrapped(backend, service, username):
            raise api.NotFound(-25300, "PRIVATE")

        def context_only(backend, service, username):
            try:
                raise api.NotFound(-25300, "PRIVATE")
            except api.NotFound:
                raise errors.PasswordDeleteError("PRIVATE")

        def other_type(backend, service, username):
            try:
                raise api.NotFound(-25300, "PRIVATE")
            except api.NotFound as error:
                raise RuntimeError("PRIVATE") from error

        for name, delete in (("denied", wrapped(api.Error(-128, "PRIVATE"))), ("other", wrapped(RuntimeError("PRIVATE"))),
                             ("bare", bare), ("unwrapped", unwrapped), ("context", context_only),
                             ("other_type", other_type)):
            with self.subTest(name=name), patch.object(self.mac, "delete_password", delete):
                self.assertEqual(self.forget_error(), "credential_store_delete_unknown")

    def test_forget_on_windows_without_entry_reports_unavailable_after_guard(self):
        with patch.object(sys, "platform", "win32"), \
                patch.object(self.windows, "_read_credential", lambda backend, target: None, create=True), \
                patch.object(self.windows, "_delete_password", side_effect=AssertionError("delete"), create=True):
            self.assertEqual(self.forget_error(), "saved_credentials_unavailable")
        self.assertEqual(self.calls, [("construct", "windows"), ("guard",)])


class PinnedMacCredentialsTests(unittest.TestCase):
    """Run the unchanged pinned macOS backend with only its Keychain item calls replaced."""

    @classmethod
    def setUpClass(cls):
        if sys.platform != "darwin":
            raise unittest.SkipTest("The pinned macOS backend loads the macOS Security framework")
        try:
            installed = distribution("keyring")
        except PackageNotFoundError:
            raise unittest.SkipTest("Optional pinned keyring is not installed")
        if installed.version != "25.7.0":
            raise unittest.SkipTest("The macOS source regression requires keyring 25.7.0")
        for path, digest in (("keyring/backends/macOS/__init__.py",
                              "f8220e36fc2b2456deba3eb4a296c2319c38c16b621b21ee182b21a1c77835d8"),
                             ("keyring/backends/macOS/api.py",
                              "7a292205a19c602429a83b0d74bcbcc0da01fe714161f638d63ff7f2fb0c2a9a")):
            if hashlib.sha256(installed.locate_file(path).read_bytes()).hexdigest() != digest:
                raise AssertionError("Pinned macOS backend source differs from the official wheel")
        from keyring.backends.macOS import api
        cls.api = api

    def setUp(self):
        from runtime import observability_credentials
        self.credentials = observability_credentials
        self.statuses = []

        def delete(query):
            self.statuses.append("delete")
            return self.status

        def forbidden(*args):
            raise AssertionError("Real Keychain item access")

        clean = {key: value for key, value in os.environ.items()
                 if "KEYRING_PROPERTY_" not in key and key != "KEYCHAIN_PATH"}
        self.assertIs(self.api.delete_generic_password.__globals__, vars(self.api))
        for context in (patch.object(self.api, "SecItemDelete", delete),
                        patch.object(self.api, "SecItemCopyMatching", forbidden),
                        patch.object(self.api, "SecItemAdd", forbidden),
                        patch.dict(os.environ, clean, clear=True)):
            context.start()
            self.addCleanup(context.stop)

    def forget(self):
        self.credentials.forget(DESTINATION, PROJECT, before_change=lambda: self.statuses.append("guard"))

    def test_item_not_found_is_unavailable_after_guard(self):
        self.status = self.api.error.item_not_found
        with self.assertRaisesRegex(self.credentials.CredentialError, "^saved_credentials_unavailable$"):
            self.forget()
        self.assertEqual(self.statuses, ["guard", "delete"])

    def test_other_statuses_stay_uncertain_and_success_returns(self):
        for status in (self.api.error.keychain_denied, -25293, -1):
            self.statuses.clear()
            self.status = status
            with self.subTest(status=status), \
                    self.assertRaisesRegex(self.credentials.CredentialError, "^credential_store_delete_unknown$"):
                self.forget()
            self.assertEqual(self.statuses, ["guard", "delete"])
        self.status = 0
        self.assertIsNone(self.forget())


class PinnedWindowsCredentialsTests(unittest.TestCase):
    """Run the unchanged pinned backend with only its native calls replaced."""

    @classmethod
    def setUpClass(cls):
        try:
            installed = distribution("keyring")
        except PackageNotFoundError:
            raise unittest.SkipTest("Optional pinned keyring is not installed")
        if installed.version != "25.7.0":
            raise unittest.SkipTest("The Windows source regression requires keyring 25.7.0")
        source = installed.locate_file("keyring/backends/Windows.py")
        if hashlib.sha256(source.read_bytes()).hexdigest() != "da98b72d2576442c17acb61e3699485152603cb1f9b8c9f261c481828fba926c":
            raise AssertionError("Pinned Windows backend source differs from the official wheel")
        native = ModuleType("win32cred")
        errors = ModuleType("pywintypes")

        class WindowsError(Exception):
            def __init__(self, winerror, funcname):
                self.winerror, self.funcname = winerror, funcname
                super().__init__("PRIVATE native failure " + PAIR[1])

        errors.error = cls.WindowsError = WindowsError
        package, pywin32 = ModuleType("win32ctypes"), ModuleType("win32ctypes.pywin32")
        package.__path__ = []
        pywin32.pywintypes, pywin32.win32cred = errors, native
        spec = importlib.util.spec_from_file_location("keyring.backends.Windows", source)
        cls.windows = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"win32ctypes": package, "win32ctypes.pywin32": pywin32}):
            spec.loader.exec_module(cls.windows)

    def setUp(self):
        from runtime import observability_credentials
        self.credentials = observability_credentials
        self.entries, self.calls = {}, []
        self.failure, self.fail_after = None, False
        native = ModuleType("win32cred")
        native.CRED_TYPE_GENERIC, native.CRED_PERSIST_ENTERPRISE = 1, 3

        def read(*, Type, TargetName):
            self.calls.append(("read", TargetName))
            if self.failure == "read":
                raise self.WindowsError(5, "CredRead")
            if TargetName not in self.entries:
                raise self.WindowsError(1168, "CredRead")
            return dict(self.entries[TargetName])

        def write(credential, flags):
            self.assertEqual(flags, 0)
            self.assertEqual(credential["Type"], 1)
            self.assertEqual(credential["Persist"], 3)
            self.calls.append(("write", credential["TargetName"]))
            if self.failure != "write" or self.fail_after:
                self.entries[credential["TargetName"]] = dict(
                    credential, CredentialBlob=credential["CredentialBlob"].encode("utf-16"))
            if self.failure == "write":
                raise self.WindowsError(5, "CredWrite")

        def delete(*, Type, TargetName):
            self.calls.append(("delete", TargetName))
            if self.failure != "delete" or self.fail_after:
                self.entries.pop(TargetName, None)
            if self.failure == "delete":
                raise self.WindowsError(5, "CredDelete")

        native.CredRead, native.CredWrite, native.CredDelete = read, write, delete
        clean = {key: value for key, value in os.environ.items()
                 if "KEYRING_PROPERTY_" not in key and key != "KEYCHAIN_PATH"}
        for context in (patch.object(self.windows, "win32cred", native),
                        patch.dict(sys.modules, {"keyring.backends.Windows": self.windows}),
                        patch.object(sys, "platform", "win32"), patch.dict(os.environ, clean, clear=True)):
            context.start()
            self.addCleanup(context.stop)
        self.target = self.credentials.reference(DESTINATION, PROJECT)
        self.guard = lambda: self.calls.append(("guard",))

    def save(self, pair=PAIR):
        self.credentials.store(DESTINATION, PROJECT, pair, before_change=self.guard)

    def test_replacement_keeps_only_current_pair_in_one_native_target(self):
        replacement = ("pk-lf-replacement", "sk-lf-replacement")
        self.save()
        self.save(replacement)
        self.assertEqual(set(self.entries), {self.target})
        self.assertEqual(self.credentials.load(DESTINATION, PROJECT), replacement)
        self.assertNotIn(PAIR[1], self.entries[self.target]["CredentialBlob"].decode("utf-16"))
        self.assertEqual(self.calls[:6], [("read", self.target), ("guard",), ("write", self.target)] * 2)

    def test_forget_reads_and_deletes_only_the_exact_managed_target(self):
        self.save()
        self.entries["unrelated"] = dict(self.entries[self.target])
        self.calls.clear()
        self.credentials.forget(DESTINATION, PROJECT, before_change=self.guard)
        self.assertEqual(set(self.entries), {"unrelated"})
        self.assertEqual(self.calls, [("read", self.target), ("guard",), ("delete", self.target)])

    def test_forget_without_entry_reports_unavailable_after_guard_without_delete(self):
        self.entries["unrelated"] = dict(Type=1, TargetName="unrelated", UserName="project-keys",
                                         CredentialBlob="x".encode("utf-16"))
        with self.assertRaisesRegex(self.credentials.CredentialError, "^saved_credentials_unavailable$"):
            self.credentials.forget(DESTINATION, PROJECT, before_change=self.guard)
        self.assertEqual(self.calls, [("read", self.target), ("guard",)])
        self.assertEqual(set(self.entries), {"unrelated"})

    def test_compound_entry_is_never_a_read_fallback(self):
        self.save()
        alias = "project-keys@" + self.target
        self.entries[alias] = self.entries.pop(self.target)
        self.calls.clear()
        with self.assertRaisesRegex(self.credentials.CredentialError, "^saved_credentials_unavailable$"):
            self.credentials.load(DESTINATION, PROJECT)
        self.assertEqual(self.calls, [("read", self.target)])
        self.assertEqual(set(self.entries), {alias})

    def test_foreign_username_is_preserved_for_all_operations(self):
        self.save()
        self.entries[self.target]["UserName"] = "foreign-owner"
        original = dict(self.entries[self.target])
        for operation in (lambda: self.credentials.load(DESTINATION, PROJECT), self.save,
                          lambda: self.credentials.forget(DESTINATION, PROJECT, before_change=self.guard)):
            self.calls.clear()
            with self.assertRaisesRegex(self.credentials.CredentialError, "^credential_store_target_conflict$"):
                operation()
            self.assertEqual(self.calls, [("read", self.target)])
            self.assertEqual(self.entries, {self.target: original})

    def test_write_failure_reports_uncertainty_without_migration_or_delete(self):
        for after in (False, True):
            self.failure = None
            self.save()
            original = dict(self.entries[self.target])
            self.calls.clear()
            self.failure, self.fail_after = "write", after
            with self.assertRaisesRegex(self.credentials.CredentialError, "^credential_store_write_unknown$"):
                self.save(("pk-lf-new", "sk-lf-new"))
            self.assertEqual(self.calls, [("read", self.target), ("guard",), ("write", self.target)])
            self.assertEqual(set(self.entries), {self.target})
            self.assertEqual(self.entries[self.target] == original, not after)

    def test_delete_failure_reports_uncertainty_without_other_target_access(self):
        for after in (False, True):
            self.failure = None
            self.save()
            self.calls.clear()
            self.failure, self.fail_after = "delete", after
            with self.assertRaisesRegex(self.credentials.CredentialError, "^credential_store_delete_unknown$"):
                self.credentials.forget(DESTINATION, PROJECT, before_change=self.guard)
            self.assertEqual(self.calls, [("read", self.target), ("guard",), ("delete", self.target)])
            self.assertEqual(self.target in self.entries, not after)

    def test_read_failure_stops_before_guard_or_mutation(self):
        self.failure = "read"
        for operation in (lambda: self.credentials.load(DESTINATION, PROJECT), self.save,
                          lambda: self.credentials.forget(DESTINATION, PROJECT, before_change=self.guard)):
            self.calls.clear()
            with self.assertRaisesRegex(self.credentials.CredentialError, "^credential_store_unavailable$"):
                operation()
            self.assertEqual(self.calls, [("read", self.target)])
        self.assertEqual(self.entries, {})

    def test_owner_guard_runs_after_exact_read_and_before_native_mutation(self):
        def changed_owner():
            self.calls.append(("guard",))
            raise self.credentials.CredentialError("credential_owner_changed")
        for operation, arguments in ((self.credentials.store, (DESTINATION, PROJECT, PAIR)),
                                     (self.credentials.forget, (DESTINATION, PROJECT))):
            self.calls.clear()
            with self.assertRaisesRegex(self.credentials.CredentialError, "^credential_owner_changed$"):
                operation(*arguments, before_change=changed_owner)
            self.assertEqual(self.calls, [("read", self.target), ("guard",)])
        self.assertEqual(self.entries, {})


if __name__ == "__main__":
    unittest.main()
