"""Optional Langfuse upload of one task's Codex usage after its completion verification (D9 integration).

``after_completion`` runs once after an ordinary ``evidence verify`` returns. It acts only when the verification is
an ordinary verified completion, this checkout's own state folder has a Project record with collection on that lists
the repository, and the checkout connection is enabled. Otherwise it returns after the first unmet condition, with
no further reads, credentials or network. When it acts, it reads the task's Codex session files (O3), reconciles
them (O4) and plans and delivers at most one Langfuse record (O5).

Upload state lives in one private file per task and destination under ``<state_dir>/observability/exports/``. The
file is locked for the whole run; a record is saved as ``pending`` before its POST and settled after it, so a crash
in between leaves ``pending``, which the next run turns into ``uncertain`` and never resends.

The hook never changes the verification output or exit code: it catches ``Exception`` and records ``hook_failed``
when it can. Cancellation, ``SystemExit`` and other ``BaseException`` propagate.
"""

from datetime import datetime, timezone
import os
import re
import time


O3_SECONDS = 10
_IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,128}")
# The session-file reader accepts letters, digits and `-` only; any other ID is unresolved, never read.
_THREAD = re.compile(r"[A-Za-z0-9-]{1,128}")


def after_completion(scope, request, report):
    """Run the optional upload once; never raises ``Exception`` and never changes ``report``."""
    context = {}
    try:
        _run(scope, request, report, context)
    except Exception as error:
        _failed(scope, context, error)


def _ordinary(request, report):
    return (isinstance(request, dict) and request.get("purpose") == "completion" and isinstance(report, dict)
            and report.get("ok") is True and report.get("verified") is True
            and report.get("admission_mode") == "ordinary")


def _project_present(scope):
    # One local name check keeps projects without observability settings free of further imports and reads.
    project_id, runtime = getattr(scope, "project_id", None), getattr(scope, "runtime", None)
    if type(project_id) is not str or not _IDENTIFIER.fullmatch(project_id) or runtime is None:
        return False
    return os.path.lexists(runtime.state_dir / "observability" / ("project-" + project_id + ".json"))


def _run(scope, request, report, context):
    if not _ordinary(request, report) or not _project_present(scope):
        return
    from runtime import observability_settings as settings
    with settings.held_scope(scope):
        _upload(settings, scope, report, context)


def _upload(settings, scope, report, context):
    connection = settings.export_destination(scope)
    if connection is None:
        return
    task = report["task"]
    task_ref = {key: task[key] for key in settings.TASK_REF}
    key = settings.destination_key(connection["destination"], connection["langfuse_project_id"])
    context.update(task_ref=task_ref, destination_key=key)
    threads = task_threads(scope, task)
    if threads is None:
        return _record(scope, task_ref, key, "participants_unresolved")
    created = (task.get("lifecycle") or {}).get("created_at")
    if type(created) is not int:
        return _record(scope, task_ref, key, "totals_unknown")
    from runtime import observability_accounting as accounting, observability_export as export
    from runtime.adapters import codex_usage
    # The task writer stores `lifecycle.created_at` as whole Unix seconds (`state._owner_time`).
    started_at = datetime.fromtimestamp(created, timezone.utc)
    cutoff_at = datetime.now(timezone.utc)
    observations = codex_usage.acquire_task_observations(threads, started_at=started_at, cutoff_at=cutoff_at,
        deadline=time.monotonic() + O3_SECONDS, qualified_versions=codex_usage.QUALIFIED_VERSIONS)
    reconciled = accounting.reconcile_task(observations, verification=report)
    with settings.export_store(scope, task_ref, key, write=True, create=True) as owner:
        envelope = owner.load()
        owner.check()
        state = export.recover_pending(envelope["export_state"] if envelope else None)
        plan = export.plan_export(reconciled, export_state=state)
        if plan is None or plan["action"] != "send":
            _save(owner, envelope, task_ref, key, state, "nothing_to_send" if plan is None else plan["reason"])
            return
        link = _connection(connection)
        if link is None:
            _save(owner, envelope, task_ref, key, state, "credentials_unavailable")
            return
        owner.check()
        envelope = _save(owner, envelope, task_ref, key, plan["state"], None)
        outcome = export.deliver(plan, connection=link)["outcome"]
        settled = export.record_outcome(plan["state"], plan["record"]["export_id"], outcome)
        _save(owner, envelope, task_ref, key, settled, outcome)


def _connection(connection):
    from runtime import langfuse, observability_credentials as credentials
    try:
        pair = None
        if connection["credential_source"] == "os-store":
            pair = credentials.load(connection["destination"], connection["langfuse_project_id"])
        return langfuse._Connection(connection["destination"], connection["langfuse_project_id"],
                                    langfuse.REQUEST_SECONDS, pair)
    except (credentials.CredentialError, langfuse.LangfuseError):
        return None


def _save(owner, envelope, task_ref, key, state, code):
    """Compare-and-set on the envelope revision; `code` None keeps the previous last result."""
    last = envelope["last_result"] if envelope else None
    if code is not None:
        last = dict(at=int(time.time()), code=code)
    updated = dict(version=1, revision=(envelope["revision"] if envelope else 0) + 1, binding=owner.binding,
                   task_ref=task_ref, destination_key=key, export_state=state, last_result=last)
    owner.save(updated)
    return updated


def _record(scope, task_ref, key, code):
    """Save only a last result, keeping any stored export state unchanged."""
    from runtime import observability_settings as settings
    with settings.export_store(scope, task_ref, key, write=True, create=True) as owner:
        envelope = owner.load()
        owner.check()
        _save(owner, envelope, task_ref, key, envelope["export_state"] if envelope else None, code)


def _failed(scope, context, error):
    if "task_ref" not in context:
        return
    try:
        from runtime.observability_settings import SettingsError
        if isinstance(error, SettingsError) and str(error) == "observability_operation_busy":
            # Another run holds the upload lock and owns this file; its last result must stand.
            return
        _record(scope, context["task_ref"], context["destination_key"], "hook_failed")
    except Exception:
        # The failure stays local; the verification result is already final.
        pass


def task_threads(scope, task):
    """Return the task's model threads as ``[{thread_id, role}]``, or None when any participant is unresolved.

    The coordinator is the task's ``session_id``. Each native D6 worker or reviewer assignment of the task adds the
    single ``worker_id`` its observation history records. Each D5 review attempt adds the ``reviewer_id`` of its
    collected receipt, or else its linked assignment's worker. Check runs and coordinator operations add none.
    """
    from runtime import delegation, evidence, state, task_profile
    threads = {}

    def add(thread_id, role):
        if type(thread_id) is not str or not _THREAD.fullmatch(thread_id):
            return False
        threads.setdefault(thread_id, role)
        return True

    if not add(task.get("session_id"), "coordinator"):
        return None
    scope_id = state._scope_id(scope)
    with state._database(scope) as db:
        records = [] if db is None else delegation._ledger(scope, db, task=task)
    owner = dict(scope_id=scope_id, **{key: task[key] for key in ("client", "session_id", "task_id")})
    owned = {record["id"]: record for record in records
             if all(record["owner"].get(key) == value for key, value in owner.items())}
    for record in owned.values():
        role = record["data"]["role"]
        if task_profile.native(role) and not add(_worker(scope, record), role):
            return None
    for attempt in (task.get("evidence") or {}).get("attempts", []):
        if attempt.get("kind") != "review":
            continue
        linked = owned.get(attempt.get("id"))
        if "collected" in attempt:
            thread = evidence._read(scope, attempt["collected"])["receipt"].get("reviewer_id")
        else:
            thread = _worker(scope, linked) if linked is not None else None
        if not add(thread, linked["data"]["role"] if linked is not None else "reviewer"):
            return None
    return [dict(thread_id=thread_id, role=role) for thread_id, role in threads.items()]


def _worker(scope, record):
    from runtime import delegation, evidence
    reference = record["data"].get("observation")
    if reference is None:
        return None
    workers = set()
    for _, value in delegation._worker_history(scope, reference):
        receipt = value.get("receipt")
        if receipt is not None:
            if not isinstance(receipt, dict):
                return None
            workers.add(receipt.get("worker_id"))
        if value.get("conflict") is not None:
            # D6 keeps the earlier receipt and stores a contradicting one only behind `conflict`.
            conflicting = evidence._read(scope, value["conflict"]).get("receipt")
            if not isinstance(conflicting, dict):
                return None
            workers.add(conflicting.get("worker_id"))
    return workers.pop() if len(workers) == 1 else None


def _summary(key, envelope):
    state = envelope["export_state"]
    records = [] if state is None else [
        {name: record[name] for name in ("export_id", "sequence", "kind", "outcome")} for record in state["records"]]
    return dict(destination_key=key, revision=envelope["revision"], records=records,
                exported_totals=None if state is None else dict(state["exported_totals"]),
                last_result=envelope["last_result"])


def _task_request(request, extra=()):
    from runtime import observability_settings as settings
    fields = set(settings.TASK_REF) | set(extra)
    if not isinstance(request, dict) or set(request) != fields or not all(
            type(request[name]) is str and 0 < len(request[name]) <= 1024 for name in settings.TASK_REF):
        raise settings.SettingsError("invalid_observability_request")
    return {name: request[name] for name in settings.TASK_REF}


def export_status(scope, request):
    """Read-only upload summary for the checkout's configured destination; no credentials, network or writes."""
    from runtime import observability_export as export, observability_settings as settings
    task_ref = _task_request(request)
    checkout = settings.status(scope)
    destinations = []
    if checkout["configured"]:
        key = settings.destination_key(checkout["destination"], checkout["langfuse_project_id"])
        with settings.export_store(scope, task_ref, key, recovery=True) as owner:
            envelope = None
            if owner is not None:
                envelope = owner.load()
                owner.check()
        if envelope is not None:
            export._state(envelope["export_state"])
            destinations.append(_summary(key, envelope))
    return dict(ok=True, task_ref=task_ref, destinations=destinations)


def resolve_export(scope, request):
    """Apply the user's finding for one uncertain record: ``landed`` counts it, ``missing`` allows a new upload."""
    from runtime import observability_export as export, observability_settings as settings
    task_ref = _task_request(request, ("expected_revision", "destination_key", "export_id", "resolution"))
    key, export_id, resolution = request["destination_key"], request["export_id"], request["resolution"]
    if (type(request["expected_revision"]) is not int or request["expected_revision"] < 1
            or type(key) is not str or not settings.DESTINATION_KEY.fullmatch(key)
            or type(export_id) is not str or not re.fullmatch(r"[0-9a-f]{16}", export_id)
            or resolution not in ("landed", "missing")):
        raise settings.SettingsError("invalid_observability_request")
    with settings.export_store(scope, task_ref, key, write=True, recovery=True) as owner:
        if owner is None:
            raise settings.SettingsError("observability_export_not_found")
        envelope = owner.load()
        owner.check()
        if envelope is None:
            raise settings.SettingsError("observability_export_not_found")
        if request["expected_revision"] != envelope["revision"]:
            raise settings.SettingsError("observability_revision_conflict")
        # The lock excludes a running upload, so a pending record here was interrupted.
        try:
            state = export.resolve_uncertain(export.recover_pending(envelope["export_state"]), export_id, resolution)
        except ValueError:
            raise settings.SettingsError("observability_export_not_resolvable") from None
        envelope = _save(owner, envelope, task_ref, key, state, None)
    return dict(ok=True, task_ref=task_ref, destinations=[_summary(key, envelope)])
