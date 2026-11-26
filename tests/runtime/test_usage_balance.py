"""Expiry scoring and comparable slot order at a fixed clock; synthetic meters only, with no reads, probes or model calls."""
import copy
import math
import unittest

from runtime import usage_balance

NOW = 1893570000
HOUR = 3600
SCOPE = 'family:opus'


def meter(used, hours, duration_hours, *, at=NOW, age_offset=0, discrete=True):
    duration = None if duration_hours is None else round(duration_hours * HOUR)
    return dict(used_percent=used, earliest=at - age_offset, latest=at, reset_at=at + round(hours * HOUR),
                duration=duration, discrete=discrete)


def score(family, meters, *, now=NOW):
    return usage_balance.expiry_score(family, meters, now=now)


def go_example(*, rolling_used=60, weekly_used=20, month_used=50):
    meters = {'5h': meter(rolling_used, 1, 5), '7d': meter(weekly_used, 96, 168)}
    if month_used is not None:
        meters['monthly'] = meter(month_used, 240, None)
    return meters


WEEKLY = meter(40, 72, 168)
SHORT = meter(25, 1, 5)
TIMING = meter(50, 5 / 60, 5, age_offset=60)
AGED = meter(40, 72, 168, age_offset=60)
CLOSING = meter(10, 0.5 / 60, 5)

ROUTES = {  # subscription is the exact route path ID; only c and c-alias share one
    'a': dict(subscription='claude-1', price=1.0, score=1.0, expiry=1.0),
    'b': dict(subscription='chatgpt-1', price=1.0, score=1.0, expiry=1.4),
    'c': dict(subscription='go-1', price=1.0, score=1.0, expiry=2.0),
    'c-alias': dict(subscription='go-1', price=1.0, score=1.0, expiry=2.0),
    'small_gain': dict(subscription='chatgpt-1', price=1.0, score=1.0, expiry=1.2),
    'outside_bounds': dict(subscription='chatgpt-1', price=1.501, score=1.0, expiry=3.0),
    'unknown': dict(subscription='chatgpt-1', price=1.0, score=1.0, expiry=None),
}


def reorder_names(names, table=ROUTES):
    ordered = [dict(route=name, price=table[name]['price'], score=table[name]['score']) for name in names]
    subscriptions = {name: table[name]['subscription'] for name in names}
    expiry = {name: table[name]['expiry'] for name in names}
    return [item['route'] for item in usage_balance.reorder(ordered, subscriptions=subscriptions, expiry=expiry)]


def rank_two(*, b_expiry, price_ratio=1.0, score_ratio=1.0, b_subscription='chatgpt-1'):
    ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=price_ratio, score=score_ratio)]
    subscriptions = dict(a='claude-1', b=b_subscription)
    expiry = dict(a=1.0, b=b_expiry)
    return [item['route'] for item in usage_balance.reorder(ordered, subscriptions=subscriptions, expiry=expiry)]


def rank_two_same_subscription(*, b_expiry):
    return rank_two(b_expiry=b_expiry, b_subscription='claude-1')



class UsageBalanceTest(unittest.TestCase):
    def test_reference_and_fresh_reset(self):
        self.assertAlmostEqual(score('chatgpt', {'7d': meter(90, 2, 168)}), 8.4)
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY}), 1.4)
        self.assertAlmostEqual(score('chatgpt', {'7d': meter(0, 168, 168)}), 1)
        self.assertAlmostEqual(score('claude', {'5h': meter(0, 5, 5), '7d': meter(0, 168, 168)}), 1)
        self.assertAlmostEqual(score('claude', {'7d': meter(90.01, 2, 168)}), 1)

    def test_go_allowances(self):
        self.assertAlmostEqual(score('go', go_example(month_used=50)), 2)
        self.assertAlmostEqual(score('go', go_example(month_used=98)), 1)
        self.assertAlmostEqual(score('go', go_example(month_used=None)), 1)
        self.assertAlmostEqual(score('go', go_example(weekly_used=98)), 1)
        self.assertAlmostEqual(score('go', go_example(weekly_used=80)), 1)
        self.assertAlmostEqual(score('go', go_example(rolling_used=98, weekly_used=20)), 1)
        self.assertIsNone(score('go', {'monthly': meter(50, 240, None)}))
        self.assertAlmostEqual(score('go', {'7d': meter(20, 96, 168)}), 1)  # weekly boost needs monthly

    def test_scoped_meters_and_siblings(self):
        self.assertAlmostEqual(score('claude', {'7d': meter(25, 24, 168), SCOPE: meter(98, 24, 168)}), 1)
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY, '5h': SHORT, SCOPE: meter(85, 144, 168)}), 1.4)
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY, '5h': SHORT, SCOPE: meter(20, 96, 168)}), 3.75)
        self.assertIsNone(score('claude', {SCOPE: meter(25, 24, 168)}))
        self.assertAlmostEqual(score('claude', {'7d': meter(40, 72, 168), SCOPE: meter(85, 144, 168)}), 1.4)
        self.assertAlmostEqual(score('chatgpt', {'5h': meter(50, 1, 5)}), 1)
        self.assertAlmostEqual(score('claude', {'5h': SHORT, '7d': meter(85, 144, 168, discrete=False)}), 3.75)
        self.assertAlmostEqual(score('claude', {'5h': SHORT, '7d': meter(50, 84, 168)}), 3.75)  # pace exactly 1
        self.assertIsNone(score('claude', {'7d': meter(25, 24, 168, discrete=False)}))

    def test_duration_keys_protect_headroom_only(self):
        self.assertAlmostEqual(score('chatgpt', {'7d': WEEKLY, 'duration:86400': meter(95, 12, 24)}), 1)
        self.assertAlmostEqual(score('chatgpt', {'5h': SHORT, '7d': WEEKLY, 'duration:86400': meter(80, 12, 24)}),
                               3.75)

    def test_pace_and_timing(self):
        self.assertAlmostEqual(score('claude', {'5h': TIMING, '7d': WEEKLY}), 25)
        aged_time_left = 72 * HOUR + 60  # from earliest, not from the later pick clock
        self.assertAlmostEqual(score('claude', {'7d': AGED}, now=AGED['earliest'] + 899),
                               0.6 * 604800 / aged_time_left)
        self.assertGreater(score('claude', {'7d': AGED}, now=AGED['earliest'] + 899), 1)
        self.assertIsNone(score('claude', {'7d': AGED}, now=AGED['earliest'] + 900))
        self.assertAlmostEqual(score('claude', {'5h': CLOSING, '7d': WEEKLY}, now=CLOSING['reset_at'] - 1), 90)
        self.assertAlmostEqual(score('claude', {'5h': CLOSING, '7d': WEEKLY}, now=CLOSING['reset_at']), 1.4)
        half_5h = meter(0, 0.5 / 60, 5)
        half_7d = meter(0, 0.5 / 60, 168)
        self.assertAlmostEqual(score('claude', {'5h': half_5h, '7d': WEEKLY}), 100)
        self.assertAlmostEqual(score('claude', {'7d': half_7d}), 100)

    def test_fresh_meter_bounds(self):
        self.assertTrue(usage_balance.fresh_meter(CLOSING, now=CLOSING['reset_at'] - 1))
        self.assertFalse(usage_balance.fresh_meter(CLOSING, now=CLOSING['reset_at']))
        self.assertTrue(usage_balance.fresh_meter(AGED, now=AGED['earliest'] + 899))
        self.assertFalse(usage_balance.fresh_meter(AGED, now=AGED['earliest'] + 900))
        self.assertFalse(usage_balance.fresh_meter(WEEKLY, now=NOW - 2))
        self.assertTrue(usage_balance.fresh_meter(dict(WEEKLY, latest=NOW + 1), now=NOW))
        self.assertTrue(usage_balance.fresh_meter(dict(SHORT, reset_at=NOW + 5 * HOUR + 60), now=NOW))
        self.assertFalse(usage_balance.fresh_meter(dict(SHORT, reset_at=NOW + 5 * HOUR + 61), now=NOW))

    def test_expired_weekly_cannot_make_short_positive(self):
        self.assertAlmostEqual(score('claude', {'5h': SHORT, '7d': meter(40, -1, 168)}), 1)

    def test_invalid_readings_yield_no_score(self):
        for used in (True, False, math.nan, math.inf, -1, 100.5):
            with self.subTest(used=used):
                self.assertIsNone(score('claude', {'5h': meter(used, 1, 5)}))
        for field, value in (('extra', 1), ('discrete', 1), ('earliest', float(NOW)), ('reset_at', True),
                             ('duration', 0)):
            with self.subTest(field=field):
                self.assertIsNone(score('claude', {'7d': dict(WEEKLY, **{field: value})}))
        missing = dict(WEEKLY)
        del missing['discrete']
        self.assertIsNone(score('claude', {'7d': missing}))

    def test_unknown_key_is_unknown_by_itself_and_keeps_valid_siblings(self):
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY, 'weird': SHORT}), 1.4)
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY, '5h': SHORT, 'weird': meter(85, 144, 168)}), 3.75)

    def test_rejected_meter_cannot_pace_veto(self):
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY, '5h': SHORT,
                                                SCOPE: dict(meter(85, 144, 168), extra=1)}), 3.75)

    def test_wrong_purported_duration_is_invalid_for_that_meter(self):
        self.assertIsNone(score('chatgpt', {'7d': dict(meter(0, 1, 168), duration=5 * HOUR)}))
        self.assertAlmostEqual(score('claude', {'5h': dict(SHORT, used_percent=95, duration=7 * 24 * HOUR),
                                                '7d': WEEKLY}), 1.4)
        self.assertAlmostEqual(score('claude', {'5h': dict(SHORT, duration=7 * 24 * HOUR), '7d': WEEKLY}), 1.4)

    def test_fresh_unused_window_with_wider_earliest_is_known_at_one(self):
        self.assertAlmostEqual(score('chatgpt', {'7d': meter(0, 168, 168, age_offset=899)}), 1)
        self.assertAlmostEqual(score('claude', {'5h': meter(0, 5, 5, age_offset=899), '7d': meter(0, 168, 168)}), 1)

    def test_comparable_bounds(self):
        base = dict(price=2.0, score=1.0)
        self.assertTrue(usage_balance.comparable(dict(price=3.0, score=0.97), base))
        self.assertFalse(usage_balance.comparable(dict(price=3.0000001, score=1.0), base))
        self.assertFalse(usage_balance.comparable(dict(price=2.0, score=0.969), base))
        self.assertFalse(usage_balance.comparable(dict(price=2.0), base))

    def test_reorder_slots(self):
        self.assertEqual(reorder_names(['a', 'unknown', 'b', 'c', 'c-alias']), ['c', 'unknown', 'c-alias', 'b', 'a'])
        self.assertEqual(reorder_names(['unknown', 'a', 'b']), ['unknown', 'a', 'b'])
        self.assertEqual(reorder_names(['a', 'outside_bounds', 'b']), ['b', 'outside_bounds', 'a'])
        self.assertEqual(reorder_names(['a', 'small_gain', 'b']), ['b', 'small_gain', 'a'])

    def test_reorder_gain_and_bound_boundaries(self):
        self.assertEqual(rank_two(b_expiry=1.01), ['a', 'b'])
        self.assertEqual(rank_two(b_expiry=1.25, price_ratio=1.5, score_ratio=.97), ['b', 'a'])
        self.assertEqual(rank_two(b_expiry=2, price_ratio=1.501), ['a', 'b'])
        self.assertEqual(rank_two(b_expiry=2, score_ratio=.969), ['a', 'b'])
        self.assertEqual(rank_two_same_subscription(b_expiry=2), ['a', 'b'])

    def test_reorder_nonparticipants_keep_position(self):
        ordered = [dict(route=name, price=1.0, score=1.0) for name in ('a', 'b', 'c', 'd')]
        subscriptions = dict(a='p-a', c='p-c', d='p-d')  # b has no subscription mapping
        expiry = dict(a=1.0, b=3.0, d=2.0)  # c has no expiry mapping
        result = usage_balance.reorder(ordered, subscriptions=subscriptions, expiry=expiry)
        self.assertEqual([item['route'] for item in result], ['d', 'b', 'c', 'a'])

    def test_reorder_without_baseline_usable_score_keeps_order(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
        result = usage_balance.reorder(ordered, subscriptions=dict(a='p-a', b='p-b'), expiry=dict(b=2.0))
        self.assertEqual([item['route'] for item in result], ['a', 'b'])

    def test_reorder_does_not_mutate_inputs(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
        subscriptions = dict(a='p-a', b='p-b')
        expiry = dict(a=1.0, b=2.0)
        before = copy.deepcopy((ordered, subscriptions, expiry))
        result = usage_balance.reorder(ordered, subscriptions=subscriptions, expiry=expiry)
        self.assertEqual([item['route'] for item in result], ['b', 'a'])
        self.assertIsNot(result, ordered)
        self.assertEqual((ordered, subscriptions, expiry), before)

    def test_distinct_paths_in_one_family_can_reorder(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
        result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', b='claude-2'),
                                       expiry=dict(a=1.0, b=2.0))
        self.assertEqual([item['route'] for item in result], ['b', 'a'])

    def test_same_path_never_triggers(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
        result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', b='claude-1'),
                                       expiry=dict(a=1.0, b=2.0))
        self.assertEqual([item['route'] for item in result], ['a', 'b'])

    def test_null_or_invalid_identity_is_neutral(self):
        for bad in (None, '', 5, ['x'], False):
            with self.subTest(identity=bad):
                ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
                # A null candidate neither triggers nor reorders.
                result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', b=bad),
                                               expiry=dict(a=1.0, b=2.0))
                self.assertEqual([item['route'] for item in result], ['a', 'b'])
                # A null baseline keeps the baseline order, even with a valid candidate after it.
                result = usage_balance.reorder(ordered, subscriptions=dict(a=bad, b='chatgpt-1'),
                                               expiry=dict(a=1.0, b=2.0))
                self.assertEqual([item['route'] for item in result], ['a', 'b'])

    def test_null_candidate_stays_in_its_slot_and_does_not_trigger(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='n', price=1.0, score=1.0),
                   dict(route='c', price=1.0, score=1.0)]
        result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', n=None, c='chatgpt-1'),
                                       expiry=dict(a=1.0, n=2.0, c=1.4))
        self.assertEqual([item['route'] for item in result], ['c', 'n', 'a'])
        alone = [dict(route='a', price=1.0, score=1.0), dict(route='n', price=1.0, score=1.0)]
        result = usage_balance.reorder(alone, subscriptions=dict(a='claude-1', n=None), expiry=dict(a=1.0, n=2.0))
        self.assertEqual([item['route'] for item in result], ['a', 'n'])

    def test_missing_candidate_identity_stays_in_its_slot(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='m', price=1.0, score=1.0),
                   dict(route='c', price=1.0, score=1.0)]
        result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', c='chatgpt-1'),
                                       expiry=dict(a=1.0, m=5.0, c=1.4))
        self.assertEqual([item['route'] for item in result], ['c', 'm', 'a'])

    def test_oversized_integers_are_invalid_not_crashes(self):
        big = 10 ** 400
        cases = [
            (dict(price=big, score=1.0), dict(price=1.0, score=1.0), dict(b=2.0)),   # candidate price
            (dict(price=1.0, score=big), dict(price=1.0, score=1.0), dict(b=2.0)),   # candidate score
        ]
        for candidate, base_fields, expiry in cases:
            with self.subTest(candidate=candidate):
                ordered = [dict(route='a', **base_fields), dict(route='b', **candidate)]
                result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', b='chatgpt-1'),
                                               expiry=dict(a=1.0, **expiry))
                self.assertEqual([item['route'] for item in result], ['a', 'b'])
        ordered = [dict(route='a', price=big, score=1.0), dict(route='b', price=1.0, score=1.0)]
        result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', b='chatgpt-1'),
                                       expiry=dict(a=1.0, b=2.0))
        self.assertEqual([item['route'] for item in result], ['a', 'b'])  # baseline price
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
        for expiry in (dict(a=1.0, b=big), dict(a=big, b=2.0)):
            with self.subTest(expiry=expiry):
                result = usage_balance.reorder(ordered, subscriptions=dict(a='claude-1', b='chatgpt-1'),
                                               expiry=expiry)
                self.assertEqual([item['route'] for item in result], ['a', 'b'])
        self.assertFalse(usage_balance.comparable(dict(price=big, score=1.0), dict(price=1.0, score=1.0)))

    def test_oversized_meter_values_are_ignored(self):
        big = 10 ** 400
        self.assertIsNone(score('chatgpt', {'7d': meter(big, 168, 168)}))  # oversized percentage
        self.assertIsNone(score('chatgpt', {'7d': dict(WEEKLY, reset_at=big)}))  # oversized time
        self.assertIsNone(score('chatgpt', {'7d': WEEKLY}, now=big))  # oversized clock
        # An oversized scoped duration is invalid alone: it neither vetoes nor divides by zero.
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY, '5h': SHORT,
                                                SCOPE: dict(meter(50, 240, None), duration=big)}), 3.75)
        self.assertAlmostEqual(score('claude', {'7d': WEEKLY, '5h': SHORT,
                                                SCOPE: dict(meter(big, 144, 168))}), 3.75)


def deep(depth):
    """A value nested `depth` lists deep: JSON-readable, but beyond what deepcopy can follow."""
    value = 0
    for _ in range(depth):
        value = [value]
    return value


def batch(earliest, latest, *, used=30, meters=True):
    found = {'7d': dict(used_percent=used, earliest=earliest, latest=latest, reset_at=NOW + 72 * HOUR,
                        duration=168 * HOUR, discrete=True)} if meters else {}
    return dict(earliest=earliest, latest=latest, meters=found)


class BatchTest(unittest.TestCase):
    """One provider's ranking batch: a newer reply replaces it, a provably older one changes nothing, and an
    overlapping one keeps only the combined interval. Stored meters are validated again on every read."""

    def test_first_reply_publishes_its_meters(self):
        published = usage_balance.batch_after(None, earliest=NOW - 60, latest=NOW,
                                              meters=batch(NOW - 60, NOW)['meters'])
        self.assertEqual(published, batch(NOW - 60, NOW))

    def test_strictly_newer_reply_replaces_the_batch_even_without_meters(self):
        previous = batch(NOW - 60, NOW)
        for meters in (batch(NOW + 1, NOW + 2)['meters'], {}):
            with self.subTest(meters=bool(meters)):
                replaced = usage_balance.batch_after(previous, earliest=NOW + 1, latest=NOW + 2, meters=meters)
                self.assertEqual(replaced, dict(earliest=NOW + 1, latest=NOW + 2, meters=meters))

    def test_provably_older_reply_changes_nothing(self):
        previous = batch(NOW - 60, NOW)
        older = usage_balance.batch_after(previous, earliest=NOW - 400, latest=NOW - 61, meters={})
        self.assertEqual(older, previous)

    def test_overlapping_reply_discards_meters_and_keeps_the_combined_interval(self):
        previous = batch(NOW - 60, NOW)
        for earliest, latest in ((NOW, NOW + 30), (NOW - 30, NOW + 30), (NOW - 90, NOW - 60)):
            with self.subTest(earliest=earliest, latest=latest):
                merged = usage_balance.batch_after(previous, earliest=earliest, latest=latest,
                                                   meters=batch(earliest, latest)['meters'])
                self.assertEqual(merged, dict(earliest=min(NOW - 60, earliest), latest=max(NOW, latest), meters={}))

    def test_equal_boundary_is_overlap_not_newer(self):
        previous = batch(NOW - 60, NOW)
        merged = usage_balance.batch_after(previous, earliest=NOW, latest=NOW + 1, meters={})
        self.assertEqual(merged['meters'], {})
        self.assertEqual(merged['latest'], NOW + 1)

    def test_corrupt_stored_batch_is_treated_as_absent(self):
        for previous in ('bad', dict(earliest='x', latest=NOW, meters={}), dict(earliest=NOW, latest=NOW - 1,
                                                                                 meters={}),
                         dict(earliest=NOW - 60, latest=NOW, meters={}, extra=True)):
            with self.subTest(previous=previous):
                replaced = usage_balance.batch_after(previous, earliest=NOW + 5, latest=NOW + 6, meters={})
                self.assertEqual(replaced, dict(earliest=NOW + 5, latest=NOW + 6, meters={}))

    def test_older_reply_retains_a_deep_stored_batch_without_traversing_it(self):
        # Deeper than deepcopy can follow, yet JSON-readable: a corrupt meter must not fail the publication.
        stored = dict(earliest=NOW + 100, latest=NOW + 200, meters={'5h': {'used_percent': deep(700)}})
        self.assertIs(usage_balance.batch_after(stored, earliest=NOW - 60, latest=NOW, meters={}), stored)

    def test_meter_timestamps_must_match_the_batch_interval(self):
        fresh = dict(meter(40, 72, 168, at=NOW), earliest=NOW - 60)  # fresh-dated, spanning the usual reply interval
        stale = dict(earliest=NOW - 1000, latest=NOW - 900, meters={'7d': fresh})
        self.assertEqual(usage_balance.batch_meters(stale, now=NOW, keep=lambda key: True), {})
        matching = dict(earliest=NOW - 60, latest=NOW, meters={'7d': fresh, '5h': dict(fresh, earliest=NOW - 61)})
        self.assertEqual(sorted(usage_balance.batch_meters(matching, now=NOW, keep=lambda key: True)), ['7d'])
        self.assertEqual(usage_balance.batch_meters(matching, now=NOW, keep=lambda key: key == '5h'), {})

    def test_batch_inputs_are_not_mutated(self):
        previous = batch(NOW - 60, NOW)
        meters = batch(NOW - 30, NOW + 30)['meters']
        before = copy.deepcopy((previous, meters))
        usage_balance.batch_after(previous, earliest=NOW - 30, latest=NOW + 30, meters=meters)
        self.assertEqual((previous, meters), before)

    def test_stored_meters_are_rechecked_alone_on_read(self):
        stored = dict(earliest=NOW - 60, latest=NOW, meters={
            '7d': dict(used_percent=40, earliest=NOW - 60, latest=NOW, reset_at=NOW + 3600, duration=604800,
                       discrete=True),
            '5h': dict(used_percent='bad', earliest=NOW - 60, latest=NOW, reset_at=NOW + 3600, duration=18000,
                       discrete=True),
            'family:opus': dict(used_percent=10, earliest=NOW - 60, latest=NOW, reset_at=NOW + 3600,
                                duration=604800, discrete=True, raw='response'),
            'monthly': dict(used_percent=10, earliest=NOW - 60, latest=NOW, reset_at=NOW + 3600, duration=None,
                            discrete=True),
        })
        kept = usage_balance.batch_meters(stored, now=NOW, keep=lambda key: key in ('7d', 'family:opus', '5h'))
        self.assertEqual(sorted(kept), ['7d'])
        self.assertEqual(usage_balance.batch_meters(stored, now=NOW + 900, keep=lambda key: True), {})
        self.assertEqual(usage_balance.batch_meters('bad', now=NOW, keep=lambda key: True), {})
        self.assertEqual(usage_balance.batch_meters(dict(stored, extra=1), now=NOW, keep=lambda key: True), {})
        kept['7d']['used_percent'] = 99
        self.assertEqual(stored['meters']['7d']['used_percent'], 40)


class SupportTest(unittest.TestCase):
    """`expires_at` counts exactly the readings the scorer uses: a reading the scorer ignores cannot end a score."""
    def test_the_end_of_support_is_the_first_reset_or_age_out_of_a_scored_reading(self):
        weekly, short = meter(90, 2, 168), meter(40, 1, 5)
        self.assertEqual(usage_balance.expires_at({'7d': weekly}, now=NOW), NOW + 900)  # ages out before it resets
        self.assertEqual(usage_balance.expires_at({'7d': weekly, '5h': meter(40, 0.1, 5)}, now=NOW), NOW + 360)
        aged = meter(90, 2, 168, age_offset=600)
        self.assertEqual(usage_balance.expires_at({'7d': aged, '5h': short}, now=NOW), NOW + 300)
        scoped = meter(50, 0.05, 168)  # a protecting meter is scored for headroom, so its reset counts
        self.assertEqual(usage_balance.expires_at({'7d': weekly, SCOPE: scoped}, now=NOW), NOW + 180)

    def test_a_reading_the_scorer_ignores_never_sets_the_end(self):
        weekly = meter(90, 2, 168)
        bogus = dict(used_percent=10, earliest=NOW, latest=NOW, reset_at=NOW + 2, duration=60, discrete=True)
        self.assertTrue(usage_balance.fresh_meter(bogus, now=NOW))  # well formed and fresh, but no 5-hour window
        for label, extra in (('wrong length', {'5h': bogus}), ('unknown key', {'bogus': bogus}),
                             ('malformed', {'5h': dict(bogus, used_percent='x')}),
                             ('expired', {'5h': dict(meter(40, 1, 5), reset_at=NOW)}),
                             ('stale', {'5h': meter(40, 1, 5, age_offset=900)})):
            with self.subTest(label=label):
                meters = dict({'7d': weekly}, **extra)
                self.assertAlmostEqual(score('chatgpt', meters), 8.4)
                self.assertEqual(usage_balance.expires_at(meters, now=NOW), NOW + 900)
        self.assertEqual(usage_balance.expires_at({'duration:60': bogus, '7d': weekly}, now=NOW), NOW + 2)  # scored

    def test_no_scored_reading_has_no_end(self):
        for meters in ({}, None, [], {'5h': dict(meter(40, 1, 5), duration=60)}, {'bogus': WEEKLY}):
            with self.subTest(meters=meters):
                self.assertIsNone(usage_balance.expires_at(meters, now=NOW))
                self.assertIsNone(score('chatgpt', meters))


if __name__ == '__main__':
    unittest.main()
