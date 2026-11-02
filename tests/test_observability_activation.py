"""Activation controls use disposable scopes; no live accounts or native telemetry."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from runtime import startup, state
from tests.openrouter_fixtures import enroll, cli_driver
from tests.test_langfuse import protocol_server, PUBLIC_KEY, SECRET_KEY


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / 'scripts/observability.py'


class ObservabilityActivationTests(unittest.TestCase):
    def setUp(self):
        self.scope = enroll(self)
        self.fixture = self._optional_fixtures[-1]
        self.driver = cli_driver(self, self.scope, SCRIPT)

    def snapshot_state(self):
        directory = self.scope.runtime.state_dir
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.rglob('*') if path.is_file()}

    def invoke(self, action, request=None, environment=None, raw=None):
        overrides = environment or {}
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('LANGFUSE_', 'GIT_'))}
        environment.update(overrides)
        return subprocess.run([sys.executable, '-I', '-B', str(self.driver), action,
            '--cwd', str(self.fixture.repo)], input=raw if raw is not None else json.dumps(request or {}).encode(),
            capture_output=True, cwd=self.fixture.repo, env=environment, timeout=25)

    def successful(self, action, request=None, environment=None):
        result = self.invoke(action, request, environment)
        self.assertEqual(result.returncode, 0, result.stdout.decode())
        self.assertEqual(result.stderr, b'')
        return json.loads(result.stdout)

    def test_status_defaults_off_without_creating_configuration_or_changing_core_state(self):
        before = self.snapshot_state()
        result = self.invoke('status')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        report = json.loads(result.stdout)
        self.assertFalse(report['configured'])
        self.assertIs(report['desired_opt_in'], False)
        self.assertEqual(self.snapshot_state(), before)
        self.assertFalse((self.scope.runtime.state_dir / 'observability').exists())
        self.assertEqual(result.stderr, b'')

    def test_real_cli_configure_verify_probe_disable_and_no_further_export(self):
        before = self.snapshot_state()
        with protocol_server() as server:
            configured = self.successful('configure', dict(expected_revision=0,
                destination=server.url, langfuse_project_id='lf-project'))
            self.assertFalse(configured['desired_opt_in'])
            self.assertEqual(server.requests, [])
            self.successful('status')
            self.assertEqual(server.requests, [])
            enabled = self.successful('enable', dict(expected_revision=1), server.environment)
            self.assertTrue(enabled['desired_opt_in'])
            self.assertEqual(enabled['collection'], 'unavailable')
            self.assertEqual([request['method'] for request in server.requests], ['GET'])
            verified = self.successful('probe', dict(expected_revision=2), server.environment)
            self.assertTrue(verified['probe']['verified'])
            self.assertFalse(verified['native_collection_qualified'])
            self.assertEqual(sum(request['method'] == 'POST' for request in server.requests), 1)
            count = len(server.requests)
            disabled = self.successful('disable', dict(expected_revision=2))
            self.assertFalse(disabled['desired_opt_in'])
            self.assertEqual(len(server.requests), count)
            blocked = self.invoke('probe', dict(expected_revision=3), server.environment)
            self.assertEqual(blocked.returncode, 1)
            self.assertEqual(len(server.requests), count)
            self.assertNotIn(PUBLIC_KEY, blocked.stdout.decode())
            self.assertNotIn(SECRET_KEY, blocked.stdout.decode())
        after = {key: value for key, value in self.snapshot_state().items() if not key.startswith('observability/')}
        self.assertEqual(after, before)

    def test_invalid_cli_input_and_missing_keys_return_only_bounded_errors(self):
        for raw in (b'{"expected_revision":0,"expected_revision":1}', b'[]', b'{' + b' ' * 9000):
            result = self.invoke('enable', raw=raw)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result.stdout)['ok'])
            self.assertEqual(result.stderr, b'')
        self.successful('configure', dict(expected_revision=0, destination='https://cloud.langfuse.com',
            langfuse_project_id='lf-project'))
        result = self.invoke('enable', dict(expected_revision=1))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['error'], 'credentials_unavailable')
        self.assertFalse(self.successful('status')['desired_opt_in'])

    def test_probe_verifies_new_keys_again_and_never_reuses_cached_connection_health(self):
        with protocol_server() as server:
            self.successful('configure', dict(expected_revision=0, destination=server.url,
                langfuse_project_id='lf-project'))
            self.successful('enable', dict(expected_revision=1), server.environment)
            server.projects_status = 401
            result = self.invoke('probe', dict(expected_revision=2), server.environment)
            self.assertEqual(result.returncode, 1)
            self.assertFalse(any(request['method'] == 'POST' for request in server.requests))
            count = len(server.requests)
            status = self.successful('status')
            self.assertTrue(status['desired_opt_in'])
            self.assertEqual(status['connection'], 'not_checked')
            self.assertEqual(len(server.requests), count)

    def _credential_reader(self):
        specification = importlib.util.spec_from_file_location('observability_cli_test', SCRIPT)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        if not hasattr(module, '_read_pair'):
            self.fail('Hidden local credential entry is not implemented')
        return module._read_pair

    def test_noninteractive_credential_entry_never_calls_getpass(self):
        from runtime.observability_credentials import CredentialError
        reader = self._credential_reader()
        with patch.object(sys, 'stdin', io.StringIO()), patch('getpass.getpass') as prompt:
            with self.assertRaises(CredentialError):
                reader()
            prompt.assert_not_called()

    def test_local_entry_hides_both_values_and_rejects_getpass_fallback(self):
        import getpass
        from runtime.observability_credentials import CredentialError
        reader = self._credential_reader()
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        terminal = Terminal()
        with patch.object(sys, 'stdin', terminal), patch.object(sys, 'stderr', terminal):
            with patch('getpass.getpass', side_effect=[PUBLIC_KEY, SECRET_KEY]) as prompt:
                self.assertEqual(reader(), (PUBLIC_KEY, SECRET_KEY))
                self.assertEqual(prompt.call_count, 2)
            with patch('getpass.getpass', side_effect=getpass.GetPassWarning('PRIVATE')):
                with self.assertRaises(CredentialError) as caught:
                    reader()
            self.assertNotIn('PRIVATE', str(caught.exception))
            for cancellation in (EOFError(), KeyboardInterrupt()):
                with patch('getpass.getpass', side_effect=cancellation):
                    with self.assertRaises(CredentialError) as caught:
                        reader()
                self.assertEqual(str(caught.exception), 'credential_entry_cancelled')
        self.assertNotIn(PUBLIC_KEY, terminal.getvalue())
        self.assertNotIn(SECRET_KEY, terminal.getvalue())

    def test_credential_forget_command_uses_revision_argument_and_no_secret_json(self):
        self.successful('configure', dict(expected_revision=0, destination='https://cloud.langfuse.com',
            langfuse_project_id='lf-project', credential_source='os-store'))
        environment = {key: value for key, value in os.environ.items() if not key.startswith(('LANGFUSE_', 'GIT_'))}
        result = subprocess.run([sys.executable, '-I', '-B', str(self.driver), 'forget-credentials',
            '--cwd', str(self.fixture.repo), '--expected-revision', '0'],
            input=b'', capture_output=True, env=environment, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b'')
        self.assertEqual(json.loads(result.stdout)['error'], 'observability_revision_conflict')

    @unittest.skipUnless(sys.platform == 'darwin', 'This CLI fixture selects the macOS backend')
    def test_real_terminal_store_enable_probe_disable_forget_with_fake_native_store(self):
        package = self.fixture.base / 'isolated-package'
        keyring = package / 'keyring'
        (keyring / 'backends').mkdir(parents=True)
        (keyring / '__init__.py').write_text('def get_keyring():\n    raise AssertionError("default backend")\n')
        (keyring / 'backends/__init__.py').write_text('')
        # This fake stores synthetic test keys only, outside every real OS store.
        (keyring / 'backends/macOS.py').write_text('''import json, os
from pathlib import Path
class Keyring:
    priority = 5
    keychain = None
    def _entry(self, action, service, username, password=None):
        path = Path(os.environ['VCW_TEST_STORE'])
        rows = json.loads(path.read_text()) if path.exists() else {}
        key = service + '|' + username
        log = path.with_suffix('.calls')
        with log.open('a') as stream: stream.write(action + '\\n')
        if action == 'get': return rows.get(key)
        if action == 'set': rows[key] = password
        if action == 'delete': del rows[key]
        path.write_text(json.dumps(rows)); path.chmod(0o600)
    def get_password(self, service, username): return self._entry('get', service, username)
    def set_password(self, service, username, password): self._entry('set', service, username, password)
    def delete_password(self, service, username): self._entry('delete', service, username)
''')
        metadata = package / 'keyring-25.7.0.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: keyring\nVersion: 25.7.0\n')
        script = cli_driver(self, self.scope, SCRIPT)
        script.write_text(script.read_text().replace('runpy.run_path(',
            f'sys.path.insert(0, {str(package)!r})\nrunpy.run_path('))
        store = self.fixture.base / 'synthetic-credentials.json'
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('LANGFUSE_', 'GIT_'))
                       and 'KEYRING_PROPERTY_' not in key and key != 'KEYCHAIN_PATH'}
        environment.update(VCW_TEST_STORE=str(store), LANGFUSE_PUBLIC_KEY='pk-lf-unrelated',
                           LANGFUSE_SECRET_KEY='sk-lf-unrelated')

        def command(action, *options):
            return [sys.executable, '-I', '-B', str(script), action,
                    '--cwd', str(self.fixture.repo), *options]

        def invoke(action, request=None, *options):
            result = subprocess.run(command(action, *options), input=json.dumps(request or {}).encode(),
                env=environment, capture_output=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout.decode())
            self.assertEqual(result.stderr, b'')
            self.assertNotIn(PUBLIC_KEY.encode(), result.stdout)
            self.assertNotIn(SECRET_KEY.encode(), result.stdout)
            return json.loads(result.stdout)

        with protocol_server() as server:
            environment['SSL_CERT_FILE'] = server.environment['SSL_CERT_FILE']
            invoke('configure', dict(expected_revision=0, destination=server.url,
                langfuse_project_id='lf-project', credential_source='os-store'))
            refused = subprocess.run(command('store-credentials', '--expected-revision', '1'),
                input=b'', capture_output=True, env=environment, timeout=10)
            self.assertEqual(refused.returncode, 1)
            self.assertEqual(json.loads(refused.stdout)['error'], 'credential_entry_requires_terminal')
            self.assertEqual(server.requests, [])
            self.assertFalse(store.exists())

            master, slave = os.openpty()
            process = subprocess.Popen(command('store-credentials', '--expected-revision', '1'),
                stdin=slave, stderr=slave, stdout=subprocess.PIPE, env=environment, start_new_session=True)
            os.close(slave)
            selector = selectors.DefaultSelector()
            selector.register(master, selectors.EVENT_READ)
            terminal, entered = bytearray(), set()
            deadline = time.monotonic() + 15
            try:
                while process.poll() is None and time.monotonic() < deadline:
                    for key, event in selector.select(.1):
                        try:
                            chunk = os.read(master, 4096)
                        except OSError:
                            chunk = b''
                        terminal.extend(chunk)
                        for prompt, value in ((b'Langfuse public key: ', PUBLIC_KEY),
                                              (b'Langfuse secret key: ', SECRET_KEY)):
                            if prompt in terminal and prompt not in entered:
                                os.write(master, value.encode() + b'\n')
                                entered.add(prompt)
                stdout, _ = process.communicate(timeout=2)
                self.assertEqual(process.returncode, 0, stdout.decode())
                self.assertEqual(len(entered), 2)
                self.assertNotIn(PUBLIC_KEY.encode(), terminal)
                self.assertNotIn(SECRET_KEY.encode(), terminal)
                report = json.loads(stdout)
                self.assertFalse(report['desired_opt_in'])
                self.assertEqual(report['revision'], 2)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                selector.close()
                os.close(master)
                process.stdout.close()

            self.assertFalse(any(row['method'] == 'POST' for row in server.requests))
            invoke('enable', dict(expected_revision=2))
            result = invoke('probe', dict(expected_revision=3))
            self.assertTrue(result['probe']['verified'])
            self.assertEqual(sum(row['method'] == 'POST' for row in server.requests), 1)
            calls = store.with_suffix('.calls').read_text()
            invoke('status')
            invoke('disable', dict(expected_revision=3))
            self.assertEqual(store.with_suffix('.calls').read_text(), calls)
            invoke('forget-credentials', None, '--expected-revision', '4')
            self.assertEqual(json.loads(store.read_text()), {})


if __name__ == '__main__':
    unittest.main()
