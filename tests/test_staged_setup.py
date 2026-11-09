"""Disposable software fixtures; no native qualification or primary activation."""

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import changes, doctor, setup_observation as setup, startup, state
from scripts import onboard_coordinator
from tests.fixture_canonical_birth import CanonicalBirthFixture
from tests.test_setup_source import SetupSourceFixture
from tests import test_unenrolled_checkout as unenrolled


class StagedAssessmentTests(unittest.TestCase):
    def assessment(self):
        proof=dict(reference='fixture:independent-assessment',sha256='a'*64)
        return dict(cases=[dict(id=key,status='pending',evidence=[]) for key in setup.BEHAVIOR_CASES],
            requirements=[dict(id='catalog-'+str(index),status='pending',evidence=[]) for index in range(44)],
            general_review=[proof],security_review=[proof],semantic_assessment=dict(complete=True,
                catalog_complete=True,conflicts_checked=True,instructions_checked=True,source_provenance_checked=True))

    def test_pending_catalog_kept_without_claiming_managed_case(self):
        assessment=self.assessment()
        assessment['cases'][0].update(status='verified',evidence=assessment['general_review'])
        retained=setup._assessment(assessment,staged=True)
        self.assertEqual(retained,assessment)
        self.assertEqual(len(retained['requirements']),44)
        self.assertEqual([row['id'] for row in retained['cases'] if row['status']=='verified'],['startup-context'])
        smaller=copy.deepcopy(assessment);smaller['requirements']=smaller['requirements'][:3]
        self.assertEqual(len(setup._assessment(smaller,staged=True)['requirements']),3)

    def test_all_cases_and_evidence_are_required_without_caller_completion_flag(self):
        for mutation in ('drop-case','verified-without-proof','invent-completeness','inapplicable-without-reason'):
            candidate=self.assessment()
            if mutation=='drop-case':candidate['cases'].pop()
            elif mutation=='verified-without-proof':candidate['cases'][0]['status']='verified'
            elif mutation=='invent-completeness':candidate['complete']=True
            else:candidate['requirements'][0]['status']='inapplicable'
            with self.subTest(mutation=mutation),self.assertRaises(startup.StartupError):
                setup._assessment(candidate,staged=True)

    def test_successor_retains_original_evidence_and_pending_catalog(self):
        assessment=self.assessment();proof=assessment['general_review'][0]
        assessment['cases'][0].update(status='verified',evidence=[proof])
        previous=dict(version=2,assessment=copy.deepcopy(assessment))
        successor=dict(version=2,assessment=copy.deepcopy(assessment))
        successor['assessment']['cases'][1].update(status='verified',evidence=[proof])
        setup._monotonic_coverage(previous,successor)
        for mutation in ('drop-requirement','erase-proof','regress-verified'):
            changed=copy.deepcopy(successor)
            if mutation=='drop-requirement':changed['assessment']['requirements'].pop()
            elif mutation=='erase-proof':changed['assessment']['cases'][0]['evidence']=[]
            else:changed['assessment']['cases'][0]['status']='pending'
            with self.subTest(mutation=mutation),self.assertRaises(startup.StartupError):
                setup._monotonic_coverage(previous,changed)

    def test_serialized_decision_cannot_issue_frontend_or_allocation_authority(self):
        with self.assertRaises(startup.StartupError):
            setup.qualification_attempt_projection(dict(mode='qualification'),case_id='startup-context')

    def test_raw_metadata_cannot_be_retained_as_a_cli_fragment(self):
        reference=dict(reference='fixture:original-attempt',sha256='a'*64)
        payload=dict(binding=dict(accepted_scope_digest='a'*64,attempt_ref=reference,transaction_id='capture'),
            package_root='/tmp/fake-plugin',native={},codex_home='/tmp/fake-home',hooks=None,
            layers=[dict(config=dict(unrelated_secret='fixture-secret-sentinel'))],requirements=None,
            observations={},bootstrap=[reference],pending_reason='native_metadata_pending')
        capture=dict(version=1,origin=dict(transaction_id='capture',action_id='cli',attempt_id='original'),witness=payload,
            launch=dict(command_digest='a'*64,cwd='/tmp/fake-checkout',codex_home='/tmp/fake-home',configuration_overrides_present=False))
        with self.assertRaisesRegex(startup.StartupError,'minimal native CLI fragment'):
            setup._capture_shape(capture)


class OwnedSetupCommandTests(unittest.TestCase):
    # Children here finish or are stopped by the observer long before this; tight budgets made
    # loaded runs flaky. test_owned_command_deadline_stops_silent_child proves enforcement.
    BUDGET = 60

    def setUp(self):
        # Command-owner markers live under LOCK_ROOT; never use the real home or another test's value.
        locks=tempfile.TemporaryDirectory(prefix='owned-setup-locks-');self.addCleanup(locks.cleanup)
        lock_root=patch.object(changes,'LOCK_ROOT',Path(locks.name).resolve()/'locks')
        lock_root.start();self.addCleanup(lock_root.stop)

    def allocation(self, operation, seconds=BUDGET):
        return startup._canonical_freeze(dict(operations=[operation],limits=dict(deadline=time.time()+seconds)))

    def command_fixture(self, code, timeout=BUDGET):
        fixture=unenrolled.UnenrolledCheckoutTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        private=tempfile.TemporaryDirectory(prefix='owned-setup-lifecycle-');self.addCleanup(private.cleanup)
        directory=Path(private.name).resolve();directory.chmod(0o700)
        changes._load_journal(directory)
        root=fixture.repo;(root/'effect.txt').write_text('original')
        owner=dict(kind='repository',root=str(root),git_dir=str(root/'.git'),repository_id='repository',checkout_id='primary')
        effect=dict(path='effect.txt',before=changes._fingerprint(root,'effect.txt'),
            after=dict(sha256=hashlib.sha256(b'completed').hexdigest(),mode=0o644),target=changes._scoped_target(root,'effect.txt'))
        proof=dict(reference='fixture:qualified-local-effects',sha256='a'*64)
        action=dict(id='observe',owner=owner,recovery='file-preimages',resources=['checkout'],preconditions=[],effects=[effect],
            execution=dict(argv=[sys.executable,'-I','-B','-c',code],cwd=str(root),
                cwd_target=changes.d3._physical_target(root,root,None,dict(paths=0,bytes=0),directory=True),
                timeout_seconds=timeout,qualification=proof,idempotency=proof))
        journal=dict(transaction_id='lifecycle-fixture',manifest=dict(actions=[action]),receipts={},
            plan=dict(assessments=dict(project=None,repositories=[dict(owner=owner,decisions=[],coverage=[])])),
            qualification_scope=dict(attempts=[dict(task=None,command_action='observe')]))
        descriptor=os.open(root/'.git',os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,descriptor)
        return directory,action,journal,descriptor

    def test_metadata_eof_and_cli_drain_preserve_real_exit_zero(self):
        for metadata in (True,False):
            with self.subTest(metadata=metadata):
                code=("import sys;from pathlib import Path;"+
                    ("sys.stdin.read();" if metadata else "print('header',flush=True);")+
                    "sys.stdout.write('x'*131072);sys.stdout.flush();Path('effect.txt').write_text('completed')")
                directory,action,journal,descriptor=self.command_fixture(code);seen=[]
                allocation=self.allocation('observe-setup-metadata' if metadata else 'observe-cli-basic')
                class Observer:
                    def setup_command_allocation(self,action,journal):
                        return allocation
                    def observe_setup_command(self,process,path,journal,action,attempt):
                        seen.append(process)
                        if not metadata:
                            if not process.read(process.deadline).startswith(b'header\n'):raise AssertionError('Actual child header missing')
                with self.assertRaisesRegex(changes.Pending,'descendant lifecycle proof'):
                    changes._scoped_apply_action(directory,journal,action,dict(kind='command',decision_ids=[]),None,descriptor,observer_owner=Observer())
                receipt=journal['receipts']['observe']
                self.assertEqual(receipt['returncode'],0);self.assertEqual(seen[0].process.poll(),0)
                self.assertGreaterEqual(seen[0].bytes_read,131072)
                self.assertFalse(receipt['command']['stopped'])
                self.assertIsNotNone(receipt['files'][0]['owned_target'])
                self.assertIsNotNone(changes._command_owner(action['owner']['git_dir']))

    def test_observation_error_becomes_pending_after_child_cleanup_and_receipt_save(self):
        directory,action,journal,descriptor=self.command_fixture('import time;time.sleep(30)');seen=[]
        allocation=self.allocation('observe-cli-basic')
        class Observer:
            def setup_command_allocation(self,action,journal):
                return allocation
            def observe_setup_command(self,process,path,journal,action,attempt):
                seen.append(process)
                raise doctor.ObservationError('fixture-sensitive-native-error')
        with self.assertRaisesRegex(changes.Pending,'Owned setup command observation is unavailable'):
            changes._scoped_apply_action(directory,journal,action,dict(kind='command',decision_ids=[]),None,descriptor,observer_owner=Observer())
        receipt=journal['receipts']['observe']
        self.assertIsNotNone(seen[0].process.poll());self.assertNotEqual(receipt['returncode'],0)
        self.assertFalse(receipt['command']['stopped'])
        self.assertEqual(json.loads((directory/'transaction.json').read_bytes())['receipts']['observe']['returncode'],receipt['returncode'])
        self.assertIsNotNone(changes._command_owner(action['owner']['git_dir']))

    def test_owned_command_deadline_stops_silent_child(self):
        # Either bound (command timeout or allocation deadline) stops a child that never finishes.
        for bound in ('timeout','allocation'):
            with self.subTest(bound=bound):
                directory,action,journal,descriptor=self.command_fixture('import time;time.sleep(30)',
                    timeout=1 if bound=='timeout' else self.BUDGET);seen=[]
                allocation=self.allocation('observe-cli-basic',1 if bound=='allocation' else self.BUDGET)
                class Observer:
                    def setup_command_allocation(self,action,journal):
                        return allocation
                    def observe_setup_command(self,process,path,journal,action,attempt):
                        seen.append(process)
                started=time.monotonic()
                with self.assertRaisesRegex(changes.Pending,'Owned setup command observation is unavailable'):
                    changes._scoped_apply_action(directory,journal,action,dict(kind='command',decision_ids=[]),None,descriptor,observer_owner=Observer())
                self.assertLess(time.monotonic()-started,self.BUDGET/2)
                receipt=journal['receipts']['observe']
                self.assertEqual(len(seen),1);self.assertIsNotNone(seen[0].process.poll())
                self.assertNotEqual(receipt['returncode'],0);self.assertFalse(receipt['command']['stopped'])
                self.assertEqual(json.loads((directory/'transaction.json').read_bytes())['receipts']['observe']['returncode'],receipt['returncode'])

    def test_bootstrap_selector_and_verified_plan_cannot_replace_actual_command_claim(self):
        birth=CanonicalBirthFixture('runTest');self.addCleanup(birth.doCleanups)
        fixture,request,decision,_=birth.prepare()
        record=request['manifest']['enrollment']['record'];key=next(iter(changes._scoped_entries(record)))
        identity=startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(record),
            repository_id=key[0],checkout_id=key[1],client=key[2]))
        reference=dict(reference='fixture:original-bootstrap-allocation',sha256='b'*64)
        decision['qualification_scope']=dict(decision_reference=decision['decision_reference'],target=dict(
            accepted_scope_digest=startup._canonical_digest(identity),repository_root=str(fixture.repo),
            git_common_dir=identity['git_common_dir'],disposable=True),attempts=[dict(reference=reference,
                task=None,native_sessions=[],operations=['observe-cli-basic'],capability_keys=['startup-context'],
                command_action=request['manifest']['actions'][0]['id'],
                limits=dict(deadline=time.time()+120,max_attempts=1,max_resource_units=1),stop_conditions=['Stop after one header'])])
        owner=onboard_coordinator.CoordinatorOperationOwner(decision)
        with self.assertRaises(changes.Pending):owner.acquire_setup_attempt(identity,reference)

    def test_failed_observer_reaps_exact_child_and_cannot_relaunch_consumed_allocation(self):
        fixture=unenrolled.UnenrolledCheckoutTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        private=tempfile.TemporaryDirectory(prefix='owned-setup-command-');self.addCleanup(private.cleanup)
        directory=Path(private.name).resolve();directory.chmod(0o700)
        changes._load_journal(directory)
        root=fixture.repo;target=root/'effect.txt';target.write_text('original')
        owner=dict(kind='repository',root=str(root),git_dir=str(root/'.git'),repository_id='repository',checkout_id='primary')
        effect=dict(path='effect.txt',before=changes._fingerprint(root,'effect.txt'),
            after=dict(sha256='f'*64,mode=0o644),target=changes._scoped_target(root,'effect.txt'))
        proof=dict(reference='fixture:qualified-command-effects',sha256='a'*64)
        argv=[sys.executable,'-I','-B','-c','import json,time;print(json.dumps({"type":"thread.started","thread_id":"fixture-thread"}),flush=True);time.sleep(30)']
        action=dict(id='observe',owner=owner,recovery='file-preimages',resources=['checkout'],preconditions=[],effects=[effect],
            execution=dict(argv=argv,cwd=str(root),cwd_target=changes.d3._physical_target(root,root,None,dict(paths=0,bytes=0),directory=True),
                timeout_seconds=self.BUDGET,qualification=proof,idempotency=proof))
        journal=dict(transaction_id='command-fixture',manifest=dict(actions=[action]),receipts={},
            plan=dict(assessments=dict(project=None,repositories=[dict(owner=owner,decisions=[],coverage=[])])),
            qualification_scope=dict(attempts=[dict(task=None,command_action='observe')]))
        seen=[];allocation=self.allocation('observe-cli-basic')
        class Observer:
            def setup_command_allocation(self,action,journal):
                return allocation
            def observe_setup_command(self,process,path,journal,action,attempt):
                seen.append(process)
                self_header=json.loads(process.read(process.deadline).decode())
                if self_header.get('thread_id')!='fixture-thread':raise AssertionError('Actual child header missing')
                if process.process.stdin is not None:raise AssertionError('CLI stdin must remain closed')
                raise startup.StartupError('fixture observer failed after actual child ownership')
        descriptor=os.open(root/'.git',os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,descriptor)
        with self.assertRaisesRegex(startup.StartupError,'fixture observer failed'):
            changes._scoped_apply_action(directory,journal,action,dict(kind='command',decision_ids=[]),None,descriptor,observer_owner=Observer())
        receipt=journal['receipts']['observe']
        self.assertEqual(len(seen),1);self.assertIsNotNone(seen[0].process.poll())
        self.assertFalse(receipt['command']['stopped']);self.assertIsNotNone(receipt['returncode'])
        self.assertIsNotNone(changes._command_owner(owner['git_dir']))
        observation=dict(command_reconciliation=[dict(id='observe',attempt_id=receipt['command']['attempt_id'],stopped=True,evidence=[proof])])
        with self.assertRaisesRegex(changes.Pending,'allocation is consumed'):
            changes._scoped_apply_action(directory,journal,action,dict(kind='command',decision_ids=[]),observation,descriptor,observer_owner=Observer())
        self.assertEqual(len(seen),1);self.assertTrue(receipt['command']['stopped'])
        with self.assertRaisesRegex(changes.Pending,'allocation is consumed'):
            changes._scoped_apply_action(directory,journal,action,dict(kind='command',decision_ids=[]),observation,descriptor)
        self.assertEqual(len(seen),1)


class StagedJournalTests(SetupSourceFixture):
    def staged(self):
        birth=CanonicalBirthFixture('runTest');self.addCleanup(birth.doCleanups)
        fixture,acquired,_=birth.accepted_birth()
        identity=startup._canonical_plain(acquired.resolution.scope.accepted_identity)
        self.package=state.PACKAGE
        journal=copy.deepcopy(changes._load_journal(fixture.directory))
        required=journal['manifest']['enrollment']['qualification'][0]['requirements']
        old=self.observe(identity,journal['transaction_id'],required)
        content=setup._issued(old)
        assessment=copy.deepcopy(content['assessment'])
        for row in [*assessment['cases'],*assessment['requirements']]:
            row.update(status='pending',evidence=[])
        assessment['cases'][0].update(status='verified',evidence=assessment['general_review'])
        content.update(version=2,predecessor=None,assessment=assessment)
        issued=setup._SetupObservation(startup._canonical_freeze(content),setup._ISSUER)
        record=journal['manifest']['enrollment']['record']
        key=next(iter(changes._scoped_entries(record)));entry=changes._scoped_entries(record)[key][2]
        binding=setup.receipt_binding(issued,fixture.directory)
        entry['activation'].update(status='active',qualification='qualified',transaction_id=journal['transaction_id'],
            accepted_scope_digest=binding['accepted_scope_digest'],qualification_binding=binding,evidence=[binding['receipt']])
        journal['manifest_digest']=changes.d3._digest(journal['manifest'])
        task=dict(client='codex',session_id='setup',task_id='guard-probe')
        proof=assessment['general_review'][0]
        self.attempt_ref=dict(reference='fixture:approved-guard-probe',sha256='c'*64)
        authorization=dict(decision_reference=proof,target=dict(accepted_scope_digest=binding['accepted_scope_digest'],
            repository_root=str(fixture.repo),git_common_dir=identity['git_common_dir'],disposable=True),attempts=[dict(
                reference=self.attempt_ref,task=task,native_sessions=['setup'],operations=['task-start'],
                capability_keys=['pre-tool-enforcement'],limits=dict(deadline=time.time()+120,max_attempts=1,max_resource_units=1),
                stop_conditions=['Stop after one observed guard outcome'])])
        # This fixture injects an explicit fake host owner; it is not native qualification.
        journal['setup_receipts']={}
        setup.persist_setup(fixture.directory,journal,[issued],qualification_scope=authorization)
        journal['state']='applied';journal['blocked']=[]
        raw=json.dumps(record,sort_keys=True,ensure_ascii=True).encode()+b'\n'
        acquired.authority_path.write_bytes(raw)
        target=changes._scoped_target(Path(record['project_root']),str(changes.PROJECT_FILE))
        for post in journal['enrollment']['postimages']:
            if post['phase']=='final':
                post.update(raw_sha256=hashlib.sha256(raw).hexdigest(),target=target,
                    after=dict(sha256=hashlib.sha256(raw).hexdigest(),mode=0o600))
        for member in journal['enrollment']['members']:member.update(status='active',blocked=None)
        changes._save(fixture.directory,journal)
        return fixture,journal,task

    def test_staged_journal_round_trip_retains_original_source_and_authorization(self):
        fixture,journal,_=self.staged()
        before=(fixture.directory/'transaction.json').read_bytes()
        retained=changes._load_journal(fixture.directory)
        self.assertEqual(retained,journal)
        self.assertEqual(retained['qualification_scope']['attempts'][0]['reference'],self.attempt_ref)
        self.assertEqual((fixture.directory/'transaction.json').read_bytes(),before)

    def test_disposable_startup_allows_only_exact_qualification_not_ordinary_pending_use(self):
        fixture,journal,task=self.staged()
        with patch.dict(os.environ,HOME=str(self.home.parent),CODEX_HOME=str(self.home)):
            acquired=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=fixture.repo),state.PACKAGE)
            self.assertEqual(acquired.resolution.status,'active',acquired.resolution)
            scope=acquired.resolution.scope
            self.assertFalse(scope.qualification['complete'])
            self.assertEqual(tuple(scope.qualification['verified']),('startup-context',))
            with self.assertRaises(startup.StartupError):
                setup.require_capability(scope,['pre-tool-enforcement'],task=task,operation='task-start')
            admitted=setup.require_capability(scope,['pre-tool-enforcement'],task=task,operation='task-start',attempt_ref=self.attempt_ref)
            self.assertEqual(admitted.mode,'qualification')
            self.assertEqual(startup._canonical_plain(admitted.attempt_ref),self.attempt_ref)
            for mutation in ('operation','task','scope'):
                with self.subTest(mutation=mutation),self.assertRaises(startup.StartupError):
                    setup.require_capability(replace(scope,repository_root=scope.primary_root.parent) if mutation=='scope' else scope,
                        ['pre-tool-enforcement'],task=task|dict(task_id='ordinary') if mutation=='task' else task,
                        operation='task-ready' if mutation=='operation' else 'task-start',attempt_ref=self.attempt_ref)
            with self.assertRaises(startup.StartupError):
                setup.require_capability(scope,['full-onboarding'],task=task,operation='task-ready',attempt_ref=self.attempt_ref)
            for state_name in ('PAUSED','CANCELLED','WAITING'):
                with self.subTest(state=state_name),self.assertRaises(startup.StartupError):
                    setup.require_capability(scope,['pre-tool-enforcement'],task=task|dict(state=state_name),
                        operation='task-start',attempt_ref=self.attempt_ref)
        self.assertEqual(len(journal['setup_receipts']),1)

    def test_partial_scope_needs_original_disposable_authority_and_cannot_hide_catalog(self):
        fixture,journal,task=self.staged()
        content=next(iter(journal['setup_receipts'].values()))
        with self.assertRaises(startup.StartupError):
            setup._staged_scope(content,{key:value for key,value in journal.items() if key!='qualification_scope'})
        altered=copy.deepcopy(journal);altered['qualification_scope']['target']['accepted_scope_digest']='f'*64
        with self.assertRaises(startup.StartupError):setup._staged_scope(content,altered)
        changed=copy.deepcopy(content);changed['assessment']['requirements']=[]
        issued=setup._SetupObservation(startup._canonical_freeze(changed),setup._ISSUER)
        with self.assertRaises(startup.StartupError):setup.persist_setup(fixture.directory,journal,[issued])

    def test_expired_attempt_and_changed_observable_configuration_cannot_admit(self):
        fixture,journal,task=self.staged()
        with patch.dict(os.environ,HOME=str(self.home.parent),CODEX_HOME=str(self.home)):
            scope=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=fixture.repo),state.PACKAGE).resolution.scope
            journal['qualification_scope']['attempts'][0]['limits']['deadline']=time.time()-1
            changes._save(fixture.directory,journal)
            with self.assertRaises(startup.StartupError):
                setup.require_capability(scope,['pre-tool-enforcement'],task=task,operation='task-start',attempt_ref=self.attempt_ref)
            journal['qualification_scope']['attempts'][0]['limits']['deadline']=time.time()+120
            changes._save(fixture.directory,journal)
            self.config.write_text(self.config.read_text().replace('enabled = true','enabled = false',1))
            with self.assertRaises(startup.StartupError):
                setup.require_capability(scope,['pre-tool-enforcement'],task=task,operation='task-start',attempt_ref=self.attempt_ref)

    def test_successor_cannot_rebind_original_attempt_limits_or_identity(self):
        fixture,journal,task=self.staged()
        content=next(iter(journal['setup_receipts'].values()))
        reference=next(iter(changes._scoped_entries(journal['manifest']['enrollment']['record']).values()))[2]['activation']['qualification_binding']['receipt']
        successor=copy.deepcopy(content);successor['predecessor']=reference
        for mutation in ('limits','task','operations'):
            changed=copy.deepcopy(journal)
            attempt=changed['qualification_scope']['attempts'][0]
            if mutation=='limits':attempt['limits']['max_attempts']=2
            elif mutation=='task':attempt['task']['task_id']='replacement'
            else:attempt['operations']=['operation-coordinator']
            with self.subTest(mutation=mutation),self.assertRaises(startup.StartupError):
                setup._original_attempt(successor,changed,self.attempt_ref)


if __name__=='__main__':
    unittest.main()
