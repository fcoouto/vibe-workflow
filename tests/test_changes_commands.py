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
from tests.fixture_changes import ChangesFixture, disable_inherited_d3_tests


class ChangesCommandTests(ChangesFixture, d3_tests.OnboardingTests):

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

    def test_completed_command_requires_exact_attempt_lifecycle_proof(self):
        request = self.command_request("from pathlib import Path; Path('result.txt').write_text('result')")
        result = self.changes.apply_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertFalse(result["completed"])
        result = self.changes.resume_plan(self.directory, request)
        self.assertEqual(result["exit_code"], 2, result)
        self.assertIn("lifecycle", str(result["blocked"]))
        self.assertEqual(self.changes.resume_plan(self.directory, self.observe(request))["exit_code"], 0)

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


disable_inherited_d3_tests(ChangesCommandTests)
