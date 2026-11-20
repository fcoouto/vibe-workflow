"""Map runnable models to OpenRouter's public Artificial Analysis scores, cached privately on disk."""
import http.client
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

from runtime import routing, storage

URL = 'https://openrouter.ai/api/v1/models'
HOST = 'openrouter.ai'
ATTRIBUTION = 'Scores: Artificial Analysis via OpenRouter'
CREATOR = {'opencode-claude-subscription': 'anthropic', 'opencode-chatgpt-subscription': 'openai'}
MAX_MODELS = 5000
CACHE = 'openrouter-models.json'
GAP = 900
CEILING = 7 * 86400
RETRY = (3600, 14400, 43200, 86400)
BACKOFF = (300, 3600)
FIRST_TIMEOUT = 5.0
MAX_BYTES = 5 << 20
CHUNK = 1 << 16
LOCK = '.scores.lock'


def normalize(model_id):
    return re.sub(r'[^a-z0-9]+', '-', model_id.lower().rsplit('/', 1)[-1]).strip('-')


def family_key(key):
    return '-'.join(part for part in key.split('-') if not part.isdigit())


def version(key):
    return tuple(int(part) for part in key.split('-') if part.isdigit())


def _price(value):
    if value is None:
        return None
    try:
        number = float(value) * 1e6
    except (TypeError, ValueError):
        raise ValueError('scores_invalid') from None
    if type(value) is bool or not math.isfinite(number) or number < 0:
        raise ValueError('scores_invalid')
    return round(number, 6)


def _score(item):
    value = item.get('benchmarks')
    for name in ('artificial_analysis', 'intelligence_index'):
        value = value.get(name) if type(value) is dict else None
    if value is None:
        return None
    if type(value) not in (int, float) or not routing._finite(value) or not 0 <= value <= 100:
        raise ValueError('scores_invalid')
    return float(value)


def parse_openrouter(raw, previous_scored=0):
    try:
        data = json.loads(raw)['data']
    except (ValueError, TypeError, KeyError):
        raise ValueError('scores_invalid') from None
    if type(data) is not list or len(data) > MAX_MODELS:
        raise ValueError('scores_invalid')
    models, ambiguous = {}, set()
    for item in data:
        model_id = item.get('id') if type(item) is dict else None
        if not routing._text(model_id):
            continue
        creator, _, rest = model_id.partition('/')
        key = normalize(rest)
        pricing = item.get('pricing')
        if not creator or not key or not (pricing is None or type(pricing) is dict):
            continue
        pricing = pricing or {}
        created = item.get('created')
        try:
            entry = dict(
                key=key, creator=creator, family=family_key(key), score=_score(item),
                input=_price(pricing.get('prompt')), output=_price(pricing.get('completion')),
                released=created if routing._amount(created) else 0)
        except ValueError:
            continue
        name = f'{creator}/{key}'
        if name in models:
            ambiguous.add(name)
        models[name] = entry
    for name in ambiguous:
        del models[name]
    if sum(entry['score'] is not None for entry in models.values()) * 2 < previous_scored:
        raise ValueError('scores_invalid')
    return {'models': models}


def _result(entry, source, priced):
    own = entry['input'] is not None and entry['output'] is not None
    given = entry if own else priced
    price = routing.price(given['input'], given['output']) \
        if given['input'] is not None and given['output'] is not None else None
    return dict(score=entry['score'], price=price, score_source=source, released=entry['released'])


def lookup(cache, key, creator):
    models = cache.get('models', {})
    if creator is not None:
        own = models.get(f'{creator}/{key}')
    else:
        matches = [entry for entry in models.values() if entry['key'] == key]
        if len(matches) > 1:
            return None
        own = matches[0] if matches else None
    if own is not None and own['score'] is not None:
        return _result(own, 'openrouter', own)
    # A model never borrows from a newer version of itself.
    members = [entry for entry in models.values() if entry['family'] == family_key(key)
               and entry['score'] is not None and creator in (None, entry['creator'])
               and version(entry['key']) <= version(key)]
    if not members:
        return None
    newest = max(members, key=lambda entry: (version(entry['key']), entry['released'], entry['key'], entry['creator']))
    entry = dict(newest, input=own['input'] if own else None, output=own['output'] if own else None,
                 released=newest['released'])
    return _result(entry, 'family', newest)


def load(state_dir):
    """The cache, or None when it is missing or its content is malformed (the next fetch replaces it).
    An unsafe file still raises, so it is never overwritten."""
    try:
        value = storage.read_json(Path(state_dir) / CACHE)
    except storage.StorageError:
        raise
    except (ValueError, RecursionError):
        return None
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1 \
            or type(value.get('models')) is not dict or type(value.get('unscored')) is not dict \
            or not all(type(value.get(key)) is int for key in ('failures', 'fetched_at', 'attempt_at')) \
            or value['failures'] < 0:
        return None
    return value


def _scored(cache, key):
    return any(entry['key'] == key and entry['score'] is not None for entry in cache['models'].values())


def due(cache, wanted, now):
    if cache is None:
        return True
    failures = cache['failures']
    wait = max(GAP, min(BACKOFF[0] << min(failures - 1, 8), BACKOFF[1]) if failures else 0)
    if 0 <= now - cache['attempt_at'] < wait:
        return False
    if not 0 <= now - cache['fetched_at'] <= CEILING:
        return True
    for key in wanted:
        if _scored(cache, key):
            continue
        retry = cache['unscored'].get(key)
        if retry is None or retry['next_at'] <= now:
            return True
    return False


def _unscored(cache, parsed, wanted, now):
    unscored = {}
    for key in wanted:
        if _scored(parsed, key):
            continue
        old = (cache or {}).get('unscored', {}).get(key)
        if old is not None and old['next_at'] > now:
            unscored[key] = old
            continue
        step = min(old['step'] + 1 if old else 0, len(RETRY))
        unscored[key] = dict(step=step, next_at=now + (RETRY[step] if step < len(RETRY) else CEILING))
    return unscored


def refresh(state_dir, wanted, now, *, fetch=None, timeout=15.0):
    with storage._locked(Path(state_dir) / CACHE, block=False, lock=LOCK) as path:
        if path is None:
            return 'busy'
        cache = load(state_dir)
        if not due(cache, wanted, now):
            return 'skipped'
        base = dict(cache or dict(version=1, fetched_at=0, failures=0, models={}, unscored={}), attempt_at=now)
        storage._write(path, base)
        previous = sum(entry['score'] is not None for entry in base['models'].values())
        try:
            parsed = parse_openrouter((fetch or fetch_openrouter)(timeout), previous)
            storage._write(path, dict(
                version=1, fetched_at=now, attempt_at=now, failures=0, models=parsed['models'],
                unscored=_unscored(cache, parsed, wanted, now)))
            return 'stored'
        except (OSError, ValueError, RecursionError, http.client.HTTPException):
            kept = {key: row for key, row in base['unscored'].items() if key in wanted}
            storage._write(path, dict(base, failures=base['failures'] + 1, unscored=kept))
            return 'failed'


class _SameHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != 'https' or target.hostname != HOST:
            raise OSError('redirect_refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_openrouter(timeout, environ=None, opener=None):
    if (os.environ if environ is None else environ).get('VCW_TEST_HOME'):
        raise OSError('network_disabled')
    request = urllib.request.Request(URL, headers={'User-Agent': 'vibe-workflow-smart-router'})
    deadline = time.monotonic() + timeout
    raw = b''
    with (opener or urllib.request.build_opener(_SameHost)).open(request, timeout=timeout) as response:
        # read1 returns what has arrived, so a slow drip cannot hold one call past the deadline;
        # the socket timeout bounds a single stalled read.
        read = getattr(response, 'read1', response.read)
        while len(raw) <= MAX_BYTES:
            chunk = read(min(CHUNK, MAX_BYTES + 1 - len(raw)))
            if time.monotonic() > deadline:
                raise OSError('scores_timeout')
            if not chunk:
                break
            raw += chunk
    if len(raw) > MAX_BYTES:
        raise OSError('scores_too_large')
    return raw
