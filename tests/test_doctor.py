"""Independent diagnostics exercise native framing without models or user settings."""

from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from runtime import startup


PACKAGE = Path(__file__).resolve().parents[1]
EVENTS = ("sessionStart", "preToolUse", "postToolUse", "stop", "interrupt")
SECRET = "PRIVATE_NATIVE_CONTENT"


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe doctor ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.scope = startup.ProjectScope("fixture", self.repo, self.repo / ".git",
            startup.policy_digest(PACKAGE), startup.RuntimeSettings(2, self.base / "state"))
        self.state = dict(configured=True, present=True, compatible=True, schema_version=3,
                          runtime_digest="a" * 64, policy_digest=self.scope.policy_digest, tasks=[])
        self.hooks = []
        matchers = ("^(startup|resume|clear|compact|fork)$", "^apply_patch$", "*", None, None)
        for event, matcher in zip(EVENTS, matchers):
            title = event[0].upper() + event[1:]
            self.hooks.append(dict(eventName=event, source="plugin",
                sourcePath=str(PACKAGE / "hooks/codex.json"),
                key=f"{PACKAGE}/hooks/codex.json:{event}:0:0", currentHash="sha256:" + "b" * 64,
                enabled=True, trustStatus="trusted", isManaged=False, displayOrder=0,
                handlerType="command", command=f'python3 -I -B "{PACKAGE}/scripts/workflow.py" codex {title}',
                matcher=matcher, timeoutSec=1 if event == "interrupt" else 5,
                **{"async": False, "additionalContextLimit": None, "pluginId": "vibe-coding-workflow"}))

    @contextmanager
    def state_api(self, failure=False):
        module = types.ModuleType("runtime.state")
        module.StateError = type("StateError", (startup.StartupError,), {})
        module.runtime_fingerprint = lambda: "a" * 64
        def inspect(scope):
            if failure:
                raise module.StateError(SECRET)
            return self.state if scope.runtime else dict(configured=False, present=False)
        module.inspect_state = inspect
        with patch.dict(sys.modules, {"runtime.state": module}):
            yield

    def executable(self, mode="success", *, version="0.163.0", hooks=None):
        hooks = self.hooks if hooks is None else hooks
        response = dict(data=[dict(cwd=str(self.repo), hooks=hooks, warnings=[], errors=[])])
        if mode == "discovery_errors":
            response["data"][0]["errors"] = [dict(path=SECRET, message=SECRET)]
        if mode == "discovery_warnings":
            response["data"][0]["warnings"] = [SECRET]
        path = self.base / "native"
        log = self.base / "requests.jsonl"
        pid = self.base / "pid"
        script = f'''#!{sys.executable}
import json, os, signal, sys, time
mode = {mode!r}
if sys.argv[1:] == ["--version"]:
    if mode == "slow":
        time.sleep(0.08)
    if mode == "version_noise":
        print("{SECRET}" * 40000, flush=True)
    elif mode == "version_timeout":
        time.sleep(60)
    else:
        print("codex-cli {version}")
    raise SystemExit
assert sys.argv[1:] == ["app-server", "--listen", "stdio://"]
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
open({str(pid)!r}, "w").write(str(os.getpid()))
for line in sys.stdin:
    request = json.loads(line)
    with open({str(log)!r}, "a") as output:
        output.write(json.dumps(request) + "\\n")
    if request.get("method") == "initialized":
        continue
    assert request["method"] in ("initialize", "hooks/list")
    if mode == "slow":
        time.sleep(0.08)
    if request["method"] == "initialize":
        assert request["params"] == {{"clientInfo": {{"name": "vibe-workflow-doctor", "version": "1"}}, "capabilities": {{"experimentalApi": True}}}}
        result = {{"codexHome": "/private/{SECRET}", "platformFamily": "unix", "platformOs": "macos", "userAgent": "fixture/{SECRET}"}}
    else:
        assert request["params"] == {{"cwds": [{str(self.repo)!r}]}}
        if mode in ("timeout", "ignore_term"):
            time.sleep(60)
        if mode == "noise":
            print("{SECRET}" * 40000, flush=True)
            continue
        if mode == "notification_noise":
            while True:
                print(json.dumps({{"method": "private", "params": {{"secret": "{SECRET}"}}}}), flush=True)
        if mode == "malformed":
            print("{SECRET}", flush=True)
            continue
        if mode == "duplicate_json":
            print('{{"id":2,"id":2,"result":{{"data":[]}}}}', flush=True)
            continue
        if mode == "rpc_error":
            print(json.dumps({{"id": request["id"], "error": {{"code": -1, "message": "{SECRET}"}}}}), flush=True)
            continue
        result = {response!r}
        if mode == "malformed_shape":
            result = {{"data": "{SECRET}"}}
    response_id = request["id"] + 10 if mode == "wrong_id" else request["id"]
    response = {{"id": response_id, "result": result}}
    if mode == "wrong_jsonrpc":
        response["jsonrpc"] = "1.0"
    print(json.dumps(response), flush=True)
'''
        path.write_text(script)
        path.chmod(0o700)
        return path

    def inspect(self, executable=None, **options):
        from runtime import doctor
        with self.state_api(**options):
            report = doctor.inspect_runtime(self.scope, executable or self.executable())
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertFalse(self.scope.runtime.state_dir.exists())
        pid_file = self.base / "pid"
        if pid_file.exists():
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)
        return report

    def test_exact_definitions_pass_only_inspectable_checks(self):
        report = self.inspect()
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["package"]["status"], "passed")
        self.assertEqual(report["enrollment"]["status"], "passed")
        self.assertEqual(report["state"]["status"], "passed")
        self.assertEqual(report["native"]["version"], "0.163.0")
        self.assertEqual(report["hooks"]["status"], "passed")
        self.assertEqual(set(report["hooks"]["events"]), set(EVENTS))
        self.assertTrue(all(item["definition"] == "matched" for item in report["hooks"]["events"].values()))
        self.assertEqual(report["execution"]["status"], "unverified")
        self.assertEqual(report["execution"]["desktop_loaded_definition"], "unknown")
        self.assertEqual(report["execution"]["guidance"], "docs/d2-validation.md")
        methods = [json.loads(line)["method"] for line in (self.base / "requests.jsonl").read_text().splitlines()]
        self.assertEqual(methods, ["initialize", "initialized", "hooks/list"])

    def test_current_observer_binary_is_supported_without_claiming_native_execution(self):
        report = self.inspect(self.executable(version="0.165.0"))
        self.assertEqual(report["native"], dict(status="passed", version="0.165.0", version_evidence="recorded"))
        self.assertEqual(report["execution"]["status"], "unverified")
        self.assertEqual(report["exit_code"], 0)
        report = self.inspect(self.executable(version="0.165.0-alpha.1.2"))
        self.assertEqual(report["native"]["status"], "passed")
        self.assertEqual(report["native"]["version_evidence"], "unrecorded")

    def test_disabled_untrusted_and_modified_candidates_fail_separately(self):
        for field, value, expected in (("enabled", False, "disabled"),
                ("trustStatus", "untrusted", "untrusted"), ("trustStatus", "modified", "modified")):
            with self.subTest(field=field, value=value):
                hooks = [dict(hook) for hook in self.hooks]
                hooks[1][field] = value
                report = self.inspect(self.executable(hooks=hooks))
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["hooks"]["events"]["preToolUse"]["status"], expected)

    def test_equal_count_of_unrelated_hooks_does_not_prove_registration(self):
        hooks = [dict(hook, sourcePath="/unrelated/hooks.json") for hook in self.hooks]
        report = self.inspect(self.executable(hooks=hooks))
        self.assertEqual(report["exit_code"], 1)
        self.assertTrue(all(item["status"] == "missing" for item in report["hooks"]["events"].values()))

    def test_candidate_definition_drift_and_duplicate_are_detected(self):
        for change in ({"command": SECRET}, {"matcher": "*"}, {"timeoutSec": 12},
                       {"async": True}, {"additionalContextLimit": 1}):
            with self.subTest(change=change):
                hooks = [dict(hook) for hook in self.hooks]
                hooks[1].update(change)
                report = self.inspect(self.executable(hooks=hooks))
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["hooks"]["events"]["preToolUse"]["definition"], "modified")
        report = self.inspect(self.executable(hooks=self.hooks + [self.hooks[1]]))
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["hooks"]["events"]["preToolUse"]["status"], "duplicate")

    def test_native_hash_and_unknown_trust_are_not_accepted(self):
        for change in ({"currentHash": SECRET}, {"trustStatus": "future"},
                       {"additionalContextLimit": SECRET}, {"timeoutSec": -1},
                       {"command": None}, {"source": "future"}):
            hooks = [dict(hook) for hook in self.hooks]
            hooks[0].update(change)
            report = self.inspect(self.executable(hooks=hooks))
            self.assertEqual(report["exit_code"], 2)

    def test_unexpected_event_in_same_source_is_definition_drift(self):
        extra = dict(self.hooks[0], eventName="userPromptSubmit")
        report = self.inspect(self.executable(hooks=self.hooks + [extra]))
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["hooks"]["unexpected_count"], 1)

    def test_unrecorded_version_uses_live_contract_checks_without_claiming_execution(self):
        report = self.inspect(self.executable(version="9.999.0"))
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["native"]["status"], "passed")
        self.assertEqual(report["native"]["version_evidence"], "unrecorded")
        self.assertEqual(report["execution"]["status"], "unverified")

    def test_desktop_compound_native_hash_is_preserved_as_opaque_identity(self):
        value = "sha256:" + "b" * 64 + "/sha256:" + "c" * 64
        hooks = [dict(hook, currentHash=value) for hook in self.hooks]
        report = self.inspect(self.executable(version="0.164.0-alpha.11.1", hooks=hooks))
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["hooks"]["events"]["preToolUse"]["current_hash"], value)

    def test_unavailable_executable_has_sanitized_incomplete_report(self):
        report = self.inspect(self.base / SECRET)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["native"]["status"], "unavailable")

    def test_malformed_mismatched_error_and_noisy_protocol_are_bounded(self):
        for mode in ("malformed", "wrong_id", "wrong_jsonrpc", "duplicate_json", "malformed_shape", "rpc_error",
                     "noise", "notification_noise", "version_noise"):
            with self.subTest(mode=mode):
                began = time.monotonic()
                report = self.inspect(self.executable(mode))
                self.assertEqual(report["exit_code"], 2)
                self.assertLess(time.monotonic() - began, 2)

    def test_timeouts_reap_owned_process_and_do_not_wait_for_eof(self):
        from runtime import doctor
        for mode in ("timeout", "version_timeout", "ignore_term"):
            with self.subTest(mode=mode), patch.object(doctor, "RPC_TIMEOUT", 0.15):
                began = time.monotonic()
                report = self.inspect(self.executable(mode))
                self.assertEqual(report["exit_code"], 2)
                self.assertEqual(report["native"]["status"], "timeout")
                self.assertLess(time.monotonic() - began, 1)

    def test_total_budget_includes_version_and_all_native_requests(self):
        from runtime import doctor
        with patch.object(doctor, "TOTAL_TIMEOUT", 0.45), patch.object(doctor, "RPC_TIMEOUT", 0.4):
            began = time.monotonic()
            report = self.inspect(self.executable("slow"))
        self.assertEqual(report["native"]["status"], "timeout")
        self.assertLess(time.monotonic() - began, 0.7)

    def test_discovery_diagnostics_are_counts_without_private_messages(self):
        for mode, code, field in (("discovery_errors", 1, "error_count"),
                                  ("discovery_warnings", 2, "warning_count")):
            with self.subTest(mode=mode):
                report = self.inspect(self.executable(mode))
                self.assertEqual(report["exit_code"], code)
                self.assertEqual(report["hooks"][field], 1)

    def test_package_validation_failure_is_independent_from_native_discovery(self):
        from runtime import doctor
        with patch.object(doctor, "validate", side_effect=ValueError(SECRET)):
            report = self.inspect()
        self.assertEqual(report["package"]["status"], "failed")
        self.assertEqual(report["native"]["status"], "passed")
        self.assertEqual(report["exit_code"], 1)

    def test_timeout_stops_only_owned_child(self):
        from runtime import doctor
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            with patch.object(doctor, "RPC_TIMEOUT", 0.15):
                self.inspect(self.executable("timeout"))
            self.assertIsNone(other.poll())
        finally:
            other.terminate()
            other.wait(timeout=2)

    def test_real_state_pin_inspection_is_read_only_and_detects_mismatch(self):
        from runtime import doctor, state
        from tests.fixture_canonical_birth import fresh_canonical
        fixture,acquired,_=fresh_canonical(self)
        self.scope,self.repo=acquired.resolution.scope,fixture.repo
        executable = self.executable()
        report = doctor.inspect_runtime(self.scope, executable)
        self.assertEqual(report['state']['status'],'passed')
        state.start_task(self.scope, dict(client="codex", session_id="session", task_id="task",
            scope_ref="request:1", mode="editable", decision_ref="user:1"))
        directory = self.scope.runtime.state_dir
        before = {str(path): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
        report = doctor.inspect_runtime(self.scope, executable)
        self.assertEqual(report["state"]["status"], "passed")
        self.assertEqual(before, {str(path): path.read_bytes() for path in directory.rglob("*") if path.is_file()})
        database = directory / "runtime.sqlite3"
        with sqlite3.connect(database) as connection:
            task = json.loads(connection.execute("select data from tasks").fetchone()[0])
            task["pins"]["runtime_digest"] = "0" * 64
            connection.execute("update tasks set data=?", (json.dumps(task),))
        before = database.read_bytes()
        report = doctor.inspect_runtime(self.scope, executable)
        self.assertEqual(report["state"]["status"], "incompatible")
        self.assertFalse(report["state"]["compatible"])
        self.assertEqual(report["state"]["tasks"], [])
        self.assertEqual(report["state"]["error"], "Task snapshot, pins and physical checkout differ")
        self.assertEqual(report["package"]["status"], "passed")
        self.assertEqual(report["native"]["status"], "passed")
        self.assertEqual(report["hooks"]["status"], "passed")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(before, database.read_bytes())

    def test_state_and_policy_pin_mismatch_are_failures_without_repairs(self):
        self.state.update(compatible=False)
        report = self.inspect()
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["state"]["status"], "incompatible")
        self.scope = replace(self.scope, policy_digest="c" * 64)
        report = self.inspect()
        self.assertEqual(report["enrollment"]["status"], "policy_mismatch")
        self.assertEqual(report["exit_code"], 1)

    def test_unsafe_state_error_is_sanitized_and_missing_state_stays_missing(self):
        report = self.inspect(failure=True)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["state"]["status"], "unsafe_or_incompatible")
        self.state.update(present=False)
        report = self.inspect()
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["state"]["status"], "missing")

    def test_missing_enrollment_is_explicit_without_creating_files(self):
        from runtime import doctor
        with self.state_api():
            report = doctor.inspect_runtime(None, self.executable())
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["enrollment"]["status"], "missing")
        self.assertEqual(report["native"], {"status": "observed", "version": "0.163.0", "version_evidence": "recorded"})
        self.assertEqual(report["hooks"]["status"], "requires_enrollment")
        self.assertFalse((self.base / "requests.jsonl").exists())
        self.assertFalse(self.scope.runtime.state_dir.exists())

    def test_missing_enrollment_does_not_hide_unavailable_executable(self):
        from runtime import doctor
        report = doctor.inspect_runtime(None, self.base / SECRET)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["enrollment"]["status"], "missing")
        self.assertEqual(report["native"]["status"], "unavailable")
        self.assertEqual(report["hooks"]["status"], "requires_enrollment")
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertFalse(self.scope.runtime.state_dir.exists())


if __name__ == "__main__":
    unittest.main()


class LegacyDoctorReportTests(unittest.TestCase):
    def test_legacy_report_cannot_be_current_even_when_empty(self):
        fixture=DoctorTests("runTest");fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.state=fixture.state
        self.state['schema_version']=2
        report=fixture.inspect()
        self.assertEqual(report['state']['status'],'incompatible')
        self.assertEqual(report['exit_code'],1)
