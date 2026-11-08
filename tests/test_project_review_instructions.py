"""D10 uses prepared selective delivery, without loading review at startup."""

from pathlib import Path
import unittest

from scripts import read_instructions as reader


ROOT = Path(__file__).resolve().parents[1]
ENTRY = "skills/review-my-project/SKILL.md"
PROCEDURE = "skills/review-my-project/references/procedure.md"
FOCUSED = "skills/review-my-project/references/source-structure.md"


class ProjectReviewInstructionsTests(unittest.TestCase):
    def delivered(self, paths):
        first = reader.load_batch(ROOT, paths, auto=True)
        batch = first
        sources = {}
        while True:
            for section in batch["sections"]:
                sources.setdefault(section["path"], "")
                sources[section["path"]] += section["text"]
            if batch["last_page"] == batch["pages"]:
                break
            batch = reader.load_batch(ROOT, paths, batch["last_page"] + 1,
                                      first["revision"], auto=True)
        self.assertEqual(list(sources), paths)
        for name, text in sources.items():
            self.assertEqual(text, (ROOT / name).read_text())
        return sources

    def test_selected_review_delivers_complete_procedure_and_security_contract(self):
        sources = self.delivered([ENTRY, PROCEDURE, "policies/05-tests-and-builds.md",
                                  "policies/04-security-review.md"])
        self.assertNotIn(FOCUSED, sources)
        self.assertNotIn("clients/runtime.md", sources)

    def test_focused_analysis_delivers_its_separate_reference(self):
        self.delivered([ENTRY, PROCEDURE, FOCUSED])

    def test_startup_does_not_deliver_review_or_managed_runtime(self):
        sources = self.delivered(reader.BOOTSTRAP)
        self.assertNotIn(ENTRY, sources)
        self.assertNotIn(PROCEDURE, sources)
        self.assertNotIn("clients/runtime.md", sources)


if __name__ == "__main__":
    unittest.main()
