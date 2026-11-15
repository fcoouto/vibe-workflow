"""Native Codex account/catalog metadata from the 0.167.2 stable app-server protocol.

LocalSource, the native catalog and the native bridge select it by its exact path
or client only. One bounded, read-only exchange
checks practical identity around a complete catalog and incidental quota read.
Only derived identity and exact launch aliases/efforts persist. No usage cache-age
bound or complete constraint mapping is qualified, so usable_windows always refuses
headroom. OpenCode's distinct ChatGPT connection is never inferred from this login.
"""
import hashlib
import json
import os
import re
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


def _read(session, init):
    started = int(time.time())
    session.send('account/read', 2, dict(refreshToken=False))
    key = parse_account(session.response(2))
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
    return dict(key=key, observed_at=started, client_version='unknown', models=models,
                usage_reason=_usage_problem(limits))


def _usage_problem(limits):
    """Even ordinary windows have unqualified cache age; extra constraints must not disappear."""
    by_limit = limits.get('rateLimitsByLimitId')
    if by_limit is not None and (type(by_limit) is not dict or set(by_limit) != {'codex'}):
        return 'codex_limits_unsupported'
    snapshot = by_limit.get('codex') if type(by_limit) is dict else limits.get('rateLimits')
    if type(snapshot) is not dict or snapshot.get('individualLimit') is not None \
            or snapshot.get('spendControlReached') is not None or snapshot.get('normalModelSlug') is not None:
        return 'codex_limits_unsupported'
    return USAGE_UNCONFIRMED


def _store(settings_path):
    return observations._resolved(observations.window_path(settings_path)).parent / STORE_FILE


def _state(settings_path):
    return observations._read(_store(settings_path))


def _save(settings_path, **change):
    with observations._locked(_store(settings_path)) as path:
        value = observations._read(path) or dict(version=1)
        value.update(change)
        observations._write(path, value)


def _account(settings_path, now):
    if settings_path is None:
        return None
    try:
        found = _state(settings_path).get('account')
    except (OSError, ValueError):
        return None
    if type(found) is not dict or type(found.get('key')) is not str \
            or re.fullmatch('[0-9a-f]{32}', found['key']) is None \
            or not routing._amount(found.get('observed_at')) \
            or not 0 <= now - found['observed_at'] <= observations.FRESH_SECONDS \
            or found.get('client_version') != 'unknown' or found.get('usage_reason') not in REASONS:
        return None
    return found


def binding(settings_path):
    """Current native metadata binding; missing version remains the explicit string `unknown`."""
    current = _account(settings_path, int(time.time()))
    return None if current is None else dict(client_version=current['client_version'],
                                             connection='account:' + current['key'], controls=CONTROLS)


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
    return dict(producer='codex-native:' + current['key'][:16], **subscription_billing.scope(route),
                observed_at=current['observed_at'], valid_until=current['observed_at'] + observations.FRESH_SECONDS,
                paid_crossover='unknown')


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
            state = _state(settings_path)
            current = _account(settings_path, now)
            snapshot = native_catalog.stored(settings_path, CLIENT)
            if not force and current is not None and snapshot is not None and snapshot['binding'] == dict(
                    client_version=current['client_version'], connection='account:' + current['key'], controls=CONTROLS):
                return dict(state='fresh', windows=[], reason=current['usage_reason'])
            failure = state.get('failure')
            if not force and type(failure) is dict and routing._amount(failure.get('at')) \
                    and 0 <= now - failure['at'] < lifecycle.FAILURE_BACKOFF:
                reason = failure.get('reason')
                return dict(state='backoff', reason=reason if reason in REASONS else 'observation_store_invalid')
            _save(settings_path, account=None)
            try:
                found = lifecycle.exchange(command or lifecycle.default_command(environ), environ, timeout, timing,
                                           _read=_read)
                native_catalog.store_snapshot(settings_path, CLIENT, dict(binding=dict(
                    client_version=found['client_version'], connection='account:' + found['key'], controls=CONTROLS),
                    models=found.pop('models')))
                _save(settings_path, account=found, failure=None)
                return dict(state='stored', windows=[], reason=found['usage_reason'])
            except (ReadError, OSError, ValueError) as error:
                reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
                _save(settings_path, account=None, failure=dict(at=int(time.time()), reason=reason))
                return dict(state='failed', reason=reason)
    except (ReadError, OSError, ValueError) as error:
        reason = str(error) if str(error) in REASONS else 'observation_store_invalid'
        return dict(state='failed', reason=reason)
