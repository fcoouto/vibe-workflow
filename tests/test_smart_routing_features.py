"""C.2: Smart Routing feature contracts F01-F17 (plan Task C.2; spec Appendix A), one class per group.

Every double here counts calls and makes no model, network or secret access. Contracts that already hold on `main`
are plain tests. Contracts that need Task C.3 (callers routed through `scripts/routing.py prepare`, legacy selectors
retired) are `@unittest.expectedFailure` with the reason `NEEDS_C3` and a comment naming what C.3 must change: C.3
turns each into an unexpected success, which fails the run, so C.3 must remove the marker with the change.
"""
import ast
from contextlib import ExitStack, contextmanager
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.request
import uuid

from runtime import external_delegation, observability, routing, routing_settings, usage_credentials
from scripts import routing as command
from tests import test_routing_command as command_tests
from tests import test_routing_gates as gate_tests
from tests.test_routing_command import KEY_REF, SCOPE, SECRET, api_observations, job, prepare

ROOT = Path(__file__).resolve().parents[1]
NEEDS_C3 = 'needs plan Task C.3: callers routed through scripts/routing.py prepare and legacy selectors retired'
CHOICE = dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')


def needs_c3(test):
    """Mark a contract that fails on `main` until C.3; C.3 removes the marker when it makes it pass."""
    test.needs_c3 = NEEDS_C3
    return unittest.expectedFailure(test)


class Counter:
    """A counting double. With `real` it passes calls through; without it, it refuses the call."""
    def __init__(self, real=None, name='call'):
        self.real, self.name, self.calls = real, name, []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.real is None:
            raise AssertionError('unexpected ' + self.name)
        return self.real(*args, **kwargs)

    @property
    def count(self):
        return len(self.calls)


@contextmanager
def side_channels():
    """Count every network, process, secret and routing-inference entry point; refuse the outward ones."""
    counters = dict(connect=Counter(name='socket connect'), create_connection=Counter(name='create_connection'),
                    urlopen=Counter(name='urlopen'), popen=Counter(name='subprocess'),
                    resolve_key=Counter(name='key resolution'), read_key=Counter(name='keychain read'),
                    ensure_profile=Counter(routing.ensure_profile), rank=Counter(routing.rank),
                    admit=Counter(routing.admit))
    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(socket.socket, 'connect', counters['connect']))
        stack.enter_context(mock.patch.object(socket, 'create_connection', counters['create_connection']))
        stack.enter_context(mock.patch.object(urllib.request, 'urlopen', counters['urlopen']))
        stack.enter_context(mock.patch.object(subprocess, 'Popen', counters['popen']))
        stack.enter_context(mock.patch.object(usage_credentials, 'resolve_key', counters['resolve_key']))
        stack.enter_context(mock.patch.object(usage_credentials, '_read_key', counters['read_key']))
        for name in ('ensure_profile', 'rank', 'admit'):
            stack.enter_context(mock.patch.object(routing, name, counters[name]))
        yield counters


OUTWARD = ('connect', 'create_connection', 'urlopen', 'popen', 'resolve_key', 'read_key')


def load_script(name):
    spec = importlib.util.spec_from_file_location('c2_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def usage():
    tokens = dict(input=10, output=5, reasoning=0, cache_read=0, cache_write=0)
    return dict(source='host_message_totals', requests=[dict(kind='primary', model='m', tokens=tokens)])


class C2aStartupStatusNamingDiagnostics(command_tests.Base):
    """F01 Prepare my Workspace, F02 Onboard my Project, F15 naming/visibility, F17 startup/diagnostics."""

    def assertNoOutwardCalls(self, counters):
        self.assertEqual({name: counters[name].count for name in OUTWARD}, dict.fromkeys(OUTWARD, 0))

    def test_status_reports_default_on_without_inference_secrets_or_writes(self):
        """F01/F17: verify/status is cheap: no inference, no key, no network, and it creates no state."""
        with side_channels() as counters:
            response = self.call(dict(v=1, op='status'))
        self.assertEqual(response['state'], 'ok', response)
        self.assertIs(response['settings']['smart_routing'], True)  # built in, ON by default
        self.assertIs(response['settings']['api_usage_enabled'], False)  # default ON never enables API Usage
        self.assertEqual(response['settings_revision'], 0)  # no explicit choice recorded
        self.assertEqual(response['ledger'], dict(state='absent'))
        self.assertNoOutwardCalls(counters)
        self.assertEqual((counters['ensure_profile'].count, counters['rank'].count, counters['admit'].count),
                         (0, 0, 0))
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.ledger.exists())

    def test_explicit_off_is_a_model_free_write_that_survives_a_restart_and_later_changes(self):
        """F02/F17: explicit OFF persists through restarts and unrelated setting changes."""
        with side_channels() as counters:
            off = self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=False)))
        self.assertEqual((off['state'], off['revision'], off['settings']['smart_routing']), ('updated', 1, False))
        self.assertNoOutwardCalls(counters)
        self.assertEqual(counters['ensure_profile'].count + counters['rank'].count, 0)
        caps = dict(routing_settings.DEFAULTS['caps'])
        changed = self.call(dict(v=1, op='set', expected_revision=1, change=dict(caps=caps)))
        self.assertIs(changed['settings']['smart_routing'], False)  # explicit values merge; defaults never reset
        stale = self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=True)))
        self.assertEqual(stale, dict(v=1, state='blocked', reason='stale_revision'))
        done = self.run_cli(['status'], dict(v=1, op='status'))  # a fresh process: the restart
        restarted = json.loads(done.stdout)
        self.assertIs(restarted['settings']['smart_routing'], False)
        self.assertEqual(restarted['settings_revision'], 2)

    def test_only_the_routing_command_writes_routing_settings(self):
        """F02: onboarding, setup and maintenance cannot reset an explicit OFF, because nothing else writes it."""
        writers = []
        for path in sorted([*ROOT.glob('runtime/*.py'), *ROOT.glob('scripts/*.py'), *ROOT.glob('hooks/*.py')]):
            if path.name == 'routing_settings.py' or path.relative_to(ROOT).as_posix() == 'scripts/routing.py':
                continue
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                        and node.value.id == 'routing_settings' and node.attr in ('update', '_write'):
                    writers.append(path.name)
            if 'routing/settings.json' in source or 'strategy-table.json' in source:
                writers.append(path.name)
        self.assertEqual(writers, [])

    def test_enablement_is_reported_separately_from_readiness(self):
        """F01/F17: missing evidence leaves Smart Routing enabled, waits with a fixed reason and never blocks the
        deterministic path (RF8): an unusable table is reported on the admission it degraded."""
        self.store_table(None)  # no compiled strategy table yet: enabled, not ready
        with side_channels() as counters:
            response = self.call(prepare('r1'))
        self.assertEqual(response['state'], 'pending', response)
        self.assertRegex(response['reason'], r'^[a-z_]+$')
        self.assertNoOutwardCalls(counters)
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(worker_choices=dict(implementer=[CHOICE]))))
        self.store_table({'rows': 'not-a-table'})
        degraded = self.call(prepare('r2'))
        self.assertEqual(degraded['state'], 'admitted', degraded)
        self.assertEqual(degraded['admission']['mode'], 'worker_choice')
        self.assertIn('table_unavailable', degraded['ranking']['reason_codes'])  # visible, never hidden
        status = self.call(dict(v=1, op='status'))
        self.assertIs(status['settings']['smart_routing'], True)  # never silently switched OFF
        self.assertEqual(status['settings_revision'], 1)

    def test_waiting_reasons_for_display_are_fixed_codes_without_brief_or_secret(self):
        """F15: what a title or visibility badge can show is a fixed reason code, never brief text or a key."""
        request = prepare('n1', observations=api_observations())
        request['observations']['routes'] = request['observations']['routes'][1:]  # the API route only
        response = self.call(request)
        self.assertEqual(response['state'], 'pending', response)
        raw = json.dumps(response) + json.dumps(self.call(dict(v=1, op='status')))
        for secret in (SECRET, KEY_REF):
            self.assertNotIn(secret, raw)
        self.assertRegex(response['reason'], r'^[a-z_]+$')


class C2bWorkstreamsDelegationResources(gate_tests.GateBase):
    """F03 Workstreams, F11 planning/native delegation/continuation, F12 resource admission, F16 context."""

    def test_a_supplied_profile_needs_no_jev_and_only_semantic_changes_reprofile(self):
        """F03/F16: a valid profile takes zero Jev calls; progress or unrelated metadata does not reprofile."""
        jev = Counter(lambda view, fields: {}, name='jev')
        base = routing.ensure_profile(job(), jev=jev)
        self.assertEqual(jev.count, 0)
        progress = routing.ensure_profile(job(progress=dict(done=3, total=5), status='running', attempt=2), jev=jev)
        self.assertEqual(progress['semantic_digest'], base['semantic_digest'])
        changed = routing.ensure_profile(job(objective='A different objective'), jev=jev)
        self.assertNotEqual(changed['semantic_digest'], base['semantic_digest'])
        self.assertEqual(jev.count, 0)

    def test_one_task_graph_ready_jobs_admit_dependent_and_conflicting_jobs_wait(self):
        """F03: the workflow is the only graph; distinct ready jobs admit, dependent and conflicting ones wait."""
        entry, locator = gate_tests.entry, gate_tests.locator
        self.workflow.write(dict(
            build=entry(),
            docs=entry(claims=[['/work/repo/docs.md', 'write']]),
            lib=entry(claims=[['/work/repo/lib.py', 'write']], state='running'),
            after=entry(claims=[['/work/repo/after.py', 'write']], dependencies=['lib']),
            clash=entry(claims=[['/work/repo/lib.py', 'write']])))
        self.assertEqual(self.call(prepare('j1', workflow=locator('build')))['state'], 'admitted')
        self.assertEqual(self.call(prepare('j2', workflow=locator('docs')))['state'], 'admitted')
        waiting = self.call(prepare('j3', workflow=locator('after')))
        self.assertEqual((waiting['state'], waiting['reason']), ('pending', 'dependency_not_ready'))
        conflict = self.call(prepare('j4', workflow=locator('clash')))
        self.assertEqual((conflict['state'], conflict['reason']), ('pending', 'ownership_conflict'))
        self.assertGreaterEqual(self.workflow.views, 4)  # every decision read the canonical graph
        body = self.ledger_body()
        for name in ('docs.md', 'after.py', 'dependencies":['):
            self.assertNotIn(name, body)  # the ledger keeps digests, not a second copy of the graph

    def test_an_unchanged_continuation_reuses_its_profile_but_gets_fresh_admission(self):
        """F11: a continuation (new attempt, same job) reuses the profile and is admitted and authorized afresh."""
        first = self.prepared('c1')
        repeat = self.call(prepare('c1', workflow=gate_tests.locator()))
        self.assertTrue(repeat['duplicate'])  # a repeated prepare is the same admission, not a new one
        sent = self.call(gate_tests.dispatch('c1'))
        self.assertEqual(sent['state'], 'dispatched', sent)
        second = self.prepared('c2')
        self.assertFalse(second['duplicate'])
        self.assertEqual(second['profile']['semantic_digest'], first['profile']['semantic_digest'])
        self.assertNotEqual(second['admission']['attempt_id'], first['admission']['attempt_id'])
        resent = self.call(gate_tests.dispatch('c2'))
        self.assertEqual(resent['state'], 'dispatched', resent)
        self.assertNotEqual(resent['authority'], sent['authority'])  # no authority carries over

    def test_changed_scope_or_brief_invalidates_the_prepared_selection(self):
        """F11/F16: a changed brief makes the prepared route stale before anything is sent."""
        self.prepared('s1')
        self.workflow.update('build', semantic=gate_tests.semantic(objective='Rewritten objective'))
        self.assertRefused('s1', 'binding_stale')

    def test_resource_and_funding_gates_bind_the_same_job_revision(self):
        """F12: admission binds the job revision and resource authority; headroom never stands in for them."""
        self.workflow.write(dict(build=gate_tests.entry(resource=dict(lease='lease-1', active=True))))
        self.prepared('g1')
        binding = command_tests.budget.Ledger.admission(self.snapshot(), 'g1')['binding']['job']
        self.assertIsNotNone(binding['resource'])
        self.assertIsNotNone(binding['revision'])
        self.workflow.update('build', resource=dict(lease='lease-1', active=False))
        self.assertRefused('g1', 'resource_authority_lost')  # funding is still available; resources are not
        self.workflow.update('build', resource=dict(lease='lease-1', active=True), revision=2)
        self.assertRefused('g1', 'binding_stale')

    # C.3 must make `--project` mandatory for production `prepare` with a workflow locator (S3.5 wiring list), so
    # an unpinned service refuses a workflow-bound prepare with `project_required`, as `settle` already does.
    @needs_c3
    def test_a_workflow_bound_prepare_requires_the_project_pin(self):
        response = self.call(prepare('p1', workflow=gate_tests.locator()))
        self.assertEqual((response['state'], response.get('reason')), ('blocked', 'project_required'),
                         'an unpinned routing service answered a workflow-bound prepare')


class C2bNativeDelegationThroughRouting(unittest.TestCase):
    """F11/F03: native delegation obtains its worker through the one routing prepare path."""

    def setUp(self):
        from tests.fixture_delegation import AdmissionFixture
        self.fixture = type('Fixture', (AdmissionFixture, unittest.TestCase), dict(runTest=lambda self: None))()
        self.fixture._testMethodName = 'runTest'
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.plan()

    def prepare_counted(self):
        admit = Counter(routing.admit)
        with mock.patch.object(routing, 'admit', admit):
            prepared = self.fixture.prepare()
        self.assertTrue(prepared['launch_admitted'])
        return admit

    # C.3 must replace the caller-supplied native `selection` with `scripts/routing.py prepare`: the native worker
    # admission calls the routing admission exactly once (plan C.3 box 1; spec F11).
    @needs_c3
    def test_native_worker_admission_calls_routing_prepare_once(self):
        admit = self.prepare_counted()
        self.assertEqual(admit.count, 1, 'native worker admission did not go through routing prepare')

    # C.3 must prepare every planned Workstreams job with `workflow` after delegation's own reservation, so the
    # routing admission is bound to the canonical job (S3.5 C.3 wiring list; RF3).
    @needs_c3
    def test_native_worker_admission_is_bound_to_its_workflow_job(self):
        admit = self.prepare_counted()
        bound = [kwargs.get('workflow') for _, kwargs in admit.calls if kwargs.get('workflow') is not None]
        self.assertTrue(bound, 'routing admission for a planned job carried no workflow binding')
        self.assertEqual(bound[0]['locator']['job_id'], 'edit')


class Funded(command_tests.Base):
    def fund(self, organization='personal'):
        self.service._ledger(True, write=True)  # the ledger exists before the user's explicit enablement
        super().fund(organization)


class C2cApiUsageAndOpenRouter(Funded):
    """F04 Manage API Usage, F05 OpenRouter integration, F06 proposals, F07 workers."""

    def api_only(self, attempt, **route):
        request = prepare(attempt, observations=api_observations())
        request['observations']['routes'] = request['observations']['routes'][1:]
        request['observations']['routes'][0].update(route)
        return request

    def test_api_usage_stays_off_until_explicitly_enabled(self):
        """F04: default ON routing does not enable API Usage; an API-only assignment waits on that reason."""
        self.fund()
        with side_channels() as counters:
            response = self.call(self.api_only('a1'))
        self.assertEqual(response['state'], 'pending', response)
        self.assertIn('api_usage_disabled', response['reasons'])
        self.assertEqual(counters['resolve_key'].count + counters['read_key'].count, 0)
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(smart_routing=True)))
        again = self.call(self.api_only('a2'))
        self.assertIn('api_usage_disabled', again['reasons'])  # an explicit ON is still not API enablement
        enabled = self.call(dict(v=1, op='set', expected_revision=1, change=dict(api_usage_enabled=True)))
        self.assertIs(enabled['settings']['api_usage_enabled'], True)
        admitted = self.call(self.api_only('a3'))
        self.assertEqual(admitted['state'], 'admitted', admitted)
        self.assertEqual(admitted['admission']['billing_class'], 'api_grant')

    def test_api_usage_enablement_never_funds_the_openrouter_gateway(self):
        """F05: OpenRouter keeps its own activation; direct API enablement and a ready key do not open it."""
        self.fund()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(api_usage_enabled=True)))
        request = self.api_only('o1', billing_class='gateway', harness='openrouter', integration_enabled=False)
        request['observations']['pools']['api']['billing_class'] = 'gateway'
        response = self.call(request)
        self.assertEqual(response['state'], 'pending', response)
        self.assertIn('integration_disabled', response['reasons'])

    def test_routing_settings_cannot_activate_optional_integrations(self):
        """F05/F08/F10 (RF1): Smart Routing has no switch for OpenRouter, Council, observability or Jev."""
        for key in ('openrouter_enabled', 'council', 'observability', 'jev', 'model_ranker'):
            with self.subTest(key=key):
                response = self.call(dict(v=1, op='set', expected_revision=0, change={key: True}))
                self.assertEqual(response, dict(v=1, state='blocked', reason='unknown_setting'))
        self.assertFalse(self.settings.exists())

    def test_a_scoped_packet_never_widens_destination_or_sources(self):
        """F06: routing preference cannot add a route or file the user's permission does not list."""
        from tests.test_external_delegation import permission
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'src').mkdir()
            (root / 'src' / 'code.py').write_text('source bytes\n')
            (root / 'secret.txt').write_text('outside the permission\n')
            consent = permission('openrouter', model='model/v1', provider='p', effort=None)
            consent['sources'] = ['src']
            packet = external_delegation.prepare(root, consent, [dict(kind='source', path='src/code.py')])
            binding = dict(packet['binding'], automatic=True, local_preferred=False)
            route = dict(service='openrouter', mode='proposal', provider='p', effort=None,
                         class_name=consent['classes'][0], now=0)
            self.assertIn('source bytes', external_delegation.check(root, consent, binding, model='model/v1',
                                                                     **route)['text'])
            with self.assertRaisesRegex(ValueError, 'external_destination_denied'):
                external_delegation.check(root, consent, binding, model='model/v2-preferred', **route)
            with self.assertRaisesRegex(ValueError, 'external_source_denied'):
                external_delegation.prepare(root, consent, [dict(kind='source', path='secret.txt')])

    def test_v1_openrouter_worker_configuration_stays_read_only(self):
        """F07: the qualified V1 worker is read-only; nothing in it authorizes editing or V2 enforcement."""
        workers = load_script('openrouter_workers')
        config = workers.configuration(dict(files=['input.txt'], steps=3, model='vendor/model', provider='p',
                                            effort='low'))
        permission = config['permission']
        self.assertEqual(permission['*'], 'deny')
        self.assertEqual({key for key, value in permission.items() if value != 'deny'}, {'read', 'glob', 'grep'})
        self.assertEqual(permission['read'], {'*': 'deny', 'input.txt': 'allow'})
        options = config['provider']['openrouter']['models']['vendor/model']['options']
        self.assertEqual(options['provider'], dict(only=['p'], allow_fallbacks=False))
        self.assertEqual((config['mcp'], config['plugin']), ({}, []))

    # C.3 must retire the caller-owned automatic route selector: selection is `scripts/routing.py prepare` only
    # (plan C.3 box 1; spec F11, F06), with permission and source checks kept as constraints.
    @needs_c3
    def test_external_delegation_keeps_no_caller_owned_selector(self):
        self.assertFalse(hasattr(external_delegation, 'select'),
                         'runtime.external_delegation.select is still a caller-owned route selector')

    # C.3 must retire the Usage Routing Smart Balance decision that `scripts/routing.py` replaces (plan C.3 box 3):
    # `scripts/usage_routing.py evaluate` stops existing, with no compatibility alias.
    @needs_c3
    def test_usage_routing_cli_no_longer_selects_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            done = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / 'usage_routing.py'), 'evaluate',
                                   '--cwd', temp], input=b'{}', capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 2, 'usage_routing.py evaluate still answers a routing decision: '
                         + done.stdout.decode()[:200])
        self.assertIn(b'invalid choice', done.stderr)


class C2cOpenRouterCallersThroughRouting(unittest.TestCase):
    """F06/F07: an approved OpenRouter proposal or worker is admitted through the common routing service."""

    def setUp(self):
        from tests.openrouter_fixtures import enroll
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = enroll(self).repository_root

    def counted(self, call):
        admit = Counter(routing.admit)
        with mock.patch.object(routing, 'admit', admit):
            result = call()
        return admit, result

    def proposal(self):
        from tests import test_openrouter_proposals as proposals
        from tests.openrouter_fixtures import configure
        api = load_script('openrouter_proposals')
        self.settings = configure(self.repo, routes=('proposals',))
        store = Path(self.settings['proposal_store'])
        call = lambda action, request=None: api.run(action, store, request or {}, now=100, cwd=self.repo)
        token = call('claim')['refresh_token']
        call('publish', dict(refresh_token=token, metadata=proposals.METADATA))
        consent = external_delegation.effective_permission(
            self.settings['external_permission'], self.settings['activation'], service='openrouter',
            mode='proposal', model='model/v1', provider='p', effort=None, class_name='investigation')
        packet = external_delegation.prepare(self.repo, consent, [dict(kind='text', text='Fixture objective',
                                                                        sources=[])])
        request = dict(task_id='task-1', profile=proposals.PROFILE, model='model/v1', provider='p', effort=None,
                       snapshot_digest=call('status')['snapshot_digest'], base_ref='git:abc', output_tokens=500,
                       timeout_seconds=60, requirements=dict(context_tokens=100, output_tokens=500,
                                                             modalities=['text'], parameters=[]),
                       external=dict(packet['binding'], automatic=True, local_preferred=False,
                                     class_name='investigation', coordinator='native-coordinator',
                                     reviewer='native-reviewer', integration_available=True))
        return lambda: call('begin', request)

    # C.3 must route an approved OpenRouter proposal through `scripts/routing.py prepare` (plan C.3 box 1; F06):
    # `begin` obtains one routing admission, keeping endpoint pinning and scope as constraints.
    @needs_c3
    def test_proposal_begin_obtains_one_routing_admission(self):
        admit, begun = self.counted(self.proposal())
        self.assertIn('attempt_token', begun)
        self.assertEqual(admit.count, 1, 'an OpenRouter proposal began without a routing admission')

    # C.3 must route each OpenRouter worker through `scripts/routing.py prepare` (plan C.3 box 1; F07): `prepare`
    # obtains one routing admission and records the requested route.
    @needs_c3
    def test_worker_prepare_obtains_one_routing_admission(self):
        from tests.openrouter_fixtures import configure
        workers = load_script('openrouter_workers')
        root = Path(self.temp.name).resolve()
        (root / 'auth').mkdir(mode=0o700)
        executable = root / 'worker'
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o700)
        settings = configure(self.repo, executable=executable, auth=root / 'auth', routes=('workers',))
        (self.repo / 'input.txt').write_text('three lines\nof evidence\nfor review\n')
        brief = dict(task_id='pilot-1', coordinator_chat_id=str(uuid.uuid4()), model='vendor/model',
                     provider='provider', objective='Read input.txt and count lines.', source_root=str(self.repo),
                     files=['input.txt'], timeout_seconds=5, steps=3, effort='low')
        consent = external_delegation.effective_permission(
            settings['external_permission'], settings['activation'], service='openrouter', mode='worker',
            model='vendor/model', provider='provider', effort='low', class_name='investigation')
        packet = external_delegation.prepare(self.repo, consent, [dict(kind='text', text=brief['objective'],
                                                                        sources=brief['files'])])
        brief['external'] = dict(packet['binding'], automatic=True, local_preferred=False,
                                 class_name='investigation', coordinator=brief['coordinator_chat_id'],
                                 reviewer='native-reviewer', integration_available=True)
        admit, run = self.counted(lambda: workers.prepare(Path(settings['worker_root']), brief))
        self.assertTrue((run / 'brief.json').exists())
        self.assertEqual(admit.count, 1, 'an OpenRouter worker was prepared without a routing admission')


class C2dCouncilAndProjectReview(command_tests.Base):
    """F08 LLM Council (Checkout feature), F09 Review my Project (Checkout feature)."""

    SKILL = ROOT / 'skills' / 'review-my-project'

    def text(self):
        return '\n'.join(path.read_text() for path in sorted(self.SKILL.rglob('*.md')))

    def test_ordinary_routing_never_reaches_council(self):
        """F08: Council runs only on explicit invocation; default-on routing has no op or setting that invokes it."""
        self.assertFalse(any('council' in op for op in command.FIELDS))
        response = self.call(dict(v=1, op='council', question='q'))
        self.assertEqual(response, dict(v=1, state='blocked', reason='unknown_op'))
        self.assertEqual(self.call(dict(v=1, op='set', expected_revision=0, change=dict(council=True))),
                         dict(v=1, state='blocked', reason='unknown_setting'))

    def test_project_review_keeps_scope_enrollment_and_fix_limits(self):
        """F09: the requested scope stays authoritative; review never enrolls, schedules or authorizes fixes."""
        text = self.text()
        for phrase in ('Review selected repositories and areas only', 'Never enroll or auto-join',
                       'A scan does not authorize installation or fixes',
                       'Do not run Actions, periodic scans, or automatic fixes',
                       'Do not turn this review into onboarding or enrollment',
                       'Review approval permits inspection only'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_a_review_assignment_cannot_be_admitted_as_an_implementer(self):
        """F09/F13: a delegated reviewer keeps its own role; routing cannot relabel it to borrow worker routes."""
        response = self.call(prepare('rv1', job=job(role='review')) | dict(
            assignment=dict(attempt_id='rv1', task='task-rv1', role='implementer', scope=SCOPE)))
        self.assertEqual(response, dict(v=1, state='blocked', reason='role_mismatch'))

    # C.3 must update the project-review entry point so delegated reviewers are routed through
    # `scripts/routing.py prepare` within the selected scope (plan C.3 "affected skill entry points"; F09).
    @needs_c3
    def test_project_review_routes_delegated_reviewers_through_the_routing_command(self):
        self.assertIn('scripts/routing.py', self.text(),
                      'review-my-project does not route its delegated reviewers through the routing command')


class C2eObservabilityReviewDelivery(Funded):
    """F10 Manage Observability, F13 tests/review/evidence, F14 worktrees/delivery/merge."""

    def test_disabled_export_inspects_nothing_and_local_accounting_stays_complete(self):
        """F10: export is opt-in; with it off nothing is prepared, while routing still settles locally."""
        class Watched(dict):
            reads = 0

            def __getitem__(self, key):
                Watched.reads += 1
                return super().__getitem__(key)

            def get(self, key, default=None):
                Watched.reads += 1
                return super().get(key, default)

        self.assertIsNone(observability.prepare_task_status(Watched(ok=True, task={}), enabled=False))
        self.assertEqual(Watched.reads, 0)
        self.assertEqual(self.call(prepare('e1'))['state'], 'admitted')
        self.assertEqual(self.call(dict(v=1, op='dispatch', attempt_id='e1',
                                        request=command_tests.facts()))['state'], 'dispatched')
        done = self.call(dict(v=1, op='record', event=dict(event_id='done-e1', attempt_id='e1', kind='complete',
                                                           usage=usage())))
        self.assertEqual(done['state'], 'recorded', done)
        settled = self.call(dict(v=1, op='settle', root_task_id='task-e1'))
        self.assertEqual((settled['attempts'], settled['cost_to_date']['tokens']['input']), (1, 10), settled)
        self.assertEqual((settled['acceptance'], settled['ratio']), ('unavailable', None))  # usage is not acceptance

    def test_routing_outputs_never_carry_keys_prompts_or_source(self):
        """F10: anything an exporter could read from routing is sanitized: no brief, key reference or account."""
        self.fund()
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(api_usage_enabled=True)))
        responses = [self.call(prepare('x1')), self.call(prepare('x2', observations=api_observations())),
                     self.call(dict(v=1, op='status')), self.call(dict(v=1, op='settle', root_task_id='task-x1'))]
        raw = json.dumps(responses)
        self.assertEqual([item['state'] for item in responses[:2]], ['admitted', 'admitted'], raw)
        for secret in (SECRET, KEY_REF, 'anthropic:acct', 'anthropic:org'):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, raw)

    def test_reviewer_independence_survives_routing(self):
        """F13: a reviewer is its own assignment and never collapses onto the implementer or its choices."""
        with self.assertRaisesRegex(ValueError, 'external_independent_review_required'):
            external_delegation.native_review(dict(integration_available=True, coordinator='same', reviewer='same'))
        self.call(dict(v=1, op='set', expected_revision=0, change=dict(
            smart_routing=False, worker_choices=dict(implementer=[CHOICE]))))
        review = self.call(prepare('rv2', job=job(role='review')) | dict(
            assignment=dict(attempt_id='rv2', task='task-rv2', scope=SCOPE)))
        self.assertEqual((review['state'], review['reason']), ('pending', 'worker_choice_unavailable'), review)
        worker = self.call(prepare('w2'))
        self.assertEqual(worker['state'], 'admitted', worker)  # the implementer list still serves implementers

    def test_routing_success_grants_no_delivery_authority(self):
        """F14: routing runs no git or host command and has no commit, push, merge or cleanup operation."""
        with side_channels() as counters:
            admitted = self.call(prepare('d1'))
            sent = self.call(dict(v=1, op='dispatch', attempt_id='d1', request=command_tests.facts()))
            self.call(dict(v=1, op='record', event=dict(event_id='done-d1', attempt_id='d1', kind='complete',
                                                        usage=usage())))
            self.call(dict(v=1, op='settle', root_task_id='task-d1'))
        self.assertEqual((admitted['state'], sent['state']), ('admitted', 'dispatched'))
        self.assertEqual(counters['popen'].count, 0)
        for op in ('commit', 'push', 'merge', 'publish', 'cleanup', 'delete_worktree'):
            with self.subTest(op=op):
                self.assertEqual(self.call(dict(v=1, op=op)), dict(v=1, state='blocked', reason='unknown_op'))
        self.assertLessEqual(set(admitted['admission']), set(command.ADMISSION_FIELDS))


if __name__ == '__main__':
    unittest.main()
