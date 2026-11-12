"""Exercise the delivery contract's native observations, not an enforcement claim."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from scripts import read_instructions as reader


ROOT = Path(__file__).resolve().parents[1]
PROCEDURE = "clients/delivery.md"


class CheckoutStabilityInstructionsTests(unittest.TestCase):
    def test_selected_procedure_is_delivered_completely_without_managed_controls(self):
        paths = ["policies/02-task-planning.md", PROCEDURE]
        batch = reader.load_batch(ROOT, paths, auto=True)
        revision, delivered = batch["revision"], {}
        while True:
            for section in batch["sections"]:
                delivered.setdefault(section["path"], "")
                delivered[section["path"]] += section["text"]
            if batch["last_page"] == batch["pages"]:
                break
            batch = reader.load_batch(ROOT, paths, batch["last_page"] + 1, revision, auto=True)
        self.assertEqual(list(delivered), paths)
        for path in paths:
            self.assertEqual(delivered[path], (ROOT / path).read_text())
        self.assertNotIn("clients/runtime.md", delivered)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="checkout-observations-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.primary = self.directory / "primary folder"
        self.primary.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="Fixture",
                        GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_NAME="Fixture",
                        GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.git(self.primary, "init", "-b", "trunk")
        (self.primary / "file.txt").write_text("initial\n")
        self.git(self.primary, "add", "file.txt")
        self.git(self.primary, "commit", "-m", "Initial fixture")

    def git(self, root, *arguments):
        result = subprocess.run(["git", "-C", str(root), *arguments], env=self.env,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout

    def observations(self, checkout):
        source = (ROOT / PROCEDURE).read_text()
        block = re.search(r"<!-- checkout-observations -->\s*```sh\n(.*?)\n```", source, re.S)
        self.assertIsNotNone(block, "delivery contract lacks executable checkout observations")
        values = {"$CHECKOUT": str(checkout), "$PRIMARY": str(self.primary)}
        results = []
        for line in block[1].splitlines():
            argv = [values.get(part, part) for part in shlex.split(line)]
            self.assertEqual(argv[0], "git")
            results.append(subprocess.run(argv, env=self.env, capture_output=True, timeout=10))
        self.assertEqual(len(results), 4)
        return results

    def test_linked_and_symlinked_destinations_observe_primary_without_switching(self):
        task = self.directory / "task folder"
        self.git(self.primary, "worktree", "add", "-b", "task", str(task))
        alias = self.directory / "task alias"
        alias.symlink_to(task, target_is_directory=True)
        for checkout in (task, alias):
            with self.subTest(checkout=checkout):
                identity, worktrees, branch, dirty = self.observations(checkout)
                self.assertTrue(all(result.returncode == 0 for result in (identity, worktrees, branch, dirty)))
                self.assertEqual(identity.stdout.decode().splitlines(),
                                 [str(task), str(self.primary / ".git/worktrees" / task.name),
                                  str(self.primary / ".git")])
                self.assertTrue(worktrees.stdout.startswith(f"worktree {self.primary}\0".encode()))
                self.assertIn(f"worktree {task}\0".encode(), worktrees.stdout)
                self.assertEqual(branch.stdout, b"refs/heads/trunk\n")
                self.assertEqual(dirty.stdout, b"")
        self.assertEqual(self.git(task, "symbolic-ref", "HEAD"), b"refs/heads/task\n")

    def test_dirty_detached_primary_is_observed_without_repair_or_file_changes(self):
        self.git(self.primary, "checkout", "--detach")
        (self.primary / "file.txt").write_bytes(b"unfinished change\n")
        (self.primary / "untracked.txt").write_bytes(b"keep me\n")
        head = (self.primary / ".git/HEAD").read_bytes()
        identity, worktrees, branch, dirty = self.observations(self.primary)
        self.assertEqual(identity.returncode, 0)
        self.assertEqual(worktrees.returncode, 0)
        self.assertNotEqual(branch.returncode, 0)
        self.assertEqual(branch.stdout, b"")
        self.assertEqual(dirty.returncode, 0)
        self.assertIn(b" M file.txt\0", dirty.stdout)
        self.assertIn(b"?? untracked.txt\0", dirty.stdout)
        self.assertEqual((self.primary / ".git/HEAD").read_bytes(), head)
        self.assertEqual((self.primary / "file.txt").read_bytes(), b"unfinished change\n")
        self.assertEqual((self.primary / "untracked.txt").read_bytes(), b"keep me\n")

    def test_fast_forward_changes_commit_without_changing_primary_branch_identity(self):
        task = self.directory / "task"
        self.git(self.primary, "worktree", "add", "-b", "task", str(task))
        before = self.observations(task)[2].stdout
        old_head = self.git(self.primary, "rev-parse", "HEAD")
        (task / "file.txt").write_text("task change\n")
        self.git(task, "commit", "-am", "Task fixture")
        self.git(self.primary, "merge", "--ff-only", "task")
        after = self.observations(task)[2].stdout
        self.assertEqual(before, b"refs/heads/trunk\n")
        self.assertEqual(after, before)
        self.assertNotEqual(self.git(self.primary, "rev-parse", "HEAD"), old_head)


if __name__ == "__main__":
    unittest.main()
