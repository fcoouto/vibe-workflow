"""Pure normalization of OpenCode's usage limits (`GET /api/experimental/usage/limits`), F4.5.

The input is the installed OpenCode 2.2.6 response `{location, data: UsageLimits.Info | null}`, the same shape as the
public OpenAPI document: `Info = {workspace: {id, name?, usageURL}, go?: {rolling, weekly, monthly}, budgets: [...]}`
and each meter `{percent: 0-100, limited: bool, resetsAt: milliseconds since the epoch}`. Budgets are Console cash
limits, never subscription capacity, and are not read.

Whether the weekly and monthly meters are rolling or fixed, the monthly length, when the reading was originally
observed (the response has no fetched-at field and no known cache-age ceiling) and how a model's demand maps onto
these aggregate meters are all unknown. So a valid response is reported as parsed meters with
`window_semantics_unknown`, never as an admission window: nothing here dates, stores or qualifies a reading, and
`observed_at`, the caller's processing time, is used only to reject a meter whose reset has already passed.

`normalize_limits` reads no file, process, client, credential, network, store or clock. The account label is a
digest of the workspace id; the raw id, name, URL and location are never returned. A label names a scope, not an
entitlement or a billing boundary.

`refresh` is the collector-side wiring for the model-free Go path (`opencode-go-subscription`): one bounded read
through the launcher host's own request transport, only for a kept, statically eligible Go route whose stored
readings are missing or stale, outside served preparation. It needs a qualified meter binding (`window_semantics`):
the rolling/fixed semantics, lengths and original observation time that turn parsed meters into store windows. None is
qualified, so production returns before any preview or read and stores nothing; a Go route then stays
`window_unobserved`. Only code (a labelled test double) can supply a binding.
"""
from decimal import Decimal
import hashlib
import math
import time

METERS = ('rolling', 'weekly', 'monthly')
WINDOW_UNITS = 1000  # utilization thousandths, as routing_observations stores subscription windows
ACCOUNT_PREFIX = 'opencode-go:'
MAX_TEXT = 256
UNKNOWN = ('rolling_or_fixed', 'window_length', 'observation_time', 'demand')


def _unavailable(reason):
    return dict(state='unavailable', reason=reason)


def _number(value):
    """A finite JSON number: a float that is finite, or an int a float can hold (a larger one is invalid, never an
    OverflowError). Bools are not numbers."""
    if type(value) is int:
        try:
            float(value)
        except OverflowError:
            return False
        return True
    return type(value) is float and math.isfinite(value)


def _text(value):
    return type(value) is str and 0 < len(value) <= MAX_TEXT and value.isprintable()


def _workspace(value):
    if type(value) is not dict or not {'id', 'usageURL'} <= set(value) <= {'id', 'name', 'usageURL'}:
        return False
    return _text(value['id']) and type(value['usageURL']) is str and ('name' not in value or type(value['name']) is str)


def _meter(value, now):
    """(normalized meter, None) or (None, reason)."""
    if type(value) is not dict or set(value) != {'percent', 'limited', 'resetsAt'}:
        return None, 'meter_invalid'
    percent, limited, resets_at = value['percent'], value['limited'], value['resetsAt']
    if not (_number(percent) and 0 <= percent <= 100 and type(limited) is bool and _number(resets_at)
            and resets_at > 0):
        return None, 'meter_invalid'
    reset_at = resets_at / 1000  # exact: never a later reset than reported
    if reset_at <= now:
        return None, 'reset_elapsed'  # the reading no longer describes the current window
    # Round up from the number's own decimal text (0.1% is exactly 1 thousandth, any amount above it is 2): binary
    # float noise is never added, and no positive usage is removed before the ceiling. A limited meter is exhausted.
    used = WINDOW_UNITS if limited else min(WINDOW_UNITS, math.ceil(Decimal(repr(percent)) * WINDOW_UNITS / 100))
    return dict(used=used, limit=WINDOW_UNITS, limited=limited, reset_at=reset_at), None


def account_label(workspace_id):
    """The nonsecret account label of a Console workspace id (a digest; never the id itself)."""
    return ACCOUNT_PREFIX + hashlib.sha256(workspace_id.encode()).hexdigest()[:16]


def normalize_limits(payload, *, observed_at):
    """`{state: 'unavailable', reason}` for every response. A signed-out location is `not_signed_in`; a response
    without Go data is `go_unavailable`; a malformed envelope, workspace or meter is `usage_malformed`,
    `workspace_invalid`, `meter_missing` or `meter_invalid`; a meter past its reset is `reset_elapsed`. A valid
    response adds the account label, the three normalized meters and the unknown semantics, with the reason
    `window_semantics_unknown`: it is parsed, not an admission observation."""
    if not (_number(observed_at) and observed_at > 0):
        return _unavailable('observed_at_invalid')
    if type(payload) is not dict or set(payload) != {'location', 'data'}:
        return _unavailable('usage_malformed')
    data = payload['data']
    if data is None:
        return _unavailable('not_signed_in')
    if type(data) is not dict or not {'workspace', 'budgets'} <= set(data) <= {'workspace', 'go', 'budgets'} \
            or type(data['budgets']) is not list:
        return _unavailable('usage_malformed')
    if not _workspace(data['workspace']):
        return _unavailable('workspace_invalid')
    go = data.get('go')
    if go is None:
        return _unavailable('go_unavailable')
    if type(go) is not dict or not set(go) <= set(METERS):
        return _unavailable('meter_invalid')
    if set(go) != set(METERS):
        return _unavailable('meter_missing')
    meters = {}
    for name in METERS:
        meters[name], reason = _meter(go[name], observed_at)
        if reason is not None:
            return _unavailable(reason)
    account = account_label(data['workspace']['id'])
    return dict(_unavailable('window_semantics_unknown'), account=account, meters=meters, unknown=list(UNKNOWN))


# -- the Go path's usage refresh (F4.5) ---------------------------------------------------------------------------
GO_PROVIDER = 'opencode-go'
USAGE_PATH = '/api/experimental/usage/limits'
# Supervision, shared with the Codex reader's private patterns (`codex_rate_limits`): one owner-only non-blocking
# lock per pool next to the window store (`reader_busy` at once, no waiting), and a failed or unusable read recorded as
# `{at, reason}` only, so no new read starts for FAILURE_BACKOFF seconds.
STATE_FILE = 'opencode-go-usage.json'
FAILURE_BACKOFF = 300  # seconds; the Codex reader's `FAILURE_BACKOFF`, kept equal by a test
LOCK_PREFIX = 'opencode-go.'


def window_semantics():
    """The qualified binding `(parsed, *, requested_at, completed_at) -> [window]` from `normalize_limits`' parsed
    meters to store windows, or None. No binding is qualified (capability matrix, OpenCode Go binding assessment):
    production returns None, so no Go reading is ever read or stored."""
    return None


def _unavailable_reading(reason):
    return dict(state='unavailable', reason=reason)


def refresh(service, read, request, *, now):
    """Make the kept Go routes' readings fresh before `prepare` (the launcher calls this outside served preparation).

    `service` is the pinned routing service (its internal `preview` gives the request's shared shortlist), `read()`
    returns the host's `(status, body)` for USAGE_PATH and `request` is the `prepare` body. Returns `{state: 'skipped' |
    'fresh' | 'stored' | 'unavailable', reason?}`; nothing is stored unless every constraint of the path is covered."""
    from runtime import routing_observations as collector, routing_paths
    binding = window_semantics()
    if binding is None:
        return dict(state='skipped', reason='window_semantics_unqualified')
    settings_path = service.settings_path
    paths = {item['path']: item for item in routing_paths.resolved(routing_paths.load_rules(),
                                                                   routing_paths.qualifications(settings_path))
             if item['provider'] == GO_PROVIDER}
    body = {key: value for key, value in request.items() if key not in ('v', 'op')}
    preview = service.preview(body) if paths else {}
    kept = sorted({routing_paths.path_of(route) for route in preview.get('routes', [])} & set(paths))
    if not kept:
        return dict(state='skipped', reason='no_kept_go_route')
    source = collector.LocalSource(settings_path)

    def fresh(path):
        stored = {window['id']: window for window in source.windows(path['pool'], now)}
        boundary = collector.consumed_at(settings_path, path['pool'])
        return all(window in stored and 0 <= now - stored[window]['observed_at'] <= collector.FRESH_SECONDS
                   and stored[window]['observed_at'] > boundary
                   and (stored[window].get('reset_at') is None or stored[window]['reset_at'] > now)
                   for window in path['windows'])
    stale = [paths[name] for name in kept if not fresh(paths[name])]
    if not stale:
        return dict(state='fresh')
    from runtime import codex_rate_limits as reader
    pool = stale[0]['pool']
    try:
        with reader._pool_lock(settings_path, LOCK_PREFIX + pool) as held:
            if not held:
                return _unavailable_reading('reader_busy')
            if all(fresh(path) for path in stale):  # another reader stored a reading meanwhile
                return dict(state='fresh')
            folder = reader._store_dir(settings_path)
            failure = _failure(folder, pool)
            if type(failure) is dict and type(failure.get('at')) is int \
                    and 0 <= int(time.time()) - failure['at'] < FAILURE_BACKOFF:
                return dict(state='backoff', reason=failure.get('reason'))
            outcome = _read_and_store(settings_path, read, stale, binding, now)
            if outcome['state'] != 'stored':
                _record_failure(folder, pool, dict(at=int(time.time()), reason=outcome['reason']))
            elif failure is not None:
                _record_failure(folder, pool, None)
            return outcome
    except (OSError, ValueError, reader.ReadError):
        return _unavailable_reading('observation_store_unsafe')


def _failure(folder, pool):
    from runtime import routing_observations as collector
    try:
        return collector._read(folder / STATE_FILE).get('pools', {}).get(pool)
    except (OSError, ValueError):
        return None


def _record_failure(folder, pool, failure):
    from runtime import routing_observations as collector
    with collector._locked(folder / STATE_FILE) as path:
        value = collector._read(path) or dict(version=1, pools={})
        value['pools'][pool] = failure
        collector._write(path, value)


def _read_and_store(settings_path, read, stale, binding, now):
    """One read under the pool lock: parsed, account-checked, bound and stored only when every constraint fits."""
    from runtime import routing_observations as collector
    requested_at = int(time.time())
    try:
        status, payload = read()
    except Exception:  # noqa: BLE001 - a lost read leaves the route stale or unobserved; never retried here
        return _unavailable_reading('usage_unreadable')
    if status != 200:
        return _unavailable_reading('usage_unreadable')
    parsed = normalize_limits(payload, observed_at=now)
    if parsed.get('reason') != 'window_semantics_unknown':
        return _unavailable_reading(parsed.get('reason', 'usage_malformed'))
    if any(parsed['account'] != path['account'] for path in stale):
        return _unavailable_reading('account_mismatch')  # another workspace: never this connection's reading
    try:
        windows = binding(parsed, requested_at=requested_at, completed_at=int(time.time()))
    except Exception:  # noqa: BLE001
        windows = None
    for path in stale:
        if not (type(windows) is list and sorted(item.get('id') for item in windows if type(item) is dict)
                == sorted(path['windows']) and all(collector._valid_window(item) and 'source' not in item
                                                   and item.get('unit') == path['window_unit'] for item in windows)):
            return _unavailable_reading('window_semantics_unusable')
    for path in stale:
        for window in windows:
            collector.store_window(settings_path, path['pool'], window)
    return dict(state='stored')
