"""Claude Code runtime hook boundary: normalization, rendering and the per-client write guard."""
import json
import unittest
from pathlib import Path

from runtime import engine
from runtime.adapters import claude_code, codex
from runtime.engine import HookResult, RuntimeEvent
from runtime.startup import StartupError


class ClaudeNormalizeTests(unittest.TestCase):
    def normalize(self, **updates):
        payload = {"hook_event_name": "PreToolUse", "session_id": "session-1", "cwd": "/tmp",
                   "tool_name": "Edit", **updates}
        return claude_code.normalize_runtime(payload)

    def test_identity_comes_from_prompt_and_tool_use_ids_and_leaks_no_payload(self):
        event = self.normalize(prompt_id="prompt-1", tool_use_id="call-1", transcript_path="/private/t.jsonl",
                               tool_input={"file_path": "/tmp/a", "old_string": "PRIVATE"})
        self.assertEqual((event.kind, event.client, event.session_id), ("PreToolUse", "claude-code", "session-1"))
        self.assertEqual(event.cwd, Path("/tmp"))
        self.assertEqual((event.turn_id, event.tool_call_id, event.tool_name), ("prompt-1", "call-1", "Edit"))
        self.assertIsNone(event.transcript_path)
        self.assertIsNone(event.patch_input_fingerprint)
        self.assertNotIn("PRIVATE", repr(event))
        self.assertIsNone(self.normalize().turn_id)
        self.assertIsNone(self.normalize().tool_call_id)

    def test_receipt_fingerprint_needs_stable_identity_and_tracks_observations(self):
        first = self.normalize(prompt_id="p", tool_use_id="c", tool_input={"b": 2, "a": 1})
        reordered = self.normalize(prompt_id="p", tool_use_id="c", tool_input={"a": 1, "b": 2})
        changed = self.normalize(prompt_id="p", tool_use_id="c", tool_input={"a": 3, "b": 2})
        self.assertEqual(first.receipt_fingerprint, reordered.receipt_fingerprint)
        self.assertNotEqual(first.receipt_fingerprint, changed.receipt_fingerprint)
        self.assertIsNone(self.normalize(tool_use_id="c").receipt_fingerprint)
        self.assertIsNone(self.normalize(prompt_id="p").receipt_fingerprint)
        post = self.normalize(hook_event_name="PostToolUse", prompt_id="p", tool_use_id="c", tool_response={"ok": 1})
        failed = self.normalize(hook_event_name="PostToolUse", prompt_id="p", tool_use_id="c", tool_response={"ok": 0})
        self.assertNotEqual(post.receipt_fingerprint, failed.receipt_fingerprint)

    def test_malformed_identity_is_rejected(self):
        for field in ("session_id", "cwd", "prompt_id", "tool_use_id", "tool_name"):
            for value in (True, [], 1, "", " ", "bad\0id", "bad\nid", "x" * 4097):
                with self.subTest(field=field, value=repr(value)[:20]), self.assertRaises(StartupError):
                    self.normalize(**{field: value})
        for updates in ({"cwd": "relative"}, {"hook_event_name": "SessionStart"}, {"hook_event_name": []},
                        {"tool_name": None}):
            with self.subTest(updates=updates), self.assertRaises(StartupError):
                self.normalize(**updates)

    def test_interrupt_and_prompt_events_are_not_runtime_events(self):
        self.assertEqual(claude_code.RUNTIME_EVENTS, {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"})
        for kind in ("Interrupt", "UserPromptSubmit", "SubagentStop"):
            with self.subTest(kind=kind), self.assertRaises(StartupError):
                self.normalize(hook_event_name=kind)

    def test_a_failed_tool_invalidates_like_a_completed_one(self):
        failed = self.normalize(hook_event_name="PostToolUseFailure", tool_name="Bash", prompt_id="p",
                                tool_use_id="c", tool_input={"command": "x"}, error="Exit code 1\nPRIVATE")
        self.assertEqual((failed.kind, failed.tool_name, failed.client), ("PostToolUse", "Bash", "claude-code"))
        self.assertEqual(len(failed.receipt_fingerprint), 64)
        self.assertNotIn("PRIVATE", repr(failed))
        other = self.normalize(hook_event_name="PostToolUseFailure", tool_name="Bash", prompt_id="p",
                               tool_use_id="c", tool_input={"command": "x"}, error="Exit code 2")
        self.assertNotEqual(failed.receipt_fingerprint, other.receipt_fingerprint)
        # The tool input alone identifies a Pre event; a stray error field is ignored there.
        pre = self.normalize(prompt_id="p", tool_use_id="c", tool_input={"a": 1})
        self.assertEqual(pre.receipt_fingerprint,
                         self.normalize(prompt_id="p", tool_use_id="c", tool_input={"a": 1}, error="x").receipt_fingerprint)

    def test_stop_active_requires_boolean_and_other_events_ignore_tool_fields(self):
        self.assertTrue(self.normalize(hook_event_name="Stop", stop_hook_active=True).stop_hook_active)
        self.assertIsNone(self.normalize(hook_event_name="Stop").stop_hook_active)
        for value in (None, 0, 1, "false", []):
            with self.subTest(value=value), self.assertRaises(StartupError):
                self.normalize(hook_event_name="Stop", stop_hook_active=value)
        event = self.normalize(hook_event_name="Stop", tool_name=[], tool_use_id={},
                               last_assistant_message="PRIVATE")
        self.assertIsNone(event.tool_name)
        self.assertIsNone(event.tool_call_id)
        self.assertNotIn("PRIVATE", repr(event))

    def test_subagent_edit_hooks_report_the_child_with_the_root_session_as_parent(self):
        event = self.normalize(prompt_id="p", tool_use_id="c", agent_id="agent-9", agent_type="worker")
        self.assertEqual((event.session_id, event.hook_parent_session_id), ("agent-9", "session-1"))
        for tool in ("Write", "NotebookEdit"):
            self.assertEqual(self.normalize(tool_name=tool, agent_id="agent-9").session_id, "agent-9")
        # Non-guarded tools keep the shared session, as Codex does for everything but its patch tool.
        read = self.normalize(tool_name="Read", agent_id="agent-9")
        self.assertEqual((read.session_id, read.hook_parent_session_id), ("session-1", None))
        with self.assertRaises(StartupError):
            self.normalize(agent_id="session-1")


class WriteTargetScopeTests(unittest.TestCase):
    """A guarded write is judged where it lands, not where the session happens to be."""

    def setUp(self):
        import shutil
        import tempfile
        self.base = Path(tempfile.mkdtemp(prefix="claude-target-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.repo, self.other = self.base / "repo", self.base / "other"
        (self.repo / "src").mkdir(parents=True)
        self.other.mkdir()

    def event(self, cwd, tool="Edit", kind="PreToolUse", **tool_input):
        payload = {"hook_event_name": kind, "session_id": "s", "cwd": str(cwd), "tool_name": tool, "tool_input": tool_input}
        return claude_code.normalize_runtime(payload)

    def test_a_target_inside_the_session_cwd_keeps_the_cwd(self):
        self.assertEqual(self.event(self.repo, file_path=str(self.repo / "src/a.py")).cwd, self.repo)
        self.assertEqual(self.event(self.repo, file_path="src/new/deeper.py").cwd, self.repo)

    def test_a_target_in_another_checkout_is_decided_there(self):
        # The session sits in an unrelated directory but edits a file in the enrolled checkout.
        for kind in ("PreToolUse", "PostToolUse"):
            self.assertEqual(self.event(self.other, kind=kind, file_path=str(self.repo / "src/a.py")).cwd, self.repo / "src")
        self.assertEqual(self.event(self.other, tool="Write", file_path=str(self.repo / "src/missing/a.py")).cwd, self.repo / "src")
        self.assertEqual(self.event(self.other, tool="NotebookEdit", notebook_path=str(self.repo / "n.ipynb")).cwd, self.repo)

    def test_a_home_relative_path_is_expanded_before_the_scope_is_chosen(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, HOME=str(self.base)):
            self.assertEqual(self.event(self.other, file_path="~/repo/src/a.py").cwd, self.repo / "src")

    def test_a_symlink_cannot_hide_the_real_destination(self):
        (self.repo / "link").symlink_to(self.other)
        self.assertEqual(self.event(self.repo, file_path=str(self.repo / "link/x.py")).cwd, self.other)

    def test_relative_escapes_resolve_before_the_scope_is_chosen(self):
        self.assertEqual(self.event(self.repo / "src", file_path="../../other/x.py").cwd, self.other)

    def test_an_unreadable_path_keeps_the_cwd_and_other_tools_are_untouched(self):
        for tool_input in ({}, {"file_path": ""}, {"file_path": 5}, {"file_path": "bad\x00"}, {"file_path": "x" * 5000}, "text"):
            payload = {"hook_event_name": "PreToolUse", "session_id": "s", "cwd": str(self.other), "tool_name": "Edit",
                       "tool_input": tool_input}
            self.assertEqual(claude_code.normalize_runtime(payload).cwd, self.other, tool_input)
        bash = self.event(self.other, tool="Bash", file_path=str(self.repo / "a.py"))
        self.assertEqual(bash.cwd, self.other)


class ClaudeRenderTests(unittest.TestCase):
    def result(self, kind, action, reason="safe reason"):
        return claude_code.render_runtime(HookResult(kind, action, reason))

    def test_pass_grants_nothing_and_stop_returns_json(self):
        for kind in ("PreToolUse", "PostToolUse", "Stop"):
            self.assertEqual(self.result(kind, "pass"), {} if kind == "Stop" else None)

    def test_only_documented_claude_output_schemas_are_emitted(self):
        self.assertEqual(self.result("PreToolUse", "deny"), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "safe reason"}})
        self.assertEqual(self.result("PostToolUse", "context"), {"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": "safe reason"}})
        # Designed continuation is non-error feedback; informational Stop output is a user message.
        self.assertEqual(self.result("Stop", "continue"), {"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": "safe reason"}})
        self.assertEqual(self.result("Stop", "context"), {"systemMessage": "safe reason"})

    def test_failure_context_names_the_native_event(self):
        output = claude_code.render_runtime(HookResult("PostToolUse", "context", "safe reason"),
                                            native_event="PostToolUseFailure")
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")

    def test_sanitizes_and_bounds_reason_and_rejects_invalid_combinations(self):
        reason = self.result("PreToolUse", "deny", "safe\nreason\0\x1b" + "x" * 2000)[
            "hookSpecificOutput"]["permissionDecisionReason"]
        self.assertTrue(len(reason) <= 1024 and not set(reason) & {"\n", "\0", "\x1b"})
        for kind, action in (("Interrupt", "pass"), ("PostToolUse", "deny"), ("PreToolUse", "continue"),
                             ("Stop", "deny"), ("SessionStart", "context"), ("Stop", "unknown"), ([], "pass")):
            with self.subTest(kind=kind, action=action), self.assertRaises(StartupError):
                self.result(kind, action)
        for reason in (None, [], "", "\0"):
            with self.subTest(reason=reason), self.assertRaises(StartupError):
                self.result("PreToolUse", "deny", reason)

    def test_failure_is_a_constant_system_message_never_a_decision(self):
        output = claude_code.runtime_diagnostic("PreToolUse", "invalid input")
        self.assertEqual(set(output), {"systemMessage"})
        self.assertIn("PreToolUse", output["systemMessage"])
        self.assertNotIn("permissionDecision", json.dumps(output))


class ClientGuardTests(unittest.TestCase):
    task = dict(state="REVIEW", mode="editable", conditions=[], revision=1, turn_id=None)

    def event(self, client, tool, kind="PreToolUse"):
        return RuntimeEvent(kind, client, "s", Path("/tmp"), "turn", "call", tool, None, "receipt")

    def test_each_client_guards_only_its_own_edit_tools(self):
        for tool in ("Edit", "Write", "NotebookEdit"):
            self.assertTrue(engine.guarded_mutation(self.event("claude-code", tool)))
            self.assertFalse(engine.guarded_mutation(self.event("codex", tool)))
        self.assertTrue(engine.guarded_mutation(self.event("codex", "apply_patch")))
        self.assertFalse(engine.guarded_mutation(self.event("claude-code", "apply_patch")))
        for tool in ("Bash", "Read", "MultiEdit"):
            self.assertFalse(engine.guarded_mutation(self.event("claude-code", tool)))
        self.assertFalse(engine.guarded_mutation(self.event("claude-code", "Edit", "PostToolUse")))

    def test_evaluate_denies_a_blocked_claude_edit_and_passes_unguarded_tools(self):
        snapshot = dict(task=dict(self.task, task_id="t"), turn=None, receipt=None)
        denied, _ = engine.evaluate(self.event("claude-code", "Edit"), snapshot)
        self.assertEqual(denied.action, "deny")
        passed, _ = engine.evaluate(self.event("claude-code", "Bash"), snapshot)
        self.assertEqual(passed.action, "pass")
        wrong_client, _ = engine.evaluate(self.event("codex", "Edit"), snapshot)
        self.assertEqual(wrong_client.action, "pass")

    def test_codex_behavior_is_unchanged(self):
        snapshot = dict(task=dict(self.task, task_id="t"), turn=None, receipt=None)
        self.assertEqual(engine.evaluate(self.event("codex", "apply_patch"), snapshot)[0].action, "deny")
        self.assertEqual(codex.RUNTIME_EVENTS, {"PreToolUse", "PostToolUse", "Stop", "Interrupt"})


if __name__ == "__main__":
    unittest.main()
