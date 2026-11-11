"""Funding: seeded property test (I1-I6) and the multi-process race. See test_routing_funding.py for the invariants."""
import multiprocessing
import random
import tempfile
import unittest
from pathlib import Path
from runtime import routing, usage_budget as budget
from tests.fixture_routing_funding import Funded, ITERATIONS, M, NOW, STEPS, assignment, authorize, funded_pool, native, observations, policy, protected, ranking, reserved, route, settings


# ---------------------------------------------------------------------------------------------------------
# Seeded property test
# ---------------------------------------------------------------------------------------------------------
ORGS = ('o1', 'o2')
CAPS = (6, 3)


class Property:
    """One random sequence over 2 organizations x {subscription, api_grant, gateway} with a shared organization.
    Ranked admissions fund only subscription routes (F5.3); dollar holds come from legacy explicit choices."""
    def __init__(self, test, seed):
        self.test, self.rng = test, random.Random(seed)
        self.seed = seed
        self.clock = NOW
        self.ids = 0
        temp = Path(test.temp.name) / ('s%d' % seed)
        self.path = temp / 'ledger.sqlite3'
        identity = budget.initialize(self.path)
        self.ledger = budget.Ledger(self.path, identity)
        self.ledger.set_caps(*CAPS)
        # One sequence in four has no organization at all: funded routes are simply unavailable.
        self.orgs = () if seed % 4 == 0 else ORGS
        if self.orgs:
            self.ledger.enable_organization('o1', policy(allowance_bp=9800, ceiling='custom', amount=40 * M,
                                                         task_limit=15 * M, pilot_limit=50 * M), 0)
            self.ledger.enable_organization('o2', policy(allowance_bp=10000), 0)
            self.ledger.observe('o1', 30 * M, self.clock, issued=30 * M)
            self.ledger.observe('o2', 20 * M, self.clock, issued=20 * M)
        self.pools = dict(sub=dict(windows=[dict(id='5h', limit=1000, used=0, length=18000, reset_at=None, rolling=True,
                                                 fresh=True)]),
                          g1=funded_pool('o1'), g1b=funded_pool('o1', kind='api_prepaid'),
                          gw=funded_pool('o2', kind='gateway'))
        self.kinds = dict(sub='included_subscription', g1='api_grant', g1b='api_prepaid', gw='gateway')

    def close(self):
        self.ledger.close()

    def fresh_id(self, prefix):
        self.ids += 1
        return '%s%d' % (prefix, self.ids)

    def check(self, step):
        data = self.ledger.snapshot()
        where = 'seed %d step %s' % (self.seed, step)
        self.test.assertEqual(sorted(data['organizations']), sorted(self.orgs), 'routing created an organization')
        for name in self.orgs:
            org = data['organizations'][name]
            pol = org['policy']
            r = reserved(data, name)
            self.test.assertGreaterEqual(org['balance'] - protected(org) - r, 0, 'I1 ' + where)
            if pol['pilot_limit'] is not None:
                self.test.assertLessEqual(r, pol['pilot_limit'], 'I2 pilot ' + where)
            if pol['task_limit'] is not None:
                for task in {run['task'] for run in data['runs'].values()} | {h['task'] for h in data['holds'].values()}:
                    self.test.assertLessEqual(reserved(data, name, task), pol['task_limit'], 'I2 task ' + where)
            if pol['ceiling'] == 'custom':
                self.test.assertLessEqual(reserved(data, name, cycle=pol['cycle']), pol['amount'], 'I2 cycle ' + where)
        for attempt, hold in data['holds'].items():
            self.test.assertIs(type(hold['amount']), int, 'I3 ' + where)
            if hold['amount']:
                self.test.assertIn(hold['organization'], data['organizations'], 'I3 ' + where)
            if hold['closed']:
                self.test.assertNotIn(attempt, budget.Ledger.open_holds(data), 'I6 ' + where)
            if hold['state'] == 'uncertain':
                self.test.assertFalse(hold['closed'], 'I6 ' + where)
        attempts = {i for i, run in data['runs'].items() if not run['closed']} | \
            {i for i, hold in data['holds'].items() if not hold['closed']}
        accounts = budget.Ledger.open_accounts(data)
        self.test.assertEqual(len(accounts), len(attempts), 'I5 ' + where)
        self.test.assertLessEqual(len(accounts), CAPS[0], 'I5 ' + where)
        for account in set(accounts):
            self.test.assertLessEqual(accounts.count(account), CAPS[1], 'I5 ' + where)

    def step(self, index):
        op = self.rng.choice(('admit', 'admit', 'admit', 'begin', 'begin', 'top_up', 'authorize', 'authorize', 'convert', 'convert',
                              'dispatched', 'uncertain', 'close', 'run_dispatch', 'complete', 'observe', 'toggle'))
        self.clock += 1
        before = self.ledger.snapshot()
        try:
            getattr(self, 'op_' + op)(before)
        except budget.BudgetError:
            self.test.assertEqual(self.ledger.snapshot(), before, 'refused %s changed state, seed %d' % (op, self.seed))
        self.check('%d:%s' % (index, op))

    def amount(self):
        return self.rng.choice((self.rng.randrange(1, 3 * M), self.rng.randrange(3 * M, 12 * M), 0))

    def op_admit(self, before):
        chosen = self.rng.sample(sorted(self.pools), self.rng.randint(1, 4))
        routes = []
        for pool in chosen:
            demand = {'5h': self.rng.randrange(1, 400)} if pool == 'sub' else {'credit': self.amount()}
            account = self.rng.choice(('A', 'B', 'C'))
            routes.append(route('r-' + pool, pool, self.kinds[pool], account, demand))
        task = self.rng.choice(('t1', 't2', 't3'))
        cfg = settings(caps=dict(total=CAPS[0], per_account=CAPS[1]))
        paid = [r for r in routes if r['billing_class'] != 'included_subscription']
        # F5.3: a ranked decision funds only subscription routes; a paid route is reached through the legacy explicit
        # choice (synthetic coverage of the dollar invariants, not a paid production path).
        legacy = bool(paid) and self.rng.random() < 0.75
        request = dict(assignment(self.fresh_id('h'), task), choice=dict(
            {key: paid[0][key] for key in routing.ROUTE_IDENTITY}, funding='api')) if legacy else \
            assignment(self.fresh_id('h'), task)
        result = routing.admit(request, ranking() if legacy else ranking(*[r['strategy_id'] for r in routes]),
                               cfg, observations(routes, self.pools, self.clock), ledger=self.ledger)
        self.test.assertIn(result['status'], ('admitted', 'pending'))
        if result['status'] == 'pending':
            self.test.assertEqual(self.ledger.snapshot(), before)
        if not self.orgs or not legacy:
            self.test.assertTrue(result['status'] == 'pending' or result['class'] == 'subscription')

    def op_begin(self, before):
        task = self.rng.choice(('t1', 't2', 't3'))
        run = self.fresh_id('r')
        self.ledger.begin(self.rng.choice(ORGS), task, run, 'packet', self.amount(), 1, self.clock, native(task))

    def open_runs(self, before):
        return sorted(i for i, run in before['runs'].items() if not run['closed'])

    def op_top_up(self, before):
        runs = self.open_runs(before)
        if runs:
            self.ledger.top_up(self.rng.choice(runs), self.amount(), 1, self.clock)

    def op_authorize(self, before):
        funded = sorted(i for i, h in before['holds'].items() if not h['closed'] and h['state'] == 'admitted'
                        and h['amount'])
        attempt = self.rng.choice(funded) if funded else self.pick_hold(before, ('admitted',))
        if attempt:
            authorize(self.ledger, attempt, self.clock)

    def op_convert(self, before):
        holds = sorted(i for i, h in before['holds'].items() if not h['closed'] and h['amount'])
        authorized = [i for i in holds if 'dispatch' in before['holds'][i] and before['holds'][i]['state'] == 'dispatched']
        if authorized and self.rng.random() < 0.8:  # S3.4: only these convert; the rest exercise the refusal
            holds = authorized
        if not holds:
            return
        attempt = self.rng.choice(holds)
        hold = before['holds'][attempt]
        organization = hold['organization'] if self.rng.random() < 0.8 else self.rng.choice(ORGS)
        estimate = self.rng.choice((hold['amount'], hold['amount'] // 2, hold['amount'] + 1))
        run = self.ledger.convert_hold(attempt, organization, 'packet', estimate, 1, self.clock, native(hold['task']))
        after = self.ledger.snapshot()
        self.test.assertEqual(run['organization'], hold['organization'], 'I4 seed %d' % self.seed)
        self.test.assertEqual(run['account'], hold['account'], 'I5 seed %d' % self.seed)
        for name in ORGS:
            self.test.assertEqual(reserved(after, name), reserved(before, name), 'I4 seed %d' % self.seed)

    def pick_hold(self, before, states):
        holds = sorted(i for i, h in before['holds'].items() if not h['closed'] and h['state'] in states)
        return self.rng.choice(holds) if holds else None

    def op_dispatched(self, before):
        attempt = self.pick_hold(before, ('admitted',))
        if attempt:
            self.ledger.mark_dispatched(attempt)

    def op_uncertain(self, before):
        attempt = self.pick_hold(before, ('admitted', 'dispatched'))
        if attempt:
            self.ledger.mark_uncertain(attempt)

    def op_close(self, before):
        attempt = self.pick_hold(before, ('admitted', 'dispatched', 'uncertain'))
        if attempt:
            self.ledger.close_attempt(attempt, self.rng.choice(budget.HOLD_OUTCOMES))

    def op_run_dispatch(self, before):
        runs = [i for i in self.open_runs(before) if before['runs'][i]['calls']['count']['state'] == 'reserved']
        if runs:
            self.ledger.dispatch(self.rng.choice(runs), 'count', 1, self.clock)

    def op_complete(self, before):
        runs = [i for i in self.open_runs(before) if before['runs'][i]['calls']['count']['state'] == 'dispatching']
        if runs:
            run = self.rng.choice(runs)
            self.ledger.complete(run, 'count', self.rng.randrange(0, before['runs'][run]['calls']['count']['maximum'] + 1))

    def op_toggle(self, before):
        """The user disables or re-enables an organization; open claims stay counted either way."""
        if self.orgs:
            name = self.rng.choice(self.orgs)
            org = before['organizations'][name]
            if org.get('disabled'):
                self.ledger.enable_organization(name, org['policy'], org['revision'])
            else:
                self.ledger.disable_organization(name)

    def op_observe(self, before):
        """External spend lowers B, but a well-behaved provider never reports below what is reserved."""
        if not self.orgs:
            return
        name = self.rng.choice(self.orgs)
        org = before['organizations'][name]
        floor = protected(org) + reserved(before, name)
        if org['balance'] > floor:
            self.ledger.observe(name, self.rng.randrange(floor, org['balance'] + 1), self.clock)


class PropertyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_invariants_hold_after_every_step_of_random_sequences(self):
        admitted = converted = funded = 0
        for seed in range(ITERATIONS):
            run = Property(self, seed)
            try:
                for index in range(STEPS):
                    run.step(index)
                state = run.ledger.snapshot()
                admitted += len(state['admissions'])
                funded += sum(1 for record in state['admissions'] if record['class'] == 'api')
                converted += sum(1 for attempt in state['runs'] if attempt in state['holds'])
            finally:
                run.close()
        self.assertGreater(admitted, ITERATIONS)  # the sequences exercise admission, not only refusals
        self.assertGreater(funded, ITERATIONS // 4)
        self.assertGreater(converted, ITERATIONS // 10)


# ---------------------------------------------------------------------------------------------------------
# Multi-process race
# ---------------------------------------------------------------------------------------------------------
def racer(path, identity, index, queue):
    try:
        with budget.Ledger(path, identity) as ledger:
            if index % 2:
                routes = [route('r', 'g', 'api_grant', 'acct%d' % index, {'credit': 3 * M})]
                result = routing.admit(assignment('p%d' % index), ranking('s-r'), settings(),
                                       observations(routes, dict(g=funded_pool('org'))), ledger=ledger)
                queue.put(result['status'])
            else:
                ledger.begin('org', 'b%d' % index, 'b%d' % index, 'packet', 3_600_000, 1, NOW, native('b%d' % index))
                queue.put('admitted')
    except budget.BudgetError as error:
        queue.put(str(error))
    except Exception as error:  # noqa: BLE001 - reported to the parent
        queue.put('raised %r' % error)


class RaceTests(Funded, unittest.TestCase):
    def test_eight_processes_mixing_admit_and_begin_never_overdraw(self):
        self.fund(balance=15 * M, allowance_bp=10000)  # four 3.6M claims fit, the other four do not
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        processes = [ctx.Process(target=racer, args=(self.path, self.identity, i, queue)) for i in range(8)]
        for process in processes: process.start()
        results = [queue.get(timeout=60) for _ in processes]
        for process in processes: process.join(60)
        self.assertFalse([r for r in results if r.startswith('raised')], results)
        self.assertEqual(results.count('admitted'), 4, results)
        data = self.ledger.snapshot()
        self.assertEqual(reserved(data, 'org'), 4 * 3_600_000)
        self.assertGreaterEqual(15 * M - reserved(data, 'org'), 0)


if __name__ == '__main__':
    unittest.main()
