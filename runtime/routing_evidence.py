"""Compile the Smart Routing strategy table from guidance, catalog, benchmark and terms evidence.

Maintenance-time only: the compiler is pure, makes no network or model call and is
never run on spawn paths or hooks. Strategies naming models absent from the live
catalog, unsupported efforts or harnesses, or lacking cited evidence are dropped,
never guessed. Model, effort and harness names appear only as data.

Input shapes (all plain JSON):
  guidance:   [{id, sha256, checked_at, rows:[{role, task_family, complexity_band, risk_band,
                strategies:[{model, effort, harness, evidence_refs}]}]}]   # ordered strongest first
  catalog:    [{model, supported_efforts, harnesses?, context_limit?}]     # as native/OpenRouter discovery reports
  benchmarks: [{id, model, harness?, task_family, sha256, checked_at}]
  terms:      [{model, zero_retention, no_training, forbids_confidential, user_opt_in}]
"""
import hashlib
import json
import re
import time

from runtime import routing

SCHEMA = 1
BLOCKED = dict(blocked=True, reason='no_strategy')
# Rows for these roles select models that see task text outside the worker, so data terms apply.
CONTROL_ROLES = ('profiler', 'ranker')
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


def _terms(items):
    result = {}
    for item in _list(items, 'terms'):
        _require(isinstance(item, dict), 'Invalid terms entry')
        result[_text(item.get('model'), 'terms model', 128)] = item
    return result


def _control_allowed(terms):
    if not terms or terms.get('forbids_confidential') is True:
        return False
    return (terms.get('zero_retention') is True and terms.get('no_training') is True) or terms.get('user_opt_in') is True


def _qualified(strategy, family, refs):
    """A strategy qualifies only through cited evidence: a guidance entry, or a benchmark for the same model,
    harness and task family."""
    cited = strategy['evidence_refs']
    if not cited:
        return False
    for ref in cited:
        if ref not in refs:
            return False
        benchmark = refs[ref]
        if benchmark is not None and (benchmark.get('model') != strategy['model'] or
                                      benchmark.get('task_family') not in (None, family) or
                                      benchmark.get('harness') not in (None, strategy['harness'])):
            return False
    return True


def compile_table(guidance, catalog, benchmarks, terms, *, now=None):
    """Return the versioned strategy table; see the module docstring for input shapes."""
    live = _catalog(catalog)
    sources, refs = _sources(guidance, benchmarks)
    policy = _terms(terms)
    rows, dropped = {}, []
    for entry in guidance:
        for row in _list(entry.get('rows'), 'guidance rows'):
            _require(isinstance(row, dict), 'Invalid guidance row')
            role = _text(row.get('role'), 'row role', 64)
            _require(role in routing.ROLES + CONTROL_ROLES, 'Unknown row role')
            _require(row.get('complexity_band') in routing.COMPLEXITY_BANDS and
                     row.get('risk_band') in routing.RISK_BANDS, 'Invalid row band')
            family = _text(row.get('task_family'), 'row task family', 64)
            target = rows.setdefault(routing.row_key(role, family, row['complexity_band'], row['risk_band']), [])
            for item in _list(row.get('strategies'), 'row strategies'):
                _require(isinstance(item, dict), 'Invalid strategy')
                _require(item.get('effort') is None or isinstance(item.get('effort'), str), 'Invalid strategy effort')
                strategy = dict(model=_text(item.get('model'), 'strategy model', 128), effort=item.get('effort'),
                                harness=_text(item.get('harness'), 'strategy harness', 64),
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
                elif not _qualified(strategy, family, refs):
                    reason = 'evidence_missing'
                elif role in CONTROL_ROLES and not _control_allowed(policy.get(strategy['model'])):
                    reason = 'control_plane_terms'
                if reason:
                    dropped.append(dict(reason=reason, model=strategy['model'], effort=strategy['effort'],
                                        row=routing.row_key(role, family, row['complexity_band'], row['risk_band'])))
                    continue
                strategy['strategy_id'] = _digest([strategy['model'], strategy['effort'], strategy['harness']])[:16]
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
    """Maintenance refresh: ``load()`` returns the four compile inputs.

    Any failure keeps the last verified table with its original ``compiled_at``
    and marks ``refresh_failed``; with no previous table the failure is an error.
    """
    try:
        return compile_table(*load(), now=now)
    except (OSError, ValueError, TypeError, KeyError) as error:
        if previous is None:
            raise ValueError('No verified strategy table to retain') from error
        return dict(previous, refresh_failed=True)


def lookup(table, profile):
    """Strategies for a profile (exact row, then stricter or conservative fallback), or ``BLOCKED``."""
    strategies, _ = routing.resolve_row(table, profile)
    return strategies if strategies else dict(BLOCKED)
