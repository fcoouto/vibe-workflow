#!/usr/bin/env python3
"""The single local Smart Routing command every client calls (spec 10.3, A1). No network, model or secret access.

Usage:
  routing.py <op> [--settings PATH] [--ledger PATH]   one JSON request on stdin, one JSON response on stdout
  routing.py --serve [--settings PATH] [--ledger PATH] one request per stdin line, one response per stdout line

Every request is a JSON object ``{"v": 1, "op": <op>, "id"?: <string|integer>, ...}``; one-shot mode takes the op
from argv (an ``op`` field, if present, must match). Every response is ``{"v": 1, "state": ..., "id"?: ...}``,
either a result or ``{"v": 1, "state": "blocked", "reason": <fixed code>}``. A valid ``id`` is echoed. Blocked is an
outcome: the exit code is 0 for every handled request and 2 only for a usage error. Requests over ``MAX_INPUT``
bytes, malformed JSON (including duplicate keys and NaN), unknown fields and internal failures are blocked; no
traceback, input text or secret is ever written. Consumers must ignore unknown response fields: later slices add
fields (S3.4 adds ``authority`` to ``check`` results) without changing ``v``.

Ops (fields beyond v/op/id):
  prepare  {assignment: {attempt_id, task, role, scope, choice?}, job, observations, table?, constraints?}
           ensure_profile(job) -> rank (skipped when Smart Routing is OFF; an empty ranking falls through to
           worker choices) -> admit through the ledger (created if absent; never an organization or key).
           -> {state: admitted, duplicate, admission, ranking, profile} | {state: pending, reason, reasons}
  check    {attempt_id, request: {provider, model, variant, harness, kind, funding_pool, bound?}, now?}
           Read-only. -> {state: allow, attempt_id, route, pool, settings_revision, remaining}
  record   {event: {event_id, attempt_id, kind}}  kind: dispatched | uncertain | not_dispatched | complete
           -> {state: recorded, event_id, duplicate}
  status   {} -> {state: ok, settings, settings_revision, ledger}   local files only (RF5)
  set      {expected_revision, change} -> {state: updated, revision, settings, invalidated_attempts}
           {organization: {id, enabled: true, policy, expected_revision} | {id, enabled: false}}
           -> {state: updated, organization}
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import routing, routing_settings, usage_budget  # noqa: E402
from runtime.routing_settings import SettingsError  # noqa: E402
from runtime.usage_budget import BudgetError  # noqa: E402

VERSION = 1
MAX_INPUT = 1 << 20
MAX_ID = 128
USAGE = 2
FIELDS = dict(prepare=({'assignment', 'job', 'observations'}, {'table', 'constraints'}),
              check=({'attempt_id', 'request'}, {'now'}), record=({'event'}, set()), status=(set(), set()),
              set=(set(), {'expected_revision', 'change', 'organization'}))
ASSIGNMENT_FIELDS = {'attempt_id', 'task', 'role', 'scope', 'choice'}
# What a client may see of an admission record: no account, credential reference, pressure or snapshot detail.
ADMISSION_FIELDS = ('attempt_id', 'task', 'route', 'pool', 'class', 'billing_class', 'mode', 'settings_revision', 'at',
                    'reserved', 'reason_codes', 'route_identity')
PROFILE_FIELDS = ('semantic_digest', 'task_family', 'complexity_band', 'risk_band', 'review_policy', 'uncertainty')
# admit() reports malformed input as pending; to a client it is a refusal, not a wait.
SHAPE_REASONS = ('invalid_assignment', 'invalid_ranking', 'invalid_settings', 'invalid_observation',
                 'admission_error')


class Blocked(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'invalid_json')
        result[key] = value
    return result


def _constant(_):
    raise Blocked('invalid_json')


def parse(raw):
    require(len(raw) <= MAX_INPUT, 'request_too_large')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise Blocked('invalid_json') from None


def _text(value):
    return type(value) is str and 0 < len(value) <= 256 and value.isprintable()


def _amount(value):
    return type(value) is int and 0 <= value <= usage_budget.MAX_INTEGER


def _valid_id(value):
    return (type(value) is str and 0 < len(value) <= MAX_ID and value.isprintable()) or _amount(value)


def _facts(value):
    require(type(value) is dict and set(routing.REQUEST_FIELDS) <= set(value) <= {*routing.REQUEST_FIELDS, 'bound'},
            'invalid_request')
    require(all(_text(value[key]) for key in ('provider', 'model', 'harness', 'funding_pool'))
            and (value['variant'] is None or _text(value['variant'])) and value['kind'] in routing.REQUEST_KINDS,
            'invalid_request')
    bound = value.get('bound')
    require(bound is None or (type(bound) is dict and len(bound) <= usage_budget.MAX_CLAIMS and all(
        type(key) is str and 0 < len(key) <= usage_budget.MAX_KEY and key.isprintable() and _amount(amount)
        for key, amount in bound.items())), 'invalid_request')
    return value


def _public(record):
    return {key: record[key] for key in ADMISSION_FIELDS if key in record}


class Service:
    """Handles requests against one settings file and one ledger; the ledger connection is reused while serving."""
    def __init__(self, settings_path, ledger_path):
        # Private-path checks compare against the resolved path, so resolve symlinked parents (e.g. /var -> /private/var).
        self.settings_path, self.ledger_path = (Path(path).absolute().parent.resolve() / Path(path).name
                                                for path in (settings_path, ledger_path))
        self._open = None

    def close(self):
        if self._open is not None:
            self._open.close()
            self._open = None

    def handle(self, raw, op=None):
        request_id = None
        try:
            value = parse(raw)
            require(type(value) is dict, 'invalid_request')
            if 'id' in value:
                require(_valid_id(value['id']), 'invalid_id')
                request_id = value['id']
            require(type(value.get('v')) is int and value['v'] == VERSION, 'unsupported_version')
            require(op is None or value.get('op', op) == op, 'invalid_request')
            name = value.get('op', op)
            require(type(name) is str and name in FIELDS, 'unknown_op')
            body = {key: item for key, item in value.items() if key not in ('v', 'op', 'id')}
            required, optional = FIELDS[name]
            require(required <= set(body) <= required | optional, 'invalid_request')
            result = getattr(self, '_' + name)(body)
        except Blocked as error:
            result = dict(state='blocked', reason=error.reason)
        except BudgetError as error:
            self.close()
            result = dict(state='blocked', reason=str(error))
        except sqlite3.Error:
            self.close()
            result = dict(state='blocked', reason='ledger_unavailable')
        except Exception:  # noqa: BLE001 - fail closed with a fixed reason; never echo input or a traceback
            self.close()
            result = dict(state='blocked', reason='internal_error')
        response = dict(v=VERSION, **result)
        if request_id is not None:
            response['id'] = request_id
        return response

    # -- stores ------------------------------------------------------------------------------------------------
    def _settings(self):
        try:
            current, values = routing_settings._load(self.settings_path)
            return routing_settings.resolve(values), current
        except SettingsError:
            raise Blocked('settings_unavailable') from None

    def _ledger(self, create):
        if self._open is not None:
            return self._open
        path = self.ledger_path
        if not os.path.lexists(path):
            if not create:
                return None
            try:
                usage_budget.initialize(path)
            except BudgetError as error:
                if str(error) != 'ledger_exists':
                    raise
        self._open = usage_budget.Ledger(path, self._identity(path))
        return self._open

    @staticmethod
    def _identity(path):
        """Read the ledger identity; a concurrent `initialize` may not have written its table yet."""
        for _ in range(200):
            usage_budget.private(path)
            try:
                with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=5)) as db:
                    row = db.execute('SELECT body FROM ledger WHERE id=1').fetchone()
            except sqlite3.OperationalError:
                row = None
            if row is not None:
                require(len(row[0]) <= usage_budget.MAX_RECORD, 'invalid_ledger')
                return usage_budget.name(json.loads(row[0])['identity'])
            time.sleep(0.01)
        raise Blocked('ledger_unavailable')

    # -- ops ---------------------------------------------------------------------------------------------------
    def _prepare(self, body):
        assignment, job = body['assignment'], body['job']
        require(type(assignment) is dict and set(assignment) <= ASSIGNMENT_FIELDS
                and assignment.get('role') in routing_settings.ROLES, 'invalid_request')
        settings, revision = self._settings()
        try:
            profile = routing.ensure_profile(job)
        except Exception:  # noqa: BLE001 - ProfileUnavailable or an unreadable job
            raise Blocked('profile_unavailable') from None
        table, constraints = body.get('table', {}), body.get('constraints', {})
        require(type(table) is dict and type(constraints) is dict, 'invalid_request')
        if settings['smart_routing']:
            try:
                ranking = routing.rank(profile, table, constraints)
            except ValueError:
                raise Blocked('invalid_constraints') from None
            except Exception:  # noqa: BLE001 - a malformed caller-supplied table
                raise Blocked('invalid_table') from None
        else:
            ranking = dict(strategy_ids=[], reason_codes=['smart_routing_off'], table_version=None,
                           profile_digest=profile['semantic_digest'])
        result = routing.admit(dict(assignment, complexity_band=profile['complexity_band']), ranking,
                               dict(settings, revision=revision), body['observations'], ledger=self._ledger(True))
        if result['status'] == 'pending':
            require(result['reason_code'] not in SHAPE_REASONS, result['reason_code'])
            return dict(state='pending', reason=result['reason_code'], reasons=result['reason_codes'])
        return dict(state='admitted', duplicate=result.get('duplicate', False), admission=_public(result),
                    ranking=dict(strategy_ids=ranking['strategy_ids'], reason_codes=ranking['reason_codes'],
                                 table_version=ranking.get('table_version')),
                    profile={key: profile[key] for key in PROFILE_FIELDS})

    def _check(self, body):
        attempt, facts = body['attempt_id'], _facts(body['request'])
        require(_text(attempt), 'invalid_request')
        now = body.get('now', int(time.time()))
        require(_amount(now), 'invalid_request')
        _, revision = self._settings()
        ledger = self._ledger(False)
        require(ledger is not None, 'ledger_absent')
        return routing.check_request(ledger.snapshot(), attempt, facts, revision, now)

    def _record(self, body):
        event = body['event']
        require(type(event) is dict and set(event) == {'event_id', 'attempt_id', 'kind'}
                and all(_text(event[key]) for key in event), 'invalid_request')
        ledger = self._ledger(False)
        require(ledger is not None, 'ledger_absent')
        result = routing.record_event(ledger, event['attempt_id'], event['kind'])
        return dict(result, event_id=event['event_id']) if result['state'] == 'recorded' else result

    def _status(self, body):
        try:
            settings, revision = self._settings()
        except Blocked:
            settings, revision = None, None
        if self._open is None and not os.path.lexists(self.ledger_path):
            ledger = dict(state='absent')
        else:
            try:
                data = self._ledger(False).snapshot()
                stale = len(routing.unsent_admissions(data, revision)) if revision is not None else None
                ledger = dict(state='ready', stale_admissions=stale, **routing.ledger_summary(data))
            except (Blocked, BudgetError, sqlite3.Error) as error:
                self.close()
                reason = error.reason if isinstance(error, Blocked) else \
                    str(error) if isinstance(error, BudgetError) else 'ledger_unavailable'
                ledger = dict(state='unavailable', reason=reason)
        return dict(state='ok', settings=settings, settings_state='ok' if settings is not None else 'unavailable',
                    settings_revision=revision, ledger=ledger)

    def _set(self, body):
        if 'organization' in body:
            require(set(body) == {'organization'}, 'invalid_request')
            return self._organization(body['organization'])
        require(set(body) == {'expected_revision', 'change'}, 'invalid_request')
        try:
            updated = routing_settings.update(self.settings_path, body['change'], body['expected_revision'])
        except SettingsError as error:
            raise Blocked(str(error)) from None
        revision = updated.pop('revision')
        ledger = self._ledger(False)
        stale = routing.unsent_admissions(ledger.snapshot(), revision) if ledger is not None else []
        return dict(state='updated', revision=revision, settings=updated, invalidated_attempts=stale)

    def _organization(self, value):
        """Explicit user action through the ledger's own enable/disable paths; routing never calls this itself."""
        require(type(value) is dict and _text(value.get('id')) and type(value.get('enabled')) is bool,
                'invalid_request')
        if value['enabled']:
            require(set(value) == {'id', 'enabled', 'policy', 'expected_revision'}, 'invalid_request')
            try:
                revision = self._ledger(True).enable_organization(value['id'], value['policy'],
                                                                  value['expected_revision'])
            except (TypeError, KeyError, AttributeError):
                raise Blocked('invalid_request') from None
            return dict(state='updated', organization=dict(id=value['id'], enabled=True, revision=revision))
        require(set(value) == {'id', 'enabled'}, 'invalid_request')
        ledger = self._ledger(False)
        require(ledger is not None, 'organization_unconfigured')
        ledger.disable_organization(value['id'])
        return dict(state='updated', organization=dict(id=value['id'], enabled=False))


def _write(stdout, response):
    try:
        line = json.dumps(response, allow_nan=False, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        line = json.dumps(dict(v=VERSION, state='blocked', reason='internal_error'))
    stdout.write(line.encode() + b'\n')
    stdout.flush()


def serve(service, stdin, stdout):
    """One request per line until EOF; a bad line is answered and never ends the loop."""
    while True:
        line = stdin.readline(MAX_INPUT + 1)
        if not line:
            return 0
        if len(line) > MAX_INPUT and not line.endswith(b'\n'):
            while line and not line.endswith(b'\n'):
                line = stdin.readline(MAX_INPUT + 1)
            _write(stdout, dict(v=VERSION, state='blocked', reason='request_too_large'))
            continue
        try:
            _write(stdout, service.handle(line.rstrip(b'\r\n')))
        except BrokenPipeError:
            return 0


def default_settings():
    return Path.home() / '.local/state/vibe-workflow/routing/settings.json'


def default_ledger():
    from runtime import usage_settings  # the one account-wide ledger Usage Routing already uses
    return usage_settings.directory() / 'organizations.sqlite3'


def _arguments(argv):
    op, serving, paths = None, False, {}
    items = list(argv)
    while items:
        item = items.pop(0)
        if item == '--serve' and not serving:
            serving = True
        elif item in ('--settings', '--ledger') and item not in paths and items and not items[0].startswith('-'):
            paths[item] = items.pop(0)
        elif not item.startswith('-') and op is None:
            op = item
        else:
            return None
    if serving == (op is not None):
        return None
    return op, serving, paths


def main(argv=None, stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    parsed = _arguments(sys.argv[1:] if argv is None else argv)
    if parsed is None:
        _write(stdout, dict(v=VERSION, state='blocked', reason='usage_error'))
        return USAGE
    op, serving, paths = parsed
    service = Service(paths.get('--settings') or default_settings(), paths.get('--ledger') or default_ledger())
    try:
        if serving:
            return serve(service, stdin, stdout)
        _write(stdout, service.handle(stdin.read(MAX_INPUT + 1), op=op))
        return 0
    finally:
        service.close()


if __name__ == '__main__':
    raise SystemExit(main())
