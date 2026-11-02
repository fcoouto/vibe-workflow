"""Real consumer and linked lifecycle fixtures; host/stop authority is simulated."""
import unittest
from unittest.mock import patch

from runtime import state
from tests import test_evidence as evidence_tests
from tests import test_foreign_admission as foreign_tests


class DiagnosticCleanupConsumerTests(unittest.TestCase):
    retention_age = 604800

    def setUp(self):
        self.clock = [1_800_000_000]
        self.enterContext(patch.object(state, '_owner_time', side_effect=lambda: self.clock[0]))

    def fixture(self):
        fixture = evidence_tests.CanonicalEvidenceTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def ordinary(self):
        fixture = self.fixture()
        task, context, owner, attempt = evidence_tests.AssignedCancelledClosureTests.stopped(fixture)
        closed = evidence_tests.AssignedCancelledClosureTests.close(fixture, task, context, owner)
        api = fixture.fixture.api
        output = api._read(fixture.scope, attempt['execution'])['output']
        retired = api._retire_closed_task(context, owner)
        self.assertTrue(retired['retired'], retired)
        return fixture, context, owner, attempt, output, closed

    def assert_protected(self, api, context, output, closed):
        path = context.directory / 'evidence' / output['name']
        before = path.read_bytes()
        self.clock[0] = closed['lifecycle']['safe_closed_at'] + self.retention_age
        result = api._cleanup_diagnostics(context)
        self.assertNotIn(output, result['expired'])
        self.assertNotIn(output, result['pending'])
        protected = [row for row in result['protected'] if row['reference'] == output]
        self.assertTrue(protected, result)
        self.assertTrue(all(row['reason'] for row in protected))
        self.assertEqual(path.read_bytes(), before)
        with api._context_database(context) as db:
            self.assertEqual(state._metadata(db, output['name'])['status'], 'live')
        return result

    def test_independent_live_consumer_prevents_seven_day_output_deletion(self):
        fixture, context, _, _, output, closed = self.ordinary()
        source = fixture.fixture
        other = state.start_task(fixture.scope, source.identity | dict(
            session_id='consumer-session', task_id='consumer-task', scope_ref='fixture:independent-consumer',
            mode='editable', decision_ref='user:accepted'))['task']
        consumer = source.api._artifact(fixture.scope, dict(kind='fixture-live-consumer', output=output),
            owner=source.api._artifact_owner(fixture.scope, other))
        self.assertEqual(source.api._read(fixture.scope, consumer)['output'], output)
        self.assert_protected(source.api, context, output, closed)

    def test_real_incomplete_foreign_intent_prevents_output_deletion(self):
        fixture, context, owner, _, output, closed = self.ordinary()
        source = fixture.fixture
        foreign = foreign_tests.ForeignAdmissionTests(methodName='runTest')
        foreign.setUp()
        self.addCleanup(foreign.doCleanups)
        destination = foreign.fixture
        observations = destination.observations()
        original_observe = observations['observe']
        qualified_output = dict(authority=owner['authority'], reference=output)

        def observe(facts):
            acquired = original_observe(facts)
            self.assertTrue(acquired['shared'][0]['evidence'])
            acquired['shared'][0]['evidence'].append(qualified_output)
            return acquired

        observations['observe'] = observe
        original_commit = destination.api._commit_foreign_intent
        committed = []

        def interrupt_after_commit(plan, selected):
            receipt = original_commit(plan, selected)
            if selected == qualified_output:
                committed.append(receipt)
                raise state.StateError('fixture: interrupted after actual diagnostic intent commit')
            return receipt

        with patch.object(destination.api, '_commit_foreign_intent', side_effect=interrupt_after_commit):
            with self.assertRaisesRegex(state.StateError, 'interrupted after actual diagnostic intent commit'):
                destination.verify(observations)
        self.assertEqual(len(committed), 1)
        self.assertEqual(source.api._source_intents(context, output), committed)
        self.assertNotIn('linked_verification', destination.task(destination.selected)['evidence'])
        target = committed[0]['content']['destination']
        selected = destination.api._reference_context(target['authority'])
        with destination.api._context_database(selected) as db:
            self.assertIsNone(state._metadata(db, target['artifact']['name']))
        self.assert_protected(source.api, context, output, closed)
        self.assertEqual(source.api._source_intents(context, output), committed)

    def test_closed_unretired_inventory_consumer_protects_unreferenced_output(self):
        fixture, context, _, _, output, closed = self.ordinary()
        source = fixture.fixture
        self.clock[0] += 1
        source.identity = source.identity | dict(task_id='inventory-task',
            scope_ref='fixture:separate-inventory-owner')
        state.start_task(fixture.scope, source.identity | dict(mode='editable', decision_ref='user:accepted'))
        source.plan()
        original_root = source.root
        second_root = original_root / 'inventory-owner-proofs'
        second_root.mkdir(mode=0o700)
        try:
            source.root = second_root
            task, second_context, second_owner, _ = evidence_tests.AssignedCancelledClosureTests.stopped(fixture)
        finally:
            source.root = original_root
        second_closed = evidence_tests.AssignedCancelledClosureTests.close(fixture, task, second_context, second_owner)
        self.assertEqual(context.database, second_context.database)
        compact = source.api._read(fixture.scope, second_closed['lifecycle']['compact'])
        self.assertNotIn(output, [edge['reference'] for edge in source.api._artifact_dependencies(compact)])
        with source.api._context_database(context) as db:
            self.assertIsNone(state._retired(db, 'task', {key: second_owner[key]
                for key in ('scope_id', 'client', 'session_id', 'task_id')}))
        self.assert_protected(source.api, context, output, closed)
        self.assertTrue(source.api._retire_closed_task(context, second_owner)['retired'])

    def test_linked_preparation_after_component_expiry_keeps_required_content(self):
        fixture, context, _, _, output, closed = self.ordinary()
        source = fixture.fixture
        self.clock[0] = closed['lifecycle']['safe_closed_at'] + self.retention_age
        # Isolate prior unlink state; this is not public canonical cleanup eligibility.
        with patch.object(source.api, '_diagnostic_consumers',
                return_value=dict(complete=True, required=[], blockers=[])):
            result = source.api._cleanup_diagnostics(context)
        self.assertIn(output, result['expired'], result)
        self.assertFalse((context.directory / 'evidence' / output['name']).exists())
        with source.api._context_database(context) as db:
            self.assertEqual(state._metadata(db, output['name'])['status'], 'expired')

        source.identity = source.identity | dict(task_id='linked-successor', scope_ref='fixture:linked-successor')
        state.start_task(fixture.scope, source.identity | dict(mode='editable', decision_ref='user:accepted'))
        peer = self.fixture().fixture
        linked = evidence_tests.LinkedVerificationTests(methodName='runTest')
        self.addCleanup(linked.doCleanups)
        linked.api = source.api
        linked.members = [source, peer]
        linked.selected = source
        linked.establish()
        verified = linked.verify()
        self.assertTrue(verified['verified'], verified)
        pointer = verified['verification']
        selected_task = linked.task(source)
        execution = next(attempt['execution'] for attempt in selected_task['evidence']['attempts']
            if attempt['kind'] == 'check')
        required_output = source.api._read(fixture.scope, execution)['output']
        required_path = context.directory / 'evidence' / required_output['name']
        required_bytes = required_path.read_bytes()
        authorities = linked.preparation_stops(pointer)
        observed = source.api._observe_linked_preparation(pointer, authorities)
        facts = source.api._canonical_plain(observed.facts)
        self.assertEqual(len(facts['members']), 2)
        self.assertIn(linked.reference(source), [member['reference'] for member in facts['members']])
        self.clock[0] += self.retention_age
        repeated = source.api._cleanup_diagnostics(context)
        self.assertNotIn(required_output, repeated['expired'])
        self.assertNotIn(required_output, repeated['pending'])
        self.assertEqual(required_path.read_bytes(), required_bytes)
        with source.api._context_database(context) as db:
            self.assertEqual(state._metadata(db, required_output['name'])['status'], 'live')

        contexts = tuple(source.api._evidence_context(member.scope) for member in linked.members)
        references = tuple(linked.reference(member) for member in linked.members)
        fresh = source.api._observe_linked_preparation(pointer, authorities)
        prepared = source.api._prepare_linked_closure(fresh)
        self.assertTrue(prepared['complete'], prepared)
        self.assertEqual(len(prepared['preparations']), len(linked.members))
        closed_members = []
        for index, member in enumerate(linked.members):
            observation = source.api._observe_closure(contexts, references[index])
            result = source.api.close_task(member.scope, linked.linked_closure_request(index), observation)
            self.assertIsNotNone(result['task']['lifecycle']['safe_closed_at'])
            closed_members.append(result['task'])
        compact_bytes = [(selected.directory / 'evidence' / task['lifecycle']['compact']['name']).read_bytes()
                         for selected, task in zip(contexts, closed_members)]
        retired = [source.api._retire_closed_task(selected, reference)
                   for selected, reference in zip(contexts, references)]
        self.assertTrue(all(result['retired'] for result in retired))
        for selected, reference, result, task, original in zip(
                contexts, references, retired, closed_members, compact_bytes):
            self.assertEqual(source.api._retire_closed_task(selected, reference), result)
            self.assertEqual((selected.directory / 'evidence' / task['lifecycle']['compact']['name']).read_bytes(),
                             original)
        self.assertFalse((context.directory / 'evidence' / output['name']).exists())
        with source.api._context_database(context) as db:
            self.assertEqual(state._metadata(db, output['name'])['status'], 'expired')
            self.assertEqual(state._metadata(db, required_output['name'])['status'], 'live')
        self.assertEqual(required_path.read_bytes(), required_bytes)


if __name__ == '__main__':
    unittest.main()
