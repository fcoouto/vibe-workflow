"""Exercise leftover archiving in throwaway repositories, never a real checkout."""
import contextlib
import errno
import importlib.util
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills/vibe-pull-requests/scripts/archive.py"
ARCHIVES = ".vibe-workflow/archives"


def load():
    spec = importlib.util.spec_from_file_location("archive", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
                          env=env, capture_output=True, text=True, check=True).stdout


def files(root):
    """Every file below root, by relative name; a missing root has none."""
    if not root.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if not path.is_dir())


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main = self.checkout(self.root / "main")
        self.linked = self.root / "linked"
        git(self.main, "worktree", "add", "-q", "-b", "feat/x", str(self.linked))
        self.archives = self.main / ARCHIVES

    def checkout(self, path):
        """A checkout whose root mode does not depend on the caller's umask."""
        path.mkdir()
        os.chmod(path, 0o755)
        git(path, "init", "-q", "-b", "main")
        (path / ".gitignore").write_text(".env\n")
        (path / "README.md").write_text("fixture\n")
        git(path, "add", ".gitignore", "README.md")
        git(path, "commit", "-qm", "fixture")
        return path

    def base(self):
        """The settings folder with a mode that does not depend on the umask."""
        base = self.main / ".vibe-workflow"
        base.mkdir(exist_ok=True)
        os.chmod(base, 0o755)
        return base

    def archive(self, *paths, branch="feat/x", worktree=None):
        command = [sys.executable, "-I", "-B", str(SCRIPT), "--worktree", str(worktree or self.linked),
                   "--branch", branch]
        for path in paths:
            command += ["--path", path]
        return subprocess.run(command, cwd=self.main, capture_output=True, text=True, timeout=30)

    def refused(self, run):
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"\Arefused: [^\n]+\n\Z")
        self.assertEqual(run.stdout, "")

    def archived(self):
        return [name for name in files(self.archives) if name != ".gitignore"]

    def test_moves_ignored_and_untracked_paths_keeping_layout(self):
        (self.linked / ".env").write_text("SECRET=1\n")
        (self.linked / "reports").mkdir()
        (self.linked / "reports/a.md").write_text("finding\n")
        run = self.archive(".env", "reports/a.md")
        self.assertEqual(run.returncode, 0, run.stderr)
        first = self.archives / "feat/x/.env"
        second = self.archives / "feat/x/reports/a.md"
        self.assertEqual(run.stdout.splitlines(), [str(first), str(second)])
        self.assertEqual(first.read_text(), "SECRET=1\n")
        self.assertEqual(second.read_text(), "finding\n")
        self.assertFalse((self.linked / ".env").exists())
        self.assertFalse((self.linked / "reports/a.md").exists())
        self.assertEqual(git(self.main, "status", "--porcelain"), "")

    def test_second_run_gets_a_suffix_and_never_overwrites(self):
        (self.linked / ".env").write_text("first\n")
        self.assertEqual(self.archive(".env").returncode, 0)
        (self.linked / ".env").write_text("second\n")
        run = self.archive(".env")
        self.assertEqual(run.returncode, 0, run.stderr)
        dest = Path(run.stdout.strip())
        self.assertRegex(dest.relative_to(self.archives).as_posix(), r"\Afeat/x-\d{8}-\d{6}/\.env\Z")
        self.assertEqual(dest.read_text(), "second\n")
        self.assertEqual((self.archives / "feat/x/.env").read_text(), "first\n")

    def test_refuses_tracked_absolute_dotdot_git_and_linked_parent(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("keep\n")
        (self.root / "keep.txt").write_text("keep\n")
        (self.linked / ".env").write_text("x\n")
        os.symlink(outside, self.linked / "link")
        cases = (("README.md",), (str(self.linked / ".env"),), ("../keep.txt",), (".git",), ("link/secret.txt",))
        for paths in cases:
            with self.subTest(paths=paths):
                self.refused(self.archive(*paths))
                self.assertEqual((self.linked / "README.md").read_text(), "fixture\n")
                self.assertEqual((self.linked / ".env").read_text(), "x\n")
                self.assertEqual((self.root / "keep.txt").read_text(), "keep\n")
                self.assertEqual((outside / "secret.txt").read_text(), "keep\n")
                self.assertTrue((self.linked / ".git").is_file())
                self.assertTrue((self.linked / "link").is_symlink())
                self.assertEqual(self.archived(), [])

    def test_refuses_a_linked_archives_folder_or_gitignore(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.linked / ".env").write_text("x\n")
        base = self.base()
        os.symlink(outside, self.archives)
        self.refused(self.archive(".env"))
        self.assertEqual(list(outside.iterdir()), [])
        os.unlink(self.archives)
        self.archives.mkdir(mode=0o700)
        target = self.root / "notes.txt"
        target.write_text("user notes\n")
        os.symlink(target, self.archives / ".gitignore")
        self.refused(self.archive(".env"))
        self.assertEqual(target.read_text(), "user notes\n")
        self.assertEqual((self.linked / ".env").read_text(), "x\n")
        self.assertTrue(base.is_dir())

    def test_rewrites_a_differing_gitignore(self):
        self.base()
        self.archives.mkdir(mode=0o700)
        (self.archives / ".gitignore").write_text("*.log\n")
        (self.linked / ".env").write_text("x\n")
        run = self.archive(".env")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.archives / ".gitignore").read_bytes(), b"*\n")
        self.assertEqual(Path(run.stdout.strip()).read_text(), "x\n")

    def test_cross_device_is_refused_and_source_kept(self):
        (self.linked / ".env").write_text("x\n")
        module = load()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.chdir(self.main), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(module.os, "rename", side_effect=OSError(errno.EXDEV, "Cross-device link")):
            code = module.main(["--worktree", str(self.linked), "--branch", "feat/x", "--path", ".env"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertRegex(err.getvalue(), r"\Arefused: [^\n]+\n\Z")
        self.assertEqual((self.linked / ".env").read_text(), "x\n")
        self.assertEqual(self.archived(), [])

    def test_partial_progress_is_reported(self):
        (self.linked / ".env").write_text("x\n")
        run = self.archive(".env", "README.md")
        self.assertEqual(run.returncode, 2, run.stderr)
        dest = self.archives / "feat/x/.env"
        self.assertEqual(run.stdout.splitlines(), [f"archived: {dest}"])
        self.assertEqual(run.stderr.splitlines(), ["refused: README.md is tracked by Git"])
        self.assertEqual(dest.read_text(), "x\n")
        self.assertFalse((self.linked / ".env").exists())
        self.assertEqual((self.linked / "README.md").read_text(), "fixture\n")

    def test_refuses_a_worktree_of_another_repository(self):
        other = self.checkout(self.root / "other")
        other_linked = self.root / "other-linked"
        git(other, "worktree", "add", "-q", "-b", "task", str(other_linked))
        (other_linked / ".env").write_text("x\n")
        self.refused(self.archive(".env", worktree=other_linked))
        self.assertEqual((other_linked / ".env").read_text(), "x\n")
        self.assertFalse((self.main / ".vibe-workflow").exists())
        self.assertFalse((other / ".vibe-workflow").exists())

    def test_refuses_an_invalid_branch_name(self):
        (self.linked / ".env").write_text("x\n")
        for branch in ("feat..x", "-x", "@{-1}", "a b"):
            with self.subTest(branch=branch):
                self.refused(self.archive(".env", branch=branch))
                self.assertEqual((self.linked / ".env").read_text(), "x\n")
        self.assertFalse((self.main / ".vibe-workflow").exists())


if __name__ == "__main__":
    unittest.main()
