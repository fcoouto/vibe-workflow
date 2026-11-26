"""Quota evidence must classify from one native usage answer without snapshot completeness; synthetic answers only."""
from datetime import datetime, timezone
import importlib
import unittest

NOW = 1893570000
MODELS = [
    dict(alias='alias-of-5-2', resolved='claude-example-5-2', efforts=[]),
    dict(alias='alias-b', resolved='claude-example-5-2', efforts=[]),
    dict(alias='example', resolved='claude-example-5-1', efforts=[]),
    dict(alias='other', resolved='claude-other-5-2', efforts=[]),
]


def stamp(at, fraction=''):
    return datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + fraction + 'Z'


def window(percent, reset):
    return dict(utilization=percent, resets_at=reset)


def weekly_all(percent, reset):
    return dict(kind='weekly_all', scope=None, percent=percent, resets_at=reset)


def weekly_scoped(label, percent, reset):
    return dict(kind='weekly_scoped', scope=dict(model=dict(display_name=label)), percent=percent, resets_at=reset)


def scoped(label, percent, reset):
    return dict(display_name=label, utilization=percent, resets_at=reset)


def usage(five=None, seven=None, limits=(), model_scoped=()):
    rate = dict(five_hour=five, seven_day=seven, extra_usage=dict(is_enabled=False), limits=list(limits),
                model_scoped=list(model_scoped))
    return dict(subscription_type='max', rate_limits_available=True, rate_limits=rate)


class ClaudeQuotaTest(unittest.TestCase):
    def setUp(self):
        try:
            self.quota = importlib.import_module('runtime.claude_quota')
        except ImportError:
            self.fail('claude_quota parser is missing')

    def evidence(self, use, models=MODELS, started=NOW + 0.25, received=NOW + 0.75):
        return self.quota.evidence(use, models, started=started, received=received)

    def test_account_wide_without_five_hour(self):
        use = usage(seven=window(100, stamp(NOW + 3600, '.250')),
                    limits=[weekly_all(100, stamp(NOW + 3600, '.750')),
                            weekly_scoped('Claude Example', 71, stamp(NOW + 3600))],
                    model_scoped=[scoped('Claude Example', 71, stamp(NOW + 3600))])
        evidence = self.evidence(use)
        self.assertEqual(evidence['meters']['7d'], {'exhausted': True, 'reset_at': NOW + 3600})
        self.assertFalse(evidence['meters']['family:example']['exhausted'])
        self.assertEqual(evidence['meters']['family:example']['reset_at'], NOW + 3600)
        self.assertNotIn('5h', evidence['meters'])
        self.assertEqual(evidence['earliest'], NOW - 60)
        self.assertEqual(evidence['latest'], NOW + 1)
        self.assertTrue(evidence['scopes_complete'])

    def test_conflicting_percentage_is_unknown(self):
        use = usage(seven=window(100, stamp(NOW + 3600, '.250')),
                    limits=[weekly_all(99.4, stamp(NOW + 3600, '.750'))],
                    model_scoped=[scoped('Claude Example', 100, stamp(NOW + 3600))])
        conflicting = self.evidence(use)
        self.assertNotIn('7d', conflicting['meters'])
        self.assertTrue(conflicting['meters']['family:example']['exhausted'])

    def test_missing_limits_and_header_only_do_not_confirm_reply(self):
        use = usage(seven=window(100, stamp(NOW + 3600)))
        del use['rate_limits']['limits']
        self.assertIsNone(self.evidence(use))
        use = usage(seven=window(100, stamp(NOW + 3600)))
        use['rate_limits']['limits'] = None
        self.assertIsNone(self.evidence(use))
        use = usage(seven=window(100, stamp(NOW + 3600)), limits=[weekly_all(100, stamp(NOW + 3600))])
        use['rate_limits_available'] = False
        self.assertIsNone(self.evidence(use))

    def test_scoped_only_and_exact_coverage(self):
        use = usage(limits=[weekly_scoped('Claude Example 5.2', 40, stamp(NOW + 3600))])
        evidence = self.evidence(use)
        self.assertEqual(evidence['meters']['model:example-5-2'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertNotIn('7d', evidence['meters'])
        covers = self.quota.covers
        self.assertTrue(covers('model:example-5-2', 'alias-of-5-2', MODELS))
        self.assertTrue(covers('model:example-5-2', 'alias-b', MODELS))
        self.assertTrue(covers('model:example-5-2', 'claude-example-5-2', MODELS))
        self.assertFalse(covers('model:example-5-2', 'example', MODELS))
        self.assertFalse(covers('model:example-5-2', 'other', MODELS))
        self.assertFalse(covers('model:example-5-2', 'nothing', MODELS))
        self.assertTrue(covers('family:example', 'alias-of-5-2', MODELS))
        self.assertTrue(covers('family:example', 'example', MODELS))
        self.assertFalse(covers('family:example', 'other', MODELS))
        self.assertTrue(covers('7d', 'alias-of-5-2', MODELS))

    def test_absent_normalized_scopes_are_complete(self):
        use = usage(seven=window(30, stamp(NOW + 3600)), limits=[weekly_all(30, stamp(NOW + 3600))])
        empty = self.evidence(use)
        self.assertTrue(empty['scopes_complete'])
        self.assertEqual(empty['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})

    def test_unknown_or_malformed_raw_entry_keeps_valid_weekly(self):
        use = usage(seven=window(100, stamp(NOW + 3600)),
                    limits=[weekly_all(100, stamp(NOW + 3600)), dict(kind='monthly', percent=3, resets_at=None)])
        partial = self.evidence(use)
        self.assertTrue(partial['meters']['7d']['exhausted'])
        self.assertFalse(partial['scopes_complete'])

    def test_idle_meter_is_unknown(self):
        use = usage(five=dict(utilization=0, resets_at=None), seven=window(30, stamp(NOW + 3600)),
                    limits=[weekly_all(30, stamp(NOW + 3600))])
        idle = self.evidence(use)
        self.assertNotIn('5h', idle['meters'])
        self.assertEqual(idle['meters']['7d']['reset_at'], NOW + 3600)

    def test_unresolved_scope_is_not_empty(self):
        cases = {
            'generic': (MODELS, 'Claude'),
            'unmapped': (MODELS, 'Claude Missing'),
            'versioned_unmapped': (MODELS, 'Claude Example 9.9'),
            'ambiguous': ([dict(alias='example', resolved='claude-example-5-1', efforts=[]),
                           dict(alias='example', resolved='claude-other-1', efforts=[])], 'Claude Example'),
            'family_mismatch': ([dict(alias='example', resolved='claude-other-5-1', efforts=[])], 'Claude Example'),
            'bare_without_alias': ([dict(alias='alias-of-5-2', resolved='claude-example-5-2', efforts=[])],
                                   'Claude Example'),
        }
        for name, (models, label) in cases.items():
            with self.subTest(name):
                evidence = self.evidence(usage(model_scoped=[scoped(label, 40, stamp(NOW + 3600))]), models)
                self.assertIsNotNone(evidence)
                self.assertFalse(evidence['scopes_complete'])
                self.assertFalse([key for key in evidence['meters'] if key.startswith(('family:', 'model:'))])

    def test_scoped_meter_present_but_unusable(self):
        cases = {
            'idle': [scoped('Claude Example', 0, None)],
            'expired': [scoped('Claude Example', 40, stamp(NOW))],
            'conflicting': [scoped('Claude Example', 40, stamp(NOW + 3600)),
                            scoped('Claude Example', 80, stamp(NOW + 3600))],
        }
        for name, entries in cases.items():
            with self.subTest(name):
                unusable = self.evidence(usage(model_scoped=entries))
                self.assertNotIn('family:example', unusable['meters'])
                self.assertFalse(unusable['scopes_complete'])

    def test_malformed_values_and_reset_bounds_yield_no_meter(self):
        reset = stamp(NOW + 3600)
        for value in (None, True, False, float('nan'), float('inf'), -1, 100.5, '50'):
            with self.subTest(percent=value):
                evidence = self.evidence(usage(seven=dict(utilization=value, resets_at=reset)))
                self.assertIsNotNone(evidence)
                self.assertNotIn('7d', evidence['meters'])
        for seven in (dict(utilization=0), dict(utilization=20, resets_at='soon'),
                      dict(utilization=20, resets_at=stamp(NOW)),
                      dict(utilization=20, resets_at=stamp(NOW + 7 * 86400 + 61))):
            with self.subTest(seven=seven):
                self.assertNotIn('7d', self.evidence(usage(seven=seven))['meters'])
        self.assertNotIn('5h', self.evidence(usage(five=window(20, stamp(NOW + 5 * 3600 + 61))))['meters'])
        at_margin = self.evidence(usage(seven=window(20, stamp(NOW + 7 * 86400 + 60))))
        self.assertEqual(at_margin['meters']['7d']['reset_at'], NOW + 7 * 86400 + 60)

    def test_bad_observation_times_return_none(self):
        for started, received in ((None, NOW + 1), (float('nan'), NOW + 1), (NOW, float('inf')), (True, NOW + 1),
                                  (NOW, True), (-1, 5), (NOW + 1, NOW)):
            with self.subTest(started=started, received=received):
                self.assertIsNone(self.evidence(usage(), started=started, received=received))

    def test_weekly_all_alone_is_unknown(self):
        evidence = self.evidence(usage(limits=[weekly_all(100, stamp(NOW + 3600))]))
        self.assertNotIn('7d', evidence['meters'])
        self.assertFalse(evidence['scopes_complete'])

    def test_legacy_repeat_matches_only_the_whole_second_reset(self):
        boundary = self.evidence(usage(seven=window(50, stamp(NOW + 3600, '.999')),
                                       limits=[weekly_all(50, stamp(NOW + 3601, '.001'))]))
        self.assertEqual(boundary['meters']['7d']['reset_at'], NOW + 3600)
        self.assertFalse(boundary['scopes_complete'])
        different = self.evidence(usage(seven=window(50, stamp(NOW + 3600)),
                                        limits=[weekly_all(50, stamp(NOW + 7200))]))
        self.assertEqual(different['meters']['7d']['reset_at'], NOW + 3600)
        self.assertFalse(different['scopes_complete'])
        both = self.evidence(usage(five=window(50, stamp(NOW + 3600)), seven=window(50, stamp(NOW + 3600)),
                                   limits=[weekly_all(50, stamp(NOW + 3600))]))
        self.assertFalse(both['scopes_complete'])

    def test_conflicting_repeat_makes_only_its_meter_unknown(self):
        evidence = self.evidence(usage(seven=window(50, stamp(NOW + 3600)),
                                       limits=[weekly_all(60, stamp(NOW + 3600, '.500'))]))
        self.assertNotIn('7d', evidence['meters'])
        self.assertTrue(evidence['scopes_complete'])
        malformed = self.evidence(usage(seven=window(50, stamp(NOW + 3600)),
                                        limits=[weekly_all('high', stamp(NOW + 3600))]))
        self.assertNotIn('7d', malformed['meters'])
        self.assertFalse(malformed['scopes_complete'])

    def test_weekly_all_with_model_scope_is_unrecognized(self):
        limit = dict(kind='weekly_all', scope=dict(model=dict(display_name='Claude Example')), percent=100,
                     resets_at=stamp(NOW + 3600))
        evidence = self.evidence(usage(seven=window(100, stamp(NOW + 3600)), limits=[limit]))
        self.assertTrue(evidence['meters']['7d']['exhausted'])
        self.assertFalse(evidence['scopes_complete'])

    def test_unrecognized_surfaces_make_inventory_incomplete(self):
        reset = stamp(NOW + 3600)
        listed, legacy, unknown, cove = (usage(seven=window(20, reset)) for _ in range(4))
        listed['rate_limits']['model_scoped'] = {}
        legacy['rate_limits']['seven_day_opus'] = window(1, reset)
        unknown['rate_limits']['cinder_extra'] = window(1, reset)
        cove['rate_limits']['cinder_cove'] = window(1, reset)
        cases = {
            'model_scoped_not_list': listed,
            'legacy_scoped_meter': legacy,
            'unknown_meter_like_field': unknown,
            'known_unknown_meter': cove,
            'non_dict_raw_row': usage(seven=window(20, reset), limits=['weird']),
        }
        for name, use in cases.items():
            with self.subTest(name):
                evidence = self.evidence(use)
                self.assertIsNotNone(evidence)
                self.assertFalse(evidence['scopes_complete'])
        plain = usage(seven=window(20, reset))
        plain['rate_limits']['note'] = 'invented'
        self.assertTrue(self.evidence(plain)['scopes_complete'])

    def test_duplicate_scopes_agree_or_make_inventory_incomplete(self):
        agreeing = self.evidence(usage(model_scoped=[scoped('Claude Example', 40, stamp(NOW + 3600)),
                                                     scoped('Claude Example', 40.5, stamp(NOW + 3600))]))
        self.assertTrue(agreeing['scopes_complete'])
        self.assertEqual(agreeing['meters']['family:example'], {'exhausted': False, 'reset_at': NOW + 3600})
        catalog = [dict(alias='alias-%d' % i, resolved='claude-example-5-%d' % i, efforts=[]) for i in range(1, 10)]
        for count, complete in ((8, True), (9, False)):
            with self.subTest(count=count):
                listed = [scoped('Claude Example 5.%d' % i, 40, stamp(NOW + 3600)) for i in range(1, count + 1)]
                evidence = self.evidence(usage(model_scoped=listed), catalog)
                self.assertEqual(evidence['scopes_complete'], complete)
                models = [key for key in evidence['meters'] if key.startswith('model:')]
                self.assertEqual(len(models), count if complete else 0)

    def test_covers_requires_one_resolution(self):
        ambiguous = [dict(alias='dup', resolved='claude-example-5-2', efforts=[]),
                     dict(alias='dup', resolved='claude-other-5-2', efforts=[])]
        self.assertFalse(self.quota.covers('model:example-5-2', 'dup', ambiguous))
        self.assertFalse(self.quota.covers('family:example', 'dup', ambiguous))
        unresolved = [dict(alias='nores', resolved=None, efforts=[])]
        self.assertFalse(self.quota.covers('family:example', 'nores', unresolved))
        malformed = [dict(alias='example')]
        self.assertFalse(self.quota.covers('model:example-5-2', 'example', malformed))
        self.assertFalse(self.quota.covers('model:example-5-2', 'example', None))
        listed = usage(limits=[weekly_scoped('Claude Example', 1, stamp(NOW + 3600))])
        self.assertFalse(self.evidence(listed, malformed)['scopes_complete'])


if __name__ == '__main__':
    unittest.main()
