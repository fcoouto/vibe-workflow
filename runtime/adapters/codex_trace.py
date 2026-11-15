"""Bounded raw Codex trace evidence, never caller-authored identity mappings.

Custody follows the existing cooperative native observer: a trusted host selects
private recorder files under the originally recorded native home, outside the
checkout. This does not authenticate files against an unrestricted same-user
writer. No public task/proposal/CLI input selects this source.
"""

import hashlib
import math
import os
from pathlib import Path
import re
import time
import uuid

from runtime import onboarding as d3, startup
from runtime.doctor import ObservationError


MAX_ENTRIES = 4096
MAX_FILES = 32
MAX_BYTES = 24 * 1024 * 1024
MAX_FILE = 8 * 1024 * 1024
MAX_RECORD = 128 * 1024
MAX_PAYLOAD = 128 * 1024
MAX_DEPTH = 32
MAX_EVENTS = 8192
TRACE_PROOFS = 7
_MARKER = '#native-trace-v1:'


def _require(condition, code='native_trace_relation_unavailable'):
    if not condition:
        raise ObservationError(code)


def _check(source):
    deadline = source['deadline']
    _require(type(deadline) in (float, int) and math.isfinite(deadline) and time.monotonic() < deadline, 'timeout')
    budget = source.setdefault('_budget', dict(files=0, bytes=0, entries=0))
    _require(budget['files'] <= MAX_FILES and budget['bytes'] <= MAX_BYTES
        and budget['entries'] <= MAX_ENTRIES, 'native_trace_budget_exceeded')
    return budget


def _stamp(path, *, directory=False):
    descriptor = d3._open_path(path, os.O_RDONLY | (os.O_DIRECTORY if directory else 0))
    try:
        info = os.fstat(descriptor)
        d3._private(info, directory=directory)
        return dict(device=info.st_dev, inode=info.st_ino)
    finally:
        os.close(descriptor)


def _custody(source):
    _check(source)
    paths = {key: startup._canonical_path(source[key]) for key in
        ('bundle', 'native_home', 'repository_root', 'rollout_path')}
    home, bundle, root, rollout = (paths[key] for key in
        ('native_home', 'bundle', 'repository_root', 'rollout_path'))
    _require(bundle != home and bundle.is_relative_to(home) and rollout != home and rollout.is_relative_to(home)
        and not home.is_relative_to(root) and not bundle.is_relative_to(root) and not rollout.is_relative_to(root),
        'native_trace_custody_unavailable')
    for target in (bundle, rollout.parent):
        for path in (target, *target.parents):
            if not path.is_relative_to(home):
                break
            _check(source)
            _stamp(path, directory=True)
            _require(not os.path.lexists(path/'.git'), 'native_trace_custody_unavailable')
    custody = [_stamp(path, directory=True) for path in (home, bundle, bundle/'payloads')]
    custody.append(_stamp(rollout))
    _check(source)
    return paths, custody


def _entries(path, source):
    descriptor = d3._open_path(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        d3._private(os.fstat(descriptor), directory=True)
        with os.scandir(descriptor) as entries:
            for _ in entries:
                budget = _check(source)
                budget['entries'] += 1
                _check(source)
    finally:
        os.close(descriptor)


def _read(path, source, limit):
    budget = _check(source)
    budget['files'] += 1
    _check(source)
    physical = _stamp(path)
    raw = d3._read_file(path, min(limit, MAX_FILE, MAX_BYTES-budget['bytes']), private=True, expected_binding=physical)
    budget['bytes'] += len(raw)
    _check(source)
    _require(_stamp(path) == physical, 'native_trace_source_changed')
    return raw, physical


def _json(raw, source, limit):
    _check(source)
    _require(len(raw) <= limit, 'native_trace_budget_exceeded')
    # Bound nesting before json.loads allocates a nested object graph.
    depth = 0
    quoted = escaped = False
    for offset, byte in enumerate(raw):
        if offset % 4096 == 0:
            _check(source)
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            _require(depth <= MAX_DEPTH, 'native_trace_budget_exceeded')
        elif byte in (93, 125):
            depth -= 1
    value = startup.json_object(raw, limit=limit)
    _check(source)
    return value


def _id(value):
    _require(isinstance(value, str) and 0 < len(value) <= 256 and value.isprintable() and bool(value.strip()))
    return value


def _proof(path, raw, physical, outer, custody):
    numbers = [physical['device'], physical['inode']]
    numbers.extend(number for binding in custody for number in (binding['device'], binding['inode']))
    token = ':'.join(map(str, numbers))+':'+outer.encode().hex()
    return dict(reference=str(path)+_MARKER+token, sha256=hashlib.sha256(raw).hexdigest())


def retained_selector(proofs):
    """Recover only the originally issued bundle/outer selector, not ambient state."""
    _require(len(proofs) == TRACE_PROOFS)
    path, split, token = proofs[0]['reference'].partition(_MARKER)
    _require(split and Path(path).name == 'manifest.json')
    parts = token.split(':')
    _require(len(parts) == 11 and all(re.fullmatch(r'(0|[1-9][0-9]{0,19})', part) for part in parts[:10])
        and re.fullmatch(r'(?:[a-f0-9]{2}){1,1024}', parts[-1]) is not None)
    outer = _id(bytes.fromhex(parts[-1]).decode('utf-8'))
    return str(startup._canonical_path(path).parent), outer


def recheck(fact, source):
    """Re-read every relied-on file with its issued hash and physical binding."""
    try:
        paths, custody = _custody(source)
        proofs = fact['sources']
        _require(len(proofs) == TRACE_PROOFS)
        for proof in proofs:
            path, split, _ = proof['reference'].partition(_MARKER)
            path = startup._canonical_path(path)
            _require(split and path.is_relative_to(paths['bundle']))
            limit = MAX_FILE if path.name == 'trace.jsonl' else MAX_PAYLOAD
            raw, physical = _read(path, source, limit)
            _require(_proof(path, raw, physical, fact['native_call_id'], custody) == proof,
                'native_trace_source_changed')
        _check(source)
    except ObservationError:
        raise
    except (startup.StartupError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ObservationError('native_trace_source_changed') from None


def read_rejection(source, *, thread_id, turn_id, native_call_id, source_js, submitted_patch, rejection):
    """Derive one non-yielding parent cell's exact denied apply_patch invocation."""
    try:
        thread, turn, outer = map(_id, (thread_id, turn_id, native_call_id))
        paths, custody = _custody(source)
        bundle = paths['bundle']
        for directory in (bundle, bundle/'payloads'):
            _entries(directory, source)
        proofs = []

        def read(path, limit):
            raw, physical = _read(path, source, limit)
            proofs.append(_proof(path, raw, physical, outer, custody))
            return raw

        manifest = _json(read(bundle/'manifest.json', 16384), source, 16384)
        d3._object(manifest, ('schema_version', 'trace_id', 'rollout_id', 'root_thread_id',
            'started_at_unix_ms', 'raw_event_log', 'payloads_dir'))
        trace_id = str(uuid.UUID(manifest['trace_id']))
        _require(manifest['schema_version'] == 1 and type(manifest['schema_version']) is int
            and manifest['trace_id'] == trace_id and manifest['rollout_id'] == manifest['root_thread_id'] == thread
            and type(manifest['started_at_unix_ms']) is int and manifest['started_at_unix_ms'] >= 0
            and manifest['raw_event_log'] == 'trace.jsonl' and manifest['payloads_dir'] == 'payloads'
            and bundle.name == f'trace-{trace_id}-{thread}')
        raw = read(bundle/'trace.jsonl', MAX_FILE)
        _require(raw.endswith(b'\n'))
        lines = raw.splitlines(keepends=True)
        _require(0 < len(lines) <= MAX_EVENTS, 'native_trace_budget_exceeded')
        events = []
        for seq, line in enumerate(lines, 1):
            event = _json(line, source, MAX_RECORD)
            d3._object(event, ('schema_version', 'seq', 'wall_time_unix_ms', 'rollout_id',
                'thread_id', 'codex_turn_id', 'payload'))
            _require(type(event['seq']) is int and event['seq'] == seq
                and type(event['schema_version']) is int and event['schema_version'] == 1
                and event['rollout_id'] == thread and type(event['wall_time_unix_ms']) is int
                and event['wall_time_unix_ms'] >= manifest['started_at_unix_ms']
                and isinstance(event['payload'], dict) and isinstance(event['payload'].get('type'), str))
            events.append(event)

        def one(kind, **selectors):
            matches = [event for event in events if event['payload']['type'] == kind
                and all(event['payload'].get(key) == value for key, value in selectors.items())]
            _require(len(matches) == 1)
            return matches[0]

        start = one('rollout_started')
        _require(start['seq'] == 1 and start['payload'] == dict(type='rollout_started',
            trace_id=trace_id, root_thread_id=thread))
        thread_start = one('thread_started', thread_id=thread)
        turn_start = one('codex_turn_started', codex_turn_id=turn)
        turn_end = one('codex_turn_ended', codex_turn_id=turn)
        cell_start = one('code_cell_started', model_visible_call_id=outer)
        cell = _id(cell_start['payload']['runtime_cell_id'])
        _require(one('code_cell_started', runtime_cell_id=cell) == cell_start
            and cell_start['payload']['source_js'] == source_js)
        cell_initial = one('code_cell_initial_response', runtime_cell_id=cell)
        cell_end = one('code_cell_ended', runtime_cell_id=cell)
        nested = [event for event in events if event['payload']['type'] == 'tool_call_started'
            and event['payload'].get('requester') == dict(type='code_cell', runtime_cell_id=cell)]
        _require(len(nested) == 1)
        tool_start = nested[0]; invocation = tool_start['payload']
        inner = _id(invocation['tool_call_id'])
        runtime_tool = _id(invocation['code_mode_runtime_tool_id'])
        _require(inner != outer and one('tool_call_started', tool_call_id=inner) == tool_start
            and invocation['model_visible_call_id'] is None and invocation['kind'] == dict(type='apply_patch'))
        tool_end = one('tool_call_ended', tool_call_id=inner)
        selected = [turn_start, cell_start, tool_start, tool_end, cell_initial, cell_end, turn_end]
        _require([event['seq'] for event in selected] == sorted({event['seq'] for event in selected})
            and thread_start['seq'] < turn_start['seq']
            and thread_start['thread_id'] == thread
            and turn_start['payload']['thread_id'] == thread
            and all(event['thread_id'] == thread and event['codex_turn_id'] == turn for event in selected)
            and tool_end['payload']['status'] == cell_initial['payload']['status'] == cell_end['payload']['status'] == 'failed'
            and turn_end['payload']['status'] in ('completed', 'failed'))
        # Reject another cell using the same runtime bridge ID within this cell.
        _require(sum(event['payload'].get('code_mode_runtime_tool_id') == runtime_tool
            and event['payload'].get('requester') == invocation['requester'] for event in events) == 1)
        seen_payloads = set()

        def payload(reference, kind):
            d3._object(reference, ('raw_payload_id', 'kind', 'path'))
            match = re.fullmatch(r'raw_payload:([1-9][0-9]{0,18})', reference['raw_payload_id'])
            _require(match is not None and reference['kind'] == dict(type=kind)
                and reference['path'] == f'payloads/{match[1]}.json'
                and reference['raw_payload_id'] not in seen_payloads)
            seen_payloads.add(reference['raw_payload_id'])
            return _json(read(bundle/reference['path'], MAX_PAYLOAD), source, MAX_PAYLOAD)

        metadata = payload(thread_start['payload']['metadata_payload'], 'session_metadata')
        _require(metadata['thread_id'] == thread and metadata['agent_path'] == thread_start['payload']['agent_path'] == '/root'
            and metadata['cwd'] == str(paths['repository_root']) and metadata['rollout_path'] == str(paths['rollout_path'])
            and metadata['session_source'] == 'exec')
        request = payload(invocation['invocation_payload'], 'tool_invocation')
        _require(request == dict(tool_name='apply_patch', tool_namespace=None,
            payload=dict(type='custom', input=submitted_patch)))
        result = payload(tool_end['payload']['result_payload'], 'tool_result')
        _require(result == dict(type='error', error=rejection))
        for event in (cell_initial, cell_end):
            response = payload(event['payload']['response_payload'], 'tool_result')
            # RuntimeResponse uses default externally tagged Serde, not snake_case.
            _require(response == dict(response={'Result':dict(cell_id=cell, content_items=[],
                stored_values={}, error_text=rejection)}))
        relation = dict(trace_id=trace_id, rollout_id=thread, thread_id=thread, turn_id=turn,
            native_call_id=outer, runtime_cell_id=cell, code_mode_runtime_tool_id=runtime_tool,
            tool_call_id=inner)
        fact = dict(call_id=inner, native_call_id=outer, trace_relation=relation, sources=proofs)
        recheck(fact, source)
        return fact
    except ObservationError:
        raise
    except (startup.StartupError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ObservationError('native_trace_relation_unavailable') from None
