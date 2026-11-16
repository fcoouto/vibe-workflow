"""Task 8: what the live real-account run showed about OpenCode turns (tests/test_opencode_launcher.py's host double).

A: a sent turn the host ends without success (its idle record says `failed`, no assistant message) is surfaced as
`failed`/`host_turn_failed`, settled as sent. C: the root cause of the live ChatGPT failure. The identity's `none`
effort sentinel was sent as an omitted variant even for a model that lists a literal `none`; OpenCode 2.2.6 stored that
session as `default`, and the shim's model check refused `default` against the admitted `none` (`route_mismatch`)
inside a sent turn.
"""
import unittest

from tests.test_opencode_launcher import MODEL, PROVIDER, LauncherBase, route

FAILURE = dict(error=dict(type='unknown', message='invented failure'))  # the observed 2.2.6 idle-record error shape


class TurnOutcomeTests(LauncherBase):
    def test_a_succeeded_turn_carries_no_failure(self):
        result = self.launch()
        self.assertEqual((result['state'], result['event']), ('recorded', 'complete'), result)
        self.assertNotIn('failed', result)
        self.assertIsNone(result.get('reason'))

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
