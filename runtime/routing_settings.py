"""Smart Routing settings: one private, revisioned file with explicit values over built-in defaults."""
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid

from runtime import task_profile, usage_budget
from runtime.usage_budget import private

VERSION = 1
MAX_BYTES = 65536
MAX_REVISION = 2 ** 53 - 1
# `worker_choices` is keyed by routed roster specialist. The orchestrator is never routed: the user's coordinator keeps
# its own model. A stored key of any other name (an old role) stays in the file, is ignored and is reported by `warnings`.
FUNDING = ('subscription', 'api')
CHOICE_FIELDS = {'provider', 'model', 'effort', 'harness', 'funding'}
MAX_CHOICES = 16
# C.3 phase W (design 4.1, 4.2): checkouts (realpaths) that use the managed routing path. Changed only by the one-shot
# `scripts/routing.py set`, behind its activation gate; C3-7 removes it.
MAX_MANAGED = 64
MAX_PROJECT_PATH = 1024
MAX_GIT_LINK = 4096  # a linked worktree's `.git`, `gitdir` and `commondir` files are one short line each (O5)
# The fixed code a legacy selector or writer answers for a managed project (design 3.6; used by C3-4b and C3-5).
LEGACY_ROUTE_RETIRED = 'legacy_route_retired'
DEFAULTS = dict(smart_routing=True, api_usage_enabled=False, L=0.65, H=0.85, T_hours=72,
                min_elapsed_fraction=0.10, lookback_hours=72, margin_bp=usage_budget.DEFAULT_MARGIN_BP,
                worker_choices={}, managed_projects=[])
# F5.A1: a stored `caps` value stays readable and validated but has no default and no effect on admission.


class SettingsError(ValueError):
    """Only fixed, nonsecret reasons cross this boundary."""


def require(condition, reason):
    if not condition:
        raise SettingsError(reason)


def _number(value):
    return type(value) in (int, float) and value == value and abs(value) != float('inf')


def _positive_int(value):
    return type(value) is int and 0 < value <= usage_budget.MAX_INTEGER


def _caps(value):
    require(type(value) is dict and set(value) == set(usage_budget.DEFAULT_CAPS)
            and all(_positive_int(v) for v in value.values()), 'invalid_caps')
    return dict(value)


def _choice(entry):
    require(type(entry) is dict and set(entry) == CHOICE_FIELDS, 'invalid_worker_choice')
    for key in CHOICE_FIELDS:
        require(type(entry[key]) is str and 0 < len(entry[key]) <= 256 and entry[key].isprintable(),
                'invalid_worker_choice')
    require(entry['funding'] in FUNDING, 'invalid_worker_choice')
    return dict(entry)


def _routed(key):
    try:
        return task_profile.routed(key)
    except task_profile.TemplateUnavailable:
        raise SettingsError('roster_unavailable') from None


def _worker_choices(value):
    require(type(value) is dict and all(_routed(key) for key in value), 'invalid_worker_choices')
    for role, entries in value.items():
        require(type(entries) is list and len(entries) <= MAX_CHOICES, 'invalid_worker_choices')
    result = {role: [_choice(entry) for entry in entries] for role, entries in value.items()}
    for entries in result.values():
        require(len({json.dumps(entry, sort_keys=True) for entry in entries}) == len(entries),
                'duplicate_worker_choice')
    return result


def _bool(value):
    require(type(value) is bool, 'invalid_boolean')
    return value


def _fraction(value):
    require(_number(value) and 0 <= value <= 1 and type(value) is not bool, 'invalid_fraction')
    return value


def _project_path(value):
    return (type(value) is str and 0 < len(value) <= MAX_PROJECT_PATH and value.isprintable()
            and os.path.isabs(value) and os.path.normpath(value) == value)


def _managed_projects(value):
    """Absolute, normalized, distinct paths; the routing command stores realpaths."""
    require(type(value) is list and len(value) <= MAX_MANAGED and all(_project_path(item) for item in value)
            and len(set(value)) == len(value), 'invalid_managed_projects')
    return list(value)


KEYS = dict(smart_routing=_bool, api_usage_enabled=_bool, L=_fraction, H=_fraction,
            min_elapsed_fraction=_fraction, caps=_caps, worker_choices=_worker_choices,
            managed_projects=_managed_projects)


def _validated(key, value):
    if key in KEYS:
        return KEYS[key](value)
    if key in ('T_hours', 'lookback_hours'):
        require(_number(value) and value > 0, 'invalid_' + key)
        return value
    if key == 'margin_bp':
        require(_positive_int(value) and value <= usage_budget.BASIS_POINTS, 'invalid_margin')
        return value
    raise SettingsError('unknown_setting')


def resolve(stored):
    """Merge explicitly stored values over the defaults and enforce the invariants."""
    require(type(stored) is dict, 'invalid_settings')
    value = {key: (dict(v) if type(v) is dict else list(v) if type(v) is list else v) for key, v in DEFAULTS.items()}
    for key, item in stored.items():
        if key == 'worker_choices' and type(item) is dict:
            item = {role: entries for role, entries in item.items() if _routed(role)}
        value[key] = _validated(key, item)
    require(0 <= value['L'] < value['H'] <= 1, 'invalid_thresholds')
    return value


def warnings(stored):
    """Stored settings that are kept but ignored: `worker_choices` keys that name no routed roster specialist, and
    legacy `caps` (F5.A1: never enforced)."""
    choices = stored.get('worker_choices') if type(stored) is dict else None
    ignored = [dict(code='worker_choice_key_unknown', key=key) for key in choices if not _routed(key)] \
        if type(choices) is dict else []
    return ignored + ([dict(code='caps_not_enforced', key='caps')] if type(stored) is dict and 'caps' in stored else [])


def _load(path):
    """Return (revision, explicit values); a missing file is revision 0 with no values."""
    path = Path(path)
    try:
        private(path.parent, True)
    except FileNotFoundError:
        return 0, {}
    try:
        info = private(path)
    except FileNotFoundError:
        return 0, {}
    require(info.st_size <= MAX_BYTES, 'invalid_settings')
    try:
        record = json.loads(path.read_bytes())
    except ValueError:
        raise SettingsError('invalid_settings') from None
    require(type(record) is dict and set(record) == {'version', 'revision', 'values'}
            and record['version'] == VERSION and type(record['revision']) is int
            and 0 < record['revision'] <= MAX_REVISION and type(record['values']) is dict,
            'invalid_settings')
    resolve(record['values'])
    return record['revision'], record['values']


def revision(path):
    return _load(path)[0]


def read(path):
    return resolve(_load(path)[1])


def _write(path, record):
    raw = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    require(len(raw) <= MAX_BYTES, 'invalid_settings')
    temporary = path.parent / ('.settings-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def update(path, change, expected_revision):
    """Apply explicit changes if `expected_revision` is current; return the resolved settings and revision."""
    require(type(change) is dict and change and type(expected_revision) is int, 'invalid_change')
    path = Path(path).absolute()
    path = path.parent.resolve() / path.name
    for folder in reversed([p for p in (path.parent, *path.parent.parents) if not p.exists()]):
        folder.mkdir(mode=0o700)  # every directory this call creates is private, not just the leaf
    private(path.parent, True)
    lock = os.open(path.parent / '.settings.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current, values = _load(path)
        require(current == expected_revision, 'stale_revision')
        require(current < MAX_REVISION, 'revision_exhausted')
        merged = dict(values)
        for key, item in change.items():
            merged[key] = _validated(key, item)
        resolved = resolve(merged)
        _write(path, dict(version=VERSION, revision=current + 1, values=merged))
        return dict(resolved, revision=current + 1)
    finally:
        os.close(lock)


def assignment_role(profile_role, explicit=None):
    """The worker-choice key for a profile's specialist: the specialist itself. An explicit role must equal it."""
    require(not task_profile.orchestrates(profile_role), 'coordinator_not_routed')
    require(_routed(profile_role), 'invalid_role')
    require(explicit is None or explicit == profile_role, 'role_mismatch')
    return profile_role


def _identity(path):
    """(st_dev, st_ino) of an existing path, None when it does not exist; any other error propagates."""
    try:
        info = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return info.st_dev, info.st_ino


def _small(path):
    """The stripped text of a small regular Git link file; ValueError for anything else. Opened without blocking, so a
    FIFO planted by a forged link is refused instead of waited on."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('git_link_not_a_file')
        raw = os.read(fd, MAX_GIT_LINK + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_GIT_LINK:
        raise ValueError('git_link_too_large')
    return raw.decode('utf-8').strip()


def _linked_primary(target):
    """The primary checkout of the linked Git worktree holding `target` (a realpath), or None (O5).

    No Git process: walk up to the nearest `.git` entry. A directory is a primary checkout or a plain repository,
    which the caller already matched. A `gitdir: <p>` file counts only when `<p>/gitdir` names this same `.git`
    file back (by file identity), so a forged `.git` file never joins a list; then `<p>/commondir` gives the common
    directory, whose parent is the primary when that parent's `.git` is the common directory. Relative links resolve
    from the file's own directory. Bounded: one stat per parent until the `.git` entry, three small reads. Any
    error or mismatch is None: an unresolvable worktree is simply not a linked one."""
    try:
        for folder in (target, *target.parents):
            marker = folder / '.git'
            try:
                info = os.stat(marker)
            except (FileNotFoundError, NotADirectoryError):
                continue
            text = _small(marker) if stat.S_ISREG(info.st_mode) else ''
            if not text.startswith('gitdir: '):
                return None
            gitdir = Path(os.path.join(folder, text[len('gitdir: '):])).resolve()
            if _identity(os.path.join(gitdir, _small(gitdir / 'gitdir'))) != (info.st_dev, info.st_ino):
                return None
            common = Path(os.path.join(gitdir, _small(gitdir / 'commondir'))).resolve()
            found = _identity(common)
            return common.parent if found is not None and _identity(common.parent / '.git') == found else None
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _within(target, projects):
    """Whether the realpath `target` is a registered root or lies inside one, by spelling or by file identity."""
    chain = [target, *target.parents]
    if any(item in map(str, chain) for item in projects):
        return True
    roots = {found for found in map(_identity, projects) if found is not None}
    return any(found in roots for found in map(_identity, chain) if found is not None)


def default_path():
    """The machine's routing settings, `scripts/routing.py`'s default store (HOME-relative, so tests use a temp HOME)."""
    return Path.home() / '.local/state/vibe-workflow/routing/settings.json'


def managed_project(path, settings_path=None):
    """Whether `path` is a managed project or lies inside one. `settings_path` defaults to the routing command's
    settings (`scripts.routing.default_settings`).

    Paths compare by file identity, not spelling: `path` (realpath) and each of its existing parents are matched
    against each registered root's (st_dev, st_ino), so a differently cased spelling on a case-insensitive
    filesystem, `..` and symlinks all match. A path that does not exist yet matches through its nearest existing
    parent, so a new path under a managed root is managed. A registered root that no longer exists still matches
    by its stored path. A path in a linked Git worktree whose primary checkout is managed is managed too, wherever
    the worktree lives (O5, `_linked_primary`).

    Fail closed: a missing settings file has no managed project, but settings that exist and cannot be read, or a
    path or root that cannot be inspected, count as managed. The managed path then blocks (`settings_unavailable`)
    and every legacy spending entry point refuses, so a corrupt settings file also stops legacy API Usage spending
    (once C3-5 lands) until it is repaired. A worktree link that cannot be resolved is not managed through it."""
    if settings_path is None:
        from scripts import routing as command
        settings_path = command.default_settings()
    return managed_state(path, settings_path) is not False


def managed_state(path, settings_path):
    """`managed_project`'s answer without its fail-closed step: True, False, or None where it counts a project as
    managed only because the settings or a path cannot be read. One settings read; `workflow.py features status`
    reports None as unavailable, never as on."""
    try:
        projects = read(settings_path)['managed_projects']
        target = Path(path).resolve()
        if _within(target, projects):
            return True
        primary = _linked_primary(target)
        return primary is not None and _within(primary, projects)
    except (SettingsError, usage_budget.BudgetError, OSError, RuntimeError, ValueError):
        return None


def workstreams_enabled(scope, *, load=None):
    """Read the Workstreams project preference from its authority; this module stores no copy."""
    if load is None:
        from runtime import features
        return features.resolve(scope.repository_root)['features']['workstreams']['effective'] is True
    return load(scope)['enabled'] is True
