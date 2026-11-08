"""Deterministic ranker: table lookup only, narrowing constraints, no calls, no authority."""
import copy
import socket
import subprocess
import unittest
from unittest.mock import patch

from runtime import routing


def strategy(name, model, effort='low', harness='native', **extra):
    return dict(strategy_id=name, model=model, effort=effort, harness=harness, evidence_refs=['e'], **extra)


def table(rows, version='v1'):
    return dict(schema=1, version=version,
                rows={routing.row_key(*key): value for key, value in rows.items()})


TABLE = table({
    ('worker', 'implementation', 'medium', 'medium'): [strategy('s1', 'm-a', 'high'), strategy('s2', 'm-b')],
    ('worker', 'implementation', 'high', 'high'): [strategy('s3', 'm-a', 'max', context_limit=1000)],
    ('worker', 'research', 'critical', 'critical'): [strategy('s4', 'm-c')],
    ('worker', 'research', 'low', 'low'): [strategy('s5', 'm-d')],
})


def profile(**changes):
    value = dict(role='worker', task_family='implementation', complexity_band='medium', risk_band='medium',
                 semantic_digest='d' * 64, context=dict(tokens=10, kind='estimated'), tools=[])
    value.update(changes)
    return value


class RankTests(unittest.TestCase):
    def test_exact_row_returns_its_order(self):
        result = routing.rank(profile(), TABLE, {})
        self.assertEqual(result['strategy_ids'], ['s1', 's2'])
        self.assertEqual(result['table_version'], 'v1')
        self.assertEqual(result['profile_digest'], 'd' * 64)
        self.assertNotIn('ranking_conservative', result['reason_codes'])

    def test_missing_row_uses_next_stricter_band(self):
        result = routing.rank(profile(complexity_band='high', risk_band='medium'), TABLE, {})
        self.assertEqual(result['strategy_ids'], ['s3'])
        self.assertIn('ranking_stricter_band', result['reason_codes'])

    def test_stricter_search_never_goes_to_a_weaker_band(self):
        result = routing.rank(profile(task_family='research', complexity_band='medium', risk_band='medium'), TABLE, {})
        self.assertEqual(result['strategy_ids'], ['s4'])

    def test_no_stricter_row_uses_strongest_qualified_strategy_conservatively(self):
        result = routing.rank(profile(complexity_band='critical', risk_band='critical'), TABLE, {})
        self.assertEqual(result['strategy_ids'], ['s4'])
        self.assertIn('ranking_conservative', result['reason_codes'])
        unknown_family = routing.rank(profile(task_family='novel'), TABLE, {})
        self.assertIn('ranking_conservative', unknown_family['reason_codes'])
        self.assertEqual(unknown_family['strategy_ids'], ['s4'])

    def test_conservative_fallback_never_uses_a_weaker_row(self):
        weak = table({('worker', 'implementation', 'low', 'low'): [strategy('w1', 'm-a')]})
        result = routing.rank(profile(complexity_band='critical', risk_band='critical'), weak, {})
        self.assertEqual(result['strategy_ids'], [])
        self.assertIn('no_recommendation', result['reason_codes'])
        one_band = routing.rank(profile(complexity_band='low', risk_band='critical'), weak, {})
        self.assertEqual(one_band['strategy_ids'], [])

    def test_malformed_table_keys_are_ignored(self):
        broken = dict(TABLE, rows=dict(TABLE['rows'], **{'worker|x|bogus|low': [strategy('bad', 'm-q')], 'junk': []}))
        self.assertEqual(routing.rank(profile(), broken, {})['strategy_ids'], ['s1', 's2'])
        self.assertEqual(routing.rank(profile(complexity_band='critical', risk_band='critical'), broken, {})['strategy_ids'], ['s4'])

    def test_role_without_rows_gives_no_recommendation(self):
        result = routing.rank(profile(role='review'), TABLE, {})
        self.assertEqual(result['strategy_ids'], [])
        self.assertIn('no_recommendation', result['reason_codes'])

    def test_constraints_only_narrow(self):
        result = routing.rank(profile(), TABLE, dict(allowed_models=['m-b', 'm-zzz']))
        self.assertEqual(result['strategy_ids'], ['s2'])
        self.assertIn('constraints_applied', result['reason_codes'])
        result = routing.rank(profile(), TABLE, dict(excluded_models=['m-a']))
        self.assertEqual(result['strategy_ids'], ['s2'])
        result = routing.rank(profile(), TABLE, dict(allowed_efforts=['high'], allowed_harnesses=['native']))
        self.assertEqual(result['strategy_ids'], ['s1'])

    def test_constraint_naming_unlisted_model_never_widens(self):
        result = routing.rank(profile(), TABLE, dict(allowed_models=['m-not-in-table']))
        self.assertEqual(result['strategy_ids'], [])
        self.assertIn('constraints_exclude_all', result['reason_codes'])

    def test_constraints_exclusion_does_not_fall_back_to_a_wider_row(self):
        result = routing.rank(profile(), TABLE, dict(allowed_models=['m-c']))
        self.assertEqual(result['strategy_ids'], [])

    def test_context_limit_filters_strategies_that_cannot_fit(self):
        result = routing.rank(profile(complexity_band='high', risk_band='high', context=dict(tokens=5000, kind='measured')),
                              TABLE, {})
        self.assertEqual(result['strategy_ids'], [])
        self.assertIn('context_exceeds_limit', result['reason_codes'])

    def test_unknown_constraint_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            routing.rank(profile(), TABLE, dict(allow_everything=True))

    def test_table_refresh_reranks_without_reprofiling(self):
        value = profile()
        before = copy.deepcopy(value)
        refreshed = table({('worker', 'implementation', 'medium', 'medium'): [strategy('s9', 'm-z')]}, version='v2')
        first, second = routing.rank(value, TABLE, {}), routing.rank(value, refreshed, {})
        self.assertEqual(value, before)
        self.assertEqual(second['strategy_ids'], ['s9'])
        self.assertEqual(first['profile_digest'], second['profile_digest'])
        self.assertNotEqual(first['table_version'], second['table_version'])

    def test_ranking_is_deterministic_and_does_not_mutate_inputs(self):
        before = copy.deepcopy(TABLE)
        self.assertEqual(routing.rank(profile(), TABLE, {}), routing.rank(profile(), TABLE, {}))
        self.assertEqual(TABLE, before)

    def test_ranking_makes_no_network_or_subprocess_calls(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('ranking must not use the network or subprocesses')
        with patch.object(socket.socket, 'connect', forbidden), patch.object(socket, 'create_connection', forbidden), \
                patch.object(subprocess, 'Popen', forbidden), patch.object(subprocess, 'run', forbidden):
            self.assertEqual(routing.rank(profile(), TABLE, {})['strategy_ids'], ['s1', 's2'])
            routing.rank(profile(role='review'), TABLE, dict(allowed_models=['x']))

    def test_result_exposes_the_cache_key_inputs(self):
        result = routing.rank(profile(review_policy='strict', scope_ref='scope-a'), TABLE, {})
        self.assertEqual((result['review_policy'], result['scope_ref']), ('strict', 'scope-a'))

    def test_empty_recommendation_is_never_a_refusal(self):
        for bad in (dict(schema=1, version='v0', rows={}), dict(version='v0'), None, {}, 'junk'):
            result = routing.rank(profile(), bad, {})
            self.assertEqual(result['strategy_ids'], [])
            self.assertIn('no_recommendation', result['reason_codes'])
            self.assertNotIn('blocked', result)
            self.assertNotIn('blocked', str(result).lower())
        stale = dict(TABLE, refresh_failed=True)
        self.assertEqual(routing.rank(profile(), stale, {})['strategy_ids'], ['s1', 's2'])

    def test_result_carries_no_spending_authority(self):
        result = routing.rank(profile(), TABLE, {})
        self.assertEqual(set(result), {'strategy_ids', 'reason_codes', 'table_version', 'profile_digest', 'review_policy',
                                       'scope_ref'})


if __name__ == '__main__':
    unittest.main()
