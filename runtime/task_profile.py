"""The Task Profile template: the specialist roster, profile fields, level text and floors.

`templates/task-profile.json` is the single source. This module reads it on demand, validates it and caches it by
path, size and modification time, so repeated reads cost one `stat`. Standard library only: no network,
subprocess or model call. Any problem raises `TemplateUnavailable`; callers fail closed.
"""
import copy
import json
import os
from pathlib import Path
import re
import stat

TEMPLATE = Path(__file__).resolve().parents[1] / 'templates' / 'task-profile.json'
MAX_BYTES = 16 * 1024
MAX_BRIEF_BYTES = 64 * 1024
MAX_TEXT = 200
LEVEL_FIELDS = ('complexity_band', 'risk_band')
TOP_LEVEL = ('contract_version', 'fields', 'write_floors', 'levels', 'work_kinds', 'roster')
ENTRY_KEYS = {'id', 'label', 'when_to_use', 'work_kind', 'writes', 'reviews'}
SPECIALIST_ID = re.compile(r'[a-z][a-z0-9-]{1,63}')
MAX_CACHED = 8
BRIEF_START = '<!-- task-profile:start (generated from templates/task-profile.json) -->'
BRIEF_END = '<!-- task-profile:end -->'

ORCHESTRATOR = 'orchestrator'
CHECK = 'check'  # reserved job role for a check run: no model, not a specialist

# The role names written before the roster. Stored records keep them; they are read, never written again.
_IMPLEMENTS = {'work_kind': 'implementation', 'writes': True, 'reviews': False}
_REVIEWS = {'work_kind': 'review', 'writes': False, 'reviews': True}
_LEGACY_ENTRIES = {
    'worker': _IMPLEMENTS, 'review': _REVIEWS,
    'coordinator': {'id': ORCHESTRATOR, 'label': 'Orchestrator', 'work_kind': 'planning', 'writes': False,
                    'reviews': False},
    'implementer': _IMPLEMENTS, 'reviewer': _REVIEWS,
    'researcher': {'work_kind': 'investigation', 'writes': False, 'reviews': False}, 'repair': _IMPLEMENTS,
}
_LEGACY_ENTRIES = {name: dict({'id': name, 'label': name}, **entry) for name, entry in _LEGACY_ENTRIES.items()}
LEGACY = tuple(_LEGACY_ENTRIES)

_cache = {}  # path -> ((size, modification time), validated template)


class TemplateUnavailable(ValueError):
    """The template is missing, too large, unreadable or invalid."""


def _require(condition, problem):
    if not condition:
        raise TemplateUnavailable('task profile template: ' + problem)


def _text(value, limit=MAX_TEXT):
    # Text lands in a Markdown table row, so it is one printable line without a column separator.
    return isinstance(value, str) and 0 < len(value) <= limit and value.isprintable() and '|' not in value


def _values(fields, name):
    values = fields.get(name, {}).get('values') if isinstance(fields.get(name), dict) else None
    _require(isinstance(values, list) and values and all(_text(item, 64) for item in values)
             and len(set(values)) == len(values), 'field %s has no values' % name)
    return values


def _validate(template):
    _require(isinstance(template, dict) and all(key in template for key in TOP_LEVEL), 'missing sections')
    _require(type(template['contract_version']) is int, 'contract_version is not an integer')
    fields, levels, kinds = template['fields'], template['levels'], template['work_kinds']
    _require(isinstance(fields, dict) and all(isinstance(spec, dict) for spec in fields.values()), 'invalid fields')
    for name, spec in fields.items():
        if spec.get('type') == 'integer':
            _require(type(spec.get('minimum')) is int and type(spec.get('maximum')) is int, 'invalid %s' % name)
        if 'values' in spec and spec['values'] != 'roster':
            _values(fields, name)  # every enum field, not only those with a floor
        if 'floor' in spec:
            _require(spec['floor'] in _values(fields, name), 'invalid floor for %s' % name)
    floors = template['write_floors']
    _require(isinstance(floors, dict) and all(name in fields and value in _values(fields, name)
                                              for name, value in floors.items()), 'invalid write_floors')
    _require(isinstance(levels, dict) and tuple(levels) == LEVEL_FIELDS, 'levels must cover complexity and risk')
    for name in LEVEL_FIELDS:
        entries = levels[name]
        _require(isinstance(entries, dict) and list(entries) == _values(fields, name),
                 'levels of %s differ from its values' % name)
        for text in entries.values():
            _require(isinstance(text, dict) and set(text) == {'definition', 'example'}
                     and _text(text['definition']) and _text(text['example']), 'invalid level text in %s' % name)
    _require(isinstance(kinds, list) and kinds and all(_text(kind, 64) for kind in kinds), 'invalid work_kinds')
    roster = template['roster']
    _require(isinstance(roster, list), 'roster is not a list')
    seen = set()
    for entry in roster:
        _require(isinstance(entry, dict) and set(entry) == ENTRY_KEYS, 'invalid roster entry')
        identifier = entry['id']
        _require(isinstance(identifier, str) and SPECIALIST_ID.fullmatch(identifier) and identifier not in seen,
                 'invalid or duplicate roster id')
        seen.add(identifier)
        _require(_text(entry['label'], 64) and _text(entry['when_to_use']), 'invalid text for %s' % identifier)
        _require(entry['work_kind'] in kinds, 'unknown work kind for %s' % identifier)
        writes, reviews = entry['writes'], entry['reviews']
        _require(type(writes) is bool and type(reviews) is bool, 'writes and reviews must be booleans')
        _require(not (writes and reviews), '%s both writes and reviews' % identifier)
        # The ledger stage follows the work kind, so only review work may carry the review rules.
        _require(reviews == (entry['work_kind'] == 'review'), 'reviews must match the review work kind')


def load(path=TEMPLATE):
    """Return a validated copy of the template at `path`; raises `TemplateUnavailable`."""
    path = Path(path)
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        raise TemplateUnavailable('task profile template: unavailable') from None
    _require(stat.S_ISREG(info.st_mode), 'not a regular file')
    cached = _cache.get(str(path))
    if cached is None or cached[0] != (info.st_size, info.st_mtime_ns):
        _require(info.st_size <= MAX_BYTES, 'larger than %d bytes' % MAX_BYTES)
        try:
            with open(path, 'rb') as handle:
                opened = os.fstat(handle.fileno())  # the key describes the bytes actually read
                raw = handle.read(MAX_BYTES + 1)
            template = json.loads(raw.decode('utf-8'))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            raise TemplateUnavailable('task profile template: unreadable') from None
        _require(stat.S_ISREG(opened.st_mode), 'not a regular file')
        _require(len(raw) <= MAX_BYTES, 'larger than %d bytes' % MAX_BYTES)
        _validate(template)
        key = (opened.st_size, opened.st_mtime_ns)
        if len(_cache) >= MAX_CACHED:
            _cache.clear()
        cached = _cache[str(path)] = key, template
    return copy.deepcopy(cached[1])


def render_brief(template):
    """The worker brief's generated Task Profile section, markers included, from a validated template."""
    fields = template['fields']
    choices = []
    for name, spec in fields.items():
        if name == 'role':
            continue  # the Specialist line and the roster table cover it
        if isinstance(spec.get('values'), list):
            choices.append('%s (%s)' % (name, '|'.join(spec['values'])))
        elif spec.get('type') == 'integer':
            choices.append('%s (integer, %d to %d)' % (name, spec['minimum'], spec['maximum']))
        else:
            choices.append('%s (%s)' % (name, spec.get('type')))
    lines = [BRIEF_START,
             '- Specialist: [the id of the specialist whose when_to_use sentence fits this job, from the roster '
             'table below; it is the job\'s role. The orchestrator is the user\'s coordinator and is never routed.]',
             '- Task profile: [choose each level from the table below and leave out a field you cannot judge: %s]'
             % ', '.join(choices),
             '', '| Field | Level | Definition | Example |', '| --- | --- | --- | --- |']
    for name, levels in template['levels'].items():
        lines += ['| %s | %s | %s | %s |' % (name, level, text['definition'], text['example'])
                  for level, text in levels.items()]
    lines += ['', '| Specialist | Label | When to use | Work kind | Writes | Reviews |', '| --- | --- | --- | --- | --- | --- |']
    lines += ['| `%s` | %s | %s | %s | %s | %s |' % (entry['id'], entry['label'], entry['when_to_use'], entry['work_kind'],
                                                  'yes' if entry['writes'] else 'no', 'yes' if entry['reviews'] else 'no')
              for entry in template['roster'] if entry['id'] != ORCHESTRATOR]
    floors = ['%s %s' % (name, spec['floor']) for name, spec in fields.items() if 'floor' in spec]
    raised = ['%s %s' % item for item in template['write_floors'].items()]
    lines += ['', 'Floors: a missing field takes %s; a job that writes files needs at least %s.'
              % (', '.join(floors), ' and '.join(raised)), BRIEF_END]
    return '\n'.join(lines)


def brief_drift(root):
    """Problems with the generated section of `root`'s worker brief; empty when it matches the template."""
    root = Path(root)
    try:
        expected = render_brief(load(root / 'templates' / 'task-profile.json'))
    except TemplateUnavailable as error:
        return ['templates/task-profile.json: %s' % error]
    try:
        with open(root / 'templates' / 'worker-brief.md', 'rb') as handle:
            raw = handle.read(MAX_BRIEF_BYTES + 1)
        text = raw.decode('utf-8')
    except (OSError, UnicodeDecodeError):
        return ['templates/worker-brief.md: unreadable']
    if len(raw) > MAX_BRIEF_BYTES:
        return ['templates/worker-brief.md: larger than %d bytes' % MAX_BRIEF_BYTES]
    if text.count(BRIEF_START) != 1 or text.count(BRIEF_END) != 1:
        return ['templates/worker-brief.md: needs exactly one generated task profile section']
    start = text.index(BRIEF_START)
    if text[start:text.index(BRIEF_END) + len(BRIEF_END)] != expected:
        return ['templates/worker-brief.md: the task profile section differs from templates/task-profile.json; '
                'replace it with runtime.task_profile.render_brief output']
    return []


def roster():
    """Specialist id to roster entry, in template order."""
    return {entry['id']: entry for entry in load()['roster']}


def specialist(identifier):
    """The roster entry for `identifier`, or None. Only roster specialists match; `check` is not one."""
    return roster().get(identifier) if isinstance(identifier, str) else None


def stored(identifier):
    """The roster entry for `identifier`, or for an old role name in a stored record its fixed entry with
    `legacy: True`; None otherwise. The only reader of the old names: new input is checked with `specialist`."""
    if isinstance(identifier, str) and identifier in _LEGACY_ENTRIES:
        return dict(_LEGACY_ENTRIES[identifier], legacy=True)
    return specialist(identifier)


def _attribute(identifier, name):
    entry = stored(identifier)
    return entry[name] if entry else None


def routed(identifier):
    """A roster specialist other than the orchestrator, whose model routing may choose."""
    return identifier != ORCHESTRATOR and specialist(identifier) is not None


def orchestrates(identifier):
    """The orchestrator, or the stored value `coordinator`."""
    return identifier in (ORCHESTRATOR, 'coordinator')


def native(identifier):
    """A native worker or reviewer job: a roster id or a stored old name, never the orchestrator or `check`."""
    return not orchestrates(identifier) and stored(identifier) is not None


def native_worker(identifier):
    """A native job that is not a review, so the independent-review rules do not apply to it."""
    return native(identifier) and not reviews(identifier)


def reviews(identifier):
    return _attribute(identifier, 'reviews') is True


def writes(identifier):
    return _attribute(identifier, 'writes') is True


def work_kind(identifier):
    return _attribute(identifier, 'work_kind')
