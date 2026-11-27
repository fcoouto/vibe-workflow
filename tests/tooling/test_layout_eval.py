"""Pure parts of the dev-only layout eval harness; never runs OpenCode."""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


live_check = load("live_check")
layout_eval = load("layout_eval")
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def fake_package(folder):
    package = Path(folder) / "package"
    (package / "skills" / "vibe-project-layout").mkdir(parents=True)
    (package / "index.js").write_text("export default {\n  " + live_check.PLUGIN_ID + "\n}\n")
    (package / "skills" / "vibe-project-layout" / "SKILL.md").write_text(
        "---\nname: vibe-project-layout\ndescription: Use when creating files.\n---\n\n# Project layout\n")
    return package


class ScriptTest(unittest.TestCase):
    def test_runs_as_a_program_although_scripts_holds_select_py(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "layout_eval.py"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--summary", run.stdout)


class RunModelTimeoutTest(unittest.TestCase):
    def test_run_model_passes_its_timeout_to_opencode(self):
        seen = {}

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            seen.setdefault("timeout", timeout)
            return ""
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            live_check.run_model(Path("/clone"), "a/one", {"HOME": "/h"}, prompt="p", timeout=900)
        self.assertEqual(seen["timeout"], 900)


class FixtureTest(unittest.TestCase):
    def build(self, name, description="current", layout=False):
        folder = tempfile.mkdtemp(prefix="layout-eval-test-")
        self.addCleanup(subprocess.run, ["rm", "-rf", folder])
        return layout_eval.build_fixture(Path(folder) / "project", name, fake_package(folder), description, layout)

    def test_every_fixture_is_a_clean_repo_with_its_required_lists(self):
        for name, fixture in layout_eval.FIXTURES.items():
            project = self.build(name)
            self.assertEqual(subprocess.run(["git", "-C", str(project), "status", "--porcelain"],
                                            capture_output=True, text=True).stdout, "", name)
            for listed in fixture["lists"]:
                self.assertTrue((project / listed).is_file(), f"{name}: {listed}")
            self.assertFalse((project / "AGENTS.md").exists(), name)

    def test_the_plugin_copy_is_renamed_and_kept_out_of_git(self):
        project = self.build("node-files")
        index = (project / ".opencode" / "plugins" / "live-check" / "index.js").read_text()
        self.assertIn(live_check.LIVE_ID, index)
        self.assertIn(".opencode/", (project / ".git" / "info" / "exclude").read_text())

    def test_description_c_replaces_only_the_description_line(self):
        skill = self.build("node-files", description="C") / ".opencode/plugins/live-check/skills/vibe-project-layout/SKILL.md"
        text = skill.read_text()
        self.assertRegex(text, r"^---\nname: vibe-project-layout\ndescription: [^\n]+\n---\n")
        self.assertIn("description: " + layout_eval.DESCRIPTIONS["C"] + "\n", text)
        self.assertNotIn(": ", layout_eval.DESCRIPTIONS["C"], "a colon-space breaks the unquoted YAML value")

    def test_a_recorded_layout_names_the_required_list(self):
        project = self.build("python-modules", layout=True)
        agents = (project / "AGENTS.md").read_text()
        self.assertIn("## Project layout", agents)
        self.assertIn("pyproject.toml", agents)


class ScoreTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="layout-eval-score-")
        self.addCleanup(subprocess.run, ["rm", "-rf", folder])
        self.project = layout_eval.build_fixture(Path(folder) / "project", "node-files", fake_package(folder), "current", False)
        self.base = subprocess.run(["git", "-C", str(self.project), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.fixture = layout_eval.FIXTURES["node-files"]

    def test_an_untouched_project_has_no_code_file(self):
        self.assertIsNone(layout_eval.score(self.project, self.base, self.fixture)["code_file"])

    def test_a_listed_module_in_a_linked_worktree_counts_and_tests_do_not(self):
        tree = self.project.parent / "elsewhere"
        subprocess.run([*GIT, "-C", str(self.project), "worktree", "add", "-q", "-b", "task", str(tree)], check=True)
        (tree / "src" / "duration.js").write_text("export const parse = () => 0\n")
        (tree / "test" / "duration.test.js").write_text("// test\n")
        package = json.loads((tree / "package.json").read_text())
        package["files"].append("src/duration.js")
        (tree / "package.json").write_text(json.dumps(package))
        subprocess.run([*GIT, "-C", str(tree), "add", "-A"], check=True)
        subprocess.run([*GIT, "-C", str(tree), "commit", "-qm", "work"], check=True)
        best = layout_eval.best_score(self.project, self.base, self.fixture)
        self.assertEqual((best["code_file"], best["placed"], best["registered"], best["where"]),
                         ("src/duration.js", True, True, "linked-worktree"))

    def test_an_unlisted_module_is_not_registered(self):
        (self.project / "src" / "duration.js").write_text("export const parse = () => 0\n")
        scored = layout_eval.score(self.project, self.base, self.fixture)
        self.assertEqual((scored["code_file"], scored["registered"]), ("src/duration.js", False))


class FactsTest(unittest.TestCase):
    def test_skill_load_outside_reads_and_offer(self):
        project = Path("/tmp/fx/project")
        tools = [{"tool": "skill", "input": {"name": "vibe-project-layout"}},
                 {"tool": "read", "input": {"filePath": "/home/u/.cache/vibe-workflow/skills/vibe-project-layout/references/procedure.md"}},
                 {"tool": "read", "input": {"filePath": str(project / ".opencode/plugins/live-check/skills/vibe-project-layout/SKILL.md")}}]
        facts = layout_eval.facts(tools, "Want me to record this layout in AGENTS.md?", project)
        self.assertTrue(facts["skill_loaded"])
        self.assertEqual(len(facts["outside_skill_reads"]), 1)
        self.assertTrue(facts["offered_to_record"])

    def test_no_offer_and_no_load(self):
        facts = layout_eval.facts([{"tool": "read", "input": {"filePath": "/x/src/a.js"}}], "Done. Two files created.", Path("/p"))
        self.assertEqual((facts["skill_loaded"], facts["offered_to_record"], facts["outside_skill_reads"]), (False, False, []))

    def test_validity_needs_a_finished_run_a_code_file_and_no_outside_reads(self):
        good = {"error": None, "code_file": "src/a.js", "outside_skill_reads": []}
        self.assertTrue(layout_eval.is_valid(good))
        for broken in ({"error": "timeout"}, {"code_file": None}, {"outside_skill_reads": ["/x"]}):
            self.assertFalse(layout_eval.is_valid({**good, **broken}), broken)


class IsolationTest(unittest.TestCase):
    def test_a_relative_read_inside_the_fixture_is_not_an_outside_read(self):
        project = Path("/tmp/fx/project")
        tools = [{"tool": "read", "input": {"path": ".opencode/plugins/live-check/skills/vibe-project-layout/SKILL.md"}},
                 {"tool": "read", "input": {"path": "/elsewhere/skills/vibe-project-layout/SKILL.md"}}]
        self.assertEqual(layout_eval.facts(tools, "", project)["outside_skill_reads"], ["/elsewhere/skills/vibe-project-layout/SKILL.md"])

    def test_the_clean_env_hides_home_skills_but_keeps_logins(self):
        with tempfile.TemporaryDirectory() as folder:
            env = layout_eval.clean_env(Path(folder), Path("/tmp/fx/project"))
            self.assertTrue(env["HOME"].startswith(folder))
            self.assertEqual(env["XDG_DATA_HOME"], str(Path.home() / ".local" / "share"))
            self.assertTrue(env["XDG_CONFIG_HOME"].startswith(folder))
            self.assertEqual(env["PWD"], "/tmp/fx/project")
            self.assertFalse([name for name in env if name.startswith(("OPENCODE", "GIT_"))])


class SummaryTest(unittest.TestCase):
    def test_counts_registered_out_of_valid_per_cell(self):
        rows = [{"experiment": "E1", "model": "m", "description": "C", "layout": False, "valid": True, "registered": True,
                 "skill_loaded": True, "offered_to_record": False},
                {"experiment": "E1", "model": "m", "description": "C", "layout": False, "valid": True, "registered": False,
                 "skill_loaded": False, "offered_to_record": True},
                {"experiment": "E1", "model": "m", "description": "C", "layout": False, "valid": False, "registered": False,
                 "skill_loaded": False, "offered_to_record": False}]
        cell = layout_eval.summarize(rows)[("E1", "m", "C", False)]
        self.assertEqual(cell, {"trials": 3, "valid": 2, "registered": 1, "skill_loaded": 1, "offered_to_record": 1})


if __name__ == "__main__":
    unittest.main()
