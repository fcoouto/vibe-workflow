#!/usr/bin/env python3
"""Save a security report in the main checkout's private reports folder, or refuse and write no report."""
import argparse
import os
import re
import stat
import sys
import time

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if not os.path.isfile(os.path.join(ROOT, "runtime", "private_folder.py")):
    print("refused: the plugin's files are incomplete", file=sys.stderr)
    sys.exit(2)
sys.path.insert(0, ROOT)
from runtime.private_folder import BASE, Refused, git, main_checkout, open_private_folder

REPORTS = "security-reports"
FOLDER = f"{BASE}/{REPORTS}"
TOPIC = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("invalid arguments; give --cwd <dir>, --mode full|ship, --topic <slug> and --from <file>")


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
    if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid") or os.open not in os.supports_dir_fd
            or os.listdir not in os.supports_fd):
        raise Refused("this system cannot create the report safely")
    body = _read_source(source)
    if not body:
        raise Refused("the report is empty")
    main = main_checkout(cwd)
    folder, opened = open_private_folder(main, REPORTS, "reports folder")
    try:
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
