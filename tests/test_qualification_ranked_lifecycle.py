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
    c = canary_ranked.setup(run, approved=a["digest"], state_dir=a["state_dir"])
except Exception as error:
    print(json.dumps(dict(refused=str(error))))
    raise SystemExit(0)
out = dict(state=c.allowance.state(), earliest_reset=c.earliest_reset, completed=c.completed)
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
        self.state_dir = self.private(self.tmp / "ranked-state")
        ranked = self.controller(run, state_dir=self.state_dir)
        return run, ledger, ranked

    def resume(self, digest, **extra):
        """A new process: `setup` with the approved digest on the same stores and record directory."""
        body = dict(settings=str(self.settings), ledger=str(self.state["ledger"]), project=str(self.project),
                    harnesses=[CLAUDE], digest=digest, state_dir=str(self.state_dir), **extra)
        done = subprocess.run([sys.executable, "-I", "-B", "-c", RESUME, json.dumps(body)], capture_output=True,
                              text=True, timeout=60, env=dict(os.environ, HOME=str(self.home)))
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def record_path(self):
        return canary_ranked_state.path_for(self.state_dir, self.state["ledger"])

    def test_approval_binds_the_preflight_digest_before_execution(self):
        run, _ = self.make_run()
        state_dir = self.private(self.tmp / "ranked-state")
        with self.assertRaisesRegex(safety.Refused, "--approve-digest does not match"):
            canary_ranked.setup(run, approved="0" * 64, acknowledge=8, state_dir=state_dir)
        approved = self.digest(run)
        self.table(strategy("s-a", "invented-a"), version="tv-2")  # changed after the operator approved
        with self.assertRaisesRegex(safety.Refused, "--approve-digest does not match"):
            canary_ranked.setup(run, approved=approved, acknowledge=8, state_dir=state_dir)
        self.assertEqual(list(state_dir.iterdir()) and [p.name for p in state_dir.iterdir() if p.suffix == ".json"], [])

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
    def cli(self, *argv):
        """`opencode_live.py` in a new process; only the production defaults point at this test's stores."""
        script = ("import sys; sys.path.insert(0, %r); from unittest import mock; from tests.qualification import "
                  "canary, opencode_live; mock.patch.object(canary, '_production_paths', return_value=(%r, %r)).start();"
                  " raise SystemExit(opencode_live.main(sys.argv[1:]))") % (
            str(ROOT), str(self.settings), str(self.state["ledger"]))
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
        self.assertIn("acknowledge-low-coverage 8", self.cli("--ranked-preflight", *common).stderr)

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
