"""Disposable D4 transactions exercise ownership, durability and recovery."""

import copy
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests import test_onboarding as d3_tests
from runtime import onboarding
from runtime.startup import PROJECT_FILE, policy_digest


class ChangesTests(d3_tests.OnboardingTests):
    # Reuse the disposable D3 setup, not its inherited test cases.
    def setUp(self):
        super().setUp()
        self.home_patch = patch.dict(os.environ, HOME=str(self.base / "isolated-home"))
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        from runtime import review_storage
        self.machine_config_patch = patch.object(review_storage, "_machine_config_path",
            return_value=self.base / "machine" / "machine.json")
        self.machine_config_patch.start()
        self.addCleanup(self.machine_config_patch.stop)
        from runtime import changes
        self.changes = changes
        self.lock_patch = patch.object(changes, "LOCK_ROOT", self.base / "locks")
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        (self.repo / "AGENTS.md").chmod(0o755)
        self.inspect()
        onboarding.record_plan(self.directory, self.proposal(self.plan()["revision"]))

    def fingerprint(self, relative):
        path = self.repo / relative
        if not path.exists():
            return None
        return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": path.stat().st_mode & 0o777}

    def test_project_inspection_rejects_all_d4_operations_before_validation_or_locks(self):
        (self.directory / "plan.json").unlink()
        onboarding.inspect_project(self.repo, self.directory, self.project_record())
        before = (self.directory / "plan.json").read_bytes()
        target = (self.repo / "AGENTS.md").read_bytes()
        with patch.object(self.changes, "_validate", side_effect=AssertionError("D4 validation ran")), \
                patch.object(self.changes, "_worktree_lock", side_effect=AssertionError("worktree lock acquired")):
            for operation in (self.changes.apply_plan, self.changes.resume_plan, self.changes.rollback_plan):
                with self.subTest(operation=operation.__name__):
                    result = operation(self.directory, {})
                    self.assertNotEqual(result["exit_code"], 0)
                    self.assertIn("trusted coordinator", str(result["blocked"]))
                    self.assertFalse((self.directory / "transaction.json").exists())
                    self.assertFalse((self.base / "locks").exists())
        self.assertEqual((self.directory / "plan.json").read_bytes(), before)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(), target)
        self.assertFalse((self.repo / PROJECT_FILE).exists())

    def request(self):
        """Representative manifest/request fixture; evidence is disposable test data."""
        plan = self.plan()
        root, git_dir, common = self.changes._checkout(self.repo)
        evidence = {"reference": "fixture:trusted-host-observation", "sha256": "e" * 64}
        manifest = {"schema_version": 1, "transaction_id": "fixture-transaction",
            "plan": {key: plan[key] for key in ("plan_id", "revision", "review_digest", "current_input_digest")},
            "checkout": {"repository_root": str(root), "git_dir": str(git_dir), "git_common_dir": str(common)},
            "pins": {"package_pins": plan["package_pins"], "toolkit_commit": plan["sources"]["declared"]["toolkit"]["commit"],
                     "sources_digest": onboarding._digest(plan["sources"])},
            "authorization_reference": "fixture:user-approval", "approved_scope": [a["id"] for a in plan["actions"]] + ["enrollment"],
            "actions": [{"id": action["id"], "group": "configuration", "resources": ["checkout"],
                "preconditions": [], "effects": [{"path": action["target"], "before": self.fingerprint(action["target"]),
                    "after": {"sha256": action["after"], "mode": 0o644} if action["kind"] == "write" else None}]
                    if action["kind"] in ("write", "delete") else [],
                "recovery": "file-preimages", "execution": {"argv": action["text"], "timeout_seconds": 5,
                    "qualification": evidence, "idempotency": None} if action["kind"] == "command" else None}
                for action in plan["actions"]],
            "enrollment": {"record": {"schema_version": 1, "project_id": "fixture", "clients": ["codex"],
                "repository_root": str(root), "git_common_dir": str(common), "policy_digest": policy_digest(onboarding.PACKAGE)},
                "before": self.fingerprint(PROJECT_FILE), "requires": [a["id"] for a in plan["actions"]],
                "qualification": {"requirements": [row["id"] for row in plan["coverage"]
                    if row["mandatory"] and row["applicability"] == "applicable"], "security_required": True}}}
        return self.bind(manifest)

    def bind(self, manifest):
        return {"schema_version": 1, "manifest": manifest,
            "approval": {"reference": manifest["authorization_reference"],
                "evidence": [{"reference": "fixture:verified-session-approval", "sha256": "a" * 64}],
                "manifest_digest": onboarding._digest(manifest)}}

    def observe(self, request):
        request = copy.deepcopy(request)
        evidence = {"reference": "fixture:trusted-host-observation", "sha256": "e" * 64}
        request["observations"] = {"manifest_digest": request["approval"]["manifest_digest"],
            "input_digest": self.changes.observation_digest(request["manifest"]),
            "qualification": {"requirements": [{"id": key, "evidence": [evidence]}
                for key in request["manifest"]["enrollment"]["qualification"]["requirements"]],
                "general_review": [evidence], "security_review": [evidence]},
            "task_reconciliation": evidence, "prior_compatibility": evidence}
        if (self.directory / "transaction.json").exists():
            request["observations"]["command_reconciliation"] = [dict(id=key, attempt_id=value["command"]["attempt_id"],
                stopped=True, evidence=[evidence]) for key, value in self.journal()["receipts"].items() if "command" in value]
        return request

    def test_linked_foreign_check_uses_relevant_ledger_claims_and_corrupt_links_block(self):
        from tests.test_evidence import EvidenceTests
        from runtime import evidence, state
        from runtime.startup import ProjectScope, StartupError
        fixture = EvidenceTests(methodName='runTest')
        fixture._canonical_peer=True
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.plan()
        definition = evidence._load_plan(fixture.scope, fixture.identity)
        definition['checks'][0]['resources'] = [dict(kind='service', key='fixture:unrelated', host='local', mode='read')]
        evidence.plan_evidence(fixture.scope, fixture.request(plan=definition, change_decision_ref='user:declared-resource'))
        checked = evidence.run_check(fixture.scope, fixture.request(check_id='unit'))
        target = fixture.peer_scope
        self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope) as db:
            original = state._task(db, state._scope_id(fixture.scope), 'codex', 'parent')
        for delta in ('missing-link', 'wrong-claims', 'wrong-definition'):
            corrupt = copy.deepcopy(original)
            attempt = corrupt['evidence']['attempts'][0]
            if delta == 'missing-link':
                attempt.pop('ledger_assignment_digest')
            elif delta == 'wrong-claims':
                attempt['ledger_claims_digest'] = 'f' * 64
            else:
                attempt['check_id'] = 'another-check'
            with state._database(fixture.scope, write=True) as db:
                state._save(db, state._scope_id(fixture.scope), corrupt)
            with self.subTest(delta=delta), self.assertRaises(StartupError):
                self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope, write=True) as db:
            state._save(db, state._scope_id(fixture.scope), original)
        self.assertEqual(checked['attempt']['status'], 'uncertain')

    def test_linked_foreign_review_requires_exact_plain_prompt_and_pair_before_independence(self):
        from tests.test_delegation import Stage4NativeReviewTests
        from runtime import state
        from runtime.startup import ProjectScope, StartupError
        fixture = Stage4NativeReviewTests(methodName='runTest')
        fixture._canonical_peer=True
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        plan = fixture.review_plan()
        plan['jobs'][0]['claims'] = [dict(kind='service', key='fixture:independent-review', host='local', mode='read')]
        fixture.plan(plan)
        prepared = fixture.prepared(assignment='Review only the declared independent service read-only.')
        target = fixture.peer_scope
        self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope) as db:
            original = state._task(db, state._scope_id(fixture.scope), 'codex', 'parent')
        for change in ('missing-link', 'plain-digest', 'wrong-job'):
            corrupt = copy.deepcopy(original)
            attempt = corrupt['evidence']['attempts'][0]
            if change == 'missing-link':
                attempt.pop('ledger_assignment_digest')
            elif change == 'plain-digest':
                attempt['assignment_digest'] = 'f' * 64
            else:
                attempt['job_id'] = 'another-review'
            with state._database(fixture.scope, write=True) as db:
                state._save(db, state._scope_id(fixture.scope), corrupt)
            with self.subTest(change=change), self.assertRaises(StartupError):
                self.changes._writers({}, {}, {'task_reconciliation': True}, scope=target)
        with state._database(fixture.scope, write=True) as db:
            state._save(db, state._scope_id(fixture.scope), original)
        self.assertEqual(prepared['attempt']['status'], 'reserved')

    def test_first_authority_directory_race_revalidates_private_ownership_before_retry(self):
        from runtime.startup import StartupError
        original = self.changes.d3._plan_path
        for mode in (0o700, 0o755, 'symlink', 'repository'):
            directory = self.base / ('authority-first-' + str(mode))
            calls = []
            def raced(value):
                calls.append(value)
                if len(calls) == 1:
                    if mode == 'symlink':
                        target = self.base / 'authority-link-target'
                        target.mkdir(mode=0o700)
                        directory.symlink_to(target, target_is_directory=True)
                    else:
                        directory.mkdir(mode=0o700 if mode == 'repository' else mode)
                        if mode == 'repository':
                            (directory / '.git').mkdir(mode=0o700)
                    raise FileExistsError('fixture concurrent authority directory creation')
                return original(value)
            with self.subTest(mode=mode), patch.object(self.changes, 'LOCK_ROOT', directory), patch.object(self.changes.d3, '_plan_path', side_effect=raced):
                if mode == 0o700:
                    self.assertEqual(len(self.changes._authority_locations([self.base / 'private-authority-state'])), 1)
                    self.assertEqual(len(calls), 2)
                else:
                    with self.assertRaises(StartupError):
                        self.changes._authority_locations([self.base / 'private-authority-state'])

    def complete(self, request):
        result = self.changes.apply_plan(self.directory, request)
        if result["exit_code"] == 2:
            result = self.changes.resume_plan(self.directory, self.observe(request))
        return result

    def journal(self):
        return json.loads((self.directory / "transaction.json").read_text())

    def replan(self, actions=None, edit=None):
        proposal = self.proposal(self.plan()["revision"])
        if actions is not None:
            proposal["actions"] = actions
            for row in proposal["coverage"]:
                row["actions"] = [action["id"] for action in actions]
        if edit:
            edit(proposal)
        return onboarding.record_plan(self.directory, proposal)

    def existing_enrollment(self, runtime=False, stale=False):
        record = self.request()["manifest"]["enrollment"]["record"]
        if runtime:
            record["runtime"] = {"schema_version": 1 if runtime == "legacy" else 2, "state_dir": str(self.base / "task-state")}
        if stale:
            record["policy_digest"] = "f" * 64
        (self.repo / PROJECT_FILE.parent).mkdir()
        original = json.dumps(record).encode()
        (self.repo / PROJECT_FILE).write_bytes(original)
        (self.directory / "plan.json").unlink()
        self.inspect()
        onboarding.record_plan(self.directory, self.proposal(1))
        request = self.request()
        if runtime:
            request["manifest"]["enrollment"]["record"]["runtime"] = (
                {"schema_version": 2, "state_dir": str(self.base / "fresh-state")}
                if runtime == "legacy" else record["runtime"])
            request = self.bind(request["manifest"])
        return record, original, request

    def historical_runtime(self, record, kind='live'):
        """Bind portable original-generation data to the exact retained record, without publication."""
        from tests.test_legacy_currentness import LegacyCurrentnessTests
        fixture=LegacyCurrentnessTests('runTest')
        fixture.root,self_scope=self.base,self.changes._record(record,self.plan(),current=False)
        fixture.repo=self.repo
        fixture.identity=dict(client='codex',session_id='old-session',task_id='old-task',scope_ref='fixture:original-M')
        fixture.legacy(kind,scope=self_scope,publish=False)
        return self_scope

    def retained_inventory(self, directory):
        return {str(p):(p.read_bytes() if p.is_file() else None,p.stat().st_ino,p.stat().st_mode)
            for p in directory.rglob('*')}

    def assert_activation_incompatible(self, result):
        self.assertEqual(result['exit_code'],2,result)
        self.assertTrue(any('incompatible' in row['reason'].lower() for row in result['blocked']),result)
        self.assertFalse((self.repo/PROJECT_FILE).exists())
        self.assertFalse((self.base/'fresh-state').exists())

    def legacy_transition(self, retired=True, unresolved=False):
        """Exact old schema fixture, independent of the current initializer."""
        record, original, request = self.existing_enrollment(runtime="legacy")
        directory = Path(record["runtime"]["state_dir"])
        directory.mkdir(mode=0o700)
        database = directory / "runtime.sqlite3"
        database.touch(mode=0o600)
        scope_id = hashlib.sha256(json.dumps([record["project_id"], str(self.repo),
            record["git_common_dir"]]).encode()).hexdigest()
        task = dict(client="codex", session_id="old-session", task_id="old-task", scope_ref="fixture:old",
            mode="editable", decision_ref="fixture:old", state="cancelled", revision=2,
            pins=dict(schema_version=1, policy_digest=record["policy_digest"], runtime_digest="a" * 64),
            reason="Old runtime retired writer", reference="host:stopped", conditions=[], turn_id="old-turn",
            turn_uncertain=False)
        attempts = []
        if unresolved:
            reference = dict(name="b" * 32 + ".json", digest="c" * 64)
            attempts = [dict(id="d" * 32, kind="check", status="uncertain", candidate=reference,
                             candidate_digest="e" * 64, plan_digest="f" * 64)]
            task["evidence"] = dict(version=1, plan=reference, candidate=reference, attempts=attempts)
        proof = dict(version=1, kind="stopped-writer-reconciliation",
            identity={key: task[key] for key in ("client", "session_id", "task_id", "scope_ref")},
            expected_revision=1, expected_pins=task["pins"], observed_turn_id="old-turn", stopped=True,
            descendants_stopped=True, attempt_ids=[a["id"] for a in attempts],
            observation_ref="host:old-writer-tree", coordinator_ref="coordinator:verified-old-runtime")
        if retired:
            artifacts = directory / "evidence"
            artifacts.mkdir(mode=0o700)
            raw = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
            artifact = artifacts / ("a" * 32 + ".json")
            artifact.write_bytes(raw)
            artifact.chmod(0o400)
            task["retirement"] = dict(proof_digest=hashlib.sha256(raw).hexdigest(),
                proof=dict(name=artifact.name, digest=hashlib.sha256(raw).hexdigest()),
                observation_ref=proof["observation_ref"], coordinator_ref=proof["coordinator_ref"], previous_revision=1)
        with closing(sqlite3.connect(database)) as db, db:
            db.executescript("""
                CREATE TABLE checkouts(scope_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    repository_root TEXT NOT NULL, git_common_dir TEXT NOT NULL, generation INTEGER NOT NULL);
                CREATE TABLE tasks(scope_id TEXT, client TEXT, session_id TEXT, task_id TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(scope_id,client,session_id,task_id));
                CREATE TABLE bindings(scope_id TEXT, client TEXT, session_id TEXT, task_id TEXT NOT NULL,
                    PRIMARY KEY(scope_id,client,session_id));
                CREATE TABLE turns(scope_id TEXT, client TEXT, session_id TEXT, turn_id TEXT, task_id TEXT,
                    continuation_consumed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_id,client,session_id,turn_id));
                CREATE TABLE receipts(scope_id TEXT, client TEXT, session_id TEXT, turn_id TEXT, tool_call_id TEXT,
                    kind TEXT, fingerprint TEXT NOT NULL, conflicted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope_id,client,session_id,turn_id,tool_call_id,kind));
                PRAGMA application_id=1447641669;
                PRAGMA user_version=1;
            """)
            db.execute("insert into checkouts values(?,?,?,?,0)",
                (scope_id, record["project_id"], str(self.repo), record["git_common_dir"]))
            db.execute("insert into tasks values(?,?,?,?,?)",
                (scope_id, task["client"], task["session_id"], task["task_id"], json.dumps(task)))
            db.execute("insert into bindings values(?,?,?,?)", (scope_id, task["client"], task["session_id"], task["task_id"]))
        return record, original, request, database, task

    def rewrite_legacy_task(self, database, task):
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("update tasks set data=?", (json.dumps(task),))

    def legacy_other_checkout(self, *, linked=True, retired=False, unresolved=True, mode='editable', task_state=None):
        record, original, request, database, task = self.legacy_transition(retired=retired, unresolved=unresolved)
        owner = self.base / ('legacy-linked' if linked else 'legacy-unrelated')
        if linked:
            self.git('worktree', 'add', '-q', '--detach', str(owner), 'HEAD')
            common = record['git_common_dir']
        else:
            owner.mkdir()
            self.git('-C', str(owner), 'init', '-q')
            common = str((owner / '.git').resolve())
        owner = owner.resolve()
        scope_id = hashlib.sha256(json.dumps([record['project_id'], str(owner), common]).encode()).hexdigest()
        task['mode'] = mode
        if task_state is not None:
            task['state'] = task_state
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('update checkouts set scope_id=?,repository_root=?,git_common_dir=?', (scope_id, str(owner), common))
            for table in ('tasks', 'bindings', 'turns', 'receipts'):
                db.execute('update ' + table + ' set scope_id=?', (scope_id,))
            db.execute('update tasks set data=?', (json.dumps(task),))
        return record, original, request, database, task

    def assert_legacy_foreign_owner_blocks_before_mutation(self, **fields):
        record, original, request, database, _ = self.legacy_other_checkout(**fields)
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        rules = (self.repo / 'AGENTS.md').read_bytes()
        result = self.changes.apply_plan(self.directory, self.observe(request))
        self.assertEqual(result['exit_code'], 2, result)
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)
        self.assertTrue((self.repo / PROJECT_FILE).exists(), result)
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(), original)
        self.assertEqual((self.repo / 'AGENTS.md').read_bytes(), rules)
        self.assertFalse((self.base / 'fresh-state').exists())
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, self.observe(request)['observations'])

    def test_legacy_linked_unresolved_owner_blocks_before_enrollment_and_configuration_mutations(self):
        self.assert_legacy_foreign_owner_blocks_before_mutation()

    def test_legacy_linked_active_editable_owner_without_check_blocks_before_mutations(self):
        self.assert_legacy_foreign_owner_blocks_before_mutation(unresolved=False, task_state='active')

    def test_legacy_unrelated_readonly_unresolved_attempt_has_unknown_shared_footprint(self):
        self.assert_legacy_foreign_owner_blocks_before_mutation(linked=False, mode='read-only')

    def test_legacy_linked_retirement_must_match_before_incompatible_activation(self):
        record, _, request, database, task = self.legacy_other_checkout(retired=True)
        observation = self.observe(request)['observations']
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        self.changes._writers(self.plan(), record, observation)
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)
        self.rewrite_legacy_task(database, task | dict(turn_id='another-turn'))
        corrupt_before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, corrupt_before)
        self.rewrite_legacy_task(database, task)
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        result = self.complete(request)
        if result['exit_code'] == 2:
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assert_activation_incompatible(result)
        self.assertFalse((self.base / 'fresh-state').exists())
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)
        self.assertEqual(task['pins']['schema_version'], 1)

    def test_legacy_linked_readonly_owner_without_attempts_does_not_claim_mutation(self):
        record, _, request, database, _ = self.legacy_other_checkout(unresolved=False, mode='read-only', task_state='active')
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        self.changes._writers(self.plan(), record, self.observe(request)['observations'])
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)

    def test_legacy_unrelated_retired_owner_remains_safely_reconciled(self):
        record, _, request, database, _ = self.legacy_other_checkout(linked=False, retired=True)
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        self.changes._writers(self.plan(), record, self.observe(request)['observations'])
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)

    def test_legacy_retired_state_preserves_history_and_refuses_current_activation(self):
        record, _, request, database, task = self.legacy_transition(unresolved=True)
        before = {str(p): p.read_bytes() for p in database.parent.rglob("*") if p.is_file()}
        result = self.complete(request)
        if result["exit_code"] == 2:
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assert_activation_incompatible(result)
        self.assertFalse((self.base / "fresh-state").exists())
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob("*") if p.is_file()}, before)
        self.assertEqual(task["pins"]["schema_version"], 1)
        from runtime import startup
        with self.assertRaises(startup.StartupError):
            startup.runtime_settings(record, onboarding.PACKAGE, Path(record["git_common_dir"]))

    def test_legacy_cancelled_flag_without_retained_proof_cannot_release_writer(self):
        _, original, request, database, _ = self.legacy_transition(retired=False, unresolved=True)
        before = database.read_bytes()
        result = self.changes.apply_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(), original)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
        self.assertEqual(database.read_bytes(), before)

    def test_legacy_proof_binding_corruption_and_unsafe_state_block_transition(self):
        record, _, request, database, task = self.legacy_transition(unresolved=True)
        observation = self.observe(request)["observations"]
        self.changes._writers(self.plan(), record, observation)
        changes = [dict(pins=task["pins"] | dict(schema_version=2)), dict(turn_id="different"),
            dict(revision=3), dict(retirement=task["retirement"] | dict(observation_ref="unbound")),
            dict(evidence=task["evidence"] | dict(attempts=[]))]
        for delta in changes:
            with self.subTest(delta=delta):
                self.rewrite_legacy_task(database, task | delta)
                with self.assertRaises(self.changes.Pending):
                    self.changes._writers(self.plan(), record, observation)
        self.rewrite_legacy_task(database, task)
        artifact = database.parent / "evidence" / task["retirement"]["proof"]["name"]
        raw = artifact.read_bytes()
        proof = json.loads(raw)
        for delta in (dict(expected_revision=True), dict(expected_pins=task["pins"] | dict(schema_version=True))):
            with self.subTest(proof=delta):
                altered = json.dumps(proof | delta).encode()
                artifact.chmod(0o600)
                artifact.write_bytes(altered)
                artifact.chmod(0o400)
                retirement = task["retirement"] | dict(proof=dict(name=artifact.name,
                    digest=hashlib.sha256(altered).hexdigest()))
                self.rewrite_legacy_task(database, task | dict(retirement=retirement))
                with self.assertRaises(self.changes.Pending):
                    self.changes._writers(self.plan(), record, observation)
        artifact.chmod(0o600)
        artifact.write_bytes(raw)
        artifact.chmod(0o400)
        self.rewrite_legacy_task(database, task)
        artifact.chmod(0o600)
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        artifact.chmod(0o400)
        database.chmod(0o644)
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        database.chmod(0o600)
        retained = database.with_suffix(".retained")
        database.rename(retained)
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        self.assertFalse(database.exists())
        retained.rename(database)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("create view unsupported as select * from tasks")
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        self.assertEqual(artifact.read_bytes(), raw)

    def test_legacy_schema_one_rejects_shape_valid_schema_two_delegation_state(self):
        record, _, request, database, task = self.legacy_transition()
        task['delegation'] = dict(version=1, plan=task['retirement']['proof'],
                                  attempts_consumed=0, resource_units_consumed=0)
        self.rewrite_legacy_task(database, task)
        before = {str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, self.observe(request)['observations'])
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob('*') if p.is_file()}, before)

    def assert_legacy_null_event_scope_is_rejected(self, table):
        record, _, request, database, _ = self.legacy_transition(unresolved=True)
        observation = self.observe(request)["observations"]
        self.changes._writers(self.plan(), record, observation)
        values = ((None, "codex", "orphan", "turn", "task", 0) if table == "turns" else
                  (None, "codex", "orphan", "turn", "call", "edit", "fingerprint", 0))
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("insert into " + table + " values(" + ",".join("?" for _ in values) + ")", values)
        before = {str(p): p.read_bytes() for p in database.parent.rglob("*") if p.is_file()}
        with self.assertRaises(self.changes.Pending):
            self.changes._writers(self.plan(), record, observation)
        self.assertEqual({str(p): p.read_bytes() for p in database.parent.rglob("*") if p.is_file()}, before)

    def test_legacy_null_turn_checkout_blocks_without_mutating_history(self):
        self.assert_legacy_null_event_scope_is_rejected("turns")

    def test_legacy_null_receipt_checkout_blocks_without_mutating_history(self):
        self.assert_legacy_null_event_scope_is_rejected("receipts")

    def test_legacy_recovery_inspects_backup_and_blocks_new_unreconciled_writer(self):
        _, original, request, database, task = self.legacy_transition()
        fired = False
        def crash(stage):
            nonlocal fired
            if stage == "rename" and not fired:
                fired = True
                raise RuntimeError("interrupted transition")
        with patch.object(self.changes, "_checkpoint", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, self.observe(request))
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        active = dict(task, state="active")
        active.pop("retirement")
        self.rewrite_legacy_task(database, active)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.rewrite_legacy_task(database, task)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        if result["exit_code"] == 2:
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assert_activation_incompatible(result)
        result = self.changes.rollback_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual((self.directory / "enrollment.backup").read_bytes(), original)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def command_request(self, script):
        action = self.plan()["actions"][0]
        action.update(id="command", kind="command", target="fixture-command", section=None,
                      before=None, text=[__import__("sys").executable, "-c", script], after=None)
        self.replan([action], lambda p: p["evidence"].append({"id": "command-approval", "kind": "authorization",
            "summary": "disposable command approval", "scope": "checkout", "inputs": [], "reference": "fixture"}))
        request = self.request()
        request["manifest"]["actions"][0]["effects"] = [{"path": "result.txt", "before": None,
            "after": {"sha256": hashlib.sha256(b"result").hexdigest(), "mode": 0o644}}]
        return self.bind(request["manifest"])

    def test_runtime_resume_reuses_current_retained_observation_without_weakening_writer_guard(self):
        from runtime import state
        record, _, request = self.existing_enrollment()
        result = self.changes.apply_plan(self.directory, request)
        for _ in range(3):
            if result['exit_code'] == 0:
                break
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result['exit_code'], 0, result)
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))['exit_code'], 0)
        retained = len(self.journal()['observations'])
        resumed = self.changes.resume_plan(self.directory, request)
        self.assertEqual(resumed['exit_code'], 0, resumed)
        self.assertEqual(len(self.journal()['observations']), retained)
        # Historical writer rejection is retained in the bound live/late writer tests below.

    def test_runtime_resume_rejects_stale_retained_observation(self):
        _, _, request = self.existing_enrollment()
        result = self.changes.apply_plan(self.directory, request)
        for _ in range(3):
            if result['exit_code'] == 0:
                break
            result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result['exit_code'], 0, result)
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))['exit_code'], 0)
        (self.repo / 'README.md').write_text('independent external edit\n')
        before = (self.repo / PROJECT_FILE).read_bytes()
        result = self.changes.resume_plan(self.directory, request)
        self.assertEqual(result['exit_code'], 1, result)
        self.assertEqual(result['state'], 'interrupted')
        self.assertEqual(result['completed'], ['write-rules'])
        self.assertEqual(result['blocked'], [{'id': 'transaction', 'reason': 'Unrelated current checkout inputs changed'}])
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(), before)
        self.assertEqual((self.repo / 'README.md').read_text(), 'independent external edit\n')

    def test_configuration_waits_for_current_qualification_and_returns_digest(self):
        request = self.request()
        stale = self.observe(request)
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual(result["completed"], ["write-rules"])
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual(result["observation_input_digest"], self.changes.observation_digest(request["manifest"]))
        self.assertEqual(self.changes.resume_plan(self.directory, stale)["exit_code"], 1)
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))["exit_code"], 0)
        self.assertEqual(len(self.journal()["observations"]), 1)

    def test_same_worktree_lock_is_independent_of_plan_directory(self):
        request = self.request()
        other = self.base / "second-plan"
        other.mkdir(mode=0o700)
        for name in ("plan.json", "catalog.md"):
            (other / name).write_bytes((self.directory / name).read_bytes())
            (other / name).chmod(0o600)
        git_dir = Path(request["manifest"]["checkout"]["git_dir"])
        with self.changes._worktree_lock(git_dir):
            result = self.changes.apply_plan(other, request)
            self.assertEqual(result["exit_code"], 1)
            self.assertIn("busy", result["blocked"][0]["reason"])
        self.assertFalse((other / "transaction.json").exists())
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_linked_worktree_has_a_distinct_transaction_lock(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        root, git_dir, common = self.changes._checkout(linked)
        self.assertEqual(common, self.changes._checkout(self.repo)[2])
        self.assertNotEqual(git_dir, self.changes._checkout(self.repo)[1])
        with self.changes._worktree_lock(git_dir):
            result = self.changes.apply_plan(self.directory, self.request())
        self.assertEqual(result["exit_code"], 2, result)

    def test_external_edit_during_temp_write_is_preserved(self):
        request = self.request()
        edited = False
        def edit(stage):
            nonlocal edited
            if stage == "temp_fsync" and not edited:
                edited = True
                (self.repo / "AGENTS.md").write_text("external editor\n")
        with patch.object(self.changes, "_checkpoint", side_effect=edit):
            result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "external editor\n")

    def test_stale_plan_and_unrelated_inputs_reject_before_mutation(self):
        request = self.request()
        (self.repo / "README.md").write_text("unrelated edit")
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 1)
        self.assertFalse((self.directory / "transaction.json").exists())
        self.assertFalse((self.repo / PROJECT_FILE).exists())

    def test_missing_command_contract_stays_pending(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        request["manifest"]["actions"][0]["execution"]["qualification"] = None
        result = self.changes.apply_plan(self.directory, self.bind(request["manifest"]))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / "result.txt").exists())

    def test_uncertain_command_never_blindly_replays(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        with patch.object(self.changes, "_checkpoint", side_effect=lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == "command_started" else None):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, request)
        result = self.changes.resume_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / "result.txt").exists())
        self.assertIn("uncertain", result["blocked"][0]["reason"])

    def test_command_postimages_reconcile_after_crash(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        with patch.object(self.changes, "_checkpoint", side_effect=lambda s: (_ for _ in ()).throw(RuntimeError("crash")) if s == "command_finished" else None):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, request)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)
        self.assertFalse((self.repo / "result.txt").exists())

    def test_mixed_write_delete_restores_reverse_dependency_order(self):
        write = self.plan()["actions"][0]
        delete = dict(write, id="delete-readme", dependencies=["write-rules"], kind="delete", target="README.md",
            section=None, before=hashlib.sha256((self.repo / "README.md").read_bytes()).hexdigest(), text=None, after="absent")
        self.replan([write, delete])
        request = self.request()
        self.assertEqual(self.complete(request)["exit_code"], 0)
        self.assertFalse((self.repo / "README.md").exists())
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)
        self.assertEqual((self.repo / "README.md").read_text(), "product\n")

    def test_existing_enrollment_deactivates_first_and_restores_last(self):
        _, original, request = self.existing_enrollment()
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))["exit_code"], 0)
        result = self.changes.rollback_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
        self.assertEqual(self.changes.rollback_plan(self.directory, self.observe(request))["exit_code"], 0)
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(), original)

    def test_stale_prior_enrollment_remains_inactive_after_rollback(self):
        _, _, request = self.existing_enrollment(stale=True)
        self.assertEqual(self.complete(request)["exit_code"], 0)
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 2)
        result = self.changes.rollback_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertIn("stale", result["blocked"][0]["reason"])

    def test_external_enrollment_change_blocks_configuration_rollback(self):
        request = self.request()
        self.assertEqual(self.complete(request)["exit_code"], 0)
        (self.repo / PROJECT_FILE).write_text("external enrollment")
        result = self.changes.rollback_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual((self.repo / PROJECT_FILE).read_text(), "external enrollment")
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nnew process\n")

    def test_active_task_writers_preserve_enrollment_and_configuration(self):
        record,original,request=self.existing_enrollment(runtime=True)
        scope=self.historical_runtime(record)
        before=self.retained_inventory(scope.runtime.state_dir)
        result=self.changes.apply_plan(self.directory,self.observe(request))
        self.assertEqual(result['exit_code'],2,result)
        self.assertIn('writers',result['blocked'][0]['reason'])
        self.assertEqual((self.repo/PROJECT_FILE).read_bytes(),original)
        self.assertEqual((self.repo/'AGENTS.md').read_text(),'product rules\nlegacy process\n')
        self.assertEqual(before,self.retained_inventory(scope.runtime.state_dir))

    def test_unresolved_decision_blocks_only_dependent_actions(self):
        write = self.plan()["actions"][0]
        second = dict(write, id="write-readme", target="README.md", before=hashlib.sha256(b"product\n").hexdigest(),
            text="new readme\n", after=hashlib.sha256(b"new readme\n").hexdigest())
        def decision(proposal):
            proposal["actions"][0]["decision_ids"] = ["choice"]
            proposal["decisions"] = [{"id": "choice", "rule": "policy:01-project-scope", "source": "AGENTS.md",
                "scope": "checkout", "options": ["preserve", "replace"], "recommendation": "preserve", "selected": None,
                "disposition": "pending", "provenance": "unanswered fixture choice", "evidence": [],
                "affected_actions": ["write-rules"], "invalidate_on": ["AGENTS.md"], "conflict": True, "mode": "decide"}]
        self.replan([write, second], decision)
        request = self.request()
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual(result["completed"], ["write-readme"])
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
        self.assertEqual((self.repo / "README.md").read_text(), "new readme\n")

    def test_process_death_leaves_reconcilable_owned_temporaries(self):
        import sys
        for boundary in ("temp_write", "temp_fsync", "rename", "parent_fsync", "outcome", "state"):
            with self.subTest(boundary=boundary):
                if (self.directory / "transaction.json").exists():
                    self.changes.rollback_plan(self.directory, request)
                    (self.directory / "transaction.json").unlink()
                request = self.request()
                script = ("import json,os,sys; from pathlib import Path; from runtime import changes; "
                    + "changes.LOCK_ROOT=Path(" + repr(str(self.base / "locks")) + "); "
                    + "changes._checkpoint=lambda stage: os._exit(91) if stage==" + repr(boundary) + " else None; "
                    + "changes.apply_plan(" + repr(str(self.directory)) + ",json.load(sys.stdin))")
                child = subprocess.run([sys.executable, "-B", "-c", script], cwd=onboarding.PACKAGE,
                    input=json.dumps(request).encode(), capture_output=True, timeout=10)
                self.assertEqual(child.returncode, 91, child.stderr)
                result = self.changes.resume_plan(self.directory, request)
                if result["exit_code"] == 2:
                    result = self.changes.resume_plan(self.directory, self.observe(request))
                self.assertEqual(result["exit_code"], 0, result)
                self.assertFalse(list(self.repo.glob(".d4-*")))
                self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)

    def test_generic_enrollment_effect_must_match_final_approved_record(self):
        request = self.request()
        request["manifest"]["actions"][0]["effects"][0]["path"] = str(PROJECT_FILE)
        self.assertEqual(self.changes.apply_plan(self.directory, self.bind(request["manifest"]))["exit_code"], 1)
        self.assertFalse((self.repo / PROJECT_FILE).exists())

    def test_backup_and_target_links_are_rejected(self):
        request = self.request()
        backup = self.directory / "backup-0-0"
        backup.symlink_to(self.repo / "AGENTS.md")
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 1, result)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_unknown_command_effects_and_shared_resources_stay_pending(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        for key, value in (("effects", []), ("resources", ["global"]), ("recovery", "manual")):
            with self.subTest(key=key):
                changed = copy.deepcopy(request["manifest"])
                changed["actions"][0][key] = value
                if (self.directory / "transaction.json").exists():
                    (self.directory / "transaction.json").unlink()
                result = self.changes.apply_plan(self.directory, self.bind(changed))
                self.assertEqual(result["exit_code"], 2, result)
                self.assertFalse((self.repo / "result.txt").exists())

    def test_exact_d3_enrollment_proposal_is_installed_only_last(self):
        record = self.request()["manifest"]["enrollment"]["record"]
        write = self.plan()["actions"][0]
        text = json.dumps(record, indent=2)
        enrollment = dict(write, id="activate-project", dependencies=["write-rules"], target=str(PROJECT_FILE),
            section=None, before="absent", text=text, after=hashlib.sha256(text.encode()).hexdigest())
        self.replan([write, enrollment])
        request = self.request()
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual((self.repo / PROJECT_FILE).read_text(), text)
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)

    def test_owned_command_holds_worktree_lock_without_journal_lock(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        popen = subprocess.Popen
        def launch(*args, **kwargs):
            if args and args[0] == request["manifest"]["actions"][0]["execution"]["argv"]:
                with self.assertRaisesRegex(Exception, "busy"):
                    with self.changes._worktree_lock(Path(request["manifest"]["checkout"]["git_dir"])):
                        pass
                with self.changes._lock(self.directory / ".d4-journal.lock"):
                    pass
            return popen(*args, **kwargs)
        with patch.object(self.changes.subprocess, "Popen", side_effect=launch):
            result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual((self.repo / "result.txt").read_text(), "result")

    def test_crash_during_prior_enrollment_restore_reconciles_ownership(self):
        _, original, request = self.existing_enrollment()
        self.assertEqual(self.complete(request)["exit_code"], 0)
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 2)
        request = self.observe(request)
        with patch.object(self.changes, "_checkpoint", side_effect=lambda stage: (_ for _ in ()).throw(RuntimeError("crash")) if stage == "rename" else None):
            with self.assertRaises(RuntimeError):
                self.changes.rollback_plan(self.directory, request)
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(), original)
        result = self.changes.rollback_plan(self.directory, self.bind(request["manifest"]))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(), original)

    def test_unapproved_optional_action_does_not_gate_mandatory_enrollment(self):
        write = self.plan()["actions"][0]
        optional = dict(write, id="optional-readme", target="README.md", before=hashlib.sha256(b"product\n").hexdigest(),
            text="optional readme\n", after=hashlib.sha256(b"optional readme\n").hexdigest())
        def coverage(proposal):
            for row in proposal["coverage"]:
                row["actions"] = ["write-rules"] if row["mandatory"] else ["optional-readme"]
        self.replan([write, optional], coverage)
        request = self.request()
        request["manifest"]["approved_scope"] = ["write-rules", "enrollment"]
        request["manifest"]["enrollment"]["requires"] = ["write-rules"]
        request = self.bind(request["manifest"])
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 2)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertTrue((self.repo / PROJECT_FILE).exists())
        self.assertEqual((self.repo / "README.md").read_text(), "product\n")
        self.assertEqual([row["id"] for row in result["blocked"]], ["optional-readme"])

    def test_changed_restored_configuration_keeps_prior_enrollment_inactive(self):
        _, _, request = self.existing_enrollment()
        self.assertEqual(self.complete(request)["exit_code"], 0)
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 2)
        (self.repo / "AGENTS.md").write_text("user edited restored configuration\n")
        result = self.changes.rollback_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "user edited restored configuration\n")

    def test_reconciled_historical_writer_reaches_incompatible_activation(self):
        record,_,request=self.existing_enrollment(runtime=True)
        scope=self.historical_runtime(record,'cancelled')
        before=self.retained_inventory(scope.runtime.state_dir)
        self.changes._writers(self.plan(),record,self.observe(request)['observations'])
        result=self.changes.apply_plan(self.directory,self.observe(request))
        self.assertEqual(result['exit_code'],2,result)
        self.assertFalse((self.repo/PROJECT_FILE).exists())
        result=self.changes.resume_plan(self.directory,self.observe(request))
        self.assert_activation_incompatible(result)
        self.assertEqual(before,self.retained_inventory(scope.runtime.state_dir))

    def test_proven_idempotency_contract_permits_uncertain_command_resume(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        request["manifest"]["actions"][0]["execution"]["idempotency"] = {
            "reference": "fixture:coordinator-verified-idempotent-contract", "sha256": "d" * 64}
        request = self.bind(request["manifest"])
        with patch.object(self.changes, "_checkpoint", side_effect=lambda stage: (_ for _ in ()).throw(RuntimeError("crash")) if stage == "command_started" else None):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, request)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual((self.repo / "result.txt").read_text(), "result")
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))["exit_code"], 0)

    def test_null_evidence_entries_reject_at_all_required_boundaries(self):
        request = self.request()
        invalid = copy.deepcopy(request)
        invalid["approval"]["evidence"] = [None]
        self.assertEqual(self.changes.apply_plan(self.directory, invalid)["exit_code"], 1)
        self.assertFalse((self.directory / "transaction.json").exists())
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 2)
        for field in ("requirements", "general_review", "security_review"):
            with self.subTest(field=field):
                invalid = self.observe(request)
                proof = invalid["observations"]["qualification"]
                if field == "requirements":
                    proof[field][0]["evidence"] = [None]
                else:
                    proof[field] = [None]
                self.assertEqual(self.changes.resume_plan(self.directory, invalid)["exit_code"], 1)
                self.assertFalse((self.repo / PROJECT_FILE).exists())

    def nested_request(self):
        action = dict(self.plan()["actions"][0], target="docs/design/architecture.md", before="absent")
        self.replan([action])
        return self.request()

    def test_nested_write_creates_owned_parents_and_removes_them_on_rollback(self):
        request = self.nested_request()
        self.assertEqual(self.complete(request)["exit_code"], 0)
        self.assertTrue((self.repo / "docs/design/architecture.md").exists())
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)
        self.assertFalse((self.repo / "docs").exists())

    def test_nested_parent_crash_gap_preserves_uncertain_directory(self):
        request = self.nested_request()
        with patch.object(self.changes, "_checkpoint", side_effect=lambda stage: (_ for _ in ()).throw(RuntimeError("crash")) if stage == "directory_created" else None):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, request)
        result = self.changes.resume_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        result = self.changes.rollback_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertTrue((self.repo / "docs").is_dir())
        self.assertIn("docs", result["retained_directories"])

    def test_direct_rollback_after_parent_creation_crash_reports_retained_metadata(self):
        request = self.nested_request()
        with patch.object(self.changes, "_checkpoint", side_effect=lambda stage: (_ for _ in ()).throw(RuntimeError("crash")) if stage == "directory_created" else None):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, request)
        self.assertEqual(self.journal()["directories"]["docs"]["status"], "prepared")
        result = self.changes.rollback_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertTrue((self.repo / "docs").is_dir())
        self.assertEqual(result["retained_directories"], ["docs"])
        self.assertEqual(self.journal()["directories"]["docs"]["status"], "retained")
        self.assertIsNone(self.journal()["directories"]["docs"]["inode"])

    def test_direct_rollback_records_absent_prepared_parent_as_removed(self):
        request = self.nested_request()
        with patch.object(self.changes, "_checkpoint", side_effect=lambda stage: (_ for _ in ()).throw(RuntimeError("crash")) if stage == "directory_created" else None):
            with self.assertRaises(RuntimeError):
                self.changes.apply_plan(self.directory, request)
        (self.repo / "docs").rmdir()
        result = self.changes.rollback_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["retained_directories"], [])
        self.assertEqual(self.journal()["directories"]["docs"]["status"], "removed")

    def test_nested_rollback_preserves_preexisting_and_nonempty_directories(self):
        (self.repo / "docs").mkdir()
        request = self.nested_request()
        self.assertEqual(self.complete(request)["exit_code"], 0)
        (self.repo / "docs/design/user.txt").write_text("user content")
        result = self.changes.rollback_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertTrue((self.repo / "docs").is_dir())
        self.assertEqual((self.repo / "docs/design/user.txt").read_text(), "user content")
        self.assertIn("docs/design", result["retained_directories"])

    def test_prepared_enrollment_resume_rechecks_new_historical_writer(self):
        record,original,request=self.existing_enrollment(runtime=True)
        replace=self.changes._replace
        reached=[]
        def stop_before_deactivation(root,relative,raw,fingerprint,expected,*args):
            if relative==str(PROJECT_FILE) and fingerprint is None:
                reached.append(relative)
                raise RuntimeError('crash before deactivation')
            return replace(root,relative,raw,fingerprint,expected,*args)
        with patch.object(self.changes,'_replace',side_effect=stop_before_deactivation):
            with self.assertRaises(RuntimeError):self.changes.apply_plan(self.directory,self.observe(request))
        self.assertTrue(reached)
        scope=self.historical_runtime(record)
        before=self.retained_inventory(scope.runtime.state_dir)
        result=self.changes.resume_plan(self.directory,self.observe(request))
        self.assertEqual(result['exit_code'],2,result)
        self.assertIn('writers',result['blocked'][0]['reason'])
        self.assertEqual((self.repo/PROJECT_FILE).read_bytes(),original)
        self.assertEqual((self.repo/'AGENTS.md').read_text(),'product rules\nlegacy process\n')
        self.assertEqual(before,self.retained_inventory(scope.runtime.state_dir))

    def test_historical_rollback_rechecks_writer_before_owned_restoration(self):
        record,_,request=self.existing_enrollment(runtime=True)
        self.changes.apply_plan(self.directory,self.observe(request))
        self.assert_activation_incompatible(self.changes.resume_plan(self.directory,self.observe(request)))
        scope=self.historical_runtime(record)
        before=self.retained_inventory(scope.runtime.state_dir)
        reached=[];original=self.changes._writers
        def inspect(*args,**kwargs):
            reached.append(True)
            return original(*args,**kwargs)
        with patch.object(self.changes,'_writers',side_effect=inspect):
            result=self.changes.rollback_plan(self.directory,self.observe(request))
        self.assertTrue(reached)
        self.assertEqual(result['exit_code'],2,result)
        self.assertTrue(any(row['reason']=='Affected task writers remain active or unreconciled; stop and reconcile them first'
            for row in result['blocked']),result)
        self.assertFalse((self.repo/PROJECT_FILE).exists())
        self.assertEqual((self.repo/'AGENTS.md').read_text(),'product rules\nnew process\n')
        self.assertEqual(before,self.retained_inventory(scope.runtime.state_dir))
        # Live-writer rollback after actual canonical activation remains required by C1C.

    def optional_request(self, pending_decision=False):
        write = self.plan()["actions"][0]
        optional = dict(write, id="optional-readme", target="README.md", before=hashlib.sha256(b"product\n").hexdigest(),
            text="optional readme\n", after=hashlib.sha256(b"optional readme\n").hexdigest())
        def coverage(proposal):
            for row in proposal["coverage"]:
                row["actions"] = ["write-rules"] if row["mandatory"] else ["optional-readme"]
                if not row["mandatory"] and not pending_decision:
                    row.update(outcome="blocked", blocker="optional prerequisite", resumption="optional manual step")
            if pending_decision:
                proposal["actions"][1]["decision_ids"] = ["optional-choice"]
                proposal["decisions"] = [{"id": "optional-choice", "rule": "policy:01-project-scope", "source": "README.md",
                    "scope": "checkout", "options": ["preserve", "replace"], "recommendation": "preserve", "selected": None,
                    "disposition": "pending", "provenance": "unanswered optional choice", "evidence": [],
                    "affected_actions": ["optional-readme"], "invalidate_on": ["README.md"], "conflict": True, "mode": "decide"}]
        self.replan([write, optional], coverage)
        request = self.request()
        request["manifest"]["enrollment"]["requires"] = ["write-rules"]
        return self.bind(request["manifest"])

    def test_optional_coverage_blocker_does_not_gate_mandatory_activation(self):
        request = self.optional_request()
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 2)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertTrue((self.repo / PROJECT_FILE).exists())
        self.assertEqual([item["id"] for item in result["blocked"]], ["optional-readme"])

    def test_optional_pending_decision_does_not_gate_mandatory_activation(self):
        request = self.optional_request(pending_decision=True)
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 2)
        result = self.changes.resume_plan(self.directory, self.observe(request))
        self.assertEqual(result["exit_code"], 2, result)
        self.assertTrue((self.repo / PROJECT_FILE).exists())
        self.assertEqual([item["id"] for item in result["blocked"]], ["optional-readme"])

    def test_completed_command_requires_exact_attempt_lifecycle_proof(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse(result["completed"])
        result = self.changes.resume_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertIn("lifecycle", str(result["blocked"]))
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))["exit_code"], 0)

    def orphan_command_case(self, descendant_closes_lock):
        import sys
        import time
        child = "from pathlib import Path; import time; Path('started.txt').write_text('started'); time.sleep(1.5); Path('result.txt').write_text('result')"
        program = ("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) +
                   "],close_fds=True); time.sleep(0.15)") if descendant_closes_lock else child
        request = self.command_request(program)
        request["manifest"]["actions"][0]["effects"].append({"path": "started.txt", "before": None,
            "after": {"sha256": hashlib.sha256(b"started").hexdigest(), "mode": 0o644}})
        request = self.bind(request["manifest"])
        script = ("import json,sys; from pathlib import Path; from runtime import changes; changes.LOCK_ROOT=Path("
            + repr(str(self.base / "locks")) + "); changes.apply_plan(" + repr(str(self.directory)) + ",json.load(sys.stdin))")
        helper = subprocess.Popen([sys.executable, "-B", "-c", script], cwd=onboarding.PACKAGE,
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            helper.stdin.write(json.dumps(request).encode())
            helper.stdin.close()
            deadline = time.monotonic() + 4
            while not (self.repo / "started.txt").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.repo / "started.txt").exists())
            helper.kill()
            helper.wait(timeout=2)
            if descendant_closes_lock:
                time.sleep(0.25)
            result = self.changes.rollback_plan(self.directory, request)
            self.assertIn(result["exit_code"], (1, 2), result)
            self.assertFalse((self.repo / "result.txt").exists())
            other = self.base / "another-plan"
            other.mkdir(mode=0o700)
            for name in ("plan.json", "catalog.md"):
                (other / name).write_bytes((self.directory / name).read_bytes())
                (other / name).chmod(0o600)
            result = self.changes.apply_plan(other, request)
            self.assertIn(result["exit_code"], (1, 2), result)
            if descendant_closes_lock:
                self.assertEqual(result["exit_code"], 2, result)
                self.assertIn("owner", str(result["blocked"]))
            while not (self.repo / "result.txt").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual((self.repo / "result.txt").read_text(), "result")
            time.sleep(0.05)
            result = self.changes.rollback_plan(self.directory, request)
            self.assertEqual(result["exit_code"], 2, result)
            self.assertIn("lifecycle", str(result["blocked"]))
            result = self.changes.rollback_plan(self.directory, self.observe(request))
            self.assertEqual(result["exit_code"], 0, result)
            self.assertFalse((self.repo / "result.txt").exists())
            self.assertFalse((self.repo / "started.txt").exists())
        finally:
            if helper.poll() is None:
                helper.kill()
                helper.wait(timeout=2)

    def test_parent_death_does_not_allow_rollback_while_owned_command_survives(self):
        self.orphan_command_case(descendant_closes_lock=False)

    def test_closed_fd_descendant_keeps_cross_plan_command_owner_blocked(self):
        self.orphan_command_case(descendant_closes_lock=True)

    def test_lifecycle_proof_rejects_missing_evidence_and_wrong_attempt(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 2)
        observed = self.observe(request)
        observed["observations"]["command_reconciliation"][0]["evidence"] = [None]
        self.assertEqual(self.changes.resume_plan(self.directory, observed)["exit_code"], 1)
        observed = self.observe(request)
        observed["observations"]["command_reconciliation"][0]["attempt_id"] = "0" * 32
        self.assertEqual(self.changes.resume_plan(self.directory, observed)["exit_code"], 2)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))["exit_code"], 0)

    def test_apply_enrolls_last_and_safe_rerun_rollback_restores_mode(self):
        (self.repo / "AGENTS.md").chmod(0o755)
        request = self.request()
        result = self.complete(request)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["state"], "applied")
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nnew process\n")
        self.assertTrue((self.repo / PROJECT_FILE).exists())
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 0)
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
        self.assertEqual((self.repo / "AGENTS.md").stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)
        self.assertEqual((self.directory / "transaction.json").stat().st_mode & 0o777, 0o600)

    def test_untrusted_d3_authorization_is_insufficient(self):
        request = self.request()
        request["approval"]["evidence"] = []
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_bound_manifest_rejects_changed_content(self):
        request = self.request()
        request["manifest"]["actions"][0]["effects"][0]["after"]["mode"] = 0o755
        self.assertEqual(self.changes.apply_plan(self.directory, request)["exit_code"], 1)

    def test_user_edit_is_preserved_during_resume_and_rollback(self):
        request = self.request()
        self.assertEqual(self.complete(request)["exit_code"], 0)
        (self.repo / "AGENTS.md").write_text("user edit\n")
        self.assertEqual(self.changes.resume_plan(self.directory, request)["exit_code"], 2)
        self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 2)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "user edit\n")

    def test_crash_boundaries_reconcile_without_lost_original(self):
        for boundary in ("backup", "prepared", "temp_write", "temp_fsync", "rename", "parent_fsync", "outcome", "state"):
            with self.subTest(boundary=boundary):
                if (self.directory / "transaction.json").exists():
                    self.changes.rollback_plan(self.directory, request)
                    (self.directory / "transaction.json").unlink()
                request = self.request()
                fired = False
                def crash(stage):
                    nonlocal fired
                    if stage == boundary and not fired:
                        fired = True
                        raise RuntimeError("injected crash")
                with patch.object(self.changes, "_checkpoint", side_effect=crash):
                    with self.assertRaises(RuntimeError):
                        self.changes.apply_plan(self.directory, request)
                self.assertTrue(fired)
                result = self.changes.resume_plan(self.directory, request)
                if result["exit_code"] == 2:
                    result = self.changes.resume_plan(self.directory, self.observe(request))
                self.assertEqual(result["exit_code"], 0, result)
                self.assertEqual(self.changes.rollback_plan(self.directory, request)["exit_code"], 0)
                self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")


class ScopedChangesTests(d3_tests.OnboardingTests):
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

    def run_with_d4_host(self, request, operation):
        class Host:
            def acquire_d4_bookkeeping(owner, facts, issue): return issue(facts)
        with self.changes._bootstrap_d4_host(Host(), issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
            return self.changes._run_scoped(self.directory, request, operation, host=host)

    def test_scoped_configured_publication_and_rollback_keep_public_gate(self):
        request = self.scoped_request()
        rejected = self.changes.apply_plan(self.directory, request)
        self.assertNotEqual(rejected["exit_code"],0)
        result = self.changes._run_scoped(self.directory, request, "apply")
        self.assertEqual(result["exit_code"],0,result)
        self.assertEqual(result["activation_status"],"configured")
        self.assertFalse(result["qualified"])
        self.assertEqual(json.loads((self.repo / PROJECT_FILE).read_text()),request["manifest"]["enrollment"]["record"])
        result = self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["exit_code"],0,result)
        self.assertFalse((self.repo / PROJECT_FILE).exists())
        self.assertEqual((self.repo / "AGENTS.md").read_text(),"product rules\nlegacy process\n")

    def test_scoped_umbrella_duplicate_local_ids_and_selected_worktree_are_preserved(self):
        request = self.scoped_request(umbrella=True)
        result = self.changes._run_scoped(self.directory,request,"apply")
        self.assertEqual(result["exit_code"],0,result)
        journal = self.changes._load_journal(self.directory)
        self.assertEqual(len(journal["receipts"]),3)
        self.assertEqual(len(set(journal["receipts"])),3)
        for action in request["manifest"]["actions"]:
            self.assertEqual(self.changes._fingerprint(Path(action["owner"]["root"]),action["effects"][0]["path"]),action["effects"][0]["after"])

    def test_scoped_canonical_rename_crash_recovers_but_same_byte_replacement_blocks(self):
        request = self.scoped_request()
        def crash(stage):
            if stage == "rename" and (self.repo / PROJECT_FILE).exists(): raise RuntimeError("canonical crash")
        with patch.object(self.changes,"_checkpoint",side_effect=crash),self.assertRaises(RuntimeError):
            self.changes._run_scoped(self.directory,request,"apply")
        result = self.changes._run_scoped(self.directory,request,"resume")
        self.assertEqual(result["exit_code"],0,result)
        current = self.repo / PROJECT_FILE
        raw = current.read_bytes()
        replacement = current.with_name("external")
        replacement.write_bytes(raw); replacement.chmod(0o600); replacement.replace(current)
        result = self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["state"],"rollback_partial")
        self.assertEqual(current.read_bytes(),raw)

    def test_scoped_postimage_user_join_preserved_and_independent_member_recovers(self):
        request = self.scoped_request()
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        current = json.loads((self.repo / PROJECT_FILE).read_text())
        current["revision"] += 1
        (self.repo / PROJECT_FILE).write_text(json.dumps(current))
        newer = (self.repo / PROJECT_FILE).read_bytes()
        result = self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["state"],"rollback_partial",result)
        self.assertEqual((self.repo / PROJECT_FILE).read_bytes(),newer)
        self.assertEqual((self.repo / "AGENTS.md").read_text(),"product rules\nlegacy process\n")

    def test_scoped_corrupt_backup_and_target_parent_change_block_before_recovery(self):
        request = self.scoped_request()
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        journal = self.changes._load_journal(self.directory)
        file = next(iter(journal["receipts"].values()))["files"][0]
        (self.directory / file["backup"]).write_text("untrusted backup")
        after = (self.repo / "AGENTS.md").read_bytes()
        self.assertNotEqual(self.changes._run_scoped(self.directory,request,"rollback")["exit_code"],0)
        self.assertEqual((self.repo / "AGENTS.md").read_bytes(),after)

    def activate_request(self,request):
        from runtime.startup import canonical_scope_projection,decode_project_record,_canonical_plain
        m = request["manifest"]; record=m["enrollment"]["record"]
        repo=record["repositories"][0]; checkout=repo["checkouts"][0]; entry=checkout["clients"][0]
        entry["configuration"]["runtime"] = dict(schema_version=3,state_dir=str(self.base / "state"))
        identity=_canonical_plain(canonical_scope_projection(decode_project_record(record),repository_id=repo["repository_id"],checkout_id=checkout["checkout_id"],client="codex"))
        ref=dict(reference="fixture:qualified-session",sha256="e"*64)
        harness=self.base / "harness"; harness.mkdir(exist_ok=True); source=harness / "hook"; source.write_text("fixture hook")
        binding=dict(client="codex",surface="fixture-codex",version="fixture-1",configuration_digest="f"*64,
            hook_owner=dict(root=str(harness),binding=onboarding._filesystem_binding(harness),source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest()),
            accepted_scope_digest=onboarding._digest(identity),receipt=ref)
        entry["activation"].update(status="active",qualification="qualified",accepted_scope_digest=onboarding._digest(identity),transaction_id=m["transaction_id"],evidence=[ref],qualification_binding=binding)
        request["approval"]["manifest_digest"]=onboarding._digest(m)
        return request

    def observe_active(self,request):
        request=copy.deepcopy(request); m=request["manifest"]; record=m["enrollment"]["record"]
        proofs=[]; ref=dict(reference="fixture:qualified-session",sha256="e"*64)
        for rule in m["enrollment"]["qualification"]:
            repo=next(r for r in record["repositories"] if r["repository_id"]==rule["repository_id"])
            checkout=next(c for c in repo["checkouts"] if c["checkout_id"]==rule["checkout_id"])
            entry=next(e for e in checkout["clients"] if e["client"]==rule["client"])
            q=entry["activation"]["qualification_binding"]
            state=Path(entry["configuration"]["runtime"]["state_dir"])
            chain=[dict(path=str(p),binding=onboarding._filesystem_binding(p) if p.exists() else None,kind="directory" if p.exists() else "absent") for p in (state,*state.parents)]
            observation=dict(project_root=record["project_root"],project_binding=record["project_binding"],root=checkout["root"],primary_root=repo["primary_root"],
                git_dir=checkout["git_dir"],git_common_dir=repo["git_common_dir"],binding=repo["binding"]|checkout["binding"],membership="included",conflicting_authorities=[],dependencies=[],
                qualification=dict(current={k:q[k] for k in ("client","surface","version","configuration_digest","hook_owner")},receipt=q["receipt"],binding=q,provenance=dict(kind="trusted-coordinator",**ref)),
                runtime_location=dict(path=str(state),existing_ancestors=chain,git_probe=dict(cwd=next(a["path"] for a in chain if a["kind"]=="directory"),result="outside"),
                    exclusions=dict(package_root=str(onboarding.PACKAGE),software_roots=sorted({record["project_root"],checkout["root"],repo["primary_root"]}),git_roots=sorted({checkout["git_dir"],repo["git_common_dir"]}),plugin_cache_roots=[]),provenance=dict(kind="runtime-location-observer",**ref)))
            facts=dict(authority=dict(path=str(Path(record["project_root"])/PROJECT_FILE),target=self.changes._scoped_target(Path(record["project_root"]),str(PROJECT_FILE))),observation=observation,client="codex",
                installed=dict(policy_digest=entry["configuration"]["policy_digest"],runtime_digest=entry["configuration"]["runtime_digest"],clients=["codex"],package_root=str(onboarding.PACKAGE)))
            proofs.append({k:rule[k] for k in ("repository_id","checkout_id","client")} | dict(requirements=[dict(id=i,evidence=[ref]) for i in rule["requirements"]],general_review=[ref],security_review=[ref],scope_observations=facts))
        request["observations"]=dict(manifest_digest=onboarding._digest(m),input_digest=self.changes._scoped_input_digest(m),qualification=proofs,task_reconciliation=[],prior_compatibility=[],command_reconciliation=[])
        return request

    def test_scoped_activation_requires_actual_qualified_observations_and_postread(self):
        request=self.activate_request(self.scoped_request())
        result=self.changes._run_scoped(self.directory,request,"apply")
        self.assertFalse(result["qualified"])
        self.assertFalse((self.repo/PROJECT_FILE).exists())
        request=self.observe_active(request)
        result=self.changes._run_scoped(self.directory,request,"resume")
        self.assertEqual(result["exit_code"],0,result)
        self.assertTrue(result["qualified"])
        self.assertEqual(result["activation_status"],"active")

    def test_scoped_activation_general_review_and_changed_preimage_cannot_pass(self):
        request=self.activate_request(self.scoped_request())
        self.changes._run_scoped(self.directory,request,"apply")
        observed=self.observe_active(request)
        observed["observations"]["qualification"][0]["general_review"]=[]
        result=self.changes._run_scoped(self.directory,observed,"resume")
        self.assertFalse(result["qualified"])
        self.assertFalse((self.repo/PROJECT_FILE).exists())
        observed=self.observe_active(request)
        (self.repo/PROJECT_FILE.parent).mkdir(exist_ok=True)
        (self.repo/PROJECT_FILE).write_text("external authority")
        raw=(self.repo/PROJECT_FILE).read_bytes()
        result=self.changes._run_scoped(self.directory,observed,"resume")
        self.assertFalse(result["qualified"])
        self.assertEqual((self.repo/PROJECT_FILE).read_bytes(),raw)


    def test_scoped_published_qualification_requires_normal_resolver_postread(self):
        from runtime.startup import ScopeResolution
        request=self.activate_request(self.scoped_request())
        self.changes._run_scoped(self.directory,request,"apply")
        request=self.observe_active(request)
        with patch("runtime.startup.resolve_canonical_scope",return_value=ScopeResolution("conflict",None,"identity_changed")) as resolver:
            result=self.changes._run_scoped(self.directory,request,"resume")
        self.assertTrue(resolver.called)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["state"],"blocked")
        self.assertTrue((self.repo/PROJECT_FILE).exists())

    def test_scoped_suspension_preserves_other_member_active_pins(self):
        from runtime.startup import canonical_scope_projection,decode_project_record,_canonical_plain
        other=self.second_repository()
        location,prior=self.canonical_authority(self.base,[self.repo,other],"umbrella")
        ref=dict(reference="fixture:historical",sha256="a"*64)
        for repo in prior["repositories"]:
            checkout=repo["checkouts"][0]
            checkout["clients"]=[dict(client="codex",configuration=dict(policy_digest=policy_digest(onboarding.PACKAGE),runtime_digest=onboarding.runtime_fingerprint(),runtime=None,shared_dependencies=[],evidence=[ref]),activation=dict(status="configured",qualification="pending",accepted_scope_digest=None,transaction_id=None,evidence=[],qualification_binding=None))]
        for repo in prior["repositories"]:
            checkout=repo["checkouts"][0]
            digest=onboarding._digest(_canonical_plain(canonical_scope_projection(decode_project_record(prior),repository_id=repo["repository_id"],checkout_id=checkout["checkout_id"],client="codex")))
            checkout["clients"][0]["activation"]=dict(status="active",qualification="qualified",accepted_scope_digest=digest,transaction_id="historical",evidence=[ref],qualification_binding=dict(client="codex",surface="historical",version="1",configuration_digest="f"*64,hook_owner=dict(root=str(self.base),binding=onboarding._filesystem_binding(self.base),source=str(self.base/"hook"),sha256="e"*64),accepted_scope_digest=digest,receipt=ref))
        location.write_text(json.dumps(prior))
        (self.base/"AGENTS.md").write_text("shared fixture rules\n")
        record=self.project_record(self.base,[self.repo],"umbrella",["AGENTS.md"])
        onboarding.inspect_project(self.base,self.directory,record)
        request=self.scoped_request(existing=True)
        untouched=copy.deepcopy(prior["repositories"][1])
        snapshots=[]
        def observe(stage):
            if stage=="rename" and location.exists():
                value=json.loads(location.read_text())
                snapshots.append(value)
        with patch.object(self.changes,"_checkpoint",side_effect=observe):
            result=self.changes._run_scoped(self.directory,request,"apply")
        self.assertEqual(result["exit_code"],0,result)
        self.assertTrue(any(v["repositories"][0]["checkouts"][0]["clients"][0]["activation"]["status"]=="suspended" for v in snapshots))
        self.assertTrue(all(v["repositories"][1]==untouched for v in snapshots))

    def recovery_authority(self):
        from types import SimpleNamespace
        from tests.fixture_canonical_birth import fresh_canonical
        from runtime import evidence, state, startup
        _, acquired, _ = fresh_canonical(self)
        fixture = SimpleNamespace(scope=startup._entry_scope(acquired))
        state.start_task(fixture.scope, dict(client='codex', session_id='session', task_id='task',
            scope_ref='request:1', mode='editable', decision_ref='user:1'))
        context=evidence._evidence_context(fixture.scope)
        return fixture,context

    def test_recovery_source_registration_retains_original_age_and_reuses_snapshot(self):
        from runtime import evidence,state
        request,contexts=self.d4_bookkeeping_request();context=contexts[0];facts=[];grants=[];registered=[]
        class Host:
            def acquire_d4_bookkeeping(owner,acquired,issue):
                facts.append(evidence._canonical_plain(acquired));grant=issue(acquired);grants.append(grant);return grant
        original_register=evidence._register_recovery_source
        def register(scope,db,source,*,context=None):
            reference=original_register(scope,db,source,context=context)
            if not any(item['source'] is source for item in registered):
                pointer=evidence._canonical_plain(reference);metadata=state._metadata(db,pointer['name'])
                body=evidence._canonical_plain(evidence._read(None,pointer,context=context,db=db))
                registered.append(dict(source=source,reference=pointer,body=body,age=metadata['created_at']))
                with patch.object(evidence.uuid,'uuid4',wraps=evidence.uuid.uuid4) as allocate:
                    reused=original_register(scope,db,source,context=context)
                self.assertEqual(evidence._canonical_plain(reused),pointer);self.assertEqual(allocate.call_count,0)
                self.assertEqual(state._metadata(db,pointer['name']),metadata)
                self.assertEqual(metadata['created_at'],source.created_at)
            return reference
        with self.changes._bootstrap_d4_host(Host(),issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
            with patch.object(evidence,'_register_recovery_source',side_effect=register):
                result=self.changes._run_scoped(self.directory,request,'apply',host=host)
        self.assertEqual(result['exit_code'],0,result);self.assertEqual([f['journal']['state'] for f in facts],['prepared','applied'])
        self.assertEqual(len(grants),2);self.assertIsNot(grants[0].acquisition,grants[1].acquisition)
        self.assertEqual(len(registered),2);first,second=registered
        self.assertEqual([first['body']['journal']['state'],second['body']['journal']['state']],['prepared','applied'])
        self.assertEqual(first['body']['identity'],second['body']['identity'])
        self.assertNotEqual(first['body'],second['body']);self.assertEqual(first['body']['previous'],None)
        self.assertEqual(second['body']['previous'],first['reference']);self.assertNotEqual(first['reference'],second['reference'])
        self.assertEqual(first['age'],second['age']);self.assertEqual(first['age'],first['source'].created_at)
        self.assertEqual(second['age'],second['source'].created_at)
        with evidence._context_database(context) as db:
            history=evidence._recovery_source_history(context,db,second['reference'],second['body']['identity'])
        self.assertEqual([row[0] for row in history],[second['reference'],first['reference']])

    def recovery_release_fixture(self, *, stopped=True, assignment=False, foreign_support=False, umbrella=False):
        from runtime import evidence,state
        request,contexts=self.d4_bookkeeping_request(umbrella=umbrella);context=contexts[0];scope=self.d4_bookkeeping_scopes[0]
        self.release_fixture_contexts=contexts
        if umbrella:
            from dataclasses import replace
            from runtime.startup import _canonical_freeze,canonical_scope_projection,decode_project_record
            proposal=request['manifest']['enrollment']['record'];repository=proposal['repositories'][0];checkout=repository['checkouts'][0]
            projection=evidence._canonical_plain(canonical_scope_projection(decode_project_record(proposal),
                repository_id=repository['repository_id'],checkout_id=checkout['checkout_id'],client='codex'))
            scope=replace(scope,project_root=Path(projection['project_root']),authority_path=Path(projection['project_root'])/PROJECT_FILE,
                authority_target=_canonical_freeze(request['manifest']['enrollment']['before']['target']),authority_revision=proposal['revision'],
                accepted_identity=_canonical_freeze(projection),accepted_scope_digest=state._digest(projection))
            self.d4_bookkeeping_scopes[0]=scope
        owner=dict(client='codex',session_id='session',task_id='task',scope_ref='request:1')
        state.start_task(scope,dict(owner,mode='editable',decision_ref='user:1'))
        if assignment:
            from runtime import delegation
            with evidence._context_database(context) as db:
                task=state._task(db,state._scope_id(scope),'codex','session','task')
            owner={k:task[k] for k in ('client','session_id','task_id','scope_ref')}
            operation=delegation._reserve_operation(scope,dict(owner,expected_revision=task['revision'],
                dispatch_id='release-operation',claims=[dict(kind='path',key=str(self.repo/'AGENTS.md'),mode='write')],
                operation=dict(id='release-operation',kind='coordinator-work'),reason='Bounded fixture operation',
                ownership_ref='fixture:owner',stop_conditions=['Stop after operation']),role='coordinator')
            with evidence._context_database(context) as db:
                record=delegation._ledger(scope,db)[0]
                task=state._task(db,state._scope_id(scope),'codex','session','task')
            facts=dict(operation_id='release-operation',operation_digest=record['body']['operation']['digest'],
                claims_digest=evidence._digest(record['data']['claims']))
            if foreign_support:
                foreign=dict(client='codex',session_id='foreign',task_id='foreign',scope_ref='fixture:foreign')
                state.start_task(scope,dict(foreign,mode='editable',decision_ref='fixture:foreign-owner'))
                with state._database(scope) as db:foreign_task=state._task(db,state._scope_id(scope),'codex','foreign','foreign')
                self.release_foreign_ref=evidence._artifact(scope,dict(required='foreign original body'),owner=evidence._artifact_owner(scope,foreign_task))
                facts['foreign_support']=self.release_foreign_ref
                self.stop_assignment_fixture(scope,foreign)
            observed=evidence._artifact(scope,facts,owner=evidence._artifact_owner(scope,task))
            proof=dict(version=2,kind='coordinator-operation-reconciliation',identity=owner,expected_pins=task['pins'],
                expected_revision=task['revision'],scope_id=record['owner']['scope_id'],attempt_id=record['id'],
                dispatch_id=record['owner']['dispatch_id'],assignment_digest=record['data']['assignment']['digest'],
                decision_ref=task['decision_ref'],operation_evidence=observed,resource_observations=observed,
                operation_stopped=True,operation_descendants_stopped=True,observation_ref='fixture:operation',coordinator_ref='fixture:coordinator',**{k:v for k,v in facts.items() if k!='foreign_support'})
            raw=json.dumps(proof).encode();path=self.base/'release-host-proof.json';path.write_bytes(raw);path.chmod(0o600)
            delegation.reconcile_assignment(scope,dict(owner,expected_revision=task['revision'],attempt_id=operation['attempt_id'],
                host_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()),assessment=dict(outcome='cancelled',changed_paths=[],
                result_assessment='Stopped fixture',partial_change_assessment='No remaining effects',evidence=['fixture:inspection'],coordinator_ref='fixture:owner')))
        if stopped:self.stop_assignment_fixture(scope,owner)
        reference=dict(authority=evidence._canonical_plain(context.authority_set[0]),scope_id=state._scope_id(scope),client='codex',session_id='session',task_id='task')
        identity,old=self._publish_release_fixture(request,context)
        self.release_fixture_scope=scope
        return request,context,reference,identity,old

    def _publish_release_fixture(self,request,context):
        from runtime import evidence,state
        class Host:
            def acquire_d4_bookkeeping(owner,facts,issue):return issue(facts)
        with self.changes._bootstrap_d4_host(Host(),issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
            result=self.changes._run_scoped(self.directory,request,'apply',host=host)
        self.assertEqual(result['exit_code'],0,result)
        with evidence._context_database(context) as db:
            baseline=state._source_baseline(None,db,context=context)
            self.assertEqual(len(baseline['dependencies']),1)
            head=baseline['dependencies'][0]['reference']
        return evidence._canonical_plain(head['identity']),evidence._canonical_plain(head['artifact'])


    def stop_assignment_fixture(self,scope,identity):
        from runtime import evidence,state
        context=evidence._evidence_context(scope)
        with evidence._context_database(context) as db:
            task=state._task(db,state._scope_id(scope),identity['client'],identity['session_id'],identity['task_id'])
        unresolved=[a['id'] for a in task.get('evidence',{}).get('attempts',[]) if a['status'] in {'reserved','running','uncertain'}]
        proof=dict(version=1,kind='stopped-writer-reconciliation',identity=identity,expected_revision=task['revision'],
            expected_pins=task['pins'],observed_turn_id=task['turn_id'],stopped=True,descendants_stopped=True,
            attempt_ids=unresolved,observation_ref='fixture:stopped',coordinator_ref='fixture:owner')
        raw=json.dumps(proof).encode();path=self.base/('release-stop-'+hashlib.sha256(raw).hexdigest()+'.json')
        path.write_bytes(raw);path.chmod(0o600)
        state.retire_task(scope,dict(identity,expected_revision=task['revision'],expected_pins=task['pins'],
            reason='Stopped fixture',reference='fixture:stopped',stop_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest())))
        reference=dict(authority=evidence._canonical_plain(context.authority_set[0]),scope_id=state._scope_id(scope),
            **{k:identity[k] for k in ('client','session_id','task_id')})
        with evidence._context_database(context) as db:
            certificate=self.changes._stopped_certificate(context,db,dict(reference=reference,pins=task['pins']))
        return context,certificate

    def test_assignment_certificate_uses_actual_check_revision_without_assessment(self):
        from tests.test_evidence import CanonicalEvidenceTests
        from runtime import evidence,state,delegation
        fixture=CanonicalEvidenceTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        f=fixture.fixture;checked=f.checked()
        self.assertEqual(checked['attempt']['status'],'passed')
        context,certificate=self.stop_assignment_fixture(f.scope,f.identity)
        assignment=certificate['assignments'][0];rec=assignment['reconciliation']
        self.assertEqual(assignment['role'],'check');self.assertIsNone(rec['assessment'])
        with evidence._context_database(context) as db:
            original=evidence._read(None,rec['reference'],context=context,db=db)
            self.assertNotIn('revision',original);self.assertNotIn('assessment',original)
            self.assertEqual(rec['revision'],original['proof']['expected_revision']+1)
            self.changes._check_stopped_certificate(context,db,certificate)
        for mutate in ('outcome','proof','attempt'):
            with self.subTest(mutate=mutate),evidence._context_database(context,write=True) as db:
                task=state._task(db,state._scope_id(f.scope),'codex','parent','task')
                row=db.execute('select id,scope_id,client,session_id,task_id,dispatch_id,data from assignments').fetchone()
                record=delegation._record(None,row,context=context,db=db)
                if mutate=='outcome': record['data']['result']['outcome']='cancelled'
                elif mutate=='proof':
                    original['proof']['execution_digest']='f'*64
                    record['data']['reconciliation']=evidence._artifact(f.scope,original,owner=evidence._artifact_owner(f.scope,task),db=db)
                else: task['evidence']['attempts'][0]['candidate_digest']='f'*64
                with self.assertRaises(Exception): self.changes._assignment_certificate(context,db,task,record)

    def test_assignment_certificate_uses_actual_native_receipt(self):
        from tests.test_delegation import CanonicalDelegationTests
        from runtime import evidence
        fixture=CanonicalDelegationTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        f=fixture.fixture;prepared=f.prepare();f.observe_worker(prepared)
        f.api.reconcile_assignment(f.scope,f.worker_reconciliation_request(prepared,outcome='cancelled'))
        context,certificate=self.stop_assignment_fixture(f.scope,f.identity)
        assignment=certificate['assignments'][0];rec=assignment['reconciliation']
        self.assertEqual(assignment['role'],'worker')
        self.assertEqual(rec['proof']['native_identity'],rec['bindings']['native_receipt']['native_identity'])
        self.assertEqual(rec['proof']['result_digest'],rec['bindings']['native_receipt']['result_digest'])
        with evidence._context_database(context) as db:self.changes._check_stopped_certificate(context,db,certificate)
        for key in ('result_digest','native_identity','launch_state'):
            bad=copy.deepcopy(certificate);bad['assignments'][0]['reconciliation']['bindings']['native_receipt'][key]=None
            with self.subTest(key=key),self.assertRaises(Exception):self.changes._stopped_certificate_shape(bad)

    def test_assignment_certificate_requires_actual_linked_review(self):
        from tests.test_evidence import CanonicalEvidenceTests
        from runtime import evidence,state,delegation
        fixture=CanonicalEvidenceTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        f=fixture.fixture;f.review()
        context,certificate=self.stop_assignment_fixture(f.scope,f.identity)
        assignment=certificate['assignments'][0];self.assertEqual(assignment['role'],'review')
        with evidence._context_database(context) as db:
            self.changes._check_stopped_certificate(context,db,certificate)
            task=state._task(db,state._scope_id(f.scope),'codex','parent','task')
            record=delegation._ledger(None,db,context=context)[0]
            task['evidence']['attempts'][0]['assignment_digest']='f'*64
            with self.assertRaises(Exception):self.changes._assignment_certificate(context,db,task,record)

    def exercise_retired_assignment_recovery(self,request,context,task_ref,identity,old):
        from runtime import evidence,state
        observation=self.changes._observe_recovery_release(context.authority_set,identity)
        original_artifacts=[];old_sources=[]
        with evidence._context_database(context) as db:
            for ref,content in evidence._recovery_source_history(context,db,old,identity):
                old_sources.append(ref);original_artifacts.extend(content['artifacts'])
        result=self.changes._register_recovery_release(observation);self.assertTrue(result['complete'],result)
        certificate=evidence._canonical_plain(observation.content)['stopped'][0];assignment=certificate['assignments'][0]
        if assignment['role'] in {'worker','review'} and assignment['reconciliation']['bindings']['observation'] is None:
            rec=assignment['reconciliation'];self.assertEqual(rec['proof']['launch_state'],'not-started')
            receipt=rec['bindings']['native_receipt'];self.assertIsNone(receipt['result_digest'])
            self.assertTrue(all(receipt['native_identity'][k] is None for k in ('parent_turn_id','spawn_item_id','worker_id','worker_turn_id','result_item_id')))
        with evidence._context_database(context,write=True) as db:
            ref=assignment['reference'];marker=dict(version=1,identity=ref['owner'],assignment_id=ref['id'],
                pins_digest=state._digest(certificate['pins']),request_digest=assignment['request_digest'],created_at=assignment['created_at'],
                retired_at=assignment['created_at'],outcome='cancelled',evidence_expired=False)
            db.execute('insert into retired_identities values(?,?,?,?)',('assignment',state._retired_identity('assignment',ref['owner']),json.dumps(marker),ref['id']))
            db.execute('delete from assignments where id=?',(ref['id'],))
        self.seed_recovery_task_marker(context,task_ref,certificate)
        retained={a['artifact']['name'] for a in observation.content['artifacts']}
        for ref in [*old_sources,*[a['artifact'] for a in original_artifacts if a['artifact']['name'] not in retained]]:
            (context.directory/'evidence'/ref['name']).unlink(missing_ok=True)
        self.assertFalse((context.directory/'evidence'/assignment['reference']['assignment']['name']).exists())
        self.assertFalse((context.directory/'evidence'/assignment['reconciliation']['reference']['name']).exists())
        with evidence._context_database(context) as db:self.changes._check_stopped_certificate(context,db,certificate)
        other=copy.deepcopy(assignment['reference']);other['assignment']=dict(name='f'*64+'.json',digest='f'*64)
        with self.assertRaisesRegex(Exception,'issued pointer'):self.changes._stopped_assignment_certificate(certificate,other)
        inspected=self.changes.inspect_lifecycle_recovery(context.authority_set,(task_ref,))
        self.assertTrue(inspected['complete'],inspected)
        self.assertTrue(any(r['kind']=='assignment' and r['need']=='identity' and r['reference']==assignment['reference'] for r in inspected['requirements']))
        class Host:
            def acquire_d4_bookkeeping(owner,facts,issue):return issue(facts)
        for operation in ('resume','rollback'):
            with self.changes._bootstrap_d4_host(Host(),issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
                result=self.changes._run_scoped(self.directory,request,operation,host=host)
            self.assertEqual(result['exit_code'],0,result)
        _,sources=self.changes._recovery_sources(context)
        self.assertEqual(sources[0][1]['stopped'],[certificate])


    def recovery_release_role_fixture(self,role):
        from tests.test_evidence import CanonicalEvidenceTests
        from tests.test_delegation import CanonicalDelegationTests
        from runtime import evidence,state,delegation
        (self.repo/'app.py').write_text('value = 1\n')
        request,contexts=self.d4_bookkeeping_request();scope=self.d4_bookkeeping_scopes[0]
        native_worker=role.startswith('worker')
        fixture=(CanonicalDelegationTests if native_worker else CanonicalEvidenceTests)(methodName='runTest')
        fixture.setUp();self.addCleanup(fixture.doCleanups);f=fixture.fixture
        if native_worker:
            cached=delegation.guidance_status(f.scope,f.cache_request)
            guidance=evidence._read(f.scope,cached['snapshot_ref'])
        f.scope=scope;f.repo=self.repo;f.root=self.base
        state.start_task(scope,dict(f.identity,mode='editable',decision_ref='user:accepted'))
        if native_worker:
            f.fixture.scope=scope;f.fixture.repo=self.repo;f.fixture.root=self.base;f.fixture.plan()
            claim=delegation.claim_guidance(scope,f.cache_request|dict(trigger=dict(kind='routine',id='release-fixture')))
            delegation.record_guidance(scope,f.cache_request|dict(token=claim['token'],generation=claim['generation'],
                coordinator_ref='fixture:checked',guidance=guidance))
            f.plan()
        else:f.plan()
        if role=='check':f.checked()
        elif role=='review':f.review()
        else:
            if native_worker:
                prepared=f.prepare()
                if role=='worker':f.observe_worker(prepared)
                delegation.reconcile_assignment(f.scope,f.worker_reconciliation_request(prepared,outcome='cancelled'))
            else:
                prepared=evidence.prepare_review(f.scope,f.native_review_request(coverage=['general','security'],criteria=['works'],assignment='Read-only review fixture'))
                with state._database(f.scope) as db:
                    record=delegation._ledger(f.scope,db)[0]
                    task=state._task(db,state._scope_id(f.scope),'codex','parent','task')
                data=record['data'];native={k:data['binding'][k] for k in ('client_surface','native_version','parent_session_id','task_name')}
                native.update({k:None for k in ('parent_turn_id','spawn_item_id','worker_id','worker_turn_id','result_item_id')})
                proof=dict(version=2,kind='assignment-writer-reconciliation',identity=f.identity,expected_pins=task['pins'],
                    expected_revision=task['revision'],scope_id=record['owner']['scope_id'],attempt_id=record['id'],dispatch_id=record['owner']['dispatch_id'],
                    assignment_digest=data['assignment']['digest'],candidate_digest=data['candidate']['digest'],
                    current_candidate_digest=evidence._digest(evidence.capture_candidate(f.scope,evidence._read(f.scope,task['evidence']['plan']))),
                    d6_plan_digest=data['plan_digest'],claims_digest=evidence._digest(data['claims']),observation_digest=None,result_digest=None,
                    native_identity=native,launch_state='not-started',stopped=True,descendants_stopped=True,
                    observation_ref='fixture:independent-no-launch-inspection',coordinator_ref='fixture:owner')
                raw=json.dumps(proof).encode();path=f.root/'not-started-review.json';path.write_bytes(raw);path.chmod(0o600)
                delegation.reconcile_assignment(f.scope,f.request(attempt_id=record['id'],host_proof=dict(path=str(path),sha256=hashlib.sha256(raw).hexdigest()),
                    assessment=dict(outcome='cancelled',changed_paths=[],result_assessment='No launch independently established',
                        partial_change_assessment='No effects',evidence=['fixture:host-inspection'],coordinator_ref='fixture:owner')))
        context,certificate=self.stop_assignment_fixture(f.scope,f.identity)
        identity,old=self._publish_release_fixture(request,context)
        return request,context,certificate['reference'],identity,old


    def test_assignment_certificate_mixed_store_retry_retains_raw_support(self):
        from runtime import evidence
        _,first,task_ref,identity,_=self.recovery_release_fixture(assignment=True,umbrella=True)
        contexts=self.release_fixture_contexts
        authorities=tuple(authority for context in contexts for authority in context.authority_set)
        observation=self.changes._observe_recovery_release(authorities,identity)
        original=evidence._register_recovery_source;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(kwargs['context'].directory)
            if len(calls)==2:raise OSError('fixture assignment second-store fault')
            return original(*args,**kwargs)
        with patch.object(evidence,'_register_recovery_source',side_effect=fail_second):
            result=self.changes._register_recovery_release(observation)
        self.assertFalse(result['complete']);self.assertEqual(len(result['replacements']),1)
        inspected=self.changes.inspect_lifecycle_recovery(authorities,())
        self.assertTrue(inspected['complete'],inspected)
        self.assertTrue(any(r['kind']=='assignment' and r['need']=='raw' for r in inspected['requirements']))
        replay=self.changes._register_recovery_release(self.changes._observe_recovery_release(authorities,identity))
        self.assertTrue(replay['complete'],replay)
        inspected=self.changes.inspect_lifecycle_recovery(authorities,())
        self.assertFalse(any(r['kind']=='assignment' and r['need']=='raw' for r in inspected['requirements']))


    def seed_recovery_task_marker(self,context,reference,certificate):
        # Explicit future-owner fixture: the production closure/retirement writer remains gated.
        from runtime import evidence,state
        with evidence._context_database(context,write=True) as db:
            identity={k:reference[k] for k in ('scope_id','client','session_id','task_id')}
            marker=dict(version=1,identity=identity,pins_digest=state._digest(certificate['pins']),request_digest=None,
                created_at=certificate['created_at'],retired_at=certificate['created_at'],outcome='cancelled',evidence_expired=False)
            db.execute('insert into retired_identities values(?,?,?,NULL)',('task',state._retired_identity('task',identity),json.dumps(marker)))
            db.execute('delete from tasks where scope_id=? and client=? and session_id=? and task_id=?',tuple(identity.values()))


    def test_recovery_release_mixed_store_heads_protect_raw_until_retry(self):
        from runtime import evidence
        _,first,task_ref,identity,old=self.recovery_release_fixture(umbrella=True)
        contexts=self.release_fixture_contexts
        authorities=tuple(authority for context in contexts for authority in context.authority_set)
        observation=self.changes._observe_recovery_release(authorities,identity)
        original=evidence._register_recovery_source;calls=[]
        def fail_second(*args,**kwargs):
            calls.append(kwargs['context'].directory)
            if len(calls)==2: raise OSError('fixture second-store fault')
            return original(*args,**kwargs)
        with patch.object(evidence,'_register_recovery_source',side_effect=fail_second):
            result=self.changes._register_recovery_release(observation)
        self.assertFalse(result['complete']);self.assertEqual(len(result['replacements']),1)
        inspected=self.changes.inspect_lifecycle_recovery(authorities,())
        self.assertTrue(inspected['complete'],inspected)
        raw=[r for r in inspected['requirements'] if r['kind']=='task' and r['reference']==task_ref and r['need']=='raw']
        identity_only=[r for r in inspected['requirements'] if r['kind']=='task' and r['reference']==task_ref and r['need']=='identity']
        self.assertTrue(raw);self.assertTrue(identity_only)
        self.assertNotEqual(raw[0]['root'],identity_only[0]['root'])
        retry=self.changes._register_recovery_release(self.changes._observe_recovery_release(authorities,identity))
        self.assertTrue(retry['complete'],retry)
        inspected=self.changes.inspect_lifecycle_recovery(authorities,())
        self.assertFalse(any(r['kind']=='task' and r['reference']==task_ref and r['need']=='raw' for r in inspected['requirements']))


    def test_guarded_recovery_inspector_has_no_reentrant_locks(self):
        request,contexts=self.d4_bookkeeping_request();context=contexts[0]
        self.assertEqual(self.run_with_d4_host(request,'apply')['exit_code'],0)
        public=self.changes.inspect_lifecycle_recovery(context.authority_set,())
        with self.changes._authority_guards([context.directory],observe=True):
            with patch.object(self.changes,'_authority_guards',side_effect=AssertionError('reentrant authority guard')), \
                 patch.object(self.changes,'_lock',side_effect=AssertionError('reentrant physical lock')):
                guarded=self.changes._inspect_lifecycle_recovery_guarded(context.authority_set,())
        self.assertTrue(guarded['complete'],guarded);self.assertEqual(guarded,public)
        self.assertEqual(guarded['discovered_authorities'],())
        from runtime import evidence
        original=evidence._context_database
        def readers_only(selected,**kwargs):
            self.assertFalse(kwargs.get('write',False),'guarded inspector opened a second writer')
            return original(selected,**kwargs)
        with self.changes._authority_guards([context.directory],observe=True):
            with original(context,write=True):
                with patch.object(evidence,'_context_database',side_effect=readers_only), \
                     patch.object(self.changes,'_authority_guards',side_effect=AssertionError('nested guard')):
                    inside=self.changes._inspect_lifecycle_recovery_guarded(context.authority_set,())
                    self.assertEqual(inside,public)

    def test_guarded_recovery_inspector_reports_actual_missing_authority_closure(self):
        from runtime import evidence
        request,contexts=self.d4_bookkeeping_request(umbrella=True);first,second=contexts
        self.assertEqual(self.run_with_d4_host(request,'apply')['exit_code'],0)
        journal=self.changes._load_journal(self.directory)
        source=self.changes._observe_recovery_source(self.directory,journal,(first,second),())
        with self.changes._authority_guards([first.directory],observe=True):
            with patch.object(self.changes,'_authority_guards',side_effect=AssertionError('must release then reacquire')):
                incomplete=self.changes._inspect_lifecycle_recovery_guarded(first.authority_set,())
        self.assertFalse(incomplete['complete']);self.assertEqual(incomplete['authorities'],first.authority_set)
        self.assertEqual(incomplete['discovered_authorities'],second.authority_set)
        public=self.changes.inspect_lifecycle_recovery(first.authority_set,())
        self.assertTrue(public['complete'],public);self.assertEqual(len(public['authorities']),2)
        self.assertEqual(public['discovered_authorities'],())
        def snapshot():
            result=[]
            for context in contexts:
                with evidence._context_database(context) as db:
                    result.append((db.execute('select * from evidence_metadata order by name').fetchall(),
                        {path.name:path.read_bytes() for path in (context.directory/'evidence').iterdir()}))
            return result
        before=snapshot()
        current=self.changes._observe_recovery_source(self.directory,journal,(first,),())
        with evidence._context_database(first,write=True) as db, \
                self.assertRaisesRegex(self.changes.StartupError,'Recovery publication requires its actual active owner lifetime'):
            evidence._register_recovery_source(None,db,current,context=first)
        self.assertEqual(snapshot(),before)

    def reconstructed_v1_history(self):
        from tests.fixture_d4_history import ReconstructedV1History
        fixture=ReconstructedV1History();self.addCleanup(fixture.cleanup)
        return fixture

    def test_reconstructed_v1_history_discovers_prior_authority_without_rewriting(self):
        from runtime import evidence,state
        from tests.fixture_d4_history import HISTORICAL_RUNTIME_DIGEST
        fixture=self.reconstructed_v1_history();first,second=fixture.contexts
        before=fixture.snapshot()
        with evidence._context_database(first) as db:
            baseline=state._source_baseline(None,db,context=first)
            self.assertEqual(baseline['content']['source_contract'],2)
            self.assertEqual(baseline['content']['runtime_digest'],HISTORICAL_RUNTIME_DIGEST)
            self.assertNotEqual(baseline['content']['runtime_digest'],state.runtime_fingerprint())
            self.assertEqual(baseline['content']['created_at'],fixture.AUTHORITY_CREATED_AT)
            self.assertEqual(baseline['dependencies'],(evidence._canonical_freeze(dict(kind='recovery',reference=dict(identity=fixture.identity,artifact=fixture.head))),))
            head=next(value for ref,value in self.changes._recovery_sources(first)[1] if ref==fixture.head)
            self.assertEqual(head['version'],1);self.assertEqual(head['authorities'],[fixture.authorities[0]])
            self.assertEqual(head['tasks'],[]);self.assertEqual(head['assignments'],[]);self.assertEqual(head['artifacts'],[])
            self.assertEqual(head['previous'],fixture.prior)
            history=evidence._recovery_source_history(first,db,fixture.head,fixture.identity)
            self.assertEqual([ref for ref,_ in history],[fixture.head,fixture.prior])
            self.assertIn(fixture.authorities[1],history[1][1]['authorities'])
            for ref,body in history:
                metadata,raw=evidence._managed_artifact(None,ref,context=first,db=db)
                self.assertEqual(metadata['version'],1);self.assertEqual(metadata['owner'],fixture.owner)
                self.assertEqual(metadata['created_at'],fixture.SOURCE_CREATED_AT)
                self.assertEqual(metadata['managed']['mode'],0o400)
                self.assertEqual(metadata['bytes'],len(raw));self.assertEqual(metadata['managed']['sha256'],hashlib.sha256(raw).hexdigest())
                expected=[dict(kind='recovery',reference=dict(authority=fixture.authorities[0],artifact=fixture.prior))] if ref==fixture.head else []
                self.assertEqual(metadata['dependencies'],expected)
            for table in state.CANONICAL_TABLE_SQL:
                if table!='evidence_metadata':self.assertEqual(db.execute('select count(*) from '+table).fetchone()[0],0)
        with evidence._context_database(second) as db:
            self.assertEqual(self.changes._recovery_sources(second)[1],[(fixture.second_source,history[1][1])])
            self.assertEqual(state._metadata(db,fixture.second_source['name'])['created_at'],fixture.SOURCE_CREATED_AT)
        for context in fixture.contexts:
            for location in self.changes._authority_locations([context.directory]):
                self.assertIsNone(self.changes._authority_owner(location))
            with evidence._context_database(context) as db:
                for table in state.CANONICAL_TABLE_SQL:
                    if table!='evidence_metadata':self.assertEqual(db.execute('select count(*) from '+table).fetchone()[0],0)
                for name,digest in db.execute('select name,digest from evidence_metadata'):
                    metadata,raw=evidence._managed_artifact(None,dict(name=name,digest=digest),context=context,db=db)
                    body=json.loads(raw)
                    self.assertIn(body['kind'],('authority-source-baseline','d4-recovery-source'))
                    self.assertEqual(metadata['version'],1);self.assertEqual(metadata['owner'],fixture.owner)
                    self.assertEqual(metadata['class'],'compact');self.assertEqual(metadata['status'],'live')
                    self.assertIsNone(metadata['closed_at']);self.assertIsNone(metadata['export']);self.assertEqual(metadata['targets'],[])
                    self.assertEqual(metadata['managed']['mode'],0o400)
                    self.assertEqual(metadata['created_at'],fixture.AUTHORITY_CREATED_AT if body['kind']=='authority-source-baseline' else fixture.SOURCE_CREATED_AT)
        self.assertEqual(fixture.journal['plan']['package_pins']['runtime_digest'],HISTORICAL_RUNTIME_DIGEST)
        self.changes._scoped_journal_shape(fixture.journal)
        with self.changes._authority_guards([first.directory],observe=True):
            with patch.object(self.changes,'_authority_guards',side_effect=AssertionError('must release then reacquire')):
                incomplete=self.changes._inspect_lifecycle_recovery_guarded(first.authority_set,())
        self.assertFalse(incomplete['complete']);self.assertEqual(incomplete['authorities'],first.authority_set)
        self.assertEqual(incomplete['discovered_authorities'],second.authority_set)
        self.assertIn('recovery_authority_set_incomplete',[row['reason'] for row in incomplete['blockers']])
        inspected=self.changes.inspect_lifecycle_recovery(first.authority_set,())
        self.assertTrue(inspected['complete'],inspected);self.assertEqual(inspected['blockers'],())
        self.assertEqual(inspected['discovered_authorities'],())
        self.assertEqual({evidence._raw(evidence._canonical_plain(a)) for a in inspected['authorities']},{evidence._raw(a) for a in fixture.authorities})
        self.assertEqual(fixture.snapshot(),before)

    def test_reconstructed_v1_history_requires_retained_traversal_for_discovery(self):
        from runtime import evidence
        fixture=self.reconstructed_v1_history();first,second=fixture.contexts;before=fixture.snapshot()
        history=evidence._recovery_source_history;calls=[]
        def head_only(*args,**kwargs):
            actual=history(*args,**kwargs);calls.append(len(actual));return actual[:1]
        with patch.object(evidence,'_recovery_source_history',side_effect=head_only):
            with self.changes._authority_guards([first.directory],observe=True):
                inspected=self.changes._inspect_lifecycle_recovery_guarded(first.authority_set,())
        self.assertTrue(calls);self.assertIn(2,calls)
        self.assertEqual(inspected['discovered_authorities'],());self.assertEqual(inspected['authorities'],first.authority_set)
        self.assertTrue(inspected['complete'],inspected)
        self.assertEqual(fixture.snapshot(),before)

    def test_reconstructed_v1_history_corruption_stays_protective_without_repair(self):
        for corruption in ('missing-prior','wrong-previous-edge'):
            with self.subTest(corruption=corruption):
                fixture=self.reconstructed_v1_history();first,second=fixture.contexts
                if corruption=='missing-prior':
                    (first.directory/'evidence'/fixture.prior['name']).unlink()
                else:
                    with closing(sqlite3.connect(first.database)) as db,db:
                        raw=db.execute('select data from evidence_metadata where name=?',(fixture.head['name'],)).fetchone()[0]
                        metadata=json.loads(raw);metadata['dependencies']=[]
                        db.execute('update evidence_metadata set data=? where name=?',(json.dumps(metadata),fixture.head['name']))
                before=fixture.snapshot();inspected=self.changes.inspect_lifecycle_recovery(first.authority_set,())
                self.assertFalse(inspected['complete'],inspected);self.assertTrue(inspected['blockers'])
                if corruption=='wrong-previous-edge':
                    self.assertIn('Recovery typed metadata dependencies differ from owner source',[row['reason'] for row in inspected['blockers']])
                else:
                    self.assertIn('recovery_source_unavailable',[row['reason'] for row in inspected['blockers']])
                self.assertEqual(fixture.snapshot(),before)

    def scoped_recovery_request(self, *, umbrella=False):
        request=self.scoped_request(umbrella=umbrella)
        fixture,context=self.recovery_authority()
        for repository in request['manifest']['enrollment']['record']['repositories']:
            for checkout in repository['checkouts']:
                checkout['clients'][0]['configuration']['runtime']=dict(schema_version=3,state_dir=str(context.directory))
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        return request,fixture,context

    def test_repeated_scoped_rollback_settles_exact_operation_markers(self):
        request,contexts=self.d4_bookkeeping_request();context=contexts[0]
        for action in ('apply','rollback','rollback'):
            result=self.run_with_d4_host(request,action)
            self.assertEqual(result['exit_code'],0,dict(action=action,result=result))
            recovery=self.changes.inspect_lifecycle_recovery(context.authority_set,())
            self.assertTrue(recovery['complete'],dict(action=action,blockers=recovery['blockers']))
            self.assertFalse(any(self.changes._authority_owner(location) for location in
                self.changes._authority_locations([context.directory])))

    def test_d4_bookkeeping_without_actual_host_authority_stays_pending(self):
        from runtime import evidence,state
        request,_,context=self.scoped_recovery_request()
        before={a['id']:self.changes._fingerprint(Path(a['owner']['root']),a['effects'][0]['path'])
            for a in request['manifest']['actions']}
        with evidence._context_database(context) as db:
            baseline=evidence._canonical_plain(state._source_baseline(None,db,context=context))
        result=self.changes._run_scoped(self.directory,request,'apply')
        self.assertEqual(result['exit_code'],2,result)
        self.assertTrue(any('host' in row['reason'].lower() for row in result['blocked']),result)
        for action in request['manifest']['actions']:
            self.assertEqual(self.changes._fingerprint(Path(action['owner']['root']),action['effects'][0]['path']),before[action['id']])
        with evidence._context_database(context) as db:
            self.assertEqual(evidence._canonical_plain(state._source_baseline(None,db,context=context)),baseline)

    def d4_bookkeeping_request(self, *, umbrella=False, state_root=None):
        from runtime import evidence,state
        from runtime.startup import ProjectScope,RuntimeSettings
        from tests.test_state import canonical_scope
        request=self.scoped_request(umbrella=umbrella);record=request['manifest']['enrollment']['record'];contexts=[]
        self.d4_bookkeeping_scopes=[]
        for index,repository in enumerate(record['repositories']):
            checkout=repository['checkouts'][0]
            scope=canonical_scope(ProjectScope(record['project_id'],Path(checkout['root']),Path(repository['git_common_dir']),
                policy_digest(state.PACKAGE),RuntimeSettings(2,(state_root or self.base)/('d4-bookkeeping-'+str(index)))),
                repository_id=repository['repository_id'],checkout_id=checkout['checkout_id'],
                primary_root=Path(repository['primary_root']),git_dir=Path(checkout['git_dir']))
            with state._database(scope,write=True,initialize=True):pass
            checkout['clients'][0]['configuration']['runtime']=dict(schema_version=3,state_dir=str(scope.runtime.state_dir))
            contexts.append(evidence._evidence_context(scope))
            self.d4_bookkeeping_scopes.append(scope)
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        return request,contexts

    def test_d4_real_entry_uses_fresh_batch_grants_and_independent_release(self):
        from runtime import evidence,state
        request,contexts=self.d4_bookkeeping_request(umbrella=True);grants=[];facts=[]
        class Host:
            def acquire_d4_bookkeeping(self,acquired,issue):
                facts.append(evidence._canonical_plain(acquired));grant=issue(acquired);grants.append(grant);return grant
        with self.changes._bootstrap_d4_host(Host(),issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
            result=self.changes._run_scoped(self.directory,request,'apply',host=host)
            self.assertEqual(result['exit_code'],0,result)
            self.assertEqual([item['journal']['state'] for item in facts],['prepared','applied'])
            self.assertIsNone(facts[0]['permissions']['record']['target']['binding'])
            self.assertIsNone(facts[0]['permissions']['record']['content'])
            self.assertIsNotNone(facts[1]['permissions']['record']['target']['binding'])
            self.assertEqual(facts[1]['permissions']['record']['content'],request['manifest']['enrollment']['record'])
            self.assertIsNot(grants[0].acquisition,grants[1].acquisition)
            for grant in grants:
                with self.assertRaises(self.changes.StartupError):self.changes._check_d4_grant(grant)
        authorities=[evidence._canonical_plain(context.authority_set[0]) for context in contexts]
        ages=[]
        for context in contexts:
            self.assertTrue(all(self.changes._authority_owner(location) is None for location in self.changes._authority_locations([context.directory])))
            with evidence._context_database(context) as db:
                baseline=state._source_baseline(None,db,context=context);self.assertEqual(len(baseline['dependencies']),1)
                pointer=baseline['dependencies'][0]['reference']['artifact'];ages.append(state._metadata(db,pointer['name'])['created_at'])
        self.assertEqual(len(set(ages)),1)
        identity=facts[0]['identity']
        observed=self.changes._observe_recovery_release(authorities,identity)
        released=self.changes._register_recovery_release(observed)
        self.assertTrue(released['complete'],released)

    def test_d4_host_callback_cannot_change_the_callers_approved_publication(self):
        request,_=self.d4_bookkeeping_request();approved=copy.deepcopy(request['manifest']['enrollment']['record']);mutations=[]
        class Host:
            def acquire_d4_bookkeeping(owner,facts,issue):
                grant=issue(facts)
                if not mutations:
                    request['manifest']['enrollment']['record']['revision']+=100
                    mutations.append(True)
                return grant
        with self.changes._bootstrap_d4_host(Host(),issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
            result=self.changes._run_scoped(self.directory,request,'apply',host=host)
        self.assertEqual(mutations,[True])
        self.assertEqual(json.loads((self.repo/PROJECT_FILE).read_text()),approved)
        self.assertEqual(result['exit_code'],0,result)

    def d4_reachable_taskless_guidance(self, *, task_subject=False):
        from runtime import delegation,evidence,state
        from runtime.startup import ProjectScope,RuntimeSettings,canonical_scope_projection,decode_project_record
        from tests.test_evidence import EvidenceTests
        from tests.test_state import canonical_scope
        from tests.test_startup import CanonicalAcquisitionTests
        from types import SimpleNamespace
        request,contexts=self.d4_bookkeeping_request();scope=self.d4_bookkeeping_scopes[0]
        foreign=EvidenceTests(methodName='runTest');foreign.setUp();self.addCleanup(foreign.doCleanups)
        source_scope=canonical_scope(ProjectScope('co-resident-project',foreign.repo,foreign.repo/'.git',
            policy_digest(state.PACKAGE),RuntimeSettings(2,self.base/'d4-bookkeeping-0')),
            repository_id='co-resident-repository',checkout_id='co-resident-checkout')
        value,path=CanonicalAcquisitionTests.record(SimpleNamespace(repo=foreign.repo,base=foreign.root,assertIsNone=self.assertIsNone))
        identity=evidence._canonical_plain(source_scope.accepted_identity)
        value['project_id']=identity['project_id'];repository=value['repositories'][0];repository['repository_id']=identity['repository_id']
        checkout=repository['checkouts'][0];checkout['checkout_id']=identity['checkout_id'];checkout['clients'][0]['configuration'].update(identity['configuration'])
        path.write_text(json.dumps(value))
        self.assertEqual(evidence._canonical_plain(canonical_scope_projection(decode_project_record(value),
            repository_id=identity['repository_id'],checkout_id=identity['checkout_id'],client='codex')),identity)
        state.start_task(source_scope,dict(client='codex',session_id='guidance-owner',task_id='guidance-owner',
            scope_ref='fixture:guidance-owner',mode='read-only',decision_ref='fixture:accepted-guidance-owner'))
        if task_subject:
            with state._database(source_scope) as db:
                source_task=state._task(db,state._scope_id(source_scope),'codex','guidance-owner','guidance-owner')
            guidance=evidence._artifact(source_scope,dict(observation='Genuine read-only B task support'),
                owner=evidence._artifact_owner(source_scope,source_task))
        else:
            cache=dict(client_surface='codex-desktop',applicability=dict(client_version='fixture',capability_fingerprint='a'*64))
            claim=delegation.claim_guidance(source_scope,cache|dict(trigger=dict(kind='manual',id='co-resident-source')))
            guidance=delegation.record_guidance(source_scope,cache|dict(token=claim['token'],generation=claim['generation'],coordinator_ref='fixture:verified-guidance',
                guidance=dict(schema_version=1,client_surface=cache['client_surface'],checked_at='2026-01-01T00:00:00Z',applicability=cache['applicability'],
                    sources=[dict(id='official',url='https://example.invalid/official',sha256='b'*64)],recommendations=[])))['snapshot_ref']
        owner=dict(client='codex',session_id='consumer',task_id='consumer',scope_ref='fixture:guidance-consumer')
        state.start_task(scope,owner|dict(mode='editable',decision_ref='fixture:accepted-consumer'))
        prepared=delegation._reserve_operation(scope,owner|dict(expected_revision=1,dispatch_id='guidance-consumption',
            claims=[dict(kind='path',key=str(self.repo/'AGENTS.md'),mode='write')],operation=dict(id='guidance-consumption',kind='coordinator-work'),
            reason='Consume actual retained guidance',ownership_ref='fixture:consumer',stop_conditions=['Stop after observation']),role='coordinator')
        with state._database(scope) as db:
            task=state._task(db,state._scope_id(scope),'codex','consumer','consumer');record=delegation._ledger(scope,db)[0]
        facts=dict(operation_id='guidance-consumption',operation_digest=record['body']['operation']['digest'],claims_digest=evidence._digest(record['data']['claims']))
        observation=evidence._artifact(scope,dict(facts,guidance=guidance),owner=evidence._artifact_owner(scope,task))
        proof=dict(version=2,kind='coordinator-operation-reconciliation',identity=owner,expected_pins=task['pins'],expected_revision=task['revision'],
            scope_id=record['owner']['scope_id'],attempt_id=record['id'],dispatch_id=record['owner']['dispatch_id'],assignment_digest=record['data']['assignment']['digest'],
            decision_ref=task['decision_ref'],operation_evidence=observation,resource_observations=observation,operation_stopped=True,operation_descendants_stopped=True,
            observation_ref='fixture:independent-observation',coordinator_ref='fixture:independent-owner',**facts)
        raw=json.dumps(proof).encode();proof_path=self.base/'guidance-operation-stop.json';proof_path.write_bytes(raw);proof_path.chmod(0o600)
        delegation.reconcile_assignment(scope,owner|dict(expected_revision=task['revision'],attempt_id=prepared['attempt_id'],
            host_proof=dict(path=str(proof_path),sha256=hashlib.sha256(raw).hexdigest()),assessment=dict(outcome='cancelled',changed_paths=[],
                result_assessment='Observed stopped operation',partial_change_assessment='No remaining effects',evidence=['fixture:inspection'],coordinator_ref='fixture:owner')))
        task=state.task_status(scope,'codex','consumer')['task']
        stop=dict(version=1,kind='stopped-writer-reconciliation',identity=owner,expected_revision=task['revision'],expected_pins=task['pins'],observed_turn_id=task['turn_id'],
            stopped=True,descendants_stopped=True,attempt_ids=[],observation_ref='fixture:stopped-consumer',coordinator_ref='fixture:independent-owner')
        raw=json.dumps(stop).encode();stop_path=self.base/'guidance-consumer-stop.json';stop_path.write_bytes(raw);stop_path.chmod(0o600)
        state.retire_task(scope,owner|dict(expected_revision=task['revision'],expected_pins=task['pins'],reason='Stopped consumer',reference='fixture:stopped',
            stop_proof=dict(path=str(stop_path),sha256=hashlib.sha256(raw).hexdigest())))
        with evidence._context_database(contexts[0]) as db:
            metadata=state._metadata(db,guidance['name'])
            if task_subject:
                self.assertEqual(metadata['owner']['project_id'],source_scope.project_id)
                self.assertEqual(metadata['owner']['task']['task_id'],source_task['task_id'])
            else:self.assertEqual(metadata['owner'],dict(kind='project',project_id=source_scope.project_id,task=None))
            artifact_refs=[];delegation._ledger(None,db,context=contexts[0],artifact_refs=artifact_refs)
            reachable=[];pending=list(artifact_refs)
            while pending:
                reference=pending.pop()
                if reference in reachable:continue
                reachable.append(reference);metadata,_=evidence._managed_artifact(None,reference,context=contexts[0],db=db)
                for edge in metadata['dependencies']:
                    if edge['kind']=='artifact':
                        target=edge['reference'];pending.append(target.get('artifact',target))
            self.assertIn(guidance,reachable)
        return request,path,guidance,contexts[0],source_scope.project_id


    def test_d4_late_baseline_change_cannot_become_the_registration_basis(self):
        from runtime import evidence,state
        request,contexts=self.d4_bookkeeping_request();context=contexts[0];changed=[]
        class Host:
            def acquire_d4_bookkeeping(owner,facts,issue):
                grant=issue(facts)
                with evidence._context_database(context,write=True) as db:
                    baseline=state._source_baseline(None,db,context=context)
                    metadata=state._metadata(db,baseline['reference']['name'])
                    identity=dict(evidence._canonical_plain(facts['identity']),transaction_id='unrecognized-protective-owner')
                    metadata['dependencies'].append(dict(kind='recovery',reference=dict(identity=identity,artifact=evidence._canonical_plain(baseline['reference']))))
                    db.execute('update evidence_metadata set data=? where name=?',(evidence._raw(metadata).decode(),baseline['reference']['name']))
                changed.append(True);return grant
        before={a['id']:self.changes._fingerprint(Path(a['owner']['root']),a['effects'][0]['path']) for a in request['manifest']['actions']}
        with self.changes._bootstrap_d4_host(Host(),issuer=self.changes._D4_HOST_BOOTSTRAP_ISSUER) as host:
            result=self.changes._run_scoped(self.directory,request,'apply',host=host)
        self.assertEqual(changed,[True]);self.assertEqual(result['exit_code'],2,result)
        self.assertTrue(any('baseline changed' in row['reason'] for row in result['blocked']),result)
        for action in request['manifest']['actions']:
            self.assertEqual(self.changes._fingerprint(Path(action['owner']['root']),action['effects'][0]['path']),before[action['id']])

    def test_recovery_hooks_register_before_effect_and_after_final_journal(self):
        from runtime import evidence,state
        request,contexts=self.d4_bookkeeping_request();context=contexts[0]
        inspected=[]
        def checkpoint(stage):
            if stage=='prepared':
                with evidence._context_database(context) as db:
                    baseline=state._source_baseline(None,db,context=context)
                    self.assertEqual(len(baseline['dependencies']),1)
                    ref=dict(baseline['dependencies'][0]['reference']['artifact'])
                    source=evidence._read(None,ref,context=context,db=db)
                    self.assertEqual(source['journal']['state'],'prepared')
                    self.assertEqual(source['tasks'],[])
                    inspected.append(True)
        with patch.object(self.changes,'_checkpoint',side_effect=checkpoint):
            result=self.run_with_d4_host(request,'apply')
        self.assertEqual(result['exit_code'],0,result)
        self.assertTrue(inspected)
        recovery=self.changes.inspect_lifecycle_recovery(context.authority_set,())
        self.assertTrue(recovery['complete'],recovery)
        self.assertEqual(len(recovery['dependencies']),1)
        self.assertEqual(recovery['dependencies'][0]['journal']['state'],'applied')
        self.assertEqual(recovery['dependencies'][0]['transaction_id'],'scoped-fixture')

    def test_recovery_final_registration_failure_keeps_markers_and_resumes(self):
        from runtime import evidence
        request,contexts=self.d4_bookkeeping_request();context=contexts[0]
        original=evidence._register_recovery_source
        def register(scope,db,source,**kwargs):
            if source.content['journal']['state']=='applied': raise self.changes.Pending('fixture final store failure')
            return original(scope,db,source,**kwargs)
        with patch.object(evidence,'_register_recovery_source',side_effect=register):
            result=self.run_with_d4_host(request,'apply')
        self.assertNotEqual(result['exit_code'],0,result)
        locations=self.changes._authority_locations([context.directory])
        self.assertTrue(all(self.changes._authority_owner(location) is not None for location in locations))
        self.assertFalse(self.changes.inspect_lifecycle_recovery(context.authority_set,())['complete'])
        result=self.run_with_d4_host(request,'resume')
        self.assertEqual(result['exit_code'],0,result)
        self.assertTrue(all(self.changes._authority_owner(location) is None for location in locations))
        self.assertTrue(self.changes.inspect_lifecycle_recovery(context.authority_set,())['complete'])


    def test_recovery_two_store_admission_failure_preserves_markers_and_preimages(self):
        from runtime import evidence,state
        request,contexts=self.d4_bookkeeping_request(umbrella=True)
        original=evidence._register_recovery_source; calls=[]
        before={a['id']:self.changes._fingerprint(Path(a['owner']['root']),a['effects'][0]['path']) for a in request['manifest']['actions']}
        def register(scope,db,source,**kwargs):
            calls.append(kwargs['context'])
            if len(calls)==2: raise self.changes.Pending('fixture second authority failure')
            return original(scope,db,source,**kwargs)
        with patch.object(evidence,'_register_recovery_source',side_effect=register):
            result=self.run_with_d4_host(request,'apply')
        self.assertNotEqual(result['exit_code'],0,result)
        for action in request['manifest']['actions']:
            self.assertEqual(self.changes._fingerprint(Path(action['owner']['root']),action['effects'][0]['path']),before[action['id']])
        for context in contexts:
            self.assertTrue(all(self.changes._authority_owner(location) is not None for location in self.changes._authority_locations([context.directory])))
        with evidence._context_database(contexts[0]) as db:
            baseline=state._source_baseline(None,db,context=contexts[0]); first=dict(baseline['dependencies'][0]['reference']['artifact'])
            age=state._metadata(db,first['name'])['created_at']
        self.assertEqual(self.run_with_d4_host(request,'resume')['exit_code'],0)
        result=self.changes.inspect_lifecycle_recovery(contexts[0].authority_set,())
        self.assertTrue(result['complete'],result)
        self.assertEqual(len(result['dependencies']),2)
        with evidence._context_database(contexts[0]) as db:
            baseline=state._source_baseline(None,db,context=contexts[0]); current=dict(baseline['dependencies'][0]['reference']['artifact'])
            self.assertEqual(state._metadata(db,current['name'])['created_at'],age)

    def test_recovery_inspector_protects_missing_previous_journal_and_artifact(self):
        from runtime import evidence
        request,contexts=self.d4_bookkeeping_request();context=contexts[0]
        self.assertEqual(self.run_with_d4_host(request,'apply')['exit_code'],0)
        baseline,sources=self.changes._recovery_sources(context)
        previous=sources[0][1]['previous']; self.assertIsNotNone(previous)
        path=context.directory/'evidence'/previous['name']; saved=path.with_suffix('.saved'); path.rename(saved)
        self.assertFalse(self.changes.inspect_lifecycle_recovery(context.authority_set,())['complete'])
        saved.rename(path)
        self.assertTrue(self.changes.inspect_lifecycle_recovery(context.authority_set,())['complete'])
        journal=self.directory/'transaction.json'; journal.rename(journal.with_suffix('.saved'))
        self.assertFalse(self.changes.inspect_lifecycle_recovery(context.authority_set,())['complete'])


    def test_recovery_unknown_typed_dependency_blocks_instead_of_guessing(self):
        from runtime import evidence,state
        request=self.scoped_request(); self.assertEqual(self.changes._run_scoped(self.directory,request,'apply')['exit_code'],0)
        fixture,context=self.recovery_authority()
        with evidence._context_database(context,write=True) as db:
            row=db.execute('select scope_id,client,session_id,task_id from tasks').fetchone(); task=state._task(db,*row)
            owner=evidence._artifact_owner(fixture.scope,task)
            reference=evidence._artifact(fixture.scope,dict(kind='fixture-stop-proof'),owner=owner,db=db)
            metadata=state._metadata(db,reference['name']); metadata['dependencies'].append(dict(kind='delivery',reference=dict(arbitrary='unknown owner contract')))
            db.execute('update evidence_metadata set data=? where name=?',(evidence._raw(metadata).decode(),reference['name']))
            task['state']='CANCELLED'; task['revision']+=1
            task['retirement']=dict(proof_digest=reference['digest'],proof=reference,observation_ref='fixture',coordinator_ref='fixture',previous_revision=task['revision']-1)
            state._save(db,row[0],task)
            ref=dict(authority=state._authority_identity(state._path(fixture.scope)),scope_id=row[0],client=row[1],session_id=row[2],task_id=row[3])
        with self.assertRaisesRegex(self.changes.Pending,'Unsupported typed'):
            self.changes._observe_recovery_source(self.directory,self.changes._load_journal(self.directory),(context,),(ref,))


    def test_recovery_two_store_final_failure_releases_no_markers(self):
        from runtime import evidence
        request,contexts=self.d4_bookkeeping_request(umbrella=True)
        original=evidence._register_recovery_source; calls=[]
        def register(scope,db,source,**kwargs):
            if source.content['journal']['state']=='applied':
                calls.append(kwargs['context'])
                if len(calls)==2: raise self.changes.Pending('fixture second final authority failure')
            return original(scope,db,source,**kwargs)
        with patch.object(evidence,'_register_recovery_source',side_effect=register):
            result=self.run_with_d4_host(request,'apply')
        self.assertNotEqual(result['exit_code'],0,result)
        for context in contexts:
            self.assertTrue(all(self.changes._authority_owner(location) is not None for location in self.changes._authority_locations([context.directory])))
        self.assertFalse(self.changes.inspect_lifecycle_recovery(contexts[0].authority_set,())['complete'])
        shared=next(a for a in request['manifest']['actions'] if a['owner']['kind']=='project')
        target=Path(shared['owner']['root'])/shared['effects'][0]['path']; raw=target.read_bytes()
        target.write_bytes(b'external changed instruction')
        self.assertNotEqual(self.run_with_d4_host(request,'resume')['exit_code'],0)
        self.assertEqual(target.read_bytes(),b'external changed instruction')
        target.write_bytes(raw)
        saved=target.with_name('owned-instruction-saved'); target.rename(saved)
        target.write_bytes(raw); target.chmod(0o644)
        self.assertNotEqual(self.run_with_d4_host(request,'resume')['exit_code'],0)
        self.assertEqual(target.read_bytes(),raw)
        target.unlink(); saved.rename(target)
        result=self.run_with_d4_host(request,'resume')
        self.assertEqual(result['exit_code'],0,result)
        self.assertTrue(self.changes.inspect_lifecycle_recovery(contexts[0].authority_set,())['complete'])

    def test_recovery_one_original_source_age_across_all_stores_and_partial_replay(self):
        from runtime import evidence,state
        request,contexts=self.d4_bookkeeping_request(umbrella=True)
        original=evidence._register_recovery_source;registered=[];calls=[]
        def register(scope,db,source,**kwargs):
            calls.append(kwargs['context'])
            if len(calls)==2:raise self.changes.Pending('fixture interrupted second authority')
            with patch.object(state,'_owner_time',return_value=200):
                reference=original(scope,db,source,**kwargs)
            self.assertEqual(state._metadata(db,reference['name'])['created_at'],100)
            registered.append((kwargs['context'],reference))
            return reference
        with patch.object(state,'_owner_time',return_value=100),patch.object(evidence,'_register_recovery_source',side_effect=register):
            partial=self.run_with_d4_host(request,'apply')
        self.assertNotEqual(partial['exit_code'],0,partial);self.assertEqual(len(registered),1)
        first_context,first=registered[0]
        self.assertEqual(set(c.database for c in calls),set(c.database for c in contexts))
        def replay_register(scope,db,source,**kwargs):
            with patch.object(state,'_owner_time',return_value=400):
                reference=original(scope,db,source,**kwargs)
            self.assertEqual(state._metadata(db,reference['name'])['created_at'],100)
            return reference
        with patch.object(state,'_owner_time',return_value=300),patch.object(evidence,'_register_recovery_source',side_effect=replay_register):
            replay=self.run_with_d4_host(request,'resume')
        self.assertEqual(replay['exit_code'],0,replay)
        second_context=next(context for context in contexts if context.database!=first_context.database)
        with evidence._context_database(first_context) as db:
            self.assertEqual(state._metadata(db,first['name'])['created_at'],100)
        with evidence._context_database(second_context,write=True) as db:
            baseline=state._source_baseline(None,db,context=second_context)
            second=baseline['dependencies'][0]['reference']['artifact']
            metadata=state._metadata(db,second['name']);metadata['created_at']=101
            db.execute('update evidence_metadata set created_at=?,data=? where name=?',(101,evidence._raw(metadata).decode(),second['name']))
        with self.assertRaisesRegex(self.changes.StartupError,'age'):
            self.changes._observe_recovery_source(self.directory,self.changes._load_journal(self.directory),contexts,())


    def test_recovery_registration_and_inspection_hold_no_cross_store_sqlite_transaction(self):
        from contextlib import contextmanager
        from runtime import evidence
        request,contexts=self.d4_bookkeeping_request(umbrella=True)
        active=[]; original=evidence._context_database
        @contextmanager
        def database(context,**kwargs):
            self.assertFalse(any(path!=context.database for path in active),'Cross-store SQLite transaction nesting')
            active.append(context.database)
            try:
                with original(context,**kwargs) as db: yield db
            finally: active.pop()
        with patch.object(evidence,'_context_database',side_effect=database):
            result=self.run_with_d4_host(request,'apply')
            self.assertEqual(result['exit_code'],0,result)
            self.assertTrue(self.changes.inspect_lifecycle_recovery(contexts[0].authority_set,())['complete'])
        self.assertEqual(active,[])

    def enrolled_linked_stopped_writer(self):
        from runtime import evidence,state,startup
        initial=self.activate_request(self.scoped_request(linked=True))
        pending=self.run_with_d4_host(initial,'apply');self.assertEqual(pending['exit_code'],2,pending)
        observed=self.observe_active(initial)
        applied=self.run_with_d4_host(observed,'resume');self.assertEqual(applied['exit_code'],0,applied)
        published=json.loads((self.repo/PROJECT_FILE).read_text())
        facts=copy.deepcopy(observed['observations']['qualification'][0]['scope_observations'])
        facts['authority']['target']=self.changes._scoped_target(self.repo,str(PROJECT_FILE))
        resolved=startup.resolve_canonical_scope(startup.decode_project_record(published),**facts)
        self.assertEqual(resolved.status,'active');scope=resolved.scope
        self.assertNotEqual(scope.repository_root,self.repo)
        self.assertEqual(scope.git_common_dir,self.repo/'.git')
        projection=startup.canonical_scope_projection(startup.decode_project_record(published),
            repository_id=scope.repository_id,checkout_id=scope.checkout_id,client='codex')
        self.assertEqual(projection,scope.accepted_identity)
        owner=dict(client='codex',session_id='linked-writer',task_id='linked-writer',scope_ref='linked')
        state.start_task(scope,dict(owner,mode='editable',decision_ref='fixture:enrolled-linked-writer'))
        context,certificate=self.stop_assignment_fixture(scope,owner)
        proof=certificate['retirement']['proof']
        with evidence._context_database(context) as db:
            task=state._task(db,state._scope_id(scope),'codex','linked-writer','linked-writer')
            self.assertEqual(task['accepted_identity'],evidence._canonical_plain(projection))
        source=self.source.read_bytes()
        temporary=tempfile.TemporaryDirectory(prefix='vcw-linked-target-');self.addCleanup(temporary.cleanup)
        self.directory=Path(temporary.name).resolve()/'plan';self.directory.mkdir(mode=0o700)
        self.source=self.directory/'catalog.md';self.source.write_bytes(source);self.source.chmod(0o600)
        self.record['toolkit']['files'][0]['local_path']=str(self.source)
        onboarding.inspect_project(self.repo,self.directory,self.project_record())
        onboarding.record_plan(self.directory,self.scoped_proposal())
        request=self.scoped_request(prepared=True)
        record=request['manifest']['enrollment']['record'];repository=record['repositories'][0]
        target=next(c for c in repository['checkouts'] if c['root']==str(self.repo))
        target['clients'][0]['configuration']['runtime']=dict(schema_version=3,state_dir=str(context.directory))
        request['approval']['manifest_digest']=onboarding._digest(request['manifest'])
        affected=request['manifest']['enrollment']['affected']
        self.assertEqual([row['checkout_id'] for row in affected],[target['checkout_id']])
        self.assertNotEqual(target['checkout_id'],scope.checkout_id)
        current=startup.canonical_scope_projection(startup.decode_project_record(record),
            repository_id=scope.repository_id,checkout_id=scope.checkout_id,client='codex')
        self.assertEqual(current,scope.accepted_identity)
        return request,scope,context,task,proof


    def historical_shared_consumer(self, *, changed=True, mode_change=False):
        from runtime import startup,state
        storage=tempfile.TemporaryDirectory(prefix='vcw-historical-private-');self.addCleanup(storage.cleanup)
        first,contexts=self.d4_bookkeeping_request(umbrella=True,state_root=Path(storage.name).resolve())
        record=first['manifest']['enrollment']['record']
        action=next(a for a in first['manifest']['actions'] if a['owner']['kind']=='project')
        shared=Path(action['owner']['root'])/action['effects'][0]['path']
        record['shared_policy']=[dict(path=str(shared),sha256=action['effects'][0]['after']['sha256'])]
        first=self.activate_request(first)
        activation=copy.deepcopy(record['repositories'][0]['checkouts'][0]['clients'][0]['activation'])
        for repository,context in zip(record['repositories'],contexts):
            checkout=repository['checkouts'][0]; entry=checkout['clients'][0]
            entry['configuration']['runtime']=dict(schema_version=3,state_dir=str(context.directory))
            entry['configuration']['shared_dependencies']=[str(shared)]
            identity=startup.canonical_scope_projection(startup.decode_project_record(record),repository_id=repository['repository_id'],checkout_id=checkout['checkout_id'],client='codex')
            digest=onboarding._digest(startup._canonical_plain(identity))
            entry['activation']=copy.deepcopy(activation); entry['activation']['accepted_scope_digest']=digest
            entry['activation']['qualification_binding']['accepted_scope_digest']=digest
        first['approval']['manifest_digest']=onboarding._digest(first['manifest'])
        pending=self.run_with_d4_host(first,'apply')
        self.assertEqual(pending['exit_code'],2,pending)
        self.assertFalse(pending['qualified'])
        self.assertIn('Fresh scoped adoption qualification',str(pending))
        observed=self.observe_active(first)
        for row in observed['observations']['qualification']:
            row['scope_observations']['observation']['dependencies']=[dict(path=str(shared),status='read',target=self.changes._scoped_target(self.base,'AGENTS.md'))]
        applied=self.run_with_d4_host(observed,'resume'); self.assertEqual(applied['exit_code'],0,applied)
        self.assertTrue(applied['qualified'],applied)
        published=json.loads((self.base/PROJECT_FILE).read_text()); consumer=published['repositories'][1]
        facts=copy.deepcopy(observed['observations']['qualification'][1]['scope_observations'])
        facts['authority']=dict(path=str(self.base/PROJECT_FILE),target=self.changes._scoped_target(self.base,str(PROJECT_FILE)))
        resolved=startup.resolve_canonical_scope(startup.decode_project_record(published),**facts)
        self.assertEqual(resolved.status,'active'); scope=resolved.scope
        task=state.start_task(scope,dict(client='codex',session_id='historical-consumer',task_id='historical-consumer',scope_ref='fixture:shared',mode='editable',decision_ref='fixture'))['task']
        self.assertEqual(task['accepted_identity']['shared_policy'],published['shared_policy'])
        source=self.source.read_bytes()
        def request(label):
            temporary=tempfile.TemporaryDirectory(prefix='vcw-historical-'+label+'-');self.addCleanup(temporary.cleanup)
            self.directory=Path(temporary.name).resolve()/'plan';self.directory.mkdir(mode=0o700)
            self.source=self.directory/'catalog.md';self.source.write_bytes(source);self.source.chmod(0o600)
            self.record['toolkit']['files'][0]['local_path']=str(self.source)
            onboarding.inspect_project(self.base,self.directory,self.project_record(self.base,[self.repo],'umbrella',['AGENTS.md']))
            proposal=self.scoped_proposal(); action=proposal['assessments']['project']['actions'][0]
            action['text']='product rules\nchanged shared process\n' if changed else shared.read_text()
            action['after']=hashlib.sha256(action['text'].encode()).hexdigest()
            self.assertEqual(onboarding.record_plan(self.directory,proposal)['status'],'ready_for_review')
            pending=self.scoped_request(prepared=True)
            effect=next(a for a in pending['manifest']['actions'] if a['owner']['kind']=='project')['effects'][0]
            pending['manifest']['enrollment']['record']['shared_policy']=[dict(path=str(shared),sha256=effect['after']['sha256'])]
            pending['approval']['manifest_digest']=onboarding._digest(pending['manifest'])
            return pending
        if changed:
            control=request('current'); before=shared.read_bytes()
            blocked=self.changes._run_scoped(self.directory,control,'apply')
            self.assertNotEqual(blocked['exit_code'],0,blocked)
            self.assertIn('affected set',str(blocked)); self.assertEqual(shared.read_bytes(),before)
        # A fresh inspection must retain an externally edited current record without repinning the old task.
        current=copy.deepcopy(published); entry=current['repositories'][1]['checkouts'][0]['clients'][0]
        entry['configuration']['shared_dependencies']=[];entry['activation']['status']='suspended';current['revision']+=1
        startup.decode_project_record(current)
        (self.base/PROJECT_FILE).write_text(json.dumps(current,sort_keys=True)+'\n')
        if mode_change: shared.chmod(0o600)
        pending=request('historical'); self.changes._validate_scoped(pending,self.plan())
        affected={tuple(row[k] for k in ('repository_id','checkout_id','client')) for row in pending['manifest']['enrollment']['affected']}
        self.assertNotIn((consumer['repository_id'],consumer['checkouts'][0]['checkout_id'],'codex'),affected)
        return pending,scope,contexts[1],task,shared

    def test_historical_shared_consumer_blocks_actual_effect_after_current_dependency_removed(self):
        from runtime import evidence,state
        request,scope,context,task,shared=self.historical_shared_consumer()
        before=shared.read_bytes()
        result=self.run_with_d4_host(request,'apply')
        self.assertNotEqual(result['exit_code'],0,result)
        self.assertIn('stopped-writer',str(result));self.assertEqual(shared.read_bytes(),before)
        with evidence._context_database(context) as db:
            self.assertEqual(state._task(db,state._scope_id(scope),'codex','historical-consumer'),task)


    def test_historical_shared_consumer_unchanged_effect_is_not_new_dependency(self):
        from runtime import evidence,state
        request,scope,context,task,shared=self.historical_shared_consumer(changed=False)
        before=shared.read_bytes()
        result=self.run_with_d4_host(request,'apply');self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(shared.read_bytes(),before)
        _,sources=self.changes._recovery_sources(context)
        source=next(content for _,content in sources if content['identity']['plan_dir']==str(self.directory))
        self.assertFalse(any(item['reference']['task_id']=='historical-consumer' for item in source['tasks']))
        with evidence._context_database(context) as db:
            self.assertEqual(state._task(db,state._scope_id(scope),'codex','historical-consumer'),task)

    def test_historical_shared_consumer_blocks_mode_only_effect(self):
        request,scope,context,task,shared=self.historical_shared_consumer(changed=False,mode_change=True)
        effect=next(a for a in request['manifest']['actions'] if a['owner']['kind']=='project')['effects'][0]
        self.assertEqual(effect['before']['sha256'],effect['after']['sha256'])
        self.assertNotEqual(effect['before']['mode'],effect['after']['mode'])
        result=self.run_with_d4_host(request,'apply')
        self.assertNotEqual(result['exit_code'],0,result);self.assertIn('stopped-writer',str(result))
        self.assertEqual(self.changes._fingerprint(shared.parent,shared.name),effect['before'])

    def command_scoped_request(self, before=None):
        import sys
        cwd=self.repo/"app"; cwd.mkdir()
        (cwd/"source.txt").write_text("source")
        if before is not None: (cwd/"result.txt").write_bytes(before)
        self.scoped_inspect()
        proposal=self.scoped_proposal(); assessment=proposal["assessments"]["repositories"][0]
        original=assessment["actions"][0]
        original.update(kind="command",target="fixture-command",section=None,before=None,after=None,cwd=str(cwd),
            text=[sys.executable,"-c","from pathlib import Path; Path('result.txt').write_text(str(Path.cwd()))"])
        authority=copy.deepcopy(assessment["evidence"][0]); authority.update(id="authority",kind="authorization"); assessment["evidence"].append(authority)
        onboarding.record_plan(self.directory,proposal)
        request=self.scoped_request(prepared=True)
        m=request["manifest"]; action=m["actions"][0]; ref=dict(reference="fixture:qualified-command",sha256="a"*64)
        action["effects"]=[dict(path="app/result.txt",before=self.changes._fingerprint(self.repo,"app/result.txt"),after=dict(sha256=hashlib.sha256(str(cwd).encode()).hexdigest(),mode=0o644),target=self.changes._scoped_target(self.repo,"app/result.txt"))]
        action["execution"]=dict(argv=original["text"],cwd=str(cwd),cwd_target=self.plan()["assessments"]["repositories"][0]["identity"]["targets"]["cwd:"+str(cwd)],timeout_seconds=5,qualification=ref,idempotency=None)
        request["approval"]["manifest_digest"]=onboarding._digest(m)
        return request,cwd

    def test_scoped_command_launch_rechecks_prepared_effects(self):
        cases=[(boundary,mutation) for boundary in ("prepared","command_started") for mutation in ("bytes","inode","parent")]
        for index,(boundary,mutation) in enumerate(cases):
            with self.subTest(boundary=boundary,mutation=mutation):
                if index:
                    self.tearDown(); self.setUp()
                request,cwd=self.command_scoped_request(before=b"approved original")
                def edit(stage):
                    if stage != boundary: return
                    if mutation=="bytes": (cwd/"result.txt").write_bytes(b"external edit")
                    elif mutation=="inode":
                        replacement=cwd/"external"; replacement.write_bytes(b"approved original"); replacement.replace(cwd/"result.txt")
                    else:
                        cwd.rename(cwd.with_name("old-app")); cwd.mkdir(); (cwd/"result.txt").write_bytes(b"approved original")
                with patch.object(self.changes,"_checkpoint",side_effect=edit):
                    result=self.changes._run_scoped(self.directory,request,"apply")
                self.assertNotEqual(result["exit_code"],0,result)
                self.assertEqual((cwd/"result.txt").read_bytes(),b"external edit" if mutation=="bytes" else b"approved original")

    def test_scoped_command_relaunch_rechecks_original_physical_effect(self):
        request,cwd=self.command_scoped_request(before=b"approved original")
        request["manifest"]["actions"][0]["execution"]["idempotency"]=dict(reference="fixture:idempotent",sha256="f"*64)
        request["approval"]["manifest_digest"]=onboarding._digest(request["manifest"])
        def crash(stage):
            if stage=="command_started": raise RuntimeError("interruption")
        with patch.object(self.changes,"_checkpoint",side_effect=crash),self.assertRaises(RuntimeError):
            self.changes._run_scoped(self.directory,request,"apply")
        journal=self.changes._load_journal(self.directory); identity=next(iter(journal["receipts"])); command=journal["receipts"][identity]["command"]
        replacement=cwd/"external"; replacement.write_bytes(b"approved original"); replacement.replace(cwd/"result.txt")
        request["observations"]=dict(manifest_digest=onboarding._digest(request["manifest"]),input_digest=self.changes._scoped_input_digest(request["manifest"]),qualification=[],task_reconciliation=[],prior_compatibility=[],
            command_reconciliation=[dict(id=identity,attempt_id=command["attempt_id"],stopped=True,evidence=[dict(reference="fixture:all-descendants",sha256="e"*64)])])
        result=self.changes._run_scoped(self.directory,request,"resume")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertEqual((cwd/"result.txt").read_bytes(),b"approved original")

    def test_scoped_prepared_only_rollback_preserves_original_and_replays(self):
        request=self.scoped_request(); target=self.repo/"AGENTS.md"
        original=target.read_bytes(); inode=target.stat().st_ino
        def crash(stage):
            if stage=="prepared": raise RuntimeError("interruption")
        with patch.object(self.changes,"_checkpoint",side_effect=crash),self.assertRaises(RuntimeError):
            self.changes._run_scoped(self.directory,request,"apply")
        for _ in range(2):
            result=self.changes._run_scoped(self.directory,request,"rollback")
            self.assertEqual(result["exit_code"],0,result)
            self.assertEqual(target.read_bytes(),original)
            self.assertEqual(target.stat().st_ino,inode)

    def test_scoped_prepared_only_rollback_rejects_same_byte_substitution(self):
        request=self.scoped_request(); target=self.repo/"AGENTS.md"
        original=target.read_bytes()
        def crash(stage):
            if stage=="prepared": raise RuntimeError("interruption")
        with patch.object(self.changes,"_checkpoint",side_effect=crash),self.assertRaises(RuntimeError):
            self.changes._run_scoped(self.directory,request,"apply")
        replacement=target.with_name("external"); replacement.write_bytes(original); replacement.replace(target)
        inode=target.stat().st_ino
        result=self.changes._run_scoped(self.directory,request,"rollback")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertEqual(target.stat().st_ino,inode)

    def test_scoped_command_uses_actual_cwd_and_requires_descendant_reconciliation(self):
        request,cwd=self.command_scoped_request()
        result=self.changes._run_scoped(self.directory,request,"apply")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertEqual((cwd/"result.txt").read_text(),str(cwd))
        self.assertFalse((self.repo/"result.txt").exists())
        journal=self.changes._load_journal(self.directory); identity=next(iter(journal["receipts"])); command=journal["receipts"][identity]["command"]
        request["observations"]=dict(manifest_digest=onboarding._digest(request["manifest"]),input_digest=self.changes._scoped_input_digest(request["manifest"]),qualification=[],task_reconciliation=[],prior_compatibility=[],
            command_reconciliation=[dict(id=identity,attempt_id=command["attempt_id"],stopped=True,evidence=[dict(reference="fixture:all-descendants",sha256="e"*64)])])
        result=self.changes._run_scoped(self.directory,request,"resume")
        self.assertEqual(result["exit_code"],0,result)
        self.assertIsNone(self.changes._command_owner(request["manifest"]["actions"][0]["owner"]["git_dir"]))

    def test_scoped_command_reviewed_target_parent_substitution_rejects_before_journal(self):
        request,cwd=self.command_scoped_request()
        cwd.rename(cwd.with_name("old-app")); cwd.mkdir()
        result=self.changes._run_scoped(self.directory,request,"apply")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertFalse((self.directory/"transaction.json").exists())
        self.assertFalse((cwd/"result.txt").exists())

    def test_scoped_command_same_byte_target_substitution_rejects_before_journal(self):
        request,cwd=self.command_scoped_request(before=b"original")
        replacement=cwd/"replacement"; replacement.write_bytes(b"original"); replacement.replace(cwd/"result.txt")
        result=self.changes._run_scoped(self.directory,request,"apply")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertFalse((self.directory/"transaction.json").exists())
        self.assertEqual((cwd/"result.txt").read_bytes(),b"original")

    def test_scoped_linked_primary_legacy_lock_blocks_without_primary_edit_permission(self):
        request=self.scoped_request(linked=True)
        original=(self.repo/"AGENTS.md").read_bytes()
        common=request["manifest"]["owners"][0]["owner"]["git_common_dir"]
        with self.changes._worktree_lock(common):
            result=self.changes._run_scoped(self.directory,request,"apply")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertFalse((self.directory/"transaction.json").exists())
        result=self.changes._run_scoped(self.directory,request,"apply")
        self.assertEqual(result["exit_code"],0,result)
        self.assertEqual((self.repo/"AGENTS.md").read_bytes(),original)
        selected=Path(request["manifest"]["owners"][0]["owner"]["root"])
        self.assertNotEqual((selected/"AGENTS.md").read_bytes(),original)

    def test_scoped_configured_prior_raw_bytes_and_revision_restore_exactly(self):
        location,prior=self.canonical_authority()
        raw=(json.dumps(prior,indent=4)+"\n\n").encode(); location.write_bytes(raw)
        onboarding.inspect_project(self.repo,self.directory,self.project_record())
        request=self.scoped_request(existing=True)
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        result=self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["exit_code"],0,result)
        self.assertEqual(location.read_bytes(),raw)
        self.assertEqual(json.loads(location.read_bytes())["revision"],prior["revision"])
        self.assertEqual(self.changes._run_scoped(self.directory,request,"rollback")["exit_code"],0)

    def test_scoped_delete_rename_crash_requires_durable_delete_intent(self):
        relative,raw,before,after,receipt,journal=self.physical_write_fixture()
        def crash(stage):
            if stage=="rename": raise RuntimeError("delete crash")
        with patch.object(self.changes,"_checkpoint",side_effect=crash),self.assertRaises(RuntimeError):
            self.changes._scoped_replace(self.directory,self.repo,relative,b"",before,None,receipt,journal)
        self.assertFalse((self.repo/relative).exists())
        self.changes._scoped_replace(self.directory,self.repo,relative,b"",before,None,receipt,journal)
        self.assertTrue(receipt["delete_intent"])

    def test_scoped_forged_recovery_filename_rejects_before_read_or_write(self):
        request=self.scoped_request()
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        journal=self.changes._load_journal(self.directory)
        next(iter(journal["receipts"].values()))["files"][0]["backup"]="../outside"
        (self.directory/"transaction.json").write_text(json.dumps(journal))
        before=(self.repo/"AGENTS.md").read_bytes()
        result=self.changes._run_scoped(self.directory,request,"rollback")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertEqual((self.repo/"AGENTS.md").read_bytes(),before)

    def test_scoped_canonical_parent_substitution_with_owned_file_blocks(self):
        request=self.scoped_request()
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        directory=self.repo/PROJECT_FILE.parent
        directory.rename(directory.with_name("old-enrollment")); directory.mkdir()
        (directory.with_name("old-enrollment")/"project.json").rename(directory/"project.json")
        before=(directory/"project.json").read_bytes()
        result=self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["state"],"rollback_partial")
        self.assertEqual((directory/"project.json").read_bytes(),before)

    def test_scoped_interrupted_canonical_owner_blocks_another_plan_without_state_db(self):
        request=self.scoped_request()
        def crash(stage):
            if stage=="prepared": raise RuntimeError("interruption")
        with patch.object(self.changes,"_checkpoint",side_effect=crash),self.assertRaises(RuntimeError):
            self.changes._run_scoped(self.directory,request,"apply")
        first=self.directory
        self.directory=self.base/"different-plan"; self.directory.mkdir(mode=0o700)
        self.source=self.directory/"catalog.md"; self.source.write_bytes((first/"catalog.md").read_bytes()); self.source.chmod(0o600)
        self.record["toolkit"]["files"][0]["local_path"]=str(self.source)
        other=self.scoped_request()
        result=self.changes._run_scoped(self.directory,other,"apply")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertFalse((self.directory/"transaction.json").exists())
        self.assertEqual((self.repo/"AGENTS.md").read_text(),"product rules\nlegacy process\n")

    def test_scoped_journal_rejects_substituted_canonical_postimage_metadata(self):
        request=self.scoped_request()
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        journal=self.changes._load_journal(self.directory)
        journal["enrollment"]["postimages"][0]["raw_sha256"]="0"*64
        (self.directory/"transaction.json").write_text(json.dumps(journal))
        with self.assertRaises(self.changes.StartupError): self.changes._load_journal(self.directory)

    def test_scoped_prepared_temp_substitution_is_preserved_without_target_write(self):
        relative,raw,before,after,receipt,journal=self.physical_write_fixture()
        temporary=self.repo/receipt["temporary"]
        def substitute(stage):
            if stage=="scoped_temp_recorded":
                replacement=self.repo/"user-temp"
                replacement.write_text("external temporary")
                replacement.replace(temporary)
        with patch.object(self.changes,"_checkpoint",side_effect=substitute),self.assertRaises(self.changes.Pending):
            self.changes._scoped_replace(self.directory,self.repo,relative,raw,before,after,receipt,journal)
        self.assertEqual(self.changes._fingerprint(self.repo,relative),before)
        self.assertEqual(temporary.read_text(),"external temporary")

    def test_scoped_rejected_existing_approval_preserves_journal_bytes(self):
        request=self.scoped_request()
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        before=(self.directory/"transaction.json").read_bytes()
        rejected=copy.deepcopy(request)
        rejected["approval"]["evidence"].append(dict(reference="different-session",sha256="f"*64))
        result=self.changes._run_scoped(self.directory,rejected,"resume")
        self.assertNotEqual(result["exit_code"],0)
        self.assertEqual((self.directory/"transaction.json").read_bytes(),before)

    def test_scoped_failure_journal_save_remains_under_transaction_locks(self):
        import threading
        request=self.scoped_request()
        entered=threading.Event(); release=threading.Event(); first_result=[]
        original_save=self.changes._save; original_publish=self.changes._scoped_publish
        def publish(*args,**kwargs):
            if threading.current_thread().name=="first-scoped-caller": raise self.changes.Pending("fixture operational failure")
            return original_publish(*args,**kwargs)
        def save(path,journal):
            if threading.current_thread().name=="first-scoped-caller" and journal["state"]=="blocked" and not entered.is_set():
                entered.set()
                if not release.wait(5): raise AssertionError("fixture release missing")
            return original_save(path,journal)
        with patch.object(self.changes,"_save",side_effect=save),patch.object(self.changes,"_scoped_publish",side_effect=publish):
            first=threading.Thread(name="first-scoped-caller",target=lambda:first_result.append(self.changes._run_scoped(self.directory,request,"apply")))
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second=self.changes._run_scoped(self.directory,request,"resume")
            finally:
                release.set(); first.join(5)
            self.assertFalse(first.is_alive())
        self.assertNotEqual(second["exit_code"],0,second)
        self.assertNotEqual(first_result[0]["exit_code"],0)
        self.assertEqual(self.changes._run_scoped(self.directory,request,"resume")["exit_code"],0)

    def test_scoped_command_stopped_boolean_without_retained_proof_cannot_complete(self):
        request,cwd=self.command_scoped_request()
        self.assertNotEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        journal=self.changes._load_journal(self.directory)
        next(iter(journal["receipts"].values()))["command"]["stopped"]=True
        (self.directory/"transaction.json").write_text(json.dumps(journal))
        result=self.changes._run_scoped(self.directory,request,"resume")
        self.assertNotEqual(result["exit_code"],0,result)
        self.assertFalse((self.repo/PROJECT_FILE).exists())

    def test_scoped_newer_inactive_record_with_changed_binding_cannot_authorize_recovery(self):
        request=self.scoped_request()
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        current=json.loads((self.repo/PROJECT_FILE).read_text())
        current["revision"]+=1
        current["project_binding"]["inode"]+=1
        current["repositories"][0]["binding"]["primary_root"]["inode"]+=1
        current["repositories"][0]["checkouts"][0]["binding"]["root"]["inode"]+=1
        (self.repo/PROJECT_FILE).write_text(json.dumps(current))
        before=(self.repo/"AGENTS.md").read_bytes()
        result=self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["state"],"rollback_partial")
        self.assertEqual((self.repo/"AGENTS.md").read_bytes(),before)

    def test_scoped_later_join_shared_dependency_blocks_only_its_required_recovery(self):
        request=self.scoped_request(umbrella=True)
        self.assertEqual(self.changes._run_scoped(self.directory,request,"apply")["exit_code"],0)
        location=self.base/PROJECT_FILE; current=json.loads(location.read_text())
        third=self.base/"third"; self.git("clone","-q","--no-hardlinks",str(self.repo),str(third))
        identity,error=onboarding._checkout_observation(str(third),{"paths":0,"bytes":0}); self.assertIsNone(error)
        path=str(self.repo/"AGENTS.md"); current["shared_policy"].append(dict(path=path,sha256=hashlib.sha256((self.repo/"AGENTS.md").read_bytes()).hexdigest()))
        client=copy.deepcopy(current["repositories"][0]["checkouts"][0]["clients"][0]); client["configuration"]["shared_dependencies"]=[path]
        current["repositories"].append(dict(repository_id="new-dependent",primary_root=identity["primary_root"],git_common_dir=identity["git_common_dir"],binding={k:identity["binding"][k] for k in ("primary_root","git_common_dir")},membership="joined",checkouts=[dict(checkout_id="new-dependent-checkout",root=identity["root"],git_dir=identity["git_dir"],binding={k:identity["binding"][k] for k in ("root","git_dir")},clients=[client])]))
        current["revision"]+=1; location.write_text(json.dumps(current)); raw=location.read_bytes()
        first=(self.repo/"AGENTS.md").read_bytes(); second=Path(current["repositories"][1]["primary_root"])/"AGENTS.md"
        result=self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["state"],"rollback_partial",result)
        self.assertEqual(location.read_bytes(),raw)
        self.assertEqual((self.repo/"AGENTS.md").read_bytes(),first)
        self.assertFalse(second.exists())
        second_id=current["repositories"][1]["repository_id"]
        outcome=next(m for m in result["members"] if m["repository_id"]==second_id)
        self.assertEqual(outcome["status"],"restored")
        self.assertIsNotNone(outcome["blocked"])

    def test_scoped_active_supporting_rollback_suspends_before_restoring_files(self):
        request=self.activate_request(self.scoped_request())
        self.changes._run_scoped(self.directory,request,"apply")
        request=self.observe_active(request)
        self.assertTrue(self.changes._run_scoped(self.directory,request,"resume")["qualified"])
        request=self.observe_active(request)
        seen=[]
        def observe(stage):
            if stage=="rename" and (self.repo/"AGENTS.md").read_text()=="product rules\nlegacy process\n" and (self.repo/PROJECT_FILE).exists():
                seen.append(json.loads((self.repo/PROJECT_FILE).read_text())["repositories"][0]["checkouts"][0]["clients"][0]["activation"]["status"])
        with patch.object(self.changes,"_checkpoint",side_effect=observe):
            result=self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual(result["exit_code"],0,result)
        self.assertTrue(seen)
        self.assertNotIn("active",seen)

    def test_scoped_rollback_suspension_rename_crash_resumes_exactly_once(self):
        request=self.activate_request(self.scoped_request())
        self.changes._run_scoped(self.directory,request,"apply")
        request=self.observe_active(request)
        self.assertTrue(self.changes._run_scoped(self.directory,request,"resume")["qualified"])
        request.pop("observations")
        def crash(stage):
            if stage=="rename" and (self.repo/PROJECT_FILE).exists() and json.loads((self.repo/PROJECT_FILE).read_text())["repositories"][0]["checkouts"][0]["clients"][0]["activation"]["status"]=="suspended":
                raise RuntimeError("rollback suspension crash")
        with patch.object(self.changes,"_checkpoint",side_effect=crash),self.assertRaises(RuntimeError):
            self.changes._run_scoped(self.directory,request,"rollback")
        self.assertEqual((self.repo/"AGENTS.md").read_text(),"product rules\nnew process\n")
        self.assertEqual(self.changes._run_scoped(self.directory,request,"rollback")["exit_code"],0)
        journal=self.changes._load_journal(self.directory)
        self.assertEqual(sum(p["phase"]=="rollback_suspended" for p in journal["enrollment"]["postimages"]),1)
        self.assertEqual(self.changes._run_scoped(self.directory,request,"rollback")["exit_code"],0)

    def physical_write_fixture(self):
        self.changes._load_journal(self.directory)
        relative = "README.md"
        before = self.changes._fingerprint(self.repo, relative)
        raw = b"scoped replacement\n"
        after = dict(sha256=hashlib.sha256(raw).hexdigest(), mode=0o644)
        receipt = dict(temporary=".d4-scoped-test", prepared_binding=None, result_binding=None)
        journal = dict(schema_version=2, receipts={"action": receipt})
        return relative, raw, before, after, receipt, journal

    def test_scoped_replacement_rename_crash_recovers_only_prepared_inode(self):
        relative, raw, before, after, receipt, journal = self.physical_write_fixture()
        def crash(stage):
            if stage == "rename": raise RuntimeError("fixture crash")
        with patch.object(self.changes, "_checkpoint", side_effect=crash), self.assertRaises(RuntimeError):
            self.changes._scoped_replace(self.directory, self.repo, relative, raw, before, after, receipt, journal)
        self.assertIsNotNone(receipt["prepared_binding"])
        self.assertIsNone(receipt["result_binding"])
        self.changes._scoped_replace(self.directory, self.repo, relative, raw, before, after, receipt, journal)
        self.assertEqual(receipt["result_binding"], receipt["prepared_binding"])

    def test_scoped_replacement_same_bytes_new_inode_does_not_acquire_ownership(self):
        relative, raw, before, after, receipt, journal = self.physical_write_fixture()
        def crash(stage):
            if stage == "rename": raise RuntimeError("fixture crash")
        with patch.object(self.changes, "_checkpoint", side_effect=crash), self.assertRaises(RuntimeError):
            self.changes._scoped_replace(self.directory, self.repo, relative, raw, before, after, receipt, journal)
        replacement = self.repo / "replacement"
        replacement.write_bytes(raw)
        replacement.chmod(0o644)
        replacement.replace(self.repo / relative)
        with self.assertRaisesRegex(self.changes.Pending, "ownership|identity"):
            self.changes._scoped_replace(self.directory, self.repo, relative, raw, before, after, receipt, journal)
        self.assertEqual((self.repo / relative).read_bytes(), raw)

    def test_scoped_replacement_prepared_temp_replays_without_content_inference(self):
        relative, raw, before, after, receipt, journal = self.physical_write_fixture()
        def crash(stage):
            if stage == "scoped_temp_recorded": raise RuntimeError("fixture crash")
        with patch.object(self.changes, "_checkpoint", side_effect=crash), self.assertRaises(RuntimeError):
            self.changes._scoped_replace(self.directory, self.repo, relative, raw, before, after, receipt, journal)
        self.assertEqual(self.changes._fingerprint(self.repo, relative), before)
        self.changes._scoped_replace(self.directory, self.repo, relative, raw, before, after, receipt, journal)
        self.assertEqual(self.changes._fingerprint(self.repo, relative), after)


# Inherited D3 tests are already covered by test_onboarding.py.
for _name in list(vars(d3_tests.OnboardingTests)):
    if _name.startswith("test_") and _name not in vars(ChangesTests):
        setattr(ChangesTests, _name, None)

for _name in list(vars(d3_tests.OnboardingTests)):
    if _name.startswith("test_") and _name not in vars(ScopedChangesTests):
        setattr(ScopedChangesTests, _name, None)
