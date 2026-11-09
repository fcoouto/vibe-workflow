"""Bounded persisted-history receipts never trust caller claims or execute review."""

import copy
import hashlib
import json
import sys
import time
import types
import unittest
from unittest.mock import patch

from runtime import doctor, evidence
from runtime.adapters import codex
from runtime.startup import StartupError
from tests.fixture_native_reviews import NativeReviewFixture


class NativeReviewReceiptTests(NativeReviewFixture, unittest.TestCase):
    def test_v1_collab_call_correlates_prepared_assignment_without_inventing_agent_path(self):
        for review in (False, True):
            responses, binding = self.v1_fixture(review)
            observed = (self.observe_review_assignment(responses, binding) if review else
                        self.observe_worker(responses, binding))
            receipt = observed["receipt"]
            self.assertEqual(receipt["mechanism"], "codex-native-collab-call")
            self.assertIsNone(receipt["agent_path"])
            self.assertEqual((receipt["parent_session_id"], receipt["parent_turn_id"], receipt["spawn_item_id"],
                              receipt["worker_id"], receipt["worker_turn_id"], receipt["result_item_id"]),
                             ("parent", "parent-turn", "spawn", "child", "child-turn", "final"))
            self.assertEqual(receipt["assignment_digest"], binding["assignment_digest"])
            self.assertEqual(receipt["requested_model"], "requested-model")
            self.assertEqual(receipt["configured_model"], "configured-model")
            self.assertEqual(receipt["spawn_observed_model"], "gpt-6-luna")
            self.assertEqual(receipt["native_version"], "0.163.0")
            self.assertEqual(receipt["current_observer_version"], "0.163.0")
            self.assertEqual(observed["result"], self.text)
            self.assertIsNone(receipt["execution_model"])
            self.assertIsNone(receipt["execution_effort"])
            self.assertFalse(receipt["descendants_stopped"])
            self.assertEqual(receipt["prompt_delivery"], "unverified")
        responses, lifecycle_binding = self.v1_fixture(review=True)
        binding = self.binding | dict(ledger_assignment_digest=lifecycle_binding["assignment_digest"])
        receipt = self.collect(responses, binding=binding)["receipt"]
        self.assertEqual(receipt["mechanism"], "codex-native-collab-call")
        self.assertIsNone(receipt["agent_path"])
        self.assertEqual(receipt["assignment_digest"], self.binding["assignment_digest"])
        self.assertNotEqual(receipt["assignment_digest"], binding["ledger_assignment_digest"])
        responses["child"]["thread"]["source"]["subAgent"]["thread_spawn"]["agent_path"] = "/actual/native/path"
        self.assertEqual(self.collect(responses, binding=binding)["receipt"]["agent_path"], "/actual/native/path")
        self.assertEqual(self.collect()["receipt"]["mechanism"], "codex-native-agent-path")

    def test_v1_large_prepared_review_uses_bounded_real_subprocess_transport(self):
        responses, lifecycle_binding = self.v1_fixture(review=True)
        assignment = "x" * 65536
        plaintext_digest = hashlib.sha256(assignment.encode()).hexdigest()
        spawn = responses["spawn"]["data"][0]["item"]
        envelope = json.loads(spawn["prompt"])
        envelope["assignment"] = envelope["ownership"]["request"]["assignment"] = assignment
        ledger_digest = evidence._digest(envelope["ownership"])
        envelope["ledger_assignment_digest"] = ledger_digest
        spawn["prompt"] = json.dumps(envelope)
        report = self.report | dict(assignment_digest=plaintext_digest)
        text = json.dumps(report)
        responses["items"]["data"][0]["item"]["text"] = text
        binding = self.binding | dict(assignment=assignment, assignment_digest=plaintext_digest,
                                      ledger_assignment_digest=ledger_digest)
        initialization = dict(codexHome="/private/native", platformFamily="unix", platformOs="macos", userAgent="fixture")
        results = [initialization, responses["parent"], responses["spawn"], responses["child"],
                   responses["turns"], responses["items"], responses["turns"]]
        frames = [json.dumps(dict(id=number, result=result)).encode() + b"\n"
                  for number, result in enumerate(results, 1)]
        self.assertGreater(len(spawn["prompt"].encode()), doctor.MAX_INPUT)
        self.assertGreater(len(frames[2]), doctor.MAX_INPUT)
        self.assertLess(sum(map(len, frames)), doctor.MAX_OUTPUT)
        self.assertNotEqual(plaintext_digest, ledger_digest)
        collected = self.collect(responses, binding=binding)
        self.assertEqual(collected["report"], report)
        self.assertEqual(collected["receipt"]["assignment_digest"], plaintext_digest)
        self.assertEqual(collected["receipt"]["mechanism"], "codex-native-collab-call")
        self.assertIsNone(collected["receipt"]["agent_path"])
        lifecycle_binding["assignment_digest"] = ledger_digest
        observed = self.observe_review_assignment(responses, lifecycle_binding)
        self.assertEqual(observed["receipt"]["assignment_digest"], ledger_digest)
        self.assertEqual(observed["result"], text)
        self.assertFalse(observed["receipt"]["descendants_stopped"])

    def test_native_selected_response_limit_preserves_default_notification_and_total_bounds(self):
        params = dict(threadId="parent", turnId="parent-turn", itemIds=["spawn"])
        cases = (("default128", 128 * 1024, None, "normal"),
                 ("selected-over512", 512 * 1024, doctor.MAX_OUTPUT, "normal"),
                 ("notification-over128", 1, doctor.MAX_OUTPUT, "oversized-notification"))
        for label, size, limit, mode in cases:
            responses = copy.deepcopy(self.responses)
            responses["spawn"] = dict(data="x" * size)
            notifications = []
            process = doctor.NativeProcess([self.executable(responses, mode)], time.monotonic() + 5,
                                           on_notification=notifications.append)
            try:
                kwargs = {} if limit is None else dict(response_limit=limit)
                with self.subTest(case=label), self.assertRaises(doctor.ObservationError):
                    process.request(1, "thread/items/read", params, **kwargs)
                self.assertEqual(notifications, [])
            finally:
                process.close()
        responses = copy.deepcopy(self.responses)
        responses["spawn"] = dict(data="x" * (270 * 1024))
        frame = json.dumps(dict(id=1, result=responses["spawn"])).encode() + b"\n"
        self.assertLess(len(frame), doctor.MAX_OUTPUT)
        self.assertGreater(2 * len(frame), doctor.MAX_OUTPUT)
        process = doctor.NativeProcess([self.executable(responses)], time.monotonic() + 5)
        try:
            self.assertEqual(process.request(1, "thread/items/read", params, response_limit=doctor.MAX_OUTPUT), responses["spawn"])
            self.assertGreater(process.bytes_read, doctor.MAX_INPUT)
            with self.assertRaisesRegex(doctor.ObservationError, "output_limit"):
                process.request(2, "thread/items/read", params, response_limit=doctor.MAX_OUTPUT)
            self.assertGreater(process.bytes_read, doctor.MAX_OUTPUT)
        finally:
            process.close()
        process = doctor.NativeProcess([self.executable()], time.monotonic() + 5)
        try:
            with self.assertRaises(doctor.ObservationError):
                process.request(1, "thread/items/read", params, response_limit=doctor.MAX_OUTPUT + 1)
        finally:
            process.close()
        responses = copy.deepcopy(self.responses)
        responses["parent"]["padding"] = "x" * (128 * 1024)
        with patch.object(codex.shutil, "which", return_value=self.executable(responses)):
            with codex._native_reader(codex.REVIEW_METHODS) as (_, request):
                with self.assertRaises(doctor.ObservationError):
                    request("thread/read", dict(threadId="parent", includeTurns=False))

    def test_v1_collab_call_mismatch_fails_closed(self):
        for review in (False, True):
            baseline, binding = self.v1_fixture(review)
            cases = []
            def changed(label, mutate):
                responses = copy.deepcopy(baseline)
                mutate(responses)
                cases.append((label, responses))
            changed("parent turn", lambda r: r["spawn"]["data"][0].update(turnId="other"))
            changed("call", lambda r: r["spawn"]["data"][0]["item"].update(id="other"))
            for field, values in (("senderThreadId", (None, "other")), ("tool", (None, "sendInput")),
                                  ("status", ("failed", "inProgress", None)),
                                  ("receiverThreadIds", ([], ["child", "other"], ["parent"], "child", [None])),
                                  ("prompt", (None, "not JSON", "{}", '{"assignment":{},"assignment":{}}'))):
                for value in values:
                    changed(field + repr(value), lambda r, field=field, value=value:
                            r["spawn"]["data"][0]["item"].update({field: value}))
            for field, value in (("id", "other"), ("parentThreadId", "other"), ("cwd", "/other"),
                                  ("source", None)):
                changed("child " + field, lambda r, field=field, value=value: r["child"]["thread"].update({field: value}))
            changed("independent origin", lambda r:
                    r["child"]["thread"]["source"]["subAgent"]["thread_spawn"].update(parent_thread_id="other"))
            changed("caller origin only", lambda r: (r["child"]["thread"].pop("source"),
                    r["spawn"]["data"][0]["item"].update(threadSource={"parent_thread_id": "parent"}, parentTurnId="parent-turn")))
            for value in (False, "", "relative/path", "x" * 4097, "line\nbreak"):
                changed("bad actual path", lambda r, value=value:
                        r["child"]["thread"]["source"]["subAgent"]["thread_spawn"].update(agent_path=value))
            for mutation in ("digest", "body", "name", "extra", "outer"):
                responses = copy.deepcopy(baseline)
                spawn = responses["spawn"]["data"][0]["item"]
                envelope = json.loads(spawn["prompt"])
                body = envelope["ownership" if review else "assignment"]
                if mutation == "digest":
                    envelope["ledger_assignment_digest" if review else "assignment_digest"] = "f" * 64
                elif mutation == "body":
                    body["request"]["assignment"] = "Changed immutable request"
                elif mutation == "name":
                    body["binding"]["task_name"] = "other"
                elif mutation == "extra":
                    envelope["unprepared"] = True
                elif review:
                    envelope["assignment"] = "Changed outer assignment"
                else:
                    envelope["assignment_digest"] = None
                spawn["prompt"] = json.dumps(envelope)
                cases.append((mutation, responses))
            if review:
                responses = copy.deepcopy(baseline)
                spawn = responses["spawn"]["data"][0]["item"]
                envelope = json.loads(spawn["prompt"])
                envelope["report_contract"]["coverage"] = ["other"]
                spawn["prompt"] = json.dumps(envelope)
                cases.append(("altered outer report contract", responses))
            # Even a matching digest cannot replace the generated immutable task name.
            responses = copy.deepcopy(baseline)
            envelope = json.loads(responses["spawn"]["data"][0]["item"]["prompt"])
            body = envelope["ownership" if review else "assignment"]
            body["binding"]["task_name"] = "other"
            digest = evidence._digest(body)
            envelope["ledger_assignment_digest" if review else "assignment_digest"] = digest
            responses["spawn"]["data"][0]["item"]["prompt"] = json.dumps(envelope)
            observer = self.observe_review_assignment if review else self.observe_worker
            with self.subTest(review=review, case="matching digest wrong name"), self.assertRaises(StartupError):
                observer(responses, binding | dict(assignment_digest=digest))
            for label, responses in cases:
                with self.subTest(review=review, case=label), self.assertRaises(StartupError):
                    observer(responses, binding)
        malformed_v2, binding = self.worker_fixture()
        malformed_v2["spawn"]["data"][0]["item"]["type"] = "collabAgentToolCall"
        with self.assertRaises(StartupError):
            self.observe_worker(malformed_v2, binding)

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


if __name__ == "__main__":
    unittest.main()
