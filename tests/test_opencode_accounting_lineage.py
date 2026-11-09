"""OpenCode child sessions, lineage, reachability and ledger settlement (S3.6b-1) against the recorded OpenCode 2.2.6 probe payloads."""
import json
import math
import unittest

from runtime import opencode_accounting as accounting
from runtime import routing
from tests import fixture_routing_accounting as ledger_tests
from tests.fixture_opencode_accounting import (
    account,
    assert_fixed_error,
    C_ID,
    C_MESSAGES,
    C_TOTAL,
    CHILD,
    CHILD_ID,
    CHILD_TOTAL,
    D_ID,
    D_MESSAGES,
    D_TOTAL,
    FIELDS,
    load,
    M,
    MARKER,
    normalized,
    SESSIONS,
    spawn_call,
    state,
    synthetic,
    tokens,
    tree,
    tree_session,
)


class Consistency(unittest.TestCase):
    def test_report_on_recorded_session(self):
        self.assertEqual(accounting.consistency_report(state()), {
            "session_id": C_ID, "messages": 5, "by_type": {"assistant": 1, "compaction": 1, "idle": 2, "user": 1},
            "countable": 2, "duplicates": 0, "conflicting_duplicates": 0, "missing_tokens": 0,
            "in_progress": 0, "unknown_records": 0, "missing_model": 0, "complete": True, "title": "known",
            "title_reason": None, "negative_classes": [], "rate_limit_messages": 1, "children_referenced": 0,
            "children_included": 0, "children_unaccounted": 0, "spawns_without_session": 0,
            "tool_calls_dropped": 0, "lineage_unaccounted": 0, "lineage_conflicts": 0, "lineage_loops": 0})
        unchecked = accounting.consistency_report(state(lineage=None))
        self.assertEqual((unchecked["lineage_unaccounted"], unchecked["lineage_conflicts"], unchecked["lineage_loops"]),
                         (None, None, None))

    def test_report_counts_lineage_problems_without_raising(self):
        child = linked("ses_l1", C_ID)
        lineage = [{"session_id": "ses_l1", "parent_session_id": "ses_other"},  # contradicts the child's link
                   {"session_id": "ses_l2", "parent_session_id": C_ID},
                   {"session_id": "ses_l2", "parent_session_id": "ses_other"},  # two parents
                   {"session_id": "ses_c1", "parent_session_id": "ses_c2"},
                   {"session_id": "ses_c2", "parent_session_id": "ses_c1"},  # a loop
                   {"session_id": "ses_l3", "parent_session_id": "ses_l1"}]
        report = accounting.consistency_report(state(children=[child], lineage=lineage))
        self.assertEqual((report["lineage_conflicts"], report["lineage_loops"], report["lineage_unaccounted"]),
                         (2, 1, 2))
        rooted = [{"session_id": C_ID, "parent_session_id": "ses_l1"}]
        self.assertEqual(accounting.consistency_report(state(children=[child], lineage=rooted))["lineage_loops"], 1)
        tool_linked = [{"session_id": CHILD_ID, "parent_session_id": D_ID}]
        report = accounting.consistency_report(state(D_MESSAGES, D_TOTAL, D_ID, children=[CHILD], lineage=tool_linked))
        self.assertEqual((report["lineage_conflicts"], report["lineage_loops"], report["lineage_unaccounted"]),
                         (0, 0, 0))
        for name, bad in (("conflict", lineage[:1]), ("two parents", lineage[1:3]), ("loop", lineage[3:5])):
            with self.subTest(name):
                self.assertEqual(assert_fixed_error(self, accounting.usage_from_messages, C_MESSAGES, C_TOTAL,
                                                    session_id=C_ID, children=[child], lineage=bad), "invalid lineage")
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
