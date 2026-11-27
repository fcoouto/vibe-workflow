#!/usr/bin/env python3
"""List a worktree's leftover files by their stored names, or fingerprint paths in it. It only reads."""
import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys

LISTED = (b"node_modules/", b"__pycache__/", b".pytest_cache/", b".mypy_cache/", b".ruff_cache/", b".DS_Store")
SORT = ("status", "--porcelain=v1", "-z", "--ignored", "--untracked-files=normal")
EXPAND = ("status", "--porcelain=v1", "-z", "--untracked-files=all")


class Refused(Exception):
    """The listing cannot be trusted; nothing is printed."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("invalid arguments; give sort, expand or manifest, then --worktree <dir> and any --path <relative path>")


def _git(worktree, *args):
    """Run Git in the worktree without the Git variables this process inherited. Pathspecs are literal names."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        done = subprocess.run(["git", "--literal-pathspecs", "-C", worktree, *args], env={**env, "LC_ALL": "C"},
                              capture_output=True, timeout=120)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise Refused("Git could not run") from error
    if done.returncode:
        raise Refused("Git could not read the worktree")
    return done.stdout


def _top(worktree):
    """Paths in a listing are relative to the top folder, so the worktree must be named by it."""
    top = _git(worktree, "rev-parse", "--show-toplevel").removesuffix(b"\n")
    try:
        if not os.path.samefile(top, worktree):
            raise Refused("--worktree is not the worktree's top folder")
    except OSError as error:
        raise Refused("--worktree is not a folder") from error
    return os.fsencode(worktree)


def _relative(path):
    """One stored name below the top folder, as bytes. One trailing slash is accepted, as Git prints a folder."""
    name = os.fsencode(path)
    name = name[:-1] if name.endswith(b"/") else name
    if name.startswith(b"/") or any(part in (b"", b".", b"..", b".git") for part in name.split(b"/")):
        raise Refused("each --path must be a relative path with no empty, '.', '..' or '.git' part")
    return name


def _unsafe(name):
    """A control character or bytes that are not UTF-8: such a name cannot be typed into a command safely."""
    try:
        name.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return any(byte < 0x20 or byte == 0x7F for byte in name)


def _rebuildable(status, name):
    """Ignored, and the last path component, as Git printed it, is a listed name or a compiled Python file."""
    if status != "!!" or _unsafe(name):
        return False
    folder = name.endswith(b"/")
    last = name[:-1].rsplit(b"/", 1)[-1] + b"/" if folder else name.rsplit(b"/", 1)[-1]
    return last in LISTED or (not folder and last.endswith(b".pyc"))


def _entries(raw, only=None):
    """One row per NUL-ended status entry. A rename or copy is followed by one more field, its old name."""
    fields = raw.split(b"\0")
    if fields.pop() != b"":
        raise Refused("Git's listing is incomplete")
    rows = []
    while fields:
        entry = fields.pop(0)
        if len(entry) < 4 or entry[2:3] != b" ":
            raise Refused("Git's listing could not be read")
        status, name = entry[:2].decode("ascii", "replace"), entry[3:]
        row = {"status": status, "path": os.fsdecode(name)}
        if "R" in status or "C" in status:
            if not fields:
                raise Refused("Git's listing is incomplete")
            row["from"] = os.fsdecode(fields.pop(0))
        row["group"] = "rebuildable" if _rebuildable(status, name) else "real work"
        if _unsafe(name):
            row["unsafe"] = True
        if only is None or status == only:
            rows.append(row)
    return rows


def _manifest(top, name):
    """Every entry at and below one path, by stored name, type and content; links are recorded, never followed."""
    rows = []
    pending = [name]
    while pending:
        here = pending.pop()
        full = os.path.join(top, here)
        mode = os.lstat(full).st_mode
        row = {"path": os.fsdecode(here)}
        if stat.S_ISDIR(mode):
            row["type"] = "folder"
            pending += sorted((os.path.join(here, child) for child in os.listdir(full)), reverse=True)
        elif stat.S_ISLNK(mode):
            row["type"], row["target"] = "link", os.fsdecode(os.readlink(full))
        elif stat.S_ISREG(mode):
            descriptor = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with open(descriptor, "rb") as file:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise Refused(f"{os.fsdecode(here)!r} changed while it was read")
                row["type"], row["sha256"] = "file", hashlib.file_digest(file, "sha256").hexdigest()
        else:
            row["type"] = "other"
        rows.append(row)
    lines = [json.dumps(row, sort_keys=True) for row in rows]
    digest = hashlib.sha256("\n".join(lines).encode("ascii")).hexdigest()
    return [*lines, json.dumps({"digest": digest, "path": os.fsdecode(name), "entries": len(lines)})]


def listing(mode, worktree, paths):
    top = _top(worktree)
    names = [_relative(path) for path in paths]
    if mode == "sort":
        if names:
            raise Refused("sort takes no --path")
        return [json.dumps(row) for row in _entries(_git(worktree, *SORT))]
    if mode == "expand":
        return [json.dumps(row) for row in _entries(_git(worktree, *EXPAND, "--", *names), only="??")]
    if not names:
        raise Refused("manifest needs one or more --path")
    try:
        return [line for name in names for line in _manifest(top, name)]
    except (OSError, RecursionError) as error:
        raise Refused(f"a path could not be read ({type(error).__name__})") from error


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("mode", choices=("sort", "expand", "manifest"))
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--path", dest="paths", action="append", default=[])
    try:
        args = parser.parse_args(argv)
        lines = listing(args.mode, args.worktree, args.paths)
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
