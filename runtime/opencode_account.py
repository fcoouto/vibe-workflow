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
  the `x-opencode-org-id` header the Go provider sends. Quota comes from Go's documented usage endpoint, using that
  key rather than the Console login. A replaced key or another Console account changes the generation.

A fact only confirms the account. Positive Go and Claude meter evidence can refuse a recommendation, not authorize
spending. ChatGPT has a separate limit mark.
The OpenCode ChatGPT subject and the native Codex login are never merged into one identity. Codex plan windows are
kept beside the proved ChatGPT binding as ranking hints only, on the accepted assumption that both are one account:
they order comparable picks and never start, extend or clear a pause.
"""
import base64
from contextlib import closing
from datetime import datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time

from runtime import chatgpt_limit, claude_usage, codex_reset, continuity, processes as lifecycle, routing, \
    routing_observations as observations, routing_paths, storage, subscription_billing, usage_balance

PATHS = dict(claude='opencode-claude-subscription', chatgpt='opencode-chatgpt-subscription',
             go='opencode-go-subscription')
POOLS = dict(claude='claude-subscription', chatgpt='chatgpt-subscription', go='opencode-go-subscription')
FAMILIES = tuple(PATHS)
STORE_FILE = 'opencode-account.json'
LOCK = 'opencode-account'
CREDENTIAL = '/api/credential'
GO_HOST = 'opencode.ai'
GO_USAGE = '/inference/go/v1/usage'
GO_WINDOWS = dict(rolling=5 * 3600, weekly=7 * 86400, monthly=31 * 86400)
GO_MAX_BODY = 32 << 10
GO_REFUSED = 3  # The private HTTPS child returns this only for an uncached 401/403.
EXHAUSTED = 'quota_exhausted'
BILLING_UNVERIFIED = 'billing_unverified'
CHATGPT_LOCK = '.chatgpt-usage.lock'  # held by the one usage worker of this store; never the store's write lock
CHATGPT_SPACING = 60  # the least time between two usage read starts
CHATGPT_COOLDOWN = 900  # after a failed or interrupted usage read
CHATGPT_KEY = re.compile(r'5h|7d|duration:[1-9][0-9]*')
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
    """Verify Console workspace attribution; report whether this provider supports the key-bound quota read."""
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
    settings = entries[0].get('settings')
    url = settings.get('baseURL') if type(settings) is dict else None
    return routing._text(url) and bool(re.fullmatch(r'https://opencode\.ai/inference/go(?:/[A-Za-z0-9_-]+)*/?', url)) \
        and not any(header.lower() in ('authorization', 'api-key', 'x-api-key') for header in headers)


def _go_http(key, timeout):
    """One fixed-host TLS GET; run only inside the disposable quota child."""
    try:
        with closing(http.client.HTTPSConnection(GO_HOST, timeout=timeout)) as conn:
            conn.request('GET', GO_USAGE, headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json',
                                                  'Cache-Control': 'no-cache, no-store'})
            with conn.getresponse() as response:
                if response.getheader('Age') not in (None, '0'):
                    return None
                if response.status in (401, 403):
                    raise ReadError('opencode_go_unsupported')
                if response.status != 200:
                    return None
                raw = bytearray()
                while len(raw) <= GO_MAX_BODY:
                    chunk = response.read1(GO_MAX_BODY + 1 - len(raw))
                    if not chunk:
                        return bytes(raw) if response.length in (None, 0) else None
                    raw.extend(chunk)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return None


def _go_child(timeout):
    key = sys.stdin.buffer.read(routing.MAX_ID + 1).decode('ascii')
    try:
        body = _go_http(key, timeout)
    except ReadError:
        return GO_REFUSED
    if body is not None:
        sys.stdout.buffer.write(body)
    return 0


def _go_request(key, deadline):
    """Use stdin for the key and bound the entire HTTPS exchange, including DNS, by the refresh deadline."""
    left = deadline - time.monotonic()
    if left <= 0 or os.environ.get('VCW_TEST_HOME') or not routing._text(key) or not key.isascii() \
            or any(c.isspace() for c in key):
        return None
    boot = 'import sys; sys.path.insert(0, sys.argv[1]); from runtime.opencode_account import _go_child; ' \
           'sys.exit(_go_child(float(sys.argv[2])))'
    try:
        reply = subprocess.run([sys.executable, '-I', '-B', '-c', boot, str(Path(__file__).resolve().parents[1]),
                                str(left)], input=key.encode('ascii'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=child_env(os.environ), cwd='/', timeout=left)
        if time.monotonic() >= deadline:
            return None
        if reply.returncode == GO_REFUSED:
            raise ReadError('opencode_go_unsupported')
        if reply.returncode == 0 and 0 < len(reply.stdout) <= GO_MAX_BODY:
            return json.loads(reply.stdout)
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
        pass
    return None


def _go_valid(name, entry):
    return type(entry) is dict and set(entry) == {'exhausted', 'reset_at', 'observed_at'} \
        and type(entry['exhausted']) is bool and routing._amount(entry['reset_at']) \
        and routing._amount(entry['observed_at']) \
        and 0 < entry['reset_at'] - entry['observed_at'] <= GO_WINDOWS[name] + storage.CLOCK_MARGIN


def _go_meter(name, meter, observed_at):
    if type(meter) is not dict:
        return None
    status, percent, reset = meter.get('status'), meter.get('percent'), meter.get('resetsAt')
    if status not in ('ok', 'rate-limited') or type(percent) is not int or not 0 <= percent <= 100 \
            or status == 'ok' and percent == 100 or not routing._text(reset) or len(reset) > 40:
        return None
    try:
        stamp = datetime.fromisoformat(reset.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return None
        entry = dict(exhausted=status == 'rate-limited', reset_at=int(stamp.timestamp()), observed_at=observed_at)
    except (ValueError, OverflowError, OSError):
        return None
    return entry if _go_valid(name, entry) else None


GO_RANKING = ('5h', '7d', 'monthly')  # Go's rolling and weekly windows, aliased to the account windows
GO_ALIAS = dict(rolling='5h', weekly='7d')
GO_NATIVE = {GO_ALIAS.get(name, name): name for name in GO_WINDOWS}  # a ranking key's native window


def _go_hint(name, meter, entry, started):
    """The ranking meter of one Go row already validated as `entry` (the same parse); `rate-limited` counts as full."""
    return GO_ALIAS.get(name, name), dict(
        used_percent=100 if entry['exhausted'] else meter['percent'], earliest=started, latest=entry['observed_at'],
        reset_at=entry['reset_at'], duration=None if name == 'monthly' else GO_WINDOWS[name], discrete=True)


def _go_sample(key, deadline):
    """`(started, exhaustion meters, ranking)` of one provider read. `ranking` is `(received, hints)` only for a usage
    object, so a failed transport renews no ranking batch; `started` is stamped before the request."""
    started = int(time.time())
    answer = _go_request(key, deadline)
    received = int(time.time())
    usage = answer.get('usage') if type(answer) is dict else None
    if type(usage) is not dict or not 0 <= started <= received:
        return started, {}, None
    meters, hints = {}, {}
    for name in GO_WINDOWS:
        entry = _go_meter(name, usage.get(name), received)
        if entry is not None:
            meters[name] = entry
            alias, hint = _go_hint(name, usage[name], entry, started)
            hints[alias] = hint
    return started, meters, (received, hints)


def _bound_go(part, key, generation):
    quota = part.get('go_quota')
    if type(quota) is dict and quota.get('key') == key and quota.get('generation') == generation \
            and type(quota.get('meters')) is dict:
        return quota['meters']
    return {}


def _go_ranking(part, key, generation, sample):
    """The Go ranking batch after `sample`: a recognized usage answer replaces it under the interval rule; a failed
    read keeps the batch the same key and generation already hold."""
    quota = part.get('go_quota')
    previous = quota.get('ranking') if type(quota) is dict and quota.get('key') == key \
        and quota.get('generation') == generation else None
    if sample[2] is None:
        return previous
    received, hints = sample[2]
    try:
        return usage_balance.batch_after(previous, earliest=sample[0], latest=received, meters=hints)
    except Exception:  # optional, as for Claude: the exhaustion meters of this same publication still stand
        return None


def _merge_go(previous, sample, now):
    """Retain known meters across clock rollback; exhaustion wins a same-second tie, recovery must be newer."""
    started, meters = sample[0], sample[1]
    found = {name: previous[name] for name in GO_WINDOWS if _go_valid(name, previous.get(name))}
    for name, entry in meters.items():
        old = found.get(name)
        if entry['observed_at'] <= now and (old is None or started > old['observed_at']
                or started == old['observed_at'] and entry['exhausted'] and not old['exhausted']):
            found[name] = entry
    return found


def _store(settings_path):
    return observations._resolved(observations.window_path(settings_path)).parent / STORE_FILE


def _part(state, family):
    value = state.get(family) if type(state) is dict else None
    return value if type(value) is dict else {}


def _storable(value):
    """The store as the strict write can store it. The file is shared, so one hint that cannot be written back would
    block every family's account and pause updates. Only optional ranking state goes: an unstorable batch becomes
    unknown, and a ChatGPT usage record that still cannot be stored goes whole, which leaves its read due. Accounts,
    identities and pause meters are never altered; if they cannot be stored, the strict write still refuses."""
    if storage.storable(value):
        return value
    kept = dict(value)
    for name, part in value.items():
        if type(part) is dict:
            part = kept[name] = dict(part)
            if 'go_quota' in part:
                part['go_quota'] = usage_balance.storable_ranking(part['go_quota'])
            if 'chatgpt_usage' in part:
                usage = usage_balance.storable_ranking(part['chatgpt_usage'])
                part['chatgpt_usage'] = usage if storage.storable(usage) else None
    return kept


def _transition(settings_path, family, step):
    """One read-validate-write of one family's state under the store lock (ValueError aborts)."""
    with observations._locked(_store(settings_path)) as path:
        value = continuity._owned(path)
        part = _part(value, family)
        value[family] = dict(part, **step(part))
        observations._write(path, _storable(value))


def _complete(part, now):
    """The family's complete, proved and fresh account record within its state `part`, else None."""
    found = part.get('account')
    if type(found) is not dict or 'pending' in found or continuity._proved(part) is None \
            or not routing._amount(found.get('observed_at')) \
            or not 0 <= now - found['observed_at'] <= observations.FRESH_SECONDS:
        return None
    return found


def _billable(part, now):
    """The complete, proved account record while ordinary billing still accepts its proof. `_complete` keeps a record
    through its 900th second for the account reader's own schedule; `subscription_billing.problem` calls it stale from
    that second on, and so does this."""
    record = _complete(part, now)
    return record if record is not None and now - record['observed_at'] < observations.FRESH_SECONDS else None


def _account(settings_path, family, now):
    """The family's complete, proved and fresh account record, else None; Claude's native login is rechecked."""
    if settings_path is None or family not in PATHS:
        return None
    try:
        part = _part(observations._read(_store(settings_path)), family)
        native = claude_usage.account(settings_path, now) if family == 'claude' else None
    except (OSError, ValueError):
        return None
    found = _complete(part, now)
    if found is None:
        return None
    if family == 'claude' and (native is None or found.get('native') != _native(native)):
        return None
    if family == 'go':
        return dict(found, go_quota=_bound_go(part, found['key'], found['generation'])
                    if found.get('go_usage') is True else {})
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
    return _fact(name, current, route, crossover)


def _fact(name, record, route, crossover):
    """The account-bound billing fact of one account record: the shape `eligible` marks and ranking compares."""
    return dict(producer=_producer(name, record), **subscription_billing.scope(route),
                observed_at=record['observed_at'], valid_until=record['observed_at'] + observations.FRESH_SECONDS,
                paid_crossover=crossover)


def native_current(settings_path, now):
    """Whether the OpenCode Claude fact is current, so its stored native login is the current one (key, generation)."""
    return _account(settings_path, 'claude', now) is not None


def ranking_meters(settings_path, route, now, *, marker):
    """Ranking hints for `route`'s account, valid only while its billing marker is `marker` (the candidate's marker
    from `eligible`). One read of the account store supplies both the marker's record and the Go hints, so a login
    switch between reads can never pair one account's marker with another's hints. Claude's hints come from its own
    store, bound to this record's native login. Empty when anything is unproved, stale, changed or malformed; never
    an exhaustion input."""
    name = family(route)
    if settings_path is None or name is None or route.get('pool') != POOLS[name] or type(marker) is not str:
        return {}
    try:
        part = _part(observations._read(_store(settings_path)), name)
    except (OSError, ValueError):
        return {}
    record = _complete(part, now)
    if record is None or subscription_billing.marker(_fact(name, record, route, 'unknown'), route, now) != marker:
        return {}
    if name == 'claude':
        return claude_usage.ranking_meters(settings_path, route, now, native=record.get('native'))
    if name == 'chatgpt':  # only the Codex batch stored for this binding; never a Go-shaped field
        usage = _usage(part, record)
        return usage_balance.batch_meters(usage.get('ranking'), now=now,
                                          keep=lambda key: CHATGPT_KEY.fullmatch(key) is not None) if usage else {}
    quota = part.get('go_quota')
    if name != 'go' or record.get('go_usage') is not True or type(quota) is not dict \
            or quota.get('key') != record['key'] or quota.get('generation') != record['generation']:
        return {}
    hints = usage_balance.batch_meters(quota.get('ranking'), now=now, keep=lambda key: key in GO_RANKING)
    # A cached hint must pass the native parser's reset horizon again: the month has no duration to bound it.
    return {key: value for key, value in hints.items() if _go_valid(GO_NATIVE[key], dict(
        exhausted=False, reset_at=value['reset_at'], observed_at=value['latest']))}


def _usage(part, record):
    """The ChatGPT usage record stored for exactly this account record's key and generation, else None."""
    usage = part.get('chatgpt_usage')
    return usage if type(usage) is dict and usage.get('key') == record['key'] \
        and usage.get('generation') == record['generation'] else None


def _usage_due(usage, now):
    """Whether one more Codex read may start for a binding whose usage record is `usage`. A failed or interrupted read
    waits out the cooldown, from its completion when it returned. A success allows one follow-up once its 900-second
    age or its earliest known reset is reached, never within the minimum spacing of that start. A clock behind the
    record starts nothing."""
    if usage is None or not routing._amount(usage.get('started')):
        return True
    started, finished = usage['started'], usage.get('finished')
    if usage.get('ok') is not True:
        return now - (finished if routing._amount(finished) and finished >= started else started) >= CHATGPT_COOLDOWN
    reset = usage.get('reset')
    return now - started >= CHATGPT_SPACING and (
        now - started >= storage.FRESH_SECONDS or not routing._amount(reset) or now >= reset)


def chatgpt_usage_due(settings_path, route, now, *, marker):
    """Whether an on-demand Codex usage read is due for the ChatGPT candidate `route`, answered only while its billing
    marker is still `marker` (the candidate's marker from `eligible`). One read of the account store supplies both the
    marker's record and its usage record, so a login that rotated after the candidate was marked is never answered
    for. A pure local read: no account, no current proof, another binding or an unreadable store is never due."""
    try:
        if family(route) != 'chatgpt' or route.get('pool') != POOLS['chatgpt'] or type(marker) is not str:
            return False
        part = _part(observations._read(_store(settings_path)), 'chatgpt')
        record = _complete(part, now)
        if record is None or subscription_billing.marker(_fact('chatgpt', record, route, 'unknown'), route,
                                                         now) != marker:
            return False
        return _usage_due(_usage(part, record), now)
    except (OSError, ValueError, TypeError):
        return False


def refresh_chatgpt_usage(settings_path, *, environ=None, command=None):
    """One bounded Codex window read for the proved ChatGPT OAuth binding: `stored`, `busy`, `not_due`, `unbound` or
    `failed`, and nothing else.

    One worker per store holds a dedicated nonblocking lock, never the store's write lock, so picks and account reads
    go on. Under it the binding and the due status are checked again, and a start record that counts as failed is
    written before the exchange: a killed worker leaves the cooldown, not a retry. The reading is published only while
    the same key and generation are still the complete, proved account; a failed read keeps the batch already held.
    Both checks read the clock while holding the account lock, so a wait for it can neither shorten the spacing nor
    carry a binding past its freshness. The worker takes no candidate: it binds to the account proof it finds at the
    start, and that proof must be one ordinary billing still accepts, at the start and again at publication (stale
    once it is 900 seconds old). A success is spaced from the exchange start its meters carry; a failure cools down
    from the exchange's completion."""
    try:
        if not isinstance(settings_path, Path) or not settings_path.is_absolute():
            return 'failed'
        store = _store(settings_path)
        if _billable(_part(observations._read(store), 'chatgpt'), int(time.time())) is None:
            return 'unbound'  # before the lock, so an absent store is not created to say so
        with storage._locked(store, block=False, lock=CHATGPT_LOCK) as held:
            if held is None:
                return 'busy'
            bound, skip = {}, []

            def start(part):
                now = int(time.time())  # read while the account lock is held
                record = _billable(part, now)
                usage = _usage(part, record) if record else None
                if record is None or not _usage_due(usage, now):
                    skip.append('unbound' if record is None else 'not_due')
                    raise ValueError('skipped')
                bound.update(key=record['key'], generation=record['generation'], started=now, finished=None, ok=False,
                             reset=None, ranking=usage.get('ranking') if usage else None)
                return dict(chatgpt_usage=dict(bound))
            try:
                _transition(settings_path, 'chatgpt', start)
            except ValueError:
                if skip:
                    return skip[0]
                raise
            meters = codex_reset.read_windows(environ=environ, command=command)
            finished = int(time.time())  # the exchange's completion: what a failure cools down from

            def publish(part):
                record = _billable(part, int(time.time()))  # read while the account lock is held
                usage = _usage(part, record) if record else None
                if usage is None or continuity._proved(part, bound['key']) != bound['generation'] \
                        or usage.get('started') != bound['started'] or usage.get('ok') is not False:
                    skip.append('unbound')  # logged out, rotated or superseded during the read: nothing is published
                    raise ValueError('skipped')
                change = dict(bound, finished=finished)
                if meters:
                    first = next(iter(meters.values()))
                    change.update(ok=True, started=max(bound['started'], first['earliest']),
                                  reset=min(meter['reset_at'] for meter in meters.values()),
                                  ranking=usage_balance.batch_after(bound['ranking'], earliest=first['earliest'],
                                                                    latest=first['latest'], meters=meters))
                return dict(chatgpt_usage=change)
            try:
                _transition(settings_path, 'chatgpt', publish)
            except ValueError:
                if not skip:
                    raise
                return skip[0] if meters else 'failed'
            return 'stored' if meters else 'failed'
    except (OSError, ValueError, TypeError, LookupError, RecursionError, KeyboardInterrupt):
        return 'failed'  # an interrupted or broken read publishes nothing; its start record keeps the cooldown


def _quota_gate(settings_path, route, now, producer=None):
    """Refuse confirmed exhaustion until reset. A Go or Claude billing producer must still be current; ChatGPT
    retains its separate limit mark."""
    name = family(route)
    if name in ('claude', 'go'):
        if route.get('pool') != POOLS[name]:
            return None, None
        found = _account(settings_path, name, now)
        if producer is not None and (found is None or _producer(name, found) != producer):
            return BILLING_UNVERIFIED, None  # the fact's login is no longer the current one: no quota for it
        if found is None:
            return None, None
        if name == 'go':
            meters = found['go_quota']
            left = max((meters[name]['reset_at'] - now for name in GO_WINDOWS
                        if _go_valid(name, meters.get(name)) and meters[name]['exhausted']
                        and meters[name]['observed_at'] <= now < meters[name]['reset_at']), default=None)
            return (EXHAUSTED, left) if left is not None else (None, None)
        bound, left = claude_usage._bound_exhaustion(settings_path, route, now, found['native'])
        if not bound and producer is not None:
            return BILLING_UNVERIFIED, None  # the native login changed after the fact's read: its proof is lost
        return (EXHAUSTED, left) if left is not None else (None, None)
    if name == 'chatgpt':
        left = chatgpt_limit.remaining(Path(settings_path).parent, now) if settings_path is not None else None
        return (EXHAUSTED, left) if left else (None, None)
    return None, None


def quota_problem(settings_path, route, now, producer=None):
    """`quota_exhausted` on positive evidence, or `billing_unverified` when a Go or Claude billing producer is no
    longer current. None otherwise. Without a producer the current login is used."""
    return _quota_gate(settings_path, route, now, producer)[0]


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
    supported = _go(found, _api(command, PROVIDER, environ, deadline))
    return observed[0], dict(go_usage=supported, sample=_go_sample(found[0]['value']['key'], deadline)
                             if supported else None)


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
        generation = held['observed']
        sample = extra.pop('sample', None)
        record = dict(key=key, generation=generation, observed_at=int(time.time()), **extra)

        def publish(part):  # the complete account, only while the state still proves its generation
            if continuity._proved(part, key) != generation:
                raise ValueError('observation_store_invalid')
            change = dict(account=record, failure=None)
            if name == 'go' and sample is not None:
                meters = _merge_go(_bound_go(part, key, generation), sample, record['observed_at'])
                change['go_quota'] = dict(key=key, generation=generation, meters=meters,
                                          ranking=_go_ranking(part, key, generation, sample))
            return change
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
