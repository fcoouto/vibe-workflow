"""Internal fixture proofs for owned suspension and unrelated durable heads."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import changes, delegation, evidence, onboarding, state
from runtime.startup import PROJECT_FILE
from tests import test_canonical_rollback


class D4OwnedTransitionTests(unittest.TestCase):
    def setUp(self):
        self.operation = test_canonical_rollback.CanonicalOwnedRollbackTests('runTest')
        self.addCleanup(self.operation.doCleanups)
        self.operation.prepare(active_prior=True)
        self.fixture = self.operation.fixture
        self.changes = changes
        self.scope = self.operation.acquire(self.fixture.repo,
            self.fixture.test_observations[str(self.fixture.repo)])

    def _host(self, facts, grants):
        class Host:
            def acquire_d4_bookkeeping(self, acquired, issue):
                facts.append(evidence._canonical_plain(acquired))
                grant = issue(acquired)
                grants.append(grant)
                return grant
        return self.changes._bootstrap_d4_host(Host(), issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER)

    def _fresh_request(self, label, *, runtime=None):
        record = json.loads(self.operation.authority.read_bytes())
        members = [Path(checkout['root']) for _, checkout, _ in changes._scoped_entries(record).values()]
        directories = {Path(checkout['root']): Path(entry['configuration']['runtime']['state_dir'])
            for _, checkout, entry in changes._scoped_entries(record).values()}
        request, _ = self.operation.host.successor(self.fixture, members, label, directories, shared=True)
        return request

    def guidance_request(self):
        cache = dict(client_surface='codex-desktop',
            applicability=dict(client_version='fixture', capability_fingerprint='a' * 64))
        claim = delegation.claim_guidance(self.scope, cache | dict(trigger=dict(kind='manual', id='current-source')))
        guidance = delegation.record_guidance(self.scope, cache | dict(token=claim['token'],
            generation=claim['generation'], coordinator_ref='fixture:verified-guidance',
            guidance=dict(schema_version=1, client_surface=cache['client_surface'], checked_at='2026-01-01T00:00:00Z',
                applicability=cache['applicability'], sources=[dict(id='official',
                    url='https://example.invalid/official', sha256='b' * 64)], recommendations=[])))['snapshot_ref']
        self._stopped_operation(self.scope, guidance=guidance)
        return (self.operation.request, self.scope.authority_path, guidance,
            evidence._evidence_context(self.scope), self.scope.project_id)

    def _stopped_operation(self, scope, *, guidance=None):
        owner = dict(client='codex', session_id='owned-source', task_id='owned-source', scope_ref='fixture:owned-source')
        state.start_task(scope, owner | dict(mode='editable', decision_ref='fixture:owned-decision'))
        prepared = delegation.reserve_coordinator(scope, owner | dict(expected_revision=1, dispatch_id='owned-source-operation',
            claims=[dict(kind='path', key=str(scope.repository_root / 'AGENTS.md'), mode='write')],
            operation=dict(id='owned-source-operation', kind='coordinator-work'), reason='Retained task-owned support',
            ownership_ref='fixture:owned-operation', stop_conditions=['Stop after observation']))
        with state._database(scope) as db:
            task = state._task(db, state._scope_id(scope), owner['client'], owner['session_id'], owner['task_id'])
            row = next(row for row in delegation._ledger(scope, db) if row['id'] == prepared['attempt_id'])
        facts = dict(operation_id='owned-source-operation', operation_digest=row['body']['operation']['digest'],
            claims_digest=evidence._digest(row['data']['claims']))
        reference = evidence._artifact(scope, dict(facts, observation='Actual operation stopped before the owned update',
            **(dict(guidance=guidance) if guidance is not None else {})),
            owner=evidence._artifact_owner(scope, task))
        proof = dict(version=2, kind='coordinator-operation-reconciliation', identity=owner, expected_pins=task['pins'],
            expected_revision=task['revision'], scope_id=row['owner']['scope_id'], attempt_id=row['id'],
            dispatch_id=row['owner']['dispatch_id'], assignment_digest=row['data']['assignment']['digest'],
            decision_ref=task['decision_ref'], operation_evidence=reference, resource_observations=reference,
            operation_stopped=True, operation_descendants_stopped=True, observation_ref='fixture:owned-stop',
            coordinator_ref='fixture:independent-owned-observer', **facts)
        path = self.fixture.directory / 'owned-operation-stop.json'
        raw = json.dumps(proof).encode(); path.write_bytes(raw); path.chmod(0o600)
        delegation.reconcile_assignment(scope, owner | dict(expected_revision=task['revision'], attempt_id=row['id'],
            host_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()), assessment=dict(outcome='cancelled',
                changed_paths=[], result_assessment='Stopped actual operation', partial_change_assessment='No remaining effects',
                evidence=['fixture:owned-inspection'], coordinator_ref='fixture:independent-owned-observer')))
        task = state.task_status(scope, owner['client'], owner['session_id'])['task']
        stop = dict(version=1, kind='stopped-writer-reconciliation', identity=owner, expected_revision=task['revision'],
            expected_pins=task['pins'], observed_turn_id=task['turn_id'], stopped=True, descendants_stopped=True,
            attempt_ids=[], observation_ref='fixture:owned-task-stop', coordinator_ref='fixture:independent-owned-observer')
        path = self.fixture.directory / 'owned-task-stop.json'
        raw = json.dumps(stop).encode(); path.write_bytes(raw); path.chmod(0o600)
        stopped = state.retire_task(scope, owner | dict(expected_revision=task['revision'], expected_pins=task['pins'],
            reason='Stopped before owned update', reference='fixture:owned-retirement',
            stop_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())))['task']
        self.assertEqual(stopped['state'], 'CANCELLED')
        with state._database(scope) as db:
            self.assertTrue(all(row['data']['status'] == 'settled' for row in delegation._ledger(scope, db)))
        return reference, stopped

    def _markers(self, context, project_root):
        canonical = Path(self.changes.LOCK_ROOT) / ('canonical-' + hashlib.sha256(
            ('destination:' + str(project_root / PROJECT_FILE)).encode()).hexdigest() + '.lock')
        return [*self.changes._authority_locations([context.directory]), canonical]

    def test_owned_suspended_postimage_reacquires_and_registers_existing_task_body(self):
        fixture = self.fixture
        scope = self.scope
        body, task = self._stopped_operation(scope)
        context = evidence._evidence_context(scope)
        with evidence._context_database(context) as db:
            original = evidence._managed_artifact(None, body, context=context, db=db)
        request = self.operation.request
        before = {a['id']: self.changes._fingerprint(Path(a['owner']['root']), a['effects'][0]['path'])
            for a in request['manifest']['actions']}
        self.assertTrue(self.changes._validate_scoped(request, fixture.plan())[3])
        facts, grants, interruptions = [], [], []
        real_publish = self.changes._scoped_publish

        def interrupt_after_owned_publish(path, journal, phase, record):
            real_publish(path, journal, phase, record)
            if phase == 'suspended':
                current = json.loads((scope.project_root / PROJECT_FILE).read_text())
                self.assertEqual(current, self.changes._scoped_suspended(journal))
                self.assertTrue(self.changes._scoped_owned_authority(journal))
                self.assertEqual(journal['enrollment']['postimages'][-1]['status'], 'published')
                interruptions.append(copy.deepcopy(current))
                raise RuntimeError('fixture interruption after durable owned suspension')

        with self._host(facts, grants) as host:
            with patch.object(self.changes, '_scoped_publish', side_effect=interrupt_after_owned_publish):
                with self.assertRaisesRegex(RuntimeError, 'durable owned suspension'):
                    self.changes._run_scoped(fixture.directory, request, 'apply', host=host)
        self.assertEqual(len(interruptions), 1)
        self.assertEqual(len(grants), 1)
        for action in request['manifest']['actions']:
            self.assertEqual(self.changes._fingerprint(Path(action['owner']['root']), action['effects'][0]['path']), before[action['id']])
        markers = self._markers(context, scope.project_root)
        self.assertTrue(all(self.changes._authority_owner(marker) is not None for marker in markers))
        with evidence._context_database(context) as db:
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            first_edge = next(edge for edge in baseline['dependencies'] if edge['reference']['identity'] == facts[0]['identity'])
            first_head = first_edge['reference']['artifact']
            original_age = state._metadata(db, first_head['name'])['created_at']
        real_register = evidence._register_recovery_source
        suspended_registrations = []

        def observe_registration(actual_scope, db, source, *, context=None):
            reference = real_register(actual_scope, db, source, context=context)
            current = json.loads((scope.project_root / PROJECT_FILE).read_text())
            if current == interruptions[0]:
                current_journal = self.changes._load_journal(fixture.directory)
                self.assertTrue(self.changes._scoped_owned_authority(current_journal))
                key = tuple(task['accepted_identity'][k] for k in ('repository_id', 'checkout_id', 'client'))
                self.assertTrue(self.changes._d4_owned_transition(current_journal, key))
                qualified = dict(authority=original[0]['owner']['task']['authority'], artifact=body)
                self.assertIn(qualified, evidence._canonical_plain(source.content)['artifacts'])
                self.assertTrue(any(item['reference'] == original[0]['owner']['task'] for item in source.content['tasks']))
                self.assertEqual(state._metadata(db, reference['name'])['created_at'], original_age)
                if context.directory == scope.runtime.state_dir:
                    suspended_registrations.append(evidence._canonical_plain(reference))
            return reference

        with self._host(facts, grants) as host:
            with patch.object(evidence, '_register_recovery_source', side_effect=observe_registration):
                resumed = self.changes._run_scoped(fixture.directory, request, 'resume', host=host)
        self.assertEqual(resumed['exit_code'], 0, resumed)
        self.assertEqual(resumed['state'], 'applied', resumed)
        self.assertEqual(len(suspended_registrations), 1)
        self.assertEqual(len(grants), 3)
        self.assertEqual(facts[-1]['journal']['state'], 'applied')
        self.assertEqual(len({id(grant.acquisition) for grant in grants}), len(grants))
        for grant in grants:
            with self.assertRaises(self.changes.StartupError): self.changes._check_d4_grant(grant)
        final_journal = self.changes._load_journal(fixture.directory)
        self.assertEqual(sum(row['phase'] == 'suspended' for row in final_journal['enrollment']['postimages']), 1)
        self.assertEqual(json.loads((scope.project_root / PROJECT_FILE).read_text()), request['manifest']['enrollment']['record'])
        with evidence._context_database(context) as db:
            self.assertEqual(evidence._managed_artifact(None, body, context=context, db=db), original)
            current_task = state._task(db, state._scope_id(scope), task['client'], task['session_id'], task['task_id'])
            self.assertEqual(current_task['pins'], task['pins'])
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            edge = next(row for row in baseline['dependencies'] if row['reference']['identity'] == facts[0]['identity'])
            final_body = evidence._read(None, edge['reference']['artifact'], context=context, db=db)
            self.assertEqual(final_body['journal']['state'], 'applied')
            self.assertIn(dict(authority=evidence._canonical_plain(context.authority_set[0]), artifact=body),
                evidence._canonical_plain(final_body)['artifacts'])
            history = evidence._recovery_source_history(context, db, edge['reference']['artifact'], facts[0]['identity'])
            self.assertIn(first_head, [dict(reference) for reference, _ in history])
            self.assertIn(suspended_registrations[0], [dict(reference) for reference, _ in history])
            self.assertTrue(all(state._metadata(db, reference['name'])['created_at'] == original_age for reference, _ in history))
        for action in request['manifest']['actions']:
            self.assertEqual(self.changes._fingerprint(Path(action['owner']['root']), action['effects'][0]['path']), action['effects'][0]['after'])
        self.assertTrue(all(self.changes._authority_owner(marker) is None for marker in markers))

    def test_distinct_operation_preserves_genuinely_released_unrelated_edge_and_age(self):
        fixture = self.fixture
        request, _, guidance, context, _ = self.guidance_request()
        facts, grants = [], []
        with self._host(facts, grants) as host:
            result = self.changes._run_scoped(fixture.directory, request, 'apply', host=host)
        self.assertEqual(result['state'], 'applied', result)
        self.assertTrue(all(row['id'] == 'qualification' for row in result['blocked']), result)
        identity = facts[0]['identity']
        with evidence._context_database(context) as db:
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            first_head = next(edge['reference']['artifact'] for edge in baseline['dependencies']
                if edge['reference']['identity'] == identity)
            first_age = state._metadata(db, first_head['name'])['created_at']
        observed = self.changes._observe_recovery_release(
            [evidence._canonical_plain(c.authority_set[0]) for c in self.operation.contexts], identity)
        self.assertTrue(observed.content['stopped'])
        self.assertIn(dict(authority=evidence._canonical_plain(context.authority_set[0]), artifact=guidance),
            evidence._canonical_plain(observed.content)['artifacts'])
        released = self.changes._register_recovery_release(observed)
        self.assertTrue(released['complete'], released)
        with evidence._context_database(context) as db:
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            old_edge = next(edge for edge in baseline['dependencies'] if edge['reference']['identity'] == identity)
            head = old_edge['reference']['artifact']
            original = evidence._managed_artifact(None, head, context=context, db=db)
            self.assertNotEqual(head, first_head)
            self.assertEqual(original[0]['created_at'], first_age)
            history = [(dict(ref), dict(body)) for ref, body in evidence._recovery_source_history(context, db, head, identity)]
        protected = {}
        for peer in self.operation.contexts:
            with evidence._context_database(peer) as db:
                baseline = evidence._canonical_plain(state._source_baseline(None, db, context=peer))
                edge = next(row for row in baseline['dependencies'] if row['reference']['identity'] == identity)
                pointer = edge['reference']['artifact']
                protected[str(peer.database)] = (edge, pointer,
                    evidence._managed_artifact(None, pointer, context=peer, db=db),
                    [(dict(ref), dict(body)) for ref, body in evidence._recovery_source_history(peer, db, pointer, identity)])
        second = self._fresh_request('unrelated-edge-update')
        self.assertNotEqual(second['manifest']['transaction_id'], identity['transaction_id'])
        self.assertNotEqual(str(fixture.directory), identity['plan_dir'])
        batches = []
        real_register = evidence._register_recovery_source

        def preserve_original(actual_scope, db, source, *, context=None):
            edge, pointer, retained, chain = protected[str(context.database)]
            before = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            self.assertIn(edge, before['dependencies'])
            reference = real_register(actual_scope, db, source, context=context)
            after = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            self.assertIn(edge, after['dependencies'])
            self.assertEqual(evidence._managed_artifact(None, pointer, context=context, db=db), retained)
            self.assertEqual([(dict(ref), dict(body)) for ref, body in evidence._recovery_source_history(context, db, pointer, identity)], chain)
            self.assertNotEqual(evidence._canonical_plain(source.content)['identity'], identity)
            batches.append((str(context.database), evidence._canonical_plain(reference)))
            return reference

        with self._host(facts, grants) as host:
            with patch.object(evidence, '_register_recovery_source', side_effect=preserve_original):
                result = self.changes._run_scoped(fixture.directory, second, 'apply', host=host)
        self.assertEqual(result['state'], 'applied', result)
        self.assertTrue(all(row['id'] == 'qualification' for row in result['blocked']), result)
        self.assertEqual(len(batches), 2 * len(self.operation.contexts))
        with evidence._context_database(context) as db:
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            self.assertIn(old_edge, baseline['dependencies'])
            new_edge = next(edge for edge in baseline['dependencies'] if edge['reference']['identity'] == facts[-1]['identity'])
            self.assertEqual(new_edge['reference']['identity'], facts[-1]['identity'])
            self.assertEqual(new_edge['reference']['artifact'],
                [reference for database, reference in batches if database == str(context.database)][-1])
            self.assertEqual(evidence._managed_artifact(None, head, context=context, db=db), original)
            self.assertEqual([(dict(ref), dict(body)) for ref, body in evidence._recovery_source_history(context, db, head, identity)], history)
        self.assertTrue(all(self.changes._authority_owner(marker) is None for marker in self._markers(context, fixture.repo)))


if __name__ == '__main__':
    unittest.main()
