#!/usr/bin/env python3
"""Local OpenRouter consent controls; no account, credential or model requests."""
import argparse
import json
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))


def main():
    if sys.version_info < (3, 11):
        print(json.dumps(dict(ok=False, error='python_3_11_required')))
        return 1
    from runtime import openrouter_settings as settings
    from runtime.startup import StartupError, json_object
    class Parser(argparse.ArgumentParser):
        def error(self, message): raise StartupError('invalid_openrouter_arguments')
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('action', choices=('configure', 'status', 'enable', 'disable'))
        parser.add_argument('--cwd', required=True)
        args = parser.parse_args()
        raw = b'{}' if args.action == 'status' and sys.stdin.isatty() else sys.stdin.buffer.read(settings.MAX_CONFIG + 1)
        request = json_object(raw or b'{}', limit=settings.MAX_CONFIG)
        if args.action == 'status':
            settings.require(not request, 'invalid_openrouter_status_request')
            result = settings.status(Path(args.cwd))
        else:
            result = getattr(settings, args.action)(Path(args.cwd), request)
        print(json.dumps(result, separators=(',', ':')))
        return 0
    except settings.SettingsError as error:
        reason = str(error)
    except (StartupError, OSError, ValueError, TypeError, KeyError, ImportError, RecursionError):
        reason = 'invalid_openrouter_input_or_runtime'
    print(json.dumps(dict(ok=False, error=reason), separators=(',', ':')))
    return 1


if __name__ == '__main__': sys.exit(main())
