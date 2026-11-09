"""Private, bounded SQLite task controls and atomic deterministic hook decisions."""

from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import stat
import time
from types import MappingProxyType

from runtime.engine import HookResult, evaluate, guarded_mutation
from runtime.startup import (StartupError, AcceptedScope, RuntimeSettings, PROJECT_FILE, json_object, _canonical_plain, _canonical_freeze,
    _canonical_path, _canonical_binding, _canonical_ref, _canonical_hash, _budgeted_source_read, _entry_budget_check)


SCHEMA_VERSION = 2
APPLICATION_ID = 0x56494245
PACKAGE = Path(__file__).resolve().parents[1]
LIMITATIONS = ["Supported apply_patch guard only; shell and external edits remain possible",
               "Hooks alone do not establish client execution, filesystem locality or current completion evidence"]
_CAPTURE_SQL_SCOPE = ContextVar('capture_sql_scope', default=None)


def _capture_selection(operation):
    if operation == 'schema':
        bound=2*len(CANONICAL_TABLE_SQL)+len(CANONICAL_INDEX_SQL)+1
        return ("select type,name,sql from sqlite_master limit ?", (bound+1,), bound,
            "select count(*),coalesce(sum(coalesce(length(cast(type as blob)),0)+coalesce(length(cast(name as blob)),0)+coalesce(length(cast(sql as blob)),0)),0),"
            "coalesce(sum(typeof(type)!='text' or typeof(name)!='text' or (typeof(sql)!='text' and not(type='index' and typeof(sql)='null'))),0) "
            "from (select type,name,sql from sqlite_master limit ?)")
    if operation == 'database_list':
        return ("select seq,name,file from pragma_database_list order by seq limit 2", (), 1,
            "select count(*),coalesce(sum(coalesce(length(cast(seq as text)),0)+coalesce(length(cast(name as blob)),0)+coalesce(length(cast(file as blob)),0)),0),"
            "coalesce(sum(typeof(seq)!='integer' or typeof(name)!='text' or typeof(file)!='text'),0) "
            "from (select seq,name,file from pragma_database_list order by seq limit 2)")
    raise StateError('Unsupported capture measurement selection')


def _capture_measure(db,operation):
    sql,parameters,bound,measure=_capture_selection(operation)
    row=db.execute(measure,parameters).fetchone()
    if (type(row) is not tuple or len(row)!=3 or any(type(v) is not int or v<0 for v in row)
            or row[0]>bound or row[2]):
        raise StateError('Capture measurement overflow or invalid scalar')
    count,material,_=row
    # Includes selected strings plus schema split/join scratch; accounted material, not RSS.
    return sql,parameters,4*material+64*count,64*material+1024*count


def _capture_registered(resources,db,*,prepaid=False):
    resources._check_native_connection(db)
    if prepaid:resources.check_integrity()
    else:resources.check()
    record=resources._connections.get(id(db))
    if (type(db) is not sqlite3.Connection or type(record) is not tuple or len(record)!=2
            or record[0] is not db or record[1].owner is not resources or record[1].amount!=1024
            or resources._leases.get(id(record[1])) is not record[1]):
        raise StateError('Capture lacks its actual registered connection')


@contextmanager
def _capture_precharge(db,operation):
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is None:
        yield None
        return
    resources,escrow=scope
    if operation not in {'schema','database_list','application_id','user_version','journal_mode',
            'trusted_schema','synchronous','begin'}:
        raise StateError('Unknown fixed capture operation')
    _capture_registered(resources,db,prepaid=escrow is not None)
    if escrow is not None:
        record=resources._exit_scopes.get(id(escrow))
        if (type(escrow) is not dict or len(escrow)!=3 or any(type(k) is not str or k not in ('lease','caps','remaining') for k in escrow)
                or type(record) is not tuple or len(record)!=4):
            raise StateError('Invalid finite prepaid validation record')
        caps=escrow['caps'];remaining=escrow['remaining'];allowed=('database_list','schema','application_id','user_version','journal_mode')
        if (type(caps) is not dict or len(caps)!=2 or any(type(k) is not str or k not in ('database_list','schema') for k in caps)
                or type(record[2]) is not tuple or len(record[2])!=2
                or any(type(t) is not tuple or len(t)!=2 or any(type(v) is not int or not 0<=v<=bound for v,bound in zip(t,(resources.work.limit,resources.live.limit))) for t in (caps['database_list'],caps['schema'],record[2][0],record[2][1]))
                or any(type(s) is not set or len(s)>5 or any(type(v) is not str or v not in allowed for v in s) for s in (remaining,record[3]))):
            raise StateError('Invalid finite prepaid validation caps')
        if (record[0] is not escrow or record[1] is not escrow['lease']
                or type(record[1]) is not type(resources._connections[id(db)][1]) or record[1].owner is not resources
                or record[1].amount!=4096+2048*5+caps['database_list'][1]+caps['schema'][1]
                or resources._leases.get(id(record[1])) is not record[1]
                or (caps['database_list'],caps['schema'])!=record[2] or remaining!=record[3]
                or operation not in escrow['remaining']):
            raise StateError('Prepaid validation operation repeated or unavailable')
        escrow['remaining'].remove(operation)
        record[3].remove(operation)
        if operation in ('schema','database_list'):
            sql,parameters,work,live=_capture_measure(db,operation)
            allowed=escrow['caps'][operation]
            if work>allowed[0] or live>allowed[1]:
                raise StateError('Exit validation material changed beyond prepaid bound')
            yield sql,parameters,work,live
        else:yield None
        return
    control=resources.reserve(requested=512,live=2048)
    payload=None
    try:
        if operation in ('schema','database_list'):
            sql,parameters,work,live=_capture_measure(db,operation)
            payload=resources.reserve(requested=work,live=live)
            yield sql,parameters,work,live
        else:yield None
    except BaseException as error:
        resources.retain_failure(control,error)
        if payload is not None:resources.retain_failure(payload,error)
        raise
    else:
        if payload is not None:payload.discard()
        control.discard()


def _capture_exit_reservation(db,resources):
    planning=resources.reserve(live=4096)
    try:
        caps={}
        for operation in ('database_list','schema'):
            with _capture_precharge(db,operation) as selection:
                caps[operation]=selection[2:]
        operations={'database_list','schema','application_id','user_version','journal_mode'}
        lease=resources.reserve(requested=512*len(operations)+sum(v[0] for v in caps.values()),
            live=4096+2048*len(operations)+sum(v[1] for v in caps.values()))
        result=dict(lease=lease,caps=caps,remaining=operations)
        resources._exit_scopes[id(result)]=(result,lease,(caps['database_list'],caps['schema']),set(operations))
    except BaseException as error:
        resources.retain_failure(planning,error)
        raise
    else:
        planning.discard()
        return result


def _capture_scalar(db,operation):
    if operation not in {'application_id','user_version','journal_mode'}:
        raise StateError('Unsupported capture scalar')
    with _capture_precharge(db,operation):
        return db.execute('pragma '+operation).fetchone()[0]


def _capture_json(raw,loader,*,comparison_budget=None):
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is None:
        if comparison_budget is not None:
            from runtime import resource_accounting as accounting
            accounting.require(isinstance(comparison_budget,accounting.CaptureBudget))
            material=accounting._json_comparison_bound(raw)
            comparison_budget.take(bytes=material)
        return loader(raw)
    return scope[0].current_acquisition().decode(raw,loader,comparison_budget=comparison_budget)


def _resource_origin(error):
    scope=_CAPTURE_SQL_SCOPE.get()
    return scope is not None and getattr(error,'_vcw_resource_origin',None) is scope[0]


def _capture_point_selection(operation):
    if operation=='metadata':
        return ("select name,digest,owner_key,class,created_at,closed_at,status,bytes,data from evidence_metadata where name=?",
            "select count(*),coalesce(sum(coalesce(length(cast(name as blob)),0)+coalesce(length(cast(digest as blob)),0)+"
            "coalesce(length(cast(owner_key as blob)),0)+coalesce(length(cast(class as blob)),0)+coalesce(length(cast(created_at as blob)),0)+"
            "coalesce(length(cast(closed_at as blob)),0)+coalesce(length(cast(status as blob)),0)+coalesce(length(cast(bytes as blob)),0)+"
            "coalesce(length(cast(data as blob)),0)),0) from evidence_metadata where name=?")
    if operation=='retired':
        return ("select data,assignment_id from retired_identities where kind=? and identity_digest=?",
            "select count(*),coalesce(sum(coalesce(length(cast(data as blob)),0)+coalesce(length(cast(assignment_id as blob)),0)),0) "
            "from retired_identities where kind=? and identity_digest=?")
    if operation=='retired_assignment':
        return ("select identity_digest,data,assignment_id from retired_identities where kind='assignment' and assignment_id=?",
            "select count(*),coalesce(sum(coalesce(length(cast(identity_digest as blob)),0)+coalesce(length(cast(data as blob)),0)+"
            "coalesce(length(cast(assignment_id as blob)),0)),0) from retired_identities where kind='assignment' and assignment_id=?")
    if operation=='task':
        return ("select data from tasks where scope_id=? and client=? and session_id=? and task_id=?",
            "select count(*),coalesce(sum(length(cast(data as blob))),0),coalesce(sum(typeof(data) not in ('text','blob')),0) "
            "from tasks where scope_id=? and client=? and session_id=? and task_id=?")
    if operation=='binding':
        return ("select task_id from bindings where scope_id=? and client=? and session_id=?",
            "select count(*),coalesce(sum(length(cast(task_id as blob))),0),coalesce(sum(typeof(task_id)!='text'),0) "
            "from bindings where scope_id=? and client=? and session_id=?")
    if operation=='checkout':
        return ("select * from checkouts where scope_id=?",
            "select count(*),coalesce(sum(length(cast(scope_id as blob))+length(cast(project_id as blob))+"
            "length(cast(repository_root as blob))+length(cast(git_common_dir as blob))+coalesce(length(cast(generation as blob)),0)+"
            "length(cast(physical_identity as blob))),0),coalesce(sum(typeof(scope_id)!='text' or typeof(project_id)!='text' "
            "or typeof(repository_root)!='text' or typeof(git_common_dir)!='text' "
            "or typeof(physical_identity) not in ('text','blob')),0) from checkouts where scope_id=?")
    if operation=='task_database':
        return ("pragma database_list",
            "select count(*),coalesce(sum(length(cast(seq as text))+length(cast(name as blob))+length(cast(file as blob))),0),"
            "coalesce(sum(typeof(seq)!='integer' or typeof(name)!='text' or typeof(file)!='text'),0) "
            "from (select seq,name,file from pragma_database_list limit 1)")
    raise StateError('Unsupported fixed captured point read')


@contextmanager
def _capture_point(db,operation,parameters):
    sql,measurement=_capture_point_selection(operation);scope=_CAPTURE_SQL_SCOPE.get()
    if scope is None:
        yield db.execute(sql,parameters).fetchone()
        return
    resources,escrow=scope
    if escrow is not None:raise StateError('Point read cannot borrow exit-validation allowance')
    _capture_registered(resources,db);owner=resources.current_acquisition()
    control=owner.reserve(requested=512,live=2048);payload=None;row=None;measured=None
    try:
        measured=db.execute(measurement,parameters).fetchone()
        if (type(measured) is not tuple or len(measured) not in (2,3)
                or any(type(v) is not int or v<0 for v in measured) or measured[0]>1
                or len(measured)==3 and measured[2]):
            raise StateError('Captured point measurement overflow')
        material=4*measured[1]+128*measured[0]
        payload=owner.reserve(requested=material,live=4*material+512*measured[0])
        row=db.execute(sql,parameters).fetchone()
        yield row
    except BaseException as error:owner.fail(error);raise
    else:
        row=measured=None;owner.release(payload);owner.release(control)
from runtime.task_statuses import ATTENTION_STATES, RETIRED_ATTENTION_STATES, RETIRED_STATES, STATES
_HISTORICAL_STATES = {"planned", "active", "awaiting_user", "blocked", "verifying", "ready", "cancelled"}
TABLE_SQL = {
    "checkouts": """CREATE TABLE checkouts(scope_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        repository_root TEXT NOT NULL, git_common_dir TEXT NOT NULL, generation INTEGER NOT NULL)""",
    "tasks": """CREATE TABLE tasks(scope_id TEXT, client TEXT, session_id TEXT, task_id TEXT, data TEXT NOT NULL,
        PRIMARY KEY(scope_id,client,session_id,task_id))""",
    "bindings": """CREATE TABLE bindings(scope_id TEXT, client TEXT, session_id TEXT, task_id TEXT NOT NULL,
        PRIMARY KEY(scope_id,client,session_id))""",
    "turns": """CREATE TABLE turns(scope_id TEXT, client TEXT, session_id TEXT, turn_id TEXT, task_id TEXT,
        continuation_consumed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_id,client,session_id,turn_id))""",
    "receipts": """CREATE TABLE receipts(scope_id TEXT, client TEXT, session_id TEXT, turn_id TEXT, tool_call_id TEXT,
        kind TEXT, fingerprint TEXT NOT NULL, conflicted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_id,client,session_id,turn_id,tool_call_id,kind))""",
    "assignments": """CREATE TABLE assignments(id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, client TEXT NOT NULL,
        session_id TEXT NOT NULL, task_id TEXT NOT NULL, dispatch_id TEXT NOT NULL, data TEXT NOT NULL,
        UNIQUE(scope_id,client,session_id,task_id,dispatch_id))""",
}
CANONICAL_SCHEMA_VERSION = 3
CANONICAL_TABLE_SQL = TABLE_SQL | {
    "checkouts": TABLE_SQL["checkouts"][:-1] + ", physical_identity TEXT NOT NULL)",
    "evidence_metadata": """CREATE TABLE evidence_metadata(name TEXT PRIMARY KEY, digest TEXT NOT NULL,
        owner_key TEXT NOT NULL, class TEXT NOT NULL, created_at INTEGER NOT NULL, closed_at INTEGER,
        status TEXT NOT NULL, bytes INTEGER NOT NULL, data TEXT NOT NULL)""",
    "retired_identities": """CREATE TABLE retired_identities(kind TEXT NOT NULL, identity_digest TEXT NOT NULL,
        data TEXT NOT NULL, assignment_id TEXT, PRIMARY KEY(kind,identity_digest))""",
}
CANONICAL_INDEX_SQL = {
    "evidence_lifecycle": "CREATE INDEX evidence_lifecycle ON evidence_metadata(owner_key,class,status,closed_at,created_at,name)",
    "assignment_owner": "CREATE INDEX assignment_owner ON assignments(scope_id,client,session_id,task_id)",
    "retired_assignment_id": "CREATE UNIQUE INDEX retired_assignment_id ON retired_identities(assignment_id) WHERE kind='assignment'",
}
TABLES = set(TABLE_SQL)


class StateError(StartupError):
    """Sanitized task/state diagnostic, without private record contents."""


def _runtime_sources():
    return sorted([*(PACKAGE / "runtime").glob("*.py"), *(PACKAGE / "runtime/adapters").glob("*.py"), PACKAGE / "scripts/workflow.py", PACKAGE / "scripts/workstreams_family.py", PACKAGE / "scripts/workstreams_recovery.py", PACKAGE / "scripts/onboard_coordinator.py", PACKAGE / "scripts/delivery_coordinator.py", PACKAGE / "scripts/read_instructions.py", PACKAGE / "scripts/drain_instructions.js", PACKAGE / "scripts/worker_visibility.py"])


def runtime_fingerprint(*, budget=None):
    """Pin installed runtime sources, dispatcher and adapter contents, by relative path."""
    digest = hashlib.sha256()
    for path in _runtime_sources():
        digest.update(str(path.relative_to(PACKAGE)).encode() + b"\0" + _budgeted_source_read(path, budget) + b"\0")
    return digest.hexdigest()


def _validate_pins(pins, *, version=None):
    _fields(pins, ("schema_version", "policy_digest", "runtime_digest"), ("accepted_scope_digest",))
    actual = pins["schema_version"]
    if type(actual) is not int or actual not in (1, 2, 3) or version is not None and actual != version:
        raise StateError("Unsupported stored task pin schema")
    _fields(pins, ("schema_version", "policy_digest", "runtime_digest", "accepted_scope_digest") if actual == 3
            else ("schema_version", "policy_digest", "runtime_digest"))
    for key in ("policy_digest", "runtime_digest"):
        _text(pins[key], "stored " + key)
    if actual == 3:
        for key in ("policy_digest", "runtime_digest", "accepted_scope_digest"):
            if not isinstance(pins[key], str) or not re.fullmatch(r"[0-9a-f]{64}", pins[key]):
                raise StateError("Invalid canonical task pins")
    return actual


def _accepted_identity(value):
    try:
        identity = json_object(json.dumps(_canonical_plain(value), ensure_ascii=True))
        _fields(identity, ("version", "project_id", "repository_id", "checkout_id", "project_root", "project_binding",
            "primary_root", "repository_root", "git_dir", "git_common_dir", "bindings", "client", "membership", "configuration", "shared_policy"), ("delivery_policy",))
        if "delivery_policy" in identity:
            from runtime.delivery import validate_policy
            validate_policy(identity["delivery_policy"])
        if type(identity["version"]) is not int or identity["version"] != 1:
            raise StateError("Unsupported accepted identity")
        for key in ("project_id", "repository_id", "checkout_id", "client"):
            _text(identity[key], key, 128)
        if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", identity["project_id"])
                or identity["repository_id"] == identity["checkout_id"]):
            raise StateError("Invalid canonical identities")
        for key in ("project_root", "primary_root", "repository_root", "git_dir", "git_common_dir"):
            _canonical_path(identity[key])
        _canonical_binding(identity["project_binding"])
        _fields(identity["bindings"], ("root", "primary_root", "git_dir", "git_common_dir"))
        locations = {identity["project_root"]: identity["project_binding"]}
        for key, binding in identity["bindings"].items():
            _canonical_binding(binding)
            path = identity["repository_root" if key == "root" else key]
            if path in locations and locations[path] != binding:
                raise StateError("Canonical directory bindings disagree")
            locations[path] = binding
        _fields(identity["membership"], ("included", "authorization"))
        if identity["membership"]["included"] is not True:
            raise StateError("Canonical task requires accepted membership")
        _canonical_ref(identity["membership"]["authorization"])
        config = identity["configuration"]
        _fields(config, ("policy_digest", "runtime_digest", "runtime"))
        for key in ("policy_digest", "runtime_digest"):
            _canonical_hash(config[key])
        _fields(config["runtime"], ("schema_version", "state_dir"))
        if type(config["runtime"]["schema_version"]) is not int or config["runtime"]["schema_version"] != 3:
            raise StateError("Canonical task requires schema 3")
        _canonical_path(config["runtime"]["state_dir"])
        shared = identity["shared_policy"]
        if not isinstance(shared, list) or len(shared) > 4096:
            raise StateError("Invalid accepted dependencies")
        for item in shared:
            _fields(item, ("path", "sha256"))
            _canonical_path(item["path"]); _canonical_hash(item["sha256"])
        paths = [item["path"] for item in shared]
        if paths != sorted(set(paths)):
            raise StateError("Accepted dependency paths must be unique and sorted")
        return identity
    except (StartupError, ValueError, TypeError, KeyError, RecursionError):
        raise StateError("Canonical accepted identity is invalid") from None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _physical_identity(identity):
    return {key: identity[key] for key in ("version", "project_id", "repository_id", "checkout_id", "project_root",
        "project_binding", "primary_root", "repository_root", "git_dir", "git_common_dir", "bindings")}



def _frozen_snapshot(value):
    if isinstance(value, MappingProxyType):
        return all(isinstance(key, str) and _frozen_snapshot(item) for key, item in value.items())
    if isinstance(value, tuple):
        return all(_frozen_snapshot(item) for item in value)
    return value is None or isinstance(value, (str, int, bool))



def _owner_time():
    try:
        value = int(time.time())
        _integer(value, "owner creation time")
        return value
    except (ValueError, OverflowError):
        raise StateError("Owner creation time is unavailable") from None


def _capability(scope, task, operation, *, attempt_ref=None):
    """Consume Core's retained decision; request data cannot issue admission."""
    from runtime import setup_observation
    try:
        keys = setup_observation.OPERATION_CAPABILITIES[operation]
        decision = setup_observation.require_capability(scope, keys, task=task,
            operation=operation, attempt_ref=attempt_ref)
        if (not isinstance(decision, setup_observation._CapabilityAdmission)
                or decision.issuer is not setup_observation._ISSUER
                or decision.mode not in ('ordinary', 'qualification')):
            raise StateError('Capability admission requires its retained owner')
        if decision.mode == 'ordinary' and (operation == 'task-patch' or operation == 'task-start' and task['mode'] == 'editable'):
            decision = setup_observation.require_capability(scope, (*keys, 'post-tool-handling'),
                task=task, operation=operation)
        if decision.mode == 'qualification':
            _canonical_ref(_canonical_plain(decision.attempt_ref))
            if decision.limits is None or decision.original_attempt is None:
                raise StateError('Qualification admission lost its original limits')
        return decision
    except StartupError as error:
        raise StateError(str(error)) from None


def _ordinary_capability(scope, task, operation):
    decision = _capability(scope, task, operation)
    if decision.mode != 'ordinary' or 'qualification_attempt' in task:
        raise StateError('Qualification evidence cannot authorize ordinary readiness or completion')
    return decision


def _artifact_reference(value):
    _fields(value, ("name", "digest"))
    if not isinstance(value["name"], str) or not re.fullmatch(r"[0-9a-f]{32}\.json", value["name"]):
        raise StateError("Invalid retained artifact reference")
    _canonical_hash(value["digest"])


def _task_lifecycle(task):
    """Validate stored dates only; absence/closure never establishes safe cleanup."""
    if "lifecycle" not in task:
        return None
    value = task["lifecycle"]
    _fields(value, ("created_at", "safe_closed_at", "closure", "compact"))
    _integer(value["created_at"], "original task creation time")
    if value["compact"] is not None:
        _artifact_reference(value["compact"])
    if value["safe_closed_at"] is None:
        if value["closure"] is not None:
            raise StateError("Closure has no owner-proved closure time")
    else:
        _integer(value["safe_closed_at"], "safe closure time", value["created_at"])
        closure = value["closure"]
        _fields(closure, ("version", "task_revision", "dependency_digest", "observed_at", "evidence"))
        if type(closure["version"]) is not int or closure["version"] != 1:
            raise StateError("Unsupported stored closure receipt")
        _integer(closure["task_revision"], "closure task revision", 1)
        _integer(closure["observed_at"], "closure observation time", value["created_at"])
        _canonical_hash(closure["dependency_digest"])
        if not isinstance(closure["evidence"], list) or len(closure["evidence"]) > 4096:
            raise StateError("Invalid closure evidence references")
        for reference in closure["evidence"]:
            _artifact_reference(reference)
    return value


def _retired_identity(kind, identity):
    fields = {
        "task": ("scope_id", "client", "session_id", "task_id"),
        "assignment": ("scope_id", "client", "session_id", "task_id", "dispatch_id"),
        "turn": ("scope_id", "client", "session_id", "turn_id"),
        "receipt": ("scope_id", "client", "session_id", "turn_id", "tool_call_id", "kind"),
    }
    if kind not in fields:
        raise StateError("Unsupported retired identity kind")
    _fields(identity, fields[kind])
    _canonical_hash(identity["scope_id"])
    for field, value in identity.items():
        _text(value, "retired " + field, 128 if field == "task_id" else 4096)
    return _digest(identity)


def _retired_record(kind, digest, raw, assignment_id):
    try:
        scope=_CAPTURE_SQL_SCOPE.get()
        if scope is None:
            value=json_object(raw)
            return _validate_retired_record(kind,digest,value,assignment_id)
        return scope[0].current_acquisition().decode(raw,json_object,
            validate=lambda value:_validate_retired_record(kind,digest,value,assignment_id))
    except (StartupError, ValueError, TypeError, KeyError, RecursionError) as error:
        if _resource_origin(error):raise
        raise StateError("Retired identity marker is corrupt or incompatible") from None


def _validate_retired_record(kind,digest,value,assignment_id):
    _fields(value, ("version", "identity", "pins_digest", "request_digest", "created_at", "retired_at", "outcome", "evidence_expired",
                    *(["assignment_id"] if kind == "assignment" else [])))
    if (type(value["version"]) is not int or value["version"] != 1
            or _retired_identity(kind, value["identity"]) != digest
            or value["outcome"] not in ("completed", "failed", "cancelled", "unknown", "DONE", "REVIEW", "CANCELLED") or kind=="assignment" and value["outcome"] in {"DONE","REVIEW","CANCELLED"}
            or type(value["evidence_expired"]) is not bool
            or value["evidence_expired"] and value["outcome"] != "unknown"):
        raise StateError("Retired identity marker is corrupt")
    if kind == "assignment":
        if (not isinstance(assignment_id, str) or not re.fullmatch(r"[0-9a-f]{32}", assignment_id)
                or value["assignment_id"] != assignment_id):
            raise StateError("Retired native assignment identity differs from its scalar")
    elif assignment_id is not None:
        raise StateError("Only assignment markers may have a native assignment ID")
    for field in ("pins_digest", "request_digest"):
        if value[field] is not None:
            _canonical_hash(value[field])
    if (kind in ("task", "assignment") and value["pins_digest"] is None
            or kind in ("assignment", "receipt") and value["request_digest"] is None):
        raise StateError("Retired identity is missing replay digests")
    _integer(value["created_at"], "original marker creation time")
    _integer(value["retired_at"], "marker retirement time", value["created_at"])
    return value


def _retired(db, kind, identity):
    """Point replay lookup for exact owner-written retirement markers."""
    version = _capture_scalar(db,'user_version')
    if version in (1, 2):
        return None
    if version != 3:
        raise StateError("Unsupported marker authority")
    scope=_CAPTURE_SQL_SCOPE.get();identity_lease=None
    if scope is not None:
        owner=scope[0].current_acquisition()
        identity_lease=_capture_identity_precharge(owner,identity)
    digest = _retired_identity(kind, identity)
    with _capture_point(db,'retired',(kind,digest)) as row:
        if row is None:value=None
        else:
            value = _retired_record(kind, digest, *row)
            if value["identity"] != identity:
                raise StateError("Retired identity differs from its lookup key")
        row=None
    digest=None
    if identity_lease is not None:owner.release(identity_lease)
    return value


def _retired_assignment(db, assignment_id):
    """Resolve the original native ID through its scalar index, never lifetime scans."""
    version = _capture_scalar(db,'user_version')
    if version in (1, 2):
        return None
    if version != 3 or not isinstance(assignment_id, str) or not re.fullmatch(r"[0-9a-f]{32}", assignment_id):
        raise StateError("Unsupported retired native assignment lookup")
    with _capture_point(db,'retired_assignment',(assignment_id,)) as row:
        value=_retired_record("assignment",*row) if row else None
        row=None
    return value


def _metadata(db, name):
    """Read stored shape/accounting columns; no file or dependency trust is implied."""
    if _capture_scalar(db,'user_version') != 3:
        return None
    _text(name, "metadata name", 4096)
    with _capture_point(db,'metadata',(name,)) as row:
        if row is None:return None
        try:value=_metadata_record(row)
        except (StartupError, ValueError, TypeError, KeyError, RecursionError) as error:
            if _resource_origin(error):raise
            raise StateError("Evidence metadata is corrupt or incompatible; content remains protected") from None
        row=None
    return value


def _metadata_record(row,*,comparison_budget=None):
    name = row[0]
    value = _capture_json(row[8],json_object,comparison_budget=comparison_budget)
    _fields(value, ("version", "owner", "created_at", "class", "bytes", "status", "closed_at",
                    "dependencies", "targets", "export", "managed"), ("screening", "screening_result"))
    if type(value["version"]) is not int or value["version"] not in (1, 2):
        raise StateError("Unsupported evidence metadata")
    owner = value["owner"]
    if not isinstance(owner, dict):
        raise StateError("Invalid evidence metadata owner")
    if owner.get("kind") == "temporary":
        _fields(owner, ("kind", "id", "created_at"))
        if not isinstance(owner["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", owner["id"]):
            raise StateError("Invalid temporary evidence owner")
        _integer(owner["created_at"], "original owner creation time")
        owner_key = "temporary:" + owner["id"]
    else:
        _fields(owner, ("kind", "project_id", "task"))
        if owner["kind"] != "project" or not isinstance(owner["project_id"], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", owner["project_id"]):
            raise StateError("Invalid Project evidence owner")
        owner_key = "project:" + owner["project_id"]
        if owner["task"] is not None:
            _task_reference(owner["task"])
    _integer(value["created_at"], "artifact original creation time")
    _integer(value["bytes"], "observed artifact bytes")
    if value["closed_at"] is not None:
        _integer(value["closed_at"], "first artifact closure time", value["created_at"])
    if value["class"] not in ("detail", "compact") or value["status"] not in ("live", "delete_pending", "expired"):
        raise StateError("Invalid evidence metadata class/status")
    if row[:8] != (name, row[1], owner_key, value["class"], value["created_at"], value["closed_at"], value["status"], value["bytes"]):
        raise StateError("Evidence metadata columns disagree with stored values")
    _canonical_hash(row[1])
    managed = value["managed"]
    _fields(managed, ("path", "binding", "parents", "sha256", "mode", "bytes"))
    path = _canonical_path(managed["path"])
    _canonical_binding(managed["binding"])
    _metadata_parents(path, managed["parents"])
    if str(path.parent) not in managed["parents"]:
        raise StateError("Managed content lacks its parent binding")
    _integer(managed["mode"], "observed managed mode")
    _integer(managed["bytes"], "observed managed bytes")
    if managed["mode"] > 0o777 or managed["sha256"] != row[1] or managed["bytes"] != value["bytes"]:
        raise StateError("Managed content metadata disagrees with observed columns")
    if value["export"] is not None:
        _fields(value["export"], ("kind",))
        if value["export"]["kind"] != "managed-copy":
            raise StateError("Unsupported managed export")
    elif path.name != name or not re.fullmatch(r"[0-9a-f]{32}\.json", name):
        raise StateError("Artifact name differs from its managed content")
    for field in ("dependencies", "targets"):
        if not isinstance(value[field], list) or len(value[field]) > 4096:
            raise StateError("Invalid bounded evidence metadata references")
    # Actual owner-specific dependencies and physical trust are composed by the later evidence owner.
    for dependency in value["dependencies"]:
        _fields(dependency, ("kind", "reference"))
        if dependency["kind"] not in ("task", "artifact", "assignment", "recovery", "delivery") or not isinstance(dependency["reference"], dict) or not dependency["reference"]:
            raise StateError("Invalid typed evidence dependency")
    for target in value["targets"]:
        _fields(target, ("path", "binding", "parents", "git"))
        target_path = _canonical_path(target["path"])
        _canonical_binding(target["binding"])
        _metadata_parents(target_path, target["parents"])
        if target["git"] is not None:
            git = target["git"]
            _fields(git, ("root", "primary_root", "git_dir", "git_common_dir", "binding"))
            _fields(git["binding"], ("root", "primary_root", "git_dir", "git_common_dir"))
            for field in git["binding"]:
                _canonical_path(git[field]); _canonical_binding(git["binding"][field])
    if "screening" in value:
        _screening_record(value["screening"])
        if (owner["kind"] != "temporary" or value["class"] != "compact"
                or value["status"] != "live" or value["closed_at"] is not None
                or not value["targets"] or value["dependencies"] or value["export"] is not None
                or value["created_at"] != owner["created_at"]):
            raise StateError("Screening accounting requires its live temporary owner anchor")
    if "screening_result" in value:
        pointer = _screening_result(value["screening_result"])
        if ("screening" not in value or value["screening"]["revision"] != 2
                or value["screening"]["last_receipt"]["ticket"] != pointer["ticket"]):
            raise StateError("Screening result requires its exact terminal receipt")
    return value


def _screening_result(value):
    _fields(value, ("version", "ticket", "plan_directory", "directory_binding", "plan_id", "projection_digest"))
    if type(value["version"]) is not int or value["version"] != 1:
        raise StateError("Unsupported screening result")
    _screening_ticket(value["ticket"])
    path = _canonical_path(value["plan_directory"])
    binding = value["directory_binding"]
    _fields(binding, ("directory", "parents"))
    _canonical_binding(binding["directory"])
    _metadata_parents(path, binding["parents"])
    if set(binding["parents"]) != {str(p) for p in path.parents}:
        raise StateError("Incomplete screening plan parent bindings")
    if not isinstance(value["plan_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["plan_id"]):
        raise StateError("Invalid screening plan identity")
    _canonical_hash(value["projection_digest"])
    return value


def _screening_ticket(value):
    _fields(value, ("sequence", "nonce", "ceiling_ms"))
    if (type(value["sequence"]) is not int or value["sequence"] != 1
            or type(value["ceiling_ms"]) is not int or value["ceiling_ms"] != 300000
            or not isinstance(value["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["nonce"])):
        raise StateError("Invalid screening ticket")
    return value


def _screening_record(value):
    _fields(value, ("version", "birth", "limit_ms", "revision", "charged_ms", "settled_sequence",
                    "reservation", "last_receipt"))
    for name in ("version", "limit_ms", "revision", "charged_ms", "settled_sequence"):
        _integer(value[name], "screening " + name)
    if (value["version"] != 1 or value["birth"] != "temporary-owner-publication"
            or value["limit_ms"] != 300000):
        raise StateError("Unsupported screening accounting")
    reservation, receipt = value["reservation"], value["last_receipt"]
    if reservation is not None:
        _screening_ticket(reservation)
    if receipt is not None:
        _fields(receipt, ("ticket", "disposition"))
        _screening_ticket(receipt["ticket"])
        if receipt["disposition"] != "charged-full":
            raise StateError("Unsupported screening disposition")
    shape = (value["revision"], value["charged_ms"], value["settled_sequence"],
             reservation is not None, receipt is not None)
    if shape not in ((0, 0, 0, False, False), (1, 0, 0, True, False), (2, 300000, 1, False, True)):
        raise StateError("Inconsistent screening accounting")
    return value


def _metadata_parents(path, parents):
    if not isinstance(parents, dict) or not parents or len(parents) > 4096:
        raise StateError("Invalid metadata parent observations")
    for name, binding in parents.items():
        parent = _canonical_path(name)
        if parent == path or not path.is_relative_to(parent):
            raise StateError("Metadata parent escapes its target")
        _canonical_binding(binding)


def _task_reference(value):
    _fields(value, ("authority", "scope_id", "client", "session_id", "task_id"))
    _retired_identity("task", {k: value[k] for k in ("scope_id", "client", "session_id", "task_id")})
    authority = value["authority"]
    _fields(authority, ("directory", "database", "directory_binding", "database_binding"))
    directory, database = (_canonical_path(authority[k]) for k in ("directory", "database"))
    if database != directory / "runtime.sqlite3":
        raise StateError("Task reference database differs from its authority")
    _canonical_binding(authority["directory_binding"]); _canonical_binding(authority["database_binding"])


def _lifecycle_identity(db, kind, reference):
    """Read bounded original identity/age from one exact existing schema-3 authority."""
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is not None and kind == 'task':
        return _captured_lifecycle_identity(db,reference,scope)
    if scope is not None and kind == 'assignment':
        if scope[1] is not None:raise StateError('Lifecycle cannot borrow exit-validation allowance')
        _capture_registered(scope[0],db)
        captured_owner=scope[0].current_acquisition()
        chars,units,integer=_lifecycle_reference_size(captured_owner,reference)
        tree=4*(chars+1)+384*(units+1)+4*integer
        path=768+196*(chars+1)
        captured_owner.reserve(requested=20*tree+2*path,live=12*tree+2*path+4096)
    from runtime import delegation, evidence
    if db is None or not db.in_transaction:
        raise StateError("Lifecycle identity requires its existing authority transaction")
    if kind == "task":
        _task_reference(reference)
        identity = {key: reference[key] for key in ("scope_id", "client", "session_id", "task_id")}
    elif kind == "assignment":
        _fields(reference, ("authority", "id", "owner", "assignment"))
        if not isinstance(reference["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", reference["id"]):
            raise StateError("Invalid lifecycle assignment ID")
        _retired_identity("assignment", reference["owner"])
        _artifact_reference(reference["assignment"])
        identity = dict(id=reference["id"], owner=reference["owner"])
    else:
        raise StateError("Unsupported lifecycle identity kind")

    context = evidence._existing_authority_context(reference["authority"], db=db)
    authority = _canonical_plain(context.authority_set[0])
    if reference["authority"] != authority:
        raise StateError("Lifecycle identity authority is not canonical")
    _schema(db, version=CANONICAL_SCHEMA_VERSION)

    if kind == "task":
        marker = _retired(db, "task", identity)
        row = db.execute("select 1 from tasks where scope_id=? and client=? and session_id=? and task_id=?",
                         tuple(identity[key] for key in ("scope_id", "client", "session_id", "task_id"))).fetchone()
        if row is None:
            if marker is None:
                raise StateError("Lifecycle task identity or original age is unavailable")
            return _canonical_freeze(dict(status="expired" if marker["evidence_expired"] else "retired",
                identity=identity, pins_digest=marker["pins_digest"], request_digest=marker["request_digest"],
                created_at=marker["created_at"]))
        if marker is not None:
            raise StateError("Live task conflicts with its retired identity marker")
        task = _task(db, identity["scope_id"], identity["client"], identity["session_id"], identity["task_id"])
        lifecycle = _task_lifecycle(task)
        if task["pins"]["schema_version"] != CANONICAL_SCHEMA_VERSION or lifecycle is None:
            raise StateError("Lifecycle task lacks canonical pins or an original creation time")
        return _canonical_freeze(dict(status="live", identity=identity, pins_digest=_digest(task["pins"]),
            request_digest=None, created_at=lifecycle["created_at"]))

    marker_owner = _retired(db, "assignment", reference["owner"])
    marker_id = _retired_assignment(db, reference["id"])
    row = _lifecycle_assignment_row(db,reference["id"])
    if row is None:
        if marker_owner is None or marker_id is None or marker_owner != marker_id:
            raise StateError("Lifecycle assignment identity or original age is unavailable")
        if marker_id["identity"] != reference["owner"] or marker_id["assignment_id"] != reference["id"]:
            raise StateError("Retired assignment identity differs from its reference")
        # The marker intentionally retains identity, not the old concrete assignment artifact pointer.
        return _canonical_freeze(dict(status="expired" if marker_id["evidence_expired"] else "retired",
            identity=identity, pins_digest=marker_id["pins_digest"], request_digest=marker_id["request_digest"],
            created_at=marker_id["created_at"]))
    if marker_owner is not None or marker_id is not None:
        raise StateError("Live assignment conflicts with its retired identity marker")
    record = delegation._record(None, row, db=db, context=context)
    if scope is not None:
        chars,units,integer=_lifecycle_reference_size(captured_owner,record)
        tree=4*(chars+1)+384*(units+1)+4*integer
        serialized=12*(chars+1)+4*(units+1)+4*integer+32
        # Fresh credit for original binding checks, pins hashing and both compact containers.
        captured_owner.reserve(requested=8*tree+6*serialized,live=8*tree+6*serialized)
    if (record["id"] != reference["id"] or record["owner"] != reference["owner"] or
            record["data"]["assignment"] != reference["assignment"]):
        raise StateError("Live assignment differs from its exact identity reference")
    task_owner = record["owner"]
    task = _task(db, task_owner["scope_id"], task_owner["client"], task_owner["session_id"], task_owner["task_id"])
    if (task["pins"] != record["body"]["pins"] or
            task.get("accepted_identity") != record["body"].get("accepted_identity")):
        raise StateError("Live assignment differs from its original task pins")
    lifecycle = record["data"].get("lifecycle")
    if lifecycle is None:
        raise StateError("Lifecycle assignment lacks an original creation time")
    _fields(lifecycle, ("created_at",))
    _integer(lifecycle["created_at"], "original assignment creation time")
    return _canonical_freeze(dict(status="live", identity=identity,
        pins_digest=_digest(record["body"]["pins"]), request_digest=record["data"]["request_digest"],
        created_at=lifecycle["created_at"]))


def _lifecycle_assignment_row(db,identity):
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is not None:
        resources,escrow=scope
        if escrow is not None:raise StateError('Assignment point read cannot borrow exit-validation allowance')
        _capture_registered(resources,db)
        owner=resources.current_acquisition()
        owner.reserve(requested=512,live=2048)
        try:
            measured=db.execute(
                'select count(*),coalesce(sum(coalesce(length(cast(id as blob)),0)+'
                'coalesce(length(cast(scope_id as blob)),0)+coalesce(length(cast(client as blob)),0)+'
                'coalesce(length(cast(session_id as blob)),0)+coalesce(length(cast(task_id as blob)),0)+'
                'coalesce(length(cast(dispatch_id as blob)),0)+coalesce(length(cast(data as blob)),0)),0),'
                "coalesce(sum(typeof(id)!='text' or typeof(scope_id)!='text' or typeof(client)!='text' or "
                "typeof(session_id)!='text' or typeof(task_id)!='text' or typeof(dispatch_id)!='text' or "
                "typeof(data)!='text'),0) from "
                '(select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=? limit 2)',
                (identity,)).fetchone()
            if (type(measured) is not tuple or len(measured)!=3
                    or any(type(value) is not int or value<0 for value in measured)
                    or measured[0]>1 or measured[2]):
                raise StateError('Captured assignment point measurement overflow or invalid scalar')
            material=4*measured[1]+128*measured[0]
            owner.reserve(requested=material,live=4*material+512*measured[0])
        except BaseException as error:owner.fail(error);raise
    return db.execute("select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=?",
        (identity,)).fetchone()


def _lifecycle_reference_size(owner,value,depth=0):
    # Charge the visit/frame before recursion, integer arithmetic or iterator allocation.
    visit=owner.reserve(requested=512,live=1024)
    if depth>owner.resources.depth_limit:
        _capture_unsupported('Resource lifecycle reference depth exhausted')
    kind=type(value);chars=integer=0;units=1
    if kind is str:chars=len(value)
    elif kind is int:
        bits=value.bit_length()
        integer=bits+2  # Covers native limbs and signed decimal width without coercion.
    elif value is None or kind in (bool,float):pass
    elif kind not in (dict,MappingProxyType,list,tuple):
        _capture_unsupported('Resource lifecycle reference requires native values')
    scalar=owner.reserve(requested=4*(chars+1)+4*integer,live=4*(chars+1)+4*integer)
    if kind in (dict,MappingProxyType):
        for key,item in value.items():
            for child in (key,item):
                c,u,i=_lifecycle_reference_size(owner,child,depth+1)
                chars+=c;units+=u;integer+=i
                if units>owner.resources.unit_limit:
                    _capture_unsupported('Resource lifecycle reference units exhausted')
    elif kind in (list,tuple):
        for child in value:
            c,u,i=_lifecycle_reference_size(owner,child,depth+1)
            chars+=c;units+=u;integer+=i
            if units>owner.resources.unit_limit:
                _capture_unsupported('Resource lifecycle reference units exhausted')
    result=(chars,units,integer)
    child=key=item=value=None
    owner.release(scalar);owner.release(visit)
    return result


def _captured_lifecycle_identity(db,reference,scope):
    owner=scope[0].current_acquisition()
    control=owner.reserve(requested=512,live=2048)
    if scope[1] is not None:raise StateError('Lifecycle cannot borrow exit-validation allowance')
    _capture_registered(scope[0],db)
    if not db.in_transaction:
        raise StateError('Lifecycle identity requires its existing authority transaction')
    chars,units,integer=_lifecycle_reference_size(owner,reference)
    tree=4*(chars+1)+384*(units+1)+4*integer
    path=768+196*(chars+1)
    reference_lease=owner.reserve(requested=20*tree+2*path,live=12*tree+2*path+4096)
    decodes=[];scratch=[]
    result=_captured_lifecycle_task_result(db,reference,owner,decodes,scratch)
    # The helper frame (task, marker, lifecycle and temporary compact aliases) is gone.
    for decoded in decodes:owner._release_owned(decoded)
    decodes.clear();decoded=None
    for lease in scratch:owner.release(lease)
    scratch.clear();lease=None
    owner.release(reference_lease);owner.release(control)
    return result


def _lifecycle_retired_owned(db,identity,owner):
    version=_capture_scalar(db,'user_version')
    if version in (1,2):return None
    if version!=3:raise StateError('Unsupported marker authority')
    identity_lease=_capture_identity_precharge(owner,identity)
    digest=_retired_identity('task',identity)
    with _capture_point(db,'retired',('task',digest)) as row:
        decoded=None
        if row is not None:
            try:
                decoded=owner._decode_owned(row[0],json_object,
                    lambda value:_validate_retired_record('task',digest,value,row[1]))
            except (StartupError,ValueError,TypeError,KeyError,RecursionError) as error:
                if _resource_origin(error):raise
                raise StateError('Retired identity marker is corrupt or incompatible') from None
            if decoded.value['identity']!=identity:
                raise StateError('Retired identity differs from its lookup key')
        row=None
    digest=None
    owner.release(identity_lease)
    return decoded


def _lifecycle_task_owned(db,identity,owner):
    scope_id,client,session,task_id=(identity[k] for k in ('scope_id','client','session_id','task_id'))
    inputs=_capture_task_inputs(db,owner,scope_id,client,session,task_id)
    if _capture_scalar(db,'user_version')!=3:
        _capture_unsupported('Resource task capture requires canonical schema 3')
    with _capture_point(db,'task',(scope_id,client,session,task_id)) as row:
        if row is None:
            if _task_retired(db,scope_id,client,session,task_id):
                raise StateError('Retired task evidence is unavailable or expired')
            raise StateError('Runtime task binding is corrupt')
        version=_capture_scalar(db,'user_version')
        if version!=3:_capture_unsupported('Resource task capture requires canonical schema 3')
        decoded=owner._decode_owned(row[0],lambda raw:json_object(raw,limit=4*1024*1024),
            lambda value:_validate_task(db,scope_id,client,session,task_id,version,value))
        row=None
    owner.release(inputs)
    return decoded


def _captured_lifecycle_task_result(db,reference,owner,decodes,scratch):
    from runtime import evidence
    _task_reference(reference)
    identity={key:reference[key] for key in ('scope_id','client','session_id','task_id')}
    context=evidence._existing_authority_context(reference['authority'],db=db)
    authority=_canonical_plain(context.authority_set[0])
    if reference['authority']!=authority:
        raise StateError('Lifecycle identity authority is not canonical')
    _schema(db,version=CANONICAL_SCHEMA_VERSION)
    marker_decode=_lifecycle_retired_owned(db,identity,owner)
    if marker_decode is not None:
        owner._require_owned(marker_decode)
        decodes.append(marker_decode)
    marker=marker_decode.value if marker_decode is not None else None
    presence=owner.reserve(requested=512,live=2048)
    row=db.execute('select 1 from tasks where scope_id=? and client=? and session_id=? and task_id=?',
        tuple(identity[key] for key in ('scope_id','client','session_id','task_id'))).fetchone()
    present=row is not None
    row=None
    owner.release(presence)
    if not present:
        if marker is None:raise StateError('Lifecycle task identity or original age is unavailable')
        tail=owner.reserve(requested=4*marker_decode.tree+3*marker_decode.serialized,
            live=4*marker_decode.tree+3*marker_decode.serialized)
        scratch.append(tail)
        return _lifecycle_compact_owned(owner,'expired' if marker['evidence_expired'] else 'retired',
            identity,marker['pins_digest'],marker['request_digest'],marker['created_at'],scratch)
    if marker is not None:raise StateError('Live task conflicts with its retired identity marker')
    task_decode=_lifecycle_task_owned(db,identity,owner)
    owner._require_owned(task_decode)
    decodes.append(task_decode)
    task=task_decode.value
    # Covers lifecycle revalidation, pins serialization/hash, and both compact containers.
    tail=owner.reserve(requested=4*task_decode.tree+3*task_decode.serialized,
        live=4*task_decode.tree+3*task_decode.serialized)
    scratch.append(tail)
    lifecycle=_task_lifecycle(task)
    if task['pins']['schema_version']!=CANONICAL_SCHEMA_VERSION or lifecycle is None:
        raise StateError('Lifecycle task lacks canonical pins or an original creation time')
    return _lifecycle_compact_owned(owner,'live',identity,_digest(task['pins']),None,lifecycle['created_at'],scratch)


def _lifecycle_compact_owned(owner,status,identity,pins_digest,request_digest,created_at,scratch):
    chars=integer=0;units=1
    for key,value in zip(('status','identity','pins_digest','request_digest','created_at'),
            (status,identity,pins_digest,request_digest,created_at)):
        for item in (key,value):
            c,u,i=_lifecycle_reference_size(owner,item)
            chars+=c;units+=u;integer+=i
    tree=4*(chars+1)+384*(units+1)+4*integer
    building=owner.reserve(requested=2*tree+1024,live=2*tree+1024)
    scratch.append(building)
    destination=owner.reserve(requested=tree+1024,live=tree+1024)
    return _canonical_freeze(dict(status=status,identity=identity,pins_digest=pins_digest,
        request_digest=request_digest,created_at=created_at))


def inspect_dependencies(db, task_ref):
    """Observe bounded existing rows; this is not closure or deletion permission."""
    from runtime import evidence
    capture=_CAPTURE_SQL_SCOPE.get();resource_owner=None
    projection_chars=projection_units=projection_integer=0
    turn_material=receipt_material=0
    if capture is not None:
        resources,escrow=capture
        if escrow is not None:raise StateError('Dependency inventory cannot borrow exit-validation allowance')
        _capture_registered(resources,db);resource_owner=resources.current_acquisition()
        if not db.in_transaction:raise StateError('Dependency inventory requires its existing transaction')
        ref_chars,ref_units,ref_integer=_lifecycle_reference_size(resource_owner,task_ref)
        reference_tree=4*(ref_chars+1)+384*(ref_units+1)+4*ref_integer
        resource_owner.reserve(requested=4*reference_tree+1024,live=4*reference_tree+1024)
    def project(value):
        nonlocal projection_chars,projection_units,projection_integer
        if resource_owner is not None:
            chars,units,integer=_lifecycle_reference_size(resource_owner,value)
            tree=4*(chars+1)+384*(units+1)+4*integer
            resource_owner.reserve(requested=4*tree,live=4*tree)
            projection_chars+=chars;projection_units+=units;projection_integer+=integer
        return value
    _task_reference(task_ref)
    context=evidence._existing_authority_context(task_ref['authority'],db=db)
    selected_identity=_lifecycle_identity(db,'task',task_ref)
    authority=_canonical_plain(context.authority_set[0])
    def bounded(sql):
        nonlocal projection_chars,projection_units,projection_integer,turn_material,receipt_material
        if resource_owner is not None:
            _capture_registered(resources,db)
            if resources.current_acquisition() is not resource_owner:
                raise StateError('Dependency inventory acquisition changed')
            resource_owner.reserve(requested=512+4*len(sql),live=2048+8*len(sql))
            try:
                columns=sql[7:sql.index(' from ')].split(',')
                material='+'.join('coalesce(length(cast('+column+' as blob)),0)' for column in columns)
                measured=db.execute('select count(*),coalesce(sum('+material+'),0) from ('+sql+' limit 4097)').fetchone()
                if (type(measured) is not tuple or len(measured)!=2
                        or any(type(value) is not int or value<0 for value in measured) or measured[0]>4097):
                    raise StateError('Dependency inventory measurement overflow or invalid scalar')
                count,raw_bytes=measured
                if count>4096:raise StateError('Lifecycle current history exceeds its observation bound')
                if ' from turns ' in sql:turn_material=raw_bytes
                elif ' from receipts ' in sql:receipt_material=raw_bytes
                cells=count*len(columns)
                raw=4*raw_bytes+128*cells
                resource_owner.reserve(requested=raw,live=4*raw+512*cells)
                # Separate credit for native row-derived references, lists, sets and blockers.
                chars=raw_bytes+count*ref_chars
                units=cells+count+1+count*ref_units
                integer=64*cells+count*ref_integer
                tree=4*(chars+1)+384*(units+1)+4*integer
                resource_owner.reserve(requested=4*tree,live=4*tree)
                projection_chars+=chars;projection_units+=units;projection_integer+=integer
            except BaseException as error:resource_owner.fail(error);raise
        rows=db.execute(sql+' limit 4097').fetchall()
        if len(rows)>4096:raise StateError('Lifecycle current history exceeds its observation bound')
        return rows
    tasks=[];requirements=[];blockers=[]
    for scope_id,client,session,task_id in bounded('select scope_id,client,session_id,task_id from tasks order by scope_id,client,session_id,task_id'):
        reference=dict(authority=authority,scope_id=scope_id,client=client,session_id=session,task_id=task_id)
        task=project(_task(db,scope_id,client,session,task_id))
        tasks.append(dict(reference=reference,task=task))
        if reference!=task_ref:requirements.append(dict(owner=reference,kind='task',reference=reference,need='raw'))
    selected=next((row['task'] for row in tasks if row['reference']==task_ref),None)
    if selected is None and selected_identity['status']=='live':raise StateError('Lifecycle task body is unavailable')
    assignments=bounded('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments order by id')
    for row in assignments:
        requirements.append(dict(owner=dict(authority=authority,id=row[0]),kind='assignment',
            reference=dict(authority=authority,id=row[0],owner=dict(zip(('scope_id','client','session_id','task_id','dispatch_id'),row[1:6]))),need='raw'))
        if tuple(row[1:5])==tuple(task_ref[k] for k in ('scope_id','client','session_id','task_id')):
            blockers.append('Assignment body requires original task; exact assignment certificate integration is required')
    turns=bounded('select scope_id,client,session_id,turn_id,task_id,continuation_consumed from turns order by scope_id,client,session_id,turn_id')
    receipts=bounded('select scope_id,client,session_id,turn_id,tool_call_id,kind,fingerprint,conflicted from receipts order by scope_id,client,session_id,turn_id,tool_call_id,kind')
    owner=tuple(task_ref[k] for k in ('scope_id','client','session_id'))
    known_tasks={tuple(row['reference'][k] for k in ('scope_id','client','session_id','task_id')) for row in tasks}
    for row in turns:
        if tuple(row[:3])==owner and tuple(row[:3])+(row[4],) not in known_tasks:
            blockers.append('Turn task ownership is ambiguous')
    if resource_owner is not None and receipts and turns:
        comparisons=len(receipts)*len(turns)
        # Two four-cell slices and native cell comparisons for every possible pair.
        resource_owner.reserve(requested=512*comparisons+8*(len(receipts)*turn_material+len(turns)*receipt_material),
            live=2048+8*(turn_material+receipt_material))
    for row in receipts:
        if tuple(row[:3])==owner and row[7]:blockers.append('Conflicted receipt ownership remains protected')
        if tuple(row[:3])==owner and not any(tuple(t[:4])==tuple(row[:4]) and t[4] is not None for t in turns):
            blockers.append('Receipt task ownership is ambiguous')
    metadata=[]
    for row in bounded('select name,digest,owner_key,class,created_at,closed_at,status,bytes,data from evidence_metadata order by name'):
        metadata.append(dict(reference=dict(name=row[0],digest=row[1]),metadata=project(_metadata_record(row))))
    checkouts=bounded('select scope_id,project_id,repository_root,git_common_dir,generation,physical_identity from checkouts order by scope_id')
    if not any(row[0]==task_ref['scope_id'] for row in checkouts):raise StateError('Lifecycle checkout is unavailable')
    bindings=bounded('select scope_id,client,session_id,task_id from bindings order by scope_id,client,session_id')
    if resource_owner is not None:
        tree=4*(projection_chars+ref_chars+1)+384*(projection_units+ref_units+1)+4*(projection_integer+ref_integer)
        serialized=12*(projection_chars+ref_chars+1)+4*(projection_units+ref_units+1)+4*(projection_integer+ref_integer)+32
        # Fresh whole-result freeze/plain copies, JSON encoding, hashing and output material.
        resource_owner.reserve(requested=6*tree+8*serialized+1024,live=4*tree+4*serialized+1024)
    result=dict(task=selected,tasks=tasks,assignments=assignments,turns=turns,receipts=receipts,metadata=metadata,
        checkouts=checkouts,bindings=bindings,requirements=requirements,blockers=sorted(set(blockers)))
    result=_canonical_plain(_canonical_freeze(result))
    result['digest']=_digest(result)
    return result


def _assert_open(task):
    if task is not None and task.get('lifecycle',{}).get('safe_closed_at') is not None:
        raise StateError('Safely closed task cannot admit mutations or new evidence')


def _ready_stop_bindings(context,db,task):
    """Actual complete original owner snapshot, without lifecycle or stop-envelope recursion."""
    from runtime import delegation
    scope_id=_digest([3,task['accepted_identity']['project_id'],task['accepted_identity']['repository_id'],task['accepted_identity']['checkout_id']])
    records=[r for r in delegation._ledger(None,db,context=context) if r['owner']['scope_id']==scope_id and
        all(r['owner'][k]==task[k] for k in ('client','session_id','task_id'))]
    if any(r['data']['status']!='settled' for r in records):raise StateError('Unresolved original ready owner remains protected')
    return dict(task_digest=_digest({k:v for k,v in task.items() if k not in {'lifecycle','writer_stop'}}),
        attempts_digest=_digest(task.get('evidence',{}).get('attempts',[])),
        assignments_digest=_digest(sorted(records,key=lambda r:r['id'])),
        generation=db.execute('select generation from checkouts where scope_id=?',(scope_id,)).fetchone()[0])


def _writer_stop_shape(value):
    _fields(value,('version','task_revision','proof_digest','proof','observation_ref','coordinator_ref','generation','candidate','verification','request_digest','inputs'),('linked',))
    if type(value['version']) is not int or value['version']!=1:raise StateError('Unsupported retained ready stop')
    _integer(value['task_revision'],'ready stop revision',1);_integer(value['generation'],'ready stop generation')
    for key in ('proof_digest','request_digest'):_canonical_hash(value[key])
    for key in ('proof','candidate','verification',*(['linked'] if 'linked' in value else [])):
        if key=='verification' and value[key] is None and 'linked' in value:continue
        _artifact_reference(value[key])
    for key in ('observation_ref','coordinator_ref'):_text(value[key],key)
    if not isinstance(value['inputs'],dict) or len(value['inputs'])>4096:raise StateError('Ready input observation exceeds bound')
    for path,info in value['inputs'].items():
        _canonical_path(path)
        if info is not None and (not isinstance(info,list) or len(info) not in (4,8) or any(type(n) is not int for n in info) or len(info)==4 and not stat.S_ISDIR(info[2])):
            raise StateError('Ready input physical observation is corrupt')


def _ready_stop_local_inputs(context,db,task):
    from runtime import evidence
    evidence._check_context(context,db)
    stop=task.get('writer_stop');_writer_stop_shape(stop)
    proof=evidence._read(None,stop['proof'],context=context,db=db)
    bindings=_ready_stop_bindings(context,db,task)
    candidate=evidence._read(None,stop['candidate'],context=context,db=db)
    linked=evidence._read(None,stop['linked'],context=context,db=db) if 'linked' in stop else None
    verification_ref=evidence._stop_verification(context,db,task,allow_absent=linked is not None and linked['version']==2)
    if linked is not None:evidence._linked_stop_shape(linked)
    return dict(proof=proof,bindings=bindings,candidate=candidate,linked=linked,verification_ref=verification_ref)


def _check_ready_stop_local_inputs(context,db,task,inputs):
    from runtime import evidence
    stop=task['writer_stop'];proof=inputs['proof'];bindings=inputs['bindings'];candidate=inputs['candidate']
    linked=inputs['linked'];verification_ref=inputs['verification_ref']
    evidence._ready_stop_proof(task,proof,bindings,candidate,verification_ref,linked)
    if (stop['task_revision']!=task['revision'] or stop['generation']!=bindings['generation'] or
        stop['verification']!=verification_ref or
        any(proof[k]!=stop[k] for k in ('observation_ref','coordinator_ref'))):
        raise StateError('Retained ready stop envelope changed')
    evidence._candidate_projection(candidate,task)
    if linked is None or task['lifecycle']['safe_closed_at'] is None:evidence._check_ready_inputs(stop['inputs'],directory_identity=True)
    verification=evidence._read(None,stop['verification'],context=context,db=db) if stop['verification'] is not None else None
    if task['state']=='DONE' and (verification is None or verification['candidate_digest']!=proof['candidate_digest']):raise StateError('Retained ready verification changed')
    return proof


def _ready_stop_local(context,db,task):
    """Validate raw local stop inputs; a returned linked job still needs complete validation."""
    from runtime import evidence
    inputs=_ready_stop_local_inputs(context,db,task)
    proof=_check_ready_stop_local_inputs(context,db,task,inputs)
    linked=inputs['linked']
    job=None if linked is None else dict(reference=evidence._linked_basis_root(linked),
        authorities=linked['authorities'],expected=linked)
    return dict(proof=proof,bindings=inputs['bindings'],candidate=inputs['candidate'],linked=job)


def _ready_stop(context,db,task,*,observe=False,read_context=None,read_roster=None):
    from runtime import evidence
    member=(dict(authority=evidence._canonical_plain(context.authority_set[0]),scope_id=evidence._task_scope_id(task),
        **{k:task[k] for k in ('client','session_id','task_id')}) if read_context is not None or read_roster is not None else None)
    stop=task.get('writer_stop');_writer_stop_shape(stop)
    if read_roster is not None:
        linked=evidence._read(None,stop['linked'],context=context,db=db) if stop.get('linked') else None
        read_roster=evidence._matching_linked_read_roster(read_roster,linked)
    if stop.get('linked') and task['lifecycle']['safe_closed_at'] is not None:
        linked=evidence._read(None,stop['linked'],context=context,db=db)
        if evidence._linked_component_closed(evidence._linked_basis_root(linked),writer=(context,db)):
            retained=evidence._linked_retained_component(evidence._linked_basis_root(linked),evidence._linked_read_authorities(read_context,linked,member=member,writer=(context,db),read_roster=read_roster),writer=(context,db))
            if retained.get('discovered_authorities'):raise StateError('Retained linked stop requires complete source guards')
            own=next(m for m in retained['members'] if m['reference']['authority']==evidence._canonical_plain(context.authority_set[0]) and
                m['reference']['scope_id']==evidence._task_scope_id(task) and all(m['reference'][key]==task[key] for key in ('client','session_id','task_id')))
            if own['task']!=task or own['preparation']['content']['stopped']['writer_stop']!=stop:raise StateError('Historical linked stop owner changed')
            return evidence._finish_linked_read_context(own['preparation']['content']['stopped']['proof'],read_context,read_roster=read_roster)
    inputs=_ready_stop_local_inputs(context,db,task)
    linked=inputs['linked']
    if linked is not None:
        if evidence._linked_stopped_basis(evidence._linked_basis_root(linked),evidence._linked_read_authorities(read_context,linked,member=member,writer=(context,db),read_roster=read_roster),writer=(context,db))!=linked:raise StateError('Linked complete-stop basis changed')
    proof=_check_ready_stop_local_inputs(context,db,task,inputs)
    if observe and linked is None:evidence._ready_stop_candidate(context,task,inputs['candidate'],read_context=read_context,read_roster=read_roster)
    return evidence._finish_linked_read_context(proof,read_context,read_roster=read_roster)


def _retain_ready_stop(scope,request,*,linked_basis=None):
    """Private complete host stop, preserving original outcome/revision and age."""
    from runtime import evidence,changes
    if not isinstance(scope,AcceptedScope) or _scope_version(scope)!=3:raise StateError('Ready stop requires actual accepted schema3 scope')
    _fields(request,('client','session_id','task_id','scope_ref','expected_revision','expected_pins','stop_proof'),('linked_verification',))
    _identity(request);_integer(request['expected_revision'],'ready stop request revision',1);_validate_pins(request['expected_pins'])
    context=evidence._evidence_context(scope)
    if linked_basis is not None and 'linked_verification' in request:raise StateError('Select one actual linked stop source')
    pointer=linked_basis if linked_basis is not None else request.get('linked_verification')
    request_digest=_digest(dict(request=request,linked_basis=linked_basis)) if linked_basis is not None else _digest(request)
    guards=evidence._linked_stop_guards(pointer,_canonical_plain(context.authority_set)) if pointer is not None else changes._authority_guards([context.directory])
    with guards as authorities:
        with evidence._context_database(context) as db:
            task=_task(db,_scope_id(scope),request['client'],request['session_id'],request['task_id'])
            _compatible(scope,task)
            if task['revision']!=request['expected_revision'] or task['pins']!=request['expected_pins'] or task['scope_ref']!=request['scope_ref']:
                raise StateError('Ready stop request changed original task pins/revision')
            if task.get('writer_stop'):
                if linked_basis is not None:
                    retained=evidence._read(None,task['writer_stop']['linked'],context=context,db=db)
                    if evidence._linked_basis_root(retained)!=pointer:raise StateError('Ready stop replay common basis changed')
                _ready_stop(context,db,task,observe=True,read_context=evidence._bind_linked_read_context(pointer,authorities,writer=(context,db),basis=evidence._read(None,task['writer_stop']['linked'],context=context,db=db)) if pointer is not None and task['writer_stop'].get('linked') else None)
                if task['writer_stop']['request_digest']!=request_digest or task['writer_stop']['proof_digest']!=request['stop_proof']['sha256']:
                    raise StateError('Ready stop replay payload changed')
                return dict(ok=True,task=task,replayed=True)
            _assert_open(task)
            if task['state'] not in ({'DONE','REVIEW','CANCELLED'} if pointer is not None else {'DONE','CANCELLED'}) or 'evidence' not in task:
                raise StateError('Independent ready stop requires actual ordinary ready verification')
            snapshot=_ready_stop_bindings(context,db,task)
            verification_ref=evidence._stop_verification(context,db,task,allow_absent=linked_basis is not None)
            plan=evidence._read(None,task['evidence']['plan'],context=context,db=db)
        proof=evidence._read_host_proof(scope,request['stop_proof'])
        linked=evidence._linked_stop_basis(pointer,authorities) if pointer is not None else None
        if linked is not None:
            selected=[row for row in linked['members'] if row['task']['authority']==_canonical_plain(context.authority_set[0]) and
                row['task']['scope_id']==_scope_id(scope) and all(row['task'][k]==task[k] for k in ('client','session_id','task_id'))]
            if len(selected)!=1:raise StateError('Linked stop task is outside the complete approved set')
        candidate=evidence.capture_candidate(scope,plan)
        files=selected[0]['inputs'] if linked is not None else evidence._ready_stop_candidate(context,task,candidate)
        with evidence._context_database(context,write=True) as db:
            current=_task(db,_scope_id(scope),request['client'],request['session_id'],request['task_id'])
            if current!=task or _ready_stop_bindings(context,db,current)!=snapshot:raise StateError('Ready owner changed during stop capture')
            evidence._check_ready_inputs(files)
            owner=evidence._artifact_owner(scope,task)
            # Validate before registering any artifact; the candidate ref digest is its actual canonical content.
            evidence._ready_stop_proof(task,proof,snapshot,candidate,verification_ref,linked)
            if linked is not None and evidence._linked_stop_basis(pointer,authorities)!=linked:raise StateError('Linked stop changed before owner commit')
            stop=dict(version=1,task_revision=task['revision'],proof_digest=request['stop_proof']['sha256'],
                proof=evidence._artifact(scope,proof,owner=owner,db=db),observation_ref=proof['observation_ref'],
                coordinator_ref=proof['coordinator_ref'],generation=snapshot['generation'],
                candidate=evidence._artifact(scope,candidate,owner=owner,db=db),verification=verification_ref,request_digest=request_digest,inputs=files)
            if linked is not None:
                dependencies=[dict(kind='artifact',reference=dict(authority=pointer['authority'],artifact=pointer['reference']))]
                dependencies += [dict(kind='task',reference=row['task']) for row in linked['members']]
                with evidence._bound_stop_registration(scope,request,context,db,owner,linked,dependencies,
                        proof,candidate,verification_ref,authorities) as retained_owner:
                    stop['linked']=evidence._artifact(scope,linked,owner=retained_owner,db=db)
            task=dict(task,writer_stop=stop);_writer_stop_shape(stop)
            raw=json.dumps(task,separators=(',',':'))
            if len(raw.encode())>4*1024*1024:raise StateError('Ready stop exceeds the original task record bound')
            db.execute('update tasks set data=? where scope_id=? and client=? and session_id=? and task_id=?',
                (raw,_scope_id(scope),task['client'],task['session_id'],task['task_id']))
            _ready_stop(context,db,task,read_context=evidence._bind_linked_read_context(pointer,authorities,writer=(context,db),basis=evidence._read(None,task['writer_stop']['linked'],context=context,db=db)) if pointer is not None and task['writer_stop'].get('linked') else None)
            evidence._check_ready_inputs(files)
            if linked is not None and evidence._linked_stop_basis(pointer,authorities)!=linked:raise StateError('Linked stop source changed during registration')
            return dict(ok=True,task=task,replayed=False)


def _close_lifecycle(db, task_ref, observations, compact):
    """Owner-only same-revision write; general _save never permits lifecycle changes."""
    from runtime import evidence
    context=evidence._existing_authority_context(task_ref['authority'],db=db)
    facts=evidence._check_closure_observation(context,db,task_ref,observations,new_compact=compact)
    task=_task(db,task_ref['scope_id'],task_ref['client'],task_ref['session_id'],task_ref['task_id'])
    _assert_open(task)
    if task['revision']!=facts['task']['revision'] or task['state']!=facts['task']['state'] or (task['state'] not in {'CANCELLED','DONE'} and not ('linked' in facts and task['state']=='REVIEW')):
        raise StateError('Closure original stopped owner changed')
    closed_at=_owner_time()
    original_age=max([task['lifecycle']['created_at']]+[item['created_at'] for item in facts['certificate'].get('assignments',[])]+
        [item['created_at'] for item in facts['projection']['numerical']+facts['projection']['verifications']])
    if closed_at<original_age:raise StateError('Closure clock regressed before original owner age')
    task['lifecycle']=dict(created_at=task['lifecycle']['created_at'],safe_closed_at=closed_at,compact=compact,
        closure=dict(version=1,task_revision=task['revision'],dependency_digest=_digest(facts),observed_at=closed_at,evidence=facts['artifacts']))
    _task_lifecycle(task)
    db.execute('update tasks set data=? where scope_id=? and client=? and session_id=? and task_id=?',
        (json.dumps(task,separators=(',',':')),task_ref['scope_id'],task_ref['client'],task_ref['session_id'],task_ref['task_id']))
    evidence._check_closure_observation(context,db,task_ref,observations,new_compact=compact)
    return task


def retire_closed(db, task_ref, closure):
    """Atomically mark exact identities before removing rows; never delete evidence files."""
    from runtime import evidence
    context=evidence._existing_authority_context(task_ref['authority'],db=db)
    facts=evidence._check_closure_observation(context,db,task_ref,closure,assignments_retired=True)
    assignment_markers=[(item['reference']['id'],_retired_assignment(db,item['reference']['id']))
        for item in facts['certificate'].get('assignments',[])]
    task=_task(db,task_ref['scope_id'],task_ref['client'],task_ref['session_id'],task_ref['task_id'])
    lifetime=_task_lifecycle(task)
    if lifetime is None or lifetime['safe_closed_at'] is None or evidence._read(None,lifetime['compact'],context=context,db=db)!=evidence._retirement_content(facts):
        raise StateError('Retirement requires exact current retained closure support')
    identity={k:task_ref[k] for k in ('scope_id','client','session_id','task_id')}
    if db.execute('select 1 from assignments where scope_id=? and client=? and session_id=? and task_id=?',tuple(identity.values())).fetchone():
        raise StateError('Surviving assignment still requires its original task')
    observed=inspect_dependencies(db,task_ref)
    if observed['blockers']:raise StateError('; '.join(observed['blockers']))
    retired_at=_owner_time()
    if retired_at<lifetime['safe_closed_at']:raise StateError('Retirement clock regressed before safe closure')
    pins_digest=_digest(task['pins']);age=lifetime['created_at'];markers=[]
    def marker(kind,key,request_digest=None):
        if _retired(db,kind,key):raise StateError('Live identity conflicts with its retired marker')
        data=dict(version=1,identity=key,pins_digest=pins_digest,request_digest=request_digest,
            created_at=age,retired_at=retired_at,outcome=task['state'],evidence_expired=False)
        digest=_retired_identity(kind,key);raw=json.dumps(data,separators=(',',':'))
        _retired_record(kind,digest,raw,None)
        db.execute('insert into retired_identities values(?,?,?,NULL)',(kind,digest,raw))
        markers.append((kind,key,data))
    turns=[row for row in observed['turns'] if tuple(row[:3])==tuple(identity.values())[:3] and row[4]==identity['task_id']]
    receipts=[row for row in observed['receipts'] if any(tuple(row[:4])==tuple(turn[:4]) for turn in turns)]
    if any(row[7] for row in receipts):raise StateError('Conflicted event receipt ownership remains protected')
    marker('task',identity)
    for row in turns:
        key=dict(zip(('scope_id','client','session_id','turn_id'),row[:4]));marker('turn',key)
    for row in receipts:
        key=dict(zip(('scope_id','client','session_id','turn_id','tool_call_id','kind'),row[:6]));marker('receipt',key,_digest(row[6]))
    for row in receipts:db.execute('delete from receipts where scope_id=? and client=? and session_id=? and turn_id=? and tool_call_id=? and kind=?',row[:6])
    for row in turns:db.execute('delete from turns where scope_id=? and client=? and session_id=? and turn_id=?',row[:4])
    db.execute('delete from bindings where scope_id=? and client=? and session_id=? and task_id=?',tuple(identity.values()))
    db.execute('delete from tasks where scope_id=? and client=? and session_id=? and task_id=?',tuple(identity.values()))
    evidence._check_closure_observation(context,db,task_ref,closure,new_compact=lifetime['compact'],retired=True)
    for kind,key,data in markers:
        if _retired(db,kind,key)!=data:raise StateError('Retirement marker changed before commit')
    if any(_retired_assignment(db,identity)!=marker for identity,marker in assignment_markers):
        raise StateError('Assignment retirement marker changed before commit')
    evidence._check_context(context,db)
    return dict(retired=True,identity=identity,outcome=task['state'])


def _scope_version(scope):
    if isinstance(scope, AcceptedScope):
        if (not all(isinstance(getattr(scope, key), MappingProxyType) and _frozen_snapshot(getattr(scope, key))
                    for key in ("accepted_identity", "bindings", "authority_target", "qualification_provenance"))
                or not all(isinstance(getattr(scope, key), Path) for key in
                    ("project_root", "repository_root", "primary_root", "git_dir", "git_common_dir", "authority_path"))
                or not isinstance(scope.runtime, RuntimeSettings) or type(scope.runtime.schema_version) is not int):
            raise StateError("Canonical scope requires immutable supported snapshots")
        identity = _accepted_identity(scope.accepted_identity)
        expected = dict(project_id=scope.project_id, repository_id=scope.repository_id, checkout_id=scope.checkout_id,
            project_root=str(scope.project_root), primary_root=str(scope.primary_root), repository_root=str(scope.repository_root),
            git_dir=str(scope.git_dir), git_common_dir=str(scope.git_common_dir), client=scope.client,
            bindings=_canonical_plain(scope.bindings))
        if (any(identity[key] != value for key, value in expected.items()) or scope.runtime is None
                or scope.runtime.schema_version != 3 or identity["configuration"] != dict(policy_digest=scope.policy_digest,
                    runtime_digest=scope.runtime_digest, runtime=dict(schema_version=3, state_dir=str(scope.runtime.state_dir)))
                or _digest(identity) != scope.accepted_scope_digest
                or scope.authority_path != scope.project_root / PROJECT_FILE):
            raise StateError("Canonical scope differs from its accepted projection")
        return 3
    if scope.runtime is None or scope.runtime.schema_version != SCHEMA_VERSION:
        raise StateError("Runtime state is not configured with a supported schema")
    return SCHEMA_VERSION


def _pins(scope, *, budget=None):
    version = _scope_version(scope) if isinstance(scope, AcceptedScope) else SCHEMA_VERSION
    runtime = scope.runtime_digest if version == 3 else runtime_fingerprint(budget=budget)
    pins = dict(schema_version=version, policy_digest=scope.policy_digest, runtime_digest=runtime)
    if version == 3 and not _installed_compatible(pins, budget=budget):
        raise StateError("Installed runtime differs from accepted canonical scope")
    if version == 3:
        pins["accepted_scope_digest"] = scope.accepted_scope_digest
    return pins


def _installed_compatible(pins, *, budget=None):
    from runtime.startup import _package_compatible, policy_digest
    return _package_compatible(pins, dict(runtime_digest=runtime_fingerprint(budget=budget), policy_digest=policy_digest(PACKAGE, budget=budget)))


def _scope_id(scope):
    if isinstance(scope, AcceptedScope):
        _scope_version(scope)
        return _digest([3, scope.project_id, scope.repository_id, scope.checkout_id])
    return hashlib.sha256(json.dumps([scope.project_id, str(scope.repository_root), str(scope.git_common_dir)]).encode()).hexdigest()


def _checkout_row(db, scope, *, initialize=False):
    version, scope_id = _scope_version(scope), _scope_id(scope)
    row = db.execute("select * from checkouts where scope_id=?", (scope_id,)).fetchone()
    expected = (scope_id, scope.project_id, str(scope.repository_root), str(scope.git_common_dir))
    physical = _physical_identity(_accepted_identity(scope.accepted_identity)) if version == 3 else None
    if row is not None:
        if (row[:4] != expected or type(row[4]) is not int or row[4] < 0
                or version == 3 and json_object(row[5]) != physical):
            raise StateError("Checkout row differs from accepted physical identity")
    elif initialize:
        if version == 3:
            db.execute("insert into checkouts values(?,?,?,?,0,?)", (*expected, json.dumps(physical, separators=(",", ":"))))
        else:
            db.execute("insert into checkouts values(?,?,?,?,0)", expected)
        row = db.execute("select * from checkouts where scope_id=?", (scope_id,)).fetchone()
        from runtime.workstreams import initialize_default
        initialize_default(scope, db)
    return row


def _task_projection(db, scope_id, task):
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is not None:scope[0].current_acquisition()._require_validation(task)
    version = _capture_scalar(db,'user_version')
    _validate_pins(task.get("pins"), version=version)
    if version != 3:
        if "accepted_identity" in task:
            raise StateError("Legacy task cannot acquire canonical identity")
        return
    identity = _accepted_identity(task.get("accepted_identity"))
    if scope is None:
        row=db.execute("select * from checkouts where scope_id=?",(scope_id,)).fetchone()
        database_path=Path(db.execute("pragma database_list").fetchone()[2])
        _validate_task_projection(identity,row,database_path,scope_id,task)
        return
    owner=scope[0].current_acquisition()
    with _capture_point(db,'checkout',(scope_id,)) as row:
        with _capture_point(db,'task_database',()) as database:
            length=len(database[2])
            path=owner.reserve(requested=768+196*(length+1),live=768+196*(length+1))
            database_path=Path(database[2])
            _validate_task_projection(identity,row,database_path,scope_id,task)
            database_path=database=None
            owner.release(path)
        row=None


def _checkout_matches(raw,identity):
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is None:return json_object(raw)==_physical_identity(identity)
    return scope[0].current_acquisition()._decode_check(raw,json_object,
        lambda value:value==_physical_identity(identity))


def _validate_task_projection(identity,row,database_path,scope_id,task):
    if (identity["configuration"]["runtime"]["state_dir"] != str(database_path.parent)
            or identity["client"] != task["client"] or _digest(identity) != task["pins"]["accepted_scope_digest"]
            or any(identity["configuration"][k] != task["pins"][k] for k in ("policy_digest", "runtime_digest"))
            or _digest([3, identity["project_id"], identity["repository_id"], identity["checkout_id"]]) != scope_id
            or row is None or row[:4] != (scope_id, identity["project_id"], identity["repository_root"], identity["git_common_dir"])
            or not _checkout_matches(row[5],identity)):
        raise StateError("Task snapshot, pins and physical checkout differ")


def _text(value, field, limit=1024):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise StateError("Invalid " + field)
    return value


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or not set(required) <= set(value):
        raise StateError("Invalid task control fields")


def _identity(request, *, allow_claude=False):
    for name in ("client", "session_id", "task_id", "scope_ref"):
        _text(request.get(name), name, 128 if name == "task_id" else 1024)
    if request["client"] != "codex" and not (allow_claude and request["client"] == "claude-code"):
        raise StateError("Unsupported task client")


def _require_claude_scope(scope, client):
    if client == "claude-code" and (not isinstance(scope, AcceptedScope) or scope.client != client):
        raise StateError("Claude Code runtime controls require an accepted canonical client scope")


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        raise StateError("Invalid " + field)


def _private(path, directory=False):
    info = path.lstat()
    if (info.st_uid != os.getuid() or info.st_mode & 0o077 or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
            (not directory and info.st_nlink != 1)):
        raise StateError("Runtime state requires private current-user directories and regular files")


def _path(scope):
    _scope_version(scope)
    directory = scope.runtime.state_dir
    if not directory.is_absolute() or directory.resolve() != directory:
        raise StateError("Runtime state path must be canonical and have no symlinks")
    for path in (directory, *directory.parents):
        if os.path.lexists(path) and (path.is_symlink() or not path.is_dir()):
            raise StateError("Runtime state ancestors must be directories without symlinks")
    if directory.exists():
        _private(directory, True)
    return directory / "runtime.sqlite3"


def _sidecars(path):
    _private(path)
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if os.path.lexists(sidecar):
            _private(sidecar)
            if suffix != "-journal":
                raise StateError("Unsupported runtime journal mode")


class ChangeWatch:
    """Cheap read-only change marker for one SQLite database file (Smart Routing S3.5 dispatch gates).

    Holds ONE read-only SQLite connection and asks `PRAGMA data_version`, which changes whenever another
    connection, in any process, commits to the file and never on a rollback; the file's (device, inode) from
    `os.lstat` makes a replaced file a change too. Markers compare only within one watch. It never opens a raw
    descriptor on the database: on POSIX, closing any descriptor of a file drops every lock this process holds
    on it, and only SQLite's own close defers that while its other connections still hold locks. A busy database
    (another connection holds PENDING or EXCLUSIVE) returns None after at most `BUSY_SECONDS`. Never writes."""
    BUSY_SECONDS = 0.1

    def __init__(self, path):
        self.path = Path(path)
        _sidecars(self.path)
        self._db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=self.BUSY_SECONDS,
                                   isolation_level=None)
        try:
            self._db.execute("pragma query_only=on")
        except BaseException:
            self._db.close()
            raise

    def marker(self):
        try:
            info = os.lstat(self.path)
            version = self._db.execute("pragma data_version").fetchone()[0]
        except sqlite3.OperationalError as error:
            if "locked" in str(error) or "busy" in str(error):
                return None
            raise StateError("Runtime state is unavailable, corrupt, locked or incompatible") from None
        return "%d:%d:%d" % (info.st_dev, info.st_ino, version)

    def close(self):
        self._db.close()


def _schema(db, *, version=SCHEMA_VERSION):
    with _capture_precharge(db,'schema') as selection:
        return _schema_selected(db,version=version,selection=selection)


def _schema_selected(db, *, version, selection):
    # Version 1 is accepted only by D4's private read-only transition inspector.
    tables = TABLES - {"assignments"} if version == 1 else set(CANONICAL_TABLE_SQL) if version == 3 else TABLES
    objects = (db.execute("select type,name,sql from sqlite_master").fetchall() if selection is None
        else db.execute(selection[0],selection[1]).fetchall())
    if (version not in (1, SCHEMA_VERSION, CANONICAL_SCHEMA_VERSION) or
            _capture_scalar(db,'application_id') != APPLICATION_ID or
            _capture_scalar(db,'user_version') != version or
            {name for kind, name, _ in objects if kind == "table"} != tables or
            _capture_scalar(db,'journal_mode') != "delete"):
        raise StateError("Runtime database is unowned or has an incompatible schema")
    for kind, name, sql in objects:
        if kind == "table":
            if "".join(sql.split()).lower() != "".join((CANONICAL_TABLE_SQL if version == 3 else TABLE_SQL)[name].split()).lower():
                raise StateError("Runtime database has an incompatible schema")
        elif kind == "index" and version == 3:
            if sql is None:
                allowed = {"sqlite_autoindex_" + table + "_1" for table in CANONICAL_TABLE_SQL} | {"sqlite_autoindex_assignments_2"}
                if name not in allowed:
                    raise StateError("Runtime database contains unexpected indexes")
            elif name not in CANONICAL_INDEX_SQL or sql.strip() != CANONICAL_INDEX_SQL[name]:
                raise StateError("Runtime database has incompatible admission indexes")
        elif kind != "index" or sql is not None:
            raise StateError("Runtime database contains unsupported schema objects")
    if version == 3 and {name for kind, name, sql in objects if kind == "index" and sql is not None} != set(CANONICAL_INDEX_SQL):
        raise StateError("Runtime database is missing required lifecycle indexes")


_CREATION_ISSUER = object()


@dataclass(frozen=True)
class _AuthorityCreation:
    authority: object
    created_at: int
    descriptor: int
    issuer: object


def _authority_identity(path):
    def binding(item):
        info = item.lstat()
        return dict(device=info.st_dev, inode=info.st_ino)
    return dict(directory=str(path.parent), database=str(path),
        directory_binding=binding(path.parent), database_binding=binding(path))


def _check_creation(path, creation, db=None):
    if not isinstance(creation, _AuthorityCreation) or creation.issuer is not _CREATION_ISSUER:
        raise StateError('Fresh authority creation witness is required')
    info = os.fstat(creation.descriptor)
    authority = _canonical_plain(creation.authority)
    _private(path.parent, True)
    _private(path)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or info.st_mode & 0o077 or dict(device=info.st_dev, inode=info.st_ino) != authority['database_binding']
            or _authority_identity(path) != authority):
        raise StateError('Fresh database physical identity changed')
    if db is not None:
        if db.execute('pragma database_list').fetchall() != [(0, 'main', str(path))]:
            raise StateError('Fresh database connection differs from creation witness')


def _prepare_directory(directory):
    missing = []
    ancestor = directory
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    for item in reversed(missing):
        try:
            item.mkdir(mode=0o700)
        except FileExistsError:
            _private(item, True)
    _private(directory, True)


def _initialize(path, *, version=SCHEMA_VERSION, expected_directory=None, initialize_metadata=None):
    if version != CANONICAL_SCHEMA_VERSION:
        raise StateError('Legacy authority creation is unavailable; retain the original pinned package and use approved D4 birth')
    if initialize_metadata is None:
        _prepare_directory(path.parent)
    else:
        _private(path.parent, True)
    if version == 3:
        parent_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory_info = os.fstat(parent_descriptor)
            binding = dict(device=directory_info.st_dev, inode=directory_info.st_ino)
            if expected_directory is not None and binding != expected_directory:
                raise StateError('Authority directory differs from the guarded physical identity')
            descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=parent_descriptor)
        finally:
            os.close(parent_descriptor)
    else:
        directory_info = path.parent.lstat()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    creation = None
    try:
        if version == 3:
            info = os.fstat(descriptor)
            authority = dict(directory=str(path.parent), database=str(path),
                directory_binding=dict(device=directory_info.st_dev, inode=directory_info.st_ino),
                database_binding=dict(device=info.st_dev, inode=info.st_ino))
            creation = _AuthorityCreation(_canonical_freeze(authority), _owner_time(), descriptor, _CREATION_ISSUER)
            _check_creation(path, creation)
            if initialize_metadata is not None:
                from runtime.changes import _checkpoint
                _checkpoint('birth-unreceipted')
        else:
            os.close(descriptor)
            descriptor = None
        with closing(sqlite3.connect(path, timeout=0.1)) as db, db:
            if creation is not None:
                _check_creation(path, creation, db)
            statements = list((CANONICAL_TABLE_SQL if version == 3 else TABLE_SQL).values())
            if version == 3:
                statements += list(CANONICAL_INDEX_SQL.values())
            db.executescript('BEGIN IMMEDIATE;\n' + ';\n'.join(statements) + ';')
            db.execute('pragma application_id=' + str(APPLICATION_ID))
            db.execute('pragma user_version=' + str(version))
            if creation is not None:
                _check_creation(path, creation, db)
            if initialize_metadata is not None:
                initialize_metadata(db, creation)
                _check_creation(path, creation, db)
        if creation is not None:
            os.fsync(creation.descriptor)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory)
            finally: os.close(directory)
        return creation
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _source_baseline_name(authority):
    return _digest(['authority-source-baseline', authority])[:32] + '.json'


def _source_baseline(scope, db, *, context=None):
    """Return fresh-owner provenance only; no source completeness or cleanup permission."""
    if (scope is None) == (context is None):
        raise StateError('Select exactly one existing evidence authority')
    if db is None or context is None and _scope_version(scope) != 3:
        return None
    from runtime import evidence
    selected = context
    context = context or evidence._evidence_context(scope)
    evidence._check_context(context, db)
    authority = _canonical_plain(context.authority_set[0])
    name = _source_baseline_name(authority)
    metadata = _metadata(db, name)
    if metadata is None:
        return None
    reference = dict(name=name, digest=metadata['managed']['sha256'])
    content = evidence._read(None if selected else scope, reference, context=selected, db=db)
    _fields(content, ('version', 'kind', 'source_contract', 'authority', 'project_id', 'created_at', 'runtime_digest'), ('birth_expectation',))
    if (type(content['version']) is not int or content['version'] != 1 or content['kind'] != 'authority-source-baseline'
            or type(content['source_contract']) is not int or content['source_contract'] not in (1, 2)
            or content['authority'] != authority or metadata['class'] != 'compact'
            or metadata['owner'] != dict(kind='project', project_id=content['project_id'], task=None)
            or metadata['created_at'] != content['created_at'] or metadata['closed_at'] is not None
            or metadata['targets'] or metadata['export'] is not None):
        raise StateError('Authority source baseline provenance is invalid')
    if 'birth_expectation' in content:
        expectation = content['birth_expectation']
        _fields(expectation, ('version', 'identity', 'intent_digest'))
        _fields(expectation['identity'], ('plan_dir', 'transaction_id', 'manifest_digest'))
        if expectation['version'] != 1 or type(expectation['version']) is not int or content['source_contract'] != 2:
            raise StateError('Unsupported original birth expectation')
        _canonical_path(expectation['identity']['plan_dir'])
        _text(expectation['identity']['transaction_id'], 'birth transaction')
        _canonical_hash(expectation['identity']['manifest_digest'])
        _canonical_hash(expectation['intent_digest'])
    _canonical_hash(content['runtime_digest'])
    _integer(content['created_at'], 'original authority creation time')
    for dependency in metadata['dependencies']:
        if dependency['kind'] != 'recovery':
            raise StateError('Authority baseline has an unsupported source dependency')
        _fields(dependency['reference'], ('identity', 'artifact'))
        _fields(dependency['reference']['identity'], ('plan_dir', 'transaction_id', 'manifest_digest'))
        _canonical_path(dependency['reference']['identity']['plan_dir'])
        _text(dependency['reference']['identity']['transaction_id'], 'recovery transaction identity')
        _canonical_hash(dependency['reference']['identity']['manifest_digest'])
        _artifact_reference(dependency['reference']['artifact'])
    return _canonical_freeze(dict(reference=reference, content=content, dependencies=metadata['dependencies']))


def _check_current_authority(scope, db, *, budget=None):
    if budget is not None: _entry_budget_check(budget)
    baseline = _source_baseline(scope, db)
    if budget is not None: _entry_budget_check(budget)
    if (baseline is None or baseline['content']['runtime_digest'] != _pins(scope, budget=budget)['runtime_digest']
            or baseline['content']['project_id'] != scope.project_id):
        raise StateError('Canonical authority belongs to another or unproven runtime generation; retain its original pinned reader')
    from runtime import onboarding
    observed = onboarding._authority_observation(scope.project_root, budget if budget is not None else dict(paths=0, bytes=0), canonical_only=True)
    if budget is not None: _entry_budget_check(budget)
    if observed['error'] or observed['target'] != _canonical_plain(scope.authority_target):
        raise StateError('Canonical project authority changed; reacquire the accepted scope')


@contextmanager
def _database(scope, write=False, initialize=False, *, guard_existing=False, historical=False, deadline=None):
    db = creation = None
    budget = dict(paths=0, bytes=0, deadline=deadline) if deadline is not None else None
    with ExitStack() as guards:
        try:
            if budget is not None: _entry_budget_check(budget)
            version = _scope_version(scope)
            if historical:
                if version != 2 or write or initialize or guard_existing:
                    raise StateError('Historical inspection is read-only and cannot create or mutate authority')
            elif version != 3:
                raise StateError('Legacy runtime requires its original pinned package; fresh approved D4 enrollment is required for current authority')
            path = _path(scope)
            if initialize and version == 3:
                if not write:
                    raise StateError('Fresh schema3 bootstrap requires its write transaction')
                _prepare_directory(path.parent)
                directory_binding = _authority_identity(path)['directory_binding'] if path.exists() else dict(
                    device=path.parent.lstat().st_dev, inode=path.parent.lstat().st_ino)
                from runtime.changes import _authority_guards
                guards.enter_context(_authority_guards([path.parent]))
                info = path.parent.lstat()
                if dict(device=info.st_dev, inode=info.st_ino) != directory_binding:
                    raise StateError('Authority directory changed before bootstrap guard')
            elif guard_existing and version == 3:
                if not write:
                    raise StateError('Existing canonical task guard requires its write transaction')
                _private(path.parent, True)
                authority = _authority_identity(path)
                from runtime.changes import _authority_guards
                guards.enter_context(_authority_guards([path.parent]))
                if _authority_identity(path) != authority:
                    raise StateError('Existing authority physical identity changed before task guard')
            if not os.path.lexists(path):
                if initialize:
                    try:
                        if version == 3:
                            creation = _initialize(path, version=version, expected_directory=directory_binding)
                        else:
                            creation = _initialize(path, version=version)
                    except FileExistsError:
                        pass
                else:
                    if version == 3:
                        raise StateError('Canonical authority database is missing; the owned D4 operation is required')
                    yield None
                    return
            if budget is not None: _entry_budget_check(budget)
            _sidecars(path)
            if creation is not None:
                _check_creation(path, creation)
            if budget is not None: _entry_budget_check(budget)
            remaining = min(0.1, max(0, deadline - time.monotonic())) if deadline is not None else 0.1
            db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=remaining)
            if deadline is not None:
                db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
                _entry_budget_check(budget)
            if creation is not None:
                _check_creation(path, creation, db)
            remaining = min(0.1, max(0, deadline - time.monotonic())) if deadline is not None else 0.1
            db.execute('pragma busy_timeout=' + str(int(remaining * 1000)))
            db.execute('pragma trusted_schema=off')
            if historical:db.execute('pragma query_only=on')
            if budget is not None: _entry_budget_check(budget)
            _schema(db, version=version)
            if budget is not None:
                _entry_budget_check(budget)
                db.execute('pragma busy_timeout=' + str(int(min(0.1, max(0, deadline - time.monotonic())) * 1000)))
            db.execute('begin immediate' if write else 'begin')
            if budget is not None: _entry_budget_check(budget)
            if creation is not None:
                from runtime.evidence import _register_source_baseline
                _register_source_baseline(scope, db, creation)
                _check_creation(path, creation, db)
            elif version == 3:
                _check_current_authority(scope, db, budget=budget)
            if budget is not None: _entry_budget_check(budget)
            yield db
            if budget is not None: _entry_budget_check(budget)
            if write:
                if version == 3 and creation is None:
                    _check_current_authority(scope, db, budget=budget)
                _sidecars(path)
                if creation is not None:
                    _check_creation(path, creation, db)
                if budget is not None:
                    _entry_budget_check(budget)
                    db.execute('pragma busy_timeout=' + str(int(min(0.1, max(0, deadline - time.monotonic())) * 1000)))
                db.commit()
                if creation is not None:
                    _check_creation(path, creation, db)
        except StateError:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            raise StateError('Runtime state is unavailable, corrupt, locked or incompatible') from None
        finally:
            if db is not None:
                db.close()
            if creation is not None:
                os.close(creation.descriptor)


def _capture_unsupported(message):
    error=StateError(message);error._vcw_resource_origin=_CAPTURE_SQL_SCOPE.get()[0]
    raise error


def _capture_task_inputs(db,owner,scope_id,client,session,task_id):
    owner.resources._check_native_connection(db)
    bookkeeping=owner.reserve(requested=512,live=512)
    values=(scope_id,client,session,task_id)
    if any(value is not None and type(value) is not str for value in values):
        _capture_unsupported('Resource task capture requires native identifiers')
    if (type(scope_id) is not str or len(scope_id)!=64 or type(client) is not str or len(client)>11
            or type(session) is not str or len(session)>1024 or task_id is not None and len(task_id)>128):
        _capture_unsupported('Resource task identifier exceeds supported bounds')
    size=sum(len(value) for value in values if value is not None)
    lease=owner.reserve(requested=8*size+1024,live=8*size+1024)
    if (not re.fullmatch(r'[0-9a-f]{64}',scope_id) or client not in ('codex','claude-code')
            or not session.strip() or not session.isprintable()
            or task_id is not None and (not task_id.strip() or not task_id.isprintable())):
        _capture_unsupported('Resource task capture has invalid identifiers')
    values=None
    owner.release(bookkeeping)
    return lease


def _capture_identity_precharge(owner,identity):
    control=owner.reserve(requested=512,live=512)
    if type(identity) is not dict or len(identity)>6 or any(type(k) is not str or type(v) is not str for k,v in identity.items()):
        _capture_unsupported('Resource retired lookup requires native identity')
    chars=sum(len(k)+len(v) for k,v in identity.items());units=2*len(identity)+1
    tree=4*(chars+1)+384*(units+1);serialized=12*(chars+1)+4*(units+1)+32
    lease=owner.reserve(requested=4*tree+3*serialized,live=4*tree+3*serialized)
    owner.release(control)
    return lease


def _task_retired(db,scope_id,client,session,task_id):
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is None:return _retired(db,'task',dict(scope_id=scope_id,client=client,session_id=session,task_id=task_id))
    owner=scope[0].current_acquisition()
    size=len(scope_id)+len(client)+len(session)+len(task_id)
    lease=owner.reserve(requested=4*size+4096,live=4*size+4096)
    identity=dict(scope_id=scope_id,client=client,session_id=session,task_id=task_id)
    value=_retired(db,'task',identity)
    identity=None
    if value is not None:return value
    owner.release(lease)
    return None


def _task(db, scope_id, client, session, task_id=None, *, historical=False):
    scope=_CAPTURE_SQL_SCOPE.get()
    if scope is None:return _task_unaccounted(db,scope_id,client,session,task_id,historical=historical)
    if historical:_capture_unsupported("Historical task inspection requires its original unaccounted reader")
    owner=scope[0].current_acquisition()
    inputs=_capture_task_inputs(db,owner,scope_id,client,session,task_id)
    task=_captured_task_lookup(db,scope_id,client,session,task_id)
    owner.release(inputs)
    return task


def _captured_task_lookup(db,scope_id,client,session,task_id):
    owner=_CAPTURE_SQL_SCOPE.get()[0].current_acquisition()
    if _capture_scalar(db,'user_version')!=3:
        _capture_unsupported('Resource task capture requires canonical schema 3')
    if task_id is None:
        with _capture_point(db,'binding',(scope_id,client,session)) as binding:
            if binding is None:return None
            task_id=binding[0]
            inputs=_capture_task_inputs(db,owner,scope_id,client,session,task_id)
            task=_captured_task_row(db,scope_id,client,session,task_id)
            binding=task_id=None
            owner.release(inputs)
            return task
    return _captured_task_row(db,scope_id,client,session,task_id)


def _captured_task_row(db,scope_id,client,session,task_id):
    with _capture_point(db,'task',(scope_id,client,session,task_id)) as row:
        if row is None:
            if _task_retired(db,scope_id,client,session,task_id):
                raise StateError("Retired task evidence is unavailable or expired")
            raise StateError("Runtime task binding is corrupt")
        version=_capture_scalar(db,'user_version')
        if version!=3:_capture_unsupported('Resource task capture requires canonical schema 3')
        task=_CAPTURE_SQL_SCOPE.get()[0].current_acquisition().decode(row[0],
            lambda raw:json_object(raw,limit=4*1024*1024),
            validate=lambda value:_validate_task(db,scope_id,client,session,task_id,version,value))
        row=None
    return task


def _task_unaccounted(db, scope_id, client, session, task_id=None, *, historical=False):
    if task_id is None:
        binding = db.execute("select task_id from bindings where scope_id=? and client=? and session_id=?",
                             (scope_id, client, session)).fetchone()
        if binding is None:
            return None
        task_id = binding[0]
    row = db.execute("select data from tasks where scope_id=? and client=? and session_id=? and task_id=?",
                     (scope_id, client, session, task_id)).fetchone()
    if row is None:
        if _retired(db, "task", dict(scope_id=scope_id, client=client, session_id=session, task_id=task_id)):
            raise StateError("Retired task evidence is unavailable or expired")
        raise StateError("Runtime task binding is corrupt")
    version = db.execute("pragma user_version").fetchone()[0]
    task = json_object(row[0], limit=4 * 1024 * 1024) if version == 3 else json.loads(row[0])
    return _validate_task(db,scope_id,client,session,task_id,version,task,historical=historical)


def _validate_task(db,scope_id,client,session,task_id,version,task,*,historical=False):
    _fields(task, ("client", "session_id", "task_id", "scope_ref", "mode", "decision_ref", "state", "revision",
                   "pins", "reason", "reference", "conditions", "turn_id", "turn_uncertain", *(["accepted_identity"] if version == 3 else [])), ("resolution_ref", "evidence", "retirement", "delegation", "milestones", "delivery", *(["lifecycle","writer_stop","qualification_attempt","user_attention"] if version == 3 else [])))
    if 'qualification_attempt' in task:
        _canonical_ref(task['qualification_attempt'])
        if task['state'] == 'DONE':
            raise StateError('Qualification task cannot acquire ordinary completion')
    if "delivery" in task:
        from runtime.delivery import validate_record
        validate_record(task["delivery"])
    stored_claude = (version == 3 and task.get("client") == "claude-code" and
                     isinstance(task.get("accepted_identity"), dict) and
                     task["accepted_identity"].get("client") == "claude-code")
    _identity(task, allow_claude=stored_claude)
    _integer(task["revision"], "stored revision", 1)
    for field in ("decision_ref", "reason", "reference", "resolution_ref"):
        if field in task:
            _text(task[field], "stored " + field)
    legacy = ((historical and version in (1, 2) and task['state'] in _HISTORICAL_STATES or task['state'] in RETIRED_STATES)
        and isinstance(task['pins'], dict) and task['pins'].get('runtime_digest') != runtime_fingerprint())
    if (task['state'] in _HISTORICAL_STATES or task['state'] in RETIRED_STATES) and not legacy:
        raise StateError('Historical task vocabulary requires its original pinned package; current authority requires approved D4 birth')
    if (task["task_id"] != task_id or task["client"] != client or task["session_id"] != session or
            (task["state"] not in STATES and not legacy) or task["mode"] not in {"editable", "read-only"} or
            type(task["turn_uncertain"]) is not bool or not isinstance(task["conditions"], list) or
            len(task["conditions"]) > 64):
        raise StateError("Runtime task record is corrupt")
    if task["turn_id"] is not None:
        _text(task["turn_id"], "stored turn_id", 4096)
    _task_projection(db, scope_id, task)
    if task["client"] == "claude-code" and any(field in task for field in ("evidence", "delegation", "retirement", "writer_stop")):
        raise StateError("Claude Code task records do not support evidence, delegation or retirement")
    if version == 3:
        _task_lifecycle(task)
        if _task_retired(db,scope_id,client,session,task_id):
            raise StateError("Live task conflicts with a retired identity marker")
    seen = set()
    for condition in task["conditions"]:
        _fields(condition, ("id", "kind", "action", "reason", "reference", "status"), ("resolution_ref", "continuation"))
        for field in ("id", "reason", "reference", "resolution_ref"):
            if field in condition:
                _text(condition[field], "stored condition " + field, 128 if field == "id" else 1024)
        if (condition["id"] in seen or condition["kind"] not in ("blocker", "decision", "runnable") or
                condition["action"] not in ("patch", "task") or condition["status"] not in ("pending", "resolved") or
                (condition["status"] == "resolved" and "resolution_ref" not in condition)):
            raise StateError("Runtime condition record is corrupt")
        seen.add(condition["id"])
        if "continuation" in condition:
            continuation = condition["continuation"]
            _fields(continuation, ("turn_id", "budget", "consumed", "revoked"))
            _text(continuation["turn_id"], "stored continuation turn_id", 4096)
            if (condition["kind"] != "runnable" or type(continuation["revoked"]) is not bool or any(
                    type(continuation[field]) is not int or continuation[field] not in (0, 1)
                    for field in ("budget", "consumed"))):
                raise StateError("Runtime continuation record is corrupt")
    if "evidence" in task:
        from runtime.evidence import validate_evidence
        validate_evidence(task["evidence"], canonical=version == 3)
    if "delegation" in task:
        from runtime.delegation import validate_delegation
        validate_delegation(task["delegation"])
    if "milestones" in task:_milestones(task["milestones"])
    if 'user_attention' in task and (type(task['user_attention']) is not bool or task['state'] not in ATTENTION_STATES | RETIRED_ATTENTION_STATES):
        raise StateError('Invalid stored title attention')
    if "writer_stop" in task:_writer_stop_shape(task["writer_stop"])
    if "retirement" in task:
        retirement = task["retirement"]
        _fields(retirement, ("proof_digest", "proof", "observation_ref", "coordinator_ref", "previous_revision"))
        for field in ("proof_digest", "observation_ref", "coordinator_ref"):
            _text(retirement[field], "stored retirement " + field)
        _integer(retirement["previous_revision"], "stored retirement revision", 1)
        if task["state"] not in ({"cancelled"} if legacy else {"CANCELLED"}) or retirement["previous_revision"] >= task["revision"]:
            raise StateError("Retired task must remain terminal with original history")
    return task


def _save(db, scope_id, task):
    if _capture_scalar(db, 'user_version') != CANONICAL_SCHEMA_VERSION:
        raise StateError('Legacy task mutation requires its original pinned package; current authority requires approved D4 birth')
    if task.get('state') not in STATES or not _installed_compatible(task.get('pins', {})):
        raise StateError('Historical task mutation requires its original pinned package; current authority requires approved D4 birth')
    previous = db.execute('select data from tasks where scope_id=? and client=? and session_id=? and task_id=?',
        (scope_id,task['client'],task['session_id'],task['task_id'])).fetchone()
    if previous is not None:
        original = json_object(previous[0], limit=4 * 1024 * 1024)
        if original.get('state') not in STATES or original.get('pins') != task['pins']:
            raise StateError('Historical task vocabulary and pins cannot be rewritten')
        if (original.get('qualification_attempt') != task.get('qualification_attempt')
                or ('qualification_attempt' in original) != ('qualification_attempt' in task)):
            raise StateError('Original qualification purpose is immutable')
    if 'qualification_attempt' in task:
        _canonical_ref(task['qualification_attempt'])
        if task['state'] == 'DONE':
            raise StateError('Qualification task cannot acquire ordinary completion')
    if 'milestones' in task:_milestones(task['milestones'])
    if task['state'] not in ATTENTION_STATES:
        task.pop('user_attention', None)
    _task_projection(db, scope_id, task)
    if task["pins"]["schema_version"] == 3:
        previous = db.execute("select data from tasks where scope_id=? and client=? and session_id=? and task_id=?",
            (scope_id, task["client"], task["session_id"], task["task_id"])).fetchone()
        if previous:
            old = json_object(previous[0], limit=4 * 1024 * 1024)
            _assert_open(old)
            if old["accepted_identity"] != task["accepted_identity"] or old["pins"] != task["pins"]:
                raise StateError("Stored task identity and pins are immutable")
            if old.get("writer_stop")!=task.get("writer_stop") or ("writer_stop" in old)!=("writer_stop" in task):
                raise StateError("Ready stop belongs to its independent owner writer")
            if old.get("lifecycle") != task.get("lifecycle") or ("lifecycle" in old) != ("lifecycle" in task):
                raise StateError("Original lifecycle age/closure is immutable until owner integration")
        else:
            if "lifecycle" in task or "writer_stop" in task:
                raise StateError("Caller cannot supply task lifecycle age or closure")
            task["lifecycle"] = dict(created_at=_owner_time(), safe_closed_at=None, closure=None, compact=None)
        _task_lifecycle(task)
        owner = dict(scope_id=scope_id, **{k: task[k] for k in ("client", "session_id", "task_id")})
        if _retired(db, "task", owner):
            raise StateError("Retired task cannot be saved or rebound")
    if "delegation" in task:
        from runtime.delegation import validate_delegation
        validate_delegation(task["delegation"])
    if 'evidence' in task:
        from runtime.evidence import validate_evidence
        validate_evidence(task['evidence'], canonical=task['pins']['schema_version'] == 3)
    db.execute("insert or replace into tasks values(?,?,?,?,?)", (scope_id, task["client"], task["session_id"],
               task["task_id"], json.dumps(task, separators=(",", ":"))))


def _compatible(scope, task, *, budget=None):
    if task is not None and (task["state"] not in STATES or task["pins"] != _pins(scope, budget=budget) or isinstance(scope, AcceptedScope)
                            and task["accepted_identity"] != _canonical_plain(scope.accepted_identity)):
        raise StateError("Task policy, runtime or schema pins differ; retain the original pinned package and arrange fresh approved D4 enrollment")


def _status(db, scope, client, session):
    scope_id = _scope_id(scope)
    task = _task(db, scope_id, client, session) if db else None
    _compatible(scope, task)
    row = _checkout_row(db, scope) if db else None
    return dict(ok=True, task=task, generation=row[4] if row else 0,
                blockers=[condition for condition in task["conditions"] if condition["status"] == "pending" and
                          condition["kind"] in {"blocker", "decision"}] if task else [], limitations=LIMITATIONS)


def task_status(scope, client, session_id):
    _text(client, "client")
    _text(session_id, "session_id")
    if client not in {"codex", "claude-code"}:
        raise StateError("Unsupported task client")
    _require_claude_scope(scope, client)
    if client == "codex":
        from runtime.evidence import revalidate_ready
        revalidate_ready(scope, client, session_id)
    with _database(scope) as db:
        status = _status(db, scope, client, session_id)
        if client == "claude-code" and status["task"] is not None and status["task"]["state"] == "DONE":
            raise StateError("Claude Code task readiness is unverified by the available evidence adapter")
        if status['task'] is not None and status['task'].get('lifecycle',{}).get('safe_closed_at') is not None:
            from runtime.evidence import _closed_status
            status['historical']=_closed_status(scope,status['task'])
        return status


def inspect_state(scope):
    """Inspect ownership/schema/pins without creating files or requiring a session."""
    report = dict(configured=scope.runtime is not None, present=False, compatible=False,
                  schema_version=3 if isinstance(scope, AcceptedScope) else SCHEMA_VERSION, policy_digest=scope.policy_digest,
                  runtime_digest=runtime_fingerprint(), tasks=[])
    if isinstance(scope, AcceptedScope):
        try: _scope_version(scope)
        except StateError as error:
            report["error"] = str(error)
            return report
    if scope.runtime is None:
        report["compatible"] = True
        return report
    report["present"] = os.path.lexists(scope.runtime.state_dir / "runtime.sqlite3")
    try:
        with _database(scope) as db:
            if db:
                _checkout_row(db, scope)
                for client, session, task_id in db.execute("select client,session_id,task_id from tasks where scope_id=?", (_scope_id(scope),)):
                    task = _task(db, _scope_id(scope), client, session, task_id)
                    report["tasks"].append(dict(task_id=task_id, pins=task["pins"], compatible=task["pins"] == _pins(scope)))
                report["compatible"] = all(task["compatible"] for task in report["tasks"])
            else:
                report["compatible"] = True
    except StateError as error:
        report["error"] = str(error)
    return report


def _guard_assignments(db, scope, client, session):
    from runtime.delegation import _ledger
    if any(record['owner']['scope_id'] == _scope_id(scope) and record['owner']['client'] == client and
           record['owner']['session_id'] == session and record['data']['status'] != 'settled'
           for record in _ledger(scope, db, current=True)):
        raise StateError('Unresolved assignments retain historical session ownership; reconcile exact attempts first')


def start_task(scope, request):
    _fields(request, ("client", "session_id", "task_id", "scope_ref", "mode", "decision_ref"), ("replaces", "attempt_ref"))
    _identity(request, allow_claude=request.get("client") == "claude-code")
    _require_claude_scope(scope, request["client"])
    _text(request["decision_ref"], "decision_ref")
    if request["mode"] not in ("read-only", "editable"):
        raise StateError("Invalid task mode")
    if 'attempt_ref' in request:
        _canonical_ref(request['attempt_ref'])
    replacement = request.get("replaces")
    if "replaces" in request:
        _fields(replacement, ("task_id", "expected_revision"))
        _text(replacement["task_id"], "replaced task_id", 128)
        _integer(replacement["expected_revision"], "expected_revision", 1)
    if isinstance(scope, AcceptedScope) and request["client"] != scope.client:
        raise StateError("Task client differs from accepted scope")
    pins = _pins(scope)
    if isinstance(scope, AcceptedScope) and not _path(scope).exists():
        raise StateError('Canonical authority birth requires the owned D4 operation')
    with _database(scope, write=True, initialize=not isinstance(scope, AcceptedScope),
                   guard_existing=isinstance(scope, AcceptedScope)) as db:
        if db is None:
            raise StateError('Canonical authority is unavailable')
        if isinstance(scope, AcceptedScope) and _source_baseline(scope, db) is None:
            raise StateError('Canonical authority original baseline is unavailable')
        placeholders=','.join('?' for _ in STATES)
        incompatible=db.execute("select 1 from tasks where json_type(data,'$.state') is not 'text' or "
            "json_extract(data,'$.state') not in ("+placeholders+") or json_type(data,'$.pins.runtime_digest') is not 'text' or "
            "json_extract(data,'$.pins.runtime_digest') != ? or json_type(data,'$.pins.schema_version') is not 'integer' or "
            "json_extract(data,'$.pins.schema_version') != ? or json_type(data,'$.pins.policy_digest') is not 'text' or "
            "(scope_id=? and json_extract(data,'$.pins.policy_digest') != ?) limit 1",
            (*sorted(STATES),pins['runtime_digest'],_scope_version(scope),_scope_id(scope),pins['policy_digest'])).fetchone()
        if incompatible:
            raise StateError('Retained task authority is incompatible; preserve its original pinned package and arrange any fresh approved enrollment through D4')
        scope_id = _scope_id(scope)
        if _retired(db, "task", dict(scope_id=scope_id, **{k:request[k] for k in ("client", "session_id", "task_id")})):
            raise StateError("Retired task identity cannot start again; evidence is unavailable or expired")
        _checkout_row(db, scope, initialize=True)
        if isinstance(scope, AcceptedScope) and not db.execute('select 1 from tasks where scope_id=? limit 1', (scope_id,)).fetchone():
            from runtime.workstreams import initialize_default
            initialize_default(scope, db)
        current = _task(db, scope_id, request["client"], request["session_id"])
        if current and current["task_id"] == request["task_id"]:
            _assert_open(current)
            _compatible(scope, current)
            if any(current[field] != request[field] for field in ("scope_ref", "mode", "decision_ref")):
                raise StateError("Task mode, scope and decision reference are immutable")
            if 'attempt_ref' in request and request['attempt_ref'] != current.get('qualification_attempt'):
                raise StateError('Task cannot select another qualification attempt')
            return _status(db, scope, request["client"], request["session_id"])
        if db.execute("select 1 from tasks where scope_id=? and client=? and session_id=? and task_id=?",
                      (scope_id, request["client"], request["session_id"], request["task_id"])).fetchone():
            raise StateError("Historical task identity cannot be rebound or replaced")
        _guard_assignments(db, scope, request["client"], request["session_id"])
        if current:
            _compatible(scope, current)
            if replacement != dict(task_id=current["task_id"], expected_revision=current["revision"]):
                raise StateError("Replacing a bound task requires its identity and current revision")
            if current.get("lifecycle",{}).get("safe_closed_at") is None:
                current["revision"] += 1
            for condition in current["conditions"]:
                if "continuation" in condition:
                    condition["continuation"]["revoked"] = True
            if current.get("lifecycle",{}).get("safe_closed_at") is None:
                _save(db, scope_id, current)
        elif replacement is not None:
            raise StateError("No bound task matches the requested replacement")
        task = {field: request[field] for field in ("client", "session_id", "task_id", "scope_ref", "mode", "decision_ref")}
        task.update(state="TRIAGE", revision=1, pins=pins, reason="Task started", reference=request["decision_ref"],
                    conditions=[], turn_id=None, turn_uncertain=True)
        if isinstance(scope, AcceptedScope):
            task["accepted_identity"] = _accepted_identity(scope.accepted_identity)
        admission = _capability(scope, task, 'task-start', attempt_ref=request.get('attempt_ref'))
        if admission.mode == 'qualification':
            task['qualification_attempt'] = _canonical_plain(admission.attempt_ref)
        _save(db, scope_id, task)
        db.execute("insert or replace into bindings values(?,?,?,?)", (scope_id, request["client"], request["session_id"], request["task_id"]))
        return _status(db, scope, request["client"], request["session_id"])


def _condition(value, task, previous):
    _fields(value, ("id", "kind", "action", "reason", "reference"), ("continuation",))
    for field in ("id", "reason", "reference"):
        _text(value[field], "condition " + field, 128 if field == "id" else 1024)
    if value["kind"] not in ("blocker", "decision", "runnable") or value["action"] not in ("patch", "task"):
        raise StateError("Invalid condition kind or affected action")
    condition = dict(value, status=previous.get("status", "pending") if previous else "pending")
    if previous and previous.get("resolution_ref"):
        condition["resolution_ref"] = previous["resolution_ref"]
    if previous and (value["kind"], value["action"]) != (previous["kind"], previous["action"]):
        raise StateError("A stable condition cannot change kind or action")
    old = previous.get("continuation") if previous else None
    if "continuation" in value:
        continuation = value["continuation"]
        _fields(continuation, ("turn_id", "budget"))
        _text(continuation["turn_id"], "continuation turn_id")
        if type(continuation["budget"]) is not int or continuation["budget"] not in (0, 1):
            raise StateError("Continuation budget must be zero or one")
        if (value["kind"] != "runnable" or task["state"] not in {"WORKING", "REVIEW"} or
                task["turn_id"] != continuation["turn_id"] or task["turn_uncertain"]):
            raise StateError("Continuation requires a runnable condition and observed current task turn")
        if old and (old["turn_id"] != continuation["turn_id"] or continuation["budget"] > old["budget"]):
            raise StateError("A stable condition cannot change turns or increase its continuation budget")
        condition["continuation"] = dict(continuation, consumed=old["consumed"] if old else 0,
                                         revoked=old["revoked"] if old else False)
    elif old:
        condition["continuation"] = old
    return condition


def update_task(scope, request):
    _fields(request, ("client", "session_id", "task_id", "scope_ref", "expected_revision", "state", "reason", "reference"),
            ("conditions", "resolutions", "resolution_ref", "milestones", "user_attention"))
    _identity(request)
    _integer(request["expected_revision"], "expected_revision", 1)
    for field in ("reason", "reference"):
        _text(request[field], field)
    if "resolution_ref" in request:
        _text(request["resolution_ref"], "resolution_ref")
    if not isinstance(request["state"], str) or request["state"] not in STATES - {"DONE"}:
        retired = isinstance(request["state"], str) and request["state"] in RETIRED_STATES
        raise StateError(f"Task status {request['state']} was retired; use {'WORKING' if request['state'] == 'READY' else 'WAITING'}"
                         if retired else "Only completion evidence verification can declare DONE")
    user_attention = request.get('user_attention', False)
    if type(user_attention) is not bool or (user_attention and request['state'] not in ATTENTION_STATES):
        raise StateError('Title attention requires a boolean and an eligible task state')
    for field in ("conditions", "resolutions"):
        if field in request and (not isinstance(request[field], list) or len(request[field]) > 64):
            raise StateError("Task conditions and resolutions must be bounded lists")
    with _database(scope, write=True) as db:
        if db is None:
            raise StateError("Task state has not been initialized")
        scope_id = _scope_id(scope)
        task = _task(db, scope_id, request["client"], request["session_id"])
        if task is None or task["task_id"] != request["task_id"] or task["scope_ref"] != request["scope_ref"]:
            raise StateError("Task update does not match the current binding")
        _compatible(scope, task)
        if task["revision"] != request["expected_revision"]:
            raise StateError("Task revision conflict")
        old_state, state = task["state"], request["state"]
        allowed = (old_state == state or state in {'WAITING','PAUSED','CANCELLED'} or
            (old_state,state) in {('TRIAGE','WORKING'),('TRIAGE','REVIEW'),('WORKING','REVIEW'),('REVIEW','WORKING'),
                ('DONE','WORKING'),('DONE','REVIEW')} or
            (old_state in {'WAITING','PAUSED'} and state in {'TRIAGE','WORKING','REVIEW'} and 'resolution_ref' in request))
        if old_state == 'CANCELLED' or not allowed:
            raise StateError("Task state transition is not allowed")
        task.update(state=state, reason=request["reason"], reference=request["reference"])
        if 'user_attention' in request and state in ATTENTION_STATES:
            task['user_attention'] = user_attention
        else:
            task.pop('user_attention', None)
        if 'milestones' in request:task['milestones']=_milestones(request['milestones'])
        if "resolution_ref" in request:
            task["resolution_ref"] = request["resolution_ref"]
        conditions = {condition["id"]: condition for condition in task["conditions"]}
        seen = set()
        for value in request.get("conditions", []):
            condition = _condition(value, task, conditions.get(value.get("id")) if isinstance(value, dict) and isinstance(value.get("id"), str) else None)
            if condition["id"] in seen:
                raise StateError("Duplicate condition identity")
            seen.add(condition["id"])
            conditions[condition["id"]] = condition
        if len(conditions) > 64:
            raise StateError("Task condition history exceeds its bound")
        seen = set()
        for resolution in request.get("resolutions", []):
            _fields(resolution, ("id", "reference"))
            _text(resolution["id"], "resolution id", 128)
            _text(resolution["reference"], "resolution reference")
            if resolution["id"] not in conditions or resolution["id"] in seen:
                raise StateError("Resolution must identify an existing condition once")
            seen.add(resolution["id"])
            conditions[resolution["id"]].update(status="resolved", resolution_ref=resolution["reference"])
        task["conditions"] = list(conditions.values())
        if state in {'WAITING','PAUSED','CANCELLED'}:
            for condition in task["conditions"]:
                if "continuation" in condition:
                    condition["continuation"]["revoked"] = True
        task["revision"] += 1
        _save(db, scope_id, task)
        return _status(db, scope, request["client"], request["session_id"])



def _unbound_patch_conflict(db, root, context, *, deadline=None):
    from runtime import delegation
    rows = db.execute("select t.scope_id,t.client,t.session_id,t.task_id from tasks t join checkouts c "
        "on c.scope_id=t.scope_id where c.repository_root=? and "
        "json_extract(t.data,'$.mode')='editable' and "
        "json_extract(t.data,'$.lifecycle.safe_closed_at') is null limit 4097", (str(root),)).fetchall()
    if len(rows) > 4096:
        raise StateError('Existing writer inspection exceeds its bound')
    uncertain = False
    for task_scope, client, session, task_id in rows:
        if deadline is not None and time.monotonic() >= deadline:
            raise StateError('Writer observation deadline exceeded')
        try:
            task = _task(db, task_scope, client, session, task_id)
            if task is not None and not task.get('retirement'):
                return 'An existing managed editable task retains this checkout; reconcile its original writer'
        except (StateError, KeyError, TypeError):
            uncertain = True
    claim = delegation._claims([dict(kind='path', key=str(root), mode='write')])[0]
    assignments = db.execute("select id from assignments where coalesce(json_extract(data,'$.status'),'')!='settled' limit 4097").fetchall()
    if len(assignments) > 4096:
        raise StateError('Existing writer inspection exceeds its bound')
    for assignment_id, in assignments:
        if deadline is not None and time.monotonic() >= deadline:
            raise StateError('Writer observation deadline exceeded')
        try:
            for record in delegation._ledger(None, db, context=context, assignment_id=assignment_id):
                for held in record['data']['claims']:
                    current = delegation._claims([dict(kind=held['kind'], key=held['path'], mode=held['mode'])])[0] if 'path' in held else held
                    if any(delegation._overlap(claim, candidate) and
                           (claim['mode'] == 'write' or candidate['mode'] == 'write') for candidate in (held, current)):
                        return 'An overlapping managed writer remains unresolved; reconcile its original assignment'
        except (StateError, KeyError, TypeError):
            uncertain = True
    if uncertain:
        raise StateError('Some existing writer ownership could not be observed')
    return None


def retained_patch_guard(event, *, deadline=None):
    """Protect selected local writers when current managed activation is unavailable."""
    if not guarded_mutation(event):
        return None
    from runtime import local_ownership, evidence
    try:
        owner = local_ownership.acquire(event.cwd, deadline=deadline)
    except (StartupError, OSError, ValueError, TypeError, KeyError):
        return None
    if not os.path.lexists(owner.database):
        return None
    selected = False
    try:
        targets = local_ownership.targets(owner, deadline=deadline)
        remaining = max(0, min(0.1, deadline - time.monotonic())) if deadline is not None else 0.1
        with closing(sqlite3.connect(owner.database.as_uri() + '?mode=ro', uri=True, timeout=remaining)) as db:
            if deadline is not None:
                db.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            db.execute('pragma trusted_schema=off')
            db.execute('pragma query_only=on')
            db.execute('begin')
            authority = dict(directory=str(owner.state_dir), database=str(owner.database),
                directory_binding=targets['directory'], database_binding=targets['database'])
            context = evidence._existing_authority_context(authority, db=db)
            row = db.execute('select * from checkouts where scope_id=?', (owner.scope_id,)).fetchone()
            physical = _physical_identity(_canonical_plain(owner.accepted_identity))
            if (row is None or row[:4] != (owner.scope_id, physical['project_id'],
                    str(owner.repository_root), str(owner.git_common_dir)) or json_object(row[5]) != physical):
                raise StateError('Retained checkout differs from its physical owner')
            selected = db.execute('select 1 from bindings where scope_id=? and client=? and session_id in (?,?)',
                (owner.scope_id, event.client, event.session_id, event.hook_parent_session_id)).fetchone() is not None
            reason = ('The selected managed task requires current authority; inspect its original task status'
                if selected else _unbound_patch_conflict(db, owner.repository_root, context, deadline=deadline))
            selected = selected or reason is not None
            if local_ownership.targets(owner, deadline=deadline) != targets:
                raise StateError('Existing managed writer identity changed')
            evidence._check_context(context, db)
            return HookResult(event.kind, 'deny', reason) if reason else None
    except (StartupError, OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return HookResult(event.kind, 'deny' if selected else 'context',
            'Existing managed ownership could not be observed; advanced execution remains unavailable')


def process_event(scope, event, *, deadline=None):
    """Evaluate selected managed tasks; ordinary hooks never require or create one."""
    task = None
    selected = False
    budget = dict(paths=0, bytes=0, deadline=deadline) if deadline is not None else None
    try:
        if isinstance(scope, AcceptedScope): _scope_version(scope)
        if scope.runtime is None:
            return HookResult(event.kind)
        _require_claude_scope(scope, event.client)
        if isinstance(scope, AcceptedScope) and event.client != scope.client:
            raise StateError("Event client differs from accepted scope")
        with _database(scope, write=True, deadline=deadline) as db:
            if db is None:
                raise StateError("Task state has not been initialized")
            scope_id = _scope_id(scope)
            if event.hook_parent_session_id is not None:
                parent = _task(db, scope_id, event.client, event.hook_parent_session_id)
                if (parent is not None and 'qualification_attempt' not in parent
                        and _task(db, scope_id, event.client, event.session_id) is None):
                    # Ordinary native hooks retain the shared session's existing task authority.
                    event = replace(event, session_id=event.hook_parent_session_id, hook_parent_session_id=None)
            retired_event=False
            event_identity = dict(scope_id=scope_id, client=event.client, session_id=event.session_id, turn_id=event.turn_id)
            if event.turn_id is not None:
                if _retired(db, "turn", event_identity):
                    retired_event=True
                    if event.kind!="PostToolUse":raise StateError("Retired event cannot be rebound")
                if event.tool_call_id is not None:
                    receipt_identity = event_identity | dict(tool_call_id=event.tool_call_id, kind=event.kind)
                    if _retired(db, "receipt", receipt_identity):
                        retired_event=True
                        if event.kind!="PostToolUse":raise StateError("Retired receipt cannot be replayed as new work")
            _checkout_row(db, scope)
            if guarded_mutation(event):
                selected = db.execute('select 1 from bindings where scope_id=? and client=? and session_id=?',
                    (scope_id, event.client, event.session_id)).fetchone() is not None
            task = _task(db, scope_id, event.client, event.session_id)
            child_patch = None
            qualification = isinstance(scope, AcceptedScope) and scope.qualification is not None
            if guarded_mutation(event) and task is None:
                from runtime import delegation, evidence
                conflict = _unbound_patch_conflict(db, scope.repository_root, evidence._evidence_context(scope), deadline=deadline)
                if conflict:
                    selected = True
                    if qualification:
                        child_patch = delegation._qualified_child_patch(scope, db, event, budget=budget)
                    if child_patch is None:
                        return HookResult(event.kind, 'deny', conflict)
                    event = replace(event, receipt_fingerprint=child_patch)
            if budget is not None: _entry_budget_check(budget)
            _compatible(scope, task, budget=budget)
            if budget is not None: _entry_budget_check(budget)
            closed=task is not None and task.get("lifecycle",{}).get("safe_closed_at") is not None
            if closed and event.kind!="PostToolUse":_assert_open(task)
            identity = (scope_id, event.client, event.session_id, event.turn_id)
            row = db.execute("select task_id,continuation_consumed from turns where scope_id=? and client=? and session_id=? and turn_id=?", identity).fetchone() if event.turn_id else None
            receipt_identity = (*identity, event.tool_call_id, event.kind)
            receipt = db.execute("select fingerprint,conflicted from receipts where scope_id=? and client=? and session_id=? and turn_id=? and tool_call_id=? and kind=?", receipt_identity).fetchone() if event.turn_id and event.tool_call_id else None
            if closed or retired_event:
                # External completions still invalidate the physical checkout, without resuming a terminal owner.
                result,changes=evaluate(event,dict(task=None,turn=None,receipt={"fingerprint":receipt[0],"conflicted":receipt[1]} if receipt else None))
                if changes['generation_delta']:
                    db.execute('update checkouts set generation=generation+? where scope_id=?',(changes['generation_delta'],scope_id))
                if changes.get('poison_receipt'):
                    db.execute('update receipts set conflicted=1 where scope_id=? and client=? and session_id=? and turn_id=? and tool_call_id=? and kind=?',receipt_identity)
                return HookResult(event.kind,'context','Terminal task remains closed; external completion freshness was observed')
            if budget is not None: _entry_budget_check(budget)
            child_post_unbound = False
            if task is None and qualification and event.kind == 'PostToolUse' and event.receipt_fingerprint is not None:
                pre = db.execute("select fingerprint,conflicted from receipts where scope_id=? and client=? "
                    "and session_id=? and turn_id=? and tool_call_id=? and kind='PreToolUse'",
                    (*identity, event.tool_call_id)).fetchone()
                from runtime import delegation
                if pre is not None and pre[0].startswith(delegation._CHILD_PATCH_PRE_PREFIX):
                    post = delegation._child_patch_fingerprint(pre[0], event.receipt_fingerprint)
                    # Exact replay retains its original binding even after the writer stops.
                    admitted = pre[1] == 0 and event.tool_name == 'apply_patch' and receipt == (post, 0)
                    if not admitted and pre[1] == 0 and event.tool_name == 'apply_patch':
                        try:
                            admitted = delegation._qualified_child_patch(scope, db, event, budget=budget,
                                pre_fingerprint=pre[0]) == pre[0]
                        except (StartupError, KeyError, TypeError):
                            pass
                    if admitted:
                        event = replace(event, receipt_fingerprint=post)
                    else:
                        child_post_unbound = True
            result, changes = evaluate(event, dict(task=task, turn={"task_id": row[0], "continuation_consumed": row[1]} if row else None,
                                                   receipt={"fingerprint": receipt[0], "conflicted": receipt[1]} if receipt else None))
            if guarded_mutation(event) and child_patch is not None and changes.get('poison_receipt'):
                result = HookResult(event.kind, 'deny', 'Child patch receipt conflicts with its original assignment')
            if child_post_unbound:
                if "receipt" in changes or receipt is not None:
                    changes["poison_receipt"] = True
                    if receipt is not None and not receipt[1]:
                        changes["generation_delta"] = 1
                result = HookResult(event.kind, 'context', 'Child patch completion has no exact admitted pre-tool receipt')
            if guarded_mutation(event) and task is not None and result.action != 'deny':
                admission = _capability(scope, task, 'task-patch')
                from runtime import delegation
                delegation._qualification_event_allocation(scope, db, task, admission, event)
            if result.action == 'continue':
                admission = _capability(scope, task, 'task-continue')
                from runtime import delegation
                delegation._qualification_event_allocation(scope, db, task, admission, event)
            if budget is not None: _entry_budget_check(budget)
            if changes["generation_delta"]:
                _checkout_row(db, scope, initialize=True)
                db.execute("update checkouts set generation=generation+? where scope_id=?", (changes["generation_delta"], scope_id))
            if "receipt" in changes:
                db.execute("insert into receipts values(?,?,?,?,?,?,?,0)", (*receipt_identity, changes["receipt"]))
            if changes.get("poison_receipt"):
                db.execute("update receipts set conflicted=1 where scope_id=? and client=? and session_id=? and turn_id=? and tool_call_id=? and kind=?", receipt_identity)
            if "turn" in changes:
                db.execute("insert into turns values(?,?,?,?,?,0)", (*identity, changes["turn"]["task_id"]))
            if changes.get("consume_turn"):
                db.execute("update turns set continuation_consumed=1 where scope_id=? and client=? and session_id=? and turn_id=?", identity)
            if "task" in changes:
                _save(db, scope_id, changes["task"])
            return result
    except (StartupError, StateError, KeyError, TypeError):
        if guarded_mutation(event) and (selected or task is not None):
            reason = ("Runtime state is missing, unsafe or incompatible; inspect task status"
                      if event.client == "codex" else
                      "Runtime state is missing, unsafe or incompatible; inspect the bound task")
            return HookResult(event.kind, "deny", reason)
        retained = retained_patch_guard(event, deadline=deadline)
        if retained is not None:
            return retained
        if event.kind == "Stop":
            return HookResult(event.kind)
        return HookResult(event.kind, "context", "Runtime state is unavailable; event processing and coverage are unverified")


def retire_task(scope, request):
    """Cancel one exact historical writer using retained host reconciliation evidence.

    Like D4, this binds coordinator-verified host proof; local JSON cannot
    authenticate host/operator authority or create success evidence.
    """
    _fields(request, ('client', 'session_id', 'task_id', 'scope_ref', 'expected_revision', 'expected_pins',
                      'reason', 'reference', 'stop_proof'))
    _identity(request)
    _integer(request['expected_revision'], 'expected_revision', 1)
    for field in ('reason', 'reference'):
        _text(request[field], field)
    _validate_pins(request['expected_pins'])
    from runtime.evidence import _artifact, _artifact_owner, _read_host_proof
    proof = _read_host_proof(scope, request['stop_proof'])
    _fields(proof, ('version', 'kind', 'identity', 'expected_revision', 'expected_pins', 'observed_turn_id',
                    'stopped', 'descendants_stopped', 'attempt_ids', 'observation_ref', 'coordinator_ref'))
    for field in ('observation_ref', 'coordinator_ref'):
        _text(proof[field], 'host proof ' + field)
    if (type(proof['version']) is not int or proof['version'] != 1 or proof['kind'] != 'stopped-writer-reconciliation' or
            proof['stopped'] is not True or proof['descendants_stopped'] is not True):
        raise StateError('Trusted stopped-writer and descendant reconciliation remains missing')
    with _database(scope, write=True) as db:
        if db is None:
            raise StateError('Task state has not been initialized')
        scope_id = _scope_id(scope)
        task = _task(db, scope_id, request['client'], request['session_id'], request['task_id'])
        identity = {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')}
        if (identity != {key: request[key] for key in identity} or task['revision'] != request['expected_revision'] or
                task['pins'] != request['expected_pins'] or task['pins']['schema_version'] != _scope_version(scope) or
                'retirement' in task):
            raise StateError('Retirement requires exact supported stored identity, pins and revision')
        _guard_assignments(db, scope, request['client'], request['session_id'])
        attempts = [a['id'] for a in task.get('evidence', {}).get('attempts', []) if a['status'] in {'reserved', 'running', 'uncertain'}]
        if (proof['identity'] != identity or proof['expected_revision'] != task['revision'] or proof['expected_pins'] != task['pins'] or
                proof['observed_turn_id'] != task['turn_id'] or proof['attempt_ids'] != attempts):
            raise StateError('Stopped-writer proof does not match exact historical task and attempts')
        _compatible(scope, task)
        retained_proof = _artifact(scope, proof, owner=_artifact_owner(scope, task), db=db)
        task.update(state='CANCELLED', reason=request['reason'], reference=request['reference'],
                    retirement=dict(proof_digest=request['stop_proof']['sha256'], proof=retained_proof, observation_ref=proof['observation_ref'],
                                    coordinator_ref=proof['coordinator_ref'], previous_revision=task['revision']))
        for condition in task['conditions']:
            if 'continuation' in condition:
                condition['continuation']['revoked'] = True
        task['revision'] += 1
        _save(db, scope_id, task)
    return dict(ok=True, task=task, limitations=['Cancellation only; host-proof authority is coordinator-verified, not authenticated by local JSON'])


def _milestones(values):
    if not isinstance(values,list) or len(values)>64:
        raise StateError('Task milestones must be a bounded list')
    seen=set()
    for value in values:
        _fields(value,('id','weight','accepted','reference'))
        _text(value['id'],'milestone id',128);_text(value['reference'],'milestone acceptance reference',1024)
        if (value['id'] in seen or type(value['weight']) is not int or not 1<=value['weight']<=1000000
                or type(value['accepted']) is not bool):
            raise StateError('Invalid task milestone identity, weight or acceptance')
        seen.add(value['id'])
    return values
