"""The Smart Routing observation collector (C.3 design 3.3, 3.4): the `observations` snapshot `prepare` trusts.

It reads local trusted records only: the route manifest, stored rate-limit observations, the entitlement record,
the ledger it is given and `openrouter_settings.status`. It never reads a secret, makes a network call, calls a
model or takes the OpenRouter admission lock. Window and entitlement files live next to the routing settings path
the caller gives, never under a path derived from HOME.
"""
from contextlib import closing, contextmanager
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from runtime import routing, usage_budget

MANIFEST = Path(__file__).with_name('routing_routes.json')
WINDOWS_FILE = 'window-observations.json'
ENTITLEMENTS_FILE = 'entitlements.json'
FRESH_SECONDS = usage_budget.FRESH_SECONDS  # the same 900 s freshness the ledger applies to balances
MAX_FILE = 1 << 20
MAX_MARKS = 256
CLOCK_MARGIN = routing.MAX_CLOCK_DRIFT  # seconds a stored observation may run ahead of this clock
LEDGER_WAIT = 0.2  # seconds: a busy ledger leaves dollar routes unverified, it never stalls prepare
# The unit of stored subscription window observations: a thousandth of the provider's utilisation (limit 1000). The
# manifest declares it (`window_unit`) and each stored window carries it (`unit`), so a route's `demand` and its
# windows can never be in different units; the OpenCode launcher (C3-3) stores windows in this unit by this name.
WINDOW_UNIT = 'utilization_thousandths'
# The OpenRouter route each gateway harness needs enabled in `openrouter_settings` (`routes`).
INTEGRATION_ROUTES = ('proposals', 'workers')
ROUTE_FIELDS = ('route', 'strategy_id', *routing.ROUTE_IDENTITY, 'account', 'billing_class', 'pool', 'demand',
                'next_request_bound')
WINDOW_FIELDS = ('id', 'limit', 'used', 'length', 'reset_at', 'rolling', 'protected', 'organization',
                 'organization_revision', 'unit')


class StaticSource:
    """A source built in code (tests, the fixture and the self-check sandbox); never selected by input.

    `windows` maps a pool to its window observations or is a callable `(pool, now)`; `credential` is a callable
    `(route, scope)` returning binding readiness metadata `{state, scope, ref}` or None."""

    def __init__(self, routes, windows=None, credential=None):
        self.routes, self._windows, self._credential = copy.deepcopy(list(routes)), windows or {}, credential

    def manifest(self):
        return copy.deepcopy(self.routes)

    def windows(self, pool, now):
        found = self._windows(pool, now) if callable(self._windows) else self._windows.get(pool, [])
        return copy.deepcopy(list(found))

    def credential(self, route, scope):
        return None if self._credential is None else self._credential(route, scope)


class LocalSource:
    """The production source: the shipped manifest, the private window store and no secret.

    It reports no credential. For gateway routes the collector derives binding readiness itself from the cached
    `openrouter_settings.status` report and never asks a source; direct API routes (API Usage) stay unwired, so they
    report no credential (`credential_unavailable`) and fail closed until that wiring lands after the release."""

    def __init__(self, settings_path=None):
        self.settings_path = settings_path

    def manifest(self):
        """The shipped routes, each carrying the manifest's top-level `window_unit` unless it declares its own."""
        manifest = load_manifest()
        unit = manifest.get('window_unit')
        return [dict(route, window_unit=route.get('window_unit', unit)) if type(route) is dict and unit is not None
                else route for route in manifest['routes']]

    def windows(self, pool, now):
        if self.settings_path is None:
            return []
        stored = _read(window_path(self.settings_path)).get('pools', {})
        return [dict(window, id=key) for key, window in sorted(stored.get(pool, {}).items())]

    def credential(self, route, scope):
        return None


def default_source(settings_path=None):
    """The local source. Only code replaces it (a test patches this attribute); nothing a request, setting,
    table, environment variable or command-line flag carries can select another."""
    return LocalSource(settings_path)


def assignment_scope(checkout):
    """The assignment `scope` credential readiness is compared with: a digest of the checkout's real path."""
    return 'project:' + hashlib.sha256(os.fsencode(os.path.realpath(checkout))).hexdigest()[:32]


def validate_routes(routes):
    """`routes`, or ValueError when a manifest is unusable. The route identity has no endpoint field yet, so two
    `qualified` routes with the same identity but different endpoints (an absent endpoint counts as one) could be
    confused at dispatch: the whole manifest is refused instead of picking one."""
    if type(routes) is not list:
        raise ValueError('route_manifest_invalid')
    endpoints = {}
    for route in routes:
        if type(route) is dict and route.get('status') == 'qualified':
            identity = tuple(json.dumps(route.get(key)) for key in routing.ROUTE_IDENTITY)
            if endpoints.setdefault(identity, route.get('endpoint')) != route.get('endpoint'):
                raise ValueError('route_manifest_endpoint_ambiguous')
    return routes


def manifest_routes(source):
    """The source's manifest routes after `validate_routes`; every reader of a manifest goes through here."""
    return validate_routes(source.manifest())


def load_manifest(path=MANIFEST):
    value = json.loads(Path(path).read_text())
    if type(value) is not dict or value.get('version') != 1 or type(value.get('routes')) is not list \
            or ('window_unit' in value and not routing._text(value['window_unit'])):
        raise ValueError('route_manifest_invalid')
    validate_routes(value['routes'])
    return value


def manifest_check(source=None):
    """The activation-gate data check (C.3 design 4.2): the manifest is valid and at least one route is
    `qualified`."""
    try:
        routes = manifest_routes(source or default_source())
    except (OSError, ValueError):
        return ['route_manifest_invalid']
    return [] if any(type(route) is dict and route.get('status') == 'qualified' for route in routes) \
        else ['route_manifest_unqualified']


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
    if type(window) is not dict or not set(window) <= {*WINDOW_FIELDS, 'observed_at'}:
        return False
    if 'unit' in window and not routing._text(window['unit']):
        return False
    numbers = all(window.get(key) is None or routing._finite(window[key])
                  for key in ('limit', 'used', 'length', 'reset_at'))
    return (routing._part(window.get('id')) and numbers and routing._finite(window.get('limit'))
            and window['limit'] > 0 and (window.get('used') is None or window['used'] >= 0)
            and routing._amount(window.get('observed_at')) and type(window.get('rolling', False)) is bool
            and (window.get('organization') is None or routing._text(window['organization'])))


def store_window(settings_path, pool, window):
    """Record one window observation for `pool` (the launcher stores what it read after settlement). An older
    observation never replaces a newer one, and one dated beyond now plus CLOCK_MARGIN is refused, so a future-dated
    record can never shadow later ones."""
    if not routing._part(pool) or not _valid_window(window) or window['observed_at'] > time.time() + CLOCK_MARGIN:
        raise ValueError('invalid_window_observation')
    with _locked(window_path(settings_path)) as path:
        value = _read(path) or dict(version=1, pools={})
        windows = value['pools'].setdefault(pool, {})
        current = windows.get(window['id'])
        if current is None or window['observed_at'] > current['observed_at']:
            windows[window['id']] = {key: item for key, item in window.items() if key != 'id'}
            _write(path, value)


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
        if row is None or len(row[0]) > usage_budget.MAX_RECORD:
            return None
        data = json.loads(row[0])
    except (OSError, ValueError, sqlite3.Error, usage_budget.BudgetError):
        return None
    return data if type(data) is dict and type(data.get('organizations')) is dict else None


def collect(project, assignment, *, settings_path, ledger_path, source=None):
    """Build `{observations, seen}` for `prepare` from local records (C.3 design 3.3, collector return shape).

    It reads only the stores the calling service uses: `settings_path` locates the private window and entitlement
    files and `ledger_path` the ledger whose organizations fund dollar routes. It never opens a default path.
    `observations` is the snapshot `routing.admit` takes plus `excluded`, one fixed reason per route left out;
    `seen` holds only the revisions read: `openrouter_revision` (null when unread or not needed).

    A gateway route's credential is derived here from the same cached `status` report, never from the source: it is
    ready only when the report is enabled, lists the host's route and is locally `ready`, and names an activation.
    A manifest `validate_routes` refuses raises ValueError, so `prepare` fails closed."""
    now = int(time.time())
    source = source if source is not None else default_source(settings_path)
    data = _ledger_data(ledger_path)
    scope = assignment.get('scope') if type(assignment) is dict else None
    try:
        marks = {(mark['route'], mark['account']) for mark in entitlement_marks(settings_path)}
        marks_reason = None
    except (OSError, ValueError, KeyError, TypeError):
        marks, marks_reason = set(), 'entitlement_unavailable'
    routes, pools, excluded, cache = [], {}, [], {}
    seen = dict(openrouter_revision=None)

    def leave(route, reason):
        excluded.append(dict(route=route.get('route') if type(route) is dict and routing._text(route.get('route'))
                             else None, reason=reason))

    for spec in manifest_routes(source):
        reason = _manifest_reason(spec)
        if reason is None:
            reason = marks_reason or ('not_entitled' if (spec['route'], spec['account']) in marks else None)
        if reason is not None:
            leave(spec, reason)
            continue
        billing = spec['billing_class']
        dollar = billing in routing.FUNDED
        route = {key: copy.deepcopy(spec[key]) for key in ROUTE_FIELDS if key in spec}
        route.update(capable=True, permitted=True)
        if billing == 'included_subscription':
            route['binding_enforceable'] = spec.get('binding_enforceable', True) is True
        if billing == 'gateway':
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
            credential = _gateway_credential(report)
            if credential is None:
                leave(spec, 'credential_unavailable')
                continue
            route['integration_enabled'] = True
        if billing == 'included_subscription' and not routing._text(spec.get('window_unit')):
            leave(spec, 'window_unit_undeclared')  # its demand's unit is unknown: fail closed
            continue
        if spec['pool'] not in pools:
            windows = []
            for window in source.windows(spec['pool'], now):
                if not _valid_window(window):
                    continue
                item = {key: window[key] for key in WINDOW_FIELDS if key in window}
                item['fresh'] = 0 <= now - window['observed_at'] <= FRESH_SECONDS
                organization = (data or {}).get('organizations', {}).get(item.get('organization'))
                if item.get('organization') and 'organization_revision' not in item and organization is not None:
                    item['organization_revision'] = organization['revision']
                windows.append(item)
            pools[spec['pool']] = dict(windows=windows[:routing.MAX_WINDOWS])
            if dollar:
                pools[spec['pool']]['billing_class'] = billing
        pool = pools[spec['pool']]
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
            route['credential'] = credential if billing == 'gateway' else source.credential(copy.deepcopy(spec), scope)
        routes.append(route)
    used = {route['pool'] for route in routes}
    observations = dict(now=now, routes=routes[:routing.MAX_ROUTES],
                        pools={key: pool for key, pool in pools.items() if key in used}, excluded=excluded)
    return dict(observations=observations, seen=seen)
