"""One foreground canary using receipts read by the trusted Codex coordinator.

The host obtains native approval directly. This module checks its binding
and does not authenticate copies supplied by task inputs.
It has no public JSON entry point.
"""
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
import time

from runtime import usage_admission, usage_canary, usage_credentials, usage_settings
from runtime.usage_budget import BudgetError, integer, name, require

PACKAGE = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Plan:
    thread_id: str
    host_id: str
    checkout: str
    scope: str
    package_digest: str
    account: str
    reviewer: str
    organization: str
    project: str
    key_name: str
    key_expiry: int | None
    grant_expiry: int
    grant_amount: int
    created_at: int
    expires_at: int


def approval_text(plan):
    require(type(plan) is Plan, 'invalid_canary_plan')
    return 'Approve Usage Routing canary ' + usage_admission.digest(asdict(plan))[:16]


def direct_approval(turn, token):
    direct = [item for item in turn['items'] if item['type'] == 'userMessage']
    return (len(direct) == 1 and bool(name(direct[0]['id']))
            and direct[0]['content'] in ([{'type': 'text', 'text': token}],
                                         [{'type': 'text', 'text': token + '\n'}]))


def verify_approval(plan, thread_snapshot, now):
    """Check the newest direct user message from native read_thread."""
    require(type(plan) is Plan and integer(plan.created_at) <= integer(now) < integer(plan.expires_at)
            < integer(plan.grant_expiry), 'canary_plan_expired')
    try:
        thread, turn = thread_snapshot['thread'], thread_snapshot['turns'][0]
        require((thread['kind'], thread['id'], thread['hostId'], thread['cwd']) ==
                ('codex', plan.thread_id, plan.host_id, plan.checkout), 'canary_thread_changed')
        approved_at = integer(turn['startedAt'])
        require(turn['status'] == 'inProgress'
                and plan.created_at <= approved_at <= now < approved_at + usage_canary.MAX_WAIT,
                'canary_approval_stale')
        require(direct_approval(turn, approval_text(plan)), 'canary_approval_missing')
    except (KeyError, IndexError, TypeError):
        raise BudgetError('canary_approval_missing') from None
    return approved_at


def native_capacity(account, response=None, observed_at=None, now=None, allowance_pp=None):
    """Retain legacy host arguments while supplying only native task identity."""
    return dict(account=name(account), windows=[])


def issue(plan, *, thread_snapshot, key_receipt, config, policy, grant, native_limits=None,
          native_observed_at=None, reviewer, scope, package_digest, now):
    """Bind an approved plan to current host evidence for one canary."""
    approved_at = verify_approval(plan, thread_snapshot, now)
    require(scope == plan.scope and type(package_digest) is str
            and re.fullmatch('[0-9a-f]{64}', package_digest) is not None
            and package_digest == plan.package_digest, 'canary_candidate_changed')
    require(name(reviewer) == plan.reviewer and reviewer != plan.thread_id,
            'independent_review_unavailable')
    try:
        expected_selection = dict(name=plan.key_name, organization_id=plan.organization, project_id=plan.project)
        if plan.key_expiry is not None:
            expected_selection['expires_in_seconds'] = integer(plan.key_expiry)
        binding = config['binding']
        usage_credentials.validate_binding(binding)
        require(key_receipt == dict(selection=expected_selection, mode='off', binding=binding)
                and (binding['organization'], binding['project']) == (plan.organization, plan.project),
                'canary_key_receipt_changed')
        require(type(grant) is usage_admission.Grant and grant.pool == 'benefit'
                and grant.expiry == plan.grant_expiry and policy['ceiling'] == 'subscription_benefit'
                and policy['grant_amount'] == integer(plan.grant_amount) > 0
                and policy['pilot_limit'] == policy['task_limit'] == usage_canary.MAXIMUM_USD_MICROS,
                'canary_funding_changed')
        profile = config['profile']
        native = native_capacity(plan.account)
        approval_id = usage_admission.digest(asdict(plan))
        task = usage_canary.task_for(approval_id, binding, native)
        evidence = usage_admission.Admission(
            scope=scope, config_revision=config['revision'], org_revision=config['org_revision'],
            policy_digest=usage_admission.digest(policy), binding_digest=usage_admission.digest(binding),
            profile_digest=usage_admission.digest(profile), task_digest=usage_admission.digest(task),
            grant=grant, price_observed_at=profile['verified_at'], price_valid_until=profile['valid_until'],
            approval_id=approval_id, approved_at=approved_at,
            approval_until=min(plan.expires_at, approved_at + usage_canary.MAX_WAIT),
            maximum_usd_micros=usage_canary.MAXIMUM_USD_MICROS, coordinator=plan.thread_id,
            reviewer=reviewer)
        usage_canary.validate(evidence, binding=binding, profile=profile, task=task, now=now)
        owned = dict(config, canary_owner=usage_admission.digest(asdict(evidence)))
        usage_admission.validate(evidence, scope=scope, config=owned, policy=policy, task=task, now=now)
        return evidence, native
    except usage_credentials.CredentialError:
        raise BudgetError('canary_key_receipt_changed') from None
    except (KeyError, TypeError, AttributeError, OverflowError):
        raise BudgetError('invalid_canary_host_evidence') from None


def package_digest(root=PACKAGE):
    """Digest the exact staged package. Any added file changes the result."""
    index = {}
    total = 0
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'canary_candidate_changed')
        if path.is_dir():
            continue
        require(path.is_file() and len(index) < 1500, 'canary_candidate_changed')
        size = path.stat().st_size
        total += size
        require(total <= 32 * 1024 * 1024, 'canary_candidate_changed')
        index[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return usage_admission.digest(index)


def _temporary_run(controller, plan, evidence, binding, profile, native):
    owner = usage_admission.digest(asdict(evidence))
    failure = None
    try:
        with controller.ledger.transaction() as data:
            config = controller._config(data)
            require(config['revision'] == 1 and not config['enabled'] and not config['disabled'],
                    'consent_revision_changed')
            config.update(revision=2, enabled=True, canary_owner=owner)
        return usage_canary.run(plan.checkout, evidence, binding=binding, profile=profile, native=native)
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            with controller.ledger.transaction() as data:
                config = data.get('routing', {}).get(controller.scope)
                if config and config.get('canary_owner') == owner and config['revision'] == 2:
                    config.update(revision=3, enabled=False, disabled=True)
                    del config['canary_owner']
        except Exception:
            if failure is None:
                raise BudgetError('canary_cleanup_required') from None
            failure.add_note('Canary cleanup requires status and manual disable verification.')


def execute(plan, *, thread_snapshot, key_receipt, grant, native_limits=None,
            native_observed_at=None, reviewer, price_observed_at, price_valid_until):
    """Trusted foreground execution after actual approval and Keychain receipt."""
    now = int(time.time())
    verify_approval(plan, thread_snapshot, now)
    scope, checkout = usage_settings.scope(plan.checkout)
    require(str(checkout) == plan.checkout, 'canary_candidate_changed')
    digest = package_digest()
    try:
        binding = key_receipt['binding']
    except (KeyError, TypeError):
        raise BudgetError('canary_key_receipt_changed') from None
    profile = usage_canary.profile_for(price_observed_at, price_valid_until)
    policy = dict(cycle=usage_admission.CANARY_CYCLE_PREFIX + usage_admission.digest(asdict(plan)), start=plan.created_at,
                  end=plan.grant_expiry, expiry=plan.grant_expiry,
                  stop_at=min(plan.expires_at, plan.grant_expiry - 900),
                  ceiling='subscription_benefit', amount=None, grant_amount=plan.grant_amount,
                  pilot_limit=usage_canary.MAXIMUM_USD_MICROS, task_limit=usage_canary.MAXIMUM_USD_MICROS)
    config = dict(enabled=True, disabled=False, requested_mode='manual', revision=2, org_revision=1,
                  class_name=usage_canary.CLASS_NAME, binding=binding, profile=profile)
    evidence, native = issue(
        plan, thread_snapshot=thread_snapshot, key_receipt=key_receipt, config=config, policy=policy,
        grant=grant, native_limits=native_limits, native_observed_at=native_observed_at,
        reviewer=reviewer, scope=scope, package_digest=digest, now=now)
    with usage_settings.controller(plan.checkout, create=True) as controller:
        require((controller.scope, controller.root) == (scope, checkout), 'canary_candidate_changed')
        current = controller.ledger.snapshot()
        require(scope not in current.get('routing', {})
                and plan.organization not in current['organizations'], 'canary_state_already_configured')
        controller.configure(dict(version=2, expected_revision=0, expected_org_revision=0,
                                  binding=binding, profile=profile, policy=policy,
                                  class_name=usage_canary.CLASS_NAME, mode='manual'))
        return _temporary_run(controller, plan, evidence, binding, profile, native)
