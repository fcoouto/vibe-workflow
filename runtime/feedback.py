"""Feedback cards between models: one bounded private store and the capped adjustment it feeds.

Never raises: a damaged store reads as empty, an unsafe one refuses writes.
"""
from pathlib import Path
import math
import re

from runtime import routing
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
WEIGHT_MAX = 10 ** 6  # a sanity bound on stored totals, far above any real count
AT_MAX = 10 ** 15  # a sanity bound on stored times (seconds)
TOTAL_TOLERANCE = 1e-6
LAB = re.compile(r'~?[a-z0-9][a-z0-9-]{0,63}')
MODEL = re.compile(r'[a-z0-9][a-z0-9-]{0,127}')
FAMILY = re.compile(r'[a-z0-9-]{0,128}')
SESSION = re.compile(r'[0-9a-fA-F]{16,64}')


def _text(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _bounded(value, limit):
    """A finite number in [0, limit]. Range-checked first, so huge ints never reach float conversion."""
    return type(value) in (int, float) and 0 <= value <= limit


def _at(value):
    return type(value) is int and 0 <= value <= AT_MAX


def _factor(at, now):
    return 0.5 ** (max(0, now - at) / HALF_LIFE)


def _store_key(creator, key, work_kind, size_group):
    return f'{creator}/{key}|{work_kind}|{size_group}'


def _pair_ok(text):
    """A `creator/key` string with valid parts."""
    lab, slash, model = text.partition('/') if type(text) is str else ('', '', '')
    return bool(slash and LAB.fullmatch(lab) and MODEL.fullmatch(model))


def _parse_key(name):
    """(lab, model key, work kind, size group) of a stored key, or None when malformed."""
    parts = name.split('|') if type(name) is str else []
    if len(parts) != 3 or not _pair_ok(parts[0]) or parts[1] not in routing.WORK_KINDS \
            or parts[2] not in SIZE_GROUPS:
        return None
    lab, _, model = parts[0].partition('/')
    return lab, model, parts[1], parts[2]


def _entry_ok(name, entry):
    parsed = _parse_key(name)
    if parsed is None or type(entry) is not dict:
        return False
    w, wr, sessions, comments = entry.get('w'), entry.get('wr'), entry.get('sessions'), entry.get('comments')
    return _bounded(w, WEIGHT_MAX) and _bounded(wr, 5 * WEIGHT_MAX) \
        and wr <= 5 * w + TOTAL_TOLERANCE * (1 + w) \
        and _at(entry.get('at')) \
        and _text(FAMILY, entry.get('family')) and entry['family'] == scores.family_key(parsed[1]) \
        and type(sessions) is list and len(sessions) <= MAX_SESSIONS \
        and all(_text(SESSION, item) for item in sessions) \
        and type(comments) is list and len(comments) <= MAX_COMMENTS \
        and all(type(item) is str and len(item) <= COMMENT_MAX for item in comments) \
        and (entry.get('last_rater') is None or _pair_ok(entry['last_rater']))


def _vote_ok(name, at):
    parts = name.split('|') if type(name) is str else []
    return len(parts) == 3 and _text(SESSION, parts[0]) and _pair_ok(parts[1]) \
        and parts[2] in routing.WORK_KINDS and _at(at)


def _valid(value):
    """The store when every field has its specified shape and bound, else None (the caller treats it as damaged)."""
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1 \
            or type(value.get('keys')) is not dict or type(value.get('votes')) is not dict:
        return None
    keys, votes = value['keys'], value['votes']
    if len(keys) > MAX_KEYS or len(votes) > MAX_VOTES:
        return None
    if not all(_entry_ok(name, entry) for name, entry in keys.items()):
        return None
    if not all(_vote_ok(name, at) for name, at in votes.items()):
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
    """The store, or None when missing, damaged, unsafe or unreadable. Never raises."""
    try:
        return _read(Path(state_dir) / FILE)
    except Exception:  # any read problem means no adjustment, never an error in a pick
        return None


def _card(card):
    """The card's fields as a dict, or None when any field is missing or out of range."""
    if type(card) is not dict or type(card.get('rated')) is not dict:
        return None
    rated, rater = card['rated'], card.get('rater')
    session, comment = card.get('session'), card.get('comment')
    if not (_text(LAB, rated.get('creator')) and _text(MODEL, rated.get('key'))
            and _text(FAMILY, rated.get('family')) and rated['family'] == scores.family_key(rated['key'])
            and card.get('work_kind') in routing.WORK_KINDS
            and card.get('size_group') in SIZE_GROUPS and type(card.get('rating')) is int
            and 0 <= card['rating'] <= 5 and _text(SESSION, session)
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
    except storage.StorageError as error:
        return _refused('store_full' if str(error) == 'observation_store_full' else 'store_unsafe')
    except Exception:  # never raise; the reason names the failure class without hiding it as "unsafe"
        return _refused('store_error')


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
