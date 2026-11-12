"""Shared helpers and the `Funded` ledger fixture for the routing funding tests (test_routing_funding*.py)."""
import math
import os
import tempfile
from pathlib import Path
from runtime import routing, routing_settings, usage_budget as budget


M = 1_000_000
NOW = 1_000_000
SCOPE = 'scope1'
READY = dict(ref='keychain:opaque-ref', scope=SCOPE, state='ready')
ITERATIONS = int(os.environ.get('ROUTING_PROPERTY_ITERATIONS', '300'))
STEPS = 30


def policy(**updates):
    base = dict(cycle='c1', start=0, end=10 * NOW, expiry=10 * NOW, stop_at=9 * NOW, ceiling='none', amount=None,
                grant_amount=0, pilot_limit=None, task_limit=None)
    base.update(updates)
    return base


def native(task):
    return {'account': 'account', 'task': task, 'windows': []}


def window(wid='credit', limit=10**12, used=0, organization=None, revision=1, **extra):
    item = dict(id=wid, limit=limit, used=used, length=None, reset_at=None, fresh=True, **extra)
    if organization is not None:
        item.update(organization=organization, organization_revision=revision)
    return item


def funded_pool(organization, kind='api_grant', **window_options):
    return dict(billing_class=kind, windows=[window(organization=organization, **window_options)])


def sub_pool(limit=1000, used=0):
    return dict(windows=[dict(id='5h', limit=limit, used=used, length=18000, reset_at=None, rolling=True, fresh=True)])


def route(rid, pool, kind, account, demand, strategy=None):
    item = dict(route=rid, strategy_id=strategy or 's-' + rid, provider='p', model='m', effort='high', harness=rid,
                account=account, billing_class=kind, pool=pool, demand=demand, capable=True, permitted=True)
    if kind == 'included_subscription':
        item['binding_enforceable'] = True
    else:
        item.update(credential=dict(READY), funding_verified=True)
    if kind == 'gateway':
        item['integration_enabled'] = True
    return item


def settings(**updates):
    base = dict(api_usage_enabled=True, caps=dict(total=40, per_account=40))
    base.update(updates)
    return dict(routing_settings.resolve(base), revision=1)


def assignment(attempt, task=None):
    return dict(attempt_id=attempt, task=task or attempt, role='software-engineer', work_kind='implementation', complexity_band='medium',
                scope=SCOPE)


def ranking(*ids):
    return dict(strategy_ids=list(ids), reason_codes=['ranking_exact'], table_version='t1', profile_digest='d1')


def observations(routes, pools, now=NOW):
    return dict(now=now, routes=routes, pools=pools)


def reserved(data, organization, task=None, cycle=None):
    """R computed independently of the ledger's own helpers."""
    total = 0
    for run in data['runs'].values():
        if run['organization'] == organization and (task is None or run['task'] == task):
            for call in run['calls'].values():
                if cycle is None or call['cycle'] == cycle:
                    total += call['cost'] if call['cost'] is not None else call['maximum']
    for hold in data['holds'].values():
        if not hold['closed'] and hold['organization'] == organization and (task is None or hold['task'] == task) \
                and (cycle is None or hold['cycle'] == cycle):
            total += hold['amount']
    return total


def protected(org):
    if org['policy'].get('allowance') == 'no-limit':
        return 0
    bp = org['policy'].get('allowance_bp', budget.DEFAULT_ALLOWANCE_BP)
    return math.ceil((budget.BASIS_POINTS - bp) * (org.get('issued') or 0) / budget.BASIS_POINTS)




def authorize(ledger, attempt, now=None):
    """Dispatch a hold with an issued authority (S3.4); `convert_hold` accepts only such holds."""
    ids = dict(session_id='ses_' + attempt, message_id='msg_' + attempt)
    return ledger.dispatch_hold(attempt, '0' * 64, ids, lambda data, moment: None, NOW if now is None else now)


class Funded:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'private' / 'ledger.sqlite3'
        self.identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, self.identity)
        self.ledger.set_caps(40, 40)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def fund(self, organization='org', balance=10 * M, at=NOW, **updates):
        revision = self.ledger.configure(organization, policy(**updates), 0)
        self.ledger.observe(organization, balance, at, issued=balance)
        return revision

    def admit(self, attempt, routes, pools, ids=None, task=None, cfg=None):
        ids = ids or [item['strategy_id'] for item in routes]
        return routing.admit(assignment(attempt, task), ranking(*ids), cfg or settings(), observations(routes, pools),
                             ledger=self.ledger)

    def begin(self, run, estimate, organization='org', task=None, now=NOW, revision=1):
        return self.ledger.begin(organization, task or run, run, 'packet', estimate, revision, now, native(task or run))
