"""Root-pinned managed continuations (account-continuity design §4, Task 3B).

A continuation is admitted only on its trusted root: the assignment's own first admission, resolved through the
routing ledger, with a valid subscription account marker. A missing, pruned or legacy root refuses; the original
selection, the current login or another route is never substituted. A child admitted on another account marker is
closed provably unsent and refused. Real managed fixture stores and services; the account fact is the fixture's
labelled synthetic one, whose producer a test changes to model an observed account change.
"""
import contextlib
import time
import unittest
from unittest import mock

from runtime import delegation_routing, routing_launch, routing_observations, workstreams
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
            scope = before(prepared) if before is not None else None  # a returned patch spans this prepare only
            with scope if scope is not None else contextlib.nullcontext():
                intent = workstreams.prepare_continuation(self.scope, self.request(
                    attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision'], reason='follow-up',
                    prompt='Finish the original accepted scope', human_continue=False,
                    host_proof=proof['host_proof']))
        return prepared, intent

    def consumed(self, between):
        """Prepare a continuation of the dispatched root turn, apply `between`, then the public consume."""
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        clock, real = dict(now=int(time.time())), routing_observations.mark_consumed

        def mark(*args):
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
            intent = workstreams.prepare_continuation(self.scope, self.request(
                attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision'], reason='follow-up',
                prompt='Finish the original accepted scope', human_continue=False, host_proof=proof['host_proof']))
            self.assertEqual(intent['routing']['state'], 'admitted', intent)
            with between(prepared):
                try:
                    sent = workstreams.consume_continuation(self.scope, self.request(
                        attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision'],
                        token=intent['token']))
                except Exception as error:  # noqa: BLE001 - the refusal raised before any send
                    sent = error
        return prepared, intent['routing']['attempt_id'], sent

    def test_consume_keeps_one_permit_on_the_same_account_and_refuses_a_b_and_a_b_a(self):
        prepared, child, sent = self.consumed(lambda prepared: contextlib.nullcontext())
        self.assertTrue(sent['send_admitted'], sent)
        self.assertEqual(self.hold(child), 'dispatched')
        self.assertEqual(self.admission(child)['route_identity'], self.admission(prepared['attempt_id'])['route_identity'])
        for producer in ('synthetic:another-account', 'synthetic:account-a:generation-2'):
            with self.subTest(producer=producer):
                self.setUp()

                def switch(prepared, producer=producer):
                    source = routing_observations.default_source()
                    billing = source._billing
                    return mock.patch.object(source, '_billing', lambda route, now: dict(billing(route, now),
                                                                                         producer=producer))
                prepared, child, sent = self.consumed(switch)
                self.assertIsInstance(sent, Exception)
                self.assertIn('account_changed', str(sent))
                self.assertEqual(self.hold(child), 'admitted')  # never dispatched: provably unsent until closed

    def test_consume_rechecks_a_missing_or_legacy_root(self):
        real = delegation_routing._hold
        for name, root in (('missing', lambda admission: None),
                           ('legacy', lambda admission: dict(admission, binding=dict(admission['binding'],
                                                                                     credential=None)))):
            with self.subTest(root=name):
                self.setUp()

                def lose(prepared, root=root):
                    def read(service, attempt_id):
                        hold, admission = real(service, attempt_id)
                        return (hold, root(admission)) if attempt_id == prepared['attempt_id'] else (hold, admission)
                    return mock.patch.object(delegation_routing, '_hold', read)  # the ledger read at consume only
                prepared, child, sent = self.consumed(lose)
                self.assertIsInstance(sent, Exception)
                self.assertIn('continuation_root_unavailable', str(sent))
                self.assertEqual(self.hold(child), 'admitted')

    def split(self, between):
        """A prepared managed assignment, then `between`, then the public split consume (`consume_dispatch`)."""
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        with self.managed(delegation_tests.NATIVE):
            prepared = self.prepare(workstream_revision=enabled['revision'], workstream_limits=LIMITS)
            with between(prepared):
                try:
                    sent = workstreams.consume_dispatch(self.scope, self.request(
                        attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision']))
                except Exception as error:  # noqa: BLE001 - the refusal raised before any send
                    sent = error
        return prepared, sent

    def test_split_consume_keeps_one_permit_and_refuses_a_b_and_a_b_a(self):
        prepared, sent = self.split(lambda prepared: contextlib.nullcontext())
        self.assertNotIsInstance(sent, Exception)
        self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')  # the same marked account: one permit
        for producer in ('synthetic:another-account', 'synthetic:account-a:generation-2'):
            with self.subTest(producer=producer):
                self.setUp()

                def switch(prepared, producer=producer):
                    source = routing_observations.default_source()
                    billing = source._billing
                    return mock.patch.object(source, '_billing', lambda route, now: dict(billing(route, now),
                                                                                         producer=producer))
                prepared, sent = self.split(switch)
                self.assertIsInstance(sent, Exception)
                self.assertIn('account_changed', str(sent))
                self.assertEqual(self.hold(prepared['attempt_id']), 'admitted')  # no permit, nothing sent

    def immediate(self, producer=None):
        """The managed immediate prepare (admit, check, dispatch in one call); `producer` switches the account after
        collection and admission, just before dispatch, at the `admitted_check` seam (a new B before collection would
        be legitimately admissible)."""
        with self.managed(delegation_tests.NATIVE):
            if producer is None:
                return self.prepare()
            source, real = routing_observations.default_source(), routing_launch.admitted_check
            billing = source._billing

            def switched(*args, **kwargs):
                source._billing = lambda route, now: dict(billing(route, now), producer=producer)
                return real(*args, **kwargs)
            with mock.patch.object(routing_launch, 'admitted_check', switched):
                return self.prepare()

    def test_the_managed_immediate_prepare_keeps_one_permit_and_refuses_a_b_and_a_b_a(self):
        reply = self.immediate()
        self.assertEqual((reply['routing']['state'], reply.get('launch_admitted')), ('admitted', True), reply)
        self.assertEqual(self.hold(reply['attempt_id']), 'dispatched')
        for producer in ('synthetic:another-account', 'synthetic:account-a:generation-2'):
            with self.subTest(producer=producer):
                self.setUp()
                reply = self.immediate(producer)
                self.assertEqual((reply['routing']['state'], reply['routing']['reason']), ('blocked', 'account_changed'))
                self.assertFalse(reply.get('launch_admitted'))
                self.assertNotEqual(self.hold(reply['attempt_id']), 'dispatched')  # released, provably unsent

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
        for name, root in (('missing', lambda admission: None),
                           ('legacy', lambda admission: admission and dict(admission, binding=dict(
                               admission['binding'], credential=None)))):  # an unmarked root record
            with self.subTest(root=name):
                self.setUp()

                def pruned(service, attempt_id, root=root):  # the root admission as the ledger read returns it
                    hold, admission = real(service, attempt_id)
                    return (hold, root(admission)) if '.c' not in attempt_id else (hold, admission)

                def lose_root(prepared, pruned=pruned):
                    return mock.patch.object(delegation_routing, '_hold', pruned)
                prepared, intent = self.continued(lose_root)
                self.assertEqual((intent['routing']['state'], intent['routing']['reason']),
                                 ('blocked', 'continuation_root_unavailable'))
                self.assertIsNone(self.admission(prepared['attempt_id'] + '.c1'))  # nothing admitted or substituted


if __name__ == '__main__':
    unittest.main()
