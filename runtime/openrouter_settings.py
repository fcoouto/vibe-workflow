"""Private checkout consent for qualified helper paths, independent of accounts."""
from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import uuid

from runtime import changes, onboarding, startup, state

PACKAGE = Path(__file__).resolve().parents[1]
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
    scope = startup._entry_scope(startup.acquire_project_entry(
        SimpleNamespace(client='codex', cwd=Path(cwd)), PACKAGE))
    require(type(scope) is startup.AcceptedScope and scope.runtime is not None,
            'existing_enrolled_runtime_required')
    return scope


def authority(scope):
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
    require(isinstance(routes, list) and 1 <= len(routes) <= 3
            and all(type(route) is str and route in ('proposals', 'workers', 'council') for route in routes)
            and len(set(routes)) == len(routes), 'invalid_openrouter_routes')


def runtime_references(opencode, auth_data):
    require(reference(opencode) and reference(auth_data), 'invalid_openrouter_runtime_references')
    executable, auth = Path(opencode), Path(auth_data)
    require(executable.resolve(strict=True) == executable and executable.is_file()
            and os.access(executable, os.X_OK), 'openrouter_runtime_unavailable')
    require(auth.resolve(strict=True) == auth, 'openrouter_auth_directory_unavailable')
    state._private(auth, True)


class Store:
    def __init__(self, scope, root, directory, binding):
        self.scope, self.root, self.directory = scope, root, directory
        self.folder = scope.runtime.state_dir / 'openrouter'
        self.binding = dict(binding)
        if directory is not None: self.binding['namespace'] = identity(os.fstat(directory))
        self.name = binding['scope_id'] + '.json'
        self.path = self.folder / self.name
        self.data = self.folder / binding['scope_id']
        self.snapshot = None
        self.record = None

    def target(self):
        try: return self.path.lstat()
        except FileNotFoundError: return None

    def check(self):
        current = authority(self.scope)
        require(current == {key: value for key, value in self.binding.items() if key != 'namespace'}
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
        require(set(record) == FIELDS and type(record['version']) is int and record['version'] == 1
                and integer(record['revision']) and record['revision'] > 0
                and type(record['enabled']) is bool and record['binding'] == self.binding,
                'invalid_or_changed_openrouter_configuration')
        validate_routes(record['routes'])
        if set(record['routes']) & {'workers', 'council'}:
            require(reference(record['opencode']) and reference(record['auth_data']), 'invalid_openrouter_configuration')
        else:
            require(record['opencode'] is None and record['auth_data'] is None, 'invalid_openrouter_configuration')
        self.record = record

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
        readiness = 'not_configured'
        if record:
            try:
                state._private(self.data, True)
                if set(record['routes']) & {'workers', 'council'}:
                    runtime_references(record['opencode'], record['auth_data'])
                readiness = 'ready'
            except (SettingsError, startup.StartupError, OSError, RuntimeError):
                readiness = 'unavailable'
        return dict(ok=True, control_scope='checkout', configured=record is not None,
            revision=record['revision'] if record else 0, enabled=record['enabled'] if record else False,
            routes=record['routes'] if record else [], scope_id=self.binding['scope_id'],
            checkout=str(self.scope.repository_root), local_configuration=readiness,
            connection='not_checked', execution_qualification='not_checked',
            opencode=record['opencode'] if record else None, auth_data=record['auth_data'] if record else None,
            proposal_store=str(self.data / 'discovery.json'), worker_root=str(self.data / 'runs'),
            council_root=str(self.data / 'council'))


@contextmanager
def guard(cwd, *, write=False, create=False):
    in_body = False
    try:
        scope = resolve(cwd)
        with ExitStack() as stack:
            if write:
                try: stack.enter_context(changes._authority_guards([scope.runtime.state_dir]))
                except startup.StartupError as error:
                    if type(error) is startup.StartupError and str(error) == 'D4 transaction busy; retry the same bound request':
                        raise SettingsError('openrouter_operation_busy') from None
                    raise
            binding = authority(scope)
            root = onboarding._open_path(scope.runtime.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, root)
            if create:
                try:
                    os.mkdir('openrouter', mode=0o700, dir_fd=root)
                    os.fsync(root)
                except FileExistsError: pass
            try:
                directory = os.open('openrouter', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                stack.callback(os.close, directory)
                state._private(scope.runtime.state_dir / 'openrouter', True)
                require(identity(os.fstat(directory)) == identity((scope.runtime.state_dir / 'openrouter').lstat()),
                        'openrouter_namespace_changed')
                try: fcntl.flock(directory, (fcntl.LOCK_EX if write else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                except BlockingIOError: raise SettingsError('openrouter_operation_busy') from None
            except FileNotFoundError:
                directory = None
            owner = Store(scope, root, directory, binding)
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
    with guard(cwd) as owner: return owner.report()


def operate(cwd, action, request):
    fields = {'expected_revision', 'routes', 'opencode', 'auth_data'} if action == 'configure' else {'expected_revision'}
    require(isinstance(request, dict) and set(request) == fields and integer(request['expected_revision']),
            'invalid_openrouter_request')
    with guard(cwd, write=True, create=action == 'configure') as owner:
        current = owner.record
        revision = current['revision'] if current else 0
        require(request['expected_revision'] == revision, 'openrouter_revision_conflict')
        if action == 'configure':
            require(current is None or not current['enabled'], 'disable_before_openrouter_reconfiguration')
            validate_routes(request['routes'])
            opencode, auth = request['opencode'], request['auth_data']
            if set(request['routes']) & {'workers', 'council'}:
                require(reference(opencode) and reference(auth), 'invalid_openrouter_runtime_references')
                opencode, auth = str(Path(opencode).resolve(strict=True)), str(Path(auth).resolve(strict=True))
                runtime_references(opencode, auth)
            else:
                require(opencode is None and auth is None, 'invalid_openrouter_runtime_references')
            try: os.mkdir(owner.binding['scope_id'], mode=0o700, dir_fd=owner.directory)
            except FileExistsError: pass
            state._private(owner.data, True)
            updated = dict(version=1, revision=revision + 1, binding=owner.binding,
                routes=sorted(request['routes']), opencode=opencode, auth_data=auth, enabled=False)
        else:
            if action == 'disable' and current is None: return owner.report()
            require(current is not None, 'openrouter_not_configured')
            if action == 'enable' and set(current['routes']) & {'workers', 'council'}:
                runtime_references(current['opencode'], current['auth_data'])
            wanted = action == 'enable'
            if current['enabled'] == wanted: return owner.report()
            updated = dict(current, revision=revision + 1, enabled=wanted)
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
        report = owner.report()
        require(report['local_configuration'] == 'ready', 'openrouter_configuration_unavailable')
        require(revision is None or type(revision) is int and revision == record['revision'],
                'openrouter_preparation_stale')
        require(scope_id is None or scope_id == report['scope_id'], 'openrouter_scope_changed')
        expected_root = report['council_root'] if route == 'council' else report['worker_root']
        for actual, expected in ((store, report['proposal_store']), (root, expected_root),
                                 (opencode, record['opencode']), (auth_data, record['auth_data'])):
            if actual is not None:
                require(expected is not None and Path(actual).resolve() == Path(expected),
                        'openrouter_configuration_mismatch')
        state._private(owner.data, True)
        owner.check()
        yield report
