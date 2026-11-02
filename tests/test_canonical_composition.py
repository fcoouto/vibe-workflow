"""Fresh canonical composition through genuine disposable D4 birth operations."""
import copy
import hashlib
import io
import json
import sys
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import changes, evidence, onboarding, review_storage, startup, state
from tests.fixture_canonical_birth import ScopedBirthFixture, CanonicalBirthFixture
from scripts.onboard_coordinator import run_verified_operation


class CanonicalCompositionTests(CanonicalBirthFixture):








    def test_fresh_birth_retains_original_baseline_and_pending_qualification(self):
        self.assertTrue(hasattr(startup, 'acquire_project_entry'), 'Canonical entry selector is required')
        prepared = self.prepare()
        f, request, decision, directory = prepared
        result = self.invoke(prepared)
        self.assertEqual(result['exit_code'], 2, result)
        self.assertFalse(result['qualified'])
        journal = changes._load_journal(f.directory)
        self.assertEqual(journal['state'], 'applied', result)
        certificate = journal['birth']['certificate']
        context = self.context(f)
        with evidence._context_database(context) as db:
            baseline = state._source_baseline(None, db, context=context)
            self.assertEqual(baseline['content']['birth_expectation']['intent_digest'], onboarding._digest(decision['creation']['intent']))
            self.assertEqual(db.execute('pragma user_version').fetchone()[0], 3)
            self.assertEqual(db.execute('select count(*) from checkouts').fetchone()[0], 1)
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0], 0)
            self.assertEqual(evidence._read_birth_handoff(context, db)['certificate'], certificate)
        self.assertNotEqual((f.repo / 'AGENTS.md').read_text(), 'product rules\nlegacy process\n')
        acquired = startup.acquire_canonical_project(type('Event', (), dict(client='codex', cwd=f.repo))(), state.PACKAGE)
        self.assertIsNone(acquired.resolution.scope)



    def test_canonical_task_states_milestones_interrupt_and_disabled_default(self):
        from runtime import workstreams
        from runtime.engine import RuntimeEvent
        fixture, acquisition, observation = self.accepted_birth()
        scope = startup._entry_scope(acquisition)
        identity = dict(client='codex',session_id='canonical-session',task_id='task',scope_ref='user:task')
        before = {str(p):p.read_bytes() for p in scope.runtime.state_dir.rglob('*') if p.is_file()}
        default = workstreams.control(scope,'status',dict(client='codex'))
        self.assertTrue(default['enabled'])
        self.assertEqual(default['revision'], 0)
        self.assertEqual(before, {str(p):p.read_bytes() for p in scope.runtime.state_dir.rglob('*') if p.is_file()})
        task = state.start_task(scope, identity | dict(mode='editable',decision_ref='user:accepted'))['task']
        self.assertEqual(task['state'], 'TRIAGE')
        self.assertEqual(task['pins']['schema_version'], 3)
        self.assertEqual(workstreams.control(scope,'status',dict(client='codex'))['revision'], 1)
        disabled = workstreams.control(scope,'disable',dict(client='codex',expected_revision=1))
        def update(status, **fields):
            nonlocal task
            task = state.update_task(scope,identity | dict(expected_revision=task['revision'],state=status,
                reason='Canonical public transition',reference='fixture:accepted') | fields)['task']
            return task
        update('READY',milestones=[dict(id='slice',weight=1,accepted=True,reference='fixture:milestone')])
        self.assertEqual(task['state'],'READY')
        self.assertIsNone(task['lifecycle']['safe_closed_at'])
        with self.assertRaisesRegex(state.StateError,'verification'):
            update('DONE')
        update('WORKING')
        event = RuntimeEvent('PreToolUse','codex','canonical-session',fixture.repo,'turn','call','apply_patch',False,'receipt')
        self.assertEqual(state.process_event(scope,event).action,'pass')
        state.process_event(scope,__import__('dataclasses').replace(event,kind='Interrupt'))
        task=state.task_status(scope,'codex','canonical-session')['task']
        self.assertEqual(task['state'],'PAUSED')
        self.assertEqual(task['milestones'][0]['id'],'slice')
        self.assertIsNone(task['lifecycle']['safe_closed_at'])
        state.start_task(scope,identity | dict(task_id='next',mode='editable',decision_ref='user:next',
            replaces=dict(task_id='task',expected_revision=task['revision'])))
        self.assertFalse(workstreams.control(scope,'status',dict(client='codex'))['enabled'])
        self.assertEqual(workstreams.control(scope,'status',dict(client='codex'))['revision'],disabled['revision'])

    def test_empty_prior_generation_authority_cannot_acquire_current_generation(self):
        import shutil
        with tempfile.TemporaryDirectory(prefix='vcw-prior-package-') as directory:
            source = Path(directory).resolve() / 'package'
            shutil.copytree(state.PACKAGE, source, ignore=shutil.ignore_patterns('.git','.superpowers','__pycache__'))
            with (source / 'runtime/engine.py').open('a') as stream:
                stream.write('\n# Separate prior-generation software fixture.\n')
            self._assert_empty_generation_rejected(source, 'tests.test_canonical_composition', 'CanonicalCompositionTests')

    def _assert_empty_generation_rejected(self, source, module, name):
        import subprocess
        program = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import importlib
from runtime import evidence, startup, state
scenario=getattr(importlib.import_module(sys.argv[2]),sys.argv[3])('runTest')
try:
    prepared=scenario.prepare(history=False)
    fixture=prepared[0]
    result=scenario.invoke(prepared)
    assert result['exit_code']==2 and not result['qualified'],result
    context=scenario.context(fixture)
    record=json.loads((fixture.repo/startup.PROJECT_FILE).read_bytes())
    repository=record['repositories'][0];checkout=repository['checkouts'][0]
    identity=startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(record),
        repository_id=repository['repository_id'],checkout_id=checkout['checkout_id'],client='codex'))
    with evidence._context_database(context) as db:
        baseline=startup._canonical_plain(state._source_baseline(None,db,context=context))
        assert db.execute('select count(*) from tasks').fetchone()[0]==0
    print(json.dumps(dict(identity=identity,baseline=baseline,roots=[str(fixture.base),str(context.directory.parent)])),flush=True)
    sys.stdin.readline()
finally:
    scenario.doCleanups()
"""
        child=subprocess.Popen([sys.executable,'-I','-B','-c',program,str(source),module,name],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            packet=json.loads(child.stdout.readline())
            identity=packet['identity']
            original_runtime=identity['configuration']['runtime_digest']
            self.assertEqual(packet['baseline']['content']['runtime_digest'],original_runtime)
            self.assertNotEqual(original_runtime,state.runtime_fingerprint())
            roots=[Path(path) for path in packet['roots']]
            def snapshot():
                return {str(path):(path.read_bytes(),path.stat().st_ino,path.stat().st_mtime_ns)
                    for root in roots for path in root.rglob('*') if path.is_file()}
            before=snapshot()
            attempted=copy.deepcopy(identity)
            attempted['configuration'].update(runtime_digest=state.runtime_fingerprint(),policy_digest=startup.policy_digest(state.PACKAGE))
            target=onboarding._authority_observation(Path(identity['project_root']),{'paths':0,'bytes':0},canonical_only=True)
            scope=startup.AcceptedScope(identity['project_id'],identity['repository_id'],identity['checkout_id'],
                Path(identity['project_root']),Path(identity['project_root'])/startup.PROJECT_FILE,
                startup._canonical_freeze(target['target']),1,Path(identity['repository_root']),Path(identity['primary_root']),
                Path(identity['git_dir']),Path(identity['git_common_dir']),startup._canonical_freeze(identity['bindings']),
                'codex',attempted['configuration']['policy_digest'],attempted['configuration']['runtime_digest'],
                startup.RuntimeSettings(3,Path(identity['configuration']['runtime']['state_dir'])),
                startup._canonical_freeze(attempted),startup._canonical_digest(attempted),
                startup._canonical_freeze(dict(kind='trusted-coordinator',reference='fixture:unapproved-successor-attempt',sha256='b'*64)))
            with tempfile.TemporaryDirectory(prefix='vcw-generation-guard-') as locks, \
                    patch.object(changes,'LOCK_ROOT',Path(locks).resolve()):
                with self.assertRaisesRegex(state.StateError,'generation'):
                    state.start_task(scope,dict(client='codex',session_id='attempt',task_id='attempt',scope_ref='fixture:attempt',
                        mode='editable',decision_ref='fixture:unapproved'))
            self.assertEqual(snapshot(),before)
        finally:
            child.communicate(input='stop\n',timeout=10)
            self.assertEqual(child.returncode,0)

    def test_missing_canonical_database_is_unavailable_without_recreation(self):
        fixture, acquisition, _ = self.accepted_birth()
        scope=startup._entry_scope(acquisition)
        database=scope.runtime.state_dir/'runtime.sqlite3'
        database.unlink()
        before={str(path):path.read_bytes() for path in scope.runtime.state_dir.rglob('*') if path.is_file()}
        with self.assertRaises(state.StateError):
            state.task_status(scope,'codex','canonical-session')
        with self.assertRaises(state.StateError):
            state.start_task(scope,dict(client='codex',session_id='canonical-session',task_id='task',scope_ref='user:task',
                mode='editable',decision_ref='user:accepted'))
        self.assertFalse(database.exists())
        self.assertEqual(before,{str(path):path.read_bytes() for path in scope.runtime.state_dir.rglob('*') if path.is_file()})

    def test_changed_canonical_activation_invalidates_acquired_scope(self):
        fixture, acquisition, _ = self.accepted_birth()
        scope=startup._entry_scope(acquisition)
        record=json.loads(scope.authority_path.read_bytes())
        record['repositories'][0]['checkouts'][0]['clients'][0]['activation']['status']='suspended'
        scope.authority_path.write_text(json.dumps(record))
        before={str(path):path.read_bytes() for path in scope.runtime.state_dir.rglob('*') if path.is_file()}
        with self.assertRaises(state.StateError):
            state.start_task(scope,dict(client='codex',session_id='canonical-session',task_id='task',scope_ref='user:task',
                mode='editable',decision_ref='user:accepted'))
        self.assertEqual(before,{str(path):path.read_bytes() for path in scope.runtime.state_dir.rglob('*') if path.is_file()})

    def test_canonical_command_entry_reaches_task_and_guarded_hook(self):
        import subprocess
        from tests.fixture_canonical_birth import save_test_acquisition
        from tests.test_runtime import RuntimeTests
        fixture,acquisition,observation=self.accepted_birth()
        driver=SimpleNamespace(base=fixture.base,test_acquisition=save_test_acquisition(
            fixture.base/'command-owner.json',fixture,observation))
        command=RuntimeTests.workflow_command(driver)
        identity=dict(client='codex',session_id='canonical-session',task_id='task',scope_ref='user:task')
        def control(action,request=None):
            argv=command+['task',action,'--cwd',str(fixture.repo)]
            if action=='status':argv+=['--client','codex','--session','canonical-session']
            result=subprocess.run(argv,cwd=fixture.repo,input=json.dumps(request or {}).encode(),capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(result.stderr,b'')
            return json.loads(result.stdout)
        task=control('start',identity|dict(mode='editable',decision_ref='user:accepted'))['task']
        self.assertEqual(task['state'],'TRIAGE')
        self.assertEqual(task['pins']['schema_version'],3)
        task=control('update',identity|dict(expected_revision=task['revision'],state='WORKING',reason='Start work',reference='user:accepted'))['task']
        self.assertEqual(control('status')['task'],task)
        payload=dict(hook_event_name='PreToolUse',cwd=str(fixture.repo),session_id='canonical-session',
            turn_id='turn',tool_use_id='call',tool_name='apply_patch',tool_input={})
        result=subprocess.run(command+['codex','PreToolUse'],cwd=fixture.repo,input=json.dumps(payload).encode(),capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stderr,b'')
        rendered=json.loads(result.stdout) if result.stdout else {}
        self.assertNotEqual(rendered.get('hookSpecificOutput',{}).get('permissionDecision'),'deny')


    def test_committed_birth_crash_resumes_without_new_baseline_or_ids(self):
        prepared=self.prepare();f,_,decision,directory=prepared
        def crash(stage):
            if stage=='birth-committed':raise RuntimeError('birth crash')
        with patch.object(changes,'_checkpoint',side_effect=crash), self.assertRaises(RuntimeError):self.invoke(prepared)
        journal=changes._load_journal(f.directory); allocation=copy.deepcopy(journal['birth']['allocation'])
        context=changes._recovery_context(allocation['authority'])
        with evidence._context_database(context) as db:original=evidence._canonical_plain(state._source_baseline(None,db,context=context))
        # Independently observe completed birth for the new recovery decision.
        rows=changes._birth_seed(None,decision['creation']['intent'])
        certificate=dict(version=1,intent=decision['creation']['intent'],allocation=allocation,
            baseline={key:original[key] for key in ('reference','content')},seed_digest=onboarding._digest(rows))
        recovery=copy.deepcopy(decision);recovery['operation']='resume'
        recovery['creation']['resume']=dict(intent_digest=journal['birth']['intent_digest'],allocation=allocation,
            baseline=certificate['baseline'],certificate_digest=onboarding._digest(certificate))
        result=run_verified_operation(recovery,f.directory,prepared[1]);self.assertEqual(result['exit_code'],2,result)
        self.assertEqual(changes._load_journal(f.directory)['birth']['allocation'],allocation)



    def test_unreceipted_and_uncommitted_orphans_are_not_reinitialized(self):
        for boundary in ('birth-unreceipted','birth-before-commit'):
            with self.subTest(boundary=boundary):
                prepared=self.prepare();f,request,decision,directory=prepared
                def crash(stage):
                    if stage==boundary:raise RuntimeError('orphan')
                with patch.object(changes,'_checkpoint',side_effect=crash),self.assertRaises(RuntimeError):self.invoke(prepared)
                before={str(path):path.read_bytes() for path in directory.rglob('*') if path.is_file()}
                recovery=copy.deepcopy(decision);recovery['operation']='resume'
                journal=changes._load_journal(f.directory)
                recovery['creation']['resume']=dict(intent_digest=journal['birth']['intent_digest'],allocation=journal['birth']['allocation'],baseline=None,certificate_digest=None)
                result=run_verified_operation(recovery,f.directory,request)
                self.assertNotEqual(result['exit_code'],0,result)
                self.assertEqual(before,{str(path):path.read_bytes() for path in directory.rglob('*') if path.is_file()})
                self.assertEqual((f.repo/'AGENTS.md').read_text(),'product rules\nlegacy process\n')



    def test_unknown_existing_database_cannot_be_reclassified_as_birth(self):
        prepared=self.prepare();f,_,_,directory=prepared
        directory.mkdir(mode=0o700);(directory/'runtime.sqlite3').write_bytes(b'')
        result=self.invoke(prepared);self.assertNotEqual(result['exit_code'],0,result)
        self.assertFalse((f.directory/'transaction.json').exists())
        self.assertEqual((directory/'runtime.sqlite3').read_bytes(),b'')



    def test_claimed_native_qualification_cannot_activate_a_fresh_birth(self):
        prepared=self.prepare();f,request,decision,directory=prepared
        record=request['manifest']['enrollment']['record'];repo=record['repositories'][0];checkout=repo['checkouts'][0];client=checkout['clients'][0]
        identity=onboarding._digest(startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(record),
            repository_id=repo['repository_id'],checkout_id=checkout['checkout_id'],client='codex')))
        ref=dict(reference='fixture:unobserved-claim',sha256='e'*64);source=state.PACKAGE/'hooks/codex.json'
        binding=dict(client='codex',surface='unobserved-fixture',version='unobserved',configuration_digest='f'*64,
            hook_owner=dict(root=str(state.PACKAGE),binding=evidence._binding(state.PACKAGE),source=str(source),
                sha256=__import__('hashlib').sha256(source.read_bytes()).hexdigest()),accepted_scope_digest=identity,receipt=ref)
        client['activation'].update(status='active',qualification='qualified',accepted_scope_digest=identity,
            transaction_id=request['manifest']['transaction_id'],evidence=[ref],qualification_binding=binding)
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        captured=self.decision(f,request,'apply',())
        with review_storage._capture_birth_history(record) as (history,reobserve):
            intent=changes._birth_intent(f.plan(),request['manifest'],history,plan_dir=f.directory);reobserve()
        captured['creation']=dict(version=1,intent=intent,resume=None)
        result=run_verified_operation(captured,f.directory,request)
        self.assertEqual(result['exit_code'],2,result);self.assertFalse(result['qualified'])
        self.assertTrue(any('configured enrollment only' in row['reason'] for row in result['blocked']),result)
        self.assertFalse(directory.exists());self.assertFalse((f.directory/'transaction.json').exists())



from tests import test_evidence as _evidence_fixtures


class CanonicalLifecycleTests(unittest.TestCase):
    git = _evidence_fixtures.EvidenceTests.git
    request = _evidence_fixtures.EvidenceTests.request
    plan = _evidence_fixtures.EvidenceTests.plan
    reconcile = _evidence_fixtures.EvidenceTests.reconcile
    check = _evidence_fixtures.EvidenceTests.check
    checked = _evidence_fixtures.EvidenceTests.checked
    verify = _evidence_fixtures.EvidenceTests.verify
    ready_proof_fixture = _evidence_fixtures.EvidenceTests.ready_proof_fixture

    def setUp(self):
        host = CanonicalCompositionTests('runTest')
        self.addCleanup(host.doCleanups)
        fixture, self.acquisition, _ = host.accepted_birth()
        self.scope = startup._entry_scope(self.acquisition)
        self.root, self.repo = fixture.base, fixture.repo
        (self.repo / 'app.py').write_text('value = 1\n')
        self.identity = dict(client='codex',session_id='parent',task_id='task',scope_ref='user:task')
        self.api = evidence
        state.start_task(self.scope,self.identity | dict(mode='editable',decision_ref='user:accepted'))

    def ready(self):
        from runtime.engine import RuntimeEvent
        for kind in ('PreToolUse','PostToolUse'):
            state.process_event(self.scope,RuntimeEvent(kind,'codex','parent',self.repo,turn_id='turn',
                tool_call_id='fixture-read',tool_name='read_file',receipt_fingerprint='a' * 64))
        self.ready_proof_fixture()
        verified = self.verify()
        self.assertTrue(verified['verified'],verified)
        self.assertEqual(verified['task']['state'],'DONE')
        return verified['task']

    def test_done_is_open_stop_does_not_close_and_reopening_preserves_age(self):
        from runtime.engine import RuntimeEvent
        ready = self.ready()
        self.assertIsNone(ready['lifecycle']['safe_closed_at'])
        self.assertEqual(state.process_event(self.scope,RuntimeEvent('Stop','codex','parent',self.repo,
            turn_id='turn',stop_hook_active=False)).action,'pass')
        current = state.task_status(self.scope,'codex','parent')['task']
        self.assertIsNone(current['lifecycle']['safe_closed_at'])
        reopened=state.update_task(self.scope,self.request(state='WORKING',reason='More authorized work',reference='user:reopen'))['task']
        self.assertEqual(reopened['state'],'WORKING')
        self.assertEqual(reopened['lifecycle']['created_at'],ready['lifecycle']['created_at'])

    def test_owner_observed_ordinary_closure_freezes_history_and_retired_identity(self):
        task = self.ready()
        context=evidence._evidence_context(self.scope)
        reference=evidence._canonical_plain(evidence._artifact_owner(self.scope,task).task)
        request=self.identity | dict(expected_revision=task['revision'],expected_pins=task['pins'])
        with self.assertRaises(state.StateError):
            evidence.close_task(self.scope,request,{})
        with self.assertRaises(state.StateError):
            evidence._observe_closure((context,),reference)
        with evidence._context_database(context) as db:
            bindings=state._ready_stop_bindings(context,db,task)
        verification=evidence._read(self.scope,task['evidence']['verification'])
        proof=dict(version=2,kind='stopped-writer-reconciliation',identity=self.identity,
            expected_revision=task['revision'],expected_pins=task['pins'],observed_turn_id=task['turn_id'],
            stopped=True,descendants_stopped=True,attempt_ids=[a['id'] for a in task['evidence']['attempts']],
            observation_ref='fixture:synthetic-independent-complete-stop',coordinator_ref='fixture:host-checked',
            candidate_digest=verification['candidate_digest'],verification_digest=task['evidence']['verification']['digest'],**bindings)
        raw=json.dumps(proof).encode();path=self.root/'complete-stop.json';path.write_bytes(raw);path.chmod(0o400)
        stopped=state._retain_ready_stop(self.scope,request | dict(stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())))['task']
        self.assertEqual(stopped['revision'],task['revision'])
        observations=evidence._observe_closure((context,),reference)
        closed=evidence.close_task(self.scope,request,observations)['task']
        self.assertEqual(closed['state'],'DONE')
        self.assertEqual(closed['pins'],task['pins'])
        self.assertEqual(closed['lifecycle']['created_at'],task['lifecycle']['created_at'])
        self.assertIsNotNone(closed['lifecycle']['safe_closed_at'])
        compact=evidence._read(self.scope,closed['lifecycle']['compact'])
        self.assertEqual(compact['outcome'],'DONE')
        self.assertTrue(compact['numerical'])
        self.assertTrue(compact['verifications'])
        with self.assertRaises(state.StateError):
            state.update_task(self.scope,self.request(state='WORKING',reason='Closed owner cannot reopen',reference='user:work'))
        retired=evidence._retire_closed_task(context,reference)
        self.assertTrue(retired['retired'])
        with evidence._context_database(context) as db:
            marker=state._retired(db,'task',{key:reference[key] for key in ('scope_id','client','session_id','task_id')})
            self.assertEqual(marker['outcome'],'DONE')
            self.assertEqual(marker['created_at'],task['lifecycle']['created_at'])
        with self.assertRaises(state.StateError):
            state.start_task(self.scope,self.identity | dict(mode='editable',decision_ref='user:accepted'))

    def test_same_byte_evidence_replacement_remains_unavailable(self):
        task=self.ready()
        reference=task['evidence']['verification']
        original=self.scope.runtime.state_dir/'evidence'/reference['name']
        replacement=self.root/'replacement.json'
        replacement.write_bytes(original.read_bytes());replacement.chmod(0o400);replacement.replace(original)
        with self.assertRaises(state.StateError):
            evidence._read(self.scope,reference)


class CanonicalLinkedLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.api = evidence
        self.members = []
        for _ in range(2):
            fixture = CanonicalLifecycleTests('runTest')
            self.addCleanup(fixture.doCleanups)
            fixture.setUp()
            self.members.append(fixture)
        self.selected = self.members[0]
        self.establish()


    def task(self, f):
        with state._database(f.scope) as db:
            return state._task(db, state._scope_id(f.scope), 'codex', 'parent')


    def reference(self, f):
        return self.api._canonical_plain(self.api._artifact_owner(f.scope, self.task(f)).task)


    def establish(self, delivery=None):
        review = dict(applicability='inapplicable', reason='Trivial fixed accepted unit command', independent=False)
        for index, f in enumerate(self.members):
            peers = [dict(task=self.reference(peer), delivery=delivery if peer is self.selected else None)
                     for peer in self.members if peer is not f]
            updates=dict(version=2, nontrivial=False, reviews=dict(general=review, security=review),
                linked=dict(acceptance=[dict(id='together', text='Accepted changes work together')],
                    delivery=delivery if f is self.selected else None, companions=peers))
            task=self.task(f)
            if 'evidence' in task:
                plan=self.api._read(f.scope,task['evidence']['plan']);plan.update(updates)
                self.api.plan_evidence(f.scope,f.request(plan=plan,change_decision_ref='fixture:approved-linked-revision'))
            else:
                f.plan(**updates)
            result = f.checked()
            self.assertEqual(result['attempt']['status'], 'passed')


    def retained(self, f, value):
        return dict(authority=self.reference(f)['authority'],
            reference=self.api._artifact(f.scope, value, owner=self.api._artifact_owner(f.scope, self.task(f))))


    def input_rows(self):
        rows=[]
        for f in self.members:
            task=self.task(f); plan=self.api._read(f.scope,task['evidence']['plan'])
            rows.append(dict(task=self.reference(f),pins=task['pins'],plan_digest=self.api._digest(plan),
                candidate_digest=self.api._digest(self.api.capture_candidate(f.scope,plan))))
        return sorted(rows,key=lambda row:self.api._raw(row['task']))


    def observations(self, *, delivery=None, delivery_status='unknown', shared_status='passed'):
        inputs=self.input_rows(); digest=self.api._digest(inputs)
        plan=self.api._read(self.selected.scope,self.task(self.selected)['evidence']['plan'])
        projection=self.api._linked_projection(self.reference(self.selected),plan)
        decision=self.retained(self.selected,dict(version=1,kind='linked-scope-decision',
            projection_digest=self.api._digest(projection),decision_ref='user:accepted',
            reference='fixture:independently checked complete user scope',observed_at=100))
        integration=self.retained(self.selected,dict(version=1,kind='linked-integration-observation',
            inputs_digest=digest,criteria=dict(together=shared_status),observed_at=101,
            reference='fixture:synthetic independently observed integration for exact versions'))
        deliveries=[]
        if delivery is not None:
            candidate=self.api.capture_candidate(self.selected.scope,plan)
            source=self.retained(self.selected,dict(version=1,kind='linked-delivery-observation',
                delivery=delivery,status=delivery_status,revision=candidate['head'],observed_at=102,
                reference='fixture:synthetic coordinator-verified provider observation'))
            deliveries=[dict(delivery=delivery,status=delivery_status,source=source,
                observed_at=102,revision=candidate['head'])]
        def observe(facts):
            self.assertEqual(facts['inputs_digest'],digest)
            return dict(scope_decision=decision,shared=[dict(id='together',status=shared_status,
                evidence=[integration],reference='fixture:semantic acceptance independently checked')],deliveries=deliveries)
        return dict(members=[dict(scope=f.scope,request=f.request(purpose='completion')) for f in self.members],observe=observe)


    def verify(self, observations=None):
        return self.api.verify_linked(self.selected.scope,self.selected.request(purpose='completion'),
            observations or self.observations())


    def test_fresh_linked_verification_retains_registered_foreign_provenance(self):
        result=self.verify()
        self.assertTrue(result['implementation_verified'],result)
        self.assertFalse(result['merge_authority'])
        self.assertEqual(result['task']['state'],'DONE')
        content=evidence._linked_source(result['verification'])
        self.assertEqual(len(content['members']),2)
        authorities={evidence._raw(row['task']['authority']) for row in content['members']}
        self.assertEqual(len(authorities),2)
        for fixture in self.members:
            task=self.task(fixture)
            self.assertIsNone(task['lifecycle']['safe_closed_at'])
            self.assertIn(task['state'],{'DONE','REVIEW'})
        self.assertTrue(evidence.evidence_status(self.selected.scope,'codex','parent')['evidence']['implementation_verified'])


    def test_peer_generation_change_during_publication_rejects_without_aggregate(self):
        from runtime.engine import RuntimeEvent
        observations=self.observations();original=self.api._artifact;changed=[];peer=self.members[1]
        def publish(scope,value,**kwargs):
            if value.get('kind')=='linked-verification' and not changed:
                changed.append(True)
                event=RuntimeEvent('PostToolUse','codex','parent',peer.repo,'fixture-turn','fixture-call','apply_patch',False,'fixture')
                state.process_event(peer.scope,event)
            return original(scope,value,**kwargs)
        with patch.object(self.api,'_artifact',side_effect=publish):
            with self.assertRaises(state.StateError):self.verify(observations)
        self.assertTrue(changed)
        self.assertNotIn('linked_verification',self.task(self.selected)['evidence'])



class CanonicalFixtureFoundationTests(CanonicalBirthFixture):
    def test_standalone_birth_acquires_only_with_explicit_software_owner(self):
        fixture,acquired,_=self.accepted_birth()
        scope=startup._entry_scope(acquired)
        self.assertEqual(scope.authority_path,scope.project_root/startup.PROJECT_FILE)
        self.assertEqual(scope.repository_root,fixture.repo)
        self.assertEqual(len(fixture.member_acquisitions),1)
        self.assertTrue(state.inspect_state(scope)['compatible'])

    def _assert_orientation_uses_project_authority(self,**topology):
        fixture,acquired,observation=self.accepted_birth(**topology)
        scope=startup._entry_scope(acquired)
        member_path=scope.repository_root/startup.PROJECT_FILE
        self.assertNotEqual(scope.authority_path,member_path)
        def snapshot():
            result={}
            for path in (scope.authority_path,scope.runtime.state_dir/'runtime.sqlite3'):
                info=path.stat()
                result[path]=(path.read_bytes(),info.st_dev,info.st_ino,info.st_mode,info.st_mtime_ns)
            return result
        before=snapshot()
        self.assertFalse(member_path.exists() or member_path.is_symlink())
        event=startup.StartupEvent('codex','canonical-session',fixture.repo,'startup')
        actual_acquire=startup.acquire_project_entry
        def qualified_entry(event,package):
            return actual_acquire(event,package,qualifier=lambda record,facts:observation)
        with patch.object(startup,'acquire_project_entry',side_effect=qualified_entry):
            context=startup.orient(event,state.PACKAGE)
        self.assertEqual(snapshot(),before)
        self.assertFalse(member_path.exists() or member_path.is_symlink())
        self.assertIn(f"accepted project configuration at {json.dumps(str(scope.authority_path))}.",context)
        self.assertNotIn(json.dumps(str(member_path)),context)

    def test_umbrella_orientation_uses_project_authority(self):
        self._assert_orientation_uses_project_authority(peer=True)

    def test_selected_linked_orientation_uses_project_authority(self):
        self._assert_orientation_uses_project_authority(linked=True)

    def test_selected_linked_birth_uses_declared_project_authority(self):
        fixture,acquired,_=self.accepted_birth(linked=True)
        scope=startup._entry_scope(acquired)
        self.assertNotEqual(scope.repository_root,scope.primary_root)
        self.assertEqual(scope.authority_path,scope.project_root/startup.PROJECT_FILE)
        self.assertNotEqual(scope.authority_path,scope.repository_root/startup.PROJECT_FILE)
        self.assertEqual(scope.repository_root,fixture.repo)
        self.assertTrue(state.inspect_state(scope)['compatible'])

    def test_same_project_members_share_store_with_distinct_accepted_identities(self):
        fixture,acquired,_=self.accepted_birth(peer=True)
        members=[startup._entry_scope(value) for value in fixture.member_acquisitions.values()]
        self.assertEqual(len(members),2)
        self.assertEqual(len({scope.project_id for scope in members}),1)
        self.assertEqual(len({scope.runtime.state_dir for scope in members}),1)
        self.assertEqual(len({scope.accepted_scope_digest for scope in members}),2)
        self.assertEqual(len({state._scope_id(scope) for scope in members}),2)
        for scope in members:self.assertTrue(state.inspect_state(scope)['compatible'])

    def test_configured_canonical_without_runtime_has_no_accepted_scope(self):
        fixture,acquired,observation=self.accepted_birth()
        scope=startup._entry_scope(acquired)
        record=json.loads(scope.authority_path.read_bytes())
        repository=record['repositories'][0];checkout=repository['checkouts'][0];entry=checkout['clients'][0]
        entry['configuration']['runtime']=None
        projection=startup.canonical_scope_projection(startup.decode_project_record(record),
            repository_id=repository['repository_id'],checkout_id=checkout['checkout_id'],client='codex')
        digest=startup._canonical_digest(startup._canonical_plain(projection))
        entry['activation']['accepted_scope_digest']=digest
        entry['activation']['qualification_binding']['accepted_scope_digest']=digest
        observation['binding']=copy.deepcopy(entry['activation']['qualification_binding'])
        scope.authority_path.write_text(json.dumps(record))
        result=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=fixture.repo),state.PACKAGE,
            qualifier=lambda record,facts:observation)
        self.assertEqual(result.resolution.status,'configured',result.resolution)
        self.assertEqual(result.resolution.reason,'runtime_location_unobserved')
        self.assertIsNone(result.resolution.scope)

    def test_native_review_observation_is_valid_before_collection(self):
        from tests.test_evidence import EvidenceTests
        fixture=EvidenceTests('runTest');self.addCleanup(fixture.doCleanups);fixture.setUp()
        fixture.plan();fixture.checked();result=fixture.review()
        task=state.task_status(fixture.scope,'codex','parent')['task']
        reviews=[attempt for attempt in task['evidence']['attempts'] if attempt['kind']=='review']
        self.assertEqual(len(reviews),1)
        self.assertEqual(reviews[0]['status'],'assessed')
        self.assertEqual(result['attempt']['id'],reviews[0]['id'])
