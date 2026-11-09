import json
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

# Each test creates its marker exclusively, so a test that runs twice errors out.
MARKING = """import os
from pathlib import Path
import unittest


def mark(name):
    open(Path(os.environ["MARK_DIR"]) / name, "x").close()


def setUpModule():
    mark("{module}.setUpModule." + str(os.getpid()))


class First(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ready = True

    @classmethod
    def tearDownClass(cls):
        mark("{module}.First.tearDownClass." + str(os.getpid()))

    def test_heavy(self):
        mark("{module}.First.test_heavy")

    def test_light(self):
        self.assertTrue(self.ready)
        mark("{module}.First.test_light")

    def test_other(self):
        self.assertTrue(self.ready)
        mark("{module}.First.test_other")


class Second(unittest.TestCase):
    def test_alone(self):
        mark("{module}.Second.test_alone")
"""

# Records its environment, optionally waits for a peer unit, then writes under the default home location.
HOME_WRITING = """import json
import os
from pathlib import Path
import time
import unittest


class HomeWriting(unittest.TestCase):
    def test_home(self):
        home = Path.home()
        record = {{"home": str(home), "real": os.path.realpath(home), "home_env": os.environ.get("HOME"),
                  "vcw": os.environ.get("VCW_TEST_HOME"), "codex": os.environ.get("CODEX_HOME"),
                  "xdg": [os.environ.get(name) for name in
                          ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")],
                  "share": (home / ".local" / "share").is_dir(),
                  "share_entries": os.listdir(home / ".local" / "share")}}
        marks = Path(os.environ["MARK_DIR"])
        (marks / "{module}.json").write_text(json.dumps(record))
        if os.environ.get("WAIT_FOR_PEER"):
            deadline = time.monotonic() + 20
            while len(list(marks.glob("*.json"))) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
        if {write}:
            (home / ".config").mkdir()
            (home / ".config" / "tool.txt").write_text("x")
"""

MARKED_TESTS = ["First.test_heavy", "First.test_light", "First.test_other", "Second.test_alone"]

# Distinct sizes give a known planning order: pass, fail, skip, crash.
MODULES = {"test_pass": (PASSING, 900), "test_fail": (FAILING, 700),
           "test_skip": (SKIPPED, 500), "test_crash": (CRASHING, 300)}


def padded(source, size):
    filler = size - len(source) - 3
    return source + "#" + "x" * filler + "\n\n"


def timings(modules=None, tests=None, slow=()):
    return {"version": 1, "modules": modules or {}, "tests": tests or {}, "slow_modules": list(slow)}


def sleeper(pid_file):
    return (f"import os, time, unittest\n\nclass Sleeper(unittest.TestCase):\n"
            f"    def test_sleep(self):\n        open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
            f"        time.sleep(120)\n")


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

    def add_module(self, name, source):
        (self.root / "tests" / f"{name}.py").write_text(source)
        self.created = self.snapshot()

    def write_timings(self, data):
        path = self.workspace / "timings.json"
        path.write_text(json.dumps(data))
        return path

    def run_script(self, *args, log_dir="logs", env=None):
        command = [sys.executable, "-B", str(SCRIPT), "--root", str(self.root)]
        if log_dir:
            self.log_dir = self.workspace / log_dir
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

    def test_simulates_longest_first_on_the_earliest_free_worker(self):
        units = [test_sharded.unit(name, name, cost) for name, cost in
                 [("a", 5), ("b", 4), ("c", 3), ("d", 3), ("e", 1)]]
        placements, wall, critical = test_sharded.simulate(units, 2)
        self.assertEqual([("a", 1, 0, 5), ("b", 2, 0, 4), ("c", 2, 4, 7), ("d", 1, 5, 8), ("e", 2, 7, 8)],
                         [(item["name"], worker, start, end) for item, worker, start, end in placements])
        self.assertEqual((8, "d"), (wall, critical["name"]))
        self.assertEqual(1, test_sharded.worker_count(None, 1))
        self.assertEqual(2, test_sharded.worker_count(8, 2))
        with self.assertRaises(test_sharded.UsageError):
            test_sharded.worker_count(0, 3)

    def test_plan_splits_heavy_tests_and_estimates_unknown_modules_by_size(self):
        sizes = {"test_a": 1000, "test_b": 2000, "test_c": 500}
        data = timings({"test_a": 100.0, "test_b": 10.0},
                       {"test_a.K.test_big": 60.0, "test_a.K.test_mid": 20.0, "test_a.K.test_small": 14.0,
                        "test_ab.K.test_prefix": 50.0})
        units = test_sharded.plan_units(["test_a", "test_b", "test_c"], sizes, data)
        summary = [(item["name"], round(item["cost"], 6), item["test"], item["exclude"]) for item in units]
        # Median seconds per byte of known modules: (0.1 + 0.005) / 2 = 0.0525.
        self.assertEqual([
            ("test_a.K.test_big", 60.0, "test_a.K.test_big", []),
            ("test_c", 26.25, None, []),
            ("test_a (rest)", 20.0, None, ["test_a.K.test_big", "test_a.K.test_mid"]),
            ("test_a.K.test_mid", 20.0, "test_a.K.test_mid", []),
            ("test_b", 10.0, None, []),
        ], summary)
        fallback = test_sharded.plan_units(["test_c"], sizes, None)
        self.assertEqual(500 * test_sharded.DEFAULT_SECONDS_PER_BYTE, fallback[0]["cost"])

    def test_list_prints_deterministic_plan_without_running(self):
        first = self.run_script("--workers", "2", "--list", log_dir=False)
        second = self.run_script("--workers", "2", "--list", log_dir=False)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        lines = first.stdout.splitlines()
        self.assertIn("no timings file at", lines[0])
        self.assertEqual("Plan: 2 workers, 4 units, 4 modules; costs from file sizes", lines[1])
        self.assertEqual(["test_pass", "test_fail", "test_skip", "test_crash"],
                         [line.split()[-1] for line in lines[2:6]])
        self.assertIn("worker  2      1.8-     3.0s  test_skip", lines[4])
        self.assertEqual("Predicted wall: 3.0s (critical unit: test_skip)", lines[6])

    def test_list_shows_heavy_units_from_a_timings_file(self):
        path = self.write_timings(timings({"test_pass": 40.0}, {"test_pass.Passing.test_one": 30.0}))
        result = self.run_script("--workers", "1", "--list", "--timings", str(path), "test_pass", log_dir=False)
        self.assertEqual(0, result.returncode, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(f"Plan: 1 workers, 2 units, 1 modules; costs from {path}", lines[0])
        self.assertIn("30.0s", lines[1])
        self.assertTrue(lines[1].endswith("test_pass.Passing.test_one"))
        self.assertTrue(lines[2].endswith("test_pass (rest)"))
        self.assertEqual("Predicted wall: 40.0s (critical unit: test_pass (rest))", lines[3])

    def test_corrupt_or_old_timings_fall_back_to_sizes(self):
        for content in ["{not json", json.dumps({"version": 0, "modules": {}})]:
            with self.subTest(content=content):
                path = self.workspace / "timings.json"
                path.write_text(content)
                result = self.run_script("--list", "--timings", str(path), log_dir=False)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("estimating unit costs from file sizes", result.stdout)
                self.assertIn("costs from file sizes", result.stdout)

    def test_every_test_runs_exactly_once_across_split_units(self):
        self.add_module("test_mark", MARKING.replace("{module}", "test_mark"))
        self.add_module("test_unknown", MARKING.replace("{module}", "test_unknown"))
        heavy, stale = "test_mark.First.test_heavy", "test_mark.First.test_gone"
        path = self.write_timings(timings({"test_mark": 50.0}, {heavy: 20.0, stale: 30.0}))
        marks = self.workspace / "marks"
        marks.mkdir()
        result = self.run_script("--workers", "3", "--timings", str(path), "test_mark", "test_unknown",
                                 env={**os.environ, "MARK_DIR": str(marks)})
        output = result.stdout + result.stderr
        self.assertEqual(0, result.returncode, output)
        self.assertIn("units: 4", output)
        self.assertIn("total: 2 modules, 8 tests, 0 skipped", output)
        names = sorted(path.name for path in marks.iterdir())
        for module in ["test_mark", "test_unknown"]:
            for test in MARKED_TESTS:
                self.assertEqual(1, names.count(f"{module}.{test}"), test)
        # The heavy and rest units each run the module fixture; the stale unit runs no tests.
        self.assertEqual(2, sum(name.startswith("test_mark.setUpModule.") for name in names))
        self.assertEqual(2, sum(name.startswith("test_mark.First.tearDownClass.") for name in names))
        self.assertEqual(1, sum(name.startswith("test_unknown.First.tearDownClass.") for name in names))
        self.assertIn(f"Stale timing entries (no such test; refresh with --update-timings):\n  {stale}",
                      result.stdout)
        logs = sorted(path.name for path in self.log_dir.iterdir())
        # test_unknown is a slightly larger file, estimated at test_mark's 50 s per its size.
        self.assertEqual(["unit-001-test_unknown.log", "unit-002-test_mark.First.test_gone.log",
                          "unit-003-test_mark.First.test_heavy.log", "unit-004-test_mark-rest.log"], logs)

    def test_fast_skips_slow_modules_with_a_banner(self):
        path = self.write_timings(timings({"test_fail": 9.0, "test_pass": 1.0}, slow=["test_fail", "test_gone"]))
        listed = self.run_script("--fast", "--list", "--timings", str(path), log_dir=False)
        self.assertEqual(0, listed.returncode, listed.stderr)
        self.assertIn("FAST TIER: a development aid, NOT a release gate.", listed.stdout)
        self.assertIn("Skipping 1 slow module(s)", listed.stdout)
        self.assertIn("3 units, 3 modules", listed.stdout)
        self.assertNotIn("test_fail", listed.stdout.split("Plan:")[1])
        ran = self.run_script("--fast", "--timings", str(path), "test_pass", "test_fail")
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
        self.assertIn("total: 1 modules, 2 tests", ran.stdout)
        self.assertIn("NOT a release gate", ran.stdout.split("Logs:")[1])
        empty = self.run_script("--fast", "--timings", str(path), "test_fail")
        self.assertEqual(2, empty.returncode)
        self.assertIn("--fast left no modules", empty.stderr)
        unknown = self.run_script("--fast", "--list", log_dir=False)
        self.assertIn("every module counts as fast", unknown.stdout)
        self.assertIn("4 units", unknown.stdout)

    def test_merge_timings_keeps_heavy_tests_and_slow_modules(self):
        previous = timings({"test_a": 5.0, "test_old": 1.0, "test_gone": 2.0},
                           {"test_a.K.test_x": 20.0, "test_old.K.test_y": 16.0}, ["test_a", "test_old"])
        measured = {"test_a": (12.34, {"test_a.K.test_x": 2.0, "test_a.K.test_z": 3.0}),
                    "test_new": (40.0, {"test_new.K.test_w": 15.0, "test_new.K.test_v": 0.1})}
        merged = test_sharded.merge_timings(previous, measured, {"test_a", "test_old", "test_new"})
        self.assertEqual(timings({"test_a": 12.3, "test_new": 40.0, "test_old": 1.0},
                                 {"test_new.K.test_w": 15.0, "test_old.K.test_y": 16.0},
                                 ["test_a", "test_new", "test_old"]), merged)
        quick = test_sharded.merge_timings(None, {"test_q": (1.0, {"test_q.K.test_u": 2.9})}, {"test_q"})
        self.assertEqual(timings({"test_q": 1.0}), quick)

    def test_update_timings_writes_measurements_and_keeps_failed_modules(self):
        path = self.write_timings(timings({"test_fail": 99.0, "test_crash": 77.0}, {"test_fail.Failing.x": 50.0},
                                          ["test_fail"]))
        result = self.run_script("--update-timings", "--timings", str(path),
                                 "test_pass", "test_fail", "test_crash", "test_skip")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("kept previous timings for 2 module(s) that failed or crashed: test_crash test_fail",
                      result.stdout)
        text = path.read_text()
        data = json.loads(text)
        self.assertEqual(json.dumps(data, indent=1, sort_keys=True) + "\n", text)
        self.assertEqual({"version", "modules", "tests", "slow_modules"}, set(data))
        self.assertEqual((99.0, 77.0), (data["modules"]["test_fail"], data["modules"]["test_crash"]))
        self.assertEqual({"test_fail.Failing.x": 50.0}, data["tests"])
        self.assertEqual(["test_fail"], data["slow_modules"])
        self.assertGreater(data["modules"]["test_pass"], 0)
        self.assertIn("test_skip", data["modules"])
        self.assertEqual([], [p.name for p in self.workspace.iterdir() if p.name.startswith(".timings-")])
        passing = self.run_script("--update-timings", "--timings", str(path), "test_pass", log_dir="logs-2")
        self.assertEqual(0, passing.returncode, passing.stdout + passing.stderr)
        self.assertIn(f"Updated {path} for 1 module(s).", passing.stdout)

    def test_all_passing_exits_zero_and_reports_skips(self):
        result = self.run_script("--workers", "2", "test_pass", "test_skip")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("workers: 2, units: 2", result.stdout)
        self.assertIn("total: 2 modules, 4 tests, 1 skipped", result.stdout)
        self.assertIn("slowest units:\n    test_", result.stdout)
        self.assertIn(f"Logs: {self.log_dir}", result.stdout)
        self.assertEqual(["unit-001-test_pass.log", "unit-002-test_skip.log"],
                         sorted(p.name for p in self.log_dir.iterdir()))

    def test_failure_exits_one_and_names_failing_test_with_log(self):
        result = self.run_script("--workers", "2", "test_pass", "test_fail")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        line = next(line for line in result.stdout.splitlines() if "FAIL: test_broken" in line)
        self.assertIn("Failing.test_broken", line)
        self.assertIn(str(self.log_dir / "unit-002-test_fail.log"), line)
        self.assertIn("python3 -B -m unittest tests.<module>", result.stdout)

    def test_crashed_unit_is_reported_with_log_tail(self):
        result = self.run_script("--workers", "2", "test_crash", "test_pass")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("test_crash: no Ran line", result.stdout)
        self.assertIn("Unit test_crash FAILED without a Ran line (exit 3)", result.stdout)
        self.assertIn("total: 2 modules, 2 tests", result.stdout)

    def test_unmatched_selection_is_a_usage_error(self):
        result = self.run_script("test_missing")
        self.assertEqual(2, result.returncode)
        self.assertIn("no test module matches 'test_missing'", result.stderr)
        self.assertFalse(self.log_dir.exists())
        workers = self.run_script("--workers", "0", "test_pass")
        self.assertEqual(2, workers.returncode)
        self.assertIn("--workers must be at least 1", workers.stderr)

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

    def test_child_mode_does_not_import_from_scripts(self):
        self.add_module("test_shadow", "import unittest\n\nimport validate\n\n\nclass S(unittest.TestCase):\n"
                                       "    def test_root_module(self):\n"
                                       "        self.assertTrue(validate.FROM_ROOT)\n")
        (self.root / "validate.py").write_text("FROM_ROOT = True\n")
        self.created = self.snapshot()
        result = self.run_script("test_shadow")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_signals_stop_every_unit(self):
        for name, sig in [("sigterm", signal.SIGTERM), ("sigint", signal.SIGINT)]:
            with self.subTest(signal=name):
                pid_files = [self.workspace / f"{name}-{index}.pid" for index in range(2)]
                for index, pid_file in enumerate(pid_files):
                    self.add_module(f"test_sleep_{name}_{index}", sleeper(pid_file))
                runner = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "--root", str(self.root),
                                           "--workers", "2", "--log-dir", str(self.workspace / f"logs-{name}"),
                                           f"test_sleep_{name}_*"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.addCleanup(runner.kill)
                self.wait_until(lambda: all(path.exists() and path.read_text() for path in pid_files))
                children = [int(path.read_text()) for path in pid_files]
                self.addCleanup(self.kill_all, children)
                runner.send_signal(sig)
                expected = 130 if sig == signal.SIGINT else 128 + signal.SIGTERM
                self.assertEqual(expected, runner.wait(timeout=30))
                self.wait_until(lambda: not any(self.alive(child) for child in children))

    def run_home_modules(self, *args, modules=("test_homewriter",), clean=(), write=True, **extra):
        for name in modules:
            self.add_module(name, HOME_WRITING.format(module=name, write=write))
        for name in clean:
            self.add_module(name, HOME_WRITING.format(module=name, write=False))
        modules = modules + tuple(clean)
        self.marks = self.workspace / "marks"
        self.marks.mkdir(exist_ok=True)
        env = {**os.environ, "MARK_DIR": str(self.marks), "CODEX_HOME": str(self.workspace / "codex-parent"),
               **{name: str(self.workspace / "parent-value") for name in (
                   "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")}, **extra}
        return self.run_script("--workers", "2", *args, *modules, env=env)

    def record(self, name):
        return json.loads((self.marks / f"{name}.json").read_text())

    def test_unit_runs_with_a_private_canonical_home(self):
        result = self.run_home_modules()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        record = self.record("test_homewriter")
        home = Path(record["home"])
        self.assertNotEqual(os.environ.get("HOME"), record["home"])
        self.assertEqual(record["home"], record["real"])
        self.assertFalse(test_sharded.is_inside(home, self.root))
        self.assertEqual(record["home"], record["vcw"])
        self.assertEqual(record["home"], record["home_env"])
        self.assertTrue(record["share"])
        self.assertEqual([], record["share_entries"])
        self.assertIsNone(record["codex"])
        self.assertEqual([None] * 4, record["xdg"])

    def test_home_writes_are_reported_without_failing_by_default(self):
        result = self.run_home_modules()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("Files written under the private HOME", result.stdout)
        self.assertIn("test_homewriter: 2 path(s)", result.stdout)
        self.assertIn("    .config\n    .config/tool.txt\n", result.stdout)
        self.assertNotIn("kept HOME", result.stdout)
        self.assertFalse((self.log_dir / "homes").exists())

    def test_fail_on_home_writes_exits_one(self):
        result = self.run_home_modules("--fail-on-home-writes")
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("--fail-on-home-writes", result.stdout)
        self.assertIn("1 tests", result.stdout)
        clean = self.run_script("--fail-on-home-writes", "test_pass", log_dir="logs-clean")
        self.assertEqual(0, clean.returncode, clean.stdout + clean.stderr)

    def test_keep_homes_keeps_only_homes_with_leaked_files(self):
        result = self.run_home_modules("--keep-homes", clean=("test_homeclean",))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        kept = Path(self.record("test_homewriter")["home"])
        self.assertIn(f"kept HOME: {kept}", result.stdout)
        self.assertEqual("x", (kept / ".config" / "tool.txt").read_text())
        self.assertEqual([kept.name], [path.name for path in (self.log_dir / "homes").iterdir()])

    def test_clean_unit_reports_nothing_and_leaves_no_home(self):
        result = self.run_home_modules("--keep-homes", write=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("Files written under the private HOME", result.stdout)
        self.assertFalse((self.log_dir / "homes").exists())

    def test_concurrent_units_get_different_homes(self):
        result = self.run_home_modules(modules=("test_home_a", "test_home_b"), WAIT_FOR_PEER="1")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        homes = {self.record(name)["home"] for name in ("test_home_a", "test_home_b")}
        self.assertEqual(2, len(homes))

    def test_unit_does_not_start_when_its_home_cannot_be_created(self):
        self.log_dir = self.workspace / "logs"
        self.log_dir.mkdir()
        (self.log_dir / "homes").write_text("in the way")
        result = self.run_home_modules()
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("unit failed to start", result.stderr)
        self.assertIn("cannot create private HOME", result.stderr)
        self.assertEqual([], list(self.marks.iterdir()))

    def test_refuses_a_log_dir_that_puts_homes_beside_the_root(self):
        self.add_module("test_beside", HOME_WRITING.format(module="test_beside", write=False))
        self.marks = self.workspace / "marks"
        self.marks.mkdir()
        beside = self.run_script("--log-dir", str(self.workspace), "test_beside",
                                 log_dir=False, env={**os.environ, "MARK_DIR": str(self.marks)})
        self.assertEqual(1, beside.returncode, beside.stdout + beside.stderr)
        self.assertIn("beside the root", beside.stderr)
        self.assertEqual([], list(self.marks.iterdir()))
        self.assertFalse((self.workspace / "homes").exists())

    def test_interrupt_reports_finished_units_and_keeps_their_homes(self):
        for name, sig in [("sigterm", signal.SIGTERM), ("sigint", signal.SIGINT)]:
            with self.subTest(signal=name):
                writer = f"test_iwrite_{name}"
                pid_file = self.workspace / f"{name}.pid"
                self.add_module(writer, HOME_WRITING.format(module=writer, write=True))
                self.add_module(f"test_isleep_{name}", sleeper(pid_file))
                self.marks = self.workspace / f"marks-{name}"
                self.marks.mkdir()
                log_dir = self.workspace / f"logs-int-{name}"
                runner = subprocess.Popen(
                    [sys.executable, "-B", str(SCRIPT), "--root", str(self.root), "--workers", "2",
                     "--keep-homes", "--log-dir", str(log_dir), writer, f"test_isleep_{name}"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    env={**os.environ, "MARK_DIR": str(self.marks)})
                self.addCleanup(runner.kill)
                self.wait_until(lambda: pid_file.exists() and pid_file.read_text())
                self.addCleanup(self.kill_all, [int(pid_file.read_text())])
                self.wait_until(lambda: any("Ran 1 test" in path.read_text() for path in log_dir.glob(
                    f"*{writer}.log")))
                time.sleep(1)
                runner.send_signal(sig)
                out, err = runner.communicate(timeout=30)
                self.assertEqual(130 if sig == signal.SIGINT else 143, runner.returncode, out + err)
                self.assertIn("Interrupted", err)
                self.assertIn(f"{writer}: 2 path(s)", out)
                kept = Path(self.record(writer)["home"])
                self.assertIn(f"kept HOME: {kept}", out)
                self.assertEqual("x", (kept / ".config" / "tool.txt").read_text())
                self.assertEqual([kept.name], [path.name for path in (log_dir / "homes").iterdir()])

    def kill_all(self, pids):
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

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
