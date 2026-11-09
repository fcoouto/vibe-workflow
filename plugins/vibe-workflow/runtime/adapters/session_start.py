"""The SessionStart JSON subset shared by Codex and Claude Code."""

from pathlib import Path

from runtime.startup import StartupError, StartupEvent


def normalize(payload, client, sources):
    if payload.get("hook_event_name") != "SessionStart":
        raise StartupError(f"Unsupported {client} event")
    for field in ("session_id", "cwd", "source"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 4096:
            raise StartupError(f"Invalid {client} {field}")
    if payload["source"] not in sources or not Path(payload["cwd"]).is_absolute():
        raise StartupError("Unsupported startup source or relative cwd")
    for field in ("model", "permission_mode"):
        if payload.get(field) is not None and not isinstance(payload[field], str):
            raise StartupError(f"Invalid {client} {field}")
    return StartupEvent(client, payload["session_id"], Path(payload["cwd"]), payload["source"],
                        payload.get("model"), payload.get("permission_mode"))


def render(context):
    if context is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def diagnostic(reason):
    message = f"Vibe Coding Workflow startup unavailable: {reason}. Resolve this before claiming workflow readiness."
    return {"systemMessage": message,
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": message}}
