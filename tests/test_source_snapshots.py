"""Acquisition-local policy/runtime source reads; real temporary files, no native client or network."""

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import startup


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.py'
        self.source.write_bytes(b'abc')

    def budget(self, **changes):
        return dict(paths=0, bytes=0, deadline=time.monotonic() + 10) | changes

    def test_repeated_source_is_read_once_and_charged_once(self):
        budget = self.budget()
        opened, enabled = [], True

        def audit(event, arguments):
            if enabled and event == 'open' and arguments[0] == str(self.source):
                opened.append(arguments[0])

        sys.addaudithook(audit)
        try:
            self.assertEqual(startup._budgeted_source_read(self.source, budget), b'abc')
            self.assertEqual(startup._budgeted_source_read(self.source, budget), b'abc')
            self.assertEqual(budget['bytes'], 3)
            self.assertEqual(budget['paths'], 2)
            self.assertEqual(len(opened), 1)
        finally:
            enabled = False

    def test_independent_budgets_do_not_share_snapshots(self):
        first = self.budget()
        self.assertEqual(startup._budgeted_source_read(self.source, first), b'abc')
        self.source.write_bytes(b'xyz')
        second = self.budget()
        self.assertEqual(startup._budgeted_source_read(self.source, second), b'xyz')
        self.assertEqual(second['bytes'], 3)
        with self.assertRaises(startup.StartupError):
            startup._budgeted_source_read(self.source, first)

    def test_same_size_edit_with_restored_mtime_is_rejected(self):
        budget = self.budget()
        startup._budgeted_source_read(self.source, budget)
        before = self.source.stat()
        self.source.write_bytes(b'xyz')
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(startup.StartupError):
            startup._budgeted_source_read(self.source, budget)
        self.assertGreaterEqual(budget['bytes'], startup.PACKAGE_SCAN_MAX_BYTES)

    def test_replacement_deletion_and_mode_changes_are_not_cached_success(self):
        for change in ('replace', 'delete', 'mode'):
            with self.subTest(change=change):
                self.source.write_bytes(b'abc')
                self.source.chmod(0o600)
                budget = self.budget()
                startup._budgeted_source_read(self.source, budget)
                if change == 'replace':
                    replacement = self.root / 'replacement.py'
                    replacement.write_bytes(b'abc')
                    replacement.replace(self.source)
                elif change == 'delete':
                    self.source.unlink()
                else:
                    self.source.chmod(0o400)
                with self.assertRaises((OSError, startup.StartupError)):
                    startup._budgeted_source_read(self.source, budget)
                self.assertGreaterEqual(budget['bytes'], startup.PACKAGE_SCAN_MAX_BYTES)

    def test_cache_hits_still_enforce_deadline_and_path_budget(self):
        for exhausted in ('deadline', 'paths'):
            with self.subTest(exhausted=exhausted):
                budget = self.budget()
                startup._budgeted_source_read(self.source, budget)
                if exhausted == 'deadline':
                    budget['deadline'] = time.monotonic() - 1
                else:
                    budget['paths'] = startup.PACKAGE_SCAN_MAX_PATHS
                with self.assertRaises(startup.StartupError):
                    startup._budgeted_source_read(self.source, budget)

    def test_cache_hit_cannot_rescue_an_exhausted_byte_budget(self):
        budget = self.budget()
        startup._budgeted_source_read(self.source, budget)
        budget['bytes'] = 8 * 1024 * 1024
        with self.assertRaises(startup.StartupError):
            startup._budgeted_source_read(self.source, budget)

    def test_eight_mib_bound_is_not_increased_or_charged_twice(self):
        budget = self.budget(bytes=8 * 1024 * 1024 - 4)
        self.assertEqual(startup._budgeted_source_read(self.source, budget), b'abc')
        self.assertEqual(startup._budgeted_source_read(self.source, budget), b'abc')
        self.assertEqual(budget['bytes'], 8 * 1024 * 1024 - 1)
        with self.assertRaises(startup.StartupError):
            startup._budgeted_source_read(self.source, self.budget(bytes=8 * 1024 * 1024 - 3))

    def test_first_read_keeps_the_existing_one_mib_file_limit(self):
        self.source.write_bytes(b'x' * (1024 * 1024 + 1))
        with self.assertRaises(startup.StartupError):
            startup._budgeted_source_read(self.source, self.budget())

    def test_symlink_is_rejected_on_first_read_and_cache_reuse(self):
        link = self.root / 'link.py'
        link.symlink_to(self.source)
        with self.assertRaises((OSError, startup.StartupError)):
            startup._budgeted_source_read(link, self.budget())
        budget = self.budget()
        startup._budgeted_source_read(self.source, budget)
        target = self.root / 'target.py'
        self.source.rename(target)
        self.source.symlink_to(target)
        with self.assertRaises((OSError, startup.StartupError)):
            startup._budgeted_source_read(self.source, budget)

    def test_file_change_during_initial_read_is_rejected(self):
        original = os.fdopen
        source = self.source

        class ChangedReader:
            def __init__(self, descriptor, *args, **kwargs):
                self.stream = original(descriptor, *args, **kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def read(self, limit):
                raw = self.stream.read(limit)
                source.write_bytes(b'xyz')
                return raw

            def fileno(self):
                return self.stream.fileno()

        with patch('os.fdopen', new=ChangedReader), self.assertRaises(startup.StartupError):
            startup._budgeted_source_read(self.source, self.budget())

    def test_budget_free_reads_remain_uncached(self):
        self.assertEqual(startup._budgeted_source_read(self.source, None), b'abc')
        self.source.write_bytes(b'xyz')
        self.assertEqual(startup._budgeted_source_read(self.source, None), b'xyz')


if __name__ == '__main__':
    unittest.main()
