"""Bounded persisted-history receipts never trust caller claims or execute review."""

from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from runtime import doctor
from runtime.adapters import codex
from runtime.startup import ProjectScope, StartupError


class NativeReviewTests(unittest.TestCase):
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
        with patch.dict(sys.modules, {"runtime.evidence": module}):
            yield calls

    def collect(self, responses=None, mode="normal", version="0.163.0", binding=None):
        with self.prepared(binding) as calls, patch.object(codex.shutil, "which", return_value=self.executable(responses, mode, version)):
            result = codex.collect_native_review(self.scope, "attempt", "parent-turn", "spawn")
        self.assertEqual(len(calls), 2)
        return result

    def test_receipt_fetches_actual_report_and_only_read_only_methods(self):
        for version in ("0.163.0", "0.164.0-alpha.11.1"):
            with self.subTest(version=version):
                responses = copy.deepcopy(self.responses)
                for identity in ("parent", "child"):
                    responses[identity]["thread"]["cliVersion"] = version
                observed = self.collect(responses, version=version)
                self.assertEqual(observed["report"], self.report)
                receipt = observed["receipt"]
                self.assertEqual(receipt["reviewer_id"], "child")
                self.assertEqual(receipt["report_digest"], hashlib.sha256(self.text.encode()).hexdigest())
                self.assertEqual(receipt["mechanism"], "codex-native-agent-path")
                self.assertEqual(receipt["native_version"], version)
                self.assertEqual(self.collect(responses, version=version), observed)
        methods = [json.loads(line)["method"] for line in self.log.read_text().splitlines()]
        self.assertEqual(set(methods), {"initialize", "initialized", "thread/read", "thread/items/read",
                                       "thread/turns/list", "thread/items/list"})

    def test_thread_creation_versions_do_not_replace_current_reader_provenance(self):
        for version, other in (("0.163.0", "0.164.0-alpha.11.1"),
                               ("0.164.0-alpha.11.1", "0.163.0")):
            for identity in ("parent", "child"):
                responses = copy.deepcopy(self.responses)
                for matched in ("parent", "child"):
                    responses[matched]["thread"]["cliVersion"] = version
                responses[identity]["thread"]["cliVersion"] = other
                with self.subTest(version=version, identity=identity):
                    receipt = self.collect(responses, version=version)["receipt"]
                    self.assertIn("current_observer_version", receipt)
                    self.assertEqual(receipt["current_observer_version"], version)
                    self.assertEqual(receipt["parent_creation_version"], responses["parent"]["thread"]["cliVersion"])
                    self.assertEqual(receipt["worker_creation_version"], responses["child"]["thread"]["cliVersion"])

    def test_invalid_native_chains_fail_closed(self):
        cases = []
        def changed(label, mutate):
            responses = copy.deepcopy(self.responses)
            mutate(responses)
            cases.append((label, responses))
        changed("other turn", lambda r: r["spawn"]["data"][0].update(turnId="other"))
        changed("other item", lambda r: r["spawn"]["data"][0]["item"].update(id="other"))
        changed("missing native marker", lambda r: r["spawn"]["data"][0]["item"].pop("agentPath"))
        changed("changed native marker", lambda r: r["spawn"]["data"][0]["item"].update(agentPath="/root/review_other"))
        changed("unobservable spawn", lambda r: r["spawn"]["data"][0]["item"].update(type="collabAgentToolCall"))
        changed("self review", lambda r: r["spawn"]["data"][0]["item"].update(agentThreadId="parent"))
        changed("incomplete spawn", lambda r: r["spawn"]["data"][0]["item"].update(kind="completed"))
        changed("other child", lambda r: r["child"]["thread"].update(id="other"))
        changed("other child parent", lambda r: r["child"]["thread"].update(parentThreadId="other"))
        changed("missing native origin", lambda r: r["child"]["thread"].pop("source"))
        changed("other native origin", lambda r: r["child"]["thread"]["source"]["subAgent"]["thread_spawn"].update(parent_thread_id="other"))
        changed("other native path", lambda r: r["child"]["thread"]["source"]["subAgent"]["thread_spawn"].update(agent_path="/root/review_other"))
        changed("other checkout", lambda r: r["child"]["thread"].update(cwd="/other"))
        changed("incomplete turn", lambda r: r["turns"]["data"][0].update(status="inProgress"))
        changed("missing phase", lambda r: r["items"]["data"][0]["item"].pop("phase"))
        changed("other final turn", lambda r: r["items"]["data"][0].update(turnId="other"))
        changed("changed report", lambda r: r["items"]["data"][0]["item"].update(text=self.text.replace("b" * 64, "c" * 64)))
        changed("duplicate final", lambda r: r["items"]["data"].append(copy.deepcopy(r["items"]["data"][0])))
        changed("endless page", lambda r: r["items"].update(nextCursor="repeated"))
        for label, responses in cases:
            with self.subTest(label=label), self.assertRaises(StartupError):
                self.collect(responses)

    def test_malformed_envelopes_are_not_provenance(self):
        for text in ("not JSON", '{"version":1,"version":1}', "{}", self.text.replace('"findings": []', '"findings": true')):
            responses = copy.deepcopy(self.responses)
            responses["items"]["data"][0]["item"]["text"] = text
            with self.subTest(text=text), self.assertRaises(StartupError):
                self.collect(responses)

    def test_candidate_rechecked_after_fetch_and_errors_are_sanitized(self):
        module = types.ModuleType("runtime.evidence")
        module.prepared_native_review = unittest.mock.Mock(side_effect=[self.binding, StartupError("candidate changed")])
        with patch.dict(sys.modules, {"runtime.evidence": module}), patch.object(codex.shutil, "which", return_value=self.executable()):
            with self.assertRaises(StartupError):
                codex.collect_native_review(self.scope, "attempt", "parent-turn", "spawn")
        with self.assertRaisesRegex(StartupError, "Native review observation unavailable"):
            self.collect(mode="oversized")

    def test_pagination_is_bounded_and_latest_turn_is_rechecked(self):
        responses = copy.deepcopy(self.responses)
        responses["items_second"] = responses["items"]
        responses["items"] = dict(data=[], nextCursor="next-page")
        self.assertEqual(self.collect(responses)["report"], self.report)
        responses["turns_second"] = dict(data=[dict(id="later-turn", status="completed", error=None)])
        with self.assertRaises(StartupError):
            self.collect(responses)
        with patch.object(doctor, "RPC_TIMEOUT", 0.05), self.assertRaisesRegex(StartupError, "Native review observation unavailable"):
            self.collect(mode="slow")

    def test_large_tool_history_does_not_overflow_a_report_collection_frame(self):
        responses = copy.deepcopy(self.responses)
        responses['history'] = [dict(turnId='child-turn', item=dict(id='tool-' + str(i),
            type='commandExecution', aggregatedOutput='x' * 1536)) for i in range(120)]
        responses['history'].extend(responses['items']['data'])
        try:
            observed = self.collect(responses)
        except StartupError:
            self.fail('Bounded collection must retrieve the small report through large tool history')
        self.assertEqual(observed['report'], self.report)

    def test_large_individual_tool_items_preserve_transport_bounds(self):
        responses = copy.deepcopy(self.responses)
        responses['history'] = [dict(turnId='child-turn', item=dict(id='tool-' + str(i),
            type='commandExecution', aggregatedOutput='x' * (20 * 1024))) for i in range(16)]
        responses['history'].extend(responses['items']['data'])
        self.assertEqual(self.collect(responses)['report'], self.report)

    def test_paginated_report_collection_keeps_uniqueness_and_total_item_bound(self):
        for count, duplicate in ((120, True), (400, False)):
            responses = copy.deepcopy(self.responses)
            responses['history'] = [dict(turnId='child-turn', item=dict(id='tool-' + str(i),
                type='commandExecution', aggregatedOutput='ok')) for i in range(count)]
            responses['history'].extend(responses['items']['data'])
            if duplicate:
                responses['history'].insert(0, copy.deepcopy(responses['items']['data'][0]))
            with self.subTest(count=count, duplicate=duplicate), self.assertRaises(StartupError):
                self.collect(responses)

    def test_invalid_identifiers_do_not_launch_native_process(self):
        with self.prepared(), patch.object(codex, "NativeProcess") as process:
            for value in (None, "", "x" * 257, "line\nbreak"):
                with self.subTest(value=value), self.assertRaises(StartupError):
                    codex.collect_native_review(self.scope, "attempt", value, "spawn")
            process.assert_not_called()


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

    def observe_worker(self, responses=None, binding=None, changed=None, mode="normal", version="0.163.0"):
        defaults, prepared = self.worker_fixture()
        executable = self.executable(responses or defaults, mode, version)
        with self.prepared_worker(binding or prepared, changed), patch.object(codex.shutil, "which", return_value=executable):
            return codex.observe_native_worker(self.scope, "attempt", "parent-turn", "spawn")

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

    def observe_review_assignment(self, responses=None, binding=None, changed=None):
        defaults, prepared = self.review_assignment_fixture()
        executable = self.executable(responses or defaults)
        with self.prepared_review_assignment(binding or prepared, changed), patch.object(codex.shutil, "which", return_value=executable):
            return codex.observe_native_review_assignment(self.scope, attempt_id="attempt", parent_turn_id="parent-turn", spawn_item_id="spawn")

    def test_review_assignment_observation_preserves_unknown_and_incomplete_lifecycle(self):
        self.assertTrue(callable(getattr(codex, "observe_native_review_assignment", None)), "Review assignment lifecycle observation is unavailable")
        for status in (None, "inProgress", "interrupted", "failed"):
            responses, binding = self.review_assignment_fixture()
            if status is None:
                responses["turns"]["data"] = []
            else:
                responses["turns"]["data"][0]["status"] = status
            observed = self.observe_review_assignment(responses, binding)
            receipt = observed["receipt"]
            self.assertEqual(receipt["version"], 2)
            self.assertEqual(receipt["task_name"], "review_attempt")
            self.assertEqual(receipt["assignment_digest"], "d" * 64)
            self.assertEqual(receipt["turn_status"], status or "unknown")
            self.assertEqual(receipt["worker_turn_id"], "child-turn" if status else None)
            self.assertIsNone(receipt["result_item_id"])
            self.assertIsNone(receipt["result_digest"])
            self.assertIsNone(observed["result"])
            self.assertFalse(receipt["descendants_stopped"])
            self.assertEqual(receipt["prompt_delivery"], "unverified")
        methods = {json.loads(line)["method"] for line in self.log.read_text().splitlines()}
        self.assertLessEqual(methods, {"initialize", "initialized", "thread/read", "thread/items/read", "thread/turns/list"})

    def test_completed_review_assignment_returns_raw_result_and_d6_binding_without_d5_acceptance(self):
        self.assertTrue(callable(getattr(codex, "observe_native_review_assignment", None)), "Review assignment lifecycle observation is unavailable")
        responses, binding = self.review_assignment_fixture()
        responses["parent"]["thread"]["modelProvider"] = "parent-provider"
        responses["child"]["thread"]["modelProvider"] = "review-provider"
        for text in (self.text, "plain review output without a D5 report envelope"):
            responses["items"]["data"][0]["item"]["text"] = text
            observed = self.observe_review_assignment(responses, binding)
            receipt = observed["receipt"]
            self.assertEqual(observed["result"], text)
            self.assertEqual(receipt["result_digest"], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(receipt["assignment_digest"], "d" * 64)
            self.assertEqual(receipt["candidate_digest"], "b" * 64)
            self.assertEqual(receipt["worker_id"], "child")
            self.assertEqual(receipt["worker_turn_id"], "child-turn")
            self.assertEqual(receipt["result_item_id"], "final")
            self.assertEqual(receipt["agent_path"], "/root/review_attempt")
            self.assertEqual(receipt["parent_model_provider"], "parent-provider")
            self.assertEqual(receipt["worker_model_provider"], "review-provider")
            self.assertEqual(receipt["requested_model"], "requested-model")
            self.assertEqual(receipt["configured_model"], "configured-model")
            self.assertEqual(receipt["spawn_observed_model"], "gpt-6-luna")
            self.assertIsNone(receipt["execution_model"])
            self.assertEqual(self.observe_review_assignment(responses, binding), observed)

    def test_review_assignment_rejects_wrong_marker_binding_and_child(self):
        self.assertTrue(callable(getattr(codex, "observe_native_review_assignment", None)), "Review assignment lifecycle observation is unavailable")
        for label in ("worker marker", "wrong native marker", "wrong binding", "changed binding", "wrong child", "changed turn", "oversized result"):
            responses, binding = self.review_assignment_fixture()
            changed = None
            if label == "worker marker": binding["task_name"] = "worker_attempt"
            elif label == "wrong native marker": responses["spawn"]["data"][0]["item"]["agentPath"] = "/root/worker_attempt"
            elif label == "wrong binding": binding["attempt_id"] = "other"
            elif label == "changed binding": changed = dict(binding, candidate_digest="c" * 64)
            elif label == "wrong child": responses["child"]["thread"]["parentThreadId"] = "other"
            elif label == "changed turn": responses["turns_second"] = dict(data=[dict(id="other", status="completed", error=None)])
            else: responses["items"]["data"][0]["item"]["text"] = "x" * (64 * 1024 + 1)
            with self.subTest(label=label), self.assertRaises(StartupError):
                self.observe_review_assignment(responses, binding, changed)
        responses, binding = self.review_assignment_fixture()
        with self.assertRaises(StartupError):
            self.observe_worker(responses, binding)

    def test_workstream_creation_names_preserve_family_role_and_exact_native_lineage(self):
        cases = [(self.worker_fixture, self.observe_worker, "WS42.01", "worker_ws42_01_build_attempt"),
                 (self.review_assignment_fixture, self.observe_review_assignment,
                  "WS42.03", "review_ws42_03_review_attempt")]
        for fixture, observe, context, marker in cases:
            responses, binding = fixture()
            binding.update(workstream_context_id=context, task_name=marker)
            responses["spawn"]["data"][0]["item"]["agentPath"] = "/root/" + marker
            responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"]["agent_path"] = "/root/" + marker
            with self.subTest(marker=marker):
                try:
                    receipt = observe(responses, binding)["receipt"]
                except StartupError:
                    self.fail("Exact prepared Workstreams creation marker was rejected")
                self.assertEqual(receipt["task_name"], marker)
                self.assertEqual(receipt["agent_path"], "/root/" + marker)
                self.assertEqual(receipt["worker_id"], "child")
                self.assertNotIn("workstream_context_id", receipt)
        responses = copy.deepcopy(self.responses)
        marker = "review_ws42_03_review_attempt"
        responses["spawn"]["data"][0]["item"]["agentPath"] = "/root/" + marker
        responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"]["agent_path"] = "/root/" + marker
        binding = dict(self.binding, workstream_context_id="WS42.03", task_name=marker)
        try:
            receipt = self.collect(responses, binding=binding)["receipt"]
        except StartupError:
            self.fail("Exact prepared Workstreams D5 review marker was rejected")
        self.assertEqual(receipt["task_name"], marker)

    def test_workstream_creation_names_reject_other_attempt_family_role_or_unbound_context(self):
        for change in ("attempt", "family", "role", "context", "missing context", "legacy marker", "origin path"):
            responses, binding = self.worker_fixture()
            marker = "worker_ws42_01_build_attempt"
            binding.update(workstream_context_id="WS42.01", task_name=marker)
            if change == "attempt": marker = "worker_ws42_01_build_other"
            elif change == "family": marker = "worker_ws99_01_build_attempt"
            elif change == "role": marker = "worker_ws42_01_review_attempt"
            elif change == "context": binding["workstream_context_id"] = "WS42.02"
            elif change == "missing context": binding.pop("workstream_context_id")
            elif change == "legacy marker": marker = "worker_attempt"
            binding["task_name"] = marker
            responses["spawn"]["data"][0]["item"]["agentPath"] = "/root/" + marker
            responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"]["agent_path"] = "/root/" + marker
            if change == "origin path":
                responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"]["agent_path"] = "/root/worker_ws99_01_build_attempt"
            with self.subTest(change=change), self.assertRaises(StartupError):
                self.observe_worker(responses, binding)

    def test_native_task_names_normalize_without_truncating_or_losing_attempt_identity(self):
        self.assertTrue(callable(getattr(codex, "native_task_name", None)), "Shared native marker derivation is unavailable")
        self.assertEqual(codex.native_task_name("attempt", "worker"), "worker_attempt")
        self.assertEqual(codex.native_task_name("attempt", "review"), "review_attempt")
        self.assertEqual(codex.native_task_name("attempt", "worker", "WSAb42.01"), "worker_wsab42_01_build_attempt")
        self.assertEqual(codex.native_task_name("a" * 32, "review", "WS42.03"), "review_ws42_03_review_" + "a" * 32)
        for context in ("C12", "WS42.0", "WS42.00", "WS42.01.BUILD", "WS42/01", "WS42.01\n", "WS.01", "WS" + "a" * 17 + ".01", "WS42." + "1" * 17, 42, {}):
            with self.subTest(context=context), self.assertRaises(StartupError):
                codex.native_task_name("attempt", "worker", context)
        for attempt, role, context in (("a" * 64, "worker", "WS42.01"),
                                       ("other/path", "worker", "WS42.01"),
                                       ("UPPER", "worker", "WS42.01"),
                                       ("attempt", "coord", "WS42.01")):
            with self.subTest(attempt=attempt, role=role), self.assertRaises(StartupError):
                codex.native_task_name(attempt, role, context)

    def test_worker_observation_separates_settings_and_never_proves_stopped_writers(self):
        self.assertTrue(callable(getattr(codex, "observe_native_worker", None)), "Worker observation is unavailable")
        observed = self.observe_worker()
        receipt = observed["receipt"]
        self.assertEqual(receipt["version"], 2)
        self.assertEqual(receipt["worker_id"], "child")
        self.assertEqual(receipt["task_name"], "worker_attempt")
        self.assertEqual(receipt["requested_model"], "requested-model")
        self.assertEqual(receipt["requested_effort"], "low")
        self.assertEqual(receipt["configured_model"], "configured-model")
        self.assertEqual(receipt["configured_effort"], "high")
        self.assertEqual(receipt["spawn_observed_model"], "gpt-6-luna")
        self.assertEqual(receipt["spawn_observed_effort"], "medium")
        self.assertIsNone(receipt["execution_model"])
        self.assertIsNone(receipt["execution_effort"])
        self.assertFalse(receipt["descendants_stopped"])
        self.assertEqual(receipt["prompt_delivery"], "unverified")
        self.assertEqual(observed["result"], self.text)
        self.assertEqual(self.observe_worker(), observed)
        methods = {json.loads(line)["method"] for line in self.log.read_text().splitlines()}
        self.assertLessEqual(methods, codex.REVIEW_METHODS | {"initialized"})

    def test_worker_unknown_settings_and_incomplete_lifecycle_remain_unknown(self):
        responses, binding = self.worker_fixture()
        responses["child"]["thread"].pop("model")
        responses["child"]["thread"].pop("reasoningEffort")
        responses["spawn"]["data"][0]["item"].pop("model")
        responses["spawn"]["data"][0]["item"].pop("reasoningEffort")
        for status in ("inProgress", "interrupted", "failed"):
            responses["turns"]["data"][0]["status"] = status
            observed = self.observe_worker(responses, binding)
            self.assertEqual(observed["receipt"]["turn_status"], status)
            self.assertIsNone(observed["result"])
            for field in ("configured_model", "configured_effort", "spawn_observed_model", "spawn_observed_effort"):
                self.assertIsNone(observed["receipt"][field])
        responses["turns"]["data"] = []
        observed = self.observe_worker(responses, binding)
        self.assertEqual(observed["receipt"]["turn_status"], "unknown")
        self.assertIsNone(observed["receipt"]["worker_turn_id"])
        self.assertIsNone(observed["result"])

    def test_assignment_failure_preserves_only_recognized_codes_and_explicit_retry_intent(self):
        cases = [
            ("serverOverloaded", True, "serverOverloaded"),
            ("rateLimitExceeded", False, "rateLimitExceeded"),
            ("usageLimitExceeded", None, "usageLimitExceeded"),
            ("internalServerError", False, "internalServerError"),
            ({"httpConnectionFailed": {"httpStatusCode": 503}}, None, "httpConnectionFailed"),
            ({"responseStreamConnectionFailed": {}}, None, "responseStreamConnectionFailed"),
            ({"responseStreamDisconnected": {}}, None, "responseStreamDisconnected"),
            ({"responseTooManyFailedAttempts": {}}, False, "responseTooManyFailedAttempts"),
            ({"activeTurnNotSteerable": {"turnKind": "review"}}, None, "activeTurnNotSteerable"),
        ]
        for info, retry, expected in cases:
            responses, binding = self.worker_fixture()
            turn = responses["turns"]["data"][0]
            turn.update(status="failed", error=dict(codexErrorInfo=info,
                message="secret-token", additionalDetails="secret-details"))
            if retry is not None:
                turn["willRetry"] = retry
            with self.subTest(info=info, retry=retry):
                observed = self.observe_worker(responses, binding)
                self.assertIn("failure_code", observed["receipt"])
                self.assertEqual(observed["receipt"]["failure_code"], expected)
                self.assertIs(observed["receipt"]["will_retry"], retry)
                self.assertNotIn("secret", json.dumps(observed))
                self.assertNotIn("httpStatusCode", json.dumps(observed))
                self.assertIsNone(observed["result"])

    def test_assignment_unknown_failure_and_retry_intent_are_never_inferred(self):
        cases = [None, {}, "serverOverloaded", {"message": "rateLimitExceeded"},
                 {"codexErrorInfo": "unknown-secret"}, {"codexErrorInfo": "x" * 4097},
                 {"codexErrorInfo": ["serverOverloaded"]},
                 {"codexErrorInfo": {"serverOverloaded": {}}},
                 {"codexErrorInfo": {"httpConnectionFailed": "secret"}},
                 {"codexErrorInfo": {"httpConnectionFailed": {}, "other": {}}}]
        for error in cases:
            responses, binding = self.review_assignment_fixture()
            responses["turns"]["data"][0].update(status="failed", error=error, willRetry="false")
            with self.subTest(error=error):
                receipt = self.observe_review_assignment(responses, binding)["receipt"]
                self.assertIn("failure_code", receipt)
                self.assertIsNone(receipt["failure_code"])
                self.assertIsNone(receipt["will_retry"])
        for status in (None, "inProgress", "interrupted", "failed", "completed"):
            responses, binding = self.worker_fixture()
            if status is None:
                responses["turns"]["data"] = []
            else:
                responses["turns"]["data"][0]["status"] = status
            with self.subTest(status=status):
                receipt = self.observe_worker(responses, binding)["receipt"]
                self.assertIn("failure_code", receipt)
                self.assertIsNone(receipt["failure_code"])
                self.assertIsNone(receipt["will_retry"])

    def test_assignment_rechecks_normalized_failure_and_host_retry_intent(self):
        for changed in ({"error": {"codexErrorInfo": "rateLimitExceeded"}}, {"willRetry": False}):
            responses, binding = self.worker_fixture()
            responses["turns"]["data"][0].update(status="failed",
                error={"codexErrorInfo": "serverOverloaded"}, willRetry=True)
            responses["turns_second"] = copy.deepcopy(responses["turns"])
            responses["turns_second"]["data"][0].update(changed)
            with self.subTest(changed=changed), self.assertRaises(StartupError):
                self.observe_worker(responses, binding)

    def test_worker_observation_accepts_observed_desktop_surface_tuple(self):
        responses, binding = self.worker_fixture()
        binding.update(client_surface="codex-desktop", native_version="0.164.0-alpha.11.1")
        for identity in ("parent", "child"):
            responses[identity]["thread"]["cliVersion"] = "0.164.0-alpha.11.1"
        responses["parent"]["thread"].update(originator="Codex Desktop", source="vscode")
        try:
            observed = self.observe_worker(responses, binding, version="0.164.0-alpha.11.1")
        except StartupError as error:
            self.fail("Observed Desktop worker surface was rejected: " + str(error))
        self.assertEqual(observed["receipt"]["client_surface"], "codex-desktop")
        self.assertEqual(observed["receipt"]["originator"], "Codex Desktop")
        for source in ("appServer", "cli", "exec", "unknown"):
            responses["parent"]["thread"]["source"] = source
            with self.subTest(source=source), self.assertRaises(StartupError):
                self.observe_worker(responses, binding, version="0.164.0-alpha.11.1")
        responses["parent"]["thread"].update(source="vscode", originator="unknown")
        with self.assertRaises(StartupError):
            self.observe_worker(responses, binding, version="0.164.0-alpha.11.1")

    def test_worker_rejects_unbound_identity_surface_and_changed_assignment(self):
        responses, binding = self.worker_fixture()
        cases = []
        for field, value in (("task_name", "worker_other"), ("attempt_id", "other"),
                             ("native_version", None), ("client_surface", "codex-desktop"),
                             ("assignment_digest", "bad"), ("candidate_digest", "bad")):
            other = dict(binding, **{field: value})
            cases.append((responses, other, None))
        for field, value in (("originator", "Codex Desktop"), ("cwd", "/other")):
            other = copy.deepcopy(responses)
            other["parent"]["thread"][field] = value
            cases.append((other, binding, None))
        changed = dict(binding, candidate_digest="c" * 64)
        cases.append((responses, binding, changed))
        for observed, prepared, changed in cases:
            with self.subTest(binding=prepared, changed=changed), self.assertRaises(StartupError):
                self.observe_worker(observed, prepared, changed)

    def test_worker_bounds_results_history_and_turn_changes(self):
        for label in ("too large", "duplicate final", "changed turn", "endless page", "wrong child"):
            responses, binding = self.worker_fixture()
            if label == "too large":
                responses["items"]["data"][0]["item"]["text"] = "x" * (64 * 1024 + 1)
            elif label == "duplicate final":
                responses["items"]["data"].append(copy.deepcopy(responses["items"]["data"][0]))
            elif label == "changed turn":
                responses["turns_second"] = dict(data=[dict(id="other", status="completed", error=None)])
            elif label == "endless page":
                responses["items"]["nextCursor"] = "repeat"
            else:
                responses["child"]["thread"]["parentThreadId"] = "other"
            with self.subTest(label=label), self.assertRaises(StartupError):
                self.observe_worker(responses, binding)
        with patch.object(doctor, "RPC_TIMEOUT", 0.05), self.assertRaises(StartupError):
            self.observe_worker(mode="slow")

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

    def test_discovery_binds_catalog_controls_surface_and_version_without_dispatch(self):
        self.assertTrue(callable(getattr(codex, "discover_native_workers", None)), "Worker discovery is unavailable")
        observed = self.discover_workers()
        self.assertEqual(observed["catalog"], [dict(model="new-native-model", supported_efforts=["low", "high"], default_effort="low")])
        self.assertEqual(observed["client_surface"], "codex-cli")
        self.assertEqual(observed["native_version"], "0.163.0")
        self.assertEqual(len(observed["capability_digest"]), 64)
        self.assertEqual(self.discover_workers(), observed)
        responses, controls = self.control_fixture()
        controls["dispatch_controls"]["explicit_effort"] = False
        self.assertNotEqual(self.discover_workers(responses, controls)["capability_digest"], observed["capability_digest"])
        methods = {json.loads(line)["method"] for line in self.log.read_text().splitlines()}
        self.assertEqual(methods, {"initialize", "initialized", "thread/read", "model/list"})

    def test_reader_update_keeps_capability_digest_but_catalog_or_controls_changes_do_not(self):
        responses, controls = self.control_fixture()
        original = self.discover_workers(responses, controls)
        updated_controls = dict(controls)
        updated = self.discover_workers(responses, updated_controls, version="9.999.0")
        self.assertEqual(original["native_version"], "0.163.0")
        self.assertEqual(updated["native_version"], "9.999.0")
        self.assertEqual(updated["capability_digest"], original["capability_digest"])
        changed = copy.deepcopy(responses)
        changed["catalog"]["data"][0]["supportedReasoningEfforts"].append(dict(reasoningEffort="xhigh"))
        self.assertNotEqual(self.discover_workers(changed, updated_controls, version="9.999.0")["capability_digest"],
                            updated["capability_digest"])
        changed_controls = copy.deepcopy(updated_controls)
        changed_controls["dispatch_controls"]["explicit_effort"] = False
        self.assertNotEqual(self.discover_workers(responses, changed_controls, version="9.999.0")["capability_digest"],
                            updated["capability_digest"])
        self.assertEqual(controls["native_version"], "0.163.0")

    def test_discovery_rejects_unqualified_surface_and_malformed_or_unbounded_catalog(self):
        for label in ("desktop", "version metadata", "originator", "control type", "duplicate", "effort", "page loop"):
            responses, controls = self.control_fixture()
            if label == "desktop": controls["client_surface"] = "codex-desktop"
            elif label == "version metadata": controls["native_version"] = None
            elif label == "originator": responses["parent"]["thread"]["originator"] = "Codex Desktop"
            elif label == "control type": controls["dispatch_controls"]["spawn"] = "true"
            elif label == "duplicate": responses["catalog"]["data"] *= 2
            elif label == "effort": responses["catalog"]["data"][0]["defaultReasoningEffort"] = "unsupported"
            else: responses["catalog"]["nextCursor"] = "repeat"
            with self.subTest(label=label), self.assertRaises(StartupError):
                self.discover_workers(responses, controls)
        responses, controls = self.control_fixture()
        responses["catalog_second"] = responses["catalog"]
        responses["catalog"] = dict(data=[], nextCursor="next-page")
        self.assertEqual(len(self.discover_workers(responses, controls)["catalog"]), 1)

    def test_desktop_catalog_preserves_surface_binding_across_reader_updates(self):
        responses, controls = self.control_fixture()
        controls.update(client_surface="codex-desktop", native_version="0.164.0-alpha.11.1")
        responses["parent"]["thread"].update(cliVersion="0.164.0-alpha.11.1", source="vscode", originator="Codex Desktop")
        executable = self.executable(responses, version="0.164.0-alpha.11.1")
        with patch.object(codex.shutil, "which", return_value=executable):
            try:
                observed = codex.discover_native_workers(self.scope, controls)
            except StartupError as error:
                self.fail("Observed Desktop catalog was rejected: " + str(error))
        self.assertEqual(observed["client_surface"], "codex-desktop")
        self.assertEqual(observed["native_version"], "0.164.0-alpha.11.1")
        self.assertEqual(observed["originator"], "Codex Desktop")
        self.assertEqual(observed["catalog"], [dict(model="new-native-model", supported_efforts=["low", "high"], default_effort="low")])
        for source in ("appServer", "cli", "exec", "unknown"):
            responses["parent"]["thread"]["source"] = source
            with self.subTest(source=source), self.assertRaises(StartupError):
                self.discover_workers(responses, controls, version="0.164.0-alpha.11.1")
        responses["parent"]["thread"]["source"] = "vscode"
        for version in ("0.163.0", "9.999.0"):
            with self.subTest(version=version):
                updated = self.discover_workers(responses, controls, version=version)
                self.assertEqual(updated["native_version"], version)
                self.assertEqual(updated["capability_digest"], observed["capability_digest"])
        controls["client_surface"] = "codex-cli"
        with self.assertRaises(StartupError):
            self.discover_workers(responses, controls, version="0.164.0-alpha.11.1")
        controls.update(client_surface="codex-desktop", native_version="0.163.0")
        responses["parent"]["thread"]["cliVersion"] = "0.163.0"
        self.assertEqual(self.discover_workers(responses, controls, version="0.163.0")["client_surface"], "codex-desktop")

    def test_work_desktop_originator_preserves_live_surface_and_identity_checks(self):
        responses, controls = self.current_desktop_fixture(self.control_fixture)
        responses['parent']['thread']['originator']='codex_work_desktop'
        observed=self.discover_workers(responses,controls,version='0.165.0')
        self.assertEqual(observed['originator'],'codex_work_desktop')
        for source in ('exec','unknown',None):
            changed=copy.deepcopy(responses)
            changed['parent']['thread']['source']=source
            with self.assertRaises(StartupError):
                self.discover_workers(changed,controls,version='0.165.0')

    def current_desktop_fixture(self, fixture):
        responses, binding = fixture()
        binding.update(client_surface="codex-desktop", native_version="0.165.0")
        for identity in ("parent", "child"):
            responses[identity]["thread"]["cliVersion"] = "0.165.0-alpha.1.2"
        responses["parent"]["thread"].update(source="vscode", originator="Codex Desktop")
        return responses, binding

    def test_current_desktop_discovery_distinguishes_observer_from_creation_version(self):
        responses, controls = self.current_desktop_fixture(self.control_fixture)
        try:
            observed = self.discover_workers(responses, controls, version="0.165.0")
        except StartupError:
            self.fail("Proven observer/creation version pair was rejected")
        self.assertEqual(observed["native_version"], "0.165.0")
        self.assertEqual(observed["client_surface"], "codex-desktop")
        self.assertEqual(observed["originator"], "Codex Desktop")
        for field, value in (("cliVersion", None), ("cliVersion", ""),
                             ("cliVersion", {}), ("source", "exec"),
                             ("originator", "codex_exec"), ("originator", "Codex desktop"),
                             ("cwd", "/other")):
            changed = copy.deepcopy(responses)
            changed["parent"]["thread"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(StartupError):
                self.discover_workers(changed, controls, version="0.165.0")
        for reader in ("0.163.0", "0.164.0-alpha.11.1", "0.165.0-alpha.1.2", "9.999.0"):
            changed = dict(controls, native_version=reader)
            with self.subTest(reader=reader):
                self.assertEqual(self.discover_workers(responses, changed, version=reader)["native_version"], reader)
        controls["client_surface"] = "codex-cli"
        with self.assertRaises(StartupError):
            self.discover_workers(responses, controls, version="0.165.0")

    def test_current_desktop_native_chain_requires_exact_identity_with_independent_creation_versions(self):
        responses, binding = self.current_desktop_fixture(self.worker_fixture)
        try:
            receipt = self.observe_worker(responses, binding, version="0.165.0")["receipt"]
        except StartupError:
            self.fail("Proven native chain observer/creation version pair was rejected")
        self.assertEqual(receipt["native_version"], "0.165.0")
        self.assertEqual(receipt["worker_id"], "child")
        self.assertEqual(receipt["turn_status"], "completed")
        for creation in ("0.165.0", "0.163.0", "9.999.0-alpha.42"):
            changed = copy.deepcopy(responses)
            changed["child"]["thread"]["cliVersion"] = creation
            with self.subTest(creation=creation):
                receipt = self.observe_worker(changed, binding, version="0.165.0")["receipt"]
                self.assertEqual(receipt["worker_creation_version"], creation)
        for field, value in (("cliVersion", None), ("cliVersion", ""),
                             ("cliVersion", "line\nbreak"), ("cwd", "/other"),
                             ("parentThreadId", "other")):
            changed = copy.deepcopy(responses)
            changed["child"]["thread"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(StartupError):
                self.observe_worker(changed, binding, version="0.165.0")
        changed = copy.deepcopy(responses)
        changed["spawn"]["data"][0]["item"]["agentPath"] = "/root/worker_other"
        with self.assertRaises(StartupError):
            self.observe_worker(changed, binding, version="0.165.0")
        changed = copy.deepcopy(responses)
        changed["turns_second"] = dict(data=[dict(id="other", status="completed", error=None)])
        with self.assertRaises(StartupError):
            self.observe_worker(changed, binding, version="0.165.0")

    def test_current_desktop_d5_review_uses_proven_creation_pair_and_exact_surface(self):
        responses, _ = self.current_desktop_fixture(lambda: (copy.deepcopy(self.responses), {}))
        try:
            observed = self.collect(responses, version="0.165.0")
        except StartupError:
            self.fail("Proven D5 observer/creation version pair was rejected")
        self.assertEqual(observed["report"], self.report)
        self.assertEqual(observed["receipt"]["native_version"], "0.165.0")
        for field, value in (("source", "exec"), ("originator", "codex_exec"), ("cliVersion", None)):
            changed = copy.deepcopy(responses)
            changed["parent"]["thread"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(StartupError):
                self.collect(changed, version="0.165.0")

    def test_d5_review_rejects_consistent_surface_switch_from_admitted_binding(self):
        responses, _ = self.current_desktop_fixture(lambda: (copy.deepcopy(self.responses), {}))
        binding = dict(self.binding, native_version="0.165.0", client_surface="codex-desktop")
        self.assertEqual(self.collect(responses, binding=binding, version="9.999.0")["report"], self.report)
        responses["parent"]["thread"].update(source="exec", originator="codex_exec")
        with self.assertRaises(StartupError):
            self.collect(responses, binding=binding, version="9.999.0")

    def test_cli_surface_requires_exact_observed_standalone_exec_tuple(self):
        responses, controls = self.control_fixture()
        responses["parent"]["thread"].update(cliVersion="0.163.0", source="exec", originator="codex_exec", modelProvider="openai")
        try:
            observed = self.discover_workers(responses, controls)
        except StartupError as error:
            self.fail("Observed standalone CLI exec surface was rejected: " + str(error))
        self.assertEqual(observed["native_version"], "0.163.0")
        self.assertEqual(observed["originator"], "codex_exec")
        self.assertEqual(observed["parent_model_provider"], "openai")
        for source in ("cli", "vscode", "appServer", "unknown"):
            responses["parent"]["thread"]["source"] = source
            with self.subTest(source=source), self.assertRaises(StartupError):
                self.discover_workers(responses, controls)
        responses["parent"]["thread"]["source"] = "exec"
        for originator in ("codex_cli_rs", "Codex Desktop", "other", None):
            responses["parent"]["thread"]["originator"] = originator
            with self.subTest(originator=originator), self.assertRaises(StartupError):
                self.discover_workers(responses, controls)
        responses["parent"]["thread"]["originator"] = "codex_exec"
        for version in ("0.164.0-alpha.11.1", "9.999.0"):
            controls["native_version"] = version
            responses["parent"]["thread"]["cliVersion"] = version
            with self.subTest(version=version):
                self.assertEqual(self.discover_workers(responses, controls, version=version)["native_version"], version)

    def test_updated_reader_keeps_admitted_worker_and_review_versions_without_rebinding(self):
        responses, binding = self.current_desktop_fixture(self.worker_fixture)
        responses["child"]["thread"]["cliVersion"] = "0.166.0-alpha.1"
        try:
            receipt = self.observe_worker(responses, binding, version="9.999.0")["receipt"]
        except StartupError:
            self.fail("Reader update rejected the unchanged admitted assignment")
        self.assertEqual(receipt["native_version"], "0.165.0")
        self.assertEqual(receipt["current_observer_version"], "9.999.0")
        self.assertEqual(receipt["parent_creation_version"], "0.165.0-alpha.1.2")
        self.assertEqual(receipt["worker_creation_version"], "0.166.0-alpha.1")
        self.assertEqual(receipt["assignment_digest"], binding["assignment_digest"])
        self.assertEqual(receipt["worker_id"], "child")
        responses, _ = self.current_desktop_fixture(lambda: (copy.deepcopy(self.responses), {}))
        binding = dict(self.binding, native_version="0.165.0")
        receipt = self.collect(responses, binding=binding, version="9.999.0")["receipt"]
        self.assertEqual(receipt["native_version"], "0.165.0")
        self.assertEqual(receipt["current_observer_version"], "9.999.0")

    def test_live_initialization_contract_rejects_missing_malformed_or_changed_shapes(self):
        for initialized in ({}, {"codexHome": "/private/native"},
                dict(codexHome=True, platformFamily="unix", platformOs="macos", userAgent="fixture"),
                dict(codexHome="/private/native", platformFamily="unix", platformOs="macos", userAgent="")):
            responses, binding = self.worker_fixture()
            responses["initialize"] = initialized
            with self.subTest(initialized=initialized), self.assertRaises(StartupError):
                self.observe_worker(responses, binding)

    def test_provider_metadata_preserves_unknown_and_differing_native_values(self):
        discovered = self.discover_workers()
        self.assertIn("parent_model_provider", discovered)
        self.assertIsNone(discovered["parent_model_provider"])
        receipt = self.observe_worker()["receipt"]
        self.assertIn("parent_model_provider", receipt)
        self.assertIn("worker_model_provider", receipt)
        self.assertIsNone(receipt["parent_model_provider"])
        self.assertIsNone(receipt["worker_model_provider"])
        responses, controls = self.control_fixture()
        responses["parent"]["thread"]["modelProvider"] = "parent-provider"
        changed = self.discover_workers(responses, controls)
        self.assertEqual(changed["parent_model_provider"], "parent-provider")
        self.assertNotEqual(changed["capability_digest"], discovered["capability_digest"])
        responses, binding = self.worker_fixture()
        responses["parent"]["thread"]["modelProvider"] = "parent-provider"
        responses["child"]["thread"]["modelProvider"] = "worker-provider"
        receipt = self.observe_worker(responses, binding)["receipt"]
        self.assertEqual(receipt["parent_model_provider"], "parent-provider")
        self.assertEqual(receipt["worker_model_provider"], "worker-provider")
        responses["parent"]["thread"]["modelProvider"] = None
        responses["child"]["thread"]["modelProvider"] = None
        receipt = self.observe_worker(responses, binding)["receipt"]
        self.assertIsNone(receipt["parent_model_provider"])
        self.assertIsNone(receipt["worker_model_provider"])

    def test_canonical_linked_native_observation_binds_original_projection_and_selected_cwd(self):
        from runtime import state
        from runtime.startup import _canonical_plain
        from tests.test_evidence import EvidenceTests
        f = EvidenceTests(methodName='runTest')
        f._canonical_linked = True
        f.setUp(); self.addCleanup(f.doCleanups)
        self.scope = f.scope
        linked = f.repo
        responses, binding = self.worker_fixture()
        for name in ('parent', 'child'): responses[name]['thread']['cwd'] = str(linked)
        binding.update(pins=state._pins(self.scope), accepted_identity=_canonical_plain(self.scope.accepted_identity),
                       scope_id=state._scope_id(self.scope))
        result = self.observe_worker(responses, binding)
        self.assertEqual(result['receipt']['checkout'], str(linked))
        self.assertIsNone(result['receipt']['execution_model'])
        changed = copy.deepcopy(binding)
        changed['accepted_identity']['checkout_id'] = 'other-checkout'
        with self.assertRaises(StartupError): self.observe_worker(responses, changed)
        responses['child']['thread']['cwd'] = str(f.scope.primary_root)
        with self.assertRaises(StartupError): self.observe_worker(responses, binding)


    def test_present_provider_metadata_requires_bounded_printable_strings(self):
        for value in ("", "x" * 257, "provider\nname", True, {}):
            responses, controls = self.control_fixture()
            responses["parent"]["thread"]["modelProvider"] = value
            with self.subTest(value=value, source="catalog"), self.assertRaises(StartupError):
                self.discover_workers(responses, controls)
            for identity in ("parent", "child"):
                responses, binding = self.worker_fixture()
                responses[identity]["thread"]["modelProvider"] = value
                with self.subTest(value=value, source=identity), self.assertRaises(StartupError):
                    self.observe_worker(responses, binding)


if __name__ == "__main__":
    unittest.main()
