"""Wave F1 addendum E1-E4: a provider may reset an account early, so an exhausted reading refuses only briefly, a
provider quota-limit error marks its pool for at most 120 s, and every such refusal names `retry_after_seconds`.
Synthetic stores and explicit clocks only: no account, client or network."""
import time
import unittest
from unittest import mock

from runtime import codex_account, delegation_routing, native_routing, opencode_account, quota_recheck, \
    routing_observations as collector
from tests.test_opencode_account import route_of
from tests.test_routing_observations import Stores

CODEX = dict(route='codex-chatgpt-subscription:choice:0123456789abcdef', account='chatgpt-plan',
             pool='chatgpt-subscription', billing_class='included_subscription')


class ShortTrustTests(Stores):
    def seed(self, used_percent, read_at):
        key, generation = 'b' * 32, 'd' * 32
        quota = dict(windows=[dict(used_percent=used_percent, reset_at=read_at + 3600)], ordinary_allowed=True,
                     read_at=read_at)
        codex_account._save(self.settings, identity=dict(key=key, generation=generation), account=dict(
            key=key, generation=generation, observed_at=read_at, client_version='unknown',
            usage_reason=codex_account.USAGE_UNCONFIRMED, quota=quota))

    def test_an_exhausted_reading_refuses_only_within_the_recheck_window(self):
        now = int(time.time())
        self.assertEqual(collector.EXHAUSTED_RECHECK_SECONDS, 60)
        self.seed(100, now - 15)
        self.assertEqual((codex_account.quota_problem(self.settings, CODEX, now),
                          codex_account.quota_retry_after(self.settings, CODEX, now)), ('quota_exhausted', 45))
        self.seed(100, now - 61)  # scheduled reset still an hour ahead, but the reading is too old to keep refusing
        self.assertEqual((codex_account.quota_problem(self.settings, CODEX, now),
                          codex_account.quota_retry_after(self.settings, CODEX, now)), (None, None))
        self.seed(100, now + 30)  # a clock that went back never refuses
        self.assertIsNone(codex_account.quota_problem(self.settings, CODEX, now))
        self.seed(50, now)  # a reading with capacity never refuses, whatever its schedule
        self.assertIsNone(codex_account.quota_problem(self.settings, CODEX, now))

    def test_a_provider_quota_error_marks_the_opencode_pool_for_at_most_120_seconds(self):
        now = int(time.time())
        chatgpt = route_of('chatgpt')
        self.assertIsNone(opencode_account.quota_problem(self.settings, chatgpt, now))
        opencode_account.mark_provider_limit(self.settings, chatgpt, now)
        self.assertEqual((opencode_account.quota_problem(self.settings, chatgpt, now + 10),
                          opencode_account.quota_retry_after(self.settings, chatgpt, now + 10)),
                         ('quota_exhausted', 110))
        self.assertIsNone(opencode_account.quota_problem(self.settings, chatgpt, now + 121))  # the next prepare sends
        self.assertIsNone(opencode_account.quota_problem(self.settings, chatgpt, now - 5))  # clock jump: no refusal
        self.assertIsNone(opencode_account.quota_problem(self.settings, route_of('go'), now + 10))  # its own pool only
        self.assertIsNone(codex_account.quota_problem(self.settings, CODEX, now + 10))  # never a native Codex refusal

    def test_the_source_names_the_remaining_trust_window(self):
        now = int(time.time())
        self.seed(100, now - 20)
        source = collector.LocalSource(self.settings)
        self.assertEqual(source.quota_retry(dict(CODEX), now), 40)


class RecheckTests(ShortTrustTests):
    """E1/E2: one bounded re-read on a routing request only when an exhausted reading has gone stale, at most once per
    window; nothing is read or written while nothing is exhausted; the Codex cross-check only clears."""

    def reader(self, used_percent=None):
        calls = []

        def refresh(settings_path, **options):
            calls.append(options)
            if used_percent is not None:
                self.seed(used_percent, int(time.time()))
        self.enterContext(mock.patch.object(codex_account, 'refresh', refresh))
        return calls

    def recheck_file(self):
        return collector._resolved(collector.window_path(self.settings)).parent / collector.RECHECK_FILE

    def test_a_stale_exhausted_reading_is_re_read_once_and_an_early_reset_admits(self):
        now = int(time.time())
        self.seed(100, now - 90)  # the schedule says an hour more, but the provider may have reset already
        self.assertEqual(quota_recheck.due(self.settings, [CODEX], now), {'codex'})
        calls = self.reader(used_percent=10)  # the second read shows capacity: an early reset
        quota_recheck.recheck(self.settings, [CODEX], now)
        self.assertEqual(calls, [dict(force=True)])
        self.assertIsNone(codex_account.quota_problem(self.settings, CODEX, now))

    def test_a_failed_re_read_is_not_repeated_within_the_window(self):
        now = int(time.time())
        self.seed(100, now - 90)
        calls = self.reader()  # the read fails: the reading stays stale (and never refuses)
        quota_recheck.recheck(self.settings, [CODEX], now)
        quota_recheck.recheck(self.settings, [CODEX], now + 30)
        self.assertEqual(len(calls), 1)
        quota_recheck.recheck(self.settings, [CODEX], now + 61)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(codex_account.quota_problem(self.settings, CODEX, now))

    def test_nothing_exhausted_reads_and_writes_nothing(self):
        now = int(time.time())
        self.seed(50, now - 600)
        calls = self.reader()
        quota_recheck.recheck(self.settings, [CODEX, route_of('chatgpt'), route_of('go')], now)
        self.assertEqual((calls, self.recheck_file().exists()), ([], False))

    def test_the_codex_cross_check_clears_a_mark_and_never_refuses_on_its_own(self):
        now = int(time.time())
        chatgpt = route_of('chatgpt')
        self.seed(100, now)  # a full native Codex reading alone never refuses the OpenCode ChatGPT route
        self.assertIsNone(opencode_account.quota_problem(self.settings, chatgpt, now))
        opencode_account.mark_provider_limit(self.settings, chatgpt, now)
        self.assertEqual(quota_recheck.due(self.settings, [chatgpt], now + 5), {'codex'})  # read only while marked
        self.seed(100, now + 5)  # newer but still full: the mark keeps refusing
        self.assertEqual(opencode_account.quota_problem(self.settings, chatgpt, now + 10), 'quota_exhausted')
        self.seed(20, now + 6)  # newer and with capacity: an early reset, recognized without a send
        self.assertIsNone(opencode_account.quota_problem(self.settings, chatgpt, now + 10))


class ReplyTests(unittest.TestCase):
    def test_every_host_reply_keeps_retry_after_seconds(self):
        answer = dict(state='pending', reason='no_feasible_route', reasons=['no_feasible_route', 'quota_exhausted'],
                      retry_after_seconds=45)
        self.assertEqual(delegation_routing._refusal(answer), answer)
        self.assertIn('retry_after_seconds', native_routing.RELAYED)


if __name__ == '__main__':
    unittest.main()
