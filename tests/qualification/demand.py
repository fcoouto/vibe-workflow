"""`measure-demand`: per-launch window demand measured live at no extra cost (user design rule, 10 November 2026).

No model call, process or provider read is added to measure. Every reading comes from data the budgeted launches
already produce:
- Claude route: the settled launch's own `native.rateLimit` windows, which `opencode_launcher.launch` stores in the
  run's temporary window store (`routing_observations.window_path`), and the drill's re-read of its idle session.
- ChatGPT/Codex pool: one Codex rate-limit read before and one after the A5 prompt through the read-only reader of
  lane R7 (`env.codex_window()`); until that reader is on `main` it answers None and the pool stays unmeasured.

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


def log_path(ledger_path):
    return Path(ledger_path).parent / FILE


def from_windows(windows, label, pool, at=None):
    """A reading from stored window entries `{id: {used, reset_at, observed_at, ...}}` or a list of windows."""
    items = windows.items() if isinstance(windows, dict) else [(w.get("id"), w) for w in windows or []]
    found = {}
    for window_id, window in items:
        if isinstance(window, dict) and type(window.get("used")) in (int, float) and window_id:
            found[str(window_id)] = dict(used=window["used"], reset_at=window.get("reset_at"),
                                         observed_at=window.get("observed_at"))
    if not found:
        return None
    return dict(label=label, pool=pool, at=int(at if at is not None else time.time()), windows=found, unit=UNIT)


def from_store(settings_path, pool, label):
    """The reading the launcher stored after a settled subscription launch (no provider call)."""
    from runtime import routing_observations
    try:
        value = routing_observations._read(routing_observations.window_path(settings_path))
    except (OSError, ValueError):
        return None
    return from_windows(((value or {}).get("pools") or {}).get(pool) or {}, label, pool)


def from_observation(observation, label, pool):
    """A reading from an accounting result's `rate_limit` (the drill's re-read of its idle session)."""
    from runtime import opencode_launcher
    if not observation:
        return None
    return from_windows(opencode_launcher.windows(observation), label, pool)


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
    """Per launch (each reading after the first of its pool): `{label, pool, windows: {id: {delta | reason}}}`."""
    measured, last = [], {}
    for reading in readings:
        pool = reading.get("pool")
        before = last.get(pool)
        windows = {}
        for window_id, now in sorted(reading["windows"].items()):
            prior = (before or {}).get("windows", {}).get(window_id)
            if before is None or prior is None:
                windows[window_id] = dict(reason="no_before")
            elif prior.get("reset_at") != now.get("reset_at"):
                windows[window_id] = dict(reason="reset_between")
            elif reading["at"] - before["at"] > STALE_S:
                windows[window_id] = dict(reason="stale_before", gap_s=reading["at"] - before["at"])
            elif now["used"] < prior["used"]:
                windows[window_id] = dict(reason="decreased")
            else:
                windows[window_id] = dict(delta=now["used"] - prior["used"])
        measured.append(dict(label=reading.get("label"), pool=pool, windows=windows))
        last[pool] = reading
    return measured


def suggest(measured, margin_pct=MARGIN_PCT):
    """Per pool and window: samples, the largest delta and the suggested manifest `demand` (largest + margin, >= 1)."""
    found = {}
    for entry in measured:
        for window_id, value in entry["windows"].items():
            if "delta" not in value:
                continue
            slot = found.setdefault(entry["pool"], {}).setdefault(window_id, dict(samples=0, max_delta=0))
            slot["samples"] += 1
            slot["max_delta"] = max(slot["max_delta"], value["delta"])
    for windows in found.values():
        for slot in windows.values():
            slot["demand"] = max(1, math.ceil(slot["max_delta"] * (100 + margin_pct) / 100))
            slot["margin_pct"], slot["unit"] = margin_pct, UNIT
    return found


def report(log):
    """The `measure-demand` answer over every reading in the log (0 calls)."""
    readings = log.readings()
    measured = measure(readings)
    return dict(readings=len(readings), per_launch=measured, suggestion=suggest(measured), stale_s=STALE_S,
                note="upper bounds: other use of the same login between two readings raises a delta; stored windows "
                     "are whole thousandths, so a launch below one thousandth reads 0")
