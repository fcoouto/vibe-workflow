"""Model-free controls for the disposable pilot operator."""
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        spec = importlib.util.spec_from_file_location('pilot_runner', HERE / 'runner.py')
        self.assertIsNotNone(spec)
        self.assertTrue(Path(spec.origin).exists(), 'pilot runner is not implemented')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.path = Path(self.tmp.name) / 'state.json'
        self.module.initialize(self.path, Path(self.tmp.name) / 'runs')

    def test_concurrency_failed_start_and_duplicate_are_accounted(self):
        self.module.reserve(self.path, 'a', now=100)
        self.module.reserve(self.path, 'b', now=101)
        with self.assertRaisesRegex(ValueError, 'concurrency'):
            self.module.reserve(self.path, 'c', now=102)
        self.module.settle(self.path, 'a', 'failed_start')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.module.reserve(self.path, 'a', now=103)
        self.module.reserve(self.path, 'c', now=103)
        state = json.loads(self.path.read_text())
        self.assertEqual(len(state['sessions']), 3)
        self.assertEqual(state['deadline'], 2800)

    def test_thirteenth_session_and_expiry_refused(self):
        for n in range(12):
            self.module.reserve(self.path, str(n), now=100+n)
            self.module.settle(self.path, str(n), 'stopped')
        with self.assertRaisesRegex(ValueError, 'sessions'):
            self.module.reserve(self.path, 'thirteen', now=200)
        other = Path(self.tmp.name) / 'other.json'
        self.module.initialize(other, Path(self.tmp.name) / 'other')
        self.module.reserve(other, 'first', now=100)
        self.module.settle(other, 'first', 'stopped')
        with self.assertRaisesRegex(ValueError, 'deadline'):
            self.module.reserve(other, 'late', now=2800)
        with self.assertRaises(FileExistsError):
            self.module.initialize(other, Path(self.tmp.name) / 'other')

    def test_api_key_and_expiring_auth_refused(self):
        with self.assertRaisesRegex(ValueError, 'subscription'):
            self.module.validate_auth({'type': 'api', 'key': 'synthetic'}, now=100)
        with self.assertRaisesRegex(ValueError, 'expiry'):
            self.module.validate_auth({'type': 'oauth', 'methodID': 'chatgpt-token-sharing', 'expires': 200}, now=100)
        good = {'type': 'oauth', 'methodID': 'chatgpt-token-sharing', 'expires': 4000, 'refresh': 'synthetic'}
        self.assertEqual(self.module.validate_auth(good, now=100)['refresh'], '')

    def test_auth_expiry_uses_original_remaining_deadline(self):
        value={'type':'oauth','methodID':'chatgpt-token-sharing','expires':200,'refresh':'synthetic'}
        self.assertEqual(self.module.validate_auth(value,now=100,required_seconds=90)['refresh'],'')
        with self.assertRaisesRegex(ValueError,'expiry'):
            self.module.validate_auth(value,now=100,required_seconds=120)


class FixtureTests(unittest.TestCase):
    def test_protected_fixture_rejects_line_ending_changes(self):
        from cases import preserved
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'check.py').write_bytes(b'assert True\r\n')
            hashes = {'check.py':hashlib.sha256(b'assert True\n').hexdigest()}
            self.assertEqual(preserved(root, {'check.py':'assert True\n'}, [], hashes),
                             ['protected_changed:check.py'])

    def test_native_notice_state_survives_repeated_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            (root/'project').mkdir(); (root/'before').mkdir()
            (root/'before/README.md').write_text('# Keep\n')
            (root/'project/README.md').write_text('# Keep\n[Broken](missing.md)\n')
            selection=dict(project=str(root/'project'),before=str(root/'before'),package=str(HERE.parents[1]),
                           documents=['README.md'],templates=[],python=sys.executable,helper=str(HERE/'helper.py'),log=str(root/'events'))
            (root/'selection.json').write_text(json.dumps(selection))
            event=dict(tool_name='Read',tool_input=dict(file_path=str(root/'project/README.md')),
                       hook_event_name='PostToolUse',session_id='synthetic')
            outputs=[]
            for _ in range(4):
                done=subprocess.run([sys.executable,'-I','-B',str(HERE/'native_hook.py'),str(root/'selection.json')],
                                    input=json.dumps(event),text=True,capture_output=True,timeout=10,check=True)
                outputs.append(done.stdout)
            rows=[json.loads(line) for line in (root/'events').read_text().splitlines()]
            self.assertEqual([r['kind'] for r in rows],['hook']*4)
            self.assertIn('link_missing',outputs[0])
            self.assertEqual(outputs[1:],['','',''])
            json.loads((root/'previous.json').read_text())

    def test_protected_fixture_rejects_extra_writes_and_removed_originals(self):
        self.assertTrue((HERE/'cases.py').exists(), 'pilot fixtures are not implemented')
        from cases import preserved
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'README.md').write_text('Keep me.\nAdded guidance.\n')
            (root/'check.py').write_text('assert True\n')
            initial = {'README.md':'Keep me.\n', 'check.py':'assert True\n'}
            hashes = {name:hashlib.sha256(text.encode()).hexdigest() for name,text in initial.items()}
            self.assertEqual(preserved(root, initial, ['README.md'], hashes), [])
            (root/'README.md').write_text('Added guidance.\n')
            (root/'check.py').write_text('assert False\n')
            (root/'extra.txt').write_text('unauthorized')
            findings = preserved(root, initial, ['README.md'], hashes)
            self.assertIn('original_missing:README.md', findings)
            self.assertIn('protected_changed:check.py', findings)
            self.assertIn('unexpected_file:extra.txt', findings)


class AccountingTests(unittest.TestCase):
    def test_usage_components_and_unknown_values_are_not_double_counted(self):
        self.assertTrue((HERE/'collect.py').exists(),'pilot collector is not implemented')
        from collect import normalized
        self.assertEqual(normalized('opencode',dict(input=10,output=3,reasoning=7,cache=dict(read=20,write=0)))['total'],40)
        self.assertEqual(normalized('claude',dict(input_tokens=2,output_tokens=10,cache_read_input_tokens=30,
                         cache_creation_input_tokens=5,output_tokens_details=dict(thinking_tokens=4)))['total'],47)
        self.assertIsNone(normalized('opencode',{})['total'])


if __name__ == '__main__':
    unittest.main()
