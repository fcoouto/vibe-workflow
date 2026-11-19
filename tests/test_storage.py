"""Private cache extraction must reject unsafe files and preserve atomic writes."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        try:
            self.storage = importlib.import_module("runtime.storage")
            self.processes = importlib.import_module("runtime.processes")
        except ImportError:
            self.fail("standalone private-storage helpers are missing")

    def test_absent_reads_never_create_a_directory(self):
        target = self.root / "absent/cache.json"
        self.assertEqual(self.storage._read(target), {})
        self.assertFalse(target.parent.exists())

    def test_private_atomic_roundtrip_and_failed_replace_preserves_previous_data(self):
        target = self.root / "state/cache.json"
        with self.storage._locked(target) as path:
            self.storage._write(path, dict(version=1, previous=True))
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with mock.patch("runtime.storage.os.replace", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                self.storage._write(target, dict(version=1, changed=True))
        self.assertEqual(self.storage._read(target), dict(version=1, previous=True))
        self.assertEqual([p.name for p in target.parent.iterdir() if p.name.startswith(".observations-")], [])

    def test_symlink_hardlink_and_public_files_are_refused(self):
        original = self.root / "original.json"
        original.write_text('{"version":1}')
        original.chmod(0o600)
        alias = self.root / "alias.json"
        alias.symlink_to(original)
        with self.assertRaises(ValueError):
            self.storage._read(alias)
        alias.unlink()
        alias.hardlink_to(original)
        with self.assertRaises(ValueError):
            self.storage._read(alias)
        alias.unlink()
        original.chmod(0o644)
        with self.assertRaises(ValueError):
            self.storage._read(original)

    def test_parent_symlink_cannot_redirect_a_cache(self):
        actual = self.root / "actual"
        actual.mkdir(mode=0o700)
        link = self.root / "link"
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.storage._read(link / "cache.json")

    def test_oversized_and_malformed_files_fail_closed(self):
        target = self.root / "cache.json"
        for content in ("not json", "x" * ((1 << 20) + 1)):
            target.write_text(content)
            target.chmod(0o600)
            with self.assertRaises(ValueError):
                self.storage._read(target)

    def test_reader_lock_cannot_be_taken_twice(self):
        settings = self.root / "state/router.json"
        with self.processes._pool_lock(settings, "invented", 0) as first:
            with self.processes._pool_lock(settings, "invented", 0) as second:
                self.assertTrue(first)
                self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
