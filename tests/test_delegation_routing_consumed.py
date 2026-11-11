"""F4 recheck N-1: a managed Codex delegation turn settles without a window reading, so its admitted shared pool is
marked used (`routing_observations.mark_consumed`, once per attempt) before its dispatched hold releases, at both
completion sites: `reconcile_assignment` (`delegation_routing.settled`) and an earlier continuation turn
(`delegation_routing._close_turns`).

Real Codex delegation preparation on `LocalSource` with temporary stores (the P2 fixture of
tests/test_routing_production_flows.py). The Codex reader is a labelled double here: it fails (`codex_unavailable`),
or returns an independently newer reading; no process, network, model or provider call is made.

A closure kept held (failed mark, or a failed release after the mark) is recovered here only through existing
commands: a replay of `reconcile_assignment` does not reach the hook, so the public path is the client `record
uncertain` followed by the one-shot operator `reconcile_subscription` abandon. The direct hook calls below prove
crash ordering and mark-once semantics only.
"""
from contextlib import closing
import io
import json
import time
import unittest
from unittest import mock

from runtime import codex_rate_limits, delegation, delegation_routing, routing_launch, \
    routing_observations as collector, subscription_reconcile, workstreams
from scripts import routing as command
from tests import test_routing_production_flows as flows
from tests.test_routing_command import job
from tests.test_routing_observations import SCOPE

CODEX_PATH, CODEX_POOL, CLAUDE_PATH, CLAUDE_POOL = flows.CODEX_PATH, flows.CODEX_POOL, flows.CLAUDE_PATH, \
    flows.CLAUDE_POOL
LIMITS = dict(context_id='WS42.01', rationale='One bounded outcome', output_tokens=12000, context_tokens=64000,
              max_steps=40, max_seconds=1200, max_continuations=3, backoff_seconds=0)


class ManagedCompletionMarks(flows.CodexDelegationFlowTests):
    def ready(self, aliases=False):
        self.qualify(CODEX_PATH, {'7d': 5})
        self.seed(CODEX_POOL, '7d', 604800, 'seed-codex-7d')
        if aliases:  # the OpenCode alias of the same pool, and an unrelated pool
            self.qualify('opencode-chatgpt-subscription', {'7d': 5})
            self.qualify(CLAUDE_PATH, {'5h': 5, '7d': 5})
            for window, length in (('5h', 18000), ('7d', 604800)):
                self.seed(CLAUDE_POOL, window, length, 'seed-claude-' + window)

    def reader(self, newer=None):
        """The Codex reader double: `codex_unavailable`, or with `newer` a reading observed after the clock moves."""
        def read(**options):
            if newer is None:
                raise codex_rate_limits.ReadError('codex_unavailable')
            newer['now'] += 2
            return [flows.week_window(newer['now'])]
        return mock.patch.object(codex_rate_limits, 'read_windows', side_effect=read)

    def clock(self):
        """A test clock every module reads through `time.time` (moved by the newer-reading double)."""
        value = dict(now=int(time.time()))
        patcher = mock.patch('time.time', lambda: float(value['now']))
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def spy(self):
        """Every mark_consumed call with the attempt's hold state at that moment."""
        calls, real = [], collector.mark_consumed

        def mark(settings_path, pool, at, attempt):
            calls.append((pool, attempt, self.hold(attempt)))
            return real(settings_path, pool, at, attempt)
        return calls, mock.patch.object(collector, 'mark_consumed', mark)

    def snapshot(self):
        with closing(command.Service(self.settings, self.ledger)) as service:
            return service._ledger(False).snapshot()

    def hold(self, attempt):
        return (self.snapshot()['holds'].get(attempt) or {}).get('state')

    def find(self, attempt):
        return next((r for r in self.snapshot()['admissions'] if r['attempt_id'] == attempt), None)

    def direct(self, attempt, host, harness, **fields):
        """A routing `prepare` on this pool's alias from another launcher (or an unrelated pool)."""
        assignment = dict(attempt_id=attempt, task='task-' + attempt, role='software-engineer', scope=SCOPE,
                          choice=routing_launch.choice(host, 'subscription', **fields))
        body = dict(v=1, op='prepare', assignment=assignment, job=job(), constraints=dict(allowed_harnesses=[harness]))
        with closing(command.Service(self.settings, self.ledger, project=self.repo)) as service:
            return service.handle(json.dumps(body).encode())

    def codex_alias(self, attempt):
        return self.direct(attempt, 'codex_native', 'codex-native', model='new-native-model', effort='high')

    def opencode_alias(self, attempt):
        return self.direct(attempt, 'opencode', routing_launch.shim_harness('openai'), provider='openai',
                           model='new-native-model', effort='high')

    def continued(self):
        """A dispatched Workstreams assignment whose native turn ended, and its `prepare_continuation` call."""
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        prepared = self.prepare(workstream_revision=enabled['revision'], workstream_limits=LIMITS)
        self.assertTrue(prepared['dispatch_prepared'], prepared)
        workstreams.consume_dispatch(self.scope, self.request(attempt_id=prepared['attempt_id'],
                                                              workstream_revision=enabled['revision']))
        self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')
        self.observe_worker(prepared)
        proof = self.worker_reconciliation_request(prepared, outcome='failed')
        request = self.request(attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision'],
                               reason='follow-up', prompt='Finish the original accepted scope', human_continue=False,
                               host_proof=proof['host_proof'])
        return prepared, enabled, lambda: workstreams.prepare_continuation(self.scope, request)

    def failing_release(self):
        """`record complete` refused once (a busy ledger) after the mark was written."""
        real, failed = delegation_routing._record, []

        def record(service, attempt, kind, event_id):
            if kind == 'complete' and not failed:
                failed.append(attempt)
                return dict(state='blocked', reason='ledger_busy')
            return real(service, attempt, kind, event_id)
        return mock.patch.object(delegation_routing, '_record', record)

    # -- 1: settlement ----------------------------------------------------------------------------------------------
    def test_a_managed_completion_marks_its_admitted_pool_before_the_hold_releases(self):
        self.ready(aliases=True)
        self.assertEqual(self.opencode_alias('alias-before')['state'], 'admitted')  # the alias shares fresh readings
        prepared = self.prepare(dispatch_id='mark-1')
        attempt = prepared['attempt_id']
        self.assertEqual((self.hold(attempt), self.find(attempt)['pool']), ('dispatched', CODEX_POOL))
        calls, spy = self.spy()
        with spy:
            settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                prepared, outcome='cancelled'))
        self.assertEqual(settled['routing']['closed'], [dict(attempt_id=attempt, kind='complete', state='recorded')])
        self.assertEqual(calls, [(CODEX_POOL, attempt, 'dispatched')])  # marked while the hold still counted
        self.assertEqual(self.hold(attempt), 'released')
        self.assertGreater(collector.consumed_at(self.settings, CODEX_POOL), 0)
        with self.reader():
            for name, answer in (('codex', self.codex_alias('alias-codex')),
                                 ('opencode', self.opencode_alias('alias-opencode'))):
                with self.subTest(alias=name):
                    self.assertNotEqual(answer['state'], 'admitted', answer)  # never the pre-turn headroom
        claude = self.direct('other-pool', 'opencode', routing_launch.shim_harness('claude-agent'),
                             provider='claude-agent', model='invented-claude-model', effort='high')
        self.assertEqual(claude['state'], 'admitted', claude)  # an unrelated pool stays usable
        self.assertEqual(collector.consumed_at(self.settings, CLAUDE_POOL), 0)

    # -- 2: earlier continuation turns ------------------------------------------------------------------------------
    def test_an_earlier_dispatched_turn_marks_before_release_and_the_new_turn_needs_a_newer_reading(self):
        self.ready()
        prepared, _, prepare = self.continued()
        attempt = prepared['attempt_id']
        calls, spy = self.spy()
        with spy, self.reader():
            intent = prepare()
        self.assertEqual(calls, [(CODEX_POOL, attempt, 'dispatched')])
        self.assertEqual(self.hold(attempt), 'released')
        self.assertGreater(collector.consumed_at(self.settings, CODEX_POOL), 0)
        self.assertNotEqual(intent['routing']['state'], 'admitted', intent)  # the same pre-turn reading is stale
        self.assertIsNone(self.find(attempt + '.c1'))

    def test_an_independently_newer_reading_restores_the_continuation_on_the_same_route(self):
        self.ready()
        clock = self.clock()
        prepared, enabled, prepare = self.continued()
        attempt = prepared['attempt_id']
        with self.reader(newer=clock):
            intent = prepare()
        self.assertEqual(intent['routing'], dict(state='admitted', attempt_id=attempt + '.c1'), intent)
        boundary = collector.consumed_at(self.settings, CODEX_POOL)
        self.assertLess(boundary, clock['now'])  # the newer reading was observed after the mark
        first, second = self.find(attempt), self.find(attempt + '.c1')
        self.assertEqual(second['route_identity'], first['route_identity'])
        self.assertEqual(second['ranking']['profile_digest'], first['ranking']['profile_digest'])
        sent = workstreams.consume_continuation(self.scope, self.request(
            attempt_id=attempt, workstream_revision=enabled['revision'], token=intent['token']))
        self.assertTrue(sent['send_admitted'])
        self.assertEqual(sent['worker_id'], 'child')  # the same child: identity and receipts unchanged
        self.assertEqual(self.hold(attempt + '.c1'), 'dispatched')

    # -- 3: a failed mark keeps the capacity held -------------------------------------------------------------------
    def test_a_failed_mark_at_settlement_keeps_the_hold_and_reports_it(self):
        self.ready()
        prepared = self.prepare(dispatch_id='mark-fail')
        attempt = prepared['attempt_id']
        with mock.patch.object(collector, 'mark_consumed', side_effect=OSError('store unwritable')):
            settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                prepared, outcome='cancelled'))
        self.assertEqual(self.raw_assignment(attempt)['status'], 'settled')  # delegation's settlement stands
        self.assertEqual(settled['routing']['closed'], [dict(attempt_id=attempt, kind='complete', state='blocked',
                                                             reason='observation_store_unavailable')])
        self.assertEqual(self.hold(attempt), 'dispatched')  # never released as if it were safe
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), 0)

    def test_a_failed_mark_before_a_continuation_admits_nothing(self):
        self.ready()
        prepared, _, prepare = self.continued()
        attempt = prepared['attempt_id']
        with mock.patch.object(collector, 'mark_consumed', side_effect=OSError('store unwritable')):
            intent = prepare()
        self.assertEqual(intent['routing'], dict(state='blocked', reason='observation_store_unavailable'))
        self.assertEqual(self.hold(attempt), 'dispatched')
        self.assertIsNone(self.find(attempt + '.c1'))  # no fresh permit on top of the held turn

    # -- 4: duplicates and retries ----------------------------------------------------------------------------------
    def test_a_retry_after_a_failed_release_never_moves_the_mark_or_hides_a_newer_reading(self):
        self.ready()
        clock = self.clock()
        prepared = self.prepare(dispatch_id='retry-1')
        attempt = prepared['attempt_id']
        with self.failing_release():
            settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                prepared, outcome='cancelled'))
        self.assertEqual(settled['routing']['closed'], [dict(attempt_id=attempt, kind='complete', state='blocked')])
        self.assertEqual(self.hold(attempt), 'dispatched')  # marked, not released: the capacity stays held
        boundary = collector.consumed_at(self.settings, CODEX_POOL)
        self.assertEqual(boundary, clock['now'])
        clock['now'] += 2
        collector.store_window(self.settings, CODEX_POOL, flows.week_window(clock['now']))  # independently newer
        clock['now'] += 1
        record = delegation_routing._committed(self.scope, attempt)
        # Hook-level crash-ordering proof only: a replay of the same reconcile command never reaches this hook
        # (runtime/delegation.py returns the replay first); the public recovery is the `recover` tests below.
        retried = delegation_routing.settled(self.scope, record, {})
        self.assertEqual(retried['routing']['closed'], [dict(attempt_id=attempt, kind='complete', state='recorded')])
        self.assertEqual(delegation_routing.settled(self.scope, record, {}), {})  # a duplicate closes nothing
        self.assertEqual(self.hold(attempt), 'released')
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), boundary)  # the mark never moved
        self.assertEqual(self.codex_alias('after-retry')['state'], 'admitted')  # the newer reading counts, no read

    def test_a_same_turn_continuation_retry_keeps_the_first_mark(self):
        self.ready()
        clock = self.clock()
        prepared, _, prepare = self.continued()
        attempt = prepared['attempt_id']
        with self.failing_release(), self.reader():
            intent = prepare()
        self.assertNotEqual(intent['routing']['state'], 'admitted', intent)
        self.assertEqual(self.hold(attempt), 'dispatched')
        boundary = collector.consumed_at(self.settings, CODEX_POOL)
        self.assertEqual(boundary, clock['now'])
        record = delegation_routing._committed(self.scope, attempt)
        value = delegation._continuation_intent(self.scope, record)
        clock['now'] += codex_rate_limits.FAILURE_BACKOFF  # past the failed read's backoff
        # Hook-level: `prepare_continuation` reaches this hook again for the same `.c1` attempt only after its own
        # inputs changed (an unchanged re-prepare is refused by delegation); the mark-once ordering is what is proved.
        with self.reader(newer=clock):
            retried = delegation_routing.continuation(self.scope, self.request(), record, value, {})
        self.assertEqual(retried['routing'], dict(state='admitted', attempt_id=attempt + '.c1'), retried)
        self.assertEqual(self.hold(attempt), 'released')
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), boundary)

    # -- 3/4: public recovery of a closure kept held ----------------------------------------------------------------
    def one_shot(self, request):
        """The routing command as the operator runs it (`main()`: the only one-shot service)."""
        stdout = io.BytesIO()
        command.main([request['op'], '--settings', str(self.settings), '--ledger', str(self.ledger)],
                     io.BytesIO(json.dumps(request).encode()), stdout)
        return json.loads(stdout.getvalue())

    def counted(self):
        return subscription_reconcile.held(self.snapshot())  # what admission counts against each window

    def recover(self, request, settle, attempt):
        """A held managed closure through the existing commands only: the reconcile replay changes nothing, no
        launch permit is granted again, `record uncertain` keeps the claims counted, and only the one-shot operator
        `reconcile_subscription` abandon closes the hold, turning its claims into a window debit."""
        boundary, assignment, before = collector.consumed_at(self.settings, CODEX_POOL), \
            self.raw_assignment(attempt), self.snapshot()
        claims = dict(before['holds'][attempt]['windows'])
        self.assertTrue(claims)
        self.assertEqual(self.counted(), claims)
        replay = self.api.reconcile_assignment(self.scope, settle)  # the same command, the same request
        self.assertTrue(replay['replayed'])
        self.assertNotIn('routing', replay)  # it never reaches the routing hook
        self.assertEqual(self.snapshot(), before)
        again = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(again.get('launch_admitted'), again)  # no second permit for the settled assignment
        self.assertEqual(len(self.snapshot()['admissions']), len(before['admissions']))
        abandon = dict(v=1, op='reconcile_subscription', attempt_id=attempt, evidence=dict(kind='abandon'),
                       event_id='abandon-' + attempt[:8])
        with closing(command.managed_service(self.scope.repository_root, scope=self.scope)) as service:
            self.assertEqual(service.handle(json.dumps(abandon).encode())['reason'], 'operator_op_not_served')
        self.assertEqual(self.one_shot(dict(abandon, event_id='early-' + attempt[:8]))['reason'],
                         'hold_state_forbidden')  # a dispatched hold first needs `uncertain`
        uncertain = dict(v=1, op='record', event=dict(event_id='uncertain-' + attempt[:8], attempt_id=attempt,
                                                      kind='uncertain'))
        self.assertEqual(self.one_shot(uncertain)['state'], 'recorded')
        self.assertEqual(self.hold(attempt), 'uncertain')
        self.assertEqual(self.counted(), claims)  # still counted in full
        bare = dict(v=1, op='record', event=dict(event_id='complete-' + attempt[:8], attempt_id=attempt,
                                                 kind='complete'))
        self.assertEqual(self.one_shot(bare)['reason'], 'reconciliation_required')  # no release around the operator
        self.assertEqual(delegation_routing.settled(self.scope, delegation_routing._committed(self.scope, attempt),
                                                    {}), {})  # nothing for the hook to close
        done = self.one_shot(abandon)
        self.assertEqual((done['state'], done['outcome'], done['settlement']['debited']),
                         ('recorded', 'abandoned', claims), done)
        self.assertEqual(self.hold(attempt), 'released')
        self.assertEqual(self.counted(), claims)  # the claims stay counted as a debit until a later fixed window
        self.assertEqual(set(self.snapshot()[subscription_reconcile.DEBITS]), set(claims))
        self.assertTrue(self.one_shot(abandon)['duplicate'])  # the same operator event is idempotent
        self.assertEqual(self.one_shot(dict(abandon, event_id='again-' + attempt[:8]))['reason'], 'attempt_closed')
        self.assertEqual(self.counted(), claims)
        self.assertEqual(self.raw_assignment(attempt), assignment)  # delegation's D5/D6 record is untouched
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), boundary)  # the mark never moved

    def test_a_failed_mark_recovers_through_uncertain_and_the_operator_abandon(self):
        self.ready()
        request = self.prepare_request(dispatch_id='recover-mark')
        prepared = self.api.prepare_assignment(self.scope, request)
        settle = self.worker_reconciliation_request(prepared, outcome='cancelled')
        with mock.patch.object(collector, 'mark_consumed', side_effect=OSError('store unwritable')):
            self.assertEqual(self.api.reconcile_assignment(self.scope, settle)['routing']['closed'][0]['reason'],
                             'observation_store_unavailable')
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), 0)
        self.recover(request, settle, prepared['attempt_id'])

    def test_a_failed_release_after_the_mark_recovers_the_same_way(self):
        self.ready()
        request = self.prepare_request(dispatch_id='recover-record')
        prepared = self.api.prepare_assignment(self.scope, request)
        settle = self.worker_reconciliation_request(prepared, outcome='cancelled')
        with self.failing_release():
            self.assertEqual(self.api.reconcile_assignment(self.scope, settle)['routing']['closed'][0]['state'],
                             'blocked')
        self.assertGreater(collector.consumed_at(self.settings, CODEX_POOL), 0)
        self.recover(request, settle, prepared['attempt_id'])

    # -- 5: unsent and non-subscription holds -----------------------------------------------------------------------
    def test_an_admitted_unsent_hold_marks_nothing(self):
        self.ready()
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        prepared = self.prepare(workstream_revision=enabled['revision'], workstream_limits=LIMITS)
        attempt = prepared['attempt_id']
        self.assertEqual(self.hold(attempt), 'admitted')
        settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
            prepared, outcome='cancelled'))
        self.assertEqual(settled['routing']['closed'], [dict(attempt_id=attempt, kind='not_dispatched',
                                                             state='recorded')])
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), 0)

    def funded_as(self, billing):
        """The ledger snapshot with this test's admission read as another funding class (None: no admission)."""
        real = delegation_routing._holds

        def holds(service):
            data = real(service)
            admissions = [dict(r, billing_class=billing) for r in data.get('admissions', [])] if billing else []
            return dict(data, admissions=admissions)
        return mock.patch.object(delegation_routing, '_holds', holds)

    def test_a_non_subscription_hold_is_released_without_a_mark(self):
        self.ready()
        prepared = self.prepare(dispatch_id='funded')
        with self.funded_as('api_grant'):
            settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                prepared, outcome='cancelled'))
        self.assertEqual(settled['routing']['closed'], [dict(attempt_id=prepared['attempt_id'], kind='complete',
                                                             state='recorded')])
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), 0)

    def test_unknown_funding_keeps_the_hold(self):
        self.ready()
        previous = {}
        for billing in (None, 'invented-class'):
            with self.subTest(billing=billing):
                prepared = self.prepare(dispatch_id='unknown-%s' % billing, **previous)
                previous = dict(retry_of=prepared['attempt_id'], diagnostic=dict(
                    prior_attempt_id=prepared['attempt_id'], cause='routing', evidence=['routing:held'],
                    change='Retry after the held attempt was settled'))
                with self.funded_as(billing):
                    settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                        prepared, outcome='cancelled'))
                self.assertEqual(settled['routing']['closed'], [dict(
                    attempt_id=prepared['attempt_id'], kind='complete', state='blocked', reason='admission_unknown')])
                self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), 0)


def load_tests(loader, standard, pattern):
    """Only this module's own cases: the inherited P2 cases run in their own module."""
    case = ManagedCompletionMarks
    return unittest.TestSuite(case(name) for name in loader.getTestCaseNames(case) if name in vars(case))


if __name__ == '__main__':
    unittest.main()
