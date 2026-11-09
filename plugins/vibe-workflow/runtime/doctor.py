"""Bounded native transport and observations; diagnostics do not authorize execution."""

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time

from runtime.startup import MAX_INPUT, StartupError, json_object, policy_digest
from scripts.validate import validate


PACKAGE = Path(__file__).resolve().parents[1]
RPC_TIMEOUT = 5
TOTAL_TIMEOUT = 15
MAX_OUTPUT = 512 * 1024
# Historical observations only; support is determined by the live protocol contract.
NATIVE_COMPATIBILITY = {
    ("0.163.0", "0.163.0", "codex-cli", "exec", "codex_exec"),
    ("0.164.0-alpha.11.1", "0.164.0-alpha.11.1", "codex-desktop", "vscode", "Codex Desktop"),
    ("0.165.0", "0.165.0-alpha.1.2", "codex-desktop", "vscode", "Codex Desktop"),
}
KNOWN_VERSIONS = {pair[0] for pair in NATIVE_COMPATIBILITY}
HASH = re.compile(r"sha256:[0-9a-f]{64}(?:/sha256:[0-9a-f]{64})?\Z")
SOURCES = {"system", "user", "project", "mdm", "sessionFlags", "plugin", "cloudRequirements",
           "cloudManagedConfig", "legacyManagedConfigFile", "legacyManagedConfigMdm", "unknown"}
_HOOK_STREAM = ContextVar('hook_stream', default=None)
_NATIVE_WITNESS_ISSUER = object()


class ObservationError(Exception):
    """Fixed status only; child output and OS exception messages stay private."""


@dataclass(frozen=True, init=False, slots=True, repr=False)
class NativeSetupWitness:
    """Native facts only; Core owns semantic assessment and setup acceptance."""

    _content: object
    _issuer: object

    def __init__(self, content, *, _issuer=None):
        if _issuer is not _NATIVE_WITNESS_ISSUER:
            raise ObservationError("unissued_native_witness")
        from runtime.startup import _canonical_freeze
        try:
            raw = json.dumps(content, allow_nan=False).encode()
            frozen = _canonical_freeze(json_object(raw, limit=MAX_OUTPUT))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ObservationError("invalid_native_witness") from None
        object.__setattr__(self, '_content', frozen)
        object.__setattr__(self, '_issuer', _issuer)


def _native_witness_content(witness):
    if type(witness) is not NativeSetupWitness or getattr(witness, '_issuer', None) is not _NATIVE_WITNESS_ISSUER:
        raise ObservationError("unissued_native_witness")
    from runtime.startup import _canonical_plain
    return _canonical_plain(witness._content)


def native_behavior_records(raw, *, thread_id, source_reference):
    """Decode bounded original rollout bytes; retain actual IDs and exact line sources."""
    from runtime.startup import CANONICAL_MAX_ROWS, _canonical_freeze
    try:
        def identifier(value):
            if not isinstance(value, str) or not value.strip() or len(value) > 256 or not value.isprintable():
                raise ValueError
            return value
        identifier(thread_id)
        if (not isinstance(raw, bytes) or not raw or len(raw) > MAX_OUTPUT or not raw.endswith(b'\n')
                or not isinstance(source_reference, str) or not source_reference.isprintable()
                or len(source_reference) > 4096 or not Path(source_reference).is_absolute()
                or '#' in source_reference):
            raise ValueError
        lines = raw.splitlines(keepends=True)
        if len(lines) > CANONICAL_MAX_ROWS:
            raise ValueError
        records, turns, started, completed = [], [], set(), set()
        session = active = None
        offset = 0
        for line in lines:
            if not line.endswith(b'\n') or len(line) > MAX_INPUT:
                raise ValueError
            value = json_object(line)
            if (not {'timestamp', 'type', 'payload'} <= set(value)
                    or set(value) - {'timestamp', 'type', 'payload', 'ordinal', 'metadata'}
                    or not isinstance(value['timestamp'], str) or not value['timestamp']
                    or len(value['timestamp']) > 128 or not isinstance(value['payload'], dict)):
                raise ValueError
            kind, payload = value['type'], value['payload']
            identifier(kind)
            ordinal = value.get('ordinal')
            if ordinal is not None and (type(ordinal) is not int or not 0 <= ordinal < 2**64):
                raise ValueError
            if 'metadata' in value and (kind != 'response_item'
                    or value['metadata'] is not None and not isinstance(value['metadata'], dict)):
                raise ValueError
            source = dict(reference=f'{source_reference}#bytes:{offset}:{len(line)}',
                sha256=hashlib.sha256(line).hexdigest())
            offset += len(line)
            if kind == 'session_meta':
                if session is not None or identifier(payload.get('id')) != thread_id:
                    raise ValueError
                session = thread_id
            elif session is None:
                raise ValueError
            elif kind == 'turn_context' or kind == 'event_msg' and payload.get('type') in {
                    'task_started', 'turn_started', 'task_complete', 'turn_complete'}:
                turn = payload.get('turn_id')
                if turn is None and kind == 'turn_context':
                    continue
                turn = identifier(turn)
                event = payload.get('type')
                if event in {'task_complete', 'turn_complete'}:
                    if active != turn or turn in completed:
                        raise ValueError
                    completed.add(turn)
                    active = None
                else:
                    if active not in (None, turn) or turn in completed:
                        raise ValueError
                    if event in {'task_started', 'turn_started'}:
                        if turn in started:
                            raise ValueError
                        started.add(turn)
                    active = turn
                if turn not in turns:
                    turns.append(turn)
            elif kind == 'response_item':
                identifier(payload.get('type'))
                records.append(dict(turn_id=active, payload=payload, source=source))
        if session != thread_id:
            raise ValueError
        return _canonical_freeze(dict(thread_id=session, turn_ids=turns, records=records))
    except (StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ObservationError('native_records_unavailable') from None


class HookCollector:
    """Scoped native-stream input, never a qualification or currentness certificate."""

    def __init__(self, *, thread_id, turn_id, source_path):
        values = (thread_id, source_path) + (() if turn_id is None else (turn_id,))
        if (any(not isinstance(value, str) or not value or len(value) > 4096
                or not value.isprintable() for value in values)
                or not Path(source_path).is_absolute()):
            raise ObservationError("invalid_hook_scope")
        self.scope = (thread_id, turn_id, source_path)
        self.runs = {}
        self.invalid = False
        self.native_process = None
        self.native_only = True

    def __call__(self, notification):
        if self.invalid:
            raise ObservationError("invalid_hook_observation")
        if notification.get("method") not in ("hook/started", "hook/completed"):
            return
        try:
            params = notification["params"]
            run = params["run"]
            if (params["threadId"], params.get("turnId"), run["sourcePath"]) != self.scope:
                return
            origin = _HOOK_STREAM.get()
            self.native_only &= origin is not None
            if self.native_process is not None and origin is not self.native_process:
                self.native_only = False
            if origin is not None:
                self.native_process = origin
            fields = ("id", "eventName", "source", "scope", "executionMode", "handlerType")
            if any(not isinstance(run[name], str) or not run[name] or len(run[name]) > 4096
                   or not run[name].isprintable() for name in fields):
                raise ValueError
            if (run["source"] != "plugin" or run["handlerType"] != "command"
                    or run["executionMode"] != "sync"
                    or run["scope"] not in ("thread", "turn")
                    or run["eventName"] not in {"sessionStart", "preToolUse", "postToolUse", "stop", "interrupt"}
                    or type(run["startedAt"]) is not int or run["startedAt"] < 0):
                raise ValueError
            row = dict(thread_id=self.scope[0], turn_id=self.scope[1], source_path=self.scope[2],
                       **{name: run[name] for name in fields}, startedAt=run["startedAt"])
            prior = self.runs.get(run["id"])
            if notification["method"] == "hook/started":
                if prior is not None or len(self.runs) >= 64 or run["status"] != "running":
                    raise ValueError
                self.runs[run["id"]] = row
            else:
                if (prior != row or run["status"] not in {"completed", "failed", "blocked", "stopped"}
                        or type(run["completedAt"]) is not int or run["completedAt"] < run["startedAt"]
                        or type(run["durationMs"]) is not int or run["durationMs"] < 0):
                    raise ValueError
                self.runs[run["id"]] = dict(row, status=run["status"], completedAt=run["completedAt"],
                                            durationMs=run["durationMs"])
        except (KeyError, TypeError, ValueError):
            self.invalid = True
            self.runs.clear()
            raise ObservationError("invalid_hook_observation") from None

    def snapshot(self):
        if self.invalid:
            raise ObservationError("invalid_hook_observation")
        from runtime.startup import _canonical_freeze
        return _canonical_freeze([row for row in self.runs.values() if "status" in row])


class NativeProcess:
    """Own one child, bounded nonblocking stdout, and no inherited stderr output."""

    def __init__(self, argv, deadline, *, on_notification=None, stdin=subprocess.PIPE,
                 cwd=None, env=None, start_new_session=False, pass_fds=()):
        """A trusted synchronous observer must obey the deadline and handle private payloads."""
        if on_notification is not None and not callable(on_notification):
            raise ObservationError("invalid_observer")
        if time.monotonic() >= deadline:
            raise ObservationError("timeout")
        self.on_notification = on_notification
        self.argv = tuple(argv)
        self.launch_cwd = str(Path(os.fsdecode(cwd) if cwd is not None else os.getcwd()).resolve())
        launch_environment = os.environ if env is None else env
        home = launch_environment.get('CODEX_HOME')
        if not home and isinstance(launch_environment.get('HOME'), str) and launch_environment['HOME']:
            home = str(Path(launch_environment['HOME'])/'.codex')
        self.launch_codex_home = str(Path(home).resolve()) if isinstance(home, str) and Path(home).is_absolute() else None
        self.identity = None
        self.response_source = None
        self._stdout_offset = 0
        self.deadline = deadline
        self.buffer = b""
        self.bytes_read = 0
        self.process = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0, cwd=cwd, env=env,
                                        start_new_session=start_new_session, pass_fds=pass_fds)
        self.selector = selectors.DefaultSelector()
        os.set_blocking(self.process.stdout.fileno(), False)
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.wait(timeout=0.1)
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.stdout.close()

    def read(self, deadline):
        remaining = min(deadline, self.deadline) - time.monotonic()
        if remaining <= 0 or not self.selector.select(remaining):
            raise ObservationError("timeout")
        chunk = os.read(self.process.stdout.fileno(), 8192)
        self.bytes_read += len(chunk)
        if self.bytes_read > MAX_OUTPUT:
            raise ObservationError("output_limit")
        return chunk

    def cli_header(self):
        """Read the original exec JSON header without assigning an RPC identity."""
        if (self.identity is not None or self.bytes_read or self.buffer or len(self.argv) < 3
                or self.argv[1] != 'exec' or '--json' not in self.argv[2:]):
            raise ObservationError("cli_source_unverified")
        deadline = min(self.deadline, time.monotonic()+RPC_TIMEOUT)
        while b'\n' not in self.buffer:
            chunk = self.read(deadline)
            if not chunk:
                raise ObservationError("unavailable")
            self.buffer += chunk
            if len(self.buffer.split(b'\n', 1)[0]) > MAX_INPUT:
                raise ObservationError("output_limit")
        raw, self.buffer = self.buffer.split(b'\n', 1)
        self._stdout_offset += len(raw)+1
        try:
            header = json_object(raw)
            thread = header.get('thread_id')
            if (set(header) != {'type', 'thread_id'} or header['type'] != 'thread.started'
                    or not isinstance(thread, str) or not thread.strip() or len(thread) > 256
                    or not thread.isprintable()):
                raise ValueError
        except (ValueError, RecursionError, UnicodeError):
            raise ObservationError("malformed_cli_header") from None
        from runtime.startup import _canonical_freeze
        return _canonical_freeze(dict(thread_id=thread, source=dict(
            reference=f'native-process:{self.process.pid}#stdout:0:{len(raw)+1}',
            sha256=hashlib.sha256(raw+b'\n').hexdigest())))

    def send(self, message):
        # Read RPCs only; native hook enumeration may initialize its own trust baselines.
        if self.process.stdin is None:
            raise ObservationError("stdin_unavailable")
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()

    def _deliver_notification(self, response):
        token = _HOOK_STREAM.set(self)
        try:
            self.on_notification(response)
        finally:
            _HOOK_STREAM.reset(token)

    def request(self, identity, method, params, *, response_limit=MAX_INPUT):
        if type(response_limit) is not int or not 0 < response_limit <= MAX_OUTPUT:
            raise ObservationError("invalid_response_limit")
        self.send({"id": identity, "method": method, "params": params})
        deadline = min(time.monotonic() + RPC_TIMEOUT, self.deadline)
        while True:
            if time.monotonic() >= deadline:
                raise ObservationError("timeout")
            while b"\n" not in self.buffer:
                chunk = self.read(deadline)
                if not chunk:
                    raise ObservationError("unavailable")
                self.buffer += chunk
                if len(self.buffer) > response_limit:
                    raise ObservationError("output_limit")
            raw, self.buffer = self.buffer.split(b"\n", 1)
            offset = self._stdout_offset
            self._stdout_offset += len(raw)+1
            try:
                response = json_object(raw, limit=response_limit)
            except (ValueError, RecursionError, UnicodeError):
                raise ObservationError("malformed_response") from None
            if response.get("jsonrpc", "2.0") != "2.0":
                raise ObservationError("malformed_response")
            if "id" not in response:
                if len(raw) > MAX_INPUT:
                    raise ObservationError("output_limit")
                if not isinstance(response.get("method"), str) or "result" in response or "error" in response:
                    raise ObservationError("malformed_response")
                if self.on_notification is not None:
                    # Stream observations are input to the owner, never qualification by themselves.
                    self._deliver_notification(response)
                continue
            if type(response["id"]) is not int or response["id"] != identity:
                raise ObservationError("mismatched_response")
            if ("error" in response) == ("result" in response):
                raise ObservationError("malformed_response")
            if "error" in response:
                raise ObservationError("rpc_error")
            if not isinstance(response["result"], dict):
                raise ObservationError("malformed_response")
            if method == 'initialize':
                validate_initialize(response['result'])
                self.identity = {key: response['result'][key] for key in
                    ('codexHome', 'platformFamily', 'platformOs', 'userAgent')}
            from runtime.startup import _canonical_freeze
            self.response_source = _canonical_freeze(dict(
                reference=f'native-process:{self.process.pid}#stdout:{offset}:{len(raw)+1}',
                sha256=hashlib.sha256(raw+b'\n').hexdigest()))
            return response["result"]


def version(executable, deadline):
    child = NativeProcess([str(executable), "--version"], deadline)
    try:
        output = b""
        rpc_deadline = min(time.monotonic() + RPC_TIMEOUT, deadline)
        while True:
            chunk = child.read(rpc_deadline)
            if not chunk:
                break
            output += chunk
            if len(output) > 4096:
                raise ObservationError("output_limit")
        remaining = rpc_deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationError("timeout")
        if child.process.wait(timeout=remaining) != 0:
            raise ObservationError("unavailable")
        match = re.fullmatch(rb"codex-cli ([0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?)\s*", output)
        if not match or len(match[1]) > 80:
            raise ObservationError("unknown_version")
        return match[1].decode("ascii")
    finally:
        child.close()


def validate_initialize(response):
    for field in ("codexHome", "platformFamily", "platformOs", "userAgent"):
        value = response.get(field)
        if (not isinstance(value, str) or not value.strip() or len(value) > 4096
                or any(not char.isprintable() for char in value)):
            raise ObservationError("malformed_response")


def discover(executable, cwd, deadline):
    """Native enumeration can initialize Codex-owned .hook-trust baselines."""
    child = NativeProcess([str(executable), "app-server", "--listen", "stdio://"], deadline)
    try:
        initialized = child.request(1, "initialize", {
            "clientInfo": {"name": "vibe-workflow-doctor", "version": "1"},
            "capabilities": {"experimentalApi": True}})
        validate_initialize(initialized)
        child.send({"method": "initialized"})
        return child.request(2, "hooks/list", {"cwds": [str(cwd)]})
    finally:
        child.close()


def inspect_hooks(response, scope, definitions, *, package=PACKAGE):
    data = response.get("data")
    if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
            or data[0].get("cwd") != str(scope.repository_root)):
        raise ObservationError("malformed_response")
    entry = data[0]
    hooks, errors, warnings = (entry.get(key) for key in ("hooks", "errors", "warnings"))
    if (not isinstance(hooks, list) or len(hooks) > 1024 or not isinstance(errors, list)
            or not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings)
            or any(not isinstance(item, dict) or not isinstance(item.get("message"), str)
                   or not isinstance(item.get("path"), str) for item in errors)):
        raise ObservationError("malformed_response")
    source = str(package / "hooks/codex.json")
    for hook in hooks:
        if (not isinstance(hook, dict) or not isinstance(hook.get("eventName"), str)
                or not isinstance(hook.get("sourcePath"), str)):
            raise ObservationError("malformed_response")
    events = {}
    expected = {name[0].lower() + name[1:] for name in definitions}
    unexpected = sum(hook["sourcePath"] == source and hook["eventName"] not in expected for hook in hooks)
    failed = bool(errors or unexpected)
    incomplete = bool(warnings)
    for name, groups in definitions.items():
        event = name[0].lower() + name[1:]
        candidates = [hook for hook in hooks if hook["eventName"] == event and hook["sourcePath"] == source]
        item = {"status": "missing", "definition": "unobserved"}
        events[event] = item
        if len(candidates) != 1:
            item["status"] = "duplicate" if candidates else "missing"
            failed = True
            continue
        hook = candidates[0]
        if (any(not isinstance(hook.get(key), str) for key in ("key", "source", "currentHash", "trustStatus", "handlerType"))
                or any(type(hook.get(key)) is not bool for key in ("enabled", "isManaged"))
                or type(hook.get("displayOrder")) is not int or type(hook.get("timeoutSec")) is not int
                or hook["timeoutSec"] < 0 or hook["source"] not in SOURCES
                or not HASH.fullmatch(hook["currentHash"])
                or hook["trustStatus"] not in {"trusted", "managed", "untrusted", "modified"}
                or hook["handlerType"] not in {"command", "mcpTool", "prompt", "agent"}
                or (hook.get("matcher") is not None and not isinstance(hook["matcher"], str))
                or type(hook.get("async", False)) is not bool
                or (hook["handlerType"] == "command" and not isinstance(hook.get("command"), str))
                or (hook.get("additionalContextLimit") is not None and
                    (type(hook["additionalContextLimit"]) is not int or hook["additionalContextLimit"] < 0))):
            item["status"] = "unknown_schema"
            incomplete = True
            continue
        group = groups[0]
        handler = group["hooks"][0]
        matched = (hook["source"] == "plugin" and hook["handlerType"] == "command"
            and hook.get("command") == handler["command"].replace("${PLUGIN_ROOT}", str(package))
            and hook.get("matcher") == group.get("matcher") and hook["timeoutSec"] == handler["timeout"]
            and not hook.get("async", False) and hook.get("additionalContextLimit") is None)
        item.update(definition="matched" if matched else "modified", current_hash=hook["currentHash"],
                    enabled=hook["enabled"], trust=hook["trustStatus"])
        if not matched:
            item["status"] = "definition_mismatch"
        elif not hook["enabled"]:
            item["status"] = "disabled"
        elif hook["trustStatus"] in {"untrusted", "modified"}:
            item["status"] = hook["trustStatus"]
        else:
            item["status"] = "passed"
        failed |= item["status"] != "passed"
    return {"status": "failed" if failed else "incomplete" if incomplete else "passed", "events": events,
            "source": source, "source_digest": hashlib.sha256((package / "hooks/codex.json").read_bytes()).hexdigest(),
            "error_count": len(errors), "warning_count": len(warnings), "unexpected_count": unexpected}


def inspect_runtime(scope, executable, *, acquisition=None, enrollment_unavailable=False):
    """Return JSON-safe independent observations; exit_code is 0, failure 1, incomplete 2."""
    deadline = time.monotonic() + TOTAL_TIMEOUT - 0.25
    report = {
        "package": {"status": "failed"}, "enrollment": {"status": "missing"},
        "state": {"status": "not_checked"}, "native": {"status": "not_checked"},
        "hooks": {"status": "not_checked"},
        "execution": {"status": "unverified", "desktop_loaded_definition": "unknown",
                      "guidance": "docs/d2-validation.md"},
    }
    failed = False
    incomplete = False
    definitions = None
    try:
        validation = validate(PACKAGE)
        installed_policy = policy_digest(PACKAGE)
        manifest = json_object((PACKAGE / ".codex-plugin/plugin.json").read_bytes())
        definitions = json_object((PACKAGE / "hooks/codex.json").read_bytes())["hooks"]
        report["package"] = {"status": "passed", "name": validation["package"],
                             "version": manifest["version"], "policy_digest": installed_policy}
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        failed = True
    state_api = None
    try:
        from runtime.state import inspect_state, runtime_fingerprint
        state_api = inspect_state
        report["package"]["runtime_digest"] = runtime_fingerprint()
    except ImportError:
        report["state"] = {"status": "unavailable"}
        incomplete = True
    if enrollment_unavailable:
        report["enrollment"] = {"status": "unavailable"}
    if acquisition is not None:
        resolution = acquisition.resolution
        scope = resolution.scope if resolution.status == "active" else None
        if scope is None and resolution.status != "outside":
            report["enrollment"] = {"status": resolution.status, "reason": resolution.reason}
    if scope is None:
        failed = True
        report["hooks"] = {"status": "requires_enrollment"}
    else:
        report["enrollment"] = {"status": "passed", "project_id": scope.project_id}
        if report["package"]["status"] != "passed":
            report["enrollment"]["status"] = "package_unverified"
        elif scope.policy_digest != installed_policy:
            report["enrollment"]["status"] = "policy_mismatch"
            failed = True
        try:
            if state_api is None:
                raise ImportError
            state = state_api(scope)
            status = ("not_configured" if not state["configured"] else "incompatible" if state['schema_version'] != 3
                      else "missing" if not state["present"] else "incompatible" if not state["compatible"] else "passed")
            report["state"] = {"status": status, **{key: state[key] for key in
                ("configured", "present", "compatible", "schema_version", "policy_digest", "runtime_digest", "tasks", "error")
                if key in state}}
            failed |= status != "passed"
        except ImportError:
            report["state"] = {"status": "unavailable"}
            incomplete = True
        except (StartupError, OSError, ValueError, KeyError, TypeError):
            report["state"] = {"status": "unsafe_or_incompatible"}
            failed = True
    try:
        observed = version(executable, deadline)
        report["native"] = {"status": "observed", "version": observed,
                            "version_evidence": "recorded" if observed in KNOWN_VERSIONS else "unrecorded"}
        if scope is not None:
            response = discover(executable, scope.repository_root, deadline)
            report["native"]["status"] = "passed"
            if definitions is not None:
                report["hooks"] = inspect_hooks(response, scope, definitions)
                failed |= report["hooks"]["status"] == "failed"
                incomplete |= report["hooks"]["status"] == "incomplete"
            else:
                incomplete = True
    except ObservationError as error:
        report["native"]["status"] = str(error)
        if scope is not None:
            report["hooks"] = {"status": "unobserved"}
        incomplete = True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report["native"]["status"] = "unavailable"
        if scope is not None:
            report["hooks"] = {"status": "unobserved"}
        incomplete = True
    report["exit_code"] = 1 if failed else 2 if incomplete else 0
    return report
