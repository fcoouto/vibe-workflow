"""Offline transport boundaries; no real connection or credential is used."""
import copy
import hashlib
import importlib.util
import json
from threading import Event
import time
import unittest
from unittest.mock import patch

from runtime.usage_budget import BudgetError


PACKET = dict(version=1, model='gpt-test', reasoning='high', text='Synthetic prompt',
              input_tokens=11, max_output_tokens=64)
KEYCHAIN_BINDING = dict(organization='org-synthetic', project='proj-synthetic',
                        credential_ref='vcw_' + '1' * 32, storage_kind='macos_keychain')
BINDING = KEYCHAIN_BINDING
SYNTHETIC_KEY = b'sk-proj-fixture-only'


class Response:
    status = 200

    def __init__(self, body, status=200):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.offset = 0

    def read(self, size):
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def getheader(self, name):
        return 'req_synthetic' if name.lower() == 'x-request-id' else None


class Connection:
    def __init__(self, response, error=None):
        self.response = response
        self.error = error
        self.requests = []
        self.closed = False

    def request(self, method, path, body, headers):
        self.requests.append((method, path, json.loads(body), headers))
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def completed():
    return dict(object='response', id='resp_synthetic', status='completed', model='gpt-test',
                service_tier='default', error=None, incomplete_details=None,
                output=[dict(type='reasoning', summary=[]),
                        dict(type='message', role='assistant', status='completed',
                             content=[dict(type='output_text', text='Untrusted proposal', annotations=[])])],
                usage=dict(input_tokens=11, output_tokens=17, total_tokens=28,
                           input_tokens_details=dict(cached_tokens=3, cache_write_tokens=2),
                           output_tokens_details=dict(reasoning_tokens=7)))


class OpenAITransportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('runtime.openai_api'), 'offline transport is missing')
        from runtime import openai_api
        self.api = openai_api

    def dispatch(self, phase):
        raw = json.dumps(PACKET, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        return dict(phase=phase, attempt_id='synthetic-attempt', packet_digest=hashlib.sha256(raw).hexdigest(),
                    deadline=time.time() + 60, committed=True)

    def call(self, phase, packet, binding, dispatch, factory, admit=lambda: None):
        return self.api._authenticated_call(packet, binding, dispatch, phase, admit=admit,
                                            connection_factory=factory, resolver=lambda _: SYNTHETIC_KEY)

    def invoke(self, phase, response, **kwargs):
        conn = Connection(response, kwargs.pop('error', None))
        factory_calls = []

        def factory(host, timeout):
            factory_calls.append((host, timeout))
            return conn

        result = self.call(phase, kwargs.pop('packet', PACKET), kwargs.pop('binding', BINDING),
                           kwargs.pop('dispatch', self.dispatch(phase)), factory)
        self.assertEqual(factory_calls[0][0], 'api.openai.com')
        self.assertTrue(0 < factory_calls[0][1] <= 300)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(len(conn.requests), 1)
        self.assertTrue(conn.closed)
        return result, conn.requests[0]

    def test_no_unauthenticated_entry_point_and_admission_is_required_before_any_access(self):
        self.assertFalse(hasattr(self.api, 'count') or hasattr(self.api, 'generate'))
        with patch.dict('os.environ', {}, clear=True), patch('os.getenv', side_effect=AssertionError('credential read')), \
             patch('runtime.openai_api.usage_credentials.resolve_key', side_effect=AssertionError('credential read')):
            for phase in ('count', 'generation'):
                with self.assertRaises(self.api.TransportError) as caught:
                    self.api._authenticated_call(PACKET, BINDING, self.dispatch(phase), phase, admit=None)
                self.assertEqual(str(caught.exception), 'live_admission_required')
                self.assertIsInstance(caught.exception, BudgetError)

    def test_count_and_generation_have_same_frozen_semantic_input(self):
        count, count_request = self.invoke('count', Response(dict(object='response.input_tokens', input_tokens=11)))
        generation, generation_request = self.invoke('generation', Response(completed()))
        self.assertEqual(count, dict(input_tokens=11, request_id='req_synthetic'))
        self.assertEqual(count_request[:3], ('POST', '/v1/responses/input_tokens',
                                          dict(model='gpt-test', input='Synthetic prompt', reasoning=dict(effort='high'))))
        self.assertEqual(generation_request[0:2], ('POST', '/v1/responses'))
        self.assertEqual(generation_request[2], dict(model='gpt-test', input='Synthetic prompt', reasoning=dict(effort='high'),
                                                   max_output_tokens=64, service_tier='default', store=False,
                                                   background=False, tools=[]))
        for request in (count_request, generation_request):
            self.assertEqual(request[3]['Authorization'], 'Bearer sk-proj-fixture-only')
            self.assertEqual(request[3]['OpenAI-Organization'], 'org-synthetic')
            self.assertEqual(request[3]['OpenAI-Project'], 'proj-synthetic')
            self.assertNotIn('sk-proj-fixture-only', json.dumps(request[:3]))
        self.assertEqual(generation, dict(input_tokens=11, output_tokens=17, cached_tokens=3,
                                          cache_write_tokens=2, request_id='req_synthetic',
                                          text='Untrusted proposal'))

    def test_invalid_admission_never_constructs_connection(self):
        variants = [dict(phase='generation'), dict(committed=False), dict(committed=1),
                    dict(packet_digest='0' * 64), dict(deadline=time.time() - 1),
                    dict(deadline=float('inf')), dict(attempt_id=''), dict(extra=True)]
        for update in variants:
            with self.subTest(update=update):
                dispatch = self.dispatch('count') | update
                with self.assertRaises(self.api.TransportError) as caught:
                    self.call('count', PACKET, BINDING, dispatch,
                              lambda *_: self.fail('constructed connection'))
                self.assertFalse(caught.exception.uncertain)

    def test_untrusted_packet_and_binding_rejected_before_connection(self):
        variants = [('packet', PACKET | dict(text='')), ('packet', PACKET | dict(text='x' * 65537)),
                    ('packet', PACKET | dict(input_tokens=True)), ('packet', PACKET | dict(max_output_tokens=0)),
                    ('packet', PACKET | dict(model='bad\r\nheader')), ('packet', PACKET | dict(extra=1)),
                    ('binding', BINDING | dict(organization='org\r\nInjected: yes')),
                    ('binding', BINDING | dict(project='')), ('binding', BINDING | dict(extra=1))]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                with self.assertRaises(self.api.TransportError) as caught:
                    self.call('count', value if key == 'packet' else PACKET,
                              value if key == 'binding' else BINDING, self.dispatch('count'),
                              lambda *_: self.fail('constructed connection'))
                self.assertFalse(caught.exception.uncertain)

    def test_count_rejects_nonintegers_duplicates_and_oversized_json(self):
        bodies = [b'{"object":"response.input_tokens","input_tokens":true}',
                  b'{"object":"response.input_tokens","input_tokens":-1}',
                  b'{"object":"response.input_tokens","input_tokens":11.0}',
                  b'{"object":"response.input_tokens","input_tokens":1,"input_tokens":11}',
                  b'{"object":"response.input_tokens","input_tokens":NaN}',
                  b'{"object":"wrong","input_tokens":11}', b'not json', b'[]', b'\xff', b' ' * 1048577]
        for body in bodies:
            with self.subTest(body=body[:80]):
                with self.assertRaises(self.api.TransportError) as caught:
                    self.invoke('count', Response(body))
                self.assertTrue(caught.exception.uncertain)
                self.assertNotIn('input_tokens', str(caught.exception))

    def test_generation_rejects_incomplete_usage_overruns_and_tools(self):
        variants = [dict(status='incomplete'), dict(model='unexpected'), dict(service_tier='flex'),
                    dict(error=dict(message='private provider error')), dict(incomplete_details={}),
                    dict(usage=None), dict(output=[]), dict(output=[dict(type='function_call', arguments='evil')])]
        for update in variants:
            with self.subTest(update=update):
                with self.assertRaises(self.api.TransportError) as caught:
                    self.invoke('generation', Response(completed() | update))
                self.assertTrue(caught.exception.uncertain)
                self.assertNotIn('private', str(caught.exception))
        for key, value in [('input_tokens', True), ('output_tokens', 65), ('total_tokens', 0),
                           ('input_tokens_details', dict(cached_tokens=12, cache_write_tokens=0)),
                           ('output_tokens_details', dict(reasoning_tokens=18))]:
            body = completed()
            body['usage'][key] = value
            with self.subTest(key=key), self.assertRaises(self.api.TransportError):
                self.invoke('generation', Response(body))
        body = completed()
        body['output'][1]['content'][0]['text'] = 'x' * 65537
        with self.assertRaises(self.api.TransportError):
            self.invoke('generation', Response(body))

    def test_incomplete_generation_preserves_only_safe_failure_metadata(self):
        body = completed() | dict(status='incomplete', incomplete_details=dict(
            reason='max_output_tokens', detail='synthetic provider diagnostic'))
        with self.assertRaisesRegex(self.api.TransportError, '^incomplete_response$') as caught:
            self.invoke('generation', Response(body))
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(caught.exception.incomplete, dict(
            reason='max_output_tokens', response_id='resp_synthetic', request_id='req_synthetic',
            usage=dict(input_tokens=11, output_tokens=17, cached_tokens=3, cache_write_tokens=2,
                       reasoning_tokens=7)))

    def test_incomplete_generation_keeps_safe_reason_without_unusable_usage_or_request_id(self):
        for reason, safe_reason in [('content_filter', 'content_filter'), ('secret provider reason', 'unknown')]:
            for usage in (None, {'input_tokens': True},
                          completed()['usage'] | dict(input_tokens=12, total_tokens=29),
                          completed()['usage'] | dict(output_tokens=65, total_tokens=76)):
                with self.subTest(reason=reason, usage=usage):
                    response = Response(completed() | dict(status='incomplete',
                        incomplete_details=dict(reason=reason), usage=usage))
                    response.getheader = lambda _: 'secret provider header'
                    with self.assertRaisesRegex(self.api.TransportError, '^incomplete_response$') as caught:
                        self.invoke('generation', response)
                    self.assertTrue(caught.exception.uncertain)
                    self.assertEqual(caught.exception.incomplete, dict(reason=safe_reason,
                        response_id='resp_synthetic', request_id=None, usage=None))

    def test_incomplete_generation_rejects_metadata_from_wrong_envelope(self):
        incomplete = completed() | dict(status='incomplete', incomplete_details=dict(reason='max_output_tokens'))
        for change in (dict(object='other'), dict(id='private provider diagnostic'), dict(model='other'),
                       dict(service_tier='flex'), dict(error={}), dict(background=True), dict(tools=[{}])):
            with self.subTest(change=change), self.assertRaisesRegex(self.api.TransportError, '^incomplete_response$') as caught:
                self.invoke('generation', Response(incomplete | change))
            self.assertTrue(caught.exception.uncertain)
            self.assertIsNone(caught.exception.incomplete)

    def test_failures_are_sanitized_closed_and_never_retried(self):
        for status in [301, 302, 400, 401, 403, 402, 429, 500, 503]:
            conn = Connection(Response(dict(error=dict(message='secret synthetic provider text')), status))
            with self.subTest(status=status), self.assertRaises(self.api.TransportError) as caught:
                self.call('count', PACKET, BINDING, self.dispatch('count'), lambda *_: conn)
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(len(conn.requests), 1)
            self.assertTrue(conn.closed)
        for error in [TimeoutError('secret timeout'), OSError('secret disconnect')]:
            conn = Connection(Response({}), error)
            with self.subTest(error=type(error)), self.assertRaises(self.api.TransportError) as caught:
                self.call('count', PACKET, BINDING, self.dispatch('count'), lambda *_: conn)
            self.assertTrue(caught.exception.uncertain)
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(len(conn.requests), 1)
            self.assertTrue(conn.closed)

    def test_factory_failure_before_send_is_certain(self):
        def fail(*_):
            raise OSError('secret synthetic failure')
        with self.assertRaises(self.api.TransportError) as caught:
            self.call('count', PACKET, BINDING, self.dispatch('count'), fail)
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(str(caught.exception), 'transport_failure')

    def test_injected_budget_error_cannot_leak_provider_or_credential_text(self):
        def fail(*_):
            raise BudgetError('synthetic-private-provider-text')
        with self.assertRaises(self.api.TransportError) as caught:
            self.call('count', PACKET, BINDING, self.dispatch('count'), fail)
        self.assertEqual(str(caught.exception), 'transport_failure')

    def test_packet_digest_uses_documented_canonical_encoding(self):
        expected = hashlib.sha256(b'{"a":"\\u00e9","z":1}').hexdigest()
        self.assertEqual(self.api.packet_digest(dict(z=1, a='é')), expected)
        with self.assertRaises(self.api.TransportError):
            self.api.packet_digest(dict(value=float('nan')))

    def test_elapsed_deadline_blocks_send_and_marks_late_response_uncertain(self):
        dispatch = self.dispatch('count') | dict(deadline=200)
        conn = Connection(Response(dict(object='response.input_tokens', input_tokens=11)))
        with patch('runtime.openai_api.time.time', side_effect=[100, 100, 100, 201]):
            with self.assertRaises(self.api.TransportError) as caught:
                self.call('count', PACKET, BINDING, dispatch, lambda *_: conn)
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(conn.requests, [])
        self.assertTrue(conn.closed)
        conn = Connection(Response(dict(object='response.input_tokens', input_tokens=11)))
        with patch('runtime.openai_api.time.time', side_effect=[100, 100, 100, 100, 100, 201]):
            with self.assertRaises(self.api.TransportError) as caught:
                self.call('count', PACKET, BINDING, dispatch, lambda *_: conn)
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(conn.requests), 1)
        self.assertTrue(conn.closed)

    def test_packet_is_frozen_before_injected_factory_runs(self):
        packet = copy.deepcopy(PACKET)
        conn = Connection(Response(dict(object='response.input_tokens', input_tokens=11)))

        def mutate(*_):
            packet['text'] = 'changed after admission'
            return conn

        self.call('count', packet, BINDING, self.dispatch('count'), mutate)
        self.assertEqual(conn.requests[0][2]['input'], 'Synthetic prompt')

    def test_authenticated_admission_follows_credential_and_connection_preparation(self):
        events = []
        conn = Connection(Response(completed()))

        def resolve(binding):
            self.assertEqual(binding, KEYCHAIN_BINDING)
            events.append('credential')
            return SYNTHETIC_KEY

        def factory(host, timeout):
            self.assertEqual(host, 'api.openai.com')
            self.assertTrue(0 < timeout <= 300)
            events.append('connection')
            return conn

        def admit():
            self.assertEqual(conn.requests, [])
            events.append('admission')

        result = self.api._authenticated_call(PACKET, KEYCHAIN_BINDING, self.dispatch('generation'), 'generation',
                                              admit=admit, resolver=resolve, connection_factory=factory)
        self.assertEqual(events, ['credential', 'connection', 'admission'])
        self.assertEqual(len(conn.requests), 1)
        method, path, body, headers = conn.requests[0]
        self.assertEqual((method, path), ('POST', '/v1/responses'))
        self.assertEqual(headers['Authorization'], 'Bearer sk-proj-fixture-only')
        self.assertEqual(headers['OpenAI-Organization'], 'org-synthetic')
        self.assertEqual(headers['OpenAI-Project'], 'proj-synthetic')
        self.assertNotIn('sk-proj-fixture-only', json.dumps([body, result]))
        self.assertTrue(conn.closed)

    def test_authenticated_rejections_before_post_never_send(self):
        def deny():
            raise RuntimeError('private host diagnostic')

        for key, value, expected in [
            ('binding', BINDING | dict(credential_ref='env:SYNTHETIC_KEY'), 'invalid_credential_reference'),
            ('resolver', lambda _: b'sk-fake\r\nHeader: forged', 'credential_unavailable'),
            ('resolver', lambda _: deny(), 'credential_unavailable'),
            ('admit', None, 'live_admission_required'),
            ('admit', deny, 'dispatch_admission_denied'),
            ('admit', lambda: False, 'dispatch_admission_denied'),
        ]:
            with self.subTest(key=key, reason=expected):
                conn = Connection(Response(completed()))
                options = dict(binding=KEYCHAIN_BINDING, resolver=lambda _: SYNTHETIC_KEY, admit=lambda: None,
                               connection_factory=lambda *_: conn) | {key: value}
                with self.assertRaises(self.api.TransportError) as caught:
                    self.api._authenticated_call(PACKET, options.pop('binding'), self.dispatch('generation'),
                                                 'generation', **options)
                self.assertEqual(str(caught.exception), expected)
                self.assertFalse(caught.exception.uncertain)
                self.assertEqual(conn.requests, [])

    def test_https_adapter_preconnects_verified_tls_and_rejects_redirect_once(self):
        conn = Connection(Response({}, status=302))
        events = []

        class Socket:
            def shutdown(self, *_):
                pass

        def connect():
            events.append('tls')
            conn.sock = Socket()

        conn.connect = connect

        def https_connection(host, *, port, timeout, context):
            self.assertEqual((host, port), ('api.openai.com', 443))
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode.name, 'CERT_REQUIRED')
            self.assertIsNone(context.keylog_filename)
            return conn

        with patch('http.client.HTTPSConnection', side_effect=https_connection), \
             patch('runtime.openai_api.usage_credentials.resolve_key', return_value=SYNTHETIC_KEY):
            with self.assertRaises(self.api.TransportError) as caught:
                self.api._authenticated_call(PACKET, KEYCHAIN_BINDING, self.dispatch('count'), 'count',
                                             admit=lambda: events.append('admission'))
        self.assertEqual(events, ['tls', 'admission'])
        self.assertEqual(len(conn.requests), 1)
        self.assertEqual(conn.requests[0][1], '/v1/responses/input_tokens')
        self.assertEqual(str(caught.exception), 'provider_request_rejected')
        self.assertTrue(caught.exception.uncertain)
        self.assertTrue(conn.closed)

    def test_https_preparation_timeout_abandons_and_closes_late_connection(self):
        entered, release, closed = Event(), Event(), Event()
        conn = Connection(Response({}))

        def connect():
            entered.set()
            release.wait(2)

        conn.connect = connect
        conn.close = closed.set
        dispatch = self.dispatch('count') | dict(deadline=time.time() + 0.2)
        try:
            with patch('ssl.SSLContext'), patch('http.client.HTTPSConnection', return_value=conn), \
                 patch('runtime.openai_api.usage_credentials.resolve_key', return_value=SYNTHETIC_KEY):
                started = time.monotonic()
                with self.assertRaises(self.api.TransportError) as caught:
                    self.api._authenticated_call(PACKET, KEYCHAIN_BINDING, dispatch, 'count',
                                                 admit=lambda: self.fail('late connection admitted'))
                self.assertLess(time.monotonic() - started, 1)
                self.assertEqual(str(caught.exception), 'transport_timeout')
                self.assertFalse(caught.exception.uncertain)
                self.assertTrue(entered.is_set())
                self.assertFalse(closed.is_set())
        finally:
            release.set()
        self.assertTrue(closed.wait(1))
        self.assertEqual(conn.requests, [])


if __name__ == '__main__':
    unittest.main()
