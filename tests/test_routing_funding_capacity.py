"""Funding: capacity (I7), soft limits and incomplete bounds. See test_routing_funding.py for the invariants."""
from contextlib import closing
import copy
import json
import sqlite3
import unittest
from runtime import ledger_versions, routing, subscription_reconcile, usage_budget as budget
from tests.fixture_routing_funding import Funded, M, NOW, assignment, authorize, funded_pool, native, observations, policy, route, settings, window


# ---------------------------------------------------------------------------------------------------------
# Capacity (I7)
# ---------------------------------------------------------------------------------------------------------
def long(tag):
    return (tag + 'x' * 256)[:256]


def unique(index, value):
    return ('%05d' % index + value)[:256]


def largest_new_record(ledger):
    """F5.4: the actual record the routing builder makes for the largest shipped-path subscription admission (ten
    Go candidates with three windows each, maximal identities and binding), as `Ledger.admit` would store it."""
    from tests import test_routing_admission as admission
    request, ranked, snapshot, job = admission.largest_replay()
    with ledger.transaction(write=False) as data:
        decided = routing._decide(data, request, ranked, admission.settings(), snapshot, job)
    return dict(decided['record'], nonce='0' * 32, authority_required=True)


def padded(item, size):
    """`item` with a `pad` field making its compact JSON exactly `size` characters."""
    item = dict(item, pad='')
    item['pad'] = 'p' * (size - len(json.dumps(item, separators=(',', ':'))))
    return item


class CapacityTests(Funded, unittest.TestCase):
    def long_admission(self, attempt, windows, fill='x'):
        """A real admission with 256-character identifiers everywhere and `windows` demanded windows; a non-ASCII
        `fill` makes every identifier but the organization's serialize as six-character escapes."""
        text = lambda tag: (tag + fill * 256)[:256]  # noqa: E731
        organization = long('org')
        if organization not in self.ledger.snapshot()['organizations']:
            self.ledger.configure(organization, policy(cycle=long('cycle')), 0)
            self.ledger.observe(organization, 10**12, NOW, issued=10**12)
        ids = [text('w%d' % i) for i in range(windows)]
        pools = {text('pool'): dict(billing_class='api_grant', windows=[window(i, organization=organization)
                                                                        for i in ids])}
        api = route(text('r'), text('pool'), 'api_grant', text('acct%s' % attempt), {i: M for i in ids})
        api.update(strategy_id=text('sid'), model=text('m'), harness=text('h'), provider=text('p'))
        api['credential']['ref'] = text('ref')
        obs = dict(observations([api], pools), snapshot_id=text('snap'))
        rank = dict(strategy_ids=[text('sid')], reason_codes=[text('code')] * routing.MAX_REASONS,
                    table_version=text('t'), profile_digest=text('d'))
        choice = dict({key: api[key] for key in routing.ROUTE_IDENTITY}, funding='api')  # F5.3: the legacy choice
        return routing.admit(dict(assignment(text(attempt), text('task')), choice=choice), rank, settings(), obs,
                             ledger=self.ledger)

    def test_an_oversized_admission_is_pending_and_writes_nothing(self):
        before = self.ledger.snapshot()
        # F5.4: the 10,752 cap admits the ASCII form of this admission (9,016 characters); escaped text exceeds it.
        result = self.long_admission('big', routing.MAX_WINDOWS, fill='\u00e9')
        self.assertEqual((result['status'], result['reason_code']), ('pending', 'record_too_large'))
        self.assertEqual(self.ledger.snapshot()['holds'], before['holds'])
        self.assertEqual(self.long_admission('one', 1)['status'], 'admitted')  # long ids alone still fit

    def test_a_full_admissions_table_never_blocks_and_keeps_open_attempts(self):
        record = lambda attempt: dict(attempt_id=attempt, task='t', bucket=[], shares={})  # noqa: E731
        decide = lambda attempt: (lambda data: dict(record=record(attempt), vector={}, account='a%s' % attempt))  # noqa: E731
        self.ledger.set_caps(budget.MAX_HOLDS, budget.MAX_HOLDS)
        self.ledger.admit('open', decide('open'))  # the oldest record belongs to an attempt that stays open
        for i in range(budget.MAX_ADMISSIONS + 3):
            self.ledger.admit('c%d' % i, decide('c%d' % i))
            self.ledger.close_attempt('c%d' % i, 'not_dispatched')
        data = self.ledger.snapshot()
        self.assertEqual(len(data['admissions']), budget.MAX_ADMISSIONS)
        self.assertIsNotNone(budget.Ledger.admission(data, 'open'))  # closed attempts' records go first
        self.assertIsNone(budget.Ledger.admission(data, 'c0'))
        self.assertTrue(self.ledger.admit('open', decide('open'))['duplicate'])

    def test_the_worst_case_ledger_fits_under_the_hard_limit_with_margin(self):
        """Every table at its bound, every entry at its size bound, 256-character identifiers, real native keys,
        every native task finished and every call carrying a maximal `incomplete` record."""
        organization = long('org')
        self.ledger.configure(organization, policy(cycle=long('cycle'), pilot_limit=budget.MAX_INTEGER,
                                                   task_limit=budget.MAX_INTEGER), 0)
        self.ledger.observe(organization, budget.MAX_INTEGER, NOW, issued=budget.MAX_INTEGER,
                            lots=[dict(id=unique(i, long('lot')), balance=budget.MAX_INTEGER,
                                       expiry=budget.MAX_INTEGER, verified=True) for i in range(64)])
        run_id = long('run')
        gate = lambda data, now: dict(pin=long('pin'), maximum=budget.MAX_INTEGER)  # noqa: E731
        self.ledger.begin(organization, long('task'), run_id, long('digest'), M, 1, NOW,
                          dict(account=long('na'), task=long('task'), windows=[]), provision=M, live_gate=gate)
        self.ledger.dispatch(run_id, 'count', 1, NOW, live_gate=gate)
        self.ledger.complete(run_id, 'count', 1)
        self.ledger.reserve_generation(run_id, long('digest2'), M, 1, NOW, live_gate=gate)
        data = self.ledger.snapshot()
        run = data['runs'][run_id]
        run.update(consent=dict(scope=long('scope'), revision=budget.MAX_INTEGER,
                                qualification_revision=budget.MAX_INTEGER),
                   transferred_from=dict(run=long('parent'), amount=budget.MAX_INTEGER), account=long('acct'),
                   revision=budget.MAX_INTEGER)
        incomplete = padded(dict(reason='unknown'), budget.MAX_INCOMPLETE_BYTES)
        for call in run['calls'].values():
            call.update(maximum=budget.MAX_INTEGER, cost=budget.MAX_INTEGER, provision=budget.MAX_INTEGER,
                        incomplete=incomplete)
        # An over-bound marker (S3.6a) on at most one call per run: a count call that is over-bound never gets a
        # generation call, and a generation call follows only a completed count. `cost` stays at its bound too.
        run['calls']['count']['over_bound'] = budget.MAX_INTEGER
        data['organizations'][organization]['revision'] = budget.MAX_INTEGER
        # S3.6 settlement: per-cycle settled charges, per-task charges and the settlement each closed hold carries.
        data['organizations'][organization]['settled'] = dict(total=budget.MAX_INTEGER, cycles={
            unique(i, long('cycle')): budget.MAX_INTEGER for i in range(budget.MAX_SETTLED_CYCLES)})
        data['settled_tasks'] = {'%064x' % i: budget.MAX_INTEGER for i in range(budget.MAX_SETTLED_TASKS)}
        data['organizations'][organization]['legacy_block'] = True  # the only stored block source (v4)
        # F5.4: every retained slot holds the actual largest new record shape, snapshot detail included (the worst
        # case lasts its 24 hours), padded to the admission bound it must fit.
        largest = largest_new_record(self.ledger)
        self.assertLessEqual(len(json.dumps(largest, separators=(',', ':'))), budget.MAX_ADMISSION_BYTES)
        data['admissions'] = [padded(dict(largest, attempt_id=unique(i, long('a'))), budget.MAX_ADMISSION_BYTES)
                              for i in range(budget.MAX_ADMISSIONS)]
        # An admitted hold at its bound plus the fixed fields dispatch, uncertainty and proof add later (S3.4).
        data['holds'] = {unique(i, long('h')): padded(dict(closed=False), budget.MAX_HOLD_BYTES + budget.DISPATCH_HOLD_BYTES
                                                     + budget.SETTLEMENT_HOLD_BYTES)
                         for i in range(budget.MAX_HOLDS)}
        data['seen_attempts'] = [unique(i, long('s')) for i in range(budget.MAX_SEEN)]
        runs, native = {}, {}
        for i in range(budget.MAX_RUNS):  # one native task per run, keyed exactly as `_native` keys it
            account = unique(i, long('na'))
            key = json.dumps([account, long('task')])
            native[key] = dict(account=account, task=long('task'), latest_at=budget.MAX_INTEGER)
            runs[unique(i, run_id)] = dict(copy.deepcopy(run), native=key)
        data['runs'], data['native'] = runs, native
        data['finished_native'] = list(native)  # bounded by native entries, which are bounded by runs
        data['caps'] = dict(total=budget.MAX_INTEGER, per_account=budget.MAX_INTEGER)
        # S3.6c: the window debits abandoned subscription holds leave behind, every key at its bound.
        data[subscription_reconcile.DEBITS] = {
            ('win:' + unique(i, long('p')) + '/' + long('w')): dict(units=budget.MAX_INTEGER, at=budget.MAX_INTEGER)
            for i in range(subscription_reconcile.MAX_WINDOW_DEBITS)}
        # F5.4 (approved caps): with every admission slot at the new 10,752 bound the body written here is
        # 11,765,486 characters, and 11,784,912 once a non-growing write re-serializes it (below 90% of 13,100,000).
        self.assertEqual(data['version'], ledger_versions.STATE_VERSION)
        raw = json.dumps(data, allow_nan=False, separators=(',', ':'))
        sizes = {key: len(json.dumps(value, separators=(',', ':'))) for key, value in data.items()}
        self.assertLessEqual(len(raw), budget.MAX_RECORD * 0.9, sizes)  # at least 10% margin
        self.assertGreater(len(raw), budget.SOFT_RECORD)  # beyond the soft limit: only settlement continues
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('UPDATE ledger SET body=?', (raw,)); db.commit()
        self.ledger.set_caps(5, 2)  # a full ledger still reads and commits a non-growing write
        with closing(sqlite3.connect(self.path)) as db:
            stored = len(db.execute('SELECT body FROM ledger').fetchone()[0])
        self.assertLessEqual(stored, budget.MAX_RECORD * 0.9)  # the body that write stored keeps the margin too
        self.assertGreater(stored, budget.SOFT_RECORD)
        with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
            self.ledger.configure(long('another'), policy(), 0)

    def test_claim_and_hold_table_bounds_refuse_new_admissions(self):
        decide = lambda attempt, vector: (lambda data: dict(  # noqa: E731
            record=dict(attempt_id=attempt, task='t', bucket=[], shares={}), vector=vector, account=attempt))
        too_many = {'win:p/w%d' % i: 1 for i in range(budget.MAX_CLAIMS + 1)}
        with self.assertRaisesRegex(budget.BudgetError, 'record_too_large'):
            self.ledger.admit('wide', decide('wide', too_many))
        self.ledger.set_caps(budget.MAX_HOLDS * 2, budget.MAX_HOLDS * 2)
        for i in range(budget.MAX_HOLDS):
            self.ledger.admit('h%d' % i, decide('h%d' % i, {}))
        with self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
            self.ledger.admit('one-more', decide('one-more', {}))  # open holds are never pruned
        self.assertEqual(len(self.ledger.snapshot()['holds']), budget.MAX_HOLDS)


class SoftLimitTests(Funded, unittest.TestCase):
    """Above SOFT_RECORD nothing adds exposure; everything that settles or reduces it still commits."""
    def setUp(self):
        super().setUp()
        self.fund(balance=100 * M)
        pools = dict(g=funded_pool('org'))
        self.routes = [route('r', 'g', 'api_grant', 'acct', {'credit': M})]
        self.pools = pools
        for attempt in ('h1', 'h2', 'h3', 'h4'):
            self.assertEqual(self.admit(attempt, self.routes, pools)['status'], 'admitted')
        self.ledger.mark_dispatched('h2')
        for run in ('r1', 'r2', 'r3'):
            self.begin(run, M)
            self.ledger.dispatch(run, 'count', 1, NOW)
        self.inflate()

    def inflate(self):
        """Grow the record just past the soft limit with additional configured organizations."""
        with closing(sqlite3.connect(self.path)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            org = data['organizations']['org']
            filler = len(json.dumps({long('filler'): org}, separators=(',', ':')))
            count = (budget.SOFT_RECORD - len(json.dumps(data, separators=(',', ':')))) // filler
            for i in range(count + 50):  # a little past the soft limit
                data['organizations'][unique(i, long('filler'))] = copy.deepcopy(org)
            raw = json.dumps(data, separators=(',', ':'))
            db.execute('UPDATE ledger SET body=?', (raw,)); db.commit()
        self.assertGreater(len(raw), budget.SOFT_RECORD)
        self.assertLess(len(raw), budget.MAX_RECORD)

    def test_nothing_that_adds_exposure_commits(self):
        before = self.ledger.snapshot()
        result = self.admit('new', self.routes, self.pools)
        self.assertEqual((result['status'], result['reason_code']), ('pending', 'ledger_capacity_reached'))
        refused = dict(begin=lambda: self.begin('r9', M), top_up=lambda: self.ledger.top_up('r1', 1, 1, NOW),
                       configure=lambda: self.ledger.configure('new-org', policy(), 0),
                       enable=lambda: self.ledger.enable_organization(budget.PERSONAL, policy(), 0))
        for label, call in refused.items():
            with self.subTest(label), self.assertRaisesRegex(budget.BudgetError, 'ledger_capacity_reached'):
                call()
        self.assertEqual(self.ledger.snapshot(), before)

    def test_settlement_always_commits(self):
        self.ledger.complete('r1', 'count', M // 2)
        self.ledger.uncertain('r2', 'count', incomplete=dict(reason='unknown', response_id='resp_x'))
        self.ledger.close_run('r1')
        self.ledger.reject_unsent('r3', 'count')
        self.ledger.close_attempt('h1', 'not_dispatched')
        self.ledger.close_attempt('h2', 'uncertain')
        self.ledger.mark_uncertain('h3')
        authorize(self.ledger, 'h4')
        self.ledger.convert_hold('h4', 'org', 'packet', M, 1, NOW, native('h4'))
        self.ledger.dispatch('h4', 'count', 1, NOW)
        self.ledger.complete('h4', 'count', M)
        self.ledger.observe('org', 90 * M, NOW + 1)
        self.ledger.disable_organization('org')
        data = self.ledger.snapshot()
        self.assertEqual(data['runs']['r2']['calls']['count']['state'], 'unresolved')
        self.assertEqual([data['holds'][h]['state'] for h in ('h1', 'h2', 'h3', 'h4')],
                         ['released', 'uncertain', 'uncertain', 'converted'])
        self.assertNotIn('dispatching', [c['state'] for r in data['runs'].values() for c in r['calls'].values()])


class IncompleteBoundTests(Funded, unittest.TestCase):
    def test_an_oversized_incomplete_record_is_replaced_and_never_blocks_uncertainty(self):
        self.fund(balance=100 * M)
        self.begin('r', M)
        self.ledger.dispatch('r', 'count', 1, NOW)
        self.ledger.uncertain('r', 'count', incomplete=dict(reason='x' * (budget.MAX_INCOMPLETE_BYTES * 2)))
        call = self.ledger.snapshot()['runs']['r']['calls']['count']
        self.assertEqual((call['state'], call['incomplete']), ('unresolved', dict(omitted='too_large')))


if __name__ == '__main__':
    unittest.main()
