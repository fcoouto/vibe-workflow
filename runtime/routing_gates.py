"""Smart Routing fresh gates at dispatch (S3.5, RF3): the admission's workflow binding and its re-validation.

`prepare` reads the canonical workflow job once and stores a minimal binding in the admission record; `dispatch`
re-reads current workflow state and refuses when the binding no longer holds. The contract (binding fields, reason
codes, the snapshot/etag rule and the `job: null` path) is defined once in the plan, Task S3.5
(docs/superpowers/plans/2026-11-08-smart-routing.md#task-s35-fresh-gates-at-dispatch-rf3).

A reader is any object with `view(locator) -> view` and `etag(locator) -> str | None`, both read-only. `view` may
be slow and always runs before the ledger's write transaction; `etag` must be cheap, because it is the only
workflow read made while that transaction holds the ledger lock. `None` from `etag` means "busy": treated as a
change. The production reader is `D6Workflow`; requests can never supply one.
"""
import hashlib
import json
from pathlib import Path
import re

from runtime.usage_budget import MAX_BINDING_BYTES

BINDING_VERSION = 1
# Rounds of (view before the transaction, etag inside it) before a still-changing workflow is refused.
ROUNDS = 3
LOCATOR_LIMITS = dict(client=32, cwd=1024, session_id=256, task_id=128, job_id=128, attempt_id=128)
LOCATOR_REQUIRED = ('client', 'cwd', 'session_id', 'task_id', 'job_id')
MAX_ROLE = 32
MAX_ETAG = 512
MAX_DEPENDENCIES = 4096
_HEX = re.compile(r'[0-9a-f]{64}')
VIEW_FIELDS = ('etag', 'job', 'dependencies', 'ownership', 'resource')
JOB_FIELDS = ('locator', 'role', 'revision', 'semantic_digest', 'dependencies', 'ownership', 'resource')


class WorkflowChanged(Exception):
    """Workflow state committed a change while a view was being read; the caller retries or refuses."""


class ProjectMismatch(Exception):
    """The locator resolved to a checkout other than the pinned project (`project_mismatch`)."""


# The serialized size check uses a placeholder credential marker, the largest the stored binding can carry.
_CREDENTIAL_PLACEHOLDER = '0' * 64
MAX_WATCHES = 64


def _hex(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _text(value, limit):
    return type(value) is str and 0 < len(value) <= limit and value.isprintable()


def _count(value):
    return type(value) is int and 0 <= value <= MAX_DEPENDENCIES


def digest(value):
    """SHA-256 of canonical JSON."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def valid_locator(value):
    """Where the canonical job lives: checkout (client, absolute cwd), root task identity, job and optional
    existing delegation assignment (`attempt_id`). Bounded so the binding fits its allowance."""
    if type(value) is not dict or not set(LOCATOR_REQUIRED) <= set(value) <= set(LOCATOR_LIMITS):
        return False
    if not all(_text(value[key], LOCATOR_LIMITS[key]) for key in value):
        return False
    return value['cwd'].startswith('/')


def valid_view(view):
    """Shape of a reader's view; anything else (extra keys, unbounded or malformed values) is unusable."""
    if type(view) is not dict or set(view) != set(VIEW_FIELDS) or not _text(view['etag'], MAX_ETAG):
        return False
    job = view['job']
    if job is None:
        return all(view[key] is None for key in VIEW_FIELDS[2:])
    if type(job) is not dict or set(job) != {'role', 'revision', 'semantic_digest', 'open'} or \
            not _text(job['role'], MAX_ROLE) or not _hex(job['revision']) or not _hex(job['semantic_digest']) or \
            type(job['open']) is not bool:
        return False
    deps, owner, resource = view['dependencies'], view['ownership'], view['resource']
    if type(deps) is not dict or set(deps) != {'accepted', 'digest', 'count'} or type(deps['accepted']) is not bool \
            or not _count(deps['count']) or not (_hex(deps['digest']) if deps['accepted'] else deps['digest'] is None):
        return False
    if type(owner) is not dict or set(owner) != {'digest', 'conflict'} or not _hex(owner['digest']) or \
            type(owner['conflict']) is not bool:
        return False
    return resource is None or (type(resource) is dict and set(resource) == {'marker', 'current'}
                                and _hex(resource['marker']) and type(resource['current']) is bool)


def readiness(view, semantic_digest):
    """Why `prepare` may not bind this job now, as (state, reason), or None when it is ready.

    Waiting conditions (unready dependency, a conflicting writer, a paused task, unavailable resource
    authority) are `pending`; a missing job or one whose semantics differ from the request is `blocked`."""
    job = view['job']
    if job is None:
        return 'blocked', 'job_not_found'
    if not job['open']:
        return 'pending', 'job_not_ready'
    if job['semantic_digest'] != semantic_digest:
        return 'blocked', 'binding_stale'
    if not view['dependencies']['accepted']:
        return 'pending', 'dependency_not_ready'
    if view['ownership']['conflict']:
        return 'pending', 'ownership_conflict'
    if view['resource'] is not None and not view['resource']['current']:
        return 'pending', 'resource_authority_unavailable'
    return None


def job_binding(locator, view):
    """The job part of a binding: identifiers, revisions and digests only, never briefs, prompts or source."""
    job, deps, resource = view['job'], view['dependencies'], view['resource']
    return dict(locator=dict(locator), role=job['role'], revision=job['revision'],
                semantic_digest=job['semantic_digest'], dependencies=dict(count=deps['count'], digest=deps['digest']),
                ownership=view['ownership']['digest'], resource=None if resource is None else resource['marker'])


def valid_job_binding(value):
    if value is None:
        return True
    if type(value) is not dict or set(value) != set(JOB_FIELDS) or not valid_locator(value['locator']):
        return False
    deps = value['dependencies']
    return (_text(value['role'], MAX_ROLE) and all(_hex(value[key]) for key in ('revision', 'semantic_digest',
                                                                                    'ownership'))
            and (value['resource'] is None or _hex(value['resource']))
            and type(deps) is dict and set(deps) == {'count', 'digest'} and _count(deps['count']) and _hex(deps['digest']))


def binding(job, credential):
    return dict(v=BINDING_VERSION, job=job, credential=credential)


def binding_fits(job):
    """Whether a job binding still fits MAX_BINDING_BYTES once serialized with the largest credential marker.
    Character limits alone do not bound bytes: non-ASCII text serializes as six-byte escapes."""
    return valid_job_binding(job) and valid_binding(binding(job, None if job is None else _CREDENTIAL_PLACEHOLDER))


def valid_binding(value):
    """A stored binding: version, job part (or None) and credential marker (or None), within MAX_BINDING_BYTES."""
    if type(value) is not dict or set(value) != {'v', 'job', 'credential'} or value['v'] != BINDING_VERSION:
        return False
    if not valid_job_binding(value['job']) or not (value['credential'] is None or _hex(value['credential'])):
        return False
    return len(json.dumps(value, separators=(',', ':'))) <= MAX_BINDING_BYTES


def verdict(job, view):
    """The fixed reason current workflow state refuses a bound dispatch, or None. Pure comparison."""
    current = view['job']
    if current is None or not current['open'] or current['role'] != job['role'] or \
            current['revision'] != job['revision'] or current['semantic_digest'] != job['semantic_digest']:
        return 'binding_stale'
    deps = view['dependencies']
    if not deps['accepted'] or deps['digest'] != job['dependencies']['digest'] or \
            deps['count'] != job['dependencies']['count']:
        return 'dependency_reopened'
    if view['ownership']['conflict'] or view['ownership']['digest'] != job['ownership']:
        return 'ownership_changed'
    resource = view['resource']
    if (resource is None) != (job['resource'] is None) or \
            (resource is not None and (not resource['current'] or resource['marker'] != job['resource'])):
        return 'resource_authority_lost'
    return None


def _resolve_scope(locator):
    """The enrolled checkout scope for a locator, through the same entry path workflow commands use."""
    from types import SimpleNamespace
    from runtime import state
    from runtime.startup import _entry_scope, acquire_project_entry
    acquisition = acquire_project_entry(SimpleNamespace(client=locator['client'], cwd=Path(locator['cwd'])),
                                        state.PACKAGE)
    scope = _entry_scope(acquisition)
    if scope is None or scope.runtime is None:
        raise LookupError('workflow scope unavailable')
    return scope


class D6Workflow:
    """Reads the canonical delegation state (runtime/delegation.py) for a locator; never writes it.

    `view` resolves the checkout (slow), opens a fresh `state.ChangeWatch` (one read-only SQLite connection; no
    raw descriptor, so no lock this process holds on the database is ever dropped) and reads one consistent
    snapshot. `etag` reuses that watch: a `PRAGMA data_version` query plus the Workstreams setting digest, cheap
    enough to run inside the ledger transaction. At most MAX_WATCHES watches are kept; closing one is SQLite's
    own close, which keeps its descriptor open while other connections of this process hold locks.

    `project` (the command's `--project`) pins the checkout: after resolving a locator, its checkout root
    (realpath) must equal the realpath of `project`, at `prepare` and again at every `dispatch`, else
    `ProjectMismatch` before any workflow read. Comparing the resolved root, not the request's `cwd`, refuses a
    nested separately enrolled checkout under the pinned root and a `cwd` symlink swapped after the request.
    Paths compare byte-for-byte: on a case-insensitive filesystem a differently cased spelling of the same root
    is refused too (fails closed)."""
    def __init__(self, resolve=None, project=None):
        self._resolve = resolve or _resolve_scope
        self.project = None if project is None else Path(project).resolve()
        self._watches = {}

    @staticmethod
    def _key(locator):
        return json.dumps(locator, sort_keys=True)

    def _forget(self, key):
        entry = self._watches.pop(key, None)
        if entry is not None:
            entry[1].close()

    def view(self, locator):
        from runtime import delegation
        key = self._key(locator)
        scope = self._resolve(locator)
        self._forget(key)
        if self.project is not None and Path(scope.repository_root).resolve() != self.project:
            raise ProjectMismatch('locator resolves outside the pinned project')
        while len(self._watches) >= MAX_WATCHES:
            self._forget(next(iter(self._watches)))
        self._watches[key] = (scope, delegation.routing_watch(scope))
        return delegation.routing_view(scope, locator, self._watches[key][1])

    def etag(self, locator):
        from runtime import delegation
        entry = self._watches.get(self._key(locator))
        if entry is None:
            raise LookupError('etag requested before a view')
        return delegation.routing_etag(*entry)

    def close(self):
        for key in list(self._watches):
            self._forget(key)
