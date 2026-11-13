"""C3-3: the OpenCode managed launcher (C.3 design 3.1-3.7, 6.3 C3-3) against a fake server and the real routing
command on temporary stores. No model call, no OpenCode server, no network."""
import inspect
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

from runtime import opencode_launcher as launcher, routing, routing_launch, routing_observations as collector, \
    routing_settings, state, subscription_billing
from runtime.adapters import opencode as adapter
from scripts import routing as command
from tests.test_routing_observations import SCOPE, Stores, ready, request_of, sub_window

PROVIDER, MODEL, VARIANT = 'claude-agent', 'haiku', 'low'
HARNESS = adapter.HARNESS[PROVIDER]
POOL = 'claude-sub'
ACCOUNT = 'anthropic:test'
TOKENS = dict(input=2, output=4, reasoning=0, cache=dict(read=40, write=6))
STATIC_PROJECT_COMMAND = re.compile(r"""['"][^'"\n]*/command\b|\.command\(|session\.command""")


def route(**extra):
    value = dict(route='claude-haiku-low', status='qualified', provider=PROVIDER, model=MODEL, effort=VARIANT,
                 harness=HARNESS, billing_class='included_subscription', pool=POOL, account=ACCOUNT,
                 demand={'5h': 10}, binding_enforceable=True, window_unit=collector.WINDOW_UNIT)
    value.update(extra)
    return value


def gateway(**extra):
    value = dict(route='or-worker', status='qualified', provider='openrouter', model='vendor/model', effort='low',
                 harness=adapter.HARNESS.get('openrouter', 'opencode provider openrouter'), billing_class='gateway',
                 pool='gw', account='gw-account', demand={'credit': 1_000_000}, integration_route='workers')
    value.update(extra)
    return value


class FakeHost:
    """An OpenCode server double with the recorded 2.2.6 shapes. The shim's decisions (attach, every managed model
    request) go to the REAL routing command through `shim` (a `--serve`-like service on the test's stores)."""

    def __init__(self, shim, directory, order, **knobs):
        self.shim, self.directory, self.order = shim, directory, order
        self.sessions, self.attached, self.counter, self.tokens_seen, self.requests = {}, {}, 0, [], []
        self.health, self.denies, self.version = 'ok', list(adapter.MANAGED_DENIES), adapter.QUALIFIED_VERSION
        self.models = [dict(providerID=PROVIDER, id=MODEL, variants=[VARIANT, 'high']),
                       dict(providerID='openrouter', id='vendor/model', variants=['low'])]
        self.attach_ok, self.prompt_status, self.prompt_raises, self.descendants_ok = True, 200, None, True
        self.upstream_error, self.rate_limit, self.child, self.health_after = None, None, False, None
        self.child_error, self.message_model, self.descendants_raises, self.prompt_hook = None, None, False, None
        # `GET /api/agent`: None lists every built-in agent with the carried step cap, as the inline config sets it
        self.agents, self.agent_status, self.agent_raises = None, 200, None
        self.__dict__.update(knobs)

    def _id(self, prefix):
        self.counter += 1
        return '%s_1bf%023dAa' % (prefix, self.counter)

    def _check(self, session, attempt, token, pool, model, kind='primary'):
        provider, rest = model.split('/', 1)
        name, _, variant = rest.partition('#')  # no `#`: no variant, null as the shim's routeFacts reports it
        facts = dict(provider=provider, model=name, variant=variant or None, harness=adapter.HARNESS.get(
            provider, 'opencode provider %s' % provider), kind=kind, funding_pool=pool)
        answer = self.shim.handle(json.dumps(dict(v=1, op='check', attempt_id=attempt, session_id=session,
                                                  authority=token, request=facts)).encode())
        return answer.get('state') == 'allow' and answer.get('authority') == 'verified'

    def rpc(self, method, payload):
        self.order.append('rpc:' + method)
        self.requests.append(('rpc', method))
        if method == 'health':
            if self.health_after is not None and self.order.count('rpc:health') > self.health_after:
                return 500, dict(type='rpc.internal')
            if self.health != 'ok':
                return 200, dict(state='not_ready', reason='ledger_absent', version=1)
            return 200, dict(state='ok', child='up', version=1, respawn_ms=0, managed_denies=list(self.denies))
        token = payload.get('authority')
        self.tokens_seen.append(token)
        session = payload['session_id']
        if method == 'attach':
            if self.attach_ok and self._check(session, payload['attempt_id'], token, payload['funding_pool'],
                                              payload['model']):
                self.attached[session] = dict(payload)
                return 200, dict(state='attached', session_id=session)
            return 500, dict(type='rpc.internal', message='RPC call failed')
        held = self.attached.get(session)
        if held is None or (held['attempt_id'], held['authority']) != (payload['attempt_id'], token):
            return 500, dict(type='rpc.internal', message='RPC call failed')
        if method == 'detach':
            del self.attached[session]
            return 200, dict(state='detached', session_id=session)
        if method == 'descendants' and self.descendants_raises:
            raise ConnectionResetError('descendants call lost')
        if method == 'descendants' and self.descendants_ok:
            children = [dict(session_id=sid, parent_session_id=item['record']['parentID'])
                        for sid, item in sorted(self.sessions.items()) if item['record']['parentID'] == session]
            return 200, dict(state='ok', root=session, descendants=children, truncated=False, complete=True,
                             reason=None, cache=dict(recorded=len(children), retired=0))
        return 500, dict(type='rpc.internal', message='RPC call failed')

    def _session(self, sid, parent=None, model=None, title=None):
        record = dict(id=sid, parentID=parent, title=title, model=model, cost=0, time=dict(created=int(time.time() * 1000)),
                      tokens=dict(input=0, output=0, reasoning=0, cache=dict(read=0, write=0)))
        self.sessions[sid] = dict(record=record, messages=[])
        return record

    def _assistant(self, sid, held, now):
        session = self.sessions[sid]
        model = session['record']['model']
        message = dict(id=self._id('msg'), type='assistant', agent='build', finish='stop', cost=0, model=dict(model),
                       tokens=json.loads(json.dumps(TOKENS)), time=dict(created=now, completed=now + 1))
        if self.message_model is not None:
            message['model'] = dict(self.message_model)
        if self.upstream_error is not None:
            message.update(finish='error', tokens=dict(input=0, output=0, reasoning=0, cache=dict(read=0, write=0)),
                           error=dict(self.upstream_error))
        if self.rate_limit is not None:
            message['native'] = dict(rateLimit=self.rate_limit)
        session['messages'].append(message)
        session['record']['tokens'] = json.loads(json.dumps(message['tokens']))

    def request(self, method, path, body=None, query=None):
        self.requests.append((method, path))
        if '/command' in path:
            raise AssertionError('a project command was invoked in a managed session')
        now = int(time.time() * 1000)
        parts = path.strip('/').split('/')
        if (method, path) == ('GET', '/api/info'):
            return 200, dict(version=self.version)
        if (method, path) == ('GET', '/api/model'):
            return 200, dict(data=self.models)
        if (method, path) == ('GET', '/api/agent'):
            if self.agent_raises is not None:
                raise self.agent_raises
            agents = self.agents
            if agents is None:
                steps = (getattr(self, 'options', None) or {}).get('steps')
                agents = [dict(id=name, name=name, mode='primary', hidden=False, permissions=[], request={},
                               **({} if steps is None else dict(steps=steps))) for name in launcher.BUILTIN_AGENTS]
            return self.agent_status, dict(data=agents)
        if (method, path) == ('POST', '/api/session'):
            self.order.append('create')
            self.created = dict(body)
            record = self._session(body.get('id') or self._id('ses'), model=body['model'], title=body.get('title'))
            return 200, dict(data=record)
        if (method, path) == ('GET', '/api/session/active'):
            return 200, dict(data={})
        if (method, path) == ('GET', '/api/session'):
            return 200, dict(data=[item['record'] for item in self.sessions.values()])
        if parts[2] not in self.sessions:
            return 404, dict(type='not_found')
        session = self.sessions[parts[2]]
        if method == 'GET' and len(parts) == 3:
            return 200, dict(data=session['record'])
        if method == 'GET' and parts[3] == 'message':
            if query and 'cursor' in query:
                return 200, dict(data=[], cursor=dict(previous='p', next=None))
            return 200, dict(data=list(session['messages']), cursor=dict(previous=None, next='n'))
        if method == 'POST' and parts[3] == 'prompt':
            self.order.append('prompt')
            if self.prompt_hook is not None:
                self.prompt_hook()
            if self.prompt_raises is not None:
                raise self.prompt_raises
            sid, held = parts[2], self.attached.get(parts[2])
            session['messages'].append(dict(id=body.get('id') or self._id('msg'), type='user', time=dict(created=now)))
            # The shim's `model.request` check of the session's own requested model (not the attach string).
            requested = session['record']['model'] or {}
            spoken = '%s/%s' % (requested.get('providerID'), requested.get('id')) + (
                '#' + requested['variant'] if requested.get('variant') is not None else '')
            allowed = held is not None and self._check(sid, held['attempt_id'], held['authority'], held['funding_pool'],
                                                       spoken)
            if allowed:
                self._assistant(sid, held, now)
                if self.child:
                    child = self._session(self._id('ses'), parent=sid, model=session['record']['model'])
                    child_id = child['id']
                    if self.child_error is not None:
                        self.sessions[child_id]['messages'].append(dict(
                            id=self._id('msg'), type='assistant', agent='build', finish='error', cost=0,
                            model=dict(session['record']['model']), error=dict(self.child_error),
                            tokens=dict(input=0, output=0, reasoning=0, cache=dict(read=0, write=0)),
                            time=dict(created=now, completed=now + 1)))
                    self.sessions[child_id]['messages'].append(dict(id=self._id('msg'), type='idle', outcome='succeeded',
                                                                    time=dict(created=now + 1)))
            session['messages'].append(dict(id=self._id('msg'), type='idle', outcome='succeeded' if allowed else 'failed',
                                            time=dict(created=now + 2)))
            return self.prompt_status, dict(data=session['messages'][0])
        raise AssertionError((method, path))


class Logged(command.Service):
    """The managed service, logging the ops it answers into the shared order list."""

    order = None

    def handle(self, raw, op=None):
        value = json.loads(raw)
        name = value.get('op')
        if name == 'record':
            name = 'record:' + value['event']['kind']
        self.order.append(name)
        return super().handle(raw, op)


class LauncherBase(Stores):
    def setUp(self):
        super().setUp()
        self.checkout = self.root / 'checkout'
        self.checkout.mkdir()
        self.service.close()
        self.order = []
        Logged.order = self.order
        self.service = Logged(self.settings, self.ledger_path, project=self.checkout)
        self.addCleanup(self.service.close)
        self.shim = command.Service(self.settings, self.ledger_path, serving=True)
        self.addCleanup(self.shim.close)
        self.routes = [route()]
        self.windows = {POOL: [sub_window(self.now)], 'gw': []}
        patches = [mock.patch.object(collector, 'default_source', lambda settings_path=None: collector.StaticSource(
                       self.routes, self.windows, ready,  # a labelled double supplies positive billing
                       billing=subscription_billing.double('opencode-launcher'))),
                   mock.patch.object(routing_settings, 'managed_project', new=self.managed_project),
                   mock.patch.object(launcher, 'IDLE_POLL', 0), mock.patch.object(launcher, 'REREAD_WAIT', 0)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(self.call(dict(v=1, op='set', authority_required=True))['state'], 'updated')
        self.host = FakeHost(self.shim, str(self.checkout), self.order)

    def managed_project(self, path, settings_path=None):
        return Path(path).resolve() == self.checkout.resolve()

    def assignment(self, attempt='a1', **choice):
        identity = dict(provider=PROVIDER, model=MODEL, effort=VARIANT, harness=HARNESS, funding='subscription')
        identity.update(choice)
        # The scope gateway credentials are bound to (the collector derives it from the OpenRouter report).
        return dict(attempt_id=attempt, task='task-' + attempt, scope=routing_launch.assignment_scope(self.checkout),
                    choice=identity)

    def launch(self, **options):
        options.setdefault('assignment', self.assignment())
        options.setdefault('job', routing_launch.unplanned_job('software-engineer', SCOPE, 'Do the fixture task'))
        options.setdefault('prompt', 'Do the fixture task')
        return launcher.launch(self.checkout, host=options.pop('host', self.host),
                               service=options.pop('service', self.service), **options)

    def holds(self):
        return self.service._ledger(False).snapshot()['holds']

    def files(self):
        """Every byte the launch could have written: the temporary stores and anything else under the test root."""
        found = b''
        for path in self.root.rglob('*'):
            if path.is_file():
                found += path.read_bytes()
        return found


class LaunchTests(LauncherBase):
    def test_a_managed_launch_runs_the_handshake_and_settles(self):
        result = self.launch()
        self.assertEqual(result['state'], 'recorded', result)
        self.assertEqual((result['attempt_id'], result['event']), ('a1', 'complete'))
        self.assertEqual(self.holds()['a1']['state'], 'released')
        self.assertEqual(self.host.attached, {})  # detached
        created = self.host.created
        self.assertEqual(created['model'], dict(id=MODEL, providerID=PROVIDER, variant=VARIANT))
        self.assertEqual(created['location'], dict(directory=str(self.checkout)))
        self.assertTrue(created['title'])  # titled at creation: no title request
        self.assertTrue(created['id'].startswith('ses_'))

    def test_the_launch_order_follows_the_contract(self):
        """Health before dispatch; dispatch before attach; attach before the session exists; descendants, then
        `record complete`, then detach (clients/opencode.md#handshake)."""
        self.launch()
        order = [item for item in self.order if item in (
            'prepare', 'dispatch', 'rpc:health', 'rpc:attach', 'create', 'prompt', 'rpc:descendants',
            'record:complete', 'rpc:detach')]
        self.assertLess(order.index('prepare'), order.index('dispatch'))
        last_health = max(index for index, item in enumerate(order) if item == 'rpc:health')
        self.assertLess(order.index('prepare'), last_health)  # health is re-checked right before dispatch
        self.assertLess(last_health, order.index('dispatch'))
        self.assertEqual(order[order.index('dispatch'):], ['dispatch', 'rpc:attach', 'create', 'prompt',
                                                           'rpc:descendants', 'record:complete', 'rpc:detach'])

    def test_the_token_never_appears_in_output_or_files(self):
        result = self.launch()
        self.assertEqual(result['state'], 'recorded')
        tokens = {token for token in self.host.tokens_seen if token}
        self.assertEqual(len(tokens), 1)
        token = tokens.pop()
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn(token.encode(), self.files())

    def test_seen_passes_through(self):
        result = self.launch()
        self.assertEqual(result.get('seen'), dict(openrouter_revision=None))

    def test_the_session_profile_denies_external_directories_shells_and_fetches(self):
        self.launch()
        rules = self.host.created['permissions']
        for action in ('external_directory', 'shell', 'bash', 'webfetch', 'websearch'):
            with self.subTest(action=action):
                self.assertEqual(launcher.rule_effect(rules, action), 'deny')
        self.assertEqual(launcher.rule_effect(rules, 'read'), 'allow')
        self.assertEqual(launcher.rule_effect(rules, 'edit'), 'allow')  # inside the project; external_directory denied

    def test_a_listed_but_refused_model_writes_a_not_entitled_mark_and_never_retries(self):
        self.host.upstream_error = dict(type='APIError', status=400, message='upstream refused', response=dict(
            body=json.dumps(dict(error=dict(type='invalid_request_error', code='model_not_found')))))
        result = self.launch()
        self.assertEqual(result['state'], 'recorded', result)  # the attempt settles normally
        self.assertIs(result['not_entitled'], True)
        marks = collector.entitlement_marks(self.settings)
        self.assertEqual([(mark['route'], mark['account'], mark['mark']) for mark in marks],
                         [('claude-haiku-low', ACCOUNT, 'not_entitled')])
        self.assertEqual(len([r for r in self.host.requests if r == ('POST', '/api/session')]), 1)  # no retry
        again = self.launch(assignment=self.assignment('a2'))  # the collector now excludes the route
        self.assertEqual(again['state'], 'pending', again)

    def test_a_successful_launch_stores_its_rate_limit_window(self):
        resets = self.now + 3000
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=resets,
                                    unifiedWindows=dict(five_hour=dict(utilization=0.25, resetsAt=resets,
                                                                       status='allowed')))
        self.assertEqual(self.launch()['state'], 'recorded')
        stored = collector.LocalSource(self.settings).windows(POOL, self.now)
        self.assertEqual(len(stored), 1)
        window = stored[0]
        self.assertEqual((window['id'], window['limit'], window['used'], window['reset_at'], window['length'],
                          window['rolling']), ('5h', 1000, 250, resets, 18000, False))

    def test_an_unmanaged_project_behaves_as_before_and_touches_no_host(self):
        other = self.root / 'other'
        other.mkdir()
        service = Logged(self.settings, self.ledger_path, project=other)
        self.addCleanup(service.close)
        result = launcher.launch(other, host=self.host, service=service, assignment=self.assignment(),
                                 job=routing_launch.unplanned_job('software-engineer', SCOPE, 'x'), prompt='x')
        self.assertEqual(result, dict(state='unmanaged', reason='project_not_managed'))
        self.assertEqual(self.host.requests, [])


class HostRuleTests(LauncherBase):
    """F4.4 and the effort omission contract on the OpenCode launcher."""

    def test_host_version_metadata_does_not_pin_managed_launches(self):
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=self.now + 3000,
            unifiedWindows=dict(five_hour=dict(utilization=0.25, resetsAt=self.now + 3000, status='allowed')))
        for index, version in enumerate(('2.2.7', '2.3.0', '3.0.0', '10.0.0', None, 'unknown')):
            with self.subTest(version=version):
                self.host.version = version
                result = self.launch(assignment=self.assignment('version-%d' % index))
                self.assertEqual(result['state'], 'recorded', result)

    def test_enforcement_requires_real_health_even_when_version_passes(self):
        self.host.version = '3.0.0'
        for answer in (None, {}, dict(state='not_ready', managed_denies=list(adapter.MANAGED_DENIES))):
            with self.subTest(answer=answer):
                self.assertEqual(launcher.enforcement(self.host, answer), 'advisory')

    def test_unknown_info_does_not_replace_positive_plugin_health(self):
        original = self.host.request
        for response in ((503, None), (200, {})):
            with self.subTest(response=response), mock.patch.object(self.host, 'request', side_effect=lambda method,
                    path, body=None, query=None: response if path == '/api/info' else original(method, path, body, query)):
                self.assertEqual(launcher.enforcement(self.host, launcher.health(self.host)), 'enforced')

    def test_the_request_carries_the_opencode_hosts_harnesses(self):
        prepared = []
        real = command.Service._prepare
        with mock.patch.object(command.Service, '_prepare', lambda service, body: prepared.append(body) or
                               real(service, body)):
            self.assertEqual(self.launch()['state'], 'recorded')
        # The catalog lists claude-agent and openrouter; only the subscription path's harness is sent.
        self.assertEqual(prepared[0]['constraints'], dict(allowed_harnesses=[HARNESS]))

    def test_a_catalog_naming_no_path_sends_nothing(self):
        self.host.models = [dict(providerID='unlisted', id='m', variants=['low'])]
        result = self.launch()
        self.assertEqual(result, dict(state='blocked', reason='harness_required'))
        self.assertNotIn('prepare', self.order)

    def test_a_launch_without_an_effort_omits_the_variant_end_to_end(self):
        """The admitted identity carries `none`; attach, the session, the shim's model check and accounting all see
        an omitted variant, never a literal `none`, and the hold settles."""
        self.host.models = [dict(providerID=PROVIDER, id=MODEL)]  # no variants: only omission is expressible
        self.routes = [route(effort='none')]
        result = self.launch(assignment=self.assignment(effort='none'))
        self.assertEqual(result['state'], 'recorded', result)
        self.assertEqual(self.host.created['model'], dict(id=MODEL, providerID=PROVIDER))
        self.assertNotIn('#', json.dumps([self.host.attached, self.host.created]))
        [assistant] = [item for session in self.host.sessions.values() for item in session['messages']
                       if item['type'] == 'assistant']  # the shim's check allowed the model request
        self.assertNotIn('variant', assistant['model'])
        self.assertEqual(self.holds()['a1']['state'], 'released')
        self.assertNotIn('"none"', json.dumps(self.host.created))


class RefusalTests(LauncherBase):
    """Each refusal happens before any session is created (C.3 design 6.3 C3-3)."""

    def assert_refused(self, reason, result, closed=None):
        self.assertEqual((result['state'], result.get('reason')), ('blocked', reason), result)
        self.assertNotIn(('POST', '/api/session'), self.host.requests)
        self.assertNotIn('prompt', self.order)
        if closed is not None:
            self.assertEqual(self.holds()[closed]['state'], 'released')  # `not_dispatched` closed the admission

    def test_an_unhealthy_host_is_refused_before_prepare(self):
        self.host.health = 'not_ready'
        self.assert_refused('host_unhealthy', self.launch())
        self.assertNotIn('prepare', self.order)

    def test_a_health_failure_right_before_dispatch_closes_the_admission(self):
        self.host.health_after = 1  # the first health answer is ok; the one before dispatch is an RPC error
        self.assert_refused('host_unhealthy', self.launch(), closed='a1')
        self.assertNotIn('dispatch', self.order)

    def test_an_advisory_profile_is_refused(self):
        for knob in (dict(denies=[]), dict(version='2.2.5'), dict(version='2.2.6-rc.1')):
            with self.subTest(knob=knob):
                self.host.__dict__.update(knob)
                self.assert_refused('profile_not_enforced', self.launch())
                self.assertNotIn('prepare', self.order)
                self.host.denies, self.host.version = list(adapter.MANAGED_DENIES), adapter.QUALIFIED_VERSION

    def test_a_catalog_mismatch_sends_nothing(self):
        self.host.models = [dict(providerID=PROVIDER, id=MODEL, variants=['high'])]
        self.assert_refused('catalog_mismatch', self.launch(), closed='a1')

    def test_a_not_entitled_route_is_refused_before_dispatch(self):
        self.assertEqual(self.launch()['state'], 'recorded')
        collector.mark_not_entitled(self.settings, 'claude-haiku-low', ACCOUNT, self.now)
        self.order.clear()
        self.host.requests.clear()
        result = self.launch(assignment=self.assignment('a2'))
        self.assertEqual(result['state'], 'pending')  # the collector already leaves it out
        self.assertNotIn(('POST', '/api/session'), self.host.requests)

    def test_an_api_funded_route_is_refused_until_qualified(self):
        from runtime import openrouter_settings
        self.fund('gw-org')
        self.routes = [gateway()]
        self.windows['gw'] = [dict(id='credit', limit=10**12, used=0, length=None, reset_at=None, rolling=False,
                                   observed_at=self.now, organization='gw-org')]
        report = dict(enabled=True, routes=['workers'], local_configuration='ready', revision=3,
                      checkout=str(self.checkout), activation=dict(ref='activation:openrouter:s:r3'))
        choice = dict(provider='openrouter', model='vendor/model', effort='low', harness=gateway()['harness'],
                      funding='api')
        prepared = []
        real = command.Service._prepare
        with mock.patch.object(openrouter_settings, 'status', lambda cwd: dict(report)), \
                mock.patch.object(command.Service, '_prepare', lambda service, body: prepared.append(body) or
                                  real(service, body)):
            result = self.launch(assignment=self.assignment('g1', **choice))
        # F4.4: the OpenCode host's harnesses exclude the dollar paths' until API funding is qualified, so the
        # gateway choice is never admitted (no hold to close) and nothing is created.
        self.assertEqual((result['state'], result['reason']), ('pending', 'explicit_choice_unavailable'), result)
        self.assertNotIn(('POST', '/api/session'), self.host.requests)
        self.assertNotIn('g1', self.holds() if self.ledger_path.exists() else {})
        self.assertEqual(prepared[0]['constraints']['allowed_harnesses'], [HARNESS])
        self.assertFalse(launcher.API_FUNDING_QUALIFIED)

    def test_the_gate_still_refuses_an_api_funded_dispatch(self):
        """Defense in depth below the host filter: the gate refuses a dollar admission before `dispatch`."""
        gate = launcher._Gate(self.service, self.host)
        gate.admission = dict(billing_class='gateway', attempt_id='g2')
        answer = gate.handle(json.dumps(dict(v=1, op='dispatch', attempt_id='g2', request={})).encode())
        self.assertEqual((answer['state'], answer['reason']), ('blocked', 'api_funding_unqualified'))
        self.assertNotIn('dispatch', self.order)

    def test_a_refused_attach_creates_nothing_and_closes_the_hold(self):
        self.host.attach_ok = False
        result = self.launch()
        self.assert_refused('attach_failed', result)
        self.assertEqual(self.holds()['a1']['state'], 'released')
        self.assertIn('record:complete', self.order)  # nothing was created, so nothing was sent

    def test_preflight_refusals_touch_no_host(self):
        fresh = self.root / 'fresh'
        service = Logged(fresh / 'routing' / 'settings.json', fresh / 'usage' / 'organizations.sqlite3',
                         project=self.checkout)
        self.addCleanup(service.close)
        with mock.patch.object(routing_settings, 'managed_project', new=lambda path, settings_path=None: True):
            result = launcher.launch(self.checkout, host=self.host, service=service, assignment=self.assignment(),
                                     job=routing_launch.unplanned_job('software-engineer', SCOPE, 'x'), prompt='x')
        self.assertEqual((result['state'], result['reason']), ('blocked', 'authority_not_required'))
        self.assertEqual(self.host.requests, [])


class LostAcknowledgmentTests(LauncherBase):
    def test_a_failed_prompt_records_uncertain_and_names_the_operator_op(self):
        self.host.prompt_raises = ConnectionResetError('lost')
        result = self.launch()
        self.assertEqual((result['state'], result['reason']), ('uncertain', 'launch_unconfirmed'), result)
        self.assertEqual(result['recovery'], 'reconcile_subscription')
        self.assertIn('scripts/routing.py', result['command'])
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')
        self.assertEqual(self.host.attached, {})  # detached: the shim blocks the attempt's sessions again
        self.assertNotIn('dispatch', self.order[self.order.index('dispatch') + 1:])  # never a second dispatch

    def test_a_server_error_on_the_prompt_is_unconfirmed_too(self):
        self.host.prompt_status = 502
        self.assertEqual(self.launch()['reason'], 'launch_unconfirmed')
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')

    def test_the_launcher_never_runs_an_operator_op(self):
        self.host.prompt_raises = TimeoutError('lost')
        self.launch()
        self.assertFalse(set(self.order) & set(command.OPERATOR_OPS))


class AccountingTests(LauncherBase):
    def test_descendants_are_read_before_detach_and_children_are_accounted(self):
        self.host.child = True
        result = self.launch()
        self.assertEqual(result['state'], 'recorded', result)
        self.assertLess(self.order.index('rpc:descendants'), self.order.index('rpc:detach'))

    def test_a_failed_descendants_call_passes_none(self):
        """`shim_descendants: None` makes an API cost unknown (and is only a flag on subscription routes)."""
        self.host.descendants_ok = False
        captured = {}
        real = launcher.opencode_accounting.usage_from_messages

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return real(*args, **kwargs)
        with mock.patch.object(launcher.opencode_accounting, 'usage_from_messages', spy):
            self.assertEqual(self.launch()['state'], 'recorded')
        self.assertIn('shim_descendants', captured)
        self.assertIsNone(captured['shim_descendants'])

    def test_shim_descendants_none_makes_an_api_cost_unknown(self):
        self.host.descendants_ok = False
        self.host.attached['ses_root'] = dict(attempt_id='x', authority='t')
        self.host._session('ses_root', model=dict(id=MODEL, providerID=PROVIDER, variant=VARIANT))
        self.host._assistant('ses_root', None, int(time.time() * 1000))
        self.host.sessions['ses_root']['record']['cost'] = 0.01
        self.host.sessions['ses_root']['messages'][0]['cost'] = 0.01
        self.host.sessions['ses_root']['messages'].append(dict(id='msg_idle1', type='idle', outcome='succeeded',
                                                               time=dict(created=int(time.time() * 1000) + 5)))
        read = launcher.account(self.host, 'ses_root', funding='api', attempt='x', authority='t')
        self.assertIn('shim_lineage_not_checked', read['result']['unknown'])
        costs = [request.get('cost_usd_micros') for request in read['result']['usage']['requests']]
        self.assertEqual(costs, [None] * len(costs))


class StaticTests(unittest.TestCase):
    def test_no_project_command_is_ever_invoked(self):
        for path in (state.PACKAGE / 'runtime' / 'opencode_launcher.py', state.PACKAGE / 'scripts' / 'opencode_launch.py'):
            with self.subTest(path=path.name):
                self.assertIsNone(STATIC_PROJECT_COMMAND.search(path.read_text()))

    def test_the_launcher_uses_the_c3_1_core_and_the_shared_factory(self):
        source = inspect.getsource(launcher)
        self.assertIn('routing_launch.launch(', source)
        self.assertIn('managed_service(', source)
        self.assertNotIn('Service(default_settings', source)

    HOSTILE = dict(PATH='/project/node_modules/.bin:/usr/bin', HOME='/h', USER='u', LANG='C.UTF-8', TMPDIR='/t',
                   XDG_DATA_HOME='/h/.data', ANTHROPIC_API_KEY='k', GITHUB_TOKEN='t', DB_PASSWORD='p',
                   OPENROUTER_API_KEY='o', NODE_OPTIONS='--require /project/x.js', BUN_INSTALL='/b', BUN_OPTIONS='x',
                   PYTHONPATH='/project', PYTHONHOME='/p', PYTHONSTARTUP='/project/s.py', LD_PRELOAD='/x.so',
                   DYLD_INSERT_LIBRARIES='/x.dylib', OPENCODE_CONFIG='/project/c.json', OPENCODE_CONFIG_DIR='/p',
                   XDG_CONFIG_HOME='/project/.config', GIT_DIR='/project', SHELL='/bin/zsh', EDITOR='vim')

    def test_the_server_environment_is_exactly_the_allowlist(self):
        cleaned = launcher.host_environment(self.HOSTILE)
        self.assertEqual(set(cleaned), {'PATH', 'HOME', 'USER', 'LANG', 'TMPDIR', 'XDG_DATA_HOME'})
        self.assertEqual(cleaned['PATH'], launcher.SERVER_PATH)  # fixed, never the caller's (project-local) PATH
        self.assertEqual({key: cleaned[key] for key in ('HOME', 'USER', 'LANG', 'TMPDIR', 'XDG_DATA_HOME')},
                         {key: self.HOSTILE[key] for key in ('HOME', 'USER', 'LANG', 'TMPDIR', 'XDG_DATA_HOME')})
        self.assertFalse([name for name in launcher.ENV_ALLOWED if adapter.credential_name(name)])
        self.assertEqual(set(launcher.host_environment(dict(HOME='/h'))), {'HOME', 'PATH'})  # absent names stay absent

    def test_the_inline_config_pins_the_plugin_and_disables_mcp_formatters_and_lsp(self):
        config = launcher.server_config(Path('/x/state/routing/settings.json'),
                                        Path('/x/state/usage/organizations.sqlite3'), Path('/plugins/routing'))
        self.assertEqual([item['package'] for item in config['plugins']], ['/plugins/routing'])
        self.assertEqual(config['mcp'], {})
        self.assertIs(config['formatter'], False)
        self.assertIs(config['lsp'], False)
        self.assertEqual(config['permissions'], launcher.WORKER_RULES)
        self.assertEqual(launcher.rule_effect(config['permissions'], 'external_directory'), 'deny')
        self.assertEqual(set(config), {'$schema', 'plugins', 'mcp', 'formatter', 'lsp', 'permissions'})

    def test_the_server_config_protects_the_routing_state(self):
        settings, ledger = Path('/x/state/routing/settings.json'), Path('/x/state/usage/organizations.sqlite3')
        config = launcher.server_config(settings, ledger, Path('/plugins/routing'))
        options = config['plugins'][0]['options']
        self.assertEqual((options['settings'], options['ledger']), (str(settings), str(ledger)))
        self.assertIn('/x/state/routing', options['protected_paths'])
        self.assertIn('/x/state/usage', options['protected_paths'])
        self.assertEqual(adapter.extra_paths(options['protected_paths']) is None, False)  # valid for the shim


class ServerStartTests(unittest.TestCase):
    """`start_server` against a fake `opencode` executable: the environment it gets and the config it loads."""

    def test_the_started_server_gets_a_stripped_environment_and_the_launch_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            record = root / 'seen.json'
            fake = root / 'opencode'
            fake.write_text(textwrap.dedent('''\
                #!%s
                import json, os, sys, time
                with open(%r, 'w') as out:
                    json.dump(dict(argv=sys.argv[1:], env=dict(os.environ), cwd=os.getcwd()), out)
                print('server listening on http://127.0.0.1:1', flush=True)
                time.sleep(30)
                ''') % (sys.executable, str(record)))
            fake.chmod(0o700)
            checkout = root / 'checkout'
            checkout.mkdir()
            settings, ledger = root / 'state' / 'routing' / 'settings.json', root / 'state' / 'usage' / 'l.sqlite3'
            environ = dict(StaticTests.HOSTILE, HOME=str(root), GITHUB_TOKEN='secret-value', TMPDIR=str(root))
            server = launcher.start_server(checkout, settings_path=settings, ledger_path=ledger, opencode=str(fake),
                                           environ=environ, timeout=10)
            try:
                seen = json.loads(record.read_text())
            finally:
                server.stop()
            allowed = {'PATH', 'HOME', 'USER', 'LANG', 'TMPDIR', 'XDG_DATA_HOME', 'OPENCODE_CONFIG_CONTENT',
                       'OPENCODE_SERVER_PASSWORD', *launcher.SERVER_FLAGS}
            # macOS adds __CF_USER_TEXT_ENCODING to every process; nothing else may appear
            self.assertEqual(set(seen['env']) - allowed - {'__CF_USER_TEXT_ENCODING'}, set())
            self.assertEqual({key: seen['env'].get(key) for key in launcher.SERVER_FLAGS}, launcher.SERVER_FLAGS)
            self.assertEqual(launcher.SERVER_FLAGS['OPENCODE_DISABLE_PROJECT_CONFIG'], '1')
            for name in ('NODE_OPTIONS', 'BUN_INSTALL', 'PYTHONPATH', 'GITHUB_TOKEN', 'OPENCODE_CONFIG'):
                self.assertNotIn(name, seen['env'])
            self.assertEqual(seen['cwd'], str(checkout))
            self.assertEqual(seen['argv'][:3], ['serve', '--hostname', '127.0.0.1'])
            config = json.loads(seen['env']['OPENCODE_CONFIG_CONTENT'])
            options = config['plugins'][0]['options']
            self.assertIn(str(settings.parent), options['protected_paths'])
            self.assertTrue(seen['env']['OPENCODE_SERVER_PASSWORD'])
            self.assertEqual(server.directory, str(checkout))
            self.assertFalse(server.workdir.exists())  # the private plugin wrapper and log are removed


class SelfCheckTests(unittest.TestCase):
    def test_the_self_check_passes_in_a_child_and_leaves_home_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            workdir.chmod(0o700)
            for name in ('home', 'tmp'):
                (workdir / name).mkdir(mode=0o700)
            code = ('import json, sys; sys.path.insert(0, sys.argv[1]); from runtime import opencode_launcher; '
                    'print(json.dumps(opencode_launcher.managed_self_check(sys.argv[2])))')
            env = dict(PATH='/usr/bin:/bin', HOME=str(workdir / 'home'), TMPDIR=str(workdir / 'tmp'), LANG='C.UTF-8')
            started = time.monotonic()
            done = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(state.PACKAGE), str(workdir / 'check')],
                                  env=env, cwd=workdir, capture_output=True, timeout=10, start_new_session=True)
            elapsed = time.monotonic() - started
            home = sorted(str(path) for path in (workdir / 'home').rglob('*'))
        self.assertEqual(done.returncode, 0, done.stderr.decode()[-2000:])
        self.assertEqual(json.loads(done.stdout), [])
        self.assertEqual(home, [])
        self.assertLess(elapsed, 10)

    def test_the_self_check_body_refuses_to_run_outside_its_child(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(launcher._self_check_in_child(Path(temp) / 'body'), ['self_check_not_in_child'])

    def test_the_activation_gate_names_this_capability(self):
        self.assertIn(('opencode_launcher', 'module', 'runtime.opencode_launcher:managed_self_check'),
                      command.CAPABILITIES)


class CommandLineTests(LauncherBase):
    def test_the_cli_refuses_bad_input_without_echoing_it(self):
        script = state.PACKAGE / 'scripts' / 'opencode_launch.py'
        for raw in (b'not json', json.dumps(dict(v=1, op='launch', secret='do-not-echo')).encode()):
            with self.subTest(raw=raw[:20]):
                done = subprocess.run([sys.executable, '-I', '-B', str(script)], input=raw, capture_output=True,
                                      timeout=30, env=dict(PATH='/usr/bin:/bin', HOME=str(self.root)))
                answer = json.loads(done.stdout)
                self.assertEqual(answer['state'], 'blocked')
                self.assertNotIn(b'do-not-echo', done.stdout + done.stderr)

    def test_the_cli_launch_prints_no_token(self):
        from scripts import opencode_launch
        stdout = []
        request = dict(v=1, op='launch', checkout=str(self.checkout), assignment=self.assignment(),
                       job=routing_launch.unplanned_job('software-engineer', SCOPE, 'x'), prompt='x')
        server = mock.Mock(directory=str(self.checkout))
        server.request, server.rpc = self.host.request, self.host.rpc
        with mock.patch.object(opencode_launch.opencode_launcher, 'start_server', return_value=server), \
                mock.patch.object(opencode_launch.command, 'managed_service', return_value=self.service):
            code = opencode_launch.main([], stdin=json.dumps(request).encode(), write=stdout.append)
        self.assertEqual(code, 0)
        answer = json.loads(stdout[0])
        self.assertEqual(answer['state'], 'recorded', answer)
        token = [item for item in self.host.tokens_seen if item][0]
        self.assertNotIn(token, stdout[0])
        server.stop.assert_called_once_with()


REFUSED = dict(type='APIError', status=400, message='upstream refused', response=dict(
    body=json.dumps(dict(error=dict(type='invalid_request_error', code='model_not_found')))))


class FlakySource:
    """A manifest that answers the core's entitlement check, then cannot be read (the account lookup)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, 0

    def admitted_route(self, route_id, identity=None):
        self.calls += 1
        if self.calls > 1:
            raise OSError('manifest unreadable')
        return collector.StaticSource(self.routes).admitted_route(route_id, identity)

    def billing(self, route, now):
        return subscription_billing.double('flaky-source')(route, now)


class NotEntitledTests(LauncherBase):
    """S3 (review of #218): only the primary session's own 400 `model_not_found` on the admitted route is a mark."""

    def assert_no_mark(self, result, mark=None):
        self.assertEqual(result['state'], 'recorded', result)
        self.assertIs(result['not_entitled'], False)
        self.assertEqual(collector.entitlement_marks(self.settings), [])
        self.assertEqual(result.get('entitlement_mark'), mark)

    def test_a_child_sessions_refusal_writes_no_mark(self):
        self.host.child, self.host.child_error = True, REFUSED
        self.assert_no_mark(self.launch())

    def test_a_refusal_for_another_model_writes_no_mark(self):
        self.host.upstream_error = REFUSED
        self.host.message_model = dict(id=MODEL, providerID=PROVIDER, variant='high')
        self.assert_no_mark(self.launch())

    def test_a_400_without_model_not_found_writes_no_mark(self):
        self.host.upstream_error = dict(REFUSED, response=dict(body=json.dumps(dict(error=dict(code='rate_limited')))))
        self.assert_no_mark(self.launch())

    def test_an_unknown_account_writes_no_mark_and_says_why(self):
        self.host.upstream_error = REFUSED
        result = self.launch(source=FlakySource(self.routes))
        self.assertEqual(result['state'], 'recorded', result)
        self.assertIs(result['not_entitled'], True)
        self.assertEqual(result['entitlement_mark'], 'account_unknown')
        self.assertEqual(collector.entitlement_marks(self.settings), [])


class DescendantsRaiseTests(LauncherBase):
    def test_a_descendants_call_that_raises_passes_none(self):
        """S4: the RPC raising (not answering non-200) still gives `shim_descendants: None`, never `{}`."""
        self.host.descendants_raises = True
        captured = {}
        real = launcher.opencode_accounting.usage_from_messages

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return real(*args, **kwargs)
        with mock.patch.object(launcher.opencode_accounting, 'usage_from_messages', spy):
            self.assertEqual(self.launch()['state'], 'recorded')
        self.assertIn('shim_descendants', captured)
        self.assertIsNone(captured['shim_descendants'])


class Terminated(BaseException):
    pass


class InterruptTests(LauncherBase):
    """S2: an interrupt past the core's `except Exception` never leaves a dispatched hold without a record."""

    def test_an_interrupt_during_the_prompt_records_uncertain_and_re_raises(self):
        self.host.prompt_raises = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.launch()
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')
        self.assertEqual(self.host.attached, {})  # still detached

    def test_an_interrupt_after_record_complete_changes_nothing(self):
        real = launcher._Attempt.detach

        def interrupted(attempt):
            real(attempt)
            raise Terminated()
        with mock.patch.object(launcher._Attempt, 'detach', interrupted), self.assertRaises(Terminated):
            self.launch()
        self.assertEqual(self.holds()['a1']['state'], 'released')  # `complete` won; the late `uncertain` is refused

    def test_an_interrupt_before_prepare_leaves_nothing(self):
        with mock.patch.object(launcher, 'catalog', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.launch()
        self.assertNotIn('dispatch', self.order)
        self.assertEqual(self.holds(), {})

    def test_an_interrupt_after_admission_before_dispatch_closes_the_hold(self):
        with mock.patch.object(routing_launch, 'admitted_check', side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            self.launch()
        self.assertNotIn('dispatch', self.order)
        self.assertEqual(self.holds()['a1']['state'], 'released')  # `not_dispatched`, the core's event id
        self.assertIn('record:not_dispatched', self.order)

    def interrupting(self, on_dispatch):
        """The managed service with one interrupt injected around `dispatch` (`on_dispatch(super_handle, raw)`)."""
        service, order = self.service, self.order

        class Interrupting(Logged):
            fired = False

            def handle(self, raw, op=None):
                if json.loads(raw).get('op') == 'dispatch' and not Interrupting.fired:
                    Interrupting.fired = True
                    order.append('dispatch')
                    return on_dispatch(lambda: command.Service.handle(self, raw, op))
                return super().handle(raw, op)
        interrupting = Interrupting(service.settings_path, service.ledger_path, project=self.checkout)
        self.addCleanup(interrupting.close)
        return interrupting

    def test_an_interrupt_after_a_refused_dispatch_closes_the_hold_instead_of_uncertain(self):
        def refused(handle):
            raise KeyboardInterrupt  # the dispatch never ran: the hold is still only admitted
        with self.assertRaises(KeyboardInterrupt):
            self.launch(service=self.interrupting(refused))
        self.assertEqual(self.holds()['a1']['state'], 'released')

    def test_an_interrupt_after_a_refused_answer_closes_the_hold(self):
        fired = []

        def refused(handle):
            return dict(v=1, state='blocked', reason='settings_revision_changed')

        service = self.interrupting(refused)
        real = Logged.handle

        def interrupt_once(this, raw, op=None):
            if json.loads(raw).get('op') == 'record' and not fired:
                fired.append(True)
                raise KeyboardInterrupt  # before the core's own `not_dispatched` reaches the ledger
            return real(this, raw, op)
        with mock.patch.object(Logged, 'handle', interrupt_once), self.assertRaises(KeyboardInterrupt):
            self.launch(service=service)
        self.assertEqual(self.holds()['a1']['state'], 'released')

    def test_an_interrupt_after_the_dispatch_committed_records_uncertain(self):
        def committed(handle):
            handle()  # the ledger committed the dispatch; its answer (and token) is lost
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.launch(service=self.interrupting(committed))
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')
        self.assertNotIn(('POST', '/api/session'), self.host.requests)


class WindowUnitTests(LauncherBase):
    """S5: every stored window carries the launcher's declared unit, the collector's own name for it."""

    def rate_limited(self):
        resets = self.now + 3000
        self.host.rate_limit = dict(status='allowed', rateLimitType='five_hour', resetsAt=resets,
                                    unifiedWindows=dict(five_hour=dict(utilization=0.25, resetsAt=resets,
                                                                       status='allowed')))

    def test_the_unit_is_declared_on_every_window(self):
        self.rate_limited()
        for window in launcher.windows(dict(observed_ms=self.now * 1000, windows=dict(
                five_hour=dict(utilization_fraction=0.5, resets_at_s=self.now + 10)))):
            self.assertEqual(window['unit'], launcher.WINDOW_UNIT)
        self.assertEqual((launcher.WINDOW_UNIT, collector.WINDOW_UNIT), ('utilization_thousandths',) * 2)

    def test_the_launcher_always_stores_the_unit_and_the_collector_admits_on_it(self):
        self.rate_limited()
        result = self.launch()
        self.assertEqual(result['state'], 'recorded', result)
        self.assertNotIn('window_unit_recorded', result)  # no fallback path is left
        stored = json.loads(collector.window_path(self.settings).read_text())['pools'][POOL]['5h']
        self.assertEqual(stored['unit'], 'utilization_thousandths')
        # The stored window is what the production source reads; the collector admits on it.
        local = collector.default_source(self.settings)
        found = collector.collect(str(self.checkout), dict(scope='s'), settings_path=self.settings,
                                  ledger_path=self.ledger_path,
                                  source=collector.StaticSource([route()], local.windows, ready,
                                                                billing=subscription_billing.double('local-windows')),
                                  **request_of([route()]))
        self.assertEqual([item['route'] for item in found['observations']['routes']], ['claude-haiku-low'])
        mismatched = collector.collect(str(self.checkout), dict(scope='s'), settings_path=self.settings,
                                       ledger_path=self.ledger_path, source=collector.StaticSource(
                                           [route(window_unit='percent')], local.windows, ready,
                                           billing=subscription_billing.double('local-windows')),
                                       **request_of([route()]))
        self.assertEqual(mismatched['observations']['excluded'],
                         [dict(route='claude-haiku-low', reason='window_unit_mismatch')])


class SignalTests(LauncherBase):
    """S2: SIGTERM and SIGHUP to the command become an exception, so its server is always stopped."""

    def check_signal(self, sig):
        code, answer, server = self.run_signal(sig, 'a1')
        self.assertEqual((code, answer['state'], answer['reason']), (1, 'blocked', 'interrupted'))
        server.stop.assert_called_once_with()
        self.assertEqual(self.holds()['a1']['state'], 'uncertain')
        # a second Ctrl-C, SIGTERM or SIGHUP cannot interrupt the cleanup
        self.assertEqual(self.during_stop, {item: signal.SIG_IGN for item in self.during_stop})

    def test_sigterm_stops_the_server_and_leaves_the_hold_uncertain(self):
        self.check_signal(signal.SIGTERM)

    def test_sighup_stops_the_server_and_leaves_the_hold_uncertain(self):
        self.check_signal(signal.SIGHUP)

    def test_a_first_ctrl_c_is_converted_and_a_second_is_ignored(self):
        self.check_signal(signal.SIGINT)

    def run_signal(self, sig, attempt):
        from scripts import opencode_launch
        stdout = []
        request = dict(v=1, op='launch', checkout=str(self.checkout), assignment=self.assignment(attempt),
                       job=routing_launch.unplanned_job('software-engineer', SCOPE, 'x'), prompt='x')
        server = mock.Mock(directory=str(self.checkout))
        server.request, server.rpc = self.host.request, self.host.rpc
        watched = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)
        server.stop.side_effect = lambda: setattr(self, 'during_stop', {item: signal.getsignal(item) for item in watched})
        self.host.prompt_hook = lambda: os.kill(os.getpid(), sig)
        before = {item: signal.getsignal(item) for item in watched}
        with mock.patch.object(opencode_launch.opencode_launcher, 'start_server', return_value=server), \
                mock.patch.object(opencode_launch.command, 'managed_service', return_value=self.service):
            code = opencode_launch.main([], stdin=json.dumps(request).encode(), write=stdout.append)
        self.assertEqual(before, {item: signal.getsignal(item) for item in before})  # handlers restored
        return code, json.loads(stdout[0]), server

    def test_a_keyboard_interrupt_stops_the_server(self):
        from scripts import opencode_launch
        server = mock.Mock(directory=str(self.checkout))
        request = dict(v=1, op='launch', checkout=str(self.checkout), assignment=self.assignment(),
                       job=routing_launch.unplanned_job('software-engineer', SCOPE, 'x'), prompt='x')
        stdout = []
        with mock.patch.object(opencode_launch.opencode_launcher, 'start_server', return_value=server), \
                mock.patch.object(opencode_launch.command, 'managed_service', return_value=self.service), \
                mock.patch.object(opencode_launch.opencode_launcher, 'launch', side_effect=KeyboardInterrupt):
            code = opencode_launch.main([], stdin=json.dumps(request).encode(), write=stdout.append)
        self.assertEqual((code, json.loads(stdout[0])['reason']), (1, 'interrupted'))
        server.stop.assert_called_once_with()


class ChoiceTests(unittest.TestCase):
    def test_choice_and_catalog_entries_are_route_identity(self):
        """C3-1b: the launcher's identities come from `routing_launch.route_identity('opencode', ...)`, with the same
        values its former identity()/harness() helpers gave."""
        self.assertFalse(hasattr(launcher, 'identity') or hasattr(launcher, 'harness'))
        for provider in (PROVIDER, 'openrouter', 'openai', 'anthropic', 'other'):
            for variant in (VARIANT, None):
                with self.subTest(provider=provider, variant=variant):
                    former = dict(provider=provider, model=MODEL, effort='none' if variant is None else variant,
                                  harness=adapter.HARNESS.get(provider, 'opencode provider %s' % provider))
                    self.assertEqual(routing_launch.route_identity('opencode', provider=provider, model=MODEL,
                                                                   effort=variant), former)
                    self.assertEqual(launcher.choice(provider, MODEL, variant), dict(former, funding='subscription'))
        models = [dict(providerID=PROVIDER, id=MODEL, variants=[VARIANT, dict(id='high')]),
                  dict(providerID='openrouter', id='vendor/model'), dict(providerID='', id='m')]
        host = mock.Mock(request=mock.Mock(return_value=(200, dict(data=models))))
        entries = launcher.catalog(host)  # the malformed entry is left out: it could never match an admission
        self.assertEqual(entries, [routing_launch.route_identity('opencode', provider=PROVIDER, model=MODEL, effort=e)
                                   for e in (VARIANT, 'high')]
                         + [routing_launch.route_identity('opencode', provider='openrouter', model='vendor/model')])

    def test_choice_uses_the_shims_harness_naming(self):
        self.assertEqual(launcher.choice(PROVIDER, MODEL, VARIANT),
                         dict(provider=PROVIDER, model=MODEL, effort=VARIANT, harness=HARNESS, funding='subscription'))
        self.assertEqual(launcher.choice('other', 'm', None)['harness'], 'opencode provider other')
        self.assertEqual(launcher.choice('other', 'm', None)['effort'], 'none')
        assignment = dict(attempt_id='a', task='t', role='software-engineer', work_kind='implementation', complexity_band='high', scope=SCOPE,
                          choice=launcher.choice(PROVIDER, MODEL, VARIANT))
        self.assertTrue(routing._valid_assignment(assignment))


if __name__ == '__main__':
    unittest.main()
