"""Uniform task states preserve evidence and edit boundaries."""
import unittest
from contextlib import closing
from tests import test_state


class CanonicalTaskStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_state.StateTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_new_task_triage_then_ready_then_working(self):
        self.assertEqual(self.fixture.start()['task']['state'],'TRIAGE')
        for status in ('READY','WORKING','REVIEW','WORKING'):
            self.assertEqual(self.fixture.update(state=status)['task']['state'],status)

    def test_done_cannot_be_declared_by_task_update(self):
        self.fixture.start()
        with self.assertRaises(ValueError):self.fixture.update(state='DONE')

    def test_waiting_blocked_and_paused_require_explicit_resolution(self):
        self.fixture.start()
        for status in ('WAITING','BLOCKED','PAUSED'):
            self.fixture.update(state=status)
            with self.assertRaises(ValueError): self.fixture.update(state='WORKING')
            self.assertEqual(self.fixture.update(state='WORKING',resolution_ref='user:resume')['task']['state'],'WORKING')

    def test_cancelled_is_terminal(self):
        self.fixture.start()
        self.fixture.update(state='CANCELLED')
        with self.assertRaises(ValueError): self.fixture.update(state='WORKING',resolution_ref='user:resume')

    def test_milestones_are_validated_and_update_with_task_revision(self):
        initial=self.fixture.start()['task']
        values=[dict(id='design',weight=2,accepted=True,reference='user:design-approved'),
                dict(id='delivery',weight=3,accepted=False,reference='plan:delivery')]
        result=self.fixture.update(milestones=values)['task']
        self.assertEqual(result['milestones'],values)
        self.assertEqual(result['revision'],initial['revision']+1)
        for invalid in ([values[0],values[0]], [values[0]|dict(weight=0)],
                        [values[0]|dict(weight=True)], [values[0]|dict(accepted='yes')],
                        [values[0]|dict(reference='')], [values[0]|dict(id='bad\n')],
                        [values[0]|dict(weight=1000001)], [values[0]|dict(extra='unsupported')],
                        [values[0]|dict(id=str(i)) for i in range(65)]):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                self.fixture.update(milestones=invalid)
        self.assertEqual(self.fixture.status()['task']['milestones'],values)



    def test_missing_or_null_state_prevents_new_session_in_corrupt_authority(self):
        import json
        import sqlite3
        self.fixture.start()
        path = self.fixture.scope.runtime.state_dir / 'runtime.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            original = json.loads(db.execute('SELECT data FROM tasks').fetchone()[0])
        for value in ('missing', None):
            corrupt = dict(original)
            if value == 'missing':
                del corrupt['state']
            else:
                corrupt['state'] = None
            raw = json.dumps(corrupt)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('UPDATE tasks SET data=?', (raw,))
            with self.assertRaises(ValueError):
                self.fixture.start(session_id='other-session', task_id='other-task')
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute('SELECT data FROM tasks').fetchall(), [(raw,)])

    def test_retained_incompatible_pins_prevent_cross_session_admission(self):
        import json
        import sqlite3
        self.fixture.start()
        path = self.fixture.scope.runtime.state_dir / 'runtime.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            original = json.loads(db.execute('SELECT data FROM tasks').fetchone()[0])
        for pins in (original['pins'] | dict(runtime_digest='f'*64), original['pins'] | dict(schema_version=1),
                     original['pins'] | dict(policy_digest='older-policy')):
            old = original | dict(pins=pins)
            raw = json.dumps(old)
            with closing(sqlite3.connect(path)) as db, db:db.execute('UPDATE tasks SET data=?', (raw,))
            with self.assertRaises(ValueError):
                self.fixture.start(session_id='other-session', task_id='other-task')
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT data FROM tasks').fetchall(), [(raw,)])

    def test_wait_states_block_verification_and_model_free_dispatch_without_side_effects(self):
        from tests.test_evidence import EvidenceTests
        from runtime import state, delegation
        fixture = EvidenceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plan()
        for status in ('WAITING', 'BLOCKED', 'PAUSED'):
            state.update_task(fixture.scope, fixture.request(state=status, reason='Waiting for resolution', reference='user:hold'))
            files = {str(p):p.read_bytes() for p in fixture.scope.runtime.state_dir.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ValueError, 'Resolve the task status'):
                fixture.api.verify_completion(fixture.scope, fixture.request(purpose='completion'))
            operation = fixture.request(dispatch_id='blocked-dispatch', claims=[dict(kind='path',key=str(fixture.repo),mode='read')],
                operation=dict(id='blocked-dispatch',kind='coordinator-work'),reason='Observe only',
                ownership_ref='user:scope',stop_conditions=['Return after observation'])
            with self.assertRaisesRegex(ValueError, 'Task cannot reserve'):
                delegation._reserve_operation(fixture.scope, operation, role='coordinator')
            self.assertEqual({str(p):p.read_bytes() for p in fixture.scope.runtime.state_dir.rglob('*') if p.is_file()}, files)
            state.update_task(fixture.scope, fixture.request(state='WORKING',reason='Resume accepted task',
                reference='user:resume',resolution_ref='user:resolved'))




class HistoricalTaskStatusTests(unittest.TestCase):
    """Original schema-two data is retained history, never canonical setup."""
    def setUp(self):
        from tests.test_legacy_currentness import LegacyCurrentnessTests
        self.fixture=LegacyCurrentnessTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def alter(self, scope, update):
        import json,sqlite3
        path=scope.runtime.state_dir/'runtime.sqlite3'
        with closing(sqlite3.connect(path)) as db,db:
            task=json.loads(db.execute('select data from tasks').fetchone()[0])
            update(task);raw=json.dumps(task)
            db.execute('update tasks set data=?',(raw,))
        return raw

    def test_legacy_task_remains_unchanged_and_requires_original_package(self):
        from runtime import state
        scope=self.fixture.legacy('live')
        raw=self.alter(scope,lambda task:task.update(state='ready'))
        before=self.fixture.snapshot()
        with self.assertRaisesRegex(ValueError,'original pinned'):state.task_status(scope,'codex','old-session')
        with self.assertRaisesRegex(ValueError,'original pinned'):
            state.start_task(scope,self.fixture.identity|dict(session_id='new-session',task_id='new-task',mode='editable',decision_ref='user:new'))
        self.assertEqual(before,self.fixture.snapshot())

    def test_version_specific_historical_reader_does_not_authorize_relabel_or_repin(self):
        from runtime import state
        scope=self.fixture.legacy('live')
        self.alter(scope,lambda task:task.update(state='cancelled'))
        before=self.fixture.snapshot()
        with state._database(scope,historical=True) as db:
            task=state._task(db,state._scope_id(scope),'codex','old-session',historical=True)
            self.assertEqual(task['state'],'cancelled')
            self.assertEqual(task['pins']['runtime_digest'],'f'*64)
            with self.assertRaises(ValueError):state._compatible(scope,task)
            with self.assertRaises(ValueError):state._save(db,state._scope_id(scope),task)
            relabeled=task|dict(state='CANCELLED',pins=task['pins']|dict(runtime_digest=state.runtime_fingerprint()))
            with self.assertRaises(ValueError):state._save(db,state._scope_id(scope),relabeled)
        self.assertEqual(before,self.fixture.snapshot())

    def test_schema_two_d4_reads_original_cancelled_stop_without_changing_authority(self):
        from runtime import changes
        scope=self.fixture.legacy('cancelled')
        self.alter(scope,lambda task:task.update(state='cancelled'))
        before=self.fixture.snapshot()
        changes._writers({}, {},dict(task_reconciliation=True),scope=scope)
        self.assertEqual(before,self.fixture.snapshot())

    def test_historical_cancelled_flag_does_not_clear_unresolved_check_claims(self):
        from runtime import changes,state
        scope=self.fixture.legacy('live')
        reference=dict(name='a'*32+'.json',digest='b'*64)
        def unresolved(task):
            task.update(state='cancelled',evidence=dict(version=1,plan=reference,candidate=reference,
                attempts=[dict(id='c'*32,kind='check',status='uncertain',candidate_digest='d'*64,
                    plan_digest='e'*64,candidate=reference)]))
        self.alter(scope,unresolved)
        before=self.fixture.snapshot()
        with state._database(scope,historical=True) as db:
            task=state._task(db,state._scope_id(scope),'codex','old-session',historical=True)
            self.assertEqual(task['evidence']['attempts'][0]['status'],'uncertain')
        with self.assertRaises(changes.Pending):changes._writers({}, {},dict(task_reconciliation=True),scope=scope)
        self.assertEqual(before,self.fixture.snapshot())
