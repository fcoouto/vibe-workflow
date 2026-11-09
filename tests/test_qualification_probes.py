"""The live-probe stages without a server: the `qualification-2` and `chatgpt` purposes and their approval lines, the
route allowlist per mode (guard, CLI and harness), redaction of OAuth secrets, and the `descendants`, `s33d` and
`chatgpt` stages against a stand-in OpenCode server with the real scripts/routing.py on temporary stores."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from tests.qualification import chatgpt, probe_stages, safety
from tests.qualification import opencode_live as live
from tests.qualification.doubles import Temp
from tests.qualification.probe_doubles import Events, ProbeServer

NODE = shutil.which("node")
QUAL2 = ("claude-agent", "haiku", "low")
GPT = ("openai", "gpt-test-mini", "low")
APPROVAL = dict(by="user (via the coordinator)", at="2026-11-09", scope="test")


def approved_ledger(path, qualification2=10, chatgpt_cap=4):
    ledger = safety.CallLedger.initialize(path)
    ledger.set_cap("qualification-2", qualification2, "approved for the live probes", approval=APPROVAL)
    ledger.set_cap("chatgpt", chatgpt_cap, "approved for the ChatGPT route", approval=APPROVAL)
    return ledger


class PurposeApprovalTests(Temp):
    def ledger(self):
        return safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")

    def test_new_purposes_start_closed_until_an_approval_line(self):
        ledger = self.ledger()
        before = ledger.summary()
        self.assertEqual(before["qualification-2"], dict(cap=0, spent=0, remaining=0, open_reservations=0))
        self.assertEqual(before["chatgpt"], dict(cap=0, spent=0, remaining=0, open_reservations=0))
        with self.assertRaises(safety.BudgetExceeded):
            ledger.reserve("qualification-2", "before approval", 1)
        ledger.set_cap("qualification-2", 10, "approved", approval=APPROVAL)
        ledger.set_cap("chatgpt", 4, "approved", approval=APPROVAL)
        self.assertEqual(ledger.summary()["qualification-2"]["remaining"], 10)
        self.assertEqual(ledger.summary()["chatgpt"]["remaining"], 4)
        for purpose, cap in (("qualification-2", 11), ("chatgpt", 5)):
            with self.assertRaises(safety.Refused):
                ledger.set_cap(purpose, cap, "too much", approval=APPROVAL)
        for purpose in ("qualification", "pilot"):  # the existing purposes and their history stay untouched
            self.assertEqual(ledger.summary()[purpose], before[purpose])
        caps = [e for e in ledger.export()["entries"] if e["kind"] == "cap"]
        self.assertEqual([(e["purpose"], e["cap"], e["approval"]["by"]) for e in caps],
                         [("qualification-2", 10, APPROVAL["by"]), ("chatgpt", 4, APPROVAL["by"])])

    def test_approvals_need_who_when_and_scope_and_purposes_must_be_known(self):
        ledger = self.ledger()
        for approval in (None, dict(by="user"), dict(by="", at="x", scope="y"), dict(by="u", at="d", scope="s", extra=1)):
            with self.assertRaises(safety.Refused, msg=approval):
                ledger.set_cap("chatgpt", 4, "approved", approval=approval)
        with self.assertRaises(safety.Refused):
            ledger.reserve("chat", "unknown", 1)
        with self.assertRaises(safety.Refused):
            ledger.set_cap("openai", 4, "x", approval=APPROVAL)


class RouteTests(unittest.TestCase):
    LISTED = [dict(id="gpt-test-mini", providerID="openai", variants=["low", "medium"]),
              dict(id="haiku", providerID="claude-agent", variants=["low"])]

    def test_parse_route(self):
        self.assertEqual(safety.parse_route("openai/gpt-test-mini#low"), GPT)
        for ref in ("openai/gpt-test-mini", "claude", "", "a/b#c#d", "openai/#low", None, "open ai/x#y"):
            with self.assertRaises(safety.Refused, msg=ref):
                safety.parse_route(ref)

    def test_each_mode_allows_exactly_its_route(self):
        self.assertEqual(safety.check_route("qualification-2", QUAL2), QUAL2)
        for route in (GPT, ("claude-agent", "haiku", "high"), ("claude-agent", "sonnet", "low")):
            with self.assertRaises(safety.Refused, msg=route):
                safety.check_route("qualification-2", route)
        self.assertEqual(safety.check_route("chatgpt", GPT, self.LISTED), GPT)
        for route in (("openai", "gpt-other", "low"), ("openai", "gpt-test-mini", "high"), QUAL2,
                      ("opencode", "gpt-test-mini", "low")):
            with self.assertRaises(safety.Refused, msg=route):
                safety.check_route("chatgpt", route, self.LISTED)
        with self.assertRaises(safety.Refused):
            safety.check_route("chatgpt", GPT, None)  # the login's model list is required
        with self.assertRaises(safety.Refused):
            safety.check_route("pilot", QUAL2)


@unittest.skipUnless(NODE, "node is required to run the recorder guard")
class RecorderRouteTests(Temp):
    DRIVER = r"""
import plugin from "./recorder.mjs"
const hooks = {}
const ctx = { options: { log: process.argv[2], control: process.argv[3] },
  session: { hook: (n, f) => { hooks.model = f } }, tool: { hook: (n, f) => { hooks.tool = f } },
  permission: { hook: (n, f) => { hooks.permission = f } }, shell: { hook: (n, f) => { hooks.shell = f } } }
await plugin.setup(ctx)
const out = []
for (const event of JSON.parse(process.argv[4])) {
  try { await hooks[event.hook](event.body); out.push("pass") } catch (error) { out.push(String(error.message)) }
}
console.log(JSON.stringify(out))
"""

    def guard(self, route, events):
        (self.tmp / "recorder.mjs").write_text(safety.recorder_source("post"))
        (self.tmp / "driver.mjs").write_text(self.DRIVER)
        (self.tmp / "control.json").write_text(json.dumps(dict(budget=9, route=route)))
        done = subprocess.run([NODE, str(self.tmp / "driver.mjs"), str(self.tmp / "hooks.jsonl"), str(self.tmp / "control.json"),
                               json.dumps(events)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    @staticmethod
    def request(route):
        provider, model, variant = route
        return dict(hook="model", body=dict(kind="primary", sessionID="ses_a", model=dict(providerID=provider, id=model, variant=variant)))

    def test_the_control_route_is_the_only_one_allowed(self):
        spawn = lambda m: dict(hook="tool", body=dict(tool="subagent", sessionID="ses_a", input=dict(model=m)))  # noqa: E731
        self.assertEqual(self.guard(list(GPT), [self.request(GPT), self.request(QUAL2), spawn("openai/gpt-test-mini#low"),
                                                spawn("claude-agent/haiku#low")]),
                         ["pass", "qualification guard (guard_model)", "pass", "qualification guard (guard_spawn_model)"])
        self.assertEqual(self.guard(list(QUAL2), [self.request(QUAL2), self.request(GPT)]),
                         ["pass", "qualification guard (guard_model)"])
        self.assertEqual(self.guard(None, [self.request(QUAL2), self.request(GPT)]),
                         ["qualification guard (guard_model)", "qualification guard (guard_model)"])

    def test_edit_resources_are_logged_bounded(self):
        resources = ["a" * 500] + ["r%d" % i for i in range(10)]
        self.guard(list(QUAL2), [dict(hook="permission", body=dict(action="edit", effect="allow", sessionID="ses_a",
                                                                   resources=resources))])
        record = json.loads((self.tmp / "hooks.jsonl").read_text().splitlines()[0])
        self.assertEqual(len(record["resources"]), 4)
        self.assertEqual(len(record["resources"][0]), 160)


class SecretKeyTests(unittest.TestCase):
    def test_oauth_secrets_are_redacted_by_key_but_the_url_and_user_code_stay(self):
        redact = safety.Redactor()
        payload = {"data": {"status": "complete", "access": "tok-xyz-1", "refresh": "ref-2", "id_token": "idt-3",
                            "headers": {"Authorization": "Bearer abc-4", "cookie": "c-5"}, "code_verifier": "v-6",
                            "device_code": "d-7", "url": "https://auth.openai.com/oauth/authorize?state=s", "user_code": "ABCD-EFGH"}}
        clean = redact(payload)
        text = json.dumps(clean)
        for value in ("tok-xyz-1", "ref-2", "idt-3", "abc-4", "c-5", "v-6", "d-7"):
            self.assertNotIn(value, text)
        self.assertIn("https://auth.openai.com/oauth/authorize?state=s", text)
        self.assertIn("ABCD-EFGH", text)
        self.assertEqual(redact.leaks("later output with tok-xyz-1"), ["tok-xyz-1"])  # remembered as secrets


class StageHarness(Temp):
    def probe_run(self, purpose="qualification-2", stages=("descendants", "s33d"), model=None, qualification2=10, **knobs):
        ledger = approved_ledger(self.private(self.tmp / "state") / "calls.jsonl", qualification2=qualification2)
        base = self.private(self.tmp / "iso")
        for name in ("routing", "usage", "project"):
            self.private(base / name)
        run = live.Run(base, ledger, purpose=purpose, stages=list(stages) if purpose == "qualification-2" else None,
                       chatgpt_model=model)
        self.addCleanup(run.routing.stop)
        route = QUAL2 if purpose == "qualification-2" else GPT
        server = ProbeServer(run.routing, base / "hooks.jsonl", base / "project", run.request_facts(route), route=route)
        if purpose == "chatgpt":
            server.control["route"] = None
            server.models = []
        server.__dict__.update(knobs)
        run.server, run.events, run.reread_wait = server, Events(server), 0
        run.start = lambda stage: None
        run.probes = lambda stage: []
        return run, ledger, server


class DescendantsStageTests(StageHarness):
    def test_positive_and_negative_runs(self):
        run, ledger, server = self.probe_run(stages=["descendants"])
        probe_stages.setup(run, "descendants")
        probe_stages.descendants(run)
        result = run.results["stages"]["descendants"]
        positive, negative = result["positive"], result["negative"]
        self.assertTrue(result["pass"], result)
        self.assertEqual((positive["rpc"]["complete"], positive["rpc"]["reason"]), (True, None))
        self.assertTrue(positive["listing_matches"])
        self.assertEqual(positive["children"], dict(tool_subagent="observed", command_child="observed", api_child="observed"))
        self.assertEqual([u for u in positive["accounting_unknown"] if u.startswith("shim_")], [])
        self.assertEqual(positive["settlement"], "window")
        self.assertTrue(positive["after_detach_refused"])
        self.assertTrue(positive["root_created_after_attach"])
        self.assertEqual((negative["rpc"]["complete"], negative["rpc"]["reason"]), (False, "root_creation_unobserved"))
        self.assertIn("shim_lineage_incomplete", negative["accounting_unknown"])
        self.assertTrue(negative["api_offline"]["costs_null"])
        self.assertIn("shim_lineage_incomplete", negative["api_offline"]["unknown"])
        self.assertEqual(negative["calls"], 0)
        self.assertEqual(positive["calls"], 5)
        self.assertEqual(ledger.summary()["qualification-2"], dict(cap=10, spent=5, remaining=5, open_reservations=0))
        self.assertEqual(ledger.summary()["qualification"]["spent"], 5)  # another purpose untouched

    def test_without_the_tool_path_the_stage_records_it_unobserved(self):
        run, ledger, server = self.probe_run(stages=["descendants"], tool_subagent=False)
        probe_stages.setup(run, "descendants")
        probe_stages.descendants(run)
        positive = run.results["stages"]["descendants"]["positive"]
        self.assertEqual(positive["children"]["tool_subagent"], "unobserved")
        self.assertTrue(run.results["stages"]["descendants"]["pass"])
        self.assertEqual(positive["calls"], 3)

    def test_a_descendants_answer_after_detach_fails_the_stage(self):
        run, ledger, server = self.probe_run(stages=["descendants"])
        original = server.rpc

        def leaky(method, payload):
            if method == "descendants" and payload.get("session_id") not in server.roots:
                return 200, dict(state="ok", descendants=[], complete=True, reason=None), 1.0
            return original(method, payload)
        server.rpc = leaky
        probe_stages.setup(run, "descendants")
        probe_stages.descendants(run)
        result = run.results["stages"]["descendants"]
        self.assertFalse(result["pass"])
        self.assertIn("descendants_after_detach_not_refused", result["failures"])

    def test_not_enough_budget_refuses_before_any_request(self):
        run, ledger, server = self.probe_run(stages=["descendants"], qualification2=3)
        probe_stages.setup(run, "descendants")
        with self.assertRaises(safety.Refused):
            probe_stages.descendants(run)
        self.assertEqual(server.transmitted(), [])
        self.assertEqual(server.sessions, {})


class S33dStageTests(StageHarness):
    def test_config_edits_denied_ordinary_edit_allowed_and_env_probe_recorded(self):
        run, ledger, server = self.probe_run(stages=["s33d"])
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertTrue(result["pass"], result)
        project = run.server.project
        self.assertFalse((project / ".opencode" / "command" / "x.md").exists())
        self.assertFalse((project / "sub" / "opencode.json").exists())
        self.assertEqual((project / "agents" / "ok.txt").read_text(), "ok")
        self.assertEqual(result["config_edits"], dict(denied=2, files_absent=True))
        self.assertEqual(result["ordinary_edit"], dict(allowed=1, written=True))
        self.assertEqual(result["project_changes"], ["agents/", "agents/ok.txt"])
        self.assertEqual(result["env_command"]["marker"], "absent")
        self.assertEqual(result["image_generation"], "unobserved: no claude-agent image model in the isolated server")
        self.assertEqual(result["calls"], 2)
        self.assertNotIn("dummy-not-a-secret", json.dumps(run.results))

    def test_an_unstripped_env_is_recorded_not_failed(self):
        run, ledger, server = self.probe_run(stages=["s33d"], strip_env=False)
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertEqual(result["env_command"]["marker"], "present")
        self.assertTrue(result["pass"])

    def test_an_allowed_config_edit_fails_the_stage(self):
        run, ledger, server = self.probe_run(stages=["s33d"])
        original = server._permission
        server._permission = lambda session, resource: original(None, resource)  # as if the shim were not loaded
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertFalse(result["pass"])
        self.assertIn("config_edit_not_denied", result["failures"])


class SequencingTests(StageHarness):
    def test_failed_probes_stop_before_any_stage_request(self):
        run, ledger, server = self.probe_run()
        run.probes = lambda stage: ["guard_probe_reason"]
        run.snapshot_user_dirs = lambda: {}
        run.report_only_home = lambda: {}
        run.compare_user_dirs = lambda before, home: None
        results = run.run()
        self.assertIn("guard_probe_reason", results["blockers"][0])
        self.assertEqual(server.sessions, {})
        self.assertEqual(ledger.summary()["qualification-2"]["spent"], 0)

    def test_parse_stages(self):
        self.assertEqual(probe_stages.parse_stages("descendants,s33d"), ["descendants", "s33d"])
        for text in ("", "pilot", "descendants,descendants", "s33d,"):
            with self.assertRaises(safety.Refused, msg=text):
                probe_stages.parse_stages(text)


class ChatGPTTests(StageHarness):
    def connect(self, **knobs):
        run, ledger, server = self.probe_run(purpose="chatgpt", model="openai/gpt-test-mini#low", **knobs)
        announced, ticks = [], iter(range(0, 10000, 1))
        state_file = self.tmp / "iso" / "chatgpt-login.json"
        outcome = chatgpt.connect(run, state_file, timeout_s=10, poll_s=0, sleep=lambda s: None,
                                  clock=lambda: next(ticks), announce=announced.append)
        return run, ledger, server, announced, state_file, outcome

    def test_the_oauth_flow_announces_only_the_url_and_polls_to_completion(self):
        run, ledger, server, announced, state_file, outcome = self.connect()
        self.assertEqual((outcome["status"], outcome["polls"], outcome["connections"]), ("complete", 3, 1))
        state = json.loads(state_file.read_text())
        self.assertEqual(sorted(state), ["instructions", "mode", "url"])
        self.assertTrue(any(server.cancelled == [] and "https://auth.openai.com" in line for line in announced))
        for text in (json.dumps(run.results), state_file.read_text(), "\n".join(announced), json.dumps(outcome)):
            for secret in ("tok-not-real", "ref-not-real", "idt-not-real"):
                self.assertNotIn(secret, text)
        self.assertEqual(oct(state_file.stat().st_mode & 0o777), "0o600")

    def test_a_timeout_cancels_the_attempt_and_stops(self):
        with self.assertRaises(safety.Blocker):
            self.connect(oauth_result="never")

    def test_a_failed_sign_in_stops(self):
        with self.assertRaises(safety.Blocker):
            self.connect(oauth_result="failed")

    def test_an_unlisted_model_is_refused_after_sign_in(self):
        run, ledger, server, *_ = self.connect()
        run.chatgpt_model = "openai/gpt-other#low"
        with self.assertRaises(safety.Refused):
            chatgpt.select_model(run)
        self.assertIsNone(server.control["route"])
        self.assertEqual(server.transmitted(), [])

    def test_one_managed_request_on_the_chosen_model(self):
        run, ledger, server, *_ = self.connect()
        self.assertIsNone(server.control["route"])  # nothing may be sent before the model is checked
        chatgpt.select_model(run)
        self.assertEqual(server.control["route"], list(GPT))
        chatgpt.request(run)
        result = run.results["stages"]["chatgpt"]
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["request"]["kinds"], ["primary"])
        self.assertEqual(result["request"]["costs"], [None])
        self.assertEqual(result["native_rate_limit"], "absent")
        self.assertIn("unobserved", result["codex_window"])
        self.assertTrue(result["status_clean"])
        self.assertEqual(ledger.summary()["chatgpt"], dict(cap=4, spent=1, remaining=3, open_reservations=0))
        self.assertEqual(ledger.summary()["qualification-2"]["spent"], 0)
        self.assertEqual({(r["provider"], r["model"]) for r in server.transmitted()}, {("openai", "gpt-test-mini")})


class ModeRefusalTests(Temp):
    def test_modes_are_exclusive_and_routes_checked_before_anything_starts(self):
        ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        approved_ledger(ledger_path)
        target = self.private(self.tmp / "ok")
        base = ["--isolated-dir", str(target), "--call-ledger", str(ledger_path)]
        for extra in (["--stages", "descendants", "--chatgpt", "--chatgpt-model", "openai/gpt-test-mini#low"],
                      ["--stages", "bogus"], ["--chatgpt"], ["--chatgpt", "--chatgpt-model", "claude-agent/haiku#low"],
                      ["--chatgpt", "--chatgpt-model", "openai/gpt-test-mini"], ["--stages", "s33d", "--canary"],
                      ["--chatgpt", "--chatgpt-model", "openai/gpt-test-mini#low", "--canary"],
                      ["--chatgpt-model", "openai/gpt-test-mini#low"]):
            with self.subTest(extra=extra):
                self.assertEqual(live.main(base + extra), 2)
        self.assertEqual(list(target.iterdir()), [])


class ProbeStructureTests(StageHarness):
    def test_new_runs_shadow_no_method(self):
        for purpose, model in (("qualification-2", None), ("chatgpt", "openai/gpt-test-mini#low")):
            run, *_ = self.probe_run(purpose=purpose, model=model)
            cls = type(run)
            shadowed = [name for name in vars(run) if callable(getattr(cls, name, None)) or isinstance(getattr(cls, name, None), property)]
            self.assertEqual(shadowed, [], purpose)
            shutil.rmtree(self.tmp / "iso")
            shutil.rmtree(self.tmp / "state")


if __name__ == "__main__":
    unittest.main()
