"""S3.6c: operator reconciliation of uncertain SUBSCRIPTION (window) holds and the conservative window debit.

Contract: clients/opencode.md#uncertain-subscription-holds (S3.6c) and the S3.6c follow-up of
docs/superpowers/plans/2026-11-08-smart-routing.md.
"""
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

from runtime import routing, subscription_reconcile as sub, usage_budget as budget
from scripts import routing as command
from tests.fixture_opencode_accounting import account
from tests.fixture_routing_accounting import Accounting, complete, usage
from tests.test_routing_command import API, M, SCRIPT, SECRET, facts, now, observations, prepare
from tests.test_routing_dispatch import record

KEY = 'win:sub/5h'
HOUR = 3600
LENGTH = 18000  # the 5h window of the default subscription observation


def host_usage(units=None, session_id=None, **change):
    """The launcher's idle re-read of the derived session: `usage_from_messages(...)['usage']`, as `complete` sends."""
    value = account(lineage=[])['usage']
    if units is not None:
        value['requests'][0]['units'] = dict(units)
    evidence = dict(kind='host_usage', session_id=session_id, usage=value)
    evidence.update(change)
    return evidence


ABANDON = dict(kind='abandon')


def reconcile(attempt, evidence, event_id='s1', **extra):
    return dict(v=1, op='reconcile_subscription', attempt_id=attempt, evidence=evidence, event_id=event_id, **extra)


def blocked(reason):
    return dict(v=1, state='blocked', reason=reason)


class Subscription(Accounting):
    """Subscription attempts each use their own account (`acct-<attempt>`) on pool `sub`, window `5h`."""
    def setUp(self):
        super().setUp()
        self.service.close()
        # Tests supply an off-clock observation `now` to `prepare` (the window-epoch rule needs later windows).
        self.service = command.Service(self.settings, self.ledger, workflow=self.reader, test_inputs=True)

    def sub_dispatched(self, attempt):
        self.admit_sub(attempt)
        return self.send(attempt, sub=True)

    def sub_uncertain(self, attempt):
        self.sub_dispatched(attempt)
        self.assertEqual(self.call(record(attempt, 'uncertain'))['state'], 'recorded')
        self.assertEqual(self.hold(attempt)['state'], 'uncertain')

    def session(self, attempt):
        return self.hold(attempt)['dispatch']['session_id']

    def usage_of(self, attempt, **extra):
        return host_usage(session_id=self.session(attempt), **extra)

    def held(self, key=KEY):
        return sub.held(self.snapshot()).get(key, 0)

    def debits(self):
        return self.snapshot().get(sub.DEBITS, {})

    def status(self):
        return self.call(dict(v=1, op='status'))['ledger']

    def window(self, at, start, *, used=100, limit=1000, **change):
        """A pool snapshot at `at` whose 5h window began at `start` (reset_at = start + length)."""
        value = dict(id='5h', limit=limit, used=used, length=LENGTH, reset_at=start + LENGTH, rolling=False,
                     fresh=True)
        value.update(change)
        return value

    def prepare_at(self, attempt, at, window):
        obs = observations(at=at, account='acct-' + attempt)
        obs['pools']['sub']['windows'] = [window]
        return self.call(prepare(attempt, observations=obs, constraints=dict(allowed_strategy_ids=['s-sub'])))

    def cleared_rows(self):
        return [body for event_id, body in self.events() if body['kind'] == 'window_debit_cleared']


class HostUsageTests(Subscription):
    def test_a_host_re_read_settles_like_a_normal_subscription_completion(self):
        self.sub_uncertain('a1')
        claims = dict(self.hold('a1')['windows'])
        self.assertEqual(self.held(), claims[KEY])
        settled, credit = self.settled(), self.credit()
        result = self.call(reconcile('a1', self.usage_of('a1', units={KEY: 7})))
        self.assertEqual((result['state'], result['duplicate'], result['outcome'], result['event_id']),
                         ('recorded', False, 'complete', 's1'))
        self.assertEqual(result['settlement'], dict(kind='window', released=claims, observed_units={KEY: 7},
                                                    resolution=dict(basis='host_usage')))
        hold = self.hold('a1')
        self.assertEqual((hold['closed'], hold['state'], hold['amount']), (True, 'released', 0))
        self.assertEqual(self.held(), 0)  # the window claim is released, no debit is kept
        self.assertEqual(self.debits(), {})
        self.assertNotIn('acct-a1', budget.Ledger.open_accounts(self.snapshot()))  # the concurrency place is free
        self.assertEqual((self.settled(), self.credit()), (settled, credit))  # never dollars
        self.assertEqual(self.call(dict(v=1, op='check', attempt_id='a1', request=facts(),
                                        authority=self.tokens['a1'])), blocked('attempt_closed'))
        self.assertEqual(self.call(complete('a1', 'late', usage=usage(None, source='host_message_totals'))),
                         blocked('attempt_closed'))

    def test_the_settlement_joins_the_root_like_a_completion(self):
        self.admit_sub('a1', root='r1')
        self.send('a1', sub=True)
        self.call(record('a1', 'uncertain'))
        self.call(reconcile('a1', self.usage_of('a1', units={KEY: 7})))
        report = self.call(dict(v=1, op='settle', root_task_id='r1',
                                workflow=dict(client='codex', cwd='/checkout', session_id='session-1', task_id='r1')))
        self.assertEqual(report['cost_to_date']['units'], {KEY: 7})
        self.assertEqual(report['unresolved']['attempts'], 0)
        self.assertEqual(report['cost_to_date']['usd_micros'], 0)

    def test_host_usage_must_be_a_host_re_read_of_the_attempts_own_session(self):
        self.sub_uncertain('a1')
        self.admit_sub('bare')
        self.call(record('bare', 'dispatched'))  # an unstamped hold dispatched without `dispatch`: no session
        self.call(record('bare', 'uncertain'))
        before, stored = self.snapshot(), self.ledger_bytes()
        other = 'ses_' + '0' * 32
        for request, reason in (
                (reconcile('a1', host_usage(session_id=other)), 'evidence_mismatch'),
                (reconcile('bare', host_usage(session_id=other)), 'dispatch_required'),
                (reconcile('a1', dict(self.usage_of('a1'), usage=usage(None, source='provider_receipt'))),
                 'evidence_invalid'),
                (reconcile('a1', dict(self.usage_of('a1'), usage=usage(1, source='estimate'))), 'evidence_invalid')):
            with self.subTest(reason=reason):
                self.assertEqual(self.call(request), blocked(reason))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.call(reconcile('a1', self.usage_of('a1')))['state'], 'recorded')  # not remembered


class AbandonTests(Subscription):
    def test_abandon_frees_the_concurrency_place_and_keeps_the_window_claim_as_a_debit(self):
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=1, per_account=1))))
        self.sub_uncertain('a1')
        claims = dict(self.hold('a1')['windows'])
        self.assertEqual(self.admit_pending('a2'), 'concurrency_cap')  # the uncertain hold keeps its place
        settled, credit = self.settled(), self.credit()
        moment = now()
        result = self.call(reconcile('a1', ABANDON))
        self.assertEqual((result['state'], result['duplicate'], result['outcome']), ('recorded', False, 'abandoned'))
        self.assertEqual(result['settlement'], dict(kind='window', released={}, debited=claims, observed_units=None,
                                                    resolution=dict(basis='abandon')))
        hold = self.hold('a1')
        self.assertEqual((hold['closed'], hold['state']), (True, 'released'))
        debit = self.debits()[KEY]
        self.assertEqual(debit['units'], claims[KEY])
        self.assertTrue(moment <= debit['at'] <= now())
        self.assertEqual(self.held(), claims[KEY])  # still counted against the window
        self.assertEqual(budget.Ledger.held(self.snapshot()), {})  # but no hold carries it
        self.assertEqual((self.settled(), self.credit()), (settled, credit))  # never dollars
        self.assertEqual(self.admit_sub('a2')['state'], 'admitted')  # the concurrency place is free
        self.assertEqual(self.call(dict(v=1, op='check', attempt_id='a1', request=facts(),
                                        authority=self.tokens['a1'])), blocked('attempt_closed'))

    def admit_pending(self, attempt):
        response = self.call(prepare(attempt, observations=observations(account='acct-' + attempt),
                                     constraints=dict(allowed_strategy_ids=['s-sub'])))
        self.assertEqual(response['state'], 'pending', response)
        return response['reason']

    def test_abandon_never_invents_units(self):
        self.admit_sub('a1', root='r1')
        self.send('a1', sub=True)
        self.call(record('a1', 'uncertain'))
        self.call(reconcile('a1', ABANDON))
        report = self.call(dict(v=1, op='settle', root_task_id='r1',
                                workflow=dict(client='codex', cwd='/checkout', session_id='session-1', task_id='r1')))
        self.assertEqual(report['cost_to_date']['units'], {})
        rows = [body for _, body in self.events() if body.get('attempt_id') == 'a1' and body['kind'] ==
                sub.KIND]
        self.assertEqual(len(rows), 1)
        self.assertNotIn('usage', rows[0])

    def test_debits_of_one_window_add_up_and_keep_the_latest_mark(self):
        self.sub_uncertain('a1')
        self.sub_uncertain('a2')
        first = self.hold('a1')['windows'][KEY]
        second = self.hold('a2')['windows'][KEY]
        self.call(reconcile('a1', ABANDON, 's-a1'))
        mark = self.debits()[KEY]['at']
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data[sub.DEBITS][KEY]['at'] = mark - 100  # an older mark
        self.call(reconcile('a2', ABANDON, 's-a2'))
        debit = self.debits()[KEY]
        self.assertEqual(debit['units'], first + second)
        self.assertGreaterEqual(debit['at'], mark)  # the later abandon decides when the debit can clear

    def test_a_full_debit_table_refuses_a_new_window_and_changes_nothing(self):
        self.sub_uncertain('a1')
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data[sub.DEBITS] = {'win:p%d/w' % i: dict(units=1, at=1) for i in range(sub.MAX_WINDOW_DEBITS)}
        before, stored = self.snapshot(), self.ledger_bytes()
        self.assertEqual(self.call(reconcile('a1', ABANDON)), blocked('ledger_capacity_reached'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.hold('a1')['state'], 'uncertain')  # fail closed: the hold stays counted
        with self.open_ledger() as ledger, ledger.transaction() as data:
            del data[sub.DEBITS]['win:p0/w']
            data[sub.DEBITS][KEY] = dict(units=1, at=1)  # an existing window still merges
        self.assertEqual(self.call(reconcile('a1', ABANDON))['outcome'], 'abandoned')
        self.assertEqual(len(self.debits()), sub.MAX_WINDOW_DEBITS)


class DebitClearingTests(Subscription):
    def abandoned(self):
        self.sub_uncertain('a1')
        self.call(reconcile('a1', ABANDON))
        return self.debits()[KEY]

    def test_the_debit_counts_against_window_headroom_until_a_later_window_is_observed(self):
        debit = self.abandoned()
        reserve, mark = debit['units'], debit['at']
        later = mark + 2 * HOUR
        # Room for exactly one reserve without the debit, none with it.
        tight = dict(used=1000 - 2 * reserve + 1)
        same = self.window(later, mark - HOUR, **tight)  # the window the request may have consumed
        self.assertIn('insufficient_headroom', self.prepare_at('b1', later, same)['reasons'])
        self.assertIn(KEY, self.debits())
        fresh = self.window(later, mark + HOUR, **tight)  # began an hour after the abandon
        result = self.prepare_at('b2', later, fresh)
        self.assertEqual(result['state'], 'admitted', result)
        self.assertEqual(self.debits(), {})
        rows = self.cleared_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['outcome'], dict(state='cleared', key=KEY, units=reserve, at=mark,
                                                  window_start=mark + HOUR))
        # Exactly once: another qualifying observation finds nothing to clear.
        self.call(record('b2', 'not_dispatched'))
        self.prepare_at('b3', later + 60, self.window(later + 60, mark + HOUR, **tight))
        self.assertEqual(len(self.cleared_rows()), 1)

    def test_a_merely_newer_observation_never_clears_the_debit(self):
        debit = self.abandoned()
        mark = debit['at']
        later = mark + 2 * HOUR  # a newer observation time and snapshot epoch, the same window
        cases = dict(
            same_window=self.window(later, mark - 1),
            within_clock_margin=self.window(later, mark + routing.MAX_CLOCK_DRIFT - 1),
            rolling=self.window(later, mark + HOUR, rolling=True),
            no_reset=self.window(later, mark + HOUR, reset_at=None),
            no_length=self.window(later, mark + HOUR, length=None),
            stale=self.window(later, mark + HOUR, fresh=False),
            unknown_use=self.window(later, mark + HOUR, used=None),
            not_yet_begun=self.window(later, later + 1),
            already_reset=self.window(later, later - LENGTH),
            other_window=self.window(later, mark + HOUR, id='7d'))
        for i, (name, window) in enumerate(cases.items()):
            with self.subTest(case=name):
                obs = observations(at=later, account='acct-x%d' % i)
                obs['pools']['sub']['windows'] = [window]
                obs.update(epoch_at=later, snapshot_id='newer')
                self.call(prepare('x%d' % i, observations=obs, constraints=dict(allowed_strategy_ids=['s-sub'])))
                self.assertEqual(self.debits(), {KEY: debit})
        # A window that began after the mark but has already reset is a stale observation, not evidence.
        late = mark + 7 * HOUR
        self.prepare_at('y1', late, self.window(late, mark + HOUR))
        self.assertEqual(self.debits(), {KEY: debit})
        self.assertEqual(self.cleared_rows(), [])

    def test_only_an_explicitly_fixed_window_clears_the_debit(self):
        """Review S1: clearing is a release, so it fails closed: `rolling` must be exactly False. A missing key, None
        or 0 never clears (admission's own default for a missing key is unchanged)."""
        debit = self.abandoned()
        later = debit['at'] + 2 * HOUR
        good = self.window(later, debit['at'] + HOUR)
        missing = {k: v for k, v in good.items() if k != 'rolling'}
        for i, window in enumerate((missing, dict(good, rolling=None), dict(good, rolling=0),
                                    dict(good, rolling=0.0), dict(good, rolling='false'))):
            with self.subTest(rolling=window.get('rolling', 'missing')):
                self.prepare_at('r%d' % i, later, window)
                self.assertEqual(self.debits(), {KEY: debit})
        self.assertEqual(self.cleared_rows(), [])
        self.prepare_at('r-ok', later, good)
        self.assertEqual(self.debits(), {})

    def test_duplicate_window_ids_never_clear_the_debit(self):
        """Review NIT 2: the snapshot shape check does not reject duplicate window ids, so an ambiguous window (one
        qualifying copy and another, or two qualifying copies) reaches clearing and fails closed."""
        debit = self.abandoned()
        later = debit['at'] + 2 * HOUR
        good = self.window(later, debit['at'] + HOUR)
        same = self.window(later, debit['at'] - HOUR)
        for i, windows in enumerate(([good, same], [same, good], [good, dict(good)])):
            with self.subTest(case=i):
                obs = observations(at=later, account='acct-d%d' % i)
                obs['pools']['sub']['windows'] = windows
                self.call(prepare('d%d' % i, observations=obs, constraints=dict(allowed_strategy_ids=['s-sub'])))
                self.assertEqual(self.debits(), {KEY: debit})
        self.assertEqual(self.cleared_rows(), [])

    def test_the_clearing_audit_row_never_overwrites_an_event(self):
        """Review NIT 1: the audit row's id is longer than any client `event_id` may be, it is inserted without
        replacement, and a second clearing of the same key at the same mark gets its own row."""
        debit = self.abandoned()
        mark, later = debit['at'], debit['at'] + 2 * HOUR
        before = dict(self.events())
        self.prepare_at('b1', later, self.window(later, mark + HOUR))
        self.call(record('b1', 'not_dispatched'))
        added = [event_id for event_id, _ in self.events() if event_id not in before]
        self.assertEqual(len(added), 2)  # the clearing row and b1's not_dispatched
        cleared = [event_id for event_id, body in self.events() if body['kind'] == 'window_debit_cleared']
        self.assertEqual(len(cleared), 1)
        self.assertGreater(len(cleared[0]), budget.MAX_EVENT_ID)  # no client event id can take this shape
        self.assertEqual(routing.event_error(dict(event_id=cleared[0], attempt_id='a1', kind='complete')),
                         'invalid_request')
        # The same key debited again at the same mark and cleared again: a distinct row, the first one kept.
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data[sub.DEBITS] = {KEY: dict(debit)}
        self.prepare_at('b2', later, self.window(later, mark + HOUR))
        rows = [(event_id, body) for event_id, body in self.events() if body['kind'] == 'window_debit_cleared']
        self.assertEqual(len(rows), 2)
        self.assertIn(cleared[0], dict(rows))
        self.assertEqual([body['outcome']['at'] for _, body in rows], [mark, mark])

    def test_a_pending_prepare_still_clears_a_qualifying_debit_once(self):
        debit = self.abandoned()
        later = debit['at'] + 2 * HOUR
        full = self.window(later, debit['at'] + HOUR, used=1000)  # no room at all
        self.assertIn('insufficient_headroom', self.prepare_at('b1', later, full)['reasons'])
        self.assertEqual(self.debits(), {})
        self.assertEqual(len(self.cleared_rows()), 1)

    def test_derived_block_reasons_and_dollar_books_are_unaffected(self):
        self.funded('d1')
        self.start_run('s1')
        self.over_bound_run('s1')
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data['organizations']['personal']['legacy_block'] = True
        reasons, settled, credit = self.reasons(), self.settled(), self.credit()
        self.assertEqual(reasons, ['legacy', 'over_bound'])
        debit = self.abandoned()
        later = debit['at'] + 2 * HOUR
        self.prepare_at('b1', later, self.window(later, debit['at'] + HOUR))
        self.assertEqual((self.reasons(), self.settled(), self.credit()), (reasons, settled, credit))
        self.assertEqual(self.hold('d1')['state'], 'dispatched')

    def over_bound_run(self, run):
        with self.open_ledger() as ledger, self.assertRaisesRegex(budget.BudgetError, 'cost_exceeds_bound'):
            ledger.complete(run, 'count', 2 * M)


class StateTests(Subscription):
    def test_only_an_open_uncertain_subscription_hold_is_reconciled(self):
        self.admit_sub('adm')
        self.sub_dispatched('disp')
        self.funded('dollar')
        self.call(record('dollar', 'uncertain'))  # an uncertain DOLLAR hold uses reconcile_uncertain
        self.funded('unk')
        self.call(complete('unk', 'c-unk'))  # cost_unknown
        self.sub_uncertain('done')
        self.call(reconcile('done', ABANDON, 's-done'))
        before, stored = self.snapshot(), self.ledger_bytes()
        session = 'ses_' + '1' * 32
        for attempt, reason in (('adm', 'hold_state_forbidden'), ('disp', 'hold_state_forbidden'),
                                ('dollar', 'hold_state_forbidden'), ('unk', 'hold_state_forbidden'),
                                ('done', 'attempt_closed'), ('nope', 'unknown_attempt')):
            for evidence in (host_usage(session_id=session), ABANDON):
                with self.subTest(attempt=attempt, evidence=evidence['kind']):
                    self.assertEqual(self.call(reconcile(attempt, evidence, 'x-' + attempt)), blocked(reason))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        # The dollar op still refuses a subscription hold, and the client's bare `complete` stays refused.
        self.sub_uncertain('s2')
        self.assertEqual(self.call(dict(v=1, op='reconcile_uncertain', attempt_id='s2', event_id='u-s2',
                                        evidence=dict(kind='charge_reservation'))), blocked('hold_state_forbidden'))
        self.assertEqual(self.call(complete('s2', 'c-s2')), blocked('reconciliation_required'))

    def test_a_repeat_is_idempotent_and_byte_identical(self):
        for kind in ('host_usage', 'abandon'):
            with self.subTest(kind=kind):
                attempt = 'i-' + kind
                self.sub_uncertain(attempt)
                evidence = self.usage_of(attempt) if kind == 'host_usage' else ABANDON
                first = self.call(reconcile(attempt, evidence, 'e-' + attempt))
                self.assertEqual(first['duplicate'], False)
                before, stored = self.snapshot(), self.ledger_bytes()
                self.assertEqual(self.call(reconcile(attempt, evidence, 'e-' + attempt)), dict(first, duplicate=True))
                self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_a_conflicting_repeat_or_a_second_resolution_is_refused(self):
        self.sub_uncertain('a1')
        good = self.usage_of('a1')
        self.call(reconcile('a1', ABANDON))
        before, stored = self.snapshot(), self.ledger_bytes()
        for request, reason in ((reconcile('a1', good), 'event_conflict'),
                                (reconcile('a1', ABANDON, 's2'), 'attempt_closed'),
                                (reconcile('a1', good, 's3'), 'attempt_closed'),
                                (complete('a1', 's1'), 'event_conflict'),
                                (dict(v=1, op='reconcile_uncertain', attempt_id='a1', event_id='s1',
                                      evidence=dict(kind='charge_reservation')), 'event_conflict')):
            with self.subTest(request=request):
                self.assertEqual(self.call(request), blocked(reason))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_record_cannot_carry_a_subscription_reconciliation(self):
        self.sub_uncertain('a1')
        before = self.snapshot()
        for event, reason in ((dict(event_id='r1', attempt_id='a1', kind='reconcile_subscription'), 'record_unsupported'),
                              (dict(event_id='r1', attempt_id='a1', kind='complete', evidence=ABANDON),
                               'invalid_request'),
                              (dict(event_id='r1', attempt_id='a1', kind='abandon'), 'record_unsupported')):
            with self.subTest(event=event):
                self.assertEqual(self.call(dict(v=1, op='record', event=event)), blocked(reason))
        self.assertEqual(self.snapshot(), before)

    def test_the_ledger_refuses_unvalidated_events_itself(self):
        self.sub_uncertain('a1')
        with self.open_ledger() as ledger:
            for evidence, reason in ((dict(kind='lease_expired'), 'evidence_unsupported'),
                                     (dict(kind='receipts'), 'evidence_unsupported')):
                event = dict(event_id='z', attempt_id='a1', evidence=evidence, kind=sub.KIND)
                self.assertEqual(sub.reconcile(ledger, event, now()), dict(state='blocked', reason=reason))
        self.assertEqual(self.hold('a1')['state'], 'uncertain')


class MalformedTests(Subscription):
    def test_hostile_and_malformed_evidence_is_refused_with_fixed_reasons(self):
        self.sub_uncertain('a1')
        good = self.usage_of('a1')
        request = good['usage']['requests'][0]
        unsupported = [dict(kind=kind) for kind in ('lease_expired', 'timeout', 'host_not_found', 'no_spend_proof',
                                                    'receipts', 'charge_reservation', 'operator_says_no_spend')]
        invalid = [None, [], 'abandon', {}, dict(kind=7), dict(kind=True), dict(kind=None), dict(kind=''),
                   dict(kind='x' * 257), dict(kind='abandon\x00'), dict(ABANDON, reason=SECRET),
                   dict(ABANDON, units={KEY: 1}), dict(good, extra=1), {k: v for k, v in good.items() if k != 'usage'},
                   {k: v for k, v in good.items() if k != 'session_id'}, dict(good, session_id=None),
                   dict(good, session_id=7), dict(good, session_id='x' * 257), dict(good, session_id='a\x00'),
                   dict(good, usage=None), dict(good, usage=[]), dict(good, usage={}),
                   dict(good, usage=dict(good['usage'], requests=[])),
                   dict(good, usage=dict(good['usage'], requests=[request] * 33)),
                   dict(good, usage=dict(good['usage'], requests=[dict(request, tokens=dict(request['tokens'],
                                                                                        input=True))])),
                   dict(good, usage=dict(good['usage'], requests=[dict(request, tokens=dict(request['tokens'],
                                                                                        input=1.0))])),
                   dict(good, usage=dict(good['usage'], requests=[dict(request, tokens=dict(request['tokens'],
                                                                                        input=-1))])),
                   dict(good, usage=dict(good['usage'], requests=[dict(request, units={KEY: 10**30})])),
                   dict(good, usage=dict(good['usage'], requests=[dict(request, units={'org:x': 1})])),
                   dict(good, usage=dict(good['usage'], requests=[dict(request, prompt=SECRET)])),
                   dict(good, usage=dict(good['usage'], source='made_up')),
                   dict(good, usage=dict(good['usage'], requests=[[[[['deep']]]]])),
                   dict(good, usage=dict(good['usage'], requests=[dict(request, receipt='x' * 129)]))]
        before, stored = self.snapshot(), self.ledger_bytes()
        for evidence in unsupported:
            with self.subTest(evidence=evidence['kind']):
                self.assertEqual(self.call(reconcile('a1', evidence)), blocked('evidence_unsupported'))
        for evidence in invalid:
            with self.subTest(evidence=json.dumps(evidence)[:100]):
                self.assertEqual(self.call(reconcile('a1', evidence)), blocked('invalid_request'))
        for request in (reconcile('a1', ABANDON, 'x' * 129), reconcile('a1', ABANDON, ''), reconcile('a1', ABANDON, 7),
                        reconcile('a\x00', ABANDON), reconcile('x' * 257, ABANDON), reconcile('', ABANDON),
                        reconcile('a1', ABANDON, prompt=SECRET), reconcile('a1', ABANDON, now=now()),
                        dict(v=1, op='reconcile_subscription', attempt_id='a1', event_id='s1')):
            with self.subTest(request=json.dumps(request)[:100]):
                self.assertEqual(self.call(request)['state'], 'blocked')
        raw = json.dumps(reconcile('a1', good)).replace('"input": 2', '"input": NaN').encode()
        self.assertEqual(self.call(raw), blocked('invalid_json'))
        nested = b'{"v":1,"op":"reconcile_subscription","attempt_id":"a1","event_id":"s1","evidence":' + \
            b'[' * 100000 + b']' * 100000 + b'}'
        self.assertEqual(self.call(nested)['state'], 'blocked')
        huge = json.dumps(reconcile('a1', dict(ABANDON, pad='x' * (1 << 20)))).encode()
        self.assertEqual(self.call(huge), blocked('request_too_large'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.call(reconcile('a1', ABANDON))['state'], 'recorded')  # refusals were not remembered

    def test_the_audit_event_carries_no_receipts_session_ids_tokens_or_content(self):
        """The event row only; the root aggregate keeps receipt digests and the hold its session id, as for any
        completion (S3.6a, S3.4)."""
        self.sub_uncertain('a1')
        session = self.session('a1')
        self.call(reconcile('a1', self.usage_of('a1')))
        body = dict(self.events())['s1']
        self.assertEqual((body['kind'], body['attempt_id'], body['funding']), (sub.KIND, 'a1', 'subscription'))
        self.assertEqual(body['outcome']['settlement']['resolution'], dict(basis='host_usage'))
        self.assertEqual(set(body) - set(budget.EVENT_BODY_FIELDS), set())
        stored = json.dumps([row for row in self.ledger_bytes()[1] if row[0] == 's1'])
        for absent in ('msg_fixture30', 'msg_fixture28', session, self.tokens['a1'], SECRET):
            self.assertNotIn(absent, stored)


class StatusTests(Subscription):
    def test_status_lists_uncertain_subscription_holds_and_window_debits(self):
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=32, per_account=32))))
        with self.open_ledger() as ledger:
            ledger.set_caps(32, 32)
        for i in range(18):
            self.sub_uncertain('s%02d' % i)
        ledger = self.status()
        listed = ledger['reconcile']
        self.assertEqual(listed['reconcile_subscription'], dict(count=18, ids=['s%02d' % i for i in range(16)], more=2))
        self.assertEqual(listed['reconcile_uncertain'], dict(count=0, ids=[], more=0))
        self.assertEqual(listed['window_debits'], dict(count=0, keys=[], more=0))
        self.assertEqual(ledger['reconcile_hint'], sub.HINT)
        self.assertLessEqual(len(sub.HINT), 512)
        self.assertIn('reconcile_subscription', sub.HINT)
        self.assertEqual(ledger['uncertain_dollar'], dict(ids=[], more=0))  # unchanged: dollar holds only
        self.call(reconcile('s00', ABANDON))
        listed = self.status()['reconcile']
        self.assertEqual(listed['reconcile_subscription']['count'], 17)
        self.assertEqual(listed['window_debits'], dict(count=1, keys=[KEY], more=0))

    def test_hints_combine_and_a_clean_ledger_has_none(self):
        self.assertIsNone(self.status()['reconcile_hint'])
        self.funded('d1')
        self.call(record('d1', 'uncertain'))
        self.assertEqual(self.status()['reconcile_hint'], routing.RECONCILE_HINT)  # dollar only: unchanged
        self.sub_uncertain('s1')
        self.assertEqual(self.status()['reconcile_hint'], routing.RECONCILE_HINT + ' ' + sub.HINT)
        self.call(dict(v=1, op='reconcile_uncertain', attempt_id='d1', event_id='u1',
                       evidence=dict(kind='charge_reservation')))
        self.call(reconcile('s1', ABANDON))
        ledger = self.status()
        self.assertIsNone(ledger['reconcile_hint'])  # a debit clears from telemetry; it needs no operator action
        self.assertEqual(ledger['reconcile']['window_debits']['count'], 1)


def drop(key, marker, event_id='d1', **extra):
    return dict(v=1, op='drop_debit', key=key, marker=marker, event_id=event_id, **extra)


class ServeClearingTests(Subscription):
    """C3-0a (#192 review): a qualifying window clears a debit only outside `--serve`; the shim's persistent child,
    which a plugin or worker can reach, never releases one."""
    def abandoned(self):
        self.sub_uncertain('a1')
        self.call(reconcile('a1', ABANDON))
        return self.debits()[KEY]

    def test_a_serve_prepare_with_a_qualifying_window_leaves_the_debit(self):
        debit = self.abandoned()
        later = debit['at'] + 2 * HOUR
        serving = command.Service(self.settings, self.ledger, workflow=self.reader, test_inputs=True, serving=True)
        self.addCleanup(serving.close)
        obs = observations(at=later, account='acct-b1')
        obs['pools']['sub']['windows'] = [self.window(later, debit['at'] + HOUR)]
        request = prepare('b1', observations=obs, constraints=dict(allowed_strategy_ids=['s-sub']))
        self.assertEqual(serving.handle(json.dumps(request).encode())['state'], 'admitted')
        self.assertEqual((self.debits(), self.cleared_rows()), ({KEY: debit}, []))
        # The same snapshot through the persistent child of the real command: still kept.
        serving.close()
        self.service.close()
        request = prepare('b2', observations=dict(obs, routes=[dict(r, account='acct-b2') for r in obs['routes']]),
                          constraints=dict(allowed_strategy_ids=['s-sub']))
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), '--serve', '--test-inputs', '--settings',
                               str(self.settings), '--ledger', str(self.ledger)],
                              input=json.dumps(request).encode() + b'\n', capture_output=True, timeout=60)
        self.assertEqual((done.returncode, done.stderr), (0, b''))
        self.assertEqual(json.loads(done.stdout)['state'], 'admitted', done.stdout)
        self.assertEqual((self.debits(), self.cleared_rows()), ({KEY: debit}, []))
        # A one-shot `prepare` with the same evidence clears it.
        request = prepare('b3', observations=dict(obs, routes=[dict(r, account='acct-b3') for r in obs['routes']]),
                          constraints=dict(allowed_strategy_ids=['s-sub']))
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), 'prepare', '--test-inputs', '--settings',
                               str(self.settings), '--ledger', str(self.ledger)],
                              input=json.dumps(request).encode(), capture_output=True, timeout=60)
        self.assertEqual(json.loads(done.stdout)['state'], 'admitted', done.stdout)
        self.assertEqual((self.debits(), len(self.cleared_rows())), ({}, 1))

    def test_admit_clears_only_when_the_service_asks(self):
        self.assertIs(routing.admit.__kwdefaults__['clear_debits'], False)  # fail closed for any other caller
        seen = []
        real = routing.admit

        def spy(*args, **kwargs):
            seen.append(kwargs['clear_debits'])
            return real(*args, **kwargs)
        serving = command.Service(self.settings, self.ledger, workflow=self.reader, test_inputs=True, serving=True)
        self.addCleanup(serving.close)
        with mock.patch.object(command.routing, 'admit', spy):
            self.admit_sub('in-process')
            request = prepare('serving', observations=observations(account='acct-serving'),
                              constraints=dict(allowed_strategy_ids=['s-sub']))
            self.assertEqual(serving.handle(json.dumps(request).encode())['state'], 'admitted')
        self.assertEqual(seen, [True, False])


class DropDebitTests(Subscription):
    def setUp(self):
        super().setUp()
        self.operator = command.Service(self.settings, self.ledger, workflow=self.reader, one_shot=True)
        self.addCleanup(self.operator.close)

    def op(self, request):
        return self.operator.handle(json.dumps(request).encode())

    def listed(self):
        return self.status()['reconcile']['drop_debit']

    def marker(self, key=KEY):
        return {entry['key']: entry['marker'] for entry in self.listed()['debits']}[key]

    def test_status_lists_each_debit_with_its_marker(self):
        self.assertEqual(self.listed(), dict(debits=[], more=0))
        self.sub_uncertain('a1')
        self.call(reconcile('a1', ABANDON))
        debit = self.debits()[KEY]
        entry = self.listed()['debits'][0]
        self.assertEqual({k: v for k, v in entry.items() if k != 'marker'}, dict(key=KEY, units=debit['units'],
                                                                                at=debit['at']))
        self.assertRegex(entry['marker'], r'^dd_[0-9a-f]{32}$')
        self.assertIsNone(self.status()['reconcile_hint'])  # a debit still needs no operator action by itself

    def test_drop_debit_removes_one_debit_with_an_audit_row(self):
        self.sub_uncertain('a1')
        self.call(reconcile('a1', ABANDON))
        units = self.debits()[KEY]['units']
        settled, credit, marker = self.settled(), self.credit(), self.marker()
        result = self.op(drop(KEY, marker))
        self.assertEqual(result, dict(v=1, state='recorded', duplicate=False, outcome='debit_dropped', key=KEY,
                                      units=units, event_id='d1'))
        self.assertEqual((self.debits(), self.held()), ({}, 0))
        self.assertNotIn(sub.DEBITS, self.snapshot())
        self.assertEqual((self.settled(), self.credit()), (settled, credit))  # never dollars
        body = dict(self.events())['d1']
        self.assertEqual((body['kind'], body['outcome']['outcome']), ('drop_debit', 'debit_dropped'))
        self.assertEqual(set(body) - set(budget.EVENT_BODY_FIELDS), set())
        before, stored = self.snapshot(), self.ledger_bytes()
        self.assertEqual(self.op(drop(KEY, 'dd_' + '0' * 32)), blocked('event_conflict'))
        self.assertEqual(self.op(drop(KEY, marker)), dict(result, duplicate=True))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_a_stale_marker_is_refused(self):
        self.sub_uncertain('a1')
        self.sub_uncertain('a2')
        self.call(reconcile('a1', ABANDON, 's-a1'))
        seen = self.marker()
        self.call(reconcile('a2', ABANDON, 's-a2'))  # the debit grew after the operator reviewed it
        before, stored = self.snapshot(), self.ledger_bytes()
        self.assertEqual(self.op(drop(KEY, seen)), blocked('marker_stale'))
        self.assertEqual(self.op(drop('win:sub/7d', seen, 'd2')), blocked('debit_not_pending'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        self.assertEqual(self.op(drop(KEY, self.marker(), 'd3'))['state'], 'recorded')
        self.assertEqual(self.op(drop(KEY, seen, 'd4')), blocked('debit_not_pending'))

    def test_malformed_requests_are_refused_with_fixed_reasons(self):
        self.sub_uncertain('a1')
        self.call(reconcile('a1', ABANDON))
        good = self.marker()
        before, stored = self.snapshot(), self.ledger_bytes()
        for request in (drop(KEY, 'lb_' + good[3:]), drop(KEY, good.upper()), drop(KEY, good + '0'), drop(KEY, None),
                        drop('org:personal', good), drop('sub/5h', good), drop('win:' + 'x' * 600, good),
                        drop(7, good), drop(KEY, good, 'x' * 129), drop(KEY, good, ''), drop(KEY, good, units=1),
                        drop(KEY, good, prompt=SECRET), dict(v=1, op='drop_debit', key=KEY, event_id='d1')):
            with self.subTest(request=json.dumps(request)[:80]):
                self.assertEqual(self.op(request), blocked('invalid_request'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))

    def test_only_a_one_shot_command_drops_a_debit(self):
        self.sub_uncertain('a1')
        self.call(reconcile('a1', ABANDON))
        marker = self.marker()
        before, stored = self.snapshot(), self.ledger_bytes()
        serving = command.Service(self.settings, self.ledger, serving=True, one_shot=True)
        self.addCleanup(serving.close)
        for service in (self.service, serving):
            with self.subTest(serving=service.serving):
                self.assertEqual(service.handle(json.dumps(drop(KEY, marker)).encode()),
                                 blocked('operator_op_not_served'))
        self.service.close()
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), '--serve', '--settings', str(self.settings),
                               '--ledger', str(self.ledger)], input=json.dumps(drop(KEY, marker)).encode() + b'\n',
                              capture_output=True, timeout=60)
        self.assertEqual(json.loads(done.stdout), blocked('operator_op_not_served'))
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        done = self.run_cli(['drop_debit'], drop(KEY, marker))
        self.assertEqual(json.loads(done.stdout)['outcome'], 'debit_dropped', done.stdout)
        self.assertEqual(self.debits(), {})

    def test_fifteen_racing_processes_drop_once(self):
        self.sub_uncertain('a1')
        self.call(reconcile('a1', ABANDON))
        marker = self.marker()
        self.service.close()
        results = self.collect([self.spawn('drop_debit', drop(KEY, marker, 'same')) for _ in range(15)])
        self.assertEqual(sorted(r['duplicate'] for r in results), [False] + [True] * 14, results)
        results = self.collect([self.spawn('drop_debit', drop(KEY, marker, 'v%d' % i)) for i in range(15)])
        self.assertEqual(results, [blocked('debit_not_pending')] * 15)


class ProcessTests(Subscription):
    def test_fifteen_racing_processes_apply_one_reconciliation(self):
        self.sub_uncertain('race')
        self.service.close()
        same = reconcile('race', ABANDON, 's-race')
        results = self.collect([self.spawn('reconcile_subscription', same) for _ in range(15)])
        self.assertEqual(sorted(r['duplicate'] for r in results), [False] + [True] * 14, results)
        self.assertEqual(self.debits()[KEY]['units'], self.hold('race')['windows'][KEY])
        self.sub_uncertain('race2')
        good = self.usage_of('race2')
        self.service.close()
        children = []
        for i in range(15):
            if i % 3 == 0:
                children.append(self.spawn('record', complete('race2', 'c%d' % i,
                                                              usage=usage(None, source='host_message_totals'))))
            else:
                evidence = good if i % 3 == 1 else ABANDON
                children.append(self.spawn('reconcile_subscription', reconcile('race2', evidence, 'v%d' % i)))
        results = self.collect(children)
        effects = [r for r in results if r['state'] == 'recorded']
        self.assertEqual(len(effects), 1, results)
        self.assertTrue(all(r['reason'] in ('attempt_closed', 'reconciliation_required') for r in results
                            if r not in effects), results)
        units = self.hold('race2')['windows'][KEY]
        expected = 2 * units if effects[0]['outcome'] == 'abandoned' else units
        self.assertEqual(self.debits()[KEY]['units'], expected)

    def test_serve_refuses_the_op_and_one_shot_applies_it(self):
        self.sub_uncertain('a1')
        self.service.close()
        before, stored = self.snapshot(), self.ledger_bytes()
        lines = [reconcile('a1', ABANDON), dict(reconcile('a1', ABANDON, 's2'), id=7), dict(v=1, op='status')]
        raw = b''.join(json.dumps(line).encode() + b'\n' for line in lines)
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), '--serve', '--settings', str(self.settings),
                               '--ledger', str(self.ledger)], input=raw, capture_output=True, timeout=60)
        answers = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual((done.returncode, done.stderr, len(answers)), (0, b'', 3))
        self.assertEqual(answers[0], blocked('operator_op_not_served'))
        self.assertEqual(answers[1], dict(blocked('operator_op_not_served'), id=7))
        self.assertEqual(answers[2]['ledger']['reconcile']['reconcile_subscription']['count'], 1)
        self.assertEqual((self.snapshot(), self.ledger_bytes()), (before, stored))
        done = self.run_cli(['reconcile_subscription'], reconcile('a1', ABANDON))
        self.assertEqual(json.loads(done.stdout)['outcome'], 'abandoned', done.stdout)

    def test_the_pilot_lost_acknowledgment_now_has_a_recovery_path(self):
        """Qualification attempt 4 (lost acknowledgment), end to end through one-shot commands on temporary stores:
        dispatch, the launcher dies, `record uncertain`; before S3.6c no operator op applied to the hold."""
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=1, per_account=1))))
        self.service.close()

        def cli(op, request):
            done = self.run_cli([op], request)
            self.assertEqual((done.returncode, done.stderr), (0, b''))
            return json.loads(done.stdout)

        admitted = cli('prepare', prepare('p1', observations=observations(account='acct-p1'),
                                          constraints=dict(allowed_strategy_ids=['s-sub'])))
        self.assertEqual(admitted['state'], 'admitted', admitted)
        sent = cli('dispatch', dict(v=1, op='dispatch', attempt_id='p1', request=facts()))
        self.assertEqual(sent['state'], 'dispatched')
        self.assertEqual(cli('record', record('p1', 'uncertain'))['state'], 'recorded')
        self.assertEqual(cli('check', dict(v=1, op='check', attempt_id='p1', request=facts(),
                                           authority=sent['authority']))['state'], 'blocked')
        self.assertEqual(cli('record', complete('p1', 'c1')), blocked('reconciliation_required'))
        self.assertEqual(cli('reconcile_uncertain', dict(v=1, op='reconcile_uncertain', attempt_id='p1',
                                                         event_id='u1', evidence=dict(kind='charge_reservation'))),
                         blocked('hold_state_forbidden'))
        waiting = cli('prepare', prepare('p2', observations=observations(account='acct-p2'),
                                         constraints=dict(allowed_strategy_ids=['s-sub'])))
        self.assertEqual(waiting['reason'], 'concurrency_cap')
        status = cli('status', dict(v=1, op='status'))['ledger']
        self.assertEqual(status['reconcile']['reconcile_subscription']['ids'], ['p1'])
        self.assertEqual(status['reconcile_hint'], sub.HINT)
        # The recovery: the operator stops the worker and abandons the hold.
        result = cli('reconcile_subscription', reconcile('p1', ABANDON, 'op-1'))
        self.assertEqual((result['state'], result['outcome']), ('recorded', 'abandoned'))
        self.assertEqual(cli('reconcile_subscription', reconcile('p1', ABANDON, 'op-1'))['duplicate'], True)
        status = cli('status', dict(v=1, op='status'))['ledger']
        self.assertEqual((status['open_attempts'], status['reconcile_hint']), (0, None))
        self.assertEqual(status['reconcile']['window_debits']['keys'], [KEY])
        self.assertEqual(cli('prepare', prepare('p2', observations=observations(account='acct-p2'),
                                                constraints=dict(allowed_strategy_ids=['s-sub'])))['state'], 'admitted')
        self.assertEqual(cli('check', dict(v=1, op='check', attempt_id='p1', request=facts(),
                                           authority=sent['authority'])), blocked('attempt_closed'))


if __name__ == '__main__':
    unittest.main()
