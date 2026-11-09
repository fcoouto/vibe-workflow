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
from tests.qualification.safety import PROMPT_TEXT, Blocker, Refused, check_route, parse_route

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
REQUEST_S = 5  # each registration poll's own timeout, so the whole wait stays near REGISTER_S
# A refused sign-in's reason: only these short plain-string fields of the attempt's final status are kept (2.2.6's
# `failed` status carries `message`; refusal codes such as subscription_sharing_user_not_eligible may arrive there).
ERROR_FIELDS = ("code", "error", "error_description", "message", "reason")
ERROR_CAP = 200


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
        try:
            status, info, _ = run.server.request("GET", "/api/integration/%s" % integration, timeout=REQUEST_S)
        except OSError:  # includes timeouts: the total deadline still bounds the wait
            status, info = None, None
        methods = methods_of(run.data(info) if status == 200 else None)
        seen = (status, [m["id"] for m in methods])
        if seen != last:
            polls.append(dict(http=status, methods=seen[1]))
            last = seen
        if any(m["id"] == method_id and m["type"] == "oauth" for m in methods) or clock() >= deadline:
            return status, methods, polls
        sleep(0.5)


def sign_in_error(body):
    """`(error fields, key names)` of a sign-in attempt's final status: short plain strings of `ERROR_FIELDS`, already
    redacted, never a URL; every other field contributes its name only."""
    body = body if isinstance(body, dict) else {}
    error = {k: body[k][:ERROR_CAP] for k in ERROR_FIELDS
             if isinstance(body.get(k), str) and body[k].strip() and "://" not in body[k] and body[k] != "<secret>"}
    return error, sorted(str(k)[:40] for k in body)[:30]


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
        final = state if isinstance(state, str) else "unknown"
        error, keys = sign_in_error(run.redact(run.data(body) or {}))
        run.note(PURPOSE, "signin", dict(status=final, error=error, keys=keys))
        detail = error.get("message") or ", ".join("%s=%s" % item for item in sorted(error.items())) or "no reason given"
        raise Blocker("the ChatGPT sign-in ended with status %s: %s (fields: %s)" % (final, detail, ", ".join(keys)))
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
    run.route, run.guard_variant, run.chatgpt_variants = route, others[0], [str(v)[:40] for v in variants][:10]
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


# Sanitized facts about what the server returned for the managed request, so one run can tell an upstream refusal,
# an invalid variant, a wrong request shape and a harness cut apart. OpenCode 2.2.6 shapes (binary): an assistant
# message has finish, rawFinish, cost, tokens, error and retry {attempt, at, error}; an idle record has outcome
# (succeeded, failed, interrupted) and error; errors are `Session.StructuredError` {type, message, status?,
# response?: {body}, retryAfterMs?}; session status events are idle, busy or retry {attempt, message, next}.
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
BODY_FIELDS = ERROR_FIELDS + ("param", "type")
# Refusal codes the 2.2.6 chatgpt plugin itself recognizes, and common OpenAI error codes; matched in error bodies.
KNOWN_CODES = ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_user_not_eligible",
               "subscription_sharing_unsupported_capability", "subscription_sharing_route_not_supported",
               "subscription_sharing_invalid_user", "chatpass_v2_scope_not_authorized",
               "chatpass_v2_invalid_authorization_context", "usage_limit_reached", "rate_limit_exceeded",
               "insufficient_quota", "model_not_found", "unsupported_value", "unsupported_parameter",
               "invalid_request_error", "context_length_exceeded")
EVENT_TYPES = ("session.status", "session.error", "session.idle", "message.updated")


def short(run, value, cap=ERROR_CAP):
    """A redacted, URL-free, length-capped string, or None."""
    return URL.sub("<url>", run.redact(value))[:cap] if isinstance(value, str) and value.strip() else None


def body_facts(run, raw):
    """An upstream error body: whitelisted short fields of its (inner) error object, its key names, known codes."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    inner = parsed.get("error") if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict) else parsed
    inner = run.redact(inner) if isinstance(inner, dict) else {}
    return dict(fields={k: short(run, inner[k]) for k in BODY_FIELDS if short(run, inner.get(k))},
                keys=sorted(str(k)[:40] for k in inner)[:30], codes=[c for c in KNOWN_CODES if c in raw],
                json=isinstance(parsed, dict), length=len(raw))


def structured_error(run, error):
    if not isinstance(error, dict):
        return None
    raw = (error.get("response") or {}).get("body") if isinstance(error.get("response"), dict) else None
    clean = run.redact(error)
    return dict(type=short(run, clean.get("type"), 80), message=short(run, clean.get("message")),
                status=error.get("status") if type(error.get("status")) is int else None,
                retry_after_ms=error.get("retryAfterMs") if type(error.get("retryAfterMs")) is int else None,
                keys=sorted(str(k)[:40] for k in error)[:20], body=body_facts(run, raw) if isinstance(raw, str) else None)


def integer_tokens(tokens):
    flat = {}
    for key, value in (tokens or {}).items() if isinstance(tokens, dict) else ():
        if isinstance(value, dict):
            flat.update({"%s.%s" % (key, k): v for k, v in value.items() if type(v) is int})
        elif type(value) is int:
            flat[str(key)] = value
    return flat


def assistant_facts(run, message):
    model = message.get("model") if isinstance(message.get("model"), dict) else {}
    retry = message.get("retry") if isinstance(message.get("retry"), dict) else None
    times = message.get("time") if isinstance(message.get("time"), dict) else {}
    return dict(finish=short(run, message.get("finish"), 40), raw_finish=short(run, message.get("rawFinish"), 80),
                error=structured_error(run, message.get("error")), time_completed=bool(times.get("completed")),
                time_streamed=bool(times.get("streamed")), tokens=integer_tokens(message.get("tokens")),
                cost_present=message.get("cost") is not None,
                model={k: short(run, model.get(k), 80) for k in ("providerID", "id", "variant")},
                retry=dict(attempt=retry.get("attempt") if type(retry.get("attempt")) is int else None,
                           error=structured_error(run, retry.get("error"))) if retry else None,
                content_types=sorted({str(p.get("type"))[:20] for p in message.get("content") or [] if isinstance(p, dict)}))


def event_facts(run, root):
    found = []
    for event in (run.events.events if run.events else []):
        data = event.get("data") if isinstance(event, dict) and isinstance(event.get("data"), dict) else {}
        if event.get("type") not in EVENT_TYPES or data.get("sessionID") != root:
            continue
        status = data.get("status") if isinstance(data.get("status"), dict) else None
        error = data.get("error") or ((data.get("info") or {}).get("error") if isinstance(data.get("info"), dict) else None)
        if event["type"] == "message.updated" and not error:
            continue
        entry = dict(type=event["type"])
        if status:
            entry["status"] = {k: v for k, v in (("type", short(run, status.get("type"), 20)),
                                                 ("attempt", status.get("attempt"))) if v is not None and v != ""}
            if short(run, status.get("message")):
                entry["status"]["message"] = short(run, status.get("message"))
        if error:
            entry["error"] = structured_error(run, error)
        found.append(entry)
    return found[:30]


def session_facts(run, root, began, body, waited, wait_ms):
    """What the server returned for the managed session (ids, kinds, shapes and short redacted errors only)."""
    status, session, _ = run.server.request("GET", "/api/session/%s" % root)
    info = run.data(session) if status == 200 and isinstance(run.data(session), dict) else {}
    http, page, _ = run.server.request("GET", "/api/session/%s/message" % root, query={"order": "asc"})
    try:
        messages = run.messages(root)
    except Blocker:
        messages = []
    counts = {}
    for message in messages:
        kind = str(message.get("type"))[:30]
        counts[kind] = counts.get(kind, 0) + 1
    recent = [m for m in messages if ((m.get("time") or {}).get("created") or 0) >= began]
    return dict(session=dict(http=status, keys=sorted(str(k)[:40] for k in info)[:40],
                             outcome=info.get("outcome") if info.get("outcome") in ("succeeded", "failed", "interrupted") else None),
                messages_http=http, messages_keys=sorted(str(k)[:40] for k in page)[:20] if isinstance(page, dict) else None,
                messages_by_type=counts, user_message=any(m.get("type") == "user" for m in messages),
                assistant=[assistant_facts(run, m) for m in messages if m.get("type") == "assistant"][:5],
                idle=[dict(outcome=short(run, m.get("outcome"), 20),
                           error_type=(structured_error(run, m.get("error")) or {}).get("type"))
                      for m in recent if m.get("type") == "idle"][:5],
                events=event_facts(run, root), request_body_keys=sorted(body), variant=run.route[2],
                variants_listed=list(getattr(run, "chatgpt_variants", None) or []),
                wait=dict(ms=wait_ms, reason=waited))


def upstream_code(facts):
    """The most specific refusal recorded: a known code, the error body's code or type, else the error's type."""
    errors = [a["error"] for a in facts["assistant"] if a["error"]] + \
        [a["retry"]["error"] for a in facts["assistant"] if a["retry"] and a["retry"]["error"]] + \
        [e["error"] for e in facts["events"] if e.get("error")]
    for error in errors:
        body = error.get("body") or {}
        if body.get("codes"):
            return body["codes"][0]
    for error in errors:
        fields = (error.get("body") or {}).get("fields") or {}
        if fields.get("code") or fields.get("type"):
            return fields.get("code") or fields.get("type")
    failed = [i for i in facts["idle"] if i["outcome"] == "failed"]
    return next((e.get("type") for e in errors if e.get("type")), None) or ("idle_failed" if failed else None)


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
    body = {"text": PROMPT_TEXT, "id": a["message"]}  # exactly what is sent; its key names are recorded
    run.server.request("POST", "/api/session/%s/prompt" % root, body)
    started = time.monotonic()
    try:
        run.wait_idle(root, began)
    except Blocker as error:
        run.note(s, "session_facts", session_facts(run, root, began, body, "timeout: %s" % error,
                                                    round((time.monotonic() - started) * 1000)))
        raise
    facts = session_facts(run, root, began, body, "idle record after the prompt and the session not active",
                          round((time.monotonic() - started) * 1000))
    run.note(s, "session_facts", facts)
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
    upstream = upstream_code(facts) if sent == 1 and observed["kinds"] != ["primary"] else None
    if upstream:  # transmitted, then refused or failed upstream: reported apart from a missing or extra request
        failures.append("request_failed_upstream")
        run.note(s, "upstream_error", upstream)
    for condition, reason in ((upstream or (sent == 1 and observed["kinds"] == ["primary"]), "not_one_primary_request"),
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
    model request; only the openai integration's method ids, types and labels and the number of integrations are
    recorded."""
    run.results["mode"] = "chatgpt-methods"
    run.start(PURPOSE)
    http, methods, polls = wait_integration(run, INTEGRATION, METHOD_ID, sleep=sleep, clock=clock)
    _, info, _ = run.server.request("GET", "/api/integration/%s" % INTEGRATION)
    _, listing, _ = run.server.request("GET", "/api/integration")
    listed = run.data(listing)
    connections = (run.data(info) or {}).get("connections") if http == 200 else None
    run.note(PURPOSE, "methods", dict(openai=dict(http=http, methods=methods, polls=polls,
                                                  connections=len(connections) if isinstance(connections, list) else None),
                                      integrations_count=len(listed) if isinstance(listed, list) else None))
    run.note(PURPOSE, "sign_in_method_listed", any(m["id"] == METHOD_ID and m["type"] == "oauth" for m in methods))


def run(run_, announce=print, sleep=time.sleep):
    """The whole chatgpt run: server, sign-in, model check, authority, the plugin-chain proof, one request (or, with
    `--chatgpt-methods`, only the zero-call method listing)."""
    if getattr(run_, "chatgpt_methods", False):
        return diagnose(run_, sleep=sleep)
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
