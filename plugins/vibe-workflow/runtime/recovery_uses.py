"""Pure, bounded annotations of recovery dependency uses; these functions grant no permission."""
from copy import deepcopy
from collections.abc import Mapping
import json
from pathlib import PurePosixPath
import re

MAX_ROWS = 4096
MAX_BYTES = 4 * 1024 * 1024
MAX_CAPTURE_BYTES = 16 * 1024 * 1024


class UseContractError(ValueError):
    """A use annotation differs from its exact bounded contract."""


def key(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise UseContractError('Recovery use contains non-JSON data') from None


def _require(condition, message='Invalid recovery use contract'):
    if not condition: raise UseContractError(message)


def _fields(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _text(value):
    _require(isinstance(value, str) and 0 < len(value) <= 4096 and not any(ord(c) < 32 for c in value))


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None)


def _path(value, absolute=True):
    _text(value)
    path = PurePosixPath(value)
    _require(path.is_absolute() == absolute and str(path) == value and '..' not in path.parts and value not in ('.', '/'))


def _authority(value):
    _fields(value, ('directory', 'database', 'directory_binding', 'database_binding'))
    _path(value['directory']); _path(value['database'])
    _require(value['database'] == value['directory'] + '/runtime.sqlite3')
    for field in ('directory_binding', 'database_binding'):
        _fields(value[field], ('device', 'inode'))
        _require(all(type(n) is int and n >= 0 for n in value[field].values()))


def _artifact(value):
    _fields(value, ('name', 'digest'))
    _require(isinstance(value['name'], str) and re.fullmatch('[0-9a-f]{32}\\.json', value['name']) is not None)
    _hash(value['digest'])


def _pointer(value):
    _fields(value, ('authority', 'artifact')); _authority(value['authority']); _artifact(value['artifact'])


def _task(value):
    _fields(value, ('authority', 'scope_id', 'client', 'session_id', 'task_id'))
    _authority(value['authority']); _hash(value['scope_id'])
    for field in ('client', 'session_id', 'task_id'): _text(value[field])
    _require(len(value['task_id']) <= 128)


def _identity(value):
    _fields(value, ('plan_dir', 'transaction_id', 'manifest_digest'))
    _path(value['plan_dir']); _text(value['transaction_id']); _hash(value['manifest_digest'])


def _dependency(value):
    _fields(value, ('kind', 'reference')); ref = value['reference']
    if value['kind'] == 'task': _task(ref)
    elif value['kind'] in ('artifact', 'recovery'): _pointer(ref)
    elif value['kind'] == 'assignment':
        _fields(ref, ('authority', 'id', 'owner', 'assignment')); _authority(ref['authority']); _artifact(ref['assignment'])
        _require(isinstance(ref['id'], str) and re.fullmatch('[0-9a-f]{32}', ref['id']) is not None)
        _fields(ref['owner'], ('scope_id', 'client', 'session_id', 'task_id', 'dispatch_id'))
        _task(dict(authority=ref['authority'], **{k: ref['owner'][k] for k in ('scope_id', 'client', 'session_id', 'task_id')}))
        _text(ref['owner']['dispatch_id'])
    else: raise UseContractError('Unknown recovery use dependency')


def qualify_dependency(edge, authority):
    """Normalize an existing local typed edge without observing or authorizing its source."""
    _authority(authority); _fields(edge, ('kind', 'reference'))
    value = deepcopy(edge); ref = value['reference']
    if value['kind'] == 'artifact' and type(ref) is dict and set(ref) == {'name', 'digest'}:
        value['reference'] = dict(authority=deepcopy(authority), artifact=ref)
    elif value['kind'] == 'recovery' and type(ref) is dict and set(ref) == {'identity', 'artifact'}:
        _identity(ref['identity']); value['reference'] = dict(authority=deepcopy(authority), artifact=ref['artifact'])
    _dependency(value)
    return value


def validate(value):
    """Validate canonical shape only; the owner must independently check live provenance and coverage."""
    _fields(value, ('version', 'roots', 'edges', 'legacy_routes'))
    _require(type(value['version']) is int and value['version'] == 1)
    for field in ('roots', 'edges', 'legacy_routes'):
        rows = value[field]
        _require(type(rows) is list and len(rows) <= MAX_ROWS)
        _require([key(r) for r in rows] == sorted(set(key(r) for r in rows)), 'Recovery use rows are not canonical')
    _require(len(key(value).encode()) <= MAX_BYTES, 'Recovery use contract exceeds byte bound')
    for root in value['roots']:
        _require(type(root) is dict)
        historical = root.get('purpose') == 'historical-stop'
        _fields(root, ('purpose', 'reference', *(('task', 'proof', 'effect') if historical else ())))
        _require(root['purpose'] in ('historical-stop', 'content')); _dependency(root['reference'])
        if historical:
            _task(root['task']); _pointer(root['proof']); _fields(root['effect'], ('action_id', 'path'))
            _text(root['effect']['action_id']); _path(root['effect']['path'], absolute=False)
            _require(root['reference'] == dict(kind='task', reference=root['task']))
            _require(root['proof']['authority'] == root['task']['authority'])
    for edge in value['edges']:
        _fields(edge, ('root', 'parent', 'dependency', 'purpose'))
        _require(type(edge['root']) is int and 0 <= edge['root'] < len(value['roots']))
        if edge['parent'] is not None: _pointer(edge['parent'])
        _dependency(edge['dependency']); _require(edge['purpose'] in ('retention-only', 'content'))
        if value['roots'][edge['root']]['purpose'] == 'content':
            _require(edge['purpose'] == 'content', 'Content reason lost its permission requirement')
    origins = set()
    for row in value['legacy_routes']:
        _fields(row, ('origin', 'destinations')); origin = row['origin']
        _fields(origin, ('authority', 'artifact', 'identity', 'journal_sha256'))
        _authority(origin['authority']); _artifact(origin['artifact']); _identity(origin['identity']); _hash(origin['journal_sha256'])
        _require(key(origin) not in origins, 'Conflicting legacy route origin'); origins.add(key(origin))
        _require(type(row['destinations']) is list and len(row['destinations']) <= MAX_ROWS)
        for authority in row['destinations']: _authority(authority)
        _require([key(a) for a in row['destinations']] == sorted(set(key(a) for a in row['destinations'])))
    return value


def build(roots, edges, legacy_routes=()):
    """Canonicalize root indices, preserving different reasons for the same dependency."""
    roots = list(roots); edges = list(edges)
    _require(len(roots) <= MAX_ROWS and len(edges) <= MAX_ROWS, 'Recovery use row bound exceeded')
    unique = {key(root): deepcopy(root) for root in roots}; ordered = sorted(unique)
    indices = {raw: index for index, raw in enumerate(ordered)}
    remapped = []
    for edge in edges:
        _fields(edge, ('root', 'parent', 'dependency', 'purpose'))
        _require(type(edge.get('root')) is int and 0 <= edge['root'] < len(roots))
        remapped.append(dict(deepcopy(edge), root=indices[key(roots[edge['root']])]))
    canonical = lambda rows: [deepcopy(row) for _, row in sorted({key(row): row for row in rows}.items())]
    return validate(dict(version=1, roots=[unique[raw] for raw in ordered], edges=canonical(remapped), legacy_routes=canonical(legacy_routes)))


def merge(contracts):
    roots = []; indices = {}; edges = {}; routes = {}
    for contract in contracts:
        validate(contract); remap = {}
        for index, root in enumerate(contract['roots']):
            raw = key(root)
            if raw not in indices:
                _require(len(roots) < MAX_ROWS, 'Recovery use inherited roots exceed bound')
                indices[raw] = len(roots); roots.append(root)
            remap[index] = indices[raw]
        for edge in contract['edges']:
            row = dict(edge, root=remap[edge['root']]); edges[key(row)] = row
            _require(len(edges) <= MAX_ROWS, 'Recovery use inherited edges exceed bound')
        for row in contract['legacy_routes']:
            origin = key(row['origin'])
            _require(origin not in routes or routes[origin] == row, 'Legacy route projection changed')
            routes[origin] = row
            _require(len(routes) <= MAX_ROWS, 'Recovery use inherited routes exceed bound')
    return build(roots, list(edges.values()), list(routes.values()))


def check_coverage(contract, direct, metadata):
    """Compare exact qualified direct edges and each observed managed parent's dependencies.

    metadata maps key({authority, artifact}) to its qualified dependency list.
    Every producer-supplied parent is required, including parents with no dependencies.
    """
    validate(contract)
    _require(type(metadata) is dict and len(metadata) <= MAX_ROWS)
    _require(type(direct) in (list, tuple) and len(direct) <= MAX_ROWS)
    _require(all(type(rows) in (list, tuple) for rows in metadata.values()))
    _require(sum(len(rows) for rows in metadata.values()) <= MAX_ROWS)
    direct_keys = {key(edge) for edge in direct}
    for edge in direct: _dependency(edge)
    actual = {key(row['dependency']) for row in contract['edges'] if row['parent'] is None}
    _require(actual == direct_keys, 'Recovery use direct coverage differs')
    by_parent = {}
    for row in contract['edges']:
        if row['parent'] is not None:
            by_parent.setdefault(key(row['parent']), set()).add(key(row['dependency']))
    _require(set(by_parent) <= set(metadata), 'Recovery use has unobserved parent')
    for parent, dependencies in metadata.items():
        try: pointer = json.loads(parent)
        except (TypeError, ValueError): raise UseContractError('Invalid recovery metadata parent') from None
        _pointer(pointer)
        _require(key(pointer) == parent, 'Noncanonical recovery metadata parent')
        expected = {key(edge) for edge in dependencies}
        for edge in dependencies: _dependency(edge)
        incoming = [row for row in contract['edges'] if row['dependency'] in
            (dict(kind='artifact', reference=pointer), dict(kind='recovery', reference=pointer))]
        _require(incoming, 'Recovery use has an unrooted observed parent')
        for root in {row['root'] for row in incoming}:
            observed = {key(row['dependency']) for row in contract['edges']
                if row['root'] == root and row['parent'] == pointer}
            _require(observed == expected, 'Recovery use rooted metadata coverage differs')
            if any(row['root'] == root and row['purpose'] == 'content' for row in incoming):
                content = {key(row['dependency']) for row in contract['edges']
                    if row['root'] == root and row['parent'] == pointer and row['purpose'] == 'content'}
                _require(content == expected, 'Recovery content use lost its outgoing content coverage')
        _require(by_parent.get(parent, set()) == expected, 'Recovery use metadata coverage differs')
    for row in contract['edges']:
        if row['parent'] is None: continue
        incoming = dict(kind='artifact', reference=row['parent'])
        recovery = dict(kind='recovery', reference=row['parent'])
        _require(any(other['root'] == row['root'] and other['dependency'] in (incoming, recovery)
            and (row['purpose'] == 'content' or other['purpose'] == 'retention-only')
            for other in contract['edges']), 'Recovery use parent has no compatible incoming purpose')
        _require(any(other['root'] == row['root'] and other['parent'] is None
            and other['dependency'] == row['dependency'] and other['purpose'] == row['purpose']
            for other in contract['edges']), 'Recovery use lost its flattened purpose')
    return contract


def project(contract, direct, metadata):
    """Drop only cut uses. Unknown replacement routes fail; no purpose can be upgraded."""
    validate(contract); direct_keys = {key(edge) for edge in direct}
    edges = [row for row in contract['edges'] if
        (row['parent'] is None and key(row['dependency']) in direct_keys) or
        (row['parent'] is not None and key(row['parent']) in metadata and
         key(row['dependency']) in {key(edge) for edge in metadata[key(row['parent'])]})]
    result = build(contract['roots'], edges, contract['legacy_routes'])
    check_coverage(result, direct, metadata)
    return result


def legacy_projection(origin, content):
    """Carry an actual unextended source's original inventory; never a merged inventory."""
    _require('use_contract' not in content, 'Restricted inventory cannot create legacy permission')
    _require(origin['identity'] == content['identity'] and origin['journal_sha256'] == content['journal']['sha256'],
        'Legacy origin differs from observed source')
    destinations = sorted({key(authority): authority for authority in content['authorities']}.values(), key=key)
    return build([], [], [dict(origin=origin, destinations=destinations)])['legacy_routes'][0]


class CaptureBudget:
    """One explicit acquisition-wide comparison budget; this object grants no authority."""
    def __init__(self, *, parents=MAX_ROWS, edges=MAX_ROWS, jobs=MAX_ROWS, bytes=MAX_CAPTURE_BYTES):
        self.limits = dict(parents=parents, edges=edges, jobs=jobs, bytes=bytes)
        ceilings = dict(parents=MAX_ROWS,edges=MAX_ROWS,jobs=MAX_ROWS,bytes=MAX_CAPTURE_BYTES)
        _require(all(type(value) is int and 0 <= value <= ceilings[name] for name,value in self.limits.items()))
        self.used = dict.fromkeys(self.limits, 0)

    def take(self, **amounts):
        _require(set(amounts) <= set(self.limits))
        _require(all(type(value) is int and value >= 0 for value in amounts.values()))
        _require(all(self.used[name] + value <= self.limits[name] for name, value in amounts.items()),
            'Recovery capture aggregate bound exceeded')
        for name, value in amounts.items(): self.used[name] += value

    def retain(self, value):
        # Count before copying, including immutable source mappings, without a whole JSON copy.
        pending = [(value, False)]; active = set()
        while pending:
            item, leaving = pending.pop()
            if leaving:
                active.remove(id(item)); continue
            if isinstance(item, Mapping) or isinstance(item, (list, tuple)):
                _require(id(item) not in active, 'Recovery capture JSON is cyclic')
                active.add(id(item)); pending.append((item, True))
                self.take(bytes=2 + max(0, len(item)-1))
                if isinstance(item, Mapping):
                    for name, child in item.items():
                        _require(type(name) is str)
                        self.take(bytes=len(json.dumps(name,ensure_ascii=True).encode())+1)
                        pending.append((child,False))
                else: pending.extend((child,False) for child in item)
            else: self.take(bytes=len(json.dumps(item,ensure_ascii=True,allow_nan=False).encode()))



def _history_body(body):
    _require(type(body) is dict and type(body.get('version')) is int and body['version'] in (1,2))
    _fields(body, ('version','kind','identity','journal','authorities','tasks','assignments','artifacts','previous',
        *(['stopped','supersedes'] if body['version']==2 else []), *(['use_contract'] if 'use_contract' in body else []),
        *(['birth_handoff'] if 'birth_handoff' in body else [])))
    _require(body['kind']=='d4-recovery-source'); _identity(body['identity'])
    _fields(body['journal'],('sha256','observed_at','state')); _hash(body['journal']['sha256'])
    _require(type(body['journal']['observed_at']) is int and body['journal']['observed_at']>=0)
    _require(body['journal']['state'] in ('prepared','applying','interrupted','applied','blocked','rollback_partial','rolled_back'))
    for name in ('authorities','tasks','assignments','artifacts',*(['stopped','supersedes'] if body['version']==2 else [])):
        _require(type(body[name]) is list and len(body[name])<=MAX_ROWS)
    for authority in body['authorities']: _authority(authority)
    if body['previous'] is not None:_artifact(body['previous'])
    if body['version']==2:_require(body['previous'] is None)
    if 'use_contract' in body:validate(body['use_contract'])
    if 'birth_handoff' in body:
        from runtime import evidence
        evidence._birth_handoff_shape(body['birth_handoff'],body['identity'])


def _history_dependencies(body, authority):
    rows = [dict(kind='task', reference=row['reference']) for row in body['tasks']]
    rows.extend(dict(kind='assignment', reference=row) for row in body['assignments'])
    rows.extend(dict(kind='artifact', reference=row) for row in body['artifacts'])
    if body['previous']:
        rows.append(dict(kind='recovery', reference=dict(authority=authority, artifact=body['previous'])))
    origin=body.get('birth_handoff',{}).get('origin_source')
    if origin is not None:
        edge=dict(kind='recovery',reference=origin)
        if edge not in rows:rows.append(edge)
    for row in rows: _dependency(row)
    _require(len(rows) <= MAX_ROWS)
    return rows


def _history_pointer(edge):
    if edge['kind'] == 'task': return None
    ref = edge['reference']
    return dict(authority=ref['authority'], artifact=ref['assignment']) if edge['kind']=='assignment' else ref


def _history_walk(head, metadata, *, unresolved=None, reached_unresolved=None):
    """Account for every known parent; only explicit absent frontier keys may defer traversal."""
    if unresolved is not None:
        _require(type(unresolved) is set and len(unresolved)<=MAX_ROWS
            and type(reached_unresolved) is set and reached_unresolved<=unresolved,
            'Invalid recovery history unresolved frontier')
        _require(type(metadata) is dict and len(metadata)<=MAX_ROWS
            and (not isinstance(head,list) or len(head)<=MAX_ROWS),'Recovery partial history bound exceeded')
        observed_edges=0
        for name in unresolved:
            try:pointer=json.loads(name)
            except (TypeError,ValueError):raise UseContractError('Invalid recovery history unresolved parent') from None
            _pointer(pointer);_require(key(pointer)==name,'Noncanonical recovery history unresolved parent')
    else:_require(reached_unresolved is None,'Recovery history frontier needs explicit unresolved parents')
    if head is None:
        _require(not metadata, 'Recovery history has extra observed parents')
        return []
    colors = {}; order = []; pending = [(pointer, False) for pointer in head] if isinstance(head,list) else [(head,False)]
    while pending:
        pointer, leaving = pending.pop(); name = key(pointer); _pointer(pointer)
        if leaving:
            colors[name] = 2
            continue
        _require(colors.get(name) != 1, 'Recovery history graph is cyclic')
        if colors.get(name) == 2: continue
        if name not in metadata and unresolved is not None and name in unresolved:
            reached_unresolved.add(name);continue
        _require(name in metadata, 'Recovery history is missing an observed parent')
        _require(len(colors) < MAX_ROWS, 'Recovery history parent bound exceeded')
        colors[name] = 1; order.append(pointer); pending.append((pointer, True))
        rows = metadata[name]
        _require(type(rows) in (list, tuple))
        if unresolved is not None:
            observed_edges+=len(rows)
            _require(observed_edges<=MAX_ROWS,'Recovery partial history edge bound exceeded')
        _require(len({key(row) for row in rows}) == len(rows), 'Recovery history has duplicate typed edges')
        for row in rows:
            _dependency(row); child = _history_pointer(row)
            if child is not None: pending.append((child, False))
    return order


def _append_history_occurrences(edges, roots, anchor, starts, history_metadata, budget, *,
        unresolved=None, reached_unresolved=None):
    """Append history rows only; copying, validation and publication remain with the caller."""
    if unresolved is not None or reached_unresolved is not None:
        budget.take(bytes=2)
        _history_walk([],history_metadata,unresolved=unresolved,reached_unresolved=reached_unresolved)
    for start in {key(pointer):pointer for pointer in starts}.values():
        index = roots.index(anchor['root'])
        seed = dict(root=index,parent=None,dependency=dict(kind='recovery',reference=start),purpose='retention-only')
        budget.take(edges=1); budget.retain(seed); edges.append(seed)
        for parent in _history_walk(start,history_metadata,unresolved=unresolved,reached_unresolved=reached_unresolved):
            for dependency in history_metadata[key(parent)]:
                row = dict(root=index,parent=parent,dependency=dependency,purpose='retention-only')
                budget.take(edges=1); budget.retain(row); edges.append(row)
                _require(len(edges)<=MAX_ROWS, 'Recovery history use occurrence bound exceeded')


def _history_obligations(program, anchors, edges):
    supports={program['roots'].index(row['root']):set(map(key,row['support'])) for row in anchors}
    obligations = []
    for row in edges:
        waiver = row['purpose']=='retention-only' and key(row['dependency']) in supports.get(row['root'],set())
        obligations.append(dict(row,requirement='validated-stop-support' if waiver else 'current-permission'))
    return obligations


def compare_issued_program(program, *, direct, payload_direct, payload_metadata, history_metadata,
        starts, anchors, legacy_routes, pending_jobs, pending_parents, budget):
    """Compare acquired occurrences; owner authenticates inputs and already charges enqueued jobs."""
    _require(isinstance(budget,CaptureBudget),'Issued comparison needs its shared capture budget')
    budget.retain(program);validate(program)
    for rows in (direct,payload_direct,starts,anchors,legacy_routes,pending_jobs,pending_parents):
        _require(type(rows) is list and len(rows)<=MAX_ROWS,'Issued comparison row bound exceeded')
        budget.retain(rows)
    for rows in (direct,payload_direct):
        for row in rows:_dependency(row)
        _require(len({key(row) for row in rows})==len(rows),'Issued direct dependencies are duplicated')
    budget.take(parents=len(starts)+len(pending_parents))
    for pointer in starts:_pointer(pointer)
    for pointer in pending_parents:_pointer(pointer)
    budget.take(bytes=128+64*(len(pending_parents)+len(pending_jobs)))
    unresolved={key(pointer) for pointer in pending_parents}
    _require(len(unresolved)==len(pending_parents),'Issued pending parent is duplicated')
    for job in pending_jobs:
        _require(type(job) is dict and bool(job),'Issued pending job is invalid');key(job);_text(job.get('kind'))
        if job['kind']=='managed-source':
            _fields(job,('kind','reference'));_pointer(job['reference']);unresolved.add(key(job['reference']))
    for name in unresolved:budget.retain(name)
    validate(dict(version=1,roots=[],edges=[],legacy_routes=legacy_routes))
    _require(program['legacy_routes']==legacy_routes,'Issued legacy inventory differs')
    roots=program['roots'];seen_anchors=set()
    for anchor in anchors:
        _fields(anchor,('root','support'));root=anchor['root']
        _require(root in roots and root['purpose']=='historical-stop','Issued anchor has no actual historical root')
        allowed=[root['reference'],dict(kind='artifact',reference=root['proof'])]
        _require(type(anchor['support']) is list and len(anchor['support'])==2
            and set(map(key,anchor['support']))==set(map(key,allowed)),'Issued anchor support differs')
        name=key(root);_require(name not in seen_anchors,'Issued anchor is duplicated');seen_anchors.add(name)
    for graph in (payload_metadata,history_metadata):
        _require(type(graph) is dict and len(graph)<=MAX_ROWS,'Issued parent bound exceeded')
        budget.retain(graph);total=0
        for name,rows in graph.items():
            try:pointer=json.loads(name)
            except (TypeError,ValueError):raise UseContractError('Invalid issued metadata parent') from None
            _pointer(pointer);_require(key(pointer)==name,'Noncanonical issued metadata parent')
            _require(type(rows) in (list,tuple),'Issued parent dependencies are invalid')
            total+=len(rows);_require(total<=MAX_ROWS,'Issued metadata edge bound exceeded')
            for row in rows:
                _dependency(row);child=_history_pointer(row)
                _require(child is None or key(child) in graph or key(child) in unresolved,
                    'Issued graph has an unreported missing parent')
            _require(len({key(row) for row in rows})==len(rows),'Issued parent dependencies are duplicated')
        budget.take(parents=len(graph),edges=total)
        # Inspect every acquired component, including components beyond a missing frontier.
        budget.retain(graph);budget.take(parents=len(graph),bytes=64*len(graph))
        _history_walk([json.loads(name) for name in graph],graph,
            unresolved=unresolved,reached_unresolved=set())
    for name in set(payload_metadata)&set(history_metadata):
        _require(payload_metadata[name]==history_metadata[name],'Issued payload/history metadata differs')
    for row in program['edges']:
        if row['parent'] is None:continue
        name=key(row['parent']);known=payload_metadata.get(name,history_metadata.get(name))
        _require(known is not None or name in unresolved,'Issued occurrence has an unreported parent')
        if known is not None:
            _require(row['dependency'] in known,'Issued occurrence differs from actual parent metadata')
    budget.retain(payload_direct);budget.retain(direct);budget.retain(starts)
    payload_keys={key(row) for row in payload_direct}
    start_keys={key(dict(kind='recovery',reference=pointer)) for pointer in starts}
    direct_keys={key(row) for row in direct}
    _require(direct_keys==payload_keys|start_keys,'Issued direct payload/history coverage differs')
    _require({key(row['dependency']) for row in program['edges'] if row['parent'] is None}==direct_keys,
        'Issued program direct coverage differs')
    for row in payload_direct:
        pointer=_history_pointer(row)
        _require(pointer is None or key(pointer) in payload_metadata or key(pointer) in unresolved,
            'Issued payload has an unreported direct parent')
    for pointer in starts:
        _require(key(pointer) in history_metadata or key(pointer) in unresolved,
            'Issued history has an unreported start parent')
    budget.retain(payload_direct)
    payload_starts=[pointer for row in payload_direct if (pointer:=_history_pointer(row)) is not None]
    frontiers=[]
    for heads,graph in ((payload_starts,payload_metadata),(starts,history_metadata)):
        budget.retain(graph);budget.take(parents=len(graph),bytes=64*len(graph))
        frontier=set();observed=_history_walk(heads,graph,unresolved=unresolved,reached_unresolved=frontier)
        if not frontier:
            _require({key(pointer) for pointer in observed}==set(graph),'Issued graph has extra observed parents')
        frontiers.append(frontier)
    _require(not starts or anchors,'Issued history lacks independently validated anchors')
    selected=min(anchors,key=lambda row:key(row['root'])) if anchors else None
    generated=[]
    budget.retain(history_metadata);budget.take(bytes=64*len(history_metadata))
    _append_history_occurrences(generated,roots,selected,starts,history_metadata,budget,
        unresolved=unresolved,reached_unresolved=frontiers[1])
    budget.retain(generated);budget.retain(program['edges'])
    actual={key(row) for row in program['edges']};generated_keys={key(row) for row in generated}
    _require(generated_keys<=actual,'Issued program lost generated history occurrences')
    by_parent={}
    for row in program['edges']:
        parent=(row['root'],key(row['parent']));budget.retain(parent);budget.take(edges=1)
        by_parent.setdefault(parent,[]).append(row)
    payload={};pending=[];seen=set();payload_frontier=set()
    def include(row):
        name=key(row)
        if name in payload:return
        budget.take(edges=1);budget.retain(row);payload[name]=row
        pointer=_history_pointer(row['dependency'])
        if pointer is not None:
            budget.retain(pointer);pending.append((row['root'],pointer,row['purpose']))
    for row in program['edges']:
        if row['parent'] is None and key(row['dependency']) in payload_keys:include(row)
    while pending:
        root,parent,purpose=pending.pop();name=key(parent);state=(root,name,purpose)
        if state in seen:continue
        budget.take(parents=1);budget.retain(state);seen.add(state)
        if name not in payload_metadata:
            _require(name in unresolved,'Issued payload parent is unobserved')
            budget.retain(state);payload_frontier.add((root,purpose));continue
        budget.retain(payload_metadata[name])
        expected={key(row) for row in payload_metadata[name]};covered=set()
        for row in by_parent.get((root,name),()):
            if purpose=='content' and row['purpose']!='content':continue
            dependency=key(row['dependency'])
            _require(dependency in expected,'Issued payload has an unsupported child')
            covered.add(dependency);include(row)
        _require(covered==expected,'Issued payload lost rooted purpose coverage')
    for row in payload.values():
        if row['parent'] is None:continue
        _require(any(other['parent'] is None and other['root']==row['root']
            and other['dependency']==row['dependency'] and other['purpose']==row['purpose']
            for other in payload.values()),'Issued payload lost its flattened purpose')
    budget.take(bytes=64+2*len(payload))
    for name in payload:budget.retain(name)
    ordered=[payload[name] for name in sorted(payload)]
    budget.retain(roots);budget.retain(ordered);budget.retain(legacy_routes)
    view=dict(version=1,roots=roots,edges=ordered,legacy_routes=legacy_routes)
    if not frontiers[0]:check_coverage(view,payload_direct,payload_metadata)
    selected_index=roots.index(selected['root']) if selected else None
    for row in program['edges']:
        if key(row) in payload or key(row) in generated_keys:continue
        possible_history=bool(frontiers[1]) and row['root']==selected_index and row['purpose']=='retention-only'
        possible_payload=any(root==row['root'] and (purpose=='retention-only' or row['purpose']=='content')
            for root,purpose in payload_frontier)
        if possible_payload and not possible_history:
            _require(any(other['parent'] is None and other['root']==row['root']
                and other['dependency']==row['dependency'] and other['purpose']==row['purpose']
                for other in program['edges']),'Issued deferred payload lost its flattened purpose')
        _require(row['parent'] is not None and (possible_history or possible_payload),
            'Issued program has an unsupported occurrence')
    if pending_jobs or pending_parents:
        budget.retain(pending_jobs);budget.retain(pending_parents);budget.take(bytes=128)
        return dict(comparison_only=True,pending_jobs=deepcopy(pending_jobs),
            pending_parents=deepcopy(pending_parents),obligations=None)
    budget.retain(program['edges']);budget.retain(anchors)
    budget.take(edges=len(program['edges']),bytes=128+64*len(program['edges']))
    obligations=_history_obligations(program,anchors,program['edges'])
    return dict(comparison_only=True,pending_jobs=[],pending_parents=[],obligations=obligations)


def project_history(inputs, created_at, budget):
    """Pure, incomplete-by-design candidate comparison; never a registration or permission."""
    _require(isinstance(budget, CaptureBudget), 'Recovery history needs its shared capture budget')
    _require(type(created_at) is int and created_at >= 0)
    authority = inputs['authority']; _authority(authority)
    template = inputs['template']; _history_body(template); identity = inputs['identity']; _identity(identity)
    _require(template['identity'] == identity and authority in template['authorities'] and template['previous'] is None,
        'Recovery candidate differs from captured identity or authority')
    _require(template['version'] in (1, 2) and template['kind']=='d4-recovery-source')
    _require('use_contract' in template,'History comparison requires its actual current root program')
    program = validate(template['use_contract'])
    direct = _history_dependencies(template, authority)
    birth_origin=template.get('birth_handoff',{}).get('origin_source')
    origin_edge=dict(kind='recovery',reference=birth_origin) if birth_origin is not None else None
    payload_direct=[edge for edge in direct if edge!=origin_edge]
    head = inputs['reference']; history = inputs['history']; baseline = inputs['baseline']
    _require(type(history) is list and len(history)<=MAX_ROWS)
    _require(type(inputs['anchors']) is list and len(inputs['anchors'])<=MAX_ROWS)
    for graph in (inputs['metadata'],inputs['history_metadata']):
        _require(type(graph) is dict and len(graph)<=MAX_ROWS)
        _require(all(type(rows) in (list,tuple) for rows in graph.values()))
        _require(sum(len(rows) for rows in graph.values())<=MAX_ROWS,'Recovery observed edge bound exceeded')
    heads = [row['reference']['artifact'] for row in baseline['dependencies']
        if row['kind']=='recovery' and row['reference']['identity']==identity]
    _require(len(heads)<=1 and head==(heads[0] if heads else None), 'Recovery previous differs from captured baseline')
    _require((head is None and not history) or (history and history[0]['reference']==head), 'Recovery previous head differs')
    expected = head; legacy = {}; restricted = set()
    def retain_legacy(row):
        name = key(row['origin'])
        if name in legacy:
            _require(legacy[name]==row, 'Conflicting legacy route origin')
            return
        _require(len(legacy)<MAX_ROWS,'Recovery merged legacy origin bound exceeded')
        budget.take(bytes=len(name.encode())); budget.retain(row)
        legacy[name] = deepcopy(row)
    for item in history:
        body = item['content']; _history_body(body); metadata = item['metadata']; ref = item['reference']; _artifact(ref)
        _require(ref == expected and body['identity']==identity and authority in body['authorities'], 'Recovery history chain, identity or authority differs')
        _require(metadata['created_at']==created_at and metadata['owner']==dict(kind='project',
            project_id=baseline['content']['project_id'],task=None) and metadata['class']=='compact',
            'Recovery history original owner or age differs')
        dependencies = _history_dependencies(body, authority)
        _require(metadata['dependencies']==dependencies, 'Recovery history typed metadata differs')
        pointer = dict(authority=authority,artifact=ref)
        _require(inputs['history_metadata'].get(key(pointer))==[qualify_dependency(row,authority) for row in dependencies],
            'Recovery captured head metadata differs')
        budget.retain((authority,ref,identity,body['journal']['sha256']))
        origin = dict(authority=authority,artifact=ref,identity=identity,journal_sha256=body['journal']['sha256'])
        if 'use_contract' in body:
            contract = validate(body['use_contract']); restricted.add(key(origin))
            rows = contract['legacy_routes']
        else:
            _require(key(origin) in legacy or len(legacy)<MAX_ROWS,'Recovery merged legacy origin bound exceeded')
            # The old helper stays unchanged; reserve its generated facts before it copies them.
            budget.retain(dict(origin=None,destinations=None))
            budget.retain(origin); budget.retain(body['authorities'])
            rows = [legacy_projection(origin, body)]
        for row in rows: retain_legacy(row)
        expected = body['previous']
    _require(expected is None, 'Recovery previous chain is incomplete')
    _require(not (set(legacy) & restricted), 'Recovery origin cannot be both legacy and restricted')
    for row in program['legacy_routes']:
        _require(key(row['origin']) in legacy and legacy[key(row['origin'])]==row,
            'Recovery candidate has an unobserved legacy origin')
    jobs = inputs['jobs']
    _require(type(jobs) is list and len(jobs)<=MAX_ROWS)
    if jobs:
        budget.retain(jobs)
        return dict(comparison_only=True,content=None,reference=None,created_at=created_at,
            pending_jobs=deepcopy(jobs),obligations=[])
    check_coverage(program, payload_direct, inputs['metadata'])
    payload = [pointer for edge in payload_direct if (pointer:=_history_pointer(edge)) is not None]
    observed_payload = _history_walk(payload,inputs['metadata'])
    _require({key(row) for row in observed_payload}==set(inputs['metadata']), 'Recovery payload has extra observed parents')
    pointer = dict(authority=authority,artifact=head) if head else None
    order = _history_walk(pointer,inputs['history_metadata'])
    _require({key(row) for row in order} == set(inputs['history_metadata']), 'Recovery history has extra observed parents')
    for name in set(inputs['metadata']) & set(inputs['history_metadata']):
        _require(inputs['metadata'][name]==inputs['history_metadata'][name], 'Recovery payload/history metadata differs')
    anchors = inputs['anchors']; seen_anchors=set()
    for row in anchors:
        _fields(row,('root','support'))
        _require(row['root'] in program['roots'] and row['root']['purpose']=='historical-stop',
            'Recovery history has an unjustified anchor')
        allowed=[row['root']['reference'],dict(kind='artifact',reference=row['root']['proof'])]
        _require(type(row['support']) is list and len(row['support'])==2 and set(map(key,row['support']))==set(map(key,allowed)),
            'Recovery historical support is not exact')
        name=key(row['root']);_require(name not in seen_anchors,'Recovery history has duplicate anchors');seen_anchors.add(name)
    if (head and template['version']==1) or birth_origin is not None:
        _require(anchors, 'Recovery history has no independently justified historical anchor')
    anchor = min(anchors,key=lambda row:key(row['root'])) if anchors else None
    def candidate(previous, observed_at=None):
        _require(len(key(template).encode())<=MAX_BYTES,'Recovery candidate whole-body byte bound exceeded')
        budget.retain(template); budget.take(edges=len(program['edges']))
        body = deepcopy(template); body['previous'] = previous
        if observed_at is not None: body['journal']['observed_at'] = observed_at
        edges = deepcopy(program['edges']); roots = program['roots']
        starts=([dict(authority=authority,artifact=previous)] if previous else [])+([birth_origin] if birth_origin is not None else [])
        _append_history_occurrences(edges, roots, anchor, starts, inputs['history_metadata'], budget)
        # Reserve every merged contract copy before constructing its list or canonical form.
        budget.retain(roots); budget.retain(edges)
        budget.take(bytes=2+max(0,len(legacy)-1))
        for row in legacy.values(): budget.retain(row)
        body['use_contract'] = build(roots,edges,list(legacy.values()))
        _require(len(key(body).encode())<=MAX_BYTES,'Recovery candidate whole-body byte bound exceeded')
        dependencies = _history_dependencies(body,authority)
        actual = {key(row['dependency']) for row in body['use_contract']['edges'] if row['parent'] is None}
        _require(actual=={key(row) for row in dependencies},'Recovery history direct coverage differs')
        return body,dependencies
    reused = None
    if history:
        prior = history[0]['content']
        replay,deps = candidate(prior['previous'] if template['version']==1 else None,prior['journal']['observed_at'])
        if replay==prior:
            _require(deps==history[0]['metadata']['dependencies'],'Recovery replay metadata differs')
            reused = head
    if reused is None: body,dependencies = candidate(head if template['version']==1 else None)
    else: body,dependencies = replay,deps
    budget.retain(body)
    obligations=_history_obligations(program,anchors,body['use_contract']['edges'])
    return dict(comparison_only=True,content=body,reference=reused,dependencies=dependencies,created_at=created_at,
        pending_jobs=[],obligations=obligations)


def check_history_coverage(candidate, created_at, inputs, budget):
    """Recompute every payload/history occurrence; caller labels cannot exempt a parent."""
    result = project_history(inputs,created_at,budget)
    _require(result['content'] is not None and candidate==result['content'], 'Recovery history candidate or rooted coverage differs')
    return candidate
