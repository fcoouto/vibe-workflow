"""The strict replay over exported admission records (plan Task F7.1, validation Edit 7 points 2, 3 and 6), on
synthetic exports with invented names: nothing but the caller's temporary export directory is opened, no model and no
network. Ranked records are recomputed through `routing_funding.choose`, the function admission itself calls."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

from runtime import routing, routing_funding
from tests.qualification import replay_export, replay_strict
from tests.qualification.doubles import Temp

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_800_000_000
DAY = 86_400
SHARES = dict(api=0.0, subscription=1.0)


def constraint(window="5h", headroom=900, cost=10, resets_at=NOW + 3600):
    return dict(window=window, headroom=headroom, cost=cost, resets_at=resets_at)


def candidate(route, strategy, pool, *constraints):
    return dict(route=route, strategy_id=strategy, cls="subscription", pool=pool,
                constraints=[dict(c) for c in (constraints or (constraint(),))])


def ranked(attempt, at, candidates=None, shortlist=None, chosen=0, revision=4, bucket=("implementation", "low")):
    """A ranked record in `routing_funding.record`'s shape: the chosen candidate's constraints (pool restored) in
    `pressure.constraints`, the feasible candidates in shortlist order, the reservation padded by 20%."""
    candidates = candidates or [candidate("route-a", "strategy-a", "pool-a"),
                                candidate("route-b", "strategy-b", "pool-b", constraint(headroom=500))]
    pick = candidates[chosen]
    full = [dict(c, pool=pick["pool"]) for c in pick["constraints"]]
    reserved = {routing._window_key(pick["pool"], c["window"]): c["cost"] * 12 // 10 for c in pick["constraints"]}
    return dict(attempt_id=attempt, task="task-" + attempt, bucket=list(bucket), shares=dict(SHARES),
                **{"class": "subscription"}, route=pick["route"], pool=pick["pool"], account="account-invented",
                route_identity="identity-" + attempt, billing_class="included_subscription", credential_ref=None,
                pressure=dict(u=None, e=None, pressure=0.025, constraints=full, epoch=0, feasible=["subscription"],
                              minimum_horizon=900, allowed_harnesses=[], candidates=copy.deepcopy(candidates)),
                reserved=reserved, settings_revision=revision, organization_revision=None, at=at, mode="ranked",
                snapshot_id="snapshot-" + attempt, reason_codes=[],
                ranking=dict(table_version="table-invented", profile_digest="digest-" + attempt,
                             shortlist=shortlist or [c["strategy_id"] for c in candidates] + ["strategy-z"],
                             strategy_id=pick["strategy_id"], score_version=None, routes_version=None),
                risk_band="medium", floor_filled=False, strategy={}, binding=dict(v=1, job=None, credential=None),
                nonce="nonce-" + attempt)


def pilot(attempt, at, headroom=900, cost=10, reserved=12):
    """A copy of the pilot's record shape: `worker_choice`, one constraint, no `snapshot_id`, no replay fields."""
    return dict(attempt_id=attempt, task="task-" + attempt, bucket=["review", "medium"], shares=dict(SHARES),
                **{"class": "subscription"}, route="sub", pool="pilot-sub", account="anthropic:pilot-canary",
                route_identity="identity-" + attempt, billing_class="included_subscription", credential_ref=None,
                pressure=dict(u=None, e=None, pressure=None, constraints=[
                    dict(pool="pilot-sub", window="5h", headroom=headroom, cost=cost, resets_at=None)]),
                reserved={"win:pilot-sub/5h": reserved}, settings_revision=1, organization_revision=None, at=at,
                mode="worker_choice", reason_codes=[], ranking=dict(table_version=None, profile_digest="d"),
                nonce="nonce-" + attempt)


def current_choice(attempt, at):
    """A new `worker_choice` record: `ranking.shortlist: []`, no `pressure.candidates`."""
    record = pilot(attempt, at)
    record["snapshot_id"] = None
    record["pressure"].update(epoch=0, feasible=["subscription"], minimum_horizon=900, allowed_harnesses=[])
    record["ranking"].update(shortlist=[], strategy_id=None, score_version=None, routes_version=None)
    return record


class StrictReplayTests(Temp):
    def setUp(self):
        super().setUp()
        self.export = self.private(self.tmp / "export")

    def write(self, records, identity="ledger-invented"):
        with open(self.export / replay_export.RECORDS, "a") as handle:
            for record in records:
                key = "%s|%s|%s" % (identity, record.get("attempt_id"), record.get("nonce"))
                handle.write(json.dumps(dict(key=key, ledger_identity=identity, exported_at=NOW + 5 * DAY,
                                             snapshot_detail=all(k in record for k in replay_export.DETAIL),
                                             record=record), sort_keys=True) + "\n")
        os.chmod(self.export / replay_export.RECORDS, 0o600)

    def run_replay(self, records, **kwargs):
        self.write(records, **kwargs)
        return replay_strict.run(self.export)

    def test_reproduced_export_passes(self):
        report = self.run_replay([ranked("a-1", NOW), ranked("a-2", NOW + 60, bucket=("review", "medium")),
                                  current_choice("a-3", NOW + 120)])
        self.assertTrue(report["passed"], report)
        self.assertEqual((report["records"], report["class_reproduced"], report["route_reproduced"]), (3, 2, 2))
        self.assertEqual(report["legacy_records"], 1)
        self.assertEqual(report["worker_choice"], dict(old=0, new=1, failed=0))
        self.assertEqual((report["funding_violations"], report["resurrected_capacity"], report["stray_records"]),
                         (0, 0, 0))
        self.assertEqual(report["specialists"], dict(implementation=1, review=2))
        self.assertEqual(report["bands"], dict(low=1, medium=2))

    def test_a_null_routes_version_is_audit_data_not_corruption(self):
        record = ranked("a-1", NOW)
        self.assertIsNone(record["ranking"]["routes_version"])
        with mock.patch.object(routing, "load_route_table", create=True, side_effect=AssertionError("no lookup")):
            self.assertTrue(self.run_replay([record])["passed"])

    def test_a_changed_class_fails(self):
        record = ranked("a-1", NOW)
        record["class"] = "api"
        report = self.run_replay([record])
        self.assertFalse(report["passed"])
        self.assertEqual((report["class_reproduced"], report["route_reproduced"]), (0, 1))

    def test_a_changed_route_fails(self):
        report = self.run_replay([ranked("a-1", NOW, chosen=1)])  # pool-a has more pressure: `choose` picks route-a
        self.assertFalse(report["passed"])
        self.assertEqual((report["class_reproduced"], report["route_reproduced"]), (1, 0))

    def test_a_reordered_shortlist_fails(self):
        record = ranked("a-1", NOW)
        record["ranking"]["shortlist"] = ["strategy-b", "strategy-a"]  # candidates are no longer a subsequence
        report = self.run_replay([record])
        self.assertFalse(report["passed"])
        self.assertEqual(report["route_reproduced"], 0)
        other = ranked("a-2", NOW)
        other["ranking"]["strategy_id"] = "strategy-b"  # the chosen candidate's id must equal the stored one
        self.assertEqual(replay_strict.replay_records([other])["route_reproduced"], 0)

    def test_a_duplicate_attempt_is_a_stray_record(self):
        first = ranked("a-1", NOW)
        second = ranked("a-1", NOW + 60)
        second["nonce"] = "nonce-other"
        report = self.run_replay([first, second])
        self.assertEqual(report["stray_records"], 1)
        self.assertFalse(report["passed"])

    def test_reserved_beyond_the_stored_headroom_is_a_funding_violation(self):
        record = ranked("a-1", NOW)
        record["reserved"] = {"win:pool-a/5h": 901}
        report = self.run_replay([record])
        self.assertEqual(report["funding_violations"], 1)
        self.assertFalse(report["passed"])
        over = ranked("a-2", NOW, candidates=[candidate("route-a", "strategy-a", "pool-a", constraint(cost=950))])
        self.assertEqual(replay_strict.replay_records([over])["funding_violations"], 1)  # cost beyond headroom

    def test_a_later_record_with_more_headroom_and_no_reset_is_resurrected_capacity(self):
        later = ranked("a-2", NOW + 60, candidates=[candidate("route-a", "strategy-a", "pool-a",
                                                              constraint(headroom=950))])
        report = self.run_replay([ranked("a-1", NOW), later])
        self.assertEqual(report["resurrected_capacity"], 1)
        self.assertFalse(report["passed"])
        after_reset = ranked("a-3", NOW + 3600, candidates=[candidate("route-a", "strategy-a", "pool-a",
                                                                      constraint(headroom=990, resets_at=NOW + 7200))])
        self.assertEqual(replay_strict.replay_records([ranked("a-1", NOW), after_reset])["resurrected_capacity"], 0)
        lower = ranked("a-4", NOW + 60, candidates=[candidate("route-a", "strategy-a", "pool-a",
                                                              constraint(headroom=880))])
        self.assertEqual(replay_strict.replay_records([ranked("a-1", NOW), lower])["resurrected_capacity"], 0)

    def test_ranked_paid_record_fails_and_is_counted(self):
        record = ranked("a-1", NOW)
        record.update(billing_class="api_prepaid", shares=dict(api=1.0, subscription=0.0), **{"class": "api"})
        record["pressure"]["candidates"][0]["cls"] = "api"
        report = self.run_replay([record, ranked("a-2", NOW + 60)])
        self.assertEqual(report["ranked_paid"], 1)
        self.assertEqual(report["records"], 2)  # counted, never skipped
        self.assertFalse(report["passed"])
        shares = ranked("a-3", NOW)
        shares["shares"] = dict(api=0.5, subscription=0.5)  # a ranked share other than the fixed subscription-only one
        self.assertEqual(replay_strict.replay_records([shares])["ranked_paid"], 1)

    def test_earlier_records_follow_the_routers_own_filters(self):
        records = [ranked("old", NOW - 15 * DAY), ranked("other-revision", NOW - DAY, revision=3),
                   ranked("kept", NOW - DAY), ranked("subject", NOW), ranked("later", NOW + 60)]
        seen = {}
        real = routing_funding.choose

        def spy(candidates, shares, rows, *rest):
            seen.setdefault(len(seen), [row["attempt_id"] for row in rows])
            return real(candidates, shares, rows, *rest)
        with mock.patch.object(routing_funding, "choose", side_effect=spy), \
                mock.patch.object(routing, "decision_rows", wraps=routing.decision_rows) as filters:
            report = self.run_replay(list(reversed(records)))  # file order is not `at` order
        self.assertTrue(report["passed"], report)
        self.assertEqual(seen[3], ["kept"])  # same revision, at most 14 days older, earlier only
        self.assertEqual(seen[4], ["kept", "subject"])
        self.assertEqual(filters.call_count, 5)

    def test_identities_replay_separately(self):
        self.write([ranked("a-1", NOW)], identity="ledger-one")
        self.write([ranked("a-1", NOW + 60, candidates=[candidate("route-a", "strategy-a", "pool-a",
                                                                  constraint(headroom=990))])], identity="ledger-two")
        report = replay_strict.run(self.export)
        self.assertEqual((report["stray_records"], report["resurrected_capacity"]), (0, 0))
        self.assertTrue(report["passed"], report)

    def test_legacy_worker_choice_records_follow_the_legacy_rule(self):
        records = [pilot("p-%02d" % index, NOW + index) for index in range(1, 4)]
        self.assertNotIn("snapshot_id", records[0])
        report = self.run_replay(records)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["worker_choice"], dict(old=3, new=0, failed=0))
        self.assertEqual((report["legacy_records"], report["class_reproduced"], report["route_reproduced"]), (3, 0, 0))
        wrong_class = pilot("p-04", NOW + 4)
        wrong_class["class"] = "api"
        too_big = pilot("p-05", NOW + 5, reserved=901)
        no_constraint = pilot("p-06", NOW + 6)
        no_constraint["pressure"]["constraints"] = []
        report = replay_strict.replay_records(records + [wrong_class, too_big, no_constraint])
        self.assertEqual(report["worker_choice"]["failed"], 3)
        self.assertFalse(report["passed"])

    def test_a_new_worker_choice_record_claims_no_ranked_route(self):
        record = current_choice("c-1", NOW)
        record["ranking"]["shortlist"] = ["strategy-a"]
        report = replay_strict.replay_records([current_choice("c-0", NOW - 1), record])
        self.assertEqual(report["worker_choice"], dict(old=0, new=2, failed=1))
        self.assertFalse(report["passed"])

    def test_a_record_without_snapshot_detail_fails_and_is_counted(self):
        record = ranked("a-1", NOW)
        for key in replay_export.DETAIL:
            record.pop(key)
        report = self.run_replay([record, ranked("a-2", NOW + 60)])
        self.assertEqual((report["records"], report["missing_detail"]), (2, 1))
        self.assertFalse(report["passed"])
        candidates_gone = ranked("a-3", NOW)
        del candidates_gone["pressure"]["candidates"]  # never replayed by the legacy rule instead
        self.assertEqual(replay_strict.replay_records([candidates_gone])["missing_detail"], 1)

    def test_an_empty_or_unreadable_export_does_not_pass(self):
        self.assertFalse(replay_strict.run(self.export)["passed"])
        (self.export / replay_export.RECORDS).write_text("not json\n")
        os.chmod(self.export / replay_export.RECORDS, 0o600)
        report = replay_strict.run(self.export)
        self.assertEqual(report["unreadable"], 1)
        self.assertFalse(report["passed"])

    def test_the_report_holds_no_identifier(self):
        records = [ranked("attempt-secret-1", NOW), pilot("attempt-secret-2", NOW + 60)]
        report = self.run_replay(records)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = replay_strict.main(["--dir", str(self.export)])
        self.assertEqual(code, 0, out.getvalue())
        for text in (json.dumps(report), out.getvalue()):
            for secret in ("attempt-secret", "task-", "ledger-invented", "nonce-", "route-a", "pool-a", "account",
                           "identity-", "snapshot-", "digest-", "table-invented", "strategy-", str(NOW)[:6]):
                self.assertNotIn(secret, text)

    def test_the_command_exits_one_unless_passed(self):
        record = ranked("a-1", NOW)
        record["class"] = "api"
        self.write([record])
        env = dict(os.environ, HOME=str(self.home))
        done = subprocess.run([sys.executable, "-B", "-m", "tests.qualification.replay_strict", "--dir",
                               str(self.export)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertFalse(json.loads(done.stdout)["passed"])

    def test_records_of_the_real_admission_command_replay(self):
        """Worker-choice records written by the real routing command on temporary stores, exported by the real
        exporter, replay under point 3 (production-shaped; the pilot driver closes each before the next)."""
        from tests.qualification import canary
        from tests.qualification.doubles import qual_plan
        from tests.qualification.server import Routing
        state = self.private(self.tmp / "state")
        ledger = state / "ledger.sqlite3"
        service = Routing(state / "settings.json", ledger)
        canary.initialize_state(service)
        for item in qual_plan(3, "real"):
            answer, _ = service("prepare", **item["body"])
            self.assertEqual(answer.get("state"), "admitted", answer)
            attempt = item["body"]["assignment"]["attempt_id"]
            closed, _ = service("record", event=dict(event_id=attempt + "-nd", attempt_id=attempt, kind="not_dispatched"))
            self.assertEqual(closed.get("state"), "recorded", closed)
        replay_export.export(ledger, self.export, now=NOW)
        report = replay_strict.run(self.export)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["worker_choice"], dict(old=0, new=3, failed=0))

    def test_the_reader_opens_only_a_private_export_directory(self):
        loose = self.private(self.tmp / "loose", mode=0o755)
        with self.assertRaises(replay_strict.Refused):
            replay_strict.run(loose)
        with self.assertRaises(replay_strict.Refused):
            replay_strict.run(self.tmp / "missing")
