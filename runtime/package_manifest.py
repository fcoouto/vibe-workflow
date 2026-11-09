"""Which paths of a plugin root belong to its package; standard library only, no network."""

import os
from pathlib import Path
import selectors
import stat
import subprocess
import time


# Codex's own codex-plugin-bundle-v1 limits, which the bundle scan in runtime/setup_observation.py mirrors.
# Codex owns them: a package this scan accepted but Codex rejected would fail at install. The scan keeps
# total bytes below PACKAGE_SCAN_MAX_BYTES and charges paths as export_usage() counts them.
# runtime/startup.py _entry_budget_check enforces them for every entry observation, the scan included, so
# they must not follow onboarding's project inventory bound (runtime/onboarding.py MAX_TOTAL).
PACKAGE_SCAN_MAX_BYTES = 8 * 1024 * 1024
PACKAGE_SCAN_MAX_FILE = 1024 * 1024
PACKAGE_SCAN_MAX_PATHS = 4096
# Development-only trees: nothing installed reads them, and they would not fit the bundle scan.
# Local client configuration stays with the checkout.
EXCLUDED = ("tests/", "scripts/test_sharded.py", ".vibe-workflow/", ".codex/", "CLAUDE.md")
# Inherited repository variables (for example from a Git hook) would override -C. scripts/package.py drops
# these; membership drops every GIT_* variable, as runtime/startup.py read_git does.
GIT_ENVIRONMENT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
GIT_SECONDS = 10
MAX_LISTING = 4 * 1024 * 1024


class PackageManifestError(ValueError):
    """Membership could not be established; callers must not widen to the whole directory."""


def excluded(name):
    # Entries ending in "/" are directories; others name one file, as in the release pathspecs.
    return any(name.startswith(item) if item.endswith("/") else name == item for item in EXCLUDED)


def _git(root, *args, deadline):
    """Run read-only Git in root only: no inherited selectors, configured commands, locks or discovery above root."""
    unavailable = PackageManifestError("Package membership is unavailable")
    if deadline - time.monotonic() <= 0:
        raise PackageManifestError("Package membership deadline exceeded")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_CEILING_DIRECTORIES=str(root.parent))
    try:
        process = subprocess.Popen(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except OSError:
        raise unavailable from None
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise unavailable
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output += chunk
                if len(output) > MAX_LISTING:
                    raise unavailable
        process.wait(max(0, deadline - time.monotonic()))
    except (OSError, subprocess.SubprocessError):
        raise unavailable from None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    if process.returncode != 0:
        raise unavailable
    return bytes(output)


def export_members(root, *, deadline=None):
    """Return root's export candidates: relative paths with their parent directories, or None for no filter.

    A root with its own .git entry is a checkout: its tracked paths minus EXCLUDED, which scripts/package.py
    copies. Packaging also refuses tracked symlinks and skips tracked files missing from the working tree;
    a scan that meets a tracked symlink fails too. Git must find root itself as the top level: an invalid
    .git never falls through to an enclosing repository. Any other directory is already an export.
    Names come from Git as stored; a file whose on-disk name differs only by Unicode normalization is not
    matched. deadline is a time.monotonic() value. Every failure raises PackageManifestError.
    """
    root = Path(root)
    if not os.path.lexists(root / ".git"):
        return None
    deadline = time.monotonic() + GIT_SECONDS if deadline is None else deadline
    resolved = root.resolve()
    try:
        top = _git(resolved, "rev-parse", "--show-toplevel", deadline=deadline).decode("utf-8")
    except UnicodeDecodeError:
        top = ""
    if not top.endswith("\n") or os.path.realpath(top[:-1]) != str(resolved):
        raise PackageManifestError("Package membership is unavailable")
    try:
        names = _git(resolved, "ls-files", "-z", deadline=deadline).decode("utf-8").split("\0")
    except UnicodeDecodeError:
        raise PackageManifestError("Package membership is unavailable") from None
    members = set()
    for name in names:
        if name and not excluded(name):
            parts = name.split("/")
            members.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return frozenset(members)


def scan_skips(root, path, members):
    """Whether the bundle scan leaves out path below root: build caches, and anything a checkout does not export."""
    return (path.name in ("__pycache__", ".venv") or path.name.endswith((".pyc", ".egg-info"))
            or members is not None and path.relative_to(root).as_posix() not in members)


def export_usage(root, *, deadline=None):
    """Measure, by file metadata, the export of root as the bundle scan would read it.

    The export is a checkout's membership, or any other directory as it is. Development-only paths are
    listed in "excluded" and not measured: a checkout's membership never has them, and an export that still
    does would have them scanned and installed. Errors for unsupported entries.
    """
    root = Path(root)
    members = export_members(root, deadline=deadline)
    usage = dict(files=0, directories=0, bytes=0, largest_file=None, largest_bytes=0,
                 excluded=[name for name in members or () if excluded(name)])
    pending = [root]
    while pending:
        path = pending.pop()
        relative = path.relative_to(root).as_posix()
        if path != root and scan_skips(root, path, members):
            continue
        info = path.lstat()
        if path != root and (excluded(relative) or stat.S_ISDIR(info.st_mode) and excluded(relative + "/")):
            usage["excluded"].append(relative + "/" if stat.S_ISDIR(info.st_mode) else relative)
            continue
        if stat.S_ISDIR(info.st_mode):
            usage["directories"] += path != root
            pending.extend(path.iterdir())
        elif stat.S_ISREG(info.st_mode):
            usage["files"] += 1
            usage["bytes"] += info.st_size
            if info.st_size > usage["largest_bytes"]:
                usage["largest_file"], usage["largest_bytes"] = relative, info.st_size
        else:
            raise PackageManifestError(f"Package export has an unsupported entry: {relative}")
    usage["excluded"].sort()
    # The scan charges each entry when listed and when visited, and each file once more when read.
    usage["scan_paths"] = 1 + 3 * usage["files"] + 2 * usage["directories"]
    return usage
