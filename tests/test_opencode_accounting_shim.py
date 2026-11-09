"""S3.3c: the launcher's lineage cross-checked against the shim's own recorded descendant set (`routing.descendants`)."""
import copy
import json
import random
import unittest

from runtime import opencode_accounting as accounting
from runtime import routing
from tests import fixture_routing_accounting as ledger_tests
from tests import test_opencode_accounting_lineage as lineage_tests
from tests.fixture_opencode_accounting import assert_fixed_error, MARKER

Q_ROOT, Q_CHILD, Q_LINEAGE = lineage_tests.Q_ROOT, lineage_tests.Q_CHILD, lineage_tests.Q_LINEAGE
GHOST = "ses_qghost"  # a child the shim recorded and the launcher's listing missed


def shim(*entries, complete=True, truncated=False, root=Q_ROOT, reason=None):
    """A `routing.descendants` answer, as the plugin returns it."""
    descendants = [{"session_id": session, "parent_session_id": parent} for session, parent in entries]
    return {"state": "ok", "root": root, "descendants": descendants, "truncated": truncated, "complete": complete,
            "reason": reason if reason is not None or complete else "event_stream_unavailable",
            "cache": {"recorded": len(descendants), "retired": 0}}


def account(shim_descendants, children=True, lineage=Q_LINEAGE, funding="api"):
    priced = funding == "api"
    return accounting.usage_from_messages(
        lineage_tests.q_root(0.0123 if priced else 0), lineage_tests.Q_TOTAL, session_id=Q_ROOT, funding=funding,
        session_cost=0.0124 if priced else 0, children=[lineage_tests.q_child()] if children else [],
        lineage=lineage, shim_descendants=shim_descendants)


def costs(result):
    return [request["cost_usd_micros"] for request in result["usage"]["requests"]]


class CrossCheck(unittest.TestCase):
    def test_agreeing_sets_settle_with_a_dollar_figure(self):
        result = account(shim((Q_CHILD, Q_ROOT)))
        self.assertEqual((result["unknown"], result["totals"]["usd_micros"]), ([], 12400))
        self.assertIsNone(routing._usage_error(result["usage"]))

    def test_a_launcher_child_the_shim_did_not_record_is_fine(self):
        self.assertEqual(account(shim())["unknown"], [])

    def test_a_shim_child_missing_from_the_listing_and_children_is_unaccounted(self):
        for parent in (Q_ROOT, Q_CHILD, None):  # a null parent: the shim recorded it without a direct link
            result = account(shim((Q_CHILD, Q_ROOT), (GHOST, parent)))
            self.assertEqual(result["unknown"], ["child_session_unaccounted", "cost_unknown"], parent)
            self.assertIsNone(result["totals"]["usd_micros"])
            self.assertTrue(all(cost is None for cost in costs(result)))
        subscription = account(shim((GHOST, None)), funding="subscription")
        self.assertEqual(subscription["unknown"], ["child_session_unaccounted"])

    def test_a_shim_child_the_launcher_included_but_did_not_list_is_accounted(self):
        result = account(shim((Q_CHILD, Q_ROOT)), lineage=[])
        self.assertEqual((result["unknown"], result["totals"]["usd_micros"]), ([], 12400))

    def test_the_shim_set_fills_in_a_launcher_that_left_a_child_out_of_both(self):
        # The #146 hole: no child in `children` and none in `lineage` used to settle complete.
        self.assertEqual(account(None, children=False, lineage=[])["unknown"], ["cost_unknown", "shim_lineage_not_checked"])
        result = account(shim((Q_CHILD, Q_ROOT)), children=False, lineage=[])
        self.assertIn("child_session_unaccounted", result["unknown"])
        self.assertIsNone(result["totals"]["usd_micros"])

    def test_contradicting_parents_are_refused(self):
        for bad in (shim((Q_CHILD, GHOST)),  # the listing and the verified parentID say the root
                    shim((Q_ROOT, None)), shim((Q_ROOT, Q_CHILD)),  # the root is never its own descendant
                    shim((GHOST, GHOST)), shim((GHOST, Q_ROOT), (GHOST, Q_CHILD))):
            self.assertEqual(assert_fixed_error(self, account, bad), "invalid lineage")
        listing = Q_LINEAGE + [{"session_id": GHOST, "parent_session_id": Q_ROOT}]
        self.assertEqual(assert_fixed_error(self, account, shim((GHOST, Q_CHILD)), lineage=listing), "invalid lineage")
        unparented = Q_LINEAGE + [{"session_id": GHOST, "parent_session_id": None}]  # listed as a root of its own
        self.assertEqual(assert_fixed_error(self, account, shim((GHOST, Q_ROOT)), lineage=unparented), "invalid lineage")
        self.assertEqual(assert_fixed_error(self, account, shim((Q_CHILD, GHOST)), lineage=[]), "invalid lineage")
        same = account(shim((GHOST, None)), lineage=listing)  # a null shim parent never contradicts
        self.assertEqual(same["unknown"], ["child_session_unaccounted", "cost_unknown"])  # listed, not included

    def test_an_incomplete_or_truncated_shim_set_makes_api_cost_unknown(self):
        for answer in (shim((Q_CHILD, Q_ROOT), complete=False), shim((Q_CHILD, Q_ROOT), truncated=True,
                                                                    complete=False, reason="response_truncated"),
                       shim((Q_CHILD, Q_ROOT), truncated=True)):
            result = account(answer)
            self.assertEqual(result["unknown"], ["cost_unknown", "shim_lineage_incomplete"])
            self.assertTrue(all(cost is None for cost in costs(result)))
            subscription = account(answer, funding="subscription")
            self.assertEqual(subscription["unknown"], ["shim_lineage_incomplete"])
            self.assertEqual(subscription["totals"]["counted"], account(shim((Q_CHILD, Q_ROOT)),
                                                                        funding="subscription")["totals"]["counted"])

    def test_no_shim_set_is_not_silently_complete(self):
        api = account(None)
        self.assertEqual(api["unknown"], ["cost_unknown", "shim_lineage_not_checked"])
        self.assertTrue(all(cost is None for cost in costs(api)))
        self.assertEqual(account(None, funding="subscription")["unknown"], ["shim_lineage_not_checked"])
        both = account(None, lineage=None)
        self.assertEqual(both["unknown"], ["cost_unknown", "lineage_not_checked", "shim_lineage_not_checked"])
        # A launcher listing is still required: the shim set does not stand in for it.
        alone = account(shim((Q_CHILD, Q_ROOT)), lineage=None)
        self.assertEqual(alone["unknown"], ["cost_unknown", "lineage_not_checked"])

    def test_without_the_argument_the_existing_contract_is_unchanged(self):
        before = lineage_tests.q_account([lineage_tests.q_child()], funding="api", cost=0.0123, session_cost=0.0124)
        self.assertEqual((before["unknown"], before["totals"]["usd_micros"]), ([], 12400))

    def test_hostile_shim_answers_are_refused_with_fixed_reasons(self):
        good = shim((Q_CHILD, Q_ROOT))
        loop = dict(good)
        loop["descendants"] = [good["descendants"][0]]
        loop["descendants"].append(loop["descendants"])
        entry = good["descendants"][0]
        bads = [MARKER, [], 7, True, {}, dict(good, state="not_ready"), dict(good, state=MARKER),
                dict(good, descendants=MARKER), dict(good, descendants=[MARKER]), dict(good, descendants=[None]),
                dict(good, descendants=[dict(entry, session_id=MARKER)]),
                dict(good, descendants=[dict(entry, parent_session_id=MARKER)]),
                dict(good, descendants=[dict(entry, extra=MARKER)]), dict(good, descendants=[{"session_id": Q_CHILD}]),
                dict(good, complete=1), dict(good, complete=None), dict(good, truncated="no"), dict(good, reason=MARKER),
                dict(good, reason=7), dict(good, cache=MARKER), dict(good, extra=MARKER), dict(good, root=MARKER),
                {key: value for key, value in good.items() if key != "complete"}, loop]
        for bad in bads:
            self.assertIn(assert_fixed_error(self, account, bad), ("invalid shim lineage",), repr(bad)[:80])
        self.assertEqual(assert_fixed_error(self, account, dict(good, root="ses_other")), "session mismatch")
        many = dict(good, descendants=[{"session_id": f"ses_m{index}", "parent_session_id": Q_ROOT}
                                       for index in range(accounting.MAX_LINEAGE + 1)])
        self.assertEqual(assert_fixed_error(self, account, many), "too many sessions")

    def test_deterministic_and_pure(self):
        answer = shim((Q_CHILD, Q_ROOT), (GHOST, Q_CHILD), ("ses_qother", None))
        listing = Q_LINEAGE + [{"session_id": "ses_qlisted", "parent_session_id": Q_CHILD}]
        frozen = copy.deepcopy((answer, listing))
        first = account(answer, lineage=listing)
        self.assertEqual((answer, listing), frozen)
        shuffled = copy.deepcopy(answer)
        random.Random(33).shuffle(shuffled["descendants"])
        self.assertEqual(json.dumps(account(shuffled, lineage=listing[::-1]), sort_keys=True),
                         json.dumps(first, sort_keys=True))

    def test_resync_takes_the_shim_set_from_the_session_state(self):
        state = {"session_id": Q_ROOT, "messages": lineage_tests.q_root(0.0123), "tokens": lineage_tests.Q_TOTAL,
                 "cost": 0.0124, "children": [lineage_tests.q_child()], "lineage": Q_LINEAGE}
        for answer, unknown in ((shim((Q_CHILD, Q_ROOT)), []), (shim((GHOST, None)),
                                                                 ["child_session_unaccounted", "cost_unknown"]),
                                (None, ["cost_unknown", "shim_lineage_not_checked"])):
            resynced = accounting.reconcile_with_state(None, dict(state, shim_descendants=answer), funding="api")
            self.assertEqual(resynced["accounting"]["unknown"], unknown)
        self.assertEqual(accounting.reconcile_with_state(None, state, funding="api")["accounting"]["unknown"], [])


class Helper(unittest.TestCase):
    def test_merged_lineage_and_flags(self):
        listing = [{"session_id": Q_CHILD, "parent_session_id": Q_ROOT}]
        answer = shim((Q_CHILD, Q_ROOT), ("ses_qz", Q_CHILD), ("ses_qa", None))
        self.assertEqual(accounting.cross_check_lineage(listing, answer, Q_ROOT), {
            "lineage": [{"session_id": Q_CHILD, "parent_session_id": Q_ROOT},
                        {"session_id": "ses_qz", "parent_session_id": Q_CHILD}],
            "unknown": ["child_session_unaccounted"], "unlisted": 2})
        self.assertEqual(accounting.cross_check_lineage(listing, answer, Q_ROOT, children=["ses_qa", "ses_qz"]),
                         {"lineage": [{"session_id": Q_CHILD, "parent_session_id": Q_ROOT},
                                      {"session_id": "ses_qz", "parent_session_id": Q_CHILD}],
                          "unknown": [], "unlisted": 0})
        self.assertEqual(accounting.cross_check_lineage(listing, None, Q_ROOT),
                         {"lineage": listing, "unknown": ["shim_lineage_not_checked"], "unlisted": 0})
        self.assertEqual(accounting.cross_check_lineage(None, shim(complete=False), Q_ROOT),
                         {"lineage": [], "unknown": ["shim_lineage_incomplete"], "unlisted": 0})

    def test_helper_errors_are_fixed(self):
        for args in ((MARKER, shim(), Q_ROOT), ([], shim(), MARKER), ([], shim(), Q_ROOT, MARKER),
                     ([], shim(), Q_ROOT, [MARKER])):
            reason = assert_fixed_error(self, accounting.cross_check_lineage, *args)
            self.assertIn(reason, ("invalid lineage", "invalid session"))


class ShimSettlement(ledger_tests.Accounting):
    """A launcher that missed a shim-recorded child, through the merged S3.6a `record complete` path, API-funded."""

    settle_with = lineage_tests.LedgerSettlement.settle_with
    assert_cost_unknown = lineage_tests.LedgerSettlement.assert_cost_unknown

    def test_missed_shim_child_leaves_the_hold_cost_unknown(self):
        self.assert_cost_unknown(self.settle_with("s1", account(shim((Q_CHILD, Q_ROOT), (GHOST, Q_ROOT)))), "s1")

    def test_incomplete_or_absent_shim_set_leaves_the_hold_cost_unknown(self):
        self.assert_cost_unknown(self.settle_with("s2", account(shim((Q_CHILD, Q_ROOT), complete=False))), "s2")
        self.assert_cost_unknown(self.settle_with("s3", account(None)), "s3")

    def test_agreeing_shim_set_settles_as_an_estimate(self):
        settlement = self.settle_with("s4", account(shim((Q_CHILD, Q_ROOT))))["settlement"]
        self.assertEqual((settlement["cost_source"], settlement["charged"], settlement["released"]),
                         ("estimate", 5 * ledger_tests.M, ledger_tests.M))


if __name__ == "__main__":
    unittest.main()
