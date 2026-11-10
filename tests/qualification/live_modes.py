#!/usr/bin/env python3
"""Live-session modes of the qualification harness (runbook parts A, B and C), each on servers the managed launcher
starts (`runtime/opencode_launcher.py` `start_server`) and each under its own durable call purpose:

  launcher-probes  purpose `isolation`  A1-A7 isolation probes (tests/qualification/launcher_probes.py)
  drill            purpose `drill`      the S3.6c lost-acknowledgment drill (tests/qualification/drill.py)
  gateway          purpose `gateway`    OpenRouter positive and negative descendants runs (tests/qualification/gateway.py)

Run it as this script path, never with `python3 -m` (tests/__init__.py would redirect HOME):

  python3 -B tests/qualification/live_modes.py launcher-probes --isolated-dir "$Q" --call-ledger LEDGER --out OUT \\
      [--probes A1,...] [--a5-model openai/MODEL#VARIANT] [--no-variant-model PROVIDER/MODEL] [--accept-outside-reads]
  python3 -B tests/qualification/live_modes.py drill --isolated-dir "$Q" --call-ledger LEDGER --out OUT
  python3 -B tests/qualification/live_modes.py gateway --isolated-dir "$Q" --call-ledger LEDGER --out OUT \\
      --gateway-model openrouter/MODEL[#VARIANT]

The same safety rules as opencode_live.py, checked before anything starts: an empty owner-only `--isolated-dir`
outside every worktree and OpenCode's directories; `--out` outside every repository and the isolated dir; the
purpose's cap opened by a recorded `--set-cap` approval (opencode_live.py) with enough remaining and no open
reservation; the real HOME; OpenCode 2.2.6. Every send is reserved first. SIGTERM and SIGINT stop the run; cleanup
stops every server, kills leftovers, removes the isolated dir and compares the user's OpenCode directories. Output
is redacted twice and leak-checked; refused results go to the owner-only withheld file and a skeleton is written.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.qualification import (chatgpt, demand, drill, gateway, launcher_probes, live_common, outputs,  # noqa: E402
                                 user_dirs)
from tests.qualification.safety import (COUNTING, CallLedger, QUALIFIED_OPENCODE, Redactor, Refused,  # noqa: E402
                                        check_isolated_dir, check_opencode_version, check_real_home)
from tests.qualification.server import kill_leftovers  # noqa: E402

MODES = {"launcher-probes": "isolation", "drill": "drill", "gateway": "gateway", "measure-demand": None}


class Interrupted(Exception):
    pass


def _interrupt(signum, _frame):
    raise Interrupted("signal %d" % signum)


# ---------------------------------------------------------------- the live environment (OpenCode, the launcher)

class LiveEnv:
    """Servers through `opencode_launcher.start_server`; every started server's process group is remembered."""

    def __init__(self, base, redact, signin_timeout=900, announce=print, ask=input):
        self.base, self.redact, self.signin_timeout, self.announce, self.ask = Path(base), redact, signin_timeout, announce, ask
        self.pgids, self.real_home = [], Path.home()
        self.counts = dict(servers=0, children=0, routing_commands=0)

    def start(self, project, settings, ledger, environ):
        from runtime import opencode_launcher
        server = opencode_launcher.start_server(project, settings_path=settings, ledger_path=ledger, environ=environ)
        self.counts["servers"] += 1
        self.pgids.append(server.process.pid)
        self.redact.add_secret(server._password)
        return server

    def unauthenticated(self, host, path):
        connection = http.client.HTTPConnection("127.0.0.1", host.port, timeout=30)
        try:
            connection.request("GET", path, headers={"x-opencode-directory": host.directory})
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def launch(self, checkout, **kwargs):
        from runtime import opencode_launcher
        return opencode_launcher.launch(checkout, **kwargs)

    def sleep(self, seconds):
        time.sleep(seconds)

    def which(self, name, path):
        return shutil.which(name, path=path)

    def codex_window(self):
        """The Codex pool's windows through lane R7's read-only reader (branch smart-routing-codex-window-reader). It
        is not on main yet, so the ChatGPT pool's demand stays unmeasured; no model call is ever made to measure."""
        return None

    def signin(self, host, note):
        """"Sign in with ChatGPT" on this server (the qualified chatgpt.connect flow): the login lives in the probe's
        temporary data dir and is deleted with the isolated dir."""
        adapter = _SigninRun(host, self.redact, note)
        return chatgpt.connect(adapter, self.base / "chatgpt-login.json", timeout_s=self.signin_timeout,
                               announce=self.announce)

    # drill
    def child(self, spec, host):
        self.counts["children"] += 1
        return drill.ChildLaunch(spec, host)

    def routing(self, state):
        from tests.qualification.server import Routing
        counts = self.counts

        class Counted(Routing):
            def __call__(self, op, **body):
                counts["routing_commands"] += 1
                return Routing.__call__(self, op, **body)
        return Counted(state.settings, state.ledger, project=state.project)

    def environ(self):
        """The drill's server: the real HOME (the Claude login), a temporary data dir (its own sessions)."""
        data, tmp = self.base / "data-drill", self.base / "tmp-drill"
        for folder in (data, tmp):
            folder.mkdir(mode=0o700, exist_ok=True)
        return dict(HOME=str(self.real_home), USER=os.environ.get("USER", ""), LANG="en_US.UTF-8", TMPDIR=str(tmp),
                    XDG_DATA_HOME=str(data))


class GatewayEnv(LiveEnv):
    def environ(self):
        """The gateway's server: a temporary HOME (no user config) and a temporary data dir for the OpenRouter login."""
        folders = [self.base / name for name in ("home-gateway", "data-gateway", "tmp-gateway")]
        for folder in folders:
            folder.mkdir(mode=0o700, exist_ok=True)
        return dict(HOME=str(folders[0]), USER=os.environ.get("USER", ""), LANG="en_US.UTF-8", TMPDIR=str(folders[2]),
                    XDG_DATA_HOME=str(folders[1]))

    def routing(self, state):
        return gateway.ServiceRouting(state)

    def await_signin(self, host):
        return gateway.await_connection(host, self.redact, time.sleep, timeout=self.signin_timeout,
                                        announce=self.announce, data_dir=self.base / "data-gateway")

    def receipt(self, requests):
        """The operator's OpenRouter receipt for this run's generations, in micro-dollars (never read from a key)."""
        answer = self.ask("Total cost of this run's %d OpenRouter generations (openrouter.ai activity), in "
                          "micro-dollars, or empty if unavailable: " % requests).strip()
        return int(answer) if answer.isdigit() else None

    def balance(self):
        return None  # the cached openrouter_settings.status balance: unobserved until F4/C3-4b provide a reader


class _SigninRun:
    """The narrow run shape chatgpt.connect uses, over a launcher host."""

    def __init__(self, host, redact, note):
        self.server, self.redact, self._note = _Triple(host), redact, note

    def data(self, body):
        return live_common.data(body)

    def note(self, _stage, key, value):
        self._note(key, value)


class _Triple:
    def __init__(self, host):
        self.host = host

    def request(self, method, path, body=None, query=None, timeout=None):
        status, answer = self.host.request(method, path, body=body, query=query)
        return status, answer, 0


# ---------------------------------------------------------------- one run

class Session:
    def __init__(self, mode, base, ledger, args, env, home=None):
        self.mode, self.purpose, self.base, self.ledger, self.args, self.env = mode, MODES[mode], Path(base), ledger, args, env
        self.home = Path(home or Path.home())
        self.run_id = "%s-%s" % (self.purpose, uuid.uuid4().hex[:8])
        self.redact = env.redact
        self.results = dict(mode=mode, purpose=self.purpose, blockers=[])

    def body(self):
        log = demand.DemandLog(demand.log_path(self.ledger.path))
        if self.mode == "launcher-probes":
            probes = launcher_probes.LauncherProbes(
                self.base, self.ledger, self.run_id, self.env, self.home, os.environ.get("USER", ""), self.redact,
                a5_model=self.args.a5_model, no_variant_model=self.args.no_variant_model,
                accept_outside_reads=self.args.accept_outside_reads, demand_log=log)
            found = probes.run(launcher_probes.parse_probes(self.args.probes))
            self.results["probes"] = found
            failed = [probe for probe, result in found.items() if result.get("passed") is False]
            if failed:
                self.results["blockers"].append("probes failed: %s" % ", ".join(failed))
        elif self.mode == "drill":
            found = drill.run_drill(drill.Drill(self.base, self.ledger, self.run_id, self.env, demand_log=log))
            self.results["drill"] = found
            if not found.get("passed"):
                self.results["blockers"].append(found.get("blocker") or "a drill step failed")
        else:
            found = gateway.run_gateway(gateway.Gateway(self.base, self.ledger, self.run_id, self.env, self.args.gateway_model))
            self.results["gateway"] = found
            if not found.get("passed"):
                self.results["blockers"].append(found.get("blocker") or "a gateway check failed")
        if self.mode != "gateway":
            self.results["demand"] = demand.report(log)  # local file only: 0 calls, no process

    def run(self):
        began = time.monotonic()
        self.results["plan"] = plan(self.mode, self.args)
        user_before, home_before = user_dirs.snapshot(self.home), user_dirs.report_only(self.home)
        previous = {sig: signal.signal(sig, _interrupt) for sig in (signal.SIGTERM, signal.SIGINT)}
        saved_tmp = tempfile.tempdir
        tempfile.tempdir = str(self.base / "launcher-tmp")  # the launcher's private dirs live in the isolated dir
        os.makedirs(tempfile.tempdir, mode=0o700, exist_ok=True)
        try:
            self.body()
        except Exception as error:  # noqa: BLE001 - every failure is recorded; cleanup always runs
            self.results["blockers"].append("%s: %s" % (type(error).__name__, error))
        finally:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)  # a second signal must not interrupt cleanup
            try:
                self.cleanup()
            finally:
                tempfile.tempdir = saved_tmp
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        self.results["user_dirs"] = user_dirs.compare(self.home, user_before, home_before, self.results["blockers"])
        counts = dict(getattr(self.env, "counts", {}) or {})
        servers = counts.get("servers", 0)
        self.results["measured"] = dict(counts, processes=1 + 2 * servers + counts.get("children", 0)
                                        + counts.get("routing_commands", 0),
                                        sent=(self.results.get("call_ledger") or {}).get(self.purpose, {}).get("spent"),
                                        wall_s=round(time.monotonic() - began, 1))
        return self.results

    def cleanup(self):
        errors, left = [], []
        for pgid in getattr(self.env, "pgids", []) or [None]:
            try:
                left += kill_leftovers(pgid, [str(self.base), os.path.realpath(self.base)])
            except Exception as error:  # noqa: BLE001
                errors.append("kill_leftovers: %s" % type(error).__name__)
        shutil.rmtree(self.base, ignore_errors=True)
        removed = not self.base.exists()
        try:
            totals = self.ledger.summary()
        except Exception as error:  # noqa: BLE001
            totals, errors = None, errors + ["call_ledger: %s" % type(error).__name__]
        open_left = (totals or {}).get(self.purpose, {}).get("open_reservations")
        self.results["cleanup"] = dict(processes_left=len(left), isolated_dir_removed=removed, errors=errors,
                                       open_reservations=open_left,
                                       verified_by_harness=not errors and not left and removed and open_left == 0)
        if not self.results["cleanup"]["verified_by_harness"]:
            self.results["blockers"].append("cleanup could not be verified")
        self.results["call_ledger"] = totals

    def output(self):
        meta = dict(opencode=QUALIFIED_OPENCODE, date=time.strftime("%Y-%m-%d"), mode=self.mode, purpose=self.purpose,
                    counting=COUNTING + "; launcher-started servers load no recorder, so a step's count is its "
                                        "assistant messages (or the reserved count when unreadable)",
                    sanitized=True, generated_by="tests/qualification/live_modes.py (no post-processing)")
        return self.redact(dict(_meta=meta, **self.results))


# ---------------------------------------------------------------- the command

def plan(mode, args):
    """Per mode, before anything starts: expected model calls, processes started and wall time (user design rule)."""
    if mode == "launcher-probes":
        return launcher_probes.plan(launcher_probes.parse_probes(args.probes))
    if mode == "drill":
        return drill.plan()
    if mode == "gateway":
        return gateway.plan()
    return dict(expected_calls=0, servers=0, processes=0, wall_time="under a second (reads one local file)")


def measure_demand(args):
    """`measure-demand`: the per-launch window demand from the readings the budgeted launches stored (0 calls, no
    process, no server); written to `--out` with the manifest suggestion."""
    CallLedger(args.call_ledger)  # the readings live next to a valid, owner-only ledger
    outputs.check_out(args.out)
    found = dict(mode="measure-demand", plan=plan("measure-demand", args),
                 demand=demand.report(demand.DemandLog(demand.log_path(args.call_ledger))))
    text = json.dumps(found, indent=2, sort_keys=True)
    if Redactor().leaks(text):
        raise Refused("the demand report did not pass the leak check")
    Path(args.out).write_text(text + "\n")
    print(json.dumps(found["demand"]["suggestion"], indent=2))
    return 0

def needed_calls(args):
    return plan(args.mode, args).get("reserved", plan(args.mode, args)["expected_calls"])


def parser():
    parse = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parse.add_argument("mode", choices=sorted(MODES))
    parse.add_argument("--isolated-dir", help="every mode but measure-demand: the run's empty owner-only directory")
    parse.add_argument("--call-ledger", required=True)
    parse.add_argument("--out", required=True, help="the sanitized results (outside every repository and the isolated dir)")
    parse.add_argument("--probes", help="launcher-probes: a comma list of A1-A7 (default all)")
    parse.add_argument("--a5-model", help="launcher-probes A5: openai/MODEL#VARIANT from the ChatGPT login's list")
    parse.add_argument("--no-variant-model", help="launcher-probes A7: PROVIDER/MODEL listed without variants")
    parse.add_argument("--accept-outside-reads", action="store_true",
                       help="launcher-probes A5: record allowed outside reads as the user's accepted risk")
    parse.add_argument("--gateway-model", help="gateway: openrouter/MODEL[#VARIANT] (at most $1/M in, $4/M out)")
    parse.add_argument("--signin-timeout", type=int, default=900, help="seconds to wait for an interactive sign-in")
    return parse


def preflight(args, home_check=check_real_home, version_check=check_opencode_version):
    """Every refusal decidable before anything starts: `(base, ledger)` or `Refused`."""
    base = check_isolated_dir(args.isolated_dir)
    outputs.check_out(args.out)
    out = Path(os.path.realpath(args.out))
    if out == base or base in out.parents:
        raise Refused("--out must be outside the isolated dir, which is removed")
    ledger = CallLedger(args.call_ledger)
    if args.mode == "launcher-probes":
        launcher_probes.parse_probes(args.probes)
        if args.a5_model:
            live_common.parse_model(args.a5_model)
        if args.no_variant_model:
            live_common.parse_model(args.no_variant_model, variant_required=False)
    elif args.gateway_model or args.mode == "gateway":
        if args.mode != "gateway":
            raise Refused("--gateway-model belongs to the gateway mode")
        gateway.Gateway(base, ledger, "check", None, args.gateway_model or "")
    if not 60 <= args.signin_timeout <= 1800:
        raise Refused("--signin-timeout is 60 to 1800 seconds")
    live_common.require_cap(ledger, MODES[args.mode], needed_calls(args))
    home_check()  # the last checks: nothing has started yet
    from runtime import opencode_launcher
    version_check(env=opencode_launcher.host_environment(os.environ))
    return base, ledger


def main(argv=None, env_factory=None, home_check=check_real_home, version_check=check_opencode_version):
    args = parser().parse_args(argv)
    try:
        if args.mode == "measure-demand":
            if args.isolated_dir:
                raise Refused("measure-demand starts nothing and takes no --isolated-dir")
            return measure_demand(args)
        if not args.isolated_dir:
            raise Refused("--isolated-dir is required")
        base, ledger = preflight(args, home_check, version_check)
    except Refused as error:
        print("refused: %s" % error, file=sys.stderr)
        return 2
    redact = Redactor(paths=[str(base), os.path.realpath(base), str(base).replace("/private/", "/", 1), str(ROOT),
                             str(Path.home())], names=[os.environ.get("USER", "")])
    factory = env_factory or (GatewayEnv if args.mode == "gateway" else LiveEnv)
    env = factory(base, redact, signin_timeout=args.signin_timeout)
    session = Session(args.mode, base, ledger, args, env)
    results = session.run()
    clean = redact(session.output())  # a second pass: secrets learned partway through the first are removed
    text = json.dumps(clean, indent=2, sort_keys=True)
    leaks = redact.leaks(text)
    if leaks:
        kept = outputs.withhold(text, args.call_ledger, session.run_id)
        print("refused to write output: %d unsanitized fragments; full results kept at %s (owner-only, never commit; "
              "delete after reading); wrote a skeleton" % (len(leaks), kept))
        Path(args.out).write_text(outputs.skeleton_text(clean, leaks, redact))
        return 3
    Path(args.out).write_text(text + "\n")
    print(json.dumps(dict(blockers=clean["blockers"], cleanup=clean.get("cleanup"),
                          call_ledger=(clean.get("call_ledger") or {}).get(session.purpose)), indent=2))
    return 1 if results["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
