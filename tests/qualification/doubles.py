"""Shared test doubles for the qualification harness tests (not collected: the name does not start with test_).

`Temp` gives each test a private temporary tree; `Fake` is a scriptable canary environment; `FakeOpenCode` is an
OpenCode server double with the live-recorded payload shapes whose shim decisions go to the real scripts/routing.py;
`FAKE_SERVE` is a misbehaving `routing.py --serve` stand-in for reply-id tests.
"""
import json
from pathlib import Path
import shutil
import tempfile
import time
import types
import unittest

from tests.qualification import canary


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.private(self.tmp / "home")

    def private(self, path, mode=0o700):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
        return path


class Fake:
    """A canary environment double: every call is recorded; behavior is switched per test."""

    def __init__(self, **change):
        self.calls = []
        self.settings = dict(smart_routing=True, api_usage_enabled=False, revision=3)
        self.ledger = dict(state="ready", holds={}, open_attempts=0, authority_required=True, unstamped_open_holds=0,
                           unbound_open_holds=0, cost_unknown=dict(ids=[], more=0), uncertain_dollar=dict(ids=[], more=0),
                           blocked_organizations=[], overrun_backlog=False, reconcile_hint=None, organizations=[])
        self.caps = dict(total=4, per_account=2)
        self.transmit = dict(primary=1, title=0, other=0)
        self.off_route = 0
        self.settlement = dict(kind="window", released={"5h": 10}, observed_units={})
        self.fail = {}
        self.raises = {}
        self.repo = dict(new=[], dirty_changed=[], head_changed=False)
        self.__dict__.update(change)

    def _raise(self, name):
        if name in self.raises:
            raise self.raises[name]

    def status(self):
        self.calls.append("status")
        if "status" in self.fail:
            return self.fail["status"]
        return dict(state="ok", settings_state="ok", settings=dict(self.settings), settings_revision=self.settings["revision"],
                    effective_caps=dict(self.caps), ledger=json.loads(json.dumps(self.ledger)), test_inputs=False,
                    project_pinned=True)

    def health(self):
        self.calls.append("health")
        return dict(state="ok")

    def prepare(self, body):
        self.calls.append("prepare")
        self._raise("prepare")
        return dict(state=self.fail.get("prepare", "admitted"), admission=dict(billing_class="included_subscription")), 5.0

    def dispatch(self, attempt):
        self.calls.append("dispatch")
        self._raise("dispatch")
        return dict(state="dispatched", authority="t" * 43, session_id="ses_" + "a" * 32, message_id="msg_" + "b" * 32), 7.0

    def attach(self, session, attempt, token):
        self.calls.append("attach")
        self._raise("attach")
        return self.fail.get("attach", True)

    def detach(self, session, attempt, token):
        self.calls.append("detach")
        return True

    def launch(self, session, message, allow_title):
        self.calls.append("launch")
        self._raise("launch")
        if self.fail.get("launch") == "timeout":
            return dict(idle=False)
        transmit = dict(self.transmit, title=1 if allow_title else 0) if self.transmit.get("title") is None else dict(self.transmit)
        return dict(idle=True, transmitted=transmit, off_route=self.off_route, outcome="succeeded", check_ms=0.7)

    def account(self, session):
        self.calls.append("account")
        self._raise("account")
        if "account" in self.fail:
            raise ValueError("refused")
        return dict(usage=dict(source="host_message_totals", requests=[dict(kind="primary")]), unknown=[])

    def record(self, event):
        self.calls.append("record:" + event["kind"])
        return dict(state="recorded", outcome="complete", settlement=dict(self.settlement)), 4.0

    def repo_changes(self):
        self.calls.append("repo")
        return dict(self.repo)

    def kill_switch(self):
        self.calls.append("kill_switch")
        self._raise("kill_switch")
        self.settings["smart_routing"] = False
        return dict(state="updated")


def types_namespace(**values):
    import types
    return types.SimpleNamespace(**values)


FAKE_SERVE = r"""
import json, sys, time
mode, count_file = sys.argv[1], sys.argv[2]
spawns = int(open(count_file).read() or 0) + 1 if __import__("os").path.exists(count_file) else 1
open(count_file, "w").write(str(spawns))
for line in sys.stdin:
    request = json.loads(line)
    with open(count_file + ".requests", "a") as log:
        log.write(request["op"] + "\n")
    if spawns == 1 and mode == "stale":
        print(json.dumps(dict(v=1, state="ok", id="q-old", stale=True)), flush=True)
        continue
    if spawns == 1 and mode == "delay":
        time.sleep(5)
    print(json.dumps(dict(v=1, state="ok", id=request["id"], op=request["op"], spawn=spawns)), flush=True)
"""


def raw_tokens(values):
    return {"input": values["input"], "output": values["output"], "reasoning": values["reasoning"],
            "cache": {"read": values["cache_read"], "write": values["cache_write"]}}


class FakeOpenCode:
    """An OpenCode server double with the live-recorded shapes (qual-rerun-results.json): sessions created with
    the derived id, a prompt that triggers one title (first prompt of an untitled session) and one primary request,
    stored user / assistant / idle records, session totals that include the title's tokens, paging that ends on an
    empty page, an empty children listing and a session listing for lineage. The shim's decisions (attach and every
    managed `model.request` check) go to the REAL scripts/routing.py through `routing`; the qual-post guard is
    mirrored from the recorder source."""

    PRIMARY = dict(input=2, output=4, reasoning=0, cache_read=4081, cache_write=346)  # re-run stage 3, recorded
    TITLE = dict(input=2, output=4, reasoning=0, cache_read=823, cache_write=346)

    def __init__(self, routing_cmd, log):
        self.routing, self.log, self.sessions, self.attached = routing_cmd, log, {}, {}
        self.log.touch()
        self.control = dict(budget=0, hold=[], title_session=None)
        self.process, self.project_config, self.directory, self.counter = None, None, "/fake-location", 0

    def write_control(self, **change):
        self.control.update(change)

    def hooks(self):
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def transmitted(self):
        return [r for r in self.hooks() if r.get("decision") == "transmit"]

    def _log(self, **record):
        with self.log.open("a") as handle:
            handle.write(json.dumps(dict(record, t=time.perf_counter() * 1000)) + "\n")

    def _id(self, prefix):
        self.counter += 1
        return "%s_1bf%023dAa" % (prefix, self.counter)

    def rpc(self, method, payload):
        if method == "health":
            ledger = (self.routing("status")[0].get("ledger") or {}).get("state")
            return 200, dict(state="ok" if ledger == "ready" else "not_ready"), 1.0
        if method == "attach":
            answer = self.routing("check", attempt_id=payload["attempt_id"], session_id=payload["session_id"],
                                  authority=payload["authority"], request=canary.request(payload["funding_pool"]))[0]
            if answer.get("state") == "allow" and answer.get("authority") == "verified":
                self.attached[payload["session_id"]] = (payload["attempt_id"], payload["authority"])
                return 200, dict(state="attached", session_id=payload["session_id"]), 1.0
            return 500, dict(type="rpc.internal", message="RPC call failed"), 1.0
        if method == "detach":
            if self.attached.get(payload["session_id"]) == (payload["attempt_id"], payload["authority"]):
                del self.attached[payload["session_id"]]
                return 200, dict(state="detached", session_id=payload["session_id"]), 1.0
            return 500, dict(type="rpc.internal", message="RPC call failed"), 1.0
        raise AssertionError(method)

    def _request_hook(self, session, kind):
        """The shim then the qual-post guard, as on the server; returns whether OpenCode would send it."""
        base = dict(hook="model.request", kind=kind, provider="claude-agent", model="haiku", variant="low", session=session)
        self._log(phase="pre", **base)
        if session in self.attached:
            attempt, token = self.attached[session]
            answer = self.routing("check", attempt_id=attempt, session_id=session, authority=token,
                                  request=dict(canary.request(), kind=kind))[0]
            if answer.get("state") != "allow" or answer.get("authority") != "verified":
                return False
        sent = self.transmitted()
        if kind == "title" and not (self.control["title_session"] == session and
                                    not any(r["kind"] == "title" for r in sent)):
            self._log(phase="post", decision="guard_title", **base)
            return False
        if len(sent) >= self.control["budget"]:
            self._log(phase="post", decision="guard_budget", **base)
            return False
        self._log(phase="post", decision="transmit", **base)
        return True

    def request(self, method, path, body=None, query=None, timeout=60):
        now = int(time.time() * 1000)
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/api/session":
            session = body.get("id") or self._id("ses")
            record = dict(id=session, parentID=None, title=body.get("title"), model=body["model"], cost=0,
                          tokens=raw_tokens(dict.fromkeys(self.PRIMARY, 0)), time=dict(created=now))
            self.sessions.setdefault(session, dict(record=record, messages=[], prompted=False))
            return 200, dict(data=self.sessions[session]["record"]), 1.0
        if method == "GET" and path == "/api/session/active":
            return 200, dict(data={}), 1.0
        if method == "GET" and path == "/api/session":
            return 200, dict(data=[s["record"] for s in self.sessions.values()]), 1.0
        session = self.sessions[parts[2]]
        if method == "GET" and len(parts) == 3:
            return 200, dict(data=session["record"]), 1.0
        if method == "GET" and parts[3] == "message":
            if query and "cursor" in query:
                return 200, dict(data=[], cursor=dict(previous="p", next=None)), 1.0
            return 200, dict(data=list(session["messages"]), cursor=dict(previous=None, next="n")), 1.0
        if method == "POST" and parts[3] == "prompt":
            sid = parts[2]
            session["messages"].append(dict(id=body.get("id") or self._id("msg"), type="user", time=dict(created=now)))
            totals = dict.fromkeys(self.PRIMARY, 0)
            if session["record"]["title"] is None and not session["prompted"] and self._request_hook(sid, "title"):
                totals = {k: totals[k] + self.TITLE[k] for k in totals}
            session["prompted"] = True
            ok = self._request_hook(sid, "primary")
            if ok:
                totals = {k: totals[k] + self.PRIMARY[k] for k in totals}
                session["messages"].append(dict(id=self._id("msg"), type="assistant", agent="build", finish="stop", cost=0,
                                                model=dict(id="haiku", providerID="claude-agent", variant="low"),
                                                tokens=raw_tokens(self.PRIMARY), time=dict(created=now, completed=now + 1)))
            session["messages"].append(dict(id=self._id("msg"), type="idle", outcome="succeeded" if ok else "failed",
                                            time=dict(created=now + 2)))
            previous = session["record"]["tokens"]
            flat = dict(input=previous["input"], output=previous["output"], reasoning=previous["reasoning"],
                        cache_read=previous["cache"]["read"], cache_write=previous["cache"]["write"])
            session["record"]["tokens"] = raw_tokens({k: flat[k] + totals[k] for k in totals})
            return 200, dict(data=session["messages"][0]), 1.0
        raise AssertionError((method, path))

    def stop(self):
        pass
