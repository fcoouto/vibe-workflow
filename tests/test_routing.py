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


if __name__ == '__main__':
    unittest.main()
