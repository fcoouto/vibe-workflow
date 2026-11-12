"""Codex session-file usage reader: synthetic files only, never the real ~/.codex."""

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
SECRET = "sk-live-7f3a9cSECRET user said: my password is hunter2-zebra"
START = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
VERSION = "0.167.0"
QUALIFIED = frozenset({VERSION})
KEYS = codex_usage.COUNTERS
UNKNOWN = dict.fromkeys(KEYS)


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

    def test_marker_after_line_limit_is_still_too_large(self):
        row = json.dumps(message(at(10, 1), "y" * 9000))[:-1] + ', "kind": "token_count"}'
        self.write([meta(), row.encode()])
        with patch.object(codex_usage, "MAX_LINE", 4096):
            self.assertGap(self.read(), "line_too_large")

    def test_marker_split_across_read_boundary_is_too_large(self):
        head = json.dumps(message(at(10, 1), "y" * 5000)).encode()[:4090]
        self.write([meta(), head + b'"token_count"' + b"w" * 6000])
        with patch.object(codex_usage, "MAX_LINE", 4096):
            self.assertGap(self.read(), "line_too_large")

    def test_session_meta_mismatch(self):
        cases = ([meta(thread=OTHER), token(at(10, 30), 1)],
                 [message(at(9)), meta(), token(at(10, 30), 1)],
                 [context(at(9)), token(at(10, 30), 1)],
                 [meta(), meta(at(10)), token(at(10, 30), 1)],
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

    def test_source_version_unqualified(self):
        self.standard()
        result = codex_usage.read_thread_usage(THREAD, started_at=START, cutoff_at=CUTOFF,
                                               deadline=time.monotonic() + 30)
        self.assertGap(result, "source_version_unqualified")
        self.assertEqual(result["cli_version"], VERSION)
        self.assertEqual(codex_usage.QUALIFIED_VERSIONS, frozenset())
        self.assertGap(self.read(qualified=frozenset({"0.168.0"})), "source_version_unqualified")

    def test_missing_or_invalid_version_is_unqualified(self):
        for version in (None, 167, "", "x" * 200):
            with self.subTest(version=version):
                path = self.write([meta(version=version), token(at(10, 30), 1)])
                result = self.read()
                self.assertGap(result, "source_version_unqualified")
                self.assertIsNone(result["cli_version"])
                path.unlink()

    def test_token_info_missing(self):
        cases = ([meta(), token(at(10, 30), info=None)],
                 [meta(), token(at(9, 30), 1), token(at(10, 30), info={"last_token_usage": totals(1)})],
                 [meta(), token(at(9, 30), info=None)],
                 [meta(), token(at(10, 30), 1, window="large")])
        for rows in cases:
            with self.subTest(rows=rows[-1]["payload"]["info"]):
                path = self.write(rows)
                self.assertGap(self.read(), "token_info_missing")
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

    # Lookup

    def test_lookup_lists_only_window_folders_and_opens_only_the_thread_file(self):
        target = self.standard(folder=self.folder(day=10))
        decoy = self.standard(folder=self.folder(day=2, month=10), mode=0o000)
        other = self.standard(thread=OTHER, folder=self.folder(day=10), mode=0o000)
        newer = self.standard(thread=OTHER, folder=self.folder(day=13), mode=0o000)
        listed, opened = [], []
        real_listdir, real_open = os.listdir, os.open
        with (patch.object(codex_usage.os, "listdir", side_effect=lambda p: listed.append(Path(p)) or real_listdir(p)),
              patch.object(codex_usage.os, "open", side_effect=lambda p, *a, **k: opened.append(Path(p))
                           or real_open(p, *a, **k))):
            result = self.read()
        self.assertEqual(result["usage"], totals(250))
        self.assertEqual(opened, [target])
        self.assertEqual(listed, [newer.parent, target.parent])
        self.assertNotIn(decoy.parent, listed)
        self.assertNotIn(other, opened)

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

    def test_lookup_stops_at_first_matching_folder(self):
        newest = self.standard(folder=self.folder(day=12))
        self.standard(folder=self.folder(day=11), mode=0o000)
        self.assertEqual(codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=CUTOFF,
                                                      deadline=time.monotonic() + 30), newest)

    def test_lookup_ambiguity_is_a_typed_gap(self):
        self.standard()
        self.standard(name=f"rollout-2026-11-12T11-00-00-{THREAD}.jsonl")
        with self.assertRaises(codex_usage.UsageGap) as raised:
            codex_usage.find_thread_file(THREAD, started_at=START, cutoff_at=CUTOFF, deadline=time.monotonic() + 30)
        self.assertEqual((raised.exception.code, str(raised.exception)), ("thread_file_ambiguous",) * 2)

    def test_missing_codex_home_is_not_found(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "absent")}):
            self.assertGap(self.read(), "thread_file_not_found")

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

    def test_non_private_file_is_unreadable(self):
        for mode in (0o640, 0o604, 0o660):
            with self.subTest(mode=oct(mode)):
                path = self.standard(mode=mode)
                self.assertGap(self.read(), "file_unreadable")
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
        for value in ("../" + THREAD, "a*b", "a/b", "", "x" * 129, None, "a.b", "ä"):
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

    def test_task_observations_incomplete_on_any_gap(self):
        self.standard()
        result = self.acquire([{"thread_id": THREAD, "role": "coordinator"}, {"thread_id": OTHER, "role": "reviewer"}])
        self.assertEqual((result["expected_threads"], result["coverage"]), (2, "incomplete"))
        self.assertEqual(result["gaps"], [{"thread_id": OTHER, "code": "thread_file_not_found"}])
        self.assertEqual(result["threads"][0]["usage"], totals(250))
        self.assertEqual(result["threads"][1]["usage"], UNKNOWN)
        unqualified = codex_usage.acquire_task_observations(
            [{"thread_id": THREAD, "role": "coordinator"}], started_at=START, cutoff_at=CUTOFF,
            deadline=time.monotonic() + 30)
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
            "deadline_exceeded"}))
        with self.assertRaises(ValueError):
            codex_usage.UsageGap("other")


if __name__ == "__main__":
    unittest.main()
