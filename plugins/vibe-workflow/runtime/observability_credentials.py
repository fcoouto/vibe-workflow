"""Explicit native OS credential storage; no discovery, fallback or global setup."""

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
import sys

from runtime import langfuse
from runtime.startup import StartupError, json_object


KEYRING_VERSION = "25.7.0"
MAX_BLOB = 2048
USERNAME = "project-keys"
SERVICE_PREFIX = "vibe-coding-workflow.langfuse."


class CredentialError(StartupError):
    """Fixed credential diagnostic without secret values or native error contents."""


def reference(destination, project_id):
    try:
        target = [1, langfuse.validate_destination(destination), langfuse.validate_project(project_id)]
    except langfuse.LangfuseError:
        raise CredentialError("invalid_credential_target") from None
    return SERVICE_PREFIX + hashlib.sha256(json.dumps(target, separators=(",", ":")).encode()).hexdigest()


def validate_pair(pair):
    try:
        return langfuse.validate_credentials(pair)
    except langfuse.LangfuseError:
        raise CredentialError("invalid_credential_pair") from None


def _backend():
    if sys.platform not in ("darwin", "win32"):
        raise CredentialError("unsupported_credential_store")
    # The pinned native constructors apply these environment overrides themselves.
    if "KEYCHAIN_PATH" in os.environ or any("KEYRING_PROPERTY_" in name for name in os.environ):
        raise CredentialError("credential_store_environment_override")
    try:
        installed = version("keyring")
    except PackageNotFoundError:
        raise CredentialError("credential_dependency_missing") from None
    except Exception:
        raise CredentialError("credential_dependency_unavailable") from None
    if installed != KEYRING_VERSION:
        raise CredentialError("credential_dependency_version_unsupported")
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        else:
            from keyring.backends.Windows import WinVaultKeyring
            backend = WinVaultKeyring()
        _ = backend.priority
    except Exception:
        raise CredentialError("credential_store_unavailable") from None
    if sys.platform == "darwin" and getattr(backend, "keychain", None) is not None:
        raise CredentialError("credential_store_environment_override")
    return backend


def check_available():
    """Check only the explicitly selected native backend, without reading an entry."""
    _backend()


def _windows_entry(backend, target):
    try:
        entry = backend._read_credential(target)
    except Exception:
        raise CredentialError("credential_store_unavailable") from None
    if entry is not None and entry.get("UserName") != USERNAME:
        raise CredentialError("credential_store_target_conflict")
    return entry


def load(destination, project_id):
    target = reference(destination, project_id)
    backend = _backend()
    try:
        if sys.platform == "win32":
            entry = _windows_entry(backend, target)
            raw = entry.value if entry is not None else None
        else:
            raw = backend.get_password(target, USERNAME)
    except CredentialError:
        raise
    except Exception:
        raise CredentialError("credential_store_unavailable") from None
    if raw is None:
        raise CredentialError("saved_credentials_unavailable")
    try:
        if not isinstance(raw, str):
            raise ValueError
        record = json_object(raw, limit=MAX_BLOB)
        if (set(record) != {"version", "public_key", "secret_key"}
                or type(record["version"]) is not int or record["version"] != 1):
            raise ValueError
        return validate_pair((record["public_key"], record["secret_key"]))
    except (StartupError, ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise CredentialError("saved_credentials_invalid") from None


def store(destination, project_id, pair, *, before_change):
    target = reference(destination, project_id)
    public, secret = validate_pair(pair)
    raw = json.dumps(dict(version=1, public_key=public, secret_key=secret), separators=(",", ":"))
    if len(raw.encode()) > MAX_BLOB:
        raise CredentialError("invalid_credential_pair")
    backend = _backend()
    if sys.platform == "win32":
        _windows_entry(backend, target)
    before_change()
    try:
        if sys.platform == "win32":
            # The pinned public setter migrates old secrets to a second target.
            backend._set_password(target, USERNAME, raw)
        else:
            backend.set_password(target, USERNAME, raw)
    except Exception:
        raise CredentialError("credential_store_write_unknown") from None


def forget(destination, project_id, *, before_change):
    target = reference(destination, project_id)
    backend = _backend()
    if sys.platform == "win32":
        _windows_entry(backend, target)
    before_change()
    try:
        if sys.platform == "win32":
            backend._delete_password(target)
        else:
            backend.delete_password(target, USERNAME)
    except Exception:
        raise CredentialError("credential_store_delete_unknown") from None
