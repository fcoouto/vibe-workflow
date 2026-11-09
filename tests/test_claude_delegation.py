"""Claude Code native review through the D5/D6 ledger: plan, prepare, observe, collect, assess, settle, complete.

Uses a Claude-born canonical scope with a synthetic trusted-owner qualification (ordinary admission). The spawn
chain is recorded by the real hook recorder from payloads shaped like a recorded Claude Code 2.1.311 run; the version
probe is stubbed. It establishes package behavior on a fixture scope, not native Claude execution.
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
from runtime.adapters import claude_delegation, claude_subagents as subagents

SURFACE = "claude-code-cli"
VERSION = "2.1.311"
COVERAGE = ["general", "security"]
PROMPT, TOOL, AGENT = "prompt-1", "toolu_spawn_1", "agentid0001"


class ClaudeDelegationTests(unittest.TestCase):
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
        from runtime import changes, delegation
        self.delegation = delegation
        for target in (patch.object(changes, "LOCK_ROOT", self.root / "authority-guards"),
                       patch.object(claude_delegation, "_observed_version", return_value=VERSION)):
            target.start()
            self.addCleanup(target.stop)
        self.cache = dict(client_surface=SURFACE, applicability=dict(client_version=VERSION, capability_fingerprint=self.discovered()["capability_digest"]))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    def status(self):
        return state.task_status(self.scope, "claude-code", "parent")

    def request(self, **fields):
        return self.identity | dict(expected_revision=self.status()["task"]["revision"]) | fields

    def controls(self, **dispatch):
        return dict(version=1, client_surface=SURFACE, native_version=VERSION, parent_session_id="parent",
                    dispatch_controls=dict(spawn=True, explicit_model=True) | dispatch)

    def discovered(self):
        return claude_delegation.discover_native_workers(None, self.controls())

    def evidence_plan(self):
        review = dict(applicability="required", reason="Actual change", independent=True)
        plan = dict(version=1, decision_ref="user:accepted", criteria=[dict(id="works", checks=["unit"], behavior=False)],
            checks=[dict(id="unit", argv=[sys.executable, "-c", "pass"], cwd=".", timeout=3, env={"PATH": os.defpath},
                effects=dict(kind="read-only", inspection_ref="code:inspected", network=False, production=False,
                             publish=False, migrations=False),
                baseline=dict(status="passed", reference="baseline:1"), dependencies=["app.py"])],
            reviews={"general": review, "security": review},
            security_impact=dict(status="assessed", reason="Local runtime boundary", reference="code:impact"),
            base="HEAD", comparison="endpoint", nontrivial=True, instructions=[], exclusions=[], review_limit=3)
        return self.api.plan_evidence(self.scope, self.request(plan=plan))

    def record_guidance(self):
        claim = self.delegation.claim_guidance(self.scope, self.cache | dict(trigger=dict(kind="routine", id="fixture")))
        guidance = dict(schema_version=1, client_surface=SURFACE, checked_at="2026-01-01T00:00:00Z",
            applicability=self.cache["applicability"], sources=[dict(id="official", url="https://example.org/official", sha256="b" * 64)],
            recommendations=[dict(id="review-fixture", work="Fixture native review", risk="Read-only fixture", models=[],
                conditions=["Independent review"], limitations=["Documented aliases"], source_ids=["official"])])
        self.delegation.record_guidance(self.scope, self.cache | dict(token=claim["token"], generation=claim["generation"],
            coordinator_ref="fixture:verified", guidance=guidance))

    def plan_body(self, **route):
        return dict(version=1, route=dict(mechanism="claude-code-subagent-hook", client_surface=SURFACE, parent_session_id="parent",
                authority_ref="fixture:accepted-route", controls_ref="fixture:known-controls") | route,
            authority=dict(state_dir=str(self.scope.runtime.state_dir), participants_ref="fixture:shared-owners", ownership_known=True),
            selection_constraints=dict(complexity="Fixture", risk="Bounded", rationale="Explicit fixture plan", required_controls=["spawn"]),
            limits=dict(max_attempts=3, max_resource_units=3, deadline=int(time.time()) + 600, max_parallel_workers=3,
                        host_capacity=3, capacity_ref="fixture:capacity"),
            fallback=dict(action="stop", reference="user:accepted"),
            jobs=[dict(id="review", role="review", claims=[dict(kind="path", key=str(self.repo), mode="read")],
                       dependencies=[], max_attempts=3)])

    def delegation_plan(self, **route):
        return self.delegation.plan_delegation(self.scope, self.request(plan=self.plan_body(**route)))

    def prepare(self, **updates):
        guide = self.delegation.guidance_status(self.scope, self.cache)
        observed = self.discovered()
        request = self.request(coverage=COVERAGE, criteria=["works"], job_id="review", dispatch_id="review-0", resource_units=1,
            assignment="Review read-only; inspect app.py and callers. Return the bound report.",
            selection=dict(model="sonnet", effort="default", rationale="Documented alias", profile_ids=["review-fixture"], mode="explicit"),
            control_observation=self.controls(),
            discovery_expectation={key: observed[key] for key in ("client_surface", "native_version", "parent_session_id",
                                                                   "parent_model_provider", "capability_digest")},
            guidance={key: guide[key] for key in ("snapshot_ref", "snapshot_digest", "checked_at", "freshness")}) | updates
        return self.api.prepare_review(self.scope, request)

    def ready(self):
        self.evidence_plan()
        self.record_guidance()
        self.delegation_plan()
        return self.prepare()

    def hooks(self, prepared, *, marker=None, model="sonnet", message=None, tool=TOOL, prompt=PROMPT, agent=AGENT):
        binding = self.delegation.prepared_native_review_assignment(self.scope, prepared["attempt_id"])
        report = message if message is not None else json.dumps(dict(version=1, attempt_id=prepared["attempt_id"],
            assignment_digest=prepared["assignment_digest"], candidate_digest=prepared["candidate_digest"],
            coverage=COVERAGE, findings=[], limitations=[]))
        events = [dict(hook_event_name="SubagentStart", session_id="parent", prompt_id=prompt, agent_id=agent, agent_type="general-purpose"),
                  dict(hook_event_name="PostToolUse", session_id="parent", prompt_id=prompt, tool_name="Agent", tool_use_id=tool,
                       tool_input=dict(description="Review", prompt=(marker or binding["task_name"]) + "\n" + prepared["prompt"][:200],
                                       subagent_type="general-purpose", model=model),
                       tool_response=dict(isAsync=True, status="async_launched", agentId=agent, resolvedModel="claude-sonnet-5-5")),
                  dict(hook_event_name="SubagentStop", session_id="parent", prompt_id=prompt, agent_id=agent, agent_type="general-purpose",
                       stop_hook_active=False, agent_transcript_path="/x/a.jsonl", last_assistant_message=report)]
        for event in events:
            self.assertTrue(subagents.record(self.scope, event))
        return binding

    def call(self, prepared, **updates):
        return self.request(attempt_id=prepared["attempt_id"], parent_turn_id=PROMPT, spawn_item_id=TOOL) | updates

    def assess(self, prepared):
        return self.api.assess_review(self.scope, self.request(attempt_id=prepared["attempt_id"], assessment=dict(
            coverage=COVERAGE, criteria=["works"], qualification="Qualified in runtime/security review",
            independence="Different observed native child", semantic_coverage="Inspected requirements, code and failure paths",
            dispositions=[], decision_refs=["user:accepted"])))

    def settle(self, prepared):
        with state._database(self.scope) as db:
            row = next(r for r in self.delegation._ledger(self.scope, db) if r["id"] == prepared["attempt_id"])
        data, binding = row["data"], row["data"]["binding"]
        receipt = self.api._read(self.scope, data["observation"])["receipt"]
        native = {key: binding[key] for key in ("client_surface", "native_version", "parent_session_id", "task_name")}
        native.update({key: receipt[key] for key in ("parent_turn_id", "spawn_item_id", "worker_id", "worker_turn_id", "result_item_id")})
        task = self.status()["task"]
        proof = dict(version=2, kind="assignment-writer-reconciliation", identity=self.identity.copy(), expected_pins=task["pins"],
            expected_revision=task["revision"], scope_id=state._scope_id(self.scope), attempt_id=row["id"],
            dispatch_id=row["owner"]["dispatch_id"], assignment_digest=data["assignment"]["digest"],
            claims_digest=self.api._digest(data["claims"]), candidate_digest=data["candidate"]["digest"],
            current_candidate_digest=self.api._digest(self.api.capture_candidate(self.scope,
                self.api._read(self.scope, row["body"]["review"]["d5_plan"]))), d6_plan_digest=data["plan_digest"],
            observation_digest=data["observation"]["digest"], result_digest=receipt["result_digest"], native_identity=native,
            launch_state="observed", stopped=True, descendants_stopped=True, observation_ref="fixture:subagent-stop-observed",
            coordinator_ref="fixture:independent-host-inspection")
        raw = json.dumps(proof).encode()
        path = self.root / ("review-proof-" + row["id"] + ".json")
        path.write_bytes(raw)
        path.chmod(0o400)
        return self.delegation.reconcile_assignment(self.scope, self.request(attempt_id=row["id"], host_proof=dict(
            path=str(path), sha256=hashlib.sha256(raw).hexdigest()), assessment=dict(outcome="completed", changed_paths=[],
            result_assessment="Fixture report independently checked", partial_change_assessment="No candidate or shared-resource effects",
            evidence=["fixture:subagent-stop-observed"], coordinator_ref="fixture:independent-assessment")))

    def checked(self):
        result = self.api.run_check(self.scope, self.request(check_id="unit"))
        if result["attempt"].get("process_started") and result["attempt"].get("observed_status") != "uncertain":
            attempt = result["attempt"]
            task = self.status()["task"]
            with state._database(self.scope) as db:
                row = next(r for r in self.delegation._ledger(self.scope, db) if r["id"] == attempt["id"])
            proof = dict(version=2, kind="check-writer-reconciliation", identity=self.identity.copy(), expected_pins=task["pins"],
                expected_revision=task["revision"], scope_id=state._scope_id(self.scope), attempt_id=attempt["id"],
                candidate_digest=attempt["candidate_digest"], plan_digest=attempt["plan_digest"],
                current_candidate_digest=self.api._digest(self.api.capture_candidate(self.scope, self.api._load_plan(self.scope, self.identity))),
                execution_digest=attempt.get("execution", {}).get("digest"), stopped=True, descendants_stopped=True,
                dispatch_id=row["owner"]["dispatch_id"], assignment_digest=attempt["ledger_assignment_digest"],
                claims_digest=attempt["ledger_claims_digest"], observation_ref="fixture:explicit-host-lifecycle-observation",
                coordinator_ref="fixture:independently-checked")
            raw = json.dumps(proof).encode()
            path = self.root / (attempt["id"] + "-host.json")
            path.write_bytes(raw)
            path.chmod(0o400)
            self.api.reconcile_check(self.scope, self.request(attempt_id=attempt["id"], host_proof=dict(
                path=str(path), sha256=hashlib.sha256(raw).hexdigest())))

    # -- the route ---------------------------------------------------------------------------------------------

    def test_the_claude_route_plans_prepares_observes_collects_assesses_settles_and_completes(self):
        prepared = self.ready()
        self.assertEqual(prepared["status"], "prepared", prepared)
        self.assertEqual(self.delegation.prepared_native_review_assignment(self.scope, prepared["attempt_id"])["client_surface"], SURFACE)
        self.hooks(prepared)
        observed = self.delegation.observe_assignment(self.scope, self.call(prepared))
        self.assertEqual(observed["status"], "observed", observed)
        collected = self.api.collect_review(self.scope, self.call(prepared))
        self.assertEqual(collected["attempt"]["status"], "collected")
        self.assertEqual(self.assess(prepared)["attempt"]["status"], "assessed")
        self.assertEqual(self.settle(prepared)["status"], "settled")
        self.checked()
        result = self.api.verify_completion(self.scope, self.request(purpose="completion"))
        self.assertTrue(result["verified"], result)
        self.assertEqual(self.status()["task"]["state"], "DONE")

    def test_the_observed_receipt_names_the_claude_route_and_its_unobserved_effort(self):
        prepared = self.ready()
        self.hooks(prepared)
        self.delegation.observe_assignment(self.scope, self.call(prepared))
        with state._database(self.scope) as db:
            row = next(r for r in self.delegation._ledger(self.scope, db) if r["id"] == prepared["attempt_id"])
        receipt = self.api._read(self.scope, row["data"]["observation"])["receipt"]
        self.assertEqual((receipt["mechanism"], receipt["client_surface"], receipt["originator"]),
                         (subagents.MECHANISM, SURFACE, "claude-code"))
        self.assertEqual((receipt["requested_model"], receipt["spawn_observed_model"], receipt["requested_effort"],
                          receipt["spawn_observed_effort"]), ("sonnet", "claude-sonnet-5-5", "default", None))
        self.assertEqual((receipt["worker_id"], receipt["parent_turn_id"], receipt["spawn_item_id"]), (AGENT, PROMPT, TOOL))

    # -- it fails closed ---------------------------------------------------------------------------------------

    def test_a_codex_route_is_not_accepted_for_a_claude_scope_and_the_reverse_surface_is_refused(self):
        self.evidence_plan()
        with self.assertRaisesRegex(Exception, "supported existing native route"):
            self.delegation_plan(mechanism="codex-native-subagent", client_surface="codex-cli")
        with self.assertRaisesRegex(Exception, "supported existing native route"):
            self.delegation_plan(client_surface="codex-cli")

    def test_a_control_claude_does_not_expose_is_refused_not_assumed(self):
        self.evidence_plan()
        for control in ("explicit_effort", "fork_turns_none"):
            body = self.plan_body()
            body["selection_constraints"]["required_controls"] = ["spawn", control]
            with self.subTest(control), self.assertRaisesRegex(Exception, "dispatch control is unavailable"):
                self.delegation.plan_delegation(self.scope, self.request(plan=body))

    def test_effort_and_fork_controls_cannot_be_asserted_by_the_coordinator(self):
        self.evidence_plan()
        self.record_guidance()
        self.delegation_plan()
        for extra in (dict(explicit_effort=True), dict(fork_turns_none=True)):
            with self.subTest(extra), self.assertRaisesRegex(Exception, "controls are unverified"):
                self.prepare(control_observation=self.controls(**extra))

    def test_workers_stay_unsupported_for_claude(self):
        self.evidence_plan()
        with self.assertRaisesRegex(Exception, "Unsupported task client"):
            self.delegation._prepare_native(self.scope, self.request(dispatch_id="d", job_id="j", selection={}, control_observation={},
                discovery_expectation={}, guidance={}, resource_units=1, brief={}), role="worker")

    def test_a_missing_partial_or_misbound_chain_is_not_a_review(self):
        prepared = self.ready()
        observed = self.delegation.observe_assignment(self.scope, self.call(prepared))
        self.assertEqual(observed["status"], "uncertain", observed)
        # A marker for a different assignment, a different model or a different spawn call never binds.
        for label, kwargs in (("marker", dict(marker="review_" + "0" * 32)), ("model", dict(model="opus"))):
            with self.subTest(label):
                self.hooks(prepared, **kwargs, agent="agent-" + label, tool="tool-" + label)
                observed = self.delegation.observe_assignment(self.scope, self.call(prepared, spawn_item_id="tool-" + label))
                self.assertEqual(observed["status"], "uncertain", observed)

    def test_a_report_for_another_assignment_cannot_be_collected(self):
        prepared = self.ready()
        wrong = json.dumps(dict(version=1, attempt_id=prepared["attempt_id"], assignment_digest="0" * 64,
                                candidate_digest=prepared["candidate_digest"], coverage=COVERAGE, findings=[], limitations=[]))
        self.hooks(prepared, message=wrong)
        self.delegation.observe_assignment(self.scope, self.call(prepared))
        with self.assertRaisesRegex(Exception, "unavailable|does not match"):
            self.api.collect_review(self.scope, self.call(prepared))
        self.assertNotIn("collected", self.status()["task"]["evidence"]["attempts"][-1])

    def test_a_review_cannot_complete_until_its_assignment_is_settled(self):
        prepared = self.ready()
        self.hooks(prepared)
        self.delegation.observe_assignment(self.scope, self.call(prepared))
        self.api.collect_review(self.scope, self.call(prepared))
        self.assess(prepared)
        self.checked()
        result = self.api.verify_completion(self.scope, self.request(purpose="completion"))
        self.assertFalse(result["verified"], result)

    def test_the_cli_allows_only_review_side_delegation_actions_for_claude(self):
        from scripts import workflow
        self.assertEqual(set(workflow.CLAUDE_DELEGATION), {"plan", "prepare", "observe", "reconcile", "status", "guidance"})


if __name__ == "__main__":
    unittest.main()
