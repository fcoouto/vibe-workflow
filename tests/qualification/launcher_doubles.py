"""Test doubles for the live-session modes (not collected: the name does not start with test_).

`FakeLauncherHost` stands in for a server `opencode_launcher.start_server` started: the shapes the launcher reads
(as its own self-check host), plus the plugin, MCP, config, permission, integration and model listings the probes
read. Its `attach` goes through the real routing command's strict check on the run's temporary stores, so a launch
through the real `opencode_launcher.launch` is verified end to end. `prompts` counts every request that could start
a model call: zero-call tests assert it stays 0. `FakeEnv` is the probes' environment.
"""
import json
from pathlib import Path
import time

from runtime import routing_launch
from runtime.adapters import opencode as adapter



def attach_route(value):
    """`provider/model[#variant]` as the shim's `spawnModel` reads it: no `#` is an omitted variant (null), and a `#`
    without a variant is malformed (None)."""
    route, separator, variant = value.partition("#")
    provider, _, model = route.partition("/")
    if separator and not variant:
        return None
    return dict(provider=provider, model=model, variant=variant if separator else None)

class FakeLauncherHost:
    def __init__(self, directory, workdir, settings=None, ledger=None, environ=None, **knobs):
        self.directory, self.workdir, self.environ = str(directory), Path(workdir), dict(environ or {})
        self.port, self._password = 4321, "pw-" + "x" * 30
        self.prompts, self.stopped, self.sessions, self.created, self.tokens = 0, False, {}, 0, []
        self.plugins = ["vibe-workflow.routing"]
        self.mcp, self.config, self.unauth = {}, {"plugins": ["<inline>"]}, 401
        self.effects = {}  # action -> effect; default deny
        self.models = [dict(providerID="claude-agent", id="haiku", variants=[dict(id="low"), dict(id="high")])]
        self.reply, self.text_key, self.honour_hostile, self.healthy, self.attach_ok = "OK", "content", False, True, True
        self.extra_assistants, self.cost, self.trust_attach = 0, 0, False
        self.utilizations, self.resets_at = [], int(time.time()) + 3000  # native.rateLimit per prompt, in order
        self.attached, self.unobserved = set(), set()
        self.hang, self.aborted, self.options = False, [], None  # hang: prompts never go idle until aborted
        self.agents = ["build"]  # what GET /api/agent lists
        self.connections = {}
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "server.log").write_text("server password %s\nserver listening\n" % self._password)
        for name, value in knobs.items():
            if not hasattr(self, name):
                raise TypeError("unknown knob %s" % name)
            setattr(self, name, value)
        self.stores = (settings, ledger)
        if self.honour_hostile:
            self.load_hostile()

    def load_hostile(self):
        """What a server that honoured project and global config would do: list and load the hostile plugin."""
        roots = dict(project=Path(self.directory), glob=Path(self.environ.get("HOME", "/nonexistent")) / ".config" / "opencode")
        for name, root in roots.items():
            config = root / "opencode.json"
            if config.is_file() and self.honour_hostile in (True, name):
                value = json.loads(config.read_text())
                source = (Path(value["plugins"][0]["package"]) / "index.ts").read_text()
                self.plugins.append(source.split('id: "', 1)[1].split('"', 1)[0])
                self.mcp.update(value.get("mcp") or {})
                path = source.split("writeFileSync(", 1)[1].split(",", 1)[0]
                Path(json.loads(path)).write_text("loaded")

    def stop(self):
        self.stopped = True

    # ------------------------------------------------------------ RPC
    def rpc(self, method, payload):
        if method == "health":
            return (200, dict(state="ok", managed_denies=list(adapter.MANAGED_DENIES))) if self.healthy else (500, None)
        self.tokens.append(payload.get("authority"))
        if method == "attach" and self.trust_attach:  # no ledger to check against (a routing double)
            self.attached.add(payload["session_id"])
            if payload["session_id"] in self.sessions:
                self.unobserved.add(payload["session_id"])  # created before attach: the shim never saw it
            return 200, dict(state="attached")
        if method == "attach":
            parsed = attach_route(payload["model"])
            if parsed is None:
                return 500, None  # the shim's `invalid_attach`
            facts = dict(parsed, harness=routing_launch.shim_harness(parsed["provider"]), kind="primary",
                         funding_pool=payload["funding_pool"])
            from scripts import routing as command
            shim = command.Service(*self.stores, serving=True)  # per call: the launch may run in another thread
            try:
                answer = shim.handle(json.dumps(dict(v=1, op="check", attempt_id=payload["attempt_id"],
                                                     session_id=payload["session_id"], authority=payload["authority"],
                                                     request=facts)).encode())
            finally:
                shim.close()
            ok = self.attach_ok and answer.get("authority") == "verified"
            if ok:
                self.attached.add(payload["session_id"])
            return (200, dict(state="attached")) if ok else (500, None)
        if method == "descendants":
            root = payload["session_id"]
            if not payload.get("authority") or (self.trust_attach and root not in self.attached):
                return 500, None
            if root in self.unobserved:
                return 200, dict(state="ok", root=root, descendants=[], truncated=False, complete=False,
                                 reason="root_creation_unobserved")
            children = [dict(session_id=sid, parent_session_id=root) for sid, item in self.sessions.items()
                        if item["parent"] == root]
            return 200, dict(state="ok", root=root, descendants=children, truncated=False, complete=True, reason=None)
        self.attached.discard(payload.get("session_id"))
        return 200, dict(state="detached")

    def session_tokens(self, sid):
        count = sum(1 for m in self.sessions[sid]["messages"] if m.get("type") == "assistant")
        return dict(input=3 * count, output=count, reasoning=0, cache=dict(read=0, write=0))

    # ------------------------------------------------------------ HTTP
    def request(self, method, path, body=None, query=None):
        now, parts = int(time.time() * 1000) + 2000 * self.prompts, path.strip("/").split("/")  # launches seconds apart
        tokens = dict(input=3, output=1, reasoning=0, cache=dict(read=0, write=0))
        if path == "/api/info":
            return 200, dict(version=adapter.QUALIFIED_VERSION)
        if path == "/api/plugin":
            return 200, dict(location=dict(directory=self.directory),
                             data=[dict(id=plugin, source=dict(type="sdk"), features=dict(server=True),
                                        state=dict(status="active")) for plugin in self.plugins])
        if path == "/api/agent":
            return 200, dict(data=[agent if isinstance(agent, dict) else dict(name=agent) for agent in self.agents])
        if path == "/api/mcp":
            return 200, dict(data=self.mcp)
        if path == "/api/config":
            return 200, dict(data=self.config)
        if path == "/api/model":
            return 200, dict(data=self.models)
        if path == "/api/location/reload":
            return 200, None
        if parts[:2] == ["api", "integration"]:
            if len(parts) == 3:
                return 200, dict(data=dict(methods=[], connections=self.connections.get(parts[2], [])))
            self.connections.setdefault(parts[2], []).append(dict(label="connected"))
            return 200, dict(data={})
        if path == "/api/session/active":
            busy = {sid: dict(type="busy") for sid, item in self.sessions.items()
                    if self.hang and item["messages"] and sid not in self.aborted}
            return 200, dict(data=busy)
        if path == "/api/session":
            if method == "POST":
                self.created += 1
                sid = body.get("id") or "ses_fake%04d" % self.created
                self.sessions[sid] = dict(messages=[], model=body.get("model"), parent=body.get("parentID"))
                return 200, dict(data=dict(id=sid))
            return 200, dict(data=[dict(id=sid, parentID=item["parent"]) for sid, item in self.sessions.items()])
        sid = parts[2]
        if len(parts) == 3:
            spent = self.cost * sum(1 for m in self.sessions[sid]["messages"] if m.get("type") == "assistant")
            return 200, dict(data=dict(id=sid, parentID=self.sessions[sid]["parent"], tokens=self.session_tokens(sid),
                                       cost=spent))
        if parts[3] == "abort":
            self.aborted.append(sid)
            self.sessions[sid]["messages"].append(dict(id="msg_fakeabortidle%04d" % len(self.aborted), type="idle",
                                                       outcome="aborted", time=dict(created=now)))
            return 200, dict(data=True)
        if parts[3] == "permission":
            return 200, dict(data=dict(effect=self.effects.get(body["action"], "deny")))
        if parts[3] in ("prompt", "command", "shell"):
            self.prompts += 1
            model = self.sessions[sid]["model"] or {}
            reply = self.reply(body.get("text", "")) if callable(self.reply) else self.reply
            assistant = dict(id="msg_fakeassistant%04d" % self.prompts, type="assistant", finish="stop", cost=self.cost,
                             tokens=tokens, model=dict(id=model.get("id"), providerID=model.get("providerID"),
                                                       variant=model.get("variant")),
                             time=dict(created=now, completed=now + 1))
            assistant[self.text_key] = [dict(type="text", text=reply)]
            if self.utilizations:
                used = self.utilizations.pop(0)
                assistant["native"] = dict(rateLimit=dict(status="allowed", rateLimitType="five_hour", resetsAt=self.resets_at,
                                                          unifiedWindows=dict(five_hour=dict(utilization=used,
                                                                                             resetsAt=self.resets_at,
                                                                                             status="allowed"))))
            extra = [dict(assistant, id="msg_fakeextra%04d" % i) for i in range(self.extra_assistants)]
            self.sessions[sid]["messages"] += [dict(id=body.get("id") or "msg_fakeuser%04d" % self.prompts, type="user",
                                                    time=dict(created=now)), assistant, *extra]
            if not self.hang:
                self.sessions[sid]["messages"].append(dict(id="msg_fakeidle%04d" % self.prompts, type="idle",
                                                           outcome="succeeded", time=dict(created=now + 2)))
            return 200, dict(data=dict(id=body.get("id")))
        page = [] if query and "cursor" in query else list(self.sessions.get(sid, {}).get("messages", []))
        return 200, dict(data=page, cursor=dict(previous=None, next=None if not page else "n"))


class FakeEnv:
    """The probes' environment: every started host is kept for assertions; the launch is the real launcher's."""

    def __init__(self, base, **knobs):
        self.base, self.knobs, self.hosts, self.environs, self.signins = Path(base), knobs, [], [], 0
        self.per_probe = {}

    def start(self, project, settings, ledger, environ):
        self.environs.append(dict(environ))
        probe = Path(project).name if Path(project).parent.name == "projects" else Path(project).parent.name
        knobs = {k: v for k, v in dict(self.knobs, **self.per_probe.get(probe, {})).items() if k != "codex_windows"}
        host = FakeLauncherHost(project, self.base / ("workdir-%d" % len(self.hosts)), settings, ledger, environ, **knobs)
        host.probe_stores = (settings, ledger)
        self.hosts.append(host)
        return host

    def unauthenticated(self, host, path):
        return host.unauth

    def launch(self, checkout, **kwargs):
        from runtime import opencode_launcher
        return opencode_launcher.launch(checkout, **kwargs)

    def sleep(self, _seconds):
        return None

    def signin(self, host, note):
        self.signins += 1
        note("signin_polls", 1)
        return dict(status="complete")

    def which(self, name, path):
        return "/usr/local/bin/%s" % name

    def codex_window(self):
        """The Codex reader stand-in: `{windows}` from the scripted list, else the reader's `codex_unavailable`."""
        self.codex_reads = getattr(self, "codex_reads", 0) + 1
        windows = self.knobs.get("codex_windows")
        return dict(windows=windows.pop(0)) if windows else dict(reason="codex_unavailable")


class HttpFake:
    """Serves a FakeLauncherHost over loopback HTTP with Basic auth, so a child process can reach it through the
    launcher's own `ServerHost`. `port`, `_password` and `directory` are what the drill passes to its child."""

    def __init__(self, host):
        import base64
        import http.server
        import threading
        fake, password = host, host._password
        expected = "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def answer(self, method):
                if self.headers.get("Authorization") != expected:
                    self.send_response(401)
                    self.end_headers()
                    return
                import urllib.parse
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                path, _, query = self.path.partition("?")
                query = dict(urllib.parse.parse_qsl(query)) or None
                if path.startswith("/api/rpc/routing/"):
                    status, output = fake.rpc(path.rsplit("/", 1)[1], (body or {}).get("input"))
                    payload = dict(output=output) if status == 200 else dict(error="rpc.internal")
                else:
                    status, payload = fake.request(method, path, body=body, query=query)
                raw = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                self.answer("GET")

            def do_POST(self):  # noqa: N802
                self.answer("POST")

            def do_DELETE(self):  # noqa: N802
                self.answer("DELETE")

            def log_message(self, *_):
                pass

        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port, self._password, self.directory, self.fake = self.http.server_address[1], password, host.directory, host
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def request(self, method, path, body=None, query=None):
        return self.fake.request(method, path, body=body, query=query)

    def rpc(self, method, payload):
        return self.fake.rpc(method, payload)

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.fake.stop()
