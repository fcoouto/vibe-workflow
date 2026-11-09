"""Current physical ownership for local recovery; never an execution scope."""
from dataclasses import dataclass
from pathlib import Path
import time

from runtime import onboarding, startup, state


@dataclass(frozen=True)
class LocalOwnership:
    repository_root: Path
    git_common_dir: Path
    runtime: startup.RuntimeSettings | None
    scope_id: str
    accepted_scope_digest: str
    accepted_identity: object
    acquisition: startup.CanonicalMembershipAcquisition

    @property
    def state_dir(self): return self.runtime.state_dir if self.runtime else None

    @property
    def database(self): return self.state_dir / 'runtime.sqlite3'


def _acquire(cwd, *, deadline=None, settings=False):
    """Reobserve canonical membership without qualification or installed-source pins."""
    budget = dict(paths=0, bytes=0, deadline=time.monotonic() + 4)
    if deadline is not None: budget['deadline'] = min(budget['deadline'], deadline)
    acquired = startup._acquire_canonical_membership(Path(cwd), authority_path=None, budget=budget)
    if acquired.status != 'observed' or acquired.observation['membership'] != 'included':
        raise startup.StartupError('local_ownership_unavailable')
    data = startup._canonical_plain(acquired.record.data)
    observed = startup._canonical_plain(acquired.observation)
    repo = next((row for row in data['repositories'] if row['primary_root'] == observed['primary_root']), None)
    selected = next((row for row in repo['checkouts'] if row['root'] == observed['root']), None) if repo else None
    if selected is None:
        raise startup.StartupError('local_ownership_unavailable')
    if (repo['membership'] != 'joined' or data['project_binding'] != observed['project_binding']
            or selected['git_dir'] != observed['git_dir'] or repo['git_common_dir'] != observed['git_common_dir']
            or repo['binding'] | selected['binding'] != observed['binding']):
        raise startup.StartupError('local_ownership_changed')
    projected = startup.canonical_scope_projection(acquired.record,
        repository_id=repo['repository_id'], checkout_id=selected['checkout_id'], client='codex')
    identity = startup._canonical_plain(projected)
    configured = identity['configuration']['runtime'] if identity else None
    if configured is None and not settings:
        raise startup.StartupError('local_runtime_unconfigured')
    runtime = None
    if configured is not None:
        state._accepted_identity(projected)
        # Reuse location validation; this legacy schema value grants no execution authority.
        location = startup.runtime_settings(dict(runtime=dict(schema_version=2, state_dir=configured['state_dir'])),
            Path(__file__).resolve().parents[1], Path(repo['git_common_dir']), budget=budget)
        onboarding._no_links(location.state_dir)
        runtime = startup.RuntimeSettings(3, location.state_dir)
    return LocalOwnership(Path(selected['root']), Path(repo['git_common_dir']), runtime,
        state._digest([3, data['project_id'], repo['repository_id'], selected['checkout_id']]),
        state._digest(identity), startup._canonical_freeze(identity), acquired)


def acquire(cwd, *, deadline=None):
    return _acquire(cwd, deadline=deadline)


def settings_scope(cwd):
    return _acquire(cwd, settings=True)


def settings_absent(owner, namespace):
    if settings_scope(owner.repository_root) != owner:
        raise startup.StartupError('local_ownership_changed')
    if owner.runtime is None: return True
    for directory in (owner.state_dir, owner.state_dir / namespace):
        onboarding._no_links(directory)
        try: state._private(directory, True)
        except FileNotFoundError: return True
    target = owner.state_dir / namespace / (owner.scope_id + '.json')
    try: target.lstat()
    except FileNotFoundError: return True
    return False


def settings_directory(owner):
    if settings_scope(owner.repository_root) != owner:
        raise startup.StartupError('local_ownership_changed')
    onboarding._no_links(owner.state_dir)
    state._private(owner.state_dir, True)
    info = owner.state_dir.lstat()
    return dict(device=info.st_dev, inode=info.st_ino)


def targets(owner, *, deadline=None):
    """Check established private targets; database contents do not authorize recovery."""
    if type(owner) is not LocalOwnership or acquire(owner.repository_root, deadline=deadline) != owner:
        raise startup.StartupError('local_ownership_changed')
    onboarding._no_links(owner.database)
    state._private(owner.state_dir, True)
    state._private(owner.database)
    return {name: dict(device=info.st_dev, inode=info.st_ino)
        for name, info in (('directory', owner.state_dir.lstat()), ('database', owner.database.lstat()))}
