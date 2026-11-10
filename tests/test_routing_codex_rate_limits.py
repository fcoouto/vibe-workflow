"""R7: the read-only Codex rate-limit reader (runtime/codex_rate_limits.py). Doubles only: the app-server is
tests/codex_app_server_double.py, no live Codex, no network, no model call, and never the real Codex auth file."""
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from runtime import codex_rate_limits as reader, routing_observations as collector

ROOT = Path(__file__).resolve().parents[1]
DOUBLE = ROOT / 'tests' / 'codex_app_server_double.py'
PROBE = json.loads((ROOT / 'tests/fixtures/smart-routing/probes/codex_rate_limits_exhausted.json').read_text())
POOL = reader.POOL
WEEK, FIVE_HOURS = 10080, 300


def window(percent, minutes=WEEK, resets_in=3600, now=None):
    return dict(usedPercent=percent, windowDurationMins=minutes, resetsAt=(now or int(time.time())) + resets_in)


def result(primary=None, secondary=None, allowed=None, limit_id='codex', by_limit=None, **extra):
    snapshot = dict(limitId=limit_id, primary=primary, secondary=secondary, credits=None, planType='pro')
    value = dict(ordinaryUsageAllowed=allowed, rateLimits=snapshot, accountId='acct-secret-123',
                 rateLimitsByLimitId=by_limit, rateLimitUpsell=None)
    value.update(extra)
    return value


def manifest(windows=('5h', '7d')):
    route = dict(route='opencode-openai', status='qualified', provider='openai', model='m', effort='none',
                 harness='opencode provider openai', billing_class='included_subscription', pool=POOL,
                 account='chatgpt-plan', demand={'7d': 1}, binding_enforceable=True)
    return dict(version=1, window_unit=collector.WINDOW_UNIT, routes=[route],
                pools={POOL: dict(account='chatgpt-plan', windows=[dict(id=key) for key in windows])})


class Sandbox(unittest.TestCase):
    """A private temporary HOME with a fake `~/.codex/auth.json`, a private store and a double command."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.assertNotEqual(base, Path.home().resolve())  # guard: never the real home
        self.assertTrue(str(base).startswith(os.path.realpath(tempfile.gettempdir())))
        self.home = base / 'home'
        (self.home / '.codex').mkdir(parents=True)
        (self.home / '.codex' / 'auth.json').write_text('{"fake": true}')  # never a real credential
        self.settings = base / 'state' / 'routing' / 'settings.json'
        self.log = base / 'double.log'
        self.environ = dict(PATH='/usr/bin:/bin', HOME=str(self.home), LANG='C', OPENAI_API_KEY='sk-secret',
                            CODEX_API_KEY='secret', GITHUB_TOKEN='gh-secret', AWS_SECRET_ACCESS_KEY='aws')
        self.manifest = manifest()

    def command(self, scenario='ok', value=None):
        value = result(primary=window(42)) if value is None else value
        return [sys.executable, '-I', '-B', str(DOUBLE), str(self.log), scenario, json.dumps(value)]

    def entries(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def spawns(self):
        return sum(1 for entry in self.entries() if entry.get('spawned'))

    def methods(self):
        return [entry['received'].get('method') for entry in self.entries() if 'received' in entry]

    def read(self, scenario='ok', value=None, **options):
        options.setdefault('timeout', 5.0)
        return reader.read_windows(command=self.command(scenario, value), environ=self.environ,
                                   manifest=self.manifest, **options)

    def refresh(self, scenario='ok', value=None, **options):
        options.setdefault('timeout', 5.0)
        return reader.refresh(self.settings, need=options.pop('need', ('7d',)), command=self.command(scenario, value),
                              environ=self.environ, manifest=self.manifest, **options)

    def stored(self):
        return {item['id']: item for item in collector.LocalSource(self.settings).windows(POOL, int(time.time()))}


class ParseTests(unittest.TestCase):
    """The windows the account reports, mapped to the pool's windows in utilization thousandths."""

    def parse(self, value, now=None, declared=('5h', '7d')):
        return reader.parse(value, now or int(time.time()), declared)

    def test_the_exhausted_probe_maps_to_a_full_weekly_window_and_nothing_else(self):
        probe = PROBE['result']
        now = probe['rateLimits']['primary']['resetsAt'] - 3600
        windows = self.parse(probe, now)
        self.assertEqual(windows, [dict(id='7d', limit=1000, used=1000, length=604800,
                                        reset_at=probe['rateLimits']['primary']['resetsAt'], rolling=False,
                                        unit=collector.WINDOW_UNIT, observed_at=now)])

    def test_windows_are_discovered_never_assumed(self):
        now = int(time.time())
        weekly = self.parse(result(primary=window(10, now=now)), now)
        self.assertEqual([item['id'] for item in weekly], ['7d'])  # a weekly-only plan gets no 5h window
        both = self.parse(result(primary=window(25, FIVE_HOURS, 600, now), secondary=window(5, now=now)), now)
        self.assertEqual([(item['id'], item['used'], item['length']) for item in both],
                         [('5h', 250, 18000), ('7d', 50, 604800)])
        only_secondary = self.parse(result(secondary=window(7, now=now)), now)
        self.assertEqual([(item['id'], item['used']) for item in only_secondary], [('7d', 70)])

    def test_fractional_percentages_round_up(self):
        now = int(time.time())
        self.assertEqual(self.parse(result(primary=window(12.34, now=now)), now)[0]['used'], 124)
        self.assertEqual(self.parse(result(primary=window(0, now=now)), now)[0]['used'], 0)
        self.assertEqual(self.parse(result(primary=window(100, now=now)), now)[0]['used'], 1000)

    def test_ordinary_usage_refused_means_exhausted_whatever_the_percentage(self):
        now = int(time.time())
        value = result(primary=window(3, FIVE_HOURS, 600, now), secondary=window(4, now=now), allowed=False)
        self.assertEqual([item['used'] for item in self.parse(value, now)], [1000, 1000])
        self.assertEqual(self.parse(result(primary=window(3, now=now), allowed=True), now)[0]['used'], 30)

    def test_the_codex_bucket_of_the_multi_bucket_view_wins(self):
        now = int(time.time())
        codex = dict(limitId='codex', primary=window(60, now=now), secondary=None)
        other = dict(limitId='codex_other', primary=window(1, now=now), secondary=None)
        value = result(primary=window(1, now=now), limit_id='codex_other', by_limit=dict(codex=codex, codex_other=other))
        self.assertEqual(self.parse(value, now)[0]['used'], 600)

    def test_unexpected_shapes_fail_closed(self):
        now = int(time.time())
        bad = {
            'not_a_dict': [],
            'no_rate_limits': dict(ordinaryUsageAllowed=None),
            'other_bucket_only': result(primary=window(1, now=now), limit_id='codex_other'),
            'no_windows': result(),
            'bool_percent': result(primary=window(True, now=now)),
            'string_percent': result(primary=window('5', now=now)),
            'nan_percent': result(primary=window(math.nan, now=now)),
            'negative_percent': result(primary=window(-1, now=now)),
            'over_100': result(primary=window(100.5, now=now)),
            'unknown_length': result(primary=window(1, 60, 600, now)),
            'null_length': result(primary=dict(usedPercent=1, windowDurationMins=None, resetsAt=now + 60)),
            'float_length': result(primary=dict(usedPercent=1, windowDurationMins=10080.0, resetsAt=now + 60)),
            'null_reset': result(primary=dict(usedPercent=1, windowDurationMins=WEEK, resetsAt=None)),
            'past_reset': result(primary=window(1, resets_in=0, now=now)),
            'reset_beyond_length': result(primary=window(1, FIVE_HOURS, 18000 + 120, now)),
            'duplicate_window': result(primary=window(1, now=now), secondary=window(2, now=now)),
            'extra_window_field': result(primary=dict(window(1, now=now), surprise=1)),
            'allowed_not_bool': result(primary=window(1, now=now), allowed='yes'),
        }
        for label, value in bad.items():
            with self.subTest(label), self.assertRaises(reader.ReadError) as caught:
                self.parse(value, now)
            self.assertIn(str(caught.exception), reader.REASONS)

    def test_a_window_the_manifest_does_not_declare_fails_closed(self):
        now = int(time.time())
        value = result(primary=window(1, FIVE_HOURS, 600, now), secondary=window(1, now=now))
        with self.assertRaises(reader.ReadError) as caught:
            self.parse(value, now, declared=('7d',))
        self.assertEqual(str(caught.exception), 'codex_window_undeclared')
        with self.assertRaises(reader.ReadError) as caught:
            self.parse(value, now, declared=())
        self.assertEqual(str(caught.exception), 'codex_pool_undeclared')

    def test_declared_windows_come_from_the_manifest_pool_or_its_routes(self):
        self.assertEqual(reader.declared_windows(manifest(('7d',))), {'7d'})
        bare = manifest()
        del bare['pools']
        self.assertEqual(reader.declared_windows(bare), {'7d'})  # the routes' demand
        bare['routes'][0]['pool'] = 'other'
        self.assertEqual(reader.declared_windows(bare), set())


class ExchangeTests(Sandbox):
    """The bounded JSON-RPC client against the double."""

    def test_only_the_handshake_and_the_rate_limit_read_are_ever_sent(self):
        windows = self.read()
        self.assertEqual([(item['id'], item['used']) for item in windows], [('7d', 420)])
        self.assertEqual(self.methods(), ['initialize', 'initialized', 'account/rateLimits/read'])
        sent = [entry['received'] for entry in self.entries() if 'received' in entry]
        self.assertEqual(sent[0]['params']['clientInfo']['name'], reader.CLIENT_NAME)
        self.assertNotIn('id', sent[1])
        self.assertNotIn('params', sent[2])
        self.assertEqual(self.spawns(), 1)

    def test_the_client_refuses_to_send_any_other_method(self):
        self.assertEqual(reader.METHODS, ('initialize', 'initialized', 'account/rateLimits/read'))
        for method in ('thread/start', 'turn/start', 'account/logout', 'account/login/start', 'config/write'):
            with self.subTest(method), self.assertRaises(AssertionError):
                reader._message(method, 3)

    def test_the_child_gets_only_allowlisted_variables(self):
        self.environ.update(PATH='relative/bin:/usr/bin:.:/bin', TMPDIR='relative', CODEX_HOME=str(self.home / '.codex'))
        self.read()
        env = self.entries()[0]['env']
        self.assertTrue(set(env) <= set(reader.ENV_ALLOWLIST) | {'__CF_USER_TEXT_ENCODING', 'LC_CTYPE'}, env)
        for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'TMPDIR'):
            self.assertNotIn(name, env)
        child = reader.child_env(self.environ)
        self.assertEqual(child['PATH'], '/usr/bin:/bin')  # relative entries never reach the child
        self.assertEqual(self.entries()[0]['cwd'], '/')

    def test_tmpdir_is_passed_only_when_it_is_a_private_absolute_directory(self):
        private = Path(self.temp.name) / 'tmp'
        private.mkdir(mode=0o700)
        self.assertEqual(reader.child_env(dict(self.environ, TMPDIR=str(private)))['TMPDIR'], str(private))
        for value in ('relative', '/nonexistent-r7', str(private / 'missing')):
            self.assertNotIn('TMPDIR', reader.child_env(dict(self.environ, TMPDIR=value)))
        os.chmod(private, 0o777)
        self.assertNotIn('TMPDIR', reader.child_env(dict(self.environ, TMPDIR=str(private))))

    def test_the_default_command_is_an_absolute_codex_on_absolute_path_entries(self):
        bin_dir = Path(self.temp.name) / 'bin'
        bin_dir.mkdir()
        codex = bin_dir / 'codex'
        codex.write_text('#!/bin/sh\n')
        codex.chmod(0o700)
        self.assertEqual(reader.default_command(dict(PATH=str(bin_dir))), [str(codex), 'app-server'])
        with self.assertRaises(reader.ReadError) as caught:
            reader.default_command(dict(PATH='relative-bin:.'))
        self.assertEqual(str(caught.exception), 'codex_unavailable')
        cwd = os.getcwd()
        try:
            os.chdir(bin_dir)  # a `codex` in the working directory is never found through a relative entry
            with self.assertRaises(reader.ReadError):
                reader.default_command(dict(PATH='.'))
        finally:
            os.chdir(cwd)

    def test_failures_are_fixed_reasons_and_never_leak_tokens_or_account_ids(self):
        cases = dict(exit_at_once='codex_exited', init_error='codex_protocol_error', read_error='codex_protocol_error',
                     server_request='codex_protocol_unexpected', garbage='codex_protocol_invalid',
                     huge='codex_protocol_invalid', wrong_id='codex_protocol_unexpected')
        for scenario, reason in cases.items():
            with self.subTest(scenario), self.assertRaises(reader.ReadError) as caught:
                self.read(scenario)
            self.assertEqual(str(caught.exception), reason)
            self.assertEqual(caught.exception.args, (reason,))

    def test_notifications_are_skipped(self):
        self.assertEqual(len(self.read('notifications')), 1)

    def test_a_hung_server_is_killed_at_the_hard_timeout(self):
        for scenario in ('hang', 'hang_read'):
            with self.subTest(scenario):
                started = time.monotonic()
                with self.assertRaises(reader.ReadError) as caught:
                    self.read(scenario, timeout=0.5)
                self.assertEqual(str(caught.exception), 'codex_timeout')
                self.assertLess(time.monotonic() - started, 3.0)

    def test_a_changed_auth_file_fails_closed(self):
        with self.assertRaises(reader.ReadError) as caught:
            self.read('touch_auth')
        self.assertEqual(str(caught.exception), 'codex_auth_changed')

    def test_the_auth_file_is_only_stat_ed_never_opened(self):
        auth = str(self.home / '.codex' / 'auth.json')
        opened = []

        def audit(event, args):
            if event == 'open' and args and str(args[0]) == auth:
                opened.append(event)
        sys.addaudithook(audit)  # hooks cannot be removed; this one only records
        self.read()
        self.assertEqual(opened, [])

    def test_wall_time_with_the_double(self):
        started = time.monotonic()
        timing = {}
        self.read(timing=timing)
        wall = time.monotonic() - started
        self.assertLess(timing['spawn_ms'], timing['wall_ms'] + 1)
        self.assertLess(wall, 5.0)
        print('\ncodex reader double timing: spawn_to_initialized_ms=%d wall_ms=%d'
              % (timing['spawn_ms'], timing['wall_ms']), file=sys.stderr)


class RefreshTests(Sandbox):
    """On demand only: one reader per pool at a time, a fresh reading is reused, a failure backs off."""

    def test_a_missing_reading_is_read_and_stored_as_a_host_observation(self):
        outcome = self.refresh()
        self.assertEqual(outcome['state'], 'stored')
        self.assertEqual(outcome['windows'], ['7d'])
        stored = self.stored()['7d']
        self.assertEqual((stored['used'], stored['limit'], stored['unit']), (420, 1000, collector.WINDOW_UNIT))
        self.assertNotIn('source', stored)  # a host observation: store_window never writes a source
        self.assertNotIn('acct-secret-123', json.dumps(outcome) + collector.window_path(self.settings).read_text())

    def test_a_fresh_reading_is_reused_without_spawning(self):
        self.refresh()
        self.assertEqual(self.refresh()['state'], 'fresh')
        self.assertEqual(self.spawns(), 1)

    def test_a_fresh_reading_stored_by_a_settled_launch_is_reused(self):
        collector.store_window(self.settings, POOL, dict(id='7d', limit=1000, used=5, length=604800,
                                                         reset_at=int(time.time()) + 60, unit=collector.WINDOW_UNIT,
                                                         observed_at=int(time.time())))
        self.assertEqual(self.refresh()['state'], 'fresh')
        self.assertEqual(self.spawns(), 0)

    def test_a_stale_reading_is_replaced(self):
        old = int(time.time()) - collector.FRESH_SECONDS - 5
        collector.store_window(self.settings, POOL, dict(id='7d', limit=1000, used=5, length=604800,
                                                         reset_at=old + 60, unit=collector.WINDOW_UNIT,
                                                         observed_at=old))
        self.assertEqual(self.refresh()['state'], 'stored')
        self.assertEqual(self.stored()['7d']['used'], 420)

    def test_a_fresh_seed_is_reused_and_a_reader_observation_replaces_it(self):
        now = int(time.time())
        seed = dict(pool=POOL, window='7d', used=1, limit=1000, reset_at=now + 60, length=604800, event_id='e')
        collector._store_seed(self.settings, seed, now)
        self.assertEqual(self.refresh()['state'], 'fresh')  # data already produced: no process
        self.assertEqual(self.spawns(), 0)
        self.assertEqual(self.refresh(force=True)['state'], 'stored')  # the operator's read
        stored = self.stored()['7d']
        self.assertEqual(stored['used'], 420)
        self.assertNotIn('source', stored)  # a host observation now; status shows `host`
        self.assertEqual(collector.window_status(self.settings, int(time.time()))['windows'][0]['source'], 'host')

    def test_a_stale_seed_is_read_and_replaced(self):
        old = int(time.time()) - collector.FRESH_SECONDS - 5
        seed = dict(pool=POOL, window='7d', used=1, limit=1000, reset_at=old + 60, length=604800, event_id='e')
        collector._store_seed(self.settings, seed, old)
        self.assertEqual(self.refresh()['state'], 'stored')
        self.assertNotIn('source', self.stored()['7d'])

    def test_a_reading_missing_a_needed_window_is_stale(self):
        self.refresh()
        outcome = self.refresh(need=('5h', '7d'), scenario='ok')
        self.assertEqual(outcome, dict(state='failed', reason='codex_window_missing'))
        self.assertEqual(self.spawns(), 2)

    def test_a_failure_backs_off_without_spawning_again(self):
        self.assertEqual(self.refresh('exit_at_once'), dict(state='failed', reason='codex_exited'))
        self.assertEqual(self.refresh(), dict(state='backoff', reason='codex_exited'))
        self.assertEqual(self.spawns(), 1)
        folder = collector.window_path(self.settings).parent
        reader._record(folder, dict(at=int(time.time()) - reader.FAILURE_BACKOFF, reason='codex_exited'))
        self.assertEqual(self.refresh()['state'], 'stored')  # the backoff has passed
        self.assertEqual(self.spawns(), 2)
        self.assertIsNone(reader._state(collector.window_path(self.settings).parent))  # a success clears it
        self.assertEqual(self.refresh()['state'], 'fresh')

    def test_an_operator_read_ignores_the_backoff_but_not_the_lock(self):
        self.refresh('exit_at_once')
        self.assertEqual(self.refresh(force=True)['state'], 'stored')

    def test_concurrent_prepares_spawn_one_reader(self):
        outcomes = []

        def run():
            outcomes.append(self.refresh('slow')['state'])
        threads = [threading.Thread(target=run) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.spawns(), 1)
        self.assertEqual(outcomes.count('stored'), 1)  # the others found the lock held, or the fresh reading
        self.assertTrue(set(outcomes) <= {'stored', 'busy', 'fresh'}, outcomes)

    def test_a_busy_lock_never_spawns(self):
        with reader._pool_lock(self.settings, POOL) as held:
            self.assertTrue(held)
            self.assertEqual(self.refresh(), dict(state='busy', reason='reader_busy'))
        self.assertEqual(self.spawns(), 0)

    def test_an_unsafe_store_fails_closed_without_spawning(self):
        folder = self.settings.parent
        folder.mkdir(parents=True)
        os.chmod(folder, 0o755)
        self.assertEqual(self.refresh(), dict(state='failed', reason='observation_store_unsafe'))
        self.assertEqual(self.spawns(), 0)

    def test_applies_only_to_the_chatgpt_subscription_pool(self):
        route = copy.deepcopy(manifest()['routes'][0])
        self.assertTrue(reader.applies(route))
        self.assertFalse(reader.applies(dict(route, pool='claude-subscription')))
        self.assertFalse(reader.applies(dict(route, billing_class='gateway')))


class CollectorTests(Sandbox):
    """The collector reads only when a prepare needs a chatgpt-subscription route whose reading is stale."""

    def setUp(self):
        super().setUp()
        claude = dict(copy.deepcopy(self.manifest['routes'][0]), route='claude', pool='claude-subscription',
                      account='claude-plan')
        second = dict(copy.deepcopy(self.manifest['routes'][0]), route='opencode-openai-2', model='m2')
        self.manifest['routes'] += [claude, second]
        self.calls = []
        original = reader.refresh

        def refresh(settings, **options):
            self.calls.append(options)
            return original(settings, command=self.command(), environ=self.environ, manifest=self.manifest, **options)
        for patch in (mock.patch.object(reader, 'refresh', side_effect=refresh),
                      mock.patch.object(collector, 'load_manifest', return_value=copy.deepcopy(self.manifest))):
            patch.start()
            self.addCleanup(patch.stop)

    def collect(self, **options):
        return collector.collect(self.temp.name, dict(scope='s'), settings_path=self.settings,
                                 ledger_path=Path(self.temp.name) / 'ledger.sqlite3', **options)

    def test_a_missing_reading_is_read_once_per_pool_and_admitted_fresh(self):
        observations = self.collect(refresh_windows=True)['observations']
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['need'], ('7d',))
        self.assertEqual(sorted(route['route'] for route in observations['routes']),
                         ['opencode-openai', 'opencode-openai-2'])
        self.assertEqual([(item['id'], item['used'], item['fresh']) for item in observations['pools'][POOL]['windows']],
                         [('7d', 420, True)])
        self.assertIn(dict(route='claude', reason='window_unobserved'), observations['excluded'])

    def test_a_fresh_reading_spawns_nothing(self):
        self.collect(refresh_windows=True)
        self.collect(refresh_windows=True)
        self.assertEqual(self.spawns(), 1)

    def test_without_refresh_windows_nothing_is_read(self):
        observations = self.collect()['observations']
        self.assertEqual((self.calls, self.spawns()), ([], 0))
        self.assertIn(dict(route='opencode-openai', reason='window_unobserved'), observations['excluded'])

    def test_a_static_source_never_reads(self):
        source = collector.StaticSource(self.manifest['routes'])
        self.collect(refresh_windows=True, source=source)
        self.assertEqual((self.calls, self.spawns()), ([], 0))

    def test_a_failed_read_leaves_the_route_out_and_prepare_fails_closed(self):
        with mock.patch.object(self, 'command', return_value=self.command('exit_at_once')):
            observations = self.collect(refresh_windows=True)['observations']
        self.assertIn(dict(route='opencode-openai', reason='window_unobserved'), observations['excluded'])
        self.assertEqual(observations['routes'], [])

    def test_the_service_reads_only_outside_serve(self):
        from scripts import routing as command
        for serving, one_shot, expected in ((True, False, False), (False, True, True), (False, False, True)):
            service = command.Service(self.settings, Path(self.temp.name) / 'ledger.sqlite3', serving=serving,
                                      one_shot=one_shot, project=self.temp.name)
            with self.subTest(serving=serving, one_shot=one_shot), \
                    mock.patch.object(collector, 'collect', return_value=dict(observations={}, seen={})) as collect:
                service._collect(dict(scope='s'))
                self.assertIs(collect.call_args.kwargs['refresh_windows'], expected)
            service.close()


class CommandTests(Sandbox):
    """The operator's one-shot command."""

    def test_the_one_shot_command_prints_windows_and_timing_only(self):
        from scripts import codex_rate_limits as script
        output = []
        original = reader.refresh

        def refresh(settings, **options):
            self.assertEqual((settings, options['force']), (self.settings, True))
            return original(settings, force=True, command=self.command(), environ=self.environ,
                            manifest=self.manifest, timing=options['timing'])
        with mock.patch.object(script.reader, 'refresh', side_effect=refresh), \
                mock.patch('builtins.print', side_effect=lambda text: output.append(text)):
            code = script.main(['--settings', str(self.settings)])
        self.assertEqual(code, 0)
        shown = json.loads(output[0])
        self.assertEqual(shown['state'], 'stored')
        self.assertNotIn('acct-secret-123', output[0])

    def test_the_command_refuses_unknown_arguments(self):
        from scripts import codex_rate_limits as script
        with mock.patch('builtins.print'):
            self.assertEqual(script.main(['--serve']), 2)


if __name__ == '__main__':
    unittest.main()
