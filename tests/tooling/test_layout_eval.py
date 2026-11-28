"""Pure parts of the dev-only layout eval harness; never runs OpenCode."""
import importlib.util
import json
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
SHIPPED_SKILL = (ROOT / "skills" / "vibe-project-layout" / "SKILL.md").read_text()


def fake_package(folder):
    package = Path(folder) / "package"
    (package / "skills" / "vibe-project-layout").mkdir(parents=True)
    (package / "index.js").write_text("export default {\n  " + live_check.PLUGIN_ID + "\n}\n"
                                      '    await registerSkill(ctx, "vibe-pull-requests")\n'
                                      '    await registerSkill(ctx, "vibe-project-layout")\n')
    (package / "skills" / "vibe-project-layout" / "SKILL.md").write_text(SHIPPED_SKILL)
    return package


def git(path, *args):
    subprocess.run([*GIT, "-C", str(path), *args], check=True, capture_output=True)


class ScriptTest(unittest.TestCase):
    def test_runs_as_a_program_although_scripts_holds_select_py(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "layout_eval.py"), "--help"],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--summary", run.stdout)

    def test_an_unlisted_model_is_refused_before_any_trial(self):
        run = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "layout_eval.py"), "--out", "/dev/null",
                              "--model", "openrouter/paid/model", "--description", "current"],
                             capture_output=True, text=True, timeout=30)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("not an authorized model", run.stderr)


class RunModelTimeoutTest(unittest.TestCase):
    def test_run_model_passes_its_timeout_to_opencode(self):
        seen = {}

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            seen.setdefault("timeout", timeout)
            return ""
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            live_check.run_model(Path("/clone"), "a/one", {"HOME": "/h"}, prompt="p", timeout=900)
        self.assertEqual(seen["timeout"], 900)


class RunModelErrorsTest(unittest.TestCase):
    def test_run_model_surfaces_error_events(self):
        lines = [{"type": "text", "sessionID": "ses_x", "part": {"id": "p1", "type": "text", "text": "partial"}},
                 {"type": "error", "sessionID": "ses_x", "error": {"name": "ProviderError", "data": {"message": "429"}}}]

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            return "\n".join(json.dumps(line) for line in lines) if argv[1] == "run" else ""
        with mock.patch.object(live_check, "_run", side_effect=fake_run):
            answer = live_check.run_model(Path("/clone"), "a/one", {"HOME": "/h"})
        self.assertEqual(len(answer["errors"]), 1)


class Base(unittest.TestCase):
    def build(self, name, description="current", layout=False, marker="m-1"):
        folder = tempfile.mkdtemp(prefix="layout-eval-test-")
        self.addCleanup(subprocess.run, ["rm", "-rf", folder])
        project = layout_eval.build_fixture(Path(folder) / "project", name, fake_package(folder), description, layout, marker)
        base = subprocess.run(["git", "-C", str(project), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        return project, base


class FixtureTest(Base):
    def test_every_fixture_is_a_clean_repo_whose_lists_parse(self):
        for name, fixture in layout_eval.FIXTURES.items():
            project, _ = self.build(name)
            self.assertEqual(subprocess.run(["git", "-C", str(project), "status", "--porcelain"],
                                            capture_output=True, text=True).stdout, "", name)
            for spec in fixture["lists"]:
                self.assertTrue(layout_eval.entries(spec, (project / spec["file"]).read_text()), f"{name}: {spec['file']}")
            self.assertFalse((project / "AGENTS.md").exists(), name)

    def test_the_plugin_copy_is_renamed_marked_and_kept_out_of_git(self):
        project, _ = self.build("node-files", marker="m-42")
        plugin = project / ".opencode" / "plugins" / "live-check"
        self.assertIn(live_check.LIVE_ID, (plugin / "index.js").read_text())
        self.assertIn("m-42", (plugin / "skills" / "vibe-project-layout" / "SKILL.md").read_text())
        self.assertIn(".opencode/", (project / ".git" / "info" / "exclude").read_text())

    def test_description_c_changes_only_the_frontmatter_description_of_the_shipped_skill(self):
        current, _ = self.build("node-files", marker="same")
        variant, _ = self.build("node-files", description="C", marker="same")
        path = ".opencode/plugins/live-check/skills/vibe-project-layout/SKILL.md"
        before, after = (current / path).read_text().splitlines(), (variant / path).read_text().splitlines()
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(before), len(after))
        self.assertEqual(changed, [2])
        self.assertEqual(after[2], "description: " + layout_eval.DESCRIPTIONS["C"])
        self.assertNotIn(": ", layout_eval.DESCRIPTIONS["C"], "a colon-space breaks the unquoted YAML value")

    def test_the_absent_arm_removes_the_skill_and_only_its_registration(self):
        project, _ = self.build("node-files", description="absent")
        plugin = project / ".opencode" / "plugins" / "live-check"
        index = (plugin / "index.js").read_text()
        self.assertFalse((plugin / "skills" / "vibe-project-layout").exists())
        self.assertNotIn('registerSkill(ctx, "vibe-project-layout")', index)
        self.assertIn('registerSkill(ctx, "vibe-pull-requests")', index)
        self.assertIn(live_check.LIVE_ID, index)

    def test_the_absent_arm_refuses_a_plugin_whose_registration_line_changed(self):
        folder = tempfile.mkdtemp(prefix="layout-eval-test-")
        self.addCleanup(subprocess.run, ["rm", "-rf", folder])
        package = fake_package(folder)
        (package / "index.js").write_text("export default {\n  " + live_check.PLUGIN_ID + "\n}\n")
        with self.assertRaises(ValueError):
            layout_eval.build_fixture(Path(folder) / "project", "node-files", package, "absent", False, "m")

    def test_the_shipped_entry_still_registers_the_skill_on_one_line(self):
        self.assertEqual((ROOT / "index.js").read_text().count(layout_eval.REGISTRATION), 1)

    def test_a_description_line_outside_the_frontmatter_is_refused(self):
        with self.assertRaises(ValueError):
            layout_eval.patch_description("# no frontmatter\ndescription: x\n", "C")

    def test_a_recorded_layout_names_the_required_list(self):
        project, _ = self.build("python-modules", layout=True)
        agents = (project / "AGENTS.md").read_text()
        self.assertIn("## Project layout", agents)
        self.assertIn("pyproject.toml", agents)


class EntriesTest(unittest.TestCase):
    def test_each_list_kind_parses_entries_not_text(self):
        files = {"file": "package.json", "kind": "files", "base": ""}
        self.assertEqual(layout_eval.entries(files, json.dumps({"description": "duration", "files": ["src/a.js"]})), {"src/a.js"})
        py = {"file": "pyproject.toml", "kind": "py-modules", "base": ""}
        self.assertEqual(layout_eval.entries(py, '[tool.setuptools]\n# duration\npy-modules = ["stock", "duration"]\n'),
                         {"stock", "duration"})
        barrel = {"file": "index.js", "kind": "reexports", "base": ""}
        text = ('export { a } from "./slugify.js"\nexport * from \'./money\'\nimport { d } from "./duration.js"\n'
                '// export { x } from "./commented.js"\n/*\nexport { y } from "./blocked.js"\n*/\n')
        self.assertEqual(layout_eval.entries(barrel, text), {"slugify", "money"})

    def test_malformed_list_entries_are_ignored_not_fatal(self):
        files = {"file": "package.json", "kind": "files", "base": ""}
        self.assertEqual(layout_eval.entries(files, json.dumps({"files": ["src/a.js", {"x": 1}, 3]})), {"src/a.js"})
        self.assertEqual(layout_eval.entries(files, json.dumps(["not", "an", "object"])), set())

    def test_expected_entry_is_relative_to_the_list_base(self):
        self.assertEqual(layout_eval.expected_entry({"kind": "files", "base": "packages/api"}, "packages/api/src/d.js"), "src/d.js")
        self.assertEqual(layout_eval.expected_entry({"kind": "py-modules", "base": ""}, "src/duration.py"), "duration")
        self.assertEqual(layout_eval.expected_entry({"kind": "reexports", "base": ""}, "parse-duration.js"), "parse-duration")

    def test_tests_are_classified_by_convention_not_by_substring(self):
        for path in ("test/a.test.js", "tests/test_a.py", "a.test.js", "pkg/tests/x.py", "a_test.py"):
            self.assertTrue(layout_eval.is_test(path), path)
        for path in ("src/latest.js", "src/attestation.py", "contest.js"):
            self.assertFalse(layout_eval.is_test(path), path)


class ScoreTest(Base):
    def setUp(self):
        self.project, self.base = self.build("node-files")
        self.fixture = layout_eval.FIXTURES["node-files"]

    def add_file(self, root, entries_added):
        package = json.loads((root / "package.json").read_text())
        package["files"] += entries_added
        (root / "package.json").write_text(json.dumps(package))

    def test_an_untouched_project_has_no_code_file(self):
        result = layout_eval.result(self.project, self.base, self.fixture)
        self.assertEqual(result["code_files"], [])
        self.assertFalse(result["registered"])

    def test_a_listed_module_in_a_linked_worktree_counts_and_tests_do_not(self):
        tree = self.project.parent / "elsewhere"
        git(self.project, "worktree", "add", "-q", "-b", "task", str(tree))
        (tree / "src" / "duration.js").write_text("export const parse = () => 0\n")
        (tree / "test" / "duration.test.js").write_text("// test\n")
        self.add_file(tree, ["src/duration.js"])
        git(tree, "add", "-A")
        git(tree, "commit", "-qm", "work")
        result = layout_eval.result(self.project, self.base, self.fixture)
        self.assertEqual((result["code_files"], result["placed"], result["registered"], result["where"], result["split"]),
                         (["src/duration.js"], True, True, "linked-worktree", False))

    def test_a_mention_in_another_field_is_not_registration(self):
        (self.project / "src" / "duration.js").write_text("export const parse = () => 0\n")
        package = json.loads((self.project / "package.json").read_text())
        package["description"] = "orders, billing and a duration parser"
        (self.project / "package.json").write_text(json.dumps(package))
        self.assertFalse(layout_eval.result(self.project, self.base, self.fixture)["registered"])

    def test_every_new_module_must_be_registered(self):
        (self.project / "src" / "duration.js").write_text("x\n")
        (self.project / "src" / "units.js").write_text("y\n")
        self.add_file(self.project, ["src/duration.js"])
        result = layout_eval.result(self.project, self.base, self.fixture)
        self.assertEqual(result["code_files"], ["src/duration.js", "src/units.js"])
        self.assertFalse(result["registered"])

    def test_code_in_a_worktree_with_the_list_edited_in_the_fixture_is_split(self):
        tree = self.project.parent / "elsewhere"
        git(self.project, "worktree", "add", "-q", "-b", "task", str(tree))
        (tree / "src" / "duration.js").write_text("x\n")
        self.add_file(self.project, ["src/duration.js"])
        result = layout_eval.result(self.project, self.base, self.fixture)
        self.assertEqual((result["where"], result["registered"], result["split"]), ("linked-worktree", False, True))


class MoreScoreTest(Base):
    def test_new_code_in_two_checkouts_is_split_even_without_list_edits_elsewhere(self):
        project, base = self.build("node-files")
        tree = project.parent / "elsewhere"
        git(project, "worktree", "add", "-q", "-b", "task", str(tree))
        (tree / "src" / "units.js").write_text("x\n")
        (project / "src" / "duration.js").write_text("y\n")
        package = json.loads((project / "package.json").read_text())
        package["files"].append("src/duration.js")
        (project / "package.json").write_text(json.dumps(package))
        self.assertTrue(layout_eval.result(project, base, layout_eval.FIXTURES["node-files"])["split"])


class FactsTest(unittest.TestCase):
    def test_a_skill_load_counts_only_when_its_output_carries_the_marker(self):
        project = Path("/tmp/fx/project")
        good = [{"tool": "skill", "input": {"name": "vibe-project-layout"}, "output": "... eval-marker m-1 ..."}]
        bad = [{"tool": "skill", "input": {"name": "vibe-project-layout"}, "output": "the installed copy"}]
        self.assertEqual(layout_eval.facts(good, "", project, "m-1")["skill_loaded"], True)
        self.assertEqual(layout_eval.facts(good, "", project, "m-1")["unverified_skill_load"], False)
        self.assertEqual(layout_eval.facts(bad, "", project, "m-1")["unverified_skill_load"], True)

    def test_outside_reads_resolve_relative_paths_against_the_fixture(self):
        project = Path("/tmp/fx/project")
        tools = [{"tool": "read", "input": {"path": ".opencode/plugins/live-check/skills/vibe-project-layout/SKILL.md"}},
                 {"tool": "read", "input": {"path": "/elsewhere/skills/vibe-project-layout/SKILL.md"}}]
        self.assertEqual(layout_eval.facts(tools, "", project, "m")["outside_skill_reads"], ["/elsewhere/skills/vibe-project-layout/SKILL.md"])

    def test_any_shell_command_naming_the_skill_fails_closed(self):
        tools = [{"tool": "shell", "input": {"command": "cd ~/.agents/skills && cat vibe-project-layout/SKILL.md"}}]
        self.assertEqual(len(layout_eval.facts(tools, "", Path("/p"), "m")["outside_skill_reads"]), 1)

    def test_a_read_naming_the_skill_outside_the_fixture_fails_closed(self):
        tools = [{"tool": "read", "input": {"path": "/x/vibe-project-layout/SKILL.md"}}]
        self.assertEqual(len(layout_eval.facts(tools, "", Path("/p"), "m")["outside_skill_reads"]), 1)

    def test_the_offer_flag(self):
        self.assertTrue(layout_eval.facts([], "Want me to record this layout in AGENTS.md?", Path("/p"), "m")["offered_to_record"])
        self.assertFalse(layout_eval.facts([], "Done. Two files created.", Path("/p"), "m")["offered_to_record"])

    def test_validity_reasons(self):
        good = {"error": None, "code_files": ["src/a.js"], "outside_skill_reads": [], "unverified_skill_load": False,
                "split": False, "sessions_unknown": False, "run_errors": [], "reply": "Done."}
        self.assertEqual(layout_eval.invalid_reason(good), None)
        cases = {"error": {"error": "timeout"}, "no_code_file": {"code_files": []}, "outside_skill_read": {"outside_skill_reads": ["/x"]},
                 "unverified_skill_load": {"unverified_skill_load": True}, "split_result": {"split": True},
                 "run_error_event": {"run_errors": [{"type": "error"}]}, "no_final_reply": {"reply": "  "}}
        for reason, broken in cases.items():
            self.assertEqual(layout_eval.invalid_reason({**good, **broken}), reason)


class CleanupTest(unittest.TestCase):
    def test_only_paths_inside_the_trial_are_removable(self):
        trial = Path("/tmp/layout-eval-run-x/layout-eval-y")
        self.assertTrue(layout_eval.inside(trial / "project-wt", trial))
        self.assertFalse(layout_eval.inside(Path("/Users/someone/work"), trial))
        self.assertFalse(layout_eval.inside(Path("/tmp/layout-eval-run-x/layout-eval-y/../other"), trial))

    def test_the_session_sweep_deletes_every_session_listed_for_the_fixture(self):
        calls = []

        def fake_run(argv, cwd=None, timeout=120, data=None, env=None):
            calls.append(argv)
            if argv[:3] == ["opencode", "session", "list"]:
                return json.dumps([{"id": "ses_a", "directory": "/p"}, {"id": "ses_b", "location": {"directory": "/p"}},
                                   {"id": "ses_other", "directory": "/Users/someone/real-project"}, {"id": "ses_nodir"}])
            return ""
        with mock.patch.object(layout_eval.live_check, "_run", side_effect=fake_run):
            deleted, unknown = layout_eval.sweep_sessions(Path("/p"), {"PWD": "/p"})
        self.assertEqual((deleted, unknown), (["ses_a", "ses_b"], True))
        self.assertEqual([c[:4] for c in calls[1:]], [["opencode", "session", "delete", "ses_a"], ["opencode", "session", "delete", "ses_b"]])

    def test_an_unexpected_listing_shape_is_unknown_and_deletes_nothing(self):
        with mock.patch.object(layout_eval.live_check, "_run", return_value=json.dumps({"weird": 1})) as run:
            self.assertEqual(layout_eval.sweep_sessions(Path("/p"), {}), ([], True))
        self.assertEqual(run.call_count, 1)

    def test_every_fixture_gets_its_own_root_commit(self):
        folder = tempfile.mkdtemp(prefix="layout-eval-test-")
        self.addCleanup(subprocess.run, ["rm", "-rf", folder])
        package = fake_package(folder)
        roots = set()
        for marker in ("one", "two"):
            project = layout_eval.build_fixture(Path(folder) / marker, "node-files", package, "current", False, marker)
            roots.add(subprocess.run(["git", "-C", str(project), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout)
        self.assertEqual(len(roots), 2)

    def test_a_failed_listing_reports_unknown(self):
        with mock.patch.object(layout_eval.live_check, "_run", side_effect=subprocess.CalledProcessError(1, "x")):
            self.assertEqual(layout_eval.sweep_sessions(Path("/p"), {}), ([], True))


class IsolationTest(unittest.TestCase):
    def test_the_clean_env_hides_home_skills_but_keeps_logins(self):
        with tempfile.TemporaryDirectory() as folder:
            env = layout_eval.clean_env(Path(folder), Path("/tmp/fx/project"))
            self.assertTrue(env["HOME"].startswith(folder))
            self.assertEqual(env["XDG_DATA_HOME"], str(Path.home() / ".local" / "share"))
            self.assertTrue(env["XDG_CONFIG_HOME"].startswith(folder))
            self.assertEqual(env["PWD"], "/tmp/fx/project")
            self.assertFalse([name for name in env if name.startswith(("OPENCODE", "GIT_"))])


class SummaryTest(unittest.TestCase):
    def test_counts_per_fixture_and_overall_with_invalid_reasons(self):
        def row(fixture, valid, registered, reason=None):
            return {"experiment": "E1", "model": "m", "description": "C", "layout": False, "fixture": fixture,
                    "valid": valid, "invalid_reason": reason, "registered": registered, "skill_loaded": registered,
                    "offered_to_record": False}
        rows = [row("a", True, True), row("a", True, False), row("b", False, False, "no_code_file")]
        cells = layout_eval.summarize(rows)
        self.assertEqual(cells[("E1", "m", "C", False, "a")]["registered"], 1)
        overall = cells[("E1", "m", "C", False, "*")]
        self.assertEqual((overall["trials"], overall["valid"], overall["registered"], overall["invalid"]),
                         (3, 2, 1, {"no_code_file": 1}))


if __name__ == "__main__":
    unittest.main()
