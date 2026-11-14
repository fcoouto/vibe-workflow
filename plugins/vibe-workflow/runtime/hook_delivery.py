"""Hook delivery note: proof that a client runs this package's hooks.

No client lists whether it trusts a plugin's hooks, so the only evidence is the startup hook running. That hook
(`record_delivery`) leaves one small private note per client; `delivered` is true only for a note that names the
current hook definition content. Package versions are diagnostic metadata. Neither function spawns a process, uses the
network or calls a model."""

import calendar
import hashlib
import json
from pathlib import Path
import re
import time

from runtime import package_versions as pv


NOTE_DIRECTORY = "delivered"
HOOK_FILES = {"codex": "hooks/codex.json", "claude-code": "hooks/claude-code.json"}
SCHEMA_VERSION = 1
MAX_NOTE = 1024
MAX_HOOKS = 65536
MAX_AGE = 24 * 3600
MAX_EVIDENCE_AGE = 7 * 24 * 3600
MIN_REMAINING = 0.5
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DIGEST = re.compile(r"[0-9a-f]{64}")


def note_path(client, environ=None):
    return pv.record_path(environ).with_name(NOTE_DIRECTORY) / f"{client}.json"


def current(package, client):
    """`(version, hook definition hash)` of this package for the client, or None when either is unreadable."""
    version = pv.manifest_version(package, client) if client in HOOK_FILES else None
    if version is None:
        return None
    try:
        return version, hashlib.sha256(pv._read_bytes(Path(package) / HOOK_FILES[client], MAX_HOOKS)).hexdigest()
    except (OSError, ValueError):
        return None


def _epoch(text):
    return calendar.timegm(time.strptime(text, TIME_FORMAT))


def read_note(path):
    """The note, or None when absent, oversized, malformed or of another schema."""
    try:
        note = pv._read_json(path, MAX_NOTE)
        if (note.get("schema_version") != SCHEMA_VERSION or note.get("client") not in HOOK_FILES or
                not isinstance(note.get("version"), str) or not pv.VERSION.fullmatch(note["version"]) or
                not isinstance(note.get("hook_sha256"), str) or not DIGEST.fullmatch(note["hook_sha256"])):
            return None
        note["delivered_epoch"] = _epoch(note["delivered_at"])
    except (OSError, ValueError, TypeError, OverflowError):
        return None
    return note


def _matches(note, client, wanted):
    return note is not None and note["client"] == client and note["hook_sha256"] == wanted[1]


def delivery(package, client, environ=None, now=None):
    """The time of this client's note when it matches the hook definitions it runs now and
    is at most seven days old (active sessions rewrite it daily); otherwise None."""
    wanted = current(package, client)
    note = read_note(note_path(client, environ)) if wanted is not None else None
    now = int(time.time() if now is None else now)
    if _matches(note, client, wanted) and 0 <= now - note["delivered_epoch"] <= MAX_EVIDENCE_AGE:
        return note["delivered_at"]
    return None


def delivered(package, client, environ=None, now=None):
    return delivery(package, client, environ, now) is not None


def record_delivery(package, client, environ=None, deadline=None, now=None):
    """Write the note, unless less than half a second remains before the deadline, when it is absent, differs from the current version or hook hash, or is older than a day.

    Returns whether it wrote. Failures to write raise OSError or ValueError; the startup caller stays silent."""
    if deadline is not None and deadline - time.monotonic() < MIN_REMAINING:
        return False
    wanted = current(package, client)
    if wanted is None:
        return False
    now = int(time.time() if now is None else now)
    path = note_path(client, environ)
    note = read_note(path)
    if _matches(note, client, wanted) and note["version"] == wanted[0] and 0 <= now - note["delivered_epoch"] <= MAX_AGE:
        return False
    data = (json.dumps(dict(schema_version=SCHEMA_VERSION, client=client, version=wanted[0], hook_sha256=wanted[1],
                            delivered_at=time.strftime(TIME_FORMAT, time.gmtime(now)))) + "\n").encode()
    # The parent holds the other state files; pv.write_private makes the leaf directory private.
    pv._private_directory(path.parent.parent)
    pv.write_private(path, data, ".delivered-")
    return True
