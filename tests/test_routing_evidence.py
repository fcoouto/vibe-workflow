"""Strategy table compilation: evidence-qualified, live-catalog-checked, never guessed."""
import copy
import json
from pathlib import Path
import re
import unittest

from runtime import routing, routing_evidence as evidence

FIXTURES = Path(__file__).parent / 'fixtures' / 'smart-routing'
ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def inputs():
    return [load(name) for name in ('guidance', 'catalog', 'benchmarks', 'terms')]


def key(role, family, complexity, risk):
    return routing.row_key(role, family, complexity, risk)


class CompileTests(unittest.TestCase):
    def setUp(self):
        self.table = evidence.compile_table(*inputs(), now='2026-11-08T00:00:00Z')

    def models(self, row):
        return [(item['model'], item['effort']) for item in self.table['rows'][row]]

    def test_rows_are_versioned_ordered_and_cite_evidence(self):
        row = key('worker', 'implementation', 'medium', 'medium')
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
        row = key('worker', 'implementation', 'high', 'high')
        self.assertEqual(self.models(row), [('alpha-large', 'max')])
        reasons = {item['reason'] for item in self.table['dropped']}
        self.assertTrue({'model_not_in_catalog', 'effort_unsupported'} <= reasons)

    def test_new_catalog_model_needs_cited_evidence(self):
        self.assertNotIn(key('worker', 'implementation', 'low', 'low'), self.table['rows'])
        guidance, catalog, benchmarks, terms = inputs()
        guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = ['bench-beta']
        table = evidence.compile_table(guidance, catalog, benchmarks, terms)
        self.assertEqual([item['model'] for item in table['rows'][key('worker', 'implementation', 'low', 'low')]],
                         ['beta-new'])
        for refs in (['bench-unknown'], ['guide-1', 'bench-unknown']):
            guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = refs
            self.assertNotIn(key('worker', 'implementation', 'low', 'low'),
                             evidence.compile_table(guidance, catalog, benchmarks, terms)['rows'])

    def test_benchmark_for_another_model_does_not_qualify(self):
        guidance, catalog, benchmarks, terms = inputs()
        benchmarks[0]['model'] = 'alpha-small'
        guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = ['bench-beta']
        table = evidence.compile_table(guidance, catalog, benchmarks, terms)
        self.assertNotIn(key('worker', 'implementation', 'low', 'low'), table['rows'])

    def test_benchmark_for_another_task_family_does_not_qualify(self):
        guidance, catalog, benchmarks, terms = inputs()
        benchmarks[0]['task_family'] = 'research'
        guidance[0]['rows'][2]['strategies'][0]['evidence_refs'] = ['bench-beta']
        table = evidence.compile_table(guidance, catalog, benchmarks, terms)
        self.assertNotIn(key('worker', 'implementation', 'low', 'low'), table['rows'])

    def test_duplicate_catalog_models_and_bad_check_times_are_rejected(self):
        guidance, catalog, benchmarks, terms = inputs()
        with self.assertRaises(ValueError):
            evidence.compile_table(guidance, catalog + [catalog[0]], benchmarks, terms)
        guidance[0]['checked_at'] = 'yesterday'
        with self.assertRaises(ValueError):
            evidence.compile_table(guidance, catalog, benchmarks, terms)

    def test_blocked_result_is_not_shared_state(self):
        table = evidence.compile_table(*inputs())
        result = evidence.lookup(table, dict(role='review', task_family='x', complexity_band='low', risk_band='low'))
        result['reason'] = 'tampered'
        self.assertEqual(evidence.BLOCKED['reason'], 'no_strategy')

    def test_removed_catalog_model_disappears_on_recompile(self):
        guidance, catalog, benchmarks, terms = inputs()
        catalog = [item for item in catalog if item['model'] != 'alpha-small']
        table = evidence.compile_table(guidance, catalog, benchmarks, terms)
        self.assertEqual([m for m, _ in [(i['model'], 0) for i in table['rows'][
            key('worker', 'implementation', 'medium', 'medium')]]], ['alpha-large'])

    def test_harness_must_match_when_catalog_declares_harnesses(self):
        guidance, catalog, benchmarks, terms = inputs()
        catalog[0]['harnesses'] = ['other']
        table = evidence.compile_table(guidance, catalog, benchmarks, terms)
        self.assertNotIn('alpha-large', [i['model'] for i in table['rows'][
            key('worker', 'implementation', 'medium', 'medium')]])
        self.assertIn('harness_unsupported', {item['reason'] for item in table['dropped']})

    def test_control_plane_candidates_need_zero_retention_terms(self):
        row = key('profiler', 'general', 'low', 'low')
        self.assertEqual([item['model'] for item in self.table['rows'][row]], ['free-open'])
        self.assertIn('control_plane_terms', {item['reason'] for item in self.table['dropped']})

    def test_control_plane_missing_terms_excluded_and_opt_in_allows_non_forbidden(self):
        guidance, catalog, benchmarks, terms = inputs()
        self.assertNotIn(key('profiler', 'general', 'low', 'low'),
                         evidence.compile_table(guidance, catalog, benchmarks, [])['rows'])
        terms[1].update(forbids_confidential=False, user_opt_in=True)
        table = evidence.compile_table(guidance, catalog, benchmarks, terms)
        self.assertEqual([i['model'] for i in table['rows'][key('profiler', 'general', 'low', 'low')]],
                         ['free-open', 'free-private'])
        terms[1].update(forbids_confidential=True)
        table = evidence.compile_table(guidance, catalog, benchmarks, terms)
        self.assertEqual([i['model'] for i in table['rows'][key('profiler', 'general', 'low', 'low')]],
                         ['free-open'])

    def test_worker_rows_do_not_need_terms(self):
        self.assertIn(key('worker', 'implementation', 'medium', 'medium'), self.table['rows'])

    def test_invalid_input_is_rejected(self):
        guidance, catalog, benchmarks, terms = inputs()
        guidance[0]['rows'][0]['complexity_band'] = 'enormous'
        with self.assertRaises(ValueError):
            evidence.compile_table(guidance, catalog, benchmarks, terms)
        with self.assertRaises(ValueError):
            evidence.compile_table('x', catalog, benchmarks, terms)

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

    def test_first_failed_refresh_without_previous_is_an_error(self):
        def broken():
            raise OSError('offline')
        with self.assertRaises(ValueError):
            evidence.refresh_table(None, broken)


class LookupTests(unittest.TestCase):
    def test_lookup_returns_exact_row_or_blocked(self):
        table = evidence.compile_table(*inputs())
        profile = dict(role='worker', task_family='implementation', complexity_band='medium', risk_band='medium')
        found = evidence.lookup(table, profile)
        self.assertEqual([item['model'] for item in found], ['alpha-large', 'alpha-small'])
        blocked = evidence.lookup(table, dict(profile, role='review'))
        self.assertEqual(blocked, evidence.BLOCKED)


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
