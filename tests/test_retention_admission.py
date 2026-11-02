"""Prospective detail-admission provenance tests; native execution remains synthetic."""
import unittest
from unittest.mock import patch

from tests import test_evidence
from runtime import state


class RetentionAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_evidence.EvidenceTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scope = self.fixture.scope
        self.fixture.plan()
        self.api = self.fixture.api

    def source(self):
        attempt = self.fixture.checked()['attempt']
        return attempt['execution'], self.api._evidence_context(self.scope), state.task_status(self.scope, 'codex', 'parent')['task']

    def test_new_detail_admission_records_v2_qualified_live_source(self):
        source, context, task = self.source()
        authority = self.api._canonical_plain(context.authority_set[0])
        task_ref = self.api._canonical_plain(self.api._artifact_owner(self.scope, task).task)
        with self.api._context_database(context) as db:
            source_metadata = state._metadata(db, source['name'])
        self.assertEqual(source_metadata['status'], 'live')
        self.assertEqual(source_metadata['owner']['task'], task_ref)

        body = dict(kind='fixture-detail-admission', source=dict(authority=authority, artifact=source))
        admitted = self.api._artifact(self.scope, body, owner=self.api._artifact_owner(self.scope, task))
        with self.api._context_database(context) as db:
            metadata = state._metadata(db, admitted['name'])
        self.assertEqual(metadata['version'], 2)
        self.assertEqual(metadata['status'], 'live')
        self.assertEqual(metadata['owner']['task'], task_ref)
        self.assertEqual(metadata['dependencies'], [
            dict(kind='task', reference=task_ref),
            dict(kind='artifact', reference=dict(authority=authority, artifact=source)),
        ])

    def test_cached_live_source_that_becomes_pending_cannot_admit_new_detail(self):
        source, context, task = self.source()
        cached = self.api._read(None, source, context=context)
        authority = self.api._canonical_plain(context.authority_set[0])
        owner = self.api._artifact_owner(self.scope, task)
        with self.api._context_database(context, write=True) as db:
            metadata = state._metadata(db, source['name'])
            pending = dict(metadata, status='delete_pending')
            db.execute('update evidence_metadata set status=?,data=? where name=?',
                ('delete_pending', self.api._raw(pending).decode(), source['name']))
        with self.api._context_database(context) as db:
            before = list(db.iterdump())
        evidence_dir = context.directory / 'evidence'
        files_before = sorted(path.name for path in evidence_dir.iterdir())
        body = dict(kind='fixture-pending-source-admission', source=dict(authority=authority, artifact=source))
        with self.assertRaises(state.StateError):
            self.api._artifact(self.scope, body, owner=owner)
        with self.api._context_database(context) as db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertEqual(sorted(path.name for path in evidence_dir.iterdir()), files_before)

    def test_stale_runtime_cannot_admit_new_detail(self):
        _, context, task = self.source()
        owner = self.api._artifact_owner(self.scope, task)
        with self.api._context_database(context) as db:
            before = list(db.iterdump())
        files_before = sorted(path.name for path in (context.directory / 'evidence').iterdir())
        with patch.object(state, 'runtime_fingerprint', return_value='f' * 64):
            with self.assertRaises(state.StateError):
                self.api._artifact(self.scope, dict(kind='fixture-stale-runtime'), owner=owner)
        with self.api._context_database(context) as db:
            self.assertEqual(list(db.iterdump()), before)
        self.assertEqual(sorted(path.name for path in (context.directory / 'evidence').iterdir()), files_before)


if __name__ == '__main__':
    unittest.main()
