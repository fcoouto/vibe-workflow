"""Disposable private state; no model, client, enrollment or settings changes."""

from contextlib import closing
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime.engine import RuntimeEvent
from runtime.startup import ProjectScope, RuntimeSettings


PACKAGE = Path(__file__).resolve().parents[1]


class StateTests(unittest.TestCase):
    def setUp(self):
        self.fresh_scope()
        self.request = dict(client="codex", session_id="session", task_id="task", scope_ref="request:1",
                            mode="editable", decision_ref="user:1")

    def fresh_scope(self):
        from tests.fixture_canonical_birth import fresh_canonical, save_test_acquisition
        fixture, self.acquisition, observation = fresh_canonical(self)
        self.base, self.scope = fixture.base, self.acquisition.resolution.scope
        self.test_acquisition = save_test_acquisition(self.base / 'test-acquisition.json', fixture, observation)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime.state"), "durable state implementation missing")
        return importlib.import_module("runtime.state")

    def start(self, **updates):
        return self.api().start_task(self.scope, {**self.request, **updates})

    def status(self, **updates):
        return self.api().task_status(self.scope, updates.get("client", "codex"), updates.get("session", "session"))

    def update(self, **updates):
        current = self.status()["task"]
        return self.api().update_task(self.scope, {key: self.request[key] for key in
                                      ("client", "session_id", "task_id", "scope_ref")} |
                                     dict(expected_revision=current["revision"], state=current["state"],
                                          reason="work", reference="user:1") | updates)

    def event(self, kind="PreToolUse", **updates):
        event = RuntimeEvent(kind, "codex", "session", self.scope.repository_root, "turn", "call", "apply_patch",
                             False, "fingerprint")
        return self.api().process_event(self.scope, replace(event, **updates))

    def runnable(self, **updates):
        return dict(id="next", kind="runnable", action="task", reason="Finish authorized step",
                    reference="user:1", continuation={"turn_id": "turn", "budget": 1}) | updates

    def arm(self):
        self.start()
        self.update(state='WORKING')
        self.event()
        self.update(conditions=[self.runnable()])

    def test_historical_retirement_requires_exact_stopped_writer_proof_and_preserves_pins(self):
        import hashlib
        self.start()
        old = self.status()["task"]
        self.start(task_id="new", replaces={"task_id": "task", "expected_revision": old["revision"]})
        with self.api()._database(self.scope) as db:
            historical = self.api()._task(db, self.api()._scope_id(self.scope), "codex", "session", "task")
        identity = {key: historical[key] for key in ("client", "session_id", "task_id", "scope_ref")}
        request = identity | dict(expected_revision=historical["revision"], expected_pins=historical["pins"],
                                 reason="Old writer stopped", reference="host:reconciliation")
        with self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request | dict(stop_proof={"stopped": True}))
        proof = dict(version=1, kind="stopped-writer-reconciliation", identity=identity,
                     expected_revision=historical["revision"], expected_pins=historical["pins"],
                     observed_turn_id=historical["turn_id"], stopped=True, descendants_stopped=True,
                     attempt_ids=[], observation_ref="host:observed-processes", coordinator_ref="coordinator:checked")
        raw = json.dumps(proof).encode()
        path = self.base / "stop-proof.json"
        path.write_bytes(raw)
        path.chmod(0o400)
        request["stop_proof"] = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request | dict(expected_revision=1))
        files = {str(p):p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}
        with patch.object(self.api(), "runtime_fingerprint", return_value="new-runtime"), \
                self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request)
        self.assertEqual({str(p):p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}, files)
        with self.api()._database(self.scope) as db:
            self.assertEqual(self.api()._task(db, self.api()._scope_id(self.scope), 'codex', 'session', 'task'), historical)
        result = self.api().retire_task(self.scope, request)
        self.assertEqual(result["task"]["state"], 'CANCELLED')
        self.assertEqual(result["task"]["pins"], historical["pins"])
        self.assertEqual(self.status()["task"]["task_id"], "new")
        with self.assertRaises(self.api().StateError):
            self.api().retire_task(self.scope, request)

    def test_schema_two_assignments_have_unique_dispatch_and_no_legacy_adoption(self):
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("insert into assignments values(?,?,?,?,?,?,?)",
                       ("one", "scope", "codex", "session", "task", "dispatch", "{}"))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("insert into assignments values(?,?,?,?,?,?,?)",
                           ("two", "scope", "codex", "session", "task", "dispatch", "{}"))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("insert into assignments values(?,?,?,?,?,?,?)",
                           ("one", "other", "codex", "session", "task", "dispatch", "{}"))
            db.execute("drop table assignments")
            db.execute("pragma user_version=1")
        original = path.read_bytes()
        with self.assertRaises(self.api().StateError):
            self.status()
        with self.assertRaises(self.api().StateError):
            self.start()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(self.api().inspect_state(self.scope)["compatible"])

    def test_same_columns_without_assignment_constraints_are_rejected(self):
        self.start()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("drop table assignments")
            db.execute("create table assignments(id TEXT,scope_id TEXT,client TEXT,session_id TEXT,"
                       "task_id TEXT,dispatch_id TEXT,data TEXT)")
        with self.assertRaises(self.api().StateError):
            self.status()

    def test_start_restart_idempotence_and_pins(self):
        first = self.start()
        self.assertEqual(first["task"]["state"], 'TRIAGE')
        self.assertEqual(first["task"]["revision"], 1)
        self.assertEqual(first, self.start())
        result = subprocess.run([sys.executable, "-B", "-c",
            "from runtime.state import *; from runtime.startup import *; from pathlib import Path; import json; "
            f"from tests.fixture_canonical_birth import acquire_test_scope; s=acquire_test_scope({str(self.test_acquisition)!r}); "
            "print(json.dumps(task_status(s,'codex','session')))"], cwd=PACKAGE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["task"], first["task"])
        self.assertEqual(set(first["task"]["pins"]), {"policy_digest", "runtime_digest", "schema_version", "accepted_scope_digest"})

    def test_explicit_rebinding_preserves_old_task_and_revokes_continuation(self):
        self.arm()
        old = self.status()["task"]
        with self.assertRaises(self.api().StateError):
            self.start(task_id="other")
        new = self.start(task_id="other", replaces={"task_id": "task", "expected_revision": old["revision"]})
        self.assertEqual(new["task"]["task_id"], "other")
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db, db:
            stored = json.loads(db.execute("select data from tasks where task_id='task'").fetchone()[0])
        self.assertEqual(stored["revision"], old["revision"] + 1)
        self.assertTrue(stored["conditions"][0]["continuation"]["revoked"])
        self.assertEqual(self.event("Interrupt").action, "context")
        self.assertEqual(self.status()["task"]["state"], 'TRIAGE')

    def test_cas_transitions_terminal_cancellation_and_immutable_mode(self):
        self.start()
        for updates in ({"state": 'DONE'}, {"state": 'NOT A STATUS'}, {"expected_revision": 0}, {"mode": "read-only"}):
            with self.subTest(updates=updates), self.assertRaises(self.api().StateError):
                self.update(**updates)
        self.update(state='WORKING')
        self.update(state='REVIEW')
        self.update(state='WORKING')
        self.update(state='WAITING')
        with self.assertRaises(self.api().StateError):
            self.update(state='WORKING')
        self.update(state='WORKING', resolution_ref="user:resolved")
        self.update(state='CANCELLED')
        with self.assertRaises(self.api().StateError):
            self.update(state='WORKING')

    def test_conditions_need_explicit_resolution_and_no_approval_is_inferred(self):
        self.start()
        self.update(state='WORKING', conditions=[dict(id="decision", kind="decision", action="task",
                                                     reason="Choose", reference="agent:claims-user-approved")])
        self.assertEqual(self.event().action, "deny")
        self.update(conditions=[])
        self.assertEqual(self.event().action, "deny")
        self.update(resolutions=[{"id": "decision", "reference": "user:choice"}])
        self.assertEqual(self.event().action, "pass")

    def test_malformed_controls_are_rejected_without_initialization(self):
        for updates in ({"client": "other"}, {"task_id": "x" * 129}, {"session_id": []},
                        {"mode": "auto"}, {"decision_ref": "bad\nvalue"}, {"surprise": True}):
            with self.subTest(updates=updates), self.assertRaises(self.api().StateError):
                self.start(**updates)
        with self.api()._database(self.scope) as db:
            self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0], 0)

    def test_task_identity_includes_checkout_common_project_client_session(self):
        self.start()
        for changes in ({"repository_root": self.base / "other"}, {"git_common_dir": self.base / "common"},
                        {"project_id": "other"}):
            other = replace(self.scope, **changes)
            with self.assertRaises(self.api().StateError):
                self.api().task_status(other, "codex", "session")
        self.assertIsNone(self.status(session="different")["task"])

    def test_hooks_status_and_inspection_do_not_initialize(self):
        api = self.api()
        self.assertTrue(api.inspect_state(self.scope)["present"])
        original_inode=(self.scope.runtime.state_dir/'runtime.sqlite3').stat().st_ino
        self.assertIsNone(self.status()["task"])
        self.assertEqual(self.event().action, "pass")
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual((self.scope.runtime.state_dir/'runtime.sqlite3').stat().st_ino,original_inode)
        with api._database(self.scope) as db:self.assertEqual(db.execute('select count(*) from tasks').fetchone()[0],0)
        with self.assertRaises(api.StateError):
            api.start_task(replace(self.scope, runtime=None), self.request)
        self.assertEqual(api.process_event(replace(self.scope, runtime=None), RuntimeEvent(
            "PreToolUse", "codex", "session", self.scope.repository_root, tool_name="apply_patch")).action, "context")
        self.assertFalse(api.inspect_state(replace(self.scope,runtime=None))["compatible"])

    def test_private_directory_database_sidecars_and_corrupt_state(self):
        api = self.api()
        directory = self.scope.runtime.state_dir
        directory.chmod(0o755)
        with self.assertRaises(api.StateError):
            self.start()
        directory.chmod(0o700)
        path = directory / "runtime.sqlite3"
        path.write_text("not our database")
        path.chmod(0o600)
        with self.assertRaises(api.StateError):
            self.start()
        self.assertEqual(path.read_text(), "not our database")
        self.fresh_scope()
        directory=self.scope.runtime.state_dir
        path=directory/'runtime.sqlite3'
        self.start()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        sidecar = directory / "runtime.sqlite3-journal"
        sidecar.symlink_to(path)
        with self.assertRaises(api.StateError):
            self.status()
        sidecar.unlink()
        path.chmod(0o644)
        self.assertEqual(self.event().action, "context")
        self.assertEqual(self.event("Stop").action, "pass")

    def test_arbitrary_sqlite_schema_and_schema_version_are_not_adopted(self):
        directory = self.scope.runtime.state_dir
        path = directory / "runtime.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("create table private(value)")
        path.chmod(0o600)
        with self.assertRaises(self.api().StateError):
            self.start()
        self.fresh_scope()
        path=self.scope.runtime.state_dir/'runtime.sqlite3'
        self.start()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("pragma user_version=99")
        with self.assertRaises(self.api().StateError):
            self.status()
        self.assertFalse(self.api().inspect_state(self.scope)["compatible"])

    def test_policy_runtime_pin_mismatch_and_locked_state(self):
        self.start()
        self.update(state='WORKING')
        changed = replace(self.scope, policy_digest="changed")
        self.assertEqual(self.api().process_event(changed, RuntimeEvent("PreToolUse", "codex", "session",
                         self.scope.repository_root, tool_name="apply_patch")).action, "deny")
        with patch("runtime.state.runtime_fingerprint", return_value="changed"):
            with self.assertRaises(self.api().StateError):
                self.update()
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db, db:
            db.execute("begin exclusive")
            self.assertEqual(self.event().action, "context")
            self.assertEqual(self.event("Stop").action, "pass")

    def test_patch_guard_and_scoped_blockers(self):
        self.assertEqual(self.event().action, "pass")
        self.start(mode="read-only")
        self.update(state='WORKING')
        self.assertEqual(self.event().action, "deny")
        self.assertEqual(self.event(tool_name="Bash").action, "pass")
        self.start(session_id="editable")
        other = RuntimeEvent("PreToolUse", "codex", "editable", self.scope.repository_root, tool_name="apply_patch")
        self.api().update_task(self.scope, dict(client="codex", session_id="editable", task_id="task",
            scope_ref="request:1", expected_revision=1, state='WORKING', reason="work", reference="user:1"))
        self.assertEqual(self.api().process_event(self.scope, other).action, "pass")
        self.update(conditions=[dict(id="patch", kind="blocker", action="patch", reason="Check", reference="ref")])
        self.assertEqual(self.api().process_event(self.scope, other).action, "pass")

    def test_native_child_keeps_ordinary_parent_patch_authority(self):
        self.start()
        self.update(state='WORKING')
        child = dict(session_id='child', hook_parent_session_id='session')
        self.assertEqual(self.event(**child).action, 'pass')
        self.assertEqual(self.event('PostToolUse', **child).action, 'pass')
        self.assertIsNone(self.status(session='child')['task'])
        self.update(state='WAITING')
        self.assertEqual(self.event(**child, tool_call_id='second').action, 'deny')

    def test_receipts_deduplicate_conflicts_missing_identity_and_late_events(self):
        self.start()
        self.event("PostToolUse", tool_name="unknown")
        self.assertEqual(self.status()["generation"], 1)
        self.event("PostToolUse", tool_name="unknown")
        self.assertEqual(self.status()["generation"], 1)
        result = self.event("PostToolUse", receipt_fingerprint="conflict")
        self.assertEqual(result.action, "context")
        self.assertEqual(self.status()["generation"], 2)
        for _ in range(2):
            self.event("PostToolUse", turn_id=None)
        self.assertEqual(self.status()["generation"], 4)
        other = replace(self.scope, repository_root=self.base / "other")
        with self.assertRaises(self.api().StateError):
            self.api().task_status(other, "codex", "session")

    def test_continuation_requires_observation_and_never_rearms(self):
        self.start()
        self.update(state='WORKING')
        with self.assertRaises(self.api().StateError):
            self.update(conditions=[self.runnable()])
        self.event()
        self.update(conditions=[self.runnable()])
        self.assertEqual(self.event("Stop").action, "continue")
        self.assertEqual(self.event("Stop").action, "pass")
        self.update(conditions=[self.runnable(reason="Edited")])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.status()["task"]["state"], 'WORKING')

    def test_stop_suppression_for_uncertainty_budget_state_and_blockers(self):
        self.arm()
        self.assertEqual(self.event("Stop", stop_hook_active=None).action, "pass")
        self.assertEqual(self.event("Stop", stop_hook_active=True).action, "pass")
        self.update(conditions=[self.runnable(continuation={"turn_id": "turn", "budget": 0})])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.event("Stop", turn_id=None).action, "pass")
        self.assertEqual(self.event("Stop", turn_id="unobserved").action, "pass")
        self.event(turn_id="new", tool_call_id="new-call")
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.status()["task"]["conditions"][0]["continuation"]["revoked"], True)

    def test_interruption_pauses_only_correlated_task_and_cannot_continue(self):
        self.arm()
        self.assertEqual(self.event("Interrupt", turn_id="unknown").action, "context")
        self.assertEqual(self.status()["task"]["state"], 'WORKING')
        self.assertEqual(self.event("Interrupt").action, "pass")
        self.assertEqual(self.status()["task"]["state"], 'PAUSED')
        self.assertEqual(self.event("Stop").action, "pass")
        self.update(state='WORKING', resolution_ref="user:resume")
        self.assertEqual(self.event("Stop").action, "pass")

    def test_late_old_turns_do_not_pause_or_reassign_new_binding(self):
        self.arm()
        revision = self.status()["task"]["revision"]
        self.start(task_id="new-task", replaces=dict(task_id="task", expected_revision=revision))
        self.request["task_id"] = "new-task"
        self.update(state='WORKING')
        self.event(turn_id="new-turn", tool_call_id="new-call")
        self.update(conditions=[self.runnable(continuation={"turn_id": "new-turn", "budget": 1})])
        before = self.status()["task"]
        self.event("PostToolUse")
        self.event()
        self.assertEqual(self.event("Interrupt").action, "context")
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertEqual(self.status()["task"], before)
        self.assertEqual(self.event("Stop", turn_id="new-turn").action, "continue")
        self.assertEqual(self.status()["generation"], 1)

    def test_out_of_order_observations_keep_latest_turn_and_payloads_are_not_stored(self):
        self.arm()
        self.event(turn_id="second", tool_call_id="second-call")
        self.event()
        self.assertEqual(self.status()["task"]["turn_id"], "second")
        self.assertEqual(self.event("Stop").action, "pass")
        from runtime.adapters.codex import normalize_runtime
        for response in ({"exit_code": 0}, {"exit_code": 2, "stderr": "PRIVATE_SENTINEL"}):
            event = normalize_runtime(dict(hook_event_name="PostToolUse", session_id="session",
                cwd=str(self.scope.repository_root), turn_id="second", tool_use_id="result-" + str(response["exit_code"]),
                tool_name="Bash", tool_input={"command": "PRIVATE_SENTINEL"}, tool_response=response))
            self.api().process_event(self.scope, event)
        self.assertEqual(self.status()["generation"], 2)
        self.assertEqual(self.status()["task"]["state"], 'WORKING')
        for path in self.scope.runtime.state_dir.rglob("*"):
            if path.is_file(): self.assertNotIn(b"PRIVATE_SENTINEL", path.read_bytes())

    def test_cancelled_waiting_and_decisions_suppress_stop(self):
        for state in ('WAITING', 'CANCELLED'):
            with self.subTest(state=state):
                self.arm()
                self.update(state=state)
                self.assertEqual(self.event("Stop").action, "pass")
                self.assertEqual(self.status()["task"]["state"], state)
                # A separate fixture preserves terminal task identity and consumed history.
                self.fresh_scope()
        self.arm()
        for kind in ("decision", "blocker"):
            self.update(conditions=[dict(id=kind, kind=kind, action="task", reason="Waiting", reference="external:1")])
            self.assertEqual(self.event("Stop").action, "pass")
            self.update(resolutions=[dict(id=kind, reference="user:resolution")])
        self.assertEqual(self.event("Stop").action, "continue")

    def test_turn_uncertainty_and_conflicting_identity_revoke_continuation(self):
        self.arm()
        self.event("PostToolUse")
        self.event("PostToolUse", receipt_fingerprint="conflicting")
        self.assertTrue(self.status()["task"]["turn_uncertain"])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertTrue(self.status()["task"]["conditions"][0]["continuation"]["revoked"])

    def test_continuation_budget_cannot_be_increased_on_edits(self):
        self.arm()
        self.update(conditions=[self.runnable(continuation={"turn_id": "turn", "budget": 0})])
        with self.assertRaises(self.api().StateError):
            self.update(conditions=[self.runnable()])
        self.assertEqual(self.event("Stop").action, "pass")

    def test_symlink_paths_missing_database_and_read_only_status_are_safe(self):
        self.start()
        directory = self.scope.runtime.state_dir
        database = directory / "runtime.sqlite3"
        before = database.read_bytes()
        self.status()
        self.api().inspect_state(self.scope)
        self.assertEqual(database.read_bytes(), before)
        target = self.base / "database-copy"
        database.rename(target)
        database.symlink_to(target)
        self.assertEqual(self.event().action, "context")
        with self.assertRaises(self.api().StateError):
            self.start()
        self.assertEqual(database.readlink(), target)
        self.assertEqual(target.read_bytes(), before)
        database.unlink()
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertFalse(database.exists())
        from runtime import workstreams
        workstreams._path(self.scope).unlink()
        directory.rename(self.base/'retained-original-authority')
        directory.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(self.api().StateError):
            self.start()
        self.assertFalse((self.base / "runtime.sqlite3").exists())

    def test_corrupt_typed_task_record_fails_closed(self):
        self.arm()
        path = self.scope.runtime.state_dir / "runtime.sqlite3"
        original = self.status()["task"]
        for value in ({**original, "turn_uncertain": None},
                      {**original, "revision": -1},
                      {**original, "conditions": [dict(original["conditions"][0], status="unknown")]},
                      {**original, "turn_id": []}):
            with self.subTest(value=value):
                with closing(sqlite3.connect(path)) as db, db:
                    db.execute("update tasks set data=?", (json.dumps(value),))
                with self.assertRaises(self.api().StateError):
                    self.status()
                self.assertEqual(self.event().action, "deny")
                self.assertEqual(self.event("Stop").action, "pass")

    def test_update_rejects_malformed_conditions_and_identity_fields(self):
        self.arm()
        for updates in ({"conditions": [dict(self.runnable(), id=[])]},
                        {"conditions": [dict(self.runnable(), kind=[])]},
                        {"conditions": [dict(self.runnable(), continuation={"turn_id": "turn", "budget": True})]},
                        {"conditions": [dict(self.runnable(), unknown=True)]},
                        {"conditions": [self.runnable(), self.runnable()]},
                        {"resolutions": [{"id": "missing", "reference": "ref"}]},
                        {"state": []}, {"expected_revision": True}, {"scope_ref": "different"}):
            with self.subTest(updates=updates), self.assertRaises(self.api().StateError):
                self.update(**updates)

    def test_unseen_new_turn_post_revokes_old_request_without_binding_or_pausing(self):
        self.arm()
        self.event("PostToolUse", turn_id="unseen-new-turn", tool_call_id="new-result")
        task = self.status()["task"]
        self.assertEqual(task["state"], 'WORKING')
        self.assertEqual(task["turn_id"], "turn")
        self.assertTrue(task["turn_uncertain"])
        self.assertEqual(self.event("Stop").action, "pass")
        self.assertTrue(task["conditions"][0]["continuation"]["revoked"])
        # It cannot later become an observed bound turn from completion alone.
        self.event(turn_id="unseen-new-turn", tool_call_id="followup")
        self.assertEqual(self.status()["task"]["turn_id"], "turn")

    def test_unknown_or_missing_stop_persistently_suppresses_old_allowance(self):
        for observed in ("unseen-stop-turn", None):
            with self.subTest(observed=observed):
                self.fresh_scope()
                self.arm()
                self.assertEqual(self.event("Stop", turn_id=observed).action, "pass")
                task = self.status()["task"]
                self.assertEqual(task["state"], 'WORKING')
                self.assertEqual(task["turn_id"], "turn")
                self.assertTrue(task["turn_uncertain"])
                self.assertTrue(task["conditions"][0]["continuation"]["revoked"])
                self.assertEqual(self.event("Stop").action, "pass")
                if observed:
                    self.event(turn_id=observed, tool_call_id="followup")
                    self.assertEqual(self.status()["task"]["turn_id"], "turn")
                with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db:
                    turns = db.execute("select turn_id,task_id from turns order by turn_id").fetchall()
                expected_turns = [("turn", "task")]
                if observed:
                    expected_turns.append((observed, None))
                self.assertEqual(turns, sorted(expected_turns))

    def test_conflicted_receipt_remains_poisoned_when_original_fingerprint_returns_after_restart(self):
        self.start()
        self.event("PostToolUse", receipt_fingerprint="A")
        self.assertEqual(self.status()["generation"], 1)
        self.event("PostToolUse", receipt_fingerprint="A")
        self.assertEqual(self.status()["generation"], 1)
        self.assertEqual(self.event("PostToolUse", receipt_fingerprint="B").action, "context")
        self.assertEqual(self.status()["generation"], 2)
        code = ("from pathlib import Path; from runtime.startup import *; from runtime.state import *; "
                "from runtime.engine import *; import json; "
                f"from tests.fixture_canonical_birth import acquire_test_scope; s=acquire_test_scope({str(self.test_acquisition)!r}); "
                "r=process_event(s,RuntimeEvent('PostToolUse','codex','session',s.repository_root,'turn','call',"
                "'apply_patch',False,'A')); print(json.dumps({'action':r.action,"
                "'generation':task_status(s,'codex','session')['generation']}))")
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=PACKAGE,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(action="context", generation=3))
        self.assertEqual(self.event("PostToolUse", receipt_fingerprint="A").action, "context")
        self.assertEqual(self.status()["generation"], 4)
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db:
            self.assertEqual(db.execute("select fingerprint,conflicted from receipts").fetchall(), [("A", 1)])

    def test_unknown_interrupt_turn_is_unbound_and_cannot_be_guessed_by_later_pre(self):
        self.arm()
        original = self.status()["task"]
        self.assertEqual(self.event("Interrupt", turn_id="unknown-interrupt-turn").action, "context")
        self.assertEqual(self.status()["task"], original)
        self.event(turn_id="unknown-interrupt-turn", tool_call_id="later-pre")
        self.assertEqual(self.status()["task"], original)
        self.assertEqual(self.event("Stop").action, "continue")
        with closing(sqlite3.connect(self.scope.runtime.state_dir / "runtime.sqlite3")) as db:
            self.assertEqual(db.execute("select task_id from turns where turn_id='unknown-interrupt-turn'").fetchone(), (None,))

    def test_per_turn_continuation_cap_survives_distinct_condition_ids_and_edits(self):
        self.arm()
        self.update(conditions=[self.runnable(id="second")])
        self.assertEqual(self.event("Stop").action, "continue")
        self.assertEqual(self.event("Stop").action, "pass")
        self.update(conditions=[self.runnable(id="third")])
        self.assertEqual(self.event("Stop").action, "pass")
        self.event(turn_id="second-turn", tool_call_id="second-turn-call")
        self.update(conditions=[self.runnable(id="new-turn-step", continuation={"turn_id": "second-turn", "budget": 1})])
        self.assertEqual(self.event("Stop", turn_id="second-turn").action, "continue")
        self.assertEqual(self.event("Stop", turn_id="second-turn").action, "pass")

    def test_concurrent_separate_process_stop_consumes_once(self):
        self.arm()
        self.update(conditions=[self.runnable(id="second"), self.runnable(id="third")])
        code = ("from pathlib import Path; from runtime.startup import *; from runtime.state import *; "
                "from runtime.engine import *; import json; "
                f"from tests.fixture_canonical_birth import acquire_test_scope; s=acquire_test_scope({str(self.test_acquisition)!r}); "
                "print(process_event(s, RuntimeEvent('Stop','codex','session',s.repository_root,'turn',"
                "stop_hook_active=False)).action)")
        processes = [subprocess.Popen([sys.executable, "-B", "-c", code], cwd=PACKAGE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        results = [process.communicate(timeout=5) for process in processes]
        self.assertTrue(all(process.returncode == 0 for process in processes), results)
        self.assertEqual(sum(stdout.strip() == b"continue" for stdout, _ in results), 1, results)


    def test_actual_startup_only_acquisition_remains_non_enforcing(self):
        from tests.test_runtime import RuntimeTests
        from runtime import startup
        from types import SimpleNamespace
        fixture=RuntimeTests('runTest');self.addCleanup(fixture.doCleanups);fixture.setUp()
        fixture.enroll()
        acquired=startup.acquire_project_entry(SimpleNamespace(client='codex',cwd=fixture.repo),PACKAGE)
        scope=startup._entry_scope(acquired)
        self.assertIsInstance(scope,startup.ProjectScope)
        self.assertIsNone(scope.runtime)
        self.assertEqual(self.api().process_event(scope,RuntimeEvent('PreToolUse','codex','session',fixture.repo,tool_name='apply_patch')).action,'pass')
        self.assertTrue(self.api().inspect_state(scope)['compatible'])

    def test_real_members_and_projects_isolate_task_identity(self):
        from tests.fixture_canonical_birth import CanonicalBirthFixture, fresh_canonical
        from runtime import state
        owner=CanonicalBirthFixture('runTest');self.addCleanup(owner.doCleanups)
        fixture,acquired,_=owner.accepted_birth(peer=True)
        members=[a.resolution.scope for a in fixture.member_acquisitions.values()]
        self.assertEqual(len(members),2)
        self.assertEqual(members[0].project_id,members[1].project_id)
        self.assertNotEqual(members[0].repository_id,members[1].repository_id)
        self.assertNotEqual(members[0].git_common_dir,members[1].git_common_dir)
        self.assertEqual(members[0].runtime.state_dir,members[1].runtime.state_dir)
        state.start_task(members[0],self.request)
        self.assertIsNone(state.task_status(members[1],'codex','session')['task'])
        state.start_task(members[1],self.request)
        self.assertNotEqual(state._scope_id(members[0]),state._scope_id(members[1]))
        _,other,_=fresh_canonical(self)
        self.assertNotEqual(other.resolution.scope.project_id,members[0].project_id)
        self.assertIsNone(state.task_status(other.resolution.scope,'codex','session')['task'])


if __name__ == "__main__":
    unittest.main()
