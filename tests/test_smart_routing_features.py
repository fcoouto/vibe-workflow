"""C.2: Smart Routing feature contracts F01-F17 (plan Task C.2; spec Appendix A), one class per group.

Every double here counts calls and makes no model, network or secret access. Contracts that already hold on `main`
are plain tests. Contracts that need Task C.3 (callers routed through `scripts/routing.py prepare`, legacy selectors
retired) carry `@NEEDS_C3('<slice>')` with a comment naming what that C.3 slice must change (C.3 design 6.4). Only
an AssertionError (or a skip) in the test body is the expected failure; any other exception, and any marked test that
passes, makes `tearDownModule` fail the module run, so the slice that makes a contract pass must remove its marker.
"""
from contextlib import ExitStack, contextmanager
import functools
import importlib.util
import inspect
import json
from pathlib import Path
import re
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
from tests import test_routing_dispatch as dispatch_tests
from tests import test_routing_gates as gate_tests
from tests.test_routing_command import KEY_REF, SCOPE, SECRET, api_observations, job, observations, prepare

ROOT = Path(__file__).resolve().parents[1]
CHOICE = dict(provider='anthropic', model='m', effort='high', harness='h', funding='subscription')
PROJECT = '/work/repo'  # the workflow locator cwd of tests.test_routing_gates
# The reasons runtime.routing defines for an assignment no enabled route can serve yet (`_decide`, `_dominant`).
READINESS_REASONS = ('no_feasible_route', 'credential_unavailable', 'no_funded_organization', 'concurrency_cap',
                     'worker_choice_unavailable')


class FixtureError(Exception):
    """A broken precondition. Not an AssertionError, so a NEEDS_C3 contract reports it as an error."""


def precondition(condition, message):
    if not condition:
        raise FixtureError(message)


SLICES = {'C3-0a', 'C3-0b', 'C3-1', 'C3-2', 'C3-3', 'C3-4a', 'C3-4b', 'C3-5', 'C3-6', 'C3-7'}
_NON_ASSERTION = []  # marked contracts that failed without an assertion (a crash is never "waiting on C.3")
_PASSED = []  # marked contracts that now hold: their slice must remove the marker


def NEEDS_C3(slice_id):  # noqa: N802 - the marker name the C.3 design (6.4) fixes
    """Mark a contract that fails on `main` until C.3 slice `slice_id` lands (C.3 design 6.4, A1)."""
    if slice_id not in SLICES:
        raise ValueError('unknown C.3 slice: %r' % (slice_id,))

    def mark(test):
        @functools.wraps(test)
        def run(self):
            try:
                test(self)
            except (AssertionError, unittest.SkipTest):
                raise
            except Exception as error:
                _NON_ASSERTION.append('%s: %s' % (test.__qualname__, type(error).__name__))
                raise
            _PASSED.append('remove the NEEDS_C3(%s) marker from %s' % (slice_id, test.__qualname__))
        run.needs_c3 = slice_id
        return unittest.expectedFailure(run)
    return mark


def marker_problems(non_assertion, passed):
    problems = []
    if non_assertion:
        problems.append('NEEDS_C3 contracts failed without an assertion: ' + ', '.join(non_assertion))
    if passed:
        problems.append('; '.join(passed))
    return '. '.join(problems)


def tearDownModule():  # noqa: N802 - unittest's module hook
    problem = marker_problems(_NON_ASSERTION, _PASSED)
    if problem:
        raise AssertionError(problem)


def fixture_route(route, billing_class):
    """One `managed_project_fixture` route entry: a route id and its billing class (C.3 design round 3)."""
    if billing_class not in ('included_subscription', 'gateway'):  # runtime.routing's class names
        raise ValueError('unknown billing class: %r' % (billing_class,))
    return dict(route=route, billing_class=billing_class)


@contextmanager
def managed_project_fixture(test, checkout, routes=()):
    """Make `checkout` a managed project with a qualified test route and fresh window telemetry.

    `routes` are `fixture_route` entries. Until C3-1 replaces the body it only raises, so it writes nothing."""
    # C3-1's body must patch every default path (routing settings and ledger, OpenRouter, usage settings) to the
    # test's temporary directory, so the fixture never touches real user state.
    # --- managed_project_fixture: body owned by C3-1 ---
    # Route-id mapping (C.3 design 6.4, G2). Pure: it writes nothing, so it runs before the slice check.
    import importlib
    import time
    efforts, hosts = ('none', 'low', 'medium', 'high', 'max'), (('openrouter_proposal', 'proposals'),
                                                                 ('openrouter_worker', 'workers'))
    mapped = []  # (route id, host, identity fields, billing class, pool, demand, extra manifest fields)
    for entry in routes:
        precondition(type(entry) is dict and set(entry) == {'route', 'billing_class'} and type(entry['route']) is str,
                     'malformed managed_project_fixture route: %r' % (entry,))
        route, billing = entry['route'], entry['billing_class']
        if route.startswith('native:') and '#' in route and billing == 'included_subscription':
            model, effort = route[len('native:'):].rsplit('#', 1)
            precondition(model and effort, 'malformed native fixture route: %r' % (route,))
            mapped.append((route, 'codex_native', dict(model=model, effort=effort), billing,
                           'fixture-codex-subscription', {'5h': 10}, dict(binding_enforceable=True)))
        elif route.startswith('openrouter:') and '@' in route and billing in ('dollar', 'gateway'):
            model, provider = route[len('openrouter:'):].rsplit('@', 1)
            precondition(model and provider, 'malformed OpenRouter fixture route: %r' % (route,))
            for host, integration in hosts:  # the id names neither host nor effort: list every one
                for effort in efforts:
                    mapped.append(('%s|%s|%s' % (route, host, effort), host,
                                   dict(model=model, provider=provider, effort=effort), 'gateway',
                                   'fixture-openrouter', {'credit': 1_000_000},
                                   dict(endpoint=provider, integration_route=integration)))
        else:
            raise FixtureError('unmapped managed_project_fixture route: %r (%s)' % (route, billing))
    # Rule 1: every slice the fixture stands on, as an assertion (never an AttributeError or ImportError).
    needed = (('C3-0b', 'runtime.routing_settings', 'managed_project'), ('C3-0b', 'scripts.routing', 'managed_service'),
              ('C3-1', 'runtime.routing_observations', 'default_source'),
              ('C3-1', 'runtime.routing_launch', 'route_identity'))
    modules = {}
    for slice_id, name, attribute in needed:
        try:
            modules[name] = importlib.import_module(name)
        except ImportError:
            raise AssertionError('managed_project_fixture: needs %s (%s)' % (slice_id, name)) from None
        if not callable(getattr(modules[name], attribute, None)):
            raise AssertionError('managed_project_fixture: needs %s (%s.%s)' % (slice_id, name, attribute))
    launch, observations = modules['runtime.routing_launch'], modules['runtime.routing_observations']
    from runtime import openrouter_settings, usage_settings
    # Rule 3: every default path the fixture or the code under test can reach, inside a temporary directory.
    temporary = tempfile.TemporaryDirectory()
    test.addCleanup(temporary.cleanup)
    base = Path(temporary.name).resolve()
    # The ledger has its own directory, so the default_ledger patch is observable apart from usage_settings'.
    settings, ledger = base / 'routing' / 'settings.json', base / 'ledger' / 'organizations.sqlite3'
    root = Path(checkout).resolve()

    def patch(target, attribute, value):
        patcher = mock.patch.object(target, attribute, value)
        patcher.start()
        test.addCleanup(patcher.stop)

    patch(command, 'default_settings', lambda: settings)
    patch(command, 'default_ledger', lambda: ledger)
    patch(usage_settings, 'directory', lambda: base / 'usage')
    patch(openrouter_settings, 'BASIC_STORAGE', base / 'openrouter-consent')
    for name, path in (('scripts.routing.default_settings', command.default_settings()),
                       ('scripts.routing.default_ledger', command.default_ledger()),
                       ('runtime.usage_settings.directory', usage_settings.directory()),
                       ('runtime.openrouter_settings.BASIC_STORAGE', openrouter_settings.BASIC_STORAGE)):
        precondition(base in Path(path).resolve().parents,
                     'managed_project_fixture: %s resolves outside the fixture directory' % name)
    precondition((command.default_settings(), command.default_ledger()) == (settings, ledger),
                 'managed_project_fixture: the routing command defaults are not the fixture stores')
    # Rule 4: fund each billing class on those stores (the routing command's `set`, the Ledger API for balances).
    service = command.Service(settings, ledger)
    try:
        def call(request):
            return service.handle(json.dumps(request).encode())
        subscriptions = [CHOICE] + [dict(launch.route_identity(host, **fields), funding='subscription')
                                    for _, host, fields, billing, *_ in mapped if billing == 'included_subscription']
        precondition(call(dict(v=1, op='set', authority_required=True)).get('state') == 'updated',
                     'managed_project_fixture: authority_required was not set')
        choices = dict(implementer=subscriptions, reviewer=subscriptions)
        precondition(call(dict(v=1, op='set', expected_revision=0, change=dict(worker_choices=choices))).get('state')
                     == 'updated', 'managed_project_fixture: worker_choices were not set')
        revision = None
        if any(billing == 'gateway' for _, _, _, billing, *_ in mapped):
            enabled = call(dict(v=1, op='set', organization=dict(id='fixture-openrouter', enabled=True,
                                                                  policy=command_tests.POLICY, expected_revision=0)))
            precondition(enabled.get('state') == 'updated', 'managed_project_fixture: organization not enabled')
            revision = enabled['organization']['revision']
            service._ledger(False, write=True).observe('fixture-openrouter', 10_000_000, int(time.time()),
                                                       issued=10_000_000)
    finally:
        service.close()
    # Rule 2: the checkout is managed, and the collector reads the fixture source.
    manifest = [dict(route='fixture-choice', status='qualified', **{key: CHOICE[key] for key in routing.ROUTE_IDENTITY},
                     billing_class='included_subscription', pool='fixture-choice-subscription',
                     account='fixture-choice-subscription', demand={'5h': 10}, binding_enforceable=True)]
    for route, host, fields, billing, pool, demand, extra in mapped:
        manifest.append(dict(route=route, status='qualified', **launch.route_identity(host, **fields),
                             billing_class=billing, pool=pool, account=pool, demand=demand, **extra))

    def windows(pool, now):
        if pool == 'fixture-openrouter':
            return [dict(id='credit', limit=10**12, used=0, length=None, reset_at=None, rolling=False, observed_at=now,
                         organization='fixture-openrouter', organization_revision=revision)]
        return [dict(id='5h', limit=1000, used=100, length=18000, reset_at=now + 3600, rolling=False,
                     observed_at=now)]

    def credential(route, scope):
        return dict(state='ready', scope=scope, ref='fixture-openrouter') if route['billing_class'] == 'gateway' \
            else None

    source = observations.StaticSource(manifest, windows, credential)
    patch(routing_settings, 'managed_project', lambda path, *args, **kwargs: Path(path).resolve() == root)
    patch(observations, 'default_source', lambda *args, **kwargs: source)
    yield dict(routes=manifest, settings=settings, ledger=ledger)
    # --- end managed_project_fixture ---


class UnexpectedCall(Exception):
    pass


class Counter:
    """A counting double. With `real` it passes calls through; without it, it refuses the call."""
    def __init__(self, real=None, name='call'):
        self.real, self.name, self.calls = real, name, []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.real is None:
            raise UnexpectedCall(self.name)
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


@contextmanager
def counted_admissions():
    admit = Counter(routing.admit)
    with mock.patch.object(routing, 'admit', admit):
        yield admit


def load_script(name):
    spec = importlib.util.spec_from_file_location('c2_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def usage():
    tokens = dict(input=10, output=5, reasoning=0, cache_read=0, cache_write=0)
    return dict(source='host_message_totals', requests=[dict(kind='primary', model='m', tokens=tokens)])


# Negations, exclusions and permissive or conditional wording: a sentence carrying any of them is never a positive,
# mandatory instruction. Deliberately over-strict: rejecting a real instruction is safe, accepting a prohibition is not.
NEGATION = re.compile(r"\b(?:not|never|don't|no|without|instead of|avoid\w*|skip\w*|unless|except\w*|only if|"
                      r"outside|may|might|can|could|optional\w*)\b", re.I)


def routes_reviewers_through_routing(text):
    """The sentences that positively route delegated reviewers through scripts/routing.py within the selected
    scope. A sentence with a negation, exclusion or permissive or conditional word never counts (NEGATION)."""
    return [sentence for sentence in re.split(r'(?<=[.!?])\s+|\n+', text)
            if 'scripts/routing.py' in sentence and re.search(r'\breviewers?\b', sentence, re.I)
            and re.search(r'\bselected\b', sentence, re.I) and not NEGATION.search(sentence)]


def bound_external(packet, coordinator='native-coordinator'):
    return dict(packet['binding'], automatic=True, local_preferred=False, class_name='investigation',
                coordinator=coordinator, reviewer='native-reviewer', integration_available=True)


class NeedsC3MarkerTests(unittest.TestCase):
    """The marker and fixture semantics (C.3 design 6.4): only an assertion or skip is the expected failure; a crash
    or a pass is recorded so the module run fails; the C3-1 fixture sentinel is an assertion."""

    def run_marked(self, body, slice_id='C3-0a'):
        case = type('Case', (unittest.TestCase,), dict(test=NEEDS_C3(slice_id)(body)))('test')
        result, non_assertion, passed = unittest.TestResult(), [], []
        with mock.patch.object(sys.modules[__name__], '_NON_ASSERTION', non_assertion), \
                mock.patch.object(sys.modules[__name__], '_PASSED', passed):
            case.run(result)
        return result, case, non_assertion, passed

    def test_an_assertion_or_skip_is_the_expected_outcome_and_records_nothing(self):
        def body(self):
            self.assertEqual(0, 1)
        result, case, non_assertion, passed = self.run_marked(body, 'C3-4a')
        self.assertEqual((len(result.expectedFailures), len(result.errors), len(result.failures)), (1, 0, 0))
        self.assertEqual((case.test.needs_c3, non_assertion, passed), ('C3-4a', [], []))
        result, _, non_assertion, passed = self.run_marked(lambda self: self.skipTest('host unavailable'))
        self.assertEqual((len(result.skipped), non_assertion, passed), (1, [], []))

    def test_any_other_exception_is_recorded_and_fails_the_module(self):
        for error in (FixtureError('broken fixture'), AttributeError('missing'), ValueError('regression')):
            def body(self, error=error):
                raise error
            with self.subTest(error=type(error).__name__):
                _, _, non_assertion, passed = self.run_marked(body)
                self.assertEqual(len(non_assertion), 1)
                self.assertIn(type(error).__name__, non_assertion[0])
                self.assertEqual(passed, [])
                self.assertIn('failed without an assertion', marker_problems(non_assertion, passed))

    def test_a_passing_contract_asks_for_its_marker_to_be_removed(self):
        result, _, non_assertion, passed = self.run_marked(lambda self: None, 'C3-6')
        self.assertEqual(len(result.unexpectedSuccesses), 1)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(non_assertion, [])
        self.assertIn('remove the NEEDS_C3(C3-6) marker', marker_problems(non_assertion, passed))

    def test_module_teardown_raises_only_when_something_was_recorded(self):
        module = sys.modules[__name__]
        with mock.patch.object(module, '_NON_ASSERTION', []), mock.patch.object(module, '_PASSED', []):
            tearDownModule()  # nothing recorded: nothing raised
        with mock.patch.object(module, '_NON_ASSERTION', ['T.test: KeyError']), \
                mock.patch.object(module, '_PASSED', []), self.assertRaisesRegex(AssertionError, 'KeyError'):
            tearDownModule()

    def test_an_unknown_slice_is_refused(self):
        for bad in ('C3', 'C3-0', 'C3-8', 'c3-0a', None):
            with self.subTest(slice=bad), self.assertRaises(ValueError):
                NEEDS_C3(bad)

    def test_the_managed_project_fixture_refuses_unmapped_route_ids(self):
        reached = []
        for route in (fixture_route('test-route', 'included_subscription'), fixture_route('native:m#high', 'gateway'),
                      fixture_route('openrouter:m@p', 'included_subscription'), dict(route='native:m#high')):
            with self.subTest(route=route), self.assertRaises(FixtureError):
                with managed_project_fixture(self, ROOT, routes=(route,)):
                    reached.append(True)
        self.assertEqual(reached, [])  # a broken precondition never runs the contract body
        for bad in ('dollar', 'purchased_usage', 'api_grant'):
            with self.subTest(billing_class=bad), self.assertRaises(ValueError):
                fixture_route('r', bad)
        source = Path(__file__).read_text()
        start = source.index('# --- managed_project_fixture: body owned by C3-1 ---')
        self.assertLess(start, source.index('# --- end managed_project_fixture ---'))


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

    def test_enablement_is_reported_separately_from_readiness(self):
        """F01/F17: missing evidence leaves Smart Routing enabled, waits with a defined readiness reason and never
        blocks the deterministic path (RF8): an unusable table is reported on the admission it degraded."""
        self.store_table(None)  # no compiled strategy table yet: enabled, not ready
        with side_channels() as counters:
            response = self.call(prepare('r1'))
        self.assertEqual(response['state'], 'pending', response)
        self.assertIn(response['reason'], READINESS_REASONS)
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
        """F15: what a title or visibility badge can show is a defined reason code, never brief text or a key."""
        request = prepare('n1', observations=api_observations())
        request['observations']['routes'] = request['observations']['routes'][1:]  # the API route only
        response = self.call(request)
        self.assertEqual(response['state'], 'pending', response)
        self.assertIn(response['reason'], READINESS_REASONS)
        raw = json.dumps(response) + json.dumps(self.call(dict(v=1, op='status')))
        for secret in (SECRET, KEY_REF):
            self.assertNotIn(secret, raw)


class C2aOnboardingKeepsExplicitOff(unittest.TestCase):
    """F02/F17: onboarding and startup leave an explicit Smart Routing OFF untouched, in the very HOME they use."""

    def setUp(self):
        from runtime import startup
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name) / 'usage' / 'organizations.sqlite3'
        self.owner = CanonicalBirthFixture('runTest')
        self.addCleanup(self.owner.doCleanups)
        self.prepared = self.owner.prepare()  # patches HOME to the birth's isolated home until cleanup
        self.home = self.prepared[0].base / 'isolated-home'
        precondition(Path.home() == self.home, 'the birth fixture did not isolate HOME')
        self.settings = command.default_settings()
        precondition(self.home in self.settings.parents, 'routing settings are outside the onboarding HOME')
        self.project_file = Path(self.prepared[1]['manifest']['enrollment']['record']['project_root']) \
            / startup.PROJECT_FILE

    def call(self, request):
        service = command.Service(self.settings, self.ledger)
        try:
            return service.handle(json.dumps(request).encode())
        finally:
            service.close()

    def snapshot(self):
        """The settings file's bytes and identity, or None when it is gone (a deletion is a contract failure)."""
        try:
            info = self.settings.stat()
            return self.settings.read_bytes(), info.st_ino, info.st_mtime_ns
        except FileNotFoundError:
            return None

    def test_explicit_off_survives_onboarding_and_startup(self):
        current = self.call(dict(v=1, op='status'))['settings_revision']  # written fresh, whatever was there
        off = self.call(dict(v=1, op='set', expected_revision=current, change=dict(smart_routing=False)))
        precondition(off.get('state') == 'updated', 'explicit OFF could not be written')
        before = self.snapshot()
        self.owner.assert_applied_configured(self.prepared, self.owner.invoke(self.prepared))  # real onboarding
        _, acquired, _ = self.owner.qualify_members(self.prepared[0], self.project_file)  # startup acquisition
        precondition(acquired.resolution.scope is not None and Path.home() == self.home,
                     'startup did not acquire the project in the onboarding HOME')
        self.assertEqual(self.snapshot(), before, 'onboarding or startup changed the explicit OFF settings')
        status = self.call(dict(v=1, op='status'))
        self.assertEqual((status['settings_state'], status['settings'] and status['settings']['smart_routing']),
                         ('ok', False), 'Smart Routing is no longer explicitly OFF')
        self.assertEqual(status['settings_revision'], current + 1)


class PinnedGates(gate_tests.GateBase):
    """The S3.5 gates with the service pinned to the locator's project, as C.3 requires for production."""

    def setUp(self):
        super().setUp()
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, workflow=self.workflow, project=PROJECT)


class C2bWorkstreamsDelegationResources(PinnedGates):
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
        sent = self.call(dispatch_tests.dispatch('c1'))
        self.assertEqual(sent['state'], 'dispatched', sent)
        second = self.prepared('c2')
        self.assertFalse(second['duplicate'])
        self.assertEqual(second['profile']['semantic_digest'], first['profile']['semantic_digest'])
        self.assertNotEqual(second['admission']['attempt_id'], first['admission']['attempt_id'])
        resent = self.call(dispatch_tests.dispatch('c2'))
        self.assertEqual(resent['state'], 'dispatched', resent)
        self.assertNotEqual(resent['authority'], sent['authority'])  # no authority carries over

    def test_changed_scope_or_brief_invalidates_the_prepared_selection(self):
        """F11/F16: a changed brief, role or write scope makes the prepared route stale before anything is sent."""
        changes = dict(brief=dict(semantic=gate_tests.semantic(objective='Rewritten objective')),
                       role=dict(semantic=gate_tests.semantic(role='review')),
                       scope=dict(claims=[['/work/repo/other.py', 'write']]))
        for name, change in changes.items():
            with self.subTest(change=name):
                self.workflow.write(dict(build=gate_tests.entry()))
                self.prepared('s-' + name)
                self.workflow.update('build', **change)
                self.assertRefused('s-' + name, 'binding_stale')
                self.release('s-' + name)  # free the account's concurrency place for the next change

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


class C2bQuotaResetIsNotAuthority(command_tests.Base):
    """F12: a quota reset neither resumes a waiting assignment nor restarts a spent attempt."""

    def exhausted(self):
        value = observations()
        value['pools']['sub']['windows'][0]['used'] = 1000  # the 5h window is spent
        return value

    def test_a_reset_window_resumes_nothing_by_itself(self):
        waiting = self.call(prepare('q1', observations=self.exhausted()))
        self.assertEqual(waiting['state'], 'pending', waiting)
        self.assertNotIn('q1', self.snapshot()['holds'])  # waiting holds nothing to resume
        after_reset = self.call(dispatch_tests.dispatch('q1'))  # the window reset: still no authority without prepare
        self.assertEqual(after_reset['state'], 'blocked', after_reset)
        self.assertNotIn('authority', after_reset)
        self.assertEqual(self.call(prepare('q2'))['state'], 'admitted')
        self.assertEqual(self.call(dispatch_tests.dispatch('q2'))['state'], 'dispatched')
        done = self.call(dispatch_tests.record('q2', 'complete', usage=usage()))
        self.assertEqual(done['state'], 'recorded', done)
        again = self.call(dispatch_tests.dispatch('q2'))  # fresh headroom does not restart a spent attempt
        self.assertNotEqual(again['state'], 'dispatched', again)
        self.assertNotIn('authority', again)


class C2bProjectPin(gate_tests.GateBase):
    """F03 (S3.5 wiring): production prepare of a workflow job needs the project pin. Unpinned service."""

    def setUp(self):
        # Its own explicitly unpinned service, independent of GateBase.setUp's (which C3-0b pins).
        super().setUp()
        self.service.close()
        self.service = command.Service(self.settings, self.ledger, workflow=self.workflow)

    # C3-0b must make `--project` mandatory for a workflow-bound `prepare` (S3.5 wiring list): an unpinned service
    # refuses it with `project_required`, as `settle` already does.
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
        self.request = self.fixture.prepare_request()

    def prepare_counted(self):
        with counted_admissions() as admit:
            prepared = self.fixture.api.prepare_assignment(self.fixture.scope, self.request)
        precondition(prepared.get('launch_admitted') is True, 'the native fixture admitted no launch')
        return admit, prepared

    def managed(self):
        selection = self.request['selection']  # the fixture's own explicit native choice: a subscription route
        route = fixture_route('native:%s#%s' % (selection['model'], selection['effort']), 'included_subscription')
        return managed_project_fixture(self, self.fixture.repo, routes=(route,))

    # C3-2 must replace the caller-supplied native `selection` with `scripts/routing.py prepare` on a managed project:
    # the native worker admission calls the routing service in-process exactly once (plan C.3 box 1; F11).
    def test_native_worker_admission_calls_routing_prepare_once(self):
        with self.managed():
            admit, _ = self.prepare_counted()
            self.assertEqual(admit.count, 1, 'native worker admission did not go through routing prepare')

    # C3-2 must bind that call to the planned job with `workflow` (job_id `edit`) and the delegation attempt id,
    # after delegation's own reservation (S3.5 C.3 wiring list; RF3).
    def test_native_worker_admission_is_bound_to_its_workflow_job(self):
        with self.managed():
            admit, prepared = self.prepare_counted()
            bound = [(args, kwargs['workflow']) for args, kwargs in admit.calls if kwargs.get('workflow') is not None]
            self.assertTrue(bound, 'routing admission for a planned job carried no workflow binding')
            (assignment, *_), workflow = bound[0]
            self.assertEqual(workflow['locator']['job_id'], 'edit')
            self.assertEqual(assignment['attempt_id'], prepared['attempt_id'],
                             'the routing admission is not the delegation attempt')


class Funded(command_tests.Base):
    def fund(self, organization='personal'):
        self.service._ledger(True, write=True)  # the ledger exists before the user's explicit enablement
        super().fund(organization)


class C2cApiUsageAndOpenRouter(Funded):
    """F04 Manage API Usage, F06 proposals, F07 workers (local contracts)."""

    def api_only(self, attempt):
        request = prepare(attempt, observations=api_observations())
        request['observations']['routes'] = request['observations']['routes'][1:]
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

    # C3-7 must retire the caller-owned automatic route selector: selection is `scripts/routing.py prepare` only
    # (plan C.3 box 1; spec F11, F06), with permission and source checks kept as constraints.
    @NEEDS_C3('C3-7')
    def test_external_delegation_keeps_no_caller_owned_selector(self):
        candidates = [dict(route='openrouter:model/v1', enabled=True, suitable=True, automatic=True)]
        selectors = []
        for name, function in inspect.getmembers(external_delegation, inspect.isfunction):
            if function.__module__ != external_delegation.__name__:
                continue
            try:
                inspect.signature(function).bind(None, False, candidates)
                answer = function(None, False, candidates)
            except Exception:  # noqa: BLE001 - a function that cannot take candidates selects nothing
                continue
            if isinstance(answer, dict) and answer.get('route') == candidates[0]['route']:
                selectors.append(name)
        self.assertEqual(selectors, [], 'external_delegation still chooses a route from caller candidates')

    # C3-7 must retire the Usage Routing Smart Balance decision that `scripts/routing.py` replaces (plan C.3 box 3):
    # `scripts/usage_routing.py evaluate` stops existing, with no compatibility alias.
    @NEEDS_C3('C3-7')
    def test_usage_routing_cli_no_longer_selects_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            done = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / 'usage_routing.py'), 'evaluate',
                                   '--cwd', temp], input=b'{}', capture_output=True, timeout=60)
        self.assertEqual(done.returncode, 2, 'usage_routing.py evaluate still answers a routing decision: '
                         + done.stdout.decode()[:200])
        self.assertIn(b'invalid choice', done.stderr)


class EnrolledOpenRouter(unittest.TestCase):
    def setUp(self):
        from tests.openrouter_fixtures import enroll
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = enroll(self).repository_root


class C2cOpenRouterActivation(EnrolledOpenRouter):
    """F05: OpenRouter keeps its own activation and funding."""

    def test_smart_routing_and_api_usage_leave_openrouter_activation_unchanged(self):
        from runtime import openrouter_settings
        before = openrouter_settings.status(self.repo)
        self.assertIsNone(before['activation'])
        root = Path(self.temp.name)
        service = command.Service(root / 'routing' / 'settings.json', root / 'usage' / 'organizations.sqlite3')
        try:
            on = service.handle(json.dumps(dict(v=1, op='set', expected_revision=0, change=dict(
                smart_routing=True, api_usage_enabled=True))).encode())
        finally:
            service.close()
        self.assertEqual(on['state'], 'updated', on)
        self.assertEqual(openrouter_settings.status(self.repo), before)


class C2cProposalThroughRouting(EnrolledOpenRouter):
    """F06: an approved OpenRouter proposal is admitted through the common routing service."""

    def setUp(self):
        super().setUp()
        from tests import test_openrouter_proposals as proposals
        from tests.openrouter_fixtures import configure
        api = load_script('openrouter_proposals')
        settings = configure(self.repo, routes=('proposals',))
        store = Path(settings['proposal_store'])
        call = lambda action, request=None: api.run(action, store, request or {}, now=100, cwd=self.repo)
        token = call('claim')['refresh_token']
        call('publish', dict(refresh_token=token, metadata=proposals.METADATA))
        consent = external_delegation.effective_permission(
            settings['external_permission'], settings['activation'], service='openrouter', mode='proposal',
            model='model/v1', provider='p', effort=None, class_name='investigation')
        packet = external_delegation.prepare(self.repo, consent, [dict(kind='text', text='Fixture objective',
                                                                        sources=[])])
        request = dict(task_id='task-1', profile=proposals.PROFILE, model='model/v1', provider='p', effort=None,
                       snapshot_digest=call('status')['snapshot_digest'], base_ref='git:abc', output_tokens=500,
                       timeout_seconds=60, requirements=dict(context_tokens=100, output_tokens=500,
                                                             modalities=['text'], parameters=[]),
                       external=bound_external(packet))
        self.begin = lambda: call('begin', request)
        self.route = fixture_route('openrouter:%s@%s' % (request['model'], request['provider']), 'gateway')

    # C3-4a must route an approved OpenRouter proposal through `scripts/routing.py prepare` (plan C.3 box 1; F06):
    # `begin` obtains one routing admission, keeping endpoint pinning and scope as constraints.
    def test_proposal_begin_obtains_one_routing_admission(self):
        with managed_project_fixture(self, self.repo, routes=(self.route,)):
            with counted_admissions() as admit:
                begun = self.begin()
            precondition('attempt_token' in begun, 'the proposal fixture did not begin')
            self.assertEqual(admit.count, 1, 'an OpenRouter proposal began without a routing admission')


class C2cWorkerThroughRouting(EnrolledOpenRouter):
    """F07: each OpenRouter worker is admitted through the common routing service."""

    def setUp(self):
        super().setUp()
        from tests.openrouter_fixtures import configure
        self.workers = load_script('openrouter_workers')
        root = Path(self.temp.name).resolve()
        (root / 'auth').mkdir(mode=0o700)
        executable = root / 'worker'
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o700)
        settings = configure(self.repo, executable=executable, auth=root / 'auth', routes=('workers',))
        self.runs = Path(settings['worker_root'])
        (self.repo / 'input.txt').write_text('three lines\nof evidence\nfor review\n')
        brief = dict(task_id='pilot-1', coordinator_chat_id=str(uuid.uuid4()), model='vendor/model',
                     provider='provider', objective='Read input.txt and count lines.', source_root=str(self.repo),
                     files=['input.txt'], timeout_seconds=5, steps=3, effort='low')
        consent = external_delegation.effective_permission(
            settings['external_permission'], settings['activation'], service='openrouter', mode='worker',
            model='vendor/model', provider='provider', effort='low', class_name='investigation')
        packet = external_delegation.prepare(self.repo, consent, [dict(kind='text', text=brief['objective'],
                                                                        sources=brief['files'])])
        self.brief = dict(brief, external=bound_external(packet, brief['coordinator_chat_id']))

    # C3-4b must route each OpenRouter worker through `scripts/routing.py prepare` (plan C.3 box 1; F07): `prepare`
    # obtains one routing admission and records the requested route.
    @NEEDS_C3('C3-4b')
    def test_worker_prepare_obtains_one_routing_admission(self):
        route = fixture_route('openrouter:%s@%s' % (self.brief['model'], self.brief['provider']), 'gateway')
        with managed_project_fixture(self, self.repo, routes=(route,)):
            with counted_admissions() as admit:
                run = self.workers.prepare(self.runs, self.brief)
            precondition((run / 'brief.json').exists(), 'the worker fixture prepared no run')
            self.assertEqual(admit.count, 1, 'an OpenRouter worker was prepared without a routing admission')


class C2dCouncilAndProjectReview(command_tests.Base):
    """F08 LLM Council (Checkout feature), F09 Review my Project (Checkout feature)."""

    SKILL = ROOT / 'skills' / 'review-my-project'

    def text(self):
        return '\n'.join(path.read_text() for path in sorted(self.SKILL.rglob('*.md')))

    def test_ordinary_routing_never_reaches_council(self):
        """F08: Council runs only on explicit invocation; default-on routing has no op or setting that invokes it.
        Regression guard: Council itself is Checkout-only, so its members and independence are not tested here."""
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

    def test_the_review_instruction_check_accepts_only_a_positive_instruction(self):
        """The C3-6 marker cannot be satisfied by a prohibition or a sentence that routes something else."""
        for text in ('Do not use scripts/routing.py for delegated reviewers outside the selected scope.',
                     'Reviewers must not use scripts/routing.py; selected areas are reviewed natively.',
                     'Never send reviewers through scripts/routing.py for selected repositories.',
                     'Route delegated reviewers through scripts/routing.py without the selected scope.',
                     'Use scripts/routing.py for workers in the selected scope.',
                     'Reviewers in the selected scope should avoid `scripts/routing.py`.',
                     'Reviewers in the selected scope may skip scripts/routing.py.',
                     'Reviewers outside the selected scope go through scripts/routing.py.',
                     'Only if the user asks, route reviewers through scripts/routing.py in the selected scope',
                     'Unless the user objects, reviewers in the selected scope use scripts/routing.py.',
                     'Except for the selected scope, reviewers go through scripts/routing.py.',
                     'Reviewers in the selected scope can optionally use scripts/routing.py.'):
            with self.subTest(text=text):
                self.assertEqual(routes_reviewers_through_routing(text), [])
        positive = 'Route each delegated reviewer through scripts/routing.py prepare within the selected scope.'
        self.assertEqual(routes_reviewers_through_routing(positive), [positive])

    # C3-6 must update the project-review instructions so delegated reviewers are routed through
    # `scripts/routing.py prepare` within the selected scope (plan C.3 "affected skill entry points"; F09).
    def test_project_review_routes_delegated_reviewers_through_the_routing_command(self):
        routed = routes_reviewers_through_routing(self.text())
        self.assertTrue(routed, 'review-my-project has no instruction routing delegated reviewers through '
                                'scripts/routing.py within the selected scope')


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
        self.assertEqual(self.call(dispatch_tests.dispatch('e1'))['state'], 'dispatched')
        done = self.call(dispatch_tests.record('e1', 'complete', usage=usage()))
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
            sent = self.call(dispatch_tests.dispatch('d1'))
            self.call(dispatch_tests.record('d1', 'complete', usage=usage()))
            self.call(dict(v=1, op='settle', root_task_id='task-d1'))
        self.assertEqual((admitted['state'], sent['state']), ('admitted', 'dispatched'))
        self.assertEqual(counters['popen'].count, 0)
        self.assertLessEqual(set(admitted['admission']), set(command.ADMISSION_FIELDS))
        # Regression guard: the command has no delivery operation today; adding one must fail this contract.
        for op in ('commit', 'push', 'merge', 'publish', 'cleanup', 'delete_worktree'):
            with self.subTest(op=op):
                self.assertEqual(self.call(dict(v=1, op=op)), dict(v=1, state='blocked', reason='unknown_op'))


if __name__ == '__main__':
    unittest.main()
