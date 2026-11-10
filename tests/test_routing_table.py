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
NAMES = ('guidance', 'catalog', 'benchmarks', 'terms')


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
        (self.evidence / 'terms.json').write_text('{not json')
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
            'not utf-8': lambda v: v.__setitem__(3, b'\xff\xfe'),
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
    def write_evidence(self):
        guidance = [dict(id='g1', sha256='a' * 64, checked_at='2026-11-01T00:00:00Z', rows=[dict(
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
