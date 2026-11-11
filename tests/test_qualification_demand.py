"""`measure-demand` with doubles: readings come only from data the budgeted launches already produce (the launcher's
stored `native.rateLimit` windows, the drill's re-read, the Codex rate-limit reader around A5,
with its app-server double); no extra model call or server. The arithmetic, the owner-only log and the `measure-demand` mode itself."""
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest import mock

from runtime import routing_observations, routing_paths
from tests.qualification import demand, launcher_probes, live_modes, safety
from tests.qualification.doubles import Temp
from tests.qualification.launcher_doubles import FakeEnv

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="live session")
DOUBLE = Path(__file__).resolve().parent / "codex_app_server_double.py"


CLAUDE, CHATGPT = "opencode-claude-subscription", "opencode-chatgpt-subscription"
ACCOUNT = "invented-plan-label"  # a launch-bound, nonsecret account label as ManagedState carries one


def reading(label, used, at, reset=5000, pool="sub", window="5h", path=CLAUDE, run="r1", **binding):
    """A path-tagged reading (F4.3) bound to ACCOUNT unless `account` is given (None: no account source);
    `path=None` is a historical pool-only reading."""
    if path is not None:
        binding.setdefault("account", ACCOUNT)
    value = dict(label=label, pool=pool, at=at, windows={window: dict(used=used, reset_at=reset, observed_at=at)},
                 unit=demand.UNIT, **{key: item for key, item in binding.items() if item is not None})
    if path is not None:
        value.update(path=path, run=run)
    return value


def both(label, five, seven, at, path=CLAUDE, run="r1", reset=5000, **binding):
    """One reading of a path's two windows, `5h` and `7d`."""
    value = reading(label, five, at, reset=reset, path=path, run=run, **binding)
    value["windows"]["7d"] = dict(used=seven, reset_at=reset + 100000, observed_at=at)
    return value


class ArithmeticTests(Temp):
    def test_deltas_pair_consecutive_readings_of_one_pool(self):
        readings = [reading("A6", 250, 1000), reading("A5:before", 10, 1001, pool="chatgpt", path=CHATGPT),
                    reading("A7", 262, 1060), reading("A5:after", 14, 1070, pool="chatgpt", path=CHATGPT),
                    reading("drill1", 270, 1500)]
        measured = demand.measure(readings)
        self.assertEqual([m["windows"]["5h"] for m in measured],
                         [dict(reason="no_before"), dict(reason="no_before"), dict(delta=12), dict(delta=4), dict(delta=8)])
        suggestion = demand.suggest(measured)
        self.assertEqual(suggestion[CLAUDE]["5h"], dict(samples=2, max_delta=12, demand=18, margin_pct=50, unit=demand.UNIT))
        self.assertEqual(suggestion[CHATGPT]["5h"]["demand"], 6)

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
        self.assertEqual(demand.suggest(measured)[CLAUDE]["5h"], dict(samples=1, max_delta=1, demand=2, margin_pct=50,
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
        self.assertEqual(report["suggestion"][CLAUDE]["5h"]["demand"], 18)
        # F4.3: each reading carries the path of the route the harness launched, the run and the launch binding
        self.assertEqual({(r["path"], r["run"], r["pool"], r["account"]) for r in self.log.readings()},
                         {(CLAUDE, "r", "sub", "anthropic:isolation")})
        self.assertEqual([(r["model"], r.get("effort")) for r in self.log.readings()], [("haiku", "low"), ("opus", None)])
        self.assertNotIn("effort", self.log.readings()[1])  # an omitted effort is left out, never a literal `none`

    def test_the_launcher_double_reads_an_omitted_variant_as_the_shim_does(self):
        """Fixture parity with the shim's `spawnModel`: no `#` is a null variant (routing reads it as the identity's
        `none`), a bare `#` is malformed; an explicit variant is kept exactly."""
        from tests.qualification.launcher_doubles import attach_route
        self.assertEqual(attach_route("claude-agent/opus"), dict(provider="claude-agent", model="opus", variant=None))
        self.assertEqual(attach_route("claude-agent/haiku#low")["variant"], "low")
        self.assertIsNone(attach_route("claude-agent/opus#"))

    def test_A5_reads_the_codex_window_before_and_after_only_when_the_reader_answers(self):
        models = [dict(providerID="openai", id="gpt-6-luna", variants=["none"])]
        absent = FakeEnv(self.tmp / "w1", models=list(models))
        probes = launcher_probes.LauncherProbes(self.private(self.tmp / "q1"), self.ledger, "r", absent,
                                                self.private(self.tmp / "real"), "u", safety.Redactor(),
                                                a5_model="openai/gpt-6-luna#none", demand_log=self.log)
        self.assertEqual(probes.run(["A1", "A2", "A3", "A5"])["A5"]["demand"], dict(measured=False, reason="codex_unavailable"))
        self.assertEqual(absent.codex_reads, 1)  # a failed reading before A5 spawns no second read after it
        self.assertEqual(absent.hosts[1].prompts, 1)
        windows = [[dict(id="5h", used=100, reset_at=9000, observed_at=1)], [dict(id="5h", used=130, reset_at=9000, observed_at=2)]]
        present = FakeEnv(self.tmp / "w2", models=list(models), codex_windows=windows)
        probes = launcher_probes.LauncherProbes(self.private(self.tmp / "q2"), self.ledger, "r", present,
                                                self.private(self.tmp / "real"), "u", safety.Redactor(),
                                                a5_model="openai/gpt-6-luna#none", demand_log=self.log)
        self.assertEqual(probes.run(["A1", "A2", "A3", "A5"])["A5"]["demand"], dict(measured=True))
        self.assertEqual(present.codex_reads, 2)
        self.assertEqual(present.hosts[1].prompts, 1)
        # A5 has no launch-bound account label (its sign-in reports a status only), so its pair is account-unknown:
        # kept as a diagnostic row, never a sample or a qualification request.
        self.assertEqual({(r["path"], r["pool"], r["model"], r.get("account")) for r in self.log.readings()},
                         {(CHATGPT, "chatgpt", "gpt-6-luna", None)})
        report = demand.report(self.log)
        self.assertEqual(report["per_launch"][-1]["windows"]["5h"], dict(reason="account_unknown"))
        self.assertNotIn(CHATGPT, report["suggestion"])
        self.assertEqual(report["paths"][CHATGPT]["reason"], "account_unknown")

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
        self.assertEqual((self.log.readings()[0]["path"], self.log.readings()[0]["run"]), (CLAUDE, "d"))


class LiveCodexReadingTests(Temp):
    """`LiveEnv.codex_window` through runtime/codex_rate_limits.py with the reader's own app-server double
    (tests/codex_app_server_double.py): an empty PATH, so no real `codex` is reachable, and a fake auth file."""

    def setUp(self):
        super().setUp()
        self.assertNotEqual(self.tmp, Path.home().resolve())
        self.base = self.private(self.tmp / "q")
        fake_home = self.private(self.tmp / "codex-home")
        self.private(fake_home / ".codex")
        (fake_home / ".codex" / "auth.json").write_text('{"fake": true}')  # never a real credential
        empty_bin = self.private(self.tmp / "bin")
        self.environ = dict(PATH=str(empty_bin), HOME=str(fake_home), LANG="C")
        self.assertIsNone(shutil.which("codex", path=self.environ["PATH"]))
        self.double_log = self.tmp / "double.log"
        # Rules declaring no ChatGPT window (the A5 readings predate the shipped ChatGPT paths' `7d` declaration).
        patch = mock.patch.object(routing_paths, "load_rules", return_value=self.rules([]))
        patch.start()
        self.addCleanup(patch.stop)

    def env(self, scenario="ok", percents=(10, 13)):
        env = live_modes.LiveEnv(self.base, safety.Redactor())
        env.codex_environ = self.environ
        reset = int(time.time()) + 3600
        values = [dict(rateLimits=dict(limitId="codex", primary=dict(usedPercent=p, windowDurationMins=300, resetsAt=reset)))
                  for p in percents]
        commands = [[sys.executable, "-I", "-B", str(DOUBLE), str(self.double_log), scenario, json.dumps(v)] for v in values]
        env.codex_command = lambda: commands.pop(0)
        return env

    def spawns(self):
        lines = self.double_log.read_text().splitlines() if self.double_log.exists() else []
        return [json.loads(line) for line in lines]

    def test_one_read_goes_through_the_reader_and_stores_nothing(self):
        env = self.env()
        found = env.codex_window()
        self.assertEqual(sorted(found), ["declared", "windows"])
        self.assertIs(found["declared"], False)  # these rules declare no chatgpt window
        self.assertEqual([(w["id"], w["used"]) for w in found["windows"]], [("5h", 100)])
        self.assertEqual(env.counts["codex_reads"], 1)
        received = [e["received"].get("method") for e in self.spawns() if "received" in e]
        self.assertEqual(received, ["initialize", "initialized", "account/rateLimits/read"])  # the reader's only methods
        self.assertEqual(list(self.base.iterdir()), [])  # no routing settings or window store written
        self.assertFalse(Path(routing_observations.window_path(Path.home() / ".local/state/vibe-workflow/routing/s.json")).exists())

    def test_A5_logs_both_codex_readings_as_chatgpt_demand(self):
        live = self.env()
        env = FakeEnv(self.tmp / "w", models=[dict(providerID="openai", id="gpt-6-luna", variants=["none"])])
        env.codex_window = live.codex_window
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
        log = demand.DemandLog(demand.log_path(ledger.path))
        probes = launcher_probes.LauncherProbes(self.private(self.tmp / "q2"), ledger, "r", env,
                                                self.private(self.tmp / "real"), "u", safety.Redactor(),
                                                a5_model="openai/gpt-6-luna#none", demand_log=log)
        result = probes.run(["A1", "A2", "A3", "A5"])["A5"]
        self.assertIs(result["passed"], True, json.dumps(result)[:1500])
        self.assertEqual(result["demand"], dict(measured=True, declared=False))
        self.assertEqual(live.counts["codex_reads"], 2)
        self.assertEqual([r.get("declared") for r in log.readings()], [False, False])
        report = demand.report(log)
        self.assertEqual([m.get("declared") for m in report["per_launch"]], [False, False])
        self.assertEqual(sum(1 for e in self.spawns() if e.get("spawned")), 2)  # one before and one after, nothing else
        self.assertEqual([r["label"] for r in log.readings()], ["A5:before", "A5:after"])
        # The readings are logged, but A5 has no account source: no sample and no request (account-scoped demand).
        self.assertEqual(report["per_launch"][1]["windows"]["5h"], dict(reason="account_unknown"))
        self.assertEqual((report["suggestion"], report["requests"]), ({}, []))

    @staticmethod
    def rules(windows):
        """The OpenCode ChatGPT path (so A5's readings are tagged with it) declaring only `windows` for the pool."""
        from runtime import codex_rate_limits as reader
        return dict(version=2, paths=[dict(path=CHATGPT, harness="opencode provider openai", provider="openai",
                                           billing_class="included_subscription", pool=reader.POOL,
                                           windows=list(windows))])

    def test_the_path_rules_declared_windows_are_used_when_they_declare_any(self):
        for declared, expected in ((["5h"], "windows"), (["7d"], "codex_window_undeclared")):
            with self.subTest(declared=declared):
                env = self.env()
                with mock.patch.object(routing_paths, "load_rules", return_value=self.rules(declared)):
                    found = env.codex_window()
                self.assertIn(expected, (found.get("reason"), "windows" if "windows" in found else None))
                if "windows" in found:
                    self.assertIs(found["declared"], True)
        with mock.patch.object(routing_paths, "load_rules", side_effect=ValueError("route_manifest_invalid")):
            self.assertEqual(self.env().codex_window(), dict(reason="route_manifest_invalid"))

    def test_declared_readings_carry_no_mark(self):
        log = demand.DemandLog(self.tmp / demand.FILE)
        log.append(reading("A5:before", 100, 1000, pool="chatgpt", path=CHATGPT))
        log.append(reading("A5:after", 130, 1010, pool="chatgpt", path=CHATGPT))
        report = demand.report(log)
        self.assertNotIn("declared", report["per_launch"][1])
        self.assertNotIn("declared", report["suggestion"][CHATGPT]["5h"])

    def test_a_failed_read_records_its_fixed_reason_and_never_fails_A5(self):
        for scenario, reason in (("exit_at_once", "codex_exited"), (None, "codex_unavailable")):
            with self.subTest(reason):
                live = self.env(scenario or "ok")
                if scenario is None:
                    live.codex_command = lambda: None  # the default command search: nothing on the empty PATH
                env = FakeEnv(self.tmp / ("w-" + reason), models=[dict(providerID="openai", id="gpt-6-luna", variants=["none"])])
                env.codex_window = live.codex_window
                ledger = safety.CallLedger.initialize(self.private(self.tmp / ("state-" + reason)) / "calls.jsonl")
                ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
                log = demand.DemandLog(demand.log_path(ledger.path))
                probes = launcher_probes.LauncherProbes(self.private(self.tmp / ("q-" + reason)), ledger, "r", env,
                                                        self.private(self.tmp / "real"), "u", safety.Redactor(),
                                                        a5_model="openai/gpt-6-luna#none", demand_log=log)
                result = probes.run(["A1", "A2", "A3", "A5"])["A5"]
                self.assertIs(result["passed"], True, json.dumps(result)[:1500])
                self.assertEqual(result["demand"], dict(measured=False, reason=reason))
                self.assertEqual(live.counts["codex_reads"], 1)  # no read after a failed one
                self.assertEqual(log.readings(), [])

    def test_the_plan_counts_the_two_codex_reads_only_when_A5_runs(self):
        without, with_a5 = launcher_probes.plan(["A1", "A2", "A3"]), launcher_probes.plan(["A1", "A2", "A3", "A5"])
        self.assertEqual((without["codex_reads"], with_a5["codex_reads"]), (0, 2))
        self.assertEqual(with_a5["processes"], 1 + 2 * with_a5["servers"] + 2)

    def test_no_real_codex_is_reachable_by_default(self):
        env = live_modes.LiveEnv(self.base, safety.Redactor())
        env.codex_environ = self.environ  # the default command: the empty PATH has no codex
        self.assertEqual(env.codex_window(), dict(reason="codex_unavailable"))
        self.assertFalse(self.double_log.exists())


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
        self.assertEqual(found["demand"]["suggestion"][CLAUDE]["5h"]["demand"], 18)
        self.assertEqual(found["demand"]["requests"], [])  # the Claude path's `7d` window has no sample
        self.assertEqual(found["plan"]["expected_calls"], 0)
        self.assertEqual(live_modes.main(["measure-demand", "--call-ledger", str(ledger.path), "--out", str(out),
                                          "--isolated-dir", str(self.tmp)]), 2)


class PathQualificationRequestTests(Temp):
    """F4.3: `measure-demand` measures per path and proposes one `qualify_path` request per subscription path whose
    every window has a usable sample; it never relabels pool-only readings or proposes a gateway qualification."""

    def report(self, *readings):
        folder = self.tmp / ("log-%d" % len(list(self.tmp.iterdir())))
        folder.mkdir()
        log = demand.DemandLog(folder / demand.FILE)
        for item in readings:
            log.append(item)
        return demand.report(log)

    def test_measure_demand_maps_readings_to_path_ids(self):
        readings = [both("A6", 250, 40, 1000), reading("A5:before", 10, 1001, pool="chatgpt", path=CHATGPT, window="7d"),
                    both("A7", 262, 42, 1060), reading("old", 300, 1070, path=None),  # historical: pool only
                    reading("A5:after", 14, 1070, pool="chatgpt", path=CHATGPT, window="7d")]
        measured = demand.measure(readings)
        self.assertEqual([entry["path"] for entry in measured], [CLAUDE, CHATGPT, CLAUDE, None, CHATGPT])
        self.assertEqual(measured[2]["windows"], {"5h": dict(delta=12), "7d": dict(delta=2)})
        self.assertEqual(measured[3]["windows"], {"5h": dict(reason="path_untagged")})  # never a path sample
        self.assertEqual(measured[4]["windows"], {"7d": dict(delta=4)})  # paired on its own path only
        self.assertEqual(set(demand.suggest(measured)), {CLAUDE, CHATGPT})
        moved = demand.measure([both("a", 250, 40, 1000, account="plan-1"), both("b", 262, 42, 1060, account="plan-2")])
        self.assertEqual(moved[1]["windows"]["5h"], dict(reason="account_changed"))
        crossed = demand.measure([both("a", 250, 40, 1000), both("b", 262, 42, 1060, reset=7000)])
        self.assertEqual(crossed[1]["windows"]["5h"], dict(reason="reset_between"))

    def test_measure_demand_prints_one_qualify_request_per_path(self):
        found = self.report(both("A6", 250, 40, 1000), both("A7", 262, 42, 1060),
                            reading("A5:before", 10, 1001, pool="chatgpt", path=CHATGPT, window="7d", run="r2"),
                            reading("A5:after", 14, 1070, pool="chatgpt", path=CHATGPT, window="7d", run="r2"),
                            reading("w1", 10, 1000, pool="openrouter", path="openrouter-workers", window="credit"),
                            reading("w2", 900, 1010, pool="openrouter", path="openrouter-workers", window="credit"))
        self.assertEqual(found["requests"], [
            dict(v=1, op="qualify_path", path=CHATGPT, demand={"7d": 6}, evidence="r2", account=ACCOUNT),
            dict(v=1, op="qualify_path", path=CLAUDE, demand={"5h": 18, "7d": 3}, evidence="r1", account=ACCOUNT)])
        self.assertEqual(found["paths"]["openrouter-workers"], dict(request=False, reason="gateway_not_qualified"))
        self.assertEqual(found["paths"][CLAUDE], dict(request=True, account=ACCOUNT, floor_windows=[],
                                                      missing_windows=[]))
        for request in found["requests"]:  # each is a valid `qualify_path` body for the shipped rules
            self.assertEqual(set(request), {"v", "op", "path", "demand", "evidence", "account"})

    def test_a_window_below_resolution_gets_the_floor_demand_and_says_so(self):
        found = self.report(both("A6", 250, 40, 1000), both("A7", 262, 40, 1060))
        self.assertEqual(found["requests"], [dict(v=1, op="qualify_path", path=CLAUDE, demand={"5h": 18, "7d": 10},
                                                  evidence="r1", account=ACCOUNT)])
        self.assertEqual(found["paths"][CLAUDE]["floor_windows"],
                         [dict(window="7d", reason="below_resolution", demand=demand.BELOW_RESOLUTION_DEMAND)])
        self.assertEqual(demand.BELOW_RESOLUTION_DEMAND, 10)

    def test_a_path_with_an_unmeasured_window_gets_no_request(self):
        found = self.report(reading("A6", 250, 1000), reading("A7", 262, 1060),  # `5h` only; the path also has `7d`
                            reading("old-a", 100, 1000, path=None), reading("old-b", 140, 1010, path=None))
        self.assertEqual(found["requests"], [])
        self.assertEqual(found["paths"][CLAUDE], dict(request=False, reason="window_unmeasured",
                                                      missing_windows=["7d"], floor_windows=[]))
        self.assertNotIn(None, found["paths"])  # pool-only readings propose nothing
        crossing = self.report(both("A6", 250, 40, 1000), both("A7", 262, 42, 1060, reset=7000))
        self.assertEqual(crossing["requests"], [])  # a reset-crossing pair is no sample of either window
        self.assertEqual(crossing["paths"][CLAUDE]["missing_windows"], ["5h", "7d"])


class AccountScopeTests(Temp):
    """F4.3 correction: every demanded window of a request comes from one known, launch-bound account, and the request
    carries that account; unknown or mixed account scope fails closed instead of being stitched."""

    def report(self, *readings):
        log = demand.DemandLog(self.tmp / demand.FILE)
        for item in readings:
            log.append(item)
        return demand.report(log)

    def test_complementary_windows_of_two_accounts_make_no_request(self):
        found = self.report(reading("a1", 10, 1000, window="5h", account="plan-a", run="run-a"),
                            reading("a2", 20, 1010, window="5h", account="plan-a", run="run-a"),
                            reading("b1", 30, 1020, window="7d", account="plan-b", run="run-b"),
                            reading("b2", 40, 1030, window="7d", account="plan-b", run="run-b"))
        self.assertEqual([entry["windows"] for entry in found["per_launch"]][1::2],  # each account's own pair stays
                         [{"5h": dict(delta=10)}, {"7d": dict(delta=10)}])
        self.assertEqual(found["requests"], [])
        self.assertEqual(found["paths"][CLAUDE]["request"], False)
        self.assertEqual(found["paths"][CLAUDE]["reason"], "account_ambiguous")
        self.assertEqual(found["paths"][CLAUDE]["accounts"], ["plan-a", "plan-b"])

    def test_an_unknown_account_never_qualifies(self):
        unknown = [both("u1", 250, 40, 1000, account=None), both("u2", 262, 42, 1060, account=None)]
        measured = demand.measure(unknown)
        self.assertEqual(measured[1]["windows"], {"5h": dict(reason="account_unknown"),
                                                  "7d": dict(reason="account_unknown")})  # never a known pair
        found = self.report(*unknown)
        self.assertEqual(found["requests"], [])
        self.assertEqual(found["paths"][CLAUDE], dict(request=False, reason="account_unknown",
                                                      missing_windows=["5h", "7d"], floor_windows=[]))
        mixed = demand.measure([both("k", 250, 40, 1000), both("u", 262, 42, 1060, account=None)])
        self.assertEqual(mixed[1]["windows"]["5h"], dict(reason="account_unknown"))  # known then unknown: no pair

    def test_one_account_with_every_window_binds_its_request_to_that_account(self):
        found = self.report(both("a1", 250, 40, 1000, account="plan-a", run="run-a"),
                            both("a2", 262, 42, 1060, account="plan-a", run="run-a"),
                            both("x1", 100, 10, 1100, account=None, run="run-x"),  # diagnostic rows only
                            both("x2", 900, 90, 1110, account=None, run="run-x"))
        self.assertEqual(found["requests"], [dict(v=1, op="qualify_path", path=CLAUDE, demand={"5h": 18, "7d": 3},
                                                  evidence="run-a", account="plan-a")])
        self.assertEqual(found["paths"][CLAUDE]["account"], "plan-a")
        from runtime import routing_paths
        request = found["requests"][0]
        self.assertTrue(routing_paths.qualify(self.tmp / "routing" / "settings.json", request["path"],
                                              demand=request["demand"], evidence=request["evidence"],
                                              account=request["account"], now=1)["account"] == "plan-a")
