"""S3.4: single-use dispatch authority, derived host ids, `unmanaged` sessions and lost acknowledgments."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from runtime import routing, routing_settings, usage_budget as budget
from runtime.adapters import opencode
from scripts import routing as command
from tests.test_routing_command import (API, Base, SCRIPT, api_observations, check, facts, now, observations,
                                        prepare)

ROOT = Path(__file__).resolve().parents[1]
CONSERVATIVE_SESSION = re.compile(r'ses_[0-9a-f]{32}')
CONSERVATIVE_MESSAGE = re.compile(r'msg_[0-9a-f]{32}')
LEDGER_A, LEDGER_B = 'a' * 32, 'b' * 32
NONCE_A, NONCE_B = '1' * 32, '2' * 32


def dispatch(attempt, **extra):
    request = dict(v=1, op='dispatch', attempt_id=attempt, request=facts())
    request.update(extra)
    return request


def record(attempt, kind, event_id=None, **extra):
    event = dict(event_id=event_id or '%s-%s' % (kind, attempt), attempt_id=attempt, kind=kind)
    event.update(extra)
    return dict(v=1, op='record', event=event)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class DispatchBase(Base):
    def ids(self, attempt):
        return routing.admission_ids(self.snapshot(), attempt)

    def spawn(self, op, request):
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT), op, '--settings', str(self.settings),
                                  '--ledger', str(self.ledger)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        child.stdin.write(json.dumps(request).encode())
        return child

    @staticmethod
    def collect(children):
        for child in children:
            child.stdin.close()
        results = []
        for child in children:
            results.append(json.loads(child.stdout.read()))
            child.wait(timeout=60)
            child.stdout.close(); child.stderr.close()
        return results


class DeriveIdsTests(unittest.TestCase):
    def test_ids_are_deterministic_and_match_the_host_patterns(self):
        first, again = (routing.derive_ids('attempt-1', LEDGER_A, NONCE_A) for _ in range(2))
        self.assertEqual(first, again)
        self.assertEqual(set(first), {'session_id', 'message_id'})
        self.assertTrue(opencode.SESSION.fullmatch(first['session_id']))
        self.assertTrue(opencode.MESSAGE.fullmatch(first['message_id']))
        self.assertTrue(CONSERVATIVE_SESSION.fullmatch(first['session_id']))
        self.assertTrue(CONSERVATIVE_MESSAGE.fullmatch(first['message_id']))
        first['session_id'] = 'mutated'
        self.assertNotEqual(routing.derive_ids('attempt-1', LEDGER_A, NONCE_A)['session_id'], 'mutated')

    def test_ledger_and_admission_nonce_both_change_the_ids(self):
        base = routing.derive_ids('a1', LEDGER_A, NONCE_A)
        for other in (routing.derive_ids('a1', LEDGER_B, NONCE_A), routing.derive_ids('a1', LEDGER_A, NONCE_B),
                      routing.derive_ids('a1', LEDGER_A, None)):  # None: an admission recorded before nonces
            self.assertFalse(set(base.values()) & set(other.values()))
        # Field boundaries are unambiguous: moving characters between the parts changes the ids.
        self.assertNotEqual(routing.derive_ids('b', 'ledgera', NONCE_A), routing.derive_ids('ab', 'ledger', NONCE_A))

    def test_ids_are_injective_over_a_two_hundred_thousand_sample(self):
        sessions, messages = set(), set()
        for identity in (LEDGER_A, LEDGER_B):
            for i in range(50_000):
                for nonce in (NONCE_A, NONCE_B):  # one attempt id reused with a fresh admission nonce
                    ids = routing.derive_ids('attempt-%d' % i, identity, nonce)
                    sessions.add(ids['session_id'][4:])
                    messages.add(ids['message_id'][4:])
        self.assertEqual((len(sessions), len(messages)), (200_000, 200_000))
        self.assertFalse(sessions & messages)  # the session and message spaces are domain-separated

    def test_near_identical_attempts_differ_and_bad_input_is_refused(self):
        variants = ['a', 'A', 'a ', ' a', 'a\u00e9', 'a' * 256, 'b' * 256]
        self.assertEqual(len({routing.derive_ids(v, LEDGER_A, NONCE_A)['session_id'] for v in variants}),
                         len(variants))
        for bad in ('', 'x' * 257, 'tab\tid', None, 7, b'a'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                routing.derive_ids(bad, LEDGER_A, NONCE_A)
            with self.subTest(identity=bad), self.assertRaises(ValueError):
                routing.derive_ids('a1', bad, NONCE_A)
        for bad in ('', 'x' * 257, 'tab\tid', 7, b'a'):
            with self.subTest(nonce=bad), self.assertRaises(ValueError):
                routing.derive_ids('a1', LEDGER_A, bad)


class AdmissionNonceTests(DispatchBase):
    """A random per-admission nonce makes a reused attempt id get new host ids; one admission keeps its ids."""
    def test_each_admission_records_a_nonce_and_a_duplicate_prepare_keeps_it(self):
        first = self.call(prepare('a1'))
        nonce = budget.Ledger.admission(self.snapshot(), 'a1')['nonce']
        self.assertRegex(nonce, r'^[0-9a-f]{32}$')
        ids = self.ids('a1')
        self.assertEqual(ids, routing.derive_ids('a1', self.snapshot()['identity'], nonce))
        again = self.call(prepare('a1'))
        self.assertIs(again['duplicate'], True)
        self.assertEqual(again['admission'], first['admission'])
        self.assertEqual(budget.Ledger.admission(self.snapshot(), 'a1')['nonce'], nonce)
        self.assertEqual(self.ids('a1'), ids)
        dispatched = self.call(dispatch('a1'))
        self.assertEqual((dispatched['session_id'], dispatched['message_id']), (ids['session_id'], ids['message_id']))
        self.call(prepare('a2'))
        self.assertNotEqual(budget.Ledger.admission(self.snapshot(), 'a2')['nonce'], nonce)

    def test_an_attempt_id_reused_after_pruning_gets_new_ids(self):
        self.call(prepare('a1'))
        old = self.ids('a1')
        self.call(record('a1', 'not_dispatched'))
        with self.open_ledger() as ledger, ledger.transaction() as data:  # the id ages out of every table
            del data['holds']['a1']
            data['admissions'] = [r for r in data['admissions'] if r['attempt_id'] != 'a1']
            data['seen_attempts'].remove('a1')
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')
        new = self.ids('a1')
        self.assertFalse(set(old.values()) & set(new.values()))
        self.assertEqual(self.call(dispatch('a1'))['session_id'], new['session_id'])
        self.assertEqual(self.call(dict(v=1, op='check', session_id=old['session_id'], request=facts())),
                         dict(v=1, state='unmanaged'))

    def test_admissions_without_a_nonce_still_dispatch(self):
        """Admissions recorded before nonces existed derive with no nonce."""
        self.call(prepare('a1'))
        with self.open_ledger() as ledger, ledger.transaction() as data:
            del budget.Ledger.admission(data, 'a1')['nonce']
        expected = routing.derive_ids('a1', self.snapshot()['identity'], None)
        self.assertEqual(self.ids('a1'), expected)
        self.assertEqual(self.call(dispatch('a1'))['session_id'], expected['session_id'])


class DispatchTests(DispatchBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')

    def test_dispatch_issues_one_authority_and_moves_the_hold(self):
        response = self.call(dispatch('a1', id='d1'))
        ids = self.ids('a1')
        self.assertEqual(response['state'], 'dispatched', response)
        self.assertEqual((response['v'], response['id'], response['attempt_id']), (1, 'd1', 'a1'))
        self.assertEqual((response['session_id'], response['message_id']), (ids['session_id'], ids['message_id']))
        self.assertRegex(response['authority'], r'^[A-Za-z0-9_-]{43}$')
        self.assertEqual(response['reconcile_hint'], dict(
            read='message', method='GET', authoritative=False, session_id=ids['session_id'],
            message_id=ids['message_id'],
            path='/api/session/%s/message/%s' % (ids['session_id'], ids['message_id'])))
        hold = self.snapshot()['holds']['a1']
        self.assertEqual(hold['state'], 'dispatched')
        self.assertEqual(hold['dispatch']['authority'], digest(response['authority']))
        self.assertEqual((hold['dispatch']['session_id'], hold['dispatch']['message_id']),
                         (ids['session_id'], ids['message_id']))

    def test_a_second_dispatch_gets_the_decision_and_never_a_token(self):
        first = self.call(dispatch('a1'))
        held = budget.Ledger.held(self.snapshot())
        for _ in range(3):
            again = self.call(dispatch('a1'))
            self.assertEqual(again['state'], 'already_dispatched')
            self.assertNotIn('authority', again)
            self.assertEqual((again['hold_state'], again['session_id']), ('dispatched', first['session_id']))
            self.assertNotIn(first['authority'], json.dumps(again))
        self.assertEqual(self.snapshot()['holds']['a1']['dispatch']['authority'], digest(first['authority']))
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)

    def test_the_raw_token_is_never_stored_or_reported(self):
        token = self.call(dispatch('a1'))['authority']
        self.service.close()
        stored = b''.join(path.read_bytes() for path in self.ledger.parent.iterdir() if path.is_file())
        self.assertNotIn(token.encode(), stored)
        self.assertIn(digest(token).encode(), stored)
        status = json.dumps(self.call(dict(v=1, op='status')))
        for needle in (token, digest(token)):
            self.assertNotIn(needle, status)
        checked = json.dumps(self.call(check('a1', authority=token)))
        self.assertNotIn(token, checked)

    def assertNothingConsumed(self, attempt, held):
        hold = self.snapshot()['holds'][attempt]
        self.assertEqual(hold['state'], 'admitted')
        self.assertNotIn('dispatch', hold)
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)

    def test_gates_block_and_consume_nothing(self):
        held = budget.Ledger.held(self.snapshot())
        cases = [(dispatch('a1', request=facts(model='other')), 'route_mismatch'),
                 (dispatch('a1', request=facts(funding_pool='api')), 'route_mismatch'),
                 (dispatch('a1', request=facts(bound={'win:sub/5h': 13})), 'envelope_exhausted'),
                 (dispatch('a1', request=facts(kind='teleport')), 'unsupported_kind'),
                 (dispatch('nope'), 'unknown_attempt')]
        for request, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason=reason))
                self.assertNothingConsumed('a1', held)
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=False)))
        self.assertEqual(self.call(dispatch('a1')), dict(v=1, state='blocked', reason='settings_revision_changed'))
        self.assertNothingConsumed('a1', held)

    def test_an_expired_or_future_admission_is_not_dispatched(self):
        service = command.Service(self.settings, self.ledger, test_inputs=True)
        self.addCleanup(service.close)
        at = budget.Ledger.admission(self.snapshot(), 'a1')['at']
        held = budget.Ledger.held(self.snapshot())
        for moment, reason in ((at + routing.ADMISSION_TTL + 1, 'admission_expired'), (at - 1, 'clock_invalid')):
            with self.subTest(reason=reason):
                response = service.handle(json.dumps(dispatch('a1', now=moment)).encode())
                self.assertEqual(response, dict(v=1, state='blocked', reason=reason))
                self.assertNothingConsumed('a1', held)
        self.assertEqual(service.handle(json.dumps(dispatch('a1', now=at + routing.ADMISSION_TTL)).encode())['state'],
                         'dispatched')

    def test_production_dispatch_ignores_a_caller_clock(self):
        self.assertEqual(self.call(dispatch('a1', now=now() + 10**7))['state'], 'dispatched')

    def test_closed_converted_and_uncertain_attempts(self):
        self.call(record('a1', 'not_dispatched'))
        self.assertEqual(self.call(dispatch('a1')), dict(v=1, state='blocked', reason='attempt_closed'))
        self.call(prepare('a2'))
        self.call(record('a2', 'uncertain'))
        response = self.call(dispatch('a2'))
        self.assertEqual((response['state'], response['hold_state']), ('already_dispatched', 'uncertain'))
        self.assertNotIn('authority', response)
        self.assertNotIn('dispatch', self.snapshot()['holds']['a2'])

    def test_an_s31_recorded_dispatch_never_gains_an_authority(self):
        self.call(record('a1', 'dispatched'))
        response = self.call(dispatch('a1'))
        self.assertEqual((response['state'], response['hold_state']), ('already_dispatched', 'dispatched'))
        self.assertNotIn('authority', response)
        self.assertNotIn('dispatch', self.snapshot()['holds']['a1'])

    def test_derived_id_collision_is_refused(self):
        self.call(prepare('a2'))
        self.assertEqual(self.call(dispatch('a1'))['state'], 'dispatched')
        taken = self.ids('a1')
        for clash in (taken, dict(session_id='ses_' + '0' * 32, message_id=taken['message_id'])):
            with self.subTest(clash=clash), \
                    mock.patch.object(routing, 'derive_ids', side_effect=lambda *args: dict(clash)):
                self.assertEqual(self.call(dispatch('a2')), dict(v=1, state='blocked', reason='id_collision'))
            self.assertEqual(self.snapshot()['holds']['a2']['state'], 'admitted')


class FundedDispatchTests(DispatchBase):
    def setUp(self):
        super().setUp()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(api_usage_enabled=True)))
        budget.initialize(self.ledger)
        self.fund()
        request = prepare('api1', observations=api_observations(), constraints=dict(allowed_strategy_ids=['s-api']))
        self.assertEqual(self.call(request)['admission']['class'], 'api')

    def test_disabled_organization_blocks_dispatch_and_consumes_nothing(self):
        held = budget.Ledger.held(self.snapshot())
        self.call(dict(v=1, op='set', organization=dict(id='personal', enabled=False)))
        self.assertEqual(self.call(dispatch('api1', request=facts(**API))),
                         dict(v=1, state='blocked', reason='organization_unavailable'))
        self.assertEqual(self.snapshot()['holds']['api1']['state'], 'admitted')
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)

    def test_api_usage_off_blocks_dispatch(self):
        # Through the command the settings revision gate would fire first; this isolates API Usage.
        settings = dict(routing_settings.resolve({}), api_usage_enabled=False, revision=1)
        with self.open_ledger() as ledger:
            result = routing.dispatch(ledger, 'api1', facts(**API), settings, now())
        self.assertEqual(result, dict(state='blocked', reason='api_usage_disabled'))
        self.assertEqual(self.snapshot()['holds']['api1']['state'], 'admitted')

    def test_a_funded_dispatch_keeps_its_dollar_hold(self):
        held = budget.Ledger.held(self.snapshot())
        token = self.call(dispatch('api1', request=facts(**API)))['authority']
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)
        allowed = self.call(check('api1', request=facts(**API), authority=token))
        self.assertEqual((allowed['state'], allowed['remaining']), ('allow', {'org:personal': 6 * 1_000_000}))


class ConversionTests(DispatchBase):
    """`convert_hold` takes only a hold dispatched with an issued authority, and keeps its dispatch record."""
    def setUp(self):
        super().setUp()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(api_usage_enabled=True)))
        budget.initialize(self.ledger)
        self.fund()
        for attempt in ('c1', 'c2'):
            request = prepare(attempt, observations=api_observations(),
                              constraints=dict(allowed_strategy_ids=['s-api']))
            self.assertEqual(self.call(request)['state'], 'admitted')

    def convert(self, attempt):
        task = self.snapshot()['holds'][attempt]['task']
        with self.open_ledger() as ledger:
            return ledger.convert_hold(attempt, 'personal', 'packet', 5_000_000, 1, now(),
                                       dict(account='na', task=task, windows=[]))

    def test_only_an_authority_dispatched_hold_converts(self):
        with self.assertRaisesRegex(budget.BudgetError, 'convert_forbidden'):
            self.convert('c1')  # admitted, never dispatched
        self.call(record('c2', 'dispatched'))  # an S3.1 recorded dispatch carries no authority
        with self.assertRaisesRegex(budget.BudgetError, 'convert_forbidden'):
            self.convert('c2')
        self.assertEqual(self.call(dispatch('c1', request=facts(**API)))['state'], 'dispatched')
        issued = self.snapshot()['holds']['c1']['dispatch']
        run = self.convert('c1')
        hold = self.snapshot()['holds']['c1']
        self.assertEqual((run['organization'], hold['state'], hold['dispatch']), ('personal', 'converted', issued))

    def test_an_uncertain_hold_never_converts(self):
        self.call(dispatch('c1', request=facts(**API)))
        self.call(record('c1', 'uncertain'))
        before = self.snapshot()
        with self.assertRaisesRegex(budget.BudgetError, 'convert_forbidden'):
            self.convert('c1')
        self.assertEqual(self.snapshot(), before)


class GateSeamTests(DispatchBase):
    """S3.5 adds fresh gates through `gates`; a refusal there consumes nothing and gates cannot write."""
    def setUp(self):
        super().setUp()
        self.call(prepare('a1'))
        self.settings_value = dict(routing_settings.resolve({}), revision=0)

    def test_an_extra_gate_refuses_inside_the_transaction(self):
        seen = []

        def reopened(data, record):
            seen.append(record['attempt_id'])
            return 'dependency_reopened'
        with self.open_ledger() as ledger:
            result = routing.dispatch(ledger, 'a1', facts(), self.settings_value, now(), gates=(reopened,))
            self.assertEqual(result, dict(state='blocked', reason='dependency_reopened'))
            self.assertEqual(seen, ['a1'])
            self.assertEqual(ledger.snapshot()['holds']['a1']['state'], 'admitted')
            passed = routing.dispatch(ledger, 'a1', facts(), self.settings_value, now(),
                                      gates=(lambda data, record: None,))
        self.assertEqual(passed['state'], 'dispatched')

    def test_a_gate_cannot_change_the_ledger(self):
        def meddle(data, record):
            data['caps'] = dict(total=99, per_account=99)
            data['holds']['a1']['amount'] = 0
            record['route'] = 'elsewhere'
            return None
        before = self.snapshot()
        with self.open_ledger() as ledger:
            result = routing.dispatch(ledger, 'a1', facts(), self.settings_value, now(), gates=(meddle,))
        self.assertEqual((result['state'], result['route']), ('dispatched', 'sub'))
        after = self.snapshot()
        self.assertNotIn('caps', after)
        self.assertEqual(after['holds']['a1']['amount'], before['holds']['a1']['amount'])
        self.assertEqual(budget.Ledger.admission(after, 'a1')['route'], 'sub')


class CheckAuthorityTests(DispatchBase):
    def setUp(self):
        super().setUp()
        self.call(prepare('a1'))
        self.token = self.call(dispatch('a1'))['authority']
        self.derived = self.ids('a1')

    def test_the_right_token_allows_on_every_path(self):
        for extra in (dict(authority=self.token),
                      dict(authority=self.token, session_id=self.derived['session_id']),
                      dict(authority=self.token, session_id='ses_childsession1')):  # a child session of the attempt
            with self.subTest(extra=sorted(extra)):
                response = self.call(check('a1', **extra))
                self.assertEqual((response['state'], response['authority'], response['attempt_id']),
                                 ('allow', 'verified', 'a1'))
                self.assertNotIn(self.token, json.dumps(response))
        by_session = self.call(dict(v=1, op='check', session_id=self.derived['session_id'], authority=self.token,
                                    request=facts()))
        self.assertEqual((by_session['state'], by_session['attempt_id']), ('allow', 'a1'))

    def test_a_missing_or_wrong_token_blocks(self):
        session = self.derived['session_id']
        cases = [(check('a1'), 'authority_required'),
                 (check('a1', session_id=session), 'authority_required'),
                 (dict(v=1, op='check', session_id=session, request=facts()), 'authority_required'),
                 (check('a1', authority='x' * 43), 'authority_invalid'),
                 (check('a1', authority=self.token[:-1]), 'authority_invalid'),
                 (check('a1', authority=digest(self.token)), 'authority_invalid')]
        for request, reason in cases:
            with self.subTest(reason=reason, request=sorted(request)):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason=reason))

    def test_the_envelope_and_route_still_apply_with_a_token(self):
        self.assertEqual(self.call(check('a1', authority=self.token, request=facts(bound={'win:sub/5h': 13}))),
                         dict(v=1, state='blocked', reason='envelope_exhausted'))
        self.assertEqual(self.call(check('a1', authority=self.token, request=facts(model='other'))),
                         dict(v=1, state='blocked', reason='route_mismatch'))

    def test_unknown_sessions_are_unmanaged(self):
        nonce = budget.Ledger.admission(self.snapshot(), 'a1')['nonce']
        for session in ('ses_interactive1', routing.derive_ids('never-admitted', LEDGER_A, NONCE_A)['session_id'],
                        routing.derive_ids('a1', LEDGER_B, nonce)['session_id'],  # the same admission, another ledger
                        self.derived['message_id']):  # a message id is never a session id
            with self.subTest(session=session):
                self.assertEqual(self.call(dict(v=1, op='check', session_id=session, request=facts(), id='q')),
                                 dict(v=1, state='unmanaged', id='q'))

    def test_another_attempts_session_is_a_mismatch(self):
        self.call(prepare('a2'))
        other = self.ids('a2')['session_id']
        self.assertEqual(self.call(check('a1', authority=self.token, session_id=other)),
                         dict(v=1, state='blocked', reason='session_mismatch'))

    def test_strict_checks_need_a_dispatched_attempt(self):
        self.call(prepare('a2'))
        session = self.ids('a2')['session_id']
        for request in (dict(v=1, op='check', session_id=session, request=facts()),
                        check('a2', session_id=session), check('a2', authority=self.token)):
            with self.subTest(request=sorted(request)):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason='dispatch_required'))
        self.assertEqual(self.call(check('a2'))['state'], 'allow')  # the S3.1 attempt-only check is unchanged
        self.call(record('a2', 'dispatched'))  # an S3.1 recorded dispatch carries no authority
        self.assertEqual(self.call(check('a2', session_id=session)),
                         dict(v=1, state='blocked', reason='dispatch_required'))

    def test_closed_uncertain_and_forgotten_sessions(self):
        session = self.derived['session_id']
        self.call(record('a1', 'uncertain'))
        self.assertEqual(self.call(dict(v=1, op='check', session_id=session, authority=self.token, request=facts())),
                         dict(v=1, state='blocked', reason='attempt_uncertain'))
        with self.open_ledger() as ledger, ledger.transaction() as data:  # the record ages out; the hold remains
            data['admissions'] = [r for r in data['admissions'] if r['attempt_id'] != 'a1']
        self.assertEqual(self.call(dict(v=1, op='check', session_id=session, authority=self.token, request=facts())),
                         dict(v=1, state='blocked', reason='attempt_closed'))
        with self.open_ledger() as ledger, ledger.transaction() as data:  # then the hold is pruned too
            del data['holds']['a1']
        # Without its nonce the session no longer resolves; the shim blocks `unmanaged` for an attached session.
        self.assertEqual(self.call(dict(v=1, op='check', session_id=session, authority=self.token, request=facts())),
                         dict(v=1, state='unmanaged'))

    def test_check_stays_read_only(self):
        before = self.snapshot()
        for request in (check('a1', authority=self.token), check('a1'), check('a1', authority='y' * 43),
                        dict(v=1, op='check', session_id='ses_unknown1', request=facts()),
                        dict(v=1, op='check', session_id=self.derived['session_id'], authority=self.token,
                             request=facts())):
            self.call(request)
        self.assertEqual(self.snapshot(), before)

    def test_malformed_check_fields_are_refused(self):
        cases = [dict(v=1, op='check', request=facts()),
                 dict(v=1, op='check', session_id=7, request=facts()),
                 dict(v=1, op='check', session_id='', request=facts()),
                 dict(v=1, op='check', session_id='s' * 257, request=facts()),
                 check('a1', authority=7), check('a1', authority=''), check('a1', authority='z' * 129),
                 check('a1', authority=['t'])]
        for request in cases:
            with self.subTest(request=json.dumps(request)[:80]):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason='invalid_request'))


class AuthorityRequiredTests(DispatchBase):
    """The one-way ledger flag, stamped on each admission: in-flight holds keep the rule they were admitted under."""
    def require(self):
        return self.call(dict(v=1, op='set', authority_required=True))

    def test_it_is_off_by_default_and_one_way(self):
        self.call(prepare('a1'))
        self.assertIs(self.call(dict(v=1, op='status'))['ledger']['authority_required'], False)
        self.assertEqual(self.call(check('a1'))['state'], 'allow')  # S3.1 rules while off
        self.assertEqual(self.require(), dict(v=1, state='updated', authority_required=True))
        self.assertEqual(self.require(), dict(v=1, state='updated', authority_required=True))
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=False)),
                         dict(v=1, state='blocked', reason='authority_required_one_way'))
        for bad in ('yes', 1, None):
            with self.subTest(bad=bad):
                self.assertEqual(self.call(dict(v=1, op='set', authority_required=bad)),
                                 dict(v=1, state='blocked', reason='invalid_request'))
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=True, expected_revision=0)),
                         dict(v=1, state='blocked', reason='invalid_request'))
        self.assertIs(self.call(dict(v=1, op='status'))['ledger']['authority_required'], True)

    def test_turning_it_on_creates_the_ledger_when_absent(self):
        self.assertEqual(self.require()['state'], 'updated')
        self.assertTrue(self.snapshot()['authority_required'])

    def test_stamped_admissions_need_dispatch_and_in_flight_ones_keep_their_rule(self):
        self.call(prepare('old'))
        self.require()
        self.call(prepare('new'))
        data = self.snapshot()
        self.assertNotIn('authority_required', budget.Ledger.admission(data, 'old'))
        self.assertIs(budget.Ledger.admission(data, 'new')['authority_required'], True)
        self.assertEqual(self.call(check('old'))['state'], 'allow')
        self.assertEqual(self.call(check('new')), dict(v=1, state='blocked', reason='dispatch_required'))
        self.assertEqual(self.call(record('new', 'dispatched')), dict(v=1, state='blocked', reason='dispatch_required'))
        self.assertEqual(self.snapshot()['holds']['new']['state'], 'admitted')
        self.assertEqual(self.call(record('old', 'dispatched'))['state'], 'recorded')
        self.assertEqual(self.call(check('old'))['state'], 'allow')
        token = self.call(dispatch('new'))['authority']
        self.assertEqual(self.call(check('new', authority=token))['authority'], 'verified')
        self.assertEqual(self.call(check('new')), dict(v=1, state='blocked', reason='authority_required'))
        self.assertEqual(self.call(record('new', 'dispatched'))['duplicate'], True)


class LostAcknowledgmentTests(DispatchBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')
        self.derived = self.ids('a1')

    def test_a_lost_dispatch_response_cannot_be_recovered(self):
        self.call(dispatch('a1'))  # the response is lost: nobody holds the token
        again = self.call(dispatch('a1'))
        self.assertEqual((again['state'], again['hold_state']), ('already_dispatched', 'dispatched'))
        self.assertNotIn('authority', again)
        self.assertEqual(again['reconcile_hint']['message_id'], self.derived['message_id'])
        self.assertIs(again['reconcile_hint']['authoritative'], False)
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'dispatched')
        self.assertEqual(self.call(dict(v=1, op='check', session_id=self.derived['session_id'], request=facts())),
                         dict(v=1, state='blocked', reason='authority_required'))

    def test_uncertain_keeps_exposure_the_concurrency_place_and_blocks_checks(self):
        token = self.call(dispatch('a1'))['authority']
        held = budget.Ledger.held(self.snapshot())
        self.assertEqual(self.call(record('a1', 'uncertain'))['state'], 'recorded')
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)
        self.assertEqual(self.call(check('a1', authority=token)), dict(v=1, state='blocked', reason='attempt_uncertain'))
        self.assertEqual(self.call(prepare('a2'))['state'], 'admitted')
        third = self.call(prepare('a3'))
        self.assertEqual((third['state'], third['reason']), ('pending', 'concurrency_cap'))
        self.assertEqual(self.call(dict(v=1, op='status'))['ledger']['holds'], dict(uncertain=1, admitted=1))

    def test_nothing_releases_a_dispatched_or_uncertain_hold_before_s36(self):
        """No proof of non-launch is accepted in S3.4: `not_dispatched` is only for never-dispatched holds."""
        self.assertFalse(hasattr(budget.Ledger, 'release_unlaunched'))
        self.call(dispatch('a1'))
        held = budget.Ledger.held(self.snapshot())
        absent = dict(kind='message_absent', observed_at=now(), **self.derived)
        self.assertEqual(self.call(record('a1', 'not_dispatched')),
                         dict(v=1, state='blocked', reason='hold_state_forbidden'))
        self.assertEqual(self.call(record('a1', 'not_dispatched', proof=absent)),
                         dict(v=1, state='blocked', reason='invalid_request'))
        self.call(record('a1', 'uncertain'))
        for request in (record('a1', 'not_dispatched', 'nd2'), record('a1', 'not_dispatched', 'nd3', proof=absent),
                        record('a1', 'complete', 'c1')):
            with self.subTest(request=json.dumps(request)[:90]):
                self.assertEqual(self.call(request)['state'], 'blocked')
        hold = self.snapshot()['holds']['a1']
        self.assertEqual((hold['state'], hold['closed']), ('uncertain', False))
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)
        self.assertEqual(set(hold['dispatch']), {'session_id', 'message_id', 'authority', 'at'})

    def test_s31_events_after_dispatch(self):
        self.call(dispatch('a1'))
        self.assertEqual(self.call(record('a1', 'dispatched'))['duplicate'], True)
        self.assertEqual(self.call(record('a1', 'complete'))['state'], 'recorded')
        self.assertTrue(self.snapshot()['holds']['a1']['closed'])

    def test_a_process_killed_after_commit_leaves_one_dispatch_and_no_token(self):
        self.service.close()
        code = ('import os, sys; sys.path.insert(0, %r); from scripts import routing as c; '
                'c._write = lambda stdout, response: os._exit(9); '
                'sys.exit(c.main(["dispatch", "--settings", %r, "--ledger", %r]))'
                % (str(ROOT), str(self.settings), str(self.ledger)))
        done = subprocess.run([sys.executable, '-B', '-c', code], input=json.dumps(dispatch('a1')).encode(),
                              capture_output=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout), (9, b''))
        hold = self.snapshot()['holds']['a1']
        self.assertEqual(hold['state'], 'dispatched')
        self.assertIn('authority', hold['dispatch'])
        again = self.call(dispatch('a1'))
        self.assertEqual(again['state'], 'already_dispatched')
        self.assertNotIn('authority', again)


class SettingsOrderingTests(DispatchBase):
    """A settings change and a dispatch are totally ordered: `set` reports an attempt invalidated only if no
    dispatch of it can still succeed, and a dispatch that wins the lock first is not reported as invalidated."""
    def setUp(self):
        super().setUp()
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')

    def off(self, revision=0):
        return dict(v=1, op='set', expected_revision=revision, change=dict(smart_routing=False))

    def assertConsistent(self, dispatched, changed, attempt='a1'):
        listed = attempt in changed['invalidated_attempts']
        if listed:
            self.assertEqual(dispatched, dict(v=1, state='blocked', reason='settings_revision_changed'))
        else:
            self.assertEqual(dispatched['state'], 'dispatched', dispatched)
        hold = self.snapshot()['holds'][attempt]
        self.assertEqual(hold['state'], 'admitted' if listed else 'dispatched')

    def test_a_dispatch_waiting_behind_set_is_refused(self):
        real = routing_settings.update
        children = []

        def slow_update(*args, **kwargs):  # runs while `set` holds the ledger's write lock
            children.append(self.spawn('dispatch', dispatch('a1')))
            children[0].stdin.close()
            time.sleep(0.5)
            return real(*args, **kwargs)
        with mock.patch.object(command.routing_settings, 'update', side_effect=slow_update):
            changed = self.call(self.off())
        result = json.loads(children[0].stdout.read())
        children[0].wait(timeout=60); children[0].stdout.close(); children[0].stderr.close()
        self.assertEqual(changed['invalidated_attempts'], ['a1'])
        self.assertEqual(result, dict(v=1, state='blocked', reason='settings_revision_changed'))
        self.assertNotIn('dispatch', self.snapshot()['holds']['a1'])

    def test_a_set_waiting_behind_dispatch_does_not_report_it(self):
        self.service.close()
        entered, results = threading.Event(), []
        settings_value = dict(routing_settings.resolve({}), revision=0)

        def slow_settings():  # runs inside the dispatch's write transaction
            entered.set()
            time.sleep(0.5)
            return settings_value

        def worker():
            with self.open_ledger() as ledger:
                results.append(routing.dispatch(ledger, 'a1', facts(), slow_settings, now))
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(10))
        changed = self.call(self.off())
        thread.join(60)
        self.assertEqual(results[0]['state'], 'dispatched')
        self.assertEqual(changed['invalidated_attempts'], [])

    def test_the_reviewers_probe_is_never_inconsistent(self):
        """Hold the ledger lock, start a dispatch, then `set`; release. Whichever takes the lock first, the
        outcome is consistent, and a `set` that lists the attempt always means the dispatch was refused."""
        self.service.close()
        lock = sqlite3.connect(self.ledger, isolation_level=None)
        lock.execute('BEGIN IMMEDIATE')
        try:
            waiting = self.spawn('dispatch', dispatch('a1'))
            waiting.stdin.close()
            time.sleep(0.3)
            setter = self.spawn('set', self.off())
            setter.stdin.close()
            time.sleep(0.3)
        finally:
            lock.execute('ROLLBACK')
            lock.close()
        dispatched, changed = (json.loads(child.stdout.read()) for child in (waiting, setter))
        for child in (waiting, setter):
            child.wait(timeout=60); child.stdout.close(); child.stderr.close()
        self.assertEqual(changed['state'], 'updated', changed)
        self.assertConsistent(dispatched, changed)

    def test_dispatch_racing_set_stays_consistent(self):
        self.service.close()
        revision = 0
        for i in range(12):
            attempt = 'r%d' % i
            if i:
                self.assertEqual(self.call(prepare(attempt))['state'], 'admitted')
            else:
                attempt = 'a1'
            margin = 2000 + i + 1
            children = [self.spawn('dispatch', dispatch(attempt)),
                        self.spawn('set', dict(v=1, op='set', expected_revision=revision, change=dict(margin_bp=margin)))]
            dispatched, changed = self.collect(children)
            with self.subTest(round=i):
                self.assertEqual(changed['state'], 'updated', changed)
                self.assertConsistent(dispatched, changed, attempt)
            revision = changed['revision']
            kind = 'complete' if dispatched['state'] == 'dispatched' else 'not_dispatched'
            self.assertEqual(self.call(record(attempt, kind))['state'], 'recorded')
            self.service.close()


class RaceTests(DispatchBase):
    def test_fifteen_racing_processes_yield_one_authority(self):
        self.assertEqual(self.call(prepare('race'))['state'], 'admitted')
        self.service.close()
        results = self.collect([self.spawn('dispatch', dispatch('race')) for _ in range(15)])
        states = sorted(r['state'] for r in results)
        self.assertEqual(states, ['already_dispatched'] * 14 + ['dispatched'], results)
        tokens = [r['authority'] for r in results if 'authority' in r]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(self.snapshot()['holds']['race']['dispatch']['authority'], digest(tokens[0]))

    def test_distinct_attempts_dispatch_concurrently(self):
        attempts = dict(d1='anthropic:acct', d2='anthropic:acct', d3='other:acct', d4='other:acct')
        for attempt, account in attempts.items():
            self.assertEqual(self.call(prepare(attempt, observations=observations(account=account)))['state'],
                             'admitted')
        self.service.close()
        results = self.collect([self.spawn('dispatch', dispatch(attempt)) for attempt in attempts])
        self.assertEqual([r['state'] for r in results], ['dispatched'] * 4, results)
        self.assertEqual(len({r['authority'] for r in results}), 4)
        self.assertEqual(sorted(r['attempt_id'] for r in results), sorted(attempts))

    def test_threads_on_separate_ledger_instances_yield_one_authority(self):
        self.call(prepare('t1'))
        settings = dict(routing_settings.resolve({}), revision=0)
        results, start = [], threading.Barrier(15)

        def worker():
            with self.open_ledger() as ledger:
                start.wait()
                results.append(routing.dispatch(ledger, 't1', facts(), settings, now()))
        threads = [threading.Thread(target=worker) for _ in range(15)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(r['state'] for r in results), ['already_dispatched'] * 14 + ['dispatched'], results)


class ServeDispatchTests(DispatchBase):
    def test_serve_handles_dispatch_and_authority_checks_with_ids(self):
        self.call(prepare('s1'))
        session = self.ids('s1')['session_id']
        self.service.close()
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT), '--serve', '--settings', str(self.settings),
                                  '--ledger', str(self.ledger)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        try:
            def ask(request):
                child.stdin.write(json.dumps(request).encode() + b'\n'); child.stdin.flush()
                return json.loads(child.stdout.readline())
            first = ask(dispatch('s1', id='r1'))
            second = ask(dispatch('s1', id=2))
            allowed = ask(dict(v=1, op='check', id='r3', attempt_id='s1', session_id=session,
                               authority=first['authority'], request=facts()))
            unmanaged = ask(dict(v=1, op='check', id='r4', session_id='ses_someoneelse', request=facts()))
        finally:
            child.stdin.close(); child.wait(timeout=60); child.stdout.close(); child.stderr.close()
        self.assertEqual((first['id'], first['state']), ('r1', 'dispatched'))
        self.assertEqual((second['id'], second['state']), (2, 'already_dispatched'))
        self.assertEqual((allowed['id'], allowed['state'], allowed['authority']), ('r3', 'allow', 'verified'))
        self.assertEqual(unmanaged, dict(v=1, id='r4', state='unmanaged'))


class HostileInputTests(DispatchBase):
    def test_malformed_dispatch_requests_are_refused(self):
        self.call(prepare('a1'))
        cases = [(dispatch(7), 'invalid_request'), (dispatch(''), 'invalid_request'),
                 (dispatch('x' * 257), 'invalid_request'), (dispatch('a1', authority='t' * 43), 'invalid_request'),
                 (dispatch('a1', session_id='ses_x'), 'invalid_request'),
                 (dispatch('a1', request=dict(facts(), prompt='x')), 'invalid_request'),
                 (dict(v=1, op='dispatch', attempt_id='a1'), 'invalid_request'),
                 (dispatch('a1', request=facts(bound={'win:sub/5h': -1})), 'invalid_request')]
        for request, reason in cases:
            with self.subTest(request=json.dumps(request)[:80]):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason=reason))
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'admitted')

    def test_dispatch_and_check_without_a_ledger_fail_closed(self):
        self.assertEqual(self.call(dispatch('a1')), dict(v=1, state='blocked', reason='ledger_absent'))
        self.assertEqual(self.call(dict(v=1, op='check', session_id='ses_any1', request=facts())),
                         dict(v=1, state='blocked', reason='ledger_absent'))
        self.assertFalse(self.ledger.exists())


class SettingsFallbackTests(DispatchBase):
    """A settings change lands even when the ledger is absent, busy, corrupt or unsafe: only the exact
    invalidated list is lost, and dispatch and check still refuse the old admissions afterwards."""
    def setUp(self):
        super().setUp()
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')
        self.service.close()

    def off(self, service=None, revision=0):
        request = dict(v=1, op='set', expected_revision=revision, change=dict(smart_routing=False))
        return (service or self.service).handle(json.dumps(request).encode())

    def assertLanded(self, changed, ledger_state):
        self.assertEqual((changed['state'], changed['revision'], changed['invalidated_attempts'],
                          changed['ledger_state']), ('updated', 1, None, ledger_state), changed)
        self.assertIs(changed['settings']['smart_routing'], False)
        self.assertIs(self.call(dict(v=1, op='status'))['settings']['smart_routing'], False)

    def assertRefused(self):
        self.assertEqual(self.call(dispatch('a1')), dict(v=1, state='blocked', reason='settings_revision_changed'))
        self.assertEqual(self.call(check('a1')), dict(v=1, state='blocked', reason='settings_revision_changed'))
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'admitted')

    def test_the_healthy_path_is_unchanged(self):
        changed = self.off()
        self.assertEqual(changed['invalidated_attempts'], ['a1'])
        self.assertNotIn('ledger_state', changed)

    def test_a_locked_ledger_does_not_stop_a_settings_change(self):
        serving = command.Service(self.settings, self.ledger, serving=True)
        self.addCleanup(serving.close)
        lock = sqlite3.connect(self.ledger, isolation_level=None)
        lock.execute('BEGIN IMMEDIATE')
        try:
            start = time.perf_counter()
            changed = self.off(serving)
            self.assertLess(time.perf_counter() - start, 2.0)
        finally:
            lock.execute('ROLLBACK')
            lock.close()
        self.assertLanded(changed, 'ledger_busy')
        self.assertRefused()

    def test_an_unsafe_ledger_path_does_not_stop_a_settings_change(self):
        self.ledger.chmod(0o644)
        try:
            changed = self.off()
        finally:
            self.ledger.chmod(0o600)
        self.assertLanded(changed, 'unsafe_ledger_path')
        self.assertRefused()

    def test_a_corrupt_ledger_does_not_stop_a_settings_change(self):
        with sqlite3.connect(self.ledger) as db:
            body = db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0]
            db.execute('UPDATE ledger SET body=? WHERE id=1', ('{not json',))
        changed = self.off()
        self.assertEqual((changed['state'], changed['invalidated_attempts']), ('updated', None), changed)
        self.assertIn(changed['ledger_state'], ('ledger_unavailable', 'invalid_ledger'))
        self.assertEqual(self.call(dispatch('a1'))['state'], 'blocked')  # the corrupt ledger refuses everything
        self.service.close()
        with sqlite3.connect(self.ledger) as db:
            db.execute('UPDATE ledger SET body=? WHERE id=1', (body,))
        self.assertRefused()

    def test_an_absent_ledger_does_not_stop_a_settings_change(self):
        self.ledger.unlink()
        self.assertLanded(self.off(), 'absent')
        self.assertFalse(self.ledger.exists())

    def test_the_revision_check_still_applies_without_a_ledger_lock(self):
        self.ledger.chmod(0o644)
        try:
            self.assertEqual(self.off()['state'], 'updated')
            self.assertEqual(self.off(revision=0), dict(v=1, state='blocked', reason='stale_revision'))
        finally:
            self.ledger.chmod(0o600)


class UnstampedStatusTests(DispatchBase):
    """Pre-canary check: `status` counts open holds admitted before `authority_required` (unstamped)."""
    def count(self):
        return self.call(dict(v=1, op='status'))['ledger']['unstamped_open_holds']

    def test_open_unstamped_holds_are_counted_until_they_close(self):
        self.call(prepare('old1'))
        self.call(prepare('old2'))
        self.assertEqual(self.count(), 2)
        self.call(dict(v=1, op='set', authority_required=True))
        self.call(prepare('new', observations=observations(account='other:acct')))
        self.assertEqual(self.count(), 2)  # stamped admissions are not counted
        self.call(record('old1', 'not_dispatched'))
        self.assertEqual(self.count(), 1)
        self.call(record('old2', 'dispatched'))  # a bare S3.1 dispatch stays open, and counted, until it completes
        self.assertEqual(self.count(), 1)
        self.call(record('old2', 'complete'))
        status = self.call(dict(v=1, op='status'))['ledger']
        self.assertEqual((status['unstamped_open_holds'], status['authority_required']), (0, True))


class ProtocolNoteTests(DispatchBase):
    def test_record_refuses_a_now_field(self):
        self.call(prepare('a1'))
        for service in (self.service, command.Service(self.settings, self.ledger, test_inputs=True)):
            with self.subTest(test_inputs=service.test_inputs):
                response = service.handle(json.dumps(dict(record('a1', 'uncertain'), now=now())).encode())
                self.assertEqual(response, dict(v=1, state='blocked', reason='invalid_request'))
                service.close()
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'admitted')


class DispatchCapacityTests(Base):
    def test_dispatch_fields_stay_within_their_fixed_allowance(self):
        hold = dict(dispatch=dict(session_id='ses_' + 'f' * 32, message_id='msg_' + 'f' * 32, authority='f' * 64,
                                  at=budget.MAX_INTEGER))
        self.assertLessEqual(len(json.dumps(hold, separators=(',', ':'))) - len('{}'), budget.DISPATCH_HOLD_BYTES)
        self.assertLessEqual(budget.DISPATCH_HOLD_BYTES, 256)


if __name__ == '__main__':
    unittest.main()
