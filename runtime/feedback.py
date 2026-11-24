"""Feedback cards between models: one bounded private store and the capped adjustment it feeds.

Never raises: a damaged store reads as empty, an unsafe one refuses writes.
"""
from pathlib import Path
import math
import re

from runtime import scores
from runtime import storage

FILE = 'router-feedback.json'
LOCK = '.feedback.lock'
HALF_LIFE = 30 * 86400
MIN_SESSIONS = 3
MAX_KEYS = 400
MAX_VOTES = 5000
MAX_SESSIONS = 10
MAX_COMMENTS = 3
COMMENT_MAX = 280
SIZE_GROUPS = ('short', 'long')
SAME_LAB_WEIGHT = 0.5
FAMILY_WEIGHT = 0.5
LAB = re.compile(r'~?[a-z0-9][a-z0-9-]{0,63}')
MODEL = re.compile(r'[a-z0-9][a-z0-9-]{0,127}')
FAMILY = re.compile(r'[a-z0-9-]{0,128}')
KIND = re.compile(r'[a-z][a-z-]{0,31}')
SESSION = re.compile(r'[0-9a-fA-F]{16,64}')


def _text(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _factor(at, now):
    return 0.5 ** (max(0, now - at) / HALF_LIFE)


def _store_key(creator, key, work_kind, size_group):
    return f'{creator}/{key}|{work_kind}|{size_group}'


def _parse_key(name):
    """(lab, model key, work kind, size group) of a stored key, or None when malformed."""
    parts = name.split('|') if type(name) is str else []
    if len(parts) != 3:
        return None
    lab, slash, model = parts[0].partition('/')
    if not slash or not (LAB.fullmatch(lab) and MODEL.fullmatch(model)
                         and KIND.fullmatch(parts[1]) and parts[2] in SIZE_GROUPS):
        return None
    return lab, model, parts[1], parts[2]


def _entry_ok(entry):
    return type(entry) is dict and _number(entry.get('w')) and _number(entry.get('wr')) \
        and type(entry.get('at')) is int and entry['at'] >= 0 and _text(FAMILY, entry.get('family')) \
        and type(entry.get('sessions')) is list and len(entry['sessions']) <= MAX_SESSIONS \
        and all(type(item) is str for item in entry['sessions']) \
        and type(entry.get('comments')) is list and len(entry['comments']) <= MAX_COMMENTS \
        and all(type(item) is str for item in entry['comments']) \
        and (entry.get('last_rater') is None or type(entry['last_rater']) is str)


def _valid(value):
    """The store when its shape is right, else None (the caller treats it as damaged)."""
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1 \
            or type(value.get('keys')) is not dict or type(value.get('votes')) is not dict:
        return None
    if not all(_parse_key(name) and _entry_ok(entry) for name, entry in value['keys'].items()):
        return None
    if not all(type(name) is str and type(at) is int for name, at in value['votes'].items()):
        return None
    return value


def _read(path):
    """The store, or None when missing or damaged. Raises StorageError when the file is unsafe."""
    try:
        info = storage.private(path)
    except FileNotFoundError:
        return None
    if info.st_size > storage.MAX_FILE:
        return None
    try:
        value = storage.read_json(path)
    except storage.StorageError:
        raise
    except (ValueError, RecursionError):
        return None
    return _valid(value)


def load(state_dir):
    """The store, or None when missing, damaged, unsafe or unreadable."""
    try:
        return _read(Path(state_dir) / FILE)
    except (OSError, ValueError, RecursionError, TypeError):
        return None


def _card(card):
    """The card's fields as a dict, or None when any field is missing or out of range."""
    if type(card) is not dict or type(card.get('rated')) is not dict:
        return None
    rated, rater = card['rated'], card.get('rater')
    session, comment = card.get('session'), card.get('comment')
    if not (_text(LAB, rated.get('creator')) and _text(MODEL, rated.get('key'))
            and _text(FAMILY, rated.get('family')) and rated['family'] == scores.family_key(rated['key'])
            and _text(KIND, card.get('work_kind'))
            and card.get('size_group') in SIZE_GROUPS and type(card.get('rating')) is int
            and 0 <= card['rating'] <= 5 and type(session) is str and SESSION.fullmatch(session)
            and (comment is None or type(comment) is str and len(comment) <= COMMENT_MAX)):
        return None
    if rater is not None and not (type(rater) is dict and _text(LAB, rater.get('creator'))
                                  and _text(MODEL, rater.get('key'))):
        return None
    return dict(
        creator=rated['creator'], key=rated['key'], family=rated['family'],
        rater=None if rater is None else (rater['creator'], rater['key']),
        work_kind=card['work_kind'], size_group=card['size_group'], rating=card['rating'],
        session=session.lower(), comment=comment or None,
    )


def _refused(reason):
    return dict(state='refused', reason=reason)


def _trim(mapping, keep, rank):
    """Drop the oldest entries, by rank, until at most `keep` remain."""
    for name in sorted(mapping, key=rank)[:max(0, len(mapping) - keep)]:
        del mapping[name]


def _record(card, state_dir, now):
    clean = _card(card)
    if clean is None:
        return _refused('invalid')
    creator, key = clean['creator'], clean['key']
    if clean['rater'] == (creator, key):
        return _refused('self_rating')
    now = int(now)
    with storage._locked(Path(state_dir) / FILE, lock=LOCK) as path:
        store = _read(path) or dict(version=1, keys={}, votes={})
        vote = f"{clean['session']}|{creator}/{key}|{clean['work_kind']}"
        if vote in store['votes']:
            return _refused('already_rated')
        skey = _store_key(creator, key, clean['work_kind'], clean['size_group'])
        entry = store['keys'].get(skey)
        if entry is None:
            _trim(store['keys'], MAX_KEYS - 1, lambda name: store['keys'][name]['at'])
            entry = store['keys'][skey] = dict(w=0.0, wr=0.0, at=now, family=clean['family'],
                                               sessions=[], comments=[], last_rater=None)
        factor = _factor(entry['at'], now)
        entry['w'] = entry['w'] * factor
        entry['wr'] = entry['wr'] * factor
        entry['at'] = now
        rater = clean['rater']
        weight = SAME_LAB_WEIGHT if rater is None or rater[0] == creator else 1.0
        entry['w'] += weight
        entry['wr'] += weight * clean['rating']
        entry['family'] = clean['family']
        if clean['session'] not in entry['sessions']:
            entry['sessions'] = (entry['sessions'] + [clean['session']])[-MAX_SESSIONS:]
        if clean['comment']:
            entry['comments'] = (entry['comments'] + [clean['comment']])[-MAX_COMMENTS:]
        entry['last_rater'] = None if rater is None else f'{rater[0]}/{rater[1]}'
        _trim(store['votes'], MAX_VOTES - 1, store['votes'].get)
        store['votes'][vote] = now
        storage._write(path, store)
    return dict(state='recorded')


def record(card, *, state_dir, now):
    """Store one card: dict(state='recorded') or dict(state='refused', reason=...). Never raises."""
    try:
        return _record(card, Path(state_dir), now)
    except (OSError, ValueError, TypeError, RecursionError):
        return _refused('store_unsafe')


def adjustment(store, *, key, creator, family, work_kind, size_group, anchor, now):
    """Decayed card record for one model, kind and size: dict(sessions=int, adjustment=float)."""
    own_key = _store_key(creator, key, work_kind, size_group)
    sessions = set()
    own_w = own_wr = family_w = family_wr = 0.0
    for name, entry in (store or {}).get('keys', {}).items():
        parsed = _parse_key(name)
        if parsed is None:
            continue
        lab, model, kind, size = parsed
        factor = _factor(entry['at'], now)
        if name == own_key:
            own_w, own_wr = entry['w'] * factor, entry['wr'] * factor
        elif lab == creator and kind == work_kind and size == size_group \
                and entry['family'] == family and scores.version(model) < scores.version(key):
            family_w += entry['w'] * factor
            family_wr += entry['wr'] * factor
        else:
            continue
        sessions.update(entry['sessions'])
    counted = len(sessions)
    n = own_w + FAMILY_WEIGHT * family_w
    if type(anchor) not in (int, float) or not math.isfinite(anchor) or anchor < 0 \
            or n <= 0 or counted < MIN_SESSIONS:
        return dict(sessions=counted, adjustment=0.0)
    mean = (own_wr + FAMILY_WEIGHT * family_wr) / n
    d = (mean - 3) / 3 if mean < 3 else (mean - 3) / 2
    cap = 0.10 * anchor
    return dict(sessions=counted, adjustment=max(-cap, min(cap, cap * d * n / (n + 5))))
