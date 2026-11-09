"""Round-4 review of the ChatGPT fallback: every candidate is checked against the listing (and its variant) before
the first request, attempt records survive a later failure, a guard block stops the fallback, the account refusal
needs HTTP 400 + model_not_found + OpenAI's text, the Codex-with-ChatGPT id parsing is strict, and the recorded public
prices carry their date with a staleness warning. New module (the 1,000-line rule)."""
import datetime
import io
from contextlib import redirect_stdout
from unittest.mock import patch

from tests.qualification import chatgpt, safety
from tests.qualification.doubles import Temp
from tests.test_qualification_fallback import OTHER, UNSUPPORTED, ChatGPTClassify, FallbackTests
from tests.test_qualification_probes import ChatGPTTests, StageHarness


class CandidateCheckTests(StageHarness):
    connect = ChatGPTTests.connect

    def refused_before_any_request(self, models):
        with self.assertRaises(safety.Refused):
            self.run_fallback(models, {})
        ledger = safety.CallLedger(self.tmp / "state" / "calls.jsonl")
        self.assertEqual([e for e in ledger.export()["entries"] if e["kind"] == "reserve"], [])

    def test_every_candidate_and_variant_is_checked_before_the_first_request(self):
        self.refused_before_any_request("openai/gpt-a,openai/gpt-unlisted")
        self.refused_before_any_request("openai/gpt-a,openai/gpt-b#high")

    def test_earlier_attempts_survive_a_later_failure(self):
        original, calls = chatgpt.request, []

        def second_fails(run):
            calls.append(1)
            if len(calls) == 2:
                raise safety.Blocker("session did not become idle")
            original(run)
        with patch.object(chatgpt, "request", second_fails), self.assertRaises(safety.Blocker):
            self.run_fallback("openai/gpt-a,openai/gpt-b", dict({"gpt-a": UNSUPPORTED, "gpt-b": None}))
        stage = self.connect_run_stage()
        self.assertEqual([a["model"] for a in stage["attempts"]], ["openai/gpt-a#none"])
        self.assertIn("model_not_supported_for_account", stage["attempts"][0]["failures"])

    def connect_run_stage(self):
        return self.last_run.results["stages"]["chatgpt"]

    def run_fallback(self, models, by_model, chatgpt_cap=4):
        original = self.connect

        def keep(**knobs):
            made = original(**knobs)
            self.last_run = made[0]
            return made
        self.connect = keep
        try:
            return FallbackTests.run_fallback(self, models, by_model, chatgpt_cap)
        finally:
            self.connect = original

    def test_a_guard_block_stops_the_fallback(self):
        guarded = dict(UNSUPPORTED)
        run, ledger, server, stage = self.run_fallback("openai/gpt-a,openai/gpt-b", dict({"gpt-a": guarded, "gpt-b": None}))
        self.assertEqual(len(stage["attempts"]), 2)  # control: an unsupported model alone falls back
        with patch.object(chatgpt, "guard_reasons", lambda facts, hooks: ["guard_budget"]):
            run, ledger, server, stage = self.run_fallback("openai/gpt-a,openai/gpt-b", dict({"gpt-a": guarded, "gpt-b": None}))
        self.assertEqual(len(stage["attempts"]), 1)
        self.assertIn("request_blocked_by_guard", stage["failures"])


class RefusalShapeTests(Temp):
    def test_the_account_refusal_needs_http_400_the_code_and_the_text(self):
        facts = ChatGPTClassify().facts
        self.assertTrue(chatgpt.unsupported_for_account(facts(UNSUPPORTED)))
        self.assertFalse(chatgpt.unsupported_for_account(facts(dict(UNSUPPORTED, status=403))))
        self.assertFalse(chatgpt.unsupported_for_account(facts(dict(UNSUPPORTED, status=None))))
        self.assertFalse(chatgpt.unsupported_for_account(facts(OTHER)))


class StrictIdTests(Temp):
    def test_only_well_formed_ids_pass_the_codex_chatgpt_rule(self):
        for good in ("gpt-6-luna", "gpt-5.6-luna", "gpt-6.1-terra-fast", "gpt-6.1-codex-mini", "gpt-7"):
            self.assertTrue(chatgpt.codex_chatgpt_allowed(good), good)
        for bad in ("gpt-5.5", "gpt-5.6", "gpt-5.50", "gpt-6x", "gpt-10.0-a-b", "gpt-6-a", "gpt-6.1-terra-fast-x",
                    "gpt-6-Luna", "gpt-06", "o4-mini", "", None):
            self.assertFalse(chatgpt.codex_chatgpt_allowed(bad), bad)


class PublicCostDateTests(Temp):
    def test_the_price_snapshot_date_is_recorded_and_stale_prices_warn(self):
        listed = [dict(id=i, providerID="openai", variants=[dict(id="none"), dict(id="low")], cost=[])
                  for i in ("gpt-6-luna", "gpt-5.6-luna")]
        for today, stale in ((datetime.date(2026, 11, 20), False), (datetime.date(2026, 12, 20), True)):
            printed = []
            with patch.object(chatgpt, "today", lambda: today):
                ref, record = chatgpt.auto_select(listed, printed.append, lambda *a: None)
            self.assertEqual(record["public_cost_date"], chatgpt.PUBLIC_COST_DATE)
            self.assertEqual(record["public_cost_stale"], stale)
            self.assertEqual(any("older than 30 days" in line for line in printed), stale)
