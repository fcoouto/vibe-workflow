"""Claude-only account-bound snapshot for scoped-limit interpretation; no adapter."""
from runtime import routing, storage

MAX_MODELS = 32
BINDING_FIELDS = ('client_version', 'connection', 'controls')


def _path(settings_path):
    return storage.window_path(settings_path).parent / 'native-catalogs.json'


def _binding(settings_path, client):
    from runtime import claude_usage
    return claude_usage.binding(settings_path) if client == 'claude-code' else None


def _snapshot_ok(value):
    if type(value) is not dict or set(value) != {'binding', 'models'}:
        return False
    binding, models = value['binding'], value['models']
    return type(binding) is dict and set(binding) == set(BINDING_FIELDS) \
        and all(routing._text(binding[key]) for key in BINDING_FIELDS) \
        and type(models) is list and 0 < len(models) <= MAX_MODELS \
        and all(type(item) is dict and set(item) == {'alias', 'resolved', 'efforts'}
                and routing._text(item['alias']) and routing._optional(item['resolved'], routing._text)
                and type(item['efforts']) is list and len(item['efforts']) <= 16
                and all(routing._text(effort) for effort in item['efforts']) for item in models)


def store_snapshot(settings_path, client, snapshot):
    if client != 'claude-code' or not _snapshot_ok(snapshot):
        raise ValueError('native_catalog_invalid')
    with storage._locked(_path(settings_path)) as path:
        value = storage._read(path) or dict(version=1, clients={})
        value['clients'][client] = snapshot
        storage._write(path, value)


def stored(settings_path, client):
    if client != 'claude-code':
        return None
    try:
        found = storage._read(_path(settings_path)).get('clients', {}).get(client)
    except (OSError, ValueError, AttributeError):
        return None
    return found if _snapshot_ok(found) else None


def from_claude_initialize(response):
    found = []
    for item in (response or {}).get('models', []) if type(response) is dict else []:
        if type(item) is not dict or not routing._text(item.get('value')):
            continue
        levels = item.get('supportedEffortLevels') if item.get('supportsEffort') is True else []
        efforts = [level for level in levels if routing._text(level)] if type(levels) is list else []
        resolved = item.get('resolvedModel') if routing._text(item.get('resolvedModel')) else None
        found.append(dict(alias=item['value'], resolved=resolved, efforts=efforts))
    return found[:MAX_MODELS]
