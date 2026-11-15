"""Native Codex account/catalog acquisition against a synthetic stdio server only."""
import importlib
import json
import os
import signal
import threading
import time
import unittest
from unittest import mock

from runtime import codex_rate_limits as lifecycle, routing_observations as observations, subscription_billing
from runtime.adapters import native_catalog
from tests.test_routing_codex_rate_limits import Sandbox, result, window

try:
    account = importlib.import_module('runtime.codex_account')
except ModuleNotFoundError:
    account = None


def identity(email='person@example.test', workspace='Workspace'):
    return dict(account=dict(type='chatgpt', email=email, workspaceName=workspace, planType='pro'),
                requiresOpenaiAuth=True)


def model(alias='native-model', efforts=('low', 'high')):
    return dict(id='picker-id', model=alias, hidden=False, displayName='Display only',
                supportedReasoningEfforts=[dict(reasoningEffort=e, description='effort') for e in efforts],
                defaultReasoningEffort='low')


def payload(**changes):
    value = dict(accounts=[identity(), identity()],
                 pages=[dict(data=[model()], nextCursor=None)],
                 limits=result(primary=window(12), allowed=True))
    value.update(changes)
    return value


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(account, 'native Codex account/catalog producer is missing')

    def test_practical_identity_changes_with_workspace_but_not_email_case_or_plan(self):
        raw = identity()['account']
        key = account.account_key(raw)
        self.assertRegex(key, r'^[0-9a-f]{32}$')
        self.assertEqual(account.parse_account(identity()), key)
        self.assertEqual(account.account_key(dict(raw, email=' PERSON@EXAMPLE.TEST ', planType='plus')), key)
        self.assertNotEqual(account.account_key(dict(raw, workspaceName='Other workspace')), key)
        self.assertNotEqual(account.account_key(dict(raw, email='other@example.test')), key)

    def test_null_empty_missing_and_foreign_account_identity_refuse_without_echo(self):
        cases = [dict(account=None), dict(account=dict(type='apiKey')), dict(account=dict(type='gcp')),
                 identity(None), identity('  '), identity(workspace=17), identity('line\nbreak')]
        missing = identity()
        del missing['account']['email']
        cases.append(missing)
        cases.extend([dict(identity(), requiresOpenaiAuth=False), dict(identity(), requiresOpenaiAuth=None)])
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(lifecycle.ReadError) as caught:
                    account.parse_account(raw)
                self.assertEqual(str(caught.exception), 'codex_account_unsupported')

    def test_actual_camelcase_catalog_maps_exact_alias_and_efforts_not_picker_id(self):
        pages = [dict(data=[model()], nextCursor='next'),
                 dict(data=[model('second', ())], nextCursor=None)]
        self.assertEqual(account.parse_models(pages),
                         [dict(alias='native-model', resolved=None, efforts=['low', 'high']),
                          dict(alias='second', resolved=None, efforts=[])])

    def test_incomplete_malformed_duplicate_and_oversized_catalogs_refuse(self):
        bad = [[], [dict(data=[], nextCursor=None)], [dict(data=[model()], nextCursor='more')],
               [dict(data=[model(), model()], nextCursor=None)],
               [dict(data=[dict(model(), supportedReasoningEfforts=['high'])], nextCursor=None)],
               [dict(data=[dict(model(), supportedReasoningEfforts=None)], nextCursor=None)],
               [dict(data=[dict(model(), model='bad\nalias')], nextCursor=None)],
               [dict(data=[model(str(i)) for i in range(33)], nextCursor=None)]]
        for pages in bad:
            with self.subTest(pages=pages), self.assertRaises(lifecycle.ReadError) as caught:
                account.parse_models(pages)
            self.assertEqual(str(caught.exception), 'codex_catalog_invalid')


class AcquisitionTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(account, 'native Codex account/catalog producer is missing')
        self.route = dict(route='codex-chatgpt-subscription:s1', account='chatgpt-plan',
                          pool='chatgpt-subscription', billing_class='included_subscription')

    def acquire(self, value=None, scenario='account', **options):
        return account.refresh(self.settings, command=self.command(scenario, payload() if value is None else value),
                               environ=self.environ, **options)

    def test_one_process_checks_identity_around_paginated_catalog_and_quota(self):
        value = payload(pages=[dict(data=[model()], nextCursor='next'),
                               dict(data=[model('second')], nextCursor=None)])
        outcome = self.acquire(value)
        self.assertEqual(outcome, dict(state='stored', windows=[], reason='codex_usage_unconfirmed'))
        self.assertEqual(self.spawns(), 1)
        self.assertEqual(self.methods(), ['initialize', 'initialized', 'account/read', 'model/list', 'model/list',
                                         'account/rateLimits/read', 'account/read'])
        sent = [entry['received'] for entry in self.entries() if 'received' in entry]
        self.assertEqual([entry['params'] for entry in sent if entry['method'] == 'account/read'],
                         [dict(refreshToken=False), dict(refreshToken=False)])
        self.assertEqual(sent[4]['params']['cursor'], 'next')
        binding = account.binding(self.settings)
        self.assertEqual(binding['client_version'], 'unknown')
        self.assertEqual(native_catalog.stored(self.settings, 'codex')['binding'], binding)
        self.assertEqual(native_catalog.stored(self.settings, 'codex')['models'][0],
                         dict(alias='native-model', resolved=None, efforts=['low', 'high']))
        with mock.patch.object(native_catalog, 'current_binding', return_value=binding):
            routes, reason = native_catalog.catalog(self.settings, 'codex')
        self.assertIsNone(reason)
        self.assertEqual([(r['model'], r['effort']) for r in routes],
                         [('native-model', 'low'), ('native-model', 'high'), ('second', 'low'), ('second', 'high')])

    def test_account_fact_is_native_only_current_and_independent_of_unproven_quota(self):
        self.acquire()
        now = int(time.time())
        fact = account.billing_fact(self.settings, self.route, now)
        self.assertIsNone(subscription_billing.problem(fact, self.route, now))
        self.assertEqual(fact['paid_crossover'], 'unknown')
        foreign = dict(self.route, route='opencode-chatgpt-subscription:s1')
        self.assertIsNone(account.billing_fact(self.settings, foreign, now))
        self.assertIsNone(account.billing_fact(self.settings, self.route, now + 901))
        self.assertEqual(account.usable_windows(self.settings, now), [])
        self.assertEqual(self.stored(), {})

    def test_persisted_files_and_outcome_never_include_raw_identity_quota_or_initialize_metadata(self):
        outcome = self.acquire()
        saved = ''.join(p.read_text() for p in self.settings.parent.glob('*.json')) + json.dumps(outcome)
        for raw in ('person@example.test', 'Workspace', 'acct-secret-123', 'codex_double', str(self.home)):
            self.assertNotIn(raw, saved)

    def test_account_change_invalidates_prior_catalog_binding(self):
        self.acquire()
        first = account.binding(self.settings)
        value = payload(accounts=[identity('other@example.test'), identity('other@example.test')])
        self.acquire(value, force=True)
        second = account.binding(self.settings)
        self.assertNotEqual(first['connection'], second['connection'])
        with mock.patch.object(native_catalog, 'current_binding', return_value=first):
            self.assertEqual(native_catalog.catalog(self.settings, 'codex')[1], 'native_binding_changed')

    def test_changed_or_refused_account_during_exchange_clears_prior_confirmation(self):
        self.acquire()
        for final in (identity('other@example.test'), dict(account=None)):
            with self.subTest(final=final):
                answer = self.acquire(payload(accounts=[identity(), final]), force=True)
                self.assertEqual(answer['state'], 'failed')
                self.assertIn(answer['reason'], ('codex_account_changed', 'codex_account_unsupported'))
                self.assertIsNone(account.binding(self.settings))
                self.assertIsNone(account.billing_fact(self.settings, self.route, int(time.time())))

    def test_complete_metadata_is_reused_without_rereading_unknown_quota(self):
        self.acquire()
        self.assertEqual(self.acquire(need=('7d',)), dict(state='fresh', windows=[],
                                                       reason='codex_usage_unconfirmed'))
        self.assertEqual(self.spawns(), 1)

    def test_mismatched_stored_catalog_is_not_reused_as_current_metadata(self):
        self.acquire()
        snapshot = native_catalog.stored(self.settings, 'codex')
        snapshot['binding']['connection'] = 'account:other'
        native_catalog.store_snapshot(self.settings, 'codex', snapshot)
        self.assertEqual(self.acquire()['state'], 'stored')
        self.assertEqual(self.spawns(), 2)
        self.assertEqual(native_catalog.stored(self.settings, 'codex')['binding'], account.binding(self.settings))

    def test_extra_individual_spend_and_ambiguous_usage_never_create_headroom(self):
        values = []
        for allowed in (None, False):
            values.append(result(primary=window(0), allowed=allowed))
        for field, value in (('individualLimit', dict(limit='10', used='1', remainingPercent=90, resetsAt=123)),
                             ('spendControlReached', True)):
            raw = result(primary=window(0), allowed=True)
            raw['rateLimits'][field] = value
            values.append(raw)
        extra = result(primary=window(0), allowed=True, by_limit=dict(other=dict(primary=window(0))))
        values.append(extra)
        for limits in values:
            with self.subTest(limits=limits):
                answer = self.acquire(payload(limits=limits), force=True)
                self.assertEqual(answer['windows'], [])
                self.assertIn(answer['reason'], ('codex_usage_unconfirmed', 'codex_limits_unsupported'))
                self.assertEqual(account.usable_windows(self.settings, int(time.time())), [])
                self.assertEqual(self.stored(), {})

    def test_unbound_fresh_windows_and_consumed_boundary_are_never_rebound_or_changed(self):
        now = int(time.time())
        observations.store_window(self.settings, lifecycle.POOL,
                                  dict(id='7d', limit=1000, used=5, length=604800, reset_at=now + 60,
                                       unit=observations.WINDOW_UNIT, observed_at=now))
        observations.mark_consumed(self.settings, lifecycle.POOL, now, 'attempt')
        before = observations.window_path(self.settings).read_bytes()
        self.acquire()
        self.assertEqual(observations.window_path(self.settings).read_bytes(), before)
        self.assertEqual(account.usable_windows(self.settings, now), [])

    def test_failure_is_redacted_clears_binding_and_backs_off_without_process(self):
        self.acquire()
        answer = self.acquire(scenario='read_error', force=True)
        self.assertEqual(answer, dict(state='failed', reason='codex_protocol_error'))
        self.assertIsNone(account.binding(self.settings))
        self.assertEqual(self.acquire(), dict(state='backoff', reason='codex_protocol_error'))
        self.assertEqual(self.spawns(), 2)

    def test_repeated_cursor_and_incomplete_catalog_fail_in_bounded_process(self):
        value = payload(pages=[dict(data=[model()], nextCursor='repeat'),
                               dict(data=[model('second')], nextCursor='repeat')])
        self.assertEqual(self.acquire(value), dict(state='failed', reason='codex_catalog_invalid'))
        self.assertEqual(self.spawns(), 1)
        self.assertIsNone(account.binding(self.settings))

    def test_timestamp_is_request_start_and_missing_version_stays_unknown(self):
        before = int(time.time())
        self.acquire(scenario='account_slow')
        now = int(time.time())
        fact = account.billing_fact(self.settings, self.route, now)
        self.assertLessEqual(fact['observed_at'], before)
        self.assertEqual(account.binding(self.settings)['client_version'], 'unknown')

    def test_concurrent_metadata_reads_reuse_one_pool_lock_and_one_process(self):
        answers = []
        def run():
            answers.append(self.acquire(scenario='account_slow')['state'])
        workers = [threading.Thread(target=run) for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(sorted(answers), ['fresh', 'fresh', 'stored'])
        self.assertEqual(self.spawns(), 1)

    def test_auth_metadata_change_and_timeout_reuse_existing_transport_protection(self):
        for scenario, reason in (('touch_auth', 'codex_auth_changed'), ('hang_read', 'codex_timeout')):
            with self.subTest(scenario=scenario):
                self.assertEqual(self.acquire(scenario=scenario, force=True, timeout=0.2),
                                 dict(state='failed', reason=reason))
                self.assertIsNone(account.binding(self.settings))

    def test_metadata_transport_cannot_refresh_login_or_send_prompts(self):
        for method, params in (('account/read', dict(refreshToken=True)),
                               ('model/list', dict(limit=33, includeHidden=False)),
                               ('model/list', dict(limit=1, includeHidden=True)),
                               ('turn/start', {}), ('thread/start', {}), ('account/login/start', {})):
            with self.subTest(method=method), self.assertRaises(lifecycle.ReadError) as caught:
                lifecycle._message(method, 4, params, account=True)
            self.assertEqual(str(caught.exception), 'codex_method_refused')

    def test_no_drain_unicode_pagination_respects_deadline_and_releases_account_and_pool(self):
        self.acquire()
        self.assertIsNotNone(account.binding(self.settings))
        self.log.unlink()  # isolate the one no-drain process from the preceding successful confirmation
        processes, answers, errors = [], [], []
        ownership = threading.Lock()
        real_spawn = lifecycle.subprocess.Popen

        def spawn(*args, **kwargs):
            process = real_spawn(*args, **kwargs)
            real_wait = process.wait
            def wait(*args, **kwargs):
                with ownership:
                    return real_wait(*args, **kwargs)
            process.wait = wait  # watchdog signals only an owned group whose leader has not been reaped
            processes.append(process)
            return process

        def acquire():
            try:
                answers.append(self.acquire(scenario='no_drain_pages', force=True, timeout=0.5))
            except BaseException as error:
                errors.append(error)

        def stop_owned():
            with ownership:
                for process in processes:
                    if process.returncode is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

        worker = threading.Thread(target=acquire)
        started = time.monotonic()
        watchdog = False
        with mock.patch.object(lifecycle.subprocess, 'Popen', side_effect=spawn):
            try:
                worker.start()
                worker.join(5)
                watchdog = worker.is_alive()
                if watchdog:
                    stop_owned()
                    worker.join(3)
            finally:
                if worker.is_alive():
                    stop_owned()
                    worker.join(32)  # the double's pre-write alarm bounds even a broken cleanup path
        elapsed = time.monotonic() - started
        self.assertFalse(worker.is_alive(), 'owned watchdog worker did not stop')
        self.assertEqual(errors, [])
        print('\nCC-R1 no-drain: watchdog_fired=%s outcome=%s wall_s=%.3f spawns=%d'
              % (watchdog, answers, elapsed, self.spawns()))
        self.assertEqual(answers, [dict(state='failed', reason='codex_timeout')])
        self.assertFalse(watchdog, 'watchdog rescued a blocked write instead of the shared deadline')
        self.assertLess(elapsed, 0.5 + 2 * lifecycle.STOP_GRACE + 1.5)
        self.assertEqual(self.spawns(), 1)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)
        self.assertIsNone(account.binding(self.settings))
        self.assertIsNone(account.billing_fact(self.settings, self.route, int(time.time())))
        self.assertEqual(self.acquire(), dict(state='backoff', reason='codex_timeout'))
        self.assertEqual(self.spawns(), 1)  # backoff, rather than busy, also proves the pool lock was released


if __name__ == '__main__':
    unittest.main()
