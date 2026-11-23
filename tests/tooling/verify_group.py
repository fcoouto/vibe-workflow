#!/usr/bin/env python3
"""Opt-in real process-group cleanup proof; all children expire without signals."""
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runtime import processes

CHILD = """
import os, signal, sys, time
from pathlib import Path
def terminate(signum, frame):
    Path(sys.argv[1]).write_text("child received SIGTERM")
    sys.exit(0)
signal.signal(signal.SIGTERM, terminate)
print(os.getpid(), flush=True)
time.sleep(5)
"""

LEADER = """
import json, os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-I", "-B", "-c", sys.argv[1], sys.argv[2]],
    stdout=subprocess.PIPE, text=True)
child_pid = int(child.stdout.readline())
def terminate(signum, frame):
    Path(sys.argv[3]).write_text("leader received SIGTERM")
    child.wait(timeout=0.2)
signal.signal(signal.SIGTERM, terminate)
print(json.dumps(dict(leader=os.getpid(), child=child_pid, group=os.getpgrp())), flush=True)
time.sleep(5)
child.wait(timeout=1)
"""


def verify(folder):
    started = time.monotonic()
    child_mark, leader_mark = folder / "child-term", folder / "leader-term"
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", LEADER, CHILD, str(child_mark), str(leader_mark)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, env={"PATH": "/usr/bin:/bin", "HOME": str(folder)}, cwd=folder)
    calls = []
    original = os.killpg

    def observed(group, sig):
        entry = dict(group=group, signal=int(sig))
        calls.append(entry)
        try:
            return original(group, sig)
        except OSError as error:
            entry.update(error=type(error).__name__, errno=error.errno)
            raise

    outcome = {}
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            if not ready.select(3):
                raise RuntimeError("fixture_not_ready")
        identity = json.loads(process.stdout.readline())
        assert identity["leader"] == process.pid == identity["group"]
        assert os.getpgid(identity["child"]) == process.pid
        try:
            with mock.patch.object(processes.os, "killpg", side_effect=observed):
                processes._stop(process, False)
        except subprocess.TimeoutExpired:
            outcome["cleanup_timeout"] = True
        process.wait(timeout=7)  # Natural expiry only if group signaling was denied.
        outcome.update(identity=identity, signals=calls, leader_returncode=process.returncode,
                       child_sigterm=child_mark.exists(), leader_sigterm=leader_mark.exists())
        denied = any(row.get("errno") in (1, 13) for row in calls)
        passed = process.returncode == -9 and child_mark.exists() and leader_mark.exists()
        outcome["state"] = "passed" if passed else "blocked" if denied else "failed"
        return outcome
    finally:
        process.wait(timeout=8)
        for pipe in (process.stdin, process.stdout):
            if not pipe.closed:
                pipe.close()
        outcome["elapsed_seconds"] = round(time.monotonic() - started, 3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = verify(args.evidence.resolve())
    (args.evidence / "group-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    sys.exit(0 if result["state"] == "passed" else 2)
