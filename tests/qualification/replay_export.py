"""Export of the canary ledger's admission records with their snapshot detail, for the C.1 strict replay (replay
amendment, PR #230). The ledger drops each closed admission's `pressure` and `snapshot_id` 24 hours after it
(`usage_budget.Ledger._prune`), so the canary exports them before that can happen:

- at the end of every canary run (opencode_live.py cleanup, also after an abort);
- during a run, at the next admission once the last export is older than MAX_AGE_S (12 h, validation Edit 6 / PR
  #230), with no timer or daemon (`export_if_due`, called by the pilot driver);
- on demand with `--export-canary-admissions` (0 calls, no server).

A new canary run is refused while any admission record in the canary ledger is missing from the export (keys are
compared, not timestamps), until it is exported. Pruning happens only on a ledger write, so an export before the
run's first write still finds the detail.

Read-only: one `mode=ro` SQLite connection and a single `SELECT body FROM ledger`; the live ledger is never written.
The export directory (default ~/.local/state/vibe-workflow-replay/canary) is owner-only (0700, files 0600, no
symlinks, outside every repository). `admissions.jsonl` is append-only and keyed by (ledger identity, attempt id,
nonce), so repeated exports add nothing twice; `last-export.json` records when the last export ran.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from tests.qualification import outputs
from tests.qualification.safety import Refused

MAX_AGE_S = 12 * 3600


def default_dir():
    """~/.local/state/vibe-workflow-replay/canary, resolved from HOME when called (never at import)."""
    return Path.home() / ".local/state/vibe-workflow-replay/canary"
RECORDS, LAST = "admissions.jsonl", "last-export.json"
DETAIL = ("pressure", "snapshot_id")


def check_dir(path, create=True):
    """The resolved export directory, or `Refused`: absolute, never a symlink, outside every repository, owned by
    this user with mode 0700 (created so, with private parents, when absent)."""
    directory = Path(path)
    if not directory.is_absolute():
        raise Refused("the replay export directory must be absolute")
    if outputs.inside_repository(directory):
        raise Refused("the replay export directory must be outside every repository")
    if not directory.exists() and create:
        for folder in reversed([p for p in (directory, *directory.parents) if not p.exists()]):
            os.mkdir(folder, 0o700)
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        raise Refused("the replay export directory does not exist") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() \
            or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused("the replay export directory must be a real directory owned by the user with mode 0700")
    return directory


def read_admissions(ledger_path):
    """`(identity, admission records)` through a read-only connection; `(None, [])` when there is no ledger."""
    path = Path(ledger_path)
    if not path.is_file():
        return None, []
    with closing(sqlite3.connect("%s?mode=ro" % path.resolve().as_uri(), uri=True, timeout=10)) as db:
        row = db.execute("SELECT body FROM ledger WHERE id=1").fetchone()
    if row is None:
        raise Refused("the canary ledger has no record")
    data = json.loads(row[0])
    records = data.get("admissions") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise Refused("the canary ledger's admissions are unreadable")
    return data.get("identity"), [record for record in records if isinstance(record, dict)]


def _key(identity, record):
    return "%s|%s|%s" % (identity, record.get("attempt_id"), record.get("nonce"))


def _private_file(path, flags):
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        os.close(descriptor)
        raise Refused("replay export files must be owner-only (0600)")
    return descriptor


def exported_keys(directory):
    path = Path(directory) / RECORDS
    if not path.exists():
        return set()
    with os.fdopen(_private_file(path, os.O_RDONLY), "r") as handle:
        return {json.loads(line)["key"] for line in handle if line.strip()}


def last_export(directory):
    path = Path(directory) / LAST
    if not path.exists():
        return None
    with os.fdopen(_private_file(path, os.O_RDONLY), "r") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) and type(value.get("at")) is int else None


def export(ledger_path, directory=None, now=None):
    """Append every admission record not exported yet, verbatim with its snapshot detail; idempotent."""
    now = int(time.time()) if now is None else now
    directory = check_dir(default_dir() if directory is None else directory)
    identity, records = read_admissions(ledger_path)
    known = exported_keys(directory)
    new = [record for record in records if _key(identity, record) not in known]
    if new:
        with os.fdopen(_private_file(directory / RECORDS, os.O_WRONLY | os.O_APPEND | os.O_CREAT), "a") as handle:
            for record in new:
                handle.write(json.dumps(dict(key=_key(identity, record), ledger_identity=identity, exported_at=now,
                                             snapshot_detail=all(k in record for k in DETAIL), record=record),
                                        sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    summary = dict(at=now, ledger_identity=identity, records_seen=len(records), exported=len(new),
                   with_snapshot_detail=sum(1 for record in new if all(k in record for k in DETAIL)))
    temporary = directory / (".%s.tmp" % LAST)
    with os.fdopen(_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), "w") as handle:
        json.dump(summary, handle, sort_keys=True)
    os.replace(temporary, directory / LAST)
    return summary


def export_if_due(ledger_path, directory=None, now=None):
    """During a run: export when the last export is missing or older than MAX_AGE_S, else None (reads one file)."""
    now = int(time.time()) if now is None else now
    directory = default_dir() if directory is None else Path(directory)
    last = last_export(check_dir(directory, create=False)) if directory.exists() else None
    if last is not None and now - last["at"] <= MAX_AGE_S:
        return None
    return export(ledger_path, directory, now)


def require_fresh(ledger_path, directory=None):
    """Refuse a new canary run while any current admission record is not in the export (compared by key, not by
    the export's age). Reads only; never exports or writes by itself."""
    directory = default_dir() if directory is None else Path(directory)
    identity, records = read_admissions(ledger_path)
    if not records:
        return None
    known = exported_keys(check_dir(directory, create=False)) if directory.exists() else set()
    missing = [record for record in records if _key(identity, record) not in known]
    if missing:
        raise Refused("%d canary admission records are not exported yet; export them first with "
                      "--export-canary-admissions (0 calls) before a new canary run" % len(missing))
    return dict(records=len(records), exported=True)
