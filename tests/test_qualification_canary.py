"""The bounded pilot canary without a server: preconditions, the canary state and its reset, the pilot driver's
abort and recovery paths, abort recording, `CanaryEnv`, run sequencing, and end-to-end admissions through a
live-shaped OpenCode double with the real scripts/routing.py on temporary stores."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from runtime import routing, routing_settings
from tests.qualification import canary, safety
from tests.qualification import opencode_live as live
from tests.qualification.doubles import Fake, FakeOpenCode, Temp, types_namespace


class CanaryPreconditionTests(Temp):
    def test_the_project_allowlist_is_exact(self):
        self.assertEqual(canary.ALLOWLIST, "/Users/fcouto/dev/git/vibe-workflow")
        for other in (str(self.tmp), "/Users/fcouto/dev/git/vibe-workflow/runtime", "/Users/fcouto/dev/git/vibe-workflow.worktrees/qual",
                      "", None):
            with self.assertRaises(safety.Refused, msg=other):
                canary.check_project(other)

    def refusals(self, fake, probes_ok=True):
        return canary.preconditions(fake.status(), fake.health(), dict(chain=[] if probes_ok else ["guard_probe_not_blocked"]))

    def test_a_clean_subscription_only_state_passes(self):
        self.assertEqual(self.refusals(Fake()), [])

    def test_each_refusal(self):
        cases = {
            "smart_routing_off": dict(settings=dict(smart_routing=False, api_usage_enabled=False, revision=1)),
            "api_usage_enabled": dict(settings=dict(smart_routing=True, api_usage_enabled=True, revision=1)),
            "organization_enabled": dict(ledger_change=dict(organizations=[dict(id="org", enabled=True)])),
            "authority_required_off": dict(ledger_change=dict(authority_required=False)),
            "unstamped_open_holds": dict(ledger_change=dict(unstamped_open_holds=1)),
            "cost_unknown": dict(ledger_change=dict(cost_unknown=dict(ids=["a"], more=0))),
            "uncertain_holds": dict(ledger_change=dict(holds=dict(uncertain=1))),
            "uncertain_dollar": dict(ledger_change=dict(uncertain_dollar=dict(ids=["a"], more=0))),
            "blocked_organizations": dict(ledger_change=dict(blocked_organizations=[dict(id="o")])),
            "overrun_backlog": dict(ledger_change=dict(overrun_backlog=True)),
            "concurrency_caps_raised": dict(caps=dict(total=5, per_account=2)),
            "open_attempts": dict(ledger_change=dict(open_attempts=1)),
        }
        for reason, change in cases.items():
            with self.subTest(reason):
                fake = Fake(**{k: v for k, v in change.items() if k != "ledger_change"})
                fake.ledger.update(change.get("ledger_change", {}))
                self.assertIn(reason, self.refusals(fake))
        self.assertIn("guard_probe_failed", self.refusals(Fake(), probes_ok=False))
        unhealthy = canary.preconditions(Fake().status(), dict(state="not_ready"), dict(chain=[]))
        self.assertIn("routing_unhealthy", unhealthy)
        self.assertIn("status_unavailable", canary.preconditions(dict(state="blocked"), dict(state="ok"), dict(chain=[])))

    def test_the_plan_covers_three_roles_and_two_bands_with_valid_jobs(self):
        plan = canary.plan_assignments(20, "pilot-x", now=1_800_000_000)
        self.assertEqual(len(plan), 20)
        self.assertEqual({p["label"]["role"] for p in plan}, {"implementer", "reviewer", "researcher"})
        self.assertEqual({p["label"]["band"] for p in plan}, {"low", "medium"})
        for item in plan:
            body = item["body"]
            self.assertEqual(body["assignment"]["choice"], dict(provider="claude-agent", model="haiku", effort="low",
                                                                harness="claude-agent-sdk via opencode", funding="subscription"))
            profile = routing.ensure_profile(body["job"])
            self.assertEqual(routing_settings.assignment_role(profile["role"], body["assignment"].get("role")), item["label"]["role"])
        self.assertEqual(len({p["body"]["assignment"]["attempt_id"] for p in plan}), 20)
        for n in (0, 21, -1, 2.0):
            with self.assertRaises(safety.Refused):
                canary.plan_assignments(n, "pilot-x", now=1)


class PilotDriverTests(Temp):
    def driver(self, fake, n=3, **kwargs):
        state = self.private(self.tmp / "state")
        ledger = safety.CallLedger.initialize(state / ("calls-%d.jsonl" % len(list(state.iterdir()))))
        return canary.PilotDriver(fake, ledger, n=n, run="pilot-test", **kwargs), ledger

    def test_a_clean_run_completes_every_admission(self):
        fake = Fake(transmit=dict(primary=1, title=None, other=0))
        driver, ledger = self.driver(fake)
        result = driver.run()
        self.assertIsNone(result["abort"])
        self.assertEqual(result["completed"], 3)
        self.assertNotIn("kill_switch", fake.calls)
        self.assertEqual(fake.calls.count("record:complete"), 3)
        self.assertEqual(ledger.summary()["pilot"]["spent"], 4)  # 3 primaries + the first admission's title
        self.assertEqual(ledger.summary()["pilot"]["open_reservations"], 0)
        self.assertEqual(result["settlements"], {"window": 3})

    def assert_aborts(self, fake, reason, **kwargs):
        driver, ledger = self.driver(fake, **kwargs)
        result = driver.run()
        self.assertEqual(result["abort"]["reason"], reason)
        self.assertEqual(fake.calls.count("kill_switch"), 1)
        self.assertFalse(fake.settings["smart_routing"])
        return result, ledger

    def test_cap_breach_aborts_before_sending(self):
        fake = Fake()
        driver, ledger = self.driver(fake)
        ledger.reserve("pilot", "fill", 24)
        result = driver.run()
        self.assertEqual(result["abort"]["reason"], "cap_breach")
        self.assertNotIn("launch", fake.calls)
        self.assertIn("kill_switch", fake.calls)

    def test_more_requests_than_reserved_abort(self):
        self.assert_aborts(Fake(transmit=dict(primary=2, title=0, other=0)), "cap_breach")

    def test_non_haiku_request_aborts(self):
        self.assert_aborts(Fake(off_route=1), "non_haiku_request")

    def test_unresolved_launch_records_uncertain_and_aborts(self):
        fake = Fake(fail=dict(launch="timeout"))
        result, ledger = self.assert_aborts(fake, "unresolved_launch")
        self.assertIn("record:uncertain", fake.calls)
        self.assertEqual(ledger.summary()["pilot"]["open_reservations"], 1)  # exposure stays counted

    def test_funding_violation_aborts(self):
        self.assert_aborts(Fake(settlement=dict(kind="usd_micros", charged=10)), "funding_violation")

    def test_status_anomaly_aborts(self):
        fake = Fake()
        fake.ledger["cost_unknown"] = dict(ids=["x"], more=0)
        self.assert_aborts(fake, "status_anomaly")

    def test_concurrency_above_one_open_attempt_aborts(self):
        fake = Fake()
        fake.ledger["open_attempts"] = 1
        self.assert_aborts(fake, "concurrency")

    def test_recovery_errors_abort(self):
        fake = Fake(fail=dict(attach=False))
        result, ledger = self.assert_aborts(fake, "recovery_error")
        # Refused before sending: nothing is recorded uncertain; the dispatched hold is left and reported.
        self.assertNotIn("record:uncertain", fake.calls)
        self.assertEqual(result["abort"]["recovery"], "left_dispatched")
        self.assertEqual(ledger.summary()["pilot"], dict(cap=24, spent=0, remaining=24, open_reservations=0))
        fake = Fake(fail=dict(account=True))
        result, _ = self.assert_aborts(fake, "recovery_error")
        self.assertEqual(result["abort"]["recovery"], "uncertain")
        fake = Fake(fail=dict(prepare="pending"))
        result, _ = self.assert_aborts(fake, "admission_refused")
        self.assertEqual(result["abort"]["recovery"], "not_dispatched")

    def test_max_duration_aborts(self):
        clock = iter([0.0, 0.0, 24 * 3600 + 1.0] + [24 * 3600 + 2.0] * 50)
        self.assert_aborts(Fake(), "max_duration", clock=lambda: next(clock))

    def test_kill_switch_failure_is_reported(self):
        fake = Fake(off_route=1)
        with self.assertRaises(TypeError):
            fake.kill_switch = lambda: None  # doubles may not replace methods (subclass instead)

        class Refusing(Fake):
            def kill_switch(self):
                self.calls.append("kill_switch")
                return dict(state="blocked", reason="x")
        fake = Refusing(off_route=1)
        driver, _ = self.driver(fake)
        result = driver.run()
        self.assertEqual(result["abort"]["kill_switch"], dict(state="blocked", reason="x"))
        self.assertFalse(result["abort"]["kill_switch_ok"])

    def test_every_exception_aborts_with_the_right_recovery(self):
        cases = [("prepare", OSError("http"), "not_dispatched", 0), ("dispatch", KeyError("state"), "not_dispatched", 0),
                 ("attach", live.Interrupted("signal 15"), "left_dispatched", 0), ("launch", KeyError("x"), "uncertain", 1),
                 ("account", safety.Refused("x"), "uncertain", 0), ("launch", live.Interrupted("signal 2"), "uncertain", 1)]
        for method, error, recovery, still_open in cases:
            with self.subTest(method=method, error=type(error).__name__):
                fake = Fake(raises={method: error})
                expected = "interrupted" if isinstance(error, live.Interrupted) else \
                    "recovery_error" if method == "account" else "unexpected_error"  # accounting refusals are classified
                result, ledger = self.assert_aborts(fake, expected)
                self.assertEqual(result["abort"]["recovery"], recovery)
                self.assertIn(type(error).__name__, result["abort"]["detail"])
                self.assertEqual("record:uncertain" in fake.calls, recovery == "uncertain")
                self.assertEqual("record:not_dispatched" in fake.calls, recovery == "not_dispatched")
                # A launch interrupted mid-way keeps its reservation open (counted in full); before any launch it is released.
                self.assertEqual(ledger.summary()["pilot"]["open_reservations"], still_open)

    def test_a_ledger_refusal_mid_admission_aborts(self):
        fake = Fake()
        driver, ledger = self.driver(fake)

        def refuse(*args, **kwargs):
            raise safety.Refused("the call ledger is damaged")
        ledger.settle = refuse
        result = driver.run()
        self.assertEqual((result["abort"]["reason"], result["abort"]["recovery"]), ("unexpected_error", "uncertain"))
        self.assertIn("kill_switch", fake.calls)

    def test_a_failing_kill_switch_is_recorded_not_raised(self):
        fake = Fake(off_route=1, raises=dict(kill_switch=OSError("down")))
        driver, _ = self.driver(fake)
        result = driver.run()
        self.assertEqual(result["abort"]["kill_switch"], dict(state="error", reason="OSError"))
        self.assertFalse(result["abort"]["kill_switch_ok"])

    def test_repo_changes_outside_dirty_paths_abort(self):
        fake = Fake(repo=dict(new=["runtime/x.py"], dirty_changed=[], head_changed=False))
        result, _ = self.assert_aborts(fake, "repo_changed", n=5)
        self.assertEqual(fake.calls.count("record:complete"), 5)  # checked after every 5 admissions
        self.assertEqual(result["abort"]["detail"], ["runtime/x.py"])

    def test_changes_to_already_dirty_paths_are_reported_only(self):
        fake = Fake(repo=dict(new=[], dirty_changed=["README.md"], head_changed=True))
        driver, _ = self.driver(fake, n=6)
        result = driver.run()
        self.assertIsNone(result["abort"])
        self.assertEqual(fake.calls.count("repo"), 2)  # after admission 5 and at the end
        self.assertEqual(result["repo"][-1], dict(at="end", new=[], dirty_changed=["README.md"], head_changed=True))

    def test_n_is_bounded(self):
        with self.assertRaises(safety.Refused):
            self.driver(Fake(), n=21)


class CanaryStateTests(Temp):
    def state(self):
        return self.private(self.tmp / "q") / "canary"

    def test_refusals(self):
        canary.check_canary_state(self.state(), home=self.home)  # a fresh dir under an owner-only parent is fine
        production = (self.tmp / "prod" / "settings.json", self.tmp / "prod" / "organizations.sqlite3")
        repo = self.private(self.tmp / "repo")
        (repo / ".git").mkdir()
        loose = self.private(self.tmp / "loose", 0o755)
        cases = [self.tmp / "prod", repo / "canary", loose / "canary", self.home / ".local/state/vibe-workflow/x",
                 self.home / ".config/opencode/canary", Path("relative")]
        for case in cases:
            with self.subTest(case=str(case)):
                with self.assertRaises(safety.Refused):
                    canary.check_canary_state(case, home=self.home, production=production)

    def test_production_defaults_are_refused(self):
        from scripts import routing as command
        with self.assertRaises(safety.Refused):
            canary.check_canary_state(command.default_settings().parent, home=Path.home())

    def test_initialize_then_kill_switch_on_the_real_command(self):
        from tests.qualification import server
        state = self.state()
        paths = canary.check_canary_state(state, home=self.home)
        routing_cmd = server.Routing(paths["settings"], paths["ledger"])
        record = canary.initialize_state(routing_cmd)
        self.assertEqual(record["created"], True)
        self.assertIn("canary ledger only", record["authority_required"])
        status = routing_cmd("status")[0]
        self.assertEqual(canary.preflight(dict(status, project_pinned=True)), [])
        self.assertEqual(canary.initialize_state(routing_cmd)["created"], False)  # never re-initialized
        env = live.CanaryEnv(types_namespace(routing=routing_cmd))
        self.assertEqual(env.kill_switch()["state"], "updated")
        status = routing_cmd("status")[0]
        self.assertFalse(status["settings"]["smart_routing"])
        self.assertIn("smart_routing_off", canary.preflight(dict(status, project_pinned=True)))
        routing_cmd.stop()

    def test_an_unclean_canary_ledger_is_refused_with_reset_advice(self):
        from tests.qualification import server
        paths = canary.check_canary_state(self.state(), home=self.home)
        routing_cmd = server.Routing(paths["settings"], paths["ledger"])
        canary.initialize_state(routing_cmd)
        self.assertEqual(routing_cmd("prepare", **canary.plan_assignments(1, "left")[0]["body"])[0]["state"], "admitted")
        refusals = canary.preflight(dict(routing_cmd("status")[0], project_pinned=True))
        self.assertIn("open_attempts", refusals)
        self.assertIn("deleting the canary directory", canary.RESET_ADVICE)


class CanaryEnvTests(unittest.TestCase):
    class Server:
        def __init__(self):
            self.log, self.rpcs, self.created = [], [], []

        def hooks(self):
            return list(self.log)

        def rpc(self, method, payload):
            self.rpcs.append((method, sorted(payload)))
            return 200, dict(state={"attach": "attached", "detach": "detached"}.get(method, "ok")), 1.0

        def write_control(self, **change):
            pass

    def run_double(self):
        server = self.Server()
        run = types_namespace(server=server, data=live.Run.data, outcome=lambda messages, began: dict(outcome="succeeded"),
                              gate=lambda permitted: None)

        def create_session(session_id=None, **kwargs):
            server.created.append(session_id)
            return 200, dict(data=dict(id=session_id)), 1.0

        def prompt(session, message_id=None):
            base = dict(session=session, model="haiku", provider="claude-agent", variant="low", hook="model.request")
            server.log += [dict(base, phase="pre", kind="primary", t=1.0), dict(base, phase="post", kind="primary", t=1.4, decision="transmit"),
                           dict(base, phase="pre", kind="title", t=2.0), dict(base, phase="post", kind="title", t=2.1, decision="guard_title"),
                           dict(base, phase="pre", kind="primary", t=3.0, model="sonnet", session="ses_other")]
            return 200, {}, 1.0
        run.create_session, run.prompt = create_session, prompt
        run.wait_idle = lambda session, began: []
        return run, server

    def test_launch_counts_what_qual_post_transmitted(self):
        run, server = self.run_double()
        env = live.CanaryEnv(run)
        result = env.launch("ses_" + "a" * 32, "msg_" + "b" * 32, allow_title=False)
        self.assertEqual(result["transmitted"], dict(primary=1, title=0, other=0))
        self.assertEqual((result["off_route"], result["blocked_titles"], result["check_ms"]), (1, 1, 0.4))
        self.assertTrue(env.attach("ses_x", "a", "t" * 43))
        self.assertTrue(env.detach("ses_x", "a", "t" * 43))
        self.assertEqual([m for m, _ in server.rpcs], ["attach", "detach"])

    def test_a_session_the_server_would_not_create_is_not_launched(self):
        run, server = self.run_double()
        run.create_session = lambda session_id=None, **kwargs: (200, dict(data=dict(id="ses_other")), 1.0)
        self.assertEqual(live.CanaryEnv(run).launch("ses_x", "msg_x", False), dict(idle=False))
        self.assertEqual(server.log, [])


class SequencingTests(Temp):
    """Nothing may be sent before the plugin-chain probes pass, and canary refusals write nothing."""

    def quiet(self, run):
        run.snapshot_user_dirs = lambda: {}
        run.report_only_home = lambda: {}
        run.compare_user_dirs = lambda before, home: None
        return run

    def test_qualification_stops_in_stage_zero_when_the_chain_is_not_proven(self):
        base = self.private(self.tmp / "q")
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        run = self.quiet(live.Run(base, ledger, through=4))
        order = []
        run.start = lambda stage: order.append("start")
        run.server.rpc = lambda method, payload: (200, dict(state="not_ready" if "set" not in order else "ok"), 1.0)

        class FakeRouting:
            argv = []

            def __call__(self, op, **body):
                order.append(op)
                return dict(set=dict(state="updated"), prepare=dict(state="admitted", admission={}),
                            status=dict(state="ok", ledger=dict(authority_required=True, unstamped_open_holds=0)))[op], 1.0

            def stop(self):
                pass
        run.routing = FakeRouting()
        run.probes = lambda stage: order.append("probes") or ["guard_probe_reason"]
        run.stage1 = lambda: order.append("stage1")
        run.prompt = lambda *args, **kwargs: order.append("prompt")
        results = run.run()
        self.assertEqual(order, ["start", "set", "prepare", "status", "probes"])
        self.assertIn("guard_probe_reason", results["blockers"][0])
        self.assertTrue(results["cleanup"]["verified_by_harness"])
        self.assertEqual(ledger.summary()["qualification"]["spent"], 5)

    def canary_run(self, prepare_left=False, probes=()):
        project = self.private(self.tmp / "project")
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        exclude = project / ".git" / "info" / "exclude"
        before = exclude.read_bytes()
        state = canary.check_canary_state(self.private(self.tmp / "q") / "canary", home=self.home)
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        run = self.quiet(live.Run(self.private(self.tmp / "iso"), ledger, canary_project=str(project), canary_state=state))
        if prepare_left:
            canary.initialize_state(run.routing)
            run.routing("prepare", **canary.plan_assignments(1, "left")[0]["body"])
        order = []
        real_start = run.start
        run.start = lambda stage: order.append("start")
        run.server.rpc = lambda method, payload: (200, dict(state="ok"), 1.0)
        run.probes = lambda stage: order.append("probes") or list(probes)
        built = []
        original = canary.PilotDriver
        canary.PilotDriver = lambda *args, **kwargs: built.append(1) or original(*args, **kwargs)
        try:
            results = run.run()
        finally:
            canary.PilotDriver = original
        del real_start
        return results, order, built, project, exclude, before

    def test_a_canary_refusal_writes_nothing_to_the_repo_and_starts_no_server(self):
        results, order, built, project, exclude, before = self.canary_run(prepare_left=True)
        self.assertEqual(order, [])
        self.assertEqual(built, [])
        self.assertIn("open_attempts", results["blockers"][0])
        self.assertFalse((project / ".vibe-qual-canary").exists())
        self.assertEqual(exclude.read_bytes(), before)

    def test_failed_probes_stop_the_canary_before_any_admission(self):
        results, order, built, *_ = self.canary_run(probes=["guard_probe_reason"])
        self.assertEqual(order, ["start", "probes"])
        self.assertEqual(built, [])
        self.assertIn("guard_probe_failed", results["blockers"][0])


class PilotHarness(Temp):
    """A canary-mode Run on temp stores with the live-shaped OpenCode double and the real routing command."""

    def pilot(self):
        project = self.private(self.tmp / "project")
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        state = canary.check_canary_state(self.private(self.tmp / "q") / "canary", home=self.home)
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        ledger.set_cap("pilot", 28, "test")
        base = self.private(self.tmp / "iso")
        run = live.Run(base, ledger, canary_project=str(project), canary_state=state)
        self.addCleanup(run.routing.stop)
        canary.initialize_state(run.routing)
        run.server = FakeOpenCode(run.routing, base / "hooks.jsonl")
        run.repo_start = safety.repo_snapshot(project)
        return run, ledger


@unittest.skipUnless(shutil.which("git"), "git is required")
class PilotEndToEndTests(PilotHarness):
    """The whole driver path with the live-shaped fake OpenCode client and the real routing command on temp stores."""

    def test_the_run_object_keeps_its_account_method_in_both_modes(self):
        run, _ = self.pilot()
        self.assertTrue(callable(run.account))
        self.assertTrue(callable(live.Run(self.private(self.tmp / "iso2"), run.ledger).account))

    def test_one_full_admission_settles_clean(self):
        run, ledger = self.pilot()
        result = canary.PilotDriver(live.CanaryEnv(run), ledger, n=1, run=run.run_id, tag="e2e").run()
        self.assertIsNone(result["abort"], result["abort"])
        self.assertEqual((result["completed"], result["settlements"]), (1, {"window": 1}))
        admission = result["admissions"][0]
        self.assertEqual(admission["sent"], dict(primary=1, title=1, other=0))
        self.assertEqual(admission["unknown"], [])
        self.assertTrue(admission["released"])  # subscription units released, no dollars
        self.assertFalse(admission["charged"])
        for op in ("prepare", "health", "dispatch", "attach", "check", "record"):
            self.assertEqual(len(result["latency"][op]), 1, op)
        status = run.routing("status")[0]
        self.assertEqual(canary.anomalies(status), [])
        self.assertEqual(status["ledger"]["open_attempts"], 0)
        self.assertEqual(ledger.summary()["pilot"]["spent"], 2)

    def test_twenty_admissions_end_clean_and_admission_two_onwards_carry_no_title(self):
        run, ledger = self.pilot()
        result = canary.PilotDriver(live.CanaryEnv(run), ledger, n=20, run=run.run_id, tag="e2e20").run()
        self.assertIsNone(result["abort"], result["abort"])
        self.assertEqual((result["completed"], result["settlements"]), (20, {"window": 20}))
        self.assertEqual([a["sent"]["title"] for a in result["admissions"]], [1] + [0] * 19)
        # Admissions 2-20 create their session with a title, so OpenCode sends no title request at all.
        self.assertEqual(result["blocked_titles"], 0)
        self.assertEqual([a["kinds"] for a in result["admissions"]], [["primary", "title"]] + [["primary"]] * 19)
        self.assertEqual([a["title_state"] for a in result["admissions"]], ["known"] + ["zero"] * 19)
        self.assertEqual({a["rereads"] for a in result["admissions"]}, {0})
        self.assertEqual({a["role"] for a in result["admissions"]}, {"implementer", "reviewer", "researcher"})
        self.assertEqual({a["band"] for a in result["admissions"]}, {"low", "medium"})
        self.assertTrue(all(a["released"] and not a["charged"] for a in result["admissions"]))
        self.assertEqual([r["at"] for r in result["repo"]], [5, 10, 15, 20, "end"])
        self.assertEqual(canary.anomalies(run.routing("status")[0]), [])
        self.assertEqual(ledger.summary()["pilot"], dict(cap=28, spent=21, remaining=7, open_reservations=0))


class AbortRecordingTests(Temp):
    def driver(self, fake, n=1):
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / ("c%d.jsonl" % id(fake)))
        return canary.PilotDriver(fake, ledger, n=n, run="t")

    def test_the_origin_is_recorded_without_payload_text(self):
        secret = "payload text sk-not-a-real-key"

        class Leaky(Fake):
            def account(self, session, titles_sent=0):
                raise TypeError(secret)
        fake = Leaky()
        result = self.driver(fake).run()
        abort = result["abort"]
        self.assertEqual(abort["origin"]["type"], "TypeError")
        self.assertEqual((abort["origin"]["file"], abort["origin"]["function"]), ("canary.py", "admit"))
        self.assertIsInstance(abort["origin"]["line"], int)
        self.assertEqual(abort["origin"]["innermost"], dict(file="test_qualification_canary.py", function="account"))
        self.assertEqual(abort["phase"], "launched")
        self.assertNotIn("sk-not", json.dumps(result))
        self.assertNotIn("payload text", json.dumps(result))

    def test_partial_admission_latencies_are_kept_on_abort(self):
        fake = Fake(fail=dict(attach=False))
        abort = self.driver(fake).run()["abort"]
        self.assertEqual(sorted(abort["timings"]), ["attach", "dispatch", "health", "prepare"])
        self.assertTrue(all(type(v) is float for v in abort["timings"].values()))

    def test_shapes_are_kept_only_for_aborted_launched_admissions(self):
        class Shaped(Fake):
            def diagnostics(self, session):
                self.calls.append("diagnostics")
                return dict(record=safety.shape_of({"id": "ses_1bf00000000000000000000Aa", "title": "secret title",
                                                    "tokens": {"input": 2, "cache": {"read": 1}}, "list": list(range(50))}))
        fake = Shaped(raises=dict(account=KeyError("x")))
        abort = self.driver(fake).run()["abort"]
        text = json.dumps(abort["shapes"])
        self.assertNotIn("secret title", text)
        self.assertNotIn("ses_1bf", text)
        self.assertIn('"title": "str"', text)
        self.assertLess(len(text), 4096)
        clean = Shaped()
        self.driver(clean).run()
        self.assertNotIn("diagnostics", clean.calls)
        early = Shaped(fail=dict(attach=False))
        self.assertNotIn("shapes", self.driver(early).run()["abort"])

    def test_shape_of_is_bounded(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}, **{"k%d" % i: i for i in range(100)}}
        shape = safety.shape_of(deep)
        self.assertLessEqual(len(json.dumps(shape)), safety.MAX_SHAPE)
        self.assertEqual(safety.shape_of([1, "x", None]), dict(list=3, items=["int", "str", "null"]))


class CanaryResetTests(Temp):
    def state(self):
        state = canary.check_canary_state(self.private(self.tmp / "q") / "canary", home=self.home)
        state["dir"].mkdir(mode=0o700)
        for name in ("settings.json", "ledger.sqlite3", ".settings.lock"):
            (state["dir"] / name).write_text("")
        return state["dir"]

    def test_reset_deletes_only_a_quiet_canary_dir(self):
        directory = self.state()
        canary.reset_state(directory, processes="  1 /Applications/OpenCode.app/.../opencode-cli serve --service\n", home=self.home)
        self.assertFalse(directory.exists())

    def test_reset_refusals(self):
        live_lines = [" 9 python3 -B tests/qualification/opencode_live.py --canary",
                      " 9 opencode serve --hostname 127.0.0.1 --port 5555",
                      " 9 python3 -I -B scripts/routing.py --serve --settings %s/settings.json" % (self.tmp / "q" / "canary")]
        for line in live_lines:
            with self.subTest(line=line[:40]):
                directory = self.state()
                with self.assertRaises(safety.Refused):
                    canary.reset_state(directory, processes=line + "\n", home=self.home)
                self.assertTrue(directory.exists())
                shutil.rmtree(directory)
        directory = self.state()
        (directory / "notes.txt").write_text("unexpected")
        with self.assertRaises(safety.Refused):
            canary.reset_state(directory, processes="", home=self.home)
        from scripts import routing as command
        with self.assertRaises(safety.Refused):
            canary.reset_state(command.default_settings().parent, processes="")  # the production state
        with self.assertRaises(safety.Refused):
            canary.reset_state(self.tmp / "q" / "absent", processes="", home=self.home)


@unittest.skipUnless(shutil.which("git"), "git is required")
class SettledAccountingTests(PilotHarness):
    """SF1: a lagging session total is re-read (bounded); a regression or a changed message set aborts."""

    def admit(self, **knobs):
        run, ledger = self.pilot()
        run.reread_wait = 0
        run.server.__dict__.update(knobs)
        result = canary.PilotDriver(live.CanaryEnv(run), ledger, n=1, run=run.run_id, tag="settle").run()
        return result, run

    def test_a_lagging_title_total_is_reread_until_it_settles(self):
        result, run = self.admit(lag_reads=2)
        self.assertIsNone(result["abort"], result["abort"])
        admission = result["admissions"][0]
        self.assertEqual((admission["rereads"], admission["kinds"], admission["title_state"]), (2, ["primary", "title"], "known"))
        self.assertEqual(result["deviations"], [])

    def test_exhausted_rereads_record_complete_with_a_deviation(self):
        result, run = self.admit(lag_reads=50)
        self.assertIsNone(result["abort"])
        admission = result["admissions"][0]
        self.assertEqual((admission["rereads"], admission["kinds"]), (5, ["primary"]))
        self.assertEqual(result["deviations"], [dict(admission=1, deviation="title_unaccounted")])
        self.assertEqual(run.server.session_reads, 6)  # the first read plus at most five re-reads

    def test_a_decreasing_total_aborts(self):
        result, _ = self.admit(lag_reads=50, regress_at=2)
        self.assertEqual((result["abort"]["reason"], result["abort"]["detail"]), ("recovery_error", "accounting_regressed"))

    def test_a_changed_message_set_aborts(self):
        result, _ = self.admit(lag_reads=50, extra_message_at=2)
        self.assertEqual((result["abort"]["reason"], result["abort"]["detail"]), ("recovery_error", "accounting_regressed"))

    def test_accounting_errors_are_never_retried_and_name_their_fixed_reason(self):
        result, run = self.admit(malformed_total=True)
        abort = result["abort"]
        self.assertEqual((abort["reason"], abort["detail"]), ("recovery_error", "refusal: invalid session"))
        self.assertEqual(run.server.session_reads, 2)  # one accounting read (never retried) + the abort's diagnostics read
        progress = abort["account_progress"]
        self.assertEqual(progress["session_read"], 200)
        self.assertEqual(progress["message_pages"], 2)  # how far the read got before the refusal

    def test_an_unexpected_extra_title_is_still_blocked_at_zero(self):
        run, ledger = self.pilot()
        run.server.force_title = True
        result = canary.PilotDriver(live.CanaryEnv(run), ledger, n=2, run=run.run_id, tag="force").run()
        self.assertIsNone(result["abort"], result["abort"])
        self.assertEqual(result["blocked_titles"], 1)  # admission 2's titled session still tried one: guard_title
        self.assertEqual(result["admissions"][1]["sent"], dict(primary=1, title=0, other=0))


@unittest.skipUnless(shutil.which("git"), "git is required")
class BudgetArithmeticTests(PilotHarness):
    def test_attempt_two_fits_the_raised_cap(self):
        self.assertEqual(canary.planned_exposure(20), 23)  # 2 probes + 2 (first admission and its title) + 19
        run, ledger = self.pilot()
        ledger.settle(ledger.reserve("pilot", "attempt 1, admission 1", 2), 2)
        self.assertEqual(ledger.remaining("pilot"), 26)
        self.assertLessEqual(canary.planned_exposure(20), ledger.remaining("pilot"))
        for label in ("guard probe", "pending probe"):
            ledger.settle(ledger.reserve("pilot", label, 1), 0)
        result = canary.PilotDriver(live.CanaryEnv(run), ledger, n=20, run=run.run_id, tag="budget").run()
        self.assertIsNone(result["abort"], result["abort"])
        self.assertEqual(ledger.summary()["pilot"], dict(cap=28, spent=23, remaining=5, open_reservations=0))


class AbortDetailTests(Temp):
    def run_with(self, error):
        class Raising(Fake):
            def account(self, session, titles_sent=0):
                raise error
        state = self.private(self.tmp / "state")
        ledger = safety.CallLedger.initialize(state / ("c%d.jsonl" % len(list(state.iterdir()))))
        return canary.PilotDriver(Raising(), ledger, n=1, run="t").run()["abort"]["detail"]

    def test_refusals_and_harness_errors_are_told_apart(self):
        from runtime.adapters.opencode import AdapterError
        from runtime.opencode_accounting import AccountingError
        self.assertEqual(self.run_with(AccountingError("invalid lineage")), "refusal: invalid lineage")
        self.assertEqual(self.run_with(AdapterError("invalid message id")), "refusal: invalid message id")
        for error in (TypeError("x"), AttributeError("x"), NameError("x"), KeyError("x")):
            self.assertEqual(self.run_with(error), "harness_error: %s" % type(error).__name__)


if __name__ == "__main__":
    unittest.main()
