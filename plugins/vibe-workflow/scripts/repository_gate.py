#!/usr/bin/env python3
"""Inspect bounded D7 fixture JSON; never authenticate, publish, or authorize."""

import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from runtime.repository_gate import GateError, MAX_INPUT, evaluate, read_envelope

    try:
        if len(sys.argv) != 1:
            raise GateError('Invalid repository gate input')
        request = read_envelope(sys.stdin.buffer.read(MAX_INPUT + 1))
        result = evaluate(request['policy'], request['before'], request['after'], now=request['now'])
    except (GateError, OSError):
        result = evaluate(None, None, None, now=0)
    print(json.dumps(result))
    return 1  # No qualified publisher/evidence source exists; inspection cannot authorize a merge.


if __name__ == '__main__':
    sys.exit(main())
