"""Disposable canonical D4 fixtures from the frozen E source; no native qualification."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from runtime import changes, evidence, onboarding, review_storage, startup, state
from runtime.startup import StartupError

PACKAGE = Path(__file__).resolve().parents[1]
_ACTUAL_ACQUIRE = startup.acquire_project_entry


class OnboardingFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe-onboarding-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        # Automatic Inspect must never discover the developer's machine storage.
        self.isolated_home = self.base / "isolated-home"
        self.home_patch = patch.dict(os.environ, HOME=str(self.isolated_home))
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.repo = self.base / "target"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "README.md").write_text("product\n")
        (self.repo / "AGENTS.md").write_text("product rules\nlegacy process\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "fixture")
        self.directory = self.base / "plan"
        self.directory.mkdir(mode=0o700)
        self.source = self.directory / "catalog.md"
        self.source.write_text("# Complete catalog\nfixture\nnew-tool\n")
        self.source.chmod(0o600)
        self.record = {"schema_version": 1, "clients": ["codex"],
            "toolkit": {"commit": "a" * 40, "origin": "https://example.invalid/toolkit",
                "acquisition": "online", "provenance": "fixture retrieval, not live evidence",
                "trust": "recorded", "files": [{"path": "catalog.md", "local_path": str(self.source),
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(), "required": True}],
                "catalog": [{"id": "fixture", "source": "catalog.md", "section": "fixture",
                    "stage": "project_onboarding", "mandatory": True, "dependencies": []},
                    {"id": "new-tool", "source": "catalog.md", "section": "new-tool",
                    "stage": "project_onboarding", "mandatory": False, "dependencies": ["fixture"]}]},
            "required_sources": ["catalog.md"]}


    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo),
            *args], check=True, capture_output=True, env=env, timeout=5).stdout


    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime.onboarding"),
                             "D3 onboarding helper is missing")
        from runtime import onboarding
        return onboarding


    def inspect(self):
        return self.module().inspect_project(self.repo, self.directory, self.record)


    def project_record(self, root=None, members=None, kind="standalone", files=None):
        root = root or self.repo
        if kind == "umbrella" and self.directory.is_relative_to(root):
            private = tempfile.TemporaryDirectory(prefix="vibe-project-plan-")
            self.addCleanup(private.cleanup)
            directory = Path(private.name).resolve() / "plan"
            self.directory.rename(directory)
            self.directory = directory
            self.source = directory / "catalog.md"
            self.record["toolkit"]["files"][0]["local_path"] = str(self.source)
        authority = root / ".vibe-coding-workflow/project.json"
        return copy.deepcopy(self.record) | {"project_scope": {
            "kind": kind, "project_root": str(root),
            "member_checkouts": [str(p) for p in (members or [self.repo])],
            "membership": {"include": ["."], "exclude": [], "future_join": False,
                           "authorization": "fixture:explicit-scope"},
            "authority": {"path": str(authority), "sha256":
                hashlib.sha256(authority.read_bytes()).hexdigest() if authority.exists() else "absent",
                "reference": "fixture:pending-adoption"}, "project_files": files or []}}


    def second_repository(self):
        first = self.repo
        self.repo = self.base / "second"
        self.repo.mkdir()
        try:
            self.git("init", "-q")
            (self.repo / "second.txt").write_text("second product\n")
            self.git("add", ".")
            self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", "second fixture")
            return self.repo
        finally:
            self.repo = first


    def plan(self):
        return json.loads((self.directory / "plan.json").read_text())


    def proposal(self, revision):
        rows = []
        for item in self.plan()["requirements"]:
            rows.append({**item, "applicability": "applicable", "reason": "required assessment",
                "current_adoption": "absent", "qualification_evidence": [], "outcome": "planned",
                "actions": ["write-rules"], "scope": "checkout", "risk": "review before D4 apply",
                "verification": "D4 verification pending", "blocker": "", "resumption": "",
                "decision_ids": []})
        return {"expected_revision": revision,
            "inventory": {key: {"facts": [{"summary": "observed fixture " + key, "evidence": ["inventory"]}], "assumptions": []}
                for key in ("product", "critical_flows", "stack", "quality_commands", "services",
                            "client_capabilities", "merge_feasibility", "instructions", "integrations")}
                | {"documents": {str(i): {"path": None, "reason": "fixture missing"} for i in range(9)}},
            "decisions": [], "coverage": rows, "actions": [{"id": "write-rules", "dependencies": [],
                "kind": "write", "target": "AGENTS.md", "section": "legacy process",
                "before": hashlib.sha256((self.repo / "AGENTS.md").read_bytes()).hexdigest(),
                "text": "product rules\nnew process\n", "after": hashlib.sha256(b"product rules\nnew process\n").hexdigest(),
                "scope": "checkout", "authorization": "user:review-only",
                "verification": "D4 checks resulting sections", "preservation_exceptions": ["product rules"],
                "decision_ids": [], "backup_required": True}],
            "evidence": [{"id": "inventory", "kind": "observation", "summary": "fixture inventory observations",
                "scope": "checkout", "inputs": ["README.md"], "reference": "disposable fixture"}],
            "semantic_assessment": {"complete": True, "catalog_complete": True,
                "conflicts_checked": True, "instructions_checked": True, "source_provenance_checked": True,
                "native_evaluation": {"method": "disposable native harness", "smoke_cases": ["startup"]}}}


    def qualified(self, owner, local):
        parts = (["project", owner["project_id"]] if owner["kind"] == "project" else
                 ["repository", owner["repository_id"], owner["checkout_id"]])
        return "/".join(part.replace("~", "~0").replace("/", "~1") for part in [*parts, local])


    def scoped_proposal(self):
        plan = self.plan()
        result = {"expected_revision": plan["revision"], "assessments": copy.deepcopy(plan["assessments"])}
        for assessment in [result["assessments"]["project"], *result["assessments"]["repositories"]]:
            if assessment is None:
                continue
            owner = assessment["owner"]
            root = Path(owner["root"])
            payload = self.proposal(plan["revision"])
            payload.pop("expected_revision")
            action = payload["actions"][0]
            action.update(owner=copy.deepcopy(owner), cwd=None)
            target = root / "AGENTS.md"
            action["before"] = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else "absent"
            ref = lambda local: self.qualified(owner, local)
            for section in payload["inventory"].values():
                if "facts" in section:
                    for fact in section["facts"]:
                        fact["evidence"] = [ref("inventory")]
            for row in payload["coverage"]:
                row["actions"] = [ref("write-rules")]
            snapshot = assessment["identity"].get("snapshot", assessment["identity"])
            self.assertIsInstance(snapshot,dict,
                'D3 assessment has no accepted snapshot; blockers: '+repr(plan.get('blockers',[])))
            self.assertTrue(snapshot.get('paths'),
                'D3 assessment has no observed input paths; blockers: '+repr(plan.get('blockers',[])))
            payload["evidence"][0]["inputs"] = [snapshot["paths"][0]]
            if owner["kind"] == "project":
                payload["inventory"] = {"instructions": payload["inventory"]["instructions"],
                    "documents": {"shared": {"path": "AGENTS.md", "reason": "fixture shared instruction"}}}
                payload["coverage"] = []
                payload["semantic_assessment"].pop("catalog_complete")
                payload["semantic_assessment"].pop("native_evaluation")
                action["scope"] = "shared"
                payload["evidence"][0].update(kind="configuration", scope="shared")
            for evidence in payload["evidence"]:
                evidence["input_digest"] = self.module()._evidence_digest(owner, snapshot, plan["package_pins"],
                    plan["sources"]["observed"], evidence["inputs"])
            assessment.update(payload)
        project = result["assessments"]["project"]
        if project:
            shared = self.qualified(project["owner"], "write-rules")
            for repository in result["assessments"]["repositories"]:
                repository["actions"][0]["dependencies"] = [shared]
                repository["coverage"][0]["actions"].append(shared)
        return result


    def scoped_inspect(self, umbrella=False, linked=False):
        if umbrella:
            other = self.second_repository()
            (self.base / "AGENTS.md").write_text("shared fixture rules\n")
            record = self.project_record(self.base, [self.repo, other], "umbrella", ["AGENTS.md"])
            cwd = self.base
        elif linked:
            cwd = self.base / "linked"
            self.git("worktree", "add", "--detach", str(cwd), "HEAD")
            record = self.project_record(members=[cwd])
        else:
            cwd, record = self.repo, self.project_record()
        self.module().inspect_project(cwd, self.directory, record)
        return cwd, record



class ScopedBirthFixture(OnboardingFixture):
    def setUp(self):
        super().setUp()
        from runtime import changes
        self.changes = changes
        lock = patch.object(changes, "LOCK_ROOT", self.base / "locks")
        lock.start()
        self.addCleanup(lock.stop)


    def scoped_request(self, *, umbrella=False, linked=False, existing=False, prepared=False):
        if not existing and not prepared:
            self.scoped_inspect(umbrella=umbrella, linked=linked)
        if not prepared:
            onboarding.record_plan(self.directory, self.scoped_proposal())
        plan = self.plan()
        ref = dict(reference="fixture:trusted-coordinator", sha256="a" * 64)
        repositories, actions, affected, qualification = [], [], [], []
        owners = [dict(owner=a["owner"], identity=a["identity"]) for a in onboarding._assessments(plan["assessments"])]
        for assessment in onboarding._assessments(plan["assessments"]):
            owner = assessment["owner"]
            for original in assessment["actions"]:
                file = Path(owner["root"]) / original["target"]
                before = self.changes._fingerprint(Path(owner["root"]), original["target"])
                actions.append(dict(id=onboarding._qualified(owner, original["id"]), owner=owner,
                    group="configuration", resources=["checkout"] if owner["kind"] == "repository" else ["project"],
                    preconditions=[], effects=[dict(path=original["target"], before=before,
                        after=dict(sha256=original["after"], mode=0o644))], recovery="file-preimages", execution=None))
            if owner["kind"] != "repository": continue
            identity = assessment["identity"]
            configuration = dict(policy_digest=plan["package_pins"]["policy_digest"],
                runtime_digest=plan["package_pins"]["runtime_digest"], runtime=None, shared_dependencies=[], evidence=[ref])
            entry = dict(client="codex", configuration=configuration, activation=dict(status="configured",
                qualification="pending", accepted_scope_digest=None, transaction_id=None, evidence=[], qualification_binding=None))
            repositories.append(dict(repository_id=owner["repository_id"], primary_root=owner["primary_root"],
                git_common_dir=owner["git_common_dir"], binding={k:identity["binding"][k] for k in ("primary_root","git_common_dir")},
                membership="joined", checkouts=[dict(checkout_id=owner["checkout_id"], root=owner["root"], git_dir=owner["git_dir"],
                    binding={k:identity["binding"][k] for k in ("root","git_dir")}, clients=[entry])]))
            member = dict(repository_id=owner["repository_id"], checkout_id=owner["checkout_id"], client="codex")
            affected.append(member)
            qualification.append(member | dict(requirements=[r["id"] for r in assessment["coverage"] if r["mandatory"] and r["applicability"] == "applicable"], security_required=True))
        common_directories=[repository['git_common_dir'] for repository in repositories]
        self.assertEqual(len(common_directories),len(set(common_directories)),
            'D3 requires one selected action checkout per Repository; use sequential scoped operations')
        authority = next(a for a in plan["identity"]["authority"] if a["path"] == plan["project_scope"]["authority"]["path"])
        record = dict(schema_version=2, revision=1, project_id=plan["identity"]["project_id"],
            project_root=plan["identity"]["project_root"], kind=plan["identity"]["kind"], project_binding=plan["identity"]["binding"],
            membership={k:plan["project_scope"]["membership"][k] for k in ("include","exclude","future_join")} | {"authorization":ref},
            shared_policy=[], repositories=repositories)
        prior=authority["record"]
        if prior and prior.get("schema_version")==2:
            retained=copy.deepcopy(prior)
            for replacement in repositories:
                previous=next(r for r in retained["repositories"] if r["repository_id"]==replacement["repository_id"])
                selected_ids={c["checkout_id"] for c in replacement["checkouts"]}
                previous["checkouts"]=[c for c in previous["checkouts"] if c["checkout_id"] not in selected_ids]+replacement["checkouts"]
            retained["membership"].update({k:plan["project_scope"]["membership"][k] for k in ("include","exclude","future_join")})
            retained["revision"] += 2 if any(e[2]["activation"]["status"]=="active" for k,e in self.changes._scoped_entries(prior).items() if k in {tuple(x.values()) for x in affected}) else 1
            record=retained
        manifest = dict(schema_version=2, transaction_id="scoped-fixture", plan={k:plan[k] for k in ("plan_id","revision","review_digest","current_input_digest")},
            pins=dict(package_pins=plan["package_pins"],toolkit_commit=plan["sources"]["declared"]["toolkit"]["commit"],sources_digest=onboarding._digest(plan["sources"])),
            authorization_reference=ref["reference"], approved_scope=[a["id"] for a in actions]+["enrollment"],
            owners=owners, actions=actions, enrollment=dict(record=record,before=dict(revision=prior.get("revision") if prior else None,raw_sha256=authority["fingerprint"].get("sha256"),target=authority["target"]),
                affected=sorted(affected,key=lambda x:tuple(x.values())),requires=[a["id"] for a in actions],qualification=qualification))
        return dict(schema_version=2,manifest=manifest,approval=dict(reference=ref["reference"],evidence=[ref],manifest_digest=onboarding._digest(manifest)))



class CanonicalBirthFixture(unittest.TestCase):
    def prepare(self, *, umbrella=False, history=True, linked=False, peer=False):
        from runtime import review_storage
        f = ScopedBirthFixture(methodName='runTest')
        self.addCleanup(f.doCleanups); f.setUp()
        private = tempfile.TemporaryDirectory(prefix='vcw-birth-private-')
        self.addCleanup(private.cleanup)
        private_root = Path(private.name).resolve()
        lock_setting = patch.object(changes, 'LOCK_ROOT', private_root / 'locks')
        lock_setting.start(); self.addCleanup(lock_setting.stop)
        machine = private_root / 'machine' / 'machine.json'
        setting = patch.object(review_storage, '_machine_config_path', return_value=machine)
        setting.start(); self.addCleanup(setting.stop)
        if history:self.assertTrue(review_storage.prepare_storage(private_root / 'private')['ok'])
        self.assertFalse(peer and linked,'Shared distinct-Repository birth and selected linked birth are separate topologies')
        request = f.scoped_request(umbrella=umbrella or peer, linked=linked)
        if linked: f.repo = Path(request["manifest"]["enrollment"]["record"]["repositories"][0]["checkouts"][0]["root"])
        if not history:self.assertTrue(review_storage.prepare_storage(private_root / 'private')['ok'])
        record = request['manifest']['enrollment']['record']
        directory = private_root / 'canonical-state'
        for repository in record['repositories']:
            for checkout in repository['checkouts']:
                for client in checkout['clients']:
                    client['configuration']['runtime'] = dict(schema_version=3, state_dir=str(directory))
        request['approval']['manifest_digest'] = onboarding._digest(request['manifest'])
        decision = self.decision(f, request, 'apply', ())
        with review_storage._capture_birth_history(record) as (history, reobserve):
            reobserve()
            intent = changes._birth_intent(f.plan(), request['manifest'], history, plan_dir=f.directory)
        decision['creation'] = dict(version=1, intent=intent, resume=None)
        return f, request, decision, directory


    def invoke(self, prepared):
        f, request, decision, directory = prepared
        from scripts.onboard_coordinator import run_verified_operation
        return run_verified_operation(decision, f.directory, copy.deepcopy(request))


    def context(self, f):
        return changes._recovery_context(changes._load_journal(f.directory)['birth']['allocation']['authority'])


    def decision(self, fixture, approved, operation, contexts):
        record = approved['manifest']['enrollment']['record']
        subjects = []
        for repository in record['repositories']:
            for checkout in repository['checkouts']:
                for client in checkout['clients']:
                    identity = startup.canonical_scope_projection(startup.decode_project_record(record),
                        repository_id=repository['repository_id'], checkout_id=checkout['checkout_id'], client=client['client'])
                    subjects.append(dict(project_id=record['project_id'], repository_id=repository['repository_id'],
                        checkout_id=checkout['checkout_id'], client=client['client'],
                        accepted_identity_digest=startup._canonical_digest(evidence._canonical_plain(identity))))
        return dict(decision_reference=dict(reference='fixture:prior-reviewed-standing-decision', sha256='c' * 64),
            operation=operation, plan_directory=dict(path=str(fixture.directory),
                binding=onboarding._filesystem_binding(fixture.directory), parents=review_storage._parents(fixture.directory)),
            plan_digest=onboarding._digest(fixture.plan()),
            approved=copy.deepcopy({key: approved[key] for key in ('manifest', 'approval')}),
            bookkeeping_scope=dict(subjects=subjects,
                authorities=[evidence._canonical_plain(context.authority_set[0]) for context in contexts]))


    def assert_applied_configured(self, prepared, result):
        fixture,request,_,_=prepared
        self.assertEqual(result.get('state'),'applied',result)
        self.assertEqual(result.get('exit_code'),2,result)
        self.assertEqual(result.get('activation_status'),'configured',result)
        self.assertFalse(result.get('qualified'),result)
        self.assertEqual({item['id'] for item in result.get('blocked',[])},{'qualification'},result)
        journal=changes._load_journal(fixture.directory)
        self.assertEqual(journal['state'],'applied',result)
        self.assertFalse(journal['blocked'],journal['blocked'])
        path=Path(request['manifest']['enrollment']['record']['project_root'])/startup.PROJECT_FILE
        self.assertEqual(json.loads(path.read_bytes()),request['manifest']['enrollment']['record'])

    def qualify_members(self, fixture, path):
        """Test-only owner observation for each actual published member; never native qualification."""
        value = json.loads(path.read_bytes())
        fixture.test_observations={}
        for repository in value['repositories']:
            for checkout in repository['checkouts']:
                entry=checkout['clients'][0]
                projection = startup.canonical_scope_projection(startup.decode_project_record(value),
                    repository_id=repository['repository_id'], checkout_id=checkout['checkout_id'], client='codex')
                scope_digest = startup._canonical_digest(startup._canonical_plain(projection))
                info = state.PACKAGE.stat()
                hook = state.PACKAGE / 'hooks/codex.json'
                proof = dict(reference='fixture:synthetic-trusted-owner-only', sha256='e' * 64)
                binding = dict(client='codex', surface='fixture-only', version='1', configuration_digest='f' * 64,
                    hook_owner=dict(root=str(state.PACKAGE), binding=dict(device=info.st_dev, inode=info.st_ino),
                        source=str(hook), sha256=hashlib.sha256(hook.read_bytes()).hexdigest()),
                    accepted_scope_digest=scope_digest, receipt=proof)
                entry['activation'].update(status='active', qualification='qualified', accepted_scope_digest=scope_digest,
                    transaction_id='fixture', evidence=[proof], qualification_binding=binding)
                observation = dict(current={key:copy.deepcopy(binding[key]) for key in
                    ('client','surface','version','configuration_digest','hook_owner')}, receipt=proof,
                    binding=copy.deepcopy(binding), provenance=dict(kind='trusted-coordinator', **proof))
                fixture.test_observations[checkout['root']]=observation
        path.write_text(json.dumps(value))
        observation=fixture.test_observations[str(fixture.repo)]
        event = SimpleNamespace(client='codex', cwd=fixture.repo, session_id='canonical-session')
        self.assertIsNone(startup.acquire_project_entry(event, state.PACKAGE).resolution.scope)
        self.assertEqual(startup.acquire_project_entry(event, state.PACKAGE, qualifier=observation).resolution.status, 'conflict')
        acquired = startup.acquire_project_entry(event, state.PACKAGE, qualifier=lambda record, facts: observation)
        self.assertEqual(acquired.resolution.status, 'active', acquired.resolution)
        fixture.member_acquisitions = {root: startup.acquire_project_entry(
            SimpleNamespace(client="codex", cwd=Path(root)), state.PACKAGE,
            qualifier=lambda record, facts, value=value: value)
            for root, value in fixture.test_observations.items()}
        for member in fixture.member_acquisitions.values():
            self.assertEqual(member.resolution.status,"active",member.resolution)
        return fixture, acquired, observation

    def accepted_birth(self, *, linked=False, peer=False):
        """Real D4 birth, followed by explicit synthetic trusted-owner qualification."""
        prepared=self.prepare(linked=linked,peer=peer)
        self.assert_applied_configured(prepared,self.invoke(prepared))
        fixture=prepared[0]
        path=Path(prepared[1]['manifest']['enrollment']['record']['project_root'])/startup.PROJECT_FILE
        return self.qualify_members(fixture,path)

    def prepare_common_git_update(self, *, qualified=True):
        """Expose the real reviewed update before publication for admission/recovery tests."""
        if qualified:
            fixture,original_acquisition,_=self.accepted_birth()
        else:
            prepared=self.prepare()
            self.assert_applied_configured(prepared,self.invoke(prepared))
            fixture=prepared[0]
            original_acquisition=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=fixture.repo),state.PACKAGE)
            self.assertEqual(original_acquisition.resolution.status,'configured')
            self.assertIsNone(original_acquisition.resolution.scope)
        original_authority=Path(fixture.plan()['identity']['project_root'])/startup.PROJECT_FILE
        original_record=original_authority.read_bytes()
        context=self.context(fixture)
        authority=state._authority_identity(context.database)
        with evidence._context_database(context) as db:
            original=state._source_baseline(None,db,context=context)
            baseline={key:evidence._canonical_plain(original[key]) for key in ('reference','content')}
            certificate=evidence._canonical_plain(evidence._read_birth_handoff(context,db)['certificate'])
        original_plan=fixture.directory
        fixture.directory=fixture.base/'linked-enrollment-plan'
        fixture.directory.mkdir(mode=0o700)
        source_record=copy.deepcopy(fixture.record)
        retained_sources={}
        for item in source_record['toolkit']['files']:
            original_source=Path(item['local_path'])
            raw=original_source.read_bytes();info=original_source.stat()
            retained_sources[original_source]=(raw,info.st_dev,info.st_ino,info.st_mode,info.st_mtime_ns)
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item['sha256'])
            copied_source=fixture.directory/item['path']
            copied_source.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            copied_source.write_bytes(raw);copied_source.chmod(0o400)
            item['local_path']=str(copied_source)
        fixture.record=source_record
        fixture.source=Path(source_record['toolkit']['files'][0]['local_path'])
        request=fixture.scoped_request(linked=True)
        for original_source,retained in retained_sources.items():
            info=original_source.stat()
            self.assertEqual((original_source.read_bytes(),info.st_dev,info.st_ino,info.st_mode,info.st_mtime_ns),retained)
        record=request['manifest']['enrollment']['record']
        for repository in record['repositories']:
            for checkout in repository['checkouts']:
                for entry in checkout['clients']:
                    entry['configuration']['runtime']=dict(schema_version=3,state_dir=str(context.directory))
        request['manifest']['transaction_id']='fixture-linked-member-update'
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        decision=self.decision(fixture,request,'apply',(context,))
        update=(fixture,request,decision,context.directory)
        return update,original_acquisition,context,dict(authority=authority,baseline=baseline,certificate=certificate,
            original_plan=original_plan,authority_path=original_authority,original_record=original_record)

    def accepted_common_git_members(self):
        """Two one-action-checkout operations retain one Project/Repository/store."""
        update,original_acquisition,context,history=self.prepare_common_git_update()
        fixture=update[0];original_scope=startup._entry_scope(original_acquisition)
        authority,baseline,certificate=(history[key] for key in ('authority','baseline','certificate'))
        self.assert_applied_configured(update,self.invoke(update))
        self.assertEqual(state._authority_identity(context.database),authority)
        with evidence._context_database(context) as db:
            retained=state._source_baseline(None,db,context=context)
            self.assertEqual({key:evidence._canonical_plain(retained[key]) for key in ('reference','content')},baseline)
            self.assertEqual(evidence._canonical_plain(evidence._read_birth_handoff(context,db)['certificate']),certificate)
        fixture,acquired,observation=self.qualify_members(fixture,original_scope.authority_path)
        members=[startup._entry_scope(value) for value in fixture.member_acquisitions.values()]
        self.assertEqual(len(members),2)
        self.assertEqual({member.project_id for member in members},{original_scope.project_id})
        self.assertEqual({member.repository_id for member in members},{original_scope.repository_id})
        self.assertEqual({member.git_common_dir for member in members},{original_scope.git_common_dir})
        self.assertEqual({member.runtime.state_dir for member in members},{original_scope.runtime.state_dir})
        self.assertEqual(len({member.checkout_id for member in members}),2)
        self.assertNotEqual(evidence._canonical_plain(acquired.resolution.scope.authority_target),
            evidence._canonical_plain(original_scope.authority_target))
        fixture.initial_birth_plan=history['original_plan']
        return fixture,acquired,observation


def fresh_canonical(testcase):
    """Return a genuinely born scope plus explicit software-only trusted-owner proof."""
    owner=CanonicalBirthFixture('runTest')
    testcase.addCleanup(owner.doCleanups)
    if getattr(testcase,"_canonical_common_git",False):return owner.accepted_common_git_members()
    return owner.accepted_birth(linked=getattr(testcase, "_canonical_linked", False),
        peer=getattr(testcase, "_canonical_peer", False))


def save_test_acquisition(path, fixture, observation):
    path.write_text(json.dumps(dict(root=str(fixture.repo),observation=observation)))
    return path


def acquire_test_scope(path):
    """Subprocess fixture driver; this callback never enters the production CLI."""
    value=json.loads(Path(path).read_bytes())
    observation=value['observation']
    if observation['current']['surface']!='fixture-only':
        raise AssertionError('Only explicit synthetic test qualification is supported')
    acquisition=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=Path(value['root'])),state.PACKAGE,
        qualifier=lambda record,facts:observation)
    return startup._entry_scope(acquisition)


def test_acquisition(scope):
    """Reacquire an actual fixture member with its explicit synthetic owner receipt."""
    from runtime import startup
    # The qualification binding is already an immutable part of this real fixture scope.
    record=json.loads(scope.authority_path.read_bytes())
    repository=next(item for item in record['repositories'] if item['repository_id']==scope.repository_id)
    checkout=next(item for item in repository['checkouts'] if item['checkout_id']==scope.checkout_id)
    entry=next(item for item in checkout['clients'] if item['client']==scope.client)
    binding=entry['activation']['qualification_binding']
    if binding['surface']!='fixture-only':raise AssertionError('Only synthetic fixture owner qualification may be injected')
    proof=binding['receipt']
    observation=dict(current={key:copy.deepcopy(binding[key]) for key in
        ('client','surface','version','configuration_digest','hook_owner')},receipt=proof,binding=copy.deepcopy(binding),
        provenance=dict(kind='trusted-coordinator',**proof))
    return _ACTUAL_ACQUIRE(SimpleNamespace(client=scope.client,cwd=scope.repository_root),state.PACKAGE,
        qualifier=lambda record,facts:observation)


def native_receipt_fields(binding):
    """Only adapter receipt fields cross the native protocol boundary."""
    return {key:copy.deepcopy(binding[key]) for key in ('attempt_id','parent_session_id','task_name',
        'assignment_digest','candidate_digest','requested_model','requested_effort','client_surface','native_version')}


def fixture_artifact(scope, identity, value, *, db=None):
    """Publish test data through its real existing task owner and supplied transaction."""
    if db is None:
        with state._database(scope) as reader:
            task=state._task(reader,state._scope_id(scope),identity['client'],identity['session_id'],identity['task_id'])
            owner=evidence._artifact_owner(scope,task)
    else:
        task=state._task(db,state._scope_id(scope),identity['client'],identity['session_id'],identity['task_id'])
        owner=evidence._artifact_owner(scope,task)
    return evidence._artifact(scope,value,owner=owner,db=db)


class CommonGitTopologyTests(unittest.TestCase):
    """Dedicated setup gate, run before consumers of the linked common-Git topology."""
    def test_sequential_update_adds_linked_member_to_same_authority(self):
        owner=CanonicalBirthFixture('runTest')
        self.addCleanup(owner.doCleanups)
        fixture,acquired,_=owner.accepted_common_git_members()
        members=[startup._entry_scope(value) for value in fixture.member_acquisitions.values()]
        self.assertEqual(len(members),2)
        self.assertNotEqual(members[0].repository_root,members[1].repository_root)
        self.assertEqual(len({member.repository_id for member in members}),1)
        self.assertEqual(len({member.git_common_dir for member in members}),1)
        self.assertEqual(len({member.runtime.state_dir for member in members}),1)
        self.assertTrue((fixture.initial_birth_plan/'transaction.json').is_file())
        self.assertTrue((fixture.directory/'transaction.json').is_file())
        self.assertNotEqual(fixture.initial_birth_plan,fixture.directory)
