"""Cooperative Desktop recovery bookkeeping; native reads and sends remain external."""
import fcntl
import os
from pathlib import Path
import time
import uuid

from runtime import delegation as d6, evidence, state
from runtime.startup import json_object


def default_directory():
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'workstreams-recovery'


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _summary(data):
    parents = data['parents']
    return dict(ok=True, wanted='present' if any(p['eligible'] for p in parents.values()) else 'absent',
        suspended=data['suspended'], schedule_id=data['schedule_id'], pending=data['pending'], parents=parents)


def compact_result(report, action, thread_id=None):
    """Project public replies without changing private recovery evidence or send authority."""
    result = {key: value for key, value in report.items() if key != 'parents'}
    parents = report.get('parents', {})
    def summary(row):
        return dict(eligible=row['eligible'], pending_delivery=row['intent'] is not None)
    if action == 'status':
        result['parents'] = {identity: dict(cwd=row['parent']['cwd'], **summary(row))
            for identity, row in parents.items()}
    elif thread_id in parents:
        result['parent'] = dict(thread_id=thread_id, **summary(parents[thread_id]))
    return result


def _validate(data):
    for thread_id, parent in data['parents'].items():
        state._fields(parent, ('parent', 'eligible', 'episode', 'intent', 'last_turn', 'last_send', 'consumed_turns'))
        _require(type(parent['eligible']) is bool and isinstance(parent['parent'], dict) and
            parent['parent'].get('thread_id') == thread_id, 'Corrupt parent registration')
        state._integer(parent['parent']['deadline'], 'original recovery deadline')
        revision_key = _revision_key(parent['parent'])
        state._integer(parent['parent'][revision_key], 'admitted recovery revision', 1)
        _require(isinstance(parent['consumed_turns'], list) and all(isinstance(t, str) for t in parent['consumed_turns']), 'Corrupt consumed turns')
        episode = parent['episode']
        if episode is not None:
            state._fields(episode, ('start', 'deadline'))
            for value in episode.values():
                state._integer(value, 'episode time')
            _require(episode['start'] < episode['deadline'] <= episode['start'] + 3600 and
                episode['deadline'] <= parent['parent']['deadline'], 'Corrupt episode deadline')
        if parent['last_send'] is not None:
            state._integer(parent['last_send'], 'last recovery send')
        intent = parent['intent']
        if intent is not None:
            state._fields(intent, ('token', 'turn_id', 'result'))
            _require(intent['result'] in {'pending', 'sent', 'uncertain'}, 'Corrupt delivery intent')
            state._text(intent['token'], 'delivery token', 64)
            state._text(intent['turn_id'], 'delivery source turn', 256)


def _revision_key(parent):
    historical = 'workstream_revision' in parent
    _require(historical != ('recovery_revision' in parent), 'Ambiguous recovery revision')
    return 'workstream_revision' if historical else 'recovery_revision'


def preflight_feature(scope, directory):
    """Check before changing preferences; unresolved historical scopes stay original."""
    from runtime import workstreams
    workstreams._feature(scope, 'capacity_recovery')
    report = control(directory, 'status', {})
    scope_root = scope.repository_root.resolve()
    needed_elsewhere = any(row['eligible'] and Path(row['parent']['cwd']).resolve() != scope_root
        for row in report['parents'].values())
    pending = [thread_id for thread_id, row in report['parents'].items()
        if Path(row['parent']['cwd']).resolve() == scope_root and
        _revision_key(row['parent']) == 'workstream_revision' and
        (row['eligible'] or row['intent'] or report['pending'] and not needed_elsewhere or
         report['schedule_id'] and report['wanted'] == 'absent')]
    return report | dict(ready=not pending, historical_registrations=pending,
        reason='Reconcile original recovery registrations before changing this preference' if pending else None)


def reconcile_feature(scope, directory):
    """Withdraw stale recovery permissions; native schedule deletion remains external."""
    from runtime import workstreams
    feature = workstreams._feature(scope, 'capacity_recovery')
    if not Path(directory).exists():
        return control(directory, 'status', {})
    return control(directory, 'reconcile-feature', dict(cwd=str(scope.repository_root),
        enabled=feature['enabled'], recovery_revision=feature['revision']))


def register(scope, directory, request):
    """Register only the current enabled workflow task, preserving original pins."""
    from runtime import workstreams
    state._fields(request, ('client', 'session_id', 'task_id', 'scope_ref', 'expected_revision', 'recovery_revision'))
    state._identity(request)
    with state._database(scope) as db:
        _require(db is not None, 'Workflow task authority is missing')
        task = d6._assignment_task(scope, db, request)
        bound = state._task(db, state._scope_id(scope), 'codex', request['session_id'])
        _require(bound is not None and bound['task_id'] == task['task_id'], 'Parent is no longer the bound task')
        workstreams._feature_permission(scope, 'capacity_recovery', request['recovery_revision'])
        _require(task['state'] in {'TRIAGE', 'READY', 'WORKING', 'REVIEW'}, 'Parent task is not in progress')
        plan = d6._task_plan(scope, task)
        parent = dict(thread_id=task['session_id'], cwd=str(scope.repository_root),
            identity={key:task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')},
            pins=task['pins'], deadline=plan['limits']['deadline'], recovery_revision=request['recovery_revision'])
        return control(directory, 'register', dict(parent=parent))


def _authority(parent, package, *, reauthorize=False):
    from types import SimpleNamespace
    from runtime import workstreams
    from runtime.startup import acquire_project_entry, _entry_scope, StartupError
    try:
        acquisition = acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(parent['cwd'])), package)
        scope = _entry_scope(acquisition)
        if scope is not None and scope.runtime is not None:
            with state._database(scope) as db:
                if db is not None:
                    identity = parent['identity']
                    task = state._task(db, state._scope_id(scope), 'codex', identity['session_id'])
                    if task is None or task['task_id'] != identity['task_id']:
                        return 'stopped', None
                    _require(task['pins'] == parent['pins'] and
                        all(task[key] == value for key, value in identity.items()), 'Parent authority changed')
                    state._compatible(scope, task)
                    revision_key = _revision_key(parent)
                    mode = workstreams._feature(scope, 'capacity_recovery')
                    if revision_key == 'workstream_revision':
                        original = workstreams._load(scope)
                        if any(mode[key] != original[key] for key in ('enabled', 'revision')):
                            return 'stopped', mode['revision']
                    eligible = (mode['enabled'] and (reauthorize or mode['revision'] == parent[revision_key]) and
                        task['state'] in {'TRIAGE', 'READY', 'WORKING', 'REVIEW'} and not any(c['status'] == 'pending' and
                        c['kind'] in {'blocker', 'decision'} for c in task['conditions']))
                    return ('eligible' if eligible else 'stopped'), mode['revision']
    except (StartupError, ValueError, OSError, KeyError, TypeError):
        pass
    return 'unknown', None


def _saved(directory, request):
    saved = control(directory, 'status', {})['parents'].get(request['thread_id'])
    _require(saved is not None, 'Parent is not registered')
    return saved['parent']


def observe(directory, request, package):
    """Recheck current binding, original pins and admitted mode generation."""
    state._fields(request, ('thread_id', 'observation'))
    authority, _ = _authority(_saved(directory, request), package)
    observation = dict(request['observation'])
    _require('authority' not in observation, 'Task authority must be read locally')
    return control(directory, 'observe', request | dict(observation=observation | dict(authority=authority)))


def reactivate(directory, request, package):
    """Human continuation reauthorizes current mode without renewing the episode."""
    state._fields(request, ('thread_id',), ('schedule_absent',))
    saved = _saved(directory, request)
    authority, revision = _authority(saved, package, reauthorize=True)
    _require(authority == 'eligible', 'Human continuation requires current eligible task authority')
    return control(directory, 'reactivate', request | {_revision_key(saved): revision})


def _archive_expired(directory, data, now):
    """Retain expired inactive audit without consuming the bounded live registry."""
    def expired(record):
        return not record['eligible'] and record['intent'] is None and record['parent']['deadline'] <= now
    retired = [record for record in [*data['parents'].values(), *data['history']] if expired(record)]
    for record in retired:
        _validate(dict(parents={record['parent']['thread_id']: record}))
        value = dict(version=1, registration=record)
        path = directory / ('history-' + evidence._digest(value) + '.json')
        raw = d6._read_bytes(path)
        if raw is None:
            d6._atomic(path, value)
        else:
            _require(json_object(raw, limit=d6.MAX_GUIDANCE) == value, 'Archived recovery record changed')
    data['parents'] = {key: record for key, record in data['parents'].items() if not expired(record)}
    data['history'] = [record for record in data['history'] if not expired(record)]


def control(directory, action, request, *, now=None):
    """Serialize cooperating callers; a consumed token cannot make a native call atomic."""
    now = int(time.time()) if now is None else now
    state._integer(now, 'recovery clock')
    directory = Path(directory)
    _require(directory.is_absolute() and directory.resolve() == directory, 'Unsafe recovery directory')
    for ancestor in directory.parents:
        _require(not ancestor.is_symlink(), 'Unsafe recovery ancestor')
    empty = dict(version=1, parents={}, suspended=False, schedule_id=None, pending=None, clock=now, history=[])
    if action == 'status' and not directory.exists():
        return _summary(empty)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    state._private(directory, True)
    lock = directory / 'monitor.lock'
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        state._private(lock)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        path = directory / 'monitor.json'
        raw = d6._read_bytes(path)
        data = json_object(raw, limit=d6.MAX_GUIDANCE) if raw is not None else empty
        state._fields(data, ('version', 'parents', 'suspended', 'schedule_id', 'pending', 'clock', 'history'))
        _require(data['version'] == 1 and isinstance(data['parents'], dict) and
            type(data['suspended']) is bool and type(data['clock']) is int and isinstance(data['history'], list), 'Corrupt recovery registry')
        _validate(data)
        if action == 'status':
            state._fields(request, ())
            return _summary(data)
        rollback = now < data['clock']
        data['clock'] = max(now, data['clock'])
        result = _operate(data, action, request, now, rollback)
        if not rollback:
            _archive_expired(directory, data, now)
        d6._atomic(path, data)
        return _summary(data) | result
    finally:
        os.close(descriptor)


def _operate(data, action, request, now, rollback):
    if action == 'reconcile-feature':
        state._fields(request, ('cwd', 'enabled', 'recovery_revision'))
        state._text(request['cwd'], 'recovery checkout')
        _require(type(request['enabled']) is bool, 'Invalid recovery preference')
        state._integer(request['recovery_revision'], 'current recovery revision', 0)
        historical = []
        for thread_id, record in data['parents'].items():
            parent = record['parent']
            if Path(parent['cwd']).resolve() != Path(request['cwd']).resolve():
                continue
            if _revision_key(parent) == 'workstream_revision':
                historical.append(thread_id)
            if not request['enabled'] or parent[_revision_key(parent)] != request['recovery_revision']:
                record['eligible'] = False
        return dict(historical_registrations=historical)
    if action == 'register':
        state._fields(request, ('parent',))
        parent = request['parent']
        state._fields(parent, ('thread_id', 'cwd', 'identity', 'pins', 'deadline'), ('workstream_revision', 'recovery_revision'))
        revision_key = _revision_key(parent)
        state._integer(parent[revision_key], 'admitted recovery revision', 1)
        if revision_key == 'recovery_revision':
            _require(not any(_revision_key(row['parent']) == 'workstream_revision' and
                Path(row['parent']['cwd']).resolve() == Path(parent['cwd']).resolve() and
                (row['eligible'] or row['intent']) for row in data['parents'].values()),
                'Reconcile original recovery registrations before switching this scope')
        state._identity(parent['identity'])
        _require(parent['thread_id'] == parent['identity']['session_id'], 'Parent must match the workflow session')
        state._text(parent['cwd'], 'parent checkout')
        state._integer(parent['deadline'], 'parent deadline')
        old = data['parents'].get(parent['thread_id'])
        if old and old['parent'] != parent:
            _require(not old['eligible'] and not old['intent'] and old['parent']['identity'] != parent['identity'],
                'Reconcile the original parent registration before replacement')
            data['history'].append(old)
            old = None
        if old is None:
            _require(now < parent['deadline'], 'Original task deadline expired')
            data['parents'][parent['thread_id']] = dict(parent=parent, eligible=True, episode=None,
                intent=None, last_turn=None, last_send=None, consumed_turns=[])
        return {}
    if action == 'suspend':
        state._fields(request, ())
        data['suspended'] = True
        return {}
    if action == 'schedule':
        state._fields(request, ('observed',), ('schedule_id',))
        observed = request['observed']
        _require(observed in {'absent', 'active', 'paused', 'unknown'}, 'Unknown schedule observation')
        if data['pending']:
            return dict(operation='pending')
        known = data['schedule_id']
        if known is not None:
            _require(observed == 'absent' or request.get('schedule_id') == known, 'Schedule identity changed')
        elif observed != 'absent':
            return dict(operation='none', reason='Unowned schedule; reconcile without adoption')
        wanted = any(p['eligible'] for p in data['parents'].values())
        if known and observed in {'paused', 'absent'} and wanted:
            data['suspended'] = True
        if observed == 'unknown':
            return dict(operation='none')
        operation = ('delete' if known and not wanted and observed != 'absent' else
                     'create' if wanted and not known and observed == 'absent' and not data['suspended'] else 'none')
        if known and observed == 'absent' and not wanted:
            data['schedule_id'] = None
        if operation == 'none':
            return dict(operation=operation)
        token = uuid.uuid4().hex
        data['pending'] = dict(operation=operation, token=token)
        return dict(operation=operation, token=token)
    if action == 'record-schedule':
        state._fields(request, ('token', 'result', 'schedule_id'))
        pending = data['pending']
        _require(pending and pending['token'] == request['token'], 'Stale schedule operation')
        result = request['result']
        if result == 'uncertain':
            return dict(operation='pending')
        _require((pending['operation'], result) in {('create', 'created'), ('delete', 'deleted'),
            ('create', 'not-created'), ('delete', 'not-deleted')}, 'Unexpected schedule result')
        state._text(request['schedule_id'], 'schedule identity', 256)
        if result == 'created':
            data['schedule_id'] = request['schedule_id']
        elif result == 'deleted':
            _require(data['schedule_id'] == request['schedule_id'], 'Deleted schedule identity changed')
            data['schedule_id'] = None
        data['pending'] = None
        return {}
    state._text(request.get('thread_id'), 'registered parent')
    parent = data['parents'].get(request['thread_id'])
    _require(parent is not None, 'Parent is not registered')
    if action in {'withdraw', 'reactivate'}:
        state._fields(request, ('thread_id',), ('schedule_absent', 'workstream_revision', 'recovery_revision') if action == 'reactivate' else ())
        if action == 'withdraw':
            parent['eligible'] = False
        else:
            _require(not parent['intent'], 'Resolve uncertain delivery before human reactivation')
            if 'schedule_absent' in request:
                _require(request['schedule_absent'] is True and not data['pending'],
                    'Confirm actual deletion and resolve lifecycle ownership first')
                data['schedule_id'] = None
            parent['eligible'] = now < parent['parent']['deadline'] and (
                parent['episode'] is None or now < parent['episode']['deadline'])
            data['suspended'] = False
            revision_key = _revision_key(parent['parent'])
            other_key = 'workstream_revision' if revision_key == 'recovery_revision' else 'recovery_revision'
            _require(other_key not in request, 'Original recovery revision authority changed')
            if revision_key in request:
                state._integer(request[revision_key], 'current recovery revision', 1)
                parent['parent'][revision_key] = request[revision_key]
        return {}
    if action == 'record-send':
        state._fields(request, ('thread_id', 'token', 'result'))
        intent = parent['intent']
        _require(intent and intent['token'] == request['token'] and intent['result'] == 'pending', 'Stale recovery delivery')
        _require(request['result'] in {'sent', 'uncertain'}, 'Unknown delivery outcome')
        intent['result'] = request['result']
        return {}
    _require(action == 'observe', 'Unsupported recovery action')
    state._fields(request, ('thread_id', 'observation'))
    observation = request['observation']
    state._fields(observation, ('thread_status', 'turn_status', 'turn_id', 'error_code', 'will_retry', 'authority'))
    _require(observation['authority'] in {'eligible', 'stopped', 'unknown'}, 'Invalid task authority observation')
    _require(observation['will_retry'] is None or type(observation['will_retry']) is bool, 'Invalid native retry intent')
    turn = observation['turn_id']
    _require(turn is None or isinstance(turn, str) and len(turn) <= 256, 'Invalid observed turn')
    if observation['authority'] == 'stopped' or observation['error_code'] in {'rateLimitExceeded', 'usageLimitExceeded'}:
        parent['eligible'] = False
    episode = parent['episode']
    if now >= parent['parent']['deadline'] or episode and now >= episode['deadline']:
        parent['eligible'] = False
    intent = parent['intent']
    if intent and turn and turn != intent['turn_id']:
        parent['intent'] = None
    parent['last_turn'] = turn
    result = dict(send=False, deadline=episode['deadline'] if episode else None)
    if (rollback or data['suspended'] or data['pending'] or not parent['eligible'] or parent['intent'] or
            observation['authority'] != 'eligible' or observation['thread_status'] != 'idle' or
            observation['turn_status'] != 'failed' or observation['error_code'] != 'serverOverloaded' or
            not turn or turn in parent['consumed_turns']):
        return result
    if episode is None:
        episode = dict(start=now, deadline=min(now + 3600, parent['parent']['deadline']))
        parent['episode'] = episode
        result['deadline'] = episode['deadline']
    previous = parent['last_send']
    if now < episode['start'] + 60 or previous is not None and now < previous + 60:
        return result
    token = uuid.uuid4().hex
    parent['intent'] = dict(token=token, turn_id=turn, result='pending')
    parent['last_send'] = now
    parent['consumed_turns'].append(turn)
    return result | dict(send=True, token=token, thread_id=parent['parent']['thread_id'],
        prompt='Continue the original task. Reconcile existing tool and worker effects before repeating work. Preserve scope, settings, ownership and remaining budgets.')
