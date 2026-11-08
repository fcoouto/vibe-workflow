"""D3 plans are private review artifacts, never enrollment or executable requests."""

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from runtime.startup import StartupError

PACKAGE = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe-onboarding-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "target"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "README.md").write_text("product\n")
        (self.repo / "AGENTS.md").write_text("product rules\nlegacy process\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "fixture")
        self.directory = self.base / "plan"
        self.directory.mkdir(mode=0o700)
        self.source = self.directory / "catalog.md"
        self.source.write_text("# Complete catalog\nfixture\nnew-tool\n")
        self.source.chmod(0o600)
        self.record = {"schema_version": 1, "clients": ["codex"],
            "toolkit": {"commit": "a" * 40, "origin": "https://example.invalid/toolkit",
                "acquisition": "online", "provenance": "fixture retrieval, not live evidence",
                "trust": "recorded", "files": [{"path": "catalog.md", "local_path": str(self.source),
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(), "required": True}],
                "catalog": [{"id": "fixture", "source": "catalog.md", "section": "fixture",
                    "stage": "project_onboarding", "mandatory": True, "dependencies": []},
                    {"id": "new-tool", "source": "catalog.md", "section": "new-tool",
                    "stage": "project_onboarding", "mandatory": False, "dependencies": ["fixture"]}]},
            "required_sources": ["catalog.md"]}

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo),
            *args], check=True, capture_output=True, env=env, timeout=5).stdout

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime.onboarding"),
                             "D3 onboarding helper is missing")
        from runtime import onboarding
        return onboarding

    def inspect(self):
        return self.module().inspect_project(self.repo, self.directory, self.record)

    def plan(self):
        return json.loads((self.directory / "plan.json").read_text())

    def proposal(self, revision):
        rows = []
        for item in self.plan()["requirements"]:
            rows.append({**item, "applicability": "applicable", "reason": "required assessment",
                "current_adoption": "absent", "qualification_evidence": [], "outcome": "planned",
                "actions": ["write-rules"], "scope": "checkout", "risk": "review before D4 apply",
                "verification": "D4 verification pending", "blocker": "", "resumption": "",
                "decision_ids": []})
        return {"expected_revision": revision,
            "inventory": {key: {"facts": [{"summary": "observed fixture " + key, "evidence": ["inventory"]}], "assumptions": []}
                for key in ("product", "critical_flows", "stack", "quality_commands", "services",
                            "client_capabilities", "merge_feasibility", "instructions", "integrations")}
                | {"documents": {str(i): {"path": None, "reason": "fixture missing"} for i in range(9)}},
            "decisions": [], "coverage": rows, "actions": [{"id": "write-rules", "dependencies": [],
                "kind": "write", "target": "AGENTS.md", "section": "legacy process",
                "before": hashlib.sha256((self.repo / "AGENTS.md").read_bytes()).hexdigest(),
                "text": "product rules\nnew process\n", "after": hashlib.sha256(b"product rules\nnew process\n").hexdigest(),
                "scope": "checkout", "authorization": "user:review-only",
                "verification": "D4 checks resulting sections", "preservation_exceptions": ["product rules"],
                "decision_ids": [], "backup_required": True}],
            "evidence": [{"id": "inventory", "kind": "observation", "summary": "fixture inventory observations",
                "scope": "checkout", "inputs": ["README.md"], "reference": "disposable fixture"}],
            "semantic_assessment": {"complete": True, "catalog_complete": True,
                "conflicts_checked": True, "instructions_checked": True, "source_provenance_checked": True,
                "native_evaluation": {"method": "disposable native harness", "smoke_cases": ["startup"]}}}

    def test_inspection_creates_private_plan_without_target_or_runtime_mutation(self):
        before = self.git("status", "--porcelain=v1", "-z")
        result = self.inspect()
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["revision"], 1)
        plan = self.plan()
        self.assertEqual(plan["identity"]["repository_root"], str(self.repo))
        self.assertEqual(len(plan["requirements"]), 13)
        self.assertEqual(self.git("status", "--porcelain=v1", "-z"), before)
        self.assertFalse((self.repo / ".vibe-workflow").exists())
        self.assertEqual((self.directory / "plan.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(plan["sources"]["observed"]["catalog.md"]["sha256"],
                         self.record["toolkit"]["files"][0]["sha256"])
        self.assertIn("not authenticate", " ".join(result["evidence_limits"]))

    def test_complete_exact_proposal_is_review_ready_and_digest_binds_text(self):
        report = self.inspect()
        report = self.module().record_plan(self.directory, self.proposal(report["revision"]))
        self.assertEqual(report["status"], "ready_for_review", report)
        original = report["review_digest"]
        request = self.proposal(report["revision"])
        request["actions"][0]["text"] = "changed process\n"
        request["actions"][0]["after"] = hashlib.sha256(b"changed process\n").hexdigest()
        report = self.module().record_plan(self.directory, request)
        self.assertNotEqual(original, report["review_digest"])
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_missing_new_catalog_row_blocks_full_coverage(self):
        self.inspect()
        request = self.proposal(1)
        request["coverage"] = [r for r in request["coverage"] if r["id"] != "new-tool"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any(b["scope"] == "new-tool" for b in report["blockers"]))

    def test_dirty_untracked_absence_and_ancestor_instruction_changes_invalidate(self):
        for path in ("README.md", "new.txt", "../AGENTS.md"):
            with self.subTest(path=path):
                self.inspect()
                before = self.plan()["revision"]
                request = self.proposal(before)
                request["actions"][0]["target"] = "new.txt"
                request["actions"][0]["before"] = "absent"
                (self.repo / path).write_text("changed private contents")
                report = self.module().record_plan(self.directory, request)
                self.assertEqual(report["status"], "stale", report)
                self.assertNotIn("changed private contents", json.dumps(self.plan()))
                (self.repo / path).unlink()
                if path == "README.md":
                    (self.repo / path).write_text("product\n")

    def test_source_tampering_does_not_replace_declared_hash(self):
        self.inspect()
        self.source.write_text("tampered")
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        self.assertEqual(self.plan()["sources"]["declared"], self.record)

    def test_pending_trust_offline_or_missing_source_has_explicit_gap(self):
        self.record["toolkit"].update(acquisition="offline", trust="pending")
        self.source.unlink()
        report = self.inspect()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("provenance" in b["reason"] for b in report["blockers"]))
        self.assertTrue(any(b["scope"] == "source:catalog.md" for b in report["blockers"]))
        self.assertIn("freshness", " ".join(report["evidence_limits"]))

    def test_source_scope_or_selected_checkout_cannot_be_replaced_on_resume(self):
        self.inspect()
        changed = copy.deepcopy(self.record)
        changed["toolkit"]["commit"] = "b" * 40
        with self.assertRaises(StartupError):
            self.module().inspect_project(self.repo, self.directory, changed)
        other = self.base / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        with self.assertRaises(StartupError):
            self.module().inspect_project(other, self.directory, self.record)
        self.assertEqual(self.plan()["revision"], 1)

    def test_revision_conflict_and_busy_lock_never_lose_decisions(self):
        self.inspect()
        request = self.proposal(1)
        self.module().record_plan(self.directory, request)
        before = (self.directory / "plan.json").read_bytes()
        with self.assertRaisesRegex(StartupError, "revision"):
            self.module().record_plan(self.directory, request)
        with open(self.directory / ".lock", "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(StartupError, "busy"):
                self.module().record_plan(self.directory, self.proposal(2))
        self.assertEqual((self.directory / "plan.json").read_bytes(), before)

    def test_private_no_link_and_nonrepository_boundaries(self):
        module = self.module()
        self.directory.chmod(0o755)
        with self.assertRaises(StartupError):
            self.inspect()
        self.directory.chmod(0o700)
        alias = self.base / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(StartupError):
            module.inspect_project(self.repo, alias, self.record)
        with self.assertRaises(StartupError):
            module.inspect_project(self.base, self.directory, self.record)
        inside = self.repo / "private"
        with self.assertRaises(StartupError):
            module.inspect_project(self.repo, inside, self.record)
        self.assertFalse(inside.exists())
        self.inspect()
        plan = self.directory / "plan.json"
        plan.unlink()
        plan.symlink_to(self.repo / "README.md")
        with self.assertRaises(StartupError):
            self.inspect()

    def test_duplicate_nonfinite_and_oversized_json_are_rejected(self):
        module = self.module()
        for raw in (b'{"clients":[],"clients":[]}', b'{"x":NaN}', b'[]',
                    b'{"x":"' + b'x' * module.MAX_REQUEST + b'"}'):
            with self.subTest(raw=raw[:25]):
                with self.assertRaises((StartupError, ValueError)):
                    module.read_request(raw)

    def test_action_hash_dependencies_and_paths_are_validated(self):
        self.inspect()
        for field, value in (("target", "../AGENTS.md"), ("after", "0" * 64),
                             ("dependencies", ["missing"]), ("backup_required", False)):
            with self.subTest(field=field):
                request = self.proposal(1)
                request["actions"][0][field] = value
                with self.assertRaises(StartupError):
                    self.module().record_plan(self.directory, request)
        self.assertEqual(self.plan()["revision"], 1)

    def test_equivalence_requires_behavior_evidence_bound_to_current_inputs(self):
        self.inspect()
        request = self.proposal(1)
        row = request["coverage"][0]
        row.update(outcome="reuse", current_adoption="configured", qualification_evidence=["config"], actions=[])
        request["evidence"].append({"id": "config", "kind": "configuration", "summary": "configured only",
            "scope": "checkout", "inputs": ["README.md"], "reference": "local inspection"})
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        request["expected_revision"] = report["revision"]
        row.update(current_adoption="qualified", qualification_evidence=["behavior"])
        request["evidence"][-1].update(id="behavior", kind="behavior", summary="observed fixture behavior")
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)

    def test_required_conflict_modes_and_pending_decision_preserve_independent_progress(self):
        self.inspect()
        request = self.proposal(1)
        decision = {"id": "conflict", "rule": "policy:01-project-scope", "source": "AGENTS.md",
            "scope": "checkout", "options": ["preserve", "replace", "decide"],
            "recommendation": "decide", "selected": None, "disposition": "pending",
            "provenance": "unanswered user choice", "evidence": [], "affected_actions": ["write-rules"],
            "invalidate_on": ["AGENTS.md"], "conflict": True, "mode": "decide"}
        request["decisions"] = [decision]
        request["actions"][0]["decision_ids"] = ["conflict"]
        request["coverage"][0]["decision_ids"] = ["conflict"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "awaiting_decision")
        self.assertEqual(report["next_decision"]["id"], "conflict")
        self.assertEqual(len(self.plan()["coverage"]), 13)
        decision.update(selected="preserve", disposition="confirmed", mode="preserve")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        decision.update(selected="replace", mode="replace")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        request["decisions"] = []
        request["actions"][0]["decision_ids"] = []
        request["coverage"][0]["decision_ids"] = []
        request["expected_revision"] = report["revision"]
        with self.assertRaisesRegex(StartupError, "decision"):
            self.module().record_plan(self.directory, request)

    def test_optional_omission_has_reason_and_shared_or_duplicate_actions_block_review(self):
        self.inspect()
        request = self.proposal(1)
        row = next(row for row in request["coverage"] if row["id"] == "new-tool")
        row.update(outcome="omit", applicability="inapplicable", actions=[], reason="")
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        row["reason"] = "optional integration is not needed"
        request["expected_revision"] = report["revision"]
        duplicate = copy.deepcopy(request["actions"][0])
        duplicate["id"] = "duplicate"
        request["actions"].append(duplicate)
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("overlap" in b["reason"] for b in report["blockers"]))

    def test_package_change_rechecks_pins_and_records_invalidation_history(self):
        self.inspect()
        with patch("runtime.onboarding.policy_digest", return_value="f" * 64):
            report = self.module().record_plan(self.directory, self.proposal(1))
        self.assertEqual(report["status"], "stale")
        self.assertTrue(self.plan()["invalidations"])


    def test_scoped_reconciliation_rechecks_current_bytes_and_preserves_decision_history(self):
        self.inspect()
        request = self.proposal(1)
        self.module().record_plan(self.directory, request)
        (self.repo / "README.md").write_text("changed product\n")
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        request = self.proposal(report["revision"])
        request["reconcile"] = {"input_digest": report["current_input_digest"],
                                "reason": "agent re-inspected changed product and dependent facts"}
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertTrue(self.plan()["invalidations"])
        self.assertEqual(self.plan()["identity"]["fingerprints"]["README.md"]["sha256"],
                         hashlib.sha256(b"changed product\n").hexdigest())
        (self.repo / "README.md").write_text("changed again\n")
        request["expected_revision"] = report["revision"]
        with self.assertRaisesRegex(StartupError, "reconciliation"):
            self.module().record_plan(self.directory, request)

    def test_facts_without_evidence_and_missing_native_cases_block_review(self):
        self.inspect()
        request = self.proposal(1)
        request["inventory"]["product"]["facts"][0]["evidence"] = []
        request["semantic_assessment"]["native_evaluation"]["smoke_cases"] = []
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any(b["scope"] == "inventory:product" for b in report["blockers"]))

    def test_two_cli_writers_cannot_commit_the_same_revision(self):
        import sys
        self.inspect()
        request = json.dumps(self.proposal(1)).encode()
        command = [sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
                   "onboard", "plan", "--plan-dir", str(self.directory)]
        processes = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE) for _ in range(2)]
        for process in processes:
            process.stdin.write(request)
            process.stdin.close()
            process.stdin = None
        outputs = [process.communicate(timeout=10) for process in processes]
        self.assertEqual(sorted(process.returncode for process in processes), [0, 1])
        self.assertEqual(self.plan()["revision"], 2)
        self.assertTrue(all(not stderr for _, stderr in outputs))

    def test_hardlinked_source_is_a_gap_and_plan_hardlink_is_rejected(self):
        alias = self.directory / "copy"
        os.link(self.source, alias)
        report = self.inspect()
        self.assertEqual(report["status"], "blocked")
        alias.unlink()
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        alias = self.directory / "copy-plan"
        os.link(self.directory / "plan.json", alias)
        with self.assertRaises(StartupError):
            self.inspect()



    def test_staged_blob_change_is_stale_when_status_and_worktree_are_unchanged(self):
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("first index bytes\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.inspect()
        before = self.git("status", "--porcelain=v1", "-z")
        (self.repo / "README.md").write_text("second index bytes\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.assertEqual(self.git("status", "--porcelain=v1", "-z"), before)
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        self.assertTrue(any(b["scope"] == "checkout:index_digest" for b in report["blockers"]))



    def test_unknown_source_stage_cannot_become_a_reviewable_requirement(self):
        self.record["toolkit"]["catalog"][0]["stage"] = "invented-stage"
        with self.assertRaises(StartupError):
            self.inspect()



    def test_exact_command_proposals_preserve_repeated_argv_values_without_execution(self):
        self.inspect()
        request = self.proposal(1)
        argv = ["git", "-c", "a=b", "-c", "c=d", "status"]
        request["actions"][0].update(kind="command", target="checkout baseline", section=None,
            before=None, text=argv, after=None)
        request["evidence"].append({"id": "command-scope", "kind": "authorization",
            "summary": "review-only command proposal authorized", "scope": "checkout",
            "inputs": ["README.md"], "reference": "user:review-only"})
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertEqual(self.plan()["actions"][0]["text"], argv)
        self.assertFalse((self.repo / "a=b").exists())



    def decision(self, identity, trigger, disposition="confirmed"):
        return {"id": identity, "rule": "policy:01-project-scope", "source": "README.md",
            "scope": "checkout", "options": ["keep", "change"], "recommendation": "keep",
            "selected": "keep", "disposition": disposition, "provenance": "fixture user choice",
            "evidence": ["inventory"], "affected_actions": ["write-rules"],
            "invalidate_on": [trigger], "conflict": False, "mode": None}

    def test_full_postimages_for_distinct_sections_still_own_the_same_file(self):
        self.inspect()
        request = self.proposal(1)
        second = copy.deepcopy(request["actions"][0])
        second.update(id="write-product", dependencies=["write-rules"], section="product rules",
                      text="changed product rules\nlegacy process\n")
        second["after"] = hashlib.sha256(second["text"].encode()).hexdigest()
        request["actions"].append(second)
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked", report)
        self.assertTrue(any("overlap" in b["reason"] for b in report["blockers"]))
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_ignored_proposed_directory_instructions_are_pinned_before_review(self):
        (self.repo / ".gitignore").write_text("scoped/\n")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "ignore scoped output")
        scoped = self.repo / "scoped"
        scoped.mkdir()
        rules = scoped / "AGENTS.md"
        rules.write_text("keep scoped contracts\n")
        self.inspect()
        request = self.proposal(1)
        request["actions"][0].update(target="scoped/result.md", section=None, before="absent")
        instruction_key = "instruction:" + str(rules)
        request["evidence"][0]["inputs"].append(instruction_key)
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertEqual(self.plan()["identity"]["fingerprints"][instruction_key]["sha256"],
                         hashlib.sha256(b"keep scoped contracts\n").hexdigest())
        digest = report["current_input_digest"]
        rules.write_text("changed scoped contracts\n")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "stale", report)
        self.assertNotEqual(report["current_input_digest"], digest)
        self.assertTrue(any(b["scope"] == instruction_key for b in report["blockers"]))
        self.assertFalse((scoped / "result.md").exists())

    def test_ignored_codex_override_changes_and_creation_invalidate_each_instruction_scope(self):
        (self.repo / ".gitignore").write_text("AGENTS.override.md\nscoped/\n")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "ignore overrides and scoped output")
        scoped = self.repo / "scoped" / "nested"
        scoped.mkdir(parents=True)
        source_text = self.source.read_text()
        for scope, directory in (("root", self.repo), ("ancestor", self.base),
                                 ("action", scoped), ("action-ancestor", scoped.parent)):
            for existing in (True, False):
                with self.subTest(scope=scope, existing=existing):
                    self.directory = self.base / f"plan-{scope}-{existing}"
                    self.directory.mkdir(mode=0o700)
                    self.source = self.directory / "catalog.md"
                    self.source.write_text(source_text)
                    self.source.chmod(0o600)
                    self.record["toolkit"]["files"][0]["local_path"] = str(self.source)
                    rules = directory / "AGENTS.override.md"
                    rules.unlink(missing_ok=True)
                    if existing:
                        rules.write_text("original override\n")
                    self.inspect()
                    request = self.proposal(1)
                    request["actions"][0].update(target="scoped/nested/result.md", section=None,
                                                 before="absent")
                    key = "instruction:" + str(rules)
                    report = self.module().record_plan(self.directory, request)
                    self.assertEqual(report["status"], "ready_for_review", report)
                    fingerprint = self.plan()["identity"]["fingerprints"][key]
                    self.assertEqual(fingerprint["status"], "read" if existing else "absent")
                    if existing:
                        self.assertEqual(fingerprint["sha256"], hashlib.sha256(b"original override\n").hexdigest())
                        self.assertIn(str(rules), [row["path"] for row in self.plan()["identity"]["instructions"]])
                    request["expected_revision"] = report["revision"]
                    request["decisions"] = [self.decision("override-choice", key)]
                    request["actions"][0]["decision_ids"] = ["override-choice"]
                    report = self.module().record_plan(self.directory, request)
                    self.assertEqual(report["status"], "ready_for_review", report)
                    before = report
                    git_before = self.git("status", "--porcelain=v1", "-z")
                    rules.write_text("changed override\n")
                    report = self.inspect()
                    self.assertEqual(self.git("status", "--porcelain=v1", "-z"), git_before)
                    self.assertEqual(report["status"], "stale", report)
                    self.assertNotEqual(report["current_input_digest"], before["current_input_digest"])
                    self.assertNotEqual(report["review_digest"], before["review_digest"])
                    self.assertTrue(any(row["scope"] == key for row in report["blockers"]))
                    self.assertEqual(self.plan()["decisions"][0]["disposition"], "pending")
                    self.assertEqual(self.plan()["identity"]["fingerprints"][key], fingerprint)
                    self.assertFalse((scoped / "result.md").exists())
                    self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
                    rules.unlink()

    def test_staged_metadata_evidence_invalidates_only_the_dependent_choice(self):
        (self.repo / "README.md").write_text("first staged blob\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.inspect()
        request = self.proposal(1)
        request["evidence"][0]["inputs"].append("checkout:index_digest")
        request["decisions"] = [self.decision("staged-choice", "checkout:index_digest"),
                                self.decision("stable-choice", "README.md")]
        request["actions"][0]["decision_ids"] = ["staged-choice", "stable-choice"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        dirty = self.git("status", "--porcelain=v1", "-z")
        (self.repo / "README.md").write_text("second staged blob\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.assertEqual(self.git("status", "--porcelain=v1", "-z"), dirty)
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        choices = {d["id"]: d for d in self.plan()["decisions"]}
        self.assertEqual(choices["staged-choice"]["disposition"], "pending")
        self.assertEqual(choices["stable-choice"]["disposition"], "confirmed")
        invalidation = self.plan()["invalidations"][-1]
        self.assertEqual(invalidation["decisions"], ["staged-choice"])
        self.assertEqual(invalidation["actions"], ["write-rules"])
        request["expected_revision"] = report["revision"]
        request["decisions"] = list(choices.values())
        request["reconcile"] = {"input_digest": report["current_input_digest"],
                                "reason": "re-inspected new staged content; dependent choice still pending"}
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "awaiting_decision", report)
        self.assertEqual(self.plan()["decisions"][1]["disposition"], "confirmed")

    def test_inspect_invalidation_preserves_prior_dispositions_once(self):
        self.inspect()
        request = self.proposal(1)
        request["decisions"] = [self.decision("confirmed-choice", "README.md"),
                                self.decision("inherited-choice", "README.md", "inherited")]
        self.module().record_plan(self.directory, request)
        (self.repo / "README.md").write_text("changed product\n")
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        history = self.plan()["decision_history"]
        self.assertEqual({h["decision"]["id"]: h["decision"]["disposition"] for h in history},
                         {"confirmed-choice": "confirmed", "inherited-choice": "inherited"})
        self.assertEqual(len(history), 2)
        self.inspect()
        self.assertEqual(self.plan()["decision_history"], history)
        self.assertTrue(all(d["disposition"] == "pending" for d in self.plan()["decisions"]))
        self.assertTrue(all("new run" not in b["resumption"] for b in report["blockers"]
                            if b["scope"] == "README.md"))

    def test_plan_invalidation_keeps_original_snapshot_without_duplicate_history(self):
        self.inspect()
        request = self.proposal(1)
        request["decisions"] = [self.decision("confirmed-choice", "README.md"),
                                self.decision("inherited-choice", "README.md", "inherited")]
        report = self.module().record_plan(self.directory, request)
        (self.repo / "README.md").write_text("changed product\n")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "stale")
        history = self.plan()["decision_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual([h["decision"]["disposition"] for h in history], ["confirmed", "inherited"])
        self.assertEqual([d["disposition"] for d in request["decisions"]], ["confirmed", "inherited"])

    def test_only_native_adapter_clients_are_accepted_for_project_plans(self):
        for client in ("index", "runtime", "startup", "superset"):
            with self.subTest(client=client):
                self.record["clients"] = [client]
                with self.assertRaises(StartupError):
                    self.inspect()
                self.assertFalse((self.directory / "plan.json").exists())
        self.record["clients"] = ["codex", "claude-code"]
        report = self.inspect()
        self.assertEqual(report["status"], "draft")
        self.assertEqual(self.plan()["identity"]["clients"], ["codex", "claude-code"])


if __name__ == "__main__":
    unittest.main()
