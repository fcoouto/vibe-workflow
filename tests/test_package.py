"""Dev package export uses temporary outputs and a fake home; no client commands."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import package


PACKAGE = Path(__file__).resolve().parents[1]


def tracked():
    output = subprocess.run(["git", "-C", str(PACKAGE), "ls-files", "-z"], check=True,
                            capture_output=True).stdout.decode()
    return {name for name in output.split("\0") if name and (PACKAGE / name).is_file()}


def exported(root):
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


class BuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe package ")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.output = self.base / "dev-package"

    def test_exports_only_tracked_files_and_validates(self):
        report = package.build(PACKAGE, self.output)
        self.assertEqual(exported(self.output), tracked())
        for ignored in (".git", ".superpowers", ".vibe-workflow", "__pycache__"):
            self.assertFalse(any(part == ignored for path in exported(self.output)
                                 for part in Path(path).parts), ignored)
        self.assertEqual(report["validation"], "passed")
        self.assertEqual(report["files"], len(tracked()))
        self.assertEqual(report["output"], str(self.output))

    def test_rebuild_replaces_previous_build_and_removes_leftovers(self):
        package.build(PACKAGE, self.output)
        (self.output / "stale.txt").write_text("old build", encoding="utf-8")
        for leftover in (".dev-package.tmp-crashed", ".dev-package.old-crashed"):
            (self.base / leftover).mkdir()
        unrelated = self.base / "keep-me"
        unrelated.mkdir()
        report = package.build(PACKAGE, self.output)
        self.assertFalse((self.output / "stale.txt").exists())
        self.assertEqual(sorted(path.name for path in self.base.iterdir()),
                         [".dev-package.lock", "dev-package", "keep-me"])
        self.assertEqual(report["removed_builds"], 3)

    def test_refuses_unsafe_outputs(self):
        foreign = self.base / "foreign"
        foreign.mkdir()
        (foreign / "notes.txt").write_text("user data", encoding="utf-8")
        link = self.base / "link"
        link.symlink_to(self.base / "elsewhere", target_is_directory=True)
        for output in (PACKAGE / "dist", PACKAGE.parent, foreign, link):
            with self.subTest(output=output), self.assertRaises(ValueError):
                package.build(PACKAGE, output)
        self.assertEqual((foreign / "notes.txt").read_text(encoding="utf-8"), "user data")

    def test_empty_existing_output_is_accepted(self):
        self.output.mkdir()
        self.assertEqual(package.build(PACKAGE, self.output)["validation"], "passed")

    def test_cli_reports_json(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/package.py"),
                                 "--output", str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["validation"], "passed")


class PruneTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe prune ")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.current = package.manifest(PACKAGE)["version"]
        self.claude = self.home / ".claude/plugins/cache/vibe-workflow-local/vibe-workflow"
        self.codex = self.home / ".codex/plugins/cache/vibe-workflow-local/vibe-workflow"
        for root, manifest in ((self.claude, ".claude-plugin"), (self.codex, ".codex-plugin")):
            for mtime, version in enumerate(("0.0.1", "0.0.2", self.current, "0.0.3"), 1):
                self.cached(root, manifest, version)
                os.utime(root / version, (mtime, mtime))
        records = self.home / ".claude/plugins/installed_plugins.json"
        records.write_text(json.dumps({"version": 2, "plugins": {"vibe-workflow@vibe-workflow-local": [
            {"installPath": str(self.claude / "0.0.2")}]}}), encoding="utf-8")
        self.other = self.home / ".codex/plugins/cache/vcw-candidate/vibe-coding-workflow/0.0.1"
        self.other.mkdir(parents=True)

    def cached(self, root, manifest, version):
        path = root / version / manifest
        path.mkdir(parents=True)
        (path / "plugin.json").write_text(json.dumps({"name": "vibe-workflow", "version": version}),
                                          encoding="utf-8")

    def versions(self, root):
        return sorted(path.name for path in root.iterdir())

    def test_dry_run_lists_without_deleting(self):
        report = package.prune(PACKAGE, self.home, dry_run=True)
        self.assertEqual(sorted(Path(path).relative_to(self.home).as_posix() for path in report["removed"]), [
            ".claude/plugins/cache/vibe-workflow-local/vibe-workflow/0.0.1",
            ".claude/plugins/cache/vibe-workflow-local/vibe-workflow/0.0.3",
            ".codex/plugins/cache/vibe-workflow-local/vibe-workflow/0.0.1",
            ".codex/plugins/cache/vibe-workflow-local/vibe-workflow/0.0.2"])
        self.assertTrue(report["dry_run"])
        self.assertEqual(len(self.versions(self.claude)), 4)
        self.assertEqual(len(self.versions(self.codex)), 4)

    def test_keeps_current_installed_and_newest_codex_copies(self):
        report = package.prune(PACKAGE, self.home, dry_run=False)
        self.assertEqual(self.versions(self.claude), sorted(["0.0.2", self.current]))
        self.assertEqual(self.versions(self.codex), sorted(["0.0.3", self.current]))
        self.assertTrue(self.other.is_dir())
        self.assertGreater(report["bytes"], 0)

    def test_skips_unrecognized_entries(self):
        (self.claude / "notes").mkdir()
        (self.claude / "9.9.9").mkdir()
        link = self.claude / "0.0.9"
        link.symlink_to(self.claude / "0.0.1", target_is_directory=True)
        report = package.prune(PACKAGE, self.home, dry_run=False)
        self.assertTrue((self.claude / "notes").is_dir())
        self.assertTrue((self.claude / "9.9.9").is_dir())
        self.assertTrue(link.is_symlink())
        self.assertNotIn(str(link), report["removed"])

    def test_missing_caches_are_not_errors(self):
        self.assertEqual(package.prune(PACKAGE, self.home / "empty", dry_run=False)["removed"], [])


if __name__ == "__main__":
    unittest.main()
