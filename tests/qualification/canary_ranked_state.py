"""The ranked canary's durable whole-run record (F7 correction F1/F2): one owner-only JSON file per routing ledger,
outside every repository and the isolated run directory, holding the approved preflight digest and everything a new
process needs to resume the same run: the approved pins, the original seeded baseline and earliest demanded reset,
the cumulative/last/seen window accounting, unsettled reservations, the stop state and the admission count.

It is created only by an explicit approved start (`create`), never on resume; `load` refuses a missing, corrupt,
mismatched or concurrently held record. A record is never replaced or deleted by the harness: a new approval for the
same ledger is refused while it exists, so no approval can silently erase a whole-run allowance. Completed and
aborted records stay retained; no archive or reset procedure exists, and choosing another record directory is not a
supported reset. This is a harness rule, not OS enforcement: the local owner can still move the file by hand. Writes are atomic
(temporary file, fsync, `os.replace`) and the caller saves before every send; a failed save fails closed.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path

from tests.qualification import replay_export
from tests.qualification.safety import Refused

VERSION = 1
FIELDS = {"version", "digest", "project", "ledger", "pins", "coverage", "acknowledged", "baseline",
          "earliest_reset", "cumulative", "last", "seen", "holds", "stopped", "completed"}


def default_dir():
    return Path.home() / ".local/state/vibe-workflow-qualification/ranked-canary"


def path_for(directory, ledger):
    """The one record of a routing ledger: keyed by the ledger's resolved path."""
    key = hashlib.sha256(str(Path(ledger).resolve()).encode()).hexdigest()[:16]
    return Path(directory) / ("run-%s.json" % key)


def digest(record):
    """The deterministic digest the approval names: pins, coverage, acknowledgement, baseline and earliest reset."""
    body = {key: record[key] for key in ("project", "ledger", "pins", "coverage", "acknowledged", "baseline",
                                         "earliest_reset")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(path, value):
    temporary = path.with_name("." + path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read(path):
    try:
        with os.fdopen(replay_export._private_file(path, os.O_RDONLY), "r") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        raise Refused("the ranked canary run record is missing") from None
    except (OSError, ValueError):
        raise Refused("the ranked canary run record is unreadable") from None
    if not isinstance(value, dict) or set(value) != FIELDS or value.get("version") != VERSION:
        raise Refused("the ranked canary run record is corrupt")
    return value


@contextlib.contextmanager
def held(path):
    """An exclusive, non-blocking lock beside the record for the whole run: a concurrent run is refused."""
    replay_export.check_dir(path.parent)
    descriptor = os.open(path.with_name(path.name + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("another ranked canary run holds this ledger's run record") from None
        yield
    finally:
        os.close(descriptor)


def create(path, record):
    """The new run's record from an approved preflight record; refused when one already exists."""
    replay_export.check_dir(path.parent)
    if path.exists() or path.is_symlink():
        raise Refused("a ranked canary run record already exists for this ledger; it is never replaced, moved or "
                      "deleted to start or resume a run. Resume it with its approved digest; another approval on this "
                      "ledger stays refused pending an explicit lifecycle decision")
    value = dict({key: record[key] for key in ("project", "ledger", "pins", "coverage", "acknowledged", "baseline",
                                               "earliest_reset")}, version=VERSION, digest=digest(record),
                 cumulative={key: 0 for key in record["baseline"]},
                 last={key: item["used"] for key, item in record["baseline"].items()},
                 seen={key: item.get("observed_at") or 0 for key, item in record["baseline"].items()},
                 holds={}, stopped=None, completed=0)
    _write(path, value)
    return value


def load(path, approved, project, ledger):
    """The existing record, refused unless it names this approved digest, project and ledger."""
    value = _read(path)
    if value["digest"] != approved or value["digest"] != digest(value):
        raise Refused("the approved digest does not match this ledger's ranked canary run record")
    if value["project"] != str(project) or value["ledger"] != str(ledger):
        raise Refused("the ranked canary run record belongs to another project or ledger")
    return value


def save(path, value):
    try:
        _write(path, value)
    except OSError:
        from tests.qualification import canary
        raise canary.Abort("state_unsaved") from None
