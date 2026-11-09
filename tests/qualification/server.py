"""The isolated OpenCode server, the routing command client and cleanup for the live qualification harness.

Qualification mode: the server's project, config and routing stores all live in the isolated directory.
Canary mode: the server's location is a dedicated, git-excluded subdirectory of the allowlisted project (see
`ProjectConfig`), and routing (the shim's child and the launcher) uses the dedicated canary settings and ledger
(canary.check_canary_state; never the production defaults) with `--project` pinned.
"""
import base64
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

from tests.qualification.safety import (Blocker, HARNESS_NAME, MODEL, PROBE_ENV, PROMPT_TEXT, PROVIDER, Refused, VARIANT,
                                        leftover_processes, recorder_source)

ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SHIM_WRAPPER = 'export { default } from %s\n'
CANARY_DIR = ".vibe-qual-canary"  # inside the allowlisted project; git-excluded while it exists
EXCLUDE_LINES = ("# vibe-workflow qualification canary (temporary; the harness removes it)", "/%s/" % CANARY_DIR)
POOL = "sub"


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def server_environment(base):
    """The minimal environment the server gets. HOME stays so claude-agent can use the user's Claude login."""
    return {"HOME": str(Path.home()), "PATH": SERVER_PATH, "USER": os.environ.get("USER", ""), "LANG": "en_US.UTF-8",
            "TMPDIR": str(base / "tmp"), "XDG_CONFIG_HOME": str(base / "config"), "XDG_DATA_HOME": str(base / "data"),
            "XDG_STATE_HOME": str(base / "state"), "XDG_CACHE_HOME": str(base / "cache"),
            "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1", PROBE_ENV: "dummy-not-a-secret"}


class ProjectConfig:
    """Canary mode: an `opencode.json` in `<project>/.vibe-qual-canary/`, excluded through `.git/info/exclude`.

    Config discovery walks a location's ancestors only, so the user's own OpenCode (which may have the project
    root open) never loads this config; only the isolated server, whose location is this subdirectory, does."""

    def __init__(self, project):
        self.project = Path(project)
        self.directory = self.project / CANARY_DIR
        self.exclude = self.project / ".git" / "info" / "exclude"

    def create(self, config):
        if not (self.project / ".git").is_dir():
            raise Refused("the canary project must be a main checkout with a .git directory")
        existing = self.exclude.read_text() if self.exclude.exists() else ""
        if self.directory.exists() or EXCLUDE_LINES[1] in existing.splitlines():
            raise Refused("a previous canary config is still present; inspect and remove it first")
        self.exclude.parent.mkdir(parents=True, exist_ok=True)
        self.original_exclude = existing if self.exclude.exists() else None
        with self.exclude.open("a") as handle:
            handle.write(("" if not existing or existing.endswith("\n") else "\n") + "\n".join(EXCLUDE_LINES) + "\n")
        self.directory.mkdir(mode=0o700)
        (self.directory / "opencode.json").write_text(json.dumps(config, indent=2))
        return self.directory

    def remove(self):
        """Remove the config and restore `.git/info/exclude` exactly; returns whether both are verifiably clean."""
        shutil.rmtree(self.directory, ignore_errors=True)
        original = getattr(self, "original_exclude", None)
        block = "\n".join(EXCLUDE_LINES) + "\n"
        if self.exclude.exists():
            text = self.exclude.read_text()
            added = ("" if not original or original.endswith("\n") else "\n") + block
            if original is not None and text == original + added:
                self.exclude.write_text(original)
            elif original is None and text == block:
                self.exclude.unlink()
            elif block in text:
                self.exclude.write_text(text.replace(block, "", 1))
        gone = not self.exclude.exists() or EXCLUDE_LINES[1] not in self.exclude.read_text().splitlines()
        return not self.directory.exists() and gone


class Server:
    """One `opencode serve` on a random localhost port with its own XDG dirs."""

    def __init__(self, base, canary_project=None, settings=None, ledger=None):
        self.base = base
        self.canary = canary_project is not None
        self.project_config = ProjectConfig(canary_project) if self.canary else None
        self.project = None
        self.port = free_port()
        self.process = None
        self.password = None
        self.directory = None
        self.log = base / "hooks.jsonl"
        self.control_path = base / "control.json"
        # Qualification pins temporary stores in the run dir; canary pins the dedicated canary state.
        self.settings = settings or base / "routing" / "settings.json"
        self.ledger = ledger or base / "usage" / "organizations.sqlite3"
        self.control = dict(budget=0, hold=[], title_session=None)

    def write_control(self, **change):
        self.control.update(change)
        temporary = self.control_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.control))
        os.replace(temporary, self.control_path)

    def config(self):
        recorder = dict(log=str(self.log), control=str(self.control_path))
        shim = {"python": "python3", "settings": str(self.settings), "ledger": str(self.ledger)}
        return {
            "$schema": "https://opencode.ai/config.json",
            "model": "%s/%s" % (PROVIDER, MODEL),
            "plugins": [{"package": str(self.base / "plugins/qual-pre"), "options": recorder},
                        {"package": str(self.base / "plugins/routing"), "options": shim},
                        {"package": str(self.base / "plugins/qual-post"), "options": recorder}],
            # A background child session on another model: the spawn test of stage 2 (blocked before sending).
            "commands": {"qualspawn": {"template": PROMPT_TEXT, "model": "%s/sonnet#%s" % (PROVIDER, VARIANT),
                                       "subagent": True, "agent": "general"}},
        }

    def prepare_files(self):
        for name in ("config", "data", "state", "cache", "tmp", "plugins/qual-pre", "plugins/qual-post", "plugins/routing"):
            (self.base / name).mkdir(parents=True, mode=0o700, exist_ok=True)
        (self.base / "plugins/qual-pre/index.ts").write_text(recorder_source("pre"))
        (self.base / "plugins/qual-post/index.ts").write_text(recorder_source("post"))
        shim = ROOT / "runtime" / "adapters" / "opencode_plugin.ts"
        (self.base / "plugins/routing/index.ts").write_text(SHIM_WRAPPER % json.dumps(str(shim)))
        self.log.touch(mode=0o600)
        self.write_control(budget=0)
        if self.canary:
            self.project = self.project_config.create(self.config())
            return
        for name in ("project", "routing", "usage"):
            (self.base / name).mkdir(mode=0o700, exist_ok=True)
        table = self.settings.parent / "strategy-table.json"
        table.write_text(json.dumps({"schema": 1, "version": "qual-1", "rows": {"worker|code|medium|medium": [
            {"strategy_id": "s-sub", "model": MODEL, "effort": VARIANT, "harness": HARNESS_NAME, "evidence_refs": ["qual"]}]}}))
        table.chmod(0o600)
        self.project = self.base / "project"
        (self.project / "opencode.json").write_text(json.dumps(self.config(), indent=2))

    def start(self):
        self.prepare_files()
        out = open(self.base / "server.log", "wb")
        self.process = subprocess.Popen(["opencode", "serve", "--hostname", "127.0.0.1", "--port", str(self.port)],
                                        cwd=self.project, env=server_environment(self.base), stdout=out,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
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
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(10)
            except ProcessLookupError:
                pass

    def _headers(self, accept):
        return {"Authorization": "Basic " + base64.b64encode(("opencode:" + self.password).encode()).decode(),
                "x-opencode-directory": self.directory, "accept": accept}

    def request(self, method, path, body=None, query=None, timeout=60):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        headers = self._headers("application/json")
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
        """The recorders' log. Unreadable or damaged lines are fatal: the counter must never read as zero."""
        records = []
        for line in self.log.read_text().splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    raise Blocker("the recorder log is damaged") from None
        return records

    def transmitted(self):
        return [record for record in self.hooks() if record.get("decision") == "transmit"]


class EventStream(threading.Thread):
    """Reads `/api/event` (server-sent events) into memory; raw records stay in memory only."""

    def __init__(self, server):
        super().__init__(daemon=True)
        self.server, self.events, self.error, self.stopped, self.status = server, [], None, False, None

    def run(self):
        try:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=600)
            connection.request("GET", "/api/event", headers=self.server._headers("text/event-stream"))
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
    """`scripts/routing.py`: one-shot calls, or one persistent `--serve` child for the launcher's own latency.

    Both modes pin the stores: the run dir's temporary ones, or the dedicated canary state with `--project`."""

    # Requests that only read: the only ones re-sent after a lost or mismatched reply. Writes are never re-sent.
    READS = ("status", "check")

    def __init__(self, settings=None, ledger=None, project=None, reply_timeout=30, child_argv=None):
        self.reply_timeout, self.child_argv, self.buffer = reply_timeout, child_argv, b""
        self.argv = []
        if settings is not None:
            self.argv += ["--settings", str(settings), "--ledger", str(ledger)]
        if project is not None:
            self.argv += ["--project", str(project)]
        self.child = None
        self.seq = 0

    def __call__(self, op, **body):
        began = time.perf_counter()
        done = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts" / "routing.py"), op, *self.argv],
                              input=json.dumps(dict(v=1, op=op, **body)), capture_output=True, text=True, timeout=60)
        elapsed = round((time.perf_counter() - began) * 1000, 2)
        try:
            return json.loads(done.stdout), elapsed
        except ValueError:
            return dict(state="unparsed", code=done.returncode), elapsed

    def _spawn(self):
        argv = self.child_argv or [sys.executable, "-I", "-B", str(ROOT / "scripts" / "routing.py"), "--serve", *self.argv]
        self.child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buffer = b""

    def _kill(self):
        """Drop the child and anything it still owes: a late reply must never be read as another request's answer."""
        child, self.child, self.buffer = self.child, None, b""
        if child is not None:
            try:
                child.kill()
                child.wait(10)
            except (OSError, subprocess.SubprocessError):
                pass

    def _readline(self, deadline):
        """One reply line from the child before `deadline` (monotonic), or None."""
        fd = self.child.stdout.fileno()
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                return None
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return line

    def served(self, op, **body):
        """The same request through a persistent `--serve` child (routing work without interpreter start-up).

        Every reply must carry this request's id. A timeout, an unreadable or mismatched reply, or an interrupt
        kills the child (so nothing it still owes is read later). Reads (`status`, `check`) are then sent once more
        to a fresh child; a write is never re-sent and answers `unconfirmed` with the reason."""
        tries = 2 if op in self.READS else 1
        for attempt in range(tries):
            if self.child is None or self.child.poll() is not None:
                self._spawn()
            self.seq += 1
            expected = "q%d" % self.seq
            began = time.perf_counter()
            try:
                self.child.stdin.write((json.dumps(dict(v=1, op=op, id=expected, **body)) + "\n").encode())
                self.child.stdin.flush()
                line = self._readline(time.monotonic() + self.reply_timeout)
            except (BrokenPipeError, OSError):
                line = None
            except BaseException:
                self._kill()
                raise
            elapsed = round((time.perf_counter() - began) * 1000, 2)
            problem, answer = None, None
            if line is None:
                problem = "reply_timeout"
            else:
                try:
                    answer = json.loads(line)
                except ValueError:
                    problem = "reply_unparsed"
                if problem is None and (type(answer) is not dict or answer.get("id") != expected):
                    problem = "reply_mismatch"
            if problem is None:
                answer.pop("id", None)
                return answer, elapsed
            self._kill()
        return dict(state="unconfirmed" if op not in self.READS else "unavailable", reason=problem), elapsed

    def stop(self):
        self._kill()

    def admission_ids(self, attempt):
        """Derived ids of an admitted, not yet dispatched attempt (read-only, in process), for the pending probe."""
        sys.path.insert(0, str(ROOT))
        from runtime import routing as core
        from scripts import routing as command
        paths = dict(zip(self.argv[::2], self.argv[1::2]))
        service = command.Service(paths.get("--settings") or command.default_settings(),
                                  paths.get("--ledger") or command.default_ledger(), project=paths.get("--project"))
        try:
            ledger = service._ledger(False)
            return None if ledger is None else core.admission_ids(ledger.snapshot(), attempt)
        finally:
            service.close()


def listener():
    """A local HTTP listener that only counts hits (the webfetch target)."""
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


def prepare_request(attempt_id, task):
    return dict(
        assignment=dict(attempt_id=attempt_id, task=task, role="implementer", scope="qual-scope"),
        job=dict(id="job-" + attempt_id, role="worker", scope_ref="qual-scope", objective="o", context="c", profile=dict(
            task_family="code", complexity_band="medium", risk_band="medium", review_policy="independent",
            context_tokens=100, tools=["edit"], uncertainty="low")),
        observations=dict(now=int(time.time()), routes=[dict(
            route="sub", strategy_id="s-sub", provider=PROVIDER, model=MODEL, effort=VARIANT, harness=HARNESS_NAME,
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


def processes():
    return subprocess.run(["ps", "-axo", "pid=,pgid=,command="], capture_output=True, text=True).stdout


def kill_leftovers(pgid, needles):
    """Kill and re-check processes left in the server's group or naming the run's paths; returns those still alive."""
    found = [pid for pid in leftover_processes(processes(), pgid, needles) if pid != os.getpid()]
    for pid in found:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if found:
        time.sleep(1)
    return [pid for pid in leftover_processes(processes(), pgid, needles) if pid != os.getpid()]
