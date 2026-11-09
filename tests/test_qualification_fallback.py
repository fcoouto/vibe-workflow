"""Attempt 6's lesson: the model listing after a ChatGPT sign-in is not an entitlement. The refusal is classified as
`model_not_supported_for_account`, `auto` prefers models OpenCode's own Codex-with-ChatGPT rule allows (ranked by a
recorded public price table), an ordered `--chatgpt-model A,B,C` falls back only on that refusal, and OpenCode's own
pruning of tool output is churn. New module (the 1,000-line rule)."""
import io
import json
import os
import shutil
from contextlib import redirect_stdout
from unittest.mock import patch

from tests.qualification import chatgpt, safety, sanitize, user_dirs
from tests.qualification import opencode_live as live
from tests.qualification.doubles import Temp
from tests.test_qualification_probes import ChatGPTTests, StageHarness, approved_ledger
from tests.test_qualification_results import touch

UNSUPPORTED = dict(type="APIError", status=400, message="The 'gpt-a' model is not supported when using Codex with a "
                   "ChatGPT account.", response=dict(body=json.dumps(dict(error=dict(
                       type="invalid_request_error", code="model_not_found", param="model",
                       message="The 'gpt-a' model is not supported when using Codex with a ChatGPT account.")))))
OTHER = dict(type="APIError", status=403, message="not eligible", response=dict(body=json.dumps(dict(error=dict(
    code="subscription_sharing_user_not_eligible", type="invalid_request_error", message="not eligible")))))
LISTED = [dict(id=i, providerID="openai", variants=[dict(id="none"), dict(id="low")]) for i in ("gpt-a", "gpt-b", "gpt-c")]


class FallbackTests(StageHarness):
    connect = ChatGPTTests.connect

    def run_fallback(self, models, by_model, chatgpt_cap=4):
        for name in ("iso", "state"):
            shutil.rmtree(self.tmp / name, ignore_errors=True)
        run, ledger, server, *_ = self.connect(openai_models=LISTED, upstream_by_model=by_model)
        if chatgpt_cap != 4:
            ledger.set_cap("chatgpt", chatgpt_cap, "test", approval=dict(by="u", at="t", scope="s"))
        run.chatgpt_model = models
        run.start, run.probes = (lambda stage: None), (lambda stage: [])
        server.oauth_pending_polls, server.oauth_polls, server.connected = 0, 0, False
        (self.tmp / "iso" / "chatgpt-login.json").unlink()
        with redirect_stdout(io.StringIO()):
            chatgpt.run(run)
        return run, ledger, server, run.results["stages"]["chatgpt"]

    def test_an_unsupported_model_falls_back_to_the_next_candidate(self):
        run, ledger, server, stage = self.run_fallback("openai/gpt-a,openai/gpt-b", dict({"gpt-a": UNSUPPORTED, "gpt-b": None}))
        self.assertEqual([a["model"] for a in stage["attempts"]], ["openai/gpt-a#none", "openai/gpt-b#none"])
        self.assertIn("model_not_supported_for_account", stage["attempts"][0]["failures"])
        self.assertNotIn("request_failed_upstream", stage["attempts"][0]["failures"])
        self.assertTrue(stage["attempts"][1]["pass"])
        self.assertTrue(stage["pass"])
        self.assertEqual([r["model"] for r in server.transmitted()], ["gpt-a", "gpt-b"])
        self.assertEqual(server.control["route"], ["openai", "gpt-b", "none"])
        self.assertFalse(server._request_hook("ses_x", "primary", ("openai", "gpt-a", "none")))  # the previous model is blocked
        self.assertEqual(ledger.summary()["chatgpt"]["spent"], 2)

    def test_every_candidate_unsupported_stops_after_the_last(self):
        run, ledger, server, stage = self.run_fallback("openai/gpt-a,openai/gpt-b", dict({"gpt-a": UNSUPPORTED, "gpt-b": UNSUPPORTED}))
        self.assertEqual(len(stage["attempts"]), 2)
        self.assertFalse(stage["pass"])
        self.assertEqual(ledger.summary()["chatgpt"]["spent"], 2)

    def test_any_other_outcome_stops(self):
        run, ledger, server, stage = self.run_fallback("openai/gpt-a,openai/gpt-b", dict({"gpt-a": OTHER, "gpt-b": None}))
        self.assertEqual(len(stage["attempts"]), 1)
        self.assertIn("request_failed_upstream", stage["failures"])
        self.assertEqual([r["model"] for r in server.transmitted()], ["gpt-a"])
        run, ledger, server, stage = self.run_fallback("openai/gpt-a,openai/gpt-b", dict({"gpt-a": None, "gpt-b": None}))
        self.assertEqual(len(stage["attempts"]), 1)
        self.assertTrue(stage["pass"])

    def test_the_budget_must_cover_every_candidate_and_the_probes(self):
        with self.assertRaises(safety.Refused) as caught:
            self.run_fallback("openai/gpt-a,openai/gpt-b,openai/gpt-c", {})
        self.assertIn("5 needed", str(caught.exception))
        run, ledger, server, stage = self.run_fallback("openai/gpt-a,openai/gpt-b,openai/gpt-c",
                                                       dict({"gpt-a": UNSUPPORTED, "gpt-b": UNSUPPORTED, "gpt-c": UNSUPPORTED}),
                                                       chatgpt_cap=6)
        self.assertEqual(ledger.summary()["chatgpt"]["spent"], 3)
        self.assertLessEqual(ledger.summary()["chatgpt"]["spent"], ledger.summary()["chatgpt"]["cap"])

    def test_the_model_list_is_validated_before_anything_starts(self):
        for good in ("openai/gpt-a", "openai/gpt-a#low", "auto", "openai/gpt-a,openai/gpt-b#low,openai/gpt-c"):
            self.assertTrue(chatgpt.parse_models(good), good)
        for bad in ("", "openai/a,openai/b,openai/c,openai/d", "openai/a,auto", "openai/a,claude-agent/haiku#low",
                    "openai/a,,openai/b", "openai/a,openai/a", "AUTO", "auto#low"):
            with self.subTest(bad=bad), self.assertRaises(safety.Refused):
                chatgpt.parse_models(bad)


class EntitlementRuleTests(Temp):
    def test_the_codex_chatgpt_rule_and_the_auto_preference(self):
        allowed = [i for i in ("gpt-5.5", "gpt-5.5-fast", "gpt-5.6", "gpt-5.6-luna", "gpt-6-luna", "gpt-6.1-terra-fast", "o4-mini")
                   if chatgpt.codex_chatgpt_allowed(i)]
        self.assertEqual(allowed, ["gpt-5.6-luna", "gpt-6-luna", "gpt-6.1-terra-fast"])
        listed = [dict(id=i, providerID="openai", variants=[dict(id="none"), dict(id="low")], cost=[]) for i in
                  ("gpt-5.5", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-6-luna", "gpt-6.1-terra", "gpt-6-luna-fast")]
        ref, record = chatgpt.auto_select(listed, lambda line: None, lambda *a: None)
        self.assertEqual(ref, "openai/gpt-6-luna")
        self.assertEqual(record["candidates"][:3], ["gpt-6-luna", "gpt-5.6-luna", "gpt-6.1-terra"])
        self.assertEqual(record["excluded"]["gpt-5.5"], "not_codex_chatgpt")

    def test_the_refusal_is_classified_apart(self):
        harness = ChatGPTClassify()
        self.assertTrue(chatgpt.unsupported_for_account(harness.facts(UNSUPPORTED)))
        self.assertFalse(chatgpt.unsupported_for_account(harness.facts(OTHER)))
        self.assertFalse(chatgpt.unsupported_for_account(dict(facts_withheld=["pattern:hex"])))


class ChatGPTClassify:
    def facts(self, error):
        redact = safety.Redactor()
        run = type("R", (), dict(redact=redact))()
        return dict(assistant=[dict(error=chatgpt.structured_error(run, error), retry=None)], events=[], idle=[])


class PruningTests(Temp):
    def test_opencode_pruning_its_own_tool_output_is_churn_with_a_reason_for_the_rest(self):
        root = self.private(self.tmp / "data")
        project = "13464ee0f9a1b2c3d4e5f60718293a4b5c6d7e8f"
        for name in ("shell/%s/sh_01JABCDEF0123456789.out" % project, "shell/%s/sh_01JOLD000000000000.out" % project,
                     "tool-output/t_old.txt", "log/old.log", "snapshot/h1/objects/ab/cd", "opencode.db"):
            touch(root / name)
        before = safety.snapshot_tree(root)
        os.unlink(root / "shell" / project / "sh_01JOLD000000000000.out")  # pruned by OpenCode's retention
        touch(root / "shell" / project / "sh_01JABCDEF0123456789.out")  # rewritten
        touch(root / "shell" / project / "sh_01JNEW000000000000.out")  # added
        os.unlink(root / "tool-output" / "t_old.txt")
        os.unlink(root / "log" / "old.log")
        shutil.rmtree(root / "snapshot" / "h1" / "objects" / "ab")
        reasons = {}
        unexpected, churn = user_dirs.classify_user_dir("data", before, safety.snapshot_tree(root), root, (), reasons)
        self.assertEqual(unexpected, [])
        self.assertEqual(churn["pruned"], 5)
        self.assertEqual(churn["shell"], 2)
        os.unlink(root / "opencode.db")
        shutil.rmtree(root / "tool-output")
        reasons = {}
        unexpected, churn = user_dirs.classify_user_dir("data", before, safety.snapshot_tree(root), root, (), reasons)
        self.assertEqual(sorted(reasons), ["deleted_db", "deleted_store"])
        entry, _ = user_dirs.user_dir_entry(".local/share/opencode", before, safety.snapshot_tree(root), root)
        self.assertEqual(entry["unexpected_reasons"], dict(deleted_db=1, deleted_store=1))
        masked = sanitize.text("shell/%s/sh_01JABCDEF0123456789.out" % project)
        self.assertNotIn(project, masked)
        self.assertEqual(sanitize.leaks(masked), [])
