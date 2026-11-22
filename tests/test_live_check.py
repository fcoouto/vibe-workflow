"""Pure parts of the dev-only live-check script; never runs OpenCode."""
import contextlib
import importlib.util
import io
import json
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


if __name__ == "__main__":
    unittest.main()
