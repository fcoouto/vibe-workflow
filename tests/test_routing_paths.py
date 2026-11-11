"""F4: model-free path rules, the path qualification record and in-memory route generation."""
import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from runtime import routing, routing_paths as paths

WINDOW_UNIT = 'utilization_thousandths'
SHIPPED = ('opencode-claude-subscription', 'opencode-chatgpt-subscription', 'codex-chatgpt-subscription',
           'claude-native-subscription', 'opencode-go-subscription', 'openrouter-workers', 'openrouter-proposals',
           'test-only-subscription')


def sub_path(path='sub-path', harness='codex-native', provider='openai', **extra):
    value = dict(path=path, harness=harness, provider=provider, sign_in='subscription',
                 billing_class='included_subscription', pool='sub-pool', account='sub-plan',
                 binding_enforceable=True, windows=['5h', '7d'], window_unit=WINDOW_UNIT)
    value.update(extra)
    return value


def gateway_path(path='gw-path', harness='openrouter-proposal', provider='openrouter', **extra):
    value = dict(path=path, harness=harness, provider=provider, sign_in='gateway_key', billing_class='gateway',
                 pool='gw-pool', account='gw-account', windows=['credit'], integration_route='proposals')
    value.update(extra)
    return value


def rules_of(*items):
    return dict(version=2, paths=[copy.deepcopy(item) for item in items])


class Folder(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.settings = self.root / 'routing' / 'settings.json'
        self.settings.parent.mkdir(mode=0o700)

    def write_rules(self, value):
        target = self.root / 'rules.json'
        target.write_text(json.dumps(value))
        return target

    def qualify(self, path_id, rules, **options):
        options.setdefault('demand', {'5h': 10, '7d': 2})
        options.setdefault('evidence', 'live-result-1')
        options.setdefault('now', 1_000)
        return paths.qualify(self.settings, path_id, rules=rules, **options)


class RuleTests(Folder):
    def test_shipped_path_rules_are_valid_and_name_no_model(self):
        rules = paths.load_rules()
        self.assertEqual(rules['version'], 2)
        self.assertEqual(tuple(item['path'] for item in rules['paths']), SHIPPED)
        self.assertLessEqual(len(rules['paths']), paths.MAX_PATHS)
        for item in rules['paths']:
            self.assertFalse({'model', 'effort', 'endpoint', 'demand'} & set(item), item['path'])
        raw = paths.RULES.read_text()
        self.assertNotIn('"model"', raw)
        by_id = {item['path']: item for item in rules['paths']}
        self.assertEqual(by_id['codex-chatgpt-subscription']['host'], 'codex_native')
        self.assertIs(by_id['codex-chatgpt-subscription']['advisory'], True)
        self.assertEqual(by_id['test-only-subscription']['status'], 'test_only')
        self.assertEqual({by_id[key]['integration_route'] for key in ('openrouter-workers', 'openrouter-proposals')},
                         {'workers', 'proposals'})
        self.assertEqual(by_id['opencode-claude-subscription']['windows'], ['5h', '7d'])

    def test_shipped_harnesses_are_the_ones_the_launchers_compare(self):
        from runtime import routing_launch
        by_id = {item['path']: item for item in paths.load_rules()['paths']}
        self.assertEqual(by_id['opencode-claude-subscription']['harness'], routing_launch.shim_harness('claude-agent'))
        self.assertEqual(by_id['opencode-chatgpt-subscription']['harness'], routing_launch.shim_harness('openai'))
        self.assertEqual(by_id['codex-chatgpt-subscription']['harness'], routing_launch.HOSTS['codex_native'][1])
        self.assertEqual(by_id['opencode-go-subscription']['harness'], routing_launch.shim_harness('opencode-go'))  # F4.5
        self.assertEqual(by_id['claude-native-subscription']['harness'], routing_launch.HOSTS['claude_native'][1])
        self.assertEqual(by_id['openrouter-workers']['harness'], routing_launch.shim_harness('openrouter'))
        self.assertEqual(by_id['openrouter-proposals']['harness'], routing_launch.HOSTS['openrouter_proposal'][1])

    def test_path_with_a_model_or_demand_is_refused(self):
        for field, value in (('model', 'm'), ('effort', 'high'), ('endpoint', 'p'), ('demand', {'5h': 1}),
                             ('unknown', 1)):
            with self.subTest(field):
                with self.assertRaisesRegex(ValueError, 'route_manifest_invalid'):
                    paths.load_rules(self.write_rules(rules_of(sub_path(**{field: value}))))

    def test_path_id_shape_is_enforced(self):
        for bad in ('Upper', 'has:colon', 'a' * 33, '', 'under_score', 5):
            with self.subTest(bad):
                with self.assertRaisesRegex(ValueError, 'route_manifest_invalid'):
                    paths.load_rules(self.write_rules(rules_of(sub_path(path=bad))))
        self.assertEqual(paths.load_rules(self.write_rules(rules_of(sub_path(path='a' * 32))))['paths'][0]['path'],
                         'a' * 32)
        for bad in (dict(version=1, paths=[]), dict(version=2, paths=[sub_path(path='p%d' % n, harness='h%d' % n)
                                                                       for n in range(17)]), [], dict(version=2)):
            with self.subTest(bad=str(bad)[:40]):
                with self.assertRaisesRegex(ValueError, 'route_manifest_invalid'):
                    paths.load_rules(self.write_rules(bad))
        with self.assertRaisesRegex(ValueError, 'route_manifest_invalid'):
            paths.load_rules(self.write_rules(rules_of(sub_path(), sub_path(harness='other'))))  # duplicate id

    def test_two_paths_with_one_harness_and_provider_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'route_manifest_invalid'):
            paths.load_rules(self.write_rules(rules_of(sub_path('a'), sub_path('b', pool='other'))))
        paths.load_rules(self.write_rules(rules_of(sub_path('a'), sub_path('b', provider='other'))))

    def test_subscription_path_needs_a_window_unit_and_gateway_path_an_integration_route(self):
        unitless = {key: value for key, value in sub_path().items() if key != 'window_unit'}
        unbound = {key: value for key, value in sub_path().items() if key != 'binding_enforceable'}
        unrouted = {key: value for key, value in gateway_path().items() if key != 'integration_route'}
        for name, item in (('unitless', unitless), ('unbound', unbound), ('unrouted', unrouted),
                           ('bad_route', gateway_path(integration_route='other')),
                           ('gateway_unit', gateway_path(window_unit=WINDOW_UNIT)),
                           ('gateway_window', gateway_path(windows=['5h'])), ('no_windows', sub_path(windows=[])),
                           ('bad_billing', sub_path(billing_class='purchased_usage')),
                           ('bad_sign_in', sub_path(sign_in='password'))):
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, 'route_manifest_invalid'):
                    paths.load_rules(self.write_rules(rules_of(item)))
        self.assertEqual(len(paths.load_rules(self.write_rules(rules_of(sub_path(), gateway_path())))['paths']), 2)

    def test_shipped_subscription_paths_are_binding_enforceable(self):
        for item in paths.load_rules()['paths']:
            if item['billing_class'] == 'included_subscription':
                self.assertIs(item['binding_enforceable'], True, item['path'])
                self.assertEqual(item['window_unit'], WINDOW_UNIT)
            else:
                self.assertNotIn('binding_enforceable', item)
                self.assertEqual(item['windows'], ['credit'])

    def test_dollar_harnesses_are_those_of_the_non_subscription_paths(self):
        rules = rules_of(sub_path(), gateway_path(), gateway_path('gw-2', harness='h2', integration_route='workers'))
        self.assertEqual(paths.dollar_harnesses(rules), {'openrouter-proposal', 'h2'})
        self.assertEqual(paths.dollar_harnesses(paths.load_rules()),
                         {'opencode provider openrouter', 'openrouter-proposal'})
        self.assertEqual(paths.pool_windows(rules, 'sub-pool'), {'5h', '7d'})
        self.assertEqual(paths.pool_windows(paths.load_rules(), 'chatgpt-subscription'), {'7d'})
        self.assertEqual(paths.pool_windows(rules, 'nothing'), set())
        self.assertEqual(paths.path_of('codex-chatgpt-subscription:abc:def'), 'codex-chatgpt-subscription')
        self.assertEqual(paths.path_of('plain'), 'plain')


class QualificationTests(Folder):
    def setUp(self):
        super().setUp()
        self.rules = rules_of(sub_path(), gateway_path(), sub_path('test-only', harness='test', provider='test',
                                                                   status='test_only', windows=['5h']))

    def test_qualify_checks_the_path_and_the_demand_windows(self):
        self.assertEqual(paths.qualifications(self.settings), {})
        for name, options, reason in (('unknown', dict(path_id='nope'), 'path_unknown'),
                                      ('missing_window', dict(demand={'5h': 10}), 'demand_invalid'),
                                      ('extra_window', dict(demand={'5h': 1, '7d': 1, '1d': 1}), 'demand_invalid'),
                                      ('zero', dict(demand={'5h': 0, '7d': 1}), 'demand_invalid'),
                                      ('over', dict(demand={'5h': 1001, '7d': 1}), 'demand_invalid'),
                                      ('bool', dict(demand={'5h': True, '7d': 1}), 'demand_invalid'),
                                      ('no_evidence', dict(evidence=''), 'evidence_invalid'),
                                      ('long_evidence', dict(evidence='e' * 129), 'evidence_invalid'),
                                      ('account', dict(account=''), 'account_invalid'),
                                      ('test_only', dict(path_id='test-only', demand={'5h': 1}), 'path_test_only')):
            with self.subTest(name):
                path_id = options.pop('path_id', 'sub-path')
                with self.assertRaisesRegex(ValueError, reason):
                    self.qualify(path_id, self.rules, **options)
        self.assertFalse((self.settings.parent / paths.QUALIFICATIONS_FILE).exists())
        record = self.qualify('sub-path', self.rules, demand={'5h': 1000, '7d': 1}, evidence='e' * 128)
        self.assertEqual(record['state'], 'qualified')
        self.assertEqual(paths.qualifications(self.settings), {'sub-path': dict(
            demand={'5h': 1000, '7d': 1}, account=None, evidence='e' * 128, qualified_at=1_000)})
        self.qualify('test-only', self.rules, demand={'5h': 1}, allow_test_only=True)  # test inputs only
        self.assertEqual(set(paths.qualifications(self.settings)), {'sub-path', 'test-only'})
        self.assertEqual(paths.unqualify(self.settings, 'sub-path'), dict(state='unqualified', path='sub-path',
                                                                         removed=True))
        self.assertEqual(paths.unqualify(self.settings, 'sub-path')['removed'], False)
        self.assertEqual(set(paths.qualifications(self.settings)), {'test-only'})

    def test_gateway_demand_is_micro_dollars_within_its_bound(self):
        for demand in ({'credit': 0}, {'credit': 1_000_001}, {'credit': 1.5}, {'5h': 1}):
            with self.subTest(demand=demand):
                with self.assertRaisesRegex(ValueError, 'demand_invalid'):
                    self.qualify('gw-path', self.rules, demand=demand)
        self.qualify('gw-path', self.rules, demand={'credit': 1_000_000})
        self.assertEqual(paths.qualifications(self.settings)['gw-path']['demand'], {'credit': 1_000_000})

    def test_a_qualifications_account_replaces_the_rules(self):
        self.qualify('sub-path', self.rules)
        self.qualify('gw-path', self.rules, demand={'credit': 5}, account='actual-org')
        resolved = {item['path']: item for item in paths.resolved(self.rules, paths.qualifications(self.settings))}
        self.assertEqual(set(resolved), {'sub-path', 'gw-path'})
        self.assertEqual(resolved['sub-path']['account'], 'sub-plan')
        self.assertEqual(resolved['gw-path']['account'], 'actual-org')
        self.assertEqual(resolved['sub-path']['demand'], {'5h': 10, '7d': 2})
        self.assertEqual(resolved['sub-path']['window_unit'], WINDOW_UNIT)
        # A qualification whose windows no longer match the rules resolves to nothing (fail closed).
        changed = rules_of(sub_path(windows=['5h']), gateway_path())
        self.assertEqual([item['path'] for item in paths.resolved(changed, paths.qualifications(self.settings))],
                         ['gw-path'])

    def test_qualification_file_is_private_and_atomic(self):
        self.qualify('sub-path', self.rules)
        target = self.settings.parent / paths.QUALIFICATIONS_FILE
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        before = target.read_bytes()
        with mock.patch.object(os, 'replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.qualify('gw-path', self.rules, demand={'credit': 5})
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(sorted(item.name for item in self.settings.parent.iterdir()
                                if not item.name.startswith('.paths')), [paths.QUALIFICATIONS_FILE])

    def test_unreadable_qualifications_fail_closed(self):
        target = self.settings.parent / paths.QUALIFICATIONS_FILE
        for raw in (b'{not json', b'[]', b'{"version":2,"paths":{}}', b'{"version":1,"paths":[]}',
                    json.dumps(dict(version=1, paths={'sub-path': dict(demand={'5h': 'x'}, account=None,
                                                                        evidence='e', qualified_at=1)})).encode()):
            with self.subTest(raw=raw[:30]):
                target.write_bytes(raw)
                target.chmod(0o600)
                with self.assertRaisesRegex(ValueError, 'path_qualifications_invalid'):
                    paths.qualifications(self.settings)
        target.write_bytes(b'{"version":1,"paths":{}}')
        target.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'path_qualifications_invalid'):
            paths.qualifications(self.settings)


def strategy(sid, model='m', effort='high', harness='codex-native', provider='openai', **extra):
    value = dict(strategy_id=sid, model=model, effort=effort, harness=harness, evidence_refs=['g'], **extra)
    if provider is not None:
        value['provider'] = provider
    return value


def table_of(*strategies, rows=None):
    return dict(schema=2, version='table-v1', rows=rows or {'implementation|medium|medium': list(strategies)})


def qualified(*records):
    return {path_id: dict(demand=demand, account=None, evidence='e', qualified_at=1) for path_id, demand in records}


class SharedPoolAccountTests(Folder):
    """F4R-02: the paths of one shared pool draw on one account. A qualification whose effective account differs
    from another qualified path's on the same pool is refused; a conflicting stored record leaves the pool out."""

    def qualify(self, path, demand, account=None):
        return paths.qualify(self.settings, path, demand=demand, evidence='e', now=1, account=account)

    def test_paths_of_one_pool_need_one_account(self):
        self.qualify('claude-native-subscription', {'5h': 5, '7d': 5})  # the rule's account
        self.qualify('opencode-claude-subscription', {'5h': 5, '7d': 5}, account='claude-plan')  # the same one
        target = self.settings.parent / paths.QUALIFICATIONS_FILE
        before = target.read_bytes()
        with self.assertRaisesRegex(ValueError, 'pool_account_conflict'):
            self.qualify('opencode-claude-subscription', {'5h': 5, '7d': 5}, account='another-plan')
        self.assertEqual(target.read_bytes(), before)  # refused under the lock: nothing written
        self.qualify('codex-chatgpt-subscription', {'7d': 5}, account='another-plan')  # another pool: independent
        with self.assertRaisesRegex(ValueError, 'pool_account_conflict'):
            self.qualify('opencode-chatgpt-subscription', {'7d': 5})  # its default account differs on that pool

    def test_a_stored_conflict_leaves_the_pool_out(self):
        target = self.settings.parent / paths.QUALIFICATIONS_FILE
        record = lambda account: dict(demand={'5h': 5, '7d': 5}, account=account, evidence='e', qualified_at=1)  # noqa
        target.write_text(json.dumps(dict(version=1, paths={
            'claude-native-subscription': record(None), 'opencode-claude-subscription': record('another-plan'),
            'codex-chatgpt-subscription': dict(record(None), demand={'7d': 5})})))
        target.chmod(0o600)
        before = target.read_bytes()
        found = [item['path'] for item in paths.resolved(paths.load_rules(), paths.qualifications(self.settings))]
        self.assertEqual(found, ['codex-chatgpt-subscription'])  # neither Claude path: no account is chosen
        self.assertEqual(target.read_bytes(), before)  # never rewritten


class GenerationTests(unittest.TestCase):
    rules = rules_of(sub_path(), gateway_path())

    def test_generation_combines_qualified_paths_with_table_strategies(self):
        table = table_of(strategy('s1'), strategy('s2', model='m2', effort='low'), strategy('s3', harness='other'),
                         strategy('s4', model='g', effort=None, harness='openrouter-proposal', provider='openrouter',
                                  endpoint='ep'))
        found = paths.generate(self.rules, qualified(('sub-path', {'5h': 10, '7d': 2}), ('gw-path', {'credit': 7})),
                               table, ())
        self.assertEqual([route['route'] for route in found['routes']], ['sub-path:s1', 'sub-path:s2', 'gw-path:s4'])
        self.assertEqual(found['skipped'], [])
        route = found['routes'][0]
        self.assertEqual({key: route[key] for key in ('status', 'path', 'strategy_id', 'provider', 'model', 'effort',
                                                       'harness', 'billing_class', 'pool', 'account', 'demand')},
                         dict(status='qualified', path='sub-path', strategy_id='s1', provider='openai', model='m',
                              effort='high', harness='codex-native', billing_class='included_subscription',
                              pool='sub-pool', account='sub-plan', demand={'5h': 10, '7d': 2}))
        # The same strategy in two rows is one route.
        twice = table_of(rows={'a|low|low': [strategy('s1')], 'b|low|low': [strategy('s1')]})
        self.assertEqual(len(paths.generate(self.rules, qualified(('sub-path', {'5h': 1, '7d': 1})), twice,
                                            ())['routes']), 1)

    def test_unqualified_path_generates_nothing(self):
        self.assertEqual(paths.generate(self.rules, {}, table_of(strategy('s1')), ())['routes'], [])
        choice = dict(provider='openai', model='m', effort='high', harness='codex-native', funding='subscription')
        self.assertEqual(paths.generate(self.rules, {}, None, [choice])['routes'], [])

    def test_strategy_without_a_provider_generates_nothing(self):
        found = paths.generate(self.rules, qualified(('sub-path', {'5h': 1, '7d': 1})),
                               table_of(strategy('s1', provider=None)), ())
        self.assertEqual(found['routes'], [])

    def test_strategy_without_an_effort_gets_the_text_none(self):
        [route] = paths.generate(self.rules, qualified(('sub-path', {'5h': 1, '7d': 1})),
                                 table_of(strategy('s1', effort=None)), ())['routes']
        self.assertEqual(route['effort'], 'none')

    def test_gateway_route_carries_endpoint_and_integration_route(self):
        [route] = paths.generate(self.rules, qualified(('gw-path', {'credit': 7})), table_of(strategy(
            's4', model='g', effort=None, harness='openrouter-proposal', provider='openrouter', endpoint='ep')),
            ())['routes']
        self.assertEqual((route['endpoint'], route['integration_route'], route['demand']), ('ep', 'proposals',
                                                                                          {'credit': 7}))
        self.assertFalse({'window_unit', 'binding_enforceable'} & set(route))

    def test_subscription_route_carries_the_window_unit(self):
        [route] = paths.generate(self.rules, qualified(('sub-path', {'5h': 1, '7d': 1})), table_of(strategy('s1')),
                                 ())['routes']
        self.assertEqual((route['window_unit'], route['binding_enforceable']), (WINDOW_UNIT, True))
        self.assertNotIn('integration_route', route)

    def test_choice_route_has_no_strategy_id_and_a_stable_id(self):
        resolved = paths.resolved(self.rules, qualified(('sub-path', {'5h': 1, '7d': 1})))[0]
        choice = dict(provider='openai', model='m', effort='high', harness='codex-native', funding='subscription')
        route = paths.choice_route(resolved, choice)
        self.assertNotIn('strategy_id', route)
        self.assertRegex(route['route'], r'^sub-path:choice:[0-9a-f]{16}$')
        self.assertEqual(paths.choice_route(resolved, dict(choice))['route'], route['route'])
        self.assertNotEqual(paths.choice_route(resolved, dict(choice, effort='low'))['route'], route['route'])
        found = paths.generate(self.rules, qualified(('sub-path', {'5h': 1, '7d': 1})), None, [choice, choice])
        self.assertEqual([item['route'] for item in found['routes']], [route['route']])

    def test_choice_route_id_changes_with_the_endpoint(self):
        resolved = paths.resolved(self.rules, qualified(('gw-path', {'credit': 1})))[0]
        choice = dict(provider='openrouter', model='g', effort='none', harness='openrouter-proposal', funding='api')
        bare, pinned = paths.choice_route(resolved, choice), paths.choice_route(resolved, dict(choice, endpoint='ep'))
        self.assertNotEqual(bare['route'], pinned['route'])
        self.assertEqual(pinned['endpoint'], 'ep')
        self.assertNotIn('endpoint', bare)
        self.assertNotEqual(pinned['route'], paths.choice_route(resolved, dict(choice, endpoint='other'))['route'])

    def test_ambiguous_endpoints_skip_only_those_strategies(self):
        gw = dict(model='g', effort=None, harness='openrouter-proposal', provider='openrouter')
        table = table_of(strategy('a', endpoint='p', **gw), strategy('b', endpoint='q', **gw),
                         strategy('c', **dict(gw, model='other'), endpoint='p'))
        found = paths.generate(self.rules, qualified(('gw-path', {'credit': 1})), table, ())
        self.assertEqual([route['route'] for route in found['routes']], ['gw-path:c'])
        self.assertEqual(found['skipped'], [dict(path='gw-path', strategy_id=sid, reason='endpoint_ambiguous')
                                            for sid in ('a', 'b')])

    def test_demand_comes_from_the_path_qualification(self):
        for demand in ({'5h': 3, '7d': 4}, {'5h': 900, '7d': 1}):
            [route] = paths.generate(self.rules, qualified(('sub-path', demand)), table_of(strategy('s1')),
                                     ())['routes']
            self.assertEqual(route['demand'], demand)

    def test_every_generated_route_passes_the_collectors_checks(self):
        from runtime import routing_observations as collector
        table = table_of(strategy('s1'), strategy('s2', effort=None),
                         strategy('s4', model='g', effort=None, harness='openrouter-proposal', provider='openrouter',
                                  endpoint='ep'))
        choices = [dict(provider='openai', model='x', effort='high', harness='codex-native', funding='subscription'),
                   dict(provider='openrouter', model='g', effort='none', harness='openrouter-proposal',
                        funding='api', endpoint='ep')]
        found = paths.generate(self.rules, qualified(('sub-path', {'5h': 1, '7d': 1}), ('gw-path', {'credit': 7})),
                               table, choices)
        self.assertEqual(len(found['routes']), 5)
        for route in found['routes']:
            with self.subTest(route=route['route']):
                self.assertIsNone(collector._manifest_reason(route))

    def test_route_selection_agrees_with_the_decision_mode(self):
        """Over Smart Routing on/off, with/without a choice, empty/non-empty ranking: `choice` and `choices` are the
        recorded `worker_choice` mode, `ranked` is `ranked` (routing._decide's own rule)."""
        choice = dict(provider='openai', model='m', effort='high', harness='codex-native', funding='subscription')
        worker = [dict(choice, model='w%d' % n) for n in range(20)]
        for smart in (True, False):
            for explicit in (None, choice):
                for ids in ([], ['s1', 's2']):
                    with self.subTest(smart=smart, explicit=bool(explicit), ids=ids):
                        settings = dict(smart_routing=smart, worker_choices={'software-engineer': worker})
                        assignment = dict(role='software-engineer', **({'choice': explicit} if explicit else {}))
                        mode, entries = paths.selection(assignment, settings, ids)
                        decided = 'ranked' if smart and not explicit and ids else 'worker_choice'
                        self.assertEqual('ranked' if mode == 'ranked' else 'worker_choice', decided)
                        expected = ([explicit] if explicit else ids if mode == 'ranked' else worker[:16])
                        self.assertEqual(entries, expected)
                        self.assertEqual(mode, 'choice' if explicit else 'ranked' if decided == 'ranked'
                                         else 'choices')


if __name__ == '__main__':
    unittest.main()
