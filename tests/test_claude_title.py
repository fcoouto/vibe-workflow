"""Claude Code title delivery: hook-mediated, acknowledged by the next observed `session_title`.

Uses a Claude-born canonical scope and its real task database; the Claude client itself is not involved.
"""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import state, title_sync
from runtime.adapters.claude_code import HookTitles

PREFIX = "\uFFFB"


def stored(title):
    """Claude Code stores a managed title without its leading U+FFFB (observed on 2.1.311)."""
    return title.removeprefix(PREFIX)


class ClaudeTitleTests(unittest.TestCase):
    _canonical_client = "claude-code"

    def setUp(self):
        from tests.fixture_canonical_birth import fresh_canonical
        fixture, acquisition, _ = fresh_canonical(self)
        self.scope = acquisition.resolution.scope
        self.repo = self.scope.repository_root
        home = patch.object(Path, "home", return_value=fixture.base)
        home.start()
        self.addCleanup(home.stop)
        self.identity = dict(client="claude-code", session_id="main", task_id="task", scope_ref="request:task")
        task = state.start_task(self.scope, self.identity | dict(mode="editable", decision_ref="user:1"))["task"]
        self.task = self.update("WORKING", task)

    def update(self, new_state, task=None, **extra):
        task = task or state.task_status(self.scope, "claude-code", "main")["task"]
        return state.update_task(self.scope, self.identity | dict(
            expected_revision=task["revision"], state=new_state, reason="work", reference="user:1") | extra)["task"]

    def operate(self, action="sync", observed=None, deliverable=True, **updates):
        transport = HookTitles(observed, deliverable=deliverable)
        request = dict(client="claude-code", session_id="main")
        if action in {"configure", "resume"}:
            task = state.task_status(self.scope, "claude-code", "main")["task"]
            request.update(task_id="task", scope_ref="request:task", expected_revision=task["revision"],
                           reference="user:approved")
        result = title_sync.operate(self.scope, action, request | updates, transport=lambda: transport)
        return result, transport.emit

    def presentation(self):
        return title_sync.operate(self.scope, "status", dict(client="claude-code", session_id="main"))["presentation"]

    def test_configuring_mid_session_stages_the_title_without_delivering_it(self):
        result, emitted = self.operate("configure", observed=None, deliverable=False, topic="Thread naming")
        self.assertTrue(result["pending"])
        self.assertIsNone(emitted)
        self.assertEqual(self.presentation()["pending"], "￻▰▱▱▱▱ [W1] P · Thread naming")
        self.assertEqual(self.presentation()["applied"], "")

    def test_prompt_event_delivers_the_staged_title_and_the_next_observation_acknowledges_it(self):
        self.operate("configure", deliverable=False, topic="Thread naming")
        _, emitted = self.operate(observed="")
        self.assertEqual(emitted, "▰▱▱▱▱ [W1] P · Thread naming")
        self.assertIsNotNone(self.presentation()["pending"])
        result, again = self.operate(observed=emitted)
        # Shared state keeps the canonical managed form; Claude itself stores it without the prefix.
        self.assertEqual((result["pending"], again, self.presentation()["applied"]),
                         (False, None, HookTitles.canonical(emitted)))
        # Unchanged revision and nothing pending: later prompts deliver nothing.
        self.assertEqual(self.operate(observed=emitted)[1], None)

    def test_a_status_change_stages_and_delivers_on_the_next_prompt(self):
        _, first = self.operate("configure", observed="", topic="Thread naming")
        self.operate(observed=first)
        self.update("REVIEW", user_attention=True)
        self.assertIsNone(self.operate(observed=first, deliverable=False)[1])
        _, emitted = self.operate(observed=first)
        self.assertEqual(emitted, "▱▱▱▱▱ REVIEW · [W1] P · Thread naming")

    def test_a_lost_delivery_is_repeated_until_the_title_is_observed(self):
        _, first = self.operate("configure", observed="", topic="Thread naming")
        _, repeat = self.operate(observed="")
        self.assertEqual(repeat, first)

    def test_a_manual_rename_is_preserved_pauses_once_and_resumes_only_on_request(self):
        _, first = self.operate("configure", observed="", topic="Thread naming")
        self.operate(observed=first)
        self.update("REVIEW", user_attention=True)
        result, emitted = self.operate(observed="My own name")
        self.assertEqual((result["paused"], emitted), (True, None))
        self.assertIn("Automatic title updates are paused", result["notification"])
        self.assertIsNone(self.operate(observed="My own name")[0]["notification"])
        self.update("WORKING")
        self.assertIsNone(self.operate(observed="My own name")[1])
        result, emitted = self.operate("resume", observed="My own name")
        self.assertFalse(result["paused"])
        self.assertEqual(emitted, "▰▱▱▱▱ [W1] P · Thread naming")

    def test_a_topic_only_rename_is_adopted(self):
        _, first = self.operate("configure", observed="", topic="Thread naming")
        self.operate(observed=first)
        self.update("REVIEW", user_attention=True)
        _, emitted = self.operate(observed=first.replace("Thread naming", "Better naming"))
        self.assertEqual(emitted, "▱▱▱▱▱ REVIEW · [W1] P · Better naming")

    def test_a_session_that_already_has_a_custom_title_is_never_overwritten_on_adoption(self):
        # Configuring without a live observation assumes no custom title; the first real observation corrects it.
        self.operate("configure", deliverable=False, topic="Thread naming")
        result, emitted = self.operate(observed="Auth refactor")
        self.assertEqual((result["paused"], emitted), (True, None))

    def run_hook(self, event, payload, scope=None):
        from tests.fixture_canonical_birth import test_acquisition
        from scripts import workflow
        scope = scope or self.scope
        output = io.StringIO()
        raw = json.dumps(dict(hook_event_name=event, session_id="main", cwd=str(self.repo)) | payload).encode()
        with patch("sys.argv", ["workflow.py", "claude-code", event]), \
                patch("sys.stdin", type("In", (), {"buffer": io.BytesIO(raw)})()), patch("sys.stdout", output), \
                patch("runtime.startup.acquire_project_entry", side_effect=lambda *a, **k: test_acquisition(scope)):
            self.assertEqual(workflow.main(), 0)
        return json.loads(output.getvalue()) if output.getvalue().strip() else None

    def test_user_prompt_submit_hook_emits_only_a_session_title(self):
        self.operate("configure", deliverable=False, topic="Thread naming")
        output = self.run_hook("UserPromptSubmit", dict(prompt="PRIVATE prompt"))
        self.assertEqual(output, {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                         "sessionTitle": "▰▱▱▱▱ [W1] P · Thread naming"}})
        acknowledged = self.run_hook("UserPromptSubmit", dict(
            prompt="PRIVATE", session_title="▰▱▱▱▱ [W1] P · Thread naming"))
        self.assertIsNone(acknowledged)
        self.assertEqual(self.presentation()["pending"], None)

    def test_claude_normalization_of_the_prefix_is_not_mistaken_for_a_manual_rename(self):
        # Regression for a native observation: Claude returns the delivered title without U+FFFB.
        _, emitted = self.operate("configure", observed="", topic="Thread naming")
        self.assertFalse(emitted.startswith(PREFIX))
        result, again = self.operate(observed=emitted)
        self.assertEqual((result["paused"], result["pending"], again), (False, False, None))
        self.assertEqual(HookTitles.canonical("▰▱▱▱▱ [W1] P · T"), PREFIX + "▰▱▱▱▱ [W1] P · T")
        self.assertEqual(HookTitles.canonical("Auth refactor"), "Auth refactor")
        self.assertEqual(HookTitles.canonical(""), "")

    def test_observed_title_accepts_the_managed_prefix_and_rejects_controls(self):
        from runtime.adapters.claude_code import observed_title
        self.assertEqual(observed_title({}), "")
        self.assertEqual(observed_title({"session_title": "￻▰▱▱▱▱ [W1] P · Topic"}), "￻▰▱▱▱▱ [W1] P · Topic")
        for bad in (["x"], 5, "bad\nname", "bad\x00", "x" * 4097):
            self.assertIsNone(observed_title({"session_title": bad}), repr(bad)[:20])

    def test_unmanaged_sessions_and_bad_hook_input_stay_silent(self):
        self.assertIsNone(self.run_hook("UserPromptSubmit", dict(prompt="p")))
        self.operate("configure", deliverable=False, topic="Thread naming")
        # A malformed title never blocks the prompt or surfaces a decision.
        output = self.run_hook("UserPromptSubmit", dict(prompt="p", session_title=["bad"]))
        self.assertNotIn("decision", json.dumps(output or {}))

    def test_session_start_delivers_on_startup_resume_and_fork_but_not_clear_or_compact(self):
        self.operate("configure", deliverable=False, topic="Thread naming")
        for source in ("clear", "compact"):
            output = self.run_hook("SessionStart", dict(source=source))
            self.assertNotIn("sessionTitle", json.dumps(output or {}), source)
        for source in ("startup", "resume", "fork"):
            output = self.run_hook("SessionStart", dict(source=source))
            self.assertEqual(output["hookSpecificOutput"]["sessionTitle"],
                             "▰▱▱▱▱ [W1] P · Thread naming", source)
            self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")


if __name__ == "__main__":
    unittest.main()
