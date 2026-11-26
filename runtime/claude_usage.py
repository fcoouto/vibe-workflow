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

The account key carries a random generation (`identity={key, generation}`) kept only across proved continuity of
the same key (account-continuity design); the catalog binding, the fact's producer and the rows carry it.
Windows are stored bound to the account key, its generation and the exchange (under `<window>@<token prefix>` with
the full `account` token, so they never replace another client's row of the shared pool, nor it theirs, and a
legacy or other-generation row is never this generation's; and the observed time): the native consumer uses
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
from contextlib import ExitStack, contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import selectors
import signal
import subprocess
import threading
import time

from runtime import claude_quota, processes as lifecycle, routing, routing_observations as observations, \
    routing_paths, storage, usage_balance

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
METER_DEPTH = 4  # nested containers an unknown server value is searched through for a meter
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
    """(initialize payload, usage payload, wall time just before `get_usage` was sent, wall time when its answer was
    read); the group is stopped after, and cleanup never moves the receipt time."""
    cancelled = reading = False

    def cancel(signum, frame):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            if reading:
                raise KeyboardInterrupt

    with ExitStack() as owned:
        # Latch cancellation through spawn and reap. Only interrupt blocking protocol I/O once cleanup is registered.
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                owned.callback(signal.signal, sig, signal.signal(sig, cancel))
        try:
            process = subprocess.Popen([*command, *FLAGS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, env=child_env(environ), cwd='/',
                                       start_new_session=True, close_fds=True)
        except OSError:
            raise ReadError('claude_unavailable') from None
        # Stop the entire group before reaping its leader, even if the leader exited after a valid reply.
        owned.callback(lifecycle._stop, process, False)
        init = None
        try:
            reading = True
            if cancelled:
                raise KeyboardInterrupt
            session = _Session(process, time.monotonic() + timeout)
            owned.callback(session.selector.close)
            session.send('vw-init', 'initialize')
            init = session.response('vw-init')
            started = time.time()
            session.send('vw-usage', 'get_usage')
            use = session.response('vw-usage')
            received = time.time()
        except (ReadError, OSError) as error:
            error.initialized = init is not None  # account material arrived (kept only in memory): not transport only
            raise
        finally:
            reading = False
    if cancelled:
        raise KeyboardInterrupt
    return init, use, started, received


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
    key = parse_identity(init, use)
    from runtime import native_catalog
    models = native_catalog.from_claude_initialize(init)
    version = init.get('claude_code_version')
    if not models or not routing._text(version):
        raise ReadError('claude_catalog_invalid')
    return key, version, models


def parse_identity(init, use):
    """The account key `parse_account` checks first (the exchange's account observation), before its catalog."""
    account = init.get('account')
    if type(account) is not dict or not routing._text(account.get('email')) \
            or account.get('apiProvider') != 'firstParty' or use.get('subscription_type') not in SUBSCRIPTIONS \
            or 'tokenSource' in account and account['tokenSource'] is not None \
            or 'apiKeySource' in account and account['apiKeySource'] is not None \
            or not routing._optional(account.get('organization'), routing._text):
        raise ReadError('claude_account_unsupported')
    return account_key(account)


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
    if type(value) is int and not 0 <= value <= 100:
        raise ReadError('claude_usage_invalid')  # before the float conversion below, which a huge integer overflows
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


def _meter_like(value, depth=METER_DEPTH):
    """Whether an unknown server value holds a quota meter (an object with `utilization` or `resets_at`) anywhere in
    its nested objects and lists. A container nested deeper than `depth` cannot be inspected and counts as one."""
    if type(value) not in (dict, list):
        return False
    if depth <= 0 or type(value) is dict and {'utilization', 'resets_at'} & set(value):
        return True
    return any(_meter_like(item, depth - 1) for item in (value.values() if type(value) is dict else value))


def _repeats(item, meters):
    """Whether an unscoped raw `limits` item only repeats a parsed meter: the same reset (within the cache ceiling)
    and the same percent (within one point). Anything else is a constraint this reader does not model."""
    try:
        percent, reset = _percent(item.get('percent')), item.get('resets_at')
        if percent == 0 and 'resets_at' in item and reset is None:
            return (0, None) in meters.values()
        reset = _time(reset)
    except ReadError:
        return False
    return any(at is not None and abs(percent - used) <= 1 and abs(reset - at) <= CACHE_CEILING
               for used, at in meters.values())


def parse_usage(use, started, now):
    """`(windows, overflow)`. Windows come only from a complete successful answer: both meters, the raw `limits`
    array present (holding nothing but unscoped repeats of those meters and model-scoped weekly limits, which
    `scopes` records per model and which are never windows). Null or non-meter server keys are ignored; an unknown
    meter or anything else is a fixed reason, never a partial reading. These are usage-level reasons only: the
    account read is recorded before this parse and stays current."""
    rate = use.get('rate_limits')
    if use.get('rate_limits_available') is not True or type(rate) is not dict:
        raise ReadError('claude_usage_unconfirmed')
    if any(_meter_like(rate[key]) for key in set(rate) - KNOWN_RATE) \
            or any(rate.get(key) is not None for key in UNKNOWN_METERS):
        raise ReadError('claude_limits_unsupported')
    overflow = _overflow(rate)
    if any(rate.get(key) is not None for key in SCOPED_METERS):
        raise ReadError('claude_limits_scoped')  # the older per-model meters keep refusing (not seen on 2.1.311)
    if rate.get('model_scoped') is not None and type(rate['model_scoped']) is not list:
        raise ReadError('claude_usage_invalid')
    meters = {}
    for key, window in METERS.items():
        meter = rate.get(key)
        if meter is None:
            raise ReadError('claude_window_missing')
        if type(meter) is not dict:
            raise ReadError('claude_usage_invalid')
        percent, reset = _percent(meter.get('utilization')), meter.get('resets_at')
        if not (percent == 0 and 'resets_at' in meter and reset is None):
            reset = _time(reset)
        meters[window] = percent, reset
    limits = rate.get('limits')
    if type(limits) is list:
        for item in limits:
            if type(item) is dict and item.get('scope') is None and _repeats(item, meters):
                continue  # an unscoped summary of a parsed meter (the 2.1.311 answer lists them)
            scope = item.get('scope') if type(item) is dict else None
            scoped = item.get('kind') == 'weekly_scoped' and type(scope) is dict and scope.get('model') is not None \
                if type(item) is dict else False
            if scoped and type(scope['model']) is dict:
                continue  # a model-scoped weekly limit: recorded per model by `scopes`, never a window
            raise ReadError('claude_limits_scoped' if scoped else 'claude_limits_unsupported')
    elif limits is not None:
        raise ReadError('claude_usage_invalid')
    if limits is None:
        raise ReadError('claude_usage_unconfirmed')  # a cached or header-seeded answer: no fresh time
    observed = max(0, math.floor(started) - CACHE_CEILING)
    found = []
    for key, window in METERS.items():
        percent, reset = meters[window]
        if reset is None:
            continue  # an explicitly unused meter has no evidenced window boundary
        length = observations.RATE_WINDOWS[key][1]
        if not now < reset <= now + length + observations.CLOCK_MARGIN:
            raise ReadError('claude_usage_invalid')
        found.append(dict(id=window, limit=observations.WINDOW_UNITS, length=length, reset_at=reset, rolling=False,
                          used=min(observations.WINDOW_UNITS, math.ceil(percent * observations.WINDOW_UNITS / 100
                                                                        - 1e-9)),
                          unit=observations.WINDOW_UNIT, observed_at=observed))
    return (found if len(found) == len(METERS) else []), overflow


MAX_SCOPES = 8  # more model-scoped limits than this name no model: every Claude route is quota-unknown
GENERIC = {'claude'}  # with digits: words that name no model family on their own (Task 12, H2)


def _tokens(value):
    return set(re.split('[^0-9a-z]+', value.casefold())) - {''} if type(value) is str else set()


def _family(name):
    """The words of a scope name that can name a model family (generic words and numbers removed)."""
    return {word for word in name.split() if word not in GENERIC and not word.isdigit()}


def scopes(use, models):
    """The answer's model-scoped weekly limits `[{name, utilization, reset, matched}]` (`model_scoped` and the raw
    `weekly_scoped` items) merged per casefolded name. `matched`: the name has a family word (not generic, not a
    number) and its family words are words of one catalog model's alias or resolved model. A malformed scope refuses
    the reading; more than MAX_SCOPES become one unmatched entry."""
    rate = use['rate_limits']
    entries = [(item.get('display_name'), item.get('utilization'), item.get('resets_at')) if type(item) is dict
               else (None, None, None) for item in rate.get('model_scoped') or []]
    for item in rate.get('limits') or []:
        scope = item.get('scope') if type(item) is dict else None
        if type(scope) is dict and item.get('kind') == 'weekly_scoped' and type(scope.get('model')) is dict:
            entries.append((scope['model'].get('display_name'), item.get('percent'), item.get('resets_at')))
    found = {}
    for name, used, reset in entries:
        if not routing._text(name) or not _tokens(name):
            raise ReadError('claude_limits_scoped')
        name, used, reset = ' '.join(sorted(_tokens(name))), _percent(used), _time(reset)
        if name not in found or used > found[name]['utilization']:
            found[name] = dict(name=name, utilization=used, reset=reset)
    if len(found) > MAX_SCOPES:
        return [dict(name='', utilization=0, reset=0, matched=False)]  # too many: no model is named
    known = [_tokens(item.get('alias')) | _tokens(item.get('resolved')) for item in models if type(item) is dict]
    return [dict(item, matched=bool(_family(item['name'])) and any(_family(item['name']) <= words for words in known))
            for _, item in sorted(found.items())]


def _bound_exhaustion(settings_path, route, now, native):
    """`(binding_current, remaining)` from one state read: whether `native` is still the current account's binding,
    and the seconds until its bound quota's reset (None when that account has no usable quota row). This takes no
    lock; it is the gate's private read, and `exhaustion_left` is its public number-or-None form."""
    from runtime import native_catalog, opencode_account
    if settings_path is None or type(route) is not dict or route.get('pool') != POOL \
            or opencode_account.family(route) != 'claude':
        return False, None
    state = _state(settings_path)
    current = _current(state, now)
    if current is None or opencode_account._native(current) != native:
        return False, None
    found = state.get('quota')
    if type(found) is not dict or found.get('key') != current['key'] \
            or found.get('generation') != current['generation']:
        return True, None
    snapshot = native_catalog.stored(settings_path, CLIENT)
    bound = snapshot is not None and snapshot['binding'] == _binding_of(current)
    alias = route.get('model') if type(route.get('model')) is str else ''
    return True, claude_quota.remaining(found.get('record'), alias, snapshot['models'] if bound else [], now=now)


def ranking_meters(settings_path, route, now, *, native):
    """Claude ranking hints for `route`, bound to the login `native` (the OpenCode fact's native digest): fresh account
    windows, and scoped meters only where the bound catalog covers the route's model. Empty unless the stored batch
    belongs to that login and generation; a malformed meter is dropped alone."""
    from runtime import native_catalog, opencode_account
    if settings_path is None or type(route) is not dict or route.get('pool') != POOL \
            or opencode_account.family(route) != 'claude':
        return {}
    state = _state(settings_path)
    current, found = _current(state, now), state.get('quota')
    if current is None or opencode_account._native(current) != native or type(found) is not dict \
            or found.get('key') != current['key'] or found.get('generation') != current['generation']:
        return {}
    snapshot = native_catalog.stored(settings_path, CLIENT)
    bound = snapshot is not None and snapshot['binding'] == _binding_of(current)
    alias = route.get('model') if type(route.get('model')) is str else ''
    models = snapshot['models'] if bound else []
    return usage_balance.batch_meters(found.get('ranking'), now=now,
                                      keep=lambda key: claude_quota.covers(key, alias, models))


def exhaustion_left(settings_path, route, now, *, native=None):
    """Seconds until the account's confirmed exhaustion ends for this OpenCode Claude route, or None.

    `native` is the opaque native binding of the billing read that proved this route's account (`opencode_account`'s
    record). Omitted, it is the current OpenCode Claude account's own binding, or None without one. A binding that no
    longer matches the account gives None: no quota is applied. This takes no lock."""
    from runtime import opencode_account
    if native is None:
        found = opencode_account._account(settings_path, 'claude', now)
        native = found['native'] if found is not None else None
    if native is None:
        return None
    return _bound_exhaustion(settings_path, route, now, native)[1]


# -- the private store -------------------------------------------------------------------------------------------
def _store(settings_path):
    return observations._resolved(observations.window_path(settings_path)).parent / STORE_FILE


def _state(settings_path):
    try:
        return observations._read(_store(settings_path))
    except (OSError, ValueError):
        return {}


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None


def _proved(state, key=None):
    """The generation `state` proves (for `key`, when given), else None: its `identity={key, generation}` and its
    complete account (same key and generation) or pending account (`{pending: True, generation}`) agree. A legacy,
    cleared, corrupt or otherwise incoherent state proves none, whatever identity it retains."""
    identity = state.get('identity') if type(state) is dict else None
    found = state.get('account') if type(state) is dict else None
    if type(identity) is not dict or set(identity) != {'key', 'generation'} or not _hex(identity['key']) \
            or not _hex(identity['generation']) or key not in (None, identity['key']) or type(found) is not dict:
        return None
    if found == dict(pending=True, generation=identity['generation']) or 'pending' not in found and \
            found.get('key') == identity['key'] and found.get('generation') == identity['generation']:
        return identity['generation']
    return None


def _current(state, now):
    """The current account record within `state` (see `account`), so several checks can share one read."""
    found = state.get('account')
    if type(found) is not dict or 'pending' in found or _proved(state) is None \
            or not routing._amount(found.get('observed_at')) \
            or not (0 <= now - found['observed_at'] <= observations.FRESH_SECONDS):
        return None
    return found


def account(settings_path, now):
    """The current complete account record `{key, generation, observed_at, client_version, overflow, usage_at}`
    proved by its identity, or None when absent, pending, legacy, incoherent or stale."""
    if settings_path is None:
        return None
    return _current(_state(settings_path), now)


def bound_id(window, key):
    """The store key of this account's reading of `window` (a key-digest prefix, never an identifier)."""
    return '%s@%s' % (window, key[:12])


def row_account(current):
    """The opaque `account` binding of an account record's window rows: a digest of its key and generation, so a
    legacy row or another generation's row of the same account is never this generation's reading."""
    raw = json.dumps(['claude-native-rows', current['key'], current['generation']], separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def usable_windows(settings_path, now, stored=None):
    """The pool rows the native consumer may use: bound to the current account's key and generation (the full row
    token) and observed no earlier than its latest confirmed exchange (`usage_at`; none after a failed, partial or
    fallback read). `stored` is one read of the window store when the caller has it."""
    current = account(settings_path, now)
    if current is None or not routing._amount(current.get('usage_at')):
        return []
    token = row_account(current)
    rows = observations.LocalSource(settings_path).windows(POOL, now, stored=stored)
    by_id = {bound_id(window, token): window for window in METERS.values()}
    return [dict(row, id=by_id[row['id']]) for row in rows if row['id'] in by_id
            and row.get('account') == token and row['observed_at'] >= current['usage_at']]


def confirmation(settings_path):
    """What a native snapshot's readings depend on beyond the window store: the stored account key, generation and
    confirmed exchange time (None when absent), read without a freshness cut so that any change is visible."""
    found = _state(settings_path).get('account')
    found = found if type(found) is dict else {}
    return [found.get('key'), found.get('generation'), found.get('usage_at')]


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
    return dict(producer='claude-native:%s:%s' % (current['key'][:16], current['generation']),
                **subscription_billing.scope(route), observed_at=current['observed_at'],
                valid_until=current['observed_at'] + observations.FRESH_SECONDS, paid_crossover=state)


def _connection(key, generation):
    return 'account:%s:%s' % (key, generation)


def _binding_of(current):
    return dict(client_version=current['client_version'],
                connection=_connection(current['key'], current['generation']), controls=CONTROLS)


def binding(settings_path):
    """The native catalog binding of the current account read (its key and generation), or None."""
    current = account(settings_path, int(time.time()))
    return None if current is None else _binding_of(current)


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
    _transition(settings_path, lambda state: change)


def _owned(path):
    """The producer state for a write: a malformed but safe private file proves nothing and is reinitialized by that
    write (atomically, under the lock); an unsafe, oversized or unreadable one is never rewritten (it raises)."""
    try:
        return observations._read(path) or dict(version=1)
    except ValueError as error:
        if str(error) == 'observation_store_unsafe':
            raise
        return dict(version=1)


def _transition(settings_path, step):
    """One read-validate-write of the producer state under its lock: `step(state)` validates the state just read and
    returns the change to write (ValueError aborts with nothing written)."""
    with observations._locked(_store(settings_path)) as path:
        value = _owned(path)
        value.update(step(value))
        observations._write(path, value)


def _quota_change(state, expected, sample, now, *, merge=True):
    """The change merging one exchange's quota evidence into the state, for the expected `(key, generation)` that the
    state still proves. Evidence from another key or generation is never carried over. The ranking batch follows the
    same binding; `merge=False` (a weak probe reply) leaves the exhaustion record as it was."""
    key, generation = expected
    if _proved(state, key) != generation:
        raise ValueError('observation_store_invalid')
    found = state.get('quota')
    kept = found if type(found) is dict and found.get('key') == key and found.get('generation') == generation else {}
    record = claude_quota.merge(kept.get('record'), sample, now=now) if merge else kept.get('record')
    ranking = kept.get('ranking') if sample is None else _ranked(kept.get('ranking'), sample)
    return dict(quota=dict(key=key, generation=generation, record=record, ranking=ranking))


def _ranked(previous, sample):
    """The ranking batch after one recognized reply. Optional: a failure here drops the batch, never the exhaustion
    record that the same publication writes."""
    try:
        return usage_balance.batch_after(previous, earliest=sample['earliest'], latest=sample['latest'],
                                         meters=sample['usage'])
    except Exception:
        return None


def _fence(state, held):
    """Before an exchange: keep in `held`, for this attempt only, what the old state proves (key, generation, record),
    and leave an unprovable in-flight state (no identity; a pending account under a random fence), so that an
    interrupted or unrecorded observation never carries the generation forward."""
    generation = _proved(state)
    held.update(generation=generation, key=state['identity']['key'] if generation else None,
                account=state.get('account'), fence=dict(pending=True, generation=secrets.token_hex(16)))
    if generation:
        held['fence']['suspended'] = dict(key=held['key'], generation=generation)
    return dict(identity=None, account=held['fence'])


def _ours(state, held):
    return state.get('identity') is None and state.get('account') == held['fence']


def _settle(state, held):
    """After an exchange that observed nothing (transport only): the proved state back, unless another writer came."""
    if not held.get('generation') or not _ours(state, held):
        return {}
    return dict(identity=dict(key=held['key'], generation=held['generation']), account=held['account'])


class _FenceLost(Exception):
    """This refresh no longer owns its observation and must not publish or set backoff."""


def _observe(key, held):
    """Record an observation only with this attempt's fence; keep the proved generation only for the same key."""
    def step(state):
        if not _ours(state, held):
            raise _FenceLost()
        same = held.get('generation') and key == held.get('key')
        held['observed'] = held['generation'] if same else secrets.token_hex(16)
        return dict(identity=dict(key=key, generation=held['observed']),
                    account=dict(pending=True, generation=held['observed']))
    return step


def _expect(key, generation, change):
    """A step writing `change` only while the state still proves `generation` for `key` (no other writer since)."""
    def step(state):
        if _proved(state, key) != generation:
            raise ValueError('observation_store_invalid')
        return change
    return step


def _fresh(settings_path, need, now):
    """Whether the required metadata is current (the account and the catalog snapshot bound to the client's current
    binding, whatever the quota state) and then each demanded window (none with `need=()`) has a fresh reading."""
    from runtime import native_catalog
    snapshot = native_catalog.stored(settings_path, CLIENT)
    current = native_catalog._binding(settings_path, CLIENT)
    if account(settings_path, now) is None or snapshot is None or type(current) is not dict \
            or any(snapshot['binding'][key] != current.get(key) for key in ('connection', 'controls')):
        return False
    if not need:
        return True
    stored = {item['id']: item for item in usable_windows(settings_path, now)}
    boundary = observations.consumed_at(settings_path, POOL)
    return all(key in stored and 0 <= now - stored[key]['observed_at'] <= observations.FRESH_SECONDS
               and stored[key]['observed_at'] > boundary for key in need)


def _record(settings_path, init, use, started, received, held):
    """Store one exchange's account, catalog, quota evidence and windows. The account and telemetry are checked before
    anything is written. Then, in order: the observed identity under this attempt's untouched fence (the generation
    `held` proves for this key, else a fresh random one) with a pending account, before the catalog is checked; the
    catalog bound to it; the complete account with the quota evidence merged, even when the usage snapshot is refused;
    and only after every row is stored, the confirmed exchange. A usage problem still records the account and catalog,
    with no confirmed exchange, so no earlier row stays usable. Rows are bound to the key and generation, so another
    account's or generation's rows never match; nothing earlier is re-dated."""
    from runtime import native_catalog
    key = parse_identity(init, use)
    check_quiet(use)
    _transition(settings_path, _observe(key, held))  # recorded before the catalog can refuse the exchange
    generation = held['observed']
    key, version, models = parse_account(init, use)
    now = int(time.time())
    sample = claude_quota.evidence(use, models, started=started, received=received, include_usage=True)
    try:
        windows, overflow, problem = (*parse_usage(use, started, now), None)
        scoped = scopes(use, models)
    except ReadError as error:
        windows, overflow, problem, scoped = [], 'unknown', error, None
    usage_at = windows[0]['observed_at'] if windows else None
    current = dict(key=key, generation=generation, observed_at=now, client_version=version, overflow=overflow,
                   usage_at=None, scoped=scoped)
    native_catalog.store_snapshot(settings_path, CLIENT, dict(binding=dict(
        client_version=version, connection=_connection(key, generation), controls=CONTROLS), models=models))
    def publish(state):
        return dict(account=current, **_quota_change(state, (key, generation), sample, now))
    _transition(settings_path, publish)
    if problem is not None:
        raise problem
    token = row_account(current)
    for item in windows:
        observations.store_window(settings_path, POOL, dict(item, id=bound_id(item['id'], token), account=token))
    _transition(settings_path, _expect(key, generation, dict(account=dict(current, usage_at=usage_at))))
    return windows


def refresh(settings_path, *, need=None, force=False, command=None, environ=None, timeout=TIMEOUT):
    """Make the account read (and the demanded windows) current: `{state, reason?, windows?}`, never raises.

    `fresh`: nothing to do, no process. `busy`: another reader still holds the lock. `backoff`: the last read
    failed less than FAILURE_BACKOFF s ago. `stored`: one exchange stored. `failed`: a fixed reason, backing off.
    An explicit empty `need` asks for the required metadata only: current metadata is reused whatever the optional
    quota state, and an exchange that stored metadata but no usable usage answers `stored` with the usage reason
    (the usage failure still backs off)."""
    environ = os.environ if environ is None else environ
    need = tuple(need) if need is not None else tuple(sorted(METERS.values()))  # the path's 5h and 7d by default
    try:
        if not force and _fresh(settings_path, need, int(time.time())):
            return dict(state='fresh')
        with _lock(settings_path, timeout + LOCK_EXTRA) as held:
            if not held:
                return dict(state='busy', reason='reader_busy')
            now = int(time.time())
            if not force and _fresh(settings_path, need, now):
                return dict(state='fresh')
            # An unsafe or unreadable state could record neither the exchange nor its failure, so none starts; a
            # malformed private one is reinitialized by the fence below.
            failure = _owned(_store(settings_path)).get('failure')
            if not force and type(failure) is dict and routing._amount(failure.get('at')) \
                    and 0 <= now - failure['at'] < FAILURE_BACKOFF:
                return dict(state='backoff', reason=failure.get('reason'))
            held = {}
            _transition(settings_path, lambda state: _fence(state, held))  # unstored: no exchange starts
            try:
                try:
                    init, use, started, received = exchange(command or default_command(environ), environ, timeout)
                except (ReadError, OSError) as error:
                    if not getattr(error, 'initialized', False):  # transport only, before any account material
                        _transition(settings_path, lambda state: _settle(state, held))
                    raise  # after initialization the fence stays: unprovable, rotated on the next success
                windows = _record(settings_path, init, use, started, received, held)
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
            except _FenceLost:
                return dict(state='failed', reason='observation_store_invalid')
            except ReadError as error:
                # An explicit account-level refusal ends the earlier account confirmation and its continuity (its
                # fact, catalog binding and generation); stored window rows stay as observed history, unusable.
                ended = dict(account=None, identity=None) if str(error) in ACCOUNT_REFUSALS else {}
                _save(settings_path, failure=dict(at=int(time.time()), reason=str(error)), **ended)
                if not need and _fresh(settings_path, need, int(time.time())):
                    return dict(state='stored', windows=[], reason=str(error))  # metadata current, usage unknown
                return dict(state='failed', reason=str(error))
            except (OSError, ValueError):
                _save(settings_path, failure=dict(at=int(time.time()), reason='observation_store_invalid'))
                return dict(state='failed', reason='observation_store_invalid')
            _save(settings_path, failure=None)
            return dict(state='stored', windows=[item['id'] for item in windows])
    except ReadError as error:
        return dict(state='failed', reason=str(error))
    except (OSError, ValueError, storage.StorageError):
        return dict(state='failed', reason='observation_store_invalid')


# -- the trigger probe -------------------------------------------------------------------------------------------
PROBE_FILE = 'claude-limit-probe.json'
PROBE_LOCK = '.claude-limit-probe.lock'
PROBE_INTERVAL = 60  # seconds before the same native account key may be probed again
PROBE_KEYS = 8  # native accounts with a live probe start at once; another account waits, and no start is evicted


class _Skip(Exception):
    """A probe that publishes nothing and reports `skipped`."""


def _hint(reset_at, now):
    """A usable trigger hint: a whole-second reset after `now`, within a week plus the clock margin."""
    return type(reset_at) is int and now < reset_at <= now + claude_quota.LENGTHS['7d'] + storage.CLOCK_MARGIN


OVERFLOW = ('enabled', 'disabled', 'unknown')  # the states `_overflow` stores


def _complete(current):
    """Whether a current account record has the metadata `_record` stores: a supported overflow state, the client
    version, the usage time (None or a time) and the scoped list (None when unreadable)."""
    return all(field in current for field in ('client_version', 'overflow', 'usage_at', 'scoped')) \
        and current['overflow'] in OVERFLOW and routing._text(current['client_version']) \
        and (current['usage_at'] is None or routing._amount(current['usage_at'])) \
        and (current['scoped'] is None or type(current['scoped']) is list)


def _bound(state, expected, now):
    """The complete, fresh current account record when it is the login `expected` names, else None. A proved login
    whose stored metadata is partial is malformed private state: it raises rather than admitting or publishing."""
    current = _current(state, now)
    if current is None or (current['key'], current['generation']) != expected:
        return None
    if not _complete(current):
        raise ReadError('observation_store_invalid')
    return current


def _starts(path):
    """The probe starts `{key: started_at}` of the cooldown file: none when the file is absent. Its content must be
    the probe's own object, and a JSON null, or any other shape, is an error, never repaired or taken as absent."""
    if not os.path.lexists(path):
        return {}
    record = storage.read_json(path)
    if type(record) is not dict or set(record) != {'version', 'accounts'} or type(record['version']) is not int \
            or record['version'] != 1:
        raise ReadError('observation_store_invalid')
    accounts = record['accounts']
    if type(accounts) is not dict or len(accounts) > PROBE_KEYS \
            or not all(_hex(key) and routing._amount(at) for key, at in accounts.items()):
        raise ReadError('observation_store_invalid')
    return accounts


def _admit(starts, key, now):
    """The live starts plus this one, or None when `key` may not start. A start is live while `now - started_at` is
    under PROBE_INTERVAL, a negative elapsed time included, so a backwards clock fails closed for that account only.
    A full cooldown refuses a new account rather than evicting a live start."""
    live = {item: at for item, at in starts.items() if now - at < PROBE_INTERVAL}
    if key in live or len(live) >= PROBE_KEYS:
        return None
    return {**live, key: now}


def _ended(state, key, generation):
    """Retire only this exact generation, including its suspension or pending re-observation."""
    pending = state.get('account')
    suspended = state.get('identity') is None and type(pending) is dict and pending.get('pending') is True \
        and pending.get('suspended') == dict(key=key, generation=generation)
    if not suspended and _proved(state, key) != generation:
        raise _Skip()
    return dict(account=None, identity=None)


def probe_limit(settings_path, *, expected, reset_at, command=None, environ=None, timeout=TIMEOUT):
    """`{state}` of one bounded native probe under the login `expected` names (its key and generation).

    `stored`: usable evidence merged under that login. `skipped`: no current login, busy or cooling down, no usable
    evidence, an out-of-range hint (at start or at commit), or a different proved login. `failed`: a malformed request
    or private state, unsupported or unproved identity, refused telemetry, timeout or a transport problem. Only the
    probe lock is held during the child; account state is read, never fenced, until the short commit."""
    try:
        from runtime import native_catalog
        environ = os.environ if environ is None else environ
        key, generation = expected if type(expected) is tuple and len(expected) == 2 else (None, None)
        if not _hex(key) or not _hex(generation) or type(reset_at) is not int:
            return dict(state='failed')
        if not _hint(reset_at, int(time.time())):
            return dict(state='skipped')
        command = command or default_command(environ)
        with storage._locked(_store(settings_path), block=False, lock=PROBE_LOCK) as path:
            if path is None:
                return dict(state='skipped')
            probe = path.parent / PROBE_FILE
            starts = _starts(probe)
            state = observations._read(path)
            now = int(time.time())
            if not _hint(reset_at, now):
                return dict(state='skipped')
            if _bound(state, (key, generation), now) is None:
                return dict(state='skipped')
            admitted = _admit(starts, key, now)
            if admitted is None:
                return dict(state='skipped')
            observations._write(probe, dict(version=1, accounts=admitted))
            init, use, started, received = exchange(command, environ, timeout)
            check_quiet(use)
            if parse_identity(init, use) != key:
                _transition(settings_path, lambda state: _ended(state, key, generation))
                return dict(state='skipped')

            outcome = {}

            def commit(state):
                at = int(time.time())
                current = _bound(state, (key, generation), at)
                if current is None or not _hint(reset_at, at):
                    raise _Skip()
                snapshot = native_catalog.stored(settings_path, CLIENT)
                bound = snapshot is not None and snapshot['binding'] == _binding_of(current)
                sample = claude_quota.evidence(use, snapshot['models'] if bound else [], started=started,
                                               received=received, include_usage=True)
                if sample is None:
                    raise _Skip()
                # A recognized reply with no usable quota still replaces the ranking batch, but its result is skipped.
                usable = bool(sample['meters'] or sample['scopes_complete'])
                outcome['state'] = 'stored' if usable else 'skipped'
                return _quota_change(state, (key, generation), sample, at, merge=usable)
            _transition(settings_path, commit)
            return dict(state=outcome['state'])
    except _Skip:
        return dict(state='skipped')
    except (ReadError, OSError, ValueError, KeyboardInterrupt):  # a cancelled exchange has already cleaned up its child
        return dict(state='failed')
