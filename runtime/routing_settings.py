"""Smart Routing settings: one private, revisioned file with explicit values over built-in defaults."""
import fcntl
import json
import os
from pathlib import Path
import uuid

from runtime import usage_budget
from runtime.usage_budget import private

VERSION = 1
MAX_BYTES = 65536
MAX_REVISION = 2 ** 53 - 1
ROLES = ('implementer', 'reviewer', 'researcher', 'repair')
FUNDING = ('subscription', 'api')
CHOICE_FIELDS = {'provider', 'model', 'effort', 'harness', 'funding'}
MAX_CHOICES = 16
DEFAULTS = dict(smart_routing=True, api_usage_enabled=False, L=0.65, H=0.85, T_hours=72,
                min_elapsed_fraction=0.10, lookback_hours=72, margin_bp=usage_budget.DEFAULT_MARGIN_BP,
                caps=usage_budget.DEFAULT_CAPS, worker_choices={})


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


def _worker_choices(value):
    require(type(value) is dict and set(value) <= set(ROLES), 'invalid_worker_choices')
    for role, entries in value.items():
        require(type(entries) is list and len(entries) <= MAX_CHOICES, 'invalid_worker_choices')
    return {role: [_choice(entry) for entry in entries] for role, entries in value.items()}


def _bool(value):
    require(type(value) is bool, 'invalid_boolean')
    return value


def _fraction(value):
    require(_number(value) and 0 <= value <= 1 and type(value) is not bool, 'invalid_fraction')
    return value


KEYS = dict(smart_routing=_bool, api_usage_enabled=_bool, L=_fraction, H=_fraction,
            min_elapsed_fraction=_fraction, caps=_caps, worker_choices=_worker_choices)


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
    value = {key: (dict(v) if type(v) is dict else v) for key, v in DEFAULTS.items()}
    for key, item in stored.items():
        value[key] = _validated(key, item)
    require(0 <= value['L'] < value['H'] <= 1, 'invalid_thresholds')
    return value


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
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


def workstreams_enabled(scope, *, load=None):
    """Read the Workstreams project preference from its authority; this module stores no copy."""
    if load is None:
        from runtime import workstreams
        load = workstreams._load
    return load(scope)['enabled'] is True
