#!/usr/bin/env python3
"""Local bridge used by the trusted Codex host after destination approval.

It exchanges metadata and ciphertext only. The host owns user confirmation and
the connector call. Starting this process confers no account or spend authority.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import sys
import termios
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import usage_settings, usage_setup

_FRAME_LIMIT = 4096


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _read_frame(timeout):
    deadline = time.monotonic() + timeout
    data = bytearray()
    fd = sys.stdin.fileno()
    while len(data) < _FRAME_LIMIT:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise TimeoutError()
        chunk = os.read(fd, _FRAME_LIMIT - len(data))
        if not chunk:
            break
        data.extend(chunk)
        if b'\n' in data:
            if data.count(b'\n') != 1 or not data.endswith(b'\n'):
                break
            return json.loads(data, object_pairs_hook=_unique)
    raise ValueError()


def exchange(read_frame, write_frame, *, node_executable):
    write_frame({'ready': True})
    selection = read_frame(20)
    # The trusted host obtains explicit approval before starting this bridge.
    # There is deliberately no approval field in the local protocol.
    with usage_setup.prepare_setup(selection, lambda _: True,
                                   node_executable=node_executable) as pending:
        write_frame({'creation_arguments': pending.creation_arguments()})
        try:
            encrypted = read_frame(120)
        except (ValueError, OSError):
            encrypted = None
        receipt = pending.finish(encrypted)
    write_frame({'receipt': receipt})


@contextmanager
def _private_terminal():
    if not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    before = termios.tcgetattr(fd)
    private = before.copy()
    private[3] &= ~(termios.ECHO | termios.ICANON)
    private[6] = before[6].copy()
    private[6][termios.VMIN] = 1
    private[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, private)
    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, before)


def _write(value):
    print(json.dumps(value, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--node', required=True, help='Absolute qualified Node executable')
    args = parser.parse_args()
    try:
        usage_settings.scope(args.cwd)
    except (ValueError, OSError):
        _write({'ok': False, 'reason': 'enrollment_unavailable_or_changed'})
        return 1
    try:
        with _private_terminal():
            exchange(_read_frame, _write, node_executable=args.node)
        return 0
    except usage_setup.SetupError as error:
        _write({'ok': False, 'reason': str(error)})
    except (ValueError, OSError):
        _write({'ok': False, 'reason': 'credential_setup_unavailable'})
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
