"""Runtime hook helpers for native clients that share the same JSON subset."""

import hashlib
import json

from runtime.startup import StartupError


def native_string(payload, field, client, optional=False):
    if optional and field not in payload:
        return None
    value = payload.get(field)
    if (not isinstance(value, str) or not value.strip() or len(value) > 4096
            or any(not char.isprintable() for char in value)):
        raise StartupError(f"Invalid {client} {field}")
    return value


def tool_fingerprint(relevant):
    canonical = json.dumps(relevant, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_runtime(result, allowed, *, native_event=None, feedback_continue=False):
    """Render one decision; `allowed` maps each supported event to its permitted actions.

    `native_event` names the client event when it differs from the engine's event kind.
    `feedback_continue` renders a bounded continuation as non-error feedback instead of a block.
    """
    kind, action = result.event_kind, result.action
    if not isinstance(kind, str) or not isinstance(action, str) or kind not in allowed or action not in allowed[kind]:
        raise StartupError("Unsupported runtime result")
    if action == "pass":
        return {} if kind == "Stop" else None
    if not isinstance(result.reason, str):
        raise StartupError("Invalid runtime result reason")
    reason = " ".join("".join(char for char in result.reason if char.isprintable() or char.isspace()).split())[:1024]
    if not reason:
        raise StartupError("Invalid runtime result reason")
    if action == "deny":
        return {"hookSpecificOutput": {"hookEventName": kind, "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    if action == "continue":
        if feedback_continue:
            return {"hookSpecificOutput": {"hookEventName": kind, "additionalContext": reason}}
        return {"decision": "block", "reason": reason}
    if kind in {"Stop", "Interrupt"}:
        return {"systemMessage": reason}
    return {"hookSpecificOutput": {"hookEventName": native_event or kind, "additionalContext": reason}}


def runtime_diagnostic(kind, reason):
    """Errors never block an unvalidated scope or replace a completed tool result."""
    return {"systemMessage": f"Vibe Coding Workflow {kind} runtime unavailable: {reason}. "
            "Execution coverage and workflow readiness remain unverified."}
