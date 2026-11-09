#!/usr/bin/env python3
"""Local settings, quotes and Smart Balance decisions; the CLI sends no provider requests."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import usage_routing
from runtime.usage_budget import BudgetError, fields


def distinct(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BudgetError('duplicate_field')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description='Local Usage Routing controls. Live requests require an approved host.')
    parser.add_argument('action', choices=('status', 'quote', 'configure', 'disable', 'enable', 'evaluate', 'run', 'reconcile', 'handoff', 'claim_handoff'))
    parser.add_argument('--cwd')
    args = parser.parse_args()
    try:
        if args.action == 'status':
            result = usage_routing.status(args.cwd)
        else:
            raw = sys.stdin.buffer.read(usage_routing.MAX_INPUT + 1)
            if len(raw) > usage_routing.MAX_INPUT:
                raise BudgetError('request_too_large')
            request = json.loads(raw, object_pairs_hook=distinct,
                                 parse_constant=lambda value: (_ for _ in ()).throw(BudgetError('invalid_number')))
            if args.action == 'quote':
                fields(request, 'version packet profile now')
                if type(request['version']) is not int or request['version'] != 1:
                    raise BudgetError('invalid_version')
                result = usage_routing.quote(request['packet'], request['profile'], request['now'])
            else:
                result = getattr(usage_routing, args.action)(args.cwd, request)
        print(json.dumps(result, allow_nan=False, sort_keys=True))
        return 0
    except BudgetError as error:
        print(json.dumps({'ok': False, 'reason': str(error)}))
        return 1
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        print(json.dumps({'ok': False, 'reason': 'invalid_offline_request'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
