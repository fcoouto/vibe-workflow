"""Task usage reconciliation (D9 O4): pure combination of O3 observations and one verification result."""

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import tests  # noqa: F401  (redirects HOME before the runtime is imported)
from runtime.adapters import codex_usage
from runtime.observability_accounting import reconcile_task
from tests import test_codex_usage as fixtures


KEYS = codex_usage.COUNTERS
UNKNOWN = dict.fromkeys(KEYS)
SECRET = "hunter2-zebra-secret"
THREAD_A = "thread-a"
THREAD_B = "thread-b"


def counts(n):
    return {key: n * (index + 1) for index, key in enumerate(KEYS)}


def settings(model="gpt-5.5", effort="high"):
    return [{"model": model, "effort": effort, "basis": "configured"}]


def thread(thread_id=THREAD_A, role="coordinator", *, usage=None, gaps=(), version="0.167.0", model_settings=None):
    known = not gaps
    usage = counts(10) if usage is None and known else usage
    return {"role": role, "source": codex_usage.SOURCE, "cli_version": version, "thread_id": thread_id,
            "baseline": counts(1) if known else dict(UNKNOWN), "final": counts(11) if known else dict(UNKNOWN),
            "usage": dict(UNKNOWN) if usage is None else usage,
            "model_settings": (settings() if known else []) if model_settings is None else model_settings,
            "gaps": list(gaps)}


def observations(*threads, expected=None, coverage=None, gaps=None):
    if gaps is None:
        gaps = [{"thread_id": item["thread_id"], "code": code} for item in threads for code in item["gaps"]]
        if not threads:
            gaps.append({"thread_id": None, "code": "no_threads"})
    return {"threads": list(threads), "expected_threads": len(threads) if expected is None else expected,
            "coverage": coverage or ("incomplete" if gaps else "complete"), "gaps": gaps}


def verification(*, verified=True, admission_mode="ordinary", revision=7):
    return {"ok": True, "admission_mode": admission_mode, "verified": verified,
            "task": {"client": "codex", "session_id": "session-1", "task_id": "task-1", "revision": revision,
                     "state": "completed"},
            "blockers": [], "candidate_digest": None, "merge_authority": False}


class ReconcileTaskTests(unittest.TestCase):
    def reconcile(self, observed, verified=None):
        return reconcile_task(observed, verification=verification() if verified is None else verified)

    def assertRefused(self, observed, verified=None):
        with self.assertRaisesRegex(ValueError, "^Invalid task usage reconciliation input$") as raised:
            self.reconcile(observed, verified)
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertIsNone(raised.exception.__context__)

    # Totals and coverage

    def test_totals_sum_across_threads(self):
        observed = observations(thread(THREAD_A, usage=counts(10)), thread(THREAD_B, "worker", usage=counts(5),
                                                                         version="0.168.0"))
        result = self.reconcile(observed)
        self.assertEqual(set(result), {"task_ref", "verified", "admission_mode", "reconciled_revision", "totals",
                                       "counted_threads", "coverage", "gaps", "source_versions", "model_settings"})
        self.assertEqual(result["totals"], counts(15))
        self.assertEqual(list(result["totals"]), list(KEYS))
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["counted_threads"], [{"thread_id": THREAD_A, "role": "coordinator"},
                                                     {"thread_id": THREAD_B, "role": "worker"}])
        self.assertEqual(result["source_versions"], ["0.167.0", "0.168.0"])

    def test_zero_usage_is_a_known_total(self):
        result = self.reconcile(observations(thread(usage=counts(0))))
        self.assertEqual((result["totals"], result["coverage"]), (counts(0), "complete"))

    def test_one_thread_gap_makes_every_total_unknown(self):
        observed = observations(thread(THREAD_A, usage=counts(10)),
                                thread(THREAD_B, "reviewer", gaps=["thread_file_not_found"], version=None))
        result = self.reconcile(observed)
        self.assertEqual(result["totals"], UNKNOWN)
        self.assertEqual(result["coverage"], "incomplete")
        self.assertEqual(result["gaps"], [{"thread_id": THREAD_B, "code": "thread_file_not_found"}])
        self.assertEqual(result["counted_threads"], [{"thread_id": THREAD_A, "role": "coordinator"}])
        self.assertEqual(result["source_versions"], ["0.167.0"])

    def test_one_unknown_counter_makes_only_that_total_unknown(self):
        partial = counts(4) | {"cached_input_tokens": None}
        result = self.reconcile(observations(thread(THREAD_A, usage=counts(10)), thread(THREAD_B, usage=partial)))
        self.assertEqual(result["totals"], counts(14) | {"cached_input_tokens": None})
        self.assertEqual(result["coverage"], "incomplete")

    def test_zero_threads_are_unknown_and_incomplete(self):
        result = self.reconcile(observations())
        self.assertEqual(result["totals"], UNKNOWN)
        self.assertEqual(result["coverage"], "incomplete")
        self.assertEqual(result["gaps"], [{"thread_id": None, "code": "no_threads"}])
        self.assertEqual((result["counted_threads"], result["source_versions"], result["model_settings"]),
                         ([], [], []))

    def test_expected_thread_mismatch_forces_incomplete(self):
        result = self.reconcile(observations(thread(), expected=2, coverage="complete", gaps=[]))
        self.assertEqual((result["totals"], result["coverage"]), (counts(10), "incomplete"))

    def test_listed_gap_forces_incomplete(self):
        observed = observations(thread(), coverage="complete", gaps=[{"thread_id": None, "code": "no_threads"}])
        self.assertEqual(self.reconcile(observed)["coverage"], "incomplete")

    def test_incomplete_coverage_is_kept(self):
        self.assertEqual(self.reconcile(observations(thread(), coverage="incomplete"))["coverage"], "incomplete")

    def test_gaps_are_copied_in_order(self):
        gaps = [{"thread_id": THREAD_B, "code": "deadline_exceeded"}, {"thread_id": THREAD_A, "code": "file_too_large"}]
        observed = observations(thread(THREAD_A, gaps=["file_too_large"]), thread(THREAD_B, gaps=["deadline_exceeded"]),
                                gaps=gaps)
        result = self.reconcile(observed)
        self.assertEqual(result["gaps"], gaps)
        self.assertIsNot(result["gaps"][0], gaps[0])

    def test_source_versions_are_sorted_distinct_and_non_null(self):
        observed = observations(thread("t1", version="0.168.0"), thread("t2", version=None, gaps=["file_unreadable"]),
                                thread("t3", version="0.167.0"), thread("t4", version="0.168.0"),
                                thread("t5", version="0.170.1"), thread("t6", version="0.100.0"),
                                thread("t7", version="0.169.0-beta"), thread("t8", version="0.150.0"))
        self.assertEqual(self.reconcile(observed)["source_versions"],
                         ["0.100.0", "0.150.0", "0.167.0", "0.168.0", "0.169.0-beta", "0.170.1"])

    def test_model_settings_are_listed_per_thread_in_input_order(self):
        observed = observations(thread(THREAD_B, "worker", model_settings=settings("model-b", None)),
                                thread(THREAD_A, gaps=["counter_missing"]),
                                thread("thread-c", "reviewer", model_settings=settings() + settings("model-c", "low")))
        self.assertEqual(self.reconcile(observed)["model_settings"], [
            {"thread_id": THREAD_B, "role": "worker", "model_settings": settings("model-b", None)},
            {"thread_id": THREAD_A, "role": "coordinator", "model_settings": []},
            {"thread_id": "thread-c", "role": "reviewer", "model_settings": settings() + settings("model-c", "low")}])

    # Verification copy and purity

    def test_verification_fields_are_copied_exactly(self):
        for verified, mode, revision in ((True, "ordinary", 7), (False, "ordinary", 0), (False, "qualification", 12)):
            with self.subTest(verified=verified, mode=mode, revision=revision):
                result = self.reconcile(observations(thread()), verification(
                    verified=verified, admission_mode=mode, revision=revision))
                self.assertEqual(result["task_ref"], {"client": "codex", "session_id": "session-1",
                                                      "task_id": "task-1"})
                self.assertIs(result["verified"], verified)
                self.assertEqual(result["admission_mode"], mode)
                self.assertEqual(result["reconciled_revision"], revision)
                self.assertIs(type(result["reconciled_revision"]), int)

    def test_inputs_are_not_mutated_and_results_are_new_objects(self):
        observed = observations(thread(THREAD_A), thread(THREAD_B, gaps=["token_info_missing"]))
        verified = verification()
        before = deepcopy((observed, verified))
        result = self.reconcile(observed, verified)
        self.assertEqual((observed, verified), before)
        result["model_settings"][0]["model_settings"][0]["model"] = "changed"
        result["gaps"][0]["code"] = "changed"
        result["task_ref"]["client"] = "changed"
        self.assertEqual((observed, verified), before)

    # Refusals

    def test_observations_keys_must_be_exact(self):
        good = observations(thread())
        for broken in ([good], {key: good[key] for key in ("threads", "expected_threads", "coverage")},
                       good | {"extra": SECRET}):
            with self.subTest(broken=type(broken).__name__):
                self.assertRefused(broken)

    def test_observation_fields_must_be_well_formed(self):
        good = observations(thread())
        for key, value in (("threads", (thread(),)), ("threads", [[THREAD_A]]), ("expected_threads", True),
                           ("expected_threads", -1), ("expected_threads", "1"), ("coverage", "partial"),
                           ("coverage", None), ("gaps", ({"thread_id": None, "code": "no_threads"},)),
                           ("gaps", [{"thread_id": None}]), ("gaps", [{"thread_id": 3, "code": "no_threads"}]),
                           ("gaps", [{"thread_id": None, "code": "no_threads", "path": SECRET}])):
            with self.subTest(key=key, value=value):
                self.assertRefused(good | {key: value})

    def test_thread_keys_must_be_the_o3_keys_plus_role(self):
        for broken in (thread() | {"path": SECRET}, {key: value for key, value in thread().items() if key != "role"},
                       {key: value for key, value in thread().items() if key != "final"}):
            with self.subTest(keys=sorted(broken)):
                self.assertRefused(observations(broken, gaps=[]))

    def test_thread_fields_must_be_well_formed(self):
        for key, value in (("role", 3), ("role", ""), ("thread_id", None), ("thread_id", ""),
                           ("source", "other-source"), ("cli_version", 167), ("model_settings", None),
                           ("model_settings", [SECRET]), ("gaps", "file_unreadable"), ("gaps", None)):
            with self.subTest(key=key, value=value):
                self.assertRefused(observations(thread() | {key: value}, gaps=[]))

    def test_duplicate_threads_are_refused(self):
        self.assertRefused(observations(thread(THREAD_A), thread(THREAD_A, "worker")))

    def test_counters_must_be_integers_or_none(self):
        for field in ("baseline", "final", "usage"):
            for value in (True, False, 1.5, "3", -1, [1]):
                with self.subTest(field=field, value=value):
                    item = thread()
                    item[field] = item[field] | {"output_tokens": value}
                    self.assertRefused(observations(item))
            with self.subTest(field=field, keys="missing"):
                item = thread()
                del item[field]["total_tokens"]
                self.assertRefused(observations(item))
            with self.subTest(field=field, keys="extra"):
                item = thread()
                item[field] = item[field] | {"secret_tokens": 1}
                self.assertRefused(observations(item))

    def test_known_usage_on_a_gapped_thread_is_refused(self):
        self.assertRefused(observations(thread(gaps=["file_unreadable"], usage=counts(3))))

    def test_gap_codes_must_be_known(self):
        self.assertRefused(observations(thread(gaps=[SECRET])))
        self.assertRefused(observations(thread(gaps=[SECRET]), gaps=[{"thread_id": THREAD_A, "code": "file_unreadable"}]))
        self.assertRefused(observations(thread(), gaps=[{"thread_id": THREAD_A, "code": SECRET}]))
        self.assertRefused(observations(thread(), gaps=[{"thread_id": THREAD_A, "code": [SECRET]}]))
        self.assertRefused(observations(thread() | {"gaps": [["file_unreadable"]], "usage": dict(UNKNOWN)}))

    def test_verification_must_be_well_formed(self):
        good = verification()
        task = good["task"]
        cases = [[good], {key: value for key, value in good.items() if key != "verified"},
                 good | {"verified": 1}, good | {"verified": None},
                 {key: value for key, value in good.items() if key != "admission_mode"}, good | {"admission_mode": 1},
                 {key: value for key, value in good.items() if key != "task"}, good | {"task": [task]}]
        for key in ("client", "session_id", "task_id", "revision"):
            cases.append(good | {"task": {name: value for name, value in task.items() if name != key}})
        for key, value in (("client", 1), ("session_id", None), ("task_id", b"task"), ("revision", -1),
                           ("revision", True), ("revision", 1.0), ("revision", "7")):
            cases.append(good | {"task": task | {key: value}})
        for case in cases:
            with self.subTest(case=cases.index(case)):
                self.assertRefused(observations(thread()), case)

    def test_refusal_never_echoes_values(self):
        with self.assertRaises(ValueError) as raised:
            self.reconcile(observations(thread(gaps=[SECRET])) | {SECRET: SECRET})
        self.assertNotIn(SECRET, repr(raised.exception))


class ReconcileRealObservationsTests(unittest.TestCase):
    """Feeds the real O3 reader output, built from synthetic session files, into reconciliation."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve() / "codex"
        self.home.mkdir(mode=0o700)
        environment = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def write(self, rows, thread):
        folder = self.home / "sessions" / "2026" / "11" / "12"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"rollout-2026-11-12T09-00-00-{thread}.jsonl"
        path.write_bytes(b"\n".join(fixtures.json.dumps(row).encode() for row in rows) + b"\n")
        os.chmod(path, 0o600)

    def test_real_observations_reconcile(self):
        at, meta, token, context = fixtures.at, fixtures.meta, fixtures.token, fixtures.context
        self.write([meta(), context(at(9, 2)), token(at(9, 30), 100), token(at(11, 30), 350)], fixtures.THREAD)
        self.write([meta(at(10, 30), thread=fixtures.OTHER), token(at(10, 40), 7)], fixtures.OTHER)
        missing = "019a6f00-0000-7000-8000-000000000003"
        threads = [{"thread_id": fixtures.THREAD, "role": "coordinator"}, {"thread_id": fixtures.OTHER, "role": "worker"}]

        def acquire(items):
            return codex_usage.acquire_task_observations(
                items, started_at=fixtures.START, cutoff_at=fixtures.CUTOFF, deadline=time.monotonic() + 30,
                qualified_versions=fixtures.QUALIFIED)

        complete = reconcile_task(acquire(threads), verification=verification(revision=3))
        self.assertEqual(complete["totals"], {key: fixtures.totals(250)[key] + fixtures.totals(7)[key] for key in KEYS})
        self.assertEqual((complete["coverage"], complete["gaps"], complete["reconciled_revision"]),
                         ("complete", [], 3))
        self.assertEqual(complete["source_versions"], [fixtures.VERSION])
        self.assertEqual(complete["model_settings"], [
            {"thread_id": fixtures.THREAD, "role": "coordinator", "model_settings": settings()},
            {"thread_id": fixtures.OTHER, "role": "worker", "model_settings": []}])

        incomplete = reconcile_task(acquire(threads + [{"thread_id": missing, "role": "reviewer"}]),
                                    verification=verification())
        self.assertEqual((incomplete["totals"], incomplete["coverage"]), (UNKNOWN, "incomplete"))
        self.assertEqual(incomplete["gaps"], [{"thread_id": missing, "code": "thread_file_not_found"}])
        self.assertEqual([item["thread_id"] for item in incomplete["counted_threads"]],
                         [fixtures.THREAD, fixtures.OTHER])

        empty = reconcile_task(acquire([]), verification=verification())
        self.assertEqual((empty["totals"], empty["coverage"], empty["gaps"]),
                         (UNKNOWN, "incomplete", [{"thread_id": None, "code": "no_threads"}]))


if __name__ == "__main__":
    unittest.main()
