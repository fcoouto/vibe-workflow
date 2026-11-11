"""Smart Routing path rules, path qualification records and in-memory route generation (F4).

A path is a model-free launch channel: one harness, one provider and the subscription or gateway pool it draws on.
The shipped rules (`routing_routes.json`) name no model, effort, endpoint or demand. The operator's `qualify_path`
records a path's measured demand beside the routing settings. Routes exist only in memory: each `prepare` combines
the qualified paths with the ranking table's strategies or the request's choices, and nothing here writes a route.
Imports routing and the standard library only.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from runtime import routing, usage_budget

RULES = Path(__file__).with_name('routing_routes.json')
QUALIFICATIONS_FILE = 'path-qualifications.json'
RULES_VERSION = 2
QUALIFICATIONS_VERSION = 1
MAX_PATHS = 16
MAX_FILE = 1 << 20
MAX_EVIDENCE = 128
MAX_SUBSCRIPTION_DEMAND = 1000  # thousandths of a window
MAX_GATEWAY_DEMAND = 1_000_000  # micro-dollars per launch
PATH_ID = re.compile(r'[a-z0-9-]{1,32}')
# A strategy id a route may carry (compiled ids are 16 hex characters); never a `:`, which separates route parts.
STRATEGY_ID = re.compile(r'[A-Za-z0-9._-]{1,64}')
WINDOW_UNIT = 'utilization_thousandths'
SUBSCRIPTION = 'included_subscription'
SIGN_INS = ('subscription', 'api_key', 'gateway_key')
INTEGRATION_ROUTES = ('proposals', 'workers')
TEST_ONLY = 'test_only'
REQUIRED = {'path', 'harness', 'provider', 'sign_in', 'billing_class', 'pool', 'account', 'windows'}
OPTIONAL = {'binding_enforceable', 'window_unit', 'integration_route', 'host', 'advisory', 'status', 'demand_scope'}
# `demand_scope: per_model`: the path's measured demand applies to a model only once a per-model demand mapping for
# it is verified (F4.5, OpenCode Go's model-dependent allowance); the collector leaves other models out
# (`demand_unverified`).
MODEL_DEMAND = 'per_model'
RECORD_FIELDS = {'demand', 'account', 'evidence', 'qualified_at'}


def _invalid(reason='route_manifest_invalid'):
    raise ValueError(reason)


def _rule(item):
    """One path rule, or ValueError: no model, effort, endpoint or demand, and the fields its billing class needs."""
    if type(item) is not dict or not REQUIRED <= set(item) <= REQUIRED | OPTIONAL:
        _invalid()
    windows = item['windows']
    subscription = item['billing_class'] == SUBSCRIPTION
    valid = (type(item['path']) is str and PATH_ID.fullmatch(item['path'])
             and all(routing._text(item[key]) for key in ('harness', 'provider', 'account'))
             and routing._part(item['pool']) and item['sign_in'] in SIGN_INS
             and item['billing_class'] in routing.FUNDING_OF
             and type(windows) is list and 0 < len(windows) <= routing.MAX_WINDOWS
             and len(set(windows)) == len(windows) and all(routing._part(window) for window in windows)
             and routing._optional(item.get('host'), routing._text)
             and type(item.get('advisory', False)) is bool and item.get('status', TEST_ONLY) == TEST_ONLY)
    valid = valid and item.get('demand_scope', MODEL_DEMAND) == MODEL_DEMAND \
        and (subscription or 'demand_scope' not in item)
    if subscription:
        valid = valid and item.get('window_unit') == WINDOW_UNIT and item.get('binding_enforceable') is True \
            and 'integration_route' not in item
    else:
        valid = valid and not {'window_unit', 'binding_enforceable'} & set(item) and windows == ['credit']
        gateway = item['billing_class'] == 'gateway'
        valid = valid and (item.get('integration_route') in INTEGRATION_ROUTES if gateway
                           else 'integration_route' not in item)
    if not valid:
        _invalid()
    return item


def load_rules(path=RULES):
    """The path rules `{version: 2, paths: [...]}`, or ValueError('route_manifest_invalid')."""
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        _invalid()
    if type(value) is not dict or value.get('version') != RULES_VERSION or type(value.get('paths')) is not list \
            or len(value['paths']) > MAX_PATHS:
        _invalid()
    ids, pairs = set(), set()
    for item in value['paths']:
        _rule(item)
        pair = (item['harness'], item['provider'])
        if item['path'] in ids or pair in pairs:
            _invalid()
        ids.add(item['path'])
        pairs.add(pair)
    return value


# -- the qualification record ------------------------------------------------------------------------------------
def _qualifications_path(settings_path):
    path = Path(settings_path).absolute()
    return path.parent.resolve() / QUALIFICATIONS_FILE


def _demand_ok(rule, demand):
    if type(demand) is not dict or set(demand) != set(rule['windows']):
        return False
    top = MAX_SUBSCRIPTION_DEMAND if rule['billing_class'] == SUBSCRIPTION else MAX_GATEWAY_DEMAND
    return all(type(amount) is int and 1 <= amount <= top for amount in demand.values())


def _record_ok(record):
    return (type(record) is dict and set(record) == RECORD_FIELDS and type(record['demand']) is dict
            and 0 < len(record['demand']) <= routing.MAX_WINDOWS
            and all(routing._part(key) and type(amount) is int and amount > 0 for key, amount in record['demand'].items())
            and routing._optional(record['account'], routing._text)
            and type(record['evidence']) is str and 0 < len(record['evidence']) <= MAX_EVIDENCE
            and record['evidence'].isprintable() and routing._amount(record['qualified_at']))


def _read(target):
    """The stored `{version, paths}` object, {} when absent; ValueError('path_qualifications_invalid') otherwise."""
    try:
        usage_budget.private(target.parent, True)
        info = usage_budget.private(target)
    except FileNotFoundError:
        return {}
    except (usage_budget.BudgetError, OSError):
        _invalid('path_qualifications_invalid')
    if info.st_size > MAX_FILE:
        _invalid('path_qualifications_invalid')
    try:
        value = json.loads(target.read_bytes())
    except (OSError, ValueError):
        _invalid('path_qualifications_invalid')
    if type(value) is not dict or set(value) != {'version', 'paths'} or value['version'] != QUALIFICATIONS_VERSION \
            or type(value['paths']) is not dict or len(value['paths']) > MAX_PATHS \
            or not all(type(key) is str and PATH_ID.fullmatch(key) and _record_ok(record)
                       for key, record in value['paths'].items()):
        _invalid('path_qualifications_invalid')
    return value


def qualifications(settings_path):
    """`{path_id: {demand, account, evidence, qualified_at}}` beside `settings_path`; {} when absent."""
    return _read(_qualifications_path(settings_path)).get('paths', {})


@contextmanager
def _locked(target):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    usage_budget.private(target.parent, True)
    lock = os.open(target.parent / '.paths.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)


def _write(target, value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
    temporary = target.parent / ('.paths-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def qualify(settings_path, path_id, *, demand, evidence, account=None, now, rules=None, allow_test_only=False):
    """Record `path_id`'s measured demand (the operator's one-shot `qualify_path`). Demand keys equal the path's
    windows: thousandths 1-1000 on a subscription path, micro-dollars 1-1,000,000 per launch on a gateway path.
    `evidence` is the live result's id. A `test_only` path is refused unless `allow_test_only` (test inputs)."""
    rules = load_rules() if rules is None else rules
    rule = next((item for item in rules['paths'] if item['path'] == path_id), None)
    if rule is None:
        _invalid('path_unknown')
    if rule.get('status') == TEST_ONLY and not allow_test_only:
        _invalid('path_test_only')
    if not _demand_ok(rule, demand):
        _invalid('demand_invalid')
    if not (type(evidence) is str and 0 < len(evidence) <= MAX_EVIDENCE and evidence.isprintable()):
        _invalid('evidence_invalid')
    if not routing._optional(account, routing._text):
        _invalid('account_invalid')
    if not routing._amount(now):
        _invalid('invalid_request')
    target = _qualifications_path(settings_path)
    record = dict(demand=dict(demand), account=account, evidence=evidence, qualified_at=now)
    with _locked(target):
        value = _read(target) or dict(version=QUALIFICATIONS_VERSION, paths={})
        value['paths'][path_id] = record
        _write(target, value)
    return dict(state='qualified', path=path_id, **record)


def unqualify(settings_path, path_id):
    """Remove `path_id`'s qualification; a held admission on it is then refused at dispatch."""
    target = _qualifications_path(settings_path)
    with _locked(target):
        value = _read(target) or dict(version=QUALIFICATIONS_VERSION, paths={})
        removed = value['paths'].pop(path_id, None) is not None
        if removed:
            _write(target, value)
    return dict(state='unqualified', path=path_id, removed=removed)


def operation(name, settings_path, body, now, test_inputs=False):
    """The one-shot operator operations `qualify_path` `{path, demand, evidence, account?}` and `unqualify_path`
    `{path}` (the routing command only dispatches here): the result, or `{state: 'blocked', reason}`."""
    try:
        if not (type(body.get('path')) is str and PATH_ID.fullmatch(body['path'])):
            _invalid('invalid_request')
        if name == 'unqualify_path':
            return unqualify(settings_path, body['path'])
        return qualify(settings_path, body['path'], demand=body.get('demand'), evidence=body.get('evidence'),
                       account=body.get('account'), now=now, allow_test_only=test_inputs)
    except ValueError as error:
        return dict(state='blocked', reason=str(error))
    except (OSError, usage_budget.BudgetError):
        return dict(state='blocked', reason='path_qualifications_invalid')


def resolved(rules, qualified):
    """The qualified paths in rule order, each with its rule fields, its `demand` and the account that applies (the
    qualification's, else the rule's). A qualification whose windows no longer match its rule is left out."""
    found = []
    for rule in rules['paths']:
        record = qualified.get(rule['path'])
        if record is None or not _demand_ok(rule, record.get('demand')):
            continue
        found.append(dict(rule, demand=dict(record['demand']), account=record.get('account') or rule['account']))
    return found


def path_of(route_id):
    """The path id of a generated route id: the text before the first `:`."""
    return route_id.split(':', 1)[0] if type(route_id) is str else ''


def pool_windows(rules, pool):
    return {window for item in rules['paths'] if item['pool'] == pool for window in item['windows']}


def dollar_harnesses(rules):
    return {item['harness'] for item in rules['paths'] if item['billing_class'] != SUBSCRIPTION}


# -- routes in memory --------------------------------------------------------------------------------------------
def _route(path, route_id, provider, model, effort):
    route = dict(route=route_id, status='qualified', path=path['path'], provider=provider, model=model,
                 effort='none' if effort is None else effort, harness=path['harness'],
                 billing_class=path['billing_class'], pool=path['pool'], account=path['account'],
                 demand=dict(path['demand']))
    if path['billing_class'] == SUBSCRIPTION:
        route.update(window_unit=path['window_unit'], binding_enforceable=path['binding_enforceable'])
        if 'demand_scope' in path:
            route['demand_scope'] = path['demand_scope']
    elif 'integration_route' in path:
        route['integration_route'] = path['integration_route']
    return route


def strategy_route(path, strategy):
    """The route of one table strategy on one resolved path. A strategy without an effort gets the text `none`."""
    route = _route(path, '%s:%s' % (path['path'], strategy['strategy_id']), path['provider'], strategy['model'],
                   strategy.get('effort'))
    route['strategy_id'] = strategy['strategy_id']
    if strategy.get('endpoint') is not None:
        route['endpoint'] = strategy['endpoint']
    return route


def choice_id(path_id, provider, model, effort, harness, endpoint=None):
    digest = hashlib.sha256(json.dumps([provider, model, effort, harness, endpoint], separators=(',', ':')).encode())
    return '%s:choice:%s' % (path_id, digest.hexdigest()[:16])


def choice_route(path, choice):
    """The route of one explicit choice on one resolved path: no `strategy_id`, the choice's `endpoint` when it has
    one, and an id from its identity and endpoint."""
    endpoint = choice.get('endpoint')
    route = _route(path, choice_id(path['path'], path['provider'], choice['model'], choice['effort'], path['harness'],
                                   endpoint), path['provider'], choice['model'], choice['effort'])
    if endpoint is not None:
        route['endpoint'] = endpoint
    return route


def _strategies(table):
    """Each distinct table strategy once (by `strategy_id`), in row order."""
    rows = table.get('rows') if type(table) is dict else None
    seen = {}
    for strategies in (rows.values() if type(rows) is dict else ()):
        for item in strategies if type(strategies) is list else ():
            if type(item) is dict and type(item.get('strategy_id')) is str and STRATEGY_ID.fullmatch(item['strategy_id']) \
                    and item['strategy_id'] not in seen \
                    and routing._text(item.get('model')) and routing._optional(item.get('effort'), routing._text):
                seen[item['strategy_id']] = item
    return list(seen.values())


def generate(rules, qualified, table, choices):
    """`{routes, skipped}` for the qualified paths: one strategy route per distinct table strategy with the path's
    harness and provider, one choice route per choice with them. Strategies that share a route identity but name
    different endpoints generate nothing and are listed in `skipped` as `endpoint_ambiguous`."""
    routes, skipped = [], []
    strategies = _strategies(table)
    for path in resolved(rules, qualified):
        matching, endpoints = [], {}
        for item in strategies:
            if item.get('harness') == path['harness'] and item.get('provider') == path['provider']:
                matching.append(item)
                endpoints.setdefault((item['model'], item.get('effort')), set()).add(item.get('endpoint'))
        for item in matching:
            if len(endpoints[(item['model'], item.get('effort'))]) > 1:
                skipped.append(dict(path=path['path'], strategy_id=item['strategy_id'], reason='endpoint_ambiguous'))
            else:
                routes.append(strategy_route(path, item))
        known = set()
        for choice in choices:
            if type(choice) is dict and (choice.get('harness'), choice.get('provider')) == (path['harness'],
                                                                                              path['provider']):
                route = choice_route(path, choice)
                if route['route'] not in known:
                    known.add(route['route'])
                    routes.append(route)
    return dict(routes=routes, skipped=skipped)


MAX_CHOICES = 16


def selection(assignment, settings, strategy_ids):
    """(mode, entries) one request needs, by `routing._decide`'s own mode rule: `choice` (the explicit choice),
    `ranked` (Smart Routing on, no choice, a non-empty ranking: its strategy ids in order) or `choices` (the
    specialist's settings `worker_choices`, at most 16). A choice never uses a strategy route, nor the reverse."""
    if assignment.get('choice'):
        return 'choice', [assignment['choice']]
    if settings.get('smart_routing') and strategy_ids:
        return 'ranked', list(strategy_ids)
    return 'choices', list(settings.get('worker_choices', {}).get(assignment.get('role'), []))[:MAX_CHOICES]


# -- the activation gate -----------------------------------------------------------------------------------------
TABLE_FILE = 'strategy-table.json'
MAX_TABLE = 8 << 20


def _table(settings_path):
    """The strategy table beside the settings, or {} when it is absent or unusable (a table never blocks)."""
    target = Path(settings_path).absolute().parent.resolve() / TABLE_FILE
    try:
        usage_budget.private(target.parent, True)
        if usage_budget.private(target).st_size > MAX_TABLE:
            return {}
        value = json.loads(target.read_bytes())
    except (OSError, ValueError, usage_budget.BudgetError):
        return {}
    return value if type(value) is dict else {}


def gate(settings_path):
    """The activation gate's data check: [] or one fixed code. It reads the packaged rules and the qualification and
    table files beside `settings_path`, nothing else; a `test_only` path never counts."""
    try:
        rules = load_rules()
    except ValueError:
        return ['route_manifest_invalid']
    try:
        qualified = qualifications(settings_path)
    except ValueError:
        return ['path_qualifications_invalid']
    production = dict(rules, paths=[item for item in rules['paths'] if item.get('status') != TEST_ONLY])
    if not resolved(production, qualified):
        return ['no_qualified_path']
    return [] if generate(production, qualified, _table(settings_path), ())['routes'] else ['no_generated_route']
