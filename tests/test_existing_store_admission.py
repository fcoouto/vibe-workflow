"""Existing-store admission uses genuine D4 births/updates; native ownership is synthetic."""
from contextlib import closing
import copy
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from runtime import changes, evidence, onboarding, review_storage, startup, state
from scripts.onboard_coordinator import run_verified_operation
from tests.fixture_canonical_birth import CanonicalBirthFixture, ScopedBirthFixture


class ExistingStoreAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.owner=CanonicalBirthFixture('runTest')
        self.addCleanup(self.owner.doCleanups)
        if self._testMethodName=='test_empty_original_generation_store_cannot_admit_current_subject':return
        qualified=self._testMethodName!='test_same_journal_resume_and_rollback_preserve_original_history'
        self.prepared,self.original,self.context,self.history=self.owner.prepare_common_git_update(qualified=qualified)
        self.fixture,self.request,self.decision,self.directory=self.prepared
        self.member=self.request['manifest']['enrollment']['affected'][0]
        self.key=tuple(self.member[name] for name in ('repository_id','checkout_id','client'))
        self.identity=dict(client='codex',session_id='new-member-session',task_id='new-member-task',scope_ref='user:member')

    def invoke(self, *, decision=None, operation='apply'):
        selected=copy.deepcopy(self.decision if decision is None else decision)
        selected['operation']=operation
        return run_verified_operation(selected,self.fixture.directory,copy.deepcopy(self.request))

    def inventory(self, *roots):
        return {str(path):(path.read_bytes() if path.is_file() else None,path.stat().st_dev,
            path.stat().st_ino,path.stat().st_mode,path.stat().st_mtime_ns if path.is_file() else None)
            for root in roots for path in root.rglob('*')}

    def protected(self):
        def observed(path):
            info=path.stat()
            return path.read_bytes(),info.st_dev,info.st_ino,info.st_mode,info.st_mtime_ns
        return self.inventory(self.context.directory),observed(self.history['authority_path']),[
            observed(Path(action['owner']['root'])/effect['path'])
            for action in self.request['manifest']['actions'] for effect in action['effects']]

    def assert_refused(self, result, message):
        self.assertNotEqual(result.get('state'),'applied',result)
        self.assertIn(result['exit_code'],(1,2),result)
        self.assertTrue(any(message in row['reason'] for row in result['blocked']),result)

    def assert_original_history(self):
        self.assertEqual(state._authority_identity(self.context.database),self.history['authority'])
        with evidence._context_database(self.context) as db:
            baseline=state._source_baseline(None,db,context=self.context)
            self.assertEqual({key:evidence._canonical_plain(baseline[key]) for key in ('reference','content')},self.history['baseline'])
            self.assertEqual(evidence._canonical_plain(evidence._read_birth_handoff(self.context,db)['certificate']),self.history['certificate'])

    def new_scope_id(self):
        return onboarding._digest([3,self.request['manifest']['enrollment']['record']['project_id'],
            self.member['repository_id'],self.member['checkout_id']])

    def rebind_destination(self, context):
        entry=changes._scoped_entries(self.request['manifest']['enrollment']['record'])[self.key][2]
        entry['configuration']['runtime']=dict(schema_version=3,state_dir=str(context.directory))
        self.request['approval']['manifest_digest']=onboarding._digest(self.request['manifest'])
        self.decision=self.owner.decision(self.fixture,self.request,'apply',(self.context,context))

    def test_real_update_reacquires_both_members_and_task_api_registers_new_checkout(self):
        with evidence._context_database(self.context) as db:
            self.assertIsNone(db.execute('select 1 from checkouts where scope_id=?',(self.new_scope_id(),)).fetchone())
        self.owner.assert_applied_configured(self.prepared,self.invoke())
        self.assert_original_history()
        with evidence._context_database(self.context) as db:
            self.assertIsNone(db.execute('select 1 from checkouts where scope_id=?',(self.new_scope_id(),)).fetchone())
        selected=changes._scoped_entries(self.request['manifest']['enrollment']['record'])[self.key][1]
        unqualified=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=Path(selected['root'])),state.PACKAGE)
        self.assertEqual(unqualified.resolution.status,'configured')
        self.assertIsNone(unqualified.resolution.scope)
        fixture,_,_=self.owner.qualify_members(self.fixture,self.history['authority_path'])
        members=[startup._entry_scope(value) for value in fixture.member_acquisitions.values()]
        joined=next(member for member in members if member.checkout_id==self.member['checkout_id'])
        prior=next(member for member in members if member.checkout_id!=joined.checkout_id)
        self.assertEqual(joined.project_id,prior.project_id)
        self.assertEqual(joined.repository_id,prior.repository_id)
        self.assertEqual(joined.git_common_dir,prior.git_common_dir)
        self.assertEqual(joined.runtime.state_dir,prior.runtime.state_dir)
        task=state.start_task(joined,self.identity|dict(mode='editable',decision_ref='user:accepted'))['task']
        self.assertEqual(task['pins']['schema_version'],3)
        self.assertEqual(task['accepted_identity'],startup._canonical_plain(joined.accepted_identity))
        self.assertIsNone(state.task_status(prior,'codex',self.identity['session_id'])['task'])
        with state._database(joined) as db:
            self.assertIsNotNone(db.execute('select 1 from checkouts where scope_id=?',(state._scope_id(joined),)).fetchone())
        self.assert_original_history()

    def test_omitted_new_subject_grant_cannot_publish(self):
        denied=copy.deepcopy(self.decision)
        denied['bookkeeping_scope']['subjects']=[row for row in denied['bookkeeping_scope']['subjects']
            if row['checkout_id']!=self.member['checkout_id']]
        before=self.protected()
        self.assert_refused(self.invoke(decision=denied),'independently verified coordinator decision')
        self.assertEqual(before,self.protected())

    def test_omitted_prior_witness_grant_cannot_publish(self):
        denied=copy.deepcopy(self.decision)
        denied['bookkeeping_scope']['subjects']=[row for row in denied['bookkeeping_scope']['subjects']
            if row['checkout_id']==self.member['checkout_id']]
        before=self.protected()
        self.assert_refused(self.invoke(decision=denied),'independently verified coordinator decision')
        self.assertEqual(before,self.protected())

    def test_omitted_store_authority_grant_cannot_publish(self):
        denied=copy.deepcopy(self.decision);denied['bookkeeping_scope']['authorities']=[]
        before=self.protected()
        self.assert_refused(self.invoke(decision=denied),'independently verified coordinator decision')
        self.assertEqual(before,self.protected())

    def test_absent_destination_is_not_existing_store_admission(self):
        absent=self.fixture.base/'unprepared-store'
        entry=changes._scoped_entries(self.request['manifest']['enrollment']['record'])[self.key][2]
        entry['configuration']['runtime']=dict(schema_version=3,state_dir=str(absent))
        self.request['approval']['manifest_digest']=onboarding._digest(self.request['manifest'])
        self.decision=self.owner.decision(self.fixture,self.request,'apply',(self.context,))
        before=self.protected()
        self.assert_refused(self.invoke(),'Complete existing canonical runtime authorities')
        self.assertFalse(absent.exists())
        self.assertEqual(before,self.protected())

    def test_foreign_store_absent_from_prior_ownership_is_refused(self):
        other=CanonicalBirthFixture('runTest');self.addCleanup(other.doCleanups)
        fixture,_,_=other.accepted_birth();foreign=other.context(fixture)
        self.rebind_destination(foreign)
        before=self.protected(),self.inventory(foreign.directory)
        self.assert_refused(self.invoke(),'No prior canonical member owns')
        self.assertEqual(before,(self.protected(),self.inventory(foreign.directory)))

    def test_replaced_physical_store_cannot_inherit_admission(self):
        database=self.context.database
        retained=self.fixture.base/'original-runtime.sqlite3';database.rename(retained)
        shutil.copy2(retained,database)
        before=self.inventory(self.context.directory),retained.read_bytes(),self.history['authority_path'].read_bytes()
        self.assert_refused(self.invoke(),'New subject destination lacks its original same-Project source baseline')
        self.assertEqual(before,(self.inventory(self.context.directory),retained.read_bytes(),self.history['authority_path'].read_bytes()))

    def test_missing_original_baseline_body_blocks_without_repair(self):
        path=self.context.directory/'evidence'/self.history['baseline']['reference']['name']
        retained=self.fixture.base/'retained-baseline.json';path.rename(retained)
        before=self.inventory(self.context.directory),retained.read_bytes(),self.history['authority_path'].read_bytes()
        self.assert_refused(self.invoke(),'Complete existing canonical runtime authorities')
        self.assertEqual(before,(self.inventory(self.context.directory),retained.read_bytes(),self.history['authority_path'].read_bytes()))

    def test_corrupt_prior_physical_witness_cannot_admit(self):
        # Corrupt one real born row for rejection; no positive row is manufactured or seeded.
        with closing(sqlite3.connect(self.context.database)) as db,db:
            scope_id,raw=db.execute('select scope_id,physical_identity from checkouts').fetchone()
            physical=json.loads(raw);physical['bindings']['root']['inode']+=1
            db.execute('update checkouts set physical_identity=? where scope_id=?',(json.dumps(physical),scope_id))
        before=self.protected()
        self.assert_refused(self.invoke(),'Taskless D4 source has no exact current or owned-transition bookkeeping permission')
        self.assertEqual(before,self.protected())

    def test_existing_unsupported_prior_subject_does_not_gain_absent_subject_exception(self):
        # Deliberately invalid prior configurations exercise the predicate, not public positive birth.
        for configuration in (None,dict(runtime=None),dict(runtime=dict(schema_version=2,state_dir=str(self.directory)))):
            with self.subTest(configuration=configuration):
                plan=copy.deepcopy(self.fixture.plan())
                _,prior=changes._scoped_prior(plan)
                selected=copy.deepcopy(changes._scoped_entries(self.request['manifest']['enrollment']['record'])[self.key][1])
                selected['clients'][0]['configuration']=configuration
                prior['repositories'][0]['checkouts'].append(selected)
                locations=changes._scoped_runtime_locations([self.request['manifest']['enrollment']['record']],{self.key})
                before=self.protected()
                with changes._authority_guards([self.directory]),self.assertRaisesRegex(changes.Pending,'preparation is required for an affected subject'):
                    changes._public_existing_authorities(locations,(self.context,),plan,self.request['manifest'])
                self.assertEqual(before,self.protected())

    def test_live_common_git_writer_blocks_new_subject(self):
        scope=startup._entry_scope(self.original)
        state.start_task(scope,dict(client='codex',session_id='prior-writer',task_id='prior-task',scope_ref='user:writer',
            mode='editable',decision_ref='user:accepted'))
        before=self.protected()
        self.assert_refused(self.invoke(),'writer')
        self.assertEqual(before,self.protected())

    def test_uncertain_common_git_assignment_retains_writer_ownership_after_cancel(self):
        from runtime import delegation
        scope=startup._entry_scope(self.original)
        identity=dict(client='codex',session_id='prior-writer',task_id='prior-task',scope_ref='user:writer')
        task=state.start_task(scope,identity|dict(mode='editable',decision_ref='user:accepted'))['task']
        reservation=delegation.reserve_coordinator(scope,identity|dict(expected_revision=task['revision'],dispatch_id='held-common-git',
            claims=[dict(kind='git',key=str(scope.git_common_dir),mode='write')],operation=dict(id='held-common-git',kind='coordinator-work'),
            reason='Actual uncompleted writer reservation',ownership_ref='user:accepted',stop_conditions=['Stop after fixture observation']))
        task=state.task_status(scope,'codex',identity['session_id'])['task']
        state.update_task(scope,identity|dict(expected_revision=task['revision'],state='CANCELLED',reason='Cancel intent retains owner',reference='user:cancel'))
        before=self.protected()
        self.assert_refused(self.invoke(),'assignment remains unreconciled')
        self.assertEqual(before,self.protected())
        with state._database(scope) as db:
            record=next(row for row in delegation._ledger(scope,db) if row['id']==reservation['attempt_id'])
            self.assertNotEqual(record['data']['status'],'settled')

    def test_same_journal_resume_and_rollback_preserve_original_history(self):
        self.owner.assert_applied_configured(self.prepared,self.invoke())
        journal=(self.fixture.directory/'transaction.json')
        identity=changes._load_journal(self.fixture.directory)['transaction_id']
        self.owner.assert_applied_configured(self.prepared,self.invoke(operation='resume'))
        self.assertEqual(changes._load_journal(self.fixture.directory)['transaction_id'],identity)
        self.assert_original_history()
        result=self.invoke(operation='rollback')
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(result['state'],'rolled_back',result)
        self.assertEqual(changes._load_journal(self.fixture.directory)['transaction_id'],identity)
        self.assertTrue(journal.is_file())
        self.assertEqual(self.history['authority_path'].read_bytes(),self.history['original_record'])
        for action in self.request['manifest']['actions']:
            for effect in action['effects']:
                self.assertEqual(changes._fingerprint(Path(action['owner']['root']),effect['path']),effect['before'])
        self.assert_original_history()

    def test_empty_original_generation_store_cannot_admit_current_subject(self):
        temporary=tempfile.TemporaryDirectory(prefix='vcw-admission-generation-');self.addCleanup(temporary.cleanup)
        root=Path(temporary.name).resolve();package=root/'prior-package'
        shutil.copytree(state.PACKAGE,package,ignore=shutil.ignore_patterns('.git','.superpowers','__pycache__'))
        with (package/'runtime/engine.py').open('a') as stream:stream.write('\n# Distinct original generation for admission refusal.\n')
        program="""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tests.fixture_canonical_birth import CanonicalBirthFixture
from runtime import evidence,state
owner=CanonicalBirthFixture('runTest')
try:
 f,acquired,_=owner.accepted_birth();context=owner.context(f)
 with evidence._context_database(context) as db:
  assert db.execute('select count(*) from tasks').fetchone()[0]==0
  baseline=state._source_baseline(None,db,context=context)
 print(json.dumps(dict(root=str(f.repo),authority=evidence._canonical_plain(context.authority_set[0]),runtime=baseline['content']['runtime_digest'])),flush=True)
 sys.stdin.readline()
finally:owner.doCleanups()
"""
        child=subprocess.Popen([sys.executable,'-I','-B','-c',program,str(package)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def close_child():
            try:_,errors=child.communicate(input='stop\n',timeout=10)
            except subprocess.TimeoutExpired:
                child.kill();_,errors=child.communicate(timeout=5)
            self.assertEqual(child.returncode,0,errors[-2000:])
        self.addCleanup(close_child)
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout,selectors.EVENT_READ)
            self.assertTrue(ready.select(20),'Original-generation child did not produce its actual birth')
            raw=child.stdout.readline();self.assertTrue(raw,'Original-generation birth failed')
        packet=json.loads(raw)
        self.assertNotEqual(packet['runtime'],state.runtime_fingerprint())
        machine=patch.object(review_storage,'_machine_config_path',return_value=root/'machine'/'machine.json')
        machine.start();self.addCleanup(machine.stop)
        self.assertTrue(review_storage.prepare_storage(root/'private')['ok'])
        fixture=ScopedBirthFixture('runTest');self.addCleanup(fixture.doCleanups);fixture.setUp()
        fixture.repo=Path(packet['root'])
        request=fixture.scoped_request(linked=True)
        directory=Path(packet['authority']['directory'])
        for repository in request['manifest']['enrollment']['record']['repositories']:
            for checkout in repository['checkouts']:
                for entry in checkout['clients']:entry['configuration']['runtime']=dict(schema_version=3,state_dir=str(directory))
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        context=changes._recovery_context(packet['authority'])
        decision=self.owner.decision(fixture,request,'apply',(context,))
        pointer=Path(packet['root'])/startup.PROJECT_FILE
        before=self.inventory(directory),pointer.read_bytes()
        result=run_verified_operation(decision,fixture.directory,request)
        self.assert_refused(result,'runtime generation differs')
        self.assertEqual(before,(self.inventory(directory),pointer.read_bytes()))
