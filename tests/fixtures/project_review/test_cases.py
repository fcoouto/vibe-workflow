"""Keep the raw D10c inputs fresh, deliberately stale and parseable."""

import ast
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class ProjectReviewFixtureTests(unittest.TestCase):
    def shortlist(self, case):
        return json.loads((ROOT / f"{case}-shortlist.json").read_text())["candidates"]

    def test_fresh_shortlist_matches_three_current_candidates_in_two_repositories(self):
        entries = self.shortlist("fresh")
        self.assertEqual(len(entries), 3)
        self.assertEqual({item["repository"] for item in entries}, {"alpha", "beta"})
        for item in entries:
            raw = (ROOT / "targets/fresh/umbrella" / item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
            self.assertEqual(len(raw.splitlines()), item["lines"])
            self.assertFalse(item["structurally_inspected"])

    def test_stale_shortlist_has_one_changed_candidate_and_one_missing_candidate(self):
        changed, missing = self.shortlist("stale")
        umbrella = ROOT / "targets/stale/umbrella"
        self.assertNotEqual(hashlib.sha256((umbrella / changed["path"]).read_bytes()).hexdigest(),
                            changed["sha256"])
        self.assertFalse((umbrella / missing["path"]).exists())
        self.assertTrue((umbrella / "beta/src/cache/store.py").is_file())
        self.assertNotIn("beta/src/cache/store.py", [item["path"] for item in (changed, missing)])

    def test_source_inputs_are_parseable_without_executing_target_code(self):
        sources = list((ROOT / "targets").rglob("*.py"))
        self.assertEqual(len(sources), 8)
        for source in sources:
            with self.subTest(source=source.relative_to(ROOT)):
                ast.parse(source.read_text(), filename=str(source))


if __name__ == "__main__":
    unittest.main()
