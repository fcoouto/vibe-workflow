"""Checkout connection consent and Project collection choice, separate from task evidence and collection."""

from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from types import SimpleNamespace
import uuid

from runtime import changes, langfuse, local_ownership, onboarding, observability_credentials as credentials, persisted_binding, startup, state


PACKAGE = Path(__file__).resolve().parents[1]
MAX_CONFIG = 8 * 1024
MAX_REVISION = 2 ** 53 - 1
FIELDS = {"version", "revision", "binding", "destination", "langfuse_project_id",
          "desired_opt_in", "connection_verified_at"}
PROJECT_FIELDS = {"version", "revision", "binding", "repository_ids", "desired_collection"}
MAX_REPOSITORIES = 64
IDENTIFIER = re.compile(r"[a-zA-Z0-9_-]{1,128}")
# Upload state for one task and destination: `<state_dir>/observability/exports/<name>.json`.
MAX_EXPORT = 64 * 1024
EXPORT_FIELDS = {"version", "revision", "binding", "task_ref", "destination_key", "export_state", "last_result"}
EXPORT_RESULTS = frozenset({"not_exportable", "totals_unknown", "usage_inconsistent", "uncertain_pending",
    "stale_reconciliation", "negative_delta", "nothing_to_send", "accepted", "rejected", "uncertain",
    "participants_unresolved", "credentials_unavailable", "hook_failed"})
DESTINATION_KEY = re.compile(r"[0-9a-f]{32}")
TASK_REF = ("client", "session_id", "task_id")


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


def _project_id(scope):
    if type(scope) is startup.AcceptedScope:
        value = scope.project_id
    elif type(scope) is local_ownership.LocalOwnership:
        value = startup._canonical_plain(scope.acquisition.record.data)["project_id"]
    else:
        raise SettingsError("unsupported_observability_scope")
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise SettingsError("invalid_observability_project")
    return value


def _binding(scope, project=False, export=False):
    # A Project record is shared by the Project's checkouts, so it binds no checkout identity.
    authority = _authority(scope)
    if export:
        # Upload state outlives qualification changes, so it binds the checkout and folder, not consent.
        return dict(scope_id=authority["scope_id"], project_id=_project_id(scope),
                    authority_directory=authority["authority_directory"])
    if not project:
        return authority
    return dict(project_id=_project_id(scope), authority_directory=authority["authority_directory"])


def destination_key(destination, langfuse_project_id):
    """First 32 hex of SHA-256 over the destination and Langfuse project; names no credential."""
    return hashlib.sha256((destination + "\n" + langfuse_project_id).encode()).hexdigest()[:32]


def export_name(scope_id, task_ref, key):
    if (not isinstance(task_ref, dict) or set(task_ref) != set(TASK_REF)
            or not all(type(task_ref[name]) is str and task_ref[name] for name in TASK_REF)
            or type(key) is not str or not DESTINATION_KEY.fullmatch(key)):
        raise SettingsError("invalid_observability_request")
    raw = json.dumps([scope_id, *(task_ref[name] for name in TASK_REF), key], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


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


def _repositories(value):
    if (type(value) is not list or not 0 < len(value) <= MAX_REPOSITORIES
            or not all(type(item) is str and IDENTIFIER.fullmatch(item) for item in value)
            or len(set(value)) != len(value)):
        return None
    return sorted(value)


def _project_request(action, request):
    fields = {"expected_revision", "repository_ids", "desired_collection"} if action == "configure" else {"expected_revision"}
    if not isinstance(request, dict) or set(request) != fields or not _integer(request["expected_revision"]):
        raise SettingsError("invalid_observability_request")
    if action == "configure":
        repositories = _repositories(request["repository_ids"])
        if repositories is None or type(request["desired_collection"]) is not bool:
            raise SettingsError("invalid_observability_request")
        return dict(request, repository_ids=repositories)
    return request


class _Store:
    def __init__(self, scope, binding, root, directory, lock, record=None, export=None, parent=None):
        self.scope, self.binding, self.root, self.directory = scope, binding, root, directory
        self.lock, self.export, self.parent = lock, export, parent
        self.project = record is not None and export is None
        self.name = (binding["scope_id"] if record is None else record) + ".json"
        self.limit = MAX_EXPORT if export is not None else MAX_CONFIG
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
            if before.st_size > self.limit:
                raise SettingsError("invalid_observability_configuration")
            raw = bytearray()
            while len(raw) <= self.limit:
                chunk = os.read(descriptor, self.limit + 1 - len(raw))
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
            record = startup.json_object(bytes(raw), limit=self.limit)
            if self.export is not None:
                result = record.get("last_result")
                if (set(record) != EXPORT_FIELDS or type(record["version"]) is not int or record["version"] != 1
                        or not _integer(record["revision"]) or record["revision"] < 1
                        or record["export_state"] is not None and type(record["export_state"]) is not dict
                        or result is not None and (type(result) is not dict or set(result) != {"at", "code"}
                            or not _integer(result["at"]) or result["code"] not in EXPORT_RESULTS)):
                    raise SettingsError("invalid_observability_configuration")
                if not persisted_binding.record_matches(record["binding"], self.binding):
                    raise SettingsError("observability_scope_changed")
                if record["task_ref"] != self.export["task_ref"] or record["destination_key"] != self.export["destination_key"]:
                    raise SettingsError("observability_export_mismatch")
                return record
            if self.project:
                if (set(record) != PROJECT_FIELDS or type(record["version"]) is not int or record["version"] != 1
                        or not _integer(record["revision"]) or record["revision"] < 1
                        or type(record["desired_collection"]) is not bool
                        or _repositories(record["repository_ids"]) != record["repository_ids"]):
                    raise SettingsError("invalid_observability_configuration")
                if not persisted_binding.record_matches(record["binding"], self.binding):
                    raise SettingsError("observability_scope_changed")
                return record
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
            if not persisted_binding.record_matches(binding, self.binding):
                raise SettingsError("observability_configuration_scope_changed")
            return record
        except SettingsError:
            raise
        except (startup.StartupError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise SettingsError("invalid_observability_configuration") from None

    def check(self):
        if _binding(self.scope, self.project, self.export is not None) != self.binding or _identity(os.fstat(self.root)) != self.binding["authority_directory"]:
            raise SettingsError("observability_authority_changed")
        _private(os.fstat(self.root), directory=True)
        chain = [(self.root, "observability", self.directory)]
        if self.export is not None:
            chain = [(self.root, "observability", self.parent), (self.parent, "exports", self.directory)]
        for container, part, opened in chain:
            if container is None:
                return
            try:
                named = os.stat(part, dir_fd=container, follow_symlinks=False)
            except FileNotFoundError:
                if opened is None:
                    return
                raise SettingsError("observability_directory_changed") from None
            if opened is None:
                raise SettingsError("observability_directory_changed")
            _private(named, directory=True)
            if _identity(named) != _identity(os.fstat(opened)):
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
        if len(raw) > self.limit:
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
def _guard(scope, *, write=False, create=False, recovery=False, project=None, export=None):
    """`project`, when a dict, selects the shared Project record and receives its `id` and the resolved `scope`.

    `export`, when `{task_ref, destination_key}`, selects that task's upload state in `exports/`."""
    root = parent = directory = lock = record = None
    guards = ExitStack()
    try:
        if recovery:
            original = scope
            if type(original) is startup.AcceptedScope: state._scope_version(original)
            known = type(original) in (startup.AcceptedScope, local_ownership.LocalOwnership)
            scope = local_ownership.settings_scope(original.repository_root if known else original)
            if type(original) is local_ownership.LocalOwnership and scope != original:
                raise SettingsError('observability_scope_changed')
        if project is not None:
            project.update(id=_project_id(scope), scope=scope)
            record = "project-" + project["id"]
        if export is not None:
            scope_id = scope.scope_id if type(scope) is local_ownership.LocalOwnership else state._scope_id(scope)
            record = export_name(scope_id, export["task_ref"], export["destination_key"])
        if recovery:
            named = record if export is None else "exports/" + record
            if local_ownership.settings_absent(scope, 'observability', named):
                yield None
                if not local_ownership.settings_absent(scope, 'observability', named):
                    raise SettingsError('observability_configuration_changed')
                return
            if type(original) is startup.AcceptedScope and (state._physical_identity(state._accepted_identity(original.accepted_identity))
                    != state._physical_identity(state._accepted_identity(scope.accepted_identity))
                    or original.runtime != scope.runtime):
                raise SettingsError('observability_scope_changed')
            if write: guards.enter_context(changes._authority_guards([scope.state_dir]))
        binding = _binding(scope, record is not None, export is not None)
        root = onboarding._open_path(scope.runtime.state_dir, os.O_RDONLY | os.O_DIRECTORY)
        for part in ("observability", "exports") if export is not None else ("observability",):
            container = root if part == "observability" else parent
            if container is None:
                break
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=container)
                    os.fsync(container)
                except FileExistsError:
                    pass
            try:
                directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=container)
                _private(os.fstat(directory), directory=True)
            except FileNotFoundError:
                directory = None
            if export is not None and part == "observability":
                parent, directory = directory, None
        if write and directory is not None:
            import fcntl
            name = (binding["scope_id"] if record is None else record) + ".lock"
            lock = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            _private(os.fstat(lock))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SettingsError("observability_operation_busy") from None
            if _signature(os.fstat(lock)) != _signature(os.stat(name, dir_fd=directory, follow_symlinks=False)):
                raise SettingsError("observability_lock_changed")
        owner = _Store(scope, binding, root, directory, lock, record, export, parent)
        yield owner
    except (SettingsError, langfuse.LangfuseError, credentials.CredentialError):
        raise
    except (OSError, startup.StartupError, ImportError, TypeError, ValueError, KeyError, RecursionError):
        raise SettingsError("observability_storage_unavailable") from None
    finally:
        for descriptor in (lock, directory, parent, root):
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
        independent_forwarding="unknown_unmanaged", remote_history="unchanged")


def _checkout_report(scope, record):
    """Checkout report with the `collection` value project-status gives for this checkout now. `record` is the
    checkout record just read, so the gate reuses it; an unreadable Project record gives `project_unavailable`."""
    try:
        owner, _, project = _read_project(scope)
    except SettingsError:
        code = "project_unavailable"
    else:
        code = _collection_code(owner, project, lambda: record)
    return dict(_report(record), **_collection_fields(code))


def _read_checkout(scope, *, recovery=False):
    with _guard(scope, recovery=recovery) as owner:
        if owner is None: return None
        record = owner.load()
        owner.check()
        return record


def status(scope):
    """Read local desired consent only; never acquire keys, refresh readiness or create files."""
    return _checkout_report(scope, _read_checkout(scope, recovery=True))


def _operate(scope, action, request):
    record, extra = _change(scope, action, request)
    return dict(_checkout_report(scope, record), **extra)


def _change(scope, action, request):
    request = _request(action, request)
    with _guard(scope, write=True, create=action == "configure", recovery=action == 'disable') as owner:
        if owner is None:
            if request['expected_revision'] != 0: raise SettingsError('observability_revision_conflict')
            return None, {}
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
                return record, {}
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
                return record, dict(probe=result)
            langfuse.verify_project(record["destination"], record["langfuse_project_id"], **connection)
            owner.check()
            updated = dict(record, revision=record["revision"] + 1, desired_opt_in=True,
                           connection_verified_at=int(time.time()))
        owner.save(updated)
        return updated, dict(connection="verified_for_current_keys") if action == "enable" else {}


def configure(scope, request):
    return _operate(scope, "configure", request)


def enable(scope, request):
    return _operate(scope, "enable", request)


def disable(scope, request):
    return _operate(scope, "disable", request)


def probe(scope, request):
    return _operate(scope, "probe", request)


def _credential_change(scope, request, action, read_pair=None):
    record = _credential_save(scope, request, action, read_pair)
    return dict(_checkout_report(scope, record), credential_operation="stored" if action == "store-credentials" else "forgotten")


def _credential_save(scope, request, action, read_pair):
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
        return updated


def store_credentials(scope, request, read_pair):
    return _credential_change(scope, request, "store-credentials", read_pair)


def forget_credentials(scope, request):
    return _credential_change(scope, request, "forget-credentials")


_UNREADABLE = object()


def collection_gate(record, repository_id, read_checkout, runtime_ready):
    """The upload hook's settings gate, shared by `export_destination` and Project reports so they never disagree.

    Checks run in order and stop at the first unmet one, so later records are never read. `read_checkout()` returns
    the checkout record, None, or `_UNREADABLE`; `runtime_ready()` says whether the hook would run here now.
    Returns `(code, connection)`; `connection` is set only for `enabled`."""
    if record is None or not record["desired_collection"]:
        return "off", None
    if repository_id is None:
        # Only a checkout without a configured runtime lacks a Repository ID, and the hook never runs there.
        return "runtime_unavailable", None
    if repository_id not in record["repository_ids"]:
        return "repository_not_selected", None
    checkout = read_checkout()
    if checkout is _UNREADABLE:
        return "connection_unavailable", None
    if checkout is None or not checkout["desired_opt_in"]:
        return "connection_not_enabled", None
    if not runtime_ready():
        return "runtime_unavailable", None
    return "enabled", dict(destination=checkout["destination"], langfuse_project_id=checkout["langfuse_project_id"],
                           credential_source=checkout.get("credential_source", "environment"))


def _repository_id(scope):
    if type(scope) is startup.AcceptedScope:
        return scope.repository_id
    identity = startup._canonical_plain(scope.accepted_identity)
    return identity.get("repository_id") if isinstance(identity, dict) else None


def _runtime_ready(scope):
    """Whether the hook would send for this checkout now: its current qualified Codex scope gets a connection."""
    try:
        current = startup._entry_scope(startup.acquire_project_entry(
            SimpleNamespace(client="codex", cwd=scope.repository_root), PACKAGE))
        return (type(current) is startup.AcceptedScope and current.client == "codex" and current.runtime is not None
                and current.runtime.schema_version == 3 and export_destination(current) is not None)
    except (startup.StartupError, OSError, ValueError, TypeError, KeyError, RecursionError):
        return False


def _collection_fields(code):
    from runtime.adapters import codex_usage
    sources = sorted("codex " + version for version in codex_usage.QUALIFIED_VERSIONS)
    return dict(collection=code, native_collection_qualified=bool(sources), collection_sources=sources)


def _collection_code(scope, project, read_checkout):
    """`collection` for this checkout: the shared gate with the current runtime check."""
    return collection_gate(project, _repository_id(scope), read_checkout, lambda: _runtime_ready(scope))[0]


def _project_report(project_id, record, collection="off", checkout=None):
    return dict(ok=True, control_scope="project", project_id=project_id, configured=record is not None,
        revision=record["revision"] if record else 0, repository_ids=list(record["repository_ids"]) if record else [],
        desired_collection=record["desired_collection"] if record else False,
        **_collection_fields(collection), checkout_connection=checkout)


def _project_collection(scope, project_id, record):
    """Project report with `collection` from the shared gate; the checkout record is read only past the Project gate."""
    seen = {}

    def read_checkout():
        try:
            checkout = _read_checkout(scope, recovery=True)
        except SettingsError as error:
            # Report-only: an unreadable checkout record must not hide or misreport the saved Project choice.
            seen["checkout"] = dict(status="unavailable", error=str(error))
            return _UNREADABLE
        seen["checkout"] = dict(configured=checkout is not None, desired_opt_in=checkout is not None and checkout["desired_opt_in"])
        return checkout
    return _project_report(project_id, record, _collection_code(scope, record, read_checkout), seen.get("checkout"))


def _read_project(scope):
    """The Project record through the recovery path: `(resolved scope, project_id, record or None)`."""
    project = {}
    with _guard(scope, recovery=True, project=project) as owner:
        record = None
        if owner is not None:
            record = owner.load()
            owner.check()
    return project["scope"], project["id"], record


def project_status(scope):
    """Read the local Project collection choice and what the upload hook would do here; no usage, credentials,
    network or file creation."""
    return _project_collection(*_read_project(scope))


def _project_operate(scope, action, request):
    request = _project_request(action, request)
    configure = action == "configure"
    if configure:
        if type(scope) is not startup.AcceptedScope:
            raise SettingsError("unsupported_observability_scope")
        if scope.repository_id not in request["repository_ids"]:
            raise SettingsError("observability_repository_not_selected")
    project = {}
    with _guard(scope, write=True, create=configure, recovery=not configure, project=project) as owner:
        if owner is None:
            if request["expected_revision"] != 0: raise SettingsError("observability_revision_conflict")
            return _project_collection(project["scope"], project["id"], None)
        record = owner.load()
        owner.check()
        if request["expected_revision"] != (record["revision"] if record else 0):
            raise SettingsError("observability_revision_conflict")
        if configure:
            updated = dict(version=1, revision=request["expected_revision"] + 1, binding=owner.binding,
                repository_ids=request["repository_ids"], desired_collection=request["desired_collection"])
        elif not record or not record["desired_collection"]:
            return _project_collection(project["scope"], project["id"], record)
        else:
            updated = dict(record, revision=record["revision"] + 1, desired_collection=False)
        owner.save(updated)
    return _project_collection(project["scope"], project["id"], updated)


def configure_project(scope, request):
    """Store the explicit Project collection choice; requires the current qualified canonical Codex scope."""
    return _project_operate(scope, "configure", request)


def disable_project(scope, request):
    """Turn the Project collection choice off and keep the record; works through local-ownership recovery."""
    return _project_operate(scope, "disable", request)


def export_destination(scope):
    """Return this checkout's enabled connection only when its own state folder's Project record selects collection
    for its repository; otherwise None. Reads stop at the first unmet condition and never touch credentials."""
    if type(scope) is not startup.AcceptedScope or scope.runtime is None:
        return None
    project = {}
    with _guard(scope, project=project) as owner:
        record = owner.load()
        owner.check()
    return collection_gate(record, scope.repository_id, lambda: _read_checkout(scope), lambda: True)[1]


def export_store(scope, task_ref, key, *, write=False, create=False, recovery=False):
    """Locked private upload-state file for one task and destination; `load`, `check` and `save` as for settings."""
    return _guard(scope, write=write, create=create, recovery=recovery,
                  export=dict(task_ref=task_ref, destination_key=key))
