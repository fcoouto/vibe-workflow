"""Graphify maintenance is reachable selectively, without loading at startup."""

from pathlib import Path
import re
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


if __name__ == "__main__":
    unittest.main()
