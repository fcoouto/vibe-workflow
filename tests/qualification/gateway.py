"""`gateway` (purpose `gateway`): the OpenRouter positive and negative descendants runs through a server the managed
launcher starts (live-session runbook part C; S3.6, docs/smart-routing-qualification.md#stage-descendants).

The OpenRouter key is entered by the user in OpenCode's own sign-in under the run's temporary XDG_DATA_HOME. The
harness never reads, copies or stores it: it only polls the server's integration listing for a connection (labels go
to the redactor's secrets in memory). Settings never hold it.

- Positive run (reserves 3: the root, its API child and one tool continuation): `prepare` must admit the gateway route
  (else a blocker before any reservation), `dispatch`, `routing.attach`, the derived root and an API child
  (`parentID`), idle, the launcher's own accounting with `routing.descendants` (cross-check on), then every request's
  cost must be known and the total equal the operator's OpenRouter receipt (micro-dollars, one per request of
  rounding), `record complete` with a dollar settlement, `detach`, `descendants` refused afterwards, `status` clean.
- Negative run (0 calls, behind the zero-call wrapper): the derived root is created before `attach`; `descendants`
  must answer `complete: false` with `root_creation_unobserved` and accounting must leave the cost unknown.
- Model rule: listed under `openrouter`, priced at most $1/M input and $4/M output in the listing (unknown is refused).

The launcher's own `launch` refuses an API-funded admission until these runs are observed (`API_FUNDING_QUALIFIED`),
so the harness drives the same steps with the launcher's module parts (`health`, `enforcement`, the worker profile,
`_wait_idle`, `account`) instead of calling `launch`.
"""
import json
import time

from tests.qualification import live_common
from tests.qualification.live_common import Reservation, ZeroCallHost, data, message_counts, zero_call_proof
from tests.qualification.managed_state import ManagedState, attempt_id
from tests.qualification.safety import Blocker, BudgetExceeded, PROMPT_TEXT, Refused

PROVIDER = "openrouter"
POOL, ACCOUNT = "openrouter", "openrouter:gateway-qual"
MAX_INPUT_PER_M, MAX_OUTPUT_PER_M = 1.0, 4.0
POSITIVE_CALLS = 3
SIGNIN_S = 900


def price(entry):
    """`(input, output)` dollars per million from the untiered `cost` entry, or None when missing."""
    for cost in (entry or {}).get("cost") or []:
        if isinstance(cost, dict) and not cost.get("tier") and all(type(cost.get(k)) in (int, float) for k in ("input", "output")):
            return float(cost["input"]), float(cost["output"])
    return None


def plan():
    return dict(expected_calls=2, reserved=POSITIVE_CALLS, servers=1, processes=3,
                processes_note="opencode --version, `opencode serve` and the shim's routing child; routing runs in process",
                wall_time="about 2 min plus the interactive OpenRouter sign-in and the receipt lookup")


def check_model(entry):
    """Reasons the listed model may not be used (empty when it may). The launcher's accounting normalizes messages
    with the adapter's name pattern, which has no `/`: an id such as `vendor/model` would fail accounting after the
    calls were spent, so it is refused first (`model_id_not_normalizable`, reported for F4)."""
    from runtime.adapters import opencode as adapter
    if entry is None:
        return ["model_not_listed"]
    if not isinstance(entry.get("id"), str) or not adapter.NAME.fullmatch(entry["id"]):
        return ["model_id_not_normalizable"]
    found = price(entry)
    if found is None:
        return ["price_unknown"]
    reasons = []
    if found[0] > MAX_INPUT_PER_M:
        reasons.append("input_price_above_limit")
    if found[1] > MAX_OUTPUT_PER_M:
        reasons.append("output_price_above_limit")
    return reasons


def await_connection(host, redact, sleep, clock=time.monotonic, timeout=SIGNIN_S, announce=print, data_dir=None):
    """Wait (bounded) until the server lists an OpenRouter connection the user made with OpenCode's own sign-in."""
    announce("Sign in to OpenRouter with OpenCode's own sign-in, using XDG_DATA_HOME=%s (enter the $1-limited key "
             "there; this harness never reads it). Waiting up to %d s." % (data_dir, timeout))
    deadline, polls = clock() + timeout, 0
    while True:
        polls += 1
        status, body = host.request("GET", "/api/integration/%s" % PROVIDER)
        listed = (data(body) or {}).get("connections") if status == 200 and isinstance(data(body), dict) else None
        for item in listed or []:
            label = item.get("label") if isinstance(item, dict) else None
            if isinstance(label, str) and len(label.strip()) >= 3:
                redact.add_secret(label.strip())
        if listed:
            return dict(connections=len(listed), polls=polls)
        if clock() >= deadline:
            raise Blocker("no OpenRouter connection was listed within %d s" % timeout)
        sleep(2)


class ServiceRouting:
    """The live routing side: the managed service on the gateway state, under the state's static route source."""

    def __init__(self, state):
        self.state = state

    def call(self, op, **body):
        service = self.state.service()
        try:
            with self.state.source_installed():
                return service.handle(json.dumps(dict(v=1, op=op, **body)).encode())
        finally:
            service.close()

    def prepare(self, assignment, job):
        return self.call("prepare", assignment=assignment, job=job)

    def dispatch(self, attempt, route):
        from runtime import routing_launch
        return self.call("dispatch", attempt_id=attempt, request=dict(
            provider=route[0], model=route[1], variant=route[2], harness=routing_launch.shim_harness(route[0]),
            kind="primary", funding_pool=POOL))

    def record(self, event):
        return self.call("record", event=event)

    def status(self):
        return self.call("status")


def status_clean(status):
    ledger = (status or {}).get("ledger") or {}
    reconcile = ledger.get("reconcile") or {}
    return (status or {}).get("state") == "ok" and ledger.get("open_attempts") == 0 \
        and ledger.get("reconcile_hint") is None and not (reconcile.get("window_debits") or {}).get("count") \
        and not (ledger.get("cost_unknown") or {}).get("ids")


class Gateway:
    """`env` supplies `start(project, settings, ledger, environ)`, `environ()` (temporary HOME and data dir),
    `await_signin(host)`, `routing(state)` (a ServiceRouting-like object), `receipt(requests)` (the operator's
    OpenRouter receipt total in micro-dollars, or None), `balance()` (the cached OpenRouter status balance, or None)
    and `sleep`."""

    def __init__(self, base, ledger, run_id, env, model_ref):
        self.base, self.ledger, self.run_id, self.env = base, ledger, run_id, env
        provider, model, variant = live_common.parse_model(model_ref, variant_required=False)
        if provider != PROVIDER:
            raise Refused("the gateway mode allows only openrouter/MODEL")
        self.route = (provider, model, variant)
        self.results = dict(positive=None, negative=None)
        self.host, self.checks = None, {}

    def run(self):
        state = ManagedState(self.base / "gateway-state", self.route, ACCOUNT, pool=POOL, billing_class="gateway")
        state.create()
        routing = self.env.routing(state)
        self.host = self.env.start(state.project, state.settings, state.ledger, self.env.environ())
        try:
            self.results["signin"] = self.env.await_signin(self.host)
            entry = live_common.listed_model(self.host, PROVIDER, self.route[1], self.env.sleep)
            reasons = check_model(entry)
            self.results["model"] = dict(reasons=reasons, price=price(entry))
            if reasons:
                raise Blocker("the gateway model is refused: %s" % ", ".join(reasons))
            if self.route[2] is None:
                variants = live_common.variants_of(entry)
                self.route = (PROVIDER, self.route[1], variants[0] if variants else None)
            self.results["balance_before"] = self.env.balance()
            self.results["positive"] = self.positive(state, routing)
            self.results["negative"] = self.negative(state, routing)
            self.results["balance_after"] = self.env.balance()
        finally:
            self.host.stop()
        self.results["passed"] = bool(self.results["positive"] and self.results["positive"]["passed"]
                                      and self.results["negative"] and self.results["negative"]["passed"])
        return self.results

    def admitted(self, state, routing, label):
        attempt = attempt_id("gw-" + label)
        assignment, job = state.assignment(attempt, PROMPT_TEXT)
        assignment["choice"] = dict(state.choice("api"))
        answer = routing.prepare(assignment, job)
        admission = answer.get("admission") or {}
        if answer.get("state") != "admitted" or admission.get("billing_class") != "gateway":
            raise Blocker("the gateway route was not admitted (%s); nothing was reserved or sent" % (
                answer.get("reason") or admission.get("billing_class")))
        token = routing.dispatch(attempt, self.route)
        if token.get("state") != "dispatched":
            raise Blocker("dispatch refused: %s" % token.get("reason"))
        return attempt, token

    def attach(self, attempt, token):
        from runtime import opencode_launcher
        model = "%s/%s#%s" % (self.route[0], self.route[1], self.route[2] or "none")
        status, body = self.host.rpc("attach", dict(session_id=token["session_id"], attempt_id=attempt,
                                                    authority=token["authority"], funding_pool=POOL, model=model,
                                                    deny_actions=list(opencode_launcher.DENY_ACTIONS)))
        return status == 200 and isinstance(body, dict) and body.get("state") == "attached"

    def session(self, host, session_id=None, parent=None, title="gateway root"):
        from runtime import opencode_launcher
        body = dict(title=title, permissions=opencode_launcher.WORKER_RULES, location=dict(directory=self.host.directory),
                    model=dict(id=self.route[1], providerID=self.route[0], variant=self.route[2]))
        if session_id:
            body["id"] = session_id
        if parent:
            body["parentID"] = parent
        status, created = host.request("POST", "/api/session", body)
        sid = (data(created) or {}).get("id") if status == 200 and isinstance(data(created), dict) else None
        if not isinstance(sid, str):
            raise Blocker("a gateway session was not created")
        return sid

    def positive(self, state, routing):
        from runtime import opencode_launcher
        checks, facts = {}, {}
        answer = opencode_launcher.health(self.host)
        checks["health_ok"] = answer is not None
        checks["profile_enforced"] = answer is not None and opencode_launcher.enforcement(self.host, answer) == "enforced"
        if not all(checks.values()):
            raise Blocker("the launcher's server is not healthy and enforced")
        attempt, token = self.admitted(state, routing, "positive")
        reservation = Reservation(self.ledger, "gateway", "gateway positive: root, API child, continuation",
                                  POSITIVE_CALLS, self.run_id)
        try:
            checks["attached"] = self.attach(attempt, token)
            if not checks["attached"]:
                raise Blocker("routing.attach refused the gateway attempt")
            root = self.session(self.host, token["session_id"])
            began = int(time.time() * 1000)
            self.host.request("POST", "/api/session/%s/prompt" % root, dict(text=PROMPT_TEXT, id=token["message_id"]))
            child = self.session(self.host, parent=root, title="gateway api child")
            self.host.request("POST", "/api/session/%s/prompt" % child, dict(text=PROMPT_TEXT))
            opencode_launcher._wait_idle(self.host, root, began, 900)
            facts["sent"] = reservation.settle(message_counts(self.host, [root, child]))
        finally:
            if reservation.settled is None:
                reservation.settle_unknown()
        read = opencode_launcher.account(self.host, root, funding="api", attempt=attempt, authority=token["authority"],
                                         route=dict(provider=self.route[0], model=self.route[1], variant=self.route[2]))
        result, usage = read["result"], read["result"].get("usage") or {}
        requests = usage.get("requests") or []
        costs = [request.get("cost_usd_micros") for request in requests]
        unknown = sorted(result.get("unknown") or [])
        receipt = self.env.receipt(len(requests))
        facts.update(children=read["children"], requests=len(requests), costs=costs, unknown=unknown, receipt=receipt)
        checks["descendants_complete"] = read["children"] >= 1 and not [u for u in unknown if u.startswith("shim_")]
        checks["costs_known"] = bool(costs) and all(type(cost) is int for cost in costs) and not unknown
        checks["receipt_matches"] = receipt is not None and checks["costs_known"] and abs(sum(costs) - receipt) <= len(costs)
        recorded = routing.record(dict(event_id=attempt + "-complete", attempt_id=attempt, kind="complete",
                                       stage="worker", usage=usage))
        settlement = recorded.get("settlement") or {}
        facts["settlement"] = dict(state=recorded.get("state"), kind=settlement.get("kind"), reason=recorded.get("reason"))
        checks["dollar_settlement"] = recorded.get("state") == "recorded" and settlement.get("kind") not in (None, "window")
        status, body = self.host.rpc("detach", dict(session_id=root, attempt_id=attempt, authority=token["authority"]))
        checks["detached"] = status == 200 and (body or {}).get("state") == "detached"
        status, body = self.host.rpc("descendants", dict(session_id=root, attempt_id=attempt, authority=token["authority"]))
        checks["descendants_refused_after_detach"] = not (status == 200 and (body or {}).get("state") == "ok")
        checks["status_clean"] = status_clean(routing.status())
        return dict(checks=checks, passed=all(checks.values()), **facts)

    def negative(self, state, routing):
        from runtime import opencode_launcher
        wrapped, checks, facts = ZeroCallHost(self.host), {}, {}
        attempt, token = self.admitted(state, routing, "negative")
        root = self.session(wrapped, token["session_id"], title="gateway negative root")  # before attach: unobserved
        checks["attached"] = self.attach(attempt, token)
        status, shim = self.host.rpc("descendants", dict(session_id=root, attempt_id=attempt, authority=token["authority"]))
        shim = shim if isinstance(shim, dict) else {}
        facts["descendants"] = dict(http=status, complete=shim.get("complete"), reason=shim.get("reason"))
        checks["root_creation_unobserved"] = shim.get("complete") is False and shim.get("reason") == "root_creation_unobserved"
        read = opencode_launcher.account(wrapped, root, funding="api", attempt=attempt, authority=token["authority"])
        usage = read["result"].get("usage") or {}
        facts["unknown"] = sorted(read["result"].get("unknown") or [])
        checks["cost_unknown"] = bool(facts["unknown"])
        recorded = routing.record(dict(event_id=attempt + "-complete", attempt_id=attempt, kind="complete",
                                       stage="worker", usage=usage))
        facts["record"] = dict(state=recorded.get("state"), reason=recorded.get("reason"))
        status, body = self.host.rpc("detach", dict(session_id=root, attempt_id=attempt, authority=token["authority"]))
        checks["detached"] = status == 200 and (body or {}).get("state") == "detached"
        proof = zero_call_proof(wrapped, message_counts(wrapped, [root]))
        facts["zero_call_proof"] = proof
        checks["zero_calls_proven"] = proof["proven"]
        return dict(checks=checks, passed=all(checks.values()), **facts)


def run_gateway(gateway):
    try:
        return gateway.run()
    except (Blocker, BudgetExceeded, Refused, live_common.ZeroCall) as error:
        return dict(gateway.results, passed=False, blocker="%s: %s" % (type(error).__name__, error))
