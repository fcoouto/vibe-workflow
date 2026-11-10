"""`measure-demand` with doubles: readings come only from data the budgeted launches already produce (the launcher's
stored `native.rateLimit` windows, the drill's re-read, lane R7's Codex reader around A5); no extra model call,
process or server. The arithmetic, the owner-only log and the `measure-demand` mode itself."""
import json
import os

from tests.qualification import demand, launcher_probes, live_modes, safety
from tests.qualification.doubles import Temp
from tests.qualification.launcher_doubles import FakeEnv

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="live session")


def reading(label, used, at, reset=5000, pool="sub", window="5h"):
    return dict(label=label, pool=pool, at=at, windows={window: dict(used=used, reset_at=reset, observed_at=at)},
                unit=demand.UNIT)


class ArithmeticTests(Temp):
    def test_deltas_pair_consecutive_readings_of_one_pool(self):
        readings = [reading("A6", 250, 1000), reading("A5:before", 10, 1001, pool="chatgpt"),
                    reading("A7", 262, 1060), reading("A5:after", 14, 1070, pool="chatgpt"), reading("drill1", 270, 1500)]
        measured = demand.measure(readings)
        self.assertEqual([m["windows"]["5h"] for m in measured],
                         [dict(reason="no_before"), dict(reason="no_before"), dict(delta=12), dict(delta=4), dict(delta=8)])
        suggestion = demand.suggest(measured)
        self.assertEqual(suggestion["sub"]["5h"], dict(samples=2, max_delta=12, demand=18, margin_pct=50, unit=demand.UNIT))
        self.assertEqual(suggestion["chatgpt"]["5h"]["demand"], 6)

    def test_unusable_pairs_are_reported_never_guessed(self):
        cases = [(reading("b", 300, 2000, reset=9999), "reset_between"), (reading("b", 300, 1000 + demand.STALE_S + 1),
                                                                          "stale_before"),
                 (reading("b", 100, 1100), "decreased")]
        for later, reason in cases:
            with self.subTest(reason):
                measured = demand.measure([reading("a", 200, 1000), later])
                self.assertEqual(measured[1]["windows"]["5h"]["reason"], reason)
                self.assertEqual(demand.suggest(measured), {})

    def test_a_delta_under_one_unit_is_below_resolution_not_a_zero_sample(self):
        measured = demand.measure([reading("a", 200, 1000), reading("b", 200, 1010), reading("c", 201, 1020)])
        self.assertEqual(measured[1]["windows"]["5h"], dict(reason="below_resolution"))
        self.assertEqual(demand.suggest(measured)["sub"]["5h"], dict(samples=1, max_delta=1, demand=2, margin_pct=50,
                                                                     unit=demand.UNIT))
        self.assertEqual(demand.suggest(demand.measure([reading("a", 200, 1000), reading("b", 200, 1010)])), {})

    def test_the_log_is_owner_only_and_skips_damaged_lines(self):
        log = demand.DemandLog(self.tmp / demand.FILE)
        self.assertTrue(log.append(reading("a", 1, 1000)))
        self.assertFalse(log.append(None))
        with open(log.path, "a") as handle:
            handle.write("{not json\n")
        self.assertEqual([r["label"] for r in log.readings()], ["a"])
        self.assertEqual(oct(log.path.stat().st_mode & 0o777), "0o600")
        os.chmod(log.path, 0o644)
        with self.assertRaises(safety.Refused):
            log.append(reading("b", 2, 1001))


class LaunchReadingTests(Temp):
    def setUp(self):
        super().setUp()
        self.ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        self.ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
        self.log = demand.DemandLog(demand.log_path(self.ledger.path))

    def test_A6_and_A7_share_one_server_and_their_own_readings_measure_A7(self):
        env = FakeEnv(self.tmp / "work", utilizations=[0.25, 0.262],
                      models=[dict(providerID="claude-agent", id="haiku", variants=[dict(id="low")]),
                              dict(providerID="claude-agent", id="opus", variants=[])])
        probes = launcher_probes.LauncherProbes(self.private(self.tmp / "q"), self.ledger, "r", env,
                                                self.private(self.tmp / "real"), "u", safety.Redactor(),
                                                no_variant_model="claude-agent/opus", demand_log=self.log)
        results = probes.run(["A4", "A6", "A7"])
        self.assertTrue(all(results[p]["passed"] for p in ("A4", "A6", "A7")), json.dumps(results)[:2000])
        self.assertEqual(len(env.hosts), 1)  # A4, A6 and A7 on one server start
        self.assertEqual(env.hosts[0].prompts, 2)  # the two budgeted launches; nothing added to measure
        report = demand.report(self.log)
        self.assertEqual([m["windows"]["5h"] for m in report["per_launch"]], [dict(reason="no_before"), dict(delta=12)])
        self.assertEqual(report["suggestion"]["sub"]["5h"]["demand"], 18)

    def test_A5_reads_the_codex_window_before_and_after_only_when_the_reader_exists(self):
        models = [dict(providerID="openai", id="gpt-6-luna", variants=["none"])]
        absent = FakeEnv(self.tmp / "w1", models=list(models))
        probes = launcher_probes.LauncherProbes(self.private(self.tmp / "q1"), self.ledger, "r", absent,
                                                self.private(self.tmp / "real"), "u", safety.Redactor(),
                                                a5_model="openai/gpt-6-luna#none", demand_log=self.log)
        self.assertEqual(probes.run(["A1", "A2", "A3", "A5"])["A5"]["demand"], dict(measured=False, reason="codex_reader_unavailable"))
        self.assertEqual(absent.hosts[1].prompts, 1)
        windows = [[dict(id="5h", used=100, reset_at=9000, observed_at=1)], [dict(id="5h", used=130, reset_at=9000, observed_at=2)]]
        present = FakeEnv(self.tmp / "w2", models=list(models), codex_windows=windows)
        probes = launcher_probes.LauncherProbes(self.private(self.tmp / "q2"), self.ledger, "r", present,
                                                self.private(self.tmp / "real"), "u", safety.Redactor(),
                                                a5_model="openai/gpt-6-luna#none", demand_log=self.log)
        self.assertEqual(probes.run(["A1", "A2", "A3", "A5"])["A5"]["demand"], dict(measured=True))
        self.assertEqual(present.codex_reads, 2)
        self.assertEqual(present.hosts[1].prompts, 1)
        self.assertEqual(demand.suggest(demand.measure(self.log.readings()))["chatgpt"]["5h"]["max_delta"], 30)

    def test_the_drill_logs_its_reread_reading(self):
        from tests.qualification import drill
        from tests.test_qualification_drill import Env
        self.ledger.set_cap("drill", 4, "approved", approval=APPROVAL)
        env = Env(self.tmp, utilizations=[0.3])
        result = drill.run_drill(drill.Drill(self.private(self.tmp / "q"), self.ledger, "d", env, pause_timeout=30,
                                             demand_log=self.log))
        self.assertTrue(result["passed"], json.dumps(result)[:2000])
        self.assertTrue(result["facts"]["demand_reading"])
        self.assertEqual([r["label"] for r in self.log.readings()], ["drill1"])
        self.assertEqual(self.log.readings()[0]["windows"]["5h"]["used"], 300)


class MeasureDemandModeTests(Temp):
    def test_measure_demand_starts_nothing_and_writes_the_suggestion(self):
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        log = demand.DemandLog(demand.log_path(ledger.path))
        for item in (reading("A6", 250, 1000), reading("A7", 262, 1060)):
            log.append(item)
        out = self.private(self.tmp / "results") / "demand.json"

        def refuse(**_kwargs):
            raise AssertionError("measure-demand must not check or start anything")
        code = live_modes.main(["measure-demand", "--call-ledger", str(ledger.path), "--out", str(out)],
                               env_factory=refuse, home_check=refuse, version_check=refuse)
        self.assertEqual(code, 0)
        found = json.loads(out.read_text())
        self.assertEqual(found["demand"]["suggestion"]["sub"]["5h"]["demand"], 18)
        self.assertEqual(found["plan"]["expected_calls"], 0)
        self.assertEqual(live_modes.main(["measure-demand", "--call-ledger", str(ledger.path), "--out", str(out),
                                          "--isolated-dir", str(self.tmp)]), 2)
