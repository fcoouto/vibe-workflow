"""Only the three OpenCode subscription paths; configuration is not qualification."""
import json
from pathlib import Path
import re

from runtime import routing

SUBSCRIPTION = 'included_subscription'
MODEL_DEMAND = 'per_model'
WINDOW_UNIT = 'utilization_thousandths'
STRATEGY_ID = re.compile(r'[A-Za-z0-9._-]{1,64}')
RULES = Path(__file__).resolve().parents[1] / 'config/routes.json'


def path_of(route_id):
    return route_id.split(':', 1)[0] if type(route_id) is str else ''


def load_rules():
    return json.loads(RULES.read_text())


def generate(enabled, table):
    rules = load_rules()['paths']
    if type(enabled) is not list or len(enabled) > 3 or len(set(enabled)) != len(enabled) \
            or not all(type(path) is str and any(rule['path'] == path for rule in rules) for path in enabled):
        raise ValueError('configuration_invalid')
    rows = table.get('rows')
    if type(rows) is not dict or len(rows) > 256:
        raise ValueError('configuration_invalid')
    strategies = {}
    for name, entries in rows.items():
        if type(name) is not str or type(entries) is not list or len(entries) > 256:
            raise ValueError('configuration_invalid')
        for item in entries:
            required = {'strategy_id', 'provider', 'model', 'effort', 'harness'}
            if type(item) is not dict or not required <= set(item) \
                    or set(item) - required - {'context_limit'} \
                    or not all(routing._text(item[key]) for key in required) \
                    or not STRATEGY_ID.fullmatch(item['strategy_id']) \
                    or not routing._optional(item.get('context_limit'), routing._amount):
                raise ValueError('configuration_invalid')
            sid = item['strategy_id']
            if sid in strategies and strategies[sid] != item:
                raise ValueError('configuration_invalid')
            strategies[sid] = item
    routes = []
    for item in strategies.values():
        for rule in rules:
            if rule['path'] in enabled and (rule['provider'], rule['harness']) == (item['provider'], item['harness']):
                routes.append(dict(rule, route=rule['path'] + ':' + item['strategy_id'],
                                   strategy_id=item['strategy_id'], model=item['model'], effort=item['effort'],
                                   demand=None))
    return routes
