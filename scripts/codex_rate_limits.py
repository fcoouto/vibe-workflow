#!/usr/bin/env python3
"""The operator's one-shot Codex rate-limit read (R7): `codex_rate_limits.py [--settings PATH]`.

It reads the ChatGPT-subscription pool's windows from `codex app-server` (`account/rateLimits/read`, no tokens)
with the user's Codex login, read-only, and stores them next to the routing settings. It ignores the failure
backoff but never runs while another read holds the pool. It prints the outcome and timing, never an account id.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import codex_rate_limits as reader  # noqa: E402

USAGE = 'usage: codex_rate_limits.py [--settings PATH]'


def default_settings():
    return Path.home() / '.local/state/vibe-workflow/routing/settings.json'  # scripts/routing.py default_settings


def main(argv):
    if argv not in ([], ) and not (len(argv) == 2 and argv[0] == '--settings' and argv[1]):
        print(USAGE)
        return 2
    settings = Path(argv[1]) if argv else default_settings()
    timing = {}
    outcome = reader.refresh(settings, force=True, timing=timing)
    print(json.dumps(dict(outcome, timing=timing), sort_keys=True))
    return 0 if outcome['state'] in ('stored', 'fresh') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
