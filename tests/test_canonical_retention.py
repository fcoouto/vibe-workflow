"""Current D4 retention baseline; clocks and native observations are software fixtures."""
from collections import Counter
from contextlib import contextmanager, ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import changes, evidence, onboarding, recovery_uses, review_storage, startup, state
from tests import fixture_canonical_birth as canonical_birth
from tests import test_canonical_recovery as canonical_recovery


class CheckedBirthFixture(canonical_birth.CanonicalBirthFixture):
    def addCleanup(self, function, *args, **kwargs):
        nested = getattr(function, '__self__', None)
        if isinstance(nested, unittest.TestCase) and function.__name__ == 'doCleanups':
            def checked():
                self.assertTrue(function(*args, **kwargs), type(nested).__name__ + ' cleanup failed')
            return super().addCleanup(checked)
        return super().addCleanup(function, *args, **kwargs)


class RetentionMeasurement:
    """Transparent observation only; every read, decode and decision calls its original."""
    def __init__(self):
        self.phase = 'stage'
        self.active = None
        self.raw_sources = {}
        self.events = []
        self.counts = Counter()
        self.core_observations = []

    @contextmanager
    def label(self, name):
        before = self.phase
        self.phase = before + '/' + name
        try:
            yield
        finally:
            self.phase = before

    @contextmanager
    def observe(self, db):
        managed = evidence._managed_content
        loads = json.loads
        opened = os.open
        fdopened = os.fdopen
        support = evidence._retention_original_support_core
        core = evidence._retention_facts_acquired_core
        inventory = evidence._evidence_inventory_rows
        receipts = evidence._retention_reobserve_receipts
        brackets = evidence._retention_global_metadata
        census = evidence._historical_inventory
        measurement = self

        def content(context, reference, metadata):
            previous = self.active
            self.active = dict(authority=evidence._canonical_plain(context.authority_set[0]), reference=reference)
            try:
                raw = managed(context, reference, metadata)
                # Keep the actual raw object alive: equal bodies can belong to different artifacts.
                self.raw_sources[id(raw)] = (raw, copy.deepcopy(self.active))
                return raw
            finally:
                self.active = previous

        def open_file(*args, **kwargs):
            descriptor = opened(*args, **kwargs)
            if self.active is not None:
                self.events.append(dict(kind='physical_open', phase=self.phase, source=self.active))
            return descriptor

        class Stream:
            def __init__(self, stream, source):
                self.stream, self.source = stream, source

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, *args, **kwargs):
                raw = self.stream.read(*args, **kwargs)
                measurement.events.append(dict(kind='physical_read', phase=measurement.phase,
                    source=self.source, bytes=len(raw)))
                return raw

            def __getattr__(self, name):
                return getattr(self.stream, name)

        def fdopen(*args, **kwargs):
            stream = fdopened(*args, **kwargs)
            return stream if self.active is None else Stream(stream, self.active)

        def decode(raw, *args, **kwargs):
            row = self.raw_sources.get(id(raw))
            event = None
            if row is not None and row[0] is raw:
                caller = sys._getframe(1)
                event = dict(kind='managed_json_decode', phase=self.phase, source=row[1],
                    bytes=len(raw), caller=dict(function=caller.f_code.co_name, line=caller.f_lineno,
                        file=caller.f_code.co_filename))
                self.events.append(event)
            try:
                result = loads(raw, *args, **kwargs)
            except BaseException:
                if event is not None:
                    event['succeeded'] = False
                raise
            if event is not None:
                event['succeeded'] = True
            return result

        def original_support(*args, **kwargs):
            self.counts['original_support_calls'] += 1
            with self.label('original-support'):
                return support(*args, **kwargs)

        def facts_core(*args, **kwargs):
            self.counts['retention_facts_core_calls'] += 1
            def native_counters():
                scope = state._CAPTURE_SQL_SCOPE.get()
                return None if scope is None else dict(work=dict(scope[0].work.state), live=dict(scope[0].live.state))
            observation = dict(native_entry=native_counters())
            started = time.monotonic()
            try:
                with self.label('core'):
                    return core(*args, **kwargs)
            finally:
                observation.update(seconds=time.monotonic() - started, native_exit=native_counters())
                self.core_observations.append(observation)

        def all_rows(*args, **kwargs):
            rows = inventory(*args, **kwargs)
            self.events.append(dict(kind='inventory_result', phase=self.phase, rows=len(rows)))
            return rows

        def reobserve(*args, **kwargs):
            with self.label('fresh-receipt-recheck'):
                return receipts(*args, **kwargs)

        def physical_census(*args, **kwargs):
            with self.label('fresh-physical-census'):
                return census(*args, **kwargs)

        @contextmanager
        def metadata(*args, **kwargs):
            original = brackets(*args, **kwargs)
            with self.label('fresh-metadata-entry'):
                entered = original.__enter__()
            try:
                yield entered
            except BaseException:
                with self.label('fresh-metadata-close'):
                    suppressed = original.__exit__(*sys.exc_info())
                if not suppressed:
                    raise
            else:
                with self.label('fresh-metadata-close'):
                    original.__exit__(None, None, None)

        def sql(statement):
            normalized = ' '.join(statement.lower().split())
            if normalized.startswith('select name,digest,owner_key,class,created_at,closed_at,status,bytes,data from evidence_metadata'):
                kind = 'owner_payload_query' if ' where owner_key=' in normalized else 'inventory_payload_query'
                self.events.append(dict(kind=kind, phase=self.phase, sql=statement))

        with ExitStack() as stack:
            for target, name, wrapper in ((evidence, '_managed_content', content), (json, 'loads', decode),
                    (os, 'open', open_file), (os, 'fdopen', fdopen),
                    (evidence, '_retention_original_support_core', original_support),
                    (evidence, '_retention_facts_acquired_core', facts_core),
                    (evidence, '_evidence_inventory_rows', all_rows),
                    (evidence, '_retention_reobserve_receipts', reobserve),
                    (evidence, '_historical_inventory', physical_census),
                    (evidence, '_retention_global_metadata', metadata)):
                stack.enter_context(patch.object(target, name, new=wrapper))
            db.set_trace_callback(sql)
            try:
                yield
            finally:
                db.set_trace_callback(None)

    def result(self):
        totals = Counter(self.counts)
        by_artifact = {}
        by_phase = {}
        for event in self.events:
            kind, phase = event['kind'], event['phase']
            totals[kind] += 1
            phases = by_phase.setdefault(phase, Counter())
            phases[kind] += 1
            if kind == 'physical_read':
                totals['physical_read_bytes'] += event['bytes']
                phases['physical_read_bytes'] += event['bytes']
            if kind == 'managed_json_decode':
                totals['managed_json_decode_succeeded' if event['succeeded'] else 'managed_json_decode_failed'] += 1
            if 'source' in event:
                key = evidence._raw(event['source'])
                row = by_artifact.setdefault(key, Counter())
                row[kind] += 1
                if kind == 'physical_read':
                    row['physical_read_bytes'] += event['bytes']
        return dict(totals=dict(totals), by_phase=by_phase,
            by_artifact={key.decode('utf-8'): value for key,value in by_artifact.items()}, events=self.events,
            core_observations=self.core_observations,
            instrumentation_overhead='Retained raw references and event records belong to measurement, not runtime allocation counters')


class CanonicalRetentionTests(unittest.TestCase):
    def checkpoint(self, name):
        self.boundary = name
        print('C2 boundary: ' + name, flush=True)

    def own(self, fixture_type):
        fixture = fixture_type('runTest')
        def cleanup():
            self.assertTrue(fixture.doCleanups(), fixture_type.__name__ + ' cleanup failed')
        self.addCleanup(cleanup)
        return fixture

    def receipt_snapshot(self, context, references):
        result = {}
        with evidence._context_database(context) as db:
            for reference in references:
                metadata, raw = evidence._managed_artifact(None, reference, context=context, db=db)
                result[evidence._raw(reference)] = (metadata, raw)
        return result

    def owner_snapshot(self, case):
        context, owner = case['context'], case['owner']
        with evidence._context_database(context) as db:
            references = [row['reference'] for row in evidence._evidence_inventory_rows(db)
                if row['metadata']['owner'].get('task') == owner]
            marker = state._retired(db, 'task', {key: owner[key] for key in ('scope_id', 'client', 'session_id', 'task_id')})
            self.assertIsNotNone(marker)
            self.assertIsNone(evidence._linked_raw_task(db, owner))
        return dict(receipts=self.receipt_snapshot(context, references), marker=marker)

    def retention_snapshot(self, case):
        context = case['context']
        with evidence._context_database(context) as db:
            references = [row['reference'] for row in evidence._evidence_inventory_rows(db)]
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            self.assertIsNone(evidence._retained_for_owner(context, db, case['owner']))
            self.assertEqual(state._lifecycle_identity(db, 'task', case['owner'])['status'], 'retired')
        return dict(receipts=self.receipt_snapshot(context, references), baseline=baseline, owner=self.owner_snapshot(case))

    def positive_facts(self, case, label):
        case['clock'][0] = case['closed']['lifecycle']['safe_closed_at'] + 604800
        result = self.measure(case, label)
        self.assertIsNone(result['error'], result['error'])
        self.assertEqual(result['totals']['retention_facts_core_calls'], 1)
        facts = result['facts']
        self.assertEqual(facts['task_ref'], case['owner'])
        self.assertTrue(facts['retention']['recovery']['complete'])
        self.assertEqual(facts['retention']['content']['pins'], case['stopped']['pins'])
        self.assertEqual(facts['retention']['content']['created_at'], case['stopped']['lifecycle']['created_at'])
        self.assertEqual(facts['retention']['content']['closed_at'], case['closed']['lifecycle']['safe_closed_at'])
        return facts

    def assert_registered_content_protected(self, case, body, target, label):
        context = case['context']
        with changes._authority_guards([context.directory]), evidence._context_database(context, write=True) as db:
            consumer = evidence._artifact(case['scope'], body,
                owner=evidence._guidance_artifact_owner(case['scope']), db=db)
            metadata, raw = evidence._managed_artifact(None, consumer, context=context, db=db)
            self.assertEqual(json.loads(raw), body)
            self.assertEqual(metadata['owner'], dict(kind='project', project_id=case['scope'].project_id, task=None))
            self.assertIn(dict(kind='artifact', reference=target), metadata['dependencies'])
            closure = state._metadata(db, case['closed']['lifecycle']['compact']['name'])
        evidence._source_current_permission(context, dict(artifact=case['closed']['lifecycle']['compact'],
            owner=closure['owner'], created_at=closure['created_at'], pins=case['stopped']['pins']))
        before = self.retention_snapshot(case)
        result = self.measure(case, label)
        self.assertEqual(result['error_type'], 'StateError')
        self.assertEqual(result['error'], 'Independent or unclassified content consumer requires closed detail')
        self.assertEqual(result['totals']['retention_facts_core_calls'], 1)
        self.assertEqual(self.retention_snapshot(case), before)
        with evidence._context_database(context) as db:
            self.assertEqual(evidence._read(None, consumer, context=context, db=db), body)
        return result

    @contextmanager
    def declared_bookkeeping_window(self, case):
        """Fixture-user private bookkeeping declaration, never native qualification."""
        scope = case['scope']
        path = scope.authority_path
        original = path.read_bytes()
        before = path.stat()
        window = json.loads(original)
        startup.decode_project_record(window)
        entry = changes._scoped_entries(window)[(scope.repository_id, scope.checkout_id, scope.client)][2]
        self.assertEqual(entry['activation']['status'], 'suspended')
        self.assertEqual(entry['configuration']['shared_dependencies'], [])
        entry['activation'] = dict(status='configured', qualification='pending', accepted_scope_digest=None,
            transaction_id=None, evidence=[], qualification_binding=None)
        window['revision'] += 1
        startup.decode_project_record(window)
        try:
            path.write_text(json.dumps(window, sort_keys=True) + '\n')
            self.assertEqual(json.loads(path.read_bytes()), window)
            self.assertEqual((path.stat().st_dev, path.stat().st_ino), (before.st_dev, before.st_ino))
            yield
        finally:
            path.write_bytes(original)
            path.chmod(before.st_mode & 0o777)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual((path.stat().st_dev, path.stat().st_ino), (before.st_dev, before.st_ino))
            self.assertEqual(path.stat().st_mode & 0o777, before.st_mode & 0o777)
            startup.decode_project_record(json.loads(path.read_bytes()))

    def retired_source(self):
        self.checkpoint('genuine-one-store-shared-umbrella-birth')
        clock = [1_800_000_000]
        timing = patch.object(state, '_owner_time', side_effect=lambda: clock[0])
        timing.start()
        self.addCleanup(timing.stop)
        birth = self.own(CheckedBirthFixture)
        fixture, request, _, directory = birth.prepare(umbrella=True)
        record = request['manifest']['enrollment']['record']
        project_action = next(action for action in request['manifest']['actions'] if action['owner']['kind'] == 'project')
        effect = project_action['effects'][0]
        shared = Path(project_action['owner']['root']) / effect['path']
        record['shared_policy'] = [dict(path=str(shared), sha256=effect['after']['sha256'])]
        for _, _, entry in changes._scoped_entries(record).values():
            entry['configuration']['shared_dependencies'] = [str(shared)]
        request['approval']['manifest_digest'] = onboarding._digest(request['manifest'])
        decision = birth.decision(fixture, request, 'apply', ())
        with review_storage._capture_birth_history(record) as (history, reobserve):
            reobserve()
            intent = changes._birth_intent(fixture.plan(), request['manifest'], history, plan_dir=fixture.directory)
        decision['creation'] = dict(version=1, intent=intent, resume=None)
        prepared = fixture, request, decision, directory
        birth.assert_applied_configured(prepared, birth.invoke(prepared))
        context = birth.context(fixture)
        authority_path = fixture.base / startup.PROJECT_FILE
        birth.qualify_members(fixture, authority_path)
        members = []
        for root, observation in fixture.test_observations.items():
            acquired_facts = []
            def observe(record, facts, observation=observation):
                acquired_facts.append(startup._canonical_plain(facts))
                return observation
            acquired = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(root)),
                state.PACKAGE, qualifier=observe)
            self.assertEqual(acquired.resolution.status, 'active', acquired.resolution)
            self.assertEqual(len(acquired_facts), 1)
            self.assertEqual(acquired_facts[0]['observation']['dependencies'],
                [startup._acquired_dependency(shared, dict(paths=0, bytes=0))])
            members.append(startup._entry_scope(acquired))
        self.assertEqual(len(members), 2)
        self.assertEqual(len({member.repository_id for member in members}), 2)
        self.assertEqual(len({state._scope_id(member) for member in members}), 2)
        self.assertEqual({evidence._evidence_context(member).database for member in members}, {context.database})
        scope = next(member for member in members if member.repository_root != fixture.repo)
        baseline, birth_sources = changes._recovery_sources(context)
        original_sources = self.receipt_snapshot(context, [reference for reference, _ in birth_sources])
        with evidence._context_database(context) as db:
            birth_certificate = evidence._canonical_plain(evidence._read_birth_handoff(context, db)['certificate'])
        original_baseline = {key: evidence._canonical_plain(baseline[key]) for key in ('reference', 'content')}
        identity = dict(client='codex', session_id='retention-owner', task_id='retention-owner', scope_ref='user:retention')
        task = state.start_task(scope, identity | dict(mode='editable', decision_ref='user:accepted'))['task']
        self.assertEqual(task['accepted_identity']['shared_policy'], record['shared_policy'])
        proof = dict(version=1, kind='stopped-writer-reconciliation', identity=identity,
            expected_revision=task['revision'], expected_pins=task['pins'], observed_turn_id=task['turn_id'],
            stopped=True, descendants_stopped=True, attempt_ids=[],
            observation_ref='fixture:synthetic-stopped-no-process-launched', coordinator_ref='fixture:synthetic-independent-owner')
        raw = json.dumps(proof, sort_keys=True).encode()
        path = fixture.directory / 'retention-stop.json'
        path.write_bytes(raw)
        path.chmod(0o600)
        stopped = state.retire_task(scope, identity | dict(expected_revision=task['revision'], expected_pins=task['pins'],
            reason='Observed stopped disposable owner', reference='fixture:synthetic-owner-stop',
            stop_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())))['task']
        self.assertEqual(stopped['state'], 'CANCELLED')
        self.assertEqual(stopped['lifecycle']['created_at'], task['lifecycle']['created_at'])
        reference = evidence._canonical_plain(evidence._artifact_owner(scope, stopped).task)
        proof_reference = stopped['retirement']['proof']
        proof_before = self.receipt_snapshot(context, [proof_reference])[evidence._raw(proof_reference)]
        self.assertEqual(json.loads(proof_before[1]), proof)
        self.checkpoint('historical-member-dependency-removal-and-suspension')
        current = json.loads(authority_path.read_bytes())
        key = (scope.repository_id, scope.checkout_id, scope.client)
        entry = changes._scoped_entries(current)[key][2]
        entry['configuration']['shared_dependencies'] = []
        entry['activation']['status'] = 'suspended'
        current['revision'] += 1
        startup.decode_project_record(current)
        authority_path.write_text(json.dumps(current, sort_keys=True) + '\n')
        setup = self.own(canonical_recovery.CanonicalTwoStoreSetupTests)
        request, retained_files = setup.successor(fixture, [fixture.repo], 'fixture-historical-retention',
            {fixture.repo: context.directory}, shared=True)
        changes._validate_scoped(request, fixture.plan())
        proposed = request['manifest']['enrollment']['record']
        self.assertNotIn(key, {tuple(row[field] for field in ('repository_id', 'checkout_id', 'client'))
            for row in request['manifest']['enrollment']['affected']})
        projection = startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(proposed),
            repository_id=key[0], checkout_id=key[1], client=key[2]))
        self.assertEqual(projection, startup._canonical_plain(startup.canonical_scope_projection(
            startup.decode_project_record(current), repository_id=key[0], checkout_id=key[1], client=key[2])))
        expected = copy.deepcopy(task['accepted_identity'])
        expected['shared_policy'] = []
        self.assertEqual(projection, expected)
        self.assertEqual(changes._scoped_entries(proposed)[key][2]['activation']['status'], 'suspended')
        historical_effects = changes._historical_effects(task['accepted_identity'], projection, request['manifest']['actions'])
        self.assertEqual(len(historical_effects), 1)
        self.checkpoint('owned-d4-update-issues-historical-stop-program')
        decision = birth.decision(fixture, request, 'apply', (context,))
        original_identity = startup._canonical_plain(stopped['accepted_identity'])
        self.assertEqual(original_identity, startup._canonical_plain(scope.accepted_identity))
        self.assertEqual(original_identity, startup._canonical_plain(startup.canonical_scope_projection(
            startup.decode_project_record(record), repository_id=key[0], checkout_id=key[1], client=key[2])))
        self.assertEqual(original_identity['shared_policy'], record['shared_policy'])
        self.assertTrue(original_identity['shared_policy'])
        original_digest = startup._canonical_digest(original_identity)
        self.assertEqual(original_digest, stopped['pins']['accepted_scope_digest'])
        self.assertEqual(stopped['pins'], proof['expected_pins'])
        self.assertEqual({field: stopped[field] for field in ('client', 'session_id', 'task_id', 'scope_ref')}, identity)
        self.assertEqual(reference['scope_id'], state._scope_id(scope))
        self.assertEqual(reference['authority'], evidence._canonical_plain(context.authority_set[0]))
        self.assertEqual(reference['authority'], evidence._canonical_plain(evidence._evidence_context(scope).authority_set[0]))
        self.assertEqual(state._physical_identity(original_identity),
            state._physical_identity(startup._canonical_plain(scope.accepted_identity)))
        with evidence._context_database(context) as db:
            self.assertEqual(state._task(db, reference['scope_id'], identity['client'], identity['session_id'], identity['task_id']), stopped)
        subject = dict(project_id=scope.project_id, repository_id=scope.repository_id,
            checkout_id=scope.checkout_id, client=scope.client)
        self.assertEqual({field: original_identity[field] for field in subject}, subject)
        subjects = [row for row in decision['bookkeeping_scope']['subjects']
            if all(row[field] == value for field, value in subject.items())]
        self.assertEqual(len(subjects), 1)
        self.assertEqual(decision['bookkeeping_scope']['authorities'], [reference['authority']])
        # The trusted fixture includes this observed historical identity; the owner adds actual prior/final projections.
        subjects[0]['accepted_identity_digest'] = original_digest
        prepared = fixture, request, decision, context.directory
        birth.assert_applied_configured(prepared, birth.invoke(prepared))
        authority = evidence._canonical_plain(context.authority_set[0])
        _, sources = changes._recovery_sources(context)
        head, body = next((ref, evidence._canonical_plain(body)) for ref, body in sources
            if body['identity']['plan_dir'] == str(fixture.directory))
        source_identity = body['identity']
        self.assertEqual(body['version'], 1)
        historical_root = dict(purpose='historical-stop', reference=dict(kind='task', reference=reference),
            task=reference, proof=dict(authority=authority, artifact=proof_reference), effect=historical_effects[0])
        self.assertIn(historical_root, body['use_contract']['roots'])
        original_head = head
        original_v1 = self.receipt_snapshot(context, [head])
        self.checkpoint('register-real-recovery-release')
        released = changes._register_recovery_release(changes._observe_recovery_release((authority,), source_identity))
        self.assertTrue(released['complete'], evidence._canonical_plain(released))
        self.checkpoint('ordinary-owner-close-and-retire')
        request = identity | dict(expected_revision=stopped['revision'], expected_pins=stopped['pins'])
        closed = evidence.close_task(scope, request, evidence._observe_closure((context,), reference))['task']
        self.assertTrue(evidence._retire_closed_task(context, reference)['retired'])
        _, sources = changes._recovery_sources(context)
        head, body = next((ref, evidence._canonical_plain(body)) for ref, body in sources if body['identity'] == source_identity)
        self.assertEqual(body['version'], 2)
        certificate = next(row for row in body['stopped'] if row['reference'] == reference)
        self.assertEqual(certificate['retirement']['proof'], stopped['retirement']['proof'])
        self.assertEqual(certificate['created_at'], stopped['lifecycle']['created_at'])
        self.assertEqual(certificate['pins'], stopped['pins'])
        self.assertIn(historical_root, body['use_contract']['roots'])
        self.assertEqual(self.receipt_snapshot(context, [original_head]), original_v1)
        self.assertEqual(self.receipt_snapshot(context, [ref for ref, _ in birth_sources]), original_sources)
        for retained_path, original_file in retained_files.items():
            self.assertEqual(setup.retained_file(retained_path), original_file)
        with evidence._context_database(context) as db:
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            self.assertEqual({field: baseline[field] for field in ('reference', 'content')}, original_baseline)
            self.assertEqual(evidence._canonical_plain(evidence._read_birth_handoff(context, db)['certificate']), birth_certificate)
            self.assertEqual(evidence._read(None, stopped['retirement']['proof'], context=context, db=db), proof)
            proof_after = state._metadata(db, proof_reference['name'])
            self.assertEqual(proof_after, proof_before[0])
            self.assertEqual(state._lifecycle_identity(db, 'task', reference)['status'], 'retired')
        return dict(fixture=fixture, context=context, clock=clock, scope=scope, owner=reference,
            stopped=stopped, closed=closed, head=head, body=body, baseline=baseline, original_proof=proof,
            original_proof_bytes_sha256=hashlib.sha256(proof_before[1]).hexdigest(), original_head=original_head,
            original_v1=original_v1, historical_root=historical_root)

    def measure(self, case, label):
        self.checkpoint(label + '-guarded-retention-facts')
        context = case['context']
        budget = recovery_uses.CaptureBudget()
        measurement = RetentionMeasurement()
        facts = error = error_type = None
        with changes._authority_guards([context.directory]), evidence._context_database(context, write=True, durable=True) as db:
            started = time.monotonic()
            try:
                with measurement.observe(db):
                    facts = evidence._retention_facts(context, db, case['closed']['lifecycle']['compact'], budget=budget)
            except (startup.StartupError, recovery_uses.UseContractError) as failed:
                error = str(failed)
                error_type = type(failed).__name__
            duration = time.monotonic() - started
            result = measurement.result()
            native = [row for row in measurement.core_observations if row['native_entry'] is not None or row['native_exit'] is not None]
            result.update(label=label, acquisition_seconds=duration, capture_used=dict(budget.used), capture_limits=dict(budget.limits),
                native_allocation=native or None, native_allocation_active=bool(native),
                error=error, error_type=error_type, facts=facts)
        return result

    def test_guarded_retention_baseline_and_independent_content_twin(self):
        output = os.environ.get('VCW_C2_MEASUREMENTS')
        report = dict(authority='software fixture: patched clock and explicit synthetic native observations', checks=[], status='incomplete')
        try:
            case = self.retired_source()
            case['clock'][0] = case['closed']['lifecycle']['safe_closed_at'] + 604800
            positive = self.measure(case, 'positive')
            report['positive'] = positive
            self.assertIsNone(positive['error'])
            self.assertEqual(positive['totals']['retention_facts_core_calls'], 1)
            for count in ('physical_open', 'physical_read', 'managed_json_decode_succeeded',
                    'inventory_payload_query', 'owner_payload_query', 'original_support_calls'):
                self.assertGreater(positive['totals'][count], 0)
            facts = positive['facts']
            content = facts['retention']['content']
            self.assertEqual(content['identity'], case['owner'])
            self.assertEqual(content['created_at'], case['stopped']['lifecycle']['created_at'])
            self.assertEqual(content['closed_at'], case['closed']['lifecycle']['safe_closed_at'])
            self.assertEqual(content['stopped']['retirement']['proof'], case['stopped']['retirement']['proof'])
            self.assertEqual(content['pins'], case['stopped']['pins'])
            self.assertTrue(facts['retention']['recovery']['complete'])
            self.assertEqual(case['clock'][0] - content['closed_at'], 604800)
            self.assertEqual(self.receipt_snapshot(case['context'], [case['original_head']]), case['original_v1'])
            report['checks'].append('positive preserves exact identity, age, pins, original v1 proof and complete D4 recovery')
            report['semantics'] = dict(owner=case['owner'], created_at=content['created_at'], closed_at=content['closed_at'],
                observed_at=case['clock'][0], pins=content['pins'], historical_root=case['historical_root'],
                original_v1_head=case['original_head'], original_proof_sha256=case['original_proof_bytes_sha256'])
            context = case['context']
            self.checkpoint('configured-only-independent-content-window')
            with self.declared_bookkeeping_window(case):
                consumer_body = dict(kind='independent-current-proof-consumer', required_proof=case['stopped']['retirement']['proof'])
                with changes._authority_guards([context.directory]), evidence._context_database(context, write=True) as db:
                    consumer = evidence._artifact(case['scope'], consumer_body,
                        owner=evidence._guidance_artifact_owner(case['scope']), db=db)
                    metadata, raw = evidence._managed_artifact(None, consumer, context=context, db=db)
                    self.assertEqual(json.loads(raw), consumer_body)
                    self.assertEqual(metadata['owner'], dict(kind='project', project_id=case['scope'].project_id, task=None))
                    self.assertIn(dict(kind='artifact', reference=case['stopped']['retirement']['proof']), metadata['dependencies'])
                    source_metadata = state._metadata(db, case['closed']['lifecycle']['compact']['name'])
                evidence._source_current_permission(context, dict(artifact=case['closed']['lifecycle']['compact'],
                    owner=source_metadata['owner'], created_at=source_metadata['created_at'], pins=content['pins']))
                before = self.retention_snapshot(case)
                negative = self.measure(case, 'independent-content-negative')
                report['negative'] = negative
                self.assertEqual(negative['error_type'], 'StateError')
                self.assertEqual(negative['error'], 'Independent or unclassified content consumer requires closed detail')
                self.assertEqual(negative['totals']['retention_facts_core_calls'], 1)
                self.assertEqual(self.retention_snapshot(case), before)
                with evidence._context_database(context) as db:
                    self.assertIsNone(evidence._retained_for_owner(context, db, case['owner']))
                    self.assertEqual(evidence._read(None, case['stopped']['retirement']['proof'], context=context, db=db), case['original_proof'])
                    self.assertEqual(evidence._read(None, consumer, context=context, db=db), consumer_body)
            self.assertEqual(self.retention_snapshot(case), before)
            self.assertEqual(self.receipt_snapshot(context, [case['original_head']]), case['original_v1'])
            report['checks'].append('registered independent content rejects at consumer traversal; original proof remains live and unchanged')
            report['status'] = 'passed'
        finally:
            report['last_boundary'] = getattr(self, 'boundary', None)
            cleaned = self.doCleanups()
            report['cleanup_succeeded'] = cleaned
            if not cleaned:
                report['status'] = 'cleanup_failed'
            if output:
                Path(output).write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
            self.assertTrue(cleaned, 'Nested fixture cleanup failed')

    def test_same_pointer_historical_and_registered_content_stays_protected(self):
        case = self.retired_source()
        facts = self.positive_facts(case, 'same-pointer-historical-only')
        proof = case['stopped']['retirement']['proof']
        pointer = dict(authority=case['owner']['authority'], artifact=proof)
        historical = [row for row in facts['retention']['d4_recovery']['occurrences']
            if row['dependency'] == dict(kind='artifact', reference=pointer) and row['purpose'] == 'retention-only']
        self.assertTrue(historical, 'Actual issued historical proof occurrence is required')
        self.assertTrue(any(row['field_path'] and row['witness'] is not None for row in historical))
        with self.declared_bookkeeping_window(case):
            self.assert_registered_content_protected(case,
                dict(kind='independent-same-pointer-content', required_proof=proof), proof, 'same-pointer-content-negative')

    def test_incoming_content_to_superseded_source_keeps_original_proof_protected(self):
        case = self.retired_source()
        facts = self.positive_facts(case, 'superseded-historical-only')
        proof_pointer = dict(authority=case['owner']['authority'], artifact=case['stopped']['retirement']['proof'])
        candidates = []
        with evidence._context_database(case['context']) as db:
            for index, receipt in enumerate(case['body']['supersedes']):
                prior = receipt['artifact']
                if prior == case['head']:
                    continue
                metadata, raw = evidence._managed_artifact(None, prior, context=case['context'], db=db)
                body = json.loads(raw)
                if proof_pointer not in body['artifacts']:
                    continue
                self.assertEqual(body['version'], 1)
                self.assertEqual(body['identity'], receipt['identity'])
                self.assertEqual(body['journal']['sha256'], receipt['journal_sha256'])
                self.assertEqual(metadata['owner'], dict(kind='project', project_id=case['scope'].project_id, task=None))
                self.assertIn(dict(kind='artifact', reference=proof_pointer), metadata['dependencies'])
                self.assertEqual(metadata['created_at'],
                    state._metadata(db, case['head']['name'])['created_at'])
                self.assertTrue(any(row['source'] == case['head'] and row['field_path'] == ['supersedes', index, 'artifact']
                    and row['purpose'] == 'retention-only'
                    and row['dependency'] == dict(kind='recovery', reference=dict(authority=case['owner']['authority'], artifact=prior))
                    for row in facts['retention']['d4_recovery']['occurrences']), 'Actual supersession occurrence is required')
                candidates.append(prior)
        self.assertTrue(candidates, 'Real issued predecessor retaining the proof is required')
        prior = candidates[0]
        with self.declared_bookkeeping_window(case):
            self.assert_registered_content_protected(case,
                dict(kind='independent-superseded-source-content', required_source=prior), prior, 'superseded-content-negative')

    def test_incoming_content_to_current_v2_head_keeps_original_proof_protected(self):
        case = self.retired_source()
        self.positive_facts(case, 'current-head-historical-only')
        self.assertEqual(case['body']['version'], 2)
        proof = case['stopped']['retirement']['proof']
        self.assertTrue(any(certificate['reference'] == case['owner'] and certificate['retirement']['proof'] == proof
            for certificate in case['body']['stopped']))
        self.assertIn(dict(kind='artifact', reference=proof), evidence._artifact_dependencies(case['body']))
        with self.declared_bookkeeping_window(case):
            self.assert_registered_content_protected(case,
                dict(kind='independent-current-head-content', required_source=case['head']), case['head'], 'current-head-content-negative')

    def test_ordinary_birth_named_nested_reference_remains_content(self):
        case = self.retired_source()
        self.positive_facts(case, 'ordinary-birth-named-historical-only')
        proof = case['stopped']['retirement']['proof']
        body = dict(kind='ordinary-unissued-birth-envelope',
            birth_handoff=dict(certificate=dict(required_proof=proof)))
        with self.declared_bookkeeping_window(case):
            self.assert_registered_content_protected(case, body, proof, 'ordinary-birth-named-content-negative')


if __name__ == '__main__':
    unittest.main()
