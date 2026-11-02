"""Codex hook boundaries; no client settings are changed."""

import hashlib
import json
import re
from contextlib import contextmanager
import shutil
import subprocess
import time
from pathlib import Path

from runtime.adapters import session_start
from runtime.adapters.session_start import diagnostic, render
from runtime.engine import HookResult, RuntimeEvent
from runtime.doctor import NativeProcess, ObservationError, validate_initialize, version
from runtime.startup import StartupError, AcceptedScope, json_object


SOURCES = {"startup", "resume", "clear", "compact", "fork"}
RUNTIME_EVENTS = {"PreToolUse", "PostToolUse", "Stop", "Interrupt"}


def normalize(payload):
    return session_start.normalize(payload, "codex", SOURCES)


def native_string(payload, field, optional=False):
    if optional and field not in payload:
        return None
    value = payload.get(field)
    if (not isinstance(value, str) or not value.strip() or len(value) > 4096
            or any(not char.isprintable() for char in value)):
        raise StartupError(f"Invalid codex {field}")
    return value


def normalize_runtime(payload) -> RuntimeEvent:
    if not isinstance(payload, dict):
        raise StartupError("Expected a runtime JSON object")
    kind = payload.get("hook_event_name")
    if not isinstance(kind, str) or kind not in RUNTIME_EVENTS:
        raise StartupError("Unsupported codex runtime event")
    session = native_string(payload, "session_id")
    cwd = Path(native_string(payload, "cwd"))
    if not cwd.is_absolute():
        raise StartupError("Runtime cwd must be absolute")
    turn = native_string(payload, "turn_id", optional=True)
    tool = call = fingerprint = None
    active = None
    if kind in {"PreToolUse", "PostToolUse"}:
        tool = native_string(payload, "tool_name")
        call = native_string(payload, "tool_use_id", optional=True)
        if turn is not None and call is not None:
            relevant = {"tool_name": tool}
            for field in ("tool_input", "tool_response"):
                if field in payload and (field != "tool_response" or kind == "PostToolUse"):
                    relevant[field] = payload[field]
            try:
                canonical = json.dumps(relevant, sort_keys=True, separators=(",", ":"), allow_nan=False)
                fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            except (ValueError, TypeError, UnicodeError):
                raise StartupError("Invalid runtime tool observation") from None
    if kind == "Stop" and "stop_hook_active" in payload:
        if type(payload["stop_hook_active"]) is not bool:
            raise StartupError("Invalid codex stop_hook_active")
        active = payload["stop_hook_active"]
    return RuntimeEvent(kind, "codex", session, cwd, turn, call, tool, active, fingerprint)


def render_runtime(result: HookResult) -> dict | None:
    kind, action = result.event_kind, result.action
    allowed = {"PreToolUse": {"pass", "deny", "context"},
               "PostToolUse": {"pass", "context"}, "Stop": {"pass", "context", "continue"},
               "Interrupt": {"pass", "context"}}
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
        return {"decision": "block", "reason": reason}
    if kind in {"Stop", "Interrupt"}:
        return {"systemMessage": reason}
    return {"hookSpecificOutput": {"hookEventName": kind, "additionalContext": reason}}


def runtime_diagnostic(kind, reason):
    """Errors never block an unvalidated scope or replace a completed tool result."""
    return {"systemMessage": f"Vibe Coding Workflow {kind} runtime unavailable: {reason}. "
            "Execution coverage and workflow readiness remain unverified."}


REVIEW_TIMEOUT = 15
NATIVE_FAILURE_VARIANTS = {"httpConnectionFailed", "responseStreamConnectionFailed",
    "responseStreamDisconnected", "responseTooManyFailedAttempts", "activeTurnNotSteerable"}
REVIEW_METHODS = {"initialize", "thread/read", "thread/items/read", "thread/turns/list", "thread/items/list"}
WORKER_METHODS = REVIEW_METHODS | {"model/list"}


def _review_id(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(not char.isprintable() for char in value)):
        raise StartupError("Invalid native review identifier")
    return value


@contextmanager
def _native_reader(methods):
    process = None
    deadline = time.monotonic() + REVIEW_TIMEOUT
    try:
        executable = shutil.which("codex")
        if executable is None:
            raise ObservationError("unavailable")
        observed_version = version(executable, deadline)
        process = NativeProcess([executable, "app-server", "--listen", "stdio://"], deadline)
        sequence = 0

        def request(method, params):
            nonlocal sequence
            if method not in methods:
                raise ObservationError("unsupported_method")
            sequence += 1
            return process.request(sequence, method, params)

        initialized = request("initialize", {"clientInfo": {"name": "vibe-workflow-native-observer", "version": "1"},
                                              "capabilities": {"experimentalApi": True}})
        validate_initialize(initialized)
        process.send({"method": "initialized"})
        yield observed_version, request
    finally:
        if process is not None:
            process.close()


def _native_metadata(scope, request, identity, observed_version):
    if isinstance(scope, AcceptedScope):
        from runtime.evidence import _canonical_inputs
        _canonical_inputs(scope)
    thread = request("thread/read", {"threadId": identity, "includeTurns": False}).get("thread")
    if (not isinstance(thread, dict) or thread.get("id") != identity
            or thread.get("cwd") != str(scope.repository_root)):
        raise ObservationError("thread_mismatch")
    _review_id(thread.get("cliVersion"))
    return thread


def _native_chain(scope, binding, attempt_id, prefix, parent_turn_id, spawn_item_id, request, observed_version):
    parent = _review_id(binding["parent_session_id"])
    parent_thread = _native_metadata(scope, request, parent, observed_version)
    surface = binding.get("client_surface", "codex-cli" if parent_thread.get("source") == "exec" else "codex-desktop")
    _native_surface(parent_thread, surface)
    selected = request("thread/items/read", {"threadId": parent, "turnId": parent_turn_id,
                                             "itemIds": [spawn_item_id]}).get("data")
    if (not isinstance(selected, list) or len(selected) != 1 or not isinstance(selected[0], dict)
            or selected[0].get("turnId") != parent_turn_id):
        raise ObservationError("spawn_unverified")
    spawn = selected[0].get("item")
    task_name = _review_id(binding["task_name"])
    if task_name != native_task_name(attempt_id, prefix.removesuffix("_"), binding.get("workstream_context_id")):
        raise ObservationError("assignment_marker_invalid")
    if (not isinstance(spawn, dict) or spawn.get("id") != spawn_item_id
            or spawn.get("type") != "subAgentActivity" or spawn.get("kind") != "started"):
        raise ObservationError("spawn_unverified")
    child = _review_id(spawn.get("agentThreadId"))
    agent_path = spawn.get("agentPath")
    if (child == parent or not isinstance(agent_path, str) or len(agent_path) > 4096
            or not agent_path.startswith("/") or agent_path.rsplit("/", 1)[-1] != task_name):
        raise ObservationError("child_mismatch")
    thread = _native_metadata(scope, request, child, observed_version)
    source = thread.get("source")
    subagent = source.get("subAgent") if isinstance(source, dict) else None
    origin = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    if (thread.get("parentThreadId") != parent or not isinstance(origin, dict)
            or origin.get("parent_thread_id") != parent or origin.get("agent_path") != agent_path):
        raise ObservationError("child_mismatch")
    return parent_thread, spawn, thread, agent_path


def _native_final(request, child, turn):
    final, cursor, seen = [], None, set()
    # One item per frame preserves all-history uniqueness within existing byte/time bounds.
    for _ in range(400):
        params = {"threadId": child, "turnId": turn, "limit": 1, "sortDirection": "asc"}
        if cursor is not None:
            params["cursor"] = cursor
        page = request("thread/items/list", params)
        entries = page.get("data")
        if not isinstance(entries, list) or len(entries) > 1:
            raise ObservationError("malformed_history")
        for entry in entries:
            if (not isinstance(entry, dict) or entry.get("turnId") != turn
                    or not isinstance(entry.get("item"), dict)):
                raise ObservationError("malformed_history")
            item = entry["item"]
            if item.get("type") == "agentMessage" and item.get("phase") == "final_answer":
                final.append(item)
        cursor = page.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in seen:
            raise ObservationError("pagination_limit")
        seen.add(cursor)
    else:
        raise ObservationError("pagination_limit")
    if len(final) != 1:
        raise ObservationError("final_unverified")
    item_id, text = _review_id(final[0].get("id")), final[0].get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > 64 * 1024:
        raise ObservationError("report_limit")
    return item_id, text


def collect_native_review(scope, attempt_id, parent_turn_id, spawn_item_id):
    """Fetch provenance and report; never dispatch, resume, message or execute review."""
    from runtime.evidence import prepared_native_review

    for value in (attempt_id, parent_turn_id, spawn_item_id):
        _review_id(value)
    binding = prepared_native_review(scope, attempt_id)
    try:
        with _native_reader(REVIEW_METHODS) as (observed_version, request):
            parent_thread, spawn, thread, agent_path = _native_chain(scope, binding, attempt_id, "review_",
                parent_turn_id, spawn_item_id, request, observed_version)
            parent, child, task_name = binding["parent_session_id"], thread["id"], binding["task_name"]
            requested_model, requested_effort = spawn.get("model"), spawn.get("reasoningEffort")
            for value in (requested_model, requested_effort):
                if value is not None:
                    _review_id(value)

            def latest():
                turns = request("thread/turns/list", {"threadId": child, "limit": 1,
                                                     "sortDirection": "desc", "itemsView": "notLoaded"}).get("data")
                if (not isinstance(turns, list) or len(turns) != 1 or not isinstance(turns[0], dict)
                        or turns[0].get("status") != "completed" or turns[0].get("error") is not None):
                    raise ObservationError("turn_incomplete")
                return _review_id(turns[0].get("id"))

            review_turn = latest()
            report_item, text = _native_final(request, child, review_turn)
            report = json_object(text.encode("utf-8"))
            if (set(report) != {"version", "attempt_id", "assignment_digest", "candidate_digest", "coverage", "findings", "limitations"}
                    or type(report.get("version")) is not int or report["version"] != 1
                    or report.get("attempt_id") != attempt_id
                    or report.get("assignment_digest") != binding["assignment_digest"]
                    or report.get("candidate_digest") != binding["candidate_digest"]
                    or report.get("coverage") != binding["requested_coverage"]
                    or not isinstance(report.get("findings"), list) or len(report["findings"]) > 256
                    or not isinstance(report.get("limitations"), list) or len(report["limitations"]) > 64
                    or any(not isinstance(item, str) or len(item) > 4096 for item in report["limitations"])):
                raise ObservationError("report_mismatch")
            if latest() != review_turn or prepared_native_review(scope, attempt_id) != binding:
                raise ObservationError("review_changed")
            return {"receipt": {"version": 1, "mechanism": "codex-native-agent-path",
                    "task_name": task_name, "agent_path": agent_path,
                    "native_version": _review_id(binding.get("native_version", observed_version)),
                    "current_observer_version": observed_version,
                    "parent_creation_version": parent_thread["cliVersion"], "worker_creation_version": thread["cliVersion"],
                    "parent_session_id": parent,
                    "parent_turn_id": parent_turn_id, "spawn_item_id": spawn_item_id,
                    "reviewer_id": child, "review_turn_id": review_turn, "report_item_id": report_item,
                    "assignment_digest": binding["assignment_digest"], "candidate_digest": binding["candidate_digest"],
                    "report_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "requested_model": requested_model, "requested_effort": requested_effort}, "report": report}
    except (ObservationError, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.TimeoutExpired):
        raise StartupError("Native review observation unavailable; independent review remains unverified") from None


def _native_surface(thread, surface, observer_version=None):
    originator = _review_id(thread.get("originator"))
    if not ((surface == "codex-cli" and thread.get("source") == "exec" and originator == "codex_exec")
            or (surface == "codex-desktop" and thread.get("source") == "vscode" and originator in {"Codex Desktop", "codex_work_desktop"})):
        raise ObservationError("surface_unverified")
    return originator


def _native_setting(value):
    return _review_id(value) if value is not None else None


def discover_native_workers(scope, control_observation):
    """Observe a matching native catalog; supplied controls confer no execution authority."""
    try:
        if (not isinstance(control_observation, dict) or type(control_observation.get("version")) is not int
                or control_observation["version"] != 1
                or control_observation.get("client_surface") not in {"codex-cli", "codex-desktop"}):
            raise ObservationError("surface_unqualified")
        controls = control_observation.get("dispatch_controls")
        if (not isinstance(controls, dict) or set(controls) != {"spawn", "explicit_model", "explicit_effort", "fork_turns_none"}
                or any(type(value) is not bool for value in controls.values())):
            raise ObservationError("controls_unverified")
        parent = _review_id(control_observation.get("parent_session_id"))
        _review_id(control_observation.get("native_version"))
        with _native_reader(WORKER_METHODS) as (observed_version, request):
            thread = _native_metadata(scope, request, parent, observed_version)
            originator = _native_surface(thread, control_observation["client_surface"], observed_version)
            catalog, models, seen, cursor = [], set(), set(), None
            for _ in range(4):
                params = {"limit": 100, "includeHidden": False}
                if cursor is not None:
                    params["cursor"] = cursor
                page = request("model/list", params)
                entries = page.get("data")
                if not isinstance(entries, list) or len(entries) > 100:
                    raise ObservationError("catalog_unverified")
                for item in entries:
                    if not isinstance(item, dict):
                        raise ObservationError("catalog_unverified")
                    model, default = _review_id(item.get("model")), _review_id(item.get("defaultReasoningEffort"))
                    options = item.get("supportedReasoningEfforts")
                    if not isinstance(options, list) or not 1 <= len(options) <= 32:
                        raise ObservationError("catalog_unverified")
                    efforts = [_review_id(option.get("reasoningEffort")) for option in options if isinstance(option, dict)]
                    if (len(efforts) != len(options) or len(set(efforts)) != len(efforts)
                            or default not in efforts or model in models):
                        raise ObservationError("catalog_unverified")
                    models.add(model)
                    catalog.append(dict(model=model, supported_efforts=efforts, default_effort=default))
                cursor = page.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in seen:
                    raise ObservationError("pagination_limit")
                seen.add(cursor)
            else:
                raise ObservationError("pagination_limit")
            capabilities = dict(client_surface=control_observation["client_surface"],
                                originator=originator, parent_model_provider=_native_setting(thread.get("modelProvider")),
                                catalog=catalog, dispatch_controls=dict(controls))
            digest = hashlib.sha256(json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            return dict(version=1, native_version=observed_version, parent_session_id=parent, **capabilities, capability_digest=digest,
                        limitations=["Dispatch controls are coordinator observations; catalog visibility grants no execution authority.",
                                     "Catalog/history observation does not prove worker dispatch."])
    except (ObservationError, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.TimeoutExpired):
        raise StartupError("Native worker discovery unavailable; dispatch capabilities remain unverified") from None


def observe_native_worker(scope, attempt, parent_turn_id, spawn_item_id):
    """Bind one prepared assignment to native history without claiming writer shutdown."""
    return _observe_native_assignment(scope, attempt, parent_turn_id, spawn_item_id, review=False)


def observe_native_review_assignment(scope, attempt_id, parent_turn_id, spawn_item_id):
    """Observe D6 reviewer lifecycle; this neither parses nor accepts a D5 review report."""
    return _observe_native_assignment(scope, attempt_id, parent_turn_id, spawn_item_id, review=True)


def _observe_native_assignment(scope, attempt, parent_turn_id, spawn_item_id, *, review):
    if review:
        from runtime.delegation import prepared_native_review_assignment as prepared
    else:
        from runtime.delegation import prepared_native_assignment as prepared

    for value in (attempt, parent_turn_id, spawn_item_id):
        _review_id(value)
    binding = prepared(scope, attempt)
    try:
        if not isinstance(binding, dict) or binding.get("attempt_id") != attempt:
            raise ObservationError("assignment_unverified")
        if isinstance(scope, AcceptedScope):
            from runtime import state
            if (binding.get("pins") != state._pins(scope)
                    or binding.get("accepted_identity") != state._accepted_identity(scope.accepted_identity)
                    or binding.get("scope_id") != state._scope_id(scope)):
                raise ObservationError("assignment_unverified")
        for field in ("assignment_digest", "candidate_digest"):
            value = binding.get(field)
            if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ObservationError("assignment_unverified")
        requested_model = _native_setting(binding.get("requested_model"))
        requested_effort = _native_setting(binding.get("requested_effort"))
        admitted_version = _review_id(binding.get("native_version"))
        with _native_reader(REVIEW_METHODS) as (observed_version, request):
            parent, spawn, thread, agent_path = _native_chain(scope, binding, attempt, "review_" if review else "worker_",
                parent_turn_id, spawn_item_id, request, observed_version)
            originator = _native_surface(parent, binding.get("client_surface"), observed_version)
            child = thread["id"]

            def latest():
                turns = request("thread/turns/list", {"threadId": child, "limit": 1,
                    "sortDirection": "desc", "itemsView": "notLoaded"}).get("data")
                if not isinstance(turns, list) or len(turns) > 1:
                    raise ObservationError("malformed_history")
                if not turns:
                    return None, "unknown", None, None
                turn = turns[0]
                if (not isinstance(turn, dict) or turn.get("status") not in {"completed", "inProgress", "interrupted", "failed"}
                        or (turn["status"] == "completed" and turn.get("error") is not None)):
                    raise ObservationError("malformed_history")
                return _review_id(turn.get("id")), turn["status"], *_native_turn_failure(turn)

            turn, status, failure_code, will_retry = latest()
            item_id, result = _native_final(request, child, turn) if status == "completed" else (None, None)
            if latest() != (turn, status, failure_code, will_retry) or prepared(scope, attempt) != binding:
                raise ObservationError("assignment_changed")
            receipt = dict(version=2, mechanism="codex-native-agent-path", attempt_id=attempt,
                task_name=binding["task_name"], agent_path=agent_path, native_version=admitted_version,
                current_observer_version=observed_version, parent_creation_version=parent["cliVersion"],
                worker_creation_version=thread["cliVersion"],
                client_surface=binding["client_surface"], originator=originator,
                parent_model_provider=_native_setting(parent.get("modelProvider")),
                worker_model_provider=_native_setting(thread.get("modelProvider")),
                parent_session_id=binding["parent_session_id"], parent_turn_id=parent_turn_id, spawn_item_id=spawn_item_id,
                worker_id=child, worker_turn_id=turn, turn_status=status, result_item_id=item_id,
                failure_code=failure_code, will_retry=will_retry,
                checkout=str(scope.repository_root), assignment_digest=binding["assignment_digest"],
                candidate_digest=binding["candidate_digest"], requested_model=requested_model, requested_effort=requested_effort,
                configured_model=_native_setting(thread.get("model")), configured_effort=_native_setting(thread.get("reasoningEffort")),
                spawn_observed_model=_native_setting(spawn.get("model")), spawn_observed_effort=_native_setting(spawn.get("reasoningEffort")),
                execution_model=None, execution_effort=None, descendants_stopped=False, prompt_delivery="unverified",
                result_digest=hashlib.sha256(result.encode("utf-8")).hexdigest() if result is not None else None)
            return dict(receipt=receipt, result=result)
    except (ObservationError, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.TimeoutExpired):
        label = "review assignment" if review else "worker"
        raise StartupError(f"Native {label} observation unavailable; assignment ownership remains unresolved") from None


def native_task_name(attempt_id, role, context_id=None):
    """Derive a native creation name from immutable assignment identity."""
    attempt_id = _review_id(attempt_id)
    if not isinstance(role, str) or role not in {"worker", "review"}:
        raise StartupError("Invalid native assignment role")
    if context_id is None:
        marker = role + "_" + attempt_id
    else:
        if (not isinstance(context_id, str)
                or re.fullmatch(r"WS[A-Za-z0-9]{1,16}\.[0-9]{1,16}", context_id) is None
                or int(context_id.split(".")[1]) == 0
                or re.fullmatch(r"[a-z0-9_]+", attempt_id) is None):
            raise StartupError("Invalid Workstreams native assignment identity")
        label = "build" if role == "worker" else "review"
        marker = role + "_" + context_id.lower().replace(".", "_") + "_" + label + "_" + attempt_id
        if len(marker) > 64:
            raise StartupError("Workstreams native assignment name exceeds its bound")
    if "/" in marker:
        raise StartupError("Invalid native assignment marker")
    return _review_id(marker)


def _native_turn_failure(turn):
    error = turn.get("error")
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    code = None
    if isinstance(info, str) and info in NATIVE_FAILURE_CODES:
        code = info
    elif isinstance(info, dict) and len(info) == 1:
        variant = next(iter(info))
        if variant in NATIVE_FAILURE_VARIANTS and isinstance(info[variant], dict):
            code = variant
    # Persisted turns currently omit the notification-only willRetry; absence is not false.
    retry = turn.get("willRetry")
    return code, retry if type(retry) is bool else None


NATIVE_FAILURE_CODES = {"contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded",
    "rateLimitExceeded", "flexUnavailable", "serverOverloaded", "cyberPolicy",
    "misalignmentPolicyViolation", "tooManyDenials", "internalServerError", "unauthorized",
    "badRequest", "threadRollbackFailed", "sandboxError", "other"}
