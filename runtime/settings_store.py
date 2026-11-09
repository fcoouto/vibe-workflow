"""Committed project settings: strict reads, and locked, backed-up, compare-and-set file changes with undo."""

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


DIRECTORY = ".vibe-workflow"
PRIVATE = "private"
SETTINGS = f"{DIRECTORY}/settings.json"
SCHEMA_VERSION = 1
MAX_BYTES = 256 * 1024
MAX_FILE = 1024 * 1024
MAX_REVISION = 2**53 - 1
BACKUP = re.compile(r"backups/[0-9]{8}T[0-9]{6}Z-[a-z_.]+-[A-Za-z0-9_]+")
COPY = re.compile(r"file-[0-9]+")
# The lock is the only file written outside a backup folder; backup and staged files are checked by exact path.
PROBES = ("settings.lock",)


class SettingsError(ValueError):
    """A user-facing settings problem; messages name keys and paths, never supplied values."""


def path(root):
    return Path(root) / SETTINGS


def private(root):
    return Path(root) / DIRECTORY / PRIVATE


def digest(raw):
    return "absent" if raw is None else "sha256:" + hashlib.sha256(raw).hexdigest()


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SettingsError(f"Duplicate key: {key}")
        value[key] = item
    return value


def snapshot(file, limit=MAX_BYTES):
    """(bytes, identity) of a regular single-link file, (None, None) when absent; never follows links or blocks."""
    try:
        descriptor = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None
    except OSError:
        raise SettingsError(f"{Path(file).name} must be a regular file") from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SettingsError(f"{Path(file).name} must be a regular file")
        raw = os.read(descriptor, limit + 1)
        if len(raw) > limit:
            raise SettingsError(f"{Path(file).name} is larger than {limit} bytes")
        return raw, (info.st_dev, info.st_ino)
    finally:
        os.close(descriptor)


def read_bytes(file, limit=MAX_BYTES):
    return snapshot(file, limit)[0]


def parse_object(raw, what):
    """Strict JSON object: UTF-8, no duplicate keys."""
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SettingsError(f"{what} is not valid JSON (line {getattr(error, 'lineno', '?')})") from None
    if not isinstance(value, dict):
        raise SettingsError(f"{what} must be a JSON object")
    return value


def parse(raw):
    """Validate the envelope; feature entries are checked against their manifests by the resolver."""
    value = parse_object(raw, "Settings")
    if set(value) != {"schema_version", "revision", "features"}:
        raise SettingsError("Settings need exactly schema_version, revision and features")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise SettingsError(f"Settings schema_version must be {SCHEMA_VERSION}")
    if type(value["revision"]) is not int or not 1 <= value["revision"] <= MAX_REVISION:
        raise SettingsError("Settings revision must be a positive integer")
    if not isinstance(value["features"], dict):
        raise SettingsError("Settings features must be an object")
    return value


def _folder(root):
    folder = Path(root) / DIRECTORY
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise SettingsError(f"{DIRECTORY} must be a directory, not a link")


def read(root):
    """Return (settings or None, raw bytes or None); never writes."""
    _folder(root)
    raw = read_bytes(path(root))
    return (None if raw is None else parse(raw)), raw


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)


def _require_ignored(root, relatives):
    result = _git(root, "check-ignore", "--no-index", *relatives)
    if result.returncode not in (0, 1) or set(result.stdout.splitlines()) != set(relatives):
        raise SettingsError(f"Add /{DIRECTORY}/{PRIVATE}/ to .git/info/exclude before writing settings")


def check_ignored(root):
    """Refuse unless private files are ignored and nothing private is tracked; return warnings."""
    _require_ignored(root, [f"{DIRECTORY}/{PRIVATE}/{probe}" for probe in PROBES])
    if _git(root, "ls-files", "--", f"{DIRECTORY}/{PRIVATE}").stdout.strip():
        raise SettingsError(f"{DIRECTORY}/{PRIVATE}/ is tracked by Git; untrack it before writing settings")
    if _git(root, "ls-files", "--", f"{DIRECTORY}/project.json").stdout.strip():
        return [f"{DIRECTORY}/project.json is tracked by Git; it is bound to one checkout and should be untracked"]
    return []


def owned_directory(folder):
    """Create or accept an owner-only directory; refuse links and shared modes; a new entry is made durable."""
    folder = Path(folder)
    if not folder.is_symlink() and not folder.exists():
        folder.mkdir(mode=0o700)
        _fsync(folder.parent)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SettingsError(f"{folder.name} must be an owner-only directory, not a link")
    return folder


@contextmanager
def locked(root):
    """Serialize every settings change and undo in this checkout; yield (private directory, warnings)."""
    warnings = check_ignored(root)
    _folder(root)
    if not (Path(root) / DIRECTORY).exists():
        (Path(root) / DIRECTORY).mkdir()
        _fsync(root)
    directory = owned_directory(private(root))
    lock = os.open(directory / "settings.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield directory, warnings
    finally:
        os.close(lock)


def portable(value, where="settings"):
    """Committed values carry no absolute or home-relative paths."""
    if isinstance(value, str) and (os.path.isabs(value) or value.startswith("~")):
        raise SettingsError(f"{where} must not contain a machine path")
    if isinstance(value, dict):
        for key, item in value.items():
            portable(item, f"{where}.{key}")
    if isinstance(value, list):
        for item in value:
            portable(item, where)


def render(root, change):
    """Return (raw before, data after or None when unchanged) for change(settings)."""
    current, raw = read(root)
    current = current or dict(schema_version=SCHEMA_VERSION, revision=0, features={})
    updated = change(copy.deepcopy(current))
    if updated == current:
        return raw, None
    if current["revision"] >= MAX_REVISION:
        raise SettingsError("Settings revision exhausted")
    updated = dict(updated, schema_version=SCHEMA_VERSION, revision=current["revision"] + 1)
    portable(updated["features"])
    data = (json.dumps(updated, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_BYTES:
        raise SettingsError(f"Settings would exceed {MAX_BYTES} bytes")
    parse(data)
    return raw, data


def _stage(staged, data, mode):
    """Write data to staged, a path already confirmed ignored; remove it on failure."""
    try:
        descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, mode)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise
    return staged


def _fsync(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace(root, relative, before, data, staging, limit):
    """Replace relative with data (None deletes) only if it still holds before; atomic and durable."""
    target = Path(root) / relative
    first, identity = snapshot(target, limit)
    staged = None if data is None else _stage(staging, data, (target.stat().st_mode & 0o777) if first is not None else 0o644)
    try:
        current, now = snapshot(target, limit)
        # Git and hand edits do not take the lock: recheck content and file identity right before replacing.
        if digest(first) != before or digest(current) != before or now != identity:
            raise SettingsError(f"{relative} changed during the update; nothing more was written")
        if staged is None:
            target.unlink(missing_ok=True)
        else:
            os.replace(staged, target)
            staged = None
        _fsync(target.parent)
    finally:
        if staged is not None:
            Path(staged).unlink(missing_ok=True)


def apply(root, directory, feature, changes):
    """Back up then apply {relative: (before bytes or None, after bytes or None)}; return the backup name."""
    backups = owned_directory(directory / "backups")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = owned_directory(tempfile.mkdtemp(prefix=f"{stamp}-{feature}-", dir=backups))
    names = [f"file-{index}" for index in range(len(changes))]
    inside = Path(DIRECTORY) / PRIVATE / folder.relative_to(directory)
    try:
        _require_ignored(root, [str(inside / name) for name in names + ["record.json"]] +
                         [str(inside / f"stage-{index}") for index in range(len(changes))])
    except SettingsError:
        folder.rmdir()
        raise
    files = {}
    for name, (relative, (before, after)) in zip(names, changes.items()):
        if before is not None:
            (folder / name).write_bytes(before)
            _fsync(folder / name)
        files[relative] = dict(copy=name, before=digest(before), after=digest(after))
    record = folder / "record.json"
    record.write_text(json.dumps(dict(version=1, feature=feature, files=files), indent=2) + "\n", encoding="utf-8")
    _fsync(record)
    _fsync(folder)
    _fsync(backups)
    for index, (relative, (before, after)) in enumerate(changes.items()):
        _replace(root, relative, digest(before), after, folder / f"stage-{index}", MAX_FILE)
    return str(folder.relative_to(directory))


def update(root, change):
    """Apply change(settings) under the lock with a backup; no-op when unchanged. Returns (warnings, backup)."""
    with locked(root) as (directory, warnings):
        raw, data = render(root, change)
        if data is None:
            return warnings, None
        return warnings, apply(root, directory, "settings", {SETTINGS: (raw, data)})


def undo(root, backup):
    """Restore each file of one backup that still holds that change's output; keep the others."""
    if not isinstance(backup, str) or not BACKUP.fullmatch(backup):
        raise SettingsError("Name a backup as returned by the change, for example backups/<name>")
    with locked(root) as (directory, _):
        folder = directory / backup
        owned_directory(folder.parent)
        if folder.is_symlink() or not folder.is_dir():
            raise SettingsError(f"No backup {backup}")
        owned_directory(folder)
        record = parse_object(read_bytes(folder / "record.json") or b"", "Backup record")
        inside = Path(DIRECTORY) / PRIVATE / backup
        _require_ignored(root, [str(inside / f"restore-{index}") for index in range(len(record["files"]))])
        restored, kept = [], []
        for index, (relative, entry) in enumerate(record["files"].items()):
            if relative not in (SETTINGS, "AGENTS.md") or not COPY.fullmatch(str(entry.get("copy"))):
                raise SettingsError(f"Backup {backup} names an unsupported file")
            before = None if entry["before"] == "absent" else read_bytes(folder / entry["copy"], MAX_FILE)
            if digest(before) != entry["before"]:
                raise SettingsError(f"Backup {backup} does not match its record")
            current, _ = snapshot(Path(root) / relative, MAX_FILE)
            if digest(current) == entry["before"]:
                continue
            if digest(current) != entry["after"]:
                kept.append(relative)
                continue
            _replace(root, relative, entry["after"], before, folder / f"restore-{index}", MAX_FILE)
            restored.append(relative)
        return dict(restored=restored, kept=kept)
