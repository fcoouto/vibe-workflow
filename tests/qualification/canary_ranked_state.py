"""One retained ranked campaign and lifetime lock per verified local account, outside repositories.

The default ledger and settings are validated metadata, never a namespace. Resume never creates; explicit first
start requires an approved digest and the operator's separate history-decision reference in the run report.
Only this account's fixed directory is inspected: legacy, orphaned or ambiguous evidence refuses unchanged, with
no adoption, migration or deletion. Unknown historical directories require a manual decision, not discovery here.
The original pins, baseline/reset, cumulative/last/seen, holds, stop and sample survive process restarts.
Writes are atomic and saved before sends; failures abort. Old writers must be quiescent before deployment.
This is a harness contract, not enforcement against owner tampering or hostile filesystem races.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from tests.qualification import canary, replay_export, safety
from tests.qualification.safety import Refused

VERSION = 1
FIELDS = {"version", "digest", "project", "ledger", "pins", "coverage", "acknowledged", "baseline",
          "earliest_reset", "cumulative", "last", "seen", "holds", "stopped", "completed"}


def canonical(ledger):
    """An absolute existing regular store, resolving parents but refusing a final symlink. Metadata reads only."""
    ledger = Path(ledger)
    if not ledger.is_absolute():
        raise Refused("the routing ledger path must be absolute")
    if ledger.is_symlink():
        raise Refused("the routing ledger must not be a symlink")
    found = ledger.parent.resolve() / ledger.name
    try:
        info = os.lstat(found)
    except OSError:
        raise Refused("the routing store path cannot be verified") from None
    if not stat.S_ISREG(info.st_mode):
        raise Refused("the routing ledger must be a regular file")
    return found


def resolve(ledger=None, settings=None):
    """The verified account's fixed campaign and default stores. Aliases validate by file identity, never key it.
    Returned store spellings come from production defaults, so setup and preflight bind identical metadata."""
    home = safety.check_real_home()
    defaults = dict(zip(("settings", "ledger"), canary._production_paths()))
    supplied = dict(settings=settings, ledger=ledger)
    for name, default in defaults.items():
        defaults[name] = canonical(default)
        if supplied[name] is not None and not os.path.samefile(canonical(supplied[name]), defaults[name]):
            raise Refused("ranked canary requires the account's default %s store" % name)
    return dict(defaults, path=home / ".local/state/vibe-workflow-qualification/ranked-canary/campaign.json")


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


def _entries(path):
    """Inspect only the fixed directory; never open legacy entries or follow recorded paths."""
    entries = set(os.listdir(path.parent))
    if entries - {path.name, path.name + ".lock"}:
        raise Refused("ranked campaign has legacy or unexplained state; history reconciliation is required")
    for name in entries:
        info = os.lstat(path.parent / name)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Refused("ranked campaign state must be regular files, never symlinks or linked evidence")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise Refused("ranked campaign files must be owner-only (0600)")
    return entries


@contextlib.contextmanager
def held(path, start=False):
    """Hold the one account lock through selection and the entire run. Never remove a lock, even after failure."""
    if not start and not os.path.lexists(path.parent):
        raise Refused("the ranked campaign is missing; resume never creates it")
    replay_export.check_dir(path.parent, create=start)
    entries = _entries(path)
    lock = path.with_name(path.name + ".lock")
    if entries and entries != {path.name, lock.name}:
        raise Refused("ranked campaign has unfinished creation or missing lock evidence; refusing unchanged")
    if not start and not entries:
        raise Refused("the ranked campaign is missing; resume never creates it")
    created = False
    try:
        descriptor = replay_export._private_file(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        created = True
    except FileExistsError:
        descriptor = replay_export._private_file(lock, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("another ranked canary run holds this account's campaign") from None
        entries = _entries(path)
        if path.name not in entries and not created:
            raise Refused("ranked campaign has prior lock-only evidence; refusing unchanged")
        if start and path.name in entries:
            raise Refused("a ranked campaign already exists for this account; only resume is allowed")
        yield
    finally:
        os.close(descriptor)


def create(path, record):
    """The new run's record from an approved preflight record; refused when one already exists."""
    replay_export.check_dir(path.parent)
    if path.exists() or path.is_symlink():
        raise Refused("a ranked campaign already exists for this account; it is never replaced, moved or deleted "
                      "to start or resume a run. Resume it with its original approved digest")
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
