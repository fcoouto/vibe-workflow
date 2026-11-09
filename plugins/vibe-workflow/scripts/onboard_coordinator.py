"""Callable trusted-coordinator entry; no proposal parser can create a decision.

The caller verifies actual human or standing authority before constructing these
values. References and equality checks bind that decision; they do not prove it.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from runtime import changes, onboarding, startup


class CoordinatorOperationOwner:
    def __init__(self, verified_decision, *, setup_observations=(), setup_observer=None, setup_attempt_ref=None):
        decision = deepcopy(startup._canonical_plain(verified_decision))
        onboarding._bounded(decision)
        onboarding._object(decision, ('decision_reference', 'operation', 'plan_directory',
            'plan_digest', 'approved', 'bookkeeping_scope'), ('creation', 'qualification_scope'))
        onboarding._require(changes._evidence(decision['decision_reference']), 'A prior verified decision reference is required')
        onboarding._require(decision['operation'] in ('apply', 'resume', 'rollback'), 'Unknown coordinator operation')
        onboarding._hash(decision['plan_digest'])
        onboarding._object(decision['approved'], ('manifest', 'approval'))
        onboarding._object(decision['bookkeeping_scope'], ('subjects', 'authorities'))
        self._creation = startup._canonical_freeze(decision['creation']) if 'creation' in decision else None
        if self._creation is not None:
            onboarding._object(decision['creation'], ('version','intent','resume'))
            self._require(type(decision['creation']['version']) is int and decision['creation']['version']==1)
            changes._birth_intent_shape(decision['creation']['intent'])
        self._decision = startup._canonical_freeze(decision)
        self._qualification_scope = decision.get('qualification_scope')
        if self._qualification_scope is not None:
            from runtime.setup_observation import _authorization_shape
            _authorization_shape(self._qualification_scope)
            self._qualification_scope = startup._canonical_freeze(self._qualification_scope)
        self._entered = False
        self._setup_observer = setup_observer
        self._setup_attempt_ref = deepcopy(setup_attempt_ref)
        self._setup_context = None
        self._require(setup_observer is None or callable(setup_observer))
        self._projections = None
        self._records = None
        from runtime.setup_observation import _issued
        self._setup_observations = tuple(setup_observations)
        for observation in self._setup_observations:
            _issued(observation)

    def _require(self, condition):
        if not condition:
            raise changes.Pending('The current operation is outside the independently verified coordinator decision')

    def _core(self, plan, request):
        decision = startup._canonical_plain(self._decision)
        self._require(onboarding._digest(plan) == decision['plan_digest'])
        self._require({key: request.get(key) for key in ('manifest', 'approval')} == decision['approved'])
        return decision

    def authorize_d4_operation(self, frozen_entry_facts):
        facts = startup._canonical_plain(frozen_entry_facts)
        decision = self._core(facts['plan'], facts['request'])
        self._require(facts['operation'] == decision['operation']
            and facts['plan_directory'] == decision['plan_directory'])
        allowed = decision['bookkeeping_scope']
        for item in facts.get('authorities', []):
            self._require((item['authority'] in allowed['authorities'] or self._birth_authority(item['authority']))
                and item['project_id'] in {subject['project_id'] for subject in allowed['subjects']})
        prior = changes._scoped_prior(facts['plan'])[1]
        final = decision['approved']['manifest']['enrollment']['record']
        template = dict(plan=facts['plan'], manifest=decision['approved']['manifest'])
        self._records = [prior, final, changes._scoped_suspended(template), changes._scoped_suspended(template, rollback=True)]
        self._projections = {onboarding._digest({key: subject[key] for key in
            ('project_id', 'repository_id', 'checkout_id', 'client')}): {subject['accepted_identity_digest']}
            for subject in allowed['subjects']}
        for record in (prior, final):
            if not record or record.get('schema_version') != 2:
                continue
            for key in changes._scoped_entries(record):
                subject = dict(project_id=record['project_id'], repository_id=key[0], checkout_id=key[1], client=key[2])
                digest = onboarding._digest(subject)
                if digest in self._projections:
                    projection = startup.canonical_scope_projection(startup.decode_project_record(record),
                        repository_id=key[0], checkout_id=key[1], client=key[2])
                    self._projections[digest].add(onboarding._digest(startup._canonical_plain(projection)))
        self._entered = True

    def _projection(self, identity):
        subject = {key: identity[key] for key in ('project_id', 'repository_id', 'checkout_id', 'client')}
        self._require(onboarding._digest(identity) in self._projections.get(onboarding._digest(subject), set()))

    def record_setup(self, directory, journal):
        from runtime.setup_observation import persist_setup
        self._require(self._entered and str(directory) == self._decision['plan_directory']['path']
            and self._decision['operation'] in ('apply', 'resume'))
        self._core(journal['plan'], {key: journal[key] for key in ('manifest', 'approval')})
        persist_setup(directory, journal, self._setup_observations,
            qualification_scope=self._qualification_scope)

    def _setup_scope(self, accepted_identity):
        from runtime.setup_observation import _authorization_shape
        from runtime.state import _accepted_identity
        decision = startup._canonical_plain(self._decision)
        directory, observed = changes._public_plan_directory(decision['plan_directory']['path'])
        self._require(observed == decision['plan_directory'])
        plan = onboarding._load(directory)
        self._core(plan, decision['approved'])
        self._require(decision['operation'] in ('apply','resume'))
        identity = _accepted_identity(accepted_identity)
        record = decision['approved']['manifest']['enrollment']['record']
        projection = startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(record),
            repository_id=identity['repository_id'],checkout_id=identity['checkout_id'],client=identity['client']))
        self._require(identity == projection and any(all(subject[key] == identity[key] for key in
            ('project_id','repository_id','checkout_id','client')) and subject['accepted_identity_digest'] == onboarding._digest(identity)
            for subject in decision['bookkeeping_scope']['subjects']))
        authority = _authorization_shape(startup._canonical_plain(self._qualification_scope))
        target = authority['target']
        self._require(target['accepted_scope_digest'] == onboarding._digest(identity)
            and all(target[key] == identity[key] for key in ('repository_root','git_common_dir')))
        self._require(onboarding._filesystem_binding(Path(identity['project_root'])) == identity['project_binding'])
        for name,binding in identity['bindings'].items():
            self._require(onboarding._filesystem_binding(Path(identity['repository_root' if name=='root' else name])) == binding)
        from runtime import state
        self._require(startup.policy_digest(state.PACKAGE)==identity['configuration']['policy_digest']
            and state.runtime_fingerprint()==identity['configuration']['runtime_digest'])
        return identity,authority,decision

    def acquire_setup_attempt(self, accepted_identity, attempt_ref):
        """Bootstrap only; this does not issue AcceptedScope or managed task authority."""
        from runtime.setup_observation import _attempt_admission
        identity,authority,decision=self._setup_scope(accepted_identity)
        attempt = next((row for row in authority['attempts'] if row['reference'] == startup._canonical_plain(attempt_ref)),None)
        self._require(attempt is not None and attempt['task'] is None and attempt['operations'] in (['observe-cli-basic'],['observe-setup-metadata'])
            and attempt['capability_keys'] == ['startup-context'])
        import time
        self._require(attempt['limits']['deadline'] > time.time())
        context = self._setup_context
        self._require(context is not None and context['attempt_ref'] == startup._canonical_plain(attempt_ref)
            and context['action_id'] == attempt['command_action'] and context['transaction_id'] == decision['approved']['manifest']['transaction_id'])
        admission = _attempt_admission(identity,authority,attempt,context['transaction_id'],None,attempt['operations'][0],
            owner=self,process=context['process'])
        if attempt['operations'] == ['observe-setup-metadata']:
            from runtime.setup_observation import setup_capture_projection
            setup_capture_projection(admission,process=context['process'])
        return admission

    def setup_command_allocation(self, action, journal):
        if self._setup_observer is None:return None
        from runtime.setup_observation import _authorization_shape
        self._require(self._entered)
        decision = self._core(journal['plan'],{key:journal[key] for key in ('manifest','approval')})
        authority = _authorization_shape(startup._canonical_plain(self._qualification_scope))
        attempt = next((row for row in authority['attempts'] if row['reference'] == self._setup_attempt_ref),None)
        self._require(attempt is not None and attempt['task'] is None)
        if action['id'] != attempt['command_action']:return None
        self._require(action in decision['approved']['manifest']['actions'])
        import time
        self._require(journal['receipts'].get(action['id'],{}).get('command') is not None
            or attempt['limits']['deadline'] > time.time())
        if attempt['operations'] == ['observe-setup-metadata']:
            from runtime.setup_observation import _retained_capture, _capture_identity, _budget
            capture, original = _retained_capture(attempt['source_capture'],_budget())
            identity = _capture_identity(capture,original)
            self._require(authority['target']['accepted_scope_digest'] == onboarding._digest(identity)
                and sorted(attempt['native_sessions']) == sorted(row['id'] for row in capture['witness']['native']['threads']))
        return startup._canonical_freeze(attempt)

    def observe_setup_command(self, process, directory, journal, action, command_attempt_id):
        from runtime.setup_observation import persist_capture
        allocation = self.setup_command_allocation(action,journal)
        self._require(allocation is not None and journal['receipts'][action['id']]['command'] ==
            dict(attempt_id=command_attempt_id,stopped=False))
        self._require(changes._command_owner(action['owner']['git_dir']) == dict(schema_version=1,
            plan_dir=str(directory),transaction_id=journal['transaction_id'],id=action['id'],attempt_id=command_attempt_id))
        self._setup_context = dict(process=process,attempt_ref=self._setup_attempt_ref,action_id=action['id'],
            transaction_id=journal['transaction_id'],command_attempt_id=command_attempt_id)
        try:
            observation = self._setup_observer(process,self)
            if observation is not None:
                persist_capture(directory,journal,observation,action['id'],command_attempt_id,process=process)
        finally:
            self._setup_context = None

    def observe_native_source(self, accepted_identity, attempt_ref, observer):
        """One authorized external native observation; no project-file recovery claim."""
        import os,subprocess,time,uuid
        from runtime import doctor,state,setup_observation as source
        identity,authority,decision=self._setup_scope(accepted_identity)
        attempt=next((row for row in authority['attempts'] if row['reference']==startup._canonical_plain(attempt_ref)),None)
        self._require(attempt is not None and 'native_execution' in attempt and callable(observer))
        execution=attempt['native_execution'];directory=Path(decision['plan_directory']['path'])
        self._require(attempt['limits']['deadline']>time.time())
        with changes._public_plan_lock(directory),changes._worktree_lock(identity['git_dir']):
            journal=changes._load_journal(directory)
            if journal is None:
                approved=decision['approved']
                journal=dict(schema_version=2,transaction_id=approved['manifest']['transaction_id'],
                    manifest_digest=onboarding._digest(approved['manifest']),manifest=approved['manifest'],approval=approved['approval'],
                    plan=onboarding._load(directory),state='prepared',receipts={},enrollment=None,blocked=[],directories={},observations=[])
            self._core(journal['plan'],{key:journal[key] for key in ('manifest','approval')})
            self._require('qualification_scope' not in journal or journal['qualification_scope']==authority)
            journal['qualification_scope']=authority;rows=journal.setdefault('native_observations',{})
            key=onboarding._digest(attempt_ref)
            self._require(key not in rows and len(rows)<64 and changes._command_owner(identity['git_dir']) is None)
            row=dict(claim_id=uuid.uuid4().hex,attempt_ref=startup._canonical_plain(attempt_ref),identity=identity,allocation_digest=onboarding._digest(attempt),
                status='claimed',returncode=None,pid=None,stopped=False,result=None,before=None,after=None,context=None,reconciliation=None)
            contract=source._native_source_contract(attempt)
            if contract is not None:row['source_contract']=contract
            if attempt.get('probe') is not None:
                row['before']=source._probe_fact(identity,attempt['probe']['canary'])
                self._require(row['before']['target']['fingerprint']['sha256']==attempt['probe']['before_sha256'])
                source._public_canary_patch(attempt['probe'],Path(identity['repository_root']),readback=row['before']['readback'])
            if attempt.get('context_reference') is not None:
                acquired=startup.acquire_project_entry(SimpleNamespace(client=identity['client'],cwd=Path(identity['repository_root'])),state.PACKAGE)
                scope=acquired.resolution.scope
                self._require(scope is not None and scope.accepted_scope_digest==onboarding._digest(identity)
                    and scope.qualification is not None and startup._canonical_plain(scope.qualification['reference'])==attempt['context_reference'])
                text=startup.qualification_context(scope,state.PACKAGE)
                self._require(text is not None)
                import hashlib
                row['context']=dict(sha256=hashlib.sha256(text.encode()).hexdigest())
            rows[key]=row;changes._save(directory,journal)
            marker=dict(schema_version=1,plan_dir=str(directory),transaction_id=journal['transaction_id'],id='native-'+key,attempt_id=row['claim_id'])
            location=changes._owner_path(identity['git_dir']);changes._atomic(location.parent,location.name,json.dumps(marker,sort_keys=True).encode())
            process=None;failed=True
            deadline=min(time.monotonic()+execution['timeout_seconds'],time.monotonic()+max(0,attempt['limits']['deadline']-time.time()))
            try:
                environment={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
                home=environment.get('CODEX_HOME') or (str(Path(environment['HOME'])/'.codex') if environment.get('HOME') else None)
                self._require(home is not None and Path(home).is_absolute() and any(Path(home).resolve().is_relative_to(Path(effect['path']))
                    for effect in execution['external_effects']))
                process=doctor.NativeProcess(execution['argv'],deadline,cwd=execution['cwd'],
                    env=environment,start_new_session=True,
                    stdin=subprocess.PIPE if attempt['operations']==['observe-setup-metadata'] else subprocess.DEVNULL)
                self._require(process.launch_codex_home is not None and any(Path(process.launch_codex_home).is_relative_to(Path(effect['path']))
                    for effect in execution['external_effects']))
                row['pid']=process.process.pid;changes._save(directory,journal)
                self._setup_context=dict(process=process,attempt_ref=row['attempt_ref'],native_row=row,allocation=attempt,journal=journal,directory=directory)
                admission=source._attempt_admission(identity,authority,attempt,journal['transaction_id'],None,attempt['operations'][0],owner=self,process=process)
                if attempt['operations']==['observe-setup-metadata']:source.setup_capture_projection(admission,process=process)
                result=observer(process,admission)
                if result is not None:row['result']=source._native_result(self._setup_context,result)
                if attempt['operations']==['observe-setup-metadata']:process.process.stdin.close()
                process.buffer=b''
                while process.read(deadline):pass
                process.process.wait(timeout=max(0,deadline-time.monotonic()))
                failed=False
            except (doctor.ObservationError,startup.StartupError,OSError,ValueError,TypeError,KeyError,subprocess.TimeoutExpired):
                failed=True
            finally:
                self._setup_context=None
                try:
                    if process is not None:process.close()
                finally:
                    if process is not None:row['returncode']=process.process.poll()
                    row['status']='failed' if failed else 'observed';changes._save(directory,journal)
            if failed:raise changes.Pending('Native source observation is unavailable; original allocation remains consumed and uncertain')
            return startup._canonical_freeze(source._native_reference(directory,row))

    def reconcile_native_source(self, attempt_ref, claim_ref, verified_stopped):
        """Settle the original declared source contract from independently verified facts."""
        from runtime import setup_observation as source
        decision=startup._canonical_plain(self._decision);directory=Path(decision['plan_directory']['path'])
        with changes._public_plan_lock(directory):
            self._require(changes._public_plan_directory(directory)[1]==decision['plan_directory'])
            journal=changes._load_journal(directory)
            self._core(journal['plan'],{key:journal[key] for key in ('manifest','approval')})
            row=journal.get('native_observations',{}).get(onboarding._digest(attempt_ref))
            self._require(row is not None and startup._canonical_plain(claim_ref)==source._native_reference(directory,row)
                and journal.get('qualification_scope')==startup._canonical_plain(self._qualification_scope))
            identity=row['identity'];record=decision['approved']['manifest']['enrollment']['record']
            projection=startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(record),
                repository_id=identity['repository_id'],checkout_id=identity['checkout_id'],client=identity['client']))
            self._require(identity==projection)
            source._native_reconciliation(row,verified_stopped)
            self._require(row['reconciliation'] is None or row['reconciliation']==verified_stopped)
            marker=dict(schema_version=1,plan_dir=str(directory),transaction_id=journal['transaction_id'],id='native-'+onboarding._digest(attempt_ref),attempt_id=row['claim_id'])
            held=changes._command_owner(row['identity']['git_dir'])
            self._require(held==marker or held is None and row['stopped'])
            row['stopped']=True;row['reconciliation']=deepcopy(verified_stopped)
            if row['status']=='observed':row['status']='reconciled'
            changes._save(directory,journal)
            if held is not None:
                import os
                location=changes._owner_path(row['identity']['git_dir']);os.unlink(location)
                descriptor=onboarding._open_path(location.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
            return startup._canonical_freeze(source._native_reference(directory,row))

    def _birth_authority(self, authority):
        if self._creation is None:
            return False
        intent = startup._canonical_plain(self._creation)['intent']
        if authority['directory'] != intent['destination']['directory']:
            return False
        journal = changes._load_journal(Path(intent['identity']['plan_dir']))
        if not journal or 'birth' not in journal or journal['birth']['intent'] != intent:
            return False
        certificate = journal['birth']['certificate']
        if certificate is None or certificate['allocation']['authority'] != authority:
            return False
        changes._birth_certificate_shape(certificate)
        from runtime import evidence, state
        context = changes._recovery_context(authority)
        with evidence._context_database(context) as db:
            baseline = state._source_baseline(None, db, context=context)
        return baseline is not None and {key: startup._canonical_plain(baseline[key]) for key in ('reference','content')} == certificate['baseline']

    def acquire_d4_bookkeeping(self, frozen_facts, issue):
        facts = startup._canonical_plain(frozen_facts)
        self._require(self._entered)
        decision = self._core(facts['plan'], facts['request'])
        if facts.get('operation') == 'canonical-authority-birth':
            self._require(self._creation is not None)
            creation = startup._canonical_plain(self._creation)
            onboarding._object(facts, ('operation','version','stage','request','plan','identity','intent','journal_digest',
                'directories','allocation','baseline','certificate_digest'))
            self._require(facts['version']==1 and facts['intent']==creation['intent'] and facts['identity']==creation['intent']['identity']
                and facts['stage'] in ('directories','allocate','commit','reconcile'))
            scope = decision['bookkeeping_scope']
            self._require(all(peer['authority'] in scope['authorities'] for peer in creation['intent']['peers']))
            record = decision['approved']['manifest']['enrollment']['record']
            for member in creation['intent']['members']:
                for client in member['clients']:
                    projection = startup.canonical_scope_projection(startup.decode_project_record(record),
                        repository_id=member['repository_id'], checkout_id=member['checkout_id'], client=client)
                    self._require(projection is not None)
                    self._projection(startup._canonical_plain(projection))
            self._require(all(row['path'] in creation['intent']['destination']['missing_directories']
                and row['status'] in ('prepared','owned') for row in facts['directories']))
            if facts['stage'] in ('directories','allocate'):
                self._require(facts['allocation'] is None and facts['baseline'] is None and facts['certificate_digest'] is None)
            if facts['stage'] in ('allocate','commit'):
                self._require(len(facts['directories'])==len(creation['intent']['destination']['missing_directories'])
                    and all(row['status']=='owned' and row['binding'] is not None for row in facts['directories']))
            if facts['stage']=='commit':
                self._require(facts['allocation'] is not None and facts['baseline'] is None and facts['certificate_digest'] is None)
            if facts['allocation'] is not None:
                self._require(facts['allocation']['authority']['directory']==creation['intent']['destination']['directory'])
            if facts['stage']=='reconcile':
                resume=creation['resume']
                self._require(resume is not None and resume==dict(intent_digest=onboarding._digest(facts['intent']),
                    allocation=facts['allocation'],baseline=facts['baseline'],certificate_digest=facts['certificate_digest']))
            elif creation['resume'] is not None:
                self._require(creation['resume']==dict(intent_digest=onboarding._digest(facts['intent']),allocation=None,baseline=None,certificate_digest=None))
            return issue(frozen_facts)
        self._require(facts['identity']['plan_dir'] == decision['plan_directory']['path'])
        scope = decision['bookkeeping_scope']
        certificate=facts.get('birth_certificate')
        if certificate is not None:
            self._require(self._creation is not None and certificate['intent']==startup._canonical_plain(self._creation)['intent']
                and self._birth_authority(certificate['allocation']['authority']))
        self._require(all(authority in scope['authorities'] or self._birth_authority(authority) for authority in facts['source']['authorities']))
        self._require(facts['permissions']['record']['content'] in self._records)
        self._require(all(onboarding._digest(subject) in self._projections for subject in facts['permissions']['subjects']))
        for task in facts['source']['tasks']:
            ref = task['reference']
            candidates = [subject for subject in scope['subjects'] if subject['client'] == ref['client']
                and onboarding._digest([3, subject['project_id'], subject['repository_id'], subject['checkout_id']]) == ref['scope_id']]
            self._require(len(candidates) == 1 and ref['authority'] in scope['authorities'])
            self._require(task['pins']['accepted_scope_digest'] in self._projections[onboarding._digest(
                {key: candidates[0][key] for key in ('project_id', 'repository_id', 'checkout_id', 'client')})])
        for project in facts['permissions']['project_permissions']:
            for permitted in project['permitted']:
                self._projection(permitted['identity'])
        for permission in facts['permissions']['current_permissions']:
            if 'permission' in permission:
                self._projection(permission['permission']['projection'])
            elif 'subject' in permission:
                subject = permission['subject']
                projection = startup.canonical_scope_projection(startup.decode_project_record(permission['authority']['record']),
                    repository_id=subject['repository_id'], checkout_id=subject['checkout_id'], client=subject['client'])
                self._projection(startup._canonical_plain(projection))
        # Inherited owners need their exact independently captured identity, not only a subject name.
        for projection in facts['registration_projections'].values():
            capture=projection['inputs'].get('history_capture')
            if capture is None:continue
            for response in capture['job_results']:
                kind=response['job']['kind'];result=response['result']
                if kind=='task':self._projection(result['task']['accepted_identity'])
                elif kind in ('historical-anchor','stopped-certificate'):
                    self._projection(result['certificate']['accepted_identity'])
        for permission in facts['permissions'].get('retention_bookkeeping',[]):
            self._require(permission['authority'] in scope['authorities'] or self._birth_authority(permission['authority']))
            self._projection(permission['identity'])
        return issue(frozen_facts)


def run_verified_operation(verified_decision, plan_dir, proposal, *, setup_observations=(), setup_observer=None, setup_attempt_ref=None):
    """Called only after the coordinator's independent semantic verification."""
    owner = CoordinatorOperationOwner(verified_decision, setup_observations=setup_observations,
        setup_observer=setup_observer,setup_attempt_ref=setup_attempt_ref)
    operation = startup._canonical_plain(owner._decision)['operation']
    function = {'apply': changes.apply_plan, 'resume': changes.resume_plan, 'rollback': changes.rollback_plan}[operation]
    with changes._bootstrap_d4_host(owner, issuer=changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
        return function(plan_dir, proposal, host=host)
