"""Current D4 coupling with actual stores; owner observations are synthetic fixtures."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import changes, delegation, evidence, onboarding, startup, state
from scripts import onboard_coordinator
from tests import test_canonical_recovery as recovery


class CanonicalCouplingTests(unittest.TestCase):
    def owner(self):
        owner = recovery.CanonicalTwoStoreSetupTests('runTest')
        self.addCleanup(owner.doCleanups)
        return owner

    def acquire(self, fixture, root):
        observed = fixture.test_observations[str(root)]
        acquired = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(root)),
            state.PACKAGE, qualifier=lambda record, facts: observed)
        self.assertEqual(acquired.resolution.status, 'active', acquired.resolution)
        scope = startup._entry_scope(acquired)
        self.assertEqual(scope.qualification_provenance['reference'], 'fixture:synthetic-trusted-owner-only')
        return scope

    def actor(self, fixture, scope, suffix):
        identity = dict(client='codex', session_id='coupling-' + suffix, task_id='coupling-' + suffix,
            scope_ref='fixture:synthetic-current-coupling-' + suffix)
        task = state.start_task(scope, identity | dict(mode='read-only', decision_ref='user:fixture-accepted'))['task']
        request = identity | dict(expected_revision=task['revision'], dispatch_id='read-' + suffix,
            claims=[dict(kind='path', key=str(scope.repository_root / 'AGENTS.md'), mode='read')],
            operation=dict(id='read-' + suffix, kind='coordinator-work'), reason='Observe a current reservation guard',
            ownership_ref='fixture:synthetic-independent-owner', stop_conditions=['Stop without launching a process'])
        return dict(scope=scope, identity=identity, task=copy.deepcopy(task), request=request)

    def actor_rows(self, actor):
        scope = actor['scope']
        with state._database(scope) as db:
            task = state._task(db, state._scope_id(scope), actor['identity']['client'],
                actor['identity']['session_id'], actor['identity']['task_id'])
            rows = copy.deepcopy(delegation._ledger(scope, db))
        return task, rows

    def excluded(self, fixture, actor):
        scope = self.acquire(fixture, actor['scope'].repository_root)
        self.assertEqual(startup._canonical_plain(scope.accepted_identity),
                         startup._canonical_plain(actor['scope'].accepted_identity))
        actor['scope'] = scope
        before = self.actor_rows(actor)
        self.assertEqual(before[0], actor['task'])
        with self.assertRaisesRegex(state.StateError, 'D4 transaction busy|active or uncertain D4 operation'):
            delegation.reserve_coordinator(scope, copy.deepcopy(actor['request']))
        self.assertEqual(self.actor_rows(actor), before)

    def histories(self, contexts):
        result = []
        for context in contexts:
            with evidence._context_database(context) as db:
                baseline = state._source_baseline(None, db, context=context)
                handoff = evidence._read_birth_handoff(context, db)
                result.append(dict(authority=state._authority_identity(context.database),
                    baseline={key: evidence._canonical_plain(baseline[key]) for key in ('reference', 'content')},
                    certificate=evidence._canonical_plain(handoff['certificate']),
                    checkouts=db.execute('select scope_id,physical_identity from checkouts order by scope_id').fetchall()))
        return result

    def retain(self, owner, originals):
        for path, retained in originals.items():
            self.assertEqual(owner.retained_file(path), retained)

    def markers(self, fixture, request, contexts):
        authority = Path(request['manifest']['enrollment']['record']['project_root']) / changes.PROJECT_FILE
        result = changes._authority_locations([context.directory for context in contexts])
        result.append(Path(changes.LOCK_ROOT) / ('canonical-' + hashlib.sha256(
            ('destination:' + str(authority)).encode()).hexdigest() + '.lock'))
        return result

    def assert_owners(self, fixture, request, contexts, *, released=False):
        identity = dict(schema_version=1, plan_dir=str(fixture.directory),
            transaction_id=request['manifest']['transaction_id'], manifest_digest=request['approval']['manifest_digest'])
        for marker in self.markers(fixture, request, contexts):
            self.assertEqual(changes._authority_owner(marker), None if released else identity)

    def operation(self, birth, fixture, request, contexts, operation):
        decision = birth.decision(fixture, request, operation, contexts)
        return onboard_coordinator.run_verified_operation(decision, fixture.directory, copy.deepcopy(request))

    def observation(self, request, *, command=None, prior=False):
        manifest = request['manifest']
        value = copy.deepcopy(request)
        value['observations'] = dict(manifest_digest=request['approval']['manifest_digest'],
            input_digest=changes._scoped_input_digest(manifest), qualification=[], task_reconciliation=[],
            prior_compatibility=[], command_reconciliation=[])
        if command is not None:
            action, attempt = command
            value['observations']['command_reconciliation'] = [dict(id=action, attempt_id=attempt,
                stopped=True, evidence=[dict(
                    reference='fixture:synthetic-observed-exact-command-and-descendants-stopped',
                    sha256=onboarding._digest(dict(id=action, attempt_id=attempt, stopped=True,
                        descendants_stopped=True, observation='synthetic local command lifecycle')))])]
        if prior:
            value['observations']['prior_compatibility'] = [member | dict(state_dir=None,
                evidence=[dict(reference='fixture:synthetic-prior-unaffected-active-member-retained',
                    sha256=onboarding._digest(dict(member=member, observation='synthetic prior compatibility')))])
                for member in manifest['enrollment']['affected']]
        return value

    def targeted(self, *, target=1, command=False):
        owner = self.owner()
        actors = []

        def qualify_unaffected(birth, fixture, contexts):
            path = fixture.base / changes.PROJECT_FILE
            before = json.loads(path.read_bytes())
            birth.qualify_members(fixture, path)
            qualified = json.loads(path.read_bytes())
            target_root = next(Path(checkout['root']) for _, checkout, entry in changes._scoped_entries(before).values()
                if Path(entry['configuration']['runtime']['state_dir']) == contexts[target].directory)
            for key, (_, checkout, entry) in changes._scoped_entries(qualified).items():
                if Path(checkout['root']) == target_root:
                    entry['activation'] = copy.deepcopy(changes._scoped_entries(before)[key][2]['activation'])
            path.write_text(json.dumps(qualified))
            actor_root = next(Path(checkout['root']) for _, checkout, entry in changes._scoped_entries(qualified).values()
                if Path(entry['configuration']['runtime']['state_dir']) == contexts[1-target].directory)
            actors.append(self.actor(fixture, self.acquire(fixture, actor_root), 'unaffected'))

        birth, prepared, contexts, _, originals = owner.prepare_existing_two_store_umbrella(before_final=qualify_unaffected)
        fixture = prepared[0]
        previous = fixture.directory
        sources = copy.deepcopy(fixture.record)
        private = tempfile.TemporaryDirectory(prefix='vcw-coupling-plan-')
        self.addCleanup(private.cleanup)
        fixture.directory = Path(private.name).resolve() / 'plan'
        fixture.directory.mkdir(mode=0o700)
        originals[previous / 'plan.json'] = owner.retained_file(previous / 'plan.json')
        for source in sources['toolkit']['files']:
            original = Path(source['local_path'])
            originals[original] = owner.retained_file(original)
            copied = fixture.directory / source['path']
            copied.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            copied.write_bytes(original.read_bytes())
            copied.chmod(0o400)
            source['local_path'] = str(copied)
        fixture.record = sources
        fixture.source = Path(sources['toolkit']['files'][0]['local_path'])
        current = json.loads((fixture.base / changes.PROJECT_FILE).read_bytes())
        selected = next(Path(checkout['root']) for _, checkout, entry in changes._scoped_entries(current).values()
            if Path(entry['configuration']['runtime']['state_dir']) == contexts[target].directory)
        onboarding.inspect_project(fixture.base, fixture.directory,
            fixture.project_record(fixture.base, [selected], 'umbrella', ['AGENTS.md']))
        proposal = fixture.scoped_proposal()
        text = 'product rules\nfixture-targeted-coupling\n'
        for assessment in onboarding._assessments(proposal['assessments']):
            action = assessment['actions'][0]
            action.update(text=text, after=hashlib.sha256(text.encode()).hexdigest())
            if command and assessment['owner']['kind'] == 'repository':
                action.update(kind='command', before=None, after=None, section=None,
                    text=[sys.executable, '-c', "from pathlib import Path; Path('AGENTS.md').write_text(" + repr(text) + ")"],
                    cwd=str(selected))
                authorization = copy.deepcopy(assessment['evidence'][0])
                authorization.update(id='command-authorization', kind='authorization',
                    summary='Explicit synthetic fixture Repository command authorization')
                assessment['evidence'].append(authorization)
        onboarding.record_plan(fixture.directory, proposal)
        request = fixture.scoped_request(prepared=True)
        manifest = request['manifest']
        manifest['transaction_id'] = 'fixture-targeted-coupling'
        shared = str(fixture.base / 'AGENTS.md')
        manifest['enrollment']['record']['shared_policy'] = [dict(path=shared,
            sha256=hashlib.sha256(text.encode()).hexdigest())]
        for _, checkout, entry in changes._scoped_entries(manifest['enrollment']['record']).values():
            if Path(checkout['root']) == selected:
                entry['configuration']['runtime'] = dict(schema_version=3, state_dir=str(contexts[target].directory))
                entry['configuration']['shared_dependencies'] = [shared]
        if command:
            assessment = next(row for row in onboarding._assessments(fixture.plan()['assessments'])
                if row['owner']['kind'] == 'repository')
            action = next(row for row in manifest['actions'] if row['owner']['kind'] == 'repository')
            action['effects'] = [dict(path='AGENTS.md', before=changes._fingerprint(selected, 'AGENTS.md'),
                after=dict(sha256=hashlib.sha256(text.encode()).hexdigest(), mode=0o644),
                target=changes._scoped_target(selected, 'AGENTS.md'))]
            action['execution'] = dict(argv=assessment['actions'][0]['text'], cwd=str(selected),
                cwd_target=assessment['identity']['targets']['cwd:' + str(selected)], timeout_seconds=5,
                qualification=dict(reference='fixture:synthetic-reviewed-local-command',
                    sha256=onboarding._digest(dict(argv=assessment['actions'][0]['text'], cwd=str(selected),
                        effects=action['effects'], assessed_evidence=assessment['evidence']))), idempotency=None)
        request['approval']['manifest_digest'] = onboarding._digest(manifest)
        changes._validate_scoped(request, fixture.plan())
        self.assertEqual(len(manifest['enrollment']['affected']), 1)
        actor = actors[0]
        actor['scope'] = self.acquire(fixture, actor['scope'].repository_root)
        return owner, birth, fixture, request, contexts, originals, actor

    def uncertain(self):
        data = self.targeted(command=True)
        owner, birth, fixture, request, contexts, originals, actor = data
        history = self.histories(contexts)
        result = self.operation(birth, fixture, request, contexts, 'apply')
        self.assertEqual(result['exit_code'], 2, result)
        journal = changes._load_journal(fixture.directory)
        action = next(row for row in request['manifest']['actions'] if row['execution'] is not None)
        receipt = journal['receipts'][action['id']]
        self.assertEqual(receipt['returncode'], 0)
        self.assertFalse(receipt['command']['stopped'])
        attempt = receipt['command']['attempt_id']
        marker = changes._command_owner(action['owner']['git_dir'])
        self.assertEqual(marker, dict(schema_version=1, plan_dir=str(fixture.directory),
            transaction_id=journal['transaction_id'], id=action['id'], attempt_id=attempt))
        self.assert_owners(fixture, request, contexts)
        self.excluded(fixture, actor)
        self.assertEqual(self.histories(contexts), history)
        self.retain(owner, originals)
        return data, action, attempt, history

    def test_current_shared_authority_excludes_foreign_reservation_after_scan(self):
        owner = self.owner()
        actors = []

        def prepare_actors(birth, fixture, contexts):
            birth.qualify_members(fixture, fixture.base / changes.PROJECT_FILE)
            for index, context in enumerate(contexts):
                root = next(Path(root) for root, acquired in fixture.member_acquisitions.items()
                    if startup._entry_scope(acquired).runtime.state_dir == context.directory)
                actors.append(self.actor(fixture, self.acquire(fixture, root), str(index)))

        birth, prepared, contexts, _, originals = owner.prepare_existing_two_store_umbrella(before_final=prepare_actors)
        fixture, request, _, _ = prepared
        history = self.histories(contexts)
        scanned, finish = threading.Event(), threading.Event()
        original = changes._scoped_writers

        def scan(*args, **kwargs):
            result = original(*args, **kwargs)
            if not scanned.is_set():
                self.assertTrue(changes._held_authority_guards(context.directory for context in contexts))
                scanned.set()
                self.assertTrue(finish.wait(timeout=10))
            return result

        with patch.object(changes, '_scoped_writers', side_effect=scan), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(birth.invoke, prepared)
            try:
                self.assertTrue(scanned.wait(timeout=5), 'Real current writer scan was not reached')
                for actor in actors:
                    self.excluded(fixture, actor)
            finally:
                finish.set()
            result = future.result(timeout=15)
        birth.assert_applied_configured(prepared, result)
        self.assertEqual(self.histories(contexts), history)
        self.retain(owner, originals)
        self.assert_owners(fixture, request, contexts, released=True)

    def test_current_uncertain_command_owner_retains_authorities_until_exact_reconciliation(self):
        data, action, attempt, history = self.uncertain()
        owner, birth, fixture, request, contexts, originals, actor = data
        binding = {key: changes._load_journal(fixture.directory)[key]
            for key in ('transaction_id', 'manifest_digest', 'manifest', 'approval', 'plan')}
        for observed in (copy.deepcopy(request), self.observation(request, command=(action['id'], '0' * 32))):
            result = self.operation(birth, fixture, observed, contexts, 'resume')
            self.assertEqual(result['exit_code'], 2, result)
            self.assertFalse(changes._load_journal(fixture.directory)['receipts'][action['id']]['command']['stopped'])
            self.assert_owners(fixture, request, contexts)
            self.excluded(fixture, actor)
        observed = self.observation(request, command=(action['id'], attempt))
        result = self.operation(birth, fixture, observed, contexts, 'resume')
        self.assertEqual(result['state'], 'applied', result)
        self.assertEqual(result['exit_code'], 2, result)
        journal = changes._load_journal(fixture.directory)
        self.assertEqual({key: journal[key] for key in binding}, binding)
        self.assertEqual(journal['receipts'][action['id']]['command'], dict(attempt_id=attempt, stopped=True))
        self.assertIsNone(changes._command_owner(action['owner']['git_dir']))
        self.assert_owners(fixture, request, contexts, released=True)
        self.assertEqual(self.histories(contexts), history)
        self.retain(owner, originals)

    def test_current_preexisting_foreign_reservation_blocks_before_mutation(self):
        owner = self.owner()
        actors = []

        def reserve_foreign(birth, fixture, contexts):
            birth.qualify_members(fixture, fixture.base / changes.PROJECT_FILE)
            root = next(Path(root) for root, acquired in fixture.member_acquisitions.items()
                if startup._entry_scope(acquired).runtime.state_dir == contexts[1].directory)
            actor = self.actor(fixture, self.acquire(fixture, root), 'existing')
            actor['reservation'] = delegation.reserve_coordinator(actor['scope'], actor['request'])
            self.assertTrue(actor['reservation']['reservation_admitted'])
            actors.append(actor)

        birth, prepared, contexts, _, originals = owner.prepare_existing_two_store_umbrella(before_final=reserve_foreign)
        fixture, request, _, _ = prepared
        actor = actors[0]
        before = self.actor_rows(actor)
        authority = fixture.base / changes.PROJECT_FILE
        retained = owner.retained_file(authority)
        effects = {(row['id'], effect['path']): changes._scoped_target(Path(row['owner']['root']), effect['path'])
            for row in request['manifest']['actions'] for effect in row['effects']}
        history = self.histories(contexts)
        scans = []
        original = changes._scoped_writers

        def scan(*args, **kwargs):
            scans.append(True)
            return original(*args, **kwargs)

        with patch.object(changes, '_scoped_writers', side_effect=scan):
            result = birth.invoke(prepared)
        self.assertTrue(scans)
        self.assertEqual(result['exit_code'], 2, result)
        self.assertTrue(any('foreign or uncertain assignment' in row['reason'] for row in result['blocked']), result)
        self.assertFalse((fixture.directory / 'transaction.json').exists())
        self.assertEqual(owner.retained_file(authority), retained)
        for row in request['manifest']['actions']:
            for effect in row['effects']:
                self.assertEqual(changes._scoped_target(Path(row['owner']['root']), effect['path']), effects[row['id'], effect['path']])
        self.assertEqual(self.actor_rows(actor), before)
        self.assertEqual(self.histories(contexts), history)
        self.retain(owner, originals)

    def test_current_rollback_guards_proposed_actual_and_backup_authorities(self):
        observed_stores = set()
        for target in (0, 1):
            with self.subTest(target=target):
                owner, birth, fixture, request, contexts, originals, actor = self.targeted(target=target)
                history = self.histories(contexts)
                prior = copy.deepcopy(changes._scoped_prior(fixture.plan())[1])
                before_authority = (fixture.base / changes.PROJECT_FILE).read_bytes()
                before_effects = {(row['id'], effect['path']): (Path(row['owner']['root']) / effect['path']).read_bytes()
                    for row in request['manifest']['actions'] for effect in row['effects']}
                result = self.operation(birth, fixture, request, contexts, 'apply')
                self.assertEqual(result['state'], 'applied', result)
                journal = changes._load_journal(fixture.directory)
                binding = {key: copy.deepcopy(journal[key]) for key in ('transaction_id', 'manifest_digest', 'manifest', 'approval', 'plan')}
                backup = fixture.directory / journal['enrollment']['backup']
                retained_backup = owner.retained_file(backup)
                self.assertEqual(backup.read_bytes(), before_authority)
                self.assertEqual(json.loads(backup.read_bytes()), prior)
                applied_authority = owner.retained_file(fixture.base / changes.PROJECT_FILE)
                applied_postimages = copy.deepcopy(journal['enrollment']['postimages'])
                roles = []
                locked_scans = []
                original_scan = changes._scoped_writers
                original_restore = changes._scoped_restore

                def scan(*args, **kwargs):
                    result = original_scan(*args, **kwargs)
                    if changes._held_authority_guards(context.directory for context in contexts):
                        locked_scans.append(tuple(args[0]))
                    return result

                def restore(path, current, actual, owned, observation):
                    self.assertTrue(owned)
                    self.assertTrue(locked_scans)
                    self.assert_owners(fixture, request, contexts)
                    role_records = [('proposed', current['manifest']['enrollment']['record']),
                        ('actual', actual), ('backup', json.loads(backup.read_bytes()))]
                    for name, record in role_records:
                        locations = changes._scoped_runtime_locations([record])
                        self.assertTrue(locations)
                        for directory in locations:
                            self.assertIn(directory, {context.directory for context in contexts})
                        roles.append((name, copy.deepcopy(record), tuple(locations)))
                    self.assertEqual(actual, current['manifest']['enrollment']['record'])
                    self.assertEqual(role_records[2][1], prior)
                    self.assertTrue(current['enrollment']['postimages'])
                    self.assertEqual(current['enrollment']['postimages'][-1]['phase'], 'final')
                    self.excluded(fixture, actor)
                    observed_stores.add(target ^ 1)
                    return original_restore(path, current, actual, owned, observation)

                with patch.object(changes, '_scoped_restore', side_effect=restore), \
                        patch.object(changes, '_scoped_writers', side_effect=scan):
                    observed = self.observation(request, prior=True)
                    result = self.operation(birth, fixture, observed, contexts, 'rollback')
                    self.assertEqual(result['state'], 'rollback_partial', result)
                    self.assertEqual(result['exit_code'], 2, result)
                    self.assertEqual(result['blocked'], [dict(id='transaction',
                        reason='Fresh scoped adoption qualification observations are required')])
                    self.assertEqual([name for name, _, _ in roles], ['proposed', 'actual', 'backup'])
                    partial = changes._load_journal(fixture.directory)
                    self.assertEqual({key: partial[key] for key in binding}, binding)
                    self.assertEqual(partial['enrollment']['postimages'], applied_postimages)
                    self.assertTrue(all(receipt['status'] == 'rolled_back' for receipt in partial['receipts'].values()))
                    self.assertEqual(owner.retained_file(fixture.base / changes.PROJECT_FILE), applied_authority)
                    self.assertEqual(owner.retained_file(backup), retained_backup)
                    for row in request['manifest']['actions']:
                        for effect in row['effects']:
                            self.assertEqual(changes._fingerprint(Path(row['owner']['root']), effect['path']), effect['before'])
                            self.assertEqual((Path(row['owner']['root']) / effect['path']).read_bytes(),
                                             before_effects[row['id'], effect['path']])
                    self.assertEqual(self.histories(contexts), history)
                    self.retain(owner, originals)
                    self.assert_owners(fixture, request, contexts)

                    fresh_facts = []
                    actor_root = actor['scope'].repository_root

                    def observe_unaffected(record, facts):
                        fresh_facts.append(startup._canonical_plain(facts))
                        return fixture.test_observations[str(actor_root)]

                    acquired = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=actor_root),
                        state.PACKAGE, qualifier=observe_unaffected)
                    self.assertEqual(acquired.resolution.status, 'active', acquired.resolution)
                    self.assertEqual(len(fresh_facts), 1)
                    actor['scope'] = startup._entry_scope(acquired)
                    self.assertEqual(self.actor_rows(actor)[0], actor['task'])
                    fresh = self.observation(request, prior=True)
                    self.assertNotEqual(fresh['observations']['input_digest'], observed['observations']['input_digest'])
                    # Only the unaffected prior member is active; its fresh acquisition retains its original qualification.
                    affected = {tuple(member[key] for key in ('repository_id', 'checkout_id', 'client'))
                        for member in request['manifest']['enrollment']['affected']}
                    self.assertTrue(any(entry['activation']['status'] == 'active'
                        for _, _, entry in changes._scoped_entries(prior).values()))
                    self.assertTrue(all(entry['activation']['status'] != 'active'
                        for key, (_, _, entry) in changes._scoped_entries(prior).items() if key in affected))
                    for proof in fresh['observations']['prior_compatibility']:
                        proof['evidence'] = [dict(reference='fixture:synthetic-fresh-prior-compatibility-after-owned-restoration',
                            sha256=onboarding._digest(dict(prior=prior, observed=fresh_facts[0],
                                qualifier=fixture.test_observations[str(actor_root)],
                                current_input_digest=fresh['observations']['input_digest'])))]
                    locked_scans.clear()
                    result = self.operation(birth, fixture, fresh, contexts, 'rollback')
                self.assertEqual(result['state'], 'rolled_back', result)
                self.assertEqual(result['exit_code'], 0, result)
                self.assertEqual([name for name, _, _ in roles], ['proposed', 'actual', 'backup'] * 2)
                journal = changes._load_journal(fixture.directory)
                self.assertEqual({key: journal[key] for key in binding}, binding)
                self.assertEqual(journal['receipts'], partial['receipts'])
                self.assertEqual(journal['enrollment']['postimages'][:-1], applied_postimages)
                self.assertEqual(journal['enrollment']['postimages'][-1]['phase'], 'restore')
                self.assertEqual(json.loads((fixture.base / changes.PROJECT_FILE).read_bytes()), prior)
                self.assertEqual(owner.retained_file(backup), retained_backup)
                for row in request['manifest']['actions']:
                    for effect in row['effects']:
                        self.assertEqual((Path(row['owner']['root']) / effect['path']).read_bytes(), before_effects[row['id'], effect['path']])
                self.assertEqual(self.histories(contexts), history)
                self.retain(owner, originals)
                self.assert_owners(fixture, request, contexts, released=True)
        self.assertEqual(observed_stores, {0, 1})

    def test_current_missing_journal_never_releases_uncertain_command_owner(self):
        data, action, attempt, history = self.uncertain()
        owner, birth, fixture, request, contexts, originals, actor = data
        journal_path = fixture.directory / 'transaction.json'
        saved = journal_path.read_bytes()
        mode = journal_path.stat().st_mode & 0o777
        command_owner = changes._command_owner(action['owner']['git_dir'])
        journal_path.unlink()
        try:
            self.excluded(fixture, actor)
            result = self.operation(birth, fixture, request, contexts, 'resume')
            self.assertIn(result['exit_code'], (1, 2), result)
            self.assertNotEqual(result['state'], 'applied', result)
            self.assertFalse(journal_path.exists())
            self.assert_owners(fixture, request, contexts)
            self.assertEqual(changes._command_owner(action['owner']['git_dir']), command_owner)
            self.excluded(fixture, actor)
            self.assertEqual(self.histories(contexts), history)
            self.retain(owner, originals)
        finally:
            journal_path.write_bytes(saved)
            journal_path.chmod(mode)
        observed = self.observation(request, command=(action['id'], attempt))
        result = self.operation(birth, fixture, observed, contexts, 'resume')
        self.assertEqual(result['state'], 'applied', result)
        self.assertEqual(changes._load_journal(fixture.directory)['receipts'][action['id']]['command'],
                         dict(attempt_id=attempt, stopped=True))
        self.assertIsNone(changes._command_owner(action['owner']['git_dir']))
        self.assert_owners(fixture, request, contexts, released=True)
        self.assertEqual(self.histories(contexts), history)
        self.retain(owner, originals)
