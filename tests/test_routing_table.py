"""S2.3: the maintenance refresh writes the strategy table `scripts/routing.py` reads."""
import io
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request

from runtime import routing_evidence as evidence, usage_credentials
from scripts import routing as command, routing_table
from tests.test_routing_evidence import NoHardCodedModelTests, inputs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'routing_table.py'
ROUTING = ROOT / 'scripts' / 'routing.py'
NAMES = ('guidance', 'catalog', 'benchmarks')


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.settings = self.root / 'routing' / 'settings.json'
        self.table = self.settings.parent / 'strategy-table.json'
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.write_inputs(inputs())

    def tearDown(self):
        self.temp.cleanup()

    def write_inputs(self, values):
        for name, value in zip(NAMES, values):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            (self.evidence / (name + '.json')).write_bytes(data)

    def args(self, *extra):
        result = ['refresh', '--settings', str(self.settings)]
        for name in NAMES:
            result += ['--' + name, str(self.evidence / (name + '.json'))]
        return result + list(extra)

    def refresh(self, *extra, now=None):
        out = io.StringIO()
        with mock.patch.object(routing_table, '_now', return_value=now or '2026-11-10T00:00:00Z'), \
                mock.patch('sys.stdout', out):
            code = routing_table.main(self.args(*extra))
        return code, json.loads(out.getvalue())

    def stored(self):
        return json.loads(self.table.read_text())


class WriteTests(Base):
    def test_refresh_writes_a_private_compiled_table_atomically(self):
        code, report = self.refresh()
        self.assertEqual((code, report['state']), (0, 'refreshed'), report)
        info = self.table.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.table.parent.lstat().st_mode) & 0o077, 0)
        table = self.stored()
        expected = evidence.compile_table(*inputs(), now='2026-11-10T00:00:00Z')
        for field in ('schema', 'version', 'compiled_at', 'sources', 'rows'):
            self.assertEqual(table[field], expected[field], field)
        self.assertEqual(set(table['input_digests']), set(NAMES))
        self.assertTrue(all(len(value) == 64 for value in table['input_digests'].values()))
        self.assertEqual(report['version'], table['version'])
        self.assertEqual([p.name for p in self.table.parent.iterdir()], ['strategy-table.json'])  # no temp left

    def test_a_terms_file_left_in_the_sources_folder_is_ignored(self):
        self.assertEqual(routing_table.INPUTS, ('guidance', 'catalog', 'benchmarks'))
        (self.evidence / 'terms.json').write_text('{not json')
        code, report = self.refresh()
        self.assertEqual((code, report['state']), (0, 'refreshed'), report)
        self.assertEqual(set(self.stored()['input_digests']), set(NAMES))
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            code = routing_table.main(self.args('--terms', str(self.evidence / 'terms.json')))
        self.assertEqual((code, json.loads(out.getvalue())['reason']), (2, 'usage_error'))

    def test_reader_accepts_the_written_table(self):
        self.refresh()
        service = command.Service(self.settings, self.root / 'usage' / 'ledger.sqlite3')
        try:
            table, problem = service._table()
        finally:
            service.close()
        self.assertIsNone(problem)
        self.assertEqual(table['version'], self.stored()['version'])

    def test_explicit_table_path_is_honored(self):
        other = self.root / 'other' / 'table.json'
        code, report = self.refresh('--table', str(other))
        self.assertEqual(code, 0, report)
        self.assertTrue(other.exists())
        self.assertFalse(self.table.exists())

    def test_interrupted_write_leaves_the_previous_table_and_no_temp_file(self):
        self.refresh(now='2026-11-01T00:00:00Z')
        before = self.table.read_bytes()
        with mock.patch.object(routing_table.os, 'replace', side_effect=OSError('disk')):
            code, report = self.refresh()
        self.assertEqual((code, report['state'], report['reason']), (1, 'blocked', 'write_failed'))
        self.assertEqual(self.table.read_bytes(), before)
        self.assertEqual([p.name for p in self.table.parent.iterdir()], ['strategy-table.json'])

    def test_folder_sync_failure_after_the_rename_still_reports_the_written_table(self):
        real_fsync = os.fsync

        def fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('folder sync')
            return real_fsync(fd)
        with mock.patch.object(routing_table.os, 'fsync', fsync):
            code, report = self.refresh()
        self.assertEqual((code, report['state'], report['warnings']), (0, 'refreshed', ['dir_sync_failed']))
        self.assertEqual(self.stored()['version'], report['version'])
        self.assertNotIn('warnings', self.refresh()[1])

    def test_a_failed_temp_file_open_removes_the_temp_file(self):
        with mock.patch.object(routing_table.os, 'fdopen', side_effect=OSError('fdopen')):
            code, report = self.refresh()
        self.assertEqual((code, report['reason']), (1, 'write_failed'))
        self.assertEqual(list(self.table.parent.iterdir()), [])

    def test_refuses_symlink_or_non_regular_targets_and_shared_folders(self):
        self.refresh()
        real = self.root / 'elsewhere.json'
        real.write_text('{}')
        real.chmod(0o600)
        self.table.unlink()
        self.table.symlink_to(real)
        code, report = self.refresh()
        self.assertEqual((code, report['reason']), (1, 'unsafe_table_path'))
        self.assertEqual(real.read_text(), '{}')
        self.assertTrue(self.table.is_symlink())
        self.table.unlink()
        self.table.mkdir()
        self.assertEqual(self.refresh()[1]['reason'], 'unsafe_table_path')
        self.table.rmdir()
        self.table.parent.chmod(0o755)
        self.assertEqual(self.refresh()[1]['reason'], 'unsafe_table_path')
        self.assertFalse(self.table.exists())

    def test_refuses_a_symlinked_settings_folder(self):
        real = self.root / 'real-routing'
        real.mkdir(mode=0o700)
        link = self.root / 'linked'
        link.symlink_to(real)
        code, report = self.refresh('--table', str(link / 'nested' / 'strategy-table.json'))
        self.assertEqual(code, 0, report)  # a symlinked ancestor resolves, exactly as the reader resolves it
        self.assertTrue((real / 'nested' / 'strategy-table.json').exists())


class FailureTests(Base):
    def test_failed_refresh_keeps_the_last_verified_table_with_its_original_age(self):
        self.refresh(now='2026-11-01T00:00:00Z')
        first = self.stored()
        (self.evidence / 'catalog.json').unlink()
        code, report = self.refresh(now='2026-11-09T00:00:00Z')
        self.assertEqual((code, report['state']), (1, 'kept'), report)
        kept = self.stored()
        self.assertTrue(kept['refresh_failed'])
        self.assertEqual((kept['version'], kept['compiled_at'], kept['rows']),
                         (first['version'], '2026-11-01T00:00:00Z', first['rows']))
        self.assertEqual(kept['input_digests'], first['input_digests'])
        self.write_inputs(inputs())
        code, report = self.refresh(now='2026-11-10T00:00:00Z')
        self.assertEqual((code, report['state']), (0, 'refreshed'))
        self.assertNotIn('refresh_failed', self.stored())
        self.assertEqual(self.stored()['compiled_at'], '2026-11-10T00:00:00Z')

    def test_first_failure_without_a_table_writes_nothing(self):
        (self.evidence / 'benchmarks.json').write_text('{not json')
        code, report = self.refresh()
        self.assertEqual((code, report['state'], report['reason']), (1, 'blocked', 'no_verified_table'))
        self.assertFalse(self.table.exists())

    def test_hostile_inputs_keep_the_previous_table(self):
        self.refresh(now='2026-11-01T00:00:00Z')
        good = self.stored()
        cases = {
            'huge': lambda v: v.__setitem__(1, b'[' + b' ' * (routing_table.MAX_INPUT + 1) + b']'),
            'nan': lambda v: v.__setitem__(1, b'[{"model": "a", "supported_efforts": [], "context_limit": NaN}]'),
            'infinity': lambda v: v.__setitem__(1, b'[{"model": "a", "supported_efforts": [], "context_limit": 1e999}]'),
            'duplicate key': lambda v: v.__setitem__(1, b'[{"model": "a", "model": "b", "supported_efforts": []}]'),
            'nested': lambda v: v.__setitem__(2, b'[' * 100000 + b']' * 100000),
            'not utf-8': lambda v: v.__setitem__(2, b'\xff\xfe'),
            'wrong top type': lambda v: v.__setitem__(0, {'rows': []}),
            'wrong field type': lambda v: v[1][0].__setitem__('supported_efforts', 'high'),
            'bool limit': lambda v: v[1][0].__setitem__('context_limit', True),
            'bad band': lambda v: v[0][0]['rows'][0].__setitem__('risk_band', 'enormous'),
        }
        for label, mutate in cases.items():
            with self.subTest(label):
                values = inputs()
                mutate(values)
                self.write_inputs(values)
                code, report = self.refresh(now='2026-11-09T00:00:00Z')
                self.assertEqual((code, report['state']), (1, 'kept'), report)
                kept = self.stored()
                self.assertEqual((kept['version'], kept['compiled_at']), (good['version'], good['compiled_at']))

    def test_a_fifo_input_is_refused_without_waiting(self):
        self.refresh(now='2026-11-01T00:00:00Z')
        fifo = self.evidence / 'benchmarks.json'
        fifo.unlink()
        os.mkfifo(fifo)
        code, report = self.refresh(now='2026-11-09T00:00:00Z')
        self.assertEqual((code, report['state']), (1, 'kept'))

    def refresh_with(self, name, path, now='2026-11-09T00:00:00Z'):
        args = self.args()
        args[args.index('--' + name) + 1] = str(path)
        out = io.StringIO()
        with mock.patch.object(routing_table, '_now', return_value=now), mock.patch('sys.stdout', out):
            code = routing_table.main(args)
        return code, json.loads(out.getvalue())

    def test_a_symlinked_input_is_refused(self):
        real = self.root / 'real-benchmarks.json'
        real.write_bytes((self.evidence / 'benchmarks.json').read_bytes())
        link = self.root / 'linked-benchmarks.json'
        link.symlink_to(real)
        self.assertEqual(self.refresh_with('benchmarks', link), (1, dict(v=1, state='blocked', reason='unsafe_input')))
        self.assertFalse(self.table.exists())
        self.refresh(now='2026-11-01T00:00:00Z')
        code, report = self.refresh_with('benchmarks', link)
        self.assertEqual((code, report['state'], report['reason']), (1, 'kept', 'unsafe_input'))
        self.assertEqual(self.stored()['compiled_at'], '2026-11-01T00:00:00Z')

    def test_a_device_input_is_refused_before_it_is_opened(self):
        opened = []
        real_open = os.open

        def spy(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)
        with mock.patch.object(routing_table.os, 'open', spy):
            code, report = self.refresh_with('catalog', os.devnull)
        self.assertEqual((code, report['reason']), (1, 'unsafe_input'))
        self.assertNotIn(os.devnull, opened)

    def test_inputs_open_without_following_a_swapped_in_link(self):
        flags = []
        real_open = os.open

        def spy(path, flag, *args, **kwargs):
            if str(path).startswith(str(self.evidence)):
                flags.append(flag)
            return real_open(path, flag, *args, **kwargs)
        with mock.patch.object(routing_table.os, 'open', spy):
            self.assertEqual(self.refresh()[0], 0)
        self.assertEqual(len(flags), len(NAMES))
        self.assertTrue(all(flag & os.O_NOFOLLOW and flag & os.O_NONBLOCK for flag in flags))

    def test_previous_table_read_is_bounded(self):
        self.refresh(now='2026-11-01T00:00:00Z')
        with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded read')):
            code, report = self.refresh()
        self.assertEqual((code, report['state']), (0, 'refreshed'))

    def test_oversized_compiled_table_is_not_written(self):
        self.refresh(now='2026-11-01T00:00:00Z')
        good = self.stored()
        larger = inputs()
        larger[2].append(dict(larger[2][0], id='bench-extra', sha256='e' * 64))
        self.write_inputs(larger)
        before = self.table.read_bytes()
        with mock.patch.object(command, 'MAX_TABLE', len(before)):  # not even the failure mark fits
            code, report = self.refresh(now='2026-11-09T00:00:00Z')
        self.assertEqual((code, report['state']), (1, 'kept'))
        self.assertEqual(self.table.read_bytes(), before)
        with mock.patch.object(command, 'MAX_TABLE', len(before) + 64):
            code, report = self.refresh(now='2026-11-09T00:00:00Z')
        self.assertEqual((code, report['state']), (1, 'kept'))
        kept = self.stored()
        self.assertTrue(kept['refresh_failed'])
        self.assertEqual((kept['rows'], kept['compiled_at']), (good['rows'], good['compiled_at']))

    def test_an_unreadable_previous_table_is_not_treated_as_verified(self):
        self.table.parent.mkdir(mode=0o700, parents=True)
        self.table.write_text('{"rows": NaN}')
        self.table.chmod(0o600)
        (self.evidence / 'catalog.json').unlink()
        self.assertEqual(self.refresh()[1]['reason'], 'no_verified_table')
        self.write_inputs(inputs())
        self.assertEqual(self.refresh()[1]['state'], 'refreshed')

    def test_report_never_echoes_evidence_content(self):
        values = inputs()
        values[1] = b'"SECRET-CATALOG-TEXT'
        self.write_inputs(values)
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            routing_table.main(self.args())
        self.assertNotIn('SECRET', out.getvalue())


class PreviousTableTests(Base):
    """C.4 rollback: a successful refresh keeps one previous version; `rollback` swaps it into place."""
    def setUp(self):
        super().setUp()
        self.previous = self.table.parent / 'strategy-table.previous.json'

    def change_inputs(self):
        values = inputs()
        values[2].append(dict(values[2][0], id='bench-extra', sha256='e' * 64))
        self.write_inputs(values)

    def two_versions(self):
        self.refresh(now='2026-11-01T00:00:00Z')
        old = self.table.read_bytes()
        self.change_inputs()
        code, report = self.refresh(now='2026-11-09T00:00:00Z')
        self.assertEqual((code, report['state']), (0, 'refreshed'), report)
        return old, self.table.read_bytes()

    def rollback(self, *extra):
        out = io.StringIO()
        with mock.patch('sys.stdout', out):
            code = routing_table.main(['rollback', '--settings', str(self.settings), *extra])
        return code, json.loads(out.getvalue())

    def test_first_refresh_keeps_no_previous_version(self):
        self.refresh()
        self.assertFalse(os.path.lexists(self.previous))

    def test_successful_refresh_keeps_the_replaced_table_unchanged_and_private(self):
        old, new = self.two_versions()
        self.assertNotEqual(old, new)
        self.assertEqual(self.previous.read_bytes(), old)  # original version and compiled_at
        self.assertEqual(json.loads(old)['compiled_at'], '2026-11-01T00:00:00Z')
        info = self.previous.lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.table.parent.iterdir()),
                         ['strategy-table.json', 'strategy-table.previous.json'])

    def test_only_one_previous_version_is_kept(self):
        _, middle = self.two_versions()
        self.write_inputs(inputs())
        self.refresh(now='2026-11-10T00:00:00Z')
        self.assertEqual(self.previous.read_bytes(), middle)
        self.assertEqual(len(list(self.table.parent.iterdir())), 2)

    def test_same_version_or_failed_refresh_leaves_the_previous_version(self):
        old, new = self.two_versions()
        code, report = self.refresh(now='2026-11-10T00:00:00Z')  # same evidence, same version
        self.assertEqual((code, report['state']), (0, 'refreshed'))
        self.assertEqual(self.previous.read_bytes(), old)
        (self.evidence / 'catalog.json').unlink()
        self.assertEqual(self.refresh()[1]['state'], 'kept')
        self.assertEqual(self.previous.read_bytes(), old)

    def test_unverified_table_is_not_kept_as_previous(self):
        self.table.parent.mkdir(mode=0o700, parents=True)
        self.table.write_text('{"rows": NaN}')
        self.table.chmod(0o600)
        self.assertEqual(self.refresh()[1]['state'], 'refreshed')
        self.assertFalse(os.path.lexists(self.previous))

    def test_unsafe_previous_path_blocks_the_refresh_and_keeps_the_table(self):
        old = self.two_versions()[1]
        real = self.root / 'elsewhere.json'
        real.write_text('{}')
        real.chmod(0o600)
        self.previous.unlink()
        self.previous.symlink_to(real)
        self.write_inputs(inputs())
        code, report = self.refresh(now='2026-11-10T00:00:00Z')
        self.assertEqual((code, report['state'], report['reason']), (1, 'blocked', 'unsafe_table_path'))
        self.assertEqual(self.table.read_bytes(), old)
        self.assertEqual(real.read_text(), '{}')

    def test_failed_previous_write_leaves_the_table(self):
        _, new = self.two_versions()
        self.write_inputs(inputs())
        with mock.patch.object(routing_table.os, 'replace', side_effect=OSError('disk')):
            code, report = self.refresh(now='2026-11-10T00:00:00Z')
        self.assertEqual((code, report['reason']), (1, 'write_failed'))
        self.assertEqual(self.table.read_bytes(), new)
        self.assertEqual(len(list(self.table.parent.iterdir())), 2)  # no temp file

    def test_rollback_swaps_and_a_second_rollback_returns_to_the_newer_table(self):
        old, new = self.two_versions()
        code, report = self.rollback()
        self.assertEqual((code, report['state']), (0, 'rolled_back'), report)
        self.assertEqual(self.table.read_bytes(), old)
        self.assertEqual(self.previous.read_bytes(), new)
        self.assertEqual((report['version'], report['compiled_at'], report['replaced_version']),
                         (json.loads(old)['version'], '2026-11-01T00:00:00Z', json.loads(new)['version']))
        self.assertEqual(stat.S_IMODE(self.table.lstat().st_mode), 0o600)
        self.assertEqual(self.rollback()[0], 0)
        self.assertEqual((self.table.read_bytes(), self.previous.read_bytes()), (new, old))
        self.assertEqual(len(list(self.table.parent.iterdir())), 2)

    def test_rollback_honors_an_explicit_table_path(self):
        other = self.root / 'other' / 'table.json'
        self.refresh('--table', str(other), now='2026-11-01T00:00:00Z')
        old = other.read_bytes()
        self.change_inputs()
        self.refresh('--table', str(other), now='2026-11-09T00:00:00Z')
        self.assertTrue((other.parent / 'table.previous.json').exists())
        self.assertEqual(self.rollback('--table', str(other))[0], 0)
        self.assertEqual(other.read_bytes(), old)

    def test_previous_name_keeps_the_table_suffix(self):
        other = self.root / 'other' / 'table'
        self.refresh('--table', str(other), now='2026-11-01T00:00:00Z')
        old = other.read_bytes()
        self.change_inputs()
        self.refresh('--table', str(other), now='2026-11-09T00:00:00Z')
        self.assertEqual(sorted(p.name for p in other.parent.iterdir()), ['table', 'table.previous'])
        self.assertEqual((other.parent / 'table.previous').read_bytes(), old)
        self.assertEqual(self.rollback('--table', str(other))[0], 0)
        self.assertEqual(other.read_bytes(), old)

    def test_rollback_refuses_a_world_readable_previous_version(self):
        old, new = self.two_versions()
        self.previous.chmod(0o644)
        self.assertEqual(self.rollback(), (1, dict(v=1, state='blocked', reason='unsafe_previous_table')))
        self.assertEqual((self.table.read_bytes(), self.previous.read_bytes()), (new, old))

    def test_rollback_without_a_previous_version_is_refused(self):
        self.assertEqual(self.rollback(), (1, dict(v=1, state='blocked', reason='no_previous_table')))
        self.refresh()
        before = self.table.read_bytes()
        self.assertEqual(self.rollback()[1]['reason'], 'no_previous_table')
        self.assertEqual(self.table.read_bytes(), before)

    def test_rollback_refuses_an_unsafe_or_invalid_previous_version(self):
        old, new = self.two_versions()
        real = self.root / 'elsewhere.json'
        real.write_bytes(old)
        real.chmod(0o600)
        self.previous.unlink()
        self.previous.symlink_to(real)
        self.assertEqual(self.rollback()[1]['reason'], 'unsafe_previous_table')
        self.previous.unlink()
        os.link(real, self.previous)  # a hard link is shared, not private
        self.assertEqual(self.rollback()[1]['reason'], 'unsafe_previous_table')
        self.previous.unlink()
        os.mkfifo(self.previous, 0o600)
        self.assertEqual(self.rollback()[1]['reason'], 'unsafe_previous_table')
        self.previous.unlink()
        for label, data in (('nan', b'{"rows": NaN}'), ('shape', b'{"rows": []}'), ('utf', b'\xff')):
            with self.subTest(label):
                self.previous.write_bytes(data)
                self.previous.chmod(0o600)
                self.assertEqual(self.rollback()[1]['reason'], 'invalid_previous_table')
                self.previous.unlink()
        self.previous.write_bytes(old)
        self.previous.chmod(0o600)
        with mock.patch.object(command, 'MAX_TABLE', len(old) - 1):  # the reader limit applies to both files
            self.assertEqual(self.rollback()[1]['reason'], 'invalid_previous_table')
        self.assertEqual(self.table.read_bytes(), new)

    def test_rollback_refuses_an_unsafe_table_path(self):
        old, new = self.two_versions()
        self.table.chmod(0o644)
        self.assertEqual(self.rollback()[1]['reason'], 'unsafe_table_path')
        self.table.chmod(0o600)
        self.table.parent.chmod(0o755)
        self.assertEqual(self.rollback()[1]['reason'], 'unsafe_table_path')
        self.table.parent.chmod(0o700)
        self.assertEqual((self.table.read_bytes(), self.previous.read_bytes()), (new, old))

    def test_rollback_over_an_unverified_or_missing_table_installs_the_previous_version(self):
        old, _ = self.two_versions()
        self.table.write_text('{"rows": NaN}')
        code, report = self.rollback()
        self.assertEqual((code, report['state'], report['replaced_version'], report['warnings']),
                         (0, 'rolled_back', None, ['current_table_unverified_discarded']))
        self.assertEqual(self.table.read_bytes(), old)
        self.assertEqual(self.previous.read_bytes(), old)  # an unverified table is never kept
        self.table.unlink()
        code, report = self.rollback()
        self.assertEqual((code, report['state'], report['replaced_version']), (0, 'rolled_back', None))
        self.assertNotIn('warnings', report)  # nothing was discarded
        self.assertEqual(self.table.read_bytes(), old)

    def test_interrupted_rollback_never_leaves_no_table(self):
        old, new = self.two_versions()
        with mock.patch.object(routing_table.os, 'replace', side_effect=OSError('disk')):
            code, report = self.rollback()
        self.assertEqual((code, report['reason']), (1, 'write_failed'))
        self.assertEqual((self.table.read_bytes(), self.previous.read_bytes()), (new, old))
        real_replace, calls = os.replace, []

        def second_fails(src, dst):
            calls.append(dst)
            if len(calls) == 2:
                raise OSError('disk')
            return real_replace(src, dst)
        with mock.patch.object(routing_table.os, 'replace', second_fails):
            code, report = self.rollback()
        self.assertEqual((code, report['state'], report['warnings']), (0, 'rolled_back', ['newer_table_not_kept']))
        self.assertEqual(self.table.read_bytes(), old)  # the table is installed first
        self.assertEqual(len(list(self.table.parent.iterdir())), 2)

    def test_rollback_usage_errors(self):
        for argv in (['rollback', '--guidance', 'x'], ['rollback', '--table'], ['rollback', 'extra']):
            out = io.StringIO()
            with mock.patch('sys.stdout', out):
                self.assertEqual(routing_table.main(argv), 2, argv)
            self.assertEqual(json.loads(out.getvalue())['reason'], 'usage_error')


class LocalOnlyTests(Base):
    """RF5: the refresh reads and writes local files only."""
    def test_refresh_makes_no_network_subprocess_model_or_secret_store_call(self):
        forbidden = mock.Mock(side_effect=AssertionError('forbidden call'))
        patches = [mock.patch.object(socket, 'socket', forbidden),
                   mock.patch.object(socket, 'create_connection', forbidden),
                   mock.patch.object(subprocess, 'Popen', forbidden), mock.patch.object(subprocess, 'run', forbidden),
                   mock.patch.object(urllib.request, 'urlopen', forbidden),
                   mock.patch.object(usage_credentials, 'resolve_key', forbidden),
                   mock.patch.object(usage_credentials, '_read_key', forbidden),
                   mock.patch.object(usage_credentials, '_native_keychain', forbidden)]
        for patch in patches:
            patch.start()
        try:
            code, report = self.refresh()
            (self.evidence / 'catalog.json').unlink()
            self.refresh()
            with mock.patch('sys.stdout', io.StringIO()):
                routing_table.main(['rollback', '--settings', str(self.settings)])
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual((code, report['state']), (0, 'refreshed'))
        forbidden.assert_not_called()

    def test_command_names_no_commercial_model(self):
        text = SCRIPT.read_text()
        self.assertIsNone(NoHardCodedModelTests.PATTERN.search(text))
        self.assertNotRegex(text, r"['\"](xhigh|minimal)['\"]")

    def test_spawn_paths_do_not_run_the_refresh(self):
        for path in [ROOT / 'scripts' / 'routing.py', *sorted((ROOT / 'hooks').rglob('*.py')),
                     *sorted((ROOT / 'runtime').glob('*.py'))]:
            self.assertNotIn('routing_table', path.read_text(), path.name)


class RoundTripTests(Base):
    """Refresh, then a one-shot `routing.py prepare` ranks from the written table."""
    def write_evidence(self, digest='a'):
        guidance = [dict(id='g1', sha256=digest * 64, checked_at='2026-11-01T00:00:00Z', rows=[dict(
            role='worker', task_family='code', complexity_band='medium', risk_band='medium',
            strategies=[dict(model='m', effort='high', harness='h', evidence_refs=['g1'])])])]
        self.write_inputs([guidance, [dict(model='m', supported_efforts=['high'])], [], []])

    def prepare(self, ledger, strategy_id):
        choice = dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')
        setting = dict(v=1, op='set', expected_revision=0, change=dict(worker_choices=dict(implementer=[choice])))
        profile = dict(task_family='code', complexity_band='medium', risk_band='medium', review_policy='independent',
                       context_tokens=100, tools=['edit'], uncertainty='low')
        job = dict(id='job-1', role='worker', scope_ref='scope1', objective='o', context='c', profile=profile)
        route = dict(route='sub', strategy_id=strategy_id, provider='anthropic', model='m', effort='high', harness='h',
                     account='anthropic:acct', billing_class='included_subscription', pool='sub', demand={'5h': 10},
                     capable=True, permitted=True, binding_enforceable=True)
        window = dict(id='5h', limit=1000, used=100, length=18000, reset_at=None, rolling=True, fresh=True)
        observations = dict(now=int(__import__('time').time()), routes=[route], pools=dict(sub=dict(windows=[window])))
        request = dict(v=1, op='prepare', job=job, observations=observations,
                       assignment=dict(attempt_id='a1', task='t1', role='implementer', scope='scope1'))
        results = []
        for op, body in (('set', setting), ('prepare', request)):
            done = subprocess.run([sys.executable, '-B', str(ROUTING), op, '--settings', str(self.settings),
                                   '--ledger', str(ledger)], input=json.dumps(body).encode(), capture_output=True,
                                  timeout=60, env=dict(os.environ, HOME=str(self.root)))
            self.assertEqual(done.returncode, 0, done.stderr)
            results.append(json.loads(done.stdout))
        return results[1]

    def test_prepare_ranks_from_the_refreshed_table(self):
        self.write_evidence()
        code, report = self.refresh()
        self.assertEqual(code, 0, report)
        strategy = self.stored()['rows']['worker|code|medium|medium'][0]['strategy_id']
        response = self.prepare(self.root / 'usage' / 'ledger.sqlite3', strategy)
        self.assertEqual(response['state'], 'admitted', response)
        ranking = response['ranking']
        self.assertNotIn('table_unavailable', ranking['reason_codes'])
        self.assertEqual(ranking['table_version'], self.stored()['version'])
        self.assertEqual(ranking['strategy_ids'], [strategy])

    def test_prepare_ranks_from_the_previous_table_after_rollback(self):
        self.write_evidence()
        self.assertEqual(self.refresh(now='2026-11-01T00:00:00Z')[0], 0)
        old = self.stored()['version']
        self.write_evidence('b')
        self.assertEqual(self.refresh()[0], 0)
        self.assertNotEqual(self.stored()['version'], old)
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), 'rollback', '--settings', str(self.settings)],
                              capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['state'], 'rolled_back')
        strategy = self.stored()['rows']['worker|code|medium|medium'][0]['strategy_id']
        response = self.prepare(self.root / 'usage' / 'ledger.sqlite3', strategy)
        self.assertEqual(response['state'], 'admitted', response)
        self.assertNotIn('table_unavailable', response['ranking']['reason_codes'])
        self.assertEqual(response['ranking']['table_version'], old)

    def test_cli_runs_one_shot_and_exits_by_state(self):
        self.write_evidence()
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), *self.args()], capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)['state'], 'refreshed')
        done = subprocess.run([sys.executable, '-B', str(SCRIPT), 'refresh'], capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 2)
        self.assertNotIn(b'Traceback', done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout), dict(v=1, state='blocked', reason='usage_error'))


if __name__ == '__main__':
    unittest.main()
