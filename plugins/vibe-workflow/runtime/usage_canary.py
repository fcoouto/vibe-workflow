"""One source-free Manual canary for the trusted host's admission path."""
from copy import deepcopy
import time

from runtime import usage_admission, usage_credentials, usage_routing
from runtime.usage_budget import BudgetError, integer, name, require

CLASS_NAME = 'canary'
MAXIMUM_USD_MICROS = 50_640
MAX_WAIT = 300
_MODEL = 'gpt-6.1-sol'
_REASONING = 'medium'
_INPUT_LIMIT = 10_000
_OUTPUT_LIMIT = 64


def profile_for(observed_at, valid_until) -> dict:
    """Return the canary's verified short Standard rate card and limits."""
    require(integer(observed_at) < integer(valid_until), 'pricing_stale')
    return dict(model=_MODEL, reasoning=_REASONING, tier='default', input_rate=2_000_000,
                cached_input_rate=100_000, cache_write_rate=5_000_000, output_rate=10_000_000,
                max_input_tokens=_INPUT_LIMIT, max_output_tokens=_OUTPUT_LIMIT,
                verified_at=observed_at, valid_until=valid_until, count_maximum=0)


def task_for(approval_id, binding, native) -> dict:
    """Build the exact packet and stable ledger identifiers without opening Keychain."""
    try:
        usage_credentials.validate_binding(binding)
    except usage_credentials.CredentialError:
        raise BudgetError('invalid_credential_reference') from None
    approval_id = name(approval_id)
    require(approval_id == approval_id.strip(), 'canary_approval_unqualified')
    identity = usage_admission.digest([approval_id, binding['organization'], binding['project']])
    task_id = 'canary-task-' + identity
    require(type(native) is dict and set(native) in ({'account', 'windows'}, {'account', 'windows', 'task'}),
            'invalid_canary_capacity')
    require(native.get('task', task_id) == task_id, 'native_task_mismatch')
    return dict(
        task_id=task_id, run_id='canary-run-' + identity, class_name=CLASS_NAME,
        packet=dict(version=1, model=_MODEL, reasoning=_REASONING, text='Reply with the word READY.',
                    input_tokens=_INPUT_LIMIT, max_output_tokens=_OUTPUT_LIMIT),
        provider='openai-api', local_preferred=False, api_only=True, reviewer_available=True,
        native=dict(account=native['account'], windows=deepcopy(native['windows']), task=task_id), snapshots=[],
    )


def validate(evidence, *, binding, profile, task, now) -> None:
    """Check the fixed canary envelope. The live runner checks current authority."""
    require(type(evidence) is usage_admission.Admission
            and type(getattr(evidence, 'grant', None)) is usage_admission.Grant, 'trusted_live_evidence_required')
    require(type(task) is dict and 'native' in task, 'canary_task_changed')
    fixed = task_for(evidence.approval_id, binding, task['native'])
    fixed_digest = usage_admission.digest(fixed)
    require(usage_admission.digest(task) == fixed_digest == evidence.task_digest, 'canary_task_changed')
    require(usage_admission.digest(binding) == evidence.binding_digest, 'live_binding_changed')
    fixed_profile = profile_for(evidence.price_observed_at, evidence.price_valid_until)
    require(type(profile) is dict and usage_admission.digest(profile)
            == usage_admission.digest(fixed_profile) == evidence.profile_digest, 'live_profile_changed')
    bound = usage_routing.quote(fixed['packet'], profile, integer(now))
    require((evidence.grant.organization, evidence.grant.project) == (binding['organization'], binding['project']),
            'observation_identity_changed')
    require(now < integer(evidence.grant.expiry), 'expiry_or_cycle_boundary')
    approved_at, approval_until = integer(evidence.approved_at), integer(evidence.approval_until)
    require(approved_at <= now < approval_until <= approved_at + MAX_WAIT
            and 0 < bound['maximum_usd_micros'] <= integer(evidence.maximum_usd_micros) <= MAXIMUM_USD_MICROS,
            'canary_approval_unqualified')


def run(cwd, evidence, *, binding, profile, native):
    """Delegate one approved attempt. The shared ledger rejects reuse of its run ID."""
    require(type(evidence) is usage_admission.Admission, 'trusted_live_evidence_required')
    task = task_for(evidence.approval_id, binding, native)
    validate(evidence, binding=binding, profile=profile, task=task, now=int(time.time()))
    return usage_routing._run_live(cwd, task, evidence)
