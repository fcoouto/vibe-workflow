"""Select routes and enforce local controls for Usage Routing."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
from types import SimpleNamespace

from runtime import startup, usage_admission, usage_credentials, external_delegation
from runtime.usage_budget import BudgetError, FRESH_SECONDS, Ledger, fields, integer, name, require

PACKAGE = Path(__file__).resolve().parents[1]
MAX_INPUT = 65536
FUNDING_WARNING = ('Grant-only funding is best effort. Account auto-reload may buy credits and '
                   'the provider may consume other funding; local limits cannot guarantee otherwise.')
HANDOFF_REASONS = frozenset(('insufficient_headroom', 'balance_stale_or_missing',
    'expiry_or_cycle_boundary', 'evidence_deadline_expired', 'observation_stale'))  # F5.A1: no count reason


def refuse_managed(path):
    """Phase W (C.3 design 4.3): refuse a legacy API Usage spending entry for a managed project, as its first action.

    Its only read is the routing settings' `managed_projects`; nothing here opens Usage Routing settings, a
    credential, a configuration or the ledger. Fail closed: settings that exist but cannot be read count as managed
    (`routing_settings.managed_project`), so a corrupt routing settings file also stops legacy API Usage spending
    until it is repaired. A missing settings file manages nothing. A `None` path cannot be checked, so it is refused
    too. Setup and report actions never call this: they open no spending."""
    from runtime import routing_settings
    try:
        managed = path is None or routing_settings.managed_project(path) is not False
    except Exception:  # noqa: BLE001 - an uninspectable path is treated as managed
        managed = True
    require(not managed, routing_settings.LEGACY_ROUTE_RETIRED)


def status(cwd=None):
    """Read enrollment only, without creating runtime state or observing billing."""
    enrollment = 'unknown'
    if cwd is not None:
        try:
            entry = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(cwd)), PACKAGE)
            enrollment = entry.resolution.status
        except (OSError, ValueError, startup.StartupError):
            enrollment = 'unavailable'
    enrolled = True if enrollment in ('active', 'configured') else False if enrollment == 'outside' else None
    result = dict(version=1, mode='off', live_enabled=False, live_dispatch=False,
                  canary_active=False,
                  review_authority=False, enrolled=enrolled, enrollment=enrollment,
                  reason='live_adapter_unavailable', enabled=False, funding_policy='best_effort_grant',
                  reusable_setups=[], reuse_reason='not_configured', activation=None,
                  reuse_reason_by_mode=dict(smart='not_configured'),
                  warnings=[FUNDING_WARNING])
    from runtime.usage_settings import controller, directory
    if cwd is not None and directory().exists():
        try:
            with controller(cwd, discover=True) as control:
                result.update(control.status())
        except (BudgetError, OSError, ValueError, KeyError, TypeError):
            result['reason'] = 'configuration_or_scope_unavailable'
    return result



def enable(cwd, request):
    if cwd is None:
        raise BudgetError('live_execution_unavailable')
    from runtime.usage_settings import controller
    fields(request, 'expected_revision'
           + (' mode' if isinstance(request, dict) and 'mode' in request else '')
           + (' reuse_scope' if isinstance(request, dict) and 'reuse_scope' in request else '')
           + (' reuse_pin' if isinstance(request, dict) and 'reuse_pin' in request else ''))
    with controller(cwd, attach=True) as control:
        return control.enable(request['expected_revision'], mode=request.get('mode'),
                              reuse_scope=request.get('reuse_scope'), reuse_pin=request.get('reuse_pin'))


def run(cwd, request):
    raise BudgetError('live_execution_unavailable')


def _run_live(cwd, task, evidence):
    """Internal Codex host path. The host owns evidence provenance."""
    from runtime import usage_admission, usage_settings

    require(type(evidence) is usage_admission.Admission, 'trusted_live_evidence_required')
    refuse_managed(cwd)
    with usage_settings.controller(cwd) as control:
        def check_scope():
            require(usage_settings.scope(cwd) == (control.scope, control.root), 'scope_changed')

        return control._run_live(task, evidence, scope_check=check_scope)


def quote(packet, profile, now):
    """Conservative offline quote from synthetic, untrusted count/pricing observations.

    A digest binds these inputs, but does not authenticate them or authorize a call.
    Rates use integer USD micro-units per million tokens.
    """
    fields(packet, 'version model reasoning text input_tokens max_output_tokens')
    fields(profile, 'model reasoning tier input_rate cached_input_rate cache_write_rate output_rate max_input_tokens max_output_tokens verified_at valid_until count_maximum')
    require(type(packet['version']) is int and packet['version'] == 1, 'invalid_version')
    for item in (packet['model'], packet['reasoning'], profile['model'], profile['reasoning']): name(item)
    require(packet['model'] == profile['model'] and packet['reasoning'] == profile['reasoning'], 'profile_mismatch')
    require(profile['tier'] == 'default', 'unsupported_processing_tier')
    require(type(packet['text']) is str and 0 < len(packet['text'].encode()) <= MAX_INPUT, 'invalid_text')
    for key in ('input_tokens', 'max_output_tokens'): integer(packet[key])
    for key in ('input_rate', 'cached_input_rate', 'cache_write_rate', 'output_rate',
                'max_input_tokens', 'max_output_tokens', 'verified_at', 'valid_until', 'count_maximum'):
        integer(profile[key])
    require(profile['input_rate'] > 0 and profile['output_rate'] > 0, 'unknown_price')
    require(profile['verified_at'] <= integer(now) < profile['valid_until'], 'pricing_stale')
    require(packet['input_tokens'] <= profile['max_input_tokens'] and
            0 < packet['max_output_tokens'] <= profile['max_output_tokens'], 'token_limit_exceeded')
    rate = max(profile['input_rate'], profile['cached_input_rate'], profile['cache_write_rate'])
    maximum = (packet['input_tokens'] * rate + packet['max_output_tokens'] * profile['output_rate'] + 999999) // 1000000
    integer(maximum)
    raw = json.dumps(dict(packet=packet, profile=profile), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    require(len(raw) <= MAX_INPUT, 'request_too_large')
    return dict(version=1, digest=hashlib.sha256(raw).hexdigest(), maximum_usd_micros=maximum,
                count_maximum_usd_micros=profile['count_maximum'])


def profile_digest(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Controller:
    """Durable consent, route selection and internal live admission.

    Observations and qualifications alone grant no live authority.
    """
    def __init__(self, ledger, scope, *, clock=None, root=None, bind=None, check_authority=None):
        import time
        self.ledger, self.scope = ledger, name(scope)
        self.clock = clock or (lambda: int(time.time()))
        self.root = Path(root).resolve() if root is not None else None
        self._bind = bind
        self._check_authority = check_authority

    def _backstop(self):
        """Refuse a managed project again, for a caller that skipped its entry point's refusal. Production
        controllers (`usage_settings.controller`) always carry a root; a rootless one has no project to check."""
        if self.root is not None:
            refuse_managed(self.root)

    def _config(self, data):
        config = data.get('routing', {}).get(self.scope)
        require(config is not None, 'not_configured')
        return config

    def configure(self, request):
        expected = 'version expected_revision expected_org_revision binding policy profile class_name mode'
        convert_budget = isinstance(request, dict) and 'production_budget' in request
        fields(request, expected + (' production_budget' if convert_budget else '')
               + (' external_permission' if isinstance(request, dict) and 'external_permission' in request else ''))
        if request.get('external_permission') is not None:
            try: external_delegation.validate_permission(request['external_permission'])
            except ValueError as error: raise BudgetError(str(error)) from None
        version = request['version']
        require(type(version) is int and version in (1, 2), 'invalid_version')
        require(not convert_budget or (version == 2 and request['production_budget'] is True),
                'invalid_production_budget')
        integer(request['expected_revision']); integer(request['expected_org_revision'])
        binding = request['binding']
        if version == 1:
            fields(binding, 'organization project credential_ref')
            for value in binding.values(): name(value)
            require(binding['credential_ref'].isidentifier(), 'invalid_credential_reference')
        else:
            try:
                usage_credentials.validate_binding(binding)
            except usage_credentials.CredentialError as error:
                raise BudgetError(str(error)) from None
        name(request['class_name'])
        # Keep the existing schema's smart value; manual is read-only compatibility.
        require(request['mode'] == 'smart', 'invalid_mode')
        profile = request['profile']
        # Validate the complete profile without transmitting a counting request.
        packet = dict(version=1, model=profile['model'], reasoning=profile['reasoning'],
                      text='qualification', input_tokens=0, max_output_tokens=1)
        quote(packet, profile, self.clock())
        with self.ledger.transaction(grows=True) as data:  # creates an organization
            records = data.setdefault('routing', {})
            old = records.get(self.scope)
            require((old['revision'] if old else 0) == request['expected_revision'], 'consent_revision_changed')
            if old is None and self._check_authority is not None:
                self._check_authority(data)
            organization, policy = binding['organization'], request['policy']
            previous = data['organizations'].get(organization, {}).get('policy')
            revision = self.ledger._configure(data, organization, policy, request['expected_org_revision'])
            same_canary_cycle = (old is not None and old['binding']['organization'] == organization
                                 and previous is not None and previous['cycle'] == policy['cycle']
                                 and policy['cycle'].startswith(usage_admission.CANARY_CYCLE_PREFIX))
            require(not convert_budget or (same_canary_cycle and
                    ('canary_owner' not in old or old['disabled'] is True)),
                    'routing_budget_required')
            records[self.scope] = dict(revision=request['expected_revision']+1, binding=binding.copy(),
                org_revision=revision, profile=profile.copy(), class_name=request['class_name'],
                requested_mode=request['mode'], enabled=False, disabled=False, live_consent=False,
                production_budget=same_canary_cycle and (convert_budget or old.get('production_budget') is True),
                qualification=None, qualification_revision=0)
            permission = request.get('external_permission', old.get('external_permission') if old else None)
            if permission is not None:
                records[self.scope]['external_permission'] = permission
        return self.status()

    def _reusable(self, data):
        if self._check_authority is not None:
            self._check_authority(data)
        setups = {}
        for scope, config in data.get('routing', {}).items():
            if scope == self.scope:
                continue
            try:
                binding = config['binding']
                usage_credentials.validate_binding(binding)
                org = data['organizations'][binding['organization']]
                policy = org['policy']
                require(config['org_revision'] == org['revision']
                        and not Ledger.block_reasons(data, binding['organization']),
                        'policy_revision_changed')
                self.ledger._configure(data, binding['organization'], policy, org['revision'])
                require(policy['start'] <= self.clock() < policy['stop_at']
                        and not usage_admission.canary_budget_only(config, policy)
                        and 'canary_owner' not in config and 'manual_test_owner' not in config,
                        'routing_budget_required')
                profile = config['profile']
                quote(dict(version=1, model=profile['model'], reasoning=profile['reasoning'],
                           text='qualification', input_tokens=0, max_output_tokens=1), profile, self.clock())
                name(scope); name(config['class_name'])
                require(config['requested_mode'] in ('manual', 'smart'), 'invalid_mode')
                setups[scope] = dict(binding=binding.copy(), org_revision=org['revision'],
                    profile=profile.copy(), class_name=config['class_name'],
                    requested_mode='smart', production_budget=config.get('production_budget', False))
            except (ValueError, KeyError, TypeError):
                continue
        return setups

    @staticmethod
    def _reuse_reason(setups, mode=None):
        distinct = {usage_admission.digest(dict(setup, requested_mode=mode or setup['requested_mode']))
                    for setup in setups.values()}
        return ('reuse_available' if len(distinct) == 1 else
                'reuse_selection_required' if distinct else 'reusable_setup_unavailable')

    @staticmethod
    def _setup_pin(data, scope, setup):
        source = data['routing'][scope]
        return usage_admission.digest(dict(scope=scope, setup=setup, source_revision=source['revision'],
            enabled=source['enabled'], disabled=source['disabled'], live_consent=source.get('live_consent'),
            policy=data['organizations'][setup['binding']['organization']]['policy']))

    def status(self):
        data = self.ledger.snapshot()
        config = data.get('routing', {}).get(self.scope)
        canary_only = False
        live_enabled = False
        if config:
            org = data['organizations'][config['binding']['organization']]
            canary_only = usage_admission.canary_budget_only(config, org['policy'])
            live_enabled = (config['org_revision'] == org['revision'] and
                            usage_admission.live_consent_current(config, org['policy'], self.clock()))
        locally_enabled = bool(config and config.get('enabled', False) and not config['disabled'])
        canary_active = locally_enabled and 'canary_owner' in config
        enabled = locally_enabled and not canary_only
        setups = self._reusable(data) if config is None else {}
        reuse_reason = 'already_configured' if config else self._reuse_reason(setups)
        by_mode = {mode: 'already_configured' if config else self._reuse_reason(setups, mode)
                   for mode in ('smart',)}
        reusable = [dict(scope=scope, organization=setup['binding']['organization'],
                        api_project=setup['binding']['project'], org_revision=setup['org_revision'],
                        model=setup['profile']['model'], reasoning=setup['profile']['reasoning'],
                        class_name=setup['class_name'], requested_mode=setup['requested_mode'],
                        profile=setup['profile'].copy(), production_budget=setup['production_budget'],
                        setup_pin=self._setup_pin(data, scope, setup),
                        connection=dict(storage_kind=setup['binding']['storage_kind'],
                            fingerprint=usage_admission.digest(setup['binding']), key_name=None),
                        policy=data['organizations'][setup['binding']['organization']]['policy'].copy())
                    for scope, setup in sorted(setups.items())]
        reason = 'live_adapter_unavailable'
        if canary_only:
            reason = 'routing_budget_required'
        elif live_enabled:
            reason = 'trusted_host_required'
        elif config and config['disabled']:
            reason = 'disabled'
        return dict(version=1, mode='smart' if enabled else 'off', enabled=enabled,
                    live_enabled=live_enabled, live_dispatch=False, review_authority=False, funding_policy='best_effort_grant',
                    canary_active=canary_active,
                    warnings=[FUNDING_WARNING],
                    requested_mode='smart' if config else None,
                    activation=self._activation(config, org['policy']) if live_enabled else None,
                    revision=config['revision'] if config else 0,
                    reusable_setups=reusable, reuse_reason=reuse_reason,
                    reuse_reason_by_mode=by_mode,
                    external_permission=config.get('external_permission') if config else None, reason=reason)

    def enable(self, expected_revision, *, mode=None, reuse_scope=None, reuse_pin=None):
        integer(expected_revision)
        require(mode in (None, 'smart'), 'invalid_mode')
        if reuse_scope is not None:
            name(reuse_scope)
        with self.ledger.transaction() as data:
            config = data.get('routing', {}).get(self.scope)
            require((config['revision'] if config else 0) == expected_revision, 'consent_revision_changed')
            if config is None:
                setups = self._reusable(data)
                if reuse_scope is not None:
                    require(reuse_scope in setups, 'reusable_setup_unavailable')
                else:
                    reason = self._reuse_reason(setups, mode)
                    require(reason == 'reuse_available', reason)
                require(reuse_pin is not None, 'reuse_pin_required')
                selected = {scope: candidate for scope, candidate in setups.items()
                            if (reuse_scope is None or scope == reuse_scope)
                            and self._setup_pin(data, scope, candidate) == reuse_pin}
                require(selected, 'reusable_setup_changed')
                setup = next(iter(selected.values()))
                config = dict(setup, revision=0, enabled=False, disabled=False, live_consent=False,
                              qualification=None, qualification_revision=0)
                data.setdefault('routing', {})[self.scope] = config
            else:
                require(reuse_scope is None and reuse_pin is None, 'already_configured')
            require('manual_test_owner' not in config, 'manual_test_in_use')
            org = data['organizations'][config['binding']['organization']]
            require(not usage_admission.canary_budget_only(config, org['policy']), 'routing_budget_required')
            if mode is not None:
                config['requested_mode'] = mode
            config['revision'] += 1
            config['enabled'] = True
            config['disabled'] = False
            config['live_consent'] = config['binding'].get('storage_kind') == 'macos_keychain'
            if config['live_consent']:
                require(config['org_revision'] == org['revision'], 'policy_revision_changed')
                require(usage_admission.live_consent_current(config, org['policy'], self.clock()),
                        'expiry_or_cycle_boundary')
            if self._bind is not None:
                self._bind()
        return self.status()

    def disable(self, expected_revision):
        integer(expected_revision)
        with self.ledger.transaction() as data:
            config = self._config(data)
            require(config['revision'] == expected_revision, 'consent_revision_changed')
            config['revision'] += 1
            config['disabled'] = True
            config['enabled'] = False
            config['live_consent'] = False
        return self.status()

    def record_qualification(self, record):
        expected = 'class_name profile_digest samples quality_passed net_native_saved uncertainty unit observed_at valid_until'
        if isinstance(record, dict) and 'native_calibrated' in record:
            expected += ' native_calibrated'
        if isinstance(record, dict) and 'native_allowance_pp' in record:
            expected += ' native_allowance_pp'
        fields(record, expected)
        for field in ('samples','net_native_saved','uncertainty','observed_at','valid_until'):
            integer(record[field])
        require(type(record['quality_passed']) is bool, 'invalid_qualification')
        require(record['observed_at'] <= self.clock() < record['valid_until'], 'qualification_stale')
        name(record['unit'])
        with self.ledger.transaction() as data:
            config = self._config(data)
            require('manual_test_owner' not in config, 'manual_test_in_use')
            require(record['class_name'] == config['class_name'] and
                    record['profile_digest'] == profile_digest(config['profile']), 'qualification_profile_changed')
            config['qualification'] = dict(record)
            config['qualification_revision'] += 1

    def observe(self, observation):
        fields(observation, 'version organization project currency balance expiry observed_at source pool')
        require(type(observation['version']) is int and observation['version'] == 1, 'invalid_version')
        require(observation['currency'] == 'USD' and observation['source'] in
                ('codex-dashboard', 'platform-credit-grants'), 'observation_unqualified')
        integer(observation['balance']); integer(observation['observed_at']); integer(observation['expiry'])
        with self.ledger.transaction() as data:
            config = self._config(data); org_id = config['binding']['organization']
            require((observation['organization'], observation['project']) ==
                    (org_id, config['binding']['project']), 'observation_identity_changed')
            org = data['organizations'][org_id]
            require(observation['expiry'] == org['policy']['expiry'], 'expiry_changed')
            require(observation['pool'] in ('benefit','prepaid','mixed') and
                    (org['policy']['ceiling'] != 'subscription_benefit' or observation['pool'] == 'benefit'), 'pool_unqualified')
            require(0 <= self.clock() - observation['observed_at'] <= 900, 'observation_stale')
            require(org['observed_at'] is None or observation['observed_at'] > org['observed_at'], 'observation_not_newer')
            self.ledger._note_balance(org, observation['balance'])
            org['observed_at'] = observation['observed_at']
            org['observation_source'] = observation['source']
            org['funding_state'] = 'unverified'

    def _snapshots(self, snapshots):
        require(type(snapshots) is list and len(snapshots) <= 32, 'invalid_snapshots')
        for snapshot in snapshots:
            fields(snapshot, 'path sha256')
            require(self.root is not None, 'snapshot_changed')
            path = Path(snapshot['path'])
            require(path.is_absolute() and not path.is_symlink() and path.resolve().is_relative_to(self.root)
                    and path.is_file() and path.stat().st_size <= MAX_INPUT, 'snapshot_changed')
            require(hashlib.sha256(path.read_bytes()).hexdigest() == snapshot['sha256'], 'snapshot_changed')

    def _activation(self, config, policy):
        """Derive checkout authority solely from saved settings and live consent."""
        if not usage_admission.live_consent_current(config, policy, self.clock()):
            return None
        return dict(ref=f"activation:openai-api:{self.scope}:r{config['revision']}",
            funding_ref=f"funding:openai-api:{config['binding']['organization']}:r{config['org_revision']}",
            classes=[config['class_name']], destinations=[dict(service='openai-api', mode='proposal',
                provider='openai', model=config['profile']['model'], effort=config['profile']['reasoning'])])

    def _external(self, config, task, policy):
        if 'external' not in task:
            require(not task['snapshots'] and config.get('external_permission') is None,
                    'external_provenance_required')
            return
        try:
            assignment = dict(service='openai-api', mode='proposal', model=config['profile']['model'],
                provider='openai', effort=config['profile']['reasoning'], class_name=task['class_name'])
            permission = external_delegation.effective_permission(config.get('external_permission'),
                self._activation(config, policy), **assignment)
            require(task['provider'] != 'auto' or external_delegation.automatic_allowed(permission),
                    'external_automatic_selection_denied')
            packet = external_delegation.check(self.root, permission, task['external'],
                **assignment, now=self.clock())
            require(task['packet']['text'] == packet['text'] and task['snapshots'] == packet['snapshots'],
                    'external_packet_changed')
        except ValueError as error:
            raise BudgetError(str(error)) from None

    def _evaluate(self, data, task, *, pin_qualification=False, live=False):
        self._backstop()
        fields(task, 'task_id run_id class_name packet provider local_preferred api_only reviewer_available native snapshots'
               + (' external' if isinstance(task, dict) and 'external' in task else ''))
        for field in ('local_preferred','api_only','reviewer_available'):
            require(type(task[field]) is bool, 'invalid_task')
        for field in ('task_id','run_id','class_name','provider'): name(task[field])
        config = self._config(data)
        require(not config['disabled'], 'disabled')
        if task['provider'] not in ('auto','openai-api') or task['local_preferred']:
            return dict(decision='paused' if task['api_only'] else 'native', reason='explicit_provider_or_local_preference')
        org_id = config['binding']['organization']
        org = data['organizations'][org_id]
        require(live or not usage_admission.canary_budget_only(config, org['policy']), 'routing_budget_required')
        require(task['reviewer_available'], 'independent_review_unavailable')
        require(task['class_name'] == config['class_name'], 'task_class_unqualified')
        require(org['revision'] == config['org_revision'], 'policy_revision_changed')
        self._snapshots(task['snapshots'])
        self._external(config, task, org['policy'])
        bound = quote(task['packet'], config['profile'], self.clock())
        require(task['native'].get('task') == task['task_id'], 'native_task_mismatch')
        # Operate on a deserialized snapshot: selection must not reserve or write.
        self.ledger._native(data, task['native'], self.clock())
        self.ledger._admit(data, org_id, task['task_id'],
                           bound['maximum_usd_micros'] + bound['count_maximum_usd_micros'],
                           config['org_revision'], self.clock())
        return dict(decision='eligible', reason='qualified_profile_and_budget', maximum_usd_micros=bound['maximum_usd_micros'],
                    consent=self._consent(config, pin_qualification))

    def _consent(self, config, pin_qualification):
        return dict(scope=self.scope, revision=config['revision'],
                    qualification_revision=config['qualification_revision'] if pin_qualification else None)

    def evaluate(self, task):
        try:
            result = self._evaluate(self.ledger.snapshot(), task)
        except (BudgetError, KeyError, TypeError, OSError, ValueError) as error:
            result = dict(decision='paused', reason=str(error) if isinstance(error,BudgetError) else 'invalid_task_or_state')
        return result | dict(warning_codes=['best_effort_grant_funding'],
                             handoff_recommended=result['decision'] == 'paused' and result['reason'] in HANDOFF_REASONS)

    def request_handoff(self, task, reason):
        """Queue remaining work for existing native dispatch; never clears API exposure."""
        import copy
        self._backstop()
        require(reason in HANDOFF_REASONS, 'unsupported_handoff_reason')
        fields(task, 'task_id run_id class_name packet provider local_preferred api_only reviewer_available native snapshots'
               + (' external' if isinstance(task, dict) and 'external' in task else ''))
        name(task['task_id']); name(task['run_id']); name(task['provider'])
        require(type(task['api_only']) is bool and type(task['reviewer_available']) is bool, 'invalid_task')
        require(type(task['packet']) is dict and type(task['packet'].get('text')) is str and
                len(json.dumps(task['packet']).encode()) <= MAX_INPUT, 'invalid_handoff_packet')
        self._snapshots(task['snapshots'])
        packet_id = profile_digest(task['packet'])
        key = profile_digest(dict(scope=self.scope,task=task['task_id']))
        with self.ledger.transaction() as data:
            config = self._config(data)
            require(not config['disabled'], 'disabled')
            records = data.setdefault('handoffs',{})
            record = records.get(key)
            if record is not None:
                require(record['packet_digest'] == packet_id, 'handoff_task_changed')
            if record is None or record['claim_attempt'] is None:
                state = 'ready'
                if task['api_only'] or task['provider'] not in ('auto','openai-api','native'):
                    state = 'user_choice_required'
                elif not task['reviewer_available']:
                    state = 'waiting_for_native_review'
                else:
                    try:
                        require(task['native'].get('task') == task['task_id'], 'native_task_mismatch')
                        self.ledger._native(copy.deepcopy(data),task['native'],self.clock())
                    except BudgetError:
                        state = 'native_task_unavailable'
                require(record is not None or len(records) < 1000, 'handoff_capacity_reached')
                record = dict(handoff_id=key, state=state, reason=reason, task_id=task['task_id'],
                    packet_digest=packet_id, scope=self.scope, revision=config['revision'],
                    native=copy.deepcopy(task['native']), claim_attempt=None)
                records[key] = record
            pending = any(run['task'] == task['task_id'] and
                          (run.get('consent') or {}).get('scope',self.scope) == self.scope and
                          any(call['state'] in ('dispatching','unresolved') for call in run['calls'].values())
                          for run in data['runs'].values())
            result = dict(record)
        return result | dict(pending_api_accounting=pending, requires_native_review=True, accepted=False,
            brief=dict(task_id=task['task_id'], packet=task['packet'], snapshots=task['snapshots'],
                       instruction='Continue remaining work natively. Recheck source; do not apply late API output without fresh checks and independent review.'))

    def claim_handoff(self, task_id, attempt_id):
        """Claim once BEFORE host native dispatch; a replay must observe, never respawn."""
        name(task_id); name(attempt_id)
        key = profile_digest(dict(scope=self.scope,task=task_id))
        with self.ledger.transaction() as data:
            record = data.get('handoffs',{}).get(key)
            require(record is not None, 'handoff_missing')
            if record['claim_attempt'] is not None:
                require(record['claim_attempt'] == attempt_id, 'handoff_already_claimed')
                return dict(record,new_claim=False)
            config = self._config(data)
            require(config['revision'] == record['revision'] and not config['disabled'], 'consent_changed')
            require(record['state'] == 'ready', 'handoff_not_ready')
            key_native = self.ledger._native(data,record['native'],self.clock())
            data['native'][key_native]['latest_at'] = self.clock()
            record.update(state='claimed',claim_attempt=attempt_id)
            return dict(record,new_claim=True)

    def _transmit(self, packet, binding, dispatch, factory, run_id, revision, resolver,
                  live_gate=None, final_check=None):
        from runtime import openai_api

        def dispatch_gate(state, _):
            # Recheck local scope after acquiring the dispatch transaction.
            # Credential lookup and connection preparation stay outside it.
            final_check()
            now = self.clock()
            require(now < dispatch['deadline'], 'evidence_deadline_expired')
            return live_gate(state, now)

        def admit():
            # The final grant is atomic. Disable after it sees accounted work.
            require(self.clock() < dispatch['deadline'], 'evidence_deadline_expired')
            self.ledger.dispatch(run_id, dispatch['phase'], revision, self.clock(),
                                 live_gate=dispatch_gate if live_gate is not None else None)

        require(self.clock() < dispatch['deadline'], 'evidence_deadline_expired')
        return openai_api._authenticated_call(
            packet, binding, dispatch, dispatch['phase'], admit=admit,
            connection_factory=factory, resolver=resolver)

    def _stage_live(self, data, task, evidence, now):
        from dataclasses import asdict
        from runtime import usage_admission

        config = self._config(data)
        org = data['organizations'][config['binding']['organization']]
        require(config['org_revision'] == org['revision'], 'policy_revision_changed')
        deadline = usage_admission.validate(evidence, scope=self.scope, config=config,
                                            policy=org['policy'], task=task, now=now)
        grant = evidence.grant
        current = dict(observed_at=grant.observed_at, digest=usage_admission.digest(asdict(grant)))
        previous = org.get('live_funding')
        require(previous is None or current == previous or
                current['observed_at'] > previous['observed_at'], 'live_grant_observation_changed')
        # Earlier observations can only lower the usable balance. The first
        # trusted observation establishes the issued allowance.
        self.ledger._note_balance(org, grant.balance)
        if previous is None or previous['digest'] is None:
            org['issued'] = max(org.get('issued') or 0, grant.balance)
        org['observed_at'] = max(org['observed_at'] or 0, grant.observed_at)
        org['live_funding'] = current
        return deadline, dict(pin=usage_admission.digest(asdict(evidence)),
                              maximum=evidence.maximum_usd_micros)

    def _run_live(self, task, evidence, *, scope_check=None,
                  connection_factory=None, credential_resolver=None):
        """Trusted host entry. This is intentionally absent from the JSON CLI."""
        from runtime import usage_admission

        require(type(evidence) is usage_admission.Admission, 'trusted_live_evidence_required')
        require(type(task) is dict, 'invalid_task')
        require((connection_factory is None) == (credential_resolver is None),
                'invalid_transport_dependencies')
        require(connection_factory is not None or callable(scope_check), 'live_scope_check_required')
        if scope_check is not None:
            scope_check()
        return self._run(task, connection_factory, credential_resolver, evidence, scope_check)

    def _run(self, task, connection_factory, credential_resolver, evidence=None, scope_check=None):
        import copy
        from runtime import openai_api

        self._backstop()  # before any decision and before Ledger.begin
        require(evidence is not None or (callable(connection_factory) and callable(credential_resolver)),
                'live_execution_unavailable')
        task = copy.deepcopy(task)
        data = self.ledger.snapshot()
        existing = data['runs'].get(task.get('run_id'))
        if existing is not None:
            require(evidence is None, 'live_run_id_already_used')
            require(existing.get('consent', {}).get('scope') == self.scope and
                    existing['digest'] == openai_api.packet_digest(task['packet']) and
                    existing['task'] == task['task_id'], 'run_identity_changed')
            return dict(replayed=True, accepted=False,
                        requires_native_review=True, state=existing['calls'])
        if evidence is not None:
            require(not any(run['task'] == task.get('task_id')
                    and (run.get('consent') or {}).get('scope') == self.scope
                    for run in data['runs'].values()), 'live_task_already_attempted')
        live_gate = final_check = None
        pin_qualification = False
        if evidence is None:
            decision = self.evaluate(task)
            data = self.ledger.snapshot()
        else:
            # Keep a newer lower balance even when it rules out the request.
            # Otherwise an older proof could still allocate the depleted pool.
            denial = None
            with self.ledger.transaction() as data:
                try:
                    deadline, _ = self._stage_live(data, task, evidence, self.clock())
                except BudgetError as error:
                    if str(error) not in ('expiry_changed', 'live_grant_observation_changed'):
                        raise
                    # A fresh, identity-checked conflicting row retires prior
                    # evidence. Resume only from a newer usable observation.
                    org = data['organizations'][evidence.grant.organization]
                    previous = org.get('live_funding')
                    observed_at = evidence.grant.observed_at
                    if previous is None or observed_at >= previous['observed_at']:
                        org['live_funding'] = dict(observed_at=observed_at, digest=None)
                    denial = error
            if denial is not None:
                raise denial
            pin_qualification = evidence.qualification_digest is not None
            decision = self._evaluate(data, task, pin_qualification=pin_qualification, live=True)

            def live_gate(state, now):
                return self._stage_live(state, task, evidence, now)[1]

            def final_check():
                if scope_check is not None:
                    scope_check()
                self._snapshots(task['snapshots'])
                self._external(self._config(data), task,
                               data['organizations'][self._config(data)['binding']['organization']]['policy'])

        require(decision['decision'] == 'eligible', decision['reason'])
        config = self._config(data)
        require(decision['consent'] == self._consent(config, pin_qualification), 'consent_changed')
        binding, profile, packet = config['binding'], config['profile'], task['packet'].copy()
        now = self.clock(); org_id = binding['organization']; run_id = task['run_id']
        if evidence is None:
            deadline = min(now+300, profile['valid_until'],
                           data['organizations'][org_id]['policy']['stop_at'])
        else:
            deadline = min(deadline, now + 300)
        packet_id = openai_api.packet_digest(packet)
        self.ledger.begin(org_id, task['task_id'], run_id, packet_id, profile['count_maximum'],
                          config['org_revision'], now, task['native'],
                          consent=decision['consent'], live_gate=live_gate)
        phase = 'count'
        try:
            dispatch = dict(phase=phase, attempt_id=run_id+':'+phase, packet_digest=packet_id,
                            committed=True, deadline=deadline)
            counted = self._transmit(packet, binding, dispatch, connection_factory,
                                     run_id, config['org_revision'], credential_resolver, live_gate, final_check)
            self.ledger.complete(run_id, phase, profile['count_maximum'])
            packet['input_tokens'] = counted['input_tokens']
            bound = quote(packet, profile, self.clock())
            self._snapshots(task['snapshots'])
            packet_id = openai_api.packet_digest(packet)
            self.ledger.reserve_generation(run_id, packet_id, bound['maximum_usd_micros'],
                                           config['org_revision'], self.clock(), live_gate=live_gate)
            phase = 'generation'
            dispatch.update(phase=phase, attempt_id=run_id+':'+phase, packet_digest=packet_id)
            result = self._transmit(packet, binding, dispatch, connection_factory,
                                    run_id, config['org_revision'], credential_resolver, live_gate, final_check)
            require(result['input_tokens'] == counted['input_tokens'], 'counted_input_changed')
            used = dict(packet, input_tokens=result['input_tokens'], max_output_tokens=result['output_tokens'])
            # Use conservative rates; retain local charge even if provider balance includes it.
            if result['output_tokens'] == 0:
                raise BudgetError('incomplete_output')
            actual = quote(used, profile, self.clock())['maximum_usd_micros']
            self.ledger.complete(run_id, phase, actual)
            return dict(accepted=False, requires_native_review=True,
                        text=result['text'], request_id=result['request_id'], run_id=run_id,
                        execution_route='openai-api',
                        reported_cost_usd_micros=actual, profile_digest=profile_digest(profile))
        except BaseException as error:
            record = self.ledger.snapshot()['runs'][run_id]['calls'].get(phase)
            if record and record['state'] == 'dispatching':
                if isinstance(error, openai_api.TransportError) and not error.uncertain:
                    self.ledger.reject_unsent(run_id, phase)
                else:
                    incomplete = error.incomplete if isinstance(error, openai_api.TransportError) else None
                    self.ledger.uncertain(run_id, phase, incomplete=incomplete)
            elif not any(c['state'] in ('dispatching','unresolved')
                         for c in self.ledger.snapshot()['runs'][run_id]['calls'].values()):
                self.ledger.close_run(run_id)
            raise


def configure(cwd, request):
    from runtime.usage_settings import controller
    with controller(cwd, create=True) as control:
        return control.configure(request)


def disable(cwd, request):
    from runtime.usage_settings import controller
    fields(request, 'expected_revision')
    with controller(cwd) as control:
        return control.disable(request['expected_revision'])


def evaluate(cwd, task):
    refuse_managed(cwd)
    from runtime.usage_settings import controller
    try:
        with controller(cwd) as control:
            return control.evaluate(task)
    except (BudgetError, OSError, KeyError, TypeError, ValueError):
        return dict(decision='paused', reason='configuration_or_scope_unavailable')


def reconcile(cwd, request):
    from runtime.usage_settings import controller
    fields(request, '')
    with controller(cwd) as control:
        return control.ledger.reconcile(scope=control.scope)


def handoff(cwd, request):
    refuse_managed(cwd)
    from runtime.usage_settings import controller
    fields(request, 'task reason')
    with controller(cwd) as control:
        return control.request_handoff(request['task'], request['reason'])


def claim_handoff(cwd, request):
    refuse_managed(cwd)
    from runtime.usage_settings import controller
    fields(request, 'task_id attempt_id')
    with controller(cwd) as control:
        return control.claim_handoff(request['task_id'], request['attempt_id'])


# -- activation-gate self-check (C.3 design 4.2, capability "API Usage") -----------------------------------------
SELF_CHECK_BUDGET = 8  # seconds, inside the activation gate's own 10 s budget
SELF_CHECK_MARK = 'usage-routing-self-check:'
SELF_CHECK_CHILD = ('import json, sys\nsys.path.insert(0, sys.argv[1])\nfrom runtime import usage_routing\n'
                    'print(usage_routing.SELF_CHECK_MARK + json.dumps(usage_routing._self_check_body(sys.argv[2])))\n')


class _Reached(BaseException):
    """A tripwire no handler under test catches, so a refusal that comes too late cannot hide."""


def _sandbox(workdir):
    path = Path(workdir).absolute()
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or any(path.iterdir()):
        return None
    return path.resolve()


def managed_self_check(workdir):
    """`[]` when every legacy API Usage spending entry refuses a managed project before any settings, credential,
    configuration or ledger access, and `Ledger.begin` is never reached; else fixed failure codes.

    The body swaps module globals, so it runs in a child of this interpreter with the plugin package root first on
    its import path, a fixed environment and its stores in `workdir`. No network, model, host or secret-store call."""
    root = _sandbox(workdir)
    if root is None:
        return ['self_check_workdir_unsafe']
    for item in ('home', 'tmp'):
        (root / item).mkdir(mode=0o700)
    env = dict(PATH='/usr/bin:/bin', HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'), LANG='C.UTF-8')
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', SELF_CHECK_CHILD, str(PACKAGE), str(root / 'check')],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                             cwd=root, start_new_session=True)
    try:
        output, _ = child.communicate(timeout=SELF_CHECK_BUDGET)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        child.communicate()
        return ['self_check_timeout']
    lines = [line for line in output.decode('utf-8', 'replace').splitlines() if line.startswith(SELF_CHECK_MARK)]
    try:
        codes = json.loads(lines[-1][len(SELF_CHECK_MARK):])
    except (IndexError, ValueError):
        return ['self_check_error']
    if type(codes) is not list or not all(type(code) is str for code in codes):
        return ['self_check_error']
    return codes if child.returncode == 0 or codes else ['self_check_error']


def _self_check_body(workdir):
    """The check. It swaps module globals and restores them, so only `managed_self_check`'s child runs it."""
    import dataclasses
    from runtime import (routing_settings, usage_canary, usage_canary_host, usage_codex_host, usage_credentials,
                         usage_settings)
    from scripts import routing as command  # the gate puts the plugin package root first on sys.path
    root = _sandbox(workdir)
    if root is None:
        return ['self_check_workdir_unsafe']
    checkout, other, settings = root / 'checkout', root / 'other', root / 'routing' / 'settings.json'
    checkout.mkdir(mode=0o700)
    other.mkdir(mode=0o700)
    routing_settings.update(settings, dict(managed_projects=[str(checkout)]), 0)

    def trip(attribute):
        def reached(*args, **kwargs):
            raise _Reached(attribute)
        return reached
    swaps = [(command, 'default_settings', lambda: settings), (usage_settings, 'directory', lambda: root / 'usage')]
    swaps += [(target, attribute, trip(attribute)) for target, attribute in (
        (Ledger, 'begin'), (usage_settings, 'controller'), (usage_settings, 'scope'),
        (Controller, 'configure'), (usage_credentials, 'validate_binding'), (usage_credentials, 'resolve_key'),
        (usage_codex_host, 'package_digest'), (usage_canary_host, 'package_digest'),
        (usage_canary_host, 'verify_approval'))]
    saved = [(target, attribute, getattr(target, attribute)) for target, attribute, _ in swaps]

    def blank(kind, **values):
        return kind(**({item.name: None for item in dataclasses.fields(kind)} | values))
    path = str(checkout)
    evidence = blank(usage_admission.Admission)
    native = dict(account='self-check', windows=[], task='self-check')
    task = dict(task_id='self-check', run_id='self-check', class_name='self-check', packet={}, provider='auto',
                local_preferred=False, api_only=False, reviewer_available=True, native=native, snapshots=[])
    control = Controller(None, 'self-check', root=checkout, clock=lambda: 0)
    entries = dict(
        codex_freeze=lambda: usage_codex_host.freeze(path, [task], thread_snapshot={}, reviewer=None, expires_at=0),
        codex_execute=lambda: usage_codex_host.execute(blank(usage_codex_host.Plan, checkout=path), task,
                                                       thread_snapshot={}, reviewer=None, grant=None, price=None),
        canary_host_execute=lambda: usage_canary_host.execute(
            blank(usage_canary_host.Plan, checkout=path), thread_snapshot={}, key_receipt={}, grant=None,
            reviewer=None, price_observed_at=0, price_valid_until=1),
        canary_run=lambda: usage_canary.run(path, evidence, binding={}, profile={}, native=native),
        run_live=lambda: _run_live(path, task, evidence),
        evaluate=lambda: evaluate(path, task),
        handoff=lambda: handoff(path, dict(task=task, reason='insufficient_headroom')),
        claim_handoff=lambda: claim_handoff(path, dict(task_id='self-check', attempt_id='self-check')),
        backstop_run=lambda: control._run(task, None, None),
        backstop_evaluate=lambda: control._evaluate({}, task),
        backstop_handoff=lambda: control.request_handoff(task, 'insufficient_headroom'))
    codes = []
    try:
        for target, attribute, value in swaps:
            setattr(target, attribute, value)
        for entry, call in entries.items():
            try:
                call()
                codes.append(entry + '_not_refused')
            except _Reached:
                codes.append(entry + '_not_refused_first')
            except BudgetError as error:
                if str(error) != routing_settings.LEGACY_ROUTE_RETIRED:
                    codes.append(entry + '_not_refused')
            except Exception:  # noqa: BLE001 - any other failure is a fixed code, never a traceback
                codes.append(entry + '_not_refused')
        try:
            refuse_managed(str(other))
        except BudgetError:
            codes.append('unmanaged_project_refused')
    except Exception:  # noqa: BLE001
        codes.append('self_check_error')
    finally:
        for target, attribute, value in saved:
            setattr(target, attribute, value)
    return codes
