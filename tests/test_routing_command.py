"""S3.1: the single local routing command (`scripts/routing.py`) every client calls."""
import io
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import urllib.request

from runtime import usage_budget as budget, usage_credentials
from scripts import routing as command

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'routing.py'
SCOPE = 'scope1'
SECRET = 'SECRET-PROMPT-TEXT-do-not-echo'
KEY_REF = 'keychain:SECRET-KEY-REF'
CHOICE = dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')


def now():
    return int(time.time())


def job(**extra):
    profile = dict(task_family='code', complexity_band='medium', risk_band='medium', review_policy='independent',
                   context_tokens=100, tools=['edit'], uncertainty='low')
    value = dict(id='job-1', role='worker', scope_ref=SCOPE, objective=SECRET, context=SECRET, profile=profile)
    value.update(extra)
    return value


M = 1_000_000
POLICY = dict(cycle='c', start=0, end=2 * 10**10, expiry=2 * 10**10, stop_at=10**10, ceiling='none', amount=None,
              grant_amount=0, pilot_limit=None, task_limit=None)


def table():
    sub = dict(strategy_id='s-sub', model='m', effort='high', harness='h', evidence_refs=['g1'])
    api = dict(strategy_id='s-api', model='m', effort='high', harness='api', evidence_refs=['g1'])
    return dict(schema=1, version='t1', rows={'worker|code|medium|medium': [sub, api]})


def api_observations(at=None, organization='personal'):
    value = observations(at)
    value['routes'].append(dict(route='api', strategy_id='s-api', provider='anthropic', model='m', effort='high',
                                harness='api', account='anthropic:org', billing_class='api_grant', pool='api',
                                credential=dict(ref=KEY_REF, scope=SCOPE, state='ready'), funding_verified=True,
                                demand={'credit': 5 * M}, capable=True, permitted=True))
    value['pools']['api'] = dict(billing_class='api_grant', windows=[dict(
        id='credit', limit=30 * M, used=0, length=None, reset_at=None, fresh=True, organization=organization,
        organization_revision=1)])
    return value


def observations(at=None, **route):
    spec = dict(route='sub', strategy_id='s-sub', provider='anthropic', model='m', effort='high', harness='h',
                account='anthropic:acct', billing_class='included_subscription', pool='sub', demand={'5h': 10},
                capable=True, permitted=True, binding_enforceable=True)
    spec.update(route)
    window = dict(id='5h', limit=1000, used=100, length=18000, reset_at=None, rolling=True, fresh=True)
    return dict(now=now() if at is None else at, routes=[spec], pools=dict(sub=dict(windows=[window])))


def prepare(attempt, **extra):
    request = dict(v=1, op='prepare', assignment=dict(attempt_id=attempt, task='task-' + attempt, role='implementer',
                                                      scope=SCOPE),
                   job=job(), observations=observations())
    request.update(extra)
    return request


def facts(**change):
    value = dict(provider='anthropic', model='m', variant='high', harness='h', kind='primary', funding_pool='sub')
    value.update(change)
    return value


def check(attempt, **extra):
    request = dict(v=1, op='check', attempt_id=attempt, request=facts())
    request.update(extra)
    return request


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = root / 'routing' / 'settings.json'
        self.ledger = root / 'usage' / 'organizations.sqlite3'
        self.store_table(table())
        self.service = command.Service(self.settings, self.ledger)

    def store_table(self, value):
        """The compiled strategy table lives next to the settings, private like them."""
        self.settings.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.settings.parent / 'strategy-table.json'
        if value is None:
            path.unlink(missing_ok=True)
            return
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def open_ledger(self):
        identity = json.loads(__import__('sqlite3').connect(self.ledger).execute(
            'SELECT body FROM ledger WHERE id=1').fetchone()[0])['identity']
        return budget.Ledger(self.ledger, identity)

    def fund(self, organization='personal'):
        """The user's explicit enablement plus a fresh balance observation (the collector's job)."""
        with self.open_ledger() as ledger:
            ledger.enable_organization(organization, POLICY, 0)
            ledger.observe(organization, 1000 * M, now(), issued=1000 * M)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def call(self, request):
        raw = request if isinstance(request, bytes) else json.dumps(request).encode()
        return self.service.handle(raw)

    def run_cli(self, args, data):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), *args, '--settings', str(self.settings),
                               '--ledger', str(self.ledger)], input=raw, capture_output=True, timeout=60)
        return done

    def release(self, attempt):
        """Free the attempt's concurrency place (default cap: two open attempts per account)."""
        event = dict(event_id='nd-' + attempt, attempt_id=attempt, kind='not_dispatched')
        self.assertEqual(self.call(dict(v=1, op='record', event=event))['state'], 'recorded')

    def snapshot(self):
        with self.open_ledger() as ledger:
            return ledger.snapshot()


class MalformedInputTests(Base):
    def assertBlocked(self, response, reason):
        self.assertEqual(response, dict(v=1, state='blocked', reason=reason))

    def test_malformed_inputs_are_blocked_with_fixed_reasons(self):
        cases = [(b'{not json', 'invalid_json'), (b'[1, 2]', 'invalid_request'), (b'', 'invalid_json'),
                 (b'{"v": 1, "op": "status", "op": "set"}', 'invalid_json'),
                 (b'{"v": NaN, "op": "status"}', 'invalid_json'),
                 (b'\xff\xfe', 'invalid_json'),
                 (json.dumps(dict(op='status')).encode(), 'unsupported_version'),
                 (json.dumps(dict(v=2, op='status')).encode(), 'unsupported_version'),
                 (json.dumps(dict(v=True, op='status')).encode(), 'unsupported_version'),
                 (json.dumps(dict(v=1, op='launch')).encode(), 'unknown_op'),
                 (json.dumps(dict(v=1, op='status', extra=1)).encode(), 'invalid_request'),
                 (json.dumps(dict(v=1, op='check', attempt_id=7, request=facts())).encode(), 'invalid_request'),
                 (json.dumps(prepare('t', table=table())).encode(), 'invalid_request'),
                 (json.dumps(check('a', request=facts(kind='tool'))).encode(), 'unsupported_kind'),
                 (b'{"v": 1, "op": "status", "pad": "' + b'x' * command.MAX_INPUT + b'"}', 'request_too_large')]
        for raw, reason in cases:
            with self.subTest(reason=reason, raw=raw[:40]):
                self.assertBlocked(self.call(raw), reason)
        # Deep nesting either exceeds the parser's recursion limit or parses to a non-object: both fixed refusals.
        self.assertIn(self.call(b'[' * 100000 + b']' * 100000)['reason'], ('invalid_json', 'invalid_request'))

    def test_cli_blocks_without_traceback_and_exits_zero(self):
        for args, raw, reason in ((['status'], b'{oops', 'invalid_json'), (['launch'], b'{"v":1}', 'unknown_op'),
                                  (['status'], b'{"v":9}', 'unsupported_version'),
                                  (['check'], b'{"v":1,"op":"status"}', 'invalid_request'),
                                  (['status'], b'x' * (command.MAX_INPUT + 10), 'request_too_large')):
            with self.subTest(args=args, reason=reason):
                done = self.run_cli(args, raw)
                self.assertEqual(done.returncode, 0)
                self.assertEqual(done.stderr, b'')
                self.assertEqual(json.loads(done.stdout), dict(v=1, state='blocked', reason=reason))

    def test_usage_errors_exit_nonzero_without_traceback(self):
        for args in ([], ['status', '--ledger'], ['status', '--bogus', 'x']):
            with self.subTest(args=args):
                done = subprocess.run([sys.executable, '-B', str(SCRIPT), *args], input=b'{}', capture_output=True,
                                      timeout=60)
                self.assertNotEqual(done.returncode, 0)
                self.assertNotIn(b'Traceback', done.stderr + done.stdout)
                self.assertEqual(json.loads(done.stdout), dict(v=1, state='blocked', reason='usage_error'))

    def test_an_internal_failure_is_blocked_not_raised(self):
        with mock.patch.object(command.Service, '_status', side_effect=RuntimeError('boom ' + SECRET)):
            response = self.call(dict(v=1, op='status'))
        self.assertEqual(response, dict(v=1, state='blocked', reason='internal_error'))


class LocalOnlyTests(Base):
    """RF5: status and set read and write local files only."""
    def guarded(self):
        forbidden = mock.Mock(side_effect=AssertionError('forbidden call'))
        patches = [mock.patch.object(socket, 'socket', forbidden), mock.patch.object(socket, 'create_connection', forbidden),
                   mock.patch.object(subprocess, 'Popen', forbidden), mock.patch.object(subprocess, 'run', forbidden),
                   mock.patch.object(urllib.request, 'urlopen', forbidden),
                   mock.patch.object(usage_credentials, 'resolve_key', forbidden),
                   mock.patch.object(usage_credentials, 'store_key', forbidden),
                   mock.patch.object(usage_credentials, '_read_key', forbidden),
                   mock.patch.object(usage_credentials, '_native_keychain', forbidden)]
        return forbidden, patches

    def test_status_and_set_make_no_model_network_subprocess_or_secret_store_call(self):
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')
        forbidden, patches = self.guarded()
        for patch in patches:
            patch.start()
        try:
            status = self.call(dict(v=1, op='status'))
            changed = self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=False)))
            again = self.call(dict(v=1, op='status'))
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual(forbidden.call_count, 0)
        self.assertEqual((status['state'], changed['state'], again['state']), ('ok', 'updated', 'ok'))
        self.assertIs(again['settings']['smart_routing'], False)
        self.assertEqual(again['settings_revision'], 1)
        self.assertEqual(again['ledger']['state'], 'ready')
        self.assertEqual(again['ledger']['holds'], dict(admitted=1))

    def test_status_without_ledger_or_settings_reports_defaults_and_creates_nothing(self):
        status = self.call(dict(v=1, op='status'))
        self.assertEqual(status['state'], 'ok')
        self.assertIs(status['settings']['smart_routing'], True)
        self.assertIs(status['settings']['api_usage_enabled'], False)
        self.assertEqual(status['settings_revision'], 0)
        self.assertEqual(status['ledger'], dict(state='absent'))
        self.assertFalse(self.ledger.exists() or self.settings.exists())


class SettingsTests(Base):
    def test_stale_expected_revision_is_refused(self):
        self.assertEqual(self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=False)))['revision'], 1)
        refused = self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=True)))
        self.assertEqual(refused, dict(v=1, state='blocked', reason='stale_revision'))
        self.assertIs(self.call(dict(v=1, op='status'))['settings']['smart_routing'], False)

    def test_settings_changes_validate(self):
        ok = self.call(dict(v=1, op='set', expected_revision=0,
                            change=dict(api_usage_enabled=True, worker_choices=dict(implementer=[CHOICE]),
                                        caps=dict(total=3, per_account=1))))
        self.assertEqual((ok['state'], ok['revision']), ('updated', 1))
        self.assertEqual(ok['settings']['caps'], dict(total=3, per_account=1))
        for change, reason in ((dict(smart_routing='no'), 'invalid_boolean'), (dict(bogus=1), 'unknown_setting'),
                               ({}, 'invalid_change'), (dict(caps=dict(total=0, per_account=1)), 'invalid_caps')):
            with self.subTest(change=change):
                self.assertEqual(self.call(dict(v=1, op='set', expected_revision=1, change=change)),
                                 dict(v=1, state='blocked', reason=reason))

    def test_routing_on_never_creates_or_enables_an_organization(self):
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=True, api_usage_enabled=True)))
        self.call(prepare('a1'))
        self.assertEqual(self.snapshot()['organizations'], {})

    def test_organization_enablement_uses_the_ledger_paths(self):
        policy = dict(cycle='c', start=0, end=2 * 10**10, expiry=2 * 10**10, stop_at=10**10, ceiling='none',
                      amount=None, grant_amount=0, pilot_limit=None, task_limit=None)
        blocked = self.call(dict(v=1, op='set', organization=dict(id='personal', enabled=False)))
        self.assertEqual(blocked, dict(v=1, state='blocked', reason='organization_unconfigured'))
        self.call(prepare('a1'))  # creates the ledger, not an organization
        self.assertEqual(self.snapshot()['organizations'], {})
        enabled = self.call(dict(v=1, op='set', organization=dict(id='personal', enabled=True, policy=policy,
                                                                  expected_revision=0)))
        self.assertEqual((enabled['state'], enabled['organization']), ('updated', dict(id='personal', enabled=True,
                                                                                       revision=1)))
        disabled = self.call(dict(v=1, op='set', organization=dict(id='personal', enabled=False)))
        self.assertEqual(disabled['organization'], dict(id='personal', enabled=False))
        self.assertTrue(self.snapshot()['organizations']['personal']['disabled'])
        self.assertEqual(self.call(dict(v=1, op='status'))['ledger']['organizations'],
                         [dict(id='personal', enabled=False)])


class PrepareTests(Base):
    def test_prepare_admits_a_subscription_route_with_api_usage_off_and_no_organizations(self):
        response = self.call(prepare('a1'))
        self.assertEqual(response['state'], 'admitted')
        self.assertIs(response['duplicate'], False)
        admission = response['admission']
        self.assertEqual((admission['attempt_id'], admission['route'], admission['class'], admission['mode'],
                          admission['settings_revision']), ('a1', 'sub', 'subscription', 'ranked', 0))
        self.assertEqual(response['ranking']['strategy_ids'], ['s-sub', 's-api'])
        self.assertEqual(self.snapshot()['organizations'], {})

    def test_no_recommendation_falls_back_to_worker_choices(self):
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(worker_choices=dict(implementer=[CHOICE]))))
        for label, stored in (('empty', dict(schema=1, version='t0', rows={})), ('absent', None),
                              ('unreadable', 'not a table')):
            with self.subTest(label):
                self.store_table(stored)
                response = self.call(prepare('rf8-' + label))
                self.assertEqual(response['state'], 'admitted')
                self.assertEqual(response['admission']['mode'], 'worker_choice')
                self.assertIn('no_recommendation', response['ranking']['reason_codes'])
                self.release('rf8-' + label)

    def test_pending_and_profile_failures_report_fixed_reasons(self):
        self.store_table(None)
        none = self.call(prepare('a-none'))
        self.assertEqual((none['state'], none['reason']), ('pending', 'worker_choice_unavailable'))
        bad = self.call(prepare('a-bad', job=job(role='oracle')))
        self.assertEqual(bad, dict(v=1, state='blocked', reason='profile_unavailable'))
        shape = self.call(prepare('a-shape', observations=dict(now='soon', routes=[], pools={})))
        self.assertEqual(shape, dict(v=1, state='blocked', reason='invalid_observation'))

    def test_repeated_prepare_returns_the_existing_decision(self):
        first = self.call(prepare('a1'))
        second = self.call(prepare('a1'))
        self.assertIs(second['duplicate'], True)
        self.assertEqual(first['admission'], second['admission'])
        self.assertEqual(len(self.snapshot()['holds']), 1)

    def test_responses_never_contain_secrets_or_prompts(self):
        obs = observations()
        obs['routes'][0].update(credential=dict(ref=KEY_REF, scope=SCOPE, state='ready'))
        texts = [json.dumps(self.call(prepare('a1', observations=obs))),
                 json.dumps(self.call(check('a1'))), json.dumps(self.call(dict(v=1, op='status')))]
        for text in texts:
            for needle in (SECRET, KEY_REF, 'anthropic:acct'):
                self.assertNotIn(needle, text)


class CheckTests(Base):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.call(prepare('a1'))['state'], 'admitted')

    def test_a_live_admission_allows_a_matching_request(self):
        response = self.call(check('a1'))
        self.assertEqual(response['state'], 'allow')
        self.assertEqual((response['attempt_id'], response['route'], response['remaining']), ('a1', 'sub', {'win:sub/5h': 12}))
        self.assertNotIn('authority', response)  # S3.4 adds single-use dispatch authority

    def test_check_is_read_only(self):
        before = self.snapshot()
        for _ in range(3):
            self.call(check('a1'))
        self.call(check('nope'))
        self.assertEqual(self.snapshot(), before)

    def test_check_blocks_with_fixed_reasons(self):
        cases = [(check('nope'), 'unknown_attempt'),
                 (check('a1', request=facts(model='other')), 'route_mismatch'),
                 (check('a1', request=facts(variant='low')), 'route_mismatch'),
                 (check('a1', request=facts(harness='x')), 'route_mismatch'),
                 (check('a1', request=facts(funding_pool='api')), 'route_mismatch'),
                 (check('a1', request=facts(kind='teleport')), 'unsupported_kind'),
                 (check('a1', request=facts(bound={'win:sub/5h': 13})), 'envelope_exhausted'),
                 (check('a1', request=facts(bound={'win:other/5h': 1})), 'envelope_exhausted')]
        for request, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason=reason))
        self.assertEqual(self.call(check('a1', request=facts(bound={'win:sub/5h': 12})))['state'], 'allow')

    def test_closed_attempt_is_blocked(self):
        recorded = self.call(dict(v=1, op='record', event=dict(event_id='e1', attempt_id='a1', kind='not_dispatched')))
        self.assertEqual(recorded['state'], 'recorded')
        self.assertEqual(self.call(check('a1')), dict(v=1, state='blocked', reason='attempt_closed'))

    def test_uncertain_attempt_is_blocked(self):
        self.call(dict(v=1, op='record', event=dict(event_id='e1', attempt_id='a1', kind='uncertain')))
        self.assertEqual(self.call(check('a1')), dict(v=1, state='blocked', reason='attempt_uncertain'))

    def test_turning_smart_routing_off_invalidates_unsent_admissions_and_keeps_running_claims(self):
        self.assertEqual(self.call(prepare('run'))['state'], 'admitted')
        # The default cap is two open attempts per account, so the third uses another account.
        self.assertEqual(self.call(prepare('lost', observations=observations(account='other:acct')))['state'],
                         'admitted')
        self.call(dict(v=1, op='record', event=dict(event_id='e-run', attempt_id='run', kind='dispatched')))
        self.call(dict(v=1, op='record', event=dict(event_id='e-lost', attempt_id='lost', kind='uncertain')))
        held = budget.Ledger.held(self.snapshot())
        changed = self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=False)))
        self.assertEqual((changed['state'], changed['revision']), ('updated', 1))
        self.assertEqual(changed['invalidated_attempts'], ['a1'])
        self.assertEqual(self.call(check('a1')), dict(v=1, state='blocked', reason='settings_revision_changed'))
        self.assertEqual(self.call(check('run'))['state'], 'allow')  # started workers finish within budget
        data = self.snapshot()
        self.assertEqual(budget.Ledger.held(data), held)  # nothing released without established non-dispatch
        self.assertEqual({k: h['state'] for k, h in data['holds'].items()},
                         dict(a1='admitted', run='dispatched', lost='uncertain'))
        # Re-enabling never revives the stale decision.
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(smart_routing=True)))
        self.assertEqual(self.call(check('a1')), dict(v=1, state='blocked', reason='settings_revision_changed'))


class RecordTests(Base):
    def setUp(self):
        super().setUp()
        self.call(prepare('a1'))

    def event(self, kind, event_id='e1', attempt='a1'):
        return self.call(dict(v=1, op='record', event=dict(event_id=event_id, attempt_id=attempt, kind=kind)))

    def test_lifecycle_events_are_idempotent(self):
        self.assertEqual(self.event('dispatched'), dict(v=1, state='recorded', event_id='e1', duplicate=False))
        self.assertEqual(self.event('dispatched'), dict(v=1, state='recorded', event_id='e1', duplicate=True))
        self.assertEqual(self.event('complete', 'e2'), dict(v=1, state='recorded', event_id='e2', duplicate=False))
        self.assertEqual(self.snapshot()['holds']['a1']['closed'], True)
        self.assertEqual(self.event('complete', 'e2'), dict(v=1, state='blocked', reason='attempt_closed'))

    def test_unsupported_or_bad_events_are_blocked(self):
        self.assertEqual(self.event('usage'), dict(v=1, state='blocked', reason='record_unsupported'))
        self.assertEqual(self.event('dispatched', attempt='nope'), dict(v=1, state='blocked', reason='unknown_attempt'))
        self.assertEqual(self.event('complete'), dict(v=1, state='blocked', reason='hold_state_forbidden'))
        self.assertEqual(self.call(dict(v=1, op='record', event=dict(event_id='e', attempt_id='a1', kind='dispatched',
                                                                     prompt=SECRET))),
                         dict(v=1, state='blocked', reason='invalid_request'))


class ServeTests(Base):
    def serve(self):
        return subprocess.Popen([sys.executable, '-B', str(SCRIPT), '--serve', '--settings', str(self.settings),
                                 '--ledger', str(self.ledger)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

    def test_many_requests_with_id_echo_bad_lines_and_clean_eof(self):
        child = self.serve()
        lines = [json.dumps(dict(prepare('s1'), id='r1')).encode(), b'{broken',
                 json.dumps(dict(check('s1'), id=2)).encode(),
                 json.dumps(dict(v=1, op='status', id='r3')).encode(),
                 b'x' * (command.MAX_INPUT + 5),
                 json.dumps(dict(v=1, op='nope', id='r5')).encode(),
                 json.dumps(dict(check('missing'), id='r6')).encode()]
        out, err = child.communicate(b'\n'.join(lines) + b'\n', timeout=60)
        self.assertEqual(child.returncode, 0)
        self.assertNotIn(b'Traceback', err)
        responses = [json.loads(line) for line in out.splitlines()]
        self.assertEqual(len(responses), len(lines))
        self.assertEqual([r.get('id') for r in responses], ['r1', None, 2, 'r3', None, 'r5', 'r6'])
        self.assertEqual([r['state'] for r in responses], ['admitted', 'blocked', 'allow', 'ok', 'blocked', 'blocked',
                                                           'blocked'])
        self.assertEqual([r.get('reason') for r in responses[4:]], ['request_too_large', 'unknown_op', 'unknown_attempt'])
        self.assertTrue(all(r['v'] == 1 for r in responses))

    def test_bad_id_is_blocked_without_echo(self):
        child = self.serve()
        out, _ = child.communicate(json.dumps(dict(v=1, op='status', id=['x'])).encode() + b'\n', timeout=60)
        self.assertEqual(json.loads(out), dict(v=1, state='blocked', reason='invalid_id'))

    def test_in_process_serve_loop(self):
        stdin = io.BytesIO(json.dumps(dict(v=1, op='status', id='a')).encode() + b'\n\n')
        stdout = io.BytesIO()
        self.assertEqual(command.serve(self.service, stdin, stdout), 0)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([r.get('id') for r in responses], ['a', None])
        self.assertEqual(responses[1]['reason'], 'invalid_json')


class RaceAndLatencyTests(Base):
    def test_fifteen_racing_processes_yield_one_admission(self):
        identity = budget.initialize(self.ledger)
        self.assertTrue(identity)
        raw = json.dumps(prepare('race')).encode()
        children = [subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'prepare', '--settings', str(self.settings),
                                      '--ledger', str(self.ledger)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE) for _ in range(15)]
        for child in children:
            child.stdin.write(raw)
            child.stdin.close()
        results = []
        for child in children:
            results.append(json.loads(child.stdout.read()))
            child.wait(timeout=60)
            child.stdout.close(); child.stderr.close()
        self.assertTrue(all(r['state'] == 'admitted' for r in results), results)
        self.assertEqual(sorted(r['duplicate'] for r in results), [False] + [True] * 14)
        self.assertEqual(len({json.dumps(r['admission'], sort_keys=True) for r in results}), 1)
        self.assertEqual(list(self.snapshot()['holds']), ['race'])

    def test_check_latency_is_well_under_the_hook_budget(self):
        self.call(prepare('lat'))
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT), '--serve', '--settings', str(self.settings),
                                  '--ledger', str(self.ledger)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        line = json.dumps(check('lat')).encode() + b'\n'
        served = []
        try:
            for _ in range(200):
                start = time.perf_counter()
                child.stdin.write(line); child.stdin.flush()
                self.assertEqual(json.loads(child.stdout.readline())['state'], 'allow')
                served.append((time.perf_counter() - start) * 1000)
        finally:
            child.stdin.close(); child.wait(timeout=60); child.stdout.close(); child.stderr.close()
        spawned = []
        for _ in range(10):
            start = time.perf_counter()
            done = self.run_cli(['check'], check('lat'))
            spawned.append((time.perf_counter() - start) * 1000)
            self.assertEqual(json.loads(done.stdout)['state'], 'allow')
        served.sort(); spawned.sort()
        report = dict(serve_median_ms=round(statistics.median(served), 3), serve_p95_ms=round(served[189], 3),
                      spawn_median_ms=round(statistics.median(spawned), 1), spawn_max_ms=round(spawned[-1], 1))
        print('\nrouting check latency:', json.dumps(report), file=sys.stderr)
        self.assertLess(statistics.median(served), 500)



class ClockTests(Base):
    """The command stamps and judges admissions with its own clock; callers cannot stretch an admission."""
    def test_prepare_refuses_observations_off_the_command_clock(self):
        for label, at in (('future', now() + 10**9), ('milliseconds', now() * 1000), ('old', now() - 10**6)):
            with self.subTest(label):
                self.assertEqual(self.call(prepare(label, observations=observations(at))),
                                 dict(v=1, state='blocked', reason='observation_stale'))

    def test_admission_time_is_the_command_clock(self):
        before = now()
        response = self.call(prepare('a1', observations=observations(now() - 30)))
        self.assertGreaterEqual(response['admission']['at'], before)

    def test_check_ignores_a_caller_clock_in_production(self):
        self.call(prepare('a1'))
        for at in (0, now() + 10**7):
            self.assertEqual(self.call(check('a1', now=at))['state'], 'allow')

    def test_switch_honours_caller_clocks_and_the_ttl_boundary(self):
        service = command.Service(self.settings, self.ledger, test_inputs=True)
        try:
            at = now()
            request = prepare('a1', observations=observations(at), table=table())
            self.assertEqual(service.handle(json.dumps(request).encode())['admission']['at'], at)
            ttl = command.routing.ADMISSION_TTL
            for offset, expected in ((ttl, 'allow'), (ttl + 1, 'admission_expired'), (-1, 'clock_invalid')):
                with self.subTest(offset=offset):
                    response = service.handle(json.dumps(check('a1', now=at + offset)).encode())
                    self.assertEqual(response.get('reason', response['state']), expected)
        finally:
            service.close()

    def test_cli_switch_is_an_explicit_flag_with_pinned_paths(self):
        self.call(prepare('a1'))
        late = check('a1', now=now() + 10**7)
        # The environment cannot turn test inputs on.
        inherited = subprocess.run([sys.executable, '-B', str(SCRIPT), 'check', '--settings', str(self.settings),
                                    '--ledger', str(self.ledger)], input=json.dumps(late).encode(),
                                   capture_output=True, timeout=60,
                                   env=dict(os.environ, VIBE_ROUTING_TEST_INPUTS='1'))
        self.assertEqual(json.loads(inherited.stdout)['state'], 'allow')
        self.assertEqual(json.loads(self.run_cli(['check', '--test-inputs'], late).stdout)['reason'],
                         'admission_expired')
        status = json.loads(self.run_cli(['status', '--test-inputs'], dict(v=1, op='status')).stdout)
        self.assertIs(status['test_inputs'], True)
        self.assertIs(json.loads(self.run_cli(['status'], dict(v=1, op='status')).stdout)['test_inputs'], False)
        # Refused unless both stores are pinned explicitly, away from the production defaults.
        for args in (['check', '--test-inputs'], ['check', '--test-inputs', '--ledger', str(self.ledger)],
                     ['check', '--test-inputs', '--settings', str(command.default_settings()),
                      '--ledger', str(self.ledger)]):
            with self.subTest(args=args):
                done = subprocess.run([sys.executable, '-B', str(SCRIPT), *args], input=json.dumps(late).encode(),
                                      capture_output=True, timeout=60)
                self.assertEqual(done.returncode, 2)
                self.assertEqual(json.loads(done.stdout), dict(v=1, state='blocked', reason='usage_error'))


API = dict(harness='api', funding_pool='api')


class FundingCheckTests(Base):
    """Every check on a dollar-funded hold re-reads the organization and API Usage; subscriptions do not."""
    def setUp(self):
        super().setUp()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(api_usage_enabled=True)))
        budget.initialize(self.ledger)
        self.fund()

    def admit(self, attempt, strategy='s-api', service=None):
        request = prepare(attempt, observations=api_observations(),
                          constraints=dict(allowed_strategy_ids=[strategy]))
        response = (service or self.service).handle(json.dumps(request).encode())
        self.assertEqual(response['state'], 'admitted', response)
        return response

    def dispatch(self, attempt):
        self.call(dict(v=1, op='record', event=dict(event_id='d-' + attempt, attempt_id=attempt, kind='dispatched')))

    def test_a_funded_api_hold_allows(self):
        self.assertEqual(self.admit('api1')['admission']['class'], 'api')
        response = self.call(check('api1', request=facts(**API)))
        self.assertEqual((response['state'], response['remaining']), ('allow', {'org:personal': 6 * M}))

    def test_disabling_the_organization_blocks_unsent_and_dispatched_api_work(self):
        self.admit('api1')
        self.admit('api2')
        self.dispatch('api2')
        self.admit('sub1', 's-sub')
        self.call(dict(v=1, op='set', organization=dict(id='personal', enabled=False)))
        for attempt in ('api1', 'api2'):
            self.assertEqual(self.call(check(attempt, request=facts(**API))),
                             dict(v=1, state='blocked', reason='organization_unavailable'))
        self.assertEqual(self.call(check('sub1'))['state'], 'allow')  # subscription holds are unaffected

    def test_api_usage_off_blocks_dispatched_api_holds_but_not_subscription_ones(self):
        self.admit('api1')
        self.admit('sub1', 's-sub')
        self.dispatch('api1')
        self.dispatch('sub1')
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(api_usage_enabled=False)))
        self.assertEqual(self.call(check('api1', request=facts(**API))),
                         dict(v=1, state='blocked', reason='api_usage_disabled'))
        self.assertEqual(self.call(check('sub1'))['state'], 'allow')

    def test_smart_routing_off_keeps_a_funded_dispatched_api_worker(self):
        self.admit('api1')
        self.dispatch('api1')
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(smart_routing=False)))
        self.assertEqual(self.call(check('api1', request=facts(**API)))['state'], 'allow')

    def test_in_flight_spend_is_counted_twice_until_s36(self):
        """Pins today's conservative semantics: a dispatched 6M hold on a 10M balance stops once the collector
        reports 5M, though only ~1M of the hold is still at risk. S3.6 records consumption and replaces this."""
        with self.open_ledger() as ledger:
            ledger.enable_organization('small', dict(POLICY, allowance_bp=10000), 0)
            ledger.observe('small', 10 * M, now(), issued=10 * M)
        request = prepare('api1', observations=api_observations(organization='small'),
                          constraints=dict(allowed_strategy_ids=['s-api']))
        self.assertEqual(self.call(request)['admission']['reserved'], {'org:small': 6 * M})
        self.dispatch('api1')
        self.assertEqual(self.call(check('api1', request=facts(**API)))['state'], 'allow')
        with self.open_ledger() as ledger:
            ledger.observe('small', 5 * M, now() + 1)
        self.assertEqual(self.call(check('api1', request=facts(**API))),
                         dict(v=1, state='blocked', reason='funding_unverified'))

    def test_blocked_stale_short_or_expired_funding_blocks_dispatched_api_work(self):
        service = command.Service(self.settings, self.ledger, test_inputs=True)
        self.addCleanup(service.close)
        self.admit('api1', service=service)
        self.dispatch('api1')

        def at(moment):
            return service.handle(json.dumps(check('api1', request=facts(**API), now=moment)).encode())
        self.assertEqual(at(now() + budget.FRESH_SECONDS + 60)['reason'], 'funding_unverified')  # stale balance
        with self.open_ledger() as ledger:
            ledger.observe('personal', 1 * M, now() + 1)  # the balance no longer covers what is held
        self.assertEqual(at(now() + 2)['reason'], 'funding_unverified')
        with self.open_ledger() as ledger:
            ledger.observe('personal', 1 * M, POLICY['stop_at'] + 5)
        self.assertEqual(at(POLICY['stop_at'] + 10)['reason'], 'funding_unverified')  # past the cycle boundary
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data['organizations']['personal']['blocked'] = True
        self.assertEqual(at(POLICY['stop_at'] + 10)['reason'], 'organization_unavailable')



class TableTests(Base):
    """A bad stored table falls back to worker choices exactly like a missing one; it never blocks."""
    def setUp(self):
        super().setUp()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(worker_choices=dict(implementer=[CHOICE]))))

    def raw_table(self, raw, mode=0o600):
        path = self.settings.parent / 'strategy-table.json'
        path.unlink(missing_ok=True)
        path.write_bytes(raw)
        path.chmod(mode)
        return path

    def assertFallsBack(self, attempt):
        response = self.call(prepare(attempt))
        self.assertEqual((response['state'], response['admission']['mode']), ('admitted', 'worker_choice'), response)
        self.assertIn('table_unavailable', response['ranking']['reason_codes'])
        self.release(attempt)

    def test_corrupt_strategy_ids_or_version_fall_back(self):
        good = dict(strategy_id='s-sub', model='m', effort='high', harness='h')
        for label, value in (('numeric id', dict(version='t', rows={'worker|code|medium|medium': [dict(good, strategy_id=5)]})),
                             ('object version', dict(version={}, rows={'worker|code|medium|medium': [good]})),
                             ('reviewer table', dict(version={}, rows={'worker|code|medium|medium': [dict(good, strategy_id=5)]})),
                             ('empty id', dict(version='t', rows={'worker|code|medium|medium': [dict(good, strategy_id='')]})),
                             ('rows not lists', dict(version='t', rows={'worker|code|medium|medium': 'x'}))):
            with self.subTest(label):
                self.raw_table(json.dumps(value).encode())
                self.assertFallsBack('t-' + label.replace(' ', '-'))

    def test_unreadable_tables_still_fall_back(self):
        folder = self.settings.parent
        cases = [('nan', lambda: self.raw_table(b'{"version": NaN, "rows": {}}')),
                 ('nested', lambda: self.raw_table(b'{"rows": ' + b'[' * 100000 + b']' * 100000 + b'}')),
                 ('huge', lambda: self.raw_table(b'{"rows": {}, "pad": "' + b'x' * (command.MAX_TABLE + 1) + b'"}')),
                 ('world-writable', lambda: self.raw_table(json.dumps(table()).encode(), 0o666))]
        for label, make in cases:
            with self.subTest(label):
                make()
                self.assertFallsBack('u-' + label)
        target = folder / 'real-table.json'
        target.write_text(json.dumps(table()))
        target.chmod(0o600)
        link = folder / 'strategy-table.json'
        link.unlink()
        link.symlink_to(target)
        self.assertFallsBack('u-symlink')
        outside = command.Service(self.settings, self.ledger, folder / '..' / 'missing' / 'table.json')
        self.addCleanup(outside.close)
        response = outside.handle(json.dumps(prepare('u-dotdot')).encode())
        self.assertEqual((response['state'], response['admission']['mode']), ('admitted', 'worker_choice'))

class RoleTests(Base):
    def test_profile_roles_map_to_worker_choice_roles_once(self):
        from runtime import routing_settings
        self.assertEqual(routing_settings.PROFILE_ROLES, dict(worker='implementer', review='reviewer', check='reviewer'))
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(worker_choices=dict(reviewer=[CHOICE]))))
        for role in ('review', 'check'):
            with self.subTest(role):
                request = prepare('r-' + role, job=job(role=role))
                del request['assignment']['role']
                self.assertEqual(self.call(request)['admission']['bucket'][0], 'reviewer')
                self.release('r-' + role)
        request = prepare('w')
        del request['assignment']['role']
        self.assertEqual(self.call(request)['admission']['bucket'][0], 'implementer')

    def test_explicit_roles_coordinator_and_mismatch(self):
        repair = prepare('rep')
        repair['assignment']['role'] = 'repair'
        self.assertEqual(self.call(repair)['admission']['bucket'][0], 'repair')
        mismatch = prepare('mis')
        mismatch['assignment']['role'] = 'reviewer'
        self.assertEqual(self.call(mismatch), dict(v=1, state='blocked', reason='role_mismatch'))
        coordinator = prepare('coord', job=job(role='coordinator'))
        del coordinator['assignment']['role']
        self.assertEqual(self.call(coordinator), dict(v=1, state='blocked', reason='coordinator_not_routed'))


class RobustnessTests(Base):
    def test_uncertain_hold_needs_reconciliation_not_a_bare_complete(self):
        self.call(prepare('a1'))
        for kind in ('dispatched', 'uncertain'):
            self.call(dict(v=1, op='record', event=dict(event_id=kind, attempt_id='a1', kind=kind)))
        self.assertEqual(self.call(dict(v=1, op='record', event=dict(event_id='c', attempt_id='a1', kind='complete'))),
                         dict(v=1, state='blocked', reason='reconciliation_required'))
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'uncertain')

    def test_a_locked_ledger_answers_ledger_busy_quickly(self):
        import sqlite3
        self.call(prepare('a1'))
        fresh = command.Service(self.settings, self.ledger)
        self.addCleanup(fresh.close)
        lock = sqlite3.connect(self.ledger, isolation_level=None)
        lock.execute('BEGIN EXCLUSIVE')
        try:
            for service in (self.service, fresh):
                start = time.perf_counter()
                checked = service.handle(json.dumps(check('a1')).encode())
                status = service.handle(json.dumps(dict(v=1, op='status')).encode())
                elapsed = time.perf_counter() - start
                self.assertEqual(checked, dict(v=1, state='blocked', reason='ledger_busy'))
                self.assertEqual(status['ledger'], dict(state='unavailable', reason='ledger_busy'))
                self.assertLess(elapsed, 1.0)
        finally:
            lock.execute('ROLLBACK')
            lock.close()
        self.assertEqual(self.call(check('a1'))['state'], 'allow')

    def test_closed_stdout_exits_quietly(self):
        child = subprocess.Popen([sys.executable, '-B', str(SCRIPT), 'status', '--settings', str(self.settings),
                                  '--ledger', str(self.ledger)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        child.stdout.close()
        child.stdout = None
        _, err = child.communicate(json.dumps(dict(v=1, op='status')).encode(), timeout=60)
        self.assertEqual((child.returncode, err), (0, b''))

    def test_status_reports_effective_caps(self):
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(caps=dict(total=3, per_account=1))))
        self.assertEqual(self.call(dict(v=1, op='status'))['effective_caps'], dict(total=3, per_account=1))
        self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=dict(total=10, per_account=1))))
        self.assertEqual(self.call(dict(v=1, op='status'))['effective_caps'], dict(total=4, per_account=1))

if __name__ == '__main__':
    unittest.main()
