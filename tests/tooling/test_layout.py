import os
import pwd
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMAND = "python3 -B -m unittest discover -s tests -t ."


class DiscoveryTest(unittest.TestCase):
    def test_filtered_discovery_without_a_top_level_dir_is_refused(self):
        # test_routing.py is pure logic, so the unrefused run is harmless; it still passes or fails on its own merits.
        done = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_routing.py"],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(done.returncode, 0, done.stderr)
        self.assertIn("-t .", done.stderr)

    def test_home_is_not_the_real_home(self):
        real = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        self.assertNotEqual(os.path.realpath(os.environ["HOME"]), real, f"tests ran against the real home; run: {COMMAND}")


if __name__ == "__main__":
    unittest.main()
