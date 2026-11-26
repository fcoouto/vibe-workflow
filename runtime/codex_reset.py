"""One bounded read of the Codex login's rate limits (`codex app-server`, `account/rateLimits/read`).

Only `initialize`, `initialized` and the read are sent; a server request is never answered and Codex's auth file is
never opened. `read_reset` gives the reset time a ChatGPT pause uses: any failure is None, and the caller falls back to
a fixed pause. `read_windows` gives the plan windows as ranking meters: any failure is no meter. Neither reads an
account, a credit balance or another method."""
import json
import math
import os
import selectors
import subprocess
import sys
import time

from runtime import processes as lifecycle, storage

TIMEOUT = 5.0
BUCKET = 'codex'  # the documented plan-wide limit ID; other buckets and slot names never name a scope
BOOST_MINUTES = {300: '5h', 10_080: '7d'}  # the verified shapes; any other length only protects headroom
MAX_MINUTES = 366 * 1440
# The ranking read's guard: a short-lived parent of Codex, leader of its own process group. Its alarm kills that group
# when the read's bound passes, whether or not the worker that started it is still alive. It never touches the pipes.
# It outlives the worker's group SIGTERM, so a worker that dies before its SIGKILL leaves the deadline armed. The
# handler is caught, not ignored: Codex starts with the default SIGTERM action, as without a guard.
GUARD = '''import os, signal, subprocess, sys
signal.signal(signal.SIGTERM, lambda *args: None)
signal.signal(signal.SIGALRM, lambda *args: os.killpg(0, signal.SIGKILL))
signal.setitimer(signal.ITIMER_REAL, float(sys.argv[1]))
sys.exit(subprocess.call(sys.argv[2:]))
'''
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
    else None. Every failure is None; it refuses whenever the given environ sets `VCW_TEST_HOME` (tests pass an
    explicit environ without it, with a fake command)."""
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
    except (lifecycle.ReadError, OSError, ValueError, RecursionError, subprocess.SubprocessError):
        return None


def _claim(window, received):
    """`(window minutes, reading or None)` of one slot that states a plausible window length, else None. The reading is
    `(used percent, reset)` when the percentage is valid and the reset lies ahead, within one window of the receipt."""
    minutes = window.get('windowDurationMins') if type(window) is dict else None
    if type(minutes) is not int or not 0 < minutes <= MAX_MINUTES:
        return None
    used, reset = window.get('usedPercent'), window.get('resetsAt')
    valid = type(used) in (int, float) and 0 <= used <= 100 and type(reset) is int \
        and received < reset <= received + minutes * 60 + storage.CLOCK_MARGIN
    return minutes, (used, reset) if valid else None


def _claims(snapshot, received):
    """Every reading a rate-limit snapshot states per window length; the slot a window sits in decides nothing."""
    found = {}
    for slot in ('primary', 'secondary'):
        claim = _claim(snapshot.get(slot), received)
        if claim is not None:
            found.setdefault(claim[0], []).append(claim[1])
    return found


def _windows(result, started, received):
    """Ranking meters of the `codex` bucket in one answer. Its entry under `rateLimitsByLimitId` decides which meters
    exist; without that entry, the single view does. Either must state `limitId: "codex"` itself: the map key alone
    proves no scope, since it can stand for a missing ID. A window length whose readings disagree, within one view or
    between the two, is dropped alone."""
    listed, single = result.get('rateLimitsByLimitId'), result.get('rateLimits')
    views = []
    if type(listed) is dict and BUCKET in listed:
        entry = listed[BUCKET]
        if type(entry) is not dict or entry.get('limitId') != BUCKET:
            return {}  # the preferred entry does not name the bucket itself: nothing else may stand in for it
        views.append(_claims(entry, received))
    if type(single) is dict and single.get('limitId') == BUCKET:
        views.append(_claims(single, received))
    meters = {}
    for minutes, readings in (views[0] if views else {}).items():
        readings = readings + [reading for view in views[1:] for reading in view.get(minutes, [])]
        if readings[0] is None or any(reading != readings[0] for reading in readings):
            continue
        key = BOOST_MINUTES.get(minutes, 'duration:%d' % (minutes * 60))
        meters[key] = dict(used_percent=readings[0][0], earliest=started, latest=received, reset_at=readings[0][1],
                           duration=minutes * 60, discrete=minutes in BOOST_MINUTES)
    return meters


def read_windows(*, timeout=TIMEOUT, environ=None, command=None):
    """The Codex plan windows as ranking meters, keyed `5h`, `7d` or `duration:<seconds>`; empty on every failure.

    `earliest` is stamped just before the bounded exchange and `latest` when it returns: the interval bounds this
    exchange, not the age of the provider's accounting. Only a 5-hour or weekly window has a verified discrete reset.
    It refuses under `VCW_TEST_HOME`, as `read_reset` does. Codex runs under `GUARD`, so a worker killed mid-exchange
    cannot leave it running past the bound, even when it ignores the end of its input."""
    environ = os.environ if environ is None else environ
    try:
        if environ.get('VCW_TEST_HOME'):
            return {}
        command, env = command or _command(environ), _env(environ)
        guarded = [sys.executable, '-I', '-B', '-S', '-c', GUARD, repr(timeout + lifecycle.STOP_GRACE), *command]
        started = math.floor(time.time())
        result = _exchange(guarded, env, timeout)
        return _windows(result, started, math.ceil(time.time()))
    except (lifecycle.ReadError, OSError, ValueError, RecursionError, subprocess.SubprocessError):
        return {}
