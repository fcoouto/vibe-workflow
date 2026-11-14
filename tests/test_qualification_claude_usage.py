"""Synthetic wire-contract tests only: no native client, account, network or model calls."""
import copy
from contextlib import ExitStack, contextmanager
import errno
import json
import math
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
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
if scenario == 'delayed_exit':
    sys.stdout.close()
    time.sleep(0.15)
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
                    {'resets_at': '2026-11-14T00:00:00+00:60'},
                    {'resets_at': '2026-11-14T00:00:00-00:60'},
                    {'utilization': 1, 'resets_at': '2026-11-14T00:00:00+00:60'},
                    {'utilization': 1, 'resets_at': '2026-11-14T00:00:00-00:60'}):
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
        for reset in ('2026-11-14T00:00:00+00:59', '2026-11-14T00:00:00-00:59'):
            with self.subTest(reset=reset):
                value['rate_limits']['five_hour'] = {'utilization': 1, 'resets_at': reset}
                self.assertEqual(self.parse(value)['aggregate_meters']['five_hour'],
                                 {'utilization': 1, 'resets_at': reset})

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
        self.processes, self.events, self.resources = [], [], []
        self.allow_rescue = False
        self.enterContext(self.owned_children())

    @contextmanager
    def owned_children(self):
        """Spy on successful real reaps, including subprocess's cached destructor bindings; rescue red children."""
        real_waitpid, real_killpg, real_popen = os.waitpid, os.killpg, subprocess.Popen
        self.process_class = real_popen

        def waitpid(pid, flags):
            result = real_waitpid(pid, flags)
            if result[0] > 0:
                self.events.append(('reap', result[0], os.waitstatus_to_exitcode(result[1])))
            return result

        def killpg(pid, sig):
            if sig:
                self.events.append(('signal', pid, sig))
            return real_killpg(pid, sig)

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            self.processes.append((process, process.stdin, process.stdout))
            self.events.append(('spawn', process.pid))
            return process

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(os, 'waitpid', side_effect=waitpid))
            stack.enter_context(mock.patch.object(os, 'killpg', side_effect=killpg))
            stack.enter_context(mock.patch.object(subprocess, 'Popen', side_effect=spawn))
            if hasattr(subprocess, '_waitpid'):
                stack.enter_context(mock.patch.object(subprocess, '_waitpid', side_effect=waitpid))
            poll = real_popen._internal_poll
            defaults = tuple(waitpid if value is real_waitpid else value for value in poll.__defaults__)
            stack.enter_context(mock.patch.object(poll, '__defaults__', defaults))
            if hasattr(subprocess, '_del_safe'):
                stack.enter_context(mock.patch.object(subprocess._del_safe, 'waitpid', new=staticmethod(waitpid)))
            try:
                yield
            finally:
                rescued = []
                for process, stdin, stdout in self.processes:
                    if not any(event[:2] == ('reap', process.pid) for event in self.events):
                        rescued.append(process.pid)
                        try:
                            real_killpg(process.pid, signal.SIGKILL)  # this owned leader is still unreaped
                        except OSError:
                            pass
                        end = time.monotonic() + 3
                        while time.monotonic() < end:
                            pid, status = real_waitpid(process.pid, os.WNOHANG)
                            if pid:
                                process.returncode = os.waitstatus_to_exitcode(status)
                                break
                            time.sleep(0.01)
                        else:
                            self.fail('Independent cleanup could not reap its owned child')
                    for pipe in (stdin, stdout):
                        if pipe is not None:
                            pipe.close()
                # Red runs still own the leaked helper; never signal its group after the leader is reaped.
                for entry in self.entries():
                    if 'child' in entry:
                        try:
                            os.kill(entry['child'], signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        self.assert_gone(entry['child'])
                self.assertTrue(self.allow_rescue or not rescued, 'Exchange left an owned child unreaped')

    def assert_gone(self, pid):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail('Owned synthetic process remains alive')

    def assert_order(self, process, status, failed):
        events = [entry for entry in self.events if entry[1] == process.pid and entry[0] != 'spawn']
        want = [('signal', process.pid, signal.SIGTERM), ('signal', process.pid, signal.SIGKILL)] if failed else []
        self.assertEqual(events, want + [('reap', process.pid, status)])
        self.assert_gone(process.pid)
        for entry in self.entries():
            if 'child' in entry:
                self.assert_gone(entry['child'])
        # macOS can report EPERM for a group containing an orphan awaiting reaping; check owned PIDs first.
        with self.assertRaises(ProcessLookupError):
            os.killpg(process.pid, 0)

    @contextmanager
    def exit_backend(self, kqueue=False):
        """Use the native backend for core tests; force real kqueue only for macOS-specific extra coverage."""
        self.assertTrue(hasattr(reader, '_waitid_capable'), 'Local lifecycle capability seam is missing')
        if not kqueue:
            yield
            return
        if sys.platform != 'darwin' or not callable(getattr(select, 'kqueue', None)):
            self.skipTest('macOS-specific kqueue assertion')
        with mock.patch.object(reader, '_waitid_capable', return_value=False):
            yield

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
        native_waitid, original = reader._waitid_capable(), reader._ExitObserver.open
        def opened(observer):
            original(observer)
            self.assertEqual(observer.using_waitid, native_waitid)
        with self.exit_backend(), mock.patch.object(reader._ExitObserver, 'open', new=opened):
            self.assertEqual(self.exchange(), EXPECTED)
        self.assert_order(self.processes[-1][0], 0, False)

    def test_failed_exit_after_valid_responses_kills_helpers_before_reaping(self):
        for scenario, status in (('nonzero_with_child', 3), ('signaled_with_child', -signal.SIGTERM)):
            with self.subTest(scenario=scenario):
                self.log.write_text('')
                with self.assertRaises(reader.ReadError) as caught:
                    self.exchange(scenario)
                self.assertEqual(str(caught.exception), 'claude_exited')
                entries = self.entries()
                self.assertEqual(next(entry['child_pgid'] for entry in entries if 'child' in entry), entries[0]['pid'])
                self.assertTrue(entries[-1]['eof'])
                self.assert_order(self.processes[-1][0], status, True)

    def test_timeout_kills_owned_group_before_reaping_even_when_leader_exits_on_eof(self):
        for scenario in ('hang', 'eof_leaving_child', 'hang_after_usage'):
            self.log.write_text('')
            started = time.monotonic()
            with self.subTest(scenario=scenario), self.assertRaises(reader.ReadError) as caught:
                self.exchange(scenario, timeout=0.4)
            self.assertEqual(str(caught.exception), 'claude_timeout')
            self.assertLess(time.monotonic() - started, 3)
            status = 0 if scenario == 'eof_leaving_child' else -signal.SIGKILL if scenario == 'hang' else -signal.SIGTERM
            self.assert_order(self.processes[-1][0], status, True)
            if scenario == 'eof_leaving_child':
                self.assertTrue(self.entries()[-1]['eof'])

    def test_supported_kqueue_zero_delayed_and_failed_exits(self):
        with self.exit_backend(kqueue=True):
            for scenario, status in (('ok', 0), ('delayed_exit', 0), ('nonzero_with_child', 3),
                                     ('signaled_with_child', -signal.SIGTERM)):
                with self.subTest(scenario=scenario):
                    self.log.write_text('')
                    if status:
                        with self.assertRaises(reader.ReadError) as caught:
                            self.exchange(scenario)
                        self.assertEqual(str(caught.exception), 'claude_exited')
                    else:
                        self.assertEqual(self.exchange(scenario), EXPECTED)
                    self.assert_order(self.processes[-1][0], status, bool(status))

    def test_kqueue_is_allocated_before_spawn_and_registered_before_controls(self):
        with self.exit_backend(kqueue=True):
            original = reader._ExitObserver.attach
            original_open = reader._ExitObserver.open
            def opened(observer):
                original_open(observer)
                self.assertEqual(self.processes, [])
                self.resources.append(observer.queue)
            def attach(observer, pid):
                self.assertFalse(any('received' in entry for entry in self.entries()))
                self.assertFalse(os.get_inheritable(observer.queue.fileno()))
                return original(observer, pid)
            with mock.patch.object(reader._ExitObserver, 'attach', new=attach), \
                    mock.patch.object(reader._ExitObserver, 'open', new=opened):
                self.assertEqual(self.exchange(), EXPECTED)
            self.assertTrue(self.resources[-1].closed)

    def test_kqueue_allocation_and_spawn_failures_close_without_a_child(self):
        with self.exit_backend(kqueue=True):
            before = len(os.listdir('/dev/fd'))
            with mock.patch.object(select, 'kqueue', side_effect=OSError('private')), self.assertRaises(reader.ReadError):
                self.exchange()
            with mock.patch.object(os, 'set_inheritable', side_effect=OSError('private')), \
                    self.assertRaises(reader.ReadError):
                self.exchange()
            self.assertEqual(self.processes, [])
            self.assertLessEqual(len(os.listdir('/dev/fd')), before)
            original = reader._ExitObserver.open
            def opened(observer):
                original(observer)
                self.resources.append(observer.queue)
            with mock.patch.object(reader._ExitObserver, 'open', new=opened), self.assertRaises(reader.ReadError):
                reader.exchange(['/nonexistent-private'], self.environ)
            self.assertTrue(self.resources[-1].closed)

    def test_kqueue_registration_of_exited_child_refuses_without_sending_controls(self):
        with self.exit_backend(kqueue=True):
            original = reader._ExitObserver.attach
            def attach(observer, pid):
                self.processes[-1][0].stdout.read()  # EOF from an exiting child, not a reap
                time.sleep(0.05)
                return original(observer, pid)
            with mock.patch.object(reader._ExitObserver, 'attach', new=attach), self.assertRaises(reader.ReadError) as caught:
                self.exchange('exit')
            self.assertEqual(str(caught.exception), 'claude_protocol_invalid')
            self.assertFalse(any('received' in entry for entry in self.entries()))
            self.assert_order(self.processes[-1][0], 7, True)

    def test_observer_and_session_setup_errors_still_signal_and_reap(self):
        with self.exit_backend():
            before = len(os.listdir('/dev/fd'))
            with mock.patch.object(reader, '_ExitObserver', side_effect=RuntimeError('private')), \
                    self.assertRaises(reader.ReadError):
                self.exchange()
            self.assertEqual(self.processes, [])
            for target, name in ((reader._ExitObserver, 'attach'), (reader._ExitObserver, 'wait'),
                                 (reader._Session, '__init__'), (reader.os, 'set_blocking'),
                                 (reader.selectors, 'DefaultSelector')):
                with self.subTest(name=name):
                    self.log.write_text('')
                    with mock.patch.object(target, name, side_effect=RuntimeError('private')), \
                            self.assertRaises(reader.ReadError) as caught:
                        self.exchange()
                    self.assertEqual(str(caught.exception), 'claude_protocol_invalid')
                    process = self.processes[-1][0]
                    self.assert_order(process, process.returncode, True)
                    self.assertTrue(self.processes[-1][1].closed and self.processes[-1][2].closed)
            self.assertLessEqual(len(os.listdir('/dev/fd')), before)

    def test_terminal_status_validation_rejects_errors_and_accepts_ordinary_eof(self):
        with self.exit_backend(kqueue=True):
            observer = reader._ExitObserver()
            observer.pid = 12345
            event = dict(ident=12345, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_EOF,
                         fflags=select.KQ_NOTE_EXIT | 0x04000000, data=0)
            self.assertEqual(observer.kqueue_status([SimpleNamespace(**event)]), 0)
            for change in ({'ident': 1}, {'filter': select.KQ_FILTER_READ},
                           {'flags': select.KQ_EV_ERROR, 'data': errno.ESRCH}, {'fflags': select.KQ_NOTE_EXIT},
                           {'fflags': 0x04000000}, {'data': -1}, {'data': True}, {'data': 65536}, {'data': 0x7f},
                           {'flags': False}, {'data': '0'}):
                with self.subTest(change=change), self.assertRaises(reader.ReadError):
                    observer.kqueue_status([SimpleNamespace(**dict(event, **change))])
            for events in ([], [SimpleNamespace(**event)] * 2):
                with self.assertRaises(reader.ReadError):
                    observer.kqueue_status(events)
            for raw, want in ((3 << 8, 3), (int(signal.SIGTERM), -signal.SIGTERM)):
                self.assertEqual(observer.kqueue_status([SimpleNamespace(**dict(event, data=raw))]), want)

    def test_waitid_status_validation_is_independent_of_kqueue(self):
        observer = reader._ExitObserver()
        observer.pid = 12345
        info = dict(si_pid=12345, si_code=os.CLD_EXITED, si_status=0)
        self.assertEqual(observer.waitid_status(SimpleNamespace(**info)), 0)
        with self.assertRaises(reader.ReadError):
            observer.waitid_status(None)
        with self.assertRaises(reader.ReadError) as caught:
            observer.wait(time.monotonic() - 1)
        self.assertEqual(str(caught.exception), 'claude_timeout')
        for change in ({'si_pid': 1}, {'si_code': os.CLD_STOPPED}, {'si_status': -1},
                       {'si_status': 256}, {'si_status': True}, {'si_code': os.CLD_KILLED, 'si_status': 0}):
            with self.subTest(change=change), self.assertRaises(reader.ReadError):
                observer.waitid_status(SimpleNamespace(**dict(info, **change)))

    def test_late_close_errors_cannot_signal_after_reaping_or_return_success(self):
        with self.exit_backend():
            for target in (reader._ExitObserver, reader._Session):
                original = target.close
                def close(handle, original=original):
                    original(handle)
                    raise OSError('private')
                with self.subTest(target=target), mock.patch.object(target, 'close', new=close), \
                        self.assertRaises(reader.ReadError) as caught:
                    self.exchange()
                self.assertEqual(str(caught.exception), 'claude_protocol_invalid')
                self.assert_order(self.processes[-1][0], 0, False)

    def test_inconsistent_final_reap_refuses_without_late_signaling(self):
        self.assertTrue(hasattr(reader, '_ExitObserver'), 'Local lifecycle observer is missing')
        # A false clean observation must not override the independent final successful reap's real status.
        original = reader._ExitObserver.wait
        def observe(observer, deadline):
            original(observer, deadline)
            return 0
        with mock.patch.object(reader._ExitObserver, 'wait', new=observe), self.assertRaises(reader.ReadError) as caught:
            self.exchange('nonzero')
        self.assertEqual(str(caught.exception), 'claude_exited')
        self.assert_order(self.processes[-1][0], 3, False)

    def test_pipe_close_faults_cannot_skip_other_finalization(self):
        class CloseOnce:
            def __init__(self, pipe):
                self.pipe, self.first = pipe, True
            def __getattr__(self, name):
                return getattr(self.pipe, name)
            def close(self):
                if self.first:
                    self.first = False
                    raise OSError('private')
                self.pipe.close()
        with self.exit_backend():
            original = reader._Session.__init__
            for name in ('stdin', 'stdout'):
                def session(handle, process, deadline):
                    original(handle, process, deadline)
                    setattr(process, name, CloseOnce(getattr(process, name)))
                with self.subTest(name=name), mock.patch.object(reader._Session, '__init__', new=session), \
                        self.assertRaises(reader.ReadError) as caught:
                    self.exchange()
                self.assertEqual(str(caught.exception), 'claude_protocol_invalid')
                self.assert_order(self.processes[-1][0], 0, name == 'stdin')
                self.assertTrue(all(pipe.closed for pipe in self.processes[-1][1:]))

    def test_signal_error_still_attempts_kill_and_final_reap(self):
        with self.exit_backend():
            real = os.killpg
            def signal_error(pid, sig):
                result = real(pid, sig)
                if sig == signal.SIGTERM:
                    raise OSError('private')
                return result
            with mock.patch.object(os, 'killpg', side_effect=signal_error), self.assertRaises(reader.ReadError) as caught:
                self.exchange('error')
            self.assertEqual(str(caught.exception), 'claude_protocol_error')
            self.assert_order(self.processes[-1][0], 0, True)

    def test_failed_final_wait_closes_resources_without_another_signal_phase(self):
        with self.exit_backend():
            self.allow_rescue = True  # independent finally will reap the deliberately interrupted final wait
            with mock.patch.object(self.process_class, 'wait', side_effect=OSError('private')), \
                    self.assertRaises(reader.ReadError) as caught:
                self.exchange()
            self.assertEqual(str(caught.exception), 'claude_protocol_invalid')
            self.assertFalse(any(event[0] == 'signal' for event in self.events))
            self.assertTrue(all(pipe.closed for pipe in self.processes[-1][1:]))

    def test_repeated_exchanges_do_not_accumulate_descriptors(self):
        with self.exit_backend():
            before = len(os.listdir('/dev/fd'))
            for _ in range(3):
                self.exchange()
                with self.assertRaises(reader.ReadError):
                    self.exchange('error')
            self.assertLessEqual(len(os.listdir('/dev/fd')), before)

    def test_readerror_never_preserves_arbitrary_native_text(self):
        self.assertEqual(str(reader.ReadError('fixture-private-error')), 'claude_protocol_invalid')


if __name__ == '__main__':
    unittest.main()
