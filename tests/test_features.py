"""Feature settings use disposable Git checkouts and packages; no client, network or user state."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from runtime import features, routing_settings, settings_store


PACKAGE = Path(__file__).resolve().parents[1]
BLOCK = ("<!-- vibe-workflow:workstreams:start -->\n- [{mark}] Workstreams\n"
         "Follow the [Workstreams project preference](skills/workstreams/references/procedure.md).\n"
         "<!-- vibe-workflow:workstreams:end -->\n")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


class Checkout(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe features ")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()
        git(self.root, "init", "-q")
        (self.root / ".gitignore").write_text("/.vibe-workflow/project.json\n/.vibe-workflow/private/\n", encoding="utf-8")

    def settings(self, value):
        path = self.root / ".vibe-workflow/settings.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def feature(self, report, name):
        return report["features"][name]


class PackageFeatures(Checkout):
    """A disposable package with one feature of each kind."""

    def setUp(self):
        super().setUp()
        self.package = self.base / "package"
        self.manifest("workstreams", control="settings", default=False)
        self.manifest("tuned", control="settings", default=True,
                      settings={"margin": {"type": "integer", "default": 5}})
        self.manifest("export", control="settings", default=False, consent="transmits")
        self.manifest("naming", control="fixed", default=True)
        self.manifest("router", control="adapter", default=True, source={"store": "user-settings", "scope": "user"})

    def manifest(self, name, *, control, default, settings=None, consent="none", source=None):
        value = dict(manifest_version=1, id=name, summary=name, skills=[], default=dict(enabled=default),
                     settings=settings or {}, scopes=["project"], consent=consent, control=control, requires=[],
                     procedure="skills/x/SKILL.md")
        if source is not None:
            value["source"] = source
        path = self.package / "features" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def resolve(self):
        return features.resolve(self.root, package=self.package)


class ResolveTests(PackageFeatures):
    def test_defaults_apply_without_a_settings_file(self):
        report = self.resolve()
        self.assertEqual(report["revision"], 0)
        self.assertEqual(report["fingerprint"], "absent")
        workstreams = self.feature(report, "workstreams")
        self.assertEqual((workstreams["desired"], workstreams["effective"], workstreams["reason"]),
                         ({"enabled": False, "source": "default"}, False, "default"))
        self.assertEqual(self.feature(report, "tuned")["values"], {"margin": 5})
        self.assertFalse(list(self.root.glob(".vibe-workflow")))

    def test_project_settings_override_defaults(self):
        self.settings({"schema_version": 1, "revision": 2, "features": {
            "workstreams": {"enabled": True}, "tuned": {"enabled": False, "values": {"margin": 9}}}})
        report = self.resolve()
        self.assertEqual(report["revision"], 2)
        self.assertEqual(self.feature(report, "workstreams")["desired"], {"enabled": True, "source": "project"})
        self.assertTrue(self.feature(report, "workstreams")["effective"])
        self.assertEqual((self.feature(report, "tuned")["effective"], self.feature(report, "tuned")["values"]),
                         (False, {"margin": 9}))

    def test_committed_value_never_grants_consent(self):
        self.settings({"schema_version": 1, "revision": 1, "features": {"export": {"enabled": True}}})
        export = self.feature(self.resolve(), "export")
        self.assertEqual((export["consent"], export["effective"], export["reason"]),
                         ("missing", False, "needs_consent"))

    def test_values_for_fixed_and_adapter_features_are_ignored(self):
        self.settings({"schema_version": 1, "revision": 1, "features": {
            "naming": {"enabled": False}, "router": {"enabled": False}}})
        report = self.resolve()
        self.assertEqual((self.feature(report, "naming")["effective"], self.feature(report, "naming")["reason"]),
                         (True, "ignored_override"))
        self.assertEqual((self.feature(report, "router")["effective"], self.feature(report, "router")["reason"]),
                         (None, "ignored_override"))

    def test_invalid_entry_makes_only_that_feature_unavailable(self):
        self.settings({"schema_version": 1, "revision": 1, "features": {
            "tuned": {"enabled": "yes"}, "workstreams": {"enabled": True}, "unknown": {"enabled": True}}})
        report = self.resolve()
        self.assertEqual((self.feature(report, "tuned")["effective"], self.feature(report, "tuned")["reason"]),
                         (False, "invalid_settings"))
        self.assertTrue(self.feature(report, "workstreams")["effective"])
        self.assertTrue(any("unknown" in error for error in report["errors"]))

    def test_invalid_file_never_turns_a_default_on_feature_back_on(self):
        for text in ("{not json", '{"schema_version": 1, "revision": 1, "features": {}, "features": {}}',
                     '{"schema_version": 2, "revision": 1, "features": {}}', "[]"):
            with self.subTest(text=text):
                self.settings(text)
                report = self.resolve()
                tuned = self.feature(report, "tuned")
                self.assertEqual((tuned["effective"], tuned["reason"]), (False, "invalid_settings"))
                self.assertTrue(report["errors"])
                self.assertTrue(self.feature(report, "naming")["effective"])

    def test_unknown_setting_key_and_wrong_type_are_invalid(self):
        for values in ({"other": 1}, {"margin": "9"}, {"margin": True}):
            with self.subTest(values=values):
                self.settings({"schema_version": 1, "revision": 1, "features": {"tuned": {"enabled": True, "values": values}}})
                self.assertEqual(self.feature(self.resolve(), "tuned")["reason"], "invalid_settings")

    def test_fingerprint_changes_with_content_even_at_the_same_revision(self):
        self.settings({"schema_version": 1, "revision": 1, "features": {"workstreams": {"enabled": True}}})
        first = self.resolve()["fingerprint"]
        self.settings({"schema_version": 1, "revision": 1, "features": {"workstreams": {"enabled": False}}})
        self.assertNotEqual(self.resolve()["fingerprint"], first)

    def test_settings_file_must_be_a_regular_file(self):
        target = self.base / "elsewhere.json"
        target.write_text('{"schema_version": 1, "revision": 1, "features": {}}', encoding="utf-8")
        (self.root / ".vibe-workflow").mkdir()
        (self.root / ".vibe-workflow/settings.json").symlink_to(target)
        self.assertEqual(self.feature(self.resolve(), "workstreams")["reason"], "invalid_settings")

    def test_malformed_manifest_is_rejected(self):
        bad = self.package / "features/bad.json"
        for value in ({"id": "bad"}, dict(json.loads((self.package / "features/naming.json").read_text()), id="bad", extra=1),
                      dict(json.loads((self.package / "features/naming.json").read_text()), id="bad", procedure="../x.md")):
            with self.subTest(value=value):
                bad.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.resolve()

    def test_summary_lists_every_feature(self):
        summary = features.summary(self.resolve())
        self.assertTrue(summary.startswith("Features: "))
        for name in ("workstreams off", "tuned on", "naming on", "router unchecked", "export off"):
            self.assertIn(name, summary)


class WriteTests(PackageFeatures):
    def test_enable_writes_project_settings_and_repeating_is_a_no_op(self):
        report = features.change(self.root, "workstreams", enabled=True, package=self.package)
        self.assertTrue(self.feature(report, "workstreams")["effective"])
        value = json.loads((self.root / ".vibe-workflow/settings.json").read_text())
        self.assertEqual(value, {"schema_version": 1, "revision": 1, "features": {"workstreams": {"enabled": True}}})
        again = features.change(self.root, "workstreams", enabled=True, package=self.package)
        self.assertEqual(again["revision"], 1)
        private = self.root / ".vibe-workflow/private"
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)
        self.assertEqual(git(self.root, "status", "--porcelain", "--untracked-files=all").split(),
                         ["??", ".gitignore", "??", ".vibe-workflow/settings.json"])

    def test_set_and_unset_values(self):
        features.change(self.root, "tuned", values={"margin": 7}, package=self.package)
        self.assertEqual(self.feature(self.resolve(), "tuned")["values"], {"margin": 7})
        features.change(self.root, "tuned", unset="margin", package=self.package)
        self.assertEqual(self.feature(self.resolve(), "tuned")["values"], {"margin": 5})

    def test_refuses_fixed_adapter_and_consent_features(self):
        for name in ("naming", "router", "export"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                features.change(self.root, name, enabled=True, package=self.package)

    def test_refuses_invalid_values(self):
        for values in ({"margin": "x"}, {"other": 1}, {"margin": 2**60}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                features.change(self.root, "tuned", values=values, package=self.package)

    def test_refuses_when_private_files_would_not_be_ignored(self):
        (self.root / ".gitignore").write_text("", encoding="utf-8")
        with self.assertRaises(ValueError):
            features.change(self.root, "workstreams", enabled=True, package=self.package)
        self.assertFalse((self.root / ".vibe-workflow").exists())

    def test_tracked_enrollment_is_a_warning_not_a_refusal(self):
        record = self.root / ".vibe-workflow/project.json"
        record.parent.mkdir()
        record.write_text("{}", encoding="utf-8")
        git(self.root, "add", "-f", ".vibe-workflow/project.json")
        report = features.change(self.root, "workstreams", enabled=True, package=self.package)
        self.assertTrue(any("project.json" in warning for warning in report["warnings"]))

    def test_refuses_an_invalid_file_instead_of_overwriting_it(self):
        self.settings("{not json")
        with self.assertRaises(ValueError):
            features.change(self.root, "workstreams", enabled=True, package=self.package)
        self.assertEqual((self.root / ".vibe-workflow/settings.json").read_text(), "{not json")

    def test_detects_a_change_made_between_read_and_replace(self):
        original = settings_store.read

        def edited(root):
            value, raw = original(root)
            self.settings({"schema_version": 1, "revision": 1, "features": {"tuned": {"enabled": False}}})
            return value, raw
        settings_store.read = edited
        self.addCleanup(setattr, settings_store, "read", original)
        with self.assertRaises(ValueError):
            features.change(self.root, "workstreams", enabled=True, package=self.package)
        self.assertEqual(json.loads((self.root / ".vibe-workflow/settings.json").read_text())["features"],
                         {"tuned": {"enabled": False}})

    def test_refuses_absolute_paths_in_committed_values(self):
        self.manifest("pathy", control="settings", default=False, settings={"where": {"type": "string", "default": "x"}})
        for value in ("/Users/someone/x", "~/x"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                features.change(self.root, "pathy", values={"where": value}, package=self.package)


class LegacyWorkstreamsTests(Checkout):
    def agents(self, text):
        (self.root / "AGENTS.md").write_text(text, encoding="utf-8")

    def workstreams(self):
        return features.resolve(self.root)["features"]["workstreams"]

    def test_legacy_block_is_reported_until_migrated(self):
        self.agents("# Rules\n\n" + BLOCK.format(mark="x"))
        workstreams = self.workstreams()
        self.assertEqual((workstreams["desired"], workstreams["effective"], workstreams["reason"]),
                         ({"enabled": True, "source": "legacy"}, True, "migration_pending"))

    def test_project_settings_win_over_a_legacy_block(self):
        self.agents(BLOCK.format(mark="x"))
        self.settings({"schema_version": 1, "revision": 1, "features": {"workstreams": {"enabled": False}}})
        self.assertEqual(self.workstreams()["desired"], {"enabled": False, "source": "project"})

    def test_malformed_legacy_block_is_unavailable(self):
        self.agents(BLOCK.format(mark="x") + BLOCK.format(mark=" "))
        self.assertEqual((self.workstreams()["effective"], self.workstreams()["reason"]), (False, "invalid_settings"))

    def test_migration_moves_the_block_and_can_be_undone(self):
        original = "# Rules\n\nKeep this.\n\n" + BLOCK.format(mark="x") + "\nAfter.\n"
        self.agents(original)
        plan = features.migrate(self.root, "plan")
        self.assertEqual(plan["workstreams"]["value"], True)
        self.assertFalse((self.root / ".vibe-workflow").exists())
        applied = features.migrate(self.root, "apply")
        text = (self.root / "AGENTS.md").read_text()
        self.assertNotIn("vibe-workflow:workstreams", text)
        self.assertIn("vibe-workflow:features:start", text)
        self.assertTrue(text.startswith("# Rules\n\nKeep this.\n\n") and text.endswith("\nAfter.\n"))
        self.assertEqual(self.workstreams()["desired"], {"enabled": True, "source": "project"})
        backup = applied["backup"]
        self.assertTrue((self.root / ".vibe-workflow/private" / backup).is_dir())
        features.migrate(self.root, "undo", backup=backup)
        self.assertEqual((self.root / "AGENTS.md").read_text(), original)
        self.assertFalse((self.root / ".vibe-workflow/settings.json").exists())

    def test_undo_preserves_later_edits(self):
        self.agents(BLOCK.format(mark=" "))
        backup = features.migrate(self.root, "apply")["backup"]
        (self.root / "AGENTS.md").write_text("edited later\n", encoding="utf-8")
        report = features.migrate(self.root, "undo", backup=backup)
        self.assertEqual((self.root / "AGENTS.md").read_text(), "edited later\n")
        self.assertIn("AGENTS.md", report["kept"])

    def test_migration_without_a_block_changes_nothing(self):
        self.agents("# Rules\n")
        self.assertEqual(features.migrate(self.root, "apply")["workstreams"], {"status": "no_legacy_block"})
        self.assertFalse((self.root / ".vibe-workflow").exists())

    def test_migration_refuses_a_conflicting_saved_value(self):
        self.agents(BLOCK.format(mark="x"))
        self.settings({"schema_version": 1, "revision": 1, "features": {"workstreams": {"enabled": False}}})
        with self.assertRaises(ValueError):
            features.migrate(self.root, "apply")
        self.assertIn("vibe-workflow:workstreams", (self.root / "AGENTS.md").read_text())

    def test_routing_reads_the_resolved_preference(self):
        class Scope:
            repository_root = self.root
        self.assertIs(routing_settings.workstreams_enabled(Scope()), False)
        self.settings({"schema_version": 1, "revision": 1, "features": {"workstreams": {"enabled": True}}})
        self.assertIs(routing_settings.workstreams_enabled(Scope()), True)


class CommandTests(Checkout):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"), "features", *args,
                               "--cwd", str(self.root)], capture_output=True, text=True)

    def test_status_enable_and_summary(self):
        result = self.run_cli("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["features"]["workstreams"]["effective"], False)
        self.assertEqual(self.run_cli("enable", "workstreams").returncode, 0)
        result = self.run_cli("status", "workstreams", "--summary")
        self.assertEqual(result.stdout.strip(), "Features: workstreams on (project)")

    def test_errors_are_specific(self):
        result = self.run_cli("enable", "no-such-feature")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no-such-feature", json.loads(result.stdout)["error"])


class ShippedManifestTests(unittest.TestCase):
    def test_every_shipped_manifest_loads(self):
        loaded = features.manifests()
        self.assertEqual(loaded["workstreams"]["default"], {"enabled": False})
        self.assertEqual(loaded["workstreams"]["control"], "settings")


if __name__ == "__main__":
    unittest.main()
