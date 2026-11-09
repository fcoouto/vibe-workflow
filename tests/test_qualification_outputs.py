"""Round-3 review: the results and skeleton writing (double redaction pass, leak-checked skeleton with a minimal
fallback, hardened withheld file, --out outside every repository) and the stricter user-directory rules (secret
names, exact sensitive names in checkouts and packages, deletions, created stores, this run's own ids in new names).
New module (the 1,000-line rule)."""
import io
import json
import os
from pathlib import Path
import shutil
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tests.qualification import chatgpt, outputs, safety, sanitize, user_dirs
from tests.qualification import opencode_live as live
from tests.qualification.doubles import Temp
from tests.test_qualification_probes import StageHarness, approved_ledger
from tests.test_qualification_results import EndToEndTests, touch

REPO = Path(__file__).resolve().parents[1]


def fake_run(output, run_id="chatgpt-out"):
    class FakeRun:
        def __init__(self, *args, **kwargs):
            self.redact, self.run_id = safety.Redactor(), run_id

        def run(self):
            return dict(blockers=[])

        def output(self, results):
            return self.redact(dict(output))
    return FakeRun


class WritingTests(Temp):
    def main(self, output, out=None, run_id="chatgpt-out"):
        ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        if not ledger_path.exists():
            approved_ledger(ledger_path)
        out = out or self.tmp / ("out-%s.json" % run_id)
        base = ["--isolated-dir", str(self.private(self.tmp / ("iso-" + run_id))), "--call-ledger", str(ledger_path),
                "--chatgpt", "--chatgpt-model", "openai/gpt-5.5", "--out", str(out)]
        printed = io.StringIO()
        with patch.object(live, "Run", fake_run(output, run_id)), patch.object(live, "check_opencode_version", lambda env: None), \
                patch.object(live, "check_real_home", lambda: None), redirect_stdout(printed), redirect_stderr(io.StringIO()):
            code = live.main(base)
        return code, out, printed.getvalue(), ledger_path

    def test_a_secret_learned_late_in_the_pass_is_still_removed(self):
        code, out, printed, _ = self.main({"blockers": ["failed with tok-SECRETVALUE"], "z": {"access": "tok-SECRETVALUE"}})
        self.assertEqual(code, 1)  # a blocker, written normally
        self.assertNotIn("SECRETVALUE", out.read_text())

    def test_a_skeleton_that_would_leak_becomes_the_fixed_minimal_record(self):
        code, out, printed, ledger_path = self.main({"blockers": ["x"], "note": "alice@example.com",
                                                    "cleanup": dict(verified_by_harness=True),
                                                    "call_ledger": {"bob@example.com": dict(cap=1, spent=0)}}, run_id="leaky")
        self.assertEqual(code, 3)
        written = json.loads(out.read_text())
        self.assertEqual(sorted(written), ["blockers_count", "call_ledger", "cleanup", "results_withheld"])
        self.assertEqual(written["blockers_count"], 1)
        self.assertEqual(safety.Redactor().leaks(out.read_text()), [])
        kept = ledger_path.parent / "withheld" / "leaky.json"
        self.assertTrue(kept.is_file())

    def test_the_withheld_directory_is_never_a_symlink_or_inside_a_repository(self):
        target = self.private(self.tmp / "elsewhere")
        state = self.private(self.tmp / "state2")
        os.symlink(target, state / "withheld")
        with self.assertRaises(safety.Refused):
            outputs.withhold("{}", state / "calls.jsonl", "r1")
        self.assertEqual(list(target.iterdir()), [])
        with self.assertRaises(safety.Refused):
            outputs.withhold("{}", REPO / "tests" / "calls.jsonl", "r2")
        self.assertFalse((REPO / "tests" / "withheld").exists())

    def test_the_withheld_file_is_never_followed_or_overwritten(self):
        state = self.private(self.tmp / "state3")
        (state / "withheld").mkdir(mode=0o700)
        os.symlink(self.tmp / "victim", state / "withheld" / "r3.json")
        with self.assertRaises(OSError):
            outputs.withhold("{}", state / "calls.jsonl", "r3")
        self.assertFalse((self.tmp / "victim").exists())

    def test_out_inside_a_repository_is_refused_for_live_runs(self):
        for out in (REPO / "results.json", REPO / "tests" / "fixtures" / "x.json"):
            with self.subTest(out=out):
                code, _, _, _ = self.main({"blockers": []}, out=out, run_id="repo")
                self.assertEqual(code, 2)
                self.assertFalse(out.exists())


class StricterDirTests(Temp):
    def classify(self, policy, change, existing=(), remove=None, own_ids=()):
        root = self.private(self.tmp / ("d-%d" % len(list(self.tmp.iterdir()))))
        for name in existing:
            touch(root / name)
        before = safety.snapshot_tree(root)
        if remove:
            target = root / remove
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        if change:
            touch(root / change)
        return user_dirs.classify_user_dir(policy, before, safety.snapshot_tree(root), root, own_ids)

    def test_secret_names_fail_even_in_checkouts(self):
        stores = ("worktree/x/README.md", "storage/x", "log/x")
        for name in ("worktree/x/.opencode/auth.json", "worktree/h/repo/.env", "worktree/h/repo/mcp-auth.json",
                     "worktree/h/repo/deploy/key.pem", "storage/secrets.json", "storage/key.pem", "storage/apikey.txt",
                     "log/secret.log"):
            with self.subTest(name=name):
                self.assertTrue(self.classify("data", name, stores)[0], name)
        for name in ("worktree/h/repo/src/config.ts", "worktree/h/repo/tokens.py", "worktree/h/repo/author.md",
                     "log/author.log", "storage/monkey.json"):
            with self.subTest(name=name):
                self.assertEqual(self.classify("data", name, stores)[0], [], name)

    def test_package_names_in_the_cache_are_not_secrets(self):
        for name in ("npm/node_modules/js-tokens/index.js", "npm/node_modules/@scope/auth-lib/package.json",
                     "packages/node_modules/keyv/src/index.js"):
            with self.subTest(name=name):
                self.assertEqual(self.classify("cache", name)[0], [], name)
        for name in ("npm/node_modules/x/.env", "npm/node_modules/x/auth.json", "auth/token.json", "secrets.db"):
            with self.subTest(name=name):
                self.assertTrue(self.classify("cache", name)[0], name)

    def test_deletions_of_the_db_a_store_or_a_snapshot_fail(self):
        existing = ("opencode.db", "snapshot/h1/objects/ab/cd", "log/a.log", "storage/s/x.json")
        for remove in ("opencode.db", "snapshot/h1", "snapshot", "storage"):
            with self.subTest(remove=remove):
                self.assertTrue(self.classify("data", None, existing, remove=remove)[0], remove)
        self.assertEqual(self.classify("data", None, existing, remove="log/a.log")[0], [])

    def test_repos_and_stores_created_during_the_run_are_counted(self):
        for name in ("repos/r/file.txt", "tool-output/t.txt", "log/new.log"):
            with self.subTest(name=name):
                unexpected, churn = self.classify("data", name, ("opencode.db",))
                self.assertEqual(unexpected, [], name)
                self.assertTrue(churn)

    def test_new_names_with_this_runs_ids_fail_without_naming_them(self):
        ids = ("ses_0123456789abcdef0123456789abcdef", "msg_fedcba9876543210fedcba9876543210")
        for name in ("storage/session/%s.json" % ids[0], "log/%s.log" % ids[1], "snapshot/x/%s" % ids[0]):
            with self.subTest(name=name):
                unexpected, _ = self.classify("data", name, ("storage/x", "log/x", "snapshot/y"), own_ids=ids)
                self.assertEqual(unexpected, [user_dirs.OWN_ID_NAME])
        self.assertTrue(self.classify("cache", "x/%s" % ids[0], own_ids=ids)[0])


class OwnIdsEndToEndTests(StageHarness):
    whole_run = EndToEndTests.whole_run

    def test_the_isolated_servers_ids_in_the_real_stores_fail_the_run(self):
        original = chatgpt.request

        def request_then_leak(run):
            original(run)
            touch(Path.home() / ".local/share/opencode/storage/session" / ("%s.json" % sorted(run.own_ids)[0]))
        with patch.object(chatgpt, "request", request_then_leak):
            run, results, text = self.whole_run()
        self.assertIn("the user's .local/share/opencode changed during the run", results["blockers"])
        self.assertTrue(run.own_ids)
        for own in run.own_ids:
            self.assertNotIn(own, text)
        self.assertEqual(run.redact.leaks(text), [])

    def test_isolated_store_is_recorded_on_the_timeout_path(self):
        run, ledger, server = self.probe_run(purpose="chatgpt", model="openai/gpt-test-mini#low")
        server.oauth_pending_polls = 0
        with redirect_stdout(io.StringIO()):
            chatgpt.connect(run, run.base / "chatgpt-login.json", timeout_s=10, poll_s=0, sleep=lambda s: None)
        chatgpt.select_model(run)
        run.wait_idle = lambda *a, **k: (_ for _ in ()).throw(safety.Blocker("session did not become idle"))
        with self.assertRaises(safety.Blocker):
            chatgpt.request(run)
        stage = run.results["stages"]["chatgpt"]
        self.assertEqual(stage["isolated_store"], dict(data_dir=True, session_store=True))
        self.assertTrue(run.own_ids)
