"""Claude Code doctor observations against a fake `claude` executable; nothing here starts a model."""
import json
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime import doctor
from runtime.adapters import claude_code

PACKAGE = Path(__file__).resolve().parents[1]
SECRET = "PRIVATE-OTHER-PLUGIN"


class ClaudeDoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claude doctor ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        (self.repo / ".claude").mkdir(parents=True)
        self.config = self.base / "config"
        self.config.mkdir()
        for target in (patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(self.config)}),
                       patch.object(claude_code, "MANAGED_DIRECTORIES", {})):
            target.start()
            self.addCleanup(target.stop)
        self.scope = SimpleNamespace(repository_root=self.repo, project_id="p")
        self.definitions = json.loads((PACKAGE / "hooks/claude-code.json").read_bytes())["hooks"]

    def executable(self, version="2.1.311 (Claude Code)", plugins=None, plugin_exit=0):
        listing = json.dumps(plugins if plugins is not None else [])
        path = self.base / "claude"
        path.write_text(f"""#!/bin/sh
case "$1" in
  --version) echo '{version}' ;;
  plugin) cat <<'JSON'
{listing}
JSON
    exit {plugin_exit} ;;
esac
""")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def ours(self, **updates):
        return dict(id="vibe-workflow@local", version="1.0.0", scope="user", enabled=True,
                    installPath=str(PACKAGE)) | updates

    def observe(self, executable, scope="default"):
        report = {"native": {"status": "not_checked"}, "hooks": {"status": "not_checked"}}
        scope = self.scope if scope == "default" else scope
        failed, incomplete = claude_code.observe_native(report, scope, executable, self.definitions,
                                                        time.monotonic() + 10)
        return report, failed, incomplete

    def test_hook_locks_read_only_the_documented_controls_from_each_layer(self):
        (self.config / "settings.json").write_text(json.dumps({"disableAllHooks": True, "secret": SECRET}))
        (self.repo / ".claude/settings.local.json").write_text(json.dumps({"disableAllHooks": False}))
        locks = claude_code.hook_locks(self.repo)
        self.assertEqual(locks, {"disabled_by": ["user"], "managed_hooks_only": False, "unreadable": 0})
        self.assertNotIn(SECRET, json.dumps(locks))

    def test_managed_hooks_only_counts_only_from_managed_sources_and_bad_files_are_unreadable(self):
        managed = self.base / "managed"
        (managed / "managed-settings.d").mkdir(parents=True)
        (managed / "managed-settings.json").write_text("{not json")
        (managed / "managed-settings.d/10.json").write_text(json.dumps({"allowManagedHooksOnly": True}))
        (self.repo / ".claude/settings.json").write_text(json.dumps({"allowManagedHooksOnly": True}))
        (self.config / "settings.json").write_bytes(b"{" + b" " * (claude_code.MAX_NATIVE_FILE + 1) + b"}")
        with patch.object(claude_code, "MANAGED_DIRECTORIES", {claude_code.sys.platform: str(managed)}):
            locks = claude_code.hook_locks(self.repo)
        self.assertEqual((locks["managed_hooks_only"], locks["unreadable"], locks["disabled_by"]), (True, 2, []))

    def test_a_fifo_where_a_settings_file_belongs_is_unreadable_and_never_hangs(self):
        import os
        os.mkfifo(self.repo / ".claude/settings.json")
        started = time.monotonic()
        locks = claude_code.hook_locks(self.repo)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(locks["unreadable"], 1)

    def test_plugin_lookup_reports_only_this_package_and_never_other_plugins(self):
        other = dict(id=SECRET, version="9", scope="user", enabled=True, installPath="/elsewhere")
        listed = claude_code.installed_plugin(self.executable(plugins=[other, self.ours()]), PACKAGE, time.monotonic() + 5)
        self.assertEqual(listed, {"status": "enabled", "scope": "user", "version": "1.0.0"})
        self.assertNotIn(SECRET, json.dumps(listed))
        self.assertEqual(claude_code.installed_plugin(self.executable(plugins=[other]), PACKAGE,
                                                      time.monotonic() + 5), {"status": "not_listed"})
        self.assertEqual(claude_code.installed_plugin(self.executable(plugins=[self.ours(enabled=False)]), PACKAGE,
                                                      time.monotonic() + 5)["status"], "disabled")
        with self.assertRaises(OSError):
            claude_code.installed_plugin(self.executable(plugin_exit=1), PACKAGE, time.monotonic() + 5)

    def test_enabled_plugin_is_registered_but_execution_stays_unverified(self):
        report, failed, incomplete = self.observe(self.executable(plugins=[self.ours()]))
        self.assertEqual(report["native"]["version"], "2.1.311")
        hooks = report["hooks"]
        self.assertEqual((hooks["status"], hooks["plugin"]["status"], hooks["trust"]),
                         ("incomplete", "enabled", "not_exposed_by_client"))
        self.assertEqual(set(hooks["events"]), set(self.definitions))
        self.assertEqual(len(hooks["source_digest"]), 64)
        self.assertEqual((failed, incomplete), (False, True))

    def test_disabled_hooks_or_plugin_fail_and_an_unlisted_plugin_is_only_incomplete(self):
        (self.config / "settings.json").write_text(json.dumps({"disableAllHooks": True}))
        report, failed, _ = self.observe(self.executable(plugins=[self.ours()]))
        self.assertEqual((report["hooks"]["status"], failed), ("failed", True))
        (self.config / "settings.json").write_text("{}")
        report, failed, _ = self.observe(self.executable(plugins=[self.ours(enabled=False)]))
        self.assertEqual((report["hooks"]["status"], failed), ("failed", True))
        report, failed, incomplete = self.observe(self.executable(plugins=[]))
        self.assertEqual((report["hooks"]["plugin"]["status"], failed, incomplete), ("not_listed", False, True))

    def test_unknown_version_or_missing_binary_is_incomplete_and_hides_hooks(self):
        report, failed, incomplete = self.observe(self.executable(version="codex-cli 0.163.0"))
        self.assertEqual((report["native"]["status"], report["hooks"], failed, incomplete),
                         ("unknown_version", {"status": "unobserved"}, False, True))
        report, failed, incomplete = self.observe(self.base / "missing")
        self.assertEqual((report["native"]["status"], failed, incomplete), ("unavailable", False, True))

    def test_without_an_enrolled_scope_only_the_version_is_observed(self):
        report, failed, incomplete = self.observe(self.executable(), scope=None)
        self.assertEqual((report["native"]["status"], report["hooks"]["status"], failed, incomplete),
                         ("observed", "not_checked", False, False))

    def test_inspect_runtime_selects_the_claude_package_files_and_exit_contract(self):
        report = doctor.inspect_runtime(None, self.executable(), client="claude-code")
        self.assertEqual(report["package"]["status"], "passed")
        self.assertEqual(report["hooks"]["status"], "requires_enrollment")
        self.assertEqual((report["native"]["version"], report["exit_code"]), ("2.1.311", 1))
        self.assertNotIn("codex", json.dumps(report["package"]).lower())


if __name__ == "__main__":
    unittest.main()
