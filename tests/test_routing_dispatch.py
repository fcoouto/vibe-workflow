"""S3.4: single-use dispatch authority, derived host ids, `unmanaged` sessions and lost acknowledgments."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
from unittest import mock

from runtime import routing, usage_budget as budget
from runtime.adapters import opencode
from scripts import routing as command
from tests.test_routing_command import (API, Base, POLICY, SCRIPT, api_observations, check, facts, now,
                                        observations, prepare)

ROOT = Path(__file__).resolve().parents[1]
CONSERVATIVE_SESSION = re.compile(r'ses_[0-9a-f]{32}')
CONSERVATIVE_MESSAGE = re.compile(r'msg_[0-9a-f]{32}')


def dispatch(attempt, **extra):
    request = dict(v=1, op='dispatch', attempt_id=attempt, request=facts())
    request.update(extra)
    return request


def record(attempt, kind, event_id=None, **extra):
    event = dict(event_id=event_id or '%s-%s' % (kind, attempt), attempt_id=attempt, kind=kind)
    event.update(extra)
    return dict(v=1, op='record', event=event)


def proof(attempt, observed_at, **change):
    ids = routing.derive_ids(attempt)
    value = dict(kind='message_absent', session_id=ids['session_id'], message_id=ids['message_id'],
                 observed_at=observed_at)
    value.update(change)
    return value


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class DeriveIdsTests(unittest.TestCase):
    def test_ids_are_deterministic_and_match_the_host_patterns(self):
        first, again = routing.derive_ids('attempt-1'), routing.derive_ids('attempt-1')
        self.assertEqual(first, again)
        self.assertEqual(set(first), {'session_id', 'message_id'})
        self.assertTrue(opencode.SESSION.fullmatch(first['session_id']))
        self.assertTrue(opencode.MESSAGE.fullmatch(first['message_id']))
        self.assertTrue(CONSERVATIVE_SESSION.fullmatch(first['session_id']))
        self.assertTrue(CONSERVATIVE_MESSAGE.fullmatch(first['message_id']))
        first['session_id'] = 'mutated'
        self.assertNotEqual(routing.derive_ids('attempt-1')['session_id'], 'mutated')

    def test_ids_are_unique_over_a_hundred_thousand_attempts(self):
        sessions, messages = set(), set()
        for i in range(100_000):
            ids = routing.derive_ids('attempt-%d' % i)
            sessions.add(ids['session_id'][4:])
            messages.add(ids['message_id'][4:])
        self.assertEqual((len(sessions), len(messages)), (100_000, 100_000))
        self.assertFalse(sessions & messages)  # the session and message spaces are domain-separated

    def test_near_identical_attempts_differ_and_bad_input_is_refused(self):
        variants = ['a', 'A', 'a ', ' a', 'aé', 'a' * 256, 'b' * 256]
        self.assertEqual(len({routing.derive_ids(v)['session_id'] for v in variants}), len(variants))
        for bad in ('', 'x' * 257, 'tab\tid', None, 7, b'a'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                routing.derive_ids(bad)


class DispatchTests(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')

    def test_dispatch_issues_one_authority_and_moves_the_hold(self):
        response = self.call(dispatch('a1', id='d1'))
        ids = routing.derive_ids('a1')
        self.assertEqual(response['state'], 'dispatched', response)
        self.assertEqual((response['v'], response['id'], response['attempt_id']), (1, 'd1', 'a1'))
        self.assertEqual((response['session_id'], response['message_id']), (ids['session_id'], ids['message_id']))
        self.assertRegex(response['authority'], r'^[A-Za-z0-9_-]{43}$')
        self.assertEqual(response['reconcile_hint'], dict(
            read='message', method='GET', session_id=ids['session_id'], message_id=ids['message_id'],
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
        fixed = dict(session_id='ses_' + '0' * 32, message_id='msg_' + '0' * 32)
        with mock.patch.object(routing, 'derive_ids', return_value=dict(fixed)):
            self.assertEqual(self.call(dispatch('a1'))['state'], 'dispatched')
            self.assertEqual(self.call(dispatch('a2')), dict(v=1, state='blocked', reason='id_collision'))
        self.assertEqual(self.snapshot()['holds']['a2']['state'], 'admitted')


class FundedDispatchTests(Base):
    def setUp(self):
        super().setUp()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(api_usage_enabled=True)))
        budget.initialize(self.ledger)
        self.fund()
        request = prepare('api1', observations=api_observations(), constraints=dict(allowed_strategy_ids=['s-api']))
        self.assertEqual(self.call(request)['admission']['class'], 'api')

    def test_disabled_organization_or_api_usage_blocks_dispatch_and_consumes_nothing(self):
        held = budget.Ledger.held(self.snapshot())
        self.call(dict(v=1, op='set', organization=dict(id='personal', enabled=False)))
        self.assertEqual(self.call(dispatch('api1', request=facts(**API))),
                         dict(v=1, state='blocked', reason='organization_unavailable'))
        self.assertEqual(self.snapshot()['holds']['api1']['state'], 'admitted')
        self.assertEqual(budget.Ledger.held(self.snapshot()), held)

    def test_api_usage_off_blocks_dispatch(self):
        # Through the command the settings revision gate would fire first; this isolates API Usage.
        settings = dict(routing.routing_settings.resolve({}), api_usage_enabled=False, revision=1)
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


class GateSeamTests(Base):
    """S3.5 adds fresh gates through `gates`; a refusal there consumes nothing."""
    def test_an_extra_gate_refuses_inside_the_transaction(self):
        self.call(prepare('a1'))
        seen = []

        def reopened(data, record):
            seen.append(record['attempt_id'])
            return 'dependency_reopened'
        settings = dict(routing.routing_settings.resolve({}), revision=0)
        with self.open_ledger() as ledger:
            result = routing.dispatch(ledger, 'a1', facts(), settings, now(), gates=(reopened,))
            self.assertEqual(result, dict(state='blocked', reason='dependency_reopened'))
            self.assertEqual(seen, ['a1'])
            self.assertEqual(ledger.snapshot()['holds']['a1']['state'], 'admitted')
            passed = routing.dispatch(ledger, 'a1', facts(), settings, now(), gates=(lambda data, record: None,))
        self.assertEqual(passed['state'], 'dispatched')


class CheckAuthorityTests(Base):
    def setUp(self):
        super().setUp()
        self.call(prepare('a1'))
        self.token = self.call(dispatch('a1'))['authority']
        self.ids = routing.derive_ids('a1')

    def test_the_right_token_allows_on_every_path(self):
        for extra in (dict(authority=self.token),
                      dict(authority=self.token, session_id=self.ids['session_id']),
                      dict(authority=self.token, session_id='ses_childsession1')):  # a child session of the attempt
            with self.subTest(extra=sorted(extra)):
                response = self.call(check('a1', **extra))
                self.assertEqual((response['state'], response['authority'], response['attempt_id']),
                                 ('allow', 'verified', 'a1'))
                self.assertNotIn(self.token, json.dumps(response))
        by_session = self.call(dict(v=1, op='check', session_id=self.ids['session_id'], authority=self.token,
                                    request=facts()))
        self.assertEqual((by_session['state'], by_session['attempt_id']), ('allow', 'a1'))

    def test_a_missing_or_wrong_token_blocks(self):
        other = 'x' * 43
        cases = [(check('a1'), 'authority_required'),
                 (check('a1', session_id=self.ids['session_id']), 'authority_required'),
                 (dict(v=1, op='check', session_id=self.ids['session_id'], request=facts()), 'authority_required'),
                 (check('a1', authority=other), 'authority_invalid'),
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
        for session in ('ses_interactive1', routing.derive_ids('never-admitted')['session_id']):
            with self.subTest(session=session):
                self.assertEqual(self.call(dict(v=1, op='check', session_id=session, request=facts(), id='q')),
                                 dict(v=1, state='unmanaged', id='q'))

    def test_another_attempts_session_is_a_mismatch(self):
        self.call(prepare('a2'))
        other = routing.derive_ids('a2')['session_id']
        self.assertEqual(self.call(check('a1', authority=self.token, session_id=other)),
                         dict(v=1, state='blocked', reason='session_mismatch'))

    def test_strict_checks_need_a_dispatched_attempt(self):
        self.call(prepare('a2'))
        session = routing.derive_ids('a2')['session_id']
        for request in (dict(v=1, op='check', session_id=session, request=facts()),
                        check('a2', session_id=session), check('a2', authority=self.token)):
            with self.subTest(request=sorted(request)):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason='dispatch_required'))
        self.assertEqual(self.call(check('a2'))['state'], 'allow')  # the S3.1 attempt-only check is unchanged
        self.call(record('a2', 'dispatched'))  # an S3.1 recorded dispatch carries no authority
        self.assertEqual(self.call(check('a2', session_id=session)),
                         dict(v=1, state='blocked', reason='dispatch_required'))

    def test_closed_uncertain_and_forgotten_sessions_stay_blocked(self):
        session = self.ids['session_id']
        self.call(record('a1', 'uncertain'))
        self.assertEqual(self.call(dict(v=1, op='check', session_id=session, authority=self.token, request=facts())),
                         dict(v=1, state='blocked', reason='attempt_uncertain'))
        with self.open_ledger() as ledger, ledger.transaction() as data:  # pruned hold and record, id remembered
            del data['holds']['a1']
            data['admissions'] = [r for r in data['admissions'] if r['attempt_id'] != 'a1']
        self.assertIn('a1', self.snapshot()['seen_attempts'])
        self.assertEqual(self.call(dict(v=1, op='check', session_id=session, authority=self.token, request=facts())),
                         dict(v=1, state='blocked', reason='attempt_closed'))

    def test_check_stays_read_only(self):
        before = self.snapshot()
        for request in (check('a1', authority=self.token), check('a1'), check('a1', authority='y' * 43),
                        dict(v=1, op='check', session_id='ses_unknown1', request=facts()),
                        dict(v=1, op='check', session_id=self.ids['session_id'], authority=self.token,
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


class LostAcknowledgmentTests(Base):
    def setUp(self):
        super().setUp()
        self.clocked = command.Service(self.settings, self.ledger, test_inputs=True)
        self.addCleanup(self.clocked.close)
        self.at = now()
        self.assertEqual(self.timed(prepare('a1', observations=observations(self.at)))['state'], 'admitted')
        self.ids = routing.derive_ids('a1')

    def timed(self, request, at=None):
        if at is not None:
            request = dict(request, now=at)
        return self.clocked.handle(json.dumps(request).encode())

    def test_a_lost_dispatch_response_cannot_be_recovered(self):
        self.timed(dispatch('a1'), self.at)  # the response is lost: nobody holds the token
        again = self.timed(dispatch('a1'), self.at + 1)
        self.assertEqual((again['state'], again['hold_state']), ('already_dispatched', 'dispatched'))
        self.assertNotIn('authority', again)
        self.assertEqual(again['reconcile_hint']['message_id'], self.ids['message_id'])
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'dispatched')
        self.assertEqual(self.call(dict(v=1, op='check', session_id=self.ids['session_id'], request=facts())),
                         dict(v=1, state='blocked', reason='authority_required'))

    def test_uncertain_keeps_exposure_and_the_concurrency_place(self):
        self.timed(dispatch('a1'), self.at)
        held = budget.Ledger.held(self.snapshot())
        self.assertEqual(self.timed(record('a1', 'uncertain'), self.at + 1)['state'], 'recorded')
        data = self.snapshot()
        self.assertEqual(budget.Ledger.held(data), held)
        self.assertEqual(data['holds']['a1']['dispatch']['uncertain_at'], self.at + 1)
        self.assertEqual(self.timed(prepare('a2', observations=observations(self.at)))['state'], 'admitted')
        third = self.timed(prepare('a3', observations=observations(self.at)))
        self.assertEqual((third['state'], third['reason']), ('pending', 'concurrency_cap'))
        self.assertEqual(self.call(dict(v=1, op='status'))['ledger']['holds'], dict(uncertain=1, admitted=1))

    def test_release_needs_proof(self):
        self.timed(dispatch('a1'), self.at)
        self.assertEqual(self.timed(record('a1', 'not_dispatched'), self.at + 1),
                         dict(v=1, state='blocked', reason='proof_required'))
        self.assertEqual(self.timed(record('a1', 'not_dispatched', proof=proof('a1', self.at + 1)), self.at + 2),
                         dict(v=1, state='blocked', reason='uncertain_required'))
        self.timed(record('a1', 'uncertain'), self.at + 3)
        self.assertEqual(self.timed(record('a1', 'not_dispatched', event_id='nd2'), self.at + 4),
                         dict(v=1, state='blocked', reason='proof_required'))
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'uncertain')

    def test_a_valid_proof_after_the_uncertain_mark_frees_capacity(self):
        self.timed(dispatch('a1'), self.at)
        self.timed(prepare('a2', observations=observations(self.at)))
        self.timed(record('a1', 'uncertain'), self.at + 1)
        before = budget.Ledger.held(self.snapshot())
        response = self.timed(record('a1', 'not_dispatched', proof=proof('a1', self.at + 2)), self.at + 3)
        self.assertEqual(response, dict(v=1, state='recorded', event_id='not_dispatched-a1', duplicate=False))
        hold = self.snapshot()['holds']['a1']
        self.assertEqual((hold['closed'], hold['state']), (True, 'released'))
        self.assertEqual(hold['dispatch']['released'], dict(proof='message_absent', observed_at=self.at + 2))
        self.assertLess(budget.Ledger.held(self.snapshot())['win:sub/5h'], before['win:sub/5h'])
        self.assertEqual(self.call(dict(v=1, op='check', session_id=self.ids['session_id'], request=facts())),
                         dict(v=1, state='blocked', reason='attempt_closed'))
        self.assertEqual(self.timed(prepare('a3', observations=observations(self.at)))['state'], 'admitted')
        self.assertEqual(self.timed(record('a1', 'not_dispatched', proof=proof('a1', self.at + 2)), self.at + 4),
                         dict(v=1, state='blocked', reason='attempt_closed'))

    def test_forged_or_malformed_proofs_are_refused(self):
        self.timed(dispatch('a1'), self.at)
        self.timed(record('a1', 'uncertain'), self.at + 5)
        other = routing.derive_ids('a2')
        cases = [proof('a1', self.at + 5), proof('a1', self.at + 4), proof('a1', self.at + 50),
                 proof('a1', self.at + 6, message_id=other['message_id']),
                 proof('a1', self.at + 6, session_id=other['session_id']),
                 proof('a1', self.at + 6, kind='message_present'), proof('a1', self.at + 6, kind='timeout'),
                 proof('a1', True), proof('a1', -1), proof('a1', 1.5), proof('a1', str(self.at + 6)),
                 dict(proof('a1', self.at + 6), extra=1),
                 {k: v for k, v in proof('a1', self.at + 6).items() if k != 'message_id'},
                 'message_absent', [proof('a1', self.at + 6)], None]
        for value in cases:
            with self.subTest(proof=json.dumps(value)[:90]):
                self.assertEqual(self.timed(record('a1', 'not_dispatched', proof=value), self.at + 10),
                                 dict(v=1, state='blocked', reason='proof_invalid'))
                self.assertEqual(self.snapshot()['holds']['a1']['state'], 'uncertain')
        self.assertEqual(self.timed(record('a1', 'uncertain', event_id='u2', proof=proof('a1', self.at + 6)),
                                    self.at + 10), dict(v=1, state='blocked', reason='invalid_request'))

    def test_proof_does_not_apply_without_an_issued_authority(self):
        self.timed(record('a1', 'dispatched'), self.at)
        self.timed(record('a1', 'uncertain'), self.at + 1)
        self.assertEqual(self.timed(record('a1', 'not_dispatched', proof=proof('a1', self.at + 2)), self.at + 3),
                         dict(v=1, state='blocked', reason='proof_not_applicable'))
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'uncertain')

    def test_s31_events_after_dispatch(self):
        self.timed(dispatch('a1'), self.at)
        self.assertEqual(self.timed(record('a1', 'dispatched'), self.at)['duplicate'], True)
        self.assertEqual(self.timed(record('a1', 'complete'), self.at + 1)['state'], 'recorded')
        self.assertTrue(self.snapshot()['holds']['a1']['closed'])

    def test_a_process_killed_after_commit_leaves_one_dispatch_and_no_token(self):
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


class RaceTests(Base):
    def spawn(self, request):
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'dispatch', '--settings', str(self.settings),
                                  '--ledger', str(self.ledger)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        child.stdin.write(json.dumps(request).encode())
        return child

    def collect(self, children):
        for child in children:
            child.stdin.close()
        results = []
        for child in children:
            results.append(json.loads(child.stdout.read()))
            child.wait(timeout=60)
            child.stdout.close(); child.stderr.close()
        return results

    def test_fifteen_racing_processes_yield_one_authority(self):
        self.assertEqual(self.call(prepare('race'))['state'], 'admitted')
        self.service.close()
        results = self.collect([self.spawn(dispatch('race')) for _ in range(15)])
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
        results = self.collect([self.spawn(dispatch(attempt)) for attempt in attempts])
        self.assertEqual([r['state'] for r in results], ['dispatched'] * 4, results)
        self.assertEqual(len({r['authority'] for r in results}), 4)
        self.assertEqual(sorted(r['attempt_id'] for r in results), sorted(attempts))

    def test_threads_on_separate_ledger_instances_yield_one_authority(self):
        self.call(prepare('t1'))
        settings = dict(routing.routing_settings.resolve({}), revision=0)
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


class ServeDispatchTests(Base):
    def test_serve_handles_dispatch_and_authority_checks_with_ids(self):
        self.call(prepare('s1'))
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
            session = routing.derive_ids('s1')['session_id']
            allowed = ask(dict(v=1, op='check', id='r3', attempt_id='s1', session_id=session,
                               authority=first['authority'], request=facts()))
            unmanaged = ask(dict(v=1, op='check', id='r4', session_id='ses_someoneelse', request=facts()))
        finally:
            child.stdin.close(); child.wait(timeout=60); child.stdout.close(); child.stderr.close()
        self.assertEqual((first['id'], first['state']), ('r1', 'dispatched'))
        self.assertEqual((second['id'], second['state']), (2, 'already_dispatched'))
        self.assertEqual((allowed['id'], allowed['state'], allowed['authority']), ('r3', 'allow', 'verified'))
        self.assertEqual(unmanaged, dict(v=1, id='r4', state='unmanaged'))


class HostileInputTests(Base):
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

    def test_dispatch_without_a_ledger_creates_nothing(self):
        self.assertEqual(self.call(dispatch('a1')), dict(v=1, state='blocked', reason='ledger_absent'))
        self.assertFalse(self.ledger.exists())


class DispatchCapacityTests(Base):
    def test_dispatch_fields_stay_within_their_fixed_allowance(self):
        hold = dict(dispatch=dict(session_id='ses_' + 'f' * 32, message_id='msg_' + 'f' * 32, authority='f' * 64,
                                  at=budget.MAX_INTEGER, uncertain_at=budget.MAX_INTEGER,
                                  released=dict(proof='message_absent', observed_at=budget.MAX_INTEGER)))
        self.assertLessEqual(len(json.dumps(hold, separators=(',', ':'))) - len('{}'), budget.DISPATCH_HOLD_BYTES)


if __name__ == '__main__':
    unittest.main()
