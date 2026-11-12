"""Store checkout-scoped Usage Routing settings and ledger bindings."""
from contextlib import contextmanager, closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from runtime import startup, usage_budget
from runtime.usage_budget import BudgetError, private, require

PACKAGE = Path(__file__).resolve().parents[1]


def directory():
    return Path.home() / '.local/state/vibe-workflow/usage'


def scope(cwd):
    require(cwd is not None and os.name == 'posix', 'enrolled_codex_scope_required')
    root = Path(cwd).resolve(strict=True)
    acquired = startup.acquire_project_entry(SimpleNamespace(client='codex', cwd=root), PACKAGE)
    require(acquired.resolution.status in ('active','configured'), 'enrollment_unavailable_or_changed')
    authority = acquired.authority_path
    require(authority is not None and authority.is_file() and authority.stat().st_size <= 65536,
            'enrollment_unavailable_or_changed')
    raw = authority.read_bytes()
    require(len(raw) <= 65536, 'enrollment_unavailable_or_changed')
    root = startup.repository_root(root)
    require(root is not None, 'enrolled_codex_scope_required')
    binding = dict(root=str(root), device=root.stat().st_dev, inode=root.stat().st_ino,
                   authority=str(authority), digest=hashlib.sha256(raw).hexdigest())
    key = hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
    return key, root


def _read_marker(path):
    private(path)
    require(path.stat().st_size <= 1024, 'invalid_ledger_binding')
    record = json.loads(path.read_bytes())
    require(type(record) is dict and set(record) == {'identity','scope'}, 'invalid_ledger_binding')
    usage_budget.name(record['identity']); usage_budget.name(record['scope'])
    return record


def _bind(marker, identity, key):
    record = json.dumps(dict(identity=identity,scope=key)).encode()
    try:
        fd = os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    except FileExistsError:
        require(_read_marker(marker) == dict(identity=identity,scope=key), 'ledger_binding_changed')
    else:
        with os.fdopen(fd,'wb') as out:
            out.write(record); out.flush(); os.fsync(out.fileno())


def _check_bindings(folder, identity, data):
    scopes = set()
    for marker in folder.glob('checkout-*.json'):
        try:
            record = _read_marker(marker)
        except (OSError, ValueError, TypeError, KeyError):
            raise BudgetError('manual_recovery_required') from None
        require(record['identity'] == identity and marker.name == 'checkout-' + record['scope'] + '.json',
                'manual_recovery_required')
        scopes.add(record['scope'])
    require(set(data.get('routing', {})) <= scopes, 'manual_recovery_required')


@contextmanager
def _controller(cwd, *, create=False, discover=False, attach=False):
    from runtime.usage_routing import Controller
    key, root = scope(cwd)
    folder = directory()
    marker = folder / ('checkout-' + key + '.json')
    database = folder / 'organizations.sqlite3'
    if not folder.exists():
        require(create, 'not_configured')
        folder.mkdir(mode=0o700,parents=True)
    private(folder, True)
    if marker.exists() or marker.is_symlink():
        binding = _read_marker(marker)
        require(binding['scope'] == key, 'scope_changed')
        identity = binding['identity']
        unbound = False
    else:
        require(create or discover or attach, 'not_configured')
        unbound = True
        if database.exists() or database.is_symlink():
            private(database)
            # First binding to an existing canonical database; subsequent reads pin identity.
            with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro',uri=True)) as db:
                row = db.execute('SELECT body FROM ledger WHERE id=1').fetchone()
                require(row is not None and len(row[0].encode()) <= usage_budget.MAX_RECORD, 'invalid_ledger')
                identity = json.loads(row[0])['identity']
        else:
            require(not any(folder.glob('checkout-*.json')), 'manual_recovery_required')
            require(create, 'not_configured')
            identity = usage_budget.initialize(database)
    with usage_budget.Ledger(database,identity) as ledger:
        check = lambda data: _check_bindings(folder, identity, data)
        if unbound:
            check(ledger.snapshot())
        if unbound and create:
            _bind(marker, identity, key)
        bind = (lambda: _bind(marker, identity, key)) if unbound and attach else None
        yield Controller(ledger,key,root=root,bind=bind,check_authority=check)


@contextmanager
def controller(cwd, *, create=False, discover=False, attach=False):
    try:
        with _controller(cwd,create=create,discover=discover,attach=attach) as control:
            yield control
    except sqlite3.Error:
        raise BudgetError('manual_recovery_required') from None
