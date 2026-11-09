"""Checkout-scoped connection consent, separate from task evidence and collection."""

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat
import time
from types import SimpleNamespace
import uuid

from runtime import changes, langfuse, local_ownership, onboarding, observability_credentials as credentials, startup, state


PACKAGE = Path(__file__).resolve().parents[1]
MAX_CONFIG = 8 * 1024
MAX_REVISION = 2 ** 53 - 1
FIELDS = {"version", "revision", "binding", "destination", "langfuse_project_id",
          "desired_opt_in", "connection_verified_at"}


class SettingsError(startup.StartupError):
    """Fixed local diagnostic; configuration and private paths are never echoed."""


def _identity(info):
    return dict(device=info.st_dev, inode=info.st_ino)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private(info, *, directory=False):
    if (info.st_uid != os.getuid() or info.st_mode & 0o077
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory and info.st_nlink != 1):
        raise SettingsError("unsafe_observability_storage")


def _authority(scope):
    if type(scope) is local_ownership.LocalOwnership:
        return dict(scope_id=scope.scope_id, authority_directory=local_ownership.settings_directory(scope))
    if (type(scope) is not startup.AcceptedScope or scope.client != "codex"
            or scope.runtime is None or scope.runtime.schema_version != 3):
        raise SettingsError("unsupported_observability_scope")
    current = startup._entry_scope(startup.acquire_project_entry(
        SimpleNamespace(client="codex", cwd=scope.repository_root), PACKAGE))
    if current != scope:
        raise SettingsError("observability_scope_changed")
    path = state._path(scope)
    state._private(path.parent, True)
    state._private(path)
    # Configuration identity excludes qualification; consent also binds its accepted receipt.
    binding = dict(scope_id=state._scope_id(scope), accepted_scope_digest=scope.accepted_scope_digest,
        qualification_digest=startup._canonical_digest(startup._canonical_plain(scope.qualification_provenance)),
        authority_directory=_identity(path.parent.lstat()), authority_database=_identity(path.lstat()))
    with state._database(scope) as database:
        if database is None or state._checkout_row(database, scope) is None:
            raise SettingsError("existing_runtime_authority_required")
    return binding


def _integer(value):
    return type(value) is int and 0 <= value <= MAX_REVISION


def _request(action, request):
    fields = {"expected_revision", "destination", "langfuse_project_id"} if action == "configure" else {"expected_revision"}
    optional = {"credential_source"} if action == "configure" else set()
    if (not isinstance(request, dict) or not fields <= set(request) <= fields | optional
            or not _integer(request["expected_revision"])):
        raise SettingsError("invalid_observability_request")
    if action == "configure":
        if "credential_source" in request and request["credential_source"] not in ("environment", "os-store"):
            raise SettingsError("invalid_credential_source")
        return dict(request, destination=langfuse.validate_destination(request["destination"]),
                    langfuse_project_id=langfuse.validate_project(request["langfuse_project_id"]))
    return request


class _Store:
    def __init__(self, scope, binding, root, directory, lock):
        self.scope, self.binding, self.root, self.directory = scope, binding, root, directory
        self.lock = lock
        self.name = binding["scope_id"] + ".json"
        self.snapshot = None

    def _target(self):
        try:
            return os.stat(self.name, dir_fd=self.directory, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def load(self):
        if self.directory is None:
            return None
        try:
            descriptor = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(descriptor)
            _private(before)
            if before.st_size > MAX_CONFIG:
                raise SettingsError("invalid_observability_configuration")
            raw = bytearray()
            while len(raw) <= MAX_CONFIG:
                chunk = os.read(descriptor, MAX_CONFIG + 1 - len(raw))
                if not chunk:
                    break
                raw.extend(chunk)
            after, named = os.fstat(descriptor), self._target()
            if named is None or _signature(before) != _signature(after) or _signature(after) != _signature(named):
                raise SettingsError("observability_configuration_changed")
            self.snapshot = _signature(after)
        finally:
            os.close(descriptor)
        try:
            record = startup.json_object(bytes(raw), limit=MAX_CONFIG)
            schema = record.get("version")
            if type(schema) is not int or schema not in (1, 2):
                raise SettingsError("invalid_observability_configuration")
            fields = FIELDS if schema == 1 else FIELDS | {"credential_source"}
            if (set(record) != fields
                    or not _integer(record["revision"]) or record["revision"] < 1
                    or type(record["desired_opt_in"]) is not bool
                    or record["connection_verified_at"] is not None and not _integer(record["connection_verified_at"])
                    or record["destination"] != langfuse.validate_destination(record["destination"])
                    or record["langfuse_project_id"] != langfuse.validate_project(record["langfuse_project_id"])):
                raise SettingsError("invalid_observability_configuration")
            if schema == 2 and record["credential_source"] not in ("environment", "os-store"):
                raise SettingsError("invalid_observability_configuration")
            binding = record['binding']
            if type(self.scope) is local_ownership.LocalOwnership:
                historical = {'qualification_digest', 'accepted_scope_digest', 'authority_database'}
                if not isinstance(binding, dict) or set(binding) != set(self.binding) | historical:
                    raise SettingsError("observability_configuration_scope_changed")
                startup._canonical_hash(binding['qualification_digest'])
                startup._canonical_hash(binding['accepted_scope_digest'])
                startup._canonical_binding(binding['authority_database'])
                binding = {key: value for key, value in binding.items() if key not in historical}
            if json.dumps(binding, sort_keys=True) != json.dumps(self.binding, sort_keys=True):
                raise SettingsError("observability_configuration_scope_changed")
            return record
        except SettingsError:
            raise
        except (startup.StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise SettingsError("invalid_observability_configuration") from None

    def check(self):
        if _authority(self.scope) != self.binding or _identity(os.fstat(self.root)) != self.binding["authority_directory"]:
            raise SettingsError("observability_authority_changed")
        _private(os.fstat(self.root), directory=True)
        try:
            named = os.stat("observability", dir_fd=self.root, follow_symlinks=False)
        except FileNotFoundError:
            if self.directory is None:
                return
            raise SettingsError("observability_directory_changed") from None
        if self.directory is None:
            raise SettingsError("observability_directory_changed")
        _private(named, directory=True)
        if _identity(named) != _identity(os.fstat(self.directory)):
            raise SettingsError("observability_directory_changed")
        if self.lock is not None:
            locked = os.fstat(self.lock)
            _private(locked)
            if _signature(locked) != _signature(os.stat(self.name[:-5] + ".lock", dir_fd=self.directory, follow_symlinks=False)):
                raise SettingsError("observability_lock_changed")
        target = self._target()
        if (None if target is None else _signature(target)) != self.snapshot:
            raise SettingsError("observability_configuration_changed")

    def save(self, record):
        if record["revision"] > MAX_REVISION:
            raise SettingsError("observability_revision_exhausted")
        raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        if len(raw) > MAX_CONFIG:
            raise SettingsError("invalid_observability_configuration")
        temporary = "." + self.name + "." + uuid.uuid4().hex + ".tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.directory)
        info = os.fstat(descriptor)
        try:
            _private(info)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise SettingsError("observability_write_incomplete")
                offset += written
            os.fsync(descriptor)
            self.check()
            named = os.stat(temporary, dir_fd=self.directory, follow_symlinks=False)
            if _signature(named) != _signature(os.fstat(descriptor)):
                raise SettingsError("observability_temporary_changed")
            if self.snapshot is None:
                os.link(temporary, self.name, src_dir_fd=self.directory, dst_dir_fd=self.directory,
                        follow_symlinks=False)
                os.unlink(temporary, dir_fd=self.directory)
            else:
                os.replace(temporary, self.name, src_dir_fd=self.directory, dst_dir_fd=self.directory)
            os.fsync(self.directory)
            target = self._target()
            _private(target)
            if _identity(target) != _identity(info):
                raise SettingsError("observability_configuration_changed")
            self.snapshot = _signature(target)
            self.check()
        finally:
            os.close(descriptor)
            try:
                named = os.stat(temporary, dir_fd=self.directory, follow_symlinks=False)
                if _identity(named) == _identity(info) and stat.S_ISREG(named.st_mode):
                    os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError:
                pass


@contextmanager
def _guard(scope, *, write=False, create=False, recovery=False):
    root = directory = lock = None
    guards = ExitStack()
    try:
        if recovery:
            original = scope
            if type(original) is startup.AcceptedScope: state._scope_version(original)
            scope = local_ownership.settings_scope(original.repository_root if type(original) is startup.AcceptedScope else original)
            if local_ownership.settings_absent(scope, 'observability'):
                yield None
                if not local_ownership.settings_absent(scope, 'observability'):
                    raise SettingsError('observability_configuration_changed')
                return
            if type(original) is startup.AcceptedScope and (state._physical_identity(state._accepted_identity(original.accepted_identity))
                    != state._physical_identity(state._accepted_identity(scope.accepted_identity))
                    or original.runtime != scope.runtime):
                raise SettingsError('observability_scope_changed')
            if write: guards.enter_context(changes._authority_guards([scope.state_dir]))
        binding = _authority(scope)
        root = onboarding._open_path(scope.runtime.state_dir, os.O_RDONLY | os.O_DIRECTORY)
        if create:
            try:
                os.mkdir("observability", mode=0o700, dir_fd=root)
                os.fsync(root)
            except FileExistsError:
                pass
        try:
            directory = os.open("observability", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            _private(os.fstat(directory), directory=True)
        except FileNotFoundError:
            pass
        if write and directory is not None:
            import fcntl
            name = binding["scope_id"] + ".lock"
            lock = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            _private(os.fstat(lock))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SettingsError("observability_operation_busy") from None
            if _signature(os.fstat(lock)) != _signature(os.stat(name, dir_fd=directory, follow_symlinks=False)):
                raise SettingsError("observability_lock_changed")
        owner = _Store(scope, binding, root, directory, lock)
        yield owner
    except (SettingsError, langfuse.LangfuseError, credentials.CredentialError):
        raise
    except (OSError, startup.StartupError, ImportError, TypeError, ValueError, KeyError, RecursionError):
        raise SettingsError("observability_storage_unavailable") from None
    finally:
        for descriptor in (lock, directory, root):
            if descriptor is not None:
                os.close(descriptor)
        guards.close()


def _report(record):
    return dict(ok=True, provider="langfuse", control_scope="checkout", configured=record is not None,
        revision=record["revision"] if record else 0, desired_opt_in=record["desired_opt_in"] if record else False,
        destination=record["destination"] if record else None,
        langfuse_project_id=record["langfuse_project_id"] if record else None,
        credential_source=record.get("credential_source", "environment") if record else None,
        connection="not_checked", connection_verified_at=record["connection_verified_at"] if record else None,
        collection="unavailable", native_collection_qualified=False,
        independent_forwarding="unknown_unmanaged", remote_history="unchanged")


def status(scope):
    """Read local desired consent only; never acquire keys, refresh readiness or create files."""
    with _guard(scope, recovery=True) as owner:
        if owner is None: return _report(None)
        record = owner.load()
        owner.check()
        return _report(record)


def _operate(scope, action, request):
    request = _request(action, request)
    with _guard(scope, write=True, create=action == "configure", recovery=action == 'disable') as owner:
        if owner is None:
            if request['expected_revision'] != 0: raise SettingsError('observability_revision_conflict')
            return _report(None)
        record = owner.load()
        owner.check()
        if request["expected_revision"] != (record["revision"] if record else 0):
            raise SettingsError("observability_revision_conflict")
        if action == "configure":
            if record and record["desired_opt_in"]:
                raise SettingsError("disable_before_reconfiguration")
            updated = dict(version=1, revision=request["expected_revision"] + 1, binding=owner.binding,
                destination=request["destination"], langfuse_project_id=request["langfuse_project_id"],
                desired_opt_in=False, connection_verified_at=None)
            if "credential_source" in request or record and record["version"] == 2:
                previous = record.get("credential_source", "environment") if record else "environment"
                updated.update(version=2, credential_source=request.get("credential_source", previous))
        elif action == "disable":
            if not record or not record["desired_opt_in"]:
                return _report(record)
            updated = dict(record, revision=record["revision"] + 1, desired_opt_in=False)
        else:
            if record is None:
                raise SettingsError("observability_not_configured")
            if action == "probe" and not record["desired_opt_in"]:
                raise SettingsError("observability_not_enabled")
            connection = {}
            if record.get("credential_source", "environment") == "os-store":
                pair = credentials.load(record["destination"], record["langfuse_project_id"])
                owner.check()
                connection["credentials"] = pair
            if action == "probe":
                result = langfuse.synthetic_probe(record["destination"], record["langfuse_project_id"],
                                                 before_ingest=owner.check, **connection)
                owner.check()
                return dict(_report(record), probe=result)
            langfuse.verify_project(record["destination"], record["langfuse_project_id"], **connection)
            owner.check()
            updated = dict(record, revision=record["revision"] + 1, desired_opt_in=True,
                           connection_verified_at=int(time.time()))
        owner.save(updated)
        return dict(_report(updated), connection="verified_for_current_keys") if action == "enable" else _report(updated)


def configure(scope, request):
    return _operate(scope, "configure", request)


def enable(scope, request):
    return _operate(scope, "enable", request)


def disable(scope, request):
    return _operate(scope, "disable", request)


def probe(scope, request):
    return _operate(scope, "probe", request)


def _credential_change(scope, request, action, read_pair=None):
    request = _request(action, request)
    with _guard(scope, write=True) as owner:
        record = owner.load()
        owner.check()
        if request["expected_revision"] != (record["revision"] if record else 0):
            raise SettingsError("observability_revision_conflict")
        if record is None or record.get("credential_source", "environment") != "os-store":
            raise SettingsError("os_credential_source_required")
        if record["desired_opt_in"]:
            raise SettingsError("disable_before_credentials_change")
        if action == "store-credentials":
            credentials.check_available()
            owner.check()
            pair = credentials.validate_pair(read_pair())
            owner.check()
            langfuse.verify_project(record["destination"], record["langfuse_project_id"], credentials=pair)
            owner.check()
            credentials.store(record["destination"], record["langfuse_project_id"], pair, before_change=owner.check)
        else:
            credentials.forget(record["destination"], record["langfuse_project_id"], before_change=owner.check)
        owner.check()
        updated = dict(record, revision=record["revision"] + 1, connection_verified_at=None)
        owner.save(updated)
        return dict(_report(updated), credential_operation="stored" if action == "store-credentials" else "forgotten")


def store_credentials(scope, request, read_pair):
    return _credential_change(scope, request, "store-credentials", read_pair)


def forget_credentials(scope, request):
    return _credential_change(scope, request, "forget-credentials")
