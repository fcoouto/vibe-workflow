"""A stand-in OpenCode server for the live-probe stages (descendants, s33d, chatgpt), not collected by unittest.

It extends the pilot double's shapes with what those stages touch: sessions created with `parentID` (and the
session-created events), the routing shim's `descendants` record (a root's creation is "observed" only when the
session is created after `attach`), a root prompt that makes the model call the subagent tool, a `subagent: true`
command child, an API-created child, S3.3d edit permissions (config paths denied by segment), the `` !`env` ``
command probe, and OpenCode's "Sign in with ChatGPT" OAuth attempt with polling and the post-login model list.
Every managed model request is checked by the REAL scripts/routing.py through `routing`; the qual-post guard is
mirrored from the recorder source, with the allowed route taken from the control file.
"""
import json
import time

from tests.qualification.doubles import FakeOpenCode, raw_tokens

CONFIG_SEGMENTS = {".opencode", ".claude", ".agents", "opencode.json", "opencode.jsonc"}
TOOL_PROMPT_MARK = "subagent tool"
EDIT_PROMPT_MARK = "file tools"
EDIT_RESOURCES = (".opencode/command/x.md", "sub/opencode.json", "agents/ok.txt")


class ProbeServer(FakeOpenCode):
    def __init__(self, routing_cmd, log, project, request_facts, route=("claude-agent", "haiku", "low")):
        super().__init__(routing_cmd, log)
        self.project, self.request_facts, self.route = project, dict(request_facts), list(route)
        self.control = dict(budget=0, hold=[], title_session=None, route=list(route))
        self.events = []
        self.parents = {}
        self.roots = {}  # attached root -> dict(attempt, token, watched, recorded={child: parent})
        # Knobs.
        self.tool_subagent, self.strip_env, self.image_models = True, True, []
        self.models = [dict(id="haiku", providerID="claude-agent", variants=["low", "high"])]
        self.oauth_pending_polls, self.oauth_result, self.oauth_polls, self.connected = 2, "complete", 0, False
        self.cancelled = []

    # ---------------------------------------------------------------- the shim's view
    def _root_of(self, session):
        seen = set()
        while session is not None and session not in seen:
            if session in self.roots:
                return session
            seen.add(session)
            session = self.parents.get(session)
        return None

    def _create(self, session, parent, title, model, now):
        record = dict(id=session, parentID=parent, title=title, model=model, cost=0,
                      tokens=raw_tokens(dict.fromkeys(self.PRIMARY, 0)), time=dict(created=now))
        self.sessions[session] = dict(record=record, messages=[], prompted=False)
        self.parents[session] = parent
        self.events.append(dict(type="session.created", created=now, data=dict(sessionID=session, parentID=parent)))
        if session in self.roots:
            self.roots[session]["watched"] = True  # its own creation was seen while attached
        root = self._root_of(parent) if parent else None
        if root is not None:
            self.roots[root]["recorded"][session] = parent
        return record

    def rpc(self, method, payload):
        if method == "health":
            ledger = (self.routing("status")[0].get("ledger") or {}).get("state")
            return 200, dict(state="ok" if ledger == "ready" else "not_ready",
                             managed_denies=["config_edit", "image_generation"]), 1.0
        if method == "attach":
            answer = self.routing("check", attempt_id=payload["attempt_id"], session_id=payload["session_id"],
                                  authority=payload["authority"],
                                  request=dict(self.request_facts, funding_pool=payload["funding_pool"]))[0]
            if answer.get("state") == "allow" and answer.get("authority") == "verified":
                self.roots[payload["session_id"]] = dict(attempt=payload["attempt_id"], token=payload["authority"],
                                                         watched=False, recorded={})
                return 200, dict(state="attached", session_id=payload["session_id"]), 1.0
            return 500, dict(type="rpc.internal", message="RPC call failed"), 1.0
        root = self.roots.get(payload.get("session_id"))
        if root is None or (root["attempt"], root["token"]) != (payload.get("attempt_id"), payload.get("authority")):
            return 500, dict(type="rpc.internal", message="RPC call failed"), 1.0
        if method == "detach":
            del self.roots[payload["session_id"]]
            return 200, dict(state="detached", session_id=payload["session_id"]), 1.0
        if method == "descendants":
            entries = [dict(session_id=s, parent_session_id=p) for s, p in sorted(root["recorded"].items())]
            return 200, dict(state="ok", root=payload["session_id"], descendants=entries, truncated=False,
                             complete=root["watched"], reason=None if root["watched"] else "root_creation_unobserved",
                             cache=dict(recorded=len(entries), retired=0)), 1.0
        raise AssertionError(method)

    def _request_hook(self, session, kind, model=None):
        provider, name, variant = model or self._model_of(session)
        base = dict(hook="model.request", kind=kind, provider=provider, model=name, variant=variant, session=session)
        self._log(phase="pre", **base)
        root = self._root_of(session)
        if root is not None:
            facts = dict(self.request_facts, provider=provider, model=name, variant=variant, kind=kind)
            answer = self.routing("check", attempt_id=self.roots[root]["attempt"], session_id=session,
                                  authority=self.roots[root]["token"], request=facts)[0]
            if answer.get("state") != "allow" or answer.get("authority") != "verified":
                return False
        c, sent = self.control, self.transmitted()
        decision = "transmit"
        if c.get("route") is None or [provider, name] != list(c["route"][:2]):
            decision = "guard_model"
        elif variant != c["route"][2]:
            decision = "guard_variant"
        elif session in c.get("hold", []):
            decision = "guard_hold"
        elif kind == "title" and not (c.get("title_session") == session and not any(r["kind"] == "title" for r in sent)):
            decision = "guard_title"
        elif len(sent) >= c.get("budget", 0):
            decision = "guard_budget"
        self._log(phase="post", decision=decision, **base)
        return decision == "transmit"

    def _turn(self, session, now, parts=None):
        """One model request of `session`; on success an assistant message with the recorded tokens."""
        ok = self._request_hook(session, "primary", self._model_of(session))
        state = self.sessions[session]
        if ok:
            state["messages"].append(dict(id=self._id("msg"), type="assistant", agent="build", finish="stop", cost=0,
                                          model=state["record"]["model"], tokens=raw_tokens(self.PRIMARY),
                                          content=parts or [], time=dict(created=now, completed=now + 1)))
            self._add_tokens(session, self.PRIMARY)
        return ok

    def _model_of(self, session):
        model = self.sessions[session]["record"]["model"] or {}
        return model.get("providerID"), model.get("id"), model.get("variant")

    def _add_tokens(self, session, values):
        tokens = self.sessions[session]["record"]["tokens"]
        flat = dict(input=tokens["input"], output=tokens["output"], reasoning=tokens["reasoning"],
                    cache_read=tokens["cache"]["read"], cache_write=tokens["cache"]["write"])
        self.sessions[session]["record"]["tokens"] = raw_tokens({k: flat[k] + values[k] for k in flat})

    def _idle(self, session, ok, now):
        self.sessions[session]["messages"].append(dict(id=self._id("msg"), type="idle", outcome="succeeded" if ok else "failed",
                                                       time=dict(created=now + 2)))

    def _permission(self, session, resource):
        base = dict(hook="permission.evaluate", action="edit", session=session, resources=[resource])
        self._log(phase="pre", effect="allow", **base)
        segments = {segment.lower() for segment in resource.split("/")}
        denied = self._root_of(session) is not None and bool(segments & CONFIG_SEGMENTS)
        self._log(phase="post", effect="deny" if denied else "allow", **base)
        if not denied:
            target = self.project / resource
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(resource.rsplit("/", 1)[-1].split(".")[0])
        return not denied

    # ---------------------------------------------------------------- the HTTP API
    def request(self, method, path, body=None, query=None, timeout=60):
        now = int(time.time() * 1000)
        parts = path.strip("/").split("/")
        if path == "/api/info":
            return 200, dict(version="2.2.6"), 1.0
        if parts[:2] == ["api", "integration"]:
            return self._integration(method, parts, body)
        if path == "/api/location/reload":
            return 204, None, 1.0
        if path == "/api/model":
            models = self.models + ([dict(id="gpt-test-mini", providerID="openai", variants=["low", "medium"])]
                                    if self.connected else []) + list(self.image_models)
            return 200, dict(data=models), 1.0
        if path == "/api/plugin":
            return 200, dict(data=[dict(id=i) for i in ("qual-pre", "vibe-workflow.routing", "qual-post")]), 1.0
        if method == "POST" and path == "/api/session":
            session = body.get("id") or self._id("ses")
            if session not in self.sessions:
                self._create(session, body.get("parentID"), body.get("title"), body["model"], now)
            return 200, dict(data=self.sessions[session]["record"]), 1.0
        if method == "POST" and len(parts) == 4 and parts[3] == "command":
            return self._command(parts[2], body["name"], now)
        if method == "POST" and len(parts) == 4 and parts[3] == "prompt" and (
                TOOL_PROMPT_MARK in body["text"] or EDIT_PROMPT_MARK in body["text"]):
            return self._scripted_prompt(parts[2], body, now)
        return super().request(method, path, body, query, timeout)

    def _scripted_prompt(self, sid, body, now):
        state = self.sessions[sid]
        state["messages"].append(dict(id=body.get("id") or self._id("msg"), type="user", time=dict(created=now)))
        state["prompted"] = True
        if TOOL_PROMPT_MARK in body["text"]:
            parts = []
            if self.tool_subagent:
                child = self._id("ses")
                self._create(child, sid, None, state["record"]["model"], now)
                self._turn(child, now)
                self._idle(child, True, now)
                parts = [dict(type="tool", name="subagent", id="call_1bf0000000000000000001",
                              state=dict(status="completed", input=dict(model="/".join(self.route[:2]) + "#" + self.route[2]),
                                         metadata=dict(sessionID=child)))]
            ok = self._turn(sid, now, parts) and (not parts or self._turn(sid, now + 1))  # continuation after a tool call
        else:
            ok = self._turn(sid, now)
            for resource in EDIT_RESOURCES:
                self._permission(sid, resource)
            ok = ok and self._turn(sid, now + 1)
        self._idle(sid, ok, now + 3)
        return 200, dict(data=state["messages"][0]), 1.0

    def _command(self, sid, name, now):
        state = self.sessions[sid]
        if name == "qualchild":
            child = self._id("ses")
            self._create(child, sid, "qualchild", state["record"]["model"], now)
            self.sessions[child]["messages"].append(dict(id=self._id("msg"), type="user", time=dict(created=now)))
            ok = self._turn(child, now)
            self._idle(child, ok, now)
            state["messages"].append(dict(id=self._id("msg"), type="synthetic", time=dict(created=now + 1)))
        elif name == "qualenv":
            stripped = self.strip_env and self._root_of(sid) is not None
            self._log(phase="pre", hook="shell.create.before", session=None, env_count=18, probe_present=True)
            self._log(phase="post", hook="shell.create.before", session=None, env_count=17, probe_present=not stripped)
            marker = "QUAL_PROBE_ABSENT" if stripped else "QUAL_PROBE_PRESENT"
            state["messages"].append(dict(id=self._id("msg"), type="user", time=dict(created=now),
                                          content=[dict(type="text", text=marker)]))
        else:
            raise AssertionError(name)
        ok = self._turn(sid, now + 1)  # the parent turn after the child's result or the command's prompt
        self._idle(sid, ok, now + 2)
        return 204, None, 1.0

    def _integration(self, method, parts, body):
        integration = parts[2]
        if method == "GET" and len(parts) == 3:
            methods = [dict(id="chatgpt-token-sharing", type="oauth", label="Sign in with ChatGPT")] \
                if integration == "openai" else [dict(id="claude-login", type="external", label="Claude Code login")]
            connections = [dict(type="credential", id="cred_1bf0000000000000000000Aa", method="oauth")] \
                if integration == "openai" and self.connected else []
            return 200, dict(data=dict(id=integration, methods=methods, connections=connections)), 1.0
        if parts[3:5] == ["connect", "external"]:
            return 204, None, 1.0
        if parts[3:5] == ["connect", "oauth"] and method == "POST" and len(parts) == 5:
            assert body == dict(methodID="chatgpt-token-sharing"), body
            return 200, dict(data=dict(attemptID="att_1bf0000000000000000000Aa", mode="auto",
                                       url="https://auth.openai.com/oauth/authorize?client_id=x&state=s&code_challenge=c",
                                       instructions="Complete authorization in your browser.")), 1.0
        if parts[3:5] == ["connect", "oauth"] and method == "GET":
            self.oauth_polls += 1
            if self.oauth_polls <= self.oauth_pending_polls or self.oauth_result == "never":
                return 200, dict(data=dict(status="pending")), 1.0
            self.connected = self.oauth_result == "complete"
            return 200, dict(data=dict(status=self.oauth_result,
                                       access="tok-not-real", refresh="ref-not-real", id_token="idt-not-real")), 1.0
        if parts[3:5] == ["connect", "oauth"] and method == "DELETE":
            self.cancelled.append(parts[5])
            return 204, None, 1.0
        raise AssertionError((method, parts))


class Events:
    """The run's event-stream stand-in: the server's recorded events."""

    def __init__(self, server):
        self.server, self.error, self.status, self.stopped = server, None, 200, False

    @property
    def events(self):
        return list(self.server.events)


def message_text(messages):
    return json.dumps(messages)
