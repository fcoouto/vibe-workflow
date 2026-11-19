#!/usr/bin/env python3
"""One bounded advisory request. No launch, retry, installation or legacy state."""
import argparse
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.selector import check_setup, result, select

MAX_BYTES = 65536


def interrupted(signum, frame):
    # Let native exchanges finish their existing finally-based cleanup.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--check-setup", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if args.check_setup:
            answer = check_setup(state_dir=Path(args.state_dir))
        else:
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError()
            answer = select(json.loads(raw), state_dir=Path(args.state_dir))
        encoded = json.dumps(answer, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) + 1 > MAX_BYTES:
            raise ValueError()
    except (Exception, KeyboardInterrupt):
        answer = dict(state="setup_needed", reason="selector_unavailable") if args.check_setup \
            else result("blocked", "invalid_request")
        encoded = json.dumps(answer, separators=(",", ":"))
    print(encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
