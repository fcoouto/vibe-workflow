"""Focused visibility/rollback regressions around the real linked D4 fixture."""

from contextlib import contextmanager
import sqlite3
import unittest
from unittest.mock import patch
import uuid


_METHOD = "closure_fixture"


def _scenario(test_case):
    from tests.test_canonical_linked_closure import CanonicalLinkedClosureTests
    from tests.test_evidence import D4RecoveryFixture

    owner = CanonicalLinkedClosureTests(methodName="runTest")
    test_case.addCleanup(owner.doCleanups)
    owner.setUp()
    scenario = owner.linked
    scenario.closure_fixture = owner.closure_fixture
    scenario.recovery_fixtures = [(owner.operation.fixture, owner.operation.request)]
    scenario.recovery_head = D4RecoveryFixture.recovery_head.__get__(scenario)
    return scenario


def _contexts(scenario):
    return tuple(scenario.api._evidence_context(member.scope) for member in scenario.members)


def _metadata(context, api):
    with api._context_database(context) as db:
        return [tuple(row) for row in db.execute(
            "select name,digest,owner_key,class,created_at,closed_at,status,bytes,data "
            "from evidence_metadata order by name")]


def _files(context):
    folder = context.directory / "evidence"
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


def _before(scenario, contexts):
    d4 = scenario.recovery_fixtures[0][0]
    return dict(
        tasks=[scenario.task(member) for member in scenario.members],
        heads=[scenario.recovery_head(d4, context) for context in contexts],
        metadata=[_metadata(context, scenario.api) for context in contexts],
        files=[_files(context) for context in contexts],
    )


def _assert_unchanged(test_case, scenario, contexts, before, *, additions=None):
    d4 = scenario.recovery_fixtures[0][0]
    test_case.assertEqual([scenario.task(member) for member in scenario.members], before["tasks"])
    test_case.assertEqual([scenario.recovery_head(d4, context) for context in contexts], before["heads"])
    test_case.assertEqual([_metadata(context, scenario.api) for context in contexts], before["metadata"])
    additions = additions or {}
    for index, context in enumerate(contexts):
        after = _files(context)
        expected = dict(before["files"][index])
        expected.update(additions.get(index, {}))
        test_case.assertEqual(after, expected)


class LinkedWriterVisibilityTests(unittest.TestCase):
    def test_denied_release_commit_reports_only_committed_replacements(self):
        from runtime import changes, evidence, state

        class ReleaseObserved(Exception):
            pass

        for failed_index in (0, 1):
            with self.subTest(failed_writer=failed_index):
                scenario = _scenario(self)
                contexts = _contexts(scenario)
                captured = {}
                registered = []
                release = changes._register_recovery_release
                guards = changes._recovery_release_guards
                register = evidence._register_recovery_source

                @contextmanager
                def ordered_guards(authorities, identity):
                    with guards(authorities, identity) as (path, journal, guarded):
                        by_database = {str(context.database): context for context in guarded}
                        yield path, journal, tuple(by_database[str(context.database)] for context in contexts)

                def deny_commit(action, operation, unused, database, trigger):
                    if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
                        captured["commit_denied"] = True
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK

                def register_then_deny(scope, db, source, *, context=None):
                    reference = register(scope, db, source, context=context)
                    if getattr(source, "release", None) is not None:
                        registered.append(dict(authority=evidence._canonical_plain(context.authority_set[0]),
                                               artifact=evidence._canonical_plain(reference)))
                        if context.database == contexts[failed_index].database:
                            captured.update(orphan=reference, orphan_context=context)
                            self.assertIsNotNone(state._metadata(db, reference["name"]))
                            db.set_authorizer(deny_commit)
                    return reference

                def capture_release(observation):
                    captured["before"] = _before(scenario, contexts)
                    captured["baselines"] = []
                    for context in contexts:
                        with evidence._context_database(context) as db:
                            captured["baselines"].append(evidence._canonical_plain(
                                state._source_baseline(None, db, context=context)))
                    captured["observation"] = observation
                    with patch.object(changes, "_recovery_release_guards", ordered_guards), \
                            patch.object(evidence, "_register_recovery_source", side_effect=register_then_deny):
                        captured["result"] = release(observation)
                    raise ReleaseObserved

                with patch.object(changes, "_register_recovery_release", side_effect=capture_release):
                    with self.assertRaises(ReleaseObserved):
                        getattr(scenario, _METHOD)()

                result = evidence._canonical_plain(captured["result"])
                self.assertTrue(captured.get("commit_denied"), "deny the actual COMMIT after registration")
                self.assertFalse(result["complete"], result)
                self.assertIn("registration unavailable or locked", " ".join(result["blockers"]))
                before = captured["before"]
                self.assertEqual([scenario.task(member) for member in scenario.members], before["tasks"])
                d4 = scenario.recovery_fixtures[0][0]
                for index, context in enumerate(contexts):
                    with evidence._context_database(context) as db:
                        baseline = evidence._canonical_plain(state._source_baseline(None, db, context=context))
                        if index < failed_index:
                            self.assertNotEqual(baseline, captured["baselines"][index])
                            self.assertIsNotNone(state._metadata(db, registered[index]["artifact"]["name"]))
                        else:
                            self.assertEqual(baseline, captured["baselines"][index])
                    if index < failed_index:
                        head = next(edge["reference"]["artifact"] for edge in baseline["dependencies"]
                                    if edge["reference"]["identity"] == captured["observation"].content["identity"])
                        self.assertEqual(head, registered[index]["artifact"])
                    else:
                        self.assertEqual(scenario.recovery_head(d4, context), before["heads"][index])
                        self.assertEqual(_metadata(context, evidence), before["metadata"][index])
                orphan_path = captured["orphan_context"].directory / "evidence" / captured["orphan"]["name"]
                expected_files = dict(before["files"][failed_index])
                expected_files[orphan_path.name] = orphan_path.read_bytes()
                self.assertEqual(_files(contexts[failed_index]), expected_files)
                self.assertNotIn(orphan_path.name, {row[0] for row in _metadata(contexts[failed_index], evidence)})
                certificate = next(row for row in captured["observation"].content["stopped"] if row.get("linked"))
                basis = evidence._canonical_plain(certificate["linked"])
                pointer = evidence._linked_basis_root(basis)
                with self.assertRaisesRegex(state.StateError, "Unaccounted evidence content protects preparation"):
                    with evidence._linked_stop_guards(pointer, basis["authorities"]) as guarded:
                        evidence._linked_preparation_facts(pointer, guarded)
                self.assertEqual(result["replacements"], registered[:failed_index])

    def test_original_lifecycle_succeeds_with_a_then_b_as_actual_writers(self):
        scenario = _scenario(self)
        from runtime import changes, evidence

        contexts = _contexts(scenario)
        expected = [str(context.database) for context in contexts]
        actual = []
        original_guards = changes._recovery_release_guards
        original_register = evidence._register_recovery_source

        @contextmanager
        def ordered_guards(authorities, identity):
            with original_guards(authorities, identity) as (path, journal, guarded):
                by_database = {str(context.database): context for context in guarded}
                ordered = tuple(by_database[database] for database in expected)
                yield path, journal, ordered

        def record_writer(scope, db, source, *, context=None):
            if getattr(source, "release", None) is not None:
                actual.append(str(context.database))
            return original_register(scope, db, source, context=context)

        with patch.object(changes, "_recovery_release_guards", ordered_guards), \
                patch.object(evidence, "_register_recovery_source", side_effect=record_writer):
            getattr(scenario, _METHOD)()

        self.assertEqual(actual, expected)

    def test_original_lifecycle_succeeds_with_b_then_a_as_actual_writers(self):
        scenario = _scenario(self)
        from runtime import changes, evidence

        contexts = _contexts(scenario)
        expected = [str(context.database) for context in reversed(contexts)]
        actual = []
        original_guards = changes._recovery_release_guards
        original_register = evidence._register_recovery_source

        @contextmanager
        def ordered_guards(authorities, identity):
            with original_guards(authorities, identity) as (path, journal, guarded):
                by_database = {str(context.database): context for context in guarded}
                yield path, journal, tuple(by_database[database] for database in expected)

        def record_writer(scope, db, source, *, context=None):
            if getattr(source, "release", None) is not None:
                actual.append(str(context.database))
            return original_register(scope, db, source, context=context)

        with patch.object(changes, "_recovery_release_guards", ordered_guards), \
                patch.object(evidence, "_register_recovery_source", side_effect=record_writer):
            getattr(scenario, _METHOD)()

        self.assertEqual(actual, expected)

    def test_unrelated_json_before_release_fails_closed_without_publication(self):
        scenario = _scenario(self)
        from runtime import changes

        contexts = _contexts(scenario)
        captured = {}
        original = changes._register_recovery_release

        def add_unrelated_file(observation):
            before = _before(scenario, contexts)
            path = contexts[0].directory / "evidence" / ("unmanaged-" + uuid.uuid4().hex + ".json")
            body = b'{"kind":"unrelated fixture evidence"}'
            path.write_bytes(body)
            captured.update(before=before, path=path, body=body)
            result = original(observation)
            captured["result"] = result
            return result

        with patch.object(changes, "_register_recovery_release", side_effect=add_unrelated_file):
            try:
                getattr(scenario, _METHOD)()
            except AssertionError:
                pass

        self.assertIn("result", captured, "the original lifecycle must reach the release assertion")
        self.assertFalse(captured["result"]["complete"], captured["result"])
        self.assertFalse(captured["result"]["replacements"])
        self.assertIn("Unaccounted", " ".join(captured["result"]["blockers"]))
        _assert_unchanged(self, scenario, contexts, captured["before"],
                          additions={0: {captured["path"].name: captured["body"]}})

    def test_extra_json_after_artifact_registration_rolls_back_writer(self):
        scenario = _scenario(self)
        from runtime import changes, evidence

        contexts = _contexts(scenario)
        captured = {}
        release = changes._register_recovery_release
        guards = changes._recovery_release_guards
        artifact = evidence._artifact

        @contextmanager
        def selected_writer_first(authorities, identity):
            with guards(authorities, identity) as (path, journal, guarded):
                by_database = {str(context.database): context for context in guarded}
                order = [contexts[0], *(context for context in guarded if context.database != contexts[0].database)]
                yield path, journal, tuple(by_database[str(context.database)] for context in order)

        def register_with_unmanaged_file(observation):
            captured["before"] = _before(scenario, contexts)
            captured["observation"] = observation
            with patch.object(changes, "_recovery_release_guards", selected_writer_first):
                with patch.object(evidence, "_artifact", side_effect=add_file_after_artifact):
                    result = release(observation)
            captured["result"] = result
            return result

        def add_file_after_artifact(scope, value, *, context=None, owner=None, db=None):
            reference = artifact(scope, value, context=context, owner=owner, db=db)
            if not captured.get("injected") and isinstance(value, dict) and value.get("kind") == "d4-recovery-source" \
                    and getattr(owner, "source", None) is captured["observation"]:
                path = context.directory / "evidence" / ("unmanaged-" + uuid.uuid4().hex + ".json")
                body = b'{"kind":"appeared during registration"}'
                path.write_bytes(body);captured["injected"] = True
                captured.update(writer=str(context.database), orphan=reference, orphan_context=context,
                               extra_path=path, extra_body=body)
            return reference

        with patch.object(changes, "_register_recovery_release", side_effect=register_with_unmanaged_file):
            try:
                getattr(scenario, _METHOD)()
            except AssertionError:
                pass

        self.assertIn("result", captured, "release must return a failed result to the original assertion")
        self.assertTrue(captured.get("injected"), "inject only after the actual owned release projection is registered")
        self.assertFalse(captured["result"]["complete"], captured["result"])
        self.assertIn("Unaccounted", " ".join(captured["result"]["blockers"]))
        self.assertFalse(any(row["authority"] == captured["orphan_context"].authority_set[0]
                             for row in captured["result"]["replacements"]))
        writer_index = next(i for i, context in enumerate(contexts)
                            if str(context.database) == captured["writer"])
        orphan_path = captured["orphan_context"].directory / "evidence" / captured["orphan"]["name"]
        additions = {writer_index: {orphan_path.name: orphan_path.read_bytes(),
                                   captured["extra_path"].name: captured["extra_body"]}}
        _assert_unchanged(self, scenario, contexts, captured["before"], additions=additions)
        names = {row[0] for row in _metadata(captured["orphan_context"], scenario.api)}
        self.assertNotIn(captured["orphan"]["name"], names)

    def test_later_precommit_exception_leaves_unregistered_protected_orphan(self):
        scenario = _scenario(self)
        from runtime import changes, evidence

        contexts = _contexts(scenario)
        captured = {}
        release = changes._register_recovery_release
        guards = changes._recovery_release_guards
        artifact = evidence._artifact
        database = evidence._context_database

        @contextmanager
        def selected_writer_first(authorities, identity):
            with guards(authorities, identity) as (path, journal, guarded):
                by_database = {str(context.database): context for context in guarded}
                order = [contexts[0], *(context for context in guarded if context.database != contexts[0].database)]
                yield path, journal, tuple(by_database[str(context.database)] for context in order)

        def fail_after_registration(observation):
            captured["before"] = _before(scenario, contexts)
            captured["observation"] = observation

            def remember_artifact(scope, value, *, context=None, owner=None, db=None):
                reference = artifact(scope, value, context=context, owner=owner, db=db)
                if isinstance(value, dict) and value.get("kind") == "d4-recovery-source":
                    captured.update(writer=str(context.database), orphan=reference, orphan_context=context)
                return reference

            @contextmanager
            def fail_at_precommit(context, *, write=False, durable=False, before_commit=None):
                if write and durable:
                    def fail(db):
                        if before_commit is not None:
                            before_commit(db)
                        if captured.get("writer") == str(context.database):
                            raise RuntimeError("fixture: fail after registered recovery artifact")
                    with database(context, write=write, durable=durable, before_commit=fail) as db:
                        yield db
                else:
                    with database(context, write=write, durable=durable, before_commit=before_commit) as db:
                        yield db

            with patch.object(changes, "_recovery_release_guards", selected_writer_first), \
                    patch.object(evidence, "_artifact", side_effect=remember_artifact), \
                    patch.object(evidence, "_context_database", fail_at_precommit):
                return release(observation)

        with patch.object(changes, "_register_recovery_release", side_effect=fail_after_registration):
            with self.assertRaisesRegex(RuntimeError, "fail after registered recovery artifact"):
                getattr(scenario, _METHOD)()

        self.assertIn("orphan", captured, "the real artifact boundary must have been reached")
        writer_index = next(i for i, context in enumerate(contexts)
                            if str(context.database) == captured["writer"])
        orphan_path = captured["orphan_context"].directory / "evidence" / captured["orphan"]["name"]
        _assert_unchanged(self, scenario, contexts, captured["before"],
                          additions={writer_index: {orphan_path.name: orphan_path.read_bytes()}})
        names = {row[0] for row in _metadata(captured["orphan_context"], scenario.api)}
        self.assertNotIn(captured["orphan"]["name"], names)

        from runtime import state
        certificate = next(row for row in captured["observation"].content["stopped"] if row.get("linked"))
        basis = evidence._canonical_plain(certificate["linked"])
        pointer = evidence._linked_basis_root(basis)
        authorities = evidence._canonical_plain(basis["authorities"])
        with self.assertRaisesRegex(state.StateError, "Unaccounted evidence content protects preparation"):
            with evidence._linked_stop_guards(pointer, authorities) as guarded:
                evidence._linked_preparation_facts(pointer, guarded)
