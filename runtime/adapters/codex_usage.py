"""Read Codex token counts from local session files without keeping conversation content (D9 O3).

Codex writes each thread to ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<thread-id>.jsonl`` and, for
paginated history, continues it in page files ``rollout-<timestamp>-<thread-id>_<page-uuid>.jsonl``. Those files also hold message text and tool data, so the reader parses only lines that carry one of three
marker byte strings, keeps the row time and the numbers below, and drops each parsed row and its bytes at once.
Results and errors use fixed gap codes or fixed messages; they never carry line content, message text, tool
data or file paths.

Each line is ``{"timestamp": "<RFC 3339 UTC>", "type": ..., "payload": {...}}``. ``session_meta`` carries
``id``, ``timestamp`` and ``cli_version``; ``turn_context`` carries ``model`` and ``effort``; token rows are
``event_msg`` rows whose payload type is ``token_count`` and whose ``info`` is null or holds the cumulative
``total_token_usage``, ``last_token_usage`` and ``model_context_window`` (other ``info`` keys are ignored).

When a thread's baseline is zero, ``token_count`` rows with ``info: null`` that come before its first
non-null row are read as zero usage; a null after any non-null row is ``token_info_missing``.

A forked file repeats its parent's ``session_meta`` header after its own, before its first token row, and its
totals start fresh. The first row of every file must be the thread's own header with a qualified version; later
headers in a file are ignored until its first token row and are a mismatch after it.

Paginated history: every plain and page file of the thread found in the window is read in name-timestamp order
as one row stream, with rows in time order across files, within ``MAX_THREAD_BYTES`` in total. Totals are
cumulative across files, so each later file's first non-null total must equal the previous last non-null total
plus that row's ``last_token_usage``; null rows before it add nothing. ``cli_version`` comes from the newest file
read and the creation time from the earliest. When the earliest file found is a page, the history before it is
missing: the reader then needs a token row at or before task start in the files found, and reports
``history_incomplete`` otherwise, or when a page comes before the plain file or does not continue it.

A line over ``MAX_LINE`` is judged by its first ``PREFIX_BYTES`` bytes only: it is ``line_too_large`` when
that prefix shows a ``session_meta`` or ``turn_context`` row, or an ``event_msg`` row whose payload type is
``token_count`` or lies beyond the prefix. Any other oversized line, such as a compacted row or a tool output
that mentions ``"token_count"`` in its text, is skipped unparsed.

The row shape, row timestamps, inclusive counters, leading null rows, separation of parent and child thread
totals and forked headers were confirmed on real 0.167.0-era files on 12 November 2026, so
``QUALIFIED_VERSIONS`` holds ``0.167.0``; any other version reports ``source_version_unqualified``.

Resumed threads need no separate detection: on the same real files, a resumed session continued in the same
file, and after each of 94 idle gaps over an hour the next total equalled the previous total plus that row's
``last_token_usage``. The reader subtracts the baseline from the cumulative total, so resumed work is counted;
a total that resets would surface as ``counter_decreased``.

Limitations: usage after the cutoff is not counted. Identical consecutive
token rows are compared on the kept numbers. Directories on the path must belong to the current user and not
be writable by others; the thread file must be a regular, unlinked file owned by the current user and not
writable by group or others. Gaps are raised without exception context, so no path or line text is reachable
from them.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat
import time

from runtime.startup import StartupError, json_object


SOURCE = "codex-session-file"
QUALIFIED_VERSIONS = frozenset({"0.167.0"})
COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
            "output_tokens", "reasoning_output_tokens", "total_tokens")
GAP_CODES = frozenset({
    "thread_file_not_found", "thread_file_ambiguous", "file_unreadable", "file_too_large", "line_too_large",
    "session_meta_mismatch", "source_version_unqualified", "token_info_missing", "counter_missing",
    "counter_decreased", "context_window_saturated", "timestamp_unusable", "deadline_exceeded", "no_threads",
    "history_incomplete"})
MAX_FILE = 64 * 1024 * 1024
MAX_THREAD_BYTES = 256 * 1024 * 1024
MAX_LINE = 1024 * 1024
LOOKBACK_DAYS = 30
MAX_THREADS = 256
MARKERS = (b'"token_count"', b'"session_meta"', b'"turn_context"')
PREFIX_BYTES = 512
_PREFIX_TYPE = re.compile(rb'"type"\s*:\s*"([A-Za-z0-9_]{1,64})"')
_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")
_VERSION = re.compile(r"[0-9A-Za-z.+-]{1,64}")
_NAME = (r"rollout-([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2})-%s"
         r"(_[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})?\.jsonl")
_STOP = object()


class UsageGap(Exception):
    """A fixed gap code; it carries no file content or path."""

    def __init__(self, code):
        if code not in GAP_CODES:
            raise ValueError("Unknown Codex usage gap code")
        super().__init__(code)
        self.code = code


def find_thread_files(thread_id, *, started_at, cutoff_at, deadline):
    """Return the thread's session files in name-timestamp order; empty when no date folder in the window names one.

    Lists every date folder from the UTC cutoff date plus one day back for ``LOOKBACK_DAYS`` days and collects
    the plain file ``rollout-YYYY-MM-DDTHH-MM-SS-<thread-id>.jsonl`` and the page files
    ``rollout-YYYY-MM-DDTHH-MM-SS-<thread-id>_<page-uuid>.jsonl``. Opens no file. Raises ``UsageGap`` for more
    than one plain file or two equal name timestamps (``thread_file_ambiguous``), unsafe paths and an exceeded
    deadline.
    """
    return [path for path, _ in _located(thread_id, started_at, cutoff_at, deadline)]


def find_thread_file(thread_id, *, started_at, cutoff_at, deadline):
    """Return a one-file thread's session file, or None; a thread with page files is ``thread_file_ambiguous``.

    Use ``find_thread_files`` for threads with paginated history.
    """
    files = find_thread_files(thread_id, started_at=started_at, cutoff_at=cutoff_at, deadline=deadline)
    if len(files) > 1:
        raise UsageGap("thread_file_ambiguous")
    return files[0] if files else None


def _located(thread_id, started_at, cutoff_at, deadline):
    thread_id = _thread_id(thread_id)
    _window(started_at, cutoff_at, deadline)
    try:
        return _find(thread_id, cutoff_at, deadline)
    except UsageGap as gap:
        code = gap.code
    except (OSError, ValueError, RuntimeError):
        code = "file_unreadable"
    raise UsageGap(code)


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
        files = _located(thread_id, started_at, cutoff_at, deadline)
        if not files:
            raise UsageGap("thread_file_not_found")
        if files[0][1] and not all(page for _, page in files):
            raise UsageGap("history_incomplete")
        for path, page in files:
            reading.begin(page=page)
            if _scan(path, reading, deadline):
                break
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
    if not threads:
        gaps.append({"thread_id": None, "code": "no_threads"})
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
        return []
    sessions = home / "sessions"
    if not os.path.lexists(sessions):
        return []
    _directory(sessions)
    pattern = re.compile(_NAME % re.escape(thread_id))
    found = []
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
            for name in os.listdir(folder):
                match = pattern.fullmatch(name)
                if match:
                    path = folder / name
                    if path.resolve(strict=True) != path or not path.is_relative_to(sessions):
                        raise UsageGap("file_unreadable")
                    found.append((match[1], match[2] is not None, path))
    stamps = {stamp for stamp, _, _ in found}
    if sum(not page for _, page, _ in found) > 1 or len(stamps) != len(found):
        raise UsageGap("thread_file_ambiguous")
    return [(path, page) for _, page, path in sorted(found)]


def _scan(path, reading, deadline):
    """Stream one file once; pass only marker lines to ``reading``. True when a row after the cutoff stopped it."""
    try:
        return _stream(path, reading, deadline)
    except UsageGap as gap:
        code = gap.code
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StartupError, RecursionError):
        code = "file_unreadable"
    raise UsageGap(code)


def _stream(path, reading, deadline):
    before = os.lstat(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022
                or info.st_nlink != 1 or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
            raise UsageGap("file_unreadable")
        if info.st_size > MAX_FILE:
            raise UsageGap("file_too_large")
        reading.reserve(info.st_size)
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
                kept, terminated = _kept_prefix(line[:PREFIX_BYTES]), False
                while remaining > 0 and not terminated:
                    _deadline(deadline)
                    chunk = stream.readline(min(MAX_LINE, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    terminated = chunk.endswith(b"\n")
                if not terminated:
                    break
                if kept:
                    raise UsageGap("line_too_large")
                line = None
            elif not _marked(line):
                line = None
            if line is None:
                if first:
                    raise UsageGap("session_meta_mismatch")
            elif reading.row(line, first=first) is _STOP:
                return True
            line, first = None, False
        if first:
            raise UsageGap("session_meta_mismatch")
        return False


def _marked(data):
    return any(marker in data for marker in MARKERS)


def _kept_prefix(prefix):
    """Whether an oversized line's prefix shows a row kind the reader would keep."""
    kinds = _PREFIX_TYPE.findall(prefix)
    if b"session_meta" in kinds or b"turn_context" in kinds:
        return True
    if b"event_msg" not in kinds:
        return False
    payload = kinds[kinds.index(b"event_msg") + 1:]
    return not payload or b"token_count" in kinds


def _time(value):
    moment = None
    if isinstance(value, str) and len(value) <= 64:
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            pass
    if moment is None or moment.utcoffset() is None:
        raise UsageGap("timestamp_unusable")
    return moment


def _token(info):
    """Reduce ``info`` to ``(totals, context window)``, None for a null ``info``, or the gap code it reports."""
    if info is None:
        return None
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


def _last(info):
    usage = info.get("last_token_usage") if isinstance(info, dict) else None
    counts = tuple(usage.get(key) for key in COUNTERS) if isinstance(usage, dict) else ()
    return counts if counts and all(type(value) is int and value >= 0 for value in counts) else None


def _dedupe(rows):
    """Keep one of each run of identical consecutive token observations."""
    kept = []
    for moment, observation in rows:
        if not kept or kept[-1][1] != observation:
            kept.append((moment, observation))
    return kept


class _Reading:
    """Numbers kept from one thread's files, read in order; nothing else survives a row."""

    def __init__(self, thread_id, started_at, cutoff_at, qualified):
        self.thread_id, self.started_at, self.cutoff_at, self.qualified = thread_id, started_at, cutoff_at, qualified
        self.cli_version = self.created = self.last = self.carried = None
        self.tokens, self.contexts, self.token_seen = [], [], False
        self.files = self.bytes = 0
        self.page_first, self.continued = False, True

    def begin(self, *, page):
        """Start the next file. Totals carry across files; a later file must continue the last known total."""
        if not self.files:
            self.page_first, self.carried = page, None if page else (0,) * len(COUNTERS)
        self.files += 1
        self.token_seen, self.continued = False, self.files == 1

    def reserve(self, size):
        self.bytes += size
        if self.bytes > MAX_THREAD_BYTES:
            raise UsageGap("file_too_large")

    def row(self, raw, *, first):
        row = json_object(raw, limit=MAX_LINE + 1)
        kind, payload = row.get("type"), row.get("payload")
        if first:
            if kind != "session_meta" or not isinstance(payload, dict) or payload.get("id") != self.thread_id:
                raise UsageGap("session_meta_mismatch")
            version = payload.get("cli_version")
            self.cli_version = version if isinstance(version, str) and _VERSION.fullmatch(version) else None
            if self.cli_version is None or self.cli_version not in self.qualified:
                raise UsageGap("source_version_unqualified")
            moment = _time(row.get("timestamp"))
            if self.files == 1:
                self.created = _time(payload.get("timestamp"))
                if self.page_first and self.started_at < self.created:
                    raise UsageGap("history_incomplete")
            elif moment < self.last:
                raise UsageGap("timestamp_unusable")
            self.last = moment
            return None
        if kind == "session_meta":
            if self.token_seen:
                raise UsageGap("session_meta_mismatch")
            return None
        if kind == "turn_context" and isinstance(payload, dict):
            kept = (_label(payload.get("model")), _label(payload.get("effort")))
            target = self.contexts
        elif kind == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
            kept, target, self.token_seen = _token(payload.get("info")), self.tokens, True
        else:
            return None
        moment = _time(row.get("timestamp"))
        if moment < self.last:
            raise UsageGap("timestamp_unusable")
        self.last = moment
        if moment > self.cutoff_at:
            return _STOP
        if target is self.tokens:
            if not self.continued:
                if kept is None:
                    return None  # Null rows before a later file's first total add nothing.
                last = _last(payload.get("info"))
                if (not isinstance(kept, tuple) or self.carried is None or last is None
                        or kept[0] != tuple(then + now for then, now in zip(self.carried, last))):
                    raise UsageGap("history_incomplete")
                self.continued = True
            if isinstance(kept, tuple):
                self.carried = kept[0]
        target.append((moment, kept))
        return None

    def settle(self):
        tokens = _dedupe(self.tokens)
        base = None
        if self.created <= self.started_at:
            before = [index for index, (moment, _) in enumerate(tokens) if moment <= self.started_at]
            if any(tokens[index][1] is not None for index in before):
                base = before[-1]
        if base is None and self.page_first:
            raise UsageGap("history_incomplete")  # Earlier totals live in files outside the window.
        if base is None:
            # With a zero baseline, null rows before the first non-null row mean no usage yet.
            first = next((index for index, (_, observation) in enumerate(tokens) if observation is not None),
                         len(tokens))
            window = tokens[first:]
        else:
            window = tokens[base:]
        previous = None if base is not None else (0,) * len(COUNTERS)
        for _, observation in window:
            if observation is None:
                raise UsageGap("token_info_missing")
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
        return dict(zip(COUNTERS, baseline)), dict(zip(COUNTERS, final)), self._settings()

    def _settings(self):
        """Configured ``(model, effort)`` pairs in effect at task start or set after it, first seen first."""
        earlier = [item for item in self.contexts if item[0] <= self.started_at]
        contexts = earlier[-1:] + [item for item in self.contexts if item[0] > self.started_at]
        pairs = list(dict.fromkeys(pair for _, pair in contexts))
        return [{"model": model, "effort": effort, "basis": "configured"} for model, effort in pairs]
