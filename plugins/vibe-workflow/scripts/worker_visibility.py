#!/usr/bin/env python3
"""Prepare native role declarations and publish optional worker labels."""

import argparse
import json
import os
from pathlib import Path
import signal
import sys

PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from runtime import worker_visibility as visibility
from runtime.habblaud import enrich_start, forward_start, publish_native, restore_badge
from runtime.worker_visibility import prepare_assignment, prepare_context, prepare_managed, prepare_native, read_context, hook_context_path
from runtime.startup import json_object


def main():
    if sys.argv[1:2] == ['forward-start']:
        parser = argparse.ArgumentParser(description='Forward the existing Habblaud start hook')
        parser.add_argument('--context-directory', required=True, type=Path)
        parser.add_argument('--checkout', type=Path)
        parser.add_argument('--codex-executable', required=True, type=Path)
        parser.add_argument('command', nargs=argparse.REMAINDER)
        args = parser.parse_args(sys.argv[2:])
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            parser.error('supply the original hook command after --')
        return forward_start(args.context_directory, args.checkout, args.codex_executable, command)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'publish', 'restore'))
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument('--context', type=Path)
    destination.add_argument('--context-directory', type=Path)
    parser.add_argument('--visibility-plugin', type=Path)
    parser.add_argument('--habblaud-endpoint')
    parser.add_argument('--habblaud-account')
    args = parser.parse_args()
    try:
        request = json_object(sys.stdin.buffer.read(visibility.LIMIT + 1), limit=visibility.LIMIT)
        if args.action == 'prepare':
            if 'message' in request:
                result = prepare_assignment(request, context=args.context, context_directory=args.context_directory)
                if ('worker_id' in request and result['prepared'] and args.visibility_plugin
                        and args.habblaud_endpoint and args.habblaud_account):
                    result['badge'] = restore_badge(result['context'], args.visibility_plugin,
                                                   args.habblaud_endpoint, args.habblaud_account)
            else:
                path, result = prepare_context(request, context=args.context, context_directory=args.context_directory)
                if args.context is None:
                    result['context'] = str(path)
        elif args.action == 'restore':
            if request or not all((args.context, args.visibility_plugin, args.habblaud_endpoint, args.habblaud_account)):
                raise ValueError('invalid_visibility_restore_request')
            result = restore_badge(args.context, args.visibility_plugin, args.habblaud_endpoint, args.habblaud_account)
        else:
            fields = {'worker_id', 'phase'}
            if (set(request) not in (fields, fields | {'parent_turn_id', 'spawn_item_id'}) or not args.context or not args.visibility_plugin
                    or not args.habblaud_endpoint or not args.habblaud_account):
                raise ValueError('invalid_visibility_publish_request')
            result = publish_native(args.context, request['worker_id'], request['phase'],
                args.visibility_plugin, args.habblaud_endpoint, args.habblaud_account,
                parent_turn_id=request.get('parent_turn_id'), spawn_item_id=request.get('spawn_item_id'))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError, RuntimeError, KeyError):
        print(json.dumps(dict(delivered=False, reason='invalid_visibility_request')))
        return 1


if __name__ == '__main__':
    status = main()
    if status < 0:
        number = -status
        if number not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)
    raise SystemExit(status)
