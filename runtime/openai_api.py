"""Bounded Responses transport.

Every request is authenticated and admitted. Dispatch is internal. Its coordinator owns durable admission,
attempt uniqueness, ledger gating and recovery. A digest or committed flag
alone supplies no authority.
"""
from contextlib import suppress
import hashlib
import http.client
import json
import math
import re
import socket
import ssl
from threading import Event, Lock, Thread, Timer
import time

from runtime import usage_credentials
from runtime.usage_budget import BudgetError, fields, integer, name, require

MAX_INPUT = 65536
MAX_REQUEST = 524288
MAX_RESPONSE = 1048576
MAX_TEXT = 65536
MAX_WAIT = 300
HOST = 'api.openai.com'
SAFE_REASONS = frozenset('invalid_fields invalid_version invalid_identity invalid_integer invalid_text '
                         'invalid_output_limit request_too_large uncommitted_dispatch packet_digest_mismatch '
                         'invalid_deadline deadline_exceeded malformed_response response_too_large '
                         'incomplete_response response_profile_mismatch unsupported_response missing_usage '
                         'invalid_usage output_limit_exceeded invalid_output output_too_large '
                         'provider_request_rejected invalid_credential_reference credential_unavailable '
                         'dispatch_admission_denied'.split())


class TransportError(BudgetError):
    """Sanitized failure; uncertain means a request may have reached the server."""

    def __init__(self, reason, *, uncertain=False, incomplete=None):
        super().__init__(reason)
        self.uncertain = uncertain
        self.incomplete = incomplete


class _HTTPSConnection:
    def __init__(self):
        self.connection = None
        self.timer = None
        self.response = None

    def prepare(self, deadline):
        ready, lock = Event(), Lock()
        abandoned = False
        failure = None
        timeout = _remaining(deadline)

        def connect():
            nonlocal failure
            connection = None
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_default_certs()
                connection = http.client.HTTPSConnection(HOST, port=443, timeout=timeout, context=context)
                connection.auto_open = False
                connection.connect()
            except TimeoutError:
                failure = TimeoutError
            except Exception:
                failure = OSError
            with lock:
                if not abandoned:
                    self.connection = connection
                    ready.set()
                    return
            if connection is not None:
                with suppress(Exception):
                    connection.close()

        # OS DNS can outlive the caller's wait. The daemon owns any late socket
        # and never receives a credential, payload or admission callback.
        try:
            Thread(target=connect, daemon=True).start()
            if not ready.wait(_remaining(deadline)):
                raise TimeoutError()
        finally:
            with lock:
                abandoned = True
        if failure is not None:
            raise failure()
        sock = self.connection.sock

        def stop_io():
            # Socket inactivity timeouts alone permit slow response headers.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        self.timer = Timer(_remaining(deadline), stop_io)
        self.timer.daemon = True
        self.timer.start()

    def request(self, method, path, body, headers):
        self.connection.request(method, path, body=body, headers=headers)

    def getresponse(self):
        self.response = self.connection.getresponse()
        return self.response

    def close(self):
        if self.timer is not None:
            self.timer.cancel()
        try:
            if self.response is not None:
                self.response.close()
        finally:
            if self.connection is not None:
                self.connection.close()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def packet_digest(packet):
    """SHA-256 of canonical JSON (sorted keys, compact, ASCII-escaped UTF-8).

    This binds bytes only; it is neither an admission receipt nor authentication.
    """
    try:
        raw = _canonical(packet)
        require(len(raw) <= MAX_REQUEST, 'request_too_large')
        return hashlib.sha256(raw).hexdigest()
    except (BudgetError, TypeError, ValueError, OverflowError, RecursionError):
        raise TransportError('invalid_packet') from None


def _identity(value):
    name(value)
    require(value.isascii(), 'invalid_identity')
    return value


def _remaining(deadline):
    remaining = deadline - time.time()
    require(remaining > 0, 'deadline_exceeded')
    return min(remaining, MAX_WAIT)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'malformed_response')
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError('invalid_json_constant')


def _read(response, deadline):
    chunks, size = [], 0
    read = getattr(response, 'read1', response.read)
    while True:
        _remaining(deadline)
        chunk = read(min(65536, MAX_RESPONSE + 1 - size))
        require(type(chunk) is bytes, 'malformed_response')
        size += len(chunk)
        require(size <= MAX_RESPONSE, 'response_too_large')
        if not chunk:
            break
        chunks.append(chunk)
    _remaining(deadline)
    require(getattr(response, 'length', None) in (None, 0), 'malformed_response')
    body = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=_unique,
                      parse_constant=_invalid_constant)
    require(type(body) is dict, 'malformed_response')
    return body


def _usage(body, packet):
    usage = body.get('usage')
    require(type(usage) is dict, 'missing_usage')
    incoming, outgoing = integer(usage.get('input_tokens')), integer(usage.get('output_tokens'))
    require(integer(usage.get('total_tokens')) == incoming + outgoing, 'invalid_usage')
    require(outgoing <= packet['max_output_tokens'], 'output_limit_exceeded')
    details = usage.get('input_tokens_details')
    require(type(details) is dict, 'missing_usage')
    cached, written = integer(details.get('cached_tokens')), integer(details.get('cache_write_tokens', 0))
    require(cached + written <= incoming, 'invalid_usage')
    details = usage.get('output_tokens_details')
    require(type(details) is dict, 'invalid_usage')
    reasoning = integer(details.get('reasoning_tokens'))
    require(reasoning <= outgoing, 'invalid_usage')
    # Responses output_tokens already includes reasoning_tokens.
    return dict(input_tokens=incoming, output_tokens=outgoing, cached_tokens=cached,
                cache_write_tokens=written, reasoning_tokens=reasoning)


def _incomplete(body, packet, response):
    response_id = body.get('id')
    if (body.get('object') != 'response' or body.get('error') is not None
            or body.get('model') != packet['model'] or body.get('service_tier') != 'default'
            or body.get('background', False) is not False or body.get('tools', []) != []
            or type(response_id) is not str or not re.fullmatch(r'resp_[A-Za-z0-9_-]{1,250}', response_id)):
        return None
    details = body.get('incomplete_details')
    reason = details.get('reason') if type(details) is dict else None
    if reason not in ('max_output_tokens', 'content_filter'):
        reason = 'unknown'
    try:
        usage = _usage(body, packet)
        require(usage['input_tokens'] <= packet['input_tokens'], 'invalid_usage')
    except BudgetError:
        usage = None
    try:
        request_id = response.getheader('x-request-id')
    except Exception:
        request_id = None
    if type(request_id) is not str or not re.fullmatch(r'req_[A-Za-z0-9_-]{1,251}', request_id):
        request_id = None
    return dict(reason=reason, response_id=response_id, request_id=request_id, usage=usage)


def _generation(body, packet):
    require(body.get('object') == 'response' and body.get('status') == 'completed'
            and body.get('error') is None and body.get('incomplete_details') is None,
            'incomplete_response')
    _identity(body.get('id'))
    require(body.get('model') == packet['model'] and body.get('service_tier') == 'default',
            'response_profile_mismatch')
    require(body.get('background', False) is False and body.get('tools', []) == [], 'unsupported_response')
    usage = _usage(body, packet)
    usage.pop('reasoning_tokens')
    output = body.get('output')
    require(type(output) is list and 0 < len(output) <= 256, 'invalid_output')
    parts, size = [], 0
    for item in output:
        require(type(item) is dict, 'invalid_output')
        if item.get('type') == 'reasoning':
            continue
        require(item.get('type') == 'message' and item.get('role') == 'assistant'
                and item.get('status') == 'completed', 'invalid_output')
        content = item.get('content')
        require(type(content) is list and len(content) <= 256, 'invalid_output')
        for part in content:
            require(type(part) is dict and part.get('type') == 'output_text'
                    and type(part.get('text')) is str, 'invalid_output')
            size += len(part['text'].encode('utf-8'))
            require(size <= MAX_TEXT, 'output_too_large')
            parts.append(part['text'])
    require(size > 0, 'invalid_output')
    return dict(usage, text=''.join(parts))


def _call(packet, binding, dispatch, phase, connection_factory, *, admit, resolver):
    connection, sent = None, False
    incomplete = None
    try:
        fields(packet, 'version model reasoning text input_tokens max_output_tokens')
        require(type(packet['version']) is int and packet['version'] == 1, 'invalid_version')
        _identity(packet['model']); _identity(packet['reasoning'])
        require(type(packet['text']) is str and 0 < len(packet['text'].encode('utf-8')) <= MAX_INPUT, 'invalid_text')
        integer(packet['input_tokens'])
        require(integer(packet['max_output_tokens']) > 0, 'invalid_output_limit')
        raw = _canonical(packet)
        require(len(raw) <= MAX_REQUEST, 'request_too_large')
        packet = json.loads(raw)
        try:
            usage_credentials.validate_binding(binding)
        except usage_credentials.CredentialError:
            raise BudgetError('invalid_credential_reference') from None
        fields(dispatch, 'phase attempt_id packet_digest deadline committed')
        require(phase in ('count', 'generation') and dispatch['phase'] == phase
                and dispatch['committed'] is True, 'uncommitted_dispatch')
        _identity(dispatch['attempt_id'])
        require(dispatch['packet_digest'] == hashlib.sha256(raw).hexdigest(), 'packet_digest_mismatch')
        deadline = dispatch['deadline']
        require(type(deadline) in (int, float) and math.isfinite(deadline), 'invalid_deadline')
        deadline = min(deadline, time.time() + MAX_WAIT)
        timeout = _remaining(deadline)
        payload = dict(model=packet['model'], input=packet['text'], reasoning=dict(effort=packet['reasoning']))
        if phase == 'generation':
            payload.update(max_output_tokens=packet['max_output_tokens'], service_tier='default',
                           store=False, background=False, tools=[])
        body = _canonical(payload)
        require(len(body) <= MAX_REQUEST, 'request_too_large')
        headers = {'Content-Type': 'application/json',
                   'OpenAI-Organization': binding['organization'], 'OpenAI-Project': binding['project'],
                   'X-Client-Request-Id': dispatch['attempt_id']}
        try:
            secret = resolver(binding.copy())
            if (type(secret) is not bytes or not 0 < len(secret) <= usage_credentials.MAX_SECRET
                    or any(byte < 33 or byte > 126 for byte in secret)):
                raise ValueError()
            headers['Authorization'] = 'Bearer ' + secret.decode('ascii')
        except Exception:
            raise BudgetError('credential_unavailable') from None
        timeout = _remaining(deadline)
        if connection_factory is None:
            connection = _HTTPSConnection()
            connection.prepare(deadline)
        else:
            connection = connection_factory(HOST, timeout)
        _remaining(deadline)
        path = '/v1/responses/input_tokens' if phase == 'count' else '/v1/responses'
        try:
            if admit() is False:
                raise ValueError()
        except Exception:
            raise BudgetError('dispatch_admission_denied') from None
        _remaining(deadline)
        sent = True
        connection.request('POST', path, body, headers)
        response = connection.getresponse()
        require(type(response.status) is int and response.status == 200, 'provider_request_rejected')
        result = _read(response, deadline)
        if phase == 'count':
            require(result.get('object') == 'response.input_tokens', 'malformed_response')
            result = dict(input_tokens=integer(result.get('input_tokens')))
        else:
            if result.get('status') == 'incomplete':
                incomplete = _incomplete(result, packet, response)
            result = _generation(result, packet)
        request_id = response.getheader('x-request-id') if hasattr(response, 'getheader') else None
        if request_id is not None:
            _identity(request_id)
        return dict(result, request_id=request_id)
    except BudgetError as error:
        reason = str(error)
        raise TransportError(reason if reason in SAFE_REASONS else 'transport_failure', uncertain=sent,
                             incomplete=incomplete) from None
    except TimeoutError:
        raise TransportError('transport_timeout', uncertain=sent) from None
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise TransportError('malformed_response' if sent else 'invalid_request', uncertain=sent) from None
    except Exception:
        raise TransportError('transport_failure', uncertain=sent) from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _authenticated_call(packet, binding, dispatch, phase, *, admit, connection_factory=None, resolver=None):
    """Internal single POST. ``admit()`` consumes the coordinator's one-shot grant.

    The callback must refuse by raising or returning False. Tests inject both
    dependencies together so a fake connection never reads the actual Keychain.
    """
    if not callable(admit):
        raise TransportError('live_admission_required')
    if (connection_factory is None) != (resolver is None):
        raise TransportError('invalid_transport_dependencies')
    return _call(packet, binding, dispatch, phase, connection_factory, admit=admit,
                 resolver=usage_credentials.resolve_key if resolver is None else resolver)
