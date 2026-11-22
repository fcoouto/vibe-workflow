"""Pure parts of the dev-only live-check script; never runs OpenCode."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
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

    def test_records_only_when_every_model_passes(self):
        model_note, router_note = self.NOTES
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "record.json"
            path.write_text(json.dumps({"models": ["a/one", "b/two"]}))
            passing = lambda project, model, env: {"reply": model_note + " " + router_note, "tool_calls": 0}  # noqa: E731
            with mock.patch.object(live_check, "prepare", return_value=Path(folder) / "project"):
                summary = live_check.check_injection(path, runner=passing, version_text="opencode v2.3.2\n",
                                                     today="2026-11-22")
            self.assertTrue(summary["recorded"])
            self.assertEqual(json.loads(path.read_text()),
                             {"models": ["a/one", "b/two"], "version": "2.3.2", "verified": "2026-11-22"})
            before = path.read_bytes()
            failing = lambda project, model, env: ({"reply": "nothing here", "tool_calls": 0}  # noqa: E731
                                                   if model == "b/two" else passing(project, model, env))
            with mock.patch.object(live_check, "prepare", return_value=Path(folder) / "project"):
                summary = live_check.check_injection(path, runner=failing, version_text="opencode v2.4.0\n",
                                                     today="2026-12-01")
            self.assertFalse(summary["recorded"])
            self.assertEqual(path.read_bytes(), before)
            result = [item for item in summary["results"] if item["model"] == "b/two"][0]
            self.assertFalse(result["pass"])
            self.assertEqual(result["excerpt"], "nothing here")

    def test_the_cli_offers_injection(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_check.py"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertIn("--injection", run.stdout)


if __name__ == "__main__":
    unittest.main()
