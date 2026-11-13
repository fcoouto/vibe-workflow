"""Offline revision of the disposable form-gating controller; never a live entry point.

Historical canary definitions remain unchanged for source comparison. Only call,
teardown and finalization are exercised by this revision's offline tests.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.parse
from tests.qualification.form_gate_cleanup import finalize, observe_unload

ROOT = Path(__file__).resolve().parent
CLI = Path.home() / 'Library/Application Support/ai.opencode.desktop/cli/2.2.7-arm64/opencode-cli'
LOCK = threading.Lock()
COUNTS = {'test': 0, 'recovery': 0}
DEADLINE = 0
RECOVERY_DEADLINE = 0
DIRECTORY = None
GENERATION = None


def save(name, data):
    with (ROOT / name).open('x') as out:
        json.dump(data, out, indent=2)


def call(method, path, body=None, expected_status=None, recovery=False, timeout=20):
    phase = 'recovery' if recovery else 'test'
    deadline = RECOVERY_DEADLINE if recovery else DEADLINE
    clock = time.monotonic if recovery else time.time
    with LOCK:
        assert clock() < deadline and COUNTS[phase] < (10 if recovery else 120), 'Allocation exhausted'
        COUNTS[phase] += 1
        number = COUNTS[phase]
    argv = [str(CLI), 'api', method, path]
    if body is not None:
        argv += ['--data', json.dumps(body)]
    started = time.time()
    try:
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError('Allocation expired before process launch')
        result = subprocess.run(argv, capture_output=True, text=True, timeout=min(timeout, remaining))
        raw = {'phase': phase, 'call': number, 'started': started, 'at': time.time(), 'method': method, 'path': path,
               'body': body, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    except (OSError, subprocess.TimeoutExpired, TimeoutError) as error:
        with LOCK:
            with (ROOT / 'api.jsonl').open('a') as out:
                out.write(json.dumps({'phase': phase, 'call': number, 'started': started, 'at': time.time(),
                    'method': method, 'path': path, 'body': body, 'error': repr(error)}) + '\n')
        raise
    with LOCK:
        with (ROOT / 'api.jsonl').open('a') as out:
            out.write(json.dumps(raw) + '\n')
    assert clock() < deadline, 'Response arrived after allocation expired'
    if expected_status is not None:
        assert result.returncode and f'HTTP {expected_status} ' in result.stderr, raw
        return raw
    if result.returncode:
        raise RuntimeError(json.dumps(raw))
    return json.loads(result.stdout) if result.stdout.strip() else None


def query():
    return urllib.parse.urlencode({'location[directory]': str(DIRECTORY)})


def rawrpc(method, data):
    return call('post', '/api/rpc/question-box-gating/' + method + '?' + query(), {'input': data})['output']


def rpc(method, data):
    value = rawrpc(method, dict(data, generation=GENERATION))
    assert value['generation'] == GENERATION, 'Never adopt a replacement generation'
    return value


def formpath(session, form=None):
    return '/api/session/' + session + '/form' + (('/' + form) if form else '')


def records():
    path = ROOT / 'events.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def teardown(sessions, before):
    global RECOVERY_DEADLINE
    assert RECOVERY_DEADLINE == 0, 'Do not replay teardown or reset recovery limits'
    RECOVERY_DEADLINE = time.monotonic() + min(30, max(0, DEADLINE - time.time()))
    results = {'recovery_only': True, 'errors': [], 'passed': False, 'configuration_removed': None,
               'disposal_completed': None}
    try:
        for index, session in enumerate(sessions):
            try:
                assert COUNTS['recovery'] < 7, 'No form-list budget remains'
                for form in call('get', formpath(session), recovery=True, timeout=2)['data']:
                    # Preserve three operations for inventory, RPC and process verification.
                    assert COUNTS['recovery'] < 7 - (len(sessions) - index - 1), 'No cancellation budget remains'
                    call('delete', formpath(session, form['id']), recovery=True, timeout=2)
            except BaseException as error:
                results['errors'].append(repr(error))
        assert time.monotonic() < RECOVERY_DEADLINE, 'Recovery deadline expired before configuration removal'
        original = (DIRECTORY / 'opencode.json').read_bytes()
        with (ROOT / 'fixture-config-before-teardown.json').open('xb') as out:
            out.write(original)
        config = json.loads(original)
        assert config['plugins'] == [{'package': str(ROOT / 'plugin'), 'options': {
            'log': str(ROOT / 'events.jsonl'), 'deadline': int(DEADLINE * 1000), 'cli': str(CLI), 'serverPID': before['pid']}}]
        config['plugins'] = []
        (DIRECTORY / 'opencode.json').write_text(json.dumps(config, indent=2))
        results.update(configuration_removed=True, original_config_sha256=hashlib.sha256(original).hexdigest(),
                       teardown_config_sha256=hashlib.sha256((DIRECTORY / 'opencode.json').read_bytes()).hexdigest())
        # Observe asynchronous source removal, not the non-durable custom close.
        observed = observe_unload(lambda *args, **kwargs: call(*args, recovery=True, **kwargs),
            lambda: 10 - COUNTS['recovery'], RECOVERY_DEADLINE, query(), before)
        results['errors'].extend(observed.pop('errors'))
        results.update(observed)
        results['disposal_completed'] = any(item['kind'] == 'closed' and item['generation'] == GENERATION for item in records())
        results['passed'] = results['passed'] and not results['errors'] and results['disposal_completed']
    except BaseException as error:
        results['passed'] = False
        results['errors'].append(repr(error))
    finally:
        results['recovery_api_calls'] = COUNTS['recovery']
        results['within_recovery_deadline'] = time.monotonic() < RECOVERY_DEADLINE
        results['passed'] = results['passed'] and results['within_recovery_deadline']
        save('teardown.json', results)
    return results


def main():
    global DEADLINE, DIRECTORY, GENERATION
    assert not (ROOT / 'live-start.json').exists(), 'Do not reset or replay this attempt'
    checks = []
    for name in ['binding.test.mjs', 'form-access.test.mjs']:
        result = subprocess.run(['node', str(ROOT / name)], capture_output=True, text=True)
        checks.append({'test': name, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        assert result.returncode == 0, checks
    save('self-checks.json', checks)
    comm = subprocess.check_output(['ps', '-p', '61057', '-o', 'comm='], text=True).strip()
    assert Path(comm).name == 'opencode-cli'
    assert hashlib.sha256(CLI.read_bytes()).hexdigest() == '2160e46e87e83561195271e69d0385440bbc91bc4b9c8dc38e0a3822190614a1'
    started = time.time()
    DEADLINE = started + 300
    save('live-start.json', {'authorization': 'User explicitly directed form-access bridge qualification and fresh bounded gating test',
         'predecessor': 'question-box-approval-20261113; remains closed and unchanged',
         'started': started, 'deadline': DEADLINE, 'controller_call_cap': 120, 'bridge_call_cap': 60,
         'recovery_call_cap': 10, 'recovery_seconds': 30, 'owned_session_cap': 2, 'form_cap': 8,
         'model_request_cap': 0, 'old_allowance_reset': False})
    before = call('get', '/api/info')
    assert before['pid'] == 61057
    base = Path('/private/var/folders/tz/c2kgm3r911ndfsbh8zm53lvc0000gn/T/opencode')
    # The controller owns this fixed successor directory; no old fixture is reused.
    DIRECTORY = base / ('question-box-gating-' + str(int(started * 1000)))
    DIRECTORY.mkdir(mode=0o700)
    (DIRECTORY / 'canary.py').write_text('VALUE = 0\n')
    config = {'$schema': 'https://opencode.ai/config.json', 'permissions': [
        {'action': '*', 'resource': '*', 'effect': 'deny'},
        {'action': 'edit', 'resource': str(DIRECTORY / 'canary.py'), 'effect': 'allow'}],
        'plugins': [{'package': str(ROOT / 'plugin'), 'options': {'log': str(ROOT / 'events.jsonl'),
            'deadline': int(DEADLINE * 1000), 'cli': str(CLI), 'serverPID': before['pid']}}]}
    (DIRECTORY / 'opencode.json').write_text(json.dumps(config, indent=2))
    pins = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
            [*sorted((ROOT / 'plugin').glob('*')), ROOT / 'run.py', ROOT / 'binding.test.mjs', ROOT / 'form-access.test.mjs', DIRECTORY / 'opencode.json']}
    save('fixture.json', {'directory': str(DIRECTORY), 'server_before': before, 'source_pins': pins})
    sessions, observations = [], []
    failure = None
    result = None
    try:
        call('get', '/api/plugin?' + query())
        readiness_deadline = min(DEADLINE, time.time() + 5)
        for _ in range(10):
            try:
                hello = rawrpc('hello', {})
                assert hello['active'] and hello['nativeFormBridge'] and hello['permissionAssert'], hello
                GENERATION = hello['generation']
                break
            except RuntimeError as error:
                raw = json.loads(str(error))
                assert 'rpc.unavailable' in raw['stdout'] and time.time() < readiness_deadline, raw
        assert GENERATION is not None, 'Exact plugin readiness timed out'
        for title in ['Form-gated canary owner', 'Form-gated canary foreign session']:
            sessions.append(call('post', '/api/session', {'title': title, 'location': {'directory': str(DIRECTORY)}})['data']['id'])
        save('sessions.json', sessions)
        owner, foreign = sessions
        preflight = rpc('preflight', {'sessionID': owner})
        assert preflight['qualified']
        assert call('get', formpath(owner, preflight['formID']))['data']['state']['status'] == 'cancelled'
        save('preflight.json', preflight)

        def openform(value):
            form = rpc('open', {'sessionID': owner, 'value': value})['form']
            assert form['sessionID'] == owner
            return form['id']

        def attempt(label, session, form, value, allowed=False, current=0):
            answer = rpc('attempt', {'sessionID': session, 'formID': form, 'value': value, 'attempt': label})
            assert answer['allowed'] is allowed, (label, answer)
            assert (DIRECTORY / 'canary.py').read_text() == f'VALUE = {current}\n', label
            observations.append({'case': label, 'result': answer, 'canary': current})

        def reply(form, decision):
            call('post', formpath(owner, form) + '/reply', {'answer': {'decision': decision}})

        rejected = openform(1)
        attempt('pending-denies', owner, rejected, 1)
        reply(rejected, 'reject')
        attempt('reject-denies', owner, rejected, 1)
        cancelled = openform(1)
        call('delete', formpath(owner, cancelled))
        attempt('cancel-denies', owner, cancelled, 1)
        approved = openform(1)
        attempt('new-form-pending-denies', owner, approved, 1)
        attempt('foreign-session-denies', foreign, approved, 1)
        reply(approved, 'approve')
        detail = call('get', formpath(owner, approved))['data']
        assert detail['id'] == approved and detail['sessionID'] == owner and detail['state'] == {'status': 'answered', 'answer': {'decision': 'approve'}}
        attempt('tampered-action-denies', owner, approved, 2)
        stale_generation = rawrpc('attempt', {'generation': 'not-the-qualified-generation', 'sessionID': owner,
            'formID': approved, 'value': 1, 'attempt': 'wrong-generation'})
        assert stale_generation['generation'] == GENERATION and not stale_generation['allowed']
        assert (DIRECTORY / 'canary.py').read_text() == 'VALUE = 0\n'
        observations.append({'case': 'wrong-generation-denies', 'result': stale_generation, 'canary': 0})
        attempt('exact-approval-allows-one-edit', owner, approved, 1, True, 1)
        attempt('consumed-answer-denies', owner, approved, 1, current=1)
        nextform = openform(2)
        stale = openform(3)
        attempt('old-form-cannot-approve-new-action', owner, approved, 2, current=1)
        attempt('next-form-pending-denies', owner, nextform, 2, current=1)
        reply(nextform, 'approve')
        inputs = [{'sessionID': owner, 'formID': nextform, 'value': 2, 'attempt': 'concurrent-' + str(n)} for n in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent = list(pool.map(lambda data: rpc('attempt', data), inputs))
        assert sorted(item['allowed'] for item in concurrent) == [False, True], concurrent
        assert (DIRECTORY / 'canary.py').read_text() == 'VALUE = 2\n'
        observations.append({'case': 'concurrent-consumption-once', 'results': concurrent, 'canary': 2})
        reply(stale, 'approve')
        attempt('changed-before-image-denies', owner, stale, 3, current=2)
        race = openform(3)

        def competing(decision):
            try:
                reply(race, decision)
                return {'decision': decision, 'accepted': True}
            except RuntimeError as error:
                raw = json.loads(str(error))
                assert 'HTTP 409 ' in raw['stderr'], raw
                return {'decision': decision, 'accepted': False, 'http_status': 409}
        with ThreadPoolExecutor(max_workers=2) as pool:
            races = list(pool.map(competing, ['approve', 'reject']))
        race_detail = call('get', formpath(owner, race))['data']
        save('conflicting-replies.json', {'responses': races, 'state': race_detail, 'edit_attempted': False})
        assert (DIRECTORY / 'canary.py').read_text() == 'VALUE = 2\n'
        snapshot = rpc('snapshot', {'formID': approved})
        assert snapshot['bridgeQualified'] and snapshot['rejectedModelRequests'] == 0 and snapshot['bridgeCalls'] <= 60
        assert snapshot['event']['data'] == {'id': approved, 'sessionID': owner, 'answer': {'decision': 'approve'}}
        assert not any(r['kind'] == 'native_event' and r['data']['type'].startswith('permission.') for r in records())
        assert not any(r['kind'] in ['bridge_error', 'lookup_failed_closed', 'event_stream_failed', 'model_request_rejected'] for r in records()), 'Unexpected errors cannot count as successful denials'
        assert len([r for r in records() if r['kind'] == 'canary_written']) == 2
        assert {r['generation'] for r in records()} == {GENERATION}, 'Replacement generation invalidates cases'
        assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in pins.items())
        active = call('get', '/api/session/active')['data']
        assert not set(sessions).intersection(active)
        save('cases.json', observations)
        save('snapshot.json', snapshot)
        result = {'verdict': 'passed', 'scope': 'generation-bound native form bridge, permission assertion/hook and RPC canary writer',
            'generation': GENERATION, 'case_count': len(observations), 'controller_api_calls': COUNTS['test'],
            'bridge_api_calls': snapshot['bridgeCalls'], 'forms': snapshot['forms'], 'owned_sessions': 2,
            'native_form_event_correlated': True, 'canary_writes': 2, 'canary_final': 2,
            'conflicting_replies_accepted': sum(item['accepted'] for item in races), 'conflicting_reply_trials': 1,
            'native_single_reply_settlement_observed': sum(item['accepted'] for item in races) == 1,
            'ambiguous_form_used_for_edit': False, 'model_requests_initiated': 0, 'model_requests_rejected': 0,
            'manual_ui_tested': False, 'built_in_edit_tool_dispatch_tested': False, 'shipped_gate_changed': False,
            'human_origin_authenticated': False, 'restart_persistence_tested': False, 'old_attempt_replayed': False}
    except BaseException as error:
        failure = repr(error)
        save('failure.json', {'error': failure, 'sessions': sessions, 'observations': observations, 'counts': COUNTS.copy()})
    finally:
        report = finalize(result, failure, lambda: teardown(sessions, before), save)
        print(json.dumps(report, indent=2))
    if report['verdict'] != 'passed':
        raise RuntimeError(json.dumps({'cases': report['cases']['error'], 'recovery': report['recovery']}))


if __name__ == '__main__':
    raise SystemExit('Offline revision only: no live allocation is authorized. Do not replay original cases.')
