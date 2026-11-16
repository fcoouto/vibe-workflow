"""Task 8: what the live real-account run showed about OpenCode turns (tests/test_opencode_launcher.py's host double).

A: a sent turn the host ends without success (its idle record says `failed`, no assistant message) is surfaced as
`failed`/`host_turn_failed`, settled as sent. C: the root cause of the live ChatGPT failure. The identity's `none`
effort sentinel was sent as an omitted variant even for a model that lists a literal `none`; OpenCode 2.2.6 stored that
session as `default`, and the shim's model check refused `default` against the admitted `none` (`route_mismatch`)
inside a sent turn.
"""
import unittest

from runtime import routing, routing_observations as collector, subscription_fallback
from tests.test_opencode_launcher import MODEL, POOL, PROVIDER, LauncherBase, route

FAILURE = dict(error=dict(type='unknown', message='invented failure'))  # the observed 2.2.6 idle-record error shape


class TurnOutcomeTests(LauncherBase):
    def test_a_succeeded_turn_carries_no_failure(self):
        result = self.launch()
        self.assertEqual((result['state'], result['event']), ('recorded', 'complete'), result)
        self.assertNotIn('failed', result)
        self.assertNotIn('turn_outcome', result)
        self.assertIsNone(result.get('reason'))

    def outcome(self, value):
        """The host's idle records answer `outcome=value` (Task 11, N4)."""
        original = self.host.request

        def request(method, path, body=None, query=None):
            status, answer = original(method, path, body=body, query=query)
            if method == 'GET' and path.endswith('/message') and isinstance(answer, dict):
                answer = dict(answer, data=[dict(item, outcome=value) if item.get('type') == 'idle' else item
                                            for item in answer.get('data') or []])
            return status, answer
        self.host.request = request

    def test_only_known_failure_outcomes_fail_a_turn(self):
        for value, failed in (('aborted', True), ('error', True), ('cancelled', True), ('stopped-early', False)):
            with self.subTest(value=value):
                self.setUp()
                self.outcome(value)
                result = self.launch()
                self.assertEqual((result['state'], result['event']), ('recorded', 'complete'), result)
                self.assertEqual(result.get('failed', False), failed)
                self.assertEqual(result.get('turn_outcome'), None if failed else 'unknown')
                self.assertEqual(self.holds()['a1']['state'], 'released')  # settlement unchanged

    def test_a_missing_outcome_is_unknown_never_a_failed_turn(self):
        """Task 9 (G3): another OpenCode version may write no `outcome`; only an explicit non-success fails a turn."""
        original = self.host.request

        def request(method, path, body=None, query=None):  # the idle records without their `outcome`
            status, answer = original(method, path, body=body, query=query)
            if method == 'GET' and path.endswith('/message') and isinstance(answer, dict):
                answer = dict(answer, data=[{key: value for key, value in item.items()
                                             if not (item.get('type') == 'idle' and key == 'outcome')}
                                            for item in answer.get('data') or []])
            return status, answer
        self.host.request = request
        result = self.launch()
        self.assertEqual((result['state'], result['event']), ('recorded', 'complete'), result)
        self.assertEqual(result.get('turn_outcome'), 'unknown')
        self.assertNotIn('failed', result)
        self.assertIsNone(result.get('reason'))
        self.assertEqual(self.holds()['a1']['state'], 'released')  # settlement unchanged

    def test_a_failed_host_turn_is_surfaced_and_still_settled(self):
        """The prompt was sent, so settlement is unchanged (`record complete`, the hold released as sent), but the
        result says the turn failed instead of reporting a successful run."""
        self.host.turn_fails = FAILURE
        result = self.launch()
        self.assertEqual((result['state'], result['event']), ('recorded', 'complete'), result)
        self.assertEqual((result.get('failed'), result.get('reason')), (True, 'host_turn_failed'))
        self.assertIs(result['not_entitled'], False)
        self.assertEqual(self.holds()['a1']['state'], 'released')
        self.assertEqual([item for item in self.order if item in ('create', 'prompt', 'record:complete')],
                         ['create', 'prompt', 'record:complete'])  # one send, settled as sent


class RealHostVariantTests(LauncherBase):
    """The default host double stores like the parent's zero-send 2.2.6 probe (2026-11-16): a sent variant exactly,
    an omitted one as `default`. So a model that lists no variants is refused before sending."""

    def test_a_variant_less_model_is_refused_as_attach_failed_with_nothing_sent(self):
        self.host.models = [dict(providerID=PROVIDER, id=MODEL)]  # lists no variants: the variant stays omitted
        self.routes = [route(effort='none')]
        result = self.launch(assignment=self.assignment(effort='none'))
        self.assertEqual((result['state'], result.get('reason')), ('blocked', 'attach_failed'), result)
        self.assertNotIn('prompt', self.order)  # no prompt, so no model request and no spend
        self.assertEqual([item for session in self.host.sessions.values() for item in session['messages']], [])
        self.assertNotIn(self.holds()['a1']['state'], ('admitted', 'dispatched'))

    def test_a_model_with_variants_still_passes(self):
        result = self.launch()  # the default model lists variants and the admitted one is sent and kept
        self.assertEqual((result['state'], result['event']), ('recorded', 'complete'), result)
        self.assertNotIn('failed', result)


class ConsumptionTests(LauncherBase):
    """Task 12 (F1): an OpenCode Claude claim draws on the native login's bound rows, so its own reply readings never
    cover it: the completion marks the pool consumed, as a native completion does, and no alias claims those rows
    again until a reading observed after it arrives."""

    def covering_reply(self):
        resets = self.now + 3000
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=resets,
                                    unifiedWindows=dict(five_hour=dict(utilization=0.25, resetsAt=resets,
                                                                       status='allowed')))

    def test_an_opencode_claude_completion_marks_the_pool_even_when_its_reply_covers(self):
        self.routes = [route(route='opencode-claude-subscription:s-claude', native_rows=True)]  # Task 14: its basis
        self.covering_reply()
        first = self.launch()
        self.assertEqual((first['state'], first['admission']['reserved'] != {}), ('recorded', True), first)
        self.assertGreaterEqual(collector.consumed_at(self.settings, POOL), first['admission']['at'])
        second = self.launch(assignment=self.assignment('a2'))  # the rows it claimed from are no longer fresh
        self.assertEqual(second['admission']['reserved'], {}, second)
        self.assertIn(routing.FALLBACK, second['admission']['reason_codes'])

    def test_a_refused_attach_after_dispatch_still_marks_the_pool(self):
        """Task 13 (M1): nothing was sent, yet the closing `complete` marks an OpenCode Claude claim's pool (as for a
        fallback): an availability cost only, never over-admission."""
        self.routes = [route(route='opencode-claude-subscription:s-claude', native_rows=True)]
        self.host.attach_ok = False
        result = self.launch()
        self.assertEqual((result['state'], result['reason']), ('blocked', 'attach_failed'), result)
        self.assertEqual([item for item in self.order if item in ('create', 'prompt')], [])  # nothing sent
        self.assertGreater(collector.consumed_at(self.settings, POOL), 0)

    def test_a_refusal_before_dispatch_leaves_the_pool_unmarked(self):
        """Task 13 (P2): an OpenCode Claude claim closed before dispatch (`not_dispatched`) consumed nothing."""
        self.routes = [route(route='opencode-claude-subscription:s-claude')]
        self.host.health_after = 1  # the health re-check right before dispatch fails
        result = self.launch()
        self.assertEqual(result['state'], 'blocked', result)
        self.assertNotIn('dispatch', self.order)
        self.assertEqual(collector.consumed_at(self.settings, POOL), 0)

    def test_a_stored_row_claim_is_covered_by_its_reply_and_never_marked(self):
        """Task 14: an OpenCode Claude claim the collector did not draw on native rows (here a code source's stored
        rows) keeps the pre-F1 covering: its reply covers it, no mark, and the next claim is included again."""
        self.routes = [route(route='opencode-claude-subscription:s-claude')]
        self.covering_reply()
        first = self.launch()
        self.assertNotIn(subscription_fallback.NATIVE_ROWS, first['admission']['reason_codes'])
        self.assertTrue(first['admission']['reserved'], first)
        self.assertEqual(collector.consumed_at(self.settings, POOL), 0)
        second = self.launch(assignment=self.assignment('a2'))
        self.assertTrue(second['admission']['reserved'], second)

    def test_another_path_with_a_covering_reply_is_unchanged(self):
        self.covering_reply()  # control: `claude-haiku-low` is no OpenCode Claude path; its own reading covers it
        self.assertEqual(self.launch()['state'], 'recorded')
        self.assertEqual(collector.consumed_at(self.settings, POOL), 0)

    def test_the_shared_terminal_rule(self):
        """The ledger's `complete` finalization (and reconcile) marks fallback and OpenCode Claude included records."""
        native = dict(route='claude-native-subscription:x', billing_class='included_subscription', reason_codes=[])
        opencode = dict(native, route='opencode-claude-subscription:x',
                        reason_codes=[subscription_fallback.NATIVE_ROWS])
        fallback = dict(native, reason_codes=['quota_unknown', routing.FALLBACK])
        self.assertEqual([subscription_fallback.terminal(item) for item in (native, opencode, fallback)],
                         [False, True, True])
        stored = dict(opencode, reason_codes=[])  # Task 14: a claim on the pool's stored rows keeps its covering
        self.assertFalse(subscription_fallback.terminal(stored))


class VariantTests(LauncherBase):
    def test_an_admitted_none_effort_is_sent_as_the_listed_literal_variant(self):
        self.host.models = [dict(providerID=PROVIDER, id=MODEL, variants=['none', 'low'])]
        self.host.stored_variant = dict(variant='default')
        self.routes = [route(effort='none')]
        result = self.launch(assignment=self.assignment(effort='none'))
        self.assertEqual(result['state'], 'recorded', result)
        self.assertNotIn('failed', result)
        self.assertEqual(self.host.created['model'], dict(id=MODEL, providerID=PROVIDER, variant='none'))
        self.assertEqual(self.holds()['a1']['state'], 'released')

    def test_a_session_the_host_stores_with_another_model_is_refused_before_any_prompt(self):
        self.host.models = [dict(providerID=PROVIDER, id=MODEL)]  # no listed `none`: the variant stays omitted
        self.host.stored_variant = dict(variant='default')  # and the host substitutes its own
        self.routes = [route(effort='none')]
        result = self.launch(assignment=self.assignment(effort='none'))
        self.assertEqual((result['state'], result.get('reason')), ('blocked', 'attach_failed'), result)
        self.assertIn('create', self.order)
        self.assertNotIn('prompt', self.order)  # nothing sent
        self.assertNotIn(self.holds()['a1']['state'], ('admitted', 'dispatched'))


if __name__ == '__main__':
    unittest.main()
