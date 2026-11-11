#!/usr/bin/env python3
"""Prepare selected templates or check explicit documents; never modify project files."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.template_tools import check, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("prepare", "check"):
        command = commands.add_parser(operation)
        command.add_argument("--cwd", required=True)
        command.add_argument("--template", action="append", default=[])
        if operation == "prepare":
            command.add_argument("--context", action="append", default=[])
            command.add_argument("--page", type=int, default=1)
            command.add_argument("--revision")
        else:
            command.add_argument("--document", action="append", default=[])
            command.add_argument("--before")
            command.add_argument("--require", action="append", default=[])
            command.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            result = prepare(args.cwd, args.template, args.context, page=args.page, revision=args.revision)
        else:
            required = {}
            for item in args.require:
                path, separator, digest = item.rpartition("=")
                if not separator or path in required:
                    raise ValueError("digest_invalid")
                required[path] = digest
            result = check(args.cwd, args.document, args.template, before=args.before, required=required)
            if args.quiet and result["ok"]:
                return 0
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result["ok"] else 1
    except (ValueError, OSError, TypeError):
        # Neither arbitrary input nor local exception details belong in diagnostics.
        print(json.dumps(dict(ok=False, error="template_operation_unavailable")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
