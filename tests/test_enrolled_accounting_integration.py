"""Real D4 umbrella publication precedes the genuine schema3 baseline producer."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from runtime import evidence, review_storage, state
from runtime.startup import PROJECT_FILE, _canonical_plain, decode_project_record
from tests import test_review_storage


class EnrolledUmbrellaAccountingTests(unittest.TestCase):
    def setUp(self, before_publish=None):
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        from runtime import changes

        birth = CanonicalBirthFixture(methodName="runTest")
        self.addCleanup(birth.doCleanups)
        prepared = birth.prepare(umbrella=True, history=False)
        self.d4, request, decision, self.storage = prepared
        self.private = self.storage.parent
        self.locator = review_storage._machine_config_path()
        record = request["manifest"]["enrollment"]["record"]
        self.authority = Path(record["project_root"]) / PROJECT_FILE
        if before_publish is not None:
            before_publish(self, record)
        # Birth witnesses the actual machine history selected by this accounting case.
        with review_storage._capture_birth_history(record) as (history, reobserve):
            reobserve()
            intent = changes._birth_intent(self.d4.plan(), request["manifest"], history,
                plan_dir=self.d4.directory)
        decision["creation"] = dict(version=1, intent=intent, resume=None)
        birth.assert_applied_configured(prepared, birth.invoke(prepared))
        self.record = json.loads(self.authority.read_text())
        context = birth.context(self.d4)
        with evidence._context_database(context) as db:
            self.baseline = _canonical_plain(state._source_baseline(None, db, context=context))
            metadata = state._metadata(db, self.baseline["reference"]["name"])
            self.assertEqual(metadata["created_at"], self.baseline["content"]["created_at"])
            self.assertEqual(metadata["owner"], dict(kind="project", project_id=self.record["project_id"], task=None))
        self.assertEqual(self.baseline["content"]["project_id"], self.record["project_id"])
        self.members = [Path(repository["checkouts"][0]["root"]) for repository in self.record["repositories"]]
        self.assertNotEqual(*self.members)

    def snapshot(self):
        return tuple(test_review_storage.ReviewStorageTests.tree_snapshot(root) for root in
            (self.d4.base, self.d4.directory.parent, self.private))

    def test_two_joined_repositories_share_original_project_accounting_without_writes(self):
        before = self.snapshot()
        results = [review_storage.resolve_owner(member) for member in self.members]
        for member, result in zip(self.members, results):
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("status"), "resolved", result)
            self.assertEqual(result.get("owner"), dict(kind="project", project_id=self.record["project_id"], task=None))
            self.assertEqual(result.get("reference"), self.baseline["reference"])
            self.assertEqual(result.get("authority"), self.baseline["content"]["authority"])
            self.assertEqual(result.get("created_at"), self.baseline["content"]["created_at"])
            self.assertEqual(result.get("age_basis"), "authority_provenance")
            self.assertEqual(result.get("temporary_history"), dict(complete=True, reason="authenticated_no_related_history",
                owners=[], handoff=result["temporary_history"]["handoff"]))
            self.assertEqual(result["checkout"]["root"], str(member))
            self.assertEqual(result["checkout"]["primary_root"], str(member))
            self.assertEqual(result["checkout"]["git_common_dir"], str(member / ".git"))
        self.assertNotEqual(results[0]["checkout"], results[1]["checkout"])
        self.assertNotEqual(results[0]["checkout"]["binding"], results[1]["checkout"]["binding"])
        self.assertTrue(self.locator.exists())
        self.assertEqual(self.snapshot(), before)

    def test_competing_configured_member_or_client_store_protects_without_allocation(self):
        for variant in ("member", "client"):
            with self.subTest(variant=variant):
                record = copy.deepcopy(self.record)
                if variant == "member":
                    entry = record["repositories"][1]["checkouts"][0]["clients"][0]
                else:
                    clients = record["repositories"][0]["checkouts"][0]["clients"]
                    entry = copy.deepcopy(clients[0])
                    entry["client"] = "claude-code"
                    clients.append(entry)
                competing = self.private / ("competing-" + variant)
                entry["configuration"]["runtime"]["state_dir"] = str(competing)
                decode_project_record(record)
                self.authority.write_text(json.dumps(record))
                before = self.snapshot()
                for member in self.members:
                    result = review_storage.resolve_owner(member)
                    self.assertFalse(result.get("ok"), result)
                    self.assertEqual(result.get("status"), "protected", result)
                self.assertFalse(competing.exists())
                self.assertTrue(self.locator.exists())
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
