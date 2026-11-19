"""Exercise the read-only workspace check with injected probes, never the network."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/vibe-prepare-my-workspace/scripts/check.py"


def load():
    spec = importlib.util.spec_from_file_location("workspace_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.check = load()

    def test_parse_version_takes_the_numeric_prefix(self):
        parse = self.check.parse_version
        self.assertEqual(parse("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse("rtk 1.1.0"), (1, 1, 0))
        self.assertEqual(parse("git version 2.39.5 (Apple Git-154)"), (2, 39, 5))
        self.assertEqual(parse("1.0.0-rc1"), (1, 0, 0))
        with self.assertRaises(ValueError):
            parse("none")

    def test_compare_pads_and_orders_numerically(self):
        compare = self.check.compare
        self.assertEqual(compare("1.2", "1.2.0"), 0)
        self.assertEqual(compare("1.10.0", "1.9.9"), 1)
        self.assertEqual(compare("1.9.9", "1.10.0"), -1)

    def test_exit_code_prefers_action_over_unknown(self):
        code = self.check.exit_code
        self.assertEqual(code(["ready"]), 0)
        self.assertEqual(code(["ready", "unknown"]), 2)
        self.assertEqual(code(["outdated", "unknown"]), 1)
        self.assertEqual(code(["missing"]), 1)
        self.assertEqual(code(["not-supported", "ready"]), 0)

    def test_worst_state_precedence(self):
        self.assertEqual(self.check.worst(["ready", "unknown", "missing", "outdated"]), "missing")
        self.assertEqual(self.check.worst(["ready", "unknown"]), "unknown")


if __name__ == "__main__":
    unittest.main()
