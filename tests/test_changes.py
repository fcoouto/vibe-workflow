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
        from tests.test_delegation_review import Stage4NativeReviewTests
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
        # Gates replace fixed delays: the command writes its result only when released, and the
        # descendant's parent stays until the helper dies so the helper cannot observe completion.
        release, child_pid, parent_pid = (self.base / name for name in ("release-command", "child.pid", "parent.pid"))
        self.addCleanup(release.touch)
        child = ("import os,time; from pathlib import Path; Path(%r).write_text(str(os.getpid())); "
                 "Path('started.txt').write_text('started'); until=time.monotonic()+60\n"
                 "while not Path(%r).exists() and time.monotonic()<until: time.sleep(0.01)\n"
                 "Path('result.txt').write_text('result')" % (str(child_pid), str(release)))
        program = ("import os,subprocess,sys,time; from pathlib import Path; Path(%r).write_text(str(os.getpid())); "
                   "helper=os.getppid(); subprocess.Popen([sys.executable,'-c',%r],close_fds=True); until=time.monotonic()+60\n"
                   "while os.getppid()==helper and time.monotonic()<until: time.sleep(0.01)"
                   % (str(parent_pid), child)) if descendant_closes_lock else child

        def wait_for_exit(path):
            pid = int(path.read_text())
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return
                time.sleep(0.01)
            self.fail("fixture process did not exit")
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
            deadline = time.monotonic() + 60
            while not (self.repo / "started.txt").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.repo / "started.txt").exists())
            helper.kill()
            helper.wait(timeout=10)
            if descendant_closes_lock:
                wait_for_exit(parent_pid)
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
            release.touch()
            wait_for_exit(child_pid)
            self.assertEqual((self.repo / "result.txt").read_text(), "result")
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


# Inherited D3 tests are already covered by test_onboarding.py.
for _name in list(vars(d3_tests.OnboardingTests)):
    if _name.startswith("test_") and _name not in vars(ChangesTests):
        setattr(ChangesTests, _name, None)
