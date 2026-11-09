"""The whole results file the ChatGPT run would write, end to end with the stand-in server: the harness's own output
never trips its leak check (also with concurrent OpenCode sessions writing to the user's directories), the always-safe
`request_outcome_summary`, the withheld local copy when the leak check refuses, the per-directory classification of
concurrent-session churn, the preflight budget message and the recorded `chatgpt` cap maximum."""
import io
import json
import os
from pathlib import Path
import shutil
import stat
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tests.qualification import chatgpt, safety, sanitize
from tests.qualification import opencode_live as live
from tests.qualification.doubles import Temp
from tests.test_qualification_probes import UPSTREAM, StageHarness, approved_ledger

HASH = "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    stamp = path.stat().st_mtime_ns + 5_000_000_000
    os.utime(path, ns=(stamp, stamp))


def concurrent_sessions(home):
    """What other running OpenCode sessions write while the harness runs (seen around attempt 4)."""
    data = home / ".local/share/opencode"
    for name in ("log/opencode-2026.log", "snapshot/%s/objects/ab/%s" % (HASH, HASH[2:]), "worktree/%s/repo/README.md" % HASH,
                 "storage/session/ses_%s.json" % HASH[:26], "shell/%s/sh_%s.out" % (HASH[:7], HASH[:12]), "opencode.db-wal"):
        touch(data / name)
    touch(home / (".cache/opencode/models/%s.json" % HASH))
    touch(home / (".local/state/opencode/history/%s.jsonl" % HASH))


class EndToEndTests(StageHarness):
    def whole_run(self, during=None, **knobs):
        for name in ("iso", "state"):
            shutil.rmtree(self.tmp / name, ignore_errors=True)
        home = Path.home()
        for name in ("log", "snapshot", "worktree", "storage", "shell"):
            (home / ".local/share/opencode" / name).mkdir(parents=True, exist_ok=True)
        run, ledger, server = self.probe_run(purpose="chatgpt", model="openai/gpt-test-mini#low", **knobs)
        server.oauth_pending_polls = 0
        run.chatgpt_timeout = 60
        run.start = lambda stage: during(home) if during else None
        run.cleanup = lambda: run.results.__setitem__("cleanup", dict(verified_by_harness=True, processes_left=0))
        with redirect_stdout(io.StringIO()):
            results = run.run()
        text = json.dumps(run.output(results), indent=2, sort_keys=True)
        return run, results, text

    def test_the_harness_output_never_trips_its_own_leak_check(self):
        for case, knobs in (("upstream error", dict(upstream_error=UPSTREAM)), ("no reply", dict(no_reply=True)),
                            ("success", {})):
            with self.subTest(case=case):
                run, results, text = self.whole_run(during=concurrent_sessions, **knobs)
                self.assertEqual(run.redact.leaks(text), [], case)
                self.assertEqual(results["blockers"], [], case)
                entry = results["user_dirs"]["opencode"][".local/share/opencode"]
                self.assertEqual(entry["unexpected_count"], 0)
                self.assertGreaterEqual(entry["concurrent_session_churn"]["snapshot"], 1)  # 4 on the first run, then 1
                self.assertNotIn(HASH, text)
                self.assertEqual(results["stages"]["chatgpt"]["isolated_store"], dict(data_dir=True, session_store=True))

    def test_strict_changes_still_fail_and_their_names_are_masked(self):
        def credentials(home):
            touch(home / ".local/share/opencode/auth.json")
            touch(home / (".local/share/opencode/%s.json" % HASH))
        run, results, text = self.whole_run(during=credentials)
        self.assertIn("the user's .local/share/opencode changed during the run", results["blockers"])
        entry = results["user_dirs"]["opencode"][".local/share/opencode"]
        self.assertEqual(entry["unexpected_count"], 2)
        self.assertIn("auth.json", entry["unexpected"])
        self.assertEqual(run.redact.leaks(text), [])

    def test_the_outcome_summary_is_built_from_enumerations_only(self):
        expected = {"upstream error": dict(assistant_messages=1, assistant_with_tokens=0, assistant_with_error=1,
                                           finish=["error"], error_type=["APIError"], end_reason="idle"),
                    "no reply": dict(assistant_messages=0, assistant_with_tokens=0, assistant_with_error=0, finish=[],
                                     error_type=[], end_reason="idle"),
                    "success": dict(assistant_messages=1, assistant_with_tokens=1, assistant_with_error=0, finish=["stop"],
                                    error_type=[], end_reason="idle")}
        for case, knobs in (("upstream error", dict(upstream_error=UPSTREAM)), ("no reply", dict(no_reply=True)),
                            ("success", {})):
            with self.subTest(case=case):
                run, results, text = self.whole_run(**knobs)
                summary = results["stages"]["chatgpt"]["request_outcome_summary"]
                self.assertEqual(summary["transmitted_count"], 1)
                for key, value in expected[case].items():
                    self.assertEqual(summary[key], value, key)
                self.assertIn(summary["idle_outcome"], ("succeeded", "failed", "interrupted", None))
                self.assertTrue(type(summary["wait_seconds"]) in (int, float))
                strings = [v for v in summary.values() if isinstance(v, str)] + \
                    [x for v in summary.values() if isinstance(v, list) for x in v]
                allowed = set(chatgpt.FINISH) | set(sanitize.KNOWN_CODES) | set(sanitize.ERROR_CLASSES) | \
                    set(chatgpt.STATUS_TYPES) | set(chatgpt.END_REASONS) | {"<other>", "succeeded", "failed", "interrupted"}
                self.assertTrue(set(strings) <= allowed, strings)

    def test_the_summary_survives_withheld_facts(self):
        with patch.object(chatgpt, "session_facts", lambda *a: dict(facts_withheld=["facts_error:TypeError"])):
            run, results, text = self.whole_run(no_reply=True)
        stage = results["stages"]["chatgpt"]
        self.assertEqual(stage["session_facts"], dict(facts_withheld=["facts_error:TypeError"]))
        self.assertEqual(stage["request_outcome_summary"]["assistant_messages"], 0)


class WithheldFileTests(Temp):
    def test_a_refusal_keeps_the_full_results_locally_and_the_skeleton_names_paths_only(self):
        ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        approved_ledger(ledger_path)
        out = self.tmp / "out.json"
        base = ["--isolated-dir", str(self.private(self.tmp / "ok")), "--call-ledger", str(ledger_path),
                "--chatgpt", "--chatgpt-model", "openai/gpt-5.5", "--out", str(out)]

        class FakeRun:
            run_id = "chatgpt-0a1b2c3d"

            def __init__(self, *args, **kwargs):
                self.redact = safety.Redactor()

            def run(self):
                return dict(blockers=[])

            def output(self, results):
                return dict(results, cleanup=dict(verified_by_harness=True), call_ledger=dict(chatgpt=dict(cap=4, spent=2)),
                            stages=dict(chatgpt=dict(session_facts=dict(assistant=[dict(note="alice@example.com")]))))
        printed = io.StringIO()
        with patch.object(live, "Run", FakeRun), patch.object(live, "check_opencode_version", lambda env: None), \
                patch.object(live, "check_real_home", lambda: None), redirect_stdout(printed), redirect_stderr(io.StringIO()):
            self.assertEqual(live.main(base), 3)
        kept = ledger_path.parent / "withheld" / "chatgpt-0a1b2c3d.json"
        self.assertIn(str(kept), printed.getvalue())
        self.assertEqual(stat.S_IMODE(kept.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(kept.parent.stat().st_mode), 0o700)
        self.assertIn("alice@example.com", kept.read_text())  # the operator judges it; it is never committed
        skeleton = json.loads(out.read_text())
        self.assertEqual(skeleton["flagged"], [dict(path="/stages/chatgpt/session_facts/assistant[0]/note", kind="email")])
        self.assertNotIn("alice", out.read_text())
        self.assertEqual(sanitize.leaks(out.read_text()), [])


class UserDirPolicyTests(Temp):
    def classify(self, policy, change, existing=()):
        root = self.private(self.tmp / ("d-%d" % len(list(self.tmp.iterdir()))))
        for name in existing:
            touch(root / name)
        before = safety.snapshot_tree(root)
        touch(root / change)
        unexpected, churn = safety.classify_user_dir(policy, before, safety.snapshot_tree(root), root)
        return unexpected, churn

    def test_data_dir(self):
        stores = ("log/x", "snapshot/x", "worktree/x", "storage/x", "shell/x", "tool-output/x")
        for name in ("log/opencode.log", "snapshot/%s/objects/ab/cd" % HASH, "worktree/w/src/config.ts",
                     "worktree/w/auth/token.py", "storage/session/s.json", "shell/p/sh_1.out", "tool-output/t.txt",
                     "opencode.db", "opencode.db-wal"):
            with self.subTest(name=name):
                unexpected, churn = self.classify("data", name, stores)
                self.assertEqual(unexpected, [], name)
                self.assertEqual(sum(churn.values()) > 0, True)
        for name in ("auth.json", "mcp-auth.json", "settings.json", "other.jsonc", "newdir/x", "loose-file",
                     "storage/auth/x.json", "log/token.txt", "snapshot/credentials", "storage/keychain.db"):
            with self.subTest(name=name):
                unexpected, _ = self.classify("data", name, stores)
                self.assertTrue(unexpected, name)

    def test_cache_and_state_dirs(self):
        for policy, name in (("cache", "models/x.json"), ("cache", "bin/opencode-pty/x"), ("state", "history/h.jsonl"),
                             ("state", "pty/run/sock"), ("state", "kv.json")):
            with self.subTest(policy=policy, name=name):
                self.assertEqual(self.classify(policy, name)[0], [], name)
        for policy, name in (("cache", "auth/x"), ("cache", "token.json"), ("cache", "credentials.db"), ("state", "auth.json"),
                             ("state", "oauth-token"), ("state", "config.json")):
            with self.subTest(policy=policy, name=name):
                self.assertTrue(self.classify(policy, name)[0], name)

    def test_the_config_dir_stays_strict(self):
        self.assertTrue(self.classify("strict", "opencode.json")[0])
        self.assertTrue(self.classify("strict", "plugins/x.ts")[0])


class BudgetAndCapTests(StageHarness):
    def test_the_preflight_refusal_says_what_is_needed(self):
        run, ledger, server = self.probe_run(purpose="chatgpt", model="openai/gpt-test-mini#low")
        for _ in range(2):
            ledger.settle(ledger.reserve("chatgpt", "spent", 1, run="x"), 1)
        server.oauth_pending_polls = 0
        run.start = lambda stage: None
        with self.assertRaises(safety.Refused) as caught, redirect_stdout(io.StringIO()):
            chatgpt.run(run)
        message = str(caught.exception)
        for part in ("2 remain", "3 needed", "--set-cap chatgpt", "--approval BY AT SCOPE", "user's approval"):
            self.assertIn(part, message)

    def test_the_chatgpt_maximum_is_six_and_the_coordinator_command_works(self):
        path = self.private(self.tmp / "dry") / "calls.jsonl"
        ledger = approved_ledger(path)
        for _ in range(2):
            ledger.settle(ledger.reserve("chatgpt", "attempt", 1, run="x"), 1)
        with self.assertRaises(safety.Refused):
            ledger.set_cap("chatgpt", 7, "too much", approval=dict(by="u", at="t", scope="s"))
        command = ["--call-ledger", str(path), "--set-cap", "chatgpt", "6", "--reason",
                   "attempts 3 and 4 spent 1 request each without a usable diagnosis; the run needs 3 remaining",
                   "--approval", "Fernando Couto (user)", "2026-11-10T00:05:00+01:00",
                   "ChatGPT-via-OpenCode qualification, one tiny OpenAI model"]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(live.main(command[:5]), 2)  # no --reason / --approval: refused
            self.assertEqual(live.main(command), 0)
        self.assertEqual(safety.CallLedger(path).summary()["chatgpt"], dict(cap=6, spent=2, remaining=4, open_reservations=0))
        cap = [e for e in safety.CallLedger(path).export()["entries"] if e["kind"] == "cap"][-1]
        self.assertEqual(cap["approval"]["by"], "Fernando Couto (user)")
