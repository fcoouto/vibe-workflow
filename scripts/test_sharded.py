#!/usr/bin/env python3
"""Run the unittest modules under tests/ as parallel work units and summarize them.

Each unit is a separate process started from this script in a hidden child mode:
a whole module, a module minus its recorded heavy tests, or one heavy test. The
parent never imports test modules, so every test runs exactly once whatever the
timings file says. Units run longest-first on a fixed number of workers, using
estimates from tests/timings.json (or file sizes when it is missing). Logs go to
a directory outside the repository because some tests scan the package root and
its siblings.
"""

import argparse
import fnmatch
import heapq
import json
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
TIMINGS_VERSION = 1
HEAVY_SECONDS = 15
SLOW_TEST_SECONDS = 3
DEFAULT_SECONDS_PER_BYTE = 0.0025
MAX_DEFAULT_WORKERS = 12
RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
FAILED_TEST = re.compile(r"^(?:ERROR|FAIL): \S+ \(.+\)", re.MULTILINE)
SKIPPED = re.compile(r"\bskipped=(\d+)")
STALE_PREFIX = "test_sharded: stale timing entry: "
STALE = re.compile("^" + re.escape(STALE_PREFIX) + r"(\S+)$", re.MULTILINE)
SLOWEST_SHOWN = 5
TAIL_LINES = 20
POLL_SECONDS = 0.05
TERMINATE_GRACE_SECONDS = 5
HOMES_DIR = "homes"
HOME_OVERRIDES = ("CODEX_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")
LEAK_PATHS_KEPT = 20
LEAK_PATHS_SHOWN = 5


class UsageError(Exception):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int,
                        help=f"parallel units (default: min({MAX_DEFAULT_WORKERS}, CPU count))")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root containing tests/")
    parser.add_argument("--log-dir", type=Path, help="existing or new directory outside the root for unit logs")
    parser.add_argument("--timings", type=Path, help="timings file (default: <root>/tests/timings.json)")
    parser.add_argument("--update-timings", action="store_true",
                        help="record durations and rewrite the timings file after the run")
    parser.add_argument("--fast", action="store_true",
                        help="development aid: skip modules the timings file marks slow; not a release gate")
    parser.add_argument("--fail-on-home-writes", action="store_true",
                        help="exit 1 when a unit leaves files under its private HOME")
    parser.add_argument("--keep-homes", action="store_true",
                        help="keep (and print the path of) private HOMEs that hold leaked files")
    parser.add_argument("--list", action="store_true", help="print the unit plan and predicted wall time, then exit")
    parser.add_argument("modules", nargs="*", metavar="MODULE_OR_GLOB",
                        help="module names or globs, e.g. test_state, tests.test_state, 'test_delegation*'")
    hidden = argparse.SUPPRESS
    parser.add_argument("--run-unit", action="store_true", help=hidden)
    parser.add_argument("--module", help=hidden)
    parser.add_argument("--exclude", action="append", default=[], help=hidden)
    parser.add_argument("--test", help=hidden)
    parser.add_argument("--timings-out", type=Path, help=hidden)
    return parser.parse_args(argv)


# ---------------------------------------------------------------- child mode


class TimedResult(unittest.TextTestResult):
    """Text result that also records each test's wall time by test id."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.durations = {}
        self._started = {}

    def startTest(self, test):
        self._started[test.id()] = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        started = self._started.pop(test.id(), None)
        if started is not None:
            self.durations[test.id()] = time.monotonic() - started


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def short_id(test_id):
    return test_id.removeprefix("tests.")


def unit_suite(module, excluded, only):
    """Load the whole module and keep the requested tests; return (suite, stale ids)."""
    tests = list(flatten(unittest.defaultTestLoader.loadTestsFromName(f"tests.{module}")))
    ids = {short_id(test.id()) for test in tests}
    if only is not None:
        kept = [test for test in tests if short_id(test.id()) == only]
        stale = [] if kept else [only]
    else:
        kept = [test for test in tests if short_id(test.id()) not in excluded]
        stale = sorted(test_id for test_id in excluded if test_id not in ids)
    return unittest.TestSuite(kept), stale


def isolate_imports(root):
    """Behave like `python -m unittest` run from the root: the root, not scripts/, is on sys.path."""
    os.chdir(root)
    script_dir = str(SCRIPT.parent)
    sys.path[:] = [str(root)] + [entry for entry in sys.path[1:] if entry not in ("", script_dir)]


def run_unit(args):
    if (args.module is None) == (args.test is None):
        print("test_sharded: --run-unit needs exactly one of --module or --test", file=sys.stderr)
        return 2
    isolate_imports(args.root.resolve())
    module = args.module or args.test.split(".", 1)[0]
    suite, stale = unit_suite(module, set(args.exclude), args.test)
    for test_id in stale:
        print(STALE_PREFIX + test_id, file=sys.stderr, flush=True)
    warnings = None if sys.warnoptions else "default"
    result = unittest.TextTestRunner(verbosity=1, warnings=warnings, resultclass=TimedResult).run(suite)
    if args.timings_out is not None:
        durations = {short_id(test_id): seconds for test_id, seconds in result.durations.items()}
        with open(args.timings_out, "x", encoding="utf-8") as handle:
            json.dump({"tests": durations}, handle, sort_keys=True)
    return 0 if result.wasSuccessful() else 1


# ---------------------------------------------------------------- planning


def discover(root):
    """Return {module name: file size} for tests/test_*.py, in name order."""
    paths = sorted((root / "tests").glob("test_*.py"))
    return {path.stem: path.stat().st_size for path in paths if path.is_file()}


def select(modules, patterns):
    if not patterns:
        return list(modules)
    chosen = set()
    for pattern in patterns:
        name = pattern.removeprefix("tests.").removesuffix(".py")
        name = name.removeprefix("tests/")
        matches = fnmatch.filter(modules, name)
        if not matches:
            raise UsageError(f"no test module matches {pattern!r}")
        chosen.update(matches)
    return [name for name in modules if name in chosen]


def worker_count(requested, unit_count):
    if requested is not None and requested < 1:
        raise UsageError("--workers must be at least 1")
    count = requested if requested is not None else min(MAX_DEFAULT_WORKERS, os.cpu_count() or 1)
    return max(1, min(count, unit_count))


def valid_timings(data):
    def seconds_map(value):
        return isinstance(value, dict) and all(
            isinstance(key, str) and isinstance(number, (int, float)) and not isinstance(number, bool)
            for key, number in value.items())
    return (isinstance(data, dict) and data.get("version") == TIMINGS_VERSION
            and seconds_map(data.get("modules")) and seconds_map(data.get("tests"))
            and isinstance(data.get("slow_modules"), list)
            and all(isinstance(name, str) for name in data["slow_modules"]))


def load_timings(path):
    """Return (timings or None, notice or None); a bad file is never an error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"no timings file at {path}; estimating unit costs from file sizes"
    except (OSError, ValueError) as error:
        return None, f"timings file {path} is unreadable ({error}); estimating unit costs from file sizes"
    if not valid_timings(data):
        return None, (f"timings file {path} is not version {TIMINGS_VERSION} timings; "
                      "estimating unit costs from file sizes")
    return data, None


def seconds_per_byte(sizes, modules):
    rates = [seconds / sizes[name] for name, seconds in modules.items() if sizes.get(name)]
    return statistics.median(rates) if rates else DEFAULT_SECONDS_PER_BYTE


def heavy_tests(module, timings):
    tests = timings["tests"] if timings else {}
    prefix = module + "."
    return sorted(test_id for test_id, seconds in tests.items()
                  if test_id.startswith(prefix) and seconds >= HEAVY_SECONDS)


def unit(name, module, cost, test=None, exclude=()):
    return {"name": name, "module": module, "cost": cost, "test": test, "exclude": list(exclude)}


def plan_units(chosen, sizes, timings):
    """Split modules with recorded heavy tests; return units sorted longest-first."""
    modules = timings["modules"] if timings else {}
    rate = seconds_per_byte(sizes, modules)
    units = []
    for module in chosen:
        total = modules[module] if module in modules else sizes[module] * rate
        heavy = heavy_tests(module, timings)
        for test_id in heavy:
            units.append(unit(test_id, module, timings["tests"][test_id], test=test_id))
        rest = max(total - sum(timings["tests"][test_id] for test_id in heavy), 0.0)
        units.append(unit(f"{module} (rest)" if heavy else module, module, rest, exclude=heavy))
    return sorted(units, key=lambda item: (-item["cost"], item["name"]))


def simulate(units, workers):
    """Longest-first list scheduling; return (placements, predicted wall, critical unit)."""
    free = [(0.0, index) for index in range(workers)]
    placements, critical, wall = [], None, 0.0
    for item in units:
        start, index = heapq.heappop(free)
        finish = start + item["cost"]
        placements.append((item, index + 1, start, finish))
        heapq.heappush(free, (finish, index))
        if finish > wall or critical is None:
            wall, critical = finish, item
    return placements, wall, critical


def is_slow(module, timings):
    return bool(timings) and module in timings["slow_modules"]


def fast_selection(chosen, timings):
    kept = [module for module in chosen if not is_slow(module, timings)]
    if not kept:
        raise UsageError("--fast left no modules to run; every selected module is marked slow")
    return kept


def fast_banner(skipped, timings):
    lines = ["FAST TIER: a development aid, NOT a release gate.",
             f"  Skipping {skipped} slow module(s) listed in the timings file; "
             "select tests for the change and run the full suite for a release."]
    if not timings:
        lines.append("  No usable timings file, so every module counts as fast.")
    return "\n".join(lines)


# ---------------------------------------------------------------- running


def is_inside(path, root):
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def prepare_log_dir(requested, root):
    # Check before creating anything: a directory created inside the root is a write there.
    parent = Path(tempfile.gettempdir()) if requested is None else requested
    if is_inside(parent, root):
        raise UsageError(f"log directory {parent} is inside the root {root}; choose one outside it")
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="test-units-", dir=parent)).resolve()
    requested.mkdir(parents=True, exist_ok=True)
    return requested.resolve()


def file_stem(index, item):
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", item["name"]).strip("-")[:120]
    return f"unit-{index:03d}-{name}"


def unit_command(item, root, timings_out):
    command = [sys.executable, "-B", str(SCRIPT), "--run-unit", "--root", str(root)]
    if item["test"]:
        command += ["--test", item["test"]]
    else:
        command += ["--module", item["module"]]
        for test_id in item["exclude"]:
            command += ["--exclude", test_id]
    if timings_out is not None:
        command += ["--timings-out", str(timings_out)]
    return command


class HomeError(Exception):
    """A unit's private HOME could not be created; the unit must not run with the real one."""


def make_home(index, root, log_dir):
    """Create a fresh canonical HOME with .local/share for one unit, outside the root and its parent."""
    base = log_dir / HOMES_DIR
    home = base / f"home-{index:03d}"
    if is_inside(base, root):
        raise HomeError(f"private HOME directory {base} would be inside the root {root}")
    if base.parent.resolve() == root.parent:
        raise HomeError(f"private HOME directory {base} would sit in {root.parent}, beside the root; "
                        "choose a log directory that is not the root's parent")
    ready = owned = False
    try:
        base.mkdir(exist_ok=True)
        home.mkdir()
        owned = True
        (home / ".local" / "share").mkdir(parents=True)
        if Path(os.path.realpath(home)) != home:
            raise HomeError(f"private HOME {home} is not a canonical path")
        ready = True
    except OSError as error:
        raise HomeError(f"cannot create private HOME {home}: {error}") from error
    finally:
        if owned and not ready:
            shutil.rmtree(home, ignore_errors=True)  # only a directory this call created
    return home


def unit_env(home):
    """Environment for a unit: the parent's, with HOME redirected and the overriding variables removed."""
    env = {key: value for key, value in os.environ.items() if key not in HOME_OVERRIDES}
    env["HOME"] = env["VCW_TEST_HOME"] = str(home)
    return env


def home_leaks(home):
    """Return (count, first relative paths) of everything in a HOME except the empty .local/share."""
    found = []
    for directory, names, files in os.walk(home):
        base = Path(directory).relative_to(home)
        found += [str(base / name) for name in names + files]
    # The pre-created directories are not leaks; whatever is inside them still is.
    scaffold = {".local", str(Path(".local") / "share")}
    leaked = sorted(path for path in found
                    if path not in scaffold or (home / path).is_symlink() or not (home / path).is_dir())
    return len(leaked), leaked[:LEAK_PATHS_KEPT]


def remove_empty_homes_dir(log_dir):
    try:
        (log_dir / HOMES_DIR).rmdir()
    except OSError:
        pass  # absent, or it still holds a kept HOME


def drop_home(run):
    shutil.rmtree(run["home"], ignore_errors=True)


def finish_home(run, keep):
    """Record what the unit left in its HOME, then remove the HOME; never raises."""
    run["leak"] = None
    try:
        count, paths = home_leaks(run["home"])
        if count:
            run["leak"] = {"count": count, "paths": paths, "kept": run["home"] if keep else None}
    except OSError as error:
        print(f"test_sharded: cannot inspect {run['home']}: {error}", file=sys.stderr)
    if not (run["leak"] and run["leak"]["kept"]):
        drop_home(run)


def start_unit(index, item, root, log_dir, record):
    stem = file_stem(index, item)
    log_path = log_dir / f"{stem}.log"
    timings_out = log_dir / f"{stem}.json" if record else None
    # Exclusive create: never overwrite logs from an earlier run.
    log = open(log_path, "x", encoding="utf-8")
    try:
        home = make_home(index, root, log_dir)
        try:
            process = subprocess.Popen(unit_command(item, root, timings_out), cwd=root, env=unit_env(home),
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
        except BaseException:
            shutil.rmtree(home, ignore_errors=True)
            raise
    finally:
        log.close()
    return {"unit": item, "log": log_path, "timings_out": timings_out, "process": process, "home": home,
            "leak": None, "finished": False, "started": time.monotonic(), "elapsed": None}


def signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_all(runs):
    running = [run["process"] for run in runs if run["process"].poll() is None]
    for process in running:
        signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
    for process in running:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            signal_group(process, signal.SIGKILL)
            process.wait()


def run_units(units, workers, root, log_dir, record, keep_homes=False, runs=None):
    """Start the next unit whenever a worker slot frees; a failing unit does not stop the rest.

    Started runs are appended to `runs` (when given) so a caller can still report the finished ones
    if the run is interrupted."""
    queue = list(enumerate(units, start=1))
    runs = [] if runs is None else runs
    active = []
    try:
        while queue or active:
            while queue and len(active) < workers:
                index, item = queue.pop(0)
                run = start_unit(index, item, root, log_dir, record)
                runs.append(run)
                active.append(run)
            for run in [run for run in active if run["process"].poll() is not None]:
                run["elapsed"] = time.monotonic() - run["started"]
                finish_home(run, keep_homes)
                run["finished"] = True
                active.remove(run)
            if active and (len(active) == workers or not queue):
                time.sleep(POLL_SECONDS)
    except BaseException:
        stop_all(runs)
        for run in runs:
            if not run["finished"]:
                drop_home(run)
        remove_empty_homes_dir(log_dir)
        raise
    remove_empty_homes_dir(log_dir)
    return runs


def read_durations(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {key: float(value) for key, value in data["tests"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def read_result(run):
    text = run["log"].read_text(encoding="utf-8", errors="replace")
    ran = list(RAN.finditer(text))
    skipped = SKIPPED.search(text, ran[-1].end()) if ran else None
    returncode = run["process"].returncode
    return {
        "unit": run["unit"],
        "log": run["log"],
        "elapsed": run["elapsed"],
        "returncode": returncode,
        "tests": int(ran[-1].group(1)) if ran else None,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "failed_tests": FAILED_TEST.findall(text),
        "stale": STALE.findall(text),
        "leak": run["leak"],
        "ok": returncode == 0 and bool(ran),
        "tail": text.splitlines()[-TAIL_LINES:],
        "durations": read_durations(run["timings_out"]) if run["timings_out"] else None,
    }


# ---------------------------------------------------------------- timings file


def module_measurements(results):
    """Return {module: (seconds, {test id: seconds})} for modules whose units all passed."""
    by_module = {}
    for result in results:
        by_module.setdefault(result["unit"]["module"], []).append(result)
    measured = {}
    for module, group in by_module.items():
        if all(result["ok"] and result["durations"] is not None for result in group):
            tests = {key: value for result in group for key, value in result["durations"].items()}
            measured[module] = (sum(result["elapsed"] for result in group), tests)
    return measured


def merge_timings(previous, measured, existing_modules):
    """Replace measured modules' entries, keep the rest, drop modules that no longer exist."""
    previous = previous or {"modules": {}, "tests": {}, "slow_modules": []}

    def kept(module):
        return module in existing_modules and module not in measured
    modules = {name: value for name, value in previous["modules"].items() if kept(name)}
    tests = {key: value for key, value in previous["tests"].items() if kept(key.split(".", 1)[0])}
    slow = {name for name in previous["slow_modules"] if kept(name)}
    for module, (seconds, durations) in measured.items():
        modules[module] = round(seconds, 1)
        tests.update({key: round(value, 1) for key, value in durations.items() if value >= HEAVY_SECONDS})
        if any(value >= SLOW_TEST_SECONDS for value in durations.values()):
            slow.add(module)
    return {"version": TIMINGS_VERSION, "modules": modules, "tests": tests, "slow_modules": sorted(slow)}


def write_timings(path, data):
    """Write atomically with stable ordering so diffs stay small."""
    fd, temp = tempfile.mkstemp(prefix=".timings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)
            handle.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def update_timings(path, previous, results, existing_modules):
    measured = module_measurements(results)
    write_timings(path, merge_timings(previous, measured, existing_modules))
    unmeasured = sorted({result["unit"]["module"] for result in results} - set(measured))
    if unmeasured:
        print(f"\nUpdated {path} for {len(measured)} module(s); kept previous timings for "
              f"{len(unmeasured)} module(s) that failed or crashed: {' '.join(unmeasured)}")
    else:
        print(f"\nUpdated {path} for {len(measured)} module(s).")


# ---------------------------------------------------------------- reporting


def unit_line(result):
    tests = "no Ran line" if result["tests"] is None else f"{result['tests']} tests"
    status = "OK" if result["ok"] else f"FAILED (exit {result['returncode']})"
    return f"{result['unit']['name']}: {tests}, {result['elapsed']:.1f}s, {status}"


def print_crashes(results):
    for result in results:
        if result["tests"] is None:
            print(f"\nUnit {result['unit']['name']} FAILED without a Ran line (exit {result['returncode']}); "
                  f"last {TAIL_LINES} lines of {result['log']}:")
            for line in result["tail"]:
                print(f"  | {line}")


def print_home_writes(results):
    leaks = [result for result in results if result["leak"]]
    if not leaks:
        return
    print("\nFiles written under the private HOME (tests that write to default home locations):")
    for result in leaks:
        leak = result["leak"]
        print(f"  {result['unit']['name']}: {leak['count']} path(s) [{result['log']}]")
        for path in leak["paths"][:LEAK_PATHS_SHOWN]:
            print(f"    {path}")
        if leak["count"] > LEAK_PATHS_SHOWN:
            print(f"    ... and {leak['count'] - LEAK_PATHS_SHOWN} more (first {len(leak['paths'])} recorded)")
        if leak["kept"]:
            print(f"    kept HOME: {leak['kept']}")


def print_summary(results, workers, wall, log_dir, fail_on_home_writes=False):
    tests = sum(result["tests"] or 0 for result in results)
    skipped = sum(result["skipped"] for result in results)
    modules = len({result["unit"]["module"] for result in results})
    ok = all(result["ok"] for result in results)
    leaked = any(result["leak"] for result in results)
    final = ok and not (fail_on_home_writes and leaked)
    print(f"\nSummary\n  workers: {workers}, units: {len(results)}")
    print(f"  total: {modules} modules, {tests} tests, {skipped} skipped, "
          f"{wall:.1f}s wall, {'OK' if final else 'FAILED'}")
    print("  slowest units:")
    for result in sorted(results, key=lambda item: -item["elapsed"])[:SLOWEST_SHOWN]:
        print("    " + unit_line(result))
    failures = [(test, result["log"]) for result in results for test in result["failed_tests"]]
    if failures:
        print("\nFailing tests:")
        for test, log in failures:
            print(f"  {test}  [{log}]")
    print_crashes(results)
    print_home_writes(results)
    if fail_on_home_writes and leaked:
        print("\nFAILED: --fail-on-home-writes and at least one unit wrote under its private HOME.")
    stale = sorted({test_id for result in results for test_id in result["stale"]})
    if stale:
        print("\nStale timing entries (no such test; refresh with --update-timings):")
        for test_id in stale:
            print(f"  {test_id}")
    if not ok:
        print("\nTiming-sensitive tests can fail on an overloaded machine; rerun one module alone with:"
              "\n  python3 -B -m unittest tests.<module>")
    print(f"\nLogs: {log_dir}")
    return final


def print_plan(units, workers, module_count, source):
    placements, wall, critical = simulate(units, workers)
    print(f"Plan: {workers} workers, {len(units)} units, {module_count} modules; costs from {source}")
    for index, (item, worker, start, finish) in enumerate(placements, start=1):
        print(f"unit {index:03d} {item['cost']:8.1f}s  worker {worker:2d} "
              f"{start:8.1f}-{finish:8.1f}s  {item['name']}")
    name = critical["name"] if critical else "none"
    print(f"Predicted wall: {wall:.1f}s (critical unit: {name})")


# ---------------------------------------------------------------- main


def terminate(signum, frame):
    """Turn a terminating signal into an exit so run_units stops its units."""
    raise SystemExit(128 + signum)


def prepare(args, root):
    """Resolve selection, timings and plan; raise UsageError on bad input."""
    sizes = discover(root)
    if not sizes:
        raise UsageError(f"no tests/test_*.py modules under {root}")
    chosen = select(list(sizes), args.modules)
    timings_path = (args.timings or root / "tests" / "timings.json").resolve()
    timings, notice = load_timings(timings_path)
    banner = None
    if args.fast:
        selected = len(chosen)
        chosen = fast_selection(chosen, timings)
        banner = fast_banner(selected - len(chosen), timings)
    units = plan_units(chosen, sizes, timings)
    workers = worker_count(args.workers, len(units))
    source = "file sizes" if timings is None else str(timings_path)
    return {"sizes": sizes, "chosen": chosen, "timings_path": timings_path, "timings": timings,
            "notice": notice, "banner": banner, "units": units, "workers": workers, "source": source}


def execute(plan, root, log_dir, args):
    record = args.update_timings
    print(f"Running {len(plan['chosen'])} modules as {len(plan['units'])} units on "
          f"{plan['workers']} workers; logs: {log_dir}", flush=True)
    started = time.monotonic()
    runs = []
    try:
        run_units(plan["units"], plan["workers"], root, log_dir, record, args.keep_homes, runs)
    except (KeyboardInterrupt, SystemExit) as stop:
        code = 130 if isinstance(stop, KeyboardInterrupt) else stop.code
        print(f"\nInterrupted; stopped all units. Logs: {log_dir}", file=sys.stderr)
        print_home_writes([read_result(run) for run in runs if run["finished"]])
        return code
    except HomeError as error:
        print(f"test_sharded: unit failed to start, stopped all units: {error}", file=sys.stderr)
        return 1
    except FileExistsError as error:
        print(f"test_sharded: log already exists, not overwriting: {error.filename}", file=sys.stderr)
        return 2
    results = [read_result(run) for run in runs]
    ok = print_summary(results, plan["workers"], time.monotonic() - started, log_dir, args.fail_on_home_writes)
    if record:
        update_timings(plan["timings_path"], plan["timings"], results, set(plan["sizes"]))
    if plan["banner"]:
        print("\n" + plan["banner"])
    return 0 if ok else 1


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.run_unit:
        return run_unit(args)
    root = args.root.resolve()
    try:
        plan = prepare(args, root)
        if plan["notice"]:
            print(plan["notice"], flush=True)
        if plan["banner"]:
            print(plan["banner"], flush=True)
        if args.list:
            print_plan(plan["units"], plan["workers"], len(plan["chosen"]), plan["source"])
            return 0
        log_dir = prepare_log_dir(args.log_dir, root)
    except (UsageError, FileNotFoundError) as error:
        print(f"test_sharded: {error}", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    return execute(plan, root, log_dir, args)


if __name__ == "__main__":
    sys.exit(main())
