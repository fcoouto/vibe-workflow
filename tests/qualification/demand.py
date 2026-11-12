"""`measure-demand`: per-launch window demand measured live at no extra cost (user design rule, 10 November 2026).

No model call, process or provider read is added to measure. Every reading comes from data the budgeted launches
already produce:
- Claude route: the settled launch's own `native.rateLimit` windows, which `opencode_launcher.launch` stores in the
  run's temporary window store (`routing_observations.window_path`), and the drill's re-read of its idle session.
- ChatGPT/Codex pool: one Codex rate-limit read before and one after the A5 prompt through the read-only reader
  runtime/codex_rate_limits.py (`env.codex_window()`, zero tokens). A failed read records the reader's fixed reason
  and the pool has no sample from that run; A5 itself never fails for it. The path rules' declared windows for the
  pool are used when they declare any; when they declare none, the reader's known windows are read and each reading,
  per-launch entry and suggestion is marked `declared: false`.

F4.3: each reading is tagged with the path id of the route the harness launched (`path`), the live run (`run`) and
the launch's binding (`account`, `model`, `effort`) beside its `pool`. Readings pair only on the same path and
account. A reading without a path (written before F4.3) is reported as `path_untagged` and never becomes a path
sample; a pair where either reading has no account is `account_unknown`, never a known-account sample. For each subscription path where one known account's own samples cover every window, the report proposes
one `qualify_path` request bound to that account with the measured demand and the run id as evidence; a window whose every sample is `below_resolution`
gets the floor demand BELOW_RESOLUTION_DEMAND and says so; a window without any sample gets no request. A gateway
path is never proposed (decision A).

Readings are appended to an owner-only `demand-readings.jsonl` next to the call ledger (never in a repository), so
consecutive launches across modes (A6 -> A7 -> drill 1) pair up. A launch's demand is the rise of each window's
`used` (utilization thousandths, the stored unit) since the reading just before it, counted only when both share the
window's reset and lie at most STALE_S apart; anything else is reported with its reason, never guessed. Other use of
the same login in between (the user's own sessions) can only raise a delta, so a measured value is an upper bound.
The suggestion for the manifest is the largest delta per window plus MARGIN_PCT (at least 1).
"""
import json
import math
import os
from pathlib import Path
import stat
import time

from tests.qualification.safety import Refused

FILE = "demand-readings.jsonl"
STALE_S = 900
MARGIN_PCT = 50
UNIT = "utilization_thousandths"
BINDING = ("path", "run", "account", "model", "effort")
BELOW_RESOLUTION_DEMAND = 10  # approved call 29: an unchanged reading pair is not a measured cost bound
MAX_DEMAND = 1000  # thousandths of a window: what `qualify_path` accepts
MAX_EVIDENCE = 128


def path_for(provider):
    """The path id of an OpenCode launch on `provider` (its shim harness and provider), or None."""
    from runtime import routing_launch, routing_paths
    harness = routing_launch.shim_harness(provider)
    try:
        rules = routing_paths.load_rules()
    except ValueError:
        return None
    return next((item["path"] for item in rules["paths"]
                 if (item["harness"], item["provider"]) == (harness, provider)), None)


def log_path(ledger_path):
    return Path(ledger_path).parent / FILE


def from_windows(windows, label, pool, at=None, **binding):
    """A reading from stored window entries `{id: {used, reset_at, observed_at, ...}}` or a list of windows, with the
    launch's `binding` (BINDING keys; None values are left out)."""
    items = windows.items() if isinstance(windows, dict) else [(w.get("id"), w) for w in windows or []]
    found = {}
    for window_id, window in items:
        if isinstance(window, dict) and type(window.get("used")) in (int, float) and window_id:
            found[str(window_id)] = dict(used=window["used"], reset_at=window.get("reset_at"),
                                         observed_at=window.get("observed_at"))
    if not found:
        return None
    reading = dict(label=label, pool=pool, at=int(at if at is not None else time.time()), windows=found, unit=UNIT)
    reading.update({key: binding[key] for key in BINDING if binding.get(key) is not None})
    return reading


def from_store(settings_path, pool, label, **binding):
    """The reading the launcher stored after a settled subscription launch (no provider call)."""
    from runtime import routing_observations
    try:
        value = routing_observations._read(routing_observations.window_path(settings_path))
    except (OSError, ValueError):
        return None
    return from_windows(((value or {}).get("pools") or {}).get(pool) or {}, label, pool, **binding)


def from_observation(observation, label, pool, **binding):
    """A reading from an accounting result's `rate_limit` (the drill's re-read of its idle session)."""
    from runtime import opencode_launcher
    if not observation:
        return None
    return from_windows(opencode_launcher.windows(observation), label, pool, **binding)


class DemandLog:
    """The append-only, owner-only readings file next to the call ledger."""

    def __init__(self, path):
        self.path = Path(path)

    def append(self, reading):
        if reading is None:
            return False
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as handle:
            info = os.fstat(handle.fileno())
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise Refused("the demand readings file must be owner-only (0600)")
            handle.write(json.dumps(reading, sort_keys=True) + "\n")
        return True

    def readings(self):
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return []
        found = []
        for line in text.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue  # a damaged line is skipped, never guessed
            if isinstance(value, dict) and isinstance(value.get("windows"), dict) and type(value.get("at")) is int:
                found.append(value)
        return found


def measure(readings):
    """Per launch (each reading after the first of its path): `{label, path, pool, windows: {id: {delta | reason}}}`
    plus the launch's account, model and effort. A reading without a path is `path_untagged` and pairs with nothing."""
    measured, last = [], {}
    for reading in readings:
        path = reading.get("path")
        before = last.get(path) if path is not None else None
        windows = {}
        for window_id, now in sorted(reading["windows"].items()):
            prior = (before or {}).get("windows", {}).get(window_id)
            if path is None:
                windows[window_id] = dict(reason="path_untagged")  # never relabeled as a path sample
            elif before is None or prior is None:
                windows[window_id] = dict(reason="no_before")
            elif before.get("account") is None or reading.get("account") is None:
                windows[window_id] = dict(reason="account_unknown")  # two absent accounts are not one known account
            elif before.get("account") != reading.get("account"):
                windows[window_id] = dict(reason="account_changed")
            elif prior.get("reset_at") != now.get("reset_at"):
                windows[window_id] = dict(reason="reset_between")
            elif reading["at"] - before["at"] > STALE_S:
                windows[window_id] = dict(reason="stale_before", gap_s=reading["at"] - before["at"])
            elif now["used"] < prior["used"]:
                windows[window_id] = dict(reason="decreased")
            elif now["used"] == prior["used"]:
                windows[window_id] = dict(reason="below_resolution")  # under one stored unit: not a 0 sample
            else:
                windows[window_id] = dict(delta=now["used"] - prior["used"])
        entry = dict(label=reading.get("label"), path=path, pool=reading.get("pool"), windows=windows)
        entry.update({key: reading[key] for key in ("account", "model", "effort") if key in reading})
        if reading.get("declared") is False:
            entry["declared"] = False
        measured.append(entry)
        if path is not None:
            last[path] = reading
    return measured


def suggest(measured, margin_pct=MARGIN_PCT):
    """Per path and window: samples, the largest delta and the suggested `demand` (largest + margin, >= 1)."""
    found = {}
    for entry in measured:
        for window_id, value in entry["windows"].items():
            if "delta" not in value or entry.get("path") is None:
                continue
            slot = found.setdefault(entry["path"], {}).setdefault(window_id, dict(samples=0, max_delta=0))
            slot["samples"] += 1
            if entry.get("declared") is False:
                slot["declared"] = False  # the path rules declare no window for this pool yet
            slot["max_delta"] = max(slot["max_delta"], value["delta"])
    for windows in found.values():
        for slot in windows.values():
            slot["demand"] = max(1, math.ceil(slot["max_delta"] * (100 + margin_pct) / 100))
            slot["margin_pct"], slot["unit"] = margin_pct, UNIT
    return found


def _usable(value):
    return "delta" in value or value.get("reason") == "below_resolution"


def requests(readings, measured, rules=None):
    """(the `qualify_path` requests, a per-path account of why each path has one or not). One request per
    subscription path, bound to the one known account whose own samples cover every rule window: a below-resolution-
    only window gets the floor and is named; samples of several accounts are never stitched (`account_ambiguous`),
    samples without an account never qualify (`account_unknown`); a gateway path is never proposed; the evidence is the
    run id(s) of that path's readings for that account."""
    from runtime import routing_paths
    rules = routing_paths.load_rules() if rules is None else rules
    by_id = {item["path"]: item for item in rules["paths"]}
    found, paths = [], {}
    for path in sorted({entry["path"] for entry in measured if entry.get("path") is not None}):
        rule = by_id.get(path)
        if rule is None:
            paths[path] = dict(request=False, reason="path_unknown")
            continue
        if rule["billing_class"] != routing_paths.SUBSCRIPTION:
            paths[path] = dict(request=False, reason="gateway_not_qualified")
            continue
        mine = [entry for entry in measured if entry.get("path") == path]
        accounts = sorted({entry["account"] for entry in mine if entry.get("account") is not None
                           and any(_usable(value) for value in entry["windows"].values())})
        if len(accounts) > 1:
            paths[path] = dict(request=False, reason="account_ambiguous", accounts=accounts)
            continue
        if not accounts:
            unknown = any(value.get("reason") == "account_unknown" for entry in mine for value in entry["windows"].values())
            paths[path] = dict(request=False, reason="account_unknown" if unknown else "window_unmeasured",
                               missing_windows=list(rule["windows"]), floor_windows=[])
            continue
        account = accounts[0]
        scoped = [entry for entry in mine if entry.get("account") == account]
        sampled = suggest(scoped).get(path, {})
        demand, floor, missing = {}, [], []
        for window in rule["windows"]:
            slot = sampled.get(window)
            below = any(entry["windows"].get(window, {}).get("reason") == "below_resolution" for entry in scoped)
            if slot is not None:
                demand[window] = slot["demand"]
            elif below:
                demand[window] = BELOW_RESOLUTION_DEMAND
                floor.append(dict(window=window, reason="below_resolution", demand=BELOW_RESOLUTION_DEMAND))
            else:
                missing.append(window)
        runs = sorted({reading["run"] for reading in readings if reading.get("path") == path
                       and reading.get("account") == account and reading.get("run")})
        evidence = "+".join(runs)
        reason = ("window_unmeasured" if missing or not demand
                  else "demand_over_scale" if max(demand.values()) > MAX_DEMAND
                  else "evidence_missing" if not runs else "evidence_too_long" if len(evidence) > MAX_EVIDENCE else None)
        if reason is not None:
            paths[path] = dict(request=False, reason=reason, missing_windows=missing, floor_windows=floor)
            continue
        paths[path] = dict(request=True, account=account, floor_windows=floor, missing_windows=[])
        found.append(dict(v=1, op="qualify_path", path=path, demand=demand, evidence=evidence, account=account))
    return found, paths


def report(log, rules=None):
    """The `measure-demand` answer over every reading in the log (0 calls)."""
    readings = log.readings()
    measured = measure(readings)
    suggestion = suggest(measured)
    proposed, paths = requests(readings, measured, rules)
    return dict(readings=len(readings), per_launch=measured, suggestion=suggestion, requests=proposed, paths=paths,
                stale_s=STALE_S,
                note="upper bounds: other use of the same login between two readings raises a delta; stored windows "
                     "are whole thousandths, so a launch below one thousandth is below_resolution, not a sample; "
                     "`suggestion` is a per-path diagnostic over known-account samples, while each request is bound to "
                     "one account's own samples; a request qualifies a path's demand only, never its billing boundary "
                     "or account entitlement")
