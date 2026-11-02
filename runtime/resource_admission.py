"""Cooperative host reservations; callers validate task authority and stopped proofs."""
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from runtime import delegation as d6, evidence, state
from runtime.startup import json_object


RECHECK_SECONDS = 5


def _require(condition, message):
    if not condition:
        raise state.StateError(message)


def _request(value):
    state._fields(value, ('id', 'owner', 'cpu', 'memory_bytes', 'capacity_cpu',
                         'capacity_memory_bytes', 'deadline', 'ready'))
    state._text(value['id'], 'host reservation id', 128)
    _require(isinstance(value['owner'], dict) and bool(value['owner']), 'Invalid resource owner')
    evidence._bounded(value['owner'], 16384)
    for name in ('cpu', 'memory_bytes', 'capacity_cpu', 'capacity_memory_bytes', 'deadline'):
        state._integer(value[name], name, 1 if name in {'cpu', 'capacity_cpu'} else 0)
        _require(value[name] < 2**53, 'Resource value exceeds exact integer range')
    _require(type(value['ready']) is bool, 'Invalid dependency readiness')
    _require(value['cpu'] <= value['capacity_cpu'] and
        value['memory_bytes'] <= value['capacity_memory_bytes'], 'Demand exceeds authorized allowance')


def _validate(data):
    state._fields(data, ('version', 'next_order', 'reservations', 'telemetry'))
    _require(type(data['version']) is int and data['version'] == 1 and
             isinstance(data['reservations'], dict), 'Corrupt resource registry')
    state._integer(data['next_order'], 'reservation sequence', 1)
    orders = set()
    for identity, row in data['reservations'].items():
        state._fields(row, ('request', 'state', 'order', 'proof'), ('last_status',))
        if row.get('last_status') is not None:
            status = row['last_status']
            state._fields(status, ('reason', 'next_recheck', 'warning'))
            _require(status['reason'] in {None, 'deadline', 'dependencies', 'older-request',
                'occupied-reservations', 'host-pressure', 'owner-ineligible'}, 'Corrupt admission status reason')
            if status['next_recheck'] is not None:
                state._integer(status['next_recheck'], 'resource recheck')
            _require(status['warning'] is None or isinstance(status['warning'], str), 'Corrupt admission warning')
        _request(row['request'])
        _require(identity == row['request']['id'] and row['state'] in
                 {'waiting', 'active', 'released', 'cancelled'}, 'Corrupt resource reservation')
        state._integer(row['order'], 'reservation order', 1)
        _require(row['order'] < data['next_order'] and row['order'] not in orders,
                 'Corrupt reservation order')
        orders.add(row['order'])
        if row['proof'] is not None:
            _proof(row['proof'])
        _require(row['state'] != 'released' or row['proof'] is not None,
                 'Released reservation lacks stopped proof')


def _proof(value):
    state._fields(value, ('path', 'sha256'))
    _require(isinstance(value['path'], str) and Path(value['path']).is_absolute() and
        isinstance(value['sha256'], str) and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None,
        'Invalid stopped proof binding')


def measure():
    """Bounded local-only observation; collection failure is handled by admission."""
    if sys.platform.startswith('linux'):
        with open('/proc/meminfo', encoding='ascii') as stream:
            raw = stream.read(16384)
        values = dict(re.findall(r'^(MemAvailable|MemTotal):\s+(\d+) kB$', raw, re.M))
        available, total = int(values['MemAvailable']) * 1024, int(values['MemTotal']) * 1024
    elif sys.platform == 'darwin':
        total = int(subprocess.run(['/usr/sbin/sysctl', '-n', 'hw.memsize'], check=True,
            capture_output=True, text=True, timeout=2).stdout)
        raw = subprocess.run(['/usr/bin/vm_stat'], check=True, capture_output=True,
            text=True, timeout=2).stdout
        page = int(re.search(r'page size of (\d+) bytes', raw).group(1))
        values = dict(re.findall(r'^(Pages free|Pages inactive|Pages speculative):\s+(\d+)\.', raw, re.M))
        available = sum(int(values[key]) for key in ('Pages free', 'Pages inactive', 'Pages speculative')) * page
    else:
        raise OSError('Host memory measurement unsupported')
    _require(0 <= available <= total and total > 0, 'Invalid host memory observation')
    return dict(sampled_at=int(time.time()), available_memory_bytes=available,
                pressure=available < total // 20)


def _sample(value, now):
    state._fields(value, ('sampled_at', 'available_memory_bytes', 'pressure'))
    state._integer(value['sampled_at'], 'sample time')
    state._integer(value['available_memory_bytes'], 'available memory')
    _require(type(value['pressure']) is bool and
        0 <= now - value['sampled_at'] < RECHECK_SECONDS, 'Stale or malformed host measurement')


def _telemetry(data, now, probe, probe_key=None):
    cached = data['telemetry']
    warning = None
    try:
        if isinstance(cached, dict) and type(cached.get('checked_at')) is int and \
                0 <= now - cached['checked_at'] < RECHECK_SECONDS and cached.get('key') == probe_key:
            state._fields(cached, ('checked_at', 'sample', 'warning', 'key'))
            _require(cached['warning'] is None or isinstance(cached['warning'], str), 'Malformed telemetry warning')
            if cached['sample'] is not None:
                _sample(cached['sample'], now)
            else:
                _require(cached['warning'] is not None, 'Missing telemetry warning')
            return cached['sample'], cached['warning']
        sample = (probe or measure)()
        _sample(sample, now)
    except Exception as error:
        sample = None
        warning = 'Host telemetry unavailable (' + type(error).__name__ + '); remaining admission checks apply'
    data['telemetry'] = dict(checked_at=now, sample=sample, warning=warning, key=probe_key)
    return sample, warning


def _reply(data, row=None, *, newly=False, reason=None, now=None, warning=None):
    if row is None:
        return dict(ok=True, reservations=data['reservations'], telemetry=data['telemetry'])
    row['last_status'] = dict(reason=reason, warning=warning,
        next_recheck=min(now + RECHECK_SECONDS, row['request']['deadline'])
            if row['state'] == 'waiting' and now < row['request']['deadline'] else None)
    return dict(ok=True, id=row['request']['id'], state=row['state'], admitted=row['state'] == 'active',
        newly_admitted=newly, **row['last_status'])


def _operate(data, action, request, now, probe, probe_key=None, eligible=None, disk_pressure=False, disk_warning=None):
    if action == 'request':
        _request(request)
        row = data['reservations'].get(request['id'])
        if row is None:
            _require(now < request['deadline'], 'Original resource task deadline expired')
            row = dict(request=dict(request), state='waiting', order=data['next_order'], proof=None)
            data['next_order'] += 1
            data['reservations'][request['id']] = row
        else:
            _require({k:v for k,v in row['request'].items() if k != 'ready'} ==
                {k:v for k,v in request.items() if k != 'ready'}, 'Resource reservation identity or demand changed')
            row['request']['ready'] = request['ready']
        if row['state'] != 'waiting':
            return _reply(data, row, now=now)
        if eligible is not None and request['ready'] and now < request['deadline']:
            for older in data['reservations'].values():
                if older['state'] != 'waiting' or not older['request']['ready'] or older['order'] >= row['order'] or older['request']['deadline'] <= now:
                    continue
                try:
                    current = eligible(older['request']['owner'])
                except Exception:
                    current = None
                if current is False:
                    older['request']['ready'] = False
                    _reply(data, older, reason='owner-ineligible', now=now)
        reason = None
        warning = None
        if now >= request['deadline']:
            reason = 'deadline'
        elif not request['ready']:
            reason = 'dependencies'
        elif any(r['state'] == 'waiting' and r['request']['ready'] and r['order'] < row['order'] and
                 r['request']['deadline'] > now for r in data['reservations'].values()):
            reason = 'older-request'
        else:
            active = [r['request'] for r in data['reservations'].values() if r['state'] == 'active']
            cpu = min([request['capacity_cpu']] + [r['capacity_cpu'] for r in active])
            memory = min([request['capacity_memory_bytes']] + [r['capacity_memory_bytes'] for r in active])
            if sum(r['cpu'] for r in active) + request['cpu'] > cpu or \
                    sum(r['memory_bytes'] for r in active) + request['memory_bytes'] > memory:
                reason = 'occupied-reservations'
            else:
                sample, warning = _telemetry(data, now, probe, probe_key)
                # Conservative: approved reservations may not have started consuming memory yet.
                if disk_pressure or (sample is not None and (sample['pressure'] or
                    sample['available_memory_bytes'] < sum(r['memory_bytes'] for r in active) + request['memory_bytes'])):
                    reason = 'host-pressure'
        if disk_warning is not None:
            warning = '; '.join(item for item in (warning, disk_warning) if item)
        if reason is None:
            row['state'] = 'active'
        return _reply(data, row, newly=reason is None, reason=reason, now=now, warning=warning)
    state._fields(request, ('id', 'owner'), ('stopped_proof',))
    state._text(request['id'], 'host reservation id', 128)
    row = data['reservations'].get(request['id'])
    _require(row is not None and row['request']['owner'] == request['owner'], 'Unknown resource owner')
    if row['state'] in {'released', 'cancelled'}:
        return _reply(data, row, now=now)
    if action == 'release' or row['state'] == 'active':
        _proof(request.get('stopped_proof'))
        row['proof'] = request['stopped_proof']
    row['state'] = 'released' if action == 'release' else 'cancelled'
    return _reply(data, row, now=now)


def _archive_expired(directory, data, now):
    """Archive expired terminal or never-launched guards before their removal."""
    for row in data['reservations'].values():
        if row['state'] == 'waiting' and row['request']['deadline'] <= now:
            row['state'] = 'cancelled'
            _reply(data, row, reason='deadline', now=now)
    retired = [row for row in data['reservations'].values()
        if row['state'] in {'released', 'cancelled'} and row['request']['deadline'] <= now]
    for row in retired:
        value = dict(version=1, reservation=row)
        path = directory / ('history-' + evidence._digest(value) + '.json')
        raw = d6._read_bytes(path)
        if raw is None:
            d6._atomic(path, value)
        else:
            _require(json_object(raw, limit=d6.MAX_GUIDANCE) == value, 'Resource history changed')
    for row in retired:
        del data['reservations'][row['request']['id']]


def control(directory, action, request, *, now=None, probe=None, probe_key=None, eligible=None, disk_pressure=False, disk_warning=None):
    """Reserve atomically; stopped-proof meaning and task authority belong to caller."""
    _require(action in {'status', 'request', 'release', 'cancel'}, 'Unknown resource admission action')
    _require(type(disk_pressure) is bool, 'Invalid disk pressure observation')
    if disk_warning is not None:
        state._text(disk_warning, 'disk telemetry warning', 256)
    evidence._bounded(request, d6.MAX_GUIDANCE)
    now = int(time.time()) if now is None else now
    state._integer(now, 'host clock')
    directory = Path(directory)
    _require(directory.is_absolute() and directory.resolve() == directory, 'Unsafe host resource directory')
    for ancestor in directory.parents:
        _require(not ancestor.is_symlink(), 'Unsafe resource ancestor')
    empty = dict(version=1, next_order=1, reservations={}, telemetry=None)
    path = directory/'admission.json'
    if action == 'status':
        state._fields(request, ())
        if not directory.exists():
            return _reply(empty)
        state._private(directory, True)
        raw = d6._read_bytes(path)
        data = json_object(raw, limit=d6.MAX_GUIDANCE) if raw is not None else empty
        _validate(data)
        return _reply(data)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    state._private(directory, True)
    lock = directory/'admission.lock'
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        state._private(lock)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        raw = d6._read_bytes(path)
        data = json_object(raw, limit=d6.MAX_GUIDANCE) if raw is not None else empty
        _validate(data)
        reply = _operate(data, action, request, now, probe, probe_key, eligible, disk_pressure, disk_warning)
        _archive_expired(directory, data, now)
        _validate(data)
        d6._atomic(path, data)
        return reply
    finally:
        os.close(descriptor)
