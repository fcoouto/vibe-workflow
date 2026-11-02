"""Original-generation data is rejection/history proof, never a current fixture."""
from contextlib import closing
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from runtime import changes, delegation, evidence, startup, state, workstreams
from runtime.engine import RuntimeEvent


class LegacyCurrentnessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='vcw-legacy-negative-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.repo=self.root/'repository';self.repo.mkdir()
        subprocess.run(['git','-c','core.hooksPath='+os.devnull,'init','-q',str(self.repo)],check=True,capture_output=True,timeout=5)
        self.identity=dict(client='codex',session_id='old-session',task_id='old-task',scope_ref='fixture:original-M')

    def legacy(self, kind, *, current_pins=False, scope=None, publish=True):
        scope=scope or startup.ProjectScope('original-M',self.repo,self.repo/'.git',startup.policy_digest(state.PACKAGE),
            startup.RuntimeSettings(2,self.root/kind))
        record=dict(schema_version=1,project_id=scope.project_id,repository_root=str(scope.repository_root),
            git_common_dir=str(scope.git_common_dir),clients=['codex'],policy_digest=scope.policy_digest,
            runtime=dict(schema_version=2,state_dir=str(scope.runtime.state_dir)))
        if publish:
            pointer=self.repo/startup.PROJECT_FILE;pointer.parent.mkdir(exist_ok=True);pointer.write_text(json.dumps(record))
        if kind!='absent':
            scope.runtime.state_dir.mkdir(mode=0o700)
            path=scope.runtime.state_dir/'runtime.sqlite3'
            descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(descriptor)
            with closing(sqlite3.connect(path)) as db,db:
                for declaration in state.TABLE_SQL.values():db.execute(declaration)
                db.execute('pragma application_id='+str(state.APPLICATION_ID));db.execute('pragma user_version=2')
                db.execute('insert into checkouts values(?,?,?,?,0)',(state._scope_id(scope),scope.project_id,str(self.repo),str(scope.git_common_dir)))
                if kind in {'cancelled','live','forged'}:
                    task=self.identity|dict(mode='editable',decision_ref='fixture:original-decision',
                        state='CANCELLED' if kind=='cancelled' else 'WORKING',revision=1,
                        pins=dict(schema_version=2,policy_digest=scope.policy_digest,
                            runtime_digest=state.runtime_fingerprint() if current_pins else 'f'*64),
                        reason='Original package record',reference='fixture:original',conditions=[],turn_id=None,turn_uncertain=True)
                    if kind=='cancelled':
                        proof=dict(version=1,kind='stopped-writer-reconciliation',identity=self.identity,
                            expected_revision=1,expected_pins=task['pins'],observed_turn_id=None,stopped=True,
                            descendants_stopped=True,attempt_ids=[],observation_ref='fixture:retained-original',
                            coordinator_ref='fixture:original-reader-only')
                        raw=json.dumps(proof).encode();folder=scope.runtime.state_dir/'evidence';folder.mkdir(mode=0o700)
                        name='a'*32+'.json';artifact=folder/name;artifact.write_bytes(raw);artifact.chmod(0o400)
                        task['revision']=2
                        task['retirement']=dict(proof_digest=hashlib.sha256(raw).hexdigest(),
                            proof=dict(name=name,digest=hashlib.sha256(raw).hexdigest()),observation_ref=proof['observation_ref'],
                            coordinator_ref=proof['coordinator_ref'],previous_revision=1)
                    db.execute('insert into tasks values(?,?,?,?,?)' ,(state._scope_id(scope),'codex','old-session','old-task',json.dumps(task)))
                    db.execute('insert into bindings values(?,?,?,?)',(state._scope_id(scope),'codex','old-session','old-task'))
        return scope

    def snapshot(self):
        return {str(path):(path.read_bytes() if path.is_file() else None, path.stat().st_ino,
                path.stat().st_mode, path.stat().st_mtime_ns if path.is_file() else None)
            for path in self.root.rglob('*')}

    def test_original_m_matrix_has_no_current_entry_state_or_guidance_authority(self):
        for kind in ('absent','empty','cancelled','live','forged'):
            with self.subTest(kind=kind):
                scope=self.legacy(kind,current_pins=kind=='forged')
                before=self.snapshot()
                acquisition=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=self.repo),state.PACKAGE)
                self.assertIsNone(acquisition.resolution.scope)
                self.assertNotEqual(acquisition.resolution.status,'outside')
                request=self.identity|dict(mode='editable',decision_ref='fixture:new-attempt')
                for operation in (lambda:state.start_task(scope,request),lambda:state.task_status(scope,'codex','old-session'),
                        lambda:evidence.revalidate_ready(scope,'codex','old-session'),
                        lambda:workstreams.control(scope,'status',dict(client='codex')),
                        lambda:evidence._artifact(scope,dict(kind='fixture:legacy-publication-attempt')),
                        lambda:evidence._folder(scope,create=True),
                        lambda:delegation.claim_guidance(scope,dict(client_surface='codex-desktop',
                            applicability=dict(client_version='1',capability_fingerprint='a'*64),trigger=dict(kind='routine',id='test')))):
                    with self.assertRaises(state.StateError):operation()
                self.assertFalse(state.inspect_state(scope)['compatible'])
                from runtime.doctor import inspect_runtime
                report=inspect_runtime(scope,'/unavailable-native')
                self.assertEqual(report['state']['status'],'incompatible')
                self.assertIn('original pinned',report['state']['error'])
                event=RuntimeEvent('PreToolUse','codex','old-session',self.repo,'turn','call','apply_patch',False,'receipt')
                self.assertEqual(state.process_event(scope,event).action,'deny')
                command=[sys.executable,'-I','-B',str(state.PACKAGE/'scripts/workflow.py'),'task','start','--cwd',str(self.repo)]
                result=subprocess.run(command,input=json.dumps(request).encode(),capture_output=True,timeout=5)
                self.assertNotEqual(result.returncode,0,result.stdout)
                self.assertNotIn(b'Traceback',result.stdout+result.stderr)
                self.assertEqual(before,self.snapshot())

    def test_explicit_historical_open_is_read_only_and_cannot_be_escalated(self):
        scope=self.legacy('live');before=self.snapshot()
        with state._database(scope,historical=True) as db:
            self.assertEqual(db.execute('pragma query_only').fetchone()[0],1)
            task=state._task(db,state._scope_id(scope),'codex','old-session',historical=True)
            self.assertEqual(task['pins']['runtime_digest'],'f'*64)
            with self.assertRaises(sqlite3.OperationalError):db.execute('delete from tasks')
        for flags in (dict(write=True),dict(initialize=True),dict(guard_existing=True)):
            with self.assertRaises(state.StateError):
                with state._database(scope,historical=True,**flags):self.fail('Historical inspection escalated')
        self.assertEqual(before,self.snapshot())
        with self.assertRaises(changes.Pending):changes._writers({}, {},dict(task_reconciliation=True),scope=scope)
        self.assertEqual(before,self.snapshot())

    def test_low_level_legacy_creation_and_current_looking_save_are_refused(self):
        scope=self.legacy('forged',current_pins=True);before=self.snapshot()
        with self.assertRaises(state.StateError):state._initialize(self.root/'new-store/runtime.sqlite3',version=2)
        with closing(sqlite3.connect(scope.runtime.state_dir/'runtime.sqlite3')) as db:
            task=state._task(db,state._scope_id(scope),'codex','old-session',historical=True)
            with self.assertRaises(state.StateError):state._save(db,state._scope_id(scope),task)
        self.assertEqual(before,self.snapshot())


    def test_unsupported_retained_assignment_stays_protected_after_cancellation(self):
        scope=self.legacy('cancelled')
        with closing(sqlite3.connect(scope.runtime.state_dir/'runtime.sqlite3')) as db,db:
            db.execute('insert into assignments values(?,?,?,?,?,?,?)',('a'*32,state._scope_id(scope),
                'codex','old-session','old-task','original-dispatch','{}'))
        before=self.snapshot()
        with self.assertRaises(changes.Pending):changes._writers({}, {},dict(task_reconciliation=True),scope=scope)
        self.assertEqual(before,self.snapshot())
