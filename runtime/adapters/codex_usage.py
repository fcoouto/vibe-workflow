"""Read Codex token counts from local session files without keeping conversation content (D9 O3).

Codex writes one file per thread at ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<thread-id>.jsonl``.
Those files also hold message text and tool data, so the reader parses only lines that carry one of three
marker byte strings, keeps the row time and the numbers below, and drops each parsed row and its bytes at once.
Results and errors use fixed gap codes or fixed messages; they never carry line content, message text, tool
data or file paths.

The row shape comes from public upstream Codex source and is not yet confirmed on real 0.167.0 files. Each
line is ``{"timestamp": "<RFC 3339 UTC>", "type": ..., "payload": {...}}``. ``session_meta`` carries ``id``,
``timestamp`` and ``cli_version``; ``turn_context`` carries ``model`` and ``effort``; token rows are
``event_msg`` rows whose payload type is ``token_count`` and whose ``info`` is null or holds the cumulative
``total_token_usage``, ``last_token_usage`` and ``model_context_window``. ``QUALIFIED_VERSIONS`` stays empty
until real-file qualification, so real use reports ``source_version_unqualified`` and no counts.

Limitations: resumed or forked threads are not detected; only a second ``session_meta`` row is rejected.
Usage after the cutoff is not counted. Identical consecutive token rows are compared on the kept numbers.
Directories on the path must belong to the current user and not be writable by others; the thread file must
be private, regular and not linked.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat
import time

from runtime.startup import StartupError, json_object


SOURCE = "codex-session-file"
QUALIFIED_VERSIONS = frozenset()
COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
            "output_tokens", "reasoning_output_tokens", "total_tokens")
GAP_CODES = frozenset({
    "thread_file_not_found", "thread_file_ambiguous", "file_unreadable", "file_too_large", "line_too_large",
    "session_meta_mismatch", "source_version_unqualified", "token_info_missing", "counter_missing",
    "counter_decreased", "context_window_saturated", "timestamp_unusable", "deadline_exceeded"})
MAX_FILE = 64 * 1024 * 1024
MAX_LINE = 1024 * 1024
LOOKBACK_DAYS = 30
MAX_THREADS = 256
MARKERS = (b'"token_count"', b'"session_meta"', b'"turn_context"')
_MARKER_TAIL = max(len(marker) for marker in MARKERS) - 1
_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")
_STOP = object()


class UsageGap(Exception):
    """A fixed gap code; it carries no file content or path."""

    def __init__(self, code):
        if code not in GAP_CODES:
            raise ValueError("Unknown Codex usage gap code")
        super().__init__(code)
        self.code = code


def find_thread_file(thread_id, *, started_at, cutoff_at, deadline):
    """Return the thread's session file, or None when no date folder in the window names it.

    Lists date folders backward from the UTC cutoff date plus one day, for at most ``LOOKBACK_DAYS`` days,
    and stops at the first folder with a match. Opens no file. Raises ``UsageGap`` for ambiguity, unsafe
    paths and an exceeded deadline.
    """
    thread_id = _thread_id(thread_id)
    _window(started_at, cutoff_at, deadline)
    try:
        return _find(thread_id, cutoff_at, deadline)
    except UsageGap:
        raise
    except (OSError, ValueError, RuntimeError):
        raise UsageGap("file_unreadable") from None


def read_thread_usage(thread_id, *, started_at, cutoff_at, deadline, qualified_versions=QUALIFIED_VERSIONS):
    """Return one thread's usage between ``started_at`` and ``cutoff_at``; any gap leaves every counter None."""
    thread_id = _thread_id(thread_id)
    _window(started_at, cutoff_at, deadline)
    qualified = _qualified(qualified_versions)
    reading = _Reading(thread_id, started_at, cutoff_at, qualified)
    result = dict(source=SOURCE, cli_version=None, thread_id=thread_id, baseline=_unknown(), final=_unknown(),
                  usage=_unknown(), model_settings=[], gaps=[])
    try:
        _deadline(deadline)
        path = find_thread_file(thread_id, started_at=started_at, cutoff_at=cutoff_at, deadline=deadline)
        if path is None:
            raise UsageGap("thread_file_not_found")
        _scan(path, reading, deadline)
        baseline, final, settings = reading.settle()
        _deadline(deadline)
    except UsageGap as gap:
        result.update(cli_version=reading.cli_version, gaps=[gap.code])
        return result
    result.update(cli_version=reading.cli_version, baseline=baseline, final=final,
                  usage={key: final[key] - baseline[key] for key in COUNTERS}, model_settings=settings)
    return result


def acquire_task_observations(threads, *, started_at, cutoff_at, deadline, qualified_versions=QUALIFIED_VERSIONS):
    """Read every expected thread of a task; coverage is complete only when no thread has a gap."""
    _window(started_at, cutoff_at, deadline)
    _qualified(qualified_versions)
    if not isinstance(threads, list) or len(threads) > MAX_THREADS:
        raise ValueError("Invalid Codex task thread list")
    seen = set()
    for item in threads:
        if (not isinstance(item, dict) or set(item) != {"thread_id", "role"}
                or not isinstance(item["role"], str) or not _LABEL.fullmatch(item["role"])):
            raise ValueError("Invalid Codex task thread")
        if _thread_id(item["thread_id"]) in seen:
            raise ValueError("Duplicate Codex task thread")
        seen.add(item["thread_id"])
    results = [dict(role=item["role"], **read_thread_usage(
        item["thread_id"], started_at=started_at, cutoff_at=cutoff_at, deadline=deadline,
        qualified_versions=qualified_versions)) for item in threads]
    gaps = [{"thread_id": item["thread_id"], "code": code} for item in results for code in item["gaps"]]
    return {"threads": results, "expected_threads": len(threads),
            "coverage": "incomplete" if gaps else "complete", "gaps": gaps}


def _thread_id(value):
    if not isinstance(value, str) or not _THREAD_ID.fullmatch(value):
        raise ValueError("Invalid Codex thread ID")
    return value


def _window(started_at, cutoff_at, deadline):
    for value in (started_at, cutoff_at):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Codex usage times must be timezone-aware datetimes")
    if started_at > cutoff_at:
        raise ValueError("Codex usage start must not follow its cutoff")
    if type(deadline) not in (int, float) or deadline != deadline:
        raise ValueError("Codex usage deadline must be a monotonic time")


def _qualified(value):
    if not isinstance(value, (set, frozenset)) or not all(isinstance(item, str) for item in value):
        raise ValueError("Invalid qualified Codex versions")
    return frozenset(value)


def _deadline(deadline):
    if time.monotonic() >= deadline:
        raise UsageGap("deadline_exceeded")


def _unknown():
    return dict.fromkeys(COUNTERS)


def _label(value):
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else None


def _directory(path):
    info = os.lstat(path)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022):
        raise UsageGap("file_unreadable")


def _find(thread_id, cutoff_at, deadline):
    try:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve(strict=True)
    except FileNotFoundError:
        return None
    sessions = home / "sessions"
    if not os.path.lexists(sessions):
        return None
    _directory(sessions)
    prefix, suffix = "rollout-", f"-{thread_id}.jsonl"
    newest = cutoff_at.astimezone(timezone.utc).date() + timedelta(days=1)
    for offset in range(LOOKBACK_DAYS + 1):
        _deadline(deadline)
        day = newest - timedelta(days=offset)
        folder = sessions
        for part in (f"{day.year:04d}", f"{day.month:02d}", f"{day.day:02d}"):
            folder = folder / part
            if not os.path.lexists(folder):
                break
            _directory(folder)
        else:
            names = [name for name in os.listdir(folder) if name.startswith(prefix)
                     and name.endswith(suffix) and len(name) > len(prefix) + len(suffix)]
            if len(names) > 1:
                raise UsageGap("thread_file_ambiguous")
            if names:
                path = folder / names[0]
                if path.resolve(strict=True) != path or not path.is_relative_to(sessions):
                    raise UsageGap("file_unreadable")
                return path
    return None


def _scan(path, reading, deadline):
    """Stream the file once; pass only marker lines to ``reading``. Unterminated last lines are ignored."""
    try:
        before = os.lstat(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise UsageGap("file_unreadable") from None
    try:
        stream = os.fdopen(descriptor, "rb")
    except OSError:
        os.close(descriptor)
        raise UsageGap("file_unreadable") from None
    try:
        with stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or info.st_nlink != 1 or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
                raise UsageGap("file_unreadable")
            if info.st_size > MAX_FILE:
                raise UsageGap("file_too_large")
            remaining, first = info.st_size, True
            while remaining > 0:
                _deadline(deadline)
                line = stream.readline(min(MAX_LINE + 1, remaining))
                if not line:
                    break
                remaining -= len(line)
                if not line.endswith(b"\n"):
                    if len(line) <= MAX_LINE:
                        break
                    marked, terminated, tail = _marked(line), False, line[-_MARKER_TAIL:]
                    while remaining > 0 and not terminated:
                        _deadline(deadline)
                        chunk = stream.readline(min(MAX_LINE, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        marked = marked or _marked(tail + chunk)
                        terminated, tail = chunk.endswith(b"\n"), chunk[-_MARKER_TAIL:]
                    if not terminated:
                        break
                    if marked:
                        raise UsageGap("line_too_large")
                    line = None
                elif not _marked(line):
                    line = None
                if line is None:
                    if first:
                        raise UsageGap("session_meta_mismatch")
                elif reading.row(line, first=first) is _STOP:
                    return
                line, first = None, False
            if first:
                raise UsageGap("session_meta_mismatch")
    except UsageGap:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StartupError, RecursionError):
        raise UsageGap("file_unreadable") from None


def _marked(data):
    return any(marker in data for marker in MARKERS)


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise UsageGap("timestamp_unusable")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise UsageGap("timestamp_unusable") from None
    if moment.utcoffset() is None:
        raise UsageGap("timestamp_unusable")
    return moment


def _token(info):
    """Reduce ``info`` to ``(totals, context window)`` or the gap code its window check would report."""
    if not isinstance(info, dict) or not isinstance(info.get("total_token_usage"), dict):
        return "token_info_missing"
    window = info.get("model_context_window")
    if window is not None and type(window) is not int:
        return "token_info_missing"
    usage = info["total_token_usage"]
    counts = tuple(usage.get(key) for key in COUNTERS)
    if any(type(value) is not int or value < 0 for value in counts):
        return "counter_missing"
    return counts, window


def _dedupe(rows):
    """Keep one of each run of identical consecutive token observations."""
    kept = []
    for moment, observation in rows:
        if not kept or kept[-1][1] != observation:
            kept.append((moment, observation))
    return kept


class _Reading:
    """Numbers kept from one thread file; nothing else survives a row."""

    def __init__(self, thread_id, started_at, cutoff_at, qualified):
        self.thread_id, self.started_at, self.cutoff_at, self.qualified = thread_id, started_at, cutoff_at, qualified
        self.cli_version = self.created = self.last = None
        self.tokens, self.contexts = [], []

    def row(self, raw, *, first):
        row = json_object(raw, limit=MAX_LINE + 1)
        kind, payload = row.get("type"), row.get("payload")
        if first:
            if kind != "session_meta" or not isinstance(payload, dict) or payload.get("id") != self.thread_id:
                raise UsageGap("session_meta_mismatch")
            self.cli_version = _label(payload.get("cli_version"))
            if self.cli_version is None or self.cli_version not in self.qualified:
                raise UsageGap("source_version_unqualified")
            self.created = _time(payload.get("timestamp"))
            self.last = _time(row.get("timestamp"))
            return None
        if kind == "session_meta":
            raise UsageGap("session_meta_mismatch")
        if kind == "turn_context" and isinstance(payload, dict):
            kept = (_label(payload.get("model")), _label(payload.get("effort")))
            target = self.contexts
        elif kind == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
            kept, target = _token(payload.get("info")), self.tokens
        else:
            return None
        moment = _time(row.get("timestamp"))
        if moment < self.last:
            raise UsageGap("timestamp_unusable")
        self.last = moment
        if moment > self.cutoff_at:
            return _STOP
        target.append((moment, kept))
        return None

    def settle(self):
        tokens = _dedupe(self.tokens)
        base = None
        if self.created <= self.started_at:
            before = [index for index, (moment, _) in enumerate(tokens) if moment <= self.started_at]
            base = before[-1] if before else None
        window = tokens if base is None else tokens[base:]
        previous = None if base is not None else (0,) * len(COUNTERS)
        for _, observation in window:
            if isinstance(observation, str):
                raise UsageGap(observation)
            counts, context_window = observation
            if context_window is not None and counts[-1] == context_window:
                raise UsageGap("context_window_saturated")
            if previous is not None and any(now < then for now, then in zip(counts, previous)):
                raise UsageGap("counter_decreased")
            previous = counts
        zero = (0,) * len(COUNTERS)
        baseline = zero if base is None else window[0][1][0]
        final = window[-1][1][0] if window else baseline
        return dict(zip(COUNTERS, baseline)), dict(zip(COUNTERS, final)), self._settings(
            None if base is None else window[0][0])

    def _settings(self, baseline_at):
        """Configured ``(model, effort)`` pairs in effect at the baseline row or set after it, first seen first."""
        if baseline_at is None:
            contexts = self.contexts
        else:
            earlier = [item for item in self.contexts if item[0] <= baseline_at]
            contexts = earlier[-1:] + [item for item in self.contexts if item[0] > baseline_at]
        pairs = list(dict.fromkeys(pair for _, pair in contexts))
        return [{"model": model, "effort": effort, "basis": "configured"} for model, effort in pairs]
