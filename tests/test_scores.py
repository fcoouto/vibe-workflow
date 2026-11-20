import json
import unittest

from runtime import scores


def model(id_, score=None, prompt='0.000002', completion='0.00001', created=1000, **extra):
    entry = dict(id=id_, created=created, pricing=dict(prompt=prompt, completion=completion),
                 benchmarks=dict(artificial_analysis=dict(intelligence_index=score)))
    entry.update(extra)
    return entry


def parse(*models, previous=0):
    return scores.parse_openrouter(json.dumps(dict(data=list(models))).encode(), previous)


class ScoresTest(unittest.TestCase):
    def test_normalize_matches_both_spellings(self):
        self.assertEqual(scores.normalize('anthropic/claude-sonnet-5.5'), 'claude-sonnet-5-5')
        self.assertEqual(scores.normalize('claude-sonnet-5-5'), 'claude-sonnet-5-5')
        self.assertEqual(scores.normalize('openai/gpt-6.2-sol'), 'gpt-6-2-sol')

    def test_family_key(self):
        self.assertEqual(scores.family_key('gpt-6-3-sol'), 'gpt-sol')
        self.assertEqual(scores.family_key('claude-haiku-4-5-20251001'), 'claude-haiku')

    def test_parse_keeps_needed_fields_and_converts_prices(self):
        models = parse(model('openai/gpt-6.2-sol', 57.3, created=5), model('openai/other'))['models']
        self.assertEqual(models['openai/gpt-6-2-sol'], dict(
            key='gpt-6-2-sol', creator='openai', family='gpt-sol', score=57.3,
            input=2.0, output=10.0, released=5))
        self.assertIsNone(models['openai/other']['score'])

    def test_variant_ids_never_share_the_base_key(self):
        cache = parse(model('openai/gpt-6.2-sol:free', 40), model('openai/gpt-6.2-sol'))
        self.assertIn('openai/gpt-6-2-sol-free', cache['models'])
        self.assertIsNone(scores.lookup(cache, 'gpt-6-2-sol', 'openai'))
        self.assertEqual(scores.lookup(cache, 'gpt-6-2-sol-free', 'openai')['score'], 40)
        base_only = parse(model('openai/gpt-6.2-sol:free', 40))
        self.assertIsNone(scores.lookup(base_only, 'gpt-6-2-sol', 'openai'))

    def test_duplicate_key_resolves_by_creator_or_is_unscored(self):
        cache = parse(model('openai/gpt-6.2-sol', 57), model('someone/gpt-6.2-sol', 20))
        self.assertEqual(scores.lookup(cache, 'gpt-6-2-sol', 'openai')['score'], 57)
        self.assertIsNone(scores.lookup(cache, 'gpt-6-2-sol', None))

    def test_unique_key_without_creator_resolves(self):
        cache = parse(model('openai/gpt-6.2-sol', 57, created=7))
        self.assertEqual(scores.lookup(cache, 'gpt-6-2-sol', None), dict(
            score=57, price=scores_price(2, 10), score_source='openrouter', released=7))

    def test_unscored_model_borrows_newest_family_score(self):
        cache = parse(model('openai/gpt-6.2-sol', 57, created=20), model('openai/gpt-6.1-sol', 50, created=10),
                      model('openai/gpt-6.3-sol', None, prompt=None, completion=None, created=30))
        found = scores.lookup(cache, 'gpt-6-3-sol', 'openai')
        self.assertEqual(found['score_source'], 'family')
        self.assertEqual(found['score'], 57)
        self.assertEqual(found['price'], scores_price(2, 10))

    def test_family_borrow_keeps_own_price_and_honors_creator(self):
        cache = parse(model('openai/gpt-6.2-sol', 57, created=20),
                      model('openai/gpt-6.3-sol', None, prompt='0.000004', completion='0.00002', created=30),
                      model('someone/gpt-9-sol', 90, created=40))
        found = scores.lookup(cache, 'gpt-6-3-sol', 'openai')
        self.assertEqual((found['score'], found['price']), (57, scores_price(4, 20)))
        self.assertEqual(scores.lookup(cache, 'gpt-6-3-sol', None)['score'], 90)
        self.assertIsNone(scores.lookup(cache, 'unrelated', 'openai'))

    def test_parse_rejects_bad_payloads(self):
        bad = [b'not json', json.dumps(dict(data={})).encode(), json.dumps([]).encode(),
               json.dumps(dict(data=[model('x/' + 'a' * 300)])).encode()]
        for raw in bad:
            with self.assertRaises(ValueError) as caught:
                scores.parse_openrouter(raw)
            self.assertEqual(str(caught.exception), 'scores_invalid')
        for broken in (model('a/b', prompt='NaN'), model('a/b', prompt='-1'), model('a/b', score=101),
                       model('a/b', score=float('nan')), model('a/b', score=-1), model('a/b', score='5')):
            with self.assertRaises(ValueError):
                parse(model('a/ok', 1), broken)
        many = [model(f'a/m{i}', 1) for i in range(5001)]
        with self.assertRaises(ValueError):
            parse(*many)
        with self.assertRaises(ValueError):
            parse(*[model(f'a/m{i}', 1 if i < 40 else None) for i in range(100)], previous=100)

    def test_malformed_entries_are_skipped(self):
        cache = parse(model('a/ok', 1), 'junk', dict(id=5), dict(id='bare'), dict(id='/'),
                      dict(id='a/nocreated', pricing='x'))
        self.assertEqual(list(cache['models']), ['a/ok'])


def scores_price(inp, out):
    return (3 * inp + out) / 4


if __name__ == '__main__':
    unittest.main()
