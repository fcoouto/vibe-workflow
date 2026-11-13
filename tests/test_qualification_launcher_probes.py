"""`launcher-probes` (A1-A7) with doubles only: a fake launcher-started server, the real `opencode_launcher.launch`
and the real routing command on temporary stores. No OpenCode, no model call."""
import json
from pathlib import Path

from runtime import routing_observations
from tests.qualification import launcher_probes, live_common, safety
from tests.qualification.doubles import Temp
from tests.qualification.launcher_doubles import FakeEnv, FakeLauncherHost

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="runbook part A")


class ProbeTests(Temp):
    def setUp(self):
        super().setUp()
        self.base = self.private(self.tmp / "q")
        self.ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        self.ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
        self.real_home = self.private(self.tmp / "real-home")
        self.env = FakeEnv(self.tmp / "work")

    def probes(self, **kwargs):
        self.base = self.private(self.tmp / ("q%d" % len(list(self.tmp.glob("q*")))))  # a fresh isolated dir per run
        return launcher_probes.LauncherProbes(self.base, self.ledger, "iso-test", self.env, self.real_home, "tester",
                                              safety.Redactor(), **kwargs)

    def reservations(self):
        return [e for e in self.ledger.export()["entries"] if e["kind"] == "reservation" and e["purpose"] == "isolation"]

    def test_unidentified_plugin_rows_cannot_disappear_beside_an_active_shim(self):
        active = dict(id="vibe-workflow.routing", source=dict(type="local", path="/synthetic/shim"),
                      features=dict(server=True), state=dict(status="active"))
        for row in (dict(source=dict(type="local", path="/synthetic/other"), features=dict(server=True),
                         state=dict(status="failed", error="private load error")), {}, None, "untyped"):
            with self.subTest(row=row):
                checks, others = launcher_probes.plugin_checks(launcher_probes.plugin_ids(dict(data=[active, row])))
                self.assertTrue(checks["shim_listed_once"])
                self.assertFalse(checks["no_other_plugin"])
                self.assertEqual(len(others), 1)
                self.assertNotIn("private load error", json.dumps(others))

    def test_failed_or_untyped_shim_never_counts_as_loaded(self):
        for state in (dict(status="failed", error="private load error"), {}, None):
            with self.subTest(state=state):
                row = dict(id="vibe-workflow.routing", source=dict(type="local", path="/synthetic/shim"),
                           features=dict(server=True), state=state)
                checks, _ = launcher_probes.plugin_checks(launcher_probes.plugin_ids(dict(data=[row])))
                self.assertFalse(checks["shim_listed_once"])

    def test_only_active_identified_plugins_pass_the_listing_checks(self):
        rows = [dict(id=name, source=dict(type=source), features=dict(server=True), state=dict(status="active"))
                for name, source in (("opencode.agent", "builtin"), ("vibe-workflow.routing", "sdk"))]
        checks, others = launcher_probes.plugin_checks(launcher_probes.plugin_ids(dict(data=rows)))
        self.assertEqual(checks, dict(shim_listed_once=True, no_other_plugin=True))
        self.assertEqual(others, [])

    def test_zero_call_probes_pass_on_an_isolating_server_and_send_nothing(self):
        results = self.probes().run(["A1", "A2", "A3", "A4"])
        for probe in ("A1", "A2", "A3", "A4"):
            with self.subTest(probe):
                self.assertIs(results[probe]["passed"], True, results[probe])
                self.assertTrue(results[probe]["zero_call_proof"]["proven"])
        self.assertEqual(sum(host.prompts for host in self.env.hosts), 0)
        self.assertTrue(all(host.stopped for host in self.env.hosts))
        self.assertEqual(self.reservations(), [])  # zero-call probes reserve nothing
        homes = [environ["HOME"] for environ in self.env.environs]
        self.assertEqual(homes, [str(self.base / "home"), str(self.real_home)])  # two server starts for four probes
        self.assertEqual(results["A1"]["server"], "temp")
        self.assertEqual(results["A4"]["server"], "real")
        for host in self.env.hosts:  # the routing stores are the run's own, never the user's
            self.assertTrue(all(str(path).startswith(str(self.base)) for path in host.probe_stores))
            self.assertTrue(host.environ["XDG_DATA_HOME"].startswith(str(self.base)))

    def test_a_server_that_loads_hostile_config_fails_A2_and_A3(self):
        self.env.knobs["honour_hostile"] = True
        results = self.probes().run(["A2", "A3"])
        for probe in ("A2", "A3"):
            with self.subTest(probe):
                checks = results[probe]["checks"]
                self.assertIs(results[probe]["passed"], False)
                self.assertFalse(checks["no_hostile_plugin"])
                self.assertFalse(checks["no_hostile_mcp"])
                self.assertFalse(checks["no_marker_written"])
        self.assertFalse((self.base / "home" / ".config" / "opencode").exists())  # the global payload is removed
        self.env.knobs["honour_hostile"] = "project"  # one source loading fails only its own probe (and A1)
        results = self.probes().run(["A1", "A2", "A3"])
        self.assertEqual({probe: results[probe]["passed"] for probe in results}, dict(A1=False, A2=False, A3=True))
        self.assertFalse(results["A1"]["checks"]["no_other_plugin"])
        self.assertEqual(len(self.env.hosts), 2)  # one server per run of the temp-HOME group

    def test_a_relaxed_permission_fails_the_session_deny(self):
        self.env.knobs["effects"] = dict(shell="allow")
        result = self.probes().run(["A2"])["A2"]
        self.assertFalse(result["checks"]["session_denies"])
        self.assertEqual(result["effects"]["shell"]["effect"], "allow")

    def test_A1_checks_the_password_and_the_log(self):
        self.env.knobs.update(unauth=200)
        result = self.probes().run(["A1"])["A1"]
        self.assertFalse(result["checks"]["password_required"])
        env = FakeEnv(self.tmp / "work2")
        original = env.start

        def noisy(*args):
            host = original(*args)
            (host.workdir / "server.log").write_text("fetching https://models.dev/api.json\n")
            return host
        env.start = noisy
        self.env = env
        result = self.probes().run(["A1"])["A1"]
        self.assertFalse(result["checks"]["no_fetch_mentioned"])
        self.assertNotIn("models.dev", json.dumps(result))

    def test_the_zero_call_wrapper_refuses_every_sending_endpoint(self):
        host = FakeLauncherHost(self.tmp / "p", self.tmp / "w")
        wrapped = live_common.ZeroCallHost(host)
        for path in ("/api/session/s/prompt", "/api/session/s/command", "/api/session/s/shell", "/api/session/s/summarize"):
            with self.subTest(path), self.assertRaises(live_common.ZeroCall):
                wrapped.request("POST", path, {})
        self.assertEqual(host.prompts, 0)
        for method in ("attach", "detach", "check"):
            with self.subTest(method), self.assertRaises(live_common.ZeroCall):
                wrapped.rpc(method, {})
        self.assertEqual(wrapped.rpc("health", {})[0], 200)
        proof = live_common.zero_call_proof(wrapped, live_common.message_counts(wrapped))
        self.assertFalse(proof["proven"])
        self.assertIn("sending_endpoint_attempted", proof["reasons"])

    def test_a_message_on_the_server_breaks_the_zero_call_proof(self):
        host = FakeLauncherHost(self.tmp / "p", self.tmp / "w")
        host.request("POST", "/api/session", dict(title="t"))
        host.request("POST", "/api/session/ses_fake0001/prompt", dict(text="x"))
        proof = live_common.zero_call_proof(live_common.ZeroCallHost(host), live_common.message_counts(host))
        self.assertEqual(proof["reasons"], ["messages_present"])

    def test_A4_reads_the_real_global_plugin_list_read_only(self):
        config = self.private(self.real_home / ".config" / "opencode") / "opencode.json"
        config.write_text('// mine\n{"plugin": ["/x/runtime/adapters/opencode_plugin.ts", "other"]}\n')
        before = config.stat().st_mtime_ns, config.read_bytes()
        result = self.probes().run(["A4"])["A4"]
        self.assertEqual(result["global_config"], dict(files=1, entries=2, routing_entries=1, unreadable=0))
        self.assertEqual((config.stat().st_mtime_ns, config.read_bytes()), before)
        self.env.knobs["plugins"] = ["vibe-workflow.routing", "vibe-workflow.routing"]
        self.assertFalse(self.probes().run(["A4"])["A4"]["checks"]["shim_listed_once"])

    def test_A6_launches_through_the_real_launcher_and_settles_one_call(self):
        saved = routing_observations.default_source
        result = self.probes().run(["A4", "A6"])["A6"]
        self.assertIs(result["passed"], True, result)
        self.assertEqual(result["launch"]["state"], "recorded")
        self.assertEqual((result["launch"]["event"], result["launch"]["billing_class"]), ("complete", "included_subscription"))
        self.assertTrue(result["launch"]["detached"])
        self.assertEqual(result["reply"]["text_from"], "content")
        self.assertTrue(result["reply"]["reply_is_ok"])
        self.assertTrue(result["status_after"]["clean"])
        self.assertEqual(result["sent"], dict(reserved=1, sent=1, basis="assistant_messages"))
        self.assertEqual([(r["reserved"], r["sent"]) for r in self.reservations()], [(1, 1)])
        self.assertIs(routing_observations.default_source, saved)  # the static source is removed again
        self.assertEqual(self.env.environs[0]["HOME"], str(self.real_home))
        host = self.env.hosts[0]
        self.assertTrue(all(token is None or token not in json.dumps(result) for token in host.tokens))

    def test_more_requests_than_reserved_are_recorded_and_fail(self):
        self.env.knobs["extra_assistants"] = 1
        result = self.probes().run(["A4", "A6"])["A6"]
        self.assertIs(result["passed"], False)
        self.assertIn("BudgetExceeded", result.get("blocker", "") + json.dumps(result))
        self.assertEqual([(r["reserved"], r["sent"]) for r in self.reservations()], [(1, 2)])

    def test_A7_needs_a_model_listed_without_variants(self):
        self.assertIsNone(self.probes().run(["A4", "A7"])["A7"]["passed"])  # skipped without the argument
        self.env.knobs["models"] = [dict(providerID="claude-agent", id="opus", variants=[dict(id="low")])]
        result = self.probes(no_variant_model="claude-agent/opus").run(["A4", "A7"])["A7"]
        self.assertIs(result["passed"], False)
        self.assertEqual(self.reservations(), [])  # refused before any reservation
        self.env.knobs["models"] = [dict(providerID="claude-agent", id="opus", variants=[])]
        result = self.probes(no_variant_model="claude-agent/opus").run(["A4", "A7"])["A7"]
        self.assertIs(result["passed"], True, result)
        self.assertEqual(result["listed_variants"], [])

    def test_A5_records_outside_reads_and_settles_on_the_count(self):
        self.assertIsNone(self.probes().run(["A1", "A2", "A3", "A5"])["A5"]["passed"])
        self.env.knobs["models"] = [dict(providerID="openai", id="gpt-6-luna", variants=["none", "low"])]
        self.env.knobs["reply"] = "I cannot read files outside the project."
        result = self.probes(a5_model="openai/gpt-6-luna#none").run(["A1", "A2", "A3", "A5"])["A5"]
        self.assertIs(result["passed"], True, result)
        self.assertEqual(result["outside_reads"], "denied")
        self.assertTrue(result["checks"]["zero_calls_before_prompt"])
        self.assertEqual(self.env.signins, 1)
        self.assertEqual([(r["reserved"], r["sent"]) for r in self.reservations()], [(2, 1)])

        def leaking(prompt):
            path = prompt.split("Read the file ", 1)[1].split(" and reply", 1)[0]
            return Path(path).read_text().split()[0]
        self.env.knobs["reply"] = leaking
        result = self.probes(a5_model="openai/gpt-6-luna#none").run(["A1", "A2", "A3", "A5"])["A5"]
        self.assertEqual(result["outside_reads"], "allowed")
        self.assertIs(result["passed"], False)
        self.assertNotIn("QUALDECOY", json.dumps(result))  # the decoy word is compared, never recorded
        accepted = self.probes(a5_model="openai/gpt-6-luna#none", accept_outside_reads=True).run(["A1", "A2", "A3", "A5"])["A5"]
        self.assertEqual(accepted["outside_reads"], "accepted_risk")
        self.assertIs(accepted["passed"], True)

    def test_probe_selection_and_call_needs(self):
        self.assertEqual(launcher_probes.parse_probes(None), list(launcher_probes.PROBES))
        self.assertEqual(launcher_probes.parse_probes("a2,A1"), ["A1", "A2"])
        for bad in ("A8", "A1,A1", ","):
            with self.subTest(bad), self.assertRaises(safety.Refused):
                launcher_probes.parse_probes(bad)
        self.assertEqual(launcher_probes.calls_needed(launcher_probes.PROBES), 4)
        self.assertEqual(launcher_probes.calls_needed(["A1", "A2", "A3", "A4"]), 0)


class GatingTests(Temp):
    """Review B1: A5 runs on its own clean server only after A1-A3 passed; A6/A7 run only after A4 passed."""

    def setUp(self):
        super().setUp()
        self.ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        self.ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
        self.models = [dict(providerID="openai", id="gpt-6-luna", variants=["none"]),
                       dict(providerID="claude-agent", id="haiku", variants=[dict(id="low")])]

    def run_probes(self, probes, **knobs):
        self.env = FakeEnv(self.tmp / ("w%d" % len(list(self.tmp.glob("w*")))), models=list(self.models), **knobs)
        base = self.private(self.tmp / ("q%d" % len(list(self.tmp.glob("q*")))))
        seen = []
        original = self.env.start

        def start(project, settings, ledger, environ):
            home = Path(environ["HOME"])
            seen.append(dict(project_config=(Path(project) / "opencode.json").exists() or (Path(project) / ".opencode").exists(),
                             global_config=(home / ".config" / "opencode").exists() and home != self.real,
                             home=str(home)))
            return original(project, settings, ledger, environ)
        self.env.start = start
        self.real = self.private(self.tmp / "real")
        results = launcher_probes.LauncherProbes(base, self.ledger, "r", self.env, self.real, "u", safety.Redactor(),
                                                 a5_model="openai/gpt-6-luna#none").run(probes)
        return results, seen

    def test_A5_runs_on_its_own_clean_server_after_A1_to_A3_pass(self):
        results, seen = self.run_probes(["A1", "A2", "A3", "A5"])
        self.assertTrue(all(results[p]["passed"] for p in ("A1", "A2", "A3", "A5")), json.dumps(results)[:1500])
        self.assertEqual(len(seen), 2)
        self.assertEqual((seen[0]["project_config"], seen[0]["global_config"]), (True, True))  # the hostile server
        self.assertEqual((seen[1]["project_config"], seen[1]["global_config"]), (False, False))  # A5: clean
        self.assertEqual(results["A5"]["server"], "a5")
        self.assertEqual(self.env.hosts[0].prompts, 0)
        self.assertEqual(self.env.hosts[1].prompts, 1)

    def test_A5_does_not_run_unless_A1_to_A3_passed(self):
        for probes, knobs in ((["A5"], {}), (["A1", "A2", "A3", "A5"], dict(honour_hostile="glob")),
                              (["A1", "A3", "A5"], {})):
            with self.subTest(probes=probes, knobs=knobs):
                results, seen = self.run_probes(probes, **knobs)
                self.assertIsNone(results["A5"]["passed"])
                self.assertEqual(results["A5"]["skipped"], "isolation_not_proven: A1, A2 and A3 must pass in this run")
                self.assertEqual(sum(host.prompts for host in self.env.hosts), 0)
                self.assertEqual(self.env.signins, 0)
        self.assertEqual([e for e in self.ledger.export()["entries"] if e["kind"] == "reservation"], [])

    def test_A6_and_A7_do_not_run_unless_A4_passed(self):
        config = self.private(self.tmp / "real" / ".config" / "opencode") / "opencode.json"
        config.write_text('{"plugin": ["mine"]}')
        for probes, knobs in ((["A6"], {}), (["A4", "A6"], dict(plugins=["vibe-workflow.routing", "user-plugin"]))):
            with self.subTest(probes=probes):
                results, _ = self.run_probes(probes, **knobs)
                self.assertIsNone(results["A6"]["passed"])
                self.assertEqual(results["A6"]["skipped"], "isolation_not_proven: A4 must pass in this run")
                self.assertEqual(sum(host.prompts for host in self.env.hosts), 0)
        results, _ = self.run_probes(["A4", "A6"])
        self.assertTrue(results["A4"]["checks"]["no_other_plugin"])
        self.assertTrue(results["A6"]["passed"], json.dumps(results)[:1500])
