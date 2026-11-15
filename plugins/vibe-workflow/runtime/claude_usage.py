"""The Claude account and usage reader for the native Claude path (15 November account-based amendment).

One bounded zero-inference exchange with the native client: two newline-delimited control requests, `initialize`
and `get_usage` with `skip_behaviors: true`, and no user or assistant message. The native client keeps its own
sign-in and settings; hooks, tools, MCP servers and session persistence are off for the read. From one exchange it
derives the account key (a digest of email, organization when present and the claude.ai sign-in type: raw identifiers
are never stored), the native catalog snapshot (`native_catalog.store_snapshot`) and, only from the examined
successful branch, the pool's 5h/7d windows dated conservatively at request start minus the client's 60 s cache
ceiling. Installed-source evidence (2.1.311, bytes only): a claude.ai subscriber's initialization reports email,
organization and a display plan label ("Claude Max") with no `tokenSource` (environment OAuth tokens and keys report
one; the normalized subscription type is in the usage answer, and only it classifies the sign-in), and a
successful usage answer keeps the raw `limits` array and adds `model_scoped: []` when no per-model window exists,
while a header-seeded fallback strips both. The only raw limit kind with known meaning is `weekly_scoped` under
`scope.model`. So any raw limit element, scoped meter or unknown key is refused rather than mapped or ignored, and a
fallback, partial or malformed answer leaves usage unknown, never zero or spare. Session telemetry must show zero
inference (cost, API duration, model usage) and no transcript behaviors, or nothing at all is stored.

Windows are stored bound to the account key and the exchange (under `<window>@<key prefix>` with `account`, so they
never replace another client's row of the shared pool, nor it theirs; and the observed time): the native consumer uses
only rows of the current account's latest confirmed exchange (`usable_windows`), so another client's or account's
reading of the shared pool, or an earlier exchange's window beside a partial newer one, is never its headroom.
Extra Usage is recorded as `enabled`, `disabled` or `unknown` and is never a dispatch prerequisite: an enabled
account's provider applies its own paid-overflow configuration.

The child keeps the user's native sign-in and provider selection (`CLAUDE_*`/`ANTHROPIC_*` and the user's settings
files) because changing them would read another login. It cannot run extra code or inference through them: no
prompt is ever sent, hooks are disabled by `--settings`, MCP is limited to an empty `--strict-mcp-config`, tools are
empty, sessions are not persisted, `skip_behaviors` skips the transcript scan, and the telemetry check above refuses
any answer showing inference. Only the derived key, client version, overflow state, times and fixed reasons are
written; no environment value, credential or native text is stored or logged.

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
SIGN_IN = 'claude.ai'  # the one sign-in type accepted: a native claude.ai subscriber login
METERS = dict(five_hour='5h', seven_day='7d')
SCOPED_METERS = ('seven_day_oauth_apps', 'seven_day_opus', 'seven_day_sonnet')
UNKNOWN_METERS = ('cinder_cove', 'wattle_ember')  # known server keys without evidenced meaning
KNOWN_RATE = {*METERS, *SCOPED_METERS, *UNKNOWN_METERS, 'extra_usage', 'limits', 'model_scoped'}
SESSION = {'total_cost_usd', 'total_api_duration_ms', 'total_duration_ms', 'total_lines_added', 'total_lines_removed',
           'model_usage'}
ISO = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)')
REASONS = ('reader_busy', 'claude_unavailable', 'claude_exited', 'claude_timeout', 'claude_protocol_invalid',
           'claude_protocol_unexpected', 'claude_protocol_error', 'claude_account_unsupported',
           'claude_usage_unconfirmed', 'claude_usage_invalid', 'claude_limits_scoped', 'claude_limits_unsupported',
           'claude_inference_observed',
           'claude_window_missing', 'claude_catalog_invalid', 'observation_store_unsafe', 'observation_store_invalid')


ACCOUNT_REFUSALS = ('claude_account_unsupported', 'claude_inference_observed')


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
    """The nonsecret account key: email, organization when present and the accepted sign-in type, digested."""
    raw = json.dumps([account.get('email', '').strip().casefold(), account.get('organization') or None, SIGN_IN],
                     separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def parse_account(init, use):
    """`(key, client_version, models)` of a claude.ai subscriber sign-in, else `claude_account_unsupported`: a
    first-party provider, an email, a known subscription type and neither a `tokenSource` (an environment token or
    other source) nor an `apiKeySource` (a key). The subscription type is the normalized one of the same exchange's
    usage answer: initialization carries only the user-visible plan label ("Claude Max"), which is never matched."""
    account = init.get('account')
    if type(account) is not dict or not routing._text(account.get('email')) \
            or account.get('apiProvider') != 'firstParty' or use.get('subscription_type') not in SUBSCRIPTIONS \
            or 'tokenSource' in account and account['tokenSource'] is not None \
            or 'apiKeySource' in account and account['apiKeySource'] is not None \
            or not routing._optional(account.get('organization'), routing._text):
        raise ReadError('claude_account_unsupported')
    from runtime.adapters import native_catalog
    models = native_catalog.from_claude_initialize(init)
    version = init.get('claude_code_version')
    if not models or not routing._text(version):
        raise ReadError('claude_catalog_invalid')
    return account_key(account), version, models


def _zero(value, kind):
    return type(value) is kind and value == 0


def check_quiet(use):
    """Refuse an answer showing inference or transcript scanning, before anything is stored."""
    session = use.get('session')
    if type(session) is not dict or not set(session) <= SESSION or type(session.get('model_usage')) is not dict \
            or type(use.get('rate_limits_available')) is not bool \
            or use.get('subscription_type') not in (None, *SUBSCRIPTIONS):
        raise ReadError('claude_usage_invalid')
    if not (_zero(session.get('total_cost_usd'), int) or _zero(session.get('total_cost_usd'), float)) \
            or not _zero(session.get('total_api_duration_ms'), int) or session['model_usage']:
        raise ReadError('claude_inference_observed')
    if use.get('behaviors') is not None:
        raise ReadError('claude_usage_invalid')  # skip_behaviors was ignored: a transcript scan ran


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
    """`(windows, overflow)`. Windows come only from a complete successful answer: both meters, the raw `limits`
    array present and empty, and `model_scoped: []`; anything else is a fixed reason, never a partial reading."""
    rate = use.get('rate_limits')
    if use.get('rate_limits_available') is not True or type(rate) is not dict:
        raise ReadError('claude_usage_unconfirmed')
    if set(rate) - KNOWN_RATE or any(rate.get(key) is not None for key in UNKNOWN_METERS):
        raise ReadError('claude_limits_unsupported')
    overflow = _overflow(rate)
    if any(rate.get(key) is not None for key in SCOPED_METERS) or rate.get('model_scoped') not in (None, []):
        raise ReadError('claude_limits_scoped')
    meters = {}
    for key, window in METERS.items():
        meter = rate.get(key)
        if meter is None:
            raise ReadError('claude_window_missing')
        if type(meter) is not dict:
            raise ReadError('claude_usage_invalid')
        meters[window] = (_percent(meter.get('utilization')), _time(meter.get('resets_at')))
    limits = rate.get('limits')
    if type(limits) is list:
        for item in limits:
            scope = item.get('scope') if type(item) is dict else None
            scoped = item.get('kind') == 'weekly_scoped' and type(scope) is dict and scope.get('model') is not None \
                if type(item) is dict else False
            raise ReadError('claude_limits_scoped' if scoped else 'claude_limits_unsupported')
    elif limits is not None:
        raise ReadError('claude_usage_invalid')
    if limits is None or rate.get('model_scoped') != []:
        raise ReadError('claude_usage_unconfirmed')  # a cached or header-seeded answer: no fresh time
    observed = max(0, math.floor(started) - CACHE_CEILING)
    found = []
    for key, window in METERS.items():
        percent, reset = meters[window]
        length = observations.RATE_WINDOWS[key][1]
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


def bound_id(window, key):
    """The store key of this account's reading of `window` (a key-digest prefix, never an identifier)."""
    return '%s@%s' % (window, key[:12])


def usable_windows(settings_path, now, stored=None):
    """The pool rows the native consumer may use: bound to the current account and observed no earlier than its
    latest confirmed exchange (`usage_at`; none after a failed, partial or fallback read). `stored` is one read of
    the window store when the caller has it."""
    current = account(settings_path, now)
    if current is None or not routing._amount(current.get('usage_at')):
        return []
    rows = observations.LocalSource(settings_path).windows(POOL, now, stored=stored)
    by_id = {bound_id(window, current['key']): window for window in METERS.values()}
    return [dict(row, id=by_id[row['id']]) for row in rows if row['id'] in by_id
            and row.get('account') == current['key'] and row['observed_at'] >= current['usage_at']]


def confirmation(settings_path):
    """What a native snapshot's readings depend on beyond the window store: the stored account key and confirmed
    exchange time (both None when absent), read without a freshness cut so that any change is visible."""
    found = _state(settings_path).get('account')
    found = found if type(found) is dict else {}
    return [found.get('key'), found.get('usage_at')]


def usable(settings_path, now):
    """Whether both of the path's windows are usable rows (freshness is the collector's check)."""
    return set(METERS.values()) <= {row['id'] for row in usable_windows(settings_path, now)}


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
    try:
        folder = lifecycle._store_dir(settings_path)
    except lifecycle.ReadError as error:  # the shared helper's own error type: this reader's fixed reason
        raise ReadError(str(error) if str(error) in REASONS else 'observation_store_invalid') from None
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
    stored = {item['id']: item for item in usable_windows(settings_path, now)}
    boundary = observations.consumed_at(settings_path, POOL)
    return all(key in stored and 0 <= now - stored[key]['observed_at'] <= observations.FRESH_SECONDS
               and stored[key]['observed_at'] > boundary for key in need)


def _record(settings_path, init, use, started):
    """Store one exchange's account, catalog and windows. Telemetry and the account are checked before anything is
    written. A usage problem still records the account and catalog, with no confirmed exchange, so no earlier row
    stays usable. Another account's rows never match the new key; nothing earlier is re-dated."""
    from runtime.adapters import native_catalog
    key, version, models = parse_account(init, use)
    check_quiet(use)
    now = int(time.time())
    try:
        windows, overflow, problem = (*parse_usage(use, started, now), None)
    except ReadError as error:
        windows, overflow, problem = [], 'unknown', error
    usage_at = windows[0]['observed_at'] if windows else None
    _save(settings_path, account=dict(key=key, observed_at=now, client_version=version, overflow=overflow,
                                      usage_at=None))
    native_catalog.store_snapshot(settings_path, CLIENT, dict(binding=dict(
        client_version=version, connection='account:' + key, controls=CONTROLS), models=models))
    if problem is not None:
        raise problem
    for item in windows:
        observations.store_window(settings_path, POOL, dict(item, id=bound_id(item['id'], key), account=key))
    _save(settings_path, account=dict(key=key, observed_at=now, client_version=version, overflow=overflow,
                                      usage_at=usage_at))  # confirmed only after every row is stored
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
                # An explicit account-level refusal ends the earlier account confirmation (its fact and catalog
                # binding); stored window rows stay as observed history, unusable without a confirmation.
                ended = dict(account=None) if str(error) in ACCOUNT_REFUSALS else {}
                _save(settings_path, failure=dict(at=int(time.time()), reason=str(error)), **ended)
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

