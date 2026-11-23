"""Synthetic native transports. Never discovers or calls an installed client."""
import json
from pathlib import Path
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
        print(json.dumps(dict(type="control_response", response=dict(
            request_id=request["request_id"], subtype="success",
            response=data["initialize"] if i == 0 else data["usage"]))), flush=True)
        if i == 1:
            break
