"""The strict masking of recorded ChatGPT request facts (account identifiers, credentials, key names), the leak check
that withholds facts, guard blocks reported apart from upstream refusals, and hard links in OpenCode's tool-output
stores. Split from tests/test_qualification_probes.py (the 1,000-line rule)."""
import json
import os
import shutil
from unittest.mock import patch

from tests.qualification import chatgpt, safety, sanitize
from tests.qualification.doubles import Temp
from tests.test_qualification_probes import UPSTREAM, ChatGPTTests, StageHarness

# Account-identifying or credential-like values, and the fragment of each that must never be written.
SECRETS = {"email": ("fernando.test@example.com", "fernando.test"), "org": ("org-AbCdEf123456", "AbCdEf123456"),
           "user": ("user-Zx9Yw8Vu7Q", "Zx9Yw8Vu7Q"), "acct": ("acct_9f8e7d6c5b", "9f8e7d6c5b"),
           "sk": ("sk-proj-AbCdEfGh1234567890", "AbCdEfGh1234567890"),
           "jwt": ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlX3ZhbHVl", "c2lnbmF0dXJlX3ZhbHVl"),
           "bearer": ("Bearer abcDEF123456ghi", "abcDEF123456ghi"), "req": ("req_0123456789abcdef", "0123456789abcdef"),
           "uuid": ("123e4567-e89b-12d3-a456-426614174000", "426614174000"),
           "hex": ("0123456789abcdef0123456789", "0123456789abcdef01"),
           "b64": ("QWxhZGRpbjpvcGVuIHNlc2FtZQ12Ab", "QWxhZGRpbjpvcGVu"), "ip": ("192.168.10.24", "192.168.10.24"),
           "path": ("/Users/someone/secret.txt", "someone/secret")}
ALL = " ".join(value for value, _ in SECRETS.values())


class MaskTests(Temp):
    def test_every_pattern_is_masked_and_codes_survive(self):
        for kind, (value, fragment) in SECRETS.items():
            with self.subTest(kind=kind):
                masked, changed = sanitize.mask("x %s y" % value)
                self.assertTrue(changed)
                self.assertNotIn(fragment, masked)
                self.assertIn("<redacted:", masked)
                self.assertEqual(sanitize.leaks(masked), [])
                self.assertTrue(sanitize.leaks(value), kind)
        for safe in ("subscription_sharing_user_not_eligible", "invalid_request_error", "gpt-5.5", "reasoning.effort",
                     "gpt-6.1-terra-fast", "context_length_exceeded", "APIError", "the plan is not eligible"):
            self.assertEqual(sanitize.mask(safe), (safe, False), safe)

    def test_key_names_come_from_an_allow_list(self):
        self.assertEqual(sanitize.safe_keys({"code": 1, "message": 2, "fernando.test@example.com": 3, "sk-proj-x": 4}),
                         ["<key>", "<key>", "code", "message"])

    def test_code_values_and_messages(self):
        self.assertEqual(sanitize.code_value("subscription_sharing_user_not_eligible"), "subscription_sharing_user_not_eligible")
        self.assertEqual(sanitize.code_value("some_new_code"), "some_new_code")
        for bad in ("acct_9f8e7d6c5b", "org-AbCdEf123456", "Fernando", "x" * 80, 7):
            self.assertIsNone(sanitize.code_value(bad), bad)
        self.assertEqual(sanitize.code_value("APIError", type_name=True), "APIError")
        record = sanitize.message("Rate limit for org-AbCdEf123456: rate_limit_exceeded " + "z" * 300)
        self.assertEqual((record["masked"], len(record["sha256"]), record["codes"]), (True, 12, ["rate_limit_exceeded"]))
        self.assertLessEqual(len(record["text"]), 200)
        self.assertNotIn("AbCdEf123456", json.dumps(record))

    def test_the_redactor_leak_check_knows_the_patterns(self):
        for value, _ in SECRETS.values():
            self.assertTrue(safety.Redactor().leaks("output %s" % value), value)


class FuzzedFactsTests(StageHarness):
    connect = ChatGPTTests.connect

    def fuzzed(self, **extra):
        error = dict(UPSTREAM, message="refused " + ALL)  # `type` must stay a name for accounting: fuzzed directly below
        error["response"] = dict(body=json.dumps(dict(error={
            "code": "acct_9f8e7d6c5b", "type": ALL, "message": ALL, "reason": ALL, "error_description": ALL, "error": ALL,
            "param": ALL, "details": [{"deep": [ALL, {"deeper": ALL}]}], "fernando.test@example.com": ALL,
            "sk-proj-AbCdEfGh1234567890": 1})))
        overrides = dict(  # model ids and finish: see test_model_and_finish_are_masked (accounting validates them)
                         tokens={"input": 3, "fernando.test@example.com": 5, "cache": {"sk-proj-AbCdEfGh1234567890": 7}},
                         content=[dict(type=ALL)])
        run, ledger, server, *_ = self.connect(upstream_error=error, assistant_overrides=overrides, retry_message=ALL,
                                               upstream_retry=dict(attempt=2, at=1, error=error), **extra)
        chatgpt.select_model(run)
        chatgpt.request(run)
        return run, json.dumps(run.output(run.results))

    def test_no_identifier_survives_in_any_recorded_field(self):
        run, text = self.fuzzed()
        for kind, (value, fragment) in SECRETS.items():
            self.assertNotIn(fragment, text, kind)
        self.assertEqual(sanitize.leaks(text), [])
        self.assertEqual(run.redact.leaks(text), [])
        for placeholder in ("<redacted:email>", "<redacted:id>", "<redacted:jwt>", "<redacted:credential>", "<key>"):
            self.assertIn(placeholder, text)
        facts = run.results["stages"]["chatgpt"]["session_facts"]
        error = facts["assistant"][0]["error"]
        self.assertTrue(error["message"]["masked"])
        self.assertEqual(len(error["message"]["sha256"]), 12)
        self.assertIsNone(error["body"]["fields"].get("code"))  # an id-shaped code is not a code
        self.assertEqual(error["body"]["fields"]["message"]["masked"], True)
        self.assertIn("<key>", error["body"]["keys"])  # the email- and key-shaped names

    def test_model_and_finish_are_masked(self):
        run, *_ = self.connect()
        facts = chatgpt.assistant_facts(run, dict(type="assistant", model=dict(providerID=ALL, id=ALL, variant=ALL),
                                                  finish=ALL, rawFinish=ALL,
                                                  content=[dict(type=ALL)], **{ALL: 1}))
        text = json.dumps(facts)
        for kind, (value, fragment) in SECRETS.items():
            self.assertNotIn(fragment, text, kind)
        self.assertEqual(sanitize.leaks(text), [])
        self.assertIn("<key>", facts["keys"])

    def test_error_key_names_outside_the_schema_are_masked(self):
        run, *_ = self.connect()
        error = dict(UPSTREAM, type="APIError " + ALL,
                     **{"fernando.test@example.com": ALL, "sk-proj-AbCdEfGh1234567890": [ALL, {ALL: ALL}]})
        text = json.dumps(chatgpt.structured_error(run, error))
        for kind, (value, fragment) in SECRETS.items():
            self.assertNotIn(fragment, text, kind)
        self.assertIn("<key>", text)

    def test_a_masking_gap_withholds_the_facts(self):
        with patch.object(sanitize, "mask", lambda text: (text, False)):
            run, text = self.fuzzed()
        facts = run.results["stages"]["chatgpt"]["session_facts"]
        self.assertEqual(sorted(facts), ["facts_withheld"])
        self.assertTrue(facts["facts_withheld"])
        for kind, (value, fragment) in SECRETS.items():
            self.assertNotIn(fragment, text, kind)


class GuardBlockTests(StageHarness):
    connect = ChatGPTTests.connect

    def classified(self, error, retry_error):
        for name in ("iso", "state"):  # each run gets a fresh ledger and dir
            shutil.rmtree(self.tmp / name, ignore_errors=True)
        run, ledger, server, *_ = self.connect(upstream_error=error, upstream_retry=dict(attempt=2, at=1, error=retry_error))
        chatgpt.select_model(run)
        chatgpt.request(run)
        return run.results["stages"]["chatgpt"]

    def test_a_retry_blocked_by_our_guard_is_not_an_upstream_refusal(self):
        guard = dict(type="UnknownError", message="qualification guard (guard_budget)")
        result = self.classified(guard, guard)
        self.assertIn("request_blocked_by_guard", result["failures"])
        self.assertNotIn("request_failed_upstream", result["failures"])
        self.assertEqual(result["guard_block"], ["guard_budget"])
        shim = dict(type="UnknownError", message="routing: blocked (attach_required)")
        self.assertEqual(self.classified(shim, shim)["guard_block"], ["attach_required"])

    def test_an_upstream_refusal_then_a_guarded_retry_reports_both(self):
        result = self.classified(UPSTREAM, dict(type="UnknownError", message="qualification guard (guard_budget)"))
        self.assertIn("request_blocked_by_guard", result["failures"])
        self.assertIn("request_failed_upstream", result["failures"])
        self.assertEqual(result["upstream_error"], "subscription_sharing_user_not_eligible")


class HardLinkTests(Temp):
    def test_a_hard_link_in_a_tool_output_store_is_unexpected(self):
        root = self.private(self.tmp / "data")
        for store in ("shell", "tool-output"):
            (root / store).mkdir()
        (root / "auth.json").write_text("x")
        before = safety.snapshot_tree(root)
        os.link(root / "auth.json", root / "shell" / "sh_1.out")
        (root / "tool-output" / "t.txt").write_text("ok")
        unexpected, _, session = safety.classify_snapshots(before, safety.snapshot_tree(root), stores=True, root=root)
        self.assertIn("shell/sh_1.out", unexpected)
        self.assertEqual(session, ["tool-output/t.txt"])
        shutil.rmtree(root)
