"""Fake owned transport and disposable local inputs; never native qualification."""

import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import changes, doctor, onboarding, startup, state
from scripts import onboard_coordinator
from tests.fixture_canonical_birth import CanonicalBirthFixture
from tests import test_unenrolled_checkout as unenrolled


class OtherClientQualificationTests(unittest.TestCase):
    def test_claude_scoped_qualification_needs_a_retained_owner_receipt_not_a_supplied_observation(self):
        birth=CanonicalBirthFixture('runTest');self.addCleanup(birth.doCleanups)
        fixture,acquired,qualification=birth.accepted_birth()
        captured=[]
        startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=fixture.repo),state.PACKAGE,
            qualifier=lambda record,facts: captured.append(startup._canonical_plain(facts)) or qualification)
        facts={key:captured[0][key] for key in ('authority','observation','client','installed')}
        record=json.loads(acquired.authority_path.read_bytes())
        old_key=next(iter(changes._scoped_entries(record)))
        repository,checkout,entry=changes._scoped_entries(record)[old_key]
        entry['client']=facts['client']='claude-code'
        facts['installed']['clients']=['claude-code']
        qualification=copy.deepcopy(qualification)
        qualification['current']['client']=qualification['binding']['client']='claude-code'
        digest=startup._canonical_digest(startup._canonical_plain(startup.canonical_scope_projection(
            startup.decode_project_record(record),repository_id=old_key[0],checkout_id=old_key[1],client='claude-code')))
        qualification['binding']['accepted_scope_digest']=entry['activation']['accepted_scope_digest']=digest
        entry['activation']['qualification_binding']=copy.deepcopy(qualification['binding'])
        facts['observation']['qualification']=qualification
        acquired.authority_path.write_text(json.dumps(record))
        facts['authority']=dict(path=str(acquired.authority_path),target=changes._scoped_target(
            Path(record['project_root']),str(changes.PROJECT_FILE)))
        journal=copy.deepcopy(changes._load_journal(fixture.directory))
        manifest=journal['manifest'];manifest['enrollment']['record']=record
        for rows in ('affected','qualification'):
            for row in manifest['enrollment'][rows]:row['client']='claude-code'
        rule=manifest['enrollment']['qualification'][0]
        proof=qualification['receipt']
        observation=dict(input_digest=changes._scoped_input_digest(manifest),qualification=[dict(
            repository_id=repository['repository_id'],checkout_id=checkout['checkout_id'],client='claude-code',
            requirements=[dict(id=name,evidence=[proof]) for name in rule['requirements']],
            general_review=[proof],security_review=[proof],scope_observations=facts)])
        before=copy.deepcopy(observation)
        # Claude now follows the Codex rule: a coordinator-supplied observation is never qualification.
        for published in (False,True):
            with self.subTest(published=published):
                with self.assertRaisesRegex(changes.Pending,'retained owner setup qualification is unavailable'):
                    changes._scoped_qualification(journal,observation,record,published=published)
        self.assertEqual(observation,before)
        self.assertEqual(set(qualification['current']),{'client','surface','version','configuration_digest','hook_owner'})


class SetupSourceFixture(unittest.TestCase):
    def setup_api(self):
        try:
            from runtime import setup_observation
        except ImportError:
            self.fail('The owner-issued setup source is missing')
        return setup_observation

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='setup-source-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.package = self.base/'plugin'; (self.package/'hooks').mkdir(parents=True)
        (self.package/'hooks/codex.json').write_bytes((state.PACKAGE/'hooks/codex.json').read_bytes())
        for source in [*state._runtime_sources(),*(state.PACKAGE/'policies').glob('*.md')]:
            target=self.package/source.relative_to(state.PACKAGE)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        scope=unenrolled.UnenrolledCheckoutTests('runTest');scope.setUp();self.addCleanup(scope.doCleanups)
        self.checkout=scope.repo
        self.home=self.base/'home';self.home.mkdir()
        self.system=self.base/'system';self.system.mkdir()
        self.plugin_id='workflow@local-test'
        self.identity=startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(scope.record),
            repository_id='repository',checkout_id='primary',client='codex'))

    def observe(self, identity=None, transaction='transaction', requirements=('adoption',), *, partial=False, desktop=False, direct=False, session_override=False, managed_requirement=False):
        api=self.setup_api();identity=identity or self.identity
        root=Path(identity['repository_root']);source=self.package/'hooks/codex.json'
        content_hash=api.plugin_content_hash(self.package,dict(paths=0,bytes=0,deadline=time.monotonic()+10))
        self.proof=dict(reference='fixture:independently-observed-semantic-result',sha256='d'*64)
        self.assessment=dict(cases=[dict(id=name,status="verified",evidence=[self.proof]) for name in api.BEHAVIOR_CASES],
            requirements=[dict(id=name,status="verified",evidence=[self.proof]) for name in requirements],
            general_review=[self.proof],security_review=[self.proof],semantic_assessment=dict(
                complete=True,catalog_complete=True,conflicts_checked=True,instructions_checked=True,source_provenance_checked=True))
        if partial:self.assessment['cases'].pop()
        definitions=json.loads(source.read_bytes())['hooks'];hooks=[];states={}
        for index,(name,groups) in enumerate(definitions.items()):
            event=name[0].lower()+name[1:];handler=groups[0]['hooks'][0]
            label={'SessionStart':'session_start','PreToolUse':'pre_tool_use','PostToolUse':'post_tool_use','Stop':'stop','Interrupt':'interrupt'}[name]
            key=f'{self.plugin_id}:hooks/codex.json:{label}:0:0'
            states[key]=dict(enabled=True,trusted_hash='sha256:'+'a'*64,trusted_content_hash=content_hash)
            hooks.append(dict(key=key,eventName=event,sourcePath=str(source),source='plugin',pluginId=self.plugin_id,
                handlerType='command',enabled=True,isManaged=False,displayOrder=index,timeoutSec=handler['timeout'],
                command=handler['command'].replace('${PLUGIN_ROOT}',str(self.package)),matcher=groups[0].get('matcher'),
                currentHash='sha256:'+'a'*64+'/'+content_hash,trustStatus='trusted'))
        user=dict(features=dict(hooks=True,plugins=True),plugins={self.plugin_id:dict(enabled=True)},hooks=dict(state=states),
            unrelated_secret='fixture-sentinel-never-a-real-secret')
        lines=['model = "first-model"','unrelated_secret = "fixture-sentinel-never-a-real-secret"','[features]','hooks = true','plugins = true',
            '[plugins.'+json.dumps(self.plugin_id)+']','enabled = true']
        for key,value in states.items():
            lines += ['[hooks.state.'+json.dumps(key)+']','enabled = true',
                'trusted_hash = '+json.dumps(value['trusted_hash']),'trusted_content_hash = '+json.dumps(content_hash)]
        self.config=self.home/'config.toml';self.config.write_text('\n'.join(lines)+'\n')
        responses={'initialize':dict(codexHome=str(self.home),platformFamily='unix',platformOs='macos',userAgent='fixture'),
            'thread/read':dict(thread=dict(id='setup',cwd=str(root),source='vscode' if desktop else 'exec',
                originator='Codex Desktop' if desktop else 'codex_exec',cliVersion='0.165.0')),
            'config/read':dict(config=user,origins={},layers=[dict(name=dict(type='system',file=str(self.system/'config.toml')),config={},version='fixture',disabledReason=None),
                dict(name=dict(type='user',file=str(self.config)),config=user,version='fixture',disabledReason=None)]),
            'hooks/list':dict(data=[dict(cwd=str(root),hooks=hooks,errors=[],warnings=[])]),
            'configRequirements/read':dict(requirements=None)}
        if session_override:
            responses['config/read']['layers'].insert(0,dict(name=dict(type='sessionFlags'),config=dict(hooks=dict(state=states)),version='fixture'))
        if managed_requirement:
            responses['configRequirements/read']['requirements']=dict(featureRequirements=dict(hooks=True))
        payload=dict(binding=dict(accepted_scope_digest=onboarding._digest(identity),
                attempt_ref=dict(reference='fixture:fake-native-owner',sha256='b'*64),transaction_id=transaction),
            package_root=str(self.package),native=dict(surface='codex-desktop' if desktop else 'codex-cli',
                version='0.165.0',threads=[dict(id='setup',surface='codex-cli',version='0.165.0',originator='codex_exec')],
                executable=dict(path=str(Path(sys.executable).resolve()),sha256='e'*64)),codex_home=str(self.home),
            hooks=responses['hooks/list'],layers=responses['config/read']['layers'],
            requirements=responses['configRequirements/read']['requirements'],
            observations={name:[self.proof] for name in api.BEHAVIOR_CASES},bootstrap=[self.proof],pending_reason=None)
        witness=object()
        def fake_native_owner(value):
            if value is not witness or direct:raise doctor.ObservationError('Not an issued fake host witness')
            return copy.deepcopy(payload)
        # Test-only host injection; Native's owned frontend/transport has separate tests.
        with patch.object(doctor,'_native_witness_content',side_effect=fake_native_owner):
            result=api.collect_setup(witness,identity,transaction,self.assessment)

        return result


class SetupSourceTests(SetupSourceFixture):
    def test_fake_native_owner_and_complete_assessment_create_immutable_source(self):
        api=self.setup_api();observed=self.observe();content=startup._canonical_plain(observed.content)
        self.assessment['cases'].clear()
        self.assertEqual(len(content['assessment']['cases']),len(api.BEHAVIOR_CASES))
        self.assertEqual(content['native']['surface'],'codex-cli')
        self.assertEqual(content['runs'],[])
        self.assertEqual(set(content['source']['observations']),set(api.BEHAVIOR_CASES))
        self.assertNotIn('fixture-sentinel-never-a-real-secret',json.dumps(content))
        self.assertEqual(api.current_configuration(content['configuration'],self.checkout)['digest'],content['configuration']['digest'])

    def test_model_account_changes_do_not_invalidate_but_disable_and_trust_changes_do(self):
        api=self.setup_api();content=startup._canonical_plain(self.observe().content);config=content['configuration']
        saved=self.config.read_text();self.config.write_text(saved.replace('model = "first-model"','model = "other-model"\naccount_preference = "unrelated"'))
        self.assertEqual(api.current_configuration(config,self.checkout)['digest'],config['digest'])
        for changed in (saved.replace('enabled = true','enabled = false',1),
                        saved.replace('trusted_hash = "sha256:a','trusted_hash = "sha256:f',1)):
            self.config.write_text(changed)
            self.assertNotEqual(api.current_configuration(config,self.checkout)['digest'],config['digest'])

    def test_added_project_override_source_and_bundle_change_are_observable(self):
        api=self.setup_api();config=startup._canonical_plain(self.observe().content)['configuration']
        (self.checkout/'.codex').mkdir();override=self.checkout/'.codex/config.toml'
        override.write_text('[features]\nhooks = false\n')
        self.assertNotEqual(api.current_configuration(config,self.checkout)['digest'],config['digest'])
        override.unlink();(self.package/'new-source.py').write_text('changed = True\n')
        self.assertNotEqual(api.current_configuration(config,self.checkout)['digest'],config['digest'])

    def test_native_bundle_has_its_own_byte_cap_and_shares_path_and_time_limits(self):
        api=self.setup_api();config=startup._canonical_plain(self.observe().content)['configuration']
        deadline=time.monotonic()+4
        canonical=dict(paths=0,bytes=7*1024*1024,deadline=deadline)
        native=dict(paths=0,bytes=0,deadline=deadline)
        current=api.current_configuration(config,self.checkout,canonical,bundle_budget=native)
        self.assertEqual(current['digest'],config['digest'])
        self.assertGreater(native['bytes'],1024*1024)
        self.assertLess(canonical['bytes'],8*1024*1024)
        self.assertEqual(canonical['paths'],native['paths'])
        self.assertEqual((canonical['deadline'],native['deadline']),(deadline,deadline))
        for index in range(8):(self.package/f'payload-{index}').write_bytes(b'x'*(1024*1024))
        canonical=dict(paths=0,bytes=0,deadline=time.monotonic()+4)
        native=dict(paths=0,bytes=0,deadline=canonical['deadline'])
        with self.assertRaises(startup.StartupError):
            api.current_configuration(config,self.checkout,canonical,bundle_budget=native)
        self.assertGreater(native['bytes'],7*1024*1024)
        self.assertLessEqual(native['bytes'],8*1024*1024)

    def test_partial_matrix_and_desktop_source_cannot_mint_cli_receipt(self):
        self.setup_api()
        for options in (dict(partial=True),dict(desktop=True),dict(session_override=True),dict(managed_requirement=True)):
            with self.subTest(options=options),self.assertRaises(startup.StartupError):self.observe(**options)

    def test_plain_json_or_unissued_witness_cannot_be_persisted_as_owner_source(self):
        api=self.setup_api();observed=self.observe()
        with self.assertRaises(startup.StartupError):
            api.receipt_binding(startup._canonical_plain(observed.content),self.base)
        with self.assertRaises(startup.StartupError):api.collect_setup(None,self.identity,'transaction',self.assessment)
        with self.assertRaises(startup.StartupError):self.observe(direct=True)


class SetupJournalTests(SetupSourceFixture):
    def test_real_d4_owner_journal_production_reader_and_recovery(self):
        api=self.setup_api();birth=CanonicalBirthFixture('runTest');self.addCleanup(birth.doCleanups)
        prepared,_,context,history=birth.prepare_common_git_update(qualified=False)
        fixture,request,_,_=prepared;record=request['manifest']['enrollment']['record'];self.package=state.PACKAGE
        affected={tuple(row[name] for name in ('repository_id','checkout_id','client'))
            for row in request['manifest']['enrollment']['affected']}
        entries=changes._scoped_entries(record)
        selected={key:value for key,value in entries.items() if key in affected}
        retained={key:copy.deepcopy(value[2]) for key,value in entries.items() if key not in affected}
        observed=[]
        for key,(_,checkout,entry) in selected.items():
            identity=startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(record),
                repository_id=key[0],checkout_id=key[1],client=key[2]))
            rule=next(row for row in request['manifest']['enrollment']['qualification'] if all(row[k]==v for k,v in zip(('repository_id','checkout_id','client'),key)))
            source=self.observe(identity,request['manifest']['transaction_id'],rule['requirements']);observed.append(source)
            binding=api.receipt_binding(source,fixture.directory)
            entry['activation'].update(status='active',qualification='qualified',transaction_id=request['manifest']['transaction_id'],
                accepted_scope_digest=binding['accepted_scope_digest'],qualification_binding=binding,evidence=[binding['receipt']])
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        decision=birth.decision(fixture,request,'apply',(context,))
        with patch.dict(os.environ,HOME=str(self.home.parent),CODEX_HOME=str(self.home)):
            pending=onboard_coordinator.run_verified_operation(decision,fixture.directory,request)
            self.assertEqual(pending['exit_code'],2,pending)
            self.assertNotIn('setup_receipts',changes._load_journal(fixture.directory))
            self.assertFalse(pending['qualified'])
            decision=birth.decision(fixture,request,'resume',(context,))
            pending=onboard_coordinator.run_verified_operation(decision,fixture.directory,request,setup_observations=observed)
            self.assertEqual(pending['exit_code'],2,pending)
            journal=changes._load_journal(fixture.directory)
            self.assertEqual(len(journal['setup_receipts']),len(observed))
            saved_receipts=copy.deepcopy(journal['setup_receipts'])
            proof=dict(manifest_digest=onboarding._digest(request['manifest']),input_digest=changes._scoped_input_digest(request['manifest']),
                qualification=[],task_reconciliation=[],prior_compatibility=[],command_reconciliation=[])
            for key,(_,checkout,entry) in selected.items():
                budget=dict(paths=0,bytes=0,deadline=time.monotonic()+10)
                acquired=startup._acquire_canonical_membership(Path(checkout['root']),authority_path=None,budget=budget)
                observation=startup._canonical_plain(acquired.observation)
                observation['dependencies']=[startup._acquired_dependency(Path(name),budget) for name in sorted(entry['configuration']['shared_dependencies'])]
                observation['runtime_location']=startup._acquired_runtime_location(Path(entry['configuration']['runtime']['state_dir']),state.PACKAGE,record,observation,budget)
                facts=dict(authority=dict(path=str(acquired.authority_path),target=startup._canonical_plain(acquired.authority_target)),
                    observation=observation,client=key[2],installed=dict(policy_digest=startup.policy_digest(state.PACKAGE),
                        runtime_digest=state.runtime_fingerprint(),clients=['codex'],package_root=str(state.PACKAGE)))
                source=next(s for s in observed if s.content['identity']['checkout_id']==key[1]);assessment=startup._canonical_plain(source.content['assessment'])
                proof['qualification'].append(dict(zip(('repository_id','checkout_id','client'),key))|dict(
                    requirements=[{name:row[name] for name in ('id','evidence')} for row in assessment['requirements']],
                    general_review=assessment['general_review'],security_review=assessment['security_review'],scope_observations=facts))
            resumed=request|dict(observations=proof);decision=birth.decision(fixture,resumed,'resume',(context,))
            result=onboard_coordinator.run_verified_operation(decision,fixture.directory,resumed)
            self.assertEqual(result['exit_code'],0,result);self.assertTrue(result['qualified'],result)
            published=json.loads(Path(history['authority_path']).read_bytes())
            self.assertEqual({key:value[2] for key,value in changes._scoped_entries(published).items() if key in retained},retained)
            copied=self.base/'copied-journal';copied.mkdir(mode=0o700)
            copied_file=copied/'transaction.json'
            copied_file.write_bytes((fixture.directory/'transaction.json').read_bytes());copied_file.chmod(0o600)
            for changed in ('transaction','scope','source','copied-journal'):
                with self.subTest(replay=changed):
                    invalid=copy.deepcopy(record)
                    invalid_entry=changes._scoped_entries(invalid)[key][2]
                    if changed=='transaction':invalid_entry['activation']['transaction_id']='another-transaction'
                    elif changed=='scope':
                        next(c for r in invalid['repositories'] for c in r['checkouts'] if c['checkout_id']==key[1])['checkout_id']='another-checkout'
                    elif changed=='source':invalid_entry['configuration']['policy_digest']='a'*64
                    else:
                        receipt=invalid_entry['activation']['qualification_binding']['receipt']
                        receipt['reference']=str(copied_file)+'#setup:'+receipt['reference'].split('#setup:')[1]
                    self.assertIsNone(api.read_qualification(invalid,facts,cwd=checkout['root']))
            original_reader=api.current_configuration;reader_counts=[];native_counts=[]
            def measured_reader(config,cwd,budget=None,**options):
                current=original_reader(config,cwd,budget,**options)
                if 'bundle_budget' in options:native_counts.append(dict(options['bundle_budget']))
                return current
            with patch.object(api,'current_configuration',side_effect=measured_reader):
                for _,checkout,_ in selected.values():
                    event=SimpleNamespace(client='codex',cwd=Path(checkout['root']))
                    budget=dict(paths=0,bytes=0)
                    acquired=startup.acquire_project_entry(event,state.PACKAGE,budget=budget)
                    self.assertEqual(acquired.resolution.status,'active',acquired.resolution)
                    self.assertEqual(acquired.resolution.scope.accepted_scope_digest,
                        startup._canonical_digest(startup._canonical_plain(acquired.resolution.scope.accepted_identity)))
                    native=native_counts[-1]
                    self.assertLessEqual(budget['bytes'],8*1024*1024);self.assertLessEqual(native['bytes'],8*1024*1024)
                    self.assertLessEqual(budget['paths'],4096)
                    reader_counts.append(dict(canonical_bytes=budget['bytes'],native_bundle_bytes=native['bytes'],paths=budget['paths']))
            print('LOCAL_READER_COUNTS',json.dumps(reader_counts),flush=True)
            final=changes._load_journal(fixture.directory)
            self.assertEqual(final['setup_receipts'],saved_receipts)
            original=json.loads((Path(history['original_plan'])/'transaction.json').read_bytes())
            self.assertEqual(original['state'],'applied')
            final['state']='interrupted';changes._save(fixture.directory,final)
            blocked=startup.acquire_project_entry(event,state.PACKAGE)
            self.assertIsNone(blocked.resolution.scope)
            self.assertEqual(changes._load_journal(fixture.directory)['setup_receipts'],saved_receipts)
            final['state']='applied';changes._save(fixture.directory,final)
            self.config.write_text(self.config.read_text().replace('enabled = true','enabled = false',1))
            changed=startup.acquire_project_entry(event,state.PACKAGE)
            self.assertEqual((changed.resolution.status,changed.resolution.reason),('suspended','qualification_changed'))
