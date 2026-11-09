"""Dev package export uses temporary outputs and a fake home; no client commands."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import package_manifest
from scripts import package


PACKAGE = Path(__file__).resolve().parents[1]


def tracked():
    output = subprocess.run(["git", "-C", str(PACKAGE), "ls-files", "-z"], check=True,
                            capture_output=True).stdout.decode()
    return {name for name in output.split("\0")
            if name and (PACKAGE / name).is_file() and not package.excluded(name)}


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
        for ignored in (".git", ".superpowers", ".vibe-workflow", ".codex", "CLAUDE.md", "__pycache__", "tests"):
            self.assertFalse(any(part == ignored for path in exported(self.output)
                                 for part in Path(path).parts), ignored)
        self.assertEqual(report["validation"], "passed")
        self.assertEqual(report["files"], len(tracked()))
        self.assertEqual(report["output"], str(self.output))

    def test_export_fits_the_codex_bundle_scan(self):
        from runtime import setup_observation
        package.build(PACKAGE, self.output)
        budget = {"paths": 0, "bytes": 0, "deadline": time.monotonic() + 30}
        self.assertTrue(setup_observation.plugin_content_hash(self.output, budget).startswith("sha256:"))

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
                         [".dev-package.build", ".dev-package.lock", "dev-package", "keep-me"])
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

    def test_refuses_checkouts_and_unmarked_package_folders(self):
        checkout = self.base / "vibe-workflow"
        (checkout / ".codex-plugin").mkdir(parents=True)
        (checkout / ".codex-plugin/plugin.json").write_text('{"name": "vibe-workflow"}', encoding="utf-8")
        (checkout / "my-work.txt").write_text("uncommitted", encoding="utf-8")
        with self.assertRaises(ValueError):
            package.build(PACKAGE, checkout)
        package.build(PACKAGE, self.output)
        (self.output / ".git").mkdir()
        with self.assertRaises(ValueError):
            package.build(PACKAGE, self.output)
        self.assertEqual((checkout / "my-work.txt").read_text(encoding="utf-8"), "uncommitted")

    def test_exclusions_match_directories_and_exact_files(self):
        for name, expected in (("tests/x.py", True), (".codex/config.toml", True), (".codex-plugin/plugin.json", False),
                               ("CLAUDE.md", True), ("CLAUDE.md.bak", False), ("docs/CLAUDE.md", False),
                               (".vibe-workflow/project.json", True), ("scripts/test_sharded.py", True)):
            with self.subTest(name=name):
                self.assertEqual(package.excluded(name), expected)

    def test_refuses_tracked_paths_through_symlinked_directories(self):
        repo, outside = self.base / "repo", self.base / "outside"
        (repo / "d").mkdir(parents=True)
        (repo / "d/f").write_text("tracked", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "d/f"], check=True)
        outside.mkdir()
        (outside / "f").write_text("secret", encoding="utf-8")
        for item in (repo / "d").iterdir():
            item.unlink()
        (repo / "d").rmdir()
        (repo / "d").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            package.tracked_files(repo)

    def test_empty_existing_output_is_accepted(self):
        self.output.mkdir()
        self.assertEqual(package.build(PACKAGE, self.output)["validation"], "passed")

    def test_cli_reports_json(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/package.py"),
                                 "--output", str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["validation"], "passed")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe release ")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.output = self.base / "release"
        self.name = package.manifest(PACKAGE)["name"]

    def test_release_puts_the_plugin_below_the_marketplace(self):
        with patch.object(package, "tracked_changes", return_value=False):
            report = package.build(PACKAGE, self.output, release=True)
        plugin = self.output / "plugins" / self.name
        self.assertEqual(sorted(path.name for path in self.output.iterdir()),
                         [".agents", ".claude-plugin", "LICENSE", "plugins"])
        self.assertEqual(exported(plugin), tracked())
        codex = json.loads((self.output / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        claude = json.loads((self.output / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["source"] for entry in codex["plugins"]],
                         [{"source": "local", "path": f"./plugins/{self.name}"}])
        self.assertEqual([entry["source"] for entry in claude["plugins"]], [f"./plugins/{self.name}"])
        self.assertEqual(report["validation"], "passed")
        from runtime import setup_observation
        budget = {"paths": 0, "bytes": 0, "deadline": time.monotonic() + 30}
        self.assertTrue(setup_observation.plugin_content_hash(plugin, budget).startswith("sha256:"))
        with patch.object(package, "tracked_changes", return_value=False):
            self.assertEqual(package.build(PACKAGE, self.output, release=True)["removed_builds"], 1)

    def test_release_builds_the_committed_tree_not_the_working_tree(self):
        repo = self.base / "repo"
        repo.mkdir()
        archive = subprocess.run(["git", "-C", str(PACKAGE), "archive", "HEAD"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(repo)], input=archive, check=True)
        identity = ("-c", "user.name=t", "-c", "user.email=t@example.com")
        for args in (("init", "-q"), ("add", "--all"), (*identity, "commit", "-q", "-m", "snapshot")):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        (repo / "README.md").write_text("edited after the clean check", encoding="utf-8")
        with patch.object(package, "tracked_changes", return_value=False):
            report = package.build(repo, self.output, release=True)
        plugin = self.output / "plugins" / self.name
        self.assertEqual((plugin / "README.md").read_bytes(), (PACKAGE / "README.md").read_bytes())
        self.assertEqual(report["commit"], package.git(repo, "rev-parse", "HEAD").decode().strip())
        self.assertFalse((plugin / "tests").exists())
        self.assertEqual(os.access(plugin / "scripts/validate.py", os.X_OK), os.access(PACKAGE / "scripts/validate.py", os.X_OK))

    def test_release_layout_requires_one_entry_per_marketplace(self):
        staging = self.base / "staging"
        for path, value in ((".agents/plugins/marketplace.json", {"plugins": [{"name": "other", "source": {"path": "./"}}]}),
                            (".claude-plugin/marketplace.json", {"plugins": [{"name": self.name, "source": "./"}]})):
            (staging / path).parent.mkdir(parents=True, exist_ok=True)
            (staging / path).write_text(json.dumps(value), encoding="utf-8")
        (staging / "LICENSE").write_text("license", encoding="utf-8")
        with self.assertRaises(ValueError):
            package.release_layout(staging, self.name)

    def test_release_checks_the_parent_before_building(self):
        with patch.object(package, "tracked_changes", return_value=False), self.assertRaises(ValueError):
            package.build(PACKAGE, self.output, release=True, commit_onto="no-such-release-parent")
        self.assertFalse(self.output.exists())

    def test_release_refuses_uncommitted_changes(self):
        with patch.object(package, "tracked_changes", return_value=True), self.assertRaises(ValueError):
            package.build(PACKAGE, self.output, release=True)
        self.assertFalse(self.output.exists())

    def test_release_commit_writes_one_commit_and_moves_nothing(self):
        repo = self.base / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "old.txt").write_text("previous release", encoding="utf-8")
        for args in (("add", "old.txt"), ("-c", "user.name=t", "-c", "user.email=t@example.com",
                                          "commit", "-q", "-m", "previous")):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        before = {args: package.git(repo, *args) for args in (("rev-parse", "HEAD"), ("show-ref",), ("status", "--porcelain"))}
        tree = self.base / "tree"
        (tree / "plugins/x").mkdir(parents=True)
        (tree / "plugins/x/.gitignore").write_text("*.txt\n", encoding="utf-8")
        (tree / "plugins/x/kept.txt").write_text("tracked though ignored", encoding="utf-8")
        (tree / "LICENSE").write_text("license", encoding="utf-8")
        identity = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_DIR": str(self.base)}
        with patch.dict(os.environ, identity):
            commit = package.release_commit(repo, tree, "HEAD", "release 1.2.3")
        self.assertEqual(package.git(repo, "rev-parse", f"{commit}^").decode().strip(),
                         before[("rev-parse", "HEAD")].decode().strip())
        self.assertEqual(package.git(repo, "ls-tree", "-r", "--name-only", commit).decode().split(),
                         ["LICENSE", "plugins/x/.gitignore", "plugins/x/kept.txt"])
        self.assertEqual({args: package.git(repo, *args) for args in before}, before)
        with patch.dict(os.environ, identity), self.assertRaises(ValueError):
            package.release_commit(repo, tree, "no-such-branch", "release 1.2.3")


def frozen_scan(root):
    """codex-plugin-bundle-v1 as scanned before package membership existed; bounds omitted."""
    root = Path(root)
    digest = hashlib.sha256(b"codex-plugin-bundle-v1")
    pending = [root]
    while pending:
        path = pending.pop()
        if path != root and (path.name in ("__pycache__", ".venv") or path.name.endswith((".pyc", ".egg-info"))):
            continue
        relative = os.fsencode(str(path.relative_to(root))) if path != root else b""
        digest.update(len(relative).to_bytes(8, "little")); digest.update(relative)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"directory")
            pending.extend(sorted(path.iterdir(), key=lambda item: os.fsencode(item.name), reverse=True))
        else:
            digest.update(b"file"); digest.update(info.st_size.to_bytes(8, "little")); digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def write_tree(root, files):
    for name, data in files:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)


# Computed by the scan before package membership existed; an export's fingerprint must never change.
GOLDEN = ("sha256:e2a2f9be481d7d6b0cd33fbf9e1b8068b182183995aa106ea5212ea32a35cd69",
          (("a.txt", b"alpha"), ("d/e/f.bin", bytes(range(256))), ("d/g.txt", b""), ("z/__pycache__/x.pyc", b"skip"),
           ("y.pyc", b"skip"), ("tests/t.py", b"kept in an export")))


class PackageScanTests(unittest.TestCase):
    """A checkout is scanned as the export scripts/package.py builds from it; exports scan as before."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe scan ")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def scan(self, root, deadline=30):
        from runtime import setup_observation
        budget = {"paths": 0, "bytes": 0, "deadline": time.monotonic() + deadline}
        return setup_observation.plugin_content_hash(root, budget), budget

    def checkout(self):
        repo = self.base / "repo"
        write_tree(repo, (("a.txt", b"alpha"), ("docs/guide.md", b"guide"), ("tests/t.py", b"test"),
                          ("CLAUDE.md", b"local"), (".codex/config.toml", b"local"), ("runtime/x.py", b"x")))
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
        return repo

    def export(self, repo, name="export"):
        """The dev build's copy loop, without its validation of a full plugin."""
        output = self.base / name
        for relative in package.tracked_files(repo):
            (output / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / relative, output / relative)
        return output

    def test_export_fingerprint_matches_the_frozen_algorithm(self):
        root = self.base / "golden"
        write_tree(root, GOLDEN[1])
        self.assertEqual(frozen_scan(root), GOLDEN[0])
        self.assertEqual(self.scan(root)[0], GOLDEN[0])

    def test_installed_export_hash_is_unchanged(self):
        output = self.base / "dev-package"
        package.build(PACKAGE, output)
        (output / "runtime/__pycache__").mkdir(exist_ok=True)
        (output / "runtime/__pycache__/x.pyc").write_bytes(b"cache")
        self.assertEqual(self.scan(output)[0], frozen_scan(output))

    def test_checkout_scans_as_its_fresh_export(self):
        if not os.path.lexists(PACKAGE / ".git"):
            self.skipTest("needs a Git checkout")
        output = self.base / "dev-package"
        package.build(PACKAGE, output)
        exported_hash, exported_budget = self.scan(output)
        checkout_hash, _ = self.scan(PACKAGE)
        self.assertEqual(checkout_hash, exported_hash)
        self.assertFalse(any(package.excluded(name) for name in exported(output)))
        self.assertLess(exported_budget["bytes"], package_manifest.PACKAGE_SCAN_MAX_BYTES)

    def test_untracked_and_build_output_do_not_change_a_checkout(self):
        repo = self.checkout()
        before = self.scan(repo)[0]
        self.assertEqual(before, frozen_scan(self.export(repo)))
        write_tree(repo, (("untracked.txt", b"new"), ("dist/build.txt", b"built"), ("docs/notes.tmp", b"tmp")))
        (repo / ".gitignore").write_text("", encoding="utf-8")
        self.assertEqual(self.scan(repo)[0], before)

    def test_changing_exclusions_changes_export_and_scan_identically(self):
        repo = self.checkout()
        before = self.scan(repo)[0]
        with patch.object(package_manifest, "EXCLUDED", package_manifest.EXCLUDED + ("docs/",)):
            narrowed = self.scan(repo)[0]
            self.assertEqual(narrowed, frozen_scan(self.export(repo, "narrowed")))
        self.assertNotEqual(narrowed, before)

    def test_membership_is_taken_once_with_the_scan_deadline(self):
        repo = self.checkout()
        with patch.object(package_manifest, "export_members", wraps=package_manifest.export_members) as members:
            _, budget = self.scan(repo)
        members.assert_called_once()
        self.assertEqual(members.call_args.kwargs["deadline"], budget["deadline"])

    def test_membership_failure_fails_closed(self):
        from runtime import setup_observation
        repo = self.checkout()
        with patch.object(package_manifest.subprocess, "run", side_effect=OSError("no git")), \
                patch.object(setup_observation, "_read") as read, \
                self.assertRaises(package_manifest.PackageManifestError):
            self.scan(repo)
        read.assert_not_called()

    def test_bundle_bound_stays_with_codex_when_the_onboarding_total_grows(self):
        from runtime import onboarding, startup
        self.assertEqual(onboarding.MAX_TOTAL, 16 * 1024 * 1024)
        self.assertEqual(package_manifest.PACKAGE_SCAN_MAX_BYTES, 8 * 1024 * 1024)
        self.assertEqual(package_manifest.PACKAGE_SCAN_MAX_FILE, onboarding.MAX_FILE)
        self.assertEqual(package_manifest.PACKAGE_SCAN_MAX_PATHS, 4096)
        megabyte = 1024 * 1024
        fits = self.base / "fits"
        write_tree(fits, [(f"f{index}", b"x" * megabyte) for index in range(7)] + [("rest", b"x" * (megabyte - 1))])
        self.assertTrue(self.scan(fits)[0].startswith("sha256:"))
        too_large = self.base / "too-large"
        write_tree(too_large, [(f"f{index}", b"x" * megabyte) for index in range(8)])
        with self.assertRaises(startup.StartupError):
            self.scan(too_large)


class ExportBudgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe budget ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def test_validation_reports_the_export_against_the_scan_budget(self):
        from runtime import setup_observation
        from scripts import validate
        report = validate.validate(PACKAGE)
        usage = report["package_export"]
        self.assertEqual(usage["max_bytes"], package_manifest.PACKAGE_SCAN_MAX_BYTES)
        output = self.root / "dev-package"
        package.build(PACKAGE, output)
        budget = {"paths": 0, "bytes": 0, "deadline": time.monotonic() + 30}
        setup_observation.plugin_content_hash(output, budget)
        self.assertEqual((usage["bytes"], usage["scan_paths"]), (budget["bytes"], budget["paths"]))
        self.assertEqual(usage["files"], len(exported(output)))
        self.assertEqual(validate.validate(output)["package_export"], usage)

    def test_export_budget_failures_and_warning(self):
        from scripts import validate
        write_tree(self.root, (("a", b"x" * 60), ("d/b", b"x" * 15)))
        self.assertEqual(validate.package_export(self.root)["warnings"], [])
        cases = (({"PACKAGE_SCAN_MAX_BYTES": 75}, "bytes"), ({"PACKAGE_SCAN_MAX_FILE": 59}, "file"),
                 # Root, a, d and d/b: the scan charges each entry when listed and when visited.
                 ({"PACKAGE_SCAN_MAX_PATHS": 6}, "paths"))
        for limits, label in cases:
            with self.subTest(label), patch.multiple(package_manifest, **limits), self.assertRaises(ValueError):
                validate.package_export(self.root)
        with patch.object(package_manifest, "PACKAGE_SCAN_MAX_BYTES", 100):
            self.assertEqual(len(validate.package_export(self.root)["warnings"]), 1)
        with patch.object(package_manifest, "PACKAGE_SCAN_MAX_BYTES", 101):
            self.assertEqual(validate.package_export(self.root)["warnings"], [])
        write_tree(self.root, (("tests/t.py", b""),))
        with self.assertRaisesRegex(ValueError, "development-only"):
            validate.package_export(self.root)


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

    def test_unreadable_claude_record_skips_claude(self):
        for content in (None, "not json", '{"version": 3}'):
            with self.subTest(content=content):
                records = self.home / ".claude/plugins/installed_plugins.json"
                if content is None:
                    records.unlink(missing_ok=True)
                else:
                    records.write_text(content, encoding="utf-8")
                report = package.prune(PACKAGE, self.home, dry_run=True)
                self.assertFalse(any("/.claude/" in path for path in report["removed"]))
                self.assertEqual(report["skipped"], ["claude-code: unreadable install record"])

    def test_missing_caches_are_not_errors(self):
        self.assertEqual(package.prune(PACKAGE, self.home / "empty", dry_run=False)["removed"], [])


if __name__ == "__main__":
    unittest.main()
