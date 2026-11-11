"""Replay recorded arrivals through the pure allocator and an isolated ledger per run.

The harness only composes `runtime.routing_allocator`, `runtime.routing_settings.resolve` and the merged
`runtime.usage_budget` ledger. Subscription windows are plain fractions held by the harness; the ledger
holds the API grant. Admission records live in the harness until the admission layer persists them.
Network, clocks and models are never touched: time comes from each fixture event.

`AdmissionReplay` (fixtures in `arrivals/admission/`) drives the merged part-B path instead: the strategy table
compiled and refreshed by `runtime.routing_evidence`, `routing.ensure_profile`, `routing.rank`, `routing.admit` and `routing.record_event`, over one temporary ledger per run.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from runtime import routing, routing_allocator as allocator, routing_evidence, routing_settings, usage_budget as budget

FIXTURES = Path(__file__).parent / 'fixtures/smart-routing/arrivals'
ORG = 'org'
MINIMUM_HORIZON = 900
SCENARIOS = sorted(path.stem for path in FIXTURES.glob('*.json'))
EPSILON = 1e-9


def check(condition, *detail):
    """An invariant check that, unlike a bare `assert`, still runs under `python -O`."""
    if not condition:
        raise AssertionError(*detail)


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
        bucket = (event['work_kind'], event['complexity'])
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
                    return self.admit(event, now, mode='off', bucket=(event['work_kind'], event['complexity']),
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
        """No funding violation: the protected allowance and every window stay inside their limits. F5.A1: no count
        of open runs is an invariant; money and windows are."""
        data = self.ledger.snapshot()
        self.checks += 1
        check(budget.Ledger._credit_raw(data, ORG, now) >= 0, 'protected allowance breached')
        for pool in self.pools.values():
            for window in pool['windows']:
                check(window['used'] + window['reserved'] <= 1 + EPSILON, 'window over-committed')
                check(window['reserved'] >= -EPSILON, 'negative hold')


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

    def hand_scenario(self, used, expiry=2_592_000, jobs=4):
        base = load('mixed_load')
        events = []
        for i in range(jobs):
            events += [dict(t=100000 + 100 * i, kind='arrive', id='h-%d' % i, work_kind='implementation',
                            complexity='medium', api_cost=2_000_000, sub_cost=0.005),
                       dict(t=100000 + 100 * i + 30, kind='complete', id='h-%d' % i, api_actual=1_000_000,
                            sub_actual=0.004)]
        policy = dict(base['policy'], expiry=expiry, end=expiry, stop_at=expiry - 1000)
        pools = dict(only=dict(account='acct', windows=[
            dict(id='weekly', length=604800, reset_at=700000, used=used)]))
        return dict(base, policy=policy, pools=pools, events=events)

    def run_scenario(self, scenario):
        with tempfile.TemporaryDirectory() as folder:
            run = Replay(scenario, folder)
            try:
                run.run()
                return run.records
            finally:
                run.close()

    def test_hand_computed_decisions(self):
        # Independent expectations worked out on paper, not by calling the allocator.
        # Healthy subscription (about 10% used, far from reset), grant expiry 30 days away: share 0, all subscription.
        low = self.run_scenario(self.hand_scenario(used=0.10))
        self.assertEqual([r['class'] for r in low], ['subscription'] * 4)
        self.assertTrue(all(r['shares']['api'] == 0 for r in low))
        # Saturated subscription (99% used): projected utilization is above H, so the API share is 1.
        full = self.run_scenario(self.hand_scenario(used=0.99))
        self.assertEqual([r['class'] for r in full], ['api'] * 4)
        self.assertTrue(all(r['shares']['api'] == 1 for r in full))
        # Healthy subscription but the grant expires in 2 hours of a 72-hour horizon: E = 1 - 2/72 = 35/36,
        # so the first assignment goes to the API; the deficit rule then spreads later ones by those shares.
        soon = self.run_scenario(self.hand_scenario(used=0.10, expiry=107200, jobs=1))
        self.assertEqual(soon[0]['class'], 'api')
        self.assertAlmostEqual(soon[0]['shares']['api'], 35 / 36, places=6)
        self.assertEqual(soon[0]['route'], 'api:org')

    def test_rejections_and_pending_leave_no_admission_record(self):
        log, records, _ = replay('mixed_load')
        admitted = [item['id'] for item in log if item['outcome'] == 'admitted']
        self.assertEqual([r['id'] for r in records], admitted)
        refused = [item for item in log if item['outcome'] != 'admitted']
        self.assertFalse(any(item['reason'] == 'concurrency_cap' for item in refused))  # F5.A1: never a count reason
        self.assertFalse({item['id'] for item in refused} & set(admitted))

    def test_concurrent_claims_never_exceed_headroom(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Replay(load('mixed_load'), folder)
            try:
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


ADMISSION = FIXTURES / 'admission'
COMPILED_AT = '2026-11-10T00:00:00Z'
CLASSES = ('api', 'subscription')


def load_admission(name):
    return json.loads((ADMISSION / (name + '.json')).read_text())


def strategy_id(model, effort, harness):
    """The compiler's strategy id formula, kept here so the collector can offer routes the table never ranked."""
    return hashlib.sha256(json.dumps([model, effort, harness], separators=(',', ':')).encode()).hexdigest()[:16]


class AdmissionReplay:
    """Replay arrivals through compile/refresh, profile, rank, admit and settle over one isolated ledger."""
    def __init__(self, scenario, folder):
        self.scenario = copy.deepcopy(scenario)
        path = Path(folder) / 'private' / 'ledger.sqlite3'
        self.identity = budget.initialize(path)
        self.ledger = budget.Ledger(path, self.identity)
        self.settings = dict(routing_settings.resolve(self.scenario['settings']), revision=1)
        org = self.scenario['organization']
        self.org, self.balance = org['id'], org['balance']
        self.ledger.configure(self.org, org['policy'], 0)
        self.observed_at = None
        self.pools = self.scenario['pools']
        self.deployments = {d['route']: d for d in self.scenario['deployments']}
        self.table = None
        self.tables = []
        self.refresh(self.scenario['catalog'], None)
        self.log, self.open, self.settlements, self.checks, self.charged = [], {}, {}, 0, 0

    def close(self):
        self.ledger.close()

    # -- inputs ----------------------------------------------------------------------------------------
    def refresh(self, catalog, now):
        evidence = self.scenario['evidence']
        inputs = (evidence['guidance'], catalog, evidence['benchmarks'])
        self.table = routing_evidence.refresh_table(self.table, lambda: inputs, now=COMPILED_AT)
        self.catalog = {item['model']: item for item in catalog}
        self.tables.append(dict(at=now, table=self.table, catalog=self.catalog))

    def observe(self, now):
        if self.observed_at is None or now > self.observed_at:
            self.ledger.observe(self.org, self.balance - self.charged, now,
                                **({'issued': self.balance} if self.observed_at is None else {}))
            self.observed_at = now

    def observations(self, now):
        revision = self.ledger.snapshot()['organizations'][self.org]['revision']
        pools = copy.deepcopy(self.pools)
        for pool in pools.values():
            for window in pool['windows']:
                if 'organization' in window:
                    window['organization_revision'] = revision
        routes = []
        for d in self.scenario['deployments']:
            route = dict(route=d['route'], strategy_id=strategy_id(d['model'], d['effort'], d['harness']),
                         provider=d['provider'], model=d['model'], effort=d['effort'], harness=d['harness'],
                         account=d['account'], billing_class=d['billing_class'], pool=d['pool'],
                         demand=dict(d['demand']), capable=True, permitted=True)
            if d['billing_class'] == 'included_subscription':
                route['binding_enforceable'] = True
            else:
                route.update(funding_verified=True, credential=dict(ref='keychain:opaque', scope='scope1', state='ready'))
            routes.append(route)
        return dict(now=now, routes=routes, pools=pools, snapshot_id='snap-%d' % now)

    # -- lifecycle -------------------------------------------------------------------------------------
    def arrive(self, job, now):
        self.observe(now)
        profile = routing.ensure_profile(job)
        ranking = routing.rank(profile, self.table, None)
        assignment = dict(attempt_id=job['id'], task=job['id'], role=profile['role'], work_kind=profile['work_kind'],
                          complexity_band=profile['complexity_band'], scope='scope1')
        outcome = routing.admit(assignment, ranking, self.settings, self.observations(now), ledger=self.ledger)
        entry = dict(id=job['id'], at=now, outcome=outcome['status'], ranked=list(ranking['strategy_ids']),
                     table_version=ranking['table_version'])
        if outcome['status'] == 'admitted':
            entry.update(route=outcome['route'], **{'class': outcome['class']})
            self.open[job['id']] = outcome
            self.dispatched(job['id'], now)
        else:
            entry['reason'] = outcome['reason_code']
        self.log.append(entry)
        self.verify(now)
        return outcome

    def dispatched(self, attempt, now):
        result = routing.record_event(self.ledger, dict(event_id='d-' + attempt, attempt_id=attempt,
                                                        kind='dispatched'), now)
        check(result.get('state') != 'blocked', result)

    def complete(self, attempt, now):
        """Settle at 80% of each reserved amount; subscription use reaches the collector's next snapshot."""
        if attempt not in self.open:  # F5.3: a ranked arrival only paid capacity could fund waited; nothing to settle
            self.log.append(dict(id=attempt, at=now, outcome='never_admitted'))
            return None
        record = self.open.pop(attempt)
        self.observe(now)
        reserved = record['reserved']
        units = {key: amount * 4 // 5 for key, amount in reserved.items() if key.startswith(budget.WIN)}
        cost = sum(amount * 4 // 5 for key, amount in reserved.items() if key.startswith(budget.ORG))
        request = dict(kind='primary', model=self.deployments[record['route']]['model'], tokens=dict(
            input=10, output=5, reasoning=1, cache_read=0, cache_write=0), cost_usd_micros=cost)
        if units:
            request['units'] = units
        result = routing.record_event(self.ledger, dict(
            event_id='c-' + attempt, attempt_id=attempt, kind='complete',
            usage=dict(source='provider_receipt', requests=[request])), now)
        check(result.get('state') != 'blocked', result)
        for key, amount in units.items():
            pool, _, window = key[len(budget.WIN):].partition('/')
            next(w for w in self.pools[pool]['windows'] if w['id'] == window)['used'] += amount
        self.charged += cost
        self.settlements[attempt] = dict(result['settlement'], reserved_claims=dict(reserved))
        self.log.append(dict(id=attempt, at=now, outcome=result['outcome']))
        self.verify(now)
        return result

    def run(self):
        for event in self.scenario['events']:
            if event['kind'] == 'arrive':
                self.arrive(event['job'], event['t'])
            elif event['kind'] == 'complete':
                self.complete(event['id'], event['t'])
            elif event['kind'] == 'refresh':
                self.refresh(event['catalog'], event['t'])
                self.log.append(dict(at=event['t'], outcome='refreshed', table_version=self.table['version']))
        return self.log

    # -- invariants ------------------------------------------------------------------------------------
    def verify(self, now):
        """No funding violation and no resurrected capacity, checked after every transition."""
        data = self.ledger.snapshot()
        self.checks += 1
        held = budget.Ledger.held(data)
        expected = {}
        for record in self.open.values():
            for key, amount in record['reserved'].items():
                expected[key] = expected.get(key, 0) + amount
        check(held == expected, ('held claims differ from open admissions', held, expected))
        for name, pool in self.pools.items():
            for window in pool['windows']:
                if 'organization' not in window:
                    key = routing._window_key(name, window['id'])
                    check(window['used'] + held.get(key, 0) <= window['limit'], 'window over-committed')
        check(budget.Ledger._credit_raw(data, self.org, now) >= 0, 'organization credit overdrawn')
        open_attempts = budget.Ledger.open_attempts(data)
        check(sorted(open_attempts) == sorted(self.open), 'open attempts differ from open admissions')


def run_admission(scenario, runner=None):
    with tempfile.TemporaryDirectory() as folder:
        run = AdmissionReplay(scenario, folder)
        try:
            (runner or AdmissionReplay.run)(run)
            return run, copy.deepcopy(run.ledger.snapshot())
        finally:
            run.close()


def strip_nonce(records):
    return [{k: v for k, v in record.items() if k != 'nonce'} for record in records]


def replay_classes(test, records):
    """Recompute every ranked class from the admission records before it, as the allocator saw them."""
    for index, record in enumerate(records):
        with test.subTest(job=record['attempt_id']):
            prior = [r for r in records[:index] if r['settings_revision'] == record['settings_revision']]
            test.assertEqual(allocator.choose_class(record['shares'], prior, record['bucket'], 0, CLASSES),
                             record['class'])


class CatalogRefreshReplayTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load_admission('catalog_refresh')
        self.run_, self.snapshot = run_admission(self.scenario)
        self.refresh_at = next(e['t'] for e in self.scenario['events'] if e['kind'] == 'refresh')

    def test_refresh_drops_removed_unsupported_and_uncited_options(self):
        before, after = self.run_.tables[0]['table'], self.run_.tables[-1]['table']
        self.assertNotEqual(before['version'], after['version'])
        self.assertNotIn('refresh_failed', after)
        dropped = {(item['model'], item['effort'], item['reason']) for item in after['dropped']}
        self.assertLessEqual({('alpha-small', 'low', 'model_not_in_catalog'),
                              ('alpha-large', 'high', 'effort_unsupported'),
                              ('gamma-new', 'low', 'evidence_missing')}, dropped)
        models = lambda table: {(s['model'], s['effort'], s['harness']) for row in table['rows'].values() for s in row}
        self.assertEqual(models(after), {('alpha-large', 'max', 'native'), ('alpha-large', 'max', 'api')})
        self.assertIn(('alpha-small', 'low', 'native'), models(before))
        for table in (before, after):  # the harness's route ids match the compiler's
            for strategies in table['rows'].values():
                for s in strategies:
                    self.assertEqual(s['strategy_id'], strategy_id(s['model'], s['effort'], s['harness']))

    def test_later_admissions_never_choose_removed_or_unsupported_options(self):
        later = [e for e in self.run_.log if e['outcome'] == 'admitted' and e['at'] > self.refresh_at]
        self.assertTrue(later)
        live = self.run_.tables[-1]['catalog']
        for entry in later:
            deployment = self.run_.deployments[entry['route']]
            with self.subTest(job=entry['id']):
                self.assertEqual(entry['table_version'], self.run_.tables[-1]['table']['version'])
                self.assertIn(deployment['model'], live)
                self.assertIn(deployment['effort'], live[deployment['model']]['supported_efforts'])
                self.assertNotEqual(deployment['model'], 'gamma-new')  # offered by the collector, never cited
                self.assertIn(strategy_id(deployment['model'], deployment['effort'], deployment['harness']),
                              entry['ranked'])

    def test_admissions_granted_before_the_refresh_settle_normally(self):
        log = self.run_.log
        granted = {e['id']: e for e in log if e['outcome'] == 'admitted' and e['at'] < self.refresh_at}
        settled_after = [e for e in log if e['outcome'] == 'complete' and e['at'] > self.refresh_at and e['id'] in granted]
        retired = [e for e in settled_after
                   if self.run_.deployments[granted[e['id']]['route']]['model'] == 'alpha-small' or
                   self.run_.deployments[granted[e['id']]['route']]['effort'] == 'high']
        self.assertTrue(retired, 'an admission on a now-retired option must still be open at the refresh')
        self.assertEqual({granted[e['id']]['class'] for e in retired}, {'subscription'})  # F5.3: ranked never pays
        holds = self.snapshot['holds']
        for attempt in granted:
            settlement, hold = self.run_.settlements[attempt], holds[attempt]
            with self.subTest(job=attempt):
                self.assertEqual((hold['closed'], hold['state']), (True, 'released'))
                claims = settlement['reserved_claims']
                if settlement['kind'] == 'window':  # subscription: the whole window claim is released once
                    self.assertEqual(settlement['released'], claims)
                else:  # dollars: 80% charged, the rest released, no overrun
                    reserved = claims[budget.ORG + self.run_.org]
                    self.assertEqual((settlement['reserved'], settlement['charged'], settlement['released'],
                                      settlement['overrun']), (reserved, reserved * 4 // 5,
                                                               reserved - reserved * 4 // 5, 0))
                    self.assertEqual(hold['settlement']['charged'], reserved * 4 // 5)
        self.assertEqual(budget.Ledger.held(self.snapshot), {})
        self.assertEqual(self.run_.open, {})

    def test_refresh_neither_frees_nor_resurrects_capacity(self):
        def at_refresh(run):
            for event in run.scenario['events']:
                if event['kind'] == 'refresh':
                    data = run.ledger.snapshot()
                    held, credit = budget.Ledger.held(data), run.ledger.credit_headroom(run.org, event['t'])
                    run.refresh(event['catalog'], event['t'])
                    data = run.ledger.snapshot()
                    self.assertEqual(budget.Ledger.held(data), held)
                    self.assertEqual(run.ledger.credit_headroom(run.org, event['t']), credit)
                    self.assertTrue(held, 'holds must be open across the refresh')
                    self.observed = True
                elif event['kind'] == 'arrive':
                    run.arrive(event['job'], event['t'])
                else:
                    run.complete(event['id'], event['t'])
        self.observed = False
        run, snapshot = run_admission(self.scenario, at_refresh)
        self.assertTrue(self.observed)
        # Settled charges stay counted: headroom never exceeds the issued balance less protection and every charge.
        self.assertEqual(run.charged, 0)  # F5.3: ranked arrivals fund only subscriptions; no dollar is charged
        self.assertGreater(run.checks, 0)
        org = snapshot['organizations'][run.org]
        self.assertLessEqual(budget.Ledger._credit(snapshot, run.org, run.observed_at),
                             run.balance - budget.Ledger._protected(org) - run.charged)

    def test_exact_replay_of_class_and_route_from_admission_records(self):
        again, snapshot = run_admission(self.scenario)
        self.assertEqual(again.log, self.run_.log)
        records = self.snapshot['admissions']
        self.assertEqual(strip_nonce(snapshot['admissions']), strip_nonce(records))
        self.assertEqual(len(records), len([e for e in self.run_.log if e['outcome'] == 'admitted']))
        self.assertEqual({r['class'] for r in records}, {'subscription'})  # F5.3: ranked never pays
        replay_classes(self, records)
        for record in records:
            d = self.run_.deployments[record['route']]
            self.assertEqual(record['route_identity'],
                             routing.route_identity(d['provider'], d['model'], d['effort'], d['harness']))


class DependencyPlanReplayTests(unittest.TestCase):
    """Six jobs: job-1 fans out to 2, 3 and 4; 2 and 3 fan in to 5; 4 and 5 fan in to 6. No count limits the fan-out."""

    def schedule(self, run):
        """Admit ready jobs in id order whenever dependencies allow; finish the earliest-ending open job."""
        jobs = {job['id']: job for job in run.scenario['jobs']}
        done, ends, now = set(), {}, run.scenario['start']
        self.trace = []
        while len(done) < len(jobs):
            for job_id in sorted(jobs):
                job = jobs[job_id]
                if job_id in done or job_id in ends or not set(job['depends_on']) <= done:
                    continue
                payload = {k: v for k, v in job.items() if k not in ('depends_on', 'duration')}
                outcome = run.arrive(payload, now)
                if outcome['status'] == 'admitted':
                    ends[job_id] = now + job['duration']
                    self.trace.append(('admit', job_id, now, sorted(done)))
                else:
                    self.trace.append(('wait', job_id, now, outcome['reason_code']))
            job_id = min(ends, key=lambda j: (ends[j], j))
            now = ends.pop(job_id)
            run.complete(job_id, now)
            done.add(job_id)
            self.trace.append(('done', job_id, now))

    def setUp(self):
        self.scenario = load_admission('dependency_plan')
        self.run_, self.snapshot = run_admission(self.scenario, self.schedule)

    def test_jobs_are_admitted_in_dependency_order(self):
        """Checks the test's own `schedule`: the product does not order a DAG (Workstreams owns readiness). What it
        contributes here is admitting each ready job alone, funded by windows and money; see the fan-out test."""
        deps = {job['id']: set(job['depends_on']) for job in self.scenario['jobs']}
        admitted = [step for step in self.trace if step[0] == 'admit']
        self.assertEqual(sorted(step[1] for step in admitted), sorted(deps))
        for _, job_id, _, done in admitted:
            with self.subTest(job=job_id):
                self.assertLessEqual(deps[job_id], set(done))
        order = [step[1] for step in admitted]
        self.assertEqual(order[0], 'job-1')
        self.assertEqual(order[-1], 'job-6')
        self.assertLess(order.index('job-5'), order.index('job-6'))

    def test_a_fan_out_admits_every_ready_job_together_without_a_count_wait(self):
        """F5.A1 (was: the fan-out waits for a concurrency place): the three ready jobs fit their windows, so all run
        at once; nothing waits."""
        self.assertEqual([step for step in self.trace if step[0] == 'wait'], [])
        peak, current = 0, 0
        for step in self.trace:
            current += {'admit': 1, 'done': -1}.get(step[0], 0)
            peak = max(peak, current)
        self.assertEqual(peak, 3)  # jobs 2, 3 and 4 after job-1

    def test_claims_are_released_on_settlement(self):
        self.assertEqual(self.run_.checks, 12)  # one check per admission and per settlement; F5.A1: no waits
        self.assertEqual(budget.Ledger.held(self.snapshot), {})
        self.assertTrue(all(hold['closed'] for hold in self.snapshot['holds'].values()))
        self.assertEqual(len(self.snapshot['holds']), 6)

    def test_coordinator_profiles(self):
        for job in self.scenario['jobs']:
            profile = routing.ensure_profile({k: v for k, v in job.items() if k not in ('depends_on', 'duration')})
            self.assertEqual(profile['uncertain_fields'], [])
            self.assertEqual({profile['provenance'][f] for f in routing.SEMANTIC_FIELDS}, {'coordinator'})

    def test_a_profile_gap_takes_floors(self):
        job = dict(self.scenario['jobs'][0], profile=dict(review_policy='independent'))
        profile = routing.ensure_profile({k: v for k, v in job.items() if k not in ('depends_on', 'duration')})
        self.assertEqual({profile['provenance'][f] for f in ('complexity_band', 'risk_band')}, {'floor'})

    def test_exact_replay(self):
        first = (self.run_.log, self.trace)
        again, snapshot = run_admission(self.scenario, self.schedule)
        self.assertEqual((again.log, self.trace), first)
        records = self.snapshot['admissions']
        self.assertEqual(strip_nonce(snapshot['admissions']), strip_nonce(records))
        self.assertEqual(len(records), 6)
        replay_classes(self, records)


if __name__ == '__main__':
    unittest.main()
