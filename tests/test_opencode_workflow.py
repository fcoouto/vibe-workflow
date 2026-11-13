"""Run the OpenCode escalation gate's `node --test` suites and check its defaults against the feature manifest."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from runtime import features

ROOT = Path(__file__).resolve().parents[1]
SUITES = [ROOT / "tests/opencode_workflow/gate.test.ts", ROOT / "tests/opencode_workflow/plugin.test.ts",
          ROOT / "tests/opencode_workflow/adversarial.test.ts"]
DEFAULTS = ("import('./runtime/adapters/opencode_workflow/gate.ts')"
            ".then((m) => console.log(JSON.stringify(m.DEFAULT_SETTINGS)))")


class OpenCodeWorkflowTests(unittest.TestCase):
    def node(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; the escalation gate's node --test suites were not run")
        return node

    def test_node_suites(self):
        result = subprocess.run([self.node(), "--test", "--test-reporter=tap", "--test-timeout=60000",
                                 *map(str, SUITES)], cwd=ROOT, capture_output=True, text=True, timeout=170)
        self.assertEqual(result.returncode, 0, result.stdout[-6000:] + result.stderr[-2000:])
        self.assertIn("# fail 0", result.stdout)
        self.assertIn("# skipped 0", result.stdout)

    def test_gate_defaults_match_the_manifest(self):
        result = subprocess.run([self.node(), "--input-type=module", "-e", DEFAULTS], cwd=ROOT, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        defaults = json.loads(result.stdout)
        settings = features.manifests()["escalation_gate"]["settings"]
        self.assertEqual(settings["check_patterns"]["default"], defaults["checkPatterns"])
        self.assertEqual(settings["ladder"]["default"], defaults["ladder"])


if __name__ == "__main__":
    unittest.main()
