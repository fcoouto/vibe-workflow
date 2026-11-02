"""Callable trusted-coordinator entry; no proposal parser can create a decision.

The caller verifies actual human or standing authority before constructing these
values. References and equality checks bind that decision; they do not prove it.
"""

from copy import deepcopy
from pathlib import Path

from runtime import changes, onboarding, startup


class CoordinatorOperationOwner:
    def __init__(self, verified_decision):
        decision = deepcopy(startup._canonical_plain(verified_decision))
        onboarding._bounded(decision)
        onboarding._object(decision, ('decision_reference', 'operation', 'plan_directory',
            'plan_digest', 'approved', 'bookkeeping_scope'), ('creation',))
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
        self._entered = False
        self._projections = None
        self._records = None

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


def run_verified_operation(verified_decision, plan_dir, proposal):
    """Called only after the coordinator's independent semantic verification."""
    owner = CoordinatorOperationOwner(verified_decision)
    operation = startup._canonical_plain(owner._decision)['operation']
    function = {'apply': changes.apply_plan, 'resume': changes.resume_plan, 'rollback': changes.rollback_plan}[operation]
    with changes._bootstrap_d4_host(owner, issuer=changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
        return function(plan_dir, proposal, host=host)
