"""OpenCode usage accounting (S3.6b-1): fixture facts, title by subtraction, usage from messages and cost, against the recorded OpenCode 2.2.6 probe payloads."""
import copy
import json
import random
import unittest

from runtime import opencode_accounting as accounting
from runtime import routing
from tests.fixture_opencode_accounting import (
    A_ID,
    A_MESSAGES,
    A_TOTAL,
    account,
    assert_fixed_error,
    C_ID,
    C_MESSAGES,
    C_TOTAL,
    D_ID,
    D_MESSAGES,
    D_TOTAL,
    EVENTS,
    FIELDS,
    idle,
    LINEAGE,
    SESSIONS,
    spawn_call,
    synthetic,
    tokens,
    total_of,
)


class FixtureFacts(unittest.TestCase):
    """The recorded data the accounting claims rest on; if these change, the claims need re-checking."""

    def test_session_c_total_and_message_sum_match_the_recorded_summary(self):
        self.assertEqual(C_TOTAL, tokens(8, 240, 0, 52164, 1725))
        self.assertEqual(C_TOTAL, total_of(SESSIONS[C_ID]["tokens"]))
        recorded_sum = LINEAGE["aux_session_C"]["sum_of_message_tokens"]
        result = account()
        self.assertEqual((result["totals"]["messages"]["input"], result["totals"]["messages"]["output"]),
                         (recorded_sum["input"], recorded_sum["output"]))

    def test_sessions_with_given_titles_have_no_unmessaged_remainder(self):
        for messages, total, session_id in ((A_MESSAGES, A_TOTAL, A_ID), (D_MESSAGES, D_TOTAL, D_ID)):
            result = account(messages, total, session_id)
            self.assertEqual(result["totals"]["messages"], total)


class TitleBySubtraction(unittest.TestCase):
    def test_session_c_title_is_session_total_minus_its_messages(self):
        title = accounting.title_tokens(C_TOTAL, C_MESSAGES)
        self.assertEqual(title, {"tokens": tokens(2, 9, 0, 605, 549), "reason": None})

    def test_given_titles_subtract_to_zero(self):
        self.assertEqual(accounting.title_tokens(A_TOTAL, A_MESSAGES), {"tokens": tokens(0, 0, 0, 0, 0), "reason": None})
        self.assertEqual(accounting.title_tokens(D_TOTAL, D_MESSAGES), {"tokens": tokens(0, 0, 0, 0, 0), "reason": None})

    def test_negative_class_is_unknown(self):
        total = dict(C_TOTAL, output=200)
        self.assertEqual(accounting.title_tokens(total, C_MESSAGES), {"tokens": None, "reason": "negative_remainder"})

    def test_incomplete_paging_is_unknown(self):
        self.assertEqual(accounting.title_tokens(C_TOTAL, C_MESSAGES, complete=False),
                         {"tokens": None, "reason": "incomplete_coverage"})

    def test_missing_message_tokens_or_session_class_is_unknown(self):
        broken = [dict(C_MESSAGES[1], tokens=None)] + C_MESSAGES[:1] + C_MESSAGES[2:]
        self.assertEqual(accounting.title_tokens(C_TOTAL, broken)["reason"], "message_tokens_missing")
        self.assertEqual(accounting.title_tokens(dict(C_TOTAL, cache_read=None), C_MESSAGES)["reason"],
                         "session_tokens_missing")
        self.assertEqual(accounting.title_tokens(None, C_MESSAGES)["reason"], "session_tokens_missing")

    def test_in_progress_assistant_message_is_unknown(self):
        running = [dict(message, completed_ms=None, finish=None) if message["type"] == "assistant" else message
                   for message in C_MESSAGES if message["type"] != "idle"]
        self.assertEqual(accounting.title_tokens(C_TOTAL, running)["reason"], "message_in_progress")

    def test_a_later_idle_message_marks_earlier_messages_terminal(self):
        # The recorded compaction message has no completion time or finish; the idle message after it ends it.
        self.assertEqual(accounting.title_tokens(C_TOTAL, C_MESSAGES)["reason"], None)
        stopped = [dict(m, completed_ms=None, finish=None) if m["type"] == "assistant" else m for m in C_MESSAGES]
        self.assertEqual(accounting.title_tokens(C_TOTAL, stopped)["reason"], None)

    def test_compaction_without_a_later_idle_message_is_in_progress(self):
        running = [m for m in C_MESSAGES if m["type"] != "idle"]
        self.assertEqual(accounting.title_tokens(C_TOTAL, running)["reason"], "message_in_progress")
        for funding in ("subscription", "api"):
            result = account(running, funding=funding, session_cost=0.5)
            self.assertIn("message_in_progress", result["unknown"])
            self.assertEqual(result["title"]["state"], "unknown")
            self.assertTrue(all(r["cost_usd_micros"] is None for r in result["usage"]["requests"]))

    def test_randomized_subtraction_identity(self):
        rng = random.Random(3606)
        for _ in range(200):
            messages, total = [], dict.fromkeys(FIELDS, 0)
            for index in range(rng.randint(0, 40)):
                values = {field: rng.randint(0, 10 ** 6) for field in FIELDS}
                messages.append(synthetic(f"msg_r{index}", values, kind=rng.choice(("assistant", "compaction"))))
                for field in FIELDS:
                    total[field] += values[field]
            messages.append(idle("msg_zidle", 1794170000200))
            title = {field: rng.randint(0, 10 ** 5) for field in FIELDS}
            session = {field: total[field] + title[field] for field in FIELDS}
            self.assertEqual(accounting.title_tokens(session, messages), {"tokens": title, "reason": None})
            rng.shuffle(messages)
            result = accounting.usage_from_messages(messages, session, session_id="ses_r")
            self.assertEqual(result["totals"]["counted"], session)
            if messages or any(title.values()):
                self.assertIsNone(routing._usage_error(result["usage"]))


class UsageFromMessages(unittest.TestCase):
    def test_session_c_usage_requests_in_the_s36a_shape(self):
        result = account()
        self.assertEqual(result["usage"], {"source": "host_message_totals", "requests": [
            {"kind": "primary", "model": "haiku", "variant": "default", "tokens": tokens(2, 4, 0, 25945, 0),
             "cost_usd_micros": None, "receipt": "msg_fixture30"},
            {"kind": "compaction", "model": "haiku", "variant": "default", "tokens": tokens(4, 227, 0, 25614, 1176),
             "cost_usd_micros": None, "receipt": "msg_fixture28"},
            {"kind": "title", "model": "unknown", "tokens": tokens(2, 9, 0, 605, 549), "cost_usd_micros": None}]})
        self.assertIsNone(routing._usage_error(result["usage"]))
        self.assertEqual(result["totals"]["counted"], C_TOTAL)
        self.assertEqual(result["totals"]["session"], C_TOTAL)
        self.assertEqual(result["title"], {"state": "known", "tokens": tokens(2, 9, 0, 605, 549), "reason": None})
        self.assertEqual(result["message_ids"], [f"msg_fixture{n}" for n in range(27, 32)])
        self.assertEqual(result["unknown"], [])
        self.assertFalse(result["grouped"])

    def test_session_a_counts_every_assistant_message_and_no_title(self):
        result = account(A_MESSAGES, A_TOTAL, A_ID)
        requests = result["usage"]["requests"]
        self.assertEqual(len(requests), 8)
        self.assertEqual({request["kind"] for request in requests}, {"primary"})
        # Chronological (by creation time): the fixture lists messages newest first.
        self.assertEqual([request["variant"] for request in requests], ["default"] * 2 + ["low"] * 6)
        self.assertEqual([request["receipt"] for request in requests],
                         [f"msg_fixture{n}" for n in (25, 21, 17, 10, 7, 6, 3, 2)])
        self.assertEqual(result["title"]["state"], "zero")
        self.assertEqual(result["totals"]["counted"], A_TOTAL)
        self.assertIsNone(routing._usage_error(result["usage"]))

    def test_session_d_child_session_tokens_are_not_in_the_parent(self):
        result = account(D_MESSAGES, D_TOTAL, D_ID)
        self.assertEqual(result["totals"]["counted"], D_TOTAL)
        self.assertEqual(len(result["usage"]["requests"]), 2)

    def test_order_independent_and_deterministic(self):
        first = account()
        self.assertEqual(account(list(reversed(C_MESSAGES))), first)
        self.assertEqual(json.dumps(account(), sort_keys=True), json.dumps(first, sort_keys=True))

    def test_without_session_total_title_is_not_requested(self):
        result = account(total=None)
        self.assertEqual(result["title"], {"state": "unknown", "tokens": None, "reason": "session_tokens_missing"})
        self.assertEqual(result["totals"]["counted"], result["totals"]["messages"])
        self.assertIn("title_unknown", result["unknown"])
        self.assertEqual(len(result["usage"]["requests"]), 2)

    def test_negative_remainder_keeps_message_requests_and_marks_title_unknown(self):
        result = account(total=dict(C_TOTAL, output=200))
        self.assertEqual(result["title"]["reason"], "negative_remainder")
        self.assertEqual([r["kind"] for r in result["usage"]["requests"]], ["primary", "compaction"])
        self.assertIn("title_unknown", result["unknown"])

    def test_incomplete_paging_marks_title_unknown(self):
        result = account(complete=False)
        self.assertEqual(result["title"]["reason"], "incomplete_coverage")
        self.assertIn("incomplete_coverage", result["unknown"])

    def test_duplicate_messages_count_once(self):
        self.assertEqual(account(C_MESSAGES + copy.deepcopy(C_MESSAGES)), account())

    def test_conflicting_duplicate_is_refused(self):
        changed = dict(C_MESSAGES[3], tokens=tokens(9, 9, 9, 9, 9))
        self.assertEqual(assert_fixed_error(self, account, C_MESSAGES + [changed]), "conflicting duplicate message")

    def test_a_message_listed_under_two_sessions_is_refused(self):
        child = {"session_id": "ses_fixture3", "messages": [C_MESSAGES[3]], "tokens": C_MESSAGES[3]["tokens"]}
        spawning = C_MESSAGES[:3] + [dict(C_MESSAGES[3], tool_calls=[spawn_call("ses_fixture3")])]
        self.assertEqual(assert_fixed_error(self, account, spawning, children=[child]), "message in two sessions")

    def test_events_and_unknown_records_are_not_messages(self):
        usage_updated = next(e for e in EVENTS if e.get("type") == "session.usage.updated")
        self.assertEqual(assert_fixed_error(self, account, C_MESSAGES + [usage_updated]), "invalid message")

    def test_missing_tokens_on_an_assistant_message_is_unknown_not_zero(self):
        broken = [dict(m, tokens=None) if m["message_id"] == "msg_fixture30" else m for m in C_MESSAGES]
        result = account(broken)
        self.assertIn("message_tokens_missing", result["unknown"])
        self.assertEqual([r["kind"] for r in result["usage"]["requests"]], ["compaction"])
        self.assertEqual(result["title"]["state"], "unknown")

    def test_missing_model_is_counted_as_unknown_model(self):
        anonymous = [dict(m, requested=None) if m["message_id"] == "msg_fixture30" else m for m in C_MESSAGES]
        result = account(anonymous)
        self.assertEqual(result["usage"]["requests"][0]["model"], "unknown")
        self.assertNotIn("variant", result["usage"]["requests"][0])
        self.assertIn("model_unknown", result["unknown"])
        self.assertIsNone(routing._usage_error(result["usage"]))

    def test_no_countable_requests_gives_no_usage(self):
        idle_only = [m for m in C_MESSAGES if m["type"] in ("idle", "user")]
        result = account(idle_only, total=tokens(0, 0, 0, 0, 0))
        self.assertIsNone(result["usage"])
        self.assertIn("no_requests", result["unknown"])

    def test_more_requests_than_one_report_holds_are_grouped_by_route(self):
        messages = [synthetic(f"msg_g{index:02d}", tokens(1, 2, 3, 4, 5), variant=("low", "default")[index % 2])
                    for index in range(40)]
        result = accounting.usage_from_messages(messages, None, session_id="ses_g")
        self.assertTrue(result["grouped"])
        self.assertEqual(result["usage"]["requests"], [
            {"kind": "primary", "model": "haiku", "variant": "default", "tokens": tokens(20, 40, 60, 80, 100),
             "cost_usd_micros": None},
            {"kind": "primary", "model": "haiku", "variant": "low", "tokens": tokens(20, 40, 60, 80, 100),
             "cost_usd_micros": None}])
        self.assertIsNone(routing._usage_error(result["usage"]))

    def test_more_routes_than_one_report_holds_give_an_unknown_cost_report(self):
        messages = [synthetic(f"msg_v{index:02d}", tokens(1, 1, 0, 0, 0), variant=f"v{index}", cost=0.01)
                    for index in range(40)]
        for funding in ("subscription", "api"):
            result = accounting.usage_from_messages(messages, None, session_id="ses_g", funding=funding)
            self.assertEqual(result["usage"]["requests"], [{"kind": "primary", "model": "unknown",
                                                            "tokens": tokens(40, 40, 0, 0, 0), "cost_usd_micros": None}])
            self.assertIn("too_many_routes", result["unknown"])
            self.assertIsNone(result["totals"]["usd_micros"])
            self.assertIsNone(routing._usage_error(result["usage"]))

    def test_constants_agree_with_the_s36a_record_contract(self):
        self.assertEqual(accounting.TOKEN_FIELDS, routing.usage_budget.TOKEN_FIELDS)
        self.assertEqual(accounting.MAX_USAGE_REQUESTS, routing.MAX_USAGE_REQUESTS)
        self.assertEqual(accounting.MAX_TOTAL, routing.usage_budget.MAX_INTEGER)
        self.assertIn(accounting.SOURCE, routing.usage_budget.COST_SOURCES)
        self.assertLessEqual(set(accounting.KINDS.values()) | {"title"}, set(routing.REQUEST_KINDS))


class Cost(unittest.TestCase):
    def test_subscription_cost_stays_unknown_even_when_the_provider_reports_one(self):
        priced = [dict(m, cost=0.5) if m["type"] == "assistant" else m for m in C_MESSAGES]
        result = account(priced)
        self.assertTrue(all(r["cost_usd_micros"] is None for r in result["usage"]["requests"]))
        self.assertIsNone(result["totals"]["usd_micros"])
        self.assertEqual(result["reported_cost"], {"usd_micros": 500000, "messages": 2, "basis": "provider_estimate"})
        self.assertEqual(result["funding"], "subscription")
        self.assertEqual(result["usage"]["source"], "host_message_totals")
        self.assertIsNone(result["totals"]["usd_basis"])

    def test_recorded_zero_cost_is_not_billing_evidence_for_subscriptions(self):
        result = account()
        self.assertIsNone(result["totals"]["usd_micros"])
        self.assertEqual(result["reported_cost"], {"usd_micros": 0, "messages": 2, "basis": "provider_estimate"})

    def test_api_cost_passes_through_as_ceiling_micros_separate_from_tokens(self):
        messages = [synthetic("msg_c1", tokens(1, 2, 0, 0, 0), cost=0.0123),
                    synthetic("msg_c2", tokens(3, 4, 0, 0, 0), cost=1e-7)]
        result = accounting.usage_from_messages(messages, tokens(4, 6, 0, 0, 0), session_id="ses_c", funding="api", lineage=[],
                                                session_cost=0.0123001)
        self.assertEqual(result["title"]["state"], "zero")
        self.assertEqual([r["cost_usd_micros"] for r in result["usage"]["requests"]], [12300, 1])
        self.assertEqual([r["tokens"] for r in result["usage"]["requests"]], [tokens(1, 2, 0, 0, 0), tokens(3, 4, 0, 0, 0)])
        self.assertEqual(result["totals"]["usd_micros"], 12301)
        self.assertEqual(result["totals"]["usd_basis"], "estimate")
        self.assertEqual(result["totals"]["counted"], tokens(4, 6, 0, 0, 0))
        self.assertEqual(result["usage"]["source"], "estimate")

    def test_api_zero_cost_with_tokens_is_unknown(self):
        messages = [synthetic("msg_c1", tokens(50000, 20000, 0, 0, 0), cost=0.0)]
        result = accounting.usage_from_messages(messages, tokens(50000, 20000, 0, 0, 0), session_id="ses_c",
                                                funding="api", session_cost=0.0)
        self.assertEqual([r["cost_usd_micros"] for r in result["usage"]["requests"]], [None])
        self.assertIsNone(result["totals"]["usd_micros"])
        self.assertIn("cost_unknown", result["unknown"])

    def test_api_zero_title_cost_delta_with_title_tokens_is_unknown(self):
        messages = [synthetic("msg_c1", tokens(1, 1, 0, 0, 0), cost=0.01)]
        result = accounting.usage_from_messages(messages, tokens(3, 3, 0, 0, 0), session_id="ses_c", funding="api", lineage=[],
                                                session_cost=0.01)
        self.assertTrue(all(r["cost_usd_micros"] is None for r in result["usage"]["requests"]))
        self.assertIn("cost_unknown", result["unknown"])
        missing = accounting.usage_from_messages(messages, tokens(3, 3, 0, 0, 0), session_id="ses_c", funding="api", lineage=[])
        self.assertIn("cost_unknown", missing["unknown"])

    def test_api_zero_token_zero_cost_message_stays_zero(self):
        messages = [synthetic("msg_c1", tokens(0, 0, 0, 0, 0), cost=0.0), synthetic("msg_c2", tokens(1, 1, 0, 0, 0),
                                                                                    cost=0.01)]
        result = accounting.usage_from_messages(messages, tokens(1, 1, 0, 0, 0), session_id="ses_c", funding="api", lineage=[],
                                                session_cost=0.01)
        self.assertEqual([r["cost_usd_micros"] for r in result["usage"]["requests"]], [0, 10000])
        self.assertEqual(result["totals"]["usd_micros"], 10000)

    def test_api_cost_never_invented_from_tokens(self):
        messages = [synthetic("msg_c1", tokens(1000, 2000, 0, 0, 0), cost=None)]
        result = accounting.usage_from_messages(messages, None, session_id="ses_c", funding="api", lineage=[])
        self.assertIsNone(result["usage"]["requests"][0]["cost_usd_micros"])
        self.assertIsNone(result["totals"]["usd_micros"])
        self.assertIn("cost_unknown", result["unknown"])

    def test_api_title_cost_by_exact_subtraction(self):
        messages = [synthetic("msg_c1", tokens(1, 1, 0, 0, 0), cost=0.01),
                    synthetic("msg_c2", tokens(1, 1, 0, 0, 0), cost=0.01)]
        result = accounting.usage_from_messages(messages, tokens(3, 3, 0, 0, 0), session_id="ses_c", funding="api", lineage=[],
                                                session_cost=0.03)
        self.assertEqual(result["usage"]["requests"][-1], {"kind": "title", "model": "unknown",
                                                           "tokens": tokens(1, 1, 0, 0, 0), "cost_usd_micros": 10000})
        self.assertEqual(result["totals"]["usd_micros"], 30000)
        lower = accounting.usage_from_messages(messages, tokens(3, 3, 0, 0, 0), session_id="ses_c", funding="api", lineage=[],
                                               session_cost=0.01)
        self.assertIsNone(lower["usage"]["requests"][-1]["cost_usd_micros"])
        self.assertIsNone(lower["totals"]["usd_micros"])

    def test_unknown_message_under_api_funding_makes_every_cost_unknown(self):
        messages = [synthetic("msg_c1", tokens(1, 1, 0, 0, 0), cost=0.01), dict(synthetic("msg_c2", {}, cost=0.01),
                                                                               tokens=None)]
        result = accounting.usage_from_messages(messages, None, session_id="ses_c", funding="api", lineage=[])
        self.assertEqual([r["cost_usd_micros"] for r in result["usage"]["requests"]], [None])
        self.assertIsNone(result["totals"]["usd_micros"])


if __name__ == "__main__":
    unittest.main()
