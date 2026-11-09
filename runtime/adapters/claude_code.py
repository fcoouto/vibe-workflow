"""Claude Code hook boundaries; no client settings are changed."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from runtime.adapters import hook_events, session_start
from runtime.adapters.session_start import diagnostic, render
from runtime.engine import GUARDED_TOOLS, HookResult, RuntimeEvent
from runtime.startup import StartupError


SOURCES = {"startup", "resume", "clear", "compact", "fork"}
# `Write` carries a whole file in `tool_input`, so a write guard that read only 128 KiB would miss large
# files. The payload is parsed and hashed in memory and never stored; larger input stays a diagnostic.
RUNTIME_INPUT_LIMIT = 8 * 1024 * 1024
# Claude has no Interrupt hook; the prompt event is a delivery point, not a runtime decision.
# A failed tool may still have changed files, so PostToolUseFailure invalidates like PostToolUse.
RUNTIME_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"}
# The prompt event carries only title presentation; `sessionTitle` applies to these start sources.
PROMPT_EVENTS = {"UserPromptSubmit"}
# Subagent lifecycle events are recorded as observations only; they never decide or print anything.
SUBAGENT_EVENTS = {"SubagentStart", "SubagentStop"}
TITLE_SOURCES = {"startup", "resume", "fork"}
ALLOWED_ACTIONS = {"PreToolUse": {"pass", "deny", "context"},
                   "PostToolUse": {"pass", "context"}, "Stop": {"pass", "context", "continue"}}


def normalize(payload):
    return session_start.normalize(payload, "claude-code", SOURCES)


def native_string(payload, field, optional=False):
    return hook_events.native_string(payload, field, "claude-code", optional)


def target_directory(cwd, tool_input):
    """Where a guarded write really lands: its own directory when that is outside the session `cwd`.

    Claude's edit tools take absolute paths and the session `cwd` follows `cd` and worktrees, so deciding
    from `cwd` alone would leave an edit into an enrolled checkout unguarded. An unreadable path keeps `cwd`.
    """
    if not isinstance(tool_input, dict):
        return cwd
    raw = tool_input.get("file_path", tool_input.get("notebook_path"))
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096 or any(not char.isprintable() for char in raw):
        return cwd
    target = Path(os.path.realpath(raw if os.path.isabs(raw) else cwd / raw))
    if target.is_relative_to(os.path.realpath(cwd)):
        return cwd
    directory = target.parent
    while not directory.is_dir() and directory != directory.parent:
        directory = directory.parent
    return directory


def normalize_runtime(payload) -> RuntimeEvent:
    """Map one Claude hook payload; `prompt_id` is the only turn identity Claude exposes."""
    if not isinstance(payload, dict):
        raise StartupError("Expected a runtime JSON object")
    native = payload.get("hook_event_name")
    if not isinstance(native, str) or native not in RUNTIME_EVENTS:
        raise StartupError("Unsupported claude-code runtime event")
    kind = "PostToolUse" if native == "PostToolUseFailure" else native
    session = native_string(payload, "session_id")
    cwd = Path(native_string(payload, "cwd"))
    if not cwd.is_absolute():
        raise StartupError("Runtime cwd must be absolute")
    turn = native_string(payload, "prompt_id", optional=True)
    tool = call = fingerprint = None
    active = None
    if kind in {"PreToolUse", "PostToolUse"}:
        tool = native_string(payload, "tool_name")
        call = native_string(payload, "tool_use_id", optional=True)
        if turn is not None and call is not None:
            relevant = {"tool_name": tool}
            for field in ("tool_input", "tool_response", "error"):
                if field in payload and (field == "tool_input" or kind == "PostToolUse"):
                    relevant[field] = payload[field]
            try:
                fingerprint = hook_events.tool_fingerprint(relevant)
            except (ValueError, TypeError, UnicodeError):
                raise StartupError("Invalid runtime tool observation") from None
    if kind == "Stop" and "stop_hook_active" in payload:
        if type(payload["stop_hook_active"]) is not bool:
            raise StartupError("Invalid claude-code stop_hook_active")
        active = payload["stop_hook_active"]
    if tool in GUARDED_TOOLS["claude-code"]:
        cwd = target_directory(cwd, payload.get("tool_input"))
    parent_session = None
    if tool in GUARDED_TOOLS["claude-code"] and "agent_id" in payload:
        # Subagent tool hooks carry the root session and the concrete subagent separately.
        agent = native_string(payload, "agent_id")
        if agent == session:
            raise StartupError("Claude Code subagent tool hook must identify a distinct agent")
        parent_session, session = session, agent
    return RuntimeEvent(kind, "claude-code", session, cwd, turn, call, tool, active, fingerprint, None,
                        None, parent_session)


CLAUDE_VERSION = rb"([0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?) \(Claude Code\)\s*"
MAX_NATIVE_FILE = 256 * 1024
MANAGED_DIRECTORIES = {"darwin": "/Library/Application Support/ClaudeCode", "linux": "/etc/claude-code"}


def settings_files(root):
    """Documented file-backed settings sources; MDM, registry and cloud policy are not file-observable."""
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    files = [("user", config / "settings.json"), ("project", root / ".claude/settings.json"),
             ("local", root / ".claude/settings.local.json")]
    directory = MANAGED_DIRECTORIES.get(sys.platform)
    if directory is not None:
        managed = Path(directory)
        files.append(("managed", managed / "managed-settings.json"))
        try:
            files.extend(("managed", path) for path in sorted(managed.glob("managed-settings.d/*.json")))
        except OSError:
            files.append(("managed", managed / "managed-settings.d"))
    return files


def hook_locks(root):
    """Report only the two documented hook controls; other settings content is never retained."""
    locks = {"disabled_by": [], "managed_hooks_only": False, "unreadable": 0}
    for scope, path in settings_files(root):
        try:
            with open(path, "rb") as handle:
                raw = handle.read(MAX_NATIVE_FILE + 1)
            if len(raw) > MAX_NATIVE_FILE:
                raise ValueError("oversized")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            continue
        except (OSError, ValueError, UnicodeError, RecursionError):
            locks["unreadable"] += 1
            continue
        if value.get("disableAllHooks") is True:
            locks["disabled_by"].append(scope)
        if scope == "managed" and value.get("allowManagedHooksOnly") is True:
            locks["managed_hooks_only"] = True
    locks["disabled_by"] = sorted(set(locks["disabled_by"]))
    return locks


def installed_plugin(executable, package, deadline):
    """Find only this package among installed plugins; other plugins are never reported."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("plugin list", 0)
    result = subprocess.run([str(executable), "plugin", "list", "--json"], capture_output=True,
                            timeout=min(remaining, 5), stdin=subprocess.DEVNULL, check=False)
    if result.returncode != 0 or len(result.stdout) > 512 * 1024:
        raise OSError("plugin list unavailable")
    entries = json.loads(result.stdout)
    if not isinstance(entries, list):
        raise ValueError("malformed plugin list")
    package = Path(package).resolve()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for field in ("installPath", "readFromFolder"):
            location = entry.get(field)
            if isinstance(location, str) and Path(location).resolve() == package:
                return {"status": "enabled" if entry.get("enabled") is True else "disabled",
                        "scope": entry.get("scope") if isinstance(entry.get("scope"), str) else None,
                        "version": entry.get("version") if isinstance(entry.get("version"), str) else None}
    return {"status": "not_listed"}


def observe_native(report, scope, executable, definitions, deadline):
    """Fill the native and hooks sections; return (failed, incomplete).

    Claude exposes no hook-trust or hook-list call, so registration is checked from package definitions,
    installation state and the documented hook controls. Execution always stays unverified.
    """
    import hashlib
    from runtime.doctor import ObservationError, PACKAGE, version
    failed = incomplete = False
    try:
        observed = version(executable, deadline, CLAUDE_VERSION)
        report["native"] = {"status": "observed", "version": observed, "version_evidence": "unrecorded"}
    except ObservationError as error:
        report["native"]["status"] = str(error)
        incomplete = True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report["native"]["status"] = "unavailable"
        incomplete = True
    if scope is None:
        return failed, incomplete
    if report["native"].get("status") != "observed" or definitions is None:
        report["hooks"] = {"status": "unobserved"}
        return failed, True
    hooks = {"status": "incomplete", "source": str(PACKAGE / "hooks/claude-code.json"),
             "source_digest": hashlib.sha256((PACKAGE / "hooks/claude-code.json").read_bytes()).hexdigest(),
             "events": {name: {"status": "defined"} for name in definitions},
             "trust": "not_exposed_by_client", "managed_sources": "files_only"}
    hooks["locks"] = locks = hook_locks(scope.repository_root)
    try:
        hooks["plugin"] = plugin = installed_plugin(executable, PACKAGE, deadline)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        hooks["plugin"] = plugin = {"status": "unobserved"}
    # A managed-hooks-only policy still runs a plugin that managed settings force-enable, so it stays incomplete.
    if locks["disabled_by"] or plugin["status"] == "disabled":
        hooks["status"] = "failed"
        failed = True
    report["hooks"] = hooks
    return failed, True


def normalize_prompt(payload):
    """Identity for a prompt event; the prompt text is never read or kept."""
    if payload.get("hook_event_name") not in PROMPT_EVENTS:
        raise StartupError("Unsupported claude-code prompt event")
    session = native_string(payload, "session_id")
    cwd = Path(native_string(payload, "cwd"))
    if not cwd.is_absolute():
        raise StartupError("Prompt cwd must be absolute")
    return SimpleNamespace(client="claude-code", session_id=session, cwd=cwd)


def normalize_subagent(payload):
    """Identity for a subagent lifecycle event; the final message is read only by the recorder."""
    if payload.get("hook_event_name") not in SUBAGENT_EVENTS:
        raise StartupError("Unsupported claude-code subagent event")
    session = native_string(payload, "session_id")
    native_string(payload, "agent_id")
    cwd = Path(native_string(payload, "cwd"))
    if not cwd.is_absolute():
        raise StartupError("Subagent cwd must be absolute")
    return SimpleNamespace(client="claude-code", session_id=session, cwd=cwd)


class HookTitles:
    """Title transport for Claude Code, which accepts a title only in hook output.

    A title is observed from a hook's `session_title` input and delivered through `sessionTitle` on
    `SessionStart` (startup, resume, fork) or `UserPromptSubmit`. There is no immediate readback, so a
    write stays pending until a later observation equals it. Without an observation (a control command
    or a tool event) the saved baseline is assumed and the title is only staged.
    """

    deferred = True
    # Observed on Claude Code 2.1.311: the leading U+FFFB of a managed title (a Codex font workaround) is
    # stripped when stored, and the rest is returned unchanged. Deliver without it and restore it on reading
    # so the shared exact-match acknowledgment still holds.
    PREFIX = "￻"

    def __init__(self, observed=None, *, deliverable=False):
        self.observed, self.deliverable, self.emit = self.canonical(observed), deliverable, None

    @classmethod
    def canonical(cls, title):
        if title and title[0] in "▰▱":
            return cls.PREFIX + title
        return title

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, session):
        return self.observed

    def write(self, session, title):
        if self.deliverable:
            self.emit = title.removeprefix(self.PREFIX)


def observed_title(payload):
    """The hook's custom title, or '' when the session has none; malformed input is treated as unobserved."""
    value = payload.get("session_title")
    if value is None:
        return ""
    # U+FFFB opens every managed title and is a format character, so printability is checked without it.
    if not isinstance(value, str) or len(value) > 4096 or not value.replace("￻", "").isprintable():
        return None
    return value


def title_output(event, title, output=None):
    """Merge a delivered title into one hook response without disturbing existing context."""
    result = dict(output or {})
    specific = dict(result.get("hookSpecificOutput", {}))
    specific.update(hookEventName=event, sessionTitle=title)
    result["hookSpecificOutput"] = specific
    return result


def render_runtime(result: HookResult, native_event=None) -> dict | None:
    # A bounded continuation is designed behavior, so use Claude's non-error feedback form.
    return hook_events.render_runtime(result, ALLOWED_ACTIONS, native_event=native_event, feedback_continue=True)


def runtime_diagnostic(kind, reason):
    return hook_events.runtime_diagnostic(kind, reason)
