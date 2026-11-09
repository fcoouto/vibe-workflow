"""The ChatGPT route probe under the durable purpose `chatgpt` (at most 4 calls; its own run, never with Claude).

OpenCode 2.2.6 offers "Sign in with ChatGPT" as the `openai` integration's OAuth method `chatgpt-token-sharing`
(read from the binary: `POST /api/integration/openai/connect/oauth {methodID}` returns `{attemptID, mode: "auto",
url, instructions}`; the server listens on a random 127.0.0.1 port for `/auth/callback`; `GET .../oauth/{attemptID}`
is polled until its status is no longer `pending`; the scope must include token sharing). The user completes the
sign-in in their own browser; the login lives only in the isolated server's directory and is deleted with it. The
harness writes only the URL, mode and instructions to a state file and to stdout, never a token.

Codex rate limits are NOT read: the repository has no standalone reader of `account/rateLimits/read` (only a
fixture and a host bridge that receives limits from the Codex app), so the before/after window is unobserved here.
"""
import json
import os
import time

from tests.qualification import probe_stages
from tests.qualification.safety import Blocker, Refused, check_route, parse_route

PURPOSE = "chatgpt"
INTEGRATION = "openai"
METHOD_ID = "chatgpt-token-sharing"
DONE = ("complete", "completed", "connected", "success", "succeeded")
# Reasoning variants from cheapest up. The openai plugin drops maxTokens, so output length is not capped: when the
# operator names only the model, its lowest listed variant is used.
VARIANT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh")
CODEX_WINDOW = "unobserved: the repository has no standalone Codex rate-limit reader (see the qualification report)"


def connect(run, state_file, timeout_s=900, poll_s=2, sleep=time.sleep, clock=time.monotonic, announce=print):
    """Start "Sign in with ChatGPT", announce the URL, and poll (bounded) until it completes."""
    _, info, _ = run.server.request("GET", "/api/integration/%s" % INTEGRATION)
    methods = [m for m in (run.data(info) or {}).get("methods") or [] if m.get("id") == METHOD_ID and m.get("type") == "oauth"]
    if not methods:
        raise Blocker("the openai integration offers no 'Sign in with ChatGPT' OAuth method")
    status, started, _ = run.server.request("POST", "/api/integration/%s/connect/oauth" % INTEGRATION, {"methodID": METHOD_ID})
    attempt = run.data(started) or {}
    if status != 200 or not attempt.get("attemptID") or not attempt.get("url"):
        raise Blocker("the ChatGPT sign-in did not start")
    public = dict(url=attempt["url"], mode=attempt.get("mode"), instructions=attempt.get("instructions"))
    descriptor = os.open(state_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps(public, indent=1) + "\n")
    announce("Sign in with ChatGPT in your browser: %s" % public["url"])
    if public["instructions"]:
        announce(str(public["instructions"]))
    path = "/api/integration/%s/connect/oauth/%s" % (INTEGRATION, attempt["attemptID"])
    deadline, polls = clock() + timeout_s, 0
    while True:
        polls += 1
        _, body, _ = run.server.request("GET", path)
        state = run.redact(run.data(body) or {}).get("status")  # anything credential-like is redacted and remembered
        if state != "pending":
            break
        if clock() >= deadline:
            run.server.request("DELETE", path)
            raise Blocker("the ChatGPT sign-in was not completed within %d s" % timeout_s)
        sleep(poll_s)
    if state not in DONE:
        raise Blocker("the ChatGPT sign-in ended with status %s" % (state if isinstance(state, str) else "unknown"))
    _, info, _ = run.server.request("GET", "/api/integration/%s" % INTEGRATION)
    connections = len((run.data(info) or {}).get("connections") or [])
    if not connections:
        raise Blocker("no ChatGPT connection is listed after the sign-in")
    run.server.request("POST", "/api/location/reload")
    outcome = dict(status=state, polls=polls, connections=connections, method=METHOD_ID, mode=public["mode"])
    run.note(PURPOSE, "oauth", outcome)
    return outcome


def select_model(run):
    """The operator's `--chatgpt-model` must be an openai model and variant the login lists; it becomes the run's
    only route (and the guard's), with another listed variant for the guard probe."""
    _, models, _ = run.server.request("GET", "/api/model")
    listed = [m for m in (run.data(models) or []) if isinstance(m, dict)]
    ref = run.chatgpt_model or ""
    if "#" not in ref:
        ref += "#" + lowest_variant(listed, ref)
    route = check_route(PURPOSE, parse_route(ref), listed)
    model = next(m for m in listed if m.get("providerID") == "openai" and m.get("id") == route[1])
    variants = variants_of(model)
    others = [v for v in variants if v != route[2]]
    if not others:
        raise Refused("the guard probe needs a second listed variant of the model")
    run.route, run.guard_variant = route, others[0]
    run.server.write_control(route=list(route))
    run.note(PURPOSE, "model", dict(listed=True, variants=len(variants),
                                    openai_models=sum(1 for m in listed if m.get("providerID") == "openai")))
    return route


def variants_of(model):
    return [v if isinstance(v, str) else (v or {}).get("id") for v in (model or {}).get("variants") or []]


def lowest_variant(listed, ref):
    provider, _, name = ref.partition("/")
    model = next((m for m in listed if m.get("providerID") == provider and m.get("id") == name), None)
    known = [v for v in VARIANT_ORDER if v in variants_of(model)]
    if not known:
        raise Refused("cannot tell the model's lowest variant: pass openai/MODEL#VARIANT")
    return known[0]


def request(run):
    """One tiny prompt through the full managed handshake; accounting, `record complete`, a clean `status`."""
    s, failures = PURPOSE, []
    if run.ledger.remaining(PURPOSE) < 1:
        raise Refused("the chatgpt purpose has no call left")
    a = probe_stages.managed(run, s, run.run_id + "-gpt")
    probe_stages.attach(run, a)
    root = a["session"]
    run.create_session(root, title="qual chatgpt")  # titled: OpenCode sends no title request
    mark, began = len(run.server.hooks()), probe_stages.now_ms()
    token = run.expect(s, "chatgpt: one managed request", 1)
    run.prompt(root, a["message"])
    run.wait_idle(root, began)
    sent = run.done(token)
    transmitted = [r for r in run.server.hooks()[mark:] if r.get("decision") == "transmit"]
    status, answer = probe_stages.ask_descendants(run, a)
    ok = status == 200 and (answer or {}).get("state") == "ok"
    read = run.account_settled(root, 0, shim_descendants=answer if ok else None, cross_check=True)
    result = read["result"]
    requests = (result["usage"] or {}).get("requests") or []
    rate = result.get("rate_limit")
    settlement, detached = probe_stages.close(run, a, result["usage"])
    clean = probe_stages.status_clean(run)
    observed = dict(sent=sent, kinds=[r["kind"] for r in requests], costs=[r.get("cost_usd_micros") for r in requests],
                    tokens=[r.get("tokens") for r in requests], transmitted_kinds=sorted(r.get("kind") for r in transmitted),
                    unknown=sorted(result["unknown"]), title=(result.get("title") or {}).get("state"),
                    descendants=dict(http=status, complete=(answer or {}).get("complete")), settlement=settlement,
                    detached=detached)
    for condition, reason in ((sent == 1 and observed["kinds"] == ["primary"], "not_one_primary_request"),
                              (all(c is None for c in observed["costs"]), "cost_not_null"),
                              (settlement == "window" and detached, "not_settled"), (clean, "status_not_clean")):
        if not condition:
            failures.append(reason)
    run.note(s, "request", observed)
    run.note(s, "native_rate_limit", "absent" if rate is None else dict(keys=sorted(rate), units="fractions (0-1)"))
    run.note(s, "codex_window", CODEX_WINDOW)
    run.note(s, "status_clean", clean)
    run.note(s, "failures", failures)
    run.note(s, "pass", not failures)


def run(run_):
    """The whole chatgpt run: server, sign-in, model check, authority, the plugin-chain proof, one request."""
    run_.start(PURPOSE)
    connect(run_, run_.base / "chatgpt-login.json", timeout_s=run_.chatgpt_timeout)
    select_model(run_)
    if run_.ledger.remaining(PURPOSE) < 3:  # two probe exposures plus the request
        raise Refused("the chatgpt purpose needs 3 calls left (2 probe exposures and 1 request)")
    answer, _ = run_.routing("set", authority_required=True)
    status, health, _ = run_.server.rpc("health", {})
    run_.note(PURPOSE, "setup", dict(authority_required=answer.get("state"), health=(health or {}).get("state")))
    if status != 200 or (health or {}).get("state") != "ok":
        raise Blocker("routing.health was not ok")
    failures = run_.probes(PURPOSE)
    if failures:
        raise Blocker("the recorder/shim/recorder chain is not proven: %s" % ", ".join(failures))
    request(run_)
