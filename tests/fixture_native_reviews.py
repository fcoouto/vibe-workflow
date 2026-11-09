"""Shared fixtures for native review tests; contains no test cases."""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from runtime import evidence
from runtime.adapters import codex
from runtime.startup import ProjectScope, StartupError


class NativeReviewFixture:
    """Mixin: disposable project scope, fake native executable and response catalog."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scope = ProjectScope("fixture", self.root, self.root / ".git", "policy")
        self.binding = dict(parent_session_id="parent", assignment="VCW review attempt attempt digest " + "a" * 64,
                            assignment_digest="a" * 64, candidate_digest="b" * 64,
                            requested_coverage=["general"], task_name="review_attempt")
        self.report = dict(version=1, attempt_id="attempt", assignment_digest="a" * 64,
                           candidate_digest="b" * 64, coverage=["general"], findings=[], limitations=[])
        self.text = json.dumps(self.report)
        self.responses = {
            "parent": dict(thread=dict(id="parent", cwd=str(self.root), cliVersion="0.163.0",
                                       source="exec", originator="codex_exec")),
            "spawn": dict(data=[dict(turnId="parent-turn", item=dict(
                id="spawn", type="subAgentActivity", kind="started", agentThreadId="child",
                agentPath="/root/review_attempt", model="gpt-6-luna", reasoningEffort="medium"))]),
            "child": dict(thread=dict(id="child", parentThreadId="parent", cwd=str(self.root),
                                       cliVersion="0.163.0", source={"subAgent": {"thread_spawn": {
                                           "parent_thread_id": "parent", "agent_path": "/root/review_attempt"}}})),
            "turns": dict(data=[dict(id="child-turn", status="completed", error=None)], nextCursor=None),
            "items": dict(data=[dict(turnId="child-turn", item=dict(id="final", type="agentMessage",
                                        phase="final_answer", text=self.text))], nextCursor=None),
        }
        self.log = self.root / "rpc.jsonl"

    def executable(self, responses=None, mode="normal", version="0.163.0"):
        path = self.root / "native"
        path.write_text(f'''#!{sys.executable}
import json, os, sys, time
if sys.argv[1:] == ["--version"]:
    print("codex-cli {version}")
    raise SystemExit
responses = {responses or self.responses!r}
mode = {mode!r}
turn_reads = 0
for line in sys.stdin:
    request = json.loads(line)
    with open({str(self.log)!r}, "a") as log:
        log.write(json.dumps(request) + "\\n")
    if "id" not in request:
        continue
    method = request["method"]
    params = request["params"]
    if mode == "slow":
        time.sleep(30)
    if method == "initialize": result = responses.get("initialize", {{"codexHome": "/private/native", "platformFamily": "unix", "platformOs": "macos", "userAgent": "fixture"}})
    elif method == "thread/read": result = responses[params["threadId"]]
    elif method == "thread/items/read": result = responses["spawn"]
    elif method == "model/list": result = responses.get("catalog_second", responses["catalog"]) if params.get("cursor") else responses["catalog"]
    elif method == "thread/turns/list":
        turn_reads += 1
        if mode == "changed-rollout" and turn_reads == 2:
            with open(responses["child"]["thread"]["path"], "ab") as source:
                source.write(b"\\n")
        result = responses.get("turns_second", responses["turns"]) if turn_reads > 1 else responses["turns"]
    elif method == "thread/items/list":
        if "history" in responses:
            start = int(params.get("cursor", "0"))
            stop = start + params["limit"]
            result = {{"data": responses["history"][start:stop], "nextCursor": str(stop) if stop < len(responses["history"]) else None}}
        else:
            result = responses.get("items_second", responses["items"]) if params.get("cursor") else responses["items"]
    else: raise RuntimeError("Unexpected RPC")
    if mode == "oversized": result = {{"data": "x" * (600 * 1024)}}
    if mode == "oversized-notification" and method == "thread/items/read":
        print(json.dumps({{"method": "fixture/notification", "params": {{"data": "x" * (128 * 1024)}}}}), flush=True)
    print(json.dumps({{"id": request["id"], "result": result}}), flush=True)
''')
        path.chmod(0o700)
        return str(path)

    @contextmanager
    def prepared(self, binding=None, failure=False):
        module = types.ModuleType("runtime.evidence")
        calls = []
        def prepared_native_review(scope, attempt_id):
            calls.append((scope, attempt_id))
            if failure:
                raise StartupError("candidate changed")
            return copy.deepcopy(binding or self.binding)
        module.prepared_native_review = prepared_native_review
        module._digest = evidence._digest
        with patch.dict(sys.modules, {"runtime.evidence": module}):
            yield calls

    def collect(self, responses=None, mode="normal", version="0.163.0", binding=None):
        with self.prepared(binding) as calls, patch.object(codex.shutil, "which", return_value=self.executable(responses, mode, version)):
            result = codex.collect_native_review(self.scope, "attempt", "parent-turn", "spawn")
        self.assertEqual(len(calls), 2)
        return result

    def v1_fixture(self, review=False):
        responses, binding = self.review_assignment_fixture() if review else self.worker_fixture()
        body = dict(binding=dict(task_name=binding["task_name"]), request=dict(assignment="Prepared fixture"),
                    report_contract={}, review=dict(coverage=["general"]))
        digest = evidence._digest(body)
        binding["assignment_digest"] = digest
        envelope = (dict(ledger_assignment_digest=digest, ownership=body, assignment="Prepared fixture",
                         report_contract=dict(attempt_id="attempt", candidate_digest=binding["candidate_digest"],
                                              coverage=["general"])) if review else dict(assignment_digest=digest, assignment=body))
        responses["spawn"]["data"][0]["item"] = dict(id="spawn", type="collabAgentToolCall",
            tool="spawnAgent", status="completed", senderThreadId="parent", receiverThreadIds=["child"],
            prompt=json.dumps(envelope), model="gpt-6-luna", reasoningEffort="medium")
        responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"].pop("agent_path")
        return responses, binding

    def worker_fixture(self):
        responses = copy.deepcopy(self.responses)
        responses["parent"]["thread"].update(originator="codex_exec", source="exec")
        responses["child"]["thread"].update(model="configured-model", reasoningEffort="high")
        responses["spawn"]["data"][0]["item"]["agentPath"] = "/root/worker_attempt"
        responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"]["agent_path"] = "/root/worker_attempt"
        binding = dict(attempt_id="attempt", parent_session_id="parent", task_name="worker_attempt",
                       assignment_digest="a" * 64, candidate_digest="b" * 64,
                       requested_model="requested-model", requested_effort="low",
                       client_surface="codex-cli", native_version="0.163.0")
        return responses, binding

    @contextmanager
    def prepared_worker(self, binding, changed=None):
        module = types.ModuleType("runtime.delegation")
        module.prepared_native_assignment = unittest.mock.Mock(
            side_effect=[copy.deepcopy(binding), copy.deepcopy(changed or binding)])
        with patch.dict(sys.modules, {"runtime.delegation": module}):
            yield

    def observe_worker(self, responses=None, binding=None, changed=None, mode="normal", version="0.163.0", **options):
        defaults, prepared = self.worker_fixture()
        executable = self.executable(responses or defaults, mode, version)
        with self.prepared_worker(binding or prepared, changed), patch.object(codex.shutil, "which", return_value=executable), \
                patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            return codex.observe_native_worker(self.scope, "attempt", "parent-turn", "spawn", **options)

    def review_assignment_fixture(self):
        responses, binding = self.worker_fixture()
        binding.update(task_name="review_attempt", assignment_digest="d" * 64)
        responses["spawn"]["data"][0]["item"]["agentPath"] = "/root/review_attempt"
        responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"]["agent_path"] = "/root/review_attempt"
        return responses, binding

    @contextmanager
    def prepared_review_assignment(self, binding, changed=None):
        module = types.ModuleType("runtime.delegation")
        module.prepared_native_review_assignment = unittest.mock.Mock(
            side_effect=[copy.deepcopy(binding), copy.deepcopy(changed or binding)])
        with patch.dict(sys.modules, {"runtime.delegation": module}):
            yield

    def observe_review_assignment(self, responses=None, binding=None, changed=None, **options):
        defaults, prepared = self.review_assignment_fixture()
        executable = self.executable(responses or defaults)
        with self.prepared_review_assignment(binding or prepared, changed), patch.object(codex.shutil, "which", return_value=executable), \
                patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            return codex.observe_native_review_assignment(self.scope, attempt_id="attempt", parent_turn_id="parent-turn", spawn_item_id="spawn", **options)

    def cooperative_rollout(self, responses, activity, *, terminal=True):
        path = self.root / 'rollout.jsonl'
        responses['child']['thread']['path'] = str(path)
        responses['initialize'] = dict(codexHome=str(self.root), platformFamily='unix', platformOs='macos', userAgent='fixture')
        result = responses['items']['data'][0]['item']['text']
        rows = [('session_meta', dict(id='child')), ('event_msg', dict(type='task_started', turn_id='child-turn'))]
        rows.extend(('event_msg' if item['type'] in {'item_completed', 'thread_settings_applied'} else 'response_item',
                     item) for item in activity)
        if terminal:
            rows.append(('event_msg', dict(type='task_complete', turn_id='child-turn', last_agent_message=result)))
        raw = ''.join(json.dumps(dict(timestamp='fixture', type=kind, payload=payload)) + '\n' for kind, payload in rows).encode()
        path.write_bytes(raw)
        return raw

    def control_fixture(self):
        responses, _ = self.worker_fixture()
        responses["catalog"] = dict(data=[dict(model="new-native-model", defaultReasoningEffort="low",
            supportedReasoningEfforts=[dict(reasoningEffort="low"), dict(reasoningEffort="high")])], nextCursor=None)
        controls = dict(version=1, client_surface="codex-cli", native_version="0.163.0", parent_session_id="parent",
                        dispatch_controls=dict(spawn=True, explicit_model=True, explicit_effort=True, fork_turns_none=True))
        return responses, controls

    def discover_workers(self, responses=None, controls=None, version="0.163.0"):
        defaults, control = self.control_fixture()
        executable = self.executable(responses or defaults, version=version)
        with patch.object(codex.shutil, "which", return_value=executable):
            return codex.discover_native_workers(self.scope, controls or control)

    def current_desktop_fixture(self, fixture):
        responses, binding = fixture()
        binding.update(client_surface="codex-desktop", native_version="0.165.0")
        for identity in ("parent", "child"):
            responses[identity]["thread"]["cliVersion"] = "0.165.0-alpha.1.2"
        responses["parent"]["thread"].update(source="vscode", originator="Codex Desktop")
        return responses, binding
