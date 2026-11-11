"""Compile the Smart Routing strategy table from guidance, catalog and benchmark evidence.

Maintenance-time only: the compiler is pure, makes no network or model call and is
never run on spawn paths or hooks. Strategies naming models absent from the live
catalog, unsupported efforts or harnesses, or lacking cited evidence are dropped,
never guessed. Model, effort and harness names appear only as data.

Input shapes (all plain JSON):
  guidance:   [{id, sha256, checked_at, rows:[{work_kind, complexity_band, risk_band,
                strategies:[{model, effort, harness, evidence_refs}]}]}]   # ordered strongest first
  catalog:    [{model, supported_efforts, harnesses?, context_limit?}]     # native catalog shape (runtime/delegation.py)
  benchmarks: [{id, model, harness, work_kind, sha256, checked_at}]        # harness and work_kind are required to match

A row's work kind is one of the task profile template's; a row naming a `role` or `task_family` (the schema-1
shape) is invalid, so republished guidance must drop them. Guidance evidence binds to its own entry: a strategy may cite only the guidance entry whose rows contain it.
A catalog entry without ``harnesses`` accepts any harness. A strategy's ``endpoint`` defaults to its harness.
OpenRouter-specific mapping into these shapes belongs to a thin adapter added later; none exists yet.
An empty or unusable table yields ``no_recommendation`` for callers, never a refusal to delegate.
"""
import hashlib
import json
import re
import time

from runtime import routing, task_profile

SCHEMA = routing.TABLE_SCHEMA
ROW_KEYS = {'work_kind', 'complexity_band', 'risk_band', 'strategies'}
NO_RECOMMENDATION = dict(no_recommendation=True, reason='no_recommendation')
MAX_ROWS = 4096
_TIME = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z')


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, field, limit=256):
    _require(isinstance(value, str) and 0 < len(value) <= limit, 'Invalid ' + field)
    return value


def _list(value, field):
    _require(isinstance(value, list) and len(value) <= MAX_ROWS, 'Invalid ' + field)
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _sha(value, field):
    _require(isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value), 'Invalid ' + field)
    return value


def _catalog(items):
    live = {}
    for item in _list(items, 'catalog'):
        _require(isinstance(item, dict), 'Invalid catalog entry')
        model = _text(item.get('model'), 'catalog model', 128)
        efforts = item.get('supported_efforts', [])
        _require(isinstance(efforts, list) and all(isinstance(e, str) and 0 < len(e) <= 64 for e in efforts),
                 'Invalid catalog efforts')
        harnesses = item.get('harnesses')
        _require(harnesses is None or isinstance(harnesses, list) and all(isinstance(h, str) for h in harnesses),
                 'Invalid catalog harnesses')
        limit = item.get('context_limit')
        _require(limit is None or type(limit) is int and limit >= 0, 'Invalid catalog context limit')
        _require(model not in live, 'Duplicate catalog model')
        live[model] = dict(efforts=efforts, harnesses=harnesses, context_limit=limit)
    return live


def _sources(guidance, benchmarks):
    sources, refs = [], {}
    for kind, items in (('guidance', guidance), ('benchmark', benchmarks)):
        for item in _list(items, kind):
            _require(isinstance(item, dict), 'Invalid ' + kind + ' entry')
            identity = _text(item.get('id'), kind + ' id', 128)
            _require(identity not in refs, 'Duplicate evidence id')
            checked = _text(item.get('checked_at'), kind + ' check time', 40)
            _require(_TIME.fullmatch(checked), 'Invalid ' + kind + ' check time')
            sources.append(dict(id=identity, kind=kind, sha256=_sha(item.get('sha256'), kind + ' digest'),
                                checked_at=checked))
            refs[identity] = item if kind == 'benchmark' else None
    return sources, refs


def _qualified(strategy, kind, refs, entry):
    """A strategy qualifies only through cited evidence: its own guidance entry, or a benchmark for the same
    model, harness and work kind (records lacking harness or work kind match nothing)."""
    cited = strategy['evidence_refs']
    if not cited:
        return False
    for ref in cited:
        if ref not in refs:
            return False
        benchmark = refs[ref]
        if benchmark is None:
            if ref != entry:
                return False
        elif (benchmark.get('model') != strategy['model'] or benchmark.get('work_kind') != kind or
              benchmark.get('harness') != strategy['harness']):
            return False
    return True


def compile_table(guidance, catalog, benchmarks, *, now=None):
    """Return the versioned strategy table; see the module docstring for input shapes."""
    live = _catalog(catalog)
    sources, refs = _sources(guidance, benchmarks)
    kinds = task_profile.load()['work_kinds']  # TemplateUnavailable is a ValueError: the compile fails
    rows, dropped = {}, []
    for entry in guidance:
        for row in _list(entry.get('rows'), 'guidance rows'):
            _require(isinstance(row, dict) and set(row) <= ROW_KEYS, 'Invalid guidance row')
            kind = row.get('work_kind')
            _require(isinstance(kind, str) and kind in kinds, 'Unknown row work kind')
            _require(row.get('complexity_band') in routing.COMPLEXITY_BANDS and
                     row.get('risk_band') in routing.RISK_BANDS, 'Invalid row band')
            name = routing.row_key(kind, row['complexity_band'], row['risk_band'])
            target = rows.setdefault(name, [])
            for item in _list(row.get('strategies'), 'row strategies'):
                _require(isinstance(item, dict), 'Invalid strategy')
                _require(item.get('effort') is None or isinstance(item.get('effort'), str), 'Invalid strategy effort')
                strategy = dict(model=_text(item.get('model'), 'strategy model', 128), effort=item.get('effort'),
                                harness=_text(item.get('harness'), 'strategy harness', 64),
                                endpoint=item.get('endpoint') or item.get('harness'),
                                evidence_refs=list(item.get('evidence_refs') or []))
                _require(all(isinstance(ref, str) for ref in strategy['evidence_refs']), 'Invalid evidence reference')
                reason = None
                model = live.get(strategy['model'])
                if model is None:
                    reason = 'model_not_in_catalog'
                elif strategy['effort'] is None and model['efforts'] or (
                        strategy['effort'] is not None and strategy['effort'] not in model['efforts']):
                    reason = 'effort_unsupported'
                elif model['harnesses'] is not None and strategy['harness'] not in model['harnesses']:
                    reason = 'harness_unsupported'
                elif not _qualified(strategy, kind, refs, entry.get('id')):
                    reason = 'evidence_missing'
                if reason:
                    dropped.append(dict(reason=reason, model=strategy['model'], effort=strategy['effort'], row=name))
                    continue
                strategy['strategy_id'] = _digest([strategy['model'], strategy['effort'], strategy['harness']] + (
                    [strategy['endpoint']] if strategy['endpoint'] != strategy['harness'] else []))[:16]
                if model['context_limit'] is not None:
                    strategy['context_limit'] = model['context_limit']
                if all(existing['strategy_id'] != strategy['strategy_id'] for existing in target):
                    target.append({k: strategy[k] for k in ('strategy_id', 'model', 'effort', 'harness',
                                                            'evidence_refs', 'context_limit') if k in strategy})
    rows = {name: value for name, value in rows.items() if value}
    checks = [source['checked_at'] for source in sources]
    return dict(schema=SCHEMA, version=_digest([SCHEMA, rows, sources]), compiled_at=now or _now(),
                checked_at=min(checks) if checks else None, sources=sources, rows=rows, dropped=dropped)


def refresh_table(previous, load, *, now=None):
    """Maintenance refresh: ``load()`` returns the three compile inputs.

    Any failure keeps the last verified table with its original ``compiled_at`` and marks
    ``refresh_failed``; with no previous table the failure is an error. A successful compile that
    drops every row of a non-empty previous table (for example an empty catalog) is a failure too.
    """
    try:
        fresh = compile_table(*load(), now=now)
        failure = None
        if previous and previous.get('rows') and not fresh['rows']:
            failure = ValueError('Refresh produced no qualified rows')
    except Exception as error:  # Any loader or compiler fault must leave the last verified table in place.
        fresh, failure = None, error
    if failure is None:
        return fresh
    if previous is None:
        raise ValueError('No verified strategy table to retain') from failure
    return dict(previous, refresh_failed=True)


def lookup(table, profile):
    """Strategies for a profile (exact row, then a stricter band of its work kind), or ``NO_RECOMMENDATION``.

    ``NO_RECOMMENDATION`` is not a refusal: the caller falls through to the deterministic path or the
    user's explicit worker choices."""
    strategies, _ = routing.resolve_row(table, profile)
    return strategies if strategies else dict(NO_RECOMMENDATION)
