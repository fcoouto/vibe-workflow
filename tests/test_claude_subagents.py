"""Claude Code subagent observation: recorded spawn chains, bounded and immutable; incomplete chains are no evidence.

Payload shapes mirror a recorded real run (PostToolUse(Agent) with tool_input and tool_response, SubagentStart,
SubagentStop). They establish package behavior; the real observation is recorded in the validation record.
"""
import io
import json
import os
import stat
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.adapters import claude_code, claude_subagents as subagents
from runtime.startup import StartupError

SESSION = "session-1"
AGENT = "a799a446ccca4676a"
MARKER = "review_attempt_1"
DIGESTS = dict(attempt_id="attempt-1", assignment_digest="a" * 64, candidate_digest="b" * 64,
               coverage=dict(kind="general", scope="candidate"))


def spawn(agent=AGENT, tool_use="toolu_spawn", prompt_id="prompt-1", marker=MARKER, model="haiku", subagent="general-purpose"):
    return dict(hook_event_name="PostToolUse", session_id=SESSION, prompt_id=prompt_id, tool_name="Agent",
                tool_use_id=tool_use, tool_input=dict(description="Review", prompt=marker + "\nReview the candidate.",
                                                      subagent_type=subagent, model=model),
                tool_response=dict(isAsync=True, status="async_launched", agentId=agent, resolvedModel="claude-haiku-5-5",
                                   outputFile="/x/out", canReadOutputFile=True, canContinueAgent=True))


def start(agent=AGENT, prompt_id="prompt-1", agent_type="general-purpose"):
    return dict(hook_event_name="SubagentStart", session_id=SESSION, prompt_id=prompt_id, agent_id=agent, agent_type=agent_type)


def report(**updates):
    return json.dumps(dict(version=1, findings=[], limitations=["none"], **DIGESTS) | updates)


def stop(agent=AGENT, prompt_id="prompt-1", agent_type="general-purpose", message=None):
    return dict(hook_event_name="SubagentStop", session_id=SESSION, prompt_id=prompt_id, agent_id=agent,
                agent_type=agent_type, stop_hook_active=False, agent_transcript_path="/x/agent.jsonl",
                last_assistant_message=report() if message is None else message)


class SubagentChainTests(unittest.TestCase):
    _canonical_client = "claude-code"

    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        fixture, acquisition, _ = fresh_canonical(self)
        self.scope = acquisition.resolution.scope
        self.repo = self.scope.repository_root

    def record(self, *payloads):
        for payload in payloads:
            self.assertTrue(subagents.record(self.scope, payload))

    def collect(self, **updates):
        arguments = dict(session_id=SESSION, agent_id=AGENT, marker=MARKER, native_version="2.1.311",
                         requested_model="haiku", requested_effort="default", **DIGESTS)
        return subagents.collect_review(self.scope, **(arguments | updates))

    def test_a_complete_chain_yields_the_review_receipt_and_report_contract(self):
        self.record(start(), spawn(), stop())
        result = self.collect()
        receipt = result["receipt"]
        self.assertEqual((receipt["mechanism"], receipt["task_name"], receipt["reviewer_id"], receipt["spawn_item_id"]),
                         (subagents.MECHANISM, MARKER, AGENT, "toolu_spawn"))
        self.assertEqual((receipt["requested_model"], receipt["requested_effort"], receipt["parent_turn_id"]),
                         ("haiku", "default", "prompt-1"))
        # The review receipt carries exactly the D5 contract; the observed model lives in the worker observation.
        self.assertNotIn("spawn_observed_model", receipt)
        self.assertEqual(receipt["report_digest"], receipt["report_item_id"])
        self.assertEqual(result["report"]["attempt_id"], "attempt-1")
        # The Codex collector's required receipt fields are all present.
        for field in ("version", "mechanism", "native_version", "parent_session_id", "parent_turn_id", "spawn_item_id",
                      "reviewer_id", "review_turn_id", "report_item_id", "assignment_digest", "candidate_digest",
                      "report_digest", "requested_model", "requested_effort"):
            self.assertIn(field, receipt)

    def test_a_reviewer_spawned_with_another_model_is_refused(self):
        self.record(start(), spawn(), stop())
        with self.assertRaisesRegex(StartupError, "different model"):
            self.collect(requested_model="opus")

    def test_the_spawning_agent_call_identifies_exactly_one_agent(self):
        self.record(start(), spawn(), stop())
        self.assertEqual(subagents.agent_for_spawn(self.scope, SESSION, "prompt-1", "toolu_spawn"), AGENT)
        self.assertIsNone(subagents.agent_for_spawn(self.scope, SESSION, "prompt-1", "toolu_other"))
        self.assertIsNone(subagents.agent_for_spawn(self.scope, SESSION, "prompt-2", "toolu_spawn"))
        self.assertIsNone(subagents.agent_for_spawn(self.scope, "other-session", "prompt-1", "toolu_spawn"))

    def test_event_order_does_not_matter_for_background_agents(self):
        self.record(spawn(), start(), stop())
        self.assertIsNotNone(subagents.chain(self.scope, SESSION, AGENT))

    def test_worker_receipt_uses_the_agent_id_for_claudes_missing_turn_ids(self):
        self.record(start(), spawn(marker="worker_job_1"), stop(message="done"))
        receipt = subagents.observe_worker(self.scope, session_id=SESSION, agent_id=AGENT, marker="worker_job_1",
                                           native_version="2.1.311")
        self.assertEqual((receipt["worker_id"], receipt["worker_turn_id"], receipt["turn_status"]), (AGENT, AGENT, "completed"))
        self.assertEqual(receipt["spawn_observed_model"], "claude-haiku-5-5")

    def test_partial_conflicting_or_inconsistent_chains_are_not_evidence(self):
        self.record(start(), spawn())
        self.assertIsNone(subagents.chain(self.scope, SESSION, AGENT))
        with self.assertRaisesRegex(StartupError, "absent, partial"):
            self.collect()
        self.record(stop())
        self.assertIsNotNone(subagents.chain(self.scope, SESSION, AGENT))
        # A later different spawn for the same agent conflicts and poisons the chain permanently.
        self.record(spawn(tool_use="toolu_other"))
        self.assertIsNone(subagents.chain(self.scope, SESSION, AGENT))

    def test_prompt_type_and_completeness_mismatches_are_refused(self):
        for label, payloads in (
                ("prompt", (start(prompt_id="p2"), spawn(), stop())),
                ("stop-prompt", (start(), spawn(), stop(prompt_id="p2"))),
                ("type", (start(agent_type="Explore"), spawn(), stop(agent_type="Explore"))),
                ("oversized", (start(), spawn(), stop(message="x" * (subagents.MAX_REPORT + 1))))):
            with self.subTest(label):
                other = AGENT + label
                self.record(*(dict(p, **{"agent_id": other}) if p["hook_event_name"] != "PostToolUse" else
                              dict(p, tool_response=dict(p["tool_response"], agentId=other)) for p in payloads))
                self.assertIsNone(subagents.chain(self.scope, SESSION, other))

    def test_a_missing_subagent_type_in_the_request_does_not_block_a_consistent_chain(self):
        payload = spawn()
        del payload["tool_input"]["subagent_type"]
        self.record(start(), payload, stop())
        self.assertIsNotNone(subagents.chain(self.scope, SESSION, AGENT))

    def test_the_assignment_marker_must_be_the_first_line_of_the_spawn_prompt(self):
        self.record(start(), spawn(), stop())
        for marker in ("review_other", "worker_attempt_1", "not a marker", None):
            with self.subTest(marker=marker), self.assertRaises(StartupError):
                self.collect(marker=marker)

    def test_the_report_must_match_this_assignment_exactly(self):
        for label, message in (("not-json", "All good, no findings."), ("other-attempt", report(attempt_id="x")),
                               ("other-candidate", report(candidate_digest="c" * 64)),
                               ("other-coverage", report(coverage=dict(kind="security"))),
                               ("extra-field", report(extra=1)), ("many-findings", report(findings=[1] * 257)),
                               ("bad-limitation", report(limitations=[1]))):
            with self.subTest(label):
                other = AGENT + label
                self.record(dict(start(), agent_id=other), dict(spawn(), tool_response=dict(spawn()["tool_response"], agentId=other)),
                            dict(stop(message=message), agent_id=other))
                with self.assertRaises(StartupError):
                    self.collect(agent_id=other)

    def test_fields_are_immutable_and_the_spool_is_private_and_bounded(self):
        self.record(start(), spawn(), stop())
        self.record(stop(message="a different final message"))
        self.assertIsNone(subagents.chain(self.scope, SESSION, AGENT))
        path = subagents._path(self.scope, SESSION)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for index in range(subagents.MAX_AGENTS + 3):
            subagents.record(self.scope, start(agent=f"agent-{index}"))
        data = json.loads(path.read_bytes())
        self.assertLessEqual(len(data["agents"]), subagents.MAX_AGENTS)
        self.assertTrue(data["overflow"])
        self.assertIsNone(subagents.chain(self.scope, SESSION, "agent-" + str(subagents.MAX_AGENTS + 2)))

    def test_unrelated_events_and_malformed_observations_record_nothing_or_fail_closed(self):
        self.assertFalse(subagents.record(self.scope, dict(hook_event_name="PostToolUse", tool_name="Read", session_id=SESSION)))
        self.assertFalse(os.path.lexists(subagents._path(self.scope, SESSION)))
        with self.assertRaises(StartupError):
            subagents.record(self.scope, dict(spawn(), tool_response="not an object"))
        with self.assertRaises(StartupError):
            subagents.record(self.scope, dict(stop(), last_assistant_message=5))

    def run_hook(self, event, payload):
        from tests.fixture_canonical_birth import test_acquisition
        from scripts import workflow
        output = io.StringIO()
        raw = json.dumps(dict(payload, hook_event_name=event, cwd=str(self.repo))).encode()
        with patch("sys.argv", ["workflow.py", "claude-code", event]), \
                patch("sys.stdin", type("In", (), {"buffer": io.BytesIO(raw)})()), patch("sys.stdout", output), \
                patch("runtime.startup.acquire_project_entry", side_effect=lambda *a, **k: test_acquisition(self.scope)):
            self.assertEqual(workflow.main(), 0)
        return json.loads(output.getvalue()) if output.getvalue().strip() else None

    def test_the_registered_hooks_record_the_chain_silently(self):
        self.assertIsNone(self.run_hook("SubagentStart", start()))
        secret = dict(spawn(), tool_input=dict(spawn()["tool_input"], prompt=MARKER + "\nPRIVATE_PROMPT_BODY"))
        spawned = self.run_hook("PostToolUse", secret)
        self.assertNotIn("PRIVATE_PROMPT_BODY", json.dumps(spawned or {}))
        self.assertIsNone(self.run_hook("SubagentStop", stop()))
        self.assertEqual(self.collect()["receipt"]["reviewer_id"], AGENT)
        # Only the marker line and a digest of the prompt are retained, never its body.
        self.assertNotIn(b"PRIVATE_PROMPT_BODY", subagents._path(self.scope, SESSION).read_bytes())

    def test_contention_waits_briefly_and_a_timeout_reports_a_dropped_event(self):
        import fcntl
        path = subagents._path(self.scope, SESSION)
        held = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(lambda: os.close(held) if held is not None else None)
        fcntl.flock(held, fcntl.LOCK_EX)
        with patch.object(subagents, "LOCK_TIMEOUT", 0.2):
            self.assertFalse(subagents.record(self.scope, start()))
        self.assertIsNone(subagents.chain(self.scope, SESSION, AGENT))
        fcntl.flock(held, fcntl.LOCK_UN)
        self.assertTrue(subagents.record(self.scope, start()))

    def test_parallel_writers_are_all_recorded_not_dropped(self):
        from concurrent.futures import ThreadPoolExecutor
        agents = [f"parallel-{index}" for index in range(12)]
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda agent: subagents.record(self.scope, start(agent=agent)), agents))
        self.assertTrue(all(results), results)
        data = json.loads(subagents._path(self.scope, SESSION).read_bytes())
        self.assertEqual(set(agents), set(data["agents"]))

    def test_bad_subagent_input_is_silent_and_records_nothing(self):
        for payload in (dict(start(), agent_id=5), dict(stop(), last_assistant_message=None), dict(start(), session_id="")):
            event = payload["hook_event_name"]
            self.assertIsNone(self.run_hook(event, payload))
        self.assertFalse(os.path.lexists(subagents._path(self.scope, SESSION)))


class SubagentAdapterTests(unittest.TestCase):
    def test_subagent_events_are_not_runtime_events_and_need_distinct_identity(self):
        self.assertEqual(claude_code.SUBAGENT_EVENTS, {"SubagentStart", "SubagentStop"})
        self.assertNotIn("SubagentStop", claude_code.RUNTIME_EVENTS)
        identity = claude_code.normalize_subagent(dict(start(), cwd="/tmp"))
        self.assertEqual((identity.client, identity.session_id), ("claude-code", SESSION))
        for payload in (dict(start(), cwd="relative"), dict(start(), agent_id=""), dict(start(), hook_event_name="Stop", cwd="/tmp")):
            with self.assertRaises(StartupError):
                claude_code.normalize_subagent(payload)


if __name__ == "__main__":
    unittest.main()
