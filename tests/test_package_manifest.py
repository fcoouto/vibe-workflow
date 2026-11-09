"""Package membership has one definition; temporary Git fixtures only, no network."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import package_manifest
from scripts import package


PACKAGE = Path(__file__).resolve().parents[1]


def with_parents(files):
    members = set()
    for name in files:
        parts = Path(name).parts
        members.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return members


class ExclusionTests(unittest.TestCase):
    def test_package_script_uses_the_shared_definition(self):
        self.assertIs(package.EXCLUDED, package_manifest.EXCLUDED)
        self.assertIs(package.excluded, package_manifest.excluded)
        self.assertEqual(package_manifest.EXCLUDED,
                         ("tests/", "scripts/test_sharded.py", ".vibe-workflow/", ".codex/", "CLAUDE.md"))

    def test_directories_match_by_prefix_and_files_exactly(self):
        for name, expected in (("tests/x.py", True), ("tests/a/b.py", True), ("tests", False), ("tests.md", False),
                               ("docs/tests/x.py", False), (".codex/config.toml", True),
                               (".codex-plugin/plugin.json", False), ("CLAUDE.md", True),
                               ("CLAUDE.md.bak", False), ("docs/CLAUDE.md", False),
                               ("scripts/test_sharded.py", True), ("scripts/test_sharded.py.orig", False),
                               (".vibe-workflow/project.json", True)):
            with self.subTest(name=name):
                self.assertEqual(package_manifest.excluded(name), expected)


class MembershipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe manifest ")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.repo = self.base / "repo"
        files = ("a.txt", "d/e/f.txt", "tests/t.py", "tests/deep/u.py", "CLAUDE.md", "docs/CLAUDE.md",
                 ".codex/config.toml", ".codex-plugin/plugin.json", "scripts/test_sharded.py", "scripts/run.py")
        for name in files:
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(name, encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "--all"], check=True)
        (self.repo / "untracked.txt").write_text("not tracked", encoding="utf-8")
        (self.repo / "dist").mkdir()
        (self.repo / "dist/build.txt").write_text("not tracked", encoding="utf-8")

    def test_checkout_members_are_tracked_files_minus_exclusions_with_parents(self):
        expected = {"a.txt", "d", "d/e", "d/e/f.txt", "docs", "docs/CLAUDE.md",
                    ".codex-plugin", ".codex-plugin/plugin.json", "scripts", "scripts/run.py"}
        self.assertEqual(package_manifest.export_members(self.repo), expected)
        self.assertEqual(package_manifest.export_members(self.repo), with_parents(package.tracked_files(self.repo)))

    def test_this_checkout_matches_what_package_exports(self):
        if not os.path.lexists(PACKAGE / ".git"):
            self.skipTest("needs a Git checkout")
        members = package_manifest.export_members(PACKAGE, deadline=time.monotonic() + 30)
        self.assertEqual(members, with_parents(package.tracked_files(PACKAGE)))
        self.assertFalse(any(package_manifest.excluded(name) for name in members))
        self.assertNotIn("tests", members)

    def test_directories_without_git_metadata_have_no_filter(self):
        export = self.base / "export"
        (export / "tests").mkdir(parents=True)
        self.assertIsNone(package_manifest.export_members(export))
        # An installed copy inside some other repository is still an export, not that repository.
        nested = self.repo / "installed"
        nested.mkdir()
        self.assertIsNone(package_manifest.export_members(nested))

    def test_inherited_repository_variables_are_ignored(self):
        other = self.base / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_INDEX_FILE": str(other / "index")}):
            self.assertIn("a.txt", package_manifest.export_members(self.repo))

    def test_git_failures_fail_closed(self):
        broken = self.base / "broken"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /nonexistent/vibe-manifest\n", encoding="utf-8")
        cases = (
            ("unreadable repository", broken, None, None),
            ("missing git", self.repo, OSError("no git"), None),
            ("timeout", self.repo, subprocess.TimeoutExpired("git", 1), None),
            ("failed listing", self.repo, None, subprocess.CompletedProcess([], 128, b"", b"fatal")),
            ("undecodable listing", self.repo, None, subprocess.CompletedProcess([], 0, b"\xff\0", b"")),
        )
        for label, root, error, result in cases:
            with self.subTest(label):
                run = patch.object(package_manifest.subprocess, "run", side_effect=error, return_value=result)
                with (run if error or result else patch.dict(os.environ)):
                    with self.assertRaises(package_manifest.PackageManifestError) as raised:
                        package_manifest.export_members(root)
                    self.assertEqual(str(raised.exception), "Package membership is unavailable")
        self.assertTrue(issubclass(package_manifest.PackageManifestError, ValueError))

    def test_deadline_bounds_the_git_call(self):
        with patch.object(package_manifest.subprocess, "run") as run, \
                self.assertRaises(package_manifest.PackageManifestError) as raised:
            package_manifest.export_members(self.repo, deadline=time.monotonic() - 1)
        run.assert_not_called()
        self.assertEqual(str(raised.exception), "Package membership deadline exceeded")
        real = subprocess.run
        with patch.object(package_manifest.subprocess, "run", side_effect=real) as run:
            package_manifest.export_members(self.repo, deadline=time.monotonic() + 5)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
