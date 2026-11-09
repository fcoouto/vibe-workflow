"""Validate evidence supplied by a trusted in-process host before a live send.

The types separate host evidence from JSON. The host still owns provenance,
approval capture and trusted observations. Digests bind inputs to that evidence.
"""
from dataclasses import asdict, dataclass
import hashlib
import json

from runtime import usage_credentials
from runtime.usage_budget import BudgetError, FRESH_SECONDS, fields, integer, name, require

CANARY_CYCLE_PREFIX = 'canary-'


@dataclass(frozen=True, slots=True)
class Grant:
    """An observed grant pool in integer USD micro-units."""

    organization: str
    project: str
    balance: int
    expiry: int
    observed_at: int
    source: str
    pool: str


@dataclass(frozen=True, slots=True)
class Admission:
    scope: str
    config_revision: int
    org_revision: int
    policy_digest: str
    binding_digest: str
    profile_digest: str
    task_digest: str
    grant: Grant
    price_observed_at: int
    price_valid_until: int
    approval_id: str
    approved_at: int
    approval_until: int
    maximum_usd_micros: int
    coordinator: str
    reviewer: str
    qualification_digest: str | None = None


def digest(value):
    """Canonical SHA-256 for the JSON values used by this protocol."""
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise BudgetError('invalid_live_admission') from None
    return hashlib.sha256(raw).hexdigest()


def smart_qualification(config, now):
    record = config['qualification']
    require(record is not None and record['observed_at'] <= now < record['valid_until'],
            'qualification_stale')
    require(record['class_name'] == config['class_name'] and record['profile_digest'] == digest(config['profile']),
            'qualification_profile_changed')
    require(record['samples'] >= 2 and record['quality_passed']
            and record['net_native_saved'] > record['uncertainty'], 'savings_unqualified')
    return record


def canary_budget_only(config, policy):
    return policy['cycle'].startswith(CANARY_CYCLE_PREFIX) and config.get('production_budget') is not True


def live_consent_current(config, policy, now):
    """Check saved consent within the caller's current scope and policy revision."""
    return (config.get('live_consent') is True and config['enabled'] is True
            and config['disabled'] is False and config['requested_mode'] in ('manual', 'smart')
            and config['binding'].get('storage_kind') == 'macos_keychain'
            and not canary_budget_only(config, policy)
            and policy['start'] <= now < policy['stop_at'] < min(policy['end'], policy['expiry']))


def validate_maximum(maximum, policy):
    """Require a finite approval bound even when the policy omits its cumulative cap."""
    limit = policy['pilot_limit']
    require(0 < integer(maximum) and (limit is None or maximum <= integer(limit)), 'live_approval_unqualified')


def validate(evidence, *, scope, config, policy, task, now) -> int:
    """Return the exclusive send deadline for the current, exact admission inputs."""
    try:
        require(type(evidence) is Admission and type(evidence.grant) is Grant, 'live_admission_required')
        return _validate(evidence, scope, config, policy, task, integer(now))
    except usage_credentials.CredentialError:
        raise BudgetError('invalid_credential_reference') from None
    except (AttributeError, KeyError, TypeError, OverflowError, RecursionError):
        raise BudgetError('invalid_live_admission') from None


def _validate(evidence, scope, config, policy, task, now):
    require(type(config) is dict and type(policy) is dict, 'invalid_live_admission')
    if canary_budget_only(config, policy) or 'canary_owner' in config:
        require(config.get('canary_owner') == digest(asdict(evidence))
                and policy['cycle'] == CANARY_CYCLE_PREFIX + evidence.approval_id, 'routing_budget_required')
    mode = config['requested_mode']
    require(config['enabled'] is True and config['disabled'] is False and mode in ('manual', 'smart'),
            'live_mode_unqualified')
    require(name(evidence.scope) == name(scope) and integer(evidence.config_revision) == integer(config['revision']),
            'consent_revision_changed')
    require(integer(evidence.org_revision) == integer(config['org_revision']), 'policy_revision_changed')

    binding, profile = config['binding'], config['profile']
    usage_credentials.validate_binding(binding)
    require(type(profile) is dict, 'invalid_live_admission')
    fields(task, 'task_id run_id class_name packet provider local_preferred api_only reviewer_available native snapshots'
           + (' external' if isinstance(task, dict) and 'external' in task else ''))
    for observed, value, reason in (
        (evidence.policy_digest, policy, 'policy_revision_changed'),
        (evidence.binding_digest, binding, 'live_binding_changed'),
        (evidence.profile_digest, profile, 'live_profile_changed'),
        (evidence.task_digest, task, 'live_task_changed'),
    ):
        require(type(observed) is str and observed == digest(value), reason)
    require((task['provider'] == 'openai-api' or (mode == 'smart' and task['provider'] == 'auto'))
            and task['local_preferred'] is False and type(task['api_only']) is bool,
            'explicit_api_selection_required')
    require(name(task['class_name']) == config['class_name'], 'task_class_unqualified')
    require(task['reviewer_available'] is True and name(evidence.coordinator).strip() == evidence.coordinator
            and name(evidence.reviewer).strip() == evidence.reviewer and evidence.coordinator != evidence.reviewer,
            'independent_review_unavailable')
    require(name(evidence.approval_id).strip() == evidence.approval_id
            and integer(evidence.approved_at) <= now < integer(evidence.approval_until),
            'live_approval_unqualified')
    validate_maximum(evidence.maximum_usd_micros, policy)

    grant = evidence.grant
    require((name(grant.organization), name(grant.project)) == (binding['organization'], binding['project']),
            'observation_identity_changed')
    require(type(grant.source) is str and grant.source == 'platform-credit-grants', 'observation_unqualified')
    ceiling = policy['ceiling']
    require(ceiling in ('none', 'custom', 'subscription_benefit') and type(grant.pool) is str
            and grant.pool in ('benefit', 'prepaid', 'mixed')
            and (ceiling != 'subscription_benefit' or grant.pool == 'benefit'), 'pool_unqualified')
    integer(grant.balance)
    observed_at = integer(grant.observed_at)
    require(observed_at <= now < observed_at + FRESH_SECONDS, 'balance_stale_or_missing')
    expiry, stop = integer(policy['expiry']), integer(policy['stop_at'])
    require(integer(grant.expiry) == expiry, 'expiry_changed')
    require(integer(policy['start']) <= now < stop < min(integer(policy['end']), expiry), 'expiry_or_cycle_boundary')
    price_start, price_end = integer(evidence.price_observed_at), integer(evidence.price_valid_until)
    require(price_start == integer(profile['verified_at']) and price_end == integer(profile['valid_until'])
            and price_start <= now < price_end, 'pricing_stale')
    integer(profile['count_maximum'])
    deadline = min(observed_at + FRESH_SECONDS, price_end, stop, evidence.approval_until)
    if mode == 'smart':
        if evidence.qualification_digest is None:
            require(live_consent_current(config, policy, now), 'live_consent_required')
        else:
            # Retain admission for exact approvals issued under the legacy scope.
            require(config.get('live_consent') is not True, 'qualification_changed')
            record = smart_qualification(config, now)
            require(evidence.qualification_digest == digest([config['qualification_revision'], record]),
                    'qualification_changed')
            deadline = min(deadline, record['valid_until'])
    else:
        require(evidence.qualification_digest is None, 'qualification_changed')

    native = task['native']
    fields(native, 'account task windows')
    name(native['account']); name(task['run_id'])
    require(name(native['task']) == name(task['task_id']), 'native_task_mismatch')
    return deadline
