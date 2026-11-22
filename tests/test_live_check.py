"""Pure parts of the dev-only live-check script; never runs OpenCode."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("live_check", ROOT / "scripts" / "live_check.py")
live_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_check)


class ScriptTest(unittest.TestCase):
    def test_runs_as_a_program_although_scripts_holds_select_py(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_check.py"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--prompt", run.stdout)


class RenameTest(unittest.TestCase):
    def test_renames_the_plugin_id_once(self):
        text = 'export default {\n  id: "vibe-workflow",\n  async setup(ctx) {}\n}\n'
        self.assertEqual(live_check.rename_plugin_id(text),
                         'export default {\n  id: "vibe-workflow-live",\n  async setup(ctx) {}\n}\n')

    def test_refuses_zero_or_several_ids(self):
        for text in ('id: "other",', 'id: "vibe-workflow", id: "vibe-workflow",'):
            with self.assertRaises(ValueError):
                live_check.rename_plugin_id(text)


class SummarizeTest(unittest.TestCase):
    def test_lists_tool_calls_with_notes_and_replies_in_order(self):
        messages = [
            {"type": "user", "text": "hi"},
            {"type": "assistant", "content": [
                {"type": "reasoning", "text": "thinking"},
                {"type": "tool", "name": "skill", "state": {"input": {"id": "using-git-worktrees"},
                 "content": [{"type": "text", "text": "body"},
                             {"type": "text", "text": "\n\nvibe-workflow: declared preference.\nmore"}]}},
                {"type": "tool", "name": "write", "state": {"input": {"path": "a.md"}, "output": "ok"}},
                {"type": "text", "text": "Done."},
            ]},
        ]
        self.assertEqual(live_check.summarize(messages), [
            {"kind": "tool", "name": "skill", "input": {"id": "using-git-worktrees"},
             "note": "vibe-workflow: declared preference."},
            {"kind": "tool", "name": "write", "input": {"path": "a.md"}, "note": None},
            {"kind": "reply", "text": "Done."},
        ])

    def test_reads_a_string_result_and_ignores_malformed_parts(self):
        messages = [{"type": "assistant", "content": [
            {"type": "tool", "name": "shell", "state": {"input": {"command": "gh pr ready 3"},
             "content": "ready\n\nvibe-workflow: before merging, check."}},
            {"type": "tool"}, {"type": "tool", "name": "read", "state": "odd"}, "junk", {"type": "text"},
        ]}, "junk"]
        self.assertEqual(live_check.summarize(messages), [
            {"kind": "tool", "name": "shell", "input": {"command": "gh pr ready 3"},
             "note": "vibe-workflow: before merging, check."},
            {"kind": "tool", "name": None, "input": {}, "note": None},
            {"kind": "tool", "name": "read", "input": {}, "note": None},
            {"kind": "reply", "text": ""},
        ])


class CleanupTest(unittest.TestCase):
    def test_a_failed_session_delete_is_reported_with_its_id(self):
        def api(method, path, data=None, timeout=60):
            if method == "delete":
                raise subprocess.CalledProcessError(1, "opencode")
            return {"id": "ses_leak"} if path == "/api/session" else []
        stderr = io.StringIO()
        with mock.patch.object(live_check, "prepare", return_value=Path("/nonexistent")), \
                mock.patch.object(live_check, "_api", side_effect=api), contextlib.redirect_stderr(stderr):
            live_check.check(["hi"])
        self.assertIn("ses_leak", stderr.getvalue())

    def test_a_create_response_without_an_id_fails_clearly(self):
        with mock.patch.object(live_check, "prepare", return_value=Path("/nonexistent")), \
                mock.patch.object(live_check, "_api", return_value={"unexpected": True}):
            with self.assertRaisesRegex(ValueError, "session id"):
                live_check.check(["hi"])


class VersionTest(unittest.TestCase):
    def test_parses_the_cli_version(self):
        self.assertEqual(live_check.parse_version("opencode v2.3.2\n"), "2.3.2")
        for text in ("v2.3.2", "", "opencode 2.3", "opencode v2.3"):
            self.assertIsNone(live_check.parse_version(text), text)

    def test_reads_a_record_and_rejects_broken_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "record.json"
            good = {"version": "2.3.2", "verified": "2026-11-22", "models": ["claude-agent/haiku"]}
            path.write_text(json.dumps(good))
            self.assertEqual(live_check.read_record(path), good)
            for text in (None, "{", json.dumps({"models": []}), json.dumps({"models": [1]}), json.dumps([])):
                if text is None:
                    path.unlink()
                else:
                    path.write_text(text)
                with self.assertRaises(ValueError, msg=text):
                    live_check.read_record(path)


SHELL_NOTE_TEXT = ("vibe-workflow: the primary checkout (/x/project) is now on vibe-injection-probe. Never touch "
                   "the primary checkout; tell the user, and do task work in a worktree.")
SHELL_ANSWER = {"reply": SHELL_NOTE_TEXT, "tool_calls": 1,
                "tools": [{"command": "git switch -c vibe-injection-probe", "output": "Switched\n" + SHELL_NOTE_TEXT}]}


class InjectionTest(unittest.TestCase):
    NOTES = live_check.note_texts((ROOT / "index.js").read_text())

    def test_extracts_both_notes_from_the_real_index(self):
        model_note, router_note = self.NOTES
        self.assertTrue(model_note.startswith("vibe-workflow: your human partner asks"))
        self.assertTrue(model_note.endswith("Otherwise leave it unset."))
        self.assertTrue(router_note.startswith("Smart Router is on:"))
        self.assertTrue(router_note.endswith("spending authority."))

    def test_passes_only_with_both_notes_and_no_tool_call(self):
        model_note, router_note = self.NOTES
        quoted = ('"NEVER set this. ' + model_note + "\n " + router_note + '"').replace('"', '\\"')
        self.assertTrue(live_check.injection_passes(quoted, 0, self.NOTES))
        self.assertFalse(live_check.injection_passes("NEVER set this. " + model_note, 0, self.NOTES))
        self.assertFalse(live_check.injection_passes(model_note + " " + router_note, 1, self.NOTES))

    def test_isolated_env_hides_the_user_config(self):
        with tempfile.TemporaryDirectory() as folder:
            env = live_check.isolated_env(Path(folder))
            self.assertEqual(env["XDG_CONFIG_HOME"], folder)
            self.assertEqual(env["HOME"], os.environ["HOME"])
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_run_model_points_opencode_at_the_clone_with_empty_stdin(self):
        seen = {}

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            seen.update(argv=argv, cwd=cwd, data=data, env=env)
            return "\n".join([json.dumps({"type": "text", "part": {"id": "p1", "type": "text", "text": "quoted"}}),
                              "not json", json.dumps({"type": "tool_use", "part": {"id": "t1", "type": "tool"}})])
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            answer = live_check.run_model(Path("/clone"), "a/one", {"PWD": "/elsewhere", "HOME": "/h"})
        self.assertEqual(seen["cwd"], Path("/clone"))
        self.assertEqual(seen["env"]["PWD"], "/clone")
        self.assertEqual(seen["env"]["HOME"], "/h")
        self.assertEqual(seen["data"], "")
        self.assertEqual((answer["reply"], answer["tool_calls"], answer["session"]), ("quoted", 1, None))

    def test_isolated_env_drops_opencode_and_git_overrides(self):
        overrides = {"OPENCODE_CONFIG": "/x.json", "OPENCODE_CONFIG_DIR": "/x", "OPENCODE_CONFIG_CONTENT": "{}",
                     "OPENCODE_SESSION_ID": "ses_parent", "GIT_DIR": "/elsewhere/.git", "GIT_CONFIG_GLOBAL": "/g"}
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, overrides):
            env = live_check.isolated_env(Path(folder))
        self.assertFalse([name for name in env if name.startswith(("OPENCODE", "GIT_"))])
        self.assertEqual(env["XDG_CONFIG_HOME"], folder)

    def test_run_model_joins_streamed_deltas_and_keeps_growing_snapshots(self):
        lines = [{"type": "text", "sessionID": "ses_x", "part": {"id": "p1", "type": "text", "text": "NEVER se"}},
                 {"type": "text", "part": {"id": "p1", "type": "text", "text": "t this"}},
                 {"type": "text", "part": {"id": "p2", "type": "text", "text": "abc"}},
                 {"type": "text", "part": {"id": "p2", "type": "text", "text": "abcdef"}}]
        calls = []

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            calls.append(argv)
            return "\n".join(json.dumps(line) for line in lines) if argv[1] == "run" else ""
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            answer = live_check.run_model(Path("/clone"), "a/one", {"HOME": "/h"})
        self.assertEqual(answer["reply"], "NEVER set this\nabcdef")

    def test_run_model_deletes_its_session(self):
        calls = []

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            calls.append((argv, env))
            if argv[1] == "run":
                return json.dumps({"type": "text", "sessionID": "ses_x", "part": {"id": "p1", "type": "text", "text": "hi"}})
            raise subprocess.CalledProcessError(1, argv)
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            answer = live_check.run_model(Path("/clone"), "a/one", {"HOME": "/h"})
        delete, env = calls[1]
        self.assertEqual(delete[:4], ["opencode", "session", "delete", "ses_x"])
        self.assertEqual(env["PWD"], "/clone")
        self.assertEqual((answer["session"], answer["deleted"]), ("ses_x", False))

    def test_run_model_reads_opencode_events_as_they_arrive(self):
        # OpenCode 2.3.2 `run --format json`: one event per line; tool results nest text that is not the reply.
        note = SHELL_ANSWER["reply"]
        lines = [{"type": "step_start", "sessionID": "ses_x", "part": {"id": "prt_1", "type": "step-start"}},
                 {"type": "tool_use", "sessionID": "ses_x", "part": {"id": "toolu_1", "type": "tool", "state": {
                     "input": {"command": "git switch -c vibe-injection-probe"}, "metadata": {"content": [{"type": "text", "text": "Switched"}, {"type": "text", "text": note}]}}}},
                 {"type": "step_finish", "sessionID": "ses_x", "part": {"id": "prt_2", "type": "step-finish"}},
                 {"type": "text", "sessionID": "ses_x", "part": {"id": "prt_3", "type": "text", "text": "Quoted."}}]

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            return "\n".join(json.dumps(line) for line in lines) if argv[1] == "run" else ""
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            answer = live_check.run_model(Path("/clone"), "a/one", {"HOME": "/h"})
        self.assertEqual((answer["reply"], answer["tool_calls"], answer["session"]), ("Quoted.", 1, "ses_x"))
        self.assertEqual(answer["tools"][0]["command"], "git switch -c vibe-injection-probe")
        self.assertIn(note, answer["tools"][0]["output"])

    def test_shell_probe_needs_the_exact_command_and_the_note_in_its_result_and_reply(self):
        self.assertTrue(live_check.shell_probe_passes(SHELL_ANSWER))
        tool = SHELL_ANSWER["tools"][0]
        for broken in ({**SHELL_ANSWER, "tools": [{**tool, "output": "Switched to a new branch"}]},
                       {**SHELL_ANSWER, "tools": [{**tool, "command": "git switch -c vibe-injection-probe && ls"}]},
                       {**SHELL_ANSWER, "tools": [tool, tool], "tool_calls": 2},
                       {**SHELL_ANSWER, "tools": [], "tool_calls": 0},
                       {**SHELL_ANSWER, "reply": "Switched to a new branch 'vibe-injection-probe'"}):
            self.assertFalse(live_check.shell_probe_passes(broken), broken)

    def test_the_prepared_clone_runs_no_git_hooks(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"GIT_DIR": "/nonexistent/.git"}):
            project = live_check.prepare(Path(folder).resolve())
            clean = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
            hooks = subprocess.run(["git", "config", "core.hooksPath"], cwd=project, capture_output=True, text=True,
                                   env=clean)
        self.assertEqual(hooks.stdout.strip(), "/dev/null")

    def test_the_cli_offers_injection(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_check.py"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertIn("--injection", run.stdout)


def host(*ids):
    """A host model list as reduce_models returns it."""
    return [{"provider": item.split("/", 1)[0], "id": item.split("/", 1)[1], "model": None, "name": None,
             "family": None, "variants": [], "context": None, "released": None} for item in ids]


RECORDED = ["claude-agent/haiku", "openai/gpt-6.1-terra", "opencode-go/claude-haiku-5-5"]


class PathTest(unittest.TestCase):
    NOTES = live_check.note_texts((ROOT / "index.js").read_text())

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = self.folder / "record.json"
        self.path.write_text(json.dumps({"models": RECORDED}))
        self.calls = []
        prepare = mock.patch.object(live_check, "prepare", return_value=self.folder / "project")
        prepare.start()
        self.addCleanup(prepare.stop)

    def runner(self, project, model, env, prompt=live_check.INJECTION_PROMPT):
        self.calls.append((model, prompt))
        if prompt == live_check.SHELL_PROMPT:
            return SHELL_ANSWER
        return {"reply": " ".join(self.NOTES), "tool_calls": 0}

    def check(self, listed=RECORDED, picker=None, runner=None):
        picker = picker or mock.Mock(side_effect=AssertionError("no pick expected"))
        return live_check.check_injection(self.path, runner=runner or self.runner, version_text="opencode v2.3.2\n",
                                          today="2026-11-22", host_models=lambda env: host(*listed), picker=picker)

    def test_paths_come_from_the_routes_config(self):
        self.assertEqual(set(live_check.PATHS), {"claude-agent", "openai", "opencode-go"})
        self.assertEqual(live_check.PATHS["claude-agent"], "claude-agent-sdk via opencode")

    def test_the_record_needs_exactly_one_model_per_subscription_path(self):
        for models in (RECORDED[:2], RECORDED + ["openrouter/paid"], RECORDED[:2] + ["claude-agent/sonnet"]):
            self.path.write_text(json.dumps({"models": models}))
            with self.assertRaisesRegex(ValueError, "one model per subscription path"):
                self.check()
            self.assertEqual(self.calls, [])

    def test_listed_models_are_kept_without_selecting_and_recorded_after_all_pass(self):
        summary = self.check()
        self.assertTrue(summary["recorded"])
        self.assertEqual([model for model, prompt in self.calls if prompt == live_check.INJECTION_PROMPT], RECORDED)
        self.assertEqual(json.loads(self.path.read_text()),
                         {"models": RECORDED, "version": "2.3.2", "verified": "2026-11-22"})

    def test_the_shell_probe_uses_the_claude_agent_path_model(self):
        self.check()
        self.assertEqual([model for model, prompt in self.calls if prompt == live_check.SHELL_PROMPT],
                         ["claude-agent/haiku"])
        features = {key for entry in live_check.INJECTION_SETTINGS["features"] for key in entry}
        self.assertEqual(features, {"smart_router", "git_workflow"})
        self.assertIn("\ngit switch -c vibe-injection-probe\n", live_check.SHELL_PROMPT)

    def test_an_unlisted_model_is_picked_once_on_its_path_and_recorded_after_all_pass(self):
        listed = ["claude-agent/haiku", "openai/gpt-6.3-terra", "opencode-go/claude-haiku-5-5"]
        picker = mock.Mock(return_value=("openai/gpt-6.3-terra", None))
        summary = self.check(listed=listed, picker=picker)
        picker.assert_called_once()
        self.assertEqual(picker.call_args.args[0], "openai")
        self.assertEqual({item["provider"] for item in picker.call_args.args[1]}, set(live_check.PATHS))
        self.assertTrue(summary["recorded"])
        self.assertEqual(json.loads(self.path.read_text())["models"], listed)
        picked = [item for item in summary["results"] if item["model"] == "openai/gpt-6.3-terra"][0]
        self.assertEqual(picked["replaced"], "openai/gpt-6.1-terra")

    def test_a_failed_or_off_path_pick_is_could_not_select_and_runs_nothing(self):
        before = self.path.read_bytes()
        for answer in ((None, "no_eligible_route"), ("claude-agent/sonnet", None), ("openai/not-listed", None)):
            self.calls.clear()
            summary = self.check(listed=["claude-agent/haiku", "opencode-go/claude-haiku-5-5"],
                                 picker=mock.Mock(return_value=answer))
            self.assertFalse(summary["recorded"])
            self.assertEqual(self.calls, [])
            self.assertEqual(self.path.read_bytes(), before)
            blocked = [item for item in summary["results"] if item.get("outcome") == "could_not_select"]
            self.assertEqual([item["path"] for item in blocked], ["openai"])

    def test_a_failing_note_or_shell_probe_blocks_the_record(self):
        before = self.path.read_bytes()
        for broken in ("notes", "shell"):
            def runner(project, model, env, prompt=live_check.INJECTION_PROMPT, broken=broken):
                answer = self.runner(project, model, env, prompt)
                if broken == "shell" and prompt == live_check.SHELL_PROMPT:
                    return {**answer, "tools": [{**answer["tools"][0], "output": "no note came back"}]}
                if broken == "notes" and model == "opencode-go/claude-haiku-5-5":
                    return {"reply": "nothing here", "tool_calls": 0}
                return answer
            summary = self.check(runner=runner)
            self.assertFalse(summary["recorded"])
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(len([item for item in summary["results"] if not item["pass"]]), 1)


class HostModelsTest(unittest.TestCase):
    def test_reduce_models_mirrors_the_plugin(self):
        raw = [{"providerID": "claude-agent", "id": "haiku", "modelID": "haiku", "name": "Haiku", "family": "claude-haiku",
                "enabled": True, "status": "active", "capabilities": {"tools": True},
                "variants": [{"id": "low"}, {"id": 7}], "limit": {"context": 1000000}, "time": {"released": 1700000000999}},
               {"providerID": "openai", "id": "off", "enabled": False},
               {"providerID": "openai", "id": "old", "enabled": True, "status": "deprecated"},
               {"providerID": "openai", "id": "notools", "enabled": True, "capabilities": {"tools": False}},
               {"providerID": "openrouter", "id": "paid", "enabled": True}]
        self.assertEqual(live_check.reduce_models({"data": raw}), [
            {"provider": "claude-agent", "id": "haiku", "model": "haiku", "name": "Haiku", "family": "claude-haiku",
             "variants": ["low"], "context": 1000000, "released": 1700000000}])

    def test_pick_asks_level_one_on_that_path_only(self):
        seen = {}

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            seen.update(argv=argv, request=json.loads(data))
            return json.dumps({"state": "selected", "route": {"provider": "openai", "model": "gpt-6.3-terra"}})
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            self.assertEqual(live_check.pick_for_path("openai", host("openai/gpt-6.3-terra")),
                             ("openai/gpt-6.3-terra", None))
        request = seen["request"]
        self.assertEqual((request["work_kind"], request["task"]), ("testing", {"files": "1-2", "spec": "exact",
                                                                              "risk": "reversible"}))
        self.assertEqual(request["constraints"], {"allowed_harnesses": [live_check.PATHS["openai"]]})
        self.assertIn(str(ROOT / "scripts" / "select.py"), seen["argv"])

        def no_pick(argv, cwd=None, timeout=120, data=None, env=None):
            return json.dumps({"state": "no_selection", "reason": "no_eligible_route", "route": None})
        with mock.patch.object(live_check, "_run", side_effect=no_pick):
            self.assertEqual(live_check.pick_for_path("openai", []), (None, "no_eligible_route"))


if __name__ == "__main__":
    unittest.main()
