"""Fake `routing.py --serve` child for the OpenCode plugin tests.

Usage: fake_routing.py --serve --control PATH --log PATH

The control file maps an op to a list of modes consumed in order across processes (the last one repeats):
allow, unmanaged, blocked, stall, exit, garbage, wrong_id, wrong_version, no_state, leak_reason, shuffle, and for
`status` also ledger_absent and settings_unavailable (a live child that is not ready).
`admitted` ({provider, model, variant}) makes an `allow` check answer route_mismatch for any other route.
`tokens` maps an attempt id to its dispatch authority: an `allow` check with an attempt id answers
authority_required without a token, authority_invalid with another one, and otherwise adds `authority: "verified"`,
like the S3.4 command. Further check modes: allow_unverified (allow without `authority`), allow_other_attempt (allow
for another attempt) and echo_authority (a blocked answer whose reason and fields repeat the token sent).
A session-only check (no attempt id) is the shim's ledger probe; its modes come from the `probe` key and it answers
authority_required for a session listed in `known_sessions`, otherwise unmanaged.
Every spawn and request is appended to the log file so tests can count spawns and calls.
"""
import json
import os
import random
import sys
import threading
import time


def argument(name):
    return sys.argv[sys.argv.index(name) + 1]


CONTROL, LOG = argument("--control"), argument("--log")
lock = threading.Lock()


def log(record):
    with lock, open(LOG, "a") as handle:
        handle.write(json.dumps(record) + "\n")


def next_mode(op):
    with lock:
        control = json.load(open(CONTROL))
        counter_path = CONTROL + ".counter"
        counters = json.load(open(counter_path)) if os.path.exists(counter_path) else {}
        modes = control.get(op, ["allow"])
        index = counters.get(op, 0)
        counters[op] = index + 1
        json.dump(counters, open(counter_path, "w"))
        return modes[min(index, len(modes) - 1)], control


def write(response):
    with lock:
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


def answer(request):
    op = request.get("op")
    probe = op == "check" and "session_id" in request and "attempt_id" not in request
    mode, control = next_mode("probe" if probe else op)
    rid = request.get("id")
    state = {"check": "allow", "status": "ok"}.get(op, "blocked")
    if mode == "stall":
        time.sleep(3600)
    if mode == "exit":
        os._exit(3)
    if mode == "garbage":
        with lock:
            sys.stdout.write("not json at all\n")
            sys.stdout.flush()
        return
    response = {"v": 1, "state": state, "id": rid, "extra_field": "ignored"}
    if op == "status":
        response.update(settings_state="ok", ledger={"state": "ready"})
    if mode == "ledger_absent":
        response["ledger"] = {"state": "absent"}
    elif mode == "settings_unavailable":
        response["settings_state"] = "unavailable"
    elif mode == "unmanaged":
        response["state"] = "unmanaged"
    elif mode == "blocked":
        response.update(state="blocked", reason="route_mismatch")
    elif mode == "leak_reason":
        response.update(state="blocked", reason="sk-ant-SECRET-VALUE prompt text")
    elif mode == "wrong_id":
        response["id"] = f"{rid}-other"
    elif mode == "wrong_version":
        response["v"] = 2
    elif mode == "no_state":
        del response["state"]
    elif mode == "shuffle":
        time.sleep(random.random() * 0.05)
    elif mode == "echo_authority":
        sent = str(request.get("authority"))
        response.update(state="blocked", reason=sent, detail=sent)
    if probe and response["state"] == "allow":
        known = request["session_id"] in control.get("known_sessions", [])
        response.update(state="blocked", reason="authority_required") if known else response.update(state="unmanaged")
        write(response)
        return
    if op == "check" and response["state"] == "allow" and mode in ("allow", "shuffle"):
        token = control.get("tokens", {}).get(request.get("attempt_id"))
        if "authority" not in request:
            response.update(state="blocked", reason="authority_required")
        elif token is None or request["authority"] != token:
            response.update(state="blocked", reason="authority_invalid")
        else:
            response.update(authority="verified", attempt_id=request["attempt_id"])
    elif op == "check" and mode == "allow_other_attempt":
        response.update(authority="verified", attempt_id="someone-else")
    admitted = control.get("admitted")
    if op == "check" and response["state"] == "allow" and admitted:
        facts = request.get("request", {})
        if any(facts.get(key) != admitted.get(key) for key in ("provider", "model", "variant")):
            response.update(state="blocked", reason="route_mismatch")
    write(response)


def main():
    log({"spawn": os.getpid()})
    for line in sys.stdin:
        request = json.loads(line)
        log({"request": request})
        if json.load(open(CONTROL)).get("threads"):
            threading.Thread(target=answer, args=(request,), daemon=True).start()
        else:
            answer(request)


if __name__ == "__main__":
    main()
