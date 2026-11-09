"""The live qualification harness's pure parts: refusals, the call budget, redaction and the recorder guard.

No server is started; tests/qualification/opencode_live.py itself is an operator tool and is not collected.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.qualification import opencode_live as live


class ModelTests(unittest.TestCase):
    def test_only_claude_agent_haiku_is_accepted(self):
        self.assertEqual(live.check_model("claude-agent/haiku"), "claude-agent/haiku#low")
        self.assertEqual(live.check_model("claude-agent/haiku#low"), "claude-agent/haiku#low")
        for ref in ("claude-agent/sonnet", "claude-agent/haiku#high", "openai/gpt-5", "chatgpt/gpt-5", "opencode/big-pickle",
                    "openrouter/anthropic/claude-haiku", "claude-agent/haiku ", "", None, ["claude-agent/haiku"]):
            with self.assertRaises(live.Refused, msg=repr(ref)):
                live.check_model(ref)

    def test_recorder_guard_names_only_the_qualification_route(self):
        source = live.recorder_source("post")
        self.assertIn('m.providerID !== "claude-agent" || m.id !== "haiku"', source)
        self.assertIn("guard_budget", source)
        self.assertIn("guard_title", source)
        self.assertNotIn("__", source.replace("__dirname", ""))
        # Values never reach the log: only whether the probe variable exists.
        self.assertNotIn("env[", source)


class IsolatedDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.home = self.tmp / "home"
        self.home.mkdir()

    def private(self, path):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
        return path

    def test_accepts_an_empty_private_directory(self):
        target = self.private(self.tmp / "q")
        self.assertEqual(live.check_isolated_dir(str(target), self.home), target.resolve())

    def test_refusals(self):
        missing = self.tmp / "missing"
        loose = self.tmp / "loose"
        loose.mkdir()
        loose.chmod(0o755)
        full = self.private(self.tmp / "full")
        (full / "x").write_text("")
        link = self.tmp / "link"
        link.symlink_to(self.private(self.tmp / "real"))
        cases = [None, "", "relative/dir", str(missing), str(loose), str(full), str(link), str(self.home),
                 str(self.private(self.home / ".config/opencode")), str(self.private(self.home / ".local/share/opencode/x")),
                 str(self.private(self.home / ".local/state/opencode"))]
        for case in cases:
            with self.assertRaises(live.Refused, msg=repr(case)):
                live.check_isolated_dir(case, self.home)

    def test_main_refuses_before_starting_anything(self):
        with self.assertRaises(SystemExit):
            live.main(["--budget", "1"])  # no --isolated-dir
        loose = self.tmp / "loose2"
        loose.mkdir()
        self.assertEqual(live.main(["--isolated-dir", str(loose), "--budget", "1"]), 2)
        target = self.private(self.tmp / "ok")
        self.assertEqual(live.main(["--isolated-dir", str(target), "--budget", "1", "--model", "claude-agent/sonnet"]), 2)
        self.assertEqual(live.main(["--isolated-dir", str(target), "--budget", "15"]), 2)
        self.assertEqual(list(target.iterdir()), [])


class BudgetTests(unittest.TestCase):
    def test_spend_counts_and_refuses_past_the_limit(self):
        budget = live.CallBudget(3)
        self.assertEqual(budget.spend("1", "a", 1), 2)
        self.assertEqual(budget.spend("2", "blocked", 0), 2)
        self.assertTrue(budget.allow(2))
        self.assertFalse(budget.allow(3))
        with self.assertRaises(live.BudgetExceeded):
            budget.spend("2", "b", 3)
        self.assertEqual(budget.used, 4)  # the overrun is recorded, flagged, never hidden
        self.assertTrue(budget.entries[-1]["over_budget"])
        with self.assertRaises(ValueError):
            budget.spend("2", "c", -1)

    def test_limit_is_bounded_by_the_authorized_maximum(self):
        for limit in (-1, 15, 1.5, "3", None):
            with self.assertRaises(live.Refused, msg=repr(limit)):
                live.CallBudget(limit)
        self.assertEqual(live.CallBudget(14).remaining, 14)
        self.assertEqual(live.CallBudget(0).remaining, 0)


class RedactionTests(unittest.TestCase):
    def test_secrets_paths_ids_and_names_are_removed(self):
        token = "Abc_def-0123456789abcdefghijklmnopqrstuvwxy"
        redact = live.Redactor(secrets=[token, "pw-secret"], paths=["/private/tmp/q1", "/Users/someone"], names=["someone"])
        payload = {"authority": token, "log": "server password pw-secret at /private/tmp/q1/data",
                   "ses_0123456789abcdef0123456789abcdef": ["msg_1bf0000000000AaBbCcDdEeFfG", "ses_0123456789abcdef0123456789abcdef"],
                   "owner": "someone", "home": "/Users/someone/.claude", "kind": "title", "n": 3}
        clean = redact(payload)
        text = json.dumps(clean)
        self.assertEqual(redact.leaks(text), [])
        self.assertNotIn(token, text)
        self.assertNotIn("pw-secret", text)
        self.assertIn('"<ses#1>": ["<msg#1>", "<ses#1>"]', text)  # stable placeholders
        self.assertEqual(clean["kind"], "title")
        self.assertEqual(clean["n"], 3)
        self.assertEqual(clean["owner"], "<user>")

    def test_leaks_reports_unsanitized_text(self):
        redact = live.Redactor(secrets=["tok-123"], paths=["/tmp/x"], names=["alice"])
        self.assertEqual(len(redact.leaks("tok-123 /tmp/x ses_ABCDEF123456 alice")), 4)
        self.assertEqual(redact.leaks("<ses#1> <secret> <path> malice"), [])


class ShapeTests(unittest.TestCase):
    def test_id_shape_never_returns_the_value(self):
        derived = "ses_" + "0a" * 16
        shape = live.id_shape(derived)
        self.assertEqual(shape, dict(prefix="ses", body_length=32, classes=["digit", "lower"], lower_hex_only=True, derived_shape=True))
        own = live.id_shape("ses_1bf0000000000AaBbCcDdEeFfG")
        self.assertFalse(own["derived_shape"])
        self.assertEqual(own["body_length"], 26)
        self.assertIn("upper", own["classes"])
        self.assertNotIn("1bf0000000000AaBbCcDdEeFfG", json.dumps(own))
        self.assertFalse(live.id_shape(None)["derived_shape"])

    def test_latency_pairs_match_pre_and_post_per_hook_and_session(self):
        records = [dict(phase="pre", hook="model.request", session="s1", kind="primary", t=1.0),
                   dict(phase="pre", hook="model.request", session="s2", kind="primary", t=2.0),
                   dict(phase="post", hook="model.request", session="s1", kind="primary", t=4.5)]
        pairs, blocked = live.latency_pairs(records)
        self.assertEqual(pairs, [dict(hook="model.request", kind="primary", ms=3.5)])
        self.assertEqual([r["session"] for r in blocked], ["s2"])
        self.assertEqual(live.summarize_ms([3.0, 1.0, 2.0])["median_ms"], 2.0)
        self.assertIsNone(live.summarize_ms([]))


if __name__ == "__main__":
    unittest.main()
