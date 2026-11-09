#!/usr/bin/env python3
"""Live qualification of the Smart Routing stack on a real, isolated OpenCode 2.2.6 server.

This is an operator tool, not a unit test (its name keeps unittest discovery from collecting it). It acts as the
managed launcher of the OpenCode contract (clients/opencode.md#handshake) and runs staged checks:

  0  server + plugin + real `routing.py --serve` child: health before and after `prepare`, `authority_required`,
     `status` (no model call)
  1  P0.7: derived session and message ids accepted, one request per derived message id, duplicate send, read-back,
     the shape of OpenCode's own ids, message ordering (at most 1 call)
  2  shim handshake on a real session: dispatch, attach, the allowed request, blocked paths, detach, an interactive
     session with the routing child killed
  3  P0.8: re-read accounting, title by subtraction, event sequence tracking, `record complete`, a clean `status`
  4  lost acknowledgment: dispatch then a lost launcher, `record uncertain`, operator reconciliation (no model call)

Safety rules it enforces itself:

- It refuses to run unless `--isolated-dir` names an existing, empty, owner-only (0700) directory outside the
  user's OpenCode config, data and state directories. The server gets its own XDG config, data, state and cache
  directories there, a random free localhost port and a minimal environment.
- The only model route is `claude-agent/haiku#low`. Two recorder plugins around the routing shim count every
  `model.request` (`qual-pre` before the shim, `qual-post` after it, so `qual-post` sees exactly what the shim let
  through) and `qual-post` blocks, before anything is sent, any other model, any title request not explicitly
  allowed, any session the harness put on hold, and anything beyond the call budget (`--budget`, at most 14).
- It never prints or stores the dispatch token, the server password or credentials; fixtures it writes are passed
  through `Redactor`, which replaces session, message and other ids with placeholders and removes paths and names.

Usage (the isolated directory is removed by the operator afterwards; `--keep` keeps the server for inspection):

  Q=$(mktemp -d) && chmod 700 "$Q"
  python3 -B tests/qualification/opencode_live.py --isolated-dir "$Q" --through 0 --budget 0
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PROVIDER, MODEL, VARIANT = "claude-agent", "haiku", "low"
ROUTE = "%s/%s#%s" % (PROVIDER, MODEL, VARIANT)
MAX_BUDGET = 14
PROMPT = "Reply with exactly: OK"
POOL = "sub"
HARNESS = "claude-agent-sdk via opencode"
REQUEST = dict(provider=PROVIDER, model=MODEL, variant=VARIANT, harness=HARNESS, kind="primary", funding_pool=POOL)
PROTECTED = (".config/opencode", ".local/share/opencode", ".local/state/opencode", ".cache/opencode")
DERIVED_SESSION = re.compile(r"ses_[0-9a-f]{32}")
DERIVED_MESSAGE = re.compile(r"msg_[0-9a-f]{32}")
ID_TOKEN = re.compile(r"\b(ses|msg|prt|evt|cred|per|toolu|call|prj|loc)_[A-Za-z0-9]{6,}\b")
PROBE_ENV = "QUAL_PROBE_TOKEN"


class Refused(Exception):
    """A precondition of the safety rules failed; nothing was started."""


class BudgetExceeded(Exception):
    pass


class Blocker(Exception):
    """A stage exposed a blocker: stop and report."""


# ---------------------------------------------------------------- pure parts (tests/test_qualification_harness.py)

def check_model(ref):
    """Accept only the qualification route, `claude-agent/haiku` with no variant or `#low`; refuse anything else."""
    if type(ref) is not str:
        raise Refused("model must be text")
    if ref not in ("%s/%s" % (PROVIDER, MODEL), ROUTE):
        raise Refused("only %s is allowed" % ROUTE)
    return ROUTE


def check_isolated_dir(path, home=None):
    """The resolved isolated directory, or `Refused`: it must exist, be empty, owner-only and outside OpenCode's
    user directories (and not be the home directory itself)."""
    if not path:
        raise Refused("an explicit --isolated-dir is required")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise Refused("--isolated-dir must be absolute")
    if not candidate.is_dir() or candidate.is_symlink():
        raise Refused("--isolated-dir must be an existing directory, not a symlink")
    resolved = candidate.resolve()
    home = Path(home or Path.home()).resolve()
    if resolved == home or resolved in home.parents:
        raise Refused("--isolated-dir must not be the home directory or above it")
    for protected in PROTECTED:
        guarded = home / protected
        if resolved == guarded or guarded in resolved.parents:
            raise Refused("--isolated-dir must be outside the user's OpenCode directories")
    info = resolved.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Refused("--isolated-dir must be owned by this user with mode 0700")
    if any(resolved.iterdir()):
        raise Refused("--isolated-dir must be empty")
    return resolved


class CallBudget:
    """Counts real model requests (transmitted past the shim) against a hard limit; `spend` raises past it."""

    def __init__(self, limit):
        if type(limit) is not int or not 0 <= limit <= MAX_BUDGET:
            raise Refused("budget must be an integer from 0 to %d" % MAX_BUDGET)
        self.limit = limit
        self.entries = []

    @property
    def used(self):
        return sum(entry["calls"] for entry in self.entries)

    @property
    def remaining(self):
        return self.limit - self.used

    def allow(self, calls):
        """Whether `calls` more requests fit; used before an action that may send them."""
        return type(calls) is int and 0 <= calls <= self.remaining

    def spend(self, stage, label, calls):
        if type(calls) is not int or calls < 0:
            raise ValueError("calls must be a non-negative integer")
        if calls > self.remaining:
            self.entries.append(dict(stage=stage, label=label, calls=calls, over_budget=True))
            raise BudgetExceeded("%s: %d calls exceed the remaining budget %d" % (label, calls, self.remaining))
        self.entries.append(dict(stage=stage, label=label, calls=calls))
        return self.remaining


class Redactor:
    """Sanitizes harness output: secrets and paths are removed, ids become stable placeholders (`<ses#1>`)."""

    def __init__(self, secrets=(), paths=(), names=()):
        self.secrets = [value for value in secrets if value]
        self.paths = sorted({str(value) for value in paths if value}, key=len, reverse=True)
        self.names = [value for value in names if value]
        self.ids = {}

    def add_secret(self, value):
        if value:
            self.secrets.append(value)

    def placeholder(self, value):
        prefix = value.split("_", 1)[0]
        if value not in self.ids:
            self.ids[value] = "<%s#%d>" % (prefix, 1 + sum(1 for known in self.ids if known.split("_", 1)[0] == prefix))
        return self.ids[value]

    def text(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "<secret>")
        for path in self.paths:
            value = value.replace(path, "<path>")
        value = ID_TOKEN.sub(lambda match: self.placeholder(match.group(0)), value)
        for name in self.names:
            value = re.sub(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), "<user>", value)
        return value

    def __call__(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(key)): self(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        return value

    def leaks(self, text):
        """Fragments of `text` that should never appear in committed output."""
        found = [secret for secret in self.secrets if secret in text]
        found += [path for path in self.paths if path in text]
        found += [match.group(0) for match in ID_TOKEN.finditer(text)]
        found += [name for name in self.names if re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), text)]
        return found


def id_shape(value):
    """The shape of an id without its value: prefix, body length, character classes and derived-shape match."""
    if type(value) is not str or "_" not in value:
        return dict(prefix=None, body_length=None, classes=[], derived_shape=False)
    prefix, body = value.split("_", 1)
    classes = [name for name, pattern in (("digit", r"[0-9]"), ("lower", r"[a-z]"), ("upper", r"[A-Z]"))
               if re.search(pattern, body)]
    derived = (DERIVED_SESSION if prefix == "ses" else DERIVED_MESSAGE).fullmatch(value) is not None
    return dict(prefix=prefix, body_length=len(body), classes=classes, lower_hex_only=bool(re.fullmatch(r"[0-9a-f]+", body)),
                derived_shape=derived)


def latency_pairs(records):
    """Shim latency per hook: each `qual-pre` record matched with the next `qual-post` record of the same hook and
    session (FIFO). Unmatched pre records were blocked by the shim (or the post guard never saw them)."""
    waiting, pairs, blocked = {}, [], []
    for record in records:
        key = (record.get("hook"), record.get("session"))
        if record.get("phase") == "pre":
            waiting.setdefault(key, []).append(record)
        elif record.get("phase") == "post" and waiting.get(key):
            before = waiting[key].pop(0)
            pairs.append(dict(hook=record["hook"], kind=record.get("kind"), ms=round(record["t"] - before["t"], 3)))
    for queue in waiting.values():
        blocked.extend(queue)
    return pairs, blocked


def summarize_ms(values):
    values = sorted(values)
    if not values:
        return None
    pick = lambda q: values[min(len(values) - 1, int(round(q * (len(values) - 1))))]  # noqa: E731
    return dict(n=len(values), median_ms=round(pick(0.5), 3), p95_ms=round(pick(0.95), 3), max_ms=round(values[-1], 3))


# ---------------------------------------------------------------- recorder plugins (written into the isolated dir)

RECORDER = r"""
// Qualification recorder (__PHASE__): logs hook facts as JSON lines to the isolated dir. Never logs headers,
// environment values or prompt text. `post` runs after the routing shim and is the last gate before sending.
import { appendFileSync, readFileSync } from "node:fs"
const PHASE = "__PHASE__"
export default {
  id: "qual-__PHASE__",
  async setup(ctx) {
    const o = ctx?.options ?? {}
    const log = (record) => { try { appendFileSync(o.log, JSON.stringify({ ...record, phase: PHASE, t: performance.now(), wall: Date.now() }) + "\n") } catch {} }
    const control = () => { try { return JSON.parse(readFileSync(o.control, "utf8")) } catch { return { budget: 0 } } }
    const sent = () => { try { return readFileSync(o.log, "utf8").split("\n").filter((line) => line.includes('"decision":"transmit"')).length } catch { return 1e9 } }
    const titles = () => { try { return readFileSync(o.log, "utf8").split("\n").filter((line) => line.includes('"decision":"transmit"') && line.includes('"kind":"title"')).length } catch { return 1e9 } }
    const guard = (reason, record) => { log({ ...record, decision: reason }); throw new Error(`qualification guard (${reason})`) }
    await ctx.session.hook("model.request", async (event) => {
      const m = event?.model ?? {}
      const record = { hook: "model.request", kind: event?.kind ?? null, agent: event?.agent ?? null, provider: m.providerID ?? null,
        model: m.id ?? null, variant: m.variant ?? null, session: event?.sessionID ?? null }
      if (PHASE === "pre") return log(record)
      const c = control()
      if (m.providerID !== "__PROVIDER__" || m.id !== "__MODEL__") guard("guard_model", record)
      if ((c.hold ?? []).includes(event?.sessionID)) guard("guard_hold", record)
      if (event?.kind === "title" && !(c.title_session === event?.sessionID && titles() < 1)) guard("guard_title", record)
      if (event?.kind !== "primary" && event?.kind !== "title") guard("guard_kind", record)
      if (sent() >= (c.budget ?? 0)) guard("guard_budget", record)
      log({ ...record, decision: "transmit" })
    })
    await ctx.tool.hook("execute.before", async (event) => {
      const record = { hook: "tool.execute.before", tool: event?.tool ?? null, session: event?.sessionID ?? null,
        spawn_model: typeof event?.input?.model === "string" ? event.input.model : null }
      if (PHASE === "pre") return log(record)
      if (record.tool === "subagent" && record.spawn_model !== null && !record.spawn_model.startsWith("__PROVIDER__/__MODEL__")) guard("guard_spawn_model", record)
      log({ ...record, decision: "passed" })
    })
    await ctx.permission.hook("evaluate", async (event) => {
      log({ hook: "permission.evaluate", action: event?.action ?? null, effect: event?.effect ?? null, session: event?.sessionID ?? null })
    })
    await ctx.shell.hook("create.before", (event) => {
      const env = event?.env
      log({ hook: "shell.create.before", session: null, env_count: env && typeof env === "object" ? Object.keys(env).length : null,
        probe_present: !!(env && typeof env === "object" && Object.prototype.hasOwnProperty.call(env, "__PROBE__")) })
    })
  },
}
"""

SHIM_WRAPPER = 'export { default } from %s\n'


def recorder_source(phase):
    return (RECORDER.replace("__PHASE__", phase).replace("__PROVIDER__", PROVIDER).replace("__MODEL__", MODEL)
            .replace("__PROBE__", PROBE_ENV))


# ---------------------------------------------------------------- isolated server and clients

def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Server:
    """One `opencode serve` on a random localhost port with its own XDG dirs; config and plugins in the dir."""

    def __init__(self, base, budget):
        self.base = base
        self.project = (base / "project")
        self.port = free_port()
        self.process = None
        self.password = None
        self.directory = None
        self.log = base / "hooks.jsonl"
        self.control_path = base / "control.json"
        self.settings = base / "routing" / "settings.json"
        self.ledger = base / "usage" / "organizations.sqlite3"
        self.control = dict(budget=0, hold=[], title_session=None)
        self.budget = budget

    def write_control(self, **change):
        self.control.update(change)
        temporary = self.control_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.control))
        os.replace(temporary, self.control_path)

    def prepare_files(self):
        for name in ("config", "data", "state", "cache", "tmp", "project", "plugins/qual-pre", "plugins/qual-post",
                     "plugins/routing", "routing", "usage"):
            (self.base / name).mkdir(parents=True, mode=0o700, exist_ok=True)
        (self.base / "plugins/qual-pre/index.ts").write_text(recorder_source("pre"))
        (self.base / "plugins/qual-post/index.ts").write_text(recorder_source("post"))
        shim = ROOT / "runtime" / "adapters" / "opencode_plugin.ts"
        (self.base / "plugins/routing/index.ts").write_text(SHIM_WRAPPER % json.dumps(str(shim)))
        table = self.settings.parent / "strategy-table.json"
        table.write_text(json.dumps({"schema": 1, "version": "qual-1", "rows": {"worker|code|medium|medium": [
            {"strategy_id": "s-sub", "model": MODEL, "effort": VARIANT, "harness": HARNESS, "evidence_refs": ["qual"]}]}}))
        table.chmod(0o600)
        self.write_control(budget=0)
        recorder = dict(log=str(self.log), control=str(self.control_path))
        config = {
            "$schema": "https://opencode.ai/config.json",
            "model": "%s/%s" % (PROVIDER, MODEL),
            "plugins": [
                {"package": str(self.base / "plugins/qual-pre"), "options": recorder},
                {"package": str(self.base / "plugins/routing"),
                 "options": {"python": "python3", "settings": str(self.settings), "ledger": str(self.ledger)}},
                {"package": str(self.base / "plugins/qual-post"), "options": recorder},
            ],
            # A background child session on another model: the spawn test of stage 2 (blocked before sending).
            "commands": {"qualspawn": {"template": PROMPT, "model": "%s/sonnet#%s" % (PROVIDER, VARIANT),
                                       "subagent": True, "agent": "general"}},
        }
        (self.project / "opencode.json").write_text(json.dumps(config, indent=2))

    def environment(self):
        env = {"HOME": str(Path.home()), "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
               "USER": os.environ.get("USER", ""), "LANG": "en_US.UTF-8", "TMPDIR": str(self.base / "tmp"),
               "XDG_CONFIG_HOME": str(self.base / "config"), "XDG_DATA_HOME": str(self.base / "data"),
               "XDG_STATE_HOME": str(self.base / "state"), "XDG_CACHE_HOME": str(self.base / "cache"),
               "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
               PROBE_ENV: "dummy-not-a-secret"}
        return env

    def start(self):
        self.prepare_files()
        out = open(self.base / "server.log", "wb")
        self.process = subprocess.Popen(["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(self.port)],
                                        cwd=self.project, env=self.environment(), stdout=out, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + 30
        while time.time() < deadline:
            text = (self.base / "server.log").read_text(errors="replace")
            match = re.search(r"^server password (\S+)$", text, re.M)
            if match and "server listening" in text:
                self.password = match.group(1)
                break
            if self.process.poll() is not None:
                raise Blocker("server exited during start-up")
            time.sleep(0.2)
        else:
            raise Blocker("server did not start")
        self.directory = os.path.realpath(self.project)

    def stop(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)

    # -- HTTP
    def request(self, method, path, body=None, query=None, timeout=60):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        import base64
        headers = {"Authorization": "Basic " + base64.b64encode(("opencode:" + self.password).encode()).decode(),
                   "x-opencode-directory": self.directory, "accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        if query:
            path += "?" + urllib.parse.urlencode(query)
        began = time.perf_counter()
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        elapsed = (time.perf_counter() - began) * 1000
        connection.close()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = {"_unparsed": len(raw)}
        return response.status, parsed, round(elapsed, 2)

    def rpc(self, method, payload):
        """`(http, output, ms)`: the plugin method's `output`, or the error body when the call failed."""
        status, body, ms = self.request("POST", "/api/rpc/routing/%s" % method, {"input": payload})
        return status, (body.get("output", body) if isinstance(body, dict) else body), ms

    def hooks(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def transmitted(self):
        return [record for record in self.hooks() if record.get("decision") == "transmit"]


class EventStream(threading.Thread):
    """Reads `/api/event` (server-sent events) into memory; raw records stay in memory only."""

    def __init__(self, server):
        super().__init__(daemon=True)
        self.server = server
        self.events = []
        self.error = None
        self.stopped = False

    def run(self):
        import base64
        try:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=600)
            headers = {"Authorization": "Basic " + base64.b64encode(("opencode:" + self.server.password).encode()).decode(),
                       "x-opencode-directory": self.server.directory, "accept": "text/event-stream"}
            connection.request("GET", "/api/event", headers=headers)
            response = connection.getresponse()
            self.status = response.status
            buffer = []
            while not self.stopped:
                line = response.fp.readline()
                if not line:
                    break
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    buffer.append(line[5:].strip())
                elif line == "" and buffer:
                    try:
                        self.events.append(json.loads("\n".join(buffer)))
                    except ValueError:
                        self.events.append({"_unparsed": True})
                    buffer = []
        except Exception as error:  # noqa: BLE001 - recorded as a fact, never raised into the stages
            self.error = type(error).__name__


class Routing:
    """One-shot `scripts/routing.py <op>` on the pinned temporary stores."""

    def __init__(self, server):
        self.server = server

    def __call__(self, op, **body):
        began = time.perf_counter()
        done = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts" / "routing.py"), op,
                               "--settings", str(self.server.settings), "--ledger", str(self.server.ledger)],
                              input=json.dumps(dict(v=1, op=op, **body)), capture_output=True, text=True, timeout=60)
        elapsed = round((time.perf_counter() - began) * 1000, 2)
        try:
            return json.loads(done.stdout), elapsed
        except ValueError:
            return dict(state="unparsed", code=done.returncode), elapsed


def prepare_request(attempt_id, task):
    return dict(
        assignment=dict(attempt_id=attempt_id, task=task, role="implementer", scope="qual-scope"),
        job=dict(id="job-" + attempt_id, role="worker", scope_ref="qual-scope", objective="o", context="c", profile=dict(
            task_family="code", complexity_band="medium", risk_band="medium", review_policy="independent",
            context_tokens=100, tools=["edit"], uncertainty="low")),
        observations=dict(now=int(time.time()), routes=[dict(
            route="sub", strategy_id="s-sub", provider=PROVIDER, model=MODEL, effort=VARIANT, harness=HARNESS,
            account="anthropic:qual", billing_class="included_subscription", pool=POOL, demand={"5h": 10},
            capable=True, permitted=True, binding_enforceable=True)],
            pools={POOL: dict(windows=[dict(id="5h", limit=1000, used=100, length=18000, reset_at=None, rolling=True,
                                            fresh=True)])}))


def status_view(status):
    ledger = (status or {}).get("ledger") or {}
    keys = ("state", "holds", "open_attempts", "authority_required", "unstamped_open_holds", "unbound_open_holds",
            "cost_unknown", "uncertain_dollar", "blocked_organizations", "reconcile_hint", "overrun_backlog")
    return dict(state=(status or {}).get("state"), settings_state=(status or {}).get("settings_state"),
                ledger={key: ledger.get(key) for key in keys if key in ledger})


# ---------------------------------------------------------------- the run

class Run:
    def __init__(self, base, budget, through):
        self.base = base
        self.budget = CallBudget(budget)
        self.through = through
        self.server = Server(base, budget)
        self.routing = Routing(self.server)
        self.results = dict(stages={}, ledger=[], blockers=[], deviations=[])
        self.redact = Redactor(paths=[str(base), os.path.realpath(base), str(base).replace("/private/", "/", 1), str(ROOT),
                                      str(Path.home())],
                               names=[os.environ.get("USER", "")])
        self.events = None
        self.token = None

    def note(self, stage, key, value):
        self.results["stages"].setdefault(stage, {})[key] = value

    def account(self, stage, label, before):
        """Charge the budget with the requests transmitted since `before` (the post recorder's count)."""
        sent = len(self.server.transmitted()) - before
        self.budget.spend(stage, label, sent)
        self.results["ledger"].append(dict(stage=stage, label=label, calls=sent))
        return sent

    def permit(self, calls):
        """Raise the post recorder's ceiling to `calls` more transmissions, never past the budget."""
        if not self.budget.allow(calls):
            raise BudgetExceeded("planned %d calls exceed the remaining budget %d" % (calls, self.budget.remaining))
        self.server.write_control(budget=len(self.server.transmitted()) + calls)

    def wait_idle(self, session, after_ms, timeout=120):
        """Until the session is not active and has an idle record created after `after_ms`."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, active, _ = self.server.request("GET", "/api/session/active")
            listed = json.dumps(active) if status == 200 else ""
            messages = self.messages(session)
            idle = [m for m in messages if m.get("type") == "idle" and ((m.get("time") or {}).get("created") or 0) >= after_ms]
            if idle and session not in listed:
                return messages
            time.sleep(0.5)
        raise Blocker("session did not become idle")

    def messages(self, session, limit=None):
        """Every page of a session's messages (ascending); records whether paging was exhaustive."""
        found, cursor, pages = [], None, 0
        while True:
            query = {} if cursor else {"order": "asc"}  # the cursor carries the order; sending both is HTTP 400
            if limit:
                query["limit"] = str(limit)
            if cursor:
                query["cursor"] = cursor
            status, body, _ = self.server.request("GET", "/api/session/%s/message" % session, query=query)
            if status != 200:
                raise Blocker("message list failed with HTTP %d" % status)
            pages += 1
            data = (body.get("data") if isinstance(body, dict) else body) or []
            found.extend(data)
            # `cursor` is `{previous, next}`; `next` stays set on the last page, so paging ends on an empty page.
            cursor = ((body.get("cursor") or {}).get("next")) if isinstance(body, dict) else None
            if not data or not cursor or pages > 200:
                self.last_pages = pages
                return found

    def create_session(self, session_id=None, title=None, permissions=None):
        body = {"model": {"id": MODEL, "providerID": PROVIDER, "variant": VARIANT},
                "location": {"directory": self.server.directory}}
        if session_id:
            body["id"] = session_id
        if title:
            body["title"] = title
        body["permissions"] = permissions if permissions is not None else [{"action": "*", "resource": "*", "effect": "deny"}]
        return self.server.request("POST", "/api/session", body)

    # ------------------------------------------------------------ stage 0
    def stage0(self):
        s = "0"
        self.server.start()
        self.redact.add_secret(self.server.password)
        self.note(s, "server", dict(version=self.server.request("GET", "/api/info")[1].get("version"), port="<random>"))
        for _ in range(20):  # the provider's integration registers shortly after start-up
            if self.server.request("GET", "/api/integration/claude-agent")[0] == 200:
                break
            time.sleep(0.5)
        status, _, _ = self.server.request("POST", "/api/integration/claude-agent/connect/external",
                                           {"methodID": "claude-login"})
        self.note(s, "claude_agent_connect_http", status)
        if status not in (200, 204):
            raise Blocker("could not connect claude-agent through its external Claude Code login method")
        self.server.request("POST", "/api/location/reload")
        listed = []
        for _ in range(30):
            time.sleep(0.5)
            status, models, _ = self.server.request("GET", "/api/model")
            listed = [m for m in (models or {}).get("data", []) if m.get("providerID") == PROVIDER and m.get("id") == MODEL]
            if listed:
                break
        variants = [v if isinstance(v, str) else (v or {}).get("id") for v in (listed[0].get("variants") or [])] if listed else []
        self.note(s, "haiku_listed", dict(listed=bool(listed), low_variant=VARIANT in variants))
        if not listed:
            raise Blocker("claude-agent/haiku is not available on the isolated server")
        status, plugins, _ = self.server.request("GET", "/api/plugin")
        self.note(s, "plugin_list", dict(http=status, ids=sorted({p.get("id") for p in (plugins or {}).get("data", []) if isinstance(p, dict)})))
        self.events = EventStream(self.server)
        self.events.start()
        status, health, ms = self.server.rpc("health", {})
        self.note(s, "health_before_ledger", dict(http=status, body=health, ms=ms))
        if status != 200 or (health or {}).get("state") != "not_ready":
            self.results["deviations"].append("stage 0: health before any ledger was not not_ready")
        answer, ms = self.routing("set", authority_required=True)
        self.note(s, "set_authority_required", dict(answer=answer, ms=ms))
        answer, ms = self.routing("prepare", **prepare_request("qual-a1", "task-qual"))
        admitted = answer.get("state") == "admitted"
        self.note(s, "prepare", dict(state=answer.get("state"), reason=answer.get("reason"), ms=ms,
                                     route=(answer.get("admission") or {}).get("route"),
                                     billing_class=(answer.get("admission") or {}).get("billing_class")))
        if not admitted:
            raise Blocker("prepare did not admit the qualification attempt")
        status, health, ms = self.server.rpc("health", {})
        self.note(s, "health_after_prepare", dict(http=status, body=health, ms=ms))
        if status != 200 or (health or {}).get("state") != "ok":
            raise Blocker("routing.health was not ok after prepare")
        answer, ms = self.routing("status")
        self.note(s, "status", dict(view=status_view(answer), ms=ms))
        ledger = answer.get("ledger") or {}
        if ledger.get("authority_required") is not True or ledger.get("unstamped_open_holds") != 0:
            raise Blocker("status does not show authority_required with zero unstamped open holds")
        # Shapes of the server's API, without values: an empty titled session and its message list.
        status, created, _ = self.create_session(title="qual shape probe")
        session = ((created or {}).get("data") or created or {}).get("id")
        self.note(s, "session_create_keys", dict(http=status, top=sorted((created or {}).keys()),
                                                 data=sorted(((created or {}).get("data") or {}).keys())))
        status, listing, _ = self.server.request("GET", "/api/session/%s/message" % session, query={"order": "asc"})
        self.note(s, "message_list_keys", dict(http=status, top=sorted((listing or {}).keys()) if isinstance(listing, dict) else type(listing).__name__))
        time.sleep(1)
        self.note(s, "event_stream", dict(status=getattr(self.events, "status", None), error=self.events.error,
                                          events=len(self.events.events),
                                          types=sorted({e.get("type") for e in self.events.events if isinstance(e, dict)})[:20],
                                          keys=sorted({k for e in self.events.events[:5] if isinstance(e, dict) for k in e})))
        self.note(s, "hooks_seen", sorted({(r["phase"], r["hook"]) for r in self.server.hooks()}))
        self.note(s, "calls", 0)

    # ------------------------------------------------------------ helpers for the live stages
    def records(self, since, session=None, hook=None, phase=None):
        return [r for r in self.server.hooks()[since:]
                if (session is None or r.get("session") == session) and (hook is None or r.get("hook") == hook)
                and (phase is None or r.get("phase") == phase)]

    @staticmethod
    def reason(body):
        """The fixed reason code in an error body (`routing: blocked (x)` or a bare code), never the body itself."""
        text = json.dumps(body)
        match = re.search(r"routing: blocked \(([a-z0-9_]+)\)", text) or re.search(r"qualification guard \(([a-z_]+)\)", text)
        return match.group(1) if match else None

    @staticmethod
    def data(body):
        return body.get("data", body) if isinstance(body, dict) else body

    def prompt(self, session, message_id=None, text=PROMPT):
        body = {"text": text}
        if message_id:
            body["id"] = message_id
        return self.server.request("POST", "/api/session/%s/prompt" % session, body)

    def outcome(self, messages, after_ms):
        idle = [m for m in messages if m.get("type") == "idle" and ((m.get("time") or {}).get("created") or 0) >= after_ms]
        last = idle[-1] if idle else {}
        return dict(outcome=last.get("outcome"), reason=self.reason(last))

    def blocked_prompt(self, stage, label, session, expect):
        """Send a prompt that the shim must block; record the reason, invocations and that nothing was sent."""
        mark, before, began = len(self.server.hooks()), len(self.server.transmitted()), int(time.time() * 1000)
        self.permit(0)
        status, _, ms = self.prompt(session)
        messages = self.wait_idle(session, began)
        sent = self.account(stage, label, before)
        invoked = self.records(mark, session=session, hook="model.request", phase="pre")
        assistant = [m for m in messages if m.get("type") == "assistant" and ((m.get("time") or {}).get("created") or 0) >= began]
        result = dict(http=status, sent=sent, pre_invocations=len(invoked), kinds=sorted({r["kind"] for r in invoked}),
                      assistant_messages=len(assistant), **self.outcome(messages, began))
        result["pass"] = sent == 0 and not assistant and result["reason"] == expect
        if sent:
            raise Blocker("%s: a request was transmitted when it must be blocked" % label)
        if not result["pass"]:
            self.results["deviations"].append("%s: expected %s, observed %s" % (label, expect, result["reason"]))
        return result

    # ------------------------------------------------------------ stage 1 (P0.7)
    def stage1(self):
        s = "1"
        import secrets as random_source
        from runtime import routing as core
        identity = "qual-identity-" + random_source.token_hex(8)
        ids = core.derive_ids("qual-p07", identity, random_source.token_hex(16))
        session, message = ids["session_id"], ids["message_id"]
        # (a) a derived session id and message id, one prompt: accepted, exactly one model request.
        status, created, ms = self.create_session(session, title="qual p07")
        accepted = status == 200 and (self.data(created) or {}).get("id") == session
        self.note(s, "a_create_derived_session", dict(http=status, accepted_same_id=accepted, ms=ms))
        if not accepted:
            raise Blocker("the server did not accept the derived session id")
        mark, before, began = len(self.server.hooks()), len(self.server.transmitted()), int(time.time() * 1000)
        self.permit(1)
        status, body, ms = self.prompt(session, message)
        returned = (self.data(body) or {}).get("id") if isinstance(self.data(body), dict) else None
        messages = self.wait_idle(session, began)
        self.permit(0)
        sent = self.account(s, "P0.7 (a) prompt with derived ids", before)
        invoked = self.records(mark, session=session, hook="model.request", phase="pre")
        users = [m for m in messages if m.get("type") == "user"]
        assistants = [m for m in messages if m.get("type") == "assistant"]
        self.note(s, "a_prompt", dict(http=status, ms=ms, response_keys=sorted(self.data(body).keys()) if isinstance(self.data(body), dict) else None,
                                      response_id_is_derived=returned == message, user_message_ids_derived=[m.get("id") == message for m in users],
                                      model_request_invocations=len(invoked), kinds=sorted(r["kind"] for r in invoked), transmitted=sent,
                                      assistant_messages=len(assistants), message_types=[m.get("type") for m in messages],
                                      **self.outcome(messages, began)))
        if not any(m.get("id") == message for m in users):
            raise Blocker("the server did not store the prompt under the derived message id")
        if sent != 1 or len(invoked) != 1:
            raise Blocker("one prompt with a derived message id caused %d transmitted requests (%d hook invocations)" % (sent, len(invoked)))
        # (b) the same prompt again with the same message id: no second request, the original message returned.
        mark, before, began = len(self.server.hooks()), len(self.server.transmitted()), int(time.time() * 1000)
        self.server.write_control(hold=[session])  # a second request would be blocked here, and counted as invoked
        status, body, ms = self.prompt(session, message)
        time.sleep(4)
        again = self.messages(session)
        self.server.write_control(hold=[])
        sent = self.account(s, "P0.7 (b) duplicate prompt", before)
        invoked = self.records(mark, session=session, hook="model.request", phase="pre")
        returned = (self.data(body) or {}).get("id") if isinstance(self.data(body), dict) else None
        self.note(s, "b_duplicate", dict(http=status, ms=ms, returned_original_id=returned == message,
                                         model_request_invocations=len(invoked), transmitted=sent,
                                         messages_before=len(messages), messages_after=len(again),
                                         same_message_ids=[m.get("id") for m in again] == [m.get("id") for m in messages]))
        if invoked or sent:
            raise Blocker("the duplicate prompt caused a second model request")
        # (c) read the message back by its derived id.
        status, body, ms = self.server.request("GET", "/api/session/%s/message/%s" % (session, message))
        self.note(s, "c_read_back", dict(http=status, ms=ms, same_id=(self.data(body) or {}).get("id") == message,
                                         type=(self.data(body) or {}).get("type")))
        # (d) OpenCode's own ids: a normal session (no call) and the server's message ids from (a).
        status, created, _ = self.create_session(title="qual own id")
        own = (self.data(created) or {}).get("id")
        own_messages = [m.get("id") for m in messages if m.get("id") != message]
        shapes = dict(session=id_shape(own), messages=sorted({json.dumps(id_shape(i), sort_keys=True) for i in own_messages}))
        shapes["messages"] = [json.loads(x) for x in shapes["messages"]]
        self.note(s, "d_own_id_shapes", shapes)
        if id_shape(own)["derived_shape"] or any(id_shape(i)["derived_shape"] for i in own_messages):
            raise Blocker("an OpenCode-generated id has the derived shape")
        # (e) ordering: shell messages (durable history, no model call) with server ids and derived ids among them.
        status, created, _ = self.create_session(title="qual order")
        order = (self.data(created) or {}).get("id")
        self.server.write_control(hold=[order])
        mark, before = len(self.server.hooks()), len(self.server.transmitted())
        low = high = None
        for index in range(400):
            candidate = core.derive_ids("qual-order-%d" % index, identity, None)["message_id"]
            low = low or (candidate if candidate[4] == "0" else None)
            high = high or (candidate if candidate[4] == "f" else None)
            if low and high:
                break
        inserted = []
        for chosen in (None, high, None, low, None):
            body = {"command": "true"}
            if chosen:
                body["id"] = chosen
            status, _, _ = self.server.request("POST", "/api/session/%s/shell" % order, body)
            inserted.append(dict(id=chosen, http=status))
            time.sleep(0.3)
        time.sleep(1)
        listed = [m.get("id") for m in self.messages(order) if m.get("type") == "shell"]
        server_ids = [i for i in listed if i not in (low, high)]
        self.server.write_control(hold=[])
        sent = self.account(s, "P0.7 (e) ordering", before)
        invoked = self.records(mark, session=order, hook="model.request", phase="pre")
        positions = [listed.index(i) if i in listed else None for i in (high, low)]
        self.note(s, "e_ordering", dict(inserted=["derived" if e["id"] else "server" for e in inserted], http=[e["http"] for e in inserted],
                                        listed=["derived" if i in (low, high) else "server" for i in listed],
                                        derived_low_hex_first="0", derived_high_hex_first="f",
                                        derived_positions_high_then_low=positions, insertion_order_kept=positions == [1, 3] and len(listed) == 5,
                                        lexicographic_order=listed == sorted(listed),
                                        server_ids_time_ordered=server_ids == sorted(server_ids),
                                        model_request_invocations=len(invoked), transmitted=sent))
        if positions != [1, 3]:
            self.results["deviations"].append("stage 1 (e): derived message ids are not listed where they were inserted")

    # ------------------------------------------------------------ stage 2 (shim live)
    def listener(self):
        """A local HTTP listener that only counts hits (the webfetch target)."""
        import http.server

        class Count(http.server.BaseHTTPRequestHandler):
            hits = 0

            def do_GET(self):  # noqa: N802
                Count.hits += 1
                self.send_response(204)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Count)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, Count

    def stage2(self):
        s = "2"
        answer, ms = self.routing("dispatch", attempt_id="qual-a1", request=REQUEST)
        if answer.get("state") != "dispatched":
            raise Blocker("dispatch refused: %s" % answer.get("reason"))
        self.token = answer.pop("authority")
        self.redact.add_secret(self.token)
        session, message = answer["session_id"], answer["message_id"]
        self.session, self.message = session, message
        self.note(s, "dispatch", dict(state="dispatched", ms=ms, token_length=len(self.token),
                                      session_derived=bool(DERIVED_SESSION.fullmatch(session)),
                                      message_derived=bool(DERIVED_MESSAGE.fullmatch(message)),
                                      reconcile_hint_authoritative=(answer.get("reconcile_hint") or {}).get("authoritative")))
        again, _ = self.routing("dispatch", attempt_id="qual-a1", request=REQUEST)
        self.note(s, "dispatch_repeated", dict(state=again.get("state"), has_authority="authority" in again))
        status, health, ms = self.server.rpc("health", {})
        self.note(s, "health", dict(http=status, state=(health or {}).get("state"), ms=ms))
        if (health or {}).get("state") != "ok":
            raise Blocker("routing.health not ok before the managed launch")
        attach = dict(session_id=session, attempt_id="qual-a1", authority=self.token, funding_pool=POOL, model=ROUTE)
        status, body, ms = self.server.rpc("attach", {**attach, "authority": "A" * 43})
        self.note(s, "attach_wrong_token", dict(http=status, reason=self.reason(body), ms=ms))
        status, body, ms = self.server.rpc("attach", attach)
        self.note(s, "attach", dict(http=status, state=(body or {}).get("state"), ms=ms))
        if (body or {}).get("state") != "attached":
            raise Blocker("routing.attach with the right token failed")
        # The managed request: the derived session and message ids.
        permissions = [{"action": "*", "resource": "*", "effect": "deny"},
                       {"action": "webfetch", "resource": "*", "effect": "ask"},
                       {"action": "subagent", "resource": "*", "effect": "allow"}]
        status, created, _ = self.create_session(session, permissions=permissions)
        if (self.data(created) or {}).get("id") != session:
            raise Blocker("the derived session was not created")
        self.server.write_control(title_session=session)
        mark, before, began = len(self.server.hooks()), len(self.server.transmitted()), int(time.time() * 1000)
        self.permit(2)  # the primary request and the one title request of stage 3
        status, body, ms = self.prompt(session, message)
        messages = self.wait_idle(session, began)
        time.sleep(3)  # the title request may finish after the primary one
        self.permit(0)
        transmitted = self.server.transmitted()[before:]
        primaries = [r for r in transmitted if r["kind"] == "primary"]
        titles = [r for r in transmitted if r["kind"] == "title"]
        self.budget.spend(s, "managed primary request (derived ids)", len(primaries))
        self.results["ledger"].append(dict(stage=s, label="managed primary request (derived ids)", calls=len(primaries)))
        self.budget.spend("3", "title request of the managed session", len(titles))
        self.results["ledger"].append(dict(stage="3", label="title request of the managed session", calls=len(titles)))
        invoked = self.records(mark, session=session, hook="model.request", phase="pre")
        assistants = [m for m in messages if m.get("type") == "assistant"]
        self.note(s, "managed_request", dict(http=status, ms=ms, primary_transmitted=len(primaries), title_transmitted=len(titles),
                                             invocations=sorted(r["kind"] for r in invoked), assistant_messages=len(assistants),
                                             finish=[m.get("finish") for m in assistants], **self.outcome(messages, began)))
        if len(primaries) != 1 or not assistants:
            raise Blocker("the managed request was not allowed and completed exactly once")
        # webfetch for the attached session: the shim must deny it during permission.evaluate.
        listener, counter = self.listener()
        url = "http://127.0.0.1:%d/qual" % listener.server_address[1]
        mark = len(self.server.hooks())
        status, body, ms = self.server.request("POST", "/api/session/%s/permission" % session,
                                               {"action": "webfetch", "resources": [url], "metadata": {"url": url, "format": "text"}})
        evaluated = self.records(mark, hook="permission.evaluate")
        listener.shutdown()
        self.note(s, "webfetch_permission", dict(http=status, ms=ms, response=self.reason(body) or (
            sorted(self.data(body).keys()) if isinstance(self.data(body), dict) else None),
            effect_text=re.findall(r'"(?:effect|state|status)":\s*"([a-z_]+)"', json.dumps(body))[:4],
            hook=[dict(phase=r["phase"], effect=r["effect"]) for r in evaluated], listener_hits=counter.hits))
        post = [r for r in evaluated if r["phase"] == "post"]
        if post and post[-1]["effect"] != "deny":
            raise Blocker("webfetch was not denied for the attached session")
        # Shell environment while attached: credential-like names are stripped (a dummy probe variable).
        status, created, _ = self.create_session(title="qual shell")
        shell_session = (self.data(created) or {}).get("id")
        mark = len(self.server.hooks())
        status, body, ms = self.server.request("POST", "/api/session/%s/shell" % shell_session, {"command": "true"})
        time.sleep(2)
        shells = self.records(mark, hook="shell.create.before")
        self.note(s, "shell_env_attached", dict(http=status, ms=ms, hooks=[dict(phase=r["phase"], probe_present=r["probe_present"]) for r in shells]))
        if any(r["phase"] == "post" and r["probe_present"] for r in shells):
            raise Blocker("the shell kept a credential-like variable while a session was attached")
        # A subagent on another model from the attached session: blocked before sending (0 calls).
        mark, before, began = len(self.server.hooks()), len(self.server.transmitted()), int(time.time() * 1000)
        self.server.write_control(hold=[session])  # a follow-up turn of the parent, if any, is held (0 tokens)
        status, body, ms = self.server.request("POST", "/api/session/%s/command" % session, {"name": "qualspawn", "text": ""})
        time.sleep(8)
        _, listing, _ = self.server.request("GET", "/api/session")
        children = [c.get("id") for c in (self.data(listing) or []) if isinstance(c, dict) and c.get("parentID") == session]
        for child in children:
            try:
                self.wait_idle(child, began, timeout=30)
            except Blocker:
                pass
        self.wait_idle(session, 0, timeout=30)
        self.server.write_control(hold=[])
        sent = self.account(s, "subagent spawn on another model", before)
        spawn = self.records(mark, hook="tool.execute.before")
        requests = [r for r in self.records(mark, hook="model.request") if r["session"] != session or r["phase"] == "pre"]
        self.children = children
        self.note(s, "subagent_other_model", dict(http=status, ms=ms, response_reason=self.reason(body), children=len(children),
                                                  tool_hook=[dict(phase=r["phase"], tool=r["tool"], spawn_model=r["spawn_model"]) for r in spawn],
                                                  child_requests=[dict(phase=r["phase"], kind=r["kind"], model=r["model"], variant=r["variant"],
                                                                       decision=r.get("decision")) for r in requests if r["session"] in children],
                                                  parent_requests=[dict(phase=r["phase"], kind=r["kind"], decision=r.get("decision"))
                                                                   for r in self.records(mark, session=session, hook="model.request")],
                                                  child_outcomes=[self.outcome(self.messages(c), began) for c in children],
                                                  transmitted=sent))
        if sent or any(r.get("decision") == "guard_model" for r in self.records(mark)):
            raise Blocker("a request on another model passed the shim")
        # Detach: a wrong token is refused, the right one detaches.
        status, body, ms = self.server.rpc("detach", dict(session_id=session, attempt_id="qual-a1", authority="A" * 43))
        self.note(s, "detach_wrong_token", dict(http=status, reason=self.reason(body), ms=ms))
        status, body, ms = self.server.rpc("detach", dict(session_id=session, attempt_id="qual-a1", authority=self.token))
        self.note(s, "detach", dict(http=status, state=(body or {}).get("state"), ms=ms))
        # Without a token (not attached) and with a wrong token: blocked before sending.
        self.note(s, "prompt_without_attachment", self.blocked_prompt(s, "prompt, session not attached", session, "attach_required"))
        status, body, ms = self.server.rpc("attach", {**attach, "authority": "B" * 43})
        self.note(s, "attach_wrong_token_again", dict(http=status, reason=self.reason(body), ms=ms))
        self.note(s, "prompt_after_wrong_token", self.blocked_prompt(s, "prompt after a wrong-token attach", session, "attach_required"))

    # ------------------------------------------------------------ stage 3 (P0.8 accounting)
    def stage3(self):
        s = "3"
        from runtime import opencode_accounting as accounting
        from runtime.adapters import opencode as adapter
        session = self.session
        status, record, _ = self.server.request("GET", "/api/session/%s" % session)
        raw_messages = self.messages(session)
        pages = self.last_pages
        paged = self.messages(session, limit=2)  # paging check: small pages must give the same list
        paged_pages = self.last_pages
        normalized = [adapter.normalize_message(m) for m in raw_messages]
        total = accounting.normalize_session(self.data(record))
        children = []
        for child in getattr(self, "children", []):
            _, child_record, _ = self.server.request("GET", "/api/session/%s" % child)
            child_total = accounting.normalize_session(self.data(child_record))
            children.append(dict(session_id=child, messages=[adapter.normalize_message(m) for m in self.messages(child)],
                                 tokens=child_total["tokens"], cost=child_total["cost"], complete=True))
        state = dict(session_id=session, messages=normalized, tokens=total["tokens"], cost=total["cost"], complete=True,
                     children=children)
        try:
            result = accounting.usage_from_messages(normalized, total["tokens"], session_id=session, funding="subscription",
                                                    complete=True, session_cost=total["cost"], children=children)
        except accounting.AccountingError as error:
            raise Blocker("usage_from_messages refused the live session: %s" % error)
        title = accounting.title_tokens(total["tokens"], normalized)
        report = accounting.consistency_report(state)
        events, failures = [], 0
        for event in self.events.events if self.events else []:
            if not isinstance(event, dict) or not str(event.get("type", "")).startswith("session."):
                continue
            try:
                events.append(adapter.normalize_event(event))
            except adapter.AdapterError:
                failures += 1
        tracked = accounting.track_events(events)
        resync = accounting.reconcile_with_state(result, state, funding="subscription", tracker=tracked["state"])
        requests = (result["usage"] or {}).get("requests") or []
        self.note(s, "reread", dict(session_http=status, message_pages=pages, small_page_count=paged_pages,
                                    small_pages_same=[m.get("id") for m in paged] == [m.get("id") for m in raw_messages],
                                    message_types=[m.get("type") for m in raw_messages], session_tokens=total["tokens"],
                                    session_cost=total["cost"], children=len(children)))
        self.note(s, "usage_from_messages", dict(source=(result["usage"] or {}).get("source"),
                                                 requests=[dict(kind=r["kind"], model=r["model"], variant=r.get("variant"),
                                                                tokens=r["tokens"], cost_usd_micros=r.get("cost_usd_micros"),
                                                                receipt_is_message=bool(r.get("receipt"))) for r in requests],
                                                 totals=result["totals"], title=result["title"], unknown=result["unknown"],
                                                 reported_cost=result["reported_cost"],
                                                 rate_limit=None if result["rate_limit"] is None else sorted(result["rate_limit"].keys())))
        self.note(s, "title_tokens", title)
        self.note(s, "consistency_report", report)
        self.note(s, "events", dict(session_events=len(events), normalize_failures=failures, counts=tracked["counts"],
                                    resync_needed=[dict(reason=r["reason"]) for r in tracked["resync_needed"]],
                                    types=sorted({e.get("type") or e.get("kind") for e in events})))
        self.note(s, "reconcile_with_state", resync["changes"])
        title_requests = [r for r in requests if r["kind"] == "title"]
        title_sent = [r for r in self.server.transmitted() if r["kind"] == "title" and r["session"] == session]
        if title_sent and not title_requests:
            self.results["deviations"].append("stage 3: a title request was sent but no title remainder was found")
        if any(resync["changes"]["tokens_delta"].values()) or resync["changes"]["messages_added"] or resync["changes"]["messages_missing"]:
            raise Blocker("reconcile_with_state on the re-read state is not zero")
        answer, ms = self.routing("record", event=dict(event_id="qual-a1-complete", attempt_id="qual-a1", kind="complete",
                                                       stage="worker", usage=result["usage"]))
        settlement = answer.get("settlement") or {}
        self.note(s, "record_complete", dict(state=answer.get("state"), reason=answer.get("reason"), outcome=answer.get("outcome"),
                                             duplicate=answer.get("duplicate"), ms=ms, settlement=settlement))
        if answer.get("state") != "recorded":
            raise Blocker("record complete refused: %s" % answer.get("reason"))
        status, _ = self.routing("status")
        view = status_view(status)
        self.note(s, "status_after_complete", view)
        ledger = view["ledger"]
        if ledger.get("cost_unknown", {}).get("ids") or ledger.get("uncertain_dollar", {}).get("ids") or \
                ledger.get("blocked_organizations") or ledger.get("unstamped_open_holds"):
            raise Blocker("status is not clean after completion")
        if settlement.get("kind") != "window":
            raise Blocker("a subscription hold did not settle as a window observation")

    # ------------------------------------------------------------ stage 2, after completion
    def stage2_closed(self):
        s = "2"
        session = self.session
        self.note(s, "prompt_closed_attempt", self.blocked_prompt(s, "prompt after record complete", session, "attempt_closed"))
        # An interactive session (OpenCode's own id, never attached) with the routing child killed: passes untouched.
        killed = []
        listing = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
        for line in listing.splitlines():
            if "routing.py" in line and "--serve" in line and str(self.server.ledger) in line:
                pid = int(line.split()[0])
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
        self.note(s, "routing_child_killed", len(killed))
        status, created, _ = self.create_session(title="qual interactive")
        interactive = (self.data(created) or {}).get("id")
        mark, before, began = len(self.server.hooks()), len(self.server.transmitted()), int(time.time() * 1000)
        self.permit(1)
        status, body, ms = self.prompt(interactive)
        messages = self.wait_idle(interactive, began)
        self.permit(0)
        sent = self.account(s, "interactive session, routing child killed", before)
        pairs, _ = latency_pairs(self.records(mark, session=interactive))
        self.note(s, "interactive_child_killed", dict(http=status, transmitted=sent, shim_ms=[p["ms"] for p in pairs],
                                                      assistant_messages=len([m for m in messages if m.get("type") == "assistant"]),
                                                      **self.outcome(messages, began)))
        if sent != 1:
            raise Blocker("the interactive session was not left untouched with the routing child down")
        status, health, ms = self.server.rpc("health", {})
        self.note(s, "health_after_kill", dict(http=status, state=(health or {}).get("state"), child=(health or {}).get("child"),
                                               respawn_ms=(health or {}).get("respawn_ms"), ms=ms))

    # ------------------------------------------------------------ stage 4 (lost acknowledgment)
    def stage4(self):
        s = "4"
        answer, ms = self.routing("prepare", **prepare_request("qual-a2", "task-qual-2"))
        self.note(s, "prepare", dict(state=answer.get("state"), reason=answer.get("reason"), ms=ms))
        if answer.get("state") != "admitted":
            raise Blocker("prepare for the lost-acknowledgment drill failed")
        # The launcher dispatches and dies before it sends anything: only the derived ids leave the child.
        code = ("import json, os, signal, subprocess, sys\n"
                "done = subprocess.run(sys.argv[1:], input=sys.stdin.read(), capture_output=True, text=True)\n"
                "answer = json.loads(done.stdout)\n"
                "print(json.dumps({k: answer.get(k) for k in ('state', 'session_id', 'message_id')}), flush=True)\n"
                "os.kill(os.getpid(), signal.SIGKILL)\n")
        launcher = subprocess.run([sys.executable, "-I", "-B", "-c", code, sys.executable, "-I", "-B", str(ROOT / "scripts" / "routing.py"),
                                   "dispatch", "--settings", str(self.server.settings), "--ledger", str(self.server.ledger)],
                                  input=json.dumps(dict(v=1, op="dispatch", attempt_id="qual-a2", request=REQUEST)),
                                  capture_output=True, text=True, timeout=60)
        lost = json.loads(launcher.stdout.splitlines()[0])
        self.note(s, "lost_launcher", dict(dispatch_state=lost.get("state"), launcher_exit=launcher.returncode))
        if lost.get("state") != "dispatched":
            raise Blocker("the drill's dispatch did not happen")
        session, message = lost["session_id"], lost["message_id"]
        status, _ = self.routing("status")
        self.note(s, "status_after_lost_launcher", status_view(status))
        message_http = self.server.request("GET", "/api/session/%s/message/%s" % (session, message))[0]
        session_http = self.server.request("GET", "/api/session/%s" % session)[0]
        self.note(s, "host_read", dict(message_http=message_http, session_http=session_http))
        probe = dict(provider="opencode", model="session-probe", variant=None, harness="opencode session probe", kind="primary",
                     funding_pool="none")
        answer, _ = self.routing("check", session_id=session, request=probe)
        self.note(s, "check_session_dispatched", dict(state=answer.get("state"), reason=answer.get("reason")))
        answer, _ = self.routing("record", event=dict(event_id="qual-a2-uncertain", attempt_id="qual-a2", kind="uncertain"))
        self.note(s, "record_uncertain", dict(state=answer.get("state"), reason=answer.get("reason"), outcome=answer.get("outcome")))
        status, _ = self.routing("status")
        self.note(s, "status_after_uncertain", status_view(status))
        answer, _ = self.routing("check", attempt_id="qual-a2", session_id=session, request=REQUEST)
        self.note(s, "check_after_uncertain", dict(state=answer.get("state"), reason=answer.get("reason")))
        answer, _ = self.routing("record", event=dict(event_id="qual-a2-complete", attempt_id="qual-a2", kind="complete"))
        self.note(s, "complete_on_uncertain", dict(state=answer.get("state"), reason=answer.get("reason")))
        answer, _ = self.routing("reconcile_uncertain", attempt_id="qual-a2", evidence=dict(kind="charge_reservation"),
                                 event_id="qual-a2-reconcile")
        self.note(s, "reconcile_uncertain_subscription", dict(state=answer.get("state"), reason=answer.get("reason")))
        status, _ = self.routing("status")
        self.note(s, "status_final", status_view(status))
        self.note(s, "calls", 0)

    def latencies(self):
        pairs, blocked = latency_pairs(self.server.hooks())
        by_hook = {}
        for pair in pairs:
            by_hook.setdefault("%s/%s" % (pair["hook"], pair.get("kind") or "-"), []).append(pair["ms"])
        return dict(shim_ms_by_hook={key: summarize_ms(values) for key, values in sorted(by_hook.items())},
                    unmatched_pre_records=len(blocked))

    def run(self):
        # stage2_closed needs stage 3's `record complete`, so it runs with stage 3.
        stages = [(0, self.stage0), (1, self.stage1), (2, self.stage2), (3, self.stage3), (3, self.stage2_closed),
                  (4, self.stage4)]
        try:
            for number, stage in stages:
                if number > self.through:
                    break
                stage()
        except (Blocker, BudgetExceeded) as error:
            self.results["blockers"].append("%s: %s" % (type(error).__name__, error))
        finally:
            if self.events:
                self.events.stopped = True
            self.results["budget"] = dict(limit=self.budget.limit, used=self.budget.used, entries=self.budget.entries)
            hooks = self.server.hooks()
            self.results["hook_records"] = len(hooks)
            self.results["latency"] = self.latencies()
            fields = ("phase", "hook", "kind", "model", "variant", "tool", "spawn_model", "effect", "probe_present",
                      "decision", "session")
            self.results["hooks"] = [{key: record.get(key) for key in fields if record.get(key) is not None} for record in hooks]
        return self.results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--isolated-dir", required=True)
    parser.add_argument("--budget", type=int, required=True, help="real model requests this run may send (max 14)")
    parser.add_argument("--through", type=int, default=4, help="last stage to run")
    parser.add_argument("--model", default=ROUTE)
    parser.add_argument("--out", help="write the sanitized results JSON here")
    parser.add_argument("--keep", action="store_true", help="leave the server running (prints only its port)")
    args = parser.parse_args(argv)
    try:
        check_model(args.model)
        base = check_isolated_dir(args.isolated_dir)
        CallBudget(args.budget)
    except Refused as error:
        print("refused: %s" % error, file=sys.stderr)
        return 2
    run = Run(base, args.budget, args.through)
    try:
        results = run.run()
    finally:
        if not args.keep:
            run.server.stop()
    clean = run.redact(results)
    text = json.dumps(clean, indent=2, sort_keys=True)
    leaks = run.redact.leaks(text)
    if leaks:
        print("refused to write output: %d unsanitized fragments" % len(leaks), file=sys.stderr)
        return 3
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 1 if results["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
