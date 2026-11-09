"""In-process setup protocol for a trusted Codex host. No account or routing calls."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
from threading import Lock
import time
from types import MappingProxyType

from runtime.usage_credentials import IDENTITY, SERVICE, STORAGE_KIND, CredentialError, validate_binding

_HELPER = Path(__file__).resolve().parents[1] / 'scripts' / 'usage_key_setup.mjs'
_KEY_WAIT = 15
_RECEIPT_WAIT = 20
_EXCHANGE_WAIT = 120  # Keep the connector window within the helper's lifetime.
_PIPE_LIMIT = 4096
_RSA_BYTES = re.compile(r'[A-Za-z0-9_-]{682}[AEIMQUYcgkosw048]')
_SELECTION_FIELDS = {'name', 'organization_id', 'project_id'}


class SetupError(CredentialError):
    """A fixed safe setup reason. An unverified creation must be reconciled before retry."""


def _rsa_bytes(value):
    return type(value) is str and _RSA_BYTES.fullmatch(value) is not None


def _selection(value):
    if type(value) is not dict or set(value) not in (_SELECTION_FIELDS, _SELECTION_FIELDS | {'expires_in_seconds'}):
        raise SetupError('invalid_key_selection')
    key_name = value['name']
    if (type(key_name) is not str or not key_name.strip() or len(key_name) > 128 or not key_name.isprintable()
            or any(type(value[key]) is not str or not IDENTITY.fullmatch(value[key])
                   for key in ('organization_id', 'project_id'))):
        raise SetupError('invalid_key_selection')
    if 'expires_in_seconds' in value:
        expiry = value['expires_in_seconds']
        if type(expiry) is not int or not 0 < expiry < 2**53:
            raise SetupError('invalid_key_selection')
    return value.copy()


class _PendingSetup:
    def __init__(self, selection, helper):
        self._selection = selection
        self._helper = helper
        self._lock = Lock()
        self._state = 'prepared'
        jwk = helper.public_key()
        if (type(jwk) is not dict or set(jwk) != {'kty', 'n', 'e'} or jwk['kty'] != 'RSA'
                or jwk['e'] != 'AQAB' or not _rsa_bytes(jwk['n']) or jwk['n'][0] not in 'ghijklmnopqrstuvwxyz0123456789_-'):
            raise SetupError('credential_setup_unavailable')
        self._jwk = jwk.copy()

    def creation_arguments(self):
        """Consume the single creation attempt and return public connector arguments."""
        with self._lock:
            if self._state != 'prepared':
                raise SetupError('credential_setup_already_used')
            if not self._helper.ready():
                raise SetupError('credential_setup_unavailable')
            self._state = 'creating'
            return dict(self._selection, recipient_public_key_jwk=self._jwk.copy())

    def finish(self, result):
        """Consume the connector response once. Pass None if the call result is unknown."""
        with self._lock:
            if self._state != 'creating':
                raise SetupError('credential_setup_not_pending')
            self._state = 'finished'
        if type(result) is not dict or result.get('isError', False) is not False:
            raise SetupError('credential_creation_unverified')
        response = result.get('structuredContent')
        if type(response) is not dict:
            raise SetupError('credential_creation_unverified')
        for key in response.keys() & (_SELECTION_FIELDS | {'expires_in_seconds'}):
            selected = self._selection.get(key)
            if type(response[key]) is not type(selected) or response[key] != selected:
                raise SetupError('credential_creation_unverified')
        encrypted = response.get('encrypted_api_key')
        if (type(encrypted) is not dict or type(encrypted.get('version')) is not int
                or encrypted['version'] != 1 or not _rsa_bytes(encrypted.get('ciphertext'))):
            raise SetupError('credential_creation_unverified')
        try:
            receipt = self._helper.store(encrypted['ciphertext'])
            if type(receipt) is not dict or set(receipt) != {'credential_ref'}:
                raise SetupError('credential_storage_recovery_required')
            binding = dict(organization=self._selection['organization_id'], project=self._selection['project_id'],
                           storage_kind=STORAGE_KIND, credential_ref=receipt['credential_ref'])
            validate_binding(binding)
        except Exception:
            raise SetupError('credential_storage_recovery_required') from None
        return dict(selection=self._selection.copy(), mode='off', binding=binding)

    def _close(self):
        with self._lock:
            self._state = 'closed'


@contextmanager
def prepare_setup(selection, approve_destination, *, node_executable=None, start_helper=None):
    """Bind trusted picker data and explicit approval to a one-shot helper exchange.

    The host owns the picker, displayed approval, qualified Node executable and
    connector invocation. The result contains metadata for separate OFF configuration.
    """
    selection = _selection(selection)
    proposal = MappingProxyType(dict(selection, expires_in_seconds=selection.get('expires_in_seconds'),
                                     storage_kind=STORAGE_KIND, keychain='default', service=SERVICE))
    if approve_destination(proposal) is not True:
        raise SetupError('credential_setup_not_approved')
    with ExitStack() as stack:
        try:
            targets = (selection['organization_id'], selection['project_id'])
            start = (start_helper(*targets) if start_helper is not None
                     else _start_helper(*targets, node_executable))
            helper = stack.enter_context(start)
            pending = _PendingSetup(selection, helper)
        except Exception:
            raise SetupError('credential_setup_unavailable') from None
        try:
            yield pending
        finally:
            pending._close()


class _Helper:
    def __init__(self, process, deadline):
        self.process = process
        self.deadline = deadline

    def ready(self):
        return time.monotonic() < self.deadline and not select.select([self.process.stdout], [], [], 0)[0]

    def public_key(self):
        deadline = min(self.deadline, time.monotonic() + _KEY_WAIT)
        public = self._read_line(deadline)
        if type(public) is dict and set(public) == {'recipient_public_key_jwk'}:
            return public['recipient_public_key_jwk']
        raise SetupError('credential_setup_unavailable')

    def _read_line(self, deadline, *, until_eof=False):
        stream = self.process.stdout
        data = bytearray()
        while len(data) < _PIPE_LIMIT:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
                break
            chunk = os.read(stream.fileno(), _PIPE_LIMIT - len(data))
            if not chunk:
                if until_eof and data.count(b'\n') == 1 and data.endswith(b'\n'):
                    return json.loads(data)
                break
            data.extend(chunk)
            if not until_eof and b'\n' in data:
                if data.count(b'\n') != 1 or not data.endswith(b'\n'):
                    break
                return json.loads(data)
        raise SetupError('credential_setup_unavailable')

    def store(self, ciphertext):
        if not self.ready():
            raise SetupError('credential_storage_recovery_required')
        payload = json.dumps(dict(version=1, ciphertext=ciphertext)).encode('ascii') + b'\n'
        if self.process.stdin.write(payload) != len(payload):
            raise SetupError('credential_storage_recovery_required')
        self.process.stdin.close()
        return self._read_line(min(self.deadline, time.monotonic() + _RECEIPT_WAIT), until_eof=True)


@contextmanager
def _start_helper(organization, project, node_executable):
    if sys.platform != 'darwin' or node_executable is None or not Path(sys.executable).is_absolute():
        raise SetupError('credential_setup_unavailable')
    node = Path(node_executable)
    if not node.is_absolute():
        raise SetupError('credential_setup_unavailable')
    node = node.resolve(strict=True)
    if not node.is_file() or not os.access(node, os.X_OK):
        raise SetupError('credential_setup_unavailable')
    deadline = time.monotonic() + _EXCHANGE_WAIT
    process = subprocess.Popen([str(node), str(_HELPER), organization, project, sys.executable],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True, env={}, bufsize=0)
    try:
        yield _Helper(process, deadline)
    finally:
        # Keep the leader unreaped until the group is stopped, even after its exit.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # macOS can report EPERM for a group containing only its exited leader.
            pass
        process.stdin.close()
        process.stdout.close()
        try:
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            raise SetupError('credential_setup_cleanup_unverified') from None
