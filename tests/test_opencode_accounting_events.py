"""OpenCode event-gap detection, resync, rate limits, hostile input and purity (S3.6b-1) against the recorded OpenCode 2.2.6 probe payloads."""
import ast
import builtins
import copy
import math
import unittest
from unittest.mock import patch

from runtime import opencode_accounting as accounting
from runtime.adapters import opencode
from tests.fixture_opencode_accounting import (
    A_ID,
    A_MESSAGES,
    A_TOTAL,
    account,
    assert_fixed_error,
    C_ID,
    C_MESSAGES,
    C_TOTAL,
    D_MESSAGES,
    event,
    EVENTS,
    FIELDS,
    LINEAGE,
    MARKER,
    MODULE,
    spawn_call,
    state,
    synthetic,
    tokens,
    tree,
)


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


if __name__ == "__main__":
    unittest.main()
