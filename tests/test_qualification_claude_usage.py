"""Synthetic wire-contract tests only: no native client, account, network or model calls."""
import copy
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

from runtime import subscription_billing
from tests.qualification import claude_usage as reader


# Invented values in the installed-source field shapes; not captured account observations.
INITIALIZATION = {
    'commands': [{'name': 'fixture-command', 'description': 'Synthetic fixture', 'argumentHint': '', 'builtin': True}],
    'agents': [{'name': 'fixture-agent', 'description': 'Synthetic fixture'}],
    'output_style': 'default',
    'available_output_styles': ['default', 'Proactive', 'Concise', 'Explanatory', 'Learning'],
    'user_output_styles_dir': '/synthetic/styles',
    'models': [{'value': 'fixture-alias', 'resolvedModel': 'fixture-model', 'displayName': 'Fixture',
                'description': 'Synthetic fixture', 'supportsEffort': True,
                'supportedEffortLevels': ['low', 'medium', 'high', 'xhigh', 'max'],
                'supportsAdaptiveThinking': True, 'supportsFastMode': True, 'supportsAutoMode': True,
                'supportsThinkingOff': False}],
    'account': {'email': 'fixture@example.invalid', 'organization': 'Fixture Org',
                'subscriptionType': 'max', 'apiProvider': 'firstParty'},
    'pid': 12345, 'claude_code_version': '2.1.311', 'current_permission_mode': 'default',
    'feedback_mode': {'kind': 'bundle'}, 'analytics_disabled': False,
    'remote_control_auto_enable': False, 'remote_control_auto_connect_default': False,
    'remote_control_available': True, 'remote_control_auto_on_by_default': False,
    'ide_rc_auto_enable_gate': False, 'fast_mode_state': 'off',
    'fast_mode_disabled_reason': 'sdk_opt_in_required', 'session_state': 'idle',
    'capabilities': ['ui_surface_v1', 'mcp_call_result_not_for_model_v1'],
}
USAGE = {
    'session': {'total_cost_usd': 0, 'total_api_duration_ms': 0, 'total_duration_ms': 1,
                'total_lines_added': 0, 'total_lines_removed': 0, 'model_usage': {}},
    'subscription_type': 'max', 'rate_limits_available': True,
    'rate_limits': {
        'five_hour': {'utilization': 96, 'resets_at': '2026-11-14T05:00:00+00:00'},
        'seven_day': {'utilization': 32, 'resets_at': '2026-11-20T00:00:00+00:00'},
        'extra_usage': {'is_enabled': False, 'monthly_limit': None, 'used_credits': None,
                        'utilization': None, 'currency': None},
    },
    'behaviors': None,
}
EXPECTED = {
    'qualified': False, 'reason': 'claude_source_unqualified', 'subscription_type': 'max',
    'rate_limits_available': True, 'extra_usage_enabled': False,
    'aggregate_meters': {
        'five_hour': {'utilization': 96, 'resets_at': '2026-11-14T05:00:00+00:00'},
        'seven_day': {'utilization': 32, 'resets_at': '2026-11-20T00:00:00+00:00'},
    },
}
FLAGS = ['--bare', '--safe-mode', '--print', '--input-format', 'stream-json', '--output-format',
         'stream-json', '--verbose', '--no-session-persistence', '--tools', '', '--strict-mcp-config',
         '--mcp-config', '{"mcpServers":{}}', '--settings', '{"disableAllHooks":true}', '--setting-sources', '']

# A real child executable, not a mocked Popen. It logs only synthetic data in its private directory.
DOUBLE = r'''
import json, os, signal, subprocess, sys, time
log, fixture, scenario = sys.argv[1:4]
def record(value):
    with open(log, 'a') as handle:
        handle.write(json.dumps(value) + '\n')
def emit(value):
    print(json.dumps(value), flush=True)
with open(fixture) as handle:
    init, usage = json.load(handle)
record(dict(pid=os.getpid(), pgid=os.getpgrp(), argv=sys.argv[4:], env=dict(os.environ), cwd=os.getcwd()))
if scenario == 'exit':
    sys.exit(7)
if scenario == 'hang':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c',
        'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    record(dict(child=child.pid))
    time.sleep(60)
if scenario == 'eof_leaving_child':
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(60)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    record(dict(child=child.pid))
    for line in sys.stdin:
        record(dict(received=json.loads(line)))
    record(dict(eof=True))
    sys.exit(0)
notice = dict(type='system', subtype='ui_invalidate', event='ui.render',
              uuid='00000000-0000-4000-8000-000000000001', session_id='fixture-session')
for line in sys.stdin:
    control = json.loads(line)
    record(dict(received=control))
    ident = control['request_id']
    initializing = control['request']['subtype'] == 'initialize'
    if scenario == 'huge':
        print('x' * (300 << 10), flush=True)
        continue
    if scenario == 'garbage':
        print('fixture-private-error', flush=True)
        continue
    if scenario == 'invalid_utf8':
        sys.stdout.buffer.write(b'\xff\n'); sys.stdout.buffer.flush()
        continue
    if scenario == 'duplicate_keys':
        print('{"type":"control_response","type":"user"}', flush=True)
        continue
    if scenario == 'unterminated':
        sys.stdout.write('{"type":'); sys.stdout.flush(); sys.exit(0)
    if scenario in ('request', 'assistant', 'unknown_notice', 'old_system_init'):
        message = {'request': dict(type='control_request', request_id='foreign', request={}),
                   'assistant': dict(type='assistant', message='fixture@example.invalid'),
                   'unknown_notice': dict(notice, event='other'),
                   'old_system_init': dict(notice, subtype='init')}[scenario]
        emit(message)
        continue
    if scenario == 'flood':
        for _ in range(100):
            emit(notice)
        continue
    if scenario == 'error' and not initializing or scenario == 'init_error' and initializing:
        print('fixture-private-stderr', file=sys.stderr, flush=True)
        emit(dict(type='control_response', response=dict(subtype='error', request_id=ident,
                                                       error='fixture-private-error')))
        continue
    if initializing:
        emit(notice)
    response = dict(subtype='success', request_id='wrong' if scenario == 'wrong_id' else ident,
                    response=init if initializing else usage)
    if initializing:
        response.update(pending_permission_requests=[], pending_user_dialog_requests=[])
    if scenario == 'pending':
        response['pending_permission_requests'] = [{'secret': 'fixture-private-error'}]
    if scenario == 'bad_payload':
        response['response'] = []
    emit(dict(type='control_response', response=response))
    if not initializing:
        if scenario in ('nonzero_with_child', 'signaled_with_child'):
            child = subprocess.Popen([sys.executable, '-I', '-B', '-c', 'import time; time.sleep(60)'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            record(dict(child=child.pid, child_pgid=os.getpgid(child.pid)))
        if scenario == 'trailing':
            emit(dict(type='assistant', message='fixture@example.invalid'))
        if scenario == 'hang_after_usage':
            time.sleep(60)
        if scenario == 'nonzero':
            sys.exit(3)
record(dict(eof=True))
if scenario == 'nonzero_with_child':
    sys.exit(3)
if scenario == 'signaled_with_child':
    os.kill(os.getpid(), signal.SIGTERM)
'''


class Contract(unittest.TestCase):
    def parse(self, usage=None, initialization=None):
        return reader.parse(copy.deepcopy(INITIALIZATION if initialization is None else initialization),
                            copy.deepcopy(USAGE if usage is None else usage))

    def refusal(self, usage, reason):
        with self.assertRaises(reader.ReadError) as caught:
            self.parse(usage)
        self.assertEqual(caught.exception.args, (reason,))


class ParseTests(Contract):
    def test_projection_is_nonsecret_unqualified_and_not_a_billing_fact(self):
        projected = self.parse()
        self.assertEqual(projected, EXPECTED)
        self.assertEqual(subscription_billing.problem(projected, {}, 1794614400), 'billing_malformed')
        shown = json.dumps(projected)
        for secret in ('fixture@example.invalid', 'Fixture Org', '/synthetic', 'fixture-model',
                       'observed_at', 'valid_until', 'paid_crossover', 'account', 'pool', 'path'):
            self.assertNotIn(secret, shown)

    def test_unknown_raw_fields_never_escape_the_projection(self):
        value = copy.deepcopy(USAGE)
        value['private'] = {'email': 'fixture@example.invalid', 'error': 'fixture-private-error'}
        value['rate_limits']['five_hour']['display_name'] = 'fixture@example.invalid'
        value['rate_limits']['extra_usage']['currency'] = 'fixture-private-error'
        self.assertEqual(self.parse(value), EXPECTED)

    def test_unavailable_response_does_not_invent_meters_or_extra_usage(self):
        value = dict(session=copy.deepcopy(USAGE['session']), subscription_type=None,
                     rate_limits_available=False, rate_limits=None, behaviors=None)
        self.assertEqual(self.parse(value, {'account': {'tokenSource': 'none', 'apiProvider': 'firstParty'}}),
                         dict(qualified=False, reason='claude_source_unqualified', subscription_type=None,
                              rate_limits_available=False, extra_usage_enabled=None, aggregate_meters={}))

    def test_missing_meter_values_remain_absent_not_zero(self):
        for row in (None, {}, {'utilization': None}, {'resets_at': None}, {'utilization': 1},
                    {'resets_at': '2026-11-20T00:00:00Z'}):
            with self.subTest(row=row):
                value = copy.deepcopy(USAGE)
                value['rate_limits'] = {'five_hour': row}
                self.assertEqual(self.parse(value)['aggregate_meters'], {})
        value['rate_limits'] = {}
        self.assertEqual(self.parse(value)['aggregate_meters'], {})
        del value['rate_limits']
        self.assertEqual(self.parse(value)['aggregate_meters'], {})

    def test_extra_usage_enabled_or_unknown_is_never_billing_authority(self):
        for extra, want in (({'is_enabled': True}, True), ({'is_enabled': False}, False), ({}, None),
                            (None, None)):
            with self.subTest(extra=extra):
                value = copy.deepcopy(USAGE)
                value['rate_limits'] = {'extra_usage': extra}
                projected = self.parse(value)
                self.assertIs(projected['extra_usage_enabled'], want)
                self.assertEqual(subscription_billing.problem(projected, {}, 1794614400), 'billing_malformed')

    def test_known_subscription_values_are_metadata_only(self):
        for subscription in (None, 'pro', 'max', 'team', 'enterprise'):
            with self.subTest(subscription=subscription):
                value = dict(USAGE, subscription_type=subscription)
                self.assertEqual(self.parse(value)['subscription_type'], subscription)

    def test_inference_or_transcript_behavior_refuses(self):
        for key, bad in (('total_cost_usd', 0.01), ('total_api_duration_ms', 1), ('model_usage', {'fixture': {}}),
                         ('total_cost_usd', False), ('total_api_duration_ms', '0'), ('model_usage', [])):
            with self.subTest(key=key, bad=bad):
                value = copy.deepcopy(USAGE)
                value['session'][key] = bad
                self.refusal(value, 'claude_inference_unverified')
        for key in ('total_cost_usd', 'total_api_duration_ms', 'model_usage'):
            value = copy.deepcopy(USAGE)
            del value['session'][key]
            self.refusal(value, 'claude_inference_unverified')
        for behaviors in ({}, [], {'private': 'fixture@example.invalid'}):
            self.refusal(dict(USAGE, behaviors=behaviors), 'claude_behaviors_unverified')
        value = copy.deepcopy(USAGE)
        del value['behaviors']
        self.refusal(value, 'claude_behaviors_unverified')

    def test_invalid_metadata_types_refuse(self):
        for key, bad in (('subscription_type', 'fixture@example.invalid'), ('subscription_type', []),
                         ('rate_limits_available', 1), ('rate_limits_available', None),
                         ('rate_limits', []), ('session', None)):
            with self.subTest(key=key, bad=bad):
                self.refusal(dict(USAGE, **{key: bad}), 'claude_metadata_invalid' if key != 'session'
                             else 'claude_inference_unverified')
        for extra in (False, [], {'is_enabled': 'false'}, {'is_enabled': 0}, {'is_enabled': None}):
            value = copy.deepcopy(USAGE)
            value['rate_limits']['extra_usage'] = extra
            self.refusal(value, 'claude_metadata_invalid')

    def test_invalid_aggregate_types_refuse_even_if_the_other_field_is_missing(self):
        for row in ([], 0, {'utilization': True}, {'utilization': '1'}, {'utilization': -1},
                    {'utilization': 101}, {'utilization': math.nan}, {'utilization': math.inf},
                    {'resets_at': 1}, {'resets_at': 'fixture@example.invalid'},
                    {'resets_at': '2026-02-30T00:00:00Z'}, {'resets_at': '2026-11-14'},
                    {'resets_at': '2026-11-14T00:00:00'},
                    {'resets_at': '2026-11-14T00:00:00+00:60'}):
            with self.subTest(row=row):
                value = copy.deepcopy(USAGE)
                value['rate_limits']['five_hour'] = row
                self.refusal(value, 'claude_meter_invalid')

    def test_valid_percent_bounds_and_iso_offsets_are_preserved_not_redated(self):
        value = copy.deepcopy(USAGE)
        value['rate_limits']['five_hour'] = {'utilization': 0, 'resets_at': '2026-11-14T05:00:00Z'}
        value['rate_limits']['seven_day'] = {'utilization': 100.0, 'resets_at': '2026-11-20T00:00:00-03:00'}
        self.assertEqual(self.parse(value)['aggregate_meters'],
                         {'five_hour': {'utilization': 0, 'resets_at': '2026-11-14T05:00:00Z'},
                          'seven_day': {'utilization': 100.0, 'resets_at': '2026-11-20T00:00:00-03:00'}})

    def test_unsupported_scopes_never_silently_drop_constraints(self):
        for key, scoped in (('model_scoped', {'fixture': {}}), ('limits', [{}]),
                            ('seven_day_oauth_apps', {}), ('seven_day_opus', {}),
                            ('seven_day_sonnet', {}), ('unknown_meter', None),
                            ('model_scoped', False), ('limits', '')):
            with self.subTest(key=key, scoped=scoped):
                value = copy.deepcopy(USAGE)
                value['rate_limits'][key] = scoped
                self.refusal(value, 'claude_scope_unsupported')
        value = copy.deepcopy(USAGE)
        value['rate_limits'].update(model_scoped={}, limits=[], seven_day_oauth_apps=None,
                                    seven_day_opus=None, seven_day_sonnet=None)
        self.assertEqual(self.parse(value), EXPECTED)


class ExchangeTests(Contract):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix='claude-wire-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.bin = self.root / 'bin'
        self.bin.mkdir(mode=0o700)
        self.log = self.root / 'received.jsonl'
        self.log.touch(mode=0o600)
        self.fixture = self.root / 'fixture.json'
        self.fixture.write_text(json.dumps([INITIALIZATION, USAGE]))
        self.executable = self.bin / 'synthetic-control'
        self.executable.write_text('#!' + sys.executable + '\n' + DOUBLE)
        self.executable.chmod(0o700)
        self.environ = dict(HOME=str(self.home), PATH=str(self.bin), LANG='C',
                            ANTHROPIC_API_KEY='synthetic-key', CLAUDE_CODE_OAUTH_TOKEN='synthetic-token',
                            OPENAI_API_KEY='synthetic-other-key', NODE_OPTIONS='synthetic-options',
                            CLAUDE_CONFIG_DIR='/synthetic/foreign', XDG_CONFIG_HOME='/synthetic/foreign')

    def exchange(self, scenario='ok', timeout=2):
        return reader.exchange([str(self.executable), str(self.log), str(self.fixture), scenario],
                               self.environ, timeout=timeout)

    def entries(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_one_child_fixed_safe_flags_and_only_two_prompt_free_controls(self):
        self.assertEqual(self.exchange(), EXPECTED)
        entries = self.entries()
        self.assertEqual(entries[0]['argv'], FLAGS)
        self.assertEqual(entries[0]['pid'], entries[0]['pgid'])
        self.assertEqual(entries[0]['cwd'], '/')
        self.assertEqual([entry['received'] for entry in entries if 'received' in entry], [
            {'type': 'control_request', 'request_id': 'contract-init',
             'request': {'subtype': 'initialize', 'hooks': None}},
            {'type': 'control_request', 'request_id': 'contract-usage',
             'request': {'subtype': 'get_usage', 'skip_behaviors': True}},
        ])
        self.assertEqual(sum('pid' in entry for entry in entries), 1)
        self.assertTrue(entries[-1]['eof'])

    def test_environment_drops_credentials_and_configuration_overrides(self):
        self.environ.update(PATH='relative/bin:.:/usr/bin:/bin', TMPDIR='relative')
        self.exchange()
        env = self.entries()[0]['env']
        self.assertTrue(set(env) <= {'HOME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR',
                                     'LC_CTYPE', '__CF_USER_TEXT_ENCODING'}, env)
        self.assertEqual(env['HOME'], str(self.home))
        self.assertEqual(env['PATH'], '/usr/bin:/bin')
        self.assertNotIn('TMPDIR', env)
        self.assertNotIn('synthetic-key', json.dumps(env))

    def test_no_default_or_relative_executable_discovery(self):
        for command in (None, [], '', ['claude'], ['./synthetic-control'], [True]):
            with self.subTest(command=command), self.assertRaises(reader.ReadError) as caught:
                reader.exchange(command, self.environ)
            self.assertEqual(str(caught.exception), 'claude_command_invalid')
        self.assertEqual(self.entries(), [])

    def test_unsafe_home_and_invalid_deadline_refuse_before_spawning(self):
        command = [str(self.executable), str(self.log), str(self.fixture), 'ok']
        for home in (None, '.', '/nonexistent-claude-wire-home'):
            with self.subTest(home=home), self.assertRaises(reader.ReadError) as caught:
                reader.exchange(command, dict(self.environ, HOME=home))
            self.assertEqual(str(caught.exception), 'claude_environment_invalid')
        for timeout in (0, -1, True, math.nan, math.inf, 6, '1'):
            with self.subTest(timeout=timeout), self.assertRaises(reader.ReadError) as caught:
                reader.exchange(command, self.environ, timeout)
            self.assertEqual(str(caught.exception), 'claude_timeout_invalid')
        self.assertEqual(self.entries(), [])

    def test_home_must_be_private_and_not_a_symlink(self):
        command = [str(self.executable), str(self.log), str(self.fixture), 'ok']
        alias = self.root / 'alias'
        alias.symlink_to(self.home, target_is_directory=True)
        for home in (alias, self.home):
            if home == self.home:
                self.home.chmod(0o755)
            with self.subTest(home=str(home)), self.assertRaises(reader.ReadError) as caught:
                reader.exchange(command, dict(self.environ, HOME=str(home)))
            self.assertEqual(str(caught.exception), 'claude_environment_invalid')
        self.assertEqual(self.entries(), [])

    def test_private_tmpdir_is_preserved(self):
        self.environ['TMPDIR'] = str(self.root)
        self.exchange()
        self.assertEqual(self.entries()[0]['env']['TMPDIR'], str(self.root))

    def test_spawn_failure_is_a_fixed_reason_without_the_path(self):
        with self.assertRaises(reader.ReadError) as caught:
            reader.exchange(['/nonexistent-fixture@example.invalid'], self.environ)
        self.assertEqual(caught.exception.args, ('claude_unavailable',))

    def test_errors_unexpected_messages_and_bad_framing_are_fixed_refusals(self):
        cases = {'exit': 'claude_exited', 'error': 'claude_protocol_error', 'init_error': 'claude_protocol_error',
                 'wrong_id': 'claude_protocol_unexpected', 'request': 'claude_protocol_unexpected',
                 'assistant': 'claude_protocol_unexpected', 'unknown_notice': 'claude_protocol_unexpected',
                 'old_system_init': 'claude_protocol_unexpected', 'trailing': 'claude_protocol_unexpected',
                 'pending': 'claude_protocol_unexpected', 'flood': 'claude_protocol_unexpected',
                 'garbage': 'claude_protocol_invalid', 'huge': 'claude_protocol_invalid',
                 'invalid_utf8': 'claude_protocol_invalid', 'duplicate_keys': 'claude_protocol_invalid',
                 'unterminated': 'claude_protocol_invalid', 'bad_payload': 'claude_protocol_error',
                 'nonzero': 'claude_exited'}
        for scenario, reason in cases.items():
            self.log.write_text('')
            with self.subTest(scenario=scenario), self.assertRaises(reader.ReadError) as caught:
                self.exchange(scenario)
            self.assertEqual(caught.exception.args, (reason,))
            self.assertNotIn('fixture-private', repr(caught.exception))
            if scenario in ('error', 'init_error'):
                self.assertEqual(sum('received' in entry for entry in self.entries()),
                                 2 if scenario == 'error' else 1)

    def test_exchange_cannot_return_unprojected_usage_or_inference(self):
        value = copy.deepcopy(USAGE)
        value['session']['total_cost_usd'] = 1
        self.fixture.write_text(json.dumps([INITIALIZATION, value]))
        with self.assertRaises(reader.ReadError) as caught:
            self.exchange()
        self.assertEqual(str(caught.exception), 'claude_inference_unverified')

    def test_normal_shutdown_is_graceful_without_group_signals(self):
        with mock.patch.object(os, 'killpg', wraps=os.killpg) as signals:
            self.assertEqual(self.exchange(), EXPECTED)
        self.assertEqual(signals.call_args_list, [])
        with self.assertRaises(ProcessLookupError):
            os.kill(self.entries()[0]['pid'], 0)

    def test_failed_exit_after_valid_responses_kills_helpers_before_reaping(self):
        real_killpg = os.killpg
        for scenario, code, status in (('nonzero_with_child', os.CLD_EXITED, 3),
                                       ('signaled_with_child', os.CLD_KILLED, signal.SIGTERM)):
            with self.subTest(scenario=scenario):
                signals, removed = [], False
                self.log.write_text('')

                def killpg(pid, sig):
                    exited = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    signals.append((sig, exited))
                    return real_killpg(pid, sig)

                try:
                    with mock.patch.object(os, 'killpg', side_effect=killpg), \
                            self.assertRaises(reader.ReadError) as caught:
                        self.exchange(scenario)
                    self.assertEqual(str(caught.exception), 'claude_exited')
                    entries = self.entries()
                    leader = entries[0]['pid']
                    helpers = [entry for entry in entries if 'child' in entry]
                    self.assertEqual(len(helpers), 1)
                    self.assertEqual(helpers[0]['child_pgid'], leader)
                    self.assertTrue(entries[-1]['eof'])
                    self.assertEqual([entry['received']['request']['subtype'] for entry in entries
                                      if 'received' in entry], ['initialize', 'get_usage'])
                    self.assertEqual([sig for sig, _ in signals], [signal.SIGTERM, signal.SIGKILL])
                    for _, exited in signals:
                        self.assertIsNotNone(exited)  # each signal must precede reaping the failed leader
                        self.assertEqual((exited.si_pid, exited.si_code, exited.si_status), (leader, code, status))
                    for owned in (leader, helpers[0]['child']):
                        end = time.monotonic() + 3
                        while time.monotonic() < end:
                            try:
                                os.kill(owned, 0)
                            except ProcessLookupError:
                                break
                            time.sleep(0.05)
                        with self.assertRaises(ProcessLookupError):
                            os.kill(owned, 0)
                    removed = True
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(leader, 0)
                finally:
                    # Red runs still own the leaked helper; never signal its group after the leader is reaped.
                    if not removed:
                        for entry in self.entries():
                            if 'child' not in entry:
                                continue
                            try:
                                os.kill(entry['child'], signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            end = time.monotonic() + 3
                            while time.monotonic() < end:
                                try:
                                    os.kill(entry['child'], 0)
                                except ProcessLookupError:
                                    break
                                time.sleep(0.05)
                            with self.assertRaises(ProcessLookupError):
                                os.kill(entry['child'], 0)

    def test_timeout_kills_owned_group_before_reaping_even_when_leader_exits_on_eof(self):
        real_killpg = os.killpg
        for scenario in ('hang', 'eof_leaving_child', 'hang_after_usage'):
            signals = []
            def killpg(pid, sig):
                os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                signals.append(sig)
                return real_killpg(pid, sig)
            self.log.write_text('')
            started = time.monotonic()
            with self.subTest(scenario=scenario), mock.patch.object(os, 'killpg', side_effect=killpg), \
                    self.assertRaises(reader.ReadError) as caught:
                self.exchange(scenario, timeout=0.4)
            self.assertEqual(str(caught.exception), 'claude_timeout')
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(signals, [signal.SIGTERM, signal.SIGKILL])
            entries = self.entries()
            pid = entries[0]['pid']
            # macOS can report EPERM for a group containing an orphan awaiting reaping; check owned PIDs first.
            for owned in [pid, *[entry['child'] for entry in entries if 'child' in entry]]:
                end = time.monotonic() + 3
                while time.monotonic() < end:
                    try:
                        os.kill(owned, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.05)
                with self.assertRaises(ProcessLookupError):
                    os.kill(owned, 0)
            with self.assertRaises(ProcessLookupError):
                os.killpg(pid, 0)
            if scenario == 'eof_leaving_child':
                self.assertTrue(entries[-1]['eof'])

    def test_readerror_never_preserves_arbitrary_native_text(self):
        self.assertEqual(str(reader.ReadError('fixture-private-error')), 'claude_protocol_invalid')


if __name__ == '__main__':
    unittest.main()
