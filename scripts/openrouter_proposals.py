#!/usr/bin/env python3
"""Private advisory discovery/history. No network, credentials, tools or authority.

Tokens prevent accidental replay in this store; coordinator-supplied references
are reports, not authenticated native verification or spending authorization.
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import time

PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path: sys.path.insert(0, str(PACKAGE))
from runtime import openrouter_settings as settings, worker_visibility, external_delegation

MAX_INPUT = 1024 * 1024
MAX_STATE = 8 * 1024 * 1024
MAX_RESPONSE = 1024 * 1024
MAX_ATTEMPTS = 1000
TTL = 86400
LEASE = 120
BACKOFF = 3600
MAX_OUTPUT_TOKENS = 8192
MAX_TIMEOUT_SECONDS = 120


def encoded(value, limit):
    data = bytearray()
    try:
        for chunk in json.JSONEncoder(allow_nan=False, sort_keys=True, separators=(',', ':')).iterencode(value):
            chunk = chunk.encode('ascii')
            if len(data) + len(chunk) > limit:
                break
            data.extend(chunk)
        else:
            return bytes(data)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('finite JSON data required') from exc
    raise ValueError('JSON size limit exceeded')


def text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError(name + ' must be bounded nonempty text')
    return value


def number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(name + ' must be a finite nonnegative number')
    return value


def profile(value):
    if not isinstance(value, dict) or not all(key in value for key in ('family', 'language', 'framework', 'difficulty', 'scope', 'verification')):
        raise ValueError('complete task profile required')
    for key in ('family', 'difficulty', 'scope', 'verification'):
        text(value[key], key)
    return value


def private_file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise ValueError('state and lock must be private regular files owned by this user')


def store_path(store):
    store = Path(store)
    if not store.is_absolute() or store.name in ('', '.', '..'):
        raise ValueError('absolute private store path required')
    parent = store.parent.resolve(strict=True)
    for directory in (parent, *parent.parents):
        if (directory / '.git').exists() or (directory / '.claude-plugin').exists() or (directory / '.codex-plugin').exists():
            raise ValueError('store must be outside repositories and plugin installations')
    info = parent.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('store parent must be a private directory owned by this user')
    return parent / store.name


def status(state, now):
    snapshot = state.get('snapshot')
    fresh = bool(snapshot and now < snapshot['expires_at'] and not state.get('failure'))
    claim = state.get('claim')
    return dict(advisory=True, fresh=fresh,
                snapshot_digest=snapshot['digest'] if snapshot else None,
                fetched_at=snapshot['fetched_at'] if snapshot else None,
                expires_at=snapshot['expires_at'] if snapshot else None,
                refresh_eligible=not fresh and now >= state.get('retry_after', 0) and not (claim and now < claim['expires_at']),
                retry_after=state.get('retry_after'), failure=state.get('failure'))


def matching(records, query):
    return [row for row in records if all(row.get(key) == query[key] for key in ('profile', 'model', 'provider', 'effort') if key in query)]


def requirements_for(request):
    requirements = request.get('requirements')
    if not isinstance(requirements, dict):
        raise ValueError('requirements must be an object')
    for key in ('context_tokens', 'output_tokens'):
        if type(requirements.get(key)) is not int or requirements[key] < 0:
            raise ValueError(key + ' must be a nonnegative integer')
    for key in ('modalities', 'parameters'):
        if not isinstance(requirements.get(key), list) or not all(isinstance(x, str) for x in requirements[key]):
            raise ValueError(key + ' must be a list of strings')
    if request.get('effort') is not None:
        text(request['effort'], 'effort')
    return requirements


def incompatibilities(model, endpoint, requirements, effort):
    reasons = []
    for field, required, reason, optional in (
        ('context_length', requirements['context_tokens'] + requirements['output_tokens'], 'context_tokens', False),
        ('max_prompt_tokens', requirements['context_tokens'], 'max_prompt_tokens', True),
        ('max_output_tokens', requirements['output_tokens'], 'output_tokens', False),
    ):
        limits = [item.get(field) for item in (model or {}, endpoint)]
        limits = [x for x in limits if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0]
        if limits and required > min(limits) or not limits and not optional:
            reasons.append(reason)
    modalities = endpoint.get('modalities', (model or {}).get('modalities'))
    modalities = modalities if isinstance(modalities, list) else []
    reasons.extend('modality:' + item for item in requirements['modalities'] if item not in modalities)
    parameters = endpoint.get('supported_parameters')
    parameters = parameters if isinstance(parameters, list) else []
    requested = set(requirements['parameters'])
    if effort is not None:
        if not any(control in parameters for control in ('reasoning', 'reasoning_effort')):
            reasons.append('parameter:reasoning')
        reasoning = (model or {}).get('reasoning')
        efforts = reasoning.get('supported_efforts') if isinstance(reasoning, dict) else None
        if efforts is None:
            efforts = (model or {}).get('supported_reasoning_efforts')
        endpoint_efforts = endpoint.get('supported_reasoning_efforts')
        if not isinstance(efforts, list) or effort not in efforts or endpoint_efforts is not None and (not isinstance(endpoint_efforts, list) or effort not in endpoint_efforts):
            reasons.append('effort:' + effort)
    reasons.extend('parameter:' + item for item in sorted(requested) if item not in parameters)
    if model is None:
        reasons.append('missing_model')
    return reasons


def triage(state, request, now):
    if not status(state, now)['fresh']:
        return dict(fallback='stale_discovery', candidates=[], incompatible=[])
    profile(request.get('profile'))
    requirements = requirements_for(request)
    metadata = state['snapshot']['metadata']
    candidates, incompatible = [], []
    result = dict(candidates=candidates, incompatible=incompatible, snapshot_digest=state['snapshot']['digest'], advisory=True)
    remaining = MAX_RESPONSE - len(encoded(result, MAX_RESPONSE)) - 1
    for endpoint in metadata['endpoints']:
        model = next((m for m in metadata['models'] if m['id'] == endpoint['model']), None)
        reasons = incompatibilities(model, endpoint, requirements, request.get('effort'))
        if reasons:
            target = incompatible
            item = dict(model=endpoint['model'], provider=endpoint['provider'], reasons=reasons)
        else:
            target = candidates
            related = [row for row in state['attempts'] if row['model'] == endpoint['model'] and row['provider'] == endpoint['provider'] and all(row['profile'][key] == request['profile'][key] for key in ('family', 'language'))][-100:]
            item = dict(model=endpoint['model'], provider=endpoint['provider'], endpoint=endpoint, model_metadata=model,
                        benchmarks=[b for b in metadata['benchmarks'] if b.get('model') == endpoint['model']],
                        history=related, history_differences=[])
        try:
            size = len(encoded(item, remaining - bool(target))) + bool(target)
            if not reasons:
                differences = item['history_differences']
                for row in related:
                    difference = dict(task_id=row['task_id'], fields=[key for key in request['profile'] if row['profile'].get(key) != request['profile'][key]])
                    size += len(encoded(difference, remaining - size - bool(differences))) + bool(differences)
                    differences.append(difference)
        except ValueError:
            return dict(fallback='response_capacity', candidates=[], incompatible=[])
        remaining -= size
        target.append(item)
    return result


def act(action, state, request, now, *, activation_ref=None, authorize=None, settle=None, own=None):
    """`authorize(row)` and `settle(row, request)` run under the store lock just before a dispatch or an outcome is
    stored; a returned reason refuses it and stores nothing (the routing hold's dispatch and closing event). A
    write-ahead routing entry (`routing_pending`) is a pending attempt for every `begin` but its `own`."""
    current = status(state, now)
    if action == 'status':
        if request.get('include_metadata') is True:
            current['metadata'] = state.get('snapshot', {}).get('metadata')
        return current
    if action == 'claim':
        force = request.get('force', False)
        if not isinstance(force, bool):
            raise ValueError('force must be boolean')
        claim = state.get('claim')
        if claim and now < claim['expires_at'] or not force and not current['refresh_eligible']:
            return current
        token = secrets.token_urlsafe(24)
        state['claim'] = dict(token=token, expires_at=now + LEASE)
        return dict(status(state, now), refresh_token=token)
    if action == 'publish':
        claim = state.get('claim')
        if not claim or request.get('refresh_token') != claim['token'] or now >= claim['expires_at']:
            raise ValueError('expired or superseded refresh token')
        if ('metadata' in request) == ('failure' in request):
            raise ValueError('publish requires either metadata or failure')
        if 'failure' in request:
            state['failure'] = text(request['failure'], 'failure')
            state['retry_after'] = now + BACKOFF
        else:
            metadata = request['metadata']
            if not isinstance(metadata, dict) or not isinstance(metadata.get('coverage'), dict) or not metadata['coverage']:
                raise ValueError('discovery coverage required')
            for key in ('models', 'endpoints', 'benchmarks', 'task_usage'):
                if not isinstance(metadata.get(key), list) or not all(isinstance(item, dict) for item in metadata[key]):
                    raise ValueError(key + ' must be a list of objects')
            for model in metadata['models']:
                text(model.get('id'), 'model id')
            for endpoint in metadata['endpoints']:
                text(endpoint.get('model'), 'endpoint model')
                text(endpoint.get('provider'), 'endpoint provider')
            digest = hashlib.sha256(encoded(metadata, MAX_INPUT)).hexdigest()
            state['snapshot'] = dict(metadata=metadata, fetched_at=now, expires_at=now + TTL, digest=digest)
            state.pop('failure', None)
            state.pop('retry_after', None)
        state.pop('claim', None)
        return status(state, now)
    if action == 'triage':
        return triage(state, request, now)
    if action == 'history':
        limit = request.get('limit', 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('history limit must be 1..100')
        return dict(records=matching(state['attempts'], request)[-limit:])
    if action == 'begin':
        task_id = text(request.get('task_id'), 'task_id')
        if any(row['task_id'] == task_id for row in state['attempts']):
            return dict(fallback='task_already_attempted')
        if not current['fresh'] or request.get('snapshot_digest') != current['snapshot_digest']:
            return dict(fallback='stale_discovery')
        if any('outcome' not in row for row in state['attempts']) or any(
                entry['attempt_id'] != own for entry in state.get('routing_pending', [])):
            return dict(fallback='pending_attempt')
        if len(state['attempts']) >= MAX_ATTEMPTS:
            return dict(fallback='capacity')
        visibility = worker_visibility.optional_declaration(request.get('visibility'))
        profile(request.get('profile'))
        for key in ('model', 'provider', 'base_ref'):
            text(request.get(key), key)
        if 'approval_ref' in request:
            text(request['approval_ref'], 'approval_ref')
        if request.get('effort') is not None:
            text(request['effort'], 'effort')
        for key, ceiling in (('output_tokens', MAX_OUTPUT_TOKENS), ('timeout_seconds', MAX_TIMEOUT_SECONDS)):
            if type(request.get(key)) is not int or not 1 <= request[key] <= ceiling:
                raise ValueError(key + ' outside execution bounds')
        requirements = requirements_for(request)
        if requirements['output_tokens'] != request['output_tokens']:
            raise ValueError('requirements output_tokens must equal dispatch output_tokens')
        endpoint = next((ep for ep in state['snapshot']['metadata']['endpoints'] if ep['model'] == request['model'] and ep['provider'] == request['provider']), None)
        if endpoint is None:
            return dict(fallback='unknown_route')
        metadata = state['snapshot']['metadata']
        model = next((m for m in metadata['models'] if m['id'] == request['model']), None)
        reasons = incompatibilities(model, endpoint, requirements, request.get('effort'))
        if reasons:
            return dict(fallback='incompatible_route', reasons=reasons)
        token = secrets.token_urlsafe(24)
        row = {key: request.get(key) for key in ('task_id', 'profile', 'model', 'provider', 'effort', 'snapshot_digest', 'approval_ref', 'base_ref', 'output_tokens', 'timeout_seconds', 'requirements')}
        row['approval_ref'] = request.get('approval_ref', activation_ref)
        row['discovery_evidence'] = dict(model=model, endpoint=endpoint, benchmarks=[b for b in metadata['benchmarks'] if b.get('model') == request['model']])
        if visibility is not None:
            row['visibility'] = visibility
        row['external_digest'] = external_delegation.digest({key: value for key, value in request.items()
            if key not in ('attempt_token', 'visibility')})
        row['reviewer'] = request['external']['reviewer']
        row.update(attempt_token=token, begun_at=now, snapshot_fetched_at=current['fetched_at'])
        state['attempts'].append(row)
        return dict(attempt_token=token, advisory=True)
    if action == 'dispatch':
        row = next((r for r in state['attempts'] if r['attempt_token'] == request.get('attempt_token')), None)
        if row is None or 'external_digest' not in row:
            raise ValueError('external_attempt_required')
        if 'dispatched_at' in row or 'outcome' in row:
            return dict(fallback='already_dispatched')
        if not current['fresh'] or request.get('snapshot_digest') != current['snapshot_digest']:
            return dict(fallback='stale_discovery')
        expected = external_delegation.digest({key: value for key, value in request.items()
            if key not in ('attempt_token', 'visibility')})
        if row['external_digest'] != expected:
            raise ValueError('external_attempt_changed')
        row['dispatched_at'] = now
        try:  # capacity first: a stored dispatch must fit before any routing authority is taken
            encoded(state, MAX_STATE)
        except ValueError:
            del row['dispatched_at']
            return dict(fallback='capacity')
        refused = authorize(row) if authorize is not None else None
        if refused is not None:
            del row['dispatched_at']
            return dict(fallback=refused)
        return dict(dispatched=True, attempt_token=row['attempt_token'])
    if action == 'record':
        row = next((r for r in state['attempts'] if r['attempt_token'] == request.get('attempt_token')), None)
        if row is None:
            raise ValueError('unknown attempt token')
        if 'outcome' in row:
            return dict(fallback='already_recorded')
        disposition = request.get('disposition')
        if disposition not in ('accepted', 'repaired', 'rejected', 'transport_failure', 'unresolved'):
            raise ValueError('unknown disposition')
        if disposition in ('accepted', 'repaired'):
            # Historical unbound attempts may settle; every new admission records this binding.
            if 'external_digest' in row and request.get('reviewer') != row['reviewer']:
                raise ValueError('external_independent_review_required')
            text(request.get('native_review_ref'), 'native_review_ref')
            checks = request.get('native_checks')
            if not isinstance(checks, list) or not checks or not all(isinstance(c, dict) and c.get('success') is True and isinstance(c.get('ref'), str) and c['ref'].strip() for c in checks):
                raise ValueError('successful native check references required')
        refused = settle(row, request) if settle is not None and 'routing' in row else None
        if refused is not None:
            return dict(fallback=refused)
        row['outcome'] = {key: request[key] for key in ('disposition', 'native_review_ref', 'native_checks', 'observations') if key in request}
        row['outcome']['recorded_at'] = now
        return dict(recorded=True, advisory=True)
    raise ValueError('unknown action')


def _external(report, request, now):
    """(permission, packet) for this request under an OpenRouter settings report: binding, independent review,
    permission, destination, source and packet checks (shared external delegation)."""
    external_delegation.require('external' in request, 'binding_required')
    binding = request['external']
    external_delegation.native_review(binding)
    assignment = dict(service='openrouter', mode='proposal', model=request['model'],
        provider=request['provider'], effort=request.get('effort'), class_name=binding['class_name'])
    permission = external_delegation.effective_permission(report['external_permission'],
        report['activation'], **assignment)
    packet = external_delegation.check(Path(report['checkout']), permission, binding,
        **assignment, now=time.time() if now is None else now)
    return permission, packet


def run(action: str, store: Path, request: dict, *, now: float | None = None, cwd=None) -> dict:
    if action in ('begin', 'dispatch'):
        try:
            managed = cwd is not None and _managed(cwd)
            if action == 'begin' and managed:
                return _begin_managed(store, request, now, cwd)
            granted = []  # routed holds dispatched in this call; a dispatch that is not stored keeps their exposure
            try:
                with settings.admission(cwd, 'proposals', store=store) as report:
                    _, packet = _external(report, request, now)
                    result = _run(action, store, request, now=now,
                        management=dict(scope_id=report['scope_id'], revision=report['revision']),
                        activation_ref=report['activation']['ref'],
                        authorize=lambda row: _authorize(row, managed, granted))
            except BaseException:
                _unsent(granted)
                raise
            if not result.get('dispatched'):
                _unsent(granted)
            elif action == 'dispatch':
                result.update(packet=packet['text'], model=request['model'], provider=request['provider'],
                              effort=request.get('effort'), requires_native_review=True, accepted=False)
            return result
        except settings.SettingsError as exc:
            return dict(fallback=str(exc), advisory=True)
    return _run(action, store, request, now=now, settle=_settle if action == 'record' else None)


# -- Smart Routing managed launch (C.3 design 3.1, 3.3 "OpenRouter lock and revision", 6.3 C3-4a) ------------------
# In a managed project `begin` is one routing admission on the `gateway` route, prepared before the OpenRouter
# admission lock; `dispatch` takes the routing authority and checks it in process before the packet is returned (the
# caller sends only after that); `record` closes the hold. Unmanaged projects keep the path above unchanged.
#
# Holds are never stranded. `begin` writes its routing attempt id to the store (`routing_pending`) BEFORE `prepare`,
# so a crash between `prepare` and the stored attempt leaves a record: it blocks new attempts like any pending one
# for LEASE seconds, then the next `begin` closes its hold (`not_dispatched`) and drops it. A failure after the
# routing `dispatch` (the ledger never accepts `not_dispatched` once dispatched) completes the hold with no usage, so
# its whole reservation stays held as `cost_unknown` for the operator; the packet was never returned.
HOST = 'openrouter_proposal'
RECOVERED = ('unknown_attempt', 'attempt_closed')  # no admitted hold is left behind for this id


def _managed(cwd):
    """Phase W: whether `cwd` lies in a managed project (an unreadable routing setting counts as managed)."""
    from runtime import routing_settings
    return routing_settings.managed_project(Path(cwd)) is not False


def _routing():
    from scripts import routing as command
    from runtime import routing_launch
    return command, routing_launch


def _close(service, attempt):
    """Close an admitted hold that will never be sent (`not_dispatched`); True when none is left open."""
    _, launch = _routing()
    answer = launch._record(service, attempt, 'not_dispatched')
    return answer.get('state') == 'recorded' or answer.get('reason') in RECOVERED


def _forget(state, attempt):
    kept = [entry for entry in state.get('routing_pending', []) if entry['attempt_id'] != attempt]
    if kept:
        state['routing_pending'] = kept
    else:
        state.pop('routing_pending', None)


def _release(service, store, attempt, now):
    """A begin that will not store its attempt: close the hold, then drop the write-ahead entry."""
    if _close(service, attempt):
        _run('begin', store, {}, now=now, hook=lambda state, moment: _forget(state, attempt) or {})


def _recover(state, moment):
    """Under the store lock: close and drop write-ahead entries older than LEASE (their begin died)."""
    command, _ = _routing()
    for entry in [entry for entry in state.get('routing_pending', []) if moment >= entry['at'] + LEASE]:
        service = command.managed_service(entry['checkout'])
        try:
            if _close(service, entry['attempt_id']):
                _forget(state, entry['attempt_id'])
        finally:
            service.close()


def _unsent(granted):
    """Routed holds dispatched for a packet that was never returned: completed with no usage (`cost_unknown`)."""
    command, launch = _routing()
    for checkout, attempt in granted:
        service = command.managed_service(checkout)
        try:
            launch._record(service, attempt, 'complete')
        finally:
            service.close()
    granted.clear()


def _endpoint(settings_path, route, provider):
    """The admitted route pins this request's endpoint: its manifest entry names it (the OpenRouter catalog check)."""
    from runtime import routing_observations
    try:
        specs = routing_observations.default_source(settings_path).manifest()
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return any(type(spec) is dict and spec.get('route') == route and spec.get('endpoint') == provider
               for spec in specs)


def _prepare(service, checkout, request, limits, attempt):
    """(dispatch facts with the reservation, seen revision) for an admitted proposal, else (None, reason). Routing
    decides from the collector's observations; the coordinator's model is an explicit choice it admits or refuses,
    never substitutes."""
    _, launch = _routing()
    reason = launch.preflight(launch._call(service, dict(v=1, op='status')), dollar=True)
    if reason is not None:
        return None, reason
    fields = dict(model=request['model'], provider=request['provider'], effort=request.get('effort'))
    scope = launch.assignment_scope(checkout)
    assignment = dict(attempt_id=attempt, role='researcher', scope=scope,
                      task='proposal:' + hashlib.sha256(request['task_id'].encode()).hexdigest()[:32],
                      choice=launch.choice(HOST, 'api', **fields))
    job = launch.unplanned_job('researcher', scope, request['task_id'])
    prepared = launch._call(service, launch.prepare_request(assignment, job, constraints=limits))
    if prepared.get('state') != 'admitted':
        reason = prepared.get('reason')
        return None, reason if isinstance(reason, str) and reason else 'routing_unavailable'
    admission = prepared['admission']
    facts, reason = launch.admitted_check(admission, host=HOST, catalog=[launch.route_identity(HOST, **fields)],
                                          settings_path=service.settings_path)
    if reason is None and not _endpoint(service.settings_path, admission['route'], request['provider']):
        reason = launch.CATALOG_MISMATCH
    if reason is not None:
        return None, reason
    reserved = [value for key, value in (admission.get('reserved') or {}).items() if key.startswith('org:')]
    return dict(facts=facts, reserved=max(reserved) if reserved else None), \
        (prepared.get('seen') or {}).get('openrouter_revision')


def _begin_managed(store, request, now, cwd):
    command, _ = _routing()
    for key in ('model', 'provider', 'task_id'):
        text(request.get(key), key)
    current = settings.status(cwd)  # a brief shared lock, never the admission lock
    settings.require(current['enabled'], 'openrouter_disabled')
    settings.require('proposals' in current['routes'], 'openrouter_route_disabled')
    permission, _ = _external(current, request, now)  # nothing outside the user's scope reaches routing
    limits = external_delegation.constraints(permission, request['external'], service='openrouter', mode='proposal')
    checkout = Path(current['checkout'])
    attempt = 'proposal-' + secrets.token_hex(12)

    def reserve(state, moment):
        """Recover dead write-ahead entries, run every local `begin` refusal on a copy (nothing reaches routing
        unless the attempt could be stored), then record this attempt id before `prepare`."""
        _recover(state, moment)
        trial = act('begin', json.loads(encoded(state, MAX_STATE)), request, moment, own=attempt)
        if 'attempt_token' not in trial:
            return trial
        state.setdefault('routing_pending', []).append(dict(attempt_id=attempt, checkout=str(checkout), at=moment))
        return dict(reserved=True)
    reserved = _run('begin', store, request, now=now, hook=reserve)
    if not reserved.get('reserved'):
        return reserved
    service = command.managed_service(checkout)
    result = None
    try:
        bound, seen = _prepare(service, checkout, request, limits, attempt)
        if bound is None:
            return dict(fallback=seen, advisory=True)
        settings.require(type(seen) is int, 'openrouter_preparation_stale')
        with settings.admission(cwd, 'proposals', store=store, revision=seen) as report:
            _external(report, request, now)
            result = _run('begin', store, request, now=now,
                management=dict(scope_id=report['scope_id'], revision=report['revision']),
                activation_ref=report['activation']['ref'],
                routing=dict(attempt_id=attempt, checkout=str(checkout), **bound))
        return result
    finally:
        try:
            if result is None or 'attempt_token' not in result:
                _release(service, store, attempt, now)
        finally:
            service.close()


def _authorize(row, managed, granted):
    """Before a dispatch is stored: the dollar preflight again, the routing authority and an in-process `check` for
    a routed attempt. The token stays here; it is never stored or returned. A managed project never dispatches an
    unrouted attempt. Before the routing dispatch a refusal closes the hold; after it, `run` keeps the exposure."""
    bound = row.get('routing')
    if bound is None:
        from runtime import routing_settings
        return routing_settings.LEGACY_ROUTE_RETIRED if managed else None
    command, launch = _routing()
    service = command.managed_service(bound['checkout'])
    try:
        attempt, facts = bound['attempt_id'], bound['facts']
        reason = launch.preflight(launch._call(service, dict(v=1, op='status')), dollar=True)
        sent = {} if reason is not None else launch._call(service, dict(v=1, op='dispatch', attempt_id=attempt,
                                                                         request=facts))
        if sent.get('state') == 'already_dispatched':
            return 'already_dispatched'  # an earlier grant's send is unknown: reconcile, never close or resend
        authority = sent.pop('authority', None)
        if reason is None and not (sent.get('state') == 'dispatched' and authority):
            reason = sent.get('reason') or 'dispatch_refused'
        if reason is not None:
            _close(service, attempt)  # still admitted: nothing was dispatched
            return reason
        granted.append((bound['checkout'], attempt))
        checked = launch._call(service, dict(v=1, op='check', attempt_id=attempt, authority=authority,
                                             request=facts))
        return None if checked.get('state') == 'allow' else checked.get('reason') or 'check_refused'
    finally:
        service.close()


def _charged_usage(usage, reserved):
    """The routing usage a self-reported cost settles with (coordinator decision on S1 of the PR #216 review).

    The helper cannot verify a provider receipt, so a reported cost never lowers the charge: the hold is charged its
    full reservation, labelled `estimate`, and a reported cost above it stays an unconfirmed overrun for the operator.
    Tokens are kept as observations. A report with any unknown cost, or no known reservation, settles nothing
    (`cost_unknown`). A receipt re-read that may lower the charge is future work."""
    from runtime import routing, usage_budget
    if routing.event_error(dict(event_id='usage', attempt_id='usage', kind='complete', usage=usage)) is not None:
        raise ValueError('invalid_usage')
    costs = [item.get('cost_usd_micros') for item in usage['requests']]
    if None in costs or type(reserved) is not int:
        return None
    limit = usage_budget.MAX_INTEGER
    tokens = {field: min(limit, sum(item['tokens'][field] for item in usage['requests']))
              for field in usage_budget.TOKEN_FIELDS}
    return dict(source='estimate', requests=[dict(kind='primary', model=usage['requests'][0]['model'], tokens=tokens,
                                                  cost_usd_micros=min(limit, max(sum(costs), reserved)))])


def _settle(row, request):
    """Before an outcome is stored: close the routed hold. A dispatched attempt completes; a self-reported `usage`
    is charged as `_charged_usage` says, and without one the dollar exposure stays held as `cost_unknown`."""
    command, launch = _routing()
    dispatched = 'dispatched_at' in row
    usage = _charged_usage(request['usage'], row['routing'].get('reserved')) \
        if dispatched and 'usage' in request else None
    service = command.managed_service(row['routing']['checkout'])
    try:
        recorded = launch._record(service, row['routing']['attempt_id'],
                                  'complete' if dispatched else 'not_dispatched',
                                  **({} if usage is None else dict(usage=usage)))
    finally:
        service.close()
    return None if recorded.get('state') == 'recorded' else recorded.get('reason') or 'record_refused'


# -- activation-gate self-check (C.3 design 4.2: "External choice and proposals") ---------------------------------
SELF_CHECK_BUDGET = 8  # seconds, inside the activation gate's own 10 s budget
SELF_CHECK_UNSAFE = 'self_check_workdir_unsafe'


def managed_self_check(workdir):
    """`[]` when a managed `begin` obtains one routing admission on a test `gateway` route, else fixed codes.

    The check replaces module globals for its run, so it runs this script with `--managed-self-check` in a child of
    this interpreter with a fixed environment, never in the calling process. Every store is created in `workdir`."""
    import signal
    import subprocess
    root = _self_check_root(workdir)  # the child's own safety checks, before this process creates anything
    if root is None:
        return [SELF_CHECK_UNSAFE]
    for name in ('home', 'tmp'):
        (root / name).mkdir(mode=0o700)
    env = dict(PATH='/usr/bin:/bin', HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'), LANG='C.UTF-8')
    child = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--managed-self-check',
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
        codes = json.loads(output)['codes']
    except (ValueError, KeyError, TypeError):
        return ['self_check_error']
    if type(codes) is not list or not all(type(code) is str for code in codes):
        return ['self_check_error']
    return codes if child.returncode == 0 or codes else ['self_check_error']


def _self_check_root(workdir):
    """`workdir` as an empty owner-only directory outside every repository and the default state, or None."""
    command, launch = _routing()
    from runtime import usage_settings
    path = Path(workdir).absolute()
    try:
        parent = path.parent.resolve(strict=True)
    except OSError:
        return None
    defaults = [command.default_settings().parent.parent, usage_settings.directory(), settings.BASIC_STORAGE]
    if any((folder / marker).exists() for folder in (parent, *parent.parents) for marker in ('.git', '.claude-plugin',
                                                                                              '.codex-plugin')):
        return None
    if any(default.resolve() == folder for default in defaults for folder in (parent / path.name, parent, *parent.parents)):
        return None
    return launch._sandbox(path)


def _self_check(workdir):
    """The flag's body: it never opens a default path; every store lives in `workdir`."""
    root = _self_check_root(workdir)
    if root is None:
        return [SELF_CHECK_UNSAFE]
    from contextlib import contextmanager
    command, launch = _routing()
    from runtime import routing, routing_observations, routing_settings, usage_settings
    routing_path, ledger_path = root / 'routing' / 'settings.json', root / 'ledger' / 'organizations.sqlite3'
    command.default_settings, command.default_ledger = (lambda: routing_path), (lambda: ledger_path)
    usage_settings.directory = lambda: root / 'usage'
    settings.BASIC_STORAGE = root / 'openrouter-consent'
    checkout, folder = root / 'checkout', root / 'proposals'
    for path in (checkout, folder):
        path.mkdir(mode=0o700)
    store = folder / 'discovery.json'
    report = dict(enabled=True, routes=['proposals'], revision=1, local_configuration='ready', checkout=str(checkout),
                  scope_id='self-check', external_permission=None, proposal_store=str(store),
                  activation=dict(ref='activation:openrouter:self-check:r1', funding_ref='self-check', classes=None,
                                  destinations=None))

    @contextmanager
    def admission(cwd, route, *, store=None, revision=None, **_):
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
        model = dict(model='self-check/model', provider='self-check', effort=None)
        source = routing_observations.StaticSource(
            [dict(route='self-check-gateway', status='qualified', **launch.route_identity(HOST, **model),
                  billing_class='gateway', pool='self-check', account='self-check', demand={'credit': 1000},
                  integration_route='proposals', endpoint='self-check')],
            lambda pool, at: [dict(id='credit', limit=10**12, used=0, rolling=False, observed_at=at,
                                   organization='self-check', organization_revision=enabled['organization']['revision'])],
            lambda route, scope: dict(state='ready', scope=scope, ref='self-check'))
        routing_observations.default_source = lambda *args, **kwargs: source
        token = _run('claim', store, {})['refresh_token']
        endpoint = dict(model='self-check/model', provider='self-check', context_length=4096, max_output_tokens=512,
                        modalities=['text'], supported_parameters=[])
        published = _run('publish', store, dict(refresh_token=token, metadata=dict(
            models=[dict(id='self-check/model', context_length=4096, max_output_tokens=512)], endpoints=[endpoint],
            benchmarks=[], task_usage=[], coverage=dict(complete=True, source='self-check'))))
        permission = external_delegation.effective_permission(None, report['activation'], service='openrouter',
                                                              mode='proposal', class_name='self-check', **model)
        packet = external_delegation.prepare(checkout, permission, [dict(kind='text', text='self-check', sources=[])])
        request = dict(task_id='self-check', **model, snapshot_digest=published['snapshot_digest'], base_ref='self-check',
                       profile=dict(family='self-check', language=None, framework=None, difficulty='small',
                                    scope='none', verification='none'), output_tokens=16, timeout_seconds=1,
                       requirements=dict(context_tokens=16, output_tokens=16, modalities=['text'], parameters=[]),
                       external=dict(packet['binding'], automatic=True, local_preferred=False, class_name='self-check',
                                     coordinator='self-check-coordinator', reviewer='self-check-reviewer',
                                     integration_available=True))
        admissions, real = [], routing.admit

        def admit(*args, **kwargs):
            result = real(*args, **kwargs)
            admissions.append(result)
            return result
        routing.admit = admit
        begun = run('begin', store, request, cwd=checkout)
        if 'attempt_token' not in begun or [(item.get('status'), item.get('billing_class')) for item in admissions] \
                != [('admitted', 'gateway')]:
            codes.append('proposal_admission_failed')
    except Exception:  # noqa: BLE001 - any failure is a fixed code, never a traceback
        codes.append('self_check_error')
    finally:
        service.close()
    return codes


def _run(action: str, store: Path, request: dict, *, now: float | None = None, management=None, activation_ref=None,
         routing=None, authorize=None, settle=None, hook=None) -> dict:
    """Serialize one short local operation; callers perform all external actions."""
    if not isinstance(request, dict):
        raise ValueError('request must be an object')
    request = json.loads(encoded(request, MAX_INPUT))
    now = number(time.time() if now is None else now, 'now')
    store = store_path(store)
    try:
        lock = os.open(str(store) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(lock, 'r+') as handle:
            private_file(handle.fileno())
            deadline = time.monotonic() + 2
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return dict(fallback='store_busy', advisory=True)
                    time.sleep(0.01)
            try:
                fd = os.open(store, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                state = dict(schema=1, attempts=[])
            else:
                with os.fdopen(fd, 'rb') as saved:
                    private_file(saved.fileno())
                    raw = saved.read(MAX_STATE + 1)
                if len(raw) > MAX_STATE:
                    raise ValueError('state size limit exceeded')
                state = json.loads(raw)
                if not isinstance(state, dict) or state.get('schema') != 1 or not isinstance(state.get('attempts'), list):
                    raise ValueError('incompatible state; preserve it and use native execution')
            before = encoded(state, MAX_STATE)
            if action == 'dispatch':
                row = next((row for row in state['attempts'] if row['attempt_token'] == request.get('attempt_token')), None)
                if row is not None and row.get('management') != management:
                    raise ValueError('openrouter_preparation_stale')
            own = routing['attempt_id'] if routing is not None else None
            result = hook(state, now) if hook is not None else act(
                action, state, request, now, activation_ref=activation_ref, authorize=authorize, settle=settle, own=own)
            if action == 'begin' and 'attempt_token' in result:
                state['attempts'][-1]['management'] = management
                if routing is not None:
                    state['attempts'][-1]['routing'] = routing
                    _forget(state, own)
            try:
                after = encoded(state, MAX_STATE)
            except ValueError:
                return dict(fallback='capacity', advisory=True)
            if after != before:
                fd, temporary = tempfile.mkstemp(prefix='.proposal-', dir=store.parent)
                try:
                    with os.fdopen(fd, 'wb') as output:
                        output.write(after)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, store)
                    directory = os.open(store.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            return dict(result, advisory=True)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError('unsafe, unreadable or corrupt proposal store: ' + str(exc)) from exc


def main():
    if sys.argv[1:2] == ['--managed-self-check']:  # C.3 design 4.2; refused unless the workdir is a safe sandbox
        if len(sys.argv) != 3:
            print(json.dumps(dict(codes=[SELF_CHECK_UNSAFE])))
            return 2
        codes = _self_check(sys.argv[2])
        print(json.dumps(dict(codes=codes)))
        return 1 if codes else 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action')
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--cwd', type=Path)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('request size limit exceeded')
        result = run(args.action, args.store, json.loads(raw), cwd=args.cwd)
        output = encoded(result, MAX_RESPONSE - 1 if args.action == 'triage' else MAX_STATE)
    except (ValueError, RecursionError) as exc:
        print(json.dumps(dict(error=str(exc))))
        return 1
    sys.stdout.buffer.write(output + b'\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
