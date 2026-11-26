"""OpenCode selected-account identity for the three OpenCode subscription paths (Claude, ChatGPT, Go).

LocalSource selects it by exact path only. One bounded `opencode api GET /api/credential` (plus, for Go only, the
provider listing `GET /api/provider`) reads the selected connections, each with `--standalone`: a private
server of this CLI version that never uses or starts the background service. The credential answer carries full
tokens: it is parsed in memory and dropped; only domain-separated digests, a random generation per family (kept only
across proved continuity of the same key, exactly as `codex_account`) and fixed reasons persist, in a private 0600
store.

- Claude: the active `claude-agent` credential must be `external`/`claude-login`, which is the native Claude Code
  login. Its identity is the native one (`claude_usage.account`, rechecked at every read); when it is not current,
  the existing native reader runs once, asking for `5h`/`7d` only when a route demands them (else `need=()`). Its
  zero-token `get_usage` exchange is how that identity is read. None proved: refused.
- ChatGPT: the active `openai` credential must be `oauth`/`chatgpt-token-sharing`. The key is the access JWT's `sub`
  claim (its payload is decoded locally; no signature check is possible or claimed) with the credential id, so a token
  refresh keeps the generation.
- Go: the active `opencode-go` key and the active `opencode` Console login (`accountID`, `orgID`), whose org must be
  the `x-opencode-org-id` header the Go provider sends: that proves where Go requests are attributed. That the key
  belongs to the workspace is user-attested. A replaced key or another Console account changes the generation.

A fact only confirms the account. Go and ChatGPT quota stays unknown (provider-managed fallback, never an included
claim); OpenCode Claude may claim from the native login's bound rows while that login is current (`native_rows`).
The OpenCode ChatGPT subject and the native Codex login are never merged into one identity.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time

from runtime import chatgpt_limit, claude_usage, continuity, processes as lifecycle, routing, \
    routing_observations as observations, routing_paths, storage, subscription_billing

PATHS = dict(claude='opencode-claude-subscription', chatgpt='opencode-chatgpt-subscription',
             go='opencode-go-subscription')
POOLS = dict(claude='claude-subscription', chatgpt='chatgpt-subscription', go='opencode-go-subscription')
FAMILIES = tuple(PATHS)
STORE_FILE = 'opencode-account.json'
LOCK = 'opencode-account'
CREDENTIAL = '/api/credential'
USAGE = '/api/experimental/usage/limits'  # the Go workspace's meters (zero-token)
GO_METERS = ('rolling', 'weekly', 'monthly')
EXHAUSTED = 'quota_exhausted'
BILLING_UNVERIFIED = 'billing_unverified'
PROVIDER = '/api/provider'  # the provider listing (2.2.6 `--standalone` answers 404 for `/api/provider/<id>`)
TIMEOUT = 10.0
MAX_OUTPUT = 256 << 10
ENV_ALLOWLIST = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR', 'SHELL', 'XDG_DATA_HOME')
# The managed launcher's server flags (`opencode_launcher.SERVER_FLAGS`, a test keeps them equal): no project config,
# auto-update or models fetch. User-global config is an unobserved residual, as for launches.
LAUNCH_FLAGS = dict(OPENCODE_DISABLE_AUTOUPDATE='1', OPENCODE_DISABLE_MODELS_FETCH='1',
                    OPENCODE_DISABLE_PROJECT_CONFIG='1')
REFUSED = ('opencode_claude_unsupported', 'opencode_chatgpt_unsupported', 'opencode_go_unsupported',
           'opencode_go_workspace_mismatch')  # an observed refusal or change: continuity ends
REASONS = ('reader_busy', 'opencode_unavailable', 'opencode_exited', 'opencode_timeout', 'opencode_output_invalid',
           'opencode_credential_invalid', 'opencode_claude_unproved', 'opencode_go_provider_unlisted', *REFUSED,
           'observation_store_unsafe', 'observation_store_invalid')
ReadError = lifecycle.ReadError


def child_env(environ):
    """The allowlisted environment (of the XDG set only XDG_DATA_HOME, as the launcher passes; nothing
    credential-like); relative PATH entries are dropped and TMPDIR is kept only when private and absolute."""
    env = {key: value for key, value in environ.items() if type(value) is str and value and key in ENV_ALLOWLIST}
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
    """One `opencode api --standalone GET <path>`: a private server of this CLI version, never the background service
    (plain `opencode api` uses whichever service runs, possibly another version), with the launcher's flags (no
    project config, auto-update or models fetch) and an empty private OPENCODE_CONFIG_DIR made for this call and
    removed after. User-global config is an unobserved residual, as for launches. Stdout capped, stderr discarded,
    the group stopped after."""
    folder = tempfile.mkdtemp(prefix='vibe-opencode-account-')  # 0700 and empty
    try:
        return _call(command, path, dict(child_env(environ), **LAUNCH_FLAGS, OPENCODE_CONFIG_DIR=folder), deadline)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _call(command, path, env, deadline):
    try:
        process = subprocess.Popen([*command, 'api', '--standalone', 'GET', path], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, cwd='/',
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


def _go_key(found):
    key, account, org = found
    return _digest('go', account, org, key['id'], hashlib.sha256(key['value']['key'].encode()).hexdigest())


def _go(found, answer):
    """Go requests carry the Console org as `x-opencode-org-id` (the usage workspace is only the Console login's, so
    it proves nothing about them). The key's own workspace is user-attested, not provable here."""
    _key, _account, org = found
    listed = answer.get('data') if type(answer) is dict else None
    entries = [item for item in listed if type(item) is dict and item.get('id') == 'opencode-go'] \
        if type(listed) is list else []
    headers = entries[0].get('headers') if len(entries) == 1 else None
    attributed = headers.get('x-opencode-org-id') if type(headers) is dict else None
    if not routing._text(attributed):  # no single listed Go provider with its header: a read problem, proving nothing
        raise ReadError('opencode_go_provider_unlisted')
    if attributed != org:
        raise ReadError('opencode_go_workspace_mismatch')


def _usage(org, command, environ, deadline):
    """This acquisition's Go meters `{rolling, weekly, monthly: {percent, limited, reset_at}, observed_at}` for the
    proved workspace, or None: unreadable, another workspace or any other shape is no reading and never a gate."""
    observed_at = int(time.time())
    try:
        answer = _api(command, USAGE, environ, deadline)
    except ReadError:
        return None
    data = answer.get('data') if type(answer) is dict else None
    meters = data.get('go') if type(data) is dict else None
    workspace = data.get('workspace') if type(data) is dict else None
    if type(workspace) is not dict or workspace.get('id') != org or type(meters) is not dict \
            or set(meters) != set(GO_METERS):
        return None
    found = dict(observed_at=observed_at)
    for name in GO_METERS:
        meter = meters[name]
        if type(meter) is not dict or set(meter) != {'percent', 'limited', 'resetsAt'} \
                or type(meter['percent']) is not int or not 0 <= meter['percent'] <= 100 \
                or type(meter['limited']) is not bool or type(meter['resetsAt']) is not int or meter['resetsAt'] <= 0:
            return None
        found[name] = dict(percent=meter['percent'], limited=meter['limited'], reset_at=meter['resetsAt'] // 1000)
    return found


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


def _producer(name, current):
    """The billing producer of one family's account record: its key prefix and generation, never the raw key."""
    return 'opencode-%s:%s:%s' % (name, current['key'][:16], current['generation'])


def billing_fact(settings_path, route, now):
    """A pure bounded local read: the account-only fact while the family's read is current; quota stays unknown."""
    name = family(route)
    if name is None or route.get('pool') != POOLS[name]:
        return None
    current = _account(settings_path, name, now)
    if current is None:
        return None
    crossover = 'unknown'
    if name == 'claude':
        native = _native_account(settings_path, now)
        if native is None or current.get('native') != _native(native):
            return None
        overflow = native.get('overflow')
        crossover = 'provider_blocked' if overflow == 'disabled' else \
            'provider_managed' if overflow == 'enabled' else 'unknown'
    return dict(producer=_producer(name, current), **subscription_billing.scope(route),
                observed_at=current['observed_at'],
                valid_until=current['observed_at'] + observations.FRESH_SECONDS, paid_crossover=crossover)


def native_current(settings_path, now):
    """Whether the OpenCode Claude fact is current, so its stored native login is the current one (key, generation)."""
    return _account(settings_path, 'claude', now) is not None


def _quota_gate(settings_path, route, now, producer=None):
    """(`quota_exhausted`, seconds it may still last) on positive evidence only, never headroom: a provider quota-limit
    error on this pool within PROVIDER_LIMIT_SECONDS (E2); Go, this acquisition's bound meters (one limited or at 100%
    with its reset ahead) read at most EXHAUSTED_RECHECK_SECONDS ago. ChatGPT: only its limit mark (`chatgpt_limit`),
    never a reading. Claude: confirmed exhaustion in the native login's bound quota evidence while this fact's login is
    current, lasting until the reset (`claude_usage.exhaustion_left`). A Claude `producer` (the billing fact's) must
    still be the current login's, else `billing_unverified`."""
    name = family(route)
    if name == 'claude':
        if route.get('pool') != POOLS['claude']:
            return None, None
        found = _account(settings_path, name, now)
        if producer is not None and (found is None or _producer(name, found) != producer):
            return BILLING_UNVERIFIED, None  # the fact's login is no longer the current one: no quota for it
        if found is None:
            return None, None
        bound, left = claude_usage._bound_exhaustion(settings_path, route, now, found['native'])
        if not bound and producer is not None:
            return BILLING_UNVERIFIED, None  # the native login changed after the fact's read: its proof is lost
        return (EXHAUSTED, left) if left is not None else (None, None)
    if name == 'chatgpt':
        left = chatgpt_limit.remaining(Path(settings_path).parent, now) if settings_path is not None else None
        return (EXHAUSTED, left) if left else (None, None)
    found = _account(settings_path, name, now) if name == 'go' else None
    usage = (found or {}).get('usage')
    if type(usage) is not dict or route.get('pool') != POOLS['go'] or not any(
            usage[name]['reset_at'] > now and (usage[name]['limited'] or usage[name]['percent'] >= 100)
            for name in GO_METERS):
        return None, None
    left = observations.trust_left(found.get('observed_at'), now)
    return (EXHAUSTED, left) if left is not None else (None, None)


def quota_problem(settings_path, route, now, producer=None):
    """`quota_exhausted` on positive evidence, or `billing_unverified` when a Claude route's `producer` (the billing
    fact's) is no longer the current login's; None otherwise. Without a producer the current login is used."""
    return _quota_gate(settings_path, route, now, producer)[0]


def exhausted_stale(settings_path, route, now):
    """E1: whether Go's stored meters still show exhaustion but were read too long ago to refuse."""
    found = _account(settings_path, 'go', now) if family(route) == 'go' else None
    usage = (found or {}).get('usage')
    if type(usage) is not dict or not routing._amount(found.get('observed_at')):
        return False
    return now - found['observed_at'] > observations.EXHAUSTED_RECHECK_SECONDS and any(
        usage[name]['reset_at'] > now and (usage[name]['limited'] or usage[name]['percent'] >= 100) for name in GO_METERS)


def quota_retry_after(settings_path, route, now):
    return _quota_gate(settings_path, route, now)[1]


def _native_account(settings_path, now):
    try:
        return claude_usage.account(settings_path, now)
    except (OSError, ValueError):
        return None


def _observe(rows, name, settings_path, command, environ, deadline, observed):
    """(key, extra record fields) for one family from the credential rows, or a fixed ReadError. A Go key derived
    from the rows is put in `observed` before the listing is read, so a listing problem still records it (pending)."""
    if name == 'claude':
        key, native = _claude(rows, claude_usage.account(settings_path, int(time.time())))
        return key, dict(native=native)
    if name == 'chatgpt':
        return _chatgpt(rows), {}
    found = _console(rows)
    observed.append(_go_key(found))
    _go(found, _api(command, PROVIDER, environ, deadline))
    return observed[0], dict(usage=_usage(found[2], command, environ, deadline))


def refresh(settings_path, *, families=FAMILIES, force=False, command=None, environ=None, timeout=TIMEOUT, need=()):
    """At most one bounded credential read (and Go's provider read) for the wanted families that are not current.

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
            if 'claude' in wanted and (_native_account(settings_path, now) is None
                                       or need and not claude_usage.usable(settings_path, now)):
                # The OpenCode Claude identity is the native login: the existing bounded native reader, with the
                # `5h`/`7d` windows only when the route demands them (`need`), never for ChatGPT or Go.
                claude_usage.refresh(settings_path, need=tuple(need), environ=environ)
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
    observed = []
    try:
        key, extra = _observe(rows, name, settings_path, command, environ, deadline, observed)
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
        kept = continuity._observe(observed[0], held) if observed and 'observed' not in held \
            else lambda part: continuity._settle(part, held)
        _transition(settings_path, name, lambda part: dict(
            ended if ended is not None else kept(part), failure=dict(at=int(time.time()), reason=reason)))
        return reason


def probe_claude_limit(settings_path, *, reset_at, command=None, environ=None, timeout=claude_usage.TIMEOUT):
    """`{state}` of one native probe under the current proved OpenCode Claude login (`claude_usage.probe_limit`).

    The OpenCode fact's native digest must match the complete native account read after it, so a race skips. The
    probe never waits for, or fences, ordinary account reads."""
    try:
        now = int(time.time())
        if type(reset_at) is not int or not isinstance(settings_path, Path) or not settings_path.is_absolute():
            return dict(state='failed')
        folder = storage._resolved(settings_path).parent
        try:
            storage.private(folder, True)
        except FileNotFoundError:
            return dict(state='skipped')
        found = _account(settings_path, 'claude', now)
        observations._read(_store(settings_path))
        state = observations._read(claude_usage._store(settings_path))
        current = claude_usage._current(state, now)
        if found is None or current is None or _native(current) != found['native']:
            return dict(state='skipped')
        return claude_usage.probe_limit(settings_path, expected=(current['key'], current['generation']),
                                        reset_at=reset_at, command=command, environ=environ, timeout=timeout)
    except (OSError, ValueError, KeyboardInterrupt):  # a cancelled read or probe publishes nothing
        return dict(state='failed')
