"""Tripwire: the installed OpenCode must be the version the injection live check last verified.

Runs `opencode --version` only (no model call). When it fails, run the command in its message: the live check
proves agents still receive the injected notes and records the new version.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("live_check", ROOT / "scripts" / "live_check.py")
live_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_check)
MODELS = ["claude-agent/haiku"]


class OpenCodeVersionTest(unittest.TestCase):
    def test_installed_opencode_matches_the_live_verified_version(self):
        if shutil.which("opencode") is None:
            self.skipTest("opencode is not installed")
        # A throwaway HOME keeps OpenCode's own cache writes out of the suite's temporary home.
        with tempfile.TemporaryDirectory() as home:
            installed = subprocess.run(["opencode", "--version"], capture_output=True, text=True, timeout=30,
                                       env={**os.environ, "HOME": home}).stdout
        try:
            record = live_check.read_record(live_check.RECORD)
        except ValueError as error:
            self.fail(f"{error}; run `{live_check.INJECTION_COMMAND}`")
        problem = live_check.version_problem(installed, record)
        self.assertIsNone(problem, problem)

    def test_record_problems_fail_with_the_command(self):
        problem = live_check.version_problem("opencode v2.3.2\n", {"models": MODELS})
        self.assertIn(live_check.INJECTION_COMMAND, problem)

    def test_unparsable_version_fails_with_the_raw_output(self):
        problem = live_check.version_problem("garbage", {"version": "2.3.2", "models": MODELS})
        self.assertIn("garbage", problem)
        self.assertIn(live_check.INJECTION_COMMAND, problem)

    def test_a_different_version_names_both(self):
        problem = live_check.version_problem("opencode v2.4.0\n", {"version": "2.3.2", "models": MODELS})
        self.assertIn("2.4.0", problem)
        self.assertIn("2.3.2", problem)
        self.assertIsNone(live_check.version_problem("opencode v2.3.2\n", {"version": "2.3.2", "models": MODELS}))


if __name__ == "__main__":
    unittest.main()
