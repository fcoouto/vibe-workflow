"""Current canonical writer/rollback proof; all owner observations are software fixtures."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import changes, evidence, onboarding, startup, state
from scripts import onboard_coordinator
from tests import test_canonical_recovery as recovery


class CanonicalOwnedRollbackTests(unittest.TestCase):
    def prepare(self, *, active_prior=False):
        host = recovery.CanonicalTwoStoreSetupTests('runTest')
        self.addCleanup(host.doCleanups)

        def qualify_prior(birth, fixture, contexts):
            birth.qualify_members(fixture, fixture.base / changes.PROJECT_FILE)

        result = host.prepare_existing_two_store_umbrella(
            before_final=qualify_prior if active_prior else None)
        self.birth, self.prepared, self.contexts, self.histories, self.originals = result
        self.fixture, self.request, _, _ = self.prepared
        self.authority = Path(self.request['manifest']['enrollment']['record']['project_root']) / changes.PROJECT_FILE
        self.host = host
        return result

    def assert_history_retained(self):
        for context, history in zip(self.contexts, self.histories):
            self.assertEqual(state._authority_identity(context.database), history['authority'])
            with evidence._context_database(context) as db:
                baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
                self.assertEqual({key: baseline[key] for key in ('reference', 'content')}, history['baseline'])
                certificate = evidence._canonical_plain(evidence._read_birth_handoff(context, db)['certificate'])
                self.assertEqual(certificate, history['certificate'])
                self.assertEqual(db.execute('select scope_id,physical_identity from checkouts order by scope_id').fetchall(),
                                 history['checkouts'])
        for path, retained in self.originals.items():
            self.assertEqual(self.host.retained_file(path), retained)

    def protected_files(self):
        paths = {self.authority}
        for action in self.request['manifest']['actions']:
            paths.update(Path(action['owner']['root']) / effect['path'] for effect in action['effects'])
        return {path: (self.host.retained_file(path), changes._scoped_target(path.parent, path.name))
                for path in paths}

    def assert_files_retained(self, files):
        self.assertEqual(self.protected_files(), files)

    def markers(self):
        locations = changes._authority_locations([context.directory for context in self.contexts])
        locations.append(Path(changes.LOCK_ROOT) / ('canonical-' + hashlib.sha256(
            ('destination:' + str(self.authority)).encode()).hexdigest() + '.lock'))
        return {location: changes._authority_owner(location) for location in locations}

    def invoke(self, operation, request=None):
        request = self.request if request is None else request
        decision = self.birth.decision(self.fixture, request, operation, self.contexts)
        return onboard_coordinator.run_verified_operation(decision, self.fixture.directory, copy.deepcopy(request))

    def acquire(self, root, qualifier):
        acquisition = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(root)), state.PACKAGE,
            qualifier=lambda record, facts: copy.deepcopy(qualifier))
        self.assertEqual(acquisition.resolution.status, 'active', acquisition.resolution)
        scope = startup._entry_scope(acquisition)
        self.assertEqual(scope.qualification_provenance['reference'], 'fixture:synthetic-trusted-owner-only')
        return scope

    def start_writer(self, scope, session):
        identity = dict(client='codex', session_id=session, task_id=session,
                        scope_ref='fixture:synthetic-' + session)
        task = state.start_task(scope, identity | dict(mode='editable', decision_ref='user:fixture-accepted'))['task']
        self.assertEqual(task['pins']['schema_version'], 3)
        return identity, task

    def stored_task(self, scope, identity):
        context = next(context for context in self.contexts if context.directory == scope.runtime.state_dir)
        with evidence._context_database(context) as db:
            return copy.deepcopy(state._task(db, state._scope_id(scope), identity['client'],
                                              identity['session_id'], identity['task_id']))

    def assert_writer_blocked(self, result):
        self.assertEqual(result['exit_code'], 2, result)
        self.assertFalse(result['qualified'], result)
        self.assertTrue(any(row['reason'] == 'Affected editable task lacks exact stopped-writer retirement proof'
                            for row in result['blocked']), result)

    def test_current_live_writer_blocks_before_owned_suspension_or_effects(self):
        self.prepare(active_prior=True)
        scope = self.acquire(self.fixture.repo, self.fixture.test_observations[str(self.fixture.repo)])
        identity, task = self.start_writer(scope, 'current-live-writer')
        files, markers = self.protected_files(), self.markers()
        self.assertTrue(all(owner is None for owner in markers.values()))
        result = self.invoke('apply')
        self.assert_writer_blocked(result)
        self.assertFalse((self.fixture.directory / 'transaction.json').exists())
        self.assert_files_retained(files)
        self.assertEqual(self.markers(), markers)
        self.assertEqual(self.stored_task(scope, identity), task)
        self.assert_history_retained()

    def test_prepared_journal_resume_rechecks_actual_late_writer(self):
        self.prepare(active_prior=True)
        files = self.protected_files()
        saved = changes._save
        reached = []

        def interrupt_after_prepared(path, journal):
            saved(path, journal)
            if Path(path) == self.fixture.directory and journal['state'] == 'prepared' and not reached:
                self.assertIsNone(journal['enrollment'])
                self.assertEqual(journal['receipts'], {})
                reached.append(copy.deepcopy(journal))
                raise RuntimeError('fixture: after actual initial prepared journal, before publication markers')

        with patch.object(changes, '_save', side_effect=interrupt_after_prepared):
            with self.assertRaisesRegex(RuntimeError, 'after actual initial prepared journal'):
                self.invoke('apply')
        self.assertEqual(len(reached), 1)
        self.assertEqual(changes._load_journal(self.fixture.directory), reached[0])
        self.assert_files_retained(files)
        markers = self.markers()
        self.assertTrue(all(owner is None for owner in markers.values()))
        scope = self.acquire(self.fixture.repo, self.fixture.test_observations[str(self.fixture.repo)])
        identity, task = self.start_writer(scope, 'late-current-writer')
        result = self.invoke('resume')
        self.assert_writer_blocked(result)
        journal = changes._load_journal(self.fixture.directory)
        for key in ('transaction_id', 'manifest_digest', 'manifest', 'approval', 'plan', 'receipts', 'enrollment'):
            self.assertEqual(journal[key], reached[0][key])
        self.assert_files_retained(files)
        self.assertEqual(self.markers(), markers)
        self.assertEqual(self.stored_task(scope, identity), task)
        self.assert_history_retained()

    def active_manifest(self):
        record = self.request['manifest']['enrollment']['record']
        qualifiers = {}
        source = state.PACKAGE / 'hooks/codex.json'
        info = state.PACKAGE.stat()
        proof = dict(reference='fixture:synthetic-trusted-owner-only', sha256='e' * 64)
        for key, (_, checkout, entry) in changes._scoped_entries(record).items():
            projection = startup.canonical_scope_projection(startup.decode_project_record(record),
                repository_id=key[0], checkout_id=key[1], client=key[2])
            digest = startup._canonical_digest(startup._canonical_plain(projection))
            binding = dict(client='codex', surface='fixture-only', version='1', configuration_digest='f' * 64,
                hook_owner=dict(root=str(state.PACKAGE), binding=dict(device=info.st_dev, inode=info.st_ino),
                    source=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest()),
                accepted_scope_digest=digest, receipt=proof)
            entry['activation'].update(status='active', qualification='qualified', accepted_scope_digest=digest,
                transaction_id=self.request['manifest']['transaction_id'], evidence=[proof], qualification_binding=binding)
            qualifiers[checkout['root']] = dict(current={name: copy.deepcopy(binding[name]) for name in
                ('client', 'surface', 'version', 'configuration_digest', 'hook_owner')},
                receipt=proof, binding=copy.deepcopy(binding), provenance=dict(kind='trusted-coordinator', **proof))
        self.request['approval']['manifest_digest'] = onboarding._digest(self.request['manifest'])
        return qualifiers

    def fresh_qualification(self, qualifiers):
        manifest = self.request['manifest']
        observation = dict(manifest_digest=onboarding._digest(manifest),
            input_digest=changes._scoped_input_digest(manifest), qualification=[], task_reconciliation=[],
            prior_compatibility=[], command_reconciliation=[])
        for key, (_, checkout, entry) in changes._scoped_entries(manifest['enrollment']['record']).items():
            captured = []

            def observe_configured(record, facts):
                captured.append(startup._canonical_plain(facts))
                return None

            acquisition = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(checkout['root'])),
                state.PACKAGE, qualifier=observe_configured)
            self.assertEqual(acquisition.resolution.status, 'configured', acquisition.resolution)
            self.assertIsNone(acquisition.resolution.scope)
            self.assertEqual(len(captured), 1)
            facts = {name: captured[0][name] for name in ('authority', 'observation', 'client', 'installed')}
            # The prior has no shared policy; observe the reviewed final dependencies after actual effects.
            facts['observation']['dependencies'] = [startup._acquired_dependency(Path(name), {'paths': 0, 'bytes': 0})
                for name in sorted(entry['configuration']['shared_dependencies'])]
            facts['observation']['qualification'] = copy.deepcopy(qualifiers[checkout['root']])
            member = dict(repository_id=key[0], checkout_id=key[1], client=key[2])
            rule = next(row for row in manifest['enrollment']['qualification'] if all(row[name] == value
                        for name, value in member.items()))
            proof = qualifiers[checkout['root']]['receipt']
            observation['qualification'].append(member | dict(
                requirements=[dict(id=name, evidence=[proof]) for name in rule['requirements']],
                general_review=[proof], security_review=[proof], scope_observations=facts))
        return self.request | dict(observations=observation)

    def retire_writer(self, scope, identity, task):
        proof = dict(version=1, kind='stopped-writer-reconciliation', identity=identity,
            expected_revision=task['revision'], expected_pins=task['pins'], observed_turn_id=task['turn_id'],
            stopped=True, descendants_stopped=True, attempt_ids=[],
            observation_ref='fixture:synthetic-task-owner-observed-no-process-or-descendants-launched',
            coordinator_ref='fixture:synthetic-independent-task-owner')
        path = self.fixture.directory / 'software-fixture-task-stop.json'

        def request(value):
            raw = json.dumps(value).encode()
            path.write_bytes(raw)
            path.chmod(0o600)
            return identity | dict(expected_revision=task['revision'], expected_pins=task['pins'],
                reason='Observed stopped disposable task; no process or descendants launched',
                reference='fixture:synthetic-retirement',
                stop_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()))

        invalid = copy.deepcopy(proof)
        invalid['identity']['task_id'] = 'another-task'
        with self.assertRaisesRegex(state.StateError, 'does not match exact historical task'):
            state.retire_task(scope, request(invalid))
        self.assertEqual(self.stored_task(scope, identity), task)
        protected = self.protected_files()
        self.assert_writer_blocked(self.invoke('rollback'))
        self.assert_files_retained(protected)
        journal = changes._load_journal(self.fixture.directory)
        self.assertEqual([row['phase'] for row in journal['enrollment']['postimages']], ['final'])
        stop_request = request(proof)
        publication_start = state._owner_time()
        retired = state.retire_task(scope, stop_request)['task']
        publication_end = state._owner_time()
        self.assertEqual(retired['state'], 'CANCELLED')
        self.assertEqual(retired['pins'], task['pins'])
        self.assertEqual(retired['lifecycle']['created_at'], task['lifecycle']['created_at'])
        context = next(context for context in self.contexts if context.directory == scope.runtime.state_dir)
        reference = retired['retirement']['proof']
        with evidence._context_database(context) as db:
            metadata, raw = evidence._managed_artifact(None, reference, context=context, db=db)
            self.assertEqual(json.loads(raw), proof)
            self.assertGreaterEqual(metadata['created_at'], publication_start)
            self.assertLessEqual(metadata['created_at'], publication_end)
        return retired, (context, reference, copy.deepcopy(metadata), raw)

    def test_owned_active_postimage_rollback_waits_for_exact_current_writer_stop(self):
        self.prepare()
        prior = self.authority.read_bytes()
        before_effects = {(action['id'], effect['path']): (Path(action['owner']['root']) / effect['path']).read_bytes()
            for action in self.request['manifest']['actions'] for effect in action['effects']}
        qualifiers = self.active_manifest()
        approved = copy.deepcopy(self.request)
        paused = self.invoke('apply')
        self.assertEqual(paused['exit_code'], 2, paused)
        self.assertTrue(any(row['reason'] == 'Fresh scoped adoption qualification observations are required'
                            for row in paused['blocked']), paused)
        journal = changes._load_journal(self.fixture.directory)
        self.assertEqual(journal['manifest'], approved['manifest'])
        self.assertEqual(journal['approval'], approved['approval'])
        self.assertIsNotNone(journal['enrollment'])
        self.assertEqual(journal['enrollment']['postimages'], [])
        self.assertTrue(journal['receipts'])
        self.assertTrue(all(row['status'] == 'completed' for row in journal['receipts'].values()))
        self.assertEqual(self.authority.read_bytes(), prior)
        owner = dict(schema_version=1, plan_dir=str(self.fixture.directory),
            transaction_id=journal['transaction_id'], manifest_digest=journal['manifest_digest'])
        self.assertTrue(all(value == owner for value in self.markers().values()))
        backups = {self.fixture.directory / journal['enrollment']['backup']:
                   self.host.retained_file(self.fixture.directory / journal['enrollment']['backup'])}
        for receipt in journal['receipts'].values():
            for file in receipt['files']:
                if file['backup']:
                    path = self.fixture.directory / file['backup']
                    backups[path] = self.host.retained_file(path)
        observed = self.fresh_qualification(qualifiers)
        applied = self.invoke('resume', observed)
        self.assertEqual(applied['state'], 'applied', applied)
        self.assertEqual(applied['exit_code'], 0, applied)
        self.assertTrue(applied['qualified'], applied)
        self.assertEqual(applied['activation_status'], 'active', applied)
        self.assertTrue(all(row['status'] == 'active' for row in applied['members']))
        active = changes._load_journal(self.fixture.directory)
        self.assertEqual(active['manifest'], approved['manifest'])
        self.assertEqual(active['approval'], approved['approval'])
        self.assertTrue(changes._scoped_owned_authority(active))
        self.assertEqual([row['phase'] for row in active['enrollment']['postimages']], ['final'])
        self.assertEqual(active['enrollment']['postimages'][0]['status'], 'published')
        self.assertEqual(json.loads(self.authority.read_bytes()), approved['manifest']['enrollment']['record'])
        self.assertTrue(all(value is None for value in self.markers().values()))
        scopes = [self.acquire(root, qualifier) for root, qualifier in qualifiers.items()]
        identity, task = self.start_writer(scopes[0], 'owned-active-current-writer')
        files = self.protected_files()
        blocked = self.invoke('rollback')
        self.assert_writer_blocked(blocked)
        blocked_journal = changes._load_journal(self.fixture.directory)
        for key in ('manifest', 'approval', 'transaction_id', 'manifest_digest', 'receipts'):
            self.assertEqual(blocked_journal[key], active[key])
        self.assertEqual(blocked_journal['enrollment']['postimages'], active['enrollment']['postimages'])
        self.assertNotIn('rollback_suspended', [row['phase'] for row in blocked_journal['enrollment']['postimages']])
        self.assertTrue(changes._scoped_owned_authority(blocked_journal))
        self.assert_files_retained(files)
        self.assertEqual(self.stored_task(scopes[0], identity), task)
        self.assert_history_retained()
        for path, retained in backups.items():
            self.assertEqual(self.host.retained_file(path), retained)
        retired, proof = self.retire_writer(scopes[0], identity, task)
        result = self.invoke('rollback')
        self.assertEqual(result['state'], 'rolled_back', result)
        self.assertEqual(result['exit_code'], 0, result)
        self.assertFalse(result['blocked'], result)
        final = changes._load_journal(self.fixture.directory)
        for key in ('manifest', 'approval', 'transaction_id', 'manifest_digest', 'plan'):
            self.assertEqual(final[key], active[key])
        self.assertEqual([row['phase'] for row in final['enrollment']['postimages']],
                         ['final', 'rollback_suspended', 'restore'])
        self.assertTrue(all(row['status'] == 'published' for row in final['enrollment']['postimages']))
        self.assertTrue(changes._scoped_owned_authority(final))
        self.assertEqual(self.authority.read_bytes(), prior)
        for action in self.request['manifest']['actions']:
            for effect in action['effects']:
                self.assertEqual((Path(action['owner']['root']) / effect['path']).read_bytes(),
                                 before_effects[(action['id'], effect['path'])])
                self.assertEqual(changes._fingerprint(Path(action['owner']['root']), effect['path']), effect['before'])
        self.assertTrue(all(value is None for value in self.markers().values()))
        self.assertEqual(self.stored_task(scopes[0], identity), retired)
        context, reference, metadata, raw = proof
        with evidence._context_database(context) as db:
            self.assertEqual(evidence._managed_artifact(None, reference, context=context, db=db), (metadata, raw))
        self.assert_history_retained()
        for path, retained in backups.items():
            self.assertEqual(self.host.retained_file(path), retained)
