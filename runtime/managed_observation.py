"""Owner-issued parent and legacy delegated qualification observations."""

import hashlib
import json
from pathlib import Path

from runtime import onboarding as d3, startup, task_profile


def validate_task(task, original=None):
    from runtime import state
    if original is not None:
        for key in ('qualification_attempt', 'qualification_before'):
            if original.get(key) != task.get(key) or (key in original) != (key in task):
                label = 'purpose' if key == 'qualification_attempt' else 'before state'
                raise state.StateError(f'Original qualification {label} is immutable')
    if 'qualification_before' in task:
        state._artifact_reference(task['qualification_before'])
        d3._require('qualification_attempt' in task, 'Parent before state needs its original qualification attempt')
    if 'qualification_attempt' in task:
        state._canonical_ref(task['qualification_attempt'])
        if task['state'] == 'DONE':
            raise state.StateError('Qualification task cannot acquire ordinary completion')


def validate_cursors_and_probe(value):
    from runtime import evidence, setup_observation, state
    from runtime.delegation import _require
    for key in ('revision', 'generation', 'receipt_watermark'):
        state._integer(value[key], 'original qualification ' + key, 1 if key == 'revision' else 0)
        _require(value[key].bit_length() <= 63, 'Original qualification cursor exceeds its database bound')
    probe = value['probe']
    if probe is not None:
        state._fields(probe, ('descriptor', 'before'))
        descriptor, before = probe['descriptor'], probe['before']
        state._fields(descriptor, ('canary', 'before_sha256', 'patch'))
        startup._canonical_path(descriptor['canary'])
        startup._canonical_hash(descriptor['before_sha256'])
        state._fields(before, ('canary', 'target', 'readback'))
        state._fields(before['target'], ('path', 'binding', 'parents', 'fingerprint'))
        _require(isinstance(before['readback'], str), 'Original qualification probe readback must be text')
        raw = before['readback'].encode('utf-8')
        _require(len(raw) <= 65536 and before['canary'] == descriptor['canary'] == before['target']['path']
            and before['target']['binding'] is not None
            and before['target']['fingerprint']['sha256'] == descriptor['before_sha256'] == hashlib.sha256(raw).hexdigest(),
            'Original qualification public probe changed')
        setup_observation._public_canary_patch(descriptor, Path(min(before['target']['parents'], key=len)), readback=before['readback'])
    evidence._bounded(value, evidence.MAX_ARTIFACT)


def _parent_before(value):
    from runtime import state
    state._fields(value, ('version', 'kind', 'identity', 'pins', 'attempt_ref',
        'revision', 'generation', 'receipt_watermark', 'probe'))
    d3._require(type(value['version']) is int and value['version'] == 1
        and value['kind'] == 'qualification-parent-before' and value['probe'] is not None, 'Invalid parent qualification before state')
    state._fields(value['identity'], ('client', 'session_id', 'task_id', 'scope_ref'))
    state._identity(value['identity'])
    state._canonical_ref(value['attempt_ref'])
    validate_cursors_and_probe(value)
    return value


def capture_task_start(scope, db, task, admission):
    """Capture once inside task start's transaction, after registering its owner."""
    if admission.mode != 'qualification':
        return
    from runtime import evidence, setup_observation, state
    attempt = startup._canonical_plain(admission.original_attempt)['attempt']
    descriptor = attempt.get('probe')
    if descriptor is None or {'native-worker', 'native-review'} & set(attempt['operations']):
        return
    d3._require(task['revision'] == 1 and task['turn_id'] is None
        and 'qualification_before' not in task, 'Parent before state requires the original task birth')
    before = _parent_before(dict(version=1, kind='qualification-parent-before',
        identity={key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')},
        pins=task['pins'], attempt_ref=task['qualification_attempt'], revision=task['revision'],
        generation=db.execute('select generation from checkouts where scope_id=?', (state._scope_id(scope),)).fetchone()[0],
        receipt_watermark=db.execute('select coalesce(max(rowid),0) from receipts').fetchone()[0],
        probe=dict(descriptor=descriptor, before=setup_observation._probe_fact(
            state._accepted_identity(scope.accepted_identity), descriptor['canary']))))
    task['qualification_before'] = evidence._artifact(scope, before, owner=evidence._artifact_owner(scope, task), db=db)
    # The task row must exist for artifact ownership; both writes share the birth transaction.
    db.execute('update tasks set data=? where scope_id=? and client=? and session_id=? and task_id=?',
        (json.dumps(task, separators=(',', ':')), state._scope_id(scope),
            task['client'], task['session_id'], task['task_id']))


def _parent_source(scope, task_ref, attempt_ref, case_id):
    from runtime import evidence, setup_observation as setup, state
    d3._object(task_ref, ('client', 'session_id', 'task_id', 'scope_ref', 'expected_revision', 'turn_id', 'call_id'), ('stream',))
    d3._require(isinstance(scope, startup.AcceptedScope) and case_id == 'pre-tool-enforcement'
        and task_ref.get('stream', 'parent') == 'parent', 'Parent-only proof supports only the pre-tool guard')
    state._identity(task_ref)
    state._integer(task_ref['expected_revision'], 'parent observation revision', 1)
    for key in ('turn_id', 'call_id'):
        state._text(task_ref[key], 'parent observation ' + key)
    with state._database(scope) as db:
        d3._require(db is not None, 'Managed source authority is unavailable')
        task = state._task(db, state._scope_id(scope), task_ref['client'], task_ref['session_id'], task_ref['task_id'])
        d3._require(task is not None and task['scope_ref'] == task_ref['scope_ref']
            and task['revision'] == task_ref['expected_revision'] and task['turn_id'] == task_ref['turn_id']
            and task.get('qualification_attempt') == startup._canonical_plain(attempt_ref),
            'Original managed task or qualification purpose changed')
        admission = setup.require_capability(scope, ['pre-tool-enforcement'], task=task,
            operation='task-patch', attempt_ref=attempt_ref)
        d3._require(admission.mode == 'qualification', 'Parent proof requires its qualification purpose')
        reference = task.get('qualification_before')
        d3._require(reference is not None, 'Original parent before observation is unavailable')
        before = _parent_before(evidence._read(scope, reference, db=db))
        d3._require(before['identity'] == {key: task[key] for key in before['identity']}
            and before['pins'] == task['pins'] == state._pins(scope)
            and before['attempt_ref'] == task['qualification_attempt']
            and before['probe']['descriptor'] == startup._canonical_plain(admission.original_attempt)['attempt'].get('probe')
            and task['revision'] == before['revision'] + 1 and task['state'] == 'TRIAGE'
            and task['turn_uncertain'] is False, 'Original parent before binding or first-turn revision changed')
        physical = evidence._managed_metadata(scope, reference, db=db)
        source = dict(reference=physical['managed']['path'], sha256=physical['managed']['sha256'])
        content, _ = setup._retained_reference(startup._canonical_plain(scope.qualification['reference']), setup._budget())
        binding = dict(kind='parent', parent_session_id=task['session_id'],
            client_surface=content['native']['surface'], before_digest=reference['digest'])
        return task, None, before, binding, source, admission


def managed_source(scope, task_ref, attempt_ref, case_id):
    if isinstance(task_ref, dict) and 'attempt_id' not in task_ref:
        return _parent_source(scope, task_ref, attempt_ref, case_id)
    from runtime import state, delegation, evidence, setup_observation as setup
    d3._require(isinstance(scope,startup.AcceptedScope) and case_id in setup.BEHAVIOR_CASES,'Actual managed qualification scope is required')
    d3._object(task_ref,('client','session_id','task_id','scope_ref','expected_revision','attempt_id','parent_turn_id','spawn_item_id','turn_id','call_id'),('stream',))
    stream=task_ref.get('stream','parent')
    d3._require(stream in ('parent','child') and (stream=='parent' or case_id=='post-tool-handling'),
        'Unsupported managed tool observation stream')
    with state._database(scope) as db:
        d3._require(db is not None,'Managed source authority is unavailable')
        task=state._task(db,state._scope_id(scope),task_ref['client'],task_ref['session_id'],task_ref['task_id'])
        d3._require(task is not None and task['scope_ref']==task_ref['scope_ref'] and task['revision']==task_ref['expected_revision']
            and task.get('qualification_attempt')==startup._canonical_plain(attempt_ref),'Original managed task or qualification purpose changed')
        record=next((row for row in delegation._ledger(scope,db,assignment_id=task_ref['attempt_id'])
            if row['id']==task_ref['attempt_id']),None)
        d3._require(record is not None and task_profile.native(record['data']['role'])
            and all(record['owner'][key]==task[key] for key in ('client','session_id','task_id')),
            'Original native assignment belongs to another task')
        operation='native-review' if task_profile.reviews(record['data']['role']) else 'native-worker'
        admission=setup.require_capability(scope,setup.OPERATION_CAPABILITIES[operation],task=task,operation=operation,attempt_ref=attempt_ref)
        d3._require(admission.mode=='qualification' and case_id in admission.capability_keys,'Original native capability is not admitted')
        reference=record['body'].get('qualification_before')
        d3._require(reference is not None,'Original managed before observation is unavailable')
        before=evidence._read(scope,reference,db=db)
        d3._require(before['assignment_id']==record['id'] and before['identity']==record['owner']
            and before['pins']==record['body']['pins']==state._pins(scope) and before['revision']+1==record['body']['revision']
            and before['candidate']==record['data']['candidate'] and before['plan']==record['body']['plan'],
            'Original managed before artifact changed')
        receipt=delegation._observed_worker_receipt(scope,db,record)
        d3._require(receipt is not None and all(receipt[key]==task_ref[key] for key in ('parent_turn_id','spawn_item_id'))
            and receipt['parent_session_id']==record['data']['binding']['parent_session_id']==task['session_id'],
            'Native selector differs from the original assignment observation')
        if stream=='child':
            d3._require(task_profile.native_worker(record['data']['role']) and receipt['worker_id']!=task['session_id']
                and receipt['worker_turn_id'] is not None and task_ref['turn_id']==receipt['worker_turn_id'],
                'Child selector differs from the observed worker turn')
        else:
            d3._require(task_ref['turn_id']==task['turn_id'], 'Parent selector differs from the managed task turn')
        binding=delegation._native_binding(record);binding.update(worker_id=receipt['worker_id'],worker_turn_id=receipt['worker_turn_id'])
        physical=evidence._managed_metadata(scope,reference,db=db)
        source=dict(reference=physical['managed']['path'],sha256=physical['managed']['sha256'])
        binding.update(role=record['data']['role'],observation=receipt)
        return task,record,before,binding,source,admission


def projection(scope, task_ref, attempt_ref, *, case_id):
    from runtime import setup_observation as setup, state
    task,record,before,binding,reference,admission=managed_source(scope,task_ref,attempt_ref,case_id)
    content,_=setup._retained_reference(startup._canonical_plain(scope.qualification['reference']),setup._budget())
    descriptor=before['probe']['descriptor'] if before['probe'] is not None else None
    probe=dict(patch=descriptor['patch'] if descriptor else None,canary=descriptor['canary'] if descriptor else None,
        before=before,reference=reference)
    attempt=startup._canonical_plain(admission.original_attempt['attempt']);attempt.update(before=before)
    keys=('turn_id','call_id') if record is None else ('parent_turn_id','spawn_item_id','turn_id','call_id')
    if record is not None:
        attempt['assignment_id']=record['id']
    return startup._canonical_freeze(dict(identity=state._accepted_identity(scope.accepted_identity),transaction_id=content['transaction_id'],
        attempt=attempt,binding=binding,selectors=dict(stream=task_ref.get('stream','parent'),**{key:task_ref[key] for key in keys}),
        probe=probe,setup_reference=startup._canonical_plain(scope.qualification['reference'])))


def readback(scope, task_ref, attempt_ref, *, case_id):
    from runtime import state,delegation,setup_observation as setup
    task,record,before,binding,_,_=managed_source(scope,task_ref,attempt_ref,case_id)
    child_stream=task_ref.get('stream')=='child'
    session_id=binding['worker_id'] if child_stream else task['session_id']
    event_identity=(state._scope_id(scope),task_ref['client'],session_id,task_ref['turn_id'],task_ref['call_id'])
    with state._database(scope) as db:
        current=state._task(db,state._scope_id(scope),task_ref['client'],task_ref['session_id'],task_ref['task_id'])
        d3._require(current==task,'Managed owner changed during independent readback')
        kind='PreToolUse' if case_id=='pre-tool-enforcement' else 'PostToolUse'
        row=db.execute('select rowid,fingerprint,conflicted from receipts where scope_id=? and client=? and session_id=? and turn_id=? and tool_call_id=? and kind=?',
            (*event_identity,kind)).fetchone()
        generation=db.execute('select generation from checkouts where scope_id=?',(state._scope_id(scope),)).fetchone()[0]
        receipt=None if row is None else dict(rowid=row[0],fingerprint=row[1],conflicted=bool(row[2]),kind=kind)
        post_count=None
        if kind=='PostToolUse':
            rows=db.execute('select rowid,client,session_id,turn_id,tool_call_id,fingerprint,conflicted from receipts '
                'where scope_id=? and kind=? and rowid>? limit 4097',(state._scope_id(scope),kind,before['receipt_watermark'])).fetchall()
            d3._require(0<len(rows)<=4096 and receipt is not None and receipt['rowid'] in {item[0] for item in rows},
                'Post-tool target is not a fresh original-scope receipt')
            for item in rows:
                event=dict(scope_id=state._scope_id(scope),client=item[1],session_id=item[2],turn_id=item[3])
                d3._require(all(isinstance(value,str) and value for value in item[1:6]) and not item[6]
                    and not state._retired(db,'turn',event) and not state._retired(db,'receipt',event|dict(tool_call_id=item[4],kind=kind)),
                    'Post-tool generation includes ambiguous or retired observations')
                startup._canonical_hash(item[5])
            post_count=len(rows)
            d3._require(generation-before['generation']==post_count,'Post-tool generation includes unattributed effects')
        canary=None if before['probe'] is None else setup._probe_fact(startup._canonical_plain(scope.accepted_identity),before['probe']['descriptor']['canary'])
        if child_stream:
            from runtime.adapters.codex import patch_input_fingerprint
            descriptor=before['probe']['descriptor']
            pre=db.execute("select rowid,fingerprint,conflicted from receipts where scope_id=? and client=? "
                "and session_id=? and turn_id=? and tool_call_id=? and kind='PreToolUse'",event_identity).fetchone()
            expected=delegation._child_patch_pre_fingerprint(record['data']['assignment']['digest'],
                patch_input_fingerprint(descriptor['patch']))
            d3._require(pre is not None and receipt is not None and before['receipt_watermark']<pre[0]<receipt['rowid']
                and pre[1:]==(expected,0) and not receipt['conflicted'], 'Child completion lacks its admitted pre-tool receipt')
            d3._require(canary['readback']==setup._public_canary_patch(descriptor,scope.repository_root,
                readback=before['probe']['before']['readback']), 'Child patch effect differs from the approved canary')
            receipt.update(session_id=session_id,turn_id=task_ref['turn_id'])
        digest_field='before_digest' if record is None else 'assignment_digest'
        result=dict(identity=state._accepted_identity(scope.accepted_identity),attempt_ref=startup._canonical_plain(attempt_ref),
            task_revision=current['revision'],generation=generation,receipt=receipt,canary=canary,post_receipts_count=post_count)
        result[digest_field]=binding[digest_field]
        if record is None:
            d3._require(generation==before['generation'], 'Parent guard has unexplained checkout effects')
    fragment='#managed-parent-observation:' if record is None else '#managed-child-observation:' if child_stream else '#managed-observation:'
    key=task['qualification_before']['name'] if record is None else record['id']
    result['reference']=dict(reference=str(state._path(scope))+fragment+key+':'+task_ref['call_id'],sha256=d3._digest(result))
    return startup._canonical_freeze(result)


def retained_parent_task(scope, selector, before_ref):
    from runtime import evidence, state
    name, split, call_id = selector.partition(':')
    d3._require(split and call_id, 'Invalid retained parent observation selector')
    reference = dict(name=name, digest=before_ref['sha256'])
    with state._database(scope) as db:
        before = _parent_before(evidence._read(scope, reference, db=db))
        identity = before['identity']
        task = state._task(db, state._scope_id(scope), identity['client'], identity['session_id'], identity['task_id'])
        d3._require(task is not None and task.get('qualification_before') == reference,
            'Original retained parent before binding changed')
        return dict(identity, expected_revision=task['revision'], turn_id=task['turn_id'], call_id=call_id, stream='parent')


def retained_readback(scope, task_ref, attempt_ref, *, case_id, projection, proofs):
    """Recheck the issued native source relation before consuming its Core receipt."""
    from runtime import doctor
    from runtime.adapters import codex
    try:
        binding = projection['binding']
        child = task_ref.get('stream') == 'child'
        thread = binding['worker_id'] if child else binding['parent_session_id']
        path, separator, _ = proofs[0]['reference'].partition('#bytes:')
        d3._require(separator and proofs[1]['reference'].startswith(path + '#bytes:'),
            'Managed native sources belong to different rollouts')
        path = startup._canonical_path(path)
        d3._no_links(path)
        info = path.stat()
        physical = dict(device=info.st_dev, inode=info.st_ino)
        raw = d3._read_file(path, codex.NATIVE_MAX_FILE, expected_binding=physical)
        records = doctor.native_behavior_records(raw, thread_id=thread, source_reference=str(path))
        fact = startup._canonical_plain(codex._native_managed_tool(records,
            submitted_patch=projection['probe']['patch'], turn_id=task_ref['turn_id'],
            call_id=task_ref['call_id'], case_id=case_id, child_patch=child, raw=raw))
        d3._require(fact['call_id'] == task_ref['call_id'] and fact['sources'] == proofs[:2],
            'Managed native identity relation changed')
        actual = readback(scope, task_ref, attempt_ref, case_id=case_id)
        d3._require(d3._read_file(path, codex.NATIVE_MAX_FILE, expected_binding=physical) == raw,
            'Managed native source changed during retained readback')
        return fact, actual
    except (doctor.ObservationError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise startup.StartupError('Retained native hook correlation is unavailable') from None
