"""One-use accounting for authenticated fresh temporary owner publications."""

from copy import deepcopy
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
import time
import uuid

from runtime import evidence, state
from runtime.startup import _check_observation_deadline, _canonical_freeze, _canonical_plain


def _fresh():
    return dict(version=1, birth='temporary-owner-publication', limit_ms=300000,
                revision=0, charged_ms=0, settled_sequence=0, reservation=None, last_receipt=None)


def _check_birth(context, db, targets, budget):
    from runtime import review_storage as storage
    acquired = storage._MACHINE_ACQUISITION.get()
    storage._require(acquired is not None and any(context is selected for selected in acquired['contexts']))
    config = storage._configuration(acquired['path'], acquired['descriptor'])
    storage._require(config is not None)
    owners = storage._owners(config, context, db, budget=budget)
    storage._require(not storage._related_owners(owners, targets, budget))


def _protected(identity=None):
    return dict(identity or {}, ok=False, status='protected', admitted=False, screening=None, revision=None)


class _Control:
    def __init__(self, operation, argument, entered):
        self.operation, self.argument, self.entered = operation, argument, entered
        self.write = operation != 'status'
        self.identity = None

    def access(self, config, context, select, reobserve, budget, publication=None):
        """Use the resolver's live guard and context, never its returned accounting mapping."""
        from runtime import review_storage as storage
        acquired = storage._MACHINE_ACQUISITION.get()
        storage._require(acquired is not None and any(context is selected for selected in acquired['contexts']))
        expected = None

        def validate(db):
            reobserve()
            storage._require(storage._owners(config, context, db, budget=budget) == owners)
            if expected is not None:
                storage._require(state._metadata(db, owner['reference']['name']) == expected)
            if budget is not None:
                _check_observation_deadline(budget)

        with evidence._context_database(context, write=self.write, durable=self.write,
                                        before_commit=validate if self.write else None) as db:
            owners = storage._owners(config, context, db, budget=budget)
            owner = select(owners)
            if owner is None:
                result = _protected()
            else:
                self.identity = {key: owner[key] for key in ('owner', 'reference', 'created_at')}
                metadata = state._metadata(db, owner['reference']['name'])
                result = self.transition(db, metadata)
                expected = state._metadata(db, owner['reference']['name'])
            validate(db)
        return result

    def transition(self, db, metadata):
        screening = metadata.get('screening')
        if screening is None:
            return _protected(self.identity)
        state._screening_record(screening)
        current = deepcopy(screening)
        admitted = False
        if self.operation == 'reserve' and current['revision'] == self.argument == 0:
            ticket = dict(sequence=1, nonce=uuid.uuid4().hex, ceiling_ms=300000)
            current.update(revision=1, reservation=ticket)
            admitted = True
        elif self.operation == 'settle':
            try:
                state._screening_ticket(self.argument)
            except state.StartupError:
                return _protected(self.identity)
            if current['revision'] == 1 and current['reservation'] == self.argument:
                current.update(revision=2, charged_ms=300000, settled_sequence=1,
                    reservation=None, last_receipt=dict(ticket=deepcopy(self.argument), disposition='charged-full'))
            elif current['revision'] != 2 or current['last_receipt']['ticket'] != self.argument:
                return _protected(self.identity)
        if current != screening:
            state._screening_record(current)
            changed = dict(metadata, screening=current)
            db.execute('update evidence_metadata set data=? where name=?',
                       (evidence._raw(changed).decode(), self.identity['reference']['name']))
        result = dict(self.identity, ok=True, status={0: 'available', 1: 'reserved', 2: 'settled'}[current['revision']],
                      admitted=admitted, screening=current, revision=current['revision'])
        if admitted:
            result['deadline_monotonic'] = self.entered + 300
        return result


def operate(selection, operation, argument=None):
    entered = time.monotonic()
    from runtime import review_storage as storage
    control = _Control(operation, argument, entered)
    try:
        evidence._bounded(selection)
        storage._require(isinstance(selection, dict) and set(selection) in ({'target'}, {'project_scope'}))
        if operation == 'reserve':
            state._integer(argument, 'expected screening revision')
        if 'target' in selection:
            target = selection['target']
            storage._require(isinstance(target, str) and Path(target).is_absolute() and len(target) <= 4096)
            enrolled = storage.acquire_enrolled_accounting(target)
            if enrolled['status'] != 'outside':
                return _protected(enrolled)
            result = storage._temporary_target_result(target, budget=storage._budget(), control=control)
        else:
            result = storage._project_owner_result(selection['project_scope'], control=control)
        if 'admitted' not in result:
            status = result.get('status')
            result = _protected(dict(control.identity or {}, **result))
            if status == 'storage_pending':
                result['status'] = status
        return result
    except storage._HistoryConflict as error:
        return _protected(storage._history_protected(error.owners))
    except (*storage._ERRORS, RuntimeError):
        return _protected(control.identity)


def screening_status(selection):
    return operate(selection, 'status')


def reserve_screening(selection, expected_revision):
    return operate(selection, 'reserve', expected_revision)


def settle_screening(selection, ticket):
    return operate(selection, 'settle', ticket)


# The ticket is public accounting. Only this live run may produce its result.
_ACTIVE_RUN = ContextVar('onboarding_screening_producer', default=None)


@dataclass(frozen=True, eq=False)
class _ScreeningRun:
    entered_monotonic: float
    deadline_monotonic: float
    execution: dict = field(default_factory=dict, repr=False)


@contextmanager
def _onboarding_screening_run():
    entered = time.monotonic()
    run = _ScreeningRun(entered, entered + 300)
    run.execution['run'] = run
    token = _ACTIVE_RUN.set(run)
    try:
        yield run
    finally:
        _ACTIVE_RUN.reset(token)


def _execution(run):
    from runtime import review_storage as storage
    storage._require(isinstance(run, _ScreeningRun) and _ACTIVE_RUN.get() is run
                     and run.execution.get('run') is run)
    _check_observation_deadline(dict(deadline=run.deadline_monotonic))
    return run.execution


def _selection(plan):
    return (dict(project_scope=deepcopy(plan['project_scope'])) if plan['schema_version'] == 2
            else dict(target=plan['identity']['repository_root']))


def _resolve_run(run, control):
    from runtime import review_storage as storage
    execution = _execution(run)
    selection = _canonical_plain(execution['selection'])
    budget = execution.setdefault('observation_budget', dict(paths=0, bytes=0, deadline=run.deadline_monotonic))
    if 'project_scope' in selection:
        result = storage._project_owner_result(selection['project_scope'], deadline=run.deadline_monotonic,
                                               control=control, budget=budget)
        if result.get('status') not in ('admitted', 'published', 'reuse'):
            return _protected(result)
        return result
    enrolled = storage.acquire_enrolled_accounting(selection['target'], deadline=run.deadline_monotonic, budget=budget)
    if enrolled['status'] != 'outside':
        return _protected(enrolled)
    return storage._temporary_target_result(selection['target'], budget=budget,
                                             control=control)


class _OnboardingControl:
    def __init__(self, run, stage):
        self.run, self.stage = run, stage

    def access(self, config, context, select, reobserve, budget, publication=None):
        from runtime import onboarding as d3, review_storage as storage
        execution = _execution(self.run)
        acquired = storage._MACHINE_ACQUISITION.get()
        storage._require(acquired is not None and any(context is item for item in acquired['contexts']))
        # Discover the immutable source pointer before locking plans, then recheck in the transaction.
        with evidence._context_database(context) as db:
            owners = storage._owners(config, context, db, budget=budget)
            prior = select(owners)
            prior_metadata = state._metadata(db, prior['reference']['name']) if prior else None
        pointer = prior_metadata.get('screening_result') if prior_metadata else None
        paths = {execution['plan_directory']}
        if pointer:
            paths.add(pointer['plan_directory'])
        with ExitStack() as stack:
            locked = {path: stack.enter_context(d3._existing_screening_plan(path, budget=budget))
                      for path in sorted(paths)}
            path, descriptor = locked[execution['plan_directory']]
            plan = d3._load(path)
            if plan is not None:
                execution['last_plan'] = deepcopy(plan)
            storage._require(plan is not None and plan['plan_id'] == execution['plan_id']
                and plan['revision'] == execution['expected_revision']
                and d3._screening_directory_binding(path) == _canonical_plain(execution['directory_binding'])
                and _selection(plan) == _canonical_plain(execution['selection']))
            expected = None

            def validate(db):
                _execution(self.run)
                reobserve()
                storage._require(storage._owners(config, context, db, budget=budget) == published)
                storage._require(d3._screening_directory_binding(path) == _canonical_plain(execution['directory_binding']))
                saved = d3._load(path)
                storage._require(saved == plan)
                if expected is not None:
                    storage._require(state._metadata(db, owner['reference']['name']) == expected)
                if self.stage == 'finalize':
                    storage._require(d3._screening_inputs_match(execution['produced'], execution['verification_budget']))
                if pointer is not None:
                    d3._read_onboarding_screening_source(pointer, locked=locked, budget=budget)
                _check_observation_deadline(budget)

            write = self.stage in ('begin', 'finalize')
            with evidence._context_database(context, write=write, durable=write,
                    before_commit=validate if write else None) as db:
                storage._require(storage._owners(config, context, db, budget=budget) == owners)
                owner = select(owners)
                storage._require(owner == prior)
                if owner is None and self.stage == 'begin':
                    storage._require(publication is not None and len(owners) < storage.MAX_RECORDS - 1)
                    created_at = state._owner_time()
                    identity = dict(kind='temporary', id=uuid.uuid4().hex, created_at=created_at)
                    content = {key: value for key, value in publication.items() if key != 'targets'}
                    content.update(version=1, owner=identity, created_at=created_at, provenance=config['provenance'])
                    storage._require(sum(item['bytes'] for item in owners) + len(evidence._raw(content)) <= evidence.MAX_ARTIFACT)
                    reference = storage._register(context, db, content, publication['targets'], budget=budget)
                    owner = dict(owner=identity, reference=reference, created_at=created_at)
                published = storage._owners(config, context, db, budget=budget)
                if owner is None:
                    return _protected()
                metadata = state._metadata(db, owner['reference']['name'])
                if prior_metadata is not None:
                    storage._require(metadata == prior_metadata)
                identity = {key: owner[key] for key in ('owner', 'reference', 'created_at')}
                accounting = dict(identity, authority=deepcopy(config['authority']))
                record = metadata.get('screening')
                if record is None:
                    expected = metadata
                    validate(db)
                    return _protected(identity)
                state._screening_record(record)
                if self.stage == 'finalize':
                    storage._require(accounting == _canonical_plain(execution['accounting'])
                        and metadata['targets'] == _canonical_plain(execution['targets'])
                        and pointer is None and record['revision'] == 1
                        and record['reservation'] == execution['produced']['ticket'])
                    # Durable projection precedes pointer+settlement; failure leaves a spent reservation.
                    storage._require(self.run.deadline_monotonic - time.monotonic() > 0.05)
                    storage._require(d3._screening_inputs_match(execution['produced'], execution['verification_budget']))
                    projection = dict(version=1, payload=deepcopy(execution['produced']),
                                      freshness=d3._screening_freshness(execution['produced'], plan['current_input_digest']))
                    d3._screening_projection(projection)
                    plan['screening'] = projection
                    plan['revision'] += 1
                    plan['review_digest'] = d3._review_digest(plan)
                    d3._save(plan, path, descriptor, budget=budget)
                    execution['last_plan'] = deepcopy(plan)
                    pointer = dict(version=1, ticket=deepcopy(record['reservation']), plan_directory=str(path),
                        directory_binding=_canonical_plain(execution['directory_binding']), plan_id=plan['plan_id'],
                        projection_digest=evidence._digest(projection['payload']))
                    settled = dict(record, revision=2, charged_ms=300000, settled_sequence=1, reservation=None,
                        last_receipt=dict(ticket=deepcopy(record['reservation']), disposition='charged-full'))
                    expected = dict(metadata, screening=settled, screening_result=pointer)
                    db.execute('update evidence_metadata set data=? where name=?',
                               (evidence._raw(expected).decode(), identity['reference']['name']))
                    state._metadata(db, identity['reference']['name'])
                    result = dict(ok=True, status='published', projection=projection)
                elif pointer is not None:
                    payload = d3._read_onboarding_screening_source(pointer, locked=locked, budget=budget)
                    storage._require(payload['accounting'] == accounting
                        and payload['ticket'] == record['last_receipt']['ticket']
                        and payload['repositories'] == [target for target in metadata['targets'] if target['git'] is not None])
                    expected = metadata
                    freshness = d3._screening_freshness(payload, plan['current_input_digest'])
                    projection = dict(version=1, payload=payload, freshness=freshness)
                    if self.stage != 'reuse' and plan.get('screening') != projection:
                        plan['screening'] = projection
                        plan['revision'] += 1
                        plan['review_digest'] = d3._review_digest(plan)
                        d3._save(plan, path, descriptor, budget=budget)
                        execution['last_plan'] = deepcopy(plan)
                    result = dict(ok=True, status='reuse', payload=payload, freshness=freshness)
                elif record['revision'] == 0 and self.stage == 'begin':
                    ticket = dict(sequence=1, nonce=uuid.uuid4().hex, ceiling_ms=300000)
                    expected = dict(metadata, screening=dict(record, revision=1, reservation=ticket))
                    db.execute('update evidence_metadata set data=? where name=?',
                               (evidence._raw(expected).decode(), identity['reference']['name']))
                    execution.update(accounting=_canonical_freeze(accounting), targets=_canonical_freeze(metadata['targets']),
                                     ticket=_canonical_freeze(ticket))
                    result = dict(ok=True, status='admitted')
                else:
                    expected = metadata
                    result = dict(ok=False, status='unavailable')
                if not write:
                    validate(db)
            # Issue report evidence only after the controlled transaction commits, while plan locks remain held.
            if result.get('status') in ('published', 'reuse'):
                execution['report_authentication'] = _canonical_freeze(dict(status=result['status'],
                    payload=projection['payload'], plan_id=plan['plan_id'], plan_directory=str(path),
                    directory_binding=_canonical_plain(execution['directory_binding']),
                    projection_digest=evidence._digest(projection['payload']), plan_digest=d3._digest(plan)))
            return result


def _begin_onboarding_screening(run, plan_dir, expected_revision, *, admit=True):
    from runtime import onboarding as d3
    execution = _execution(run)
    budget = dict(deadline=run.deadline_monotonic)
    with d3._existing_screening_plan(plan_dir, budget=budget) as (path, _):
        plan = d3._load(path)
        d3._require(plan is not None and plan['revision'] == expected_revision, 'Onboarding revision conflict')
        execution['last_plan'] = deepcopy(plan)
        execution.update(plan_directory=str(path), plan_id=plan['plan_id'], expected_revision=expected_revision,
            directory_binding=_canonical_freeze(d3._screening_directory_binding(path)),
            selection=_canonical_freeze(_selection(plan)), inputs=_canonical_freeze(dict(digest=plan['current_input_digest'])))
    return _resolve_run(run, _OnboardingControl(run, 'begin' if admit else 'reuse'))


def _publish_onboarding_screening(run, payload):
    from runtime import review_storage as storage
    storage._require(isinstance(run, _ScreeningRun) and run.execution.get('run') is run)
    execution = run.execution
    storage._require(payload is execution.get('produced')
        and evidence._digest(payload) == execution.get('produced_digest'))
    if execution.get('completed'):
        return deepcopy(execution['completed'])
    _execution(run)
    result = _resolve_run(run, _OnboardingControl(run, 'finalize'))
    if result.get('status') == 'published':
        execution['completed'] = deepcopy(result)
    return result
