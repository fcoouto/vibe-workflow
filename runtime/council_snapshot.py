"""Bound reviewed text bytes into private read-only Council evidence; run no tools."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat

from runtime import changes, onboarding

MAX_FILE = 512 * 1024
MAX_TOTAL = 32 * 1024 * 1024
MAX_PATHS = 2000
MAX_MANIFEST = 2 * 1024 * 1024
MARKER = '.council-snapshot.json'
GAPS = ['Only explicitly approved files are included; omitted content is not reviewed.',
        'Structural exclusions do not establish semantic secret review.',
        'Nested repositories are excluded; no project tools or Git configuration are copied.']
DENIED_DIRECTORIES = {'.git', '.hg', '.svn', '.ssh', '.aws', '.azure', '.gcloud', '.config',
    '.local', '.cache', '.npm', '.codex', '.claude', '.cursor', '.opencode',
    '.vibe-workflow', '.vibe-coding-workflow'}
DENIED_FILES = {'.netrc', '.npmrc', '.pypirc', '.gitconfig', '.git-credentials', 'auth.json',
    'credentials.json', 'credentials', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', MARKER}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _canonical(path):
    _require(isinstance(path, Path) and path.is_absolute() and str(path.resolve()) == str(path),
             'council_path_must_be_absolute_canonical_without_links')
    return path


def _relative(name):
    _require(type(name) is str and 0 < len(name) <= 4096 and name.isprintable()
             and '\\' not in name and '\x00' not in name, 'invalid_council_relative_path')
    path = PurePosixPath(name)
    _require(not path.is_absolute() and str(path) == name and name != '.' and '..' not in path.parts,
             'invalid_council_relative_path')
    return path


def _approved(value, *, empty=False):
    _require(type(value) is dict and (empty or bool(value)) and len(value) <= MAX_PATHS,
             'invalid_council_approved_files')
    total = 0
    for name, item in value.items():
        _relative(name)
        _require(type(item) is dict and set(item) == {'sha256', 'size'}
                 and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}', item['sha256'])
                 and type(item['size']) is int and 0 <= item['size'] <= MAX_FILE,
                 'invalid_council_review_binding')
        total += item['size']
    _require(total <= MAX_TOTAL, 'council_snapshot_total_limit')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _identity(info):
    return dict(device=info.st_dev, inode=info.st_ino)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns)


def _safe(info, *, directory=False, sealed=False):
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
             and info.st_uid == os.getuid() and not info.st_mode & (0o022 | 0o7000)
             and (directory or info.st_nlink == 1), 'unsafe_council_file_identity_or_permissions')
    if sealed:
        _require(stat.S_IMODE(info.st_mode) == (0o500 if directory else 0o400),
                 'council_snapshot_is_not_read_only')


def _directory(path, *, private=False, sealed=False):
    descriptor = onboarding._open_path(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        info = os.fstat(descriptor)
        _safe(info, directory=True, sealed=sealed)
        if private:
            _require(not info.st_mode & 0o077, 'council_destination_must_be_private')
        return _identity(info)
    finally:
        os.close(descriptor)


def _excluded(name):
    parts = PurePosixPath(name).parts
    return (any(part.lower() in DENIED_DIRECTORIES for part in parts)
            or any(part.lower() in DENIED_FILES or part.lower() == '.env'
                   or part.lower().startswith('.env.') for part in parts)
            or PurePosixPath(name).suffix.lower() in {'.key', '.pem', '.p12', '.pfx', '.sqlite', '.sqlite3'})


def _repository(directory):
    return (os.path.lexists(directory / '.git')
            or all(os.path.lexists(directory / name) for name in ('HEAD', 'objects', 'refs')))


def _source(checkout, name, reviewed):
    """Bind every source directory and final descriptor; never follow a named link."""
    path = checkout / name
    directories = [checkout]
    for part in PurePosixPath(name).parts[:-1]:
        directories.append(directories[-1] / part)
    bindings = []
    for directory in directories:
        identity = _directory(directory)
        info = directory.lstat()
        _require(_identity(info) == identity, 'council_source_directory_changed')
        bindings.append((directory, _signature(info)))
    if any(_repository(directory) for directory in directories[1:]):
        return None
    before = path.lstat()
    _safe(before)
    raw = onboarding._read_file(path, MAX_FILE, expected_binding=_identity(before))
    _require(_signature(path.lstat()) == _signature(before)
             and all(_directory(directory) == dict(device=binding[0], inode=binding[1])
                     and _signature(directory.lstat()) == binding for directory, binding in bindings),
             'council_source_changed_during_capture')
    _require(len(raw) == reviewed['size'] and _hash(raw) == reviewed['sha256'],
             'council_reviewed_bytes_changed')
    raw.decode('utf-8', errors='strict')
    return raw, _signature(before), bindings


def _destination(checkout, destination):
    _canonical(destination)
    _require(destination != checkout and not destination.is_relative_to(checkout)
             and not checkout.is_relative_to(destination), 'council_destination_must_be_outside_checkout')
    _require(not os.path.lexists(destination), 'council_destination_must_be_absent')
    _require(not any(_repository(parent) for parent in destination.parents),
             'council_destination_must_be_outside_repositories')
    return _directory(destination.parent, private=True)


def capture(checkout: Path, destination: Path, approved: dict, *, allow_empty: bool = False) -> dict:
    """Capture reviewed UTF-8 files. Only a final marker plus verify means complete."""
    created = None
    complete = False
    folders = {}
    try:
        _require(os.name == 'posix', 'council_requires_posix')
        _canonical(checkout)
        _directory(checkout)
        _require(type(allow_empty) is bool, 'invalid_council_empty_scope')
        _approved(approved, empty=allow_empty)
        parent = _destination(checkout, destination)
        contents, observations, exclusions = {}, {}, []
        for name, reviewed in sorted(approved.items()):
            if _excluded(name):
                exclusions.append(dict(path=name, reason='reserved_git_auth_runtime_or_credentials'))
                continue
            observed = _source(checkout, name, reviewed)
            if observed is None:
                exclusions.append(dict(path=name, reason='nested_repository'))
                continue
            contents[name], signature, bindings = observed
            observations[name] = signature, bindings
        files = {name: dict(approved[name]) for name in contents}
        manifest = dict(version=1, files=files, exclusions=exclusions,
            coverage=dict(approved=len(approved), captured=len(files), excluded=len(exclusions), gaps=list(GAPS)))
        manifest['digest'] = _hash(_json(manifest))
        raw_manifest = _json(manifest)
        _require(len(raw_manifest) <= MAX_MANIFEST, 'council_manifest_size_limit')
        directory_count = len({parent for name in files for parent in PurePosixPath(name).parents})
        block_size = os.statvfs(destination.parent).f_frsize
        _require(shutil.disk_usage(destination.parent)[2] >= sum(map(len, contents.values()))
                 + len(raw_manifest) + (len(files) + directory_count + 1) * block_size
                 + 1024 * 1024, 'council_snapshot_insufficient_space')
        _require(_directory(destination.parent, private=True) == parent, 'council_destination_parent_changed')
        destination.mkdir(mode=0o700)
        created = _directory(destination, private=True)
        def guard():
            _require(_directory(destination) == created
                     and _directory(destination.parent, private=True) == parent, 'council_destination_changed')
        folders = {destination: created}
        for name, raw in contents.items():
            guard()
            folder = destination
            for part in PurePosixPath(name).parts[:-1]:
                folder /= part
                if folder not in folders:
                    folder.mkdir(mode=0o700)
                    folders[folder] = _directory(folder, private=True)
                _require(_directory(folder, private=True) == folders[folder], 'council_destination_changed')
            def file_guard():
                guard()
                _require(_directory(folder, private=True) == folders[folder], 'council_destination_changed')
            changes._atomic(folder, PurePosixPath(name).name, raw, mode=0o400, guard=file_guard)
        for name, reviewed in files.items():
            observed = _source(checkout, name, reviewed)
            _require(observed is not None and (observed[1], observed[2]) == observations[name],
                     'council_source_changed_during_capture')
        guard()
        changes._atomic(destination, MARKER, raw_manifest, mode=0o400, guard=guard)
        for folder, binding in reversed(list(folders.items())):
            descriptor = onboarding._open_path(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                _require(_identity(os.fstat(descriptor)) == binding, 'council_destination_changed')
                os.fchmod(descriptor, 0o500)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        verify(destination, manifest)
        complete = True
        return manifest
    except (OSError, UnicodeError) as error:
        raise ValueError('council_snapshot_unavailable') from error
    finally:
        if created is not None and not complete:
            # A failed capture owns only its unchanged directory; uncertain paths remain for inspection.
            _cleanup(folders)


def _cleanup(folders):
    """Remove only empty owned directories; leave partial evidence for explicit recovery."""
    for folder, identity in reversed(list(folders.items())):
        try:
            descriptor = onboarding._open_path(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                if _identity(os.fstat(descriptor)) != identity:
                    continue
                os.fchmod(descriptor, 0o700)
                if os.listdir(descriptor):
                    continue
                _require(_directory(folder) == identity, 'council_destination_changed')
                folder.rmdir()
            finally:
                os.close(descriptor)
        except (OSError, ValueError):
            pass


def verify(snapshot: Path, manifest: dict) -> None:
    """Reject incomplete or changed snapshot files, links, permissions and manifests."""
    try:
        _canonical(snapshot)
        _require(type(manifest) is dict and set(manifest) == {'version', 'digest', 'files', 'exclusions', 'coverage'}
                 and type(manifest['version']) is int and manifest['version'] == 1,
                 'invalid_council_snapshot_manifest')
        _approved(manifest['files'], empty=True)
        _require(type(manifest['exclusions']) is list and len(manifest['exclusions']) <= MAX_PATHS,
                 'invalid_council_exclusions')
        excluded = set()
        for item in manifest['exclusions']:
            _require(type(item) is dict and set(item) == {'path', 'reason'}
                     and item['reason'] in ('reserved_git_auth_runtime_or_credentials', 'nested_repository'),
                     'invalid_council_exclusions')
            _relative(item['path'])
            _require(item['path'] not in excluded and item['path'] not in manifest['files'],
                     'invalid_council_exclusions')
            excluded.add(item['path'])
        count_files, count_excluded = len(manifest['files']), len(excluded)
        _require(count_files + count_excluded <= MAX_PATHS
                 and type(manifest['coverage']) is dict
                 and all(type(manifest['coverage'].get(key)) is int for key in ('approved', 'captured', 'excluded'))
                 and manifest['coverage'] == dict(approved=count_files + count_excluded,
                     captured=count_files, excluded=count_excluded, gaps=GAPS), 'invalid_council_coverage')
        _require(len(_json(manifest)) <= MAX_MANIFEST, 'council_manifest_size_limit')
        _require(manifest['digest'] == _hash(_json({key: value for key, value in manifest.items() if key != 'digest'})),
                 'council_snapshot_manifest_digest_changed')
        root = _directory(snapshot, private=True, sealed=True)
        marker_path = snapshot / MARKER
        marker_before = marker_path.lstat()
        _safe(marker_before, sealed=True)
        marker = onboarding._read_file(marker_path, MAX_MANIFEST, private=True,
                                       expected_binding=_identity(marker_before))
        _require(_signature(marker_path.lstat()) == _signature(marker_before), 'council_snapshot_manifest_changed')
        _require(marker == _json(manifest), 'council_snapshot_manifest_changed_or_missing')
        expected_directories = {str(parent) for name in manifest['files']
                                for parent in PurePosixPath(name).parents if str(parent) != '.'}
        observed = set()
        signatures = {marker_path: _signature(marker_before)}
        count = 0
        for folder, directories, files in os.walk(snapshot, followlinks=False):
            folder = Path(folder)
            _directory(folder, private=True, sealed=True)
            signatures[folder] = _signature(folder.lstat())
            for name in directories:
                relative = str((folder / name).relative_to(snapshot))
                _require(relative in expected_directories, 'unexpected_council_snapshot_directory')
                _directory(folder / name, private=True, sealed=True)
            for name in files:
                count += 1
                _require(count <= MAX_PATHS + 1, 'council_snapshot_path_limit')
                path = folder / name
                relative = str(path.relative_to(snapshot))
                if relative == MARKER: continue
                _require(relative in manifest['files'] and not _excluded(relative), 'unexpected_council_snapshot_file')
                before = path.lstat()
                _safe(before, sealed=True)
                raw = onboarding._read_file(path, MAX_FILE, private=True, expected_binding=_identity(before))
                _require(_signature(path.lstat()) == _signature(before)
                         and len(raw) == manifest['files'][relative]['size']
                         and _hash(raw) == manifest['files'][relative]['sha256'], 'council_snapshot_bytes_changed')
                raw.decode('utf-8', errors='strict')
                observed.add(relative)
                signatures[path] = _signature(before)
        _require(observed == set(manifest['files']) and _directory(snapshot, sealed=True) == root
                 and all(_signature(path.lstat()) == signature for path, signature in signatures.items()),
                 'council_snapshot_files_changed_or_missing')
    except (OSError, UnicodeError, TypeError, KeyError) as error:
        raise ValueError('council_snapshot_unavailable') from error
