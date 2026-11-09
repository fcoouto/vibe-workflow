#!/usr/bin/env python3
"""Prune provably stale files from the D4 transaction lock directory.

Dry run by default. Only empty, unheld, old lock files and old owner markers whose plan
directory is gone are eligible; everything else is reported and kept. With --yes, each
listed file is re-verified (same inode, size and mtime; criteria still hold) immediately
before it is unlinked.
"""

import argparse
import errno
import json
import os
import re
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:  # Non-POSIX platforms cannot probe flock-based locks.
    fcntl = None

DEFAULT_ROOT = Path.home() / ".local/share/vcw-transactions"
HASH = "[0-9a-f]{64}"
LOCK = re.compile(rf"(?:authority-|canonical-)?{HASH}\.lock")
# Marker name -> sibling lock name that production holds while it reads or writes the marker.
MARKERS = (
    (re.compile(rf"({HASH})\.command\.json"), "{}.lock"),
    (re.compile(rf"authority-({HASH})\.json"), "authority-{}.lock"),
    (re.compile(rf"canonical-({HASH})\.lock\.json"), "canonical-{}.lock"),
)
MAX_MARKER = 1024 * 1024
EXAMPLES = 5
SECONDS_PER_DAY = 86400

WARNING = """\
WARNING: before running with --yes, stop every agent session, plugin operation and test
run that may use this directory. A lock file cannot be proven unheld across the window
between the check and the unlink; deleting one that another process is about to lock
breaks mutual exclusion for that resource."""


class Refusal(Exception):
    """The root or the arguments are unsafe; nothing was changed."""


@dataclass
class Entry:
    kind: str      # lock, marker, temp, unknown
    name: str
    reason: str    # "eligible" or why the file is kept
    ident: tuple
    size: int

    @property
    def eligible(self):
        return self.reason == "eligible"


@dataclass
class Report:
    root: str
    entries: list = field(default_factory=list)

    def select(self, kind, eligible=None):
        return [e for e in self.entries if e.kind == kind and (eligible is None or e.eligible == eligible)]


def _ident(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _lstat(dir_fd, name):
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)


def _marker_sibling(name):
    for pattern, sibling in MARKERS:
        match = pattern.fullmatch(name)
        if match:
            return sibling.format(match.group(1))
    return None


def _static_reason(info, now, min_age_days):
    """Reasons shared by locks and markers that need no content or lock probe."""
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if not stat.S_ISREG(info.st_mode):
        return "not a regular file"
    if info.st_nlink != 1:
        return "hard-linked"
    if info.st_uid != os.getuid():
        return "foreign owner"
    if now - info.st_mtime < min_age_days * SECONDS_PER_DAY:
        return "young"
    return None


def _open_locked(dir_fd, name, expected=None):
    """Return a descriptor holding a non-blocking exclusive flock, or None when held.

    Raises FileNotFoundError when the name is gone and ValueError when the opened file is not
    the regular, singly linked file that was expected.
    """
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError("symlink") from None
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("not a regular file")
        if expected is not None and _ident(info) != expected:
            raise ValueError("changed")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return fd
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _classify_lock(dir_fd, name, now, min_age_days, probe=True):
    info = _lstat(dir_fd, name)
    reason = _static_reason(info, now, min_age_days)
    if reason is None and info.st_size != 0:
        reason = "non-empty"
    if reason is None and probe:
        try:
            fd = _open_locked(dir_fd, name, _ident(info))
        except ValueError as error:
            reason = str(error)
        except FileNotFoundError:
            reason = "vanished"
        else:
            if fd is None:
                reason = "held"
            else:
                os.close(fd)
    return Entry("lock", name, reason or "eligible", _ident(info), info.st_size)


def _read_marker(dir_fd, name, info):
    """Parse a marker without following links; None when it is not a readable JSON object."""
    if info.st_size > MAX_MARKER:
        return None
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        if _ident(os.fstat(fd)) != _ident(info):
            raise ValueError("changed")
        raw = os.read(fd, MAX_MARKER + 1)
    finally:
        os.close(fd)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _plan_dir_reason(marker):
    """None when the marker's plan_dir provably no longer exists; otherwise why it is kept."""
    if "plan_dir" not in marker:
        return "no path"
    plan_dir = marker["plan_dir"]
    if not isinstance(plan_dir, str) or not os.path.isabs(plan_dir) or "\0" in plan_dir:
        return "invalid path"
    try:
        os.lstat(plan_dir)  # Any entry, even a dangling link, counts as present.
    except (FileNotFoundError, NotADirectoryError):
        return None
    return "plan_dir exists"


def _classify_marker(dir_fd, name, now, min_age_days, probe=True):
    info = _lstat(dir_fd, name)
    reason = _static_reason(info, now, min_age_days)
    if reason is None:
        try:
            marker = _read_marker(dir_fd, name, info)
        except ValueError as error:
            reason = str(error)
        else:
            reason = "unparseable" if marker is None else _plan_dir_reason(marker)
    if reason is None and probe:
        reason = _sibling_probe(dir_fd, _marker_sibling(name))
    return Entry("marker", name, reason or "eligible", _ident(info), info.st_size)


def _sibling_probe(dir_fd, sibling):
    """None when the sibling lock is absent or could be taken; otherwise why the marker is kept."""
    try:
        fd = _open_locked(dir_fd, sibling)
    except FileNotFoundError:
        return None
    except ValueError as error:
        return f"sibling lock {error}"
    if fd is None:
        return "sibling lock held"
    os.close(fd)
    return None


def classify(dir_fd, name, now, min_age_days):
    if LOCK.fullmatch(name):
        return _classify_lock(dir_fd, name, now, min_age_days)
    if _marker_sibling(name):
        return _classify_marker(dir_fd, name, now, min_age_days)
    info = _lstat(dir_fd, name)
    kind = "temp" if name.startswith(".d4-") else "unknown"
    reason = "symlink" if stat.S_ISLNK(info.st_mode) else "report only"
    return Entry(kind, name, reason, _ident(info), info.st_size)


def open_root(root):
    """Open the root without following a final symlink and refuse unless it is private."""
    if fcntl is None:
        raise Refusal("This platform has no fcntl.flock; lock files cannot be probed safely.")
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        raise Refusal(f"Root does not exist: {root}") from None
    if stat.S_ISLNK(info.st_mode):
        raise Refusal(f"Root is a symlink; refusing: {root}")
    if not stat.S_ISDIR(info.st_mode):
        raise Refusal(f"Root is not a directory: {root}")
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise Refusal(f"Root changed while opening; refusing: {root}") from None
        raise
    opened = os.fstat(fd)
    problem = None
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        problem = "Root changed while opening"
    elif opened.st_uid != os.getuid():
        problem = "Root is not owned by the current user"
    elif stat.S_IMODE(opened.st_mode) != 0o700:
        problem = f"Root mode is {stat.S_IMODE(opened.st_mode):04o}, not 0700"
    if problem:
        os.close(fd)
        raise Refusal(f"{problem}; refusing: {root}")
    return fd


def scan(dir_fd, root, min_age_days, now=None):
    now = time.time() if now is None else now
    report = Report(str(root))
    for name in sorted(os.listdir(dir_fd)):
        try:
            report.entries.append(classify(dir_fd, name, now, min_age_days))
        except FileNotFoundError:
            continue  # Removed by its owner since the listing; nothing to report.
    return report


def _delete_marker(dir_fd, entry, now, min_age_days):
    sibling = _marker_sibling(entry.name)
    lock_fd = None
    try:
        try:
            lock_fd = _open_locked(dir_fd, sibling)
        except FileNotFoundError:
            pass
        except ValueError as error:
            return f"sibling lock {error}"
        else:
            if lock_fd is None:
                return "sibling lock held"
        current = _classify_marker(dir_fd, entry.name, now, min_age_days, probe=False)
        if not current.eligible:
            return current.reason
        if current.ident != entry.ident or _ident(_lstat(dir_fd, entry.name)) != entry.ident:
            return "changed"
        os.unlink(entry.name, dir_fd=dir_fd)
        return None
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def _delete_lock(dir_fd, entry, now, min_age_days):
    current = _classify_lock(dir_fd, entry.name, now, min_age_days, probe=False)
    if not current.eligible:
        return current.reason
    if current.ident != entry.ident:
        return "changed"
    try:
        fd = _open_locked(dir_fd, entry.name, entry.ident)
    except ValueError as error:
        return str(error)
    if fd is None:
        return "held"
    try:
        if _ident(_lstat(dir_fd, entry.name)) != entry.ident:
            return "changed"
        os.unlink(entry.name, dir_fd=dir_fd)
        return None
    finally:
        os.close(fd)


def apply(dir_fd, report, min_age_days, now=None, deleted=None, skipped=None):
    """Delete exactly the report's eligible files that still qualify; markers before locks.

    Unexpected errors (including permission errors) propagate and stop the run; the lists
    passed in keep what happened before the error.
    """
    now = time.time() if now is None else now
    deleted = [] if deleted is None else deleted
    skipped = [] if skipped is None else skipped
    for kind, delete in (("marker", _delete_marker), ("lock", _delete_lock)):
        for entry in report.select(kind, eligible=True):
            try:
                reason = delete(dir_fd, entry, now, min_age_days)
            except FileNotFoundError:
                reason = "vanished"
            if reason is None:
                deleted.append(entry)
            else:
                skipped.append((entry, reason))
    if deleted:
        os.fsync(dir_fd)
    return deleted, skipped


def _examples(root, entries):
    return [f"      {os.path.join(root, entry.name)}" for entry in entries[:EXAMPLES]]


def render(report):
    lines = []
    labels = (("lock", "Locks", "ineligible"), ("marker", "Markers", "kept"))
    for kind, title, kept_label in labels:
        eligible = report.select(kind, eligible=True)
        kept = report.select(kind, eligible=False)
        lines.append(f"{title}: {len(eligible)} eligible, {len(kept)} {kept_label}")
        lines.append(f"  eligible: {len(eligible)} ({sum(e.size for e in eligible)} bytes)")
        lines += _examples(report.root, eligible)
        for reason in sorted({e.reason for e in kept}):
            group = [e for e in kept if e.reason == reason]
            lines.append(f"  {kept_label} ({reason}): {len(group)}")
            lines += _examples(report.root, group)
    for kind, title in (("temp", "D4 temporary files (.d4-*)"), ("unknown", "Unknown entries")):
        group = report.select(kind)
        lines.append(f"{title}: {len(group)} (report only)")
        lines += _examples(report.root, group)
    total = sum(e.size for e in report.entries if e.eligible)
    count = sum(e.eligible for e in report.entries)
    lines.append(f"Total eligible: {count} files, {total} bytes")
    return "\n".join(lines)


def _days(value):
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number of days") from None
    if days < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return days


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"lock directory (default: {DEFAULT_ROOT})")
    parser.add_argument("--min-age-days", type=_days, default=7, help="minimum age by mtime (default: 7)")
    parser.add_argument("--yes", action="store_true", help="delete the eligible files after re-verifying each")
    args = parser.parse_args(argv)
    root = Path(os.path.abspath(os.path.expanduser(args.root)))
    print(f"Root: {root}")
    try:
        dir_fd = open_root(root)
    except Refusal as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 2
    deleted, skipped = [], []
    try:
        report = scan(dir_fd, root, args.min_age_days)
        print(render(report))
        if not args.yes:
            print("\nDry run: nothing was deleted.\n\n" + WARNING + "\n\nRe-run with --yes to delete the eligible files.")
            return 0
        apply(dir_fd, report, args.min_age_days, deleted=deleted, skipped=skipped)
    finally:
        if args.yes:
            print(f"\nDeleted: {len(deleted)} ({sum(e.size for e in deleted)} bytes)")
            for entry in deleted:
                print(f"  {os.path.join(root, entry.name)}")
            print(f"Skipped at deletion: {len(skipped)}")
            for entry, reason in skipped:
                print(f"  {os.path.join(root, entry.name)}: {reason}")
        os.close(dir_fd)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as error:
        print(f"Prune stopped: {error}", file=sys.stderr)
        sys.exit(1)
