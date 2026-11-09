"""Codex hook boundaries; no client settings are changed."""

import hashlib
import json
import os
import re
from contextlib import contextmanager
import shlex
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

from runtime.adapters import session_start
from runtime.adapters.session_start import diagnostic, render
from runtime.engine import HookResult, RuntimeEvent
from runtime.doctor import MAX_OUTPUT, NativeProcess, ObservationError, validate_initialize, version
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


def _tool_fingerprint(relevant):
    canonical = json.dumps(relevant, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def patch_input_fingerprint(command):
    return _tool_fingerprint(dict(tool_name="apply_patch", tool_input=dict(command=command)))


def foreground_wait_args(wait, checkout):
    """Derive the sole foreground shell input allowed by a held D6 admission."""
    from runtime.startup import _canonical_path

    if (not isinstance(wait, dict) or set(wait) != {'marker', 'python', 'seconds'}
            or type(wait['seconds']) is not int or not 1 <= wait['seconds'] <= 20):
        raise StartupError('Invalid foreground wait binding')
    marker = _canonical_path(wait['marker'])
    executable = _canonical_path(wait['python'])
    script = '''import os, stat, sys, time
deadline = time.monotonic() + int(sys.argv[2])
while time.monotonic() < deadline:
    try:
        mode = os.lstat(sys.argv[1]).st_mode
    except FileNotFoundError:
        time.sleep(0.05)
        continue
    if stat.S_ISREG(mode):
        print("D6_READY")
        raise SystemExit(0)
    break
raise SystemExit(1)
'''
    return dict(cmd=shlex.join([str(executable), '-I', '-B', '-S', '-c', script, str(marker), str(wait['seconds'])]),
                workdir=str(_canonical_path(str(checkout))), shell='/bin/sh', login=False, tty=False,
                yield_time_ms=(wait['seconds'] + 5) * 1000)


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
    tool = call = fingerprint = patch_input = None
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
                fingerprint = _tool_fingerprint(relevant)
                tool_input = relevant.get("tool_input")
                if (tool == "apply_patch" and isinstance(tool_input, dict) and set(tool_input) == {"command"}
                        and isinstance(tool_input["command"], str)
                        and (kind == "PreToolUse" or isinstance(relevant.get("tool_response"), str))):
                    patch_input = patch_input_fingerprint(tool_input["command"])
            except (ValueError, TypeError, UnicodeError):
                raise StartupError("Invalid runtime tool observation") from None
    if kind == "Stop" and "stop_hook_active" in payload:
        if type(payload["stop_hook_active"]) is not bool:
            raise StartupError("Invalid codex stop_hook_active")
        active = payload["stop_hook_active"]
    raw_path = payload.get('transcript_path')
    transcript = None
    if (patch_input is not None and isinstance(raw_path, str) and 0 < len(raw_path) <= 4096
            and raw_path.isprintable() and Path(raw_path).is_absolute()):
        transcript = Path(raw_path)
    parent_session = None
    if tool == 'apply_patch' and 'agent_id' in payload:
        # Native child tool hooks carry the root session and the concrete child thread separately.
        agent = native_string(payload, 'agent_id')
        if agent == session:
            raise StartupError('Codex child tool hook must identify a distinct agent')
        parent_session, session = session, agent
    return RuntimeEvent(kind, "codex", session, cwd, turn, call, tool, active, fingerprint, patch_input,
                        transcript, parent_session)


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


def with_output_notice(payload, output):
    """Surface the observed child marker; later outer clipping is not visible here."""
    if (not isinstance(payload, dict) or payload.get("hook_event_name") != "PostToolUse"
            or payload.get("tool_name") != "Bash"):
        return output
    response = payload.get("tool_response")
    if not isinstance(response, str) or not response.startswith("Warning: truncated output (original token count:"):
        return output
    # Stdout can imitate this prefix; emit only constant advisory text.
    notice = ("The tool response contains a truncation marker. Recover any missing instructions or evidence "
              "required for this task before relying on it; retain complete material already received.")
    result = dict(output or {})
    specific = dict(result.get("hookSpecificOutput", {}))
    previous = specific.get("additionalContext", "")
    specific.update(hookEventName="PostToolUse", additionalContext=(previous + "\n" if previous else "") + notice)
    result["hookSpecificOutput"] = specific
    return result


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
def _native_reader(methods, *, on_notification=None, deadline=None, executable=None, native_home=None):
    process = None
    limit = time.monotonic() + REVIEW_TIMEOUT
    deadline = limit if deadline is None else min(limit, deadline)
    if time.monotonic() >= deadline:
        raise ObservationError('timeout')
    try:
        executable = executable if executable is not None else shutil.which("codex")
        if executable is None:
            raise ObservationError("unavailable")
        observed_version = version(executable, deadline)
        process = NativeProcess([executable, "app-server", "--listen", "stdio://"], deadline,
                                on_notification=on_notification)
        sequence = 0

        def request(method, params):
            nonlocal sequence
            if method not in methods:
                raise ObservationError("unsupported_method")
            sequence += 1
            if method == "thread/items/read":
                return process.request(sequence, method, params, response_limit=MAX_OUTPUT)
            return process.request(sequence, method, params)

        initialized = request("initialize", {"clientInfo": {"name": "vibe-workflow-native-observer", "version": "1"},
                                              "capabilities": {"experimentalApi": True}})
        validate_initialize(initialized)
        if native_home is not None and (initialized['codexHome'] != native_home or process.launch_codex_home != native_home):
            raise ObservationError('native_home_mismatch')
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
    if not isinstance(spawn, dict) or spawn.get("id") != spawn_item_id:
        raise ObservationError("spawn_unverified")
    if spawn.get("type") == "subAgentActivity":
        if spawn.get("kind") != "started":
            raise ObservationError("spawn_unverified")
        child = _review_id(spawn.get("agentThreadId"))
        agent_path = spawn.get("agentPath")
        if (child == parent or not isinstance(agent_path, str) or len(agent_path) > 4096
                or not agent_path.startswith("/") or agent_path.rsplit("/", 1)[-1] != task_name):
            raise ObservationError("child_mismatch")
        mechanism = "codex-native-agent-path"
    elif spawn.get("type") == "collabAgentToolCall":
        receivers = spawn.get("receiverThreadIds")
        if (spawn.get("tool") != "spawnAgent" or spawn.get("status") != "completed"
                or spawn.get("senderThreadId") != parent or not isinstance(receivers, list) or len(receivers) != 1):
            raise ObservationError("spawn_unverified")
        child = _review_id(receivers[0])
        if child == parent or not isinstance(spawn.get("prompt"), str):
            raise ObservationError("child_mismatch")
        from runtime.evidence import _digest
        envelope = json_object(spawn["prompt"].encode("utf-8"), limit=MAX_OUTPUT)
        review = prefix == "review_"
        digest = binding.get("ledger_assignment_digest", binding["assignment_digest"])
        body = envelope.get("ownership" if review else "assignment")
        if (not isinstance(body, dict) or _digest(body) != digest
                or not isinstance(body.get("binding"), dict) or body["binding"].get("task_name") != task_name):
            raise ObservationError("assignment_unverified")
        expected = (dict(assignment=body["request"]["assignment"], ledger_assignment_digest=digest,
            report_contract=body["report_contract"] | dict(attempt_id=attempt_id,
                candidate_digest=binding["candidate_digest"], coverage=body["review"]["coverage"]), ownership=body)
            if review else dict(assignment_digest=digest, assignment=body))
        if _digest(envelope) != _digest(expected):
            raise ObservationError("assignment_unverified")
        mechanism, agent_path = "codex-native-collab-call", None
    else:
        raise ObservationError("spawn_unverified")
    thread = _native_metadata(scope, request, child, observed_version)
    source = thread.get("source")
    subagent = source.get("subAgent") if isinstance(source, dict) else None
    origin = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    if (thread.get("parentThreadId") != parent or not isinstance(origin, dict)
            or origin.get("parent_thread_id") != parent):
        raise ObservationError("child_mismatch")
    if mechanism == "codex-native-agent-path":
        if origin.get("agent_path") != agent_path:
            raise ObservationError("child_mismatch")
    else:
        agent_path = origin.get("agent_path")
        if agent_path is not None and (not isinstance(agent_path, str) or not agent_path.startswith("/")
                or len(agent_path) > 4096 or any(not char.isprintable() for char in agent_path)):
            raise ObservationError("child_mismatch")
    return parent_thread, spawn, thread, agent_path, mechanism


def _native_final(request, child, turn, *, projected_tools=None, wait_args=None):
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
            if projected_tools is not None:
                kind = item.get('type')
                if kind in {'fileChange', 'functionCallOutput'}:
                    if (kind == 'fileChange' and item.get('status') not in {'completed', 'failed', 'declined'}
                            or kind == 'functionCallOutput' and item.get('name') != 'apply_patch'):
                        raise ObservationError('cooperative_projection_unverified')
                    tool = 'apply_patch'
                elif kind == 'commandExecution' and wait_args is not None:
                    if (item.get('status') != 'completed' or type(item.get('exitCode')) is not int or item['exitCode'] != 0
                            or item.get('source') != 'unifiedExecStartup' or item.get('aggregatedOutput') != 'D6_READY\n'
                            or item.get('cwd') != wait_args['workdir']):
                        raise ObservationError('cooperative_projection_unverified')
                    tool = 'exec_command'
                elif kind not in {'userMessage', 'agentMessage', 'reasoning', 'hookPrompt'} or item.get('delivery') is not None:
                    raise ObservationError('cooperative_projection_unverified')
                else:
                    tool = None
                if tool is not None:
                    call_id = _review_id(item.get('id'))
                    if call_id in projected_tools and (tool != 'apply_patch' or projected_tools[call_id] != tool):
                        raise ObservationError('cooperative_projection_unverified')
                    projected_tools[call_id] = tool
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
            parent_thread, spawn, thread, agent_path, mechanism = _native_chain(scope, binding, attempt_id, "review_",
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
            return {"receipt": {"version": 1, "mechanism": mechanism,
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


def guarded_child_lineage(event, *, deadline):
    """Read the CLI child hook transcript. Settlement corroborates the parent spawn separately."""
    from runtime.onboarding import _read_file
    from runtime.startup import CANONICAL_MAX_ROWS

    try:
        if event.client != 'codex' or event.kind != 'PreToolUse' or event.tool_name != 'apply_patch':
            raise ObservationError('guarded_child_event_unverified')
        child = _review_id(event.session_id)
        turn = _review_id(event.turn_id)
        cwd = str(event.cwd)
        home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').resolve(strict=True)
        path = event.transcript_path
        if (time.monotonic() >= deadline or not path.is_relative_to(home / 'sessions')
                or path.resolve(strict=True) != path or not path.name.startswith('rollout-')
                or not path.name.endswith(f'-{child}.jsonl')):
            raise ObservationError('guarded_rollout_unverified')
        raw = _read_file(path, MAX_OUTPUT, private=True)
        lines = raw.splitlines()
        if not lines or not raw.endswith(b'\n') or len(lines) > CANONICAL_MAX_ROWS:
            raise ObservationError('guarded_rollout_unverified')
        first, *rows = [json_object(line) for line in lines]
        metadata = first['payload']
        if (first['type'] != 'session_meta' or metadata['id'] != child
                or any(row['type'] == 'session_meta' for row in rows)):
            raise ObservationError('guarded_rollout_unverified')
        origin = metadata['source']['subagent']['thread_spawn']
        parent = _review_id(origin['parent_thread_id'])
        agent_path = _review_id(origin['agent_path'])
        task = re.fullmatch(r'/root/(worker_[0-9a-f]{32})', agent_path)
        version = _review_id(metadata['cli_version'])
        if (parent == child or not task or type(origin['depth']) is not int or origin['depth'] != 1
                or metadata['cwd'] != cwd or metadata['originator'] != 'codex_exec'
                or metadata['agent_path'] != agent_path):
            raise ObservationError('guarded_child_source_unverified')
        events = [row['payload'] for row in rows if row['type'] == 'event_msg']
        start, = [item for item in events if item['type'] in ('task_started', 'turn_started')]
        attribution = start['turn_attribution']
        parent_turn = _review_id(attribution['parent_turn_id'])
        if (start['type'] != 'task_started' or start['turn_id'] != turn or attribution['turn_id'] != turn
                or attribution['turn_trigger'] != 'exec' or attribution['root_turn_id'] != parent_turn
                or attribution['initiating_agent_path'] != '/root'
                or any(item['type'] in ('task_complete', 'turn_complete', 'turn_aborted') for item in events)):
            raise ObservationError('guarded_child_turn_unverified')
        context, = [row['payload'] for row in rows if row['type'] == 'turn_context']
        model, effort = _review_id(context['model']), _review_id(context['effort'])
        if (context['turn_id'] != turn or context['cwd'] != cwd or context['multi_agent_version'] != 'v2'
                or time.monotonic() >= deadline):
            raise ObservationError('guarded_child_turn_unverified')
        return dict(parent_session_id=parent, task_name=task[1], native_version=version, model=model, effort=effort)
    except (ObservationError, OSError, StartupError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise StartupError('Guarded native child lineage is unavailable') from None


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


def observe_native_worker(scope, attempt, parent_turn_id, spawn_item_id, *, cooperative=False):
    """Bind one prepared assignment to native history without claiming writer shutdown."""
    return _observe_native_assignment(scope, attempt, parent_turn_id, spawn_item_id, review=False, cooperative=cooperative)


def _visibility_spawn_task(request, parent, child, binding):
    """Correlate the original declaration when native history omits agent_path."""
    turn = _review_id(binding['parent_turn_id'])
    item_id = _review_id(binding['spawn_item_id'])
    rows = request('thread/items/read', {'threadId': parent, 'turnId': turn, 'itemIds': [item_id]}).get('data')
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get('turnId') != turn):
        raise ObservationError('spawn_unverified')
    item = rows[0].get('item')
    if (not isinstance(item, dict) or item.get('id') != item_id or item.get('type') != 'collabAgentToolCall'
            or item.get('tool') != 'spawnAgent' or item.get('status') != 'completed'
            or item.get('senderThreadId') != parent or item.get('receiverThreadIds') != [child]):
        raise ObservationError('spawn_unverified')
    prompt = item.get('prompt')
    if not isinstance(prompt, str) or len(prompt.encode('utf-8')) > MAX_OUTPUT:
        raise ObservationError('assignment_unverified')
    if 'ledger_assignment_digest' in binding:
        from runtime.evidence import _digest
        envelope = json_object(prompt.encode('utf-8'), limit=MAX_OUTPUT)
        if 'ownership' in envelope:
            body, digest = envelope['ownership'], envelope.get('ledger_assignment_digest')
        else:
            body, digest = envelope.get('assignment'), envelope.get('assignment_digest')
        if (not isinstance(body, dict) or digest != binding['ledger_assignment_digest'] or _digest(body) != digest
                or body['binding']['task_name'] != binding['task_name']
                or body['request']['visibility'] != binding['visibility']):
            raise ObservationError('assignment_unverified')
    elif prompt.partition('\n')[0] != binding['prompt_marker']:
        raise ObservationError('assignment_unverified')
    return _review_id(binding['task_name'])


def _visibility_model_name(request, model):
    if model is None:
        return None
    try:
        cursor = None
        for _ in range(4):
            params = {"limit": 100, "includeHidden": True}
            if cursor is not None:
                params["cursor"] = cursor
            page = request("model/list", params)
            rows = page.get("data")
            if not isinstance(rows, list) or len(rows) > 100:
                return None
            names = [row.get("displayName") for row in rows
                     if isinstance(row, dict) and row.get("model") == model]
            if len(names) == 1:
                name = _review_id(names[0])
                # The bundled catalog also spells this display name GPT-6.1-Sol.
                return "GPT-6.1 Sol" if model == "gpt-6.1-sol" and name == "GPT-6.1-Sol" else name
            cursor = page.get("nextCursor")
            if names or not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
                break
    except (ObservationError, StartupError, OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        pass
    return None


def observe_worker_visibility(checkout, parent_session_id, worker_id, *, spawn_binding=None, deadline=None,
                              executable=None, resolve_hook_parent=False):
    """Read actual native lineage for display only; no runtime enrollment or stop proof."""
    import uuid
    try:
        for identity in (parent_session_id, worker_id):
            if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity:
                raise ObservationError('identity_unverified')
        if worker_id == parent_session_id:
            raise ObservationError('child_mismatch')
        checkout = Path(checkout)
        if not checkout.is_absolute() or checkout.resolve(strict=True) != checkout or not checkout.is_dir():
            raise ObservationError('checkout_unverified')
        methods = {'initialize', 'thread/read', 'model/list'}
        if spawn_binding is not None:
            methods.add('thread/items/read')
        options = {} if deadline is None else {'deadline': deadline}
        if executable is not None:
            options['executable'] = executable
        with _native_reader(methods, **options) as (observed_version, request):
            def metadata(identity):
                thread = request('thread/read', {'threadId': identity, 'includeTurns': False}).get('thread')
                if not isinstance(thread, dict) or thread.get('id') != identity:
                    raise ObservationError('thread_mismatch')
                cwd = Path(thread['cwd'])
                if (str(cwd) != thread['cwd'] or not cwd.is_absolute()
                        or cwd.resolve(strict=True) != cwd or not cwd.is_dir()):
                    raise ObservationError('checkout_unverified')
                _review_id(thread.get('cliVersion'))
                return thread

            child = metadata(worker_id)
            source = child.get('source')
            subagent = source.get('subAgent') if isinstance(source, dict) else None
            origin = subagent.get('thread_spawn') if isinstance(subagent, dict) else None
            immediate_parent = child.get('parentThreadId')
            if (not isinstance(immediate_parent, str) or str(uuid.UUID(immediate_parent)) != immediate_parent
                    or not isinstance(origin, dict) or origin.get('parent_thread_id') != immediate_parent):
                raise ObservationError('child_mismatch')
            hook_session_id = parent_session_id
            if resolve_hook_parent:
                parent_session_id = immediate_parent
            elif immediate_parent != parent_session_id:
                raise ObservationError('child_mismatch')
            parent = metadata(parent_session_id)
            if str(checkout) not in {parent['cwd'], child['cwd']}:
                raise ObservationError('checkout_unverified')
            root, seen = parent, {worker_id}
            for _ in range(16):
                if root['id'] in seen:
                    raise ObservationError('child_mismatch')
                seen.add(root['id'])
                source = root.get('source')
                if not isinstance(source, dict):
                    surface = 'codex-cli' if source == 'exec' else 'codex-desktop'
                    _native_surface(root, surface, observed_version)
                    break
                subagent = source.get('subAgent')
                origin = subagent.get('thread_spawn') if isinstance(subagent, dict) else None
                ancestor = root.get('parentThreadId')
                if (not isinstance(origin, dict) or not isinstance(ancestor, str)
                        or str(uuid.UUID(ancestor)) != ancestor or origin.get('parent_thread_id') != ancestor):
                    raise ObservationError('child_mismatch')
                root = metadata(ancestor)
            else:
                raise ObservationError('surface_unverified')
            if hook_session_id not in seen:
                raise ObservationError('child_mismatch')
            origin = child['source']['subAgent']['thread_spawn']
            agent_path = origin.get('agent_path')
            task_name = None
            if agent_path is not None:
                if not _review_id(agent_path).startswith('/'):
                    raise ObservationError('child_mismatch')
                task_name = agent_path.rsplit('/', 1)[-1]
            if spawn_binding is not None and agent_path is None:
                if _visibility_spawn_task(request, parent_session_id, worker_id, spawn_binding) != (task_name or spawn_binding['task_name']):
                    raise ObservationError('assignment_unverified')
                task_name = spawn_binding['task_name']
            model = _native_setting(child.get('model'))
            return dict(version=1, parent_session_id=parent_session_id, worker_id=worker_id,
                checkout=str(checkout), parent_checkout=parent['cwd'], worker_checkout=child['cwd'],
                client_surface=surface, native_version=observed_version,
                agent_path=agent_path, task_name=task_name, configured_model=model,
                model_display_name=_visibility_model_name(request, model),
                configured_provider=_native_setting(child.get('modelProvider')),
                execution_model=None, execution_effort=None,
                **({'hook_session_id': hook_session_id} if resolve_hook_parent else {}),
                **({'native_role': origin['agent_role']} if origin.get('agent_role') is not None else {}))
    except (ObservationError, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.TimeoutExpired):
        raise StartupError('Native display identity unavailable; visibility remains unverified') from None


def observe_native_review_assignment(scope, attempt_id, parent_turn_id, spawn_item_id, *, cooperative=False):
    """Observe D6 reviewer lifecycle; this neither parses nor accepts a D5 review report."""
    return _observe_native_assignment(scope, attempt_id, parent_turn_id, spawn_item_id, review=True, cooperative=cooperative)


def _exec_wrapped_input(item, tool):
    if item.get('name') != 'exec' or not isinstance(item.get('input'), str):
        return None
    script = item['input'].removesuffix('\n').removesuffix(';')
    prefix = f'text(await tools.{tool}('
    if not script.startswith(prefix) or not script.endswith('))'):
        return None
    literal = script[len(prefix):-2]
    if tool == 'exec_command':
        return json_object(literal)
    command = json.loads(literal)
    return command if isinstance(command, str) and command.strip() else None


def _exec_wrapped_pairs(raw, data, turn, *, tools):
    """Bind a literal one-tool wrapper to its one native completion inside the call interval."""
    rows = [row for row in data['records'] if row['payload']['type'].endswith(('_call', '_call_output'))]
    pairs, ids = {}, set()
    for call, output in zip(rows, rows[1:]):
        item, result = call['payload'], output['payload']
        if (call['turn_id'] != turn or item['type'] != 'custom_tool_call' or item.get('name') != 'exec'):
            continue
        for tool in tools:
            value = _exec_wrapped_input(item, tool)
            if value is not None:
                break
        else:
            continue
        outer = _review_id(item.get('call_id'))
        parts = result.get('output')
        if (output['turn_id'] != turn or item.get('namespace') not in (None, 'functions')
                or result['type'] != 'custom_tool_call_output' or result.get('call_id') != outer
                or result.get('namespace') not in (None, 'functions') or result.get('name') not in (None, 'exec')
                or not isinstance(parts, list) or len(parts) != 2
                or any(not isinstance(part, dict) or set(part) != {'type', 'text'}
                    or part['type'] != 'input_text' or not isinstance(part['text'], str) for part in parts)
                or not re.fullmatch(r'Script completed\nWall time [0-9]{1,6}(?:\.[0-9]{1,6})? seconds\nOutput:\n',
                    parts[0]['text'])):
            raise ObservationError('cooperative_wrapper_unverified')
        _, call_offset, call_size = call['source']['reference'].rsplit(':', 2)
        source, output_offset, output_size = output['source']['reference'].rsplit(':', 2)
        start = int(call_offset) + int(call_size)
        end = int(output_offset) + int(output_size)
        middle = [json_object(line) for line in raw[start:int(output_offset)].splitlines()]
        completion, = [row for row in middle if row['type'] != 'token_usage_record']
        event = completion['payload']
        if (completion['type'] != 'event_msg' or event.get('type') != 'item_completed'
                or event.get('thread_id') != data['thread_id'] or event.get('turn_id') != turn):
            raise ObservationError('cooperative_wrapper_unverified')
        nested = event['item']
        if not isinstance(nested, dict):
            raise ObservationError('cooperative_wrapper_unverified')
        inner = _review_id(nested.get('id'))
        if inner == outer or {inner, outer} & ids or nested.get('status') != 'completed':
            raise ObservationError('cooperative_wrapper_unverified')
        ids.update((inner, outer))
        payload = parts[1]['text']
        if tool == 'exec_command':
            reply = json_object(payload)
            if (nested.get('type') != 'CommandExecution'
                    or set(reply) - {'chunk_id', 'wall_time_seconds', 'exit_code', 'original_token_count', 'output'}
                    or type(reply.get('exit_code')) is not int or reply['exit_code'] != 0
                    or reply.get('output') != 'D6_READY\n' or nested.get('aggregated_output') != reply['output']):
                raise ObservationError('cooperative_wait_unverified')
            inner_call = dict(type='function_call', call_id=inner, name=tool, arguments=json.dumps(value))
            inner_output = dict(type='function_call_output', call_id=inner, output=reply['output'])
        else:
            if (payload != '{}' or nested.get('type') != 'FileChange' or nested.get('stderr') != ''
                    or not isinstance(nested.get('changes'), dict) or not nested['changes']
                    or not isinstance(nested.get('stdout'), str) or not nested['stdout']):
                raise ObservationError('cooperative_wrapper_unverified')
            inner_call = dict(type='custom_tool_call', call_id=inner, name=tool, input=value)
            inner_output = dict(type='custom_tool_call_output', call_id=inner, name=tool, output=nested['stdout'])
        # Keep proof of both the nested completion and the matching outer result.
        proof = dict(reference=f'{source}:{start}:{end-start}', sha256=hashlib.sha256(raw[start:end]).hexdigest())
        pairs[outer] = (dict(call, payload=inner_call), dict(output, payload=inner_output, source=proof))
    if pairs:
        nested_ids = {call['payload']['call_id'] for call, _ in pairs.values()}
        completed = set()
        for line in raw.splitlines():
            row = json_object(line)
            event = row['payload']
            item = event.get('item')
            if (row['type'] == 'event_msg' and event.get('type') == 'item_completed'
                    and event.get('turn_id') == turn and isinstance(item, dict)
                    and isinstance(item.get('id'), str) and item['id'] in nested_ids):
                if item['id'] in completed:
                    raise ObservationError('cooperative_wrapper_unverified')
                completed.add(item['id'])
    return pairs


def _cooperative_activity(raw, *, path, child, turn, result, review, wait_args=None):
    """Account for one completed rollout under the native trust assumption."""
    from runtime.doctor import native_behavior_records
    from runtime.startup import _canonical_plain
    data = _canonical_plain(native_behavior_records(raw, thread_id=child, source_reference=str(path)))
    if data['turn_ids'] != [turn]:
        raise ObservationError('cooperative_turn_unverified')
    wrapped = {} if review else _exec_wrapped_pairs(raw, data, turn, tools=('exec_command', 'apply_patch'))
    inner_ids = {outer: pair[0]['payload']['call_id'] for outer, pair in wrapped.items()}
    wrapped_ids = set(inner_ids.values())
    calls, outputs = {}, set()
    declared = False
    wait_call = None
    wait_digest = _tool_fingerprint(dict(tool_name='exec_command', tool_input=wait_args)) if wait_args else None
    for record in data['records']:
        item = record['payload']
        kind = item['type']
        if (kind == 'additional_tools' and not declared and not calls and item.get('role') == 'developer'
                and isinstance(item.get('tools'), list) and item.get('delivery') is None):
            declared = True
            continue
        if kind in {'message', 'agent_message', 'reasoning'}:
            if item.get('delivery') is not None:
                raise ObservationError('cooperative_activity_unverified')
            continue
        if review or record['turn_id'] != turn or item.get('namespace') not in (None, 'functions'):
            raise ObservationError('cooperative_activity_unverified')
        pair = wrapped.get(item.get('call_id'))
        if pair is not None:
            index = {'custom_tool_call': 0, 'custom_tool_call_output': 1}.get(kind)
            if index is None:
                raise ObservationError('cooperative_activity_unverified')
            item = pair[index]['payload']
            kind = item['type']
        call_id = _review_id(item.get('call_id'))
        if kind == 'function_call' and item.get('name') == 'exec_command' and wait_args is not None and not calls:
            arguments = item.get('arguments')
            if not isinstance(arguments, str) or _tool_fingerprint(dict(tool_name='exec_command',
                    tool_input=json_object(arguments))) != wait_digest:
                raise ObservationError('cooperative_wait_unverified')
            wait_call = call_id
            calls[call_id] = dict(id=call_id, tool='exec_command', input_digest=wait_digest)
        elif kind == 'function_call_output' and call_id == wait_call and call_id not in outputs:
            output = item.get('output')
            # The pinned direct tool output has no session line when it returns synchronously.
            if pair is None and (not isinstance(output, str) or re.fullmatch(
                    r'(?:Chunk ID: [0-9a-f]+\n)?Wall time: [0-9]+\.[0-9]{4} seconds\n'
                    r'Process exited with code 0\n(?:Original token count: [0-9]+\n)?Output:\nD6_READY\n',
                    output) is None):
                raise ObservationError('cooperative_wait_unverified')
            outputs.add(call_id)
        elif (kind == 'custom_tool_call' and item.get('name') == 'apply_patch' and call_id not in calls
                and (wait_args is None or wait_call in outputs)):
            command = item.get('input')
            if not isinstance(command, str) or not command.strip():
                raise ObservationError('cooperative_activity_unverified')
            calls[call_id] = dict(id=call_id, tool='apply_patch', input_digest=patch_input_fingerprint(command))
        elif (kind == 'custom_tool_call_output' and item.get('name') in (None, 'apply_patch')
                and call_id in calls and call_id != wait_call and call_id not in outputs):
            output = item.get('output')
            if not (isinstance(output, str) or isinstance(output, list) and output and all(
                    isinstance(part, dict) and part.get('type') == 'input_text' and isinstance(part.get('text'), str)
                    for part in output)):
                raise ObservationError('cooperative_activity_unverified')
            outputs.add(call_id)
        else:
            raise ObservationError('cooperative_activity_unverified')
    if outputs != calls.keys() or wait_args is not None and wait_call is None:
        raise ObservationError('cooperative_activity_unverified')

    phase = 'before'
    initial_delivery = set()
    wait_started = False
    wait_completed = False
    tools_started = False
    settings_seen = False
    for line in raw.splitlines():
        row = json_object(line)
        kind, event = row['type'], row['payload']
        if kind in {'session_meta', 'turn_context', 'token_usage_record', 'world_state'}:
            continue
        if kind in {'inter_agent_communication', 'inter_agent_communication_metadata'}:
            initial_trigger = (kind == 'inter_agent_communication_metadata' and phase == 'active'
                and settings_seen and not tools_started and event.keys() == {'trigger_turn'}
                and event['trigger_turn'] is True)
            if kind in initial_delivery or (phase != 'before' and not initial_trigger):
                raise ObservationError('cooperative_activity_unverified')
            initial_delivery.add(kind)
            continue
        if kind == 'response_item' and (phase == 'active' or phase == 'before'
                and event.get('type') in {'message', 'agent_message', 'reasoning', 'additional_tools'}):
            if event['type'] in {'function_call', 'custom_tool_call'}:
                tools_started = True
                call_id = event.get('call_id')
                if wait_call is not None and inner_ids.get(call_id, call_id) == wait_call:
                    wait_started = True
                elif wait_call is not None and event['type'] == 'custom_tool_call' and not wait_completed:
                    raise ObservationError('cooperative_wait_unverified')
            continue
        if kind != 'event_msg':
            raise ObservationError('cooperative_activity_unverified')
        event_type = event.get('type')
        if event_type in {'task_started', 'turn_started'} and phase == 'before' and event.get('turn_id') == turn:
            phase = 'active'
        elif event_type in {'task_complete', 'turn_complete'} and phase == 'active' and event.get('turn_id') == turn:
            if (event.get('last_agent_message') != result or event.get('error') is not None
                    or event.get('will_retry', False) is not False):
                raise ObservationError('cooperative_turn_unverified')
            phase = 'after'
        elif event_type == 'token_count':
            continue
        elif (event_type == 'thread_settings_applied' and not settings_seen and not tools_started
                and (phase == 'before' or phase == 'active' and event.get('thread_id') == child
                     and isinstance(event.get('thread_settings'), dict))):
            settings_seen = True
        elif phase == 'active' and event_type in {'user_message', 'agent_message', 'agent_reasoning', 'agent_reasoning_raw_content'}:
            continue
        elif (phase == 'active' and event_type == 'patch_apply_end' and event.get('turn_id') == turn
                and event.get('call_id') in outputs and event.get('call_id') not in wrapped_ids):
            continue
        elif phase == 'active' and event_type == 'item_completed':
            item = event.get('item')
            if event.get('thread_id') != child or event.get('turn_id') != turn or not isinstance(item, dict):
                raise ObservationError('cooperative_activity_unverified')
            if item.get('type') == 'CommandExecution' and wait_args is not None:
                if (not wait_started or wait_completed or item.get('id') != wait_call or item.get('status') != 'completed'
                        or type(item.get('exit_code')) is not int or item['exit_code'] != 0
                        or item.get('source') != 'unified_exec_startup' or item.get('aggregated_output') != 'D6_READY\n'
                        or item.get('interaction_input') is not None
                        or item.get('cwd') != Path(wait_args['workdir']).as_uri()
                        or item.get('command') != [wait_args['shell'], '-c', wait_args['cmd']]):
                    raise ObservationError('cooperative_wait_unverified')
                wait_completed = True
            elif not (item.get('type') in {'UserMessage', 'AgentMessage', 'Reasoning', 'HookPrompt'}
                        and item.get('delivery') is None or item.get('type') in {'FileChange', 'FunctionCallOutput'}
                        and item.get('id') in outputs and item.get('id') != wait_call):
                raise ObservationError('cooperative_activity_unverified')
        else:
            raise ObservationError('cooperative_activity_unverified')
    if phase != 'after' or wait_args is not None and not wait_completed:
        raise ObservationError('cooperative_turn_unverified')
    return dict(source_digest=hashlib.sha256(raw).hexdigest(), calls=list(calls.values()))


def _observe_native_assignment(scope, attempt, parent_turn_id, spawn_item_id, *, review, cooperative):
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
        deadline = time.monotonic() + REVIEW_TIMEOUT
        native_home = str(Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').resolve()) if cooperative else None
        with _native_reader(REVIEW_METHODS, deadline=deadline, native_home=native_home) as (observed_version, request):
            parent, spawn, thread, agent_path, mechanism = _native_chain(scope, binding, attempt, "review_" if review else "worker_",
                parent_turn_id, spawn_item_id, request, observed_version)
            originator = _native_surface(parent, binding.get("client_surface"), observed_version)
            child = thread["id"]

            def latest():
                page = request("thread/turns/list", {"threadId": child, "limit": 1,
                    "sortDirection": "desc", "itemsView": "notLoaded"})
                turns = page.get("data")
                if not isinstance(turns, list) or len(turns) > 1:
                    raise ObservationError("malformed_history")
                if cooperative and page.get('nextCursor') is not None:
                    raise ObservationError('cooperative_turn_unverified')
                if not turns:
                    return None, "unknown", None, None
                turn = turns[0]
                if (not isinstance(turn, dict) or turn.get("status") not in {"completed", "inProgress", "interrupted", "failed"}
                        or (turn["status"] == "completed" and turn.get("error") is not None)):
                    raise ObservationError("malformed_history")
                if cooperative and turn.get('willRetry') is not None and turn['willRetry'] is not False:
                    raise ObservationError('cooperative_turn_unverified')
                return _review_id(turn.get("id")), turn["status"], *_native_turn_failure(turn)

            turn, status, failure_code, will_retry = latest()
            projected_tools = {} if cooperative else None
            wait = binding.get('foreground_wait') if cooperative else None
            if review and wait is not None:
                raise ObservationError('cooperative_wait_unverified')
            wait_args = foreground_wait_args(wait, scope.repository_root) if wait is not None else None
            item_id, result = _native_final(request, child, turn, projected_tools=projected_tools,
                wait_args=wait_args) if status == "completed" else (None, None)
            if cooperative:
                from runtime.onboarding import _no_links, _read_file
                from runtime.startup import _canonical_path
                if status != 'completed' or time.monotonic() >= deadline:
                    raise ObservationError('cooperative_turn_unverified')
                path = _canonical_path(thread.get('path'))
                if path.suffix != '.jsonl' or not path.is_relative_to(Path(native_home)):
                    raise ObservationError('cooperative_source_unverified')
                _no_links(path)
                info = path.stat()
                physical = dict(device=info.st_dev, inode=info.st_ino)
                raw = _read_file(path, MAX_OUTPUT, expected_binding=physical)
                activity = _cooperative_activity(raw, path=path, child=child, turn=turn, result=result,
                    review=review, wait_args=wait_args)
                observed_tools = {call['id']: call['tool'] for call in activity['calls']}
                if (not projected_tools.items() <= observed_tools.items()
                        or wait_args is not None and 'exec_command' not in projected_tools.values()):
                    raise ObservationError('cooperative_projection_unverified')
            if latest() != (turn, status, failure_code, will_retry) or prepared(scope, attempt) != binding:
                raise ObservationError("assignment_changed")
            if cooperative and (_native_metadata(scope, request, child, observed_version).get('path') != str(path)
                    or _read_file(path, MAX_OUTPUT, expected_binding=physical) != raw or time.monotonic() >= deadline):
                raise ObservationError('cooperative_source_changed')
            receipt = dict(version=2, mechanism=mechanism, attempt_id=attempt,
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
            return dict(receipt=receipt, result=result, **({'activity': activity} if cooperative else {}))
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


NATIVE_READ_ONLY_REJECTION = 'patch rejected: writing is blocked by read-only sandbox; rejected by user approval settings'


def _native_patch_denied(item, output, submitted_patch):
    if item.get('name') == 'apply_patch':
        return item.get('input') == submitted_patch and output == NATIVE_READ_ONLY_REJECTION
    if item.get('name') != 'exec' or not isinstance(item.get('input'), str):
        return False
    script = item['input'].removesuffix('\n')
    prefix, suffix = 'text(await tools.apply_patch(', '));'
    if not script.startswith(prefix) or not script.endswith(suffix):
        return False
    literal = script[len(prefix):-len(suffix)]
    if not literal.startswith('"') or json.loads(literal) != submitted_patch:
        return False
    if not isinstance(output, list) or len(output) != 2:
        return False
    envelope, denial = output
    return (isinstance(envelope, dict) and set(envelope) == {'type', 'text'}
            and envelope['type'] == 'input_text' and isinstance(envelope['text'], str)
            and re.fullmatch(r'Script failed\nWall time [0-9]{1,6}(?:\.[0-9]{1,6})? seconds\nOutput:\n', envelope['text'])
            and denial == {'type': 'input_text', 'text': 'Script error:\n' + NATIVE_READ_ONLY_REJECTION})


def _verify_bootstrap_reads(rows, expected_hashes, patch_call_id):
    if len(expected_hashes) != 3 or len(rows) not in (6, 7):
        raise ValueError
    seen = {patch_call_id}
    for call, result, expected in zip(rows[:6:2], rows[1:6:2], expected_hashes, strict=True):
        item, output = call['payload'], result['payload']
        call_id = _review_id(item.get('call_id'))
        script, parts = item.get('input'), output.get('output')
        if (item['type'] != 'custom_tool_call' or item.get('name') != 'exec'
                or item.get('namespace') not in (None, 'functions') or call_id in seen
                or not isinstance(script, str) or hashlib.sha256(script.encode()).hexdigest() != expected
                or output['type'] != 'custom_tool_call_output' or output.get('call_id') != call_id
                or output.get('name') is not None or not isinstance(parts, list) or len(parts) != 2
                or any(not isinstance(part, dict) or set(part) != {'type', 'text'}
                    or part['type'] != 'input_text' or not isinstance(part['text'], str) for part in parts)
                or not re.fullmatch(r'Script completed\nWall time [0-9]{1,6}(?:\.[0-9]{1,6})? seconds\nOutput:\n',
                    parts[0]['text'])):
            raise ValueError
        seen.add(call_id)
    # The instruction loader can also emit one separate notification on its own call.
    if len(rows) == 7:
        notification = rows[6]['payload']
        if (notification['type'] != 'custom_tool_call_output' or notification.get('name') != 'exec'
                or notification.get('call_id') != call_id or not isinstance(notification.get('output'), str)):
            raise ValueError


def _native_policy_rejection(records, *, submitted_patch, turn_id, startup_read_sha256=()):
    """Bind the submitted patch to native policy denial, not effective input or OS enforcement."""
    from runtime.startup import MAX_INPUT, _canonical_freeze, _canonical_plain
    try:
        data = _canonical_plain(records)
        if (not isinstance(submitted_patch, str) or len(submitted_patch.encode()) > MAX_INPUT
                or not submitted_patch.startswith('*** Begin Patch\n')
                or not submitted_patch.rstrip('\n').endswith('\n*** End Patch')
                or submitted_patch.count('*** Update File: ') != 1
                or not any(line.startswith(('+', '-')) for line in submitted_patch.splitlines())
                or data['turn_ids'] != [turn_id]):
            raise ValueError
        calls = [row for row in data['records'] if row['payload']['type'].endswith(('_call', '_call_output'))]
        if len(calls) < 2 or any(row['turn_id'] != turn_id for row in calls):
            raise ValueError
        call, output = calls[-2:]
        item, result = call['payload'], output['payload']
        call_id = _review_id(item.get('call_id'))
        if startup_read_sha256:
            _verify_bootstrap_reads(calls[:-2], startup_read_sha256, call_id)
        elif len(calls) != 2:
            raise ValueError
        if (item['type'] != 'custom_tool_call' or item.get('namespace') not in (None, 'functions')
                or result['type'] != 'custom_tool_call_output' or result.get('call_id') != call_id
                or not _native_patch_denied(item, result.get('output'), submitted_patch)):
            raise ValueError
        return _canonical_freeze(dict(thread_id=data['thread_id'], turn_id=turn_id, call_id=call_id,
            submitted_patch_sha256=hashlib.sha256(submitted_patch.encode()).hexdigest(),
            sources=[call['source'], output['source']]))
    except (StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ObservationError('native_policy_rejection_unobserved') from None


def _native_managed_tool(records, *, submitted_patch, turn_id, call_id, case_id, child_patch=False, raw=None):
    """Correlate native tool records; post success additionally requires Core-owned effects."""
    from runtime.startup import MAX_INPUT, _canonical_freeze, _canonical_plain
    try:
        data = _canonical_plain(records)
        if (case_id not in {'pre-tool-enforcement', 'post-tool-handling'}
                or not isinstance(submitted_patch, str) or len(submitted_patch.encode()) > MAX_INPUT
                or not submitted_patch.startswith('*** Begin Patch\n')
                or not submitted_patch.rstrip('\n').endswith('\n*** End Patch')
                or submitted_patch.count('*** Update File: ') != 1
                or not any(line.startswith(('+', '-')) for line in submitted_patch.splitlines())
                or turn_id not in data['turn_ids']):
            raise ValueError
        _review_id(call_id)
        calls = [row for row in data['records'] if row['payload'].get('call_id') == call_id
            and row['payload']['type'] in {'custom_tool_call', 'custom_tool_call_output',
                'function_call', 'function_call_output', 'local_shell_call'}]
        if child_patch and raw is not None:
            pairs = _exec_wrapped_pairs(raw, data, turn_id, tools=('apply_patch',))
            matches = [pair for pair in pairs.values() if pair[0]['payload']['call_id'] == call_id]
            if matches:
                if calls or len(matches) != 1:
                    raise ValueError
                calls = matches[0]
        if len(calls) != 2:
            raise ValueError
        call, output = calls
        item, result = call['payload'], output['payload']
        if (call['turn_id'] != turn_id or output['turn_id'] != turn_id
                or item['type'] != 'custom_tool_call' or item.get('name') != 'apply_patch'
                or item.get('namespace') not in (None, 'functions') or item.get('input') != submitted_patch
                or item.get('call_id') != call_id or result['type'] != 'custom_tool_call_output'
                or result.get('call_id') != call_id or result.get('name') not in (None, 'apply_patch')):
            raise ValueError
        text = result.get('output')
        if case_id == 'pre-tool-enforcement':
            reason = 'Patching requires a WORKING editable bound task without applicable blockers'
            if text != f'Command blocked by PreToolUse hook: {reason}. Command: {submitted_patch}':
                raise ValueError
        elif not (isinstance(text, str) or isinstance(text, list) and all(isinstance(part, dict)
                and part.get('type') == 'input_text' and isinstance(part.get('text'), str) for part in text)):
            raise ValueError
        fact = dict(thread_id=data['thread_id'], turn_id=turn_id, call_id=call_id,
            submitted_patch_sha256=hashlib.sha256(submitted_patch.encode()).hexdigest(), sources=[call['source'], output['source']])
        if child_patch:
            if isinstance(text, list) and len(text) == 1:
                text = text[0]['text']
            if case_id != 'post-tool-handling' or not isinstance(text, str):
                raise ValueError
            fact['post_hook_fingerprint'] = _tool_fingerprint(dict(tool_name='apply_patch',
                tool_input=dict(command=submitted_patch), tool_response=text))
        return _canonical_freeze(fact)
    except (StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ObservationError('native_managed_tool_unobserved') from None


def _native_hook_context(records, *, expected_context, turn_id):
    """Observe exact harness-hook context without inventing hook events or source paths."""
    import xml.etree.ElementTree as xml
    from runtime.startup import MAX_INPUT, _canonical_freeze, _canonical_plain
    try:
        if not isinstance(expected_context, str) or not expected_context or len(expected_context.encode()) > MAX_INPUT:
            raise ValueError
        data = _canonical_plain(records)
        found = []
        for row in data['records']:
            item = row['payload']
            if item['type'] != 'message' or item.get('role') != 'user':
                continue
            content = item.get('content')
            passthrough = item.get('internal_chat_message_metadata_passthrough')
            metadata = passthrough.get('content_item_metadata') if isinstance(passthrough, dict) else None
            if not isinstance(content, list) or not isinstance(metadata, list) or len(content) != len(metadata):
                continue
            for index, part in enumerate(content):
                provenance = metadata[index].get('provenance') if isinstance(metadata[index], dict) else None
                if (not isinstance(part, dict) or part.get('type') != 'input_text'
                        or not isinstance(provenance, dict) or provenance.get('type') != 'harness'
                        or provenance.get('hook') is not True):
                    continue
                text = part.get('text')
                if not isinstance(text, str) or '<!' in text or '<?' in text:
                    raise ValueError
                fragment = xml.fromstring(text)
                if (fragment.tag != 'hook_prompt' or set(fragment.attrib) != {'hook_run_id'}
                        or len(fragment) or fragment.tail is not None):
                    raise ValueError
                hook_id = _review_id(fragment.attrib['hook_run_id'])
                if fragment.text == expected_context:
                    if row['turn_id'] != turn_id:
                        raise ValueError
                    message_id = _review_id(item['id']) if item.get('id') is not None else None
                    found.append(dict(thread_id=data['thread_id'], turn_id=row['turn_id'],
                        hook_run_id=hook_id, message_id=message_id, content_index=index,
                        context_sha256=hashlib.sha256(fragment.text.encode()).hexdigest(), sources=[row['source']]))
        if len(found) != 1:
            raise ValueError
        return _canonical_freeze(found[0])
    except (StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, xml.ParseError):
        raise ObservationError('native_hook_context_unobserved') from None


def _setup_executable(process):
    from runtime import onboarding as d3
    path = Path(process.argv[0]).resolve(strict=True)
    d3._no_links(path)
    info = path.stat()
    if not path.is_file():
        raise ObservationError('executable_unavailable')
    binding = dict(device=info.st_dev,inode=info.st_ino,size=info.st_size,mtime_ns=info.st_mtime_ns,ctime_ns=info.st_ctime_ns)
    # Large native binaries retain their actual file binding without exceeding the read cap.
    digest = None
    if info.st_size <= MAX_OUTPUT:
        raw = d3._read_file(path,MAX_OUTPUT,expected_binding=dict(device=info.st_dev,inode=info.st_ino))
        digest = hashlib.sha256(raw).hexdigest()
    return dict(path=str(path),binding=binding,sha256=digest)


def _disabled_inherited_hook_keys(config):
    states = config['hooks']['state']
    if (not isinstance(states, dict) or not 0 < len(states) <= 64 or config != {'hooks': {'state': states}}
            or any(not isinstance(key, str) or not key.strip() or len(key) > 4096 or not key.isprintable()
                   or not isinstance(value, dict) or set(value) != {'enabled'} or value['enabled'] is not False
                   for key, value in states.items())):
        raise ObservationError('setup_hook_override_unverified')
    return sorted(states)


def _setup_hook_override(process, projection, *, metadata=False):
    execution = projection['attempt'].get('native_execution', {})
    argv = process.argv[1:]
    valid_argv = (len(argv) == 5 and argv[:4] == ('app-server', '--listen', 'stdio://', '-c')) if metadata else (
        len(argv) == 8 and argv[:5] == ('exec', '--json', '--sandbox', 'read-only', '-c') and argv[6] == '--')
    if (not valid_argv or execution.get('source_contract') != 'bounded-native-source-v1'
            or projection['target']['disposable'] is not True or execution.get('argv') != list(process.argv)
            or execution.get('cwd') != process.launch_cwd):
        raise ObservationError('setup_hook_override_unverified')
    override = argv[4] if metadata else argv[5]
    if (not override.startswith('hooks.state={') or not override.endswith('}') or not override.isprintable()
            or len(override.encode()) > 16384):
        raise ObservationError('setup_hook_override_unverified')
    return _disabled_inherited_hook_keys(tomllib.loads(override))


def _verify_setup_hook_isolation(package, cwd, listed, layers, disabled):
    from runtime import doctor
    session = [layer['config'] for layer in layers if layer['name']['type'] == 'sessionFlags']
    if len(session) != 1 or _disabled_inherited_hook_keys(session[0]) != disabled:
        raise ObservationError('setup_hook_override_unverified')
    source = package / 'hooks/codex.json'
    definitions = json_object(source.read_bytes())['hooks']
    if doctor.inspect_hooks(listed, SimpleNamespace(repository_root=cwd), definitions, package=package)['status'] != 'passed':
        raise ObservationError('setup_hook_isolation_unverified')
    keys, inherited = set(), set()
    for hook in listed['data'][0]['hooks']:
        key = hook.get('key')
        if not isinstance(key, str) or not key or key in keys:
            raise ObservationError('setup_hook_isolation_unverified')
        keys.add(key)
        if hook['sourcePath'] != str(source):
            if hook.get('enabled') is not False:
                raise ObservationError('setup_hook_isolation_unverified')
            if hook.get('source') == 'user' and hook.get('isManaged') is False:
                inherited.add(key)
    if not set(disabled) <= inherited:
        raise ObservationError('setup_hook_isolation_unverified')


def _setup_partial(process, projection):
    from runtime import doctor, startup
    if not isinstance(process,NativeProcess) or process.launch_cwd != projection['identity']['repository_root']:
        raise ObservationError('setup_launch_mismatch')
    binding = dict(accepted_scope_digest=startup._canonical_digest(projection['identity']),
        attempt_ref=projection['attempt']['reference'],transaction_id=projection['transaction_id'])
    options = process.argv[2:process.argv.index('--')] if '--' in process.argv[2:] else process.argv[2:]
    overrides = any(value in ('-c','--config','-p','--profile','--enable','--disable')
        or value.startswith(('--config=','--profile=','--enable=','--disable='))
        or value.startswith(('-c','-p')) and value not in ('-c','-p') for value in options)
    launch = dict(cwd=process.launch_cwd,codex_home=process.launch_codex_home,configuration_overrides_present=overrides)
    if overrides and projection['attempt'].get('native_execution', {}).get('source_contract') == 'bounded-native-source-v1':
        launch['disabled_inherited_hook_keys'] = _setup_hook_override(process, projection)
    header = startup._canonical_plain(process.cli_header())
    executable = _setup_executable(process)
    thread = dict(id=header['thread_id'],surface='codex-cli',launch=launch,capture_binding=binding)
    executable_source = dict(reference=executable['path']+'#native-executable-binding',
        sha256=startup._canonical_digest(executable))
    return dict(binding=binding,package_root=str(doctor.PACKAGE),
        native=dict(surface='codex-cli',version=None,threads=[thread],executable=executable),
        codex_home=process.launch_codex_home,hooks=None,layers=None,requirements=None,
        observations={},bootstrap=[header['source'],executable_source],
        pending_reason='metadata_and_frontend_behavior_unobserved')


def _setup_witness(content):
    from runtime.doctor import NativeSetupWitness, _NATIVE_WITNESS_ISSUER
    return NativeSetupWitness(content,_issuer=_NATIVE_WITNESS_ISSUER)


def observe_cli_basic(process, attempt):
    """Retain only genuine original CLI facts; metadata and behavior may be pending."""
    from runtime import setup_observation, startup
    projection = startup._canonical_plain(setup_observation.qualification_attempt_projection(
        attempt,operation='observe-cli-basic',case_id='startup-context',process=process))
    try:
        return _setup_witness(_setup_partial(process,projection))
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        raise ObservationError('cli_setup_observation_unavailable') from None


def observe_setup_metadata(process, attempt):
    """Corroborate the retained CLI using only this separately claimed API process."""
    from runtime import doctor, setup_observation, startup
    projection = startup._canonical_plain(setup_observation.qualification_attempt_projection(
        attempt,operation='observe-setup-metadata',case_id='startup-context',process=process))
    if not isinstance(process,NativeProcess):
        raise ObservationError('setup_metadata_source_mismatch')
    original = startup._canonical_plain(setup_observation.setup_capture_projection(attempt,process=process))
    try:
        if (process.identity is not None or process.bytes_read or process.process.stdin is None
                or original['package_root'] != str(doctor.PACKAGE)
                or original['binding']['accepted_scope_digest'] != startup._canonical_digest(projection['identity'])
                or process.launch_cwd != projection['identity']['repository_root']
                or original['codex_home'] is None or process.launch_codex_home != original['codex_home']
                or _setup_executable(process) != original['native']['executable']
                or len(original['native']['threads']) != 1):
            raise ObservationError('setup_metadata_source_mismatch')
        retained = original['native']['threads'][0]
        if (retained['launch']['cwd'] != process.launch_cwd
                or retained['launch']['codex_home'] != process.launch_codex_home):
            raise ObservationError('cli_configuration_source_unverified')
        disabled = retained['launch'].get('disabled_inherited_hook_keys')
        if retained['launch']['configuration_overrides_present']:
            if (not disabled or '#native-observation:' not in projection['attempt']['source_capture']['reference']
                    or _setup_hook_override(process, projection, metadata=True) != disabled):
                raise ObservationError('cli_configuration_source_unverified')
        elif disabled is not None or process.argv[1:] != ('app-server','--listen','stdio://'):
            raise ObservationError('setup_metadata_source_mismatch')
        thread_id = _review_id(retained['id'])
        if projection['attempt']['native_sessions'] != [thread_id]:
            raise ObservationError('setup_metadata_session_mismatch')
        sources = []
        sequence = 0
        def request(method, params, **limits):
            nonlocal sequence
            sequence += 1
            result = process.request(sequence,method,params,**limits)
            sources.append(startup._canonical_plain(process.response_source))
            return result
        initialized = request('initialize',dict(clientInfo=dict(name='vibe-workflow-native-observer',version='1'),
            capabilities=dict(experimentalApi=True)))
        if initialized['codexHome'] != original['codex_home']:
            raise ObservationError('setup_metadata_home_mismatch')
        process.send(dict(method='initialized'))
        thread = request('thread/read',dict(threadId=thread_id,includeTurns=False)).get('thread')
        if (not isinstance(thread,dict) or thread.get('id') != thread_id
                or thread.get('cwd') != projection['identity']['repository_root']):
            raise ObservationError('setup_metadata_thread_mismatch')
        originator = _native_surface(thread,'codex-cli')
        native_version = _review_id(thread.get('cliVersion'))
        listed = request('hooks/list',dict(cwds=[process.launch_cwd]))
        limits = {'response_limit': MAX_OUTPUT} if disabled else {}
        layers = request('config/read',dict(cwd=process.launch_cwd,includeLayers=True),**limits).get('layers')
        requirements = request('configRequirements/read',{}).get('requirements')
        if not isinstance(layers,list):
            raise ObservationError('setup_metadata_layers_unavailable')
        if disabled:
            _verify_setup_hook_isolation(doctor.PACKAGE, process.launch_cwd, listed, layers, disabled)
        probe = setup_observation.setup_probe_projection(attempt, process=process)
        context = setup_observation.setup_context_projection(attempt, process=process)
        native_path = thread.get('path')
        if probe is not None or context is not None:
            if native_path is None:
                raise ObservationError('setup_native_source_unavailable')
            from runtime import onboarding as d3
            path = startup._canonical_path(native_path)
            if not path.is_relative_to(Path(original['codex_home'])):
                raise ObservationError('setup_native_source_mismatch')
            d3._no_links(path)
            info = path.stat()
            physical = dict(device=info.st_dev, inode=info.st_ino)
            if time.monotonic() >= process.deadline:
                raise ObservationError('timeout')
            raw = d3._read_file(path, MAX_OUTPUT, expected_binding=physical)
            records = doctor.native_behavior_records(raw, thread_id=thread_id, source_reference=str(path))
            if probe is not None:
                probe = startup._canonical_plain(probe)
                data = startup._canonical_plain(records)
                if len(data['turn_ids']) != 1:
                    raise ObservationError('setup_native_turn_unobserved')
                rejected = startup._canonical_plain(_native_policy_rejection(records,
                    submitted_patch=probe['patch'], turn_id=data['turn_ids'][0],
                    startup_read_sha256=probe.get('startup_read_sha256', ())))
                readback = startup._canonical_plain(setup_observation.setup_probe_readback(attempt, process=process))
                if readback['before_reference'] != probe['reference']:
                    raise ObservationError('setup_probe_reference_mismatch')
                original['native_safety'] = dict(claim='submitted-write-policy-rejection',
                    evidence=[*rejected['sources'], probe['reference'], readback['reference']])
            if context is not None:
                context = startup._canonical_plain(context)
                if hashlib.sha256(context['text'].encode()).hexdigest() != context['sha256']:
                    raise ObservationError('setup_context_source_mismatch')
                matches = []
                # Startup context may precede the first actual turn; never assign one retroactively.
                for turn_id in (None, *startup._canonical_plain(records)['turn_ids']):
                    try:
                        matches.append(_native_hook_context(records, expected_context=context['text'], turn_id=turn_id))
                    except ObservationError:
                        continue
                if len(matches) != 1:
                    raise ObservationError('setup_context_delivery_unobserved')
                actual = startup._canonical_plain(matches[0])
                original['observations']['startup-context'] = [*actual['sources'], context['reference']]
            if d3._read_file(path, MAX_OUTPUT, expected_binding=physical) != raw:
                raise ObservationError('setup_native_source_changed')
            if time.monotonic() >= process.deadline:
                raise ObservationError('timeout')
            setup_observation.qualification_attempt_projection(attempt,
                operation='observe-setup-metadata', case_id='startup-context', process=process)
        original['binding'] = dict(accepted_scope_digest=startup._canonical_digest(projection['identity']),
            attempt_ref=projection['attempt']['reference'],transaction_id=projection['transaction_id'])
        original['native']['version'] = native_version
        retained.update(originator=originator,version=native_version)
        original.update(hooks=listed,layers=layers,requirements=requirements,
            pending_reason='frontend_behavior_unobserved')
        original['bootstrap'].extend([projection['attempt']['source_capture'],*sources])
        return _setup_witness(original)
    except startup.StartupError:
        raise
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        raise ObservationError('setup_metadata_unavailable') from None


def observe_managed_case(scope, task_ref, attempt_ref, case_id):
    """Observe a parent task hook or the admitted child canary completion."""
    from runtime import delegation, onboarding as d3, setup_observation, startup
    projection = startup._canonical_plain(setup_observation.managed_observation_projection(
        scope, task_ref, attempt_ref, case_id=case_id))
    try:
        if case_id not in {'pre-tool-enforcement', 'post-tool-handling'}:
            raise ObservationError('managed_case_pending')
        binding, selectors, probe = projection['binding'], projection['selectors'], projection['probe']
        if (probe['patch'] is None or probe['before']['probe'] is None
                or binding['role'] not in {'worker', 'review'}):
            raise ObservationError('managed_source_unqualified')
        assignment = projection['attempt']['assignment_id']
        prefix = 'review_' if binding['role'] == 'review' else 'worker_'
        now = time.monotonic()
        remaining = projection['attempt']['limits']['deadline']-time.time()
        deadline = now+min(REVIEW_TIMEOUT, max(0, remaining))
        with _native_reader(REVIEW_METHODS, deadline=deadline) as (observed_version, request):
            parent, _, child, _, _ = _native_chain(scope, binding, assignment, prefix,
                selectors['parent_turn_id'], selectors['spawn_item_id'], request, observed_version)
            if parent['id'] != task_ref['session_id'] or parent['id'] != binding['parent_session_id']:
                raise ObservationError('managed_parent_mismatch')
            stream = parent
            if selectors['stream'] == 'child':
                if child['id'] != binding['worker_id'] or selectors['turn_id'] != binding['worker_turn_id']:
                    raise ObservationError('managed_child_mismatch')
                stream = child
            path = startup._canonical_path(stream.get('path'))
            d3._no_links(path)
            info = path.stat()
            physical = dict(device=info.st_dev, inode=info.st_ino)
            if time.monotonic() >= deadline:
                raise ObservationError('timeout')
            raw = d3._read_file(path, MAX_OUTPUT, expected_binding=physical)
            from runtime.doctor import native_behavior_records
            records = native_behavior_records(raw, thread_id=stream['id'], source_reference=str(path))
            fact = startup._canonical_plain(_native_managed_tool(records, submitted_patch=probe['patch'],
                turn_id=selectors['turn_id'], call_id=selectors['call_id'], case_id=case_id,
                child_patch=selectors['stream'] == 'child', raw=raw))
            after = startup._canonical_plain(setup_observation.managed_observation_readback(
                scope, task_ref, attempt_ref, case_id=case_id))
            before = probe['before']; receipt = after['receipt']
            kind = 'PreToolUse' if case_id == 'pre-tool-enforcement' else 'PostToolUse'
            if (after['identity'] != projection['identity'] or after['attempt_ref'] != startup._canonical_plain(attempt_ref)
                    or after['assignment_digest'] != binding['assignment_digest']
                    or after['task_revision'] != task_ref['expected_revision'] or not isinstance(receipt, dict)
                    or receipt['kind'] != kind or receipt['conflicted'] is not False
                    or type(receipt['rowid']) is not int or receipt['rowid'] <= before['receipt_watermark']):
                raise ObservationError('managed_effects_unobserved')
            startup._canonical_hash(receipt['fingerprint'])
            if selectors['stream'] == 'child':
                pre = delegation._child_patch_pre_fingerprint(binding['assignment_digest'], patch_input_fingerprint(probe['patch']))
                if receipt['fingerprint'] != delegation._child_patch_fingerprint(pre, fact['post_hook_fingerprint']):
                    raise ObservationError('managed_child_receipt_mismatch')
            if case_id == 'pre-tool-enforcement':
                original = before['probe']['before']
                if after['canary'] != original:
                    raise ObservationError('managed_canary_changed')
            else:
                count = after['post_receipts_count']
                if type(count) is not int or count <= 0 or after['generation']-before['generation'] != count:
                    raise ObservationError('managed_generation_unattributed')
            if d3._read_file(path, MAX_OUTPUT, expected_binding=physical) != raw:
                raise ObservationError('managed_native_source_changed')
            if (time.monotonic() >= deadline or projection != startup._canonical_plain(
                    setup_observation.managed_observation_projection(scope, task_ref, attempt_ref, case_id=case_id))
                    or after != startup._canonical_plain(setup_observation.managed_observation_readback(
                        scope, task_ref, attempt_ref, case_id=case_id))):
                raise ObservationError('managed_owner_changed')
        return _setup_witness(dict(binding=dict(accepted_scope_digest=startup._canonical_digest(projection['identity']),
            attempt_ref=startup._canonical_plain(attempt_ref), transaction_id=projection['transaction_id']),
            setup_reference=projection['setup_reference'],
            observations={case_id:[*fact['sources'], probe['reference'], after['reference']]}))
    except startup.StartupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, subprocess.TimeoutExpired):
        raise ObservationError('managed_setup_observation_unavailable') from None
