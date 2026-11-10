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

Hard ceiling (review B2), every part required:
- (a) the launch carries F4's per-launch output cap and step count (`opencode_launcher.launch_options` and
  `start_server(options=)`, PR #229), detected by capability; without them the mode refuses
  (`launch_options_unavailable`). The step count must cover every agent the server lists (`steps_not_universal`
  otherwise), and the run needs this session's launcher-probes result showing A1 passed on the same OpenCode
  (`--isolation-result`): only that shows the inline config is honoured;
- (b) the harness waits itself: on an idle timeout or an unreadable count it aborts every session of the attempt
  through the API, re-counts from the server and settles the actual count (more than reserved is recorded as sent);
  a count still unreadable settles the full reservation; the run fails either way;
- (c) the operator types `--key-limit-usd N` (0 < N <= 1.00) confirming the OpenRouter key's own limit, and the
  reserved estimate must fit in it;
- (d) the run aborts as soon as the running cost exceeds the reserved estimate (reserved requests times the
  model's price for INPUT_TOKENS in and the output cap out); the harness's own prompt is checked against INPUT_TOKENS
  (by bytes) before it is sent.

Stated honestly in the results: the only hard bound is the OpenRouter key's own limit (`hard_bound:
key_limit_usd`); the output cap, the step count and the cost abort are soft bounds that hold only when OpenCode
honours the inline config, as the A1 result shows (`soft_bounds`).

The launcher's own `launch` refuses an API-funded admission until these runs are observed (`API_FUNDING_QUALIFIED`),
so the harness drives the same steps with the launcher's module parts (`health`, `enforcement`, the worker profile,
`_wait_idle`, `account`) instead of calling `launch`.
"""
import json
import math
from pathlib import Path
import time

from tests.qualification import live_common
from tests.qualification.live_common import Reservation, ZeroCallHost, data, message_counts, zero_call_proof
from tests.qualification.managed_state import ManagedState, attempt_id
from tests.qualification.safety import Blocker, BudgetExceeded, PROMPT_TEXT, Refused

PROVIDER = "openrouter"
MAX_OUTPUT_TOKENS, STEPS, INPUT_TOKENS = 500, 2, 10_000
KEY_LIMIT_MAX = 1.0
ISOLATION_MAX_AGE_S = 12 * 3600
ISOLATION_PROBES = ("A1", "A2", "A3", "A4")
IDLE_S, POLL_S = 900, 1.0
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
    return dict(expected_calls=2, reserved=POSITIVE_CALLS, servers=1, processes=3, output_cap=MAX_OUTPUT_TOKENS,
                steps=STEPS, ceiling="reserved estimate in micro-dollars, at most --key-limit-usd (<= 1.00)",
                processes_note="opencode --version, `opencode serve` and the shim's routing child; routing runs in process",
                wall_time="about 2 min plus the interactive OpenRouter sign-in and the receipt lookup")


def check_model(entry):
    """Reasons the listed model may not be used (empty when it may). The launcher's accounting normalizes messages
    with the adapter's name pattern, which has no `/`: an id such as `vendor/model` would fail accounting after the
    calls were spent, so it is refused first (`model_id_not_normalizable`, reported for F4)."""
    from runtime.adapters import opencode as adapter
    if entry is None:
        return ["model_not_listed"]
    if not isinstance(entry.get("id"), str) or not adapter.MODEL_ID.fullmatch(entry["id"]):
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


def prompt_tokens(text):
    """An upper bound on the tokens of the harness's own prompt: its UTF-8 bytes (a token is at least one byte)."""
    return len(text.encode("utf-8"))


def read_isolation(path, now=None):
    """The launcher-probes result of this session, or `Refused` with a fixed reason: A1, A2, A3 and A4 all passed,
    no blockers, cleanup verified, on the qualified OpenCode version, written today and at most ISOLATION_MAX_AGE_S
    ago. A1 shows the inline config's plugin, password and flags honoured; it does not show the step count or the
    output cap honoured (those are observed by the gateway run itself)."""
    from tests.qualification.safety import QUALIFIED_OPENCODE
    now = time.time() if now is None else now
    if not path:
        raise Refused("--isolation-result is required: the launcher-probes result of this session (A1 passed)")
    try:
        file = Path(path)
        value = json.loads(file.read_text())
        age = now - file.stat().st_mtime
    except (OSError, ValueError):
        raise Refused("isolation_result_unreadable: --isolation-result cannot be read") from None
    meta = value.get("_meta") if isinstance(value, dict) else None
    probes = (value.get("probes") if isinstance(value, dict) else None) or {}
    a1 = probes.get("A1") or {}
    if not isinstance(meta, dict) or meta.get("mode") != "launcher-probes":
        raise Refused("isolation_result_wrong_mode: --isolation-result is not a launcher-probes result")
    failed = [probe for probe in ISOLATION_PROBES if (probes.get(probe) or {}).get("passed") is not True]
    if failed:
        raise Refused("isolation_probes_not_passed: %s did not pass in --isolation-result" % ", ".join(failed))
    if value.get("blockers") != []:
        raise Refused("isolation_run_blocked: --isolation-result records blockers")
    if (value.get("cleanup") or {}).get("verified_by_harness") is not True:
        raise Refused("isolation_cleanup_unverified: --isolation-result's cleanup was not verified")
    if a1.get("server_version") != QUALIFIED_OPENCODE:
        raise Refused("isolation_version_differs: --isolation-result's server is not OpenCode %s" % QUALIFIED_OPENCODE)
    if meta.get("date") != time.strftime("%Y-%m-%d", time.localtime(now)) or not 0 <= age <= ISOLATION_MAX_AGE_S:
        raise Refused("isolation_result_stale: --isolation-result is not from this session (today, at most 12 h old)")
    return dict(file=file.name, server_version=a1["server_version"])


def launch_options_supported():
    """F4's per-launch options, by capability (never by version): `launch_options` and `start_server(options=)`."""
    import inspect
    from runtime import opencode_launcher
    return callable(getattr(opencode_launcher, "launch_options", None)) and \
        "options" in inspect.signature(opencode_launcher.start_server).parameters


def key_limit(value):
    """The operator's typed OpenRouter key limit in dollars, or `Refused`: required, finite, above 0, at most 1.00."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float("nan")
    if not (0 < number <= KEY_LIMIT_MAX):
        raise Refused("--key-limit-usd must confirm the OpenRouter key's own limit: above 0 and at most 1.00")
    return number


def estimate_micros(entry, calls=POSITIVE_CALLS, output_tokens=MAX_OUTPUT_TOKENS):
    """The reserved estimate in micro-dollars: a price in $/M tokens is micro-dollars per token."""
    found = price(entry)
    return None if found is None else int(math.ceil(calls * (found[0] * INPUT_TOKENS + found[1] * output_tokens)))


class Ceiling(Exception):
    """The attempt was aborted (idle timeout, unreadable count or cost above the estimate)."""


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

    def __init__(self, base, ledger, run_id, env, model_ref, key_limit_usd=None, endpoint=None, isolation_result=None):
        self.base, self.ledger, self.run_id, self.env = base, ledger, run_id, env
        self.isolation = read_isolation(isolation_result)
        provider, model, variant = live_common.parse_model(model_ref, variant_required=False)
        if provider != PROVIDER:
            raise Refused("the gateway mode allows only openrouter/MODEL")
        if not launch_options_supported():
            raise Refused("launch_options_unavailable: the launcher carries no per-launch output cap and step count "
                          "(F4, PR #229)")
        self.key_limit = key_limit(key_limit_usd)
        if not isinstance(endpoint, str) or not endpoint:
            raise Refused("--gateway-endpoint is required: the launch pins the OpenRouter endpoint")
        from runtime import opencode_launcher
        try:
            self.options = opencode_launcher.launch_options(provider, model, endpoint=endpoint,
                                                            max_output_tokens=MAX_OUTPUT_TOKENS, steps=STEPS)
        except ValueError:
            raise Refused("the gateway's launch options cannot be expressed (model or --gateway-endpoint)") from None
        self.route = (provider, model, variant)
        self.clock = getattr(env, "clock", time.monotonic)
        self.estimate = None
        self.results = dict(positive=None, negative=None, bounds=dict(
            hard_bound="key_limit_usd", key_limit_usd=self.key_limit, soft_bounds=["output_cap", "steps", "cost_abort"],
            output_cap=MAX_OUTPUT_TOKENS, steps=STEPS, input_limit=INPUT_TOKENS,
            input_note="the harness's own prompt is checked; OpenCode's system prompt and tool schemas are outside it",
            inline_config_honoured="plugin, password and flags per A1 result %s; agents.*.steps and body.max_tokens are "
                                   "not shown by A1" % self.isolation["file"],
            soft_bounds_status="unobserved until the live gateway run shows them (a session stopping at the step cap, "
                               "output stopped within max_tokens): see positive.observed"))
        self.host, self.checks = None, {}

    def run(self):
        state = ManagedState(self.base / "gateway-state", self.route, ACCOUNT, pool=POOL, billing_class="gateway")
        state.create()
        routing = self.env.routing(state)
        self.host = self.env.start(state.project, state.settings, state.ledger, self.env.environ(), options=self.options)
        try:
            self.check_server(state)
            self.results["signin"] = self.env.await_signin(self.host)
            entry = live_common.listed_model(self.host, PROVIDER, self.route[1], self.env.sleep)
            reasons = check_model(entry)
            self.results["model"] = dict(reasons=reasons, price=price(entry))
            if reasons:
                raise Blocker("the gateway model is refused: %s" % ", ".join(reasons))
            self.estimate = estimate_micros(entry)
            self.results["estimate_micros"] = self.estimate
            if self.estimate > self.key_limit * 1_000_000:
                raise Blocker("key_limit_below_estimate: the reserved estimate exceeds the typed key limit")
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

    def check_server(self, state):
        """Before any sign-in or reservation: the same OpenCode version as the A1 result, and the step count on every
        agent the server lists (a session or its children may run any of them), else `steps_not_universal`. The rule
        is the launcher's `uncapped_agents`: an agent's steps come from the server's own listing when it reports them;
        otherwise from the inline config the launcher builds, and the results say so."""
        from runtime import opencode_launcher
        status, info = self.host.request("GET", "/api/info")
        version = info.get("version") if status == 200 and isinstance(info, dict) else None
        if version != self.isolation["server_version"]:
            raise Blocker("server_version_differs: not the OpenCode the A1 result was taken on")
        status, body = self.host.request("GET", "/api/agent")
        listed = data(body) if status == 200 else None
        config = opencode_launcher.server_config(state.settings, state.ledger, "plugin", self.options).get("agents") or {}
        missing = opencode_launcher.uncapped_agents(listed, config, STEPS)  # the launcher's own rule
        unreadable = missing == [opencode_launcher.LISTING_UNREADABLE]
        names = None if unreadable else sorted(agent.get("id") if isinstance(agent.get("id"), str) else agent["name"]
                                                for agent in listed)
        if not unreadable and all("steps" in agent for agent in listed):
            source = "server"
        else:
            source = "config: the agent listing reports no steps, so the launcher's inline config is read instead"
        capped = [] if unreadable else [name for name in names if name not in missing]
        self.results["bounds"]["agents"] = dict(listed=names, capped=capped, steps_source=source)
        if missing:
            raise Blocker("steps_not_universal: the step count does not cover every agent (%s)" % ", ".join(missing))

    def observed_bounds(self, tree):
        """What the run itself shows of the soft bounds; never assumed. `observed` is True only when a bound visibly
        stopped something (a session at the step cap, output stopped by length within max_tokens); `violated` when a
        session went past the step cap or a message's output past max_tokens (then the run fails)."""
        sessions, outputs, length_stops = [], [], 0
        for sid in tree:
            status, body = self.host.request("GET", "/api/session/%s/message" % sid, query={"order": "asc"})
            page = data(body) if status == 200 else None
            if not isinstance(page, list):
                return dict(steps=dict(observed=False, reason="unreadable"), max_tokens=dict(observed=False, reason="unreadable"),
                            violated=None)
            turns = [m for m in page if isinstance(m, dict) and m.get("type") == "assistant"]
            sessions.append(len(turns))
            for message in turns:
                output = (message.get("tokens") or {}).get("output")
                if type(output) is int:
                    outputs.append(output)
                length_stops += message.get("finish") == "length"
        steps_hit = any(count == STEPS for count in sessions)
        over_steps, over_tokens = any(count > STEPS for count in sessions), any(o > MAX_OUTPUT_TOKENS for o in outputs)
        return dict(steps=dict(observed=steps_hit and not over_steps, reason=None if steps_hit else "cap_not_reached",
                               turns_per_session=sessions),
                    max_tokens=dict(observed=bool(length_stops) and not over_tokens and bool(outputs),
                                    reason=None if length_stops else "cap_not_reached", max_output=max(outputs or [0])),
                    violated=over_steps or over_tokens)

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
        checks = {}
        facts = self.results["positive"] = dict(checks=checks, passed=False)  # kept when the run stops early
        answer = opencode_launcher.health(self.host)
        checks["health_ok"] = answer is not None
        checks["profile_enforced"] = answer is not None and opencode_launcher.enforcement(self.host, answer) == "enforced"
        if not all(checks.values()):
            raise Blocker("the launcher's server is not healthy and enforced")
        if prompt_tokens(PROMPT_TEXT) > INPUT_TOKENS:
            raise Blocker("prompt_above_input_limit: the harness's prompt exceeds %d tokens" % INPUT_TOKENS)
        attempt, token = self.admitted(state, routing, "positive")
        reservation = Reservation(self.ledger, "gateway", "gateway positive: root, API child, continuation",
                                  POSITIVE_CALLS, self.run_id)
        root = None
        try:
            checks["attached"] = self.attach(attempt, token)
            if not checks["attached"]:
                raise Blocker("routing.attach refused the gateway attempt")
            root = self.session(self.host, token["session_id"])
            began = int(time.time() * 1000)
            self.host.request("POST", "/api/session/%s/prompt" % root, dict(text=PROMPT_TEXT, id=token["message_id"]))
            child = self.session(self.host, parent=root, title="gateway api child")
            self.host.request("POST", "/api/session/%s/prompt" % child, dict(text=PROMPT_TEXT))
            facts["running_cost_micros"] = self.watch(root, began)
            facts["observed"] = self.observed_bounds(live_common.session_tree(self.host, root))
            facts["sent"] = reservation.settle(message_counts(self.host, live_common.session_tree(self.host, root)))
        except Ceiling as stop:
            facts["sent"] = self.settle_aborted(reservation, root)
            raise Blocker("gateway attempt aborted: %s" % stop) from None
        finally:
            if reservation.settled is None:
                reservation.settle_unknown()
            facts["sent"] = reservation.settled
        checks["soft_bounds_not_violated"] = facts["observed"]["violated"] is False
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
        facts["passed"] = all(checks.values())
        return facts

    def watch(self, root, began, timeout=IDLE_S):
        """Wait until the attempt is idle, aborting it on a timeout, an unreadable count or a running cost above the
        reserved estimate (review B2). Returns the running cost in micro-dollars."""
        deadline = self.clock() + timeout
        while True:
            tree = live_common.session_tree(self.host, root)
            cost, readable = self.running_cost(tree)
            if not readable:
                raise Ceiling("count_unreadable")
            if cost > self.estimate:
                raise Ceiling("cost_ceiling: %d micro-dollars above the estimate %d" % (cost, self.estimate))
            if self.idle(root, tree, began):
                return cost
            if self.clock() > deadline:
                raise Ceiling("idle_timeout")
            self.env.sleep(POLL_S)

    def running_cost(self, tree):
        total = 0
        for sid in tree:
            status, body = self.host.request("GET", "/api/session/%s/message" % sid, query={"order": "asc"})
            page = data(body) if status == 200 else None
            if not isinstance(page, list):
                return None, False
            for message in page:
                cost = message.get("cost") if isinstance(message, dict) and message.get("type") == "assistant" else None
                if type(cost) in (int, float):
                    total += int(math.ceil(cost * 1_000_000))
        return total, True

    def idle(self, root, tree, began):
        status, active = self.host.request("GET", "/api/session/active")
        if status != 200:
            return False
        listed = json.dumps(active)
        status, body = self.host.request("GET", "/api/session/%s/message" % root, query={"order": "asc"})
        done = any(isinstance(m, dict) and m.get("type") == "idle" and ((m.get("time") or {}).get("created") or 0) >= began
                   for m in (data(body) if status == 200 else None) or [])
        return done and not any(sid in listed for sid in tree)

    def settle_aborted(self, reservation, root):
        """Abort every session of the attempt, re-count from the server (bounded) and settle the actual count; an
        unreadable count settles the full reservation, never less than may have been sent."""
        tree = live_common.session_tree(self.host, root) if root else []
        for sid in tree:
            try:
                self.host.request("POST", "/api/session/%s/abort" % sid, {})
            except Exception:  # noqa: BLE001 - every session is tried; the re-count decides the settlement
                pass
        counts = None
        for _ in range(3):
            counts = message_counts(self.host, tree) if tree else None
            if counts and counts["readable"]:
                break
            self.env.sleep(POLL_S)
        try:
            return reservation.settle(counts if counts and counts["readable"] else None)
        except BudgetExceeded:
            return reservation.settled

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
