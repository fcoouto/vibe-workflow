"""Real disposable canonical births with explicit software-only qualification."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

from runtime import changes, review_storage, routing, routing_observations, startup
from scripts import routing as command
from tests.fixture_canonical_birth import fresh_canonical

PACKAGE = Path(__file__).resolve().parents[1]
_ACTUAL_ACQUIRE = startup.acquire_project_entry


def install_test_acquisition(observations):
    """Inject only synthetic owner observations; retain actual acquisition checks."""
    if any(value['current']['surface'] != 'fixture-only' for value in observations.values()):
        raise AssertionError('Only software-fixture qualification may be injected')

    def acquire(event, package, **kwargs):
        if 'qualifier' not in kwargs:
            kwargs['qualifier'] = lambda record, facts: observations.get(facts['observation']['root'])
        return _ACTUAL_ACQUIRE(event, package, **kwargs)

    return patch.object(startup, 'acquire_project_entry', side_effect=acquire)


def enroll(testcase):
    fixture, acquired, observation = fresh_canonical(testcase)
    if not hasattr(testcase, '_openrouter_observations'):
        testcase._openrouter_observations = {}
        injection = install_test_acquisition(testcase._openrouter_observations)
        injection.start()
        testcase.addCleanup(injection.stop)
    testcase._openrouter_observations.update(fixture.test_observations)
    testcase._openrouter_members = fixture.member_acquisitions
    return startup._entry_scope(acquired)


def fixture_command(testcase, script, *args):
    """Run an actual CLI under an explicit test driver, never a production grant."""
    path = Path(testcase.tmp.name) / 'test-acquisition.json'
    path.write_text(json.dumps(dict(observations=testcase._openrouter_observations,
        machine_config=str(review_storage._machine_config_path()), lock_root=str(changes.LOCK_ROOT))))
    path.chmod(0o600)
    driver = '''import json,runpy,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv.pop(1))
from tests.openrouter_fixtures import install_test_acquisition
from runtime import changes,review_storage
fixture=json.loads(Path(sys.argv.pop(1)).read_text())
target=sys.argv.pop(1)
with patch.object(changes,'LOCK_ROOT',Path(fixture['lock_root'])), patch.object(review_storage,'_machine_config_path',return_value=Path(fixture['machine_config'])), install_test_acquisition(fixture['observations']):
    if target=='-c':
        program=sys.argv.pop(1)
        sys.argv[0]='-c'
        exec(compile(program,'<openrouter-software-fixture>','exec'),{'__name__':'__main__'})
    else:
        sys.argv[0]=target
        runpy.run_path(target,run_name='__main__')
'''
    return [sys.executable, '-I', '-B', '-c', driver, str(PACKAGE), str(path), str(script), *map(str, args)]


def configure(repo, *, executable=None, auth=None, routes=('proposals', 'workers')):
    from runtime import openrouter_settings as settings
    report = settings.configure(repo, dict(expected_revision=0, routes=list(routes),
        opencode=str(executable) if executable else None, auth_data=str(auth) if auth else None))
    request = dict(expected_revision=report['revision'])
    if 'workers' in routes:
        request['consent_version'] = 2
    return settings.enable(repo, request)


@contextmanager
def managed_gateway(testcase, repo, *endpoints):
    """C.3 phase W: `repo` is a managed project whose qualified OpenRouter gateway routes are `endpoints`
    (`(model, provider)` pairs), funded on temporary routing stores. It is the C.2 `managed_project_fixture`
    (C.3 design 6.4), so default paths never reach real user state. Yields `{routes, settings, ledger}`."""
    from tests.test_smart_routing_features import fixture_route, managed_project_fixture
    routes = [fixture_route('openrouter:%s@%s' % pair, 'gateway') for pair in endpoints]
    with managed_project_fixture(testcase, repo, routes=routes) as stores:
        yield stores


class Funding:
    """A source double over the fixture's: `organization` renames the dollar windows' organization and `extra`
    routes come first in its fixed list (so they shape a shared pool). Lookups answer from that whole list."""
    def __init__(self, source, organization=None, extra=()):
        self.source, self.organization, self.extra = source, organization, list(extra)

    def manifest(self, table=None, choices=()):
        return self.extra + self.source.manifest(table, choices)

    def _fixed(self):
        return routing_observations.StaticSource(self.manifest())

    def admitted_route(self, route_id, identity=None):
        return self._fixed().admitted_route(route_id, identity)

    def pins_endpoint(self, route_id, identity, endpoint):
        return self._fixed().pins_endpoint(route_id, identity, endpoint)

    def seedable(self, pool, window):
        return self._fixed().seedable(pool, window)

    def routes_version(self, table=None):
        return None

    def windows(self, pool, now):
        return [dict(window, organization=self.organization) if self.organization and window.get('organization')
                else window for window in self.source.windows(pool, now)]

    def credential(self, route, scope):
        return self.source.credential(route, scope)


class ManagedRouting:
    """Spies and stores for managed-project tests; the class also needs `self.repo` and `enterContext`."""

    def manage(self, *endpoints):
        self.stores = self.enterContext(managed_gateway(self, self.repo, *(endpoints or [('model/v1', 'p')])))

    def ledger(self, request=None, *, one_shot=False):
        """The routing `status` ledger summary, or the answer to `request` (`one_shot`: the operator's command)."""
        service = command.Service(self.stores['settings'], self.stores['ledger'], one_shot=one_shot)
        try:
            answer = service.handle(json.dumps(request or dict(v=1, op='status')).encode())
        finally:
            service.close()
        return answer if request else answer['ledger']

    def holds(self):
        return self.ledger()['holds']

    def unsent(self, failure):
        """Begin and dispatch with `failure` active; the packet is never returned (nothing can be sent)."""
        self.manage()
        request = self.scoped_request()
        begun = self.call('begin', request)
        with failure:
            try:
                result = self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))
            except (ValueError, KeyboardInterrupt) as error:
                result = dict(raised=type(error).__name__)
        self.assertNotIn('packet', result)
        return result

    def assert_recovered(self, disposition='routed_unsent', holds=None):
        """The routed attempt's row is settled and the store usable: `record` has nothing left, and once the
        operator resolves any unknown cost the next `begin` is admitted."""
        [row] = self.call('history')['records']
        self.assertEqual(row['outcome']['disposition'], disposition)
        self.assertFalse({'dispatched_at', 'routing_state'} & set(row))
        self.assertEqual(self.holds(), {'cost_unknown': 1} if holds is None else holds)
        self.assertEqual(self.call('record', dict(attempt_token=row['attempt_token'], disposition='rejected'))
                         ['fallback'], 'already_recorded')
        if holds is None:
            resolved = self.ledger(dict(v=1, op='resolve_cost_unknown', attempt_id=row['routing']['attempt_id'],
                                        cost_usd_micros=1, event_id='resolve:1'), one_shot=True)
            self.assertEqual(resolved['state'], 'recorded', resolved)
        self.assertIn('attempt_token', self.call('begin', self.begin_request('task-2')))

    def admissions(self):
        calls, real = [], routing.admit

        def admit(assignment, *args, **kwargs):
            result = real(assignment, *args, **kwargs)
            calls.append((assignment, result))
            return result
        self.enterContext(patch.object(routing, 'admit', admit))
        return calls

    def recorded(self):
        """Every routing lifecycle event as (event, answer)."""
        events, real = [], routing.record_event

        def record(ledger, event, now):
            answer = real(ledger, event, now)
            events.append((event, answer))
            return answer
        self.enterContext(patch.object(routing, 'record_event', record))
        return events

    def prepared(self, then=None):
        bodies, real = [], command.Service._prepare

        def prepare(service, body):
            bodies.append(copy.deepcopy(body))
            answer = real(service, body)
            if then is not None:
                then(answer)
            return answer
        self.enterContext(patch.object(command.Service, '_prepare', prepare))
        return bodies

    def source(self, **options):
        original = routing_observations.default_source
        self.enterContext(patch.object(routing_observations, 'default_source',
                                       lambda *args, **kwargs: Funding(original(*args, **kwargs), **options)))

    def settled(self, cost):
        """Begin, dispatch and record one proposal with a self-reported `cost` (None: no usage). Returns the
        routing `complete` answer and the hold's reservation."""
        calls, events = self.admissions(), self.recorded()
        request = self.begin_request('cost-%s' % cost)
        begun = self.call('begin', request)
        self.assertTrue(self.call('dispatch', dict(request, attempt_token=begun['attempt_token']))['dispatched'])
        outcome = dict(attempt_token=begun['attempt_token'], disposition='rejected')
        if cost is not None:  # a coordinator's claim: no receipt the helper can verify
            outcome['usage'] = dict(source='provider_receipt', requests=[dict(
                kind='primary', model='model/v1', cost_usd_micros=cost,
                tokens=dict(input=10, output=5, reasoning=0, cache_read=0, cache_write=0))])
        self.assertTrue(self.call('record', outcome)['recorded'])
        [(_, answer)] = [item for item in events if item[0]['kind'] == 'complete']
        reserved = max(value for key, value in calls[-1][1]['reserved'].items() if key.startswith('org:'))
        return answer, reserved
