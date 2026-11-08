"""Check raw probe inputs; these checks do not establish native review behavior."""

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent / "fixtures/project_review/grouped_selection"
GROUPS = {
    "alpha": {"src/orders/application", "src/orders/adapters", "src/payments", "src/reporting"},
    "beta": {"src/cache", "src/transport/http", "src/transport/queue", "src/metrics"},
}


class GroupedSelectionFixtureTests(unittest.TestCase):
    def setUp(self):
        self.entries = json.loads((ROOT / "shortlist.json").read_text())["candidates"]

    def test_inventory_has_distinct_groups_exclusions_and_missing_paths(self):
        self.assertEqual(len(self.entries), 48)
        self.assertEqual(len({row["path"] for row in self.entries}), 48)
        self.assertEqual(Counter(row["repository"] for row in self.entries),
                         {"alpha": 24, "beta": 24})
        for repository, expected_groups in GROUPS.items():
            groups = Counter()
            excluded = missing = 0
            for row in self.entries:
                if row["repository"] != repository:
                    continue
                path = Path(row["path"])
                self.assertEqual(path.parts[0], repository)
                self.assertNotIn("..", path.parts)
                self.assertFalse(row["structurally_inspected"])
                if path.parts[1] in {"generated", "vendor"}:
                    excluded += 1
                    self.assertTrue((ROOT / "targets" / path).is_file())
                elif not (ROOT / "targets" / path).exists():
                    missing += 1
                    self.assertIsNone(row["lines"])
                else:
                    groups[str(path.relative_to(repository).parent)] += 1
            self.assertEqual(groups, {name: 5 for name in expected_groups})
            self.assertEqual((excluded, missing), (3, 1))

    def test_existing_candidates_match_inventory_without_executing_them(self):
        checked = 0
        for row in self.entries:
            path = ROOT / "targets" / row["path"]
            if not path.is_file():
                continue
            raw = path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])
            self.assertEqual(len(raw.splitlines()), row["lines"])
            checked += 1
        self.assertEqual(checked, 46)
        sources = list((ROOT / "targets").rglob("*.py"))
        self.assertEqual(len(sources), 47)
        for source in sources:
            ast.parse(source.read_text(), filename=str(source))

    def test_selected_samples_are_current_and_leave_gamma_outside_shortlist(self):
        repositories = json.loads((ROOT / "repositories.json").read_text())["repositories"]
        self.assertEqual([(row["id"], row["name"]) for row in repositories],
                         [(1, "alpha"), (2, "beta"), (3, "gamma")])
        samples = json.loads((ROOT / "cases.json").read_text())["grouping"]["samples"]
        self.assertEqual(len(set(samples)), 4)
        self.assertEqual(Counter(Path(name).parts[0] for name in samples),
                         {"alpha": 2, "beta": 2})
        for name in samples:
            self.assertIn(name, {row["path"] for row in self.entries})
            self.assertTrue((ROOT / "targets" / name).is_file())
            self.assertNotIn(Path(name).parts[1], {"generated", "vendor"})
        self.assertTrue((ROOT / "targets/gamma/private_scope_marker.py").is_file())


if __name__ == "__main__":
    unittest.main()
