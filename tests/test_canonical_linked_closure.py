"""Current linked owner closure; host observations are explicitly synthetic."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import changes, evidence, state
from runtime.engine import RuntimeEvent
from tests import test_canonical_composition as canonical
from tests import test_canonical_recovery as recovery
from tests import test_canonical_rollback as rollback


class CanonicalLinkedClosureTests(unittest.TestCase):
    def setUp(self):
        host = recovery.CanonicalTwoStoreSetupTests('runTest')
        self.addCleanup(host.doCleanups)

        def seed_sources(birth, fixture, contexts):
            record = json.loads((fixture.base / changes.PROJECT_FILE).read_bytes())
            for _, checkout, _ in changes._scoped_entries(record).values():
                (Path(checkout['root']) / 'app.py').write_text('value = 1\n')

        operation = rollback.CanonicalOwnedRollbackTests('runTest')
        self.addCleanup(operation.doCleanups)
        operation.birth, operation.prepared, operation.contexts, operation.histories, operation.originals = (
            host.prepare_existing_two_store_umbrella(before_final=seed_sources))
        operation.fixture, operation.request, _, _ = operation.prepared
        operation.authority = Path(operation.request['manifest']['enrollment']['record']['project_root']) / changes.PROJECT_FILE
        operation.host = host
        self.operation = operation
        self.qualifiers = operation.active_manifest()
        pending = operation.invoke('apply')
        self.assertEqual(pending['exit_code'], 2, pending)
        self.assertTrue(any(row['reason'] == 'Fresh scoped adoption qualification observations are required'
                            for row in pending['blocked']), pending)
        applied = operation.invoke('resume', operation.fresh_qualification(self.qualifiers))
        self.assertEqual(applied['state'], 'applied', applied)
        self.assertEqual(applied['exit_code'], 0, applied)
        self.assertTrue(applied['qualified'], applied)
        self.assertEqual(applied['activation_status'], 'active', applied)
        self.assertTrue(changes._scoped_owned_authority(changes._load_journal(operation.fixture.directory)))
        self.linked = canonical.CanonicalLinkedLifecycleTests('runTest')
        self.addCleanup(self.linked.doCleanups)
        self.linked.api = evidence
        self.linked.members = []
        # Lifecycle setup would create replacement stores instead of these owned members.
        for index, (root, qualifier) in enumerate(self.qualifiers.items()):
            member = canonical.CanonicalLifecycleTests('runTest')
            self.addCleanup(member.doCleanups)
            member.scope = operation.acquire(root, qualifier)
            member.repo = member.scope.repository_root
            member.root = operation.fixture.directory / 'linked-observations' / str(index)
            member.root.mkdir(parents=True)
            member.identity = dict(client='codex', session_id='parent', task_id='task', scope_ref='user:task')
            member.api = evidence
            state.start_task(member.scope, member.identity | dict(mode='editable', decision_ref='user:accepted'))
            self.linked.members.append(member)
        self.assertEqual(len(self.linked.members), 2)
        self.linked.selected = self.linked.members[0]
        self.linked.establish()

    def retain_task_recovery_source(self, contexts, references):
        operation = self.operation
        original = changes._load_journal(operation.fixture.directory)
        identity = dict(plan_dir=str(operation.fixture.directory),
            **{key: original[key] for key in ('transaction_id', 'manifest_digest')})
        files = operation.protected_files()
        tasks = [self.linked.task(member) for member in self.linked.members]
        ages = []
        for context in contexts:
            _, sources = changes._recovery_sources(context)
            matching = [(reference, body) for reference, body in sources if body['identity'] == identity]
            self.assertEqual(len(matching), 1)
            with evidence._context_database(context) as db:
                ages.append(state._metadata(db, matching[0][0]['name'])['created_at'])
        self.assertEqual(len(set(ages)), 1)
        resumed = operation.invoke('resume', operation.fresh_qualification(self.qualifiers))
        self.assertEqual(resumed['state'], 'applied', resumed)
        self.assertEqual(resumed['exit_code'], 0, resumed)
        self.assertTrue(resumed['qualified'], resumed)
        journal = changes._load_journal(operation.fixture.directory)
        for key in ('manifest', 'approval', 'transaction_id', 'manifest_digest'):
            self.assertEqual(journal[key], original[key])
        self.assertTrue(changes._scoped_owned_authority(journal))
        operation.assert_files_retained(files)
        operation.assert_history_retained()
        self.assertEqual([self.linked.task(member) for member in self.linked.members], tasks)
        authorities = [evidence._canonical_plain(context.authority_set[0]) for context in contexts]
        retained = []
        for context, age in zip(contexts, ages):
            _, sources = changes._recovery_sources(context)
            matching = [(reference, body) for reference, body in sources if body['identity'] == identity]
            self.assertEqual(len(matching), 1)
            reference, body = matching[0]
            self.assertTrue(all(any(row['reference'] == ref for row in body['tasks']) for ref in references))
            self.assertEqual(body['journal']['state'], 'applied')
            self.assertEqual(body['journal']['sha256'],
                hashlib.sha256((operation.fixture.directory / 'transaction.json').read_bytes()).hexdigest())
            self.assertEqual(sorted(body['authorities'], key=evidence._raw), sorted(authorities, key=evidence._raw))
            with evidence._context_database(context) as db:
                self.assertEqual(state._metadata(db, reference['name'])['created_at'], age)
            retained.append(body)
        return retained[0]

    def stop_request(self, member, pointer):
        task = self.linked.task(member)
        context = evidence._evidence_context(member.scope)
        with evidence._context_database(context) as db:
            bindings = state._ready_stop_bindings(context, db, task)
        plan = evidence._read(member.scope, task['evidence']['plan'])
        candidate = evidence.capture_candidate(member.scope, plan)
        proof = dict(version=2, kind='stopped-writer-reconciliation', identity=member.identity,
            expected_revision=task['revision'], expected_pins=task['pins'],
            observed_turn_id=task['turn_id'], stopped=True, descendants_stopped=True,
            attempt_ids=[attempt['id'] for attempt in task['evidence']['attempts']],
            observation_ref='fixture:synthetic-independent-complete-linked-stop',
            coordinator_ref='fixture:synthetic-host-checked',
            candidate_digest=evidence._digest(candidate),
            verification_digest=task['evidence']['verification']['digest'], **bindings)
        with evidence._linked_stop_guards(pointer, evidence._canonical_plain(context.authority_set)) as authorities:
            proof['linked_digest'] = evidence._digest(evidence._linked_stop_basis(pointer, authorities))
        raw = json.dumps(proof).encode()
        path = member.root / 'synthetic-linked-stop.json'
        path.write_bytes(raw)
        path.chmod(0o400)
        return member.identity | dict(expected_revision=task['revision'], expected_pins=task['pins'],
            linked_verification=pointer,
            stop_proof=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest()))

    def closure_fixture(self):
        for member in self.linked.members:
            for kind in ('PreToolUse', 'PostToolUse'):
                state.process_event(member.scope, RuntimeEvent(kind, 'codex', 'parent', member.repo,
                    turn_id='turn', tool_call_id='linked-closure-read', tool_name='read_file',
                    receipt_fingerprint='a' * 64))
        verified = self.linked.verify()
        self.assertTrue(verified['implementation_verified'], verified)
        self.assertEqual([self.linked.task(member)['state'] for member in self.linked.members],
                         ['DONE', 'REVIEW'])
        pointer = verified['verification']
        contexts = tuple(evidence._evidence_context(member.scope) for member in self.linked.members)
        references = tuple(self.linked.reference(member) for member in self.linked.members)
        before = [self.linked.task(member) for member in self.linked.members]
        for member, task in zip(self.linked.members, before):
            stopped = state._retain_ready_stop(member.scope, self.stop_request(member, pointer))['task']
            self.assertEqual({key: value for key, value in stopped.items() if key != 'writer_stop'}, task)
        source = self.retain_task_recovery_source(contexts, references)
        authorities = source['authorities']
        observed = evidence._observe_linked_preparation(pointer, authorities)
        self.assertTrue(evidence._prepare_linked_closure(observed)['complete'])
        released = changes._register_recovery_release(changes._observe_recovery_release(authorities, source['identity']))
        self.assertTrue(released['complete'], released)
        recovery = changes.inspect_lifecycle_recovery(authorities, references)
        self.assertTrue(recovery['complete'], recovery)
        for reference in references:
            requirements = [row for row in recovery['requirements'] if row['kind'] == 'task'
                and row['reference'] == reference and row['root']['identity'] == source['identity']]
            self.assertTrue(any(row['need'] == 'identity' for row in requirements))
            self.assertFalse(any(row['need'] == 'raw' for row in requirements))
        return contexts, references, authorities

    def request(self, index):
        task = self.linked.task(self.linked.members[index])
        return {key: task[key] for key in ('client', 'session_id', 'task_id', 'scope_ref')} | dict(
            expected_revision=task['revision'], expected_pins=task['pins'])

    def raw_rows(self, contexts):
        result = []
        for context in contexts:
            with evidence._context_database(context) as db:
                rows = {name: db.execute('select * from ' + name + ' order by rowid').fetchall()
                    for name in ('tasks', 'assignments', 'turns', 'receipts', 'bindings', 'retired_identities')}
                rows['baseline'] = state._source_baseline(None, db, context=context)
                rows['birth_handoff'] = evidence._read_birth_handoff(context, db)
                result.append(rows)
        return result

    def closure_candidates(self, context, reference):
        found = []
        with evidence._context_database(context) as db:
            rows = db.execute("select name,digest,owner_key,class,created_at,closed_at,status,bytes,data "
                              "from evidence_metadata where class='compact'")
            for row in rows:
                metadata = state._metadata_record(row)
                if metadata['owner'].get('task') != reference:
                    continue
                artifact = dict(name=row[0], digest=row[1])
                body = evidence._read(None, artifact, context=context, db=db)
                if body.get('kind') == 'task-closure':
                    found.append(dict(reference=artifact, metadata=metadata, content=body))
        return found

    def test_linked_safe_closure_sequential_members_preserves_raw_history_and_recovery(self):
        contexts, references, authorities = self.closure_fixture()
        original = [self.linked.task(member) for member in self.linked.members]
        raw = self.raw_rows(contexts)
        self.assertTrue(all(rows['turns'] and rows['receipts'] for rows in raw))
        self.assertTrue(all(rows['baseline'] is not None and rows['birth_handoff'] is not None for rows in raw))
        compacts = []
        for index, (member, reference) in enumerate(zip(self.linked.members, references)):
            task = original[index]
            peers = [self.linked.task(peer) for peer in self.linked.members if peer is not member]
            observed = evidence._observe_closure(contexts, reference)
            closed = evidence.close_task(member.scope, self.request(index), observed)['task']
            compacts.append(closed['lifecycle']['compact'])
            self.assertIsNotNone(closed['lifecycle']['safe_closed_at'])
            self.assertEqual({key: value for key, value in closed.items() if key != 'lifecycle'},
                             {key: value for key, value in task.items() if key != 'lifecycle'})
            self.assertEqual(closed['lifecycle']['created_at'], task['lifecycle']['created_at'])
            compact = evidence._read(member.scope, closed['lifecycle']['compact'])
            self.assertEqual(compact['outcome'], task['state'])
            self.assertEqual(compact['created_at'], task['lifecycle']['created_at'])
            self.assertEqual([self.linked.task(peer) for peer in self.linked.members if peer is not member], peers)
            recovery = changes.inspect_lifecycle_recovery(authorities, references)
            self.assertTrue(recovery['complete'], recovery)
            for before, after in zip(raw, self.raw_rows(contexts)):
                self.assertEqual(len(after['tasks']), len(before['tasks']))
                self.assertEqual({key: value for key, value in after.items() if key != 'tasks'},
                                 {key: value for key, value in before.items() if key != 'tasks'})
        first = self.linked.task(self.linked.members[0])
        replay = evidence.close_task(self.linked.members[0].scope, self.request(0),
            evidence._observe_closure(contexts, references[0]))['task']
        self.assertEqual(replay, first)
        closed_rows = self.raw_rows(contexts)
        for context, reference in zip(contexts, references):
            historical = evidence._observe_closure(contexts, reference)
            with evidence._context_database(context, write=True) as db, self.assertRaises(state.StateError):
                state.retire_closed(db, reference, historical)
        self.assertEqual(self.raw_rows(contexts), closed_rows)
        self.assertEqual(compacts, [self.linked.task(member)['lifecycle']['compact'] for member in self.linked.members])

    def test_linked_safe_closure_second_transaction_failure_reuses_registered_candidate(self):
        contexts, references, authorities = self.closure_fixture()
        first = evidence.close_task(self.linked.members[0].scope, self.request(0),
            evidence._observe_closure(contexts, references[0]))['task']
        peer = self.linked.task(self.linked.members[1])
        before = self.raw_rows(contexts)
        close_lifecycle = state._close_lifecycle
        failed = []

        def fail_after_sql(db, reference, observation, compact):
            result = close_lifecycle(db, reference, observation, compact)
            failed.append(result)
            raise RuntimeError('fixture: failure after lifecycle SQL before commit')

        observed = evidence._observe_closure(contexts, references[1])
        with patch.object(state, '_close_lifecycle', side_effect=fail_after_sql), \
                self.assertRaisesRegex(RuntimeError, 'after lifecycle SQL before commit'):
            evidence.close_task(self.linked.members[1].scope, self.request(1), observed)
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.linked.task(self.linked.members[0]), first)
        self.assertEqual(self.linked.task(self.linked.members[1]), peer)
        self.assertEqual(self.raw_rows(contexts), before)
        candidates = self.closure_candidates(contexts[1], references[1])
        self.assertEqual(len(candidates), 1, 'Retry needs durable managed ownership, not an unregistered orphan')
        candidate = candidates[0]
        self.assertEqual(candidate['metadata']['created_at'], peer['lifecycle']['created_at'])
        path = Path(candidate['metadata']['managed']['path'])
        physical = (path.read_bytes(), evidence._closure_file_stat(path))
        retry = evidence.close_task(self.linked.members[1].scope, self.request(1),
            evidence._observe_closure(contexts, references[1]))['task']
        self.assertEqual(retry['lifecycle']['compact'], candidate['reference'])
        self.assertIsNotNone(retry['lifecycle']['safe_closed_at'])
        self.assertEqual({key: value for key, value in retry.items() if key != 'lifecycle'},
                         {key: value for key, value in peer.items() if key != 'lifecycle'})
        self.assertEqual(retry['lifecycle']['created_at'], peer['lifecycle']['created_at'])
        self.assertEqual(self.closure_candidates(contexts[1], references[1]), candidates)
        self.assertEqual((path.read_bytes(), evidence._closure_file_stat(path)), physical)
        replay = evidence.close_task(self.linked.members[1].scope, self.request(1),
            evidence._observe_closure(contexts, references[1]))['task']
        self.assertEqual(replay, retry)
        self.assertEqual(self.linked.task(self.linked.members[0]), first)
        for previous, current in zip(before, self.raw_rows(contexts)):
            self.assertEqual(len(current['tasks']), len(previous['tasks']))
            self.assertEqual({key: value for key, value in current.items() if key != 'tasks'},
                             {key: value for key, value in previous.items() if key != 'tasks'})
        recovery = changes.inspect_lifecycle_recovery(authorities, references)
        self.assertTrue(recovery['complete'], recovery)
