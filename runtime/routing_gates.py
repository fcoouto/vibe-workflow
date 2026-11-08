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

    `view` resolves the checkout (slow) and reads one consistent snapshot; `etag` reuses that resolution and
    reads only the database change counter and the Workstreams setting digest, so it is cheap enough to run
    inside the ledger transaction."""
    def __init__(self, resolve=None):
        self._resolve = resolve or _resolve_scope
        self._scopes = {}

    @staticmethod
    def _key(locator):
        return json.dumps(locator, sort_keys=True)

    def view(self, locator):
        from runtime import delegation
        scope = self._resolve(locator)
        self._scopes[self._key(locator)] = scope
        return delegation.routing_view(scope, locator)

    def etag(self, locator):
        from runtime import delegation
        scope = self._scopes.get(self._key(locator))
        if scope is None:
            raise LookupError('etag requested before a view')
        return delegation.routing_etag(scope)
