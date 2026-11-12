"""C3-4a managed proposals (C.3 design 3.3, 6.3): one routing admission per `begin`, an in-process check at
`dispatch`, every hold closed on every path. Split from test_openrouter_proposals.py by responsibility."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from runtime import openrouter_settings as settings, routing, routing_observations
from scripts import routing as command
from tests.openrouter_fixtures import ManagedRouting
from tests.test_openrouter_proposals import SCRIPT, ProposalCase


class ManagedProposalTests(ManagedRouting, ProposalCase):
    """C3-4a (C.3 design 3.3, 6.3): in a managed project `begin` is one routing admission on the `gateway` route,
    prepared before the OpenRouter admission lock; `dispatch` checks it in process; `record` closes it."""

    def setUp(self):
        super().setUp()
        folder = Path(self.tmp.name).resolve() / 'routing-defaults'
        self.enterContext(patch.object(command, 'default_settings', lambda: folder / 'settings.json'))

    def test_begin_prepares_through_routing_before_the_openrouter_admission(self):
        self.manage()
        self.publish()
        order, real = [], settings.admission

        def admission(*args, **kwargs):
            order.append(('admission', kwargs.get('revision')))
            return real(*args, **kwargs)
        self.prepared(then=lambda answer: order.append(('prepare', answer['state'], answer.get('seen'))))
        with patch.object(settings, 'admission', admission):
            begun = self.call('begin', self.begin_request())
        self.assertIn('attempt_token', begun)
        revision = self.settings['revision']
        self.assertEqual(order, [('prepare', 'admitted', dict(openrouter_revision=revision)), ('admission', revision)])

    def test_a_settings_change_after_prepare_fails_closed_and_closes_the_hold(self):
        self.manage()
        self.publish()
        request = self.begin_request()

        def change(answer):
            disabled = settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
            settings.enable(self.repo, dict(expected_revision=disabled['revision']))
        self.prepared(then=change)
        events, real = [], routing.record_event

        def record(ledger, event, now):
            events.append(event['kind'])
            return real(ledger, event, now)
        with patch.object(routing, 'record_event', record):
            begun = self.call('begin', request)
        self.assertEqual(begun['fallback'], 'openrouter_preparation_stale')
        self.assertEqual(events, ['not_dispatched'])
        self.assertEqual(self.holds(), {})
        self.assertEqual(self.call('history')['records'], [])

    def test_begin_obtains_exactly_one_routing_admission_on_the_gateway_route(self):
        self.manage()
        self.publish()
        calls = self.admissions()
        bodies = self.prepared()
        self.assertIn('attempt_token', self.call('begin', self.begin_request()))
        [(assignment, result)] = calls
        self.assertEqual((result['status'], result['billing_class']), ('admitted', 'gateway'))
        self.assertEqual(assignment['role'], 'explorer-agent')
        self.assertEqual(assignment['choice'], dict(provider='openrouter', model='model/v1', effort='none',
                                                    harness='openrouter-proposal', funding='api', endpoint='p'))
        self.assertNotIn('workflow', bodies[0])
        self.assertNotIn('observations', bodies[0])
        self.assertEqual(self.holds(), {'admitted': 1})
        self.assertIn('routing', self.call('history')['records'][0])

    def test_openrouter_is_a_candidate_only_with_its_own_activation_and_funding(self):
        self.manage()
        self.publish()
        calls = self.admissions()
        request = self.begin_request()
        disabled = settings.disable(self.repo, dict(expected_revision=self.settings['revision']))
        self.assertEqual(self.call('begin', request)['fallback'], 'openrouter_disabled')
        self.assertEqual(calls, [])
        self.settings = settings.enable(self.repo, dict(expected_revision=disabled['revision']))
        self.source(organization='unfunded-organization')
        self.assertEqual(self.call('begin', self.begin_request())['fallback'], 'explicit_choice_unavailable')
        [(_, result)] = calls
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(self.holds(), {})

    def granted(self, **funding):
        """API Usage on and a qualified direct API grant on the gateway's pool label and account (F4: the collector
        builds pools from the request's own routes only, so the grant can only matter if the request selects it).
        Returns the admissions and the collector's snapshots of the next `begin`."""
        self.manage()
        self.publish()
        service = command.Service(self.stores['settings'], self.stores['ledger'])
        try:
            revision = service.handle(b'{"v":1,"op":"status"}')['settings_revision']
            enabled = service.handle(json.dumps(dict(v=1, op='set', expected_revision=revision,
                                                     change=dict(api_usage_enabled=True))).encode())
        finally:
            service.close()
        self.assertEqual(enabled['state'], 'updated', enabled)
        grant = dict(route='direct-grant', status='qualified', provider='openai', model='direct/model', effort='none',
                     harness='api', billing_class='api_grant', pool='fixture-openrouter', account='fixture-openrouter',
                     demand={'credit': 1_000_000})
        self.source(extra=[grant], **funding)
        collected, real = [], routing_observations.collect

        def collect(*args, **kwargs):
            result = real(*args, **kwargs)
            collected.append(result['observations'])
            return result
        self.enterContext(patch.object(routing_observations, 'collect', collect))
        return self.admissions(), collected

    def test_a_direct_api_grant_never_funds_the_gateway(self):
        """The gateway's own funding is unverified (its credit window names an organization the ledger does not
        fund); a funded direct API grant on the same pool label and account, with API Usage on, does not stand in."""
        calls, collected = self.granted(organization='unfunded-organization')
        begun = self.call('begin', self.begin_request())
        self.assertEqual(begun['fallback'], 'explicit_choice_unavailable')
        self.assertNotIn('packet', begun)
        [(_, result)] = calls
        self.assertEqual(result['status'], 'pending')
        [observations] = collected
        [gateway] = [item['route'] for item in observations['excluded']]
        self.assertTrue(gateway.startswith('openrouter:model/v1@p|'), gateway)
        self.assertEqual(observations['excluded'], [dict(route=gateway, reason='funding_unverified')])
        self.assertEqual((observations['routes'], observations['pools']), ([], {}))  # the grant is never a candidate
        self.assertEqual(self.holds(), {})

    def test_a_direct_api_grant_does_not_block_the_gateways_own_funding(self):
        """With the gateway's own organization funded, an unrelated grant sharing its pool label neither funds nor
        blocks it: the admission is the gateway route, reserved on the gateway organization's credit only."""
        calls, collected = self.granted()
        self.assertIn('attempt_token', self.call('begin', self.begin_request()))
        [(_, result)] = calls
        self.assertEqual((result['status'], result['billing_class']), ('admitted', 'gateway'))
        self.assertTrue(result['route'].startswith('openrouter:model/v1@p|'), result['route'])
        self.assertEqual({key for key in result['reserved'] if key.startswith('org:')}, {'org:fixture-openrouter'})
        [observations] = collected
        self.assertEqual([route['billing_class'] for route in observations['routes']], ['gateway'])
        self.assertEqual(observations['pools']['fixture-openrouter']['billing_class'], 'gateway')
        self.assertEqual(self.holds(), {'admitted': 1})

    def test_proposals_gain_no_file_or_tool_permission(self):
        self.manage()
        request = self.scoped_request()
        (self.repo / 'secret.txt').write_text('outside the permission\n')
        bodies = self.prepared()
        begun = self.call('begin', request)
        [body] = bodies
        self.assertEqual((body['job']['tools'], body['assignment']['role']), ([], 'explorer-agent'))
        result = self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))
        self.assertIn('source bytes', result['packet'])
        self.assertNotIn('outside the permission', result['packet'])
        self.assertEqual((result['requires_native_review'], result['accepted']), (True, False))
        self.assertFalse({'tools', 'permission', 'authority', 'files'} & set(result))
        calls = self.admissions()
        part = dict(kind='source', path='secret.txt')
        with self.assertRaisesRegex(ValueError, 'source_denied'):
            self.call('begin', dict(request, task_id='task-2', external=dict(request['external'], parts=[part])))
        self.assertEqual(calls, [])

    def test_an_explicit_choice_narrows_candidates_and_never_widens_them(self):
        self.manage(('model/v1', 'p'), ('model/v2', 'p'))
        request = self.scoped_request()
        calls = self.admissions()
        bodies = self.prepared()
        self.assertIn('attempt_token', self.call('begin', request))
        [body] = bodies
        self.assertEqual(body['assignment']['choice']['model'], 'model/v1')
        self.assertEqual(body['constraints'], dict(allowed_models=['model/v1'], allowed_efforts=['none'],
                                                   allowed_harnesses=['openrouter-proposal']))  # F4.4: its host's
        self.assertTrue(calls[0][1]['route'].startswith('openrouter:model/v1@p|'), calls[0][1]['route'])
        with self.assertRaisesRegex(ValueError, 'destination_denied'):
            self.call('begin', dict(request, task_id='task-2', model='model/v2'))
        self.assertEqual(len(calls), 1)

    def test_dispatch_checks_the_admitted_route_in_process_before_returning_the_packet(self):
        self.manage()
        request = self.scoped_request()
        begun = self.call('begin', request)
        dispatch = dict(request, attempt_token=begun['attempt_token'])
        checks, real = [], routing.check

        def check(data, facts, current, now, **options):
            answer = real(data, facts, current, now, **options)
            checks.append((facts, options, answer))
            return answer
        with patch.object(routing, 'check', check):
            result = self.call('dispatch', dispatch)
            self.assertEqual(self.call('dispatch', dispatch)['fallback'], 'already_dispatched')
        self.assertIn('source bytes', result['packet'])
        [(facts, options, answer)] = checks
        self.assertEqual(answer['state'], 'allow')
        self.assertEqual((facts['model'], facts['harness']), ('model/v1', 'openrouter-proposal'))
        self.assertTrue(options['authority'])
        self.assertNotIn(options['authority'], json.dumps(result) + self.store.read_text())
        self.assertEqual(self.holds(), {'dispatched': 1})

    def test_record_closes_the_routing_hold(self):
        self.manage()
        self.publish()
        begun = self.call('begin', self.begin_request())
        self.assertTrue(self.call('record', dict(attempt_token=begun['attempt_token'], disposition='unresolved'))['recorded'])
        self.assertEqual(self.holds(), {})  # never dispatched: not_dispatched releases it
        self.assertNotIn('usage', json.dumps(self.call('history')['records']))

    def test_record_passes_routings_fixed_refusal_reason_through(self):
        """PR #216 follow-up: a refused settlement answers routing's own reason, never a generic one."""
        self.manage()
        request = self.scoped_request()
        begun = self.call('begin', request)
        self.assertTrue(self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))['dispatched'])
        outcome = dict(attempt_token=begun['attempt_token'], disposition='unresolved')
        for reason in ('ledger_busy', 'invalid_request'):
            with self.subTest(reason=reason), \
                    patch.object(command.Service, '_record', return_value=dict(state='blocked', reason=reason)):
                self.assertEqual(self.call('record', outcome)['fallback'], reason)
        [row] = self.call('history')['records']
        uncertain = self.ledger(dict(v=1, op='record', event=dict(event_id='uncertain:x', kind='uncertain',
                                                                  attempt_id=row['routing']['attempt_id'])))
        self.assertEqual(uncertain['state'], 'recorded', uncertain)
        self.assertEqual(self.call('record', outcome)['fallback'], 'reconciliation_required')
        self.assertNotIn('outcome', self.call('history')['records'][0])  # nothing stored while routing refuses

    def test_a_self_reported_cost_is_charged_the_full_reservation(self):
        self.manage()
        self.publish()
        answer, reserved = self.settled(1)
        self.assertEqual(answer['outcome'], 'complete')
        settlement = answer['settlement']
        self.assertEqual((settlement['charged'], settlement['released'], settlement['overrun']), (reserved, 0, 0))
        self.assertEqual(settlement['cost_source'], 'estimate')  # never recorded as a provider receipt
        self.assertEqual(self.holds(), {})

    def test_missing_usage_leaves_the_cost_unknown(self):
        self.manage()
        self.publish()
        answer, _ = self.settled(None)
        self.assertEqual(answer['outcome'], 'cost_unknown')
        self.assertEqual(self.holds(), {'cost_unknown': 1})
        # An unknown dollar cost keeps its exposure and stops new dollar launches until the operator resolves it.
        self.assertEqual(self.call('begin', self.begin_request('task-4'))['fallback'], 'ledger_reconcile_pending')

    def test_a_reported_cost_above_the_reservation_is_an_unconfirmed_overrun(self):
        self.manage()
        self.publish()
        answer, reserved = self.settled(10**9)
        self.assertEqual(answer['outcome'], 'overrun')
        settlement = answer['settlement']
        self.assertEqual((settlement['charged'], settlement['overrun'], settlement['overrun_state']),
                         (reserved, 10**9 - reserved, 'unconfirmed'))
        blocked = self.ledger()['blocked_organizations']
        self.assertEqual([(item['id'], sorted(item['reasons'])) for item in blocked],
                         [('fixture-openrouter', ['overrun'])])  # until the operator's one-shot resolve_overrun
        self.assertEqual(self.call('begin', self.begin_request('task-4'))['fallback'], 'ledger_reconcile_pending')

    def test_local_refusals_are_checked_before_routing(self):
        self.manage()
        self.publish()
        calls = self.admissions()
        self.assertEqual(self.call('begin', dict(self.begin_request(), snapshot_digest='old'))['fallback'],
                         'stale_discovery')
        begun = self.call('begin', self.begin_request())
        self.assertEqual(self.call('begin', self.begin_request('task-2'))['fallback'], 'pending_attempt')
        self.call('record', dict(attempt_token=begun['attempt_token'], disposition='unresolved'))
        self.assertEqual(self.call('begin', self.begin_request())['fallback'], 'task_already_attempted')
        self.assertEqual(len(calls), 1)  # only the one begin that passed every local check reached routing
        self.assertEqual(self.holds(), {})

    def test_a_crash_after_prepare_leaves_a_pending_attempt_that_recovery_closes(self):
        self.manage()
        self.publish()
        events = self.recorded()
        with patch.object(self.api, '_release', lambda *args, **kwargs: None), \
                patch.object(settings, 'admission', side_effect=KeyboardInterrupt):  # the process dies here
            with self.assertRaises(KeyboardInterrupt):
                self.call('begin', self.begin_request())
        [pending] = json.loads(self.store.read_text())['routing_pending']
        self.assertEqual(self.holds(), {'admitted': 1})
        self.assertEqual(self.call('begin', self.begin_request())['fallback'], 'pending_attempt')  # within the lease
        begun = self.call('begin', self.begin_request(), now=100 + self.api.LEASE + 1)
        self.assertIn('attempt_token', begun)
        self.assertIn((pending['attempt_id'], 'not_dispatched'),
                      [(event['attempt_id'], event['kind']) for event, _ in events])
        self.assertNotIn('routing_pending', json.loads(self.store.read_text()))
        self.assertEqual(self.holds(), {'admitted': 1})  # the new attempt's hold only

    def test_dispatch_reruns_the_dollar_preflight_and_closes_the_hold(self):
        self.manage()
        request = self.scoped_request()
        begun = self.call('begin', request)
        real = command.Service._status

        def status(service, body):
            answer = real(service, body)
            answer['ledger']['reconcile_hint'] = 'reconcile_uncertain'
            return answer
        with patch.object(command.Service, '_status', status):
            refused = self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))
        self.assertEqual(refused['fallback'], 'ledger_reconcile_pending')
        self.assertNotIn('packet', refused)
        self.assertEqual(self.holds(), {})
        self.assertNotIn('dispatched_at', self.call('history')['records'][0])

    def test_a_failure_after_the_routing_dispatch_keeps_the_exposure(self):
        self.manage()
        request = self.scoped_request()
        begun = self.call('begin', request)
        dispatch = dict(request, attempt_token=begun['attempt_token'])
        with patch.object(routing, 'check', return_value=dict(state='blocked', reason='route_changed')):
            self.assertEqual(self.call('dispatch', dispatch)['fallback'], 'route_changed')
        self.assertEqual(self.holds(), {'cost_unknown': 1})  # nothing was sent, but dispatch is never undone

    def test_a_store_capacity_failure_never_strands_a_dispatched_hold(self):
        self.manage()
        request = self.scoped_request()
        begun = self.call('begin', request)
        dispatches = []
        real = command.Service._dispatch
        with patch.object(self.api, 'MAX_STATE', len(self.store.read_bytes()) + 4), \
                patch.object(command.Service, '_dispatch', lambda service, body: dispatches.append(body) or
                             real(service, body)):
            self.assertEqual(self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))['fallback'],
                             'capacity')
        self.assertEqual((dispatches, self.holds()), ([], {'admitted': 1}))  # refused before the routing dispatch
        self.call('record', dict(attempt_token=begun['attempt_token'], disposition='unresolved'))
        self.assertEqual(self.holds(), {})

    def test_a_check_refusal_after_the_routing_dispatch_never_sticks_the_store(self):
        refused = patch.object(routing, 'check', return_value=dict(state='blocked', reason='route_changed'))
        self.assertEqual(self.unsent(refused)['fallback'], 'route_changed')
        self.assert_recovered()

    def test_an_exception_after_the_routing_dispatch_never_sticks_the_store(self):
        writes, real = [], os.replace
        def replace(source, target, *args, **kwargs):  # noqa: E306 - the store write after the routing dispatch fails
            writes.append(target)
            if Path(target) == self.store and writes.count(target) == 2:
                raise OSError('disk full')
            return real(source, target, *args, **kwargs)
        self.assertEqual(self.unsent(patch.object(os, 'replace', replace)), dict(raised='ValueError'))
        self.assert_recovered()

    def test_a_kill_around_the_routing_dispatch_is_recovered(self):
        with patch.object(self.api, '_unsent', lambda *args, **kwargs: None):  # the process dies after the dispatch
            self.assertEqual(self.unsent(patch.object(routing, 'check', side_effect=KeyboardInterrupt)), dict(raised='KeyboardInterrupt'))
        self.assertEqual((self.call('history')['records'][0]['routing_state'], self.holds()),
                         ('dispatching', {'dispatched': 1}))
        self.assertEqual(self.call('begin', self.begin_request('task-3'))['fallback'], 'ledger_reconcile_pending')
        self.assert_recovered()

    def test_a_kill_before_the_routing_dispatch_releases_the_hold_on_record(self):
        killed = patch.object(command.Service, '_dispatch', side_effect=KeyboardInterrupt)
        self.assertEqual(self.unsent(killed), dict(raised='KeyboardInterrupt'))
        self.assertEqual(self.holds(), {'admitted': 1})
        [row] = self.call('history')['records']
        self.assertTrue(self.call('record', dict(attempt_token=row['attempt_token'], disposition='unresolved'))['recorded'])
        self.assert_recovered('unresolved', holds={})

    def test_begin_refuses_when_recovery_closed_its_own_reservation(self):
        self.manage()
        self.publish()
        recover = lambda state, moment: self.api._recover(state, moment) or {}  # noqa: E731 - after a long sleep
        self.prepared(then=lambda answer: self.api._run('begin', self.store, {}, now=221, hook=recover))
        self.assertEqual(self.call('begin', self.begin_request())['fallback'], 'routing_reservation_lost')
        self.assertEqual((self.holds(), self.call('history')['records']), ({}, []))

    def test_an_unmanaged_project_never_reaches_routing(self):
        self.publish()
        request = self.begin_request()
        with patch.object(command, 'managed_service', side_effect=AssertionError('routing reached')), \
                patch.object(routing, 'admit', side_effect=AssertionError('routing reached')):
            begun = self.call('begin', request)
            self.assertTrue(self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))['dispatched'])
            self.assertTrue(self.call('record', dict(attempt_token=begun['attempt_token'],
                                                     disposition='rejected'))['recorded'])
        self.assertNotIn('routing', self.call('history')['records'][0])

    def test_a_managed_project_never_dispatches_an_attempt_begun_outside_routing(self):
        self.publish()
        request = self.begin_request()
        begun = self.call('begin', request)
        self.manage()
        self.assertEqual(self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))['fallback'],
                         'legacy_route_retired')
        self.assertNotIn('dispatched_at', self.call('history')['records'][0])

    def test_managed_self_check_admits_one_gateway_proposal_in_a_sandbox(self):
        outside = tempfile.TemporaryDirectory()  # self.tmp holds this class's patched default routing state
        self.addCleanup(outside.cleanup)
        self.assertEqual(self.api.managed_self_check(Path(outside.name).resolve() / 'self-check'), [])
        self.assertEqual(command._self_check('script', 'scripts/openrouter_proposals.py', Path(outside.name) / 'settings.json'), [])

    def test_the_self_check_flag_refuses_an_unsafe_workdir(self):
        crowded = Path(self.tmp.name).resolve() / 'crowded'
        crowded.mkdir(mode=0o700)
        (crowded / 'file').write_text('x')
        for unsafe in (crowded, self.repo / 'inside-repository'):
            with self.subTest(workdir=unsafe):
                done = subprocess.run([sys.executable, '-I', '-B', str(SCRIPT), '--managed-self-check', str(unsafe)],
                                      capture_output=True, text=True, timeout=30)
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(json.loads(done.stdout), dict(codes=['self_check_workdir_unsafe']))
                self.assertEqual(self.api.managed_self_check(unsafe), ['self_check_workdir_unsafe'])
        self.assertFalse((self.repo / 'inside-repository').exists())  # the parent creates nothing either
        self.assertEqual(sorted(path.name for path in crowded.iterdir()), ['file'])


if __name__ == '__main__':
    unittest.main()
