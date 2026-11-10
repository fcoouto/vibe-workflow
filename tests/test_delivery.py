"""Delivery adoption is explicit, bounded, and preserves historical decisions. Pending inventory is bounded, local and independent of checkout scans. Native ownership is supplied by a trusted host, then checked against real Git. Implementation proof and delivery completion have separate meanings. Held verification uses real local reservations; native observations are fixtures."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import delegation, delivery, evidence, state, title_sync
from tests import test_evidence


def policy(**updates):
    return dict(version=1, merge='manual', separate_worktrees=True, targets=[],
                decision_ref='user:delivery') | updates


class DeliveryPolicyTests(unittest.TestCase):
    def api(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('runtime.delivery'), 'Delivery adoption is missing')
        from runtime import delivery
        return delivery

    def test_defaults_and_task_override(self):
        api = self.api()
        saved = policy()
        original = deepcopy(saved)
        self.assertEqual(api.effective_policy(saved, None), saved)
        override = api.effective_policy(saved, dict(merge='agent', decision_ref='user:this-task'))
        self.assertEqual(override['merge'], 'agent')
        self.assertTrue(override['separate_worktrees'])
        self.assertEqual(saved, original)
        override['targets'].append('untrusted')
        self.assertEqual(saved, original)

    def test_invalid_policy_and_override_are_rejected(self):
        api = self.api()
        for invalid in (policy(version=True), policy(merge='auto'), policy(separate_worktrees=1),
                        policy(decision_ref=''), policy(extra=True), policy(targets=[{}] * 65)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                api.validate_policy(invalid)
        for invalid in ({'merge': 'agent'}, {'merge': 'agent', 'decision_ref': ''},
                        {'merge': 'agent', 'decision_ref': 'user:1', 'trusted': True}):
            with self.assertRaises(ValueError):
                api.effective_policy(policy(), invalid)

    def test_target_scope_and_duplicates_are_validated(self):
        api = self.api()
        target = dict(provider='github', host='github.com', repository='owner/repo', target='main',
                      merge_method='squash', publication_ref=None, merge_ref=None, remote_cleanup_ref=None)
        self.assertEqual(api.validate_policy(policy(targets=[target]))['targets'], [target])
        for targets in ([target, target], [target | dict(target='--all')],
                        [target | dict(host='github.com/other')], [target | dict(repository='../other')]):
            with self.assertRaises(ValueError):
                api.validate_policy(policy(targets=targets))

    def test_worktree_opt_out_survives_task_override(self):
        result = self.api().effective_policy(policy(separate_worktrees=False),
                                            dict(merge='agent', decision_ref='user:one'))
        self.assertFalse(result['separate_worktrees'])

    def test_read_only_scope_needs_no_delivery_decision(self):
        task = dict(accepted_identity=dict(delivery_policy=policy()), mode='read-only')
        result = self.api().completion_requirements(None, None, task)
        self.assertFalse(result['delivery_required'])
        self.assertTrue(result['delivery_complete'])
        self.assertEqual(result['blockers'], [])

    def test_canonical_projection_binds_policy(self):
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        from runtime import startup, state
        fixture = CanonicalBirthFixture('runTest')
        self.addCleanup(fixture.doCleanups)
        f, request, _, _ = fixture.prepare()
        record = request['manifest']['enrollment']['record']
        original = deepcopy(record)
        repository = record['repositories'][0]
        repository['delivery_policy'] = policy()
        decoded = startup.decode_project_record(record)
        projection = startup._canonical_plain(startup.canonical_scope_projection(decoded,
            repository_id=repository['repository_id'], checkout_id=repository['checkouts'][0]['checkout_id'], client='codex'))
        self.assertEqual(projection['delivery_policy'], policy())
        self.assertEqual(state._accepted_identity(projection)['delivery_policy'], policy())
        self.assertNotEqual(startup._canonical_digest(record), startup._canonical_digest(original))
        repository['delivery_policy'] = policy(merge='invalid')
        with self.assertRaises(ValueError):
            startup.decode_project_record(record)


class DeliveryPlanTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        from runtime import state
        self.fixture = CanonicalBirthFixture('runTest')
        self.addCleanup(self.fixture.doCleanups)
        prepared = self.fixture.prepare(delivery_policy=policy(), linked=getattr(self, 'linked', False))
        self.fixture.assert_applied_configured(prepared, self.fixture.invoke(prepared))
        f = prepared[0]
        from runtime import startup
        _, acquisition, _ = self.fixture.qualify_members(f, Path(prepared[1]['manifest']['enrollment']['record']['project_root']) / startup.PROJECT_FILE)
        self.scope = acquisition.resolution.scope
        self.identity = dict(client='codex', session_id='delivery', task_id='task', scope_ref='user:feature')
        state.start_task(self.scope, self.identity | dict(mode='editable', decision_ref='user:scope'))

    def request(self, **extra):
        from runtime import state
        task = state.task_status(self.scope, 'codex', 'delivery')['task']
        return self.identity | dict(expected_revision=task['revision']) | extra

    def plan(self, **updates):
        return dict(version=1, decision_ref='user:scope', policy=policy(), workspace=None,
                    publications=[], companions=[]) | updates

    def test_plan_is_persisted_and_bound_to_adopted_policy(self):
        from runtime import delivery
        result = delivery.operate(self.scope, 'plan', self.request(plan=self.plan()))
        self.assertEqual(result['plan'], self.plan())
        status = delivery.operate(self.scope, 'status', dict(client='codex', session_id='delivery'))
        self.assertEqual(status['plan'], self.plan())
        with self.assertRaises(ValueError):
            delivery.operate(self.scope, 'plan', self.request(plan=self.plan(policy=policy(merge='agent'))))

    def test_no_publication_decision_requires_explicit_plan(self):
        from runtime import delivery, state
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'delivery')
            self.assertTrue(delivery.completion_requirements(self.scope, db, task)['blockers'])
        delivery.operate(self.scope, 'plan', self.request(plan=self.plan()))
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'delivery')
            result = delivery.completion_requirements(self.scope, db, task)
            self.assertEqual(result['blockers'], [])
            self.assertTrue(result['delivery_complete'])

    def test_embedded_policy_cannot_use_boolean_integer_equality(self):
        from runtime import delivery
        for invalid in (policy(version=True), policy(separate_worktrees=1)):
            with self.subTest(policy=invalid), self.assertRaises(ValueError):
                delivery.operate(self.scope, 'plan', self.request(plan=self.plan(policy=invalid)))

    def test_policy_edit_cannot_repin_a_bound_task(self):
        import json
        from runtime import state
        path = state._path(self.scope)
        original = path.read_bytes()
        record = json.loads(self.scope.authority_path.read_bytes())
        record['repositories'][0]['delivery_policy']['merge'] = 'agent'
        self.scope.authority_path.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            state.task_status(self.scope, 'codex', 'delivery')
        self.assertEqual(path.read_bytes(), original)


class DeliveryAdoptionRecoveryTests(unittest.TestCase):
    def test_interrupted_adoption_retains_policy_and_allocation(self):
        from unittest.mock import patch
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        from runtime import changes
        from scripts.onboard_coordinator import run_verified_operation
        fixture = CanonicalBirthFixture('runTest')
        self.addCleanup(fixture.doCleanups)
        selected = policy(separate_worktrees=False)
        prepared = fixture.prepare(delivery_policy=selected)
        f, request, decision, _ = prepared

        def interrupt(stage):
            if stage == 'birth-before-commit':
                raise RuntimeError('synthetic interruption')

        with patch.object(changes, '_checkpoint', side_effect=interrupt), self.assertRaises(RuntimeError):
            fixture.invoke(prepared)
        journal = changes._load_journal(f.directory)
        allocation = deepcopy(journal['birth']['allocation'])
        self.assertEqual(journal['manifest']['enrollment']['record']['repositories'][0]['delivery_policy'], selected)
        recovery = deepcopy(decision)
        recovery['operation'] = 'resume'
        recovery['creation']['resume'] = dict(intent_digest=journal['birth']['intent_digest'],
                                            allocation=allocation, baseline=None, certificate_digest=None)
        result = run_verified_operation(recovery, f.directory, request)
        self.assertNotEqual(result['exit_code'], 0)
        retained = changes._load_journal(f.directory)
        self.assertEqual(retained['birth']['allocation'], allocation)
        self.assertEqual(retained['manifest']['enrollment']['record']['repositories'][0]['delivery_policy'], selected)


class PendingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = DeliveryPlanTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope

    def test_pages_known_tasks_without_mutation_or_silent_exhaustion(self):
        for number in range(34):
            state.start_task(self.scope, dict(client='codex', session_id='session-' + str(number),
                task_id='task', scope_ref='user:task', mode='editable', decision_ref='user:accepted'))
        database = state._path(self.scope)
        before = database.read_bytes()
        first = delivery.pending(self.scope)
        self.assertLessEqual(len(first['records']), 32)
        self.assertIsNotNone(first['cursor'])
        second = delivery.pending(self.scope, cursor=first['cursor'])
        self.assertIsNone(second['cursor'])
        records = first['records'] + second['records']
        self.assertEqual(len(records), 35)
        self.assertEqual(len({(r['scope_id'], r['client'], r['session_id'], r['task_id']) for r in records}), 35)
        self.assertTrue(all(r['blockers'] for r in records))
        self.assertEqual(database.read_bytes(), before)
        self.assertEqual(first['coverage'], 'known retained tasks in this authority')

    def test_invalid_bounds_and_cursor_fail_explicitly(self):
        for limit in (True, 0, 33, '32'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                delivery.pending(self.scope, limit=limit)
        for cursor in ('', 'not a cursor', '{}'):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                delivery.pending(self.scope, cursor=cursor)

    def test_explicit_no_delivery_decision_is_not_listed_as_pending(self):
        delivery.operate(self.scope, 'plan', self.fixture.request(plan=self.fixture.plan()))
        result = delivery.pending(self.scope)
        self.assertEqual(result['records'], [])
        self.assertIsNone(result['cursor'])

    def test_inventory_survives_a_client_removed_checkout(self):
        import shutil
        from pathlib import Path
        from runtime import startup
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        host = CanonicalBirthFixture('runTest')
        self.addCleanup(host.doCleanups)
        prepared = host.prepare(peer=True, delivery_policy=policy())
        host.assert_applied_configured(prepared, host.invoke(prepared))
        fixture = prepared[0]
        authority = Path(prepared[1]['manifest']['enrollment']['record']['project_root']) / startup.PROJECT_FILE
        host.qualify_members(fixture, authority)
        scopes = [member.resolution.scope for member in fixture.member_acquisitions.values()]
        self.assertEqual(len(scopes), 2)
        for number, scope in enumerate(scopes):
            state.start_task(scope, dict(client='codex', session_id='peer-' + str(number), task_id='task',
                scope_ref='user:peer', mode='editable', decision_ref='user:accepted'))
        shutil.rmtree(scopes[1].repository_root)
        result = delivery.pending(scopes[0])
        self.assertEqual({row['session_id'] for row in result['records']}, {'peer-0', 'peer-1'})


class DeliveryWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = DeliveryPlanTests('runTest')
        self.fixture.linked = True
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope
        from runtime import delivery
        delivery.operate(self.scope, 'plan', self.fixture.request(plan=self.fixture.plan()))

    def owner(self, request, observation):
        self.assertIsNotNone(importlib.util.find_spec('scripts.delivery_coordinator'), 'Delivery owner is missing')
        from scripts.delivery_coordinator import DeliveryOperationOwner

        class Native:
            def observe_worktree(native, root):
                return observation() if callable(observation) else observation

        decision = dict(action='bind-worktree', request_digest=evidence._digest(request),
                        accepted_scope_digest=self.scope.accepted_scope_digest, reference='fixture:host-observation')
        return DeliveryOperationOwner(decision, native=Native(), repository=None, gate=None)

    def observation(self):
        return dict(root=str(self.scope.repository_root), owner='codex',
                    attachment='fixture:native-managed-worktree', reference='fixture:observed-attachment')

    def test_binds_actual_admitted_destination_without_rebinding_task(self):
        request = self.fixture.request(operation_id='workspace-1')
        result = self.owner(request, self.observation()).run(self.scope, 'bind-worktree', request)
        self.assertEqual(result['plan']['workspace']['root'], str(self.scope.repository_root))
        self.assertNotEqual(self.scope.git_dir, self.scope.git_common_dir)
        self.assertEqual(result['task']['accepted_identity']['checkout_id'], self.scope.checkout_id)
        self.assertEqual(result['plan']['workspace']['cleanup_owner'], 'client')

    def test_uncertain_or_foreign_native_attachment_does_not_bind(self):
        from runtime import delivery
        for observation in (None, self.observation() | dict(root=str(self.scope.primary_root))):
            request = self.fixture.request(operation_id='workspace-1')
            with self.assertRaises(ValueError):
                self.owner(request, observation).run(self.scope, 'bind-worktree', request)
            status = delivery.operate(self.scope, 'status', dict(client='codex', session_id='delivery'))
            self.assertIsNone(status['plan']['workspace'])

    def test_untrusted_request_or_changed_owner_binding_cannot_authorize(self):
        from runtime import delivery
        request = self.fixture.request(operation_id='workspace-1')
        with self.assertRaises(ValueError):
            delivery.operate(self.scope, 'bind-worktree', request)
        owner = self.owner(request, self.observation())
        with self.assertRaises(ValueError):
            owner.run(self.scope, 'bind-worktree', request | dict(operation_id='different'))

    def test_replay_reuses_completed_binding_without_another_native_observation(self):
        request = self.fixture.request(operation_id='workspace-1')
        owner = self.owner(request, self.observation())
        first = owner.run(self.scope, 'bind-worktree', request)
        second = owner.run(self.scope, 'bind-worktree', request)
        self.assertEqual(first['task']['revision'], second['task']['revision'])
        self.assertEqual(first['plan'], second['plan'])

    def test_replaced_checkout_cannot_bind_before_or_during_native_observation(self):
        import shutil
        request = self.fixture.request(operation_id='workspace-1')
        root = self.scope.repository_root
        original = root.with_name(root.name + '-original')
        database = state._path(self.scope)
        before = database.read_bytes()

        def replace():
            root.rename(original)
            shutil.copytree(original, root)
            return self.observation()

        with self.assertRaises(ValueError):
            self.owner(request, replace).run(self.scope, 'bind-worktree', request)
        self.assertEqual(database.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.owner(request, self.observation()).run(self.scope, 'bind-worktree', request)
        self.assertEqual(database.read_bytes(), before)


class DeliveryOwnerPinTests(unittest.TestCase):
    def test_coordinator_code_changes_invalidate_runtime_pin(self):
        import shutil
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory)
            for source in [*state._runtime_sources(), state.PACKAGE / 'scripts/delivery_coordinator.py']:
                target = package / source.relative_to(state.PACKAGE)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            with patch.object(state, 'PACKAGE', package):
                before = state.runtime_fingerprint()
                with (package / 'scripts/delivery_coordinator.py').open('a') as stream:
                    stream.write('\n# changed owner implementation\n')
                self.assertNotEqual(before, state.runtime_fingerprint())


def target():
    return dict(provider='github', host='github.com', repository='owner/repo', target='main',
                merge_method='squash', publication_ref='user:publish', merge_ref=None,
                remote_cleanup_ref=None)


class MergeWaitBindingTests(unittest.TestCase):
    def test_manual_merge_title_uses_latest_applicable_provider_facts(self):
        publication = dict(id='pr', candidate=dict(observation='initial', readiness='receipt'))
        records = dict(initial=dict(observation=dict(draft=False, mergeability=True)),
            receipt=dict(operation='ready', binding='binding'),
            ready=dict(kind='delivery-effect', action='ready', status='completed', binding='binding',
                previous_plan='plan', observation=dict(draft=False, mergeability=False)),
            plan=dict(publications=[dict(id='pr', candidate=dict(observation='initial'))]),
            later=dict(observation=dict(draft=False, mergeability=True)))
        self.assertFalse(delivery._manual_publication_needs_user(publication, records.__getitem__))
        publication['candidate']['observation'] = 'later'
        self.assertTrue(delivery._manual_publication_needs_user(publication, records.__getitem__))

    def test_changed_plan_cannot_reuse_the_old_wait_marker(self):
        reference = lambda char: dict(name=char * 32 + '.json', digest=char * 64)
        condition = dict(id='delivery:merge', kind='blocker', action='task', status='pending',
            reason='Implementation verified; required delivery remains pending', reference='delivery:completion')
        task = dict(state='WAITING', accepted_identity=dict(delivery_policy=policy()), conditions=[condition],
            evidence=dict(verification=reference('a')), delivery=dict(plan=reference('b'), operations=[],
            merge_wait=dict(condition_id=condition['id'], verification=reference('a'), plan=reference('c'))))
        self.assertEqual(delivery.implementation_task(task)['conditions'], [condition])

    def test_generated_wait_preserves_a_resolved_user_condition_with_same_id(self):
        reference = dict(name='a' * 32 + '.json', digest='a' * 64)
        user_condition = dict(id='delivery:merge', kind='decision', action='task', status='resolved',
            reason='User scope', reference='user:scope', resolution_ref='user:chosen')
        task = dict(state='REVIEW', accepted_identity=dict(delivery_policy=policy()), conditions=[user_condition],
            evidence=dict(verification=reference), delivery=dict(plan=reference, operations=[]))
        delivery.apply_completion(task, dict(delivery_complete=False, blockers=[], pending=['pr-0'],
                                            waiting_for_merge=True), True)
        self.assertEqual(task['conditions'][0], user_condition)
        self.assertEqual(task['state'], 'WAITING')
        self.assertEqual(delivery.implementation_task(task)['conditions'], [user_condition])


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_evidence.EvidenceTests('runTest')
        self.fixture._delivery_policy = policy(targets=[target()], separate_worktrees=getattr(self, 'separate_worktrees', True))
        self.fixture._canonical_linked = getattr(self, 'linked', False)
        self.fixture._canonical_peer = getattr(self, 'peer', False)
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope

    def delivery_plan(self, count=0, *, required=True):
        publications = [dict(id='pr-' + str(n), **{k: target()[k] for k in
            ('provider', 'host', 'repository', 'target')}, branch='feature-' + str(n),
            candidate=None, pull_request=None, dependencies=[], required=required) for n in range(count)]
        return delivery.operate(self.scope, 'plan', self.fixture.request(plan=dict(version=1,
            decision_ref='user:scope', policy=self.fixture._delivery_policy, workspace=None,
            publications=publications, companions=[])))

    def proof(self):
        self.fixture.ready_proof_fixture()
        return self.fixture.verify()

    def test_missing_delivery_decision_blocks_overall_completion_only(self):
        result = self.proof()
        self.assertTrue(result['implementation_verified'])
        self.assertFalse(result['verified'])
        self.assertFalse(result['delivery_complete'])
        self.assertNotEqual(result['task']['state'], 'DONE')

        self.assertIn('Delivery scope decision is missing', result['blockers'])

    def test_explicit_no_publication_scope_allows_done(self):
        self.delivery_plan()
        result = self.proof()
        self.assertTrue(result['implementation_verified'])
        self.assertTrue(result['delivery_complete'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['task']['state'], 'DONE')

    def test_user_condition_with_delivery_like_id_still_blocks_completion(self):
        self.delivery_plan()
        state.update_task(self.scope, self.fixture.request(state='WORKING', reason='User decision pending',
            reference='user:decision', conditions=[dict(id='delivery:merge', kind='decision', action='task',
            reason='User must choose scope', reference='user:scope')]))
        result = self.proof()
        self.assertFalse(result['verified'])
        self.assertFalse(result['implementation_verified'])
        self.assertNotEqual(result['task']['state'], 'DONE')
        status = self.fixture.api.evidence_status(self.scope, 'codex', 'parent')
        self.assertIn('Task conditions remain unresolved', status['evidence']['blockers'])
        self.assertFalse(status['evidence']['implementation_verified'])

    def test_status_requires_a_current_verification_receipt(self):
        self.delivery_plan()
        self.fixture.ready_proof_fixture()
        status = self.fixture.api.evidence_status(self.scope, 'codex', 'parent')
        self.assertFalse(status['evidence']['implementation_verified'])

    def test_accepted_scope_extension_reopens_done_before_recomputing_progress(self):
        initial = self.delivery_plan()
        old_milestone = dict(id='old-scope', weight=1, accepted=True, reference='user:accepted')
        state.update_task(self.scope, self.fixture.request(state='WORKING', reason='Accepted scope',
            reference='user:accepted', milestones=[old_milestone]))
        completed = self.proof()['task']
        plan = initial['plan'] | dict(decision_ref='user:expanded', publications=[dict(id='new-pr',
            **{k: target()[k] for k in ('provider', 'host', 'repository', 'target')}, branch='new-feature',
            candidate=None, pull_request=None, dependencies=[], required=True)])
        result = delivery.operate(self.scope, 'plan', self.fixture.request(plan=plan,
            previous_plan=completed['delivery']['plan'], change_decision_ref='user:expanded',
            milestones=[old_milestone, dict(id='new-scope', weight=1, accepted=False, reference='user:expanded')]))
        self.assertEqual(result['task']['state'], 'REVIEW')
        self.assertFalse(result['delivery_complete'])
        self.assertIn('new-pr', result['pending'])
        self.assertEqual(title_sync.progress_cells(result['task']), 2)
        self.assertEqual(self.fixture.api._read(self.scope, initial['task']['delivery']['plan']), initial['plan'])
        title = title_sync.format_title('Feature', result['task']['state'], 5)
        self.assertNotIn('▰▰▰▰▰', title)

    def test_scope_revision_preserves_explicit_pause_and_required_deliveries(self):
        initial = self.delivery_plan(1)
        state.update_task(self.scope, self.fixture.request(state='PAUSED', reason='User pause', reference='user:pause'))
        revised = initial['plan'] | dict(decision_ref='user:expanded')
        result = delivery.operate(self.scope, 'plan', self.fixture.request(plan=revised,
            previous_plan=initial['task']['delivery']['plan'], change_decision_ref='user:expanded'))
        self.assertEqual(result['task']['state'], 'PAUSED')
        with self.assertRaises(ValueError):
            delivery.operate(self.scope, 'plan', self.fixture.request(plan=revised | dict(publications=[]),
                previous_plan=result['task']['delivery']['plan'], change_decision_ref='user:expanded'))

    def test_unpublished_required_publications_keep_publication_work_open(self):
        self.delivery_plan(2)
        result = self.proof()
        self.assertTrue(result['implementation_verified'])
        self.assertFalse(result['delivery_complete'])
        self.assertFalse(result['verified'])
        self.assertEqual(result['task']['state'], 'REVIEW')
        conditions = result['task']['conditions']
        self.assertEqual([c['id'] for c in conditions if c['status'] == 'pending'], [])
        title = title_sync.format_title('Feature', result['task']['state'], 4)
        self.assertEqual(title, '\uFFFB▰▰▰▰▱ Feature')
        self.assertNotIn('▰▰▰▰▰', title)
        status = self.fixture.api.evidence_status(self.scope, 'codex', 'parent')
        self.assertFalse(status['evidence']['delivery_complete'])
        self.assertEqual(status['task']['state'], 'REVIEW')
        with state._database(self.scope) as db:
            proof = self.fixture.api._read(self.scope, result['task']['evidence']['verification'], db=db)
        self.assertEqual(proof['blockers'], [])
        self.assertFalse(result['waiting_for_merge'])

    def test_merge_evidence_does_not_require_the_merge_to_have_happened(self):
        self.delivery_plan(1)
        self.fixture.plan()
        self.fixture.checked()
        self.fixture.review()
        result = self.fixture.api.verify_completion(self.scope, self.fixture.request(purpose='merge-evidence'))
        self.assertTrue(result['verified'])
        self.assertTrue(result['implementation_verified'])
        self.assertFalse(result['delivery_complete'])
        self.assertNotEqual(result['task']['state'], 'DONE')


class LinkedCompletionTests(unittest.TestCase):
    def linked(self):
        from tests import test_canonical_composition as canonical
        linked = canonical.CanonicalLinkedLifecycleTests('runTest')
        linked.api = test_evidence.importlib.import_module('runtime.evidence')
        linked.members = []
        for number in range(2):
            case = CompletionTests('runTest')
            case.setUp()
            self.addCleanup(case.doCleanups)
            case.delivery_plan(number)
            linked.members.append(case.fixture)
        linked.selected = linked.members[0]
        linked.establish()
        return linked

    def test_required_child_delivery_does_not_complete_parent_acceptance(self):
        linked = self.linked()
        result = linked.verify()
        self.assertTrue(result['implementation_verified'], result)
        self.assertFalse(result['verified'], result)
        self.assertFalse(result['delivery_complete'], result)
        self.assertEqual(result['task']['state'], 'REVIEW')
        self.assertNotEqual(linked.task(linked.members[1])['state'], 'DONE')

    def test_factual_lifecycle_cannot_release_pending_publication(self):
        linked = self.linked()
        for member in linked.members:
            linked.api._verify_member(member.scope, member.request(purpose='completion'), linked=True)
        result = linked.api._acquire_linked_basis(linked.selected.scope,
            linked.selected.request(purpose='completion'), linked.observations())
        self.assertFalse(result['basis']['delivery_complete'], result)


class MergeVerificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_evidence.EvidenceTests('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.scope = self.fixture.scope
        self.fixture.plan()
        self.fixture.checked()
        self.fixture.review()

    def acquire(self):
        acquire = getattr(evidence, 'acquire_merge_verification', None)
        self.assertTrue(callable(acquire), 'Merge publication needs a live verification reservation')
        return acquire(self.scope, self.fixture.request(purpose='merge-evidence'))

    def record(self, grant):
        with state._database(self.scope) as db:
            return next(row for row in delegation._ledger(self.scope, db)
                        if row['id'] == grant.barrier_id)

    def test_publication_keeps_actual_reservation_until_local_finalize(self):
        with self.acquire() as grant:
            self.assertEqual(self.record(grant)['data']['status'], 'prepared')
            self.assertFalse(grant.recheck()['merge_authority'])
            with state._database(self.scope) as db:
                task = state._task(db, state._scope_id(self.scope), 'codex', 'parent')
                self.assertEqual(task['evidence']['verification'], grant.verification_ref)
                claims = delegation._claims([dict(kind='path', key=str(self.fixture.repo / 'app.py'), mode='write')])
                with self.assertRaises(state.StateError):
                    delegation._resources_available(self.scope, db, claims, delegation._ledger(self.scope, db))
            seen = []
            def publish(db):
                self.assertTrue(db.in_transaction)
                seen.append(db)
                return grant.record(dict(version=1, kind='fixture-publication',
                    verification=grant.verification_ref), db=db)
            reference = grant.finalize(publish)
            self.assertEqual(len(seen), 1)
            self.assertEqual(evidence._read(self.scope, reference)['kind'], 'fixture-publication')
            with self.assertRaises(state.StateError):
                grant.finalize(publish)
        with state._database(self.scope) as db:
            records = delegation._ledger(self.scope, db)
            self.assertTrue(all(row['data']['status'] == 'settled' for row in records))
        with self.assertRaises(state.StateError):
            grant.recheck()

    def test_uncertain_publication_and_context_close_keep_recovery_owner(self):
        with self.acquire() as grant:
            barrier = grant.barrier_id
            with self.assertRaisesRegex(RuntimeError, 'uncertain publication'):
                grant.finalize(lambda db: (_ for _ in ()).throw(RuntimeError('uncertain publication')))
            self.assertEqual(self.record(grant)['data']['status'], 'prepared')
        with self.assertRaises(state.StateError):
            grant.recheck()
        with state._database(self.scope) as db:
            actual = next(row for row in delegation._ledger(self.scope, db) if row['id'] == barrier)
            self.assertEqual(actual['data']['status'], 'prepared')
            self.assertIsNone(actual['data']['reconciliation'])

    def test_changed_candidate_cannot_reach_publication_callback(self):
        with self.acquire() as grant:
            (self.fixture.repo / 'app.py').write_text('value = 2\n')
            def forbidden(db):
                self.fail('Changed candidate reached publication')
            with self.assertRaises(state.StateError):
                grant.finalize(forbidden)
            self.assertEqual(self.record(grant)['data']['status'], 'prepared')

    def test_ordinary_completion_keeps_settle_on_return(self):
        result = self.fixture.verify()
        self.assertTrue(result['verified'], result)
        with state._database(self.scope) as db:
            records = delegation._ledger(self.scope, db)
        self.assertTrue(records)
        self.assertTrue(all(row['data']['status'] == 'settled' for row in records))


if __name__ == '__main__':
    unittest.main()
