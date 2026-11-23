import unittest

from runtime import routing

FACTS = dict(files='1-2', spec='exact', risk='reversible')

# name: (score, input per million, output per million)
PLANS = {
    'fable-5.2': (64.6, 8, 40), 'astra': (62.1, 8, 40), 'opus': (59.1, 4, 20),
    'sol': (57.3, 2, 10), 'sonnet': (54.5, 2, 10), 'terra': (51.3, 1, 5),
    'fable-5.1': (50.0, 10, 50), 'haiku': (36.5, 0.10, 0.50), 'luna': (27.9, 0.10, 0.50),
}
CLAUDE = ('fable-5.2', 'opus', 'sonnet', 'fable-5.1', 'haiku')


def candidates(names):
    return [dict(route=name, score=PLANS[name][0], price=routing.price(*PLANS[name][1:]), released=0)
            for name in names]


def pick(names, level):
    return routing.order(candidates(names), level)[0]['route']


class RoutingTest(unittest.TestCase):
    def test_level_is_max_of_base_and_facts(self):
        self.assertEqual(routing.task_level('implementation', FACTS), 1)
        self.assertEqual(routing.task_level('review', FACTS), 2)
        self.assertEqual(routing.task_level('planning', FACTS), 3)
        self.assertEqual(routing.task_level('implementation', dict(FACTS, files='3+')), 2)
        self.assertEqual(routing.task_level('implementation', dict(FACTS, risk='critical')), 3)

    def test_missing_or_unknown_facts_are_rejected(self):
        bad = [{k: v for k, v in FACTS.items() if k != 'risk'}, dict(FACTS, files='many'),
               dict(FACTS, extra=1), dict(FACTS, context_tokens=True), dict(FACTS, context_tokens=-1)]
        for task in bad:
            with self.assertRaises(ValueError):
                routing.task_level('implementation', task)
        with self.assertRaises(ValueError):
            routing.task_level('cooking', FACTS)
        self.assertEqual(routing.task_level('implementation', dict(FACTS, context_tokens=5)), 1)

    def test_golden_two_plans(self):
        names = list(PLANS)
        self.assertEqual(pick(names, 1), 'haiku')
        self.assertEqual(pick(names, 2), 'sol')
        self.assertEqual(pick(names, 3), 'sol')

    def test_golden_claude_only(self):
        self.assertEqual([pick(CLAUDE, level) for level in (1, 2, 3)], ['haiku', 'sonnet', 'opus'])

    def test_near_clause_needs_both_conditions(self):
        floor = 100 * routing.RATIO[0]
        base = dict(route='a', score=100, price=10, released=0)
        cheap_low = dict(route='b', score=0.96 * floor, price=2.5, released=0)
        near_dear = dict(route='c', score=0.98 * floor, price=6, released=0)
        routes = [c['route'] for c in routing.order([base, cheap_low, near_dear], 1)]
        self.assertEqual(routes, ['a'])
        near_cheap = dict(route='d', score=0.98 * floor, price=4, released=0)
        routes = [c['route'] for c in routing.order([base, near_cheap], 1)]
        self.assertEqual(routes, ['d', 'a'])

    def test_ties_break_by_score_then_release_then_route(self):
        a = dict(route='a', score=50, price=1, released=1)
        b = dict(route='b', score=60, price=1, released=0)
        self.assertEqual([c['route'] for c in routing.order([a, b], 1)], ['b', 'a'])
        c = dict(route='c', score=60, price=1, released=5)
        self.assertEqual(routing.order([b, c], 1)[0]['route'], 'c')

    def test_own_score_beats_a_borrowed_one_at_equal_price_and_score(self):
        borrowed = dict(route='a', score=60, price=1, released=9, score_source='family')
        own = dict(route='b', score=60, price=1, released=0, score_source='openrouter')
        self.assertEqual([c['route'] for c in routing.order([borrowed, own], 1)], ['b', 'a'])

    def test_effort_clamps_to_variants(self):
        self.assertEqual(routing.effort_for(2, ['low', 'medium', 'high']), 'medium')
        self.assertEqual(routing.effort_for(3, ['low', 'medium']), 'medium')
        self.assertEqual(routing.effort_for(1, ['high', 'max']), 'high')
        self.assertIsNone(routing.effort_for(2, []))

    def test_constraints_are_bounded_lists(self):
        self.assertEqual(routing.check_constraints(dict(allowed_models=['m'])), dict(allowed_models=['m']))
        for bad in (dict(other=['x']), dict(allowed_strategy_ids=['x']), dict(allowed_models='m'),
                    dict(allowed_models=['m'] * 257), 'x'):
            with self.assertRaises(ValueError):
                routing.check_constraints(bad)



def c(route, key, creator, score, price=1, source='openrouter', released=0):
    return dict(route=route, key=key, creator=creator, score=score, price=price, score_source=source,
                released=released)


def routes(items):
    return [item['route'] for item in items]


class SeatsTest(unittest.TestCase):
    def test_one_per_lab_then_more_frontier(self):
        found = routing.seats([c('fable', 'claude-fable-5-2', 'anthropic', 64.6), c('opus', 'claude-opus-5-5', 'anthropic', 59.1),
                               c('astra', 'gpt-6-astra', 'openai', 62.1), c('sol', 'gpt-6-2-sol', 'openai', 57.3),
                               c('sonnet', 'claude-sonnet-5-5', 'anthropic', 54.5)])
        self.assertEqual(routes(found), ['fable', 'astra', 'opus', 'sol', 'sonnet'])

    def test_below_frontier_only_after_frontier(self):
        found = routing.seats([c('a1', 'a-1', 'anthropic', 90), c('a2', 'a-2', 'anthropic', 85),
                               c('o1', 'o-1', 'openai', 70)])
        self.assertEqual(routes(found), ['a1', 'a2', 'o1'])

    def test_unknown_creator_is_not_a_new_lab(self):
        found = routing.seats([c('a1', 'a-1', 'anthropic', 90), c('a2', 'a-2', 'anthropic', 89),
                               c('x', 'x-1', None, 89.5)])
        self.assertEqual(routes(found), ['a1', 'x', 'a2'])
        found = routing.seats([c('x', 'x-1', None, 95), c('a1', 'a-1', 'anthropic', 90),
                               c('o1', 'o-1', 'openai', 89)])
        self.assertEqual(routes(found), ['a1', 'o1', 'x'])

    def test_one_model_one_seat(self):
        cases = [
            ([c('claude', 'claude-haiku-5-5', 'anthropic', 50, price=2), c('go', 'claude-haiku-5-5', 'anthropic', 50)],
             ['go']),
            ([c('dated', 'claude-haiku-5-5-20261001', 'anthropic', 50), c('base', 'claude-haiku-5-5', 'anthropic', 50)],
             ['base']),
            ([c('fast', 'gpt-6-astra-fast', 'openai', 62, price=4), c('astra', 'gpt-6-astra', 'openai', 62)],
             ['astra']),
            ([c('six', 'gpt-6', 'openai', 60), c('six-two', 'gpt-6-2', 'openai', 61)], ['six-two', 'six']),
            ([c('s3', 'gpt-6-3-sol', 'openai', 57, source='family'), c('s2', 'gpt-6-2-sol', 'openai', 57)],
             ['s2', 's3']),
            ([c('ours', 'gpt-6-astra-fast', 'openai', 62), c('theirs', 'gpt-6-astra', 'someone', 62, price=2)],
             ['ours', 'theirs']),
        ]
        for given, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(routes(routing.seats(given)), expected)

    def test_a_sibling_with_its_own_score_keeps_its_own_seat(self):
        cases = [
            ([c('base', 'gpt-6', 'openai', 70, price=4), c('mini', 'gpt-6-mini', 'openai', 45),
              c('opus', 'claude-opus-5-5', 'anthropic', 68)], ['base', 'opus', 'mini']),
            ([c('six', 'gpt-6', 'openai', 60), c('pro', 'gpt-6-pro', 'openai', 72)], ['pro', 'six']),
            ([c('flash', 'gemini-3-flash', 'google', 50), c('lite', 'gemini-3-flash-lite', 'google', 35)],
             ['flash', 'lite']),
            ([c('free', 'gpt-6-astra-free', 'openai', 62, price=0), c('astra', 'gpt-6-astra', 'openai', 62)],
             ['free']),
        ]
        for given, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(routes(routing.seats(given)), expected)

    def test_one_identity_keeps_its_strongest_copy(self):
        found = routing.seats([c('cheap', 'claude-haiku-5-5', 'anthropic', 40, price=1),
                               c('strong', 'claude-haiku-5-5', 'anthropic', 50, price=2)])
        self.assertEqual(routes(found), ['strong'])

    def test_ties_use_the_shared_order_key(self):
        found = routing.seats([c('d', 'm-d', 'anthropic', 70, released=1), c('c', 'm-c', 'anthropic', 70, released=2),
                               c('b', 'm-b', 'anthropic', 70, source='family'), c('a', 'm-a', 'anthropic', 70, price=0.5),
                               c('e', 'm-e', 'anthropic', 70, released=1)])
        self.assertEqual(routes(found), ['a', 'c', 'd', 'e', 'b'])

    def test_empty(self):
        self.assertEqual(routing.seats([]), [])


if __name__ == '__main__':
    unittest.main()
