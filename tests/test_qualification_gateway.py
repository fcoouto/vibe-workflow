"""The OpenRouter gateway mode with doubles: a fake launcher server (with the shim's descendants semantics), a routing
double for the gateway admission (its real routing side is F4/C3-4b's), the launcher's own accounting. No OpenCode,
no OpenRouter, no key."""
import builtins
import contextlib
import inspect
import json
import os
from pathlib import Path
from unittest import mock

from runtime import opencode_launcher
from tests.qualification import gateway, safety
from tests.qualification.doubles import Temp
from tests.qualification.launcher_doubles import FakeLauncherHost

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="runbook part C")
KEY = dict(key_limit_usd="1.00", endpoint="vendor")


def isolation_file(directory, passed=True, version="2.2.6", date=None, mode="launcher-probes", others=True,
                   blockers=(), cleanup=True):
    """A launcher-probes result as live_modes.py writes it: A1 (outcome, server version), A2-A4, blockers, cleanup."""
    import time
    path = Path(directory) / ("isolation-%d.json" % len(list(Path(directory).glob("isolation-*.json"))))
    probes = dict(A1=dict(passed=passed, server_version=version), **{p: dict(passed=others) for p in ("A2", "A3", "A4")})
    path.write_text(json.dumps(dict(_meta=dict(mode=mode, date=date or time.strftime("%Y-%m-%d")), probes=probes,
                                    blockers=list(blockers), cleanup=dict(verified_by_harness=cleanup))))
    return str(path)


@contextlib.contextmanager
def without_launch_options():
    """The launcher as it was before F4 (#229): no `launch_options`, `start_server` without `options`."""
    def start_server(checkout, *, settings_path, ledger_path, opencode="opencode", environ=None, timeout=30):
        raise AssertionError("tests never start a server")
    with mock.patch.object(opencode_launcher, "launch_options", None), \
            mock.patch.object(opencode_launcher, "start_server", start_server):
        yield


@contextlib.contextmanager
def launch_options():
    """Kept for callers: the real F4 launcher now provides the capability, so nothing is patched."""
    yield


MODEL = dict(providerID="openrouter", id="vendor-small", variants=["none"], cost=[dict(input=0.5, output=2.0)])


class FakeRouting:
    def __init__(self, admitted=True):
        self.calls, self.admitted, self.attempts = [], admitted, {}

    def prepare(self, assignment, job):
        self.calls.append("prepare")
        if not self.admitted:
            return dict(state="blocked", reason="no_funded_organization")
        self.attempts[assignment["attempt_id"]] = len(self.attempts)
        return dict(state="admitted", admission=dict(billing_class="gateway", attempt_id=assignment["attempt_id"]))

    def dispatch(self, attempt, route):
        self.calls.append("dispatch")
        index = self.attempts[attempt]
        return dict(state="dispatched", authority="T" * 43, session_id="ses_%032x" % (index + 1),
                    message_id="msg_%032x" % (index + 1))

    def record(self, event):
        self.calls.append("record:" + event["kind"])
        return dict(state="recorded", settlement=dict(kind="dollar"))

    def status(self):
        return dict(state="ok", ledger=dict(open_attempts=0, reconcile_hint=None, reconcile={}, cost_unknown=dict(ids=[])))


class Env:
    def __init__(self, base, routing=None, receipt=lambda n: 24 * n, **knobs):
        self.base, self.knobs, self.hosts, self.fake_routing, self.receipts = base, knobs, [], routing or FakeRouting(), receipt
        self.signins = 0

    def start(self, project, settings, ledger, environ, options=None):
        knobs = dict(dict(models=[MODEL], cost=0.000024, trust_attach=True), **self.knobs)
        host = FakeLauncherHost(project, self.base / "workdir", settings, ledger, environ, **knobs)
        self.started_options = options
        self.hosts.append(host)
        return host

    def clock(self):
        self.ticks = getattr(self, "ticks", 0) + 30
        return self.ticks

    def environ(self):
        return dict(HOME=str(self.base / "home"), XDG_DATA_HOME=str(self.base / "data"))

    def await_signin(self, host):
        self.signins += 1
        return dict(connections=1)

    def routing(self, state):
        return self.fake_routing

    def receipt(self, requests):
        return self.receipts(requests)

    def balance(self):
        return None

    def sleep(self, _seconds):
        return None


class GatewayBase(Temp):
    def setUp(self):
        super().setUp()
        self.ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        self.ledger.set_cap("gateway", 8, "approved", approval=APPROVAL)

    def key(self, **change):
        return dict(KEY, isolation_result=isolation_file(self.private(self.tmp / "results")), **change)

    def run_gateway(self, env, model="openrouter/vendor-small", **kwargs):
        base = self.private(self.tmp / ("q%d" % len(list(self.tmp.glob("q*")))))
        return gateway.run_gateway(gateway.Gateway(base, self.ledger, "gw-test", env, model, **self.key(**kwargs)))

    def reservations(self):
        return [(e["reserved"], e["sent"]) for e in self.ledger.export()["entries"]
                if e["kind"] == "reservation" and e["purpose"] == "gateway"]



class GatewayTests(GatewayBase):
    def test_positive_and_negative_runs_pass(self):
        env = Env(self.tmp)
        result = self.run_gateway(env)
        self.assertIs(result["passed"], True, json.dumps(result, indent=1)[:3000])
        positive, negative = result["positive"], result["negative"]
        self.assertEqual(positive["children"], 1)
        self.assertEqual(positive["costs"], [24, 24])
        self.assertEqual(positive["receipt"], 48)
        self.assertEqual(negative["descendants"]["reason"], "root_creation_unobserved")
        self.assertTrue(negative["zero_call_proof"]["proven"])
        self.assertEqual(env.hosts[0].prompts, 2)  # root and API child; the negative run sent nothing
        self.assertEqual(self.reservations(), [(3, 2)])
        self.assertTrue(env.hosts[0].stopped)
        self.assertEqual(env.signins, 1)

    def test_a_receipt_that_differs_fails(self):
        result = self.run_gateway(Env(self.tmp, receipt=lambda n: 30 * n))
        self.assertFalse(result["positive"]["checks"]["receipt_matches"])
        self.assertIs(result["passed"], False)
        self.assertFalse(self.run_gateway(Env(self.tmp, receipt=lambda n: None))["positive"]["checks"]["receipt_matches"])

    def test_unknown_costs_fail(self):
        result = self.run_gateway(Env(self.tmp, cost=0))
        self.assertFalse(result["positive"]["checks"]["costs_known"])
        self.assertIs(result["passed"], False)

    def test_refusals_before_any_reservation_or_request(self):
        cases = dict(not_admitted=dict(routing=FakeRouting(admitted=False)),
                     price=dict(models=[dict(MODEL, cost=[dict(input=3.0, output=15.0)])]),
                     price_unknown=dict(models=[dict(MODEL, cost=[])]),
                     unlisted=dict(models=[]))
        reasons = dict(not_admitted="no_funded_organization", price="input_price_above_limit",
                       price_unknown="price_unknown", unlisted="model_not_listed")
        for name, change in cases.items():
            with self.subTest(name):
                env = Env(self.tmp, **change)
                result = self.run_gateway(env)
                self.assertIs(result["passed"], False)
                self.assertIn("Blocker", result["blocker"])
                self.assertIn(reasons[name], result["blocker"])
                self.assertEqual(env.hosts[0].prompts, 0)
                self.assertTrue(env.hosts[0].stopped)
        self.assertEqual(self.reservations(), [])

    def test_only_openrouter_models(self):
        with self.assertRaises(safety.Refused):
            gateway.Gateway(self.tmp, self.ledger, "gw", Env(self.tmp), "anthropic/claude", **self.key())

    def test_the_signin_wait_is_bounded_and_records_no_label(self):
        host = FakeLauncherHost(self.tmp / "p", self.tmp / "w")
        redact, clock = safety.Redactor(), iter(range(0, 10_000, 10))
        with self.assertRaises(safety.Blocker):
            gateway.await_connection(host, redact, lambda _s: None, clock=lambda: next(clock), timeout=60,
                                     announce=lambda _text: None)
        host.connections["openrouter"] = [dict(label="alice@example.com key")]
        found = gateway.await_connection(host, redact, lambda _s: None, announce=lambda _text: None)
        self.assertEqual(found["connections"], 1)
        self.assertIn("alice@example.com key", redact.secrets)


class CeilingTests(GatewayBase):
    """Review B2: the gateway's hard ceiling (launch options, abort and actual count, key limit, running cost)."""

    def test_no_launch_options_capability_refuses_before_anything(self):
        with without_launch_options(), self.assertRaises(safety.Refused) as refused:
            gateway.Gateway(self.tmp, self.ledger, "gw", Env(self.tmp), "openrouter/vendor-small", **self.key())
        self.assertIn("launch_options_unavailable", str(refused.exception))

    def test_with_the_real_f4_launcher_the_launch_carries_the_output_cap_and_step_count(self):
        self.assertTrue(gateway.launch_options_supported())  # F4 (#229) is on main: detected by capability
        env = Env(self.tmp)
        result = self.run_gateway(env)
        self.assertTrue(result["passed"], json.dumps(result)[:1500])
        self.assertEqual(env.started_options, opencode_launcher.launch_options(
            "openrouter", "vendor-small", endpoint="vendor", max_output_tokens=gateway.MAX_OUTPUT_TOKENS,
            steps=gateway.STEPS))
        agents = result["bounds"]["agents"]
        self.assertEqual((agents["listed"], agents["capped"]), (["build"], [opencode_launcher.PRIMARY_AGENT]))
        self.assertTrue(agents["steps_source"].startswith("config:"))

    def test_the_bounds_are_stated_honestly(self):
        result = self.run_gateway(Env(self.tmp))
        bounds = result["bounds"]
        self.assertEqual((bounds["hard_bound"], bounds["soft_bounds"]), ("key_limit_usd", ["output_cap", "steps", "cost_abort"]))
        self.assertRegex(bounds["inline_config_honoured"], r"^plugin, password and flags per A1 result isolation-\d+\.json; "
                                                           r"agents\.\*\.steps and body\.max_tokens are not shown by A1$")
        self.assertTrue(bounds["soft_bounds_status"].startswith("unobserved until the live gateway run shows them"))
        observed = result["positive"]["observed"]  # reported by the run itself, never assumed
        self.assertEqual(observed["steps"], dict(observed=False, reason="cap_not_reached", turns_per_session=[1, 1]))
        self.assertEqual(observed["max_tokens"], dict(observed=False, reason="cap_not_reached", max_output=1))
        self.assertIs(observed["violated"], False)
        self.assertTrue(result["positive"]["checks"]["soft_bounds_not_violated"])
        self.assertEqual(bounds["key_limit_usd"], 1.0)

    def test_steps_must_cover_every_agent(self):
        for agents in (["build", "plan", "general"], []):
            with self.subTest(agents=agents):
                env = Env(self.tmp, agents=agents)
                result = self.run_gateway(env)
                self.assertIn("steps_not_universal", result["blocker"])
                self.assertEqual(env.hosts[0].prompts, 0)
                self.assertEqual(env.signins, 0)
        self.assertEqual(self.reservations(), [])

    def test_the_gateway_needs_this_sessions_passing_A1_result(self):
        results = self.private(self.tmp / "results")
        old = isolation_file(results)
        os.utime(old, (1, 1))
        cases = [(None, "isolation_result_unreadable"), (str(results / "missing.json"), "isolation_result_unreadable"),
                 (isolation_file(results, passed=False), "isolation_probes_not_passed: A1"),
                 (isolation_file(results, others=None), "isolation_probes_not_passed: A2, A3, A4"),
                 (isolation_file(results, blockers=["probes failed: A5"]), "isolation_run_blocked"),
                 (isolation_file(results, cleanup=False), "isolation_cleanup_unverified"),
                 (isolation_file(results, version="2.3.0"), "isolation_version_differs"),
                 (isolation_file(results, date="2026-01-01"), "isolation_result_stale"),
                 (isolation_file(results, mode="drill"), "isolation_result_wrong_mode"), (old, "isolation_result_stale")]
        for path, reason in cases:
            with self.subTest(path=path, reason=reason), self.assertRaises(safety.Refused) as refused:
                gateway.Gateway(self.tmp, self.ledger, "gw", Env(self.tmp), "openrouter/vendor-small",
                                **dict(KEY, isolation_result=path))
            self.assertIn(reason if path else "required", str(refused.exception))
        original = FakeLauncherHost.request

        def other_version(host, method, path, body=None, query=None):
            return (200, dict(version="2.2.7")) if path == "/api/info" else original(host, method, path, body=body, query=query)
        env = Env(self.tmp)
        with mock.patch.object(FakeLauncherHost, "request", other_version):
            result = self.run_gateway(env)
        self.assertIn("server_version_differs", result["blocker"])
        self.assertEqual(env.hosts[0].prompts, 0)

    def test_the_harness_prompt_is_checked_against_the_input_limit(self):
        self.assertLessEqual(gateway.prompt_tokens(gateway.PROMPT_TEXT), gateway.INPUT_TOKENS)
        env = Env(self.tmp)
        with mock.patch.object(gateway, "PROMPT_TEXT", "x" * (gateway.INPUT_TOKENS + 1)):
            result = self.run_gateway(env)
        self.assertIn("prompt_above_input_limit", result["blocker"])
        self.assertEqual(env.hosts[0].prompts, 0)
        self.assertEqual(self.reservations(), [])

    def test_a_vendor_slash_model_runs_end_to_end(self):
        env = Env(self.tmp, models=[dict(MODEL, id="vendor/small")])
        result = self.run_gateway(env, "openrouter/vendor/small")
        self.assertTrue(result["passed"], json.dumps(result)[:1500])
        self.assertEqual(env.started_options["model"], "vendor/small")

    def test_model_ids_follow_the_adapter_pattern(self):
        self.assertEqual(gateway.check_model(dict(MODEL, id="vendor/small")), [])
        for hostile in ("../etc", "vendor//small", "a/b/c", "/lead", "trail/", "x" * 129, "a b", "v\nx", ""):
            with self.subTest(hostile=hostile):
                self.assertEqual(gateway.check_model(dict(MODEL, id=hostile)), ["model_id_not_normalizable"])

    def test_the_key_limit_must_be_typed_and_at_most_one_dollar(self):
        for value in (None, "", "1.01", "0", "-1", "abc", "nan"):
            with self.subTest(value=value), self.assertRaises(safety.Refused):
                gateway.Gateway(self.tmp, self.ledger, "gw", Env(self.tmp), "openrouter/vendor-small",
                                **self.key(key_limit_usd=value))
        result = self.run_gateway(Env(self.tmp), key_limit_usd="0.000001")  # below the reserved estimate
        self.assertIn("key_limit_below_estimate", result["blocker"])
        self.assertEqual(self.reservations(), [])

    def test_an_idle_timeout_aborts_recounts_and_settles_the_actual_count(self):
        env = Env(self.tmp, hang=True)
        result = self.run_gateway(env)
        self.assertIs(result["passed"], False)
        self.assertIn("idle_timeout", result["blocker"])
        host = env.hosts[0]
        self.assertEqual(sorted(host.aborted), sorted(host.sessions))  # root and child aborted through the API
        self.assertEqual(self.reservations(), [(3, 2)])  # the recounted actual, not the reserved 3
        self.assertEqual(result["positive"]["sent"]["basis"], "assistant_messages")

    def test_more_sent_than_reserved_is_settled_as_sent(self):
        env = Env(self.tmp, hang=True, extra_assistants=1)
        result = self.run_gateway(env)
        self.assertIs(result["passed"], False)
        self.assertEqual(self.reservations(), [(3, 4)])

    def test_unreadable_counts_abort_and_fail(self):
        env = Env(self.tmp)
        original = FakeLauncherHost.request

        def failing(host, method, path, body=None, query=None):
            if path.endswith("/message") and host.prompts >= 2:
                return 500, None
            return original(host, method, path, body=body, query=query)
        with mock.patch.object(FakeLauncherHost, "request", failing):
            result = self.run_gateway(env)
        self.assertIs(result["passed"], False)
        self.assertIn("count_unreadable", result["blocker"])
        self.assertEqual(sorted(env.hosts[0].aborted), sorted(env.hosts[0].sessions))
        self.assertEqual(self.reservations(), [(3, 3)])  # unknown: never below what may have been sent

    def test_running_cost_above_the_estimate_aborts(self):
        env = Env(self.tmp, cost=0.01, hang=True)  # 10,000 micro-dollars a request against a 3-request estimate
        result = self.run_gateway(env)
        self.assertIs(result["passed"], False)
        self.assertIn("cost_ceiling", result["blocker"])
        self.assertTrue(env.hosts[0].aborted)
        self.assertEqual(self.reservations(), [(3, 2)])

    def test_the_gateway_never_opens_the_opencode_auth_file(self):
        opened = []
        real_open, real_os_open = builtins.open, os.open

        def guard(path):
            text = os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else ""
            if str(text).endswith(os.path.join("opencode", "auth.json")):
                opened.append(text)
                raise AssertionError("the OpenCode auth file was opened")

        def checked_open(file, *args, **kwargs):
            guard(file)
            return real_open(file, *args, **kwargs)

        def checked_os_open(path, *args, **kwargs):
            guard(path)
            return real_os_open(path, *args, **kwargs)
        env = Env(self.tmp)
        data = Path(env.environ()["XDG_DATA_HOME"]) / "opencode"
        data.mkdir(parents=True, exist_ok=True)
        (data / "auth.json").write_text('{"openrouter": {"key": "sk-or-never-read"}}')
        with mock.patch.object(builtins, "open", checked_open), mock.patch.object(os, "open", checked_os_open):
            result = self.run_gateway(env)
        self.assertTrue(result["passed"], json.dumps(result)[:1500])
        self.assertEqual(opened, [])
        self.assertNotIn("sk-or", json.dumps(result))


class SoftBoundTests(GatewayBase):
    def test_a_session_at_the_step_cap_is_observed(self):
        env = Env(self.tmp, extra_assistants=gateway.STEPS - 1)  # each prompt makes STEPS assistant turns
        result = self.run_gateway(env)
        self.assertIs(result["positive"]["observed"]["steps"]["observed"], True)
        self.assertEqual(self.reservations(), [(3, 4)])  # more than reserved: recorded as sent, the run fails
        self.assertIs(result["passed"], False)

    def test_a_violated_bound_fails_the_run(self):
        original = FakeLauncherHost.request

        def long_output(host, method, path, body=None, query=None):
            status, answer = original(host, method, path, body=body, query=query)
            if path.endswith("/message") and isinstance((answer or {}).get("data"), list):
                for message in answer["data"]:
                    if message.get("type") == "assistant":
                        message["tokens"] = dict(message["tokens"], output=gateway.MAX_OUTPUT_TOKENS + 1)
            return status, answer
        with mock.patch.object(FakeLauncherHost, "request", long_output):
            result = self.run_gateway(Env(self.tmp))
        self.assertIs(result["positive"]["observed"]["violated"], True)
        self.assertFalse(result["positive"]["checks"]["soft_bounds_not_violated"])
        self.assertIs(result["passed"], False)

    def test_steps_come_from_the_server_when_it_reports_them(self):
        env = Env(self.tmp, agents=[dict(name="build", steps=2), dict(name="plan", steps=2)])
        result = self.run_gateway(env)
        self.assertTrue(result["passed"], json.dumps(result)[:1500])
        self.assertEqual(result["bounds"]["agents"]["steps_source"], "server")
        env = Env(self.tmp, agents=[dict(name="build", steps=2), dict(name="plan", steps=50)])
        self.assertIn("steps_not_universal: the step count does not cover every agent (plan)", self.run_gateway(env)["blocker"])
        result = self.run_gateway(Env(self.tmp))  # names only: the config-based check, said so
        self.assertTrue(result["bounds"]["agents"]["steps_source"].startswith("config: the agent listing reports no steps"))
