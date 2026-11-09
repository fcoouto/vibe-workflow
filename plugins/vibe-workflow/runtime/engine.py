"""Client-independent runtime records; state and policy evaluation follow later."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


EventKind = Literal["PreToolUse", "PostToolUse", "Stop", "Interrupt"]
Action = Literal["pass", "deny", "context", "continue"]


@dataclass(frozen=True)
class RuntimeEvent:
    kind: EventKind
    client: str
    session_id: str
    cwd: Path
    turn_id: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    stop_hook_active: bool | None = None
    # Conflict detection only; never substitute this digest for native identity.
    receipt_fingerprint: str | None = None
    patch_input_fingerprint: str | None = None
    transcript_path: Path | None = None
    hook_parent_session_id: str | None = None


@dataclass(frozen=True)
class HookResult:
    event_kind: EventKind
    action: Action = "pass"
    reason: str = ""


def patch_blocked(task):
    return task["state"] != "WORKING" or task["mode"] != "editable" or any(
        condition["status"] == "pending" and condition["kind"] in {"blocker", "decision"}
        for condition in task["conditions"])


def evaluate(event, snapshot):
    """Evaluate only deterministic observations; changes contain no native payloads."""
    from copy import deepcopy

    result = HookResult(event.kind)
    changes = {"generation_delta": 0}
    task = deepcopy(snapshot.get("task"))
    turn = snapshot.get("turn")
    bound = task is not None and turn is not None and turn["task_id"] == task["task_id"]
    current = bound and task.get("turn_id") == event.turn_id

    def revoke():
        if task:
            for condition in task["conditions"]:
                if "continuation" in condition:
                    condition["continuation"]["revoked"] = True

    if event.kind == "PreToolUse":
        if event.turn_id is None:
            if task:
                task["turn_uncertain"] = True
                revoke()
        elif turn is None:
            changes["turn"] = {"task_id": task["task_id"] if task else None}
            if task:
                revoke()
                task["turn_id"] = event.turn_id
                task["turn_uncertain"] = False
        if event.tool_name == "apply_patch":
            if task and patch_blocked(task):
                result = HookResult(event.kind, "deny", "Patching requires a WORKING editable bound task without applicable blockers")
    if event.kind in {"PreToolUse", "PostToolUse"}:
        stable = event.turn_id is not None and event.tool_call_id is not None and event.receipt_fingerprint is not None
        previous = snapshot.get("receipt")
        conflict = stable and previous is not None and (
            previous["conflicted"] or previous["fingerprint"] != event.receipt_fingerprint)
        if stable and previous is None:
            changes["receipt"] = event.receipt_fingerprint
        if conflict:
            changes["poison_receipt"] = True
        if event.kind == "PostToolUse" and (not stable or previous is None or conflict):
            changes["generation_delta"] = 1
        if conflict or (event.kind == "PostToolUse" and not stable):
            result = HookResult(event.kind, "context", "Runtime event identity is uncertain; deduplication is unverified") if result.action == "pass" else result
            if task and (current or event.turn_id is None):
                task["turn_uncertain"] = True
                revoke()
        # A late completion cannot establish a turn against a later binding.
        if event.kind == "PostToolUse" and event.turn_id is not None and turn is None:
            changes["turn"] = {"task_id": None}
            if task:
                task["turn_uncertain"] = True
                revoke()
    if event.kind in {"Stop", "Interrupt"} and event.turn_id is not None and turn is None:
        changes["turn"] = {"task_id": None}
    if event.kind == "Stop" and task and (event.turn_id is None or turn is None or turn["task_id"] is None):
        task["turn_uncertain"] = True
        revoke()
    if event.kind == "Interrupt":
        if not current or task.get("turn_uncertain"):
            result = HookResult(event.kind, "context", "Interrupt correlation is uncertain; the bound task was not changed")
        elif task["state"] != "CANCELLED":
            revoke()
            task.update(state="PAUSED", reason="Native turn interrupted; task paused", reference="native:Interrupt")
    if event.kind == "Stop" and (current and turn["continuation_consumed"] == 0 and event.stop_hook_active is False and
            not task.get("turn_uncertain") and task["state"] in {"WORKING", "REVIEW"} and not any(
                condition["status"] == "pending" and condition["kind"] in {"decision", "blocker"}
                for condition in task["conditions"])):
        for condition in task["conditions"]:
            continuation = condition.get("continuation")
            if (condition["status"] == "pending" and condition["kind"] == "runnable" and continuation and
                    continuation["turn_id"] == event.turn_id and continuation["budget"] == 1 and
                    continuation["consumed"] == 0 and not continuation["revoked"]):
                continuation["consumed"] = 1
                changes["consume_turn"] = True
                result = HookResult(event.kind, "continue", condition["reason"])
                break
    if task != snapshot.get("task"):
        task["revision"] += 1
        changes["task"] = task
    return result, changes


def guarded_mutation(event):
    return event.kind == "PreToolUse" and event.client == "codex" and event.tool_name == "apply_patch"
