"""Genuine canonical foreign publication; owner observations are software fixtures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import changes, delegation, evidence, onboarding, review_storage, startup, state
from scripts import onboard_coordinator
from scripts.onboard_coordinator import run_verified_operation
from tests import fixture_canonical_birth as canonical_birth
from tests import test_canonical_composition as canonical


class CanonicalLinkedForeignPublicationTests(unittest.TestCase):
    def setUp(self):
        self.linked = canonical.CanonicalLinkedLifecycleTests('runTest')
        self.addCleanup(self.linked.doCleanups)
        self.linked.setUp()

    def third_source(self):
        birth = canonical_birth.CanonicalBirthFixture('runTest')
        self.addCleanup(birth.doCleanups)
        _, acquisition, _ = birth.accepted_birth()
        scope = startup._entry_scope(acquisition)
        identity = dict(client='codex', session_id='foreign-source', task_id='source',
                        scope_ref='fixture:third-authority-source')
        task = state.start_task(scope, identity | dict(mode='editable', decision_ref='user:accepted'))['task']
        context = evidence._evidence_context(scope)
        self.assertNotIn(context.database, [evidence._evidence_context(member.scope).database
                                           for member in self.linked.members])
        value = dict(version=1, kind='linked-integration-observation',
            inputs_digest=evidence._digest(self.linked.input_rows()), criteria=dict(together='passed'),
            observed_at=101, reference='fixture:synthetic independently checked integration source')
        artifact = evidence._artifact(scope, value, owner=evidence._artifact_owner(scope, task))
        source = dict(authority=evidence._canonical_plain(context.authority_set[0]), reference=artifact)
        observations = self.linked.observations()
        original = observations['observe']
        calls = []

        def observe(facts):
            calls.append(True)
            acquired = original(facts)
            acquired['shared'][0]['evidence'] = [source]
            return acquired

        observations['observe'] = observe
        with evidence._context_database(context) as db:
            metadata, raw = evidence._managed_artifact(None, artifact, context=context, db=db)
        mandate = evidence._source_mandate(context, artifact)
        self.assertEqual(mandate['metadata']['owner'], metadata['owner'])
        self.assertEqual(mandate['metadata']['created_at'], metadata['created_at'])
        self.assertEqual(mandate['content']['source']['managed_binding'], metadata['managed'])
        retained = dict(metadata=copy.deepcopy(metadata), raw=raw, mandate=copy.deepcopy(mandate))
        return scope, context, source, retained, observations, calls

    def assert_source_retained(self, context, source, retained):
        self.assertEqual(state._authority_identity(context.database), source['authority'])
        with evidence._context_database(context) as db:
            metadata, raw = evidence._managed_artifact(None, source['reference'], context=context, db=db)
        self.assertEqual(metadata, retained['metadata'])
        self.assertEqual(raw, retained['raw'])
        self.assertEqual(evidence._source_mandate(context, source['reference']), retained['mandate'])

    def test_normal_foreign_publication_replays_exact_original_receipt(self):
        _, context, source, retained, observations, calls = self.third_source()
        original = evidence._commit_foreign_intent
        receipts = []

        def commit_and_replay(plan, selected):
            first = original(plan, selected)
            if selected == source:
                with patch.object(evidence.uuid, 'uuid4', side_effect=AssertionError('new replay identity')):
                    second = original(plan, selected)
                receipts.extend((copy.deepcopy(first), copy.deepcopy(second)))
            return first

        with patch.object(evidence, '_commit_foreign_intent', side_effect=commit_and_replay):
            result = self.linked.verify(observations)
        self.assertTrue(result['implementation_verified'], result)
        self.assertEqual(result['task']['state'], 'DONE')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0], receipts[1])
        self.assertEqual(receipts[0]['metadata']['owner'], retained['metadata']['owner'])
        self.assertEqual(receipts[0]['metadata']['created_at'], retained['metadata']['created_at'])
        self.assertEqual(receipts[0]['content']['source']['artifact'], source['reference'])
        self.assertEqual(evidence._source_intents(context, source['reference']), [receipts[0]])
        self.assert_source_retained(context, source, retained)
        self.assertTrue(evidence.evidence_status(self.linked.selected.scope, 'codex', 'parent')
                        ['evidence']['implementation_verified'])

    def test_durable_intent_survives_interruption_and_rerun_cannot_claim_readiness(self):
        _, context, source, retained, observations, calls = self.third_source()
        original = evidence._commit_foreign_intent
        committed = []

        def commit_then_interrupt(plan, selected):
            receipt = original(plan, selected)
            if selected == source:
                # Original returns only after the actual source SQLite COMMIT.
                committed.append(copy.deepcopy(receipt))
                raise state.StateError('fixture: interrupt after durable source commit')
            return receipt

        with patch.object(evidence, '_commit_foreign_intent', side_effect=commit_then_interrupt):
            with self.assertRaisesRegex(state.StateError, 'after durable source commit'):
                self.linked.verify(observations)
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]['metadata']['created_at'], retained['metadata']['created_at'])
        self.assertEqual(evidence._source_intents(context, source['reference']), committed)
        self.assert_source_retained(context, source, retained)
        selected = self.linked.task(self.linked.selected)
        self.assertNotIn('linked_verification', selected['evidence'])
        self.assertNotEqual(selected['state'], 'DONE')
        with patch.object(evidence, '_verify_member', side_effect=AssertionError('producer reran')), \
                patch.object(evidence.uuid, 'uuid4', side_effect=AssertionError('fresh allowance')):
            with self.assertRaisesRegex(state.StateError, 'explicit operation recovery'):
                self.linked.verify(observations)
        self.assertEqual(len(calls), 1)
        self.assertEqual(evidence._source_intents(context, source['reference']), committed)
        self.assert_source_retained(context, source, retained)
        self.assertFalse(evidence.evidence_status(self.linked.selected.scope, 'codex', 'parent')
                         ['evidence']['implementation_verified'])

    def test_lost_current_permission_keeps_committed_intent_and_destination_unpublished(self):
        scope, context, source, retained, observations, calls = self.third_source()
        original = evidence._commit_foreign_intent
        committed = []

        def commit_then_remove_permission(plan, selected):
            receipt = original(plan, selected)
            if selected == source:
                committed.append(copy.deepcopy(receipt))
                scope.authority_path.unlink()  # Disposable genuine source enrollment only.
            return receipt

        with patch.object(evidence, '_commit_foreign_intent', side_effect=commit_then_remove_permission):
            with self.assertRaises(state.StateError):
                self.linked.verify(observations)
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]['metadata']['created_at'], retained['metadata']['created_at'])
        self.assertFalse(scope.authority_path.exists())
        self.assertEqual(evidence._source_intents(context, source['reference']), committed)
        self.assert_source_retained(context, source, retained)
        selected = self.linked.task(self.linked.selected)
        self.assertNotIn('linked_verification', selected['evidence'])
        self.assertNotEqual(selected['state'], 'DONE')
        self.assertFalse(evidence.evidence_status(self.linked.selected.scope, 'codex', 'parent')
                         ['evidence']['implementation_verified'])
        self.assertEqual(len(calls), 1)
        with patch.object(evidence, '_verify_member', side_effect=AssertionError('producer reran')), \
                patch.object(evidence.uuid, 'uuid4', side_effect=AssertionError('fresh allowance')):
            with self.assertRaises(state.StateError):
                self.linked.verify(observations)
        self.assertEqual(evidence._source_intents(context, source['reference']), committed)
        self.assert_source_retained(context, source, retained)
        self.assertFalse(scope.authority_path.exists())


class CanonicalTwoStoreSetupTests(unittest.TestCase):
    def retained_file(self, path):
        info = path.stat()
        return path.read_bytes(), info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns

    def successor(self, fixture, members, transaction, directories, *, shared=False):
        previous = fixture.directory
        protected = {previous / 'transaction.json': self.retained_file(previous / 'transaction.json')}
        sources = copy.deepcopy(fixture.record)
        private = tempfile.TemporaryDirectory(prefix='vcw-recovery-plan-')
        self.addCleanup(private.cleanup)
        fixture.directory = Path(private.name).resolve() / 'plan'
        fixture.directory.mkdir(mode=0o700)
        for item in sources['toolkit']['files']:
            original = Path(item['local_path'])
            protected[original] = self.retained_file(original)
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), item['sha256'])
            copied = fixture.directory / item['path']
            copied.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            copied.write_bytes(original.read_bytes())
            copied.chmod(0o400)
            item['local_path'] = str(copied)
        fixture.record = sources
        fixture.source = Path(sources['toolkit']['files'][0]['local_path'])
        source_record = fixture.project_record(fixture.base, members, 'umbrella', ['AGENTS.md'])
        onboarding.inspect_project(fixture.base, fixture.directory, source_record)
        proposal = fixture.scoped_proposal()
        text = 'product rules\n' + transaction + '\n'
        for assessment in onboarding._assessments(proposal['assessments']):
            assessment['actions'][0].update(text=text, after=hashlib.sha256(text.encode()).hexdigest())
        onboarding.record_plan(fixture.directory, proposal)
        request = fixture.scoped_request(prepared=True)
        manifest = request['manifest']
        manifest['transaction_id'] = transaction
        record = manifest['enrollment']['record']
        selected = {Path(member) for member in members}
        shared_path = str(fixture.base / 'AGENTS.md')
        if shared:
            record['shared_policy'] = [dict(path=shared_path, sha256=hashlib.sha256(text.encode()).hexdigest())]
        for _, checkout, entry in changes._scoped_entries(record).values():
            if Path(checkout['root']) not in selected:
                continue
            entry['configuration']['runtime'] = dict(schema_version=3, state_dir=str(directories[Path(checkout['root'])]))
            if shared:
                entry['configuration']['shared_dependencies'] = [shared_path]
        request['approval']['manifest_digest'] = onboarding._digest(manifest)
        for path, retained in protected.items():
            self.assertEqual(self.retained_file(path), retained)
        return request, protected

    def store_history(self, context):
        with evidence._context_database(context) as db:
            baseline = state._source_baseline(None, db, context=context)
            certificate = evidence._canonical_plain(evidence._read_birth_handoff(context, db)['certificate'])
            rows = db.execute('select scope_id,physical_identity from checkouts order by scope_id').fetchall()
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0], 0)
        return dict(authority=state._authority_identity(context.database),
            baseline={key: evidence._canonical_plain(baseline[key]) for key in ('reference', 'content')},
            certificate=certificate, checkouts=rows)

    def prepare_existing_two_store_umbrella(self, *, before_final=None):
        birth = canonical_birth.CanonicalBirthFixture('runTest')
        self.addCleanup(birth.doCleanups)
        initial = birth.prepare(umbrella=True)
        birth.assert_applied_configured(initial, birth.invoke(initial))
        fixture = initial[0]
        original_record = initial[1]['manifest']['enrollment']['record']
        original_members = changes._scoped_entries(original_record)
        self.assertEqual(len(original_members), 2)
        roots = [Path(checkout['root']) for _, checkout, _ in original_members.values()]
        first = birth.context(fixture)
        first_history = self.store_history(first)
        self.assertEqual(first_history['baseline']['content']['runtime_digest'], state.runtime_fingerprint())
        original_plan = fixture.directory
        second_directory = first.directory.parent / 'canonical-state-second'
        self.assertFalse(second_directory.exists())
        request, originals = self.successor(fixture, [roots[1]], 'fixture-second-owned-birth',
            {roots[1]: second_directory})
        decision = birth.decision(fixture, request, 'apply', (first,))
        with review_storage._capture_birth_history(request['manifest']['enrollment']['record']) as (history, reobserve):
            reobserve()
            intent = changes._birth_intent(fixture.plan(), request['manifest'], history, plan_dir=fixture.directory)
        self.assertEqual(intent['destination']['directory'], str(second_directory))
        self.assertEqual([peer['authority'] for peer in intent['peers']], [first_history['authority']])
        decision['creation'] = dict(version=1, intent=intent, resume=None)
        second_prepared = fixture, request, decision, second_directory
        birth.assert_applied_configured(second_prepared, birth.invoke(second_prepared))
        second_plan = fixture.directory
        second = birth.context(fixture)
        self.assertNotEqual(first.database_binding, second.database_binding)
        self.assertEqual(self.store_history(first), first_history)
        second_history = self.store_history(second)
        self.assertEqual(second_history['baseline']['content']['runtime_digest'], state.runtime_fingerprint())
        self.assertEqual(second_history['certificate']['intent']['identity']['plan_dir'], str(second_plan))
        self.assertEqual(first_history['certificate']['intent']['identity']['plan_dir'], str(original_plan))
        current = request['manifest']['enrollment']['record']
        self.assertEqual({key: current[key] for key in ('project_id', 'project_root', 'project_binding', 'kind')},
                         {key: original_record[key] for key in ('project_id', 'project_root', 'project_binding', 'kind')})
        self.assertEqual(set(changes._scoped_entries(current)), set(original_members))
        for key, (repository, checkout, _) in changes._scoped_entries(current).items():
            old_repository, old_checkout, _ = original_members[key]
            self.assertEqual(repository['binding'], old_repository['binding'])
            self.assertEqual(checkout['binding'], old_checkout['binding'])
            self.assertEqual(checkout['root'], old_checkout['root'])
            self.assertEqual(state._physical_identity(changes._scoped_projection(current, key)),
                             state._physical_identity(changes._scoped_projection(original_record, key)))
        if before_final is not None:
            before_final(birth, fixture, (first, second))
        final_request, previous = self.successor(fixture, roots, 'fixture-two-store-recovery',
            {roots[0]: first.directory, roots[1]: second.directory}, shared=True)
        originals.update(previous)
        final_decision = birth.decision(fixture, final_request, 'apply', (first, second))
        prepared = fixture, final_request, final_decision, first.directory
        return birth, prepared, (first, second), (first_history, second_history), originals

    def test_sequential_owned_births_prepare_current_two_store_operation(self):
        _, prepared, contexts, histories, originals = self.prepare_existing_two_store_umbrella()
        fixture, request, decision, _ = prepared
        self.assertFalse((fixture.directory / 'transaction.json').exists())
        self.assertNotIn('creation', decision)
        self.assertEqual(len(request['manifest']['enrollment']['affected']), 2)
        self.assertEqual({Path(entry['configuration']['runtime']['state_dir'])
            for _, _, entry in changes._scoped_entries(request['manifest']['enrollment']['record']).values()},
            {context.directory for context in contexts})
        changes._validate_scoped(request, fixture.plan())
        for action in request['manifest']['actions']:
            for effect in action['effects']:
                self.assertEqual(changes._fingerprint(Path(action['owner']['root']), effect['path']), effect['before'])
                self.assertNotEqual(effect['before'], effect['after'])
        for context, history in zip(contexts, histories):
            self.assertEqual(self.store_history(context), history)
            self.assertEqual(history['baseline']['content']['project_id'], request['manifest']['enrollment']['record']['project_id'])
        for path, retained in originals.items():
            self.assertEqual(self.retained_file(path), retained)

    def test_actual_partial_peer_commit_resumes_original_identity_and_age(self):
        birth, prepared, contexts, histories, originals = self.prepare_existing_two_store_umbrella()
        fixture, request, decision, _ = prepared
        project = Path(request['manifest']['enrollment']['record']['project_root'])
        authority_path = project / changes.PROJECT_FILE
        original_authority = self.retained_file(authority_path)
        effects = {(action['id'], effect['path']): changes._fingerprint(Path(action['owner']['root']), effect['path'])
            for action in request['manifest']['actions'] for effect in action['effects']}
        before = {}
        for context in contexts:
            with evidence._context_database(context) as db:
                before[str(context.database)] = evidence._canonical_plain(state._source_baseline(None, db, context=context))
        original = evidence._register_recovery_source
        first = []
        durable = {}
        rolled_back = {}

        def interrupt_second_peer(scope, db, source, *, context=None):
            reference = original(scope, db, source, context=context)
            if not first:
                first.append(context)
            elif context.database != first[0].database and not durable:
                # A separate connection proves the first actual transaction committed.
                with evidence._context_database(first[0]) as reader:
                    baseline = evidence._canonical_plain(state._source_baseline(None, reader, context=first[0]))
                    identity = evidence._canonical_plain(source.content)['identity']
                    edges = [edge for edge in baseline['dependencies'] if edge['reference']['identity'] == identity]
                    self.assertEqual(len(edges), 1)
                    head = edges[0]['reference']['artifact']
                    metadata, raw = evidence._managed_artifact(None, head, context=first[0], db=reader)
                    body = evidence._canonical_plain(evidence._read(None, head, context=first[0], db=reader))
                    self.assertEqual(metadata['created_at'], source.created_at)
                    self.assertEqual(body['journal']['state'], 'prepared')
                    durable.update(context=first[0], identity=identity, head=head, body=body,
                        metadata=copy.deepcopy(metadata), raw=raw)
                second_reference = evidence._canonical_plain(reference)
                self.assertIsNotNone(state._metadata(db, second_reference['name']))
                rolled_back.update(context=context, reference=second_reference)
                raise changes.StartupError('fixture: interrupted second peer registration')
            return reference

        with patch.object(evidence, '_register_recovery_source', side_effect=interrupt_second_peer):
            failed = birth.invoke(prepared)
        self.assertEqual(failed['exit_code'], 2, failed)
        self.assertFalse(failed['qualified'], failed)
        self.assertTrue(durable, failed)
        self.assertTrue(rolled_back, failed)
        journal = changes._load_journal(fixture.directory)
        operation = {key: journal[key] for key in ('transaction_id', 'manifest_digest')}
        self.assertEqual(durable['identity'], dict(plan_dir=str(fixture.directory), **operation))
        self.assertEqual(journal['manifest'], request['manifest'])
        self.assertEqual(journal['approval'], request['approval'])
        self.assertIsNone(journal['enrollment'])
        self.assertEqual(journal['receipts'], {})
        self.assertEqual(self.retained_file(authority_path), original_authority)
        for action in request['manifest']['actions']:
            for effect in action['effects']:
                self.assertEqual(changes._fingerprint(Path(action['owner']['root']), effect['path']),
                                 effects[(action['id'], effect['path'])])
        with evidence._context_database(rolled_back['context']) as db:
            self.assertEqual(evidence._canonical_plain(state._source_baseline(None, db, context=rolled_back['context'])),
                             before[str(rolled_back['context'].database)])
            self.assertIsNone(state._metadata(db, rolled_back['reference']['name']))
        for context, history in zip(contexts, histories):
            self.assertEqual(self.store_history(context), history)
        markers = changes._authority_locations([context.directory for context in contexts])
        markers.append(Path(changes.LOCK_ROOT) / ('canonical-' + hashlib.sha256(
            ('destination:' + str(authority_path)).encode()).hexdigest() + '.lock'))
        owner = dict(schema_version=1, **durable['identity'])
        for marker in markers:
            self.assertEqual(changes._authority_owner(marker), owner)
        authorities = [evidence._canonical_plain(context.authority_set[0]) for context in contexts]
        inspection = changes.inspect_lifecycle_recovery(authorities, ())
        self.assertFalse(inspection['complete'], inspection)
        self.assertTrue(any(row.get('reason') == 'active_or_uncertain_operation' for row in inspection['blockers']))
        with evidence._context_database(durable['context']) as db:
            metadata, raw = evidence._managed_artifact(None, durable['head'], context=durable['context'], db=db)
            self.assertEqual((metadata, raw), (durable['metadata'], durable['raw']))

        recovery = birth.decision(fixture, request, 'resume', contexts)
        self.assertEqual(recovery['approved'], decision['approved'])
        resumed_registrations = []

        def observe_resume(scope, db, source, *, context=None):
            reference = original(scope, db, source, context=context)
            self.assertEqual(evidence._canonical_plain(source.content)['identity'], durable['identity'])
            self.assertEqual(source.created_at, durable['metadata']['created_at'])
            for marker in markers:
                self.assertEqual(changes._authority_owner(marker), owner)
            resumed_registrations.append((str(context.database), source.content['journal']['state']))
            return reference

        with patch.object(evidence, '_register_recovery_source', side_effect=observe_resume):
            resumed = run_verified_operation(recovery, fixture.directory, copy.deepcopy(request))
        birth.assert_applied_configured(prepared, resumed)
        self.assertEqual({database for database, phase in resumed_registrations if phase == 'applied'},
                         {str(context.database) for context in contexts})
        journal = changes._load_journal(fixture.directory)
        self.assertEqual({key: journal[key] for key in operation}, operation)
        self.assertEqual(journal['manifest'], request['manifest'])
        self.assertEqual(journal['approval'], request['approval'])
        final_bodies = []
        for context, history in zip(contexts, histories):
            self.assertEqual(self.store_history(context), history)
            with evidence._context_database(context) as db:
                baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
                edges = [edge for edge in baseline['dependencies'] if edge['reference']['identity'] == durable['identity']]
                self.assertEqual(len(edges), 1)
                head = edges[0]['reference']['artifact']
                chain = evidence._recovery_source_history(context, db, head, durable['identity'])
                references = {evidence._raw(reference) for reference, _ in chain}
                self.assertEqual(len(chain), len(references))
                committed = set()
                for name, digest in db.execute("select name,digest from evidence_metadata where class='compact'").fetchall():
                    reference = dict(name=name, digest=digest)
                    _, raw = evidence._managed_artifact(None, reference, context=context, db=db)
                    content = json.loads(raw)
                    if content.get('kind') == 'd4-recovery-source' and content.get('identity') == durable['identity']:
                        committed.add(evidence._raw(reference))
                self.assertEqual(committed, references)
                for reference, body in chain:
                    metadata = state._metadata(db, reference['name'])
                    self.assertEqual(body['identity'], durable['identity'])
                    self.assertEqual(metadata['created_at'], durable['metadata']['created_at'])
                    self.assertEqual(metadata['owner'], durable['metadata']['owner'])
                if context.database == durable['context'].database:
                    retained = next(body for reference, body in chain if reference == durable['head'])
                    self.assertEqual(evidence._canonical_plain(retained), durable['body'])
                    metadata, raw = evidence._managed_artifact(None, durable['head'], context=context, db=db)
                    self.assertEqual((metadata, raw), (durable['metadata'], durable['raw']))
                body = evidence._canonical_plain(evidence._read(None, head, context=context, db=db))
                self.assertEqual(body['journal']['state'], 'applied')
                self.assertEqual(body['journal']['sha256'], hashlib.sha256((fixture.directory / 'transaction.json').read_bytes()).hexdigest())
                self.assertEqual({evidence._raw(authority) for authority in body['authorities']},
                                 {evidence._raw(authority) for authority in authorities})
                body.pop('previous')
                final_bodies.append(body)
        self.assertEqual(final_bodies[0], final_bodies[1])
        for marker in markers:
            self.assertIsNone(changes._authority_owner(marker))
        for path, retained in originals.items():
            self.assertEqual(self.retained_file(path), retained)


    def history_sources(self, certificate):
        history = certificate['intent']['history']
        configuration = history['machine']['configuration']
        context = evidence._reference_context(configuration['authority'])
        references = [row['reference'] for row in history['inventory'] if row['reference'] in history['related']]
        references.append(configuration['provenance'])
        metadata = {}
        files = {Path(history['machine']['path'])}
        with evidence._context_database(context) as db:
            for reference in references:
                record, _ = evidence._managed_artifact(None, reference, context=context, db=db)
                metadata[reference['name']] = copy.deepcopy(record)
                files.add(Path(record['managed']['path']))
        for source in history['screening_sources']:
            files.add(Path(source['pointer']['plan_directory']) / 'plan.json')
        return metadata, {path: self.retained_file(path) for path in files}

    def assert_retained_history_refused(self, certificate, stage):
        try:
            review_storage._retained_birth_history(certificate)
        except state.StartupError as error:
            trace = error.__traceback__
            observed = None
            while trace is not None:
                if trace.tb_frame.f_code.co_name == stage:
                    observed = dict(trace.tb_frame.f_locals)
                trace = trace.tb_next
            self.assertIsNotNone(observed, 'Refusal did not reach ' + stage + ': ' + str(error))
            return observed, str(error)
        self.fail('Changed original history was accepted')

    def test_retained_peer_birth_replays_complete_original_related_history(self):
        _, prepared, contexts, histories, originals = self.prepare_existing_two_store_umbrella()
        certificate = histories[1]['certificate']
        self.assertEqual(len(certificate['intent']['members']), 1)
        record = prepared[1]['manifest']['enrollment']['record']
        expected_targets = {record['project_root']} | {
            checkout['root'] for _, checkout, _ in changes._scoped_entries(record).values()}
        history = certificate['intent']['history']
        related = [row for row in history['inventory'] if row['reference'] in history['related']]
        self.assertTrue(related)
        self.assertEqual({target['path'] for row in related for target in row['metadata']['targets']}, expected_targets)
        self.assertEqual(len(expected_targets), 3)
        before = self.history_sources(certificate)
        review_storage._retained_birth_history(certificate)
        self.assertEqual(self.history_sources(certificate), before)
        for row in related:
            self.assertEqual(before[0][row['reference']['name']], row['metadata'])
        for context, retained in zip(contexts, histories):
            self.assertEqual(self.store_history(context), retained)
        for path, retained in originals.items():
            self.assertEqual(self.retained_file(path), retained)

    def test_retained_peer_birth_rejects_changed_original_history_sources(self):
        _, prepared, contexts, histories, originals = self.prepare_existing_two_store_umbrella()
        certificate = histories[1]['certificate']
        history = certificate['intent']['history']
        related = [row for row in history['inventory'] if row['reference'] in history['related']]
        self.assertTrue(related)
        sibling = next(checkout['root'] for _, checkout, entry in changes._scoped_entries(
            prepared[1]['manifest']['enrollment']['record']).values()
            if entry['configuration']['runtime']['state_dir'] == str(contexts[0].directory))
        before = self.history_sources(certificate)

        def unchanged():
            review_storage._retained_birth_history(certificate)
            self.assertEqual(self.history_sources(certificate), before)
            for context, retained in zip(contexts, histories):
                self.assertEqual(self.store_history(context), retained)
            self.assertFalse((prepared[0].directory / 'transaction.json').exists())

        unchanged()
        with self.subTest(source='underdeclared-related-owner'):
            changed = copy.deepcopy(certificate)
            changed['intent']['history']['related'] = []
            observed, _ = self.assert_retained_history_refused(changed, '_birth_history_snapshot')
            self.assertTrue(observed['related'])
            self.assertTrue(any(target['path'] not in observed['allowed']
                for owner in observed['related'] for target in owner['targets']))
        unchanged()

        with self.subTest(source='missing-original-owner-body'):
            source = Path(related[0]['metadata']['managed']['path'])
            retained = prepared[0].directory / 'held-original-owner.json'
            source.rename(retained)
            try:
                observed, _ = self.assert_retained_history_refused(certificate, '_owners')
                self.assertNotEqual(observed['actual'], observed['names'])
            finally:
                retained.rename(source)
        unchanged()

        with self.subTest(source='wrong-original-peer-binding'):
            changed = copy.deepcopy(certificate)
            target = next(target for row in changed['intent']['history']['inventory']
                if row['reference'] in history['related'] for target in row['metadata']['targets']
                if target['path'] == sibling)
            for binding in (target['binding'], target['git']['binding']['root'], target['git']['binding']['primary_root']):
                binding['inode'] += 1
            observed, _ = self.assert_retained_history_refused(changed, 'observe')
            self.assertEqual(observed['selected']['root'], sibling)
            self.assertNotEqual(observed['observed']['binding']['root'], observed['expected']['binding']['root'])
        unchanged()

        with self.subTest(source='conflicting-original-project-root'):
            changed = copy.deepcopy(certificate)
            changed['intent']['members'][0]['physical_identity']['project_binding']['inode'] += 1
            observed, _ = self.assert_retained_history_refused(changed, '_retained_birth_history')
            self.assertEqual(observed['target']['path'], prepared[1]['manifest']['enrollment']['record']['project_root'])
            self.assertNotEqual(observed['targets'][observed['target']['path']], observed['observed'])
        unchanged()

        with self.subTest(source='replaced-physical-peer'):
            source = Path(sibling)
            retained = source.with_name(source.name + '-original-history')
            source.rename(retained)
            source.mkdir(mode=0o700)
            try:
                observed, _ = self.assert_retained_history_refused(certificate, 'observe')
                self.assertEqual(observed['selected']['root'], sibling)
                self.assertIsNotNone(observed['error'])
            finally:
                source.rmdir()
                retained.rename(source)
        unchanged()

        with self.subTest(source='changed-original-screening-payload'):
            self.assertTrue(history['screening_sources'])
            source = Path(history['screening_sources'][0]['pointer']['plan_directory']) / 'plan.json'
            info = source.stat()
            raw = source.read_bytes()
            changed = json.loads(raw)
            changed['screening']['payload']['timing']['elapsed_ms'] += 1
            source.write_text(json.dumps(changed))
            try:
                _, reason = self.assert_retained_history_refused(certificate, '_read_onboarding_screening_source')
                self.assertEqual(reason, 'Screening source payload changed')
            finally:
                source.write_bytes(raw)
                os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
        unchanged()

        with self.subTest(source='replaced-original-machine-configuration'):
            source = Path(history['machine']['path'])
            retained = source.with_name('held-original-machine.json')
            raw = source.read_bytes()
            source.rename(retained)
            source.write_bytes(raw)
            source.chmod(0o600)
            try:
                observed, _ = self.assert_retained_history_refused(certificate, '_retained_birth_history')
                self.assertNotEqual(observed['actual']['machine'], observed['expected']['machine'])
            finally:
                source.unlink()
                retained.rename(source)
        unchanged()
        for path, retained in originals.items():
            self.assertEqual(self.retained_file(path), retained)


    def test_retained_linked_birth_preserves_primary_project_and_git_roles(self):
        birth = canonical_birth.CanonicalBirthFixture('runTest')
        self.addCleanup(birth.doCleanups)
        original_request = canonical_birth.ScopedBirthFixture.scoped_request
        originals = {}
        accounting = {}

        def primary_owner_before_linked(fixture, **options):
            self.assertTrue(options['linked'])
            self.assertFalse((fixture.base / 'linked').exists())
            onboarding.inspect_project(fixture.repo, fixture.directory, fixture.record)
            prior_plan = fixture.plan()
            accounting.update(copy.deepcopy(prior_plan['screening']['payload']['accounting']))
            originals[fixture.directory / 'plan.json'] = self.retained_file(fixture.directory / 'plan.json')
            sources = copy.deepcopy(fixture.record)
            fixture.directory = fixture.base / 'linked-birth-plan'
            fixture.directory.mkdir(mode=0o700)
            for item in sources['toolkit']['files']:
                source = Path(item['local_path'])
                originals[source] = self.retained_file(source)
                copied = fixture.directory / item['path']
                copied.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                copied.write_bytes(source.read_bytes())
                copied.chmod(0o400)
                item['local_path'] = str(copied)
            fixture.record = sources
            fixture.source = Path(sources['toolkit']['files'][0]['local_path'])
            return original_request(fixture, **options)

        with patch.object(canonical_birth.ScopedBirthFixture, 'scoped_request', new=primary_owner_before_linked):
            prepared = birth.prepare(linked=True)
        birth.assert_applied_configured(prepared, birth.invoke(prepared))
        context = birth.context(prepared[0])
        retained = self.store_history(context)
        certificate = retained['certificate']
        self.assertEqual(len(certificate['intent']['members']), 1)
        member = certificate['intent']['members'][0]['physical_identity']
        self.assertNotEqual(member['project_root'], member['repository_root'])
        history = certificate['intent']['history']
        owner = next(row for row in history['inventory'] if row['reference'] == accounting['reference'])
        self.assertIn(owner['reference'], history['related'])
        self.assertEqual(owner['metadata']['owner'], accounting['owner'])
        self.assertEqual(owner['metadata']['created_at'], accounting['created_at'])
        primary = next(target for target in owner['metadata']['targets'] if target['path'] == member['project_root'])
        self.assertIsNotNone(primary['git'])
        supplied = review_storage._record_targets(prepared[1]['manifest']['enrollment']['record'])
        self.assertTrue(any(target['path'] == primary['path'] and target['git'] is None for target in supplied))
        before = self.history_sources(certificate)
        review_storage._retained_birth_history(certificate)
        self.assertEqual(self.history_sources(certificate), before)
        self.assertEqual(self.store_history(context), retained)
        for path, original in originals.items():
            self.assertEqual(self.retained_file(path), original)


    def test_stopped_member_body_precedes_peer_reverse_edge_and_survives_d4(self):
        stopped = {}

        def prepare_stopped_member(birth, fixture, contexts):
            origin = contexts[1]
            authority_path = fixture.base / changes.PROJECT_FILE
            fixture, _, _ = birth.qualify_members(fixture, authority_path)
            scope = next(startup._entry_scope(acquired) for acquired in fixture.member_acquisitions.values()
                if startup._entry_scope(acquired).runtime.state_dir == origin.directory)
            self.assertEqual(scope.qualification_provenance['reference'], 'fixture:synthetic-trusted-owner-only')
            identity = dict(client='codex', session_id='stopped-member-b', task_id='stopped-member-b',
                scope_ref='fixture:synthetic-stopped-member-b')
            task = state.start_task(scope, identity | dict(mode='editable', decision_ref='user:fixture-accepted'))['task']
            task_created_at = task['lifecycle']['created_at']
            reservation = delegation.reserve_coordinator(scope, identity | dict(expected_revision=task['revision'],
                dispatch_id='member-b-observation', claims=[dict(kind='path', key=str(scope.repository_root / 'AGENTS.md'), mode='write')],
                operation=dict(id='member-b-observation', kind='coordinator-work'), reason='Observe one actual fixture owner',
                ownership_ref='fixture:synthetic-independent-owner', stop_conditions=['Stop after local owner observation']))
            with state._database(scope) as db:
                task = state._task(db, state._scope_id(scope), identity['client'], identity['session_id'], identity['task_id'])
                record = next(row for row in delegation._ledger(scope, db) if row['id'] == reservation['attempt_id'])
            self.assertEqual(record['data']['role'], 'coordinator')
            self.assertEqual(task['lifecycle']['created_at'], task_created_at)
            facts = dict(operation_id='member-b-observation', operation_digest=record['body']['operation']['digest'],
                claims_digest=evidence._digest(record['data']['claims']))
            publication_started_at = state._owner_time()
            body = evidence._artifact(scope, facts | dict(observation='Actual local reserved owner; no process or descendants launched'),
                owner=evidence._artifact_owner(scope, task))
            publication_finished_at = state._owner_time()

            def host_proof(name, value):
                path = fixture.directory / name
                raw = json.dumps(value).encode()
                path.write_bytes(raw)
                path.chmod(0o600)
                return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())

            proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=identity,
                expected_pins=task['pins'], expected_revision=task['revision'], scope_id=record['owner']['scope_id'],
                attempt_id=record['id'], dispatch_id=record['owner']['dispatch_id'],
                assignment_digest=record['data']['assignment']['digest'], decision_ref=task['decision_ref'],
                operation_evidence=body, resource_observations=body, operation_stopped=True,
                operation_descendants_stopped=True, observation_ref='fixture:synthetic-observed-no-process-tree',
                coordinator_ref='fixture:synthetic-independent-operation-owner', **facts)
            reconciled = delegation.reconcile_assignment(scope, identity | dict(expected_revision=task['revision'],
                attempt_id=record['id'], host_proof=host_proof('member-b-operation-proof.json', proof),
                assessment=dict(outcome='cancelled', changed_paths=[], result_assessment='Reserved observation ended without effects',
                    partial_change_assessment='No process launched and no remaining effects',
                    evidence=['fixture:synthetic-independent-local-observation'], coordinator_ref='fixture:synthetic-owner')))
            self.assertEqual(reconciled['outcome'], 'cancelled')
            task = state.task_status(scope, identity['client'], identity['session_id'])['task']
            self.assertEqual(task['lifecycle']['created_at'], task_created_at)
            proof = dict(version=1, kind='stopped-writer-reconciliation', identity=identity,
                expected_revision=task['revision'], expected_pins=task['pins'], observed_turn_id=task['turn_id'],
                stopped=True, descendants_stopped=True, attempt_ids=[],
                observation_ref='fixture:synthetic-observed-task-and-descendants-stopped',
                coordinator_ref='fixture:synthetic-independent-task-owner')
            retired = state.retire_task(scope, identity | dict(expected_revision=task['revision'], expected_pins=task['pins'],
                reason='Observed stopped disposable member', reference='fixture:synthetic-retirement',
                stop_proof=host_proof('member-b-task-stop.json', proof)))['task']
            self.assertEqual(retired['state'], 'CANCELLED')
            self.assertEqual(retired['lifecycle']['created_at'], task_created_at)
            with evidence._context_database(origin) as db:
                settled = next(row for row in delegation._ledger(None, db, context=origin) if row['id'] == record['id'])
                self.assertEqual(settled['data']['status'], 'settled')
                metadata, raw = evidence._managed_artifact(None, body, context=origin, db=db)
                self.assertEqual(metadata['owner']['task']['authority'], evidence._canonical_plain(origin.authority_set[0]))
                self.assertGreaterEqual(metadata['created_at'], publication_started_at)
                self.assertLessEqual(metadata['created_at'], publication_finished_at)
                self.assertEqual(evidence._source_intents(origin, body, db=db), [])
            stopped.update(scope=scope, identity=identity, task=copy.deepcopy(retired), assignment=settled,
                body=body, metadata=copy.deepcopy(metadata), raw=raw, task_created_at=task_created_at)

        birth, prepared, contexts, histories, originals = self.prepare_existing_two_store_umbrella(before_final=prepare_stopped_member)
        fixture, request, _, _ = prepared
        destination, origin = contexts
        source_authority = evidence._canonical_plain(origin.authority_set[0])
        destination_authority = evidence._canonical_plain(destination.authority_set[0])
        qualified = dict(authority=source_authority, artifact=stopped['body'])
        dependency = dict(kind='artifact', reference=qualified)

        def destination_consumers(db):
            return [name for name, in db.execute('select name from evidence_metadata').fetchall()
                if dependency in state._metadata(db, name)['dependencies']]

        structural_authorities = {evidence._raw(source_authority), evidence._raw(destination_authority)}
        before = {}
        for context in contexts:
            with evidence._context_database(context) as db:
                before[str(context.database)] = evidence._canonical_plain(state._source_baseline(None, db, context=context))
        with evidence._context_database(origin) as db:
            self.assertEqual({evidence._raw(authority) for authority in evidence._reverse_source_authorities(
                origin, db, stopped['metadata'])}, structural_authorities)
        with evidence._context_database(destination) as db:
            self.assertEqual(destination_consumers(db), [])
        first = []
        grants = []
        register = evidence._register_recovery_source
        acquire = onboard_coordinator.CoordinatorOperationOwner.acquire_d4_bookkeeping

        def observe_first(scope, db, observation, *, context=None):
            if context.database == destination.database and observation.content['journal']['state'] == 'prepared':
                self.assertTrue(changes._held_authority_guards(c.directory for c in contexts))
                self.assertIn(qualified, evidence._canonical_plain(observation.content)['artifacts'])
                self.assertTrue(any(row['id'] == stopped['assignment']['id'] for row in observation.content['assignments']))
                with evidence._context_database(origin) as reader:
                    self.assertEqual(evidence._canonical_plain(state._source_baseline(None, reader, context=origin)), before[str(origin.database)])
                    metadata, raw = evidence._managed_artifact(None, stopped['body'], context=origin, db=reader)
                    self.assertEqual((metadata, raw), (stopped['metadata'], stopped['raw']))
                    self.assertEqual({evidence._raw(authority) for authority in evidence._reverse_source_authorities(
                        origin, reader, metadata)}, structural_authorities)
                self.assertEqual(destination_consumers(db), [])
                first.append(observation.created_at)
            reference = register(scope, db, observation, context=context)
            if context.database == destination.database and observation.content['journal']['state'] == 'prepared':
                self.assertIn(reference['name'], destination_consumers(db))
            return reference

        def observe_grant(owner, facts, issue):
            result = acquire(owner, facts, issue)
            grants.append(evidence._canonical_plain(facts))
            return result

        with patch.object(evidence, '_register_recovery_source', side_effect=observe_first), \
                patch.object(onboard_coordinator.CoordinatorOperationOwner, 'acquire_d4_bookkeeping', new=observe_grant):
            result = birth.invoke(prepared)
        birth.assert_applied_configured(prepared, result)
        self.assertEqual(len(first), 1)
        self.assertEqual([row['journal']['state'] for row in grants], ['prepared', 'applied'])
        subject = {key: stopped['scope'].accepted_identity[key] for key in ('project_id', 'repository_id', 'checkout_id', 'client')}
        for grant in grants:
            self.assertTrue(any(row['reference'] == qualified and row['metadata'] == stopped['metadata']
                for row in grant['permissions']['sources']))
            self.assertIn(subject, grant['permissions']['subjects'])
        identity = dict(plan_dir=str(fixture.directory), transaction_id=request['manifest']['transaction_id'],
                        manifest_digest=onboarding._digest(request['manifest']))
        for context, retained in zip(contexts, histories):
            with evidence._context_database(context) as db:
                baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
                self.assertEqual({key: baseline[key] for key in ('reference', 'content')}, retained['baseline'])
                self.assertEqual(evidence._canonical_plain(evidence._read_birth_handoff(context, db)['certificate']), retained['certificate'])
                heads = [edge['reference']['artifact'] for edge in baseline['dependencies'] if edge['reference']['identity'] == identity]
                self.assertEqual(len(heads), 1)
                body = evidence._read(None, heads[0], context=context, db=db)
                metadata = state._metadata(db, heads[0]['name'])
                self.assertEqual(body['journal']['state'], 'applied')
                self.assertIn(qualified, evidence._canonical_plain(body)['artifacts'])
                self.assertIn(dependency, metadata['dependencies'])
                self.assertEqual(metadata['created_at'], first[0])
                self.assertTrue(any(row['id'] == stopped['assignment']['id'] and row['authority'] == source_authority
                    for row in body['assignments']))
            self.assertEqual(state._authority_identity(context.database), retained['authority'])
        with evidence._context_database(origin) as db:
            metadata, raw = evidence._managed_artifact(None, stopped['body'], context=origin, db=db)
            self.assertEqual((metadata, raw), (stopped['metadata'], stopped['raw']))
            task = state._task(db, state._scope_id(stopped['scope']), 'codex', stopped['identity']['session_id'])
            self.assertEqual(task, stopped['task'])
            self.assertEqual(task['lifecycle']['created_at'], stopped['task_created_at'])
            self.assertEqual({evidence._raw(authority) for authority in evidence._reverse_source_authorities(
                origin, db, metadata)}, structural_authorities)
            assignment = next(row for row in delegation._ledger(None, db, context=origin) if row['id'] == stopped['assignment']['id'])
            self.assertEqual(assignment, stopped['assignment'])
        markers = changes._authority_locations([context.directory for context in contexts])
        markers.append(Path(changes.LOCK_ROOT) / ('canonical-' + hashlib.sha256(
            ('destination:' + str(fixture.base / changes.PROJECT_FILE)).encode()).hexdigest() + '.lock'))
        for marker in markers:
            self.assertIsNone(changes._authority_owner(marker))
        for path, retained in originals.items():
            self.assertEqual(self.retained_file(path), retained)


if __name__ == '__main__':
    unittest.main()
