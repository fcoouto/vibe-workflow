"""Native-only Smart Routing for Codex and Claude Code (F4.6): `native_prepare` and `native_complete`.

The coordinator's shell runs `scripts/routing.py native_prepare --project <checkout>` with `{v: 1, client,
assignment, job, profile?}`. The bridge takes the client's own catalog from the adapter (`runtime/adapters/
native_catalog.py`), never from the request, and asks the one routing service to admit, check and dispatch exactly
one subscription route on that client's native path (`codex-native` or `claude-native`). A `dispatched` answer is the
one-use permit: the coordinator then invokes its native Agent/subagent once with exactly the returned `model` and
`effort` (no effort when it is null) and reports the result with `native_complete {v: 1, attempt_id, outcome}`. Any
other answer starts nothing; a replay of the same attempt never gets a second permit, and an unknown send is recorded
`uncertain`, never retried.

Native launch is advisory: nothing blocks a send the coordinator makes anyway, so the permit is the contract. The
served model and effort are reported only when the host exposes them (`unknown` otherwise); a different served model
or effort is reported (`substituted`, `effort_changed`), never relabelled, and the real outcome settles. Missing
completion telemetry is not a quota reading or zero usage. A client whose enrolment keeps runtime task state (managed
Codex delegation, D5/D6) keeps its own stronger path (`managed_delegation_required`). Reuses `prepare_request`,
`admitted_check`, dispatch, `record` and settlement; no store, token or ledger of its own.
"""
import json
from pathlib import Path
import time

from runtime import routing, routing_launch, routing_observations as collector, routing_paths
from runtime.adapters import native_catalog

CLIENTS = native_catalog.HOSTS
OUTCOMES = ('complete', 'not_started', 'unknown')
OUTCOME_FIELDS = {'kind', 'model', 'effort'}


def _call(service, request):
    return service.handle(json.dumps(request).encode())


def _blocked(reason, **extra):
    return dict(state='blocked', reason=reason, **extra)


def _record(service, attempt, kind):
    answer = _call(service, dict(v=1, op='record', event=dict(event_id='%s:%s' % (kind, attempt), attempt_id=attempt,
                                                              kind=kind)))
    return {key: value for key, value in answer.items() if key != 'v'}  # the command adds its own `v`


def prepare(service, *, client, assignment, job, catalog, profile=None):
    """Admit, check and dispatch one native subscription route for `client` on `catalog` (the host's own route
    identities). Returns `{state: 'dispatched', attempt_id, model, effort, route, mode}` or the refusal."""
    host = CLIENTS.get(client)
    if host is None:
        return _blocked('invalid_request')
    harness = routing_launch.FIXED_HARNESSES[host][0]
    if not catalog or any(type(item) is not dict or item.get('harness') != harness for item in catalog):
        return _blocked('foreign_catalog')
    choice = assignment.get('choice') if type(assignment) is dict else None
    if type(choice) is dict and not any(all(item.get(key) == choice.get(key) for key in routing.ROUTE_IDENTITY)
                                        for item in catalog):
        return dict(state='pending', reason='explicit_choice_unavailable')  # exact: never substituted, nothing held
    try:
        request = routing_launch.prepare_request(assignment, job, profile=profile,
                                                 harnesses=routing_launch.host_harnesses(host))
    except routing_launch.Refused as error:
        return _blocked(error.reason)
    reason = routing_launch.preflight(_call(service, dict(v=1, op='status')))
    if reason is not None:
        return _blocked(reason)
    answer = _call(service, request)
    if answer.get('state') != 'admitted':
        return {key: answer[key] for key in ('state', 'reason', 'reasons') if key in answer}
    admission = answer['admission']
    attempt = admission['attempt_id']
    if answer.get('duplicate'):
        return _blocked('already_prepared', attempt_id=attempt)  # the permit was granted once, on the first call
    facts, reason = routing_launch.admitted_check(admission, host=host, catalog=catalog,
                                                  settings_path=service.settings_path)
    if reason is None:
        sent = _call(service, dict(v=1, op='dispatch', attempt_id=attempt, request=facts))
        sent.pop('authority', None)  # advisory host: the token is never kept or returned
        if sent.get('state') == 'already_dispatched':
            return _blocked('already_dispatched', attempt_id=attempt)
        if sent.get('state') != 'dispatched':
            reason = sent.get('reason') if routing._text(sent.get('reason')) else 'dispatch_refused'
    if reason is not None:
        _record(service, attempt, 'not_dispatched')
        return _blocked(reason, attempt_id=attempt)
    return dict(state='dispatched', attempt_id=attempt, client=client, model=facts['model'],
                effort=None if facts['variant'] == 'none' else facts['variant'], route=admission['route'],
                mode=admission.get('mode'))


def _admitted(service, attempt_id):
    """(admission record, client, `{model, effort}` admitted, hold state) of the attempt, from the ledger, the path
    rules and the client's current catalog (the identity digest decodes against it); unknown parts are None."""
    ledger = service._ledger(False)
    data = ledger.snapshot() if ledger is not None else {}
    record = next((item for item in data.get('admissions', []) if item.get('attempt_id') == attempt_id), None)
    hold = (data.get('holds', {}).get(attempt_id) or {}).get('state')
    if record is None:
        return None, None, None, hold
    rule = next((item for item in routing_paths.load_rules()['paths']
                 if item['path'] == routing_paths.path_of(record.get('route'))), None)
    client = next((name for name, host in CLIENTS.items() if rule and rule.get('host') == host), None)
    entries = native_catalog.catalog(service.settings_path, client)[0] if client else None
    for entry in entries or []:
        if routing.route_identity(*(entry[field] for field in routing.ROUTE_IDENTITY)) == record.get('route_identity'):
            return record, client, dict(model=entry['model'],
                                        effort=None if entry['effort'] == 'none' else entry['effort']), hold
    return record, client, None, hold


def complete(service, *, attempt_id, outcome):
    """Settle the native result through the existing events. Reports what the host said it served.

    Only an attempt this bridge admitted on a native path is settled here (`not_a_native_attempt` otherwise). `complete`
    records `complete`, which releases the hold's window claims; a native launch reports no reading, so while the hold
    is still `dispatched` the pool's current readings are first marked used (`mark_consumed`, once per attempt): they
    stop counting for every alias of the pool until a reading observed later arrives. A replay, a rejected transition
    or a retry never moves that mark. If it cannot be written nothing is recorded and the hold stays.
    `not_started` and `unknown` record `uncertain`: once the permit was issued routing cannot verify that an advisory
    launch never ran, and the ledger closes a dispatched hold only through settlement or recovery, so the hold stays
    counted until the operator's `reconcile_subscription`; it never gets another permit."""
    if not (routing._text(attempt_id) and type(outcome) is dict and outcome.get('kind') in OUTCOMES
            and set(outcome) <= OUTCOME_FIELDS and all(routing._optional(outcome.get(key), routing._text)
                                                      for key in ('model', 'effort'))):
        return _blocked('invalid_request')
    record, client, admitted, hold = _admitted(service, attempt_id)
    if record is None or client is None:
        return _blocked('not_a_native_attempt')  # never admitted, or another launcher's: nothing to settle here
    kind = 'complete' if outcome['kind'] == 'complete' else 'uncertain'
    if kind == 'complete' and hold == 'dispatched' and record.get('billing_class') == 'included_subscription':
        try:
            collector.mark_consumed(service.settings_path, record['pool'], int(time.time()), attempt_id)
        except (OSError, ValueError):
            return _blocked('observation_store_unavailable')  # nothing recorded: the hold keeps its capacity
    answer = _record(service, attempt_id, kind)
    if answer.get('state') != 'recorded':
        return answer
    if kind == 'uncertain':
        answer = dict(answer, disposition='uncertain', recovery='reconcile_subscription')
    served = dict(model=outcome.get('model', 'unknown'), effort=outcome.get('effort', 'unknown'))
    if 'model' in outcome:
        expected = set()
        if admitted is not None:
            expected = {value for value in (admitted['model'], native_catalog.resolved_of(
                service.settings_path, client, admitted['model'])) if value}
        served['substituted'] = outcome['model'] not in expected if expected else 'unknown'
    if 'effort' in outcome:
        served['effort_changed'] = outcome['effort'] != admitted['effort'] if admitted is not None else 'unknown'
    return dict(answer, served=served)


def _runtime_delegation(project, client):
    """`managed_delegation_required` when `client` keeps runtime task state for this checkout (its own delegation
    path, with its D5/D6 bindings, applies), else None. Read from the canonical enrolment's runtime configuration,
    the source `open_unrouted_assignments` uses; with no canonical record only a resolving legacy codex scope counts,
    as for `enrolled_hosts`. An unreadable enrolment fails closed (`enrolment_unavailable`)."""
    from runtime import task_profile
    try:
        from runtime import delegation_routing, routing_gates  # delegation_routing reads the template at import
        entries = delegation_routing._enrolment(Path(project).resolve())
    except task_profile.TemplateUnavailable:
        return 'profile_unavailable'
    except Exception:  # noqa: BLE001 - RuntimeScopeError, OSError: unknown runtime state never bypasses it
        return 'enrolment_unavailable'
    if entries is None:
        if client != 'codex':
            return None
        try:
            routing_gates._resolve_scope(dict(client='codex', cwd=str(project)))
        except Exception:  # noqa: BLE001 - LookupError, RuntimeScopeError: no confirmed legacy codex scope
            return None
        return 'managed_delegation_required'
    runtime = any(entry.get('client') == client and (entry.get('configuration') or {}).get('runtime')
                  for entry in entries)
    return 'managed_delegation_required' if runtime else None


def command_prepare(service, body):
    """`native_prepare` (one-shot only): the request names no catalog or quota; the adapter supplies the catalog."""
    if not service.one_shot:
        return _blocked('native_op_not_served')
    client = body.get('client')
    if client not in CLIENTS or type(body.get('assignment')) is not dict:
        return _blocked('invalid_request')
    if service.project is None:
        return _blocked(routing_launch.PROJECT_REQUIRED)
    if not routing_launch.managed(service.project, service.settings_path):
        return dict(state='unmanaged', reason=routing_launch.PROJECT_NOT_MANAGED)
    reason = _runtime_delegation(service.project, client)
    if reason is not None:
        return _blocked(reason)
    entries, reason = native_catalog.catalog(service.settings_path, client)
    if reason is not None:
        return _blocked(reason)
    return prepare(service, client=client, assignment=body['assignment'], job=body['job'], catalog=entries,
                   profile=body.get('profile'))


def command_complete(service, body):
    """`native_complete` (one-shot only)."""
    if not service.one_shot:
        return _blocked('native_op_not_served')
    return complete(service, attempt_id=body['attempt_id'], outcome=body['outcome'])
