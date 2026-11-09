"""Which paths of a plugin root belong to its package; standard library only, no network."""

import os
from pathlib import Path
import subprocess
import time


# Development-only trees: nothing installed reads them, and Codex's bundle scan allows 8 MiB in total.
# Local client configuration stays with the checkout.
EXCLUDED = ("tests/", "scripts/test_sharded.py", ".vibe-workflow/", ".codex/", "CLAUDE.md")
# Inherited repository variables (for example from a Git hook) would override -C.
GIT_ENVIRONMENT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")
GIT_SECONDS = 10


class PackageManifestError(ValueError):
    """Membership could not be established; callers must not widen to the whole directory."""


def excluded(name):
    # Entries ending in "/" are directories; others name one file, as in the release pathspecs.
    return any(name.startswith(item) if item.endswith("/") else name == item for item in EXCLUDED)


def export_members(root, *, deadline=None):
    """Return the relative paths, with their parent directories, that scripts/package.py exports from root.

    A root with its own Git metadata is a checkout: its tracked files minus EXCLUDED. Any other
    directory is already an export, so None means no filter. deadline is a time.monotonic() value.
    """
    root = Path(root)
    if not os.path.lexists(root / ".git"):
        return None
    remaining = (time.monotonic() + GIT_SECONDS if deadline is None else deadline) - time.monotonic()
    if remaining <= 0:
        raise PackageManifestError("Package membership deadline exceeded")
    env = {key: value for key, value in os.environ.items() if key not in GIT_ENVIRONMENT}
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], stdin=subprocess.DEVNULL,
                                capture_output=True, env=env, timeout=remaining)
        if result.returncode != 0:
            raise ValueError(result.returncode)
        names = result.stdout.decode("utf-8").split("\0")
    except (OSError, ValueError, subprocess.SubprocessError):
        raise PackageManifestError("Package membership is unavailable") from None
    members = set()
    for name in names:
        if name and not excluded(name):
            parts = name.split("/")
            members.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return frozenset(members)
