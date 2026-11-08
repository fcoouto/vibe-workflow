#!/usr/bin/env python3
"""Deliver one OpenCode title from {session_id, title, baseline?, adopt?}; always print JSON and exit 0."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    try:
        from runtime.opencode_titles import deliver
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict) or not set(request) <= {'session_id', 'title', 'baseline', 'adopt'}:
            raise ValueError
        result = deliver(request.get('session_id'), request.get('title'), request.get('baseline'),
                         adopt=request.get('adopt', False))
    except Exception:
        result = dict(ok=False, deferred=False, reason='invalid_request')
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
