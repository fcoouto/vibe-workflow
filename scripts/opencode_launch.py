#!/usr/bin/env python3
"""Run one Smart Routing managed launch on OpenCode (C.3 design slice C3-3; clients/opencode.md#managed-launcher).

Usage: opencode_launch.py < request.json

The request is one JSON object: `{"v": 1, "op": "launch", "checkout", "assignment", "job", "prompt", "workflow"?,
"constraints"?, "planned"?, "profile"?}`. For a managed project the command starts a private OpenCode server for the
checkout (`opencode_launcher.start_server`), runs the attempt through `scripts.routing.managed_service`, stops the
server and prints one JSON result. An unmanaged project answers `project_not_managed` and starts nothing.

The result never carries the dispatch token, the server password or the prompt. A launch that cannot be confirmed
names the one-shot operator op (`scripts/routing.py <op>`) for the operator to run; this command never runs one.
Blocked is an outcome (exit 0); only a usage error exits 2. SIGTERM, SIGHUP and Ctrl-C become `interrupted`
(exit 1) after the server is stopped and a dispatched hold is recorded `uncertain`.
"""
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import opencode_launcher, routing_launch  # noqa: E402
from scripts import routing as command  # noqa: E402

VERSION = 1
MAX_INPUT = 1 << 20
REQUIRED = {'checkout', 'assignment', 'job', 'prompt'}
OPTIONAL = {'workflow', 'constraints', 'planned', 'profile'}


SIGNALS = (signal.SIGTERM, signal.SIGHUP)
WATCHED = SIGNALS + (signal.SIGINT,)


class Interrupted(BaseException):
    """SIGTERM or SIGHUP, raised where the command is, so every `finally` runs (the server is stopped and the
    launcher records `uncertain` for a dispatched hold) instead of the process ending with its server alive."""


def _interrupt(signum, frame):
    for number in WATCHED:  # one conversion only: no second signal or Ctrl-C can interrupt the cleanup
        signal.signal(number, signal.SIG_IGN)
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise Interrupted(signum)


def _blocked(reason):
    return dict(state='blocked', reason=reason)


def _request(raw):
    """The launch request, or a fixed refusal reason. Input text is never echoed."""
    if len(raw) > MAX_INPUT:
        return None, 'request_too_large'
    try:
        value = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None, 'invalid_json'
    if type(value) is not dict or value.get('v') != VERSION or value.get('op') != 'launch':
        return None, 'invalid_request'
    body = {key: item for key, item in value.items() if key not in ('v', 'op')}
    if not REQUIRED <= set(body) <= REQUIRED | OPTIONAL or type(body['checkout']) is not str \
            or type(body['prompt']) is not str or not body['prompt'] or type(body['assignment']) is not dict \
            or type(body.get('planned', False)) is not bool:
        return None, 'invalid_request'
    return body, None


def run(body):
    checkout = Path(body['checkout']).resolve()
    if not checkout.is_dir():
        return _blocked('invalid_request')
    service = command.managed_service(checkout)
    server = None
    try:
        if not routing_launch.managed(service.project, service.settings_path):
            return dict(state='unmanaged', reason=routing_launch.PROJECT_NOT_MANAGED)
        try:
            server = opencode_launcher.start_server(checkout, settings_path=service.settings_path,
                                                    ledger_path=service.ledger_path)
        except (opencode_launcher.ServerUnavailable, OSError):
            return _blocked(opencode_launcher.HOST_UNHEALTHY)
        return opencode_launcher.launch(checkout, host=server, service=service, assignment=body['assignment'],
                                        job=body['job'], prompt=body['prompt'], workflow=body.get('workflow'),
                                        constraints=body.get('constraints'), planned=body.get('planned', False),
                                        profile=body.get('profile'))
    finally:
        if server is not None:
            server.stop()  # ends the worker too, whatever the outcome
        service.close()


def main(argv=None, stdin=None, write=None):
    write = write or (lambda line: (sys.stdout.write(line + '\n'), sys.stdout.flush()))
    if (sys.argv[1:] if argv is None else argv):
        write(json.dumps(dict(v=VERSION, **_blocked('usage_error'))))
        return 2
    raw = stdin if stdin is not None else sys.stdin.buffer.read(MAX_INPUT + 1)
    body, reason = _request(raw)
    previous = {number: signal.signal(number, _interrupt) for number in WATCHED}
    code = 0
    try:
        result = _blocked(reason) if body is None else run(body)
    except (Interrupted, KeyboardInterrupt):  # `run` stopped the server; the launcher recorded what it had to
        result, code = _blocked('interrupted'), 1
    except Exception:  # noqa: BLE001 - a fixed reason, never a traceback, input text or secret
        result = _blocked('internal_error')
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    write(json.dumps(dict(v=VERSION, **result), sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
