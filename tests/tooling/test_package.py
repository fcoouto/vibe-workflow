"""Exercise the public CLI and clean export, never personal native accounts."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = dict(os.environ, HOME=str(self.root), VCW_TEST_HOME=str(self.root),
                        XDG_STATE_HOME=str(self.root / "fresh-state"))

    def cli(self, data, state=None, cwd=None, extra=()):
        self.assertTrue((ROOT / "scripts/select.py").is_file(), "standalone JSON CLI is missing")
        return subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/select.py"),
                               "--state-dir", str(state or self.root / "state"), *extra],
                              input=data, cwd=cwd or self.root, env=self.env, capture_output=True, timeout=5)

    def request(self, **extra):
        models = [dict(provider="openai", id="gpt-6-2-sol", model=None, family=None, variants=["high"],
                       context=None, released=5)]
        return json.dumps(dict(dict(v=2, work_kind="review", task=dict(files="1-2", spec="exact", risk="reversible"),
                                    models=models), **extra)).encode()

    def test_cli_empty_model_list_has_no_effects(self):
        run = self.cli(self.request(models=[]))
        self.assertEqual(run.returncode, 0, run.stderr)
        answer = json.loads(run.stdout)
        self.assertEqual((answer["state"], answer["reason"]), ("no_selection", "no_models"))
        self.assertFalse((self.root / "state").exists())
        self.assertFalse((self.root / "fresh-state").exists())
        for key in ("admission", "reservation", "spend_permit"):
            self.assertIs(answer[key], False)

    def test_cli_without_account_evidence_has_no_eligible_route(self):
        answer = json.loads(self.cli(self.request()).stdout)
        self.assertEqual((answer["state"], answer["reason"], answer["route"]), ("no_selection", "no_eligible_route", None))
        self.assertFalse(answer["admission"])

    def test_cli_council_roster_without_account_evidence(self):
        models = [dict(provider="openai", id="gpt-6-2-sol", model=None, family=None, variants=["high"],
                       context=None, released=5)]
        run = self.cli(json.dumps(dict(v=1, models=models)).encode(), extra=["--council-roster"])
        self.assertEqual(run.returncode, 0, run.stderr)
        answer = json.loads(run.stdout)
        self.assertEqual(answer["state"], "checked")
        self.assertEqual([seat["status"] for seat in answer["seats"]], ["billing_unverified"])

    def test_cli_council_roster_refuses_invalid_input(self):
        run = self.cli(b"PRIVATE-SENTINEL", extra=["--council-roster"])
        self.assertEqual(run.stdout.strip(), b'{"state":"failed"}')
        self.assertNotIn(b"PRIVATE-SENTINEL", run.stdout + run.stderr)

    def test_cli_chatgpt_limit(self):
        run = self.cli(b"ignored", extra=["--chatgpt-limit"])
        self.assertEqual(run.returncode, 0, run.stderr)
        answer = json.loads(run.stdout)
        self.assertEqual((answer["state"], answer["source"]), ("marked", "fallback"))
        self.assertGreater(answer["until"], int(time.time()))
        self.assertEqual((self.root / "state/chatgpt-limit.json").stat().st_mode & 0o777, 0o600)

    def test_cli_refresh_skips_a_fresh_cache_without_echo(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        now = int(time.time())
        (state / "openrouter-models.json").write_text(json.dumps(dict(
            version=1, fetched_at=now, attempt_at=now, failures=0, models={}, unscored={})))
        (state / "openrouter-models.json").chmod(0o600)
        run = self.cli(b'{"wanted":["PRIVATE-SENTINEL"]}', extra=["--refresh"])
        self.assertEqual(run.stdout.strip(), b'{"state":"skipped"}')
        self.assertNotIn(b"PRIVATE-SENTINEL", run.stdout + run.stderr)

    def test_cli_refuses_invalid_oversized_non_json_and_relative_paths_without_echo(self):
        for raw, state in [(b"PRIVATE-SENTINEL", None), (b"x" * 65537, None),
                           (self.request(command="PRIVATE-SENTINEL"), None),
                           (self.request(), Path("relative"))]:
            with self.subTest(state=state, size=len(raw)):
                run = self.cli(raw, state)
                self.assertEqual(json.loads(run.stdout)["state"], "blocked")
                self.assertNotIn(b"PRIVATE-SENTINEL", run.stdout + run.stderr)
                self.assertLess(len(run.stdout), 65536)

    def build(self, output):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/package.py"),
                               "--output", str(output)], env=self.env, capture_output=True, timeout=15)

    def test_export_runs_without_repo_and_excludes_all_unapproved_files(self):
        output = self.root / "export"
        run = self.build(output)
        self.assertEqual(run.returncode, 0, run.stderr.decode())
        files = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        expected = {"index.js", "src/project.js", "src/onboarding.js", "src/pr-advice.js", "src/advice.js", "src/skill-advice.js", "src/session-facts.js", "src/session-titles.js", "src/council.js",
                    "package.json", "README.md", "LICENSE", "assets/logo.png",
                    "skills/vibe-onboard-project/SKILL.md", "skills/vibe-onboard-project/references/procedure.md",
                    "skills/vibe-prepare-my-workspace/SKILL.md", "skills/vibe-prepare-my-workspace/references/procedure.md",
                    "skills/vibe-prepare-my-workspace/scripts/check.py", "skills/vibe-prepare-my-workspace/partners.json",
                    "skills/vibe-council/SKILL.md", "skills/vibe-council/references/procedure.md",
                    "skills/vibe-council/references/brief-template.md",
                    "skills/vibe-council/references/record-template.md",
                    "skills/vibe-project-layout/SKILL.md", "skills/vibe-project-layout/references/procedure.md",
                    "skills/vibe-project-layout/references/layouts.md",
                    "skills/vibe-pull-requests/SKILL.md", "skills/vibe-pull-requests/references/procedure.md",
                    "skills/vibe-pull-requests/references/pull-request-template.md",
                    "skills/vibe-pull-requests/references/worktree-cleanup.md",
                    "scripts/select.py", "config/routes.json"}
        expected |= {"runtime/" + name + ".py" for name in [
            "__init__", "routing", "routing_paths", "routing_observations",
            "subscription_billing", "selector", "storage", "processes", "continuity",
            "native_catalog", "claude_usage", "claude_quota", "opencode_account", "scores", "codex_reset", "chatgpt_limit", "council", "feedback"]}
        self.assertEqual(files, expected)
        self.assertEqual(json.loads((output / "package.json").read_text())["name"], "vibe-workflow")
        self.assertEqual((output / "assets/logo.png").read_bytes(), (ROOT / "assets/logo.png").read_bytes())
        for path in output.rglob("*"):
            if path.suffix in (".py", ".js", ".json", ".md"):
                self.assertNotIn(os.fsencode(self.root), path.read_bytes())
        check = subprocess.run([sys.executable, "-I", "-B", str(output / "scripts/select.py"),
                                "--state-dir", str(self.root / "absent")], input=self.request(models=[]),
                               env=self.env, cwd=self.root, capture_output=True, timeout=5)
        self.assertEqual(json.loads(check.stdout)["reason"], "no_models")
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
assert.deepEqual(tools.sort(), ['smart_router_feedback', 'smart_router_select', 'vibe_council_roster', 'vibe_workflow_onboard'])
assert.deepEqual(skills.map(({id, name}) => [id, name]), [['vibe-onboard-project', 'vibe-onboard-project'],
  ['vibe-prepare-my-workspace', 'vibe-prepare-my-workspace'], ['vibe-pull-requests', 'vibe-pull-requests'], ['vibe-council', 'vibe-council'],
  ['vibe-project-layout', 'vibe-project-layout']])
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

    def test_npm_files_cover_the_export(self):
        # OpenCode installs the plugin from git through npm, which ships only package.json "files".
        sys.path.insert(0, str(ROOT / "scripts"))
        from manifest import FILES
        listed = json.loads((ROOT / "package.json").read_text())["files"]
        missing = [name for name in FILES if name != "package.json"
                   and not any(name == entry or name.startswith(entry.rstrip("/") + "/") for entry in listed)]
        self.assertEqual(missing, [])

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
