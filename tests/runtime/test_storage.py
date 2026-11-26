"""Private cache extraction must reject unsafe files and preserve atomic writes."""
import importlib
import json
import os
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

    def test_the_write_stays_strict_and_storable_only_reports_what_it_would_refuse(self):
        target = self.root / "state/cache.json"
        with self.storage._locked(target) as path:
            self.storage._write(path, dict(version=1, kept=True))
        decoded = json.loads('{"version":1,"hint":1e400}')  # valid JSON text that decodes as infinity
        self.assertEqual(decoded["hint"], float("inf"))
        self.assertFalse(self.storage.storable(decoded))
        self.assertFalse(self.storage.storable(dict(version=1, hint=float("nan"))))
        self.assertTrue(self.storage.storable(dict(version=1, hint=None)))
        with self.assertRaises(ValueError):
            self.storage._write(target, decoded)
        self.assertEqual(self.storage._read(target), dict(version=1, kept=True))
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

    def racing_open(self, target, action, *, before, times=1):
        """Patch `os.open` so `action` runs around the first `times` read-only opens of `target`: before the open (the
        file checked by name is no longer the one opened) or right after it (the opened file is already unlinked)."""
        real, seen = os.open, []

        def opened(path, flags, *args, **kwargs):
            racing = str(path) == str(target) and flags & os.O_ACCMODE == os.O_RDONLY and len(seen) < times
            if racing:
                seen.append(path)
            if racing and before:
                action()
            fd = real(path, flags, *args, **kwargs)
            if racing and not before:
                action()
            return fd
        return mock.patch.object(self.storage.os, "open", side_effect=opened), seen

    def replace(self, target, value):
        """What a concurrent writer does: a complete private file, renamed over the target."""
        fresh = target.parent / ".replacement"
        fresh.write_text(json.dumps(value))
        fresh.chmod(0o600)
        os.replace(fresh, target)

    def test_an_atomic_replacement_during_a_read_gives_the_new_file_not_a_refusal(self):
        target = self.root / "state/cache.json"
        with self.storage._locked(target) as path:
            self.storage._write(path, dict(version=1, generation=1))
        for before in (True, False):
            with self.subTest(before=before):
                self.replace(target, dict(version=1, generation=1))
                patched, seen = self.racing_open(
                    target, lambda: self.storage._write(target, dict(version=1, generation=2)), before=before)
                with patched:
                    self.assertEqual(self.storage._read(target), dict(version=1, generation=2))
                self.assertEqual(len(seen), 1)
                self.assertEqual(self.storage._read(target), dict(version=1, generation=2))

    def test_a_replacement_by_an_unsafe_object_is_refused_without_reading_it(self):
        target = self.root / "state/cache.json"
        outside = self.root / "outside.json"
        cases = {}

        def public():
            self.replace(target, dict(version=1, unsafe=True))
            target.chmod(0o644)

        def linked():
            self.replace(target, dict(version=1, unsafe=True))
            (target.parent / "alias.json").hardlink_to(target)

        def symlinked():
            outside.write_text(json.dumps(dict(version=1, unsafe=True)))
            outside.chmod(0o600)
            target.unlink()
            target.symlink_to(outside)
        cases.update(public=public, linked=linked, symlinked=symlinked)
        for label, action in cases.items():
            for before in (True, False):
                with self.subTest(label=label, before=before):
                    for leftover in (target, target.parent / "alias.json"):
                        if leftover.is_symlink() or leftover.exists():
                            leftover.unlink()
                    with self.storage._locked(target) as path:
                        self.storage._write(path, dict(version=1, generation=1))
                    patched, _seen = self.racing_open(target, action, before=before)
                    with patched, self.assertRaises((ValueError, OSError)):
                        self.storage._read(target)

    def test_a_file_that_keeps_changing_is_refused_after_a_bounded_number_of_tries(self):
        target = self.root / "state/cache.json"
        with self.storage._locked(target) as path:
            self.storage._write(path, dict(version=1, generation=0))
        count = [0]

        def churn():
            count[0] += 1
            self.storage._write(target, dict(version=1, generation=count[0]))
        patched, seen = self.racing_open(target, churn, before=True, times=1000)
        with patched, self.assertRaisesRegex(ValueError, "observation_store_unsafe"):
            self.storage._read(target)
        self.assertLessEqual(len(seen), 4)
        self.assertGreaterEqual(len(seen), 2)

    def test_reader_lock_cannot_be_taken_twice(self):
        settings = self.root / "state/router.json"
        with self.processes._pool_lock(settings, "invented", 0) as first:
            with self.processes._pool_lock(settings, "invented", 0) as second:
                self.assertTrue(first)
                self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
