"""User-wide naming reservations do not grant workflow authority."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'workstreams_family.py'


class WorkstreamsFamilyTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('workstreams_family', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Path(temporary.name) / 'shared' / 'families.sqlite3'

    def reserve(self, parent):
        return self.module.reserve(self.store, 'codex', parent)

    def test_same_parent_keeps_code_across_project_moves_and_restarts(self):
        first = self.reserve('parent-one')
        self.assertEqual(first, 'W1')
        self.assertEqual(self.reserve('parent-one'), first)
        self.assertEqual(self.reserve('parent-two'), 'W2')
        self.assertEqual(self.module.reserve(self.store, 'other-client', 'parent-one'), 'W3')

    def test_concurrent_parents_get_unique_codes_and_retries_reuse_them(self):
        parents = ['parent-' + str(i) for i in range(12)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(self.reserve, parents))
        self.assertEqual(len(set(codes)), len(parents))
        for parent, code in zip(parents, codes):
            self.assertEqual(self.reserve(parent), code)

    def test_invalid_identity_does_not_create_storage(self):
        for client, parent in [('', 'p'), ('codex', ''), ('codex', 'p\n'), ('codex', 'x' * 513)]:
            with self.subTest(client=client, parent=parent), self.assertRaises(ValueError):
                self.module.reserve(self.store, client, parent)
        self.assertFalse(self.store.exists())

    def test_corrupt_store_fails_without_replacing_existing_bytes(self):
        self.store.parent.mkdir()
        self.store.write_bytes(b'not a SQLite database')
        with self.assertRaises(self.module.sqlite3.DatabaseError):
            self.reserve('parent')
        self.assertEqual(self.store.read_bytes(), b'not a SQLite database')

    def test_cli_uses_one_user_home_across_project_directories(self):
        home = self.store.parent.parent
        results = []
        for project in ('project-one', 'project-two'):
            cwd = home / project
            cwd.mkdir()
            result = subprocess.run([sys.executable, '-I', '-B', str(SCRIPT), '--client', 'codex',
                '--parent', project], cwd=cwd, env=dict(os.environ, HOME=str(home)),
                capture_output=True, text=True, check=True)
            results.append(json.loads(result.stdout)['family'])
        self.assertEqual(results, ['W1', 'W2'])
        self.assertTrue((home / '.vibe-coding-workflow' / 'workstreams-families.sqlite3').is_file())
