"""Onboarding inventory may describe untouched files without claiming their contents."""

import copy
import hashlib
import os
from unittest.mock import patch

from runtime import changes, onboarding
from runtime.startup import StartupError
from tests.fixture_canonical_birth import OnboardingFixture, ScopedBirthFixture, CanonicalBirthFixture


class FileInventoryTests(OnboardingFixture):
    def asset(self, name="asset.bin", size=1_246_437):
        path = self.repo / name
        with path.open("wb") as stream:
            stream.truncate(size)
        return path

    def snapshot(self, plan=None):
        plan = plan or self.plan()
        return (plan["identity"] if plan["schema_version"] == 1 else
                plan["assessments"]["repositories"][0]["identity"]["snapshot"])

    def propose(self):
        plan = self.plan()
        request = self.proposal(plan["revision"]) if plan["schema_version"] == 1 else self.scoped_proposal()
        payloads = [request] if plan["schema_version"] == 1 else request["assessments"]["repositories"]
        for payload in payloads:
            payload["inventory"]["uninspected_files"] = {name:
                "Untouched fixture data; adoption updates instructions using README.md evidence only."
                for name, value in self.snapshot()["fingerprints"].items() if value["status"] == "metadata"}
        return onboarding.record_plan(self.directory, request)

    def test_large_assets_are_reviewable_without_body_reads_in_both_schemas(self):
        assets = {self.asset(), self.asset("dataset.bin", 9 * 1024 * 1024)}
        original_read = onboarding._read_file

        def bounded_read(path, *args, **kwargs):
            if path in assets:
                self.fail("Untouched large asset body was opened")
            return original_read(path, *args, **kwargs)

        with patch.object(onboarding, "_read_file", side_effect=bounded_read):
            for schema in (1, 2):
                with self.subTest(schema=schema):
                    self.directory = self.base / ("plan-" + str(schema))
                    self.directory.mkdir(mode=0o700)
                    # Retained sources must live inside the selected private plan.
                    source = self.directory / "catalog.md"
                    source.write_bytes(self.source.read_bytes())
                    source.chmod(0o600)
                    self.record["toolkit"]["files"][0]["local_path"] = str(source)
                    record = self.record if schema == 1 else self.project_record()
                    onboarding.inspect_project(self.repo, self.directory, record)
                    snapshot = self.snapshot(onboarding._load(self.directory))
                    for asset in assets:
                        observed = snapshot["fingerprints"][asset.name]
                        self.assertEqual(observed["status"], "metadata")
                        self.assertEqual(observed["metadata"]["size"], asset.stat().st_size)
                        self.assertNotIn("sha256", observed)
                        self.assertNotIn("bytes", observed)
                    self.assertEqual(snapshot["fingerprints"]["README.md"]["sha256"],
                                     hashlib.sha256(b"product\n").hexdigest())
                    report = self.propose()
                    self.assertEqual(report["status"], "ready_for_review", report)
                    self.assertTrue(any("metadata" in limit.lower() for limit in report["evidence_limits"]))

    def test_metadata_change_invalidates_review_and_application_freshness(self):
        asset = self.asset()
        self.inspect()
        self.assertEqual(self.propose()["status"], "ready_for_review")
        plan = self.plan()
        changes._fresh(plan)
        with asset.open("r+b") as stream:
            stream.write(b"changed")
        report = self.inspect()
        self.assertEqual(report["status"], "stale", report)
        with self.assertRaises(StartupError):
            changes._fresh(plan)

    def test_metadata_cannot_supply_content_evidence_or_a_target_preimage(self):
        self.asset()
        self.inspect()
        for role in ("evidence", "document", "target"):
            with self.subTest(role=role):
                request = self.proposal(self.plan()["revision"])
                if role == "evidence":
                    request["evidence"][0]["inputs"] = ["asset.bin"]
                elif role == "document":
                    request["inventory"]["documents"]["0"]["path"] = "asset.bin"
                else:
                    request["actions"][0].update(target="asset.bin", before="a" * 64)
                report = onboarding.record_plan(self.directory, request)
                self.assertNotEqual(report["status"], "ready_for_review", report)
                self.assertTrue(any(b["scope"] == "asset.bin" for b in report["blockers"]), report)

    def test_oversized_instructions_and_sources_remain_blockers(self):
        self.asset("AGENTS.md")
        self.inspect()
        report = self.propose()
        self.assertNotEqual(report["status"], "ready_for_review")
        self.assertTrue(any("instruction:" in b["scope"] for b in report["blockers"]), report)
        self.source.write_bytes(b"x" * (1024 * 1024 + 1))
        report = self.inspect()
        self.assertTrue(any(b["scope"].startswith("source:") for b in report["blockers"]), report)

    def test_secret_link_and_special_files_are_recorded_without_reading(self):
        secret = self.repo / ".env"
        secret.write_text("private fixture")
        (self.repo / "alias").symlink_to(secret)
        os.mkfifo(self.repo / "pipe")
        self.inspect()
        snapshot = self.snapshot()
        for name in (".env", "alias", "pipe"):
            # Git does not enumerate FIFOs; explicitly named inputs still remain strict.
            if name == "pipe":
                self.assertNotIn(name, snapshot["paths"])
                continue
            self.assertEqual(snapshot["fingerprints"][name]["status"], "metadata")
            self.assertNotIn("sha256", snapshot["fingerprints"][name])
        self.assertEqual(self.propose()["status"], "ready_for_review")
        self.assertEqual(onboarding._fingerprint(self.repo, "pipe")["status"], "special")
        self.assertEqual(onboarding._fingerprint(self.repo, "alias")["status"], "unreadable_or_link")

    def test_saved_metadata_is_versioned_and_cannot_masquerade_as_hashed_content(self):
        self.asset()
        self.scoped_inspect()
        plan = self.plan()
        onboarding._project_plan_shape(plan)
        for mutation in ("hash", "bytes", "version", "instruction"):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(plan)
                snapshot = self.snapshot(bad)
                entry = snapshot["fingerprints"]["asset.bin"]
                if mutation == "hash":
                    entry["sha256"] = "a" * 64
                elif mutation == "bytes":
                    entry["bytes"] = 0
                elif mutation == "version":
                    snapshot["inventory_version"] = 99
                else:
                    snapshot["fingerprints"]["instruction:" + str(self.repo / "AGENTS.md")] = entry
                with self.assertRaises(StartupError):
                    onboarding._project_plan_shape(bad)

    def test_strict_snapshot_and_edit_backup_limits_remain_unchanged(self):
        self.asset()
        strict = onboarding._snapshot(self.repo)
        self.assertEqual(strict["fingerprints"]["asset.bin"]["status"], "oversized")
        self.assertNotIn("inventory_version", strict)
        with self.assertRaises(changes.Pending):
            changes._fingerprint(self.repo, "asset.bin")
        with self.assertRaises(StartupError):
            onboarding._read_file(self.repo / "asset.bin", onboarding.MAX_FILE)

    def test_exact_content_that_becomes_opaque_stales_existing_review(self):
        asset = self.asset(size=10)
        self.inspect()
        self.assertEqual(self.propose()["status"], "ready_for_review")
        self.assertEqual(self.snapshot()["fingerprints"]["asset.bin"]["status"], "read")
        with asset.open("r+b") as stream:
            stream.truncate(2 * 1024 * 1024)
        self.assertEqual(self.inspect()["status"], "stale")

    def test_unknown_content_cannot_be_silently_omitted_from_the_assessment(self):
        self.asset("unknown-source.py")
        self.inspect()
        report = onboarding.record_plan(self.directory, self.proposal(self.plan()["revision"]))
        self.assertEqual(report["status"], "blocked", report)
        self.assertTrue(any(b["scope"] == "unknown-source.py" for b in report["blockers"]), report)

    def test_inventory_preserves_shared_byte_path_and_deadline_limits(self):
        self.asset(size=9 * 1024 * 1024)
        budget = {"paths": 20, "bytes": 8 * 1024 * 1024 - 4}
        observed = onboarding._inspection_fingerprint(self.repo, "asset.bin", budget, inventory=True)
        self.assertEqual(observed["status"], "metadata")
        self.assertEqual(budget, {"paths": 21, "bytes": 8 * 1024 * 1024 - 4})
        for exhausted in ({"paths": 4096, "bytes": 0}, {"paths": 0, "bytes": 8 * 1024 * 1024 + 1},
                          {"paths": 0, "bytes": 0, "deadline": 0}):
            with self.subTest(exhausted=exhausted), self.assertRaises(StartupError):
                onboarding._inspection_fingerprint(self.repo, "asset.bin", exhausted, inventory=True)

    def test_linked_parent_cannot_become_a_metadata_inventory_boundary(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "large.bin").write_text("not in this checkout")
        (self.repo / "linked").symlink_to(outside, target_is_directory=True)
        observed = onboarding._inventory_fingerprint(self.repo, "linked/large.bin", 1)
        self.assertEqual(observed["status"], "unreadable_or_link")
        self.assertNotIn("metadata", observed)

    def test_inventory_cannot_bypass_a_strict_capture_resource_owner(self):
        self.asset()
        with self.assertRaises(StartupError):
            onboarding._inspection_fingerprint(self.repo, "asset.bin", {"paths": 0, "bytes": 0},
                                               resources=object(), inventory=True)

    def test_aggregate_exhaustion_cannot_downgrade_a_small_file_to_metadata(self):
        self.asset(size=100_000)
        with self.assertRaises(StartupError):
            onboarding._snapshot(self.repo, inventory=True,
                                 budget={"paths": 0, "bytes": 8 * 1024 * 1024 - 50_000})

    def test_old_plan_inventory_stays_strict_on_reinspection(self):
        self.inspect()
        plan = self.plan()
        plan["identity"].pop("inventory_version")
        self.asset()
        changes_found, _, current = onboarding._current(plan)
        self.assertIn("asset.bin", changes_found)
        self.assertEqual(current["identity"]["fingerprints"]["asset.bin"]["status"], "oversized")
        self.assertNotIn("inventory_version", current["identity"])


class LargeAssetFixture(ScopedBirthFixture):
    def setUp(self):
        super().setUp()
        with (self.repo / "asset.bin").open("wb") as stream:
            stream.truncate(9 * 1024 * 1024)

    def scoped_proposal(self):
        request = super().scoped_proposal()
        for assessment in request["assessments"]["repositories"]:
            assessment["inventory"]["uninspected_files"] = {
                "asset.bin": "Untouched fixture asset; adoption changes only instructions."}
        return request


class CanonicalFileInventoryTests(CanonicalBirthFixture):
    def test_canonical_apply_and_resume_preserve_untouched_large_asset(self):
        with patch("tests.fixture_canonical_birth.ScopedBirthFixture", LargeAssetFixture):
            prepared = self.prepare()
        fixture, request, decision, directory = prepared
        asset = fixture.repo / "asset.bin"
        before = asset.stat()
        result = self.invoke(prepared)
        self.assertEqual(result["exit_code"], 2, result)
        journal = changes._load_journal(fixture.directory)
        self.assertEqual(journal["state"], "applied", result)
        self.assertEqual((fixture.repo / "AGENTS.md").read_text(), "product rules\nnew process\n")
        self.assertEqual((asset.stat().st_ino, asset.stat().st_mtime_ns, asset.stat().st_size),
                         (before.st_ino, before.st_mtime_ns, 9 * 1024 * 1024))
        from scripts.onboard_coordinator import run_verified_operation
        resume = copy.deepcopy(decision)
        resume["operation"] = "resume"
        birth = journal["birth"]
        resume["creation"]["resume"] = dict(intent_digest=birth["intent_digest"], allocation=birth["allocation"],
            baseline=birth["certificate"]["baseline"], certificate_digest=onboarding._digest(birth["certificate"]))
        result = run_verified_operation(resume, fixture.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertEqual(changes._load_journal(fixture.directory)["state"], "applied", result)
        rollback = copy.deepcopy(resume)
        rollback["operation"] = "rollback"
        result = run_verified_operation(rollback, fixture.directory, request)
        self.assertEqual(changes._load_journal(fixture.directory)["state"], "rolled_back", result)
        self.assertEqual((fixture.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
        self.assertEqual(asset.stat().st_size, 9 * 1024 * 1024)
