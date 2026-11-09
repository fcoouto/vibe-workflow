import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import tempfile
import unittest

from scripts import test_sharded


SCRIPT = Path(test_sharded.__file__).resolve()

PASSING = """import unittest


class Passing(unittest.TestCase):
    def test_one(self):
        self.assertTrue(True)

    def test_two(self):
        self.assertEqual(2, 1 + 1)
"""

FAILING = """import unittest


class Failing(unittest.TestCase):
    def test_broken(self):
        self.assertEqual(1, 2)

    def test_fine(self):
        pass
"""

SKIPPED = """import unittest


class Skipped(unittest.TestCase):
    @unittest.skip("not here")
    def test_skipped(self):
        pass

    def test_fine(self):
        pass
"""

CRASHING = """import os

os._exit(3)
"""

# Distinct sizes give a known balancing order: pass, fail, skip, crash.
MODULES = {"test_pass": (PASSING, 900), "test_fail": (FAILING, 700),
           "test_skip": (SKIPPED, 500), "test_crash": (CRASHING, 300)}


def padded(source, size):
    filler = size - len(source) - 3
    return source + "#" + "x" * filler + "\n\n"


class ShardedRunnerTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="sharded-runner-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.workspace)
        self.root = self.workspace / "repo"
        tests = self.root / "tests"
        tests.mkdir(parents=True)
        (tests / "__init__.py").write_text("")
        (tests / "helper.py").write_text("")
        for name, (source, size) in MODULES.items():
            (tests / f"{name}.py").write_text(padded(source, size))
        self.created = self.snapshot()

    def snapshot(self):
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))

    def run_script(self, *args, log_dir=True, env=None):
        command = [sys.executable, "-B", str(SCRIPT), "--root", str(self.root)]
        if log_dir:
            self.log_dir = self.workspace / "logs"
            command += ["--log-dir", str(self.log_dir)]
        result = subprocess.run(command + list(args), capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(self.created, self.snapshot(), "runner wrote inside the root")
        return result

    def test_discovers_test_modules_with_sizes(self):
        sizes = test_sharded.discover(self.root)
        self.assertEqual(["test_crash", "test_fail", "test_pass", "test_skip"], list(sizes))
        self.assertEqual(900, sizes["test_pass"])

    def test_selects_by_name_dotted_name_and_glob(self):
        modules = list(test_sharded.discover(self.root))
        self.assertEqual(["test_fail", "test_pass"],
                         test_sharded.select(modules, ["tests.test_pass", "test_f*"]))
        self.assertEqual(modules, test_sharded.select(modules, []))
        with self.assertRaises(test_sharded.UsageError):
            test_sharded.select(modules, ["test_missing*"])

    def test_balances_largest_first_onto_lightest_shard(self):
        sizes = {"a": 5, "b": 4, "c": 3, "d": 3, "e": 1}
        assignment, loads = test_sharded.balance(sizes, 2)
        self.assertEqual([["a", "d"], ["b", "c", "e"]], assignment)
        self.assertEqual([8, 8], loads)
        self.assertEqual(1, test_sharded.shard_count(None, 1))
        self.assertEqual(2, test_sharded.shard_count(8, 2))
        with self.assertRaises(test_sharded.UsageError):
            test_sharded.shard_count(0, 3)

    def test_list_prints_deterministic_assignment_without_running(self):
        first = self.run_script("--shards", "2", "--list", log_dir=False)
        second = self.run_script("--shards", "2", "--list", log_dir=False)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual([
            "shard 1 (2 modules, 1200 bytes): tests.test_crash tests.test_pass",
            "shard 2 (2 modules, 1200 bytes): tests.test_fail tests.test_skip",
        ], first.stdout.splitlines())

    def test_all_passing_exits_zero_and_reports_skips(self):
        result = self.run_script("--shards", "2", "test_pass", "test_skip")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("total: 2 modules, 4 tests, 1 skipped", result.stdout)
        self.assertIn(f"Logs: {self.log_dir}", result.stdout)
        self.assertEqual(["shard-1.log", "shard-2.log"], sorted(p.name for p in self.log_dir.iterdir()))

    def test_failure_exits_one_and_names_failing_test_with_log(self):
        result = self.run_script("--shards", "2", "test_pass", "test_fail")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        line = next(line for line in result.stdout.splitlines() if "FAIL: test_broken" in line)
        self.assertIn("Failing.test_broken", line)
        self.assertIn(str(self.log_dir / "shard-"), line)
        self.assertIn("python3 -B -m unittest tests.<module>", result.stdout)

    def test_crashed_shard_is_reported_with_log_tail(self):
        result = self.run_script("--shards", "2", "test_crash", "test_pass")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("no Ran line", result.stdout)
        self.assertIn("FAILED without a Ran line (exit 3)", result.stdout)
        self.assertIn("2 tests", result.stdout)

    def test_unmatched_selection_is_a_usage_error(self):
        result = self.run_script("test_missing")
        self.assertEqual(2, result.returncode)
        self.assertIn("no test module matches 'test_missing'", result.stderr)
        self.assertFalse(self.log_dir.exists())

    def test_refuses_log_dir_inside_root_and_existing_logs(self):
        inside = self.run_script("--log-dir", str(self.root / "logs"), "test_pass")
        self.assertEqual(2, inside.returncode)
        self.assertIn("inside the root", inside.stderr)
        self.assertEqual(0, self.run_script("test_pass").returncode)
        again = self.run_script("test_pass")
        self.assertEqual(2, again.returncode)
        self.assertIn("not overwriting", again.stderr)

    def test_refuses_a_temp_directory_inside_the_root_without_creating_it(self):
        inside = self.root / "scratch"
        inside.mkdir()
        self.created = self.snapshot()
        result = self.run_script(log_dir=False, env={**os.environ, "TMPDIR": str(inside)})
        self.assertEqual(2, result.returncode)
        self.assertIn("inside the root", result.stderr)
        self.assertEqual([], list(inside.iterdir()))

    def test_sigterm_stops_the_shards(self):
        pid_file = self.workspace / "child.pid"
        sleeper = (f"import os, time, unittest\n\nclass Sleeper(unittest.TestCase):\n"
                   f"    def test_sleep(self):\n        open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                   f"        time.sleep(120)\n")
        (self.root / "tests" / "test_sleep.py").write_text(sleeper)
        self.created = self.snapshot()
        runner = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "--root", str(self.root), "--shards", "1",
                                   "--log-dir", str(self.workspace / "logs"), "test_sleep"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(runner.kill)
        self.wait_until(pid_file.exists)
        child = int(pid_file.read_text())

        def kill_child():
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.addCleanup(kill_child)
        runner.send_signal(signal.SIGTERM)
        self.assertEqual(128 + signal.SIGTERM, runner.wait(timeout=30))
        self.wait_until(lambda: not self.alive(child))

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def wait_until(self, condition, seconds=30):
        deadline = time.monotonic() + seconds
        while not condition():
            self.assertLess(time.monotonic(), deadline, "condition not reached in time")
            time.sleep(0.05)


if __name__ == "__main__":
    unittest.main()
