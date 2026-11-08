"""Replay recorded arrivals through the pure allocator and an isolated ledger per run.

The harness only composes `runtime.routing_allocator`, `runtime.routing_settings.resolve` and the merged
`runtime.usage_budget` ledger. Subscription windows are plain fractions held by the harness; the ledger
holds the API grant. Admission records live in the harness until the admission layer persists them.
Network, clocks and models are never touched: time comes from each fixture event.
"""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from runtime import routing_allocator as allocator, routing_settings, usage_budget as budget

FIXTURES = Path(__file__).parent / 'fixtures/smart-routing/arrivals'
ORG = 'org'
MINIMUM_HORIZON = 900
SCENARIOS = sorted(path.stem for path in FIXTURES.glob('*.json'))
EPSILON = 1e-9


def load(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


class Replay:
    def __init__(self, scenario, folder, settings=None):
        self.scenario = copy.deepcopy(scenario)
        self.settings = routing_settings.resolve(settings or {})
        path = Path(folder) / 'private' / 'organizations.sqlite3'
        self.identity = budget.initialize(path)
        self.ledger = budget.Ledger(path, self.identity)
        self.ledger.configure(ORG, self.scenario['policy'], 0)
        self.G = self.scenario['balance']
        self.balance = self.G
        self.ledger.observe(ORG, self.balance, 1, issued=self.G)
        self.ledger.set_caps(**self.scenario['caps'])
        self.pools = self.scenario['pools']
        for pool in self.pools.values():
            for window in pool['windows']:
                window['reserved'] = 0.0
        self.records, self.log, self.holds, self.settled = [], [], {}, []
        self.smart = self.settings['smart_routing']
        self.epoch = 0
        self.observed_at = 1
        self.checks = 0

    def close(self):
        self.ledger.close()

    # -- state ---------------------------------------------------------------------------------------
    def advance_windows(self, now):
        for window in (w for p in self.pools.values() for w in p['windows']):
            if window['reset_at'] <= now:
                while window['reset_at'] <= now:
                    window['reset_at'] += window['length']
                window['used'] = 0.0
                self.epoch = max(self.epoch, now)

    def refresh(self, now):
        if now > self.observed_at:
            self.ledger.observe(ORG, self.balance, now)
            self.observed_at = now

    def window_views(self, pool, cost):
        return [dict(id=w['id'], used=w['used'], reserved=w['reserved'], length=w['length'],
                     reset_at=w['reset_at']) for w in self.pools[pool]['windows']]

    def sub_feasible(self, cost):
        return [name for name, pool in self.pools.items()
                if all(w['used'] + w['reserved'] + cost <= 1 + EPSILON for w in pool['windows'])]

    def api_state(self, event, now):
        org = self.ledger.snapshot()['organizations'][ORG]
        policy = org['policy']
        padded = -(-event['api_cost'] * (10000 + self.settings['margin_bp']) // 10000)
        headroom = self.ledger.credit_headroom(ORG, now)
        feasible = now < policy['stop_at'] and padded <= headroom and org['balance'] is not None
        return padded, headroom, feasible, policy

    def expected_spend(self, now, hours_to_expiry):
        window = self.settings['lookback_hours'] * 3600
        spent = sum(amount for at, amount in self.settled if now - window <= at <= now)
        return spent * hours_to_expiry / self.settings['lookback_hours']

    # -- decisions -----------------------------------------------------------------------------------
    def arrive(self, event):
        now = event['t']
        self.advance_windows(now)
        self.refresh(now)
        if not self.smart:
            return self.arrive_off(event, now)
        bucket = (event['role'], event['complexity'])
        cost = event['sub_cost']
        padded, headroom, api_ok, policy = self.api_state(event, now)
        sub_pools = self.sub_feasible(cost)
        routes = {name: self.window_views(name, cost) for name in sub_pools}
        u, _ = allocator.projected_utilization(routes, cost, now, self.settings['min_elapsed_fraction'])
        feasible = [c for c, ok in (('api', api_ok), ('subscription', u is not None and bool(sub_pools))) if ok]
        hours = (policy['expiry'] - now) / 3600
        e = 0.0
        if api_ok:
            e = allocator.expiry_pressure(headroom, self.expected_spend(now, hours), hours, self.settings['T_hours'])
        if u is None:
            shares = dict(api=1.0 if api_ok else 0.0, subscription=0.0)
        else:
            share = allocator.api_share(u, e, self.settings['L'], self.settings['H'])
            shares = dict(api=share, subscription=1 - share)
        klass = allocator.choose_class(shares, self.records, bucket, self.epoch, feasible)
        if klass is None:
            return self.log.append(dict(id=event['id'], at=now, outcome='pending', reason='no_feasible_class'))
        candidates = self.candidates(klass, event, now, padded, headroom, sub_pools, policy)
        route = allocator.choose_route(candidates, now, MINIMUM_HORIZON)
        if route is None:
            return self.log.append(dict(id=event['id'], at=now, outcome='pending', reason='no_feasible_route'))
        if not self.reserve(route, event, now):
            return
        self.admit(event, now, mode='auto', bucket=bucket, shares=shares, feasible=feasible, klass=klass,
                   route=route, candidates=candidates)

    def candidates(self, klass, event, now, padded, headroom, sub_pools, policy):
        if klass == 'api':
            return [dict(route='api:' + ORG, pool='api:' + ORG, constraints=[
                dict(pool='api:' + ORG, window='grant', headroom=headroom, cost=padded,
                     resets_at=policy['expiry'])])]
        return [dict(route='sub:' + name, pool=name, constraints=[
            dict(pool=name, window=w['id'], headroom=1 - w['used'] - w['reserved'], cost=event['sub_cost'],
                 resets_at=w['reset_at']) for w in self.pools[name]['windows']]) for name in sub_pools]

    def arrive_off(self, event, now):
        for route in self.scenario['worker_choices']:
            kind, _, name = route.partition(':')
            if kind == 'sub' and name in self.sub_feasible(event['sub_cost']):
                if self.reserve(route, event, now):
                    return self.admit(event, now, mode='off', bucket=(event['role'], event['complexity']),
                                      shares=None, feasible=None, klass='subscription', route=route, candidates=None)
                return
        self.log.append(dict(id=event['id'], at=now, outcome='pending', reason='worker_choice_unavailable'))

    def reserve(self, route, event, now):
        kind, _, name = route.partition(':')
        if kind == 'sub':
            for window in self.pools[name]['windows']:
                window['reserved'] += event['sub_cost']
            self.holds[event['id']] = ('sub', name, event['sub_cost'])
            return True
        org = self.ledger.snapshot()['organizations'][ORG]
        try:
            self.ledger.begin(ORG, event['id'], event['id'], 'packet', event['api_cost'], org['revision'], now,
                              dict(account=ORG, task=event['id'], windows=[]),
                              next_request_bound=event['api_cost'], margin_bp=self.settings['margin_bp'])
        except budget.BudgetError as error:
            self.log.append(dict(id=event['id'], at=now, outcome='rejected', reason=str(error)))
            return False
        self.holds[event['id']] = ('api', ORG, event['api_cost'])
        return True

    def admit(self, event, now, **fields):
        record = dict(id=event['id'], at=now, epoch=self.epoch, **{'class': fields.pop('klass')}, **fields)
        self.records.append(record)
        self.log.append(dict(id=event['id'], at=now, outcome='admitted', route=record['route'],
                             **{'class': record['class']}))
        self.verify(now)

    def complete(self, event):
        now = event['t']
        held = self.holds.pop(event['id'], None)
        if held is None:
            return  # never admitted
        kind, name, _ = held
        if kind == 'sub':
            for window in self.pools[name]['windows']:
                window['reserved'] -= event['sub_cost'] if 'sub_cost' in event else self.cost_of(event['id'])
                window['used'] += event['sub_actual']
        else:
            self.ledger.dispatch(event['id'], 'count', self.revision(), now)
            self.ledger.complete(event['id'], 'count', event['api_actual'])
            self.ledger.close_run(event['id'])
            self.settled.append((now, event['api_actual']))
        self.verify(now)

    def cost_of(self, job):
        return next(e['sub_cost'] for e in self.scenario['events'] if e['kind'] == 'arrive' and e['id'] == job)

    def revision(self):
        return self.ledger.snapshot()['organizations'][ORG]['revision']

    def run(self):
        for event in self.scenario['events']:
            kind = event['kind']
            if kind == 'arrive':
                self.arrive(event)
            elif kind == 'complete':
                self.complete(event)
            elif kind == 'observe':
                self.balance = min(self.balance, event['balance'])
                self.ledger.observe(ORG, event['balance'], event['t'])
                self.observed_at = event['t']
            elif kind == 'toggle':
                self.smart = event['smart_routing']
                self.epoch = event['t']  # a new policy revision starts a new epoch; stale decisions stay dead
        return self.log

    # -- invariants ----------------------------------------------------------------------------------
    def verify(self, now):
        """No funding violation: the protected allowance and every window stay inside their limits."""
        data = self.ledger.snapshot()
        self.checks += 1
        assert budget.Ledger._credit_raw(data, ORG, now) >= 0, 'protected allowance breached'
        for pool in self.pools.values():
            for window in pool['windows']:
                assert window['used'] + window['reserved'] <= 1 + EPSILON, 'window over-committed'
                assert window['reserved'] >= -EPSILON, 'negative hold'
        open_runs = [r for r in data['runs'].values() if not r['closed']]
        total, per_account = data['caps']['total'], data['caps']['per_account']
        assert len(open_runs) <= total and len(open_runs) <= per_account, 'concurrency cap exceeded'


def replay(name, settings=None):
    with tempfile.TemporaryDirectory() as folder:
        run = Replay(load(name), folder, settings)
        try:
            run.run()
            return run.log, run.records, run
        finally:
            run.close()


class ReplayTests(unittest.TestCase):
    def test_fixtures_present(self):
        self.assertEqual(SCENARIOS, ['mixed_load', 'off_toggle', 'reset_and_expiry'])

    def test_every_scenario_replays_exactly(self):
        for name in SCENARIOS:
            with self.subTest(name):
                first, records_first, run = replay(name)
                second, records_second, _ = replay(name)
                self.assertEqual(first, second)
                self.assertEqual(records_first, records_second)
                self.assertGreater(run.checks, 0)
                self.assertTrue(any(item['outcome'] == 'admitted' for item in first))

    def test_class_and_route_replay_from_admission_records_alone(self):
        for name in SCENARIOS:
            _, records, _ = replay(name)
            for record in (r for r in records if r['mode'] == 'auto'):
                prior = records[:records.index(record)]
                with self.subTest(name, job=record['id']):
                    self.assertEqual(allocator.choose_class(record['shares'], prior, record['bucket'],
                                                            record['epoch'], record['feasible']), record['class'])
                    self.assertEqual(allocator.choose_route(record['candidates'], record['at'], MINIMUM_HORIZON),
                                     record['route'])

    def test_rejections_and_pending_leave_no_admission_record(self):
        log, records, _ = replay('mixed_load')
        admitted = [item['id'] for item in log if item['outcome'] == 'admitted']
        self.assertEqual([r['id'] for r in records], admitted)
        refused = [item for item in log if item['outcome'] != 'admitted']
        self.assertTrue(refused, 'the burst should hit the concurrency cap')
        self.assertTrue(all(item['reason'] == 'concurrency_cap' for item in refused if item['outcome'] == 'rejected'))
        self.assertFalse({item['id'] for item in refused} & set(admitted))

    def test_concurrent_claims_never_exceed_headroom(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Replay(load('mixed_load'), folder)
            try:
                run.ledger.set_caps(8, 8)
                run.refresh(100000)
                estimate = run.ledger.credit_headroom(ORG, 100000) // 2  # padded to 60% of headroom: one fits
                revision, outcomes = run.revision(), []

                def claim(task):
                    with budget.Ledger(run.ledger.path, run.identity) as handle:  # one connection per thread
                        try:
                            handle.begin(ORG, task, task, 'packet', estimate, revision, 100000,
                                         dict(account=ORG, task=task, windows=[]), next_request_bound=estimate,
                                         margin_bp=run.settings['margin_bp'])
                            outcomes.append('admitted')
                        except budget.BudgetError as error:
                            outcomes.append(str(error))
                threads = [threading.Thread(target=claim, args=('race-%d' % i,)) for i in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                self.assertEqual(sorted(outcomes), ['admitted', 'insufficient_headroom'])
                run.verify(100000)
            finally:
                run.close()

    def test_reset_and_expiry(self):
        log, records, run = replay('reset_and_expiry')
        stop = load('reset_and_expiry')['policy']['stop_at']
        api = [r for r in records if r['class'] == 'api']
        self.assertTrue(api, 'an expiring grant should attract work')
        self.assertTrue(all(r['at'] < stop for r in api))
        late = [r for r in records if r['at'] >= stop]
        self.assertTrue(late)
        self.assertTrue(all(r['class'] == 'subscription' for r in late))
        # The 5-hour window resets at 118000; arrivals after it start a fresh class epoch.
        before = [r for r in records if r['at'] < 118000]
        after = [r for r in records if r['at'] >= 118000]
        self.assertTrue(before and after)
        self.assertTrue(all(r['epoch'] < 118000 for r in before))
        self.assertTrue(all(r['epoch'] >= 118000 for r in after))

    def test_no_resurrected_capacity(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Replay(load('mixed_load'), folder)
            try:
                run.run()
                now = run.observed_at
                before = run.ledger.credit_headroom(ORG, now)
                with self.assertRaises(budget.BudgetError):  # an older observation is refused
                    run.ledger.observe(ORG, run.G, now - 1)
                run.ledger.observe(ORG, run.G, now + 1)  # a newer but stale-high balance cannot raise capacity
                self.assertLessEqual(run.ledger.credit_headroom(ORG, now + 1), before)
                self.assertEqual(run.ledger.snapshot()['organizations'][ORG]['balance'], run.G)
                # A completed run keeps its charge in every later headroom figure.
                spent = sum(amount for _, amount in run.settled)
                self.assertGreater(spent, 0)
                protected = budget.Ledger._protected(run.ledger.snapshot()['organizations'][ORG])
                self.assertLessEqual(run.ledger.credit_headroom(ORG, now), run.G - protected - spent)
            finally:
                run.close()

    def test_window_reset_does_not_clear_holds(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Replay(load('reset_and_expiry'), folder)
            try:
                run.advance_windows(100000)
                window = run.pools['sub-a']['windows'][1]
                window['reserved'] = 0.2
                run.advance_windows(window['reset_at'] + 1)
                self.assertEqual(window['used'], 0.0)
                self.assertEqual(window['reserved'], 0.2)
            finally:
                run.close()

    def test_off_toggle_uses_configured_choices_without_the_allocator(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Replay(load('off_toggle'), folder)
            try:
                scenario = run.scenario['events']
                off_at = next(e['t'] for e in scenario if e['kind'] == 'toggle' and not e['smart_routing'])
                on_at = next(e['t'] for e in scenario if e['kind'] == 'toggle' and e['smart_routing'])
                with mock.patch.object(allocator, 'choose_class', wraps=allocator.choose_class) as spy:
                    run.run()
                during = [r for r in run.records if off_at <= r['at'] < on_at]
                self.assertTrue(during)
                self.assertTrue(all(r['mode'] == 'off' and r['route'] == 'sub:sub-b' for r in during))
                after = [r for r in run.records if r['at'] > on_at]
                self.assertTrue(after and all(r['mode'] == 'auto' and r['epoch'] == on_at for r in after))
                # The allocator ran once per automatic admission and never while OFF.
                self.assertEqual(spy.call_count, len([r for r in run.records if r['mode'] == 'auto']))
                self.assertFalse(any(r['class'] == 'api' for r in during))
            finally:
                run.close()

    def test_settings_default_is_on_and_off_is_respected(self):
        _, records, _ = replay('mixed_load')
        self.assertTrue(all(r['mode'] == 'auto' for r in records))
        _, records, _ = replay('mixed_load', dict(smart_routing=False))
        self.assertTrue(all(r['mode'] == 'off' for r in records) or not records)


if __name__ == '__main__':
    unittest.main()
