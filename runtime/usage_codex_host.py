"""Foreground Codex adapter. Only the trusted coordinator supplies tool receipts.

This module checks bindings and limits. It does not authenticate copied data,
register a CLI action, change enablement, or initiate retries.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import time

from runtime import usage_admission, usage_budget_money, usage_credentials, usage_routing, usage_settings, worker_visibility
from runtime.usage_budget import BudgetError, fields, integer, name, require
from runtime.usage_canary_host import native_capacity, package_digest

_ADMISSION_WINDOW = 300
_PLAN_WINDOW = 7 * 24 * 60 * 60
_PROPOSALS = 12
_PROPOSAL_FIELDS = 'task_id class_name packet provider local_preferred api_only snapshots'


@dataclass(frozen=True, slots=True)
class Reviewer:
    """A distinct, ready native reviewer observed by the coordinator."""

    coordinator: str
    identity: str


@dataclass(frozen=True, slots=True)
class Price:
    profile_digest: str
    observed_at: int
    valid_until: int


@dataclass(frozen=True, slots=True)
class Plan:
    thread_id: str
    host_id: str
    checkout: str
    scope: str
    package_digest: str
    settings_digest: str
    mode: str
    organization: str
    project: str
    model: str
    reasoning: str
    account: str
    reviewer: str
    pool: str
    maximum_usd_micros: int
    quoted_usd_micros: int
    proposal_digests: tuple[str, ...]
    created_at: int
    expires_at: int


def _thread(snapshot, checkout):
    thread = snapshot['thread']
    require(thread['kind'] == 'codex' and thread['cwd'] == checkout, 'codex_thread_changed')
    return name(thread['id']), name(thread['hostId'])


def _current(snapshot, checkout, now):
    identity = _thread(snapshot, checkout)
    turn = snapshot['turns'][0]
    require(turn['status'] == 'inProgress' and integer(turn['startedAt']) <= now,
            'codex_turn_unavailable')
    name(turn['id'])
    return identity, turn


def _reviewer(receipt, coordinator):
    require(type(receipt) is Reviewer and receipt.coordinator == coordinator
            and name(receipt.identity) != coordinator, 'independent_review_unavailable')
    return receipt.identity


def _settings(control, data):
    config = control._config(data)
    org = data['organizations'][config['binding']['organization']]
    require(not usage_admission.canary_budget_only(config, org['policy']), 'routing_budget_required')
    require('canary_owner' not in config and 'manual_test_owner' not in config, 'live_mode_unqualified')
    require(config['enabled'] is True and config['disabled'] is False
            and config['requested_mode'] in ('manual', 'smart'), 'live_mode_unqualified')
    usage_credentials.validate_binding(config['binding'])
    require(config['org_revision'] == org['revision'], 'policy_revision_changed')
    require(usage_admission.live_consent_current(config, org['policy'], int(time.time())),
            'live_consent_required')
    return config, org['policy']


def _execution_proposal(proposal):
    require(isinstance(proposal, dict), 'invalid_fields')
    return {key: value for key, value in proposal.items() if key != 'visibility'}


def _proposal(control, config, policy, proposal, now):
    fields(proposal, _PROPOSAL_FIELDS + (' external' if isinstance(proposal, dict) and 'external' in proposal else ''))
    name(proposal['task_id'])
    require(proposal['class_name'] == config['class_name'], 'task_class_unqualified')
    require(proposal['provider'] in ('openai-api', 'auto')
            and proposal['local_preferred'] is False and type(proposal['api_only']) is bool,
            'api_route_unavailable')
    control._snapshots(proposal['snapshots'])
    control._external(config, proposal, policy)
    quote = usage_routing.quote(proposal['packet'], config['profile'], now)
    return usage_budget_money.buffered(quote['maximum_usd_micros'] + quote['count_maximum_usd_micros'])  # V (F5.A2)


def _funding_pool(policy, pool):
    require(pool in ('benefit', 'prepaid', 'mixed')
            and (policy['ceiling'] != 'subscription_benefit' or pool == 'benefit'), 'pool_unqualified')


def freeze(cwd, proposals, *, thread_snapshot, native_limits=None, reviewer, expires_at,
           maximum_usd_micros=None, pool='benefit'):
    """Pin the exact proposals and limits without dispatching or writing settings."""
    usage_routing.refuse_managed(cwd)  # first: a managed project never reaches legacy settings (C.3 design 4.3)
    now = int(time.time())
    try:
        with usage_settings.controller(cwd) as control:
            checkout = str(control.root)
            (thread_id, host_id), _ = _current(thread_snapshot, checkout, now)
            reviewer_id = _reviewer(reviewer, thread_id)
            state = control.ledger.snapshot()
            config, policy = _settings(control, state)
            settings_digest = usage_admission.digest([config, policy])
            _funding_pool(policy, pool)
            if maximum_usd_micros is None:
                require(policy['pilot_limit'] is None, 'live_approval_unqualified')
            else:
                usage_admission.validate_maximum(maximum_usd_micros, policy)
            require(policy['start'] <= now < integer(expires_at) <= min(policy['stop_at'], now + _PLAN_WINDOW),
                    'codex_plan_window_invalid')
            require(type(proposals) is list and 1 <= len(proposals) <= _PROPOSALS, 'codex_proposals_invalid')
            proposals = deepcopy(proposals)
            proposals = [_execution_proposal(proposal) for proposal in proposals]
            maximum = sum(_proposal(control, config, policy, proposal, now) for proposal in proposals)
            if maximum_usd_micros is None:
                exposure = control.ledger._total(state, config['binding']['organization']) + maximum
                usage_admission.validate_maximum(exposure, policy)
                maximum_usd_micros = exposure
            require(len({proposal['task_id'] for proposal in proposals}) == len(proposals)
                    and maximum <= maximum_usd_micros, 'codex_proposals_invalid')
            account = native_limits.get('accountId') if isinstance(native_limits, dict) else None
            if not isinstance(account, str) or not account.strip():
                account = thread_id
            return Plan(
                thread_id=thread_id, host_id=host_id, checkout=checkout, scope=control.scope,
                package_digest=package_digest(), settings_digest=settings_digest, mode='smart',
                organization=config['binding']['organization'], project=config['binding']['project'],
                model=config['profile']['model'], reasoning=config['profile']['reasoning'], account=account,
                reviewer=reviewer_id, pool=pool, maximum_usd_micros=maximum_usd_micros,
                quoted_usd_micros=maximum, proposal_digests=tuple(usage_admission.digest(item) for item in proposals),
                created_at=now, expires_at=expires_at)
    except (KeyError, IndexError, TypeError, AttributeError, OverflowError, RecursionError,
            usage_credentials.CredentialError):
        raise BudgetError('invalid_codex_host_evidence') from None


def _task_authorization(plan, proposal, current, config, policy, now):
    identity, _ = _current(current, plan.checkout, now)
    require(identity == (plan.thread_id, plan.host_id), 'codex_thread_changed')
    require(plan.created_at <= now < plan.expires_at
            and usage_admission.live_consent_current(config, policy, now), 'live_consent_required')
    # Preserve the prior automatic identity across refreezes, turns and re-enables.
    key = ['standing', plan.scope, proposal['task_id']]
    return now, 'codex-api-' + usage_admission.digest(key)


def _plan_settings(control, data, plan):
    config, policy = _settings(control, data)
    require((control.scope, str(control.root), plan.mode) == (plan.scope, plan.checkout, 'smart')
            and usage_admission.digest([config, policy]) == plan.settings_digest, 'codex_settings_changed')
    return config, policy


def _publish_generation(plan, task, evidence, result, habblaud, *, visibility):
    run_id = task['run_id']
    if (result.get('run_id') != run_id
            or result.get('accepted') is not False or result.get('requires_native_review') is not True
            or result.get('execution_route') != 'openai-api'
            or type(result.get('reported_cost_usd_micros')) is not int or result.get('cost_basis') != 'estimate'):
        return dict(delivered=False, reason='generation_unconfirmed')
    try:
        with usage_settings.controller(plan.checkout) as control:
            run = control.ledger.snapshot()['runs'].get(run_id)
            generation = run['calls'].get('generation') if run is not None else None
            require(generation is not None
                    and (control.scope, str(control.root)) == (plan.scope, plan.checkout)
                    and run['task'] == task['task_id']
                    and run['consent']['scope'] == plan.scope and run['closed'] is True
                    and run['live_admission']['pin'] == usage_admission.digest(asdict(evidence))
                    and generation['state'] == 'completed'
                    and generation['cost'] is None, 'generation_unconfirmed')  # F5.A2: unpriced until settled
            state = control.ledger.path.parent

        from runtime.habblaud import publish_usage_badge
        return publish_usage_badge(habblaud, model=plan.model, thread_id=plan.thread_id,
            checkout=plan.checkout, run_id=run_id, execution_route=result['execution_route'],
            visibility=visibility, state=state)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, ImportError) as error:
        reason = str(error) if str(error) in ('generation_unconfirmed', 'visibility_parent_unavailable') else 'visibility_unavailable'
        return dict(delivered=False, reason=reason)


def execute(plan, proposal, *, thread_snapshot, reviewer,
            native_limits=None, native_observed_at=None, grant, price,
            habblaud=None, generation_ready=None):
    """Issue exact, short-lived admission from current trusted Codex observations."""
    now = int(time.time())
    require(type(plan) is Plan, 'invalid_codex_host_plan')
    usage_routing.refuse_managed(plan.checkout)  # before the package digest, settings and credential binding
    try:
        require(_reviewer(reviewer, plan.thread_id) == plan.reviewer, 'independent_review_unavailable')
        require(package_digest() == plan.package_digest, 'codex_package_changed')
        supplied = deepcopy(proposal)
        proposal = _execution_proposal(supplied)
        proposal_digest = usage_admission.digest(proposal)
        if proposal_digest not in plan.proposal_digests and 'visibility' in supplied:
            # Preserve the identity of previously frozen plans that included display metadata.
            proposal_digest = usage_admission.digest(supplied)
        require(proposal_digest in plan.proposal_digests, 'codex_proposal_changed')
        with usage_settings.controller(plan.checkout) as control:
            config, policy = _plan_settings(control, control.ledger.snapshot(), plan)
            approved_at, run_id = _task_authorization(
                plan, proposal, thread_snapshot, config, policy, now)
            require(_proposal(control, config, policy, proposal, now) <= plan.maximum_usd_micros, 'live_cap_exceeded')
            _funding_pool(policy, plan.pool)
            require(type(grant) is usage_admission.Grant and grant.pool == plan.pool and integer(grant.balance) > 0,
                    'balance_stale_or_missing')
            profile = config['profile']
            require(type(price) is Price and price == Price(usage_admission.digest(profile),
                    profile['verified_at'], profile['valid_until']), 'pricing_stale')
            native = native_capacity(plan.account)
            approval_id = usage_admission.digest(asdict(plan))
            visibility = worker_visibility.optional_declaration(supplied.get('visibility'))
            task = dict(proposal, run_id=run_id,
                        reviewer_available=True, native=dict(native, task=proposal['task_id']))
            evidence = usage_admission.Admission(
                scope=plan.scope, config_revision=config['revision'], org_revision=config['org_revision'],
                policy_digest=usage_admission.digest(policy), binding_digest=usage_admission.digest(config['binding']),
                profile_digest=price.profile_digest, task_digest=usage_admission.digest(task), grant=grant,
                price_observed_at=price.observed_at, price_valid_until=price.valid_until,
                approval_id=approval_id, approved_at=approved_at,
                approval_until=min(plan.expires_at, now + _ADMISSION_WINDOW),
                maximum_usd_micros=plan.maximum_usd_micros, coordinator=plan.thread_id,
                reviewer=plan.reviewer)
            usage_admission.validate(evidence, scope=plan.scope, config=config, policy=policy, task=task, now=now)
    except (KeyError, IndexError, TypeError, AttributeError, OverflowError, RecursionError,
            usage_credentials.CredentialError):
        raise BudgetError('invalid_codex_host_evidence') from None
    result = usage_routing._run_live(plan.checkout, task, evidence)
    if habblaud is not None:
        if generation_ready is not None:
            generation_ready(result)
        result = result | dict(visibility=_publish_generation(plan, task, evidence, result, habblaud,
                                                              visibility=visibility))
    return result
