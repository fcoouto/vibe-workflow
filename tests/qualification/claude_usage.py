"""Unqualified, unauthenticated Claude metadata wire-contract tooling, not a runtime source.

Only trusted test/operator code supplies an explicit command prefix. The fixed bare-mode flags and two
controls exercise the known protocol without a prompt. There is no CLI, discovery, persistence or caller
in production. Synthetic tests establish transport mechanics, not native startup safety or subscription
authority. Source identity, authentication, freshness, overflow semantics and scope mapping remain open.
"""
from datetime import datetime
import json
import math
import os
import re
import selectors
import stat
import subprocess
import time

from runtime import codex_rate_limits as lifecycle


FLAGS = ('--bare', '--safe-mode', '--print', '--input-format', 'stream-json', '--output-format',
         'stream-json', '--verbose', '--no-session-persistence', '--tools', '', '--strict-mcp-config',
         '--mcp-config', '{"mcpServers":{}}', '--settings', '{"disableAllHooks":true}', '--setting-sources', '')
MAX_LINE = 256 << 10
MAX_BYTES = 1 << 20
MAX_MESSAGES = 8
ENV_ALLOWLIST = ('HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR')
REASONS = ('claude_command_invalid', 'claude_environment_invalid', 'claude_timeout_invalid',
           'claude_unavailable', 'claude_exited', 'claude_timeout', 'claude_protocol_invalid',
           'claude_protocol_unexpected', 'claude_protocol_error', 'claude_metadata_invalid',
           'claude_inference_unverified', 'claude_behaviors_unverified', 'claude_meter_invalid',
           'claude_scope_unsupported')
ISO_RESET = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
                       r'(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-5][0-9])')


class ReadError(Exception):
    """A fixed reason only; native text and identifiers never become an error message."""

    def __init__(self, reason):
        super().__init__(reason if type(reason) is str and reason in REASONS else 'claude_protocol_invalid')


def _scopes(value):
    for key in ('model_scoped', 'limits'):
        raw = value.get(key)
        if raw is not None and not (type(raw) in (dict, list) and not raw):
            raise ReadError('claude_scope_unsupported')
    if any(value.get(key) is not None for key in ('seven_day_oauth_apps', 'seven_day_opus', 'seven_day_sonnet')):
        raise ReadError('claude_scope_unsupported')


def _meter(raw):
    if raw is None:
        return None
    if type(raw) is not dict:
        raise ReadError('claude_meter_invalid')
    percent, reset = raw.get('utilization'), raw.get('resets_at')
    if percent is not None and (type(percent) not in (int, float) or not 0 <= percent <= 100
                                or not math.isfinite(percent)):
        raise ReadError('claude_meter_invalid')
    if reset is not None:
        if type(reset) is not str or not ISO_RESET.fullmatch(reset):
            raise ReadError('claude_meter_invalid')
        try:
            datetime.fromisoformat(reset.replace('Z', '+00:00'))
        except ValueError:
            raise ReadError('claude_meter_invalid') from None
    if percent is None or reset is None:
        return None  # a partial meter is absent, never spare capacity
    return dict(utilization=percent, resets_at=reset)


def parse(initialization, usage):
    """Project response payloads into nonsecret diagnostics; never produce a billing fact or routing window."""
    if type(initialization) is not dict or type(usage) is not dict:
        raise ReadError('claude_metadata_invalid')
    session = usage.get('session')
    if type(session) is not dict or any(type(session.get(key)) not in (int, float) or session[key] != 0
                                       for key in ('total_cost_usd', 'total_api_duration_ms')) \
            or type(session.get('model_usage')) is not dict or session['model_usage']:
        raise ReadError('claude_inference_unverified')
    if 'behaviors' not in usage or usage['behaviors'] is not None:
        raise ReadError('claude_behaviors_unverified')
    subscription, available = usage.get('subscription_type'), usage.get('rate_limits_available')
    if subscription not in (None, 'pro', 'max', 'team', 'enterprise') or type(available) is not bool:
        raise ReadError('claude_metadata_invalid')
    _scopes(usage)
    limits = usage.get('rate_limits')
    if limits is None:
        limits = {}
    if type(limits) is not dict:
        raise ReadError('claude_metadata_invalid')
    if set(limits) - {'five_hour', 'seven_day', 'extra_usage', 'model_scoped', 'limits',
                      'seven_day_oauth_apps', 'seven_day_opus', 'seven_day_sonnet'}:
        raise ReadError('claude_scope_unsupported')
    _scopes(limits)
    meters = {}
    for key in ('five_hour', 'seven_day'):
        row = _meter(limits.get(key))
        if row is not None:
            meters[key] = row
    extra, enabled = limits.get('extra_usage'), None
    if extra is not None:
        if type(extra) is not dict or 'is_enabled' in extra and type(extra['is_enabled']) is not bool:
            raise ReadError('claude_metadata_invalid')
        enabled = extra.get('is_enabled')
    return dict(qualified=False, reason='claude_source_unqualified', subscription_type=subscription,
                rate_limits_available=available, extra_usage_enabled=enabled, aggregate_meters=meters)


def _private_dir(value):
    if type(value) is not str or not os.path.isabs(value):
        return False
    try:
        info = os.lstat(value)
        return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
    except (OSError, ValueError):
        return False


def _environment(environ):
    if type(environ) is not dict or not _private_dir(environ.get('HOME')):
        raise ReadError('claude_environment_invalid')
    env = {key: environ[key] for key in ENV_ALLOWLIST if type(environ.get(key)) is str and environ[key]}
    env['PATH'] = ':'.join(part for part in env.get('PATH', '').split(':') if part.startswith('/'))
    if 'TMPDIR' in env and not _private_dir(env['TMPDIR']):
        del env['TMPDIR']
    if any('\0' in value for value in env.values()):
        raise ReadError('claude_environment_invalid')
    return env


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate_key')
        value[key] = item
    return value


def _constant(value):
    raise ValueError('nonfinite_number')


class _Session:
    def __init__(self, process, deadline):
        self.process, self.deadline = process, deadline
        self.buffer, self.total, self.seen, self.notice = b'', 0, 0, False
        self.selector = selectors.DefaultSelector()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            self.selector.register(process.stdout, selectors.EVENT_READ)
        except OSError:
            self.selector.close()
            raise

    def send(self, ident, request):
        message = dict(type='control_request', request_id=ident, request=request)
        try:
            self.process.stdin.write((json.dumps(message) + '\n').encode())
            self.process.stdin.flush()
        except OSError:
            raise ReadError('claude_exited') from None

    def line(self):
        while b'\n' not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise ReadError('claude_protocol_invalid')
            left = self.deadline - time.monotonic()
            if left <= 0 or not self.selector.select(left):
                raise ReadError('claude_timeout')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                if self.buffer:
                    raise ReadError('claude_protocol_invalid')
                return None
            self.total += len(chunk)
            if self.total > MAX_BYTES:
                raise ReadError('claude_protocol_invalid')
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        if len(line) > MAX_LINE:
            raise ReadError('claude_protocol_invalid')
        self.seen += 1
        if self.seen > MAX_MESSAGES:
            raise ReadError('claude_protocol_unexpected')
        return line

    def response(self, ident):
        while True:
            line = self.line()
            if line is None:
                raise ReadError('claude_exited')
            try:
                value = json.loads(line.decode('utf-8'), object_pairs_hook=_object, parse_constant=_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise ReadError('claude_protocol_invalid') from None
            if type(value) is not dict:
                raise ReadError('claude_protocol_invalid')
            if value.get('type') == 'system':
                if ident != 'contract-init' or self.notice \
                        or set(value) != {'type', 'subtype', 'event', 'uuid', 'session_id'} \
                        or value['subtype'] != 'ui_invalidate' or value['event'] != 'ui.render' \
                        or any(type(value[key]) is not str for key in ('uuid', 'session_id')):
                    raise ReadError('claude_protocol_unexpected')
                self.notice = True
                continue
            if set(value) != {'type', 'response'} or value['type'] != 'control_response':
                raise ReadError('claude_protocol_unexpected')
            response = value['response']
            if type(response) is not dict or response.get('request_id') != ident:
                raise ReadError('claude_protocol_unexpected')
            if response.get('subtype') != 'success' or type(response.get('response')) is not dict:
                raise ReadError('claude_protocol_error')
            if set(response) - {'subtype', 'request_id', 'response', 'pending_permission_requests',
                                'pending_user_dialog_requests'} \
                    or any(response.get(key, []) != [] for key in ('pending_permission_requests',
                                                                  'pending_user_dialog_requests')):
                raise ReadError('claude_protocol_unexpected')
            return response['response']


def exchange(command, environ, timeout=5.0):
    """One explicit child, fixed bare-mode flags, initialize/get_usage, safe projection and bounded cleanup.

    The prefix is trusted operator/test input, never request-configurable production authority. This tool
    provides no sandbox: no invocation of a real client is authorized by its presence or its tests.
    """
    if type(command) not in (list, tuple) or not command \
            or any(type(part) is not str or '\0' in part for part in command) \
            or not os.path.isabs(command[0]):
        raise ReadError('claude_command_invalid')
    if type(timeout) not in (int, float) or not 0 < timeout <= 5 or not math.isfinite(timeout):
        raise ReadError('claude_timeout_invalid')
    env = _environment(environ)
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen([*command, *FLAGS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=env, cwd='/', close_fds=True,
                                   start_new_session=True)
    except (OSError, ValueError):
        raise ReadError('claude_unavailable') from None
    session, normal = None, False
    try:
        session = _Session(process, deadline)
        session.send('contract-init', dict(subtype='initialize', hooks=None))
        initialization = session.response('contract-init')
        session.send('contract-usage', dict(subtype='get_usage', skip_behaviors=True))
        projected = parse(initialization, session.response('contract-usage'))
        process.stdin.close()
        if session.line() is not None:
            raise ReadError('claude_protocol_unexpected')  # reject even a buffered post-response message
        if not lifecycle._exited(process.pid, max(0, deadline - time.monotonic())):
            raise ReadError('claude_timeout')
        exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        if exited.si_code != os.CLD_EXITED or exited.si_status != 0:
            raise ReadError('claude_exited')  # leave the leader unreaped until failed group cleanup
        normal = True
    except OSError:
        raise ReadError('claude_protocol_invalid') from None
    finally:
        if session is not None:
            session.selector.close()
        lifecycle._stop(process, normal)  # close, grace, TERM/KILL the owned group, then reap
    if process.returncode != 0:
        raise ReadError('claude_exited')
    return projected
