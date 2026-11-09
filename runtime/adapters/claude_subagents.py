"""Claude Code subagent observation source: spawn chains recorded from hooks, verified on collection.

Claude has no queryable thread API and its transcript format is internal, so the chain is recorded from the
hooks the client documents: `PostToolUse(Agent)` carries the request and the spawn result, `SubagentStart` the
agent identity and `SubagentStop` the final message. The chain `tool_use_id -> agentId -> agent_id`, bound by
`prompt_id`, is private, bounded and immutable per field. Hooks fail open, so an absent, partial or conflicting
chain is never evidence: collection refuses it and the review or worker stays unverified.
"""
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from runtime import state
from runtime.startup import StartupError, json_object


MECHANISM = "claude-code-subagent-hook"
MAX_AGENTS = 32
MAX_REPORT = 64 * 1024
MAX_FIELD = 4096
MAX_FILE = 4 * 1024 * 1024
LOCK_TIMEOUT = 2.0  # well inside the 5 s hook budget
MARKER = re.compile(r"(?:worker|review)_[A-Za-z0-9_]{1,56}")
REPORT_FIELDS = {"version", "attempt_id", "assignment_digest", "candidate_digest", "coverage", "findings", "limitations"}


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value, field, limit=MAX_FIELD, optional=False):
    if value is None and optional:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(not char.isprintable() for char in value)):
        raise StartupError(f"Invalid Claude Code subagent {field}")
    return value


def _path(scope, session):
    _text(session, "session")
    if scope is None or scope.runtime is None:
        raise StartupError("Subagent observation needs an enrolled runtime scope")
    token = _sha(json.dumps([state._scope_id(scope), "claude-code", session]))
    return scope.runtime.state_dir / ("claude-subagents-" + token + ".json")


def _load(path):
    if not os.path.lexists(path):
        return None
    state._private(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise StartupError("Subagent observation file exceeds its bound")
    value = json_object(raw, limit=MAX_FILE)
    if value.get("version") != 1 or not isinstance(value.get("agents"), dict):
        raise StartupError("Unsupported subagent observation file")
    return value


def _store(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(raw) > MAX_FILE:
        raise StartupError("Subagent observation file exceeds its bound")
    temporary = path.parent / (uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _set(entry, section, fields):
    """Fields are immutable once seen: a different later value marks the whole agent conflicted."""
    current = entry.setdefault(section, {})
    for key, value in fields.items():
        if key in current and current[key] != value:
            entry["conflict"] = True
        else:
            current.setdefault(key, value)


def _lock(descriptor):
    """Wait briefly for the spool lock; parallel subagent hooks are separate processes and do collide."""
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)


def _marker(prompt):
    first = prompt.strip().splitlines()[0].strip() if prompt.strip() else ""
    return first if MARKER.fullmatch(first) else None


def record(scope, payload):
    """Record one documented hook event.

    Returns True when the event was recorded, and False when it is unrelated or the spool stayed locked past
    the timeout. A dropped event leaves its chain partial, which is never evidence. The spool is private to
    the current user but writable by any process of that user, including a hostile subagent with a shell, so
    it is integrity-checked workflow state, not a security boundary; entries are not pruned.
    """
    event, tool = payload.get("hook_event_name"), payload.get("tool_name")
    if event == "PostToolUse" and tool == "Agent":
        kind = "spawn"
    elif event in {"SubagentStart", "SubagentStop"}:
        kind = event
    else:
        return False
    session = _text(payload.get("session_id"), "session")
    prompt_id = _text(payload.get("prompt_id"), "prompt", optional=True)
    path = _path(scope, session)
    lock = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        state._private(path.with_suffix(".lock"))
        if not _lock(lock):
            return False
        data = _load(path) or dict(version=1, agents={})
        if kind == "spawn":
            response = payload.get("tool_response")
            request = payload.get("tool_input")
            if not isinstance(response, dict) or not isinstance(request, dict):
                raise StartupError("Malformed Agent spawn observation")
            agent = _text(response.get("agentId"), "agent identity", 256)
            prompt = request.get("prompt") if isinstance(request.get("prompt"), str) else ""
            fields = dict(tool_use_id=_text(payload.get("tool_use_id"), "tool call", 256), prompt_id=prompt_id,
                          subagent_type=_text(request.get("subagent_type"), "type", 256, True),
                          requested_model=_text(request.get("model"), "requested model", 256, True),
                          resolved_model=_text(response.get("resolvedModel"), "resolved model", 256, True),
                          prompt_sha256=_sha(prompt), marker=_marker(prompt), is_async=response.get("isAsync") is True)
            section = "spawn"
        else:
            agent = _text(payload.get("agent_id"), "agent identity", 256)
            fields = dict(agent_type=_text(payload.get("agent_type"), "type", 256, True), prompt_id=prompt_id)
            section = "start"
            if kind == "SubagentStop":
                message = payload.get("last_assistant_message")
                if not isinstance(message, str):
                    raise StartupError("Malformed subagent final message")
                transcript = payload.get("agent_transcript_path")
                fields.update(message_sha256=_sha(message), message=message[:MAX_REPORT],
                              message_complete=len(message.encode("utf-8")) <= MAX_REPORT,
                              transcript=transcript if isinstance(transcript, str) and len(transcript) <= MAX_FIELD else None)
                section = "stop"
        entry = data["agents"].get(agent)
        if entry is None:
            if len(data["agents"]) >= MAX_AGENTS:
                data["overflow"] = True
                _store(path, data)
                return False
            entry = data["agents"].setdefault(agent, {})
        _set(entry, section, fields)
        _store(path, data)
        return True
    finally:
        os.close(lock)


def chain(scope, session_id, agent_id):
    """The complete, consistent spawn chain for one agent, or None; a conflict is never evidence."""
    data = _load(_path(scope, session_id))
    entry = (data or {}).get("agents", {}).get(agent_id)
    if entry is None or entry.get("conflict") or not all(section in entry for section in ("spawn", "start", "stop")):
        return None
    spawn, start, stop = entry["spawn"], entry["start"], entry["stop"]
    if (spawn["prompt_id"] is None or not spawn["prompt_id"] == start["prompt_id"] == stop["prompt_id"]
            or start["agent_type"] != stop["agent_type"]
            or (spawn["subagent_type"] is not None and start["agent_type"] != spawn["subagent_type"])
            or not stop["message_complete"]):
        return None
    return dict(session_id=session_id, agent_id=agent_id, spawn=spawn, start=start, stop=stop)


def agent_for_spawn(scope, session_id, prompt_id, tool_use_id):
    """The one agent a given Agent tool call spawned, or None when the spool does not show exactly one."""
    agents = (_load(_path(scope, session_id)) or {}).get("agents", {})
    found = [agent for agent, entry in agents.items() if not entry.get("conflict") and "spawn" in entry
             and entry["spawn"]["tool_use_id"] == tool_use_id and entry["spawn"]["prompt_id"] == prompt_id]
    return found[0] if len(found) == 1 else None


def model_matches(requested, resolved):
    """A requested alias must appear in the model Claude resolved; no alias means the parent's model was inherited."""
    return requested is None or isinstance(resolved, str) and requested.lower() in resolved.lower()


def _chain_for(scope, session_id, agent_id, marker):
    verified = chain(scope, session_id, agent_id)
    if verified is None:
        raise StartupError("Subagent observation is absent, partial, conflicting or inconsistent")
    if not isinstance(marker, str) or not MARKER.fullmatch(marker) or verified["spawn"]["marker"] != marker:
        raise StartupError("The observed subagent was not launched with this assignment marker")
    return verified


def observe_worker(scope, *, session_id, agent_id, marker, native_version):
    """Worker receipt facts from a complete chain; Claude has no turn ids, so the agent id stands in."""
    verified = _chain_for(scope, session_id, agent_id, marker)
    spawn, stop = verified["spawn"], verified["stop"]
    return dict(version=1, mechanism=MECHANISM, native_version=_text(native_version, "native version", 256),
                parent_session_id=session_id, parent_turn_id=spawn["prompt_id"], spawn_item_id=spawn["tool_use_id"],
                worker_id=agent_id, worker_turn_id=agent_id, result_item_id=stop["message_sha256"],
                result_digest=stop["message_sha256"], spawn_observed_model=spawn["resolved_model"],
                spawn_requested_model=spawn["requested_model"], spawn_observed_effort=None, task_name=marker,
                agent_path=None, turn_status="completed")


def collect_review(scope, *, session_id, agent_id, marker, attempt_id, assignment_digest, candidate_digest,
                   coverage, native_version, requested_model, requested_effort):
    """The receipt and report contract `collect_native_review` returns, from a complete recorded chain."""
    verified = _chain_for(scope, session_id, agent_id, marker)
    spawn, stop = verified["spawn"], verified["stop"]
    if spawn["requested_model"] != requested_model or not model_matches(requested_model, spawn["resolved_model"]):
        raise StartupError("The reviewer was spawned with a different model than the prepared assignment")
    try:
        report = json_object(stop["message"].strip().encode("utf-8"), limit=MAX_REPORT)
    except (StartupError, ValueError):
        raise StartupError("The reviewer's final message is not the required JSON report") from None
    if (set(report) != REPORT_FIELDS or type(report.get("version")) is not int or report["version"] != 1
            or report.get("attempt_id") != attempt_id or report.get("assignment_digest") != assignment_digest
            or report.get("candidate_digest") != candidate_digest or report.get("coverage") != coverage
            or not isinstance(report.get("findings"), list) or len(report["findings"]) > 256
            or not isinstance(report.get("limitations"), list) or len(report["limitations"]) > 64
            or any(not isinstance(item, str) or len(item) > 4096 for item in report["limitations"])):
        raise StartupError("The reviewer's report does not match this review assignment")
    receipt = dict(version=1, mechanism=MECHANISM, native_version=_text(native_version, "native version", 256),
                   task_name=marker, agent_path=None, parent_session_id=session_id, parent_turn_id=spawn["prompt_id"],
                   spawn_item_id=spawn["tool_use_id"], reviewer_id=agent_id, review_turn_id=agent_id,
                   report_item_id=stop["message_sha256"], assignment_digest=assignment_digest,
                   candidate_digest=candidate_digest, report_digest=stop["message_sha256"],
                   requested_model=requested_model, requested_effort=requested_effort)
    return dict(receipt=receipt, report=report)
