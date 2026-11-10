"""S3.6a: admission block reasons, project pins and versions. See test_routing_accounting.py."""
from contextlib import closing
import json
import random
import sqlite3
import unittest
from unittest import mock
from runtime import ledger_versions, routing, usage_budget as budget
from scripts import routing as command
from tests.test_routing_command import M, now, observations, prepare
from tests.test_routing_dispatch import record
from tests import fixture_routing_accounting as fixture
from tests.fixture_routing_accounting import complete, settle, usage


class BlockReasonTests(fixture.Accounting):
    """Round 3: the admission block is DERIVED from its sources (pending overruns, over-bound S1 calls, a migrated
    legacy block) and never stored; resolving one source can never clear another."""
    def overrun(self, attempt, event_id):
        result = self.call(complete(attempt, event_id, usage=usage(9 * M)))
        self.assertEqual(result['outcome'], 'overrun', result)

    def resolve(self, attempt, decision, event_id):
        return self.call(dict(v=1, op='resolve_overrun', attempt_id=attempt, decision=decision, event_id=event_id))

    def over_bound(self, run):
        with self.assertRaisesRegex(budget.BudgetError, 'cost_exceeds_bound'):
            self.finish_run(run, 2 * M)

    def test_b1_overrun_then_over_bound_then_resolve_stays_blocked(self):
        self.funded('a1')
        self.start_run('s1')
        self.overrun('a1', 'c1')
        self.over_bound('s1')
        self.assertEqual(self.reasons(), ['over_bound', 'overrun'])
        self.resolve('a1', 'dismiss', 'r1')
        self.assertEqual(self.reasons(), ['over_bound'])
        self.assertTrue(self.admission_refused())
        self.assertEqual(self.admit('a2')['state'], 'pending')

    def test_b1_over_bound_then_overrun_then_resolve_stays_blocked(self):
        self.funded('a1')
        self.start_run('s1')
        self.over_bound('s1')
        self.overrun('a1', 'c1')
        self.resolve('a1', 'confirm', 'r1')
        self.assertEqual(self.reasons(), ['over_bound'])
        self.assertTrue(self.admission_refused())

    def test_case_c_a_later_lower_report_never_settles_an_over_bound_call(self):
        self.start_run('s1')
        self.over_bound('s1')
        for actual in (M // 2, M, 0):
            with self.assertRaisesRegex(budget.BudgetError, 'over_bound_unresolved'):
                self.finish_run('s1', actual)
        call = self.snapshot()['runs']['s1']['calls']['count']
        self.assertEqual((call['state'], call['over_bound'], call['cost']), ('unresolved', 2 * M, None))
        self.assertEqual(self.reasons(), ['over_bound'])

    def test_over_bound_is_sticky_against_every_client_event(self):
        self.start_run('s1')
        self.over_bound('s1')
        with self.open_ledger() as ledger:
            for attempt in (lambda: ledger.close_run('s1'), lambda: ledger.reject_unsent('s1', 'count'),
                            lambda: ledger.uncertain('s1', 'count'), lambda: ledger.complete('s1', 'count', 1)):
                with self.assertRaises(budget.BudgetError):
                    attempt()
        for kind in ('complete', 'not_dispatched', 'uncertain', 'dispatched'):
            self.assertEqual(self.call(record('s1', kind))['state'], 'blocked')
        self.assertEqual(self.reasons(), ['over_bound'])

    def test_resolve_overrun_changes_only_that_holds_overrun_state(self):
        for attempt in ('a1', 'a2'):
            self.funded(attempt)
        self.overrun('a1', 'c1')
        self.overrun('a2', 'c2')
        before = self.snapshot()
        self.resolve('a1', 'dismiss', 'r1')
        after = self.snapshot()
        before['holds']['a1']['settlement']['overrun_state'] = 'dismissed'
        self.assertEqual(after, before)  # nothing else moved: no flag, no other hold, no organization field
        self.resolve('a2', 'confirm', 'r2')
        confirmed = self.snapshot()
        self.assertEqual({k: v for k, v in confirmed['organizations']['personal'].items() if k != 'settled'},
                         {k: v for k, v in after['organizations']['personal'].items() if k != 'settled'})
        self.assertEqual(confirmed['holds']['a1'], after['holds']['a1'])

    def test_no_code_path_stores_a_block_flag(self):
        self.funded('a1')
        self.start_run('s1')
        self.overrun('a1', 'c1')
        self.over_bound('s1')
        org = self.snapshot()['organizations']['personal']
        self.assertNotIn('blocked', org)
        self.assertNotIn('overrun_block', org)

    def test_status_lists_blocked_organizations_and_pending_ids(self):
        self.funded('a1')
        self.funded('u1')
        self.start_run('s1')
        self.overrun('a1', 'c1')
        self.over_bound('s1')
        self.call(complete('u1', 'cu'))
        ledger = self.call(dict(v=1, op='status'))['ledger']
        self.assertEqual(ledger['blocked_organizations'], [dict(id='personal', reasons=dict(
            over_bound=dict(ids=['s1'], more=0), overrun=dict(ids=['a1'], more=0)))])
        self.assertEqual(ledger['cost_unknown'], dict(ids=['u1'], more=0))
        self.assertEqual(ledger['uncertain_dollar'], dict(ids=[], more=0))
        self.assertIs(ledger['overrun_backlog'], False)
        with self.open_ledger() as ledger, ledger.transaction() as data:
            for i in range(18):
                data['holds']['copy%02d' % i] = dict(data['holds']['a1'])
            data['organizations']['personal']['legacy_block'] = True
        listed = self.call(dict(v=1, op='status'))['ledger']['blocked_organizations'][0]['reasons']
        self.assertEqual((len(listed['overrun']['ids']), listed['overrun']['more']), (16, 3))
        self.assertEqual(listed['legacy'], dict(ids=[], more=0))

    def test_status_lists_uncertain_dollar_holds_until_s36b(self):
        """Round 4 S1: an uncertain dollar hold has no way out before S3.6b, so `status` names it."""
        with self.open_ledger() as ledger:
            ledger.set_caps(32, 32)
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=32, per_account=32))))
        for i in range(18):
            self.funded('d%02d' % i)
            self.call(record('d%02d' % i, 'uncertain'))
        self.admit_sub('s1')
        self.send('s1', sub=True)
        self.call(record('s1', 'uncertain'))  # a subscription hold is not listed
        listed = self.call(dict(v=1, op='status'))['ledger']['uncertain_dollar']
        self.assertEqual(listed, dict(ids=['d%02d' % i for i in range(16)], more=2))
        self.assertEqual(self.call(complete('d00', 'c1', usage=usage(M))),
                         dict(v=1, state='blocked', reason='reconciliation_required'))

    def test_a_hold_table_full_of_pending_overruns_is_an_overrun_backlog(self):
        self.funded('a1')
        self.overrun('a1', 'c1')
        with self.open_ledger() as ledger, ledger.transaction() as data:
            for i in range(budget.MAX_HOLDS - len(data['holds'])):
                data['holds']['copy%03d' % i] = dict(data['holds']['a1'])
        result = self.call(prepare('s1', observations=observations(account='acct-s1'),
                                   constraints=dict(allowed_strategy_ids=['s-sub'])))
        self.assertEqual((result['state'], result['reason']), ('pending', 'overrun_backlog'))
        self.assertIs(self.call(dict(v=1, op='status'))['ledger']['overrun_backlog'], True)

    def test_close_attempt_complete_refuses_a_dollar_hold(self):
        self.funded('a1')
        self.admit_sub('s1')
        self.send('s1', sub=True)
        with self.open_ledger() as ledger:
            with self.assertRaisesRegex(budget.BudgetError, 'settlement_required'):
                ledger.close_attempt('a1', 'complete')
            ledger.close_attempt('s1', 'complete')  # a subscription hold is unchanged
        self.assertEqual((self.hold('a1')['closed'], self.hold('s1')['closed']), (False, True))


class BlockPropertyTests(fixture.Accounting):
    """Random interleavings over two organizations: admission is refused iff the derived reason set is non-empty,
    in any order, and one organization's events never change the other's reasons."""
    def setUp(self):
        super().setUp()
        with self.open_ledger() as ledger:
            ledger.enable_organization('other', self.POLICY, 0)
            ledger.observe('other', self.BALANCE, now(), issued=self.BALANCE)
            ledger.set_caps(64, 64)  # in-flight holds and runs of both organizations stay open throughout

    def hold(self, ledger, attempt, organization):
        revision = ledger.snapshot()['organizations'][organization]['revision']
        moment = now()
        ledger.admit(attempt, lambda data: dict(record=dict(attempt_id=attempt, task='t-' + attempt, bucket=[],
                                                            shares={}), vector={'org:' + organization: M},
                                                account='acct-' + attempt, now=moment, revision=revision))
        ledger.mark_dispatched(attempt)

    def test_random_interleavings_keep_the_block_derived(self):
        rng = random.Random(3603)
        orgs = ('personal', 'other')
        holds = {org: [] for org in orgs}
        runs = {org: [] for org in orgs}
        with self.open_ledger() as ledger:
            for org in orgs:
                for i in range(5):
                    self.hold(ledger, '%s-h%d' % (org, i), org)
                    holds[org].append('%s-h%d' % (org, i))
        for org in orgs:
            for i in range(3):
                self.start_run('%s-r%d' % (org, i), org)
                runs[org].append('%s-r%d' % (org, i))
        expected = {org: dict(overrun=set(), over_bound=set(), legacy=False) for org in orgs}
        seen = set()
        for step in range(120):
            org = rng.choice(orgs)
            action = rng.choice(['overrun', 'over_bound', 'in_bound', 'legacy', 'confirm', 'dismiss', 'admit'])
            with self.open_ledger() as ledger:
                if action == 'overrun' and holds[org]:
                    attempt = holds[org].pop()
                    routing.record_event(ledger, dict(event_id='e%d' % step, attempt_id=attempt, kind='complete',
                                                      usage=usage(3 * M)), now())
                    expected[org]['overrun'].add(attempt)
                elif action == 'over_bound' and runs[org]:
                    run = runs[org].pop()
                    with self.assertRaisesRegex(budget.BudgetError, 'cost_exceeds_bound'):
                        ledger.complete(run, 'count', 2 * M)
                    expected[org]['over_bound'].add(run)
                elif action == 'in_bound' and expected[org]['over_bound']:
                    with self.assertRaisesRegex(budget.BudgetError, 'over_bound_unresolved'):
                        ledger.complete(sorted(expected[org]['over_bound'])[0], 'count', 1)
                elif action == 'legacy' and not expected[org]['legacy'] and rng.random() < 0.2:
                    with ledger.transaction() as data:
                        data['organizations'][org]['legacy_block'] = True
                    expected[org]['legacy'] = True
                elif action in ('confirm', 'dismiss') and expected[org]['overrun']:
                    attempt = sorted(expected[org]['overrun'])[0]
                    result = routing.record_event(ledger, routing.overrun_event(dict(
                        event_id='r%d' % step, attempt_id=attempt, decision=action)), now())
                    self.assertEqual(result['state'], 'recorded', result)
                    expected[org]['overrun'].discard(attempt)
            data = self.snapshot()
            for name in orgs:
                seen |= budget.Ledger.block_reasons(data, name)
                want = {reason for reason, present in (('overrun', expected[name]['overrun']),
                                                       ('over_bound', expected[name]['over_bound']),
                                                       ('legacy', expected[name]['legacy'])) if present}
                self.assertEqual(budget.Ledger.block_reasons(data, name), want, (step, action, name))
                self.assertEqual(self.admission_refused(name), bool(want), (step, action, name))
            if action == 'admit' and not self.admission_refused(org):
                with self.open_ledger() as ledger:
                    self.hold(ledger, 'new%d' % step, org)
                    holds[org].append('new%d' % step)
        self.assertEqual(seen, {'overrun', 'over_bound', 'legacy'})  # the sequence reached every reason


class ProjectPinTests(fixture.Accounting):
    def test_a_pinned_service_refuses_settle_without_a_workflow(self):
        pinned = command.Service(self.settings, self.ledger, workflow=self.reader, project='/checkout')
        self.addCleanup(pinned.close)
        self.assertEqual(pinned.handle(json.dumps(settle('root')).encode()),
                         dict(v=1, state='blocked', reason='project_required'))


class VersionTests(fixture.Accounting):
    def seed_v3(self, blocked):
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['version'] = 3
            data['organizations']['personal']['blocked'] = blocked
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()

    def test_a_v3_blocked_organization_becomes_a_legacy_block_and_keeps_blocking(self):
        self.seed_v3(True)
        org = self.snapshot()['organizations']['personal']
        self.assertNotIn('blocked', org)
        self.assertIs(org['legacy_block'], True)
        self.assertEqual(self.reasons(), ['legacy'])
        self.assertTrue(self.admission_refused())
        self.assertEqual(self.admit('a1')['state'], 'pending')

    def seed_unresolved_v3(self, blocked):
        """A v3 ledger whose S1 call was left unresolved by main's older over-bound path (no marker)."""
        self.start_run('s1')
        with self.open_ledger() as ledger:
            ledger.uncertain('s1', 'count')
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['version'] = 3
            data['organizations']['personal']['blocked'] = blocked
            data['runs']['s1']['calls']['count'].pop('over_bound', None)
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()

    def test_migration_marks_unresolved_calls_of_a_blocked_organization_over_bound(self):
        """Round 4 S2: case C is closed for migrated ledgers too: a lower report cannot settle the old call."""
        self.seed_unresolved_v3(True)
        call = self.snapshot()['runs']['s1']['calls']['count']
        self.assertEqual((call['state'], call['over_bound']), ('unresolved', call['maximum']))
        self.assertEqual(self.reasons(), ['legacy', 'over_bound'])
        with self.assertRaisesRegex(budget.BudgetError, 'over_bound_unresolved'):
            self.finish_run('s1', 1)
        total = budget.Ledger._total(self.snapshot(), 'personal')
        self.assertEqual(total, call['maximum'])  # the books keep the maximum

    def test_migration_leaves_unresolved_calls_of_an_unblocked_organization_alone(self):
        self.seed_unresolved_v3(False)
        self.assertNotIn('over_bound', self.snapshot()['runs']['s1']['calls']['count'])
        self.finish_run('s1', 1)  # an ordinary uncertain call still completes in bound
        self.assertEqual(self.reasons(), [])

    def test_migration_backfills_roots_even_without_organizations(self):
        """Round 4 N1: the root backfill runs for every hold of a migrated ledger, whatever its organizations."""
        self.admit_sub('s1', root='root')
        expected = self.hold('s1')['root']
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data.update(version=3, organizations={})
            for field in ('root', 'stage'):
                del data['holds']['s1'][field]
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()
        self.assertEqual(self.hold('s1')['root'], expected)

    def test_a_v3_unblocked_organization_has_no_reasons(self):
        self.seed_v3(False)
        org = self.snapshot()['organizations']['personal']
        self.assertNotIn('blocked', org)
        self.assertNotIn('legacy_block', org)
        self.assertEqual(self.reasons(), [])
        self.assertEqual(self.admit('a1')['state'], 'admitted')

    def test_migration_backfills_the_root_of_holds_admitted_before_v4(self):
        self.funded('a1', root='root')
        expected = self.hold('a1')['root']
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['version'] = 3
            for field in ('root', 'stage'):
                del data['holds']['a1'][field]
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()
        hold = self.hold('a1')
        self.assertEqual((hold['root'], hold['stage']), (expected, 'worker'))

    """Review S3: settled charges need version 4, so older code fails closed instead of dropping them."""
    def test_a_v3_ledger_migrates_to_the_current_version_keeping_every_claim(self):
        self.funded('a1')
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['version'] = 3
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()
        before = self.snapshot()
        self.assertEqual(before['version'], 5)  # migrated on read (3 -> 4 -> 5)
        self.call(complete('a1', 'c1', usage=usage(M)))
        with closing(sqlite3.connect(self.ledger)) as db:
            stored = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
        self.assertEqual(stored['version'], 5)
        self.assertEqual(stored['admissions'], before['admissions'])
        self.assertEqual(ledger_versions.STATE_VERSION, 5)

    def test_older_code_refuses_a_v4_ledger(self):
        self.funded('a1')
        with mock.patch.object(ledger_versions, 'STATE_VERSION', 3), self.assertRaisesRegex(budget.BudgetError,
                                                                                  'ledger_identity_changed'):
            self.snapshot()


if __name__ == '__main__':
    unittest.main()
