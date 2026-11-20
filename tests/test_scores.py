import email
import io
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock
import urllib.request
import urllib.response

from runtime import scores, storage


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
                      model('someone/gpt-6.2.1-sol', 90, created=40))
        found = scores.lookup(cache, 'gpt-6-3-sol', 'openai')
        self.assertEqual((found['score'], found['price']), (57, scores_price(4, 20)))
        self.assertEqual(scores.lookup(cache, 'gpt-6-3-sol', None)['score'], 90)
        self.assertIsNone(scores.lookup(cache, 'unrelated', 'openai'))

    def test_family_borrow_never_uses_a_newer_version(self):
        # Live: old Haiku 4.5 borrowed Haiku 5.5's score and tied the real Haiku.
        cache = parse(model('anthropic/claude-haiku-5.5', 36.5, created=50),
                      model('anthropic/claude-haiku-4-5-20251001', None, prompt=None, completion=None, created=10))
        self.assertIsNone(scores.lookup(cache, 'claude-haiku-4-5-20251001', 'anthropic'))
        self.assertEqual(scores.lookup(cache, 'claude-haiku-5-5', 'anthropic')['score_source'], 'openrouter')
        cache = parse(model('openai/gpt-6.2-sol', 57, created=20))
        self.assertEqual(scores.lookup(cache, 'gpt-6-3-sol', 'openai')['score'], 57)
        self.assertEqual(scores.lookup(cache, 'gpt-6-2-sol-20261117', 'openai')['score'], 57)
        cache = parse(model('openai/gpt-6.1-sol', 50, created=10), model('openai/gpt-6.2-sol', 57, created=20),
                      model('openai/gpt-6.4-sol', 70, created=40))
        found = scores.lookup(cache, 'gpt-6-3-sol', 'openai')
        self.assertEqual((found['score'], found['score_source'], found['released']), (57, 'family', 20))
        self.assertIsNone(scores.lookup(cache, 'gpt-6-0-sol', 'openai'))

    def test_date_segments_are_not_versions(self):
        cache = parse(model('anthropic/claude-sonnet-4', 50, created=10),
                      model('anthropic/claude-sonnet-4.6', 70, created=30),
                      model('anthropic/claude-opus-4.7', 80, created=40), model('openai/gpt-5.1-codex', 60, created=50))
        self.assertEqual(scores.lookup(cache, 'claude-sonnet-4-20250514', 'anthropic')['score'], 50)
        self.assertIsNone(scores.lookup(cache, 'claude-opus-4-20250514', 'anthropic'))
        self.assertIsNone(scores.lookup(cache, 'gpt-5-codex-20250101', 'openai'))
        self.assertEqual(scores.version('claude-haiku-4-5-20251001'), (4, 5))
        self.assertEqual(scores.version('gpt-²-sol'), ())

    def test_payload_level_problems_raise(self):
        many = [model(f'a/m{i}', 1) for i in range(5001)]
        few = [model(f'a/m{i}', 1 if i < 40 else None) for i in range(100)]
        for raw, previous in ((b'not json', 0), (json.dumps(dict(data={})).encode(), 0),
                              (json.dumps([]).encode(), 0), (json.dumps(dict(data=many)).encode(), 0),
                              (json.dumps(dict(data=few)).encode(), 100)):
            with self.assertRaisesRegex(ValueError, '^scores_invalid$'):
                scores.parse_openrouter(raw, previous)

    def test_bad_entries_are_skipped_and_valid_ones_survive(self):
        router = dict(id='openrouter/auto', pricing=dict(prompt='-1', completion='-1'))
        bad = [router, model('a/nan', prompt='NaN'), model('a/neg', prompt='-1'), model('a/inf', completion='inf'),
               model('a/high', score=101), model('a/low', score=-1), model('a/text', score='5'),
               model('a/nanscore', score=float('nan')), model('x/' + 'a' * 300), model('a/\x00b')]
        cache = parse(model('a/ok', 1), *bad)
        self.assertEqual(list(cache['models']), ['a/ok'])

    def test_same_creator_collision_is_dropped_and_lends_nothing(self):
        cache = parse(model('openai/gpt-5.1', 50), model('openai/gpt-5-1', 60), model('openai/gpt-5.0', 40))
        self.assertNotIn('openai/gpt-5-1', cache['models'])
        self.assertEqual(scores.lookup(cache, 'gpt-5-1', 'openai')['score_source'], 'family')
        self.assertEqual(scores.lookup(cache, 'gpt-5-1', 'openai')['score'], 40)

    def test_malformed_entries_are_skipped(self):
        cache = parse(model('a/ok', 1), 'junk', dict(id=5), dict(id='bare'), dict(id='/'),
                      dict(id='a/nocreated', pricing='x'))
        self.assertEqual(list(cache['models']), ['a/ok'])


def scores_price(inp, out):
    return (3 * inp + out) / 4


def payload(*models):
    return json.dumps(dict(data=list(models))).encode()


def failing(timeout):
    raise OSError('down')


class RefreshTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name).resolve() / 'state'
        self.calls = 0

    def fetcher(self, *models):
        def fetch(timeout):
            self.calls += 1
            return payload(*models)
        return fetch

    def refresh(self, wanted, now, fetch):
        return scores.refresh(self.dir, wanted, now, fetch=fetch)

    def test_due_rules(self):
        self.assertTrue(scores.due(None, [], 5))
        self.assertEqual(self.refresh([], 1000, self.fetcher(model('a/known', 1))), 'stored')
        cache = scores.load(self.dir)
        self.assertFalse(scores.due(cache, ['known'], 1000))
        self.assertFalse(scores.due(cache, ['known'], 1000 + scores.GAP))
        self.assertFalse(scores.due(cache, ['known'], 1000 + scores.CEILING))
        self.assertFalse(scores.due(cache, ['new'], 1000 + scores.GAP - 1))
        self.assertTrue(scores.due(cache, ['new'], 1000 + scores.GAP))
        self.assertFalse(scores.due(cache, [], 1000 + scores.CEILING))
        self.assertTrue(scores.due(cache, [], 1001 + scores.CEILING))
        failed = dict(cache, attempt_at=4900, failures=1)
        self.assertFalse(scores.due(failed, ['new'], 5000))
        future = dict(cache, fetched_at=9999, attempt_at=0)
        self.assertTrue(scores.due(future, [], 5000))

    def test_huge_failure_count_waits_the_capped_backoff(self):
        cache = dict(attempt_at=0, fetched_at=-2 * scores.CEILING, failures=10**18, unscored={}, models={})
        self.assertFalse(scores.due(cache, [], scores.BACKOFF[1] - 1))
        self.assertTrue(scores.due(cache, [], scores.BACKOFF[1]))

    def test_unscored_retry_schedule_then_weekly(self):
        fetch = self.fetcher(model('a/other', 1))
        now = 0
        self.assertEqual(self.refresh(['k'], now, fetch), 'stored')
        for wait in scores.RETRY:
            cache = scores.load(self.dir)
            self.assertFalse(scores.due(cache, ['k'], now + wait - 1))
            self.assertTrue(scores.due(cache, ['k'], now + wait))
            now += wait
            self.assertEqual(self.refresh(['k'], now, fetch), 'stored')
        cache = scores.load(self.dir)
        self.assertFalse(scores.due(cache, ['k'], now + scores.CEILING - 1))
        self.assertTrue(scores.due(cache, ['k'], now + scores.CEILING + 1))

    def test_removed_models_are_pruned(self):
        fetch = self.fetcher(model('a/other', 1))
        self.refresh(['gone', 'kept'], 0, fetch)
        self.assertEqual(sorted(scores.load(self.dir)['unscored']), ['gone', 'kept'])
        self.assertEqual(self.refresh([], scores.CEILING + 1, fetch), 'stored')
        self.assertEqual(scores.load(self.dir)['unscored'], {})

    def test_failure_keeps_last_good_copy_and_backs_off(self):
        self.refresh([], 0, self.fetcher(model('a/known', 1)))
        before = scores.load(self.dir)['models']
        now = 1000
        for failures in (1, 2):
            self.assertEqual(self.refresh(['new'], now, failing), 'failed')
            cache = scores.load(self.dir)
            self.assertEqual(cache['models'], before)
            self.assertEqual(cache['failures'], failures)
            self.assertFalse(scores.due(cache, ['new'], now + 299))
            now += scores.GAP
        self.assertEqual(self.refresh(['new'], now, lambda timeout: b'not json'), 'failed')
        self.assertEqual(scores.load(self.dir)['models'], before)
        capped = dict(scores.load(self.dir), attempt_at=0, failures=9)
        self.assertFalse(scores.due(capped, ['new'], scores.BACKOFF[1] - 1))
        self.assertTrue(scores.due(capped, ['new'], scores.BACKOFF[1]))

    def test_backoff_doubles_between_the_floor_and_the_cap(self):
        self.refresh([], 0, self.fetcher(model('a/known', 1)))
        cache = scores.load(self.dir)
        for failures, wait in ((3, 1200), (4, 2400)):
            failed = dict(cache, attempt_at=10000, failures=failures)
            self.assertFalse(scores.due(failed, ['new'], 10000 + wait - 1))
            self.assertTrue(scores.due(failed, ['new'], 10000 + wait))

    def test_nested_payload_fails_without_raising(self):
        self.refresh([], 0, self.fetcher(model('a/known', 1)))
        before = scores.load(self.dir)['models']
        nested = lambda timeout: b'{"data":' + b'[' * 200000
        self.assertEqual(self.refresh(['new'], 1000, nested), 'failed')
        cache = scores.load(self.dir)
        self.assertEqual((cache['models'], cache['failures']), (before, 1))

    def test_oversized_write_fails_and_keeps_last_good_copy(self):
        self.refresh([], 0, self.fetcher(model('a/known', 1)))
        before = scores.load(self.dir)['models']
        big = [model(f'a/m{index}', 1) for index in range(200)]
        with mock.patch.object(storage, 'MAX_FILE', 1000):
            self.assertEqual(self.refresh(['new'], 1000, self.fetcher(*big)), 'failed')
        cache = scores.load(self.dir)
        self.assertEqual((cache['models'], cache['failures']), (before, 1))

    def test_failure_prunes_removed_models(self):
        self.refresh(['gone'], 0, self.fetcher(model('a/other', 1)))
        self.assertEqual(self.refresh(['new'], 1000, failing), 'failed')
        self.assertEqual(list(scores.load(self.dir)['unscored']), [])

    def test_observation_lock_does_not_block_refresh(self):
        with storage._locked(self.dir / 'window-observations.json'):
            self.assertEqual(self.refresh([], 0, self.fetcher(model('a/known', 1))), 'stored')

    def test_first_failure_is_remembered(self):
        self.assertEqual(self.refresh([], 50, failing), 'failed')
        cache = scores.load(self.dir)
        self.assertEqual((cache['models'], cache['failures']), ({}, 1))
        self.assertFalse(scores.due(cache, [], 60))

    def test_single_fetcher(self):
        self.refresh([], 0, self.fetcher(model('a/known', 1)))
        calls = self.calls
        with storage._locked(self.dir / scores.CACHE, lock=scores.LOCK):
            self.assertEqual(self.refresh(['new'], 5000, self.fetcher()), 'busy')
        self.assertEqual(self.calls, calls)

    def test_late_caller_is_skipped(self):
        self.refresh([], 0, self.fetcher(model('a/known', 1)))
        self.assertEqual(self.refresh(['known'], 10, self.fetcher()), 'skipped')
        self.assertEqual(self.calls, 1)

    def test_cache_file_is_private(self):
        self.refresh([], 0, self.fetcher())
        self.assertEqual(stat.S_IMODE((self.dir / scores.CACHE).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)

    def test_small_response_is_rejected(self):
        self.refresh([], 0, self.fetcher(model('a/x', 1), model('a/y', 2), model('a/z', 3)))
        before = scores.load(self.dir)['models']
        self.assertEqual(self.refresh(['new'], 2000, self.fetcher(model('a/x', 1))), 'failed')
        self.assertEqual(scores.load(self.dir)['models'], before)


class FetchTest(unittest.TestCase):
    def test_fetch_refuses_cross_host_redirect(self):
        seen = []
        test = self

        class Fake(urllib.request.HTTPSHandler):
            def https_open(self, request):
                seen.append(request.full_url)
                headers = email.message_from_string('Location: https://evil.example/x\n')
                response = urllib.response.addinfourl(io.BytesIO(b''), headers, request.full_url, 302)
                response.msg = 'Found'
                test.addCleanup(response.close)
                return response

        opener = urllib.request.build_opener(scores._SameHost, Fake)
        with self.assertRaises(OSError):
            scores.fetch_openrouter(1, environ={}, opener=opener)
        self.assertEqual(seen, [scores.URL])

    def test_fetch_is_disabled_under_test_home(self):
        class Boom:
            def open(self, *args, **kwargs):
                raise AssertionError('opened')

        with self.assertRaises(OSError):
            scores.fetch_openrouter(1, environ={'VCW_TEST_HOME': '/x'}, opener=Boom())

    def test_fetch_enforces_a_total_deadline(self):
        clock = [0.0]

        class Trickle:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                clock[0] += 2
                return b'x'

        class Opener:
            def open(self, request, timeout):
                return Trickle()

        with mock.patch.object(scores.time, 'monotonic', lambda: clock[0]):
            with self.assertRaises(OSError):
                scores.fetch_openrouter(5, environ={}, opener=Opener())

    def test_fetch_deadline_holds_against_a_slow_drip(self):
        class Drip:
            sent = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read1(self, size=-1):
                if self.sent >= 2000:
                    return b''
                time.sleep(0.05)
                self.sent += 50
                return b'x' * 50

            def read(self, size=-1):
                data = b''
                while len(data) < size:
                    chunk = self.read1()
                    if not chunk:
                        break
                    data += chunk
                return data

        class Opener:
            def open(self, request, timeout):
                return Drip()

        started = time.monotonic()
        with self.assertRaises(OSError):
            scores.fetch_openrouter(0.5, environ={}, opener=Opener())
        self.assertLess(time.monotonic() - started, 0.5 + 0.3)

    def test_fetch_caps_size_and_sends_user_agent(self):
        requests = []

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(b'x' * (scores.MAX_BYTES + 5))

        with self.assertRaises(OSError):
            scores.fetch_openrouter(1, environ={}, opener=Opener())
        self.assertEqual(requests[0].get_header('User-agent'), 'vibe-workflow-smart-router')


if __name__ == '__main__':
    unittest.main()
