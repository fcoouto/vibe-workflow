"""Read-only package/state checks and bounded native hook discovery, never execution."""

import hashlib
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


class ObservationError(Exception):
    """Fixed status only; child output and OS exception messages stay private."""


class NativeProcess:
    """Own one child, bounded nonblocking stdout, and no inherited stderr output."""

    def __init__(self, argv, deadline):
        if time.monotonic() >= deadline:
            raise ObservationError("timeout")
        self.deadline = deadline
        self.buffer = b""
        self.bytes_read = 0
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, bufsize=0)
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

    def send(self, message):
        # Requests are small fixed diagnostics; no configuration, authorization or turn RPCs.
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        self.process.stdin.flush()

    def request(self, identity, method, params):
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
                if len(self.buffer) > MAX_INPUT:
                    raise ObservationError("output_limit")
            raw, self.buffer = self.buffer.split(b"\n", 1)
            try:
                response = json_object(raw)
            except (ValueError, RecursionError, UnicodeError):
                raise ObservationError("malformed_response") from None
            if response.get("jsonrpc", "2.0") != "2.0":
                raise ObservationError("malformed_response")
            if "id" not in response:
                if not isinstance(response.get("method"), str) or "result" in response or "error" in response:
                    raise ObservationError("malformed_response")
                continue
            if type(response["id"]) is not int or response["id"] != identity:
                raise ObservationError("mismatched_response")
            if ("error" in response) == ("result" in response):
                raise ObservationError("malformed_response")
            if "error" in response:
                raise ObservationError("rpc_error")
            if not isinstance(response["result"], dict):
                raise ObservationError("malformed_response")
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


def inspect_hooks(response, scope, definitions):
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
    source = str(PACKAGE / "hooks/codex.json")
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
            and hook.get("command") == handler["command"].replace("${PLUGIN_ROOT}", str(PACKAGE))
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
            "source": source, "source_digest": hashlib.sha256((PACKAGE / "hooks/codex.json").read_bytes()).hexdigest(),
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
