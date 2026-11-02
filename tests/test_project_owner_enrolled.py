"""Scope accounting uses real D4 publication and schema3 baseline provenance."""

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import evidence, review_storage, state
from runtime.startup import PROJECT_FILE, _canonical_plain
from tests import test_evidence, test_onboarding, test_review_storage
from tests import test_enrolled_accounting_integration


class ProjectOwnerEnrolledTests(unittest.TestCase):
    def umbrella(self, before_publish=None):
        fixture = test_enrolled_accounting_integration.EnrolledUmbrellaAccountingTests(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp(before_publish=before_publish)
        return fixture

    @staticmethod
    def scope(record, members=None):
        root = Path(record["project_root"])
        authority = root / PROJECT_FILE
        membership = copy.deepcopy(record["membership"])
        membership["authorization"] = membership["authorization"]["reference"]
        return dict(kind=record["kind"], project_root=str(root),
            member_checkouts=members or [entry["root"] for repository in record["repositories"]
                for entry in repository["checkouts"]], membership=membership,
            authority=dict(path=str(authority), sha256=hashlib.sha256(authority.read_bytes()).hexdigest(),
                reference="fixture:current-authority-preimage"), project_files=[])

    def resolve(self, scope, **kwargs):
        resolver = getattr(review_storage, "resolve_project_owner", None)
        self.assertTrue(callable(resolver), "Scope-aware Project accounting API is missing")
        return resolver(scope, **kwargs)

    def assert_baseline(self, result, record, baseline):
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("status"), "resolved", result)
        self.assertEqual(result.get("owner"), dict(kind="project", project_id=record["project_id"], task=None))
        self.assertEqual(result.get("reference"), baseline["reference"])
        self.assertEqual(result.get("created_at"), baseline["content"]["created_at"])
        self.assertEqual(result.get("authority"), baseline["content"]["authority"])
        self.assertEqual(result.get("age_basis"), "authority_provenance")

    def assert_protected(self, result):
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)

    def assert_history(self, result, prior):
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("status"), "resolved", result)
        history = result.get("temporary_history", {})
        self.assertIs(history.get("complete"), True, result)
        expected = [dict(owner=item["owner"], reference=item["reference"], created_at=item["created_at"])
            for item in prior]
        self.assertCountEqual(history.get("owners", []), expected)

    def test_non_git_enrolled_umbrella_acquires_each_checkout_and_original_baseline_without_writes(self):
        fixture = self.umbrella()
        before = fixture.snapshot()
        result = self.resolve(self.scope(fixture.record))
        self.assert_baseline(result, fixture.record, fixture.baseline)
        self.assertEqual(result.get("temporary_history"),
            dict(complete=True, reason="authenticated_no_related_history", owners=[],
                 handoff=result["temporary_history"]["handoff"]))
        checkouts = result.get("checkouts", [])
        self.assertEqual([entry["root"] for entry in checkouts], [str(member) for member in fixture.members])
        for member, checkout in zip(fixture.members, checkouts):
            self.assertEqual(checkout["primary_root"], str(member))
            self.assertEqual(checkout["git_dir"], str(member / ".git"))
            self.assertEqual(checkout["git_common_dir"], str(member / ".git"))
        self.assertNotEqual(checkouts[0]["binding"], checkouts[1]["binding"])
        self.assertTrue(fixture.locator.exists())
        self.assertEqual(fixture.snapshot(), before)

    def test_real_enrolled_standalone_preserves_success_fields_and_original_history(self):
        fixture = test_evidence.CanonicalEvidenceTests(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        root = fixture.fixture.root
        locator = review_storage._machine_config_path()
        record = json.loads((fixture.fixture.repo / PROJECT_FILE).read_text())
        context = evidence._evidence_context(fixture.scope)
        with evidence._context_database(context) as db:
            baseline = _canonical_plain(state._source_baseline(None, db, context=context))
        before = test_review_storage.ReviewStorageTests.tree_snapshot(root)
        with patch.object(review_storage, "_machine_config_path", return_value=locator):
            ordinary = review_storage.resolve_owner(fixture.fixture.repo)
            result = self.resolve(self.scope(record))
        self.assert_baseline(result, record, baseline)
        for key in ("owner", "reference", "created_at", "authority", "age_basis", "checkout", "temporary_history"):
            self.assertEqual(result.get(key), ordinary.get(key), key)
        self.assertEqual(result.get("temporary_history"), dict(complete=True, reason="authenticated_retained_history", owners=result["temporary_history"]["owners"],
                 handoff=result["temporary_history"]["handoff"]))
        self.assertTrue(result["temporary_history"]["owners"])
        self.assertEqual(test_review_storage.ReviewStorageTests.tree_snapshot(root), before)

    def test_enrolled_member_subset_retains_project_baseline_and_separate_selected_facts(self):
        fixture = self.umbrella()
        before = fixture.snapshot()
        for members in ([str(fixture.members[1])], [str(member) for member in reversed(fixture.members)]):
            with self.subTest(members=members):
                result = self.resolve(self.scope(fixture.record, members))
                self.assert_baseline(result, fixture.record, fixture.baseline)
                self.assertEqual([entry["root"] for entry in result.get("checkouts", [])], members)
                self.assertIs(result.get("temporary_history", {}).get("complete"), True)
                self.assertEqual(fixture.snapshot(), before)

    def test_enrolled_request_authenticates_root_kind_literal_membership_and_authority_preimage(self):
        fixture = self.umbrella()
        scope = self.scope(fixture.record)
        variants = {}
        changed = copy.deepcopy(scope)
        changed["authority"]["sha256"] = "0" * 64
        variants["wrong-preimage"] = changed
        changed = copy.deepcopy(scope)
        changed["membership"]["future_join"] = not scope["membership"]["future_join"]
        variants["changed-future-join"] = changed
        changed = copy.deepcopy(scope)
        changed["membership"]["include"] = [fixture.members[0].name]
        variants["changed-includes"] = changed
        changed = copy.deepcopy(scope)
        changed["membership"]["exclude"] = [fixture.members[1].name]
        variants["changed-excludes"] = changed
        changed = copy.deepcopy(scope)
        changed["kind"] = "standalone"
        changed["member_checkouts"] = [str(fixture.members[0])]
        variants["wrong-kind"] = changed
        changed = copy.deepcopy(scope)
        changed["project_root"] = str(fixture.members[0])
        changed["authority"] = dict(path=str(fixture.members[0] / PROJECT_FILE), sha256="absent", reference="fixture:wrong-root")
        variants["wrong-root"] = changed
        before = fixture.snapshot()
        for name, changed in variants.items():
            with self.subTest(name=name):
                self.assert_protected(self.resolve(changed))
                self.assertEqual(fixture.snapshot(), before)
        self.assertTrue(fixture.locator.exists())

    def test_enrolled_scope_with_one_authenticated_empty_machine_history_remains_read_only(self):
        fixture = self.umbrella()
        prepared = review_storage.prepare_storage(fixture.private / "private")
        self.assertTrue(prepared.get("ok"), prepared)
        before = fixture.snapshot()
        result = self.resolve(self.scope(fixture.record))
        self.assert_baseline(result, fixture.record, fixture.baseline)
        self.assertIs(result.get("temporary_history", {}).get("complete"), True)
        self.assertEqual(result["temporary_history"].get("reason"), "authenticated_no_related_history")
        self.assertEqual(result["temporary_history"].get("owners", []), [])
        self.assertEqual(fixture.snapshot(), before)

    def test_subset_retains_sibling_standalone_history_and_original_references_and_ages(self):
        prior = []

        def before_publish(fixture, record):
            prepared = review_storage.prepare_storage(fixture.private / "private")
            self.assertTrue(prepared.get("ok"), prepared)
            for repository in record["repositories"]:
                owner = review_storage.resolve_owner(repository["primary_root"])
                self.assertTrue(owner.get("ok"), owner)
                prior.append(owner)

        fixture = self.umbrella(before_publish)
        self.assertEqual(len(prior), 2)
        self.assertNotEqual(prior[0]["reference"], prior[1]["reference"])
        before = fixture.snapshot()
        result = self.resolve(self.scope(fixture.record, [str(fixture.members[0])]))
        self.assert_history(result, prior)
        self.assertEqual(fixture.snapshot(), before)

    def test_single_sibling_history_is_not_absent_when_selected_member_never_had_temporary_owner(self):
        prior = []

        def before_publish(fixture, record):
            self.assertTrue(review_storage.prepare_storage(fixture.private / "private").get("ok"))
            result = review_storage.resolve_owner(record["repositories"][1]["primary_root"])
            self.assertTrue(result.get("ok"), result)
            prior.append(result)

        fixture = self.umbrella(before_publish)
        before = fixture.snapshot()
        self.assert_history(self.resolve(self.scope(fixture.record, [str(fixture.members[0])])), prior)
        self.assertEqual(fixture.snapshot(), before)

    def test_prior_temporary_umbrella_history_survives_enrolled_subset_and_ordinary_lookup(self):
        prior = []

        def before_publish(fixture, record):
            self.assertTrue(review_storage.prepare_storage(fixture.private / "private").get("ok"))
            scope = copy.deepcopy(fixture.d4.plan()["project_scope"])
            self.assertEqual(scope["authority"]["sha256"], "absent")
            result = self.resolve(scope)
            self.assertTrue(result.get("ok"), result)
            prior.append(result)

        fixture = self.umbrella(before_publish)
        before = fixture.snapshot()
        self.assert_history(self.resolve(self.scope(fixture.record, [str(fixture.members[1])])), prior)
        self.assert_history(review_storage.resolve_owner(fixture.members[1]), prior)
        self.assertEqual(fixture.snapshot(), before)

    def test_unrelated_valid_temporary_umbrella_does_not_corrupt_enrolled_or_ordinary_lookup(self):
        fixture = self.umbrella()
        unrelated = test_onboarding.OnboardingTests(methodName="runTest")
        self.addCleanup(unrelated.doCleanups)
        unrelated.setUp()
        self.enterContext(patch.object(review_storage, "_machine_config_path", return_value=fixture.locator))
        self.assertTrue(review_storage.prepare_storage(fixture.private / "private").get("ok"))
        temporary_scope = unrelated.project_record(root=unrelated.base, members=[unrelated.repo], kind="umbrella")["project_scope"]
        temporary = self.resolve(temporary_scope)
        self.assertTrue(temporary.get("ok"), temporary)
        before = fixture.snapshot()
        unrelated_before = test_review_storage.ReviewStorageTests.tree_snapshot(unrelated.base)
        result = self.resolve(self.scope(fixture.record))
        self.assert_baseline(result, fixture.record, fixture.baseline)
        self.assertIs(result.get("temporary_history", {}).get("complete"), True)
        self.assertEqual(fixture.snapshot(), before)
        self.assertEqual(test_review_storage.ReviewStorageTests.tree_snapshot(unrelated.base), unrelated_before)
        ordinary = review_storage.resolve_owner(unrelated.repo / "README.md")
        self.assertTrue(ordinary.get("ok") or ordinary.get("status") == "protected", ordinary)
        if ordinary.get("ok"):
            for key in ("owner", "reference", "created_at"):
                self.assertEqual(ordinary.get(key), temporary.get(key))
        self.assertEqual(fixture.snapshot(), before)

    def test_mixed_enrollment_and_changed_sibling_store_never_allocate_temporary_owner(self):
        fixture = self.umbrella()
        external = test_onboarding.OnboardingTests(methodName="runTest")
        self.addCleanup(external.doCleanups)
        external.setUp()
        self.enterContext(patch.object(review_storage, "_machine_config_path", return_value=fixture.locator))
        extra_member = Path(fixture.record["project_root"]) / "third-member"
        external.repo.rename(extra_member)
        self.assertTrue(review_storage.prepare_storage(fixture.private / "private").get("ok"))
        scope = self.scope(fixture.record)
        scope["member_checkouts"].append(str(extra_member))
        before = fixture.snapshot()
        self.assert_protected(self.resolve(scope))
        self.assertEqual(fixture.snapshot(), before)
        record = copy.deepcopy(fixture.record)
        competing = fixture.private / "competing-project-store"
        record["repositories"][1]["checkouts"][0]["clients"][0]["configuration"]["runtime"]["state_dir"] = str(competing)
        fixture.authority.write_text(json.dumps(record))
        before = fixture.snapshot()
        self.assert_protected(self.resolve(self.scope(record, [str(fixture.members[0])])))
        self.assertFalse(competing.exists())
        self.assertEqual(fixture.snapshot(), before)

    def test_bounded_out_machine_history_reports_unknown_without_claiming_absence(self):
        def before_publish(fixture, record):
            self.assertTrue(review_storage.prepare_storage(fixture.private / "private").get("ok"))
            result = review_storage.resolve_owner(record["repositories"][1]["primary_root"])
            self.assertTrue(result.get("ok"), result)

        fixture = self.umbrella(before_publish)
        before = fixture.snapshot()
        with patch.object(review_storage, "MAX_RECORDS", 1):
            result = self.resolve(self.scope(fixture.record, [str(fixture.members[0])]))
        self.assert_protected(result)
        self.assertIs(result.get("temporary_history", {}).get("complete"), False, result)
        self.assertNotEqual(result["temporary_history"].get("reason"), "authenticated_no_related_history")
        self.assertEqual(fixture.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
