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


def act(action, state, request, now):
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
        if any('outcome' not in row for row in state['attempts']):
            return dict(fallback='pending_attempt')
        if len(state['attempts']) >= MAX_ATTEMPTS:
            return dict(fallback='capacity')
        visibility = worker_visibility.optional_declaration(request.get('visibility'))
        profile(request.get('profile'))
        for key in ('model', 'provider', 'approval_ref', 'base_ref'):
            text(request.get(key), key)
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
        row['discovery_evidence'] = dict(model=model, endpoint=endpoint, benchmarks=[b for b in metadata['benchmarks'] if b.get('model') == request['model']])
        if visibility is not None:
            row['visibility'] = visibility
        if 'external' in request:
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
            if 'external_digest' in row and request.get('reviewer') != row['reviewer']:
                raise ValueError('external_independent_review_required')
            text(request.get('native_review_ref'), 'native_review_ref')
            checks = request.get('native_checks')
            if not isinstance(checks, list) or not checks or not all(isinstance(c, dict) and c.get('success') is True and isinstance(c.get('ref'), str) and c['ref'].strip() for c in checks):
                raise ValueError('successful native check references required')
        row['outcome'] = {key: request[key] for key in ('disposition', 'native_review_ref', 'native_checks', 'observations') if key in request}
        row['outcome']['recorded_at'] = now
        return dict(recorded=True, advisory=True)
    raise ValueError('unknown action')


def run(action: str, store: Path, request: dict, *, now: float | None = None, cwd=None) -> dict:
    if action in ('begin', 'dispatch'):
        try:
            with settings.admission(cwd, 'proposals', store=store) as report:
                packet = None
                if 'external' in request:
                    binding = request['external']
                    external_delegation.native_review(binding)
                    packet = external_delegation.check(Path(report['checkout']), report['external_permission'], binding,
                        service='openrouter', mode='proposal', model=request['model'], provider=request['provider'],
                        effort=request.get('effort'), class_name=binding['class_name'],
                        now=time.time() if now is None else now)
                elif action == 'dispatch':
                    raise ValueError('external_binding_required')
                result = _run(action, store, request, now=now,
                    management=dict(scope_id=report['scope_id'], revision=report['revision']))
                if action == 'dispatch' and result.get('dispatched'):
                    result.update(packet=packet['text'], model=request['model'], provider=request['provider'],
                                  effort=request.get('effort'), requires_native_review=True, accepted=False)
                return result
        except settings.SettingsError as exc:
            return dict(fallback=str(exc), advisory=True)
    return _run(action, store, request, now=now)


def _run(action: str, store: Path, request: dict, *, now: float | None = None, management=None) -> dict:
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
            result = act(action, state, request, now)
            if action == 'begin' and 'attempt_token' in result:
                state['attempts'][-1]['management'] = management
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
