"""Bounded local selection guidance; coordinator assertions grant no route authority."""
from functools import partial
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlsplit
import uuid

from runtime import evidence, state
from runtime.startup import MAX_INPUT, StartupError, json_object


GUIDANCE_VERSION = 1
MAX_GUIDANCE = 64 * 1024
GUIDANCE_LIMITATIONS = [
    "Source verification and recommendations are coordinator assertions, not independently verified facts",
    "Cached recommendations establish neither current model availability nor execution authority",
]


def _now():
    return time.time()


def _require(condition, message):
    if not condition:
        raise state.StateError(message)


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _applicability(value):
    state._fields(value, ("client_version", "capability_fingerprint"))
    state._text(value["client_version"], "guidance client version", 128)
    _require(_hex(value["capability_fingerprint"]), "Invalid guidance capability fingerprint")


def _compatible_applicability(left, right):
    return left["capability_fingerprint"] == right["capability_fingerprint"]


def _common(request, required=(), optional=()):
    evidence._bounded(request, MAX_GUIDANCE)
    state._fields(request, ("client_surface", "applicability", *required), optional)
    _require(isinstance(request["client_surface"], str) and
             re.fullmatch(r"[a-z][a-z0-9-]{0,63}", request["client_surface"]), "Invalid guidance client surface")
    _applicability(request["applicability"])


def _strings(value, field, maximum=16, limit=512, nonempty=False):
    _require(isinstance(value, list) and len(value) <= maximum and (value or not nonempty), "Invalid " + field)
    _require(all(isinstance(item, str) for item in value) and len(set(value)) == len(value), "Invalid " + field)
    for item in value:
        state._text(item, field, limit)


def _checked(value):
    _require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value),
             "Invalid guidance checked time")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError):
        raise state.StateError("Invalid guidance checked time") from None
    _require(0 <= timestamp <= _now(), "Guidance checked time is in the future or invalid")


def _guidance(value, request):
    evidence._bounded(value, MAX_GUIDANCE)
    state._fields(value, ("schema_version", "client_surface", "checked_at", "applicability", "sources", "recommendations"))
    _require(type(value["schema_version"]) is int and value["schema_version"] == GUIDANCE_VERSION,
             "Unsupported guidance schema")
    _applicability(value["applicability"])
    _require(value["client_surface"] == request["client_surface"] and
             _compatible_applicability(value["applicability"], request["applicability"]), "Guidance applicability changed")
    _checked(value["checked_at"])
    sources = value["sources"]
    _require(isinstance(sources, list) and 1 <= len(sources) <= 16, "Invalid guidance sources")
    ids = set()
    for source in sources:
        state._fields(source, ("id", "url", "sha256"))
        state._text(source["id"], "guidance source id", 64)
        _require(source["id"] not in ids and _hex(source["sha256"]), "Invalid guidance source identity or digest")
        ids.add(source["id"])
        state._text(source["url"], "guidance source URL", 2048)
        try:
            url = urlsplit(source["url"])
            _require(url.scheme == "https" and url.hostname and not url.username and not url.password and
                     not url.fragment and url.port in (None, 443), "Invalid guidance source URL")
        except ValueError:
            raise state.StateError("Invalid guidance source URL") from None
    profiles = value["recommendations"]
    _require(isinstance(profiles, list) and len(profiles) <= 32, "Invalid guidance recommendations")
    seen = set()
    for profile in profiles:
        state._fields(profile, ("id", "work", "risk", "models", "conditions", "limitations", "source_ids"))
        state._text(profile["id"], "guidance profile id", 64)
        _require(profile["id"] not in seen, "Duplicate guidance profile")
        seen.add(profile["id"])
        for key in ("work", "risk"):
            state._text(profile[key], "guidance " + key, 1024)
        for key in ("conditions", "limitations", "source_ids"):
            _strings(profile[key], "guidance " + key, nonempty=key == "source_ids")
        _require(set(profile["source_ids"]) <= ids, "Unknown guidance source reference")
        models = profile["models"]
        _require(isinstance(models, list) and len(models) <= 16, "Invalid guidance models")
        names = set()
        for model in models:
            state._fields(model, ("model", "efforts"))
            state._text(model["model"], "guidance model", 128)
            _require(model["model"] not in names, "Duplicate guidance model")
            names.add(model["model"])
            _strings(model["efforts"], "guidance efforts", maximum=16, limit=64)


def _directory(scope, create):
    with state._database(scope) as db:
        _require(db is not None, 'Guidance requires current canonical task authority')
    parent = state._path(scope).parent
    if create:
        missing = []
        path = parent
        while not path.exists():
            missing.append(path)
            path = path.parent
        for path in reversed(missing):
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                state._private(path, True)
    elif not parent.exists():
        return None
    state._private(parent, True)
    folder = parent / "model-guidance"
    if create:
        try:
            folder.mkdir(mode=0o700)
        except FileExistsError:
            pass
    elif not os.path.lexists(folder):
        return None
    state._private(folder, True)
    return folder


def _read_bytes(path):
    if not os.path.lexists(path):
        return None
    state._private(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                 info.st_nlink == 1 and info.st_mode & 0o077 == 0, "Unsafe guidance file")
        return stream.read(MAX_GUIDANCE + 1)


def _atomic(path, value, guard=None):
    raw = evidence._raw(value)
    _require(len(raw) <= MAX_GUIDANCE, "Guidance file size limit exceeded")
    temporary = path.parent / (uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if guard is not None:
            guard()
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _control(folder, surface):
    raw = _read_bytes(folder / (surface + ".control.json"))
    if raw is None:
        return dict(generation=0, claim=None, last_attempt=None)
    try:
        _require(len(raw) <= MAX_GUIDANCE, "Oversized guidance control")
        value = json_object(raw)
        state._fields(value, ("generation", "claim", "last_attempt"))
        state._integer(value["generation"], "guidance generation")
        claim = value["claim"]
        if claim is not None:
            state._fields(claim, ("token", "generation", "applicability", "prior", "trigger", "expires_at"))
            _require(isinstance(claim["token"], str) and re.fullmatch(r"[0-9a-f]{32}", claim["token"]), "Invalid guidance token")
            _require(type(claim["generation"]) is int and claim["generation"] == value["generation"], "Invalid guidance generation")
            _applicability(claim["applicability"])
            _require(claim["prior"] == "missing" or _hex(claim["prior"]), "Invalid prior guidance digest")
            _trigger(claim["trigger"])
            _require(type(claim["expires_at"]) in (int, float) and 0 < claim["expires_at"] < float("inf"),
                     "Invalid guidance claim expiry")
        attempt = value["last_attempt"]
        if attempt is not None:
            state._fields(attempt, ("key", "trigger", "outcome", "reason", "coordinator_ref"))
            _require(_hex(attempt["key"]) and attempt["outcome"] in ("verified", "failure"), "Invalid guidance attempt")
            _trigger(attempt["trigger"])
            state._text(attempt["reason"], "guidance outcome", 1024)
            state._text(attempt["coordinator_ref"], "guidance coordinator reference", 1024)
        return value
    except (ValueError, TypeError, RecursionError):
        raise state.StateError("Guidance control is corrupt") from None


@contextmanager
def _locked(scope, request, create=False):
    try:
        folder = _directory(scope, create)
        if folder is None:
            yield None, None
            return
        lock = folder / (request["client_surface"] + ".lock")
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            state._private(lock)
            deadline = time.monotonic() + 2
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    _require(time.monotonic() < deadline, "Guidance cache lock is busy")
                    time.sleep(0.005)
            yield folder, _control(folder, request["client_surface"])
        finally:
            os.close(descriptor)
    except OSError:
        raise state.StateError("Guidance cache is unavailable, unsafe or locked") from None


def _snapshot(scope, folder, request, *, context=None, db=None):
    raw = _read_bytes(folder / (request["client_surface"] + ".json")) if folder else None
    prior = hashlib.sha256(raw).hexdigest() if raw is not None else "missing"
    if raw is None:
        return None, None, prior
    try:
        _require(len(raw) <= MAX_GUIDANCE, "Oversized guidance snapshot")
        stored = json_object(raw)
        state._fields(stored, ("guidance", "snapshot_ref"))
        guidance = stored["guidance"]
        _require(isinstance(guidance, dict), "Invalid guidance snapshot")
        _guidance(guidance, dict(client_surface=request["client_surface"], applicability=guidance.get("applicability")))
        actual=(evidence._read(scope,stored['snapshot_ref']) if context is None else
            evidence._read(None,stored['snapshot_ref'],context=context,db=db))
        _require(actual == guidance, "Guidance snapshot evidence changed")
        return guidance, stored["snapshot_ref"], prior
    except (state.StateError, ValueError, TypeError, RecursionError):
        return None, None, prior


def _closure_guidance_cache_inputs(context,db,surfaces):
    """Read only actual local cache files and their managed snapshot on this connection."""
    folder=context.directory/'model-guidance';files={};references=[]
    if folder.exists():
        state._private(folder,True);entries=list(folder.iterdir());_require(len(entries)<=4096,'Guidance cache source bound exceeded')
        surfaces.update(path.name[:-5] for path in entries if path.name.endswith('.json') and not path.name.endswith('.control.json'))
    if not surfaces:return dict(references=[],files={},folder=None)
    state._private(folder,True);folder_binding=evidence._binding(folder)
    for surface in sorted(surfaces):
        _require(re.fullmatch(r'[a-z][a-z0-9-]{0,63}',surface),'Invalid original guidance surface')
        paths=[folder/(surface+suffix) for suffix in ('.json','.control.json')]
        _require(all(os.path.lexists(path) for path in paths),'Current guidance cache/control is missing')
        before={str(path):evidence._closure_file_stat(path) for path in paths}
        raw={str(path):_read_bytes(path) for path in paths}
        control=_control(folder,surface)
        _require(control['claim'] is None and control['last_attempt'] is not None,'Guidance refresh remains incomplete')
        current,reference,_=_snapshot(None,folder,dict(client_surface=surface),context=context,db=db)
        _require(current is not None and reference is not None,'Current guidance cache is missing or corrupt')
        _require(control['generation']>0,'Current guidance control generation is unavailable')
        # Failed refresh keys bind attempted applicability, not the preserved historical snapshot.
        if control['last_attempt']['outcome']=='verified':
            _require(control['last_attempt']['key'] in _keys(dict(applicability=current['applicability']),
                hashlib.sha256(raw[str(paths[0])]).hexdigest(),True),'Current guidance control/source correspondence changed')
        _require(all(evidence._closure_file_stat(path)==before[str(path)] and _read_bytes(path)==raw[str(path)] for path in paths)
            and evidence._binding(folder)==folder_binding,'Guidance cache changed during owner observation')
        references.append(reference)
        for path in paths:
            files[str(path)]=dict(stat=before[str(path)],digest=hashlib.sha256(raw[str(path)]).hexdigest())
    return dict(references=references,files=files,folder=dict(path=str(folder),binding=folder_binding))


def _closure_guidance_inputs(context,db,records):
    """Local comparison facts only; linked and stopped obligations remain for assembly."""
    evidence._check_context(context,db)
    authority=evidence._canonical_plain(context.authority_set[0]);bodies={};metadata={};markers={};assignments={};surfaces=set()
    def fetch(reference):
        key=evidence._raw(reference).decode()
        if key not in bodies:
            bodies[key]=evidence._read(None,reference,context=context,db=db)
            metadata[reference['name']]=state._metadata(db,reference['name'])
        return bodies[key]
    def native(body,discovery=True):
        observed=fetch(body['discovery']) if discovery else body['discovery']['content']
        guidance=fetch(body['guidance']['snapshot_ref']);surface=body['binding']['client_surface'];surfaces.add(surface)
        _guidance(guidance,dict(client_surface=surface,applicability=dict(client_version=observed['native_version'],
            capability_fingerprint=observed['capability_digest'])))
    records=evidence._canonical_plain(records)
    for record in records:
        if record['data']['role'] in NATIVE_ROLES:
            _require(fetch(record['data']['assignment'])==record['body'],'Native guidance assignment body changed')
            native(record['body'])
    capture=state._CAPTURE_SQL_SCOPE.get()
    if capture is not None:
        resources,escrow=capture
        _require(escrow is None,'Guidance metadata cannot borrow exit-validation allowance')
        state._capture_registered(resources,db);acquisition=resources.current_acquisition()
        _require(db.in_transaction,'Guidance metadata requires its existing transaction')
        acquisition.reserve(requested=512,live=2048)
        try:
            measured=db.execute(
                'select count(*),coalesce(sum(coalesce(length(cast(name as blob)),0)+'
                'coalesce(length(cast(digest as blob)),0)+coalesce(length(cast(owner_key as blob)),0)+'
                'coalesce(length(cast(class as blob)),0)+coalesce(length(cast(created_at as blob)),0)+'
                'coalesce(length(cast(closed_at as blob)),0)+coalesce(length(cast(status as blob)),0)+'
                'coalesce(length(cast(bytes as blob)),0)+coalesce(length(cast(data as blob)),0)),0) from '
                "(select name,digest,owner_key,class,created_at,closed_at,status,bytes,data from evidence_metadata where class='compact' and status='live' order by name limit 4097)").fetchone()
            _require(type(measured) is tuple and len(measured)==2 and
                all(type(value) is int and value>=0 for value in measured) and measured[0]<=4097,
                'Guidance metadata measurement overflow or invalid scalar')
            count,raw_bytes=measured
            _require(count<=4096,'Retained guidance ownership support exceeds bound')
            material=4*raw_bytes+128*9*count
            # Returned native rows and exception aliases stay charged until owner settlement.
            acquisition.reserve(requested=material,live=4*material+512*9*count)
        except BaseException as error:acquisition.fail(error);raise
    rows=db.execute("select name,digest,owner_key,class,created_at,closed_at,status,bytes,data from evidence_metadata where class='compact' and status='live' order by name limit 4097").fetchall()
    _require(len(rows)<=4096,'Retained guidance ownership support exceeds bound')
    owned=[];successors=set()
    for row in rows:
        actual=state._metadata_record(row);owner=actual['owner'].get('task')
        if owner is None:continue
        _require(owner['authority']==authority,'Foreign retained guidance owner remains protected')
        reference=dict(name=row[0],digest=row[1]);compact=fetch(reference);key=evidence._raw(owner).decode()
        markers[key]=state._retired(db,'task',{field:owner[field] for field in ('scope_id','client','session_id','task_id')})
        owned.append((owner,compact))
        if compact.get('kind')=='retained-task-closure':successors.add(key)
    for owner,compact in owned:
        if markers[evidence._raw(owner).decode()] is None:continue
        kind=compact.get('kind')
        if kind=='task-closure' and evidence._raw(owner).decode() not in successors:
            for certificate in compact.get('stopped',{}).get('assignments',[]):
                ref=certificate['reference'];assignments[ref['id']]=state._retired_assignment(db,ref['id'])
                if certificate['role'] in NATIVE_ROLES:native(fetch(ref['assignment']))
            for original in compact.get('scope_guidance',[]):
                guidance=fetch(original['reference'])
                _guidance(guidance,dict(client_surface=guidance.get('client_surface'),applicability=guidance.get('applicability')))
        elif kind=='retained-task-closure':
            for receipt in [compact['original'],*compact['release']]:
                name=receipt['reference']['name'];metadata[name]=state._metadata(db,name)
            for original in compact['native_sources']:native(original,discovery=False)
            for original in compact['scope_guidance']:
                guidance=fetch(original['reference'])
                _guidance(guidance,dict(client_surface=guidance.get('client_surface'),applicability=guidance.get('applicability')))
    cache=_closure_guidance_cache_inputs(context,db,surfaces)
    for reference in cache['references']:fetch(reference)
    return dict(authority=authority,directory=str(context.directory),records=records,rows=rows,
        bodies=bodies,metadata=metadata,markers=markers,assignment_markers=assignments,cache=cache)


def _closure_guidance_job(job):
    """An unanswered or mismatched obligation never produces completed guidance."""
    response=yield job
    _require(isinstance(response,dict) and set(response)=={'job','result'} and response['job']==job,
        'Unresolved guidance validation job')
    return response['result']


def _resolve_closure_guidance_job(context,db,job,*,read_context=None,read_roster=None):
    """Normal wrapper's full validators; this is not the authority-local input seam."""
    from runtime import changes
    if read_context is not None:evidence._check_linked_read_context(read_context,evidence._canonical_plain(read_context.pointer),writer=(context,db))
    if read_roster is not None:evidence._check_linked_read_roster(read_roster,evidence._canonical_plain(read_roster.pointer))
    kind=job['kind']
    if kind=='bookkeeping':result=evidence._bookkeeping_record(context,db,job['reference'],job['body'])
    elif kind=='retained-owner':result=evidence._retained_for_owner(context,db,job['owner'])
    elif kind=='linked-component':
        basis=job['basis']
        selected=evidence._matching_linked_read_context(read_context,basis)
        read_roster=evidence._matching_linked_read_roster(read_roster,basis)
        result=evidence._linked_retained_component(evidence._linked_basis_root(basis),evidence._linked_read_authorities(selected,basis,writer=(context,db),read_roster=read_roster),writer=(context,db))
    elif kind=='stopped-certificate':result=changes._check_stopped_certificate(context,db,job['certificate'],read_context=read_context,read_roster=read_roster)
    else:raise state.StateError('Unknown guidance validation job')
    return evidence._finish_linked_read_context(dict(job=job,result=result),read_context,read_roster=read_roster)


def _closure_guidance(context,db,records,*,read_context=None,read_roster=None):
    """Preserve full normal validation; pending local inputs never authorize a closure."""
    inputs=_closure_guidance_inputs(context,db,records)
    assembly=_closure_guidance_assembly(inputs)
    try:
        job=next(assembly)
        while True:
            response=(_resolve_closure_guidance_job(context,db,job) if read_context is None and read_roster is None else
                _resolve_closure_guidance_job(context,db,job,read_context=read_context,read_roster=read_roster))
            job=assembly.send(response)
    except StopIteration as completed:
        _require(_closure_guidance_inputs(context,db,records)==inputs,
            'Guidance local inputs changed during complete validation')
        return evidence._finish_linked_read_context(completed.value,read_context,read_roster=read_roster)


def _closure_guidance_assembly(inputs):
    """Pure guidance projection; every yielded obligation remains pending until resolved."""
    records=inputs['records'];authority=inputs['authority']
    references={};surfaces=set();retained=[];native=[]
    def source(reference):
        value=inputs['bodies'][evidence._raw(reference).decode()]
        metadata=inputs['metadata'].get(reference['name'])
        _require(metadata is not None and metadata['class']=='detail' and metadata['owner']['task'] is None,
            'Guidance requires original registered scope ownership')
        result=dict(reference=reference,created_at=metadata['created_at'],owner=metadata['owner'],guidance=value)
        references[evidence._raw(reference)]=result
        return result
    def assignment(body,reference,created_at,request_digest,discovery=None):
        if discovery is None:discovery=inputs['bodies'][evidence._raw(body['discovery']).decode()]
        guidance=body['guidance'];state._fields(guidance,('snapshot_ref','snapshot_digest','checked_at','freshness'))
        surface=body['binding']['client_surface'];original=source(guidance['snapshot_ref']);surfaces.add(surface)
        _require(original['guidance']['client_surface']==surface and _compatible_applicability(original['guidance']['applicability'],
            dict(client_version=discovery['native_version'],capability_fingerprint=discovery['capability_digest'])),
            'Guidance applicability changed')
        _require(guidance['snapshot_digest']==guidance['snapshot_ref']['digest'] and guidance['checked_at']==original['guidance']['checked_at'],
            'Original native guidance correspondence changed')
        return dict(reference=reference,created_at=created_at,request_digest=request_digest,guidance=guidance,
            discovery={key:discovery[key] for key in ('client_surface','native_version','capability_digest')},
            binding={key:body['binding'][key] for key in ('client_surface','native_version','parent_session_id','task_name')},
            source_created_at=original['created_at'])
    for record in records:
        if record['data']['role'] not in NATIVE_ROLES:continue
        reference=dict(authority=authority,id=record['id'],owner=record['owner'],assignment=record['data']['assignment'])
        native.append(assignment(record['body'],reference,record['data']['lifecycle']['created_at'],record['data']['request_digest']))
    rows=inputs['rows']
    owners=set()
    for row in rows:
        metadata=state._metadata_record(row);owner=metadata['owner'].get('task')
        if owner is None:continue
        _require(owner['authority']==authority,'Foreign retained guidance owner remains protected')
        compact=inputs['bodies'][evidence._raw(dict(name=row[0],digest=row[1])).decode()]
        if (yield from _closure_guidance_job(dict(kind='bookkeeping',reference=dict(name=row[0],digest=row[1]),body=compact))):continue
        marker=inputs['markers'][evidence._raw(owner).decode()]
        if marker is None:continue
        retained_support=(yield from _closure_guidance_job(dict(kind='retained-owner',owner=owner)))
        if retained_support is not None:
            if retained_support['reference']!=dict(name=row[0],digest=row[1]):continue
            compact=retained_support['content']
            certificates=[item for item in compact['stopped'].get('assignments',[]) if item['role'] in NATIVE_ROLES]
            _require(len(compact['native_sources'])==len(compact['native_guidance'])==len(certificates),
                'Retained native source coverage differs from issued certificates')
            for certificate in certificates:
                matches=[item for item in compact['native_sources'] if item['assignment']==certificate['reference']]
                _require(len(matches)==1,'Retained native source is unavailable or ambiguous')
                original=matches[0]
                actual=assignment(original,certificate['reference'],certificate['created_at'],certificate['request_digest'],
                    discovery=original['discovery']['content'])
                _require(actual in compact['native_guidance'],'Retained native correspondence differs from original qualified projection')
            for original in compact['scope_guidance']:
                _require(source(original['reference'])==original,'Retained scope guidance changed')
            retained.append(dict(reference=retained_support['reference'],identity=owner,marker=marker,
                assignments=[[item['id'],item['marker']] for item in compact['assignment_markers']]))
            continue
        linked=None
        if compact.get('kind')=='linked-closure-preparation':
            support=(yield from _closure_guidance_job(dict(kind='linked-component',owner=owner,receipt=dict(name=row[0],digest=row[1]),basis=compact['stopped']['linked'])))
            _require(not support.get('discovered_authorities'),'Retained guidance source guards are incomplete')
            member=next(m for m in support['members'] if m['reference']==owner)
            _require(member['preparation']['reference']==dict(name=row[0],digest=row[1]),'Retained guidance preparation is not its actual owner')
            continue
        owner_key=evidence._raw(owner);_require(owner_key not in owners,'Retained guidance owner has ambiguous compact receipts');owners.add(owner_key)
        if compact.get('stopped',{}).get('linked'):
            support=(yield from _closure_guidance_job(dict(kind='linked-component',owner=owner,receipt=dict(name=row[0],digest=row[1]),basis=compact['stopped']['linked'])))
            _require(not support.get('discovered_authorities'),'Retained guidance component source guards are incomplete')
            linked=next(m for m in support['members'] if m['reference']==owner)
            _require(linked['closure']['reference']==dict(name=row[0],digest=row[1]),'Retained guidance closure is not its actual owner')
        _require(compact.get('kind')=='task-closure' and compact['identity']==owner and not marker['evidence_expired']
            and marker['pins_digest']==state._digest(compact['pins']) and marker['created_at']==compact['created_at']==metadata['created_at'],
            'Retained scope-guidance receipt differs from original task identity/age')
        yield from _closure_guidance_job(dict(kind='stopped-certificate',owner=owner,receipt=dict(name=row[0],digest=row[1]),certificate=compact['stopped']))
        _require(len(compact.get('native_guidance',[]))==sum(item['role'] in NATIVE_ROLES for item in compact['stopped'].get('assignments',[])),
            'Retained native guidance coverage differs from complete issued assignments')
        markers=[]
        for certificate in compact['stopped'].get('assignments',[]):
            ref=certificate['reference'];assignment_marker=inputs['assignment_markers'][ref['id']];markers.append([ref['id'],assignment_marker])
            if certificate['role'] not in NATIVE_ROLES:continue
            body=inputs['bodies'][evidence._raw(ref['assignment']).decode()]
            _require(body['owner']==ref['owner'] and body['role']==certificate['role'] and body['pins']==compact['pins'] and body['accepted_identity']==compact['accepted_identity']
                and body['lifecycle']['created_at']==certificate['created_at'] and evidence._digest(body['request'])==certificate['request_digest'],
                'Retained native assignment differs from issued owner/pins/request/age')
            for original in (ref['assignment'],body['discovery'],body['guidance']['snapshot_ref']):
                source_metadata=inputs['metadata'].get(original['name'])
                _require(source_metadata is not None,'Retained native guidance source metadata is unavailable')
                projected=dict(reference=original,created_at=source_metadata['created_at'],owner=source_metadata['owner'],
                    bytes=source_metadata['bytes'],unit='bytes')
                if linked is not None:
                    projected['reference']=dict(authority=owner['authority'],artifact=original)
                    present=any(source['reference']==projected['reference'] for source in support['sources'])
                else:present=original in compact['artifacts']
                _require(present and projected in compact['sources'],
                    'Retained native guidance source differs from original managed provenance/age')
            actual=assignment(body,ref,certificate['created_at'],certificate['request_digest'])
            _require(actual in compact.get('native_guidance',[]),'Retained native guidance projection differs from actual original assignment')
        for original in compact.get('scope_guidance',[]):
            actual=source(original['reference'])
            present=any(source['reference']==dict(authority=owner['authority'],artifact=original['reference']) for source in support['sources']) if linked is not None else original['reference'] in compact['artifacts']
            _require(actual==original and present,
                'Retained scope guidance differs from original source projection')
        receipt=dict(reference=dict(name=row[0],digest=row[1]),identity=owner,marker=marker,assignments=markers)
        if linked is not None:receipt['linked_component']=support
        retained.append(receipt)
    if inputs['cache']['folder'] is None:
        return dict(references=[],sources=[],native=native,retained=retained,files={},folder=None)
    for reference in inputs['cache']['references']:source(reference)
    files=inputs['cache']['files']
    sources=sorted(references.values(),key=evidence._raw)
    return dict(references=sorted([value['reference'] for value in sources],key=evidence._raw),sources=sources,native=native,
        retained=retained,files=files,folder=inputs['cache']['folder'])


def _trigger(value):
    state._fields(value, ("kind", "id"))
    _require(value["kind"] in ("routine", "manual", "maintenance"), "Invalid guidance refresh trigger")
    state._text(value["id"], "guidance trigger id", 128)


def _key(request, prior, snapshot_valid):
    return evidence._digest(dict(applicability=dict(capability_fingerprint=request["applicability"]["capability_fingerprint"]),
        prior=prior, snapshot_valid=snapshot_valid))


def _keys(request, prior, snapshot_valid):
    # Retain exact legacy control/source correspondence without rewriting stored history.
    return {_key(request, prior, snapshot_valid), evidence._digest(dict(applicability=request["applicability"],
        prior=prior, snapshot_valid=snapshot_valid))}


def _status(scope, folder, control, request):
    guidance, reference, prior = _snapshot(scope, folder, request)
    claim = control["claim"] if control else None
    active = claim is not None and claim["expires_at"] > _now()
    attempt = control["last_attempt"] if control else None
    compatible = guidance is not None and _compatible_applicability(guidance["applicability"], request["applicability"])
    keys = _keys(request, prior, guidance is not None)
    if compatible:
        keys.update(_keys(dict(applicability=guidance["applicability"]), prior, True))
    failed = attempt is not None and attempt["outcome"] == "failure" and attempt["key"] in keys
    freshness = "unavailable" if guidance is None else "incompatible" if not compatible else "stale" if failed else "verified"
    profiles = request.get("profile_ids", [])
    result = dict(client_surface=request["client_surface"], applicability=request["applicability"], freshness=freshness,
        usable=compatible, refresh_needed=not compatible and not failed, refresh_in_progress=active,
        checked_at=guidance["checked_at"] if guidance else None, snapshot_ref=reference,
        snapshot_digest=reference["digest"] if reference else None,
        recommendations=[profile for profile in guidance["recommendations"] if profile["id"] in profiles]
                        if compatible else [], limitations=list(GUIDANCE_LIMITATIONS))
    return result, prior, keys


def guidance_status(scope, request):
    """Report compatible guidance; age alone never expires it or establishes access."""
    _common(request, optional=("profile_ids",))
    if "profile_ids" in request:
        _strings(request["profile_ids"], "guidance profile ids", maximum=32, limit=64)
    with _locked(scope, request) as (folder, control):
        return _status(scope, folder, control, request)[0]


def claim_guidance(scope, request):
    """Reserve one bounded refresh; callers fetch sources after this lock is released."""
    _common(request, required=("trigger",), optional=("lease_seconds",))
    _trigger(request["trigger"])
    lease = request.get("lease_seconds", 120)
    _require(type(lease) is int and 1 <= lease <= 600, "Invalid guidance claim lease")
    with _locked(scope, request, create=True) as (folder, control):
        result, prior, keys = _status(scope, folder, control, request)
        if result["refresh_in_progress"]:
            return result | dict(claimed=False)
        expired = control["claim"] is not None
        attempt = control["last_attempt"]
        same = attempt is not None and attempt["key"] in keys
        trigger = request["trigger"]
        if not expired and ((trigger["kind"] == "routine" and not result["refresh_needed"]) or
                            (trigger["kind"] != "routine" and same and trigger == attempt["trigger"])):
            return result | dict(claimed=False)
        control["generation"] += 1
        claim = dict(token=uuid.uuid4().hex, generation=control["generation"],
            applicability=request["applicability"], prior=prior, trigger=trigger, expires_at=_now() + lease)
        control["claim"] = claim
        _atomic(folder / (request["client_surface"] + ".control.json"), control)
        return result | dict(claimed=True, refresh_in_progress=True, token=claim["token"],
                             generation=claim["generation"], expires_at=claim["expires_at"])


def record_guidance(scope, request):
    """Publish only the live claim's result; failure never rewrites verified contents."""
    _common(request, required=("token", "generation", "coordinator_ref"), optional=("guidance", "failure"))
    _require(("guidance" in request) != ("failure" in request), "Record guidance or failure, not both")
    state._text(request["coordinator_ref"], "guidance coordinator reference", 1024)
    _require(isinstance(request["token"], str) and re.fullmatch(r"[0-9a-f]{32}", request["token"]), "Invalid guidance token")
    state._integer(request["generation"], "guidance generation", 1)
    if "guidance" in request:
        _guidance(request["guidance"], request)
        _require(request["guidance"]["applicability"] == request["applicability"], "Guidance publication applicability changed")
    else:
        state._text(request["failure"], "guidance failure", 1024)
    with _locked(scope, request) as (folder, control):
        _require(control is not None, "Guidance refresh claim is unavailable")
        claim = control["claim"]
        result, prior, _ = _status(scope, folder, control, request)
        _require(claim is not None and claim["token"] == request["token"] and
                 claim["generation"] == request["generation"] and claim["expires_at"] > _now() and
                 claim["applicability"] == request["applicability"] and claim["prior"] == prior,
                 "Guidance refresh claim expired, changed or superseded")
        def unexpired():
            _require(claim["expires_at"] > _now(), "Guidance refresh claim expired before publication")
        if "guidance" in request:
            reference = evidence._artifact(scope, request["guidance"], owner=evidence._guidance_artifact_owner(scope))
            _atomic(folder / (request["client_surface"] + ".json"),
                    dict(guidance=request["guidance"], snapshot_ref=reference), guard=unexpired)
            _, _, prior = _snapshot(scope, folder, request)
        control["claim"] = None
        control["last_attempt"] = dict(key=_key(request, prior, "guidance" in request or result["snapshot_ref"] is not None),
            trigger=claim["trigger"],
            outcome="verified" if "guidance" in request else "failure",
            reason="Coordinator verified source summary" if "guidance" in request else request["failure"],
            coordinator_ref=request["coordinator_ref"])
        _atomic(folder / (request["client_surface"] + ".control.json"), control, guard=unexpired)
        return _status(scope, folder, control, request)[0]


# Admission coordinates participating owners; it cannot restrict native tools or external writers.
ADMISSION_VERSION = 1
VERSION_PROVENANCE_FIELDS = ('current_observer_version', 'parent_creation_version', 'worker_creation_version')
MAX_ASSIGNMENTS = 4096
MAX_CLAIMS = 64
MAX_JOBS = 32
# Automatic paths + D5 instructions/tools + all accepted D5 check and D6 job resources.
MAX_VERIFICATION_CLAIMS = evidence.MAX_ARTIFACT
PLAN_FIELDS = ('version', 'route', 'authority', 'selection_constraints', 'limits', 'fallback', 'jobs')
ADMISSION_LIMITATIONS = [
    'Route authority, exposed controls and host capacity require independent coordinator verification',
    'Reservations coordinate participating owners; native tools do not enforce path, process or descendant limits',
    'Deadlines and resource units bound admission, not measured execution or usage',
    'Directory claims serialize physical-path work because descendant aliases are not inventoried',
    'Launch permission is once-only in this coordinator protocol, not an idempotent native spawn mechanism',
]


def _reference(value):
    state._fields(value, ('name', 'digest'))
    _require(isinstance(value['name'], str) and re.fullmatch(r'[0-9a-f]{32}\.json', value['name']) and
             _hex(value['digest']), 'Invalid immutable delegation reference')


def validate_delegation(value):
    """Validate the one bounded task plan/counter record without adopting any authority."""
    state._fields(value, ('version', 'plan', 'attempts_consumed', 'resource_units_consumed'))
    _require(type(value['version']) is int and value['version'] == ADMISSION_VERSION, 'Unsupported delegation state')
    _reference(value['plan'])
    state._integer(value['attempts_consumed'], 'delegation attempt counter')
    state._integer(value['resource_units_consumed'], 'delegation resource counter')


def _claims(value, *, maximum=MAX_CLAIMS):
    from pathlib import Path
    import unicodedata
    _require(isinstance(value, list) and 0 < len(value) <= maximum, 'Invalid assignment resource claims')
    result = []
    for claim in value:
        state._fields(claim, ('kind', 'key', 'mode'), ('host',))
        kind, mode = claim['kind'], claim['mode']
        _require(kind in {'path', 'git', 'build', 'database', 'service', 'port'} and mode in {'read', 'write'},
                 'Unsupported assignment claim')
        state._text(claim['key'], 'resource key', 4096)
        folded = unicodedata.normalize('NFC', claim['key']).casefold()
        if kind in {'service', 'port'}:
            _require(claim.get('host') == 'local', 'Unknown or unsupported shared-resource host')
            if kind == 'port':
                match = re.fullmatch(r'(tcp|udp):(\d{1,5})', folded)
                _require(match is not None and 1 <= int(match[2]) <= 65535, 'Invalid shared port identity')
                folded = match[1] + ':' + str(int(match[2]))
            resource = dict(kind=kind, key=folded, mode=mode, host='local')
        else:
            _require('host' not in claim and Path(claim['key']).is_absolute(), 'Path claims require absolute local paths')
            try:
                path = Path(claim['key']).resolve()
                ancestors, physical, directory = [], None, None
                for ancestor in (path, *path.parents):
                    try:
                        info = ancestor.stat()
                    except FileNotFoundError:
                        continue
                    _require(ancestor == path or stat.S_ISDIR(info.st_mode), 'Resource ancestor is not a directory')
                    inode = [info.st_dev, info.st_ino]
                    if ancestor == path:
                        physical, directory = inode, stat.S_ISDIR(info.st_mode)
                    else:
                        ancestors.append(inode)
                resource = dict(kind=kind, key=unicodedata.normalize('NFC', str(path)).casefold(), mode=mode,
                                path=str(path), physical=physical, ancestors=ancestors, directory=directory)
            except (OSError, RuntimeError):
                raise state.StateError('Physical resource ownership is unknown; reconcile or serialize affected work') from None
        _require(resource not in result, 'Duplicate canonical resource claim')
        result.append(resource)
    return result


def _overlap(left, right):
    if left['kind'] in {'service', 'port'} or right['kind'] in {'service', 'port'}:
        return left['kind'] == right['kind'] and left['key'] == right['key'] and left.get('host') == right.get('host')
    a, b = left['key'], right['key']
    return (a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/') or
            left['physical'] is not None and (left['physical'] == right['physical'] or
                left['physical'] in right['ancestors']) or
            right['physical'] is not None and right['physical'] in left['ancestors'])


def _unknown_directory_aliases(left, right):
    return ('path' in left and 'path' in right and any(
        claim.get('directory') is True or 'directory' not in claim and claim['physical'] is not None
        for claim in (left, right)))


def _conflict(left, right):
    mutable = left['mode'] == 'write' or right['mode'] == 'write'
    # No descendant inventory: directory aliases may target any volume, so serialize path ownership.
    return mutable and (_unknown_directory_aliases(left, right) or _overlap(left, right))


def _covers(claim, file):
    if 'path' not in claim or 'path' not in file:
        return False
    return (claim['key'] == file['key'] or claim['physical'] is not None and claim['physical'] == file['physical'] or
            claim.get('directory') is not False and (file['key'].startswith(claim['key'].rstrip('/') + '/') or
                claim['physical'] is not None and claim['physical'] in file['ancestors']))


def _held_conflict(claims, held_claims):
    for held in held_claims:
        current = _claims([dict(kind=held['kind'], key=held['path'], mode=held['mode'])])[0] if 'path' in held else held
        if any(_conflict(claim, held) or _conflict(claim, current) for claim in claims):
            return True
    return False


def _held_overlap(claims, held_claims):
    for held in held_claims:
        current = _claims([dict(kind=held['kind'], key=held['path'], mode=held['mode'])])[0] if 'path' in held else held
        if any(_unknown_directory_aliases(claim, held) or _unknown_directory_aliases(claim, current) or
               _overlap(claim, held) or _overlap(claim, current) for claim in claims):
            return True
    return False


def _git_directory(scope):
    return _git_directory_paths(scope.repository_root, scope.git_common_dir)


def _git_directory_paths(repository_root, git_common_dir):
    from pathlib import Path
    raw = evidence._git(repository_root, 'rev-parse', '--path-format=absolute',
                        '--show-toplevel', '--git-dir', '--git-common-dir')
    try:
        root, private, common = (Path(os.fsdecode(line)).resolve(strict=True) for line in raw.splitlines())
        _require(root == repository_root and common == git_common_dir and private.is_dir(),
                 'Actual Git checkout metadata differs from the accepted scope')
        return str(private)
    except (OSError, ValueError):
        raise state.StateError('Actual worktree Git metadata is unavailable or incompatible') from None


def _git_routing(scope, git_directory):
    return _git_routing_paths(scope.repository_root, Path(git_directory), scope.git_common_dir)


def _git_routing_paths(repository_root, private, common):
    """Snapshot only bounded routing files and identities; no Git call or tree inventory."""
    from pathlib import Path
    private, common = Path(private), Path(common)
    entries = {}
    def identity(info):
        return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]
    def node(name, path):
        path = Path(path)
        resolved = path.resolve()
        value = dict(path=str(path), resolved=str(resolved), kind='missing')
        entries[name] = value
        try:
            lexical = path.lstat()
        except FileNotFoundError:
            return value, None
        physical = resolved.stat()
        value.update(identity=identity(lexical), physical=identity(physical),
                     link=os.readlink(path) if stat.S_ISLNK(lexical.st_mode) else None)
        if stat.S_ISDIR(physical.st_mode):
            value['kind'] = 'directory'
            raw = None
        else:
            _require(stat.S_ISREG(physical.st_mode) and physical.st_size <= 8192,
                     'Git routing input is unsupported or exceeds its bound')
            descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                _require(identity(opened) == identity(physical), 'Git routing input changed while opening')
                raw = stream.read(8193)
                after = os.fstat(stream.fileno())
                _require(len(raw) <= 8192 and (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) ==
                         (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'Git routing input changed while reading')
            value.update(kind='file', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        _require(path.resolve() == resolved and identity(path.lstat()) == identity(lexical),
                 'Git routing identity changed while reading')
        return value, raw
    def target(name, base, raw, expected):
        text = os.fsdecode(raw.rstrip(b'\r\n'))
        state._text(text, 'Git routing target', 4096)
        value, _ = node(name, base / text)
        _require(value['kind'] == 'directory' and value['resolved'] == str(expected),
                 'Git routing target changed from the observed directory')
    try:
        root, _ = node('checkout', repository_root)
        own, _ = node('private', private)
        shared, _ = node('common', common)
        _require(all(value['kind'] == 'directory' for value in (root, own, shared)), 'Git routing directory is unavailable')
        marker, raw = node('marker', repository_root / '.git')
        if marker['kind'] == 'directory':
            _require(marker['resolved'] == str(private), 'Git routing marker no longer identifies the observed directory')
        else:
            _require(marker['kind'] == 'file' and raw.startswith(b'gitdir: '), 'Git routing marker is unsupported')
            target('marker_target', repository_root, raw[8:], private)
        commondir, raw = node('commondir', private / 'commondir')
        if commondir['kind'] == 'missing':
            _require(private == common, 'Git common routing no longer identifies the observed directory')
        else:
            _require(commondir['kind'] == 'file', 'Git common routing input is unsupported')
            target('common_target', private, raw, common)
        node('worktree_backlink', private / 'gitdir')
        evidence._bounded(entries)
        return entries
    except (OSError, ValueError, RuntimeError):
        raise state.StateError('Git routing inputs are unavailable, unsafe or changed') from None


def _worker_claims(scope, claims, git_directory):
    from pathlib import Path
    import unicodedata
    marker = scope.repository_root / '.git'
    protected = [_claims([dict(kind='git', key=str(path), mode='write')])[0]
                 for path in dict.fromkeys((scope.git_common_dir, Path(git_directory), marker))]
    # Protect the routing marker's lexical location as well as its resolved target.
    protected.append(dict(protected[-1], key=unicodedata.normalize('NFC', str(marker)).casefold()))
    for claim in claims:
        if claim['mode'] != 'write':
            continue
        _require(claim['kind'] != 'git' and not any(_overlap(claim, git) for git in protected),
                 'Git mutations remain coordinator-owned')
        if 'path' in claim:
            path = Path(claim['path'])
            _require(not path.name.endswith('.lock') and path.name not in
                     {'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock'} and
                     '.vibe-workflow' not in path.parts and '.vibe-coding-workflow' not in path.parts,
                     'Lockfiles and tracking remain coordinator-owned')


def _worker_scope(scope, claims, files=()):
    """Check new filesystem admission against the checkout covered by D5/D6."""
    from pathlib import Path
    writes = [(claim, Path(source['key'])) for source, claim in zip(claims, _claims(claims))
              if claim['mode'] == 'write' and 'path' in claim]
    for name in files:
        declared = scope.repository_root / name
        file = _claims([dict(kind='path', key=str(declared), mode='write')])[0]
        if any(_covers(claim, file) for claim, _ in writes):
            writes.append((file, declared))
    for claim, declared in writes:
        for path in dict.fromkeys((declared, Path(claim['path']))):
            _require(path.is_relative_to(scope.repository_root), 'Worker filesystem writes must stay within the task checkout')
            # No descendant inventory: check declared paths and named files for repository boundaries.
            for ancestor in (path, *path.parents):
                if ancestor == scope.repository_root:
                    break
                try:
                    (ancestor / '.git').lstat()
                except (FileNotFoundError, NotADirectoryError):
                    continue
                except OSError:
                    raise state.StateError('Worker repository boundary is unavailable') from None
                raise state.StateError('Worker filesystem writes cannot enter a separate repository or submodule')


def _delegation_plan(scope, plan):
    evidence._bounded(plan)
    state._fields(plan, (*PLAN_FIELDS, 'git_directory'))
    state._text(plan['git_directory'], 'observed worktree Git directory', 4096)
    _require(type(plan['version']) is int and plan['version'] == ADMISSION_VERSION, 'Unsupported delegation plan')
    route = plan['route']
    state._fields(route, ('mechanism', 'client_surface', 'parent_session_id', 'authority_ref', 'controls_ref'))
    _require(route['mechanism'] == 'codex-native-subagent' and route['client_surface'] in
             {'codex-cli', 'codex-desktop'}, 'Only the supported existing native Codex route can execute')
    for field in ('parent_session_id', 'authority_ref', 'controls_ref'):
        state._text(route[field], 'native route ' + field)
    authority = plan['authority']
    state._fields(authority, ('state_dir', 'participants_ref', 'ownership_known'))
    _require(authority['state_dir'] == str(scope.runtime.state_dir) and authority['ownership_known'] is True,
             'Relevant owners require one approved shared state authority')
    state._text(authority['participants_ref'], 'participating-owner evidence')
    constraints = plan['selection_constraints']
    state._fields(constraints, ('complexity', 'risk', 'rationale', 'required_controls'))
    for field in ('complexity', 'risk', 'rationale'):
        state._text(constraints[field], 'selection ' + field)
    _strings(constraints['required_controls'], 'required dispatch controls')
    _require(set(constraints['required_controls']) <= {'spawn', 'explicit_model', 'explicit_effort', 'fork_turns_none'},
             'Required native enforcement or dispatch control is unavailable')
    limits = plan['limits']
    state._fields(limits, ('max_attempts', 'max_resource_units', 'deadline', 'max_parallel_workers', 'host_capacity', 'capacity_ref'))
    for field in ('max_attempts', 'max_resource_units', 'deadline', 'max_parallel_workers', 'host_capacity'):
        state._integer(limits[field], 'delegation ' + field, 1)
    _require(limits['max_resource_units'] <= 10**9 and
             limits['deadline'] < 2**63 and 1 <= limits['max_parallel_workers'] <= limits['host_capacity'] <= 1024,
             'Delegation limits exceed supported finite bounds')
    state._text(limits['capacity_ref'], 'observed host capacity evidence')
    fallback = plan['fallback']
    state._fields(fallback, ('action', 'reference'))
    _require(fallback['action'] in {'stop', 'direct-coordinator'}, 'Unsupported execution fallback')
    state._text(fallback['reference'], 'fallback authorization reference')
    jobs = plan['jobs']
    _require(isinstance(jobs, list) and jobs, 'Invalid bounded job plan')
    by_id, claims = {}, {}
    for job in jobs:
        state._fields(job, ('id', 'role', 'claims', 'dependencies', 'max_attempts'))
        state._text(job['id'], 'delegation job id', 128)
        _require(job['id'] not in by_id and job['role'] in {'worker', 'review', 'coordinator', 'check'}, 'Invalid job identity or role')
        state._integer(job['max_attempts'], 'job attempt limit', 1)
        _require(job['max_attempts'] <= limits['max_attempts'], 'Job attempts exceed the original task limit')
        _strings(job['dependencies'], 'job dependencies', maximum=len(jobs), limit=128)
        by_id[job['id']], claims[job['id']] = job, _claims(job['claims'])
        if job['role'] == 'review':
            _require(all(claim['mode'] == 'read' for claim in claims[job['id']]), 'Native review resources must be read-only')
        if job['role'] == 'worker':
            _worker_claims(scope, claims[job['id']], plan['git_directory'])
    # Iterative topological validation keeps depth independent of Python recursion.
    remaining = {identity: len(job['dependencies']) for identity, job in by_id.items()}
    dependents = {identity: [] for identity in by_id}
    for identity, job in by_id.items():
        for dependency in job['dependencies']:
            _require(dependency in by_id, 'Unknown or cyclic delegation dependency')
            dependents[dependency].append(identity)
    ready = [identity for identity, count in remaining.items() if not count]
    visited = 0
    while ready:
        identity = ready.pop()
        visited += 1
        for dependent in dependents[identity]:
            remaining[dependent] -= 1
            if not remaining[dependent]:
                ready.append(dependent)
    _require(visited == len(jobs), 'Unknown or cyclic delegation dependency')
    coordinator = [claim for job in jobs if job['role'] == 'coordinator' for claim in claims[job['id']]]
    for job in jobs:
        if job['role'] == 'worker':
            _require(not any(_conflict(a, b) for a in claims[job['id']] for b in coordinator),
                     'Worker scope overlaps coordinator-owned resources')
    return plan


def _task_plan(scope, task):
    _require('delegation' in task, 'Delegation has no accepted bounded plan')
    return _delegation_plan(scope, evidence._read(scope, task['delegation']['plan']))


def plan_delegation(scope, request):
    """Pin one immutable task plan; an assertion records but cannot grant native authority."""
    evidence._request(request, ('plan',))
    request = json_object(evidence._raw(request))
    with state._database(scope) as db:
        evidence._task(db,scope,request)
    state._fields(request['plan'], PLAN_FIELDS)
    plan = _delegation_plan(scope, dict(request['plan'], git_directory=_git_directory(scope)))
    _require(plan['route']['parent_session_id'] == request['session_id'], 'Prepared native parent differs from task owner')
    with state._database(scope) as db:
        task = evidence._task(db, scope, request)
        _require('evidence' in task, 'Define accepted D5 evidence before delegation')
        if 'delegation' in task:
            _require(_task_plan(scope, task) == plan, 'Delegation plan is immutable; original budgets cannot reset')
            return dict(ok=True, revision=task['revision'], plan=task['delegation']['plan'],
                        plan_digest=task['delegation']['plan']['digest'], limitations=ADMISSION_LIMITATIONS)
    _require(plan['limits']['deadline'] > _now(), 'Delegation admission deadline has elapsed')
    for job in plan['jobs']:
        if job['role'] == 'worker':
            _worker_scope(scope, job['claims'])
    reference = evidence._artifact(scope, plan, owner=evidence._artifact_owner(scope, task))
    with state._database(scope, write=True) as db:
        task = evidence._task(db, scope, request)
        _require('delegation' not in task, 'Delegation plan was concurrently established')
        for job in plan['jobs']:
            if job['role'] == 'worker':
                _worker_scope(scope, job['claims'])
        task['delegation'] = dict(version=ADMISSION_VERSION, plan=reference, attempts_consumed=0, resource_units_consumed=0)
        if task['state'] == 'DONE':
            task['state'] = 'REVIEW'
        task['revision'] += 1
        state._save(db, state._scope_id(scope), task)
    return dict(ok=True, revision=task['revision'], plan=reference, plan_digest=reference['digest'], limitations=ADMISSION_LIMITATIONS)


NATIVE_ROLES = {'worker', 'review'}


def _stored_claims(claims, *, maximum=MAX_CLAIMS):
    _require(isinstance(claims, list) and 0 < len(claims) <= maximum, 'Invalid stored claims')
    for claim in claims:
        _require(claim['kind'] in {'path', 'git', 'build', 'database', 'service', 'port'} and
            claim['mode'] in {'read', 'write'}, 'Invalid stored claim kind/mode')
        state._text(claim['key'], 'stored resource key', 4096)
        if claim['kind'] in {'service', 'port'}:
            state._fields(claim, ('kind', 'key', 'mode', 'host'))
            _require(_claims([claim]) == [claim], 'Noncanonical stored host claim')
        else:
            state._fields(claim, ('kind', 'key', 'mode', 'path', 'physical', 'ancestors', 'directory'))
            from pathlib import Path
            _require(isinstance(claim['path'], str) and Path(claim['path']).is_absolute() and
                isinstance(claim['ancestors'], list) and len(claim['ancestors']) <= 4096 and
                (claim['directory'] is None or type(claim['directory']) is bool), 'Invalid stored physical claim')
            for physical in claim['ancestors'] + ([] if claim['physical'] is None else [claim['physical']]):
                _require(isinstance(physical, list) and len(physical) == 2 and
                    all(type(value) is int and value >= 0 for value in physical), 'Invalid stored physical identity')
    return claims


def _historical_read(scope, context, db, artifact_refs, reference):
    _reference(reference)
    value=evidence._read(None if context else scope,reference,context=context,db=db)
    if artifact_refs is not None: artifact_refs.append(dict(reference))
    return value


def _record(scope, row, *, db=None, context=None, artifact_refs=None):
    identity, scope_id, client, session, task_id, dispatch_id, raw = row
    try:
        capture = state._CAPTURE_SQL_SCOPE.get()
        if capture is None:
            data = json_object(raw, limit=evidence.MAX_ARTIFACT)
            return _record_data(scope, row, data, db=db, context=context, artifact_refs=artifact_refs)
        resources, escrow = capture
        _require(escrow is None, 'Assignment parser cannot borrow exit-validation allowance')
        state._capture_registered(resources, db)
        acquisition = resources.current_acquisition()
        record = None
        def validate(data):
            nonlocal record
            record = _record_data(scope, row, data, db=db, context=context, artifact_refs=artifact_refs)
        acquisition._decode_owned(raw,
            lambda value: json_object(value, limit=evidence.MAX_ARTIFACT), validate)
        return record
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        if state._resource_origin(error): raise
        raise state.StateError('Shared assignment history is corrupt or incompatible') from None


def _record_data(scope, row, data, *, db=None, context=None, artifact_refs=None):
    read = partial(_historical_read, scope, context, db, artifact_refs)
    identity, scope_id, client, session, task_id, dispatch_id, raw = row
    state._fields(data, ('version', 'job_id', 'role', 'status', 'request_digest', 'assignment', 'candidate',
        'claims', 'resource_units', 'revision', 'plan_digest', 'host_capacity', 'binding', 'retry_of',
        'diagnostic', 'observation', 'reconciliation', 'result'), ('lifecycle', 'workstream_dispatch', 'workstream_continuation'))
    _require(type(data['version']) is int and data['version'] == ADMISSION_VERSION and
             isinstance(identity, str) and re.fullmatch(r'[0-9a-f]{32}', identity) and
             data['role'] in NATIVE_ROLES | {'coordinator', 'check'} and
             data['status'] in {'prepared', 'observed', 'uncertain', 'settled'}, 'Invalid assignment ledger record')
    _require(_hex(data['request_digest']), 'Invalid assignment request digest')
    state._integer(data['revision'], 'stored assignment revision', 1)
    _reference(data['assignment'])
    body = read(data['assignment'])
    owner = dict(scope_id=scope_id, client=client, session_id=session, task_id=task_id, dispatch_id=dispatch_id)
    _require(body['owner'] == owner and all(body[key] == data[key] for key in
        ('job_id', 'role', 'claims', 'resource_units', 'revision', 'plan_digest', 'host_capacity', 'binding', 'retry_of', 'diagnostic')) and
        body['candidate'] == data['candidate'] and evidence._digest(body['request']) == data['request_digest'],
        'Assignment immutable binding differs from its ledger record')
    state._identity(body['request'])
    pin_version = state._validate_pins(body['pins'])
    canonical_fields = ('accepted_identity',) if pin_version == 3 else ()
    _require(('lifecycle' in body) == ('lifecycle' in data), 'Assignment lifetime metadata differs from immutable body')
    if 'lifecycle' in data:
        _require(pin_version == 3 and body['lifecycle'] == data['lifecycle'], 'Legacy or rebound assignment lifetime')
        state._fields(data['lifecycle'], ('created_at',))
        state._integer(data['lifecycle']['created_at'], 'original assignment creation time')
        canonical_fields += ('lifecycle',)
    if pin_version == 3:
        accepted = state._accepted_identity(body.get('accepted_identity'))
        _require(state._digest(accepted) == body['pins']['accepted_scope_digest'] and
            state._digest([3, accepted['project_id'], accepted['repository_id'], accepted['checkout_id']]) == scope_id and
            accepted['client'] == client and all(accepted['configuration'][k] == body['pins'][k]
                for k in ('policy_digest', 'runtime_digest')), 'Assignment accepted scope changed')
    _require(type(body['version']) is int and body['version'] == ADMISSION_VERSION and
        type(body['pins']['schema_version']) is int and pin_version in (2, 3) and
        all(_hex(body['pins'][key]) for key in ('policy_digest', 'runtime_digest')) and
        all(body['request'][key] == owner[key] for key in ('client', 'session_id', 'task_id', 'dispatch_id')),
        'Assignment original owner or pins are corrupt')
    verifier = data['role'] == 'coordinator' and read(body['operation']).get('kind') == 'completion-verification'
    size = len(raw.encode('utf-8')) if isinstance(raw, str) else len(raw)
    _require(verifier or size <= MAX_INPUT, 'Assignment record exceeds its role-specific byte limit')
    _stored_claims(data['claims'], maximum=MAX_VERIFICATION_CLAIMS if verifier else MAX_CLAIMS)
    policy = read(body['policy'])
    _require(policy['policy_digest'] == body['pins']['policy_digest'], 'Immutable assignment policy binding changed')
    if data['role'] in NATIVE_ROLES:
        state._fields(body, ('version', 'owner', 'pins', *canonical_fields, 'request', 'job_id', 'role', 'claims', 'resource_units',
            'revision', 'plan', 'plan_digest', 'git_routing', 'host_capacity', 'binding', 'candidate',
            'discovery', 'policy', 'report_contract', 'guidance', 'retry_of', 'diagnostic'), ('review',))
        _reference(body['plan'])
        plan = read(body['plan'])
        _require(body['plan']['digest'] == data['plan_digest'] and
            plan['route']['parent_session_id'] == session, 'Native assignment has no actual original plan')
        if data['role'] == 'review':
            review = body['review']
            state._fields(review, ('assignment_digest', 'coverage', 'criteria', 'd5_plan'))
            _reference(review['d5_plan'])
            _require(all(review[key] == body['request'][key] for key in ('coverage', 'criteria')),
                     'Review metadata differs from its original request')
            _require(review['assignment_digest'] == hashlib.sha256(body['request']['assignment'].encode()).hexdigest() and
                read(data['candidate'])['definitions_digest'] == review['d5_plan']['digest'] and
                all(claim['mode'] == 'read' for claim in data['claims']), 'Review prompt/plan/resources binding changed')
        else:
            _require('review' not in body, 'Worker cannot carry review admission metadata')
        for name in ('resource_units', 'host_capacity'):
            state._integer(data[name], 'stored native ' + name, 1)
        _reference(data['candidate'])
        binding = data['binding']
        state._fields(binding, ('attempt_id', 'parent_session_id', 'task_name', 'candidate_digest',
            'requested_model', 'requested_effort', 'client_surface', 'native_version'))
        _require(binding['attempt_id'] == identity and binding['parent_session_id'] == session and
            binding['task_name'] == _native_name(identity, data['role'], body['request']) and
            binding['candidate_digest'] == data['candidate']['digest'], 'Native assignment locator binding is corrupt')
    else:
        state._fields(body, ('version', 'owner', 'pins', *canonical_fields, 'scope_ref', 'decision_ref', 'request', 'operation',
            'job_id', 'role', 'claims', 'resource_units', 'revision', 'plan_digest', 'candidate', 'd5_plan',
            'd6_plan', 'host_capacity', 'binding', 'retry_of', 'diagnostic', 'policy'))
        _require(type(data['resource_units']) is int and data['resource_units'] == 0 and
            data['binding'] is None and data['host_capacity'] is None and data['retry_of'] is None and
            data['diagnostic'] is None, 'Model-free assignment cannot invent native facts or consumption')
        _require(body['scope_ref'] == body['request']['scope_ref'], 'Operation scope differs from original request')
        operation = read(body['operation'])
        state._fields(operation, ('id', 'kind', 'decision_ref', 'claims_digest'))
        _require(operation['claims_digest'] == evidence._digest(data['claims']) and
            operation['decision_ref'] == body['decision_ref'], 'Operation resource/decision binding is corrupt')
        if body['d5_plan'] is None:
            _require(data['plan_digest'] is None and data['candidate'] is None,
                     'Task-only operation invented D5 plan/candidate facts')
        else:
            _reference(body['d5_plan'])
            read(body['d5_plan'])
            _require(data['plan_digest'] == body['d5_plan']['digest'], 'Original D5 plan binding changed')
            if data['candidate'] is not None:
                _reference(data['candidate'])
                candidate = read(data['candidate'])
                _require(candidate['definitions_digest'] == data['plan_digest'] and candidate['pins'] == body['pins'],
                         'Original candidate binding changed')
        if body['d6_plan'] is not None:
            _reference(body['d6_plan'])
            read(body['d6_plan'])
        if data['role'] == 'coordinator':
            _require(operation['kind'] in {'coordinator-work', 'completion-verification'}, 'Invalid coordinator operation')
            if data['job_id'] is not None:
                _require(body['d6_plan'] is not None and operation['id'] == data['job_id'] and
                    any(job['id'] == data['job_id'] and job['role'] == 'coordinator'
                        for job in read(body['d6_plan'])['jobs']), 'Coordinator job binding is corrupt')
            if operation['kind'] == 'completion-verification':
                _require(body['d5_plan'] is not None and all(c['mode'] == 'read' for c in data['claims']),
                         'Verifier barrier requires actual D5 context and read-only claims')
                state._fields(body['request']['verification'], ('purpose', 'git_directory', 'git_routing'))
                _require(body['request']['verification']['purpose'] in {'completion', 'merge-evidence'},
                         'Invalid verifier barrier purpose')
        else:
            _reference(data['candidate'])
            _require(_hex(data['plan_digest']) and operation['kind'] == 'check' and
                data['job_id'] == operation['id'], 'Check assignment requires actual D5 bindings')
            plan = read(body['d5_plan'])
            candidate = read(data['candidate'])
            _require(body['d5_plan']['digest'] == data['plan_digest'] and
                candidate['definitions_digest'] == data['plan_digest'] and candidate['pins'] == body['pins'] and
                any(check['id'] == operation['id'] for check in plan['checks']), 'Check bindings do not match actual D5 evidence')
    if data['status'] == 'settled':
        _reference(data['reconciliation'])
        read(data['reconciliation'])
        _require(isinstance(data['result'], dict) and data['result'].get('outcome') in
                 {'completed', 'failed', 'cancelled'}, 'Settled assignment has no reconciled result')
    validate_record(data, read)
    return dict(id=identity, owner=owner, data=data, body=body)


def _ledger(scope, db, *, context=None, artifact_refs=None):
    capture = state._CAPTURE_SQL_SCOPE.get()
    if capture is not None:
        resources, escrow = capture
        _require(escrow is None, 'Assignment ledger cannot borrow exit-validation allowance')
        state._capture_registered(resources, db)
        acquisition = resources.current_acquisition()
        _require(db.in_transaction, 'Captured assignment ledger requires its existing transaction')
        acquisition.reserve(requested=512, live=2048)
        try:
            measured = db.execute(
                'select count(*),coalesce(sum(coalesce(length(cast(id as blob)),0)+'
                'coalesce(length(cast(scope_id as blob)),0)+coalesce(length(cast(client as blob)),0)+'
                'coalesce(length(cast(session_id as blob)),0)+coalesce(length(cast(task_id as blob)),0)+'
                'coalesce(length(cast(dispatch_id as blob)),0)+coalesce(length(cast(data as blob)),0)),0),'
                "coalesce(sum(typeof(id)!='text' or typeof(scope_id)!='text' or typeof(client)!='text' or "
                "typeof(session_id)!='text' or typeof(task_id)!='text' or typeof(dispatch_id)!='text' or "
                "typeof(data)!='text'),0) from "
                '(select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments limit ?)',
                (MAX_ASSIGNMENTS + 1,)).fetchone()
            _require(type(measured) is tuple and len(measured) == 3 and
                all(type(value) is int and value >= 0 for value in measured) and
                measured[0] <= MAX_ASSIGNMENTS + 1 and measured[2] == 0,
                'Captured assignment measurement overflow or invalid scalar')
            material = 4 * measured[1] + 128 * measured[0]
            # Raw row aliases and error frames belong to the acquisition until settlement.
            acquisition.reserve(requested=material, live=4 * material + 512 * measured[0])
        except BaseException as error:
            acquisition.fail(error)
            raise
    read = partial(_historical_read, scope, context, db, artifact_refs)
    rows = db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments limit ?',
                      (MAX_ASSIGNMENTS + 1,)).fetchall()
    _require(len(rows) <= MAX_ASSIGNMENTS, 'Shared assignment history exceeds its inspection bound')
    records = [_record(scope, row, db=db, context=context, artifact_refs=artifact_refs) for row in rows]
    for record in records:
        owner, body = record['owner'], record['body']
        if state._retired(db, 'assignment', owner) or state._retired_assignment(db, record['id']):
            raise state.StateError('Live assignment conflicts with a retired identity marker')
        original = state._task(db, owner['scope_id'], owner['client'], owner['session_id'], owner['task_id'])
        _require(original is not None and original['pins'] == body['pins'] and
            (body['pins']['schema_version'] != 3 or original['accepted_identity'] == body['accepted_identity']),
            'Assignment no longer matches its immutable original task')
        if record['data']['candidate'] is not None:
            evidence._candidate_projection(read(record['data']['candidate']), original)
    return records


def _ready_closure(scope, task):
    try:
        verification = evidence._read(scope, task['evidence']['verification'])
        claims = _stored_claims(verification['resource_closure'], maximum=MAX_VERIFICATION_CLAIMS)
        _require(all(c['mode'] == 'read' for c in claims), 'Ready closure must be read-only')
        return claims
    except (state.StateError, KeyError, TypeError, ValueError):
        return None


def _invalidate_ready(db, scope, task, claims):
    rows = db.execute('select scope_id,client,session_id,task_id from tasks limit ?', (MAX_ASSIGNMENTS + 1,)).fetchall()
    _require(len(rows) <= MAX_ASSIGNMENTS, 'Shared task history exceeds its inspection bound')
    owner = (state._scope_id(scope), task['client'], task['session_id'], task['task_id'])
    for scope_id, client, session, task_id in rows:
        if (scope_id, client, session, task_id) == owner:
            continue
        current = state._task(db, scope_id, client, session, task_id)
        if current['state'] != 'DONE' or current.get('lifecycle',{}).get('safe_closed_at') is not None:
            continue
        closure = _ready_closure(scope, current)
        if closure is None or _held_conflict(claims, closure):
            current.update(state='REVIEW', reason='Conflicting reservation requires fresh completion evidence', reference='delegation:ownership')
            current['revision'] += 1
            state._save(db, scope_id, current)


def _insert_assignment(db, scope, task, record):
    marker = state._retired(db, 'assignment', record['owner'])
    if marker is not None:
        _require(marker['request_digest'] == record['data']['request_digest'], 'Retired dispatch was reused with changed request bytes')
        raise state.StateError('Retired dispatch cannot create a new assignment; evidence is unavailable or expired')
    if state._retired_assignment(db, record['id']):
        raise state.StateError('Retired native assignment ID cannot be reused')
    _require(db.execute('select count(*) from assignments').fetchone()[0] < MAX_ASSIGNMENTS,
             'Shared assignment history exceeds its insertion bound')
    owner, data = record['owner'], record['data']
    _require(data['revision'] == task['revision'] + 1 and owner == dict(scope_id=state._scope_id(scope),
        **{key: task[key] for key in ('client', 'session_id', 'task_id')}, dispatch_id=owner['dispatch_id']),
        'Reservation owner or revision differs from the current task')
    row = (record['id'], owner['scope_id'], owner['client'], owner['session_id'], owner['task_id'],
           owner['dispatch_id'], evidence._raw(data).decode())
    _record(scope, row, db=db)
    _invalidate_ready(db, scope, task, data['claims'])
    db.execute('insert into assignments values(?,?,?,?,?,?,?)', row)
    if data['role'] in NATIVE_ROLES:
        task['delegation']['attempts_consumed'] += 1
        task['delegation']['resource_units_consumed'] += data['resource_units']
    if task['state'] == 'DONE':
        task['state'] = 'REVIEW'
    task['revision'] += 1


def _semantic(request):
    return {key: value for key, value in request.items() if key != 'expected_revision'}


def _reply(record, admitted=False):
    data, body = record['data'], record['body']
    workstream = 'workstream_revision' in body['request']
    return dict(ok=True, attempt_id=record['id'], dispatch_id=record['owner']['dispatch_id'],
        task_name=data['binding']['task_name'], assignment_digest=data['assignment']['digest'],
        candidate_digest=data['candidate']['digest'], prompt=evidence._raw(dict(
            assignment_digest=data['assignment']['digest'], assignment=body)).decode(),
        requested_model=data['binding']['requested_model'], requested_effort=data['binding']['requested_effort'],
        revision=data['revision'], status=data['status'], launch_admitted=admitted and not workstream, replayed=not admitted,
        **(dict(dispatch_prepared=True) if workstream else {}),
        limitations=list(ADMISSION_LIMITATIONS))



def _retired_dispatch(scope, db, request):
    owner = dict(scope_id=state._scope_id(scope), **{k:request[k] for k in ('client','session_id','task_id','dispatch_id')})
    marker = state._retired(db, 'assignment', owner)
    if marker is not None:
        _require(marker['request_digest'] == evidence._digest(_semantic(request)), 'Retired dispatch was reused with changed request bytes')
        raise state.StateError('Retired dispatch evidence is unavailable or expired; no new launch is admitted')


def _replay(scope, records, request):
    owner = dict(scope_id=state._scope_id(scope), **{key: request[key] for key in ('client', 'session_id', 'task_id', 'dispatch_id')})
    found = next((r for r in records if r['owner'] == owner), None)
    if found:
        _require(found['data']['request_digest'] == evidence._digest(_semantic(request)),
                 'Dispatch request identity was reused with a changed payload')
    return found


def _brief(scope, job, brief, proof_plan):
    state._fields(brief, ('objective', 'context', 'instructions', 'files', 'resources', 'dependencies',
                         'acceptance', 'checks', 'self_review', 'stop_conditions'))
    for field in ('objective', 'context', 'self_review'):
        state._text(brief[field], 'worker brief ' + field, 8192)
    for field in ('instructions', 'files', 'dependencies', 'acceptance', 'checks', 'stop_conditions'):
        _strings(brief[field], 'worker brief ' + field, maximum=len(job['dependencies']) if field == 'dependencies' else 64, limit=4096,
                 nonempty=field in {'instructions', 'acceptance', 'checks', 'stop_conditions'})
    _require(brief['resources'] == job['claims'] and brief['dependencies'] == job['dependencies'],
             'Worker brief differs from its exact planned resources/dependencies')
    claims = _claims(brief['resources'])
    for name in brief['files']:
        evidence._relative(name)
        _require(name != '.', 'Worker files must name exact scoped files')
        file_claim = _claims([dict(kind='path', key=str(scope.repository_root / name), mode='read')])[0]
        _require(any(_covers(c, file_claim) for c in claims), 'Worker brief names an unreserved file')
    _require(set(brief['checks']) <= {check['id'] for check in proof_plan['checks']}, 'Unknown worker acceptance check')
    files = [_claims([dict(kind='path', key=str(scope.repository_root / name), mode='read')])[0]
             for name in brief['files']]
    applicable = {check['id'] for check in proof_plan['checks'] if not files or not check['dependencies'] or any(
        _overlap(file, dependency) for file in files for dependency in _claims([
            dict(kind='path', key=str(scope.repository_root / name), mode='read') for name in check['dependencies']]))}
    _require(applicable <= set(brief['checks']), 'Worker brief cannot omit an applicable accepted check')
    return claims


def _discovery(scope, plan, request):
    from runtime.adapters.codex import discover_native_workers
    controls = request['control_observation']
    state._fields(controls, ('version', 'client_surface', 'native_version', 'parent_session_id', 'dispatch_controls'))
    route = plan['route']
    _require(all(controls[key] == route[key] for key in ('client_surface', 'parent_session_id')),
             'Native controls do not match the prepared route')
    observed = discover_native_workers(scope, controls)
    expected = request['discovery_expectation']
    state._fields(expected, ('capability_digest', 'client_surface', 'native_version', 'parent_session_id', 'parent_model_provider'))
    _require(all(observed.get(key) == value for key, value in expected.items()) and
             all(observed.get(key) == controls[key] for key in ('client_surface', 'native_version', 'parent_session_id')) and
             observed['dispatch_controls'] == controls['dispatch_controls'], 'Actual native capabilities changed from the prepared expectation')
    dispatch = observed['dispatch_controls']
    _require(dispatch['spawn'] is True and all(dispatch.get(key) is True for key in
             plan['selection_constraints']['required_controls']), 'Required exposed native dispatch control is unavailable')
    selected = request['selection']
    state._fields(selected, ('model', 'effort', 'rationale', 'profile_ids', 'mode'), ('inheritance',))
    for field in ('model', 'effort', 'rationale'):
        state._text(selected[field], 'worker selection ' + field, 128 if field == 'model' else 1024)
    _strings(selected['profile_ids'], 'selected guidance profiles', maximum=32, limit=64, nonempty=True)
    model = next((item for item in observed['catalog'] if item['model'] == selected['model']), None)
    _require(model is not None and selected['effort'] in model['supported_efforts'], 'Selected model/effort is not in the actual native catalog')
    _require(selected['mode'] in {'explicit', 'inherit'}, 'Unsupported worker settings mode')
    if selected['mode'] == 'explicit':
        _require('inheritance' not in selected and all(dispatch.get(key) is True for key in
                 ('explicit_model', 'explicit_effort', 'fork_turns_none')), 'Explicit native settings are unsupported by exposed controls')
    else:
        inheritance = selected.get('inheritance')
        state._fields(inheritance, ('model', 'effort', 'reference'))
        _require(inheritance['model'] == selected['model'] and inheritance['effort'] == selected['effort'],
                 'Documented inheritance does not match the suitable selected settings')
        state._text(inheritance['reference'], 'documented native inheritance evidence')
    guidance_request = dict(client_surface=observed['client_surface'], applicability=dict(
        client_version=observed['native_version'], capability_fingerprint=observed['capability_digest']),
        profile_ids=selected['profile_ids'])
    guidance = guidance_status(scope, guidance_request)
    state._fields(request['guidance'], ('snapshot_ref', 'snapshot_digest', 'checked_at', 'freshness'))
    _require(guidance['usable'] and all(guidance[key] == value for key, value in request['guidance'].items()) and
             {p['id'] for p in guidance['recommendations']} == set(selected['profile_ids']),
             'Selected guidance is missing, incompatible, changed or lacks the selected profiles')
    return observed, guidance


def _admission(scope, db, task, plan, job, claims, records, request):
    if job['role'] == 'worker':
        _worker_scope(scope, job['claims'], request['brief']['files'])
    if 'workstream_revision' in request or 'workstream_limits' in request:
        from runtime.workstreams import validate_admission
        validate_admission(scope, request)
    limits = plan['limits']
    _require(task['state'] not in {'BLOCKED', 'WAITING', 'PAUSED', 'CANCELLED'} and not any(
        c['kind'] in {'blocker', 'decision'} and c['status'] == 'pending' for c in task['conditions']),
        'Task blockers or pending decisions prevent worker dispatch')
    _require(task['mode'] == 'editable' or all(c['mode'] == 'read' for c in claims), 'Read-only task cannot admit mutable worker claims')
    _require(limits['deadline'] > _now(), 'Original delegation deadline has elapsed')
    owner_history = [r for r in records if all(r['owner'][key] == value for key, value in
        dict(scope_id=state._scope_id(scope), **{key: task[key] for key in ('client', 'session_id', 'task_id')}).items())]
    own = [r for r in owner_history if r['data']['role'] in NATIVE_ROLES]
    counters = task['delegation']
    _require(len(own) == counters['attempts_consumed'] and sum(r['data']['resource_units'] for r in own) == counters['resource_units_consumed'],
             'Original consumed delegation budgets disagree with immutable history')
    _require(counters['attempts_consumed'] < limits['max_attempts'] and
             counters['resource_units_consumed'] + request['resource_units'] <= limits['max_resource_units'],
             'Original finite delegation budget is exhausted')
    validate_dependencies(scope, task, plan, job, records)
    previous = [r for r in own if r['data']['job_id'] == job['id']]
    _require(len(previous) < job['max_attempts'], 'Original job attempt budget is exhausted')
    for dependency in job['dependencies']:
        completed = [r for r in owner_history if r['data']['job_id'] == dependency]
        latest = max(completed, key=lambda r: r['data']['revision']) if completed else None
        prerequisite = next(item for item in plan['jobs'] if item['id'] == dependency)
        _require(latest is not None and latest['data']['role'] == prerequisite['role'] and
                 latest['data']['status'] == 'settled' and latest['data']['result']['outcome'] == 'completed',
                 'Required delegation dependency is not reconciled and complete')
    if previous:
        latest = max(previous, key=lambda r: r['data']['revision'])
        _require(request.get('retry_of') == latest['id'] and latest['data']['status'] == 'settled',
                 'Retry requires the exact original reconciled admission')
        diagnostic = request.get('diagnostic')
        state._fields(diagnostic, ('prior_attempt_id', 'cause', 'evidence', 'change'))
        _require(diagnostic['prior_attempt_id'] == latest['id'], 'Retry diagnosis belongs to another attempt')
        for field in ('cause', 'change'):
            state._text(diagnostic[field], 'retry ' + field, 4096)
        _strings(diagnostic['evidence'], 'new retry evidence', nonempty=True)
        prior_evidence = {entry for r in previous if r['data']['diagnostic'] is not None
                          for entry in evidence._read(scope, r['data']['diagnostic'])['evidence']}
        _require(set(diagnostic['evidence']) - prior_evidence, 'An unchanged retry needs new diagnostic evidence')
    else:
        _require('retry_of' not in request and 'diagnostic' not in request, 'First admission cannot invent a retry predecessor')
    unresolved = [r for r in records if r['data']['status'] != 'settled']
    managed = [r for r in unresolved if r['data']['role'] in {'worker', 'review'}]
    _require(sum(r in own for r in managed) < limits['max_parallel_workers'] and
             len(managed) < min([limits['host_capacity']] + [r['data']['host_capacity'] for r in managed]),
             'Managed worker capacity is exhausted, including uncertain historical admissions')
    _resources_available(scope, db, claims, records)


def _check_claims(scope, definition):
    if 'resources' in definition:
        return _claims(definition['resources'])
    _require(definition['effects']['kind'] == 'read-only', 'Mutable checks require declared resources')
    if definition['dependencies']:
        return _claims([dict(kind='path', key=str(scope.repository_root / path), mode='read')
                        for path in definition['dependencies']])
    return _claims([dict(kind='path', key=str(scope.repository_root), mode='write')])


def _linked_check(scope, scope_id, task, attempt, records, *, context=None, db=None, artifact_refs=None):
    read = partial(_historical_read, scope, context, db, artifact_refs)
    if attempt.get('kind') != 'check' or 'ledger_assignment_digest' not in attempt:
        return None
    found = next((r for r in records if r['id'] == attempt['id']), None)
    owner = dict(scope_id=scope_id, **{key: task[key] for key in ('client', 'session_id', 'task_id')})
    _require(found is not None and all(found['owner'][key] == value for key, value in owner.items()) and
        found['data']['role'] == 'check' and found['data']['job_id'] == attempt['check_id'] and
        found['owner']['dispatch_id'] == attempt['dispatch_id'] and
        found['data']['assignment']['digest'] == attempt['ledger_assignment_digest'] and
        evidence._digest(found['data']['claims']) == attempt['ledger_claims_digest'] and
        found['data']['candidate']['digest'] == attempt['candidate_digest'] and
        found['data']['plan_digest'] == attempt['plan_digest'] and
        found['data']['observation'] == attempt.get('execution'), 'Check ledger link is absent or corrupt')
    _require(attempt['candidate'] == found['data']['candidate'], 'Check candidate reference changed')
    if attempt.get('execution'):
        executed = read(attempt['execution'])
        definition = next(check for check in read(found['body']['d5_plan'])['checks'] if check['id'] == attempt['check_id'])
        _require(executed['command'] == definition and executed['result']['id'] == attempt['id'] and
            executed['result']['candidate_digest'] == attempt['candidate_digest'] and
            executed['tool'] == read(attempt['candidate'])['tools'][attempt['check_id']],
            'Check link does not bind actual command/execution')
    if attempt['status'] in {'reserved', 'running', 'uncertain'}:
        _require(found['data']['status'] != 'settled', 'Unresolved check cannot adopt settled ledger ownership')
    return found


def _linked_review(scope, scope_id, task, attempt, records, *, context=None, db=None, artifact_refs=None):
    read = partial(_historical_read, scope, context, db, artifact_refs)
    if attempt.get('kind') != 'review' or 'ledger_assignment_digest' not in attempt:
        return None
    record = next((r for r in records if r['id'] == attempt['id']), None)
    owner = dict(scope_id=scope_id, **{key: task[key] for key in ('client', 'session_id', 'task_id')})
    _require(record is not None and record['data']['role'] == 'review' and
        all(record['owner'][key] == value for key, value in owner.items()), 'Review ledger owner is absent or corrupt')
    data, review = record['data'], record['body']['review']
    _require(data['assignment']['digest'] == attempt['ledger_assignment_digest'] and
        evidence._digest(data['claims']) == attempt['ledger_claims_digest'] and
        record['owner']['dispatch_id'] == attempt['dispatch_id'] and data['job_id'] == attempt['job_id'] and
        data['candidate'] == attempt['candidate'] and data['candidate']['digest'] == attempt['candidate_digest'] and
        review['d5_plan']['digest'] == attempt['plan_digest'] and review['assignment_digest'] == attempt['assignment_digest'] and
        read(attempt['assignment']) == dict(prompt=record['body']['request']['assignment']) and
        all(review[key] == attempt[key] for key in ('coverage', 'criteria')) and
        data['binding']['task_name'] == attempt['task_name'] == _native_name(attempt['id'], 'review', record['body']['request']), 'Review D5/D6 immutable link changed')
    if attempt.get('ownership_reconciliation') is not None:
        _require(data['status'] == 'settled' and attempt['ownership_reconciliation'] == data['reconciliation'],
                 'Review ownership reconciliation link changed')
    return record


def _linked_attempt(scope, scope_id, task, attempt, records, *, context=None, db=None, artifact_refs=None):
    return (_linked_review if attempt.get('kind') == 'review' else _linked_check)(scope, scope_id, task, attempt, records, context=context, db=db, artifact_refs=artifact_refs)


def _resources_available(scope, db, claims, records):
    for record in records:
        if record['data']['status'] == 'settled':
            continue
        _require(not _held_conflict(claims, record['data']['claims']),
                 'Canonical resource ownership conflicts with an unresolved assignment')
    # Unlinked D5 attempts retain unknown ownership; exact linked checks use their ledger claims.
    rows = db.execute('select scope_id,client,session_id,task_id from tasks limit ?', (MAX_ASSIGNMENTS + 1,)).fetchall()
    _require(len(rows) <= MAX_ASSIGNMENTS, 'Shared task history exceeds its inspection bound')
    for scope_id, client, session, task_id in rows:
        record = state._task(db, scope_id, client, session, task_id)
        if record.get('retirement'):
            evidence._read(scope, record['retirement']['proof'])
        else:
            for attempt in record.get('evidence', {}).get('attempts', []):
                if attempt['status'] in {'reserved', 'running', 'uncertain'}:
                    _require(_linked_attempt(scope, scope_id, record, attempt, records) is not None,
                             'Unreconciled D5 attempt retains unknown shared writer ownership')


@contextmanager
def _reservation_write(scope):
    from runtime.changes import _authority_guards
    try:
        with _authority_guards([scope.runtime.state_dir]):
            with state._database(scope, write=True) as db:
                yield db
    except state.StateError:
        raise
    except StartupError as error:
        raise state.StateError(str(error)) from None


def _operation_reply(record, admitted=False):
    return dict(ok=True, attempt_id=record['id'], dispatch_id=record['owner']['dispatch_id'],
                revision=record['data']['revision'], status=record['data']['status'],
                reservation_admitted=admitted, replayed=not admitted, limitations=list(ADMISSION_LIMITATIONS))


def _operation_record(scope, task, request, role, claims, identity, candidate, operation, job_id=None, *, byte_limit=MAX_INPUT, routing=None):
    d5_plan = task.get('evidence', {}).get('plan')
    d6_plan = task.get('delegation', {}).get('plan') if role == 'coordinator' else None
    owner = dict(scope_id=state._scope_id(scope), **{key: request[key] for key in ('client', 'session_id', 'task_id', 'dispatch_id')})
    operation = evidence._artifact(scope, dict(operation, decision_ref=task['decision_ref'], claims_digest=evidence._digest(claims)), owner=evidence._artifact_owner(scope, task))
    body = dict(version=ADMISSION_VERSION, owner=owner, pins=task['pins'], scope_ref=task['scope_ref'], decision_ref=task['decision_ref'],
        request=_semantic(request), operation=operation, job_id=job_id, role=role,
        claims=claims, resource_units=0, revision=request['expected_revision'] + 1, plan_digest=d5_plan['digest'] if d5_plan else None,
        candidate=candidate, d5_plan=d5_plan, d6_plan=d6_plan, host_capacity=None, binding=None, retry_of=None, diagnostic=None,
        policy=evidence._artifact(scope, dict(policy_digest=task['pins']['policy_digest'], **(routing or {})), owner=evidence._artifact_owner(scope, task)))
    if task['pins']['schema_version'] == 3:
        body['accepted_identity'] = task['accepted_identity']
        body['lifecycle'] = dict(created_at=state._owner_time())
    evidence._bounded(body, byte_limit)
    reference = evidence._artifact(scope, body, owner=evidence._artifact_owner(scope, task))
    data = {key: body[key] for key in ('version', 'job_id', 'role', 'claims', 'resource_units', 'revision', 'plan_digest',
                                      'candidate', 'host_capacity', 'binding', 'retry_of', 'diagnostic')}
    data.update(status='prepared', request_digest=evidence._digest(_semantic(request)), assignment=reference,
                observation=None, reconciliation=None, result=None)
    if 'lifecycle' in body:
        data['lifecycle'] = body['lifecycle']
    record = dict(id=identity, owner=owner, data=data, body=body)
    return record


def _coordinator_job(scope, task, request, claims, records):
    if not task.get('delegation'):
        return None
    plan = _task_plan(scope, task)
    selected = next((job for job in plan['jobs'] if job['id'] == request['operation']['id']), None)
    applicable = [job for job in plan['jobs'] if job['role'] == 'coordinator' and _held_overlap(claims, _claims(job['claims']))]
    if selected is None and not applicable:
        return None
    _require(selected is not None and selected['role'] == 'coordinator' and _claims(selected['claims']) == claims,
             'Operation must match the exact applicable coordinator job')
    _require(plan['limits']['deadline'] > _now(), 'Original delegation deadline has elapsed')
    owner = dict(scope_id=state._scope_id(scope), **{key: task[key] for key in ('client', 'session_id', 'task_id')})
    history = [r for r in records if all(r['owner'][key] == value for key, value in owner.items())]
    validate_dependencies(scope, task, plan, selected, records)
    _require(sum(r['data']['role'] == 'coordinator' and r['data']['job_id'] == selected['id'] for r in history) < selected['max_attempts'],
             'Original coordinator job attempt budget is exhausted')
    for dependency in selected['dependencies']:
        prior = [r for r in history if r['data']['job_id'] == dependency]
        latest = max(prior, key=lambda r: r['data']['revision']) if prior else None
        required = next(job for job in plan['jobs'] if job['id'] == dependency)
        _require(latest is not None and latest['data']['role'] == required['role'] and latest['data']['status'] == 'settled' and
            latest['data']['result']['outcome'] == 'completed', 'Coordinator job dependency remains unresolved')
    return selected['id']


def _reserve_operation(scope, request, *, role, _verification=False):
    """Private model-free kernel; callers later attach actual D5 execution/operation lifecycles."""
    _require(role in {'coordinator', 'check'}, 'Unsupported model-free role')
    byte_limit = evidence.MAX_ARTIFACT if _verification else MAX_INPUT
    claim_limit = MAX_VERIFICATION_CLAIMS if _verification else MAX_CLAIMS
    evidence._request(request, ('dispatch_id', 'claims', 'operation', 'reason', 'ownership_ref', 'stop_conditions'),
                      ('verification',) if _verification else (), limit=byte_limit)
    request = json_object(evidence._raw(request), limit=byte_limit)
    for field in ('dispatch_id', 'reason', 'ownership_ref'):
        state._text(request[field], 'operation ' + field)
    _strings(request['stop_conditions'], 'operation stop conditions', nonempty=True)
    state._fields(request['operation'], ('id', 'kind'))
    state._text(request['operation']['id'], 'operation id', 128)
    expected_kind = 'completion-verification' if _verification else 'check' if role == 'check' else 'coordinator-work'
    _require(request['operation']['kind'] == expected_kind, 'Operation role/kind mismatch')
    claims = _claims(request['claims'], maximum=claim_limit)
    if _verification:
        _require(role == 'coordinator' and all(c['mode'] == 'read' for c in claims), 'Verifier barrier must be read-only')
        state._fields(request['verification'], ('purpose', 'git_directory', 'git_routing'))
    with state._database(scope) as db:
        _require(db is not None, 'Runtime task state is missing')
        _retired_dispatch(scope, db, request)
        state._assert_open(state._task(db,state._scope_id(scope),request['client'],request['session_id'],request['task_id']))
        records = _ledger(scope, db)
        replay = _replay(scope, records, request)
        if replay:
            _require(replay['data']['role'] == role, 'Dispatch identity belongs to another role')
            return _operation_reply(replay)
        task = evidence._task(db, scope, request)
        _require((_verification or task['state'] not in {'BLOCKED', 'WAITING', 'PAUSED'}) and
                 (task['mode'] == 'editable' or all(c['mode'] == 'read' for c in claims)), 'Task cannot reserve this operation')
        _resources_available(scope, db, claims, records)
        d5_plan = task.get('evidence', {}).get('plan')
        candidate = task.get('evidence', {}).get('candidate')
        d6_plan = task.get('delegation', {}).get('plan')
        job_id = _coordinator_job(scope, task, request, claims, records) if role == 'coordinator' and not _verification else None
        if role == 'check':
            _require(d5_plan is not None and candidate is not None, 'Check requires its actual accepted D5 evidence')
            definition = next((c for c in evidence._read(scope, d5_plan)['checks'] if c['id'] == request['operation']['id']), None)
            _require(definition is not None, 'Check operation is not in the accepted D5 plan')
            expected = _check_claims(scope, definition)
            _require(claims == expected, 'Check claims differ from accepted D5 resources')
    record = _operation_record(scope, task, request, role, claims, uuid.uuid4().hex, candidate, request['operation'],
        request['operation']['id'] if role == 'check' else job_id, byte_limit=byte_limit)
    with _reservation_write(scope) as db:
        _retired_dispatch(scope, db, request)
        state._assert_open(state._task(db,state._scope_id(scope),request['client'],request['session_id'],request['task_id']))
        records = _ledger(scope, db)
        replay = _replay(scope, records, request)
        if replay:
            _require(replay['data']['role'] == role, 'Dispatch identity belongs to another role')
            return _operation_reply(replay)
        task = evidence._task(db, scope, request)
        _require(_claims(request['claims'], maximum=claim_limit) == claims, 'Operation physical resources changed')
        if _verification:
            routing = request['verification']
            _require(_git_routing(scope, routing['git_directory']) == routing['git_routing'],
                     'Verification Git routing changed before barrier reservation')
        _require(task.get('evidence', {}).get('plan') == d5_plan and
            task.get('evidence', {}).get('candidate') == candidate and task.get('delegation', {}).get('plan') == d6_plan,
            'Accepted operation context changed')
        _resources_available(scope, db, claims, records)
        if role == 'coordinator' and not _verification:
            _require(_coordinator_job(scope, task, request, claims, records) == job_id, 'Coordinator job binding changed')
        _insert_assignment(db, scope, task, record)
        state._save(db, state._scope_id(scope), task)
    return _operation_reply(record, admitted=True)


def reserve_coordinator(scope, request):
    """Reserve already authorized model-free work; this grants no execution permission."""
    return _reserve_operation(scope, request, role='coordinator')


def _validate_operation_proof(scope, record, task, proof, *, current_candidate_digest=None):
    return _validate_role_proof(record, task, proof, scope_id=state._scope_id(scope), revision=task['revision'],
        read=lambda reference: evidence._read(scope, reference),
        linked_review=lambda attempt: _linked_review(scope, state._scope_id(scope), task, attempt, [record]),
        current_candidate_digest=current_candidate_digest)


def _validate_role_proof(record, task, proof, *, scope_id, revision, read, linked_review, current_candidate_digest=None):
    """Validate exact role bindings only; never authenticate host authority or release ownership."""
    evidence._bounded(proof)
    data, body, owner = record['data'], record['body'], record['owner']
    common = ('version', 'kind', 'identity', 'expected_pins', 'expected_revision', 'scope_id', 'attempt_id',
              'dispatch_id', 'assignment_digest', 'claims_digest', 'observation_ref', 'coordinator_ref')
    role = data['role']
    if role == 'coordinator':
        required = (*common, 'decision_ref', 'operation_id', 'operation_digest', 'operation_evidence',
                    'resource_observations', 'operation_stopped', 'operation_descendants_stopped')
        required += tuple(key for key, reference in (('d5_plan_digest', body['d5_plan']),
            ('d6_plan_digest', body['d6_plan']), ('candidate_digest', data['candidate']),
            ('current_candidate_digest', data['candidate'])) if reference is not None)
        kind = 'coordinator-operation-reconciliation'
    elif role == 'check':
        required = (*common, 'candidate_digest', 'current_candidate_digest', 'plan_digest', 'execution_digest', 'stopped', 'descendants_stopped')
        kind = 'check-writer-reconciliation'
    else:
        required = (*common, 'candidate_digest', 'current_candidate_digest', 'd6_plan_digest', 'observation_digest',
                    'result_digest', 'native_identity', 'launch_state', 'stopped', 'descendants_stopped')
        kind = 'assignment-writer-reconciliation'
    state._fields(proof, required)
    state._integer(proof['expected_revision'], 'proof task revision', 1)
    state._validate_pins(proof['expected_pins'])
    state._integer(proof['expected_pins']['schema_version'], 'proof schema version', 1)
    _require(all(task[key] == owner[key] == body['request'][key] for key in ('client', 'session_id', 'task_id')) and
        task['scope_ref'] == body['request']['scope_ref'], 'Proof task differs from the original reservation')
    _require(type(proof['version']) is int and proof['version'] == 2 and proof['kind'] == kind and
        proof['identity'] == {key: task[key] for key in evidence.IDENTITY[:4]} and
        proof['expected_pins'] == task['pins'] == body['pins'] and proof['expected_revision'] == revision and
        proof['scope_id'] == owner['scope_id'] == scope_id and proof['attempt_id'] == record['id'] and
        proof['dispatch_id'] == owner['dispatch_id'] and proof['assignment_digest'] == data['assignment']['digest'] and
        proof['claims_digest'] == evidence._digest(data['claims']), 'Host proof does not bind the exact original reservation')
    for key in ('observation_ref', 'coordinator_ref'):
        state._text(proof[key], 'host ' + key)
    if role == 'coordinator':
        for key in ('d5_plan', 'd6_plan'):
            if body[key] is not None:
                _require(proof[key + '_digest'] == body[key]['digest'], 'Coordinator proof changed original plan context')
        if data['candidate'] is not None:
            _require(_hex(current_candidate_digest) and proof['current_candidate_digest'] == current_candidate_digest and
                proof['candidate_digest'] == data['candidate']['digest'], 'Coordinator proof requires actual candidate context')
        operation = read(body['operation'])
        _require(proof['decision_ref'] == body['decision_ref'] == task['decision_ref'] and
            proof['operation_id'] == operation['id'] and proof['operation_digest'] == body['operation']['digest'] and
            proof['operation_stopped'] is True and proof['operation_descendants_stopped'] is True,
            'Coordinator operation or its descendants remain unverified')
        for key in ('operation_evidence', 'resource_observations'):
            _reference(proof[key])
            observed = read(proof[key])
            _require(all(observed.get(field) == proof[field] for field in ('operation_id', 'operation_digest', 'claims_digest')),
                     'Independent evidence belongs to different operation resources')
    else:
        _require(_hex(current_candidate_digest) and proof['current_candidate_digest'] == current_candidate_digest and
            proof['candidate_digest'] == data['candidate']['digest'] and proof['stopped'] is True and
            proof['descendants_stopped'] is True, 'Exact candidate and stopped-descendant proof is required')
        if role == 'check':
            execution = data['observation']['digest'] if data['observation'] else None
            _require(proof['plan_digest'] == data['plan_digest'] and proof['execution_digest'] == execution,
                     'Check proof differs from actual D5 plan/execution')
        else:
            if role == 'review':
                attempt = next((a for a in task['evidence']['attempts'] if a['id'] == record['id']), None)
                _require(attempt is not None and linked_review(attempt) is not None,
                         'Review proof has no exact immutable D5 attempt/plain-prompt binding')
            observation = read(data['observation']) if data['observation'] else None
            receipt = observation['receipt'] if observation else {}
            _require(isinstance(receipt, dict), 'No actual native identity was observed; missing history cannot prove never-started')
            binding = data['binding']
            native = {key: binding[key] for key in ('client_surface', 'native_version', 'parent_session_id', 'task_name')}
            native.update({key: receipt.get(key) for key in ('parent_turn_id', 'spawn_item_id', 'worker_id', 'worker_turn_id', 'result_item_id')})
            _require(proof['d6_plan_digest'] == data['plan_digest'] and proof['native_identity'] == native and
                proof['observation_digest'] == (data['observation']['digest'] if data['observation'] else None) and
                proof['result_digest'] == receipt.get('result_digest') and
                proof['launch_state'] == ('observed' if observation else 'not-started'), 'Native host proof differs from observed facts')
    return proof


def prepare_assignment(scope, request):
    """Reserve a worker only; D5 review preparation owns the sole review admission path."""
    return _prepare_native(scope, request, role='worker')


def _prepare_native(scope, request, *, role):
    """Reserve before native spawn, returning launch admission only on a new committed row."""
    evidence._request(request, ('dispatch_id', 'job_id', 'selection', 'control_observation',
        'discovery_expectation', 'guidance', 'resource_units', *(('brief',) if role == 'worker' else
        ('coverage', 'criteria', 'assignment'))), ('retry_of', 'diagnostic', 'workstream_revision', 'workstream_limits', 'workstream_context_id', 'continuation_limits'))
    request = json_object(evidence._raw(request))
    if 'continuation_limits' in request:
        _continuation_limits(request)
    if 'workstream_context_id' in request:
        _require(role in {'worker', 'review'} and isinstance(request['workstream_context_id'], str), 'Visible native grouping requires an immutable context ID')
        _native_name('0' * 32, role, request)
    if 'workstream_revision' in request or 'workstream_limits' in request:
        from runtime.workstreams import validate_admission
        _require(role == 'worker', 'Workstreams launch gates currently support implementer workers only')
        validate_admission(scope, request)
    state._text(request['dispatch_id'], 'stable dispatch id', 128)
    state._text(request['job_id'], 'delegation job id', 128)
    state._integer(request['resource_units'], 'admitted resource units', 1)
    with state._database(scope) as db:
        _require(db is not None, 'Runtime task state is missing')
        _retired_dispatch(scope, db, request)
        state._assert_open(state._task(db,state._scope_id(scope),request['client'],request['session_id'],request['task_id']))
        records = _ledger(scope, db)
        replay = _replay(scope, records, request)
        if replay:
            _require(replay['data']['role'] == role, 'Dispatch identity belongs to another role')
            return _reply(replay) if role == 'worker' else evidence._review_response(scope, db, request, replay)
        task = evidence._task(db, scope, request)
        plan = _task_plan(scope, task)
        proof_plan = evidence._read(scope, task['evidence']['plan'])
        plan_digest = task['delegation']['plan']['digest']
        generation = db.execute('select generation from checkouts where scope_id=?', (state._scope_id(scope),)).fetchone()[0]
        job = next((j for j in plan['jobs'] if j['id'] == request['job_id']), None)
        _require(job is not None and job['role'] == role, 'This admission path requires its exact planned native role')
        if role == 'worker':
            claims = _brief(scope, job, request['brief'], proof_plan)
        else:
            claims = _claims(job['claims'])
            evidence._review_admission(scope, task, proof_plan, request)
            _require(all(claim['mode'] == 'read' for claim in claims), 'Review resources must be read-only')
        _admission(scope, db, task, plan, job, claims, records, request)
    _require(_git_directory(scope) == plan['git_directory'], 'Prepared worktree Git metadata changed')
    git_routing = _git_routing(scope, plan['git_directory'])
    with state._database(scope) as db:
        evidence._task(db,scope,request)
    observed, guidance = _discovery(scope, plan, request)
    candidate = evidence.capture_candidate(scope, proof_plan)
    _require(candidate['actual_policy_digest'] == task['pins']['policy_digest'], 'Actual policy changed from accepted task pins')
    candidate_ref = evidence._artifact(scope, candidate, owner=evidence._artifact_owner(scope, task))
    attempt_id = uuid.uuid4().hex
    binding = dict(attempt_id=attempt_id, parent_session_id=plan['route']['parent_session_id'], task_name=_native_name(attempt_id, role, request),
        candidate_digest=candidate_ref['digest'], requested_model=request['selection']['model']
        if request['selection']['mode'] == 'explicit' else None, requested_effort=request['selection']['effort']
        if request['selection']['mode'] == 'explicit' else None, client_surface=observed['client_surface'], native_version=observed['native_version'])
    # The content digest excludes itself; the accessor supplies the immutable artifact digest.
    content_binding = binding
    diagnostic = evidence._artifact(scope, request['diagnostic'], owner=evidence._artifact_owner(scope, task)) if 'diagnostic' in request else None
    owner = dict(scope_id=state._scope_id(scope), **{key: request[key] for key in ('client', 'session_id', 'task_id', 'dispatch_id')})
    body = dict(version=ADMISSION_VERSION, owner=owner, pins=task['pins'], request=_semantic(request), job_id=job['id'], role=role,
        claims=claims, resource_units=request['resource_units'], revision=request['expected_revision'] + 1,
        plan=task['delegation']['plan'], plan_digest=plan_digest, git_routing=git_routing,
        host_capacity=plan['limits']['host_capacity'], binding=content_binding, candidate=candidate_ref,
        discovery=evidence._artifact(scope, observed, owner=evidence._artifact_owner(scope, task)), policy=evidence._artifact(scope, dict(
            policy_digest=task['pins']['policy_digest'], actual_policy_digest=candidate['actual_policy_digest'],
            files=[dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                   for path in sorted((state.PACKAGE / 'policies').glob('*.md'))]), owner=evidence._artifact_owner(scope, task)),
        report_contract=dict(version=1, status=['completed', 'blocked', 'pending-decision'],
            required=['version', 'attempt_id', 'assignment_digest', 'candidate_digest', 'status', 'changed_paths', 'evidence', 'limitations']),
        guidance={key: guidance[key] for key in ('snapshot_ref', 'snapshot_digest', 'checked_at', 'freshness')},
        retry_of=request.get('retry_of'), diagnostic=diagnostic)
    if task['pins']['schema_version'] == 3:
        body['accepted_identity'] = task['accepted_identity']
        body['lifecycle'] = dict(created_at=state._owner_time())
    if role == 'review':
        body['review'] = dict(assignment_digest=hashlib.sha256(request['assignment'].encode()).hexdigest(),
            coverage=request['coverage'], criteria=request['criteria'], d5_plan=task['evidence']['plan'])
        body['report_contract'] = dict(version=1, assignment_digest=body['review']['assignment_digest'],
            required=['version', 'attempt_id', 'assignment_digest', 'candidate_digest', 'coverage', 'findings', 'limitations'])
    evidence._bounded(body)
    assignment_ref = evidence._artifact(scope, body, owner=evidence._artifact_owner(scope, task))
    data = dict(version=ADMISSION_VERSION, job_id=job['id'], role=role, status='prepared', request_digest=evidence._digest(_semantic(request)),
        assignment=assignment_ref, candidate=candidate_ref, claims=claims, resource_units=request['resource_units'],
        revision=request['expected_revision'] + 1, plan_digest=plan_digest, host_capacity=plan['limits']['host_capacity'],
        binding=content_binding, retry_of=request.get('retry_of'), diagnostic=diagnostic,
        observation=None, reconciliation=None, result=None)
    if 'lifecycle' in body:
        data['lifecycle'] = body['lifecycle']
    with _reservation_write(scope) as db:
        _retired_dispatch(scope, db, request)
        state._assert_open(state._task(db,state._scope_id(scope),request['client'],request['session_id'],request['task_id']))
        records = _ledger(scope, db)
        replay = _replay(scope, records, request)
        if replay:
            _require(replay['data']['role'] == role, 'Dispatch identity belongs to another role')
            return _reply(replay) if role == 'worker' else evidence._review_response(scope, db, request, replay)
        task = evidence._task(db, scope, request)
        _require(_git_routing(scope, plan['git_directory']) == git_routing, 'Prepared Git routing changed before reservation commit')
        _require(task['delegation']['plan']['digest'] == plan_digest and _claims(job['claims']) == claims and
                 db.execute('select generation from checkouts where scope_id=?', (state._scope_id(scope),)).fetchone()[0] == generation,
                 'Prepared scope, policy, resources or checkout generation changed')
        if role == 'review':
            evidence._review_admission(scope, task, proof_plan, request)
        else:
            _require(_brief(scope, job, request['brief'], proof_plan) == claims, 'Prepared worker resources changed')
        _admission(scope, db, task, plan, job, claims, records, request)
        _insert_assignment(db, scope, task, dict(id=attempt_id, owner=owner, data=data, body=body))
        record = dict(id=attempt_id, owner=owner, data=data, body=body)
        if role == 'review':
            task['evidence']['attempts'].append(evidence._review_attempt(scope, record, task=task, db=db))
        state._save(db, state._scope_id(scope), task)
    if role == 'worker':
        return _reply(record, admitted=True)
    with state._database(scope) as db:
        return evidence._review_response(scope, db, request, record, admitted=True)


MAX_OBSERVATIONS = 32


def _assignment(scope, db, request, *, roles=('worker',), records=None):
    _require(db is not None, 'Missing native assignment state')
    if state._retired_assignment(db, request['attempt_id']):
        raise state.StateError('Retired assignment evidence is unavailable or expired')
    records = _ledger(scope, db) if records is None else records
    record = next((r for r in records if r['id'] == request['attempt_id']), None)
    _require(record is not None and record['data']['role'] in roles and
        record['owner'] == dict(scope_id=state._scope_id(scope),
            **{key: request[key] for key in ('client', 'session_id', 'task_id')},
            dispatch_id=record['owner']['dispatch_id']) and
        record['body']['request']['scope_ref'] == request['scope_ref'], 'Exact supported assignment is absent')
    return record


def _closure_assignments(context,db,task_ref,task,certificate,*,read_context=None,read_roster=None):
    """Complete original membership, independently stopped and reconciled per role."""
    from runtime import changes
    records=[record for record in _ledger(None,db,context=context) if all(
        record['owner'][key]==task_ref[key] for key in ('scope_id','client','session_id','task_id'))]
    issued=certificate.get('assignments',[])
    _require(len(issued)==len(records) and len({item['reference']['id'] for item in issued})==len(issued),
        'Stopped assignment certificate membership differs from complete original owners')
    actual=[changes._assignment_certificate(context,db,task,record,read_context=read_context,read_roster=read_roster) for record in records]
    _require(sorted(actual,key=evidence._raw)==sorted(issued,key=evidence._raw),
        'Stopped assignments differ from exact current immutable reconciliation support')
    return evidence._finish_linked_read_context(records,read_context,read_roster=read_roster)


def retire_settled(db, task_ref, closure):
    """Insert exact certified assignment markers before atomically removing their rows."""
    context=evidence._existing_authority_context(task_ref['authority'],db=db)
    facts=evidence._check_closure_observation(context,db,task_ref,closure)
    _require('linked' not in facts and 'retained_linked_closure' not in facts,'Linked removal needs its complete retirement observation')
    records=_closure_assignments(context,db,task_ref,facts['task'],facts['certificate'])
    task=state._task(db,task_ref['scope_id'],task_ref['client'],task_ref['session_id'],task_ref['task_id'])
    lifetime=state._task_lifecycle(task)
    _require(lifetime is not None and lifetime['safe_closed_at'] is not None
        and evidence._read(None,lifetime['compact'],context=context,db=db)==evidence._retirement_content(facts),
        'Assignment retirement requires exact retained safe closure')
    retired_at=state._owner_time()
    _require(retired_at>=lifetime['safe_closed_at'],'Assignment retirement clock regressed')
    markers=[]
    for record in records:
        owner=record['owner'];data=record['data']
        _require(state._retired(db,'assignment',owner) is None and state._retired_assignment(db,record['id']) is None,
            'Live assignment conflicts with retained marker')
        marker=dict(version=1,identity=owner,pins_digest=state._digest(record['body']['pins']),request_digest=data['request_digest'],
            created_at=data['lifecycle']['created_at'],retired_at=retired_at,outcome=data['result']['outcome'],
            evidence_expired=False,assignment_id=record['id'])
        digest=state._retired_identity('assignment',owner);raw=evidence._raw(marker).decode()
        state._retired_record('assignment',digest,raw,record['id'])
        db.execute('insert into retired_identities values(?,?,?,?)',('assignment',digest,raw,record['id']))
        markers.append((record['id'],marker))
    for record in records:db.execute('delete from assignments where id=?',(record['id'],))
    evidence._check_closure_observation(context,db,task_ref,closure,assignments_retired=True)
    _require(all(state._retired_assignment(db,identity)==marker for identity,marker in markers),
        'Assignment marker changed before local retirement')
    return len(records)


def _assignment_task(scope, db, request, *, revision=True):
    task = state._task(db, state._scope_id(scope), request['client'], request['session_id'], request['task_id'])
    _require(task is not None and all(task[key] == request[key] for key in evidence.IDENTITY[:4]),
             'Assignment task identity changed')
    state._compatible(scope, task)
    state._assert_open(task)
    _require(not revision or task['revision'] == request['expected_revision'], 'Assignment task revision conflict')
    return task


def _worker_history(scope, reference, *, context=None, db=None):
    history, seen = [], set()
    while reference is not None:
        _reference(reference)
        _require(reference['digest'] not in seen and len(history) < MAX_OBSERVATIONS, 'Worker observation history bound reached')
        seen.add(reference['digest'])
        value = evidence._read(None if context is not None else scope, reference, context=context, db=db)
        state._fields(value, ('version', 'receipt', 'result', 'report', 'error', 'previous', 'conflict', 'revision', 'status', 'request', 'incoming'))
        _reference(value['incoming'])
        evidence._read(None if context is not None else scope, value['incoming'], context=context, db=db)
        _require(type(value['version']) is int and value['version'] == 1 and value['status'] in {'observed', 'uncertain'},
                 'Worker observation history is incompatible')
        state._integer(value['revision'], 'observation revision', 1)
        history.append((reference, value))
        reference = value['previous']
    return history


def _execution_checkout(root, common):
    """Observe affiliation and routing only; this grants no execution-root authority."""
    try:
        _require(root.is_absolute() and root.resolve(strict=True) == root and root.is_dir(),
                 'Execution root must be an existing canonical directory')
        private = Path(_git_directory_paths(root, common))
        _require(private != common and private.resolve(strict=True) == private,
                 'Execution requires a separate local worktree')
        routing = _git_routing_paths(root, private, common)
        marker = routing['marker']
        _require(marker['kind'] == 'file' and marker['link'] is None and
                 routing['marker_target']['path'] == routing['marker_target']['resolved'],
                 'Execution Git routing contains an unsafe alias')
        common_target = Path(routing['common_target']['path'])
        _require(not any(path.is_symlink() for path in (common_target, *common_target.parents)),
                 'Execution Git common target contains an unsafe alias')
        _require(all(value['link'] is None and Path(value['path']).lstat().st_nlink == 1
                     for value in routing.values() if value['kind'] == 'file'),
                 'Execution Git routing contains a linked file')
        backlink = routing['worktree_backlink']
        raw = evidence._git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
        _require(backlink['kind'] == 'file' and backlink['link'] is None and
                 backlink['sha256'] in {hashlib.sha256(os.fsencode(root / '.git') + ending).hexdigest()
                                       for ending in (b'', b'\n', b'\r\n')},
                 'Execution worktree backlink differs from its checkout')
        index = evidence._git(root, 'ls-files', '--stage', '-z')
        _require(len(index) <= evidence.MAX_ARTIFACT, 'Execution index exceeds observation bound')
        return dict(root=str(root), private_git=str(private), common_git=str(common), routing=routing,
                    head=raw, index_digest=hashlib.sha256(index).hexdigest())
    except (OSError, ValueError, RuntimeError):
        raise state.StateError('Execution checkout is unavailable, unsafe or changed') from None


def observe_execution_binding(scope, request):
    """Preview an external worktree binding without enrolling, reserving or launching it."""
    from runtime import openrouter_settings as settings
    evidence._request(request, ('supervisor_attempt_id', 'execution_root', 'source_candidate_ref',
                               'settings_scope_id', 'settings_revision', 'selection_ref'))
    request = json_object(evidence._raw(request))
    for field in ('supervisor_attempt_id', 'execution_root', 'settings_scope_id', 'selection_ref'):
        state._text(request[field], field, 4096)
    state._integer(request['settings_revision'], 'settings revision', 1)
    _reference(request['source_candidate_ref'])
    root = Path(request['execution_root'])
    _require(str(root) == request['execution_root'] and root != scope.repository_root,
             'Execution root must name a distinct canonical worktree')
    assignment_request = dict(request, attempt_id=request['supervisor_attempt_id'])

    def source():
        with state._database(scope) as db:
            record = _assignment(scope, db, assignment_request)
            task = _assignment_task(scope, db, request)
            data, body = record['data'], record['body']
            _require(task['pins'] == body['pins'] and data['candidate'] == request['source_candidate_ref'] and
                     task['delegation']['plan'] == body['plan'], 'Execution source assignment or candidate changed')
            history = _worker_history(scope, data['observation'])
            _require(data['status'] == 'observed' and history and history[0][1]['status'] == 'observed',
                     'Execution binding requires an observed native supervisor')
            observed = history[0][1]
            _require(observed['error'] is None and observed['conflict'] is None and observed['receipt'] is not None,
                     'Native supervisor observation remains uncertain')
            receipt = _worker_receipt(scope, record, observed['request'],
                                      dict(receipt=observed['receipt'], result=observed['result']))
            _require(receipt['turn_status'] == 'inProgress', 'Native supervisor is not observed running')
            plan = _task_plan(scope, task)
            _require(plan['limits']['deadline'] > _now(), 'Original delegation deadline has elapsed')
            job = next((job for job in plan['jobs'] if job['id'] == data['job_id']), None)
            _require(job is not None and job['role'] == 'worker' and _claims(job['claims']) == data['claims'],
                     'Original worker claims changed')
            proof_plan = _assignment_plan(scope, record, task)
            _require(task['evidence']['plan']['digest'] == evidence._digest(proof_plan),
                     'Current evidence plan differs from original worker plan')
        return record, task, plan, proof_plan, receipt, job

    first = source()
    record, task, plan, proof_plan, receipt, job = first
    with settings.guard(scope.repository_root) as owner:
        saved = owner.record
        _require(owner.scope == scope, 'OpenRouter settings do not support this accepted source scope')
        _require(saved is not None and saved['enabled'] and saved['version'] == 2 and
                 'workers' in saved['routes'] and saved['revision'] == request['settings_revision'] and
                 owner.binding['scope_id'] == request['settings_scope_id'], 'Current worker consent binding is unavailable')
        _require(evidence._digest(evidence.capture_candidate(scope, proof_plan)) == record['data']['candidate']['digest'],
                 'Source candidate differs from original assignment')
        source_private = _git_directory(scope)
        _require(source_private == plan['git_directory'] and _git_routing(scope, source_private) == record['body']['git_routing'],
                 'Source Git routing changed from original assignment')
        execution = _execution_checkout(root, scope.git_common_dir)
        _require(execution['private_git'] != source_private, 'Execution cannot reuse the source private Git directory')
        def mapped_claims():
            mapped = []
            for claim in record['data']['claims']:
                raw = {key:claim[key] for key in ('kind', 'key', 'mode')}
                if 'host' in claim: raw['host'] = claim['host']
                if 'path' in claim:
                    raw['key'] = claim['path']
                    if Path(claim['path']).is_relative_to(scope.repository_root):
                        target = root / Path(claim['path']).relative_to(scope.repository_root)
                        _require(target.resolve() == target, 'Mapped execution claim contains an unsafe alias')
                        for ancestor in (target, *target.parents):
                            if ancestor == root: break
                            _require(not os.path.lexists(ancestor / '.git'), 'Mapped execution claim enters another repository')
                        if target.exists() and target.is_file():
                            _require(target.stat().st_nlink == 1, 'Mapped execution claim contains a hard link')
                        raw['key'] = str(target)
                mapped.append(raw)
            return _claims(mapped)
        mapped = mapped_claims()
        checks = record['body']['request']['brief']['checks']
        _require(all(any(check['id'] == key for check in proof_plan['checks']) for key in checks),
                 'Worker check identifiers differ from accepted evidence plan')
        binding = dict(version=1, source_owner=record['owner'], source_candidate_ref=record['data']['candidate'],
            source_pins=task['pins'], source_plan_ref=record['body']['plan'], evidence_plan_ref=task['evidence']['plan'],
            supervisor_attempt_id=record['id'], supervisor_assignment_ref=record['data']['assignment'],
            supervisor_observation_ref=record['data']['observation'], native_supervisor=record['data']['binding'],
            execution=execution, mapped_claims=mapped, checks=checks, limits=plan['limits'], job=job,
            supervisor_resource_units=record['data']['resource_units'],
            settings=dict(scope_id=owner.binding['scope_id'], revision=saved['revision'], consent_version=2),
            selection_ref=request['selection_ref'])
        _require(_execution_checkout(root, scope.git_common_dir) == execution,
                 'Execution checkout changed during binding observation')
        _require(mapped_claims() == mapped, 'Mapped execution claims changed during binding observation')
        _require(evidence._digest(evidence.capture_candidate(scope, proof_plan)) == record['data']['candidate']['digest'] and
                 _git_routing(scope, source_private) == record['body']['git_routing'],
                 'Source checkout changed during binding observation')
        _require(source() == first, 'Source task or native supervisor changed during binding observation')
        owner.check()
        evidence._bounded(binding)
    return dict(binding=binding, binding_digest=evidence._digest(binding), revision=task['revision'],
        execution_id=None, launch_admitted=False, supported_for_dispatch=False,
        blockers=['Execution candidate equivalence is unqualified', 'Effective execution claims are not reserved',
                  'External worker/check capacity is not reserved', 'External host stop producer is unqualified'])


def _observation_replay(scope, history, incoming):
    return next(((reference, value) for reference, value in history[:1]
                 if evidence._read(scope, value['incoming']) == incoming), None)


def _worker_report(scope, record, result):
    report = json_object(result)
    state._fields(report, ('version', 'attempt_id', 'assignment_digest', 'candidate_digest', 'status',
                          'changed_paths', 'evidence', 'limitations'))
    data = record['data']
    _require(type(report['version']) is int and report['version'] == 1 and report['attempt_id'] == record['id'] and
        report['assignment_digest'] == data['assignment']['digest'] and report['candidate_digest'] == data['candidate']['digest'] and
        report['status'] in {'completed', 'blocked', 'pending-decision'}, 'Worker report binding is invalid')
    for field in ('changed_paths', 'evidence', 'limitations'):
        _strings(report[field], 'worker report ' + field, maximum=64, limit=4096)
    root=scope.repository_root if scope is not None else Path(state._accepted_identity(record['body']['accepted_identity'])['repository_root'])
    for name in report['changed_paths']:
        evidence._relative(name)
        claim = _claims([dict(kind='path', key=str(root / name), mode='write')])[0]
        _require(name != '.' and any(_covers(held, claim) and held['mode'] == 'write' for held in data['claims']),
                 'Worker report names an unreserved mutable path')
    return report


def _validate_version_provenance(receipt):
    for key in VERSION_PROVENANCE_FIELDS:
        if key in receipt:
            state._text(receipt[key], 'observed ' + key, 256)


def _worker_receipt(scope, record, request, observed):
    state._fields(observed, ('receipt', 'result'))
    receipt = observed['receipt']
    state._fields(receipt, ('version', 'mechanism', 'attempt_id', 'task_name', 'agent_path', 'native_version',
        'client_surface', 'originator', 'parent_model_provider', 'worker_model_provider', 'parent_session_id',
        'parent_turn_id', 'spawn_item_id', 'worker_id', 'worker_turn_id', 'turn_status', 'result_item_id', 'checkout',
        'assignment_digest', 'candidate_digest', 'requested_model', 'requested_effort', 'configured_model',
        'configured_effort', 'spawn_observed_model', 'spawn_observed_effort', 'execution_model', 'execution_effort',
        'descendants_stopped', 'prompt_delivery', 'result_digest'), ('failure_code', 'will_retry', *VERSION_PROVENANCE_FIELDS))
    binding, data = record['data']['binding'], record['data']
    _validate_version_provenance(receipt)
    _require(receipt.get('failure_code') is None or isinstance(receipt['failure_code'], str) and
        len(receipt['failure_code']) <= 128, 'Invalid native failure code')
    _require(receipt.get('will_retry') is None or type(receipt['will_retry']) is bool, 'Invalid native host retry intent')
    _require(type(receipt['version']) is int and receipt['version'] == 2 and receipt['mechanism'] == 'codex-native-agent-path' and
        all(receipt[key] == value for key, value in binding.items()) and
        all(receipt[key] == request[key] for key in ('parent_turn_id', 'spawn_item_id')) and
        receipt['checkout'] == str(scope.repository_root) and receipt['assignment_digest'] == data['assignment']['digest'] and
        receipt['candidate_digest'] == data['candidate']['digest'] and
        receipt['originator'] in ({'codex_exec'} if binding['client_surface'] == 'codex-cli' else {'Codex Desktop','codex_work_desktop'}) and
        receipt['descendants_stopped'] is False and receipt['prompt_delivery'] == 'unverified',
        'Native receipt differs from the exact prepared worker binding')
    for key in ('worker_id', 'agent_path'):
        state._text(receipt[key], 'observed ' + key, 4096)
    _require(receipt['agent_path'].endswith('/' + binding['task_name']) and receipt['turn_status'] in
        {'completed', 'inProgress', 'interrupted', 'failed', 'unknown'}, 'Native worker locator/status is invalid')
    for key in ('parent_model_provider', 'worker_model_provider', 'worker_turn_id', 'result_item_id',
                'configured_model', 'configured_effort', 'spawn_observed_model', 'spawn_observed_effort',
                'execution_model', 'execution_effort'):
        if receipt[key] is not None:
            state._text(receipt[key], 'observed ' + key, 256)
    result = observed['result']
    _require((receipt['worker_turn_id'] is None) == (receipt['turn_status'] == 'unknown'), 'Native turn identity/status differs')
    if receipt['turn_status'] == 'completed':
        _require(isinstance(result, str) and len(result.encode()) <= evidence.MAX_OUTPUT and receipt['result_item_id'] is not None and
            receipt['result_digest'] == hashlib.sha256(result.encode()).hexdigest(), 'Native result integrity is invalid')
    else:
        _require(result is None and receipt['result_digest'] is None and receipt['result_item_id'] is None,
                 'Incomplete native turn cannot invent a final result')
    return receipt


def _observation_reply(record, reference, value, replayed=False):
    return dict(ok=True, attempt_id=record['id'], status=value['status'], revision=value['revision'],
                observation_digest=reference['digest'], replayed=replayed, limitations=list(ADMISSION_LIMITATIONS))


def observe_assignment(scope, request):
    """Observe a prepared worker; reported completion never releases its ownership."""
    evidence._request(request, ('attempt_id', 'parent_turn_id', 'spawn_item_id'))
    request = json_object(evidence._raw(request))
    for key in ('attempt_id', 'parent_turn_id', 'spawn_item_id'):
        state._text(request[key], key, 256)
    with state._database(scope) as db:
        record = _assignment(scope, db, request, roles=('worker', 'review'))
        task = _assignment_task(scope, db, request, revision=False)
        _require(task['pins'] == record['body']['pins'], 'Original assignment task pins changed')
        if record['data']['role'] == 'review':
            attempt = next((a for a in task['evidence']['attempts'] if a['id'] == record['id']), None)
            _require(attempt is not None and _linked_review(scope, state._scope_id(scope), task, attempt, [record]) is not None,
                     'Review observation requires the exact retained D5 attempt')
        history = _worker_history(scope, record['data']['observation'])
        if record['data']['status'] == 'settled':
            prior = next(((ref, item) for ref, item in history if item['request'] == _semantic(request)), None)
            _require(prior is not None, 'Settled assignment cannot collect a new observation')
            return _observation_reply(record, *prior, replayed=True)
    from runtime.adapters.codex import observe_native_worker, observe_native_review_assignment
    observer = observe_native_worker if record['data']['role'] == 'worker' else observe_native_review_assignment
    receipt = report = result = None
    error = None
    raw_observed = None
    try:
        observed = observer(scope, record['id'], request['parent_turn_id'], request['spawn_item_id'])
        evidence._bounded(observed, evidence.MAX_ARTIFACT)
        raw_observed = observed
        receipt = _worker_receipt(scope, record, request, observed)
        result = observed['result']
        if result is not None and record['data']['role'] == 'worker':
            try:
                report = _worker_report(scope, record, result)
            except (state.StateError, StartupError):
                error = 'Worker result is not a valid bound report'
    except (StartupError, state.StateError, ValueError, TypeError, KeyError, RecursionError):
        error = 'Native worker observation unavailable or incompatible; ownership remains unresolved'
    incoming = dict(receipt=receipt, result=result, report=report, error=error, request=_semantic(request), raw=raw_observed)
    duplicate = _observation_replay(scope, history, incoming)
    if duplicate:
        return _observation_reply(record, *duplicate, replayed=True)
    _require(len(history) < MAX_OBSERVATIONS, 'Worker observation history bound reached')
    incoming_ref = evidence._artifact(scope, incoming, owner=evidence._artifact_owner(scope, task))
    prior = history[0][1] if history else None
    conflict = prior['conflict'] if prior else None
    previous_receipt = prior['receipt'] if prior else None
    if receipt is not None and previous_receipt is not None:
        stable = ('client_surface', 'native_version', 'parent_session_id', 'parent_turn_id', 'spawn_item_id',
                  'worker_id', 'agent_path', 'originator', 'checkout', 'requested_model', 'requested_effort')
        changed = any(receipt[key] != previous_receipt[key] for key in stable)
        changed |= receipt['worker_turn_id'] != previous_receipt['worker_turn_id'] and (
            receipt['worker_turn_id'] is None or any(item['receipt'] is not None and
                item['receipt']['worker_turn_id'] == receipt['worker_turn_id'] for _, item in history))
        changed |= (previous_receipt['result_item_id'] is not None and
                    receipt['worker_turn_id'] == previous_receipt['worker_turn_id'] and any(
                        receipt[key] != previous_receipt[key] for key in ('result_item_id', 'result_digest')))
        changed |= (receipt['result_item_id'] is not None and receipt['result_item_id'] == previous_receipt['result_item_id'] and
                    receipt['result_digest'] != previous_receipt['result_digest'])
        if changed:
            conflict = incoming_ref
            receipt, result, report = previous_receipt, prior['result'], prior['report']
            error = 'Contradictory native identity/result retained for independent reconciliation'
    if receipt is None and previous_receipt is not None:
        receipt, result, report = previous_receipt, prior['result'], prior['report']
    value = dict(version=1, receipt=receipt, result=result, report=report, error=error,
        request=_semantic(request), incoming=incoming_ref, previous=record['data']['observation'], conflict=conflict,
        revision=request['expected_revision'] + 1, status='uncertain' if error or conflict else 'observed')
    reference = evidence._artifact(scope, value, owner=evidence._artifact_owner(scope, task))
    with _reservation_write(scope) as db:
        current = _assignment(scope, db, request, roles=('worker', 'review'))
        duplicate = _observation_replay(scope, _worker_history(scope, current['data']['observation']), incoming)
        if duplicate:
            return _observation_reply(current, *duplicate, replayed=True)
        task = _assignment_task(scope, db, request)
        _require(current['data'] == record['data'] and task['pins'] == record['body']['pins'], 'Assignment changed during observation')
        current['data'].update(status=value['status'], observation=reference)
        db.execute('update assignments set data=? where id=?', (evidence._raw(current['data']).decode(), record['id']))
        task['revision'] += 1
        state._save(db, state._scope_id(scope), task)
    return _observation_reply(record, reference, value)


def _assignment_plan(scope, record, task):
    original = evidence._read(scope, record['data']['candidate'])['definitions_digest']
    references = [task['evidence']['plan']] + [item['plan'] for item in task['evidence'].get('history', [])]
    reference = next((ref for ref in references if ref['digest'] == original), None)
    _require(reference is not None, 'Original candidate D5 definition is unavailable')
    return evidence._read(scope, reference)


def _reconciliation_reply(record, artifact, replayed=False):
    return dict(ok=True, attempt_id=record['id'], status='settled', revision=artifact['revision'],
        outcome=artifact['assessment']['outcome'], replayed=replayed, limitations=list(ADMISSION_LIMITATIONS))


def reconcile_assignment(scope, request):
    """Settle one worker/coordinator only with exact independent lifecycle/effect proof."""
    evidence._request(request, ('attempt_id', 'host_proof', 'assessment'))
    request = json_object(evidence._raw(request))
    state._text(request['attempt_id'], 'attempt_id', 256)
    with state._database(scope) as db:
        record = _assignment(scope, db, request, roles=('worker', 'review', 'coordinator'))
        if record['data']['status'] == 'settled':
            artifact = evidence._read(scope, record['data']['reconciliation'])
            _require(artifact.get('request_digest') == evidence._digest(_semantic(request)), 'Settled assignment replay payload changed')
            return _reconciliation_reply(record, artifact, replayed=True)
        task = _assignment_task(scope, db, request)
        generation = db.execute('select generation from checkouts where scope_id=?', (state._scope_id(scope),)).fetchone()[0]
        plan = _assignment_plan(scope, record, task) if record['data']['candidate'] is not None else None
        reservations = {item['id']: evidence._digest(item['data']) for item in _ledger(scope, db)}
    assessment = request['assessment']
    state._fields(assessment, ('outcome', 'changed_paths', 'result_assessment', 'partial_change_assessment', 'evidence', 'coordinator_ref'))
    _require(assessment['outcome'] in {'completed', 'failed', 'cancelled'}, 'Unsupported worker outcome')
    for key in ('result_assessment', 'partial_change_assessment', 'coordinator_ref'):
        state._text(assessment[key], key, 4096)
    for key in ('changed_paths', 'evidence'):
        _strings(assessment[key], 'independent assessment ' + key, maximum=64, limit=4096, nonempty=key == 'evidence')
    for name in assessment['changed_paths']:
        evidence._relative(name)
        _require(name != '.', 'Assessment must name exact changed paths')
    proof = evidence._read_host_proof(scope, request['host_proof'])
    candidate = evidence.capture_candidate(scope, plan) if plan is not None else None
    _validate_operation_proof(scope, record, task, proof, current_candidate_digest=evidence._digest(candidate) if candidate is not None else None)
    if candidate is not None:
        original = evidence._read(scope, record['data']['candidate'])
        changed = {name for name in original['fingerprints'].keys() | candidate['fingerprints'].keys()
                   if not name.startswith('instruction:') and original['fingerprints'].get(name) != candidate['fingerprints'].get(name)}
        _require(changed <= set(assessment['changed_paths']), 'Partial-change assessment omits actual candidate paths')
    if 'workstream_continuation' in record['data']:
        validate_settlement(scope, record, assessment['outcome'])
    latest = _worker_history(scope, record['data']['observation']) if record['data']['role'] == 'worker' else []
    if assessment['outcome'] == 'completed' and record['data']['role'] == 'worker':
        observed = latest[0][1] if latest else None
        _require(observed is not None and observed['status'] == 'observed' and observed['error'] is None and
            observed['conflict'] is None and observed['report'] is not None and observed['report']['status'] == 'completed' and
            set(observed['report']['changed_paths']) <= set(assessment['changed_paths']),
            'Completed outcome requires an actual bound completed worker report and independent effects assessment')
    if record['data']['role'] == 'review' and assessment['outcome'] == 'completed':
        attempt = next(a for a in task['evidence']['attempts'] if a['id'] == record['id'])
        _require(attempt['status'] in {'collected', 'assessed'} and evidence._review_quality_matches(scope, attempt, record),
            'Completed review requires the exact latest collected D5 report and uncontradicted native completion')
    artifact = dict(version=2, request_digest=evidence._digest(_semantic(request)), proof=proof,
        source_digest=request['host_proof']['sha256'], assessment=assessment,
        current_candidate=evidence._artifact(scope, candidate, owner=evidence._artifact_owner(scope, task)) if candidate is not None else None, reservation_snapshot_digest=evidence._digest(reservations),
        revision=request['expected_revision'] + 1)
    reference = evidence._artifact(scope, artifact, owner=evidence._artifact_owner(scope, task))
    with _reservation_write(scope) as db:
        current = _assignment(scope, db, request, roles=('worker', 'review', 'coordinator'))
        task = _assignment_task(scope, db, request)
        _require({item['id']: evidence._digest(item['data']) for item in _ledger(scope, db)} == reservations,
                 'Shared reservation ownership changed during reconciliation; recapture candidate and proof')
        _require(current['data'] == record['data'] and task['pins'] == record['body']['pins'] and
            db.execute('select generation from checkouts where scope_id=?', (state._scope_id(scope),)).fetchone()[0] == generation,
            'Assignment observation/pins/candidate generation changed during reconciliation')
        if current['data']['role'] == 'review':
            attempt = next(a for a in task['evidence']['attempts'] if a['id'] == current['id'])
            _linked_review(scope, state._scope_id(scope), task, attempt, [current])
            attempt['ownership_reconciliation'] = reference
            if attempt['status'] in {'reserved', 'running', 'uncertain'}:
                attempt['status'] = 'cancelled' if assessment['outcome'] == 'cancelled' else 'failed'
        current['data'].update(status='settled', reconciliation=reference, result=dict(outcome=assessment['outcome']))
        db.execute('update assignments set data=? where id=?', (evidence._raw(current['data']).decode(), record['id']))
        task['revision'] += 1
        state._save(db, state._scope_id(scope), task)
    return _reconciliation_reply(record, artifact)



def _native_binding(record):
    binding = dict(record['data']['binding'], assignment_digest=record['data']['assignment']['digest'])
    context_id = _assignment_context(record['body']['request'], record['data']['role'])
    if context_id is not None:
        binding['workstream_context_id'] = context_id
    if record['body']['pins']['schema_version'] == 3:
        binding.update(pins=record['body']['pins'], accepted_identity=record['body']['accepted_identity'],
                       scope_id=record['owner']['scope_id'])
    return binding


def prepared_native_assignment(scope, attempt_id):
    """Read the immutable prepared identity used by the actual native worker observer."""
    _require(isinstance(attempt_id, str) and re.fullmatch(r'[0-9a-f]{32}', attempt_id), 'Invalid native assignment id')
    with state._database(scope) as db:
        _require(db is not None, 'Missing native assignment state')
        if state._retired_assignment(db, attempt_id):
            raise state.StateError('Retired native assignment evidence is unavailable or expired')
        found = next((r for r in _ledger(scope, db) if r['id'] == attempt_id and r['owner']['scope_id'] == state._scope_id(scope)), None)
        _require(found is not None and found['data']['role'] == 'worker', 'Native worker assignment is absent or unqualified')
        return _native_binding(found)


def prepared_native_review_assignment(scope, attempt_id):
    """Read the exact same-ID D6 review binding for A5 lifecycle observation."""
    _require(isinstance(attempt_id, str) and re.fullmatch(r'[0-9a-f]{32}', attempt_id), 'Invalid native review assignment id')
    with state._database(scope) as db:
        if db is not None and state._retired_assignment(db, attempt_id):
            raise state.StateError('Retired native review evidence is unavailable or expired')
        record = next((r for r in _ledger(scope, db) if r['id'] == attempt_id and
            r['owner']['scope_id'] == state._scope_id(scope)), None) if db is not None else None
        _require(record is not None and record['data']['role'] == 'review', 'Exact native review assignment is absent')
        owner = record['owner']
        task = state._task(db, owner['scope_id'], owner['client'], owner['session_id'], owner['task_id'])
        attempt = next((a for a in task['evidence']['attempts'] if a['id'] == attempt_id), None)
        _require(attempt is not None, 'Exact D5 review attempt is absent')
        _linked_review(scope, owner['scope_id'], task, attempt, [record])
        return _native_binding(record)


def _compact_delegation_status(report: dict) -> dict:
    """Project recorded ownership only; never refresh native or completion evidence."""
    assignments = report['assignments']
    rows = sorted(assignments, key=lambda row: (row['status'] == 'settled', row['attempt_id']))
    result = dict(ok=report['ok'], view='compact', observation_only=True,
        assignment_count=len(rows), unsettled_count=sum(row['status'] != 'settled' for row in rows),
        conflict_count=len(report['conflicts']), blockers=list(dict.fromkeys(report['blockers'])),
        limitations=list(report['limitations']), details_required=len(rows) > 20 or bool(report['conflicts']),
        assignments=[dict(attempt_id=row['attempt_id'], task_id=row['owner']['task_id'], role=row['role'],
            status=row['status'], outcome=(row.get('result') or {}).get('outcome'),
            observation=row.get('observation'), reconciliation=row.get('reconciliation')) for row in rows[:20]])
    result.update({key: report[key] for key in ('revision', 'pins') if key in report})
    return result


def delegation_status(scope, client, session_id, *, compact=False):
    """Inspect current and retained historical admissions, never initializing or launching."""
    state._text(client, 'delegation client')
    state._text(session_id, 'delegation session')
    _require(client == 'codex', 'Unsupported delegation client')
    with state._database(scope) as db:
        if db is None:
            report = dict(ok=True, plan=None, assignments=[], conflicts=[], budgets=None, capacity=None, blockers=['Runtime task state is missing'],
                        limitations=list(ADMISSION_LIMITATIONS))
            return _compact_delegation_status(report) if compact else report
        task = state._task(db, state._scope_id(scope), client, session_id)
        records = _ledger(scope, db)
        selected = [r for r in records if r['owner']['scope_id'] == state._scope_id(scope) and
                    r['owner']['client'] == client and r['owner']['session_id'] == session_id]
        delegation = task.get('delegation') if task else None
        plan = _task_plan(scope, task) if delegation else None
        budgets = dict(delegation, attempts_remaining=plan['limits']['max_attempts'] - delegation['attempts_consumed'],
            resource_units_remaining=plan['limits']['max_resource_units'] - delegation['resource_units_consumed'],
            deadline=plan['limits']['deadline']) if plan else None
        unresolved = [r for r in records if r['data']['status'] != 'settled']
        conflicts = []
        for job in plan['jobs'] if plan else []:
            claims = _claims(job['claims'])
            for record in unresolved:
                if _held_conflict(claims, record['data']['claims']):
                    conflicts.append(dict(job_id=job['id'], attempt_id=record['id'], owner=record['owner'], claims=record['data']['claims']))
        report = dict(ok=True, revision=task['revision'] if task else None, pins=task['pins'] if task else None,
            plan=delegation['plan'] if delegation else None, budgets=budgets,
            assignments=[dict(attempt_id=r['id'], owner=r['owner'], **r['data']) for r in selected], conflicts=conflicts,
            capacity=dict(unresolved_managed_workers=sum(r['data']['role'] in {'worker', 'review'} for r in unresolved),
                max_parallel_workers=plan['limits']['max_parallel_workers'] if plan else None,
                observed_host_capacity=plan['limits']['host_capacity'] if plan else None),
            blockers=['Unreconciled assignment retains resource claims' for r in selected if r['data']['status'] != 'settled'],
            limitations=list(ADMISSION_LIMITATIONS))

        return _compact_delegation_status(report) if compact else report

def _assignment_context(request, role):
    if role == 'worker' and 'workstream_context_id' not in request:
        limits = request.get('workstream_limits', {})
        _require(isinstance(limits, dict), 'Invalid Workstreams allocation fields')
        return limits.get('context_id')
    return request.get('workstream_context_id')


def _continuation_candidate(scope, record, task):
    return evidence.capture_candidate(scope, _assignment_plan(scope, record, task))


def _continuation_intent(scope, record):
    reference = record['data'].get('workstream_continuation')
    if reference is None:
        return None
    value = evidence._read(scope, reference)
    state._fields(value, ('version', 'token', 'state', 'used', 'remaining_continuations', 'workstream_revision',
        'owner', 'assignment', 'pins', 'native_identity', 'observation', 'candidate', 'ownership_digest',
        'task_revision', 'reason', 'prompt', 'human_continue', 'retry_at', 'proof', 'previous',
        'delivery', 'delivery_ref', 'prior_reconciliation', 'prior_result', 'current_claims'))
    _require(value['version'] == 1 and value['owner'] == record['owner'] and
        value['assignment'] == record['data']['assignment'] and value['pins'] == record['body']['pins'] and
        value['state'] in {'prepared', 'consumed', 'recorded'} and re.fullmatch('[0-9a-f]{32}', value['token']),
        'Continuation intent binding is invalid')
    state._integer(value['used'], 'continuation allocation consumed', 1)
    maximum = _continuation_limits(record['body']['request'])['max_continuations']
    _require(value['used'] <= maximum and value['remaining_continuations'] == maximum - value['used'],
        'Continuation allocation binding changed')
    return value


def _continuation_limits(request):
    if 'continuation_limits' not in request:
        _require('workstream_limits' in request, 'Original assignment has no continuation allocation')
        return request['workstream_limits']
    _require('workstream_limits' not in request, 'Continuation allocation must have one authority')
    value = request['continuation_limits']
    state._fields(value, ('max_continuations', 'backoff_seconds'))
    for key in value:
        state._integer(value[key], 'continuation ' + key)
        _require(value[key] < 2**53, 'Continuation allocation exceeds finite bounds')
    return value


def _continuation_owned(scope, db, request, records, *, allow_settled=False):
    record = _assignment(scope, db, request, records=records)
    task = _assignment_task(scope, db, request)
    _require(record['body']['pins'] == task['pins'],
        'Continuation requires the original owner and pins')
    _require(allow_settled or record['data']['status'] != 'settled', 'Reconciled ownership cannot resume without reacquisition')
    _require(task['state'] not in {'BLOCKED', 'WAITING', 'PAUSED', 'CANCELLED'} and not any(
        c['kind'] in {'blocker', 'decision'} and c['status'] == 'pending' for c in task['conditions']),
        'Task blockers, pauses or pending decisions prevent continuation')
    records = [r for r in records if r['id'] != record['id']]
    _resources_available(scope, db, record['data']['claims'], records)
    plan = _task_plan(scope, task)
    _require(plan['limits']['deadline'] > _now(), 'Original delegation deadline has elapsed')
    job = next(job for job in plan['jobs'] if job['id'] == record['data']['job_id'])
    _worker_scope(scope, job['claims'], record['body']['request']['brief']['files'])
    current_claims = _claims(job['claims'])
    _resources_available(scope, db, current_claims, records)
    own = [r for r in records if all(r['owner'][key] == record['owner'][key]
        for key in ('scope_id', 'client', 'session_id', 'task_id'))]
    _require(not any(r['data']['job_id'] == job['id'] and
        r['data']['revision'] > record['data']['revision'] for r in own),
        'Superseded same-job child cannot resume; reconcile the current attempt')
    validate_dependencies(scope, task, plan, job, [record, *records])
    descendants = {job['id']}
    while True:
        expanded = descendants | {j['id'] for j in plan['jobs'] if set(j['dependencies']) & descendants}
        if expanded == descendants:
            break
        descendants = expanded
    _require(not any(r['data']['job_id'] in descendants and r['data']['status'] != 'settled' for r in own),
        'An admitted dependent still relies on the original accepted output')
    return record, task


def _continuation_ownership(records):
    return evidence._digest([dict(id=r['id'], owner=r['owner'], data={k: v for k, v in r['data'].items()
        if k != 'workstream_continuation'}) for r in sorted(records, key=lambda r: r['id'])])


def _continuation_receipt(scope, record):
    history = _worker_history(scope, record['data']['observation'])
    current = history[0][1] if history else None
    _require(current is not None and current['status'] == 'observed' and not current['error'] and
        current['conflict'] is None and isinstance(current['receipt'], dict),
        'Continuation requires current unambiguous native history')
    receipt = current['receipt']
    _require(receipt['turn_status'] in {'completed', 'interrupted', 'failed'} and
        receipt.get('will_retry') is not True and receipt['worker_id'] and receipt['worker_turn_id'],
        'Native work or host retry remains active or uncertain')
    return receipt


def _continuation_recovery(receipt, reason, human):
    code = receipt.get('failure_code')
    if receipt['turn_status'] == 'failed':
        _require(code in CONTINUATION_TRANSIENT | CONTINUATION_LIMIT_FAILURES, 'Unknown failure requires reconciliation, not resend')
        _require(human or code not in CONTINUATION_LIMIT_FAILURES, 'Capacity limits require human continue; no automatic retry')
        _require(human or receipt.get('will_retry') is False,
            'Automatic continuation requires observed host retry completion')
        _require(reason == 'transient', 'Failed child requires a recognized recovery reason')
    elif receipt['turn_status'] == 'interrupted':
        _require(human, 'Interrupted child requires human continue')
    else:
        _require(reason != 'transient', 'Transient continuation requires an observed failure')


def _continuation_reply(record, task, value, admitted=False):
    reply = dict(ok=True, attempt_id=record['id'], revision=task['revision'], token=value['token'],
        worker_id=value['native_identity']['worker_id'], task_name=record['data']['binding']['task_name'],
        state=value['state'], send_admitted=admitted, native_delivery='unobserved',
        remaining_continuations=value['remaining_continuations'], retry_at=value['retry_at'],
        delivery=value['delivery'], limitations=list(CONTINUATION_LIMITATIONS))
    if admitted:
        reply['prompt'] = value['prompt']
    return reply


def _continuation_request(request, fields, optional=()):
    evidence._request(request, ('attempt_id', *fields), optional)
    state._text(request['attempt_id'], 'Workstreams attempt', 128)
    return json_object(evidence._raw(request))


def _continuation_save(scope, db, record, task, field, value):
    reference = evidence._artifact(scope, value, owner=evidence._artifact_owner(scope, task), db=db)
    record['data'][field] = reference
    db.execute('update assignments set data=? where id=?', (evidence._raw(record['data']).decode(), record['id']))
    task['revision'] += 1
    state._save(db, state._scope_id(scope), task)
    return reference


def _native_name(identity, role, request):
    from runtime.adapters.codex import native_task_name
    try:
        return native_task_name(identity, role, _assignment_context(request, role))
    except StartupError:
        raise state.StateError('Invalid native assignment naming') from None


def consume_continuation(scope, request):
    """Consume immediately before the native same-child send; loss requires reconciliation."""
    request = _continuation_request(request, ('token',), ('workstream_revision',))
    with _reservation_write(scope) as db:
        if 'workstream_revision' in request:
            from runtime.workstreams import _permission
            _permission(scope, request.get('workstream_revision'))
        _require(db is not None, 'Missing native assignment state')
        records = _ledger(scope, db)
        record, task = _continuation_owned(scope, db, request, records, allow_settled=True)
        value = _continuation_intent(scope, record)
        _require(value is not None and value['token'] == request['token'] and value['state'] == 'prepared' and
            value['workstream_revision'] == request.get('workstream_revision') and value['task_revision'] == task['revision'],
            'Continuation permission is stale or already consumed')
        _require(value['retry_at'] <= _now(), 'Continuation backoff has not elapsed')
        _require(value['observation'] == record['data']['observation'] and
            value['ownership_digest'] == _continuation_ownership(records) and
            value['candidate']['digest'] == evidence._digest(_continuation_candidate(scope, record, task)),
            'Continuation candidate, native observation or shared ownership changed')
        _continuation_receipt(scope, record)
        plan = _task_plan(scope, task)
        current_claims = _claims(next(j for j in plan['jobs'] if j['id'] == record['data']['job_id'])['claims'])
        _require(current_claims == value['current_claims'], 'Continuation physical resource aliases changed')
        if record['data']['status'] == 'settled':
            active = [r for r in records if r['data']['role'] in NATIVE_ROLES and r['data']['status'] != 'settled']
            own = [r for r in active if all(r['owner'][k] == record['owner'][k]
                for k in ('scope_id', 'client', 'session_id', 'task_id'))]
            _require(len(own) < plan['limits']['max_parallel_workers'] and
                len(active) < min([plan['limits']['host_capacity']] + [r['data']['host_capacity'] for r in active]),
                'Managed worker capacity is exhausted during reacquisition')
            _invalidate_ready(db, scope, task, current_claims)
            if task['state'] == 'DONE':
                task['state'] = 'REVIEW'
            record['data'].update(status='observed', reconciliation=None, result=None)
        value = dict(value, state='consumed')
        _continuation_save(scope, db, record, task, 'workstream_continuation', value)
        return _continuation_reply(record, task, value, admitted=True)


def prepare_continuation(scope, request):
    """Persist bounded same-child intent only after current independent stopped-writer proof."""
    request = _continuation_request(request, ('reason', 'prompt', 'human_continue', 'host_proof'), ('retry_at', 'workstream_revision'))
    _require(request['reason'] in {'follow-up', 'review-fixes', 'transient'} and type(request['human_continue']) is bool,
        'Invalid Workstreams continuation reason')
    state._text(request['prompt'], 'bounded continuation prompt', 32768)
    retry_at = request.get('retry_at', 0)
    state._integer(retry_at, 'continuation retry time')
    proof = evidence._read_host_proof(scope, request['host_proof'])
    with _reservation_write(scope) as db:
        if 'workstream_revision' in request:
            from runtime.workstreams import _permission
            _permission(scope, request.get('workstream_revision'))
        _require(db is not None, 'Missing native assignment state')
        records = _ledger(scope, db)
        record, task = _continuation_owned(scope, db, request, records, allow_settled=True)
        receipt = _continuation_receipt(scope, record)
        _continuation_recovery(receipt, request['reason'], request['human_continue'])
        previous = _continuation_intent(scope, record)
        candidate = _continuation_candidate(scope, record, task)
        ownership = _continuation_ownership(records)
        unsent = previous is not None and previous['state'] == 'prepared'
        if previous:
            changed = (previous['workstream_revision'] != request.get('workstream_revision') or
                previous['task_revision'] != task['revision'] or previous['observation'] != record['data']['observation'] or
                previous['ownership_digest'] != ownership or previous['candidate']['digest'] != evidence._digest(candidate))
            progressed = previous['native_identity']['worker_turn_id'] != receipt['worker_turn_id']
            _require((unsent and changed) or
                (previous['state'] == 'recorded' and progressed) or
                (previous['state'] == 'consumed' and progressed and request['human_continue']),
                'Prior continuation delivery is unresolved; observe the original child before reassessing')
        limits = _continuation_limits(record['body']['request'])
        used = (previous['used'] if previous else 0) + (0 if unsent else 1)
        _require(used <= limits['max_continuations'], 'Original continuation allocation is exhausted; human input required')
        _validate_operation_proof(scope, record, task, proof, current_candidate_digest=evidence._digest(candidate))
        native = proof['native_identity']
        if previous:
            _require(all(native[key] == previous['native_identity'][key] for key in
                ('client_surface', 'native_version', 'parent_session_id', 'parent_turn_id', 'spawn_item_id', 'task_name', 'worker_id')),
                'Same-child continuation identity changed')
        earliest = int(_now()) + (limits['backoff_seconds'] if request['reason'] == 'transient' else 0)
        value = dict(version=1, token=uuid.uuid4().hex, state='prepared', used=used,
            remaining_continuations=limits['max_continuations'] - used, workstream_revision=request.get('workstream_revision'),
            owner=record['owner'], assignment=record['data']['assignment'], pins=task['pins'], native_identity=native,
            observation=record['data']['observation'], candidate=evidence._artifact(scope, candidate, owner=evidence._artifact_owner(scope, task), db=db), ownership_digest=ownership,
            task_revision=task['revision'] + 1, reason=request['reason'], prompt=request['prompt'],
            human_continue=request['human_continue'], retry_at=max(retry_at, earliest),
            proof=evidence._artifact(scope, proof, owner=evidence._artifact_owner(scope, task), db=db),
            previous=record['data'].get('workstream_continuation'), delivery=None, delivery_ref=None,
            prior_reconciliation=record['data']['reconciliation'], prior_result=record['data']['result'],
            current_claims=_claims(next(j for j in _task_plan(scope, task)['jobs']
                if j['id'] == record['data']['job_id'])['claims']))
        _continuation_save(scope, db, record, task, 'workstream_continuation', value)
        return _continuation_reply(record, task, value)


def record_continuation(scope, request):
    """Retain delivery assertions even while disabled; never release or reassign ownership."""
    request = _continuation_request(request, ('token', 'delivery', 'delivery_ref'))
    _require(request['delivery'] in {'sent', 'uncertain'}, 'Invalid continuation delivery outcome')
    state._text(request['delivery_ref'], 'continuation delivery reference', 4096)
    with _reservation_write(scope) as db:
        record = _assignment(scope, db, request)
        task = _assignment_task(scope, db, request)
        value = _continuation_intent(scope, record)
        _require(value is not None and value['token'] == request['token'] and value['state'] == 'consumed',
            'Continuation delivery has no exact consumed permission')
        value = dict(value, state='recorded', delivery=request['delivery'], delivery_ref=request['delivery_ref'])
        _continuation_save(scope, db, record, task, 'workstream_continuation', value)
        return _continuation_reply(record, task, value)


def validate_dependencies(scope, task, plan, job, records):
    """A reopened prerequisite invalidates old direct and transitive acceptance."""
    own = [r for r in records if r['owner']['scope_id'] == state._scope_id(scope) and
        all(r['owner'][key] == task[key] for key in ('client', 'session_id', 'task_id'))]
    jobs = {j['id']: j for j in plan['jobs']}
    latest = {}
    for row in own:
        key=row['data']['job_id']
        if key not in latest or row['data']['revision']>latest[key]['data']['revision']:
            latest[key]=row
    checked={}
    pending=[(identity,False) for identity in job['dependencies']]
    while pending:
        identity,expanded=pending.pop()
        if identity in checked:
            continue
        if not expanded:
            pending.append((identity,True))
            pending.extend((parent,False) for parent in jobs[identity]['dependencies'] if parent not in checked)
            continue
        row=latest.get(identity)
        _require(row is not None and row['data']['role']==jobs[identity]['role'] and row['data']['status']=='settled' and
            row['data']['result']['outcome']=='completed','Required delegation dependency prerequisite is not reconciled and complete')
        retained = evidence._read(scope, row['data']['reconciliation'])
        revision = retained.get('revision')
        if revision is None and row['data']['role'] == 'check':
            proof = retained.get('proof', {})
            _require(retained.get('version') == 2 and proof.get('kind') == 'check-writer-reconciliation'
                and proof.get('attempt_id') == row['id'], 'Invalid check prerequisite reconciliation')
            revision = proof.get('expected_revision')
            state._integer(revision, 'check reconciliation proof revision', 1)
            revision += 1
        state._integer(revision,'prerequisite reconciliation revision',1)
        _require(all(revision>checked[parent] for parent in jobs[identity]['dependencies']),
            'Prior dependent acceptance predates reopened prerequisite work')
        checked[identity]=revision


def validate_record(value, read):
    """Validate additive immutable references without changing original assignment authority."""
    for key in ('workstream_dispatch', 'workstream_continuation'):
        if key in value:
            _reference(value[key])
            read(value[key])


def validate_settlement(scope, record, outcome):
    if outcome == 'completed' and 'workstream_continuation' in record['data']:
        intent = _continuation_intent(scope, record)
        if intent['state'] in {'consumed', 'recorded'}:
            receipt = _continuation_receipt(scope, record)
            _require(receipt['worker_turn_id'] != intent['native_identity']['worker_turn_id'],
                'Completed continuation requires a newly observed native result')

CONTINUATION_LIMITATIONS = ['Send admission is cooperative and one-use; native delivery is neither atomic nor exactly-once', 'Requested allocations are not hard native execution enforcement']

CONTINUATION_TRANSIENT = {'responseStreamConnectionFailed', 'httpConnectionFailed', 'responseStreamDisconnected', 'serverOverloaded', 'responseTooManyFailedAttempts', 'internalServerError'}

CONTINUATION_LIMIT_FAILURES = {'rateLimitExceeded', 'usageLimitExceeded'}
