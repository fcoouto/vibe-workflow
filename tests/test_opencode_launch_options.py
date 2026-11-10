"""Per-launch provider options for the OpenCode managed launcher (lane F4 item 2).

A worker's endpoint pin, output cap and step count reach OpenCode in the launch's own inline config
(`OPENCODE_CONFIG_CONTENT`): the V2 `providers.<p>.models.<m>.body` request fields (OpenRouter's `provider.only` and
`allow_fallbacks`, and `max_tokens`) and `agents.build.steps`. Options that cannot be expressed refuse the launch
before anything is prepared; a server that does not carry them, or an admission on another model, refuses before
`dispatch` and closes the hold. Fake server and the real routing command on temporary stores; OpenCode never runs.
"""
import json
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from runtime import opencode_launcher as launcher
from tests import test_opencode_launcher as base

GOOD = dict(provider='openrouter', model='anthropic/claude-sonnet-4.5', endpoint='anthropic', max_output_tokens=2048,
            steps=3)


class OptionShape(unittest.TestCase):
    def test_valid_options_are_returned_as_given(self):
        self.assertEqual(launcher.launch_options(**GOOD), GOOD)
        self.assertEqual(launcher.launch_options(**dict(GOOD, endpoint='deepinfra/fp8'))['endpoint'], 'deepinfra/fp8')

    def test_options_that_cannot_be_expressed_are_refused(self):
        bad = [dict(provider='anthropic'), dict(provider='open/router'), dict(model='a/b/c'), dict(model='../x'),
               dict(model=''), dict(endpoint='../x'), dict(endpoint=''), dict(endpoint=None),
               dict(max_output_tokens=0), dict(max_output_tokens=True), dict(max_output_tokens='5'),
               dict(max_output_tokens=launcher.MAX_OUTPUT_TOKENS + 1), dict(steps=0), dict(steps=1.0),
               dict(steps=launcher.MAX_STEPS + 1), dict(steps=None)]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(launcher.Unexpressible) as caught:
                launcher.launch_options(**dict(GOOD, **change))
            self.assertEqual(str(caught.exception), launcher.LAUNCH_OPTIONS_UNEXPRESSIBLE)
        self.assertTrue(issubclass(launcher.Unexpressible, ValueError))

    def test_the_inline_config_carries_the_options_in_the_v2_keys(self):
        config = launcher.server_config('/s/settings.json', '/l/ledger.sqlite3', '/p', options=GOOD)
        self.assertEqual(config['providers'], {'openrouter': {'models': {'anthropic/claude-sonnet-4.5': {'body': {
            'provider': {'only': ['anthropic'], 'allow_fallbacks': False}, 'max_tokens': 2048}}}}})
        self.assertEqual(config['agents'], {launcher.PRIMARY_AGENT: {'steps': 3}})
        self.assertEqual(launcher.PRIMARY_AGENT, 'build')  # the agent an un-agented prompt ran as on 2.2.6
        plain = launcher.server_config('/s/settings.json', '/l/ledger.sqlite3', '/p')
        self.assertEqual({key: value for key, value in config.items() if key not in ('providers', 'agents')}, plain)
        self.assertFalse({'providers', 'agents', 'provider', 'agent'} & set(plain))
        with self.assertRaises(launcher.Unexpressible):
            launcher.server_config('/s/settings.json', '/l/ledger.sqlite3', '/p', options=dict(GOOD, steps=0))


class StartedServer(unittest.TestCase):
    def start(self, root, options):
        record = root / 'seen.json'
        fake = root / 'opencode'
        fake.write_text(textwrap.dedent('''\
            #!%s
            import json, os, time
            with open(%r, 'w') as out:
                json.dump(dict(env=dict(os.environ)), out)
            print('server listening on http://127.0.0.1:1', flush=True)
            time.sleep(30)
            ''') % (sys.executable, str(record)))
        fake.chmod(0o700)
        (root / 'checkout').mkdir(exist_ok=True)
        return record, launcher.start_server(root / 'checkout', settings_path=root / 's' / 'settings.json',
                                             ledger_path=root / 'l' / 'ledger.sqlite3', opencode=str(fake),
                                             environ=dict(base.StaticTests.HOSTILE, HOME=str(root)), timeout=10,
                                             options=options)

    def test_the_server_gets_the_options_and_remembers_them(self):
        with tempfile.TemporaryDirectory() as temp:
            record, server = self.start(Path(temp).resolve(), GOOD)
            try:
                config = json.loads(json.loads(record.read_text())['env']['OPENCODE_CONFIG_CONTENT'])
            finally:
                server.stop()
            self.assertEqual(server.options, GOOD)
            self.assertEqual(config['agents'], {'build': {'steps': 3}})
            self.assertEqual(config['providers']['openrouter']['models']['anthropic/claude-sonnet-4.5']['body']
                             ['provider'], {'only': ['anthropic'], 'allow_fallbacks': False})

    def test_unexpressible_options_start_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaises(launcher.Unexpressible):
                self.start(root, dict(GOOD, endpoint='a/b/c'))
            self.assertFalse((root / 'seen.json').exists())

    def test_a_server_without_options_has_none(self):
        with tempfile.TemporaryDirectory() as temp:
            _, server = self.start(Path(temp).resolve(), None)
            server.stop()
            self.assertIsNone(server.options)


class LaunchWithOptions(base.LauncherBase):
    """The launch refuses unless its host carries exactly the required options for the admitted model."""

    assert_refused = base.RefusalTests.assert_refused

    def test_a_host_without_the_options_is_refused_before_prepare(self):
        for carried in (None, dict(GOOD, steps=4)):
            with self.subTest(carried=carried):
                self.host.options = carried
                self.assert_refused(launcher.LAUNCH_OPTIONS_UNEXPRESSIBLE, self.launch(options=GOOD))
                self.assertNotIn('prepare', self.order)

    def test_unexpressible_options_are_refused_before_prepare(self):
        self.host.options = dict(GOOD, steps=0)
        self.assert_refused(launcher.LAUNCH_OPTIONS_UNEXPRESSIBLE, self.launch(options=dict(GOOD, steps=0)))
        self.assert_refused(launcher.LAUNCH_OPTIONS_UNEXPRESSIBLE, self.launch(options=dict(GOOD, unknown=1)))
        self.assertNotIn('prepare', self.order)

    def test_options_for_another_model_than_the_admitted_one_close_the_hold(self):
        self.host.options = GOOD  # the admission is the subscription route, not the options' model
        result = self.launch(options=GOOD)
        self.assert_refused(launcher.LAUNCH_OPTIONS_MISMATCH, result, closed='a1')
        self.assertNotIn('dispatch', self.order)

    def test_a_launch_without_options_is_unchanged(self):
        self.host.options = GOOD  # a host's options alone require nothing
        self.assertEqual(self.launch()['state'], 'recorded')


if __name__ == '__main__':
    unittest.main()
