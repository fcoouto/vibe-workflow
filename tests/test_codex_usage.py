"""Codex session-file usage reader: synthetic files only, never the real ~/.codex."""

import contextlib
from datetime import datetime, timedelta, timezone
import itertools
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import tests  # noqa: F401  (redirects HOME before the runtime is imported)
from runtime.adapters import codex_usage


THREAD = "019a6f00-0000-7000-8000-000000000001"
OTHER = "019a6f00-0000-7000-8000-000000000002"
PAGE = "019a6f00-0000-7000-8000-0000000000a1"
PAGE2 = "019a6f00-0000-7000-8000-0000000000a2"
SECRET = "sk-live-7f3a9cSECRET user said: my password is hunter2-zebra"
START = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
VERSION = "0.167.0"
QUALIFIED = frozenset({VERSION})
KEYS = codex_usage.COUNTERS
UNKNOWN = dict.fromkeys(KEYS)


def identity(path):
    info = os.stat(path)
    return info.st_dev, info.st_ino


def at(hour, minute=0, day=12):
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def totals(n):
    return {key: n * (index + 1) for index, key in enumerate(KEYS)}


def meta(moment=at(9), thread=THREAD, version=VERSION, created=None):
    payload = {"id": thread, "timestamp": stamp(created or moment), "cli_version": version,
               "cwd": "/secret/" + SECRET, "instructions": SECRET}
    return {"timestamp": stamp(moment), "type": "session_meta", "payload": payload}


def context(moment, model="gpt-5.5", effort="high"):
    payload = {"cwd": "/secret", "model": model, "effort": effort, "user_instructions": SECRET}
    if model is None:
        del payload["model"]
    if effort is None:
        del payload["effort"]
    return {"timestamp": stamp(moment), "type": "turn_context", "payload": payload}


def token(moment, n=None, *, info=..., window=1_000_000):
    if info is ...:
        info = {"total_token_usage": totals(n), "last_token_usage": totals(1), "model_context_window": window}
    return {"timestamp": stamp(moment), "type": "event_msg",
            "payload": {"type": "token_count", "info": info, "rate_limits": {"primary": {"used_percent": 1.5}}}}


def message(moment, text=SECRET):
    return {"timestamp": stamp(moment), "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}


class CodexUsageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.home = self.root / "codex"
        self.home.mkdir(mode=0o700)
        self.sessions = self.home / "sessions"
        environment = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def folder(self, day=12, month=11, year=2026):
        path = self.sessions / f"{year:04d}" / f"{month:02d}" / f"{day:02d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, rows, *, thread=THREAD, folder=None, name=None, terminated=True, mode=0o600, extra=b""):
        folder = folder or self.folder()
        path = folder / (name or f"rollout-2026-11-12T09-00-00-{thread}.jsonl")
        lines = [row if isinstance(row, bytes) else json.dumps(row).encode() for row in rows]
        data = b"\n".join(lines) + (b"\n" if terminated else b"") + extra
        path.write_bytes(data)
        os.chmod(path, mode)
        return path

    def standard(self, **kwargs):
        return self.write([meta(), message(at(9, 1)), context(at(9, 2)), token(at(9, 30), 100),
                           message(at(10, 5)), token(at(10, 30), 200), context(at(11), model="gpt-5.5-mini", effort="low"),
                           token(at(11, 30), 350), token(at(12, 30), 900)], **kwargs)

    def read(self, thread=THREAD, *, deadline=None, qualified=QUALIFIED, started=START, cutoff=CUTOFF):
        return codex_usage.read_thread_usage(
            thread, started_at=started, cutoff_at=cutoff,
            deadline=time.monotonic() + 30 if deadline is None else deadline, qualified_versions=qualified)

    def assertGap(self, result, code):
        self.assertEqual(result["gaps"], [code])
        for field in ("baseline", "final", "usage"):
            self.assertEqual(result[field], UNKNOWN)
        self.assertEqual(result["model_settings"], [])
        self.assertNotIn(SECRET, repr(result))
        self.assertNotIn(str(self.root), repr(result))

    # Counting

    def test_usage_is_final_total_minus_baseline_total(self):
        self.standard()
        result = self.read()
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["source"], "codex-session-file")
        self.assertEqual(result["cli_version"], VERSION)
        self.assertEqual(result["thread_id"], THREAD)
        self.assertEqual(result["baseline"], totals(100))
        self.assertEqual(result["final"], totals(350))
        self.assertEqual(result["usage"], totals(250))
        self.assertEqual(result["model_settings"], [
            {"model": "gpt-5.5", "effort": "high", "basis": "configured"},
            {"model": "gpt-5.5-mini", "effort": "low", "basis": "configured"}])

    def test_baseline_row_at_start_counts_as_baseline(self):
        self.write([meta(), token(START, 100), token(at(11), 130)])
        self.assertEqual(self.read()["usage"], totals(30))

    def test_thread_created_after_start_has_zero_baseline(self):
        self.write([meta(at(10, 30)), context(at(10, 31)), token(at(10, 40), 50)])
        result = self.read()
        self.assertEqual(result["baseline"], totals(0))
        self.assertEqual(result["usage"], totals(50))

    def test_session_meta_time_after_start_decides_zero_baseline(self):
        self.write([meta(at(9), created=at(10, 30)), token(at(9, 30), 100), token(at(10, 45), 160)])
        result = self.read()
        self.assertEqual(result["baseline"], totals(0))
        self.assertEqual(result["usage"], totals(160))

    def test_earlier_thread_without_token_row_before_start_has_zero_baseline(self):
        self.write([meta(), context(at(9, 5)), token(at(10, 30), 40)])
        result = self.read()
        self.assertEqual(result["baseline"], totals(0))
        self.assertEqual(result["usage"], totals(40))
        self.assertEqual(result["model_settings"], [{"model": "gpt-5.5", "effort": "high", "basis": "configured"}])

    def test_no_token_row_in_window_is_zero_usage(self):
        self.write([meta(), token(at(9, 30), 100), token(at(12, 1), 400)])
        result = self.read()
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["final"], totals(100))
        self.assertEqual(result["usage"], totals(0))

    def test_new_thread_without_token_rows_is_zero_usage(self):
        self.write([meta(at(10, 30))])
        result = self.read()
        self.assertEqual((result["gaps"], result["baseline"], result["usage"]), ([], totals(0), totals(0)))

    def test_identical_consecutive_info_is_one_observation(self):
        rows = [(at(9), (1,)), (at(9, 1), (1,)), (at(9, 2), (2,)), (at(9, 3), (1,))]
        self.assertEqual(codex_usage._dedupe(rows), [(at(9), (1,)), (at(9, 2), (2,)), (at(9, 3), (1,))])
        self.write([meta(), token(at(9, 30), 100), token(at(10, 30), 100), token(at(11), 100)])
        self.assertEqual(self.read()["usage"], totals(0))

    def test_model_settings_pivot_on_task_start(self):
        cases = ([meta(), token(at(9, 10), 5), context(at(9, 20), model="model-a"), context(at(9, 40), model="model-b"),
                  token(at(9, 50), 5), token(at(10, 30), 9)],
                 [meta(), context(at(9, 20), model="model-a"), context(at(9, 40), model="model-b"),
                  token(at(10, 30), 9)],
                 [meta(), context(at(9, 20), model="model-b"), context(at(10, 0), model="model-b"),
                  context(at(10, 20), model="model-a"), token(at(10, 30), 9), context(at(10, 40), model="model-b"),
                  context(at(12, 30), model="model-c")])
        expected = (["model-b"], ["model-b"], ["model-b", "model-a"])
        for rows, models in zip(cases, expected):
            with self.subTest(models=models):
                path = self.write(rows)
                self.assertEqual([item["model"] for item in self.read()["model_settings"]], models)
                path.unlink()

    def test_missing_model_or_effort_is_none(self):
        self.write([meta(), context(at(10, 10), model=None), context(at(10, 20), effort=None), token(at(10, 30), 5)])
        self.assertEqual(self.read()["model_settings"], [
            {"model": None, "effort": "high", "basis": "configured"},
            {"model": "gpt-5.5", "effort": None, "basis": "configured"}])

    # Gaps

    def test_thread_file_not_found(self):
        self.assertGap(self.read(), "thread_file_not_found")
        self.standard(thread=OTHER)
        self.assertGap(self.read(), "thread_file_not_found")

    def test_thread_file_ambiguous(self):
        self.standard()
        self.standard(name=f"rollout-2026-11-12T11-00-00-{THREAD}.jsonl")
        self.assertGap(self.read(), "thread_file_ambiguous")

    def test_file_unreadable(self):
        self.standard(mode=0o000)
        self.assertGap(self.read(), "file_unreadable")

    def test_malformed_marker_line_is_unreadable(self):
        self.write([meta(), b'{"type": "event_msg", "payload": {"type": "token_count", "x": "' + SECRET.encode()])
        self.assertGap(self.read(), "file_unreadable")

    def test_file_too_large(self):
        self.standard()
        with patch.object(codex_usage, "MAX_FILE", 256):
            self.assertGap(self.read(), "file_too_large")

    def test_marker_line_too_large(self):
        padding = "x" * 5000
        self.write([meta(), token(at(10, 30), 100, info={"total_token_usage": totals(1), "pad": padding})])
        with patch.object(codex_usage, "MAX_LINE", 4096):
            self.assertGap(self.read(), "line_too_large")

    def test_oversized_line_is_too_large_when_its_prefix_shows_a_kept_row(self):
        pad = "x" * 5000
        late_type = {"timestamp": stamp(at(10, 1)), "type": "event_msg",
                     "payload": {"pad": "p" * 600, "type": "agent_message", "message": pad}}
        big_meta = meta(at(9, 1), thread=OTHER)
        big_meta["payload"]["pad"] = pad
        big_context = context(at(10, 1))
        big_context["payload"]["pad"] = pad
        big_token = token(at(10, 1), 1, info={"total_token_usage": totals(1), "pad": pad})
        for row in (big_meta, big_context, big_token, late_type):
            for separators in ((", ", ": "), (",", ":")):
                with self.subTest(kind=row["type"], payload=row["payload"].get("type"), compact=separators[1] == ":"):
                    path = self.write([meta(), json.dumps(row, separators=separators).encode(), token(at(10, 30), 3)])
                    with patch.object(codex_usage, "MAX_LINE", 4096):
                        self.assertGap(self.read(), "line_too_large")
                    path.unlink()

    def test_oversized_line_with_other_prefix_is_skipped_unparsed(self):
        text = "y" * 9000
        structural = b', "x": {"type": "token_count", "info": null}}'
        agent = {"timestamp": stamp(at(10, 1)), "type": "event_msg",
                 "payload": {"type": "agent_message", "message": text}}
        compacted = {"timestamp": stamp(at(10, 2)), "type": "compacted",
                     "payload": {"replacement_history": [{"type": "token_count"}], "message": text}}
        rows = (json.dumps(agent).encode()[:-2] + structural + b"}",
                json.dumps(message(at(10, 3), text)).encode()[:-1] + structural,
                json.dumps(compacted, separators=(",", ":")).encode(),
                json.dumps(message(at(10, 4), "y" * 5000)).encode()[:4090] + b'"token_count"' + b"w" * 6000)
        for row in rows:
            with self.subTest(row=row[:60]):
                path = self.write([meta(), row, token(at(10, 30), 300)])
                with (patch.object(codex_usage, "MAX_LINE", 4096),
                      patch.object(codex_usage, "json_object", wraps=codex_usage.json_object) as parse):
                    result = self.read()
                self.assertEqual((result["gaps"], result["usage"]), ([], totals(300)))
                self.assertEqual(parse.call_count, 2)
                path.unlink()

    def test_session_meta_mismatch(self):
        cases = ([meta(thread=OTHER), token(at(10, 30), 1)],
                 [message(at(9)), meta(), token(at(10, 30), 1)],
                 [context(at(9)), token(at(10, 30), 1)],
                 [meta(), token(at(9, 30), 1), meta(at(10)), token(at(10, 30), 1)],
                 [meta(), token(at(9, 10), info=None), meta(at(9, 20), thread=OTHER), token(at(10, 30), 1)],
                 [meta(), token(at(10, 30), 1), meta(at(11), thread=OTHER)],
                 [{"timestamp": stamp(at(9)), "type": "session_meta", "payload": [THREAD]}],
                 [])
        for rows in cases:
            with self.subTest(rows=len(rows)):
                path = self.write(rows)
                self.assertGap(self.read(), "session_meta_mismatch")
                path.unlink()
        for rows in ([], [meta()]):
            with self.subTest(unterminated=len(rows)):
                path = self.write(rows, terminated=False)
                self.assertGap(self.read(), "session_meta_mismatch")
                path.unlink()

    def test_forked_header_before_first_token_row_is_ignored(self):
        parent = meta(at(9, 1), thread=OTHER, version="9.9.9", created=at(10, 30))
        same = meta(at(9, 1))
        garbage = {"timestamp": "unusable", "type": "session_meta", "payload": [SECRET]}
        older = meta(at(8), thread=OTHER)
        for header in (parent, same, garbage, older):
            with self.subTest(header=header["payload"]["id"] if isinstance(header["payload"], dict) else "list"):
                path = self.write([meta(), header, context(at(9, 2)), token(at(9, 30), 100), token(at(10, 30), 200)])
                result = self.read()
                self.assertEqual((result["gaps"], result["cli_version"]), ([], VERSION))
                self.assertEqual((result["baseline"], result["usage"]), (totals(100), totals(100)))
                self.assertNotIn(SECRET, repr(result))
                path.unlink()

    def test_source_version_unqualified(self):
        self.standard()
        result = self.read(qualified=frozenset())
        self.assertGap(result, "source_version_unqualified")
        self.assertEqual(result["cli_version"], VERSION)
        self.assertGap(self.read(qualified=frozenset({"0.168.0"})), "source_version_unqualified")

    def test_shipped_qualified_versions_are_exactly_0_167_0(self):
        self.assertEqual(codex_usage.QUALIFIED_VERSIONS, frozenset({"0.167.0"}))
        self.standard()
        result = codex_usage.read_thread_usage(THREAD, started_at=START, cutoff_at=CUTOFF,
                                               deadline=time.monotonic() + 30)
        self.assertEqual((result["gaps"], result["usage"]), ([], totals(250)))
        path = self.write([meta(version="0.168.0"), token(at(10, 30), 1)])
        self.assertGap(codex_usage.read_thread_usage(THREAD, started_at=START, cutoff_at=CUTOFF,
                                                     deadline=time.monotonic() + 30), "source_version_unqualified")
        path.unlink()

    def test_missing_or_invalid_version_is_unqualified(self):
        for version in (None, 167, "", "x" * 65, "path/x", "1.0:evil", "v@1", "0.167.0 beta"):
            with self.subTest(version=version):
                path = self.write([meta(version=version), token(at(10, 30), 1)])
                result = self.read()
                self.assertGap(result, "source_version_unqualified")
                self.assertIsNone(result["cli_version"])
                path.unlink()

    def test_token_info_missing(self):
        cases = ([meta(), token(at(9, 30), 1), token(at(10, 30), info=None)],
                 [meta(), token(at(9, 30), 1), token(at(10, 30), info={"last_token_usage": totals(1)})],
                 [meta(), token(at(9, 10), 1), token(at(9, 30), info=None)],
                 [meta(), token(at(10, 30), 1, window="large")],
                 [meta(at(10, 30)), token(at(10, 35), info=None), token(at(10, 40), 4), token(at(10, 50), info=None)],
                 [meta(at(10, 30)), token(at(10, 35), info={"last_token_usage": totals(1)}), token(at(10, 40), 4)])
        for rows in cases:
            with self.subTest(rows=rows[-1]["payload"]["info"]):
                path = self.write(rows)
                self.assertGap(self.read(), "token_info_missing")
                path.unlink()

    def test_leading_null_info_with_zero_baseline_is_zero_usage(self):
        cases = (([meta(at(10, 30)), token(at(10, 31), info=None), token(at(10, 40), 40)], 40),
                 ([meta(at(10, 30)), token(at(10, 31), info=None), token(at(10, 32), info=None)], 0),
                 ([meta(), token(at(10, 20), info=None), token(at(10, 30), 40)], 40),
                 ([meta(), token(at(9, 10), info=None), token(at(10, 30), 40)], 40),
                 ([meta(), token(at(9, 10), info=None)], 0))
        for rows, expected in cases:
            with self.subTest(case=expected, rows=len(rows)):
                path = self.write(rows)
                result = self.read()
                self.assertEqual((result["gaps"], result["baseline"], result["usage"]),
                                 ([], totals(0), totals(expected)))
                path.unlink()

    def test_null_info_before_baseline_is_ignored(self):
        self.write([meta(), token(at(9, 10), info=None), token(at(9, 30), 10), token(at(10, 30), 20)])
        self.assertEqual(self.read()["usage"], totals(10))

    def test_counter_missing(self):
        for broken in ("absent", -1, 1.5, True, "3"):
            with self.subTest(broken=broken):
                counts = totals(10)
                if broken == "absent":
                    del counts["cache_write_input_tokens"]
                else:
                    counts["reasoning_output_tokens"] = broken
                path = self.write([meta(), token(at(10, 30), info={"total_token_usage": counts,
                                                                    "model_context_window": None})])
                self.assertGap(self.read(), "counter_missing")
                path.unlink()

    def test_counter_decreased(self):
        counts = totals(20)
        counts["cached_input_tokens"] = 1
        self.write([meta(), token(at(9, 30), 10), token(at(10, 30), info={"total_token_usage": counts,
                                                                         "model_context_window": None})])
        self.assertGap(self.read(), "counter_decreased")

    def test_context_window_saturated(self):
        self.write([meta(), token(at(9, 30), 10), token(at(10, 30), 20, window=totals(20)["total_tokens"])])
        self.assertGap(self.read(), "context_window_saturated")

    def test_saturation_after_cutoff_is_not_counted(self):
        self.write([meta(), token(at(9, 30), 10), token(at(12, 30), 20, window=totals(20)["total_tokens"])])
        self.assertEqual(self.read()["gaps"], [])

    def test_timestamp_unusable(self):
        undated = token(at(10, 30), 5)
        del undated["timestamp"]
        naive = token(at(10, 30), 5)
        naive["timestamp"] = "2026-11-12T10:30:00"
        no_created = meta()
        del no_created["payload"]["timestamp"]
        cases = ([meta(), undated], [meta(), naive], [meta(), token(at(10, 30), 5) | {"timestamp": "soon"}],
                 [no_created, token(at(10, 30), 5)],
                 [meta(), token(at(10, 30), 5), token(at(10, 10), 6)],
                 [meta(), context(at(10, 30)) | {"timestamp": 7}])
        for rows in cases:
            with self.subTest(case=cases.index(rows)):
                path = self.write(rows)
                self.assertGap(self.read(), "timestamp_unusable")
                path.unlink()

    def test_deadline_exceeded(self):
        self.standard()
        self.assertGap(self.read(deadline=time.monotonic() - 1), "deadline_exceeded")
        clock = itertools.chain([0.0] * 6, itertools.repeat(100.0))
        with patch.object(codex_usage.time, "monotonic", side_effect=lambda: next(clock)):
            self.assertGap(self.read(deadline=50.0), "deadline_exceeded")

    def test_deadline_stops_the_scan_at_the_next_line(self):
        self.standard()
        parsed, original = [], codex_usage.json_object
        def parse(raw, **kwargs):
            parsed.append(None)
            return original(raw, **kwargs)
        with (patch.object(codex_usage, "json_object", side_effect=parse),
              patch.object(codex_usage.time, "monotonic", side_effect=lambda: 100.0 if len(parsed) >= 2 else 0.0)):
            self.assertGap(self.read(deadline=50.0), "deadline_exceeded")
        self.assertEqual(len(parsed), 2)

    # Lookup

    @contextlib.contextmanager
    def spy(self):
        """Record the names of non-directory open attempts and the identity of every listed directory."""
        opened, listed = [], []
        real_open, real_listdir = os.open, os.listdir

        def fake_open(path, flags, *args, **kwargs):
            if not flags & os.O_DIRECTORY:
                opened.append(os.path.basename(path))
            return real_open(path, flags, *args, **kwargs)

        def fake_listdir(path):
            info = os.fstat(path) if isinstance(path, int) else os.stat(path)
            listed.append((info.st_dev, info.st_ino))
            return real_listdir(path)

        with (patch.object(codex_usage.os, "open", side_effect=fake_open),
              patch.object(codex_usage.os, "listdir", side_effect=fake_listdir)):
            yield opened, listed

    def test_lookup_lists_only_window_folders_and_opens_only_the_thread_file(self):
        target = self.standard(folder=self.folder(day=10))
        decoy = self.standard(folder=self.folder(day=2, month=10), mode=0o000)
        other = self.standard(thread=OTHER, folder=self.folder(day=10), mode=0o000)
        newer = self.standard(thread=OTHER, folder=self.folder(day=13), mode=0o000)
        with self.spy() as (opened, listed):
            result = self.read()
        self.assertEqual(result["usage"], totals(250))
        self.assertEqual(opened, [target.name])
        self.assertEqual(listed, [identity(newer.parent), identity(target.parent)])
        self.assertNotIn(identity(decoy.parent), listed)
        self.assertNotIn(other.name, opened)

    def test_directory_swapped_before_open_cannot_redirect_the_read(self):
        self.standard()
        day = self.folder()
        outside = self.root / "outside"
        outside.mkdir()
        self.write([meta(), token(at(9, 30), 1), token(at(10, 30), 999)], folder=outside)
        real_open, swapped = os.open, []

        def swap_then_open(path, flags, *args, **kwargs):
            if not flags & os.O_DIRECTORY and not swapped:
                swapped.append(day.rename(day.with_name("12-moved")))
                day.symlink_to(outside)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(codex_usage.os, "open", side_effect=swap_then_open):
            result = self.read()
        self.assertTrue(swapped)
        self.assertEqual((result["gaps"], result["usage"]), ([], totals(250)))

    def test_lookup_window_is_thirty_days_plus_one(self):
        for day, month, found in ((13, 11, True), (14, 11, False), (14, 10, True), (13, 10, False)):
            with self.subTest(day=day, month=month):
                path = self.standard(folder=self.folder(day=day, month=month))
                self.assertEqual(codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=CUTOFF,
                                                              deadline=time.monotonic() + 30),
                                 path if found else None)
                path.unlink()

    def test_lookup_uses_utc_cutoff_date(self):
        path = self.standard(folder=self.folder(day=13))
        local = CUTOFF.astimezone(timezone(timedelta(hours=-13)))
        self.assertEqual(local.day, 11)
        self.assertEqual(codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=local,
                                                      deadline=time.monotonic() + 30), path)

    def test_plain_files_in_two_window_folders_are_ambiguous(self):
        self.standard(folder=self.folder(day=12))
        self.standard(folder=self.folder(day=11), mode=0o000)
        self.assertGap(self.read(), "thread_file_ambiguous")

    def test_lookup_matches_only_the_full_name_form(self):
        self.standard()
        self.standard(thread=OTHER, name=f"rollout-x-{OTHER}.jsonl")
        self.standard(thread=OTHER, name=f"rollout-2026-11-12T09-00-00-{OTHER}.jsonl.bak")
        self.standard(thread=OTHER, name=f"rollout-2026-11-12T9-00-00-{OTHER}.jsonl")
        with self.spy() as (opened, _):
            for short in ("000000000001", "8000-000000000001", "0000-7000-8000-000000000001"):
                with self.subTest(short=short):
                    self.assertIsNone(codex_usage.find_thread_file(
                        short, started_at=START, cutoff_at=CUTOFF, deadline=time.monotonic() + 30))
                    self.assertGap(self.read(short), "thread_file_not_found")
            self.assertGap(self.read(OTHER), "thread_file_not_found")
        self.assertEqual(opened, [])

    def test_gaps_carry_no_exception_context(self):
        folder = self.folder()
        self.standard()
        os.chmod(folder, 0o000)
        self.addCleanup(os.chmod, folder, 0o755)
        with self.assertRaises(codex_usage.UsageGap) as raised:
            codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=CUTOFF, deadline=time.monotonic() + 30)
        os.chmod(folder, 0o755)
        gaps = [raised.exception]
        bad_json = self.write([meta(), b'{"type": "turn_context", "x": "' + SECRET.encode()])
        reading = codex_usage._Reading(THREAD, START, CUTOFF, QUALIFIED)
        held = os.open(bad_json.parent, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, held)
        for call in (lambda: codex_usage._scan(bad_json.name, reading, time.monotonic() + 30, folder=held),
                     lambda: codex_usage._scan("absent.jsonl", reading, time.monotonic() + 30, folder=held),
                     lambda: codex_usage._time(SECRET)):
            with self.assertRaises(codex_usage.UsageGap) as raised:
                call()
            gaps.append(raised.exception)
        self.assertEqual([gap.code for gap in gaps],
                         ["file_unreadable", "file_unreadable", "file_unreadable", "timestamp_unusable"])
        for gap in gaps:
            self.assertIsNone(gap.__context__)
            self.assertIsNone(gap.__cause__)

    def test_scan_requires_a_held_folder(self):
        path = self.standard()
        reading = codex_usage._Reading(THREAD, START, CUTOFF, QUALIFIED)
        with self.assertRaises(TypeError):
            codex_usage._scan(path, reading, time.monotonic() + 30)
        with self.assertRaises(TypeError):
            codex_usage._scan(path.name, reading, time.monotonic() + 30, path.parent)

    def test_unreadable_folders_on_the_lookup_path_are_unreadable(self):
        self.standard()
        sessions = self.sessions
        for folder in (self.home, sessions, sessions / "2026", sessions / "2026" / "11"):
            with self.subTest(folder=folder.name):
                os.chmod(folder, 0o000)
                try:
                    self.assertGap(self.read(), "file_unreadable")
                finally:
                    os.chmod(folder, 0o700)

    def test_lookup_ambiguity_is_a_typed_gap(self):
        self.standard()
        self.standard(name=f"rollout-2026-11-12T11-00-00-{THREAD}.jsonl")
        with self.assertRaises(codex_usage.UsageGap) as raised:
            codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=CUTOFF, deadline=time.monotonic() + 30)
        self.assertEqual((raised.exception.code, str(raised.exception)), ("thread_file_ambiguous",) * 2)

    def test_codex_home_that_is_a_file_is_not_found(self):
        home = self.root / "home-file"
        home.write_bytes(b"")
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            self.assertGap(self.read(), "thread_file_not_found")

    def test_missing_codex_home_is_not_found(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "absent")}):
            self.assertGap(self.read(), "thread_file_not_found")
            self.assertEqual(codex_usage.find_thread_files(THREAD, started_at=START, cutoff_at=CUTOFF,
                                                           deadline=time.monotonic() + 30), [])
        self.assertEqual(codex_usage.find_thread_files(THREAD, started_at=START, cutoff_at=CUTOFF,
                                                       deadline=time.monotonic() + 30), [])

    # Paginated history

    def page(self, rows, stamp_text="11-00-00", page=PAGE, **kwargs):
        name = f"rollout-2026-11-12T{stamp_text}-{kwargs.pop('thread', THREAD)}_{page}.jsonl"
        return self.write(rows, name=name, **kwargs)

    def plain(self, **kwargs):
        return self.write([meta(), context(at(9, 2)), token(at(9, 30), 100), token(at(10, 30), 200)], **kwargs)

    def test_paginated_thread_sums_across_files(self):
        self.plain()
        self.page([meta(at(11), version="0.167.1"), token(at(11, 10), 201), token(at(11, 30), 350)])
        result = self.read(qualified=frozenset({VERSION, "0.167.1"}))
        self.assertEqual((result["gaps"], result["cli_version"]), ([], "0.167.1"))
        self.assertEqual((result["baseline"], result["final"], result["usage"]),
                         (totals(100), totals(350), totals(250)))

    def test_pages_in_different_date_folders_are_read_in_name_order(self):
        self.plain(folder=self.folder(day=10))
        self.page([meta(at(11)), token(at(11, 10), info=None), token(at(11, 20), 201)], folder=self.folder(day=11))
        self.page([meta(at(11, 40)), token(at(11, 45), 251), token(at(11, 50), 300)], "11-40-00", PAGE2)
        paths = codex_usage.find_thread_files(THREAD, started_at=START, cutoff_at=CUTOFF,
                                              deadline=time.monotonic() + 30)
        self.assertEqual([path.parent.name for path in paths], ["10", "11", "12"])
        self.assertEqual([path.name.endswith(".jsonl") and "_" in path.name for path in paths], [False, True, True])
        # 201 continues 200; 251 does not continue 201 + 1, so the second page breaks continuity.
        self.assertGap(self.read(), "history_incomplete")
        self.page([meta(at(11, 40)), token(at(11, 45), 202), token(at(11, 50), 300)], "11-40-00", PAGE2)
        self.assertEqual(self.read()["usage"], totals(200))

    def test_discontinuous_page_is_history_incomplete(self):
        no_last = token(at(11, 10), 201)
        del no_last["payload"]["info"]["last_token_usage"]
        for first in (token(at(11, 10), 260), token(at(11, 10), 200), no_last,
                      token(at(11, 10), info={"total_token_usage": totals(201)})):
            with self.subTest(first=first["payload"]["info"]):
                self.plain()
                path = self.page([meta(at(11)), first, token(at(11, 30), 400)])
                self.assertGap(self.read(), "history_incomplete")
                path.unlink()

    def test_page_only_history_before_task_start_is_incomplete(self):
        path = self.page([meta(at(11)), token(at(11, 10), 500), token(at(11, 30), 600)])
        self.assertGap(self.read(), "history_incomplete")
        path.unlink()
        # The header time decides even when a token row is placed before the task start.
        self.page([meta(at(9), created=at(10, 30)), token(at(9, 30), 500), token(at(10, 40), 600)], "09-00-00")
        self.assertGap(self.read(), "history_incomplete")

    def test_page_only_history_from_before_task_start_counts(self):
        self.page([meta(at(9)), context(at(9, 1)), token(at(9, 30), 500), token(at(10, 30), 600)], "09-00-00")
        result = self.read()
        self.assertEqual((result["gaps"], result["baseline"], result["usage"]), ([], totals(500), totals(100)))

    def test_page_only_without_token_row_before_start_is_incomplete(self):
        self.page([meta(at(9)), token(at(10, 30), 600)], "09-00-00")
        self.assertGap(self.read(), "history_incomplete")

    def test_page_before_plain_file_is_history_incomplete(self):
        self.plain()
        # 99 + 1 = 100 would pass continuity, so only the order rule rejects it.
        self.page([meta(at(8)), token(at(8, 10), 99)], "08-00-00")
        self.assertGap(self.read(), "history_incomplete")

    def test_equal_name_timestamps_are_ambiguous(self):
        self.plain()
        self.page([meta(at(11)), token(at(11, 10), 201)], "09-00-00")
        self.assertGap(self.read(), "thread_file_ambiguous")
        with self.assertRaises(codex_usage.UsageGap) as raised:
            codex_usage.find_thread_files(THREAD, started_at=START, cutoff_at=CUTOFF, deadline=time.monotonic() + 30)
        self.assertEqual(raised.exception.code, "thread_file_ambiguous")

    def test_find_thread_file_rejects_a_multi_file_thread(self):
        plain = self.plain()
        self.assertEqual(codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=CUTOFF,
                                                      deadline=time.monotonic() + 30), plain)
        self.page([meta(at(11)), token(at(11, 10), 201)])
        with self.assertRaises(codex_usage.UsageGap) as raised:
            codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=CUTOFF, deadline=time.monotonic() + 30)
        self.assertEqual(raised.exception.code, "thread_file_ambiguous")

    def test_pages_for_other_threads_and_malformed_page_names_are_never_opened(self):
        plain = self.plain()
        self.page([meta(at(11), thread=OTHER)], thread=OTHER, mode=0o000)
        self.write([meta(at(11))], name=f"rollout-2026-11-12T11-00-00-{THREAD}_notauuid.jsonl", mode=0o000)
        self.write([meta(at(11))], name=f"rollout-2026-11-12T11-00-00-{THREAD}_{PAGE}_{PAGE2}.jsonl", mode=0o000)
        with self.spy() as (opened, _):
            result = self.read()
        self.assertEqual((result["gaps"], result["usage"]), ([], totals(100)))
        self.assertEqual(opened, [plain.name])

    def test_middle_page_without_token_rows_carries_the_total(self):
        self.plain()
        self.page([meta(at(11)), context(at(11, 5), model="gpt-5.5-mini", effort="low")])
        self.page([meta(at(11, 40)), token(at(11, 45), 201), token(at(11, 50), 260)], "11-40-00", PAGE2)
        result = self.read()
        self.assertEqual((result["gaps"], result["baseline"], result["usage"]), ([], totals(100), totals(160)))
        self.assertEqual([item["model"] for item in result["model_settings"]], ["gpt-5.5", "gpt-5.5-mini"])
        self.page([meta(at(11, 40)), token(at(11, 45), 202)], "11-40-00", PAGE2)
        self.assertGap(self.read(), "history_incomplete")

    def test_malformed_marker_row_in_an_earlier_file_matches_the_single_file_gap(self):
        bad = b'{"type": "event_msg", "payload": {"type": "token_count", "x": "' + SECRET.encode()
        big = json.dumps(token(at(9, 40), 1, info={"total_token_usage": totals(1), "pad": "x" * 5000})).encode()
        for row, code in ((bad, "file_unreadable"), (big, "line_too_large")):
            with self.subTest(code=code), patch.object(codex_usage, "MAX_LINE", 4096):
                plain = self.write([meta(), token(at(9, 30), 100), row, token(at(10, 30), 200)])
                self.assertGap(self.read(), code)
                page = self.page([meta(at(11)), token(at(11, 10), 201)])
                self.assertGap(self.read(), code)
                page.unlink()
                plain.unlink()

    def test_page_name_collision_id_is_refused_before_lookup(self):
        collision = f"{THREAD}_{PAGE}"
        page = self.page([meta(at(11)), token(at(11, 10), 201)])
        self.assertEqual(page.name, f"rollout-2026-11-12T11-00-00-{collision}.jsonl")
        calls = (lambda: self.read(collision),
                 lambda: self.acquire([{"thread_id": collision, "role": "worker"}]),
                 lambda: codex_usage.find_thread_files(collision, started_at=START, cutoff_at=CUTOFF,
                                                       deadline=time.monotonic() + 30))
        with self.spy() as (opened, listed):
            for call in calls:
                with self.assertRaisesRegex(ValueError, "^Invalid Codex thread ID$"):
                    call()
        self.assertEqual((opened, listed), ([], []))

    def test_page_rows_must_follow_earlier_files_in_time(self):
        self.plain()
        self.page([meta(at(10, 20)), token(at(10, 40), 201)])
        self.assertGap(self.read(), "timestamp_unusable")

    def test_each_page_must_pass_its_own_header_and_version_checks(self):
        cases = (([meta(at(11), thread=PAGE), token(at(11, 10), 201)], "session_meta_mismatch", QUALIFIED),
                 ([message(at(11)), token(at(11, 10), 201)], "session_meta_mismatch", QUALIFIED),
                 ([meta(at(11), version="0.168.0"), token(at(11, 10), 201)], "source_version_unqualified", QUALIFIED),
                 ([meta(at(11)), token(at(11, 10), 201), meta(at(11, 20), thread=OTHER)], "session_meta_mismatch",
                  QUALIFIED))
        for rows, code, qualified in cases:
            with self.subTest(code=code, rows=len(rows)):
                self.plain()
                path = self.page(rows)
                self.assertGap(self.read(qualified=qualified), code)
                path.unlink()
        self.page([meta(at(11)), meta(at(11, 1), thread=OTHER), token(at(11, 10), 201)])
        self.assertEqual(self.read()["usage"], totals(101))

    def test_page_after_cutoff_is_not_opened(self):
        self.write([meta(), token(at(9, 30), 100), token(at(10, 30), 200), token(at(12, 30), 300)])
        page = self.page([meta(at(13)), token(at(13, 10), 301)], "13-00-00", mode=0o000)
        with self.spy() as (opened, _):
            result = self.read()
        self.assertEqual((result["gaps"], result["usage"]), ([], totals(100)))
        self.assertNotIn(page.name, opened)

    def test_thread_byte_limit_spans_files(self):
        self.plain()
        self.page([meta(at(11)), token(at(11, 10), 201)])
        sizes = sum(path.stat().st_size for path in self.folder().iterdir())
        self.assertEqual(codex_usage.MAX_THREAD_BYTES, 256 * 1024 * 1024)
        with patch.object(codex_usage, "MAX_THREAD_BYTES", sizes - 1):
            self.assertGap(self.read(), "file_too_large")
        with patch.object(codex_usage, "MAX_THREAD_BYTES", sizes):
            self.assertEqual(self.read()["usage"], totals(101))

    # Streaming

    def test_unmarked_lines_are_skipped_unparsed(self):
        self.write([meta(), b"{not json " + SECRET.encode(), message(at(10, 1), "z" * 9000), token(at(10, 30), 300)])
        with (patch.object(codex_usage, "MAX_LINE", 4096),
              patch.object(codex_usage, "json_object", wraps=codex_usage.json_object) as parse):
            result = self.read()
        self.assertEqual(result["usage"], totals(300))
        self.assertEqual(parse.call_count, 2)

    def test_unterminated_last_line_is_ignored(self):
        self.write([meta(), token(at(10, 30), 300), token(at(11), 500)], terminated=False)
        self.assertEqual(self.read()["usage"], totals(300))
        self.write([meta(), token(at(10, 30), 300)], extra=b'{"type": "event_msg", "payload": {"type": "token_count"')
        self.assertEqual(self.read()["usage"], totals(300))

    def test_unterminated_oversized_last_line_is_ignored(self):
        tail = json.dumps(token(at(11), 500, info={"total_token_usage": totals(5), "pad": "p" * 9000})).encode()
        self.write([meta(), token(at(10, 30), 300)], extra=tail)
        with patch.object(codex_usage, "MAX_LINE", 4096):
            self.assertEqual(self.read()["usage"], totals(300))

    def test_rows_after_cutoff_are_not_read(self):
        self.write([meta(), token(at(10, 30), 30), token(at(12, 5), 60), b'{"type": "token_count", broken'])
        self.assertEqual(self.read()["usage"], totals(30))

    # Path guards

    def test_symlinked_file_is_unreadable(self):
        real = self.standard(folder=self.folder(day=1, month=1))
        (self.folder() / f"rollout-2026-11-12T09-00-00-{THREAD}.jsonl").symlink_to(real)
        self.assertGap(self.read(), "file_unreadable")

    def test_symlinked_directories_are_unreadable(self):
        outside = self.root / "outside" / "12"
        outside.mkdir(parents=True)
        self.standard(folder=outside)
        (self.folder(day=1).parent / "12").symlink_to(outside)
        self.assertGap(self.read(), "file_unreadable")

    def test_symlinked_sessions_directory_is_unreadable(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        self.sessions.symlink_to(outside)
        self.standard(folder=self.folder())
        self.assertGap(self.read(), "file_unreadable")

    def test_symlinked_codex_home_is_resolved_like_lineage_guard(self):
        self.standard()
        link = self.root / "home-link"
        link.symlink_to(self.home)
        with patch.dict(os.environ, {"CODEX_HOME": str(link)}):
            self.assertEqual(self.read()["usage"], totals(250))

    def test_file_writable_by_others_is_unreadable(self):
        for mode in (0o660, 0o606, 0o666, 0o620, 0o602, 0o664):
            with self.subTest(mode=oct(mode)):
                path = self.standard(mode=mode)
                self.assertGap(self.read(), "file_unreadable")
                path.unlink()

    def test_file_readable_by_others_is_accepted(self):
        for mode in (0o644, 0o640, 0o604, 0o600, 0o400):
            with self.subTest(mode=oct(mode)):
                path = self.standard(mode=mode)
                result = self.read()
                self.assertEqual((result["gaps"], result["usage"]), ([], totals(250)))
                path.unlink()

    def test_hard_linked_file_is_unreadable(self):
        path = self.standard()
        os.link(path, self.root / "copy.jsonl")
        self.assertGap(self.read(), "file_unreadable")

    def test_shared_writable_directory_is_unreadable(self):
        self.standard()
        os.chmod(self.folder(), 0o777)
        self.addCleanup(os.chmod, self.folder(), 0o755)
        self.assertGap(self.read(), "file_unreadable")

    def test_directory_named_like_thread_file_is_unreadable(self):
        (self.folder() / f"rollout-2026-11-12T09-00-00-{THREAD}.jsonl").mkdir()
        self.assertGap(self.read(), "file_unreadable")

    # Inputs

    def test_naive_times_are_rejected(self):
        naive = datetime(2026, 11, 12, 10)
        for started, cutoff in ((naive, CUTOFF), (START, naive), ("2026-11-12", CUTOFF)):
            with self.assertRaisesRegex(ValueError, "^Codex usage times must be timezone-aware datetimes$"):
                self.read(started=started, cutoff=cutoff)
        with self.assertRaisesRegex(ValueError, "^Codex usage start must not follow its cutoff$"):
            self.read(started=CUTOFF, cutoff=START)
        with self.assertRaisesRegex(ValueError, "^Codex usage deadline must be a monotonic time$"):
            self.read(deadline=True)

    def test_invalid_thread_ids_are_rejected_before_lookup(self):
        for value in ("../" + THREAD, "a*b", "a/b", "", "x" * 129, None, "a.b", "ä", "a_b", f"{THREAD}_{PAGE}"):
            with self.subTest(value=value), patch.object(codex_usage.os, "listdir") as listing:
                with self.assertRaisesRegex(ValueError, "^Invalid Codex thread ID$"):
                    self.read(value)
                listing.assert_not_called()

    def test_qualified_versions_must_be_a_set_of_strings(self):
        for value in (["0.167.0"], frozenset({1}), "0.167.0"):
            with self.assertRaisesRegex(ValueError, "^Invalid qualified Codex versions$"):
                self.read(qualified=value)

    # Task observations

    def acquire(self, threads, qualified=QUALIFIED):
        return codex_usage.acquire_task_observations(threads, started_at=START, cutoff_at=CUTOFF,
                                                     deadline=time.monotonic() + 30, qualified_versions=qualified)

    def test_task_observations_complete(self):
        self.standard()
        self.write([meta(at(10, 30), thread=OTHER), token(at(10, 40), 7)], thread=OTHER)
        result = self.acquire([{"thread_id": THREAD, "role": "coordinator"}, {"thread_id": OTHER, "role": "worker"}])
        self.assertEqual((result["expected_threads"], result["coverage"], result["gaps"]), (2, "complete", []))
        self.assertEqual([(item["role"], item["thread_id"], item["usage"]) for item in result["threads"]],
                         [("coordinator", THREAD, totals(250)), ("worker", OTHER, totals(7))])

    def test_task_without_threads_is_incomplete(self):
        result = self.acquire([])
        self.assertEqual(result, {"threads": [], "expected_threads": 0, "coverage": "incomplete",
                                  "gaps": [{"thread_id": None, "code": "no_threads"}]})

    def test_task_observations_incomplete_on_any_gap(self):
        self.standard()
        result = self.acquire([{"thread_id": THREAD, "role": "coordinator"}, {"thread_id": OTHER, "role": "reviewer"}])
        self.assertEqual((result["expected_threads"], result["coverage"]), (2, "incomplete"))
        self.assertEqual(result["gaps"], [{"thread_id": OTHER, "code": "thread_file_not_found"}])
        self.assertEqual(result["threads"][0]["usage"], totals(250))
        self.assertEqual(result["threads"][1]["usage"], UNKNOWN)
        unqualified = self.acquire([{"thread_id": THREAD, "role": "coordinator"}], qualified=frozenset())
        self.assertEqual(unqualified["gaps"], [{"thread_id": THREAD, "code": "source_version_unqualified"}])

    def test_task_threads_are_validated(self):
        bad = ({"thread_id": THREAD}, {"thread_id": THREAD, "role": "worker", "path": "x"},
               {"thread_id": "../x", "role": "worker"}, {"thread_id": THREAD, "role": ""},
               {"thread_id": THREAD, "role": 3}, [THREAD, "worker"])
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.acquire([item])
        with self.assertRaisesRegex(ValueError, "^Duplicate Codex task thread$"):
            self.acquire([{"thread_id": THREAD, "role": "coordinator"}, {"thread_id": THREAD, "role": "worker"}])
        with self.assertRaises(ValueError):
            self.acquire({"thread_id": THREAD, "role": "worker"})
        with self.assertRaises(ValueError):
            self.acquire([{"thread_id": f"t{n}", "role": "worker"} for n in range(codex_usage.MAX_THREADS + 1)])

    # Privacy

    def test_secret_text_never_leaves_the_reader(self):
        scenarios = (
            [meta(), message(at(9, 1)), context(at(9, 2)), token(at(10, 30), 10), message(at(10, 31))],
            [meta(), b'{"type": "turn_context", "payload": "' + SECRET.encode() + b'"'],
            [meta(), {"timestamp": SECRET, "type": "event_msg", "payload": {"type": "token_count", "info": None}}],
            [meta(), token(at(10, 30), info={"total_token_usage": SECRET})],
            [meta(), context(at(10, 30), model=SECRET), token(at(10, 31), 2)],
            [meta(thread=SECRET[:8])], [meta(version=SECRET)])
        for rows in scenarios:
            with self.subTest(case=scenarios.index(rows)), self.assertNoLogs(level="DEBUG"):
                path = self.write(rows)
                with patch.object(codex_usage, "MAX_LINE", 4096):
                    observed = self.acquire([{"thread_id": THREAD, "role": "coordinator"}])
                text = repr(observed)
                self.assertNotIn(SECRET, text)
                self.assertNotIn("hunter2", text)
                self.assertNotIn(str(self.root), text)
                path.unlink()
        for call in (lambda: self.read(SECRET), lambda: self.acquire([{"thread_id": THREAD, "role": SECRET * 10}])):
            with self.assertRaises(ValueError) as raised:
                call()
            self.assertNotIn("hunter2", repr(raised.exception) + str(raised.exception.__context__))

    def test_model_label_with_unprintable_text_is_dropped(self):
        self.write([meta(), context(at(10, 30), model="gpt\n" + SECRET), token(at(10, 31), 2)])
        settings = self.read()["model_settings"]
        self.assertEqual(settings, [{"model": None, "effort": "high", "basis": "configured"}])

    def test_gap_codes_are_fixed(self):
        self.assertEqual(codex_usage.GAP_CODES, frozenset({
            "thread_file_not_found", "thread_file_ambiguous", "file_unreadable", "file_too_large",
            "line_too_large", "session_meta_mismatch", "source_version_unqualified", "token_info_missing",
            "counter_missing", "counter_decreased", "context_window_saturated", "timestamp_unusable",
            "deadline_exceeded", "no_threads", "history_incomplete"}))
        with self.assertRaises(ValueError):
            codex_usage.UsageGap("other")


if __name__ == "__main__":
    unittest.main()
