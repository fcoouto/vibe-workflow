#!/usr/bin/env python3
"""One bounded advisory request. No launch, retry, installation or legacy state."""
import argparse
import json
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import chatgpt_limit, council
from runtime.selector import check_setup, feedback_submit, refresh, result, select

MAX_BYTES = 65536


def interrupted(signum, frame):
    # Let native exchanges finish their existing finally-based cleanup.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    raise KeyboardInterrupt


def encode(answer):
    """The answer as compact UTF-8 JSON plus newline. Over the limit, `unmatched` is cut (with `unmatched_more`)
    before anything fails; text UTF-8 cannot carry falls back to ASCII escapes."""
    def dump(value):
        try:
            return (json.dumps(value, separators=(",", ":"), allow_nan=False, ensure_ascii=False) + "\n").encode()
        except UnicodeEncodeError:
            return (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()
    data = dump(answer)
    listed = answer.get("unmatched") if type(answer) is dict else None
    if len(data) > MAX_BYTES and type(listed) is list and listed:
        def cut(count):
            value = {key: item for key, item in answer.items() if key != "unmatched"}
            if count:
                value["unmatched"] = listed[:count]
            return dict(value, unmatched_more=len(listed) - count)
        low, high = 0, len(listed) - 1
        while low < high:
            middle = (low + high + 1) // 2
            low, high = (middle, high) if len(dump(cut(middle))) <= MAX_BYTES else (low, middle - 1)
        data = dump(cut(low))
    if len(data) > MAX_BYTES:
        raise ValueError()
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--check-setup", action="store_true")
    parser.add_argument("--allow-fetch", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--chatgpt-limit", action="store_true")
    parser.add_argument("--council-roster", action="store_true")
    parser.add_argument("--council-pick", action="store_true")
    parser.add_argument("--feedback", action="store_true")
    args = parser.parse_args()
    if args.allow_fetch and not args.check_setup:
        parser.error("--allow-fetch needs --check-setup")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        if args.chatgpt_limit:
            record = chatgpt_limit.mark(Path(args.state_dir), int(time.time()))
            answer = dict(state="marked", until=record["until"], source=record["source"])
        elif args.check_setup:
            answer = check_setup(state_dir=Path(args.state_dir), allow_fetch=args.allow_fetch)
        else:
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError()
            call = council.roster if args.council_roster else council.pick if args.council_pick \
                else feedback_submit if args.feedback else refresh if args.refresh else select
            answer = call(json.loads(raw), state_dir=Path(args.state_dir))
        encoded = encode(answer)
    except (Exception, KeyboardInterrupt):
        answer = dict(state="failed") if args.chatgpt_limit or args.council_roster or args.council_pick \
            or args.feedback else \
            dict(state="setup_needed", reason="selector_unavailable") if args.check_setup \
            else result("blocked", "selector_unavailable")
        encoded = (json.dumps(answer, separators=(",", ":")) + "\n").encode()
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
