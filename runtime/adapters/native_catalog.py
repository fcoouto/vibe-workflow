"""Native client catalogs for Smart Routing's native bridge (F4.6): Codex native subagents and Claude Code Agent.

A native catalog lists, for one client, the model aliases its own launch accepts and the efforts that launch can
express, bound to the client's version, connection and effective invocation controls. It never comes from a request
or another client's catalog, and no model name is parsed.

Sources, all unqualified today:
- `current_binding(client)`: the verified `{client_version, connection, controls}` of the client now running. No
  zero-inference source of it is qualified (a standalone initialization does not prove another coordinator's effective
  settings), so it answers None and the bridge refuses (`native_binding_unverified`).
- The stored snapshot `{binding, models: [{alias, resolved, efforts}]}` beside the routing settings, written only by a
  qualified bounded acquisition outside served preparation (`store_snapshot`). None is qualified or run here: with no
  snapshot the bridge refuses (`native_catalog_unavailable`). A snapshot is reused only while its binding equals the
  current one (`native_binding_changed` otherwise); no process starts on a `prepare`.
- `from_claude_initialize` and `from_codex_models` map the documented payloads (Claude initialization `models[]`:
  `value`, `resolvedModel`, `supportsEffort`, `supportedEffortLevels`; the Codex catalog `{model, supported_efforts}`)
  into snapshot models, for that acquisition. Parsing a payload is not qualifying its source.
"""
from runtime import routing, routing_launch, routing_observations as collector
from runtime.software_versions import MINIMUM_VERSIONS, at_least

SNAPSHOT_FILE = 'native-catalogs.json'
HOSTS = {'claude-code': 'claude_native', 'codex': 'codex_native'}
BINDING_FIELDS = ('client_version', 'connection', 'controls')
MAX_MODELS = 32


def current_binding(client):
    """The verified binding of the running `client`, or None: no source of it is qualified."""
    return None


def _path(settings_path):
    return collector.window_path(settings_path).parent / SNAPSHOT_FILE


def _binding_ok(value):
    return type(value) is dict and set(value) == set(BINDING_FIELDS) and all(routing._text(value[key])
                                                                             for key in BINDING_FIELDS)


def _model_ok(value):
    return (type(value) is dict and set(value) == {'alias', 'resolved', 'efforts'} and routing._text(value['alias'])
            and routing._optional(value['resolved'], routing._text) and type(value['efforts']) is list
            and len(value['efforts']) <= 16 and all(routing._text(item) for item in value['efforts']))


def _snapshot_ok(value):
    return (type(value) is dict and set(value) == {'binding', 'models'} and _binding_ok(value['binding'])
            and type(value['models']) is list and 0 < len(value['models']) <= MAX_MODELS
            and all(_model_ok(item) for item in value['models']))


def store_snapshot(settings_path, client, snapshot):
    """Store `client`'s snapshot (the qualified acquisition's writer; ValueError when malformed)."""
    if client not in HOSTS or not _snapshot_ok(snapshot):
        raise ValueError('native_catalog_invalid')
    with collector._locked(_path(settings_path)) as path:
        value = collector._read(path) or dict(version=1, clients={})
        value['clients'][client] = snapshot
        collector._write(path, value)


def stored(settings_path, client):
    """`client`'s stored snapshot, or None when absent or unusable."""
    try:
        found = collector._read(_path(settings_path)).get('clients', {}).get(client)
    except (OSError, ValueError, AttributeError):
        return None
    return found if _snapshot_ok(found) else None


def identities(client, models):
    """The host's catalog as route identities: every (alias, expressible effort), or the alias with no effort."""
    host = HOSTS[client]
    return [routing_launch.route_identity(host, model=item['alias'], effort=effort)
            for item in models for effort in (item['efforts'] or [None])]


def catalog(settings_path, client):
    """(route identities, None) from the stored snapshot bound to the current client, or (None, reason)."""
    binding = current_binding(client)
    if not _binding_ok(binding):
        return None, 'native_binding_unverified'
    if at_least(binding['client_version'], MINIMUM_VERSIONS[client]) is False:
        return None, 'native_version_too_old'
    snapshot = stored(settings_path, client)
    if snapshot is None:
        return None, 'native_catalog_unavailable'
    if any(snapshot['binding'][key] != binding[key] for key in ('connection', 'controls')):
        return None, 'native_binding_changed'
    return identities(client, snapshot['models']), None


def resolved_of(settings_path, client, alias):
    """The snapshot's resolved model id for `alias`, or None (unknown, or the client reports none)."""
    snapshot = stored(settings_path, client)
    return next((item['resolved'] for item in (snapshot or {}).get('models', []) if item['alias'] == alias), None)


def from_claude_initialize(response):
    """Snapshot models from a Claude initialization response's `models[]`; malformed entries are left out."""
    found = []
    for item in (response or {}).get('models', []) if type(response) is dict else []:
        if type(item) is not dict or not routing._text(item.get('value')):
            continue
        levels = item.get('supportedEffortLevels') if item.get('supportsEffort') is True else []
        efforts = [level for level in levels if routing._text(level)] if type(levels) is list else []
        resolved = item.get('resolvedModel') if routing._text(item.get('resolvedModel')) else None
        found.append(dict(alias=item['value'], resolved=resolved, efforts=efforts))
    return found[:MAX_MODELS]


def from_codex_models(models):
    """Snapshot models from the Codex native catalog `[{model, supported_efforts}]`."""
    found = []
    for item in models if type(models) is list else []:
        if type(item) is not dict or not routing._text(item.get('model')):
            continue
        efforts = item.get('supported_efforts')
        found.append(dict(alias=item['model'], resolved=None,
                          efforts=[e for e in efforts if routing._text(e)] if type(efforts) is list else []))
    return found[:MAX_MODELS]
