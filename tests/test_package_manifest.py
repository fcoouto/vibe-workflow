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

    def fake_git(self, body):
        """Put a stand-in git first on PATH; it answers rev-parse with the repository root."""
        bin_directory = self.base / "fake-bin"
        bin_directory.mkdir(exist_ok=True)
        script = bin_directory / "git"
        script.write_text(f'#!/bin/sh\ncase "$*" in *rev-parse*) printf "%s\\n" "{self.repo}"; exit 0;; esac\n{body}\n',
                          encoding="utf-8")
        script.chmod(0o755)
        return patch.dict(os.environ, {"PATH": f"{bin_directory}{os.pathsep}{os.environ['PATH']}"})

    def assert_unavailable(self, root=None, message="Package membership is unavailable", **kwargs):
        with self.assertRaises(package_manifest.PackageManifestError) as raised:
            package_manifest.export_members(root or self.repo, **kwargs)
        self.assertEqual(str(raised.exception), message)

    def test_unreadable_repository_fails_closed(self):
        broken = self.base / "broken"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /nonexistent/vibe-manifest\n", encoding="utf-8")
        self.assert_unavailable(broken)
        self.assertTrue(issubclass(package_manifest.PackageManifestError, ValueError))

    def test_git_failures_fail_closed(self):
        with self.subTest("missing git"), patch.dict(os.environ, {"PATH": str(self.base / "no-such-bin")}):
            self.assert_unavailable()
        for label, body in (("failed listing", "exit 128"), ("undecodable listing", r"printf 'a\377\000'"),
                            ("oversized listing", "head -c 300000 /dev/zero | tr '\\0' a")):
            with self.subTest(label), self.fake_git(body), patch.object(package_manifest, "MAX_LISTING", 200000):
                self.assert_unavailable()
        with self.subTest("timeout"), self.fake_git("sleep 5"):
            started = time.monotonic()
            self.assert_unavailable(message="Package membership deadline exceeded", deadline=time.monotonic() + 0.5)
            self.assertLess(time.monotonic() - started, 3)

    def test_deadline_bounds_the_git_call(self):
        with patch.object(package_manifest.subprocess, "Popen") as popen:
            self.assert_unavailable(message="Package membership deadline exceeded", deadline=time.monotonic() - 1)
        popen.assert_not_called()

    def test_an_invalid_git_entry_never_lists_an_enclosing_repository(self):
        # An installed copy below another repository, for example a dotfiles home.
        installed = self.repo / "installed"
        (installed / ".git").mkdir(parents=True)
        (installed / "plugin.txt").write_text("installed", encoding="utf-8")
        self.assert_unavailable(installed)

    def test_a_different_top_level_fails_closed(self):
        nested = self.repo / "nested"
        nested.mkdir()
        (nested / ".git").write_text("", encoding="utf-8")
        with self.fake_git("exit 0"):
            # The stand-in reports self.repo as the top level of nested.
            self.assert_unavailable(nested)

    def test_unusual_tracked_names(self):
        names = ("with space.txt", "new\nline.txt", "tab\tname.txt", 'quote".txt', "-leading.txt",
                 "caf\u00e9.txt", "deep/\u00fcber/x.txt")
        for name in names:
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text(name, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "--all"], check=True)
        members = package_manifest.export_members(self.repo)
        self.assertTrue(set(names) <= members)
        self.assertEqual(members, with_parents(package.tracked_files(self.repo)))

    def test_tracked_symlinks_are_members_that_packaging_refuses(self):
        (self.repo / "link").symlink_to("a.txt")
        subprocess.run(["git", "-C", str(self.repo), "add", "link"], check=True)
        self.assertIn("link", package_manifest.export_members(self.repo))
        with self.assertRaisesRegex(ValueError, "symlinks"):
            package.tracked_files(self.repo)

    def test_git_runs_no_configured_commands_or_inherited_selectors(self):
        marker = self.base / "marker"
        trace = self.base / "trace"
        subprocess.run(["git", "-C", str(self.repo), "config", "core.fsmonitor", f"touch {marker}-repo"], check=True)
        inherited = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor",
                     "GIT_CONFIG_VALUE_0": f"touch {marker}-env", "GIT_TRACE": str(trace),
                     "GIT_NAMESPACE": "other", "GIT_COMMON_DIR": str(self.base / "elsewhere")}
        with patch.dict(os.environ, inherited):
            self.assertIn("a.txt", package_manifest.export_members(self.repo))
        self.assertEqual(sorted(path.name for path in self.base.iterdir()), ["repo"])


if __name__ == "__main__":
    unittest.main()
