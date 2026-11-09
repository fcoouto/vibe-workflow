"""Committed project settings: strict JSON, revision and digest compare-and-set, atomic replace."""

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


DIRECTORY = ".vibe-workflow"
PRIVATE = "private"
SCHEMA_VERSION = 1
MAX_BYTES = 256 * 1024
MAX_REVISION = 2**53 - 1


class SettingsError(ValueError):
    """A user-facing settings problem; the message names what to fix."""


def path(root):
    return Path(root) / DIRECTORY / "settings.json"


def private(root):
    return Path(root) / DIRECTORY / PRIVATE


def digest(raw):
    return "absent" if raw is None else "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SettingsError(f"Duplicate key in settings: {key}")
        value[key] = item
    return value


def _read_bytes(file, limit=MAX_BYTES):
    """Read a regular, unlinked file without following links; None when absent."""
    try:
        descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SettingsError(f"{file} must be a regular file") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SettingsError(f"{file} must be a regular file")
        raw = os.read(descriptor, limit + 1)
        if len(raw) > limit:
            raise SettingsError(f"{file} is larger than {limit} bytes")
        return raw
    finally:
        os.close(descriptor)


def parse_object(raw, what):
    """Strict JSON object: UTF-8, no duplicate keys."""
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SettingsError(f"{what} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise SettingsError(f"{what} must be a JSON object")
    return value


def parse(raw):
    """Validate the envelope; feature entries are checked against their manifests by the resolver."""
    value = parse_object(raw, "Settings")
    if set(value) != {"schema_version", "revision", "features"}:
        raise SettingsError("Settings need exactly schema_version, revision and features")
    if value["schema_version"] != SCHEMA_VERSION or type(value["schema_version"]) is not int:
        raise SettingsError(f"Unsupported settings schema_version: {value['schema_version']!r}")
    if type(value["revision"]) is not int or not 1 <= value["revision"] <= MAX_REVISION:
        raise SettingsError("Settings revision must be a positive integer")
    if not isinstance(value["features"], dict):
        raise SettingsError("Settings features must be an object")
    return value


def read(root):
    """Return (settings or None, raw bytes or None); never writes."""
    raw = _read_bytes(path(root))
    return (None if raw is None else parse(raw)), raw


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)


def check_ignored(root):
    """Refuse unless private files are ignored and untracked; return warnings."""
    probe = f"{DIRECTORY}/{PRIVATE}/settings.lock"
    if _git(root, "check-ignore", "-q", "--no-index", probe).returncode != 0:
        raise SettingsError(f"Add /{DIRECTORY}/{PRIVATE}/ to .git/info/exclude or .gitignore before writing settings")
    if _git(root, "ls-files", "--", f"{DIRECTORY}/{PRIVATE}").stdout.strip():
        raise SettingsError(f"{DIRECTORY}/{PRIVATE}/ is tracked by Git; untrack it before writing settings")
    if _git(root, "ls-files", "--", f"{DIRECTORY}/project.json").stdout.strip():
        return [f"{DIRECTORY}/project.json is tracked by Git; it is bound to one checkout and should be untracked"]
    return []


def private_directory(root):
    """Create private/ owner-only, refusing links and shared modes."""
    directory = private(root)
    directory.parent.mkdir(exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SettingsError(f"{directory} must be an owner-only directory")
    return directory


def portable(value, where="settings"):
    """Committed values carry no absolute or home-relative paths."""
    if isinstance(value, str) and (os.path.isabs(value) or value.startswith("~")):
        raise SettingsError(f"{where} must not contain a machine path: {value!r}")
    if isinstance(value, dict):
        for key, item in value.items():
            portable(item, f"{where}.{key}")
    if isinstance(value, list):
        for item in value:
            portable(item, where)


def replace_file(target, data, directory):
    """Write data next to nothing shared: a private temporary file, then an atomic rename."""
    descriptor, temporary = tempfile.mkstemp(prefix=".settings-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    parent = os.open(Path(target).parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def update(root, change):
    """Apply change(settings) under the settings lock; no-op when nothing changes. Returns (settings, warnings)."""
    root = Path(root)
    warnings = check_ignored(root)
    directory = private_directory(root)
    lock = os.open(directory / "settings.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current, raw = read(root)
        current = current or dict(schema_version=SCHEMA_VERSION, revision=0, features={})
        updated = change(copy.deepcopy(current))
        if updated == current:
            return current, warnings
        if current["revision"] >= MAX_REVISION:
            raise SettingsError("Settings revision exhausted")
        updated = dict(updated, schema_version=SCHEMA_VERSION, revision=current["revision"] + 1)
        portable(updated["features"])
        data = (json.dumps(updated, indent=2) + "\n").encode("utf-8")
        # Hand edits and Git do not take the lock; compare content, not only the revision.
        if digest(_read_bytes(path(root))) != digest(raw):
            raise SettingsError("Settings changed while being updated; read them again and retry")
        replace_file(path(root), data, directory)
        return updated, warnings
    finally:
        os.close(lock)
