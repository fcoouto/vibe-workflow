"""Package hook contracts checked against disposable copies."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.validate import validate


PACKAGE = Path(__file__).resolve().parents[1]
CODEX_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "Stop", "Interrupt")


class HookManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hook manifest ")
        self.addCleanup(self.temp.cleanup)
        self.package = Path(self.temp.name) / "package"
        shutil.copytree(PACKAGE, self.package, ignore=shutil.ignore_patterns("__pycache__"))

    def read_hooks(self, client):
        path = self.package / f"hooks/{client}.json"
        return path, json.loads(path.read_text())

    def write_hooks(self, path, data):
        path.write_text(json.dumps(data))

    def seed_codex_contract(self):
        path, data = self.read_hooks("codex")
        data["hooks"] = {
            event: [{"hooks": [{
                "type": "command",
                "command": f'python3 -I -B "${{PLUGIN_ROOT}}/scripts/workflow.py" codex {event}',
                "timeout": 1 if event == "Interrupt" else 5,
            }]}]
            for event in CODEX_EVENTS
        }
        data["hooks"]["SessionStart"][0]["matcher"] = "^(startup|resume|clear|compact|fork)$"
        data["hooks"]["PreToolUse"][0]["matcher"] = "^apply_patch$"
        data["hooks"]["PostToolUse"][0]["matcher"] = "*"
        self.write_hooks(path, data)

    def test_package_hook_contracts_pass(self):
        self.assertEqual(validate(self.package)["validation"], "passed")

    def test_required_runtime_files_are_rejected_when_missing(self):
        for relative in ("runtime/engine.py", "runtime/state.py", "runtime/doctor.py", "clients/runtime.md",
                         "runtime/delegation.py", "clients/delegation.md", "runtime/repository_gate.py",
                         "scripts/repository_gate.py", "clients/repository-gate.md"):
            with self.subTest(path=relative):
                path = self.package / relative
                content = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises((OSError, ValueError)):
                        validate(self.package)
                finally:
                    path.write_bytes(content)

    def test_missing_runtime_event_is_rejected(self):
        self.seed_codex_contract()
        path, data = self.read_hooks("codex")
        del data["hooks"]["Stop"]
        self.write_hooks(path, data)
        with self.assertRaises(ValueError):
            validate(self.package)

    def test_duplicate_handler_is_rejected(self):
        self.seed_codex_contract()
        path, data = self.read_hooks("codex")
        data["hooks"]["Stop"][0]["hooks"].append(dict(data["hooks"]["Stop"][0]["hooks"][0]))
        self.write_hooks(path, data)
        with self.assertRaises(ValueError):
            validate(self.package)

    def test_wrong_native_event_or_command_is_rejected(self):
        self.seed_codex_contract()
        self.seed_codex_contract()
        path, data = self.read_hooks("codex")
        group = data["hooks"].pop("PreToolUse")
        data["hooks"]["PermissionRequest"] = group
        self.write_hooks(path, data)
        with self.assertRaises(ValueError):
            validate(self.package)

        self.seed_codex_contract()
        path, data = self.read_hooks("codex")
        data["hooks"]["Stop"][0]["hooks"][0]["command"] += " extra"
        self.write_hooks(path, data)
        with self.assertRaises(ValueError):
            validate(self.package)

    def test_bad_timeout_is_rejected(self):
        self.seed_codex_contract()
        path, data = self.read_hooks("codex")
        data["hooks"]["Interrupt"][0]["hooks"][0]["timeout"] = 5
        self.write_hooks(path, data)
        with self.assertRaises(ValueError):
            validate(self.package)

    def command(self, event):
        return {"type": "command", "timeout": 5,
                "command": f'python3 -I -B "${{CLAUDE_PLUGIN_ROOT}}/scripts/workflow.py" claude-code {event}'}

    def test_claude_manifest_registers_exactly_its_supported_events(self):
        path, data = self.read_hooks("claude-code")
        self.assertEqual(set(data["hooks"]), {"SessionStart", "PreToolUse", "PostToolUse",
                                              "PostToolUseFailure", "Stop", "UserPromptSubmit"})
        self.assertEqual(data["hooks"]["PreToolUse"][0]["matcher"], "Edit|Write|NotebookEdit")

    def test_unsupported_claude_event_is_rejected(self):
        # Claude has no Interrupt hook; registering one would never fire and misstate coverage.
        path, data = self.read_hooks("claude-code")
        data["hooks"]["Interrupt"] = [{"hooks": [self.command("Interrupt")]}]
        self.write_hooks(path, data)
        with self.assertRaisesRegex(ValueError, "claude-code hook events"):
            validate(self.package)

    def test_missing_duplicate_or_misrouted_claude_handlers_are_rejected(self):
        for mutate, message in (
                (lambda hooks: hooks.pop("Stop"), "hook events"),
                (lambda hooks: hooks["Stop"][0]["hooks"].append(self.command("Stop")), "runtime command"),
                (lambda hooks: hooks["PreToolUse"][0].update(matcher="Edit"), "matcher"),
                (lambda hooks: hooks["PostToolUse"][0]["hooks"][0].update(timeout=6), "runtime command"),
                (lambda hooks: hooks["Stop"][0]["hooks"][0].update(command=self.command("PostToolUse")["command"]),
                 "runtime command")):
            with self.subTest(message=message):
                path, data = self.read_hooks("claude-code")
                original = json.dumps(data)
                mutate(data["hooks"])
                self.write_hooks(path, data)
                with self.assertRaisesRegex(ValueError, message):
                    validate(self.package)
                path.write_text(original)


if __name__ == "__main__":
    unittest.main()
