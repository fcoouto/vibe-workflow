"""Host-neutral launcher core for Smart Routing managed launches (C.3 design 3.1 steps 1-6, 3.4, 3.6, 4.2).

A launcher pins the project, runs the `status` preflight, binds planned work, profiles from canonical data, never
supplies observations (the routing service collects them), validates the admitted strategy against the host's
catalog and the entitlement rule, and dispatches immediately before the host creates anything. Hosts plug in a
catalog and a transport; the dispatch token stays in memory and reaches only the transport. Nothing here calls an
operator op, `set managed_projects`, a model, the network or a secret store.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from runtime import routing, routing_observations, routing_settings

# Fixed refusal codes this slice defines (C.3 design 3.6); `project_required` is the routing service's own code.
PROJECT_REQUIRED = 'project_required'
AUTHORITY_NOT_REQUIRED = 'authority_not_required'
UNSTAMPED_HOLDS_OPEN = 'unstamped_holds_open'
LEDGER_RECONCILE_PENDING = 'ledger_reconcile_pending'
PROJECT_NOT_MANAGED = 'project_not_managed'
WORKFLOW_REQUIRED = 'workflow_required'
CATALOG_MISMATCH = 'catalog_mismatch'
ROUTE_NOT_ENTITLED = 'route_not_entitled'
DOLLAR_ROUTE_NEEDS_ENFORCED_HOST = 'dollar_route_needs_enforced_host'

# host -> (provider, harness); a None provider comes from the host's own request.
HOSTS = dict(codex_native=('openai', 'codex-native'), openrouter_proposal=('openrouter', 'openrouter-proposal'),
             openrouter_worker=('openrouter', 'opencode'), opencode=(None, 'opencode'), api_usage=('openai', 'api'))
PINNED_ENDPOINT = ('openrouter_proposal', 'openrouter_worker')  # the request's `provider` is the pinned endpoint
# Advisory hosts cannot block a send before it happens, so they never get a dollar route (C.3 design 3.7).
ADVISORY_HOSTS = ('codex_native',)
# Routing assignment role -> the canonical job role routing profiles (routing_settings.PROFILE_ROLES, reversed).
PROFILE_ROLE = dict(implementer='worker', researcher='worker', repair='worker', reviewer='review')
CONSERVATIVE_FLOORS = dict(complexity_band='high', risk_band='high', review_policy='independent')


class Refused(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# -- identities and canonical requests ---------------------------------------------------------------------------
def route_identity(host, **fields):
    """`{provider, model, effort, harness}` (routing.ROUTE_IDENTITY) for a host's own fields. A missing effort is
    `none`; unknown fields are ignored."""
    if host not in HOSTS:
        raise ValueError('unknown_host')
    provider, harness = HOSTS[host]
    effort = fields.get('effort')
    value = dict(provider=fields.get('provider') if provider is None else provider, model=fields.get('model'),
                 effort='none' if effort is None else effort, harness=harness)
    if not all(routing._text(item) for item in value.values()):
        raise ValueError('invalid_route_identity')
    return value


def pinned_endpoint(host, **fields):
    """The OpenRouter endpoint a gateway host pins (its request's `provider`), else None."""
    return fields.get('provider') if host in PINNED_ENDPOINT else None


def choice(host, funding, **fields):
    """The only `assignment.choice` shape `routing._valid_assignment` accepts: the route identity plus `funding`
    (`subscription`, or `api` for direct API and gateway routes)."""
    if funding not in routing_settings.FUNDING:
        raise ValueError('invalid_funding')
    return dict(route_identity(host, **fields), funding=funding)


def assignment_scope(checkout):
    """The assignment `scope` credential readiness is compared with: a digest of the checkout's real path."""
    return 'project:' + hashlib.sha256(os.fsencode(os.path.realpath(checkout))).hexdigest()[:32]


def unplanned_job(role, scope_ref, objective, *, tools=None):
    """The minimal canonical job for explicit, unplanned delegation (a proposal or worker): role, scope reference,
    an objective digest (never the text) and conservative requirement floors."""
    if role not in PROFILE_ROLE:
        raise ValueError('invalid_role')
    digest = hashlib.sha256(str(objective).encode()).hexdigest()
    return dict(id='unplanned:' + digest[:16], role=PROFILE_ROLE[role], scope_ref=scope_ref,
                objective='sha256:' + digest, tools=sorted(set(tools or [])),
                requirement_floors=dict(CONSERVATIVE_FLOORS))


def prepare_request(assignment, job, *, workflow=None, constraints=None, planned=False, profile=None):
    """The `prepare` request a launcher sends. It never carries observations; a planned job needs `workflow`."""
    if planned and workflow is None:
        raise Refused(WORKFLOW_REQUIRED)
    if type(job) is not dict:
        raise Refused('profile_unavailable')
    job = copy.deepcopy(job)
    if profile is not None:  # the coordinator's optional profile; nothing else is added to the canonical job
        job['profile'] = dict(job.get('profile') or {}, **profile)
    request = dict(v=1, op='prepare', assignment=copy.deepcopy(assignment), job=job)
    if workflow is not None:
        request['workflow'] = dict(workflow)
    if constraints is not None:
        request['constraints'] = copy.deepcopy(constraints)
    return request


# -- status: preflight, readiness and recovery --------------------------------------------------------------------
def _count(value):
    if type(value) is dict:
        return len(value.get('ids') or []) + (value.get('more') or 0)
    return len(value) if isinstance(value, (list, tuple)) else int(bool(value))


def managed(checkout):
    """Phase W: whether `checkout` is on the routing settings' `managed_projects` (C3-0b's predicate). Its absence
    means "not managed", which fails closed."""
    predicate = getattr(routing_settings, 'managed_project', None)
    if not callable(predicate):
        return False
    try:
        return predicate(Path(checkout).resolve()) is True
    except Exception:  # noqa: BLE001 - an unreadable setting never makes a project managed
        return False


def preflight(status, *, dollar=False):
    """The fixed reason a session may not prepare from this `status` response, or None (C.3 design 3.1 step 2).
    The dollar items apply only when the assignment could use a dollar route."""
    if type(status) is not dict or status.get('state') != 'ok':
        reason = status.get('reason') if type(status) is dict else None
        return reason if routing._text(reason) else 'routing_unavailable'
    if status.get('project_pinned') is not True:
        return PROJECT_REQUIRED
    ledger = status.get('ledger') if type(status.get('ledger')) is dict else {}
    if ledger.get('state') == 'unavailable':
        return ledger.get('reason') if routing._text(ledger.get('reason')) else 'ledger_unavailable'
    if ledger.get('authority_required') is not True:
        return AUTHORITY_NOT_REQUIRED
    if ledger.get('unstamped_open_holds') != 0:
        return UNSTAMPED_HOLDS_OPEN
    if dollar and (_count(ledger.get('blocked_organizations')) or _count(ledger.get('uncertain_dollar'))
                   or _count(ledger.get('cost_unknown')) or ledger.get('reconcile_hint') is not None):
        return LEDGER_RECONCILE_PENDING
    return None


def readiness(status):
    """Enabled (the user's Smart Routing setting) and ready (the preflight) reported separately, never inferred."""
    settings = status.get('settings') if type(status) is dict else None
    enabled = settings.get('smart_routing') if type(settings) is dict else None
    if type(enabled) is not bool:
        return dict(enabled=None, ready=False, reason='settings_unavailable')
    reason = preflight(status)
    return dict(enabled=enabled, ready=reason is None, reason=reason)


def recovery_hints(status):
    """The operator ops (C.3 design 3.5) a `status` response calls for. The launcher prints them, never runs them."""
    ledger = (status or {}).get('ledger') if type(status) is dict else None
    ledger = ledger if type(ledger) is dict else {}
    hints = []
    if _count(ledger.get('uncertain_dollar')):
        hints.append('reconcile_uncertain')
    if _count((ledger.get('reconcile') or {}).get('reconcile_subscription')):
        hints.append('reconcile_subscription')
    if _count(ledger.get('cost_unknown')):
        hints.append('resolve_cost_unknown')
    reasons = set()
    for organization in ledger.get('blocked_organizations') or []:
        reasons |= set((organization or {}).get('reasons') or {})
    if 'overrun' in reasons:
        hints.append('resolve_overrun')
    if reasons & {'over_bound', 'legacy'}:
        hints.append('resolve_block')
    return hints


# -- admitted strategy checks -------------------------------------------------------------------------------------
def admitted_check(admission, *, host, catalog, settings_path=None, source=None):
    """(dispatch facts, None) when the admitted route may be sent on this host, else (None, reason).

    Advisory hosts never send on a dollar route. With a source or settings path, the route must be `qualified` in
    the manifest and carry no `not_entitled` mark (C.3 design 3.4). The admitted identity must be in the host's
    actual catalog: entries are `{provider, model, effort, harness}` as the host lists them."""
    if admission.get('billing_class') in routing.FUNDED and host in ADVISORY_HOSTS:
        return None, DOLLAR_ROUTE_NEEDS_ENFORCED_HOST
    if source is not None or settings_path is not None:
        try:
            listed = {route['route']: route for route in (source or routing_observations.default_source(settings_path))
                      .manifest() if type(route) is dict and route.get('status') == 'qualified'}
            marks = routing_observations.entitlement_marks(settings_path) if settings_path is not None else []
        except (OSError, ValueError, KeyError, TypeError):
            return None, ROUTE_NOT_ENTITLED
        spec = listed.get(admission.get('route'))
        if spec is None or any(mark.get('route') == spec['route'] and mark.get('account') == spec.get('account')
                               for mark in marks):
            return None, ROUTE_NOT_ENTITLED
    admitted = admission.get('route_identity')
    for entry in catalog:
        identity = [entry.get(field) if type(entry) is dict else None for field in routing.ROUTE_IDENTITY]
        if admitted is not None and routing.route_identity(*identity) == admitted:
            provider, model, effort, harness = identity
            return dict(provider=provider, model=model, variant=effort, harness=harness, kind='primary',
                        funding_pool=admission.get('pool')), None
    return None, CATALOG_MISMATCH


# -- the launch -------------------------------------------------------------------------------------------------
def _call(service, request):
    return service.handle(json.dumps(request).encode())


def _record(service, attempt, kind, **extra):
    event = dict(event_id='%s:%s' % (kind, attempt), attempt_id=attempt, kind=kind, **extra)
    return _call(service, dict(v=1, op='record', event=event))


def _blocked(reason, **extra):
    return dict(state='blocked', reason=reason, **extra)


def launch(service, *, host, assignment, job, catalog, transport, workflow=None, constraints=None, planned=False,
           profile=None, dollar=False, settings_path=None, source=None, kind='primary'):
    """Run one managed attempt through the routing service `service` (pinned; in-process `handle`).

    `transport(route, *, authority, session_id, message_id)` performs the host send. It is the only receiver of the
    token, and returns the host's re-read outcome `{kind: 'complete' | 'not_dispatched', usage?}`; raising means the
    launch could not be confirmed, which records `uncertain` and names the operator op. A refusal before `dispatch`
    sends nothing and closes any admitted hold with `not_dispatched`. Pending and blocked answers stop the launch:
    there is no fallback to another selector or route."""
    if getattr(service, 'project', None) is None:
        return _blocked(PROJECT_REQUIRED)
    try:
        request = prepare_request(assignment, job, workflow=workflow, constraints=constraints, planned=planned,
                                  profile=profile)
    except Refused as error:
        return _blocked(error.reason)
    reason = preflight(_call(service, dict(v=1, op='status')), dollar=dollar)
    if reason is not None:
        return _blocked(reason)
    if not managed(service.project):
        return dict(state='unmanaged', reason=PROJECT_NOT_MANAGED)
    prepared = _call(service, request)
    if prepared.get('state') != 'admitted':
        return {key: prepared[key] for key in ('state', 'reason', 'reasons') if key in prepared}
    admission = prepared['admission']
    attempt = admission['attempt_id']
    facts, reason = admitted_check(admission, host=host, catalog=catalog, settings_path=settings_path, source=source)
    if reason is None:
        facts['kind'] = kind
        sent = _call(service, dict(v=1, op='dispatch', attempt_id=attempt, request=facts))
        if sent.get('state') != 'dispatched' or not sent.get('authority'):
            reason = sent.get('reason') if routing._text(sent.get('reason')) else 'dispatch_refused'
    if reason is not None:
        _record(service, attempt, 'not_dispatched')
        return _blocked(reason, attempt_id=attempt)
    try:
        outcome = transport(dict(facts, route=admission['route']), authority=sent.pop('authority'),
                            session_id=sent.get('session_id'), message_id=sent.get('message_id'))
    except Exception:  # noqa: BLE001 - an unconfirmed host launch is never retried with a second dispatch
        _record(service, attempt, 'uncertain')
        recovery = 'reconcile_uncertain' if admission.get('billing_class') in routing.FUNDED else 'reconcile_subscription'
        return dict(state='uncertain', attempt_id=attempt, recovery=recovery)
    outcome = outcome if type(outcome) is dict else {}
    if outcome.get('kind') not in ('complete', 'not_dispatched'):
        _record(service, attempt, 'uncertain')
        return dict(state='uncertain', attempt_id=attempt, recovery='reconcile_subscription'
                    if admission.get('billing_class') not in routing.FUNDED else 'reconcile_uncertain')
    extra = {'usage': outcome['usage']} if outcome['kind'] == 'complete' and 'usage' in outcome else {}
    recorded = _record(service, attempt, outcome['kind'], **extra)
    result = dict(state='recorded' if recorded.get('state') == 'recorded' else 'blocked', attempt_id=attempt,
                  event=outcome['kind'], admission=admission)
    if recorded.get('state') != 'recorded':
        result['reason'] = recorded.get('reason')
    if 'seen' in prepared:
        result['seen'] = prepared['seen']
    return result


# -- activation-gate self-check -----------------------------------------------------------------------------------
class _Unpinned:
    project = None

    def handle(self, raw, op=None):  # pragma: no cover - a pinned launch never reaches it
        raise AssertionError('an unpinned service was called')


def _sandbox(workdir):
    """Create `workdir` (or accept an existing empty owner-only directory) for the self-check's own stores."""
    path = Path(workdir).absolute()
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or any(path.iterdir())):
        return None
    return path.resolve()


def managed_self_check(workdir):
    """C.3 design 4.2 capability "Collector and launcher core": `[]` when it holds, else fixed failure codes.

    Against stores it creates in `workdir` only, with a test source built here in code: the preflight refusals,
    one launch admitted through the collector, and caller observations refused. It opens no default path and makes
    no network, model, host or secret-store call. Until C3-0b's managed-project predicate exists it fails closed."""
    root = _sandbox(workdir)
    if root is None:
        return ['self_check_workdir_unsafe']
    if not callable(getattr(routing_settings, 'managed_project', None)):
        return ['managed_projects_unavailable']
    from scripts import routing as command  # the gate puts the plugin package root first on sys.path
    checkout = root / 'checkout'
    checkout.mkdir(mode=0o700)
    identity = route_identity('codex_native', model='self-check-model', effort='high')
    source = routing_observations.StaticSource(
        [dict(route='self-check', status='qualified', billing_class='included_subscription', pool='self-check',
              account='self-check', demand={'5h': 1}, binding_enforceable=True, **identity)],
        lambda pool, at: [dict(id='5h', limit=1000, used=0, length=18000, reset_at=at + 3600, rolling=False,
                               observed_at=at)])
    saved = routing_settings.managed_project, routing_observations.default_source
    routing_settings.managed_project = lambda path, *args, **kwargs: Path(path).resolve() == checkout
    routing_observations.default_source = lambda *args, **kwargs: source
    settings = root / 'routing' / 'settings.json'
    service = command.Service(settings, root / 'usage' / 'organizations.sqlite3', project=checkout)
    codes, tokens = [], []

    def transport(route, *, authority, session_id, message_id):
        tokens.append(authority)
        return dict(kind='complete')

    def attempt(name, **options):
        options.setdefault('job', unplanned_job('implementer', assignment_scope(checkout), 'managed self-check'))
        return launch(options.pop('service', service), host='codex_native', catalog=[identity], transport=transport,
                      settings_path=settings, assignment=dict(attempt_id=name, task='self-check-' + name,
                                                              scope=assignment_scope(checkout),
                                                              choice=dict(identity, funding='subscription')), **options)
    try:
        if attempt('unpinned', service=_Unpinned()).get('reason') != PROJECT_REQUIRED:
            codes.append('unpinned_launch_not_refused')
        if attempt('no-authority').get('reason') != AUTHORITY_NOT_REQUIRED:
            codes.append('preflight_not_enforced')
        if _call(service, dict(v=1, op='set', authority_required=True)).get('state') != 'updated':
            codes.append('self_check_store_unavailable')
        if attempt('unbound', planned=True).get('reason') != WORKFLOW_REQUIRED:
            codes.append('planned_job_not_bound')
        result = attempt('admitted')
        if result.get('state') != 'recorded' or len(tokens) != 1:
            codes.append('collector_admission_failed')
        elif tokens[0] in json.dumps(result):
            codes.append('token_exposed')
        collected = routing_observations.collect(checkout, dict(scope=assignment_scope(checkout)),
                                                 settings_path=settings, now=int(time.time()))
        offered = dict(prepare_request(dict(attempt_id='offered', task='self-check-offered',
                                            scope=assignment_scope(checkout)),
                                       unplanned_job('implementer', assignment_scope(checkout), 'offered')),
                       observations=collected['observations'])
        if _call(service, offered).get('reason') != 'observations_not_accepted':
            codes.append('caller_observations_accepted')
    except Exception:  # noqa: BLE001 - any failure is a fixed code, never a traceback
        codes.append('self_check_error')
    finally:
        routing_settings.managed_project, routing_observations.default_source = saved
        service.close()
    return codes
