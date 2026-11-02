"""Enrolled-task foreground heavy phases over cooperative host reservations."""
import hashlib
import os
from pathlib import Path
import subprocess
import threading
import shutil
import tempfile
import time

from runtime import delegation as d6, evidence, state, workstreams


def _authority(scope, request, cleanup=False):
    with state._database(scope) as db:
        d6._require(db is not None, 'Missing native assignment state')
        records = d6._ledger(scope, db)
        record = d6._assignment(scope, db, request, roles=('worker', 'review', 'coordinator'), records=records)
        task = (state._task(db, state._scope_id(scope), request['client'], request['session_id'], request['task_id'])
            if cleanup else d6._assignment_task(scope, db, request, revision=False))
        d6._require(task is not None and task['pins'] == record['body']['pins'], 'Resource task authority changed')
        if cleanup:
            state._compatible(scope, task)
        if not cleanup:
            if 'resource_revision' in request:
                workstreams._feature_permission(scope, 'resource_admission', request['resource_revision'])
            else:
                # Historical requests retain their original combined revision reader.
                workstreams._feature_permission(scope, 'resource_admission', request['workstream_revision'])
                workstreams._permission(scope, request['workstream_revision'])
            d6._require(task['state'] in {'TRIAGE', 'READY', 'WORKING', 'REVIEW'} and
                not any(c['status']=='pending' and c['kind'] in {'blocker','decision'} for c in task['conditions']),
                'Resource task is paused, blocked or closed')
            d6._require(record['data']['status'] != 'settled', 'Resource owner is settled')
        plan = d6._task_plan(scope, task)
        if not cleanup:
            job=next((j for j in plan['jobs'] if j['id']==record['data']['job_id']),None)
            if job is not None:
                d6.validate_dependencies(scope,task,plan,job,records)
            claims=d6._claims(job['claims']) if job is not None else record['data']['claims']
            d6._resources_available(scope,db,claims,records if record['data']['role']=='review' else [r for r in records if r['id']!=record['id']])
        return record, plan


def _eligible(owner):
    """Suppress known-ineligible waits; unreadable authority retains its queue guard."""
    from types import SimpleNamespace
    from runtime.startup import acquire_project_entry, _entry_scope
    try:
        acquisition=acquire_project_entry(SimpleNamespace(client=owner['client'],cwd=Path(owner['cwd'])),state.PACKAGE)
        scope=_entry_scope(acquisition)
        if scope is None or scope.runtime is None:
            return True
        setting = workstreams._feature(scope, 'resource_admission')
        if not setting['enabled'] or ('resource_revision' in owner and owner['resource_revision'] != setting['revision']):
            return False
        with state._database(scope) as db:
            task=state._task(db,state._scope_id(scope),owner['client'],owner['session_id'],owner['task_id'])
            if task is None:
                return False
            if task['pins']!=owner['pins']:
                return True
            rows=d6._ledger(scope,db)
            record=next((r for r in rows if r['id']==owner['attempt_id']),None)
            if record is None or record['data']['status']=='settled':
                return False
            if 'resource_revision' not in owner:
                legacy = workstreams._load(scope)
                revision = record['body']['request'].get('workstream_revision')
                if not legacy['enabled'] or revision != legacy['revision'] or revision != setting['revision']:
                    return False
            plan=d6._task_plan(scope,task)
            job=next((j for j in plan['jobs'] if j['id']==record['data']['job_id']),None)
            if job is not None:
                try:
                    d6.validate_dependencies(scope,task,plan,job,rows)
                except state.StateError:
                    return False
            return task['state'] in {'TRIAGE','READY','WORKING','REVIEW'} and not any(
                c['status']=='pending' and c['kind'] in {'blocker','decision'} for c in task['conditions'])
    except (ValueError,OSError,KeyError,TypeError):
        return True


def operate(scope, directory, action, request):
    """Run only once per phase; uncertain execution retains capacity for reconciliation."""
    from runtime import resource_admission as host
    evidence._request(request, ('attempt_id','phase_id'),
        ('resource_revision','workstream_revision','cpu','memory_bytes','capacity_cpu','capacity_memory_bytes','argv','wait_seconds','timeout_seconds','disk_bytes'))
    state._text(request['phase_id'], 'resource phase', 128)
    d6._require(action in {'run','status','release','cancel'}, 'Unknown resource action')
    revisions = {'resource_revision', 'workstream_revision'} & request.keys()
    d6._require(len(revisions) == 1 if action == 'run' else len(revisions) <= 1,
        'Use one resource revision or the original legacy Workstreams revision')
    record, plan = _authority(scope, request, cleanup=action in {'release','cancel','status'})
    owner = dict(record['owner'], attempt_id=record['id'], pins=record['body']['pins'],cwd=str(scope.repository_root))
    identity = hashlib.sha256(evidence._raw(dict(owner=owner,phase=request['phase_id']))).hexdigest()
    if action in {'status','release','cancel'}:
        snapshot = host.control(directory, 'status', {})
    if action == 'status':
        return dict(ok=True, reservations={key:value for key,value in snapshot['reservations'].items()
            if all(value['request']['owner'].get(k)==v for k,v in owner.items())},
            next_action='Inspect original execution and independently reconcile stopped owners before releasing active reservations')
    if action in {'release','cancel'}:
        row=snapshot['reservations'].get(identity)
        d6._require(row is not None and all(row['request']['owner'].get(k)==v for k,v in owner.items()), 'Resource owner changed')
        owner=row['request']['owner']
        if action=='release':
            d6._require(record['data']['status']=='settled' and record['data']['reconciliation'] is not None,
                'Release requires independently reconciled stopped owner')
            proof=dict(path=str(state._path(scope)),sha256=record['data']['reconciliation']['digest'])
        else:
            row=snapshot['reservations'].get(identity)
            d6._require(row is not None and row['state']=='waiting', 'Only an unlaunched wait can be cancelled')
        payload=dict(id=identity,owner=owner)
        if action=='release': payload['stopped_proof']=proof
        return host.control(directory,action,payload)
    if 'resource_revision' in request:
        # Keep permission outside the phase identity, so toggles cannot permit replay.
        owner['resource_revision'] = request['resource_revision']
    argv=request.get('argv')
    d6._require(isinstance(argv,list) and argv and all(isinstance(a,str) and '\x00' not in a for a in argv), 'Expected command argv')
    wait=request.get('wait_seconds',0);timeout=request.get('timeout_seconds',plan['limits']['deadline']-time.time())
    d6._require(type(wait) in {int,float} and 0<=wait<=60 and type(timeout) in {int,float} and timeout>0,
        'Invalid bounded resource wait/timeout')
    disk=request.get('disk_bytes',0)
    state._integer(disk,'disk demand')
    # Bind the phase to its command without allowing changed retries to launch again.
    owner=owner | dict(command_digest=evidence._digest(dict(argv=argv,disk_bytes=disk)))
    deadline=plan['limits']['deadline'];end=min(time.monotonic()+wait,time.monotonic()+max(0,deadline-time.time()))
    while True:
        _authority(scope,request)
        d6._require(time.time()<deadline,'Original resource deadline elapsed')
        # Disk veto stays fresh and independent of cached memory telemetry.
        disk_pressure = False
        disk_warning = None
        if disk:
            try:
                disk_pressure = shutil.disk_usage(scope.repository_root).free < disk
            except OSError:
                disk_warning = 'Disk telemetry unavailable (OSError); remaining admission checks apply'
        result=host.control(directory,'request',dict(id=identity,owner=owner,
            **{k:request[k] for k in ('cpu','memory_bytes','capacity_cpu','capacity_memory_bytes')},deadline=deadline,ready=True),disk_pressure=disk_pressure,disk_warning=disk_warning,eligible=_eligible)
        d6._require(result['state'] in {'active','waiting'},'Phase already finished; use a new phase identity')
        if result['admitted']:break
        if time.monotonic()>=end:return result
        time.sleep(min(host.RECHECK_SECONDS,max(0,end-time.monotonic())))
    d6._require(result['newly_admitted'],'Phase already admitted; inspect original execution instead of replay')
    _authority(scope,request)
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        # Keep foreground commands isolated for later native stop inspection.
        child=subprocess.Popen(argv,cwd=scope.repository_root,stdout=stdout,stderr=stderr,start_new_session=True)
        try:
            code=child.wait(timeout=min(timeout,max(0.001,deadline-time.time())))
        except subprocess.TimeoutExpired:
            threading.Thread(target=child.wait,daemon=True).start()
            return result | dict(uncertain=True,released=False,pid=child.pid,reason='command still running; reconcile original owner')
        def tail(stream):
            size=stream.tell();stream.seek(max(0,size-16384));return stream.read().decode('utf-8',errors='replace')
        # Arbitrary commands can detach descendants; native stop reconciliation owns release.
        return result | dict(returncode=code,stdout=tail(stdout),stderr=tail(stderr),pid=child.pid,
            released=False,uncertain=True,reason='command exited; independently reconcile descendants before release')


def host_directory():
    return (Path(os.environ.get('CODEX_HOME',Path.home()/'.codex')).expanduser().resolve()/'workstreams-resources')
