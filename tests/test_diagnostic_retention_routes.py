"""Classify authentic diagnostic-output routes without authorizing deletion."""

import unittest

from runtime import state
from tests import test_evidence as evidence_tests


class DiagnosticRetentionRoutesTests(unittest.TestCase):
    def fixture(self):
        fixture = evidence_tests.AssignedCancelledClosureTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def closed_output(self, fixture):
        task, context, task_ref, attempt = fixture.stopped()
        api = fixture.fixture.api
        execution_ref = attempt["execution"]
        execution = api._read(fixture.scope, execution_ref)
        output_ref = execution["output"]
        closed = fixture.close(task, context, task_ref)
        mandate_ref = api._source_mandate(context, output_ref)["reference"]
        return task, context, task_ref, attempt, execution_ref, output_ref, closed, mandate_ref

    def output_row(self, report, output_ref):
        return next(row for row in report["outputs"] if row["reference"] == output_ref)

    def assert_protected_or_guarded(self, api, context, task_ref, output_ref):
        try:
            with api._context_database(context) as db:
                report = api._diagnostic_output_routes(context, db, task_ref)
        except state.StateError:
            return
        self.assertFalse(report["deletion_authorized"])
        output = next((row for row in report["outputs"] if row["reference"] == output_ref), None)
        if output is None:
            self.assertTrue(report.get("blockers"), report)
            return
        self.assertTrue(output["blockers"] or any(route["disposition"] == "protected" for route in output["routes"]))
        self.assertFalse(any(route["disposition"] == "diagnostic-provenance" for route in output["routes"]))

    def test_actual_retirement_classifies_execution_compact_and_birth_mandate(self):
        f = self.fixture()
        task, context, task_ref, attempt, execution_ref, output_ref, closed, mandate_ref = self.closed_output(f)
        api = f.fixture.api
        compact_ref = closed["lifecycle"]["compact"]
        references = (output_ref, execution_ref, compact_ref, mandate_ref)
        paths = {ref["name"]: context.directory / "evidence" / ref["name"] for ref in references}
        bytes_before = {name: path.read_bytes() for name, path in paths.items()}
        compact_before = api._read(f.scope, compact_ref)
        authority = api._canonical_plain(context.authority_set[0])
        root = dict(owner=task_ref, kind="artifact", reference=dict(authority=authority, artifact=execution_ref), need="content")
        with api._context_database(context) as db:
            requirements_before = api._lifecycle_requirements(context, [root], db=db)
            metadata_before = {ref["name"]: state._metadata(db, ref["name"]) for ref in references}

        retired = api._retire_closed_task(context, task_ref)
        self.assertTrue(retired["retired"], retired)
        report = retired["diagnostic_retention"]
        self.assertEqual(report["owner"], task_ref)
        self.assertEqual(report["closed_at"], closed["lifecycle"]["safe_closed_at"])
        self.assertEqual(report["coverage"], "classification-only")
        self.assertFalse(report["deletion_authorized"])
        output = self.output_row(report, output_ref)
        self.assertEqual(output["execution"], execution_ref)
        self.assertEqual(output["closure"], compact_ref)
        routes = {route["kind"]: route for route in output["routes"]}
        for kind, owner, disposition in (
            ("execution", execution_ref, "diagnostic-provenance"),
            ("compact", compact_ref, "diagnostic-provenance"),
            ("mandate", mandate_ref, "bookkeeping"),
        ):
            self.assertEqual(routes[kind]["owner"], owner)
            self.assertEqual(routes[kind]["disposition"], disposition)
            self.assertTrue(routes[kind]["reason"])

        self.assertEqual({name: path.read_bytes() for name, path in paths.items()}, bytes_before)
        self.assertEqual(api._read(f.scope, compact_ref), compact_before)
        numbers = {row["field"]: row for row in compact_before["numerical"]}
        self.assertEqual(numbers["exit_code"]["value"], attempt["exit_code"])
        self.assertEqual(numbers["output_bytes"]["value"], attempt["output_bytes"])
        self.assertEqual(numbers["output_bytes"]["source"], execution_ref)
        with api._context_database(context) as db:
            requirements_after = api._lifecycle_requirements(context, [root], db=db)
            metadata_after = {ref["name"]: state._metadata(db, ref["name"]) for ref in references}
        self.assertEqual(requirements_after, requirements_before)
        for ref in references:
            self.assertEqual(metadata_after[ref["name"]]["created_at"], metadata_before[ref["name"]]["created_at"])
            self.assertEqual(metadata_after[ref["name"]]["bytes"], metadata_before[ref["name"]]["bytes"])
        self.assertEqual(metadata_after[compact_ref["name"]]["closed_at"], report["closed_at"])

    def test_substituted_execution_output_is_protected_or_rejected_by_existing_guard(self):
        f = self.fixture()
        _, context, task_ref, _, execution_ref, output_ref, closed, _ = self.closed_output(f)
        api = f.fixture.api
        retired = api._retire_closed_task(context, task_ref)
        self.assertTrue(retired["retired"], retired)
        output = self.output_row(retired["diagnostic_retention"], output_ref)
        routes = {route["kind"]: route for route in output["routes"]}
        self.assertEqual(routes["execution"]["owner"], execution_ref)
        self.assertEqual(routes["execution"]["disposition"], "diagnostic-provenance")
        compact_ref = closed["lifecycle"]["compact"]
        self.assertEqual(routes["compact"]["owner"], compact_ref)
        self.assertEqual(routes["compact"]["disposition"], "diagnostic-provenance")
        path = context.directory / "evidence" / execution_ref["name"]
        original = path.read_bytes()
        path.chmod(0o600)
        path.write_bytes(original + b" ")
        path.chmod(0o400)
        try:
            self.assert_protected_or_guarded(api, context, task_ref, output_ref)
        finally:
            path.chmod(0o600)
            path.write_bytes(original)
            path.chmod(0o400)

    def test_independent_live_artifact_admission_keeps_output_route_protected(self):
        f = self.fixture()
        task, context, task_ref, attempt = f.stopped()
        api = f.fixture.api
        execution_ref = attempt["execution"]
        output_ref = api._read(f.scope, execution_ref)["output"]
        other = state.start_task(f.scope, f.fixture.identity | dict(session_id="independent-session",
            task_id="independent-task", scope_ref="user:independent", mode="editable",
            decision_ref="user:accepted"))["task"]
        consumer_ref = api._artifact(f.scope, dict(kind="fixture-independent-consumer", output=output_ref),
            owner=api._artifact_owner(f.scope, other))
        closed = f.close(task, context, task_ref)

        retired = api._retire_closed_task(context, task_ref)
        self.assertTrue(retired["retired"], retired)
        output = self.output_row(retired["diagnostic_retention"], output_ref)
        self.assertTrue(any(route["owner"] == consumer_ref and route["disposition"] == "protected"
                            for route in output["routes"]))
        self.assertEqual(api._read(f.scope, closed["lifecycle"]["compact"])["kind"], "task-closure")

    def test_missing_or_corrupt_real_birth_mandate_never_classifies_as_bookkeeping(self):
        for mode in ("missing", "corrupt"):
            with self.subTest(mode=mode):
                f = self.fixture()
                _, context, task_ref, _, _, output_ref, _, mandate_ref = self.closed_output(f)
                api = f.fixture.api
                api._retire_closed_task(context, task_ref)
                path = context.directory / "evidence" / mandate_ref["name"]
                original = path.read_bytes()
                moved = path.with_name("missing-mandate-fixture")
                if mode == "missing":
                    path.rename(moved)
                else:
                    path.chmod(0o600)
                    path.write_bytes(original + b" ")
                    path.chmod(0o400)
                try:
                    self.assert_protected_or_guarded(api, context, task_ref, output_ref)
                finally:
                    if mode == "missing":
                        moved.rename(path)
                    else:
                        path.chmod(0o600)
                        path.write_bytes(original)
                        path.chmod(0o400)


if __name__ == "__main__":
    unittest.main()
