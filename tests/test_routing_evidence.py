"""Strategy table compilation: evidence-qualified, live-catalog-checked, never guessed."""
import copy
from fractions import Fraction
import json
from pathlib import Path
import re
import unittest
import unittest.mock

from runtime import routing, routing_evidence as evidence

FIXTURES = Path(__file__).parent / 'fixtures' / 'smart-routing'
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def inputs():
    return [load(name) for name in ('guidance', 'catalog', 'benchmarks')]


def key(kind, complexity, risk):
    return routing.row_key(kind, complexity, risk)


class CompileTests(unittest.TestCase):
    def setUp(self):
        self.table = evidence.compile_table(*inputs(), now='2026-11-08T00:00:00Z')

    def models(self, row):
        return [(item['model'], item['effort']) for item in self.table['rows'][row]]

    def test_rows_are_versioned_ordered_and_cite_evidence(self):
        row = key('implementation', 'medium', 'medium')
        # F5.2: both unpriced, so the lower effort of its model's catalog list comes first
        self.assertEqual(self.models(row), [('alpha-small', 'low'), ('alpha-large', 'high')])
        first = self.table['rows'][row][1]
        self.assertEqual(set(first) - {'context_limit'}, {'strategy_id', 'model', 'effort', 'harness', 'evidence_refs',
                                                          'list_price_micros'})
        self.assertIsNone(first['list_price_micros'])
        self.assertEqual(first['context_limit'], 200000)
        self.assertEqual(first['evidence_refs'], ['guide-1'])
        self.assertRegex(self.table['version'], r'^[0-9a-f]{64}$')
        self.assertEqual(self.table['sources'][0]['sha256'], '1' * 64)
        self.assertIn('checked_at', self.table['sources'][0])

    def test_version_is_stable_for_identical_inputs(self):
        again = evidence.compile_table(*inputs(), now='2030-01-01T00:00:00Z')
        self.assertEqual(again['version'], self.table['version'])

    def test_absent_models_and_unsupported_efforts_are_dropped_not_guessed(self):
        row = key('implementation', 'high', 'high')
        self.assertEqual(self.models(row), [('alpha-large', 'max')])
        reasons = {item['reason'] for item in self.table['dropped']}
        self.assertTrue({'model_not_in_catalog', 'effort_unsupported'} <= reasons)

    def test_new_catalog_model_needs_cited_evidence(self):
        self.assertNotIn(key('implementation', 'low', 'low'), self.table['rows'])
        guidance, catalog, benchmarks = inputs()
        guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = ['bench-beta']
        table = evidence.compile_table(guidance, catalog, benchmarks)
        self.assertEqual([item['model'] for item in table['rows'][key('implementation', 'low', 'low')]],
                         ['beta-new'])
        for refs in (['bench-unknown'], ['guide-1', 'bench-unknown']):
            guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = refs
            self.assertNotIn(key('implementation', 'low', 'low'),
                             evidence.compile_table(guidance, catalog, benchmarks)['rows'])

    def test_benchmark_for_another_model_does_not_qualify(self):
        guidance, catalog, benchmarks = inputs()
        benchmarks[0]['model'] = 'alpha-small'
        guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = ['bench-beta']
        table = evidence.compile_table(guidance, catalog, benchmarks)
        self.assertNotIn(key('implementation', 'low', 'low'), table['rows'])

    def test_benchmark_for_another_work_kind_does_not_qualify(self):
        guidance, catalog, benchmarks = inputs()
        benchmarks[0]['work_kind'] = 'investigation'
        guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = ['bench-beta']
        table = evidence.compile_table(guidance, catalog, benchmarks)
        self.assertNotIn(key('implementation', 'low', 'low'), table['rows'])

    def test_duplicate_catalog_models_and_bad_check_times_are_rejected(self):
        guidance, catalog, benchmarks = inputs()
        with self.assertRaises(ValueError):
            evidence.compile_table(guidance, catalog + [catalog[0]], benchmarks)
        guidance[0]['checked_at'] = 'yesterday'
        with self.assertRaises(ValueError):
            evidence.compile_table(guidance, catalog, benchmarks)

    def test_empty_recommendation_is_not_shared_state(self):
        table = evidence.compile_table(*inputs())
        result = evidence.lookup(table, dict(role='code-reviewer', work_kind='review', complexity_band='low',
                                             risk_band='low'))
        result['reason'] = 'tampered'
        self.assertEqual(evidence.NO_RECOMMENDATION['reason'], 'no_recommendation')

    def test_rows_are_keyed_by_work_kind(self):
        self.assertEqual(self.table['schema'], routing.TABLE_SCHEMA)
        self.assertEqual(evidence.SCHEMA, routing.TABLE_SCHEMA)
        self.assertTrue(all(name.count('|') == 2 for name in self.table['rows']))

    def test_guidance_citation_must_come_from_the_entry_holding_the_row(self):
        guidance, catalog, benchmarks = inputs()
        other = dict(id='guide-2', sha256='3' * 64, checked_at='2026-11-01T00:00:00Z', rows=[])
        guidance[0]['rows'][0]['strategies'][0]['evidence_refs'] = ['guide-2']
        table = evidence.compile_table(guidance + [other], catalog, benchmarks)
        models = [(i['model'], i['effort']) for i in table['rows'][key('implementation', 'medium', 'medium')]]
        self.assertEqual(models, [('alpha-small', 'low')])

    def test_benchmark_must_state_harness_and_work_kind(self):
        for missing in ('harness', 'work_kind'):
            guidance, catalog, benchmarks = inputs()
            del benchmarks[0][missing]
            guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = ['bench-beta']
            table = evidence.compile_table(guidance, catalog, benchmarks)
            self.assertNotIn(key('implementation', 'low', 'low'), table['rows'], missing)

    def test_removed_catalog_model_disappears_on_recompile(self):
        guidance, catalog, benchmarks = inputs()
        catalog = [item for item in catalog if item['model'] != 'alpha-small']
        table = evidence.compile_table(guidance, catalog, benchmarks)
        self.assertEqual([m for m, _ in [(i['model'], 0) for i in table['rows'][
            key('implementation', 'medium', 'medium')]]], ['alpha-large'])

    def test_harness_must_match_when_catalog_declares_harnesses(self):
        guidance, catalog, benchmarks = inputs()
        catalog[0]['harnesses'] = ['other']
        table = evidence.compile_table(guidance, catalog, benchmarks)
        self.assertNotIn('alpha-large', [i['model'] for i in table['rows'][
            key('implementation', 'medium', 'medium')]])
        self.assertIn('harness_unsupported', {item['reason'] for item in table['dropped']})

    def test_same_model_on_two_harnesses_has_independent_efforts(self):
        """F4.2: catalog identity is (model, harness). One harness accepts an explicit effort, the other only
        omission (`effort: null`); neither inherits the other's efforts."""
        guidance, catalog, benchmarks = inputs()
        catalog = [dict(model='shared', harnesses=['h-explicit'], supported_efforts=['high']),
                   dict(model='shared', harnesses=['h-omit'], supported_efforts=[])]
        row = guidance[0]['rows'][0]
        row['strategies'] = [dict(model='shared', effort=effort, harness=harness, evidence_refs=['guide-1'])
                             for harness in ('h-explicit', 'h-omit') for effort in ('high', None)]
        table = evidence.compile_table(guidance, catalog, benchmarks)
        kept = [(item['harness'], item['effort']) for item in table['rows'][key('implementation', 'medium', 'medium')]]
        self.assertEqual(kept, [('h-explicit', 'high'), ('h-omit', None)])
        dropped = {(item['model'], item['effort'], item['reason']) for item in table['dropped']}
        self.assertTrue({('shared', None, 'effort_unsupported'), ('shared', 'high', 'effort_unsupported')} <= dropped)

    def test_duplicate_model_harness_pair_is_rejected(self):
        guidance, catalog, benchmarks = inputs()
        twice = [dict(model='m', harnesses=['h1', 'h2'], supported_efforts=[]),
                 dict(model='m', harnesses=['h2'], supported_efforts=['low'])]
        with self.assertRaisesRegex(ValueError, 'Duplicate catalog'):
            evidence.compile_table(guidance, twice, benchmarks)
        with self.assertRaisesRegex(ValueError, 'Duplicate catalog'):
            evidence.compile_table(guidance, [dict(model='m', harnesses=['h1', 'h1'], supported_efforts=[])],
                                   benchmarks)
        evidence.compile_table(guidance, [twice[0], dict(twice[1], harnesses=['h3'])], benchmarks)

    def test_catalog_requires_explicit_harnesses(self):
        guidance, catalog, benchmarks = inputs()
        for harnesses in (None, [], [''], [5], 'native'):
            with self.subTest(harnesses=harnesses):
                entry = dict(catalog[0])
                entry.pop('harnesses', None)
                if harnesses is not None:
                    entry['harnesses'] = harnesses
                with self.assertRaisesRegex(ValueError, 'Invalid catalog harnesses'):
                    evidence.compile_table(guidance, [entry], benchmarks)

    def test_benchmark_does_not_cross_harnesses(self):
        guidance, catalog, benchmarks = inputs()
        catalog = [dict(item, harnesses=['native', 'other']) if item['model'] == 'beta-new' else item
                   for item in catalog]
        strategy = guidance[0]['rows'][2]['strategies'][0]
        guidance[0]['rows'][2]['strategies'] = [dict(strategy, harness='other', evidence_refs=['bench-beta'])]
        table = evidence.compile_table(guidance, catalog, benchmarks)  # the benchmark is for `native` only
        self.assertNotIn(key('implementation', 'low', 'low'), table['rows'])
        guidance[0]['rows'][2]['strategies'] = [dict(strategy, evidence_refs=['bench-beta'])]
        table = evidence.compile_table(guidance, catalog, benchmarks)
        self.assertEqual([item['harness'] for item in table['rows'][key('implementation', 'low', 'low')]], ['native'])

    def test_strategies_carry_the_catalog_provider_and_a_distinct_endpoint(self):
        """F4.2: route generation needs `provider`, and `endpoint` when it differs from the harness; ids unchanged."""
        guidance, catalog, benchmarks = inputs()
        before = evidence.compile_table(guidance, catalog, benchmarks)
        catalog[0]['provider'] = 'vendor-a'
        guidance[0]['rows'][0]['strategies'][1]['endpoint'] = 'pinned-endpoint'
        guidance[0]['rows'][0]['strategies'][0]['endpoint'] = 'native'  # equal to the harness: not copied
        table = evidence.compile_table(guidance, catalog, benchmarks)
        second, first = table['rows'][key('implementation', 'medium', 'medium')]  # F5.2: lower effort first
        self.assertEqual((first['provider'], first.get('endpoint')), ('vendor-a', None))
        self.assertEqual((second.get('provider'), second['endpoint']), (None, 'pinned-endpoint'))
        old = {item['model']: item['strategy_id'] for item in before['rows'][key('implementation', 'medium', 'medium')]}
        self.assertEqual(first['strategy_id'], old['alpha-large'])
        with self.assertRaisesRegex(ValueError, 'Invalid catalog provider'):
            evidence.compile_table(guidance, [dict(catalog[0], provider='p' * 65)], benchmarks)

    def test_invalid_input_is_rejected(self):
        guidance, catalog, benchmarks = inputs()
        guidance[0]['rows'][0]['complexity_band'] = 'enormous'
        with self.assertRaises(ValueError):
            evidence.compile_table(guidance, catalog, benchmarks)
        with self.assertRaises(ValueError):
            evidence.compile_table('x', catalog, benchmarks)

    def test_rows_with_a_role_or_task_family_are_invalid(self):
        for extra in (dict(role='worker'), dict(role='profiler'), dict(task_family='implementation')):
            with self.subTest(extra=extra):
                guidance, catalog, benchmarks = inputs()
                guidance[0]['rows'].append(dict(guidance[0]['rows'][0], **extra))
                with self.assertRaisesRegex(ValueError, 'Invalid guidance row'):
                    evidence.compile_table(guidance, catalog, benchmarks)
        self.assertFalse(hasattr(evidence, 'CONTROL_ROLES'))

    def test_unknown_work_kind_is_rejected(self):
        for kind in ('profiler', 'imple|mentation', None):
            with self.subTest(kind=kind):
                guidance, catalog, benchmarks = inputs()
                guidance[0]['rows'][0]['work_kind'] = kind
                with self.assertRaisesRegex(ValueError, 'Unknown row work kind'):
                    evidence.compile_table(guidance, catalog, benchmarks)

    def test_compile_takes_no_terms_input(self):
        with self.assertRaises(TypeError):
            evidence.compile_table(*inputs(), [])

    def test_inputs_are_not_mutated(self):
        values = inputs()
        before = copy.deepcopy(values)
        evidence.compile_table(*values)
        self.assertEqual(values, before)


class ListPriceTests(unittest.TestCase):
    """F5.2: an optional catalog `list_price` in dollars per million tokens orders each row by value."""

    ROW = key('implementation', 'medium', 'medium')

    def compile(self, strategies, prices):
        guidance, catalog, benchmarks = inputs()
        guidance[0]['rows'][0]['strategies'] = [dict(model=model, effort=effort, harness='native',
                                                     evidence_refs=['guide-1']) for model, effort in strategies]
        for item in catalog:
            if item['model'] in prices:
                item['list_price'] = prices[item['model']]
        return evidence.compile_table(guidance, catalog, benchmarks)

    def order(self, table):
        return [(item['model'], item['effort'], item['list_price_micros']) for item in table['rows'][self.ROW]]

    def test_price_is_three_parts_input_one_part_output(self):
        table = self.compile([('alpha-large', 'high')], {'alpha-large': dict(input=1.0, output=5.0)})
        self.assertEqual(self.order(table), [('alpha-large', 'high', 2_000_000)])
        self.assertIs(type(table['rows'][self.ROW][0]['list_price_micros']), int)
        zero = self.compile([('alpha-large', 'high')], {'alpha-large': dict(input=0, output=0)})
        self.assertEqual(self.order(zero), [('alpha-large', 'high', 0)])

    def test_rows_are_ordered_by_price_then_effort_then_guidance_order(self):
        table = self.compile([('alpha-large', 'max'), ('alpha-large', 'low'), ('alpha-small', 'high'),
                              ('beta-new', 'low'), ('alpha-small', 'low')],
                             {'alpha-large': dict(input=2, output=2), 'alpha-small': dict(input=1, output=1),
                              'beta-new': dict(input=1, output=1)})
        self.assertEqual(self.order(table), [
            ('beta-new', 'low', 1_000_000),  # effort index 0, first of the ties in guidance order
            ('alpha-small', 'low', 1_000_000),
            ('alpha-small', 'high', 1_000_000),
            ('alpha-large', 'low', 2_000_000),
            ('alpha-large', 'max', 2_000_000)])

    def test_unpriced_candidates_come_last(self):
        table = self.compile([('alpha-small', 'low'), ('alpha-large', 'high'), ('beta-new', 'low')],
                             {'alpha-large': dict(input=50, output=50)})
        self.assertEqual(self.order(table), [('alpha-large', 'high', 50_000_000), ('alpha-small', 'low', None),
                                             ('beta-new', 'low', None)])

    def test_large_finite_prices_compile_without_overflow(self):
        largest = 1.7976931348623157e308
        cases = ((dict(input=1e308, output=1e308), int(1e308) * 10**6),
                 (dict(input=largest, output=0), round(Fraction(3 * int(largest), 4) * 10**6)),
                 (dict(input=10**400, output=1), round(Fraction(3 * 10**400 + 1, 4) * 10**6)),
                 (dict(input=0, output=10**400), 10**400 // 4 * 10**6))
        for price, micros in cases:
            with self.subTest(price=price):
                table = self.compile([('alpha-large', 'high')], {'alpha-large': price})
                self.assertEqual(self.order(table), [('alpha-large', 'high', micros)])

    def test_normal_prices_keep_the_stated_rounding(self):
        for price in (dict(input=1.0, output=5.0), dict(input=0.1, output=0.2), dict(input=2.5e-6, output=2.5e-6),
                      dict(input=1.5e-6, output=0.5e-6), dict(input=3, output=0), dict(input=0.15, output=0.6)):
            with self.subTest(price=price):
                expected = round((3 * price['input'] + price['output']) / 4 * 1_000_000)
                table = self.compile([('alpha-large', 'high')], {'alpha-large': price})
                self.assertEqual(self.order(table), [('alpha-large', 'high', expected)])

    def test_invalid_list_price_is_refused(self):
        for value in (dict(input=-1, output=1), dict(input=1, output=float('nan')), dict(input=float('inf'), output=1),
                      dict(input=True, output=1), dict(input='1', output=1), dict(input=1), dict(input=1, output=1,
                      cached=1), [1, 1], 1.0):
            with self.subTest(value=value):
                guidance, catalog, benchmarks = inputs()
                catalog[0]['list_price'] = value
                with self.assertRaisesRegex(ValueError, 'Invalid catalog list price'):
                    evidence.compile_table(guidance, catalog, benchmarks)


class RefreshTests(unittest.TestCase):
    def test_failed_refresh_keeps_last_verified_table_and_original_age(self):
        table = evidence.compile_table(*inputs(), now='2026-11-01T00:00:00Z')

        def broken():
            raise OSError('offline')
        kept = evidence.refresh_table(table, broken, now='2026-11-08T00:00:00Z')
        self.assertEqual(kept['version'], table['version'])
        self.assertEqual(kept['compiled_at'], '2026-11-01T00:00:00Z')
        self.assertTrue(kept['refresh_failed'])
        self.assertEqual(kept['rows'], table['rows'])

    def test_invalid_refresh_keeps_previous_and_success_replaces(self):
        table = evidence.compile_table(*inputs(), now='2026-11-01T00:00:00Z')
        bad = inputs()
        bad[0][0]['rows'][0]['risk_band'] = 'nope'
        self.assertTrue(evidence.refresh_table(table, lambda: bad, now='2026-11-08T00:00:00Z')['refresh_failed'])
        good = inputs()
        good[1] = [item for item in good[1] if item['model'] != 'alpha-small']
        fresh = evidence.refresh_table(table, lambda: good, now='2026-11-08T00:00:00Z')
        self.assertNotEqual(fresh['version'], table['version'])
        self.assertEqual(fresh['compiled_at'], '2026-11-08T00:00:00Z')
        self.assertFalse(fresh.get('refresh_failed'))

    def test_successful_but_empty_compile_keeps_the_previous_rows(self):
        table = evidence.compile_table(*inputs(), now='2026-11-01T00:00:00Z')
        guidance, catalog, benchmarks = inputs()
        every_row_dropped = [dict(guidance[0], rows=[])]
        for load in (lambda: (guidance, [], benchmarks), lambda: (every_row_dropped, catalog, benchmarks)):
            kept = evidence.refresh_table(table, load, now='2026-11-08T00:00:00Z')
            self.assertTrue(kept['refresh_failed'])
            self.assertEqual(kept['compiled_at'], '2026-11-01T00:00:00Z')
            self.assertEqual(kept['rows'], table['rows'])
            self.assertEqual(kept['version'], table['version'])

    def test_any_exception_from_load_or_compile_keeps_the_previous_table(self):
        table = evidence.compile_table(*inputs(), now='2026-11-01T00:00:00Z')
        def boom():
            raise RuntimeError('loader bug')
        self.assertTrue(evidence.refresh_table(table, boom)['refresh_failed'])
        with unittest.mock.patch.object(evidence, 'compile_table', side_effect=ZeroDivisionError):
            self.assertTrue(evidence.refresh_table(table, lambda: inputs())['refresh_failed'])
        self.assertTrue(evidence.refresh_table(table, lambda: 5)['refresh_failed'])

    def test_first_failed_refresh_without_previous_is_an_error(self):
        def broken():
            raise OSError('offline')
        with self.assertRaises(ValueError):
            evidence.refresh_table(None, broken)


class LookupTests(unittest.TestCase):
    def test_lookup_returns_strategies_or_an_empty_recommendation(self):
        table = evidence.compile_table(*inputs())
        profile = dict(role='software-engineer', work_kind='implementation', complexity_band='medium',
                       risk_band='medium')
        found = evidence.lookup(table, profile)
        self.assertEqual([item['model'] for item in found], ['alpha-small', 'alpha-large'])  # F5.2 effort order
        blocked = evidence.lookup(table, dict(profile, role='code-reviewer', work_kind='review'))
        self.assertEqual(blocked, evidence.NO_RECOMMENDATION)
        self.assertNotIn('blocked', blocked)
        self.assertEqual(evidence.lookup(None, profile), evidence.NO_RECOMMENDATION)


class NoHardCodedModelTests(unittest.TestCase):
    PATTERN = re.compile(r'\b(gpt|claude|gemini|opus|sonnet|haiku|codex|glm|nemotron|llama|qwen|deepseek|mistral|'
                         r'grok|kimi|o[134](?:-mini)?)[-_ .]?\d|\b(opus|sonnet|haiku|nemotron|glm)\b', re.I)

    def test_routing_sources_name_no_commercial_model_or_effort_ladder(self):
        files = sorted((ROOT / 'runtime').glob('routing*.py'))
        self.assertTrue(files)
        for path in files:
            text = path.read_text()
            self.assertIsNone(self.PATTERN.search(text), path.name)
            self.assertNotRegex(text, r"['\"](xhigh|minimal)['\"]", path.name)


if __name__ == '__main__':
    unittest.main()
