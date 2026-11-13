"""The Smart Routing observation collector (C.3 design 3.3, 3.4): the `observations` snapshot `prepare` trusts.

It reads local trusted records only: the path rules and qualifications (routes are generated in memory from them,
never stored), stored rate-limit observations, the entitlement record, the ledger it is given and
`openrouter_settings.status`. It never reads a secret, makes a network call, calls a model or takes the OpenRouter
admission lock; the one exception is the opt-in Codex window read (`refresh_windows`, R7), which runs
`codex app-server` read-only for a kept stale route. Window and entitlement files live next to the routing settings
path the caller gives, never under a path derived from HOME.
"""
from contextlib import closing, contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from runtime import routing, routing_funding, routing_paths, subscription_billing, usage_budget

WINDOWS_FILE = 'window-observations.json'
ENTITLEMENTS_FILE = 'entitlements.json'
FRESH_SECONDS = usage_budget.FRESH_SECONDS  # the same 900 s freshness the ledger applies to balances
MAX_FILE = 1 << 20
MAX_MARKS = 256
CLOCK_MARGIN = routing.MAX_CLOCK_DRIFT  # seconds a stored observation may run ahead of this clock
LEDGER_WAIT = 0.2  # seconds: a busy ledger leaves dollar routes unverified, it never stalls prepare
# The unit of stored subscription window observations: a thousandth of the provider's utilisation (limit 1000). A
# subscription path declares it (`window_unit`) and each stored window carries it (`unit`), so a route's `demand` and
# its windows can never be in different units; the OpenCode launcher (C3-3) stores windows in this unit by this name.
WINDOW_UNIT = routing_paths.WINDOW_UNIT
# The host's subscription windows in that unit, defined once here for routing: every window's limit (its full scale)
# and each provider window's (id, length in seconds). The OpenCode launcher stores its observations with these values;
# a test keeps its own copies equal until it re-exports these.
WINDOW_UNITS = 1000
RATE_WINDOWS = dict(five_hour=('5h', 5 * 3600), seven_day=('7d', 7 * 86400))
# The OpenRouter route each gateway harness needs enabled in `openrouter_settings` (`routes`).
INTEGRATION_ROUTES = ('proposals', 'workers')
ROUTE_FIELDS = ('route', 'strategy_id', *routing.ROUTE_IDENTITY, 'account', 'billing_class', 'pool', 'demand',
                'next_request_bound')
WINDOW_FIELDS = ('id', 'limit', 'used', 'length', 'reset_at', 'rolling', 'protected', 'organization',
                 'organization_revision', 'unit')
# User decision 6 (C.3 design 7.1): the one-shot operator op `seed_window` stores a subscription window no host has
# reported yet, marked with this `source`. Only `seed_window` writes it; the host path (`store_window`) never can.
SEED_KIND = 'seed_window'
SEED_SOURCE = 'operator'
SEED_FIELDS = ('pool', 'window', 'used', 'limit', 'reset_at', 'length', 'event_id')
STORE_ERRORS = ('observation_store_unsafe', 'observation_store_invalid', 'observation_store_full')


SUFFIX = re.compile(r'choice:[0-9a-f]{16}|' + routing_paths.STRATEGY_ID.pattern)  # what generation produces
STORE_REASONS = ('route_manifest_invalid', 'path_qualifications_invalid')


def _same(route, identity, fields=routing.ROUTE_IDENTITY):
    return identity is None or (type(identity) is dict and all(route.get(key) == identity.get(key) for key in fields))


class StaticSource:
    """A source built in code (tests, the fixture and the self-check sandbox); never selected by input. Its routes
    are a fixed list, so it answers every lookup from that list and has no `routes_version`.

    `windows` maps a pool to its window observations or is a callable `(pool, now)`; `credential` is a callable
    `(route, scope)` returning binding readiness metadata `{state, scope, ref}` or None; `billing` is a callable
    `(route, now)` returning a subscription billing fact (`runtime/subscription_billing.py`), normally that module's
    labelled `double`. Without it every subscription route is `billing_unverified`. `model_demand` is a callable
    `(route) -> bool`: whether a `demand_scope: per_model` path's demand is verified for the route's model; without it
    such routes are `demand_unverified`."""

    def __init__(self, routes, windows=None, credential=None, billing=None, model_demand=None):
        self.routes, self._windows, self._credential = copy.deepcopy(list(routes)), windows or {}, credential
        self._billing, self._model_demand = billing, model_demand

    def manifest(self, table=None, choices=()):
        return copy.deepcopy(self.routes)

    def admitted_route(self, route_id, identity=None):
        found = next((route for route in validate_routes(self.routes) if type(route) is dict
                      and route.get('route') == route_id and route.get('status') == 'qualified'), None)
        return copy.deepcopy(found) if found is not None and _same(found, identity) else None

    def pins_endpoint(self, route_id, identity, endpoint):
        found = self.admitted_route(route_id, identity)
        return found is not None and found.get('endpoint') == endpoint

    def seedable(self, pool, window):
        routes = [route for route in self.routes if type(route) is dict and route.get('pool') == pool]
        return not any(route.get('billing_class') in routing.FUNDED for route in routes) and any(
            route.get('status') == 'qualified' and route.get('billing_class') == 'included_subscription'
            and type(route.get('demand')) is dict and window in route['demand'] for route in routes)

    def routes_version(self, table=None):
        return None

    def windows(self, pool, now):
        found = self._windows(pool, now) if callable(self._windows) else self._windows.get(pool, [])
        return copy.deepcopy(list(found))

    def credential(self, route, scope):
        return None if self._credential is None else self._credential(route, scope)

    def billing(self, route, now):
        return None if self._billing is None else self._billing(route, now)

    def model_demand(self, route):
        return None if self._model_demand is None else self._model_demand(route)


class LocalSource:
    """The production source: routes generated in memory from the shipped path rules, the path qualifications and
    the request's table or choices; the private window store; and no secret. Nothing it answers is written.

    It reports no credential. For gateway routes the collector derives binding readiness itself from the cached
    `openrouter_settings.status` report and never asks a source; direct API routes (API Usage) stay unwired, so they
    report no credential (`credential_unavailable`) and fail closed until that wiring lands after the release."""

    def __init__(self, settings_path=None):
        self.settings_path = settings_path

    def _paths(self):
        """(rules, qualifications), or ValueError with a fixed reason."""
        return routing_paths.load_rules(), routing_paths.qualifications(self.settings_path)

    def manifest(self, table=None, choices=()):
        if self.settings_path is None:
            return []
        return routing_paths.generate(*self._paths(), table, choices)['routes']

    def _path(self, route_id, identity):
        """The qualified resolved path a well-formed route id names, when `identity` (if any) is on its provider and
        harness; else None."""
        path_id, _, suffix = (route_id if type(route_id) is str else '').partition(':')
        if self.settings_path is None or not SUFFIX.fullmatch(suffix):
            return None
        rules, qualified = self._paths()
        found = next((item for item in routing_paths.resolved(rules, qualified) if item['path'] == path_id), None)
        return found if found is not None and _same(found, identity, ('provider', 'harness')) else None

    def admitted_route(self, route_id, identity=None):
        found = self._path(route_id, identity)
        return None if found is None else dict(route=route_id, **{key: found[key] for key in (
            'path', 'pool', 'account', 'billing_class', 'provider', 'harness')})

    def pins_endpoint(self, route_id, identity, endpoint):
        """Whether `route_id` is the choice route of `identity` at `endpoint` on its path. A strategy route never
        pins one: no launcher sends a ranked gateway admission (decision B)."""
        found = self._path(route_id, identity)
        return found is not None and type(identity) is dict and route_id == routing_paths.choice_id(
            found['path'], found['provider'], identity.get('model'), identity.get('effort'), found['harness'], endpoint)

    def seedable(self, pool, window):
        if self.settings_path is None:
            return False
        rules, qualified = self._paths()
        return not any(item['pool'] == pool for item in rules['paths']
                       if item['billing_class'] != routing_paths.SUBSCRIPTION) and any(
            item['pool'] == pool and window in item['windows'] for item in routing_paths.resolved(rules, qualified)
            if item['billing_class'] == routing_paths.SUBSCRIPTION)

    def routes_version(self, table=None):
        """16 hex characters naming the rules, the qualifications and the table version routes come from."""
        rules, qualified = self._paths() if self.settings_path is not None else (routing_paths.load_rules(), {})
        version = table.get('version') if type(table) is dict else None
        raw = json.dumps([rules, qualified, version], sort_keys=True, separators=(',', ':')).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def windows(self, pool, now, stored=None):
        """`pool`'s stored readings; from `stored` (one read of the window store) when the caller already has it."""
        if self.settings_path is None:
            return []
        stored = (_read(window_path(self.settings_path)) if stored is None else stored).get('pools', {})
        return [dict(window, id=key) for key, window in sorted(stored.get(pool, {}).items())]

    def credential(self, route, scope):
        return None

    def billing(self, route, now):
        """No qualified subscription-only billing producer exists (F4.5/F4.6): none, read from nowhere."""
        return None

    def model_demand(self, route):
        """No per-model demand mapping is qualified (OpenCode Go's allowance is model-dependent): none."""
        return None


def default_source(settings_path=None):
    """The local source. Only code replaces it (a test patches this attribute); nothing a request, setting,
    table, environment variable or command-line flag carries can select another."""
    return LocalSource(settings_path)


def assignment_scope(checkout):
    """The assignment `scope` credential readiness is compared with: a digest of the checkout's real path."""
    return 'project:' + hashlib.sha256(os.fsencode(os.path.realpath(checkout))).hexdigest()[:32]


def validate_routes(routes):
    """`routes`, or ValueError when a fixed route list is unusable. Generated routes are never ambiguous
    (`routing_paths.generate` skips them); a fixed list qualifying one identity at two endpoints (an absent endpoint
    counts as one) could be confused at dispatch, so the whole list is refused instead of picking one."""
    if type(routes) is not list:
        raise ValueError('route_manifest_invalid')
    endpoints = {}
    for route in routes:
        if type(route) is dict and route.get('status') == 'qualified':
            identity = tuple(json.dumps(route.get(key)) for key in routing.ROUTE_IDENTITY)
            if endpoints.setdefault(identity, route.get('endpoint')) != route.get('endpoint'):
                raise ValueError('route_manifest_endpoint_ambiguous')
    return routes


# -- private local stores ----------------------------------------------------------------------------------------
def window_path(settings_path):
    return Path(settings_path).parent / WINDOWS_FILE


def entitlement_path(settings_path):
    return Path(settings_path).parent / ENTITLEMENTS_FILE


def _resolved(path):
    path = Path(path).absolute()
    return path.parent.resolve() / path.name


def _read(path):
    """A private JSON object, or {} when absent. Unsafe or malformed files raise ValueError."""
    path = _resolved(path)
    try:
        usage_budget.private(path.parent, True)
        info = usage_budget.private(path)
    except FileNotFoundError:
        return {}
    except usage_budget.BudgetError:
        raise ValueError('observation_store_unsafe') from None
    if info.st_size > MAX_FILE:
        raise ValueError('observation_store_unsafe')
    value = json.loads(path.read_bytes())
    if type(value) is not dict or value.get('version') != 1:
        raise ValueError('observation_store_invalid')
    return value


@contextmanager
def _locked(path):
    path = _resolved(path)
    for folder in reversed([p for p in (path.parent, *path.parent.parents) if not p.exists()]):
        folder.mkdir(mode=0o700)
    usage_budget.private(path.parent, True)
    lock = os.open(path.parent / '.observations.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield path
    finally:
        os.close(lock)


def _write(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_FILE:
        raise ValueError('observation_store_full')
    temporary = path.parent / ('.observations-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _valid_window(window):
    if type(window) is not dict or not set(window) <= {*WINDOW_FIELDS, 'observed_at', 'source'} \
            or window.get('source', SEED_SOURCE) != SEED_SOURCE:
        return False
    if 'unit' in window and not routing._text(window['unit']):
        return False
    numbers = all(window.get(key) is None or routing._finite(window[key])
                  for key in ('limit', 'used', 'length', 'reset_at'))
    return (routing._part(window.get('id')) and numbers and routing._finite(window.get('limit'))
            and window['limit'] > 0 and (window.get('used') is None or window['used'] >= 0)
            and routing._amount(window.get('observed_at')) and type(window.get('rolling', False)) is bool
            and (window.get('organization') is None or routing._text(window['organization'])))


def pool_state(stored, pool):
    """A digest of `pool`'s part of one read of the window store: its stored readings and its consumed boundary.
    Any reading stored for the pool, or a moved boundary, changes it; another pool's activity does not."""
    raw = json.dumps([stored.get('pools', {}).get(pool), stored.get('consumed', {}).get(pool)], sort_keys=True,
                     separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def consumed_at(settings_path, pool):
    """When a launch that reported no reading last settled on `pool` (0 when none, or with no settings path). A
    reading observed at or before it no longer counts as fresh: the launch used headroom that reading still shows."""
    if settings_path is None:
        return 0
    value = _read(window_path(settings_path)).get('consumed', {}).get(pool)
    return value if routing._amount(value) else 0


MAX_CONSUMED_BY = 256  # recent attempts per pool whose completion already wrote the boundary


def mark_consumed(settings_path, pool, at, attempt):
    """Record that `attempt`'s launch settled on `pool` at `at` without a reading (native completion, F4.6), once per
    attempt: under the store's lock a repeated or concurrent call for the same attempt changes nothing. The time only
    moves forward; the pool's readings stay as they are, and a reading observed after it restores the pool. True when
    this call wrote it."""
    if not routing._part(pool) or not routing._amount(at) or not routing._text(attempt):
        raise ValueError('invalid_window_observation')
    with _locked(window_path(settings_path)) as path:
        value = _read(path) or dict(version=1, pools={})
        done = value.setdefault('consumed_by', {}).setdefault(pool, [])
        if attempt in done:
            return False
        done.append(attempt)
        del done[:-MAX_CONSUMED_BY]
        consumed = value.setdefault('consumed', {})
        consumed[pool] = max(at, consumed.get(pool, 0))
        _write(path, value)
        return True


def store_window(settings_path, pool, window):
    """Record one window observation for `pool` (the launcher stores what it read after settlement). An older
    observation never replaces a newer one, and one dated beyond now plus CLOCK_MARGIN is refused, so a future-dated
    record can never shadow later ones. A host observation always replaces an operator seed (`seed_window`), and
    it can never carry a `source` itself. True when the store holds exactly this observation afterwards (stored now,
    or already stored); False when a newer reading was kept instead."""
    if not routing._part(pool) or not _valid_window(window) or 'source' in window \
            or window['observed_at'] > time.time() + CLOCK_MARGIN:
        raise ValueError('invalid_window_observation')
    with _locked(window_path(settings_path)) as path:
        value = _read(path) or dict(version=1, pools={})
        windows = value['pools'].setdefault(pool, {})
        current = windows.get(window['id'])
        entry = {key: item for key, item in window.items() if key != 'id'}
        if current is None or current.get('source') == SEED_SOURCE or window['observed_at'] > current['observed_at']:
            windows[window['id']] = entry
            _write(path, value)
            return True
        return current == entry


def _host_windows():
    """(full scale, {window id: length}) the host path stores subscription windows with, so a seed is in exactly
    the unit, scale and length a host observation would have."""
    return WINDOW_UNITS, dict(RATE_WINDOWS.values())


def seed_event(value):
    """The operator's `seed_window` request as a ledger event, or None when malformed: `used`, `limit`, `length` and
    `reset_at` are integers (never bools or floats) with `0 <= used <= limit`, `limit > 0` and `length > 0`, and
    `limit` is the host's full scale (1000 in utilization_thousandths): a larger one would multiply the headroom.
    The window's length and the clock bounds on `reset_at` are checked when it is applied, after a replay is
    recognised."""
    if type(value) is not dict or set(value) != set(SEED_FIELDS) or \
            not routing._bounded_id(value['event_id'], usage_budget.MAX_EVENT_ID):
        return None
    if not (routing._part(value['pool']) and routing._part(value['window'])
            and all(routing._amount(value[key]) for key in ('used', 'limit', 'length', 'reset_at'))):
        return None
    if not (0 < value['limit'] == _host_windows()[0] and value['used'] <= value['limit'] and 0 < value['length']):
        return None
    return dict(value, kind=SEED_KIND)


def _subscription_window(settings_path, pool, window, length):
    """Whether `window` of `pool` is a subscription window of that `length`: the source's `seedable` (a qualified
    subscription path on the pool lists the window, plans differ in which windows they have, and no dollar path draws
    on the pool; no table is needed) and the host stores that window id with exactly that length."""
    if _host_windows()[1].get(window) != length:
        return False
    try:
        return default_source(settings_path).seedable(pool, window) is True
    except (OSError, ValueError) as error:
        reason = str(error) if str(error) in STORE_REASONS else 'route_manifest_invalid'
        raise usage_budget.BudgetError(reason) from None


def _store_seed(settings_path, event, now):
    """Write the seed unless a fresh (or future-dated) host observation of the window exists; an organization window
    is never seeded. A stale host observation or an earlier seed is replaced."""
    with _locked(window_path(settings_path)) as path:
        value = _read(path) or dict(version=1, pools={})
        windows = value['pools'].setdefault(event['pool'], {})
        current = windows.get(event['window'])
        if current is not None:
            usage_budget.require(type(current) is dict and routing._amount(current.get('observed_at')),
                                 'observation_store_invalid')
            usage_budget.require(current.get('organization') is None, 'invalid_request')
            usage_budget.require(current.get('source') == SEED_SOURCE or now - current['observed_at'] > FRESH_SECONDS
                                 or current['observed_at'] <= value.get('consumed', {}).get(event['pool'], 0),
                                 'fresh_observation_exists')  # a reading a settled launch already used is stale
        windows[event['window']] = dict({key: event[key] for key in ('used', 'limit', 'reset_at', 'length')},
                                        rolling=False, unit=WINDOW_UNIT, source=SEED_SOURCE, observed_at=now)
        _write(path, value)


def seed_window(ledger, event, now, settings_path):
    """Apply one `seed_event` inside one ledger write transaction, idempotent by event id in the S3.6a event table:
    the same payload returns the first outcome with `duplicate: true`, another is `event_conflict`, and refusals are
    not remembered. The audit is the event row: the pool and window labels and the seed's integers only."""
    try:
        usage_budget.integer(now)
        digest = routing.event_digest(event)
        with ledger.transaction() as data:
            usage_budget._create_tables(ledger.db)
            known = ledger._event(data, event['event_id'], now)
            if known is not None:
                usage_budget.require(known[0] == digest, 'event_conflict')
                return dict(json.loads(known[1])['outcome'], duplicate=True)
            usage_budget.require(now < event['reset_at'] <= now + event['length'], 'invalid_request')
            usage_budget.require(_subscription_window(settings_path, event['pool'], event['window'], event['length']),
                                 'invalid_request')
            _store_seed(settings_path, event, now)
            outcome = dict({key: event[key] for key in ('pool', 'window', 'used', 'limit', 'reset_at', 'length')},
                           state='recorded', duplicate=False, outcome='window_seeded', observed_at=now)
            label = json.dumps([event['pool'], event['window']]).encode()
            body = dict(attempt_id='seed:' + hashlib.sha256(label).hexdigest()[:16], kind=SEED_KIND, recorded_at=now,
                        outcome=dict(outcome))
            ledger._store_event(data, event['event_id'], digest, body, now, effect=True)
            return outcome
    except usage_budget.BudgetError as error:
        return dict(state='blocked', reason=str(error))
    except ValueError as error:
        return dict(state='blocked', reason=str(error) if str(error) in STORE_ERRORS else 'observation_store_invalid')


def window_status(settings_path, now):
    """What `status` shows of the window store: each stored window's pool, id, `source` (`operator` for a seed in
    use, else `host`), observation time and freshness, 16 at most plus `more`; never the store's other content."""
    try:
        pools = _read(window_path(settings_path)).get('pools', {})
        items = [dict(pool=pool, window=key, source=window.get('source', 'host'), observed_at=window['observed_at'],
                      fresh=0 <= now - window['observed_at'] <= FRESH_SECONDS)
                 for pool, windows in sorted(pools.items()) for key, window in sorted(windows.items())]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        reason = str(error) if str(error) in STORE_ERRORS else 'observation_store_invalid'
        return dict(state='unavailable', reason=reason)
    limit = usage_budget.SUMMARY_IDS
    return dict(state='ok', windows=items[:limit], more=max(0, len(items) - limit))


def entitlement_marks(settings_path):
    return list(_read(entitlement_path(settings_path)).get('marks', []))


def mark_not_entitled(settings_path, route, account, at):
    """C.3 design 3.4: the provider refused an admitted route for this account. Only the user clears it."""
    if not (routing._text(route) and routing._text(account) and routing._amount(at)):
        raise ValueError('invalid_entitlement_mark')
    with _locked(entitlement_path(settings_path)) as path:
        value = _read(path) or dict(version=1, marks=[])
        if not any(mark['route'] == route and mark['account'] == account for mark in value['marks']):
            if len(value['marks']) >= MAX_MARKS:
                raise ValueError('entitlement_record_full')
            value['marks'].append(dict(route=route, account=account, mark='not_entitled', at=at))
            _write(path, value)


def clear_not_entitled(settings_path, route, account):
    """The user's local clearing of a mark; True when one was removed."""
    with _locked(entitlement_path(settings_path)) as path:
        value = _read(path) or dict(version=1, marks=[])
        kept = [mark for mark in value['marks'] if (mark['route'], mark['account']) != (route, account)]
        if len(kept) == len(value['marks']):
            return False
        _write(path, dict(value, marks=kept))
        return True


# -- collection --------------------------------------------------------------------------------------------------
def _manifest_reason(route):
    """Why a manifest entry is never a candidate, or None."""
    if type(route) is not dict or not routing._text(route.get('route')):
        return 'route_invalid'
    if route.get('status') != 'qualified':
        return 'route_unqualified'
    if route.get('billing_class') not in routing.FUNDING_OF:  # purchased_usage, ineligible: never a candidate
        return 'billing_class_excluded' if routing._text(route.get('billing_class')) else 'route_invalid'
    shaped = {key: route[key] for key in ROUTE_FIELDS if key in route}
    complete = all(routing._text(route.get(key)) for key in (*routing.ROUTE_IDENTITY, 'account')) \
        and routing._part(route.get('pool')) and route.get('demand') and routing._valid_route(shaped)
    if not complete or (route['billing_class'] == 'gateway' and route.get('integration_route') not in INTEGRATION_ROUTES):
        return 'route_invalid'
    return None


def _openrouter(project):
    """(report, None) from `openrouter_settings.status` (a brief shared lock, never `admission`), or (None, reason).
    Busy is answered at once and never retried here."""
    from runtime import openrouter_settings
    try:
        return openrouter_settings.status(project), None
    except openrouter_settings.SettingsError as error:
        return None, 'openrouter_operation_busy' if str(error) == 'openrouter_operation_busy' else 'openrouter_unavailable'


def _gateway_credential(report):
    """Binding readiness `{state, scope, ref}` of an enabled, ready OpenRouter report, or None. Only the checkout and
    the activation reference are read; no secret, path or other settings content enters the result."""
    activation = report.get('activation')
    ref = activation.get('ref') if type(activation) is dict else None
    checkout = report.get('checkout')
    if not routing._text(ref) or not routing._text(checkout):
        return None
    return dict(state='ready', scope=assignment_scope(checkout), ref=ref)


def _funding_verified(data, organization, now):
    """A ledger check only: the organization is enabled and its balance observation is at most 900 s old."""
    org = (data or {}).get('organizations', {}).get(organization) if routing._text(organization) else None
    return (org is not None and not org.get('disabled', False) and type(org.get('observed_at')) is int
            and 0 <= now - org['observed_at'] <= FRESH_SECONDS)


def _ledger_data(ledger_path):
    """The ledger body at `ledger_path`, read-only and briefly (never a write, never a wait), {} when absent, or
    None when it cannot be read: every dollar route is then `funding_unverified`."""
    path = _resolved(ledger_path)
    if not os.path.lexists(path):
        return {}
    try:
        usage_budget.private(path.parent, True)
        usage_budget.private(path)
        with closing(sqlite3.connect('%s?mode=ro' % path.as_uri(), uri=True, timeout=LEDGER_WAIT)) as db:
            row = db.execute('SELECT body FROM ledger WHERE id=1').fetchone()
        if row is None or len(row[0].encode()) > usage_budget.MAX_RECORD:
            return None
        data = json.loads(row[0])
    except (OSError, ValueError, sqlite3.Error, usage_budget.BudgetError):
        return None
    return data if type(data) is dict and type(data.get('organizations')) is dict else None


def refresh_needed(specs, settings_path, marks=()):
    """R7: make the Codex pool's reading fresh before it is read, only for the routes `shortlist` kept for this
    request (`runtime/codex_rate_limits.py`: on demand, one reader per pool, a fixed reason and no retry on failure).
    Returns `{pool: reason}` for each pool left without a fresh reading (`reader_busy` or a `codex_*` reason)."""
    from runtime import codex_rate_limits
    need = {}
    for spec in specs:
        if _manifest_reason(spec) is None and (spec['route'], spec['account']) not in marks \
                and codex_rate_limits.applies(spec):
            need.setdefault(spec['pool'], set()).update(spec['demand'])
    failed = {}
    for pool, windows in need.items():
        outcome = codex_rate_limits.refresh(settings_path, need=tuple(sorted(windows)))
        if outcome.get('state') not in ('fresh', 'stored'):
            failed[pool] = outcome.get('reason') if outcome.get('reason') in codex_rate_limits.REASONS \
                else 'observation_store_invalid'
    return failed


def _model_demand(source, spec):
    """Whether the source verifies a `demand_scope: per_model` path's demand for this route's model (exactly True)."""
    answer = getattr(source, 'model_demand', None)
    try:
        return callable(answer) and answer(copy.deepcopy(spec)) is True
    except Exception:  # noqa: BLE001 - an unreadable mapping is an unverified one, never an error or a pass
        return False


def _requested(source, table, strategy_ids, choices):
    """The routes one request needs from `source`, in its order: ranked strategy ids in ranking order, or the
    routes without a `strategy_id` that match a choice by identity and funding (never by a generated id)."""
    if strategy_ids is not None:
        specs = validate_routes(source.manifest(table, ()))
        by_id = {}
        for spec in specs:
            if type(spec) is dict and spec.get('strategy_id') is not None:
                by_id.setdefault(spec['strategy_id'], []).append(spec)
        return [spec for sid in dict.fromkeys(strategy_ids) for spec in by_id.get(sid, [])]
    specs = validate_routes(source.manifest(None, list(choices)))
    return [spec for spec in specs if type(spec) is dict and 'strategy_id' not in spec and any(
        type(entry) is dict and routing._choice_matches(entry, dict(spec=spec, cls=routing.FUNDING_OF.get(
            spec.get('billing_class')))) for entry in choices)]


def shortlist(project, assignment, *, settings_path, table=None, strategy_ids=None, choices=(), harnesses=None,
              source=None):
    """The shared pre-window cut `collect` and the internal request preview both use: `{routes, excluded, shortlist,
    seen, routes_version}`. In order: the request's routes (`_requested`); the host filter, which drops every route
    whose harness is outside `harnesses` silently (for this host it does not exist; None keeps nothing); the reasons
    that need no window (an invalid route, a `not_entitled` mark, a ranked dollar route, the integration and credential
    checks of an explicit gateway choice, a subscription route without a current subscription-only billing fact);
    then, for a ranking, its one subscription shortlist (`routing_funding.subscription_shortlist`, at most ten ids over
    the whole filtered order; a later refusal never refills it), and the first MAX_ROUTES in request order. It reads
    rules, qualifications, entitlement marks, the source's billing facts and, only for a surviving explicit gateway
    choice, the cached OpenRouter status; never a window, the ledger, a reader or the network, and it writes nothing.
    `shortlist` holds strategy ids."""
    source = source if source is not None else default_source(settings_path)
    now = int(time.time())
    allowed = set(harnesses or ())
    scope = assignment.get('scope') if type(assignment) is dict else None
    try:
        marks = {(mark['route'], mark['account']) for mark in entitlement_marks(settings_path)}
        marks_reason = None
    except (OSError, ValueError, KeyError, TypeError):
        marks, marks_reason = set(), 'entitlement_unavailable'
    routes, excluded, cache = [], [], {}
    seen = dict(openrouter_revision=None)

    def leave(route, reason):
        excluded.append(dict(route=route.get('route') if type(route) is dict and routing._text(route.get('route'))
                             else None, reason=reason))

    for spec in _requested(source, table, strategy_ids, choices):
        if spec.get('harness') not in allowed:
            continue
        reason = _manifest_reason(spec)
        if reason is None:
            reason = marks_reason or ('not_entitled' if (spec['route'], spec['account']) in marks else None)
        if reason is None and strategy_ids is not None and spec['billing_class'] in routing.FUNDED:
            reason = 'billing_class_excluded'  # ranked dollar routes stay out before any funding read (decision B)
        if reason is None and spec['billing_class'] == 'included_subscription' \
                and not routing._text(spec.get('window_unit')):
            reason = 'window_unit_undeclared'  # its demand's unit is unknown: fail closed
        if reason is None and spec['billing_class'] == 'included_subscription' \
                and not subscription_billing.verified(source, spec, now):
            reason = subscription_billing.UNVERIFIED  # unknown billing is neither full nor spare capacity
        if reason is None and spec.get('demand_scope') == routing_paths.MODEL_DEMAND \
                and not _model_demand(source, spec):
            reason = 'demand_unverified'  # the path's measured demand is not known to hold for this model
        if reason is not None:
            leave(spec, reason)
            continue
        kept = copy.deepcopy(spec)
        if spec['billing_class'] == 'gateway':
            if 'openrouter' not in cache:
                cache['openrouter'] = _openrouter(project)
                report = cache['openrouter'][0]
                if report is not None and type(report.get('revision')) is int:
                    seen['openrouter_revision'] = report['revision']
            report, problem = cache['openrouter']
            if problem is not None:
                leave(spec, problem)
                continue
            if not (report.get('enabled') is True and spec['integration_route'] in report.get('routes', [])
                    and report.get('local_configuration') == 'ready'):
                leave(spec, 'integration_disabled')
                continue
            kept['credential'] = _gateway_credential(report)
            if kept['credential'] is None:
                leave(spec, 'credential_unavailable')
                continue
        elif spec['billing_class'] in routing.FUNDED:
            kept['credential'] = source.credential(copy.deepcopy(spec), scope)
        routes.append(kept)
    ids = routing_funding.subscription_shortlist(strategy_ids, routes) if strategy_ids is not None else []
    if strategy_ids is not None:
        routes = [route for route in routes if route['strategy_id'] in ids]
    routes = routes[:routing.MAX_ROUTES]
    return dict(routes=routes, excluded=excluded, shortlist=ids, seen=seen,
                routes_version=source.routes_version(table))


def collect(project, assignment, *, settings_path, ledger_path, source=None, refresh_windows=False, table=None,
            strategy_ids=None, choices=(), harnesses=None):
    """Build `{observations, seen}` for `prepare` from local records (C.3 design 3.3, collector return shape).

    It reads only the stores the calling service uses: `settings_path` locates the path qualifications and the
    private window and entitlement files, and `ledger_path` the ledger whose organizations fund dollar routes. It
    never opens a default path. Routes are the request's own (`strategy_ids` of a ranking over `table`, else
    `choices`) after the host's `harnesses` and the static checks of `shortlist`; nothing generated is stored.
    `observations` is the snapshot `routing.admit` takes, with `shortlist` (the ranked ids its routes come from, before
    any window), plus `excluded`, one fixed reason per route left out, and `routes_version`; `seen` holds only the
    revisions read: `openrouter_revision` (null when unread or not needed).

    `refresh_windows` (the routing service outside `--serve` only) lets the local source's Codex pool be read from
    `codex app-server` first when a kept route needs its reading and it is missing or stale; this is the collector's
    only process or network use, and a failed read leaves the route stale or unobserved."""
    source = source if source is not None else default_source(settings_path)
    cut = shortlist(project, assignment, settings_path=settings_path, table=table, strategy_ids=strategy_ids,
                    choices=choices, harnesses=harnesses, source=source)
    excluded, seen = cut['excluded'], cut['seen']
    data = _ledger_data(ledger_path)
    unread = {}
    if refresh_windows and type(source) is LocalSource and cut['routes']:
        unread = refresh_needed(cut['routes'], settings_path)
    now = int(time.time())  # after any read, so a window just stored is not dated ahead of it
    routes, pools, boundaries = [], {}, {}

    def leave(route, reason):
        excluded.append(dict(route=route['route'], reason=reason))

    for spec in cut['routes']:
        billing = spec['billing_class']
        dollar = billing in routing.FUNDED
        route = {key: copy.deepcopy(spec[key]) for key in ROUTE_FIELDS if key in spec}
        route.update(capable=True, permitted=True)
        if billing == 'included_subscription':
            route['binding_enforceable'] = spec.get('binding_enforceable', True) is True
        if billing == 'gateway':
            route['integration_enabled'] = True
        if spec['pool'] not in pools:
            windows = []
            try:  # one read: the pool's readings, its boundary and the identity the admission rechecks (F4R-09)
                stored = _read(window_path(settings_path)) if settings_path is not None else {}
                boundary = stored.get('consumed', {}).get(spec['pool'], 0)
                boundary = boundary if routing._amount(boundary) else 0
                boundaries[spec['pool']] = pool_state(stored, spec['pool'])
            except (OSError, ValueError):
                stored, boundary = None, now  # an unreadable store leaves every reading of the pool stale (fail closed)
                boundaries[spec['pool']] = None
            own = stored is not None and type(source) is LocalSource and source.settings_path is not None \
                and Path(source.settings_path) == Path(settings_path)
            found = source.windows(spec['pool'], now, stored=stored) if own else source.windows(spec['pool'], now)
            for window in found:
                if not _valid_window(window):
                    continue
                item = {key: window[key] for key in WINDOW_FIELDS if key in window}
                item['fresh'] = 0 <= now - window['observed_at'] <= FRESH_SECONDS and window['observed_at'] > boundary
                organization = (data or {}).get('organizations', {}).get(item.get('organization'))
                if item.get('organization') and 'organization_revision' not in item and organization is not None:
                    item['organization_revision'] = organization['revision']
                windows.append(item)
            pools[spec['pool']] = dict(windows=windows[:routing.MAX_WINDOWS])
            if dollar:
                pools[spec['pool']]['billing_class'] = billing
        pool = pools[spec['pool']]
        demanded = [window for window in pool['windows'] if window['id'] in spec['demand']]
        if spec['pool'] in unread and (len(demanded) < len(spec['demand']) or not all(w['fresh'] for w in demanded)):
            leave(spec, unread[spec['pool']])  # the Codex reader's fixed reason (R7), instead of stale or unobserved
            continue
        if not pool['windows'] or not set(spec['demand']) <= {window['id'] for window in pool['windows']}:
            leave(spec, 'window_unobserved')  # enabled, but no telemetry yet (C.3 design 7.1 decision 6)
            continue
        if billing == 'included_subscription' and any(window['id'] in spec['demand'] and window.get('unit')
                                                       != spec['window_unit'] for window in pool['windows']):
            leave(spec, 'window_unit_mismatch')  # a stored window in another unit, or in none
            continue
        if dollar:
            organizations = {window.get('organization') for window in pool['windows']}
            if len(organizations) != 1 or not _funding_verified(data, organizations.pop(), now):
                leave(spec, 'funding_unverified')
                continue
            route['funding_verified'] = True
            route['credential'] = spec.get('credential')
        routes.append(route)
    used = {route['pool'] for route in routes}
    observations = dict(now=now, routes=routes, pools={key: pool for key, pool in pools.items() if key in used},
                        shortlist=cut['shortlist'], excluded=excluded, routes_version=cut['routes_version'])
    return dict(observations=observations, seen=seen, boundaries={key: boundaries[key] for key in used})


class _Rechecked:
    """The ledger as `routing.admit` sees it for a collector-built snapshot (F4R-09): inside the admission's own
    transaction, a pool whose stored state changed since the snapshot's read (`pool_state`: a completion stored its
    settled readings or moved the consumed boundary, then released its hold) has every window of the snapshot marked
    not fresh, so the decision cannot reuse that headroom. A store that cannot be read counts as changed. Every
    completion writes the store before its ledger release, and the ledger serializes the release with this
    transaction. Everything else is the ledger's."""

    def __init__(self, ledger, settings_path, observations, boundaries):
        self._ledger, self._settings = ledger, settings_path
        self._observations, self._boundaries = observations, boundaries

    def __getattr__(self, name):
        return getattr(self._ledger, name)

    def admit(self, attempt_id, decide):
        def rechecked(data):
            try:
                stored = _read(window_path(self._settings))
            except (OSError, ValueError, TypeError):  # TypeError: no settings path
                stored = None
            for pool, state in self._boundaries.items():
                if stored is None or state is None or pool_state(stored, pool) != state:
                    for window in self._observations.get('pools', {}).get(pool, {}).get('windows', []):
                        window['fresh'] = False
            return decide(data)
        return self._ledger.admit(attempt_id, rechecked)


def rechecked_ledger(ledger, settings_path, observations, boundaries):
    """`ledger` with the pool-state recheck for this collector-built `observations` (`boundaries`: each kept pool's
    `pool_state` from the snapshot's own read); unchanged without boundaries."""
    return _Rechecked(ledger, settings_path, observations, boundaries) if boundaries else ledger
