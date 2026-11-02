"""Stage A source-owned foreign-content admission tests."""
import unittest
from unittest.mock import patch

from runtime import state
from tests import test_evidence


class ForeignAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_evidence.LinkedVerificationTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def third_source(self):
        source = test_evidence.EvidenceTests(methodName='runTest')
        source.setUp()
        self.addCleanup(source.doCleanups)
        return source

    def source_for(self, fixture):
        source = self.third_source()
        task = state.task_status(source.scope, 'codex', 'parent')['task']
        owner = source.api._artifact_owner(source.scope, task)
        value = dict(version=1, kind='linked-integration-observation', inputs_digest=fixture.api._digest(fixture.input_rows()),
            criteria=dict(together='passed'), observed_at=101, reference='fixture:independent third-authority source')
        local = source.api._artifact(source.scope, value, owner=owner)
        authority = fixture.reference(source)['authority']
        reference = dict(authority=authority, reference=local)
        observations = fixture.observations()
        original = observations['observe']
        calls = []

        def observe(facts):
            calls.append(True)
            acquired = original(facts)
            acquired['shared'][0]['evidence'] = [reference]
            return acquired

        observations['observe'] = observe
        return source, reference, observations, calls

    def test_third_authority_publication_binds_mandate_to_original_owner_and_age(self):
        source = self.third_source()
        task = state.task_status(source.scope, 'codex', 'parent')['task']
        owner = source.api._artifact_owner(source.scope, task)
        value = dict(version=1, kind='linked-integration-observation', inputs_digest='a' * 64,
            criteria=dict(together='passed'), observed_at=101, reference='fixture:independent source')
        reference = source.api._artifact(source.scope, value, owner=owner)
        context = source.api._evidence_context(source.scope)
        with source.api._context_database(context) as db:
            source_metadata = state._metadata(db, reference['name'])
        mandate = source.api._source_mandate(context, reference)

        expected_source = dict(authority=source.api._canonical_plain(context.authority_set[0]),
            artifact=reference, owner=source_metadata['owner'], scope_ref=task['scope_ref'], pins=task['pins'],
            created_at=source_metadata['created_at'], managed_binding=source_metadata['managed'])
        self.assertEqual(mandate['content'], dict(version=1, kind='source-import-mandate',
            source=expected_source, operation='protective-import-bookkeeping'))
        self.assertEqual(mandate['metadata']['owner'], source_metadata['owner'])
        self.assertEqual(mandate['metadata']['created_at'], source_metadata['created_at'])
        with self.assertRaises(state.StateError):
            source.api._source_mandate(context, mandate['reference'])

    def test_missing_current_source_record_rejects_fresh_consumption_without_recreation(self):
        fixture = self.fixture
        source, reference, observations, _ = self.source_for(fixture)
        context = source.api._evidence_context(source.scope)
        source.scope.authority_path.unlink()

        with self.assertRaises(state.StateError):
            fixture.verify(observations)
        self.assertFalse(source.scope.authority_path.exists())
        self.assertEqual(source.api._source_intents(context, reference['reference']), [])
        self.assertNotIn('linked_verification', fixture.task(fixture.selected)['evidence'])

    def test_successful_source_intent_replay_reuses_original_receipt(self):
        fixture = self.fixture
        source, reference, observations, calls = self.source_for(fixture)
        context = source.api._evidence_context(source.scope)
        original = fixture.api._commit_foreign_intent
        receipts = []

        def commit_then_replay(plan, selected):
            first = original(plan, selected)
            with patch.object(fixture.api.uuid, 'uuid4', side_effect=AssertionError('replay allocated a fresh identity')):
                second = original(plan, selected)
            receipts.extend((first, second))
            return second

        with patch.object(fixture.api, '_commit_foreign_intent', side_effect=commit_then_replay):
            result = fixture.verify(observations)

        self.assertEqual(receipts[0]['reference'], receipts[1]['reference'])
        self.assertEqual(receipts[0]['metadata'], receipts[1]['metadata'])
        self.assertEqual(receipts[0]['metadata']['created_at'], receipts[1]['metadata']['created_at'])
        self.assertEqual(receipts[0]['content']['observed_at'], receipts[1]['content']['observed_at'])
        self.assertEqual(len(source.api._source_intents(context, reference['reference'])), 1)
        self.assertTrue(result['ok'] and result['verified'])
        self.assertEqual(result['task']['state'], 'DONE')
        self.assertEqual(len(calls), 1)

    def test_source_commit_failure_and_lost_permission_keep_retry_unpublished(self):
        fixture = self.fixture
        source, reference, observations, calls = self.source_for(fixture)
        context = source.api._evidence_context(source.scope)
        sqlite = fixture.api.sqlite3
        connect = sqlite.connect
        uri = context.database.as_uri()
        commit_denials_after_insert = []

        def deny_source_commit(database, *args, **kwargs):
            connection = connect(database, *args, **kwargs)
            if str(database).startswith(uri):
                inserted_metadata = []

                def authorize(action, argument, *_):
                    if action == sqlite.SQLITE_INSERT and argument == 'evidence_metadata':
                        inserted_metadata.append(True)
                    if action == sqlite.SQLITE_TRANSACTION and argument == 'COMMIT':
                        commit_denials_after_insert.append(bool(inserted_metadata))
                        return sqlite.SQLITE_DENY
                    return sqlite.SQLITE_OK

                connection.set_authorizer(authorize)
            return connection

        with patch.object(sqlite, 'connect', side_effect=deny_source_commit):
            with self.assertRaises(state.StateError):
                fixture.verify(observations)
        self.assertIn(True, commit_denials_after_insert)
        self.assertEqual(source.api._source_intents(context, reference['reference']), [])
        self.assertNotIn('linked_verification', fixture.task(fixture.selected)['evidence'])

        # A separate producer reaches a real durable source commit, then loses current enrollment.
        fixture = test_evidence.LinkedVerificationTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source, reference, observations, calls = self.source_for(fixture)
        context = source.api._evidence_context(source.scope)
        original = fixture.api._commit_foreign_intent

        def commit_then_remove_record(plan, selected):
            receipt = original(plan, selected)
            if selected == reference:
                source.scope.authority_path.unlink()
            return receipt

        with patch.object(fixture.api, '_commit_foreign_intent', side_effect=commit_then_remove_record):
            with self.assertRaises(state.StateError):
                fixture.verify(observations)
        self.assertFalse(source.scope.authority_path.exists())
        self.assertEqual(len(source.api._source_intents(context, reference['reference'])), 1)
        self.assertNotIn('linked_verification', fixture.task(fixture.selected)['evidence'])
        self.assertEqual(len(calls), 1)
        with patch.object(fixture.api, '_verify_member', side_effect=AssertionError('prior producer reran')):
            with patch.object(fixture.api.uuid, 'uuid4', side_effect=AssertionError('fresh publication allocated')):
                with self.assertRaises(state.StateError):
                    fixture.verify(observations)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(source.api._source_intents(context, reference['reference'])), 1)


if __name__ == '__main__':
    unittest.main()
