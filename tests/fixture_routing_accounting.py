"""Shared helpers and the `Accounting` ledger fixture for the S3.6a accounting tests (test_routing_accounting*.py).

Contract: docs/superpowers/plans/2026-11-08-smart-routing.md#task-s36-lineage-settlement-and-retention
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from runtime import usage_budget as budget
from scripts import routing as command
from tests.test_routing_command import API, M, POLICY, api_observations, facts, now, observations, prepare
from tests.test_routing_dispatch import DispatchBase, dispatch, record
from tests.test_routing_gates import FakeWorkflow, entry


DAY = 86400
TOKENS = dict(input=10, output=5, reasoning=1, cache_read=2, cache_write=0)


def usage(*costs, source='provider_receipt', receipts=None, units=None, kind='primary'):
    requests = []
    for i, cost in enumerate(costs):
        item = dict(kind=kind, model='m', variant='high', tokens=dict(TOKENS), cost_usd_micros=cost)
        if receipts:
            item['receipt'] = receipts[i]
        if units:
            item['units'] = dict(units)
        requests.append(item)
    return dict(source=source, requests=requests)


def complete(attempt, event_id=None, **extra):
    return record(attempt, 'complete', event_id, **extra)


def settle(root, **extra):
    request = dict(v=1, op='settle', root_task_id=root)
    request.update(extra)
    return request


def where(root, session='session-1'):
    """A root task's locator; roots are keyed by all four fields (review S4)."""
    return dict(client='codex', cwd='/checkout', session_id=session, task_id=root)


def bound(root, session='session-1'):
    return dict(where(root, session), job_id='job-x')


class Reader(FakeWorkflow):
    """Canonical workflow stand-in: one ready job binds every attempt, and acceptances come from here only."""
    def __init__(self, path, accepted=()):
        super().__init__(path)
        self.write({'job-x': entry()})
        self.accepted = list(accepted)
        self.calls = []

    def acceptances(self, locator):
        self.calls.append(dict(locator))
        if self.accepted is None:
            raise RuntimeError('canonical state unreadable')
        return list(self.accepted)


class Accounting(DispatchBase):
    """A dollar organization at `BALANCE` with no protected share, API Usage on and room for many attempts."""
    BALANCE = 1000 * M
    POLICY = dict(POLICY, allowance_bp=budget.BASIS_POINTS)

    def setUp(self):
        super().setUp()
        self.reader = Reader(Path(self.temp.name) / 'workflow.json')
        self.service.close()
        # Operator ops run only in a one-shot command (C3-0a review S1); only `main()` sets the flag in production.
        self.service = command.Service(self.settings, self.ledger, workflow=self.reader, one_shot=True)
        self.call(dict(v=1, op='set', expected_revision=0,
                       change=dict(api_usage_enabled=True, caps=dict(total=16, per_account=16))))
        budget.initialize(self.ledger)
        with self.open_ledger() as ledger:
            ledger.set_caps(16, 16)
            ledger.enable_organization('personal', self.POLICY, 0)
            ledger.observe('personal', self.BALANCE, now(), issued=self.BALANCE)

    def call_bound(self, request):
        """A workflow-bound `prepare` needs the project pin (`project_required`); `/checkout` is `where()`'s cwd.
        `self.service` stays unpinned, because a pinned service refuses `settle` without `workflow`."""
        service = command.Service(self.settings, self.ledger, workflow=self.reader, project='/checkout',
                                  test_inputs=self.service.test_inputs)
        try:
            return service.handle(json.dumps(request).encode())
        finally:
            service.close()

    def admit(self, attempt, demand=5 * M, root=None, session='session-1'):
        obs = api_observations()
        obs['routes'][-1].update(demand={'credit': demand}, account='acct-' + attempt)
        obs['pools']['api']['windows'][0]['limit'] = 10**12
        request = prepare(attempt, observations=obs, constraints=dict(allowed_strategy_ids=['s-api']))
        if root is not None:
            request.update(workflow=bound(root, session))
            return self.call_bound(request)
        return self.call(request)

    def admit_sub(self, attempt, root=None, at=None):
        request = prepare(attempt, observations=observations(at=at, account='acct-' + attempt),
                          constraints=dict(allowed_strategy_ids=['s-sub']))
        if root is not None:
            request.update(workflow=bound(root))
        response = self.call_bound(request) if root is not None else self.call(request)
        self.assertEqual(response['state'], 'admitted', response)
        return response

    def send(self, attempt, sub=False):
        response = self.call(dispatch(attempt, request=facts() if sub else facts(**API)))
        self.assertEqual(response['state'], 'dispatched', response)
        self.tokens = getattr(self, 'tokens', {})
        self.tokens[attempt] = response['authority']
        return response

    def observe_usage(self, event_id, attempt, anchor=None, token=None, **extra):
        """Report-only usage; it needs an open anchor hold (its own or `root_attempt_id`) and that hold's
        dispatch authority (round-2 review S-A)."""
        event = dict(event_id=event_id, attempt_id=attempt, kind='observed', stage='coordinator', funding='api',
                     usage=usage(1))
        if anchor is not None:
            event['root_attempt_id'] = anchor
        event['authority'] = token if token is not None else self.tokens[anchor or attempt]
        event.update(extra)
        return self.call(dict(v=1, op='record', event=event))

    def funded(self, attempt, demand=5 * M, root=None, session='session-1'):
        response = self.admit(attempt, demand, root, session)
        self.assertEqual(response['state'], 'admitted', response)
        self.send(attempt)

    def credit(self):
        with self.open_ledger() as ledger:
            return ledger.credit_headroom('personal')

    def reasons(self, organization='personal'):
        """The derived admission block (round 3): never a stored flag."""
        return sorted(budget.Ledger.block_reasons(self.snapshot(), organization))

    def admission_refused(self, organization='personal'):
        data = self.snapshot()
        revision = data['organizations'][organization]['revision']
        try:
            budget.Ledger._admission_checks(data, organization, revision, now())
        except budget.BudgetError as error:
            self.assertEqual(str(error), 'manual_recovery_required')
            return True
        return False

    def start_run(self, run, organization='personal'):
        """An S1 run call already in flight (begun and dispatched while the organization was admissible)."""
        with self.open_ledger() as ledger:
            revision = ledger.snapshot()['organizations'][organization]['revision']
            ledger.begin(organization, 'task-' + run, run, 'digest', M, revision, now(),
                         dict(account='na-' + run, task='task-' + run, windows=[]))
            ledger.dispatch(run, 'count', revision, now())

    def finish_run(self, run, actual):
        with self.open_ledger() as ledger:
            ledger.complete(run, 'count', actual)

    def hold(self, attempt):
        return self.snapshot()['holds'][attempt]

    def settled(self):
        return budget.Ledger._total(self.snapshot(), 'personal') - sum(
            hold['amount'] for hold in budget.Ledger.open_holds(self.snapshot()).values())

    def ledger_bytes(self):
        """Every stored byte that accounting writes: the record, the events and the root aggregates."""
        with closing(sqlite3.connect(self.ledger)) as db:
            return [db.execute('SELECT body FROM ledger').fetchone()[0],
                    db.execute('SELECT * FROM events ORDER BY event_id').fetchall(),
                    db.execute('SELECT * FROM roots ORDER BY root').fetchall()]

    def events(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            return [(row[0], json.loads(row[1])) for row in db.execute('SELECT event_id, body FROM events')]
