"""Per-launch provider options for the OpenCode managed launcher (lane F4 item 2).

A worker's endpoint pin, output cap and step count reach OpenCode in the launch's own inline config
(`OPENCODE_CONFIG_CONTENT`): the V2 `providers.<p>.models.<m>.body` request fields (OpenRouter's `provider.only` and
`allow_fallbacks`, and `max_tokens`) and `agents.<name>.steps` for every built-in agent. Options that cannot be
expressed refuse the launch before anything is prepared; so does a host listing an agent without the step cap (lane
F5). A server that does not carry them, or an admission on another model, refuses before `dispatch` and closes the
hold. Fake server and the real routing command on temporary stores; OpenCode never runs.
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
        self.assertEqual(config['agents'], {name: {'steps': 3} for name in launcher.BUILTIN_AGENTS})
        self.assertEqual(launcher.PRIMARY_AGENT, 'build')  # the agent an un-agented prompt ran as on 2.2.6
        # every agent the V2 documentation lists, visible and hidden: a child or maintenance run is capped too
        self.assertEqual(set(launcher.BUILTIN_AGENTS),
                         {'build', 'plan', 'general', 'explore', 'compaction', 'title', 'summary'})
        plain = launcher.server_config('/s/settings.json', '/l/ledger.sqlite3', '/p')
        self.assertEqual({key: value for key, value in config.items() if key not in ('providers', 'agents')}, plain)
        self.assertFalse({'providers', 'agents', 'provider', 'agent'} & set(plain))
        with self.assertRaises(launcher.Unexpressible):
            launcher.server_config('/s/settings.json', '/l/ledger.sqlite3', '/p', options=dict(GOOD, steps=0))


class UncappedAgents(unittest.TestCase):
    """The one rule (`uncapped_agents`) the launcher and the gateway harness share."""

    CONFIG = {name: dict(steps=3) for name in launcher.BUILTIN_AGENTS}
    UNREADABLE = [launcher.LISTING_UNREADABLE]

    def check(self, listing, steps=3, config=None):
        return launcher.uncapped_agents(listing, self.CONFIG if config is None else config, steps)

    def test_reported_steps_must_equal_the_required_count(self):
        self.assertEqual(self.check([dict(id='build', steps=3), dict(id='reviewer', steps=3)]), [])
        self.assertEqual(self.check([dict(id='build', steps=2), dict(id='plan', steps=4)]), ['build', 'plan'])

    def test_a_bool_is_never_a_step_count(self):
        # required count 1, so `True == 1` cannot pass by accident
        config = {name: dict(steps=1) for name in launcher.BUILTIN_AGENTS}
        self.assertEqual(self.check([dict(id='build', steps=True)], steps=1, config=config), ['build'])
        self.assertEqual(self.check([dict(id='build', steps=1)], steps=1, config=config), [])

    def test_unreported_steps_come_from_the_inline_config_for_that_name(self):
        self.assertEqual(self.check([dict(id=name) for name in launcher.BUILTIN_AGENTS]), [])
        self.assertEqual(self.check([dict(id='build'), dict(id='reviewer')]), ['reviewer'])
        self.assertEqual(self.check([dict(id='build')], config=dict(build=dict(steps=4))), ['build'])
        self.assertEqual(self.check([dict(id='build')], config=dict(build=dict(steps=True))), ['build'])
        self.assertEqual(self.check([dict(id='build')], steps=1, config=dict(build=dict(steps=True))), ['build'])
        self.assertEqual(self.check([dict(id='build')], config={}), ['build'])

    def test_the_name_is_the_id_else_the_name(self):
        self.assertEqual(self.check([dict(name='plan')]), [])
        self.assertEqual(self.check([dict(id='reviewer', name='build')]), ['reviewer'])
        self.assertEqual(self.check([dict(id=7, name='build')]), [])  # a non-string id falls back to the name

    def test_an_empty_malformed_or_unreadable_listing_refuses(self):
        for listing in (None, [], 'build', dict(build=dict(steps=3)), [dict(steps=3)], ['build'],
                        [dict(id='build', steps=3), None], [dict(id=None, name=None)]):
            with self.subTest(listing=listing):
                self.assertEqual(self.check(listing), self.UNREADABLE)


class StartedServer(unittest.TestCase):
    def test_startup_failure_removes_private_config_without_changing_native_data_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            record, fake = root / 'seen.json', root / 'opencode'
            fake.write_text('#!%s\nimport json, os\nopen(%r,"w").write(json.dumps(dict(os.environ)))\n'
                            % (sys.executable, str(record)))
            fake.chmod(0o700)
            with self.assertRaises(launcher.ServerUnavailable):
                launcher.start_server(root, settings_path=root / 'settings.json', ledger_path=root / 'ledger.sqlite3',
                                      opencode=str(fake), environ=dict(HOME=str(root)), timeout=5, options=GOOD)
            seen = json.loads(record.read_text())
            self.assertEqual(seen['HOME'], str(root))
            self.assertNotIn('XDG_DATA_HOME', seen)
            self.assertFalse(Path(seen['OPENCODE_CONFIG_DIR']).parent.exists())

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
            self.assertEqual(config['agents'], {name: {'steps': 3} for name in launcher.BUILTIN_AGENTS})
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
        self.host.agents = []  # and no agent listing is read
        self.assertEqual(self.launch()['state'], 'recorded')
        self.assertNotIn(('GET', '/api/agent'), self.host.requests)


class EveryAgentCapped(base.LauncherBase):
    """Lane F5: V2 has no default step limit, so the launch reads the host's agent listing once and refuses, before
    anything is prepared, unless every listed agent carries the required step cap."""

    assert_refused = base.RefusalTests.assert_refused

    def capped(self, steps=3, names=None):
        return [dict(id=name, name=name, mode='primary', hidden=False, permissions=[], request={}, steps=steps)
                for name in names or launcher.BUILTIN_AGENTS]

    def test_every_listed_agent_capped_passes_with_one_listing_read(self):
        self.host.options, self.host.agents = GOOD, self.capped()
        result = self.launch(options=GOOD)
        self.assertEqual(result['reason'], launcher.LAUNCH_OPTIONS_MISMATCH)  # it got past the check to `dispatch`
        self.assertEqual(self.host.requests.count(('GET', '/api/agent')), 1)

    def test_an_agent_without_the_cap_refuses_before_prepare(self):
        extra = dict(self.capped(names=['reviewer'])[0])
        del extra['steps']
        listings = [self.capped() + [extra],  # a new or custom agent the inline config does not name
                    self.capped()[:-1] + self.capped(steps=4)[-1:],  # one agent's cap overridden
                    self.capped()[:-1] + [dict(self.capped()[-1], steps=True)],
                    [], 'not a list', [dict(id='reviewer')], [dict(steps=3)]]
        for listing in listings:
            with self.subTest(listing=listing):
                del self.order[:]
                self.host.options, self.host.agents = GOOD, listing
                self.assert_refused(launcher.AGENT_STEPS_UNCAPPED, self.launch(options=GOOD))
                self.assertNotIn('prepare', self.order)

    def test_built_ins_listed_without_steps_pass_on_the_inline_config(self):
        self.host.options, self.host.agents = GOOD, [dict(id=name) for name in launcher.BUILTIN_AGENTS]
        self.assertEqual(self.launch(options=GOOD)['reason'], launcher.LAUNCH_OPTIONS_MISMATCH)

    def test_an_unreadable_listing_refuses(self):
        self.host.options, self.host.agents = GOOD, self.capped()
        for status, raises in ((500, None), (404, None), (200, ConnectionResetError('lost'))):
            with self.subTest(status=status, raises=raises):
                del self.order[:]
                self.host.agent_status, self.host.agent_raises = status, raises
                self.assert_refused(launcher.AGENT_STEPS_UNCAPPED, self.launch(options=GOOD))
                self.assertNotIn('prepare', self.order)


if __name__ == '__main__':
    unittest.main()
