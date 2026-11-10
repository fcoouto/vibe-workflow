"""The live-session purposes (`isolation`, `drill`, `gateway`), the cap gate every live mode passes before anything
starts, and the per-run canary project and pilot cap. No server, no model call; ledgers live in temporary dirs."""
from pathlib import Path
import subprocess

from tests.qualification import canary, live_common, outputs, safety
from tests.qualification import opencode_live as live
from tests.qualification.doubles import Temp

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="runbook part A")


class PurposeTests(Temp):
    def ledger(self):
        return safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")

    def test_new_purposes_start_closed_and_open_only_with_an_approval(self):
        ledger = self.ledger()
        for purpose, maximum in (("isolation", 8), ("drill", 4), ("gateway", 8)):
            with self.subTest(purpose):
                self.assertEqual(safety.CAPS[purpose], 0)
                self.assertEqual(safety.MAX_CAPS[purpose], maximum)
                self.assertEqual(ledger.summary()[purpose]["cap"], 0)
                with self.assertRaises(safety.BudgetExceeded):
                    ledger.reserve(purpose, "before any cap line", 1)
                with self.assertRaises(safety.Refused):
                    ledger.set_cap(purpose, maximum, "no approval record")
                with self.assertRaises(safety.Refused):
                    ledger.set_cap(purpose, maximum + 1, "above the maximum", approval=APPROVAL)
                self.assertEqual(ledger.set_cap(purpose, maximum, "approved", approval=APPROVAL)["cap"], maximum)
                ref = ledger.reserve(purpose, "after approval", 1)
                ledger.settle(ref, 0)

    def test_existing_purposes_are_unchanged(self):
        summary = self.ledger().summary()
        self.assertEqual(summary["qualification"]["cap"], 14)
        self.assertEqual(summary["pilot"]["cap"], 24)
        self.assertEqual(safety.MAX_CAPS["chatgpt"], 6)

    def test_the_minimal_record_lists_every_purpose(self):
        totals = {purpose: dict(cap=1, spent=0, remaining=1, open_reservations=0) for purpose in safety.CAPS}
        record = outputs.minimal(dict(call_ledger=totals))
        self.assertEqual(set(record["call_ledger"]), set(safety.CAPS))

    def test_settle_dead_and_reset_see_the_new_entry_script(self):
        ledger = self.ledger()
        ledger.set_cap("drill", 4, "approved", approval=APPROVAL)
        ref = ledger.reserve("drill", "dead run", 1)
        alive = "4242 python3 -B tests/qualification/live_modes.py drill --isolated-dir /x\n"
        with self.assertRaises(safety.Refused):
            ledger.settle_dead(ref, alive)
        with self.assertRaises(safety.Refused):
            canary.reset_state(self.private(self.tmp / "canary-state"), alive, home=self.home)


class CapGateTests(Temp):
    def ledger(self):
        return safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")

    def test_nothing_runs_without_an_approved_cap(self):
        ledger = self.ledger()
        for purpose in ("isolation", "drill", "gateway"):
            with self.subTest(purpose), self.assertRaises(safety.Refused) as refused:
                live_common.require_cap(ledger, purpose, 0)
            self.assertIn("--set-cap", str(refused.exception))

    def test_an_approved_cap_with_too_little_remaining_refuses(self):
        ledger = self.ledger()
        ledger.set_cap("gateway", 2, "approved", approval=APPROVAL)
        with self.assertRaises(safety.Refused):
            live_common.require_cap(ledger, "gateway", 5)
        self.assertEqual(live_common.require_cap(ledger, "gateway", 2)["remaining"], 2)

    def test_a_cap_line_without_approval_does_not_open_a_live_purpose(self):
        ledger = self.ledger()
        with self.assertRaises(safety.Refused):
            live_common.require_cap(ledger, "pilot", 0)  # only the three live-session purposes are modes here

    def test_open_reservations_refuse(self):
        ledger = self.ledger()
        ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
        ledger.reserve("isolation", "left open", 1)
        with self.assertRaises(safety.Refused):
            live_common.require_cap(ledger, "isolation", 1)


class CanaryConfigurableTests(Temp):
    def managed_settings(self, projects):
        from runtime import routing_settings
        settings = self.private(self.tmp / ("canary-%d" % len(list(self.tmp.glob("canary-*"))))) / "settings.json"
        routing_settings.update(settings, dict(managed_projects=[str(p) for p in projects]), 0)
        return settings

    def checkout(self, name):
        path = self.private(self.tmp / name)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        return path.resolve()

    def test_defaults_are_unchanged(self):
        self.assertEqual(canary.ALLOWLIST, "/Users/fcouto/dev/git/vibe-workflow")
        self.assertEqual(canary.PILOT_MAX, 20)
        with self.assertRaises(safety.Refused):
            canary.check_project(str(self.tmp))

    def test_an_explicit_project_must_be_on_managed_projects(self):
        project = self.checkout("other")
        unmanaged = self.checkout("unmanaged")
        settings = self.managed_settings([project])
        self.assertEqual(canary.check_project(str(project), allow=str(project), settings=settings), str(project))
        with self.assertRaises(safety.Refused):
            canary.check_project(str(unmanaged), allow=str(unmanaged), settings=settings)
        with self.assertRaises(safety.Refused):  # the argument and the project must agree
            canary.check_project(str(project), allow=str(unmanaged), settings=settings)
        with self.assertRaises(safety.Refused):  # an explicit project needs the canary settings to validate against
            canary.check_project(str(project), allow=str(project), settings=None)
        with self.assertRaises(safety.Refused):  # a git checkout root only
            sub = self.private(project / "sub")
            canary.check_project(str(sub), allow=str(sub), settings=self.managed_settings([sub]))

    def test_the_pilot_cap_is_bounded_by_the_ledger_cap(self):
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        self.assertEqual(canary.pilot_max(None, ledger), canary.PILOT_MAX)
        self.assertEqual(canary.pilot_max(5, ledger), 5)
        with self.assertRaises(safety.Refused):  # 2 + 2 + (21 - 1) = 24: fits pilot 24 but exceeds the code bound
            canary.pilot_max(21, ledger)
        ledger.set_cap("pilot", 24, "unchanged")
        ref = ledger.reserve("pilot", "spent", 20)
        ledger.settle(ref, 20)
        with self.assertRaises(safety.Refused):  # 4 remain; 3 admissions need 2 + 2 + 2 = 6
            canary.pilot_max(3, ledger)
        self.assertEqual(canary.pilot_max(1, ledger), 1)
        with self.assertRaises(safety.Refused):
            canary.pilot_max(0, ledger)

    def test_plan_assignments_accepts_the_run_cap(self):
        self.assertEqual(len(canary.plan_assignments(3, "t", now=1, limit=3)), 3)
        with self.assertRaises(safety.Refused):
            canary.plan_assignments(4, "t", now=1, limit=3)

    def test_the_cli_refuses_a_canary_project_off_managed_projects_before_anything_starts(self):
        q = self.private(self.tmp / "q")
        ledger = self.private(self.tmp / "state") / "calls.jsonl"
        safety.CallLedger.initialize(ledger)
        project = self.checkout("p")
        base = ["--isolated-dir", str(q), "--call-ledger", str(ledger)]
        state = self.tmp / "canary-state"
        code = live.main(base + ["--canary", "--project", str(project), "--canary-allow", str(project),
                                 "--canary-state", str(state)])
        self.assertEqual(code, 2)
        self.assertFalse(state.exists())
        self.assertEqual(list(Path(q).iterdir()), [])
