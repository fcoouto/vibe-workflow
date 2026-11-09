"""Task-owned delivery decisions; recorded references never grant external authority."""
from copy import deepcopy
import base64
import binascii
import re

from runtime import state


def _require(condition, message):
    if not condition:
        raise state.StateError(message)


def _ref(value):
    state._text(value, 'delivery decision reference', 1024)


def _branch(value):
    state._text(value, 'delivery ref', 256)
    _require(not value.startswith(('-', '/', '.')) and not value.endswith(('/', '.', '.lock'))
             and not any(c.isspace() or c in '~^:?*[\\' for c in value)
             and not any(p in value for p in ('..', '//', '@{'))
             and all(p and not p.startswith('.') and not p.endswith('.lock') for p in value.split('/')),
             'Invalid delivery branch')


def validate_policy(value: dict) -> dict:
    state._fields(value, ('version', 'merge', 'separate_worktrees', 'targets', 'decision_ref'))
    _require(type(value['version']) is int and value['version'] == 1, 'Unsupported delivery policy')
    _require(value['merge'] in ('manual', 'agent') and type(value['separate_worktrees']) is bool,
             'Invalid delivery defaults')
    _ref(value['decision_ref'])
    _require(isinstance(value['targets'], list) and len(value['targets']) <= 64, 'Invalid delivery targets')
    seen = set()
    for target in value['targets']:
        state._fields(target, ('provider', 'host', 'repository', 'target', 'merge_method',
                              'publication_ref', 'merge_ref', 'remote_cleanup_ref'))
        _require(target['provider'] == 'github', 'Unsupported delivery provider')
        _require(isinstance(target['host'], str) and re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*', target['host']),
                 'Invalid delivery host')
        _require(isinstance(target['repository'], str)
                 and re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', target['repository'])
                 and target['repository'].split('/')[1] not in ('.', '..'), 'Invalid delivery repository')
        _branch(target['target'])
        _require(target['merge_method'] in ('merge', 'squash', 'rebase', 'queue'), 'Invalid delivery merge method')
        for key in ('publication_ref', 'merge_ref', 'remote_cleanup_ref'):
            if target[key] is not None:
                _ref(target[key])
        identity = tuple(target[k] for k in ('provider', 'host', 'repository', 'target'))
        _require(identity not in seen, 'Duplicate delivery target')
        seen.add(identity)
    return deepcopy(value)


def effective_policy(saved: dict, override: dict | None) -> dict:
    result = validate_policy(saved)
    if override is not None:
        state._fields(override, ('merge', 'decision_ref'))
        _require(override['merge'] in ('manual', 'agent'), 'Invalid task merge override')
        _ref(override['decision_ref'])
        result.update(override)
    return result


def validate_record(value):
    state._fields(value, ('plan', 'operations'), ('merge_wait',))
    state._artifact_reference(value['plan'])
    _require(isinstance(value['operations'], list) and len(value['operations']) <= 64,
             'Invalid delivery operation history')
    for reference in value['operations']:
        state._artifact_reference(reference)
    if 'merge_wait' in value:
        marker = value['merge_wait']
        state._fields(marker, ('condition_id', 'verification', 'plan'))
        state._text(marker['condition_id'], 'merge wait condition', 128)
        state._artifact_reference(marker['verification'])
        state._artifact_reference(marker['plan'])


def _plan(value, saved, override=None, *, previous=None):
    from runtime import evidence
    evidence._bounded(value)
    state._fields(value, ('version', 'decision_ref', 'policy', 'workspace', 'publications', 'companions'))
    _require(type(value['version']) is int and value['version'] == 1, 'Unsupported delivery plan')
    _ref(value['decision_ref'])
    _require(validate_policy(value['policy']) == effective_policy(saved, override),
             'Plan differs from adopted delivery policy')
    _require(value['workspace'] is None if previous is None else
             evidence._raw(value['workspace']) == evidence._raw(previous['workspace']),
             'Workspace binding needs independently observed native ownership')
    publications, companions = value['publications'], value['companions']
    _require(isinstance(publications, list) and len(publications) <= 64
             and isinstance(companions, list) and len(companions) <= 64, 'Invalid delivery scope')
    ids = set()
    old_publications = {p['id']: p for p in previous['publications']} if previous else {}
    for publication in publications:
        state._fields(publication, ('id', 'provider', 'host', 'repository', 'target', 'branch',
                                   'candidate', 'pull_request', 'dependencies', 'required'))
        state._text(publication['id'], 'publication id', 128)
        _require(publication['id'] not in ids, 'Duplicate publication')
        ids.add(publication['id'])
        _branch(publication['branch'])
        _require(type(publication['required']) is bool, 'Invalid delivery requirement')
        old = old_publications.get(publication['id'])
        if old is None:
            _require(publication['pull_request'] is None and publication['candidate'] is None,
                     'Publication binding requires observed candidate and PR ownership')
        else:
            protected = ('id', 'provider', 'host', 'repository', 'target', 'branch', 'candidate', 'pull_request')
            _require(evidence._raw({k: publication[k] for k in protected}) == evidence._raw({k: old[k] for k in protected})
                and (not old['required'] or publication['required']), 'Scope revision cannot replace or discard an existing delivery')
        _require(any(all(publication[k] == t[k] for k in ('provider', 'host', 'repository', 'target'))
                     for t in value['policy']['targets']), 'Unadopted publication target')
        _require(isinstance(publication['dependencies'], list) and len(publication['dependencies']) <= 64
                 and all(isinstance(i, str) for i in publication['dependencies'])
                 and len(set(publication['dependencies'])) == len(publication['dependencies']),
                 'Invalid publication dependencies')
    remaining = {p['id']: set(p['dependencies']) for p in publications}
    _require(all(deps <= ids for deps in remaining.values()), 'Unknown publication dependency')
    while remaining:
        ready = {key for key, deps in remaining.items() if not deps & remaining.keys()}
        _require(ready, 'Cyclic publication dependency')
        remaining = {key: deps for key, deps in remaining.items() if key not in ready}
    for companion in companions:
        state._task_reference(companion)
    _require(len({evidence._raw(c) for c in companions}) == len(companions), 'Duplicate delivery companion')
    if previous:
        _require(old_publications.keys() <= ids and {evidence._raw(c) for c in previous['companions']}
            <= {evidence._raw(c) for c in companions}, 'Scope revision cannot discard existing delivery obligations')
    return deepcopy(value)


def _read_plan(scope, task, *, db=None, context=None):
    from runtime import evidence
    return evidence._read(scope, task['delivery']['plan'], db=db, context=context)


def _merge_wait_acceptance(plan):
    from runtime import evidence
    plan = deepcopy(plan)
    for item in plan['publications']:
        if item['candidate'] is not None:
            item['candidate'].pop('observation', None)
    return evidence._raw(plan)


def _manual_publication_needs_user(publication, read):
    candidate = publication['candidate']
    facts = read(candidate['observation'])['observation']
    # The ready effect supersedes only the provider observation it started from.
    receipt = read(candidate['readiness'])
    if 'operation' in receipt:
        effect = read(receipt['operation'])
        if (effect['kind'] == 'delivery-effect' and effect['action'] == 'ready' and effect['status'] == 'completed'
                and effect['binding'] == receipt['binding']):
            before = read(effect['previous_plan'])
            if any(item['id'] == publication['id'] and item['candidate'] is not None
                   and item['candidate']['observation'] == candidate['observation'] for item in before['publications']):
                facts = effect['observation']
    return not facts['draft'] and facts['mergeability'] is not False


def manual_merge_needs_user(scope, db, task):
    """Project an existing generated wait using retained delivery evidence."""
    from runtime import evidence
    marker = task.get('delivery', {}).get('merge_wait')
    if (task['state'] != 'WAITING' or task.get('reference') != 'delivery:completion'
            or not marker or marker['verification'] != task.get('evidence', {}).get('verification')):
        return False
    pending = [condition for condition in task['conditions'] if condition['status'] == 'pending']
    if pending != [dict(id=marker['condition_id'], kind='blocker', action='task', status='pending',
                       reason=task['reason'], reference=task['reference'])]:
        return False
    plan = _read_plan(scope, task, db=db)
    if plan['policy']['merge'] != 'manual':
        return False
    if marker['plan'] != task['delivery']['plan']:
        original = evidence._read(scope, marker['plan'], db=db)
        if _merge_wait_acceptance(original) != _merge_wait_acceptance(plan):
            return False
    def read(reference):
        return evidence._read(scope, reference, db=db)

    requirements = _scope_requirements(plan, read=read)
    return requirements['waiting_for_merge'] and any(_manual_publication_needs_user(publication, read)
        for publication in plan['publications'] if publication['id'] in requirements['pending'])


def completion_requirements(scope, db, task: dict, *, context=None) -> dict:
    """Local retained facts only; a title or caller's remote claim is never proof."""
    from runtime import evidence
    adopted = 'delivery_policy' in task.get('accepted_identity', {})
    if 'delivery' not in task:
        required = adopted and task['mode'] == 'editable'
        result = dict(delivery_required=required, delivery_complete=not required,
                    pending=[], blockers=['Delivery scope decision is missing'] if required else [], waiting_for_merge=False)
    else:
        plan = _read_plan(scope, task, db=db, context=context)
        result = _scope_requirements(plan, read=lambda reference: evidence._read(scope, reference, db=db, context=context))
    if 'qualification_attempt' in task:
        result.update(delivery_complete=False, waiting_for_merge=False)
        result['blockers'].append('Qualification task cannot authorize ordinary delivery')
    return result


def _scope_requirements(plan, *, read=None, readiness_required=True):
    pending, blockers = [], []
    required = [p for p in plan['publications'] if p['required']]
    waiting = bool(required) and not plan['companions']
    for publication in required:
        candidate = publication['candidate']
        observation = candidate.get('observation') if isinstance(candidate, dict) else None
        if observation is None or read is None:
            pending.append(publication['id'])
            waiting = False
            continue
        try:
            record = read(observation)
            facts = record['observation']
            _require(type(record['version']) is int and record['version'] == 1 and record['kind'] == 'delivery-observation'
                and record['binding'] == _publication_binding(publication), 'Delivery observation binding changed')
            _require(all(facts[k] == record['binding'][k] for k in
                ('host', 'pull_request', 'target', 'branch', 'head', 'repository_id'))
                and facts['repository'].casefold() == publication['repository'].casefold(),
                'Delivered candidate differs from its sealed snapshot')
            complete = facts['merged'] is True and facts['state'] == 'closed' and bool(facts['merge_commit']) and bool(facts['merged_at'])
            readiness = candidate.get('readiness')
            if readiness_required and readiness is None:
                blockers.append('PR readiness evidence is missing or stale: ' + publication['id'])
                waiting = False
            elif readiness_required:
                ready = read(readiness)
                _require(type(ready['version']) is int and ready['version'] == 1 and ready['kind'] == 'delivery-readiness'
                    and ready['binding'] == _publication_binding(publication), 'Readiness binding changed')
                verification = read(ready['local_verification'])
                _require(verification['purpose'] == 'merge-evidence' and not verification['blockers']
                    and verification['candidate_digest'] == candidate['snapshot']['digest'], 'Readiness implementation proof changed')
            if not complete:
                pending.append(publication['id'])
                if facts['state'] == 'closed':
                    blockers.append('Required PR was closed without merge: ' + publication['id'])
        except (ValueError, KeyError, TypeError, OSError):
            pending.append(publication['id'])
            waiting = False
            blockers.append('Delivery observation is unavailable or incompatible: ' + publication['id'])
    pending += ['companion:' + c['task_id'] for c in plan['companions']]
    return dict(delivery_required=bool(required or plan['companions']), delivery_complete=not pending,
                pending=pending, blockers=blockers, waiting_for_merge=bool(pending) and waiting and not blockers)


def _publication_binding(publication):
    return {key: publication[key] for key in ('id', 'host', 'repository', 'target', 'branch', 'pull_request')} | dict(
        head=publication['candidate']['head'], snapshot=publication['candidate']['snapshot'],
        repository_id=publication['candidate']['repository_id'])


def retention_requirements(scope, db, task, *, context=None):
    """Known external obligations survive an optional publication or own completion."""
    from runtime import evidence
    if 'delivery' not in task:
        return dict(retention_complete=True, retention_pending=[], retention_blockers=[])
    read = lambda reference: evidence._read(scope, reference, db=db, context=context)
    plan = deepcopy(_read_plan(scope, task, db=db, context=context))
    plan['companions'] = []  # Shared acceptance remains the linked completion owner's responsibility.
    for publication in plan['publications']:
        publication['required'] = publication['required'] or publication['pull_request'] is not None
    requirements = _scope_requirements(plan, read=read, readiness_required=False)
    pending, blockers = requirements['pending'], requirements['blockers']
    for publication in plan['publications']:
        if not publication['required'] or publication['id'] in pending:
            continue
        target = next(t for t in plan['policy']['targets'] if all(t[k] == publication[k]
            for k in ('provider', 'host', 'repository', 'target')))
        if target['remote_cleanup_ref'] is None:
            continue
        try:
            cleanup = read(publication['candidate']['cleanup'])
            _require(type(cleanup['version']) is int and cleanup['version'] == 1 and cleanup['kind'] == 'delivery-cleanup'
                and cleanup['binding'] == _publication_binding(publication) and cleanup['status'] in {'absent', 'deleted', 'disabled'},
                'Remote cleanup is not settled')
            if cleanup['status'] == 'disabled':
                _ref(cleanup['decision_ref'])
        except (ValueError, KeyError, TypeError, OSError):
            pending.append('cleanup:' + publication['id'])
    for reference in task['delivery']['operations']:
        record = read(reference)
        if record.get('kind') == 'delivery-effect' and record.get('status') != 'completed':
            pending.append('operation:' + record['operation_id'])
    return dict(retention_complete=not pending and not blockers, retention_pending=pending, retention_blockers=blockers)


def completion_blockers(requirements):
    return requirements['blockers'] + (['Required delivery remains pending: ' + ', '.join(requirements['pending'])]
        if requirements['pending'] else [])


def implementation_task(task):
    """Exclude only our generated wait when recapturing retained local proof."""
    marker = task.get('delivery', {}).get('merge_wait')
    if task['state'] != 'WAITING' or not marker or 'delivery_policy' not in task['accepted_identity']:
        return task
    if (marker['verification'] != task.get('evidence', {}).get('verification')
            or marker['plan'] != task['delivery']['plan']):
        return task
    result = deepcopy(task)
    generated = dict(id=marker['condition_id'], kind='blocker', action='task', status='pending',
        reason='Implementation verified; required delivery remains pending', reference='delivery:completion')
    result['conditions'] = [c for c in task['conditions'] if c != generated]
    return result


def apply_completion(task, requirements, implementation_verified):
    """Called only by the completion writer, after all independent work is verified."""
    if 'qualification_attempt' in task:
        if task['state'] in {'READY', 'DONE'}:
            task['state'] = 'REVIEW'
        return
    if not implementation_verified:
        if task['state'] == 'DONE':
            task['state'] = 'REVIEW'
        return
    if requirements['blockers'] or not requirements['delivery_complete'] and not requirements['waiting_for_merge']:
        if task['state'] not in {'WORKING', 'REVIEW'}:
            task['state'] = 'REVIEW'
        return
    if requirements['delivery_complete']:
        task['state'] = 'DONE'
    else:
        task.pop('user_attention', None)
        task.update(state='WAITING', reason='Implementation verified; required delivery remains pending',
                    reference='delivery:completion')
        ids = {c['id'] for c in task['conditions']}
        condition_id = 'delivery:merge'
        for number in range(1, 65):
            if condition_id not in ids:
                break
            condition_id = 'delivery:merge:' + str(number)
        condition = dict(id=condition_id, kind='blocker', action='task', status='pending',
                         reason=task['reason'], reference=task['reference'])
        task['conditions'].append(condition)
        task['delivery']['merge_wait'] = dict(condition_id=condition_id,
            verification=task['evidence']['verification'], plan=task['delivery']['plan'])
        _require(len(task['conditions']) <= 64, 'Task condition history exceeds its bound')
    for condition in task['conditions']:
        if 'continuation' in condition:
            condition['continuation']['revoked'] = True


def operate(scope, action: str, request: dict, *, owner=None) -> dict:
    from runtime import evidence
    evidence._bounded(request)
    if action == 'status':
        state._fields(request, ('client', 'session_id'))
        with state._database(scope) as db:
            task = state._task(db, state._scope_id(scope), request['client'], request['session_id'])
            _require(task is not None, 'Delivery task is not bound')
            state._compatible(scope, task)
            return dict(task=task, plan=_read_plan(scope, task, db=db) if 'delivery' in task else None,
                        **completion_requirements(scope, db, task))
    if action == 'bind-worktree':
        return _bind_worktree(scope, request, owner)
    if action == 'reconcile':
        return _reconcile(scope, request, owner)
    if action in {'ready', 'reopen', 'settle'}:
        return _manual_delivery(scope, action, request, owner)
    _require(action == 'plan', 'Unsupported delivery operation')
    evidence._request(request, ('plan',), ('override', 'previous_plan', 'change_decision_ref', 'milestones'))
    with state._database(scope, write=True) as db:
        task = evidence._task(db, scope, request)
        saved = task.get('accepted_identity', {}).get('delivery_policy')
        _require(saved is not None, 'Delivery policy has not been adopted')
        previous = None
        if 'delivery' in task:
            _require('previous_plan' in request and 'change_decision_ref' in request,
                     'Delivery plan already exists; scope revision needs its previous plan and accepted decision')
            state._artifact_reference(request['previous_plan'])
            _ref(request['change_decision_ref'])
            _require(request['previous_plan'] == task['delivery']['plan'], 'Scope revision plan changed')
            _require(len(task['delivery']['operations']) < 64, 'Delivery operation history exhausted')
            previous = _read_plan(scope, task, db=db)
            saved = previous['policy']
        else:
            _require('previous_plan' not in request and 'change_decision_ref' not in request,
                     'Initial delivery scope cannot revise an absent plan')
        plan = _plan(request['plan'], saved, request.get('override'), previous=previous)
        if previous:
            _require(plan['decision_ref'] == request['change_decision_ref'], 'Scope revision decision changed')
            old_publications = {p['id']: p for p in previous['publications']}
            expanded = (len(plan['publications']) > len(previous['publications'])
                or len(plan['companions']) > len(previous['companions'])
                or any(p['required'] and not old_publications[p['id']]['required']
                       for p in plan['publications'] if p['id'] in old_publications))
            _require(not expanded or not task.get('milestones') or 'milestones' in request,
                     'Expanded scope needs revised accepted milestones before progress is computed')
        if 'milestones' in request:
            task['milestones'] = state._milestones(request['milestones'])
        reference = evidence._artifact(scope, plan, owner=evidence._artifact_owner(scope, task), db=db)
        if previous:
            change = dict(version=1, kind='delivery-plan-change', previous_plan=task['delivery']['plan'],
                          plan=reference, decision_ref=request['change_decision_ref'])
            task['delivery']['operations'].append(evidence._artifact(scope, change,
                owner=evidence._artifact_owner(scope, task), db=db))
            task['delivery']['plan'] = reference
            if task['state'] == 'DONE':
                task['state'] = 'REVIEW'
        else:
            task['delivery'] = dict(plan=reference, operations=[])
        task['revision'] += 1
        state._save(db, state._scope_id(scope), task)
        return dict(task=task, plan=plan, **completion_requirements(scope, db, task))


def _manual_delivery(scope, action, request, owner):
    from runtime import evidence, repository_gate
    from runtime.adapters.github_delivery import GitHubDelivery
    from scripts.delivery_coordinator import DeliveryOperationOwner
    evidence._request(request, ('operation_id', 'publication_id'))
    for key in ('operation_id', 'publication_id'):
        _ref(request[key])
    _require(isinstance(owner, DeliveryOperationOwner), 'A verified delivery owner is required')
    owner.authorize(scope, action, request)
    digest = evidence._digest(request)
    with state._database(scope) as db:
        task = evidence._task(db, scope, request, revision=False)
        if action != 'reopen':
            state._ordinary_capability(scope, task, 'delivery-ready' if action == 'ready' else 'delivery-merge')
        _require('delivery' in task, 'Delivery scope is missing')
        plan = _read_plan(scope, task, db=db)
        publication = next((p for p in plan['publications'] if p['id'] == request['publication_id']), None)
        _require(publication is not None and publication['candidate'] is not None, 'Observed publication is missing')
        if action != 'settle':
            for reference in task['delivery']['operations']:
                previous = evidence._read(scope, reference, db=db)
                if previous.get('operation_id') == request['operation_id']:
                    _require(previous.get('action') == action and previous.get('request_digest') == digest,
                             'Delivery operation identity was reused with changed inputs')
                    return dict(task=task, plan=plan, replay=True, **completion_requirements(scope, db, task))
        _require(task['revision'] == request['expected_revision'], 'Task revision conflict')
        _require(task['state'] not in {'PAUSED', 'BLOCKED', 'CANCELLED'}, 'Explicit task condition prevents delivery')
        if action != 'reopen':
            _require(plan['policy']['merge'] == 'manual' and len(plan['publications']) == 1
                and publication['required'] and not plan['companions'], 'Manual pilot needs its complete bounded scope')
        if action == 'settle':
            requirements = completion_requirements(scope, db, task)
            _require(requirements['delivery_complete'] and not requirements['blockers'], 'Required delivery is unresolved')
            marker = task['delivery'].get('merge_wait')
            _require(task['state'] == 'WAITING' and marker is not None
                and marker['verification'] == task.get('evidence', {}).get('verification'), 'Generated merge wait is unavailable or stale')
            original = evidence._read(scope, marker['plan'], db=db)
            _require(_merge_wait_acceptance(original) == _merge_wait_acceptance(plan), 'Accepted scope changed during merge wait')
            conditions = [c for c in task['conditions'] if c['id'] == marker['condition_id']]
            _require(len(conditions) == 1 and conditions[0]['status'] == 'pending'
                and conditions[0]['kind'] == 'blocker' and conditions[0]['action'] == 'task'
                and conditions[0]['reference'] == 'delivery:completion'
                and not any(c['status'] == 'pending' and c['id'] != marker['condition_id'] for c in task['conditions']),
                'Unrelated or unresolved task conditions prevent settlement')
            resolution = 'delivery:' + evidence._digest([request['operation_id'], publication['candidate']['observation']])
        else:
            _require(action != 'ready' or task['state'] in {'WORKING', 'REVIEW'}, 'Resolve task status before readiness')
            _require(len(task['delivery']['operations']) < 64, 'Delivery operation history exhausted')
            previous_plan = deepcopy(task['delivery']['plan'])
            binding = _publication_binding(publication)
            marker = evidence._digest([state._canonical_plain(evidence._artifact_owner(scope, task).task), publication['id']])
    if action == 'settle':
        state.update_task(scope, {key: request[key] for key in evidence.IDENTITY} | dict(state='REVIEW',
            reason='Required manual merge observed', reference=resolution, resolution_ref=resolution,
            resolutions=[dict(id=task['delivery']['merge_wait']['condition_id'], reference=resolution)]))
        return operate(scope, 'status', dict(client=request['client'], session_id=request['session_id']))
    source_type = getattr(repository_gate, 'RepositoryReadiness', None)
    acquire = getattr(owner.gate, 'acquire_readiness', None)
    _require(isinstance(source_type, type) and callable(acquire), 'Qualified repository readiness source is unavailable')
    with acquire(scope, request | dict(action=action), binding) as held:
        _require(isinstance(held, source_type) and isinstance(held.repository, GitHubDelivery)
            and held.repository is owner.repository, 'A live private repository readiness acquisition is required')
        held.recheck()
        if action == 'ready':
            with state._database(scope) as db:
                current = evidence._task(db, scope, request, revision=False)
                state._ordinary_capability(scope, current, 'delivery-ready')
                _require(current['delivery']['plan'] == previous_plan, 'Delivery scope changed before metadata effect')
                _ready_verification(scope, db, current, publication, held.local_verification)
        operation = dict(publication=deepcopy(publication), head=publication['candidate']['head'],
            repository_id=publication['candidate']['repository_id'], marker=marker,
            operation_id=evidence._digest([action, request['operation_id']]), title='Task delivery',
            body='Ready for manual merge' if action == 'ready' else 'Readiness withdrawn for accepted implementation work')
        facts = held.repository.execute(action, operation)
        held.recheck()
        def publish(db):
            current = evidence._task(db, scope, request, revision=False)
            if action == 'ready':
                state._ordinary_capability(scope, current, 'delivery-ready')
            _require(current['delivery']['plan'] == previous_plan
                and current['state'] not in {'PAUSED', 'BLOCKED', 'CANCELLED'}, 'Delivery changed before publication')
            if action == 'ready':
                _ready_verification(scope, db, current, publication, held.local_verification)
            def artifact(value):
                dependencies = tuple(state._canonical_freeze(edge) for edge in evidence._artifact_dependencies(value))
                return evidence._artifact(scope, value,
                    owner=evidence._LinkedOwner(evidence._artifact_owner(scope, current), dependencies), db=db)
            effect = artifact(dict(version=1, kind='delivery-effect', status='completed', action=action,
                operation_id=request['operation_id'], request_digest=digest, previous_plan=previous_plan,
                binding=binding, source=deepcopy(held.reference),
                local_verification=deepcopy(held.local_verification) if action == 'ready' else None,
                observation=facts, coordinator_ref=owner._decision['reference']))
            publication['candidate']['readiness'] = (artifact(dict(version=1, kind='delivery-readiness',
                binding=binding, local_verification=deepcopy(held.local_verification),
                source=deepcopy(held.reference), operation=effect)) if action == 'ready' else None)
            current['delivery']['plan'] = artifact(plan)
            current['delivery']['operations'].append(effect)
            current['revision'] += 1
            state._save(db, state._scope_id(scope), current)
            return dict(task=current, plan=plan, replay=False, **completion_requirements(scope, db, current))
        return held.finalize(publish)


def _ready_verification(scope, db, task, publication, reference):
    from runtime import evidence
    _require(task.get('evidence', {}).get('verification') == reference, 'Current merge verification changed')
    proof = evidence._read(scope, reference, db=db)
    _require(proof['purpose'] == 'merge-evidence' and not proof['blockers']
        and proof['candidate_digest'] == publication['candidate']['snapshot']['digest'], 'Merge verification differs from sealed candidate')


def pending(scope, *, cursor: str | None = None, limit: int = 32) -> dict:
    """Inspect at most one bounded page; never scan checkouts or contact a provider."""
    from runtime import evidence
    _require(type(limit) is int and 1 <= limit <= 32, 'Pending page limit must be between 1 and 32')
    context = evidence._evidence_context(scope)
    authority = evidence._digest([scope.project_id, state._canonical_plain(context.authority_set[0])])
    after = ['', '', '', '']
    if cursor is not None:
        try:
            state._text(cursor, 'pending cursor', 32768)
            value = evidence.json_object(base64.b64decode(cursor, altchars=b'-_', validate=True), limit=32768)
            state._fields(value, ('version', 'authority', 'after'))
            _require(type(value['version']) is int and value['version'] == 1 and value['authority'] == authority,
                     'Pending cursor belongs to a different authority')
            after = value['after']
            _require(isinstance(after, list) and len(after) == 4, 'Invalid pending cursor')
            for part in after:
                state._text(part, 'pending cursor identity', 4096)
        except (ValueError, TypeError, binascii.Error):
            raise state.StateError('Invalid pending cursor for this authority') from None
    records = []
    with state._database(scope) as db:
        rows = db.execute('select scope_id,client,session_id,task_id from tasks '
            'where (scope_id,client,session_id,task_id) > (?,?,?,?) '
            'order by scope_id,client,session_id,task_id limit ?', (*after, limit + 1)).fetchall()
        selected = rows[:limit]
        for identity in selected:
            task = state._task(db, *identity)
            _require(task['accepted_identity']['project_id'] == scope.project_id, 'Foreign task in delivery authority')
            requirements = completion_requirements(None, db, task, context=context)
            retention = retention_requirements(None, db, task, context=context)
            if not requirements['delivery_complete'] or requirements['blockers'] or not retention['retention_complete']:
                records.append(dict(zip(('scope_id', 'client', 'session_id', 'task_id'), identity)) |
                    dict(state=task['state'], plan=task.get('delivery', {}).get('plan'), **requirements, **retention))
    next_cursor = None
    if len(rows) > limit:
        next_cursor = base64.urlsafe_b64encode(evidence._raw(dict(version=1, authority=authority,
            after=list(selected[-1])))).decode('ascii')
    return dict(ok=True, records=records, cursor=next_cursor, inspected=len(selected),
                coverage='known retained tasks in this authority')


def _bind_worktree(scope, request, owner):
    from runtime import changes, evidence, startup
    from scripts.delivery_coordinator import DeliveryOperationOwner
    evidence._request(request, ('operation_id',))
    _ref(request['operation_id'])
    _require(isinstance(owner, DeliveryOperationOwner), 'A verified native owner is required')
    owner.authorize(scope, 'bind-worktree', request)
    digest = evidence._digest(request)
    with state._database(scope) as db:
        task = evidence._task(db, scope, request, revision=False)
        _require('delivery' in task and task['mode'] == 'editable', 'Editable delivery plan is missing')
        for reference in task['delivery']['operations']:
            previous = evidence._read(scope, reference, db=db)
            if previous.get('operation_id') == request['operation_id']:
                _require(previous['action'] == 'bind-worktree' and previous['request_digest'] == digest,
                         'Delivery operation identity was reused with changed inputs')
                return dict(task=task, plan=_read_plan(scope, task, db=db), replay=True)
        _require(task['revision'] == request['expected_revision'], 'Task revision conflict')
        plan = _read_plan(scope, task, db=db)
        _require(plan['workspace'] is None and len(task['delivery']['operations']) < 64,
                 'Workspace already bound or delivery history exhausted')
        original = deepcopy(task['delivery']['plan'])
    # Native ownership and physical Git identity are distinct observations. Neither
    # can be taken from a caller-supplied path or substituted for D4 admission.
    observation = owner.native.observe_worktree(scope.repository_root)
    state._fields(observation, ('root', 'owner', 'attachment', 'reference'))
    for key in ('attachment', 'reference'):
        _ref(observation[key])
    _require(observation['owner'] == scope.client
             and observation['root'] == str(scope.repository_root), 'Native worktree ownership is unresolved')
    evidence._canonical_inputs(scope)
    root, git_dir, common = changes._checkout(scope.repository_root)
    _require((root, git_dir, common) == (scope.repository_root, scope.git_dir, scope.git_common_dir),
             'Observed workspace differs from accepted checkout')
    _require(not plan['policy']['separate_worktrees'] or git_dir != common,
             'Native isolated workspace is required before implementation')
    code, head = startup.read_git(root, 'rev-parse', '--verify', 'HEAD', limit=256)
    _require(code == 0, 'Workspace base could not be observed')
    base = head.decode('ascii').strip()
    _require(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', base), 'Workspace base is invalid')
    workspace = dict(root=str(root), git_dir=str(git_dir), git_common_dir=str(common), base=base,
                     owner=observation['owner'], attachment=observation['attachment'],
                     reference=observation['reference'], cleanup_owner='client')
    with state._database(scope, write=True) as db:
        task = evidence._task(db, scope, request)
        _require(task['delivery']['plan'] == original, 'Delivery plan changed during native observation')
        evidence._canonical_inputs(scope)
        plan['workspace'] = workspace
        artifact_owner = evidence._artifact_owner(scope, task)
        record = dict(version=1, kind='delivery-workspace-binding', action='bind-worktree',
                      operation_id=request['operation_id'], request_digest=digest,
                      previous_plan=original, workspace=workspace)
        reference = evidence._artifact(scope, record, owner=artifact_owner, db=db)
        task['delivery']['plan'] = evidence._artifact(scope, plan, owner=artifact_owner, db=db)
        task['delivery']['operations'].append(reference)
        task['revision'] += 1
        state._save(db, state._scope_id(scope), task)
        return dict(task=task, plan=plan, replay=False)


def _reconcile(scope, request, owner):
    from runtime import evidence, startup
    from runtime.adapters.github_delivery import GitHubDelivery
    from scripts.delivery_coordinator import DeliveryOperationOwner
    evidence._request(request, ('operation_id', 'publication_id'), ('original', 'target_revision', 'pull_request'))
    for key in ('operation_id', 'publication_id'):
        _ref(request[key])
    _require(isinstance(owner, DeliveryOperationOwner), 'A verified delivery observation owner is required')
    owner.authorize(scope, 'reconcile', request)
    _require(isinstance(owner.repository, GitHubDelivery), 'A bounded provider observation source is required')
    context = evidence._evidence_context(scope)
    authority = state._canonical_plain(context.authority_set[0])
    digest = evidence._digest(request)
    with state._database(scope) as db:
        producer = evidence._task(db, scope, request, revision=False)
        producer_ref = state._canonical_plain(evidence._artifact_owner(scope, producer).task)
        original_ref = request.get('original', producer_ref)
        state._task_reference(original_ref)
        _require(original_ref['authority'] == authority, 'Reconciliation requires the same surviving physical authority')
        original = state._task(db, original_ref['scope_id'], original_ref['client'],
                               original_ref['session_id'], original_ref['task_id'])
        _require(original is not None and original['accepted_identity']['project_id'] == scope.project_id
            and 'delivery' in original, 'Original delivery task is unavailable')
        state._assert_open(original)
        _require(all(original['pins'][key] == producer['pins'][key] for key in
                     ('schema_version', 'runtime_digest', 'policy_digest')),
                 'Original delivery needs its retained pinned package')
        for reference in original['delivery']['operations']:
            prior = evidence._read(None, reference, db=db, context=context)
            if prior.get('operation_id') == request['operation_id']:
                _require(prior.get('action') == 'reconcile' and prior['request_digest'] == digest,
                         'Delivery operation identity was reused with changed inputs')
                return dict(task=original, observation=prior['observation'], replay=True,
                    **completion_requirements(None, db, original, context=context))
        target_revision = request.get('target_revision', request['expected_revision'])
        state._integer(target_revision, 'original task revision', 1)
        _require(('original' in request) == ('target_revision' in request), 'Original task and revision must be supplied together')
        _require(producer['revision'] == request['expected_revision'] and original['revision'] == target_revision,
                 'Task revision conflict')
        _require(len(original['delivery']['operations']) < 64, 'Delivery operation history exhausted')
        previous_plan = deepcopy(original['delivery']['plan'])
        plan = _read_plan(None, original, db=db, context=context)
        publication = next((p for p in plan['publications'] if p['id'] == request['publication_id']), None)
        _require(publication is not None, 'Unknown publication in original scope')
        number = request.get('pull_request', publication['pull_request'])
        _require(type(number) is int and 0 < number <= 2 ** 53
            and publication['pull_request'] in (None, number), 'Publication PR identity changed')
        unbound = publication['candidate'] is None
        if unbound:
            _require(original_ref == producer_ref and plan['workspace'] is not None and 'evidence' in original,
                     'Unbound publication needs its original admitted workspace and evidence plan')
            evidence_plan = evidence._read(scope, original['evidence']['plan'], db=db)
    observed_publication = publication | dict(pull_request=number)
    facts = owner.repository.observe(observed_publication)
    if unbound:
        observe = getattr(owner.native, 'observe_publication', None)
        _require(callable(observe), 'Native publication ownership is unresolved')
        native = observe(scope, deepcopy(observed_publication), number)
        state._fields(native, ('host', 'repository', 'repository_id', 'pull_request', 'target', 'branch', 'head', 'reference'))
        _ref(native['reference'])
        _require(type(native['repository_id']) is int and type(native['pull_request']) is int
            and all(native[key] == facts[key] for key in native if key != 'reference'),
                 'Native publication ownership differs from provider identity')
        evidence._canonical_inputs(scope)
        code, branch = startup.read_git(scope.repository_root, 'symbolic-ref', '--quiet', '--short', 'HEAD', limit=512)
        _require(code == 0 and branch.decode('utf-8').strip() == publication['branch'], 'Local publication branch differs')
        candidate = evidence.capture_candidate(scope, evidence_plan)
        _require(not any(entry[3:] != str(state.PROJECT_FILE) for entry in candidate['dirty_inventory']),
                 'Publication candidate contains uncommitted changes')
        _require(candidate['head'] == facts['head'], 'Provider head differs from the committed local candidate')
    evidence._canonical_inputs(scope)
    with state._database(scope, write=True) as db:
        current_producer = evidence._task(db, scope, request)
        current = state._task(db, original_ref['scope_id'], original_ref['client'],
                             original_ref['session_id'], original_ref['task_id'])
        _require(current is not None and current['revision'] == target_revision
            and current['delivery']['plan'] == previous_plan, 'Original delivery changed during observation')
        state._assert_open(current)
        artifact_owner = evidence._artifact_owner(scope, current_producer)
        def publish(value):
            dependencies = evidence._artifact_dependencies(value)
            if original_ref != producer_ref:
                dependencies.append(dict(kind='task', reference=original_ref))
            linked_owner = evidence._LinkedOwner(artifact_owner,
                tuple(state._canonical_freeze(edge) for edge in dependencies))
            return evidence._artifact(scope, value, owner=linked_owner, db=db)
        if unbound:
            _require(evidence.capture_candidate(scope, evidence_plan) == candidate,
                     'Local candidate changed during publication observation')
            publication['pull_request'] = number
            publication['candidate'] = dict(head=candidate['head'], snapshot=publish(candidate),
                repository_id=facts['repository_id'], ownership_ref=native['reference'], observation=None, readiness=None)
        record = dict(version=1, kind='delivery-observation', action='reconcile', operation_id=request['operation_id'],
            request_digest=digest, previous_plan=previous_plan, original_task=original_ref, producer_task=producer_ref,
            binding=_publication_binding(publication), observation=facts, observed_at=state._owner_time(),
            coordinator_ref=owner._decision['reference'])
        reference = publish(record)
        publication['candidate']['observation'] = reference
        current['delivery']['plan'] = publish(plan)
        current['delivery']['operations'].append(reference)
        current['revision'] += 1
        state._save(db, original_ref['scope_id'], current)
        return dict(task=current, observation=facts, replay=False,
            **completion_requirements(None, db, current, context=context))
