"""Move the legacy AGENTS.md Workstreams block into project settings; undo through settings_store.undo."""

from pathlib import Path
import re

from runtime import settings_store
from runtime.settings_store import SettingsError


START = "<!-- vibe-workflow:workstreams:start -->"
END = "<!-- vibe-workflow:workstreams:end -->"
CHOICE = re.compile(r"^- \[([ x])\] Workstreams\r?$", re.MULTILINE)
POINTER = ("<!-- vibe-workflow:features:start -->\n"
           "- Feature settings live in `.vibe-workflow/settings.json`. Before work that depends on a feature, run "
           "`python3 -I -B <package>/scripts/workflow.py features status --cwd <checkout>` from the installed "
           "vibe-workflow package.\n"
           "<!-- vibe-workflow:features:end -->\n")
AGENTS = "AGENTS.md"


def _agents(root):
    raw = settings_store.read_bytes(Path(root) / AGENTS, settings_store.MAX_FILE)
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
    for ending in ("\r\n", "\n"):
        if text.startswith(ending, end):
            end += len(ending)
            break
    return start, end, choices[0] == "x"


def legacy_workstreams(root):
    """The saved choice in a not-yet-migrated block: None, {'value': bool} or {'error': text}."""
    try:
        found = _block(_agents(root)[1])
    except (SettingsError, UnicodeDecodeError) as error:
        return dict(error=str(error) if isinstance(error, SettingsError) else f"{AGENTS} is not UTF-8")
    return None if found is None else dict(value=found[2])


def _plan(root):
    raw, text = _agents(root)
    found = _block(text)
    if found is not None:
        settings, _ = settings_store.read(root)
        saved = (settings or {}).get("features", {}).get("workstreams", {}).get("enabled")
        if saved is not None and saved != found[2]:
            raise SettingsError(f"{settings_store.SETTINGS} and {AGENTS} disagree about Workstreams; "
                                "choose one and remove the other")
    return found, raw, text


def _apply(root):
    if _plan(root)[0] is None:
        return dict(workstreams=dict(status="no_legacy_block"))
    with settings_store.locked(root) as (directory, warnings):
        found, raw, text = _plan(root)
        if found is None:
            return dict(workstreams=dict(status="no_legacy_block"))
        start, end, value = found

        def choose(current):
            current["features"].setdefault("workstreams", {})["enabled"] = value
            return current

        settings_raw, settings_data = settings_store.render(root, choose)
        newline = "\r\n" if "\r\n" in text[start:end] else "\n"
        pointer = "" if "vibe-workflow:features:start" in text else POINTER.replace("\n", newline)
        changes = {}
        if settings_data is not None:
            changes[settings_store.SETTINGS] = (settings_raw, settings_data)
        changes[AGENTS] = (raw, (text[:start] + pointer + text[end:]).encode("utf-8"))
        backup = settings_store.apply(root, directory, "workstreams", changes)
        return dict(workstreams=dict(status="migrated", value=value), backup=backup, warnings=warnings)


def run(root, action):
    if action == "plan":
        found, _, _ = _plan(root)
        return dict(workstreams=dict(status="no_legacy_block") if found is None else
                    dict(status="ready", value=found[2]))
    if action == "apply":
        return _apply(root)
    raise SettingsError(f"Unknown migrate action: {action}")
