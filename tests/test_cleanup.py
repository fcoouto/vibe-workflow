"""Cleanup inspection uses isolated repositories; it never removes user resources."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime import cleanup


ROOT = Path(__file__).resolve().parents[1]


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cleanup-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        home = self.base / "home"
        home.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"))
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.primary = self.base / "primary"
        self.primary.mkdir()
        self.git(self.primary, "init", "-b", "trunk")
        (self.primary / "file.txt").write_text("base\n")
        (self.primary / ".gitignore").write_text("ignored/\n.vibe-workflow/private/\n")
        self.settings(enabled=True)
        self.git(self.primary, "add", ".")
        self.git(self.primary, "commit", "-m", "Base")
        self.worktree = self.base / "task"
        self.git(self.primary, "worktree", "add", "-b", "task", str(self.worktree))
        (self.worktree / "file.txt").write_text("merged task\n")
        self.git(self.worktree, "commit", "-am", "Task")
        self.head = self.git(self.worktree, "rev-parse", "HEAD").strip()
        self.git(self.primary, "merge", "--ff-only", "task")
        self.merge = self.head

    def git(self, root, *args, check=True):
        result = subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                                 "user.email=fixture@example.invalid", "-C", str(root), *args],
                                capture_output=True, text=True, timeout=10)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout if check else result

    def settings(self, enabled=True, remote=True):
        path = self.primary / ".vibe-workflow/settings.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "revision": 1, "features": {
            "cleanup": {"enabled": enabled, "values": {"delete_remote_branch": remote}}}}))

    def inspect(self, **overrides):
        args = dict(primary=self.primary, primary_branch="trunk", worktree=self.worktree,
                    branch="task", head=self.head, merge=self.merge)
        args.update(overrides)
        return cleanup.inspect(**args)

    def test_clean_merged_task_is_only_locally_eligible_and_never_removed(self):
        result = self.inspect()
        self.assertEqual(result["status"], "eligible")
        self.assertTrue(result["delete_remote_branch"])
        self.assertEqual(result["ref"], "refs/heads/task")
        self.assertTrue(self.worktree.is_dir())
        self.assertEqual(self.git(self.primary, "symbolic-ref", "HEAD").strip(), "refs/heads/trunk")

    def test_missing_false_and_malformed_settings_do_not_enable_cleanup(self):
        path = self.primary / ".vibe-workflow/settings.json"
        for content in (None, "{broken", json.dumps({"schema_version": 1, "revision": 1,
                                                  "features": {"cleanup": {"enabled": False}}})):
            with self.subTest(content=content):
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(content)
                self.assertEqual(self.inspect()["status"], "retained")
                self.assertTrue(self.worktree.exists())

    def test_git_environment_cannot_redirect_settings_or_later_native_effects(self):
        with patch.dict(os.environ, GIT_WORK_TREE=str(self.worktree)):
            self.assertEqual(self.inspect()["status"], "retained")

    def test_remote_override_does_not_disable_local_cleanup(self):
        self.settings(remote=False)
        self.git(self.primary, "commit", "-am", "Disable remote deletion")
        result = self.inspect()
        self.assertEqual(result["status"], "eligible")
        self.assertFalse(result["delete_remote_branch"])

    def test_dirty_untracked_and_ignored_files_are_preserved(self):
        for relative in ("file.txt", "notes.txt", "ignored/backup.txt"):
            with self.subTest(relative=relative):
                file = self.worktree / relative
                before = file.read_bytes() if file.exists() else None
                file.parent.mkdir(exist_ok=True)
                file.write_bytes(b"unfinished work\n")
                self.assertEqual(self.inspect()["status"], "retained")
                self.assertEqual(file.read_bytes(), b"unfinished work\n")
                if before is None:
                    file.unlink()
                else:
                    file.write_bytes(before)

    def test_hidden_index_changes_are_not_a_clean_worktree(self):
        for flag, undo in (("--assume-unchanged", "--no-assume-unchanged"),
                           ("--skip-worktree", "--no-skip-worktree")):
            with self.subTest(flag=flag):
                self.git(self.worktree, "update-index", flag, "file.txt")
                (self.worktree / "file.txt").write_text("hidden edit\n")
                self.assertEqual(self.inspect()["status"], "retained")
                self.git(self.worktree, "update-index", undo, "file.txt")
                (self.worktree / "file.txt").write_text("merged task\n")

    def test_locked_worktree_and_unresolved_git_operation_are_retained(self):
        self.git(self.primary, "worktree", "lock", str(self.worktree))
        self.assertEqual(self.inspect()["status"], "retained")
        self.git(self.primary, "worktree", "unlock", str(self.worktree))
        directory = Path(self.git(self.worktree, "rev-parse", "--absolute-git-dir").strip())
        (directory / "MERGE_HEAD").write_text(self.head)
        self.assertEqual(self.inspect()["status"], "retained")

    def test_submodule_index_is_retained(self):
        self.git(self.worktree, "update-index", "--add", "--cacheinfo", f"160000,{self.head},vendor")
        self.assertEqual(self.inspect()["status"], "retained")

    def test_changed_head_and_unincluded_merge_are_retained(self):
        (self.worktree / "file.txt").write_text("new unmerged work\n")
        self.git(self.worktree, "commit", "-am", "New work")
        changed = self.git(self.worktree, "rev-parse", "HEAD").strip()
        self.assertEqual(self.inspect()["status"], "retained")
        self.assertEqual(self.inspect(head=changed, merge=changed)["status"], "retained")

    def test_squash_merge_inclusion_does_not_require_head_ancestry(self):
        self.git(self.primary, "checkout", "-b", "squashed", "HEAD~1")
        self.git(self.primary, "merge", "--squash", "task")
        self.git(self.primary, "commit", "-m", "Squash")
        squash = self.git(self.primary, "rev-parse", "HEAD").strip()
        self.assertEqual(self.inspect(primary_branch="squashed", merge=squash)["status"], "eligible")

    def test_primary_target_or_linked_primary_is_rejected(self):
        self.assertEqual(self.inspect(worktree=self.primary, branch="trunk")["status"], "retained")
        self.assertEqual(self.inspect(primary=self.worktree, primary_branch="task")["status"], "retained")

    def test_detached_or_dirty_primary_is_retained_without_repair(self):
        (self.primary / "file.txt").write_text("primary work\n")
        self.assertEqual(self.inspect()["status"], "retained")
        self.assertEqual((self.primary / "file.txt").read_text(), "primary work\n")
        self.git(self.primary, "checkout", "--detach")
        self.assertEqual(self.inspect()["status"], "retained")

    def test_removed_phase_requires_absent_worktree_and_unoccupied_unchanged_ref(self):
        self.assertEqual(self.inspect(removed=True)["status"], "retained")
        self.git(self.primary, "worktree", "remove", str(self.worktree))
        self.assertEqual(self.inspect(removed=True)["status"], "eligible")
        other = self.base / "other"
        self.git(self.primary, "worktree", "add", str(other), "task")
        self.assertEqual(self.inspect(removed=True)["status"], "retained")

    def test_symbolic_task_ref_is_not_deleted_as_an_alias(self):
        self.git(self.primary, "worktree", "remove", str(self.worktree))
        self.git(self.primary, "symbolic-ref", "refs/heads/task", "refs/heads/trunk")
        self.assertEqual(self.inspect(removed=True)["status"], "retained")

    def test_simple_https_remote_must_match_exact_repository(self):
        self.git(self.primary, "remote", "add", "origin", "https://github.com/example/project.git")
        self.assertEqual(cleanup.remote_destination(self.primary, "origin", "example/project"),
                         "https://github.com/example/project.git")
        with self.assertRaises(ValueError):
            cleanup.remote_destination(self.primary, "origin", "other/project")

    def test_push_rewrites_pushurls_and_http_redirect_configuration_are_unsupported(self):
        self.git(self.primary, "remote", "add", "origin", "https://github.com/example/project.git")
        for key, value in (("url.https://other.invalid/.pushInsteadOf", "https://github.com/"),
                           ("url.https://other.invalid/.insteadOf", "https://github.com/"),
                           ("remote.origin.pushurl", "https://github.com/other/project.git"),
                           ("http.followRedirects", "true")):
            with self.subTest(key=key):
                self.git(self.primary, "config", key, value)
                with self.assertRaises(ValueError):
                    cleanup.remote_destination(self.primary, "origin", "example/project")
                self.git(self.primary, "config", "--unset-all", key)

    def test_custom_transport_environment_and_active_push_hook_are_unsupported(self):
        self.git(self.primary, "remote", "add", "origin", "https://github.com/example/project.git")
        with patch.dict(os.environ, GIT_CONFIG_COUNT="0"), self.assertRaises(ValueError):
            cleanup.remote_destination(self.primary, "origin", "example/project")
        hook = self.primary / ".git/hooks/pre-push"
        hook.write_text("#!/bin/sh\nexit 0\n")
        hook.chmod(0o700)
        with self.assertRaises(ValueError):
            cleanup.remote_destination(self.primary, "origin", "example/project")

    def test_cli_reports_retention_without_mutating_or_emitting_a_traceback(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/cleanup.py"),
                                 "--primary", str(self.primary), "--primary-branch", "trunk",
                                 "--worktree", str(self.worktree), "--branch", "task",
                                 "--head", "invalid", "--merge", self.merge], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "retained")
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(self.worktree.exists())

    def command(self, marker, values):
        source = (ROOT / "clients/delivery.md").read_text()
        block = re.search(r"<!-- " + marker + r" -->\s*```sh\n(.*?)\n```", source, re.S)
        self.assertIsNotNone(block, "missing executable conditional deletion command")
        return [values.get(part, part) for part in shlex.split(block[1])]

    def test_documented_remote_delete_preserves_an_advanced_branch(self):
        remote = self.base / "remote.git"
        self.git(self.primary, "init", "--bare", str(remote))
        self.git(self.primary, "push", str(remote), "task")
        command = self.command("cleanup-remote-delete", {
            "$PRIMARY": str(self.primary), "$DESTINATION": str(remote),
            "--force-with-lease=$REF:$HEAD": f"--force-with-lease=refs/heads/task:{self.head}",
            ":$REF": ":refs/heads/task"})
        (self.worktree / "file.txt").write_text("advanced\n")
        self.git(self.worktree, "commit", "-am", "Advanced")
        newer = self.git(self.worktree, "rev-parse", "HEAD").strip()
        self.git(self.worktree, "push", str(remote), "task")
        result = subprocess.run(command, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(remote, "rev-parse", "refs/heads/task").strip(), newer)
        command = [arg.replace(self.head, newer) for arg in command]
        self.assertEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 0)
        self.assertNotEqual(self.git(remote, "show-ref", "--verify", "refs/heads/task", check=False).returncode, 0)

    def test_documented_local_delete_preserves_changed_ref(self):
        self.git(self.primary, "worktree", "remove", str(self.worktree))
        command = self.command("cleanup-local-delete", {
            "$PRIMARY": str(self.primary), "$REF": "refs/heads/task", "$HEAD": self.head})
        older = self.git(self.primary, "rev-parse", "HEAD~1").strip()
        self.git(self.primary, "update-ref", "refs/heads/task", older, self.head)
        self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=10).returncode, 0)
        self.assertEqual(self.git(self.primary, "rev-parse", "refs/heads/task").strip(), older)


if __name__ == "__main__":
    unittest.main()
