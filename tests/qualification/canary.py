"""Bounded pilot canary (docs/smart-routing-validation.md#pilot-canary-bounded-not-the-pre-registered-canary).

Pure decisions over an injectable environment, so every refusal and abort path is unit-tested without a server:
`check_project` (the one-project allowlist), `check_canary_state` and `initialize_state` (the dedicated canary
routing state, the default), `resolve_state` (the production settings and ledger only with `--production-ledger` and a
recorded `--approval`; never created, initialized or reset by the harness), `preflight` (refusals before any write), `preconditions`
(refusals once the server and probes ran), `plan_assignments` (up to 20 explicit subscription admissions across three
roles and two complexity bands) and `PilotDriver` (sequential admissions with the durable `pilot` call cap, a 24-hour
limit, working-tree checks, and a catch-all abort that recovers by phase and runs the Smart Routing OFF kill switch).

Observations: `plan_assignments` builds the pilot's own snapshot for its unmanaged canary ledger. On a managed canary
project (on the canary settings' `managed_projects`) `server.Routing` sends every `prepare` without it, so the routing
service's collector supplies the snapshot from the shipped manifest and stored telemetry, the production path the
pre-registered canary measures (C3-3; plan Task S3.7). The harness never changes `managed_projects` itself.

The environment (`opencode_live.CanaryEnv` in a live run, a fake in tests) provides: `status()`, `health()`,
`prepare(body)`, `dispatch(attempt)`, `attach(session, attempt, token)`, `detach(...)`, `launch(session, message,
allow_title)`, `account(session, titles_sent)`, `record(event)`, `repo_changes()` and `kill_switch()`; optionally
`quiesce()`, `diagnostics(session)` (payload shapes for an aborted admission), `progress()` (how far the last
accounting read got) and `export_if_due()` (the 12-hour replay export of the canary admissions).
"""
import os
from pathlib import Path
import shutil
import stat
import time

from tests.qualification.safety import (APPROVAL_FIELDS, BudgetExceeded, HARNESS_SCRIPTS, PROTECTED, Refused,
                                        exception_origin, harness_for, inside_git_worktree, summarize_ms)

ALLOWLIST = "/Users/fcouto/dev/git/vibe-workflow"
PILOT_MAX = 20
MAX_DURATION_S = 24 * 3600
REPO_CHECK_EVERY = 5
# Distinct ids for the pilot, so nothing it admits can be mistaken for another route's or another ledger's holds.
POOL = "pilot-sub"
ACCOUNT = "anthropic:pilot-canary"
DEFAULT_STATE = Path.home() / ".local/state/vibe-workflow-qualification/canary"
PRODUCTION_STATE_ROOT = ".local/state/vibe-workflow"
RESET_ADVICE = ("The canary routing state is not clean. It belongs to the pilot only: after confirming no harness run is "
                "alive, the operator may reset it by deleting the canary directory; the production state is untouched.")
PRODUCTION_ADVICE = ("The production routing state is not clean or not ready, and the harness never deletes, resets or "
                     "initializes it. Fix the named condition through scripts/routing.py: activate the project with the "
                     "one-shot `set managed_projects`, settle or reconcile open holds with the one-shot operator ops, "
                     "then run again.")
# (label, job specialist, explicit assignment role): an explicit role must equal the job's specialist.
ROLES = (("software-engineer", "software-engineer", None), ("code-reviewer", "code-reviewer", "code-reviewer"),
         ("explorer-agent", "explorer-agent", "explorer-agent"))
BANDS = ("low", "medium")
# How far an admission got, in order; recovery after an abort depends on it.
PHASES = ("planned", "preparing", "admitted", "dispatching", "dispatched", "attached", "launching", "launched",
          "recorded", "completed")


MAX_REREADS = 5  # SF1: bounded re-reads of a lagging session total, `REREAD_WAIT_S` apart
REREAD_WAIT_S = 1.0
# Accounting results that may only mean the host has not caught up yet.
REREAD_REASONS = ("message_in_progress", "negative_remainder", "title_unknown")


class AccountingRegressed(Exception):
    """A re-read saw a token class decrease or the message set change: never settled, always an abort."""


def unsettled(result, titles_sent):
    """Whether an accounting result may still be catching up: a reason above, or a title was sent but not counted."""
    kinds = [request["kind"] for request in ((result.get("usage") or {}).get("requests") or [])]
    return bool(set(REREAD_REASONS) & set(result.get("unknown") or [])) or (titles_sent >= 1 and "title" not in kinds)


def planned_exposure(n):
    """Pilot calls an attempt may reserve at most: two probe exposures, the first admission and its title, then one
    per further admission (Edit 4: 2 + 2 + 19 = 23 for 20 admissions)."""
    return 2 + 2 + (n - 1)


def classify(error):
    """The abort detail for an accounting failure: a fixed refusal reason, or the harness error's type."""
    if isinstance(error, AccountingRegressed):
        return "accounting_regressed"
    if type(error).__name__ in ("AccountingError", "AdapterError"):
        return "refusal: %s" % error  # a fixed reason from a closed set; never payload text
    return "harness_error: %s" % type(error).__name__


def request(route, pool=POOL):
    """The `check`/`dispatch` request facts for the approved route tuple."""
    provider, model, variant = route
    return dict(provider=provider, model=model, variant=variant, harness=harness_for(provider), kind="primary",
                funding_pool=pool)


def check_project(project, allow=None, settings=None, managed=False):
    """The pilot runs only in the allowlisted checkout: exactly that path, resolved, and a git checkout root.

    `allow` replaces the default ALLOWLIST for one run (`--canary-allow`). An explicit project, and every project of
    a production-ledger canary (`managed`), must also be on the routing settings' `managed_projects` (`settings`),
    which the harness never changes itself (the user activates it with the one-shot `set`)."""
    if not project:
        raise Refused("--project is required in canary mode")
    resolved = os.path.realpath(project)
    if resolved != os.path.realpath(allow or ALLOWLIST) or not (Path(resolved) / ".git").exists():
        raise Refused("canary mode runs only in the allowlisted project")
    if allow is not None or managed:
        from runtime import routing_settings
        try:
            found = settings is not None and routing_settings.managed_project(Path(resolved), settings_path=Path(settings))
        except Exception:  # noqa: BLE001 - unreadable settings never admit a project
            found = False
        if found is not True:
            raise Refused("an explicit or production-ledger canary project must be on the routing settings' "
                          "managed_projects")
    return resolved


def pilot_max(requested, ledger):
    """The admissions for one run (`--pilot-max`): PILOT_MAX by default; an explicit value is 1 to PILOT_MAX and its
    planned exposure must fit what the durable `pilot` purpose still has."""
    if requested is None:
        return PILOT_MAX
    if type(requested) is not int or not 1 <= requested <= PILOT_MAX:
        raise Refused("--pilot-max is 1 to %d" % PILOT_MAX)
    if planned_exposure(requested) > ledger.remaining("pilot"):
        raise Refused("--pilot-max %d needs %d pilot calls; the ledger has %d" % (
            requested, planned_exposure(requested), ledger.remaining("pilot")))
    return requested


CONFIG_NAMES = ("opencode.json", "opencode.jsonc", ".opencode")


def check_ancestor_configs(project):
    """Refuse when the project or any ancestor up to / holds an OpenCode config. OpenCode merges every ancestor's
    config into the isolated server's location, so such a file could load another copy of the routing plugin
    (with default pins: the production state). The user's global config is already excluded by XDG isolation."""
    for directory in (Path(project), *Path(project).parents):
        for name in CONFIG_NAMES:
            candidate = directory / name
            if candidate.exists() or candidate.is_symlink():
                raise Refused("an OpenCode config above the canary location would be loaded: %s" % candidate)


def _production_paths():
    from scripts import routing as command
    return command.default_settings(), command.default_ledger()


def check_canary_state(path, home=None, production=None, isolated=None):
    """The dedicated canary routing state: `{dir, settings, ledger}`, or `Refused`.

    It must be an absolute directory (existing or not yet created) whose parent is owner-only, itself owner-only
    when it exists, outside every git worktree, OpenCode's user directories, the production routing state root and
    the run's isolated dir, and its settings and ledger must differ from the production defaults."""
    if not path or not Path(path).is_absolute():
        raise Refused("the canary state directory must be absolute")
    directory = Path(path)
    home = Path(home or Path.home()).resolve()
    resolved = directory.parent.resolve() / directory.name
    owner = lambda p: not p.is_symlink() and p.stat().st_uid == os.getuid() and stat.S_IMODE(p.stat().st_mode) == 0o700  # noqa: E731
    if not directory.parent.is_dir() or not owner(directory.parent):
        raise Refused("the canary state's parent directory must exist and be owner-only")
    if directory.exists() and (not directory.is_dir() or not owner(directory)):
        raise Refused("the canary state directory must be owner-only")
    if inside_git_worktree(resolved):
        raise Refused("the canary state must not be inside a git worktree")
    for guarded in (*PROTECTED, PRODUCTION_STATE_ROOT):
        root = (home / guarded).resolve()  # a symlinked ~/.local cannot slip past
        if resolved == root or root in resolved.parents:
            raise Refused("the canary state must be outside OpenCode's and the production routing state directories")
    if isolated is not None and (resolved == Path(isolated) or Path(isolated) in resolved.parents):
        raise Refused("the canary state must be outside the isolated run directory")
    settings, ledger = resolved / "settings.json", resolved / "ledger.sqlite3"
    for mine, theirs in zip((settings, ledger), production or _production_paths()):
        if mine == Path(theirs).parent.resolve() / Path(theirs).name or resolved == Path(theirs).parent.resolve():
            raise Refused("the canary state must not be the production routing state")
    return dict(dir=resolved, settings=settings, ledger=ledger)


def check_approval(values):
    """`--approval BY AT SCOPE` in the call ledger's approval shape (`APPROVAL_FIELDS`), recorded verbatim, or
    `Refused`. The scope must name the canary: the approval covers the canary only."""
    if type(values) not in (list, tuple) or len(values) != len(APPROVAL_FIELDS) or \
            not all(type(value) is str and value.strip() and len(value) <= 200 for value in values):
        raise Refused("the production-ledger canary needs --approval BY AT SCOPE: who approved, when and the scope "
                      "(each at most 200 characters)")
    if "canary" not in values[2].lower():
        raise Refused("the production-ledger approval's SCOPE must name the canary")
    return dict(zip(APPROVAL_FIELDS, values))


def refusal_advice(state):
    """What the operator does about an unclean state: delete the dedicated canary state, never the production one."""
    return PRODUCTION_ADVICE if (state or {}).get("production") else RESET_ADVICE


def resolve_state(path, production=False, approval=None, home=None, isolated=None):
    """The canary's routing state. By default (never otherwise) the dedicated canary state (`check_canary_state`,
    `path` or DEFAULT_STATE). Only with `--production-ledger` and a recorded `--approval` (the user approved the
    canary, and nothing else, on the production ledger on 2026-11-10): the production settings and ledger at the
    routing command's defaults, `{settings, ledger, production: True, approval}`. Every write still goes through
    `scripts/routing.py` (one-shot and operator rules unchanged); the harness never creates, initializes or resets
    the production state, and never changes `managed_projects`."""
    if not production:
        if approval is not None:
            raise Refused("--approval with the canary belongs to --production-ledger")
        return check_canary_state(path or DEFAULT_STATE, home=home, isolated=isolated)
    if path is not None:
        raise Refused("--production-ledger uses the production routing state; --canary-state does not apply")
    approval = check_approval(approval)
    settings, ledger = (Path(item).parent.resolve() / Path(item).name for item in _production_paths())
    return dict(dir=None, settings=settings, ledger=ledger, production=True, approval=approval)


def check_production_options(args):
    """`--production-ledger` belongs only to `--canary` and `--export-canary-admissions` (and never `--reset`)."""
    if not getattr(args, "production_ledger", False):
        return
    if args.reset_canary_state or args.set_cap or not (args.canary or args.export_canary_admissions):
        raise Refused("--production-ledger belongs only to --canary and --export-canary-admissions")


STATE_FILES = {"settings.json", "ledger.sqlite3", ".settings.lock", "ledger.sqlite3-wal", "ledger.sqlite3-shm",
               "ledger.sqlite3-journal"}


def reset_state(path, processes, home=None):
    """Delete the dedicated canary state (after an aborted pilot). Refused unless the path passes
    `check_canary_state` (so never the production state), exists and holds only the state's own files, and no harness
    process is alive: a harness run, a harness OpenCode server, or a routing child pinned to this directory."""
    state = check_canary_state(path, home=home)
    directory = state["dir"]
    if not directory.is_dir():
        raise Refused("there is no canary state directory to reset")
    own = os.getpid()
    for line in (processes or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) == own:
            continue
        command = parts[1]
        if (any(script in command for script in HARNESS_SCRIPTS) and "--reset-canary-state" not in command) \
                or "opencode serve --hostname 127.0.0.1" in command or ("routing.py" in command and str(directory) in command):
            raise Refused("a harness process is still alive; stop it before resetting the canary state")
    names = sorted(entry.name for entry in directory.iterdir())
    unexpected = [name for name in names if name not in STATE_FILES]
    if unexpected:
        raise Refused("the canary state directory holds unexpected files; inspect it first")
    shutil.rmtree(directory)
    if directory.exists():
        raise Refused("the canary state directory could not be removed")
    return dict(removed=True, files=names)


def initialize_state(routing, production=False):
    """Create the canary routing state on first use; never touch an existing one.

    `set authority_required=true` is the only `set` form that creates a ledger; the flag is one-way and applies to
    this canary ledger only. Smart Routing ON and API Usage off are written explicitly; no organization is created.
    The production state (`production`) is never created or initialized here: it must already exist, and the
    preflight then refuses it unless it is clean as it stands."""
    pins = dict(zip(routing.argv[::2], routing.argv[1::2]))
    settings, ledger = Path(pins["--settings"]), Path(pins["--ledger"])
    if production:
        if not ledger.exists():
            raise Refused("the production routing ledger does not exist; the canary never creates or initializes it")
        return dict(created=False, production=True)
    if ledger.exists():
        return dict(created=False)
    settings.parent.mkdir(mode=0o700, exist_ok=True)
    answer, _ = routing("set", authority_required=True)
    if answer.get("state") != "updated":
        raise Refused("could not initialize the canary ledger: %s" % answer.get("reason"))
    status, _ = routing("status")
    answer, _ = routing("set", expected_revision=status.get("settings_revision"),
                        change=dict(smart_routing=True, api_usage_enabled=False))
    if answer.get("state") != "updated":
        raise Refused("could not write the canary settings: %s" % answer.get("reason"))
    return dict(created=True, authority_required="one-way flag set on the canary ledger only; the production routing "
                                                 "state is untouched", settings=dict(smart_routing=True, api_usage_enabled=False))


def anomalies(status):
    """Reasons a routing `status` is not clean for a subscription-only pilot (settings and ledger)."""
    if (status or {}).get("state") != "ok" or status.get("settings_state") != "ok":
        return ["status_unavailable"]
    reasons = []
    settings = status.get("settings") or {}
    if settings.get("smart_routing") is not True:
        reasons.append("smart_routing_off")
    if settings.get("api_usage_enabled") is not False:
        reasons.append("api_usage_enabled")
    if status.get("test_inputs"):
        reasons.append("test_inputs_on")
    if status.get("project_pinned") is not True:
        reasons.append("project_not_pinned")
    ledger = status.get("ledger") or {}
    if ledger.get("state") != "ready":
        return reasons + ["ledger_unavailable"]
    if ledger.get("authority_required") is not True:
        reasons.append("authority_required_off")
    if any(org.get("enabled") for org in ledger.get("organizations") or []):
        reasons.append("organization_enabled")
    if ledger.get("unstamped_open_holds") != 0:
        reasons.append("unstamped_open_holds")
    if (ledger.get("cost_unknown") or {}).get("ids") or (ledger.get("holds") or {}).get("cost_unknown"):
        reasons.append("cost_unknown")
    if (ledger.get("holds") or {}).get("uncertain"):
        reasons.append("uncertain_holds")
    if (ledger.get("uncertain_dollar") or {}).get("ids"):
        reasons.append("uncertain_dollar")
    if ledger.get("blocked_organizations"):
        reasons.append("blocked_organizations")
    if ledger.get("overrun_backlog"):
        reasons.append("overrun_backlog")
    if ledger.get("reconcile_hint") is not None:
        reasons.append("reconcile_pending")
    return reasons  # F5.A1: saved caps are not a protection, so their value is no anomaly


def preflight(status):
    """Every refusal decidable before any write to the project or any server start. `routing.health` answers `ok`
    exactly when the settings are readable and the ledger is ready, which `anomalies` already requires; the RPC itself
    and the probes are checked by `preconditions` once the server runs."""
    reasons = anomalies(status)
    if reasons == ["status_unavailable"]:
        return reasons
    if ((status.get("ledger") or {}).get("open_attempts") or 0) != 0:
        reasons.append("open_attempts")
    return reasons


def preconditions(status, health, probes):
    """Every reason to refuse the pilot once the server runs; empty means it may start."""
    reasons = preflight(status)
    if reasons == ["status_unavailable"]:
        return reasons
    if (health or {}).get("state") != "ok":
        reasons.append("routing_unhealthy")
    if (probes or {}).get("chain") != []:
        reasons.append("guard_probe_failed")
    return reasons


def plan_assignments(n, tag, now=None, pool=POOL, account=ACCOUNT, route=None, limit=None):
    """`n` (1 to `limit`, default PILOT_MAX) explicit subscription admissions cycling three roles and two complexity
    bands. `route` is the approved `{provider, model, effort, harness}`; there is no default route."""
    if not route:
        raise Refused("the pilot needs its approved --route")
    route = dict(route)
    limit = PILOT_MAX if limit is None else min(limit, PILOT_MAX)
    if type(n) is not int or not 1 <= n <= limit:
        raise Refused("the pilot runs 1 to %d admissions" % limit)
    now = int(time.time()) if now is None else now
    plan = []
    for index in range(n):
        role, profile_role, explicit = ROLES[index % len(ROLES)]
        band = BANDS[(index // len(ROLES)) % len(BANDS)]
        attempt = "%s-%02d" % (tag, index + 1)
        assignment = dict(attempt_id=attempt, task="%s-task-%02d" % (tag, index + 1), scope="pilot-scope",
                          choice=dict(route, funding="subscription"))
        if explicit:
            assignment["role"] = explicit
        job = dict(id="job-" + attempt, role=profile_role, scope_ref="pilot-scope", objective="o", context="c",
                   profile=dict(complexity_band=band, risk_band="medium", review_policy="independent",
                                context_tokens=100, tools=["read"], uncertainty="low"))
        observations = dict(now=now, routes=[dict(route="sub", strategy_id="s-sub", **route, account=account,
                                                  billing_class="included_subscription", pool=pool, demand={"5h": 10},
                                                  capable=True, permitted=True, binding_enforceable=True)],
                            pools={pool: dict(windows=[dict(id="5h", limit=1000, used=100, length=18000, reset_at=None,
                                                            rolling=True, fresh=True)])})
        plan.append(dict(label=dict(role=role, band=band, index=index + 1),
                         body=dict(assignment=assignment, job=job, observations=observations)))
    return plan


class Abort(Exception):
    def __init__(self, reason, detail=None, origin=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.origin = origin


class PilotDriver:
    """Runs the pilot admissions one at a time (never more than one open), each with a write-ahead reservation in
    the durable `pilot` purpose. Any abort condition, and any exception or signal, stops launching, recovers the
    current admission by phase, and runs the kill switch once."""

    OPS = ("prepare", "health", "dispatch", "attach", "check", "record")  # per-admission timings, kept even on abort

    def __init__(self, env, ledger, n=PILOT_MAX, run="pilot", clock=time.monotonic, max_duration=MAX_DURATION_S,
                 tag=None, now=None, route=None):
        self.env, self.ledger, self.run_id, self.clock, self.max_duration = env, ledger, run, clock, max_duration
        self.plan = plan_assignments(n, tag or run, now, route=route)
        self.current = None
        self.results = dict(planned=n, completed=0, admissions=[], settlements={}, abort=None, repo=[], deviations=[],
                            latency={op: [] for op in self.OPS}, blocked_titles=0)

    def run(self):
        started = self.clock()
        try:
            for item in self.plan:
                index = item["label"]["index"]
                self.current = dict(index=index, attempt=item["body"]["assignment"]["attempt_id"], phase="planned",
                                    ref=None, settled=False, timings={}, session=None)
                if self.clock() - started > self.max_duration:
                    raise Abort("max_duration")
                self.admit(item, first=index == 1)
                if index % REPO_CHECK_EVERY == 0:
                    self.repo_check(index)
            self.current = None
            self.repo_check("end")
        except Abort as stop:
            self.quiesce()  # first: no signal may skip the recovery
            self.abort(stop.reason, stop.detail, stop.origin or exception_origin(stop))
        except BaseException as error:  # noqa: BLE001 - every exception and signal ends the pilot the same way
            self.quiesce()
            interrupted = isinstance(error, KeyboardInterrupt) or type(error).__name__ == "Interrupted"
            self.abort("interrupted" if interrupted else "unexpected_error", type(error).__name__, exception_origin(error))
        latency = self.results["latency"]
        self.results["latency_summary"] = {key: summarize_ms(values) for key, values in latency.items()}
        return self.results

    def repo_check(self, at):
        changes = self.env.repo_changes()
        self.results["repo"].append(dict(at=at, **changes))
        if changes.get("new"):
            raise Abort("repo_changed", changes["new"])

    def phase(self, name):
        self.current["phase"] = name

    def recover(self):
        """What the current admission's hold needs after an abort, by how far it got."""
        current = self.current
        if current is None:
            return "none"
        phase = PHASES.index(current["phase"])
        if current["ref"] is not None and not current["settled"] and phase < PHASES.index("launching"):
            self.ledger.settle(current["ref"], 0, "aborted before any launch")  # nothing could have been sent
            current["settled"] = True
        attempt = current["attempt"]
        if current["phase"] in ("preparing", "admitted", "dispatching"):
            # Admitted (or maybe) and never dispatched to the host: `not_dispatched` releases it; refused if not admitted.
            answer, _ = self.env.record(dict(event_id=attempt + "-not-dispatched", attempt_id=attempt, kind="not_dispatched"))
            return "not_dispatched" if answer.get("state") == "recorded" else "not_dispatched_refused:%s" % answer.get("reason")
        if current["phase"] in ("dispatched", "attached"):
            return "left_dispatched"  # dispatched, nothing launched: no proof of non-launch exists, so it stays counted
        if current["phase"] in ("launching", "launched"):
            answer, _ = self.env.record(dict(event_id=attempt + "-uncertain", attempt_id=attempt, kind="uncertain"))
            return "uncertain" if answer.get("state") == "recorded" else "uncertain_refused:%s" % answer.get("reason")
        return "none"

    def quiesce(self):
        if callable(getattr(self.env, "quiesce", None)):
            self.env.quiesce()  # no further signal interrupts the recovery

    def timed(self, op, action):
        began = time.perf_counter()
        try:
            return action()
        finally:
            self.current["timings"][op] = round((time.perf_counter() - began) * 1000, 2)

    def abort(self, reason, detail=None, origin=None):
        try:
            recovery = self.recover()
        except BaseException as error:  # noqa: BLE001
            recovery = "error:%s" % type(error).__name__
        current = self.current or {}
        shapes = None
        if current.get("session") and current.get("phase") in ("launched", "recorded") and \
                callable(getattr(self.env, "diagnostics", None)):
            try:
                shapes = self.env.diagnostics(current["session"])
            except BaseException as error:  # noqa: BLE001
                shapes = dict(unavailable=type(error).__name__)
        try:
            switched = self.env.kill_switch()
        except BaseException as error:  # noqa: BLE001
            switched = dict(state="error", reason=type(error).__name__)
        self.results["abort"] = dict(reason=reason, admission=current.get("index"), phase=current.get("phase"), detail=detail,
                                     origin=origin, timings=dict(current.get("timings") or {}), recovery=recovery,
                                     kill_switch=switched, kill_switch_ok=(switched or {}).get("state") == "updated")
        if callable(getattr(self.env, "progress", None)):
            try:
                self.results["abort"]["account_progress"] = self.env.progress()
            except BaseException as error:  # noqa: BLE001
                self.results["abort"]["account_progress"] = dict(unavailable=type(error).__name__)
        if shapes is not None:
            self.results["abort"]["shapes"] = shapes

    def admit(self, item, first):
        env, label, current = self.env, item["label"], self.current
        if callable(getattr(env, "export_if_due", None)):  # the 12-hour replay export, on the next routing step
            exported = env.export_if_due()
            if exported is not None:
                self.results.setdefault("exports", []).append(dict(admission=label["index"], exported=exported.get("exported")))
        status = env.status()
        if ((status.get("ledger") or {}).get("open_attempts") or 0) != 0:
            raise Abort("concurrency")
        problems = anomalies(status)
        if problems:
            raise Abort("status_anomaly", problems)
        allowed = 2 if first else 1  # the first admission may carry the one observed title request
        try:
            current["ref"] = self.ledger.reserve("pilot", "admission %d (%s, %s)" % (label["index"], label["role"], label["band"]),
                                                 allowed, run=self.run_id)
        except BudgetExceeded:
            raise Abort("cap_breach") from None
        attempt = current["attempt"]
        self.phase("preparing")
        timings = current["timings"]
        answer, timings["prepare"] = env.prepare(item["body"])
        if answer.get("state") != "admitted" or (answer.get("admission") or {}).get("billing_class") != "included_subscription":
            raise Abort("admission_refused", answer.get("reason"))
        self.phase("admitted")
        if (self.timed("health", env.health) or {}).get("state") != "ok":
            raise Abort("routing_unhealthy")
        self.phase("dispatching")
        answer, timings["dispatch"] = env.dispatch(attempt)
        if answer.get("state") != "dispatched":
            raise Abort("dispatch_refused", answer.get("reason"))
        self.phase("dispatched")
        token, session, message = answer["authority"], answer["session_id"], answer["message_id"]
        current["session"] = session
        if not self.timed("attach", lambda: env.attach(session, attempt, token)):
            raise Abort("recovery_error", "attach")
        self.phase("attached")
        self.phase("launching")
        launched = env.launch(session, message, allow_title=first)
        if not launched.get("idle"):
            raise Abort("unresolved_launch")  # the reservation stays open: its exposure counts in full
        self.phase("launched")
        if launched.get("check_ms") is not None:
            timings["check"] = launched["check_ms"]
        sent = launched["transmitted"]
        current["settled"] = True
        try:
            self.ledger.settle(current["ref"], sum(sent.values()))
        except BudgetExceeded:
            raise Abort("cap_breach") from None
        if launched.get("off_route"):
            raise Abort("non_haiku_request")
        if sent.get("primary") != 1 or sent.get("title", 0) > (1 if first else 0) or sent.get("other", 0):
            raise Abort("cap_breach" if sent.get("primary", 0) > 1 or sent.get("title", 0) or sent.get("other", 0)
                        else "managed_request_blocked", sent)
        self.results["blocked_titles"] += launched.get("blocked_titles", 0)
        try:
            accounting = env.account(session, titles_sent=sent.get("title", 0))
        except Exception as error:  # noqa: BLE001 - any accounting refusal is a recovery error
            raise Abort("recovery_error", classify(error), exception_origin(error)) from None
        if accounting.get("deviation"):
            self.results["deviations"].append(dict(admission=label["index"], deviation=accounting["deviation"]))
        answer, timings["record"] = env.record(dict(event_id=attempt + "-complete", attempt_id=attempt, kind="complete",
                                            stage="worker", usage=accounting.get("usage")))
        settlement = answer.get("settlement") or {}
        if answer.get("state") != "recorded":
            raise Abort("recovery_error", "record complete: %s" % answer.get("reason"))
        self.phase("recorded")
        if settlement.get("kind") != "window" or settlement.get("charged"):
            raise Abort("funding_violation", settlement.get("kind"))
        if not env.detach(session, attempt, token):
            raise Abort("recovery_error", "detach")
        after = anomalies(env.status())
        if after:
            raise Abort("status_anomaly", after)
        self.phase("completed")
        self.results["completed"] += 1
        self.results["settlements"][settlement["kind"]] = self.results["settlements"].get(settlement["kind"], 0) + 1
        for op, value in timings.items():
            self.results["latency"][op].append(value)
        kinds = [r["kind"] for r in ((accounting.get("usage") or {}).get("requests") or [])]
        self.results["admissions"].append(dict(label, sent=sent, unknown=sorted(accounting.get("unknown") or []),
                                               kinds=kinds, title_state=(accounting.get("title") or {}).get("state"),
                                               rereads=accounting.get("rereads", 0),
                                               settlement=settlement.get("kind"), outcome=launched.get("outcome"),
                                               released=sorted(settlement.get("released") or {}),
                                               charged=settlement.get("charged") or 0, timings=dict(timings)))
