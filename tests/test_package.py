"""Exercise the public CLI and clean export, never personal native accounts."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = dict(os.environ, HOME=str(self.root), VCW_TEST_HOME=str(self.root),
                        XDG_STATE_HOME=str(self.root / "fresh-state"))

    def cli(self, data, state=None, cwd=None):
        self.assertTrue((ROOT / "scripts/select.py").is_file(), "standalone JSON CLI is missing")
        return subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/select.py"),
                               "--state-dir", str(state or self.root / "state")],
                              input=data, cwd=cwd or self.root, env=self.env, capture_output=True, timeout=5)

    def test_cli_missing_configuration_has_no_effects(self):
        run = self.cli(b'{"v":1,"work_kind":"review"}')
        self.assertEqual(run.returncode, 0, run.stderr)
        answer = json.loads(run.stdout)
        self.assertEqual(answer["state"], "no_selection")
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "fresh-state").exists())
        for key in ("admission", "reservation", "spend_permit"):
            self.assertIs(answer[key], False)

    def test_cli_refuses_invalid_oversized_non_json_and_relative_paths_without_echo(self):
        for raw, state in [(b"PRIVATE-SENTINEL", None), (b"x" * 65537, None),
                           (b'{"v":1,"work_kind":"review","command":"PRIVATE-SENTINEL"}', None),
                           (b'{"v":1,"work_kind":"review"}', Path("relative"))]:
            with self.subTest(state=state, size=len(raw)):
                run = self.cli(raw, state)
                self.assertEqual(json.loads(run.stdout)["state"], "blocked")
                self.assertNotIn(b"PRIVATE-SENTINEL", run.stdout + run.stderr)
                self.assertLess(len(run.stdout), 65536)

    def test_failed_reader_produces_no_selection_not_a_launch(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        for name, value in [
            ("router.json", dict(version=1, enabled=True, paths=["opencode-chatgpt-subscription"])),
            ("strategy-table.json", dict(schema=2, version=1, rows={"review|high|high": [
                dict(strategy_id="invented", provider="openai", model="invented-model",
                     effort="high", harness="opencode provider openai")]})),
        ]:
            (state / name).write_text(json.dumps(value))
            (state / name).chmod(0o600)
        answer = json.loads(self.cli(b'{"v":1,"work_kind":"review"}').stdout)
        self.assertEqual(answer["state"], "no_selection")
        self.assertIsNone(answer["route"])
        self.assertFalse(answer["admission"])

    def build(self, output):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/package.py"),
                               "--output", str(output)], env=self.env, capture_output=True, timeout=15)

    def test_export_runs_without_repo_and_excludes_all_unapproved_files(self):
        output = self.root / "export"
        run = self.build(output)
        self.assertEqual(run.returncode, 0, run.stderr.decode())
        files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        expected = {"index.js", "project.js", "onboarding.js", "pr-advice.js", "package.json", "README.md", "LICENSE", "assets/logo.png",
                    "skills/vibe-onboard-project/SKILL.md", "skills/vibe-onboard-project/references/procedure.md",
                    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-prepare-my-workspace/references/procedure.md",
                    "skills/vibe-prepare-my-workspace/scripts/check.py", "skills/vibe-prepare-my-workspace/partners.json",
                    "skills/vibe-pull-requests/SKILL.md", "skills/vibe-pull-requests/references/procedure.md",
                    "skills/vibe-pull-requests/references/pull-request-template.md",
                    "skills/vibe-pull-requests/references/worktree-cleanup.md",
                    "scripts/select.py", "config/routes.json", "config/router.example.json",
                    "config/strategy-table.example.json"}
        expected |= {"runtime/" + name + ".py" for name in [
            "__init__", "routing", "routing_paths", "routing_observations", "routing_funding",
            "subscription_billing", "selector", "storage", "processes", "continuity",
            "native_catalog", "claude_usage", "opencode_account", "scores"]}
        self.assertEqual(files, expected)
        self.assertEqual(json.loads((output / "package.json").read_text())["name"], "vibe-workflow")
        self.assertEqual((output / "assets/logo.png").read_bytes(), (ROOT / "assets/logo.png").read_bytes())
        for path in output.rglob("*"):
            if path.suffix in (".py", ".js", ".json", ".md"):
                self.assertNotIn(os.fsencode(self.root), path.read_bytes())
        check = subprocess.run([sys.executable, "-I", "-B", str(output / "scripts/select.py"),
                                "--state-dir", str(self.root / "absent")], input=b'{"v":1,"work_kind":"testing"}',
                               env=self.env, cwd=self.root, capture_output=True, timeout=5)
        self.assertEqual(json.loads(check.stdout)["state"], "no_selection")
        node = subprocess.run(["node", "--input-type=module", "-e", """
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
const plugin = (await import(pathToFileURL(process.argv[1]))).default
assert.equal(plugin.id, 'vibe-workflow')
const tools = [], skills = []
await plugin.setup({
  location: { directory: process.cwd() }, options: {},
  tool: { transform: async f => f({ add: t => tools.push(t.name) }) },
  skill: { transform: async f => f({ add: s => skills.push(s) }) },
  session: { hook: async () => {} },
})
assert.deepEqual(tools.sort(), ['smart_router_select', 'vibe_workflow_onboard'])
assert.deepEqual(skills.map(({id, name}) => [id, name]), [['vibe-onboard-project', 'vibe-onboard-project'],
  ['vibe-prepare-my-workspace', 'vibe-prepare-my-workspace'], ['vibe-pull-requests', 'vibe-pull-requests']])
assert.match(await readFile(new URL('./references/procedure.md', pathToFileURL(skills[0].path)), 'utf8'),
  /Use Smart Router for this project\\?/)
""", str(output / "index.js")], env=self.env, cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(node.returncode, 0, node.stderr)

    def test_export_never_replaces_an_existing_directory(self):
        output = self.root / "owned"
        output.mkdir()
        (output / "keep").write_text("user data")
        run = self.build(output)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual((output / "keep").read_text(), "user data")

    def test_validator_rejects_legacy_files_and_imports_in_export(self):
        output = self.root / "export"
        self.assertEqual(self.build(output).returncode, 0)
        def check():
            return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/validate.py"),
                                   "--root", str(output)], env=self.env, capture_output=True, timeout=10)
        self.assertEqual(check().returncode, 0)
        (output / "runtime/ledger.py").write_text("SECRET = 1\n")
        self.assertNotEqual(check().returncode, 0)
        (output / "runtime/ledger.py").unlink()
        with (output / "runtime/selector.py").open("a") as out:
            out.write("\nimport requests\n")
        self.assertNotEqual(check().returncode, 0)


if __name__ == "__main__":
    unittest.main()
