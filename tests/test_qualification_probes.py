"""The live-probe stages without a server: the `qualification-2` and `chatgpt` purposes and their approval lines, the
route allowlist per mode (guard, CLI and harness), redaction of OAuth secrets, and the `descendants`, `s33d` and
`chatgpt` stages against a stand-in OpenCode server with the real scripts/routing.py on temporary stores."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr
import io
from unittest.mock import patch

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


class ApprovalCliTests(Temp):
    def test_set_cap_records_the_approval_and_refuses_without_one(self):
        path = self.private(self.tmp / "state") / "calls.jsonl"
        safety.CallLedger.initialize(path)
        base = ["--call-ledger", str(path), "--reason", "approved"]
        self.assertEqual(live.main(base + ["--set-cap", "chatgpt", "4"]), 2)
        self.assertEqual(live.main(base + ["--set-cap", "chatgpt", "4", "--approval", "user", "2026-11-09", "probe"]), 0)
        cap = [e for e in safety.CallLedger(path).export()["entries"] if e["kind"] == "cap"]
        self.assertEqual(cap[0]["approval"], dict(by="user", at="2026-11-09", scope="probe"))


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
        (self.tmp / "hooks.jsonl").touch()  # live, qual-pre has written the log before qual-post reads it
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
        self.assertEqual(result["config_edits"], dict(state="observed", files_absent=True, paths={
            ".opencode/command/x.md": dict(attempted=1, denied=1), "sub/opencode.json": dict(attempted=1, denied=1)}))
        self.assertEqual(result["ordinary_edit"], dict(state="observed", attempted=1, allowed=1, written=True))
        self.assertEqual({e["form"] for e in result["edit_resources"]}, {"relative"})
        self.assertEqual(sorted(e["target"] for e in result["edit_resources"]),
                         [".opencode/command/x.md", "agents/ok.txt", "sub/opencode.json"])
        self.assertEqual(result["project_changes"], ["agents/", "agents/ok.txt"])
        self.assertEqual(result["env_command"]["marker"], "absent")
        self.assertEqual(result["image_generation"], "unobserved: no claude-agent image model in the isolated server")
        self.assertEqual(result["calls"], 2)
        self.assertNotIn("dummy-not-a-secret", json.dumps(run.results))

    def test_project_absolute_resources_still_match_and_the_form_is_recorded(self):
        run, ledger, server = self.probe_run(stages=["s33d"], resource_form="absolute")
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["ordinary_edit"]["allowed"], 1)
        self.assertEqual({(e["form"], e["inside_project"]) for e in result["edit_resources"]}, {("absolute", True)})
        self.assertNotIn(str(run.server.project), json.dumps(result))  # the shape, never the path itself

    def test_no_config_edit_attempted_is_unobserved_not_a_pass(self):
        run, ledger, server = self.probe_run(stages=["s33d"], edit_attempts=("agents/ok.txt",))
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertEqual(result["config_edits"]["state"], "unobserved")
        self.assertFalse(result["pass"])
        self.assertIn("config_edit_unobserved", result["failures"])

    def test_one_config_path_attempted_and_denied_passes_and_the_other_is_reported(self):
        run, ledger, server = self.probe_run(stages=["s33d"], edit_attempts=("sub/opencode.json", "agents/ok.txt"))
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertTrue(result["pass"], result)
        self.assertEqual(result["config_edits"]["paths"][".opencode/command/x.md"], dict(attempted=0, denied=0))

    def test_no_ordinary_edit_attempted_is_unobserved_with_the_shapes_seen(self):
        run, ledger, server = self.probe_run(stages=["s33d"], edit_attempts=(".opencode/command/x.md", "elsewhere/y.txt"))
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertEqual(result["ordinary_edit"]["state"], "unobserved")
        self.assertIn("ordinary_edit_unobserved", result["failures"])
        self.assertIn(dict(form="relative", inside_project=None, config=False, target="other"), result["edit_resources"])

    def test_an_unstripped_env_is_recorded_not_failed(self):
        run, ledger, server = self.probe_run(stages=["s33d"], strip_env=False)
        probe_stages.setup(run, "s33d")
        probe_stages.s33d(run)
        result = run.results["stages"]["s33d"]
        self.assertEqual(result["env_command"]["marker"], "present")
        self.assertTrue(result["pass"])

    def test_an_allowed_config_edit_fails_the_stage(self):
        run, ledger, server = self.probe_run(stages=["s33d"], shim_denies_config=False)  # as if the shim did not deny
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

    def test_a_model_without_a_variant_gets_its_lowest_listed_variant(self):
        run, ledger, server, *_ = self.connect()
        run.chatgpt_model = "openai/gpt-test-mini"
        self.assertEqual(chatgpt.select_model(run), GPT)
        self.assertEqual(run.guard_variant, "medium")
        server.models = [dict(id="gpt-odd", providerID="openai", variants=["fast", "slow"])]
        run.chatgpt_model = "openai/gpt-odd"
        with self.assertRaises(safety.Refused):  # no known order: the operator must name the variant
            chatgpt.select_model(run)

    def test_prepare_admits_the_openai_explicit_choice_with_the_real_routing(self):
        run, ledger, server, *_ = self.connect()
        chatgpt.select_model(run)
        attempt = probe_stages.managed(run, "chatgpt", run.run_id + "-prep")
        self.assertTrue(attempt["session"].startswith("ses_"))
        status = run.routing("status")[0]["ledger"]
        self.assertEqual(status["open_attempts"], 1)
        self.assertEqual(run.request_facts()["harness"], "opencode provider openai")
        self.assertEqual(run.request_facts()["funding_pool"], "chatgpt-sub")

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


def gpt(model_id, variants=("low", "medium"), cost=None, provider="openai", **extra):
    """A listed model in OpenCode 2.2.6's `Model.Info` shape (from the binary; no live listing is recorded)."""
    entry = dict(id=model_id, modelID=model_id, providerID=provider, name=model_id,
                 variants=[dict(id=v) for v in variants], status="active", enabled=True,
                 capabilities=dict(tools=True, input=["text", "image"], output=["text"]),
                 cost=[] if cost is None else [dict(input=cost[0], output=cost[1], cache=dict(read=0, write=0))])
    entry.update(extra)
    return entry


class ChatGPTMethodTests(StageHarness):
    def connect(self, **knobs):
        self.connect_run = None
        original = self.probe_run

        def keep(*args, **kwargs):
            made = original(*args, **kwargs)
            self.connect_run = made[0]
            return made
        self.probe_run = keep
        try:
            return ChatGPTTests.connect(self, **knobs)
        finally:
            self.probe_run = original

    def test_the_sign_in_waits_for_the_integration_to_register(self):
        run, ledger, server, announced, state_file, outcome = self.connect(integration_pending=3)
        self.assertEqual(outcome["status"], "complete")
        self.assertEqual(outcome["offered_methods"], ["chatgpt-browser", "chatgpt-headless", "chatgpt-token-sharing"])

    def test_a_missing_method_blocks_and_records_what_was_offered(self):
        with self.assertRaises(safety.Blocker) as caught:
            self.connect(openai_methods=[dict(id="chatgpt-browser", type="oauth", label="Codex browser (legacy)")])
        self.assertIn("offered: chatgpt-browser (oauth)", str(caught.exception))

    def test_an_integration_that_never_registers_blocks_with_its_last_status(self):
        with self.assertRaises(safety.Blocker) as caught:
            self.connect(integration_pending=10 ** 6)
        self.assertIn("HTTP 404", str(caught.exception))

    def refused_sign_in(self, result, extra):
        for name in ("iso", "state"):  # each attempt gets a fresh ledger and dir
            shutil.rmtree(self.tmp / name, ignore_errors=True)
        with self.assertRaises(safety.Blocker) as caught:
            self.connect(oauth_result=result, oauth_extra=extra)
        run = self.connect_run
        text = str(caught.exception) + json.dumps(run.output(run.results))
        for secret in ("tok-not-real", "ref-not-real", "idt-not-real", "dev-code-not-real", "https://"):
            self.assertNotIn(secret, text)
        return str(caught.exception), run.results["stages"]["chatgpt"]["signin"]

    def test_a_refused_sign_in_keeps_its_reason(self):
        message, signin = self.refused_sign_in("failed", dict(message="subscription_sharing_user_not_eligible"))
        self.assertIn("failed: subscription_sharing_user_not_eligible", message)
        self.assertEqual(signin, dict(status="failed", error=dict(message="subscription_sharing_user_not_eligible"),
                                      keys=["access", "id_token", "message", "refresh", "status"]))
        message, signin = self.refused_sign_in("error", dict(code="subscription_sharing_user_not_eligible", reason="plan"))
        self.assertEqual(signin["error"], dict(code="subscription_sharing_user_not_eligible", reason="plan"))
        self.assertIn("code=subscription_sharing_user_not_eligible", message)

    def test_an_unknown_refusal_shape_records_only_key_names(self):
        message, signin = self.refused_sign_in("weird", dict(detail=dict(inner="x"), count=3))
        self.assertEqual(signin["error"], {})
        self.assertEqual(signin["keys"], ["access", "count", "detail", "id_token", "refresh", "status"])

    def test_secret_looking_refusal_fields_are_never_recorded(self):
        message, signin = self.refused_sign_in("failed", dict(
            message="see https://auth.openai.com/oauth?code=abc", error="bad tok-not-real", device_code="dev-code-not-real",
            reason="r" * 500))
        self.assertNotIn("message", signin["error"])  # a URL is dropped whole
        self.assertNotIn("device_code", signin["error"])
        self.assertEqual(len(signin["error"]["reason"]), 200)
        self.assertNotIn("tok-not-real", signin["error"]["error"])

    def test_the_registration_wait_uses_short_requests_and_a_total_deadline(self):
        run, ledger, server = self.probe_run(purpose="chatgpt", model=None)
        original, timeouts, ticks = server.request, [], iter(range(0, 10 ** 6, 7))

        def slow(method, path, body=None, query=None, timeout=60):
            timeouts.append(timeout)
            raise TimeoutError("no answer")
        server.request = slow
        status, methods, polls = chatgpt.wait_integration(run, "openai", chatgpt.METHOD_ID, sleep=lambda s: None,
                                                          clock=lambda: next(ticks))
        self.assertEqual((status, methods), (None, []))
        self.assertTrue(timeouts and max(timeouts) <= chatgpt.REQUEST_S, timeouts)
        self.assertLessEqual(len(timeouts), chatgpt.REGISTER_S // 7 + 2)
        server.request = original

    def test_the_methods_diagnostic_lists_without_signing_in_or_reserving(self):
        run, ledger, server = self.probe_run(purpose="chatgpt", model=None, integration_pending=2)
        run.chatgpt_methods = True
        chatgpt.run(run, sleep=lambda s: None)
        found = run.results["stages"]["chatgpt"]["methods"]
        self.assertEqual(found["openai"]["methods"], [dict(id="chatgpt-browser", type="oauth", label="Codex browser (legacy)"),
                                                      dict(id="chatgpt-headless", type="oauth", label="Codex device code (legacy)"),
                                                      dict(id="chatgpt-token-sharing", type="oauth", label="Sign in with ChatGPT")])
        self.assertEqual([p["http"] for p in found["openai"]["polls"]], [404, 200])
        self.assertEqual((found["integrations_count"], "integrations" in found), (4, False))  # openai only, plus a count
        self.assertEqual(run.results["mode"], "chatgpt-methods")
        self.assertEqual((server.oauth_started, server.transmitted()), ([], []))
        self.assertEqual([e for e in ledger.export()["entries"] if e["kind"] == "reserve"], [])
        self.assertNotIn("cred_", json.dumps(run.results))


UPSTREAM = dict(type="APIError", status=403, retryAfterMs=1500,
                message="Not eligible (subscription_sharing_user_not_eligible), see https://help.openai.com/x?t=tok-not-real",
                response=dict(body=json.dumps(dict(error=dict(code="subscription_sharing_user_not_eligible", type="invalid_request",
                                                              message="plan not eligible", access_token="tok-not-real",
                                                              param="reasoning.effort")))))


class ChatGPTRequestFactsTests(StageHarness):
    connect = ChatGPTTests.connect

    def requested(self, **knobs):
        run, ledger, server, *_ = self.connect(**knobs)
        run.redact.add_secret("tok-not-real")
        chatgpt.select_model(run)
        chatgpt.request(run)
        result = run.results["stages"]["chatgpt"]
        text = json.dumps(run.output(run.results))
        for secret in ("tok-not-real", "ref-not-real", "https://"):
            self.assertNotIn(secret, text)
        return run, ledger, server, result

    def test_an_upstream_error_is_recorded_and_classified(self):
        run, ledger, server, result = self.requested(upstream_error=UPSTREAM,
                                                     upstream_retry=dict(attempt=2, at=1, error=dict(type="RateLimit", message="slow")))
        self.assertIn("request_failed_upstream", result["failures"])
        self.assertNotIn("not_one_primary_request", result["failures"])
        self.assertEqual(result["upstream_error"], "subscription_sharing_user_not_eligible")
        facts = result["session_facts"]
        self.assertEqual(facts["messages_by_type"], dict(user=1, assistant=1, idle=1))
        self.assertTrue(facts["user_message"])
        assistant = facts["assistant"][0]
        self.assertEqual((assistant["finish"], assistant["time_completed"], assistant["tokens"], assistant["cost_present"]),
                         ("error", False, {}, False))
        self.assertEqual(assistant["model"], dict(providerID="openai", id="gpt-test-mini", variant="low"))
        error = assistant["error"]
        self.assertEqual((error["type"], error["status"], error["retry_after_ms"]), ("APIError", 403, 1500))
        self.assertIn("<url>", error["message"])
        self.assertEqual(error["body"]["fields"], dict(code="subscription_sharing_user_not_eligible", type="invalid_request",
                                                       message="plan not eligible", param="reasoning.effort"))
        self.assertIn("subscription_sharing_user_not_eligible", error["body"]["codes"])
        self.assertIn("access_token", error["body"]["keys"])
        self.assertEqual(assistant["retry"]["attempt"], 2)
        self.assertEqual(facts["idle"], [dict(outcome="failed", error_type="APIError")])
        self.assertEqual([e["type"] for e in facts["events"]], ["session.status", "session.error", "session.status"])
        self.assertEqual(facts["events"][0]["status"], dict(type="retry", attempt=1))
        self.assertEqual(facts["request_body_keys"], ["id", "text"])
        self.assertEqual((facts["variant"], facts["variants_listed"]), ("low", ["low", "medium"]))
        self.assertEqual(facts["wait"]["reason"], "idle record after the prompt and the session not active")
        self.assertEqual((facts["session"]["http"], facts["messages_http"]), (200, 200))
        self.assertEqual(ledger.summary()["chatgpt"]["spent"], 1)

    def test_a_normal_reply_records_the_same_facts_and_passes(self):
        run, ledger, server, result = self.requested()
        self.assertTrue(result["pass"], result)
        assistant = result["session_facts"]["assistant"][0]
        self.assertEqual((assistant["finish"], assistant["time_completed"], assistant["error"]), ("stop", True, None))
        self.assertTrue(assistant["tokens"] and all(type(v) is int for v in assistant["tokens"].values()))
        self.assertNotIn("upstream_error", result)

    def test_a_wait_that_never_ends_still_records_the_facts(self):
        run, ledger, server, *_ = self.connect()
        chatgpt.select_model(run)

        def stuck(session, after_ms, timeout=120):
            raise safety.Blocker("session did not become idle")
        run.wait_idle = stuck
        with self.assertRaises(safety.Blocker):
            chatgpt.request(run)
        facts = run.results["stages"]["chatgpt"]["session_facts"]
        self.assertEqual(facts["wait"]["reason"], "timeout: session did not become idle")
        self.assertEqual(facts["messages_by_type"]["user"], 1)


class ChatGPTAutoTests(StageHarness):
    connect = ChatGPTTests.connect

    def auto(self, models):
        for name in ("iso", "state"):  # a test may select more than once: each gets a fresh ledger and dir
            shutil.rmtree(self.tmp / name, ignore_errors=True)
        run, ledger, server, *_ = self.connect()
        server.openai_models = models
        run.chatgpt_model = "auto"
        printed = []
        reservations = lambda: [e for e in ledger.export()["entries"] if e["kind"] == "reserve"]  # noqa: E731
        announce = lambda line: printed.append((line, len(reservations())))  # noqa: E731
        return run, ledger, server, printed, announce, reservations

    def select(self, models):
        run, ledger, server, printed, announce, _ = self.auto(models)
        route = chatgpt.select_model(run, announce=announce)
        return run, server, route, printed

    def test_the_cheapest_catalog_cost_wins_over_a_name_hint(self):
        run, server, route, printed = self.select([gpt("gpt-b-mini", cost=(2, 8)), gpt("gpt-a", cost=(1, 4)),
                                                   gpt("gpt-c-nano")])
        self.assertEqual(route, ("openai", "gpt-a", "low"))
        self.assertEqual(run.guard_variant, "medium")
        self.assertEqual(server.control["route"], ["openai", "gpt-a", "low"])

    def test_without_costs_the_name_hint_decides_nano_then_mini_then_small(self):
        self.assertEqual(self.select([gpt("gpt-x"), gpt("gpt-x-mini"), gpt("gpt-x-nano")])[2][1], "gpt-x-nano")
        self.assertEqual(self.select([gpt("gpt-y-small"), gpt("gpt-y-mini")])[2][1], "gpt-y-mini")

    def test_zero_costs_are_unknown_and_ties_go_to_the_hint_then_the_id(self):
        self.assertEqual(self.select([gpt("gpt-b", cost=(0, 0)), gpt("gpt-z-small", cost=(0, 0))])[2][1], "gpt-z-small")
        self.assertEqual(self.select([gpt("gpt-b", cost=(1, 1)), gpt("gpt-a", cost=(1, 1))])[2][1], "gpt-a")
        self.assertEqual(self.select([gpt("gpt-b-mini", cost=(1, 1)), gpt("gpt-a", cost=(1, 1))])[2][1], "gpt-b-mini")

    def test_non_chat_non_openai_and_single_variant_models_are_excluded(self):
        models = [gpt("gpt-cheap", cost=(0.1, 0.1), provider="opencode"),
                  gpt("gpt-image-1", cost=(0.1, 0.1)), gpt("text-embedding-3-small", cost=(0.1, 0.1)),
                  gpt("whisper-1", cost=(0.1, 0.1)), gpt("gpt-4o-mini-tts", cost=(0.1, 0.1)),
                  gpt("gpt-4o-realtime-preview", cost=(0.1, 0.1)), gpt("omni-moderation-latest", cost=(0.1, 0.1)),
                  gpt("gpt-4o-mini-search-preview", cost=(0.1, 0.1)), gpt("gpt-typed", cost=(0.1, 0.1), type="image"),
                  gpt("gpt-voice", cost=(0.1, 0.1), capabilities=dict(tools=True, input=["audio"], output=["audio"])),
                  gpt("gpt-off", cost=(0.1, 0.1), enabled=False), gpt("gpt-old", cost=(0.1, 0.1), status="deprecated"),
                  gpt("gpt-one", variants=("low",), cost=(0.1, 0.1)), gpt("gpt-odd", variants=("fast", "slow"), cost=(0.1, 0.1)),
                  gpt("gpt-real", cost=(5, 5))]
        run, server, route, printed = self.select(models)
        self.assertEqual(route[1], "gpt-real")
        selection = run.results["stages"]["chatgpt"]["model_selection"]
        self.assertEqual(selection["candidates"], ["gpt-real"])
        self.assertEqual(selection["other_providers"], 1)
        self.assertEqual(sorted(selection["excluded"]), sorted(m["id"] for m in models[1:-1]))

    def test_selection_table(self):
        table = [  # (listed, expected pick)
            ([gpt("o4-mini-deep-research", cost=(0, 0)), gpt("o4-mini-high", cost=(0, 0))], "o4-mini-high"),
            ([gpt("computer-use-preview", cost=(0.1, 0.1)), gpt("gpt-5-mini", cost=(1, 1))], "gpt-5-mini"),
            ([gpt("gpt-5", cost=(1.25, 10)), gpt("gpt-5-mini", cost=(0.25, 2)), gpt("gpt-5-nano", cost=(0.05, 0.4))],
             "gpt-5-nano"),
            ([gpt("gpt-5.1-codex", cost=(0, 0)), gpt("gpt-5.1-codex-mini", cost=(0, 0))], "gpt-5.1-codex-mini"),
            ([gpt("gpt-5-nano"), gpt("gpt-expensive", cost=(50, 200))], "gpt-expensive"),  # a known cost always wins
        ]
        for listed, expected in table:
            with self.subTest(expected=expected):
                run, server, route, printed = self.select(listed)
                self.assertEqual(route[1], expected)
        excluded = run.results["stages"]["chatgpt"]["model_selection"]["excluded"]
        self.assertEqual(excluded, {})
        run, server, route, printed = self.select(table[0][0] + table[1][0])
        self.assertEqual(run.results["stages"]["chatgpt"]["model_selection"]["excluded"],
                         {"o4-mini-deep-research": "name", "computer-use-preview": "name"})

    def test_the_selection_is_announced_before_any_reservation_and_recorded(self):
        run, server, route, printed = self.select([gpt("gpt-a", cost=(1, 4)), gpt("gpt-b-nano", cost=(3, 4))])
        self.assertEqual(printed[0][1], 0)
        self.assertTrue(printed[0][0].startswith("chatgpt model selected: openai/gpt-a#low (rule: "), printed)
        selection = run.results["stages"]["chatgpt"]["model_selection"]
        self.assertEqual((selection["mode"], selection["selected"], selection["candidates"]),
                         ("auto", "openai/gpt-a#low", ["gpt-a", "gpt-b-nano"]))
        self.assertEqual(selection["rule"], chatgpt.AUTO_RULE)
        state = json.loads((self.tmp / "iso" / "chatgpt-login.json").read_text())
        self.assertEqual(state["selection"]["selected"], "openai/gpt-a#low")
        self.assertEqual(oct((self.tmp / "iso" / "chatgpt-login.json").stat().st_mode & 0o777), "0o600")

    def refused(self, models):
        run, ledger, server, printed, announce, reservations = self.auto(models)
        run.start, run.probes = (lambda stage: None), (lambda stage: [])
        server.oauth_pending_polls, server.oauth_polls, server.connected = 0, 0, False
        (self.tmp / "iso" / "chatgpt-login.json").unlink()  # the run signs in again and creates it afresh
        with self.assertRaises(safety.Refused) as caught:
            chatgpt.run(run, announce=announce)
        selection = run.results["stages"]["chatgpt"]["model_selection"]
        self.assertEqual((selection["mode"], selection["rule"], selection["selected"]), ("auto", chatgpt.AUTO_RULE, None))
        self.assertEqual(selection["candidates"], [m["id"] for m in models if m["id"] not in selection["excluded"]])
        text = json.dumps(run.output(run.results))
        for secret in ("tok-not-real", "ref-not-real", "idt-not-real"):
            self.assertNotIn(secret, text)
        self.assertEqual(reservations(), [])
        self.assertEqual(server.transmitted(), [])
        self.assertIsNone(server.control["route"])
        return str(caught.exception), printed

    def test_ambiguous_or_empty_candidates_refuse_before_any_reservation(self):
        message, printed = self.refused([gpt("gpt-x"), gpt("gpt-y")])
        self.assertIn("ambiguous", message)
        self.assertIn("gpt-x, gpt-y", message)
        self.assertTrue(any("candidates: gpt-x, gpt-y" in line for line, _ in printed), printed)
        message, _ = self.refused([gpt("gpt-x", cost=(0, 0)), gpt("gpt-y", cost=(0, 0))])
        self.assertIn("ambiguous", message)
        message, _ = self.refused([gpt("gpt-one", variants=("low",), cost=(1, 1)), gpt("gpt-image-1", cost=(1, 1))])
        self.assertIn("no candidate", message)

    def test_an_explicit_model_is_unchanged(self):
        run, ledger, server, printed, announce, _ = self.auto([gpt("gpt-a", cost=(1, 1)), gpt("gpt-test-mini")])
        run.chatgpt_model = "openai/gpt-test-mini"
        self.assertEqual(chatgpt.select_model(run, announce=announce), ("openai", "gpt-test-mini", "low"))
        self.assertEqual(run.results["stages"]["chatgpt"]["model_selection"]["mode"], "explicit")
        run.chatgpt_model = "openai/gpt-unlisted#low"
        with self.assertRaises(safety.Refused):
            chatgpt.select_model(run, announce=announce)


class ModeRefusalTests(Temp):
    def test_modes_are_exclusive_and_routes_checked_before_anything_starts(self):
        ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        approved_ledger(ledger_path)
        target = self.private(self.tmp / "ok")
        base = ["--isolated-dir", str(target), "--call-ledger", str(ledger_path)]
        for extra in (["--stages", "descendants", "--chatgpt", "--chatgpt-model", "openai/gpt-test-mini#low"],
                      ["--stages", "bogus"], ["--chatgpt"], ["--chatgpt", "--chatgpt-model", "claude-agent/haiku#low"],
                      ["--stages", "s33d", "--canary"],
                      ["--chatgpt", "--chatgpt-model", "openai/gpt-test-mini#low", "--canary"],
                      ["--chatgpt-model", "openai/gpt-test-mini#low"],
                      ["--chatgpt", "--chatgpt-model", "openai/gpt-test-mini#low", "--keep"]):
            with self.subTest(extra=extra), patch.object(live, "Run", side_effect=AssertionError("a run would start")), \
                    patch.object(live, "check_opencode_version", side_effect=AssertionError("opencode would start")):
                self.assertEqual(live.main(base + extra), 2)
        self.assertEqual(list(target.iterdir()), [])


    def test_auto_reaches_the_run_and_auto_with_a_variant_is_refused(self):
        ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        approved_ledger(ledger_path)
        target = self.private(self.tmp / "ok")
        base = ["--isolated-dir", str(target), "--call-ledger", str(ledger_path), "--chatgpt", "--chatgpt-model"]
        captured = {}

        class FakeRun:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                self.redact = safety.Redactor()

            def run(self):
                return dict(blockers=[])

            def output(self, results):
                return dict(results, cleanup=None, call_ledger=None)
        with patch.object(live, "Run", FakeRun), patch.object(live, "check_opencode_version", lambda env: None), \
                patch.object(live, "check_real_home", lambda: None):
            self.assertEqual(live.main(base + ["auto"]), 0)
        self.assertEqual(captured["chatgpt_model"], "auto")
        for ref in ("auto#low", "openai/auto#", "AUTO"):
            with patch.object(live, "Run", side_effect=AssertionError("a run would start")), \
                    patch.object(live, "check_opencode_version", side_effect=AssertionError("opencode would start")):
                self.assertEqual(live.main(base + [ref]), 2, ref)

    def test_a_redirected_home_refuses_every_live_mode_before_anything_starts(self):
        real = self.private(self.tmp / "real")
        with self.assertRaises(safety.Refused):
            safety.check_real_home(home=self.private(self.tmp / "fake"), passwd_home=real)
        self.assertEqual(safety.check_real_home(home=real, passwd_home=real), real.resolve())
        ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        approved_ledger(ledger_path)
        base = ["--isolated-dir", str(self.private(self.tmp / "ok")), "--call-ledger", str(ledger_path)]
        for extra in ([], ["--stages", "descendants"], ["--chatgpt", "--chatgpt-model", "auto"]):
            with self.subTest(extra=extra), patch.dict("os.environ", HOME=str(self.private(self.tmp / ("fake-%d" % len(extra))))), \
                    patch.object(live, "Run", side_effect=AssertionError("a run would start")), \
                    patch.object(live, "check_opencode_version", side_effect=AssertionError("opencode would start")):
                self.assertEqual(live.main(base + extra), 2)

    def test_the_methods_diagnostic_is_its_own_run(self):
        ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        approved_ledger(ledger_path)
        base = ["--isolated-dir", str(self.private(self.tmp / "ok")), "--call-ledger", str(ledger_path)]
        captured = {}

        class FakeRun:
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                self.redact = safety.Redactor()

            def run(self):
                captured["methods"] = self.chatgpt_methods
                return dict(blockers=[])

            def output(self, results):
                return dict(results, cleanup=None, call_ledger=None)
        with patch.object(live, "Run", FakeRun), patch.object(live, "check_opencode_version", lambda env: None), \
                patch.object(live, "check_real_home", lambda: None):
            self.assertEqual(live.main(base + ["--chatgpt-methods"]), 0)
        self.assertEqual((captured["purpose"], captured["methods"]), ("chatgpt", True))
        for extra in (["--chatgpt"], ["--stages", "s33d"], ["--canary"], ["--keep"], ["--chatgpt-model", "auto"]):
            with self.subTest(extra=extra), patch.object(live, "Run", side_effect=AssertionError("a run would start")), \
                    patch.object(live, "check_opencode_version", side_effect=AssertionError("opencode would start")):
                with redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(live.main(base + ["--chatgpt-methods"] + extra), 2)
                if extra in (["--chatgpt"], ["--keep"]):
                    self.assertIn("--chatgpt-methods", err.getvalue())

    def test_the_keep_warning_names_the_stored_logins(self):
        self.assertIn("ChatGPT", live.KEEP_WARNING)
        self.assertIn("refresh", live.KEEP_WARNING)


class WatchedHomeTests(Temp):
    def fake_home(self):
        home = self.private(self.tmp / "home")
        for name in ("auth.json", "config.toml", "logs_2.sqlite", "models_cache.json"):
            (self.private(home / ".codex") / name).write_text("x")
        deep = home / ".codex" / "sessions" / "a" / "b"
        deep.mkdir(parents=True)
        for index in range(30):
            (deep / ("s%d.jsonl" % index)).write_text("x")
        return home

    @staticmethod
    def touch(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("y")
        stamp = path.stat().st_mtime_ns + 5_000_000_000
        import os
        os.utime(path, ns=(stamp, stamp))

    def test_codex_credentials_and_config_fail_but_app_churn_is_reported(self):
        home = self.fake_home()
        before = safety.watched_home(home, max_entries=20)  # depth 1: the deep sessions tree is never walked
        self.touch(home / ".codex" / "logs_2.sqlite")
        self.touch(home / ".codex" / "models_cache.json")
        self.touch(home / ".codex" / "sessions" / "new")
        self.assertEqual(safety.watched_changes(before, safety.watched_home(home))[1], [])
        self.touch(home / ".codex" / "auth.json")
        self.touch(home / ".codex" / "chatgpt-token.json")
        changes, unexpected = safety.watched_changes(before, safety.watched_home(home))
        self.assertEqual(sorted(unexpected), [".codex/auth.json", ".codex/chatgpt-token.json"])
        self.assertEqual(changes[".codex"]["churn_count"], 2)

    def test_any_change_under_config_openai_fails(self):
        home = self.fake_home()
        before = safety.watched_home(home)
        self.touch(home / ".config" / "openai" / "anything")
        self.assertEqual(safety.watched_changes(before, safety.watched_home(home))[1], [".config/openai/anything"])

    def test_the_run_watches_codex_through_its_user_dir_check(self):
        home = self.fake_home()
        run = live.Run(self.private(self.tmp / "iso"), approved_ledger(self.private(self.tmp / "state") / "calls.jsonl"),
                       purpose="chatgpt", chatgpt_model="openai/gpt-test-mini#low")
        with patch.dict("os.environ", HOME=str(home)):
            before, report = run.snapshot_user_dirs(), run.report_only_home()
            self.touch(home / ".codex" / "config.toml")
            run.compare_user_dirs(before, report)
        self.assertIn("the user's ~/.codex credentials or config changed during the run", run.results["blockers"])
        self.assertEqual(run.results["user_dirs"]["codex"][".codex"]["unexpected"], [".codex/config.toml"])


class SessionOutputChurnTests(Temp):
    """OpenCode's own session tool-output stores under ~/.local/share/opencode are report-only, nothing else."""

    def tree(self):
        root = self.private(self.tmp / "data")
        for name in ("auth.json", "opencode.db", "storage/session/a.json", "shell/abc/sh_1.out", "tool-output/t1.txt",
                     "snapshot/x"):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("x")
        return root

    @staticmethod
    def touch(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("y")
        stamp = path.stat().st_mtime_ns + 5_000_000_000
        os.utime(path, ns=(stamp, stamp), follow_symlinks=False)

    def classify(self, root, before, session_output=True):
        return safety.classify_snapshots(before, safety.snapshot_tree(root), stores=session_output)

    def test_new_and_rewritten_tool_output_is_report_only(self):
        root = self.tree()
        before = safety.snapshot_tree(root)
        for name in ("shell/abc/sh_2.out", "shell/abc/sh_1.out", "shell/new/sh_3.out", "tool-output/t2.txt",
                     "tool-output/t1.txt"):
            self.touch(root / name)
        unexpected, churn, session = self.classify(root, before)
        self.assertEqual((unexpected, churn), ([], []))
        self.assertEqual(len(session), 6)  # five files and the new shell/new/ directory
        unexpected, _, session = self.classify(root, before, session_output=False)  # other OpenCode dirs: unchanged rule
        self.assertEqual((len(unexpected), session), (6, []))

    def test_everything_else_stays_unexpected(self):
        for name in ("auth.json", "storage/session/a.json", "snapshot/x", "plugins.json", "shell-evil/x", "tool-output.bak",
                     "shell", "x/shell/y", "toolOutput/t"):
            with self.subTest(name=name):
                shutil.rmtree(self.tmp / "data", ignore_errors=True)
                root = self.tree()
                before = safety.snapshot_tree(root)
                if (root / name).is_dir():
                    continue
                self.touch(root / name)
                unexpected, churn, session = self.classify(root, before)
                self.assertIn(name, unexpected)
                self.assertEqual(session, [])

    def test_deletions_and_symlinks_stay_unexpected(self):
        root = self.tree()
        before = safety.snapshot_tree(root)
        (root / "shell" / "abc" / "sh_1.out").unlink()
        os.symlink(root / "auth.json", root / "tool-output" / "link.txt")
        unexpected, _, session = self.classify(root, before)
        self.assertEqual(sorted(unexpected), ["shell/abc/sh_1.out", "tool-output/link.txt"])
        self.assertEqual(session, [])
        shutil.rmtree(self.tmp / "data")
        root = self.tree()
        before = safety.snapshot_tree(root)
        shutil.rmtree(root / "shell")
        elsewhere = self.private(self.tmp / "elsewhere")
        (elsewhere / "sh_9.out").write_text("z")
        os.symlink(elsewhere, root / "shell")  # the store replaced by a symlink: never followed, never tolerated
        unexpected, _, session = self.classify(root, before)
        self.assertIn("shell", unexpected)
        self.assertIn("shell/", unexpected)
        self.assertNotIn("shell/sh_9.out", unexpected + session)
        self.assertEqual(session, [])

    def test_names_with_dot_segments_are_never_report_only(self):
        diff = dict(added=["shell/../auth.json", "tool-output/./x", "shell//x"], removed=[], changed=[])
        self.assertEqual(safety.session_output(diff, {}, {}), [])

    def test_the_run_counts_session_output_without_names(self):
        home = self.private(self.tmp / "home")
        data = home / ".local" / "share" / "opencode"
        (data / "shell" / "abc").mkdir(parents=True)
        run = live.Run(self.private(self.tmp / "iso"), approved_ledger(self.private(self.tmp / "state") / "calls.jsonl"),
                       purpose="chatgpt", chatgpt_model="openai/gpt-test-mini#low")
        with patch.dict("os.environ", HOME=str(home)):
            before, report = run.snapshot_user_dirs(), run.report_only_home()
            self.touch(data / "shell" / "abc" / "sh_1.out")
            run.compare_user_dirs(before, report)
        self.assertEqual(run.results["blockers"], [])
        entry = run.results["user_dirs"]["opencode"][".local/share/opencode"]
        self.assertEqual((entry["unexpected_count"], entry["session_output_count"]), (0, 1))
        self.assertNotIn("sh_1.out", json.dumps(run.results))


class ProbeStructureTests(StageHarness):
    def test_new_runs_shadow_no_method(self):
        ledger = approved_ledger(self.private(self.tmp / "state") / "calls.jsonl")
        for purpose, model in (("qualification-2", None), ("chatgpt", "openai/gpt-test-mini#low")):
            run = live.Run(self.private(self.tmp / ("iso-" + purpose)), ledger, purpose=purpose,
                           stages=["descendants"] if purpose == "qualification-2" else None, chatgpt_model=model)
            cls = type(run)
            shadowed = [name for name in vars(run) if callable(getattr(cls, name, None)) or isinstance(getattr(cls, name, None), property)]
            self.assertEqual(shadowed, [], purpose)


if __name__ == "__main__":
    unittest.main()
