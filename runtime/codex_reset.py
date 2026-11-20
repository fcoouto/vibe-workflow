"""One bounded read of the Codex login's rate-limit reset time (`codex app-server`, `account/rateLimits/read`).

Only `initialize`, `initialized` and the read are sent; a server request is never answered and Codex's auth file is
never opened. Any failure is None: the caller falls back to a fixed pause."""
import json
import os
import selectors
import subprocess
import time

from runtime import processes as lifecycle

TIMEOUT = 5.0
MAX_LINE = 256 << 10
MAX_MESSAGES = 64
ENV_ALLOWLIST = ('PATH', 'HOME', 'CODEX_HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR')
HANDSHAKE = dict(id=1, method='initialize', params=dict(clientInfo=dict(
    name='vibe_workflow_reset', title='vibe-workflow reset reader', version='1')))


def _command(environ):
    for folder in lifecycle._absolute_path(environ.get('PATH')).split(':'):
        candidate = os.path.join(folder, 'codex') if folder else ''
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate, 'app-server']
    raise lifecycle.ReadError('codex_unavailable')


def _env(environ):
    env = {key: environ[key] for key in ENV_ALLOWLIST if type(environ.get(key)) is str and environ[key]}
    if 'CODEX_HOME' in env and not os.path.isabs(env['CODEX_HOME']):
        raise lifecycle.ReadError('codex_home_invalid')
    env['PATH'] = lifecycle._absolute_path(env.get('PATH'))
    if 'TMPDIR' in env and not lifecycle._private_dir(env['TMPDIR']):
        del env['TMPDIR']
    return env


def _result(process, deadline, ident, buffer):
    """The `result` of response `ident` (notifications skipped); anything else is a ReadError."""
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        for _ in range(MAX_MESSAGES):
            while b'\n' not in buffer[0]:
                left = deadline - time.monotonic()
                if left <= 0 or not selector.select(left):
                    raise lifecycle.ReadError('codex_timeout')
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk or len(buffer[0]) > MAX_LINE:
                    raise lifecycle.ReadError('codex_exited')
                buffer[0] += chunk
            line, buffer[0] = buffer[0].split(b'\n', 1)
            try:
                message = json.loads(line)
            except ValueError:
                raise lifecycle.ReadError('codex_protocol_invalid') from None
            if type(message) is not dict:
                raise lifecycle.ReadError('codex_protocol_invalid')
            if 'method' in message and 'id' not in message:
                continue
            if 'method' in message or message.get('id') != ident or type(message.get('result')) is not dict:
                raise lifecycle.ReadError('codex_protocol_error')
            return message['result']
    raise lifecycle.ReadError('codex_protocol_unexpected')


def _exchange(command, env, timeout):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, cwd='/', start_new_session=True, close_fds=True)
    deadline, buffer, normal = time.monotonic() + timeout, [b''], False
    os.set_blocking(process.stdout.fileno(), False)
    try:
        for message in (HANDSHAKE, None, dict(id=2, method='account/rateLimits/read')):
            if message is None:
                _result(process, deadline, 1, buffer)
                message = dict(method='initialized')
            process.stdin.write((json.dumps(message) + '\n').encode())
            process.stdin.flush()
        result = _result(process, deadline, 2, buffer)
        normal = True
        return result
    finally:
        lifecycle._stop(process, normal)


def read_reset(*, now, timeout=TIMEOUT, environ=None, command=None):
    """The `resetsAt` (unix seconds) of the Codex window with the highest `usedPercent` that resets after `now`,
    else None. Never under the test runner's private home unless an explicit environ and command are given."""
    environ = os.environ if environ is None else environ
    try:
        if environ.get('VCW_TEST_HOME'):
            return None
        snapshot = _exchange(command or _command(environ), _env(environ), timeout).get('rateLimits')
        windows = [snapshot.get(key) for key in ('primary', 'secondary')] if type(snapshot) is dict else []
        found = [(item['usedPercent'], item['resetsAt']) for item in windows
                 if type(item) is dict and type(item.get('usedPercent')) in (int, float)
                 and type(item.get('resetsAt')) is int and item['resetsAt'] > now]
        return max(found)[1] if found else None
    except (lifecycle.ReadError, OSError, ValueError, subprocess.SubprocessError):
        return None
