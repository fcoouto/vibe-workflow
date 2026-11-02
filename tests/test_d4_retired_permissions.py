"""Retired source permission uses genuine current closure, never a new raw owner."""
import json
import unittest

from runtime import evidence, state
from tests import test_canonical_composition


class _RollbackFaults(Exception):
    pass


class D4RetiredPermissionTests(unittest.TestCase):
    def retired_fixture(self):
        scenario = test_canonical_composition.CanonicalLifecycleTests('runTest')
        self.addCleanup(scenario.doCleanups)
        scenario.setUp()
        scenario.test_owner_observed_ordinary_closure_freezes_history_and_retired_identity()
        context = evidence._evidence_context(scenario.scope)
        owner = dict(authority=evidence._canonical_plain(context.authority_set[0]),
            scope_id=state._scope_id(scenario.scope),
            **{key: scenario.identity[key] for key in ('client', 'session_id', 'task_id')})
        with evidence._context_database(context) as db:
            candidates = []
            for name, digest in db.execute("select name,digest from evidence_metadata where class='compact'"):
                metadata = state._metadata(db, name)
                if metadata['owner'].get('task') != owner:
                    continue
                reference = dict(name=name, digest=digest)
                body = evidence._read(None, reference, context=context, db=db)
                if body.get('kind') == 'task-closure':
                    candidates.append((reference, metadata))
            self.assertEqual(len(candidates), 1)
            reference, metadata = candidates[0]
            subject = evidence._source_subject(context, db, metadata)
        source = dict(artifact=reference, owner=metadata['owner'],
            created_at=metadata['created_at'], pins=subject['pins'])
        return scenario, context, owner, metadata, subject, source

    def test_retained_owner_permission_never_admits_raw_task_or_missing_support(self):
        scenario, context, owner, metadata, subject, source = self.retired_fixture()
        with evidence._context_database(context) as db:
            marker = state._retired(db, 'task', {key: owner[key]
                for key in ('scope_id', 'client', 'session_id', 'task_id')})
            self.assertFalse(subject['live'])
            self.assertFalse(marker['evidence_expired'])
            self.assertEqual(marker['pins_digest'], evidence._digest(subject['pins']))
            self.assertGreaterEqual(subject['closed_at'], metadata['created_at'])
            self.assertEqual(evidence._source_current_permission(context, source, db=db)['projection'],
                subject['identity'])
            with self.assertRaisesRegex(state.StateError, 'Retired task evidence is unavailable'):
                state._task(db, owner['scope_id'], owner['client'], owner['session_id'], owner['task_id'])
        before = sorted(path.name for path in (context.directory / 'evidence').iterdir())
        with self.assertRaisesRegex(state.StateError, 'Retired task evidence is unavailable'):
            evidence._artifact(scenario.scope, dict(kind='fixture:new-publication-as-retired-task'),
                owner=evidence._ArtifactOwner(scenario.scope.project_id,
                    evidence._canonical_freeze(owner), evidence._canonical_freeze(subject['pins'])))
        self.assertEqual(sorted(path.name for path in (context.directory / 'evidence').iterdir()), before)

        # Faults roll back after checking the original marker and closure dependency.
        with self.assertRaises(_RollbackFaults):
            with evidence._context_database(context, write=True) as db:
                key = state._retired_identity('task', {name: owner[name]
                    for name in ('scope_id', 'client', 'session_id', 'task_id')})
                for fault in ('missing-marker', 'expired-marker', 'missing-closure'):
                    with self.subTest(fault=fault):
                        db.execute('savepoint missing_support')
                        if fault == 'missing-marker':
                            db.execute("delete from retired_identities where kind='task' and identity_digest=?", (key,))
                        elif fault == 'expired-marker':
                            row = db.execute("select data from retired_identities where kind='task' and identity_digest=?", (key,)).fetchone()
                            expired = json.loads(row[0]); expired.update(evidence_expired=True, outcome='unknown')
                            db.execute("update retired_identities set data=? where kind='task' and identity_digest=?", (json.dumps(expired), key))
                        else:
                            for name, digest in db.execute("select name,digest from evidence_metadata where class='compact'").fetchall():
                                body = evidence._read(None, dict(name=name, digest=digest), context=context, db=db)
                                if body.get('kind') in {'task-closure', 'retained-task-closure'}:
                                    db.execute('delete from evidence_metadata where name=?', (name,))
                        with self.assertRaises(state.StateError):
                            evidence._source_subject(context, db, metadata)
                        db.execute('rollback to missing_support'); db.execute('release missing_support')
                raise _RollbackFaults

    def foreign_permission_loss(self, change, message):
        scenario, context, _, _, _, source = self.retired_fixture()
        evidence._source_current_permission(context, source)
        path = scenario.scope.authority_path
        record = json.loads(path.read_bytes()); change(record)
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(state.StateError, message):
            evidence._source_current_permission(context, source)
        self.assertEqual(json.loads(path.read_bytes()), record)

    def test_retired_foreign_owner_excluded_membership_still_blocks(self):
        self.foreign_permission_loss(lambda row: row['membership'].update(exclude=['.']),
            'Current source bookkeeping permission/configuration changed')

    def test_retired_foreign_owner_changed_configuration_still_blocks(self):
        self.foreign_permission_loss(lambda row: row['repositories'][0]['checkouts'][0]['clients'][0]['configuration'].update(policy_digest='0' * 64),
            'Current source bookkeeping permission/configuration changed')

    def test_retired_foreign_owner_suspension_still_blocks(self):
        self.foreign_permission_loss(lambda row: row['repositories'][0]['checkouts'][0]['clients'][0]['activation'].update(status='suspended'),
            'Current source bookkeeping permission is suspended')
