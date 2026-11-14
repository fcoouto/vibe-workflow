"""Real configured owner paths for native behavior records; fixtures supply no qualification authority."""

import hashlib
import json
from pathlib import Path
import unittest

from runtime import doctor, startup
from runtime.adapters import codex
from tests.fixture_native_behavior import (
    REJECTION, PATCH, CONTEXT, row, encoded, session, started, call, output,
    bootstrap_reads, wrapped_patch_denial, hook)


class NativeBehaviorOwnerTests(unittest.TestCase):
    """Reuse real configured D4 ownership; transport fixtures supply no qualification authority."""

    def setUp(self):
        from types import SimpleNamespace
        from runtime import changes, state
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        self.birth = CanonicalBirthFixture('runTest')
        self.addCleanup(self.birth.doCleanups)
        self.prepared = self.birth.prepare()
        self.fixture, self.request, self.decision, self.runtime_directory = self.prepared
        self.birth.assert_applied_configured(self.prepared, self.birth.invoke(self.prepared))
        self.runtime_context = self.birth.context(self.fixture)
        record = self.request['manifest']['enrollment']['record']
        member = next(iter(changes._scoped_entries(record)))
        self.identity = startup._canonical_plain(startup.canonical_scope_projection(
            startup.decode_project_record(record), repository_id=member[0], checkout_id=member[1], client=member[2]))
        initial = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=self.fixture.repo), state.PACKAGE)
        self.assertEqual(initial.resolution.status, 'configured')
        self.assertIsNone(initial.resolution.scope)

    def _stop_native(self, owner, reference, source, directory):
        import os
        journal = json.loads((directory/'transaction.json').read_bytes())
        original = journal['native_observations'][startup._canonical_digest(reference)]
        with self.assertRaises(ProcessLookupError):
            os.kill(original['pid'], 0)
        with self.assertRaises(ProcessLookupError):
            os.killpg(original['pid'], 0)
        facts = (f"fixture:observed-reaped-child:{original['pid']}:{original['returncode']}",
            f"fixture:observed-empty-original-process-group:{original['pid']}")
        if 'source_contract' in original:
            # These audited fixture scripts wait for their known hooks; this is no native-client closure claim.
            writer_fact = 'fixture:audited-script-known-writers-and-project-effects:'+str(original['pid'])
            stopped = dict(claim_id=original['claim_id'], pid=original['pid'], returncode=original['returncode'],
                stopped=True, reaped=True, process_evidence=dict(reference=facts[0],
                    sha256=hashlib.sha256(facts[0].encode()).hexdigest()),
                writer_assessment_evidence=dict(reference=writer_fact,
                    sha256=hashlib.sha256(writer_fact.encode()).hexdigest()),
                identified_writers_reconciled=True, pending_workflow_hooks=False,
                unexpected_tool_or_spawn=False, unexplained_project_effects=False, ownership_conflicts=False)
        else:
            stopped = dict(claim_id=original['claim_id'], pid=original['pid'], returncode=original['returncode'],
                stopped=True, descendants_stopped=True, evidence=[dict(reference=value,
                    sha256=hashlib.sha256(value.encode()).hexdigest()) for value in facts])
        self.assertEqual(source, owner.reconcile_native_source(reference, source, stopped))
        return original

    def _observe_original_cli(self, *, limited=False, isolated_hook=False, approved_startup_reads=False):
        import copy
        import os
        import sys
        import time
        from unittest.mock import patch
        from scripts.onboard_coordinator import CoordinatorOperationOwner
        self.assertTrue(callable(getattr(CoordinatorOperationOwner, 'observe_native_source', None)),
            'Actual owner native-source callable is missing')
        home = self.fixture.base/'native-fixture-home'
        home.mkdir()
        canary = self.fixture.repo/'public-canary.txt'
        canary.write_text('before\n')
        calls = [call(), output()]
        if approved_startup_reads:
            reads, hashes = bootstrap_reads()
            calls = [*reads, *wrapped_patch_denial()]
        native_rows = [row('session_meta', dict(id='native-thread', cwd=str(self.fixture.repo))),
            row('world_state', dict(state='x' * (1024 * 1024))),
            started(), *calls, row('event_msg', dict(type='task_complete', turn_id='native-turn'))]
        rollout = home/'fixture-rollout.jsonl'
        settings = home/'fixture-input.json'
        settings.write_text(json.dumps(dict(thread_id='native-thread', rollout=str(rollout), records=native_rows, responses={})))
        executable = home/'fake-native'
        executable.write_text(f'#!{sys.executable}\n'+
            'from pathlib import Path\nimport json,sys,subprocess,hashlib\nfrom xml.sax.saxutils import escape\n'+
            f'data=json.loads(Path({str(settings)!r}).read_text())\n'+
            'if sys.argv[1]=="--version":\n print("codex-cli 0.165.0",flush=True)\n'
            'elif sys.argv[1]=="exec":\n'
            ' records=data["records"]\n'
            ' if data.get("tool_hook_command"):\n'
            '  payload=data["tool_hook_payload"];result=subprocess.run(data["tool_hook_command"],input=json.dumps(payload),text=True,capture_output=True,check=True,timeout=5)\n'
            '  pre=json.loads(result.stdout) if result.stdout.strip() else {}\n'
            '  decision=pre.get("hookSpecificOutput",{});command=payload["tool_input"]["command"]\n'
            '  records=[dict(timestamp="2026-11-02T10:00:00Z",type="event_msg",payload=dict(type="task_started",turn_id=payload["turn_id"])),dict(timestamp="2026-11-02T10:00:00Z",type="response_item",payload=dict(type="custom_tool_call",name="apply_patch",call_id=payload["tool_use_id"],input=command))]\n'
            '  if data.get("post_tool_hook_command"):\n'
            '   assert decision.get("permissionDecision")!="deny"\n'
            '   write=data["approved_write"];path=Path(write["path"]);assert hashlib.sha256(path.read_bytes()).hexdigest()==write["before_sha256"];path.write_text(write["after"])\n'
            '   other=data["other_post_payload"];Path(write["path"]).read_bytes()\n'
            '   subprocess.run(data["post_tool_hook_command"],input=json.dumps(other),text=True,capture_output=True,check=True,timeout=5)\n'
            '   records.extend([dict(timestamp="2026-11-02T10:00:00Z",type="response_item",payload=dict(type="function_call",name="fixture_read",call_id=other["tool_use_id"],arguments="{}")),dict(timestamp="2026-11-02T10:00:00Z",type="response_item",payload=dict(type="function_call_output",call_id=other["tool_use_id"],output="Public canary read completed"))])\n'
            '   subprocess.run(data["post_tool_hook_command"],input=json.dumps(data["post_tool_hook_payload"]),text=True,capture_output=True,check=True,timeout=5)\n'
            '   text=data["post_tool_hook_payload"]["tool_response"]\n'
            '  else:\n'
            '   assert decision["permissionDecision"]=="deny"\n'
            '   text="Command blocked by PreToolUse hook: "+decision["permissionDecisionReason"]+". Command: "+command\n'
            '  if data.get("child_patch"): text=[dict(type="input_text",text=text)]\n'
            '  records.extend([dict(timestamp="2026-11-02T10:00:00Z",type="response_item",payload=dict(type="custom_tool_call_output",call_id=payload["tool_use_id"],output=text)),dict(timestamp="2026-11-02T10:00:00Z",type="event_msg",payload=dict(type="task_complete",turn_id=payload["turn_id"]))])\n'
            '  with Path(data["rollout"]).open("ab") as stream: stream.write(b"".join(json.dumps(value).encode()+b"\\n" for value in records))\n'
            '  print(json.dumps(dict(type="thread.started",thread_id=data["thread_id"])),flush=True);sys.exit(0)\n'
            ' if data.get("hook_command"):\n'
            '  result=subprocess.run(data["hook_command"],input=json.dumps(data["hook_payload"]),text=True,capture_output=True,check=True,timeout=5)\n'
            '  text=json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]\n'
            '  hook=dict(type="message",role="user",id="fixture-message-"+data["thread_id"],content=[dict(type="input_text",text="<hook_prompt hook_run_id=\\\"fixture-hook-"+data["thread_id"]+"\\\">"+escape(text)+"</hook_prompt>")],internal_chat_message_metadata_passthrough=dict(content_item_metadata=[dict(provenance=dict(type="harness",hook=True))]))\n'
            '  if data.get("developer_context"):\n'
            '   hook.update(role="developer",content=[dict(type="input_text",text=text)],internal_chat_message_metadata_passthrough=dict(turn_id="native-turn",content_item_kinds=["hooks.additional_context"],content_item_metadata=[dict(provenance=dict(type="harness",hook=True))]))\n'
            '  records.insert(3 if data.get("developer_context") else 1,dict(timestamp="2026-11-02T10:00:00Z",type="response_item",payload=hook))\n'
            ' path=Path(data["rollout"]);path.write_bytes(b"".join(json.dumps(value).encode()+b"\\n" for value in records));path.chmod(0o600)\n'
            ' print(json.dumps(dict(type="thread.started",thread_id=data["thread_id"])),flush=True)\n'
            'else:\n'
            ' for line in sys.stdin:\n'
            '  request=json.loads(line)\n'
            '  if "id" in request:\n'
            '   result=data["responses"][request["method"]]\n'
            '   if request["method"]=="thread/read" and data.get("threads"): result=dict(thread=data["threads"][request["params"]["threadId"]])\n'
            '   print(json.dumps(dict(id=request["id"],result=result)),flush=True)\n')
        executable.chmod(0o700)
        reference = dict(reference='fixture:native-cli', sha256='a'*64)
        native = dict(argv=[str(executable), 'exec', '--json', '--sandbox', 'read-only',
            'Software fixture: submit the approved public canary patch once.'], cwd=str(self.fixture.repo),
            timeout_seconds=10, external_effects=[dict(path=str(home), kind='vendor-owned-state', recovery='manual')],
            serialization_reference=dict(reference='fixture:independently-verified-native-state-exclusion', sha256='b'*64))
        if limited:
            native['source_contract'] = 'bounded-native-source-v1'
        hook_key = None
        if isolated_hook:
            inherited = home/'hooks.json'
            inherited.write_text(json.dumps(dict(Stop=[dict(hooks=[dict(type='command', command='true')])])))
            hook_key = f'{inherited}:stop:0:0'
            native['argv'][5:5] = ['-c', 'hooks.state={'+json.dumps(hook_key)+'={enabled=false}}', '--']
        attempt = dict(reference=reference, task=None, native_sessions=[], operations=['observe-cli-basic'],
            capability_keys=['startup-context'], limits=dict(deadline=time.time()+60, max_attempts=1, max_resource_units=1),
            stop_conditions=['Stop after original policy canary; retain uncertainty'], native_execution=native,
            probe=dict(canary=str(canary), patch=PATCH, before_sha256=hashlib.sha256(canary.read_bytes()).hexdigest()))
        if approved_startup_reads:
            attempt['startup_read_sha256'] = hashes
        decision = copy.deepcopy(self.decision)
        decision['qualification_scope'] = dict(
            decision_reference=dict(reference='fixture:explicit-original-native-observation', sha256='c'*64),
            target=dict(accepted_scope_digest=startup._canonical_digest(self.identity),
                repository_root=self.identity['repository_root'], git_common_dir=self.identity['git_common_dir'], disposable=True),
            attempts=[attempt])
        owner = CoordinatorOperationOwner(decision)
        with patch.dict(os.environ, CODEX_HOME=str(home)):
            source = owner.observe_native_source(self.identity, reference, codex.observe_cli_basic)
        startup._canonical_ref(startup._canonical_plain(source))
        self.assertIn('#native-observation:', source['reference'])
        self.assertEqual(canary.read_text(), 'before\n')
        original = self._stop_native(owner, reference, source, self.fixture.directory)
        with patch.dict(os.environ, CODEX_HOME=str(home)), self.assertRaises(startup.StartupError):
            owner.observe_native_source(self.identity, reference, codex.observe_cli_basic)
        self.assertNotIn('submitted-write-policy-rejection', json.dumps(original))
        self.assertNotIn('bootstrap_verified', json.dumps(original))
        return dict(home=home, canary=canary, executable=executable, settings=settings, source=source,
            allocation=attempt, decision=decision, original=original, rollout=rollout, hook_key=hook_key)

    def test_real_owner_cli_source_retains_original_probe_without_qualification(self):
        self._observe_original_cli()

    def _observe_metadata(self, original):
        import copy
        import os
        import time
        from unittest.mock import patch
        from runtime import changes, setup_observation as source, state
        from scripts.onboard_coordinator import CoordinatorOperationOwner
        home = original['home']; package = state.PACKAGE
        system = home/'system'; system.mkdir(exist_ok=True)
        content_hash = source.plugin_content_hash(package, dict(paths=0, bytes=0, deadline=time.monotonic()+10))
        plugin_id = 'workflow@local-test'
        definitions = json.loads((package/'hooks/codex.json').read_bytes())['hooks']
        hooks = []; states = {}
        for index, (name, groups) in enumerate(definitions.items()):
            handler = groups[0]['hooks'][0]
            label = {'SessionStart':'session_start', 'PreToolUse':'pre_tool_use', 'PostToolUse':'post_tool_use',
                'Stop':'stop', 'Interrupt':'interrupt'}[name]
            key = f'{plugin_id}:hooks/codex.json:{label}:0:0'
            states[key] = dict(enabled=True, trusted_hash='sha256:'+'a'*64, trusted_content_hash=content_hash)
            hooks.append(dict(key=key, eventName=name[0].lower()+name[1:], sourcePath=str(package/'hooks/codex.json'),
                source='plugin', pluginId=plugin_id, handlerType='command', enabled=True, isManaged=False,
                displayOrder=index, timeoutSec=handler['timeout'], command=handler['command'].replace('${PLUGIN_ROOT}', str(package)),
                matcher=groups[0].get('matcher'), currentHash='sha256:'+'a'*64+'/'+content_hash, trustStatus='trusted'))
        user = dict(features=dict(hooks=True, plugins=True), plugins={plugin_id:dict(enabled=True)}, hooks=dict(state=states))
        lines = ['[features]', 'hooks = true', 'plugins = true', '[plugins.'+json.dumps(plugin_id)+']', 'enabled = true']
        for key, value in states.items():
            lines += ['[hooks.state.'+json.dumps(key)+']', 'enabled = true',
                'trusted_hash = '+json.dumps(value['trusted_hash']), 'trusted_content_hash = '+json.dumps(content_hash)]
        config = home/'config.toml'; config.write_text('\n'.join(lines)+'\n')
        thread = original['original']['result']['content']['native']['threads'][0]['id']
        data = json.loads(original['settings'].read_bytes())
        data['responses'] = {'initialize':dict(codexHome=str(home), platformFamily='unix', platformOs='macos', userAgent='fixture'),
            'thread/read':dict(thread=dict(id=thread, cwd=str(self.fixture.repo), path=str(original['rollout']),
                source='exec', originator='codex_exec', cliVersion='0.165.0')),
            'hooks/list':dict(data=[dict(cwd=str(self.fixture.repo), hooks=hooks, errors=[], warnings=[])]),
            'config/read':dict(layers=[dict(name=dict(type='system', file=str(system/'config.toml')), config={}, version='fixture'),
                dict(name=dict(type='user', file=str(config)), config=user, version='fixture')]),
            'configRequirements/read':dict(requirements=None)}
        hook_key = original.get('hook_key')
        if hook_key is not None:
            hooks.append(dict(key=hook_key, eventName='stop', sourcePath=str(home/'hooks.json'), source='user',
                enabled=False, isManaged=False))
            data['responses']['config/read']['layers'].insert(0, dict(name=dict(type='sessionFlags'),
                config=dict(hooks=dict(state={hook_key:dict(enabled=False)})), version='fixture'))
        original['settings'].write_text(json.dumps(data))
        metadata_directory = self.fixture.directory.parent
        plan_bytes = (self.fixture.directory/'plan.json').read_bytes()
        (metadata_directory/'plan.json').write_bytes(plan_bytes); (metadata_directory/'plan.json').chmod(0o600)
        from runtime import onboarding
        with onboarding._locked(metadata_directory):
            pass
        changes._load_journal(metadata_directory)
        old_directory = self.fixture.directory
        try:
            self.fixture.directory = metadata_directory
            decision = self.birth.decision(self.fixture, self.request, 'apply', ())
        finally:
            self.fixture.directory = old_directory
        reference = dict(reference='fixture:native-metadata', sha256='d'*64)
        execution = copy.deepcopy(original['allocation']['native_execution'])
        execution['argv'] = [str(original['executable']), 'app-server', '--listen', 'stdio://']
        if hook_key is not None:
            execution['argv'].extend(original['allocation']['native_execution']['argv'][5:7])
        allocation = dict(reference=reference, task=None, native_sessions=[thread], operations=['observe-setup-metadata'],
            capability_keys=['startup-context'], limits=dict(deadline=time.time()+60, max_attempts=1, max_resource_units=1),
            stop_conditions=['Retain exact policy source and pending Toolkit cases'], native_execution=execution,
            source_capture=startup._canonical_plain(original['source']))
        decision['qualification_scope'] = dict(decision_reference=dict(reference='fixture:independently-approved-exact-metadata', sha256='e'*64),
            target=copy.deepcopy(original['decision']['qualification_scope']['target']), attempts=[allocation])
        owner = CoordinatorOperationOwner(decision)
        seen = {}
        def observe(process, admission):
            witness = codex.observe_setup_metadata(process, admission)
            content = doctor._native_witness_content(witness)
            evidence = content['native_safety']['evidence']
            rule = self.request['manifest']['enrollment']['qualification'][0]
            bootstrap_evidence = [*original.get('bootstrap_evidence', [])]
            bootstrap_evidence.extend(proof for proof in evidence if proof not in bootstrap_evidence)
            assessment = dict(bootstrap=dict(status='verified', evidence=bootstrap_evidence),
                cases=[dict(id=name, status='verified' if content['observations'].get(name) else 'pending',
                    evidence=content['observations'].get(name, [])) for name in source.BEHAVIOR_CASES],
                requirements=[dict(id=name, status='pending', evidence=[]) for name in rule['requirements']],
                general_review=evidence, security_review=evidence, semantic_assessment=dict(complete=True, catalog_complete=True,
                    conflicts_checked=True, instructions_checked=True, source_provenance_checked=True))
            projection = startup._canonical_plain(source.qualification_attempt_projection(admission,
                operation='observe-setup-metadata', case_id='startup-context', process=process))
            filtered = source.collect_setup(witness, self.identity, projection['transaction_id'], assessment,
                predecessor=original.get('predecessor'))
            seen.update(filtered=filtered, assessment=assessment, witness=content)
            return filtered
        with patch.dict(os.environ, CODEX_HOME=str(home)):
            captured = owner.observe_native_source(self.identity, reference, observe)
        actual = self._stop_native(owner, reference, captured, metadata_directory)
        self.assertEqual(seen['witness']['native_safety']['claim'], 'submitted-write-policy-rejection')
        self.assertEqual(len(seen['witness']['native_safety']['evidence']), 4)
        self.assertEqual(actual['after'], original['original']['before'])
        self.assertEqual(original['canary'].read_text(), 'before\n')
        self.assertTrue(source._bootstrap_verified(startup._canonical_plain(seen['filtered'].content)))
        if original['allocation'].get('context_reference') is None:
            self.assertEqual(seen['witness']['observations'], {})
        else:
            self.assertTrue(seen['witness']['observations']['startup-context'])
        return dict(source=captured, filtered=seen['filtered'], assessment=seen['assessment'], directory=metadata_directory,
            decision=decision, allocation=allocation)

    def test_retained_native_successor_admits_task_within_one_entry_budget(self):
        import os
        import time
        from types import SimpleNamespace
        from unittest.mock import patch
        from runtime import changes, state
        from tests.fixture_staged_successor import publish_successor

        original = self._observe_original_cli(limited=True)
        metadata = self._observe_metadata(original)
        active = self._publish_partial_source(metadata, original, 'budget-bootstrap')
        self.home = original['home']
        identity = dict(client='codex', session_id='native-thread', task_id='budget-review')
        reference = dict(reference='fixture:successor-review', sha256='e' * 64)
        authority = startup._canonical_copy(active['decision']['qualification_scope'])
        authority['attempts'] = [dict(reference=reference, task=identity,
            native_sessions=[identity['session_id']], operations=['task-start'],
            capability_keys=['pre-tool-enforcement'],
            limits=dict(deadline=time.time()+90, max_attempts=1, max_resource_units=1),
            stop_conditions=['Admit only this fixture task'])]
        prior = changes._load_journal(active['directory'])
        with patch.dict(os.environ, CODEX_HOME=str(self.home)):
            successor = publish_successor(self, self.fixture, prior, qualification_scope=authority)
            paths = [Path(ref['reference'].partition('#')[0]) for ref in
                (original['source'], metadata['source'], active['binding']['receipt'],
                 successor.qualification['reference'])]
            # Match observed journal sizes without changing decoded evidence or receipt digests.
            self.assertEqual(len(set(paths)), 4)
            for path, size in zip(paths, (322_990, 321_327, 351_254, 354_956)):
                raw = path.read_bytes()
                self.assertLess(len(raw), size)
                with path.open('ab') as stream:
                    stream.write(b' ' * (size - len(raw)))
            event = SimpleNamespace(client='codex', cwd=self.fixture.repo)
            current = startup.acquire_project_entry(event, state.PACKAGE).resolution
            self.assertEqual(current.status, 'active', current.reason)
            task = state.start_task(current.scope, identity | dict(scope_ref='fixture:successor-review',
                mode='read-only', decision_ref='fixture:approved', attempt_ref=reference))['task']
            self.assertEqual(task['qualification_attempt'], reference)
            self.assertNotIn('delegation', task)

    def test_real_owner_metadata_policy_source_keeps_toolkit_cases_pending(self):
        self._observe_metadata(self._observe_original_cli())

    def test_real_owner_scoped_hook_isolation_preserves_filtered_file_backed_proof(self):
        from runtime import setup_observation

        original = self._observe_original_cli(limited=True, isolated_hook=True, approved_startup_reads=True)
        filtered = self._observe_metadata(original)
        content = startup._canonical_plain(filtered['filtered'].content)
        self.assertEqual(content['native']['threads'][0]['launch']['disabled_inherited_hook_keys'], [original['hook_key']])
        row, _, part = setup_observation._read_native_reference(startup._canonical_plain(filtered['source']), setup_observation._budget())
        self.assertIsNone(part)
        self.assertEqual(row['result']['content']['source']['native_capture'], startup._canonical_plain(original['source']))
        self.assertEqual(row['result']['content']['source']['native_source_contract']['kind'], 'bounded-native-source-v1')

    def _publish_partial_source(self, observed, original, name, *, managed_attempt=None, legacy_context=False):
        import copy
        import os
        import time
        from types import SimpleNamespace
        from unittest.mock import patch
        from runtime import changes, onboarding, setup_observation as source, state
        from scripts import onboard_coordinator
        context = self.runtime_context
        wrapper = self.fixture.base/name; wrapper.mkdir(mode=0o700)
        directory = wrapper/'plan'; directory.mkdir(mode=0o700)
        record = copy.deepcopy(self.fixture.record)
        for item in record['toolkit']['files']:
            retained = directory/item['path']
            retained.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            retained.write_bytes(Path(item['local_path']).read_bytes()); retained.chmod(0o400)
            item['local_path'] = str(retained)
        self.fixture.directory = directory; self.fixture.record = record
        self.fixture.source = Path(record['toolkit']['files'][0]['local_path'])
        self.fixture.scoped_inspect()
        request = self.fixture.scoped_request(existing=True)
        request['manifest']['transaction_id'] = 'fixture-'+name
        proposed = request['manifest']['enrollment']['record']
        key = next(iter(changes._scoped_entries(proposed)))
        entry = changes._scoped_entries(proposed)[key][2]
        entry['configuration']['runtime'] = dict(schema_version=3, state_dir=str(self.runtime_directory))
        identity = startup._canonical_plain(startup.canonical_scope_projection(startup.decode_project_record(proposed),
            repository_id=key[0], checkout_id=key[1], client=key[2]))
        self.assertEqual(identity, self.identity)
        retained = source.collect_retained_setup(startup._canonical_plain(observed['source']), self.identity,
            request['manifest']['transaction_id'], observed['assessment'])
        binding = source.receipt_binding(retained, directory)
        entry['activation'].update(status='active', qualification='qualified', transaction_id=request['manifest']['transaction_id'],
            accepted_scope_digest=binding['accepted_scope_digest'], qualification_binding=binding, evidence=[binding['receipt']])
        request['approval']['manifest_digest'] = onboarding._digest(request['manifest'])
        decision = self.birth.decision(self.fixture, request, 'apply', (context,))
        reference = dict(reference='fixture:'+name+'-context-cli', sha256=hashlib.sha256(name.encode()).hexdigest())
        allocation = copy.deepcopy(original['allocation'])
        if legacy_context:
            allocation['native_execution'].pop('source_contract', None)
        allocation.update(reference=reference, context_reference=startup._canonical_plain(binding['receipt']),
            limits=dict(deadline=time.time()+90, max_attempts=1, max_resource_units=1))
        allocation['native_execution']['argv'] = [str(original['executable']), 'exec', '--json', '--sandbox', 'read-only',
            'Software fixture: observe real qualification SessionStart context and the original public policy canary.']
        decision['qualification_scope'] = dict(decision_reference=dict(reference='fixture:explicit-'+name+'-context-authority',
            sha256=hashlib.sha256((name+'-authority').encode()).hexdigest()),
            target=copy.deepcopy(original['decision']['qualification_scope']['target']),
            attempts=[allocation] + ([] if managed_attempt is None else [managed_attempt]))
        with patch.dict(os.environ, CODEX_HOME=str(original['home'])):
            pending = onboard_coordinator.run_verified_operation(decision, directory, request)
            self.assertEqual(pending['exit_code'], 2, pending)
            decision['operation'] = 'resume'
            pending = onboard_coordinator.run_verified_operation(decision, directory, request, setup_observations=(retained,))
            self.assertEqual(pending['exit_code'], 2, pending)
            budget = dict(paths=0, bytes=0, deadline=time.monotonic()+10)
            actual = startup._acquire_canonical_membership(self.fixture.repo, authority_path=None, budget=budget)
            observation = startup._canonical_plain(actual.observation)
            observation['dependencies'] = [startup._acquired_dependency(Path(path), budget)
                for path in sorted(entry['configuration']['shared_dependencies'])]
            observation['runtime_location'] = startup._acquired_runtime_location(self.runtime_directory,
                state.PACKAGE, proposed, observation, budget)
            facts = dict(authority=dict(path=str(actual.authority_path), target=startup._canonical_plain(actual.authority_target)),
                observation=observation, client='codex', installed=dict(policy_digest=startup.policy_digest(state.PACKAGE),
                    runtime_digest=state.runtime_fingerprint(), clients=['codex'], package_root=str(state.PACKAGE)))
            evidence = observed['assessment']['bootstrap']['evidence']
            proof = dict(manifest_digest=onboarding._digest(request['manifest']), input_digest=changes._scoped_input_digest(request['manifest']),
                qualification=[dict(repository_id=key[0], checkout_id=key[1], client=key[2], requirements=[],
                    general_review=evidence, security_review=evidence, scope_observations=facts)],
                task_reconciliation=[], prior_compatibility=[], command_reconciliation=[])
            resumed = request|dict(observations=proof)
            result = onboard_coordinator.run_verified_operation(decision, directory, resumed)
            self.assertEqual(result['exit_code'], 2, result)
            self.assertEqual(result['state'], 'applied', result)
            self.assertEqual(result['activation_status'], 'active', result)
            self.assertFalse(result['qualified'], result)
            acquired = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=self.fixture.repo), state.PACKAGE)
        self.assertEqual(acquired.resolution.status, 'active', acquired.resolution)
        scope = acquired.resolution.scope
        self.assertTrue(startup.qualification_only(scope))
        self.assertTrue(scope.qualification['bootstrap_verified'])
        self.assertEqual(tuple(scope.qualification['verified']), tuple(case['id'] for case in observed['assessment']['cases']
            if case['status']=='verified'))
        self.assertEqual(startup._canonical_plain(scope.qualification['reference']), startup._canonical_plain(binding['receipt']))
        self.assertEqual(len(retained.content['assessment']['cases']), 9)
        journal = json.loads((directory/'transaction.json').read_bytes())
        self.assertIn(startup._canonical_digest(self.identity), journal['setup_receipts'])
        self.request = request
        return dict(scope=scope, decision=decision, allocation=allocation, binding=binding, source=retained, directory=directory)

    def _qualified_context(self, *, managed_pre=False, limited=False, legacy_context=False, developer_context=False):
        import copy
        import os
        import sys
        from unittest.mock import patch
        from runtime import state
        from scripts.onboard_coordinator import CoordinatorOperationOwner
        original = self._observe_original_cli(limited=limited)
        first_raw = original['rollout'].read_bytes()
        metadata = self._observe_metadata(original)
        historical = {Path(value['source']['reference'].partition('#')[0]):
            Path(value['source']['reference'].partition('#')[0]).read_bytes()
            for value in (original, metadata)} if legacy_context else {}
        published = self._publish_partial_source(metadata, original, 'native-bootstrap-publication',
            legacy_context=legacy_context)
        data = json.loads(original['settings'].read_bytes())
        data.update(thread_id='qualification-native-thread', developer_context=developer_context,
            rollout=str(original['home']/'qualification-rollout.jsonl'),
            records=[row('session_meta', dict(id='qualification-native-thread', cwd=str(self.fixture.repo))),
                row('world_state', dict(state='x' * (1024 * 1024))),
                started(), call(), output(), row('event_msg', dict(type='task_complete', turn_id='native-turn'))],
            hook_command=[sys.executable, '-B', str(state.PACKAGE/'scripts/workflow.py'), 'codex', 'SessionStart'],
            hook_payload=dict(hook_event_name='SessionStart', session_id='qualification-native-thread',
                cwd=str(self.fixture.repo), source='startup'))
        original['settings'].write_text(json.dumps(data))
        owner = CoordinatorOperationOwner(published['decision'])
        reference = published['allocation']['reference']
        with patch.dict(os.environ, CODEX_HOME=str(original['home'])):
            capture = owner.observe_native_source(self.identity, reference, codex.observe_cli_basic)
        actual = self._stop_native(owner, reference, capture, published['directory'])
        followup = dict(original, source=capture, allocation=copy.deepcopy(published['allocation']),
            decision=copy.deepcopy(published['decision']), original=actual,
            rollout=Path(data['rollout']), predecessor=startup._canonical_plain(published['binding']['receipt']),
            bootstrap_evidence=startup._canonical_plain(published['source'].content)['assessment']['bootstrap']['evidence'])
        context_source = self._observe_metadata(followup)
        facts = startup._canonical_plain(context_source['filtered'].content)
        self.assertTrue(facts['source']['observations']['startup-context'])
        self.assertEqual([case['id'] for case in facts['assessment']['cases'] if case['status']=='verified'], ['startup-context'])
        self.assertTrue(all(case['status']=='pending' for case in facts['assessment']['cases'] if case['id']!='startup-context'))
        self.assertEqual(original['rollout'].read_bytes(), first_raw)
        managed = None
        if managed_pre:
            import time
            parent = actual['result']['content']['native']['threads'][0]['id']
            reference = dict(reference='fixture:original-managed-parent-pre', sha256='f'*64)
            managed = dict(reference=reference, task=dict(client='codex', session_id=parent, task_id='managed-parent-pre'),
                native_sessions=[parent], operations=['task-start', 'task-patch', 'native-worker'],
                capability_keys=['pre-tool-enforcement', 'post-tool-handling', 'stop-ownership', 'interrupt-ownership'],
                limits=dict(deadline=time.time()+120, max_attempts=2, max_resource_units=1),
                stop_conditions=['Observe one parent guard; preserve unresolved worker ownership'],
                probe=dict(canary=str(original['canary']), patch=PATCH,
                    before_sha256=hashlib.sha256(original['canary'].read_bytes()).hexdigest()))
        final = self._publish_partial_source(context_source, followup, 'native-context-publication', managed_attempt=managed)
        from runtime import setup_observation
        with patch.dict(os.environ, CODEX_HOME=str(original['home'])), self.assertRaises(startup.StartupError):
            setup_observation.require_capability(final['scope'], ['pre-tool-enforcement'],
                task=dict(client='codex', session_id='ordinary', task_id='ordinary'), operation='task-start')
        with patch.dict(os.environ, CODEX_HOME=str(original['home'])), self.assertRaises(startup.StartupError):
            setup_observation.require_capability(final['scope'], ['pre-tool-enforcement'],
                task=dict(client='codex', session_id='ordinary', task_id='ordinary'), operation='task-patch')
        first_journal = json.loads((published['directory']/'transaction.json').read_bytes())
        self.assertIn(startup._canonical_digest(self.identity), first_journal['setup_receipts'])
        for path, before in historical.items():
            self.assertEqual(path.read_bytes(), before)
        return dict(published=final, original=followup, managed=managed)

    def test_real_owner_qualification_context_uses_actual_hook_and_retained_receipt(self):
        self._qualified_context()

    def test_real_owner_developer_context_uses_actual_hook_and_retained_receipt(self):
        self._qualified_context(developer_context=True)

    def test_real_owner_bounded_source_retains_limitation_through_qualification_context(self):
        observed = self._qualified_context(limited=True)
        content = startup._canonical_plain(observed['published']['source'].content)
        contract = content['source']['native_source_contract']
        self.assertEqual(contract['kind'], 'bounded-native-source-v1')
        self.assertEqual(contract['client_startup_descendants'], 'unknown')
        self.assertIn('may remain unobserved and cause late effects', contract['limitation'])
        self.assertNotIn('descendants_stopped', contract)
        original = observed['original']['original']
        self.assertEqual(original['source_contract'], contract)
        journal = json.loads(Path(content['source']['native_origin']['reference'].partition('#')[0]).read_bytes())
        for native in journal['native_observations'].values():
            self.assertEqual(native['source_contract'], contract)
            self.assertNotIn('descendants_stopped', native['reconciliation'])
            self.assertTrue(native['reconciliation']['reaped'])

    def test_real_owner_legacy_successor_keeps_bounded_ancestor_limitation(self):
        observed = self._qualified_context(limited=True, legacy_context=True)
        content = startup._canonical_plain(observed['published']['source'].content)
        self.assertTrue(content['source']['native_safety']['evidence'])
        self.assertTrue(content['source']['observations']['startup-context'])
        contract = content['source']['native_source_contract']
        self.assertEqual(contract['kind'], 'bounded-native-source-v1')
        self.assertEqual(contract['client_startup_descendants'], 'unknown')
        self.assertIn('may remain unobserved and cause late effects', contract['limitation'])
        self.assertNotIn('source_contract', observed['original']['original'])
        journal = json.loads(Path(content['source']['native_origin']['reference'].partition('#')[0]).read_bytes())
        for native in journal['native_observations'].values():
            self.assertNotIn('source_contract', native)
            self.assertTrue(native['reconciliation']['descendants_stopped'])

    def test_real_parent_managed_pre_and_post_use_actual_owner_receipts(self):
        self._managed_hook_receipts(child_guard=False)

    def test_real_child_patch_requires_exact_observed_turn_and_claim(self):
        self._managed_hook_receipts(child_guard=True)

    def test_child_patch_receipt_rejects_a_different_tool_at_the_same_call(self):
        self._managed_hook_receipts(child_guard=True, wrong_post_tool=True)

    def _managed_hook_receipts(self, *, child_guard, wrong_post_tool=False):
        import inspect
        import os
        import subprocess
        import sys
        import time
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import patch
        from runtime import delegation, evidence, setup_observation as source, state
        from tests.test_delegation import AdmissionTests
        from tests.test_evidence import EvidenceTests
        self.assertEqual(list(inspect.signature(codex.observe_managed_case).parameters),
            ['scope', 'task_ref', 'attempt_ref', 'case_id'], 'Managed observer still uses a D4 process')
        prepared_scope = self._qualified_context(managed_pre=True)
        scope = prepared_scope['published']['scope']; original = prepared_scope['original']; allocation = prepared_scope['managed']
        identity = dict(allocation['task'], scope_ref='fixture:actual-managed-parent-task')
        data = json.loads(original['settings'].read_bytes())
        parent = identity['session_id']
        parent_thread = dict(data['responses']['thread/read']['thread'], id=parent,
            modelProvider='fixture-provider', model='fixture-model', reasoningEffort='high')
        data['threads'] = {parent:parent_thread}
        data['responses']['model/list'] = dict(data=[dict(model='fixture-model', defaultReasoningEffort='high',
            supportedReasoningEfforts=[dict(reasoningEffort='high')])], nextCursor=None)
        original['settings'].write_text(json.dumps(data))
        which = codex.shutil.which
        def fake_transport(name, *args, **kwargs):
            return str(original['executable']) if name=='codex' else which(name, *args, **kwargs)
        with patch.dict(os.environ, CODEX_HOME=str(original['home'])), patch.object(codex.shutil, 'which', side_effect=fake_transport):
            workflow = [sys.executable, '-I', '-B', str(state.PACKAGE/'scripts/workflow.py'), 'codex']
            if child_guard and not wrong_post_tool:
                ordinary = dict(hook_event_name='PreToolUse', session_id='ordinary-session', cwd=str(self.fixture.repo),
                    turn_id='ordinary-turn', tool_name='apply_patch', tool_use_id='ordinary-call',
                    tool_input=dict(command=PATCH))
                with state._database(scope) as db:
                    generation = db.execute('select generation from checkouts').fetchone()[0]
                for kind in ('PreToolUse', 'PostToolUse', 'PostToolUse'):
                    event = ordinary if kind == 'PreToolUse' else ordinary | dict(hook_event_name=kind, tool_response='Done')
                    result = subprocess.run([*workflow, kind], input=json.dumps(event), text=True, capture_output=True,
                        check=True, timeout=5)
                    self.assertEqual(result.stdout, '')
                with state._database(scope) as db:
                    self.assertEqual(db.execute('select generation from checkouts').fetchone()[0], generation + 1)
            state.start_task(scope, identity|dict(mode='editable', decision_ref='user:qualification',
                attempt_ref=allocation['reference']))
            native = AdmissionTests('runTest')
            native.scope, native.identity, native.api = scope, identity, delegation
            native.repo, native.root = self.fixture.repo, self.fixture.base
            review = dict(applicability='required', reason='Software qualification fixture', independent=True)
            EvidenceTests.plan(SimpleNamespace(api=evidence, scope=scope, request=native.request),
                decision_ref='user:qualification', checks=[dict(id='unit', argv=[sys.executable, '-B', '-c', 'pass'], cwd='.', timeout=3,
                    env=dict(PATH=os.defpath), effects=dict(kind='read-only', inspection_ref='fixture:read-only-check', network=False,
                        production=False, publish=False, migrations=False), baseline=dict(status='passed', reference='fixture:baseline'),
                    dependencies=['public-canary.txt'])])
            controls = dict(version=1, client_surface='codex-cli', native_version='0.165.0', parent_session_id=parent,
                dispatch_controls=dict(spawn=True, explicit_model=True, explicit_effort=True, fork_turns_none=True))
            catalog = codex.discover_native_workers(scope, controls)
            native.catalog = catalog
            native.cache_request = dict(client_surface='codex-cli', applicability=dict(client_version=catalog['native_version'],
                capability_fingerprint=catalog['capability_digest']))
            claim = delegation.claim_guidance(scope, native.cache_request|dict(trigger=dict(kind='routine', id='native-parent-fixture')))
            guidance = dict(schema_version=1, client_surface='codex-cli', checked_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                applicability=native.cache_request['applicability'], sources=[dict(id='official',
                    url='https://example.invalid/software-fixture', sha256='b'*64)], recommendations=[dict(id='bounded',
                    work='Bounded software transport fixture', risk='No model request', models=[], conditions=['Fixture catalog only'],
                    limitations=['No actual native qualification'], source_ids=['official'])])
            delegation.record_guidance(scope, native.cache_request|dict(token=claim['token'], generation=claim['generation'],
                coordinator_ref='fixture:independently-verified-software-guidance', guidance=guidance))
            claims = [dict(kind='path', key=str(original['canary']), mode='write' if child_guard else 'read')]
            plan = native.plan_value(jobs=[dict(id='edit', role='software-engineer', claims=claims, dependencies=[], max_attempts=1)])
            plan['limits'].update(max_attempts=2, max_resource_units=1, max_parallel_workers=1, host_capacity=1,
                deadline=int(allocation['limits']['deadline']))
            native.plan(plan)
            request = native.prepare_request(brief=dict(objective='Observe the approved public canary', context='Preserve original qualification ownership',
                instructions=['Use only the assigned canary scope'], files=['public-canary.txt'], resources=claims, dependencies=[],
                acceptance=['Report only observed facts'], checks=['unit'], self_review='Read assignment and stop conditions',
                stop_conditions=['Retain lifecycle uncertainty']))
            request['selection'].update(model='fixture-model', effort='high')
            prepared = delegation.prepare_assignment(scope, request)
            child = 'fixture-child-'+prepared['attempt_id']
            spawn_turn, spawn_item, child_turn = 'actual-parent-spawn-turn', 'actual-spawn-item', 'actual-child-turn'
            data = json.loads(original['settings'].read_bytes())
            child_rollout = original['home']/'child-rollout.jsonl'
            child_rollout.write_bytes(encoded([row('session_meta', dict(id=child, cwd=str(self.fixture.repo))),
                row('world_state', dict(state='x' * (1024 * 1024)))]))
            child_rollout.chmod(0o600)
            data['threads'][child] = dict(id=child, cwd=str(self.fixture.repo), path=str(child_rollout), source=dict(subAgent=dict(thread_spawn=dict(parent_thread_id=parent))),
                parentThreadId=parent, originator='codex_exec', cliVersion='0.165.0', modelProvider='fixture-provider',
                model='fixture-model', reasoningEffort='high')
            data['responses']['thread/items/read'] = dict(data=[dict(turnId=spawn_turn, item=dict(id=spawn_item,
                type='collabAgentToolCall', tool='spawnAgent', status='completed', senderThreadId=parent,
                receiverThreadIds=[child], prompt=prepared['prompt'], model='fixture-model', reasoningEffort='high'))])
            data['responses']['thread/turns/list'] = dict(data=[dict(id=child_turn, status='inProgress', error=None)])
            original['settings'].write_text(json.dumps(data))
            payload = dict(hook_event_name='PreToolUse', session_id=child if child_guard else parent, cwd=str(self.fixture.repo),
                turn_id=child_turn if child_guard else 'actual-parent-pre-turn',
                tool_name='apply_patch', tool_use_id='actual-pre-call', tool_input=dict(command=PATCH),
                model='fixture-model', permission_mode='default',
                transcript_path=str(child_rollout if child_guard else original['rollout']))

            def assert_denied(event):
                result = subprocess.run([*workflow, 'PreToolUse'], input=json.dumps(event),
                    text=True, capture_output=True, check=True, timeout=5)
                self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'], 'deny')

            if child_guard:
                assert_denied(payload)
            observed = delegation.observe_assignment(scope, native.request(attempt_id=prepared['attempt_id'],
                parent_turn_id=spawn_turn, spawn_item_id=spawn_item))
            self.assertEqual(observed['attempt_id'], prepared['attempt_id'])
            with state._database(scope) as db:
                assignment = next(item for item in delegation._ledger(scope, db, assignment_id=prepared['attempt_id'])
                    if item['id']==prepared['attempt_id'])
                before = evidence._read(scope, assignment['body']['qualification_before'], db=db)
            self.assertEqual(before['identity']['session_id'], parent)
            before_bytes = original['rollout'].read_bytes()
            def task_ref(event):
                request = native.request(attempt_id=prepared['attempt_id'], parent_turn_id=spawn_turn,
                    spawn_item_id=spawn_item, turn_id=event['turn_id'], call_id=event['tool_use_id'])
                return request | dict(stream='child') if child_guard else request

            if child_guard:
                state.update_task(scope, native.request(state='WORKING', reason='Run the claimed child canary patch',
                    reference='user:qualification'))
                for changes in (dict(session_id='foreign-child'), dict(turn_id='foreign-turn'),
                        dict(tool_input=dict(command=PATCH.replace('public-canary.txt', 'unclaimed.txt'))),
                        dict(tool_input=dict(command=PATCH.replace('+after', '+different'))),
                        dict(tool_input=PATCH)):
                    with self.subTest(changes=changes):
                        assert_denied(payload | changes)
                self.assertEqual(original['canary'].read_text(), 'before\n')

                deadline = time.monotonic() + 60

                def expire_resolution(_scope, _db, _event, *, budget):
                    # The lookup is bounded by the hook deadline; simulate spending all of it.
                    self.assertEqual(budget['deadline'], deadline)
                    raise startup.StartupError('fixture ownership lookup expired')

                with patch.object(delegation, '_qualified_child_patch', side_effect=expire_resolution) as resolver:
                    result = state.process_event(scope, codex.normalize_runtime(payload), deadline=deadline)
                resolver.assert_called_once()
                self.assertEqual(result.action, 'deny')
                data.update(rollout=str(child_rollout), child_patch=True)
            else:
                data.update(tool_hook_command=[*workflow, 'PreToolUse'], tool_hook_payload=payload)
                original['settings'].write_text(json.dumps(data))
                subprocess.run([str(original['executable']), 'exec', '--json', 'software-parent-pre'], cwd=self.fixture.repo,
                    check=True, capture_output=True, timeout=10)
                self.assertTrue(original['rollout'].read_bytes().startswith(before_bytes))
                pre = codex.observe_managed_case(scope, task_ref(payload), allocation['reference'], 'pre-tool-enforcement')
                pre_content = doctor._native_witness_content(pre)
                self.assertEqual(len(pre_content['observations']['pre-tool-enforcement']), 4)
                self.assertEqual(original['canary'].read_text(), 'before\n')
                state.update_task(scope, native.request(state='WORKING', reason='Observe successful parent tool completion',
                    reference='user:qualification'))
                payload = dict(payload, turn_id='actual-parent-post-turn', tool_use_id='actual-parent-post-call')
            post = dict(payload, hook_event_name='PostToolUse', tool_response='Success. Updated the following files: M public-canary.txt')
            other = dict(post, tool_name='fixture_read', tool_use_id='actual-unrelated-post-call', tool_input={}, tool_response='Public read completed')
            if wrong_post_tool:
                post.update(tool_name='fixture_read', tool_input={})
            data.update(tool_hook_command=[*workflow, 'PreToolUse'], tool_hook_payload=payload, post_tool_hook_command=[*workflow, 'PostToolUse'], post_tool_hook_payload=post,
                other_post_payload=other, approved_write=dict(path=str(original['canary']),
                    before_sha256=hashlib.sha256(original['canary'].read_bytes()).hexdigest(), after='after\n'))
            original['settings'].write_text(json.dumps(data))
            completed = subprocess.run([str(original['executable']), 'exec', '--json', 'software-managed-post'],
                cwd=self.fixture.repo, capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            if wrong_post_tool:
                with self.assertRaises(startup.StartupError):
                    source.managed_observation_readback(scope, task_ref(payload), allocation['reference'],
                        case_id='post-tool-handling')
                self.assertEqual(original['canary'].read_text(), 'after\n')
                return
            witness = codex.observe_managed_case(scope, task_ref(payload), allocation['reference'], 'post-tool-handling')
            content = doctor._native_witness_content(witness)
            self.assertEqual(len(content['observations']['post-tool-handling']), 4)
            actual = startup._canonical_plain(source.managed_observation_readback(scope, task_ref(payload),
                allocation['reference'], case_id='post-tool-handling'))
            self.assertEqual(actual['post_receipts_count'], 2)
            self.assertEqual(actual['generation']-before['generation'], 2)
            self.assertEqual(actual['receipt']['kind'], 'PostToolUse')
            self.assertFalse(actual['receipt']['conflicted'])
            if child_guard:
                self.assertEqual(actual['receipt']['session_id'], child)
                self.assertEqual(actual['receipt']['turn_id'], child_turn)
                self.assertEqual(actual['assignment_digest'], assignment['data']['assignment']['digest'])
                with self.assertRaises(startup.StartupError):
                    source.managed_observation_readback(scope, task_ref(payload) | dict(turn_id='foreign-turn'),
                        allocation['reference'], case_id='post-tool-handling')
                state.update_task(scope, native.request(state='PAUSED', reason='Hold after canary',
                    reference='user:qualification'))
                replay = subprocess.run([*workflow, 'PostToolUse'], input=json.dumps(post), text=True,
                    capture_output=True, check=True, timeout=5)
                self.assertEqual(replay.stdout, '')
                with state._database(scope) as db:
                    self.assertEqual(db.execute('select generation from checkouts').fetchone()[0], actual['generation'])
            self.assertEqual(original['canary'].read_text(), 'after\n')
            with state._database(scope) as db:
                current = next(item for item in delegation._ledger(scope, db, assignment_id=prepared['attempt_id'])
                    if item['id']==prepared['attempt_id'])
            self.assertNotEqual(current['data']['status'], 'settled')


if __name__ == '__main__':
    unittest.main()
