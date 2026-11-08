"""Claude Code runtime decisions against a Claude-born canonical scope and its real task database.

The scope is born through the actual D4 path; only its trusted-owner qualification is a synthetic
test observation, so these tests establish package behavior, not native Claude execution.
"""
import unittest
from dataclasses import replace

from runtime import state
from runtime.adapters import claude_code


class ClaudeStateTests(unittest.TestCase):
    _canonical_client = "claude-code"

    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        fixture, acquisition, _ = fresh_canonical(self)
        self.scope = acquisition.resolution.scope
        self.repo = self.scope.repository_root
        self.assertEqual((acquisition.resolution.status, self.scope.client), ("active", "claude-code"))
        self.calls = 0

    def identity(self, session="main", task="task"):
        return dict(client="claude-code", session_id=session, task_id=task, scope_ref="request:" + task)

    def start(self, session="main", task="task", mode="editable", target="WORKING"):
        identity = self.identity(session, task)
        current = state.start_task(self.scope, identity | dict(mode=mode, decision_ref="user:1"))["task"]
        if target:
            current = state.update_task(self.scope, identity | dict(
                expected_revision=current["revision"], state=target, reason="work", reference="user:1"))["task"]
        return current

    def move(self, state_name, session="main", task="task", **updates):
        current = state.task_status(self.scope, "claude-code", session)["task"]
        return state.update_task(self.scope, self.identity(session, task) | dict(
            expected_revision=current["revision"], state=state_name, reason="move", reference="user:1") | updates)["task"]

    def event(self, kind="PreToolUse", tool="Edit", session="main", prompt="prompt-1", call=None, **fields):
        self.calls += 1
        payload = dict(hook_event_name=kind, session_id=session, cwd=str(self.repo), prompt_id=prompt,
                       tool_use_id=call or f"call-{self.calls}", tool_name=tool,
                       tool_input=dict(file_path=str(self.repo / "a.txt"), old_string="a", new_string="b"))
        payload.update(fields)
        if kind == "Stop":
            for key in ("tool_name", "tool_use_id", "tool_input"):
                payload.pop(key)
        return claude_code.normalize_runtime(payload)

    def run_event(self, **kwargs):
        return state.process_event(self.scope, self.event(**kwargs))

    def generation(self):
        return state.task_status(self.scope, "claude-code", "main")["generation"]

    def test_edit_guard_allows_only_a_working_editable_bound_task(self):
        self.start()
        for tool in ("Edit", "Write", "NotebookEdit"):
            self.assertEqual(self.run_event(tool=tool).action, "pass", tool)
        self.move("REVIEW")
        for tool in ("Edit", "Write", "NotebookEdit"):
            result = self.run_event(tool=tool)
            self.assertEqual(result.action, "deny", tool)
        # Shell commands and reads stay outside the guard, as they do for Codex.
        for tool in ("Bash", "Read", "MultiEdit"):
            self.assertEqual(self.run_event(tool=tool).action, "pass", tool)
        output = claude_code.render_runtime(self.run_event())
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_read_only_task_and_pending_blocker_deny_edits(self):
        self.start(task="ro", mode="read-only")
        self.assertEqual(self.run_event().action, "deny")
        blocked = self.start(session="other", task="blocked")
        state.update_task(self.scope, self.identity("other", "blocked") | dict(
            expected_revision=blocked["revision"], state="WORKING", reason="blocker", reference="user:1",
            conditions=[dict(id="need", kind="blocker", action="patch", reason="Needs a decision", reference="user:1")]))
        self.assertEqual(self.run_event(session="other").action, "deny")

    def test_an_unbound_writer_cannot_edit_while_another_session_holds_the_checkout(self):
        self.start()
        result = self.run_event(session="stranger")
        self.assertEqual(result.action, "deny")
        self.assertIn("retains this checkout", result.reason)

    def test_post_events_advance_generation_once_per_observed_call_and_failures_count(self):
        self.start()
        before = self.generation()
        self.run_event(kind="PostToolUse", tool="Bash", call="c1", tool_response=dict(stdout="ok"))
        self.assertEqual(self.generation(), before + 1)
        self.run_event(kind="PostToolUse", tool="Bash", call="c1", tool_response=dict(stdout="ok"))
        self.assertEqual(self.generation(), before + 1)
        self.run_event(kind="PostToolUseFailure", tool="Bash", call="c2", error="Exit code 1")
        self.assertEqual(self.generation(), before + 2)

    def test_subagent_edits_inherit_the_parent_task_authority(self):
        self.start()
        self.assertEqual(self.run_event(agent_id="agent-1", agent_type="worker").action, "pass")
        self.move("REVIEW")
        self.assertEqual(self.run_event(agent_id="agent-1", agent_type="worker").action, "deny")

    def test_stop_continuation_is_bounded_to_one_per_prompt_and_renders_as_feedback(self):
        task = self.start()
        self.run_event()  # The first guarded event in a prompt establishes its turn.
        runnable = dict(id="next", kind="runnable", action="task", reason="Finish the authorized step",
                        reference="user:1", continuation=dict(turn_id="prompt-1", budget=1))
        self.move("WORKING", conditions=[runnable])
        first = self.run_event(kind="Stop", stop_hook_active=False)
        self.assertEqual((first.action, first.reason), ("continue", "Finish the authorized step"))
        self.assertEqual(claude_code.render_runtime(first), {"hookSpecificOutput": {
            "hookEventName": "Stop", "additionalContext": "Finish the authorized step"}})
        # A repeat in the same prompt, an already-continuing stop or a new prompt never continue again.
        self.assertEqual(self.run_event(kind="Stop", stop_hook_active=False).action, "pass")
        self.assertEqual(self.run_event(kind="Stop", stop_hook_active=True).action, "pass")
        self.assertEqual(self.run_event(kind="Stop", prompt="prompt-2", stop_hook_active=False).action, "pass")
        self.assertEqual(task["state"], "WORKING")

    def test_missing_stop_flag_or_prompt_identity_never_continues(self):
        self.start()
        self.run_event()
        runnable = dict(id="next", kind="runnable", action="task", reason="Finish", reference="user:1",
                        continuation=dict(turn_id="prompt-1", budget=1))
        self.move("WORKING", conditions=[runnable])
        self.assertEqual(self.run_event(kind="Stop").action, "pass")
        no_prompt = self.event(kind="Stop", stop_hook_active=False)
        self.assertEqual(state.process_event(self.scope, replace(no_prompt, turn_id=None)).action, "pass")

    def test_an_event_from_another_client_never_changes_a_claude_scope(self):
        self.start()
        before = self.generation()
        for kind, tool in (("PreToolUse", "apply_patch"), ("PostToolUse", "Bash")):
            foreign = replace(self.event(kind=kind, tool=tool), client="codex")
            result = state.process_event(self.scope, foreign)
            self.assertEqual(result.action, "context", kind)
            self.assertIn("unverified", result.reason)
        self.assertEqual(self.generation(), before)

    def cli(self, arguments, request=None, expected=0):
        import io
        import json
        from unittest.mock import patch
        from tests.fixture_canonical_birth import test_acquisition
        from scripts import workflow
        output = io.StringIO()
        raw = json.dumps(request or {}).encode()
        with patch("sys.argv", ["workflow.py", *arguments, "--cwd", str(self.repo)]), \
                patch("sys.stdin", type("In", (), {"buffer": io.BytesIO(raw)})()), patch("sys.stdout", output), \
                patch("runtime.startup.acquire_project_entry",
                      side_effect=lambda *args, **kwargs: test_acquisition(self.scope)):
            code = workflow.main()
        result = json.loads(output.getvalue())
        self.assertEqual(code, expected, result)
        return result

    def test_task_controls_start_update_and_read_status_for_claude_only(self):
        identity = self.identity()
        started = self.cli(["task", "start"], identity | dict(mode="editable", decision_ref="user:1"))
        self.assertEqual((started["task"]["client"], started["task"]["state"]), ("claude-code", "TRIAGE"))
        self.cli(["task", "update"], identity | dict(expected_revision=started["task"]["revision"],
                 state="WORKING", reason="work", reference="user:1"))
        status = self.cli(["task", "status", "--client", "claude-code", "--session", "main"])
        self.assertEqual(status["task"]["state"], "WORKING")
        # The state layer keeps evidence, delegation and retirement Codex-only; the CLI must not suggest otherwise.
        for arguments in (["task", "retire"], ["evidence", "plan"], ["delegation", "plan"]):
            result = self.cli(arguments, identity, expected=1)
            self.assertIn("supported native task client", result["error"], arguments)
        result = self.cli(["delivery", "status", "--client", "claude-code", "--session", "main"], expected=1)
        self.assertIn("Invalid task, evidence", result["error"])

    def test_managed_startup_orientation_names_the_claude_session_within_the_word_limit(self):
        from runtime import startup
        from tests.fixture_canonical_birth import test_acquisition
        start = claude_code.normalize(dict(hook_event_name="SessionStart", source="startup",
                                           session_id="claude-session-1", cwd=str(self.repo)))
        context = startup.orient(start, startup.Path(startup.__file__).resolve().parents[1],
                                 acquisition=test_acquisition(self.scope))
        self.assertIn("native Claude Code session: \"claude-session-1\"", context)
        self.assertIn(str(self.scope.runtime.state_dir / "runtime.sqlite3"), context)
        self.assertNotIn("Codex", context)
        self.assertLessEqual(len(context.split()), 150)

    def test_ordinary_update_cannot_declare_done(self):
        self.start()
        with self.assertRaises(state.StateError):
            self.move("DONE")


if __name__ == "__main__":
    unittest.main()
