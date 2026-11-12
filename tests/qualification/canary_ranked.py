"""The ranked canary (plan Task F7.2; validation Edit 7 point 9 and approved call 36): approval pins, reservation
coverage and the reset deadline before the run, and per request the shared preview, the stale-and-needed refresh,
the seed check, the exact-admission gate, the 100-thousandths window allowance and the settled telemetry.

It runs only for the production-ledger canary (`opencode_live.Run.canary_run`); the pilot and isolation modes keep
their single explicit `--route`. `canary.PilotDriver` calls a `Controller` at three existing boundaries:

- `before_prepare(body)`, outside the preparation timer: the deadline, the pinned inputs, then one shared
  `scripts/routing.py` `Service.preview` for the exact request the served child receives (no ledger, window read,
  reader or write). Only its kept subscription routes reach `routing_observations.refresh_needed`, which delegates
  stale-and-needed ChatGPT reads to the existing bounded reader (FRESH_SECONDS, backoff); preview itself reads no
  usage. `window_status` and one `LocalSource.windows` read per kept pool then decide whether any kept pool has
  every demanded window fresh with a future reset; if none has, the run stops with a resumable `seed_required`
  naming the pool/windows. `more > 0` is incomplete status, never proof of freshness.
- `before_dispatch(admission)`: a non-ranked admission aborts; the admission must be subscription-funded on an
  approved path, its route a strategy route of the pinned table, with that route's pool (exact equality, not
  membership somewhere in the approval); the pinned inputs and the deadline are rechecked; the reservation plus this
  run's other unsettled reservations must fit the remaining allowance. A stop here leaves the admission undispatched,
  so the driver releases it (`not_dispatched`).
- `after_settlement(admission, accounting)`: only the windows the launch reported are converted
  (`opencode_launcher.windows`) and stored (`routing_observations.store_window`), at their observation time, as the
  launcher's `_finish` does; each approved window's positive increase from the seeded baseline accumulates (unrelated
  use included). A missing `7d` is never refreshed by a reported `5h`.

Stops: `allowance_exhausted` and `window_usage_unknown` (any reset of a demanded window, an ambiguous decrease, or
`now >= earliest_reset` at any boundary, even without a crossing launch) end the run incomplete; an observed overrun
aborts. Nothing replenishes the allowance, restarts the deadline or erases an unknown delta: a stopped controller
stays stopped. Debits from abandoned windows are never dropped here; one that blocks progress is reported for the
existing one-shot operator procedure. No timer, poll, retry loop, model call or persistent route cache.
"""
import hashlib
import json
import time

from runtime import opencode_launcher, routing, routing_observations as collector, routing_paths, routing_settings
from runtime import task_profile
from runtime.usage_budget import Ledger
from tests.qualification import canary
from tests.qualification.safety import Refused, harness_for

ALLOWANCE = 100  # thousandths of each approved pool/window over the whole run (call 36)
SAMPLE_MINIMUM = 60
SPECIALISTS = tuple(label for label, _, _ in canary.ROLES)
RISK_BAND = "medium"  # the canary plan's profile risk band
REFUSED = ("table_outdated", "table_empty", "no_generated_route", "no_qualified_path")
ACKNOWLEDGEMENT = "low-coverage acknowledged"  # the approval SCOPE text that accepts a coverage below the minimum
DEBIT_ADVICE = ("An abandoned-window debit blocks the canary; the harness never drops it. The operator reconciles it "
                "with the existing one-shot procedure, then a new run starts with a renewed preflight.")


def acknowledged(approval):
    return ACKNOWLEDGEMENT in str((approval or {}).get("scope") or "")


_acknowledged = acknowledged  # `setup`'s parameter of the same name overrides it


def _approved_paths(rules, qualified, harnesses):
    """The qualified OpenCode subscription paths (no other host, not test-only) of the signed-in providers."""
    return [item for item in routing_paths.resolved(rules, qualified)
            if item["billing_class"] == routing_paths.SUBSCRIPTION and item.get("host") is None
            and item.get("status") != routing_paths.TEST_ONLY and item["harness"] in set(harnesses)]


def approval_inputs(settings_path, harnesses):
    """The inputs the approval pins: approved path ids, their generated strategy routes, the table's content digest,
    the existing `routes_version`, the effective host constraints, the settings revision and margin. Reads the
    packaged rules and the files beside the settings only. `Refused` with REFUSED's reasons."""
    rules = routing_paths.load_rules()
    qualified = routing_paths.qualifications(settings_path)
    paths = _approved_paths(rules, qualified, harnesses)
    if not paths:
        raise Refused("ranked canary refused: no_qualified_path")
    target = settings_path.parent / routing_paths.TABLE_FILE
    raw = target.read_bytes() if target.is_file() else b""
    table = routing_paths._table(settings_path)
    if not table.get("rows"):
        raise Refused("ranked canary refused: table_empty")
    ids = {item["path"] for item in paths}
    routes = [route for route in routing_paths.generate(dict(rules, paths=paths), qualified, table, ())["routes"]
              if route["path"] in ids and route.get("strategy_id")]
    if not routes:
        raise Refused("ranked canary refused: no_generated_route")
    revision, _ = routing_settings._load(settings_path)
    return dict(paths=sorted(ids), routes={route["route"]: {key: route[key] for key in (
        "strategy_id", "provider", "model", "effort", "harness", "pool", "path", "demand")} for route in routes},
        table_version=table.get("version"), table_digest=hashlib.sha256(raw).hexdigest(),
        routes_version=collector.LocalSource(settings_path).routes_version(table),
        host_constraints=sorted(set(harnesses)), settings_revision=revision,
        margin_bp=routing_settings.read(settings_path)["margin_bp"])


def demanded(pins):
    """`{pool: {window: largest qualified demand}}` over the pinned subscription routes on approved paths."""
    found = {}
    for route in pins["routes"].values():
        for window, value in route["demand"].items():
            pool = found.setdefault(route["pool"], {})
            pool[window] = max(pool.get(window, 0), value)
    return found


def _rows(table):
    return {key: {item.get("strategy_id") for item in value if isinstance(item, dict)}
            for key, value in (table.get("rows") or {}).items() if isinstance(value, list)}


def coverage(pins, table=None):
    """Call 36's conservative full-reservation coverage (not a usage forecast): per approved pool/window `d_w` (the
    largest qualified demand), `r_w = Ledger._reserve(d_w, None, margin_bp)` as admission reserves it, and
    `floor(ALLOWANCE / r_w)`; the minimum across demanded windows; and the six profiles' structural coverage
    (a pinned table row with a pinned strategy). Structural coverage does not promise feasible windows."""
    windows = {}
    for pool, items in sorted(demanded(pins).items()):
        for window, demand in sorted(items.items()):
            reservation = Ledger._reserve(demand, None, pins["margin_bp"])
            windows["%s/%s" % (pool, window)] = dict(demand=demand, reservation=reservation,
                                                     full_reservations=ALLOWANCE // reservation)
    strategies = {route["strategy_id"] for route in pins["routes"].values()}
    rows = _rows(table or {})
    profiles = {"%s/%s" % (specialist, band): bool(rows.get("%s|%s|%s" % (
        task_profile.work_kind(specialist), band, RISK_BAND), set()) & strategies)
        for specialist in SPECIALISTS for band in canary.BANDS}
    return dict(windows=windows, minimum=min((item["full_reservations"] for item in windows.values()), default=0),
                sample_minimum=SAMPLE_MINIMUM, profiles=profiles, funding_classes=["subscription"])


def _stored(settings_path, pool, at):
    return {item["id"]: item for item in collector.LocalSource(settings_path).windows(pool, at)}


def baseline(settings_path, pins, at):
    """One bounded `LocalSource.windows` read per approved pool: each demanded window's `{used, reset_at}`; `Refused`
    when a reset is unknown or already passed (stale/unknown even when the reading looks fresh)."""
    found, missing = {}, []
    for pool, items in sorted(demanded(pins).items()):
        stored = _stored(settings_path, pool, at)
        for window in sorted(items):
            item = stored.get(window)
            reset = (item or {}).get("reset_at")
            if type(reset) is not int or reset <= at or type(item.get("used")) is not int:
                missing.append("%s/%s" % (pool, window))
            else:
                found["%s/%s" % (pool, window)] = dict(used=item["used"], reset_at=reset,
                                                       observed_at=item.get("observed_at"))
    if missing:
        raise Refused("ranked canary refused: reset unknown or passed for %s; seed it (operator) first"
                      % ", ".join(missing))
    return found


def preflight(settings_path, harnesses, at, acknowledged=False):
    """The approval record of the run: pins, coverage, earliest demanded reset and time remaining. `Refused` on
    missing demand, profile coverage or reset metadata, and on coverage below the sample minimum unless the start
    approval acknowledges the incomplete-sample risk. Local store reads only, outside preview and any timer."""
    pins = approval_inputs(settings_path, harnesses)
    report = coverage(pins, routing_paths._table(settings_path))
    if not report["windows"] or not all(report["profiles"].values()):
        raise Refused("ranked canary refused: demand or profile coverage is missing")
    windows = baseline(settings_path, pins, at)
    earliest = min(item["reset_at"] for item in windows.values())
    if report["minimum"] < SAMPLE_MINIMUM and not acknowledged:
        raise Refused("ranked canary refused: reservation coverage %d is below the %d-admission minimum; the start "
                      "approval must acknowledge the incomplete-sample risk" % (report["minimum"], SAMPLE_MINIMUM))
    return dict(pins=pins, coverage=report, baseline=windows, earliest_reset=earliest, remaining_s=earliest - at,
                low_coverage_acknowledged=report["minimum"] < SAMPLE_MINIMUM)


class Allowance:
    """Each approved pool/window's cumulative positive observed increase from the seeded baseline, and this run's
    unsettled reservations. Never replenished: a reset or an ambiguous decrease stops it for good."""

    def __init__(self, base):
        self.baseline = {key: dict(value) for key, value in base.items()}
        self.last = {key: value["used"] for key, value in base.items()}
        self.seen = {key: value.get("observed_at") or 0 for key, value in base.items()}  # newest reading counted
        self.cumulative = dict.fromkeys(base, 0)
        self.holds, self.stopped = {}, None

    def _stop(self, error):
        self.stopped = error
        raise error

    def observe(self, pool, window, used, reset_at, observed_at=None, newer_only=False):
        """Count one reading. A stored reading (`newer_only`) counts only when strictly newer than the last counted
        one; a launch's own telemetry also when it is dated the same second (readings are whole seconds)."""
        key = "%s/%s" % (pool, window)
        if key not in self.baseline:
            return
        if observed_at is not None:
            if observed_at < self.seen[key] or (newer_only and observed_at == self.seen[key]):
                return  # already counted (or older): one reading counts once
            self.seen[key] = observed_at
        if reset_at != self.baseline[key]["reset_at"] or used < self.last[key]:
            self._stop(canary.Incomplete("window_usage_unknown", [key]))
        self.cumulative[key] += used - self.last[key]
        self.last[key] = used
        if self.cumulative[key] > ALLOWANCE:
            self._stop(canary.Abort("window_overrun", key))

    @staticmethod
    def _windows(reserved):
        return {key[len(routing.WIN):]: value for key, value in (reserved or {}).items() if key.startswith(routing.WIN)}

    def check(self, reserved, attempt=None):
        held = {}
        for other, vector in self.holds.items():
            if other != attempt:
                for key, value in vector.items():
                    held[key] = held.get(key, 0) + value
        short = [key for key, value in self._windows(reserved).items()
                 if key in self.cumulative and self.cumulative[key] + held.get(key, 0) + value > ALLOWANCE]
        if short:
            raise canary.Incomplete("allowance_exhausted", sorted(short))

    def hold(self, attempt, reserved):
        self.holds[attempt] = self._windows(reserved)

    def release(self, attempt):
        self.holds.pop(attempt, None)


class Controller:
    """The ranked canary's per-request steps for `canary.PilotDriver` (see the module docstring)."""

    def __init__(self, env, settings_path, ledger_path, project, harnesses, record, clock=None):
        self.env, self.settings, self.ledger_path, self.project = env, settings_path, ledger_path, project
        self.harnesses, self.record, self.clock = list(harnesses), record, clock or (lambda: int(time.time()))
        self.pins, self.earliest_reset = record["pins"], record["earliest_reset"]
        self.allowance, self.timings, self.kept = Allowance(record["baseline"]), [], None

    def identity(self, admission):
        """The admitted `(provider, model, variant)` from the pinned strategy route the admission names."""
        route = self.pins["routes"][admission["route"]]
        return route["provider"], route["model"], route["effort"]

    def _deadline(self):
        if self.allowance.stopped is not None:
            raise self.allowance.stopped
        if self.clock() >= self.earliest_reset:
            self.allowance._stop(canary.Incomplete("window_usage_unknown", ["earliest_reset"]))

    def _unchanged(self):
        try:
            current = approval_inputs(self.settings, self.harnesses)
        except Refused:
            current = None
        if current != self.pins:
            raise canary.Incomplete("approval_inputs_changed", ["renewed preflight and approval required"])

    def request_body(self, body):
        """The exact `prepare` body the served child receives (`server.Routing._body`): the preview reads it."""
        return self.env.run.routing._body("prepare", json.loads(json.dumps(body)))

    def preview(self, body):
        from scripts import routing as command
        service = command.Service(self.settings, self.ledger_path, project=self.project)
        try:
            return service.preview(body)
        finally:
            service.close()

    def before_prepare(self, body):
        self._deadline()
        self._unchanged()
        began = time.perf_counter()
        answer = self.preview(self.request_body(body))
        timing = dict(preview_ms=round((time.perf_counter() - began) * 1000, 2))
        if answer.get("state") != "previewed":
            raise canary.Abort("preview_refused", answer.get("reason"))
        reasons = [code for code in (answer.get("ranking") or {}).get("reason_codes") or [] if code in REFUSED]
        if reasons or answer.get("mode") != "ranked":
            raise canary.Abort("ranking_refused", reasons or answer.get("mode"))
        if answer.get("routes_version") != self.pins["routes_version"]:
            raise canary.Incomplete("approval_inputs_changed", ["routes_version"])
        table = routing_paths._table(self.settings)
        kept = [spec for spec in collector.LocalSource(self.settings).manifest(table)
                if spec["route"] in set(answer.get("routes") or []) and spec["billing_class"] == routing_paths.SUBSCRIPTION]
        began = time.perf_counter()
        failed = collector.refresh_needed(kept, self.settings)
        timing.update(refresh_ms=round((time.perf_counter() - began) * 1000, 2), kept=[spec["route"] for spec in kept],
                      refresh_failed=sorted(failed))
        self.timings.append(timing)
        self._sync()
        self._seed_ready(kept)
        self.kept = [spec["route"] for spec in kept]

    def _seed_ready(self, kept):
        if not kept:
            return  # nothing kept: the served prepare answers pending with its own reason (the driver aborts)
        at = self.clock()
        status = collector.window_status(self.settings, at)
        listed = {} if status.get("state") != "ok" else {
            "%s/%s" % (item["pool"], item["window"]): item["fresh"] for item in status["windows"]}
        missing = set()
        for pool in sorted({spec["pool"] for spec in kept}):
            stored = _stored(self.settings, pool, at)
            needed = sorted({window for spec in kept if spec["pool"] == pool for window in spec["demand"]})
            stale = ["%s/%s" % (pool, window) for window in needed
                     if listed.get("%s/%s" % (pool, window)) is not True
                     or type((stored.get(window) or {}).get("reset_at")) is not int or stored[window]["reset_at"] <= at]
            if not stale:
                return
            missing.update(stale)
        raise canary.Incomplete("seed_required", sorted(missing), resumable=True)

    def before_dispatch(self, admission):
        if admission.get("mode") != "ranked":
            raise canary.Abort("non_ranked_admission", admission.get("mode"))
        route = self.pins["routes"].get(admission.get("route"))
        if admission.get("billing_class") != routing_paths.SUBSCRIPTION or route is None \
                or route["path"] not in self.pins["paths"] or admission.get("pool") != route["pool"] \
                or admission.get("route_identity") != routing.route_identity(*(route[key] for key in (
                    "provider", "model", "effort", "harness"))) \
                or (self.kept is not None and admission.get("route") not in self.kept):
            raise canary.Abort("admission_not_approved")
        self._unchanged()
        self._deadline()
        self.allowance.check(admission.get("reserved"), admission.get("attempt_id"))
        self.allowance.hold(admission.get("attempt_id"), admission.get("reserved"))

    def after_settlement(self, admission, accounting):
        self.allowance.release(admission.get("attempt_id"))
        for window in opencode_launcher.windows((accounting or {}).get("rate_limit")):
            collector.store_window(self.settings, admission["pool"], window)
            self.allowance.observe(admission["pool"], window["id"], window["used"], window["reset_at"],
                                   window["observed_at"])
        self._sync()
        self._deadline()

    def _sync(self):
        """Every newer stored reading of an approved window (telemetry, the bounded reader or an operator seed),
        counted once: a window the telemetry never reports still accumulates, and a lower reading is unknown."""
        at = self.clock()
        for pool in sorted({key.split("/", 1)[0] for key in self.allowance.baseline}):
            for window, item in sorted(_stored(self.settings, pool, at).items()):
                if type(item.get("used")) is int and type(item.get("observed_at")) is int:
                    self.allowance.observe(pool, window, item["used"], item.get("reset_at"), item["observed_at"],
                                           newer_only=True)

    def catalog(self, listed):
        """Host catalog entries `{provider, model, effort, harness}` from the isolated server's model list."""
        return [dict(provider=item.get("providerID"), model=item.get("id"), effort=variant,
                     harness=harness_for(item.get("providerID")))
                for item in listed if isinstance(item, dict)
                for variant in [v if isinstance(v, str) else (v or {}).get("id") for v in item.get("variants") or []]]


def setup(run, clock=None, acknowledged=None):
    """The production-ledger canary's controller, before any server starts: the preflight, recorded in the run's
    existing approval record. `Refused` stops the run before anything is written to the project."""
    clock = clock or (lambda: int(time.time()))
    state = run.canary_state
    approval = state.get("approval") or {}
    ack = _acknowledged(approval) if acknowledged is None else acknowledged
    harnesses = run.routing.harnesses
    record = preflight(state["settings"], harnesses, clock(), acknowledged=ack)
    ranked = dict({key: value for key, value in record.items() if key not in ("pins", "baseline")},
                  pins={key: value for key, value in record["pins"].items() if key != "routes"},
                  strategies=sorted(record["pins"]["routes"]))
    run.results["production_ledger"] = dict(approval=dict(approval, ranked=ranked))  # the state's own stays as given
    from tests.qualification.pilot_env import CanaryEnv
    return Controller(CanaryEnv(run), state["settings"], state["ledger"], run.canary_project, harnesses, record,
                      clock)


def summary(results):
    """`results['incomplete']` for a ranked run that ended without a stop: fewer than SAMPLE_MINIMUM admissions is
    incomplete, never a pass; strict replay and the latency budgets still gate a pass."""
    if results.get("abort") or results.get("incomplete") or results.get("completed", 0) >= SAMPLE_MINIMUM:
        return results.get("incomplete")
    return dict(reason="sample_below_minimum", detail=[results.get("completed", 0), SAMPLE_MINIMUM])
