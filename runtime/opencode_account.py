"""OpenCode selected-account identity for the three OpenCode subscription paths (Claude, ChatGPT, Go).

LocalSource selects it by exact path only. One bounded `opencode api GET /api/credential` (plus, for Go only, one
`GET /api/experimental/usage/limits`) reads the selected connections. That answer carries full tokens: it is parsed in
memory and dropped; only domain-separated digests, a random generation per family (kept only across proved continuity
of the same key, exactly as `codex_account`) and fixed reasons persist, in a private 0600 store.

- Claude: the active `claude-agent` credential must be `external`/`claude-login`, which is the native Claude Code
  login. Its identity is the native one (`claude_usage.account`, rechecked at every read); none proved: refused.
- ChatGPT: the active `openai` credential must be `oauth`/`chatgpt-token-sharing`. The key is the access JWT's `sub`
  claim (its payload is decoded locally; no signature check is possible or claimed) with the credential id, so a token
  refresh keeps the generation.
- Go: the active `opencode-go` key and the active `opencode` Console login (`accountID`, `orgID`), whose org must be
  the usage workspace. A replaced key or another Console account changes the generation.

A fact only confirms the account: these routes' quota stays unknown, so they are provider-managed fallback, never an
included claim. The OpenCode ChatGPT subject and the native Codex login are never merged into one identity.
"""
import base64
import hashlib
import json
import os
import selectors
import subprocess
import time

from runtime import claude_usage, codex_account as continuity, codex_rate_limits as lifecycle, routing, \
    routing_observations as observations, routing_paths, subscription_billing

PATHS = dict(claude='opencode-claude-subscription', chatgpt='opencode-chatgpt-subscription',
             go='opencode-go-subscription')
POOLS = dict(claude='claude-subscription', chatgpt='chatgpt-subscription', go='opencode-go-subscription')
FAMILIES = tuple(PATHS)
STORE_FILE = 'opencode-account.json'
LOCK = 'opencode-account'
CREDENTIAL = '/api/credential'
USAGE = '/api/experimental/usage/limits'
TIMEOUT = 10.0
MAX_OUTPUT = 256 << 10
ENV_ALLOWLIST = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'SHELL')
REFUSED = ('opencode_claude_unsupported', 'opencode_chatgpt_unsupported', 'opencode_go_unsupported',
           'opencode_go_workspace_mismatch')  # an observed refusal or change: continuity ends
REASONS = ('reader_busy', 'opencode_unavailable', 'opencode_exited', 'opencode_timeout', 'opencode_output_invalid',
           'opencode_credential_invalid', 'opencode_claude_unproved', 'opencode_go_usage_invalid', *REFUSED,
           'observation_store_unsafe', 'observation_store_invalid')
ReadError = lifecycle.ReadError


def child_env(environ):
    """The allowlisted environment (XDG_* included, nothing credential-like); relative PATH entries are dropped and
    TMPDIR is kept only when it is a private absolute directory."""
    env = {key: value for key, value in environ.items() if type(value) is str and value
           and (key in ENV_ALLOWLIST or key.startswith('XDG_') and key[4:].replace('_', '').isalnum())}
    env['PATH'] = lifecycle._absolute_path(env.get('PATH'))
    if 'TMPDIR' in env and not lifecycle._private_dir(env['TMPDIR']):
        del env['TMPDIR']
    return env


def default_command(environ):
    """`[<absolute opencode>]` from the absolute PATH entries only; never under the test runner's private home."""
    if environ.get('VCW_TEST_HOME'):
        raise ReadError('opencode_unavailable')
    for folder in lifecycle._absolute_path(environ.get('PATH')).split(':'):
        candidate = os.path.join(folder, 'opencode') if folder else ''
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate]
    raise ReadError('opencode_unavailable')


def _api(command, path, environ, deadline):
    """One `opencode api GET <path>` (no `--standalone`): stdout capped, stderr discarded, the group stopped after."""
    try:
        process = subprocess.Popen([*command, 'api', 'GET', path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=child_env(environ), cwd='/',
                                   start_new_session=True, close_fds=True)
    except OSError:
        raise ReadError('opencode_unavailable') from None
    process.stdin.close()  # nothing is ever sent
    chunks, size, normal = [], 0, False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                left = deadline - time.monotonic()
                if left <= 0 or not selector.select(left):
                    raise ReadError('opencode_timeout')
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_OUTPUT:
                    raise ReadError('opencode_output_invalid')
                chunks.append(chunk)
        normal = True
    finally:
        lifecycle._stop(process, normal)  # signals only the still-unreaped group, then reaps it
    if process.returncode != 0:
        raise ReadError('opencode_exited')
    try:
        return json.loads(b''.join(chunks))
    except ValueError:
        raise ReadError('opencode_output_invalid') from None


def _digest(*parts):
    return hashlib.sha256(json.dumps(['opencode-account', 1, *parts], separators=(',', ':')).encode()).hexdigest()[:32]


def _selected(rows, integration):
    """The single active credential of `integration`, or None (absent, inactive or ambiguous)."""
    found = [row for row in rows if type(row) is dict and row.get('integrationID') == integration
             and row.get('active') is True]
    if len(found) != 1 or not routing._text(found[0].get('id')) or type(found[0].get('value')) is not dict:
        return None
    return found[0]


def _subject(token):
    """The `sub` claim of an access JWT, decoded locally (no signature check is possible here or claimed)."""
    parts = token.split('.') if type(token) is str else []
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
    except (ValueError, TypeError):
        return None
    sub = claims.get('sub') if type(claims) is dict else None
    return sub if routing._text(sub) and sub.strip() else None


def _claude(rows, native):
    row = _selected(rows, 'claude-agent')
    if row is None or row['value'].get('type') != 'external' or row['value'].get('methodID') != 'claude-login':
        raise ReadError('opencode_claude_unsupported')
    if native is None:
        raise ReadError('opencode_claude_unproved')
    return _digest('claude', native['key'], native['generation'], row['id']), _native(native)


def _native(native):
    return _digest('claude-native', native['key'], native['generation'])


def _chatgpt(rows):
    row = _selected(rows, 'openai')
    value = row['value'] if row else {}
    sub = _subject(value.get('access')) if value.get('type') == 'oauth' \
        and value.get('methodID') == 'chatgpt-token-sharing' else None
    if sub is None:
        raise ReadError('opencode_chatgpt_unsupported')
    return _digest('chatgpt', sub, row['id'])


def _console(rows):
    """(Go key credential, Console account, Console org) when both selected connections are complete."""
    key, console = _selected(rows, 'opencode-go'), _selected(rows, 'opencode')
    metadata = console['value'].get('metadata') if console and console['value'].get('type') == 'oauth' else None
    if key is None or key['value'].get('type') != 'key' or not routing._text(key['value'].get('key')) \
            or type(metadata) is not dict or not routing._text(metadata.get('accountID')) \
            or not routing._text(metadata.get('orgID')):
        raise ReadError('opencode_go_unsupported')
    return key, metadata['accountID'], metadata['orgID']


def _go(found, limits):
    key, account, org = found
    data = limits.get('data') if type(limits) is dict else None
    workspace = data.get('workspace') if type(data) is dict else None
    if type(workspace) is not dict or not routing._text(workspace.get('id')):
        raise ReadError('opencode_go_usage_invalid')
    if workspace['id'] != org:
        raise ReadError('opencode_go_workspace_mismatch')
    secret = hashlib.sha256(key['value']['key'].encode()).hexdigest()
    return _digest('go', account, org, key['id'], secret)


def _store(settings_path):
    return observations._resolved(observations.window_path(settings_path)).parent / STORE_FILE


def _part(state, family):
    value = state.get(family) if type(state) is dict else None
    return value if type(value) is dict else {}


def _transition(settings_path, family, step):
    """One read-validate-write of one family's state under the store lock (ValueError aborts)."""
    with observations._locked(_store(settings_path)) as path:
        value = continuity._owned(path)
        part = _part(value, family)
        value[family] = dict(part, **step(part))
        observations._write(path, value)


def _account(settings_path, family, now):
    """The family's complete, proved and fresh account record, else None; Claude's native login is rechecked."""
    if settings_path is None or family not in PATHS:
        return None
    try:
        part = _part(observations._read(_store(settings_path)), family)
        native = claude_usage.account(settings_path, now) if family == 'claude' else None
    except (OSError, ValueError):
        return None
    found = part.get('account')
    if type(found) is not dict or 'pending' in found or continuity._proved(part) is None \
            or not routing._amount(found.get('observed_at')) \
            or not 0 <= now - found['observed_at'] <= observations.FRESH_SECONDS:
        return None
    if family == 'claude' and (native is None or found.get('native') != _native(native)):
        return None
    return found


def family(route):
    """The OpenCode family whose exact subscription path `route` names, or None."""
    if type(route) is not dict or route.get('billing_class', 'included_subscription') != 'included_subscription':
        return None
    path = routing_paths.path_of(route.get('route'))
    return next((name for name, value in PATHS.items() if value == path), None)


def applies(route):
    """Whether this producer supplies a route's account: a subscription route on an exact OpenCode path."""
    return family(route) is not None


def billing_fact(settings_path, route, now):
    """A pure bounded local read: the account-only fact while the family's read is current; quota stays unknown."""
    name = family(route)
    if name is None or route.get('pool') != POOLS[name]:
        return None
    current = _account(settings_path, name, now)
    if current is None:
        return None
    return dict(producer='opencode-%s:%s:%s' % (name, current['key'][:16], current['generation']),
                **subscription_billing.scope(route), observed_at=current['observed_at'],
                valid_until=current['observed_at'] + observations.FRESH_SECONDS, paid_crossover='unknown')


def _observe(rows, name, settings_path, command, environ, deadline):
    """(key, extra record fields) for one family from the credential rows, or a fixed ReadError."""
    if name == 'claude':
        key, native = _claude(rows, claude_usage.account(settings_path, int(time.time())))
        return key, dict(native=native)
    if name == 'chatgpt':
        return _chatgpt(rows), {}
    found = _console(rows)
    return _go(found, _api(command, USAGE, environ, deadline)), {}


def refresh(settings_path, *, families=FAMILIES, force=False, command=None, environ=None, timeout=TIMEOUT):
    """At most one bounded credential read (and Go's usage read) for the wanted families that are not current.

    Returns `{state, reason, families}` with fixed reasons only. An observed refusal ends that family's continuity;
    a transport failure brings back only proved continuity, pending; an interrupted read leaves an unprovable fence.
    """
    environ = os.environ if environ is None else environ
    wanted = tuple(name for name in FAMILIES if name in families)
    try:
        with lifecycle._pool_lock(settings_path, LOCK, timeout + lifecycle.LOCK_EXTRA) as locked:
            if not locked:
                return dict(state='busy', reason='reader_busy', families={})
            now = int(time.time())
            state = continuity._owned(_store(settings_path))  # unsafe: no exchange; malformed: reinitialized
            if not force:
                wanted = tuple(name for name in wanted if _account(settings_path, name, now) is None)
                if not wanted:
                    return dict(state='fresh', reason=None, families={})
                failures = [_part(state, name).get('failure') for name in wanted]
                if all(type(item) is dict and routing._amount(item.get('at'))
                       and 0 <= now - item['at'] < lifecycle.FAILURE_BACKOFF for item in failures):
                    reason = failures[0].get('reason')
                    return dict(state='backoff', reason=reason if reason in REASONS else 'observation_store_invalid',
                                families={})
            held = {name: {} for name in wanted}
            for name in wanted:
                _transition(settings_path, name, lambda part, name=name: continuity._fence(part, held[name]))
            deadline = time.monotonic() + timeout
            try:
                command = command or default_command(environ)
                answer = _api(command, CREDENTIAL, environ, deadline)
                rows = answer.get('data') if type(answer) is dict else None
                if type(rows) is not list:
                    raise ReadError('opencode_credential_invalid')
            except (ReadError, OSError, ValueError) as error:
                reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
                for name in wanted:
                    _transition(settings_path, name, lambda part, name=name: dict(
                        continuity._settle(part, held[name]), failure=dict(at=int(time.time()), reason=reason)))
                return dict(state='failed', reason=reason, families={name: reason for name in wanted})
            outcome = {}
            for name in wanted:
                outcome[name] = _record(settings_path, name, rows, held[name], command, environ, deadline)
            return dict(state='stored' if not any(outcome.values()) else 'partial', reason=None, families=outcome)
    except (ReadError, OSError, ValueError) as error:
        reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
        return dict(state='failed', reason=reason, families={})


def _record(settings_path, name, rows, held, command, environ, deadline):
    """Record one family's observation (its complete account) or its refusal; returns the fixed reason or None."""
    try:
        key, extra = _observe(rows, name, settings_path, command, environ, deadline)
        _transition(settings_path, name, continuity._observe(key, held))
        record = dict(key=key, generation=held['observed'], observed_at=int(time.time()), **extra)

        def publish(part):  # the complete account, only while the state still proves its generation
            if continuity._proved(part, key) != record['generation']:
                raise ValueError('observation_store_invalid')
            return dict(account=record, failure=None)
        _transition(settings_path, name, publish)
        return None
    except (ReadError, OSError, ValueError) as error:
        reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
        ended = dict(account=None, identity=None) if reason in REFUSED else None
        _transition(settings_path, name, lambda part: dict(
            ended if ended is not None else continuity._settle(part, held),
            failure=dict(at=int(time.time()), reason=reason)))
        return reason
