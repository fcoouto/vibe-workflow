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
ACCOUNT = ('5h', '7d')
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


def _instant(at):
    if type(at) is int:
        return routing._amount(at)
    return type(at) is float and math.isfinite(at) and at >= 0


def _times(started, received):
    return _instant(started) and _instant(received) and started <= received


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


def _settled(readings):
    """`(percent, reset)` when every reading of one window names one reset and all percentages agree, else None."""
    percents = [percent for percent, _ in readings]
    if len({reset for _, reset in readings}) != 1 or None in percents or not _agree(min(percents), max(percents)):
        return None
    return max(percents), readings[0][1]


def _key(value):
    return value in ACCOUNT or type(value) is str and re.fullmatch('(family|model):[a-z0-9-]+', value) is not None


def _window(key):
    return LENGTHS['5h'] if key == '5h' else LENGTHS['7d']


def _plausible(earliest, latest, now):
    """Observation times that could be real: nonnegative, ordered, none after `now`, `latest` may round up a second."""
    return routing._amount(earliest) and routing._amount(latest) and earliest <= latest and earliest <= now \
        and latest <= now + 1


def _reset(key, reset, latest):
    """A reset no earlier than the observation's `latest` (equal allowed) and within the window plus the margin."""
    return routing._amount(reset) and latest <= reset <= latest + _window(key) + storage.CLOCK_MARGIN


def _valid_meter(key, value, latest):
    return _key(key) and type(value) is dict and set(value) >= {'exhausted', 'reset_at'} \
        and type(value['exhausted']) is bool and _reset(key, value['reset_at'], latest)


def _entry(key, value, now):
    return type(value) is dict and set(value) == {'exhausted', 'reset_at', 'earliest', 'latest'} \
        and _plausible(value['earliest'], value['latest'], now) and _valid_meter(key, value, value['latest'])


def _inventory(value, now):
    return type(value) is dict and set(value) == {'scopes', 'earliest', 'latest'} \
        and type(value['scopes']) is list and all(_key(key) and key not in ACCOUNT for key in value['scopes']) \
        and _plausible(value['earliest'], value['latest'], now)


def _sampled(sample, now):
    return type(sample) is dict and type(sample.get('meters')) is dict \
        and type(sample.get('scopes_complete')) is bool \
        and _plausible(sample.get('earliest'), sample.get('latest'), now) \
        and all(_valid_meter(key, value, sample['latest']) and set(value) == {'exhausted', 'reset_at'}
                for key, value in sample['meters'].items())


def _live(key, entry, latest, now):
    # A short healthy reset ends no ordering proof: an overlapping positive can name a later reset.
    return now < (entry['reset_at'] if entry['exhausted'] else latest + _window(key) + storage.CLOCK_MARGIN)


def _kept(record, now):
    """Plausible pauses, recovery watermarks and absence inventory, within their time and size bounds."""
    value = record if type(record) is dict else {}
    raw = value.get('meters') if type(value.get('meters')) is dict else {}
    inventory = value.get('inventory')
    scopes = inventory.get('scopes') if type(inventory) is dict else None
    if len(raw) - sum(key in raw for key in ACCOUNT) > MAX_SCOPES or type(scopes) is list and len(scopes) > MAX_SCOPES:
        raw, inventory = {key: raw[key] for key in ACCOUNT if key in raw}, None
    meters = {key: dict(entry) for key, entry in raw.items()
              if _entry(key, entry, now) and _live(key, entry, entry['latest'], now)}
    if not _inventory(inventory, now) or now >= inventory['latest'] + LENGTHS['7d'] + storage.CLOCK_MARGIN:
        inventory = None
    return meters, inventory


def _capped(meters, before):
    """Two account-wide and eight scoped keys: positives already held, new positives, then recent healthy entries."""
    def rank(key):
        entry = meters[key]
        if not entry['exhausted']:
            return 2, -entry['latest'], key
        return (0 if before.get(key, {}).get('exhausted') else 1), -entry['latest'], key
    ranked = sorted(meters, key=rank)
    keep = [key for key in ranked if key in ACCOUNT][:len(ACCOUNT)] + \
        [key for key in ranked if key not in ACCOUNT][:MAX_SCOPES]
    return {key: meters[key] for key in keep}


def merge(previous, sample, *, now):
    """The record after one sample: validated unexpired meters and a live absence inventory. A sample changes an
    entry only where its observation is provably newer (`earliest` after the entry's `latest`), and it may override
    an absence fence only where it is newer than the inventory. A complete inventory clears a scoped mark it omits
    only when newer. A `None` or malformed sample changes nothing."""
    meters, inventory = _kept(previous, now)
    before = dict(meters)
    if not _sampled(sample, now):
        return dict(meters=meters, inventory=inventory)
    earliest, latest = sample['earliest'], sample['latest']
    for key, value in sample['meters'].items():
        old = meters.get(key)
        if not _live(key, value, latest, now) or old is not None and earliest <= old['latest']:
            continue
        if inventory is not None and key not in ACCOUNT and key not in inventory['scopes'] \
                and earliest <= inventory['latest']:
            continue
        meters[key] = dict(value, earliest=earliest, latest=latest)
    if sample['scopes_complete'] and (inventory is None or earliest > inventory['latest']):
        scopes = sorted(key for key in sample['meters'] if key not in ACCOUNT)
        for key in [key for key in meters if key not in ACCOUNT and key not in scopes]:
            if earliest > meters[key]['latest']:
                del meters[key]
        inventory = dict(scopes=scopes, earliest=earliest, latest=latest)
    return dict(meters=_capped(meters, before), inventory=inventory)


def remaining(record, model, models, *, now):
    """Seconds until the latest reset of a confirmed exhausted meter that covers `model`, or None."""
    meters, _ = _kept(record, now)
    left = [entry['reset_at'] - now for key, entry in meters.items()
            if entry['exhausted'] and covers(key, model, models)]
    return max(left) if left else None


def _bounded(reset, received, length):
    return received < reset <= received + length + storage.CLOCK_MARGIN


def evidence(use, models, *, started, received, include_usage=False):
    """`{earliest, latest, meters, scopes_complete}` from one usage answer, or None when it confirms nothing.

    `started` and `received` are epoch seconds. Meters are `5h`, `7d`, `family:<name>` or `model:<resolved>`, each
    `{exhausted, reset_at}`. `scopes_complete` is False when a present scoped entry yields no valid meter, or a row is
    unrecognized or cannot be attributed; account windows never affect it. With `include_usage`, a `usage` element
    adds the same readings as ranking meters `{used_percent, earliest, latest, reset_at, duration, discrete}`."""
    if not _times(started, received) or type(use) is not dict or use.get('rate_limits_available') is not True:
        return None
    rate = use.get('rate_limits')
    if type(rate) is not dict or type(rate.get('limits')) is not list:
        return None
    usage = _usage()
    catalog = _catalog(models)
    incomplete = any(usage._meter_like(rate[key]) for key in set(rate) - usage.KNOWN_RATE) \
        or any(rate.get(key) is not None for key in usage.SCOPED_METERS + usage.UNKNOWN_METERS)
    windows, broken, claims = {}, set(), {}
    for key, (window, _) in storage.RATE_WINDOWS.items():
        if rate.get(key) is None:
            continue
        try:
            windows[window] = _reading(rate[key], 'utilization')
            claims[window] = [windows[window]]
        except usage.ReadError:
            broken.add(window)
    listed = []
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
            else:
                claims[hits[0]].append((percent, reset))
        elif type(item) is dict and kind == 'weekly_all' and 'scope' in item and scope is None:
            try:
                claims.setdefault('7d', []).append(_reading(item, 'percent'))
            except usage.ReadError:
                broken.add('7d')
        elif type(item) is dict and kind == 'weekly_scoped' and type(scope) is dict \
                and set(scope) == {'model'}:
            model = scope['model']
            listed.append((model.get('display_name') if type(model) is dict else None, item, 'percent'))
        else:
            incomplete = True
    if 'model_scoped' in rate:
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
    meters, settled_by = {}, {}
    for meter, entries in scopes.items():
        if meter in excluded:
            continue
        settled = _settled(entries)
        if settled is None:
            incomplete = True
            continue
        meters[meter] = dict(exhausted=settled[0] >= 100, reset_at=settled[1])
        settled_by[meter] = settled
    for window, readings in claims.items():
        settled = None if window in broken else _settled(readings)
        if settled and settled[1] is not None and _bounded(settled[1], received, LENGTHS[window]):
            meters[window] = dict(exhausted=settled[0] >= 100, reset_at=settled[1])
            settled_by[window] = settled
    earliest, latest = max(0, math.floor(started) - usage.CACHE_CEILING), math.ceil(received)
    result = dict(earliest=earliest, latest=latest, meters=meters, scopes_complete=not incomplete)
    if include_usage:
        result['usage'] = {key: dict(used_percent=percent, earliest=earliest, latest=latest, reset_at=reset,
                                     duration=_window(key), discrete=True)
                           for key, (percent, reset) in settled_by.items()}
    return result
