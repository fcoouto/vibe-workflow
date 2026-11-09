"""Host-only Keychain storage for Usage Routing. No provider or network calls."""
from contextlib import ExitStack, contextmanager
import ctypes as ct
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from threading import Lock
import uuid

SERVICE = 'vibe-coding-workflow.openai'
STORAGE_KIND = 'macos_keychain'
MAX_SECRET = 512
READ_TIMEOUT = 5
IDENTITY = re.compile(r'[A-Za-z0-9_-]{1,128}')
REFERENCE = re.compile(r'vcw_[0-9a-f]{32}')


class CredentialError(ValueError):
    """Safe reason suitable for local status."""


def _valid(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def validate_binding(binding):
    if (type(binding) is not dict
            or set(binding) != {'organization', 'project', 'credential_ref', 'storage_kind'}
            or binding['storage_kind'] != STORAGE_KIND
            or not _valid(IDENTITY, binding['organization'])
            or not _valid(IDENTITY, binding['project'])
            or not _valid(REFERENCE, binding['credential_ref'])):
        raise CredentialError('invalid_credential_reference')


def _secret(value):
    if (type(value) is not bytes or not 0 < len(value) <= MAX_SECRET
            or any(byte < 33 or byte > 126 for byte in value)):
        raise CredentialError('invalid_credential_data')
    return value


def _account(binding):
    validate_binding(binding)
    return '/'.join(binding[key] for key in ('organization', 'project', 'credential_ref'))


def store_key(organization, project, secret: bytes) -> str:
    """Write one item after the host has approved the destination."""
    ref = 'vcw_' + uuid.uuid4().hex
    account = _account(dict(organization=organization, project=project,
                            credential_ref=ref, storage_kind=STORAGE_KIND))
    _secret(secret)
    try:
        _keychain().add(account, secret)
    except Exception:
        raise CredentialError('credential_store_failed') from None
    return ref


def resolve_key(binding: dict) -> bytes:
    """Resolve the scoped item via a bounded private pipe after trusted admission."""
    account = _account(binding)
    try:
        return _secret(_read_key(account))
    except Exception:
        raise CredentialError('credential_unavailable') from None


def _read_key(account):
    if sys.platform != 'darwin':
        raise CredentialError('credential_unavailable')
    reader, writer = os.pipe()
    try:
        try:
            subprocess.run(
                [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                 '--read', account, str(writer)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(writer,), timeout=READ_TIMEOUT, check=True,
            )
        finally:
            os.close(writer)
        return os.read(reader, MAX_SECRET + 1)
    finally:
        os.close(reader)


def _native_keychain():
    if sys.platform != 'darwin':
        raise CredentialError('credential_unavailable')
    return _SecurityKeychain()


_keychain = _native_keychain


class _SecurityKeychain:
    # Use in a dedicated credential writer or transport process. Security's
    # optional UI switch is process-wide and needs to cover the whole operation.
    _interaction_lock = Lock()

    def __init__(self):
        root = '/System/Library/Frameworks/'
        self.security = ct.CDLL(root + 'Security.framework/Security')
        self.cf = ct.CDLL(root + 'CoreFoundation.framework/CoreFoundation')
        ptr, uint, chars = ct.c_void_p, ct.c_uint32, ct.c_char_p
        pointer = ct.POINTER(ptr)
        signatures = (
            (self.security.SecKeychainCopyDefault, (pointer,)),
            (self.security.SecKeychainGetUserInteractionAllowed, (ct.POINTER(ct.c_ubyte),)),
            (self.security.SecKeychainSetUserInteractionAllowed, (ct.c_ubyte,)),
            (self.security.SecKeychainAddGenericPassword,
             (ptr, uint, chars, uint, chars, uint, chars, pointer)),
            (self.security.SecKeychainFindGenericPassword,
             (ptr, uint, chars, uint, chars, ct.POINTER(uint), pointer, pointer)),
            (self.security.SecKeychainItemFreeContent, (ptr, ptr)),
        )
        for function, arguments in signatures:
            function.restype = ct.c_int32
            function.argtypes = arguments
        self.cf.CFRelease.restype = None
        self.cf.CFRelease.argtypes = (ptr,)

    @contextmanager
    def _target(self):
        with self._interaction_lock, ExitStack() as stack:
            prior = ct.c_ubyte()
            if (self.security.SecKeychainGetUserInteractionAllowed(ct.byref(prior)) != 0
                    or self.security.SecKeychainSetUserInteractionAllowed(0) != 0):
                raise CredentialError('credential_unavailable')
            stack.callback(self.security.SecKeychainSetUserInteractionAllowed, prior.value)
            keychain = ct.c_void_p()
            status = self.security.SecKeychainCopyDefault(ct.byref(keychain))
            if keychain.value:
                stack.callback(self.cf.CFRelease, keychain)
            if status != 0 or not keychain.value:
                raise CredentialError('credential_unavailable')
            yield keychain

    def add(self, account, secret):
        service, account = SERVICE.encode('ascii'), account.encode('ascii')
        with self._target() as keychain:
            if self.security.SecKeychainAddGenericPassword(
                    keychain, len(service), service, len(account), account,
                    len(secret), secret, None) != 0:
                raise CredentialError('credential_store_failed')

    def read(self, account):
        service, account = SERVICE.encode('ascii'), account.encode('ascii')
        with self._target() as keychain, ExitStack() as stack:
            length, data = ct.c_uint32(), ct.c_void_p()
            status = self.security.SecKeychainFindGenericPassword(
                keychain, len(service), service, len(account), account,
                ct.byref(length), ct.byref(data), None)
            if data.value:
                stack.callback(self.security.SecKeychainItemFreeContent, None, data)
            if status != 0 or not data.value or not 0 < length.value <= MAX_SECRET:
                raise CredentialError('credential_unavailable')
            return ct.string_at(data, length.value)


def _write_from_pipe():
    try:
        if len(sys.argv) != 3 or sys.stdin.isatty():
            raise CredentialError('credential_store_failed')
        secret = sys.stdin.buffer.read(MAX_SECRET + 1)
        print(json.dumps({'credential_ref': store_key(*sys.argv[1:], secret)}))
        return 0
    except Exception:
        print('credential_store_failed', file=sys.stderr)
        return 1


def _read_to_pipe():
    try:
        if len(sys.argv) != 4:
            return 1
        organization, project, reference = sys.argv[2].split('/')
        account = _account(dict(organization=organization, project=project,
                                credential_ref=reference, storage_kind=STORAGE_KIND))
        descriptor = int(sys.argv[3])
        if descriptor < 3 or not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            return 1
        secret = _secret(_keychain().read(account))
        return 0 if os.write(descriptor, secret) == len(secret) else 1
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(_read_to_pipe() if sys.argv[1:2] == ['--read'] else _write_from_pipe())
