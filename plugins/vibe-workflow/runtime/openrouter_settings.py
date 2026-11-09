"""Private checkout consent for qualified helper paths, independent of accounts."""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import uuid

from runtime import changes, local_ownership, onboarding, startup, state, external_delegation

PACKAGE = Path(__file__).resolve().parents[1]
BASIC_STORAGE = Path.home() / ".vcw-openrouter-consent"
MAX_CONFIG = 8192
MAX_REVISION = 2 ** 53 - 1
FIELDS = {'version', 'revision', 'binding', 'routes', 'opencode', 'auth_data', 'enabled'}


class SettingsError(startup.StartupError):
    """Only fixed, nonsecret diagnostics cross the management boundary."""


def require(value, reason):
    if not value: raise SettingsError(reason)


def identity(info):
    return dict(device=info.st_dev, inode=info.st_ino)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode,
            info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


def resolve(cwd):
    require(cwd is not None and os.name == 'posix', 'openrouter_scope_required')
    acquired = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=Path(cwd)), PACKAGE)
    scope = startup._entry_scope(acquired)
    require(type(scope) is startup.AcceptedScope and scope.runtime is not None
            and scope.runtime.schema_version == 3,
            'existing_enrolled_runtime_required')
    return scope


@dataclass(frozen=True)
class BasicWorkerScope:
    repository_root: Path
    owner: local_ownership.LocalOwnership
    recovery: bool


def basic_worker_scope(owner, *, recovery=False):
    if owner.runtime is not None or owner.accepted_identity is None:
        return None
    if recovery:
        return BasicWorkerScope(owner.repository_root, owner, True)
    selected = owner.accepted_identity
    record = owner.acquisition.record.data
    repo = next(row for row in record['repositories'] if row['repository_id'] == selected['repository_id'])
    checkout = next(row for row in repo['checkouts'] if row['checkout_id'] == selected['checkout_id'])
    entry = next(row for row in checkout['clients'] if row['client'] == 'codex')
    if entry['activation']['status'] != 'configured':
        return None
    current = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=owner.repository_root),
        PACKAGE, managed=False)
    require(current.resolution.reason == 'basic_guidance'
            and current.authority_path == owner.acquisition.authority_path
            and local_ownership.settings_scope(owner.repository_root) == owner,
            'openrouter_basic_scope_changed')
    return BasicWorkerScope(owner.repository_root, owner, False)


def authority(scope):
    if isinstance(scope, BasicWorkerScope):
        owner = scope.owner
        require(basic_worker_scope(local_ownership.settings_scope(scope.repository_root), recovery=scope.recovery) == scope,
            'openrouter_basic_scope_changed')
        onboarding._no_links(BASIC_STORAGE)
        state._private(BASIC_STORAGE, True)
        return dict(scope_id=owner.scope_id, repository=identity(scope.repository_root.lstat()),
            git_common=identity(owner.git_common_dir.lstat()), directory=identity(BASIC_STORAGE.lstat()),
            enrollment=state._digest(startup._canonical_plain(owner.acquisition.authority_target)))
    if type(scope) is local_ownership.LocalOwnership:
        return dict(scope_id=scope.scope_id, repository=identity(scope.repository_root.lstat()),
            git_common=identity(scope.git_common_dir.lstat()), directory=local_ownership.settings_directory(scope))
    require(resolve(scope.repository_root) == scope, 'openrouter_scope_changed')
    database = state._path(scope)
    state._private(database.parent, True)
    state._private(database)
    with state._database(scope) as connection:
        require(connection is not None and state._checkout_row(connection, scope) is not None,
                'existing_runtime_authority_required')
    return dict(scope_id=state._scope_id(scope), repository=identity(scope.repository_root.lstat()),
        git_common=identity(scope.git_common_dir.lstat()), directory=identity(database.parent.lstat()),
        database=identity(database.lstat()))


def integer(value):
    return type(value) is int and 0 <= value <= MAX_REVISION


def reference(value):
    return (isinstance(value, str) and 0 < len(value) <= 4096 and value.isprintable()
            and Path(value).is_absolute() and str(Path(value)) == value and '..' not in Path(value).parts)


def validate_routes(routes):
    require(isinstance(routes, list) and 1 <= len(routes) <= 2
            and all(type(route) is str and route in ('proposals', 'workers') for route in routes)
            and len(set(routes)) == len(routes), 'invalid_openrouter_routes')


def runtime_references(opencode, auth_data):
    require(reference(opencode) and reference(auth_data), 'invalid_openrouter_runtime_references')
    executable, auth = Path(opencode), Path(auth_data)
    require(executable.resolve(strict=True) == executable and executable.is_file()
            and os.access(executable, os.X_OK), 'openrouter_runtime_unavailable')
    require(auth.resolve(strict=True) == auth, 'openrouter_auth_directory_unavailable')
    state._private(auth, True)


class Store:
    def __init__(self, scope, root, directory, binding, *, refresh=False):
        self.scope, self.root, self.directory = scope, root, directory
        storage = BASIC_STORAGE if isinstance(scope, BasicWorkerScope) else scope.runtime.state_dir if scope.runtime else None
        self.folder = storage / 'openrouter' if storage else None
        self.binding = dict(binding)
        self.stale_enrollment = isinstance(scope, BasicWorkerScope) and (scope.recovery or refresh)
        if directory is not None: self.binding['namespace'] = identity(os.fstat(directory))
        self.name = binding['scope_id'] + '.json'
        self.path = self.folder / self.name if self.folder else None
        self.data = self.folder / binding['scope_id'] if self.folder else None
        self.snapshot = None
        self.record = None

    def target(self):
        try: return self.path.lstat()
        except FileNotFoundError: return None

    def check(self):
        current = authority(self.scope)
        excluded = {'namespace'}
        if type(self.scope) is local_ownership.LocalOwnership:
            excluded.add('database')
        if self.stale_enrollment:
            excluded.add('enrollment')
        require({key: value for key, value in current.items() if key not in excluded}
                == {key: value for key, value in self.binding.items() if key not in excluded}
                and identity(os.fstat(self.root)) == current['directory'], 'openrouter_authority_changed')
        try: named = self.folder.lstat()
        except FileNotFoundError:
            require(self.directory is None, 'openrouter_namespace_changed')
            return
        state._private(self.folder, True)
        require(self.directory is not None and identity(named) == identity(os.fstat(self.directory)),
                'openrouter_namespace_changed')
        target = self.target()
        require((signature(target) if target else None) == self.snapshot, 'openrouter_configuration_changed')

    def load(self):
        if self.directory is None: return
        before = self.target()
        if before is None: return
        self.snapshot = signature(before)
        raw = onboarding._read_file(self.path, MAX_CONFIG, private=True, expected_binding=identity(before))
        require(signature(self.path.lstat()) == self.snapshot, 'openrouter_configuration_changed')
        record = startup.json_object(raw, limit=MAX_CONFIG)
        binding = record.get('binding')
        if type(self.scope) is local_ownership.LocalOwnership:
            require(isinstance(binding, dict) and set(binding) == set(self.binding) | {'database'},
                    'invalid_or_changed_openrouter_configuration')
            startup._canonical_binding(binding['database'])
            binding = {key: value for key, value in binding.items() if key != 'database'}
        if self.stale_enrollment:
            startup._canonical_hash(binding['enrollment'])
            binding = dict(binding, enrollment=self.binding['enrollment'])
        require(FIELDS <= set(record) <= FIELDS | {'external_permission'} and type(record['version']) is int and record['version'] in (1, 2)
                and integer(record['revision']) and record['revision'] > 0
                and type(record['enabled']) is bool and binding == self.binding,
                'invalid_or_changed_openrouter_configuration')
        validate_routes(record['routes'])
        if record.get('external_permission') is not None:
            external_delegation.validate_permission(record['external_permission'])
        if isinstance(self.scope, BasicWorkerScope):
            require(record['routes'] == ['workers'], 'openrouter_basic_workers_only')
        if 'workers' in record['routes']:
            require(reference(record['opencode']) and reference(record['auth_data']), 'invalid_openrouter_configuration')
        else:
            require(record['opencode'] is None and record['auth_data'] is None, 'invalid_openrouter_configuration')
        self.record = record
        self.binding = dict(record['binding'])

    def save(self, record):
        require(integer(record['revision']) and record['revision'] > 0, 'openrouter_revision_exhausted')
        raw = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        require(len(raw) <= MAX_CONFIG, 'invalid_openrouter_configuration')
        if self.snapshot is None:
            temporary = '.configure-' + uuid.uuid4().hex
            changes._atomic(self.folder, temporary, raw, guard=self.check)
            try:
                self.check()
                os.link(temporary, self.name, src_dir_fd=self.directory, dst_dir_fd=self.directory,
                        follow_symlinks=False)
            finally:
                os.unlink(temporary, dir_fd=self.directory)
            os.fsync(self.directory)
        else:
            changes._atomic(self.folder, self.name, raw, guard=self.check)
        self.snapshot = signature(self.path.lstat())
        self.record = record
        require(onboarding._read_file(self.path, MAX_CONFIG, private=True) == raw,
                'openrouter_configuration_changed')
        self.check()

    def report(self):
        record = self.record
        worker = record is not None and 'workers' in record['routes']
        renewal = worker and record['version'] == 1
        readiness = 'not_configured'
        if record:
            try:
                state._private(self.data, True)
                if 'workers' in record['routes']: runtime_references(record['opencode'], record['auth_data'])
                readiness = 'ready'
            except (SettingsError, startup.StartupError, OSError, RuntimeError):
                readiness = 'unavailable'
        return dict(ok=True, control_scope='checkout', configured=record is not None,
            revision=record['revision'] if record else 0,
            external_permission=record.get('external_permission') if record else None, enabled=record['enabled'] if record else False,
            routes=record['routes'] if record else [], scope_id=self.binding['scope_id'],
            consent_version=record['version'] if record else None,
            worker_capability=('renewal_required' if renewal else 'read_only') if worker else 'not_applicable',
            enablement_renewal_required=renewal,
            checkout=str(self.scope.repository_root), local_configuration=readiness,
            connection='not_checked', execution_qualification='not_checked',
            opencode=record['opencode'] if record else None, auth_data=record['auth_data'] if record else None,
            proposal_store=str(self.data / 'discovery.json') if self.data else None,
            worker_root=str(self.data / 'runs') if self.data else None)


@contextmanager
def guard(cwd, *, write=False, create=False, recovery=False):
    require(cwd is not None and os.name == 'posix', 'openrouter_scope_required')
    in_body = False
    try:
        local = local_ownership.settings_scope(cwd)
        basic = basic_worker_scope(local, recovery=recovery)
        if basic is not None:
            if create:
                try: onboarding._plan_path(BASIC_STORAGE)
                except FileExistsError: onboarding._plan_path(BASIC_STORAGE, create=False)
            if not os.path.lexists(BASIC_STORAGE):
                yield Store(basic, None, None, dict(scope_id=local.scope_id))
                require(not os.path.lexists(BASIC_STORAGE)
                    and basic_worker_scope(local_ownership.settings_scope(cwd), recovery=recovery) == basic,
                    'openrouter_basic_scope_changed')
                return
        scope = basic if basic is not None else local if recovery else resolve(cwd)
        if recovery and basic is None and local_ownership.settings_absent(scope, 'openrouter'):
            yield Store(scope, None, None, dict(scope_id=scope.scope_id))
            require(local_ownership.settings_absent(scope, 'openrouter'), 'openrouter_configuration_changed')
            return
        storage = BASIC_STORAGE if basic is not None else scope.runtime.state_dir
        with ExitStack() as stack:
            if write and basic is None: stack.enter_context(changes._authority_guards([storage]))
            binding = authority(scope)
            root = onboarding._open_path(storage, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, root)
            if create:
                try:
                    os.mkdir('openrouter', mode=0o700, dir_fd=root)
                    os.fsync(root)
                except FileExistsError: pass
            try:
                directory = os.open('openrouter', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                stack.callback(os.close, directory)
                state._private(storage / 'openrouter', True)
                require(identity(os.fstat(directory)) == identity((storage / 'openrouter').lstat()),
                        'openrouter_namespace_changed')
                try: fcntl.flock(directory, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                except BlockingIOError: raise SettingsError('openrouter_operation_busy') from None
            except FileNotFoundError:
                directory = None
            owner = Store(scope, root, directory, binding, refresh=create)
            owner.load()
            owner.check()
            in_body = True
            yield owner
            in_body = False
            owner.check()
    except SettingsError:
        raise
    except (OSError, startup.StartupError, ValueError, TypeError, KeyError, RecursionError, RuntimeError):
        if in_body: raise
        raise SettingsError('openrouter_storage_or_scope_unavailable') from None


def status(cwd):
    with guard(cwd, recovery=True) as owner: return owner.report()


def operate(cwd, action, request):
    require(action in ('configure', 'enable', 'disable'), 'invalid_openrouter_action')
    fields = {'expected_revision', 'routes', 'opencode', 'auth_data'} if action == 'configure' else {'expected_revision'}
    optional = {'consent_version'} if action == 'enable' else {'external_permission'} if action == 'configure' else set()
    require(isinstance(request, dict) and fields <= set(request) <= fields | optional
            and integer(request['expected_revision']),
            'invalid_openrouter_request')
    if 'consent_version' in request:
        require(type(request['consent_version']) is int and request['consent_version'] == 2,
                'invalid_openrouter_consent_version')
    with guard(cwd, write=True, create=action == 'configure', recovery=action == 'disable') as owner:
        current = owner.record
        revision = current['revision'] if current else 0
        require(request['expected_revision'] == revision, 'openrouter_revision_conflict')
        if action == 'configure':
            require(current is None or not current['enabled'], 'disable_before_openrouter_reconfiguration')
            validate_routes(request['routes'])
            if request.get('external_permission') is not None:
                try: external_delegation.validate_permission(request['external_permission'])
                except ValueError as error: raise SettingsError(str(error)) from None
            if isinstance(owner.scope, BasicWorkerScope):
                require(request['routes'] == ['workers'], 'openrouter_basic_workers_only')
            opencode, auth = request['opencode'], request['auth_data']
            if 'workers' in request['routes']:
                require(reference(opencode) and reference(auth), 'invalid_openrouter_runtime_references')
                opencode, auth = str(Path(opencode).resolve(strict=True)), str(Path(auth).resolve(strict=True))
                runtime_references(opencode, auth)
            else:
                require(opencode is None and auth is None, 'invalid_openrouter_runtime_references')
            try: os.mkdir(owner.binding['scope_id'], mode=0o700, dir_fd=owner.directory)
            except FileExistsError: pass
            state._private(owner.data, True)
            binding = owner.binding
            if isinstance(owner.scope, BasicWorkerScope):
                binding = dict(binding, enrollment=authority(owner.scope)['enrollment'])
            updated = dict(version=1, revision=revision + 1, binding=binding,
                routes=sorted(request['routes']), opencode=opencode, auth_data=auth, enabled=False)
            permission = request.get('external_permission', current.get('external_permission') if current else None)
            if permission is not None: updated['external_permission'] = permission
        else:
            if action == 'disable' and current is None: return owner.report()
            require(current is not None, 'openrouter_not_configured')
            renew = action == 'enable' and 'workers' in current['routes'] and current['version'] == 1
            require(not renew or request.get('consent_version') == 2, 'openrouter_worker_consent_required')
            if action == 'enable' and 'workers' in current['routes']:
                runtime_references(current['opencode'], current['auth_data'])
            wanted = action == 'enable'
            if current['enabled'] == wanted and not renew: return owner.report()
            updated = dict(current, version=2 if renew else current['version'], revision=revision + 1, enabled=wanted)
        owner.save(updated)
        return owner.report()


def configure(cwd, request): return operate(cwd, 'configure', request)
def enable(cwd, request): return operate(cwd, 'enable', request)
def disable(cwd, request): return operate(cwd, 'disable', request)


@contextmanager
def admission(cwd, route, *, store=None, root=None, opencode=None, auth_data=None, revision=None, scope_id=None):
    with guard(cwd, write=True) as owner:
        record = owner.record
        require(record is not None and record['enabled'], 'openrouter_disabled')
        require(route in record['routes'], 'openrouter_route_disabled')
        require(route != 'workers' or record['version'] == 2, 'openrouter_worker_consent_renewal_required')
        report = owner.report()
        require(report['local_configuration'] == 'ready', 'openrouter_configuration_unavailable')
        require(revision is None or type(revision) is int and revision == record['revision'],
                'openrouter_preparation_stale')
        require(scope_id is None or scope_id == report['scope_id'], 'openrouter_scope_changed')
        for actual, expected in ((store, report['proposal_store']), (root, report['worker_root']),
                                 (opencode, record['opencode']), (auth_data, record['auth_data'])):
            if actual is not None:
                require(expected is not None and Path(actual).resolve() == Path(expected),
                        'openrouter_configuration_mismatch')
        state._private(owner.data, True)
        owner.check()
        yield report
