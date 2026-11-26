"""Private bounded JSON caches, extracted without the former ledger."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid

MAX_FILE = 1 << 20
READ_ATTEMPTS = 3  # a writer's atomic replacement may land between the named check and the open
FRESH_SECONDS = 900
EXHAUSTED_RECHECK_SECONDS = 60
CLOCK_MARGIN = 60
WINDOW_UNIT = 'utilization_thousandths'
WINDOW_UNITS = 1000
RATE_WINDOWS = dict(five_hour=('5h', 5 * 3600), seven_day=('7d', 7 * 86400))


class StorageError(ValueError):
    """A fixed private-cache error."""


def private(path, directory=False):
    path = Path(path)
    info = path.lstat()
    if path.is_symlink() or path.resolve() != path or info.st_uid != os.getuid() \
            or info.st_mode & 0o077 or not (stat.S_ISDIR(info.st_mode) if directory else
                stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
        raise StorageError('observation_store_unsafe')
    return info


def _resolved(path):
    path = Path(path).absolute()
    if path.parent.resolve() != path.parent:
        raise StorageError('observation_store_unsafe')
    return path


def read_json(path):
    """The JSON of a private file, read only from the very file whose name passed every check.

    A writer replaces the file atomically, so the file opened can differ from the one just checked by name, or be
    unlinked already. Only that is tried again, a bounded number of times, each with the full checks on the name anew;
    an extra link, a symlink, another owner or mode, or an oversized file is refused at once."""
    path = _resolved(path)
    for _ in range(READ_ATTEMPTS):
        try:
            private(path.parent, True)
            info = private(path)
        except FileNotFoundError:
            return None
        if info.st_size > MAX_FILE:
            raise StorageError('observation_store_unsafe')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            actual = os.fstat(source.fileno())
            if actual.st_nlink > 1:
                raise StorageError('observation_store_unsafe')
            if (actual.st_dev, actual.st_ino, actual.st_nlink) != (info.st_dev, info.st_ino, 1):
                continue  # replaced between the check and the open: check the file now named, never trust this one
            raw = source.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise StorageError('observation_store_unsafe')
        return json.loads(raw)
    raise StorageError('observation_store_unsafe')


def _read(path):
    value = read_json(path)
    if value is None:
        return {}
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1:
        raise StorageError('observation_store_invalid')
    return value


@contextmanager
def _locked(path, block=True, lock='.observations.lock'):
    path = _resolved(path)
    for folder in reversed([p for p in (path.parent, *path.parent.parents) if not p.exists()]):
        folder.mkdir(mode=0o700, exist_ok=True)
    private(path.parent, True)
    lock_path = path.parent / lock
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private(lock_path)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        yield path
    finally:
        os.close(fd)


def _write(path, value):
    path = _resolved(path)
    private(path.parent, True)
    try:
        private(path)
    except FileNotFoundError:
        pass
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_FILE:
        raise StorageError('observation_store_full')
    temporary = path.parent / ('.observations-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def window_path(settings_path):
    return Path(settings_path).parent / 'window-observations.json'


def store_window(settings_path, pool, window):
    with _locked(window_path(settings_path)) as path:
        data = _read(path) or dict(version=1, pools={})
        rows = data.setdefault('pools', {}).setdefault(pool, {})
        old = rows.get(window['id'])
        if old is None or old['observed_at'] <= window['observed_at']:
            rows[window['id']] = dict(window)
        _write(path, data)


def consumed_at(settings_path, pool):
    value = _read(window_path(settings_path)).get('consumed', {}).get(pool, 0)
    return value if type(value) is int and value >= 0 else 0


def trust_left(at, now, trust=EXHAUSTED_RECHECK_SECONDS):
    return max(1, trust - (now - at)) if type(at) is int and 0 <= now - at <= trust else None
