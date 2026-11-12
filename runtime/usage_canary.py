"""One source-free Manual canary for the trusted host's admission path."""
from copy import deepcopy
import math
import time

from runtime import model_names, routing_launch, usage_admission, usage_credentials, usage_routing
from runtime.usage_budget import BudgetError, integer, name, require

CLASS_NAME = 'canary'
MAXIMUM_USD_MICROS = 50_640
MAX_WAIT = 300
SERVICE = 'openai-api'  # the Usage Routing service selector the task names; not a catalog provider
HOST = routing_launch.HOSTS['api_usage']  # the catalog's (provider, harness) for the API host
RATES = ('input_rate', 'cached_input_rate', 'cache_write_rate', 'output_rate')
_REASONING = 'medium'
_INPUT_LIMIT = 10_000
_OUTPUT_LIMIT = 64


def candidate(settings_path=None) -> dict:
    """Plan-time only: the lightest model the table lists for the API host at the canary's reasoning, with that
    entry's own catalog per-side list prices (USD micros per million tokens). The operator verifies the full rate card
    and freezes it with the model in the approved `Plan`; execution never reads the table."""
    found = model_names.cheapest(*HOST, _REASONING, settings_path)
    require(found is not None, 'canary_model_unavailable')
    model, price = found
    require(type(price) is dict and set(price) == {'input', 'output'} and all(
        type(price[side]) in (int, float) and math.isfinite(price[side]) and price[side] >= 0 for side in price),
        'canary_price_evidence_missing')
    return dict(model=model, list_input_rate=round(price['input'] * 1_000_000),
                list_output_rate=round(price['output'] * 1_000_000))


def card_of(profile):
    """The approved model and rates a profile carries (its other fields are fixed by `profile_for`)."""
    require(type(profile) is dict and {'model', *RATES} <= set(profile), 'live_profile_changed')
    return {key: profile[key] for key in ('model', *RATES)}


def profile_for(observed_at, valid_until, card) -> dict:
    """Return the canary's short Standard profile from the approved `card` (`model` and the four verified rates)."""
    require(integer(observed_at) < integer(valid_until), 'pricing_stale')
    require(type(card) is dict and set(card) == {'model', *RATES} and all(
        type(card[rate]) is int and card[rate] >= 0 for rate in RATES)
        and card['input_rate'] > 0 and card['output_rate'] > 0, 'canary_price_unverified')
    return dict(model=name(card['model']), reasoning=_REASONING, tier='default',
                **{rate: card[rate] for rate in RATES}, max_input_tokens=_INPUT_LIMIT, max_output_tokens=_OUTPUT_LIMIT,
                verified_at=observed_at, valid_until=valid_until, count_maximum=0)


def task_for(approval_id, binding, native, card) -> dict:
    """Build the exact packet and stable ledger identifiers without opening Keychain. The approved `card` (model and
    rates) is part of the task identity, so a profile with other rates never matches the admitted task."""
    try:
        usage_credentials.validate_binding(binding)
    except usage_credentials.CredentialError:
        raise BudgetError('invalid_credential_reference') from None
    approval_id = name(approval_id)
    require(approval_id == approval_id.strip(), 'canary_approval_unqualified')
    require(type(card) is dict and set(card) == {'model', *RATES}, 'canary_price_unverified')
    identity = usage_admission.digest([approval_id, binding['organization'], binding['project'], card])
    task_id = 'canary-task-' + identity
    require(type(native) is dict and set(native) in ({'account', 'windows'}, {'account', 'windows', 'task'}),
            'invalid_canary_capacity')
    require(native.get('task', task_id) == task_id, 'native_task_mismatch')
    model = name(card['model'])
    return dict(
        task_id=task_id, run_id='canary-run-' + identity, class_name=CLASS_NAME,
        packet=dict(version=1, model=model, reasoning=_REASONING, text='Reply with the word READY.',
                    input_tokens=_INPUT_LIMIT, max_output_tokens=_OUTPUT_LIMIT),
        provider=SERVICE, local_preferred=False, api_only=True, reviewer_available=True,
        native=dict(account=native['account'], windows=deepcopy(native['windows']), task=task_id), snapshots=[],
    )


def validate(evidence, *, binding, profile, task, now) -> None:
    """Check the fixed canary envelope. The live runner checks current authority."""
    require(type(evidence) is usage_admission.Admission
            and type(getattr(evidence, 'grant', None)) is usage_admission.Grant, 'trusted_live_evidence_required')
    require(type(task) is dict and 'native' in task, 'canary_task_changed')
    approved = card_of(profile)
    fixed = task_for(evidence.approval_id, binding, task['native'], approved)
    fixed_digest = usage_admission.digest(fixed)
    require(usage_admission.digest(task) == fixed_digest == evidence.task_digest, 'canary_task_changed')
    require(usage_admission.digest(binding) == evidence.binding_digest, 'live_binding_changed')
    fixed_profile = profile_for(evidence.price_observed_at, evidence.price_valid_until, approved)
    require(usage_admission.digest(profile)
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
    usage_routing.refuse_managed(cwd)
    require(type(evidence) is usage_admission.Admission, 'trusted_live_evidence_required')
    task = task_for(evidence.approval_id, binding, native, card_of(profile))
    validate(evidence, binding=binding, profile=profile, task=task, now=int(time.time()))
    return usage_routing._run_live(cwd, task, evidence)
