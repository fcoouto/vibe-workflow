"""Graphify maintenance is reachable selectively, without loading at startup."""

from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

from scripts import read_instructions as reader


ROOT = Path(__file__).resolve().parents[1]
PROCEDURE = "clients/graphify-maintenance.md"


class GraphifyMaintenanceInstructionsTests(unittest.TestCase):
    def test_merge_onboarding_and_local_sync_link_to_one_procedure(self):
        for source in ("policies/08-merge-readiness.md", "docs/development.md",
                       "skills/onboard-my-project/references/procedure.md"):
            with self.subTest(source=source):
                path = ROOT / source
                targets = {(path.parent / match.split("#", 1)[0]).resolve()
                           for match in re.findall(r"\]\(([^)]+)\)", path.read_text())
                           if not match.startswith(("https:", "http:", "#"))}
                self.assertIn(ROOT / PROCEDURE, targets)

    def test_selected_maintenance_delivers_complete_local_procedure(self):
        self.assertTrue((ROOT / PROCEDURE).is_file(), "missing maintenance procedure")
        paths = ["policies/08-merge-readiness.md", PROCEDURE]
        batch = reader.load_batch(ROOT, paths, auto=True)
        revision = batch["revision"]
        sources = {}
        while True:
            for section in batch["sections"]:
                sources.setdefault(section["path"], "")
                sources[section["path"]] += section["text"]
            if batch["last_page"] == batch["pages"]:
                break
            batch = reader.load_batch(ROOT, paths, batch["last_page"] + 1,
                                      revision, auto=True)
        self.assertEqual(list(sources), paths)
        for path in paths:
            self.assertEqual(sources[path], (ROOT / path).read_text())
        self.assertNotIn("clients/runtime.md", sources)

    def test_startup_does_not_load_graphify_maintenance(self):
        batch = reader.load_batch(ROOT, reader.BOOTSTRAP, auto=True)
        self.assertNotIn(PROCEDURE, [part["path"] for part in batch["sections"]])

    def run_destination_preflight(self, root, override=None):
        source = (ROOT / PROCEDURE).read_text()
        block = re.search(r"```python\n(.*?)\n\s*```", source, re.S)
        self.assertIsNotNone(block, "missing executable destination preflight")
        env = dict(os.environ)
        env.pop("GRAPHIFY_OUT", None)
        if override is not None:
            env["GRAPHIFY_OUT"] = str(override)
        return subprocess.run([sys.executable, "-c", textwrap.dedent(block[1])], cwd=root, env=env,
                              capture_output=True, text=True, timeout=10)

    def test_preflight_accepts_only_the_primary_checkout_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for override in (None, "", "graphify-out", root / "graphify-out"):
                with self.subTest(override=override):
                    result = self.run_destination_preflight(root, override)
                    self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(root.iterdir()), [])

    def test_preflight_rejects_another_worktree_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "primary"
            other = Path(directory) / "occupied-worktree"
            root.mkdir()
            other.mkdir()
            marker = other / "graph.json"
            marker.write_bytes(b"other writer's graph")
            for override in (other, "../occupied-worktree", "custom-output"):
                with self.subTest(override=override):
                    result = self.run_destination_preflight(root, override)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(marker.read_bytes(), b"other writer's graph")
                    self.assertEqual(list(root.iterdir()), [])
                    self.assertEqual(list(other.iterdir()), [marker])

    def test_preflight_rejects_a_symlinked_default_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "primary"
            other = Path(directory) / "occupied-worktree"
            root.mkdir()
            other.mkdir()
            (root / "graphify-out").symlink_to(other, target_is_directory=True)
            result = self.run_destination_preflight(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
