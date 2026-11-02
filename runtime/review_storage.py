"""Explicit machine-private bootstrap and compact, non-enrolling review owners."""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import fcntl
import math
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time
import uuid

from runtime import evidence, state
from runtime.screening import screening_status, reserve_screening, settle_screening
from runtime.startup import (PROJECT_FILE, checkout, json_object, read_git, _canonical_freeze,
                             _canonical_plain, _acquire_canonical_membership, _check_observation_deadline)


MAX_CONFIG = 64 * 1024
MAX_RECORDS = 4096
_ACTIVE_PRODUCER = ContextVar('temporary_storage_producer', default=None)
_PRODUCER_BUDGET = ContextVar('temporary_storage_observation_budget', default=None)
_MACHINE_ACQUISITION = ContextVar('temporary_machine_acquisition', default=None)
_ERRORS = (OSError, state.StartupError, sqlite3.Error, ValueError, TypeError, KeyError,
           RecursionError, subprocess.SubprocessError)


def _machine_config_path():
    return Path.home() / '.config' / 'vibe-coding-workflow' / 'machine.json'


def _require(value, reason='Temporary review storage remains protected'):
    if not value:
        raise state.StateError(reason)


class _HistoryConflict(state.StateError):
    def __init__(self, owners):
        super().__init__('Related temporary accounting requires explicit reconciliation')
        self.owners = owners


def _budget(deadline=None):
    now = time.monotonic()
    _require(deadline is None or type(deadline) in (int, float) and math.isfinite(deadline))
    result = dict(paths=0, bytes=0, deadline=min(deadline, now + 4) if deadline is not None else now + 4)
    _check_observation_deadline(result)
    return result


def _history_protected(owners=None):
    history = dict(complete=owners is not None, owners=[] if owners is None else [
        {key: owner[key] for key in ('owner', 'reference', 'created_at')} for owner in owners])
    return dict(_protected(), temporary_history=history)


def _path(value):
    _require(isinstance(value, (str, Path)))
    path = Path(value)
    state._canonical_path(str(path))
    try:
        _require(len(str(path)) <= 4096 and path.resolve() == path)
    except RuntimeError:
        raise state.StateError('Storage path cannot be resolved safely') from None
    return path


def _parents(path, *, existing=False):
    parents = path.parents
    _require(len(parents) <= 128)
    result = {}
    for parent in parents:
        if existing and not os.path.lexists(parent):
            continue
        info = parent.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not parent.is_symlink())
        result[str(parent)] = evidence._binding(parent)
    _require(len(evidence._raw(result)) <= MAX_CONFIG // 2)
    return result


def _check_parents(parents):
    _require(isinstance(parents, dict) and 0 < len(parents) <= 128)
    for value, binding in parents.items():
        path = _path(value)
        state._canonical_binding(binding)
        _require(path.is_dir() and not path.is_symlink() and evidence._binding(path) == binding)


def _location(value, *, deadline=None):
    path = _path(value)
    _parents(path, existing=True)
    _require(not path.is_relative_to(state.PACKAGE.resolve()))
    for ancestor in (path, *path.parents):
        _require(not os.path.lexists(ancestor / '.git') and
                 not (ancestor.name == 'plugins' and ancestor.parent.name in {'.codex', '.claude'}) and
                 not (ancestor.name == 'cache' and ancestor.parent.name == 'plugins') and
                 not (ancestor.parent.name in {'.agents', '.codex', '.claude', '.cursor'} and
                      ancestor.name in {'skills', 'commands', 'instructions', 'agents'}) and
                 not (ancestor.parent.name == '.cursor' and ancestor.name == 'rules'))
    existing = next(p for p in (path, *path.parents) if p.exists())
    _require(existing.is_dir())
    code, _ = read_git(existing, '-c', 'safe.bareRepository=all', 'rev-parse', '--absolute-git-dir',
                       limit=4096, discovery=True, timeout=2, deadline=deadline)
    _require(code != 0)
    return path


@contextmanager
def _machine_guard(*, create=False, deadline=None):
    path = _path(_machine_config_path())
    original_parents = _parents(path, existing=True)
    if not path.parent.exists():
        if not create:
            yield path, None, None
            return
        _location(path.parent, deadline=deadline)
        state._prepare_directory(path.parent)
    _location(path.parent, deadline=deadline)
    state._private(path.parent, True)
    parents = _parents(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        _require(dict(device=info.st_dev, inode=info.st_ino) == parents[str(path.parent)])
        deadline = min(deadline, time.monotonic() + 5) if deadline is not None else time.monotonic() + 5
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                _require(time.monotonic() < deadline)
                time.sleep(0.01)
        _check_parents(parents)
        _check_parents(original_parents)
        acquisition=dict(path=path,descriptor=descriptor,parents=parents,contexts=[],active=True)
        token = _MACHINE_ACQUISITION.set(acquisition)
        try:
            yield path, descriptor, parents
            _check_parents(parents)
        finally:
            acquisition['active']=False
            _MACHINE_ACQUISITION.reset(token)
    finally:
        os.close(descriptor)


def _configuration(path, descriptor):
    if descriptor is None:
        return None
    try:
        file_descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    except FileNotFoundError:
        return None
    with os.fdopen(file_descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                 and info.st_mode & 0o077 == 0 and info.st_size <= MAX_CONFIG)
        raw = stream.read(MAX_CONFIG + 1)
        current = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        _require((current.st_dev, current.st_ino, current.st_size) == (info.st_dev, info.st_ino, len(raw)))
        _require(stat.S_ISREG(current.st_mode) and current.st_uid == os.getuid()
                 and current.st_nlink == 1 and current.st_mode & 0o077 == 0)
    return json_object(raw, limit=MAX_CONFIG)


def _publish(path, descriptor, parents, value, *, expected):
    raw = evidence._raw(value)
    _require(len(raw) <= MAX_CONFIG and _configuration(path, descriptor) == expected)
    _check_parents(parents)
    name = '.machine-' + uuid.uuid4().hex
    file_descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=descriptor)
    try:
        with os.fdopen(file_descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _check_parents(parents)
        _require(_configuration(path, descriptor) == expected)
        os.replace(name, path.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        try:
            os.unlink(name, dir_fd=descriptor)
        except FileNotFoundError:
            pass


def _ready_context(config, machine_parents, *, deadline=None):
    state._fields(config, ('version', 'state', 'private_data_dir', 'parents', 'machine_parents',
                          'authority', 'provenance', 'owner', 'created_at'))
    _require(type(config['version']) is int and config['version'] == 1 and config['state'] == 'ready'
             and config['machine_parents'] == machine_parents)
    _check_parents(config['parents'])
    _check_parents(config['machine_parents'])
    root = _location(config['private_data_dir'], deadline=deadline)
    state._private(root, True)
    authority = config['authority']
    _require(authority['database'] == str(root / 'review-evidence' / 'runtime.sqlite3')
             and authority['directory'] == str(root / 'review-evidence'))
    _require(config['parents'] == _parents(Path(authority['database'])))
    context = evidence.EvidenceAuthorityContext(Path(authority['directory']), Path(authority['database']),
        _canonical_freeze(authority['directory_binding']), _canonical_freeze(authority['database_binding']),
        (_canonical_freeze(authority),), 'explicit machine-private temporary evidence authority')
    evidence._check_context(context)
    acquired = _MACHINE_ACQUISITION.get()
    if acquired is not None and acquired['parents'] == machine_parents:
        acquired['contexts'].append(context)
    return context


def _provenance_content(authority, parents, owner, created_at, runtime_digest):
    return dict(version=1, kind='temporary-authority-provenance', authority=authority,
                parents=parents, owner=owner, created_at=created_at, runtime_digest=runtime_digest)


def _provenance(config, context, db):
    reference = config['provenance']
    metadata = state._metadata(db, reference['name'])
    _require(metadata is not None and metadata['bytes'] <= MAX_CONFIG)
    content = evidence._read(None, reference, context=context, db=db)
    state._canonical_hash(content['runtime_digest'])
    _require(content == _provenance_content(config['authority'], config['parents'], config['owner'],
                 config['created_at'], content['runtime_digest'])
             and metadata['owner'] == config['owner'] and metadata['created_at'] == config['created_at']
             and config['owner']['created_at'] == config['created_at'] and metadata['class'] == 'compact'
             and metadata['closed_at'] is None and not metadata['targets'] and not metadata['dependencies']
             and metadata['export'] is None)
    for table in state.CANONICAL_TABLE_SQL:
        if table != 'evidence_metadata':
            _require(db.execute('select 1 from ' + table + ' limit 1').fetchone() is None)
    return reference


def _literal_membership(value):
    from runtime import onboarding as d3
    state._fields(value, ('include', 'exclude', 'future_join', 'authorization'))
    for name in ('include', 'exclude'):
        for boundary in d3._strings(value[name], empty=name == 'exclude'):
            if boundary != '.':
                d3._relative(boundary)
    _require(type(value['future_join']) is bool)
    d3._text(value['authorization'])
    return dict(include=sorted(value['include']), exclude=sorted(value['exclude']),
                future_join=value['future_join'], authorization=value['authorization'])


def _target_shape(target):
    state._fields(target, ('path', 'binding', 'parents', 'git'))
    root = state._canonical_path(target['path'])
    state._canonical_binding(target['binding'])
    state._metadata_parents(root, target['parents'])
    _require(len(root.parents) <= 128 and set(target['parents']) == {str(p) for p in root.parents})
    if target['git'] is not None:
        git = target['git']
        state._fields(git, ('root', 'primary_root', 'git_dir', 'git_common_dir', 'binding'))
        state._fields(git['binding'], ('root', 'primary_root', 'git_dir', 'git_common_dir'))
        _require(git['root'] == git['primary_root'] == str(root)
                 and git['git_dir'] == git['git_common_dir'] == str(root / '.git')
                 and target['binding'] == git['binding']['root'] == git['binding']['primary_root']
                 and git['binding']['git_dir'] == git['binding']['git_common_dir'])
        for binding in git['binding'].values():
            state._canonical_binding(binding)
    return root


def _owner_targets(content):
    """One strict content shape is shared by the real producer and immutable reader."""
    _require(type(content['version']) is int and content['version'] == 1)
    if content['kind'] == 'temporary-target-owner':
        state._fields(content, ('version', 'kind', 'owner', 'created_at', 'target', 'provenance'))
        _target_shape(content['target'])
        _require(content['target']['git'] is not None)
        return [content['target']]
    _require(content['kind'] == 'temporary-project-owner')
    state._fields(content, ('version', 'kind', 'owner', 'created_at', 'root', 'membership', 'members', 'provenance'))
    root = _target_shape(content['root'])
    _require(content['root']['git'] is None and _literal_membership(content['membership']) == content['membership']
             and isinstance(content['members'], list) and 0 < len(content['members']) < MAX_RECORDS)
    paths, commons = [], []
    for target in content['members']:
        member = _target_shape(target)
        _require(target['git'] is not None and member != root and member.is_relative_to(root)
                 and any(member.is_relative_to(root / name) for name in content['membership']['include'])
                 and not any(member.is_relative_to(root / name) for name in content['membership']['exclude']))
        paths.append(str(member))
        commons.append(tuple(sorted(target['git']['binding']['git_common_dir'].items())))
    _require(paths == sorted(set(paths)) and len(set(commons)) == len(commons))
    return [content['root'], *content['members']]


def _check_producer(context, db, owner):
    """Only the active real producer can register its exact observed compact facts."""
    _require(_ACTIVE_PRODUCER.get() is owner and owner.db is db and owner.context == context
             and db.in_transaction and state._frozen_snapshot(owner.content) and state._frozen_snapshot(owner.targets))
    content, targets = _canonical_plain(owner.content), _canonical_plain(owner.targets)
    metadata_owner = content['owner']
    state._fields(metadata_owner, ('kind', 'id', 'created_at'))
    _require(metadata_owner['kind'] == 'temporary' and metadata_owner['created_at'] == owner.created_at)
    state._integer(owner.created_at, 'original temporary creation time')
    if owner.creation is not None:
        state._check_creation(context.database, owner.creation, db)
        _require(not os.path.lexists(context.directory / 'evidence'))
        _require(not targets and owner.created_at == owner.creation.created_at
                 and content == _provenance_content(_canonical_plain(owner.creation.authority),
                     _parents(context.database), metadata_owner, owner.created_at, state.runtime_fingerprint()))
        for table in state.CANONICAL_TABLE_SQL:
            _require(db.execute('select 1 from ' + table + ' limit 1').fetchone() is None)
    else:
        _require(_owner_targets(content) == targets and content['created_at'] == owner.created_at)
        budget = _PRODUCER_BUDGET.get() or _budget()
        machine = _path(_machine_config_path())
        descriptor = os.open(machine.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            config = _configuration(machine, descriptor)
            _require(config is not None and _ready_context(config, _parents(machine), deadline=budget['deadline']) == context)
            _require(content['provenance'] == _provenance(config, context, db))
            _storage_outside(context, machine, targets)
        finally:
            os.close(descriptor)
        if content['kind'] == 'temporary-project-owner':
            observed = _observe_temporary_project(content['root']['path'],
                [target['path'] for target in content['members']], content['membership'], budget)
            _require(observed == {key: content[key] for key in ('root', 'membership', 'members')})
        else:
            _require(_observe_target(Path(targets[0]['path']), budget=budget) == targets[0])
        from runtime.screening import _check_birth
        _check_birth(context, db, targets, budget)
    return metadata_owner


def _register(context, db, content, targets, *, creation=None, budget=None):
    _require(len(evidence._raw(content)) <= MAX_CONFIG)
    owner = evidence._TemporaryOwner(context, db, _canonical_freeze(content), _canonical_freeze(targets),
                                     content['created_at'], creation)
    token = _ACTIVE_PRODUCER.set(owner)
    observation_token = _PRODUCER_BUDGET.set(budget)
    try:
        return evidence._artifact(None, content, context=context, owner=owner, db=db)
    finally:
        _PRODUCER_BUDGET.reset(observation_token)
        _ACTIVE_PRODUCER.reset(token)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ready_report(config):
    return dict(ok=True, status='ready', private_data_dir=config['private_data_dir'])


def _protected():
    return dict(ok=False, status='protected',
                reason='Review storage or target is interrupted, unsafe, incompatible or outside this supported slice')


def _pending():
    return dict(ok=False, status='storage_pending')


def storage_status():
    """Inspect existing selection and original provenance without creating anything."""
    try:
        with _machine_guard() as (path, descriptor, parents):
            config = _configuration(path, descriptor)
            if config is None:
                return _pending()
            context = _ready_context(config, parents)
            with evidence._context_database(context) as db:
                _provenance(config, context, db)
            return _ready_report(config)
    except _ERRORS:
        return _protected()


def prepare_storage(private_data_dir):
    """Select once; durable initializing intent prevents automatic bootstrap retries."""
    creation = None
    try:
        root = _location(private_data_dir)
        machine = _path(_machine_config_path())
        _require(not root.is_relative_to(machine.parent) and not machine.parent.is_relative_to(root))
        with _machine_guard(create=True) as (path, descriptor, parents):
            config = _configuration(path, descriptor)
            if config is not None:
                _require(config.get('private_data_dir') == str(root))
                context = _ready_context(config, parents)
                with evidence._context_database(context) as db:
                    _provenance(config, context, db)
                return _ready_report(config)
            directory = root / 'review-evidence'
            _require(not os.path.lexists(directory))
            if root.exists():
                state._private(root, True)
            intent = dict(version=1, state='initializing', private_data_dir=str(root),
                          parents=_parents(directory, existing=True), machine_parents=parents)
            for parent in parents:
                _check_parents(parents)
                _sync_directory(Path(parent))
                _check_parents(parents)
            _publish(path, descriptor, parents, intent, expected=None)
            _check_parents(intent['parents'])
            state._prepare_directory(directory)
            creation = state._initialize(directory / 'runtime.sqlite3', version=3,
                                         expected_directory=evidence._binding(directory))
            state._check_creation(directory / 'runtime.sqlite3', creation)
            authority = _canonical_plain(creation.authority)
            context = evidence.EvidenceAuthorityContext(directory, directory / 'runtime.sqlite3',
                _canonical_freeze(authority['directory_binding']), _canonical_freeze(authority['database_binding']),
                (_canonical_freeze(authority),), 'fresh machine-private temporary evidence authority')
            original = dict(kind='temporary', id=uuid.uuid4().hex, created_at=creation.created_at)
            physical_parents = _parents(context.database)
            content = _provenance_content(authority, physical_parents, original, creation.created_at,
                                          state.runtime_fingerprint())
            with evidence._context_database(context, write=True, durable=True) as db:
                reference = _register(context, db, content, [], creation=creation)
                state._check_creation(context.database, creation, db)
            os.fsync(creation.descriptor)
            _check_parents(intent['parents'])
            _check_parents(physical_parents)
            for parent in physical_parents:
                _sync_directory(Path(parent))
            ready = dict(intent, state='ready', parents=physical_parents, authority=authority,
                         provenance=reference, owner=original, created_at=creation.created_at)
            _publish(path, descriptor, parents, ready, expected=intent)
            return _ready_report(ready)
    except _ERRORS:
        return _protected()
    finally:
        if creation is not None:
            os.close(creation.descriptor)


def _observe_target(target, *, budget=None):
    from runtime.onboarding import _checkout_observation
    budget = budget if budget is not None else _budget()
    _check_observation_deadline(budget)
    _require(isinstance(target, (str, Path)) and len(str(target)) <= 4096)
    try:
        selected = Path(target).resolve(strict=True)
    except RuntimeError:
        raise state.StateError('Review target cannot be resolved safely') from None
    if selected.is_file():
        selected = selected.parent
    _require(selected.is_dir())
    identity = checkout(selected, limit=64 * 1024, deadline=budget['deadline'])
    _require(identity is not None)
    root, common = identity
    _require(root / '.git' == common and (root / '.git').is_dir() and not (root / '.git').is_symlink())
    for ancestor in (selected, *selected.parents):
        _require(not os.path.lexists(ancestor / PROJECT_FILE.parent))
        if ancestor != root and not ancestor.is_relative_to(root):
            _require(not os.path.lexists(ancestor / '.git'))
    worktrees = common / 'worktrees'
    _require(not os.path.lexists(worktrees) or worktrees.is_dir() and next(worktrees.iterdir(), None) is None)
    observed, error = _checkout_observation(str(root), budget)
    _require(error is None and observed['primary_root'] == str(root) and observed['git_dir'] == str(common))
    git = {key: observed[key] for key in ('root', 'primary_root', 'git_dir', 'git_common_dir', 'binding')}
    return dict(path=str(root), binding=evidence._binding(root), parents=_parents(root), git=git)


def _observe_temporary_project(root, members, membership, budget):
    from runtime import onboarding as d3
    _check_observation_deadline(budget)
    root = _path(root)
    d3._no_links(root)
    _require(root.is_dir())
    membership = _literal_membership(membership)
    for name in (*membership['include'], *membership['exclude']):
        boundary = root / name
        d3._no_links(boundary)
        _require(boundary.resolve().is_relative_to(root))
    for ancestor in (root, *root.parents):
        _require(not os.path.lexists(ancestor / PROJECT_FILE.parent))
    code, _ = read_git(root, '-c', 'safe.bareRepository=all', 'rev-parse', '--absolute-git-dir',
                       limit=4096, discovery=True, deadline=budget['deadline'])
    _require(code != 0)
    target = dict(path=str(root), binding=evidence._binding(root), parents=_parents(root), git=None)
    budget['paths'] += len(target['parents']) + 1
    _require(budget['paths'] <= d3.MAX_PATHS)
    selected = []
    for member in sorted(members):
        _check_observation_deadline(budget)
        member = _path(member)
        _require(member != root and member.is_relative_to(root)
                 and any(member.is_relative_to(root / name) for name in membership['include'])
                 and not any(member.is_relative_to(root / name) for name in membership['exclude']))
        observed = _observe_target(member, budget=budget)
        _require(observed['path'] == str(member))
        selected.append(observed)
    result = dict(root=target, members=selected, membership=membership)
    _owner_targets(dict(version=1, kind='temporary-project-owner', owner={}, created_at=0,
                        provenance={}, **result))
    _check_observation_deadline(budget)
    return result


def _owners(config, context, db, *, budget=None):
    _provenance(config, context, db)
    rows = db.execute('select name,digest from evidence_metadata order by name limit 4097').fetchall()
    _require(len(rows) <= MAX_RECORDS)
    names = {row[0] for row in rows}
    folder = context.directory / 'evidence'
    state._private(folder, True)
    with os.scandir(folder) as entries:
        actual = set()
        for entry in entries:
            actual.add(entry.name)
            _require(len(actual) <= MAX_RECORDS)
    _require(actual == names)
    total = 0
    owners = []
    for name, digest in rows:
        if budget is not None:
            _check_observation_deadline(budget)
        if name == config['provenance']['name']:
            continue
        reference = dict(name=name, digest=digest)
        metadata = state._metadata(db, name)
        total += metadata['bytes']
        _require(metadata['bytes'] <= MAX_CONFIG and total <= evidence.MAX_ARTIFACT)
        content = evidence._read(None, reference, context=context, db=db)
        targets = _owner_targets(content)
        _require(content['provenance'] == config['provenance']
                 and metadata['owner']['kind'] == 'temporary' and metadata['owner'] == content['owner']
                 and metadata['created_at'] == content['created_at'] == content['owner']['created_at']
                 and metadata['class'] == 'compact' and metadata['targets'] == targets
                 and not metadata['dependencies'] and metadata['closed_at'] is None and metadata['export'] is None)
        if budget is not None:
            from runtime.onboarding import MAX_TOTAL
            if evidence._CAP_INPUT_BUDGET.get() is not budget:
                budget['bytes'] += metadata['bytes']
            _require(budget['bytes'] <= MAX_TOTAL)
        details = dict(target=targets[0], targets=targets, kind=content['kind'])
        if content['kind'] == 'temporary-project-owner':
            details.update(membership=content['membership'], members=content['members'])
        owners.append(dict(owner=content['owner'], reference=reference, created_at=content['created_at'],
                           bytes=metadata['bytes'], **details))
    return sorted(owners, key=lambda owner: (owner['created_at'], owner['target']['path']))


def _related(left, right):
    root, other = Path(left['path']), Path(right['path'])
    return (root.is_relative_to(other) or other.is_relative_to(root) or left['binding'] == right['binding']
            or left['git'] is not None and right['git'] is not None
            and left['git']['binding']['git_common_dir'] == right['git']['binding']['git_common_dir'])


def _related_owners(owners, targets, budget=None):
    related = []
    for owner in owners:
        if budget is not None:
            _check_observation_deadline(budget)
        if any(_related(original, target) for original in owner['targets'] for target in targets):
            related.append(owner)
    return related


def _reobserve_project_owner(owner, budget):
    expected = dict(root=owner['target'], members=owner['members'], membership=owner['membership'])
    _require(_observe_temporary_project(owner['target']['path'],
        [target['path'] for target in owner['members']], owner['membership'], budget) == expected)


def _existing_owner(owners, target, *, budget=None):
    related = _related_owners(owners, [target], budget)
    if not related:
        return None
    if len(related) != 1:
        raise _HistoryConflict(related)
    owner = related[0]
    if owner['kind'] == 'temporary-project-owner':
        if target not in owner['members']:
            raise _HistoryConflict(related)
        try:
            _reobserve_project_owner(owner, budget if budget is not None else _budget())
        except (*_ERRORS, RuntimeError):
            raise _HistoryConflict(related) from None
    elif owner['target'] != target:
        raise _HistoryConflict(related)
    return owner


def _resolved(owner):
    return dict(ok=True, status='resolved', **{key: owner[key] for key in ('owner', 'reference', 'created_at')})


def _enrolled_selection(acquired, budget):
    """Bind included Repository accounting to its existing configured Project store."""
    _require(acquired.status == 'observed')
    data = _canonical_plain(acquired.record.data)
    observed = _canonical_plain(acquired.observation)
    target = _canonical_plain(acquired.authority_target)
    project = Path(data['project_root'])
    _require(acquired.authority_path == project / PROJECT_FILE
        and target['path'] == str(acquired.authority_path)
        and target['fingerprint']['status'] == 'read'
        and target['parents'].get(str(project)) == data['project_binding']
        and observed['project_root'] == str(project)
        and observed['project_binding'] == data['project_binding']
        and observed['membership'] == 'included')
    directories = {str(project): data['project_binding']}
    roots = {str(project), observed['root'], observed['primary_root'], observed['git_dir'], observed['git_common_dir']}
    for repo in data['repositories']:
        entries = [(repo[key], binding) for key, binding in repo['binding'].items()]
        entries += [(selected[key], binding) for selected in repo['checkouts']
                    for key, binding in selected['binding'].items()]
        for path, binding in entries:
            _require(path not in directories or directories[path] == binding)
            directories[path] = binding
            roots.add(path)
    for key, binding in observed['binding'].items():
        path = observed[key]
        _require(path not in directories or directories[path] == binding)
        directories[path] = binding
    for path, binding in target['parents'].items():
        _require(path not in directories or directories[path] == binding)
    matches = [repo for repo in data['repositories'] if repo['primary_root'] == observed['primary_root']]
    _require(len(matches) == 1)
    repo = matches[0]
    _require(repo['membership'] == 'joined' and repo['git_common_dir'] == observed['git_common_dir']
        and all(observed['binding'][key] == binding for key, binding in repo['binding'].items()))
    selected = next((entry for entry in repo['checkouts'] if entry['root'] == observed['root']), None)
    if selected is not None:
        _require(selected['git_dir'] == observed['git_dir']
            and all(observed['binding'][key] == binding for key, binding in selected['binding'].items()))
    else:
        _require(not any(entry['binding']['root'] == observed['binding']['root'] for entry in repo['checkouts']))
    stores, selected_stores = set(), set()
    for member in data['repositories']:
        if member['membership'] != 'joined':
            continue
        for entry in member['checkouts']:
            for client in entry['clients']:
                config = client['configuration']
                if config is not None and config['runtime'] is not None:
                    runtime = config['runtime']
                    _require(runtime['schema_version'] == 3)
                    stores.add(runtime['state_dir'])
                    if member is repo:
                        selected_stores.add(runtime['state_dir'])
    _require(len(stores) == 1 and selected_stores == stores)
    directory = _location(next(iter(stores)), deadline=budget['deadline'])
    _require(not any(directory.is_relative_to(Path(root)) or Path(root).is_relative_to(directory) for root in roots))
    state._private(directory, True)
    database = directory / 'runtime.sqlite3'
    state._sidecars(database)
    authority = state._authority_identity(database)
    context = evidence.EvidenceAuthorityContext(directory, database,
        _canonical_freeze(authority['directory_binding']), _canonical_freeze(authority['database_binding']),
        (_canonical_freeze(authority),), 'observed enrolled Project accounting; read only')
    evidence._check_context(context)
    _check_observation_deadline(budget)
    return data, observed, authority, context


def _enrolled_baseline(context, project_id, budget):
    _check_observation_deadline(budget)
    with evidence._context_database(context) as db:
        baseline = state._source_baseline(None, db, context=context)
        _require(baseline is not None and baseline['content']['project_id'] == project_id)
    _check_observation_deadline(budget)
    return baseline


def _record_targets(data):
    """Keep sibling history visible even when only one checkout was selected."""
    targets = [dict(path=data['project_root'], binding=data['project_binding'], git=None)]
    for repository in data['repositories']:
        for selected in repository['checkouts']:
            git = {key: repository[key] for key in ('primary_root', 'git_common_dir')}
            git.update({key: selected[key] for key in ('root', 'git_dir')})
            git['binding'] = repository['binding'] | selected['binding']
            targets.append(dict(path=selected['root'], binding=selected['binding']['root'], git=git))
    return targets


def _birth_history_snapshot(config, context, db, targets, budget):
    """Read full owner metadata; summaries cannot preserve mutable screening charges."""
    from runtime import onboarding as d3
    acquired = _MACHINE_ACQUISITION.get()
    _require(acquired is not None and any(item is context for item in acquired['contexts']))
    owners = _owners(config, context, db, budget=budget)
    related = _related_owners(owners, targets, budget)
    allowed = {target['path'] for target in targets}
    _require(all(target['path'] in allowed for owner in related for target in owner['targets']))
    inventory, sources = [], []
    for owner in owners:
        metadata, _ = evidence._managed_artifact(None, owner['reference'], context=context, db=db)
        inventory.append(dict(reference=owner['reference'], metadata=metadata))
        pointer = metadata.get('screening_result')
        if owner in related and pointer is not None:
            state._screening_record(metadata['screening']); state._screening_result(pointer)
            payload = d3._read_onboarding_screening_source(pointer, budget=budget)
            _require(payload['accounting'] == dict(authority=config['authority'], owner=owner['owner'],
                reference=owner['reference'], created_at=owner['created_at'])
                and payload['ticket'] == metadata['screening']['last_receipt']['ticket']
                and payload['repositories'] == [target for target in metadata['targets'] if target['git'] is not None])
            sources.append(dict(owner_reference=owner['reference'], pointer=pointer,
                payload_digest=d3._digest(payload)))
    machine = acquired['path']
    return dict(version=1, machine=dict(path=str(machine), binding=evidence._binding(machine),
        parents=acquired['parents'], configuration=config,
        provenance_metadata=state._metadata(db, config['provenance']['name'])),
        inventory=sorted(inventory, key=lambda row: evidence._raw(row['reference'])),
        related=sorted([owner['reference'] for owner in related], key=evidence._raw),
        screening_sources=sorted(sources, key=lambda row: evidence._raw(row['owner_reference'])))


def _birth_history_shape(value):
    state._fields(value, ('version', 'machine', 'inventory', 'related', 'screening_sources'))
    _require(type(value['version']) is int and value['version'] == 1)
    state._fields(value['machine'], ('path', 'binding', 'parents', 'configuration', 'provenance_metadata'))
    _path(value['machine']['path']); state._canonical_binding(value['machine']['binding'])
    state._fields(value['machine']['configuration'], ('version','state','private_data_dir','parents','machine_parents','authority','provenance','owner','created_at'))
    _require(value['machine']['configuration']['state']=='ready')
    for path,binding in value['machine']['parents'].items():_path(path);state._canonical_binding(binding)
    for name in ('inventory', 'related', 'screening_sources'):
        _require(isinstance(value[name], list) and len(value[name]) <= MAX_RECORDS)
    refs = []
    for row in value['inventory']:
        state._fields(row, ('reference', 'metadata')); state._artifact_reference(row['reference'])
        metadata=row['metadata']
        _require(metadata['owner']['kind'] == 'temporary')
        state._metadata_record((row['reference']['name'],row['reference']['digest'],'temporary:'+metadata['owner']['id'],
            metadata['class'],metadata['created_at'],metadata['closed_at'],metadata['status'],metadata['bytes'],evidence._raw(metadata).decode()))
        if 'screening' in row['metadata']: state._screening_record(row['metadata']['screening'])
        if 'screening_result' in row['metadata']: state._screening_result(row['metadata']['screening_result'])
        refs.append(row['reference'])
    _require(len({evidence._raw(ref) for ref in refs}) == len(refs)
        and all(ref in refs for ref in value['related']))
    for row in value['screening_sources']:
        state._fields(row, ('owner_reference', 'pointer', 'payload_digest'))
        _require(row['owner_reference'] in value['related'])
        state._screening_result(row['pointer']); state._canonical_hash(row['payload_digest'])
    _require(len(evidence._raw(value)) <= evidence.MAX_ARTIFACT)
    return value


@contextmanager
def _capture_birth_history(record):
    """A D4-only live acquisition; it does not prepare missing machine storage."""
    from runtime import onboarding as d3
    budget = _budget()
    targets = _record_targets(record)
    active = True
    with ExitStack() as guards:
        acquired = _MACHINE_ACQUISITION.get()
        if acquired is None:
            path, descriptor, parents = guards.enter_context(_machine_guard(deadline=budget['deadline']))
        else:
            _require(acquired.get('active') is True, 'Birth history requires a live machine acquisition')
            path, descriptor, parents = acquired['path'], acquired['descriptor'], acquired['parents']
            info=os.fstat(descriptor)
            _require(stat.S_ISDIR(info.st_mode) and dict(device=info.st_dev,inode=info.st_ino)==parents[str(path.parent)])
            _require(_path(_machine_config_path()) == path); _check_parents(parents)
        snapshot = _configuration_snapshot(path, descriptor); config = snapshot[0]
        _require(config is not None, 'Canonical birth needs ready machine-private history')
        machine = _MACHINE_ACQUISITION.get()
        def observe():
            _require(active and machine.get('active') is True and _MACHINE_ACQUISITION.get() is machine
                and _configuration_snapshot(path, descriptor) == snapshot)
            context = _ready_context(config, parents, deadline=budget['deadline'])
            _storage_outside(context, path, targets)
            _require(evidence._binding(Path(record['project_root'])) == record['project_binding'])
            for repository in record['repositories']:
                for selected in repository['checkouts']:
                    observed, error = d3._checkout_observation(selected['root'], budget)
                    expected = dict(root=selected['root'], primary_root=repository['primary_root'],
                        git_dir=selected['git_dir'], git_common_dir=repository['git_common_dir'],
                        binding=repository['binding'] | selected['binding'])
                    _require(error is None and all(observed[key] == value for key, value in expected.items()))
            with evidence._context_database(context) as db:
                return _birth_history_snapshot(config, context, db, targets, budget)
        history = observe(); _birth_history_shape(history)
        def reobserve():
            _require(observe() == history, 'Canonical birth history changed')
        try: yield history, reobserve
        finally: active = False


def _retained_birth_history(certificate):
    """Reobserve the original physical scope without creating an accepted task scope."""
    intent=certificate['intent']; repositories={}
    for member in intent['members']:
        physical=member['physical_identity']; key=member['repository_id']
        repository=repositories.setdefault(key,dict(repository_id=key,primary_root=physical['primary_root'],
            git_common_dir=physical['git_common_dir'],binding={name:physical['bindings'][name] for name in ('primary_root','git_common_dir')},checkouts=[]))
        repository['checkouts'].append(dict(checkout_id=member['checkout_id'],root=physical['repository_root'],git_dir=physical['git_dir'],
            binding={name:physical['bindings'][name] for name in ('root','git_dir')}))
    physical=intent['members'][0]['physical_identity']
    record=dict(project_root=physical['project_root'],project_binding=physical['project_binding'],repositories=list(repositories.values()))
    # Original related owners can span retained peers outside this store's seed members.
    targets={target['path']:target for target in _record_targets(record)}
    related={evidence._raw(reference) for reference in intent['history']['related']}
    inventory={evidence._raw(row['reference']):row['metadata'] for row in intent['history']['inventory']}
    _require(len(related)<=MAX_RECORDS and related<=inventory.keys())
    for reference in sorted(related):
        for target in inventory[reference]['targets']:
            location=_target_shape(target)
            observed={key:target[key] for key in ('path','binding','git')}
            if target['path'] in targets:
                previous=targets[target['path']]
                _require(previous['binding']==observed['binding'])
                if previous['git']==observed['git']:continue
                _require(location==Path(record['project_root']) and
                    (previous['git'] is None or observed['git'] is None))
                if observed['git'] is None:continue
            git=target['git']
            _require(git is not None and location.is_relative_to(Path(record['project_root']))
                and (target['path'] in targets or len(targets)<MAX_RECORDS))
            targets[target['path']]=observed
            record['repositories'].append(dict(primary_root=git['primary_root'],git_common_dir=git['git_common_dir'],
                binding={key:git['binding'][key] for key in ('primary_root','git_common_dir')},
                checkouts=[dict(root=git['root'],git_dir=git['git_dir'],
                    binding={key:git['binding'][key] for key in ('root','git_dir')})]))
    bindings={}
    for target in targets.values():
        observations=[(target['path'],target['binding'])]
        if target['git'] is not None:
            observations.extend((target['git'][key],binding) for key,binding in target['git']['binding'].items())
        for path,binding in observations:
            _require(path not in bindings or bindings[path]==binding)
            bindings[path]=binding
    with _capture_birth_history(record) as (actual,reobserve):
        expected=intent['history']; _require(actual['machine']==expected['machine'] and actual['related']==expected['related'])
        current={evidence._raw(row['reference']):row for row in actual['inventory']}
        old={evidence._raw(row['reference']):row for row in expected['inventory']}
        _require(all(current.get(evidence._raw(ref))==old[evidence._raw(ref)] for ref in expected['related'])
            and actual['screening_sources']==expected['screening_sources'])
        reobserve()


def _enrolled_history(config, context, observed, budget, *, data=None):
    def canonical_observation():
        _check_observation_deadline(budget)
        acquired = _acquire_canonical_membership(Path(observed['root']), authority_path=None, budget=budget)
        selected = _enrolled_selection(acquired, budget)
        canonical = selected[3]
        with evidence._context_database(canonical) as reader:
            baseline = state._source_baseline(None, reader, context=canonical)
            _require(baseline is not None)
            handoff = evidence._read_birth_handoff(canonical, reader)
        _check_observation_deadline(budget)
        return acquired, selected, baseline, handoff

    # Resolve expected history even when deleting the final owner made current inventory empty.
    original = canonical_observation()
    baseline, handoff = original[2:]
    with evidence._context_database(context) as db:
        owners = _owners(config, context, db, budget=budget)
        targets = [dict(path=observed['root'], binding=observed['binding']['root'], git=observed)]
        if data is not None:
            targets.extend(_record_targets(data))
        related = _related_owners(owners, targets, budget)
        _require(data is not None)
        if baseline['content'].get('birth_expectation') is not None:
            _require(handoff is not None)
            expected = handoff['certificate']['intent']['history']
            actual = _birth_history_snapshot(config,context,db,targets,budget)
            _require(actual['machine']==expected['machine'] and actual['related']==expected['related'])
            current = {evidence._raw(row['reference']):row for row in actual['inventory']}
            previous = {evidence._raw(row['reference']):row for row in expected['inventory']}
            _require(all(current.get(evidence._raw(ref))==previous[evidence._raw(ref)] for ref in expected['related'])
                and actual['screening_sources']==expected['screening_sources'])
        elif related:
            raise _HistoryConflict(related)
    # A serial gap cannot silently adopt a changed authority, baseline or handoff.
    _require(canonical_observation() == original, 'Canonical birth history changed during temporary observation')
    _check_observation_deadline(budget)
    return owners


def _storage_outside(context, path, targets):
    roots = {target['path'] for target in targets}
    roots.update(target['git'][key] for target in targets if target['git'] is not None
                 for key in ('root', 'primary_root', 'git_dir', 'git_common_dir'))
    _require(not any(context.directory.is_relative_to(Path(root))
        or Path(root).is_relative_to(context.directory) or path.is_relative_to(Path(root)) for root in roots))


def _configuration_snapshot(path, descriptor):
    config = _configuration(path, descriptor)
    return config, evidence._binding(path) if config is not None else None


def _enrolled_control_members(data, budget):
    """Observe historical entries without turning excluded or absent authority into inclusion."""
    from runtime import onboarding as d3
    project = Path(data['project_root'])
    result = []
    for repository in data['repositories']:
        primary = Path(repository['primary_root'])
        included = primary.is_relative_to(project) and any(
            primary.is_relative_to(project / name) for name in data['membership']['include'])
        excluded = any(primary.is_relative_to(project / name) for name in data['membership']['exclude'])
        membership = 'excluded' if excluded else 'included' if included else 'outside'
        for checkout_record in repository['checkouts']:
            _check_observation_deadline(budget)
            acquired = _acquire_canonical_membership(Path(checkout_record['root']), authority_path=None, budget=budget)
            _require(acquired.status in {'observed', 'outside'})
            if acquired.status == 'observed':
                observed = _canonical_plain(acquired.observation)
                current = _canonical_plain(acquired.record.data)
                if current == data:
                    _require(acquired.authority_path == project / PROJECT_FILE
                        and observed['project_root'] == str(project)
                        and observed['project_binding'] == data['project_binding']
                        and observed['membership'] == membership)
                else:
                    _require(repository['membership'] == 'removed' or membership != 'included')
            else:
                _require(repository['membership'] == 'removed' or membership != 'included')
                observed, error = d3._checkout_observation(checkout_record['root'], budget)
                _require(error is None)
            expected = dict(root=checkout_record['root'], primary_root=repository['primary_root'],
                git_dir=checkout_record['git_dir'], git_common_dir=repository['git_common_dir'],
                binding=repository['binding'] | checkout_record['binding'])
            _require({key: observed[key] for key in expected} == expected)
            result.append((acquired, _canonical_freeze(expected)))
    _check_observation_deadline(budget)
    return tuple(result)


def _enrolled_result(selected, acquisitions, budget, *, scope=None, control=None):
    selections = [_enrolled_selection(acquired, budget) for acquired in acquisitions]
    data, observed, authority, context = selections[0]
    _require(all((entry[0], entry[2], entry[3]) == (data, authority, context) for entry in selections))
    if scope is not None:
        membership = dict(data['membership'], authorization=data['membership']['authorization']['reference'])
        _require(scope['kind'] == data['kind'] and scope['project_root'] == data['project_root']
                 and _literal_membership(scope['membership']) == _literal_membership(membership)
                 and scope['authority']['path'] == str(acquisitions[0].authority_path)
                 and scope['authority']['sha256'] == acquisitions[0].authority_target['fingerprint']['sha256'])
        for member, entry, acquired in zip(selected, selections, acquisitions):
            _require(entry[1]['root'] == str(member)
                and acquired.authority_path == acquisitions[0].authority_path
                and acquired.authority_target == acquisitions[0].authority_target)
            _require(any(repository['primary_root'] == entry[1]['primary_root']
                and any(checkout['root'] == str(member) for checkout in repository['checkouts'])
                for repository in data['repositories']))
        _require(scope['kind'] != 'standalone' or observed['primary_root'] == scope['project_root'])
        common_bindings = [tuple(sorted(entry[1]['binding']['git_common_dir'].items())) for entry in selections]
        _require(len(set(common_bindings)) == len(common_bindings))
    baseline = _enrolled_baseline(context, data['project_id'], budget)
    with _machine_guard(deadline=budget['deadline']) as (path, descriptor, parents):
        snapshot = _configuration_snapshot(path, descriptor)
        config = snapshot[0]
        if baseline['content'].get('birth_expectation') is not None:
            _require(config is not None, 'Original birth history needs ready machine-private storage')
        history = dict(complete=False, reason='machine_configuration_absent')
        temporary_context = owners = None
        if config is not None:
            temporary_context = _ready_context(config, parents, deadline=budget['deadline'])
            _storage_outside(temporary_context, path, _record_targets(data))
            owners = _enrolled_history(config, temporary_context, observed, budget, data=data)
            retained = _related_owners(owners, _record_targets(data), budget)
            history = dict(complete=True, reason='authenticated_retained_history' if retained else 'authenticated_no_related_history')
            if scope is not None or retained or baseline['content'].get('birth_expectation') is not None:
                history['owners']=[{key:owner[key] for key in ('owner','reference','created_at')} for owner in retained]
            if baseline['content'].get('birth_expectation') is not None:
                with evidence._context_database(context) as reader: handoff=evidence._read_birth_handoff(context,reader)
                history['handoff']=dict(source=handoff['source'],certificate_digest=evidence._digest(handoff['certificate']))
        for member, acquired in zip(selected, acquisitions):
            _require(_acquire_canonical_membership(member, authority_path=None, budget=budget) == acquired)
        # Reopen after the first transaction ends so committed baseline changes are visible.
        _require(_enrolled_baseline(context, data['project_id'], budget) == baseline)
        _require(_configuration_snapshot(path, descriptor) == snapshot
            and (config is not None or not os.path.lexists(path)))
        if config is not None:
            _require(_ready_context(config, parents, deadline=budget['deadline']) == temporary_context)
            _require(_enrolled_history(config, temporary_context, observed, budget, data=data) == owners)
        _check_observation_deadline(budget)
        checkouts = [{key: entry[1][key] for key in
            ('root', 'primary_root', 'git_dir', 'git_common_dir', 'binding')} for entry in selections]
        result = dict(ok=True, status='resolved', owner=dict(kind='project', project_id=data['project_id'], task=None),
            reference=_canonical_plain(baseline['reference']), created_at=baseline['content']['created_at'],
            authority=authority, checkout=checkouts[0], age_basis='authority_provenance', temporary_history=history)
        if scope is not None:
            result['checkouts'] = checkouts
        if control is not None:
            _require(config is not None and temporary_context is not None and owners is not None)
            machine = _MACHINE_ACQUISITION.get()
            _require(machine is not None)
            original_parents = _canonical_freeze(parents)
            members = _enrolled_control_members(data, budget)
            active = True

            def reobserve():
                # A later guard, even over identical paths, cannot revive this acquisition.
                _require(active and _MACHINE_ACQUISITION.get() is machine
                    and machine['path'] == path and machine['descriptor'] == descriptor
                    and machine['parents'] == _canonical_plain(original_parents)
                    and _path(_machine_config_path()) == path
                    and any(item is temporary_context for item in machine['contexts']))
                info = os.fstat(descriptor)
                _require(stat.S_ISDIR(info.st_mode) and dict(device=info.st_dev, inode=info.st_ino)
                    == original_parents[str(path.parent)])
                _check_observation_deadline(budget)
                _check_parents(_canonical_plain(original_parents))
                _require(_configuration_snapshot(path, descriptor) == snapshot
                    and _ready_context(config, parents, deadline=budget['deadline']) == temporary_context)
                current = [_acquire_canonical_membership(member, authority_path=None, budget=budget)
                           for member in selected]
                _require(current == acquisitions)
                _require([_enrolled_selection(acquired, budget) for acquired in current] == selections)
                _require(_enrolled_control_members(data, budget) == members)
                _require(_enrolled_baseline(context, data['project_id'], budget) == baseline)
                _require(_enrolled_history(config, temporary_context, observed, budget, data=data) == owners)
                _check_parents(_canonical_plain(original_parents))
                _require(_configuration_snapshot(path, descriptor) == snapshot)
                _check_observation_deadline(budget)

            try:
                reobserve()
                # E1 must call full reobserve outside its writer; no writer-local witness is issued here.
                return control.access(project=_canonical_freeze(data), config=_canonical_freeze(config),
                    project_context=context, temporary_context=temporary_context,
                    accounting=_canonical_freeze(result), reobserve=reobserve, budget=budget)
            finally:
                # Copied ContextVars cannot revive a returned callback when a descriptor is reused.
                active = False
    return result


def acquire_enrolled_accounting(target, *, deadline=None, budget=None):
    """Resolve existing Project accounting facts without activation, setup or writes."""
    return _acquire_enrolled_accounting(target, deadline=deadline, budget=budget)


def _acquire_enrolled_accounting(target, *, deadline=None, budget=None, control=None):
    """Internal enrolled-only control path; the public wrapper always remains read-only."""
    try:
        budget = budget if budget is not None else _budget(deadline)
        _require(isinstance(target, (str, Path)) and len(str(target)) <= 4096)
        selected = Path(target).resolve(strict=True)
        if selected.is_file():
            selected = selected.parent
        _require(selected.is_dir())
        acquired = _acquire_canonical_membership(selected, authority_path=None, budget=budget)
        if acquired.status == 'outside':
            return dict(ok=False, status='outside', reason=acquired.reason)
        return _enrolled_result([selected], [acquired], budget, control=control)
    except (*_ERRORS, RuntimeError):
        return _protected()


def _existing_project_owner(owners, facts, budget):
    related = _related_owners(owners, [facts['root'], *facts['members']], budget)
    if not related:
        return None
    if len(related) == 1:
        owner = related[0]
        if (owner['kind'] == 'temporary-project-owner' and owner['target'] == facts['root']
                and owner['membership'] == facts['membership']
                and all(member in owner['members'] for member in facts['members'])):
            try:
                _reobserve_project_owner(owner, budget)
            except (*_ERRORS, RuntimeError):
                raise _HistoryConflict(related) from None
            return owner
    raise _HistoryConflict(related)


def _temporary_project_result(scope, budget, *, scope_guard, control=None):
    facts = _observe_temporary_project(scope['project_root'], scope['member_checkouts'], scope['membership'], budget)
    _require(scope['authority']['sha256'] == 'absent')
    targets = [facts['root'], *facts['members']]
    with _machine_guard(deadline=budget['deadline']) as (path, descriptor, parents):
        snapshot = _configuration_snapshot(path, descriptor)
        config = snapshot[0]
        if config is None:
            return _pending()
        context = _ready_context(config, parents, deadline=budget['deadline'])
        _storage_outside(context, path, targets)
        if control is not None:
            def reobserve():
                scope_guard()
                _require(_configuration_snapshot(path, descriptor) == snapshot
                    and _ready_context(config, parents, deadline=budget['deadline']) == context
                    and _observe_temporary_project(scope['project_root'], scope['member_checkouts'],
                        scope['membership'], budget) == facts)
            return control.access(config, context,
                lambda owners: _existing_project_owner(owners, facts, budget), reobserve, budget,
                publication=dict(kind='temporary-project-owner', targets=targets, **facts))
        with evidence._context_database(context) as db:
            owners = _owners(config, context, db, budget=budget)
            existing = _existing_project_owner(owners, facts, budget)
        if existing is not None:
            _require(_configuration_snapshot(path, descriptor) == snapshot
                     and _ready_context(config, parents, deadline=budget['deadline']) == context)
            with evidence._context_database(context) as db:
                _require(_owners(config, context, db, budget=budget) == owners)
            _check_observation_deadline(budget)
            return _resolved(existing)

        def before_commit(db):
            scope_guard()
            _require(_configuration_snapshot(path, descriptor) == snapshot
                     and _ready_context(config, parents, deadline=budget['deadline']) == context)
            _require(_observe_temporary_project(scope['project_root'], scope['member_checkouts'],
                scope['membership'], budget) == facts)
            _require(_owners(config, context, db, budget=budget) == published)
            _check_observation_deadline(budget)

        with evidence._context_database(context, write=True, durable=True, before_commit=before_commit) as db:
            owners = _owners(config, context, db, budget=budget)
            existing = _existing_project_owner(owners, facts, budget)
            if existing is None:
                _require(len(owners) < MAX_RECORDS - 1)
                created_at = state._owner_time()
                owner = dict(kind='temporary', id=uuid.uuid4().hex, created_at=created_at)
                content = dict(version=1, kind='temporary-project-owner', owner=owner,
                               created_at=created_at, provenance=config['provenance'], **facts)
                _require(sum(row['bytes'] for row in owners) + len(evidence._raw(content)) <= evidence.MAX_ARTIFACT)
                scope_guard()
                reference = _register(context, db, content, targets, budget=budget)
                existing = dict(owner=owner, reference=reference, created_at=created_at)
            published = _owners(config, context, db, budget=budget)
        return _resolved(existing)


def _scope_root_target(scope, budget):
    from runtime import onboarding as d3
    _check_observation_deadline(budget)
    root = Path(scope['project_root'])
    d3._no_links(root)
    for name in (*scope['membership']['include'], *scope['membership']['exclude']):
        boundary = root / name
        d3._no_links(boundary)
        _require(boundary.resolve().is_relative_to(root))
    if scope['kind'] == 'umbrella':
        code, _ = read_git(root, '-c', 'safe.bareRepository=all', 'rev-parse', '--absolute-git-dir',
                           limit=4096, discovery=True, deadline=budget['deadline'])
        _require(code != 0)
    target = dict(path=str(root), binding=evidence._binding(root), parents=_parents(root), git=None)
    budget['paths'] += len(target['parents']) + len(scope['membership']['include']) + len(scope['membership']['exclude']) + 1
    _require(budget['paths'] <= d3.MAX_PATHS)
    return target


def resolve_project_owner(project_scope, *, deadline=None):
    """Acquire one declared Project's accounting; do not prepare storage or enroll it."""
    return _project_owner_result(project_scope, deadline=deadline)


def _project_owner_result(project_scope, *, deadline=None, control=None, budget=None):
    from runtime import onboarding as d3
    try:
        budget = budget if budget is not None else _budget(deadline)
        d3._bounded(project_scope)
        scope = d3._project_scope(project_scope)
        root = Path(scope['project_root'])
        root_target = _scope_root_target(scope, budget)

        def scope_guard():
            _require(_scope_root_target(scope, budget) == root_target)

        members = [_path(member) for member in scope['member_checkouts']]
        acquired = [_acquire_canonical_membership(member, authority_path=None, budget=budget) for member in members]
        _check_observation_deadline(budget)
        if any(item.status != 'outside' for item in acquired):
            _require(all(item.status == 'observed' for item in acquired))
            result = _enrolled_result(members, acquired, budget, scope=scope)
        elif scope['kind'] == 'standalone':
            observed = _observe_target(members[0], budget=budget)
            _require(observed['path'] == str(members[0]) and observed['git']['primary_root'] == str(root)
                     and scope['authority']['sha256'] == 'absent'
                     and any(root.is_relative_to(root / name) for name in scope['membership']['include'])
                     and not any(root.is_relative_to(root / name) for name in scope['membership']['exclude']))
            result = _temporary_target_result(members[0], budget=budget, scope_guard=scope_guard, control=control)
        else:
            result = _temporary_project_result(scope, budget, scope_guard=scope_guard, control=control)
        scope_guard()
        _check_observation_deadline(budget)
        if control is None and result.get('status') == 'protected' and 'temporary_history' not in result:
            return _history_protected()
        return result
    except _HistoryConflict as error:
        return _history_protected(error.owners)
    except (*_ERRORS, RuntimeError):
        return _history_protected()


def resolve_owner(target):
    """Resolve existing enrolled accounting before ordinary temporary ownership."""
    enrolled = acquire_enrolled_accounting(target)
    if enrolled['status'] != 'outside':
        return enrolled
    return _temporary_target_result(target)


def _temporary_target_result(target, *, budget=None, scope_guard=None, control=None):
    try:
        observe = lambda: _observe_target(target, **({'budget': budget} if budget is not None else {}))
        guard = {'deadline': budget['deadline']} if budget is not None else {}
        with _machine_guard(**guard) as (path, descriptor, parents):
            snapshot = _configuration_snapshot(path, descriptor)
            config = snapshot[0]
            if config is None:
                return _pending()
            context = _ready_context(config, parents, **guard)
            observed = observe()
            root = Path(observed['path'])
            _require(not context.directory.is_relative_to(root) and not root.is_relative_to(context.directory)
                     and not path.is_relative_to(root))
            if control is not None:
                def reobserve():
                    if scope_guard is not None:
                        scope_guard()
                    _require(observe() == observed and _configuration_snapshot(path, descriptor) == snapshot
                             and _ready_context(config, parents, **guard) == context)
                return control.access(config, context,
                    lambda owners: _existing_owner(owners, observed, budget=budget), reobserve, budget,
                    publication=dict(kind='temporary-target-owner', targets=[observed], target=observed))
            with evidence._context_database(context) as db:
                owners = _owners(config, context, db, budget=budget)
                existing = _existing_owner(owners, observed, budget=budget)
                if existing is not None:
                    _require(observe() == observed and _configuration_snapshot(path, descriptor) == snapshot)
                    return _resolved(existing)

            def before_commit(db):
                if scope_guard is not None:
                    scope_guard()
                _require(_configuration_snapshot(path, descriptor) == snapshot and observe() == observed)
                _require(_ready_context(config, parents, **guard) == context)
                _require(_owners(config, context, db, budget=budget) == published)

            with evidence._context_database(context, write=True, durable=True, before_commit=before_commit) as db:
                owners = _owners(config, context, db, budget=budget)
                existing = _existing_owner(owners, observed, budget=budget)
                if existing is not None:
                    published = owners
                    return _resolved(existing)
                _require(len(owners) < MAX_RECORDS - 1)
                created_at = state._owner_time()
                owner = dict(kind='temporary', id=uuid.uuid4().hex, created_at=created_at)
                content = dict(version=1, kind='temporary-target-owner', owner=owner, created_at=created_at,
                               target=observed, provenance=config['provenance'])
                _require(sum(row['bytes'] for row in owners) + len(evidence._raw(content)) <= evidence.MAX_ARTIFACT)
                if scope_guard is not None:
                    scope_guard()
                reference = _register(context, db, content, [observed], **({'budget': budget} if budget else {}))
                result = dict(owner=owner, reference=reference, created_at=created_at)
                published = _owners(config, context, db, budget=budget)
            return _resolved(result)
    except _HistoryConflict:
        if budget is not None:
            raise
        return _protected()
    except _ERRORS:
        return _protected()
