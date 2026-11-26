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


def legacy_row(percent, reset):
    return dict(percent=percent, resets_at=reset, scope=None)


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

    def test_raw_only_weekly_all_is_seven_day(self):
        evidence = self.evidence(usage(limits=[weekly_all(100, stamp(NOW + 3600))]))
        self.assertEqual(evidence['meters']['7d'], {'exhausted': True, 'reset_at': NOW + 3600})
        self.assertNotIn('5h', evidence['meters'])
        self.assertTrue(evidence['scopes_complete'])

    def test_named_weekly_reset_must_match_the_whole_second(self):
        boundary = self.evidence(usage(seven=window(50, stamp(NOW + 3600, '.999')),
                                       limits=[weekly_all(50, stamp(NOW + 3601, '.001'))]))
        self.assertNotIn('7d', boundary['meters'])
        self.assertTrue(boundary['scopes_complete'])
        different = self.evidence(usage(seven=window(50, stamp(NOW + 3600)),
                                        limits=[weekly_all(50, stamp(NOW + 7200))]))
        self.assertNotIn('7d', different['meters'])
        self.assertTrue(different['scopes_complete'])
        same = self.evidence(usage(seven=window(50, stamp(NOW + 3600, '.999')),
                                   limits=[weekly_all(50, stamp(NOW + 3600, '.001'))]))
        self.assertEqual(same['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})

    def test_conflicting_named_weekly_makes_only_seven_day_unknown(self):
        evidence = self.evidence(usage(five=window(20, stamp(NOW + 3600)), seven=window(50, stamp(NOW + 3600)),
                                       limits=[weekly_all(60, stamp(NOW + 3600, '.500'))]))
        self.assertNotIn('7d', evidence['meters'])
        self.assertEqual(evidence['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertTrue(evidence['scopes_complete'])
        malformed = self.evidence(usage(seven=window(50, stamp(NOW + 3600)),
                                        limits=[weekly_all('high', stamp(NOW + 3600))]))
        self.assertNotIn('7d', malformed['meters'])
        self.assertTrue(malformed['scopes_complete'])

    def test_five_hour_is_unaffected_by_named_weekly_at_the_same_reset(self):
        evidence = self.evidence(usage(five=window(20, stamp(NOW + 3600)), limits=[weekly_all(90, stamp(NOW + 3600))]))
        self.assertEqual(evidence['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertEqual(evidence['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertTrue(evidence['scopes_complete'])

    def test_differing_named_weekly_reset_keeps_five_hour(self):
        evidence = self.evidence(usage(five=window(20, stamp(NOW + 1800)), seven=window(50, stamp(NOW + 3600)),
                                       limits=[weekly_all(50, stamp(NOW + 3601))]))
        self.assertEqual(evidence['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 1800})
        self.assertNotIn('7d', evidence['meters'])
        self.assertTrue(evidence['scopes_complete'])

    def test_named_weekly_duplicates_must_agree(self):
        agreeing = self.evidence(usage(limits=[weekly_all(40, stamp(NOW + 3600)),
                                               weekly_all(40.5, stamp(NOW + 3600))]))
        self.assertEqual(agreeing['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})
        conflicting = self.evidence(usage(limits=[weekly_all(40, stamp(NOW + 3600)),
                                                  weekly_all(70, stamp(NOW + 3600))]))
        self.assertNotIn('7d', conflicting['meters'])
        self.assertTrue(conflicting['scopes_complete'])

    def test_untyped_legacy_row_attributes_by_reset_and_named_weekly_does_not(self):
        legacy = self.evidence(usage(five=window(20, stamp(NOW + 3600)), limits=[legacy_row(20, stamp(NOW + 3600))]))
        self.assertEqual(legacy['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertNotIn('7d', legacy['meters'])
        self.assertTrue(legacy['scopes_complete'])
        named = self.evidence(usage(five=window(20, stamp(NOW + 3600)), limits=[weekly_all(20, stamp(NOW + 3600))]))
        self.assertEqual(named['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertEqual(named['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 3600})

    def test_untyped_legacy_row_that_matches_none_or_both_is_unknown(self):
        none = self.evidence(usage(seven=window(50, stamp(NOW + 3600)), limits=[legacy_row(50, stamp(NOW + 7200))]))
        self.assertEqual(none['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertFalse(none['scopes_complete'])
        both = self.evidence(usage(five=window(50, stamp(NOW + 3600)), seven=window(50, stamp(NOW + 3600)),
                                   limits=[legacy_row(50, stamp(NOW + 3600))]))
        self.assertEqual(both['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertFalse(both['scopes_complete'])

    def test_legacy_readings_must_agree_with_each_other(self):
        conflicting = self.evidence(usage(five=window(40, stamp(NOW + 3600)),
                                          limits=[legacy_row(39.1, stamp(NOW + 3600)),
                                                  legacy_row(40.9, stamp(NOW + 3600))]))
        self.assertNotIn('5h', conflicting['meters'])
        self.assertTrue(conflicting['scopes_complete'])
        named = self.evidence(usage(seven=window(40, stamp(NOW + 3600)),
                                    limits=[weekly_all(39.1, stamp(NOW + 3600)), legacy_row(40.9, stamp(NOW + 3600))]))
        self.assertNotIn('7d', named['meters'])
        self.assertTrue(named['scopes_complete'])
        agreeing = self.evidence(usage(five=window(40, stamp(NOW + 3600)),
                                       limits=[legacy_row(39.5, stamp(NOW + 3600)),
                                               legacy_row(40.5, stamp(NOW + 3600))]))
        self.assertEqual(agreeing['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 3600})

    def test_explicit_null_scoped_inventory_is_incomplete(self):
        null = usage(seven=window(20, stamp(NOW + 3600)))
        null['rate_limits']['model_scoped'] = None
        evidence = self.evidence(null)
        self.assertEqual(evidence['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertFalse(evidence['scopes_complete'])
        absent = usage(seven=window(20, stamp(NOW + 3600)))
        del absent['rate_limits']['model_scoped']
        self.assertTrue(self.evidence(absent)['scopes_complete'])

    def test_oversized_numbers_never_escape_the_parser(self):
        huge = 10 ** 400
        for started, received in ((huge, huge), (NOW, huge), (huge, huge + 1)):
            with self.subTest(started_huge=started == huge, received_huge=received == huge):
                self.assertIsNone(self.evidence(usage(), started=started, received=received))
        for percent in (huge, -huge):
            with self.subTest(positive=percent > 0):
                seven = self.evidence(usage(seven=dict(utilization=percent, resets_at=stamp(NOW + 3600))))
                self.assertNotIn('7d', seven['meters'])
                named = self.evidence(usage(limits=[weekly_all(percent, stamp(NOW + 3600))]))
                self.assertNotIn('7d', named['meters'])
                legacy = self.evidence(usage(five=window(20, stamp(NOW + 3600)),
                                             limits=[legacy_row(percent, stamp(NOW + 3600))]))
                self.assertNotIn('5h', legacy['meters'])
                self.assertFalse(legacy['scopes_complete'])
                listed = self.evidence(usage(model_scoped=[scoped('Claude Example', percent, stamp(NOW + 3600))]))
                self.assertNotIn('family:example', listed['meters'])
                self.assertFalse(listed['scopes_complete'])

    def test_named_unknown_kind_is_not_attributed(self):
        limit = dict(kind='monthly', scope=None, percent=90, resets_at=stamp(NOW + 3600))
        evidence = self.evidence(usage(five=window(20, stamp(NOW + 3600)), limits=[limit]))
        self.assertEqual(evidence['meters']['5h'], {'exhausted': False, 'reset_at': NOW + 3600})
        self.assertFalse(evidence['scopes_complete'])

    def test_account_windows_do_not_depend_on_the_catalog(self):
        self.assertTrue(self.quota.covers('7d', 'example', None))
        self.assertTrue(self.quota.covers('5h', 'example', [dict(alias='example')]))
        evidence = self.evidence(usage(seven=window(20, stamp(NOW + 3600))), [dict(alias='example')])
        self.assertEqual(evidence['meters']['7d'], {'exhausted': False, 'reset_at': NOW + 3600})

    def test_bare_family_alias_needs_one_resolved_id(self):
        models = [dict(alias='example', resolved='claude-example-5-1', efforts=[]),
                  dict(alias='example', resolved='claude-example-5-2', efforts=[])]
        evidence = self.evidence(usage(model_scoped=[scoped('Claude Example', 40, stamp(NOW + 3600))]), models)
        self.assertNotIn('family:example', evidence['meters'])
        self.assertFalse(evidence['scopes_complete'])
        self.assertFalse(self.quota.covers('family:example', 'example', models))

    def test_exact_resolved_scope_rejects_a_conflicting_alias(self):
        models = [dict(alias='example-5-2', resolved='claude-other-5-2', efforts=[]),
                  dict(alias='alias-x', resolved='claude-example-5-2', efforts=[])]
        limits = [weekly_scoped('Claude Example 5.2', 40, stamp(NOW + 3600))]
        evidence = self.evidence(usage(limits=limits), models)
        self.assertNotIn('model:example-5-2', evidence['meters'])
        self.assertFalse(evidence['scopes_complete'])
        self.assertFalse(self.quota.covers('model:example-5-2', 'example-5-2', models))

    def test_family_is_the_first_normalized_token(self):
        models = [dict(alias='sonnet', resolved='claude-3-5-sonnet', efforts=[])]
        evidence = self.evidence(usage(limits=[weekly_scoped('Claude Sonnet', 40, stamp(NOW + 3600))]), models)
        self.assertNotIn('family:sonnet', evidence['meters'])
        self.assertFalse(evidence['scopes_complete'])
        self.assertFalse(self.quota.covers('family:sonnet', 'claude-3-5-sonnet', models))

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


INVENTED = dict(alias='invented', resolved='claude-example-5-3', efforts=[])


class ClaudeQuotaRecordTest(unittest.TestCase):
    def setUp(self):
        try:
            self.quota = importlib.import_module('runtime.claude_quota')
        except ImportError:
            self.fail('claude_quota record is missing')
        self.models = [INVENTED]
        self.reset = NOW + 3600
        self.now = NOW + 200
        self.marked = self.quota.merge({}, self.sample(self.mark(True), NOW - 60, NOW), now=NOW)

    def sample(self, meters, earliest, latest, complete=False):
        return dict(meters=meters, earliest=earliest, latest=latest, scopes_complete=complete)

    def mark(self, exhausted, key='family:example', reset=None):
        return {key: dict(exhausted=exhausted, reset_at=self.reset if reset is None else reset)}

    def left(self, record, now, model='invented', models=None):
        return self.quota.remaining(record, model, self.models if models is None else models, now=now)

    def test_mark_expires_at_reset(self):
        self.assertEqual(self.left(self.marked, self.reset - 1), 1)
        self.assertIsNone(self.left(self.marked, self.reset))

    def test_overlapping_healthy_interval_does_not_replace_a_mark(self):
        healthy = self.sample(self.mark(False), NOW - 30, NOW + 30)
        self.assertEqual(self.quota.merge(self.marked, healthy, now=self.now), self.marked)

    def test_recovery_and_older_or_boundary_positives_do_not_recreate_a_pause(self):
        cleared = self.quota.merge(self.marked, self.sample(self.mark(False), NOW + 1, NOW + 61), now=self.now)
        self.assertIsNone(self.left(cleared, self.now))
        delayed = self.sample(self.mark(True), NOW + 50, NOW + 111)
        self.assertEqual(self.quota.merge(cleared, delayed, now=self.now), cleared)
        boundary = self.sample(self.mark(True), NOW + 61, NOW + 121)
        self.assertEqual(self.quota.merge(cleared, boundary, now=self.now), cleared)
        newer = self.sample(self.mark(True), NOW + 62, NOW + 122)
        restored = self.quota.merge(cleared, newer, now=self.now)
        self.assertEqual(self.left(restored, self.now), self.reset - self.now)

    def test_healthy_meter_committed_first_blocks_an_older_positive(self):
        healthy = self.quota.merge({}, self.sample(self.mark(False), NOW + 140, NOW + 200), now=self.now)
        late = self.quota.merge(healthy, self.sample(self.mark(True), NOW + 40, NOW + 100), now=self.now)
        self.assertIsNone(self.left(late, self.now))

    def test_incomplete_samples_clear_a_mark_only_through_their_own_meter(self):
        incomplete = self.sample({}, NOW + 101, NOW + 161)
        self.assertEqual(self.quota.merge(self.marked, incomplete, now=self.now), self.marked)
        recovery = self.sample(self.mark(False), NOW + 101, NOW + 161)
        self.assertIsNone(self.left(self.quota.merge(self.marked, recovery, now=self.now), self.now))

    def test_complete_empty_inventory_clears_only_a_newer_omitted_mark(self):
        absent = self.quota.merge(self.marked, self.sample({}, NOW + 101, NOW + 161, complete=True), now=self.now)
        self.assertIsNone(self.left(absent, self.now))
        self.assertEqual(absent['inventory'], dict(scopes=[], earliest=NOW + 101, latest=NOW + 161))
        overlapping = self.sample(self.mark(True), NOW + 110, NOW + 170, complete=True)
        self.assertEqual(self.quota.merge(absent, overlapping, now=self.now), absent)
        renewed = self.quota.merge(absent, self.sample(self.mark(True), NOW + 120, NOW + 180), now=self.now)
        self.assertEqual(renewed, absent)
        restored = self.quota.merge(absent, self.sample(self.mark(True), NOW + 162, NOW + 222), now=NOW + 222)
        self.assertEqual(self.left(restored, NOW + 222), self.reset - NOW - 222)

    def test_empty_inventory_does_not_fence_weekly_or_listed_scopes(self):
        absent = self.quota.merge(self.marked, self.sample({}, NOW + 101, NOW + 161, complete=True), now=self.now)
        weekly = self.sample({'7d': dict(exhausted=True, reset_at=self.reset)}, NOW + 110, NOW + 170)
        self.assertEqual(self.left(self.quota.merge(absent, weekly, now=self.now), self.now), self.reset - self.now)
        listed = dict(meters={}, inventory=dict(scopes=['family:example'], earliest=NOW + 101, latest=NOW + 161))
        positive = self.sample(self.mark(True), NOW + 110, NOW + 170)
        self.assertEqual(self.left(self.quota.merge(listed, positive, now=self.now), self.now), self.reset - self.now)

    def test_complete_sample_stores_its_scopes_as_inventory(self):
        record = self.quota.merge({}, self.sample(self.mark(False), NOW + 1, NOW + 61, complete=True), now=self.now)
        self.assertEqual(record['inventory'], dict(scopes=['family:example'], earliest=NOW + 1, latest=NOW + 61))
        self.assertEqual(record['meters']['family:example'],
                         dict(exhausted=False, reset_at=self.reset, earliest=NOW + 1, latest=NOW + 61))

    def test_capacity_keeps_positives_before_recent_healthy_scopes(self):
        healthy = {'model:m%d' % i: dict(exhausted=False, reset_at=self.reset) for i in range(8)}
        full = self.quota.merge(self.marked, self.sample(healthy, NOW + 1, NOW + 61), now=self.now)
        scoped = sorted(key for key in full['meters'] if key.startswith(('family:', 'model:')))
        self.assertEqual(len(scoped), 8)
        self.assertIn('family:example', scoped)
        self.assertNotIn('model:m7', scoped)

    def test_malformed_records_and_samples_cannot_exclude_or_clear(self):
        bad = dict(meters={'family:example': dict(exhausted='yes', reset_at=self.reset)},
                   inventory=dict(scopes=[], earliest=NOW, latest='x'))
        self.assertIsNone(self.left(bad, self.now))
        self.assertIsNone(self.left(None, self.now))
        self.assertEqual(self.quota.merge(self.marked, None, now=self.now), self.marked)
        bogus = self.sample({'family:example': dict(exhausted=False, reset_at=self.reset)}, NOW + 101, 'x', True)
        self.assertEqual(self.quota.merge(self.marked, bogus, now=self.now), self.marked)

    def test_account_wide_mark_needs_no_catalog_and_scoped_mark_needs_a_bound_one(self):
        weekly = self.quota.merge({}, self.sample({'7d': dict(exhausted=True, reset_at=self.reset)}, NOW - 60, NOW),
                                  now=NOW)
        self.assertEqual(self.left(weekly, self.now, models=[]), self.reset - self.now)
        self.assertIsNone(self.left(self.marked, self.now, models=[]))
        self.assertEqual(self.left(self.marked, self.now, model='claude-example-5-3'), self.reset - self.now)


if __name__ == '__main__':
    unittest.main()
