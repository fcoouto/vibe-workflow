"""S3.5 (RF3): fresh gates at dispatch re-validate the admission's workflow binding against current state."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest import mock

from runtime import routing, routing_gates, routing_settings, usage_budget as budget
from scripts import routing as command
from tests.test_routing_command import (API, KEY_REF, SCOPE, SECRET, Base, api_observations, facts, job, now,
                                        observations, prepare, table)
from tests.test_routing_dispatch import DispatchBase, dispatch

M = 1_000_000


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def locator(job_id='build', **extra):
    value = dict(client='codex', cwd='/work/repo', session_id='parent', task_id='root-1', job_id=job_id)
    value.update(extra)
    return value


def semantic(**extra):
    """The canonical job's semantic fields; `job()` from the command tests describes the same job."""
    value = dict(role='worker', scope_ref=SCOPE, objective=SECRET, context=SECRET)
    value.update(extra)
    return value


class FakeWorkflow:
    """Canonical workflow state stand-in: one private JSON file of jobs. The gates may only read it.

    A job: {semantic, claims [[key, mode]], dependencies [ids], state ('waiting'|'running'|'accepted'), revision,
    open, resource ({lease, active} or None)}. `running` jobs hold their write claims.
    """
    def __init__(self, path):
        self.path = path
        self.views = self.etags = 0
        self.fail = None

    def write(self, jobs):
        self.path.write_text(json.dumps(dict(jobs=jobs)))
        self.path.chmod(0o600)

    def jobs(self):
        return json.loads(self.path.read_text())['jobs']

    def update(self, name, **change):
        jobs = self.jobs()
        jobs[name].update(change)
        self.write(jobs)

    def etag(self, where):
        self.etags += 1
        if self.fail == 'etag':
            raise RuntimeError('etag failed')
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def view(self, where):
        self.views += 1
        if self.fail == 'view':
            raise RuntimeError('view failed')
        jobs = self.jobs()
        etag = hashlib.sha256(self.path.read_bytes()).hexdigest()
        found = jobs.get(where['job_id'])
        if found is None:
            return dict(etag=etag, job=None, dependencies=None, ownership=None, resource=None)
        closure, pending = {}, list(found['dependencies'])
        while pending:
            name = pending.pop()
            if name not in closure:
                closure[name] = jobs[name]
                pending.extend(jobs[name]['dependencies'])
        accepted = all(item['state'] == 'accepted' for item in closure.values())
        dependencies = dict(accepted=accepted, count=len(closure) if accepted else 0,
                            digest=sha(sorted([name, item['revision']] for name, item in closure.items()))
                            if accepted else None)
        mine = {key for key, mode in found['claims']}
        conflict = any(other['state'] == 'running' and any(
            key in mine and (mode == 'write' or dict(found['claims']).get(key) == 'write')
            for key, mode in other['claims']) for name, other in jobs.items() if name != where['job_id'])
        resource = found.get('resource')
        return dict(etag=etag,
                    job=dict(role=found['semantic']['role'], open=found.get('open', True),
                             revision=sha([found['semantic']['role'], found['claims'], found['dependencies'],
                                           found['revision']]),
                             semantic_digest=routing.semantic_digest(found['semantic'])),
                    dependencies=dependencies, ownership=dict(digest=sha(sorted(found['claims'])), conflict=conflict),
                    resource=None if resource is None else dict(marker=sha(resource['lease']), current=resource['active']))


def entry(claims=(('/work/repo/app.py', 'write'),), dependencies=(), state='waiting', **extra):
    value = dict(semantic=semantic(), claims=[list(c) for c in claims], dependencies=list(dependencies), state=state,
                 revision=1, open=True, resource=None)
    value.update(extra)
    return value


class GateBase(DispatchBase):
    def setUp(self):
        super().setUp()
        self.service.close()
        self.workflow = FakeWorkflow(Path(self.temp.name) / 'workflow.json')
        self.workflow.write(dict(build=entry()))
        self.service = command.Service(self.settings, self.ledger, workflow=self.workflow)

    def ledger_body(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            return db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0]

    def prepared(self, attempt='a1', job_id='build', **extra):
        response = self.call(prepare(attempt, workflow=locator(job_id), **extra))
        self.assertEqual(response['state'], 'admitted', response)
        return response

    def assertRefused(self, attempt, reason, request=None):
        before = self.ledger_body()
        response = self.call(dispatch(attempt, **({} if request is None else dict(request=request))))
        self.assertEqual(response, dict(v=1, state='blocked', reason=reason))
        self.assertEqual(self.ledger_body(), before)  # zero sends: nothing consumed, nothing written
        hold = self.snapshot()['holds'][attempt]
        self.assertEqual(hold['state'], 'admitted')
        self.assertNotIn('dispatch', hold)


class BindingTests(GateBase):
    def test_prepare_stores_a_minimal_bounded_binding_and_all_gates_pass_once(self):
        response = self.prepared()
        self.assertNotIn('binding', response['admission'])  # never reported to clients
        record = budget.Ledger.admission(self.snapshot(), 'a1')
        binding = record['binding']
        self.assertEqual(set(binding), {'v', 'job', 'credential'})
        self.assertEqual(binding['job']['locator'], locator())
        self.assertEqual(set(binding['job']), {'locator', 'role', 'revision', 'semantic_digest', 'dependencies',
                                               'ownership', 'resource'})
        self.assertEqual(binding['job']['semantic_digest'], routing.semantic_digest(job()))
        raw = json.dumps(binding)
        self.assertNotIn(SECRET, raw)  # no prompt, brief or source text
        self.assertLessEqual(len(json.dumps(binding, separators=(',', ':'))), budget.MAX_BINDING_BYTES)
        first = self.call(dispatch('a1'))
        self.assertEqual(first['state'], 'dispatched', first)
        again = self.call(dispatch('a1'))
        self.assertEqual(again['state'], 'already_dispatched')
        self.assertNotIn('authority', again)

    def test_prepare_refuses_or_waits_on_unready_canonical_state(self):
        jobs = dict(build=entry(), lib=entry(claims=[['/work/repo/lib.py', 'write']], state='running'),
                    after=entry(claims=[['/work/repo/after.py', 'write']], dependencies=['lib']),
                    clash=entry(claims=[['/work/repo/lib.py', 'write']]),
                    paused=entry(claims=[['/work/repo/p.py', 'write']], open=False),
                    other=entry(semantic=semantic(objective='a different brief')))
        self.workflow.write(jobs)
        cases = [('missing', 'blocked', 'job_not_found'), ('after', 'pending', 'dependency_not_ready'),
                 ('clash', 'pending', 'ownership_conflict'), ('paused', 'pending', 'job_not_ready'),
                 ('other', 'blocked', 'binding_stale')]
        before = self.ledger_body() if self.ledger.exists() else None
        for name, state, reason in cases:
            with self.subTest(job=name):
                response = self.call(prepare('p-' + name, workflow=locator(name)))
                self.assertEqual((response['state'], response['reason']), (state, reason), response)
        self.assertEqual(self.ledger_body() if self.ledger.exists() else None, before)

    def test_explicit_delegation_without_a_job_binds_null_and_keeps_funding_gates(self):
        self.assertEqual(self.call(prepare('x1'))['state'], 'admitted')
        binding = budget.Ledger.admission(self.snapshot(), 'x1')['binding']
        self.assertEqual((binding['job'], binding['credential']), (None, None))
        self.assertEqual(self.workflow.views, 0)  # no workflow read for an unbound assignment
        self.assertEqual(self.call(dispatch('x1'))['state'], 'dispatched')
        self.assertEqual(self.workflow.views, 0)

    def test_an_admission_without_a_binding_is_refused(self):
        """Admissions recorded before S3.5 carry no binding; they fail closed (they expire within 900 s anyway)."""
        self.prepared()
        with self.open_ledger() as ledger, ledger.transaction() as data:
            del budget.Ledger.admission(data, 'a1')['binding']
        self.assertRefused('a1', 'binding_missing')

    def test_smart_routing_off_and_explicit_choices_get_the_same_gates(self):
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=False, worker_choices=dict(
            implementer=[dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')]))))
        self.prepared('off1')
        self.workflow.update('build', revision=2)
        self.assertRefused('off1', 'binding_stale')
        self.workflow.update('build', revision=1)
        choice = dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')
        request = prepare('pick1', workflow=locator())
        request['assignment']['choice'] = choice
        self.assertEqual(self.call(request)['state'], 'admitted')
        self.workflow.update('build', open=False)
        self.assertRefused('pick1', 'binding_stale')


class FreshGateTests(GateBase):
    def setUp(self):
        super().setUp()
        self.workflow.write(dict(
            base=entry(claims=[['/work/repo/base.py', 'write']], state='accepted'),
            build=entry(dependencies=['base'], resource=dict(lease='lease-1', active=True)),
            other=entry(claims=[['/work/repo/other.py', 'write']])))
        self.prepared()

    def test_a_reopened_dependency_refuses_dispatch(self):
        self.workflow.update('base', state='running')
        self.assertRefused('a1', 'dependency_reopened')

    def test_a_reaccepted_dependency_with_a_new_revision_still_refuses(self):
        self.workflow.update('base', revision=2)  # reopened and accepted again after prepare
        self.assertRefused('a1', 'dependency_reopened')

    def test_changed_writer_ownership_refuses_dispatch(self):
        self.workflow.update('other', claims=[['/work/repo/app.py', 'write']], state='running')
        self.assertRefused('a1', 'ownership_changed')
        self.workflow.update('other', claims=[['/work/repo/other.py', 'write']], state='waiting')
        self.workflow.update('build', claims=[['/work/repo/app.py', 'write'], ['/work/repo/extra.py', 'write']])
        self.assertIn(self.call(dispatch('a1'))['reason'], ('binding_stale', 'ownership_changed'))

    def test_a_lost_resource_claim_refuses_dispatch(self):
        for resource in (dict(lease='lease-1', active=False), dict(lease='lease-2', active=True), None):
            with self.subTest(resource=resource):
                self.workflow.update('build', resource=resource)
                self.assertRefused('a1', 'resource_authority_lost')

    def test_a_changed_brief_scope_role_or_tools_is_binding_stale(self):
        for change in (dict(objective='new brief'), dict(scope_ref='scope2'), dict(role='review'),
                       dict(tools=['shell'])):
            with self.subTest(change=change):
                self.workflow.update('build', semantic=semantic(**change))
                self.assertRefused('a1', 'binding_stale')
        self.workflow.update('build', semantic=semantic())
        self.assertEqual(self.call(dispatch('a1'))['state'], 'dispatched')

    def test_a_job_removed_or_closed_is_binding_stale(self):
        jobs = self.workflow.jobs()
        self.workflow.write({key: value for key, value in jobs.items() if key != 'build'})
        self.assertRefused('a1', 'binding_stale')
        self.workflow.write(dict(jobs, build=dict(jobs['build'], open=False)))
        self.assertRefused('a1', 'binding_stale')

    def test_a_profile_or_ranking_cache_hit_still_runs_every_gate(self):
        again = self.call(prepare('a1', workflow=locator()))
        self.assertIs(again['duplicate'], True)  # the repeated prepare reuses the admission, profile and ranking
        self.workflow.update('base', state='running')
        self.assertRefused('a1', 'dependency_reopened')
        self.workflow.update('base', state='accepted')
        # A complete coordinator profile is the zero-inference fast path; it confers no authority either.
        self.prepared('a2')
        self.workflow.update('other', claims=[['/work/repo/app.py', 'write']], state='running')
        self.assertRefused('a2', 'ownership_changed')

    def test_the_gates_only_read_workflow_state(self):
        before = self.workflow.path.read_bytes()
        self.workflow.update('base', state='running')
        changed = self.workflow.path.read_bytes()
        self.call(dispatch('a1'))
        self.assertEqual(self.workflow.path.read_bytes(), changed)
        self.workflow.path.write_bytes(before)
        self.assertEqual(self.call(dispatch('a1'))['state'], 'dispatched')
        self.assertEqual(self.workflow.path.read_bytes(), before)


class FailClosedTests(GateBase):
    def setUp(self):
        super().setUp()
        self.prepared()

    def test_gate_exceptions_fail_closed(self):
        for where in ('view', 'etag'):
            with self.subTest(where=where):
                self.workflow.fail = where
                self.assertRefused('a1', 'gate_error')
        self.workflow.fail = None
        for bad in (dict(), dict(etag='e', job=dict(role='worker'), dependencies=None, ownership=None, resource=None),
                    dict(etag='e' * 10_000, job=None, dependencies=None, ownership=None, resource=None), 'view', None):
            with self.subTest(bad=str(bad)[:40]), mock.patch.object(self.workflow, 'view', return_value=bad):
                self.assertRefused('a1', 'gate_error')
        self.assertEqual(self.call(dispatch('a1'))['state'], 'dispatched')

    def test_a_missing_reader_fails_closed(self):
        self.service.close()
        with self.open_ledger() as ledger:
            result = routing.dispatch(ledger, 'a1', facts(), dict(routing_settings.resolve({}), revision=0), now())
        self.assertEqual(result, dict(state='blocked', reason='gate_error'))
        self.assertEqual(self.snapshot()['holds']['a1']['state'], 'admitted')

    def test_a_changing_workflow_is_retried_then_refused(self):
        """The snapshot is taken before the write transaction and re-verified by etag inside it."""
        counter = iter(range(10**6))
        with mock.patch.object(self.workflow, 'etag', side_effect=lambda where: 'moving-%d' % next(counter)):
            self.assertRefused('a1', 'workflow_changed')
        flips = iter(['stale'])
        real = self.workflow.etag
        with mock.patch.object(self.workflow, 'etag', side_effect=lambda where: next(flips, None) or real(where)):
            self.assertEqual(self.call(dispatch('a1'))['state'], 'dispatched')  # one change, then a clean retry
        self.assertLessEqual(self.workflow.views, 1 + routing_gates.ROUNDS + 2)

    def test_the_workflow_is_never_read_inside_the_transaction(self):
        """Only the cheap etag runs while the ledger write lock is held; the view runs before it."""
        events = []
        real_view, real_etag, real_tx = self.workflow.view, self.workflow.etag, budget.Ledger.transaction
        depth = [0]

        def view(where):
            events.append(('view', depth[0]))
            return real_view(where)

        def etag(where):
            events.append(('etag', depth[0]))
            return real_etag(where)

        from contextlib import contextmanager

        @contextmanager
        def tracked(ledger, write=True, grows=False):
            depth[0] += write
            try:
                with real_tx(ledger, write, grows) as data:
                    yield data
            finally:
                depth[0] -= write
        with mock.patch.object(self.workflow, 'view', side_effect=view), \
                mock.patch.object(self.workflow, 'etag', side_effect=etag), \
                mock.patch.object(budget.Ledger, 'transaction', tracked):
            self.assertEqual(self.call(dispatch('a1'))['state'], 'dispatched')
        self.assertIn(('view', 0), events)
        self.assertNotIn(('view', 1), events)
        self.assertIn(('etag', 1), events)


class HostileInputTests(GateBase):
    def test_no_gate_input_is_reachable_from_a_request(self):
        self.prepared()
        before = self.ledger_body()
        hostile = [dispatch('a1', gates=['allow']), dispatch('a1', binding=dict(job=None)),
                   dispatch('a1', workflow=locator()), dispatch('a1', job=job()),
                   dict(v=1, op='check', attempt_id='a1', request=facts(), binding=dict(job=None)),
                   prepare('h1', binding=dict(v=1, job=None, credential=None)), prepare('h2', gates=[]),
                   prepare('h3', workflow=locator(), view=dict(etag='x'))]
        for request in hostile:
            with self.subTest(request=sorted(request)):
                self.assertEqual(self.call(request), dict(v=1, state='blocked', reason='invalid_request'))
        self.assertEqual(self.ledger_body(), before)
        self.workflow.update('build', revision=2)
        self.assertRefused('a1', 'binding_stale')  # the dispatch request carries no job data; the stored one is checked

    def test_hostile_or_oversized_locators_are_refused(self):
        long = 'x' * 2000
        cases = [locator(cwd='relative/path'), locator(cwd='/' + long), locator(session_id=long),
                 locator(task_id='t' * 129), locator(job_id=''), locator(attempt_id=7), dict(locator(), extra=1),
                 dict(client='codex'), 'build', None, locator(job_id='tab\tid')]
        for value in cases:
            with self.subTest(value=str(value)[:60]):
                self.assertEqual(self.call(prepare('h1', workflow=value)),
                                 dict(v=1, state='blocked', reason='invalid_request'))
        self.assertEqual(self.workflow.views, 0)

    def test_malformed_or_oversized_views_are_refused_at_prepare(self):
        good = self.workflow.view(locator())
        bad = [dict(good, etag='e' * 600), dict(good, extra=1), dict(good, job=dict(good['job'], role='x' * 300)),
               dict(good, ownership=dict(digest='not-hex', conflict=False)), dict(good, job=dict(good['job'], open=1)),
               dict(good, dependencies=dict(accepted=True, digest=None, count=0)), [], None]
        for value in bad:
            with self.subTest(value=str(value)[:60]), mock.patch.object(self.workflow, 'view', return_value=value):
                self.assertEqual(self.call(prepare('h1', workflow=locator())),
                                 dict(v=1, state='blocked', reason='workflow_unavailable'))
        with mock.patch.object(self.workflow, 'view', side_effect=RuntimeError('boom')):
            self.assertEqual(self.call(prepare('h1', workflow=locator())),
                             dict(v=1, state='blocked', reason='workflow_unavailable'))

    def test_the_largest_binding_fits_its_bound_and_the_admission_cap(self):
        hexa = 'f' * 64
        largest = dict(v=routing_gates.BINDING_VERSION, credential=hexa, job=dict(
            locator={key: '/' + 'x' * (limit - 1) for key, limit in routing_gates.LOCATOR_LIMITS.items()},
            role='r' * routing_gates.MAX_ROLE, revision=hexa, semantic_digest=hexa,
            dependencies=dict(count=routing_gates.MAX_DEPENDENCIES, digest=hexa), ownership=hexa, resource=hexa))
        self.assertTrue(routing_gates.valid_binding(largest))
        size = len(json.dumps(largest, separators=(',', ':')))
        self.assertLessEqual(size, budget.MAX_BINDING_BYTES)
        too_big = copy.deepcopy(largest)
        too_big['job']['locator']['cwd'] += 'x'
        self.assertFalse(routing_gates.valid_binding(too_big))
        # S3.4's longest capacity-test admission (3,988 characters) plus the largest binding still fits the cap.
        self.assertLessEqual(3988 + len(',"binding":') + size, budget.MAX_ADMISSION_BYTES)


class FundingGateTests(GateBase):
    """API Usage, the organization and the credential binding are re-checked at dispatch, job or not."""
    def setUp(self):
        super().setUp()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(api_usage_enabled=True)))
        budget.initialize(self.ledger)
        self.fund()
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data['routing'] = {SCOPE: dict(revision=1, disabled=False, enabled=True, binding=dict(
                organization='org', project='proj', credential_ref=KEY_REF, storage_kind='macos_keychain'))}

    def admit(self, attempt, **extra):
        request = prepare(attempt, observations=api_observations(), constraints=dict(allowed_strategy_ids=['s-api']),
                          **extra)
        response = self.call(request)
        self.assertEqual((response['state'], response['admission']['class']), ('admitted', 'api'), response)

    def test_a_changed_credential_binding_refuses_dispatch(self):
        changes = [lambda config: config.update(disabled=True),
                   lambda config: config['binding'].update(credential_ref='vcw_' + '0' * 32),
                   lambda config: config.update(revision=2)]
        for index, change in enumerate(changes):
            for bound in (True, False):
                attempt = 'c%d%s' % (index, 'j' if bound else 'x')
                with self.subTest(change=index, bound=bound):
                    self.admit(attempt, **(dict(workflow=locator()) if bound else {}))
                    self.assertIsNotNone(budget.Ledger.admission(self.snapshot(), attempt)['binding']['credential'])
                    with self.open_ledger() as ledger, ledger.transaction() as data:
                        saved = copy.deepcopy(data['routing'])
                        change(data['routing'][SCOPE])
                    self.assertRefused(attempt, 'credential_changed', facts(**API))
                    with self.open_ledger() as ledger, ledger.transaction() as data:
                        data['routing'] = saved
                    self.release(attempt)
        with self.open_ledger() as ledger, ledger.transaction() as data:
            del data['routing']
        self.admit('gone', workflow=locator())
        with self.open_ledger() as ledger, ledger.transaction() as data:
            data['routing'] = saved
        self.assertRefused('gone', 'credential_changed', facts(**API))

    def test_disabled_api_usage_or_organization_refuses_dispatch(self):
        self.admit('api1', workflow=locator())
        settings = dict(routing_settings.resolve({}), api_usage_enabled=False, revision=1)
        before = self.ledger_body()
        with self.open_ledger() as ledger:
            result = routing.dispatch(ledger, 'api1', facts(**API), settings, now(), workflow=self.workflow)
        self.assertEqual(result, dict(state='blocked', reason='api_usage_disabled'))
        self.assertEqual(self.ledger_body(), before)
        self.call(dict(v=1, op='set', organization=dict(id='personal', enabled=False)))
        self.assertRefused('api1', 'organization_unavailable', facts(**API))

    def test_an_unchanged_binding_dispatches(self):
        self.admit('api1', workflow=locator())
        self.assertEqual(self.call(dispatch('api1', request=facts(**API)))['state'], 'dispatched')


class ConcurrencyTests(GateBase):
    def test_fifteen_racing_dispatchers_get_one_token(self):
        self.prepared('race')
        self.service.close()
        results, start = [], threading.Barrier(15)

        def worker():
            service = command.Service(self.settings, self.ledger, workflow=self.workflow)
            try:
                start.wait()
                results.append(service.handle(json.dumps(dispatch('race')).encode()))
            finally:
                service.close()
        threads = [threading.Thread(target=worker) for _ in range(15)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(r['state'] for r in results), ['already_dispatched'] * 14 + ['dispatched'], results)
        self.assertEqual(len([r for r in results if 'authority' in r]), 1)

    def test_a_six_job_plan_runs_ready_jobs_concurrently_and_dependents_wait(self):
        def node(name, dependencies=(), state='waiting'):
            return entry(claims=[['/work/repo/%s.py' % name, 'write']], dependencies=dependencies, state=state,
                         semantic=semantic(objective='job ' + name))
        jobs = dict(api=node('api'), ui=node('ui'), backend=node('backend', ['api']), front=node('front', ['ui']),
                    integrate=node('integrate', ['backend', 'front']), review=node('review', ['integrate']))
        self.workflow.write(jobs)
        accounts = dict(api='anthropic:a1', ui='anthropic:a2', backend='anthropic:a3', front='anthropic:a4',
                        integrate='anthropic:a5', review='anthropic:a6')

        def ready(name):
            return self.call(prepare('w-' + name, workflow=locator(name), job=job(objective='job ' + name),
                                     observations=observations(account=accounts[name])))
        waiting = [name for name in ('backend', 'front', 'integrate', 'review') if ready(name)['state'] == 'pending']
        self.assertEqual(waiting, ['backend', 'front', 'integrate', 'review'])  # no capacity held while unready
        self.assertEqual([ready(name)['state'] for name in ('api', 'ui')], ['admitted', 'admitted'])
        self.assertEqual(len(budget.Ledger.open_holds(self.snapshot())), 2)
        self.service.close()
        results = []

        def go(name):
            service = command.Service(self.settings, self.ledger, workflow=self.workflow)
            try:
                results.append(service.handle(json.dumps(dispatch('w-' + name)).encode()))
            finally:
                service.close()
        threads = [threading.Thread(target=go, args=(name,)) for name in ('api', 'ui')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([r['state'] for r in results], ['dispatched'] * 2, results)
        self.service = command.Service(self.settings, self.ledger, workflow=self.workflow)
        self.workflow.update('api', state='accepted')
        self.assertEqual(ready('backend')['state'], 'admitted')
        self.assertEqual(ready('integrate')['reason'], 'dependency_not_ready')
        self.workflow.update('ui', state='accepted')
        self.assertEqual(ready('front')['state'], 'admitted')
        self.assertEqual([self.call(dispatch('w-' + n))['state'] for n in ('backend', 'front')], ['dispatched'] * 2)
        self.assertEqual(ready('review')['reason'], 'dependency_not_ready')


class CanonicalWorkflowTests(unittest.TestCase):
    """The production reader over the canonical delegation state (runtime/delegation.py); slower fixture."""
    def setUp(self):
        import tempfile
        from tests import test_delegation
        self.f = test_delegation.AdmissionTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.scope = self.f.scope
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Path(temp.name) / 'routing' / 'settings.json'
        self.ledger = Path(temp.name) / 'usage' / 'organizations.sqlite3'
        Base.store_table(self, table())
        self.reader = routing_gates.D6Workflow(resolve=lambda where: self.scope)
        self.service = command.Service(self.settings, self.ledger, workflow=self.reader)
        self.addCleanup(self.service.close)
        self.jobs = [dict(id='a', role='worker', claims=[self.f.path_claim('a.py')], dependencies=[], max_attempts=2),
                     dict(id='b', role='worker', claims=[self.f.path_claim('b.py')], dependencies=['a'], max_attempts=1)]
        self.f.plan(self.f.plan_value(jobs=self.jobs))

    def call(self, request):
        return self.service.handle(json.dumps(request).encode())

    def native(self, name, dispatch_id, **updates):
        """Reserve the job's existing delegation assignment (claims and capacity) through its own path."""
        found = next(j for j in self.jobs if j['id'] == name)
        request = self.f.prepare_request(job_id=name, dispatch_id=dispatch_id, **updates)
        request['brief'].update(files=[name + '.py'], resources=found['claims'], dependencies=found['dependencies'])
        from runtime import delegation
        return delegation.prepare_assignment(self.scope, request)

    def settle(self, child, worker):
        from runtime import delegation
        observed = self.f.lifecycle_observation(child, worker_id=worker)
        report = json.loads(observed['result'])
        report['changed_paths'] = []
        observed['result'] = json.dumps(report)
        observed['receipt']['result_digest'] = hashlib.sha256(observed['result'].encode()).hexdigest()
        self.f.observe_worker(child, observed)
        delegation.reconcile_assignment(self.scope, self.f.worker_reconciliation_request(child, changed_paths=[]))

    def where(self, job_id, **extra):
        return dict(client='codex', cwd=str(self.scope.repository_root), session_id=self.f.identity['session_id'],
                    task_id=self.f.identity['task_id'], job_id=job_id, **extra)

    def job(self, job_id, brief=False):
        found = next(j for j in self.jobs if j['id'] == job_id)
        value = dict(id=job_id, role=found['role'], scope_ref=self.f.identity['scope_ref'], claims=found['claims'],
                     profile=job()['profile'])  # the profile is not part of the semantic digest
        if brief:
            value.update(objective='Implement bounded edit', context='Inspect existing callers first')
        return value

    def admit(self, attempt, job_id, brief=False, **where):
        return self.call(prepare(attempt, job=self.job(job_id, brief), workflow=self.where(job_id, **where)))

    def state_files(self):
        return {str(p): p.read_bytes() for p in self.scope.runtime.state_dir.rglob('*') if p.is_file()}

    def ledger_body(self):
        with closing(sqlite3.connect(self.ledger)) as db:
            return db.execute('SELECT body FROM ledger WHERE id=1').fetchone()[0]

    def assertRefused(self, attempt, reason):
        before, files = self.ledger_body(), self.state_files()
        self.assertEqual(self.call(dispatch(attempt)), dict(v=1, state='blocked', reason=reason))
        self.assertEqual(self.ledger_body(), before)
        self.assertEqual(self.state_files(), files)  # the gates only read canonical workflow state

    def test_ready_job_dispatches_and_unready_dependent_waits(self):
        self.assertEqual(self.admit('r-b', 'b')['reason'], 'dependency_not_ready')
        self.assertEqual(self.admit('r-a', 'a')['state'], 'admitted')
        files = self.state_files()
        self.assertEqual(self.call(dispatch('r-a'))['state'], 'dispatched')
        self.assertEqual(self.state_files(), files)

    def test_a_reopened_dependency_refuses_dispatch(self):
        first = self.native('a', 'a-original')
        self.settle(first, 'a-original')
        self.assertEqual(self.admit('r-b', 'b')['state'], 'admitted')
        self.native('a', 'a-reopened', retry_of=first['attempt_id'], diagnostic=dict(
            prior_attempt_id=first['attempt_id'], cause='Reopened prerequisite',
            evidence=['fixture:changed-prerequisite'], change='New accepted prerequisite result'))
        self.assertRefused('r-b', 'dependency_reopened')

    def test_a_new_conflicting_writer_refuses_dispatch(self):
        self.assertEqual(self.admit('r-a', 'a')['state'], 'admitted')
        self.native('a', 'a-writer')  # another owner now holds the job's write claim
        self.assertRefused('r-a', 'ownership_changed')

    def test_a_settled_bound_assignment_loses_its_resource_authority(self):
        child = self.native('a', 'a-bound')
        self.assertEqual(self.admit('r-a', 'a', brief=True, attempt_id=child['attempt_id'])['state'], 'admitted')
        self.assertEqual(self.admit('r-x', 'a', attempt_id=child['attempt_id'])['reason'], 'binding_stale')
        self.f.settle_fixture(child['attempt_id'])
        self.assertRefused('r-a', 'resource_authority_lost')

    def test_a_resource_admission_change_loses_resource_authority(self):
        from runtime import workstreams
        self.assertEqual(self.admit('r-a', 'a')['state'], 'admitted')
        status = workstreams.control(self.scope, 'status', dict(client='codex', feature='resource_admission'))
        action = 'disable' if status['enabled'] else 'enable'
        workstreams.control(self.scope, action, dict(client='codex', feature='resource_admission',
                                                     expected_revision=status['revision']))
        self.assertRefused('r-a', 'resource_authority_lost')

    def test_the_cheap_etag_tracks_every_canonical_write(self):
        from runtime import delegation
        before = delegation.routing_etag(self.scope)
        self.assertEqual(delegation.routing_etag(self.scope), before)
        self.assertEqual(delegation.routing_view(self.scope, self.where('a'))['etag'], before)
        self.native('a', 'a-writer')
        self.assertNotEqual(delegation.routing_etag(self.scope), before)


if __name__ == '__main__':
    unittest.main()
