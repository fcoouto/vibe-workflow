"""Task 6: OpenCode selected-account identity (`runtime/opencode_account.py`) for the three OpenCode subscription paths.

Every OpenCode answer here comes from a labelled synthetic `opencode` double (a private script answering the observed
`/api/credential` and usage-limits shapes with clearly fake, unsigned tokens). No real account, credential, client or
network is touched. Secret hygiene is asserted with sentinel strings that must never leave the reader's memory.
"""
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

from runtime import claude_usage, opencode_account, routing_observations as collector, routing_paths, \
    subscription_billing
from tests import test_native_routing as native_tests
from tests.test_routing_observations import Stores

REAL_POPEN = subprocess.Popen  # served tests guard Popen; only the explicit producer read uses it
SECRETS = ('SENTINEL-ACCESS-SIGNATURE', 'SENTINEL-REFRESH-TOKEN', 'SENTINEL-GO-KEY', 'SENTINEL-CONSOLE-ACCESS',
           'SENTINEL-CONSOLE-REFRESH')
SUBJECT, OTHER = 'synthetic-subject-a', 'synthetic-subject-b'
ACCOUNT, ORG = 'user_' + 'A' * 26, 'org_' + 'B' * 26
ROUTES = dict(  # family: (path, account, pool)
    claude=('opencode-claude-subscription', 'claude-plan', 'claude-subscription'),
    chatgpt=('opencode-chatgpt-subscription', 'chatgpt-plan', 'chatgpt-subscription'),
    go=('opencode-go-subscription', 'opencode-go-workspace', 'opencode-go-subscription'))
FAKE = '''import json, sys, time
config = json.load(open(sys.argv[1]))
with open(config["log"], "a") as log:  # the requested path only: never a value
    log.write(" ".join(sys.argv[2:]) + "\\n")
mode = config.get("mode", "ok")
if mode == "hang":
    time.sleep(30)
if mode == "exit":
    sys.exit(3)
if mode == "oversize":
    sys.stdout.write("x" * (300 << 10))
elif mode == "invalid":
    sys.stdout.write("{not json")
else:
    sys.stdout.write(json.dumps(config["usage"] if sys.argv[2:] == ["api", "GET", "/api/experimental/usage/limits"]
                                else config["credential"]))
'''


def segment(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')


def jwt(sub=SUBJECT, exp=1_900_000_000):
    """A clearly synthetic, unsigned token with the observed claim names; its signature is a secret sentinel."""
    claims = {'iss': 'https://auth.openai.com', 'aud': 'https://api.openai.com/v1', 'client_id': 'oaiapp_x', 'exp': exp,
              'https://api.openai.com/auth': {}}
    if sub is not None:
        claims['sub'] = sub
    return '%s.%s.SENTINEL-ACCESS-SIGNATURE' % (segment({'alg': 'none', 'typ': 'JWT'}), segment(claims))


def credentials(**changes):
    """The observed `/api/credential` shape; `changes[integration]` updates (a dict) or drops (None) an entry."""
    rows = dict(
        claude=dict(id='cred_' + 'C' * 26, integrationID='claude-agent', label='Claude Agent', active=True,
                    value=dict(type='external', methodID='claude-login')),
        openai=dict(id='cred_' + 'D' * 26, integrationID='openai', label='OpenAI', active=True, value=dict(
            type='oauth', methodID='chatgpt-token-sharing', access=jwt(), refresh='SENTINEL-REFRESH-TOKEN', expires=0,
            metadata=dict(clientID='oaiapp_x', scopes=['openid', 'email', 'profile', 'offline_access']))),
        go=dict(id='cred_' + 'E' * 26, integrationID='opencode-go', label='OpenCode Go', active=True,
                value=dict(type='key', key='SENTINEL-GO-KEY-1')),
        console=dict(id='cred_' + 'F' * 26, integrationID='opencode', label='Personal', active=True, value=dict(
            type='oauth', methodID='device', access='SENTINEL-CONSOLE-ACCESS', refresh='SENTINEL-CONSOLE-REFRESH',
            expires=0, metadata=dict(accountID=ACCOUNT, email='someone@example.invalid', orgID=ORG, orgName='Org',
                                     server='https://opencode.ai/console'))))
    for name, change in changes.items():
        if change is None:
            rows.pop(name)
        else:
            rows[name] = json.loads(json.dumps(dict(rows[name], **change)))
    return dict(data=list(rows.values()))


def usage(workspace=ORG):
    window = dict(percent=9, limited=False, resetsAt=1_900_000_000_000)
    return dict(data=dict(workspace=dict(id=workspace, name='Org', usageURL='https://example.invalid/usage'),
                          go=dict(rolling=window, weekly=window, monthly=window), budgets=[]))


def route_of(family):
    path, account, pool = ROUTES[family]
    return dict(route=path + ':choice:0123456789abcdef', account=account, pool=pool,
                billing_class='included_subscription')


class FakeOpenCode:
    """The private `opencode` double: its answers live in a 0600 config file the test rewrites between reads."""

    def __init__(self, folder):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.script, self.config, self.log = folder / 'opencode.py', folder / 'config.json', folder / 'calls.log'
        self.script.write_text(FAKE)
        self.command = [sys.executable, '-I', '-B', str(self.script), str(self.config)]
        self.answer(credentials(), usage())

    def answer(self, credential, limits, mode='ok'):
        self.config.write_text(json.dumps(dict(credential=credential, usage=limits, mode=mode, log=str(self.log))))
        os.chmod(self.config, 0o600)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class Base(Stores):
    def setUp(self):
        super().setUp()
        self.fake = FakeOpenCode(self.root / 'fake')
        self.results = []
        now = int(time.time())  # a labelled synthetic native Claude login (the native producer's own state shape)
        self.native(now, 'a' * 32)

    def native(self, now, generation):
        claude_usage._save(self.settings, identity=dict(key='9' * 32, generation=generation), account=dict(
            key='9' * 32, generation=generation, observed_at=now, client_version='invented-2', overflow='unknown',
            usage_at=None))

    def refresh(self, **options):
        result = opencode_account.refresh(self.settings, **dict(dict(command=self.fake.command), **options))
        self.results.append(result)
        return result

    def fact(self, family):
        return opencode_account.billing_fact(self.settings, route_of(family), int(time.time()))

    def producer(self, family):
        found = self.fact(family)
        return None if found is None else found['producer']

    def assert_clean(self):
        """No secret ever leaves memory: not in the store, a reason, a result or the double's own call log."""
        store = opencode_account._store(self.settings)
        texts = [store.read_text() if store.exists() else '', json.dumps(self.results), '\n'.join(self.fake.calls())]
        for secret in SECRETS + (SUBJECT, OTHER, ACCOUNT, ORG):
            for text in texts:
                self.assertNotIn(secret, text)
        if store.exists():
            self.assertEqual(stat.S_IMODE(store.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(store.parent.stat().st_mode) & 0o077, 0)


class ReaderTest(Base):
    def test_one_bounded_read_proves_all_three_families(self):
        self.assertEqual(self.refresh()['state'], 'stored')
        self.assertEqual(self.fake.calls(), ['api GET /api/credential', 'api GET /api/experimental/usage/limits'])
        now = int(time.time())
        for family in ROUTES:
            with self.subTest(family=family):
                found = self.fact(family)
                self.assertEqual(set(found), subscription_billing.FIELDS)
                self.assertTrue(found['producer'].startswith('opencode-%s:' % family))
                self.assertEqual(found['paid_crossover'], 'unknown')
                self.assertIsNone(subscription_billing.problem(found, route_of(family), now))
                wrong = dict(route_of(family), pool='another-pool')
                self.assertIsNone(opencode_account.billing_fact(self.settings, wrong, now))
        self.assertEqual(self.refresh()['state'], 'fresh')  # current: no second read
        self.assertEqual(len(self.fake.calls()), 2)
        self.assert_clean()

    def test_token_refresh_keeps_the_generation_and_a_changed_subject_never_restores_it(self):
        self.refresh()
        first = self.producer('chatgpt')
        refreshed = credentials(openai=dict(value=dict(credentials()['data'][1]['value'], access=jwt(exp=1_950_000_000))))
        self.fake.answer(refreshed, usage())
        self.refresh(force=True)
        self.assertEqual(self.producer('chatgpt'), first)  # new access token and expiry, same subject
        other = credentials(openai=dict(value=dict(credentials()['data'][1]['value'], access=jwt(sub=OTHER))))
        self.fake.answer(other, usage())
        self.refresh(force=True)
        self.assertNotEqual(self.producer('chatgpt'), first)
        self.fake.answer(credentials(), usage())
        self.refresh(force=True)
        back = self.producer('chatgpt')
        self.assertNotEqual(back.split(':')[2], first.split(':')[2])  # A -> B -> A: a new generation
        self.assertEqual(back.split(':')[1], first.split(':')[1])  # ... for the same key
        self.assert_clean()

    def test_a_replaced_go_key_or_console_account_changes_the_go_generation(self):
        self.refresh()
        first = self.producer('go')
        for change in (dict(go=dict(value=dict(type='key', key='SENTINEL-GO-KEY-2'))),
                       dict(console=dict(value=dict(credentials()['data'][3]['value'], metadata=dict(
                           credentials()['data'][3]['value']['metadata'], accountID='user_' + 'Z' * 26))))):
            with self.subTest(change=sorted(change)):
                self.fake.answer(credentials(**change), usage())
                self.refresh(force=True)
                self.assertNotIn(self.producer('go'), (None, first))
        self.assert_clean()

    def test_each_family_refuses_its_own_unproved_connection(self):
        cases = [  # (family, credential changes, usage, reason)
            ('chatgpt', dict(openai=dict(active=False)), usage(), 'opencode_chatgpt_unsupported'),
            ('chatgpt', dict(openai=None), usage(), 'opencode_chatgpt_unsupported'),
            ('chatgpt', dict(openai=dict(value=dict(type='key', key='SENTINEL-GO-KEY'))), usage(),
             'opencode_chatgpt_unsupported'),
            ('chatgpt', dict(openai=dict(value=dict(credentials()['data'][1]['value'], methodID='api-key'))), usage(),
             'opencode_chatgpt_unsupported'),
            ('chatgpt', dict(openai=dict(value=dict(credentials()['data'][1]['value'], access=jwt(sub=None)))),
             usage(), 'opencode_chatgpt_unsupported'),
            ('chatgpt', dict(openai=dict(value=dict(credentials()['data'][1]['value'], access='not-a-jwt'))),
             usage(), 'opencode_chatgpt_unsupported'),
            ('go', dict(go=dict(active=False)), usage(), 'opencode_go_unsupported'),
            ('go', dict(console=None), usage(), 'opencode_go_unsupported'),
            ('go', dict(console=dict(value=dict(credentials()['data'][3]['value'], metadata=dict(orgID=ORG)))),
             usage(), 'opencode_go_unsupported'),  # no accountID
            ('go', {}, usage(workspace='org_' + 'Y' * 26), 'opencode_go_workspace_mismatch'),
            ('claude', dict(claude=dict(active=False)), usage(), 'opencode_claude_unsupported'),
            ('claude', dict(claude=dict(value=dict(type='oauth', methodID='claude-login'))), usage(),
             'opencode_claude_unsupported'),
            ('claude', dict(claude=dict(value=dict(type='external', methodID='api-key'))), usage(),
             'opencode_claude_unsupported')]
        for family, change, limits, reason in cases:
            with self.subTest(family=family, reason=reason, change=sorted(change)):
                self.fake.answer(credentials(), usage())
                self.refresh(force=True)
                self.assertIsNotNone(self.fact(family))
                self.fake.answer(credentials(**change), limits)
                result = self.refresh(force=True)
                self.assertEqual(result['families'][family], reason)
                self.assertIsNone(self.fact(family))
                for other in set(ROUTES) - {family}:
                    self.assertIsNotNone(self.fact(other), other)  # another family's proof is unaffected
        self.assert_clean()

    def test_a_missing_or_changed_native_claude_login_refuses_opencode_claude(self):
        self.refresh()
        first = self.producer('claude')
        self.native(int(time.time()), 'b' * 32)  # the native login's generation moved after the OpenCode read
        self.assertIsNone(self.fact('claude'))  # rechecked at read time, never the stored digest alone
        self.refresh(families=('claude',))
        self.assertNotIn(self.producer('claude'), (None, first))
        claude_usage._save(self.settings, account=dict(pending=True, generation='c' * 32), identity=None)
        self.assertEqual(self.refresh(force=True, families=('claude',))['families']['claude'],
                         'opencode_claude_unproved')
        self.assertIsNone(self.fact('claude'))
        self.assertIsNotNone(self.fact('chatgpt'))
        self.assert_clean()

    def test_transport_failures_prove_nothing_and_keep_only_proved_continuity(self):
        self.refresh()
        first = {family: self.producer(family) for family in ROUTES}
        for mode, reason in (('exit', 'opencode_exited'), ('oversize', 'opencode_output_invalid'),
                             ('invalid', 'opencode_output_invalid'), ('hang', 'opencode_timeout')):
            with self.subTest(mode=mode):
                self.fake.answer(credentials(), usage(), mode=mode)
                started = time.monotonic()
                result = self.refresh(force=True, timeout=1.0)
                self.assertLess(time.monotonic() - started, 10)
                self.assertEqual((result['state'], result['reason']), ('failed', reason))
                self.assertEqual([self.fact(family) for family in ROUTES], [None, None, None])
                self.assertEqual(self.refresh()['state'], 'backoff')  # no new spawn inside the failure backoff
        missing = self.refresh(force=True, command=[str(self.root / 'absent' / 'opencode')])
        self.assertEqual(missing['reason'], 'opencode_unavailable')
        self.fake.answer(credentials(), usage())
        self.refresh(force=True)
        self.assertEqual({family: self.producer(family) for family in ROUTES}, first)  # same keys: kept
        self.assert_clean()

    def test_the_default_command_never_finds_a_relative_or_test_home_opencode(self):
        folder = self.root / 'bin'
        folder.mkdir(mode=0o700)
        binary = folder / 'opencode'
        binary.write_text('#!/bin/sh\nexit 1\n')
        binary.chmod(0o700)
        self.assertEqual(opencode_account.default_command(dict(PATH=str(folder))), [str(binary)])
        for environ in (dict(PATH=str(folder), VCW_TEST_HOME=str(self.root)), dict(PATH='bin'), {}):
            with self.assertRaises(opencode_account.ReadError) as caught:
                opencode_account.default_command(environ)
            self.assertEqual(str(caught.exception), 'opencode_unavailable')
        env = opencode_account.child_env(dict(PATH='/usr/bin:bin', HOME='/h', OPENAI_API_KEY='x', XDG_DATA_HOME='/d',
                                              OPENCODE_SERVER_PASSWORD='x'))
        self.assertEqual(env, dict(PATH='/usr/bin', HOME='/h', XDG_DATA_HOME='/d'))
        with mock.patch.dict(os.environ, VCW_TEST_HOME=str(self.root)):
            self.assertEqual(opencode_account.refresh(self.settings)['reason'], 'opencode_unavailable')


class NativeIdentityReadTest(Base):
    """Ruling B: a cold OpenCode Claude route runs the existing native reader once, for identity only (no window
    demanded); a current fact or the ChatGPT and Go families start none."""

    def test_a_cold_claude_route_reads_the_native_identity_once_and_no_windows(self):
        claude_usage._save(self.settings, account=dict(pending=True, generation='c' * 32), identity=None)
        calls = []

        def native(settings, **options):  # the native read's own effect: a current login
            calls.append(options)
            self.native(int(time.time()), 'a' * 32)
            return dict(state='stored', windows=[], reason='claude_limits_unsupported')
        source = collector.LocalSource(self.settings)
        with mock.patch.object(claude_usage, 'refresh', side_effect=native), \
                mock.patch.object(opencode_account, 'default_command', lambda environ: self.fake.command):
            for family in ('chatgpt', 'go'):
                subscription_billing.acquire(source, [route_of(family)], int(time.time()))
            self.assertEqual(calls, [])  # ChatGPT and Go never start the native reader
            subscription_billing.acquire(source, [route_of('claude')], int(time.time()))
            self.assertEqual([options.get('need') for options in calls], [()])  # identity only
            self.assertIsNotNone(subscription_billing.fact(source, route_of('claude'), int(time.time())))
            subscription_billing.acquire(source, [route_of('claude')], int(time.time()))  # current: nothing
            self.assertEqual(len(calls), 1)
        self.assert_clean()


class LocalSourceTest(Base):
    """The real `LocalSource` chooses this producer by exact path and acquires only a stale, needed family."""

    def test_the_real_source_reads_and_acquires_through_the_producer(self):
        source, now = collector.LocalSource(self.settings), int(time.time())
        self.assertIsNone(source.billing(route_of('go'), now))
        calls, original = [], opencode_account.refresh

        def spy(settings, **options):
            calls.append(options)
            return original(settings, **dict(options, command=self.fake.command))
        with mock.patch.object(opencode_account, 'refresh', side_effect=spy):
            subscription_billing.acquire(source, [route_of('go')], now)
            self.assertEqual(calls, [dict(families=('go',))])
            fact = subscription_billing.fact(source, route_of('go'), now)
            self.assertTrue(fact['producer'].startswith('opencode-go:'))
            self.assertRegex(subscription_billing.marker(fact, route_of('go'), now), '^[0-9a-f]{64}$')
            subscription_billing.acquire(source, [route_of('go')], now)  # current: not needed again
            self.assertEqual(len(calls), 1)
        self.assertIsNone(source.billing(dict(route_of('go'), route='opencode-free:choice:0123456789abcdef'), now))


class ServedTest(native_tests.NativeBase):
    """Collect -> admit as provider-managed fallback -> one dispatch permit, per family, with the real producer."""

    CHOICES = dict(claude=('claude-agent', 'invented-claude-small', 'claude-agent-sdk via opencode'),
                   chatgpt=('openai', 'invented-chatgpt-model', 'opencode provider openai'),
                   go=('opencode-go', 'invented-go-one', 'opencode provider opencode-go'))

    def setUp(self):
        super().setUp()
        self.fake = FakeOpenCode(self.root / 'fake')
        claude_usage._save(self.settings, identity=dict(key='9' * 32, generation='a' * 32), account=dict(
            key='9' * 32, generation='a' * 32, observed_at=self.now, client_version='invented-2', overflow='unknown',
            usage_at=None))
        for family in ROUTES:
            routing_paths.qualify(self.settings, ROUTES[family][0], demand=None, evidence='synthetic-1', now=self.now)

    def prepare(self, family, attempt):
        from scripts import routing as command
        provider, model, harness = self.CHOICES[family]
        service = command.Service(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(service.close)
        request = native_tests.routing_launch.prepare_request(
            dict(attempt_id=attempt, task='task-' + attempt,
                 scope=native_tests.routing_launch.assignment_scope(self.checkout),
                 choice=dict(provider=provider, model=model, effort='low', harness=harness, funding='subscription')),
            self.job(), harnesses=[harness])
        return service, service.handle(json.dumps(request).encode())

    def read(self):
        """The producer's own read with the double (served preparation and dispatch still start no process)."""
        with mock.patch.object(subprocess, 'Popen', REAL_POPEN):
            return opencode_account.refresh(self.settings, command=self.fake.command, force=True)

    def dispatch(self, service, family, attempt):
        provider, model, harness = self.CHOICES[family]
        facts = dict(provider=provider, model=model, variant='low', harness=harness, kind='primary',
                     funding_pool=ROUTES[family][2])
        return service.handle(json.dumps(dict(v=1, op='dispatch', attempt_id=attempt, request=facts)).encode())

    def test_each_family_admits_fallback_and_a_changed_account_refuses_the_unsent_dispatch(self):
        changed = dict(chatgpt=dict(openai=dict(value=dict(credentials()['data'][1]['value'], access=jwt(sub=OTHER)))),
                       go=dict(go=dict(value=dict(type='key', key='SENTINEL-GO-KEY-2'))), claude=None)
        for family in ROUTES:
            with self.subTest(family=family):
                self.fake.answer(credentials(), usage())
                self.assertEqual(self.read()['state'], 'stored')
                service, held = self.prepare(family, family + '-same')
                self.assertEqual(held['state'], 'admitted', held)
                self.assert_no_claim(family + '-same')  # quota unknown: provider-managed fallback only
                self.assertEqual(self.dispatch(service, family, family + '-same')['state'], 'dispatched')
                again = self.dispatch(service, family, family + '-same')  # one permit per attempt
                self.assertEqual(again['state'], 'already_dispatched', again)
                self.assertNotIn('authority', again)
                service, held = self.prepare(family, family + '-moved')
                self.assertEqual(held['state'], 'admitted', held)
                if changed[family] is None:  # Claude: the native login moved to a new generation
                    claude_usage._save(self.settings, identity=dict(key='9' * 32, generation='e' * 32),
                                       account=dict(key='9' * 32, generation='e' * 32, observed_at=self.now,
                                                    client_version='invented-2', overflow='unknown', usage_at=None))
                else:
                    self.fake.answer(credentials(**changed[family]), usage())
                self.read()
                sent = self.dispatch(service, family, family + '-moved')
                self.assertEqual((sent['state'], sent.get('reason')), ('blocked', 'account_changed'), sent)
                self.assertNotIn('authority', sent)
                self.assertEqual(self.holds()[family + '-moved']['state'], 'admitted')  # unsent: no permit


if __name__ == '__main__':
    unittest.main()
