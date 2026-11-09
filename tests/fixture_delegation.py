"""Shared native delegation admission fixture; no network, models or live client changes."""
from tests.fixture_canonical_birth import fixture_artifact
from copy import deepcopy
import json
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from runtime import changes, delegation, evidence, state


class AdmissionFixture:
    """Shared native-admission setUp and helpers; mix into a unittest.TestCase subclass."""

    def setUp(self):
        from tests.test_evidence import EvidenceTests
        from runtime import delegation
        self.api = delegation
        self.assertTrue(hasattr(delegation, 'plan_delegation'), 'assignment admission implementation missing')
        self.fixture = EvidenceTests(methodName='runTest')
        self.fixture._canonical_peer = getattr(self,'_canonical_peer',False) or self._testMethodName in {
            'test_cross_scope_claims_and_explicit_shared_services_conflict',
            'test_unknown_old_check_resources_in_another_checkout_block_dispatch'}
        self.fixture._canonical_linked = self._testMethodName in {
            'test_linked_checkout_git_marker_is_coordinator_owned',
            'test_actual_private_worktree_git_metadata_is_coordinator_owned',
            'test_git_marker_retarget_during_discovery_blocks_commit',
            'test_git_private_alias_retarget_with_unchanged_marker_blocks_commit',
            'test_git_commondir_byte_change_during_discovery_blocks_commit',
            'test_git_common_alias_retarget_after_candidate_capture_blocks_commit'}
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope
        if self.fixture._canonical_peer:self.peer_scope=self.fixture.peer_scope
        self.identity = dict(self.fixture.identity)
        state.start_task(self.scope, self.identity | dict(mode='editable', decision_ref='user:accepted'))
        self.fixture.plan()
        self.root, self.repo = self.fixture.root, self.fixture.repo
        from runtime import changes
        guard_patch = patch.object(changes, 'LOCK_ROOT', self.root / 'authority-guards')
        guard_patch.start()
        self.addCleanup(guard_patch.stop)
        self.catalog = dict(version=1, client_surface='codex-cli', native_version='0.163.0',
            parent_session_id='parent', originator='fixture-cli', parent_model_provider='native-provider',
            catalog=[dict(model='new-native-model', supported_efforts=['low', 'high'], default_effort='low')],
            dispatch_controls=dict(spawn=True, explicit_model=True, explicit_effort=True, fork_turns_none=True),
            capability_digest='a' * 64, limitations=['fixture native catalog, no live execution'])
        self.cache_request = dict(client_surface='codex-cli', applicability=dict(client_version='0.163.0',
            capability_fingerprint='a' * 64))
        claim = self.api.claim_guidance(self.scope, self.cache_request | dict(trigger=dict(kind='routine', id='fixture')))
        guidance = dict(schema_version=1, client_surface='codex-cli', checked_at='2026-01-01T00:00:00Z',
            applicability=self.cache_request['applicability'], sources=[dict(id='official',
                url='https://example.org/official', sha256='b' * 64)], recommendations=[dict(id='bounded',
                work='Bounded code work', risk='Local concurrency-sensitive work', models=[],
                conditions=['Coordinator verifies capability'], limitations=['Advice only'], source_ids=['official'])])
        self.api.record_guidance(self.scope, self.cache_request | dict(token=claim['token'],
            generation=claim['generation'], coordinator_ref='fixture:verified', guidance=guidance))
        def discover(scope, controls):
            return deepcopy(self.catalog) | dict(parent_session_id=controls['parent_session_id'])
        self.discovery = patch('runtime.adapters.codex.discover_native_workers', side_effect=discover)
        self.discover = self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def request(self, **fields):
        return self.identity | dict(expected_revision=state.task_status(self.scope,
            'codex', self.identity['session_id'])['task']['revision']) | fields

    def path_claim(self, name='app.py', mode='write'):
        return dict(kind='path', key=str(self.repo / name), mode=mode)

    def plan_value(self, **updates):
        return dict(version=1, route=dict(mechanism='codex-native-subagent', client_surface='codex-cli',
            parent_session_id=self.identity['session_id'], authority_ref='coordinator:verified-existing-native-route',
            controls_ref='coordinator:exposed-tools-checked'), authority=dict(state_dir=str(self.scope.runtime.state_dir),
            participants_ref='coordinator:participating-owners', ownership_known=True),
            selection_constraints=dict(complexity='Coupled code', risk='Concurrency and security',
                rationale='Native worker isolates bounded context', required_controls=['spawn']),
            limits=dict(max_attempts=8, max_resource_units=8, deadline=int(self.api._now()) + 600,
                max_parallel_workers=4, host_capacity=4, capacity_ref='coordinator:observed-slots'),
            fallback=dict(action='stop', reference='user:original-budget'), jobs=[dict(id='edit', role='worker',
                claims=[self.path_claim()], dependencies=[], max_attempts=3)]) | updates

    def plan(self, value=None):
        return self.api.plan_delegation(self.scope, self.request(plan=value or self.plan_value()))

    def prepare_request(self, **updates):
        status = self.api.guidance_status(self.scope, self.cache_request)
        return self.request(dispatch_id='request-1', job_id='edit', resource_units=1,
            brief=dict(objective='Implement bounded edit', context='Inspect existing callers first',
                instructions=['Test first; preserve validation'], files=['app.py'], resources=[self.path_claim()],
                dependencies=[], acceptance=['Required behavior verified'], checks=['unit'],
                self_review='Inspect diff and callers', stop_conditions=['Stop for review after this assignment']),
            selection=dict(model='new-native-model', effort='high', rationale='Capable native model for exact scope',
                profile_ids=['bounded'], mode='explicit'),
            control_observation=dict(version=1, client_surface='codex-cli', native_version=self.catalog['native_version'],
                parent_session_id=self.identity['session_id'], dispatch_controls=deepcopy(self.catalog['dispatch_controls'])),
            discovery_expectation={key: self.catalog[key] for key in
                ('client_surface', 'native_version', 'capability_digest', 'parent_model_provider')} |
                dict(parent_session_id=self.identity['session_id']),
            guidance={key: status[key] for key in ('snapshot_ref', 'snapshot_digest', 'checked_at', 'freshness')}) | updates

    def prepare(self, **updates):
        return self.api.prepare_assignment(self.scope, self.prepare_request(**updates))

    def raw_assignment(self, attempt_id):
        with state._database(self.scope) as db:
            return json.loads(db.execute('select data from assignments where id=?', (attempt_id,)).fetchone()[0])

    def settle_fixture(self, attempt_id, status='failed'):
        # Synthetic future reconciliation boundary; never substitutes for live host evidence.
        from runtime import evidence
        with state._database(self.scope, write=True) as db:
            data = self.raw_assignment(attempt_id)
            data.update(status='settled', result=dict(outcome=status), reconciliation=fixture_artifact(
                self.scope,self.identity,dict(fixture='independent stopped-writer boundary', attempt_id=attempt_id,
                    revision=self.request()['expected_revision']),db=db))
            db.execute('update assignments set data=? where id=?', (json.dumps(data), attempt_id))

    def second_owner(self, number, claims=None, limits=None, scope=None):
        from runtime import evidence
        identity = self.identity | dict(session_id='parent-' + str(number), task_id='task-' + str(number))
        proof_plan = evidence._load_plan(self.scope, self.identity)
        owner_scope = scope or self.scope
        state.start_task(owner_scope, identity | dict(mode='editable', decision_ref='user:accepted'))
        evidence.plan_evidence(owner_scope, identity | dict(expected_revision=1, plan=proof_plan))
        prior, prior_scope = self.identity, self.scope
        self.identity, self.scope = identity, owner_scope
        try:
            value = self.plan_value()
            if claims is not None:
                value['jobs'][0]['claims'] = claims
            if limits:
                value['limits'].update(limits)
            self.plan(value)
            request = self.prepare_request()
            request['brief']['resources'] = value['jobs'][0]['claims']
            request['brief']['files'] = [str(Path(c['key']).relative_to(owner_scope.repository_root))
                for c in value['jobs'][0]['claims'] if c['kind'] == 'path' and
                Path(c['key']).is_relative_to(owner_scope.repository_root)]
            return request
        finally:
            self.identity, self.scope = prior, prior_scope

    def legacy_unlinked_review_fixture(self, scope, identity):
        # Deliberate retained historical evidence; never a current native launch path.
        plan = evidence._load_plan(scope, identity)
        candidate = evidence.capture_candidate(scope, plan)
        identity_id = __import__('uuid').uuid4().hex
        attempt = dict(id=identity_id, kind='review', status='reserved', candidate=fixture_artifact(scope,identity, candidate),
            candidate_digest=evidence._digest(candidate), plan_digest=evidence._digest(plan), task_name='review_'+identity_id,
            assignment=fixture_artifact(scope,identity, dict(prompt='Legacy unresolved reviewer')), assignment_digest='a'*64,
            coverage=['general'], criteria=['works'])
        with state._database(scope, write=True) as db:
            task = state._task(db, state._scope_id(scope), identity['client'], identity['session_id'])
            task['evidence']['attempts'].append(attempt)
            task['revision'] += 1
            state._save(db, state._scope_id(scope), task)

    def assert_directory_alias_claims_are_exclusive(self, kind, directory_first):
        work = self.repo / 'work'
        work.mkdir()
        (work / 'inside.py').write_text('inside = 1\n')
        outside = self.repo / 'outside.py'
        if kind == 'hardlink':
            os.link(work / 'inside.py', outside)
        else:
            outside.write_text('outside = 1\n')
            (work / 'link.py').symlink_to(outside)
            (self.repo / '.git/info/exclude').write_text('work/link.py\n')
        directory, alias = self.path_claim('work'), self.path_claim('outside.py')
        first, second = (directory, alias) if directory_first else (alias, directory)
        value = self.plan_value()
        value['jobs'][0]['claims'] = [first]
        self.plan(value)
        request = self.prepare_request()
        request['brief'].update(resources=[first], files=['work/inside.py'] if directory_first else ['outside.py'])
        self.assertTrue(self.api.prepare_assignment(self.scope, request)['launch_admitted'])
        other = self.second_owner(1, [second])
        other['brief']['files'] = ['outside.py'] if directory_first else ['work/inside.py']
        with self.assertRaisesRegex(state.StateError, 'Canonical resource ownership conflicts'):
            self.api.prepare_assignment(self.scope, other)

    def operation_request(self, identity=None, **fields):
        identity = identity or self.identity
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), identity['client'], identity['session_id'], identity['task_id'])
        return identity | dict(expected_revision=task['revision'], dispatch_id='operation-1',
            claims=[self.path_claim('coordinator.txt')], operation=dict(id='operation-1', kind='coordinator-work'),
            reason='Bounded model-free operation', ownership_ref='host:authorized-resource-owner',
            stop_conditions=['Stop after this operation']) | fields

    def lifecycle_observation(self, prepared, *, turn_status='completed', **changes):
        from runtime import evidence
        binding = self.api.prepared_native_assignment(self.scope, prepared['attempt_id'])
        report = dict(version=1, attempt_id=prepared['attempt_id'], assignment_digest=binding['assignment_digest'],
            candidate_digest=binding['candidate_digest'], status='completed', changed_paths=['app.py'],
            evidence=['fixture:worker-report'], limitations=['Independently verify all claims'])
        result = json.dumps(report) if turn_status == 'completed' else None
        receipt = dict(version=2, mechanism='codex-native-agent-path', attempt_id=prepared['attempt_id'],
            task_name=binding['task_name'], agent_path='/root/' + binding['task_name'], native_version=binding['native_version'],
            client_surface=binding['client_surface'], originator='codex_exec', parent_model_provider=None,
            worker_model_provider=None, parent_session_id=binding['parent_session_id'], parent_turn_id='parent-turn',
            spawn_item_id='spawn-item', worker_id='child', worker_turn_id='child-turn', turn_status=turn_status,
            result_item_id='final-item' if result else None, checkout=str(self.repo),
            assignment_digest=binding['assignment_digest'], candidate_digest=binding['candidate_digest'],
            requested_model=binding['requested_model'], requested_effort=binding['requested_effort'],
            configured_model='new-native-model', configured_effort='high', spawn_observed_model='new-native-model',
            spawn_observed_effort='high', execution_model=None, execution_effort=None, descendants_stopped=False,
            prompt_delivery='unverified', result_digest=__import__('hashlib').sha256(result.encode()).hexdigest() if result else None)
        return dict(receipt=receipt | changes, result=result)

    def observe_worker(self, prepared, observed=None, request=None):
        request = request or self.request(attempt_id=prepared['attempt_id'], parent_turn_id='parent-turn', spawn_item_id='spawn-item')
        with patch('runtime.adapters.codex.observe_native_worker', return_value=observed or self.lifecycle_observation(prepared)):
            return self.api.observe_assignment(self.scope, request)

    def worker_reconciliation_request(self, prepared, *, outcome='completed', proof_delta=None, **assessment_delta):
        import hashlib
        from runtime import evidence
        with state._database(self.scope) as db:
            task = state._task(db, state._scope_id(self.scope), 'codex', 'parent', 'task')
            record = next(r for r in self.api._ledger(self.scope, db) if r['id'] == prepared['attempt_id'])
        data, binding = record['data'], record['data']['binding']
        observed = evidence._read(self.scope, data['observation']) if data['observation'] else None
        receipt = observed['receipt'] if observed and observed['receipt'] else {}
        native = {key: binding[key] for key in ('client_surface', 'native_version', 'parent_session_id', 'task_name')}
        native.update({key: receipt.get(key) for key in ('parent_turn_id', 'spawn_item_id', 'worker_id', 'worker_turn_id', 'result_item_id')})
        proof = dict(version=2, kind='assignment-writer-reconciliation', identity=self.identity,
            expected_pins=task['pins'], expected_revision=task['revision'], scope_id=state._scope_id(self.scope),
            attempt_id=prepared['attempt_id'], dispatch_id=record['owner']['dispatch_id'],
            assignment_digest=data['assignment']['digest'], candidate_digest=data['candidate']['digest'],
            current_candidate_digest=evidence._digest(evidence.capture_candidate(self.scope, evidence._read(self.scope, task['evidence']['plan']))),
            d6_plan_digest=data['plan_digest'], claims_digest=evidence._digest(data['claims']),
            observation_digest=data['observation']['digest'] if data['observation'] else None,
            result_digest=receipt.get('result_digest'), native_identity=native,
            launch_state='observed' if data['observation'] else 'not-started', stopped=True, descendants_stopped=True,
            observation_ref='host:exact-parent-marker-and-process-tree', coordinator_ref='coordinator:independently-verified')
        proof.update(proof_delta or {})
        path = self.root / ('worker-proof-' + __import__('uuid').uuid4().hex + '.json')
        raw = json.dumps(proof).encode(); path.write_bytes(raw); path.chmod(0o600)
        assessment = dict(outcome=outcome, changed_paths=['app.py'] if outcome == 'completed' else [],
            result_assessment='Independent inspection of worker result', partial_change_assessment='Inspected repository and shared-resource effects',
            evidence=['fixture:independent-inspection'], coordinator_ref='coordinator:assessed') | assessment_delta
        return self.identity | dict(expected_revision=task['revision'], attempt_id=prepared['attempt_id'],
            host_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()), assessment=assessment)

    def cooperative_worker(self, prepared, *, version=3, activity=None):
        from runtime import evidence
        if activity is None:
            activity = dict(source_digest='a' * 64, calls=[dict(id='patch-1', tool='apply_patch', input_digest='b' * 64)])
            (self.repo / 'app.py').write_text('changed by fixture worker\n')
        observed = self.lifecycle_observation(prepared) | dict(activity=activity)
        self.observe_worker(prepared, observed, self.request(attempt_id=prepared['attempt_id'],
            parent_turn_id='parent-turn', spawn_item_id='spawn-item', cooperative=True))
        request = self.worker_reconciliation_request(prepared)
        path = Path(request['host_proof']['path'])
        proof = json.loads(path.read_bytes())
        for key in ('stopped', 'descendants_stopped'):
            del proof[key]
        proof.update(version=version, kind='native-cooperative-completion', activity_digest=evidence._digest(activity),
                     coordinator_ref=request['assessment']['coordinator_ref'],
                     observation_ref=request['assessment']['evidence'][0])
        if version == 2:
            proof['no_pending_continuation'] = True
        else:
            proof.update(no_outstanding_coordinator_followups=True, coordinator_submissions_paused=True)
        raw = evidence._raw(proof)
        path.write_bytes(raw)
        request['host_proof']['sha256'] = hashlib.sha256(raw).hexdigest()
        return request, observed

    def retained_d4(self, *, command=False, existing=False):
        """Actual retained D4 operation over original-schema data, never a canonical store loan."""
        from tests.test_changes import ChangesTests
        d4=ChangesTests('runTest');d4.setUp();self.addCleanup(d4.doCleanups)
        if existing:
            old,_,request=d4.existing_enrollment(runtime=True)
            d4.historical_runtime(old,'empty')
        else:
            request=d4.command_request("from pathlib import Path; Path('result.txt').write_text('result')") if command else d4.request()
        directory=d4.base/'retained-shared-state'
        record=request['manifest']['enrollment']['record']
        record['runtime']=dict(schema_version=2,state_dir=str(directory))
        d4.historical_runtime(record,'empty')
        request=d4.observe(d4.bind(request['manifest']))
        return d4,request,directory

    def assert_retained_guard_blocked(self, directory):
        from runtime import changes
        from runtime.startup import StartupError
        with self.assertRaises(StartupError):
            with changes._authority_guards([directory]):self.fail('Retained D4 guard unexpectedly released')

    def assert_git_routing_change_is_rejected_at_commit(self, change):
        from runtime import evidence
        linked = self.repo
        private = Path(subprocess.check_output(['git', '-C', str(linked), 'rev-parse',
            '--path-format=absolute', '--git-dir'], text=True).strip())
        spare = self.repo / 'spare-private'
        shutil.copytree(private, spare)
        (spare / 'commondir').write_text(str(self.scope.git_common_dir) + '\n')
        scope = self.scope
        target = spare / 'HEAD'
        alias = self.root / 'routing-alias'
        if change == 'private-alias':
            alias.symlink_to(private, target_is_directory=True)
            (linked / '.git').write_text('gitdir: ' + str(alias) + '\n')
        elif change == 'common-alias':
            alias.symlink_to(self.scope.git_common_dir, target_is_directory=True)
            (private / 'commondir').write_text(str(alias) + '\n')
            spare_common = self.repo / 'spare-common'
            shutil.copytree(self.scope.git_common_dir, spare_common)
            target = spare_common / 'config'
        request = self.second_owner(1, [dict(kind='path', key=str(target), mode='write')], scope=scope)
        mutated = []
        def mutate():
            mutated.append(change)
            if change == 'marker':
                (linked / '.git').write_text('gitdir: ' + str(spare) + '\n')
            elif change == 'private-alias':
                alias.unlink()
                alias.symlink_to(spare, target_is_directory=True)
            elif change == 'commondir-bytes':
                (private / 'commondir').write_text(str(self.scope.git_common_dir) + '\n')
            else:
                alias.unlink()
                alias.symlink_to(spare_common, target_is_directory=True)
        if change == 'common-alias':
            original = evidence._artifact
            changed = False
            def publish(artifact_scope, value, **kwargs):
                nonlocal changed
                if artifact_scope == scope and not changed:
                    changed = True
                    mutate()
                return original(artifact_scope, value, **kwargs)
            interception = patch.object(evidence, '_artifact', side_effect=publish)
        else:
            original = self.discover.side_effect
            def discover(owner, controls):
                mutate()
                return original(owner, controls)
            interception = patch.object(self.discover, 'side_effect', discover)
        with interception:
            with self.assertRaises(state.StateError):
                self.api.prepare_assignment(scope, request)
        self.assertTrue(mutated, 'The intended late Git routing mutation was not reached')
        with state._database(scope) as db:
            self.assertEqual(db.execute('select count(*) from assignments').fetchone()[0], 0)
            task = state._task(db, state._scope_id(scope), request['client'], request['session_id'], request['task_id'])
            self.assertEqual(task['revision'], request['expected_revision'])
            self.assertEqual(task['delegation']['attempts_consumed'], 0)
            self.assertEqual(task['delegation']['resource_units_consumed'], 0)
