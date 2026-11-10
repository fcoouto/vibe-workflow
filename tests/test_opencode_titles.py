"""Best-effort OpenCode titles through a disposable fake `opencode` executable."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime import opencode_titles, title_sync

SESSION = 'ses_0123456789abcdefghijklmnop'
DESIRED = title_sync.format_title('Thread naming', 'WORKING', 1)
FAKE = r'''#!{python} -I
"""Fake `opencode api`: one scripted behavior per call; titles persist in state."""
import json, os, sys, time
root = os.path.dirname(os.path.abspath(__file__))
state_path = os.path.join(root, 'state.json')
state = json.load(open(state_path))
with open(os.path.join(root, 'calls.jsonl'), 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\n')
step = state['steps'].pop(0) if state['steps'] else 'ok'
args = sys.argv[1:]
operation = args[1] if len(args) > 1 else ''
params = dict(a.split('=', 1) for i, a in enumerate(args) if i and args[i - 1] == '--param')
session = params.get('sessionID', '')
if operation == 'GET':
    path = args[2].split('?')[0] if len(args) > 2 else ''
    session = path.split('/')[3] if path.count('/') == 4 else ''
if step == 'down':
    json.dump(state, open(state_path, 'w'))
    sys.stderr.write('Unable to connect to the server\n'); sys.exit(1)
if step == 'user_edit':
    state['titles'][session] = 'Manual rename'
    json.dump(state, open(state_path, 'w'))
    sys.exit(1)
if step == 'sleep':
    json.dump(state, open(state_path, 'w'))
    time.sleep(10); sys.exit(0)
if step == 'missing_tag':
    json.dump(state, open(state_path, 'w'))
    print(json.dumps({'_tag': 'SessionNotFoundError', 'message': 'Session not found'}))
    sys.stderr.write('Request failed\n'); sys.exit(1)
if step == 'missing_status':
    json.dump(state, open(state_path, 'w'))
    print('<html>gateway</html>')
    sys.stderr.write('HTTP 404 Not Found\n'); sys.exit(1)
if step == 'missing' or session not in state['titles']:
    json.dump(state, open(state_path, 'w'))
    print(json.dumps({'_tag': 'SessionNotFoundError', 'message': 'Session not found'}))
    sys.stderr.write('HTTP 404 Not Found\n'); sys.exit(1)
if operation == 'session.update':
    body = json.loads(args[args.index('-d') + 1])
    if step != 'ignore_write':
        state['titles'][session] = body['title']
    json.dump(state, open(state_path, 'w'))
    sys.exit(0)
json.dump(state, open(state_path, 'w'))
if operation == 'GET':
    if step == 'malformed':
        print('not json {'); sys.exit(0)
    print(json.dumps({'data': state.get('messages', {}).get(session, []), 'cursor': None})); sys.exit(0)
if step == 'malformed':
    print('not json {'); sys.exit(0)
if step == 'shape':
    print(json.dumps({'id': session, 'title': 'x'})); sys.exit(0)
identity = 'ses_someoneelse0000000000000' if step == 'mismatch' else session
data = {'id': identity, 'projectID': 'prj', 'cost': 0, 'tokens': {}, 'time': {}, 'location': {}}
if state['titles'][session] is not None:
    data['title'] = state['titles'][session]
print(json.dumps({'data': data}))
'''


PROOF = 'vibe-title-5d0e7c1a9b'


def shell_call(command, status='running'):
    """One newest-first message page entry: an assistant message with one shell tool part."""
    return {'id': 'msg_1', 'type': 'assistant', 'content': [
        {'type': 'tool', 'name': 'shell', 'state': {'status': status, 'input': {'command': command}}}]}


class FakeOpenCode:
    def __init__(self, test, title='', steps=(), messages=None):
        self.directory = Path(tempfile.mkdtemp())
        test.addCleanup(lambda: __import__('shutil').rmtree(self.directory, ignore_errors=True))
        executable = self.directory / 'opencode'
        executable.write_text(FAKE.replace('{python}', sys.executable))
        executable.chmod(0o755)
        self.save(dict(titles={SESSION: title}, steps=list(steps), messages={SESSION: messages or []}))
        path = patch.dict(os.environ, {'PATH': str(self.directory) + os.pathsep + os.environ.get('PATH', '')})
        path.start()
        test.addCleanup(path.stop)

    def save(self, state):
        (self.directory / 'state.json').write_text(json.dumps(state))

    def state(self):
        return json.loads((self.directory / 'state.json').read_text())

    @property
    def title(self):
        return self.state()['titles'][SESSION]

    @property
    def calls(self):
        log = self.directory / 'calls.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


class Clock:
    def __init__(self, step=0.0):
        self.now, self.step, self.sleeps = 100.0, step, []

    def __call__(self):
        self.now += self.step
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class OpenCodeTitleTests(unittest.TestCase):
    def deliver(self, desired=DESIRED, baseline=None, session=SESSION, **options):
        self.clock = options.pop('clock', None) or Clock()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = opencode_titles.deliver(session, desired, baseline,
                                             sleep=self.clock.sleep, clock=self.clock, **options)
        self.assertEqual((out.getvalue(), err.getvalue()), ('', ''))
        return result

    def test_success_reads_writes_and_acknowledges_by_readback(self):
        fake = FakeOpenCode(self)
        self.assertEqual(self.deliver(), dict(ok=True, deferred=False, reason=None, baseline=DESIRED))
        self.assertEqual(fake.title, DESIRED)
        self.assertEqual([call[1] for call in fake.calls], ['session.get', 'session.update', 'session.get'])

    def test_current_title_needs_no_write(self):
        fake = FakeOpenCode(self, title=DESIRED)
        self.assertEqual(self.deliver(baseline=DESIRED)['ok'], True)
        self.assertEqual([call[1] for call in fake.calls], ['session.get'])

    def test_server_down_then_success_on_second_attempt(self):
        fake = FakeOpenCode(self, steps=['down'])
        self.assertEqual(self.deliver(), dict(ok=True, deferred=False, reason=None, baseline=DESIRED))
        self.assertEqual(fake.title, DESIRED)
        self.assertEqual(len(self.clock.sleeps), 1)

    def test_permanent_failure_defers_silently_after_bounded_attempts(self):
        fake = FakeOpenCode(self, steps=['down'] * 10)
        result = self.deliver()
        self.assertEqual(result, dict(ok=False, deferred=True, reason='failed'))
        self.assertEqual(len(fake.calls), opencode_titles.ATTEMPTS)
        self.assertEqual(self.clock.sleeps, list(opencode_titles.BACKOFF[:opencode_titles.ATTEMPTS - 1]))
        self.assertTrue(all(0 < pause <= 1 for pause in opencode_titles.BACKOFF))
        self.assertEqual(fake.title, '')

    def test_missing_executable_defers_without_raising(self):
        with patch('shutil.which', return_value=None):
            result = self.deliver()
        self.assertEqual(result, dict(ok=False, deferred=True, reason='unavailable'))
        self.assertEqual(self.clock.sleeps, [])

    def test_non_executable_binary_is_not_retried(self):
        fake = FakeOpenCode(self)
        (fake.directory / 'opencode').chmod(0o644)
        with patch('shutil.which', return_value=str(fake.directory / 'opencode')):
            self.assertEqual(self.deliver(), dict(ok=False, deferred=True, reason='unavailable'))
        self.assertEqual(self.clock.sleeps, [])

    def test_timeout_is_bounded_and_deferred(self):
        fake = FakeOpenCode(self, steps=['sleep'] * 3)
        result = self.deliver(timeout=0.3, clock=Clock())
        self.assertEqual(result, dict(ok=False, deferred=True, reason='timeout'))
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(fake.title, '')

    def test_total_wall_time_stops_further_attempts(self):
        fake = FakeOpenCode(self, steps=['down'] * 10)
        result = self.deliver(clock=Clock(step=opencode_titles.DEADLINE / 2))
        self.assertEqual(result['deferred'], True)
        self.assertEqual(len(fake.calls), 1)

    def test_malformed_mismatched_or_missing_sessions_defer(self):
        for step, reason in (('malformed', 'malformed'), ('shape', 'malformed'),
                             ('mismatch', 'identity'), ('missing', 'not_found'),
                             ('missing_tag', 'not_found'), ('missing_status', 'not_found')):
            with self.subTest(step=step):
                fake = FakeOpenCode(self, steps=[step] * 3)
                self.assertEqual(self.deliver(), dict(ok=False, deferred=True, reason=reason))
                self.assertEqual(fake.title, '')
                self.assertEqual(self.clock.sleeps, [])
                self.assertEqual(len(fake.calls), 1)
                self.assertNotIn('session.update', [call[1] for call in fake.calls])

    def test_failure_messages_do_not_contain_identifiers_or_output(self):
        FakeOpenCode(self, steps=['missing'])
        with self.assertRaises(OSError) as error:
            with opencode_titles.OpenCodeTitles() as native:
                native.read(SESSION)
        self.assertNotIn(SESSION, str(error.exception))
        self.assertNotIn('Session not found', str(error.exception))

    def test_invalid_session_or_title_is_rejected_without_spawning(self):
        fake = FakeOpenCode(self)
        for session in ('', 'session', 'ses', 'ses_x;rm -rf', '--help', 'ses_' + 'a' * 300, None, 7):
            with self.subTest(session=session):
                self.assertEqual(self.deliver(session=session),
                                 dict(ok=False, deferred=False, reason='invalid_request'))
        for desired in ('My title', DESIRED + '\n', DESIRED.removeprefix('￻'), None,
                        '▰▰▱▱▱ [' + 'W1] P · Thread naming · WORKING',
                        DESIRED.replace(' Thread', ' [' + 'W1] P · Thread')):
            with self.subTest(desired=desired):
                self.assertEqual(self.deliver(desired=desired),
                                 dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(fake.calls, [])

    def test_title_is_one_argv_element_without_shell_interpolation(self):
        topic = 'It\'s "$(touch pwned)" `id` $HOME; echo'
        desired = title_sync.format_title(topic, 'WORKING', 1)
        fake = FakeOpenCode(self)
        self.assertTrue(self.deliver(desired=desired)['ok'])
        self.assertEqual(fake.title, desired)
        update = next(call for call in fake.calls if call[1] == 'session.update')
        self.assertEqual(update, ['api', 'session.update', '--param', f'sessionID={SESSION}',
                                  '-d', json.dumps({'title': desired})])
        self.assertFalse((fake.directory / 'pwned').exists())
        self.assertFalse(Path('pwned').exists())

    def test_only_session_get_and_update_operations_are_invoked(self):
        fake = FakeOpenCode(self, steps=['down', 'ok', 'ignore_write'])
        self.deliver()
        self.assertTrue(fake.calls)
        for call in fake.calls:
            self.assertEqual(call[0], 'api')
            self.assertIn(call[1], {'session.get', 'session.update'})
        with self.assertRaises(ValueError):
            with opencode_titles.OpenCodeTitles() as native:
                native.call('session.prompt', SESSION)

    def test_unacknowledged_write_is_deferred_not_repeated(self):
        fake = FakeOpenCode(self, steps=['ok', 'ignore_write'])
        self.assertEqual(self.deliver(), dict(ok=False, deferred=True, reason='readback'))
        self.assertEqual([call[1] for call in fake.calls].count('session.update'), 1)

    def test_lost_acknowledgment_is_resolved_against_the_same_session(self):
        fake = FakeOpenCode(self, steps=['ok', 'ok', 'down'])
        self.assertEqual(self.deliver()['ok'], True)
        self.assertEqual([call[1] for call in fake.calls].count('session.update'), 1)
        self.assertEqual(fake.title, DESIRED)

    def test_manual_title_without_baseline_is_not_clobbered(self):
        fake = FakeOpenCode(self, title='Fix the login bug')
        self.assertEqual(self.deliver(), dict(ok=False, deferred=True, reason='manual_title'))
        self.assertEqual(fake.title, 'Fix the login bug')
        self.assertNotIn('session.update', [call[1] for call in fake.calls])

    def test_without_baseline_empty_default_or_managed_titles_are_written(self):
        older = title_sync.format_title('Thread naming', 'REVIEW', 2, user_attention=True)
        for current in ('', None, 'New session - 2026-11-08T10:11:12.345Z',
                        'Child session - 2026-11-08T10:11:12.345Z', older):
            with self.subTest(current=current):
                fake = FakeOpenCode(self, title=current)
                self.assertEqual(self.deliver()['ok'], True)
                self.assertEqual(fake.title, DESIRED)

    def test_baseline_mismatch_follows_manual_title_rules(self):
        baseline = title_sync.format_title('Thread naming', 'REVIEW', 2, user_attention=True)
        topic_edit = baseline.replace('Thread naming', 'Better naming')
        for current, expected in ((topic_edit, dict(ok=False, deferred=True, reason='topic_changed',
                                                      topic='Better naming')),
                                  ('Custom title', dict(ok=False, deferred=True, reason='manual_title')),
                                  (baseline.replace('▰▰▱▱▱', '▰▰▰▱▱'), dict(ok=False, deferred=True, reason='manual_title'))):
            with self.subTest(current=current):
                fake = FakeOpenCode(self, title=current)
                self.assertEqual(self.deliver(baseline=baseline), expected)
                self.assertEqual(fake.title, current)
        fake = FakeOpenCode(self, title=baseline)
        self.assertEqual(self.deliver(baseline=baseline)['ok'], True)
        self.assertEqual(fake.title, DESIRED)

    def test_adopt_overwrites_unknown_title_once_then_baseline_protects_later_edits(self):
        fake = FakeOpenCode(self, title='Generated summary of the chat')
        result = self.deliver(adopt=True)
        self.assertEqual(result, dict(ok=True, deferred=False, reason=None, baseline=DESIRED,
                                      previous='Generated summary of the chat'))
        self.assertEqual(fake.title, DESIRED)
        state = fake.state()
        state['titles'][SESSION] = 'Renamed by the user'
        fake.save(state)
        later = title_sync.format_title('Thread naming', 'REVIEW', 2, user_attention=True)
        self.assertEqual(self.deliver(desired=later, baseline=result['baseline']),
                         dict(ok=False, deferred=True, reason='manual_title'))
        self.assertEqual(fake.title, 'Renamed by the user')
        self.assertEqual([call[1] for call in fake.calls].count('session.update'), 1)

    def test_adoption_reports_previous_title_only_when_it_replaced_one(self):
        fake = FakeOpenCode(self, title=DESIRED)
        self.assertEqual(self.deliver(adopt=True),
                         dict(ok=True, deferred=False, reason=None, baseline=DESIRED, previous=None))
        long_title = 'x' * (title_sync.MAX_TITLE_BODY + 1)
        fake = FakeOpenCode(self, title=long_title)
        self.assertEqual(self.deliver(adopt=True)['previous'], None)
        self.assertEqual(fake.title, DESIRED)
        self.assertNotIn('previous', self.deliver(desired=title_sync.format_title('Thread naming', 'WORKING', 2), baseline=DESIRED))

    def test_adopt_is_not_sticky_across_calls(self):
        fake = FakeOpenCode(self, title='Generated summary of the chat')
        self.assertEqual(self.deliver(adopt=True)['ok'], True)
        state = fake.state()
        state['titles'][SESSION] = 'Another generated title'
        fake.save(state)
        self.assertEqual(self.deliver(), dict(ok=False, deferred=True, reason='manual_title'))
        self.assertEqual(fake.title, 'Another generated title')

    def test_adopt_with_failing_transport_defers_silently(self):
        fake = FakeOpenCode(self, title='Generated summary of the chat', steps=['down'] * 10)
        self.assertEqual(self.deliver(adopt=True), dict(ok=False, deferred=True, reason='failed'))
        self.assertEqual(fake.title, 'Generated summary of the chat')

    def test_adopt_covers_only_the_title_first_observed(self):
        fake = FakeOpenCode(self, title='Generated summary of the chat', steps=['ok', 'user_edit'])
        self.assertEqual(self.deliver(adopt=True), dict(ok=False, deferred=True, reason='manual_title'))
        self.assertEqual(fake.title, 'Manual rename')

    def test_invalid_adopt_is_rejected_without_spawning(self):
        fake = FakeOpenCode(self, title='Generated summary of the chat')
        for adopt in (1, 0, 'true', None, [True]):
            with self.subTest(adopt=adopt):
                self.assertEqual(self.deliver(adopt=adopt),
                                 dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(self.deliver(adopt=True, baseline=DESIRED),
                         dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(fake.calls, [])

    def test_unexpected_errors_never_escape(self):
        def broken():
            raise RuntimeError('private detail')
        self.assertEqual(self.deliver(transport=broken), dict(ok=False, deferred=True, reason='failed'))

    def test_proof_in_a_running_shell_call_verifies_the_session_before_writing(self):
        command = f'python3 -I -B scripts/opencode_title.py --proof {PROOF} < title.json'
        fake = FakeOpenCode(self, messages=[shell_call(command)])
        self.assertEqual(self.deliver(proof=PROOF), dict(ok=True, deferred=False, reason=None, baseline=DESIRED))
        self.assertEqual(fake.title, DESIRED)
        self.assertEqual(fake.calls[0], ['api', 'GET', f'/api/session/{SESSION}/message?limit=10'])
        self.assertEqual([call[1] for call in fake.calls[1:]], ['session.get', 'session.update', 'session.get'])

    def test_session_without_the_running_proof_is_never_written(self):
        # An inherited OPENCODE_SESSION_ID names another session, which is not running this command.
        for messages in ([], [shell_call('opencode serve --port 4096')],
                         [shell_call(f'opencode_title.py --proof {PROOF}', status='completed')],
                         [shell_call(f'opencode_title.py --proof {PROOF}x')],
                         [dict(shell_call(f'opencode_title.py --proof {PROOF}'), type='user')],
                         [{'id': 'msg_1', 'type': 'assistant', 'content': [  # only the shell tool counts
                             dict(shell_call(f'x --proof {PROOF}')['content'][0], name='task')]}],
                         [shell_call(f"x --proof '{PROOF}\"")],  # unmatched quotes
                         [{'id': 'msg_1', 'type': 'assistant', 'content': [{'type': 'text', 'text': PROOF}]}]):
            with self.subTest(messages=messages):
                fake = FakeOpenCode(self, messages=messages)
                self.assertEqual(self.deliver(proof=PROOF),
                                 dict(ok=False, deferred=False, reason='session_unverified'))
                self.assertEqual(fake.title, '')
                self.assertEqual([call[1] for call in fake.calls], ['GET'])

    def test_quoted_proof_and_odd_parts_still_verify(self):
        odd = {'id': 'msg_0', 'type': 'assistant', 'content': [
            'not a part', None, {'type': 'tool', 'name': 'shell', 'state': 3},
            {'type': 'tool', 'name': 'shell', 'state': {'status': 'running', 'input': 'x'}}]}
        for command in (f"x --proof '{PROOF}' < t.json", f'x --proof "{PROOF}"'):
            with self.subTest(command=command):
                fake = FakeOpenCode(self, messages=['junk', odd, shell_call(command)])
                self.assertTrue(self.deliver(proof=PROOF)['ok'])
                self.assertEqual(fake.title, DESIRED)

    def test_proof_session_unknown_to_the_server_is_final(self):
        fake = FakeOpenCode(self, steps=['missing'], messages=[shell_call(f'x --proof {PROOF}')])
        self.assertEqual(self.deliver(proof=PROOF), dict(ok=False, deferred=False, reason='session_unverified'))
        self.assertEqual(len(fake.calls), 1)

    def test_proof_without_a_usable_session_is_session_unknown_after_validation(self):
        fake = FakeOpenCode(self)
        for session in (None, '', 'not-a-session'):
            with self.subTest(session=session):
                self.assertEqual(self.deliver(session=session, proof=PROOF),
                                 dict(ok=False, deferred=False, reason='session_unknown'))
                self.assertEqual(self.deliver(session=session, proof='short'),
                                 dict(ok=False, deferred=False, reason='invalid_request'))
                self.assertEqual(self.deliver(session=session, desired='My title', proof=PROOF),
                                 dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(fake.calls, [])

    def test_proof_lookup_failures_defer_without_writing(self):
        for steps, reason, calls in ((['down'] * 3, 'failed', 3), (['malformed'] * 3, 'malformed', 1)):
            with self.subTest(reason=reason):
                fake = FakeOpenCode(self, steps=steps, messages=[shell_call(f'x --proof {PROOF}')])
                self.assertEqual(self.deliver(proof=PROOF), dict(ok=False, deferred=True, reason=reason))
                self.assertEqual(len(fake.calls), calls)
                self.assertEqual(fake.title, '')

    def test_proof_is_checked_once_across_retries(self):
        fake = FakeOpenCode(self, steps=['ok', 'down'], messages=[shell_call(f'x --proof {PROOF}')])
        self.assertTrue(self.deliver(proof=PROOF)['ok'])
        self.assertEqual([call[1] for call in fake.calls].count('GET'), 1)

    def test_invalid_proof_is_rejected_without_spawning(self):
        fake = FakeOpenCode(self, messages=[shell_call('x --proof short')])
        for proof in ('', 'short', 'x' * 65, 'vibe title 5d0e7c1a9b', '$(id)aaaaaaaaaaaa', 7, b'vibe-title-5d0e7c1a9b'):
            with self.subTest(proof=proof):
                self.assertEqual(self.deliver(proof=proof), dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(fake.calls, [])


class OpenCodeTitleCommandTests(unittest.TestCase):
    def run_command(self, payload, path='/nonexistent', args=(), session=None):
        root = Path(__file__).resolve().parents[1]
        env = {k: v for k, v in os.environ.items() if k != 'OPENCODE_SESSION_ID'}
        if session is not None:
            env['OPENCODE_SESSION_ID'] = session
        return subprocess.run([sys.executable, '-I', '-B', str(root / 'scripts' / 'opencode_title.py'), *args],
                              input=payload, capture_output=True, text=True, timeout=30,
                              env=dict(env, PATH=path))

    def test_proof_titles_the_session_named_by_the_environment(self):
        fake = FakeOpenCode(self, messages=[shell_call(f'opencode_title.py --proof {PROOF} < t.json')])
        completed = self.run_command(json.dumps(dict(title=DESIRED, adopt=True)), os.environ['PATH'],
                                     args=('--proof', PROOF), session=SESSION)
        self.assertEqual((completed.returncode, completed.stderr), (0, ''))
        self.assertEqual(json.loads(completed.stdout),
                         dict(ok=True, deferred=False, reason=None, baseline=DESIRED, previous=''))
        self.assertEqual(fake.title, DESIRED)

    def test_proof_without_an_environment_session_does_nothing(self):
        fake = FakeOpenCode(self, messages=[shell_call(f'x --proof {PROOF}')])
        for session in (None, ''):
            with self.subTest(session=session):
                completed = self.run_command(json.dumps(dict(title=DESIRED)), os.environ['PATH'],
                                             args=('--proof', PROOF), session=session)
                self.assertEqual(json.loads(completed.stdout),
                                 dict(ok=False, deferred=False, reason='session_unknown'))
                bad = self.run_command(json.dumps(dict(title=DESIRED)), os.environ['PATH'],
                                       args=('--proof', 'short'), session=session)
                self.assertEqual(json.loads(bad.stdout), dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(fake.calls, [])

    def test_proof_arguments_are_strict(self):
        fake = FakeOpenCode(self, messages=[shell_call(f'x --proof {PROOF}')])
        path = os.environ['PATH']
        cases = ((dict(session_id=SESSION, title=DESIRED), ('--proof', PROOF)),  # one identity source only
                 (dict(title=DESIRED), ('--proof',)),
                 (dict(title=DESIRED), ('--proof', PROOF, 'extra')),
                 (dict(title=DESIRED), ('--other', PROOF)),
                 (dict(title=DESIRED), ()))
        for payload, args in cases:
            with self.subTest(args=args, payload=payload):
                completed = self.run_command(json.dumps(payload), path, args=args, session=SESSION)
                self.assertEqual(json.loads(completed.stdout),
                                 dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(fake.calls, [])

    def test_command_accepts_adopt_with_the_same_validation(self):
        fake = FakeOpenCode(self, title='Generated summary of the chat')
        path = os.environ['PATH']
        for adopt in ('true', 1, None):
            with self.subTest(adopt=adopt):
                completed = self.run_command(json.dumps(dict(session_id=SESSION, title=DESIRED, adopt=adopt)), path)
                self.assertEqual(json.loads(completed.stdout),
                                 dict(ok=False, deferred=False, reason='invalid_request'))
        self.assertEqual(fake.calls, [])
        completed = self.run_command(json.dumps(dict(session_id=SESSION, title=DESIRED, adopt=True)), path)
        self.assertEqual((completed.returncode, completed.stderr), (0, ''))
        self.assertEqual(json.loads(completed.stdout),
                         dict(ok=True, deferred=False, reason=None, baseline=DESIRED,
                              previous='Generated summary of the chat'))
        self.assertEqual(fake.title, DESIRED)

    def test_command_reports_deferral_as_json_and_exits_zero(self):
        for payload in ('not json', json.dumps(dict(session_id=SESSION, title=DESIRED))):
            with self.subTest(payload=payload):
                completed = self.run_command(payload)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stderr, '')
                self.assertEqual(json.loads(completed.stdout)['ok'], False)

    def test_interrupt_still_yields_one_json_line_and_success_exit(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('opencode_title_script', root / 'scripts' / 'opencode_title.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(opencode_titles, 'deliver', side_effect=KeyboardInterrupt), \
                patch('sys.argv', ['opencode_title.py']), \
                patch('sys.stdin', io.StringIO(json.dumps(dict(session_id=SESSION, title=DESIRED)))), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(script.main(), 0)
        self.assertEqual(err.getvalue(), '')
        self.assertEqual(out.getvalue().count('\n'), 1)
        self.assertEqual(json.loads(out.getvalue()), dict(ok=False, deferred=True, reason='interrupted'))

    def test_closed_stdout_prints_nothing_and_exits_zero(self):
        root = Path(__file__).resolve().parents[1]
        read, write = os.pipe()
        os.close(read)
        try:
            completed = subprocess.run([sys.executable, '-I', '-B', str(root / 'scripts' / 'opencode_title.py')],
                                       input=b'{}', stdout=write, stderr=subprocess.PIPE, timeout=30,
                                       env=dict(os.environ, PATH='/nonexistent'))
        finally:
            os.close(write)
        self.assertEqual((completed.returncode, completed.stderr), (0, b''))


if __name__ == '__main__':
    unittest.main()
