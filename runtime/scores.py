"""Pure mapping of runnable models to OpenRouter's public Artificial Analysis scores."""
import json
import math
import re

from runtime import routing

URL = 'https://openrouter.ai/api/v1/models'
ATTRIBUTION = 'Scores: Artificial Analysis via OpenRouter'
CREATOR = {'opencode-claude-subscription': 'anthropic', 'opencode-chatgpt-subscription': 'openai'}
MAX_MODELS = 5000


def normalize(model_id):
    return re.sub(r'[^a-z0-9]+', '-', model_id.lower().rsplit('/', 1)[-1]).strip('-')


def family_key(key):
    return '-'.join(part for part in key.split('-') if not part.isdigit())


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
    models = {}
    for item in data:
        model_id = item.get('id') if type(item) is dict else None
        if type(model_id) is not str:
            continue
        if len(model_id) > routing.MAX_ID or not model_id.isprintable():
            raise ValueError('scores_invalid')
        creator, _, rest = model_id.partition('/')
        key = normalize(rest)
        pricing = item.get('pricing')
        if not creator or not key or not (pricing is None or type(pricing) is dict):
            continue
        pricing = pricing or {}
        created = item.get('created')
        models[f'{creator}/{key}'] = dict(
            key=key, creator=creator, family=family_key(key), score=_score(item),
            input=_price(pricing.get('prompt')), output=_price(pricing.get('completion')),
            released=created if routing._amount(created) else 0)
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
    members = [entry for entry in models.values() if entry['family'] == family_key(key)
               and entry['score'] is not None and creator in (None, entry['creator'])]
    if not members:
        return None
    newest = max(members, key=lambda entry: (entry['released'], entry['key'], entry['creator']))
    entry = dict(newest, input=own['input'] if own else None, output=own['output'] if own else None,
                 released=newest['released'])
    return _result(entry, 'family', newest)
