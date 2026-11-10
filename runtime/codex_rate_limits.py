"""R7: a read-only reader of the Codex account's rate-limit windows (`codex app-server`, `account/rateLimits/read`).

It keeps the ChatGPT-subscription pool's window readings fresh. Codex's own app-server reports the windows the
account has (a weekly one on some plans, a 5-hour one as well on others), so they are discovered, never assumed.
The read spends no tokens and calls no model: the client sends only the `initialize` handshake and
`account/rateLimits/read` (`METHODS`), and never answers a server request.

Lightweight by rule (user decision, 2026-11-10): a read happens only on demand, when a `prepare` needs a
chatgpt-subscription route whose reading is missing or stale, or when the operator runs
`scripts/codex_rate_limits.py`. Never on a timer, at startup, from `status` or under `--serve`. One short-lived
process per read, at most one per pool at a time (`reader_busy` for the others), no retry: a failure is a fixed
reason and backs off for `FAILURE_BACKOFF` seconds, during which the route stays stale and fails closed.

The user's Codex login is used read-only: the child gets an allowlisted environment (no API key or other
credential-like variable reaches it), runs in `/`, and the auth file is only stat-ed before and after, never
opened; a change fails the read (`codex_auth_changed`). Account ids, plan types and error texts are never kept.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import time

from runtime import routing_observations as observations, usage_budget

POOL = 'chatgpt-subscription'
LIMIT_ID = 'codex'
METHODS = ('initialize', 'initialized', 'account/rateLimits/read')
CLIENT_NAME = 'vibe_workflow_rate_limits'
# Codex reports window lengths in minutes; only lengths with a manifest window id are understood.
WINDOW_IDS = {300: '5h', 10080: '7d'}
LIMIT = 1000  # observations.WINDOW_UNIT: thousandths of the provider's utilisation
# 5 s bounds one read: the app-server starts in well under a second and the read is one backend request; a
# `prepare` that needs it waits at most this long, and a read slower than that is treated as failed.
TIMEOUT = 5.0
# A parallel `prepare` waits for the reader in flight (its timeout plus this) instead of failing at once; it then
# finds that reading fresh. Polling a non-blocking lock: no extra process and no timer.
LOCK_EXTRA = 1.0
LOCK_POLL = 0.05
# The stop sequence protects the user's login: close stdin (Codex exits on its own), then SIGTERM, then SIGKILL to
# the process group, each after this grace.
STOP_GRACE = 0.3
FAILURE_BACKOFF = 300  # seconds without a new spawn after a failed read (no retry loop)
MAX_LINE = 256 << 10
MAX_MESSAGES = 64
ENV_ALLOWLIST = ('PATH', 'HOME', 'CODEX_HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR')
WINDOW_KEYS = {'usedPercent', 'windowDurationMins', 'resetsAt'}  # required; unknown extra keys are ignored
STATE_FILE = 'codex-rate-limits.json'
LOCK_FILE = '.codex-rate-limits.lock'
REASONS = ('reader_busy', 'codex_unavailable', 'codex_method_refused', 'codex_home_invalid', 'codex_exited', 'codex_timeout', 'codex_protocol_invalid', 'codex_protocol_error',
           'codex_protocol_unexpected', 'codex_auth_changed', 'codex_limits_invalid', 'codex_window_invalid',
           'codex_window_unknown', 'codex_window_undeclared', 'codex_pool_undeclared', 'codex_windows_absent',
           'codex_window_missing', 'observation_store_unsafe', 'observation_store_invalid', 'route_manifest_invalid')


class ReadError(Exception):
    """A fixed reason from REASONS; never a server text, token or account id."""


def applies(route):
    """Whether a manifest route's windows come from this reader: an included-subscription route on the pool."""
    return type(route) is dict and route.get('pool') == POOL and route.get('billing_class') == 'included_subscription'


def declared_windows(manifest):
    """The window ids the manifest knows for the pool: its `pools` entry, else the demand of its routes."""
    pool = (manifest.get('pools') or {}).get(POOL) if type(manifest.get('pools')) is dict else None
    if type(pool) is dict and type(pool.get('windows')) is list:
        return {item['id'] for item in pool['windows'] if type(item) is dict and type(item.get('id')) is str}
    return {key for route in manifest.get('routes', []) if type(route) is dict and route.get('pool') == POOL
            and type(route.get('demand')) is dict for key in route['demand']}


# -- the child process -------------------------------------------------------------------------------------------
def _absolute_path(value):
    return ':'.join(item for item in (value or '').split(':') if item.startswith('/'))


def _private_dir(value):
    try:
        info = os.lstat(value)
    except (OSError, ValueError):
        return False
    return value.startswith('/') and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() \
        and info.st_mode & 0o022 == 0


def child_env(environ):
    """The allowlisted environment: relative PATH entries dropped, TMPDIR only when a private absolute directory,
    and `codex_home_invalid` for a relative CODEX_HOME."""
    env = {key: environ[key] for key in ENV_ALLOWLIST if type(environ.get(key)) is str and environ[key]}
    if 'CODEX_HOME' in env and not os.path.isabs(env['CODEX_HOME']):
        raise ReadError('codex_home_invalid')
    env['PATH'] = _absolute_path(env.get('PATH'))
    if 'TMPDIR' in env and not _private_dir(env['TMPDIR']):
        del env['TMPDIR']
    return env


def default_command(environ):
    """`[<absolute codex>, 'app-server']` found on the absolute PATH entries only, or `codex_unavailable`. Under the
    test runner's private home (`VCW_TEST_HOME`, tests/__init__.py) the real Codex is never started."""
    if environ.get('VCW_TEST_HOME'):
        raise ReadError('codex_unavailable')
    for folder in _absolute_path(environ.get('PATH')).split(':'):
        candidate = os.path.join(folder, 'codex') if folder else ''
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate, 'app-server']
    raise ReadError('codex_unavailable')


def _auth_mark(env):
    """Metadata of the Codex auth file (never its content), to detect a write during the read: the entry itself and,
    through a symlink, its target. `absent` when there is no file (keyring storage); a file appearing is a change."""
    path = os.path.join(env.get('CODEX_HOME') or os.path.join(env.get('HOME', '/nonexistent'), '.codex'), 'auth.json')
    marks = []
    for read in (os.lstat, os.stat):
        try:
            info = read(path)
        except OSError:
            marks.append(None)
            continue
        marks.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return 'absent' if marks == [None, None] else tuple(marks)


def _message(method, ident=None):
    if method not in METHODS:
        raise ReadError('codex_method_refused')  # nothing else is ever sent
    value = dict(method=method)
    if ident is not None:
        value['id'] = ident
    if method == 'initialize':
        value['params'] = dict(clientInfo=dict(name=CLIENT_NAME, title='vibe-workflow rate-limit reader', version='1'))
    return (json.dumps(value) + '\n').encode()


class _Session:
    def __init__(self, process, deadline):
        self.process, self.deadline, self.buffer, self.seen = process, deadline, b'', 0
        self.selector = selectors.DefaultSelector()
        os.set_blocking(process.stdout.fileno(), False)
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def send(self, method, ident=None):
        try:
            self.process.stdin.write(_message(method, ident))
            self.process.stdin.flush()
        except OSError:
            raise ReadError('codex_exited') from None

    def _line(self):
        while b'\n' not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise ReadError('codex_protocol_invalid')
            left = self.deadline - time.monotonic()
            if left <= 0 or not self.selector.select(left):
                raise ReadError('codex_timeout')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise ReadError('codex_exited')
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        if len(line) > MAX_LINE:
            raise ReadError('codex_protocol_invalid')
        return line

    def response(self, ident):
        """The `result` of response `ident`; notifications are skipped, anything else fails closed."""
        while True:
            self.seen += 1
            if self.seen > MAX_MESSAGES:
                raise ReadError('codex_protocol_unexpected')
            try:
                message = json.loads(self._line())
            except ValueError:
                raise ReadError('codex_protocol_invalid') from None
            if type(message) is not dict:
                raise ReadError('codex_protocol_invalid')
            if 'method' in message and 'id' not in message:
                continue  # a notification
            if 'method' in message or message.get('id') != ident:
                raise ReadError('codex_protocol_unexpected')  # a server request is never answered
            if 'error' in message or type(message.get('result')) is not dict:
                raise ReadError('codex_protocol_error')
            return message['result']


def _exited(pid, seconds):
    """Whether the leader exited within `seconds`, polled without reaping it: while it is unreaped its pid and
    process group id cannot be reused, so a group signal reaches only its own group."""
    end = time.monotonic() + seconds
    while True:
        try:
            if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                return True
        except ChildProcessError:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(LOCK_POLL)


def _stop(process, normal):
    """Close stdin and let Codex exit on its own. After a normal read that exits within STOP_GRACE, nothing is
    signalled. Otherwise (a timeout, any failure, or a leader still running) the group gets SIGTERM, then SIGKILL,
    STOP_GRACE apart, both before the leader is reaped; the SIGKILL also reaches anything it started."""
    try:
        process.stdin.close()
    except OSError:
        pass
    if not (_exited(process.pid, STOP_GRACE) and normal):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except OSError:
                pass  # the group is already gone
            if sig == signal.SIGTERM:
                _exited(process.pid, STOP_GRACE)
        _exited(process.pid, 1)
    try:
        process.wait(1)  # reap last: no group signal follows
    except subprocess.TimeoutExpired:
        pass
    try:
        process.stdout.close()  # only now: a late write never meets a closed pipe
    except OSError:
        pass


def exchange(command, environ, timeout=TIMEOUT, timing=None):
    """One short-lived app-server: `initialize`, `initialized`, `account/rateLimits/read`, then kill its group."""
    env = child_env(environ)
    before = _auth_mark(env)
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=env, cwd='/', start_new_session=True, close_fds=True)
    except OSError:
        raise ReadError('codex_unavailable') from None
    normal = False
    try:
        session = _Session(process, started + timeout)
        session.send('initialize', 1)
        session.response(1)
        if timing is not None:
            timing['spawn_ms'] = int((time.monotonic() - started) * 1000)
        session.send('initialized')
        session.send('account/rateLimits/read', 2)
        result = session.response(2)
        normal = True
    finally:
        _stop(process, normal)
        if timing is not None:
            timing['wall_ms'] = int((time.monotonic() - started) * 1000)
    if _auth_mark(env) != before:
        raise ReadError('codex_auth_changed')
    return result


# -- parsing -----------------------------------------------------------------------------------------------------
def _snapshot(result):
    by_limit = result.get('rateLimitsByLimitId')
    if type(by_limit) is dict and LIMIT_ID in by_limit:
        snapshot = by_limit[LIMIT_ID]
    else:
        snapshot = result.get('rateLimits')
        if type(snapshot) is dict and snapshot.get('limitId', LIMIT_ID) not in (LIMIT_ID, None):
            raise ReadError('codex_limits_invalid')
    if type(snapshot) is not dict:
        raise ReadError('codex_limits_invalid')
    return snapshot


def _window(raw, now, exhausted):
    if type(raw) is not dict or not WINDOW_KEYS <= set(raw):
        raise ReadError('codex_window_invalid')
    percent, minutes, reset = raw['usedPercent'], raw['windowDurationMins'], raw['resetsAt']
    if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100 \
            or type(minutes) is not int or type(reset) is not int:
        raise ReadError('codex_window_invalid')
    if minutes not in WINDOW_IDS:
        raise ReadError('codex_window_unknown')
    length = minutes * 60
    if not now < reset <= now + length + observations.CLOCK_MARGIN:
        raise ReadError('codex_window_invalid')
    used = LIMIT if exhausted else min(LIMIT, math.ceil(percent * LIMIT / 100 - 1e-9))
    return dict(id=WINDOW_IDS[minutes], limit=LIMIT, used=used, length=length, reset_at=reset, rolling=False,
                unit=observations.WINDOW_UNIT, observed_at=now)


def parse(result, now, declared):
    """The account's windows as pool window observations, or ReadError. `ordinaryUsageAllowed: false` means no
    headroom whatever the percentages (Codex: clients must not infer recovery from them)."""
    if type(result) is not dict:
        raise ReadError('codex_limits_invalid')
    allowed = result.get('ordinaryUsageAllowed')
    if allowed is not None and type(allowed) is not bool:
        raise ReadError('codex_limits_invalid')
    snapshot = _snapshot(result)
    windows = [_window(snapshot[key], now, allowed is False) for key in ('primary', 'secondary')
               if snapshot.get(key) is not None]
    if not windows:
        raise ReadError('codex_windows_absent')
    if len({item['id'] for item in windows}) != len(windows):
        raise ReadError('codex_window_invalid')
    if not declared:
        raise ReadError('codex_pool_undeclared')
    if not {item['id'] for item in windows} <= set(declared):
        raise ReadError('codex_window_undeclared')  # a window admission would not see: fail closed
    return windows


def read_windows(*, command=None, environ=None, timeout=TIMEOUT, manifest=None, timing=None):
    """One read: the pool's window observations, or ReadError."""
    environ = os.environ if environ is None else environ
    try:
        manifest = observations.load_manifest() if manifest is None else manifest
    except (OSError, ValueError):
        raise ReadError('route_manifest_invalid') from None
    result = exchange(command or default_command(environ), environ, timeout, timing)
    return parse(result, int(time.time()), declared_windows(manifest))


# -- on demand, once per pool ------------------------------------------------------------------------------------
def _store_dir(settings_path):
    folder = observations._resolved(observations.window_path(settings_path)).parent
    for item in reversed([p for p in (folder, *folder.parents) if not p.exists()]):
        item.mkdir(mode=0o700, exist_ok=True)  # concurrent prepares may race here
    try:
        usage_budget.private(folder, True)
    except usage_budget.BudgetError:
        raise ReadError('observation_store_unsafe') from None
    return folder


@contextmanager
def _pool_lock(settings_path, pool, wait=0.0):
    """Yields True once this process holds the pool's reader lock, polling the non-blocking lock for up to `wait`
    seconds, or False when another reader still holds it then."""
    folder = _store_dir(settings_path)
    fd = os.open(folder / ('%s.%s' % (LOCK_FILE, pool)), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        end = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    yield False
                    return
                time.sleep(LOCK_POLL)
        yield True
    finally:
        os.close(fd)


def _fresh(settings_path, need, now):
    stored = {item.get('id'): item for item in observations.LocalSource(settings_path).windows(POOL, now)}
    return bool(stored) and all(type(stored.get(key, {}).get('observed_at')) is int
                                and 0 <= now - stored[key]['observed_at'] <= observations.FRESH_SECONDS
                                for key in (need or stored))


def _state(folder):
    try:
        return observations._read(folder / STATE_FILE).get('pools', {}).get(POOL)
    except (OSError, ValueError):
        return None


def _record(folder, failure):
    with observations._locked(folder / STATE_FILE) as path:
        value = observations._read(path) or dict(version=1, pools={})
        value['pools'][POOL] = failure
        observations._write(path, value)


def refresh(settings_path, *, need=None, force=False, command=None, environ=None, timeout=TIMEOUT, manifest=None,
            timing=None):
    """Make the pool's reading fresh if it is not: `{state, reason?, windows?}`, never raises.

    `fresh`: already fresh (a settled launch's, a seed's or an earlier read's), no process. `busy`: another reader
    still holds the pool after its timeout plus LOCK_EXTRA. `backoff`: the last read failed less than FAILURE_BACKOFF s ago. `force` (the operator's
    command) reads even then, and even over a fresh reading. `stored`: read and stored as host observations. `failed`: a fixed reason."""
    try:
        if not force and _fresh(settings_path, need, int(time.time())):
            return dict(state='fresh')
        with _pool_lock(settings_path, POOL, timeout + LOCK_EXTRA) as held:
            if not held:
                return dict(state='busy', reason='reader_busy')
            now = int(time.time())
            if not force and _fresh(settings_path, need, now):  # another reader may just have stored one
                return dict(state='fresh')
            folder = _store_dir(settings_path)
            failure = _state(folder)
            if not force and type(failure) is dict and type(failure.get('at')) is int \
                    and 0 <= now - failure['at'] < FAILURE_BACKOFF:
                return dict(state='backoff', reason=failure.get('reason'))
            try:
                windows = read_windows(command=command, environ=environ, timeout=timeout, manifest=manifest,
                                       timing=timing)
                try:
                    for item in windows:
                        observations.store_window(settings_path, POOL, item)
                except (OSError, ValueError) as error:  # partway: back off as for any failed read
                    raise ReadError(str(error) if str(error) in REASONS else 'observation_store_invalid') from None
                if need and not set(need) <= {item['id'] for item in windows}:
                    raise ReadError('codex_window_missing')
            except ReadError as error:
                _record(folder, dict(at=int(time.time()), reason=str(error)))
                return dict(state='failed', reason=str(error))
            if failure is not None:
                _record(folder, None)
            return dict(state='stored', windows=[item['id'] for item in windows])
    except ReadError as error:
        return dict(state='failed', reason=str(error))
    except (OSError, ValueError) as error:
        reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
        return dict(state='failed', reason=reason)
