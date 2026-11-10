#!/usr/bin/env python3
"""`drill` (purpose `drill`): the S3.6c lost-acknowledgment drill of the live-session runbook (part B), on a
dedicated drill routing state (a temporary managed project and temporary stores under the isolated dir; never the
production settings or ledger). clients/opencode.md#uncertain-subscription-holds-s36c.

Drill 1 (1 call): `prepare` -> `dispatch` -> attach and prompt -> SIGTERM the launcher before it records completion
-> `uncertain` (window claim and concurrency place kept, `complete` answers `reconciliation_required`) -> one-shot
`scripts/routing.py reconcile_subscription` with `evidence{kind: host_usage}` from a re-read of the idle session ->
the same event again is a `duplicate`.
Drill 2 (0 calls): kill after `dispatch`, before the prompt -> `uncertain` -> `abandon` -> one `window_debits` key,
place released -> `drop_debit` with the marker `status` lists -> `status` clean.

The launcher runs in a child process (`--child`, this file as a script path) on the parent's server: the child wraps
the host and stops at the drill's pause point (`descendants`: after the prompt went idle, before accounting and
`record complete`; `prompt`: before the prompt is sent), announces it, and the parent sends SIGTERM. The child turns
SIGTERM into an exception, so the real `opencode_launcher.launch` records `uncertain` itself. The server password
reaches the child only on stdin.
"""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.qualification import live_common  # noqa: E402
from tests.qualification.live_common import Reservation, message_counts  # noqa: E402
from tests.qualification.managed_state import ManagedState, attempt_id  # noqa: E402
from tests.qualification.safety import Blocker, BudgetExceeded, PROMPT_TEXT, Refused  # noqa: E402

ROUTE = ("claude-agent", "haiku", "low")
ACCOUNT = "anthropic:drill"
PAUSES = ("descendants", "prompt")
PAUSE_LIMIT_S = 600
PAUSED = "drill-paused"
RESULT = "drill-result:"
CALLS = 1  # drill 1's prompt; drill 2 sends nothing


class Interrupted(BaseException):
    """SIGTERM in the child, raised where the launcher is (as scripts/opencode_launch.py does)."""


# ---------------------------------------------------------------- the child: the real launcher, paused

class PausingHost:
    """The launcher's host, stopping at the drill's pause point until `stopped()` (the SIGTERM), then raising."""

    def __init__(self, host, pause, stopped, announce, sleep=time.sleep, limit=PAUSE_LIMIT_S):
        if pause not in PAUSES:
            raise Refused("unknown drill pause")
        self.host, self.pause, self.stopped, self.announce, self.sleep, self.limit = host, pause, stopped, announce, sleep, limit
        self.directory, self.port = host.directory, getattr(host, "port", None)
        self.paused = False

    def hold(self):
        self.paused = True
        self.announce()
        deadline = time.monotonic() + self.limit
        while not self.stopped():
            if time.monotonic() > deadline:
                raise Interrupted("pause limit")  # never left waiting: treated like the signal
            self.sleep(0.05)
        raise Interrupted("SIGTERM")

    def request(self, method, path, body=None, query=None):
        if self.pause == "prompt" and method == "POST" and path.endswith("/prompt"):
            self.hold()
        return self.host.request(method, path, body=body, query=query)

    def rpc(self, method, payload):
        if self.pause == "descendants" and method == "descendants":
            self.hold()
        return self.host.rpc(method, payload)


def run_child(spec, host, stopped, announce):
    """One launch on the drill state, paused at `spec["pause"]`. Returns `{state, ...}` without the token."""
    from runtime import opencode_launcher
    state = ManagedState(spec["root"], tuple(spec["route"]), ACCOUNT)
    assignment, job = state.assignment(spec["attempt"], spec["prompt"])
    service = state.service()
    try:
        with state.source_installed() as source:
            try:
                result = opencode_launcher.launch(state.project, host=PausingHost(host, spec["pause"], stopped, announce),
                                                  assignment=assignment, job=job, prompt=spec["prompt"], service=service,
                                                  source=source)
            except Interrupted:
                return dict(state="interrupted")
        return dict(state=result.get("state"), reason=result.get("reason"))
    finally:
        service.close()


def child_main():
    from runtime import opencode_launcher
    spec = json.loads(sys.stdin.readline())
    flag = dict(stop=False)

    def on_term(_signum, _frame):
        flag["stop"] = True
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # one conversion; the launcher's cleanup runs undisturbed

    signal.signal(signal.SIGTERM, on_term)
    host = opencode_launcher.ServerHost(spec.pop("port"), spec.pop("password"), spec.pop("directory"))

    def announce():
        print(PAUSED, flush=True)
    try:
        result = run_child(spec, host, lambda: flag["stop"], announce)
    except Exception as error:  # noqa: BLE001 - a fixed record, never a traceback with paths
        result = dict(state="error", error=type(error).__name__)
    print(RESULT + json.dumps(result), flush=True)
    return 0


class ChildLaunch:
    """The live child process: `wait_paused`, `terminate` (SIGTERM) and `result`."""

    def __init__(self, spec, host, python=sys.executable):
        self.process = subprocess.Popen([python, "-B", str(Path(__file__).resolve()), "--child"], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        secret = dict(spec, port=host.port, password=host._password, directory=host.directory)
        self.process.stdin.write((json.dumps(secret) + "\n").encode())
        self.process.stdin.close()
        self.buffer = b""

    def _line(self, deadline):
        fd = self.process.stdout.fileno()
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line.decode("utf-8", "replace")

    def wait_paused(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            line = self._line(deadline)
            if line is None or line.startswith(RESULT):
                return False
            if line == PAUSED:
                return True

    def terminate(self):
        try:
            os.kill(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def result(self, timeout=120):
        deadline = time.monotonic() + timeout
        while True:
            line = self._line(deadline)
            if line is None:
                self.kill()
                return dict(state="no_result")
            if line.startswith(RESULT):
                self.process.wait(30)
                self.process.stdout.close()
                return json.loads(line[len(RESULT):])

    def kill(self):
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            pass
        self.process.stdout.close()


# ---------------------------------------------------------------- the parent: the drill's steps

def ledger_view(status):
    ledger = (status or {}).get("ledger") or {}
    reconcile = ledger.get("reconcile") or {}
    holds = ledger.get("holds") or {}
    return dict(state=(status or {}).get("state"), open_attempts=ledger.get("open_attempts"),
                uncertain=holds.get("uncertain") if isinstance(holds, dict) else None,
                dispatched=holds.get("dispatched") if isinstance(holds, dict) else None,
                reconcile_subscription=(reconcile.get("reconcile_subscription") or {}).get("count"),
                window_debits=(reconcile.get("window_debits") or {}).get("count"),
                reconcile_hint=ledger.get("reconcile_hint") is not None)


def clean(view):
    return view["state"] == "ok" and view["open_attempts"] == 0 and not view["reconcile_subscription"] \
        and not view["window_debits"] and not view["reconcile_hint"]


class Drill:
    """`env` supplies `start(project, settings, ledger, environ)`, `child(spec, host)` (a ChildLaunch-like handle),
    `routing(state)` (one-shot `scripts/routing.py` on the drill state), `sleep` and `environ()` (the server's
    environment: the real HOME for the Claude login, a temporary data dir)."""

    def __init__(self, base, ledger, run_id, env, pause_timeout=600):
        self.base, self.ledger, self.run_id, self.env, self.pause_timeout = Path(base), ledger, run_id, env, pause_timeout
        self.steps, self.facts, self.host = [], {}, None

    def step(self, name, expected, observed, passed):
        self.steps.append(dict(step=name, expected=expected, observed=observed, passed=bool(passed)))
        if not passed:
            raise Blocker("drill step %s: expected %s" % (name, expected))

    def status(self, routing):
        return ledger_view(routing("status")[0])

    def launch_paused(self, state, attempt, pause):
        child = self.env.child(dict(root=str(state.root), route=list(ROUTE), attempt=attempt, prompt=PROMPT_TEXT,
                                    pause=pause), self.host)
        if not child.wait_paused(self.pause_timeout):
            child.kill()
            raise Blocker("the launcher never reached the %s pause" % pause)
        return child

    def run(self):
        state = ManagedState(self.base / "drill-state", ROUTE, ACCOUNT)
        state.create()
        routing = self.env.routing(state)
        self.step("state_clean_at_start", "clean", self.status(routing), clean(self.status(routing)))
        self.host = self.env.start(state.project, state.settings, state.ledger, self.env.environ())
        try:
            self.facts["claude_connect_http"] = live_common.connect_claude(self.host, self.env.sleep)
            entry = live_common.listed_model(self.host, ROUTE[0], ROUTE[1], self.env.sleep)
            if entry is None or ROUTE[2] not in live_common.variants_of(entry):
                raise Blocker("claude-agent/haiku#low is not listed on the drill server")
            self.drill_one(state, routing)
            self.drill_two(state, routing)
        finally:
            if self.host is not None:
                self.host.stop()
        return dict(steps=self.steps, facts=self.facts, passed=all(step["passed"] for step in self.steps))

    def drill_one(self, state, routing):
        attempt = attempt_id("drill1")
        reservation = Reservation(self.ledger, "drill", "drill 1: prompt before the lost acknowledgment", CALLS,
                                  self.run_id)
        try:
            child = self.launch_paused(state, attempt, "descendants")
            view = self.status(routing)
            self.step("1_dispatched", "one dispatched hold", view, view["open_attempts"] == 1 and view["dispatched"] == 1)
            child.terminate()
            result = child.result()
            self.step("1_sigterm", "the launcher answers interrupted", result, result.get("state") == "interrupted")
            session = routing.admission_ids(attempt)["session_id"]
            self.facts["drill1_sent"] = reservation.settle(message_counts(self.host, [session]))
        finally:
            if reservation.settled is None:
                reservation.settle_unknown()
        view = self.status(routing)
        self.step("1_uncertain", "uncertain, place held, reconcile_subscription 1, hint set", view,
                  view["uncertain"] == 1 and view["open_attempts"] == 1 and view["reconcile_subscription"] == 1
                  and view["reconcile_hint"])
        answer = routing("record", event=dict(event_id=attempt + "-client-complete", attempt_id=attempt, kind="complete"))[0]
        self.step("1_complete_refused", "reconciliation_required", answer.get("reason"),
                  answer.get("reason") == "reconciliation_required")
        from runtime import opencode_launcher
        read = opencode_launcher.account(self.host, session, funding="subscription", attempt=attempt, authority=None)
        usage = read["result"].get("usage")
        self.step("1_reread", "host_message_totals usage", (usage or {}).get("source"),
                  (usage or {}).get("source") == "host_message_totals")
        body = dict(attempt_id=attempt, evidence=dict(kind="host_usage", session_id=session, usage=usage),
                    event_id=attempt + "-host-usage")
        answer = routing("reconcile_subscription", **body)[0]
        basis = ((answer.get("settlement") or {}).get("resolution") or {}).get("basis")
        self.step("1_host_usage", "recorded complete, basis host_usage", dict(state=answer.get("state"), basis=basis,
                                                                             reason=answer.get("reason")),
                  answer.get("state") == "recorded" and basis == "host_usage")
        view = self.status(routing)
        self.step("1_released", "place and window released", view, view["open_attempts"] == 0
                  and not view["reconcile_subscription"])
        again = routing("reconcile_subscription", **body)[0]
        self.step("1_duplicate", "duplicate: true", again.get("duplicate"), again.get("duplicate") is True)

    def drill_two(self, state, routing):
        attempt = attempt_id("drill2")
        child = self.launch_paused(state, attempt, "prompt")
        view = self.status(routing)
        self.step("2_dispatched", "one dispatched hold", view, view["open_attempts"] == 1 and view["dispatched"] == 1)
        child.terminate()
        result = child.result()
        self.step("2_sigterm", "the launcher answers interrupted", result, result.get("state") == "interrupted")
        session = routing.admission_ids(attempt)["session_id"]
        proof = message_counts(self.host, [session])
        self.step("2_zero_calls", "no user or assistant message", proof, proof["readable"] and not proof["user"]
                  and not proof["assistant"])
        view = self.status(routing)
        self.step("2_uncertain", "uncertain, place held", view, view["uncertain"] == 1 and view["open_attempts"] == 1)
        answer = routing("reconcile_subscription", attempt_id=attempt, evidence=dict(kind="abandon"),
                         event_id=attempt + "-abandon")[0]
        self.step("2_abandon", "abandoned", answer.get("outcome"), answer.get("outcome") == "abandoned")
        status = routing("status")[0]
        view = ledger_view(status)
        self.step("2_debit", "one window_debits key, place released", view, view["window_debits"] == 1
                  and view["open_attempts"] == 0)
        debits = (((status.get("ledger") or {}).get("reconcile") or {}).get("drop_debit") or {}).get("debits") or []
        answer = routing("drop_debit", key=debits[0]["key"], marker=debits[0]["marker"], event_id=attempt + "-drop")[0] \
            if debits else {}
        self.step("2_drop_debit", "debit_dropped", answer.get("outcome"), answer.get("outcome") == "debit_dropped")
        view = self.status(routing)
        self.step("2_clean", "status clean", view, clean(view))


def run_drill(drill):
    """The drill's results; a blocker ends it with the steps reached so far."""
    try:
        return drill.run()
    except (Blocker, BudgetExceeded, Refused) as error:
        return dict(steps=drill.steps, facts=drill.facts, passed=False, blocker="%s: %s" % (type(error).__name__, error))


if __name__ == "__main__":
    if sys.argv[1:] != ["--child"]:
        print("drill.py runs only as the drill's child (--child); use live_modes.py drill", file=sys.stderr)
        sys.exit(2)
    sys.exit(child_main())
