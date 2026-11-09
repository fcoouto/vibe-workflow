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


class ChangesFixture:
    """Shared D4 change fixture mixin; test classes also inherit the disposable D3 setup."""
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

    def nested_request(self):
        action = dict(self.plan()["actions"][0], target="docs/design/architecture.md", before="absent")
        self.replan([action])
        return self.request()
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


def disable_inherited_d3_tests(cls):
    # Inherited D3 tests are already covered by test_onboarding.py.
    for _name in list(vars(d3_tests.OnboardingTests)):
        if _name.startswith("test_") and _name not in vars(cls):
            setattr(cls, _name, None)
