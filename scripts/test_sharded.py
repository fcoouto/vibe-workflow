#!/usr/bin/env python3
"""Run the unittest modules under tests/ as parallel shards and summarize them.

Each shard is a separate `python3 -B -m unittest tests.<module> ...` process run
from the repository root; test modules are never imported by this process. Logs
go to a directory outside the repository because some tests scan the package
root and its siblings.
"""

import argparse
import fnmatch
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
FAILED_TEST = re.compile(r"^(?:ERROR|FAIL): \S+ \(.+\)", re.MULTILINE)
SKIPPED = re.compile(r"\bskipped=(\d+)")
TAIL_LINES = 20
POLL_SECONDS = 0.1
TERMINATE_GRACE_SECONDS = 5


class UsageError(Exception):
    pass


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shards", type=int, help="number of parallel shards (default: min(4, CPU count))")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root containing tests/")
    parser.add_argument("--log-dir", type=Path, help="existing or new directory outside the root for shard logs")
    parser.add_argument("--list", action="store_true", help="print the shard assignment and exit")
    parser.add_argument("modules", nargs="*", metavar="MODULE_OR_GLOB",
                        help="module names or globs, e.g. test_state, tests.test_state, 'test_delegation*'")
    return parser.parse_args(argv)


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


def shard_count(requested, module_count):
    if requested is not None and requested < 1:
        raise UsageError("--shards must be at least 1")
    count = requested if requested is not None else min(4, os.cpu_count() or 1)
    return max(1, min(count, module_count))


def balance(sizes, count):
    """Assign the largest modules first to the lightest shard; ties go to the lowest index."""
    shards = [[] for _ in range(count)]
    loads = [0] * count
    for name in sorted(sizes, key=lambda name: (-sizes[name], name)):
        index = min(range(count), key=lambda i: (loads[i], i))
        shards[index].append(name)
        loads[index] += sizes[name]
    return [sorted(shard) for shard in shards], loads


def is_inside(path, root):
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def prepare_log_dir(requested, root):
    # Check before creating anything: a directory created inside the root is a write there.
    parent = Path(tempfile.gettempdir()) if requested is None else requested
    if is_inside(parent, root):
        raise UsageError(f"log directory {parent} is inside the root {root}; choose one outside it")
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="test-shards-", dir=parent)).resolve()
    requested.mkdir(parents=True, exist_ok=True)
    return requested.resolve()


def start_shard(index, modules, root, log_dir):
    log_path = log_dir / f"shard-{index + 1}.log"
    # Exclusive create: never overwrite logs from an earlier run.
    log = open(log_path, "x", encoding="utf-8")
    command = [sys.executable, "-B", "-m", "unittest", *(f"tests.{name}" for name in modules)]
    try:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()
    return {"index": index + 1, "modules": modules, "log": log_path, "process": process,
            "started": time.monotonic(), "elapsed": None}


def wait_for(shards):
    pending = list(shards)
    while pending:
        for shard in list(pending):
            if shard["process"].poll() is not None:
                shard["elapsed"] = time.monotonic() - shard["started"]
                pending.remove(shard)
        if pending:
            time.sleep(POLL_SECONDS)


def signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_all(shards):
    running = [shard["process"] for shard in shards if shard["process"].poll() is None]
    for process in running:
        signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + TERMINATE_GRACE_SECONDS
    for process in running:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            signal_group(process, signal.SIGKILL)
            process.wait()


def run_shards(assignment, root, log_dir):
    shards = []
    try:
        for index, modules in enumerate(assignment):
            shards.append(start_shard(index, modules, root, log_dir))
        wait_for(shards)
    except BaseException:
        stop_all(shards)
        raise
    return shards


def read_result(shard):
    text = shard["log"].read_text(encoding="utf-8", errors="replace")
    ran = list(RAN.finditer(text))
    skipped = SKIPPED.search(text, ran[-1].end()) if ran else None
    returncode = shard["process"].returncode
    return {
        "index": shard["index"],
        "modules": len(shard["modules"]),
        "log": shard["log"],
        "elapsed": shard["elapsed"],
        "returncode": returncode,
        "tests": int(ran[-1].group(1)) if ran else None,
        "skipped": int(skipped.group(1)) if skipped else 0,
        "failed_tests": FAILED_TEST.findall(text),
        "ok": returncode == 0 and bool(ran),
        "tail": text.splitlines()[-TAIL_LINES:],
    }


def shard_line(result):
    tests = "no Ran line" if result["tests"] is None else f"{result['tests']} tests"
    status = "OK" if result["ok"] else f"FAILED (exit {result['returncode']})"
    return (f"shard {result['index']}: {result['modules']} modules, {tests}, "
            f"{result['skipped']} skipped, {result['elapsed']:.1f}s, {status}")


def print_summary(results, wall, log_dir):
    print("\nSummary")
    for result in results:
        print("  " + shard_line(result))
    tests = sum(result["tests"] or 0 for result in results)
    skipped = sum(result["skipped"] for result in results)
    modules = sum(result["modules"] for result in results)
    ok = all(result["ok"] for result in results)
    print(f"  total: {modules} modules, {tests} tests, {skipped} skipped, "
          f"{wall:.1f}s wall, {'OK' if ok else 'FAILED'}")
    failures = [(test, result["log"]) for result in results for test in result["failed_tests"]]
    if failures:
        print("\nFailing tests:")
        for test, log in failures:
            print(f"  {test}  [{log}]")
    for result in results:
        if result["tests"] is None:
            print(f"\nShard {result['index']} FAILED without a Ran line (exit {result['returncode']}); "
                  f"last {TAIL_LINES} lines of {result['log']}:")
            for line in result["tail"]:
                print(f"  | {line}")
    if not ok:
        print("\nTiming-sensitive tests can fail on an overloaded machine; rerun one module alone with:"
              "\n  python3 -B -m unittest tests.<module>")
    print(f"\nLogs: {log_dir}")
    return ok


def print_assignment(assignment, loads):
    for index, (modules, load) in enumerate(zip(assignment, loads), start=1):
        print(f"shard {index} ({len(modules)} modules, {load} bytes): "
              + " ".join(f"tests.{name}" for name in modules))


def terminate(signum, frame):
    """Turn a terminating signal into an exit so run_shards stops its shards."""
    raise SystemExit(128 + signum)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = args.root.resolve()
    try:
        sizes = discover(root)
        if not sizes:
            raise UsageError(f"no tests/test_*.py modules under {root}")
        chosen = select(list(sizes), args.modules)
        count = shard_count(args.shards, len(chosen))
        assignment, loads = balance({name: sizes[name] for name in chosen}, count)
        if args.list:
            print_assignment(assignment, loads)
            return 0
        log_dir = prepare_log_dir(args.log_dir, root)
    except (UsageError, FileNotFoundError) as error:
        print(f"test_sharded: {error}", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    print(f"Running {len(chosen)} modules in {count} shards; logs: {log_dir}", flush=True)
    started = time.monotonic()
    try:
        shards = run_shards(assignment, root, log_dir)
    except KeyboardInterrupt:
        print(f"\nInterrupted; stopped all shards. Logs: {log_dir}", file=sys.stderr)
        return 130
    except FileExistsError as error:
        print(f"test_sharded: log already exists, not overwriting: {error.filename}", file=sys.stderr)
        return 2
    results = [read_result(shard) for shard in shards]
    return 0 if print_summary(results, time.monotonic() - started, log_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
