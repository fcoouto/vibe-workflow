"""Cleanup component safety with explicit prevalidated consumer input.

Authority birth, closure, permissions and filesystem transactions remain real.
The component precondition is not evidence that public canonical cleanup is eligible.
"""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import state
from tests import test_evidence as evidence_tests


class DiagnosticCleanupComponentTests(unittest.TestCase):
    retention_age = 7 * 24 * 60 * 60

    def fixture(self, *, consumer_proof=True):
        clock = [1_800_000_000]
        clock_patch = patch.object(state, "_owner_time", side_effect=lambda: clock[0])
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

        class D4AssignedCancelledFixture(evidence_tests.D4RecoveryFixture,
                                         evidence_tests.AssignedCancelledClosureTests):
            def setUp(self):
                evidence_tests.CanonicalEvidenceTests.setUp(self)

            def stopped(self):
                original_root = self.fixture.root
                proof_root = original_root / "stop-proofs" / self.fixture.identity["task_id"]
                proof_root.mkdir(parents=True, exist_ok=True)
                self.fixture.root = proof_root
                try:
                    return super().stopped()
                finally:
                    self.fixture.root = original_root

        fixture = D4AssignedCancelledFixture(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if consumer_proof:
            self.enterContext(patch.object(fixture.fixture.api, "_diagnostic_consumers",
                return_value=dict(complete=True, required=[], blockers=[])))
        return fixture, clock

    def closed_output(self, fixture):
        task, context, task_ref, attempt = fixture.stopped()
        api = fixture.fixture.api
        execution = api._read(fixture.scope, attempt["execution"])
        output_ref = execution["output"]
        execution_ref = attempt["execution"]
        closed = fixture.close(task, context, task_ref)
        compact_ref = closed["lifecycle"]["compact"]
        retired = api._retire_closed_task(context, task_ref)
        self.assertTrue(retired["retired"], retired)
        return task, context, task_ref, attempt, execution_ref, output_ref, compact_ref, closed

    def file_bytes(self, context, *references):
        paths = {ref["name"]: context.directory / "evidence" / ref["name"] for ref in references}
        return paths, {name: path.read_bytes() for name, path in paths.items()}

    def test_current_birth_history_without_complete_consumer_acquisition_remains_protected(self):
        fixture, clock = self.fixture(consumer_proof=False)
        _, context, _, _, _, output, _, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        path = context.directory / "evidence" / output["name"]
        original = path.read_bytes()
        clock[0] = closed["lifecycle"]["safe_closed_at"] + self.retention_age
        result = api._cleanup_diagnostics(context)
        self.assertEqual(result["expired"], [])
        self.assertEqual(result["pending"], [])
        self.assertIn(dict(reference=output, reason="Diagnostic consumer content is unavailable"),
                      result["protected"])
        self.assertEqual(path.read_bytes(), original)
        with api._context_database(context) as db:
            self.assertEqual(state._metadata(db, output["name"])["status"], "live")

    def test_age_boundary_deletes_only_diagnostic_and_preserves_compact_receipts(self):
        fixture, clock = self.fixture()
        task, context, task_ref, attempt, execution_ref, output_ref, compact_ref, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        execution_path, original = self.file_bytes(context, execution_ref, compact_ref, output_ref)
        compact_before = api._read(fixture.scope, compact_ref)
        with api._context_database(context) as db:
            metadata_before = {ref["name"]: state._metadata(db, ref["name"])
                               for ref in (execution_ref, compact_ref, output_ref)}
        first_closed = closed["lifecycle"]["safe_closed_at"]

        clock[0] = first_closed + self.retention_age - 1
        early = api._cleanup_diagnostics(context)
        self.assertNotIn(output_ref, early["expired"])
        self.assertTrue(execution_path[output_ref["name"]].exists())

        clock[0] = first_closed + self.retention_age
        result = api._cleanup_diagnostics(context)
        self.assertIn(output_ref, result["expired"])
        self.assertFalse(execution_path[output_ref["name"]].exists())
        self.assertEqual(execution_path[execution_ref["name"]].read_bytes(), original[execution_ref["name"]])
        self.assertEqual(execution_path[compact_ref["name"]].read_bytes(), original[compact_ref["name"]])
        compact_after = api._read(fixture.scope, compact_ref)
        self.assertEqual(compact_after, compact_before)
        numbers = {row["field"]: row for row in compact_after["numerical"]}
        self.assertEqual(numbers["exit_code"]["value"], attempt["exit_code"])
        self.assertEqual(numbers["output_bytes"]["value"], attempt["output_bytes"])
        self.assertEqual(numbers["output_bytes"]["source"], execution_ref)
        with api._context_database(context) as db:
            metadata_after = {ref["name"]: state._metadata(db, ref["name"])
                              for ref in (execution_ref, compact_ref, output_ref)}
        for ref in (execution_ref, compact_ref, output_ref):
            self.assertEqual(metadata_after[ref["name"]]["created_at"], metadata_before[ref["name"]]["created_at"])
        self.assertEqual(metadata_after[execution_ref["name"]]["closed_at"],
                         metadata_before[execution_ref["name"]]["closed_at"])
        self.assertEqual(metadata_after[compact_ref["name"]]["closed_at"],
                         metadata_before[compact_ref["name"]]["closed_at"])
        self.assertEqual(metadata_after[output_ref["name"]]["closed_at"], first_closed)
        with self.assertRaises(state.StateError):
            api._read(fixture.scope, output_ref)
        active = state.start_task(fixture.scope, fixture.fixture.identity | dict(task_id="expired-admission",
            scope_ref="user:expired-admission", mode="editable", decision_ref="user:accepted"))["task"]
        authority = api._canonical_plain(context.authority_set[0])
        with api._context_database(context, write=True) as db:
            with self.assertRaises(state.StateError):
                api._artifact(fixture.scope, dict(kind="fixture-expired-detail",
                    source=dict(authority=authority, artifact=output_ref)),
                    owner=api._artifact_owner(fixture.scope, active), db=db)

    def test_outer_artifact_opportunity_cleans_then_unrelated_successor_closes(self):
        fixture, clock = self.fixture()
        _, context, _, _, _, output_ref, _, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        clock[0] = closed["lifecycle"]["safe_closed_at"] + self.retention_age

        successor_identity = fixture.fixture.identity | dict(task_id="successor", scope_ref="user:successor")
        successor = state.start_task(fixture.scope, successor_identity | dict(mode="editable", decision_ref="user:accepted"))["task"]
        fixture.fixture.identity = successor_identity
        fixture.fixture.plan()
        admitted = api._artifact(fixture.scope, dict(kind="fixture-normal-use-opportunity"),
                                 owner=api._artifact_owner(fixture.scope, successor))
        self.assertTrue(admitted["name"])
        self.assertFalse((context.directory / "evidence" / output_ref["name"]).exists())

        next_task, next_context, next_ref, _ = fixture.stopped()
        fixture.close(next_task, next_context, next_ref)
        retired = api._retire_closed_task(next_context, next_ref)
        self.assertTrue(retired["retired"], retired)

    def interrupt_unlink(self, output_path, *, after):
        original_unlink = os.unlink
        evidence_dir = output_path.parent
        evidence_stat = evidence_dir.stat()

        def interrupted(path, *args, **kwargs):
            selected = Path(os.fsdecode(path)).name == output_path.name
            dir_fd = kwargs.get("dir_fd")
            if dir_fd is not None:
                parent = os.fstat(dir_fd)
                selected = selected and (parent.st_dev, parent.st_ino) == (evidence_stat.st_dev, evidence_stat.st_ino)
            if not selected:
                return original_unlink(path, *args, **kwargs)
            if after:
                original_unlink(path, *args, **kwargs)
            raise RuntimeError("fixture: interrupted diagnostic unlink")

        return patch("runtime.evidence.os.unlink", side_effect=interrupted)

    def test_pending_before_unlink_refuses_reads_and_admission_then_retries(self):
        fixture, clock = self.fixture()
        task, context, _, _, _, output_ref, _, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        output_path = context.directory / "evidence" / output_ref["name"]
        clock[0] = closed["lifecycle"]["safe_closed_at"] + self.retention_age
        with self.interrupt_unlink(output_path, after=False):
            with self.assertRaises(RuntimeError):
                api._cleanup_diagnostics(context)
        with api._context_database(context) as db:
            pending = state._metadata(db, output_ref["name"])
        self.assertEqual(pending["status"], "delete_pending")
        with self.assertRaises(state.StateError):
            api._read(fixture.scope, output_ref)
        authority = api._canonical_plain(context.authority_set[0])
        active = state.start_task(fixture.scope, fixture.fixture.identity | dict(task_id="pending-admission",
            scope_ref="user:pending-admission", mode="editable", decision_ref="user:accepted"))["task"]
        owner = api._artifact_owner(fixture.scope, active)
        with api._context_database(context, write=True) as db:
            with self.assertRaises(state.StateError):
                api._artifact(fixture.scope, dict(kind="fixture-new-detail",
                    source=dict(authority=authority, artifact=output_ref)), owner=owner, db=db)
        retried = api._cleanup_diagnostics(context)
        self.assertIn(output_ref, retried["expired"])
        self.assertFalse(output_path.exists())

    def test_unlink_then_interruption_resumes_absent_pending_bytes(self):
        fixture, clock = self.fixture()
        _, context, _, _, _, output_ref, _, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        output_path = context.directory / "evidence" / output_ref["name"]
        clock[0] = closed["lifecycle"]["safe_closed_at"] + self.retention_age
        with self.interrupt_unlink(output_path, after=True):
            with self.assertRaises(RuntimeError):
                api._cleanup_diagnostics(context)
        self.assertFalse(output_path.exists())
        with api._context_database(context) as db:
            pending = state._metadata(db, output_ref["name"])
        self.assertEqual(pending["status"], "delete_pending")
        result = api._cleanup_diagnostics(context)
        self.assertIn(output_ref, result["expired"])
        self.assertFalse(output_path.exists())

    def test_same_byte_replacement_after_pending_interruption_is_preserved(self):
        fixture, clock = self.fixture()
        _, context, _, _, _, output_ref, _, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        output_path = context.directory / "evidence" / output_ref["name"]
        original = output_path.read_bytes()
        clock[0] = closed["lifecycle"]["safe_closed_at"] + self.retention_age
        with self.interrupt_unlink(output_path, after=False):
            with self.assertRaises(RuntimeError):
                api._cleanup_diagnostics(context)
        replacement = output_path.with_name("replacement-output")
        replacement.write_bytes(original)
        os.chmod(replacement, 0o400)
        os.replace(replacement, output_path)
        try:
            protected = api._cleanup_diagnostics(context)
            self.assertNotIn(output_ref, protected["expired"])
            self.assertIn(output_ref, protected["pending"])
            self.assertEqual(output_path.read_bytes(), original)
        finally:
            output_path.chmod(0o600)
            output_path.unlink(missing_ok=True)

    def test_parent_replacement_after_pending_interruption_is_preserved(self):
        fixture, clock = self.fixture()
        _, context, _, _, _, output_ref, _, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        output_path = context.directory / "evidence" / output_ref["name"]
        evidence_dir = output_path.parent
        clock[0] = closed["lifecycle"]["safe_closed_at"] + self.retention_age
        with self.interrupt_unlink(output_path, after=False):
            with self.assertRaises(RuntimeError):
                api._cleanup_diagnostics(context)

        displaced = evidence_dir.with_name("evidence-displaced")
        evidence_dir.rename(displaced)
        evidence_dir.mkdir(mode=0o700)
        replacement = evidence_dir / output_ref["name"]
        replacement.write_bytes(displaced.joinpath(output_ref["name"]).read_bytes())
        replacement.chmod(0o400)
        try:
            result = api._cleanup_diagnostics(context)
        except state.StateError:
            result = None
        if result is not None:
            self.assertNotIn(output_ref, result["expired"])
            self.assertTrue(any(row["reference"] == output_ref for row in result["protected"]))
        with api._context_database(context) as db:
            self.assertEqual(state._metadata(db, output_ref["name"])["status"], "delete_pending")
        self.assertTrue(replacement.exists())
        self.assertEqual(replacement.read_bytes(), displaced.joinpath(output_ref["name"]).read_bytes())

    def test_missing_live_output_is_protected(self):
        fixture, clock = self.fixture()
        _, context, _, _, _, output_ref, _, closed = self.closed_output(fixture)
        api = fixture.fixture.api
        output_path = context.directory / "evidence" / output_ref["name"]
        output_path.unlink()
        clock[0] = closed["lifecycle"]["safe_closed_at"] + self.retention_age
        result = api._cleanup_diagnostics(context)
        self.assertNotIn(output_ref, result["expired"])
        self.assertTrue(any(row["reference"] == output_ref for row in result["protected"]))
        self.assertFalse(output_path.exists())
        with self.assertRaises(state.StateError):
            api._read(fixture.scope, output_ref)

    def test_two_current_roots_converging_on_shared_leaf_return_unique_edges(self):
        fixture, _ = self.fixture()
        api = fixture.fixture.api
        context = api._evidence_context(fixture.scope)
        task_a = state.task_status(fixture.scope, "codex", "parent")["task"]
        task_b = state.start_task(fixture.scope, fixture.fixture.identity | dict(session_id="converging-b",
            task_id="converging-b", scope_ref="user:converging-b", mode="editable",
            decision_ref="user:accepted"))["task"]
        authority = api._canonical_plain(context.authority_set[0])
        owner_b = api._artifact_owner(fixture.scope, task_b)
        leaf = api._artifact(fixture.scope, dict(kind="fixture-shared-leaf"), owner=owner_b)
        shared = api._artifact(fixture.scope, dict(kind="fixture-shared-parent",
            child=dict(authority=authority, artifact=leaf)), owner=owner_b)
        root_a = api._artifact(fixture.scope, dict(kind="fixture-root-a",
            child=dict(authority=authority, artifact=shared)), owner=api._artifact_owner(fixture.scope, task_a))
        root_b = api._artifact(fixture.scope, dict(kind="fixture-root-b",
            child=dict(authority=authority, artifact=shared)), owner=owner_b)
        roots = [
            dict(owner=api._canonical_plain(api._artifact_owner(fixture.scope, task_a).task), kind="artifact",
                 reference=dict(authority=authority, artifact=root_a), need="content"),
            dict(owner=api._canonical_plain(owner_b.task), kind="artifact",
                 reference=dict(authority=authority, artifact=root_b), need="content"),
        ]

        with api._context_database(context) as db:
            requirements = api._lifecycle_requirements(context, roots, db=db)

        self.assertNotEqual(roots[0]["owner"], roots[1]["owner"])
        self.assertNotEqual(roots[0]["reference"], roots[1]["reference"])
        leaf_edge = dict(owner=dict(authority=authority, artifact=shared), kind="artifact",
                         reference=dict(authority=authority, artifact=leaf), need="content")
        self.assertEqual(requirements.count(leaf_edge), 1)
        qualified = lambda reference: dict(authority=authority, artifact=reference)
        task_edges = [
            dict(owner=qualified(reference), kind="task", reference=task_ref, need="identity")
            for reference, task_ref in (
                (leaf, api._canonical_plain(owner_b.task)),
                (shared, api._canonical_plain(owner_b.task)),
                (root_a, roots[0]["owner"]),
                (root_b, roots[1]["owner"]),
            )
        ]
        artifact_edges = [
            dict(owner=qualified(root_a), kind="artifact", reference=qualified(shared), need="content"),
            dict(owner=qualified(root_b), kind="artifact", reference=qualified(shared), need="content"),
            leaf_edge,
        ]
        expected = sorted([*roots, *task_edges, *artifact_edges], key=api._raw)
        actual = sorted(requirements, key=api._raw)
        self.assertEqual(len(expected), 9)
        self.assertEqual(actual, expected)
        self.assertEqual(api._digest(actual), api._digest(expected))
        self.assertEqual([edge["owner"] for edge in artifact_edges[:2]],
                         [qualified(root_a), qualified(root_b)])

    def test_final_permission_recheck_preserves_target_and_parent_replacements(self):
        for replacement_kind in ("target", "parent"):
            with self.subTest(replacement_kind=replacement_kind):
                fixture, clock = self.fixture()
                _, context, _, _, execution_ref, output_ref, _, closed = self.closed_output(fixture)
                api = fixture.fixture.api
                output_path = context.directory / "evidence" / output_ref["name"]
                evidence_dir = output_path.parent
                original_bytes = output_path.read_bytes()
                original_inode = output_path.stat().st_ino
                first_closed = closed["lifecycle"]["safe_closed_at"]
                clock[0] = first_closed + self.retention_age
                original_permission = api._source_current_permission
                observations = [0]
                triggered = []
                displaced = evidence_dir.with_name("evidence-final-race-original")

                def replace_after_final_permission(*args, **kwargs):
                    result = original_permission(*args, **kwargs)
                    source = args[1] if len(args) > 1 else kwargs["source"]
                    if source.get("artifact") == execution_ref:
                        observations[0] += 1
                        if observations[0] == 3:
                            if replacement_kind == "target":
                                replacement = output_path.with_name("final-race-replacement")
                                replacement.write_bytes(original_bytes)
                                os.chmod(replacement, 0o400)
                                os.replace(replacement, output_path)
                            else:
                                evidence_dir.rename(displaced)
                                evidence_dir.mkdir(mode=0o700)
                                replacement = evidence_dir / output_ref["name"]
                                replacement.write_bytes(original_bytes)
                                os.chmod(replacement, 0o400)
                            triggered.append(observations[0])
                    return result

                with patch.object(api, "_source_current_permission", side_effect=replace_after_final_permission):
                    result = api._cleanup_diagnostics(context)

                self.assertEqual(triggered, [3], "final execution enrollment permission observation was not reached")
                self.assertEqual(observations[0], 3)
                self.assertNotIn(output_ref, result["expired"])
                self.assertTrue(any(row["reference"] == output_ref for row in result["protected"]))
                with api._context_database(context) as db:
                    self.assertEqual(state._metadata(db, output_ref["name"])["status"], "delete_pending")
                self.assertTrue(output_path.exists())
                self.assertEqual(output_path.read_bytes(), original_bytes)
                self.assertNotEqual(output_path.stat().st_ino, original_inode)
                if replacement_kind == "parent":
                    self.assertTrue(displaced.joinpath(output_ref["name"]).exists())
                    self.assertEqual(displaced.joinpath(output_ref["name"]).read_bytes(), original_bytes)
                    self.assertEqual(displaced.joinpath(output_ref["name"]).stat().st_ino, original_inode)


if __name__ == "__main__":
    unittest.main()
