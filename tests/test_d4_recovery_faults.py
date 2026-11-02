"""D4 partial durable recovery publication and retry tests."""
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import evidence, state
from tests import test_d4_owned_transitions


class D4RecoveryFaultTests(unittest.TestCase):
    def setUp(self):
        self.scenario = test_d4_owned_transitions.D4OwnedTransitionTests(methodName='runTest')
        self.addCleanup(self.scenario.doCleanups)
        self.scenario.setUp()
        self.fixture = self.scenario.fixture

    # Partial peer commits: test_canonical_recovery.CanonicalTwoStoreSetupTests.
    # test_actual_partial_peer_commit_resumes_original_identity_and_age.

    def test_actual_taskless_guidance_release_keeps_nonempty_independent_support(self):
        request, path, guidance, context, project = self.scenario.guidance_request()
        with evidence._context_database(context) as db:
            original_guidance = state._metadata(db, guidance['name'])
        self.assertEqual(original_guidance['owner'], dict(kind='project', project_id=project, task=None))
        facts = []

        class Host:
            def acquire_d4_bookkeeping(self, acquired, issue):
                facts.append(evidence._canonical_plain(acquired))
                return issue(acquired)

        with self.fixture.changes._bootstrap_d4_host(
                Host(), issuer=self.fixture.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
            applied = self.fixture.changes._run_scoped(self.fixture.directory, request, 'apply', host=host)
        self.assertEqual(applied['exit_code'], 0, applied)
        self.assertEqual(applied['state'], 'applied', applied)
        self.assertTrue(path.exists())
        self.assertEqual(len(facts), 2)
        identity = facts[0]['identity']
        authority = evidence._canonical_plain(context.authority_set[0])
        with evidence._context_database(context) as db:
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            edge = next(row for row in baseline['dependencies'] if row['reference']['identity'] == identity)
            original_head = edge['reference']['artifact']
            source_body = evidence._canonical_plain(evidence._read(None, original_head, context=context, db=db))
            self.assertTrue(source_body['tasks'])
            self.assertTrue(source_body['assignments'])
            self.assertIn(dict(authority=authority, artifact=guidance), source_body['artifacts'])
            self.assertIn(dict(kind='artifact', reference=dict(authority=authority, artifact=guidance)),
                state._metadata(db, original_head['name'])['dependencies'])
            original_age = state._metadata(db, original_head['name'])['created_at']

        released = self.fixture.changes._observe_recovery_release(
            [evidence._canonical_plain(c.authority_set[0]) for c in self.scenario.operation.contexts], identity)
        release_content = evidence._canonical_plain(released.content)
        self.assertTrue(release_content['stopped'])
        self.assertTrue(release_content['stopped'][0]['assignments'])
        self.assertEqual(release_content['stopped'][0]['assignments'][0]['reconciliation']['outcome'], 'cancelled')
        self.assertIn(dict(authority=authority, artifact=guidance), release_content['artifacts'])
        result = self.fixture.changes._register_recovery_release(released)
        self.assertTrue(result['complete'], result)
        self.assertEqual(len(result['replacements']), len(self.scenario.operation.contexts))
        with evidence._context_database(context) as db:
            baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
            edge = next(row for row in baseline['dependencies'] if row['reference']['identity'] == identity)
            replacement = edge['reference']['artifact']
            body = evidence._canonical_plain(evidence._read(None, replacement, context=context, db=db))
            metadata = state._metadata(db, replacement['name'])
            self.assertNotEqual(replacement, original_head)
            self.assertEqual(metadata['created_at'], original_age)
            self.assertTrue(body['stopped'])
            self.assertEqual(body['stopped'][0]['assignments'][0]['reconciliation']['outcome'], 'cancelled')
            self.assertIn(dict(authority=authority, artifact=guidance), body['artifacts'])
            self.assertIn(dict(kind='artifact', reference=dict(authority=authority, artifact=guidance)), metadata['dependencies'])
            retained = state._metadata(db, guidance['name'])
            self.assertEqual(retained['owner'], original_guidance['owner'])
            self.assertEqual(retained['created_at'], original_guidance['created_at'])
        root = Path(request['manifest']['enrollment']['record']['project_root'])
        canonical_marker = Path(self.fixture.changes.LOCK_ROOT) / ('canonical-' + hashlib.sha256(
            ('destination:' + str(root / self.fixture.changes.PROJECT_FILE)).encode()).hexdigest() + '.lock')
        for marker in [*self.fixture.changes._authority_locations([context.directory]), canonical_marker]:
            self.assertIsNone(self.fixture.changes._authority_owner(marker), str(marker))


if __name__ == '__main__':
    unittest.main()
