"""OpenCode usage accounting and event resync (S3.6b-1) against the recorded OpenCode 2.2.6 probe payloads."""
import ast
import builtins
import copy
import json
import math
from pathlib import Path
import random
import re
import unittest
from unittest.mock import patch

from runtime import opencode_accounting as accounting
from runtime import routing
from runtime.adapters import opencode
from runtime.opencode_accounting import AccountingError
from tests import test_routing_accounting as ledger_tests

M = ledger_tests.M
ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "fixtures" / "smart-routing" / "probes"
MODULE = ROOT / "runtime" / "opencode_accounting.py"
FAMILIES = {"claude-agent/haiku": "claude-haiku"}
PLACEHOLDER = re.compile(r"<(ses|msg|toolu|evt)([_#])(\d+)>")
MARKER = "HOSTILE-MARKER-do-not-echo"
FIELDS = ("input", "output", "reasoning", "cache_read", "cache_write")


def desanitize(value):
    """Map sanitizer placeholders (`<ses_5>`) to valid-looking IDs, as tests/test_opencode_adapter.py does."""
    if isinstance(value, dict):
        return {key: desanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [desanitize(item) for item in value]
    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: f"{m[1]}_{'fixture' if m[2] == '_' else 'hook'}{m[3]}", value)
    return value


def load(name):
    return desanitize(json.loads((PROBES / name).read_text(encoding="utf-8")))


LINEAGE = load("p0-8-lineage-aux.json")
EXCERPT = load("p0-hook-event-excerpt.json")
SESSIONS = {session["id"]: session for session in LINEAGE["sessions"]}


def normalized(raw_messages):
    return [opencode.normalize_message(message, families=FAMILIES) for message in raw_messages]


def total_of(raw_tokens):
    """A session total in the adapter's normalized token shape, through its public message normalizer."""
    return opencode.normalize_message({"type": "assistant", "tokens": raw_tokens})["tokens"]


def tokens(*values):
    return dict(zip(FIELDS, values))


# Session C (`ses_fixture4`, generated title "Reply with exact text"): a compaction and an assistant message.
C_ID = "ses_fixture4"
C_MESSAGES = normalized(LINEAGE["aux_session_C"]["messages"])
C_TOTAL = total_of(LINEAGE["aux_session_C"]["session_tokens"])
# Session D (`ses_fixture2`, title given as "probe D"): parent of `ses_fixture3` through its subagent call.
D_ID = "ses_fixture2"
D_MESSAGES = normalized(LINEAGE["nested_session_D"]["messages"])
D_TOTAL = total_of(SESSIONS[D_ID]["tokens"])
# Session A (`ses_fixture5`, title given as "probe A"): the P0.5 model and variant messages.
A_ID = "ses_fixture5"
A_MESSAGES = normalized(load("p0-5-model-variant-messages.json")["messages"])
A_TOTAL = total_of(SESSIONS[A_ID]["tokens"])
EVENTS = [opencode.normalize_event(event, families=FAMILIES) for event in EXCERPT["events"]]


def account(messages=C_MESSAGES, total=C_TOTAL, session_id=C_ID, **kwargs):
    kwargs.setdefault("lineage", [])  # launchers supply lineage; tests of its absence pass None
    return accounting.usage_from_messages(messages, total, session_id=session_id, **kwargs)


def state(messages=C_MESSAGES, total=C_TOTAL, session_id=C_ID, **extra):
    extra.setdefault("lineage", [])
    return {"session_id": session_id, "messages": messages, "tokens": total, **extra}


def event(aggregate, seq, event_id=None, version=1, kind="event", session=None):
    return {"schema": 1, "client": "opencode", "kind": kind, "type": "session.step.started",
            "event_id": event_id or f"evt_{aggregate[4:]}x{seq}", "session_id": session or aggregate,
            "durable_seq": seq, "durable_aggregate_id": aggregate, "durable_version": version}


def assert_fixed_error(case, function, *args, **kwargs):
    with case.assertRaises(AccountingError) as raised:
        function(*args, **kwargs)
    case.assertIn(str(raised.exception), accounting.REASONS)
    case.assertNotIn(MARKER, str(raised.exception))
    case.assertIsNone(raised.exception.__cause__)
    return str(raised.exception)


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


def idle(message_id, created_ms):
    return {"schema": 1, "client": "opencode", "kind": "message", "type": "idle", "message_id": message_id,
            "created_ms": created_ms, "completed_ms": None, "agent": None, "requested": None, "route_identity": None,
            "previous": None, "finish": None, "raw_finish": None, "outcome": "succeeded", "error_type": None,
            "tokens": None, "cost": None, "rate_limit": None, "tool_calls": [], "tool_calls_dropped": 0}


def spawn_call(child, status="completed", tool="subagent"):
    return {"tool": tool, "tool_class": "subagent" if tool == "subagent" else "builtin", "tool_call_id": "toolu_x",
            "executed": False, "status": status, "child_session_id": child, "spawn": None}


def synthetic(message_id, values, kind="assistant", alias="haiku", variant="default", cost=None, **extra):
    return {"schema": 1, "client": "opencode", "kind": "message", "type": kind, "message_id": message_id,
            "created_ms": 1794170000000, "completed_ms": 1794170000100 if kind == "assistant" else None,
            "agent": "build", "requested": {"provider_id": "claude-agent", "model_alias": alias, "variant": variant},
            "route_identity": None, "previous": None, "finish": "stop" if kind == "assistant" else None,
            "raw_finish": None, "outcome": None, "error_type": None, "tokens": dict(values), "cost": cost,
            "rate_limit": None, "tool_calls": [], "tool_calls_dropped": 0, **extra}


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


class EventGaps(unittest.TestCase):
    def test_recorded_excerpt_flags_its_missing_sequence_numbers(self):
        tracked = accounting.track_events(EVENTS)
        self.assertEqual(tracked["resync_needed"], [{"aggregate_id": A_ID, "last_good_seq": 1, "reason": "gap"}])
        self.assertEqual(tracked["counts"]["volatile_ignored"], 1)  # session.usage.updated
        self.assertEqual(tracked["state"]["aggregates"]["ses_fixture1"]["last_seq"], 0)

    def test_contiguous_stream_is_ok(self):
        tracked = accounting.track_events([event("ses_a", seq) for seq in range(6)])
        self.assertEqual(tracked["resync_needed"], [])
        self.assertEqual(tracked["state"]["aggregates"]["ses_a"]["last_seq"], 5)

    def test_incremental_tracking_equals_one_batch(self):
        events = [event("ses_a", seq) for seq in range(6)]
        first = accounting.track_events(events[:3])
        second, batch = accounting.track_events(events[3:], first["state"]), accounting.track_events(events)
        self.assertEqual((second["state"], second["resync_needed"]), (batch["state"], batch["resync_needed"]))

    def test_gap(self):
        tracked = accounting.track_events([event("ses_a", 0), event("ses_a", 1), event("ses_a", 3), event("ses_a", 4)])
        self.assertEqual(tracked["resync_needed"], [{"aggregate_id": "ses_a", "last_good_seq": 1, "reason": "gap"}])

    def test_exact_duplicate_is_ignored(self):
        tracked = accounting.track_events([event("ses_a", 0), event("ses_a", 1), event("ses_a", 1), event("ses_a", 2)])
        self.assertEqual(tracked["resync_needed"], [])
        self.assertEqual(tracked["counts"]["duplicates"], 1)

    def test_same_sequence_with_another_event_is_a_conflict(self):
        tracked = accounting.track_events([event("ses_a", 0), event("ses_a", 1), event("ses_a", 1, "evt_other")])
        self.assertEqual(tracked["resync_needed"], [{"aggregate_id": "ses_a", "last_good_seq": 1,
                                                     "reason": "seq_conflict"}])

    def test_out_of_order(self):
        events = [event("ses_a", seq) for seq in range(80)] + [event("ses_a", 3, "evt_late")]
        tracked = accounting.track_events(events)
        self.assertEqual(tracked["resync_needed"], [{"aggregate_id": "ses_a", "last_good_seq": 79,
                                                     "reason": "out_of_order"}])

    def test_reset_to_zero(self):
        events = [event("ses_a", seq) for seq in range(4)] + [event("ses_a", 0, "evt_again")]
        self.assertEqual(accounting.track_events(events)["resync_needed"],
                         [{"aggregate_id": "ses_a", "last_good_seq": 3, "reason": "reset"}])

    def test_version_change(self):
        events = [event("ses_a", 0), event("ses_a", 1, version=2)]
        self.assertEqual(accounting.track_events(events)["resync_needed"],
                         [{"aggregate_id": "ses_a", "last_good_seq": 0, "reason": "version_changed"}])

    def test_interleaved_aggregates_are_tracked_separately(self):
        events = [event("ses_a", 0), event("ses_b", 0), event("ses_a", 1), event("ses_b", 1), event("ses_a", 2),
                  event("ses_b", 3)]
        self.assertEqual(accounting.track_events(events)["resync_needed"],
                         [{"aggregate_id": "ses_b", "last_good_seq": 1, "reason": "gap"}])

    def test_joining_late_needs_a_resync_and_a_resync_rebaselines(self):
        tracked = accounting.track_events([event("ses_a", 7)])
        self.assertEqual(tracked["resync_needed"], [{"aggregate_id": "ses_a", "last_good_seq": None,
                                                     "reason": "joined_late"}])
        cleared = accounting.mark_resynced(tracked["state"], "ses_a")
        self.assertEqual(accounting.track_events([], cleared)["resync_needed"], [])
        after = accounting.track_events([event("ses_a", 12), event("ses_a", 13)], cleared)
        self.assertEqual(after["resync_needed"], [])
        self.assertEqual(after["state"]["aggregates"]["ses_a"]["last_seq"], 13)

    def test_first_problem_is_kept_until_resynced(self):
        events = [event("ses_a", 0), event("ses_a", 2), event("ses_a", 2, "evt_x"), event("ses_a", 9)]
        self.assertEqual(accounting.track_events(events)["resync_needed"],
                         [{"aggregate_id": "ses_a", "last_good_seq": 0, "reason": "gap"}])

    def test_volatile_usage_updates_never_touch_tracking(self):
        volatile = [e for e in EVENTS if e["durable_seq"] is None]
        self.assertTrue(any(e.get("type") == "session.usage.updated" for e in volatile))
        tracked = accounting.track_events(volatile)
        self.assertEqual(tracked["state"], {"aggregates": {}})
        self.assertEqual(tracked["counts"]["volatile_ignored"], len(volatile))

    def test_state_is_not_mutated(self):
        first = accounting.track_events([event("ses_a", 0)])
        snapshot = copy.deepcopy(first["state"])
        accounting.track_events([event("ses_a", 5)], first["state"])
        accounting.mark_resynced(first["state"], "ses_a")
        self.assertEqual(first["state"], snapshot)


class Resync(unittest.TestCase):
    def test_recompute_equals_from_scratch_and_reports_deltas(self):
        partial = account([m for m in C_MESSAGES if m["type"] != "compaction"], total=None)
        result = accounting.reconcile_with_state(partial, state())
        self.assertEqual(result["accounting"], account())
        self.assertEqual(result["changes"]["tokens_delta"], tokens(6, 236, 0, 26219, 1725))
        self.assertEqual(result["changes"]["messages_added"], 1)
        self.assertEqual(result["changes"]["messages_missing"], 0)
        self.assertEqual(result["changes"]["regressed"], [])
        self.assertIsNone(result["changes"]["usd_micros_delta"])

    def test_repeated_resync_never_double_counts(self):
        once = accounting.reconcile_with_state(None, state())
        twice = accounting.reconcile_with_state(once["accounting"], state())
        self.assertEqual(twice["accounting"], once["accounting"])
        self.assertEqual(twice["changes"]["tokens_delta"], tokens(0, 0, 0, 0, 0))
        self.assertEqual(once["changes"]["tokens_delta"], C_TOTAL)

    def test_disappearing_message_is_reported_as_regression(self):
        full = account()
        fewer = [m for m in C_MESSAGES if m["type"] != "compaction"]
        result = accounting.reconcile_with_state(full, state(fewer, total=None))
        self.assertEqual(result["changes"]["messages_missing"], 1)
        self.assertEqual(result["changes"]["regressed"], list(FIELDS[:2]) + ["cache_read", "cache_write"])

    def test_tracker_is_rebaselined_for_the_session(self):
        tracked = accounting.track_events([event(C_ID, 0), event(C_ID, 4)])
        self.assertEqual(len(tracked["resync_needed"]), 1)
        result = accounting.reconcile_with_state(None, state(), tracker=tracked["state"])
        self.assertEqual(accounting.track_events([], result["tracker"])["resync_needed"], [])

    def test_other_session_previous_is_refused(self):
        other = account(A_MESSAGES, A_TOTAL, A_ID)
        self.assertEqual(assert_fixed_error(self, accounting.reconcile_with_state, other, state()), "session mismatch")

    def test_step_events_carry_tokens_but_are_never_the_source_of_record(self):
        step = next(e for e in EVENTS if e.get("type") == "session.step.ended")
        message = next(m for m in A_MESSAGES if m["message_id"] == step["assistant_message_id"])
        self.assertEqual(step["tokens"], message["tokens"])  # same numbers; state is still what counts
        self.assertEqual(assert_fixed_error(self, account, A_MESSAGES + [step], A_TOTAL, A_ID), "invalid message")


class RateLimits(unittest.TestCase):
    def test_message_rate_limit_is_an_observation_with_explicit_units(self):
        message = next(m for m in C_MESSAGES if m["message_id"] == "msg_fixture30")
        self.assertEqual(accounting.rate_limit_observation(message), {
            "kind": "rate_limit_observation", "source": "opencode.native.rateLimit",
            "units": {"utilization": "fraction_0_1", "resets_at": "epoch_seconds", "observed_at": "epoch_ms"},
            "observed_ms": 1794170366331, "status": "allowed", "rate_limit_type": "five_hour",
            "resets_at_s": 1794178200, "is_using_overage": False, "windows": {
                "five_hour": {"utilization_fraction": 0.17, "resets_at_s": 1794178200, "status": "allowed"},
                "seven_day": {"utilization_fraction": 0.04, "resets_at_s": 1794477600, "status": "allowed"}},
            "unknown_windows": []})
        self.assertEqual(account()["rate_limit"], accounting.rate_limit_observation(message))

    def test_step_event_rate_limit_and_absent_telemetry(self):
        step = next(e for e in EVENTS if e.get("type") == "session.step.ended")
        self.assertEqual(accounting.rate_limit_observation(step)["windows"]["five_hour"]["utilization_fraction"], 0.14)
        self.assertIsNone(accounting.rate_limit_observation(D_MESSAGES[1]))

    def test_percent_looking_utilization(self):
        raw = copy.deepcopy(LINEAGE["aux_session_C"]["messages"][3])
        raw["native"]["rateLimit"]["unifiedWindows"]["five_hour"]["utilization"] = 17
        observed = accounting.rate_limit_observation(opencode.normalize_message(raw))
        self.assertIsNone(observed["windows"]["five_hour"]["utilization_fraction"])
        self.assertEqual(observed["unknown_windows"], ["five_hour"])
        message = next(m for m in C_MESSAGES if m["message_id"] == "msg_fixture30")
        for bad in (17, 1.5, True, math.nan, math.inf, -0.1, "0.5"):
            hostile = copy.deepcopy(message)
            hostile["rate_limit"]["windows"]["five_hour"]["utilization_fraction"] = bad
            assert_fixed_error(self, accounting.rate_limit_observation, hostile)

    def test_zero_completion_time_is_kept(self):
        message = dict(next(m for m in C_MESSAGES if m["message_id"] == "msg_fixture30"), completed_ms=0)
        self.assertEqual(accounting.rate_limit_observation(message)["observed_ms"], 0)

    def test_millisecond_looking_reset_is_rejected(self):
        message = copy.deepcopy(next(m for m in C_MESSAGES if m["message_id"] == "msg_fixture30"))
        message["rate_limit"]["windows"]["seven_day"]["resets_at_s"] = 1794477600000
        self.assertEqual(assert_fixed_error(self, accounting.rate_limit_observation, message), "invalid rate limit")


class Consistency(unittest.TestCase):
    def test_report_on_recorded_session(self):
        self.assertEqual(accounting.consistency_report(state()), {
            "session_id": C_ID, "messages": 5, "by_type": {"assistant": 1, "compaction": 1, "idle": 2, "user": 1},
            "countable": 2, "duplicates": 0, "conflicting_duplicates": 0, "missing_tokens": 0,
            "in_progress": 0, "unknown_records": 0, "missing_model": 0, "complete": True, "title": "known",
            "title_reason": None, "negative_classes": [], "rate_limit_messages": 1, "children_referenced": 0,
            "children_included": 0, "children_unaccounted": 0, "spawns_without_session": 0,
            "tool_calls_dropped": 0, "lineage_unaccounted": 0})
        self.assertIsNone(accounting.consistency_report(state(lineage=None))["lineage_unaccounted"])
        self.assertEqual(accounting.consistency_report(state(D_MESSAGES, D_TOTAL, D_ID))["children_unaccounted"], 1)

    def test_report_counts_problems_without_raising_or_echoing(self):
        messages = (C_MESSAGES + [C_MESSAGES[0], dict(C_MESSAGES[3], tokens=tokens(1, 1, 1, 1, 1)),
                                  {"schema": 1, "client": "opencode", "kind": "unknown", "source": "message",
                                   "type": None, "message_id": "msg_odd"}])
        report = accounting.consistency_report(state(messages, total=dict(C_TOTAL, output=1), complete=False))
        self.assertEqual((report["duplicates"], report["conflicting_duplicates"], report["unknown_records"],
                          report["complete"], report["title"]), (1, 1, 1, False, "unknown"))
        self.assertNotIn("Reply", json.dumps(report))

    def test_report_on_negative_remainder(self):
        report = accounting.consistency_report(state(total=dict(C_TOTAL, output=200, cache_write=0)))
        self.assertEqual(report["negative_classes"], ["output", "cache_write"])
        self.assertEqual(report["title_reason"], "negative_remainder")


class Hostile(unittest.TestCase):
    def calls(self, messages):
        yield accounting.usage_from_messages, (messages, C_TOTAL), {"session_id": C_ID}
        yield accounting.title_tokens, (C_TOTAL, messages), {}
        yield accounting.consistency_report, (state(messages),), {}
        yield accounting.reconcile_with_state, (None, state(messages)), {}

    def check(self, messages):
        before = copy.deepcopy(messages) if not cyclic(messages) else None
        for function, args, kwargs in self.calls(messages):
            assert_fixed_error(self, function, *args, **kwargs)
        if before is not None:
            self.assertEqual(messages, before)

    def test_huge_lists(self):
        self.check([C_MESSAGES[0]] * (accounting.MAX_MESSAGES + 1))
        assert_fixed_error(self, accounting.track_events, [event("ses_a", 0)] * (accounting.MAX_EVENTS + 1))

    def test_bad_token_values(self):
        for bad in (10 ** 30, -1, True, 1.0, math.nan, math.inf, "5", [[[[1]]]], {"x": MARKER}):
            self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], tokens=dict(C_MESSAGES[3]["tokens"], output=bad))])
        self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], tokens={"input": 1, MARKER: 2})])

    def test_bad_cost_values(self):
        for bad in (math.nan, math.inf, -1.0, True, MARKER, 10 ** 30):
            self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], cost=bad)])

    def test_bad_identifiers_and_unicode(self):
        self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], message_id=MARKER)])
        self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], requested={"provider_id": "x", "model_alias": "haïku",
                                                                    "variant": None})])
        self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], tool_calls=[spawn_call("ses_é" + MARKER)])])
        self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], tool_calls_dropped=True)])
        assert_fixed_error(self, account, children=[MARKER])
        assert_fixed_error(self, account, children=[{"session_id": C_ID, "messages": []}])
        self.check("not a list " + MARKER)
        self.check([MARKER])
        assert_fixed_error(self, accounting.usage_from_messages, C_MESSAGES, C_TOTAL, session_id=MARKER)
        assert_fixed_error(self, accounting.usage_from_messages, C_MESSAGES, C_TOTAL, session_id=C_ID, funding=MARKER)
        assert_fixed_error(self, accounting.usage_from_messages, C_MESSAGES, C_TOTAL, session_id=C_ID, complete=1)
        assert_fixed_error(self, accounting.usage_from_messages, C_MESSAGES, C_TOTAL, session_id=C_ID,
                           session_cost=math.nan)

    def test_cyclic_structures(self):
        loop = dict(C_MESSAGES[3])
        loop["requested"] = loop
        self.check(C_MESSAGES[:3] + [loop])
        tokens_loop = dict(C_MESSAGES[3]["tokens"])
        tokens_loop["output"] = tokens_loop
        self.check(C_MESSAGES[:3] + [dict(C_MESSAGES[3], tokens=tokens_loop)])

    def test_totals_beyond_the_ledger_integer_bound(self):
        big = [synthetic(f"msg_b{index}", tokens(10 ** 15, 0, 0, 0, 0)) for index in range(10)]
        self.assertEqual(assert_fixed_error(self, accounting.usage_from_messages, big, None, session_id="ses_b"),
                         "total too large")

    def test_costs_beyond_the_ledger_integer_bound(self):
        for funding in ("subscription", "api"):
            priced = [synthetic("msg_p1", tokens(1, 1, 0, 0, 0), cost=1e10)]
            self.assertEqual(assert_fixed_error(self, accounting.usage_from_messages, priced, None, session_id="ses_p",
                                                funding=funding), "total too large")

    def test_bad_events_and_tracker_state(self):
        for bad in (dict(event("ses_a", 0), durable_seq=True), dict(event("ses_a", 0), durable_seq=-1),
                    dict(event("ses_a", 0), durable_seq=10 ** 30), dict(event("ses_a", 0), durable_aggregate_id=None),
                    dict(event("ses_a", 0), durable_aggregate_id=MARKER), dict(event("ses_a", 0), event_id=MARKER),
                    MARKER, None, [event("ses_a", 0)]):
            assert_fixed_error(self, accounting.track_events, [bad])
        for state_value in (MARKER, {"aggregates": MARKER}, {"aggregates": {"ses_a": {"last_seq": True}}}, {"x": 1}):
            assert_fixed_error(self, accounting.track_events, [], state_value)
        assert_fixed_error(self, accounting.mark_resynced, {"aggregates": {}}, MARKER)

    def test_bad_previous_and_session_state(self):
        for previous in (MARKER, {"session_id": C_ID}, {"session_id": C_ID, "totals": MARKER}):
            assert_fixed_error(self, accounting.reconcile_with_state, previous, state())
        for session_state in (MARKER, {"messages": C_MESSAGES}, state(complete="yes"), dict(state(), cost=math.inf)):
            assert_fixed_error(self, accounting.consistency_report, session_state)

    def test_inputs_are_not_mutated_by_successful_calls(self):
        messages, total = copy.deepcopy(C_MESSAGES), copy.deepcopy(C_TOTAL)
        account(messages, total)
        accounting.title_tokens(total, messages)
        accounting.consistency_report(state(messages, total))
        accounting.reconcile_with_state(account(messages, total), state(messages, total))
        accounting.rate_limit_observation(messages[3])
        self.assertEqual((messages, total), (C_MESSAGES, C_TOTAL))


def cyclic(value, seen=None):
    seen = set() if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return True
        seen = seen | {id(value)}
        return any(cyclic(item, seen) for item in (value.values() if isinstance(value, dict) else value))
    return False


class Purity(unittest.TestCase):
    def test_imports_only_pure_standard_library_and_the_adapter(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.update(f"{node.module}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "print", "exec", "eval", "__import__", "input", "compile"})
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"environ", "getenv", "system", "popen", "urlopen"})
        self.assertLessEqual(names, {"math", "decimal.Decimal", "decimal.ROUND_CEILING", "runtime.adapters.opencode"})

    def test_accounting_performs_no_io(self):
        def refuse(*args, **kwargs):
            raise AssertionError("I/O attempted")
        with patch.object(builtins, "open", refuse), patch("socket.socket", refuse), \
                patch("subprocess.Popen", refuse), patch("os.system", refuse):
            accounting.reconcile_with_state(None, state(), tracker=accounting.track_events(EVENTS)["state"])
            accounting.consistency_report(state())



# Session `ses_fixture3` (agent "nester", child of session D): only its session total is recorded, so the test
# supplies one synthetic message carrying that total.
CHILD_ID = "ses_fixture3"
CHILD_TOTAL = total_of(SESSIONS[CHILD_ID]["tokens"])
CHILD = {"session_id": CHILD_ID, "tokens": CHILD_TOTAL,
         "messages": [synthetic("msg_child1", CHILD_TOTAL), idle("msg_child2", 1794170500000)]}


class ChildSessions(unittest.TestCase):
    def test_recorded_child_is_referenced_by_its_parent_spawn(self):
        self.assertEqual([call["child_session_id"] for m in D_MESSAGES for call in m.get("tool_calls", [])], [CHILD_ID])

    def test_an_unaccounted_child_leaves_the_attempt_cost_unknown(self):
        result = account(D_MESSAGES, D_TOTAL, D_ID)
        self.assertIn("child_session_unaccounted", result["unknown"])
        self.assertEqual(result["title"]["state"], "zero")  # the child's tokens are not in the parent total
        priced = [dict(m, cost=0.01) if m["type"] == "assistant" else m for m in D_MESSAGES]
        api = account(priced, D_TOTAL, D_ID, funding="api", session_cost=0.02)
        self.assertIn("cost_unknown", api["unknown"])
        self.assertIsNone(api["totals"]["usd_micros"])

    def test_included_child_is_summed_into_the_attempt(self):
        result = account(D_MESSAGES, D_TOTAL, D_ID, children=[CHILD])
        self.assertNotIn("child_session_unaccounted", result["unknown"])
        self.assertEqual(result["totals"]["counted"], {f: D_TOTAL[f] + CHILD_TOTAL[f] for f in FIELDS})
        self.assertIn("msg_child1", [r.get("receipt") for r in result["usage"]["requests"]])
        self.assertEqual([s["session_id"] for s in result["sessions"]], [D_ID, CHILD_ID])
        self.assertIsNone(routing._usage_error(result["usage"]))

    def test_child_unknowns_propagate(self):
        running = dict(CHILD, messages=[dict(CHILD["messages"][0], completed_ms=None, finish=None)])
        result = account(D_MESSAGES, D_TOTAL, D_ID, children=[running], funding="api")
        self.assertIn("message_in_progress", result["unknown"])
        self.assertIn("cost_unknown", result["unknown"])

    def test_grandchildren_must_be_included_too(self):
        spawning = dict(CHILD, messages=[dict(CHILD["messages"][0], tool_calls=[spawn_call("ses_grand")]),
                                         CHILD["messages"][1]])
        result = account(D_MESSAGES, D_TOTAL, D_ID, children=[spawning])
        self.assertIn("child_session_unaccounted", result["unknown"])

    def test_dropped_tool_calls_hide_children(self):
        hidden = [dict(m, tool_calls_dropped=3) if m["message_id"] == "msg_fixture34" else m for m in D_MESSAGES]
        self.assertIn("tool_calls_dropped", account(hidden, D_TOTAL, D_ID, children=[CHILD])["unknown"])

    def test_a_session_no_included_message_spawned_is_refused(self):
        self.assertEqual(assert_fixed_error(self, account, children=[CHILD]), "session not in the attempt")

    def test_resync_with_children_equals_from_scratch(self):
        result = accounting.reconcile_with_state(None, state(D_MESSAGES, D_TOTAL, D_ID, children=[CHILD]))
        self.assertEqual(result["accounting"], account(D_MESSAGES, D_TOTAL, D_ID, children=[CHILD]))


class NormalizeSession(unittest.TestCase):
    def test_recorded_sessions(self):
        self.assertEqual(accounting.normalize_session(SESSIONS[C_ID]),
                         {"session_id": C_ID, "parent_session_id": None, "tokens": C_TOTAL, "cost": 0})
        self.assertEqual(accounting.normalize_session(SESSIONS[CHILD_ID])["parent_session_id"], D_ID)

    def test_hostile_sessions(self):
        for bad in (MARKER, dict(SESSIONS[C_ID], id=MARKER), dict(SESSIONS[C_ID], tokens={"input": True}),
                    dict(SESSIONS[C_ID], cost=math.nan), dict(SESSIONS[C_ID], parentID=MARKER)):
            assert_fixed_error(self, accounting.normalize_session, bad)


class LedgerSettlement(ledger_tests.Accounting):
    """The report through the merged S3.6a `record complete` path, on an API-funded dollar hold ($5 padded to $6)."""

    def settle_with(self, attempt, result):
        self.funded(attempt)
        return self.call(ledger_tests.complete(attempt, "c-" + attempt, usage=result["usage"]))

    def api(self, messages, total, cost):
        return accounting.usage_from_messages(messages, total, session_id="ses_l", funding="api", session_cost=cost,
                                              lineage=[])

    def assert_cost_unknown(self, response, attempt):
        self.assertEqual(response["outcome"], "cost_unknown", response)
        hold = self.hold(attempt)
        self.assertEqual((hold["closed"], hold["state"], hold["amount"]), (False, "cost_unknown", 6 * M))

    def test_zero_reported_cost_with_tokens_does_not_release_the_hold(self):
        messages = [synthetic("msg_l1", tokens(50000, 20000, 0, 0, 0), cost=0.0)]
        response = self.settle_with("z1", self.api(messages, tokens(50000, 20000, 0, 0, 0), 0.0))
        self.assert_cost_unknown(response, "z1")

    def test_zero_title_cost_delta_does_not_release_the_hold(self):
        messages = [synthetic("msg_l1", tokens(10, 10, 0, 0, 0), cost=0.01)]
        response = self.settle_with("t1", self.api(messages, tokens(30, 20, 0, 0, 0), 0.01))
        self.assert_cost_unknown(response, "t1")

    def test_mixed_present_and_absent_costs_do_not_release_the_hold(self):
        messages = [synthetic("msg_l1", tokens(10, 10, 0, 0, 0), cost=0.01),
                    synthetic("msg_l2", tokens(10, 10, 0, 0, 0), cost=None)]
        response = self.settle_with("x1", self.api(messages, tokens(20, 20, 0, 0, 0), 0.02))
        self.assert_cost_unknown(response, "x1")

    def test_reported_costs_settle_as_an_estimate_floored_at_the_admitted_estimate(self):
        messages = [synthetic("msg_l1", tokens(10, 10, 0, 0, 0), cost=0.0123)]
        response = self.settle_with("e1", self.api(messages, tokens(12, 15, 0, 0, 0), 0.0124))
        settlement = response["settlement"]
        self.assertEqual((settlement["cost_source"], settlement["charged"], settlement["released"]),
                         ("estimate", 5 * M, M))


def tree_session(session_id, spawns=()):
    """A one-message session whose message spawns `spawns`; ids like `ses_x1` give messages like `msg_x1a`."""
    message = synthetic(f"msg_{session_id[4:]}a", tokens(1, 1, 0, 0, 0), cost=0.01,
                        tool_calls=[spawn_call(child) for child in spawns])
    return {"session_id": session_id, "tokens": tokens(1, 1, 0, 0, 0), "cost": 0.01, "messages": [message]}


def tree(root_spawns, children, funding="subscription", lineage=()):
    root = tree_session("ses_root", root_spawns)
    return accounting.usage_from_messages(root["messages"], root["tokens"], session_id="ses_root", funding=funding,
                                          session_cost=0.01, children=children,
                                          lineage=None if lineage is None else list(lineage))


class SpawnsWithoutSession(unittest.TestCase):
    def with_call(self, call):
        return [dict(m, tool_calls=[call]) if m["message_id"] == "msg_fixture34" else m for m in D_MESSAGES]

    def test_errored_or_running_spawn_without_child_id_leaves_spend_unaccounted(self):
        for status in ("error", "running"):
            messages = self.with_call(spawn_call(None, status))
            result = account(messages, D_TOTAL, D_ID)
            self.assertIn("child_session_unaccounted", result["unknown"])
            self.assertEqual(result["title"]["state"], "zero")
            priced = [dict(m, cost=0.01) if m["type"] == "assistant" else m for m in messages]
            api = account(priced, D_TOTAL, D_ID, funding="api", session_cost=0.02)
            self.assertIn("cost_unknown", api["unknown"])
            self.assertTrue(all(r["cost_usd_micros"] is None for r in api["usage"]["requests"]))
            report = accounting.consistency_report(state(messages, D_TOTAL, D_ID))
            self.assertEqual(report["spawns_without_session"], 1)

    def test_completed_spawn_with_its_child_included_is_accounted(self):
        result = account(D_MESSAGES, D_TOTAL, D_ID, children=[CHILD])
        self.assertNotIn("child_session_unaccounted", result["unknown"])
        priced = [dict(m, cost=0.01) if m["type"] == "assistant" else m for m in D_MESSAGES]
        child = dict(CHILD, cost=0.02, messages=[dict(CHILD["messages"][0], cost=0.02), CHILD["messages"][1]])
        api = account(priced, D_TOTAL, D_ID, funding="api", session_cost=0.02, children=[child])
        self.assertEqual(api["unknown"], [])
        self.assertEqual(api["totals"]["usd_micros"], 40000)

    def test_other_tools_without_a_child_id_are_fine(self):
        messages = self.with_call(spawn_call(None, tool="read"))
        self.assertNotIn("child_session_unaccounted", account(messages, D_TOTAL, D_ID)["unknown"])


class Reachability(unittest.TestCase):
    def test_valid_chain_from_the_root(self):
        result = tree(["ses_a1"], [tree_session("ses_a1", ["ses_a2"]), tree_session("ses_a2")], funding="api")
        self.assertEqual(result["unknown"], [])
        self.assertEqual([s["session_id"] for s in result["sessions"]], ["ses_root", "ses_a1", "ses_a2"])
        self.assertEqual(result["totals"]["usd_micros"], 30000)

    def test_two_parents_referencing_one_child_count_it_once(self):
        result = tree(["ses_b1", "ses_b2"], [tree_session("ses_b1", ["ses_b3"]), tree_session("ses_b2", ["ses_b3"]),
                                             tree_session("ses_b3")])
        self.assertEqual(result["unknown"], [])
        self.assertEqual(result["totals"]["counted"], tokens(4, 4, 0, 0, 0))

    def test_island_of_sessions_spawning_each_other_is_refused(self):
        children = [tree_session("ses_c1"), tree_session("ses_c2", ["ses_c3"]), tree_session("ses_c3", ["ses_c2"])]
        self.assertEqual(assert_fixed_error(self, tree, ["ses_c1"], children), "session not in the attempt")

    def test_link_back_to_the_root_is_refused(self):
        self.assertEqual(assert_fixed_error(self, tree, ["ses_d1"], [tree_session("ses_d1", ["ses_root"])]),
                         "session not in the attempt")

    def test_cycle_below_the_root_is_refused(self):
        children = [tree_session("ses_e1", ["ses_e2"]), tree_session("ses_e2", ["ses_e1"])]
        self.assertEqual(assert_fixed_error(self, tree, ["ses_e1"], children), "session not in the attempt")

    def test_session_bound(self):
        names = [f"ses_f{index}" for index in range(64)]
        self.assertEqual(len(tree(names[:63], [tree_session(name) for name in names[:63]])["sessions"]), 64)
        self.assertEqual(assert_fixed_error(self, tree, names, [tree_session(name) for name in names]),
                         "invalid session")


# The 9 November 2026 live re-qualification (docs/smart-routing-qualification.md, Blocker): the managed root ran a
# `subagent: true` command. OpenCode created the child session directly, linked to the root only by `parentID`, and
# delivered its result to the root as a `synthetic` message. Only shapes and counts were recorded, so the raw messages
# are rebuilt from the recorded types and tokens.
QUAL = load("qual-results.json")["inspection"]
Q_ROOT, Q_CHILD = "ses_qroot", "ses_qchild"
Q_TOTAL = dict(QUAL["managed_session_total_tokens"])
Q_PRIMARY = QUAL["usage_from_messages_root_only"]["requests"][0]["tokens"]
Q_LINEAGE = [{"session_id": Q_ROOT, "parent_session_id": None}, {"session_id": Q_CHILD, "parent_session_id": Q_ROOT}]


def raw_tokens(values):
    return {"input": values["input"], "output": values["output"], "reasoning": values["reasoning"],
            "cache": {"read": values["cache_read"], "write": values["cache_write"]}}


def raw_messages(types, prefix, cost=0):
    """Raw OpenCode messages of the recorded types (`idle(failed)` is an idle message with that outcome)."""
    messages = []
    for index, recorded in enumerate(types):
        kind, _, outcome = recorded.partition("(")
        message = {"id": f"msg_{prefix}{index}", "type": kind, "time": {"created": 1794170600000 + index * 1000}}
        if outcome:
            message["outcome"] = outcome.rstrip(")")
        if kind == "assistant":
            message.update(model={"id": "haiku", "providerID": "claude-agent", "variant": "low"}, finish="stop",
                           tokens=raw_tokens(Q_PRIMARY), cost=cost)
            message["time"]["completed"] = message["time"]["created"] + 500
        messages.append(message)
    return messages


def q_root(cost=0):
    return normalized(raw_messages(QUAL["managed_root_message_types"], "qroot", cost))


def q_child(parent=Q_ROOT):
    session = {"id": Q_CHILD, "parentID": parent, "tokens": raw_tokens(QUAL["command_child"]["tokens"]), "cost": 0}
    return dict(accounting.normalize_session(session), messages=normalized(raw_messages(QUAL["command_child"]["message_types"], "qchild")))


def q_account(children=(), lineage=Q_LINEAGE, funding="subscription", cost=0, session_cost=0):
    return accounting.usage_from_messages(q_root(cost), Q_TOTAL, session_id=Q_ROOT, funding=funding,
                                          session_cost=session_cost, children=list(children), lineage=lineage)


class CommandChildFixture(unittest.TestCase):
    def test_recorded_live_shape(self):
        self.assertTrue(QUAL["command_child"]["parent_is_managed_root"])
        self.assertFalse(QUAL["command_child"]["linked_by_subagent_tool_part"])
        self.assertEqual(QUAL["usage_from_messages_with_child"], "refused: session not in the attempt")
        self.assertFalse(QUAL["usage_from_messages_root_only"]["child_session_unaccounted_flagged"])
        self.assertEqual(QUAL["usage_from_messages_root_only"]["title"]["reason"], "unknown_record")
        self.assertIn("synthetic", QUAL["managed_root_message_types"])
        self.assertEqual(sum(len(m.get("tool_calls") or []) for m in q_root()), 0)
        self.assertEqual([m["kind"] for m in q_root() if m["type"] == "synthetic"], ["synthetic"])

    def test_child_linked_by_parent_id_is_accounted_and_the_title_is_known(self):
        result = q_account([q_child()])
        self.assertEqual(result["unknown"], [])
        title = {field: Q_TOTAL[field] - Q_PRIMARY[field] for field in FIELDS}
        self.assertEqual(title, tokens(2, 8, 0, 0, 1167))
        self.assertEqual(result["title"], {"state": "known", "tokens": title, "reason": None})
        self.assertEqual([s["session_id"] for s in result["sessions"]], [Q_ROOT, Q_CHILD])
        self.assertEqual(result["totals"]["counted"], Q_TOTAL)
        self.assertEqual([r["kind"] for r in result["usage"]["requests"]], ["primary", "title"])
        self.assertIsNone(routing._usage_error(result["usage"]))

    def test_child_without_its_parent_link_is_still_refused(self):
        self.assertEqual(assert_fixed_error(self, q_account, [q_child(parent=None)]), "session not in the attempt")

    def test_omitted_child_in_lineage_is_unaccounted(self):
        for funding in ("subscription", "api"):
            result = q_account(funding=funding, cost=0.0123, session_cost=0.0124)
            self.assertIn("child_session_unaccounted", result["unknown"])
            self.assertEqual(result["title"]["state"], "known")
            self.assertIsNone(result["totals"]["usd_micros"])
            self.assertTrue(all(r["cost_usd_micros"] is None for r in result["usage"]["requests"]))
        self.assertIn("cost_unknown", q_account(funding="api", cost=0.0123, session_cost=0.0124)["unknown"])
        self.assertNotIn("cost_unknown", q_account()["unknown"])

    def test_absent_lineage_is_not_silently_complete(self):
        subscription = q_account([q_child()], lineage=None)
        self.assertEqual(subscription["unknown"], ["lineage_not_checked"])
        self.assertTrue(all(r["cost_usd_micros"] is None for r in subscription["usage"]["requests"]))
        api = q_account([q_child()], lineage=None, funding="api", cost=0.0123, session_cost=0.0124)
        self.assertEqual(api["unknown"], ["cost_unknown", "lineage_not_checked"])
        self.assertIsNone(api["totals"]["usd_micros"])
        priced = q_account([q_child()], funding="api", cost=0.0123, session_cost=0.0124)
        self.assertEqual((priced["unknown"], priced["totals"]["usd_micros"]), ([], 12400))

    def test_synthetic_records_never_count_and_tokens_on_them_are_flagged(self):
        messages = q_root()
        self.assertEqual(accounting.title_tokens(Q_TOTAL, messages)["reason"], None)
        flagged = [dict(m, tokens=tokens(1, 0, 0, 0, 0)) if m["type"] == "synthetic" else m for m in messages]
        self.assertEqual(accounting.title_tokens(Q_TOTAL, flagged)["reason"], "unexpected_tokens")
        zero = [dict(m, tokens=tokens(0, 0, 0, 0, 0)) if m["type"] == "synthetic" else m for m in messages]
        self.assertEqual(accounting.title_tokens(Q_TOTAL, zero)["reason"], None)
        synthetic_record = next(m for m in messages if m["type"] == "synthetic")
        for bad in (dict(synthetic_record, type="assistant"), dict(synthetic_record, kind="message"),
                    dict(synthetic_record, message_id=MARKER), dict(synthetic_record, created_ms=True)):
            assert_fixed_error(self, accounting.title_tokens, Q_TOTAL, [bad])
        report = accounting.consistency_report({"session_id": Q_ROOT, "messages": messages, "tokens": Q_TOTAL})
        self.assertEqual((report["unknown_records"], report["by_type"]["synthetic"], report["title"]), (0, 1, "known"))

    def test_resync_with_children_and_lineage_has_zero_deltas(self):
        session = {"session_id": Q_ROOT, "messages": q_root(), "tokens": Q_TOTAL, "cost": 0,
                   "children": [q_child()], "lineage": Q_LINEAGE}
        once = accounting.reconcile_with_state(None, session)
        self.assertEqual(once["accounting"], q_account([q_child()]))
        twice = accounting.reconcile_with_state(once["accounting"], session)
        self.assertEqual(twice["accounting"], once["accounting"])
        self.assertEqual(twice["changes"]["tokens_delta"], tokens(0, 0, 0, 0, 0))
        self.assertEqual((twice["changes"]["messages_added"], twice["changes"]["messages_missing"]), (0, 0))
        report = accounting.consistency_report(dict(session, children=[]))
        self.assertEqual(report["lineage_unaccounted"], 1)


def linked(session_id, parent, spawns=()):
    return dict(tree_session(session_id, spawns), parent_session_id=parent)


class ParentLinks(unittest.TestCase):
    def test_parent_only_chain_is_reachable(self):
        result = tree([], [linked("ses_p1", "ses_root"), linked("ses_p2", "ses_p1")], funding="api")
        self.assertEqual(result["unknown"], [])
        self.assertEqual(result["totals"]["usd_micros"], 30000)

    def test_tool_link_and_agreeing_parent_link(self):
        result = tree(["ses_p1"], [linked("ses_p1", "ses_root", ["ses_p2"]), linked("ses_p2", "ses_p1"),
                                   linked("ses_p3", "ses_p1")])
        self.assertEqual(result["unknown"], [])
        self.assertEqual(len(result["sessions"]), 4)

    def test_parent_link_contradicting_a_tool_link_is_refused(self):
        children = [linked("ses_p1", "ses_root"), linked("ses_p2", "ses_p1")]
        self.assertEqual(assert_fixed_error(self, tree, ["ses_p2"], children), "session not in the attempt")
        children = [tree_session("ses_p1", ["ses_p2"]), linked("ses_p2", "ses_root")]
        self.assertEqual(assert_fixed_error(self, tree, ["ses_p1"], children), "session not in the attempt")

    def test_parent_outside_the_attempt_is_refused(self):
        self.assertEqual(assert_fixed_error(self, tree, ["ses_p1"], [linked("ses_p1", "ses_elsewhere")]),
                         "session not in the attempt")
        self.assertEqual(assert_fixed_error(self, tree, [], [linked("ses_p1", "ses_root"),
                                                             linked("ses_p2", "ses_elsewhere")]),
                         "session not in the attempt")

    def test_parent_cycle_and_root_with_an_included_parent_are_refused(self):
        children = [linked("ses_p1", "ses_p2"), linked("ses_p2", "ses_p1")]
        self.assertEqual(assert_fixed_error(self, tree, [], children), "session not in the attempt")
        root = tree_session("ses_root")
        session = dict(root, parent_session_id="ses_p1", children=[linked("ses_p1", "ses_root")], lineage=[])
        self.assertEqual(assert_fixed_error(self, accounting.reconcile_with_state, None, session),
                         "session not in the attempt")
        outside = accounting.reconcile_with_state(None, dict(session, parent_session_id="ses_elsewhere"))
        self.assertEqual(len(outside["accounting"]["sessions"]), 2)  # the root's own parent is not in the attempt

    def test_session_bound_with_parent_links(self):
        names = [f"ses_g{index}" for index in range(64)]
        self.assertEqual(len(tree([], [linked(name, "ses_root") for name in names[:63]])["sessions"]), 64)
        self.assertEqual(assert_fixed_error(self, tree, [], [linked(name, "ses_root") for name in names]),
                         "invalid session")

    def test_duplicate_child_is_refused(self):
        self.assertEqual(assert_fixed_error(self, tree, [], [linked("ses_p1", "ses_root")] * 2), "invalid session")

    def test_normalized_session_feeds_a_child_directly(self):
        session = accounting.normalize_session(SESSIONS[CHILD_ID])
        child = dict(session, messages=CHILD["messages"])
        result = account(D_MESSAGES, D_TOTAL, D_ID, children=[child],
                         lineage=[{"session_id": CHILD_ID, "parent_session_id": D_ID}])
        self.assertNotIn("child_session_unaccounted", result["unknown"])


class Lineage(unittest.TestCase):
    def test_descendants_under_the_root_must_be_included(self):
        lineage = [{"session_id": "ses_l1", "parent_session_id": "ses_root"},
                   {"session_id": "ses_l2", "parent_session_id": "ses_l1"}]
        self.assertIn("child_session_unaccounted", tree([], [], lineage=lineage)["unknown"])
        included = tree([], [linked("ses_l1", "ses_root")], lineage=lineage)
        self.assertIn("child_session_unaccounted", included["unknown"])  # the grandchild is missing
        both = tree([], [linked("ses_l1", "ses_root"), linked("ses_l2", "ses_l1")], lineage=lineage)
        self.assertEqual(both["unknown"], [])

    def test_grandchild_of_an_included_child_missing_from_lineage(self):
        lineage = [{"session_id": "ses_l2", "parent_session_id": "ses_l1"}]
        self.assertIn("child_session_unaccounted", tree(["ses_l1"], [tree_session("ses_l1")], lineage=lineage)["unknown"])

    def test_sessions_outside_the_root_are_ignored(self):
        lineage = [{"session_id": "ses_o1", "parent_session_id": None},
                   {"session_id": "ses_o2", "parent_session_id": "ses_o1"},
                   {"session_id": "ses_o3", "parent_session_id": "ses_unknown"}]
        self.assertEqual(tree([], [], lineage=lineage)["unknown"], [])

    def test_contradicting_lineage_is_refused(self):
        lineage = [{"session_id": "ses_l1", "parent_session_id": "ses_other"}]
        self.assertEqual(assert_fixed_error(self, tree, [], [linked("ses_l1", "ses_root")], lineage=lineage),
                         "invalid lineage")
        conflicting = [{"session_id": "ses_l1", "parent_session_id": "ses_root"},
                       {"session_id": "ses_l1", "parent_session_id": "ses_other"}]
        self.assertEqual(assert_fixed_error(self, tree, [], [], lineage=conflicting), "invalid lineage")
        same = [{"session_id": "ses_l1", "parent_session_id": "ses_root"}] * 2
        self.assertIn("child_session_unaccounted", tree([], [], lineage=same)["unknown"])
        rooted = [{"session_id": "ses_root", "parent_session_id": "ses_l1"},
                  {"session_id": "ses_l1", "parent_session_id": "ses_root"}]
        self.assertEqual(assert_fixed_error(self, tree, [], [], lineage=rooted), "invalid lineage")

    def test_lineage_cycle_is_refused(self):
        cycle = [{"session_id": "ses_c1", "parent_session_id": "ses_c2"},
                 {"session_id": "ses_c2", "parent_session_id": "ses_c1"}]
        self.assertEqual(assert_fixed_error(self, tree, [], [], lineage=cycle), "invalid lineage")
        self.assertEqual(assert_fixed_error(self, tree, [], [], lineage=[{"session_id": "ses_c1",
                                                                          "parent_session_id": "ses_c1"}]),
                         "invalid lineage")

    def test_long_chain_within_bounds(self):
        names = [f"ses_k{index}" for index in range(accounting.MAX_LINEAGE)]
        chain = [{"session_id": name, "parent_session_id": parent}
                 for name, parent in zip(names, ["ses_root"] + names[:-1])]
        self.assertIn("child_session_unaccounted", tree([], [], lineage=list(reversed(chain)))["unknown"])

    def test_hostile_lineage(self):
        entry = {"session_id": "ses_h1", "parent_session_id": "ses_root"}
        loop = [entry]
        loop.append(loop)
        for bad in (MARKER, {"x": 1}, [MARKER], [None], [dict(entry, session_id=MARKER)],
                    [dict(entry, parent_session_id=MARKER)], [dict(entry, session_id=True)],
                    [dict(entry, parent_session_id=1)], [dict(entry, extra=MARKER)], [{"session_id": "ses_h1"}],
                    loop, [entry] * (accounting.MAX_LINEAGE + 1)):
            self.assertIn(assert_fixed_error(self, tree, [], [], lineage=bad), ("invalid lineage", "too many sessions"))
            assert_fixed_error(self, accounting.consistency_report, dict(state(), lineage=bad))
        for parent in (True, 1, MARKER, {"x": 1}):
            self.assertEqual(assert_fixed_error(self, tree, [], [dict(tree_session("ses_h1"),
                                                                      parent_session_id=parent)]), "invalid session")


class CommandChildSettlement(ledger_tests.Accounting):
    """The live command-child shape through the merged S3.6a `record complete` path, API-funded."""

    settle_with, assert_cost_unknown = LedgerSettlement.settle_with, LedgerSettlement.assert_cost_unknown

    def test_omitted_command_child_leaves_the_hold_cost_unknown(self):
        result = q_account(funding="api", cost=0.0123, session_cost=0.0124)
        self.assert_cost_unknown(self.settle_with("q1", result), "q1")

    def test_absent_lineage_leaves_the_hold_cost_unknown(self):
        result = q_account([q_child()], lineage=None, funding="api", cost=0.0123, session_cost=0.0124)
        self.assert_cost_unknown(self.settle_with("q2", result), "q2")

    def test_accounted_command_child_settles_as_an_estimate(self):
        result = q_account([q_child()], funding="api", cost=0.0123, session_cost=0.0124)
        settlement = self.settle_with("q3", result)["settlement"]
        self.assertEqual((settlement["cost_source"], settlement["charged"], settlement["released"]),
                         ("estimate", 5 * M, M))


if __name__ == "__main__":
    unittest.main()
