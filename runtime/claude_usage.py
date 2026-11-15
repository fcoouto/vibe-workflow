"""The Claude account and usage reader for the native Claude path (15 November account-based amendment).

One bounded zero-inference exchange with the native client: two newline-delimited control requests, `initialize`
and `get_usage` with `skip_behaviors: true`, and no user or assistant message. The native client keeps its own
sign-in and settings; hooks, tools, MCP servers and session persistence are off for the read. From one exchange it
derives the account key (a digest of email, organization when present and sign-in type: raw identifiers are never
stored), the native catalog snapshot (`native_catalog.store_snapshot`) and, only from the examined successful
raw-limits branch, the pool's 5h/7d windows dated conservatively at request start minus the client's 60 s cache
ceiling. A fallback reply without raw limits, a scoped or unmatched raw limit, and malformed or partial meters leave
usage unknown, never zero or spare. Extra Usage is recorded as `enabled`, `disabled` or `unknown` and is never a
dispatch prerequisite: an enabled account's provider applies its own paid-overflow configuration.

On demand only (a demanded stale route, preactivation or native preparation), never from `status`, a preview, a
pure check or `--serve`. One reader at a time, a fixed reason and FAILURE_BACKOFF after a failure, no retry. Under
the test runner's private home the installed client is never found.
"""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import selectors
import subprocess
import time

from runtime import codex_rate_limits as lifecycle, routing, routing_observations as observations, routing_paths, \
    usage_budget

PATH = 'claude-native-subscription'
POOL = 'claude-subscription'
CLIENT = 'claude-code'
CONTROLS = 'claude-agent-alias-effort'  # the bridge sends only a catalog alias and a listed effort
FLAGS = ('--safe-mode', '--print', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
         '--no-session-persistence', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
         '--settings', '{"disableAllHooks":true}')
NOTICE = dict(type='system', subtype='ui_invalidate', event='ui.render')  # the one startup notice permitted
TIMEOUT = 8.0
LOCK_EXTRA = 1.0
FAILURE_BACKOFF = 300
CACHE_CEILING = 60  # seconds: the client may answer from a cache up to this old (installed-source evidence)
MAX_LINE = 1 << 20
MAX_MESSAGES = 16
STORE_FILE = 'claude-account.json'
LOCK_FILE = '.claude-account.lock'
ENV_ALLOWLIST = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'SHELL')
ENV_PREFIXES = ('CLAUDE_', 'ANTHROPIC_')  # the native client's own account and provider selection, passed unchanged
SUBSCRIPTIONS = ('pro', 'max', 'team', 'enterprise')
METERS = dict(five_hour='5h', seven_day='7d')
SCOPED_METERS = ('seven_day_oauth_apps', 'seven_day_opus', 'seven_day_sonnet')
KNOWN_RATE = {*METERS, *SCOPED_METERS, 'extra_usage', 'limits', 'model_scoped'}
LIMIT_SCOPES = ('model', 'surface')
ISO = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)')
REASONS = ('reader_busy', 'claude_unavailable', 'claude_exited', 'claude_timeout', 'claude_protocol_invalid',
           'claude_protocol_unexpected', 'claude_protocol_error', 'claude_account_unsupported',
           'claude_usage_unconfirmed', 'claude_usage_invalid', 'claude_limits_scoped', 'claude_limits_unsupported',
           'claude_window_missing', 'claude_catalog_invalid', 'observation_store_unsafe', 'observation_store_invalid')


class ReadError(Exception):
    """A fixed reason from REASONS; never native text, an identifier or a credential."""


def applies(route):
    """Whether this reader supplies a route's account and windows: a subscription route on its path (a dispatch
    recheck's route names no billing class; its path is subscription-only)."""
    return type(route) is dict and route.get('billing_class', 'included_subscription') == 'included_subscription' \
        and routing_paths.path_of(route.get('route')) == PATH


# -- the child process -------------------------------------------------------------------------------------------
def _installed(environ):
    if environ.get('VCW_TEST_HOME'):
        raise ReadError('claude_unavailable')
    for folder in lifecycle._absolute_path(environ.get('PATH')).split(':'):
        candidate = os.path.join(folder, 'claude') if folder else ''
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate]
    raise ReadError('claude_unavailable')


def default_command(environ):
    """`[<absolute claude>]` from absolute PATH entries, or `claude_unavailable`. Only code replaces it."""
    return _installed(environ)


def child_env(environ):
    env = {key: value for key, value in environ.items() if type(value) is str and value
           and (key in ENV_ALLOWLIST or key.startswith(ENV_PREFIXES))}
    env['PATH'] = lifecycle._absolute_path(env.get('PATH'))
    if 'TMPDIR' in env and not lifecycle._private_dir(env['TMPDIR']):
        del env['TMPDIR']
    return env


def _request(ident, subtype):
    request = dict(subtype=subtype, hooks=None) if subtype == 'initialize' else dict(subtype=subtype,
                                                                                     skip_behaviors=True)
    return (json.dumps(dict(type='control_request', request_id=ident, request=request)) + '\n').encode()


class _Session:
    def __init__(self, process, deadline):
        self.process, self.deadline, self.buffer, self.seen = process, deadline, b'', 0
        self.selector = selectors.DefaultSelector()
        os.set_blocking(process.stdout.fileno(), False)
        self.selector.register(process.stdout, selectors.EVENT_READ)

    def send(self, ident, subtype):
        try:
            self.process.stdin.write(_request(ident, subtype))
            self.process.stdin.flush()
        except OSError:
            raise ReadError('claude_exited') from None

    def _line(self):
        while b'\n' not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise ReadError('claude_protocol_invalid')
            left = self.deadline - time.monotonic()
            if left <= 0 or not self.selector.select(left):
                raise ReadError('claude_timeout')
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise ReadError('claude_exited')
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        return line

    def response(self, ident):
        """The success payload of control `ident`; only the known startup notice may come before it."""
        while True:
            self.seen += 1
            if self.seen > MAX_MESSAGES:
                raise ReadError('claude_protocol_unexpected')
            try:
                message = json.loads(self._line())
            except ValueError:
                raise ReadError('claude_protocol_invalid') from None
            if type(message) is not dict:
                raise ReadError('claude_protocol_invalid')
            if all(message.get(key) == value for key, value in NOTICE.items()):
                continue  # its opaque session values are discarded
            body = message.get('response')
            if message.get('type') != 'control_response' or type(body) is not dict \
                    or body.get('request_id') != ident:
                raise ReadError('claude_protocol_unexpected')
            if body.get('subtype') != 'success' or type(body.get('response')) is not dict:
                raise ReadError('claude_protocol_error')
            return body['response']


def exchange(command, environ, timeout=TIMEOUT):
    """(initialize payload, usage payload, wall time just before `get_usage` was sent); the group is stopped after."""
    try:
        process = subprocess.Popen([*command, *FLAGS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=child_env(environ), cwd='/',
                                   start_new_session=True, close_fds=True)
    except OSError:
        raise ReadError('claude_unavailable') from None
    normal = False
    try:
        session = _Session(process, time.monotonic() + timeout)
        session.send('vw-init', 'initialize')
        init = session.response('vw-init')
        started = time.time()
        session.send('vw-usage', 'get_usage')
        use = session.response('vw-usage')
        normal = True
    finally:
        lifecycle._stop(process, normal)
    return init, use, started


# -- parsing -----------------------------------------------------------------------------------------------------
def account_key(account):
    """The nonsecret account key: email, organization when present and sign-in type, digested."""
    raw = json.dumps([account.get('email', '').strip().casefold(), account.get('organization') or None,
                      account.get('tokenSource') or None], separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def parse_account(init):
    """`(key, client_version, models)` of a first-party subscription sign-in, else `claude_account_unsupported`."""
    account = init.get('account')
    if type(account) is not dict or not routing._text(account.get('email')) \
            or account.get('apiProvider') != 'firstParty' or account.get('subscriptionType') not in SUBSCRIPTIONS \
            or account.get('apiKeySource') not in (None, 'none') \
            or not routing._optional(account.get('organization'), routing._text):
        raise ReadError('claude_account_unsupported')
    from runtime.adapters import native_catalog
    models = native_catalog.from_claude_initialize(init)
    version = init.get('claude_code_version')
    if not models or not routing._text(version):
        raise ReadError('claude_catalog_invalid')
    return account_key(account), version, models


def _time(value):
    if type(value) is not str or not ISO.fullmatch(value):
        raise ReadError('claude_usage_invalid')
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        raise ReadError('claude_usage_invalid') from None


def _percent(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ReadError('claude_usage_invalid')
    return value


def _overflow(rate):
    extra = rate.get('extra_usage')
    if extra is None:
        return 'unknown'
    if type(extra) is not dict or type(extra.get('is_enabled', False)) is not bool and extra['is_enabled'] is not None:
        raise ReadError('claude_usage_invalid')
    return {True: 'enabled', False: 'disabled'}.get(extra.get('is_enabled'), 'unknown')


def parse_usage(use, started, now):
    """`(windows, overflow)`; windows are [] unless the reply is the successful raw-limits branch, in which case each
    raw limit must be unscoped and equal (percent and reset) to an aggregate meter it is enforced through."""
    rate = use.get('rate_limits')
    if use.get('rate_limits_available') is not True or type(rate) is not dict:
        return [], 'unknown'
    if set(rate) - KNOWN_RATE:
        raise ReadError('claude_limits_unsupported')
    overflow = _overflow(rate)
    if any(rate.get(key) is not None for key in SCOPED_METERS) or rate.get('model_scoped') not in (None, []):
        raise ReadError('claude_limits_scoped')
    meters = {}
    for key, window in METERS.items():
        meter = rate.get(key)
        if meter is None:
            continue
        if type(meter) is not dict:
            raise ReadError('claude_usage_invalid')
        meters[window] = (_percent(meter.get('utilization')), _time(meter.get('resets_at')))
    if 'limits' not in rate:
        return [], overflow  # header-seeded fallback: no fresh time, so no reading
    limits = rate['limits']
    if type(limits) is not list or len(limits) > 32:
        raise ReadError('claude_usage_invalid')
    for item in limits:
        if type(item) is not dict or not routing._text(item.get('kind')):
            raise ReadError('claude_usage_invalid')
        if any(item.get(scope) is not None for scope in LIMIT_SCOPES):
            raise ReadError('claude_limits_scoped')
        if not routing._optional(item.get('is_active'), lambda value: type(value) is bool):
            raise ReadError('claude_usage_invalid')
        shape = (_percent(item.get('percent')), _time(item.get('resets_at')))
        if shape not in meters.values():
            raise ReadError('claude_limits_unsupported')
    observed = max(0, math.floor(started) - CACHE_CEILING)
    found = []
    for window, (percent, reset) in meters.items():
        length = observations.RATE_WINDOWS[next(key for key, item in METERS.items() if item == window)][1]
        if not now < reset <= now + length + observations.CLOCK_MARGIN:
            raise ReadError('claude_usage_invalid')
        found.append(dict(id=window, limit=observations.WINDOW_UNITS, length=length, reset_at=reset, rolling=False,
                          used=min(observations.WINDOW_UNITS, math.ceil(percent * observations.WINDOW_UNITS / 100
                                                                        - 1e-9)),
                          unit=observations.WINDOW_UNIT, observed_at=observed))
    return found, overflow


# -- the private store -------------------------------------------------------------------------------------------
def _store(settings_path):
    return observations._resolved(observations.window_path(settings_path)).parent / STORE_FILE


def _state(settings_path):
    try:
        return observations._read(_store(settings_path))
    except (OSError, ValueError):
        return {}


def account(settings_path, now):
    """The current account record `{key, observed_at, client_version, overflow}`, or None when absent or stale."""
    if settings_path is None:
        return None
    found = _state(settings_path).get('account')
    if type(found) is not dict or not routing._amount(found.get('observed_at')) \
            or not (0 <= now - found['observed_at'] <= observations.FRESH_SECONDS):
        return None
    return found


def billing_fact(settings_path, route, now):
    """An account-bound fact for `route` (`subscription_billing.problem`'s shape) while the account read is current;
    `paid_crossover` reports the account's own overflow state and gates nothing."""
    from runtime import subscription_billing
    current = account(settings_path, now) if applies(route) else None
    if current is None:
        return None
    state = dict(enabled='provider_managed', disabled='provider_blocked').get(current['overflow'], 'unknown')
    return dict(producer='claude-native:' + current['key'][:16], **subscription_billing.scope(route),
                observed_at=current['observed_at'], valid_until=current['observed_at'] + observations.FRESH_SECONDS,
                paid_crossover=state)


def binding(settings_path):
    """The native catalog binding of the current account read, or None."""
    current = account(settings_path, int(time.time()))
    if current is None:
        return None
    return dict(client_version=current['client_version'], connection='account:' + current['key'], controls=CONTROLS)


@contextmanager
def _lock(settings_path, wait):
    folder = lifecycle._store_dir(settings_path)
    fd = os.open(folder / LOCK_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
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
                time.sleep(lifecycle.LOCK_POLL)
        yield True
    finally:
        os.close(fd)


def _save(settings_path, **change):
    with observations._locked(_store(settings_path)) as path:
        value = observations._read(path) or dict(version=1)
        value.update(change)
        observations._write(path, value)


def _fresh(settings_path, need, now):
    if account(settings_path, now) is None:
        return False
    stored = {item['id']: item for item in observations.LocalSource(settings_path).windows(POOL, now)}
    boundary = observations.consumed_at(settings_path, POOL)
    return all(key in stored and 0 <= now - stored[key]['observed_at'] <= observations.FRESH_SECONDS
               and stored[key]['observed_at'] > boundary for key in need)


def _record(settings_path, init, use, started):
    """Store one exchange's account, catalog and windows. An account change first makes every earlier reading of
    the pool stale (the consumed boundary), so another account's headroom is never reused."""
    from runtime.adapters import native_catalog
    key, version, models = parse_account(init)
    now = int(time.time())
    previous = _state(settings_path).get('account')
    if type(previous) is dict and previous.get('key') != key:
        observations.mark_consumed(settings_path, POOL, max(0, math.floor(started) - CACHE_CEILING),
                                   'account:%s:%d' % (key, now))
    try:
        windows, overflow, problem = (*parse_usage(use, started, now), None)
    except ReadError as error:  # the account and catalog are still this exchange's; only usage stays unknown
        windows, overflow, problem = [], 'unknown', error
    _save(settings_path, account=dict(key=key, observed_at=now, client_version=version, overflow=overflow))
    native_catalog.store_snapshot(settings_path, CLIENT, dict(binding=dict(
        client_version=version, connection='account:' + key, controls=CONTROLS), models=models))
    if problem is not None:
        raise problem
    for item in windows:
        observations.store_window(settings_path, POOL, item)
    return windows


def refresh(settings_path, *, need=None, force=False, command=None, environ=None, timeout=TIMEOUT):
    """Make the account read (and the demanded windows) current: `{state, reason?, windows?}`, never raises.

    `fresh`: nothing to do, no process. `busy`: another reader still holds the lock. `backoff`: the last read
    failed less than FAILURE_BACKOFF s ago. `stored`: one exchange stored. `failed`: a fixed reason, backing off."""
    environ = os.environ if environ is None else environ
    need = tuple(need) if need else tuple(sorted(METERS.values()))  # the path's 5h and 7d by default
    try:
        if not force and _fresh(settings_path, need, int(time.time())):
            return dict(state='fresh')
        with _lock(settings_path, timeout + LOCK_EXTRA) as held:
            if not held:
                return dict(state='busy', reason='reader_busy')
            now = int(time.time())
            if not force and _fresh(settings_path, need, now):
                return dict(state='fresh')
            failure = _state(settings_path).get('failure')
            if not force and type(failure) is dict and routing._amount(failure.get('at')) \
                    and 0 <= now - failure['at'] < FAILURE_BACKOFF:
                return dict(state='backoff', reason=failure.get('reason'))
            try:
                init, use, started = exchange(command or default_command(environ), environ, timeout)
                windows = _record(settings_path, init, use, started)
                if not windows:
                    raise ReadError('claude_usage_unconfirmed')
                if not set(need) <= {item['id'] for item in windows}:
                    raise ReadError('claude_window_missing')
                if not _fresh(settings_path, need, int(time.time())):
                    # A cached reply may predate the pool's last settled launch: unusable, and read again only
                    # after the cache ceiling, not on every preparation.
                    _save(settings_path, failure=dict(at=int(time.time()) - FAILURE_BACKOFF + CACHE_CEILING,
                                                      reason='claude_usage_unconfirmed'))
                    return dict(state='failed', reason='claude_usage_unconfirmed')
            except ReadError as error:
                _save(settings_path, failure=dict(at=int(time.time()), reason=str(error)))
                return dict(state='failed', reason=str(error))
            except (OSError, ValueError):
                _save(settings_path, failure=dict(at=int(time.time()), reason='observation_store_invalid'))
                return dict(state='failed', reason='observation_store_invalid')
            _save(settings_path, failure=None)
            return dict(state='stored', windows=[item['id'] for item in windows])
    except ReadError as error:
        return dict(state='failed', reason=str(error))
    except (OSError, ValueError, usage_budget.BudgetError):
        return dict(state='failed', reason='observation_store_invalid')

