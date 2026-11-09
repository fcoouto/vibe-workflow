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
import math
import os
import re
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
STATE_NAME = "chatgpt-login.json"
# `--chatgpt-model auto` (the user delegated the choice): the listing exists only after the sign-in, so the harness
# picks one model by this fixed rule. The listing fields come from OpenCode 2.2.6's `Model.Info` schema in the binary
# (no live listing is recorded), so the name exclusions and the two-variant rule apply as well.
AUTO = "auto"
NON_CHAT = re.compile(r"image|embed|audio|transcri|tts|speech|realtime|moderation|search-preview|deep-research|"
                      r"computer-use|dall-e|whisper|sora", re.I)
HINTS = ("nano", "mini", "small")
AUTO_RULE = ("openai provider only; chat (type absent or chat, text in and out when listed, enabled, not deprecated, "
             "no image/embedding/audio/transcription/tts/realtime/moderation/search-preview/deep-research/computer-use name); at least two variants "
             "with one in the known order; cheapest untiered input+output catalog cost per million (missing or zero = "
             "unknown, last); then nano, mini, small in the id; then the id; lowest variant; refused when the pick has "
             "neither a cost nor a hint")
CODEX_WINDOW = "unobserved: the repository has no standalone Codex rate-limit reader (see the qualification report)"


REGISTER_S = 60  # the openai integration's plugins register its methods shortly after start-up


def methods_of(info):
    """An integration's methods as `{id, type, label}` only (no form, no connection, nothing credential-like)."""
    return [dict(id=m.get("id") if isinstance(m.get("id"), str) else None, type=m.get("type"),
                 label=str(m.get("label"))[:80] if m.get("label") is not None else None)
            for m in (info or {}).get("methods") or [] if isinstance(m, dict)]


def offered(methods):
    return ", ".join("%s (%s)" % (m["id"] or "-", m["type"]) for m in methods) or "none"


def wait_integration(run, integration, method_id, timeout_s=REGISTER_S, sleep=time.sleep, clock=time.monotonic):
    """Poll `GET /api/integration/<id>` (bounded) until it lists `method_id` as an OAuth method. Returns the last
    HTTP status, the sanitized methods and every change seen (status and method ids), for the record."""
    deadline, polls, last = clock() + timeout_s, [], None
    while True:
        status, info, _ = run.server.request("GET", "/api/integration/%s" % integration)
        methods = methods_of(run.data(info) if status == 200 else None)
        seen = (status, [m["id"] for m in methods])
        if seen != last:
            polls.append(dict(http=status, methods=seen[1]))
            last = seen
        if any(m["id"] == method_id and m["type"] == "oauth" for m in methods) or clock() >= deadline:
            return status, methods, polls
        sleep(0.5)


def connect(run, state_file, timeout_s=900, poll_s=2, sleep=time.sleep, clock=time.monotonic, announce=print):
    """Start "Sign in with ChatGPT", announce the URL, and poll (bounded) until it completes."""
    http, methods, polls = wait_integration(run, INTEGRATION, METHOD_ID, sleep=sleep, clock=clock)
    run.note(PURPOSE, "offered_methods", dict(http=http, methods=methods, polls=polls))
    if not any(m["id"] == METHOD_ID and m["type"] == "oauth" for m in methods):
        raise Blocker("the openai integration offers no 'Sign in with ChatGPT' OAuth method (%s; HTTP %d; offered: %s)"
                      % (METHOD_ID, http, offered(methods)))
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
    outcome = dict(status=state, polls=polls, connections=connections, method=METHOD_ID, mode=public["mode"],
                   offered_methods=[m["id"] for m in methods])
    run.note(PURPOSE, "oauth", outcome)
    return outcome


def catalog_cost(model):
    """The untiered input+output catalog cost per million, or None when missing or zero (a subscription login may
    report every cost as 0, which says nothing about which model is cheaper)."""
    entries = model.get("cost")
    entries = [entries] if isinstance(entries, dict) else [c for c in entries or [] if isinstance(c, dict)]
    base = [c for c in entries if not c.get("tier")] or entries
    totals = [c["input"] + c["output"] for c in base if all(type(c.get(k)) in (int, float) for k in ("input", "output"))]
    return min(totals) if totals and min(totals) > 0 else None


def exclusion(model):
    """Why an openai model is not an `auto` candidate, or None."""
    caps = model.get("capabilities") if isinstance(model.get("capabilities"), dict) else {}
    if model.get("type") not in (None, "chat"):
        return "type"
    if any(isinstance(caps.get(k), list) and "text" not in caps[k] for k in ("input", "output")):
        return "modality"
    if model.get("enabled") is False or model.get("status") == "deprecated":
        return "unavailable"
    if NON_CHAT.search(str(model.get("id"))) or NON_CHAT.search(str(model.get("name") or "")):
        return "name"
    variants = variants_of(model)
    if len(variants) < 2:
        return "variants"
    if not any(v in VARIANT_ORDER for v in variants):
        return "variant_order"
    return None


def hint(model_id):
    return next((rank for rank, word in enumerate(HINTS) if word in model_id.lower()), len(HINTS))


def auto_select(listed, announce, note):
    """`openai/<id>` by `AUTO_RULE` and the sanitized record of the choice (ids only), or `Refused` (the record
    is noted in the results either way)."""
    openai = [m for m in listed if m.get("providerID") == "openai" and isinstance(m.get("id"), str)]
    excluded = {m["id"]: exclusion(m) for m in openai if exclusion(m)}
    candidates = sorted((m for m in openai if m["id"] not in excluded),
                        key=lambda m: (catalog_cost(m) if catalog_cost(m) is not None else math.inf, hint(m["id"]), m["id"]))
    ids = [m["id"] for m in candidates]
    record = dict(mode=AUTO, rule=AUTO_RULE, candidates=ids, excluded=excluded,
                  other_providers=sum(1 for m in listed if m.get("providerID") != "openai"),
                  costs={m["id"]: catalog_cost(m) for m in candidates})
    if not candidates or (catalog_cost(candidates[0]) is None and hint(ids[0]) == len(HINTS)):
        note(PURPOSE, "model_selection", dict(record, selected=None))
        announce("chatgpt model candidates: %s" % (", ".join(ids) or "none"))
        reason = "no candidate" if not candidates else "the cheapest candidate is ambiguous (no cost, no name hint)"
        raise Refused("--chatgpt-model auto: %s; pass --chatgpt-model openai/MODEL; candidates: %s"
                      % (reason, ", ".join(ids) or "none"))
    return "openai/" + ids[0], record


def write_state(run, **fields):
    """Add fields to the sign-in state file (owner-only; never a token)."""
    path = run.base / STATE_NAME
    state = json.loads(path.read_text()) if path.exists() else {}
    state.update(fields)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(json.dumps(state, indent=1) + "\n")


def select_model(run, announce=print):
    """The operator's `--chatgpt-model` (or `auto`'s pick) must be an openai model and variant the login lists; it
    becomes the run's only route (and the guard's), with another listed variant for the guard probe. Nothing is
    reserved or sent before this returns."""
    _, models, _ = run.server.request("GET", "/api/model")
    listed = [m for m in (run.data(models) or []) if isinstance(m, dict)]
    ref = run.chatgpt_model or ""
    selection = dict(mode="explicit")
    if ref == AUTO:
        ref, selection = auto_select(listed, announce, run.note)
    if "#" not in ref:
        ref += "#" + lowest_variant(listed, ref)
    route = check_route(PURPOSE, parse_route(ref), listed)
    model = next(m for m in listed if m.get("providerID") == "openai" and m.get("id") == route[1])
    variants = variants_of(model)
    others = [v for v in variants if v != route[2]]
    if not others:
        raise Refused("the guard probe needs a second listed variant of the model")
    selection["selected"] = "%s/%s#%s" % route
    if selection["mode"] == AUTO:
        announce("chatgpt model selected: %s (rule: %s)" % (selection["selected"], AUTO_RULE))
        write_state(run, selection=dict(selected=selection["selected"], rule=AUTO_RULE, candidates=selection["candidates"]))
    run.note(PURPOSE, "model_selection", selection)
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


def diagnose(run, sleep=time.sleep, clock=time.monotonic):
    """`--chatgpt-methods` (0 calls): what the isolated server's integrations offer. No sign-in, no reservation, no
    model request; only ids, types and labels are recorded."""
    run.start(PURPOSE)
    http, methods, polls = wait_integration(run, INTEGRATION, METHOD_ID, sleep=sleep, clock=clock)
    _, info, _ = run.server.request("GET", "/api/integration/%s" % INTEGRATION)
    _, listing, _ = run.server.request("GET", "/api/integration")
    integrations = {i["id"]: ["%s (%s)" % (m["id"] or "-", m["type"]) for m in methods_of(i)]
                    for i in (run.data(listing) or []) if isinstance(i, dict) and isinstance(i.get("id"), str)}
    connections = (run.data(info) or {}).get("connections") if http == 200 else None
    run.note(PURPOSE, "methods", dict(openai=dict(http=http, methods=methods, polls=polls,
                                                  connections=len(connections) if isinstance(connections, list) else None),
                                      integrations=integrations))
    run.note(PURPOSE, "sign_in_method_listed", any(m["id"] == METHOD_ID and m["type"] == "oauth" for m in methods))


def run(run_, announce=print, sleep=time.sleep):
    if getattr(run_, "chatgpt_methods", False):
        return diagnose(run_, sleep=sleep)
    """The whole chatgpt run: server, sign-in, model check, authority, the plugin-chain proof, one request."""
    run_.start(PURPOSE)
    connect(run_, run_.base / STATE_NAME, timeout_s=run_.chatgpt_timeout, announce=announce)
    select_model(run_, announce=announce)
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
