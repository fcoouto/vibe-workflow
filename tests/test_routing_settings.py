import json
import os
from pathlib import Path
import tempfile
import unittest
import unittest.mock

from runtime import routing_settings, usage_budget


CHOICE = dict(provider='p', model='m', effort='e', harness='h', funding='subscription')


class RoutingSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name).resolve() / 'routing'
        self.path = folder / 'settings.json'

    def test_defaults(self):
        value = routing_settings.resolve({})
        self.assertIs(value['smart_routing'], True)
        self.assertIs(value['api_usage_enabled'], False)
        self.assertEqual((value['L'], value['H'], value['T_hours']), (0.65, 0.85, 72))
        self.assertEqual((value['min_elapsed_fraction'], value['lookback_hours'], value['margin_bp']),
                         (0.10, 72, usage_budget.DEFAULT_MARGIN_BP))
        self.assertNotIn('caps', value)  # F5.A1: no default cap; a saved one is read, never enforced
        self.assertEqual(value['worker_choices'], {})

    def test_explicit_off_survives_write_and_read(self):
        record = routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.assertEqual(record['revision'], 1)
        self.assertIs(routing_settings.read(self.path)['smart_routing'], False)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_smart_routing_does_not_change_other_enablement(self):
        routing_settings.update(self.path, dict(smart_routing=True), 0)
        value = routing_settings.read(self.path)
        self.assertIs(value['api_usage_enabled'], False)
        self.assertEqual(set(value) & {'openrouter', 'council', 'observability'}, set())

    def test_api_usage_is_changed_only_by_its_own_field(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.assertIs(routing_settings.read(self.path)['api_usage_enabled'], False)
        routing_settings.update(self.path, dict(api_usage_enabled=True), 1)
        self.assertIs(routing_settings.read(self.path)['api_usage_enabled'], True)
        self.assertIs(routing_settings.read(self.path)['smart_routing'], False)

    def test_threshold_invariants(self):
        for bad in (dict(L=0.9, H=0.8), dict(L=0.5, H=0.5), dict(L=-0.1), dict(H=1.1), dict(T_hours=0),
                    dict(T_hours=-5), dict(min_elapsed_fraction=1.5), dict(margin_bp=0), dict(L=True)):
            with self.subTest(bad), self.assertRaises(routing_settings.SettingsError):
                routing_settings.update(self.path, bad, 0)
        self.assertEqual(routing_settings.update(self.path, dict(L=0, H=1), 0)['revision'], 1)

    def test_caps_use_ledger_field_names(self):
        routing_settings.update(self.path, dict(caps=dict(total=6, per_account=3)), 0)
        self.assertEqual(routing_settings.read(self.path)['caps'], dict(total=6, per_account=3))
        for bad in (dict(total=0, per_account=1), dict(total=1), dict(total=1, per_account=1, x=1)):
            with self.subTest(bad), self.assertRaises(routing_settings.SettingsError):
                routing_settings.update(self.path, dict(caps=bad), 1)

    def test_worker_choices_validated(self):
        routing_settings.update(self.path, dict(worker_choices={'software-engineer': [CHOICE]}), 0)
        self.assertEqual(routing_settings.read(self.path)['worker_choices'], {'software-engineer': [CHOICE]})
        missing = {k: v for k, v in CHOICE.items() if k != 'harness'}
        for bad in ({'code-reviewer': [missing]}, {'code-reviewer': [dict(CHOICE, extra=1)]},
                    {'code-reviewer': CHOICE}, {'code-reviewer': [dict(CHOICE, funding='free-money')]},
                    {'code-reviewer': [dict(CHOICE, model='')]}):
            with self.subTest(bad), self.assertRaises(routing_settings.SettingsError):
                routing_settings.update(self.path, dict(worker_choices=bad), 1)

    def test_set_refuses_a_non_roster_worker_choice_key(self):
        for key in ('implementer', 'reviewer', 'researcher', 'repair', 'worker', 'orchestrator', 'coordinator',
                    'check', 'planner'):
            with self.subTest(key), self.assertRaises(routing_settings.SettingsError) as caught:
                routing_settings.update(self.path, dict(worker_choices={key: [CHOICE]}), 0)
            self.assertEqual(str(caught.exception), 'invalid_worker_choices')
        for name in ('ROLES', 'PROFILE_ROLES', 'EXPLICIT_ROLES'):
            self.assertFalse(hasattr(routing_settings, name), name)

    def test_legacy_worker_choice_key_is_reported_not_fatal(self):
        second = dict(CHOICE, model='n')
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        stored = dict(version=1, revision=1, values=dict(smart_routing=False, worker_choices={
            'implementer': [CHOICE], 'software-engineer': [second]}))
        self.path.write_text(json.dumps(stored))
        self.assertEqual(routing_settings.read(self.path)['worker_choices'], {'software-engineer': [second]})
        values = routing_settings._load(self.path)[1]
        self.assertEqual(routing_settings.warnings(values),
                         [dict(code='worker_choice_key_unknown', key='implementer')])
        routing_settings.update(self.path, dict(smart_routing=True), 1)
        kept = json.loads(self.path.read_text())['values']['worker_choices']
        self.assertEqual(kept['implementer'], [CHOICE])  # left in the file, never rewritten or used
        self.assertEqual(routing_settings.warnings({}), [])

    def test_all_routed_specialists_accepted_and_order_kept(self):
        second = dict(CHOICE, model='n')
        choices = {role: [CHOICE, second] for role in ('software-engineer', 'code-reviewer', 'explorer-agent',
                                                       'debugger')}
        routing_settings.update(self.path, dict(worker_choices=choices), 0)
        self.assertEqual(routing_settings.read(self.path)['worker_choices'], choices)

    def test_assignment_role_is_the_profile_role(self):
        self.assertEqual(routing_settings.assignment_role('software-engineer'), 'software-engineer')
        self.assertEqual(routing_settings.assignment_role('code-reviewer', 'code-reviewer'), 'code-reviewer')
        for args, reason in ((('orchestrator',), 'coordinator_not_routed'),
                             (('software-engineer', 'code-reviewer'), 'role_mismatch'),
                             (('software-engineer', 'implementer'), 'role_mismatch')):
            with self.subTest(args), self.assertRaises(routing_settings.SettingsError) as caught:
                routing_settings.assignment_role(*args)
            self.assertEqual(str(caught.exception), reason)

    def test_duplicate_worker_choices_rejected(self):
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.update(self.path, dict(worker_choices={'code-reviewer': [CHOICE, dict(CHOICE)]}), 0)

    def test_intermediate_directories_are_private(self):
        deep = Path(self.tmp.name).resolve() / 'a' / 'b' / 'settings.json'
        routing_settings.update(deep, dict(smart_routing=False), 0)
        for folder in (deep.parent, deep.parent.parent):
            self.assertEqual(os.stat(folder).st_mode & 0o777, 0o700)

    def test_stale_revision_refused(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        with self.assertRaises(routing_settings.SettingsError) as caught:
            routing_settings.update(self.path, dict(smart_routing=True), 0)
        self.assertEqual(str(caught.exception), 'stale_revision')
        self.assertIs(routing_settings.read(self.path)['smart_routing'], False)

    def test_unknown_field_rejected(self):
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.update(self.path, dict(openrouter=True), 0)

    def test_unsafe_storage_refused(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        os.chmod(self.path, 0o644)
        with self.assertRaises(usage_budget.BudgetError):
            routing_settings.read(self.path)

    def test_corrupt_storage_refused(self):
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.path.write_text(json.dumps(dict(version=1, revision=1, values=dict(L=5))))
        os.chmod(self.path, 0o600)
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.read(self.path)

    def test_missing_file_reads_defaults_at_revision_zero(self):
        self.assertEqual(routing_settings.revision(self.path), 0)
        self.assertIs(routing_settings.read(self.path)['smart_routing'], True)

    def test_resolve_rejects_invalid_stored(self):
        with self.assertRaises(routing_settings.SettingsError):
            routing_settings.resolve(dict(L=0.9, H=0.1))

    def test_managed_projects_default_empty_and_validated(self):
        self.assertEqual(routing_settings.resolve({})['managed_projects'], [])
        routing_settings.update(self.path, dict(managed_projects=['/work/a', '/work/b']), 0)
        self.assertEqual(routing_settings.read(self.path)['managed_projects'], ['/work/a', '/work/b'])
        for bad in ('/work/a', ['relative'], ['/work/a', '/work/a'], ['/work/../a'], ['/work/a/'], [''], [7],
                    ['/' + 'x' * 1024], ['/x%d' % n for n in range(routing_settings.MAX_MANAGED + 1)]):
            with self.subTest(bad), self.assertRaises(routing_settings.SettingsError) as caught:
                routing_settings.update(self.path, dict(managed_projects=bad), 1)
            self.assertEqual(str(caught.exception), 'invalid_managed_projects')
        self.assertIsNot(routing_settings.resolve({})['managed_projects'],
                         routing_settings.resolve({})['managed_projects'])

    def test_managed_project_matches_the_realpath_and_paths_inside_it(self):
        root = Path(self.tmp.name).resolve()
        checkout, other = root / 'checkout', root / 'other'
        (checkout / 'sub').mkdir(parents=True)
        other.mkdir()
        (root / 'link').symlink_to(checkout)
        self.assertIs(routing_settings.managed_project(checkout, settings_path=self.path), False)  # no file yet
        routing_settings.update(self.path, dict(managed_projects=[str(checkout)]), 0)
        for path, expected in ((checkout, True), (root / 'link', True), (checkout / 'sub', True), (other, False),
                               (root, False), (str(root / 'checkout-2'), False)):
            with self.subTest(path):
                self.assertIs(routing_settings.managed_project(path, settings_path=self.path), expected)

    def test_managed_project_compares_file_identity_not_spelling(self):
        root = Path(self.tmp.name).resolve()
        checkout = root / 'Proj'
        (checkout / 'Sub').mkdir(parents=True)
        (root / 'link').symlink_to(checkout)
        routing_settings.update(self.path, dict(managed_projects=[str(checkout)]), 0)
        cases = [(checkout / 'Sub' / '..', True), (root / 'link' / 'Sub', True),
                 (checkout / 'not-yet' / 'deeper', True),  # a path that does not exist yet, under the root
                 (root / 'Proj-other', False)]
        if (root / 'proj').exists():  # a case-insensitive filesystem (the macOS default)
            cases += [(root / 'proj', True), (root / 'PROJ' / 'sub', True), (root / 'pRoJ' / 'nEw', True)]
        for path, expected in cases:
            with self.subTest(path):
                self.assertIs(routing_settings.managed_project(path, settings_path=self.path), expected)

    def test_managed_project_fails_closed_when_a_path_cannot_be_inspected(self):
        root = Path(self.tmp.name).resolve()
        (root / 'checkout').mkdir()
        routing_settings.update(self.path, dict(managed_projects=[str(root / 'checkout')]), 0)
        with unittest.mock.patch.object(routing_settings, '_identity', side_effect=PermissionError('denied')):
            self.assertIs(routing_settings.managed_project(root / 'elsewhere', settings_path=self.path), True)

    def test_managed_project_fails_closed_on_unreadable_settings(self):
        routing_settings.update(self.path, dict(managed_projects=[]), 0)
        os.chmod(self.path, 0o644)
        self.assertIs(routing_settings.managed_project('/anywhere', settings_path=self.path), True)
        os.chmod(self.path, 0o600)
        self.path.write_text('{not json')
        self.assertIs(routing_settings.managed_project('/anywhere', settings_path=self.path), True)

    def test_managed_project_defaults_to_the_routing_command_settings(self):
        from scripts import routing as command
        routing_settings.update(self.path, dict(managed_projects=['/work/repo']), 0)
        with unittest.mock.patch.object(command, 'default_settings', lambda: self.path):
            self.assertIs(routing_settings.managed_project('/work/repo'), True)
            self.assertIs(routing_settings.managed_project('/work/other'), False)

    def test_legacy_route_retired_is_a_fixed_code(self):
        self.assertEqual(routing_settings.LEGACY_ROUTE_RETIRED, 'legacy_route_retired')

    def test_workstreams_preference_is_read_not_copied(self):
        calls = []
        scope = object()
        loader = lambda s: calls.append(s) or dict(enabled=True)
        self.assertIs(routing_settings.workstreams_enabled(scope, load=loader), True)
        self.assertEqual(calls, [scope])
        routing_settings.update(self.path, dict(smart_routing=False), 0)
        self.assertNotIn('workstreams', json.loads(self.path.read_text()).get('values', {}))


class WorkerChoiceInstructionTests(unittest.TestCase):
    """The worker-choice instructions state the validator's contract and load nothing for API setup."""
    SKILL = Path(__file__).resolve().parents[1] / 'skills' / 'manage-usage-routing'

    def test_worker_choice_requests_find_a_complete_selective_contract(self):
        entry = (self.SKILL / 'SKILL.md').read_text()
        description = next(line for line in entry.splitlines() if line.startswith('description:'))
        self.assertIn('worker choice', description)
        self.assertIn('(references/worker-choices.md)', entry)
        text = (self.SKILL / 'references' / 'worker-choices.md').read_text()
        for field in sorted(routing_settings.CHOICE_FIELDS):
            self.assertIn('`%s`' % field, text)
        for value in (*routing_settings.FUNDING, str(routing_settings.MAX_CHOICES), '256', 'routing.py status',
                      'routing.py set', 'expected_revision', 'invalid_worker_choices', 'invalid_worker_choice',
                      'duplicate_worker_choice', 'worker_choice_key_unknown', 'stale_revision', 'orchestrator'):
            self.assertIn(value, text)
        for unloaded in ('keychain-setup.md', 'first-canary.md', 'delegation.md', 'procedure.md'):
            self.assertNotIn(unloaded, text)  # a worker-choice request loads no API setup, canary or delegation

    def test_identity_fields_come_from_sources_that_carry_them(self):
        """F2-N1: native admission replies name no provider or harness, so the guide names the path rules and the
        compiled table, whose fields routing compares, and asks for anything they do not show."""
        from runtime import routing_launch, routing_paths
        text = ' '.join((self.SKILL / 'references' / 'worker-choices.md').read_text().split())
        self.assertNotIn('earlier admission or launch reply', text)
        for source in ('runtime/routing_routes.json', 'strategy-table.json', 'ask the user'):
            self.assertIn(source, text)
        rules = routing_paths.load_rules()  # the named rules carry both fields on every path
        self.assertTrue(all(rule['provider'] and rule['harness'] for rule in rules['paths']))
        codex = routing_launch.route_identity('codex_native', model='m')
        self.assertIn('`%s` and provider `%s`' % (codex['harness'], codex['provider']), text)
        self.assertIn('`%s`' % codex['effort'], text)  # a model without an effort control
        self.assertTrue(any(rule['harness'] == codex['harness'] and rule['provider'] == codex['provider']
                            for rule in rules['paths']))

    def test_a_compiled_null_effort_becomes_none_only_in_the_choice(self):
        """F2-N1 correction: the compiled table keeps `effort: null` (test_routing_evidence
        `test_same_model_on_two_harnesses_has_independent_efforts`); the generated route identity uses the text
        `none`, which a settings choice must name, and the launch sends no effort."""
        from runtime import routing_paths
        text = ' '.join((self.SKILL / 'references' / 'worker-choices.md').read_text().split())
        self.assertNotIn('is written as effort `none`', text)
        for phrase in ('`effort` is `null`', 'write the text `none` in the choice', 'sends no effort'):
            self.assertIn(phrase, text)
        path = dict(next(rule for rule in routing_paths.load_rules()['paths'] if rule['harness'] == 'codex-native'),
                    demand={'7d': 1})
        route = routing_paths.strategy_route(path, dict(strategy_id='s1', model='m', effort=None))
        self.assertEqual(route['effort'], 'none')  # the identity a choice with effort `none` matches


    def test_documented_commands_run_against_disposable_stores(self):
        import re, subprocess
        text = (self.SKILL / 'references' / 'worker-choices.md').read_text()
        blocks = [' '.join(block.replace('\\\n', ' ').split()) for block in re.findall(r'```sh\n(.*?)```', text, re.S)]
        status, update = (next(block for block in blocks if 'routing.py ' + op in block) for op in ('status', 'set'))
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp).resolve()
            (temp / 'project').mkdir()
            (temp / 'home').mkdir(mode=0o700)
            stores = '--settings %s --ledger %s' % (temp / 'routing' / 'settings.json', temp / 'usage' / 'ledger.sqlite3')

            def run(command):
                command = command.replace('/absolute/project/path', '%s %s' % (temp / 'project', stores))
                done = subprocess.run(['sh', '-c', command], cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
                                      timeout=60, env=dict(os.environ, HOME=str(temp / 'home')))
                return json.loads(done.stdout)

            before = run(status)
            self.assertEqual((before['state'], before['settings_revision'], before['settings_warnings']), ('ok', 0, []))
            choice = {'software-engineer': [CHOICE]}
            written = run(update.replace('<revision>', str(before['settings_revision']))
                          .replace('<map>', json.dumps(choice, separators=(',', ':'))))
            self.assertEqual((written['state'], written['revision']), ('updated', 1), written)
            self.assertEqual(run(status)['settings']['worker_choices'], choice)


if __name__ == '__main__':
    unittest.main()
