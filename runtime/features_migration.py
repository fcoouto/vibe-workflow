"""Move the legacy AGENTS.md Workstreams block into project settings, with a private backup and undo."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from runtime import settings_store
from runtime.settings_store import SettingsError


START = "<!-- vibe-workflow:workstreams:start -->"
END = "<!-- vibe-workflow:workstreams:end -->"
CHOICE = re.compile(r"^- \[([ x])\] Workstreams$", re.MULTILINE)
POINTER = ("<!-- vibe-workflow:features:start -->\n"
           "- Feature settings live in `.vibe-workflow/settings.json`. Before work that depends on a feature, run "
           "`python3 -I -B <package>/scripts/workflow.py features status --cwd <checkout>` from the installed "
           "vibe-workflow package.\n"
           "<!-- vibe-workflow:features:end -->\n")
AGENTS = "AGENTS.md"
SETTINGS = f"{settings_store.DIRECTORY}/settings.json"
MAX_INSTRUCTIONS = 1024 * 1024


def _agents(root):
    raw = settings_store._read_bytes(Path(root) / AGENTS, MAX_INSTRUCTIONS)
    return raw, None if raw is None else raw.decode("utf-8")


def _block(text):
    """Return (start, end, enabled) of the one owned block, None when absent; raise when malformed."""
    if text is None or START not in text and END not in text:
        return None
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise SettingsError(f"{AGENTS} has a malformed or repeated Workstreams block")
    start, end = text.index(START), text.index(END) + len(END)
    choices = CHOICE.findall(text[start:end])
    if len(choices) != 1:
        raise SettingsError(f"{AGENTS} Workstreams block needs exactly one '- [x] Workstreams' or '- [ ] Workstreams' line")
    if text[end:end + 1] == "\n":
        end += 1
    return start, end, choices[0] == "x"


def legacy_workstreams(root):
    """The saved choice in a not-yet-migrated block: None, {'value': bool} or {'error': text}."""
    try:
        found = _block(_agents(root)[1])
    except (SettingsError, UnicodeDecodeError) as error:
        return dict(error=str(error))
    return None if found is None else dict(value=found[2])


def _plan(root):
    raw, text = _agents(root)
    found = _block(text)
    if found is None:
        return None, raw, text
    settings, _ = settings_store.read(root)
    saved = (settings or {}).get("features", {}).get("workstreams", {}).get("enabled")
    if saved is not None and saved != found[2]:
        raise SettingsError(f"{SETTINGS} saves Workstreams {'on' if saved else 'off'} but {AGENTS} says "
                            f"{'on' if found[2] else 'off'}; choose one and remove the other")
    return found, raw, text


def _write(target, data, mode, directory):
    descriptor, temporary = tempfile.mkstemp(prefix=".migrate-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _apply(root):
    found, raw, text = _plan(root)
    if found is None:
        return dict(workstreams=dict(status="no_legacy_block"))
    start, end, value = found
    warnings = settings_store.check_ignored(root)
    private = settings_store.private_directory(root)
    backups = private / "backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(tempfile.mkdtemp(prefix=f"{stamp}-workstreams-", dir=backups))
    _, settings_raw = settings_store.read(root)
    (backup / "AGENTS.md").write_bytes(raw)
    if settings_raw is not None:
        (backup / "settings.json").write_bytes(settings_raw)

    def enable(current):
        current["features"].setdefault("workstreams", {})["enabled"] = value
        return current

    settings_store.update(root, enable)
    replacement = "" if "vibe-workflow:features:start" in text else POINTER
    updated = (text[:start] + replacement + text[end:]).encode("utf-8")
    if settings_store.digest(_agents(root)[0]) != settings_store.digest(raw):
        raise SettingsError(f"{AGENTS} changed during migration; settings were saved, the block was left in place")
    _write(Path(root) / AGENTS, updated, (Path(root) / AGENTS).stat().st_mode & 0o777, private)
    record = dict(version=1, feature="workstreams", files={
        AGENTS: dict(before=settings_store.digest(raw), after=settings_store.digest(updated)),
        SETTINGS: dict(before=settings_store.digest(settings_raw),
                       after=settings_store.digest(settings_store._read_bytes(settings_store.path(root))))})
    (backup / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return dict(workstreams=dict(status="migrated", value=value), backup=str(backup.relative_to(private)),
                warnings=warnings)


def _undo(root, backup):
    if not isinstance(backup, str) or not re.fullmatch(r"backups/[A-Za-z0-9TZ_-]+", backup):
        raise SettingsError("Name a backup as returned by migrate apply, for example backups/<name>")
    private = settings_store.private(root)
    folder = private / backup
    record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
    restored, kept = [], []
    for relative, sources in ((AGENTS, "AGENTS.md"), (SETTINGS, "settings.json")):
        target = Path(root) / relative
        hashes = record["files"][relative]
        if settings_store.digest(settings_store._read_bytes(target, MAX_INSTRUCTIONS)) != hashes["after"]:
            kept.append(relative)
            continue
        if hashes["before"] == "absent":
            target.unlink()
        else:
            _write(target, (folder / sources).read_bytes(), target.stat().st_mode & 0o777, private)
        restored.append(relative)
    return dict(restored=restored, kept=kept)


def run(root, action, backup=None):
    if action == "plan":
        found, _, _ = _plan(root)
        return dict(workstreams=dict(status="no_legacy_block") if found is None else
                    dict(status="ready", value=found[2]))
    if action == "apply":
        return _apply(root)
    if action == "undo":
        return _undo(root, backup)
    raise SettingsError(f"Unknown migrate action: {action}")
