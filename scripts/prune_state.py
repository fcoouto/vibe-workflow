#!/usr/bin/env python3
"""Prune provably stale lock files from the D4 transaction lock directory.

Dry run by default: nothing changes, but every lock file is briefly probed with a
non-blocking flock. With --yes, empty, unheld, old lock files are deleted. Owner markers are
never deleted: with --yes --markers, old markers whose plan directory is gone are moved into a
private run directory, from which each one can be moved back.
"""

import argparse
import errno
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Non-POSIX platforms cannot probe flock-based locks.
    fcntl = None

DEFAULT_ROOT = Path.home() / ".local/share/vcw-transactions"
HASH = "[0-9a-f]{64}"
LOCK = re.compile(rf"(?:authority-|canonical-)?{HASH}\.lock")
# Marker name -> sibling lock name that production holds while it reads or writes the marker.
MARKERS = ((re.compile(rf"({HASH})\.command\.json"), "{}.lock"),
           (re.compile(rf"authority-({HASH})\.json"), "authority-{}.lock"),
           (re.compile(rf"canonical-({HASH})\.lock\.json"), "canonical-{}.lock"))
MAX_MARKER = 1024 * 1024
EXAMPLES = 5
QUARANTINE = "quarantined"
MANIFEST = "manifest.json"

PRECAUTIONS = """\
Before any run, even a dry run (it briefly takes each lock):
  - stop every agent session, plugin operation and test run that may use this directory;
  - mount every volume that held plan directories, so their markers are not misjudged;
  - back up the directory first, for example:
      tar -C ~/.local/share -czf ~/vcw-transactions-backup.tgz vcw-transactions"""

EPILOG = PRECAUTIONS + """

--yes refuses while any lock is held (something is using the directory) or when more than
--max-delete files would change. A lock cannot be proven unheld between the check and the unlink;
deleting one another process is about to lock breaks its mutual exclusion.

A --yes run that changes something creates a private run directory (default
<root parent>/vcw-transactions-prune-<UTC timestamp>) with manifest.json, listing every deleted
lock and moved marker, and quarantined/ holding the moved markers.

Undo: for every "quarantine-marker" action with status "moved" or "moved-unverified", move
quarantined_path back to original_path:  mv -n <run dir>/quarantined/NAME <root>/NAME
Deleted lock files need no undo: production recreates empty lock files when it needs them."""

class Refusal(Exception):
    """The root, the run directory or the state is unsafe; nothing was changed."""

@dataclass
class Entry:
    kind: str      # lock, marker, temp, unknown
    name: str
    reason: str    # "eligible" or why the file is kept
    ident: tuple
    size: int
    held: bool = False
    data: dict = None  # Markers: sha256, plan_dir and parsed content.

    @property
    def eligible(self):
        return self.reason == "eligible"

@dataclass
class Report:
    root: str
    entries: list = field(default_factory=list)

    def select(self, kind, eligible=None):
        return [e for e in self.entries if e.kind == kind and eligible in (None, e.eligible)]

    @property
    def held(self):
        return [e for e in self.entries if e.held]

def _before_final_check(name):
    """Test seam: runs after the lock is taken, before the final identity check."""

def _ident(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)

def _lstat(dir_fd, name):
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)

def _exists(dir_fd, name):
    try:
        return bool(_lstat(dir_fd, name))
    except FileNotFoundError:
        return False

def _marker_sibling(name):
    for pattern, sibling in MARKERS:
        match = pattern.fullmatch(name)
        if match:
            return sibling.format(match.group(1))
    return None

def _lock_marker(name):
    """The marker that production pairs with this lock."""
    if name.startswith("canonical-"):
        return name + ".json"
    return name[:-len(".lock")] + (".json" if name.startswith("authority-") else ".command.json")

def _static_reason(info, now, min_age_days):
    """Reasons shared by locks and markers that need no content or lock probe."""
    checks = ((stat.S_ISLNK(info.st_mode), "symlink"), (not stat.S_ISREG(info.st_mode), "not a regular file"),
              (info.st_nlink != 1, "hard-linked"), (info.st_uid != os.getuid(), "foreign owner"),
              (now - info.st_mtime < min_age_days * 86400, "young"))
    return next((reason for failed, reason in checks if failed), None)

def _open_locked(dir_fd, name, expected=None):
    """Descriptor holding a non-blocking exclusive flock, or None when held; ValueError if not as expected."""
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
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise

def _classify_lock(dir_fd, name, now, min_age_days, probe=True):
    """Probe every regular lock, whatever its age, so a running session is always noticed."""
    info = _lstat(dir_fd, name)
    reason = _static_reason(info, now, min_age_days)
    if reason is None and info.st_size != 0:
        reason = "non-empty"
    held = False
    if probe and stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        problem, fd = _take(dir_fd, name, _ident(info))
        if fd is not None:
            os.close(fd)
        held = problem == "held"
        reason = "held" if held else reason or problem
    return Entry("lock", name, reason or "eligible", _ident(info), info.st_size, held)

def _read_marker(dir_fd, name, info):
    """Return (raw, object, reason kept) without following links; only JSON objects are parsed."""
    if info.st_size > MAX_MARKER:
        return b"", None, "unparseable"
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        if _ident(os.fstat(fd)) != _ident(info):
            return b"", None, "changed"
        raw = os.read(fd, MAX_MARKER + 1)
    finally:
        os.close(fd)
    try:
        value = json.loads(raw)
    except ValueError:  # Includes invalid UTF-8.
        value = None
    return (raw, value, None) if isinstance(value, dict) else (raw, None, "unparseable")

def _plan_dir_reason(marker):
    """None when the marker's canonical plan_dir provably no longer exists; otherwise why it is kept."""
    if "plan_dir" not in marker:
        return "no path"
    plan_dir = marker["plan_dir"]
    if (not isinstance(plan_dir, str) or "\0" in plan_dir or not os.path.isabs(plan_dir)
            or os.path.normpath(plan_dir) != plan_dir):
        return "invalid path"
    try:
        os.lstat(plan_dir)  # Any entry, even a dangling link, counts as present.
    except (FileNotFoundError, NotADirectoryError):
        return None
    except PermissionError:
        return "plan_dir not accessible"
    return "plan_dir exists"

def _take(dir_fd, name, expected=None, prefix=""):
    """Return (reason kept, None) or (None, descriptor holding the lock)."""
    try:
        fd = _open_locked(dir_fd, name, expected)
    except FileNotFoundError:
        if not prefix:
            raise
        return prefix + "missing", None
    except ValueError as error:
        return prefix + str(error), None
    return (prefix + "held", None) if fd is None else (None, fd)

def _classify_marker(dir_fd, name, now, min_age_days, probe=True):
    info = _lstat(dir_fd, name)
    reason, data = _static_reason(info, now, min_age_days), None
    if reason is None:
        raw, marker, reason = _read_marker(dir_fd, name, info)
    if reason is None:
        data = dict(sha256=hashlib.sha256(raw).hexdigest(), plan_dir=marker.get("plan_dir"), content=marker)
        reason = _plan_dir_reason(marker)
    if reason is None and probe:
        reason, fd = _take(dir_fd, _marker_sibling(name), prefix="sibling lock ")
        if fd is not None:
            os.close(fd)
    return Entry("marker", name, reason or "eligible", _ident(info), info.st_size, data=data)

def classify(dir_fd, name, now, min_age_days):
    if LOCK.fullmatch(name):
        return _classify_lock(dir_fd, name, now, min_age_days)
    if _marker_sibling(name):
        return _classify_marker(dir_fd, name, now, min_age_days)
    info = _lstat(dir_fd, name)
    reason = "symlink" if stat.S_ISLNK(info.st_mode) else "report only"
    return Entry("temp" if name.startswith(".d4-") else "unknown", name, reason, _ident(info), info.st_size)

def _open_private_dir(path, label):
    """Open a directory without following a final symlink; refuse unless it is ours and 0700."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise Refusal(f"{label} does not exist: {path}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Refusal(f"{label} is {'a symlink' if stat.S_ISLNK(info.st_mode) else 'not a directory'}: {path}")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise Refusal(f"{label} changed while opening: {path}") from None
        raise
    opened = os.fstat(fd)
    checks = (((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino), "changed while opening"),
              (opened.st_uid != os.getuid(), "is not owned by the current user"),
              (stat.S_IMODE(opened.st_mode) != 0o700, f"mode is {stat.S_IMODE(opened.st_mode):04o}, not 0700"))
    problem = next((problem for failed, problem in checks if failed), None)
    if problem:
        os.close(fd)
        raise Refusal(f"{label} {problem}: {path}")
    return fd

def open_root(root):
    if fcntl is None:
        raise Refusal("This platform has no fcntl.flock; lock files cannot be probed safely.")
    return _open_private_dir(root, "Root")

def default_run_dir(root):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path, number = root.parent / f"vcw-transactions-prune-{stamp}", 1
    while os.path.lexists(path):
        path, number = root.parent / f"vcw-transactions-prune-{stamp}-{number}", number + 1
    return path

def prepare_run_dir(path, root, root_dev):
    """Create or open an empty private run directory outside the root; return (run fd, quarantine fd)."""
    real, real_root = os.path.realpath(path), os.path.realpath(root)
    if real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep):
        raise Refusal(f"Run directory is inside the root: {path}")
    if not os.path.lexists(path):
        os.mkdir(path, 0o700)
    run_fd = _open_private_dir(path, "Run directory")
    try:
        if os.listdir(run_fd):
            raise Refusal(f"Run directory is not empty: {path}")
        if os.fstat(run_fd).st_dev != root_dev:
            raise Refusal(f"Run directory is on another filesystem than the root: {path}")
        os.mkdir(QUARANTINE, 0o700, dir_fd=run_fd)
        return run_fd, os.open(QUARANTINE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=run_fd)
    except BaseException:
        os.close(run_fd)
        raise

def write_manifest(run_fd, body):
    """Atomically replace manifest.json (mode 0600) and publish it durably."""
    raw = json.dumps(body, indent=2, sort_keys=True).encode()
    temporary = f".manifest-{os.getpid()}-{time.monotonic_ns()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=run_fd)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(fd, 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(fd)
    os.rename(temporary, MANIFEST, src_dir_fd=run_fd, dst_dir_fd=run_fd)
    os.fsync(run_fd)

def scan(dir_fd, root, min_age_days, now=None):
    now = time.time() if now is None else now
    report, names = Report(str(root)), set(os.listdir(dir_fd))
    for name in sorted(names):
        try:
            report.entries.append(classify(dir_fd, name, now, min_age_days))
        except FileNotFoundError:
            continue  # Removed by its owner since the listing; nothing to report.
    for entry in (e for e in report.select("lock", eligible=True) if _lock_marker(e.name) in names):
        entry.reason = "marker present"  # Keep it so the marker stays handleable under its lock.
    return report

def _move_marker(dir_fd, entry, now, min_age_days, quarantine_fd, quarantine_path, root):
    """Return (skip reason, None) or (None, manifest action) after moving the marker under its lock."""
    reason, lock_fd = _take(dir_fd, _marker_sibling(entry.name), prefix="sibling lock ")
    if lock_fd is None:
        return reason, None
    try:
        current = _classify_marker(dir_fd, entry.name, now, min_age_days, probe=False)
        if not current.eligible or current.ident != entry.ident or current.data["sha256"] != entry.data["sha256"]:
            return current.reason if not current.eligible else "changed", None
        _before_final_check(entry.name)
        if _ident(_lstat(dir_fd, entry.name)) != entry.ident:
            return "changed", None
        if _exists(quarantine_fd, entry.name):
            return "quarantine name taken", None
        os.rename(entry.name, entry.name, src_dir_fd=dir_fd, dst_dir_fd=quarantine_fd)
        moved = _lstat(quarantine_fd, entry.name)
        verified = (moved.st_dev, moved.st_ino) == entry.ident[:2]
        return None, dict(action="quarantine-marker", name=entry.name, status="moved" if verified else "moved-unverified",
                          original_path=os.path.join(root, entry.name),
                          quarantined_path=os.path.join(quarantine_path, entry.name),
                          size=current.size, mtime_ns=current.ident[3], **current.data)
    finally:
        os.close(lock_fd)

def _delete_lock(dir_fd, entry, now, min_age_days):
    current = _classify_lock(dir_fd, entry.name, now, min_age_days, probe=False)
    if not current.eligible or current.ident != entry.ident:
        return current.reason if not current.eligible else "changed"
    reason, fd = _take(dir_fd, entry.name, entry.ident)
    if fd is None:
        return reason
    try:
        _before_final_check(entry.name)
        if _exists(dir_fd, _lock_marker(entry.name)):
            return "marker present"
        if _ident(_lstat(dir_fd, entry.name)) != entry.ident:
            return "changed"
        os.unlink(entry.name, dir_fd=dir_fd)
        return None
    finally:
        os.close(fd)

def apply(dir_fd, report, min_age_days, root, quarantine=None, now=None, done=None, skipped=None):
    """Move eligible markers (only with a quarantine), then delete eligible locks, re-verifying each.

    Unexpected errors (permission errors included) propagate; done/skipped keep the progress."""
    now = time.time() if now is None else now
    done, skipped = ([] if done is None else done), ([] if skipped is None else skipped)
    work = [(e, "marker") for e in report.select("marker", eligible=True)] if quarantine else []
    for entry, kind in work + [(e, "lock") for e in report.select("lock", eligible=True)]:
        try:
            if kind == "marker":
                reason, action = _move_marker(dir_fd, entry, now, min_age_days, *quarantine, root)
            else:
                reason = _delete_lock(dir_fd, entry, now, min_age_days)
                action = dict(action="unlink-lock", name=entry.name, status="deleted", size=entry.size,
                              path=os.path.join(root, entry.name), mtime_ns=entry.ident[3])
        except FileNotFoundError:
            reason = "vanished"
        if reason is None:
            done.append(action)
        else:
            skipped.append((entry, reason))
    if done:
        os.fsync(dir_fd)
    return done, skipped

def render(report, markers, max_delete):
    lines = []

    def group(label, entries):
        lines.append(f"  {label}: {len(entries)}")
        lines.extend(f"      {os.path.join(report.root, e.name)}" for e in entries[:EXAMPLES])

    for kind, title, kept_label in (("lock", "Locks", "ineligible"), ("marker", "Markers", "kept")):
        eligible, kept = report.select(kind, True), report.select(kind, False)
        lines.append(f"{title}: {len(eligible)} eligible, {len(kept)} {kept_label}")
        group(f"eligible ({sum(e.size for e in eligible)} bytes)", eligible)
        for reason in sorted({e.reason for e in kept}):
            group(f"{kept_label} ({reason})", [e for e in kept if e.reason == reason])
    lines.append("Report only:")
    group("D4 temporary files (.d4-*)", report.select("temp"))
    group("unknown entries", report.select("unknown"))
    locks, found = len(report.select("lock", True)), len(report.select("marker", True))
    moved, held = (found if markers else 0), report.held
    lines.append(f"Plan for --yes: delete {locks} lock files; move {moved} markers to quarantine"
                 + ("" if markers else f" ({found} eligible; markers move only with --markers)"))
    lines.append(f"Changes: {locks + moved} of --max-delete {max_delete}: "
                 + ("within the cap" if locks + moved <= max_delete else "over the cap; --yes would refuse"))
    busy = "; something is using this directory and --yes would refuse" if held else ""
    lines.append(f"Held locks: {len(held)}{busy}")
    lines.extend(f"      {os.path.join(report.root, e.name)}" for e in held[:EXAMPLES])
    return "\n".join(lines)


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__, epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"lock directory (default: {DEFAULT_ROOT})")
    parser.add_argument("--min-age-days", type=int, default=7, help="minimum age by mtime for any change (default: 7)")
    parser.add_argument("--yes", action="store_true",
                        help="delete the eligible lock files after re-verifying each; without it nothing changes")
    parser.add_argument("--markers", action="store_true",
                        help="with --yes, also move eligible owner markers into the run directory's quarantine "
                             "(never deleted); without it markers are only reported")
    parser.add_argument("--max-delete", type=int, default=1000,
                        help="refuse --yes before changing anything if more files would change (default: 1000)")
    parser.add_argument("--run-dir", type=Path,
                        help="new or empty private (0700) directory outside the root, on the same filesystem, "
                             "for manifest.json and quarantined markers "
                             "(default: <root parent>/vcw-transactions-prune-<UTC timestamp>)")
    args = parser.parse_args(argv)
    if args.min_age_days < 1 or args.max_delete < 0:
        parser.error("--min-age-days must be at least 1 and --max-delete at least 0")
    return args

def main(argv=None):
    args = parse(argv)
    root = Path(os.path.abspath(os.path.expanduser(args.root)))
    print(f"Root: {root}\n{PRECAUTIONS}\n")
    dir_fd = run_fd = quarantine_fd = None
    try:
        dir_fd = open_root(root)
        report = scan(dir_fd, root, args.min_age_days)
        print(render(report, args.markers, args.max_delete))
        if not args.yes:
            print("\nDry run: nothing was changed. --yes deletes the eligible lock files; --yes --markers also "
                  "moves the eligible markers to quarantine. See --help for the run directory and undo.")
            return 0
        markers = report.select("marker", eligible=True) if args.markers else []
        total = len(report.select("lock", eligible=True)) + len(markers)
        if report.held:
            raise Refusal("A lock is held: something is using this directory. Stop every agent session and test "
                          "run, then retry.")
        if total > args.max_delete:
            raise Refusal(f"{total} files would change, more than --max-delete {args.max_delete}. "
                          "Pass a larger --max-delete explicitly after reviewing the dry run.")
        if total == 0:
            print("\nNothing to change.")
            return 0
        run_path = Path(os.path.abspath(args.run_dir)) if args.run_dir else default_run_dir(root)
        run_fd, quarantine_fd = prepare_run_dir(run_path, root, os.fstat(dir_fd).st_dev)
        return _execute(args, root, dir_fd, report, run_path, run_fd, quarantine_fd, markers)
    except Refusal as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 2
    finally:
        for fd in filter(lambda fd: fd is not None, (quarantine_fd, run_fd, dir_fd)):
            os.close(fd)

def _execute(args, root, dir_fd, report, run_path, run_fd, quarantine_fd, markers):
    """Record the plan, act, then record exactly what happened, even when an error stops the run."""
    quarantine_path = str(run_path / QUARANTINE)
    body = dict(schema_version=1, tool="scripts/prune_state.py", root=str(root), run_dir=str(run_path),
                quarantine_dir=quarantine_path, started=datetime.now(timezone.utc).isoformat(),
                min_age_days=args.min_age_days, state="planned", actions=[], skipped=[],
                undo="For each quarantine-marker action with status moved or moved-unverified, "
                     "move quarantined_path back to original_path (mv -n). Deleted locks need no undo.",
                planned=[dict(action="quarantine-marker", name=e.name, size=e.size, **e.data) for e in markers]
                + [dict(action="unlink-lock", name=e.name) for e in report.select("lock", eligible=True)])
    write_manifest(run_fd, body)
    done, skipped = [], []
    try:
        apply(dir_fd, report, args.min_age_days, str(root), (quarantine_fd, quarantine_path) if args.markers else None,
              done=done, skipped=skipped)
        body["state"] = "complete"
    except BaseException:
        body["state"] = "stopped"
        raise
    finally:
        body["actions"] = done
        body["skipped"] = [dict(name=entry.name, reason=reason) for entry, reason in skipped]
        write_manifest(run_fd, body)
        deleted = sum(a["action"] == "unlink-lock" for a in done)
        reasons = "".join(f"\n  {reason}: {count}" for reason, count in sorted(Counter(r for _, r in skipped).items()))
        print(f"\nDeleted locks: {deleted}\nQuarantined markers: {len(done) - deleted}\n"
              f"Skipped at re-verification: {len(skipped)}{reasons}\nRun directory: {run_path}\n"
              f"Manifest (every action, state {body['state']}): {run_path / MANIFEST}")
        if len(done) > deleted:
            print("Undo marker moves: see --help (mv -n <run dir>/quarantined/NAME <root>/NAME).")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as error:
        print(f"Prune stopped: {error}", file=sys.stderr)
        sys.exit(1)
