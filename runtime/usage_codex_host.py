"""Foreground Codex adapter. Only the trusted coordinator supplies tool receipts.

This module checks bindings and limits. It does not authenticate copied data,
register a CLI action, change enablement, or initiate retries.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import re
import time
from uuid import uuid4

from runtime import usage_admission, usage_credentials, usage_routing, usage_settings, worker_visibility
from runtime.usage_budget import BudgetError, fields, integer, name, require
from runtime.usage_canary_host import direct_approval, native_capacity, package_digest

_MANUAL_WINDOW = 300
_MANUAL_PLAN_WINDOW = 24 * 60 * 60
_SMART_WINDOW = 7 * 24 * 60 * 60
_SMART_PROPOSALS = 12
_PILOT_PROPOSALS = 4
_PILOT_OUTPUT_LIMIT = 256
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
class PilotReview:
    """An observed independent acceptance of the previous paired case."""

    reviewer: str
    run_id: str
    accepted: bool


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
    native_allowance_pp: int | None = None
    manual_price_window: tuple[int, int] | None = None
    pilot_digest: str | None = None
    pilot_revision: int | None = None
    pilot_output_limit: int | None = None
    standing_consent: bool = False
    prepared_turn_id: str | None = None


def approval_text(plan):
    require(type(plan) is Plan, 'invalid_codex_host_plan')
    return 'Approve Usage Routing ' + usage_admission.digest(asdict(plan))


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


def _settings(control, data, *, one_shot=False):
    config = control._config(data)
    org = data['organizations'][config['binding']['organization']]
    require(not usage_admission.canary_budget_only(config, org['policy']), 'routing_budget_required')
    require('canary_owner' not in config and 'manual_test_owner' not in config, 'live_mode_unqualified')
    if one_shot:
        require(config['enabled'] is False and config['requested_mode'] == 'manual', 'manual_test_requires_off')
        require(all(run['closed'] for run in data['runs'].values()), 'manual_test_in_use')
    else:
        require(config['enabled'] is True and config['disabled'] is False
                and config['requested_mode'] in ('manual', 'smart'), 'live_mode_unqualified')
    usage_credentials.validate_binding(config['binding'])
    require(config['org_revision'] == org['revision'], 'policy_revision_changed')
    return config, org['policy']


def _temporary_config(config, price, now, *, pilot_output_limit=None):
    require(type(price) is Price and integer(price.observed_at) <= now < integer(price.valid_until), 'pricing_stale')
    profile = dict(config['profile'], verified_at=price.observed_at, valid_until=price.valid_until)
    if pilot_output_limit is not None:
        require(0 < integer(pilot_output_limit) <= _PILOT_OUTPUT_LIMIT, 'invalid_manual_pilot')
        profile['max_output_tokens'] = pilot_output_limit
    require(price.profile_digest == usage_admission.digest(profile), 'pricing_stale')
    return dict(config, profile=profile, revision=config['revision'] + 1, enabled=True, disabled=False)


def _execution_proposal(proposal):
    require(isinstance(proposal, dict), 'invalid_fields')
    return {key: value for key, value in proposal.items() if key != 'visibility'}


def _proposal(control, config, proposal, now):
    fields(proposal, _PROPOSAL_FIELDS + (' external' if isinstance(proposal, dict) and 'external' in proposal else ''))
    name(proposal['task_id'])
    require(proposal['class_name'] == config['class_name'], 'task_class_unqualified')
    require((proposal['provider'] == 'openai-api'
             or (config['requested_mode'] == 'smart' and proposal['provider'] == 'auto'))
            and proposal['local_preferred'] is False and type(proposal['api_only']) is bool,
            'explicit_api_selection_required')
    control._snapshots(proposal['snapshots'])
    control._external(config, proposal)
    quote = usage_routing.quote(proposal['packet'], config['profile'], now)
    return quote['maximum_usd_micros'] + quote['count_maximum_usd_micros']


def _funding_pool(policy, pool):
    require(pool in ('benefit', 'prepaid', 'mixed')
            and (policy['ceiling'] != 'subscription_benefit' or pool == 'benefit'), 'pool_unqualified')


def freeze(cwd, proposals, *, thread_snapshot, native_limits=None, reviewer, expires_at,
           maximum_usd_micros=None, pool='benefit', manual_allowance_pp=None, manual_price=None, pilot_digest=None,
           standing_consent=False):
    """Pin the exact proposals and limits without dispatching or writing settings."""
    now = int(time.time())
    try:
        with usage_settings.controller(cwd) as control:
            checkout = str(control.root)
            (thread_id, host_id), turn = _current(thread_snapshot, checkout, now)
            reviewer_id = _reviewer(reviewer, thread_id)
            one_shot = manual_price is not None
            state = control.ledger.snapshot()
            config, policy = _settings(control, state, one_shot=one_shot)
            settings_digest = usage_admission.digest([config, policy])
            pilot_revision = None
            pilot_output_limit = None
            if pilot_digest is not None:
                require(one_shot and type(pilot_digest) is str and re.fullmatch('[0-9a-f]{64}', pilot_digest),
                        'invalid_manual_pilot')
                pilot_revision = config['revision']
            mode = config['requested_mode']
            live_consent = usage_admission.live_consent_current(config, policy, now)
            require(type(standing_consent) is bool and
                    (not standing_consent or (mode == 'smart' and live_consent and not one_shot)),
                    'standing_consent_required')
            _funding_pool(policy, pool)
            if maximum_usd_micros is None:
                require(policy['pilot_limit'] is None, 'live_approval_unqualified')
            else:
                usage_admission.validate_maximum(maximum_usd_micros, policy)
            window = _MANUAL_WINDOW if mode == 'manual' else _SMART_WINDOW
            if one_shot:
                window = _MANUAL_PLAN_WINDOW
            require(policy['start'] <= now < integer(expires_at) <= min(policy['stop_at'], now + window),
                    'codex_approval_window_invalid')
            limit = _SMART_PROPOSALS if mode == 'smart' else _PILOT_PROPOSALS if pilot_digest else 1
            minimum = 2 if pilot_digest else 1
            require(type(proposals) is list and minimum <= len(proposals) <= limit, 'codex_proposals_invalid')
            proposals = deepcopy(proposals)
            if pilot_digest is not None:
                pilot_output_limit = max(integer(proposal['packet']['max_output_tokens']) for proposal in proposals)
            if one_shot:
                config = _temporary_config(config, manual_price, now, pilot_output_limit=pilot_output_limit)
            proposals = [_execution_proposal(proposal) for proposal in proposals]
            if standing_consent:
                require(all('external' not in item or item['external'].get('automatic') is True for item in proposals),
                        'external_automatic_selection_denied')
                require(len(proposals) == 1 and (proposals[0].get('snapshots') == []
                        or proposals[0].get('external', {}).get('automatic') is True),
                        'standing_source_scope_exceeded')
            maximum = sum(_proposal(control, config, proposal, now) for proposal in proposals)
            if maximum_usd_micros is None or one_shot:
                exposure = control.ledger._total(state, config['binding']['organization']) + maximum
                if maximum_usd_micros is None:
                    usage_admission.validate_maximum(exposure, policy)
                    maximum_usd_micros = exposure
                else:
                    require(exposure <= maximum_usd_micros, 'live_cap_exceeded')
            require(len({proposal['task_id'] for proposal in proposals}) == len(proposals)
                    and maximum <= maximum_usd_micros, 'codex_proposals_invalid')
            account = native_limits.get('accountId') if isinstance(native_limits, dict) else None
            if not isinstance(account, str) or not account.strip():
                account = thread_id
            return Plan(
                thread_id=thread_id, host_id=host_id, checkout=checkout, scope=control.scope,
                package_digest=package_digest(), settings_digest=settings_digest, mode=mode,
                organization=config['binding']['organization'], project=config['binding']['project'],
                model=config['profile']['model'], reasoning=config['profile']['reasoning'], account=account,
                reviewer=reviewer_id, pool=pool, maximum_usd_micros=maximum_usd_micros,
                quoted_usd_micros=maximum, proposal_digests=tuple(usage_admission.digest(item) for item in proposals),
                created_at=now, expires_at=expires_at,
                manual_price_window=(manual_price.observed_at, manual_price.valid_until) if one_shot else None,
                pilot_digest=pilot_digest, pilot_revision=pilot_revision, pilot_output_limit=pilot_output_limit,
                standing_consent=standing_consent, prepared_turn_id=turn['id'] if mode == 'manual' else None)
    except (KeyError, IndexError, TypeError, AttributeError, OverflowError, RecursionError,
            usage_credentials.CredentialError):
        raise BudgetError('invalid_codex_host_evidence') from None


def _approved_at(plan, thread_snapshot, approval_snapshot, now, *, case_index=0):
    identity, current = _current(thread_snapshot, plan.checkout, now)
    require(identity == (plan.thread_id, plan.host_id), 'codex_thread_changed')
    approved = current
    if approval_snapshot is not None:
        require((plan.mode == 'smart' or (plan.pilot_digest is not None and case_index > 0))
                and _thread(approval_snapshot, plan.checkout) == identity,
                'codex_thread_changed')
        approved = approval_snapshot['turns'][0]
        require(approved['status'] == 'completed' and name(approved['id']) != current['id']
                and integer(approved['startedAt']) <= current['startedAt'], 'codex_approval_missing')
    approved_at = integer(approved['startedAt'])
    require(plan.created_at <= approved_at <= now < plan.expires_at
            and direct_approval(approved, approval_text(plan)), 'codex_approval_missing')
    if plan.manual_price_window is not None and approval_snapshot is None:
        require(now < approved_at + _MANUAL_WINDOW, 'codex_approval_missing')
    return approved_at


def _run_id(plan, proposal_digest):
    return 'codex-api-' + usage_admission.digest([usage_admission.digest(asdict(plan)), proposal_digest])


def _task_authorization(plan, proposal, proposal_digest, current, previous, selection, config, policy, now, case_index):
    if not plan.standing_consent and selection is None:
        return (_approved_at(plan, current, previous, now, case_index=case_index),
                _run_id(plan, proposal_digest))
    identity, turn = _current(current, plan.checkout, now)
    require(identity == (plan.thread_id, plan.host_id), 'codex_thread_changed')
    require(previous is None and plan.created_at <= now < plan.expires_at
            and usage_admission.live_consent_current(config, policy, now), 'live_consent_required')
    if plan.standing_consent:
        require(plan.mode == 'smart' and selection is None and len(plan.proposal_digests) == 1
                and (proposal['snapshots'] == [] or proposal.get('external', {}).get('automatic') is True),
                'standing_source_scope_exceeded')
        # Refreezing one task must not create another automatic attempt.
        key = ['standing', plan.scope, proposal['task_id']]
        return now, 'codex-api-' + usage_admission.digest(key)
    require(plan.mode == 'manual' and plan.manual_price_window is None and turn['id'] == plan.prepared_turn_id
            and type(selection) is str and 0 < len(selection) <= 8000 and selection.strip()
            and direct_approval(turn, selection), 'codex_approval_missing')
    key = ['manual', plan.scope, plan.thread_id, turn['id']]
    return turn['startedAt'], 'codex-api-' + usage_admission.digest(key)


def _plan_settings(control, data, plan, case_index, prior_review):
    config, policy = _settings(control, data, one_shot=plan.manual_price_window is not None)
    baseline = config
    if plan.pilot_digest is not None:
        require(config['revision'] == integer(plan.pilot_revision) + 2 * case_index, 'codex_settings_changed')
        baseline = dict(config, revision=plan.pilot_revision)
        for digest in plan.proposal_digests[:case_index]:
            run = data['runs'].get(_run_id(plan, digest))
            require(run and run['closed'] and run['native'] in data['finished_native']
                    and all(run['calls'].get(phase, {}).get('state') == 'completed'
                            for phase in ('count', 'generation')), 'manual_pilot_prior_case_incomplete')
        if case_index:
            expected = _run_id(plan, plan.proposal_digests[case_index - 1])
            require(type(prior_review) is PilotReview and prior_review.reviewer == plan.reviewer
                    and prior_review.run_id == expected and prior_review.accepted is True,
                    'manual_pilot_review_required')
    require((control.scope, str(control.root), config['requested_mode']) == (plan.scope, plan.checkout, plan.mode)
            and usage_admission.digest([baseline, policy]) == plan.settings_digest, 'codex_settings_changed')
    return config, policy


def _run_temporary_manual(plan, task, evidence, config, case_index, prior_review):
    owner = uuid4().hex
    failure = None
    with usage_settings.controller(plan.checkout) as control:
        try:
            with control.ledger.transaction() as data:
                saved, _ = _plan_settings(control, data, plan, case_index, prior_review)
                data['routing'][control.scope] = dict(config, manual_test_owner=owner)
            return usage_routing._run_live(plan.checkout, task, evidence)
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                with control.ledger.transaction() as data:
                    current = control._config(data)
                    if current.get('manual_test_owner') == owner:
                        if current['revision'] == config['revision']:
                            data['routing'][control.scope] = dict(saved, revision=current['revision'] + 1)
                        else:
                            require(not current['enabled'], 'manual_test_cleanup_required')
                            del current['manual_test_owner']
            except Exception:
                if failure is None:
                    raise BudgetError('manual_test_cleanup_required') from None
                failure.add_note('Manual test cleanup requires a status and disable check.')


def _publish_generation(plan, task, evidence, result, habblaud, *, visibility):
    run_id = task['run_id']
    if (result.get('run_id') != run_id or result.get('simulation_only') is not False
            or result.get('accepted') is not False or result.get('requires_native_review') is not True
            or result.get('execution_route') != 'openai-api'
            or type(result.get('reported_cost_usd_micros')) is not int):
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
                    and generation['cost'] == result['reported_cost_usd_micros'], 'generation_unconfirmed')
            state = control.ledger.path.parent

        from runtime.habblaud import publish_usage_badge
        return publish_usage_badge(habblaud, model=plan.model, thread_id=plan.thread_id,
            checkout=plan.checkout, run_id=run_id, execution_route=result['execution_route'],
            visibility=visibility, state=state)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, ImportError) as error:
        reason = str(error) if str(error) in ('generation_unconfirmed', 'visibility_parent_unavailable') else 'visibility_unavailable'
        return dict(delivered=False, reason=reason)


def execute(plan, proposal, *, thread_snapshot, approval_snapshot=None, reviewer,
            native_limits=None, native_observed_at=None, grant, price, prior_review=None,
            habblaud=None, generation_ready=None, manual_selection=None):
    """Issue exact, short-lived admission from current trusted Codex observations."""
    now = int(time.time())
    require(type(plan) is Plan, 'invalid_codex_host_plan')
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
        case_index = plan.proposal_digests.index(proposal_digest)
        one_shot = plan.manual_price_window is not None
        with usage_settings.controller(plan.checkout) as control:
            config, policy = _plan_settings(control, control.ledger.snapshot(), plan, case_index, prior_review)
            approved_at, run_id = _task_authorization(
                plan, proposal, proposal_digest, thread_snapshot, approval_snapshot, manual_selection,
                config, policy, now, case_index)
            if one_shot:
                require(type(price) is Price and (price.observed_at, price.valid_until) == tuple(plan.manual_price_window),
                        'pricing_stale')
                config = _temporary_config(config, price, now, pilot_output_limit=plan.pilot_output_limit)
            require(_proposal(control, config, proposal, now) <= plan.maximum_usd_micros, 'live_cap_exceeded')
            _funding_pool(policy, plan.pool)
            require(type(grant) is usage_admission.Grant and grant.pool == plan.pool and integer(grant.balance) > 0,
                    'balance_stale_or_missing')
            profile = config['profile']
            require(type(price) is Price and price == Price(usage_admission.digest(profile),
                    profile['verified_at'], profile['valid_until']), 'pricing_stale')
            native = native_capacity(plan.account)
            qualification = None
            if plan.mode == 'smart' and not usage_admission.live_consent_current(config, policy, now):
                record = usage_admission.smart_qualification(config, now)
                qualification = usage_admission.digest([config['qualification_revision'], record])
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
                approval_until=min(plan.expires_at, (approved_at if one_shot and approval_snapshot is None else now)
                                   + _MANUAL_WINDOW),
                maximum_usd_micros=plan.maximum_usd_micros, coordinator=plan.thread_id,
                reviewer=plan.reviewer, qualification_digest=qualification)
            usage_admission.validate(evidence, scope=plan.scope, config=config, policy=policy, task=task, now=now)
    except (KeyError, IndexError, TypeError, AttributeError, OverflowError, RecursionError,
            usage_credentials.CredentialError):
        raise BudgetError('invalid_codex_host_evidence') from None
    if one_shot:
        result = _run_temporary_manual(plan, task, evidence, config, case_index, prior_review)
    else:
        result = usage_routing._run_live(plan.checkout, task, evidence)
    if habblaud is not None:
        if generation_ready is not None:
            generation_ready(result)
        result = result | dict(visibility=_publish_generation(plan, task, evidence, result, habblaud,
                                                              visibility=visibility))
    return result
