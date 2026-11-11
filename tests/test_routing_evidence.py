"""Strategy table compilation: evidence-qualified, live-catalog-checked, never guessed."""
import copy
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
        self.assertEqual(self.models(row), [('alpha-large', 'high'), ('alpha-small', 'low')])
        first = self.table['rows'][row][0]
        self.assertEqual(set(first) - {'context_limit'}, {'strategy_id', 'model', 'effort', 'harness', 'evidence_refs'})
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
        self.assertEqual([item['model'] for item in found], ['alpha-large', 'alpha-small'])
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
