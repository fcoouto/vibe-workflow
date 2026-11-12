"""S3.6a: root settlement, canonical acceptance, retention and bounds. See test_routing_accounting.py."""
from contextlib import closing
import json
import random
import socket
import sqlite3
import subprocess
import unittest
from unittest import mock
import urllib.request
from runtime import routing, usage_budget as budget
from scripts import routing as command
from tests.test_routing_command import API, M, api_observations, facts, now, prepare
from tests.test_routing_dispatch import dispatch, record
from tests import fixture_routing_accounting as fixture
from tests.fixture_routing_accounting import DAY, complete, settle, usage, where


class RootSettlementTests(fixture.Accounting):
    def settle(self, root='root', **extra):
        return self.call(settle(root, workflow=where(root), **extra))

    def test_one_accepted_at_100_and_one_rejected_at_90_cost_190_per_acceptance(self):
        self.funded('a', root='root')
        self.funded('b', root='root')
        self.call(complete('a', 'ea', usage=usage(100)))
        self.call(complete('b', 'eb', usage=usage(90)))
        self.reader.accepted = ['job-a']
        result = self.settle()
        self.assertEqual((result['state'], result['accepted']), ('settled', 1))
        self.assertEqual(result['cost_to_date']['usd_micros'], 190)
        self.assertEqual(result['ratio']['usd_micros'], 190)
        self.assertEqual(self.reader.calls, [where('root')])

    def test_the_ratio_carries_its_exact_fraction(self):
        for attempt, cost in (('a', 100), ('b', 91)):
            self.funded(attempt, root='root')
            self.call(complete(attempt, 'e' + attempt, usage=usage(cost)))
        self.reader.accepted = ['job-a', 'job-b', 'job-c']
        result = self.settle()
        self.assertEqual((result['ratio']['usd_micros'], result['ratio_num']['usd_micros'], result['ratio_den']),
                         (64, 191, 3))

    def test_roots_with_the_same_task_id_in_other_sessions_never_mix(self):
        self.funded('mine', root='root')
        self.funded('theirs', root='root', session='session-2')
        self.call(complete('mine', 'e1', usage=usage(10)))
        self.call(complete('theirs', 'e2', usage=usage(70)))
        self.assertEqual(self.settle()['cost_to_date']['usd_micros'], 10)
        other = self.call(settle('root', workflow=where('root', 'session-2')))
        self.assertEqual(other['cost_to_date']['usd_micros'], 70)

    def test_an_open_holds_root_survives_losing_its_admission_record(self):
        self.funded('a', root='root')
        with closing(sqlite3.connect(self.ledger)) as db:
            data = json.loads(db.execute('SELECT body FROM ledger').fetchone()[0])
            data['admissions'] = []
            db.execute('UPDATE ledger SET body=?', (json.dumps(data),)); db.commit()
        self.assertEqual(self.settle()['unresolved']['attempts'], 1)

    def test_zero_acceptances_has_no_finite_ratio(self):
        self.funded('a', root='root')
        self.call(complete('a', 'ea', usage=usage(190)))
        result = self.settle()
        self.assertEqual((result['accepted'], result['ratio'], result['cost_to_date']['usd_micros']), (0, None, 190))
        self.reader.accepted = None  # unreadable canonical state: acceptance unknown, never assumed
        unread = self.settle()
        self.assertEqual((unread['acceptance'], unread['accepted'], unread['ratio']), ('unavailable', None, None))
        self.assertEqual(unread['cost_to_date']['usd_micros'], 190)

    def test_self_reported_acceptance_never_counts(self):
        self.funded('a', root='root')
        self.assertEqual(self.call(complete('a', 'ea', usage=usage(100), accepted=True)),
                         dict(v=1, state='blocked', reason='invalid_request'))
        self.call(complete('a', 'ea', usage=usage(100), stage='review'))
        self.assertEqual(self.call(settle('root', workflow=where('root'), accepted=1)),
                         dict(v=1, state='blocked', reason='invalid_request'))
        result = self.settle()
        self.assertEqual((result['accepted'], result['ratio']), (0, None))

    def test_duplicate_child_receipts_count_once_and_every_attempt_counts(self):
        self.funded('parent', root='root')
        self.funded('child', root='root')
        self.funded('failed', root='root')
        self.assertEqual(self.admit('never', root='root')['state'], 'admitted')
        self.call(record('never', 'not_dispatched'))
        # The child's own receipt, observed while it runs and again in its completion, is reported once.
        self.assertEqual(self.observe_usage('o0', 'child', stage='worker', usage=usage(30, receipts=['child-1']))['state'], 'recorded')
        self.assertEqual(self.observe_usage('o1', 'coordinator-1', 'parent',
                                            usage=usage(5, receipts=['parent-1']))['state'], 'recorded')
        self.call(complete('child', 'e1', usage=usage(30, receipts=['child-1'])))
        self.call(complete('parent', 'e2', usage=usage(30, 50, receipts=['child-1', 'parent-1'])))
        self.call(complete('failed', 'e3', usage=usage(20), stage='worker'))
        result = self.settle()
        # child 30 (once), parent 80 (its own receipts; another attempt's id never removes them), failed 20,
        # coordinator 5: report dedup is per attempt (round-2 S-A).
        self.assertEqual(result['cost_to_date']['usd_micros'], 135)
        self.assertEqual(result['attempts'], 5)
        self.assertEqual(result['cost_to_date']['tokens']['input'], 50)
        self.assertEqual(result['by_stage']['coordinator']['usd_micros'], 5)

    def test_reviewer_rescue_is_incremental_rework(self):
        self.funded('w', root='root')
        self.funded('r', root='root')
        self.funded('x', root='root')
        self.call(complete('w', 'e1', usage=usage(40), stage='worker'))
        self.call(complete('r', 'e2', usage=usage(10), stage='review'))
        self.call(complete('x', 'e3', usage=usage(25), stage='rescue'))
        result = self.settle()
        self.assertEqual(result['rework']['usd_micros'], 25)
        self.assertEqual(result['by_stage']['review']['usd_micros'], 10)
        self.assertEqual(result['by_stage']['worker']['usd_micros'], 40)
        self.assertEqual(result['cost_to_date']['usd_micros'], 75)
        self.assertEqual(result['executor_quality'], 'not_scored')

    def test_unresolved_exposure_is_reported_separately(self):
        self.funded('done', root='root')
        self.funded('lost', root='root')
        self.funded('open', root='root')
        self.call(complete('done', 'e1', usage=usage(70)))
        self.call(record('lost', 'uncertain'))
        self.call(complete('open', 'e2'))  # unknown cost
        result = self.settle()
        self.assertEqual(result['cost_to_date']['usd_micros'], 70)
        self.assertEqual(result['unresolved'], dict(attempts=2, usd_micros_held=12 * M, units_held={},
                                                    unknown_cost_attempts=1))

    def test_settle_is_read_only_and_needs_a_matching_locator(self):
        self.funded('a', root='root')
        self.call(complete('a', 'e1', usage=usage(10)))
        with closing(sqlite3.connect(self.ledger)) as db:
            before = db.execute('SELECT body FROM ledger').fetchone()[0]
            rows = db.execute('SELECT * FROM roots').fetchall()
        self.settle()
        self.assertEqual(self.call(settle('root', workflow=where('other'))),
                         dict(v=1, state='blocked', reason='invalid_request'))
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute('SELECT body FROM ledger').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT * FROM roots').fetchall(), rows)


class CanonicalAcceptanceTests(unittest.TestCase):
    """Acceptance comes only from the canonical delegation state (runtime/delegation.py), read-only."""
    def setUp(self):
        from tests.test_routing_gates import CanonicalWorkflowTests
        self.case = CanonicalWorkflowTests(methodName='test_ready_job_dispatches_and_unready_dependent_waits')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_an_accepted_job_is_counted_only_after_canonical_reconciliation(self):
        locator = dict(self.case.where('a'))
        del locator['job_id']
        self.assertEqual(self.case.reader.acceptances(locator), [])
        files = self.case.state_files()
        child = self.case.native('a', 'a-done')
        self.case.settle(child, 'a-done')
        self.assertEqual(self.case.reader.acceptances(locator), ['a'])
        files = self.case.state_files()
        self.case.reader.acceptances(locator)
        self.assertEqual(self.case.state_files(), files)


class RetentionTests(fixture.Accounting):
    def at(self, moment):
        return mock.patch.object(command.Service, 'clock', staticmethod(lambda: moment))

    def expire(self, moment):
        with self.open_ledger() as ledger:
            return ledger.expire_evidence(moment)

    def build(self, start):
        with self.at(start):
            self.admit_sub('closed', root='root', at=start)
            self.call(record('closed', 'dispatched'))
            self.call(complete('closed', 'c1', usage=usage(1, units={'win:sub/5h': 2})))
            self.admit_sub('lost', root='root', at=start)
            self.call(record('lost', 'dispatched'))
            self.call(record('lost', 'uncertain'))
            self.admit_sub('waiting', root='other', at=start)
            self.admit_sub('finished', root='done', at=start)
            self.call(record('finished', 'dispatched'))
            self.call(complete('finished', 'c2'))

    def test_expiry_removes_diagnostics_and_keeps_safety_records(self):
        start = now()
        self.build(start)
        early = self.expire(start + DAY + 1)  # only the closed attempts' snapshot detail is due
        self.assertEqual(early['removed']['snapshots'], 2)
        self.assertNotIn('pressure', budget.Ledger.admission(self.snapshot(), 'closed'))
        self.assertIn('pressure', budget.Ledger.admission(self.snapshot(), 'waiting'))
        late = self.expire(start + 15 * DAY)
        self.assertEqual(late['removed']['events'], 4)  # the closed attempts' dispatched and complete events
        self.assertEqual(late['removed']['admissions'], 2)
        safety = late['retained_safety_records']
        self.assertEqual((safety['open_holds'], safety['uncertain_holds']), (2, 1))
        self.assertGreaterEqual(safety['idempotency_events'], 2)  # lost: dispatched + uncertain
        self.assertEqual(safety['seen_attempts'], 4)
        data = self.snapshot()
        self.assertEqual(set(budget.Ledger.open_holds(data)), {'lost', 'waiting'})
        self.assertIsNotNone(budget.Ledger.admission(data, 'lost'))
        again = self.expire(start + 15 * DAY)
        self.assertEqual(sum(again['removed'].values()), 0)  # idempotent
        self.assertEqual(self.snapshot(), data)
        self.assertEqual(self.expire(start + 61 * DAY)['removed']['aggregates'], 1)  # `done`; `root` has an open hold
        self.assertEqual(set(budget.Ledger.open_holds(self.snapshot())), {'lost', 'waiting'})

    def test_every_read_filters_expired_evidence(self):
        start = now()
        self.build(start)
        with self.open_ledger() as ledger:  # nothing deleted yet: reads alone must hide expired evidence
            self.assertIsNotNone(ledger.lookup_event('c1', start + 13 * DAY))
            self.assertIsNone(ledger.lookup_event('c1', start + 15 * DAY))
            self.assertEqual(ledger.root_summary(budget.root_key(where('root')), start + 61 * DAY)['attempts'], 2)  # an open hold keeps it
            self.assertIsNone(ledger.root_summary(budget.root_key(where('done')), start + 61 * DAY))
            self.assertIsNotNone(ledger.root_summary(budget.root_key(where('done')), start + 59 * DAY))
        with self.at(start + 61 * DAY):
            self.assertEqual(self.call(complete('closed', 'c1', usage=usage(1, units={'win:sub/5h': 2}))),
                             dict(v=1, state='blocked', reason='attempt_closed'))  # never applied again

    def test_expired_admission_rows_never_steer_a_decision(self):
        start = now()
        data = self.snapshot()
        data['admissions'] = [dict(attempt_id='old%d' % i, bucket=['implementer', 'medium'], shares=dict(api=1.0),
                                   **{'class': 'api'}, at=start - 15 * DAY, settings_revision=1) for i in range(5)]
        rows = routing.decision_rows(data, 1, start)
        self.assertEqual(rows, [])
        data['admissions'][0]['at'] = start - DAY
        self.assertEqual([r['attempt_id'] for r in routing.decision_rows(data, 1, start)], ['old0'])

    def test_an_expired_snapshot_is_ignored_by_admit(self):
        observed = now() + 10
        with self.open_ledger() as ledger:
            ledger.observe('personal', self.BALANCE, observed)
        settings = dict(command.routing_settings.resolve({}), api_usage_enabled=True, revision=1)
        obs = api_observations(at=observed + DAY + 1)  # the balance snapshot is past its 24-hour retention
        with self.open_ledger() as ledger:
            result = routing.admit(dict(attempt_id='x', task='t', role='software-engineer', work_kind='implementation', complexity_band='medium',
                                        scope='scope1'), dict(strategy_ids=['s-api'], reason_codes=[]), settings,
                                   obs, ledger=ledger)
        self.assertEqual(result['status'], 'pending')  # an expired snapshot never funds a decision

    def test_prolonged_inactivity_keeps_active_caps_and_unresolved_claims(self):
        with self.open_ledger() as ledger:
            ledger.configure('personal', dict(self.POLICY, ceiling='custom', amount=50 * M), 1)
            revision = ledger.snapshot()['organizations']['personal']['revision']
        for attempt in ('a1', 'a2'):
            request = api_observations()
            request['pools']['api']['windows'][0]['organization_revision'] = revision
            request['routes'][-1]['account'] = 'acct-' + attempt
            response = self.call(prepare(attempt, observations=request, constraints=dict(allowed_strategy_ids=['s-api'])))
            self.assertEqual(response['state'], 'admitted', response)
            self.send(attempt)
        self.call(complete('a1', 'c1', usage=usage(4 * M)))
        self.call(record('a2', 'uncertain'))
        before = self.snapshot()
        cycle_total = budget.Ledger._total(before, 'personal', cycle='c')
        self.assertEqual(cycle_total, 4 * M + 6 * M)
        months = now() + 120 * DAY
        result = self.expire(months)
        data = self.snapshot()
        self.assertEqual(budget.Ledger._total(data, 'personal', cycle='c'), cycle_total)  # never reset
        self.assertEqual(budget.Ledger._headroom(data, 'personal'), min(
            budget.Ledger._credit(data, 'personal'), 50 * M - cycle_total))
        self.assertEqual(self.hold('a2')['state'], 'uncertain')
        self.assertEqual(result['retained_safety_records']['uncertain_holds'], 1)
        self.assertEqual(data['organizations'], before['organizations'])

    def test_cleanup_runs_at_command_start_once_per_interval(self):
        start = now()
        self.build(start)
        self.service.close()
        with self.at(start + 15 * DAY), mock.patch.object(budget.Ledger, 'expire_evidence',
                                                          autospec=True, side_effect=budget.Ledger.expire_evidence) as spy:
            fresh = command.Service(self.settings, self.ledger, workflow=self.reader)
            self.addCleanup(fresh.close)
            fresh.handle(json.dumps(record('waiting', 'not_dispatched')).encode())
            fresh.handle(json.dumps(record('nothing', 'not_dispatched')).encode())
            fresh.handle(json.dumps(settle('root')).encode())  # reads never write, so never clean
        self.assertEqual(spy.call_count, 1)
        self.assertEqual([body['attempt_id'] for _, body in self.events() if body['attempt_id'] == 'closed'], [])

    def test_observability_off_exports_nothing_and_keeps_local_accounting(self):
        self.funded('a1', root='root')
        denied = mock.Mock(side_effect=AssertionError('no export or network call is allowed'))
        with mock.patch.object(socket, 'socket', denied), mock.patch.object(socket, 'create_connection', denied), \
                mock.patch.object(urllib.request, 'urlopen', denied), mock.patch.object(subprocess, 'Popen', denied):
            self.assertEqual(self.call(complete('a1', 'c1', usage=usage(2 * M)))['state'], 'recorded')
            self.assertEqual(self.call(settle('root', workflow=where('root')))['cost_to_date']['usd_micros'], 2 * M)
            with self.open_ledger() as ledger:
                ledger.expire_evidence(now() + 30 * DAY)
        self.assertEqual(self.settled(), 2 * M)
        denied.assert_not_called()


class BoundsTests(fixture.Accounting):
    def test_the_event_table_is_bounded_and_keeps_needed_idempotency_records(self):
        self.funded('open')
        self.call(record('open', 'dispatched', 'needed'))  # a duplicate: no effect, so not protected
        self.call(record('open', 'uncertain', 'needed-2'))
        with self.open_ledger() as ledger:
            with ledger.transaction() as data:
                for i in range(budget.MAX_EVENTS + 20):
                    ledger._store_event(data, 'filler-%d' % i, '0' * 64, dict(attempt_id='gone', kind='observed',
                                                                         recorded_at=now()), now(), effect=True)
            count = ledger.db.execute('SELECT count(*) FROM events').fetchone()[0]
        self.assertEqual(count, budget.MAX_EVENTS)
        with self.open_ledger() as ledger:
            self.assertIsNotNone(ledger.lookup_event('needed-2', now()))

    def test_a_maximal_settlement_fits_its_allowance(self):
        big = budget.MAX_INTEGER
        settlement = dict(reserved=big, charged=big, released=big, overrun=big, cost=big,
                          cost_source='host_message_totals', overrun_state='unconfirmed',
                          resolution=dict(basis='reservation_floor', receipts=32, receipts_digest='f' * 32))
        self.assertLessEqual(len(json.dumps(dict(settlement=settlement), separators=(',', ':'))) - 2,
                             budget.SETTLEMENT_HOLD_BYTES)

    def test_an_open_hold_keeps_at_most_three_idempotency_records(self):
        self.funded('open', root='root')
        for i in range(5):
            self.call(complete('open', 'u%d' % i))  # repeated unknown costs: the first takes effect
            self.assertEqual(self.observe_usage('o%d' % i, 'open')['state'], 'recorded')
        with closing(sqlite3.connect(self.ledger)) as db:
            effects = db.execute("SELECT count(*) FROM events WHERE attempt_id='open' AND effect=1").fetchone()[0]
        self.assertEqual(effects, 1)
        self.assertEqual(self.call(complete('open', 'u9'))['duplicate'], True)

    def test_a_long_running_root_keeps_its_aggregate(self):
        self.funded('early', root='root')
        self.funded('late', root='root')
        self.call(complete('early', 'e1', usage=usage(10)))
        with self.open_ledger() as ledger:
            ledger.record_event(dict(event_id='e2', attempt_id='late', kind='complete',
                                     usage=usage(20)), 'd' * 64, now() + 90 * DAY)
            summary = ledger.root_summary(budget.root_key(where('root')), now() + 90 * DAY)
        self.assertEqual(summary['stages']['worker']['usd_micros'], 30)  # aged, but an open hold kept it

    def test_root_aggregates_are_bounded_per_row_and_in_count(self):
        self.funded('a', root='r' * 128)
        receipts = ['%0128d' % i for i in range(budget.MAX_USAGE_RECEIPTS)]
        for start in range(0, len(receipts), routing.MAX_USAGE_REQUESTS):
            chunk = receipts[start:start + routing.MAX_USAGE_REQUESTS]
            units = {'win:%s/%d-%d' % ('p' * 200, start, i): 1 for i in range(routing.MAX_USAGE_UNITS)}
            self.assertEqual(self.observe_usage('o%d' % start, 'coord', 'a', usage=dict(
                usage(*[1] * len(chunk), receipts=chunk, units=units)))['state'], 'recorded')
        with closing(sqlite3.connect(self.ledger)) as db:
            sizes = [len(row[0]) for row in db.execute('SELECT body FROM roots')]
        self.assertTrue(sizes and max(sizes) <= budget.MAX_ROOT_BYTES, sizes)


class PropertyTests(fixture.Accounting):
    """Random admit/dispatch/record (with duplicates and conflicts)/complete/expire sequences keep the books."""
    def test_random_sequences_keep_the_invariants(self):
        rng = random.Random(3606)
        attempts, sent, applied, remembered, events, overrun = [], set(), {}, {}, [], False
        for step in range(160):
            choice = rng.random()
            if choice < 0.25 or not attempts:
                attempt = 'p%d' % step
                if self.admit(attempt, rng.choice([M, 2 * M, 5 * M]))['state'] == 'admitted':
                    attempts.append(attempt)
            elif choice < 0.45:
                attempt = rng.choice(attempts)
                if self.call(dispatch(attempt, request=facts(**API)))['state'] == 'dispatched':
                    sent.add(attempt)
            elif choice < 0.8:
                attempt = rng.choice(attempts)
                kind = rng.choice(['complete', 'complete', 'uncertain', 'not_dispatched', 'dispatched'])
                extra = {}
                if kind == 'complete' and rng.random() < 0.8:
                    extra['usage'] = usage(rng.choice([0, 1, M, 3 * M, 7 * M]),
                                           source=rng.choice(['provider_receipt', 'estimate', 'host_message_totals']))
                request = record(attempt, kind, 'e%d' % step, **extra)
                if events and rng.random() < 0.3:
                    request = rng.choice(events)  # an exact duplicate
                events.append(request)
                before = self.snapshot()
                result = self.call(request)
                key = request['event']['event_id']
                if key in remembered and result['state'] == 'recorded':
                    self.assertEqual(result, dict(remembered[key], duplicate=True))  # the original outcome
                    self.assertEqual(self.snapshot(), before)
                elif result['state'] == 'recorded':
                    remembered[key] = result
                    if result['duplicate']:  # a new id for an effect that already happened (S3.1): no change
                        self.assertEqual(self.snapshot(), before)
                    else:
                        self.assertNotIn(key, applied)  # no event applied twice
                        applied[key] = result
                        overrun = overrun or result.get('outcome') == 'overrun'
                else:
                    self.assertEqual(self.snapshot(), before)  # a refusal changes nothing
            else:
                with self.open_ledger() as ledger:
                    ledger.expire_evidence(now() + rng.choice([0, DAY, 20 * DAY]))
            data = self.snapshot()
            self.assertGreaterEqual(budget.Ledger._headroom(data, 'personal'), 0)
            if not overrun:
                self.assertGreaterEqual(budget.Ledger._credit_raw(data, 'personal'), 0)
            charged = sum(r['settlement']['charged'] for r in applied.values()
                          if r.get('settlement', {}).get('kind') == 'usd_micros')
            self.assertEqual(data['organizations']['personal'].get('settled', {}).get('total', 0), charged)
            for attempt, hold in data['holds'].items():
                if hold['closed'] and 'settlement' in hold:
                    s = hold['settlement']
                    confirmed = s['overrun'] if s.get('overrun_state') == 'confirmed' else 0
                    self.assertEqual(s['charged'] - confirmed + s['released'] + hold['amount'], s['reserved'])
        outcomes = {r.get('outcome') for r in applied.values()}
        self.assertLessEqual({'complete', 'cost_unknown'}, outcomes)  # the sequence really settled dollar holds
        self.assertTrue(any(key in remembered for key in (e['event']['event_id'] for e in events[:-1])))


if __name__ == '__main__':
    unittest.main()
