"""F4.6: native-only selection and launch for Codex and Claude Code through the `native_prepare`/`native_complete`
bridge (`runtime/native_routing.py`, `runtime/adapters/native_catalog.py`).

Every flow runs the real one-shot routing command (`scripts/routing.py`) on `LocalSource`, the shipped path rules and
temporary stores. Not qualified in production and supplied here only by labelled code-level doubles: the current
native client binding (`native_catalog.current_binding`), the stored native catalog snapshot (written through the
adapter's own `store_snapshot`, as a qualified acquisition would) and the subscription billing fact. The native Agent
or subagent spawn is a tool double that receives exactly what the bridge permits. Nothing here starts a client,
reads an account or credential, or calls a model.
"""
import io
import json
import subprocess
import time
import unittest
from unittest import mock

from runtime import native_routing, routing, routing_launch, routing_observations as collector, routing_paths, \
    routing_settings, subscription_billing, task_profile
from runtime.adapters import native_catalog
from scripts import routing as command
from tests import test_routing_command as command_tests
from tests.test_routing_observations import SCOPE, Stores

CLAUDE_PATH, CODEX_PATH = 'claude-native-subscription', 'codex-chatgpt-subscription'
BINDINGS = {'claude-code': dict(client_version='invented-2', connection='native-login', controls='agent-alias-effort'),
            'codex': dict(client_version='invented-0', connection='native-login', controls='model-effort')}
SNAPSHOTS = {'claude-code': [dict(alias='invented-small', resolved='invented-small-1', efforts=['low', 'high']),
                             dict(alias='invented-large', resolved='invented-large-1', efforts=['low', 'high'])],
             'codex': [dict(alias='invented-codex-small', resolved=None, efforts=['low', 'high']),
                       dict(alias='invented-codex-large', resolved=None, efforts=['high'])]}
HARNESS = {'claude-code': 'claude-native', 'codex': 'codex-native'}
PROVIDER = {'claude-code': 'anthropic', 'codex': 'openai'}


class NativeTool:
    """The native Agent/subagent invocation double: it launches only with what a permit names and reports back."""

    def __init__(self, resolved=None, effort=None):
        self.calls, self.resolved, self.effort = [], resolved, effort

    def __call__(self, permit):
        self.calls.append(dict(model=permit['model'], effort=permit['effort']))
        outcome = dict(kind='complete')
        if self.resolved is not None:
            outcome['model'] = self.resolved
        if self.effort is not None:
            outcome['effort'] = self.effort
        return outcome


class NativeBase(Stores):
    def setUp(self):
        super().setUp()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(self.checkout)]), 0)
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=True))['state'], 'updated')
        self.guard = mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('no client process'))
        self.guard.start()
        self.addCleanup(self.guard.stop)
        real, self.offset = time.time, 0  # a test clock that moves forward only when a test says so
        self.enterContext(mock.patch('time.time', lambda: real() + self.offset))
        self.enterContext(mock.patch.object(native_catalog, 'current_binding', lambda client: BINDINGS.get(client)))
        for client, models in SNAPSHOTS.items():
            native_catalog.store_snapshot(self.settings, client, dict(binding=BINDINGS[client], models=models))
        routing_paths.qualify(self.settings, CLAUDE_PATH, demand={'5h': 5, '7d': 5}, evidence='synthetic-1',
                              now=self.now)
        routing_paths.qualify(self.settings, CODEX_PATH, demand={'7d': 5}, evidence='synthetic-1', now=self.now)
        for pool, windows in (('claude-subscription', (('5h', 18000), ('7d', 604800))),
                              ('chatgpt-subscription', (('7d', 604800),))):
            for window, length in windows:
                self.window(pool, window, length)
        self.table()

    def window(self, pool, window, length, used=100, later=0):
        """Store a reading (setUp's are 10 s old); `later` moves it forward, since an older or equal reading never
        replaces one and a future-dated one is stale."""
        collector.store_window(self.settings, pool, dict(id=window, limit=1000, used=used, length=length,
                                                         reset_at=self.now + 3600, rolling=False,
                                                         observed_at=self.now - 10 + later, unit=collector.WINDOW_UNIT))

    def newer_readings(self, used=100):
        """Independent telemetry observed after the last completion: the clock moves on and both pools get a reading
        observed after it (a completion makes earlier readings unusable for the pool)."""
        self.offset += 3
        at = int(time.time()) - 1
        for pool, windows in (('claude-subscription', (('5h', 18000), ('7d', 604800))),
                              ('chatgpt-subscription', (('7d', 604800),))):
            for window, length in windows:
                collector.store_window(self.settings, pool, dict(id=window, limit=1000, used=used, length=length,
                                                                 reset_at=at + 3600, rolling=False, observed_at=at,
                                                                 unit=collector.WINDOW_UNIT))

    def billing(self, **change):
        double = subscription_billing.double('native')
        return self.enterContext(mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now:
                                                   dict(double(route, now), **change)))

    def table(self, extra=()):
        """Low-complexity work ranks each client's small model first, high-complexity work its large one."""
        rows = {}
        for band in ('low', 'high'):
            profile = routing.ensure_profile(self.job(band))
            key = '|'.join((profile['work_kind'], profile['complexity_band'], profile['risk_band']))
            rows[key] = [dict(strategy_id='s-%s-%s' % (client, band), model=SNAPSHOTS[client][0 if band == 'low' else 1]
                              ['alias'], effort='high', harness=HARNESS[client], provider=PROVIDER[client],
                              evidence_refs=['g']) for client in ('claude-code', 'codex')] + list(extra)
        target = self.settings.parent / 'strategy-table.json'
        target.write_text(json.dumps(dict(schema=2, version='tv-native', rows=rows)))
        target.chmod(0o600)

    def job(self, band='low'):
        job = command_tests.job()
        job['profile'].update(complexity_band=band, risk_band='low')
        return job

    def command(self, request, *flags):
        stdout = io.BytesIO()
        command.main([request['op'], '--settings', str(self.settings), '--ledger', str(self.ledger_path),
                      '--project', str(self.checkout), *flags], io.BytesIO(json.dumps(request).encode()), stdout)
        return json.loads(stdout.getvalue())

    def prepare(self, client, attempt='n1', band='low', **assignment):
        body = dict(attempt_id=attempt, task='task-' + attempt, scope=routing_launch.assignment_scope(self.checkout),
                    **assignment)
        return self.command(dict(v=1, op='native_prepare', client=client, assignment=body, job=self.job(band)))

    def complete(self, attempt, outcome):
        return self.command(dict(v=1, op='native_complete', attempt_id=attempt, outcome=outcome))

    def run_native(self, client, tool, attempt='n1', band='low', **assignment):
        """The client contract: Bash `native_prepare`, the native spawn only on a permit, then `native_complete`."""
        permit = self.prepare(client, attempt, band, **assignment)
        if permit.get('state') != 'dispatched':
            return permit, None
        return permit, self.complete(attempt, tool(permit))

    def holds(self):
        ledger = self.service._ledger(False)
        return {} if ledger is None else ledger.snapshot()['holds']


class ProductionDefaultTests(Stores):
    """The real sources: no verified current client binding and no stored snapshot, so the bridge refuses before
    any admission, without starting a process."""

    def test_the_real_binding_and_snapshot_defaults_are_unavailable(self):
        self.assertIsNone(native_catalog.current_binding('claude-code'))
        self.assertIsNone(native_catalog.current_binding('codex'))
        self.assertEqual(native_catalog.catalog(self.settings, 'claude-code'), (None, 'native_binding_unverified'))
        with mock.patch.object(native_catalog, 'current_binding', lambda client: BINDINGS[client]):
            self.assertEqual(native_catalog.catalog(self.settings, 'codex'), (None, 'native_catalog_unavailable'))

    def test_claude_initialization_and_codex_model_lists_map_without_name_parsing(self):
        claude = native_catalog.from_claude_initialize(dict(models=[
            dict(value='invented-alias', resolvedModel='invented-full-1', supportsEffort=True,
                 supportedEffortLevels=['low', 'high']),
            dict(value='invented-plain', supportsEffort=False), dict(value=''), 'junk']))
        self.assertEqual(claude, [dict(alias='invented-alias', resolved='invented-full-1', efforts=['low', 'high']),
                                  dict(alias='invented-plain', resolved=None, efforts=[])])
        codex = native_catalog.from_codex_models([dict(model='invented-codex', supported_efforts=['low', 'high']),
                                                 dict(model=None)])
        self.assertEqual(codex, [dict(alias='invented-codex', resolved=None, efforts=['low', 'high'])])
        identities = native_catalog.identities('claude-code', claude)
        self.assertIn(routing_launch.route_identity('claude_native', model='invented-plain', effort=None), identities)
        self.assertEqual({item['harness'] for item in identities}, {'claude-native'})


class NativeLaunchTests(NativeBase):
    def test_native_clients_rank_and_launch_on_the_local_source(self):
        """P9: both clients, low and high capability requirements, each on its own native catalog: the spawn gets
        exactly the admitted alias and effort, and the attempt settles once."""
        self.billing()
        for client in ('claude-code', 'codex'):
            for band, index in (('low', 0), ('high', 1)):
                attempt = '%s-%s' % (client, band)
                with self.subTest(client=client, band=band):
                    tool = NativeTool()
                    permit, completed = self.run_native(client, tool, attempt, band)
                    self.assertEqual(permit['state'], 'dispatched', permit)
                    self.assertEqual(permit['mode'], 'ranked')
                    self.assertTrue(permit['route'].startswith(CLAUDE_PATH if client == 'claude-code' else CODEX_PATH))
                    self.assertEqual(tool.calls, [dict(model=SNAPSHOTS[client][index]['alias'], effort='high')])
                    self.assertNotIn('authority', permit)
                    self.assertEqual(completed['state'], 'recorded', completed)
                    self.assertEqual(self.holds()[attempt]['state'], 'released')
                    self.assertEqual(completed['served'], dict(model='unknown', effort='unknown'))  # not observable
                    self.newer_readings()  # the next launch on this pool needs a reading newer than this completion

    def test_native_profile_changes_model_selection(self):
        self.billing()
        low, high = self.prepare('claude-code', 'p-low', 'low'), self.prepare('claude-code', 'p-high', 'high')
        self.assertEqual((low['model'], high['model']), ('invented-small', 'invented-large'))

    def test_claude_never_selects_a_foreign_route(self):
        self.billing()
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=self.now)
        self.table(extra=[dict(strategy_id='s-oc', model='haiku', effort='low', harness='claude-agent-sdk via opencode',
                               provider='claude-agent', evidence_refs=['g'])])
        permit = self.prepare('claude-code')
        self.assertTrue(permit['route'].startswith(CLAUDE_PATH + ':'), permit)

    def test_codex_never_selects_a_foreign_route(self):
        self.billing()
        routing_paths.qualify(self.settings, 'opencode-chatgpt-subscription', demand={'7d': 5}, evidence='synthetic-1',
                              now=self.now)
        self.table(extra=[dict(strategy_id='s-oc', model='invented-chatgpt', effort='high',
                               harness='opencode provider openai', provider='openai', evidence_refs=['g'])])
        permit = self.prepare('codex')
        self.assertTrue(permit['route'].startswith(CODEX_PATH + ':'), permit)

    def test_native_spawn_receives_the_admitted_settings(self):
        self.billing()
        tool = NativeTool()
        permit, _ = self.run_native('claude-code', tool, band='high')
        self.assertEqual(tool.calls, [dict(model=permit['model'], effort=permit['effort'])])
        self.assertEqual((permit['model'], permit['effort']), ('invented-large', 'high'))

    def test_native_explicit_choice_is_not_substituted(self):
        self.billing()
        choice = dict(provider='anthropic', model='invented-missing', effort='high', harness='claude-native',
                      funding='subscription')
        permit = self.prepare('claude-code', choice=choice)
        self.assertEqual((permit['state'], permit['reason']), ('pending', 'explicit_choice_unavailable'), permit)
        chosen = self.prepare('claude-code', 'n2', choice=dict(choice, model='invented-small', effort='low'))
        self.assertEqual((chosen['state'], chosen['model'], chosen['effort']), ('dispatched', 'invented-small', 'low'))

    def test_native_unavailable_route_sends_nothing(self):
        tool = NativeTool()
        permit, completed = self.run_native('claude-code', tool)  # the real billing producer: none
        self.assertEqual(permit['state'], 'pending', permit)
        self.assertEqual((tool.calls, completed, self.holds()), ([], None, {}))

    def test_a_completion_without_new_telemetry_never_reuses_the_pre_launch_headroom(self):
        """13b: a native completion releases its hold but reports no reading, so the pool's earlier readings stop
        counting for every alias of the shared pool until an independently newer one arrives."""
        self.billing()
        self.window('claude-subscription', '5h', 18000, used=993, later=1)  # room for one reservation only
        self.assertEqual(self.prepare('claude-code', 'q1')['state'], 'dispatched')
        self.assertEqual(self.prepare('claude-code', 'q2')['state'], 'pending')  # held
        store = collector.window_path(self.settings)
        before = store.read_bytes()
        self.assertEqual(self.complete('q1', dict(kind='complete'))['state'], 'recorded')
        self.assertEqual(json.loads(store.read_bytes())['pools'], json.loads(before)['pools'])  # no reading invented
        self.assertEqual(self.prepare('claude-code', 'q3')['state'], 'pending')  # not the same pre-launch headroom
        pinned = command.Service(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(pinned.close)
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=int(time.time()))
        alias = pinned.handle(json.dumps(routing_launch.prepare_request(
            dict(attempt_id='oc-q', task='task-oc-q', scope=routing_launch.assignment_scope(self.checkout),
                 choice=dict(provider='claude-agent', model='haiku', effort='low',
                             harness='claude-agent-sdk via opencode', funding='subscription')), self.job(),
            harnesses=['claude-agent-sdk via opencode'])).encode())
        self.assertEqual(alias['state'], 'pending', alias)  # the OpenCode alias of the pool is held back too
        self.assertEqual(self.prepare('codex', 'q-codex')['state'], 'dispatched')  # another pool is unaffected
        self.offset += 3
        self.window('claude-subscription', '5h', 18000, used=100, later=0)  # an older/equal cached reading: no
        self.assertEqual(self.prepare('claude-code', 'q4')['state'], 'pending')
        self.newer_readings()  # an independently newer reading restores the pool
        self.assertEqual(self.prepare('claude-code', 'q5')['state'], 'dispatched')

    def test_a_failed_completion_boundary_keeps_the_capacity_held(self):
        self.billing()
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        with mock.patch.object(collector, 'mark_consumed', side_effect=OSError('store unwritable')):
            answer = self.complete('n1', dict(kind='complete'))
        self.assertEqual(answer['state'], 'blocked', answer)
        self.assertEqual(self.holds()['n1']['state'], 'dispatched')  # never released without the boundary
        self.assertEqual(self.complete('n1', dict(kind='complete'))['state'], 'recorded')  # a retry settles it

    def boundary(self, pool='claude-subscription'):
        return collector.consumed_at(self.settings, pool)

    def test_a_replayed_completion_never_moves_the_boundary_again(self):
        """13c: the same completion after newer telemetry is the ledger's duplicate and changes no capacity."""
        self.billing()
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        self.assertEqual(self.complete('n1', dict(kind='complete'))['state'], 'recorded')
        first = self.boundary()
        self.newer_readings()
        store = collector.window_path(self.settings).read_bytes()
        replay = self.complete('n1', dict(kind='complete'))
        self.assertIs(replay.get('duplicate'), True, replay)
        self.assertEqual((self.boundary(), collector.window_path(self.settings).read_bytes()), (first, store))
        self.assertEqual(self.prepare('claude-code', 'n2')['state'], 'dispatched')  # the newer reading still counts

    def test_rejected_or_foreign_completions_never_move_the_boundary(self):
        self.billing()
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        self.assertEqual(self.complete('n1', dict(kind='complete'))['state'], 'recorded')
        first = self.boundary()
        self.newer_readings()
        late = self.complete('n1', dict(kind='unknown'))  # the ledger refuses a later, different outcome
        self.assertNotEqual(late.get('state'), 'recorded', late)
        self.assertEqual(self.complete('never-admitted', dict(kind='complete'))['reason'], 'not_a_native_attempt')
        pinned = command.Service(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(pinned.close)
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=int(time.time()))
        held = pinned.handle(json.dumps(routing_launch.prepare_request(
            dict(attempt_id='oc-x', task='task-oc-x', scope=routing_launch.assignment_scope(self.checkout),
                 choice=dict(provider='claude-agent', model='haiku', effort='low',
                             harness='claude-agent-sdk via opencode', funding='subscription')), self.job(),
            harnesses=['claude-agent-sdk via opencode'])).encode())
        self.assertEqual(held['state'], 'admitted', held)
        foreign = self.complete('oc-x', dict(kind='complete'))  # an OpenCode attempt is not this bridge's to settle
        self.assertEqual((foreign['state'], foreign['reason']), ('blocked', 'not_a_native_attempt'), foreign)
        self.assertEqual(self.holds()['oc-x']['state'], 'admitted')
        self.assertEqual(self.boundary(), first)

    def test_a_retried_completion_marks_the_boundary_once(self):
        """A completion whose ledger record failed after the boundary was written is retried; the retry settles it
        without moving the boundary past newer telemetry (one mark per attempt, under the store's lock)."""
        self.billing()
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        with mock.patch.object(native_routing, '_record', return_value=dict(state='blocked', reason='ledger_busy')):
            self.assertEqual(self.complete('n1', dict(kind='complete'))['reason'], 'ledger_busy')
        first = self.boundary()
        self.assertGreater(first, 0)  # written before the release was attempted
        self.assertEqual(self.holds()['n1']['state'], 'dispatched')  # and the hold kept its capacity
        self.newer_readings()
        self.assertEqual(self.complete('n1', dict(kind='complete'))['state'], 'recorded')
        self.assertEqual(self.boundary(), first)
        self.assertEqual(self.prepare('claude-code', 'n2')['state'], 'dispatched')

    def test_not_started_after_a_permit_is_held_for_operator_recovery(self):
        """13b: once the permit is out, routing cannot verify that an advisory native launch never started, and the
        ledger closes a dispatched hold only through settlement or recovery; so `not_started` is recorded as an
        uncertain send kept counted until the operator reconciles it, and never gets another permit."""
        self.billing()
        self.window('claude-subscription', '5h', 18000, used=993, later=1)
        self.assertEqual(self.prepare('claude-code')['state'], 'dispatched')
        answer = self.complete('n1', dict(kind='not_started'))
        self.assertEqual((answer['state'], answer['disposition'], answer['recovery']),
                         ('recorded', 'uncertain', 'reconcile_subscription'), answer)
        self.assertEqual(self.holds()['n1']['state'], 'uncertain')
        self.assertNotEqual(self.prepare('claude-code')['state'], 'dispatched')  # the same attempt: no new permit
        self.assertEqual(self.prepare('claude-code', 'n2')['state'], 'pending')  # its capacity is still held

    def test_native_unknown_send_is_not_retried(self):
        self.billing()
        permit = self.prepare('claude-code')
        self.assertEqual(self.complete('n1', dict(kind='unknown'))['state'], 'recorded')
        self.assertEqual(self.holds()['n1']['state'], 'uncertain')
        again = self.prepare('claude-code')  # the same attempt: never a second permit
        self.assertNotEqual(again['state'], 'dispatched', again)
        self.assertEqual(permit['state'], 'dispatched')

    def test_native_completion_settles_once(self):
        self.billing()
        self.prepare('claude-code')
        self.assertEqual(self.complete('n1', dict(kind='complete'))['state'], 'recorded')
        second = self.complete('n1', dict(kind='complete'))
        self.assertIs(second.get('duplicate'), True, second)  # the same event: settled once, never twice
        self.assertEqual(self.holds()['n1']['state'], 'released')
        late = self.complete('n1', dict(kind='unknown'))  # a later, different outcome cannot reopen it
        self.assertNotEqual(late.get('state'), 'recorded', late)

    def test_a_duplicate_prepare_grants_no_second_permit(self):
        self.billing()
        self.assertEqual(self.prepare('codex')['state'], 'dispatched')
        again = self.prepare('codex')
        self.assertEqual(again['state'], 'blocked', again)

    def test_native_model_substitution_is_reported(self):
        self.billing()
        permit, completed = self.run_native('claude-code', NativeTool(resolved='invented-other-9'))
        self.assertEqual(completed['state'], 'recorded', completed)
        self.assertEqual(completed['served'], dict(model='invented-other-9', effort='unknown', substituted=True))
        self.newer_readings()
        matched, done = self.run_native('claude-code', NativeTool(resolved='invented-small-1'), 'n2')
        self.assertEqual(done['served'], dict(model='invented-small-1', effort='unknown', substituted=False))

    def test_native_effort_override_is_not_silently_changed(self):
        self.billing()
        permit, completed = self.run_native('claude-code', NativeTool(effort='low'), band='high')
        self.assertEqual(permit['effort'], 'high')
        self.assertEqual(completed['served']['effort'], 'low')
        self.assertIs(completed['served']['effort_changed'], True)  # reported, never relabelled as admitted

    def test_an_unsupported_effort_is_refused_before_any_spawn(self):
        self.billing()
        native_catalog.store_snapshot(self.settings, 'claude-code', dict(binding=BINDINGS['claude-code'], models=[
            dict(alias='invented-small', resolved=None, efforts=['low'])]))  # `high` is no longer expressible
        tool = NativeTool()
        permit, _ = self.run_native('claude-code', tool)
        self.assertEqual((permit['state'], tool.calls), ('blocked', []), permit)
        self.assertEqual(permit['reason'], 'catalog_mismatch')
        self.assertEqual(self.holds()['n1']['state'], 'released')  # closed `not_dispatched`

    def test_exhaustion_and_shared_pool_reservations(self):
        self.billing()
        self.window('claude-subscription', '5h', 18000, used=993, later=1)  # room for one reservation only
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=self.now)
        pinned = command.Service(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(pinned.close)
        held = pinned.handle(json.dumps(routing_launch.prepare_request(
            dict(attempt_id='oc', task='task-oc', scope=routing_launch.assignment_scope(self.checkout),
                 choice=dict(provider='claude-agent', model='haiku', effort='low',
                             harness='claude-agent-sdk via opencode', funding='subscription')), self.job(),
            harnesses=['claude-agent-sdk via opencode'])).encode())
        self.assertEqual(held['state'], 'admitted', held)  # the OpenCode alias holds the shared Claude allowance
        tool = NativeTool()
        permit, _ = self.run_native('claude-code', tool)
        self.assertEqual((permit['state'], tool.calls), ('pending', []), permit)
        self.window('claude-subscription', '5h', 18000, used=1000, later=2)
        self.assertEqual(self.prepare('codex', 'c1')['state'], 'dispatched')  # another pool is unaffected


class NativeBoundaryTests(NativeBase):
    def test_native_command_rejects_caller_quota_and_catalog(self):
        self.billing()
        spy = mock.Mock(wraps=native_catalog.catalog)
        self.enterContext(mock.patch.object(native_catalog, 'catalog', spy))
        body = dict(attempt_id='n1', task='task-n1', scope=routing_launch.assignment_scope(self.checkout))
        for extra in (dict(catalog=[]), dict(observations={}), dict(windows=[]), dict(quota=1)):
            with self.subTest(extra=list(extra)):
                answer = self.command(dict(v=1, op='native_prepare', client='claude-code', assignment=body,
                                           job=self.job(), **extra))
                self.assertEqual((answer['state'], answer['reason']), ('blocked', 'invalid_request'))
        self.assertEqual(spy.call_count, 0)  # refused before any catalog acquisition
        self.assertEqual(self.holds(), {})
        self.assertEqual(self.command(dict(v=1, op='native_prepare', client='opencode', assignment=body,
                                           job=self.job()))['reason'], 'invalid_request')

    def test_native_alias_mapping_is_bound_to_current_client(self):
        self.billing()
        changed = dict(BINDINGS['claude-code'], client_version='invented-3')
        with mock.patch.object(native_catalog, 'current_binding', lambda client: changed):
            self.assertEqual(self.prepare('claude-code')['reason'], 'native_binding_changed')
        with mock.patch.object(native_catalog, 'current_binding', lambda client: None):  # the production default
            self.assertEqual(self.prepare('claude-code')['reason'], 'native_binding_unverified')
        self.assertEqual(self.holds(), {})

    def test_a_snapshot_is_reused_without_a_process_and_invalidated_by_a_changed_binding(self):
        self.billing()
        self.assertEqual(self.prepare('claude-code', 'r1')['state'], 'dispatched')
        self.assertEqual(self.prepare('claude-code', 'r2')['state'], 'dispatched')  # the same stored snapshot; Popen
        native_catalog.store_snapshot(self.settings, 'claude-code', dict(binding=dict(BINDINGS['claude-code'],
                                                                                      connection='other'),
                                                                         models=SNAPSHOTS['claude-code']))
        self.assertEqual(self.prepare('claude-code', 'r3')['reason'], 'native_binding_changed')

    def test_a_foreign_catalog_is_refused(self):
        self.billing()
        with mock.patch.object(native_catalog, 'catalog', lambda settings_path, client: ([
                routing_launch.route_identity('opencode', provider='claude-agent', model='haiku', effort='low')],
                None)):
            self.assertEqual(self.prepare('claude-code')['reason'], 'foreign_catalog')
        self.assertEqual(self.holds(), {})

    def test_missing_billing_or_a_changed_fact_sends_nothing(self):
        self.billing(paid_crossover='enabled')
        tool = NativeTool()
        self.assertEqual(self.run_native('claude-code', tool)[0]['state'], 'pending')
        self.assertEqual(tool.calls, [])

    def test_the_bridge_is_one_shot_and_managed_only(self):
        self.billing()
        serving = command.Service(self.settings, self.ledger_path, project=self.checkout, serving=True)
        self.addCleanup(serving.close)
        body = dict(attempt_id='s1', task='task-s1', scope=routing_launch.assignment_scope(self.checkout))
        answer = serving.handle(json.dumps(dict(v=1, op='native_prepare', client='codex', assignment=body,
                                                job=self.job())).encode())
        self.assertEqual(answer['reason'], 'native_op_not_served')
        routing_settings.update(self.settings, dict(managed_projects=[]), routing_settings.revision(self.settings))
        self.assertEqual(self.prepare('codex')['reason'], 'project_not_managed')

    def test_selected_runtime_delegation_is_not_bypassed(self):
        """A client whose enrolment keeps runtime task state (managed Codex D5/D6) uses its own path."""
        self.billing()
        from runtime import routing_gates
        with mock.patch.object(routing_gates, '_resolve_scope', lambda locator: object()):
            self.assertEqual(self.prepare('codex')['reason'], 'managed_delegation_required')
        self.assertEqual(self.holds(), {})

    def test_m2_a_missing_or_corrupt_template_is_a_structured_refusal(self):
        self.billing()
        for name, content in (('missing', None), ('corrupt', '{not json')):
            with self.subTest(template=name):
                path = self.root / ('template-%s.json' % name)
                if content is not None:
                    path.write_text(content)
                task_profile._cache.clear()
                with mock.patch.object(task_profile, 'TEMPLATE', path), \
                        mock.patch.object(task_profile.load, '__defaults__', (path,)):
                    tool = NativeTool()
                    permit, _ = self.run_native('claude-code', tool, attempt='m2-' + name)
                    self.assertEqual((permit['state'], permit['reason']), ('blocked', 'profile_unavailable'), permit)
                    self.assertEqual(tool.calls, [])
                task_profile._cache.clear()
        self.assertEqual(self.holds(), {})

    def test_m4_a_legacy_managed_job_stays_refused_and_unchanged(self):
        self.billing()
        before = {path.name: path.read_bytes() for path in self.settings.parent.iterdir() if path.is_file()}
        legacy = dict(self.job(), role='implementer')  # a stored pre-roster role name
        body = dict(attempt_id='legacy', task='task-legacy', scope=routing_launch.assignment_scope(self.checkout))
        answer = self.command(dict(v=1, op='native_prepare', client='claude-code', assignment=body, job=legacy))
        self.assertEqual((answer['state'], answer['reason']), ('blocked', 'profile_unavailable'), answer)
        after = {path.name: path.read_bytes() for path in self.settings.parent.iterdir() if path.is_file()}
        self.assertEqual(after, before)  # no alias, re-key or history rewrite
        self.assertEqual(self.holds(), {})



class BridgeInstructionTests(unittest.TestCase):
    """The documented bridge commands, run as written against disposable stores: on the real sources they answer a
    structured refusal and start nothing, and policy 03 sends a Claude Code coordinator to them."""
    ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]

    def test_documented_bridge_commands_run_and_fail_closed_on_the_real_sources(self):
        import os
        import re
        import tempfile
        from pathlib import Path
        text = (self.ROOT / 'clients' / 'claude-code.md').read_text()
        blocks = [' '.join(block.replace('\\\n', ' ').split()) for block in re.findall(r'```sh\n(.*?)```', text, re.S)]
        prepare, complete = (next(block for block in blocks if 'routing.py ' + op in block)
                             for op in ('native_prepare', 'native_complete'))
        self.assertIn('../policies/03-agent-delegation.md#managed-projects', text)
        self.assertIn('claude-code.md#native-routing-bridge',
                      (self.ROOT / 'policies' / '03-agent-delegation.md').read_text())
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp).resolve()
            project, home = temp / 'project', temp / 'home'
            project.mkdir()
            home.mkdir(mode=0o700)
            settings = temp / 'routing' / 'settings.json'
            routing_settings.update(settings, dict(managed_projects=[str(project)]), 0)
            stores = '--settings %s --ledger %s' % (settings, temp / 'usage' / 'ledger.sqlite3')
            job = json.dumps(command_tests.job(), separators=(',', ':'))

            def run(command_text):
                command_text = (command_text.replace('/absolute/project/path', '%s %s' % (project, stores))
                                .replace('<job>', job).replace('<new id>', 'doc-1').replace('<id>', 'doc-1')
                                .replace('<task id>', 'task-doc').replace('<assignment scope>', SCOPE))
                done = subprocess.run(['sh', '-c', command_text], cwd=self.ROOT, stdin=subprocess.DEVNULL,
                                      capture_output=True, timeout=60, env=dict(os.environ, HOME=str(home)))
                return json.loads(done.stdout)
            self.assertEqual(run(prepare), dict(v=1, state='blocked', reason='native_binding_unverified'))
            self.assertEqual(run(complete)['state'], 'blocked')  # nothing was admitted, so nothing settles


if __name__ == '__main__':
    unittest.main()
