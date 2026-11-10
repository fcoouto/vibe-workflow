"""Qualified fixture reads record merge facts without manufacturing local proof. Conditional remote-ref deletion against real disposable Git repositories. Manual-flow acceptance gaps; all provider execution uses disposable fixtures."""
from copy import deepcopy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from runtime import delivery, evidence, state, title_sync
from scripts.delivery_coordinator import DeliveryOperationOwner
from tests import test_delivery, test_delivery_github


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.case = test_delivery.CompletionTests('runTest')
        self.case.peer = getattr(self, 'peer', False)
        self.case.linked = not self.case.peer
        self.case.separate_worktrees = not self.case.peer
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fixture, self.scope = self.case.fixture, self.case.scope
        self.case.delivery_plan(1, required=getattr(self, 'required', True))
        self.provider = test_delivery_github.GitHubDeliveryTests('runTest')
        self.provider.setUp()
        self.provider.pr['head']['ref'] = 'feature-0'
        self.fixture.git('checkout', '-b', 'feature-0')
        self.fixture.git('add', '--', 'AGENTS.md')
        self.fixture.git('commit', '-qm', 'fixture accepted instructions')
        self.provider.pr['head']['sha'] = self.fixture.git('rev-parse', 'HEAD')
        outer = self

        class Native:
            def observe_worktree(self, root):
                return dict(root=str(root), owner='codex', attachment='fixture:native-attachment', reference='fixture:observed')

            def observe_publication(self, scope, publication, number):
                return dict(host='github.com', repository='owner/repo', repository_id=123, pull_request=number,
                    branch='feature-0', target='main', head=outer.provider.pr['head']['sha'],
                    reference='fixture:independently-observed-PR-ownership')

        self.native = Native()
        request = self.fixture.request(operation_id='workspace')
        self.owner('bind-worktree', request).run(self.scope, 'bind-worktree', request)
        self.fixture.ready_proof_fixture()

    def owner(self, action, request):
        return DeliveryOperationOwner(dict(action=action, request_digest=evidence._digest(request),
            accepted_scope_digest=self.scope.accepted_scope_digest, reference='fixture:verified-host-decision'),
            native=self.native, repository=self.provider.adapter(), gate=None)

    def reconcile(self, identity='observe-1', **updates):
        request = self.fixture.request(operation_id=identity, publication_id='pr-0', pull_request=42, **updates)
        return self.owner('reconcile', request).run(self.scope, 'reconcile', request)

    def merged(self):
        self.provider.pr.update(state='closed', draft=False, merged=True,
            merged_at='2026-11-02T12:00:00Z', merge_commit_sha='c' * 40)

    def test_manual_merge_observation_is_factual_and_never_merges_or_declares_done(self):
        opened = self.reconcile()
        self.assertFalse(opened['delivery_complete'])
        waiting = self.fixture.verify()
        self.assertEqual(waiting['task']['state'], 'REVIEW')
        proof = waiting['task']['evidence']['verification']
        self.merged()
        result = self.reconcile('observe-2')
        self.assertTrue(result['delivery_complete'])
        self.assertEqual(result['task']['state'], 'REVIEW')
        self.assertEqual(result['task']['evidence']['verification'], proof)
        self.assertTrue(all(argv[1] == 'api' for argv, _ in self.provider.calls))

    def test_exact_replay_does_not_repeat_observation_or_allow_changed_inputs(self):
        request = self.fixture.request(operation_id='observe-1', publication_id='pr-0', pull_request=42)
        owner = self.owner('reconcile', request)
        first = owner.run(self.scope, 'reconcile', request)
        count = len(self.provider.calls)
        replay = owner.run(self.scope, 'reconcile', request)
        self.assertEqual(first['task']['revision'], replay['task']['revision'])
        self.assertEqual(count, len(self.provider.calls))
        changed = request | dict(pull_request=43)
        with self.assertRaisesRegex(ValueError, 'reused'):
            self.owner('reconcile', changed).run(self.scope, 'reconcile', changed)

    def test_changed_merged_head_does_not_satisfy_the_sealed_candidate(self):
        self.reconcile()
        self.merged()
        self.provider.pr['head']['sha'] = 'e' * 40
        result = self.reconcile('observe-2')
        self.assertFalse(result['delivery_complete'])
        self.assertTrue(result['blockers'])

    def test_closed_unmerged_is_pending_with_a_disposition_needed(self):
        self.provider.pr.update(state='closed')
        result = self.reconcile()
        self.assertFalse(result['delivery_complete'])
        self.assertIn('Required PR was closed without merge: pr-0', result['blockers'])

    def test_replaced_repository_identity_cannot_deliver_the_original_scope(self):
        self.reconcile()
        self.provider.repo['id'] = 999
        self.merged()
        result = self.reconcile('observe-2')
        self.assertFalse(result['delivery_complete'])
        self.assertTrue(result['blockers'])

    def test_observed_merge_does_not_manufacture_missing_readiness_provenance(self):
        self.reconcile()
        self.fixture.verify()
        self.merged()
        self.reconcile('observe-2')
        result = self.fixture.verify()
        self.assertTrue(result['implementation_verified'])
        self.assertTrue(result['delivery_complete'])
        self.assertFalse(result['verified'])
        self.assertNotEqual(result['task']['state'], 'DONE')

    def test_uncommitted_local_contents_cannot_be_sealed_as_the_remote_candidate(self):
        (self.fixture.repo / 'app.py').write_text('unpublished = True\n')
        before = delivery.operate(self.scope, 'status', dict(client='codex', session_id='parent'))['plan']
        with self.assertRaisesRegex(ValueError, 'uncommitted'):
            self.reconcile()
        self.assertEqual(delivery.operate(self.scope, 'status', dict(client='codex', session_id='parent'))['plan'], before)


class SurvivingAuthorityTests(unittest.TestCase):
    def test_optional_open_pr_stays_visible_and_protected_after_own_completion(self):
        fixture = ReconcileTests('runTest')
        fixture.required = False
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.reconcile()
        completed = fixture.fixture.verify()['task']
        self.assertEqual(completed['state'], 'DONE')
        inventory = delivery.pending(fixture.scope)
        self.assertEqual(len(inventory['records']), 1)
        self.assertFalse(inventory['records'][0]['retention_complete'])
        self.assertFalse(inventory['records'][0]['delivery_required'])
        self.assertIn('pr-0', inventory['records'][0]['retention_pending'])

    def test_removed_original_checkout_keeps_factual_observation_and_sealed_proof(self):
        import shutil
        fixture = ReconcileTests('runTest')
        fixture.peer = True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        initial = fixture.reconcile()
        original = state._canonical_plain(evidence._artifact_owner(fixture.scope, initial['task']).task)
        survivor = fixture.fixture.peer_scope
        identity = dict(client='codex', session_id='observer', task_id='observe', scope_ref='user:observe')
        producer = state.start_task(survivor, identity | dict(mode='editable', decision_ref='user:observe'))['task']
        proof = initial['task']['evidence'].copy()
        shutil.rmtree(fixture.scope.repository_root)
        fixture.merged()
        request = identity | dict(expected_revision=producer['revision'], original=original,
            target_revision=initial['task']['revision'], publication_id='pr-0', operation_id='surviving-observer')
        owner = DeliveryOperationOwner(dict(action='reconcile', request_digest=evidence._digest(request),
            accepted_scope_digest=survivor.accepted_scope_digest, reference='fixture:verified-surviving-owner'),
            native=None, repository=fixture.provider.adapter(), gate=None)
        result = owner.run(survivor, 'reconcile', request)
        self.assertTrue(result['observation']['merged'])
        self.assertTrue(result['delivery_complete'])
        self.assertNotEqual(result['task']['state'], 'DONE')
        self.assertEqual(result['task']['evidence'], proof)
        self.assertFalse(fixture.scope.repository_root.exists())


class ConditionalDeletionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.repo, self.remote = self.root / 'checkout', self.root / 'remote.git'
        self.git_executable = shutil.which('git')
        self.environment = os.environ | dict(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.git('init', '-q', '-b', 'main', str(self.repo))
        self.git('init', '-q', '--bare', '-b', 'main', str(self.remote))
        for key, value in (('user.name', 'Fixture'), ('user.email', 'fixture@example.invalid'), ('commit.gpgsign', 'false')):
            self.git('-C', str(self.repo), 'config', key, value)
        (self.repo / 'file').write_text('one\n')
        self.git('-C', str(self.repo), 'add', 'file')
        self.git('-C', str(self.repo), 'commit', '-qm', 'fixture')
        self.head = self.git('-C', str(self.repo), 'rev-parse', 'HEAD').strip().decode()
        self.git('-C', str(self.repo), 'branch', 'feature')
        self.git('-C', str(self.repo), 'push', '-q', str(self.remote), 'HEAD:refs/heads/feature')
        self.calls, self.race, self.lose_ack = [], None, False

    def git(self, *args):
        return subprocess.check_output([self.git_executable, *args], env=self.environment, stderr=subprocess.DEVNULL, timeout=5)

    def transport(self):
        from runtime.adapters.git_delivery import GitRemote

        def run(argv, **bounds):
            self.calls.append(argv)
            if 'push' in argv and self.race:
                callback, self.race = self.race, None
                callback()
            result = subprocess.run(argv, env=self.environment, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=bounds['timeout'], check=False)
            if 'push' in argv and self.lose_ack:
                self.lose_ack = False
                raise subprocess.TimeoutExpired(argv[0], bounds['timeout'])
            return result.returncode, result.stdout

        return GitRemote(executable=self.git_executable, root=str(self.repo), destination=str(self.remote), run=run)

    def refs(self):
        return self.git('-C', str(self.repo), 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads')

    def test_exact_lease_deletes_only_the_remote_ref(self):
        transport = self.transport()
        local = self.refs()
        result = transport.delete('feature', self.head, inspection=transport.inspect())
        self.assertEqual(result['status'], 'deleted', result)
        self.assertEqual(self.refs(), local)
        self.assertTrue(self.repo.exists())
        push = next(argv for argv in self.calls if 'push' in argv)
        self.assertIn('--force-with-lease=refs/heads/feature:' + self.head, push)
        self.assertEqual(push[-1], ':refs/heads/feature')
        self.assertNotIn('--force', push)
        self.assertNotIn('--no-verify', push)

    def advance(self):
        (self.repo / 'file').write_text('two\n')
        self.git('-C', str(self.repo), 'commit', '-qam', 'advanced')
        self.new_head = self.git('-C', str(self.repo), 'rev-parse', 'HEAD').strip().decode()
        self.git('-C', str(self.repo), 'push', '-q', str(self.remote), 'HEAD:refs/heads/feature')

    def test_advanced_ref_is_retained_before_or_during_deletion(self):
        transport = self.transport()
        inspection = transport.inspect()
        self.race = self.advance
        result = transport.delete('feature', self.head, inspection=inspection)
        self.assertEqual(result['status'], 'retained', result)
        remote = self.git('ls-remote', str(self.remote), 'refs/heads/feature')
        self.assertTrue(remote.startswith(self.new_head.encode()))
        self.assertEqual(transport.delete('feature', self.head, inspection=transport.inspect())['status'], 'retained')

    def test_lost_acknowledgment_and_absence_are_observed_before_retry(self):
        transport = self.transport()
        self.lose_ack = True
        self.assertEqual(transport.delete('feature', self.head, inspection=transport.inspect())['status'], 'deleted')
        self.calls.clear()
        self.assertEqual(transport.delete('feature', self.head, inspection=transport.inspect())['status'], 'absent')
        self.assertFalse(any('push' in argv for argv in self.calls))

    def test_changed_inspected_configuration_retains_the_branch(self):
        transport = self.transport()
        inspection = transport.inspect()
        self.git('-C', str(self.repo), 'config', 'core.hooksPath', str(self.root / 'other-hooks'))
        self.assertEqual(transport.delete('feature', self.head, inspection=inspection)['status'], 'retained')
        self.assertFalse(any('push' in argv for argv in self.calls))

    def test_unavailable_remote_does_not_mean_absent(self):
        transport = self.transport()
        inspection = transport.inspect()
        shutil.rmtree(self.remote)
        self.assertEqual(transport.delete('feature', self.head, inspection=inspection)['status'], 'pending')
        self.assertFalse(any('push' in argv for argv in self.calls))

    def test_surviving_git_checkout_can_delete_without_the_original_worktree(self):
        from runtime.adapters.git_delivery import GitRemote
        original = self.transport()
        survivor = self.root / 'survivor'
        self.git('init', '-q', '-b', 'main', str(survivor))
        shutil.rmtree(self.repo)
        transport = GitRemote(executable=self.git_executable, root=str(survivor),
            destination=str(self.remote), run=original.run)
        result = transport.delete('feature', self.head, inspection=transport.inspect())
        self.assertEqual(result['status'], 'deleted', result)
        self.assertTrue((survivor / '.git').exists())
        self.assertFalse(self.repo.exists())

    def test_inspected_pre_push_hook_runs_without_being_bypassed(self):
        marker = self.root / 'hook-ran'
        hook = self.repo / '.git/hooks/pre-push'
        hook.write_text('#!/bin/sh\n: > "' + str(marker) + '"\nexit 1\n')
        hook.chmod(0o700)
        transport = self.transport()
        inspection = transport.inspect()
        self.assertIsNotNone(inspection['pre_push'])
        self.assertEqual(transport.delete('feature', self.head, inspection=inspection)['status'], 'pending')
        self.assertTrue(marker.exists())
        self.assertTrue(self.git('ls-remote', str(self.remote), 'refs/heads/feature').startswith(self.head.encode()))

    def test_remote_alias_cannot_redirect_the_approved_destination(self):
        from runtime.adapters.git_delivery import GitRemote
        transport = self.transport()
        self.git('-C', str(self.repo), 'remote', 'add', 'origin', str(self.remote))
        with self.assertRaises(ValueError):
            GitRemote(executable=self.git_executable, root=str(self.repo), destination='origin', run=transport.run)

    def test_unqualified_descriptor_platform_is_explicit(self):
        from runtime.adapters.git_delivery import GitRemote
        with patch('runtime.adapters.git_delivery.os.name', 'nt'), self.assertRaisesRegex(ValueError, 'qualified POSIX'):
            GitRemote(executable=self.git_executable, root=str(self.repo), destination=str(self.remote), run=lambda *a, **k: None)

    def test_hook_replacement_with_fifo_cannot_block_inspection(self):
        hook = self.repo / '.git/hooks/pre-push'
        hook.write_text('#!/bin/sh\nexit 0\n')
        script = textwrap.dedent('''
            import os
            import sys
            from pathlib import Path
            from unittest.mock import patch
            from runtime.adapters.git_delivery import GitRemote
            root, hook, executable = ROOT, HOOK, EXECUTABLE
            old_stat, old_open = Path.stat, os.open
            replaced = False
            def replace():
                global replaced
                if not replaced:
                    replaced = True
                    hook.unlink()
                    os.mkfifo(hook)
            def raced_stat(path, *args, **kwargs):
                result = old_stat(path, *args, **kwargs)
                if path == hook and sys._getframe(1).f_code.co_name == 'inspect': replace()
                return result
            def raced_open(path, flags, *args, **kwargs):
                if str(path) == str(hook): replace()
                return old_open(path, flags, *args, **kwargs)
            def run(argv, **bounds):
                if 'config' in argv: return 1, b''
                if '--git-path' in argv: return 0, str(hook).encode()
                return 0, ('\\n'.join([str(root), str(root/'.git'), str(root/'.git')])+'\\n').encode()
            transport = GitRemote(executable=executable, root=str(root), destination=DESTINATION, run=run)
            with patch.object(Path, 'stat', raced_stat), patch.object(os, 'open', raced_open):
                try: transport.inspect()
                except (ValueError, OSError): print('bounded refusal')
        ''').replace('ROOT', repr(self.repo)).replace('HOOK', repr(hook)).replace('EXECUTABLE', repr(self.git_executable)).replace('DESTINATION', repr(str(self.remote)))
        # Paths are represented as pathlib objects in the isolated race reproducer.
        script = 'from pathlib import PosixPath\n' + script
        try:
            result = subprocess.run([sys.executable, '-B', '-c', script], capture_output=True, timeout=3)
        except subprocess.TimeoutExpired:
            self.fail('Hook inspection blocked after FIFO replacement')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'bounded refusal', result.stdout)


class ManualDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.case = ReconcileTests('runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fixture, self.scope = self.case.fixture, self.case.scope
        state.update_task(self.scope, self.fixture.request(state='WORKING',
            reason='Accepted manual delivery fixture', reference='fixture:accepted-start'))
        self.transport = test_delivery_github.ProviderMutationTests('runTest')
        self.transport.setUp()
        self.transport.pr = self.case.provider.pr
        self.transport.fixture.pr = self.transport.pr
        self.transport.fixture.repo = self.case.provider.repo

    def status(self):
        return delivery.operate(self.scope, 'status', dict(client='codex', session_id='parent'))

    def operate(self, action, *, gate=None):
        request = self.fixture.request(publication_id='pr-0', operation_id='e' * 64)
        owner = DeliveryOperationOwner(dict(action=action, request_digest=evidence._digest(request),
            accepted_scope_digest=self.scope.accepted_scope_digest, reference='fixture:verified-decision'),
            native=self.case.native, repository=self.transport.adapter, gate=gate)
        return owner.run(self.scope, action, request)

    def retained_readiness(self, *, seed=True):
        """Seed the existing consumer fixture, not a production D7 readiness issuer."""
        task = self.status()['task']
        with state._database(self.scope) as db:
            plan = evidence._read(self.scope, task['evidence']['plan'], db=db)
        review = dict(applicability='required', reason='Actual change', independent=True)
        plan.update(nontrivial=True, reviews=dict(general=review, security=review))
        evidence.plan_evidence(self.scope, self.fixture.request(plan=plan,
            change_decision_ref='fixture:accepted-review-change'))
        self.fixture.checked()
        self.fixture.review()
        self.case.reconcile()
        verified = evidence.verify_completion(self.scope, self.fixture.request(purpose='merge-evidence'))
        self.assertTrue(verified['verified'], verified)
        if not seed:
            return
        with state._database(self.scope, write=True) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
            plan = delivery._read_plan(self.scope, task, db=db)
            publication = plan['publications'][0]
            receipt = dict(version=1, kind='delivery-readiness', binding=delivery._publication_binding(publication),
                local_verification=task['evidence']['verification'])
            publication['candidate']['readiness'] = evidence._artifact(self.scope, receipt,
                owner=evidence._artifact_owner(self.scope, task), db=db)
            task['delivery']['plan'] = evidence._artifact(self.scope, plan,
                owner=evidence._artifact_owner(self.scope, task), db=db)
            task['revision'] += 1
            state._save(db, state._scope_id(self.scope), task)
        self.transport.pr['draft'] = False
        self.transport.labels.add('[READY]')

    def fake_gate(self):
        """Only fixtures substitute the private source class; no production escape."""
        from runtime import repository_gate
        fixture = self

        class FixtureReadiness:
            def __init__(self, request):
                self.action = request['action']
                self.repository = fixture.transport.adapter
                self.local_verification = fixture.status()['task']['evidence']['verification']
                self.active = False

            def __enter__(self):
                self.active = True
                with state._database(fixture.scope, write=True) as db:
                    task = state._task(db, state._scope_id(fixture.scope), 'codex', 'parent')
                    self.reference = evidence._artifact(fixture.scope, dict(kind='fixture-d7-source',
                        action=self.action), owner=evidence._artifact_owner(fixture.scope, task), db=db)
                return self

            def __exit__(self, *error):
                self.active = False

            def recheck(self):
                if not self.active:
                    raise state.StateError('Fixture acquisition expired')

            def finalize(self, publish):
                self.recheck()
                calls = len(fixture.transport.calls)
                with state._database(fixture.scope, write=True) as db:
                    result = publish(db)
                fixture.assertEqual(len(fixture.transport.calls), calls, 'Network inside publication callback')
                return result

        class Gate:
            def acquire_readiness(self, scope, request, binding):
                fixture.assertIs(scope, fixture.scope)
                fixture.assertEqual(binding['pull_request'], 42)
                return FixtureReadiness(request)

        source = patch.object(repository_gate, 'RepositoryReadiness', FixtureReadiness, create=True)
        source.start()
        self.addCleanup(source.stop)
        return Gate()

    def waiting(self):
        self.retained_readiness()
        waiting = self.fixture.verify()
        self.assertEqual(waiting['task']['state'], 'WAITING', waiting)
        return waiting

    def test_caller_pass_flags_cannot_promote_pr_or_mint_readiness(self):
        self.case.reconcile()
        before = self.status()
        with self.assertRaisesRegex(ValueError, 'Qualified repository readiness source is unavailable'):
            self.operate('ready', gate=dict(evidence_valid=True, source_authentication='verified', ready=True))
        self.assertEqual(self.status(), before)
        self.assertEqual(self.transport.calls, [])
        self.assertTrue(self.transport.pr['draft'])

    def test_unavailable_readiness_source_cannot_promote_pr(self):
        self.case.reconcile()
        before = self.status()
        with self.assertRaisesRegex(ValueError, 'Qualified repository readiness source is unavailable'):
            self.operate('ready')
        self.assertEqual(self.status(), before)
        self.assertEqual(self.transport.calls, [])

    def test_ready_retains_exact_private_sources_after_metadata_readback(self):
        from contextlib import contextmanager
        from runtime import delegation, repository_gate
        from tests.test_repository_readiness import RepositoryReadinessTests

        self.retained_readiness(seed=False)
        source_case = RepositoryReadinessTests('runTest')
        source_case.local, source_case.scope = self.fixture, self.scope
        source_case.candidate = evidence.capture_candidate(self.scope,
            evidence._load_plan(self.scope, self.fixture.identity))
        source_case.binding = delivery._publication_binding(self.status()['plan']['publications'][0])
        source = source_case.source()
        source.repository = self.transport.adapter
        source_case.request = self.fixture.request(action='ready', publication_id='pr-0', operation_id='e' * 64)
        metadata, finalize = source.repository.mutate, source.finalize
        observed = dict(metadata=0, publication=0)
        fixture = self

        def assert_held(db):
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
            self.assertEqual(task['evidence']['verification'], observed['verification'])
            proof = evidence._read(self.scope, observed['verification'], db=db)
            self.assertEqual(proof['purpose'], 'merge-evidence')
            self.assertEqual(proof['blockers'], [])
            self.assertEqual(proof['candidate_digest'], source_case.binding['snapshot']['digest'])
            records = {row['id']: row for row in delegation._ledger(self.scope, db)}
            for identity in (proof['barrier_id'], source.operation):
                self.assertEqual(records[identity]['data']['status'], 'prepared')
            return proof['barrier_id']

        def mutate(*args, **kwargs):
            with state._database(self.scope) as db:
                observed['barrier'] = assert_held(db)
            source.remote_effects = 'uncertain'
            observed['metadata'] += 1
            return metadata(*args, **kwargs)

        def finalizing(publish, *, db=None):
            self.assertIsNotNone(db)
            self.assertTrue(db.in_transaction)
            calls = len(self.transport.calls)
            assert_held(db)
            result = finalize(publish, db=db)
            records = {row['id']: row for row in delegation._ledger(self.scope, db)}
            self.assertEqual(records[observed['barrier']]['data']['status'], 'prepared')
            self.assertEqual(records[source.operation]['data']['status'], 'settled')
            self.assertEqual(len(self.transport.calls), calls)
            observed['publication'] += 1
            return result

        source.repository.mutate, source.finalize = mutate, finalizing

        class Gate:
            @contextmanager
            def acquire_readiness(self, scope, request, binding):
                with repository_gate.acquire_readiness(scope, request, binding) as held:
                    fixture.assertIs(type(held), repository_gate.RepositoryReadiness)
                    fixture.assertIs(held.repository, fixture.transport.adapter)
                    fixture.assertFalse(held.withdrawal_only)
                    observed['verification'] = held.local_verification
                    yield held

        with patch.object(repository_gate, '_acquire_qualified_readiness_source', return_value=source):
            result = self.operate('ready', gate=Gate())
        receipt = evidence._read(self.scope, result['plan']['publications'][0]['candidate']['readiness'])
        effect = evidence._read(self.scope, receipt['operation'])
        gate = evidence._read(self.scope, receipt['source'])
        facts = evidence._read(self.scope, gate['source'])
        self.assertEqual(receipt['binding'], source_case.binding)
        self.assertEqual(effect['binding'], receipt['binding'])
        self.assertEqual(effect['source'], receipt['source'])
        self.assertEqual(effect['local_verification'], receipt['local_verification'])
        self.assertEqual(receipt['local_verification'], observed['verification'])
        self.assertEqual(gate['local_verification'], observed['verification'])
        self.assertEqual(gate['binding'], source_case.binding)
        self.assertEqual(gate['dependencies'], source.dependencies)
        self.assertEqual(facts['binding'], source_case.binding)
        self.assertEqual(facts['check_run']['repository_id'], source_case.binding['repository_id'])
        self.assertEqual(facts['outcome'], 'ready')
        self.assertGreater(observed['metadata'], 0)
        self.assertEqual(observed['publication'], 1)
        with state._database(self.scope) as db:
            records = {row['id']: row for row in delegation._ledger(self.scope, db)}
        for identity in (observed['barrier'], source.operation):
            self.assertEqual(records[identity]['data']['status'], 'settled')
            self.assertEqual(records[identity]['data']['result']['outcome'], 'completed')
        self.assertFalse(self.transport.pr['draft'])
        self.assertEqual(self.transport.labels, {'[READY]'})
        self.assertNotEqual(result['task']['state'], 'DONE')
        waiting = self.fixture.verify()
        self.assertEqual(waiting['task']['state'], 'WAITING')
        marker = waiting['task']['delivery']['merge_wait']
        self.assertEqual(marker['verification'], waiting['task']['evidence']['verification'])
        with state._database(self.scope) as db:
            self.assertTrue(delivery.manual_merge_needs_user(self.scope, db, waiting['task']))
        self.case.merged()
        self.case.reconcile('actual-gate-manual-merge')
        settled = self.operate('settle')
        self.assertEqual(settled['task']['state'], 'REVIEW')
        self.assertEqual(self.fixture.verify()['task']['state'], 'DONE')

    def test_reopen_withdraws_remote_readiness_before_acknowledging_edits(self):
        from contextlib import contextmanager
        from runtime import repository_gate
        from tests.test_repository_readiness import RepositoryReadinessTests

        self.retained_readiness()
        original = self.status()
        source_case = RepositoryReadinessTests('runTest')
        source_case.local, source_case.scope = self.fixture, self.scope
        source_case.candidate = evidence.capture_candidate(self.scope,
            evidence._load_plan(self.scope, self.fixture.identity))
        source_case.binding = delivery._publication_binding(original['plan']['publications'][0])
        source = source_case.source(withdrawal=True)
        source.repository = self.transport.adapter
        source_case.request = self.fixture.request(action='ready', publication_id='pr-0', operation_id='e' * 64)
        metadata = source.repository.mutate

        def mutate(*args, **kwargs):
            source.remote_effects = 'uncertain'
            return metadata(*args, **kwargs)

        source.repository.mutate = mutate
        fixture = self

        class Gate:
            @contextmanager
            def acquire_readiness(self, scope, request, binding):
                with repository_gate.acquire_readiness(scope, request, binding) as held:
                    fixture.assertIs(type(held), repository_gate.RepositoryReadiness)
                    fixture.assertTrue(held.withdrawal_only)
                    with fixture.assertRaises(repository_gate.GateError):
                        held.local_verification
                    yield held

        with patch.object(repository_gate, '_acquire_qualified_readiness_source', return_value=source):
            result = self.operate('reopen', gate=Gate())
        self.assertIsNone(result['plan']['publications'][0]['candidate']['readiness'])
        self.assertTrue(self.transport.pr['draft'])
        self.assertEqual(self.transport.labels, {'[BLOCKED]'})
        self.assertNotEqual(result['task']['state'], 'DONE')
        effect = evidence._read(self.scope, result['task']['delivery']['operations'][-1])
        self.assertIsNone(effect['local_verification'])
        self.assertEqual(source.remote_effects, 'uncertain')

    def test_failed_withdrawal_never_acknowledges_reopen(self):
        self.retained_readiness()
        self.transport.failed_undo = True
        with self.assertRaises(ValueError):
            self.operate('reopen', gate=self.fake_gate())
        self.assertFalse(self.transport.pr['draft'])
        self.assertNotEqual(self.status()['task']['state'], 'DONE')

    def test_observed_manual_merge_resolves_only_generated_wait_before_verification(self):
        from tests.test_title_sync_integration import NativeFixture

        waiting = self.waiting()
        marker = waiting['task']['delivery']['merge_wait']
        self.case.reconcile('manual-ready-observed')
        waiting = self.status()
        task = waiting['task']
        native = NativeFixture()
        request = {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')}
        with patch.object(Path, 'home', return_value=self.fixture.root):
            title_sync.operate(self.scope, 'configure', request | dict(expected_revision=task['revision'],
                reference='fixture:adopt', topic='Delivery'), transport=lambda: native)
        self.assertIn(' WAITING · Delivery', native.title)
        baseline = native.title.replace(' WAITING · ', ' ')

        def sync():
            return title_sync.operate(self.scope, 'sync', dict(client='codex', session_id='parent'),
                                      transport=lambda: native)

        update = {key: task[key] for key in ('state', 'reason', 'reference')}
        state.update_task(self.scope, self.fixture.request(**update, user_attention=False))
        sync()
        self.assertEqual(native.title, baseline)
        state.update_task(self.scope, self.fixture.request(**update, conditions=[dict(id='agent-check',
            kind='blocker', action='task', reason='Agent must finish a check', reference='fixture:check')]))
        sync()
        self.assertEqual(native.title, baseline)
        state.update_task(self.scope, self.fixture.request(**update,
            resolutions=[dict(id='agent-check', reference='fixture:checked')]))
        sync()
        self.assertIn(' WAITING · Delivery', native.title)
        self.case.provider.pr['draft'] = True
        self.case.reconcile('manual-back-to-draft')
        sync()
        self.assertEqual(native.title, baseline)
        self.case.merged()
        self.case.reconcile('manual-merge')
        self.assertEqual(self.status()['task']['state'], 'WAITING')
        sync()
        self.assertEqual(native.title, baseline)
        self.assertEqual(set(native.written_sessions), {request['session_id']})
        result = self.operate('settle')
        conditions = {condition['id']: condition for condition in result['task']['conditions']}
        self.assertEqual(conditions[marker['condition_id']]['status'], 'resolved')
        self.assertEqual(result['task']['state'], 'REVIEW')
        self.assertFalse(any('--method' in argv for argv, _ in self.transport.calls))
        self.assertEqual(self.fixture.verify()['task']['state'], 'DONE')

    def test_observed_merge_does_not_release_explicit_user_pause(self):
        self.waiting()
        self.case.merged()
        self.case.reconcile('manual-merge')
        state.update_task(self.scope, self.fixture.request(state='PAUSED', reason='User paused', reference='user:pause'))
        before = deepcopy(self.status())
        with self.assertRaises(ValueError):
            self.operate('settle')
        self.assertEqual(self.status(), before)
        self.assertEqual(self.transport.calls, [])

    def test_changed_merged_candidate_cannot_release_merge_wait(self):
        self.waiting()
        self.case.merged()
        self.transport.pr['head']['sha'] = 'f' * 40
        self.case.reconcile('changed-merge')
        before = self.status()
        with self.assertRaises(ValueError):
            self.operate('settle')
        self.assertEqual(self.status(), before)
        self.assertEqual(self.transport.calls, [])


if __name__ == '__main__':
    unittest.main()
