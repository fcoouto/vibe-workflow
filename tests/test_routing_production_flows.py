"""F4 production data flows on `routing_observations.LocalSource` with real temporary stores (no StaticSource).

This module holds F4.2's P3/P4/P5 cases and F4.3's activation child. P1/P2 launcher traces (F4.4) and the billing
producer cases (F4.5/F4.6) are added by their tasks. No billing-state producer exists yet: nothing here is billing
evidence, and the gateway stays refused (`API_FUNDING_QUALIFIED` is False).
"""
from contextlib import ExitStack, closing
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from runtime import (codex_rate_limits, delegation_routing, opencode_launcher, opencode_subscription,
                     openrouter_settings, routing, routing_launch, routing_observations as collector, routing_paths,
                     routing_settings, subscription_billing, usage_settings)
from scripts import routing as command
from tests import test_opencode_launcher as launcher_tests
from tests.fixture_delegation import AdmissionFixture
from tests.test_routing_command import job
from tests.test_routing_observations import SCOPE, Stores, excluded, kept

CODEX_PATH, CODEX_POOL, CODEX = 'codex-chatgpt-subscription', 'chatgpt-subscription', 'codex-native'
IDENTITY = dict(provider='openai', model='invented-codex-model', effort='high', harness=CODEX)
ROW = 'implementation|medium|medium'  # the row tests.test_routing_command.job() profiles into


def now():
    return int(time.time())


def codex_strategy(sid='s-codex', model='invented-codex-model', effort='high'):
    return dict(strategy_id=sid, model=model, effort=effort, harness=CODEX, provider='openai', evidence_refs=['g'])


def week_window(at, used=100):
    return dict(id='7d', limit=1000, used=used, length=604800, reset_at=at + 3600, rolling=False, observed_at=at,
                unit=collector.WINDOW_UNIT)


class Flow(Stores):
    """A managed checkout pinned to the test's own settings and ledger; every state file lives under its root."""

    def setUp(self):
        super().setUp()
        # No production billing producer exists: a labelled code-level double stands in for this module's flows.
        billing = mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now:
                                     subscription_billing.double('production-flows')(route, now))
        billing.start()
        self.addCleanup(billing.stop)
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(self.checkout)]), 0)
        self.service.close()
        self.service = self.pinned()
        self.addCleanup(self.service.close)
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=True))['state'], 'updated')
        self.guards = [mock.patch.object(subprocess, name, side_effect=AssertionError('process started'))
                       for name in ('Popen', 'run')]  # no reader, client or other process anywhere in these flows
        for guard in self.guards:
            guard.start()
            self.addCleanup(guard.stop)

    def pinned(self, **options):
        return command.Service(self.settings, self.ledger_path, project=self.checkout, **options)

    def qualify(self, path=CODEX_PATH, demand=None):
        return routing_paths.qualify(self.settings, path, demand=demand or {'7d': 5}, evidence='live-1', now=now())

    def table(self, *strategies):
        target = self.settings.parent / 'strategy-table.json'
        if not strategies:
            target.unlink(missing_ok=True)
            return
        target.write_text(json.dumps(dict(schema=2, version='tv-%d' % len(strategies), rows={ROW: list(strategies)})))
        target.chmod(0o600)

    def body(self, attempt, harnesses=(CODEX,), choice=None, **extra):
        assignment = dict(attempt_id=attempt, task='task-' + attempt, role='software-engineer', scope=SCOPE)
        if choice is not None:
            assignment['choice'] = choice
        return dict(v=1, op='prepare', assignment=assignment, job=job(),
                    constraints=dict(allowed_harnesses=list(harnesses)), **extra)

    def prepare(self, attempt, service=None, **options):
        return (service or self.service).handle(json.dumps(self.body(attempt, **options)).encode())

    def one_shot(self, request):
        stdout = io.BytesIO()
        command.main([request['op'], '--settings', str(self.settings), '--ledger', str(self.ledger_path)],
                     io.BytesIO(json.dumps(request).encode()), stdout)
        return json.loads(stdout.getvalue())

    def state_files(self):
        """Every byte of every state file under the test root, by relative path."""
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in sorted(self.root.rglob('*')) if path.is_file()}

    def worker_choices(self, *choices):
        revision = self.call(dict(v=1, op='status'))['settings_revision']
        answer = self.call(dict(v=1, op='set', expected_revision=revision,
                                change=dict(worker_choices={'software-engineer': list(choices)})))
        self.assertEqual(answer['state'], 'updated', answer)


class SubscriptionFlowTests(Flow):
    def test_read_paths_write_nothing(self):
        """Route generation and lookup write no route cache and leave every route input unchanged. Admission may
        write only its ledger; the window is fresh, so no reader is needed."""
        self.qualify()
        self.table(codex_strategy())
        collector.store_window(self.settings, CODEX_POOL, week_window(now()))
        inputs = lambda: {name: blob for name, blob in self.state_files().items()  # noqa: E731
                          if not name.startswith('usage/')}
        before = inputs()
        # The kept ChatGPT route asks the Codex reader outside `--serve`; its reading is fresh, so the reader answers
        # `fresh` without a process (the class guard refuses any process) and stores nothing.
        refresh = mock.Mock(wraps=codex_rate_limits.refresh)
        with mock.patch.object(codex_rate_limits, 'refresh', refresh):
            one_shot = self.prepare('r1', service=self.pinned(one_shot=True))
            serving = self.prepare('r2', service=self.pinned(serving=True))
        self.assertEqual(refresh.call_count, 1)  # the one-shot service only; served preparation never reads
        for answer in (one_shot, serving):
            self.assertEqual(answer['state'], 'admitted', answer)
            self.assertEqual(answer['admission']['route'], CODEX_PATH + ':s-codex')
        self.assertEqual(inputs(), before)  # rules, qualifications, table, windows: unchanged; no route file
        admission = one_shot['admission']
        everything = self.state_files()
        catalog = [routing_launch.route_identity('codex_native', model=IDENTITY['model'], effort='high')]
        from runtime import opencode_launcher
        from scripts import openrouter_proposals
        facts, reason = routing_launch.admitted_check(admission, host='codex_native', catalog=catalog,
                                                      settings_path=self.settings)
        self.assertIsNone(reason)
        self.assertEqual(opencode_launcher._account_of(self.settings, None, admission['route']), 'chatgpt-plan')
        self.assertIs(openrouter_proposals._endpoint(self.settings, admission['route'], IDENTITY, 'ep'), False)
        self.assertEqual(self.state_files(), everything)  # route lookups: every state file byte-identical
        self.assertFalse([name for name in everything if 'route' in Path(name).name and name.endswith('.json')])

    def test_explicit_choice_outside_the_row_still_admits(self):
        self.qualify()
        self.table(codex_strategy())
        collector.store_window(self.settings, CODEX_POOL, week_window(now()))
        outside = dict(IDENTITY, model='invented-unranked-model', funding='subscription')
        answer = self.prepare('e1', choice=outside)
        self.assertEqual(answer['state'], 'admitted', answer)
        self.assertRegex(answer['admission']['route'], r'^codex-chatgpt-subscription:choice:[0-9a-f]{16}$')
        self.assertEqual(answer['admission']['mode'], 'worker_choice')

    def test_settings_choice_covered_by_another_rows_strategy_still_admits(self):
        self.qualify()
        other = dict(codex_strategy('s-other', model='invented-other-model'))
        target = self.settings.parent / 'strategy-table.json'  # the choice is a strategy of another row only
        target.write_text(json.dumps(dict(schema=2, version='tv', rows={'review|low|low': [other]})))
        target.chmod(0o600)
        collector.store_window(self.settings, CODEX_POOL, week_window(now()))
        self.worker_choices(dict(IDENTITY, model='invented-other-model', funding='subscription'))
        answer = self.prepare('w1')
        self.assertEqual(answer['state'], 'admitted', answer)
        self.assertEqual(answer['admission']['mode'], 'worker_choice')
        self.assertIn(':choice:', answer['admission']['route'])  # the choice route, never the strategy route

    def test_settings_choice_admits_without_a_table(self):
        """P4: Smart Routing on, no table file, one qualified path and a seeded window: `worker_choice` mode."""
        self.qualify()
        self.table()
        self.worker_choices(dict(IDENTITY, funding='subscription'))
        seeded = self.one_shot(dict(v=1, op='seed_window', pool=CODEX_POOL, window='7d', used=100, limit=1000,
                                    reset_at=now() + 3600, length=604800, event_id='seed-p4'))
        self.assertEqual(seeded['state'], 'recorded', seeded)
        answer = self.prepare('w2')
        self.assertEqual(answer['state'], 'admitted', answer)
        self.assertEqual(answer['admission']['mode'], 'worker_choice')
        self.assertIn('table_empty', answer['ranking']['reason_codes'])

    def test_seed_needs_a_qualified_path_not_a_table(self):
        """P5: a seed needs a qualified subscription path listing the window; no table is involved."""
        self.table()
        request = dict(v=1, op='seed_window', pool=CODEX_POOL, window='7d', used=100, limit=1000,
                       reset_at=now() + 3600, length=604800, event_id='seed-p5')
        self.assertEqual(self.one_shot(request)['reason'], 'invalid_request')  # no qualified path yet
        self.qualify()
        self.assertEqual(self.one_shot(dict(request, event_id='seed-p5b'))['state'], 'recorded')
        self.assertFalse((self.settings.parent / 'strategy-table.json').exists())
        routing_paths.unqualify(self.settings, CODEX_PATH)
        self.assertEqual(self.one_shot(dict(request, event_id='seed-p5c', window='5h', length=18000,
                                            reset_at=now() + 600))['reason'], 'invalid_request')

    def test_unqualified_path_refuses_a_held_admission_at_dispatch(self):
        """P5: the dispatch check is by path; after `unqualify_path` a held admission on it is refused."""
        self.qualify()
        collector.store_window(self.settings, CODEX_POOL, week_window(now()))
        answer = self.prepare('u1', choice=dict(IDENTITY, funding='subscription'))
        self.assertEqual(answer['state'], 'admitted', answer)
        catalog = [routing_launch.route_identity('codex_native', model=IDENTITY['model'], effort='high')]
        check = lambda: routing_launch.admitted_check(answer['admission'], host='codex_native',  # noqa: E731
                                                      catalog=catalog, settings_path=self.settings)
        self.assertIsNone(check()[1])
        self.assertEqual(self.one_shot(dict(v=1, op='unqualify_path', path=CODEX_PATH))['removed'], True)
        self.assertEqual(check(), (None, routing_launch.ROUTE_NOT_ENTITLED))
        self.qualify()
        self.assertIsNone(check()[1])

    def test_a_strategy_that_left_the_table_still_dispatches_its_held_admission(self):
        self.qualify()
        self.table(codex_strategy())
        collector.store_window(self.settings, CODEX_POOL, week_window(now()))
        answer = self.prepare('t1')
        self.assertEqual((answer['state'], answer['admission']['mode']), ('admitted', 'ranked'), answer)
        self.table(codex_strategy('s-replacement', model='invented-replacement-model'))
        catalog = [routing_launch.route_identity('codex_native', model=IDENTITY['model'], effort='high')]
        facts, reason = routing_launch.admitted_check(answer['admission'], host='codex_native', catalog=catalog,
                                                      settings_path=self.settings)
        self.assertIsNone(reason)
        self.assertEqual(facts['model'], IDENTITY['model'])  # the held identity and the catalog pin what is sent
        sent = self.call(dict(v=1, op='dispatch', attempt_id='t1', request=facts))
        self.assertEqual(sent['state'], 'dispatched', sent)


class GatewayFlowTests(Flow):
    """P3: OpenRouter workers and proposals send an explicit choice carrying the coordinator's endpoint. Production
    OpenRouter stays deferred: no balance producer exists and API funding is unqualified."""

    def setUp(self):
        from tests.openrouter_fixtures import enroll
        self.repo = enroll(self).repository_root
        super().setUp()
        # OpenRouter's own `status` reads the checkout identity from local git, so the class-wide process guard is
        # replaced here by guards on every reader and on the network.
        for guard in self.guards:
            guard.stop()
        import socket
        for target, name in ((codex_rate_limits, 'refresh'), (socket, 'create_connection'),
                             (socket.socket, 'connect')):
            guard = mock.patch.object(target, name, side_effect=AssertionError('reader or network'))
            guard.start()
            self.addCleanup(guard.stop)
        self.service.close()
        self.checkout = self.repo
        routing_settings.update(self.settings, dict(managed_projects=[str(self.repo)]),
                                routing_settings.revision(self.settings))
        self.service = self.pinned()
        self.addCleanup(self.service.close)
        routing_paths.qualify(self.settings, 'openrouter-proposals', demand={'credit': 50_000}, evidence='e', now=now())
        self.fields = dict(model='vendor/invented-model', provider='ep-1', effort=None)

    def choice(self, endpoint='ep-1'):
        return routing_launch.choice('openrouter_proposal', 'api', endpoint=endpoint,
                                     **dict(self.fields, provider=endpoint))

    def collect(self, choice):
        return collector.collect(str(self.repo), dict(scope=routing_launch.assignment_scope(self.repo)),
                                 settings_path=self.settings, ledger_path=self.ledger_path, choices=[choice],
                                 harnesses=['openrouter-proposal'])

    def test_openrouter_choice_keeps_its_endpoint_on_the_local_source(self):
        source = collector.LocalSource(self.settings)
        pinned, other = self.choice('ep-1'), self.choice('ep-2')
        [route] = source.manifest(None, [pinned])
        self.assertEqual((route['endpoint'], route['integration_route'], route['billing_class']),
                         ('ep-1', 'proposals', 'gateway'))
        self.assertNotIn('strategy_id', route)
        identity = {key: pinned[key] for key in routing.ROUTE_IDENTITY}
        self.assertIs(source.pins_endpoint(route['route'], identity, 'ep-1'), True)
        self.assertIs(source.pins_endpoint(route['route'], identity, 'ep-2'), False)
        self.assertNotEqual(source.manifest(None, [other])[0]['route'], route['route'])
        from scripts import openrouter_proposals
        self.assertIs(openrouter_proposals._endpoint(self.settings, route['route'], identity, 'ep-1'), True)
        self.assertIs(openrouter_proposals._endpoint(self.settings, route['route'], identity, 'ep-2'), False)
        self.assertEqual(source.admitted_route(route['route'], identity)['account'], 'fixture-openrouter')

    def test_gateway_route_without_a_balance_producer_stays_out_in_production(self):
        from runtime import opencode_launcher
        from tests.openrouter_fixtures import configure
        self.assertIs(opencode_launcher.API_FUNDING_QUALIFIED, False)
        route = collector.LocalSource(self.settings).manifest(None, [self.choice()])[0]['route']
        self.assertEqual(excluded(self.collect(self.choice())), {route: 'integration_disabled'})  # no other state
        configure(self.repo, routes=('proposals',))  # synthetic local integration readiness only
        self.assertEqual(excluded(self.collect(self.choice())), {route: 'window_unobserved'})
        collector.store_window(self.settings, 'openrouter', dict(  # a credit window, no balance observation
            id='credit', limit=10**12, used=0, length=None, reset_at=None, rolling=False, observed_at=now(),
            organization='fixture-openrouter'))
        self.assertEqual(excluded(self.collect(self.choice())), {route: 'funding_unverified'})
        self.assertEqual(kept(self.collect(self.choice())), {})
        answer = self.prepare('g1', harnesses=['openrouter-proposal'], choice=self.choice())
        self.assertEqual((answer['state'], answer['reason']), ('pending', 'explicit_choice_unavailable'), answer)
        ledger = self.service._ledger(False)
        self.assertEqual(ledger.snapshot()['holds'] if ledger is not None else {}, {})  # nothing admitted or held


class ActivationChildTests(unittest.TestCase):
    """P5: the real `route_manifest` self-check runs in a child with its own empty HOME, so it can reach user state
    only through the settings path the one-shot service passes it."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        from runtime import delegation_routing  # the host check has its own tests (ActivationHostTests)
        hosts = mock.patch.object(delegation_routing, 'enrolled_hosts', return_value=['codex'])
        hosts.start()
        self.addCleanup(hosts.stop)

    def directory(self, name, qualified):
        settings = self.root / name / 'routing' / 'settings.json'
        settings.parent.mkdir(mode=0o700, parents=True)
        if qualified:
            routing_paths.qualify(settings, CODEX_PATH, demand={'7d': 5}, evidence='live-1', now=now())
            target = settings.parent / routing_paths.TABLE_FILE
            target.write_text(json.dumps(dict(schema=2, version='tv-1', rows={ROW: [codex_strategy()]})))
            target.chmod(0o600)
        return settings

    def test_activation_gate_reads_the_services_settings_path(self):
        ready, empty = self.directory('ready', True), self.directory('empty', False)
        capability = next(item for item in command.CAPABILITIES if item[0] == 'route_manifest')
        spawned, real = [], subprocess.Popen

        def spy(argv, **kwargs):
            spawned.append((argv, kwargs))
            return real(argv, **kwargs)
        answers = {}
        with mock.patch.object(subprocess, 'Popen', spy), mock.patch.object(command, 'CAPABILITIES', (capability,)):
            for settings in (ready, empty):
                service = command.Service(settings, settings.parent.parent / 'usage' / 'organizations.sqlite3',
                                          one_shot=True)  # as only `main()` builds it
                self.addCleanup(service.close)
                before = {path: path.read_bytes() for path in settings.parent.iterdir()}
                answers[settings] = service.handle(json.dumps(dict(
                    v=1, op='set', expected_revision=0,
                    change=dict(managed_projects=[str(self.checkout)]))).encode())
                if settings == empty:
                    self.assertEqual({path: path.read_bytes() for path in settings.parent.iterdir()}, before)
        self.assertEqual(answers[ready]['state'], 'updated', answers[ready])
        self.assertEqual(routing_settings.read(ready)['managed_projects'], [str(self.checkout)])
        self.assertEqual(answers[empty], dict(v=1, state='blocked', reason='managed_path_incomplete',
                                              failing=['route_manifest'],
                                              codes={'route_manifest': ['no_qualified_path']}))
        self.assertEqual(routing_settings.revision(empty), 0)
        self.assertEqual([argv[-1] for argv, _ in spawned], [str(ready), str(empty)])  # one real child each
        for argv, kwargs in spawned:
            self.assertEqual(argv[:4], [sys.executable, '-I', '-B', '-c'])
            self.assertEqual(argv[6:8], ['paths', 'runtime.routing_paths:gate'])
            home = Path(kwargs['env']['HOME'])
            self.assertNotEqual(home, Path.home())
            self.assertFalse(Path(argv[-1]).is_relative_to(home))  # the rules come from the package, the rest from here


CLAUDE_PATH, CLAUDE_POOL = 'opencode-claude-subscription', 'claude-subscription'


class CodexDelegationFlowTests(AdmissionFixture, unittest.TestCase):
    """P2 (F4.4): Codex native delegation preparation (`delegation.prepare_assignment` → `delegation_routing.prepare`
    → the in-process `managed_service`) on `LocalSource`, with the shipped path rules, real temporary routing stores
    and the existing native fixture catalog and task bindings. Preparation proof only: the coordinator's native spawn
    (P9) and any billing boundary are not exercised, and no provider request is sent."""

    def setUp(self):
        super().setUp()
        # No production billing producer exists: a labelled code-level double stands in for this module's flows.
        billing = mock.patch.object(collector.LocalSource, 'billing', lambda source, route, now:
                                     subscription_billing.double('codex-delegation-flows')(route, now))
        billing.start()
        self.addCleanup(billing.stop)
        self.plan()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name).resolve()
        self.settings, self.ledger = base / 'routing' / 'settings.json', base / 'ledger' / 'organizations.sqlite3'
        for target, name, value in ((command, 'default_settings', lambda: self.settings),
                                    (command, 'default_ledger', lambda: self.ledger),
                                    (usage_settings, 'directory', lambda: base / 'usage'),
                                    (openrouter_settings, 'BASIC_STORAGE', base / 'openrouter-consent')):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        routing_settings.update(self.settings, dict(managed_projects=[str(self.repo.resolve())]), 0)
        service = command.Service(self.settings, self.ledger)
        self.addCleanup(service.close)
        self.assertEqual(service.handle(json.dumps(dict(v=1, op='set', authority_required=True)).encode())['state'],
                         'updated')
        self.pools = []
        real = collector.LocalSource.windows

        def windows(source, pool, now, **read_once):  # `stored`: the collector's one store read (F4R-09)
            self.pools.append(pool)
            return real(source, pool, now, **read_once)
        refuse = mock.Mock(side_effect=AssertionError('no Codex reader process or network call in P2'))
        for patcher in (mock.patch.object(collector.LocalSource, 'windows', windows),
                        mock.patch.object(codex_rate_limits, 'read_windows', refuse),
                        mock.patch.object(socket.socket, 'connect', refuse)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def seed(self, pool, window, length, event_id):
        """The one-shot operator `seed_window`, as the runbook runs it."""
        request = dict(v=1, op='seed_window', pool=pool, window=window, used=100, limit=1000, reset_at=now() + 3600,
                       length=length, event_id=event_id)
        stdout = io.BytesIO()
        command.main(['seed_window', '--settings', str(self.settings), '--ledger', str(self.ledger)],
                     io.BytesIO(json.dumps(request).encode()), stdout)
        answer = json.loads(stdout.getvalue())
        self.assertEqual(answer['state'], 'recorded', answer)

    def qualify(self, path, demand):
        routing_paths.qualify(self.settings, path, demand=demand, evidence='live-1', now=now())

    def admission(self, attempt_id):
        with closing(command.Service(self.settings, self.ledger)) as service:
            data = service._ledger(False).snapshot()
        return next(record for record in data['admissions'] if record['attempt_id'] == attempt_id)

    def test_the_native_bridge_never_settles_a_managed_delegation_attempt(self):
        """F4R-06: an attempt admitted by Codex delegation preparation (workflow-bound, on the native Codex path) keeps
        its own observe/settle flow; `native_complete` refuses it and changes no ledger, hold or boundary."""
        self.qualify(CODEX_PATH, {'7d': 5})
        self.seed(CODEX_POOL, '7d', 604800, 'seed-codex-7d')
        prepared = self.prepare(dispatch_id='bridge-refusal')
        self.assertTrue(prepared['launch_admitted'], prepared)
        attempt = prepared['attempt_id']
        with closing(command.Service(self.settings, self.ledger)) as service:
            before = service._ledger(False).snapshot()
        stdout = io.BytesIO()
        command.main(['native_complete', '--settings', str(self.settings), '--ledger', str(self.ledger), '--project',
                      str(self.repo)], io.BytesIO(json.dumps(dict(v=1, op='native_complete', attempt_id=attempt,
                                                                    outcome=dict(kind='complete'))).encode()), stdout)
        answer = json.loads(stdout.getvalue())
        self.assertEqual((answer['state'], answer['reason']), ('blocked', 'managed_delegation_required'), answer)
        with closing(command.Service(self.settings, self.ledger)) as service:
            self.assertEqual(service._ledger(False).snapshot(), before)
        self.assertEqual(before['holds'][attempt]['state'], 'dispatched')
        self.assertEqual(collector.consumed_at(self.settings, CODEX_POOL), 0)

    def test_codex_explicit_choice_admits_on_a_qualified_path(self):
        first = self.prepare(dispatch_id='explicit-1')  # no path qualified yet: nothing to admit, no substitute
        self.assertFalse(first['launch_admitted'])
        self.assertEqual(first['routing'], dict(state='pending', reason='explicit_choice_unavailable'))
        delegation_routing.release(self.scope, self.prepare_request(dispatch_id='explicit-1'), first['attempt_id'],
                                   'routing_pending')
        self.qualify(CODEX_PATH, {'7d': 5})
        self.seed(CODEX_POOL, '7d', 604800, 'seed-codex-7d')
        prepared = self.prepare(dispatch_id='explicit-2', retry_of=first['attempt_id'], diagnostic=dict(
            prior_attempt_id=first['attempt_id'], cause='routing', evidence=['routing:released'],
            change='Retry after the Codex path was qualified'))
        self.assertTrue(prepared['launch_admitted'], prepared)
        self.assertEqual(prepared['routing'], dict(state='admitted', mode='worker_choice'))
        self.assertEqual(prepared['route'], dict(model='new-native-model', effort='high',
                                                 attempt_id=prepared['attempt_id']))
        admission = self.admission(prepared['attempt_id'])
        self.assertEqual(admission['route'], routing_paths.choice_id(CODEX_PATH, 'openai', 'new-native-model', 'high',
                                                                     CODEX))
        self.assertEqual(admission['route_identity'], routing.route_identity(
            *routing_launch.route_identity('codex_native', model='new-native-model', effort='high').values()))
        self.assertEqual(set(self.pools), {CODEX_POOL})  # the host's own pool only

    def test_codex_routed_admission_considers_only_codex_routes(self):
        self.qualify(CLAUDE_PATH, {'5h': 5, '7d': 5})
        self.qualify(CODEX_PATH, {'7d': 5})
        for window, length in (('5h', 18000), ('7d', 604800)):
            self.seed(CLAUDE_POOL, window, length, 'seed-claude-' + window)  # the Claude path could admit too
        self.seed(CODEX_POOL, '7d', 604800, 'seed-codex-7d')
        profile = routing.ensure_profile(dict(id='edit', role='software-engineer', scope_ref='s'))
        row = '|'.join((profile['work_kind'], profile['complexity_band'], profile['risk_band']))
        claude = dict(strategy_id='s-claude', model='invented-claude-model', effort='high',
                      harness='claude-agent-sdk via opencode', provider='claude-agent', evidence_refs=['g'])
        target = self.settings.parent / 'strategy-table.json'  # the Claude strategy ranks first
        target.write_text(json.dumps(dict(schema=2, version='tv-p2', rows={row: [
            claude, codex_strategy(model='new-native-model')]})))
        target.chmod(0o600)
        request = self.prepare_request(dispatch_id='routed-1')
        request['selection'] = dict(rationale='Routing chooses the worker', profile_ids=['bounded'], mode='routed')
        prepared = self.api.prepare_assignment(self.scope, request)
        self.assertTrue(prepared['launch_admitted'], prepared)
        self.assertEqual(prepared['routing'], dict(state='admitted', mode='ranked'))
        self.assertEqual(prepared['route']['model'], 'new-native-model')
        admission = self.admission(prepared['attempt_id'])
        self.assertEqual(admission['route'], CODEX_PATH + ':s-codex')
        self.assertNotIn('s-claude', json.dumps(admission))  # the host filter ran before ranking and the cut
        self.assertNotIn(CLAUDE_POOL, self.pools)  # the foreign pool's windows were never read
        self.assertEqual(set(self.pools), {CODEX_POOL})


GO_PATH, CLAUDE_HARNESS = 'opencode-go-subscription', 'claude-agent-sdk via opencode'
CLAUDE_MODEL = 'invented-claude-small'  # an invented OpenCode Claude model
WORKSPACE = 'wrk_invented'


class GoHost(launcher_tests.FakeHost):
    """The OpenCode server double with the installed `GET /api/experimental/usage/limits` envelope added. Every other
    request is the shared double's; an unexpected path still fails the test."""

    def __init__(self, *args, **knobs):
        super().__init__(*args, **knobs)
        self.models = [dict(providerID='claude-agent', id=CLAUDE_MODEL, variants=['low']),
                       dict(providerID='openai', id='invented-chatgpt-model', variants=['high']),
                       dict(providerID='opencode-go', id='invented-go-one', variants=['high']),
                       dict(providerID='opencode-go', id='invented-go-two', variants=['high'])]
        self.usage, self.usage_reads = None, 0

    def request(self, method, path, body=None, query=None):
        if (method, path) == ('GET', '/api/experimental/usage/limits'):
            self.usage_reads += 1
            return 200, self.usage
        return super().request(method, path, body, query)


def go_usage(at, percent=(1, 2, 3), limited=(False, False, False), workspace=WORKSPACE):
    return dict(location=dict(directory='/invented'), data=dict(
        workspace=dict(id=workspace, usageURL='https://example.invalid/usage'), budgets=[],
        go={name: dict(percent=value, limited=flag, resetsAt=(at + 3600) * 1000)
            for name, value, flag in zip(('rolling', 'weekly', 'monthly'), percent, limited)}))


class OpenCodeLaunchFlowTests(Stores):
    """P8 (F4.5) and P1 (F4.4): ranked OpenCode launches through `opencode_launcher.launch`, the real routing
    service, `LocalSource`, the shipped rules and temporary stores, with the host double as the only transport.

    Not qualified in production and supplied here only by labelled code-level doubles: the subscription billing fact,
    the Go per-model demand mapping and the Go meter semantics/observation-time binding. Ranking, allocation, host
    filtering, admission, the identity check, dispatch and settlement are the real ones. A positive run proves the
    package flow, never an installed Go binding or a release claim."""

    def setUp(self):
        super().setUp()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        routing_settings.update(self.settings, dict(managed_projects=[str(self.checkout)]), 0)
        self.service.close()
        self.service = command.Service(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(self.service.close)
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=True))['state'], 'updated')
        self.shim = command.Service(self.settings, self.ledger_path, serving=True)
        self.addCleanup(self.shim.close)
        self.order = []
        self.host = GoHost(self.shim, str(self.checkout), self.order)
        self.job = routing_launch.unplanned_job('software-engineer', SCOPE, 'Do the fixture task')
        profile = routing.ensure_profile(self.job)
        self.row = '|'.join((profile['work_kind'], profile['complexity_band'], profile['risk_band']))
        for patch in (mock.patch.object(opencode_launcher, 'IDLE_POLL', 0),
                      mock.patch.object(opencode_launcher, 'REREAD_WAIT', 0),
                      mock.patch.object(codex_rate_limits, 'read_windows',
                                        side_effect=AssertionError('no Codex reader in an OpenCode launch'))):
            patch.start()
            self.addCleanup(patch.stop)

    def doubles(self, billing='blocked', demand=True, semantics=True, enter=None):
        """Labelled code-level doubles (`billing`: 'blocked' gives a current provider-blocked fact, 'enabled' one
        that allows paid crossover, None none), entered with `enter` (default: for the whole test)."""
        enter = enter or self.enterContext
        if billing is not None:
            def fact(source, route, now):
                value = subscription_billing.double('p8')(route, now)
                return value if billing == 'blocked' else dict(value, paid_crossover=billing)
            enter(mock.patch.object(collector.LocalSource, 'billing', fact))
        if demand:
            enter(mock.patch.object(collector.LocalSource, 'model_demand', lambda source, route:
                                    route.get('model') in ('invented-go-one', 'invented-go-two')))
        if semantics:
            from tests.test_opencode_subscription_wiring import semantics_double
            enter(mock.patch.object(opencode_subscription, 'window_semantics', lambda: semantics_double))

    def qualify_go(self, demand=5):
        routing_paths.qualify(self.settings, GO_PATH, demand=dict(rolling=demand, weekly=demand, monthly=demand),
                              evidence='synthetic-1', account=opencode_subscription.account_label(WORKSPACE), now=now())

    def qualify_others(self):
        """The Claude and ChatGPT paths qualified with stored fresh readings that are fuller and reset later."""
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=now())
        routing_paths.qualify(self.settings, 'opencode-chatgpt-subscription', demand={'7d': 5}, evidence='synthetic-1',
                              now=now())
        for pool, ids in (('claude-subscription', (('5h', 18000), ('7d', 604800))),
                          ('chatgpt-subscription', (('7d', 604800),))):
            for window, length in ids:
                collector.store_window(self.settings, pool, dict(id=window, limit=1000, used=800, length=length,
                                                                 reset_at=now() + 4 * 3600, rolling=False,
                                                                 observed_at=now(), unit=collector.WINDOW_UNIT))

    def table(self, *strategies):
        target = self.settings.parent / 'strategy-table.json'
        target.write_text(json.dumps(dict(schema=2, version='tv-%d' % len(strategies),
                                          rows={self.row: list(strategies)})))
        target.chmod(0o600)

    @staticmethod
    def strategy(sid, model, provider, harness, effort='high'):
        return dict(strategy_id=sid, model=model, effort=effort, harness=harness, provider=provider,
                    evidence_refs=['g'])

    def go_strategies(self):
        harness = routing_launch.shim_harness('opencode-go')
        return [self.strategy('s-go1', 'invented-go-one', 'opencode-go', harness),
                self.strategy('s-go2', 'invented-go-two', 'opencode-go', harness)]

    def launch(self, attempt='a1'):
        assignment = dict(attempt_id=attempt, task='task-' + attempt,
                          scope=routing_launch.assignment_scope(self.checkout))
        return opencode_launcher.launch(self.checkout, host=self.host, assignment=assignment, job=self.job,
                                        prompt='Do the fixture task', service=self.service)

    def holds(self):
        ledger = self.service._ledger(False)
        return {} if ledger is None else ledger.snapshot()['holds']

    def assert_sent_nothing(self, result, attempt):
        """No session, no prompt and no open hold for `attempt`."""
        self.assertNotEqual(result.get('state'), 'recorded', result)
        self.assertNotIn('create', self.order)
        self.assertNotIn('prompt', self.order)
        self.assertNotIn(self.holds().get(attempt, {}).get('state'), ('admitted', 'dispatched'))

    # -- P8 ------------------------------------------------------------------------------------------------------
    def test_opencode_subscription_ranked_launch_on_the_local_source(self):
        self.doubles()
        self.qualify_go()
        self.qualify_others()
        claude = self.strategy('s-claude', CLAUDE_MODEL, 'claude-agent', CLAUDE_HARNESS, effort='low')
        chatgpt = self.strategy('s-chatgpt', 'invented-chatgpt-model', 'openai', 'opencode provider openai')
        self.table(claude, chatgpt, *self.go_strategies())  # the third family is ranked last; pressure decides
        self.host.usage = go_usage(now())
        result = self.launch()
        self.assertEqual(result['state'], 'recorded', result)
        admission = result['admission']
        self.assertEqual((admission['route'], admission['mode'], admission['billing_class']),
                         (GO_PATH + ':s-go1', 'ranked', 'included_subscription'))
        self.assertEqual(self.host.created['model'],
                         dict(id='invented-go-one', providerID='opencode-go', variant='high'))
        self.assertEqual(self.host.usage_reads, 1)  # one bounded read for the kept stale Go route
        self.assertEqual({key for key in admission['reserved']},
                         {'win:%s/%s' % (GO_PATH, name) for name in ('rolling', 'weekly', 'monthly')})
        self.assertEqual(self.holds()['a1']['state'], 'released')  # `record complete` settled it

    def test_every_go_constraint_and_the_shared_allowance_limit_the_launch(self):
        self.doubles()
        self.qualify_go()
        self.table(*self.go_strategies())  # only one connection qualified: routing still works with it alone
        for index, name in enumerate(('rolling', 'weekly', 'monthly')):
            with self.subTest(full=name):
                percent = [1, 1, 1]
                percent[index] = 99.9  # 999 of 1000 used: one window too full for the demand
                self.host.usage = go_usage(now(), percent=percent)
                collector_path = collector.window_path(self.settings)
                collector_path.unlink(missing_ok=True)
                self.assert_sent_nothing(self.launch('full-' + name), 'full-' + name)
        collector.window_path(self.settings).unlink(missing_ok=True)
        self.host.usage = go_usage(now(), percent=(99.2, 1, 1))  # room for one reservation, not two
        opencode_subscription.refresh(self.service, lambda: (200, self.host.usage), routing_launch.prepare_request(
            dict(attempt_id='held', task='task-held', scope=routing_launch.assignment_scope(self.checkout)), self.job,
            harnesses=[routing_launch.shim_harness('opencode-go')]), now=now())
        held = self.call(routing_launch.prepare_request(
            dict(attempt_id='held', task='task-held', scope=routing_launch.assignment_scope(self.checkout)), self.job,
            harnesses=[routing_launch.shim_harness('opencode-go')]))
        self.assertEqual((held['state'], held['admission']['route']), ('admitted', GO_PATH + ':s-go1'), held)
        self.assert_sent_nothing(self.launch('alias'), 'alias')  # the second alias draws on the same held allowance

    def test_a_go_connection_without_its_producers_sends_nothing(self):
        """Real absent producers, unknown or paid-crossover billing, no demand mapping, an exhausted meter and a host
        without the Go connection: nothing is sent. Without a kept route or a qualified binding nothing is read."""
        self.qualify_go()
        self.table(*self.go_strategies())
        cases = (('real producers', None, False), ('unknown billing', dict(billing=None), False),
                 ('paid crossover', dict(billing='enabled'), False),
                 ('unknown demand mapping', dict(demand=False), False),
                 ('exhausted', {}, True), ('disconnected', {}, False))
        models = self.host.models
        for name, doubles, reads in cases:
            attempt = 'none-' + name.replace(' ', '-')
            with self.subTest(case=name), ExitStack() as stack:
                if doubles is not None:
                    self.doubles(enter=stack.enter_context, **doubles)
                self.host.usage = go_usage(now(), limited=(False, name == 'exhausted', False))
                if name == 'disconnected':
                    self.host.models = [item for item in models if item['providerID'] != 'opencode-go']
                collector.window_path(self.settings).unlink(missing_ok=True)
                before = self.host.usage_reads
                self.assert_sent_nothing(self.launch(attempt), attempt)
                self.assertEqual(self.host.usage_reads - before, 1 if reads else 0)
                self.host.models = models

    # -- F4R-01: completions that report no covering reading -----------------------------------------------------
    def claude_only(self):
        self.doubles(demand=False, semantics=False)
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=now())
        for window, length in (('5h', 18000), ('7d', 604800)):
            collector.store_window(self.settings, 'claude-subscription', dict(
                id=window, limit=1000, used=100, length=length, reset_at=now() + 3600, rolling=False,
                observed_at=now() - 5, unit=collector.WINDOW_UNIT))
        self.table(self.strategy('s-claude', CLAUDE_MODEL, 'claude-agent', CLAUDE_HARNESS, effort='low'))

    def test_a_completion_without_a_reading_makes_the_pool_stale_for_every_alias(self):
        self.claude_only()
        self.host.rate_limit = None  # the launch reports no reading
        store = collector.window_path(self.settings)
        before = json.loads(store.read_bytes())['pools']
        self.assertEqual(self.launch('miss-1')['state'], 'recorded')
        self.assertEqual(json.loads(store.read_bytes())['pools'], before)  # nothing invented
        self.assertGreater(collector.consumed_at(self.settings, 'claude-subscription'), 0)
        second = self.launch('miss-2')
        self.assertEqual(second['state'], 'pending', second)  # not the same pre-launch headroom
        self.assertEqual(self.order.count('create'), 1)
        self.assertEqual(collector.consumed_at(self.settings, 'chatgpt-subscription'), 0)  # other pools untouched

    def test_a_partial_reading_marks_the_pool_too(self):
        self.claude_only()
        resets = now() + 3000
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=resets, unifiedWindows=dict(
            five_hour=dict(utilization=0.25, resetsAt=resets, status='allowed')))  # 5h only: 7d is not covered
        self.assertEqual(self.launch('part-1')['state'], 'recorded')
        self.assertGreater(collector.consumed_at(self.settings, 'claude-subscription'), 0)
        self.assertEqual(self.launch('part-2')['state'], 'pending')

    def test_a_covering_settled_reading_keeps_the_pool_usable(self):
        self.claude_only()
        resets = now() + 3000
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=resets, unifiedWindows=dict(
            five_hour=dict(utilization=0.25, resetsAt=resets, status='allowed'),
            seven_day=dict(utilization=0.1, resetsAt=resets + 86400, status='allowed')))
        self.assertEqual(self.launch('cover-1')['state'], 'recorded')
        self.assertEqual(collector.consumed_at(self.settings, 'claude-subscription'), 0)  # its own reading counts
        self.assertEqual(self.launch('cover-2')['state'], 'recorded')

    def covering(self, five_hour, seven_day):
        resets = now() + 3000
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=resets, unifiedWindows=dict(
            five_hour=dict(utilization=five_hour, resetsAt=resets, status='allowed'),
            seven_day=dict(utilization=seven_day, resetsAt=resets + 86400, status='allowed')))

    def test_a_whole_covering_completion_between_collection_and_admission_is_seen(self):
        """F4R-09 (covering readings): `late` collects the pool at 100/1000, then `early` runs a whole launch whose own
        settled readings (999/1000, covering both claims) are stored and whose hold is released, then `late` admits.
        No consumed boundary moved; the admission still sees that the pool's stored readings changed: pending."""
        self.claude_only()
        self.covering(0.999, 0.999)
        real, order = routing.admit, []

        def interleave(assignment, *args, **kwargs):
            if assignment['attempt_id'] == 'late' and not order:
                order.append('early')
                self.assertEqual(self.launch('early')['state'], 'recorded')  # after `late` collected
                self.assertEqual(collector.consumed_at(self.settings, 'claude-subscription'), 0)  # covered: no mark
            return real(assignment, *args, **kwargs)
        with mock.patch.object(routing, 'admit', interleave):
            late = self.launch('late')
        self.assertEqual(order, ['early'])
        self.assertEqual(late['state'], 'pending', late)  # never the 100/1000 headroom collected before
        self.assertEqual(self.order.count('create'), 1)

    def test_another_pools_reading_between_collection_and_admission_changes_nothing(self):
        """The recheck is per pool: a reading stored for another pool in between leaves this snapshot usable."""
        self.claude_only()
        self.covering(0.25, 0.1)
        real = routing.admit

        def interleave(assignment, *args, **kwargs):
            collector.store_window(self.settings, 'chatgpt-subscription', dict(
                id='7d', limit=1000, used=500, length=604800, reset_at=now() + 3600, rolling=False,
                observed_at=now(), unit=collector.WINDOW_UNIT))
            return real(assignment, *args, **kwargs)
        with mock.patch.object(routing, 'admit', interleave):
            self.assertEqual(self.launch('other-pool')['state'], 'recorded')

    def stale_reading(self, observed_at):
        """A launch whose reported reading is an older cached one (labelled double: only its time is older)."""
        real = opencode_launcher.windows
        return mock.patch.object(opencode_launcher, 'windows', lambda observation: [
            dict(window, observed_at=observed_at) for window in real(observation)])

    def test_an_older_cached_reading_that_leaves_the_store_unchanged_covers_nothing(self):
        """F4R-01: the store keeps its newer reading, so the launch's ids alone are not coverage; the pool is marked."""
        self.claude_only()  # readings observed 5 s ago
        self.covering(0.25, 0.1)
        store = collector.window_path(self.settings)
        before = json.loads(store.read_bytes())['pools']
        with self.stale_reading(now() - 60):
            self.assertEqual(self.launch('cached-1')['state'], 'recorded')
        self.assertEqual(json.loads(store.read_bytes())['pools'], before)  # nothing replaced, nothing redated
        self.assertGreater(collector.consumed_at(self.settings, 'claude-subscription'), 0)
        self.assertEqual(self.launch('cached-2')['state'], 'pending')

    def test_a_reading_observed_before_the_admission_covers_nothing(self):
        """A stored reading older than the admission cannot show that launch's use, even when it replaced an older
        one."""
        self.claude_only()  # readings observed 5 s ago
        self.covering(0.25, 0.1)
        with self.stale_reading(now() - 2):
            self.assertEqual(self.launch('before-1')['state'], 'recorded')
        self.assertGreater(collector.consumed_at(self.settings, 'claude-subscription'), 0)

    def test_a_reading_the_store_did_not_keep_covers_nothing(self):
        """Store result, not ids: another writer's reading of the same window and second is kept, so the launch's own
        reading (same time, other content) was not stored and covers nothing, though it is not older than the
        admission."""
        self.claude_only()
        self.covering(0.25, 0.1)
        real, at = opencode_launcher.windows, now()

        def superseded(observation):
            found = [dict(window, observed_at=at) for window in real(observation)]
            for window in found:  # the other writer's reading lands first, in the same second
                collector.store_window(self.settings, 'claude-subscription', dict(window, used=window['used'] + 1))
            return found
        with mock.patch.object(opencode_launcher, 'windows', superseded):
            self.assertEqual(self.launch('kept-1')['state'], 'recorded')
        self.assertGreater(collector.consumed_at(self.settings, 'claude-subscription'), 0)

    def test_a_failed_boundary_write_keeps_the_capacity_held(self):
        self.claude_only()
        self.host.rate_limit = None
        with mock.patch.object(collector, 'mark_consumed', side_effect=OSError('store unwritable')):
            result = self.launch('fail-1')
        self.assertEqual(result['state'], 'uncertain', result)  # never released: the operator reconciles it
        self.assertEqual(self.holds()['fail-1']['state'], 'uncertain')

    def test_a_refused_attach_used_nothing_and_marks_nothing(self):
        self.claude_only()
        self.host.attach_ok = False  # no session is created and nothing is sent
        self.assertEqual(self.launch('attach-1')['reason'], 'attach_failed')
        self.assertEqual(collector.consumed_at(self.settings, 'claude-subscription'), 0)
        self.host.attach_ok = True
        self.host.rate_limit = None
        self.assertEqual(self.launch('attach-2')['state'], 'recorded')  # the pool was still usable

    def test_a_go_completion_marks_its_pool(self):
        self.doubles()
        self.qualify_go()
        self.table(*self.go_strategies())
        self.host.usage = go_usage(now())
        self.assertEqual(self.launch('go-1')['state'], 'recorded')
        self.assertGreater(collector.consumed_at(self.settings, GO_PATH), 0)  # a Go launch reports no reading
        self.assertEqual(self.launch('go-2')['state'], 'pending')  # the bound read finds the pool fresh-but-used

    # -- F5.3: ranked funding never selects a paid route ---------------------------------------------------------
    def test_opencode_ranked_admission_never_selects_a_gateway_route(self):
        """Both path types are qualified and ranked (the gateway first), the host lists both providers and the
        Claude subscription is full: the launch waits, with no dollar hold and no dispatch. With a ChatGPT
        subscription that fits, the actual launcher sends exactly its admitted identity and settles once."""
        self.doubles(demand=False, semantics=False)
        routing_paths.qualify(self.settings, 'openrouter-workers', demand={'credit': 5}, evidence='synthetic-1',
                              now=now())
        routing_paths.qualify(self.settings, CLAUDE_PATH, demand={'5h': 5, '7d': 5}, evidence='synthetic-1', now=now())
        for window, length in (('5h', 18000), ('7d', 604800)):
            collector.store_window(self.settings, 'claude-subscription', dict(
                id=window, limit=1000, used=1000, length=length, reset_at=now() + 3600, rolling=False,
                observed_at=now(), unit=collector.WINDOW_UNIT))
        self.host.models.append(dict(providerID='openrouter', id='invented-or-model', variants=['high']))
        gateway = self.strategy('s-gw', 'invented-or-model', 'openrouter', routing_launch.shim_harness('openrouter'))
        claude = self.strategy('s-claude', CLAUDE_MODEL, 'claude-agent', CLAUDE_HARNESS, effort='low')
        self.table(gateway, claude)
        waiting = self.launch('only-paid-fits')
        self.assertEqual(waiting['state'], 'pending', waiting)
        self.assert_sent_nothing(waiting, 'only-paid-fits')
        self.assertEqual([hold for hold in self.holds().values() if hold.get('amount') or hold.get('organization')],
                         [])  # no dollar hold anywhere
        self.assertNotIn('dispatched', {hold['state'] for hold in self.holds().values()})
        routing_paths.qualify(self.settings, 'opencode-chatgpt-subscription', demand={'7d': 5}, evidence='synthetic-1',
                              now=now())
        collector.store_window(self.settings, 'chatgpt-subscription', dict(
            id='7d', limit=1000, used=100, length=604800, reset_at=now() + 3600, rolling=False, observed_at=now(),
            unit=collector.WINDOW_UNIT))
        chatgpt = self.strategy('s-chatgpt', 'invented-chatgpt-model', 'openai', 'opencode provider openai')
        self.table(gateway, claude, chatgpt)
        sent = self.launch('subscription-fits')
        self.assertEqual(sent['state'], 'recorded', sent)
        admission = sent['admission']
        self.assertEqual((admission['route'], admission['class'], admission['billing_class'], admission['mode']),
                         ('opencode-chatgpt-subscription:s-chatgpt', 'subscription', 'included_subscription', 'ranked'))
        self.assertEqual(self.host.created['model'], dict(id='invented-chatgpt-model', providerID='openai',
                                                          variant='high'))
        self.assertEqual((self.order.count('create'), self.order.count('prompt')), (1, 1))
        self.assertEqual(self.holds()['subscription-fits']['state'], 'released')  # settled once

    # -- P1 ------------------------------------------------------------------------------------------------------
    def test_opencode_ranked_launch_on_the_local_source(self):
        """P1 with synthetic billing: no window → pending; recovery seed → launch and settled reading; fresh
        follow-up without a seed; after a gap over 900 s with no qualified Claude reader → pending, stale. The
        automatic qualified-reader branch stays open (F4.6 source gate)."""
        self.doubles(demand=False, semantics=False)
        routing_paths.qualify(self.settings, 'opencode-claude-subscription', demand={'5h': 5, '7d': 5},
                              evidence='synthetic-1', now=now())
        self.table(self.strategy('s-claude', CLAUDE_MODEL, 'claude-agent', CLAUDE_HARNESS, effort='low'))
        excluded, admitted = [], []  # the collector's and the admission's own reasons (the answer reports the dominant)
        real_collect, real_admit = collector.collect, routing.admit

        def collect(*args, **kwargs):
            result = real_collect(*args, **kwargs)
            excluded.append({item['route']: item['reason'] for item in result['observations']['excluded']})
            return result

        def admit(*args, **kwargs):
            admitted.append(real_admit(*args, **kwargs))
            return admitted[-1]
        self.enterContext(mock.patch.object(collector, 'collect', collect))
        self.enterContext(mock.patch.object(routing, 'admit', admit))
        first = self.launch('p1-a')
        self.assertEqual((first['state'], first['reason']), ('pending', 'no_feasible_route'), first)
        self.assertEqual(excluded[-1], {'opencode-claude-subscription:s-claude': 'window_unobserved'})
        self.assert_sent_nothing(first, 'p1-a')
        for window, length in (('5h', 18000), ('7d', 604800)):
            seeded = self.one_shot(dict(v=1, op='seed_window', pool='claude-subscription', window=window, used=100,
                                        limit=1000, reset_at=now() + 3600, length=length, event_id='seed-' + window))
            self.assertEqual(seeded['state'], 'recorded', seeded)
        resets = now() + 3000
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=resets, unifiedWindows=dict(
            five_hour=dict(utilization=0.25, resetsAt=resets, status='allowed'),
            seven_day=dict(utilization=0.1, resetsAt=resets + 86400, status='allowed')))
        second = self.launch('p1-b')
        self.assertEqual(second['state'], 'recorded', second)
        self.assertEqual((admitted[-1]['class'], admitted[-1]['mode'], admitted[-1]['shares']),
                         ('subscription', 'ranked', dict(api=0.0, subscription=1.0)))  # F5.3: subscription funding
        stored = {item['id']: item
                  for item in collector.LocalSource(self.settings).windows('claude-subscription', now())}
        self.assertEqual({key: (item['used'], item.get('source')) for key, item in stored.items()},
                         {'5h': (250, None), '7d': (100, None)})  # the settled host reading replaced both seeds
        self.host.rate_limit = None
        third = self.launch('p1-c')
        self.assertEqual(third['state'], 'recorded', third)  # fresh: no seed needed
        created = self.order.count('create')
        later = time.time() + 1000
        with mock.patch('time.time', lambda: later):
            fourth = self.launch('p1-d')
        self.assertEqual(fourth['state'], 'pending', fourth)
        self.assertIn('stale_observation', admitted[-1]['reason_codes'])  # the route is kept but its reading is stale
        self.assertEqual(self.order.count('create'), created)
        self.assertEqual(self.host.usage_reads, 0)  # no Go read for a Claude-only request

    def one_shot(self, request):
        stdout = io.BytesIO()
        command.main([request['op'], '--settings', str(self.settings), '--ledger', str(self.ledger_path)],
                     io.BytesIO(json.dumps(request).encode()), stdout)
        return json.loads(stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
