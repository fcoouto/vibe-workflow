import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import time
import unittest

from unittest import mock

from runtime import chatgpt_limit, codex_reset, opencode_account, usage_balance

NOW = 1_800_000_000


def fake_codex(folder, body):
    """A fake `codex`: `body` runs after the three expected lines are read (prints answers on `out`)."""
    path = Path(folder) / 'codex'
    path.write_text('#!%s\n' % sys.executable + textwrap.dedent('''\
        import json, sys, time
        def out(value):
            print(json.dumps(value), flush=True)
        def read():
            return json.loads(sys.stdin.readline())
        ''') + textwrap.dedent(body))
    path.chmod(0o700)
    return [str(path)]


ANSWER = '''
read(); out({"id": 1, "result": {}})
read(); read()
out({"id": 2, "result": {"rateLimits": json.loads(%r)}})
'''

RESULT = '''
methods = [read().get("method")]
out({"id": 1, "result": {}})
methods += [read().get("method"), read().get("method")]
open(%r, "w").write(json.dumps(methods))
out({"id": 2, "result": json.loads(%r)})
'''
METER = {"used_percent", "earliest", "latest", "reset_at", "duration", "discrete"}
WEEK, FIVE, FORTNIGHT = 10_080, 300, 20_160


class ChatgptLimitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.state = self.dir / 'state'
        self.state.mkdir(mode=0o700)
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.dir)}  # no VCW_TEST_HOME: fake runs only

    def window(self, percent, reset):
        return dict(usedPercent=percent, windowDurationMins=300, resetsAt=reset)

    def read(self, body, **kw):
        return codex_reset.read_reset(now=NOW, environ=self.env, command=fake_codex(self.dir, body), **kw)

    def test_mark_uses_codex_reset(self):
        record = chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: NOW + 7200)
        self.assertEqual(record, dict(version=1, marked_at=NOW, until=NOW + 7200, source='codex'))
        self.assertEqual(stat.S_IMODE((self.state / chatgpt_limit.FILE).stat().st_mode), 0o600)

    def test_mark_falls_back_to_one_hour(self):
        for reset in (None, NOW - 5):
            record = chatgpt_limit.mark(self.state, NOW, reader=lambda reset=reset, **kw: reset)
            self.assertEqual((record['until'], record['source']), (NOW + 3600, 'fallback'))

    def test_mark_bounds_the_reset(self):
        for reset in (NOW + 9 * 86400, (NOW + 7200) * 1000):
            record = chatgpt_limit.mark(self.state, NOW, reader=lambda reset=reset, **kw: reset)
            self.assertEqual((record['until'], record['source']), (NOW + 3600, 'fallback'))

    def test_mark_never_shortens_a_valid_pause(self):
        codex = chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: NOW + 2 * 86400)
        kept = chatgpt_limit.mark(self.state, NOW + 10, reader=lambda **kw: None)
        self.assertEqual(kept, codex)
        self.assertEqual(chatgpt_limit.remaining(self.state, NOW + 10), 2 * 86400 - 10)
        later = chatgpt_limit.mark(self.state, NOW + 20, reader=lambda **kw: NOW + 3 * 86400)
        self.assertEqual(later, dict(version=1, marked_at=NOW + 20, until=NOW + 3 * 86400, source='codex'))
        self.assertEqual(chatgpt_limit.remaining(self.state, NOW + 20), 3 * 86400 - 20)

    def test_mark_replaces_an_expired_or_invalid_pause(self):
        path = self.state / chatgpt_limit.FILE
        for old in (dict(version=1, marked_at=NOW - 7200, until=NOW - 1, source='codex'),
                    dict(version=1, marked_at=NOW, until=NOW + 9 * 86400, source='codex'), '{bad'):
            with self.subTest(old=old):
                path.write_text(old if type(old) is str else json.dumps(old))
                path.chmod(0o600)
                record = chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: None)
                self.assertEqual(record, dict(version=1, marked_at=NOW, until=NOW + 3600, source='fallback'))
                self.assertEqual(json.loads(path.read_text()), record)

    def test_limited_refuses_an_unbounded_file(self):
        path = self.state / chatgpt_limit.FILE
        path.write_text(json.dumps(dict(version=1, marked_at=NOW, until=NOW + 9 * 86400, source='codex')))
        path.chmod(0o600)
        self.assertFalse(chatgpt_limit.limited(self.state, NOW))
        self.assertIsNone(chatgpt_limit.remaining(self.state, NOW))

    def test_limited_window(self):
        self.assertFalse(chatgpt_limit.limited(self.state, NOW))
        chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: NOW + 100)
        self.assertTrue(chatgpt_limit.limited(self.state, NOW))
        self.assertFalse(chatgpt_limit.limited(self.state, NOW + 100))
        self.assertFalse(chatgpt_limit.limited(self.state, NOW - 1))
        path = self.state / chatgpt_limit.FILE
        path.chmod(0o644)
        self.assertFalse(chatgpt_limit.limited(self.state, NOW))
        path.chmod(0o600)
        path.write_text('{bad')
        self.assertFalse(chatgpt_limit.limited(self.state, NOW))

    def test_quota_gate_excludes_only_chatgpt(self):
        chatgpt_limit.mark(self.state, NOW, reader=lambda **kw: NOW + 100)
        anchor = self.state / 'router.json'
        gate = lambda path, pool: opencode_account._quota_gate(  # noqa: E731
            anchor, dict(route=path + ':m', billing_class='included_subscription', pool=pool), NOW)
        self.assertEqual(gate('opencode-chatgpt-subscription', 'chatgpt-subscription'), ('quota_exhausted', 100))
        self.assertEqual(gate('opencode-go-subscription', 'opencode-go-subscription'), (None, None))
        self.assertEqual(gate('opencode-claude-subscription', 'claude-subscription')[0], None)

    def test_read_reset_picks_highest_used_window(self):
        both = json.dumps(dict(primary=self.window(40, NOW + 100), secondary=self.window(90, NOW + 200)))
        self.assertEqual(self.read(ANSWER % (both,)), NOW + 200)
        only = json.dumps(dict(primary=self.window(40, NOW + 100), secondary=None))
        self.assertEqual(self.read(ANSWER % (only,)), NOW + 100)
        past = json.dumps(dict(primary=self.window(40, NOW + 100), secondary=self.window(90, NOW - 1)))
        self.assertEqual(self.read(ANSWER % (past,)), NOW + 100)

    def test_read_reset_error_answer_is_none(self):
        body = 'read(); out({"id": 1, "result": {}})\nread(); read()\nout({"id": 2, "error": {"code": -32600, "message": "x"}})\n'
        self.assertIsNone(self.read(body))
        self.assertIsNone(self.read('print("not json", flush=True)\ntime.sleep(5)\n'))

    def test_read_reset_deeply_nested_answer_is_none(self):
        body = 'read(); out({"id": 1, "result": {}})\nread(); read()\nprint(\'{"id": 2, "result": \' + "[" * 100000 + "]" * 100000 + "}", flush=True)\n'
        self.assertIsNone(self.read(body))

    def test_read_reset_refuses_under_test_home_and_never_reads_auth(self):
        marker = self.dir / 'spawned'
        command = fake_codex(self.dir, 'open(%r, "w").close()\n' % str(marker))
        self.assertIsNone(codex_reset.read_reset(now=NOW, environ=dict(self.env, VCW_TEST_HOME=str(self.dir)),
                                                 command=command))
        self.assertFalse(marker.exists())
        self.assertIsNone(codex_reset.read_reset(now=NOW, environ=dict(self.env, VCW_TEST_HOME='/x')))
        opened = []
        sys.addaudithook(lambda event, args: opened.append(str(args[0])) if event == 'open' else None)
        both = json.dumps(dict(primary=self.window(40, NOW + 100)))
        self.assertEqual(self.read(ANSWER % (both,)), NOW + 100)
        self.assertFalse([path for path in opened if path.endswith('auth.json')])

    def test_read_reset_times_out(self):
        pidfile = self.dir / 'pid'
        body = 'import os\nopen(%r, "w").write(str(os.getpid()))\ntime.sleep(60)\n' % str(pidfile)
        start = time.monotonic()
        self.assertIsNone(self.read(body, timeout=0.5))
        self.assertLess(time.monotonic() - start, 1.5)
        pid = int(pidfile.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class CodexWindowsTest(unittest.TestCase):
    """`read_windows`: ranking meters of the exact `codex` bucket, keyed by duration and stamped around the exchange."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name).resolve()
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.dir)}  # no VCW_TEST_HOME: fake runs only
        self.methods = self.dir / 'methods.json'

    def window(self, percent, minutes, left):
        return dict(usedPercent=percent, windowDurationMins=minutes, resetsAt=NOW + left)

    def bucket(self, *windows, limit='codex', **extra):
        """A rate-limit snapshot; the first window sits in `primary`, whatever its length."""
        slots = dict(zip(('primary', 'secondary'), windows))
        return dict(limitId=limit, primary=slots.get('primary'), secondary=slots.get('secondary'), **extra)

    def read(self, result, *, clock=(NOW, NOW), **kw):
        """The meters of one fake exchange answering `result`; `clock` is the time before and after it."""
        times = list(clock)
        command = fake_codex(self.dir, RESULT % (str(self.methods), json.dumps(result)))
        with mock.patch('time.time', side_effect=lambda: times.pop(0) if len(times) > 1 else times[0]):
            return codex_reset.read_windows(environ=self.env, command=command, **kw)

    def by_id(self, *windows, **extra):
        return dict(rateLimits=self.bucket(limit=None), rateLimitsByLimitId=dict(codex=self.bucket(*windows, **extra)))

    def test_meters_are_keyed_by_duration_not_by_slot_and_stamped_around_the_exchange(self):
        # The weekly window sits in `primary` and the 5-hour one in `secondary`: the slot names decide nothing.
        result = self.by_id(self.window(90, WEEK, 7200), self.window(25.5, FIVE, 3600))
        hints = self.read(result, clock=(NOW + 0.9, NOW + 2.1))
        self.assertEqual(sorted(hints), ['5h', '7d'])
        self.assertEqual(hints['7d']['used_percent'], 90)
        self.assertEqual((hints['5h']['used_percent'], hints['5h']['duration']), (25.5, 18_000))
        self.assertEqual((hints['7d']['earliest'], hints['7d']['latest']), (NOW, NOW + 3))
        self.assertEqual((hints['7d']['reset_at'], hints['7d']['duration']), (NOW + 7200, 604_800))
        self.assertTrue(all(hints[key]['discrete'] is True and set(hints[key]) == METER for key in hints))
        self.assertTrue(all(usage_balance.fresh_meter(value, now=NOW + 3) for value in hints.values()))
        self.assertEqual(json.loads(self.methods.read_text()), ['initialize', 'initialized', 'account/rateLimits/read'])

    def test_another_duration_protects_headroom_and_never_scores(self):
        scored = {}
        for used in (85, 98):
            hints = self.read(self.by_id(self.window(90, WEEK, 7200), self.window(used, FORTNIGHT, 10 * 86400)))
            extra = hints['duration:1209600']
            self.assertEqual((extra['discrete'], extra['duration'], extra['used_percent']), (False, 1_209_600, used))
            scored[used] = usage_balance.expiry_score('chatgpt', hints, now=NOW)
        self.assertAlmostEqual(scored[85], 8.4)
        self.assertEqual(scored[98], 1)

    def test_only_the_exact_codex_bucket_is_read(self):
        weekly = self.window(90, WEEK, 7200)
        other = dict(rateLimits=self.bucket(weekly, limit='codex_other'),
                     rateLimitsByLimitId=dict(codex_other=self.bucket(weekly, limit='codex_other')))
        unnamed = dict(rateLimits=self.bucket(weekly, limit=None))  # slots alone never identify the plan bucket
        mislabeled = dict(rateLimitsByLimitId=dict(codex=self.bucket(weekly, limit='codex_other')))
        broken = dict(rateLimits=self.bucket(weekly), rateLimitsByLimitId=dict(codex='x'))
        # The map key alone proves no scope: the entry must state `limitId: "codex"` itself. An entry without it is
        # still the preferred view, so the single view never stands in for it.
        absent = dict(primary=weekly, secondary=None)
        missing = dict(rateLimitsByLimitId=dict(codex=absent))
        null = dict(rateLimitsByLimitId=dict(codex=self.bucket(weekly, limit=None)))
        missing_beside_single = dict(rateLimits=self.bucket(weekly), rateLimitsByLimitId=dict(codex=absent))
        null_beside_single = dict(rateLimits=self.bucket(weekly),
                                  rateLimitsByLimitId=dict(codex=self.bucket(weekly, limit=None)))
        for label, result in (('other', other), ('unnamed', unnamed), ('mislabeled', mislabeled), ('broken', broken),
                              ('missing', missing), ('null', null), ('missing beside single', missing_beside_single),
                              ('null beside single', null_beside_single),
                              ('empty text', dict(rateLimitsByLimitId=dict(codex=self.bucket(weekly, limit='')))),
                              ('other case', dict(rateLimitsByLimitId=dict(codex=self.bucket(weekly, limit='Codex')))),
                              ('empty', {}), ('list', dict(rateLimits=[weekly]))):
            with self.subTest(label=label):
                self.assertEqual(self.read(result), {})
        single = dict(rateLimits=self.bucket(weekly), rateLimitsByLimitId=dict(codex_other=self.bucket(weekly)))
        self.assertEqual(self.read(single)['7d']['used_percent'], 90)  # the single view, when it names `codex`

    def test_conflicting_views_of_one_meter_are_not_ranked_and_keep_the_sibling(self):
        five = self.window(40, FIVE, 3600)
        for label, rival in (('percent', self.window(20, WEEK, 7200)), ('reset', self.window(90, WEEK, 7300)),
                             ('invalid', self.window(90, WEEK, -5))):
            with self.subTest(label=label):
                result = dict(rateLimits=self.bucket(rival, five),
                              rateLimitsByLimitId=dict(codex=self.bucket(self.window(90, WEEK, 7200), five)))
                self.assertEqual(sorted(self.read(result)), ['5h'])
        twice = self.by_id(self.window(90, WEEK, 7200), self.window(20, WEEK, 7200))  # one bucket, two claims
        self.assertEqual(self.read(twice), {})
        agreed = dict(rateLimits=self.bucket(self.window(90, WEEK, 7200), five),
                      rateLimitsByLimitId=dict(codex=self.bucket(five, self.window(90, WEEK, 7200))))
        self.assertEqual(sorted(self.read(agreed)), ['5h', '7d'])
        extra = dict(rateLimits=self.bucket(self.window(90, WEEK, 7200), five),
                     rateLimitsByLimitId=dict(codex=self.bucket(five)))
        self.assertEqual(sorted(self.read(extra)), ['5h'])  # the preferred entry decides which meters exist

    def test_a_past_reset_or_an_invalid_duration_gives_no_hint(self):
        for label, window in (
                ('past', self.window(50, WEEK, -1)), ('now', self.window(50, WEEK, 0)),
                ('beyond the window', self.window(50, FIVE, 18_000 + 61)),
                ('zero', self.window(50, 0, 3600)), ('negative', self.window(50, -300, 3600)),
                ('bool', self.window(50, True, 30)), ('float', self.window(50, 300.0, 3600)),
                ('text', self.window(50, '300', 3600)), ('missing', dict(usedPercent=50, resetsAt=NOW + 3600)),
                ('huge', self.window(50, 10 ** 12, 3600)),
                ('percent high', self.window(101, WEEK, 7200)), ('percent low', self.window(-1, WEEK, 7200)),
                ('percent bool', self.window(True, WEEK, 7200)), ('percent text', self.window('90', WEEK, 7200)),
                ('percent nan', self.window(float('nan'), WEEK, 7200)),
                ('percent inf', self.window(float('inf'), WEEK, 7200)),
                ('percent huge', self.window(10 ** 400, WEEK, 7200)),
                ('reset huge', dict(usedPercent=50, windowDurationMins=WEEK, resetsAt=10 ** 400)),
                ('reset float', dict(usedPercent=50, windowDurationMins=WEEK, resetsAt=NOW + 7200.5)),
                ('reset ms', dict(usedPercent=50, windowDurationMins=WEEK, resetsAt=(NOW + 7200) * 1000))):
            with self.subTest(label=label):
                self.assertEqual(self.read(self.by_id(window)), {})
        partial = self.read(self.by_id(self.window(50, WEEK, -1), self.window(40, FIVE, 3600)))
        self.assertEqual(sorted(partial), ['5h'])  # the valid sibling stands; its weekly sibling is unknown
        self.assertEqual(usage_balance.expiry_score('chatgpt', partial, now=NOW), 1)  # and still vetoes its boost

    def test_credit_and_plan_fields_never_reach_a_meter(self):
        result = self.by_id(self.window(90, WEEK, 7200), credits=dict(balance='PRIVATE-SENTINEL', hasCredits=True),
                            planType='PRIVATE-SENTINEL', limitName='PRIVATE-SENTINEL')
        hints = self.read(result)
        self.assertEqual(set(hints['7d']), METER)
        self.assertNotIn('PRIVATE-SENTINEL', json.dumps(hints))

    def test_failures_and_the_test_home_give_no_hint(self):
        marker = self.dir / 'spawned'
        command = fake_codex(self.dir, 'open(%r, "w").close()\n' % str(marker))
        self.assertEqual(codex_reset.read_windows(environ=dict(self.env, VCW_TEST_HOME=str(self.dir)),
                                                  command=command), {})
        self.assertFalse(marker.exists())
        error = 'read(); out({"id": 1, "result": {}})\nread(); read()\nout({"id": 2, "error": {"code": 1}})\n'
        self.assertEqual(codex_reset.read_windows(environ=self.env, command=fake_codex(self.dir, error)), {})
        start = time.monotonic()
        self.assertEqual(codex_reset.read_windows(environ=self.env, timeout=0.5,
                                                  command=fake_codex(self.dir, 'time.sleep(60)\n')), {})
        self.assertLess(time.monotonic() - start, 1.5)

    def test_the_reset_reader_keeps_its_slot_contract_beside_the_window_reader(self):
        result = self.by_id(self.window(90, WEEK, 7200))
        result['rateLimits'] = dict(primary=self.window(40, FIVE, 100), secondary=self.window(90, FIVE, 200))
        command = fake_codex(self.dir, RESULT % (str(self.methods), json.dumps(result)))
        self.assertEqual(codex_reset.read_reset(now=NOW, environ=self.env, command=command), NOW + 200)
        self.assertEqual(self.read(result)['7d']['used_percent'], 90)  # the same answer, read by its named bucket
        del result['rateLimitsByLimitId']['codex']['limitId']  # no stated identity: no hint, the same pause reset
        command = fake_codex(self.dir, RESULT % (str(self.methods), json.dumps(result)))
        self.assertEqual(codex_reset.read_reset(now=NOW, environ=self.env, command=command), NOW + 200)
        self.assertEqual(self.read(result), {})


if __name__ == '__main__':
    unittest.main()
