"""Root-pinned managed continuations (account-continuity design §4, Task 3B).

A continuation is admitted only on its trusted root: the assignment's own first admission, resolved through the
routing ledger, with a valid subscription account marker. A missing, pruned or legacy root refuses; the original
selection, the current login or another route is never substituted. A child admitted on another account marker is
closed provably unsent and refused. Real managed fixture stores and services; the account fact is the fixture's
labelled synthetic one, whose producer a test changes to model an observed account change.
"""
import time
import unittest
from unittest import mock

from runtime import delegation_routing, routing_observations, workstreams
from tests import test_delegation_routing as delegation_tests

LIMITS = dict(context_id='WS42.01', rationale='One bounded outcome', output_tokens=12000, context_tokens=64000,
              max_steps=40, max_seconds=1200, max_continuations=3, backoff_seconds=0)


class ContinuationRootTest(delegation_tests.ManagedDelegation):
    def continued(self, before=None):
        """Dispatch and settle the root turn, then prepare its continuation (after `before`, when given)."""
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        clock, real = dict(now=int(time.time())), routing_observations.mark_consumed

        def mark(*args):  # the next reading is observed after the earlier turn's mark (as the managed suite does)
            written = real(*args)
            clock['now'] += 1
            return written
        with self.managed(delegation_tests.NATIVE), mock.patch('time.time', lambda: float(clock['now'])), \
                mock.patch.object(routing_observations, 'mark_consumed', mark):
            prepared = self.prepare(workstream_revision=enabled['revision'], workstream_limits=LIMITS)
            workstreams.consume_dispatch(self.scope, self.request(attempt_id=prepared['attempt_id'],
                                                                  workstream_revision=enabled['revision']))
            self.observe_worker(prepared)
            proof = self.worker_reconciliation_request(prepared, outcome='failed')
            if before is not None:
                before(prepared)
            intent = workstreams.prepare_continuation(self.scope, self.request(
                attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision'], reason='follow-up',
                prompt='Finish the original accepted scope', human_continue=False, host_proof=proof['host_proof']))
        return prepared, intent

    def test_a_continuation_keeps_its_roots_marker(self):
        prepared, intent = self.continued()
        self.assertEqual(intent['routing']['state'], 'admitted', intent)
        root, child = self.admission(prepared['attempt_id']), self.admission(intent['routing']['attempt_id'])
        self.assertRegex(root['binding']['credential'], '^[0-9a-f]{64}$')
        self.assertEqual(child['binding']['credential'], root['binding']['credential'])

    def test_an_account_change_closes_the_new_child_unsent_and_substitutes_nothing(self):
        def switch(prepared):
            source = routing_observations.default_source()
            billing = source._billing
            source._billing = lambda route, now: dict(billing(route, now), producer='synthetic:another-account')
        prepared, intent = self.continued(switch)
        self.assertEqual((intent['routing']['state'], intent['routing']['reason']), ('blocked', 'account_changed'))
        child = prepared['attempt_id'] + '.c1'
        self.assertIsNotNone(self.admission(child))  # admitted on the new identity, then closed provably unsent
        self.assertNotIn(self.hold(child), ('admitted', 'dispatched'))

    def test_a_missing_or_pruned_root_refuses_without_the_original_selection(self):
        real = delegation_routing._hold

        def pruned(service, attempt_id):  # the root admission is no longer in the ledger
            hold, admission = real(service, attempt_id)
            return (hold, None) if '.c' not in attempt_id else (hold, admission)

        def lose_root(prepared):
            self.enterContext(mock.patch.object(delegation_routing, '_hold', pruned))
        prepared, intent = self.continued(lose_root)
        self.assertEqual((intent['routing']['state'], intent['routing']['reason']),
                         ('blocked', 'continuation_root_unavailable'))
        self.assertIsNone(self.admission(prepared['attempt_id'] + '.c1'))  # nothing admitted, nothing substituted


if __name__ == '__main__':
    unittest.main()
