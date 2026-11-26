"""Synthetic native transports. Never discovers or calls an installed client."""
import json
import os
from pathlib import Path
import signal
import sys
import time

data = json.loads(Path(sys.argv[1]).read_text())
trace = Path(data["trace"])
if data["mode"] == "hang":
    trace.write_text("started")
    sys.stdin.read()  # no response; closing the native transport releases the fake
elif data["mode"] == "opencode":
    with trace.open("a") as out:
        out.write(sys.argv[-1] + "\n")
    print(json.dumps(data["answers"][sys.argv[-1]]))
else:
    for i, line in enumerate(sys.stdin):
        request = json.loads(line)
        with trace.open("a") as out:
            out.write(json.dumps(request) + "\n")
        if i == 1 and "release" in data:
            # Test barrier: announce that get_usage is in flight, then wait for the test to open `release`.
            with open(data["reached"], "w") as reached:
                reached.write("1")
            with open(data["release"]) as release:
                release.read()
        print(json.dumps(dict(type="control_response", response=dict(
            request_id=request["request_id"], subtype="success",
            response=data["initialize"] if i == 0 else data["usage"]))), flush=True)
        if i == 1:
            break
    if "interrupt_on_eof" in data:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        sys.stdin.read()  # EOF proves the parent has entered _stop and closed its control pipe.
        os.kill(os.getppid(), data["interrupt_on_eof"])
        time.sleep(5)  # bounded natural exit if cleanup regresses
