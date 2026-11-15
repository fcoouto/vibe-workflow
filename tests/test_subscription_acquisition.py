"""Billing acquisition precedes eligibility; synthetic producers never qualify the real source."""
import copy
import json
from unittest import mock

from runtime import (delegation_routing, native_routing, routing, routing_launch, routing_paths,
                     routing_observations as collector, routing_settings, subscription_billing)
from runtime.adapters import native_catalog
from scripts import routing as command
from tests.test_routing_command import job
from tests.test_routing_observations import Stores, native_reading, sub_window
from tests.test_qualification_canary_ranked import RankedStores

PATH = 'claude-native-subscription'
POOL = 'claude-subscription'
IDENTITY = dict(provider='anthropic', model='invented-native-model', effort='high', harness='claude-native')


class AcquisitionTests(Stores):
    def setUp(self):
        super().setUp()
        self.project = self.root / 'checkout'
        self.project.mkdir()
        self.facts, self.reads = {}, []
        for path in (PATH, 'opencode-claude-subscription'):
            routing_paths.qualify(self.settings, path, demand={'5h': 5, '7d': 5}, evidence='synthetic', now=self.now)
        self.enterContext(mock.patch.object(
            collector.LocalSource, 'billing',
            lambda source, route, now: copy.deepcopy(self.facts.get(self.key(route)))))
        self.enterContext(mock.patch.object(
            collector.LocalSource, 'acquire_billing', self.acquire, create=True))
        self.enterContext(mock.patch('subprocess.Popen', side_effect=AssertionError('real client started')))
        self.enterContext(mock.patch('subprocess.run', side_effect=AssertionError('real client started')))

    @staticmethod
    def key(route):
        return tuple(subscription_billing.scope(route).values())

    def acquire(self, routes, at):
        """The synthetic native boundary writes facts and incidental windows, never real account evidence."""
        self.reads.append(copy.deepcopy(routes))
        for route in routes:
            self.facts[self.key(route)] = subscription_billing.double('acquisition-test')(route, at)
            readings = [dict(sub_window(at), id=window, length=length) for window, length in (('5h', 18000),
                                                                                              ('7d', 604800))]
            if route['pool'] == POOL and routing_paths.path_of(route['route']) == PATH:
                native_reading(self.settings, readings, at)  # the native reader binds its rows to the account
            else:
                for item in readings:
                    collector.store_window(self.settings, route['pool'], item)

    def collect(self, refresh=True, **changes):
        options = dict(choices=[dict(IDENTITY, funding='subscription')], harnesses=['claude-native'])
        options.update(changes)
        return collector.collect(str(self.project), dict(scope='scope1'), settings_path=self.settings,
                                 ledger_path=self.ledger_path, refresh_windows=refresh, **options)

    def service_for_project(self, **options):
        service = command.Service(self.settings, self.ledger_path, project=self.project, **options)
        self.addCleanup(service.close)
        return service

    def body(self, attempt='a'):
        return dict(v=1, op='prepare', assignment=dict(
            attempt_id=attempt, task='task', scope='scope1', choice=dict(IDENTITY, funding='subscription')),
            job=job(), constraints=dict(allowed_harnesses=['claude-native']))

    def test_cold_billing_is_acquired_before_eligibility_and_incidental_windows_are_reused(self):
        answer = self.collect()
        self.assertEqual(len(answer['observations']['routes']), 1, answer)
        self.assertEqual(len(self.reads), 1)
        self.assertEqual({subscription_billing.scope(route)['path'] for route in self.reads[0]}, {PATH})
        self.assertEqual({window['id'] for window in answer['observations']['pools'][POOL]['windows']},
                         {'5h', '7d'})
        self.assertTrue(all(window['fresh'] for window in answer['observations']['pools'][POOL]['windows']))

    def test_fresh_facts_avoid_another_acquisition_and_expired_facts_do_not(self):
        self.collect()
        again = self.collect()
        self.assertEqual(len(again['observations']['routes']), 1, again)
        self.assertEqual(len(self.reads), 1)
        for fact in self.facts.values():
            fact['valid_until'] = self.now - 1
        refreshed = self.collect()
        self.assertEqual(len(refreshed['observations']['routes']), 1, refreshed)
        self.assertEqual(len(self.reads), 2)

    def test_acquisition_failure_keeps_billing_unverified(self):
        with mock.patch.object(collector.LocalSource, 'acquire_billing', side_effect=OSError('private detail')):
            answer = self.collect()
        self.assertEqual(answer['observations']['routes'], [])
        self.assertEqual([item['reason'] for item in answer['observations']['excluded']], ['billing_unverified'])
        self.assertNotIn('private detail', json.dumps(answer))

    def test_acquisition_return_value_is_not_accepted_as_a_fact(self):
        with mock.patch.object(collector.LocalSource, 'acquire_billing', return_value=dict(
                qualified=True, paid_crossover='provider_blocked')):
            answer = self.collect()
        self.assertEqual(answer['observations']['routes'], [])
        self.assertEqual([item['reason'] for item in answer['observations']['excluded']], ['billing_unverified'])

    def test_foreign_unqualified_and_not_entitled_paths_cause_no_acquisition(self):
        foreign = self.collect(harnesses=['codex-native'])
        self.assertEqual(foreign['observations']['routes'], [])
        route = collector.LocalSource(self.settings).manifest(None, [dict(IDENTITY, funding='subscription')])[0]
        collector.mark_not_entitled(self.settings, route['route'], route['account'], self.now)
        refused = self.collect()
        self.assertEqual([item['reason'] for item in refused['observations']['excluded']], ['not_entitled'])
        routing_paths.unqualify(self.settings, PATH)
        self.assertEqual(self.collect()['observations']['routes'], [])
        self.assertEqual(self.reads, [])

    def test_unreadable_entitlements_never_trigger_a_read(self):
        with mock.patch.object(collector, 'entitlement_marks', side_effect=ValueError('invalid')):
            answer = self.collect()
        self.assertEqual([item['reason'] for item in answer['observations']['excluded']], ['entitlement_unavailable'])
        self.assertEqual(self.reads, [])

    def test_preview_status_check_and_served_preparation_never_acquire(self):
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        service = self.service_for_project(serving=True)
        self.assertEqual(service.preview({key: value for key, value in self.body().items()
                                          if key not in ('v', 'op')})['state'], 'previewed')
        self.assertEqual(service.handle(b'{"v":1,"op":"status"}')['state'], 'ok')
        service.handle(b'{"v":1,"op":"check","attempt_id":"missing"}')
        self.assertEqual(service.handle(json.dumps(self.body()).encode())['state'], 'pending')
        self.assertEqual(self.collect(refresh=False)['observations']['routes'], [])
        self.assertEqual(self.reads, [])

    def test_one_shot_preparation_bootstraps_billing_before_admission(self):
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        service = self.service_for_project(one_shot=True)
        answer = service.handle(json.dumps(self.body()).encode())
        self.assertEqual(answer['state'], 'admitted', answer)
        self.assertEqual(len(self.reads), 1)
        for fact in self.facts.values():
            fact['valid_until'] = self.now - 1
        facts, reason = routing_launch.admitted_check(
            answer['admission'], host='claude_native', catalog=[IDENTITY], settings_path=self.settings)
        self.assertEqual((facts, reason), (None, 'billing_unverified'))
        self.assertEqual(len(self.reads), 1)  # dispatch rechecks, never refreshes

    def test_preactivation_acquires_before_source_readiness_but_removal_does_not(self):
        binding = dict(client_version='2.1.311', connection='synthetic', controls='synthetic')
        native_catalog.store_snapshot(self.settings, 'claude-code', dict(
            binding=binding, models=[dict(alias=IDENTITY['model'], resolved=None, efforts=['high'])]))
        service = self.service_for_project(one_shot=True)
        with mock.patch.object(delegation_routing, 'enrolled_hosts', return_value=['claude-code']), \
                mock.patch.object(delegation_routing, 'open_unrouted_assignments', return_value=[]), \
                mock.patch.object(native_routing, '_runtime_delegation', return_value=None), \
                mock.patch.object(native_catalog, 'current_binding', return_value=binding), \
                mock.patch.object(command, '_self_check', return_value=[]):
            answer = service.handle(json.dumps(dict(v=1, op='set', expected_revision=0,
                change=dict(managed_projects=[str(self.project)]))).encode())
            self.assertEqual(answer['state'], 'updated', answer)
            self.assertEqual(len(self.reads), 1)
            removed = service.handle(json.dumps(dict(v=1, op='set', expected_revision=1,
                change=dict(managed_projects=[]))).encode())
        self.assertEqual(removed['state'], 'updated', removed)
        self.assertEqual(len(self.reads), 1)

    def test_internal_acquisition_for_preview_uses_the_same_request_selection(self):
        service = self.service_for_project(one_shot=True)
        body = {key: value for key, value in self.body().items() if key not in ('v', 'op')}
        method = getattr(service, 'acquire_billing', lambda body: None)
        method(body)
        preview = service.preview(body)
        self.assertEqual(len(preview['routes']), 1, preview)
        self.assertEqual(len(self.reads), 1)
        served = self.service_for_project(serving=True)
        for fact in self.facts.values():
            fact['valid_until'] = self.now - 1
        getattr(served, 'acquire_billing', lambda body: None)(body)
        self.assertEqual(len(self.reads), 1)

    def test_unavailable_native_bridge_never_acquires_during_activation(self):
        service = self.service_for_project(one_shot=True)
        with mock.patch.object(delegation_routing, 'enrolled_hosts', return_value=['claude-code']), \
                mock.patch.object(delegation_routing, 'open_unrouted_assignments', return_value=[]), \
                mock.patch.object(native_routing, '_runtime_delegation', return_value='managed_delegation_required'), \
                mock.patch.object(command, '_self_check', return_value=[]):
            answer = service.handle(json.dumps(dict(v=1, op='set', expected_revision=0,
                change=dict(managed_projects=[str(self.project)]))).encode())
        self.assertEqual(answer['codes']['subscription_sources'], ['claude_code_native_bridge_unavailable'])
        self.assertEqual(self.reads, [])

    def test_unverified_model_demand_never_triggers_acquisition(self):
        path = next(path for path in routing_paths.load_rules()['paths'] if path.get('demand_scope') == 'per_model')
        routing_paths.qualify(self.settings, path['path'], demand={window: 5 for window in path['windows']},
                              evidence='synthetic', now=self.now)
        answer = self.collect(choices=[dict(provider=path['provider'], model='invented-go', effort='high',
            harness=path['harness'], funding='subscription')], harnesses=[path['harness']])
        self.assertEqual(answer['observations']['routes'], [])
        self.assertEqual(self.reads, [])

    def test_manifest_failure_is_sanitized_before_acquisition(self):
        routing_settings.update(self.settings, dict(managed_projects=[str(self.project)]), 0)
        service = self.service_for_project(one_shot=True)
        with mock.patch.object(collector.LocalSource, 'manifest', side_effect=ValueError('private detail')):
            answer = service.handle(json.dumps(self.body()).encode())
        self.assertEqual((answer['state'], answer['reason']), ('blocked', 'observations_unavailable'))
        self.assertNotIn('private detail', json.dumps(answer))
        self.assertEqual(self.reads, [])

    def test_nonlocal_collector_source_never_acquires(self):
        route = collector.LocalSource(self.settings).manifest(None, [dict(IDENTITY, funding='subscription')])[0]
        source = collector.StaticSource([route])
        source.acquire_billing = mock.Mock(side_effect=AssertionError('injected acquisition ran'))
        answer = collector.collect(str(self.project), dict(scope='scope1'), source=source,
            settings_path=self.settings, ledger_path=self.ledger_path, refresh_windows=True,
            choices=[dict(IDENTITY, funding='subscription')], harnesses=['claude-native'])
        self.assertEqual(answer['observations']['routes'], [])
        self.assertEqual(source.acquire_billing.call_count, 0)


class UnqualifiedSourceTests(Stores):
    def test_positive_looking_files_cannot_supply_a_production_billing_fact(self):
        self.settings.parent.mkdir(mode=0o700)
        for name in ('claude-usage.json', 'subscription-billing.json'):
            path = self.settings.parent / name
            path.write_text(json.dumps(dict(version=1, qualified=True, paid_crossover='provider_blocked')))
            path.chmod(0o600)
        source = collector.LocalSource(self.settings)
        self.assertIsNone(source.billing(dict(route=PATH + ':s', pool=POOL, account='claude-plan'), self.now))


class CanaryAcquisitionTests(RankedStores):
    def test_boundary_acquires_before_preview_while_preview_itself_stays_pure(self):
        controller = self.controller()
        facts, reads = {}, []

        def acquire(source, routes, at):
            reads.append(copy.deepcopy(routes))
            for route in routes:
                facts[tuple(subscription_billing.scope(route).values())] = \
                    subscription_billing.double('canary-acquisition')(route, at)

        with mock.patch.object(collector.LocalSource, 'billing', lambda source, route, at:
                               facts.get(tuple(subscription_billing.scope(route).values()))), \
                mock.patch.object(collector.LocalSource, 'acquire_billing', acquire, create=True):
            body = controller.request_body(self.body())
            self.assertEqual(controller.preview(body)['routes'], [])
            self.assertEqual(reads, [])
            controller.before_prepare(self.body())
        self.assertEqual(len(controller.kept), 2)
        self.assertEqual(len(reads), 1)

    def test_acquisition_time_is_not_recorded_as_preview_time(self):
        from tests.qualification import canary_ranked
        controller = self.controller()
        with mock.patch.object(canary_ranked.time, 'perf_counter', side_effect=[0, 2, 5, 5.001, 10, 10.003]):
            controller.before_prepare(self.body())
        timing = controller.timings[-1]
        self.assertEqual(timing.get('acquire_ms'), 2000)
        self.assertEqual(timing['preview_ms'], 1)
        self.assertEqual(timing['refresh_ms'], 3)
