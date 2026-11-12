"""Private guidance-cache fixtures; no network, models or live client changes."""
from tests.fixture_canonical_birth import fixture_artifact
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
import subprocess
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from runtime import delegation, evidence, state, task_profile
from tests.fixture_delegation import AdmissionFixture


class RosterRoleTests(AdmissionFixture, unittest.TestCase):
    """F2.3: new plans name roster specialists or `check`; stored plans and records keep their old role names."""
    def legacy_writer(self):
        """A pre-F2 writer: the old plan role names were accepted as new input then."""
        original = delegation._delegation_plan
        return patch.object(delegation, '_delegation_plan', lambda scope, plan, new=False: original(scope, plan))

    def test_plan_accepts_roster_specialists_and_check_jobs(self):
        value = self.plan_value()
        value['jobs'][0]['role'] = 'backend-specialist'
        value['jobs'] += [dict(id='look', role='debugger', claims=[self.path_claim('look.py', mode='read')],
                               dependencies=[], max_attempts=1),
                          dict(id='review', role='code-reviewer', claims=[self.path_claim(mode='read')],
                               dependencies=['edit'], max_attempts=1),
                          dict(id='unit', role='check', claims=[self.path_claim(mode='read')], dependencies=['edit'],
                               max_attempts=1)]
        self.assertTrue(self.plan(value)['ok'])

    def test_new_plan_refuses_an_old_role_name(self):
        for old in ('worker', 'review', 'coordinator', 'implementer', 'researcher', 'wizard'):
            value = self.plan_value()
            value['jobs'][0]['role'] = old
            with self.subTest(old), self.assertRaisesRegex(state.StateError, 'Invalid job identity or role'):
                self.plan(value)

    def test_stored_plan_with_legacy_roles_still_reads(self):
        value = self.plan_value()
        value['jobs'][0]['role'] = 'worker'
        value['jobs'] += [dict(id='review', role='review', claims=[self.path_claim(mode='read')],
                               dependencies=['edit'], max_attempts=1),
                          dict(id='own', role='coordinator', claims=[self.path_claim('own.py')], dependencies=[],
                               max_attempts=1),
                          dict(id='unit', role='check', claims=[self.path_claim(mode='read')], dependencies=['edit'],
                               max_attempts=1)]
        with self.legacy_writer():
            self.plan(value)
        with state._database(self.scope) as db:
            task = evidence._task(db, self.scope, self.request())
        self.assertEqual([job['role'] for job in delegation._task_plan(self.scope, task)['jobs']],
                         ['worker', 'review', 'coordinator', 'check'])
        prepared = self.prepare()  # the stored worker job dispatches as before
        self.assertTrue(prepared['launch_admitted'])
        record = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(record['task_name'], 'worker_' + prepared['attempt_id'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_non_writing_specialist_needs_read_only_claims(self):
        for role in ('debugger', 'code-reviewer', 'product-manager'):
            value = self.plan_value()
            value['jobs'][0]['role'] = role
            with self.subTest(role), self.assertRaises(state.StateError):
                self.plan(value)
        value = self.plan_value()
        value['jobs'][0]['role'] = 'orchestrator'  # the coordinator's own job keeps write ownership
        self.assertTrue(self.plan(value)['ok'])

    def test_new_records_carry_the_job_specialist(self):
        value = self.plan_value()
        value['jobs'][0]['role'] = 'backend-specialist'
        self.plan(value)
        prepared = self.prepare()
        body = json.loads(prepared['prompt'])['assignment']
        self.assertEqual(body['role'], 'backend-specialist')
        self.assertEqual(self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])['task_name'],
                         'worker_' + prepared['attempt_id'])
        self.assertFalse(hasattr(delegation, 'NATIVE_ROLES'))


class AdmissionTests(AdmissionFixture, unittest.TestCase):
    def test_invalid_visibility_does_not_prevent_managed_worker_admission(self):
        self.plan()
        prepared = self.prepare(visibility=dict(version=1, role='Default', specialty='Parser fixes'))
        self.assertTrue(prepared['launch_admitted'])
        self.assertNotIn('visibility', prepared)

    def test_visibility_is_bound_to_worker_replay_without_granting_authority(self):
        self.plan()
        visibility = dict(version=2, role='software-engineer', specialty='Parser fixes', custom_role_name='Parser owner')
        request = self.prepare_request(visibility=visibility)
        try:
            prepared = self.api.prepare_assignment(self.scope, request)
        except state.StateError as error:
            self.fail('Native worker visibility was rejected: ' + str(error))
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(prepared['visibility'], visibility)
        self.assertEqual(binding['visibility'], visibility)
        self.assertEqual(binding['task_name'], 'worker_' + prepared['attempt_id'])
        body = json.loads(prepared['prompt'])['assignment']
        self.assertEqual(body['role'], 'software-engineer')
        self.assertEqual(body['request']['visibility'], visibility)
        replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['attempt_id'], prepared['attempt_id'])
        self.assertEqual(replay['visibility'], visibility)
        for changed in (dict(role='code-reviewer'), dict(specialty='Other work'), dict(custom_role_name='Other owner')):
            with self.subTest(changed=changed), self.assertRaisesRegex(state.StateError, 'changed payload'):
                self.api.prepare_assignment(self.scope, request | dict(visibility=visibility | changed))
        status = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(status['budgets']['attempts_consumed'], 1)
        self.assertEqual(status['budgets']['resource_units_consumed'], 1)
        invalid_body = deepcopy(body)
        invalid_body['request']['visibility']['role'] = 'Unknown'
        invalid = self.raw_assignment(prepared['attempt_id'])
        invalid['assignment'] = fixture_artifact(self.scope, self.identity, invalid_body)
        invalid['request_digest'] = evidence._digest(invalid_body['request'])
        with state._database(self.scope) as db:
            row = db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments where id=?',
                (prepared['attempt_id'],)).fetchone()
            with self.assertRaisesRegex(state.StateError, 'visibility'):
                self.api._record_data(self.scope, row[:-1] + (json.dumps(invalid),), invalid, db=db)

    def test_native_lifecycle_and_new_task_ignore_unrelated_settled_history(self):
        self.plan()
        previous = self.prepare()
        self.settle_fixture(previous['attempt_id'])
        request = self.second_owner(2)
        self.identity = {key: request[key] for key in evidence.IDENTITY[:4]}
        with patch.object(self.api, 'MAX_ASSIGNMENTS', 1):
            prepared = self.api.prepare_assignment(self.scope, request)
            self.assertTrue(prepared['launch_admitted'])
            binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
            self.assertEqual(binding['parent_session_id'], self.identity['session_id'])
            observed = self.observe_worker(prepared)
            self.assertEqual(observed['attempt_id'], prepared['attempt_id'])
            fresh = state.start_task(self.scope, self.identity | dict(session_id='fresh', task_id='fresh',
                mode='editable', decision_ref='user:accepted'))
            self.assertEqual(fresh['task']['task_id'], 'fresh')

    def test_plan_is_immutable_and_replay_returns_once_only_admission(self):
        self.plan()
        request = self.prepare_request()
        first = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(first['launch_admitted'])
        self.assertFalse(first['replayed'])
        calls = self.discover.call_count
        for _ in range(3):
            replay = self.api.prepare_assignment(self.scope, request)
            self.assertFalse(replay['launch_admitted'])
            self.assertTrue(replay['replayed'])
            self.assertEqual(replay['attempt_id'], first['attempt_id'])
        self.assertEqual(self.discover.call_count, calls)
        current = self.request()
        replay = self.api.prepare_assignment(self.scope, request | dict(expected_revision=current['expected_revision']))
        self.assertFalse(replay['launch_admitted'])
        for delta in (dict(resource_units=2), dict(selection=request['selection'] | dict(effort='low'))):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request | delta)
        altered = self.plan_value()
        altered['limits']['max_attempts'] += 1
        with self.assertRaises(state.StateError):
            self.plan(altered)
        report = self.api.delegation_status(self.scope, 'codex', 'parent')
        self.assertEqual(report['budgets']['attempts_consumed'], 1)
        self.assertEqual(report['budgets']['resource_units_consumed'], 1)
        self.assertEqual(len(report['assignments']), 1)
        binding = self.api.prepared_native_assignment(self.scope, first['attempt_id'])
        self.assertEqual(set(binding), {'attempt_id','parent_session_id','task_name','assignment_digest',
            'candidate_digest','requested_model','requested_effort','client_surface','native_version','pins','accepted_identity','scope_id'})
        self.assertEqual(binding['task_name'], 'worker_' + first['attempt_id'])
        self.assertEqual(json.loads(first['prompt'])['assignment_digest'], binding['assignment_digest'])

    def test_fifteen_conflicting_sessions_reserve_exactly_once(self):
        requests = [self.second_owner(n) for n in range(15)]
        barrier = threading.Barrier(15)
        def prepare(request):
            barrier.wait(timeout=10)
            try:
                return self.api.prepare_assignment(self.scope, request)
            except state.StateError:
                return None
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(prepare, requests))
        self.assertEqual(sum(bool(result and result['launch_admitted']) for result in results), 1)
        with state._database(self.scope) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0], 1)

    def test_actual_discovery_runs_outside_locks_and_caller_expectations_cannot_forge_catalog(self):
        self.plan()
        original = self.discover.side_effect
        def observe(scope, controls):
            with state._database(scope, write=True):
                pass
            return original(scope, controls)
        self.discover.side_effect = observe
        request = self.prepare_request()
        self.catalog['catalog'][0]['model'] = 'different-actual-model'
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)
        self.catalog['catalog'][0]['model'] = 'new-native-model'
        for key, value in (('parent_model_provider', None), ('capability_digest', 'f' * 64),
                           ('client_surface', 'codex-desktop')):
            with self.subTest(key=key):
                before = self.catalog[key]
                self.catalog[key] = value
                with self.assertRaises(state.StateError):
                    self.api.prepare_assignment(self.scope, request)
                self.catalog[key] = before
        result = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(result['launch_admitted'])
        data = self.raw_assignment(result['attempt_id'])
        from runtime import evidence
        assignment = evidence._read(self.scope, data['assignment'])
        self.assertEqual(evidence._read(self.scope, assignment['discovery']), self.catalog)
        self.assertIn('policy', assignment)
        self.assertEqual(evidence._read(self.scope, assignment['policy'])['policy_digest'], self.scope.policy_digest)

    def test_path_hardlink_symlink_and_case_conflicts(self):
        self.plan()
        requests = [self.second_owner(number, [self.path_claim(name)])
                    for number, name in enumerate(('hard.py', 'alias.py', 'APP.py'))]
        self.prepare()
        os.link(self.repo / 'app.py', self.repo / 'hard.py')
        (self.repo / 'alias.py').symlink_to(self.repo / 'app.py')
        for request in requests:
            with self.subTest(path=request['brief']['files']):
                with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
                    self.api.prepare_assignment(self.scope, request)

    def test_directory_ancestor_conflicts(self):
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim('work/child.py')]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(files=['work/child.py'], resources=value['jobs'][0]['claims'])
        self.api.prepare_assignment(self.scope, request)
        other = self.second_owner(1, [self.path_claim('work')])
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)

    def test_disjoint_claims_are_admitted_beyond_a_declared_count(self):
        """F5.A1 (was: disjoint claims respect capacity): a declared count of two is not admission."""
        self.plan()
        self.prepare()
        for n, name in ((1, 'other.py'), (2, 'third.py')):
            other = self.second_owner(n, [self.path_claim(name)], dict(max_parallel_workers=2, host_capacity=2))
            self.assertTrue(self.api.prepare_assignment(self.scope, other)['launch_admitted'], name)

    def test_cross_scope_claims_and_explicit_shared_services_conflict(self):
        shared = dict(kind='service', host='local', key='fixture:shared-db', mode='write')
        value = self.plan_value()
        value['jobs'][0]['claims'] = [shared]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=[shared], files=[])
        self.api.prepare_assignment(self.scope, request)
        other_scope = self.peer_scope
        request = self.second_owner(1, [shared], scope=other_scope)
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(other_scope, request)
        request = self.second_owner(2, [shared])
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)

    def test_unknown_authority_hard_controls_and_non_native_route_block(self):
        for mutate in (lambda p:p['authority'].update(ownership_known=False),
                       lambda p:p['authority'].update(state_dir=str(self.root / 'different-state')),
                       lambda p:p['route'].update(mechanism='external-api'),
                       lambda p:p['selection_constraints'].update(required_controls=['writable_paths'])):
            with self.subTest(mutate=mutate):
                value = self.plan_value()
                mutate(value)
                with self.assertRaises(state.StateError):
                    self.plan(value)

    def test_plan_jobs_accept_an_optional_task_profile_and_reject_invalid_ones(self):
        value = self.plan_value()
        value['jobs'][0]['profile'] = dict(task_family='implementation', complexity_band='medium',
                                           risk_band='high', review_policy='independent', tools=['edit'])
        self.assertTrue(self.plan(value)['ok'])
        for bad in (dict(complexity_band='enormous'), dict(unknown=1), 'text'):
            invalid = self.plan_value()
            invalid['jobs'][0]['profile'] = bad
            with self.assertRaises(state.StateError):
                self.plan(invalid)

    def test_dependency_cycles_unknowns_and_unsettled_predecessors_block(self):
        value = self.plan_value()
        value['jobs'].append(dict(id='after', role='software-engineer', claims=[self.path_claim('after.py')],
            dependencies=['edit'], max_attempts=1))
        cycle = deepcopy(value)
        cycle['jobs'][0]['dependencies'] = ['after']
        with self.assertRaises(state.StateError):
            self.plan(cycle)
        missing = deepcopy(value)
        missing['jobs'][0]['dependencies'] = ['absent']
        with self.assertRaises(state.StateError):
            self.plan(missing)
        self.plan(value)
        after = self.prepare_request(job_id='after', dispatch_id='after-1')
        after['brief']['resources'] = value['jobs'][1]['claims']
        after['brief']['files'] = ['after.py']
        after['brief']['dependencies'] = ['edit']
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, after)
        first = self.prepare()
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, after | dict(expected_revision=self.request()['expected_revision']))
        self.settle_fixture(first['attempt_id'], 'completed')
        self.assertTrue(self.api.prepare_assignment(self.scope,
            after | dict(expected_revision=self.request()['expected_revision']))['launch_admitted'])

    def test_retry_preserves_original_finite_attempt_and_resource_budgets(self):
        value = self.plan_value()
        value['limits'].update(max_attempts=2, max_resource_units=2)
        value['jobs'][0]['max_attempts'] = 2
        self.plan(value)
        first = self.prepare()
        retry = self.prepare_request(dispatch_id='retry-1', retry_of=first['attempt_id'], diagnostic=dict(
            prior_attempt_id=first['attempt_id'], cause='Fixture failure identified', evidence=['host:new-observation'],
            change='Correct bounded input before retry'))
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, retry)
        self.settle_fixture(first['attempt_id'])
        second = self.api.prepare_assignment(self.scope, retry)
        self.assertTrue(second['launch_admitted'])
        self.settle_fixture(second['attempt_id'])
        retry.update(dispatch_id='retry-2', retry_of=second['attempt_id'], expected_revision=self.request()['expected_revision'])
        retry['diagnostic']['prior_attempt_id'] = second['attempt_id']
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, retry)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 2)
        with self.assertRaises(state.StateError):
            self.plan(value | dict(fallback=dict(action='direct-coordinator', reference='new:does-not-reset')))

    def test_stale_guidance_is_usable_but_changed_snapshot_and_unsupported_selection_block(self):
        self.plan()
        claim = self.api.claim_guidance(self.scope, self.cache_request | dict(trigger=dict(kind='manual', id='offline')))
        self.api.record_guidance(self.scope, self.cache_request | dict(token=claim['token'], generation=claim['generation'],
            coordinator_ref='coordinator:offline', failure='Offline refresh'))
        request = self.prepare_request()
        self.assertEqual(request['guidance']['freshness'], 'stale')
        for delta in (dict(guidance=request['guidance'] | dict(snapshot_digest='c' * 64)),
                      dict(selection=request['selection'] | dict(effort='unsupported')),
                      dict(selection=request['selection'] | dict(profile_ids=['missing']))):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request | delta)
        self.assertTrue(self.api.prepare_assignment(self.scope, request)['launch_admitted'])

    def test_deadline_resources_revision_and_unresolved_old_evidence_block(self):
        value = self.plan_value()
        value['limits']['max_resource_units'] = 1
        self.plan(value)
        with self.assertRaises(state.StateError):
            self.prepare(resource_units=2)
        request = self.prepare_request()
        with patch.object(self.api, '_now', return_value=value['limits']['deadline'] + 1):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request)
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request | dict(expected_revision=1))
        self.legacy_unlinked_review_fixture(self.scope, self.identity)
        with self.assertRaises(state.StateError):
            self.prepare()

    def test_directory_then_hardlinked_descendant_alias_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('hardlink', True)

    def test_hardlinked_descendant_alias_then_directory_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('hardlink', False)

    def test_directory_then_symlink_descendant_target_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('symlink', True)

    def test_symlink_descendant_target_then_directory_conflicts(self):
        self.assert_directory_alias_claims_are_exclusive('symlink', False)

    def test_linked_checkout_git_marker_is_coordinator_owned(self):
        scope=self.scope
        self.assertTrue((scope.repository_root/'.git').is_file())
        self.assertEqual(Path(self.api._git_directory(scope)),scope.git_dir)
        with self.assertRaises(state.StateError):
            request=self.second_owner(1,[dict(kind='path',key=str(scope.repository_root/'.git'),mode='write')])
            self.api.prepare_assignment(scope,request)

    def test_actual_private_worktree_git_metadata_is_coordinator_owned(self):
        scope=self.scope
        private=Path(subprocess.check_output(['git','-C',str(scope.repository_root),'rev-parse',
            '--path-format=absolute','--git-dir'],text=True).strip())
        self.assertEqual(private,scope.git_dir)
        self.assertEqual(Path(self.api._git_directory(scope)),private)
        # Actual private metadata outside this checkout cannot become a worker write claim.
        with self.assertRaises(state.StateError):
            request=self.second_owner(1,[dict(kind='path',key=str(private/'HEAD'),mode='write')])
            self.api.prepare_assignment(scope,request)

    def test_git_marker_retarget_during_discovery_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('marker')

    def test_git_private_alias_retarget_with_unchanged_marker_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('private-alias')

    def test_git_commondir_byte_change_during_discovery_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('commondir-bytes')

    def test_git_common_alias_retarget_after_candidate_capture_blocks_commit(self):
        self.assert_git_routing_change_is_rejected_at_commit('common-alias')

    def test_unchanged_git_routing_admits_and_replay_skips_git_and_native_observation(self):
        from runtime import evidence
        self.plan()
        request = self.prepare_request()
        first = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(first['launch_admitted'])
        with patch.object(evidence, '_git', side_effect=AssertionError('replay invoked Git')):
            with patch('runtime.adapters.codex.discover_native_workers', side_effect=AssertionError('replay invoked native discovery')):
                replay = self.api.prepare_assignment(self.scope, request)
        self.assertEqual(first['attempt_id'], replay['attempt_id'])
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])

    def test_git_metadata_observation_is_outside_database_transactions(self):
        from runtime import evidence
        original = evidence._git
        observed = []
        def read(root, *arguments):
            if '--git-dir' in arguments:
                with state._database(self.scope, write=True):
                    pass
                observed.append(root)
            return original(root, *arguments)
        with patch.object(evidence, '_git', side_effect=read):
            self.plan()
            self.assertTrue(self.prepare()['launch_admitted'])
        self.assertEqual(len(observed), 2)

    def test_descendant_claim_cannot_cover_a_named_parent_file(self):
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim('new.py/child')]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=value['jobs'][0]['claims'], files=['new.py'])
        with self.assertRaisesRegex(state.StateError, 'unreserved file'):
            self.api.prepare_assignment(self.scope, request)

    def test_status_and_admission_both_detect_retargeted_held_path(self):
        self.plan()
        first = self.prepare()
        (self.repo / 'other.py').write_text('other = 1\n')
        other = self.second_owner(1, [self.path_claim('other.py')])
        (self.repo / 'app.py').rename(self.repo / 'saved.py')
        (self.repo / 'app.py').symlink_to(self.repo / 'other.py')
        report = self.api.delegation_status(self.scope, 'codex', other['session_id'])
        self.assertEqual([c['attempt_id'] for c in report['conflicts']], [first['attempt_id']])
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)
        (self.repo / 'app.py').unlink()
        (self.repo / 'app.py').symlink_to('app.py')
        with self.assertRaises(state.StateError):
            self.api.delegation_status(self.scope, 'codex', other['session_id'])

    def test_directional_file_coverage_preserves_exact_hardlink_and_symlink_aliases(self):
        os.link(self.repo / 'app.py', self.repo / 'hard.py')
        (self.repo / 'sym.py').symlink_to(self.repo / 'app.py')
        (self.repo / '.git/info/exclude').write_text('sym.py\n')
        self.plan()
        request = self.prepare_request()
        request['brief']['files'] = ['hard.py', 'sym.py']
        self.assertTrue(self.api.prepare_assignment(self.scope, request)['launch_admitted'])

    def test_directory_file_coverage_rejects_escaping_symlink(self):
        (self.repo / 'work').mkdir()
        outside = self.repo / 'outside.py'
        outside.write_text('outside = 1\n')
        (self.repo / 'work/link.py').symlink_to(outside)
        (self.repo / '.git/info/exclude').write_text('work/link.py\n')
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim('work')]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=value['jobs'][0]['claims'], files=['work/link.py'])
        with self.assertRaisesRegex(state.StateError, 'unreserved file'):
            self.api.prepare_assignment(self.scope, request)

    def test_mutating_caller_input_cannot_change_the_validated_immutable_plan_or_brief(self):
        from runtime import evidence
        value = self.plan_value()
        original_artifact = evidence._artifact
        def publish(scope, content, **kwargs):
            value['limits']['max_attempts'] = 9
            return original_artifact(scope, content, **kwargs)
        with patch.object(evidence, '_artifact', side_effect=publish):
            planned = self.plan(value)
        self.assertEqual(evidence._read(self.scope, planned['plan'])['limits']['max_attempts'], 8)
        request = self.prepare_request()
        frozen_request = deepcopy(request)
        original_discovery = self.discover.side_effect
        def discover(scope, controls):
            request['brief']['files'] = ['../unreserved.py']
            return original_discovery(scope, controls)
        self.discover.side_effect = discover
        result = self.api.prepare_assignment(self.scope, request)
        body = evidence._read(self.scope, self.raw_assignment(result['attempt_id'])['assignment'])
        self.assertEqual(body['request']['brief']['files'], ['app.py'])
        self.assertFalse(self.api.prepare_assignment(self.scope, frozen_request)['launch_admitted'])

    def test_unsupported_explicit_selection_requires_documented_suitable_inheritance(self):
        self.catalog['dispatch_controls'].update(explicit_model=False, explicit_effort=False)
        self.catalog['parent_model_provider'] = None
        self.plan()
        request = self.prepare_request()
        with self.assertRaisesRegex(state.StateError, 'unsupported by exposed controls'):
            self.api.prepare_assignment(self.scope, request)
        request['selection']['mode'] = 'inherit'
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)
        request['selection']['inheritance'] = dict(model='new-native-model', effort='high',
            reference='coordinator:verified-current-native-inheritance')
        result = self.api.prepare_assignment(self.scope, request)
        binding = self.api.prepared_native_assignment(self.scope, result['attempt_id'])
        self.assertIsNone(binding['requested_model'])
        self.assertIsNone(binding['requested_effort'])
        from runtime import evidence
        assignment = evidence._read(self.scope, self.raw_assignment(result['attempt_id'])['assignment'])
        self.assertIsNone(evidence._read(self.scope, assignment['discovery'])['parent_model_provider'])

    def test_shared_port_keys_normalize_protocol_case_and_numeric_aliases(self):
        first_claim = dict(kind='port', host='local', key='TCP:0049', mode='write')
        value = self.plan_value()
        value['jobs'][0]['claims'] = [first_claim]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(files=[], resources=[first_claim])
        self.api.prepare_assignment(self.scope, request)
        other = self.second_owner(1, [dict(kind='port', host='local', key='tcp:49', mode='write')])
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)

    def test_status_reports_conflicting_owners_across_sessions(self):
        self.plan()
        first = self.prepare()
        other = self.second_owner(1)
        report = self.api.delegation_status(self.scope, 'codex', other['session_id'])
        self.assertIn('conflicts', report)
        self.assertEqual(report['conflicts'][0]['attempt_id'], first['attempt_id'])
        self.assertEqual(report['conflicts'][0]['owner']['session_id'], 'parent')

    def test_uncertain_launch_retains_ownership_and_never_reissues_permission(self):
        self.plan()
        request = self.prepare_request()
        first = self.api.prepare_assignment(self.scope, request)
        with state._database(self.scope, write=True) as db:
            data = self.raw_assignment(first['attempt_id'])
            data['status'] = 'uncertain'
            db.execute('update assignments set data=? where id=?', (json.dumps(data), first['attempt_id']))
        self.discover.reset_mock()
        replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['status'], 'uncertain')
        self.assertEqual(self.discover.call_count, 0)
        other = self.second_owner(1, [self.path_claim('independent.py')], dict(max_parallel_workers=1, host_capacity=1))
        self.assertTrue(self.api.prepare_assignment(self.scope, other)['launch_admitted'])  # F5.A1: no count place
        conflict = self.second_owner(2, [self.path_claim()])  # the uncertain worker still owns its file
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, conflict)
        self.assertEqual(self.raw_assignment(first['attempt_id'])['status'], 'uncertain')

    def test_retry_needs_new_evidence_and_preserves_per_job_budget(self):
        self.plan()
        first = self.prepare()
        self.settle_fixture(first['attempt_id'])
        retry = self.prepare_request(dispatch_id='retry-1', retry_of=first['attempt_id'], diagnostic=dict(
            prior_attempt_id=first['attempt_id'], cause='Diagnosed failure', evidence=['host:first-new-evidence'],
            change='Correct bounded input'))
        second = self.api.prepare_assignment(self.scope, retry)
        self.settle_fixture(second['attempt_id'])
        retry.update(dispatch_id='retry-2', retry_of=second['attempt_id'], expected_revision=self.request()['expected_revision'])
        retry['diagnostic']['prior_attempt_id'] = second['attempt_id']
        with self.assertRaisesRegex(state.StateError, 'new diagnostic evidence'):
            self.api.prepare_assignment(self.scope, retry)
        retry['diagnostic']['evidence'] = ['host:second-new-evidence']
        third = self.api.prepare_assignment(self.scope, retry)
        self.settle_fixture(third['attempt_id'])
        retry.update(dispatch_id='retry-3', retry_of=third['attempt_id'], expected_revision=self.request()['expected_revision'])
        retry['diagnostic'].update(prior_attempt_id=third['attempt_id'], evidence=['host:third-new-evidence'])
        with self.assertRaisesRegex(state.StateError, 'job attempt budget'):
            self.api.prepare_assignment(self.scope, retry)

    def test_unknown_old_check_resources_in_another_checkout_block_dispatch(self):
        from runtime import evidence
        self.plan()
        other_scope = self.peer_scope
        linked = other_scope.repository_root
        other = self.second_owner(1, [dict(kind='path', key=str(linked / 'app.py'), mode='read')], scope=other_scope)
        identity = {key: other[key] for key in ('client', 'session_id', 'task_id', 'scope_ref', 'expected_revision')}
        self.legacy_unlinked_review_fixture(other_scope, identity)
        with self.assertRaisesRegex(state.StateError, 'Unreconciled D5 attempt'):
            self.prepare()

    def test_worker_cannot_drop_an_applicable_accepted_check(self):
        from runtime import evidence
        proof = evidence._load_plan(self.scope, self.identity)
        proof['checks'].append(deepcopy(proof['checks'][0]) | dict(id='unrelated', dependencies=['unused.py']))
        evidence.plan_evidence(self.scope, self.request(plan=proof, change_decision_ref='user:checks-refined'))
        self.plan()
        request = self.prepare_request()
        request['brief']['checks'] = ['unrelated']
        with self.assertRaises(state.StateError):
            self.api.prepare_assignment(self.scope, request)

    def test_lost_response_cannot_mint_another_launch_permission(self):
        self.plan()
        request = self.prepare_request()
        with patch.object(self.api, '_reply', side_effect=RuntimeError('response lost after committed reservation')):
            with self.assertRaises(RuntimeError):
                self.api.prepare_assignment(self.scope, request)
        self.discover.reset_mock()
        replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.discover.call_count, 0)
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_failure_before_native_commit_rolls_back_without_spending_launch(self):
        self.plan()
        request = self.prepare_request()
        with state._database(self.scope) as db:
            before = deepcopy(state._task(db, state._scope_id(self.scope), 'codex', 'parent'))
        with patch.object(state, '_save', side_effect=state.StateError('fixture failure before commit')), \
             patch.object(self.api, '_reply', side_effect=AssertionError('response before durable reservation')):
            with self.assertRaisesRegex(state.StateError, 'fixture failure before commit'):
                self.api.prepare_assignment(self.scope, request)
        with state._database(self.scope) as db:
            self.assertEqual(self.api._ledger(self.scope, db), [])
            self.assertEqual(state._task(db, state._scope_id(self.scope), 'codex', 'parent'), before)
        admitted = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(admitted['launch_admitted'])
        self.assertFalse(admitted['replayed'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['budgets']['attempts_consumed'], 1)

    def test_shared_reads_coexist_and_write_claims_conflict(self):
        value = self.plan_value()
        value['jobs'][0]['claims'] = [self.path_claim(mode='read')]
        self.plan(value)
        request = self.prepare_request()
        request['brief']['resources'] = value['jobs'][0]['claims']
        self.api.prepare_assignment(self.scope, request)
        read = self.second_owner(1, [self.path_claim(mode='read')])
        self.assertTrue(self.api.prepare_assignment(self.scope, read)['launch_admitted'])
        write = self.second_owner(2)
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, write)

    def test_a_race_across_sessions_admits_one_owner_per_file_and_no_count_limit(self):
        """F5.A1 (was: a capacity race admits only one): physical ownership, not a declared count, serializes."""
        def race(names, offset):
            requests = [self.second_owner(offset + n, [self.path_claim(name)], dict(max_parallel_workers=1, host_capacity=1))
                        for n, name in enumerate(names)]
            barrier = threading.Barrier(2)
            def run(request):
                barrier.wait(timeout=5)
                try:
                    return self.api.prepare_assignment(self.scope, request)['launch_admitted']
                except state.StateError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as executor:
                return sum(executor.map(run, requests))
        self.assertEqual(race(['shared.py', 'shared.py'], 0), 1)
        self.assertEqual(race(['file-a.py', 'file-b.py'], 2), 2)

    def test_unknown_fields_brief_scope_and_coordinator_resources_are_rejected(self):
        value = self.plan_value()
        git_job = deepcopy(value)
        git_job['jobs'][0]['claims'] = [dict(kind='git', key=str(self.repo / '.git'), mode='write')]
        with self.assertRaises(state.StateError):
            self.plan(git_job)
        self.plan(value)
        request = self.prepare_request()
        for delta in (dict(secret='forbidden'), dict(brief=request['brief'] | dict(resources=[])),
                      dict(brief=request['brief'] | dict(checks=[])), dict(brief=request['brief'] | dict(files=['../outside']))):
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(self.scope, request | delta)

    def test_v1_receipt_observation_keeps_writer_ownership_until_independent_stop_proof(self):
        self.plan()
        prepared = self.prepare()
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        observed = self.lifecycle_observation(prepared, mechanism='codex-native-collab-call',
            agent_path='/actual/' + binding['task_name'])
        self.assertEqual(self.observe_worker(prepared, observed)['status'], 'observed')
        self.assertTrue(self.observe_worker(prepared, observed)['replayed'])
        # Keep the same valid path to isolate a mechanism-only identity conflict.
        route = deepcopy(observed); route['receipt']['mechanism'] = 'codex-native-agent-path'
        self.assertEqual(self.observe_worker(prepared, route)['status'], 'uncertain')
        first_conflict = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])['conflict']
        child = deepcopy(observed); child['receipt']['worker_id'] = 'different-child'
        self.assertEqual(self.observe_worker(prepared, child)['status'], 'uncertain')
        latest = evidence._read(self.scope, self.raw_assignment(prepared['attempt_id'])['observation'])
        self.assertNotEqual(first_conflict, latest['conflict'])
        self.assertEqual(latest['receipt']['worker_id'], 'child')
        for flag in (None, 'stopped', 'descendants_stopped'):
            request = self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[],
                proof_delta={flag: False} if flag else None)
            if flag is None:
                request.pop('host_proof')
            with self.subTest(flag=flag), self.assertRaises(state.StateError):
                self.api.reconcile_assignment(self.scope, request)
            self.assertNotEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')
            with self.assertRaises(state.StateError):
                self.api.reserve_coordinator(self.scope, self.operation_request(claims=[self.path_claim()]))
        # Fixture host proof models independently inspected stopping, not native qualification.
        exact = self.worker_reconciliation_request(prepared, outcome='failed', changed_paths=[])
        self.assertEqual(self.api.reconcile_assignment(self.scope, exact)['status'], 'settled')
        self.assertTrue(self.api.reconcile_assignment(self.scope, exact)['replayed'])
        self.assertEqual(self.api.delegation_status(self.scope, 'codex', 'parent')['capacity']['unresolved_managed_workers'], 0)

    def test_native_receipt_keeps_admitted_version_when_observer_updates(self):
        self.plan()
        prepared = self.prepare()
        self.observe_worker(prepared)
        observed = self.lifecycle_observation(prepared, current_observer_version='0.165.0',
            parent_creation_version='0.164.0-alpha.11.1', worker_creation_version='0.164.0-alpha.11.1')
        self.assertEqual(self.observe_worker(prepared, observed)['status'], 'observed')
        with state._database(self.scope) as db:
            record = next(row for row in self.api._ledger(self.scope, db) if row['id'] == prepared['attempt_id'])
        receipt = self.api._worker_history(self.scope, record['data']['observation'])[0][1]['receipt']
        self.assertEqual(receipt['native_version'], '0.163.0')
        self.assertEqual(receipt['current_observer_version'], '0.165.0')
        self.assertEqual(record['data']['binding']['native_version'], '0.163.0')
        request = self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        for key in ('current_observer_version', 'parent_creation_version', 'worker_creation_version'):
            bad = dict(observed, receipt=observed['receipt'] | {key: None})
            with self.subTest(key=key), self.assertRaises(state.StateError):
                self.api._worker_receipt(self.scope, record, request, bad)

    def test_admission_accepts_compatible_reader_update_and_records_actual_provenance(self):
        self.plan()
        request = self.prepare_request()
        request['control_observation']['native_version'] = '0.100.0'
        request['discovery_expectation']['native_version'] = '0.120.0'
        self.catalog['native_version'] = '9.999.0'
        prepared = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(prepared['launch_admitted'])
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        self.assertEqual(binding['native_version'], '9.999.0')
        data = self.raw_assignment(prepared['attempt_id'])
        assignment = evidence._read(self.scope, data['assignment'])
        self.assertEqual(evidence._read(self.scope, assignment['discovery'])['native_version'], '9.999.0')
        self.assertEqual(assignment['request']['control_observation']['native_version'], '0.100.0')
        self.assertEqual(assignment['request']['discovery_expectation']['native_version'], '0.120.0')


if __name__ == '__main__':
    unittest.main()
