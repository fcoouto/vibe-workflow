"""Read-only cleanup prerequisites, not permission, provider proof or writer exclusion.

The native coordinator owns effects under clients/delivery.md. No removal,
network operation, retry, journal or managed delivery admission lives here.
"""
import os
from pathlib import Path
import re
import subprocess

from runtime import features
from runtime.startup import read_git


class CleanupError(ValueError):
    """A fixed, non-sensitive reason for retaining a cleanup target."""


def _require(condition, reason):
    if not condition:
        raise CleanupError(reason)


def _git(root, *args, allowed=(0,)):
    code, output = read_git(root, *args)
    _require(code in allowed, "git-observation-unavailable")
    return output


def _identity(root):
    rows = _git(root, "rev-parse", "--path-format=absolute", "--show-toplevel",
                "--git-dir", "--git-common-dir").decode().splitlines()
    _require(len(rows) == 3 and Path(rows[0]).resolve() == root, "checkout-identity-mismatch")
    return Path(rows[1]).resolve(), Path(rows[2]).resolve()


def _worktrees(primary):
    rows = []
    for record in _git(primary, "worktree", "list", "--porcelain", "-z").split(b"\0\0"):
        if not record:
            continue
        fields = {}
        for field in record.split(b"\0"):
            if field:
                key, _, value = field.partition(b" ")
                fields[key.decode()] = os.fsdecode(value)
        _require("worktree" in fields, "worktree-observation-unavailable")
        fields["path"] = Path(fields["worktree"]).resolve()
        rows.append(fields)
    _require(bool(rows), "worktree-observation-unavailable")
    return rows


def _clean(root, *, ignored=False):
    args = ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"]
    if ignored:
        args.append("--ignored=matching")
    _require(not _git(root, *args), "unfinished-files")
    entries = _git(root, "ls-files", "-v", "-z").split(b"\0")
    _require(not any(row and (row[:1].islower() or row[:1] == b"S") for row in entries),
             "hidden-index-flags")
    entries = _git(root, "ls-files", "--stage", "-z").split(b"\0")
    _require(not any(row.startswith(b"160000 ") for row in entries), "submodules-unsupported")
    git_dir, common = _identity(root)
    for directory in {git_dir, common}:
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD",
                     "BISECT_LOG", "rebase-apply", "rebase-merge", "sequencer"):
            _require(not os.path.lexists(directory / name), "git-operation-in-progress")
        _require(not list(directory.glob("*.lock")), "git-operation-locked")


def inspect(primary, primary_branch, worktree, branch, head, merge, removed=False):
    """Inspect exact local targets; after native removal pass removed=True before deleting refs."""
    try:
        primary = Path(primary).resolve(strict=True)
        worktree = Path(worktree).resolve()
        git_dir, common = _identity(primary)
        _require(git_dir == common, "not-primary-worktree")
        rows = _worktrees(primary)
        _require(rows[0]["path"] == primary, "not-primary-worktree")
        _require(primary != worktree, "primary-is-not-a-cleanup-target")
        _require(primary_branch != branch, "primary-branch-is-not-a-cleanup-target")
        for name in (primary_branch, branch):
            _require(isinstance(name, str) and bool(name), "invalid-branch")
            _git(primary, "check-ref-format", "refs/heads/" + name)
        for oid in (head, merge):
            _require(isinstance(oid, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid),
                     "invalid-commit")
            _git(primary, "cat-file", "-e", oid + "^{commit}")
        choice = features.resolve(primary)["features"].get("cleanup", {})
        _require(choice.get("effective") is True, "cleanup-disabled-or-invalid")
        _require(_git(primary, "symbolic-ref", "--quiet", "HEAD").decode().strip() ==
                 "refs/heads/" + primary_branch, "primary-branch-mismatch")
        _clean(primary)
        _git(primary, "merge-base", "--is-ancestor", merge, "refs/heads/" + primary_branch)
        ref = "refs/heads/" + branch
        code, _ = read_git(primary, "symbolic-ref", "--quiet", ref)
        _require(code == 1, "symbolic-or-unavailable-task-ref")
        _require(_git(primary, "rev-parse", "--verify", ref).decode().strip() == head, "task-ref-changed")
        _require(not os.path.lexists(common / (ref + ".lock")), "task-ref-locked")
        attachments = [row for row in rows if row.get("branch") == ref]
        matching = [row for row in rows if row["path"] == worktree]
        if removed:
            _require(not os.path.lexists(worktree) and not matching, "worktree-still-present")
            _require(not attachments, "branch-still-in-use")
        else:
            _require(len(matching) == 1 and attachments == matching, "worktree-branch-mismatch")
            _require(not any(key in matching[0] for key in ("locked", "prunable")), "worktree-unavailable-or-locked")
            task_git, task_common = _identity(worktree)
            _require(task_git != task_common and task_common == common, "task-repository-mismatch")
            _require(_git(worktree, "rev-parse", "HEAD").decode().strip() == head, "task-head-changed")
            _clean(worktree, ignored=True)
        return dict(status="eligible", reason="local-checks-only", primary=str(primary),
                    worktree=str(worktree), ref=ref, head=head, merge=merge,
                    delete_remote_branch=choice["values"]["delete_remote_branch"])
    except CleanupError as error:
        return dict(status="retained", reason=str(error))
    except (OSError, ValueError, subprocess.SubprocessError):
        # Diagnostics never echo Git output, config values, or private filenames.
        return dict(status="retained", reason="local-prerequisite-not-met")


def remote_destination(primary, remote, repository):
    """Return a checked ordinary GitHub HTTPS destination, without contacting it.

The coordinator must separately observe the PR/repository identity and permission.
Reject custom transports instead of trying to emulate Git's configuration engine.
"""
    _require(re.fullmatch(r"[A-Za-z0-9_-]+", remote or ""), "unsupported-remote-name")
    _require(re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repository or "") and
             repository.split("/")[1] not in (".", ".."), "unsupported-repository")
    unsupported_env = any(key.startswith("GIT_") and key not in {"GIT_TERMINAL_PROMPT", "GIT_OPTIONAL_LOCKS"}
                          or key.lower() in {"http_proxy", "https_proxy", "all_proxy"} for key in os.environ)
    _require(not unsupported_env, "custom-transport-environment")
    pattern = (r"^(url\..*|http\..*|core\.(sshcommand|gitproxy|hookspath)|"
               r"remote\..*\.(pushurl|receivepack|uploadpack|proxy|vcs|mirror|push))$")
    _require(not _git(primary, "config", "--null", "--get-regexp", pattern, allowed=(0, 1)),
             "custom-transport-configuration")
    hook = Path(os.fsdecode(_git(primary, "rev-parse", "--git-path", "hooks/pre-push")).strip())
    if not hook.is_absolute():
        hook = Path(primary) / hook
    _require(not hook.is_symlink() and not os.access(hook, os.X_OK), "push-hook-unsupported")
    urls = _git(primary, "config", "--get-all", f"remote.{remote}.url").decode().splitlines()
    destination = f"https://github.com/{repository}.git"
    _require(len(urls) == 1 and urls[0] in (destination, destination[:-4]), "remote-repository-mismatch")
    for flag in ((), ("--push",)):
        effective = _git(primary, "remote", "get-url", *flag, "--all", remote).decode().splitlines()
        _require(effective == urls, "remote-destination-mismatch")
    return urls[0]
