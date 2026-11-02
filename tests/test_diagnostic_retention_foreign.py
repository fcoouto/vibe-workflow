"""Real source intent publication; fixture stop authority remains synthetic."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import state
from tests import test_evidence, test_foreign_admission


class DiagnosticRetentionForeignTests(unittest.TestCase):
    def test_retired_reverse_discovery_preserves_prospective_target_guard(self):
        fixture = test_evidence.CanonicalEvidenceTests(methodName='runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        task, context, owner, attempt = test_evidence.AssignedCancelledClosureTests.stopped(fixture)
        test_evidence.AssignedCancelledClosureTests.close(fixture, task, context, owner)
        api = fixture.fixture.api
        output = api._read(fixture.scope, attempt['execution'])['output']
        api._retire_closed_task(context, owner)
        source = dict(authority=owner['authority'], reference=output)
        with api._context_database(context) as db, api._protect_content_target(source):
            metadata = state._metadata(db, output['name'])
            self.assertIn(owner['authority'], api._reverse_source_authorities(context, db, metadata))
            with self.assertRaisesRegex(state.StateError, 'protected prospective target'):
                api._managed_content(context, output, metadata)

    def test_incomplete_foreign_intent_keeps_actual_diagnostic_output_protected(self):
        foreign = test_foreign_admission.ForeignAdmissionTests(methodName='runTest')
        foreign.setUp()
        self.addCleanup(foreign.doCleanups)
        destination = foreign.fixture
        source, integration, observations, calls = foreign.source_for(destination)
        source.plan()
        stopped_fixture = SimpleNamespace(fixture=source, scope=source.scope)
        task, context, owner, attempt = test_evidence.AssignedCancelledClosureTests.stopped(stopped_fixture)
        closed = test_evidence.AssignedCancelledClosureTests.close(stopped_fixture, task, context, owner)
        execution = source.api._read(source.scope, attempt['execution'])
        output = execution['output']
        source.api._retire_closed_task(context, owner)
        self.assertIsNotNone(closed['lifecycle']['safe_closed_at'])
        mandate = source.api._source_mandate(context, output)
        self.assertEqual(mandate['content']['source']['artifact'], output)

        original_observe = observations['observe']
        qualified_output = dict(authority=integration['authority'], reference=output)

        def observe(facts):
            acquired = original_observe(facts)
            self.assertIn(integration, acquired['shared'][0]['evidence'])
            acquired['shared'][0]['evidence'].append(qualified_output)
            return acquired

        observations['observe'] = observe
        original_commit = destination.api._commit_foreign_intent
        committed = []

        def interrupt_after_output_commit(plan, selected):
            receipt = original_commit(plan, selected)
            if selected == qualified_output:
                committed.append(receipt)
                raise state.StateError('fixture: interrupted after durable diagnostic intent')
            return receipt

        with patch.object(destination.api, '_commit_foreign_intent', side_effect=interrupt_after_output_commit):
            with self.assertRaisesRegex(state.StateError, 'interrupted after durable diagnostic intent'):
                destination.verify(observations)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(committed), 1)
        self.assertEqual(source.api._source_intents(context, output), committed)
        self.assertNotIn('linked_verification', destination.task(destination.selected)['evidence'])
        target = committed[0]['content']['destination']
        destination_context = destination.api._reference_context(target['authority'])
        with destination.api._context_database(destination_context) as db:
            self.assertIsNone(state._metadata(db, target['artifact']['name']))

        with source.api._context_database(context) as db:
            observation = source.api._diagnostic_output_routes(context, db, owner)
        self.assertEqual(observation['owner'], owner)
        self.assertEqual(observation['coverage'], 'classification-only')
        self.assertFalse(observation['deletion_authorized'])
        classified = next(row for row in observation['outputs'] if row['reference'] == output)
        self.assertEqual(classified['execution'], attempt['execution'])
        self.assertEqual(classified['closure'], closed['lifecycle']['compact'])
        intent = committed[0]['reference']
        identities = (intent, dict(authority=integration['authority'], artifact=intent),
                      dict(authority=integration['authority'], reference=intent))
        routes = [route for route in classified['routes'] if route['owner'] in identities]
        self.assertTrue(routes)
        self.assertTrue(all(route['kind'] == 'other' and route['disposition'] == 'protected' for route in routes))
        self.assertTrue(classified['blockers'])


if __name__ == '__main__':
    unittest.main()
