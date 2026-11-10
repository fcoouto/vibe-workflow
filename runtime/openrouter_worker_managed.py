"""Smart Routing managed launch of OpenRouter workers (C.3 design 3.1, 3.3 "OpenRouter lock and revision", 4.3, 6.3
C3-4b), split out of scripts/openrouter_workers.py.

In a managed project `prepare` obtains one routing admission on the `gateway` route before the OpenRouter admission
lock, takes that admission at the revision routing saw and writes the run with its attempt id. `execute` launches it
through the OpenCode managed launcher (runtime/opencode_launcher.py) in the run's private workspace copy, with the
read-only profile from `configuration`. The V1 `opencode run --pure` launch refuses a managed project. Write-ahead
entries, dispatch state and recovery: runtime/openrouter_worker_routing.py.

Every function takes `api`, the worker script's live namespace (`Script`), for the V1 helpers it shares and for the
seams tests replace there (`_server`, `_release`, `_settle_run`, `_settle_at`, `progress`).
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import uuid

from runtime import external_delegation, openrouter_settings as settings

HOST = 'openrouter_worker'
ROLE = 'researcher'
SELF_CHECK_BUDGET = 8  # seconds, inside the activation gate's own 10 s budget
SELF_CHECK_UNSAFE = 'self_check_workdir_unsafe'


class RoutingRefused(ValueError):
    """Routing refused this worker with a fixed reason; nothing was sent."""


class Script:
    """A module namespace read on every access, so a name replaced there (a test's patch) is the one used."""

    def __init__(self, namespace):
        self._namespace = namespace

    def __getattr__(self, name):
        try:
            return self._namespace[name]
        except KeyError:
            raise AttributeError(name) from None


def _routed_request(api, brief, checkout, attempt):
    """(assignment, job) of a worker's admission: an explicit `gateway` choice routing admits or refuses, never
    substitutes, and the minimal unplanned job with the read-only tools."""
    from runtime import openrouter_worker_routing as state
    _, launch = api._shared()._routing()
    scope = launch.assignment_scope(checkout)
    choice = launch.choice(HOST, 'api', model=brief['model'], provider=brief['provider'], effort=brief['effort'])
    assignment = dict(attempt_id=attempt, role=ROLE, scope=scope, choice=choice,
                      task='worker:' + hashlib.sha256(brief['task_id'].encode()).hexdigest()[:32])
    return assignment, launch.unplanned_job(ROLE, scope, brief['task_id'], tools=state.READ_ONLY)


def _admit(api, service, checkout, brief, limits, attempt):
    """(bound facts with the reservation, seen revision) for an admitted worker, else (None, reason)."""
    _, launch = api._shared()._routing()
    reason = launch.preflight(launch._call(service, dict(v=1, op='status')), dollar=True)
    if reason is not None:
        return None, reason
    assignment, job = _routed_request(api, brief, checkout, attempt)
    prepared = launch._call(service, launch.prepare_request(assignment, job, constraints=limits))
    if prepared.get('state') != 'admitted':
        reason = prepared.get('reason')
        return None, reason if isinstance(reason, str) and reason else 'routing_unavailable'
    admission = prepared['admission']
    identity = launch.route_identity(HOST, model=brief['model'], provider=brief['provider'], effort=brief['effort'])
    _, reason = launch.admitted_check(admission, host=HOST, catalog=[identity], settings_path=service.settings_path)
    if reason is None and not api._shared()._endpoint(service.settings_path, admission['route'], brief['provider']):
        reason = launch.CATALOG_MISMATCH  # the admitted manifest route must pin this brief's endpoint
    if reason is not None:
        return None, reason
    reserved = [value for key, value in (admission.get('reserved') or {}).items() if key.startswith('org:')]
    return dict(reserved=max(reserved) if reserved else None), (prepared.get('seen') or {}).get('openrouter_revision')


def prepare(api, root, brief, now, deadline, source):
    """Every local refusal first, then a write-ahead attempt id, one routing admission, the OpenRouter admission at the
    revision routing saw (a change in between fails closed) and the run. Any exit before the run closes the hold."""
    from runtime import openrouter_worker_routing as state
    command, _ = api._shared()._routing()
    root = api.private_root(root)
    if (root / brief['task_id']).exists(): raise FileExistsError('task_already_prepared')
    current = settings.status(source)  # a brief shared lock, never the admission lock
    settings.require(current['enabled'], 'openrouter_disabled')
    settings.require('workers' in current['routes'], 'openrouter_route_disabled')
    settings.require(current['local_configuration'] == 'ready', 'openrouter_configuration_unavailable')
    settings.require(current['worker_root'] is not None and Path(current['worker_root']) == root,
                     'openrouter_configuration_mismatch')
    api.scoped_packet(brief, current, now)  # nothing outside the user's scope reaches routing
    limits = external_delegation.constraints(api.worker_permission(brief, current)[1], brief['external'],
                                             service='openrouter', mode='worker')
    checkout, attempt = Path(current['checkout']), 'worker-' + secrets.token_hex(12)
    with state.root_lock(root):
        state.recover(root, api._settle_at)
        pending, entry = state.write_ahead(root, attempt, checkout)
    service, run = None, None
    try:
        service = command.managed_service(checkout)
        bound, seen = _admit(api, service, checkout, brief, limits, attempt)
        if bound is None:
            raise RoutingRefused(seen)
        settings.require(type(seen) is int, 'openrouter_preparation_stale')
        with settings.admission(source, 'workers', root=root, revision=seen) as report:
            selected = api.scoped_packet(brief, report, now)
            brief = dict(api.admitted_brief(brief, report, now), routing=dict(attempt_id=attempt,
                                                                              checkout=str(checkout), **bound))
        run = api._write_run(root, brief, selected, deadline,
                             before_save=lambda folder: state.admitted(folder, attempt, checkout))
        entry.unlink()
        return run
    finally:
        try:
            if run is None and service is not None:
                api._release(service, attempt, entry)
        finally:
            pending.close()
            if service is not None:
                service.close()


def _charge(api, reserved):
    """A `complete`'s OpenCode usage as charged: an estimate of at least the whole reservation (no provider receipt);
    None (no usage: `cost_unknown`) for any unknown cost or an unusable report."""
    def charge(usage):
        try:
            return api._shared()._charged_usage(usage, reserved)
        except ValueError:
            return None
    return charge


def _outcome(result, outcome):
    result['routing'] = {key: outcome[key] for key in ('attempt_id', 'state', 'reason', 'event', 'recovery')
                         if key in outcome}
    if outcome.get('state') == 'uncertain':
        result.update(state='unresolved', failure=dict(category='integration', reason=outcome.get('reason')))
    elif outcome.get('state') != 'recorded':
        result.update(state='failed', failure=dict(category='integration', reason=outcome.get('reason')))
    elif result['response'].strip():
        result['state'] = 'awaiting_verification'
    else:
        result.update(state='unresolved', failure=dict(category='incomplete_output'))


def execute(api, run, brief, executable, auth_data, publish):
    from runtime import opencode_launcher as launcher, openrouter_worker_routing as state
    command, _ = api._shared()._routing()
    bound = brief['routing']
    result = dict(api._failed_result(brief, None), state='running', failure=None, accounting_source='routing',
                  local_execution_stopped=False, native_fallback_ready=False, routing=dict(attempt_id=bound['attempt_id']))
    api.save(run / 'result.json', result)
    service = None
    try:
        with state.root_lock(run.parent):
            state.recover(run.parent, api._settle_at)  # this run's own execution lock is held: it is never touched here
        stamp = api.management_stamp(brief)
        with settings.admission(brief['source_root'], 'workers', root=run.parent, opencode=executable,
                                auth_data=auth_data, revision=stamp['revision'], scope_id=stamp['scope_id']) as report:
            api.launch_checks(brief, report, run / 'workspace')
        service = state.Routed(command.managed_service(bound['checkout']), run, bound['attempt_id'],
                               _charge(api, bound.get('reserved')), api._shared().RECOVERED)
        assignment, job = _routed_request(api, brief, bound['checkout'], bound['attempt_id'])
        api.progress(run, 'Starting')
        with api._server(run, executable, auth_data, service) as host:
            prompt = api.configuration(brief)['agent']['pilot']['prompt'] + '\n\n' + brief['objective']
            outcome = launcher.launch(bound['checkout'], host=state.ReadOnlyHost(host, api.session_rules(brief)),
                                      service=service, assignment=assignment, job=job, prompt=prompt,
                                      idle_timeout=brief['timeout_seconds'])
            if outcome.get('state') == 'recorded' and outcome.get('event') == 'complete' and service.session:
                result.update(session_id=service.session, response=state.response(host, service.session, api.LIMIT))
        _outcome(result, outcome)
    except settings.SettingsError as exc:
        result.update(state='failed', failure=dict(category='integration', reason=str(exc)))
    except launcher.ServerUnavailable:
        result.update(state='failed', failure=dict(category='integration', reason='server_unavailable'))
    except KeyboardInterrupt:
        result.update(state='unresolved', failure=dict(category='interrupted'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        result.update(state='unresolved', failure=result['failure'] or dict(category='runtime_or_output'))
    finally:
        try:
            if service is None:
                service = command.managed_service(bound['checkout'])
            api._settle_run(run, service, bound['attempt_id'])  # every exit closes the hold or leaves it recoverable
        finally:
            service.close()
            if result['state'] in ('failed', 'unresolved'):
                result['response'] = ''  # nothing provider-written survives a refusal or an unconfirmed launch
            result.update(local_execution_stopped=True, native_fallback_ready=result['state'] in ('failed', 'unresolved'))
            api.save(run / 'result.json', result)
            api.progress(run, 'Awaiting verification' if result['state'] == 'awaiting_verification' else result['state'])
            if publish:
                publish(result['state'])
    return result


# -- activation-gate self-check (C.3 design 4.2: "OpenRouter workers") -------------------------------------------
def managed_self_check(api, script, workdir):
    """`[]` when a managed `prepare` obtains one routing admission on a test `gateway` route and the V1 launch is
    refused, else fixed codes. It replaces module globals for its run, so it runs `script` with
    `--managed-self-check` in a child of this interpreter with a fixed environment; every store is in `workdir`."""
    root = api._shared()._self_check_root(workdir)  # the child's own safety checks, before this process creates anything
    if root is None:
        return [SELF_CHECK_UNSAFE]
    for name in ('home', 'tmp'):
        (root / name).mkdir(mode=0o700)
    env = dict(PATH='/usr/bin:/bin', HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'), LANG='C.UTF-8')
    child = subprocess.Popen([sys.executable, '-I', '-B', str(script), '--managed-self-check',
                              str(root / 'check')], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=env, cwd=root, start_new_session=True)
    try:
        output, _ = child.communicate(timeout=SELF_CHECK_BUDGET)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        child.communicate()
        return ['self_check_timeout']
    try:
        codes = json.loads(output.splitlines()[-1])['codes']  # after the refused run's progress line
    except (ValueError, KeyError, TypeError, IndexError):
        return ['self_check_error']
    if type(codes) is not list or not all(type(code) is str for code in codes):
        return ['self_check_error']
    return codes if child.returncode == 0 or codes else ['self_check_error']


def self_check(api, workdir):
    """The flag's body: it never opens a default path; every store lives in `workdir`."""
    root = api._shared()._self_check_root(workdir)
    if root is None:
        return [SELF_CHECK_UNSAFE]
    command, launch = api._shared()._routing()
    from runtime import routing, routing_observations, routing_settings, usage_settings
    routing_path, ledger_path = root / 'routing' / 'settings.json', root / 'ledger' / 'organizations.sqlite3'
    command.default_settings, command.default_ledger = (lambda: routing_path), (lambda: ledger_path)
    usage_settings.directory = lambda: root / 'usage'
    settings.BASIC_STORAGE = root / 'openrouter-consent'
    checkout, workers = root / 'checkout', root / 'workers'
    checkout.mkdir(mode=0o700)
    (checkout / 'input.txt').write_text('self-check\n')
    marker, executable = root / 'v1-launched', root / 'opencode'
    executable.write_text('#!/bin/sh\ntouch %s\n' % marker)
    executable.chmod(0o700)
    report = dict(enabled=True, routes=['workers'], revision=1, local_configuration='ready', checkout=str(checkout),
                  scope_id='self-check', external_permission=None, proposal_store=str(root / 'discovery.json'),
                  worker_root=str(workers), activation=dict(ref='activation:openrouter:self-check:r1',
                                                            funding_ref='self-check', classes=None, destinations=None))

    @contextmanager
    def admission(cwd, route, *, revision=None, **_):
        settings.require(revision is None or revision == report['revision'], 'openrouter_preparation_stale')
        yield dict(report)
    settings.status, settings.admission = (lambda cwd: dict(report)), admission
    routing_settings.managed_project = lambda path, *args, **kwargs: Path(path).resolve() == checkout
    service = command.Service(routing_path, ledger_path)
    codes = []
    try:
        policy = dict(cycle='c', start=0, end=2 * 10**10, expiry=2 * 10**10, stop_at=10**10, ceiling='none',
                      amount=None, grant_amount=0, pilot_limit=None, task_limit=None)
        launch._call(service, dict(v=1, op='set', authority_required=True))
        enabled = launch._call(service, dict(v=1, op='set', organization=dict(id='self-check', enabled=True,
                                                                              policy=policy, expected_revision=0)))
        service._ledger(False, write=True).observe('self-check', 10_000_000, int(time.time()), issued=10_000_000)
        model = dict(model='self-check/model', provider='self-check', effort='low')
        source = routing_observations.StaticSource(
            [dict(route='self-check-gateway', status='qualified', **launch.route_identity(HOST, **model),
                  billing_class='gateway', pool='self-check', account='self-check', demand={'credit': 1000},
                  integration_route='workers', endpoint='self-check')],
            lambda pool, at: [dict(id='credit', limit=10**12, used=0, rolling=False, observed_at=at,
                                   organization='self-check', organization_revision=enabled['organization']['revision'])],
            lambda route, scope: dict(state='ready', scope=scope, ref='self-check'))
        routing_observations.default_source = lambda *args, **kwargs: source
        coordinator = str(uuid.uuid4())
        permission = external_delegation.effective_permission(None, report['activation'], service='openrouter',
                                                              mode='worker', class_name='self-check', **model)
        packet = external_delegation.prepare(checkout, permission, [dict(kind='text', text='self-check',
                                                                         sources=['input.txt'])])
        brief = dict(task_id='self-check', coordinator_chat_id=coordinator, objective='self-check',
                     source_root=str(checkout), files=['input.txt'], timeout_seconds=5, steps=1, **model,
                     external=dict(packet['binding'], automatic=True, local_preferred=False, class_name='self-check',
                                   coordinator=coordinator, reviewer='self-check-reviewer', integration_available=True))
        admissions, real = [], routing.admit

        def admit(*args, **kwargs):
            result = real(*args, **kwargs)
            admissions.append(result)
            return result
        routing.admit = admit
        run = api.prepare(workers, brief)
        if [(item.get('status'), item.get('billing_class')) for item in admissions] != [('admitted', 'gateway')] \
                or 'routing' not in json.loads((run / 'brief.json').read_text()):
            codes.append('worker_admission_failed')
        legacy = {key: value for key, value in brief.items()}  # an unrouted (V1) brief in the managed project
        refused = api._execute(run, legacy, executable, root, None)
        if (refused.get('failure') or {}).get('reason') != routing_settings.LEGACY_ROUTE_RETIRED or marker.exists():
            codes.append('v1_launch_not_refused')
    except Exception:  # noqa: BLE001 - any failure is a fixed code, never a traceback
        codes.append('self_check_error')
    finally:
        service.close()
    return codes
