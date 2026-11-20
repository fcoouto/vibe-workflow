"""Only the three OpenCode subscription paths; configuration is not qualification."""
import json
from pathlib import Path

from runtime import routing

SUBSCRIPTION = 'included_subscription'
MODEL_DEMAND = 'per_model'
WINDOW_UNIT = 'utilization_thousandths'
RULES = Path(__file__).resolve().parents[1] / 'config/routes.json'


def path_of(route_id):
    return route_id.split(':', 1)[0] if type(route_id) is str else ''


def load_rules():
    return json.loads(RULES.read_text())


def routes(models):
    if type(models) is not list or len(models) > 512:
        raise ValueError('invalid_request')
    rules = {rule['provider']: rule for rule in load_rules()['paths']}
    found = []
    for item in models:
        if type(item) is not dict or not routing._text(item.get('provider')) or not routing._text(item.get('id')) \
                or not routing._optional(item.get('model'), routing._text) \
                or not routing._optional(item.get('family'), routing._text) \
                or not routing._optional(item.get('context'), routing._amount) \
                or not routing._optional(item.get('released'), routing._amount):
            raise ValueError('invalid_request')
        variants = item.get('variants')
        if type(variants) is not list or len(variants) > 32 or not all(routing._text(name) for name in variants):
            raise ValueError('invalid_request')
        rule = rules.get(item['provider'])
        if rule:
            found.append(dict(rule, route=rule['path'] + ':' + item['id'], model=item['id'], model_id=item['model'],
                              family=item['family'], variants=list(variants), context_limit=item['context'],
                              released=item['released'] or 0, demand=None))
    return found
