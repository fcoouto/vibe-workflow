"""T12: the startup context says when the checkout is a managed Smart Routing project.

One local settings read; an error adds nothing and never fails startup. Settings live in temporary stores only.
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime import routing_settings, startup, startup_routing, state
from tests import test_minimal_default, test_startup as startup_fixtures

PREFIX = '\nSmart Routing: managed project. Delegate every worker or reviewer only through '
CLAUDE = PREFIX + 'the native bridge (clients/claude-code.md#native-routing-bridge).'
CODEX = PREFIX + '`workflow.py delegation prepare`, or the native bridge without runtime state (clients/codex.md).'
LAUNCHER = PREFIX + 'the managed launcher (clients/opencode.md#managed-launcher).'


class StartupRoutingLineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        self.settings = self.root / 'routing' / 'settings.json'
        self.enterContext(mock.patch.object(startup_routing, 'settings_path', lambda: self.settings))

    def manage(self, *projects):
        routing_settings.update(self.settings, dict(managed_projects=[str(item) for item in projects]),
                                routing_settings.revision(self.settings))

    def test_the_line_names_each_clients_managed_path(self):
        self.manage(self.checkout)
        for client, text in (('claude-code', CLAUDE), ('codex', CODEX), ('opencode', LAUNCHER),
                             ('another-client', LAUNCHER)):
            with self.subTest(client):
                self.assertEqual(startup_routing.line(client, self.checkout), text)
                self.assertEqual(startup_routing.line(client, self.checkout / 'nested'), text)

    def test_an_unmanaged_or_unconfigured_checkout_adds_nothing(self):
        self.assertEqual(startup_routing.line('codex', self.checkout), '')  # no settings file at all
        self.manage(self.root / 'other')
        self.assertEqual(startup_routing.line('claude-code', self.checkout), '')

    def test_unreadable_settings_fail_closed_as_routing_and_status_do(self):
        self.manage()
        self.settings.write_text('{not json')
        self.assertEqual(startup_routing.line('claude-code', self.checkout), CLAUDE)

    def test_an_error_adds_nothing_and_never_raises(self):
        for error in (RuntimeError('boom'), ImportError('gone'), OSError('denied')):
            with self.subTest(error), mock.patch.object(routing_settings, 'managed_project', side_effect=error):
                self.assertEqual(startup_routing.line('codex', self.checkout), '')


class DefaultSettingsTests(unittest.TestCase):
    def test_the_default_settings_are_the_routing_commands_without_importing_it(self):
        from scripts import routing as command
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, HOME=home):
            self.assertEqual(startup_routing.settings_path(), command.default_settings())


class OrientTests(unittest.TestCase):
    """The real startup orientation: unchanged byte-for-byte when unmanaged, one appended line when managed."""

    def test_orient_appends_the_line_only_for_a_managed_checkout(self):
        fixture, event = test_minimal_default.MinimalDefaultTests.configured_fixture(self)
        settings = Path(tempfile.mkdtemp()).resolve() / 'routing' / 'settings.json'
        self.addCleanup(__import__('shutil').rmtree, settings.parent.parent)
        acquired = startup.acquire_project_entry(event, state.PACKAGE, managed=False)
        with mock.patch.object(startup_routing, 'settings_path', lambda: settings):
            plain = startup.orient(event, state.PACKAGE, acquisition=acquired)
            with mock.patch.object(startup_routing, 'line', return_value=''):
                self.assertEqual(startup.orient(event, state.PACKAGE, acquisition=acquired), plain)
            self.assertNotIn('Smart Routing', plain)
            routing_settings.update(settings, dict(managed_projects=[str(fixture.repo)]), 0)
            managed = startup.orient(event, state.PACKAGE, acquisition=acquired)
        self.assertEqual(managed, plain + CODEX)


class StartupBudgetTests(unittest.TestCase):
    """F9: the line is appended after orient's 150-word check, so each client's managed total is pinned here: the
    line adds exactly these words and the whole orientation stays within the 150-word budget."""
    LINE_WORDS = {'codex': 22, 'claude-code': 15}

    def orientation(self, fixture_class):
        fixture = fixture_class('test_unenrolled_is_silent_and_read_only')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.enroll()
        event = fixture.adapter.normalize(fixture.payload)
        settings = Path(tempfile.mkdtemp()).resolve() / 'routing' / 'settings.json'
        self.addCleanup(__import__('shutil').rmtree, settings.parent.parent)
        with mock.patch.object(startup_routing, 'settings_path', lambda: settings):
            plain = startup.orient(event, state.PACKAGE)
            routing_settings.update(settings, dict(managed_projects=[str(fixture.repo)]), 0)
            managed = startup.orient(event, state.PACKAGE)
        return fixture.client, plain, managed

    def test_each_clients_managed_total_is_pinned_within_150_words(self):
        self.assertEqual({client: len((startup_routing.LINE + startup_routing.HOST_PATHS[client]).split())
                          for client in self.LINE_WORDS}, self.LINE_WORDS)
        self.assertEqual(len((startup_routing.LINE + startup_routing.OTHER_HOSTS).split()), 15)
        for fixture_class in (startup_fixtures.StartupTests, startup_fixtures.ClaudeStartupTests):
            client, plain, managed = self.orientation(fixture_class)
            with self.subTest(client):
                self.assertEqual(managed, plain + startup_routing.LINE + startup_routing.HOST_PATHS[client])
                self.assertEqual(len(managed.split()) - len(plain.split()), self.LINE_WORDS[client])
                self.assertLessEqual(len(managed.split()), 150)

    def test_the_codex_runtime_orientation_with_the_line_stays_within_150_words(self):
        from runtime.adapters import codex
        from tests import test_runtime
        from tests.fixture_canonical_birth import test_acquisition
        fixture = test_runtime.RuntimeTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.canonical()
        event = codex.normalize({'hook_event_name': 'SessionStart', 'source': 'startup', 'cwd': str(fixture.repo),
                                 'session_id': 'session-1'})
        settings = Path(tempfile.mkdtemp()).resolve() / 'routing' / 'settings.json'
        self.addCleanup(__import__('shutil').rmtree, settings.parent.parent)
        with mock.patch.object(startup_routing, 'settings_path', lambda: settings):
            plain = startup.orient(event, state.PACKAGE, acquisition=test_acquisition(fixture.scope))
            routing_settings.update(settings, dict(managed_projects=[str(Path(fixture.repo).resolve())]), 0)
            managed = startup.orient(event, state.PACKAGE, acquisition=test_acquisition(fixture.scope))
        self.assertIn('Runtime state:', plain)
        self.assertEqual(len(managed.split()) - len(plain.split()), self.LINE_WORDS['codex'])
        self.assertLessEqual(len(managed.split()), 150, (len(plain.split()), len(managed.split())))
