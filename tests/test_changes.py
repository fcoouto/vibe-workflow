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


class ChangesTests(ChangesFixture, d3_tests.OnboardingTests):

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


disable_inherited_d3_tests(ChangesTests)
