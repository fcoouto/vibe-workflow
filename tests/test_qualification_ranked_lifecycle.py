"""The ranked canary's whole-run lifecycle across real new processes (F7 correction F1-F5): the approved preflight
digest, the durable owner-only run record, resume after `seed_required`, after a crash with an outstanding hold, with
changed inputs under the same consent and after a changed reset; the ranked CLI branch and its closed call purpose;
and recovery that never weakens on an incomplete stop. Synthetic temporary stores and disposable call ledgers only."""
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
from tests.qualification import canary, canary_ranked
a = json.loads(sys.argv[1])
run = types.SimpleNamespace(canary_state=dict(settings=Path(a["settings"]), ledger=Path(a["ledger"]), production=True,
                                              approval={}), canary_project=a["project"], results={},
                            routing=types.SimpleNamespace(harnesses=a["harnesses"]))
try:
    c = canary_ranked.setup(run, approved=a["digest"], acknowledge=a.get("acknowledge"))
except Exception as error:
    print(json.dumps(dict(refused=str(error))))
    raise SystemExit(0)
out = dict(state=c.allowance.state(), earliest_reset=c.earliest_reset, completed=c.completed,
           baseline=c.allowance.baseline, path=str(c.path))
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
                    harnesses=[CLAUDE], digest=digest, **extra)
        done = subprocess.run([sys.executable, "-I", "-B", "-c", RESUME, json.dumps(body)], capture_output=True,
                              text=True, timeout=60, env=dict(os.environ, HOME=str(home or self.home)),
                              cwd=str(cwd or self.tmp))
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def record_path(self):
        return canary_ranked_state.path_for(self.state["ledger"])

    def records(self):
        """Every run record anywhere under this test's temporary root."""
        return sorted(self.tmp.rglob("run-*.json"))

    def test_approval_binds_the_preflight_digest_before_execution(self):
        run, _ = self.make_run()
        with self.assertRaisesRegex(safety.Refused, "--approve-digest does not match"):
            canary_ranked.setup(run, approved="0" * 64, acknowledge=8)
        approved = self.digest(run)
        self.table(strategy("s-a", "invented-a"), version="tv-2")  # changed after the operator approved
        with self.assertRaisesRegex(safety.Refused, "--approve-digest does not match"):
            canary_ranked.setup(run, approved=approved, acknowledge=8)
        self.assertEqual(self.records(), [])

    def test_the_record_location_has_no_override(self):
        import argparse
        import inspect
        parser = argparse.ArgumentParser()
        canary_cli.add_arguments(parser)
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            parser.parse_args(["--ranked-state-dir", str(self.tmp)])
        self.assertNotIn("state_dir", inspect.signature(canary_ranked.setup).parameters)
        self.assertEqual(list(inspect.signature(canary_ranked_state.path_for).parameters), ["ledger"])

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
        self.assertIn("does not match", self.resume(fresh, home=other_home, cwd=other_cwd)["refused"])
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.records(), [self.record_path()])  # exactly one record, at the ledger's anchor

    def test_another_home_resumes_the_same_record(self):
        run, ledger, ranked = self.started()
        self.assertIsNone(self.drive(run, ledger, n=1, tag="h2")["abort"])
        ranked.before_prepare(self.body(3))
        answer, _ = __import__("tests.qualification.pilot_env", fromlist=["CanaryEnv"]).CanaryEnv(run).prepare(self.body(3))
        ranked.before_dispatch(answer["admission"])  # an outstanding hold, saved before any send
        canary_ranked.close(ranked)
        resumed = self.resume(ranked.record["digest"], home=self.private(self.tmp / "other-home"),
                              cwd=self.private(self.tmp / "other-cwd"))
        self.assertEqual(resumed["path"], str(self.record_path()))
        self.assertEqual(resumed["state"]["cumulative"][CLAUDE_POOL + "/5h"], 20)
        self.assertEqual(list(resumed["state"]["holds"]), [answer["admission"]["attempt_id"]])
        self.assertEqual((resumed["earliest_reset"], resumed["completed"]), (ranked.earliest_reset, 1))
        self.assertEqual(resumed["baseline"], ranked.record["baseline"])

    def test_a_concurrent_run_from_another_home_hits_the_same_lock(self):
        _, _, ranked = self.started()
        refused = self.resume(ranked.record["digest"], home=self.private(self.tmp / "other-home"))
        self.assertIn("another ranked canary run", refused["refused"])

    def test_a_parent_alias_reaches_the_same_record_and_a_final_symlink_is_refused(self):
        _, _, ranked = self.started()
        canary_ranked.close(ranked)
        alias = self.tmp / "alias"
        alias.symlink_to(self.state["ledger"].parent.parent)  # .../q
        aliased = alias / self.state["ledger"].parent.name / self.state["ledger"].name
        self.assertEqual(canary_ranked_state.path_for(aliased), self.record_path())
        resumed = self.resume(ranked.record["digest"], ledger=aliased)
        self.assertEqual(resumed["path"], str(self.record_path()))
        self.assertNotIn("refused", resumed)
        final = self.tmp / "ledger-link.sqlite3"
        final.symlink_to(self.state["ledger"])
        with self.assertRaisesRegex(safety.Refused, "symlink"):
            canary_ranked_state.path_for(final)
        self.assertIn("symlink", self.resume(ranked.record["digest"], ledger=final)["refused"])
        for bad in (Path("relative/usage/organizations.sqlite3"), Path("/x.sqlite3")):
            with self.assertRaises(safety.Refused):
                canary_ranked_state.path_for(bad)
        self.assertEqual(self.records(), [self.record_path()])

    def test_the_production_ledger_maps_to_the_present_default_record_directory(self):
        """Pins the `parents[2]` depth and the full legacy record filename to the routing command's actual default
        ledger layout, on a real disposable ledger under a private HOME (and a symlink alias of that HOME)."""
        import hashlib
        import sqlite3
        ledger = self.home / ".local/state/vibe-workflow/usage/organizations.sqlite3"
        ledger.parent.mkdir(parents=True, mode=0o700)
        sqlite3.connect(ledger).close()  # a real, disposable ledger file: the spelling check needs an existing entry
        alias = self.tmp / "home-alias"
        alias.symlink_to(self.home)
        # The ledger is taken before `tests` is imported: importing it redirects HOME for the suite's own safety.
        script = ("import sys; sys.path.insert(0, %r); from scripts import routing; l = routing.default_ledger(); "
                  "from tests.qualification import canary_ranked_state as s; "
                  "print(s.path_for(l.parent.resolve() / l.name))") % str(ROOT)
        found = []
        for home in (self.home, alias):
            done = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True,
                                  timeout=60, env=dict(os.environ, HOME=str(home)), cwd=str(self.tmp))
            self.assertEqual(done.returncode, 0, done.stderr)
            found.append(done.stdout.strip())
        exact = self.home.resolve() / ".local/state/vibe-workflow/usage/organizations.sqlite3"
        expected = str(self.home.resolve() / ".local/state/vibe-workflow-qualification/ranked-canary" / (
            "run-%s.json" % hashlib.sha256(str(exact).encode()).hexdigest()[:16]))
        self.assertEqual(found, [expected, expected])  # the old full filename: no key or anchor change
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

    def alias_digest(self, run, alias):
        """The fresh approval an operator could compute for the alias spelling (setup's own preflight inputs)."""
        record = dict(canary_ranked.preflight(self.settings, run.routing.harnesses, now(), 8),
                      project=str(run.canary_project), ledger=str(alias.parent.resolve() / alias.name))
        return canary_ranked_state.digest(record)

    def assert_aliases_refused(self, ledger, aliases, ranked, run):
        tried = 0
        before = self.record_path().read_bytes()
        for name, alias in aliases.items():
            if str(alias) == str(ledger) or not (alias.exists() and os.path.samefile(alias, ledger)):
                continue  # this spelling is not the same file here: not an alias on this volume
            tried += 1
            for digest in (ranked.record["digest"], self.alias_digest(run, alias)):
                with self.subTest(alias=name):
                    answer = self.resume(digest, ledger=alias, acknowledge=8)
                    self.assertIn("exact filesystem spelling", answer.get("refused", ""), answer)
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.records(), [self.record_path()])
        return tried

    def test_case_aliases_refuse_while_the_lock_is_held_and_after_release(self):
        run, _ = self.make_run()
        ranked = self.controller(run)
        aliases = {key: value for key, value in spellings(self.state["ledger"]).items() if key.endswith("case")}
        held = self.assert_aliases_refused(self.state["ledger"], aliases, ranked, run)
        canary_ranked.close(ranked)
        released = self.assert_aliases_refused(self.state["ledger"], aliases, ranked, run)
        if not held:
            self.skipTest("this volume is case-sensitive: case spellings are distinct files, not aliases")
        self.assertEqual((held, released), (2, 2))
        resumed = self.resume(ranked.record["digest"])  # the exact spelling still resumes the one record
        self.assertEqual((resumed["path"], resumed["completed"]), (str(self.record_path()), 0))

    def test_unicode_aliases_refuse_and_the_exact_unicode_spelling_keeps_its_key(self):
        """Parent AND leaf NFD aliases of a real ledger whose directory and file names are exact NFC, through actual
        setup in new processes, while the lock is held and after release, with the old and a fresh alias digest."""
        import hashlib
        import unicodedata
        root = self.private(self.tmp / unicodedata.normalize("NFC", "ledger-caf\u00e9"))
        self.build(root)
        directory = self.state["ledger"].parent
        named = directory.parent / unicodedata.normalize("NFC", "can\u00e1ry")
        directory.rename(named)  # an exact NFC component on the ledger's own path
        leaf = unicodedata.normalize("NFC", "l\u00e9dger.sqlite3")
        for suffix in ("", "-wal", "-shm", "-journal"):  # the ledger and any sidecar keep matching names
            if (named / ("ledger.sqlite3" + suffix)).exists():
                (named / ("ledger.sqlite3" + suffix)).rename(named / (leaf + suffix))
        self.state = dict(self.state, dir=named, settings=named / "settings.json", ledger=named / leaf)
        self.settings = self.state["settings"]
        self.routing = self.served_routing()
        run, _ = self.make_run(root)
        ranked = self.controller(run)
        exact = self.state["ledger"]
        self.assertEqual(self.record_path().name,
                         "run-%s.json" % hashlib.sha256(str(exact).encode()).hexdigest()[:16])  # NFC key, as given
        aliases = {key: value for key, value in spellings(exact).items() if key.endswith("nfd")}
        self.assertEqual(set(aliases), {"parent_nfd", "leaf_nfd"})
        held = self.assert_aliases_refused(exact, aliases, ranked, run)
        canary_ranked.close(ranked)
        released = self.assert_aliases_refused(exact, aliases, ranked, run)
        before = self.record_path().read_bytes()
        resumed = self.resume(ranked.record["digest"])  # the exact NFC input is still accepted, under its old key
        self.assertEqual((resumed["path"], resumed["completed"]), (str(self.record_path()), 0))
        self.assertNotIn("refused", resumed)
        self.assertEqual(self.record_path().read_bytes(), before)
        self.assertEqual(self.records(), [self.record_path()])
        if not held:
            self.skipTest("this volume keeps NFC and NFD names distinct: no Unicode alias exists here")
        self.assertEqual((held, released), (2, 2))

    def test_a_leaf_unicode_alias_is_refused_by_the_anchor(self):
        import unicodedata
        folder = self.private(self.tmp / "deep" / "a" / "b")
        exact = folder / unicodedata.normalize("NFC", "l\u00e9dger.sqlite3")
        exact.write_bytes(b"")
        decomposed = folder / unicodedata.normalize("NFD", exact.name)
        canary_ranked_state.path_for(exact)  # the exact entry is accepted
        if not (decomposed.exists() and os.path.samefile(decomposed, exact)):
            self.skipTest("this volume keeps NFC and NFD names distinct: no Unicode alias exists here")
        with self.assertRaisesRegex(safety.Refused, "exact filesystem spelling"):
            canary_ranked_state.path_for(decomposed)

    def test_distinct_coexisting_entries_keep_their_own_keys(self):
        """The directory-entry contract, deterministic on any volume: two listed spellings are never merged."""
        folder = self.private(self.tmp / "deep" / "a" / "b")
        upper = folder / "Name.sqlite3"
        upper.write_bytes(b"")
        lower = folder / "name.sqlite3"
        listed = os.listdir
        coexisting = lambda path: listed(path) + (["name.sqlite3"] if Path(path) == folder else [])  # noqa: E731
        with mock.patch.object(canary_ranked_state.os, "listdir", side_effect=coexisting):
            keys = {canary_ranked_state.path_for(upper), canary_ranked_state.path_for(lower)}
        self.assertEqual(len(keys), 2)  # each keeps its exact-spelling key

    def test_physically_coexisting_case_names_keep_their_own_keys(self):
        """Only on a case-sensitive volume; a case-insensitive one cannot hold both names (reported, not mocked)."""
        folder = self.private(self.tmp / "deep" / "a" / "b")
        upper, lower = folder / "Name.sqlite3", folder / "name.sqlite3"
        upper.write_bytes(b"")
        try:
            descriptor = os.open(lower, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self.skipTest("physical case-sensitive coexistence is unavailable on this volume")
        os.close(descriptor)
        self.assertNotEqual(canary_ranked_state.path_for(upper), canary_ranked_state.path_for(lower))

    def test_missing_unreadable_and_nonregular_ledgers_are_refused(self):
        folder = self.private(self.tmp / "deep" / "a" / "b")
        for bad in (folder / "missing.sqlite3", self.tmp / "deep" / "nowhere" / "b" / "x.sqlite3", folder):
            with self.assertRaises(safety.Refused):
                canary_ranked_state.path_for(bad)
        dangling = folder / "dangling.sqlite3"
        dangling.symlink_to(folder / "absent.sqlite3")
        with self.assertRaisesRegex(safety.Refused, "symlink"):
            canary_ranked_state.path_for(dangling)
        hidden = self.private(self.tmp / "deep" / "hidden" / "c")
        (hidden / "x.sqlite3").write_bytes(b"")
        os.chmod(hidden, 0o300)  # traversable, not listable: the spelling cannot be verified
        try:
            with self.assertRaisesRegex(safety.Refused, "cannot be verified"):
                canary_ranked_state.path_for(hidden / "x.sqlite3")
        finally:
            os.chmod(hidden, 0o700)
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
    def cli(self, *argv, ledger=None):
        """`opencode_live.py` in a new process; only the production defaults point at this test's stores."""
        script = ("import sys; sys.path.insert(0, %r); from unittest import mock; from tests.qualification import "
                  "canary, opencode_live; mock.patch.object(canary, '_production_paths', return_value=(%r, %r)).start();"
                  " raise SystemExit(opencode_live.main(sys.argv[1:]))") % (
            str(ROOT), str(self.settings), str(ledger or self.state["ledger"]))
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
            refused = self.cli("--ranked-preflight", *common, "--acknowledge-low-coverage", "8", ledger=alias)
            self.assertEqual(refused.returncode, 2, refused.stdout)
            self.assertIn("exact filesystem spelling", refused.stderr)

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
