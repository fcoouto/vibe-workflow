"""Native Codex account/catalog metadata from the 0.167.2 stable app-server protocol.

LocalSource, the native catalog and the native bridge select it by its exact path
or client only. One bounded, read-only exchange
checks practical identity around a complete catalog and incidental quota read.
Only derived identity, its random generation (kept only across proved
continuity of the same key) and exact launch aliases/efforts persist. No usage cache-age
bound or complete constraint mapping is qualified, so usable_windows always refuses
headroom. OpenCode's distinct ChatGPT connection is never inferred from this login.
"""
import hashlib
import json
import os
import re
import secrets
import time

from runtime import codex_rate_limits as lifecycle, routing, routing_observations as observations
from runtime import routing_paths, subscription_billing
from runtime.adapters import native_catalog

CLIENT = 'codex'
PATH = 'codex-chatgpt-subscription'
POOL = lifecycle.POOL
CONTROLS = 'native:model,effort'
STORE_FILE = 'codex-account.json'
USAGE_UNCONFIRMED = 'codex_usage_unconfirmed'
REASONS = (*lifecycle.REASONS, 'codex_account_unsupported', 'codex_account_changed', 'codex_catalog_invalid',
           USAGE_UNCONFIRMED, 'codex_limits_unsupported')
ReadError = lifecycle.ReadError
INVALIDATING = ('codex_account_unsupported', 'codex_account_changed', 'codex_auth_changed')
QUOTA_FRESH_SECONDS = 900  # user decision (2026-11-16): a Codex reading gates for 15 minutes; never a quota claim


def parse_account(response):
    """The nonsecret practical key of a reported ChatGPT email/workspace, or a fixed refusal."""
    value = response.get('account') if type(response) is dict else None
    if type(response) is not dict or response.get('requiresOpenaiAuth') is not True:
        raise ReadError('codex_account_unsupported')
    return account_key(value)


def account_key(account):
    """Hash email, reported workspace display name when present and sign-in type; never a UUID requirement."""
    if type(account) is not dict or account.get('type') != 'chatgpt' \
            or not routing._text(account.get('email')) or not account['email'].strip() \
            or not routing._optional(account.get('workspaceName'), routing._text):
        raise ReadError('codex_account_unsupported')
    raw = [account['email'].strip().casefold(), account.get('workspaceName'), account['type']]
    return hashlib.sha256(json.dumps(raw, separators=(',', ':')).encode()).hexdigest()[:32]


def parse_models(pages):
    """Normalize complete wire pages, never a guessed family, picker id or default effort."""
    if type(pages) is not list or not 0 < len(pages) <= native_catalog.MAX_MODELS:
        raise ReadError('codex_catalog_invalid')
    found, seen, cursors = [], set(), set()
    for index, page in enumerate(pages):
        if type(page) is not dict or type(page.get('data')) is not list or 'nextCursor' not in page:
            raise ReadError('codex_catalog_invalid')
        cursor = page['nextCursor']
        if index == len(pages) - 1:
            if cursor is not None:
                raise ReadError('codex_catalog_invalid')
        elif type(cursor) is not str or not cursor or len(cursor) > 4096 or not cursor.isprintable() or cursor in cursors:
            raise ReadError('codex_catalog_invalid')
        cursors.add(cursor)
        for row in page['data']:
            if type(row) is not dict or not routing._text(row.get('model')) or row['model'] in seen \
                    or type(row.get('supportedReasoningEfforts')) is not list:
                raise ReadError('codex_catalog_invalid')
            efforts = []
            for effort in row['supportedReasoningEfforts']:
                if type(effort) is not dict or not routing._text(effort.get('reasoningEffort')) \
                        or effort['reasoningEffort'] in efforts:
                    raise ReadError('codex_catalog_invalid')
                efforts.append(effort['reasoningEffort'])
            if len(efforts) > 16:
                raise ReadError('codex_catalog_invalid')
            seen.add(row['model'])
            found.append(dict(alias=row['model'], resolved=None, efforts=efforts))
            if len(found) > native_catalog.MAX_MODELS:
                raise ReadError('codex_catalog_invalid')
    if not found:
        raise ReadError('codex_catalog_invalid')
    return found


def _read(session, init, observed=None):
    started = int(time.time())
    session.send('account/read', 2, dict(refreshToken=False))
    key = parse_account(session.response(2))
    if observed is not None:
        observed.append(key)  # the earliest account observation, kept even when the catalog is refused
    pages, cursors, cursor, ident = [], set(), None, 3
    while True:
        params = dict(limit=native_catalog.MAX_MODELS, includeHidden=False)
        if cursor is not None:
            params['cursor'] = cursor
        session.send('model/list', ident, params)
        page = session.response(ident)
        pages.append(page)
        cursor = page.get('nextCursor')
        if 'nextCursor' not in page or len(pages) > native_catalog.MAX_MODELS:
            raise ReadError('codex_catalog_invalid')
        if cursor is None:
            break
        if type(cursor) is not str or not cursor or len(cursor) > 4096 or not cursor.isprintable() or cursor in cursors:
            raise ReadError('codex_catalog_invalid')
        cursors.add(cursor)
        ident += 1
    models = parse_models(pages)
    session.send('account/rateLimits/read', ident + 1)
    limits = session.response(ident + 1)
    session.send('account/read', ident + 2, dict(refreshToken=False))
    if parse_account(session.response(ident + 2)) != key:
        raise ReadError('codex_account_changed')
    # InitializeResponse has no stable, separate client-version field. Do not
    # guess a version from an opaque userAgent string or persist home/platform data.
    reason = _usage_problem(limits)
    return dict(key=key, observed_at=started, client_version='unknown', models=models, usage_reason=reason,
                quota=_quota(limits, started) if reason == USAGE_UNCONFIRMED else None)


def _usage_problem(limits):
    """Even ordinary windows have unqualified cache age; extra constraints must not disappear."""
    by_limit = limits.get('rateLimitsByLimitId')
    if by_limit is not None and (type(by_limit) is not dict or set(by_limit) != {'codex'}):
        return 'codex_limits_unsupported'
    snapshot = by_limit.get('codex') if type(by_limit) is dict else limits.get('rateLimits')
    if type(snapshot) is not dict or snapshot.get('individualLimit') is not None \
            or snapshot.get('spendControlReached') not in (None, False) or snapshot.get('normalModelSlug') is not None:
        return 'codex_limits_unsupported'  # `spendControlReached: false` (observed 2026-11-16) is ordinary
    return USAGE_UNCONFIRMED


def _quota(limits, started):
    """The gate reading `{used_percent, reset_at, ordinary_allowed, read_at}` of the primary window (read start as its
    time), or None for any other shape. `rateLimitResetCredits` is a user action, never required here."""
    by_limit = limits.get('rateLimitsByLimitId')
    snapshot = by_limit.get('codex') if type(by_limit) is dict else limits.get('rateLimits')
    primary = snapshot.get('primary') if type(snapshot) is dict else None
    ordinary = limits.get('ordinaryUsageAllowed')
    if type(primary) is not dict or type(primary.get('usedPercent')) not in (int, float) \
            or not 0 <= primary['usedPercent'] <= 100 or type(primary.get('resetsAt')) is not int \
            or ordinary not in (None, True, False):
        return None
    return dict(used_percent=primary['usedPercent'], reset_at=primary['resetsAt'], ordinary_allowed=ordinary,
                read_at=started)


def quota_problem(settings_path, route, now):
    """`quota_exhausted` on a fresh (15 minutes) account-bound reading only: ordinary usage disallowed, or the window at
    100% with its reset ahead. Never a claim: `usable_windows` stays empty."""
    current = _account(settings_path, now) if applies(route) and route.get('pool') == POOL else None
    quota = (current or {}).get('quota')
    if type(quota) is not dict or not 0 <= now - quota['read_at'] <= QUOTA_FRESH_SECONDS:
        return None
    if quota['ordinary_allowed'] is False or quota['used_percent'] >= 100 and quota['reset_at'] > now:
        return 'quota_exhausted'
    return None


def _store(settings_path):
    return observations._resolved(observations.window_path(settings_path)).parent / STORE_FILE


def _state(settings_path):
    return observations._read(_store(settings_path))


def _save(settings_path, **change):
    _transition(settings_path, lambda state: change)


def _owned(path):
    """The state for a write: a malformed but safe private file proves nothing and is reinitialized by that write;
    an unsafe, oversized or unreadable one is never rewritten (it raises)."""
    try:
        return observations._read(path) or dict(version=1)
    except ValueError as error:
        if str(error) == 'observation_store_unsafe':
            raise
        return dict(version=1)


def _transition(settings_path, step):
    """One read-validate-write of the state under its lock: `step(state)` returns the change (ValueError aborts)."""
    with observations._locked(_store(settings_path)) as path:
        value = _owned(path)
        value.update(step(value))
        observations._write(path, value)


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None


def _proved(state, key=None):
    """The generation `state` proves (for `key`, when given): `identity={key, generation}` and a complete (same key
    and generation) or pending (`{pending: True, generation}`) account agree. Legacy or cleared states prove none."""
    identity = state.get('identity') if type(state) is dict else None
    found = state.get('account') if type(state) is dict else None
    if type(identity) is not dict or set(identity) != {'key', 'generation'} or not _hex(identity['key']) \
            or not _hex(identity['generation']) or key not in (None, identity['key']) or type(found) is not dict:
        return None
    if found == dict(pending=True, generation=identity['generation']) or 'pending' not in found and \
            found.get('key') == identity['key'] and found.get('generation') == identity['generation']:
        return identity['generation']
    return None


def _fence(state, held):
    """Before an exchange: keep in `held`, for this attempt only, the key and generation the old state proves, and
    leave an unprovable in-flight state (no identity; a pending account under a random fence), so that an interrupted
    or unrecorded observation never carries the generation forward."""
    generation = _proved(state)
    held.update(generation=generation, key=state['identity']['key'] if generation else None,
                fence=secrets.token_hex(16))
    return dict(identity=None, account=dict(pending=True, generation=held['fence']))


def _ours(state, held):
    return state.get('identity') is None and state.get('account') == dict(pending=True, generation=held.get('fence'))


def _settle(state, held):
    """After an exchange that observed no account (transport only): the proved continuity back, still pending."""
    if not held.get('generation') or not _ours(state, held):
        return {}
    return dict(identity=dict(key=held['key'], generation=held['generation']),
                account=dict(pending=True, generation=held['generation']))


def _observe(key, held):
    """The exchange's account observation, recorded before its catalog is validated: the proved generation only for
    the same key with this attempt's fence untouched, else a fresh random one; the account stays pending."""
    def step(state):
        same = held.get('generation') and key == held.get('key') and _ours(state, held)
        held['observed'] = held['generation'] if same else secrets.token_hex(16)
        return dict(identity=dict(key=key, generation=held['observed']),
                    account=dict(pending=True, generation=held['observed']))
    return step


def _account(settings_path, now):
    if settings_path is None:
        return None
    try:
        state = _state(settings_path)
    except (OSError, ValueError):
        return None
    found = state.get('account')
    if type(found) is not dict or 'pending' in found or _proved(state) is None \
            or not routing._amount(found.get('observed_at')) \
            or not 0 <= now - found['observed_at'] <= observations.FRESH_SECONDS \
            or found.get('client_version') != 'unknown' or found.get('usage_reason') not in REASONS:
        return None
    return found


def _binding_of(current):
    return dict(client_version=current['client_version'],
                connection='account:%s:%s' % (current['key'], current['generation']), controls=CONTROLS)


def binding(settings_path):
    """Current native metadata binding (key and generation); missing version remains the explicit `unknown`."""
    current = _account(settings_path, int(time.time()))
    return None if current is None else _binding_of(current)


def applies(route):
    """Whether this producer supplies a route's account: a subscription route on its exact native path (a dispatch
    recheck's route names no billing class; its path is subscription-only)."""
    return type(route) is dict and route.get('billing_class', 'included_subscription') == 'included_subscription' \
        and routing_paths.path_of(route.get('route')) == PATH


def billing_fact(settings_path, route, now):
    """Only this native path's account confirmation; unknown overflow is not a quota claim."""
    if not applies(route) or route.get('pool') != POOL:
        return None
    current = _account(settings_path, now)
    if current is None:
        return None
    return dict(producer='codex-native:%s:%s' % (current['key'][:16], current['generation']),
                **subscription_billing.scope(route), observed_at=current['observed_at'],
                valid_until=current['observed_at'] + observations.FRESH_SECONDS, paid_crossover='unknown')


def usable_windows(settings_path, now, stored=None):
    """No complete quota/cache-age source is qualified. Never relabel legacy rows or expose invented headroom."""
    return []


def refresh(settings_path, *, need=None, force=False, command=None, environ=None, timeout=lifecycle.TIMEOUT,
            rules=None, timing=None):
    """Acquire native metadata, not usable quota. `reason` stays explicit even for stored/fresh metadata.

    `need` and `rules` retain the provider signature but cannot qualify quota.
    The existing quota-only producer/callers and their window store stay unchanged.
    """
    environ = os.environ if environ is None else environ
    try:
        with lifecycle._pool_lock(settings_path, POOL, timeout + lifecycle.LOCK_EXTRA) as held:
            if not held:
                return dict(state='busy', reason='reader_busy')
            now = int(time.time())
            state = _owned(_store(settings_path))  # unsafe: no exchange; malformed: reinitialized by the fence
            current = _account(settings_path, now)
            snapshot = native_catalog.stored(settings_path, CLIENT)
            if not force and current is not None and snapshot is not None \
                    and snapshot['binding'] == _binding_of(current):
                return dict(state='fresh', windows=[], reason=current['usage_reason'])
            failure = state.get('failure')
            if not force and type(failure) is dict and routing._amount(failure.get('at')) \
                    and 0 <= now - failure['at'] < lifecycle.FAILURE_BACKOFF:
                reason = failure.get('reason')
                return dict(state='backoff', reason=reason if reason in REASONS else 'observation_store_invalid')
            held, observed = {}, []
            _transition(settings_path, lambda state: _fence(state, held))  # validated under the lock; unstored: none
            try:
                found = lifecycle.exchange(command or lifecycle.default_command(environ), environ, timeout, timing,
                                           _read=lambda session, init: _read(session, init, observed))
                _transition(settings_path, _observe(found['key'], held))
                found['generation'] = held['observed']
                native_catalog.store_snapshot(settings_path, CLIENT, dict(binding=_binding_of(found),
                                                                          models=found.pop('models')))

                def publish(state):  # the complete account, only while the state still proves its generation
                    if _proved(state, found['key']) != found['generation']:
                        raise ValueError('observation_store_invalid')
                    return dict(account=found, failure=None)
                _transition(settings_path, publish)
                return dict(state='stored', windows=[], reason=found['usage_reason'])
            except (ReadError, OSError, ValueError) as error:
                reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
                # An account refusal or an observed account or sign-in change ends continuity. Otherwise an account
                # observed before the failure (a refused catalog) is recorded, pending; with none observed (transport
                # only) the proved continuity comes back, pending. An unrecorded observation leaves the fence.
                ended = dict(account=None, identity=None) if reason in INVALIDATING else {}
                if not ended and 'observed' not in held:
                    _transition(settings_path, _observe(observed[0], held) if observed
                                else lambda state: _settle(state, held))
                _save(settings_path, failure=dict(at=int(time.time()), reason=reason), **ended)
                return dict(state='failed', reason=reason)
    except (ReadError, OSError, ValueError) as error:
        reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
        return dict(state='failed', reason=reason)
