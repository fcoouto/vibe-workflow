"""Native delegation and review through Smart Routing `prepare` in a managed project (C.3 design 3.1-3.2, 6.3 C3-2).

Phase W only: a checkout that is not on `managed_projects` keeps today's caller-supplied `selection` unchanged. In a
managed project every selection mode is admitted by the in-process routing service (`scripts.routing.managed_service`)
after delegation's own reservation commits, bound to the planned job (`workflow`) and the delegation attempt id:

- `explicit` and `inherit` become `assignment.choice`, a hard constraint: routing admits that route or answers
  `explicit_choice_unavailable`, never a substitute. `routed` names no model and lets routing choose.
- A committed assignment whose routing answer is pending or blocked is not launched (`launch_admitted: false`). A
  replay of the same request re-runs `prepare` (idempotent by attempt id) and grants the one launch permit only on
  the call that obtains the admission. A catalog or host mismatch releases it through `reconcile_assignment`.
- `release()` records routing `not_dispatched` before it calls `reconcile_assignment`. If that reconcile fails, the
  assignment stays `prepared` with no launch permit until the coordinator reconciles it (fail closed).
- Codex native subagents are an advisory host: `dispatch` runs at the last pre-spawn step, and its token is dropped
  unread; the coordinator spawns with the returned route. Reconciliation closes the routing holds of every record
  that has a routing admission, whether or not the project is still managed.
- One permit per assignment across the managed boundary (PR #217 review S1): each call reads `managed` once; a
  project that became managed during the call releases the fresh assignment with no permit; a replay of an
  assignment committed before the project was managed is refused; and `set managed_projects` refuses to add a
  project while `open_unrouted_assignments` lists any prepared, unobserved native assignment, checking again after
  its write and writing the project back out on a hit. The check covers every enrolled client with runtime state.

Design: docs/superpowers/specs/2026-11-10-smart-routing-c3-wiring-design.md. Nothing here calls a model, the network,
a secret store, an operator op or `set managed_projects`, and no request field selects a scope or observations.
"""
from contextlib import closing
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from runtime import delegation, evidence, routing, routing_launch, state

HOST = 'codex_native'
ROLES = dict(worker='implementer', review='reviewer')
MODES = ('explicit', 'inherit', 'routed')
REASON = re.compile(r'[a-z][a-z0-9_]{0,63}')
PROOF_REF = 'delegation_routing:never-dispatched'
PRE_MANAGED = 'legacy_route_retired'  # a replay of an assignment committed before the project was managed
RACED = 'managed_during_prepare'
MAX_OPEN = 64
# The one `managed` answer of the current `prepare` call, passed down to the `_discovery` and binding hooks.
_MANAGED = ContextVar('delegation_routing_managed', default=None)
LIMITATION = 'Codex native subagents are an advisory host: routing chooses and accounts but cannot block a send'


def _command():
    from scripts import routing as command  # the plugin package root is on sys.path wherever runtime is imported
    return command


def managed(scope):
    """Whether this checkout is a managed project in the default routing settings. Fails closed: a corrupt routing
    `settings.json` counts as managed (C3-0b), and native delegation then blocks with `settings_unavailable`."""
    return routing_launch.managed(scope.repository_root, _command().default_settings())


def _managed_now(scope):
    found = _MANAGED.get()
    return managed(scope) if found is None else found


# -- line-neutral hooks in runtime/delegation.py -------------------------------------------------------------------
def routed(scope, selection):
    """`_discovery`: a managed project's `routed` selection names no model or effort yet; routing chooses them."""
    return type(selection) is dict and selection.get('mode') == 'routed' and _managed_now(scope)


def records_model(scope, selection):
    """`binding.requested_model`: only an unmanaged explicit selection records its model. In a managed project the
    routing admission is the only record of the requested model (`admitted_route`)."""
    return selection['mode'] == 'explicit' and not _managed_now(scope)


def _call(service, request):
    return service.handle(json.dumps(request).encode())


def _holds(service):
    """The routing ledger snapshot (read-only), or {} before the ledger exists. An unreadable or busy ledger is a
    delegation error, never "no hold" (fail closed)."""
    try:
        ledger = service._ledger(False)
        return {} if ledger is None else ledger.snapshot()
    except Exception:  # noqa: BLE001 - Blocked, BudgetError or sqlite3.Error from the routing command's stores
        service.close()
        raise state.StateError('Routing ledger is unavailable or busy') from None


def _hold(service, attempt_id):
    """(hold state or None, admission record or None) for a routing attempt, read-only."""
    data = _holds(service)
    hold = data.get('holds', {}).get(attempt_id)
    return (None if hold is None else hold['state']), _admission(data, attempt_id)


def _admission(data, attempt_id):
    return next((record for record in data.get('admissions', []) if record.get('attempt_id') == attempt_id), None)


def _route(scope, record, admission):
    """`{model, effort}` of an admission, matched by its route-identity digest against the record's native catalog."""
    digest = (admission or {}).get('route_identity')
    for entry in _catalog(scope, record) if digest else ():
        if routing.route_identity(*(entry[field] for field in routing.ROUTE_IDENTITY)) == digest:
            return dict(model=entry['model'], effort=entry['effort'])
    return None


def admitted_route(scope, attempt_id):
    """Read-only accessor for observation: `{model, effort}` routing admitted for a delegation attempt, or None."""
    record = _committed(scope, attempt_id)
    with closing(_command().managed_service(scope.repository_root, scope=scope)) as service:
        found = _hold(service, attempt_id)[1]
    return _route(scope, record, found)


def _record(service, attempt_id, kind, event_id):
    return _call(service, dict(v=1, op='record', event=dict(event_id=event_id[:128], attempt_id=attempt_id, kind=kind)))


# -- admission ----------------------------------------------------------------------------------------------------
def _locator(scope, request, attempt_id):
    return dict(client=request['client'], cwd=str(scope.repository_root), session_id=request['session_id'],
                task_id=request['task_id'], job_id=request['job_id'], attempt_id=attempt_id)


def _choice(selection):
    if selection['mode'] == 'routed':
        return None
    chosen = selection.get('inheritance') if selection['mode'] == 'inherit' else selection
    return routing_launch.choice(HOST, 'subscription', model=chosen['model'], effort=chosen['effort'])


def _writer_models(service, scope, request, attempt_id):
    """Review independence (F13): the models routing admitted to this task's worker assignments, from delegation's
    own records and the routing ledger, never from the request."""
    with state._database(scope) as db:
        records = delegation._ledger(scope, db, task=request)
    models = set()
    for record in records:
        if record['data']['role'] != 'worker' or record['id'] == attempt_id:
            continue
        model = (_route(scope, record, _hold(service, record['id'])[1]) or {}).get('model') or \
            record['data']['binding'].get('requested_model')
        if model:
            models.add(model)
    return sorted(models)


def admit(service, scope, request, *, attempt_id, routing_attempt, role, choice, constraints=None, job=None):
    """One routing `prepare` for a committed delegation assignment, bound to its planned job and attempt. `job` is
    the canonical job (`delegation.routing_job`); only the self-check supplies it."""
    preflight = routing_launch.preflight(_call(service, dict(v=1, op='status')))
    if preflight is not None:
        return dict(state='blocked', reason=preflight)
    locator = _locator(scope, request, attempt_id)
    assignment = dict(attempt_id=routing_attempt, task=request['task_id'], role=ROLES[role],
                      scope=routing_launch.assignment_scope(scope.repository_root))
    if choice is not None:
        assignment['choice'] = choice
    try:
        body = routing_launch.prepare_request(assignment, job or delegation.routing_job(scope, locator), workflow=locator,
                                              constraints=constraints, planned=True)
    except routing_launch.Refused as error:
        return dict(state='blocked', reason=error.reason)
    answer = _call(service, body)
    if answer.get('state') == 'blocked' and answer.get('reason') == 'workflow_changed':
        answer = _call(service, body)  # D6Workflow.view raced a commit: retry once, then report it
    return answer


def _catalog(scope, record):
    """The host's actual catalog: every (model, supported effort) native discovery observed for this assignment."""
    observed = evidence._read(scope, record['body']['discovery'])
    return [routing_launch.route_identity(HOST, model=item['model'], effort=effort)
            for item in observed['catalog'] for effort in item['supported_efforts']]


def _committed(scope, attempt_id):
    with state._database(scope) as db:
        found = None if db is None else next((r for r in delegation._ledger(scope, db, assignment_id=attempt_id)
                                              if r['owner']['scope_id'] == state._scope_id(scope)), None)
    if found is None:
        raise state.StateError('Exact supported assignment is absent')
    return found


def prepare(scope, request, role, inner):
    """`prepare_assignment` / `prepare_review`: delegation's reservation, then routing in a managed project."""
    current = managed(scope)  # read once per call (review S1a); the hooks inside `inner` see the same answer
    token = _MANAGED.set(current)
    try:
        reply = inner(scope, request, role=role)
    finally:
        _MANAGED.reset(token)
    workstream = 'dispatch_prepared' in reply
    if not current:
        if reply['replayed'] or reply['status'] != 'prepared' or not managed(scope):
            return reply  # unmanaged: today's behaviour, unchanged
        reply = dict(reply, launch_admitted=False, routing=dict(state='blocked', reason=RACED))
        if workstream:
            reply['dispatch_prepared'] = False
        reply['release'] = release(scope, request, reply['attempt_id'], RACED)  # this call handed out no permit
        return reply
    if reply['status'] != 'prepared':
        return reply
    reply = dict(reply, launch_admitted=False, limitations=[*reply['limitations'], LIMITATION])
    if workstream:
        reply['dispatch_prepared'] = False
    attempt = reply['attempt_id']
    record = _committed(scope, attempt)
    if record['data']['observation'] is not None or 'workstream_dispatch' in record['data']:
        return reply  # already launched or consumed: a replay never grants a second permit
    if reply['replayed'] and record['data']['binding']['requested_model'] is not None:
        return dict(reply, routing=dict(state='blocked', reason=PRE_MANAGED))  # committed unmanaged: reconcile first
    selection = record['body']['request']['selection']
    with closing(_command().managed_service(scope.repository_root, scope=scope)) as service:
        constraints = None
        if role == 'review':
            excluded = _writer_models(service, scope, record['body']['request'], attempt)
            constraints = dict(excluded_models=excluded) if excluded else None
        answer = admit(service, scope, request, attempt_id=attempt, routing_attempt=attempt, role=role,
                       choice=_choice(selection), constraints=constraints)
        if answer.get('state') != 'admitted':
            reply['routing'] = dict(state=answer.get('state', 'blocked'), reason=answer.get('reason'))
            return reply
        if answer.get('duplicate'):
            reply['routing'] = dict(state='admitted', duplicate=True)
            return reply  # the one permit was granted on the call that obtained this admission
        admission = answer['admission']
        facts, reason = routing_launch.admitted_check(admission, host=HOST, catalog=_catalog(scope, record),
                                                      settings_path=service.settings_path)
        if reason is None and not workstream:
            sent = _call(service, dict(v=1, op='dispatch', attempt_id=attempt, request=facts))
            if sent.get('state') != 'dispatched':
                reason = sent.get('reason') if routing._text(sent.get('reason')) else 'dispatch_refused'
            sent.pop('authority', None)  # advisory host: the token is never stored, logged or returned
        if reason is not None:
            reply['routing'] = dict(state='blocked', reason=reason)
            if reply['replayed']:  # an earlier call may have handed out a launch (e.g. before the project was
                _record(service, attempt, 'not_dispatched', '%s:%s' % (reason, attempt))  # managed): no proof
            else:  # this call committed the assignment and handed out no permit: release it now
                reply['release'] = release(scope, request, attempt, reason, service=service)
            return reply
    reply.update(routing=dict(state='admitted', mode=admission.get('mode')),
                 route=dict(model=facts['model'], effort=facts['variant'], attempt_id=attempt))
    if workstream:
        reply['dispatch_prepared'] = True
    else:
        reply['launch_admitted'] = True
    return reply


# -- release and settlement -----------------------------------------------------------------------------------------
def release(scope, request, attempt_id, reason, *, service=None):
    """Release a committed, never-launched managed assignment: record routing `not_dispatched` (closing any admitted
    hold), then settle the assignment through delegation's own `reconcile_assignment` with a not-started proof."""
    if not REASON.fullmatch(reason or ''):
        raise state.StateError('Invalid routing release reason')
    if service is None:
        with closing(_command().managed_service(scope.repository_root, scope=scope)) as owned:
            return release(scope, request, attempt_id, reason, service=owned)
    _require_unlaunched(service, attempt_id)
    _record(service, attempt_id, 'not_dispatched', '%s:%s' % (reason, attempt_id))
    identity = {key: request[key] for key in evidence.IDENTITY}
    with state._database(scope) as db:
        record = delegation._assignment(scope, db, dict(identity, attempt_id=attempt_id), roles=('worker', 'review'))
        task = delegation._assignment_task(scope, db, dict(identity, attempt_id=attempt_id), revision=False)
    state_ = record['data']
    if state_['status'] != 'prepared' or state_['observation'] is not None or 'workstream_dispatch' in state_ or \
            'workstream_continuation' in state_:
        raise state.StateError('Only a never-launched managed assignment can be released by routing')
    plan = delegation._assignment_plan(scope, record, task)
    candidate = evidence.capture_candidate(scope, plan)
    original = evidence._read(scope, state_['candidate'])
    changed = sorted(name for name in original['fingerprints'].keys() | candidate['fingerprints'].keys()
                     if not name.startswith('instruction:')
                     and original['fingerprints'].get(name) != candidate['fingerprints'].get(name))
    binding = state_['binding']
    proof = dict(version=2, kind='assignment-writer-reconciliation',
                 identity={key: task[key] for key in evidence.IDENTITY[:4]}, expected_pins=task['pins'],
                 expected_revision=task['revision'], scope_id=state._scope_id(scope), attempt_id=attempt_id,
                 dispatch_id=record['owner']['dispatch_id'], assignment_digest=state_['assignment']['digest'],
                 claims_digest=evidence._digest(state_['claims']), candidate_digest=state_['candidate']['digest'],
                 current_candidate_digest=evidence._digest(candidate), d6_plan_digest=state_['plan_digest'],
                 observation_digest=None, result_digest=None, launch_state='not-started', stopped=True,
                 descendants_stopped=True, observation_ref='%s:%s' % (PROOF_REF, reason),
                 coordinator_ref='delegation_routing:release',
                 native_identity=dict({key: binding[key] for key in
                                       ('client_surface', 'native_version', 'parent_session_id', 'task_name')},
                                      **dict.fromkeys(('parent_turn_id', 'spawn_item_id', 'worker_id',
                                                       'worker_turn_id', 'result_item_id'))))
    assessment = dict(outcome='cancelled', changed_paths=changed[:64],
                      result_assessment='Routing released the assignment before launch: ' + reason,
                      partial_change_assessment='No worker was launched; candidate changes are not its effects',
                      evidence=['routing:not_dispatched:' + reason], coordinator_ref='delegation_routing:release')
    with tempfile.TemporaryDirectory(prefix='routing-release-') as directory:
        path = Path(directory).resolve() / 'proof.json'
        raw = evidence._raw(proof)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
        settled = delegation.reconcile_assignment(scope, dict(identity, expected_revision=task['revision'],
            attempt_id=attempt_id, host_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()),
            assessment=assessment))
    return dict(settled, routing=dict(state='released', reason=reason))


def _require_unlaunched(service, attempt_id):
    hold, _ = _hold(service, attempt_id)
    if hold not in (None, 'admitted', 'released'):
        raise state.StateError('A dispatched routing attempt needs host reconciliation, not a routing release')


def settled(scope, record, reply):
    """`reconcile_assignment`: close the routing holds of a settled managed assignment and its continuations. A
    dispatched hold records `complete` (the coordinator never spawned, or the child finished); an admitted one
    records `not_dispatched`. It goes by the record's routing holds, not by whether the project is still managed
    (review S3); a record with no routing hold, and so every unmanaged one, gets today's reply unchanged."""
    if record['data']['role'] not in ROLES:
        return reply
    closed = []
    with closing(_command().managed_service(scope.repository_root, scope=scope)) as service:
        try:
            holds = _holds(service).get('holds', {})
        except state.StateError:  # the assignment is settled; its holds close at the next reconcile or by the operator
            return dict(reply, routing=dict(state='unavailable', reason='ledger_unavailable')) if managed(scope) \
                else reply
        for attempt, hold in sorted(holds.items()):
            if hold['closed'] or not (attempt == record['id'] or attempt.startswith(record['id'] + '.c')):
                continue
            kind = 'complete' if hold['state'] == 'dispatched' else 'not_dispatched' if hold['state'] == 'admitted' \
                else None
            if kind is not None:
                answer = _record(service, attempt, kind, '%s:%s' % (kind, attempt))
                closed.append(dict(attempt_id=attempt, kind=kind, state=answer.get('state')))
    return dict(reply, routing=dict(closed=closed)) if closed else reply


def _enrolled_clients(cwd):
    """The clients whose enrolment of this checkout keeps runtime state, from the canonical record. A checkout with
    no canonical record keeps the legacy entry path's client; an unreadable or conflicting record raises."""
    from runtime import startup
    budget = dict(paths=0, bytes=0, deadline=time.monotonic() + startup.OBSERVATION_SECONDS)
    acquired = startup._acquire_canonical_membership(cwd, authority_path=None, budget=budget)
    if acquired.status == 'outside':
        return ['codex']
    if acquired.status != 'observed':
        raise startup.RuntimeScopeError('Project authority is unavailable: ' + acquired.reason)
    data, observed = startup._canonical_plain(acquired.record.data), startup._canonical_plain(acquired.observation)
    selected = next((c for r in data['repositories'] if r['primary_root'] == observed['primary_root']
                     for c in r['checkouts'] if c['root'] == observed['root']), dict(clients=[]))
    return sorted(e['client'] for e in selected['clients'] if (e['configuration'] or {}).get('runtime'))


def _scopes_for(checkout):
    """The checkout's delegation scopes, one per enrolled client with runtime state, through the workflow entry
    path; [] when no client keeps runtime state. A client that cannot be resolved raises (fails closed)."""
    from runtime import routing_gates
    cwd, scopes = Path(checkout).resolve(), []
    for client in _enrolled_clients(cwd):
        try:
            scopes.append(routing_gates._resolve_scope(dict(client=client, cwd=str(cwd))))
        except LookupError:  # not enrolled for this client, or no runtime state: no native assignment can exist
            continue
    return scopes


def open_unrouted_assignments(checkout):
    """Activation precondition (review S1c): ids of the checkout's prepared, unobserved native worker and review
    assignments, which may hold an unmanaged launch permit. Read-only and bounded (MAX_OPEN); fails closed with
    `['scope_unavailable']` or `['state_unavailable']` when the checkout's state cannot be read."""
    try:
        scopes = _scopes_for(checkout)
    except Exception:  # noqa: BLE001 - e.g. RuntimeScopeError: unknown state never lets a project become managed
        return ['scope_unavailable']
    found = set()
    try:
        for scope in scopes:
            with state._database(scope) as db:
                rows = [] if db is None else db.execute(
                    "select id from assignments where scope_id=? and json_extract(data,'$.status')='prepared' and "
                    "json_extract(data,'$.observation') is null and json_extract(data,'$.role') in ('worker','review') "
                    "order by id limit ?", (state._scope_id(scope), MAX_OPEN)).fetchall()
            found.update(row[0] for row in rows)
    except Exception:  # noqa: BLE001 - sqlite3.Error, StateError
        return ['state_unavailable']
    return sorted(found)[:MAX_OPEN]


# -- continuations (spec F11) and Workstreams dispatch --------------------------------------------------------------
def _continuation_attempt(record, value):
    return '%s.c%d' % (record['id'], value['used'])


def _close_turns(service, record):
    """Close the holds of the child's earlier turns: a continuation is prepared only after its previous native turn
    ended (`_continuation_receipt`), so a dispatched turn is `complete` and an unsent one `not_dispatched`."""
    for attempt, hold in sorted(_holds(service).get('holds', {}).items()):
        if not hold['closed'] and (attempt == record['id'] or attempt.startswith(record['id'] + '.c')):
            kind = dict(dispatched='complete', admitted='not_dispatched').get(hold['state'])
            if kind is not None:
                _record(service, attempt, kind, 'turn:%s:%s' % (kind, attempt))


def continuation(scope, request, record, value, reply):
    """`prepare_continuation`: the same child and profile, with a fresh admission on the child's own route."""
    if record['data']['role'] not in ROLES or not managed(scope):
        return reply
    original, attempt = record['body']['request'], _continuation_attempt(record, value)
    identity = dict({key: original[key] for key in ('client', 'session_id', 'task_id')}, job_id=record['data']['job_id'])
    with closing(_command().managed_service(scope.repository_root, scope=scope)) as service:
        route = _route(scope, record, _hold(service, record['id'])[1])
        choice = routing_launch.choice(HOST, 'subscription', model=route['model'], effort=route['effort']) \
            if route else _choice(original['selection'])
        if _hold(service, attempt)[0] is None:
            _close_turns(service, record)
        answer = admit(service, scope, identity, attempt_id=record['id'], routing_attempt=attempt,
                       role=record['data']['role'], choice=choice)
    if answer.get('state') != 'admitted':
        return dict(reply, routing=dict(state=answer.get('state', 'blocked'), reason=answer.get('reason')))
    return dict(reply, routing=dict(state='admitted', attempt_id=attempt))


def _owned(record, request):
    if any(record['owner'][key] != request.get(key) for key in ('client', 'session_id', 'task_id')):
        raise state.StateError('Exact supported assignment is absent')


def consume(scope, request):
    """`consume_continuation`: routing `dispatch` for the prepared continuation before delegation consumes it."""
    if not managed(scope):
        return request
    record = _committed(scope, request['attempt_id'])
    _owned(record, request)
    value = delegation._continuation_intent(scope, record)
    if value is not None and value['token'] == request['token'] and value['state'] == 'prepared':
        dispatch(scope, request, _continuation_attempt(record, value), record=record)
    return request


def dispatch(scope, request, routing_attempt=None, *, record=None):
    """The last pre-send step of a managed Workstreams dispatch or continuation: routing `dispatch`. A refusal raises
    before delegation consumes anything; the token is dropped unread (advisory host). A dispatched attempt whose
    send delegation then refuses was never sent, and `settled` closes it at reconciliation."""
    if not managed(scope):
        return None
    record = record or _committed(scope, request['attempt_id'])
    _owned(record, request)
    routing_attempt = routing_attempt or record['id']
    with closing(_command().managed_service(scope.repository_root, scope=scope)) as service:
        hold, admission = _hold(service, routing_attempt)
        if admission is None or hold != 'admitted':
            raise state.StateError('Managed native launch needs a current routing admission (%s)'
                                   % ('routing_not_admitted' if admission is None else 'routing_' + str(hold)))
        facts, reason = routing_launch.admitted_check(admission, host=HOST, catalog=_catalog(scope, record),
                                                      settings_path=service.settings_path)
        sent = None if reason else _call(service, dict(v=1, op='dispatch', attempt_id=routing_attempt, request=facts))
        if sent is not None:
            sent.pop('authority', None)
            reason = None if sent.get('state') == 'dispatched' else sent.get('reason') or 'dispatch_refused'
        if reason is not None:
            raise state.StateError('Managed native launch refused by routing: ' + str(reason))
    return routing_attempt


# -- activation-gate self-check ---------------------------------------------------------------------------------
_SELF_CHECK_CHILD = False  # set only by SELF_CHECK_CHILD's entry code, never from the environment or a request
SELF_CHECK_MARK = 'delegation-routing-self-check:'
SELF_CHECK_CHILD = (
    'import json, sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from runtime import delegation_routing\n'
    'delegation_routing._SELF_CHECK_CHILD = True\n'
    'print(delegation_routing.SELF_CHECK_MARK + json.dumps(delegation_routing._self_check_in_child(sys.argv[2])))\n')
SELF_CHECK_ATTEMPT = 'a' * 32


def managed_self_check(workdir):
    """C.3 design 4.2 capability "Native delegation": `[]` when it holds, else fixed failure codes. In a child of this
    interpreter (it swaps module globals), on stores inside `workdir`: one routing admission bound to its workflow job
    and delegation attempt, and a catalog mismatch that closes the hold and dispatches nothing."""
    import signal
    import subprocess
    import sys
    root = routing_launch._sandbox(workdir)
    if root is None:
        return ['self_check_workdir_unsafe']
    for name in ('home', 'tmp'):
        (root / name).mkdir(mode=0o700)
    env = dict(PATH='/usr/bin:/bin', HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'), LANG='C.UTF-8')
    package = str(Path(__file__).resolve().parents[1])
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', SELF_CHECK_CHILD, package, str(root / 'check')],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                             cwd=root, start_new_session=True)
    try:
        output, _ = child.communicate(timeout=routing_launch.SELF_CHECK_BUDGET)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        child.communicate()
        return ['self_check_timeout']
    lines = [line for line in output.decode('utf-8', 'replace').splitlines() if line.startswith(SELF_CHECK_MARK)]
    try:
        codes = json.loads(lines[-1][len(SELF_CHECK_MARK):])
    except (IndexError, ValueError):
        return ['self_check_error']
    if type(codes) is not list or not all(type(code) is str for code in codes):
        return ['self_check_error']
    return codes if child.returncode == 0 or codes else ['self_check_error']


class _Reader:
    """A workflow double for one open planned job (routing_gates' reader contract): its view and a fixed etag."""

    def __init__(self, digest):
        self.digest = digest

    def view(self, locator):
        return dict(etag='self-check', job=dict(role='worker', revision='1' * 64, semantic_digest=self.digest, open=True),
                    dependencies=dict(accepted=True, digest='2' * 64, count=0),
                    ownership=dict(digest='3' * 64, conflict=False), resource=None)

    def etag(self, locator):
        return 'self-check'


def _self_check_in_child(workdir):
    """The check body. It refuses to run unless its child entry point set `_SELF_CHECK_CHILD`."""
    if _SELF_CHECK_CHILD is not True:
        return ['self_check_not_in_child']
    root = routing_launch._sandbox(workdir)
    if root is None:
        return ['self_check_workdir_unsafe']
    from types import SimpleNamespace
    from runtime import routing_observations, routing_settings
    checkout = root / 'checkout'
    checkout.mkdir(mode=0o700)
    identity = routing_launch.route_identity(HOST, model='self-check-model', effort='high')
    source = routing_observations.StaticSource(
        [dict(route='self-check', status='qualified', billing_class='included_subscription', pool='self-check',
              account='self-check', demand={'5h': 1}, binding_enforceable=True, **identity)],
        lambda pool, at: [dict(id='5h', limit=1000, used=0, length=18000, reset_at=at + 3600, rolling=False,
                               observed_at=at)])
    routing_settings.managed_project = lambda path, *args, **kwargs: Path(path).resolve() == checkout
    routing_observations.default_source = lambda *args, **kwargs: source
    job = dict(id='self-check', role='worker', scope_ref='self-check', claims=[], objective=None, context=None)
    service = _command().Service(root / 'routing' / 'settings.json', root / 'usage' / 'organizations.sqlite3',
                                 project=checkout, workflow=_Reader(routing.ensure_profile(job)['semantic_digest']))
    request, attempt, codes = dict(client='codex', session_id='self-check', task_id='self-check', job_id='self-check'), \
        SELF_CHECK_ATTEMPT, []
    try:
        if _call(service, dict(v=1, op='set', authority_required=True)).get('state') != 'updated':
            return ['self_check_store_unavailable']
        answer = admit(service, SimpleNamespace(repository_root=checkout), request, attempt_id=attempt,
                       routing_attempt=attempt, role='worker', job=job,
                       choice=routing_launch.choice(HOST, 'subscription', model='self-check-model', effort='high'))
        record = _hold(service, attempt)[1] if answer.get('state') == 'admitted' else None
        bound = (((record or {}).get('binding') or {}).get('job') or {}).get('locator') or {}
        if (bound.get('job_id'), bound.get('attempt_id')) != ('self-check', attempt):
            codes.append('workflow_binding_missing')
        else:
            _, reason = routing_launch.admitted_check(answer['admission'], host=HOST, catalog=[])
            if reason != routing_launch.CATALOG_MISMATCH:
                codes.append('catalog_mismatch_not_detected')
            _record(service, attempt, 'not_dispatched', 'catalog_mismatch:' + attempt)
            if _hold(service, attempt)[0] != 'released':
                codes.append('catalog_mismatch_dispatched')
    except Exception:  # noqa: BLE001 - any failure is a fixed code, never a traceback
        codes.append('self_check_error')
    finally:
        service.close()
    return codes
