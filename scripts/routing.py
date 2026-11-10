#!/usr/bin/env python3
"""The single local Smart Routing command every client calls (spec 10.3, A1). No network, model or secret access.

Usage:
  routing.py <op> [--settings PATH] [--ledger PATH] [--table PATH] [--project PATH] [--test-inputs]
  routing.py --serve [--settings PATH] [--ledger PATH] [--table PATH] [--project PATH] [--test-inputs]

`<op>` reads one request on stdin; `--serve` reads one request per line. `--project` pins the checkout a
`prepare` workflow locator may name (S3.5); a workflow-bound `prepare` without it is `project_required`. For a
managed project the service builds `prepare`'s observations itself, and `set managed_projects` is one-shot only,
behind the activation gate (C.3 design 3.3, 4.2: docs/superpowers/specs/2026-11-10-smart-routing-c3-wiring-design.md).

The request and response contract, reason codes, clock, busy-wait and test-switch rules are defined once in
the implementation plan, Task S3.1 (docs/superpowers/plans/2026-11-08-smart-routing.md#task-s31-single-routing-command).
"""
from contextlib import closing
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import (ledger_versions, routing, routing_gates, routing_settings, subscription_reconcile,  # noqa: E402
                     usage_budget)
from runtime.routing_settings import SettingsError  # noqa: E402
from runtime.usage_budget import BudgetError  # noqa: E402

VERSION = 1
MAX_INPUT = 1 << 20
MAX_TABLE = 8 << 20
MAX_ID = 128
USAGE = 2
# Test-only: the `--test-inputs` flag lets a caller supply `now` to `check`, an observations `now` off the
# command's clock to `prepare`, and a `table` to `prepare`. Only the command line enables it, and only with both
# stores pinned explicitly away from the production defaults.
TEST_FLAG = '--test-inputs'
# SQLite busy waits in seconds. Reads (`check`, `status`) answer `ledger_busy` quickly; writes wait a little
# longer, but under `--serve` (the hook path) still well inside the shim's 500 ms timeout.
READ_WAIT = 0.2
WRITE_WAIT_SERVING = 0.3
WRITE_WAIT = 5
# Attempts to write a refused activation back out when concurrent settings writes keep moving the revision.
MAX_ROLLBACK = 3
# `observations` is refused for a managed project, where the collector builds them (C.3 design 3.3).
FIELDS = dict(prepare=({'assignment', 'job'}, {'observations', 'table', 'constraints', 'workflow'}),
              check=({'request'}, {'attempt_id', 'session_id', 'authority', 'now'}),
              dispatch=({'attempt_id', 'request'}, {'now'}), record=({'event'}, set()), status=(set(), set()),
              set=(set(), {'expected_revision', 'change', 'organization', 'authority_required'}),
              settle=({'root_task_id'}, {'workflow'}),
              resolve_cost_unknown=({'attempt_id', 'cost_usd_micros', 'event_id'}, {'receipts', 'source'}),
              resolve_overrun=({'attempt_id', 'decision', 'event_id'}, set()),
              reconcile_uncertain=({'attempt_id', 'evidence', 'event_id'}, set()),
              reconcile_subscription=({'attempt_id', 'evidence', 'event_id'}, set()),
              resolve_block=({'organization', 'reason', 'decision', 'event_id'}, {'ids', 'marker'}),
              drop_debit=({'key', 'marker', 'event_id'}, set()),
              settle_legacy_call=({'run_id', 'phase', 'evidence', 'event_id'}, set()))
# Operator ops are one-shot commands only (`operator_op_not_served`): they need the `one_shot` flag that only `main()`
# sets. Neither the OpenCode shim's persistent `--serve` child, which a plugin or worker can reach, nor any in-process
# service (a launcher's `managed_service` included) can settle, release or unblock anything (C.3 design 3.5, 3.7).
OPERATOR_OPS = ('resolve_cost_unknown', 'resolve_overrun', 'reconcile_uncertain', 'resolve_block',
                'reconcile_subscription', 'drop_debit', 'settle_legacy_call')
# Retention cleanup (S3.6a) runs on a write command's first ledger use, then at most once per interval while serving.
EXPIRE_INTERVAL = 3600
ASSIGNMENT_FIELDS = {'attempt_id', 'task', 'role', 'scope', 'choice'}
# What a client may see of an admission record: no account, credential reference, pressure or snapshot detail.
ADMISSION_FIELDS = ('attempt_id', 'task', 'bucket', 'route', 'pool', 'class', 'billing_class', 'mode',
                    'settings_revision', 'at', 'reserved', 'reason_codes', 'route_identity')
PROFILE_FIELDS = ('semantic_digest', 'task_family', 'complexity_band', 'risk_band', 'review_policy', 'uncertainty')
# admit() reports malformed input as pending; to a client it is a refusal, not a wait.
SHAPE_REASONS = ('invalid_assignment', 'invalid_ranking', 'invalid_settings', 'invalid_observation',
                 'invalid_binding', 'admission_error')
# The observation collector (C3-1; C.3 design 3.3, store paths by coordinator decision):
# `collect(project, assignment, *, settings_path, ledger_path, source=None) -> {observations, seen}`.
COLLECTOR = 'runtime.routing_observations'
# Activation gate (C.3 design 4.2): a project joins `managed_projects` only when every capability's behavioural
# self-check passes. (name, kind, target): `module` calls `<module>:<function>(workdir)`; `script` loads
# PACKAGE/<target> by path and calls its `managed_self_check(workdir)`; `manifest` needs one qualified route in
# PACKAGE/<target>; `heading` needs a `Managed projects` heading in PACKAGE/<target>.
CAPABILITIES = (
    ('collector_and_launcher', 'module', 'runtime.routing_launch:managed_self_check'),  # C3-1
    ('route_manifest', 'manifest', 'runtime/routing_routes.json'),  # C3-1, content per user decision 5
    ('native_delegation', 'module', 'runtime.delegation_routing:managed_self_check'),  # C3-2
    ('opencode_launcher', 'module', 'runtime.opencode_launcher:managed_self_check'),  # C3-3
    ('openrouter_proposals', 'script', 'scripts/openrouter_proposals.py'),  # C3-4a
    ('openrouter_workers', 'script', 'scripts/openrouter_workers.py'),  # C3-4b
    ('api_usage', 'module', 'runtime.usage_routing:managed_self_check'),  # C3-5
    ('instructions', 'heading', 'policies/03-agent-delegation.md'),  # C3-6
)
# Enrolled clients whose coordinator has a managed delegation path (policy 03 `Managed projects`): codex through
# native delegation preparation (C3-2), opencode through the managed launcher (C3-3). Claude Code native subagents
# have none, so a project enrolled only for clients outside this list is refused (`advisory_host_unmanaged`); a mix
# may join, and policy 03's hold covers a Claude Code coordinator there. The user's decision 2 (whether advisory
# hosts get automatic selection) may change this list.
MANAGED_HOSTS = ('codex', 'opencode')
SELF_CHECK_TIMEOUT = 10
SELF_CHECK_REAP = 5  # bound on collecting a killed check's output; a descendant outside its group cannot hold the gate
SELF_CHECK_MARK = 'managed-self-check:'
SELF_CHECK_CODE = re.compile(r'[a-z0-9_]{1,64}')
# Runs in the child: the package root (argv[1]) first on the import path, never the project checkout.
SELF_CHECK_CHILD = r'''
import sys
sys.path.insert(0, sys.argv[1])
import importlib, importlib.util, json, os, re
package, kind, target, workdir = sys.argv[1:5]
def run():
    if kind == 'module':
        name, _, function = target.partition(':')
        try:
            check = getattr(importlib.import_module(name), function)
        except (ImportError, AttributeError):
            return ['self_check_missing']
        return check(workdir)
    path = os.path.join(package, target)
    if not os.path.isfile(path):
        return ['self_check_missing']
    if kind == 'script':
        spec = importlib.util.spec_from_file_location('managed_self_check_script', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        check = getattr(module, 'managed_self_check', None)
        return ['self_check_missing'] if check is None else check(workdir)
    with open(path, encoding='utf-8') as handle:
        text = handle.read()
    if kind == 'heading':
        return [] if re.search(r'^#+ Managed projects[ \t]*$', text, re.M) else ['heading_missing']
    data = json.loads(text)
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('routes'), list):
        return ['manifest_invalid']  # C3-1's manifest is {version: 1, ..., routes: [...]}
    qualified = any(isinstance(r, dict) and r.get('status') == 'qualified' for r in data['routes'])
    return [] if qualified else ['no_qualified_route']
try:
    result = run()
except Exception:
    result = ['self_check_error']
sys.stdout.write('\n' + %r + json.dumps(result, default=str) + '\n')
''' % SELF_CHECK_MARK


class Blocked(Exception):
    def __init__(self, reason, **detail):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


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
    require(all(_text(value[key]) for key in ('provider', 'model', 'harness', 'funding_pool', 'kind'))
            and (value['variant'] is None or _text(value['variant'])), 'invalid_request')
    require(value['kind'] in routing.REQUEST_KINDS, 'unsupported_kind')
    bound = value.get('bound')
    require(bound is None or (type(bound) is dict and len(bound) <= usage_budget.MAX_CLAIMS and all(
        type(key) is str and 0 < len(key) <= usage_budget.MAX_KEY and key.isprintable() and _amount(amount)
        for key, amount in bound.items())), 'invalid_request')
    return value


def _table_shape(value):
    """A stored table must be a bounded version plus rows of strategy lists, each with a usable strategy id."""
    version, rows = value.get('version'), value.get('rows')
    return (type(rows) is dict and (version is None or _text(version) or _amount(version))
            and all(type(strategies) is list and all(type(item) is dict and _text(item.get('strategy_id'))
                                                     for item in strategies) for strategies in rows.values()))


def _public(record):
    return {key: record[key] for key in ADMISSION_FIELDS if key in record}


def _busy(error):
    return isinstance(error, sqlite3.OperationalError) and ('locked' in str(error) or 'busy' in str(error))


def _resolved(path):
    # Private-path checks compare against the resolved path, so resolve symlinked parents (e.g. /var -> /private/var).
    return Path(path).absolute().parent.resolve() / Path(path).name


class Service:
    """Handles requests against one settings file, one strategy table and one ledger (reused while serving).

    `workflow` is the trusted reader of canonical workflow state for S3.5's gates (default: the delegation state
    reader). Only code constructs it; no request, configuration or test switch can replace it."""
    def __init__(self, settings_path, ledger_path, table_path=None, *, test_inputs=False, serving=False,
                 workflow=None, project=None, one_shot=False):
        self.project = None if project is None else Path(project).resolve()
        self.workflow = workflow if workflow is not None else routing_gates.D6Workflow(project=self.project)
        self.settings_path, self.ledger_path = _resolved(settings_path), _resolved(ledger_path)
        self.table_path = _resolved(table_path or self.settings_path.parent / 'strategy-table.json')
        self.test_inputs, self.write_wait = test_inputs, WRITE_WAIT_SERVING if serving else WRITE_WAIT
        self.serving = serving
        # Only `main()` sets this, for a one-shot command; no request field or environment can. In-process services
        # (`managed_service` and every other caller) and `--serve` never change `managed_projects` (design 4.2) and
        # never run an operator op (OPERATOR_OPS).
        self.one_shot = one_shot and not serving
        self._open = None
        self._expired_at = None

    def close(self):
        if callable(getattr(self.workflow, 'close', None)):
            self.workflow.close()
        if self._open is not None:
            self._open.close()
            self._open = None

    @staticmethod
    def clock():
        return int(time.time())

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
            require(self.one_shot or name not in OPERATOR_OPS, 'operator_op_not_served')
            body = {key: item for key, item in value.items() if key not in ('v', 'op', 'id')}
            required, optional = FIELDS[name]
            require(required <= set(body) <= required | optional, 'invalid_request')
            result = getattr(self, '_' + name)(body)
        except Blocked as error:
            result = dict(error.detail, state='blocked', reason=error.reason)
        except BudgetError as error:
            self.close()
            result = dict(state='blocked', reason=str(error))
        except sqlite3.Error as error:
            self.close()
            result = dict(state='blocked', reason='ledger_busy' if _busy(error) else 'ledger_unavailable')
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
            return dict(routing_settings.resolve(values), revision=current)
        except SettingsError:
            raise Blocked('settings_unavailable') from None

    def _table(self):
        """The compiled strategy table from local state, or ({}, reason): a missing or bad table never blocks."""
        try:
            usage_budget.private(self.table_path.parent, True)
            info = usage_budget.private(self.table_path)
        except FileNotFoundError:
            return {}, None
        except BudgetError:
            return {}, 'table_unavailable'
        if info.st_size > MAX_TABLE:
            return {}, 'table_unavailable'
        try:
            value = json.loads(self.table_path.read_bytes(), parse_constant=_constant)
        except (ValueError, RecursionError, Blocked, OSError):
            return {}, 'table_unavailable'
        if type(value) is not dict or not _table_shape(value):
            return {}, 'table_unavailable'
        return value, None

    def _ledger(self, create, write=False):
        wait = self.write_wait if write else READ_WAIT
        if self._open is None:
            path = self.ledger_path
            if not os.path.lexists(path):
                if not create:
                    return None
                try:
                    usage_budget.initialize(path)
                except BudgetError as error:
                    if str(error) != 'ledger_exists':
                        raise
            self._open = usage_budget.Ledger(path, self._identity(path, wait), timeout=wait)
        self._open.busy_timeout(wait)
        return self._open

    def _expire(self, ledger):
        """Opportunistic retention cleanup (spec 11) before a write. Reads filter expired evidence themselves, so a
        busy or failed cleanup never blocks the request and is retried later."""
        moment = self.clock()
        if self._expired_at is not None and 0 <= moment - self._expired_at < EXPIRE_INTERVAL:
            return
        self._expired_at = moment
        try:
            ledger.expire_evidence(moment)
        except (BudgetError, sqlite3.Error):
            pass

    @staticmethod
    def _identity(path, wait):
        """Read the ledger identity; a concurrent `initialize` may not have written its table yet. Busy is raised."""
        for _ in range(100):
            usage_budget.private(path)
            try:
                with closing(sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True, timeout=wait)) as db:
                    row = db.execute('SELECT body FROM ledger WHERE id=1').fetchone()
            except sqlite3.OperationalError as error:
                if _busy(error):
                    raise
                row = None
            if row is not None:
                require(len(row[0]) <= usage_budget.MAX_RECORD, 'invalid_ledger')
                return usage_budget.name(json.loads(row[0])['identity'])
            time.sleep(0.01)
        raise Blocked('ledger_unavailable')

    # -- ops ---------------------------------------------------------------------------------------------------
    def _observations(self, value):
        """The command's clock decides: a snapshot off it by more than MAX_CLOCK_DRIFT is stale, and the admission
        is stamped with the command's time. Malformed snapshots pass through to admit's own shape check."""
        if self.test_inputs or type(value) is not dict or type(value.get('now')) is not int:
            return value
        now = self.clock()
        require(abs(value['now'] - now) <= routing.MAX_CLOCK_DRIFT, 'observation_stale')
        return dict(value, now=now)

    def _rank(self, profile, body):
        constraints = body.get('constraints', {})
        require(type(constraints) is dict, 'invalid_request')
        try:
            fallback = routing.rank(profile, {}, constraints)
        except ValueError:
            raise Blocked('invalid_constraints') from None
        if 'table' in body:
            require(self.test_inputs, 'invalid_request')  # a caller-supplied table could steer ranking
            table, problem = body['table'], None
        else:
            table, problem = self._table()
        try:
            ranking = routing.rank(profile, table, constraints) if problem is None else fallback
        except Exception:  # noqa: BLE001 - an unusable table falls back, it never blocks (RF8)
            ranking, problem = fallback, 'table_unavailable'
        if not routing._valid_ranking(ranking):  # the same shape admit() requires; a bad one falls back
            ranking, problem = fallback, 'table_unavailable'
        if problem:
            ranking = dict(ranking, reason_codes=ranking['reason_codes'] + [problem])
        return ranking

    def _managed(self):
        """Whether this pinned service serves a managed project. An unreadable answer counts as managed (fail
        closed): with an unreadable settings file, even an unmanaged project's `prepare` that carries observations
        gets `observations_not_accepted`."""
        if self.project is None:
            return False
        try:
            return routing_settings.managed_project(self.project, settings_path=self.settings_path) is not False
        except Exception:  # noqa: BLE001 - fail closed: caller observations are refused
            return True

    def _collect(self, assignment):
        """The collector's snapshot and `seen` revisions for a managed project; `seen` passes through as an opaque
        mapping. The collector reads this service's own stores, never the defaults. A missing or failing collector
        admits nothing (`observations_unavailable`)."""
        try:
            result = importlib.import_module(COLLECTOR).collect(str(self.project), assignment,
                                                                settings_path=self.settings_path,
                                                                ledger_path=self.ledger_path)
        except Exception:  # noqa: BLE001 - missing module (before C3-1) or a collector failure
            raise Blocked('observations_unavailable') from None
        require(type(result) is dict and {'observations', 'seen'} <= set(result) and type(result['seen']) is dict,
                'observations_unavailable')
        return result['observations'], result['seen']

    def _prepare(self, body):
        assignment = body['assignment']
        require(type(assignment) is dict and set(assignment) <= ASSIGNMENT_FIELDS, 'invalid_request')
        managed = self._managed()
        require(not managed or self.test_inputs or 'observations' not in body, 'observations_not_accepted')
        require(managed or 'observations' in body, 'invalid_request')
        settings = self._settings()
        try:
            profile = routing.ensure_profile(body['job'])
        except Exception:  # noqa: BLE001 - ProfileUnavailable or an unreadable job
            raise Blocked('profile_unavailable') from None
        try:
            role = routing_settings.assignment_role(profile['role'], assignment.get('role'))
        except SettingsError as error:
            raise Blocked(str(error)) from None
        if settings['smart_routing']:
            ranking = self._rank(profile, body)
        else:
            require('table' not in body or self.test_inputs, 'invalid_request')
            ranking = dict(strategy_ids=[], reason_codes=['smart_routing_off'], table_version=None,
                           profile_digest=profile['semantic_digest'])
        admitted = dict(assignment, role=role, complexity_band=profile['complexity_band'])
        seen = None
        if 'observations' in body:
            observations = self._observations(body['observations'])
        else:
            collected, seen = self._collect(admitted)
            observations = self._observations(collected)
        echo = {} if seen is None else dict(seen=seen)  # C3-1's revisions, unchanged, in every answer that follows
        bound = self._workflow_binding(body, profile)
        if bound is not None and bound[0] != 'bound':
            return dict(state=bound[0], reason=bound[1], reasons=[bound[1]], **echo) if bound[0] == 'pending' \
                else dict(state='blocked', reason=bound[1], **echo)
        ledger = self._ledger(True, write=True)
        self._expire(ledger)
        # S3.6c debits clear only in the one-shot command or from a managed project's collector-built snapshot outside
        # `--serve` (C3-0a): never in the shim's persistent child, never from an in-process caller's own snapshot.
        clear = self.one_shot or (seen is not None and not self.serving)
        result = routing.admit(admitted, ranking, settings, observations, ledger=ledger,
                               workflow=None if bound is None else bound[1], clear_debits=clear)
        if result['status'] == 'pending':
            require(result['reason_code'] not in SHAPE_REASONS, result['reason_code'])
            return dict(state='pending', reason=result['reason_code'], reasons=result['reason_codes'], **echo)
        stored = result.get('binding') if isinstance(result.get('binding'), dict) else {}
        return dict(state='admitted', duplicate=result.get('duplicate', False), admission=_public(result),
                    workflow_bound=stored.get('job') is not None,
                    ranking=dict(strategy_ids=ranking['strategy_ids'], reason_codes=ranking['reason_codes'],
                                 table_version=ranking.get('table_version')),
                    profile={key: profile[key] for key in PROFILE_FIELDS}, **echo)

    def _workflow_binding(self, body, profile):
        """S3.5: read the canonical job the request names (before any ledger write) and bind to it.

        No `workflow` locator means ordinary explicit delegation with no workflow job: None (`job: null`).
        Otherwise ('bound', job binding), or (state, reason) when the job cannot be bound now."""
        if 'workflow' not in body:
            return None
        locator = body['workflow']
        require(routing_gates.valid_locator(locator), 'invalid_request')
        # Binding a workflow job needs the service pinned to its checkout (C.3 design 3.1 step 1), before any read.
        require(self.project is not None, 'project_required')
        cwd = Path(locator['cwd']).resolve()  # early refusal; the reader checks the checkout it actually resolves
        require(cwd == self.project or self.project in cwd.parents, 'project_mismatch')
        try:
            view = self.workflow.view(dict(locator))
        except routing_gates.WorkflowChanged:
            raise Blocked('workflow_changed') from None
        except routing_gates.ProjectMismatch:
            raise Blocked('project_mismatch') from None
        except Exception:  # noqa: BLE001 - an unreadable workflow never admits
            raise Blocked('workflow_unavailable') from None
        require(routing_gates.valid_view(view), 'workflow_unavailable')
        problem = routing_gates.readiness(view, profile['semantic_digest'])
        if problem is not None:
            return problem
        job = routing_gates.job_binding(locator, view)
        require(routing_gates.binding_fits(job), 'binding_too_large')
        return 'bound', job

    def _now(self, body):
        """The command's clock; only the `--test-inputs` flag lets a request supply `now`."""
        if self.test_inputs and 'now' in body:
            require(_amount(body['now']), 'invalid_request')
            return body['now']
        return self.clock()

    def _check(self, body):
        facts = _facts(body['request'])
        require(('attempt_id' in body or 'session_id' in body)
                and all(_text(body[key]) for key in ('attempt_id', 'session_id') if key in body), 'invalid_request')
        authority = body.get('authority')
        require(authority is None or (type(authority) is str and 0 < len(authority) <= routing.MAX_AUTHORITY
                                      and authority.isascii() and authority.isprintable()), 'invalid_request')
        now = self._now(body)
        settings = self._settings()
        ledger = self._ledger(False)
        require(ledger is not None, 'ledger_absent')
        return routing.check(ledger.snapshot(), facts, settings, now, attempt_id=body.get('attempt_id'),
                             session_id=body.get('session_id'), authority=authority)

    def _dispatch(self, body):
        attempt, facts = body['attempt_id'], _facts(body['request'])
        require(_text(attempt), 'invalid_request')
        ledger = self._ledger(False, write=True)
        require(ledger is not None, 'ledger_absent')
        self._expire(ledger)
        # Settings and clock are read inside the write transaction, which `set` also holds while it writes.
        return routing.dispatch(ledger, attempt, facts, self._settings, lambda: self._now(body), workflow=self.workflow)

    def _record(self, body):
        event = body['event']
        reason = routing.event_error(event)
        require(reason is None, reason)
        ledger = self._ledger(False, write=True)
        require(ledger is not None, 'ledger_absent')
        self._expire(ledger)
        result = routing.record_event(ledger, event, self.clock())
        return dict(result, event_id=event['event_id']) if result['state'] == 'recorded' else result

    def _resolve_cost_unknown(self, body):
        """Operator recovery for a `cost_unknown` dollar hold (S3.6a): the full reservation unless receipt-backed."""
        return self._operator(routing.resolve_event(body))

    def _resolve_overrun(self, body):
        """Operator decision on an unconfirmed overrun excess (S3.6a): confirm charges it, dismiss drops it."""
        return self._operator(routing.overrun_event(body))

    def _reconcile_uncertain(self, body):
        """Operator reconciliation of an uncertain dollar hold (S3.6b-2): receipts or the full reservation."""
        return self._operator(*routing.reconcile_event(body))

    def _resolve_block(self, body):
        """Operator recovery of an over-bound S1 call or a legacy block (S3.6b-2); other reasons stay."""
        return self._operator(*routing.block_event(body), apply=routing.resolve_block)

    def _reconcile_subscription(self, body):
        """Operator reconciliation of an uncertain subscription hold (S3.6c): a host re-read or abandon."""
        return self._operator(*subscription_reconcile.reconcile_event(body), apply=subscription_reconcile.reconcile)

    def _drop_debit(self, body):
        """Operator removal of one S3.6c window debit against its `status` marker (C3-0a), e.g. a rolling window's."""
        return self._operator(*subscription_reconcile.drop_event(body), apply=subscription_reconcile.drop)

    def _settle_legacy_call(self, body):
        """Operator settlement of an open legacy S1 run call (C3-0a, design 4.4): release an unsent call, or charge."""
        return self._operator(*ledger_versions.settle_event(body), apply=ledger_versions.settle)

    def _operator(self, event, reason='invalid_request', apply=None):
        require(event is not None, reason)
        ledger = self._ledger(False, write=True)
        require(ledger is not None, 'ledger_absent')
        result = (apply or routing.record_event)(ledger, event, self.clock())
        return dict(result, event_id=event['event_id']) if result['state'] == 'recorded' else result

    def _settle(self, body):
        """Read-only root-task settlement (S3.6a). With `workflow` it covers the attempts bound to that full root
        locator and acceptance comes only from the trusted workflow reader; without it, the attempts with no
        workflow job whose task id is `root_task_id`, with acceptance `unavailable` and no ratio."""
        root, locator = body['root_task_id'], body.get('workflow')
        require(_text(root) and len(root) <= routing.MAX_ROOT_ID, 'invalid_request')
        # A pinned service settles only roots it can check against its project, i.e. with a workflow locator.
        require(locator is not None or self.project is None, 'project_required')
        accepted = None
        if locator is not None:
            require(routing_gates.valid_root_locator(locator) and locator['task_id'] == root, 'invalid_request')
            if self.project is not None:
                cwd = Path(locator['cwd']).resolve()
                require(cwd == self.project or self.project in cwd.parents, 'project_mismatch')
        ledger = self._ledger(False)
        require(ledger is not None, 'ledger_absent')
        key = usage_budget.root_key(locator) if locator is not None else usage_budget.root_key(task=root)
        report = ledger.root_report(key, self.clock())
        if locator is not None:
            try:
                found = self.workflow.acceptances(dict(locator))
            except routing_gates.ProjectMismatch:
                raise Blocked('project_mismatch') from None
            except Exception:  # noqa: BLE001 - unreadable canonical state: acceptance stays unknown
                found = None
            if type(found) is list and all(_text(item) for item in found):
                accepted = len(set(found))
        return routing.settle_root(report, root, accepted)

    def _status(self, body):
        try:
            settings = self._settings()
        except Blocked:
            settings = None
        revision = settings.pop('revision') if settings is not None else None
        stored = usage_budget.DEFAULT_CAPS
        if self._open is None and not os.path.lexists(self.ledger_path):
            ledger = dict(state='absent')
        else:
            try:
                data = self._ledger(False).snapshot()
                stale = len(routing.unsent_admissions(data, revision)) if revision is not None else None
                ledger = dict(state='ready', stale_admissions=stale, legacy_outstanding=ledger_versions.outstanding(data),
                              **routing.ledger_summary(data))
                stored = ledger['caps']
            except (Blocked, BudgetError, sqlite3.Error) as error:
                self.close()
                reason = error.reason if isinstance(error, Blocked) else str(error) if isinstance(error, BudgetError) \
                    else 'ledger_busy' if _busy(error) else 'ledger_unavailable'
                ledger = dict(state='unavailable', reason=reason)
        # Admission uses the stricter of the settings caps and the ledger's stored caps; `set` can only lower them.
        effective = None if settings is None else {key: min(settings['caps'][key], stored[key]) for key in stored}
        return dict(state='ok', settings=settings, settings_state='ok' if settings is not None else 'unavailable',
                    settings_revision=revision, effective_caps=effective, ledger=ledger, test_inputs=self.test_inputs,
                    project_pinned=self.project is not None)

    def _set(self, body):
        if 'organization' in body:
            require(set(body) == {'organization'}, 'invalid_request')
            return self._organization(body['organization'])
        if 'authority_required' in body:
            value = body['authority_required']
            require(set(body) == {'authority_required'} and type(value) is bool, 'invalid_request')
            require(value, 'authority_required_one_way')
            self._ledger(True, write=True).require_authority()
            return dict(state='updated', authority_required=True)
        require(set(body) == {'expected_revision', 'change'}, 'invalid_request')
        if type(body['change']) is dict and 'managed_projects' in body['change']:
            body = dict(body, change=self._managed_change(body['change']))
            added = set(body['change']['managed_projects']) - set(self._settings()['managed_projects'])
            if added:
                return self._recheck_activation(self._write_settings(body), added)
        return self._write_settings(body)

    def _recheck_activation(self, answer, added):
        """The activation gate runs before the write, so an assignment committed in between would keep an unmanaged
        launch permit in a now-managed project (PR #217 follow-up). Re-check after the write; on a hit, write the
        added projects out again (a new revision through the same ordered write, keeping any concurrent change)
        and refuse. Exhausted retries or a failed rollback write leave the project managed, where a pre-managed replay
        is refused anyway; either is still this refusal, with `rolled_back: false` and the failure chained. Every
        refusal carries the first write's `invalidated_attempts`, which that write already invalidated."""
        from runtime import delegation_routing
        if not any(map(delegation_routing.open_unrouted_assignments, sorted(added))):
            return answer
        invalidated = answer.get('invalidated_attempts')
        for _ in range(MAX_ROLLBACK):
            current = self._settings()
            try:
                self._write_settings(dict(expected_revision=current['revision'], change=dict(managed_projects=[
                    item for item in current['managed_projects'] if item not in added])))
                break
            except Exception as error:  # noqa: BLE001 - only a stale revision is retried; anything else is reported
                if not (isinstance(error, Blocked) and error.reason == 'stale_revision'):
                    raise Blocked('open_unrouted_assignments', rolled_back=False,
                                  invalidated_attempts=invalidated) from error
        else:
            raise Blocked('open_unrouted_assignments', rolled_back=False, invalidated_attempts=invalidated)
        raise Blocked('open_unrouted_assignments', rolled_back=True, invalidated_attempts=invalidated)

    def _write_settings(self, body):
        # Healthy ledger: write the settings while holding its write lock, the lock `dispatch` reads settings under,
        # so a change and a dispatch are totally ordered and `invalidated_attempts` is exact. A ledger that is absent,
        # busy, corrupt or unsafe must not stop a change (turning API Usage or Smart Routing off is a money stop):
        # the settings are still written (the revision check applies) and the list is null with `ledger_state`.
        # That stays safe because `dispatch` re-reads the settings inside its transaction and `check` compares revisions.
        stage = {}
        try:
            ledger = self._ledger(False, write=True)
            if ledger is not None:
                with ledger.transaction() as data:
                    stage['entered'] = True
                    return self._update_settings(body, data, stage)
            problem = 'absent'
        except Exception as error:  # noqa: BLE001 - every ledger failure falls back; settings refusals re-raise
            if stage.get('entered') and 'updated' not in stage:
                raise  # the settings change itself was refused or failed: nothing was written
            self.close()
            problem = (error.reason if isinstance(error, Blocked) else str(error) if isinstance(error, BudgetError)
                       else 'ledger_busy' if _busy(error) else 'ledger_unavailable')
            if 'updated' in stage:  # written under the lock, then the ledger failed: report it as unordered
                return dict(stage['updated'], invalidated_attempts=None, ledger_state=problem)
        return dict(self._update_settings(body, None, stage), invalidated_attempts=None, ledger_state=problem)

    def _managed_change(self, change):
        """`managed_projects` changes only through the one-shot command (`operator_op_not_served` from `--serve` and
        from every in-process service, `managed_service` included) and is stored as realpaths. Adding a project needs
        the activation gate (C.3 design 4.2), which runs before any ledger lock and writes nothing when it fails;
        removal always works."""
        require(self.one_shot, 'operator_op_not_served')
        try:
            value = [str(Path(item).resolve()) for item in routing_settings._managed_projects(change['managed_projects'])]
            routing_settings._managed_projects(value)  # still distinct once resolved
        except (SettingsError, OSError, RuntimeError):
            raise Blocked('invalid_managed_projects') from None
        if set(value) - set(self._settings()['managed_projects']):
            from runtime import delegation_routing  # C3-2: no project joins with an unrouted native launch open
            _require_managed_hosts(delegation_routing, sorted(set(value) - set(self._settings()['managed_projects'])))
            require(not any(map(delegation_routing.open_unrouted_assignments, set(value) - set(self._settings()[
                'managed_projects']))), 'open_unrouted_assignments')
            codes = activation_gate()
            if codes:
                raise Blocked('managed_path_incomplete', failing=sorted(codes), codes=codes)
        return dict(change, managed_projects=value)

    def _update_settings(self, body, data, stage):
        try:
            updated = routing_settings.update(self.settings_path, body['change'], body['expected_revision'])
        except SettingsError as error:
            raise Blocked(str(error)) from None
        revision = updated.pop('revision')
        stage['updated'] = dict(state='updated', revision=revision, settings=updated)
        stale = routing.unsent_admissions(data, revision) if data is not None else None
        return dict(stage['updated'], invalidated_attempts=stale)

    def _organization(self, value):
        """Explicit user action through the ledger's own enable/disable paths; routing never calls this itself."""
        require(type(value) is dict and _text(value.get('id')) and type(value.get('enabled')) is bool,
                'invalid_request')
        if value['enabled']:
            require(set(value) == {'id', 'enabled', 'policy', 'expected_revision'}, 'invalid_request')
            try:
                revision = self._ledger(True, write=True).enable_organization(value['id'], value['policy'],
                                                                              value['expected_revision'])
            except (TypeError, KeyError, AttributeError):
                raise Blocked('invalid_request') from None
            return dict(state='updated', organization=dict(id=value['id'], enabled=True, revision=revision))
        require(set(value) == {'id', 'enabled'}, 'invalid_request')
        ledger = self._ledger(False, write=True)
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
        _write(stdout, service.handle(line.rstrip(b'\r\n')))


def default_settings():
    return Path.home() / '.local/state/vibe-workflow/routing/settings.json'


def default_ledger():
    from runtime import usage_settings  # the one account-wide ledger Usage Routing already uses
    return usage_settings.directory() / 'organizations.sqlite3'


def managed_service(checkout, scope=None):
    """The only way production code builds an in-process service for a managed launch (C.3 design 3.3): pinned to
    the realpath of `checkout`, on the default stores read at call time. A caller that holds the canonical delegation
    scope passes it and every locator resolves to it; the reader still compares its root with the pin."""
    project = Path(checkout).resolve()
    resolve = None if scope is None else (lambda locator: scope)
    return Service(default_settings(), default_ledger(), project=project,
                   workflow=routing_gates.D6Workflow(resolve=resolve, project=project))


def _package():
    from runtime import state  # the plugin package root, never the project checkout
    return state.PACKAGE


def _self_check(kind, target):
    """One capability's self-check in a child of this interpreter, in a fresh owner-only sandbox with a fixed
    environment, deleted afterwards. Returns its failure codes ([] when it passes)."""
    folder = Path(tempfile.mkdtemp(prefix='routing-self-check-')).resolve()
    try:
        for name in ('home', 'tmp', 'work'):
            (folder / name).mkdir(mode=0o700)
        env = dict(PATH='/usr/bin:/bin', HOME=str(folder / 'home'), TMPDIR=str(folder / 'tmp'), LANG='C.UTF-8')
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', SELF_CHECK_CHILD, str(_package()), kind, target,
                                  str(folder / 'work')], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, env=env, cwd=folder / 'work', start_new_session=True)
        try:
            output, _ = child.communicate(timeout=SELF_CHECK_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)  # the whole process group, not just the child
            except OSError:
                pass
            try:
                child.communicate(timeout=SELF_CHECK_REAP)
            except subprocess.TimeoutExpired:
                child.stdout.close()
                child.wait()
            return ['self_check_timeout']
        lines = [line for line in output.decode('utf-8', 'replace').splitlines() if line.startswith(SELF_CHECK_MARK)]
        try:
            codes = json.loads(lines[-1][len(SELF_CHECK_MARK):])
        except (IndexError, ValueError):
            return ['self_check_error']
        if type(codes) is not list or len(codes) > 16 or not all(
                type(code) is str and SELF_CHECK_CODE.fullmatch(code) for code in codes):
            return ['self_check_invalid']
        return codes if child.returncode == 0 or codes else ['self_check_error']
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _require_managed_hosts(delegation_routing, projects):
    """Refuse adding a project none of whose enrolled clients is in MANAGED_HOSTS; an unreadable enrolment fails
    closed (`enrolment_unavailable`). Read-only, before the self-checks and any ledger lock."""
    unreadable, advisory = [], []
    for project in projects:
        try:
            hosts = delegation_routing.enrolled_hosts(project)
        except Exception:  # noqa: BLE001 - e.g. RuntimeScopeError or OSError: unknown enrolment never joins
            unreadable.append(project)
            continue
        if not set(hosts) & set(MANAGED_HOSTS):
            advisory.append(project)
    if unreadable:
        raise Blocked('enrolment_unavailable', projects=unreadable)
    if advisory:
        raise Blocked('advisory_host_unmanaged', projects=advisory)


def activation_gate():
    """The failing capabilities as {name: codes}; empty when every self-check passes."""
    failures = {}
    for name, kind, target in CAPABILITIES:
        codes = _self_check(kind, target)
        if codes:
            failures[name] = codes
    return failures


def _arguments(argv):
    op, serving, test_inputs, paths = None, False, False, {}
    items = list(argv)
    while items:
        item = items.pop(0)
        if item == '--serve' and not serving:
            serving = True
        elif item == TEST_FLAG and not test_inputs:
            test_inputs = True
        elif item in ('--settings', '--ledger', '--table', '--project') and item not in paths and items \
                and not items[0].startswith('-'):
            paths[item] = items.pop(0)
        elif not item.startswith('-') and op is None:
            op = item
        else:
            return None
    if serving == (op is not None):
        return None
    if test_inputs and not ({'--settings', '--ledger'} <= set(paths) and
                            _resolved(paths['--settings']) != _resolved(default_settings()) and
                            _resolved(paths['--ledger']) != _resolved(default_ledger())):
        return None
    return op, serving, test_inputs, paths


def _silence(stdout):
    """The reader went away: send any further output (including the interpreter's final flush) to /dev/null."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, stdout.fileno())
        os.close(devnull)
    except (AttributeError, OSError, ValueError):
        pass


def main(argv=None, stdin=None, stdout=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    parsed = _arguments(sys.argv[1:] if argv is None else argv)
    try:
        if parsed is None:
            _write(stdout, dict(v=VERSION, state='blocked', reason='usage_error'))
            return USAGE
        op, serving, test_inputs, paths = parsed
        service = Service(paths.get('--settings') or default_settings(), paths.get('--ledger') or default_ledger(),
                          paths.get('--table'), test_inputs=test_inputs, serving=serving,
                          project=paths.get('--project'), one_shot=not serving)
        try:
            if serving:
                return serve(service, stdin, stdout)
            _write(stdout, service.handle(stdin.read(MAX_INPUT + 1), op=op))
            return 0
        finally:
            service.close()
    except BrokenPipeError:
        _silence(stdout)
        return 0 if parsed is not None else USAGE


if __name__ == '__main__':
    raise SystemExit(main())
