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

ROUTES = {
    'a': dict(family='claude', price=1.0, score=1.0, expiry=1.0),
    'b': dict(family='chatgpt', price=1.0, score=1.0, expiry=1.4),
    'c': dict(family='go', price=1.0, score=1.0, expiry=2.0),
    'c-alias': dict(family='go', price=1.0, score=1.0, expiry=2.0),
    'small_gain': dict(family='chatgpt', price=1.0, score=1.0, expiry=1.2),
    'outside_bounds': dict(family='chatgpt', price=1.501, score=1.0, expiry=3.0),
    'unknown': dict(family='chatgpt', price=1.0, score=1.0, expiry=None),
}


def reorder_names(names, table=ROUTES):
    ordered = [dict(route=name, price=table[name]['price'], score=table[name]['score']) for name in names]
    families = {name: table[name]['family'] for name in names}
    expiry = {name: table[name]['expiry'] for name in names}
    return [item['route'] for item in usage_balance.reorder(ordered, families=families, expiry=expiry)]


def rank_two(*, b_expiry, price_ratio=1.0, score_ratio=1.0, b_family='chatgpt'):
    ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=price_ratio, score=score_ratio)]
    families = dict(a='claude', b=b_family)
    expiry = dict(a=1.0, b=b_expiry)
    return [item['route'] for item in usage_balance.reorder(ordered, families=families, expiry=expiry)]


def rank_two_same_subscription(*, b_expiry):
    return rank_two(b_expiry=b_expiry, b_family='claude')


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
        families = dict(a='claude', c='chatgpt', d='chatgpt')  # b has no family mapping
        expiry = dict(a=1.0, b=3.0, d=2.0)  # c has no expiry mapping
        result = usage_balance.reorder(ordered, families=families, expiry=expiry)
        self.assertEqual([item['route'] for item in result], ['d', 'b', 'c', 'a'])

    def test_reorder_without_baseline_usable_score_keeps_order(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
        result = usage_balance.reorder(ordered, families=dict(a='claude', b='chatgpt'), expiry=dict(b=2.0))
        self.assertEqual([item['route'] for item in result], ['a', 'b'])

    def test_reorder_does_not_mutate_inputs(self):
        ordered = [dict(route='a', price=1.0, score=1.0), dict(route='b', price=1.0, score=1.0)]
        families = dict(a='claude', b='chatgpt')
        expiry = dict(a=1.0, b=2.0)
        before = copy.deepcopy((ordered, families, expiry))
        result = usage_balance.reorder(ordered, families=families, expiry=expiry)
        self.assertEqual([item['route'] for item in result], ['b', 'a'])
        self.assertIsNot(result, ordered)
        self.assertEqual((ordered, families, expiry), before)


if __name__ == '__main__':
    unittest.main()
