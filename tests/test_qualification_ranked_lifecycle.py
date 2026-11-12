"""The ranked canary's whole-run lifecycle across real new processes (F7 correction F1-F5): the approved preflight
digest, the durable owner-only run record, resume after `seed_required`, after a crash with an outstanding hold, with
changed inputs under the same consent and after a changed reset; the ranked CLI branch and its closed call purpose;
and recovery that never weakens on an incomplete stop. Synthetic temporary stores and disposable call ledgers only."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from runtime import routing_observations as collector
from tests.qualification import canary, canary_cli, canary_ranked, canary_ranked_state, safety
from tests.test_qualification_canary_ranked import CLAUDE, CLAUDE_POOL, FakeEnv, RankedStores, now, strategy

ROOT = Path(__file__).resolve().parents[1]
RESUME = r"""
import json, sys, types
from pathlib import Path
sys.path.insert(0, %r)
from unittest import mock
from tests.qualification import canary, canary_ranked, canary_ranked_state, safety
a = json.loads(sys.argv[1])
os = __import__("os")
os.environ["HOME"] = a["home"]
mock.patch.object(safety.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_dir=a["account_home"])).start()
mock.patch.object(canary, "_production_paths", return_value=(Path(a["default_settings"]),
                                                          Path(a["default_ledger"]))).start()
if a.get("pause_create"):
    original_create = canary_ranked_state.create
    def paused_create(*args):
        print(json.dumps(dict(paused=True)), flush=True)
        sys.stdin.readline()
        return original_create(*args)
    mock.patch.object(canary_ranked_state, "create", side_effect=paused_create).start()
run = types.SimpleNamespace(canary_state=dict(settings=Path(a["settings"]), ledger=Path(a["ledger"]), production=True,
                                              approval=dict(by="test", at="test", scope="ranked canary")),
                            canary_project=a["project"], results={},
                            routing=types.SimpleNamespace(harnesses=a["harnesses"]))
try:
    options = {"start": a["start"]} if "start" in a else {}
    c = canary_ranked.setup(run, approved=a["digest"], acknowledge=a.get("acknowledge"), **options)
except Exception as error:
    print(json.dumps(dict(refused=str(error))))
    raise SystemExit(0)
out = dict(state=c.allowance.state(), earliest_reset=c.earliest_reset, completed=c.completed,
           baseline=c.allowance.baseline, path=str(c.path), record=c.record, results=run.results)
try:
    if a.get("sync"):
        c._sync()
    c._deadline()
    c.allowance.check(a.get("reserve") or {})
except (canary.Incomplete, canary.Abort) as stop:
    out["stop"] = stop.reason
c.persist()
print(json.dumps(out))
""" % str(ROOT)


@unittest.skipUnless(shutil.which("git"), "git is required")
class LifecycleTests(RankedStores):
    def started(self):
        run, ledger = self.make_run()
        ranked = self.controller(run)
        return run, ledger, ranked

    def resume(self, digest, home=None, cwd=None, ledger=None, **extra):
        """A new process: `setup` with the approved digest on the same stores; HOME and cwd as given."""
        body = dict(settings=str(self.settings), ledger=str(ledger or self.state["ledger"]), project=str(self.project),
                    harnesses=[CLAUDE], digest=digest, home=str(home or self.home), account_home=str(self.home),
                    default_settings=str(self.settings), default_ledger=str(self.state["ledger"]), **extra)
        done = subprocess.run([sys.executable, "-I", "-B", "-c", RESUME, json.dumps(body)], capture_output=True,
                              text=True, timeout=60, env=dict(os.environ, HOME=str(home or self.home)),
                              cwd=str(cwd or self.tmp))
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def record_path(self):
        return self.home / ".local/state/vibe-workflow-qualification/ranked-canary/campaign.json"

    def records(self):
        """Every run record anywhere under this test's temporary root."""
        return sorted([*self.tmp.rglob("run-*.json"), *self.tmp.rglob("campaign.json")])

    def test_missing_resume_never_creates_even_with_a_fresh_approved_digest(self):
        run, _ = self.make_run()
        answer = self.resume(self.digest(run), acknowledge=8)
        self.assertIn("refused", answer, answer)
        self.assertFalse((self.home / ".local/state/vibe-workflow-qualification").exists())

    def test_old_alias_record_and_held_lock_block_current_exact_setup_unchanged(self):
        exact = self.state["ledger"]
        alias = exact.with_name(exact.name.upper())
        if not (alias.exists() and os.path.samefile(alias, exact)):
            alias = exact  # legacy exact records must also refuse on case-sensitive hardware
        self.legacy_held_refusal(alias)

    def test_old_exact_record_and_held_lock_refuse_unchanged(self):
        self.legacy_held_refusal(self.state["ledger"])

    def legacy_held_refusal(self, alias):
        run, _ = self.make_run()
        directory = self.private(self.home / ".local/state/vibe-workflow-qualification/ranked-canary")
        legacy = directory / ("run-%s.json" % hashlib.sha256(str(alias).encode()).hexdigest()[:16])
        fresh = dict(canary_ranked.preflight(self.settings, [CLAUDE], now(), 8),
                     project=str(self.project), ledger=str(alias))
        value = canary_ranked_state.create(legacy, fresh)
        key = CLAUDE_POOL + "/5h"
        value["cumulative"][key], value["last"][key] = 80, 180
        value.update(holds={"uncertain": {key: 12}}, completed=7)
        canary_ranked_state.save(legacy, value)
        lock = legacy.with_name(legacy.name + ".lock")
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            for options in ({}, {"start": "synthetic-history-decision"}):
                answer = self.resume(self.digest(run), acknowledge=8, **options)
                self.assertIn("refused", answer, answer)
                self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
        finally:
            os.close(descriptor)

    def test_distinct_direct_ledger_cannot_select_another_account_allowance(self):
        run, _ = self.make_run()
        other = self.state["ledger"].with_name("other.sqlite3")
        shutil.copyfile(self.state["ledger"], other)
        record = dict(canary_ranked.preflight(self.settings, [CLAUDE], now(), 8),
                      project=str(self.project), ledger=str(other))
        answer = self.resume(canary_ranked_state.digest(record), ledger=other, acknowledge=8)
        self.assertIn("refused", answer, answer)
        self.assertEqual(self.records(), [])

    def test_approval_binds_the_preflight_digest_before_execution(self):
        run, _ = self.make_run()
        with self.assertRaisesRegex(safety.Refused, "--approve-digest does not match"):
            canary_ranked.setup(run, approved="0" * 64, acknowledge=8, start="synthetic-history-decision")
        approved = self.digest(run)
        self.table(strategy("s-a", "invented-a"), version="tv-2")  # changed after the operator approved
        with self.assertRaisesRegex(safety.Refused, "--approve-digest does not match"):
            canary_ranked.setup(run, approved=approved, acknowledge=8, start="synthetic-history-decision")
        self.assertEqual(self.records(), [])

    def test_the_record_location_has_no_override(self):
        import argparse
        parser = argparse.ArgumentParser()
        canary_cli.add_arguments(parser)
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            parser.parse_args(["--ranked-state-dir", str(self.tmp)])

    def test_another_home_and_cwd_with_a_fresh_digest_cannot_start_a_new_run(self):
        run, ledger, ranked = self.started()
        self.assertIsNone(self.drive(run, ledger, n=1, tag="h1")["abort"])
        canary_ranked.close(ranked)
        collector.store_window(self.settings, CLAUDE_POOL, dict(id="5h", limit=1000, used=140, length=18000,
                                                                reset_at=self.resets["5h"], rolling=False,
                                                                observed_at=now() + 1, unit=collector.WINDOW_UNIT))
        before, fresh = self.record_path().read_bytes(), self.digest(run)
        self.assertNotEqual(fresh, ranked.record["digest"])  # the store moved on: a new preflight differs
        other_home, other_cwd = self.private(self.tmp / "other-home"), self.private(self.tmp / "other-cwd")
        self.assertIn("HOME", self.resume(fresh, home=other_home, cwd=other_cwd)["refused"])
        self.assertIn("does not match", self.resume(fresh, cwd=other_cwd)["refused"])
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.records(), [self.record_path()])  # one account authority, never a caller's anchor

    def test_a_home_symlink_alias_resumes_the_same_record(self):
        run, ledger, ranked = self.started()
        self.assertIsNone(self.drive(run, ledger, n=1, tag="h2")["abort"])
        ranked.before_prepare(self.body(3))
        answer, _ = __import__("tests.qualification.pilot_env", fromlist=["CanaryEnv"]).CanaryEnv(run).prepare(self.body(3))
        ranked.before_dispatch(answer["admission"])  # an outstanding hold, saved before any send
        canary_ranked.close(ranked)
        alias = self.tmp / "home-alias"
        alias.symlink_to(self.home)
        resumed = self.resume(ranked.record["digest"], home=alias,
                              cwd=self.private(self.tmp / "other-cwd"))
        self.assertEqual(resumed["path"], str(self.record_path()))
        self.assertEqual(resumed["state"]["cumulative"][CLAUDE_POOL + "/5h"], 20)
        self.assertEqual(list(resumed["state"]["holds"]), [answer["admission"]["attempt_id"]])
        self.assertEqual((resumed["earliest_reset"], resumed["completed"]), (ranked.earliest_reset, 1))
        self.assertEqual(resumed["baseline"], ranked.record["baseline"])

    def test_a_concurrent_run_from_another_cwd_hits_the_same_lock(self):
        _, _, ranked = self.started()
        refused = self.resume(ranked.record["digest"], cwd=self.private(self.tmp / "other-cwd"))
        self.assertIn("another ranked canary run", refused["refused"])

    def test_a_parent_alias_reaches_the_same_record_and_a_final_symlink_is_refused(self):
        _, _, ranked = self.started()
        canary_ranked.close(ranked)
        alias = self.tmp / "alias"
        alias.symlink_to(self.state["ledger"].parent.parent)  # .../q
        aliased = alias / self.state["ledger"].parent.name / self.state["ledger"].name
        resumed = self.resume(ranked.record["digest"], ledger=aliased)
        self.assertEqual(resumed["path"], str(self.record_path()))
        self.assertNotIn("refused", resumed)
        final = self.tmp / "ledger-link.sqlite3"
        final.symlink_to(self.state["ledger"])
        self.assertIn("symlink", self.resume(ranked.record["digest"], ledger=final)["refused"])
        for bad in (Path("relative/usage/organizations.sqlite3"), Path("/x.sqlite3")):
            self.assertIn("refused", self.resume(ranked.record["digest"], ledger=bad))
        self.assertEqual(self.records(), [self.record_path()])

    def test_real_default_resolver_uses_verified_home_without_creating_state(self):
        """Real safety and default-store resolvers; only the OS account's passwd entry is a private fixture."""
        alias = self.tmp / "home-alias"
        alias.symlink_to(self.home)
        script = ("import os, sys, types; sys.path.insert(0, %r); from unittest import mock; "
                  "from tests.qualification import canary_ranked_state as s, safety; "
                  "os.environ['HOME'] = sys.argv[1]; "
                  "mock.patch.object(safety.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_dir=%r)).start(); "
                  "print(s.resolve()['path'])") % (str(ROOT), str(self.home))
        found = []
        for home in (self.home, alias):
            done = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(home)], capture_output=True, text=True,
                                  timeout=60, env=dict(os.environ, HOME=str(home)), cwd=str(self.tmp))
            self.assertEqual(done.returncode, 0, done.stderr)
            found.append(done.stdout.strip())
        expected = str(self.record_path())
        self.assertEqual(found, [expected, expected])
        self.assertFalse((self.home / ".local/state/vibe-workflow-qualification").exists())  # pure: nothing created

    def test_a_new_process_resumes_after_seed_required_with_the_same_allowance_and_deadline(self):
        run, ledger, ranked = self.started()
        digest, deadline = ranked.record["digest"], ranked.earliest_reset
        result = self.drive(run, ledger, n=1, tag="l1")
        self.assertIsNone(result["abort"], result["abort"])
        values = json.loads(collector.window_path(self.settings).read_text())
        values["pools"][CLAUDE_POOL]["7d"]["observed_at"] = now() - collector.FRESH_SECONDS - 5
        collector.window_path(self.settings).write_text(json.dumps(values))
        stopped = self.drive(run, ledger, n=1, tag="l2")
        self.assertEqual(stopped["incomplete"]["reason"], "seed_required")
        canary_ranked.close(ranked)  # the process ends; the operator seeds, then a new process resumes
        resumed = self.resume(digest)
        self.assertEqual(resumed["state"]["cumulative"][CLAUDE_POOL + "/5h"], 20)
        self.assertEqual((resumed["earliest_reset"], resumed["completed"]), (deadline, 1))
        self.assertNotIn("stop", resumed)

    def test_a_changed_reset_after_resume_stays_incomplete(self):
        run, ledger, ranked = self.started()
        digest, deadline = ranked.record["digest"], ranked.earliest_reset
        canary_ranked.close(ranked)
        collector.store_window(self.settings, CLAUDE_POOL, dict(id="5h", limit=1000, used=10, length=18000,
                                                                reset_at=now() + 5 * 3600, rolling=False,
                                                                observed_at=now() + 1, unit=collector.WINDOW_UNIT))
        first = self.resume(digest, sync=True)
        self.assertEqual((first["stop"], first["earliest_reset"]), ("window_usage_unknown", deadline))
        again = self.resume(digest)  # a later process: the stop is durable, the deadline original
        self.assertEqual((again["stop"], again["earliest_reset"]), ("window_usage_unknown", deadline))

    def test_a_crash_with_an_outstanding_hold_keeps_its_exposure(self):
        run, _, ranked = self.started()
        ranked.before_prepare(self.body())
        answer, _ = __import__("tests.qualification.pilot_env", fromlist=["CanaryEnv"]).CanaryEnv(run).prepare(self.body())
        ranked.before_dispatch(answer["admission"])  # saved before any send; the process then dies
        canary_ranked.close(ranked)
        resumed = self.resume(ranked.record["digest"], reserve={"win:%s/5h" % CLAUDE_POOL: 90})
        self.assertEqual(list(resumed["state"]["holds"]), [answer["admission"]["attempt_id"]])
        self.assertEqual(resumed["stop"], "allowance_exhausted")  # the held 12 still counts

    def test_changed_inputs_under_the_same_consent_are_refused_and_nothing_is_erased(self):
        run, _, ranked = self.started()
        digest = ranked.record["digest"]
        canary_ranked.close(ranked)
        before = self.record_path().read_bytes()
        self.table(strategy("s-a", "invented-a"), version="tv-2")
        self.assertIn("approval_inputs_changed", self.resume(digest)["refused"])
        new = self.digest(run)
        self.assertIn("does not match", self.resume(new)["refused"])  # a new approval never replaces the record
        self.assertEqual(self.record_path().read_bytes(), before)

    def test_concurrent_missing_or_corrupt_records_are_refused(self):
        run, _, ranked = self.started()
        self.assertIn("another ranked canary run", self.resume(ranked.record["digest"])["refused"])
        canary_ranked.close(ranked)
        path = self.record_path()
        os.chmod(path, 0o644)
        self.assertIn("owner-only", self.resume(ranked.record["digest"])["refused"])
        os.chmod(path, 0o600)
        path.write_text("{}")
        self.assertIn("corrupt", self.resume(ranked.record["digest"])["refused"])

    def test_a_failed_save_aborts_before_any_send(self):
        _, _, ranked = self.started()
        with mock.patch.object(canary_ranked_state, "_write", side_effect=OSError("disk")):
            with self.assertRaises(canary.Abort) as caught:
                ranked.before_dispatch(dict(mode="ranked", billing_class="included_subscription",
                                            route=next(iter(ranked.pins["routes"]))))
        self.assertEqual(caught.exception.reason, "state_unsaved")


class CampaignTests(RankedStores):
    resume, record_path, records = LifecycleTests.resume, LifecycleTests.record_path, LifecycleTests.records

    def test_explicit_first_start_records_reference_but_never_renews_allowance(self):
        run, _ = self.make_run()
        approved = self.digest(run)
        first = self.resume(approved, acknowledge=8, start="history/decision-caf\u00e9")
        self.assertEqual(first["path"], str(self.record_path()))
        self.assertEqual(first["results"]["production_ledger"]["approval"]["ranked"]["history_decision_ref"],
                         "history/decision-caf\u00e9")
        self.assertEqual(set(first["record"]), {"version", "digest", "project", "ledger", "pins", "coverage",
                         "acknowledged", "baseline", "earliest_reset", "cumulative", "last", "seen", "holds",
                         "stopped", "completed"})  # reference adds no durable namespace
        value = first["record"]
        value["completed"] = 80
        value["cumulative"][CLAUDE_POOL + "/5h"] = 90
        value["holds"] = {"uncertain": {CLAUDE_POOL + "/5h": 12}}
        canary_ranked_state.save(self.record_path(), value)
        before = self.record_path().read_bytes()
        for ref, digest in (("history/new-decision", approved), ("history/newer", "f" * 64)):
            answer = self.resume(digest, acknowledge=8, start=ref)
            self.assertIn("already exists", answer["refused"])
        other_project = self.private(self.tmp / "another-project")
        run.canary_project = str(other_project)
        with self.assertRaisesRegex(safety.Refused, "already exists"):
            canary_ranked.setup(run, approved=approved, acknowledge=8, start="history/another-project")
        self.assertEqual(self.record_path().read_bytes(), before)
        resumed = self.resume(approved)
        self.assertEqual(resumed["record"], value)

    def test_unreviewed_or_blank_start_refuses_without_creation(self):
        run, _ = self.make_run()
        for reference in ("", " \n", True, 1):
            with self.assertRaises(safety.Refused):
                canary_ranked.setup(run, approved=self.digest(run), acknowledge=8, start=reference)
        run.canary_state = dict(run.canary_state, approval={})
        with self.assertRaisesRegex(safety.Refused, "approval"):
            canary_ranked.setup(run, approved=self.digest(run), acknowledge=8, start="history/reviewed")
        self.assertFalse(self.record_path().parent.exists())

    def test_two_initial_processes_share_one_lifetime_lock(self):
        run, _ = self.make_run()
        approved = self.digest(run)
        body = dict(settings=str(self.settings), ledger=str(self.state["ledger"]), project=str(self.project),
                    harnesses=[CLAUDE], digest=approved, home=str(self.home), account_home=str(self.home),
                    default_settings=str(self.settings), default_ledger=str(self.state["ledger"]),
                    start="history/first", acknowledge=8, pause_create=True)
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", RESUME, json.dumps(body)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=dict(os.environ, HOME=str(self.home)))
        try:
            self.assertEqual(json.loads(child.stdout.readline()), {"paused": True})
            lock = self.record_path().with_name("campaign.json.lock")
            identity, before = lock.stat().st_ino, lock.read_bytes()
            answer = self.resume(approved, acknowledge=8, start="history/second")
            self.assertIn("refused", answer)
            self.assertFalse(self.record_path().exists())
            self.assertEqual((lock.stat().st_ino, lock.read_bytes()), (identity, before))
            out, err = child.communicate("continue\n", timeout=60)
            self.assertEqual(child.returncode, 0, err)
            self.assertEqual(json.loads(out)["path"], str(self.record_path()))
            self.assertEqual(lock.stat().st_ino, identity)
            self.assertEqual(sorted(p.name for p in lock.parent.iterdir()), ["campaign.json", "campaign.json.lock"])
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=60)

    def test_legacy_or_ambiguous_evidence_refuses_untouched_in_both_modes(self):
        cases = ("run-old.json", "run-old.json.lock", ".run-old.json.tmp", ".campaign.json.tmp",
                 "campaign.json.lock", "campaign.json", "dangling", "multiple", "unknown")
        for index, kind in enumerate(cases):
            with self.subTest(kind=kind):
                root = self.private(self.tmp / ("case-%d" % index))
                self.build(root)
                run, _ = self.make_run(root)
                directory = self.private(self.record_path().parent)
                if kind == "dangling":
                    (directory / "campaign.json").symlink_to(directory / "absent")
                else:
                    for name in (("run-a.json", "run-b.json") if kind == "multiple" else (kind,)):
                        target = directory / name
                        target.write_bytes(b"retained old evidence")
                        target.chmod(0o600)
                def contents():
                    return {p.name: (os.readlink(p) if p.is_symlink() else p.read_bytes()) for p in directory.iterdir()}
                before = contents()
                for options in ({}, {"start": "history/decision"}):
                    answer = self.resume(self.digest(run), acknowledge=8, **options)
                    self.assertIn("refused", answer, answer)
                    self.assertEqual(contents(), before)

    def test_existing_singleton_does_not_hide_legacy_or_orphan_temp(self):
        run, _, ranked = LifecycleTests.started(self)
        canary_ranked.close(ranked)
        path = self.record_path()
        old = path.with_name("run-old.json")
        old.write_bytes(b"legacy")
        old.chmod(0o600)
        before = path.read_bytes()
        self.assertIn("refused", self.resume(ranked.record["digest"]))
        self.assertEqual((path.read_bytes(), old.read_bytes()), (before, b"legacy"))

    def test_unknown_history_requires_manual_reference_without_scanning(self):
        run, _ = self.make_run()
        arbitrary = self.private(self.tmp / "historical-unrelated-anchor") / "run-old.json"
        arbitrary.write_bytes(b"unknown history is outside automatic discovery")
        before = arbitrary.read_bytes()
        self.assertIn("refused", self.resume(self.digest(run), acknowledge=8))
        started = self.resume(self.digest(run), acknowledge=8, start="synthetic-manual-history-decision")
        self.assertEqual(started["completed"], 0)
        self.assertEqual(arbitrary.read_bytes(), before)

    def test_scope_is_checked_before_direct_preflight_or_run_effects(self):
        import types
        from tests.qualification import opencode_live
        run, _ = self.make_run()
        run.ranked_options = dict(approved=self.digest(run), acknowledge=8, start="history/decision")
        args = types.SimpleNamespace(approval=["test", "now", "ranked canary"], project=str(self.project),
                                     canary_allow=str(self.project), acknowledge_low_coverage=8)
        with mock.patch.object(safety, "check_real_home", side_effect=safety.Refused("wrong HOME")), \
                mock.patch.object(canary, "_production_paths", side_effect=AssertionError("store read")), \
                mock.patch.object(canary, "initialize_state", side_effect=AssertionError("state effect")), \
                mock.patch.object(opencode_live.Run, "start", side_effect=AssertionError("server effect")):
            for action in (lambda: canary_cli.preflight(args), run.canary_run,
                           lambda: canary_ranked.setup(run, approved="d" * 64)):
                with self.assertRaisesRegex(safety.Refused, "HOME"):
                    action()
        other = self.settings.with_name("other-settings.json")
        shutil.copyfile(self.settings, other)
        run.canary_state = dict(run.canary_state, settings=other)
        with self.assertRaisesRegex(safety.Refused, "default"):
            canary_ranked.setup(run, approved=self.digest(run), acknowledge=8, start="history/decision")
        self.assertFalse(self.record_path().parent.exists())


def spellings(path):
    """Alternative spellings of `path`'s parent component and final name: case and NFD (decomposed) forms."""
    import unicodedata
    parent, leaf = path.parent, path.name
    return dict(parent_case=parent.parent / parent.name.swapcase() / leaf, leaf_case=parent / leaf.swapcase(),
                parent_nfd=parent.parent / unicodedata.normalize("NFD", parent.name) / leaf,
                leaf_nfd=parent / unicodedata.normalize("NFD", leaf))


@unittest.skipUnless(shutil.which("git"), "git is required")
class SpellingTests(RankedStores):
    """N2-R1: a case or Unicode alias of the ledger's directory entries never reaches a second record or lock."""

    resume, record_path, records = LifecycleTests.resume, LifecycleTests.record_path, LifecycleTests.records

    def assert_aliases_share_authority(self, ledger, aliases, ranked, held):
        tried = 0
        before = self.record_path().read_bytes()
        for name, alias in aliases.items():
            if str(alias) == str(ledger) or not (alias.exists() and os.path.samefile(alias, ledger)):
                continue  # this spelling is not the same file here: not an alias on this volume
            tried += 1
            with self.subTest(alias=name, held=held):
                answer = self.resume(ranked.record["digest"], ledger=alias, acknowledge=8)
                if held:
                    self.assertIn("another ranked canary run", answer.get("refused", ""), answer)
                else:
                    self.assertEqual(answer["path"], str(self.record_path()))
                    self.assertEqual(answer["record"], ranked.record)
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.records(), [self.record_path()])
        return tried

    def test_case_aliases_share_the_lock_and_retained_accounting(self):
        run, _ = self.make_run()
        ranked = self.controller(run)
        aliases = {key: value for key, value in spellings(self.state["ledger"]).items() if key.endswith("case")}
        held = self.assert_aliases_share_authority(self.state["ledger"], aliases, ranked, True)
        canary_ranked.close(ranked)
        released = self.assert_aliases_share_authority(self.state["ledger"], aliases, ranked, False)
        if not held:
            self.skipTest("this volume is case-sensitive: case spellings are distinct files, not aliases")
        self.assertEqual((held, released), (2, 2))
        resumed = self.resume(ranked.record["digest"])
        self.assertEqual((resumed["path"], resumed["completed"]), (str(self.record_path()), 0))

    def test_unicode_aliases_share_authority_and_keep_exact_unicode_metadata(self):
        """Real NFC/NFD parent and leaf aliases exercise setup in new processes with one fixed account lock."""
        import unicodedata
        root = self.private(self.tmp / unicodedata.normalize("NFC", "ledger-caf\u00e9"))
        self.build(root)
        directory = self.state["ledger"].parent
        named = directory.parent / unicodedata.normalize("NFC", "us\u00e1ge")
        directory.rename(named)  # an exact NFC component on the ledger's own path
        leaf = unicodedata.normalize("NFC", "l\u00e9dger.sqlite3")
        for suffix in ("", "-wal", "-shm", "-journal"):  # the ledger and any sidecar keep matching names
            if (named / ("organizations.sqlite3" + suffix)).exists():
                (named / ("organizations.sqlite3" + suffix)).rename(named / (leaf + suffix))
        self.state = dict(self.state, ledger=named / leaf)
        self.routing = self.served_routing()
        run, _ = self.make_run(root)
        ranked = self.controller(run)
        exact = self.state["ledger"]
        self.assertEqual(ranked.record["ledger"], str(exact))
        aliases = {key: value for key, value in spellings(exact).items() if key.endswith("nfd")}
        self.assertEqual(set(aliases), {"parent_nfd", "leaf_nfd"})
        held = self.assert_aliases_share_authority(exact, aliases, ranked, True)
        canary_ranked.close(ranked)
        released = self.assert_aliases_share_authority(exact, aliases, ranked, False)
        before = self.record_path().read_bytes()
        resumed = self.resume(ranked.record["digest"])
        self.assertEqual((resumed["path"], resumed["completed"]), (str(self.record_path()), 0))
        self.assertNotIn("refused", resumed)
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.records(), [self.record_path()])
        if not held:
            self.skipTest("this volume keeps NFC and NFD names distinct: no Unicode alias exists here")
        self.assertEqual((held, released), (2, 2))

    def test_two_real_distinct_files_cannot_mint_a_second_campaign(self):
        """Both files physically exist on every filesystem; no invented directory listing or missing-file mock."""
        run, _, ranked = LifecycleTests.started(self)
        canary_ranked.close(ranked)
        other = self.state["ledger"].with_name("distinct.sqlite3")
        shutil.copyfile(self.state["ledger"], other)
        self.assertFalse(os.path.samefile(other, self.state["ledger"]))
        before = self.record_path().read_bytes()
        for options in ({}, {"start": "another-history-decision"}):
            answer = self.resume(self.digest(run), ledger=other, acknowledge=8, **options)
            self.assertIn("default", answer.get("refused", ""), answer)
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.records(), [self.record_path()])

    def test_physically_coexisting_case_names_do_not_mint_another_campaign(self):
        """Only on a case-sensitive volume; a case-insensitive one cannot hold both names (reported, not mocked)."""
        folder = self.private(self.tmp / "deep" / "a" / "b")
        upper, lower = folder / "Name.sqlite3", folder / "name.sqlite3"
        upper.write_bytes(b"")
        try:
            descriptor = os.open(lower, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self.skipTest("physical case-sensitive coexistence is unavailable on this volume")
        os.close(descriptor)
        with mock.patch.object(canary, "_production_paths", return_value=(self.settings, upper)):
            self.assertEqual(canary_ranked_state.resolve(upper)["ledger"], upper)
            with self.assertRaisesRegex(safety.Refused, "default"):
                canary_ranked_state.resolve(lower)

    def test_missing_unreadable_and_nonregular_ledgers_are_refused(self):
        folder = self.private(self.tmp / "deep" / "a" / "b")
        for bad in (folder / "missing.sqlite3", self.tmp / "deep" / "nowhere" / "b" / "x.sqlite3", folder):
            with self.assertRaises(safety.Refused):
                canary_ranked_state.resolve(bad)
        dangling = folder / "dangling.sqlite3"
        dangling.symlink_to(folder / "absent.sqlite3")
        with self.assertRaisesRegex(safety.Refused, "symlink"):
            canary_ranked_state.resolve(dangling)
        self.assertEqual(sorted(self.tmp.rglob("run-*.json")), [])

    def test_a_same_path_replacement_keeps_the_record_lock_and_allowance(self):
        run, ledger, ranked = LifecycleTests.started(self)
        self.assertIsNone(self.drive(run, ledger, n=1, tag="r1")["abort"])
        canary_ranked.close(ranked)
        before = self.record_path().read_bytes()
        copy = self.state["ledger"].with_name("replacement.sqlite3")
        shutil.copy2(self.state["ledger"], copy)
        os.replace(copy, self.state["ledger"])  # the same exact path, a new file
        collector.store_window(self.settings, CLAUDE_POOL, dict(id="5h", limit=1000, used=140, length=18000,
                                                                reset_at=self.resets["5h"], rolling=False,
                                                                observed_at=now() + 1, unit=collector.WINDOW_UNIT))
        fresh = self.digest(run)  # a strictly newer reading: a new preflight really differs
        self.assertNotEqual(fresh, ranked.record["digest"])
        resumed = self.resume(ranked.record["digest"])
        self.assertEqual((resumed["path"], resumed["completed"]), (str(self.record_path()), 1))
        self.assertEqual(resumed["state"]["cumulative"][CLAUDE_POOL + "/5h"], 20)
        self.assertEqual(resumed["baseline"], ranked.record["baseline"])
        self.assertEqual(resumed["earliest_reset"], ranked.earliest_reset)
        self.assertIn("does not match", self.resume(fresh, acknowledge=8)["refused"])  # no fresh run after replace
        self.assertEqual(self.record_path().read_bytes(), before)  # exact resume and refusal left it byte-identical
        self.assertEqual(self.records(), [self.record_path()])


@unittest.skipUnless(shutil.which("git"), "git is required")
class RecoveryTests(RankedStores):
    def test_a_refused_release_on_an_incomplete_stop_aborts_with_the_kill_switch(self):
        ranked = self.controller()
        ranked.before_prepare = lambda body: None

        def exhausted(admission):
            raise canary.Incomplete("allowance_exhausted", ["x"])
        ranked.before_dispatch = exhausted
        env = FakeEnv(dict(mode="ranked", billing_class="included_subscription"))
        real = env.__getattr__

        def refusing(name):
            if name == "record":
                return lambda *a, **k: (dict(state="blocked", reason="not_found"), 1.0)
            return real(name)
        env.__class__ = type("Refusing", (FakeEnv,), dict(__getattr__=lambda self, name: refusing(name)))
        calls = safety.CallLedger.initialize(self.private(self.tmp / "c") / "l.jsonl")
        calls.set_cap(canary.RANKED_PURPOSE, 4, "test", approval=dict(by="t", at="t", scope="t"))
        result = canary.PilotDriver(env, calls, n=1, ranked=ranked).run()
        self.assertEqual(result["abort"]["reason"], "recovery_error")
        self.assertTrue(result["abort"]["kill_switch_ok"])
        self.assertIsNone(result.get("incomplete"))

    def test_a_stop_in_telemetry_keeps_the_sent_launch_and_every_window(self):
        run, ledger = self.make_run()
        ranked = self.controller(run)
        run.server.native = dict(rateLimit=dict(status="allowed", rateLimitType="five_hour", resetsAt=now() + 7200,
                                                isUsingOverage=False, unifiedWindows=dict(
            five_hour=dict(utilization=0.15, resetsAt=now() + 7200, status="allowed"),  # a new 5h window: a reset
            seven_day=dict(utilization=0.13, resetsAt=self.resets["7d"], status="allowed"))))
        values = json.loads(collector.window_path(self.settings).read_text())
        for name in ("5h", "7d"):  # seeded 5 s earlier: the launch's readings are strictly newer, never a tie
            values["pools"][CLAUDE_POOL][name]["observed_at"] = now() - 5
        collector.window_path(self.settings).write_text(json.dumps(values))
        result = self.drive(run, ledger, n=1, tag="t1")
        self.assertEqual(result["incomplete"]["reason"], "window_usage_unknown")
        self.assertEqual((result["completed"], len(result["admissions"]), ranked.completed), (1, 1, 1))
        stored = {w["id"]: w for w in collector.LocalSource(self.settings).windows(CLAUDE_POOL, now())}
        self.assertEqual((stored["5h"]["used"], stored["7d"]["used"]), (150, 130))  # both stored before the stop


GUARDED = r"""
import sys
sys.path.insert(0, %r)
from unittest import mock
from tests.qualification import canary, canary_ranked, opencode_live, server
for target, name in ((canary, "_production_paths"), (canary, "resolve_state"), (canary, "initialize_state"),
                     (opencode_live, "CallLedger"), (opencode_live, "Run"), (server.Server, "start"),
                     (server.Routing, "served"), (canary_ranked, "preflight")):
    mock.patch.object(target, name, side_effect=AssertionError("side effect: " + name)).start()
raise SystemExit(opencode_live.main(sys.argv[1:]))
""" % str(ROOT)


@unittest.skipUnless(shutil.which("git"), "git is required")
class ProductionBoundaryTests(RankedStores):
    """N1: a production-ledger canary is the ranked one only; nothing implicit converts or bypasses it."""

    def test_an_unranked_production_canary_refuses_before_any_side_effect(self):
        iso = self.private(self.tmp / "iso-cli")
        calls = self.tmp / "calls-cli.jsonl"
        for extra in ([], ["--admissions", "20"], ["--pilot-max", "20"]):
            done = subprocess.run([sys.executable, "-I", "-B", "-c", GUARDED, "--canary", "--production-ledger",
                                   "--approval", "the user", "t", "canary", "--route", "claude-agent/invented-a#low",
                                   "--project", str(self.project), "--isolated-dir", str(iso), "--call-ledger",
                                   str(calls), *extra], capture_output=True, text=True, timeout=60,
                                  env=dict(os.environ, HOME=str(self.home)))
            self.assertEqual(done.returncode, 2, done.stderr)
            self.assertIn("production-ledger canary runs only with --ranked", done.stderr)
            self.assertNotIn("side effect", done.stderr)
        self.assertFalse(calls.exists())

    def test_a_direct_run_on_the_production_state_needs_its_ranked_options(self):
        run, _ = self.make_run()
        run.start = run.probes = mock.Mock(side_effect=AssertionError("started"))
        with mock.patch.object(canary, "check_ancestor_configs", side_effect=AssertionError("project read")), \
                mock.patch.object(canary_ranked, "setup", side_effect=AssertionError("setup")):
            for options in (None, {}):
                run.ranked_options = options
                with self.assertRaisesRegex(safety.Refused, "production-ledger canary runs only"):
                    run.canary_run()
        self.assertEqual(run.server.transmitted(), [])

    def test_a_dedicated_state_run_never_takes_ranked_options(self):
        run, _ = self.make_run()
        run.canary_state = dict(run.canary_state, production=False)
        run.ranked_options = dict(approved="d" * 64)
        with mock.patch.object(canary, "check_ancestor_configs", side_effect=AssertionError("project read")):
            with self.assertRaisesRegex(safety.Refused, "ranked"):
                run.canary_run()

    def test_the_export_and_the_preflight_keep_their_own_contracts(self):
        replay = self.tmp / "replay-export"
        done = RankedCliTests.cli(self, "--export-canary-admissions", "--production-ledger", "--approval", "the user",
                                  "t", "canary", "--replay-dir", str(replay))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["format"], 2)


class RankedCliTests(RankedStores):
    def cli(self, *argv, ledger=None, setup_only=False):
        """`opencode_live.py` in a new process; only the production defaults point at this test's stores."""
        script = ("import os, sys, types; sys.path.insert(0, %r); from unittest import mock; from tests.qualification import "
                  "canary, opencode_live, safety; os.environ['HOME'] = %r; "
                  "mock.patch.object(safety.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_dir=%r)).start(); "
                  "mock.patch.object(canary, '_production_paths', return_value=(%r, %r)).start();\n") % (
            str(ROOT), str(self.home), str(self.home), str(self.settings), str(ledger or self.state["ledger"]))
        if setup_only:
            script += """
import json
from tests.qualification import canary_ranked
mock.patch.object(opencode_live, "check_opencode_version").start()
def only_setup(run):
    controller = canary_ranked.setup(run, **run.ranked_options)
    canary_ranked.close(controller)
    print(json.dumps(run.results))
    raise SystemExit(0)
mock.patch.object(opencode_live.Run, "run", only_setup).start()
"""
        script += "raise SystemExit(opencode_live.main(sys.argv[1:]))"
        return subprocess.run([sys.executable, "-I", "-B", "-c", script, *argv], capture_output=True, text=True,
                              timeout=60, env=dict(os.environ, HOME=str(self.home)))

    def test_ranked_preflight_prints_the_digest_with_no_server(self):
        common = ["--project", str(self.project), "--canary-allow", str(self.project), "--approval", "the user", "t",
                  "ranked canary"]
        done = self.cli("--ranked-preflight", *common, "--acknowledge-low-coverage", "8")
        self.assertEqual(done.returncode, 0, done.stderr)
        record = json.loads(done.stdout)
        self.assertEqual(record["coverage"]["minimum"], 8)
        self.assertEqual(record["digest"], canary_ranked_state.digest(record))
        self.assertEqual(record["ledger"], str(canary_ranked_state.canonical(self.state["ledger"])))  # as setup binds it
        self.assertIn("acknowledge-low-coverage 8", self.cli("--ranked-preflight", *common).stderr)
        alias = spellings(self.state["ledger"])["parent_case"]
        if alias.exists() and os.path.samefile(alias, self.state["ledger"]):
            aliased = self.cli("--ranked-preflight", *common, "--acknowledge-low-coverage", "8", ledger=alias)
            self.assertEqual(aliased.returncode, 0, aliased.stderr)
            self.assertEqual(json.loads(aliased.stdout)["ledger"], str(canary_ranked_state.canonical(alias)))

    def test_start_reference_belongs_only_to_a_ranked_production_run(self):
        for flags in ([], ["--ranked-preflight"], ["--export-canary-admissions"]):
            done = self.cli(*flags, "--ranked-start", "history/decision")
            self.assertEqual(done.returncode, 2, done.stderr)
            self.assertIn("belongs only to the ranked run", done.stderr)
        done = self.cli("--canary", "--production-ledger", "--ranked", "--ranked-start", " ")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("nonblank", done.stderr)
        self.assertFalse(self.record_path().exists())

    record_path = LifecycleTests.record_path

    def test_cli_start_passes_preflight_digest_and_history_reference_to_setup(self):
        common = ["--project", str(self.project), "--canary-allow", str(self.project), "--approval", "test", "now",
                  "ranked canary", "--acknowledge-low-coverage", "8"]
        preflight = self.cli("--ranked-preflight", *common)
        self.assertEqual(preflight.returncode, 0, preflight.stderr)
        record = json.loads(preflight.stdout)
        calls = safety.CallLedger.initialize(self.private(self.tmp / "cli-calls") / "calls.jsonl")
        calls.set_cap(canary.RANKED_PURPOSE, 83, "synthetic", approval=dict(by="test", at="now", scope="canary"))
        result = self.cli("--canary", "--production-ledger", "--ranked", "--ranked-start", "history/cli-decision",
                          "--approve-digest", record["digest"], "--call-ledger", str(calls.path), "--probe-route",
                          "claude-agent/invented-a#low", "--isolated-dir", str(self.private(self.tmp / "cli-iso")),
                          "--replay-dir", str(self.tmp / "cli-export"), *common, setup_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(result.stdout)["production_ledger"]["approval"]["ranked"]
        self.assertEqual((metadata["digest"], metadata["history_decision_ref"]),
                         (record["digest"], "history/cli-decision"))
        self.assertEqual(json.loads(self.record_path().read_text())["ledger"], record["ledger"])
        self.assertEqual(calls.summary()[canary.RANKED_PURPOSE]["spent"], 0)

    def test_the_ranked_run_needs_its_own_opened_call_purpose(self):
        self.assertEqual((safety.CAPS[canary.RANKED_PURPOSE], safety.MAX_CAPS[canary.RANKED_PURPOSE]), (0, 83))
        self.assertEqual(canary_cli.exposure(canary.RANKED_MAX), 83)
        calls = safety.CallLedger.initialize(self.private(self.tmp / "calls") / "calls.jsonl")
        base = ["--canary", "--production-ledger", "--ranked", "--call-ledger", str(calls.path), "--project",
                str(self.project), "--approval", "the user", "t", "ranked canary", "--approve-digest", "d" * 64,
                "--probe-route", "claude-agent/invented-a#low"]
        done = self.cli(*base)
        self.assertEqual(done.returncode, 2)
        self.assertIn("ranked-canary call purpose has 0 of the 63", done.stderr)
        self.assertIn("no --route", self.cli(*base, "--route", "claude-agent/invented-a#low").stderr)
        self.assertIn("60 to 80", self.cli(*base, "--admissions", "20").stderr)
        self.assertEqual(calls.summary()["pilot"]["spent"], 0)  # the pilot grant is never used
        self.assertEqual(calls.summary()[canary.RANKED_PURPOSE]["cap"], 0)  # never opened by the harness
