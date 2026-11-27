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
        raise Refused("invalid arguments; give --cwd <dir>, --mode full|ship, --topic <slug> and --from <file>")


def git(directory, *args):
    """Run Git for one checkout, without the Git variables this process inherited. Output stays bytes:
    a path may hold any byte but NUL, and text decoding would turn a carriage return into a newline."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", directory, *args], env={**env, "LC_ALL": "C"},
                              capture_output=True, timeout=10)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise Refused("Git could not run") from error


def main_checkout(cwd):
    """The main checkout is the first worktree record; a bare or unresolved one has no place for reports."""
    listed = git(cwd, "worktree", "list", "--porcelain", "-z")
    if listed.returncode:
        if b"not a git repository" in listed.stderr:
            raise Refused("not a Git repository")
        raise Refused("the main checkout could not be resolved")
    path, *attributes = listed.stdout.split(b"\0\0")[0].split(b"\0")
    if not path.startswith(b"worktree ") or b"bare" in attributes:
        raise Refused("the repository has no main checkout")
    try:
        main = os.path.realpath(os.fsdecode(path[len(b"worktree "):]), strict=True)
        top = git(main, "rev-parse", "--show-toplevel")
        # Git ends the line with one newline; nothing else is stripped from the name.
        if top.returncode or not os.path.samefile(os.fsdecode(top.stdout.removesuffix(b"\n")), main):
            raise Refused("the main checkout could not be resolved")
    except OSError as error:
        raise Refused("the main checkout could not be resolved") from error
    return main


def _read_source(source):
    """The report text comes from a regular file, never through a link and never from standard input."""
    try:
        handle = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise Refused("--from is a symlink or cannot be opened") from error
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise Refused("--from is not a regular file")
        with os.fdopen(handle, "rb", closefd=False) as file:
            return file.read()
    finally:
        os.close(handle)


def _kind(path):
    try:
        return os.lstat(path).st_mode
    except FileNotFoundError:
        return None


def _trusted(descriptor, name):
    """Someone who can write a folder can rename what is inside it, so both must belong to this user alone.
    Their modes are never changed to make this pass."""
    status = os.fstat(descriptor)
    if status.st_uid != os.geteuid():
        raise Refused(f"{name} is owned by another user")
    if status.st_mode & 0o022:
        raise Refused(f"{name} is writable by its group or by everyone")


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


def _same(path, descriptor):
    """Whether the name Git was asked about still leads to the object this process holds open."""
    try:
        named, held = os.lstat(path), os.fstat(descriptor)
    except OSError:
        return False
    return (named.st_dev, named.st_ino) == (held.st_dev, held.st_ino)


def _discard(name, folder, created):
    """Remove the report entry only while it is still the file this run created; say whether it was.
    An open folder identifies the folder, not whatever now has a name inside it."""
    try:
        entry = os.lstat(name, dir_fd=folder)
    except FileNotFoundError:
        return True
    if (entry.st_dev, entry.st_ino) != created:
        return False
    os.unlink(name, dir_fd=folder)
    return True


def store(cwd, mode, topic, source, now=None):
    if mode not in ("full", "ship"):
        raise Refused("mode must be full or ship")
    if not TOPIC.fullmatch(topic):
        raise Refused("topic must be lowercase letters, digits and hyphens, at most 61 characters")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid") or os.open not in os.supports_dir_fd:
        raise Refused("this system cannot create the report safely")
    body = _read_source(source)
    if not body:
        raise Refused("the report is empty")
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
        _trusted(opened[-1], "the main checkout")
        opened.append(_open_folder(BASE, opened[-1]))
        _trusted(opened[-1], BASE)
        folder = _open_folder(REPORTS, opened[-1])
        opened.append(folder)
        if os.fstat(folder).st_uid != os.geteuid():
            raise Refused("the reports folder is owned by another user")
        os.fchmod(folder, 0o700)
        _ensure_ignore(folder)
        name, handle = _create(folder, f"{time.strftime('%Y-%m-%d-%H%M', now or time.localtime())}-{mode}-{topic}")
        path = os.path.join(main, FOLDER, name)
        created = os.fstat(handle)
        created = created.st_dev, created.st_ino
        try:
            with os.fdopen(handle, "wb") as file:
                os.fchmod(file.fileno(), 0o600)
                if git(main, "check-ignore", "-q", "--", path).returncode:
                    raise Refused("Git does not ignore the report path")
                # Git judged a name. Write only if that name is still the folder and the file held open here.
                if not _same(os.path.join(main, FOLDER), folder) or not _same(path, file.fileno()):
                    raise Refused("the reports folder or the report changed while it was being checked")
                file.write(body)
        except (Refused, OSError) as error:
            reason = str(error) if isinstance(error, Refused) else f"the report could not be written ({type(error).__name__})"
            if not _discard(name, folder, created):
                reason += "; another file now has the report's name and was left untouched"
            raise Refused(reason) from error
        except BaseException:
            _discard(name, folder, created)
            raise
    finally:
        for descriptor in opened:
            os.close(descriptor)
    try:
        os.unlink(source)
    except OSError:
        print("note: the report is saved, but the temporary file could not be deleted", file=sys.stderr)
    return path


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--from", dest="source", required=True)
    try:
        try:
            args = parser.parse_args(argv)
            path = store(args.cwd, args.mode, args.topic, args.source)
        except OSError as error:
            raise Refused(f"the report could not be written ({type(error).__name__})") from error
    except Refused as refusal:
        print(f"refused: {refusal}; the temporary file was not deleted", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(os.fsencode(path) + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
