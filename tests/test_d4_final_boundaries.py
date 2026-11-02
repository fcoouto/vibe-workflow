"""Post-registration D4 permission and overlay checks."""

import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import evidence, state
from tests import test_d4_owned_transitions


class D4FinalBoundaryTests(unittest.TestCase):
    def test_post_registration_permission_and_overlay_changes_rollback(self):
        for boundary in ('membership', 'baseline-edge'):
            with self.subTest(boundary=boundary):
                scenario = test_d4_owned_transitions.D4OwnedTransitionTests(methodName='runTest')
                self.addCleanup(scenario.doCleanups)
                scenario.setUp()
                fixture, changes = scenario.fixture, scenario.changes
                request, source_path, _, context, _ = scenario.guidance_request()
                original_record = source_path.read_bytes()
                with evidence._context_database(context) as db:
                    before_baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
                before_effects = {action['id']: changes._fingerprint(Path(action['owner']['root']), action['effects'][0]['path'])
                    for action in request['manifest']['actions']}
                original_register = evidence._register_recovery_source
                reached, issued, changed = [], [], []
                revoked_bytes = []

                def after_real_registration(scope, db, source, *, context=None):
                    reference = original_register(scope, db, source, context=context)
                    if not reached:
                        metadata = state._metadata(db, reference['name'])
                        body = evidence._canonical_plain(evidence._read(None, evidence._canonical_plain(reference), context=context, db=db))
                        self.assertEqual(body, evidence._canonical_plain(source.content))
                        self.assertEqual(metadata['class'], 'compact')
                        self.assertEqual(metadata['created_at'], source.created_at)
                        baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
                        edges = [edge for edge in baseline['dependencies']
                            if edge['reference']['identity'] == body['identity']]
                        self.assertEqual(len(edges), 1)
                        self.assertEqual(edges[0]['reference']['artifact'], evidence._canonical_plain(reference))
                        self.assertEqual(metadata['dependencies'],
                            evidence._recovery_source_dependencies(body, evidence._canonical_plain(context.authority_set[0])))
                        reached.append(reference)
                        if boundary == 'membership':
                            record = json.loads(source_path.read_text())
                            record['membership']['exclude'] = ['.']
                            record['revision'] += 1
                            source_path.write_text(json.dumps(record))
                            revoked_bytes.append(source_path.read_bytes())
                            changed.append('membership')
                        else:
                            baseline_row = state._source_baseline(None, db, context=context)
                            baseline_metadata = state._metadata(db, baseline_row['reference']['name'])
                            removed = [edge for edge in baseline_metadata['dependencies']
                                if edge.get('reference', {}).get('identity') == body['identity']]
                            self.assertEqual(len(removed), 1)
                            baseline_metadata['dependencies'] = [edge for edge in baseline_metadata['dependencies'] if edge not in removed]
                            db.execute('update evidence_metadata set data=? where name=?',
                                (evidence._raw(baseline_metadata).decode(), baseline_row['reference']['name']))
                            changed.append('baseline-edge')
                    return reference

                class Host:
                    def acquire_d4_bookkeeping(self, facts, issue):
                        grant = issue(facts)
                        issued.append(grant)
                        return grant

                with changes._bootstrap_d4_host(Host(), issuer=changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
                    with patch.object(evidence, '_register_recovery_source', side_effect=after_real_registration):
                        result = changes._run_scoped(fixture.directory, request, 'apply', host=host)
                self.assertEqual(len(reached), 1)
                self.assertEqual(len(issued), 1)
                self.assertEqual(changed, [boundary])
                self.assertEqual(result['exit_code'], 2, result)
                reason = 'Current D4 Project authority changed outside its owned transition' if boundary == 'membership' else 'Recovery baseline differs from its exact owned overlay'
                self.assertTrue(any(reason in row['reason'] for row in result['blocked']), result)
                with evidence._context_database(context) as db:
                    self.assertEqual(evidence._canonical_plain(state._source_baseline(None, db, context=context)), before_baseline)
                    self.assertIsNone(state._metadata(db, reached[0]['name']))
                for action in request['manifest']['actions']:
                    self.assertEqual(changes._fingerprint(Path(action['owner']['root']), action['effects'][0]['path']), before_effects[action['id']])
                project_file = Path(request['manifest']['enrollment']['record']['project_root']) / changes.PROJECT_FILE
                if boundary != 'membership': self.assertEqual(project_file.read_bytes(), original_record)
                canonical_marker = Path(changes.LOCK_ROOT) / ('canonical-' + hashlib.sha256(
                    ('destination:' + str(project_file)).encode()).hexdigest() + '.lock')
                markers = [*changes._authority_locations([context.directory]), canonical_marker]
                self.assertTrue(all(changes._authority_owner(marker) is not None for marker in markers), markers)
                if boundary == 'membership':
                    self.assertEqual(source_path.read_bytes(), revoked_bytes[0])


if __name__ == '__main__':
    unittest.main()
