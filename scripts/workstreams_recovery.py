#!/usr/bin/env python3
"""Private shared-monitor controls; native App operations run through supported tools."""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def main():
    from runtime import parent_recovery
    from runtime.startup import MAX_INPUT, StartupError, json_object, acquire_project_entry, _entry_scope
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('status', 'register', 'withdraw', 'reactivate', 'suspend',
        'schedule', 'record-schedule', 'observe', 'record-send', 'reconcile-feature'))
    parser.add_argument('--cwd', type=Path)
    parser.add_argument('--full', action='store_true', help='Include complete private registry details for diagnosis')
    args = parser.parse_args()
    try:
        request = json_object(sys.stdin.buffer.read(MAX_INPUT + 1))
        directory = parent_recovery.default_directory()
        if args.action in {'register', 'reconcile-feature'}:
            if args.cwd is None:
                raise ValueError('Recovery activation requires the enrolled checkout')
            acquisition = acquire_project_entry(SimpleNamespace(client='codex', cwd=args.cwd), PACKAGE)
            scope = _entry_scope(acquisition)
            if scope is None or scope.runtime is None:
                raise ValueError('Recovery activation requires an enrolled workflow task')
            if args.action == 'register':
                report = parent_recovery.register(scope, directory, request)
            else:
                if request:
                    raise ValueError('Feature reconciliation takes no request fields')
                report = parent_recovery.reconcile_feature(scope, directory)
        elif args.action == 'reactivate':
            report = parent_recovery.reactivate(directory, request, PACKAGE)
        elif args.action == 'observe':
            report = parent_recovery.observe(directory, request, PACKAGE)
        else:
            report = parent_recovery.control(directory, args.action, request)
        if not args.full:
            report = parent_recovery.compact_result(report, args.action, request.get('thread_id', request.get('session_id')))
        print(json.dumps(report))
        return 0
    except (StartupError, ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
