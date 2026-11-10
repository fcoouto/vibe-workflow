"""C3-2: native delegation and review through Smart Routing `prepare` in a managed project (C.3 design 6.3).

Synthetic native catalog and the C3-1 `managed_project_fixture` (temporary routing stores, a static route source);
no model, network, host or secret-store call, and no real user state.
"""
from contextlib import closing, contextmanager
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime import delegation, delegation_routing, evidence, routing, routing_gates, routing_launch, \
    routing_observations, state
from scripts import routing as command
from tests import test_smart_routing_features as features
from tests.fixture_delegation import AdmissionFixture

NATIVE = features.fixture_route('native:new-native-model#high', 'included_subscription')
OTHER = features.fixture_route('native:other-native-model#high', 'included_subscription')


class ManagedDelegation(AdmissionFixture, unittest.TestCase):
    planned = True

    def setUp(self):
        super().setUp()
        if self.planned:
            self.plan()

    @contextmanager
    def managed(self, *routes, windows=None):
        with features.managed_project_fixture(self, self.repo, routes=routes) as stores:
            self.stores = stores
            if windows is not None:  # a mutable window override: (pool, now) -> windows or None for the fixture's
                source = routing_observations.default_source()
                original = source._windows

                def current(pool, now):
                    found = windows(pool, now)
                    return original(pool, now) if found is None else found
                source._windows = current
            yield stores

    def service(self):
        return command.Service(self.stores['settings'], self.stores['ledger'])

    def snapshot(self):
        with closing(self.service()) as service:
            return service._ledger(False).snapshot()

    def admission(self, attempt_id):
        return next((r for r in self.snapshot()['admissions'] if r['attempt_id'] == attempt_id), None)

    def hold(self, attempt_id):
        found = self.snapshot()['holds'].get(attempt_id)
        return None if found is None else found['state']

    def worker_choices(self, *identities):
        with closing(self.service()) as service:
            revision = service.handle(json.dumps(dict(v=1, op='status')).encode())['settings_revision']
            choices = [dict(identity, funding='subscription') for identity in identities]
            answer = service.handle(json.dumps(dict(v=1, op='set', expected_revision=revision, change=dict(
                worker_choices=dict(implementer=choices, reviewer=choices)))).encode())
        self.assertEqual(answer['state'], 'updated', answer)

    @staticmethod
    def retry(prepared):
        return dict(retry_of=prepared['attempt_id'], diagnostic=dict(prior_attempt_id=prepared['attempt_id'],
            cause='routing', evidence=['routing:released'], change='Retry after routing released the attempt'))

    @staticmethod
    def native(model='new-native-model', effort='high'):
        return routing_launch.route_identity('codex_native', model=model, effort=effort)

    def routed_request(self, **updates):
        request = self.prepare_request(**updates)
        request['selection'] = dict(rationale='Routing chooses the worker', profile_ids=['bounded'], mode='routed')
        return request

    def inherit_request(self, **updates):
        request = self.prepare_request(**updates)
        request['selection'] = dict(request['selection'], mode='inherit', inheritance=dict(
            model='new-native-model', effort='high', reference='codex:documented-inheritance'))
        return request


class UnmanagedProjects(ManagedDelegation):
    def test_an_unmanaged_project_keeps_todays_selection_behaviour(self):
        with self.assertRaisesRegex(state.StateError, 'Invalid task control fields|Unsupported worker settings mode'):
            self.api.prepare_assignment(self.scope, self.routed_request(dispatch_id='routed-unmanaged'))
        with features.counted_admissions() as admit:
            prepared = self.prepare()
        self.assertTrue(prepared['launch_admitted'])
        self.assertEqual((prepared['requested_model'], prepared['requested_effort']), ('new-native-model', 'high'))
        self.assertNotIn('routing', prepared)
        self.assertNotIn('route', prepared)
        self.assertEqual(admit.count, 0)


class ManagedSelectionModes(ManagedDelegation):
    def test_an_explicit_choice_is_admitted_once_bound_to_its_job_and_attempt(self):
        with self.managed(NATIVE), features.counted_admissions() as admit:
            prepared = self.prepare()
        self.assertTrue(prepared['launch_admitted'], prepared)
        self.assertEqual(admit.count, 1)
        self.assertEqual(prepared['route'], dict(model='new-native-model', effort='high',
                                                 attempt_id=prepared['attempt_id']))
        self.assertEqual(prepared['routing']['state'], 'admitted')
        self.assertIsNone(prepared['requested_model'])  # the routing admission is the only record of the model
        self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')  # the last pre-spawn step ran
        self.assertFalse({'authority', 'session_id', 'message_id'} & (set(prepared) | set(prepared['routing'])))
        binding = self.admission(prepared['attempt_id'])['binding']['job']
        self.assertEqual((binding['locator']['job_id'], binding['locator']['attempt_id']),
                         ('edit', prepared['attempt_id']))
        self.assertEqual(delegation_routing.admitted_route(self.scope, prepared['attempt_id'])['model'],
                         'new-native-model')

    def test_explicit_and_inherit_never_get_a_substituted_route(self):
        with self.managed(OTHER):  # only another native model is qualified, and worker_choices list it
            self.worker_choices(self.native('other-native-model'))
            previous = {}
            for name, build in (('explicit', self.prepare_request), ('inherit', self.inherit_request)):
                with self.subTest(mode=name):
                    request = build(dispatch_id=name + '-1', **previous)
                    prepared = self.api.prepare_assignment(self.scope, request)
                    previous = self.retry(prepared)
                    self.assertFalse(prepared['launch_admitted'])
                    self.assertEqual(prepared['routing'], dict(state='pending', reason='explicit_choice_unavailable'))
                    self.assertNotIn('route', prepared)
                    self.assertIsNone(self.admission(prepared['attempt_id']))
                    self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'prepared')
                    delegation_routing.release(self.scope, request, prepared['attempt_id'], 'routing_pending')

    def test_inherit_is_admitted_on_the_inherited_route(self):
        with self.managed(NATIVE):
            prepared = self.api.prepare_assignment(self.scope, self.inherit_request())
        self.assertTrue(prepared['launch_admitted'], prepared)
        self.assertEqual(self.admission(prepared['attempt_id'])['route_identity'],
                         routing.route_identity(*self.native().values()))

    def test_routed_mode_lets_routing_choose(self):
        with self.managed(OTHER, NATIVE):
            self.worker_choices(self.native())
            prepared = self.api.prepare_assignment(self.scope, self.routed_request())
        self.assertTrue(prepared['launch_admitted'], prepared)
        self.assertEqual(prepared['route']['model'], 'new-native-model')
        self.assertIsNone(prepared['requested_model'])

    def test_pending_then_admitted_on_a_replay_grants_exactly_one_permit(self):
        spent = dict(value=True)

        def windows(pool, now):
            if pool == 'fixture-codex-subscription' and spent['value']:
                return [dict(id='5h', limit=1000, used=1000, length=18000, reset_at=now + 3600, rolling=False,
                             observed_at=now)]
            return None
        request = self.prepare_request()
        with self.managed(NATIVE, windows=windows):
            waiting = self.api.prepare_assignment(self.scope, request)
            self.assertFalse(waiting['launch_admitted'])
            self.assertEqual(waiting['routing']['state'], 'pending')
            spent['value'] = False
            granted = self.api.prepare_assignment(self.scope, request)
            again = self.api.prepare_assignment(self.scope, request)
        self.assertEqual(granted['attempt_id'], waiting['attempt_id'])
        self.assertTrue(granted['launch_admitted'], granted)
        self.assertFalse(again['launch_admitted'])
        self.assertTrue(again['routing']['duplicate'])
        self.assertEqual([waiting['launch_admitted'], granted['launch_admitted'], again['launch_admitted']].count(True), 1)

    def changed_workflow(self, failures):
        original, raised = routing_gates.D6Workflow.view, []

        def view(reader, locator):
            if len(raised) < failures:
                raised.append(locator)
                raise routing_gates.WorkflowChanged('raced')
            return original(reader, locator)
        with self.managed(NATIVE), mock.patch.object(routing_gates.D6Workflow, 'view', view):
            return self.prepare(), raised

    def test_a_workflow_change_is_retried_once(self):
        prepared, raised = self.changed_workflow(1)
        self.assertEqual(len(raised), 1)
        self.assertEqual(prepared['routing']['state'], 'admitted', prepared)
        self.assertTrue(prepared['launch_admitted'])

    def test_a_repeated_workflow_change_is_reported_and_launches_nothing(self):
        prepared, raised = self.changed_workflow(2)
        self.assertEqual(len(raised), 2)
        self.assertEqual(prepared['routing'], dict(state='blocked', reason='workflow_changed'))
        self.assertFalse(prepared['launch_admitted'])
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'prepared')  # released explicitly


class ManagedRelease(ManagedDelegation):
    def test_a_routed_catalog_mismatch_releases_the_never_launched_assignment(self):
        with self.managed(NATIVE):  # worker_choices list the fixture CHOICE first: an anthropic route Codex lacks
            prepared = self.api.prepare_assignment(self.scope, self.routed_request())
            self.assertFalse(prepared['launch_admitted'])
            self.assertEqual(prepared['routing'], dict(state='blocked', reason='catalog_mismatch'))
            self.assertEqual(self.hold(prepared['attempt_id']), 'released')  # `not_dispatched` closed the hold
            record = self.raw_assignment(prepared['attempt_id'])
            self.assertEqual((record['status'], record['result']), ('settled', dict(outcome='cancelled')))
            self.assertEqual(prepared['release']['status'], 'settled')
            self.worker_choices(self.native())
            again = self.api.prepare_assignment(self.scope, self.routed_request(
                dispatch_id='request-2', **self.retry(prepared)))
        self.assertTrue(again['launch_admitted'], again)  # claims and resource units were free again

    def test_a_replay_mismatch_closes_the_hold_and_leaves_release_to_the_coordinator(self):
        spent = dict(value=True)

        def windows(pool, now):
            if spent['value']:
                return [dict(id='5h', limit=1000, used=1000, length=18000, reset_at=now + 3600, rolling=False,
                             observed_at=now)]
            return None
        request = self.routed_request()
        with self.managed(NATIVE, windows=windows):  # worker_choices list the fixture CHOICE first
            waiting = self.api.prepare_assignment(self.scope, request)
            self.assertEqual(waiting['routing']['state'], 'pending', waiting)
            spent['value'] = False
            replay = self.api.prepare_assignment(self.scope, request)
            self.assertEqual(replay['routing'], dict(state='blocked', reason='catalog_mismatch',
                                                     reconcile_required=True))
            self.assertFalse(replay['launch_admitted'])
            self.assertNotIn('release', replay)  # a replay never asserts that nothing was launched
            self.assertEqual(self.hold(replay['attempt_id']), 'released')
            self.assertEqual(self.raw_assignment(replay['attempt_id'])['status'], 'prepared')

    def replay_after_pending(self, **patches):
        spent = dict(value=True)

        def windows(pool, now):
            if spent['value']:
                return [dict(id='5h', limit=1000, used=1000, length=18000, reset_at=now + 3600, rolling=False,
                             observed_at=now)]
            return None
        request = self.routed_request()
        with self.managed(NATIVE, windows=windows):
            self.worker_choices(self.native())
            waiting = self.api.prepare_assignment(self.scope, request)
            self.assertEqual(waiting['routing']['state'], 'pending', waiting)
            spent['value'] = False
            with mock.patch.multiple(*patches['target'], **patches['values']):
                replay = self.api.prepare_assignment(self.scope, request)
            return replay, self.hold(replay['attempt_id'])

    def test_a_replay_refused_at_dispatch_asks_for_reconciliation(self):
        # PR #221 review: the refusal recorded `not_dispatched`, so the coordinator reconciles rather than retries.
        refused = mock.Mock(return_value=dict(state='blocked', reason='ledger_busy'))
        replay, hold = self.replay_after_pending(target=(command.Service,), values=dict(_dispatch=refused))
        self.assertEqual(replay['routing'], dict(state='blocked', reason='ledger_busy', reconcile_required=True))
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(hold, 'released')

    def test_a_replay_whose_admission_is_blocked_is_left_to_retry(self):
        blocked = mock.Mock(return_value=dict(state='blocked', reason='ledger_busy'))
        replay, hold = self.replay_after_pending(target=(delegation_routing,), values=dict(admit=blocked))
        self.assertEqual(replay['routing'], dict(state='blocked', reason='ledger_busy'))
        self.assertFalse(replay['launch_admitted'])
        self.assertIsNone(hold)

    def test_an_advisory_host_never_gets_a_dollar_route(self):
        gateway = dict(state='admitted', duplicate=False, admission=dict(
            attempt_id='x', route='gateway-route', billing_class='gateway', pool='p', mode='worker_choice',
            route_identity=self.native()))
        dispatched = []
        with self.managed(NATIVE), mock.patch.object(delegation_routing, 'admit', return_value=gateway), \
                mock.patch.object(command.Service, '_dispatch', side_effect=lambda body: dispatched.append(body)):
            prepared = self.prepare()
        self.assertFalse(prepared['launch_admitted'])
        self.assertEqual(prepared['routing'], dict(state='blocked', reason='dollar_route_needs_enforced_host'))
        self.assertEqual(dispatched, [])
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')

    def test_reconcile_closes_a_dispatched_hold_the_coordinator_never_spawned(self):
        with self.managed(NATIVE):
            prepared = self.prepare()
            self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')
            settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                prepared, outcome='cancelled'))
            self.assertEqual(settled['routing']['closed'], [dict(attempt_id=prepared['attempt_id'], kind='complete',
                                                                 state='recorded')])
            self.assertEqual(self.hold(prepared['attempt_id']), 'released')  # a window-only hold releases its claims

    def test_an_unavailable_routing_ledger_never_undoes_a_committed_reconciliation(self):
        with self.managed(NATIVE):
            prepared = self.prepare()
            with mock.patch.object(command.Service, '_ledger', side_effect=routing.BudgetError('ledger_busy')):
                settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                    prepared, outcome='cancelled'))
            self.assertEqual(settled['routing'], dict(state='unavailable', reason='ledger_unavailable'))
            self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'settled')
            self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')  # closed later, never guessed


class ManagedGraph(ManagedDelegation):
    planned = False

    def test_one_task_graph_each_job_binds_its_own_canonical_job(self):
        value = self.plan_value(jobs=[dict(id='edit', role='worker', claims=[self.path_claim()], dependencies=[],
                                           max_attempts=3),
                                      dict(id='docs', role='worker', claims=[self.path_claim('docs.md')],
                                           dependencies=[], max_attempts=3)])
        self.plan(value)
        (self.repo / 'docs.md').write_text('docs\n')
        with self.managed(NATIVE):
            first = self.prepare()
            docs = self.prepare_request(dispatch_id='docs-1', job_id='docs')
            docs['brief'].update(resources=[self.path_claim('docs.md')], files=['docs.md'])
            second = self.api.prepare_assignment(self.scope, docs)
            body = json.dumps(self.snapshot())
        for prepared, job_id in ((first, 'edit'), (second, 'docs')):
            self.assertTrue(prepared['launch_admitted'], prepared)
            locator = self.admission(prepared['attempt_id'])['binding']['job']['locator']
            self.assertEqual((locator['job_id'], locator['attempt_id']), (job_id, prepared['attempt_id']))
        self.assertNotIn('Implement bounded edit', body)  # the ledger keeps digests, never the brief or graph


class ManagedGates(ManagedDelegation):
    def test_resource_and_funding_gates_bind_the_same_job_revision(self):
        with self.managed(NATIVE):
            prepared = self.prepare()
            stored = self.admission(prepared['attempt_id'])['binding']['job']
            locator = dict(stored['locator'])
            watch = delegation.routing_watch(self.scope)
            try:
                view = delegation.routing_view(self.scope, locator, watch)
            finally:
                watch.close()
        self.assertEqual(stored['revision'], view['job']['revision'])
        self.assertEqual(stored['resource'], view['resource']['marker'])  # the bound assignment's resource units
        self.assertIsNotNone(self.admission(prepared['attempt_id'])['pool'])

    def test_a_dependency_reopened_after_prepare_sends_nothing(self):
        from runtime import workstreams
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        limits = dict(context_id='WS42.01', rationale='One bounded outcome', output_tokens=12000, context_tokens=64000,
                      max_steps=40, max_seconds=1200, max_continuations=3, backoff_seconds=0)
        with self.managed(NATIVE):
            prepared = self.prepare(workstream_revision=enabled['revision'], workstream_limits=limits)
            self.assertTrue(prepared['dispatch_prepared'], prepared)
            self.assertEqual(self.hold(prepared['attempt_id']), 'admitted')  # dispatch waits for consume_dispatch
            with mock.patch.object(delegation, 'validate_dependencies',
                                   side_effect=state.StateError('reopened')):  # a prerequisite reopened
                with self.assertRaisesRegex(state.StateError, 'dependency_reopened'):
                    workstreams.consume_dispatch(self.scope, self.request(attempt_id=prepared['attempt_id'],
                                                                          workstream_revision=enabled['revision']))
            self.assertEqual(self.hold(prepared['attempt_id']), 'admitted')  # nothing was dispatched or sent
            self.assertNotIn('workstream_dispatch', self.raw_assignment(prepared['attempt_id']))
            sent = workstreams.consume_dispatch(self.scope, self.request(attempt_id=prepared['attempt_id'],
                                                                         workstream_revision=enabled['revision']))
            self.assertTrue(sent['send_admitted'])
            self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')

    def test_an_unchanged_continuation_reuses_its_child_and_profile_with_a_new_admission(self):
        from runtime import workstreams
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        limits = dict(context_id='WS42.01', rationale='One bounded outcome', output_tokens=12000, context_tokens=64000,
                      max_steps=40, max_seconds=1200, max_continuations=3, backoff_seconds=0)
        with self.managed(NATIVE):
            prepared = self.prepare(workstream_revision=enabled['revision'], workstream_limits=limits)
            workstreams.consume_dispatch(self.scope, self.request(attempt_id=prepared['attempt_id'],
                                                                  workstream_revision=enabled['revision']))
            self.observe_worker(prepared)
            proof = self.worker_reconciliation_request(prepared, outcome='failed')
            intent = workstreams.prepare_continuation(self.scope, self.request(
                attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision'], reason='follow-up',
                prompt='Finish the original accepted scope', human_continue=False, host_proof=proof['host_proof']))
            self.assertEqual(intent['routing']['state'], 'admitted', intent)
            fresh = intent['routing']['attempt_id']
            self.assertEqual(self.hold(prepared['attempt_id']), 'released')  # the previous turn completed
            first, second = self.admission(prepared['attempt_id']), self.admission(fresh)
            self.assertNotEqual(fresh, prepared['attempt_id'])
            self.assertEqual(first['ranking']['profile_digest'], second['ranking']['profile_digest'])
            self.assertEqual(second['route_identity'], first['route_identity'])
            sent = workstreams.consume_continuation(self.scope, self.request(
                attempt_id=prepared['attempt_id'], workstream_revision=enabled['revision'], token=intent['token']))
            self.assertTrue(sent['send_admitted'])
            self.assertEqual(sent['worker_id'], 'child')
            self.assertEqual(self.hold(fresh), 'dispatched')


class ManagedReview(ManagedDelegation):
    planned = False

    def test_review_admission_goes_through_prepare_with_the_reviewer_role(self):
        self.plan(self.plan_value(jobs=[dict(id='review', role='review', claims=[self.path_claim(mode='read')],
                                             dependencies=[], max_attempts=3)]))
        request = self.prepare_request(job_id='review', dispatch_id='review-dispatch')
        request.pop('brief')
        request |= dict(coverage=['general'], criteria=['works'], assignment='Review app.py read-only.')
        with self.managed(NATIVE), features.counted_admissions() as admit:
            prepared = evidence.prepare_review(self.scope, request)
        self.assertTrue(prepared['launch_admitted'], {k: prepared.get(k) for k in ('routing', 'launch_admitted')})
        self.assertEqual(admit.count, 1)
        (assignment, *_), kwargs = admit.calls[0]
        self.assertEqual(assignment['role'], 'reviewer')
        self.assertEqual(kwargs['workflow']['locator']['job_id'], 'review')



class ReviewS1DoubleLaunch(ManagedDelegation):
    """PR #217 review S1: an assignment committed while the project was unmanaged never gets a second permit."""

    def settings_path(self):
        settings = Path(tempfile.mkdtemp()).resolve() / 'routing' / 'settings.json'
        self.addCleanup(shutil.rmtree, settings.parents[1])
        return settings

    def activate(self, settings):
        with closing(command.Service(settings, Path(settings).parent / 'ledger.sqlite3', one_shot=True)) as service:
            revision = service.handle(json.dumps(dict(v=1, op='status')).encode())['settings_revision'] or 0
            return service.handle(json.dumps(dict(v=1, op='set', expected_revision=revision, change=dict(
                managed_projects=[str(self.repo)]))).encode())

    def test_a_replay_after_the_project_became_managed_gets_no_second_permit(self):
        request = self.prepare_request()
        first = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(first['launch_admitted'])
        with self.managed(NATIVE), features.counted_admissions() as admit:
            replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(replay['routing'], dict(state='blocked', reason='legacy_route_retired'))
        self.assertEqual(admit.count, 0)

    def test_a_project_managed_during_prepare_gets_no_second_permit(self):
        request = self.inherit_request()  # inherit carries no pre-managed marker
        with self.managed(NATIVE):
            real, reads = delegation_routing.managed, []

            def managed(scope):  # the project becomes managed after the call's one read, before the reservation
                reads.append(scope)
                return False if len(reads) == 1 else real(scope)
            with mock.patch.object(delegation_routing, 'managed', managed):
                first = self.api.prepare_assignment(self.scope, request)
            self.assertFalse(first['launch_admitted'])
            self.assertEqual(first['routing'], dict(state='blocked', reason='managed_during_prepare'))
            self.assertEqual(self.raw_assignment(first['attempt_id'])['status'], 'settled')
            replay = self.api.prepare_assignment(self.scope, request)
        self.assertFalse(replay['launch_admitted'])
        self.assertEqual(self.raw_assignment(first['attempt_id'])['result'], dict(outcome='cancelled'))

    def test_activation_is_refused_with_an_open_unrouted_assignment_and_allowed_after_reconcile(self):
        settings = self.settings_path()
        prepared = self.prepare(**self.inherit_request())
        self.assertTrue(prepared['launch_admitted'])
        with mock.patch.object(command, 'activation_gate', return_value=[]), \
                mock.patch.object(delegation_routing, '_scopes_for', return_value=[self.scope]):
            self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), [prepared['attempt_id']])
            refused = self.activate(settings)
            self.assertEqual((refused['state'], refused['reason']), ('blocked', 'open_unrouted_assignments'))
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, outcome='cancelled'))
            self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), [])
            allowed = self.activate(settings)
        self.assertEqual(allowed['state'], 'updated', allowed)
        self.assertEqual(allowed['settings']['managed_projects'], [str(Path(self.repo).resolve())])

    def test_open_unrouted_assignments_fails_closed(self):
        from runtime.startup import RuntimeScopeError
        with mock.patch.object(delegation_routing, '_scopes_for', side_effect=RuntimeScopeError('qualification')):
            self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), ['scope_unavailable'])
        with mock.patch.object(delegation_routing, '_scopes_for', return_value=[]):  # not enrolled: no native state
            self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), [])

    def test_an_assignment_committed_during_activation_rolls_the_write_back(self):
        # PR #217 follow-up: the gate passes, an inherit assignment is committed before the settings write, and the
        # re-check after the write removes the project again before refusing.
        settings, committed = self.settings_path(), []

        def gate():
            committed.append(self.prepare(**self.inherit_request()))
            return []
        with mock.patch.object(command, 'activation_gate', side_effect=gate), \
                mock.patch.object(delegation_routing, '_scopes_for', return_value=[self.scope]):
            refused = self.activate(settings)
        self.assertTrue(committed[0]['launch_admitted'])
        self.assertEqual((refused['state'], refused['reason']), ('blocked', 'open_unrouted_assignments'))
        stored = json.loads(settings.read_bytes())
        self.assertEqual((stored['revision'], stored['values']['managed_projects']), (2, []))

    def test_the_rollback_keeps_a_concurrent_settings_change(self):
        settings, real = self.settings_path(), delegation_routing.open_unrouted_assignments
        prepared = self.prepare(**self.inherit_request())
        calls = []

        def check(checkout):  # clear at the gate; after the write another writer changes the settings first
            calls.append(checkout)
            if len(calls) == 1:
                return []
            with closing(command.Service(settings, settings.parent / 'ledger.sqlite3')) as other:
                revision = other.handle(json.dumps(dict(v=1, op='status')).encode())['settings_revision']
                answer = other.handle(json.dumps(dict(v=1, op='set', expected_revision=revision, change=dict(
                    L=0.6))).encode())
            self.assertEqual(answer['state'], 'updated', answer)
            return real(checkout)
        with mock.patch.object(command, 'activation_gate', return_value=[]), \
                mock.patch.object(delegation_routing, 'open_unrouted_assignments', side_effect=check), \
                mock.patch.object(delegation_routing, '_scopes_for', return_value=[self.scope]):
            refused = self.activate(settings)
        self.assertEqual((refused['state'], refused['reason']), ('blocked', 'open_unrouted_assignments'))
        stored = json.loads(settings.read_bytes())
        self.assertEqual((stored['revision'], stored['values']['managed_projects'], stored['values']['L']),
                         (3, [], 0.6))
        self.assertTrue(prepared['launch_admitted'])
        self.assertEqual(len(calls), 2)


class RealScopeResolver(ManagedDelegation):
    """PR #217 follow-up: `open_unrouted_assignments` through the real entry path, not a mocked `_scopes_for`.
    Only the native qualification read is the fixture's synthetic trusted-owner observation."""

    @contextmanager
    def qualified(self):
        from runtime import setup_observation
        observation = json.loads(self.fixture.test_acquisition.read_bytes())['observation']
        with mock.patch.object(setup_observation, 'read_qualification', lambda record, facts, **options: observation):
            yield

    def enrol_as(self, client, runtime):
        record = json.loads(self.scope.authority_path.read_bytes())
        for repository in record['repositories']:
            for checkout in repository['checkouts']:
                for entry in checkout['clients']:
                    entry['client'] = client
                    if not runtime:
                        entry['configuration']['runtime'] = None
        self.scope.authority_path.write_text(json.dumps(record))

    def test_the_real_resolver_lists_the_open_assignment_until_it_settles(self):
        prepared = self.prepare(**self.inherit_request())
        with self.qualified():
            self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), [prepared['attempt_id']])
            self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(prepared, outcome='cancelled'))
            self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), [])

    def test_an_unqualified_runtime_fails_closed(self):
        self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), ['scope_unavailable'])

    def test_a_checkout_enrolled_for_another_client_without_runtime_state_can_be_activated(self):
        self.enrol_as('opencode', runtime=False)
        self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), [])

    def test_another_client_with_runtime_state_fails_closed(self):
        self.enrol_as('claude-code', runtime=True)
        self.assertEqual(delegation_routing.open_unrouted_assignments(self.repo), ['scope_unavailable'])

    def test_an_unenrolled_checkout_has_no_native_state(self):
        outside = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, outside)
        self.assertEqual(delegation_routing.open_unrouted_assignments(outside), [])


class ReviewS2Guards(ManagedDelegation):
    """PR #217 review S2: the guards that keep a launched assignment from being routed or released again."""

    def test_an_observed_assignment_is_never_routed_again(self):
        request = self.prepare_request()
        with self.managed(NATIVE):
            prepared = self.api.prepare_assignment(self.scope, request)
            self.observe_worker(prepared)
            with features.counted_admissions() as admit:
                replay = self.api.prepare_assignment(self.scope, request)
        self.assertEqual(admit.count, 0)
        self.assertFalse(replay['launch_admitted'])
        self.assertNotIn('routing', replay)

    def test_a_consumed_workstream_dispatch_is_never_routed_again(self):
        from runtime import workstreams
        current = workstreams.control(self.scope, 'status', dict(client='codex'))
        enabled = workstreams.control(self.scope, 'enable', dict(client='codex', expected_revision=current['revision']))
        limits = dict(context_id='WS42.01', rationale='One bounded outcome', output_tokens=12000, context_tokens=64000,
                      max_steps=40, max_seconds=1200, max_continuations=3, backoff_seconds=0)
        request = self.prepare_request(workstream_revision=enabled['revision'], workstream_limits=limits)
        with self.managed(NATIVE):
            prepared = self.api.prepare_assignment(self.scope, request)
            workstreams.consume_dispatch(self.scope, self.request(attempt_id=prepared['attempt_id'],
                                                                  workstream_revision=enabled['revision']))
            with features.counted_admissions() as admit, \
                    mock.patch.object(delegation_routing, '_hold', side_effect=AssertionError('routed again')):
                replay = self.api.prepare_assignment(self.scope, request)
        self.assertEqual(admit.count, 0)
        self.assertFalse(replay['dispatch_prepared'])
        self.assertNotIn('routing', replay)

    def test_release_refuses_a_dispatched_attempt(self):
        request = self.prepare_request()
        with self.managed(NATIVE):
            prepared = self.api.prepare_assignment(self.scope, request)
            self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')
            with self.assertRaisesRegex(state.StateError, 'dispatched routing attempt'):
                delegation_routing.release(self.scope, request, prepared['attempt_id'], 'catalog_mismatch')
            self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')
        self.assertEqual(self.raw_assignment(prepared['attempt_id'])['status'], 'prepared')


class ReviewS3Unmanaged(ManagedDelegation):
    """PR #217 review S3: holds close by the record's routing admission, not by the current managed state."""

    def test_unmanaging_after_prepare_still_closes_the_hold_at_settlement(self):
        with self.managed(NATIVE):
            prepared = self.prepare()
            self.assertEqual(self.hold(prepared['attempt_id']), 'dispatched')
            from runtime import routing_settings
            with mock.patch.object(routing_settings, 'managed_project', lambda *args, **kwargs: False):
                settled = self.api.reconcile_assignment(self.scope, self.worker_reconciliation_request(
                    prepared, outcome='cancelled'))
            self.assertEqual(settled['routing']['closed'], [dict(attempt_id=prepared['attempt_id'], kind='complete',
                                                                 state='recorded')])
            self.assertEqual(self.hold(prepared['attempt_id']), 'released')

class SelfCheck(unittest.TestCase):
    def test_managed_self_check_passes_in_a_sandbox(self):
        workdir = Path(tempfile.mkdtemp()).resolve() / 'check'
        self.addCleanup(shutil.rmtree, workdir.parent)
        self.assertEqual(delegation_routing.managed_self_check(str(workdir)), [])


if __name__ == '__main__':
    unittest.main()
