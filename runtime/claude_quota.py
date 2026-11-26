"""Claude quota evidence from one native usage answer, independent of snapshot completeness.

Pure: the caller supplies the answer, the native catalog records and the observation times. An account window (5h, 7d)
or a model or family scope becomes a meter only with a whole-second reset strictly after receipt and within its window
plus the clock margin. A row that cannot be attributed or classified leaves its meter unknown or makes the scope
inventory incomplete; it never claims headroom. Account binding, storage and selection belong to the caller.
"""
import math
import re

from runtime import routing, storage

MAX_SCOPES = 8  # recognized scope IDs beyond this: the inventory cannot be complete
LENGTHS = {window: length for window, length in storage.RATE_WINDOWS.values()}


def _usage():
    from runtime import claude_usage  # deferred: the reader may import this module at integration
    return claude_usage


def _norm(value):
    if not routing._text(value):
        return None
    words = re.findall('[a-z0-9]+', value.casefold())
    if len(words) > 1 and words[0] == 'claude':
        del words[0]
    return '-'.join(words) or None


def _family(name):
    return name.split('-')[0]


def _catalog(models):
    """`[(alias, resolved)]` normalized, with `resolved` None when the record has none; None if any record is bad."""
    if type(models) is not list:
        return None
    found = []
    for item in models:
        if type(item) is not dict or set(item) != {'alias', 'resolved', 'efforts'} \
                or type(item['efforts']) is not list:
            return None
        alias = _norm(item['alias'])
        resolved = _norm(item['resolved']) if item['resolved'] is not None else None
        if alias is None or item['resolved'] is not None and resolved is None:
            return None
        found.append((alias, resolved))
    return found


def _resolve(model, catalog):
    """The one normalized resolved ID that `model` names by alias or resolved ID; None without exactly one."""
    name = _norm(model)
    if name is None or catalog is None:
        return None
    matches = [resolved for alias, resolved in catalog if name in (alias, resolved)]
    if not matches or None in matches or len(set(matches)) != 1:
        return None
    return matches[0]


def _scope(label, catalog):
    """The meter ID a scope label names: `model:<resolved>` for a versioned label, `family:<name>` for a bare one."""
    name = _norm(label)
    if name is None:
        return None
    tokens = name.split('-')
    if not [token for token in tokens if token.isalpha() and token != 'claude']:
        return None
    if any(token.isdigit() for token in tokens):
        return 'model:' + name if _resolve(name, catalog) == name else None
    hits = [resolved for alias, resolved in catalog or [] if alias == name]
    if len(tokens) == 1 and len(set(hits)) == 1 and None not in hits and _family(hits[0]) == name:
        return 'family:' + name
    return None


def covers(meter_id, model, models):
    """Whether meter `meter_id` limits the route's native `model`; account windows (5h, 7d) limit every model."""
    if meter_id in ('5h', '7d'):
        return routing._text(model)
    kind, _, token = meter_id.partition(':') if type(meter_id) is str else ('', '', '')
    resolved = _resolve(model, _catalog(models))
    if not token or resolved is None:
        return False
    if kind == 'model':
        return token == resolved
    return kind == 'family' and token == _family(resolved)


def _times(started, received):
    return all(type(at) in (int, float) and math.isfinite(at) and at >= 0 for at in (started, received)) \
        and started <= received


def _reading(item, value):
    """`(percent, reset)` of one meter-shaped item; reset is None only for an explicit idle 0%."""
    usage = _usage()
    if type(item) is not dict:
        raise usage.ReadError('claude_usage_invalid')
    percent = usage._percent(item.get(value))
    if percent == 0 and 'resets_at' in item and item['resets_at'] is None:
        return percent, None
    return percent, usage._time(item.get('resets_at'))


def _stamp(item):
    """The whole-second reset of a row whose percentage is malformed, or None when its reset is unreadable too."""
    usage = _usage()
    try:
        return usage._time(item.get('resets_at'))
    except usage.ReadError:
        return None


def _agree(first, second):
    if first >= 100 or second >= 100:
        return first == second
    return abs(first - second) <= 1


def _bounded(reset, received, length):
    return received < reset <= received + length + storage.CLOCK_MARGIN


def evidence(use, models, *, started, received):
    """`{earliest, latest, meters, scopes_complete}` from one usage answer, or None when it confirms nothing.

    `started` and `received` are epoch seconds. Meters are `5h`, `7d`, `family:<name>` or `model:<resolved>`, each
    `{exhausted, reset_at}`. `scopes_complete` is False when a present scoped entry yields no valid meter, or a row is
    unrecognized or cannot be attributed; account windows never affect it."""
    if not _times(started, received) or type(use) is not dict or use.get('rate_limits_available') is not True:
        return None
    rate = use.get('rate_limits')
    if type(rate) is not dict or type(rate.get('limits')) is not list:
        return None
    usage = _usage()
    catalog = _catalog(models)
    incomplete = any(usage._meter_like(rate[key]) for key in set(rate) - usage.KNOWN_RATE) \
        or any(rate.get(key) is not None for key in usage.SCOPED_METERS + usage.UNKNOWN_METERS)
    windows, broken = {}, set()
    for key, (window, _) in storage.RATE_WINDOWS.items():
        if rate.get(key) is None:
            continue
        try:
            windows[window] = _reading(rate[key], 'utilization')
        except usage.ReadError:
            broken.add(window)
    named, conflicts, listed = [], set(), []
    for item in rate['limits']:
        scope = item.get('scope') if type(item) is dict else None
        kind = item.get('kind') if type(item) is dict else None
        if type(item) is dict and kind is None and scope is None:
            try:
                percent, reset = _reading(item, 'percent')
            except usage.ReadError:
                percent, reset = None, _stamp(item)
                incomplete = True
            hits = [window for window, (_, at) in windows.items() if at == reset]
            if len(hits) != 1:
                incomplete = True
            elif percent is None or not _agree(windows[hits[0]][0], percent):
                conflicts.add(hits[0])
        elif type(item) is dict and kind == 'weekly_all' and scope is None:
            try:
                named.append(_reading(item, 'percent'))
            except usage.ReadError:
                broken.add('7d')
        elif type(item) is dict and kind == 'weekly_scoped' and type(scope) is dict \
                and scope.get('model') is not None:
            model = scope['model']
            listed.append((model.get('display_name') if type(model) is dict else None, item, 'percent'))
        else:
            incomplete = True
    seven = named + ([windows['7d']] if '7d' in windows else [])
    if '7d' in broken or len({reset for _, reset in seven}) > 1 \
            or any(not _agree(first, second) for first, _ in seven for second, _ in seven):
        conflicts.add('7d')
    if seven:
        windows['7d'] = max(percent for percent, _ in seven), seven[0][1]
    if rate.get('model_scoped') is not None:
        if type(rate['model_scoped']) is not list:
            incomplete = True
        else:
            listed += [(item.get('display_name') if type(item) is dict else None, item, 'utilization')
                       for item in rate['model_scoped']]
    scopes, excluded, ids = {}, set(), set()
    for label, item, value in listed:
        meter = _scope(label, catalog)
        try:
            percent, reset = _reading(item, value)
        except usage.ReadError:
            percent, reset = None, None
        usable = reset is not None and _bounded(reset, received, LENGTHS['7d'])
        if meter is None or not usable:
            incomplete = True
        if meter is not None:
            ids.add(meter)
            if usable:
                scopes.setdefault(meter, []).append((percent, reset))
            else:
                excluded.add(meter)
    if len(ids) > MAX_SCOPES:
        incomplete, excluded = True, ids
    meters = {}
    for meter, entries in scopes.items():
        percents, resets = [percent for percent, _ in entries], {reset for _, reset in entries}
        if meter in excluded:
            continue
        if len(resets) != 1 or not all(_agree(first, second) for first in percents for second in percents):
            incomplete = True
            continue
        meters[meter] = dict(exhausted=max(percents) >= 100, reset_at=resets.pop())
    for window, (percent, reset) in windows.items():
        if reset is not None and window not in conflicts and _bounded(reset, received, LENGTHS[window]):
            meters[window] = dict(exhausted=percent >= 100, reset_at=reset)
    return dict(earliest=max(0, math.floor(started) - usage.CACHE_CEILING), latest=math.ceil(received),
                meters=meters, scopes_complete=not incomplete)
