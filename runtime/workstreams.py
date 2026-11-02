"""Independent checkout preferences and legacy send gates over the existing D6 authority."""

from runtime import delegation as d6, evidence, state
from runtime.startup import json_object


LIMITATIONS = [
    'Workstreams is an optional Codex mode; saved enablement qualifies no native capability',
    'Send admission is cooperative and one-use; native delivery is neither atomic nor exactly-once',
    'Requested token, step and time allocations are not hard native execution enforcement',
    'Delivery records are coordinator assertions; native observation and D6 ownership proof remain authoritative',
]


def _path(scope):
    return state._path(scope).parent / ('workstreams-' + state._scope_id(scope) + '.json')


def _binding(scope):
    def identity(path):
        info = path.lstat()
        return dict(device=info.st_dev, inode=info.st_ino)
    return dict(scope_id=state._scope_id(scope), repository=identity(scope.repository_root),
        git_common=identity(scope.git_common_dir), directory=identity(state._path(scope).parent),
        database=identity(state._path(scope)))


def _load(scope):
    value = _load_bound(_path(scope), _binding(scope) if _path(scope).parent.exists() and state._path(scope).exists() else None)
    from runtime.startup import AcceptedScope
    if value['revision'] == 0 and isinstance(scope, AcceptedScope):
        with state._database(scope) as db:
            if db is not None and state._checkout_row(db, scope) is not None:
                value['enabled'] = True
    return value


def _load_bound(path, binding):
    """Read the supported setting using an already authenticated physical binding."""
    if not path.parent.exists():
        return dict(version=1, revision=0, enabled=False, binding=None)
    state._private(path.parent, True)
    raw = d6._read_bytes(path)
    if raw is None:
        return dict(version=1, revision=0, enabled=False, binding=None)
    value = json_object(raw, limit=d6.MAX_GUIDANCE)
    return _validate_setting_value(value,binding)


def _validate_setting_value(value,binding):
    """Validate recorded or current v1 data against its exact physical binding."""
    state._fields(value, ('version', 'revision', 'enabled', 'binding'), ('handoff', 'features'))
    d6._require(type(value['version']) is int and value['version'] == 1 and
        type(value['revision']) is int and 0 < value['revision'] < 2**53 and
        type(value['enabled']) is bool and binding is not None and value['binding'] == binding,
        'Workstreams setting is corrupt or its checkout authority changed')
    if 'features' in value:
        features = value['features']
        state._fields(features, (), ('resource_admission', 'capacity_recovery'))
        for setting in features.values():
            state._fields(setting, ('enabled', 'revision'))
            d6._require(type(setting['enabled']) is bool, 'Invalid feature preference')
            state._integer(setting['revision'], 'feature revision', 1)
            d6._require(setting['revision'] < 2**53, 'Feature revision exhausted')
    if 'handoff' in value:
        handoff = value['handoff']
        state._fields(handoff, ('version', 'descriptor', 'manifest_digest', 'source_revision', 'source_enabled'))
        d6._require(type(handoff['version']) is int and handoff['version'] == 1
            and type(handoff['source_enabled']) is bool, 'Invalid Workstreams handoff provenance')
        d6._require(d6._hex(handoff['descriptor']) and d6._hex(handoff['manifest_digest']), 'Invalid Workstreams handoff digest')
        state._integer(handoff['source_revision'], 'Workstreams original revision')
    return value


def initialize_default(scope, db):
    """Seed only a newly registered checkout inside its existing transaction."""
    d6._require(db is not None and db.in_transaction and db.execute(
        'select 1 from checkouts where scope_id=?', (state._scope_id(scope),)).fetchone() is not None,
        'Workstreams default requires existing checkout authority and its write transaction')
    current = _load(scope)
    if current['revision'] == 0:
        updated = dict(version=1, enabled=True, revision=1, binding=_binding(scope))
        d6._atomic(_path(scope), updated,
            guard=lambda: d6._require(_load(scope) == current, 'Workstreams setting changed during publication'))


FEATURES = {'resource_admission', 'capacity_recovery'}


def _feature_value(setting, feature):
    state._text(feature, 'Workstreams feature', 64)
    d6._require(feature in FEATURES, 'Unknown Workstreams feature')
    return dict(setting.get('features', {}).get(feature,
        dict(enabled=setting['enabled'], revision=setting['revision'])))


def _feature(scope, feature):
    return _feature_value(_load(scope), feature)


def _feature_permission(scope, feature, revision):
    state._integer(revision, 'feature revision', 1)
    setting = _feature(scope, feature)
    d6._require(setting['enabled'] and setting['revision'] == revision,
        'Feature is disabled or its revision changed; reassess pending work')
    return setting


def _permission(scope, revision):
    state._integer(revision, 'Workstreams revision', 1)
    setting = _load(scope)
    d6._require(setting['enabled'] and setting['revision'] == revision,
        'Workstreams is disabled or its revision changed; reassess pending work')
    return setting


def _report(scope, setting, db):
    records = d6._ledger(scope, db) if db is not None else []
    active = [r for r in records if r['owner']['scope_id'] == state._scope_id(scope)
        and 'workstream_revision' in r['body']['request'] and r['data']['status'] != 'settled']
    return dict(ok=True, control_scope='checkout', scope_id=state._scope_id(scope),
        checkout=str(scope.repository_root), enabled=setting['enabled'], revision=setting['revision'],
        unresolved_assignments=len(active), drained=not active, activity='unresolved' if active else 'drained',
        limitations=list(LIMITATIONS))


def _feature_report(scope, setting, feature):
    return dict(ok=True, control_scope='checkout', scope_id=state._scope_id(scope),
        checkout=str(scope.repository_root), feature=feature, **_feature_value(setting, feature))


def control(scope, action, request):
    """Inspect without creating state; controls do not launch, probe, or change any route."""
    state._fields(request, ('client',) if action == 'status' else ('client', 'expected_revision'), ('feature',))
    d6._require(request['client'] == 'codex' and action in {'enable', 'disable', 'status'},
        'Unsupported Workstreams control')
    if action == 'status':
        with state._database(scope) as db:
            setting = _load(scope)
            if 'feature' in request:
                return _feature_report(scope, setting, request['feature'])
            return _report(scope, setting, db)
    state._integer(request['expected_revision'], 'Workstreams expected revision')
    with d6._reservation_write(scope) as db:
        d6._require(db is not None and db.execute('select 1 from checkouts where scope_id=?', (state._scope_id(scope),)).fetchone() is not None,
            'Workstreams requires the existing checkout runtime authority')
        current = _load(scope)
        if 'feature' in request:
            feature = request['feature']
            choice = _feature_value(current, feature)
            d6._require(choice['revision'] == request['expected_revision'], 'Feature revision conflict')
            wanted = action == 'enable'
            features = {name: _feature_value(current, name) | dict(revision=max(1, _feature_value(current, name)['revision']))
                for name in FEATURES}
            if choice['enabled'] != wanted or choice['revision'] == 0:
                choice = dict(enabled=wanted, revision=choice['revision'] + 1)
            d6._require(choice['revision'] < 2**53, 'Feature revision exhausted')
            features[feature] = choice
            updated = dict(current, revision=max(1, current['revision']), binding=_binding(scope), features=features)
            d6._atomic(_path(scope), updated, guard=lambda: d6._require(_load(scope) == current,
                'Workstreams setting changed during publication'))
            return _feature_report(scope, updated, feature)
        d6._require(current['revision'] == request['expected_revision'], 'Workstreams revision conflict')
        wanted = action == 'enable'
        if current['enabled'] != wanted or current['revision'] == 0:
            updated = dict(current, version=1, enabled=wanted, revision=current['revision'] + 1, binding=_binding(scope))
            d6._require(updated['revision'] < 2**53, 'Workstreams revision exhausted')
            d6._atomic(_path(scope), updated, guard=lambda: d6._require(_load(scope) == current,
                'Workstreams setting changed during publication'))
            current = _load(scope)
        return _report(scope, current, db)


def validate_admission(scope, request):
    """Omission preserves ordinary D6. Mode-specific fields travel in the original assignment."""
    present = {'workstream_revision', 'workstream_limits'} & request.keys()
    if not present:
        return
    d6._require(present == {'workstream_revision', 'workstream_limits'}, 'Workstreams requires revision and task-derived limits')
    _permission(scope, request['workstream_revision'])
    d6._native_name('0' * 32, 'worker', request)
    limits = request['workstream_limits']
    state._fields(limits, ('context_id', 'rationale', 'output_tokens', 'context_tokens', 'max_steps',
        'max_seconds', 'max_continuations', 'backoff_seconds'))
    for key in ('context_id', 'rationale'):
        state._text(limits[key], 'Workstreams ' + key, 4096 if key == 'rationale' else 128)
    for key in ('output_tokens', 'context_tokens', 'max_steps', 'max_seconds', 'max_continuations', 'backoff_seconds'):
        state._integer(limits[key], 'Workstreams ' + key, 0 if key in {'max_continuations', 'backoff_seconds'} else 1)
        d6._require(limits[key] < 2**53, 'Workstreams allocation exceeds finite input bounds')


def consume_dispatch(scope, request):
    """Commit one send decision before the external native call; never report actual delivery."""
    request = _request(request, ('workstream_revision',))
    with d6._reservation_write(scope) as db:
        _permission(scope, request['workstream_revision'])
        d6._require(db is not None, 'Missing native assignment state')
        records = d6._ledger(scope, db)
        record, task = _owned(scope, db, request, records)
        d6._require(record['body']['request']['workstream_revision'] == request['workstream_revision'] and
            record['data']['status'] == 'prepared' and record['data']['observation'] is None and
            'workstream_dispatch' not in record['data'], 'Prepared Workstreams dispatch is stale or already consumed')
        d6._require(evidence._digest(_candidate(scope, record, task)) == record['data']['candidate']['digest'],
            'Prepared Workstreams candidate changed')
        _save(scope, db, record, task, 'workstream_dispatch', dict(version=1, workstream_revision=request['workstream_revision'],
            assignment=record['data']['assignment'], owner=record['owner'], state='consumed', native_delivery='unobserved'))
        return d6._reply(record) | dict(send_admitted=True, native_delivery='unobserved', revision=task['revision'],
            limitations=list(LIMITATIONS))


# Retain these entry points until resource/recovery and CLI callers use base delegation.
_request = d6._continuation_request
_owned = d6._continuation_owned
_candidate = d6._continuation_candidate
_save = d6._continuation_save
prepare_continuation = d6.prepare_continuation
consume_continuation = d6.consume_continuation
record_continuation = d6.record_continuation
validate_dependencies = d6.validate_dependencies
validate_settlement = d6.validate_settlement
validate_record = d6.validate_record
