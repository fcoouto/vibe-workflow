"""Claude Code evidence: review-free completion works and revalidates; review stays unsatisfiable until N1.

Uses a Claude-born canonical scope with a synthetic trusted-owner qualification (ordinary admission) and a
synthetic host stop proof for the started check, exactly as the Codex evidence tests do. It establishes package
behavior, not native Claude execution.
"""
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from runtime import state


class ClaudeEvidenceTests(unittest.TestCase):
    _canonical_client = "claude-code"

    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        fixture, acquisition, _ = fresh_canonical(self)
        self.root, self.repo, self.scope = fixture.base, fixture.repo, acquisition.resolution.scope
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.repo / "app.py").write_text("value = 1\n")
        self.git("add", "app.py")
        self.git("commit", "-qm", "fixture application")
        self.identity = dict(client="claude-code", session_id="parent", task_id="task", scope_ref="user:task")
        state.start_task(self.scope, self.identity | dict(mode="editable", decision_ref="user:accepted"))
        self.api = importlib.import_module("runtime.evidence")
        from runtime import changes
        guard = patch.object(changes, "LOCK_ROOT", self.root / "authority-guards")
        guard.start()
        self.addCleanup(guard.stop)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def status(self):
        return state.task_status(self.scope, "claude-code", "parent")

    def request(self, **fields):
        return self.identity | dict(expected_revision=self.status()["task"]["revision"]) | fields

    def plan(self, review=False, **updates):
        applicability = dict(applicability="required", reason="Actual change", independent=True) if review else dict(
            applicability="inapplicable", reason="Trivial model-free fixture", independent=False)
        plan = dict(version=1, decision_ref="user:accepted", criteria=[dict(id="works", checks=["unit"], behavior=False)],
            checks=[dict(id="unit", argv=[sys.executable, "-c", "pass"], cwd=".", timeout=3, env={"PATH": os.defpath},
                effects=dict(kind="read-only", inspection_ref="code:inspected", network=False, production=False,
                             publish=False, migrations=False),
                baseline=dict(status="passed", reference="baseline:1"), dependencies=["app.py"])],
            reviews={"general": applicability, "security": applicability},
            security_impact=dict(status="assessed", reason="Local runtime boundary", reference="code:impact"),
            base="HEAD", comparison="endpoint", nontrivial=review, instructions=[], exclusions=[], review_limit=3)
        return self.api.plan_evidence(self.scope, self.request(plan=plan | updates))

    def reconcile(self, result):
        attempt = result["attempt"]
        task = self.status()["task"]
        plan = self.api._load_plan(self.scope, self.identity)
        proof = dict(version=1, kind="check-writer-reconciliation", identity=self.identity.copy(), expected_pins=task["pins"],
            expected_revision=task["revision"], scope_id=state._scope_id(self.scope), attempt_id=attempt["id"],
            candidate_digest=attempt["candidate_digest"],
            current_candidate_digest=self.api._digest(self.api.capture_candidate(self.scope, plan)),
            plan_digest=attempt["plan_digest"], execution_digest=attempt.get("execution", {}).get("digest"),
            stopped=True, descendants_stopped=True, observation_ref="fixture:explicit-host-lifecycle-observation",
            coordinator_ref="fixture:independently-checked")
        if "ledger_assignment_digest" in attempt:
            from runtime import delegation
            with state._database(self.scope) as db:
                row = next(r for r in delegation._ledger(self.scope, db) if r["id"] == attempt["id"])
            proof = proof | dict(version=2, dispatch_id=row["owner"]["dispatch_id"],
                assignment_digest=attempt["ledger_assignment_digest"], claims_digest=attempt["ledger_claims_digest"])
        raw = json.dumps(proof).encode()
        path = self.root / (attempt["id"] + "-host-" + str(time.time_ns()) + ".json")
        path.write_bytes(raw)
        path.chmod(0o400)
        return self.api.reconcile_check(self.scope, self.request(attempt_id=attempt["id"], host_proof=dict(
            path=str(path), sha256=hashlib.sha256(raw).hexdigest())))

    def checked(self):
        result = self.api.run_check(self.scope, self.request(check_id="unit"))
        if result["attempt"].get("process_started") and result["attempt"].get("observed_status") != "uncertain":
            result = self.reconcile(result)
        return result

    def verify(self):
        return self.api.verify_completion(self.scope, self.request(purpose="completion"))

    def test_a_review_free_plan_completes_and_status_reports_done(self):
        # Completion reads shared history that holds Claude check rows, so it needs the `delegation._ledger`
        # identity relaxation; without it the history reads as corrupt.
        self.plan()
        self.checked()
        result = self.verify()
        self.assertTrue(result["verified"], result)
        self.assertEqual(self.status()["task"]["state"], "DONE")
        self.assertEqual(self.api.evidence_status(self.scope, "claude-code", "parent")["task"]["state"], "DONE")
        self.assertNotIn("delegation", self.status()["task"])

    def test_done_is_revalidated_against_the_actual_candidate(self):
        self.plan()
        self.checked()
        self.assertTrue(self.verify()["verified"])
        (self.repo / "app.py").write_text("value = 2\n")
        self.assertNotEqual(self.status()["task"]["state"], "DONE")

    def test_a_plan_that_requires_review_cannot_complete_without_an_observed_review(self):
        self.plan(review=True)
        self.checked()
        result = self.verify()
        self.assertFalse(result["verified"])
        self.assertNotEqual(self.status()["task"]["state"], "DONE")
        self.assertTrue(any("review" in str(blocker).lower() for blocker in result["blockers"]), result["blockers"])

    def test_workers_and_linked_plans_refuse_for_claude_and_review_has_its_own_tests(self):
        # Native review for Claude lives in test_claude_delegation; workers and linked plans stay Codex-only.
        from runtime import delegation
        self.plan(review=True)
        brief = dict(dispatch_id="d", job_id="j", selection={}, control_observation={}, discovery_expectation={},
                     guidance={}, resource_units=1, brief={})
        with self.assertRaisesRegex(Exception, "Unsupported task client"):
            delegation._prepare_native(self.scope, self.request(**brief), role="worker")
        with patch.object(self.api, "_plan", return_value=dict(version=2)), \
                self.assertRaisesRegex(Exception, "Codex client"):
            self.api.plan_evidence(self.scope, self.request(plan={}))

    def test_delivery_operations_refuse_a_claude_request_at_the_library_boundary(self):
        from runtime import delivery
        self.plan()
        for action, extra in (("plan", dict(plan={})), ("ready", dict(operation_id="o", publication_id="p")),
                              ("reconcile", dict(operation_id="o", publication_id="p")), ("bind-worktree", dict(operation_id="o"))):
            with self.subTest(action), self.assertRaisesRegex(Exception, "Unsupported task client"):
                delivery.operate(self.scope, action, self.request(**extra))

    def test_a_codex_request_cannot_touch_a_claude_scope(self):
        # A valid plan, so the refusal can only come from the client binding.
        valid = self.plan()["task"]["evidence"]
        self.assertIsNotNone(valid)
        request = dict(self.request(plan=self.api._load_plan(self.scope, self.identity)), client="codex")
        with self.assertRaisesRegex(Exception, "binding changed"):
            self.api.plan_evidence(self.scope, request)


if __name__ == "__main__":
    unittest.main()
