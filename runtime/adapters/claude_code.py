"""Claude Code hook boundaries; no client settings are changed."""

from pathlib import Path

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
ALLOWED_ACTIONS = {"PreToolUse": {"pass", "deny", "context"},
                   "PostToolUse": {"pass", "context"}, "Stop": {"pass", "context", "continue"}}


def normalize(payload):
    return session_start.normalize(payload, "claude-code", SOURCES)


def native_string(payload, field, optional=False):
    return hook_events.native_string(payload, field, "claude-code", optional)


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
    parent_session = None
    if tool in GUARDED_TOOLS["claude-code"] and "agent_id" in payload:
        # Subagent tool hooks carry the root session and the concrete subagent separately.
        agent = native_string(payload, "agent_id")
        if agent == session:
            raise StartupError("Claude Code subagent tool hook must identify a distinct agent")
        parent_session, session = session, agent
    return RuntimeEvent(kind, "claude-code", session, cwd, turn, call, tool, active, fingerprint, None,
                        None, parent_session)


def render_runtime(result: HookResult, native_event=None) -> dict | None:
    # A bounded continuation is designed behavior, so use Claude's non-error feedback form.
    return hook_events.render_runtime(result, ALLOWED_ACTIONS, native_event=native_event, feedback_continue=True)


def runtime_diagnostic(kind, reason):
    return hook_events.runtime_diagnostic(kind, reason)
