"""The OpenRouter gateway mode with doubles: a fake launcher server (with the shim's descendants semantics), a routing
double for the gateway admission (its real routing side is F4/C3-4b's), the launcher's own accounting. No OpenCode,
no OpenRouter, no key."""
import json

from tests.qualification import gateway, safety
from tests.qualification.doubles import Temp
from tests.qualification.launcher_doubles import FakeLauncherHost

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="runbook part C")
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

    def start(self, project, settings, ledger, environ):
        knobs = dict(dict(models=[MODEL], cost=0.000024, trust_attach=True), **self.knobs)
        host = FakeLauncherHost(project, self.base / "workdir", settings, ledger, environ, **knobs)
        self.hosts.append(host)
        return host

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


class GatewayTests(Temp):
    def setUp(self):
        super().setUp()
        self.ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        self.ledger.set_cap("gateway", 8, "approved", approval=APPROVAL)

    def run_gateway(self, env, model="openrouter/vendor-small"):
        base = self.private(self.tmp / ("q%d" % len(list(self.tmp.glob("q*")))))
        return gateway.run_gateway(gateway.Gateway(base, self.ledger, "gw-test", env, model))

    def reservations(self):
        return [(e["reserved"], e["sent"]) for e in self.ledger.export()["entries"]
                if e["kind"] == "reservation" and e["purpose"] == "gateway"]

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
                     unlisted=dict(models=[]),
                     slash_id=dict(models=[dict(MODEL, id="vendor/small")]))
        reasons = dict(not_admitted="no_funded_organization", price="input_price_above_limit",
                       price_unknown="price_unknown", unlisted="model_not_listed", slash_id="model_id_not_normalizable")
        for name, change in cases.items():
            with self.subTest(name):
                env = Env(self.tmp, **change)
                result = self.run_gateway(env, "openrouter/vendor/small" if name == "slash_id" else "openrouter/vendor-small")
                self.assertIs(result["passed"], False)
                self.assertIn("Blocker", result["blocker"])
                self.assertIn(reasons[name], result["blocker"])
                self.assertEqual(env.hosts[0].prompts, 0)
                self.assertTrue(env.hosts[0].stopped)
        self.assertEqual(self.reservations(), [])

    def test_only_openrouter_models(self):
        with self.assertRaises(safety.Refused):
            gateway.Gateway(self.tmp, self.ledger, "gw", Env(self.tmp), "anthropic/claude")

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
