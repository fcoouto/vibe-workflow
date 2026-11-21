"""Compose the tested advisory flow; never dispatch, admit, reserve or retry."""
from pathlib import Path
import time

from runtime import native_catalog, routing, routing_paths, routing_observations, scores, subscription_billing

CLAUDE = 'opencode-claude-subscription'


def result(state, reason=None, **extra):
    return dict(dict(state=state, reason=reason, route=None, alternatives=[], excluded=[], level=None,
                     attribution=scores.ATTRIBUTION), **extra, admission=False, reservation=False,
                spend_permit=False)


def _has_scores(cache):
    return cache is not None and any(entry['score'] is not None for entry in cache['models'].values())


def _fetch_first(state_dir, wanted, now, fetch):
    try:
        scores.refresh(state_dir, wanted, now, fetch=fetch, timeout=scores.FIRST_TIMEOUT)
    except (OSError, ValueError):
        pass  # Unwritable state is the same as no scores.
    return scores.load(state_dir)


def check_setup(*, state_dir, allow_fetch=False, fetch=None):
    """Local readiness only; the optional fetch is public scores, never an account or quota read."""
    try:
        state_dir = Path(state_dir)
        if not state_dir.is_absolute() or state_dir.is_symlink():
            raise ValueError('invalid_request')
        cache = scores.load(state_dir)
        if allow_fetch and not _has_scores(cache):
            cache = _fetch_first(state_dir, [], int(time.time()), fetch)
        reason = None if _has_scores(cache) else 'scores_unavailable'
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        reason = 'selector_unavailable'
    return dict(state='ready' if reason is None else 'setup_needed', reason=reason)


def refresh(request, *, state_dir, fetch=None):
    try:
        wanted = request['wanted']
        if type(request) is not dict or set(request) != {'wanted'} or type(wanted) is not list \
                or len(wanted) > 512 or not all(routing._text(key) for key in wanted):
            raise ValueError('invalid_request')
        state_dir = Path(state_dir)
        if not state_dir.is_absolute() or state_dir.is_symlink():
            raise ValueError('invalid_request')
        return dict(state=scores.refresh(state_dir, wanted, int(time.time()), fetch=fetch))
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        return dict(state='failed')


def _key(route, catalog):
    if route['path'] == CLAUDE:
        for item in (catalog or {}).get('models', []):
            if item['alias'] == route['model'] and item['resolved']:
                return scores.normalize(item['resolved'])
    return scores.normalize(route['model_id'] or route['model'])


def _constrained(item, effort, constraints):
    values = dict(allowed_models=item['model'], allowed_efforts=effort, allowed_harnesses=item['harness'])
    return item['model'] in constraints.get('excluded_models', []) \
        or any(value not in constraints[key] for key, value in values.items() if key in constraints)


def select(request, *, state_dir, source=None, fetch=None, now=None):
    # Only the caller's own fields are its mistake; everything after them is the selector's.
    try:
        if type(request) is not dict or type(request.get('v')) is not int or request['v'] != 2 \
                or not {'v', 'work_kind', 'task'} <= set(request) \
                <= {'v', 'work_kind', 'task', 'models', 'constraints'}:
            raise ValueError('invalid_request')
        level = routing.task_level(request['work_kind'], request['task'])
        constraints = routing.check_constraints(request.get('constraints', {}))
    except (ValueError, TypeError, KeyError, RecursionError):
        return result('blocked', 'invalid_request')
    try:
        return _select(request, level, constraints, state_dir=state_dir, source=source, fetch=fetch, now=now)
    except Exception:
        return result('blocked', 'selector_unavailable')


def _candidates(models, level, constraints, tokens, *, state_dir, source, fetch, fixed):
    """The selection steps up to the scored candidates, shared by Smart Router and council picks."""
    state_dir = Path(state_dir)
    now = int(time.time()) if fixed is None else fixed
    if not state_dir.is_absolute() or state_dir.is_symlink():
        raise ValueError('selector_unavailable')
    unique = {}
    for item in routing_paths.routes(models):
        unique.setdefault(item['route'], item)
    pool = dict(reason=None, excluded=[], candidates=[], kept=0, unscored=0, keys={}, creators={},
                had=False, cache=None, wanted=set(), now=now, fixed=fixed, source=source)
    if not unique:
        return dict(pool, reason='no_models')
    anchor = state_dir / 'router.json'
    source = pool['source'] = source if source is not None else routing_observations.LocalSource(anchor)
    source.acquire_billing(list(unique.values()), now)
    # Readers stamp the facts they just acquired, so an earlier clock would see them as future.
    now = pool['now'] = int(time.time()) if fixed is None else fixed
    kept, excluded = routing_observations.eligible(list(unique.values()), source, now)
    pool.update(excluded=excluded, kept=len(kept))
    catalog = native_catalog.stored(anchor, 'claude-code') if any(r['path'] == CLAUDE for r in kept) else None
    if not kept:
        return dict(pool, reason='no_eligible_route')
    keys = pool['keys'] = {item['route']: _key(item, catalog) for item in kept}
    cache = scores.load(state_dir)
    had = _has_scores(cache)
    if not had:
        cache = _fetch_first(state_dir, sorted(set(keys.values())), now, fetch)
    if not _has_scores(cache):
        return dict(pool, reason='scores_unavailable')
    pool.update(had=had, cache=cache)
    for item in kept:
        key, effort = keys[item['route']], routing.effort_for(level, item['variants'])
        own = scores.lookup(cache, key, scores.CREATOR.get(item['path']))
        if own is None or own['score_source'] != 'openrouter':
            pool['wanted'].add(key)
        if own is None or own['price'] is None:
            pool['unscored'] += 1
            excluded.append(dict(route=item['route'], reason='unscored'))
        elif item['context_limit'] is not None and item['context_limit'] < tokens:
            excluded.append(dict(route=item['route'], reason='context_limit'))
        elif _constrained(item, effort, constraints):
            excluded.append(dict(route=item['route'], reason='constraint'))
        else:
            pool['creators'][item['route']] = scores.CREATOR.get(item['path']) or own['creator']
            pool['candidates'].append(dict(item, effort=effort,
                                           score=own['score'], score_source=own['score_source'], price=own['price'],
                                           released=item['released'] or own['released']))
    return pool


def _confirm(ordered, pool, limit):
    """Recheck each account in order; a route whose account changed since eligibility is dropped."""
    chosen = []
    for item in ordered:
        current = int(time.time()) if pool['fixed'] is None else pool['fixed']
        fact = subscription_billing.fact(pool['source'], item, current)
        if subscription_billing.marker(fact, item, current) != item['account_marker']:
            pool['excluded'].append(dict(route=item['route'], reason='account_changed'))
        else:
            chosen.append(item)
        if len(chosen) == limit:
            break
    return chosen


def _empty_reason(pool):
    return 'no_eligible_route' if pool['candidates'] \
        else 'unscored' if pool['unscored'] == pool['kept'] else 'no_recommendation'


def _refresh_flag(answer, pool):
    if pool['had'] and scores.due(pool['cache'], sorted(pool['wanted']), pool['now']):
        answer['refresh'] = dict(wanted=sorted(pool['wanted']))
    return answer


def _select(request, level, constraints, *, state_dir, source, fetch, now):
    pool = _candidates(request['models'], level, constraints, request['task'].get('context_tokens', 0),
                       state_dir=state_dir, source=source, fetch=fetch, fixed=now)
    if pool['reason']:
        return result('no_selection', pool['reason'], level=level, excluded=pool['excluded'])
    chosen = _confirm(routing.order(pool['candidates'], level), pool, 3)
    reason = None if chosen else _empty_reason(pool)
    answer = result('selected' if chosen else 'no_selection', reason, level=level, excluded=pool['excluded'],
                    route=chosen[0] if chosen else None, alternatives=chosen[1:])
    return _refresh_flag(answer, pool)
