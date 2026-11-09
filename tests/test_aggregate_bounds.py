"""Only onboarding's project inventory follows MAX_TOTAL; other aggregates keep 8 MiB. Temporary files only."""
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime import changes, evidence, onboarding, review_storage
from runtime.startup import StartupError
from runtime.state import StateError


MEBIBYTE = 1024 * 1024


class AggregateBoundTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="vibe-bounds-")
        self.addCleanup(temp.cleanup)
        self.context = SimpleNamespace(directory=Path(temp.name).resolve())
        (self.context.directory / "evidence").mkdir(mode=0o700)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("create table evidence_metadata(name text, digest text)")

    def artifacts(self, *names):
        for name in names:
            self.db.execute("insert into evidence_metadata values (?, ?)", (name, "d" * 64))
            (self.context.directory / "evidence" / name).write_bytes(b"")

    def test_bounds(self):
        self.assertEqual(onboarding.MAX_TOTAL, 16 * MEBIBYTE)
        self.assertEqual(onboarding.LEGACY_MAX_TOTAL, 8 * MEBIBYTE)

    def test_birth_artifact_inventory_keeps_eight_mebibytes(self):
        self.artifacts("a", "b")
        sizes = {"a": 4 * MEBIBYTE, "b": 4 * MEBIBYTE}
        with patch.object(changes.task_state, "_metadata", side_effect=lambda db, name: {"bytes": sizes[name]}), \
                patch.object(evidence, "_managed_artifact") as managed:
            changes._birth_store_inventory(self.context, self.db)
            self.assertEqual(managed.call_count, 2)
            sizes["b"] += 1
            with self.assertRaisesRegex(StartupError, "byte bound exceeded"):
                changes._birth_store_inventory(self.context, self.db)

    def test_review_storage_owner_budget_keeps_eight_mebibytes(self):
        self.artifacts("owner")
        owner, target = {"kind": "temporary", "created_at": 0}, {"path": "/project"}
        content = dict(provenance={"name": "provenance"}, owner=owner, created_at=0, kind="temporary-review")
        metadata = dict(bytes=100, owner=owner, created_at=0, targets=[target], dependencies=[],
                        closed_at=None, export=None, **{"class": "compact"})
        config = {"provenance": {"name": "provenance"}}
        with patch.object(review_storage, "_provenance"), \
                patch.object(review_storage.state, "_metadata", return_value=metadata), \
                patch.object(review_storage.evidence, "_read", return_value=content), \
                patch.object(review_storage, "_owner_targets", return_value=[target]):
            budget = {"paths": 0, "bytes": 8 * MEBIBYTE - 100, "deadline": time.monotonic() + 10}
            self.assertEqual(len(review_storage._owners(config, self.context, self.db, budget=budget)), 1)
            budget = {"paths": 0, "bytes": 8 * MEBIBYTE - 99, "deadline": time.monotonic() + 10}
            with self.assertRaises(StateError):
                review_storage._owners(config, self.context, self.db, budget=budget)
            self.assertEqual(budget["bytes"], 8 * MEBIBYTE + 1)


if __name__ == "__main__":
    unittest.main()
