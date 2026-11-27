#!/usr/bin/env python3
"""Save a security report in the main checkout's private reports folder, or refuse and write no report."""
import argparse
import os
import re
import stat
import subprocess
import sys
import time

BASE, REPORTS = ".vibe-workflow", "security-reports"
FOLDER = f"{BASE}/{REPORTS}"
IGNORE = b"*\n"
TOPIC = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


class Refused(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("invalid arguments; give --cwd <dir>, --mode full|ship and --topic <slug>")


def git(directory, *args):
    """Run Git for one checkout, without the Git variables this process inherited."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", directory, *args], env={**env, "LC_ALL": "C"},
                              capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise Refused("Git could not run") from error


def main_checkout(cwd):
    """The main checkout is the first worktree record; a bare or unresolved one has no place for reports."""
    listed = git(cwd, "worktree", "list", "--porcelain", "-z")
    if listed.returncode:
        if "not a git repository" in listed.stderr:
            raise Refused("not a Git repository")
        raise Refused("the main checkout could not be resolved")
    path, *attributes = listed.stdout.split("\0\0")[0].split("\0")
    if not path.startswith("worktree ") or "bare" in attributes:
        raise Refused("the repository has no main checkout")
    try:
        main = os.path.realpath(path[len("worktree "):], strict=True)
    except OSError as error:
        raise Refused("the main checkout could not be resolved") from error
    top = git(main, "rev-parse", "--show-toplevel")
    if top.returncode or os.path.realpath(top.stdout.removesuffix("\n")) != main:
        raise Refused("the main checkout could not be resolved")
    return main


def _kind(path):
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return None


def _open_folder(name, parent):
    """Create the folder when missing, then open it without following a link."""
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    except FileExistsError:
        pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except OSError as error:
        raise Refused(f"{name} is a symlink or not a directory") from error


def _ensure_ignore(folder):
    """The folder's own .gitignore must be the single line `*`. A differing regular file is replaced, never edited."""
    try:
        mode = os.lstat(".gitignore", dir_fd=folder).st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None:
        if not stat.S_ISREG(mode):
            raise Refused("the reports folder's .gitignore is a symlink or not a regular file")
        current = os.open(".gitignore", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        with os.fdopen(current, "rb") as file:
            if file.read(len(IGNORE) + 1) == IGNORE:
                return
    temporary = f".gitignore.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    handle = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=folder)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(IGNORE)
        os.replace(temporary, ".gitignore", src_dir_fd=folder, dst_dir_fd=folder)
    except BaseException:
        os.unlink(temporary, dir_fd=folder)
        raise


def _create(folder, stem):
    """Create a new report file. An existing name, link or not, is never opened."""
    for suffix in ("", *(f"-{number}" for number in range(2, 100))):
        name = f"{stem}{suffix}.md"
        try:
            return name, os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        except FileExistsError:
            continue
    raise Refused("no free report name is left for this minute")


def store(cwd, mode, topic, body, now=None):
    if mode not in ("full", "ship"):
        raise Refused("mode must be full or ship")
    if not TOPIC.fullmatch(topic):
        raise Refused("topic must be lowercase letters, digits and hyphens, at most 61 characters")
    if not body:
        raise Refused("the report is empty")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid") or os.open not in os.supports_dir_fd:
        raise Refused("this system cannot create the report safely")
    main = main_checkout(cwd)
    # Nothing is created until every existing piece has been looked at.
    for name in (BASE, FOLDER):
        kind = _kind(os.path.join(main, name))
        if kind is not None and stat.S_ISLNK(kind):
            raise Refused(f"{name} is a symlink")
        if kind is not None and not stat.S_ISDIR(kind):
            raise Refused(f"{name} is not a directory")
    tracked = git(main, "ls-files", "--", FOLDER)
    if tracked.returncode or tracked.stdout:
        raise Refused("the main checkout tracks files in the reports folder, or its index could not be read")
    opened = []
    try:
        opened.append(os.open(main, os.O_RDONLY | os.O_DIRECTORY))
        opened.append(_open_folder(BASE, opened[-1]))
        folder = _open_folder(REPORTS, opened[-1])
        opened.append(folder)
        if os.fstat(folder).st_uid != os.geteuid():
            raise Refused("the reports folder is owned by another user")
        os.fchmod(folder, 0o700)
        _ensure_ignore(folder)
        name, handle = _create(folder, f"{time.strftime('%Y-%m-%d-%H%M', now or time.localtime())}-{mode}-{topic}")
        path = os.path.join(main, FOLDER, name)
        try:
            with os.fdopen(handle, "wb") as file:
                os.fchmod(file.fileno(), 0o600)
                if git(main, "check-ignore", "-q", "--", path).returncode:
                    raise Refused("Git does not ignore the report path")
                file.write(body)
        except BaseException:
            os.unlink(name, dir_fd=folder)
            raise
        return path
    finally:
        for descriptor in opened:
            os.close(descriptor)


def main(argv=None, body=None, now=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--topic", required=True)
    try:
        args = parser.parse_args(argv)
        path = store(args.cwd, args.mode, args.topic, sys.stdin.buffer.read() if body is None else body, now)
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"refused: the report could not be written ({type(error).__name__})", file=sys.stderr)
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
