"""Compose the tested advisory flow; never dispatch, admit, reserve or retry."""
from pathlib import Path
import math
import time

from runtime import feedback, native_catalog, routing, routing_paths, routing_observations, scores, subscription_billing

CLAUDE = 'opencode-claude-subscription'


def result(state, reason=None, **extra):
    return dict(dict(state=state, reason=reason, route=None, alternatives=[], excluded=[], level=None,
                     attribution=scores.ATTRIBUTION, below_level=False), **extra, admission=False, reservation=False,
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


def excluded(item, key, entries):
    """True when an excluded_models entry names this route: its bare model name, its catalog key or its route name
    exactly, or its provider/model with any #variant dropped from the entry."""
    pair = f"{item['provider']}/{item['model']}"
    return any(entry in (item['model'], key, item['route']) or entry.split('#', 1)[0] == pair for entry in entries)


def _constrained(item, key, constraints):
    return excluded(item, key, constraints.get('excluded_models', []))


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
    unmatched = _unmatched_for(request.get('models', []), constraints.get('excluded_models', []), state_dir)
    try:
        return _select(request, level, constraints, state_dir=state_dir, source=source, fetch=fetch, now=now,
                       unmatched=unmatched)
    except Exception:
        return result('blocked', 'selector_unavailable', **_reported(unmatched))


def _candidates(models, level, constraints, tokens, *, state_dir, source, fetch, fixed, task=None):
    """The selection steps up to the scored candidates, shared by Smart Router and council picks."""
    long = task is not None and routing.size_group(task) == 'long'
    state_dir = Path(state_dir)
    now = int(time.time()) if fixed is None else fixed
    if not state_dir.is_absolute() or state_dir.is_symlink():
        raise ValueError('selector_unavailable')
    anchor = state_dir / 'router.json'
    unique = {}
    for item in routing_paths.routes(models):
        unique.setdefault(item['route'], item)
    pool = dict(reason=None, excluded=[], candidates=[], kept=0, unscored=0, keys={}, creators={},
                had=False, cache=None, wanted=set(), now=now, fixed=fixed, source=source)
    if not unique:
        return dict(pool, reason='no_models')
    source = pool['source'] = source if source is not None else routing_observations.LocalSource(anchor)
    source.acquire_billing(list(unique.values()), now)
    # Readers stamp the facts they just acquired, so an earlier clock would see them as future.
    now = pool['now'] = int(time.time()) if fixed is None else fixed
    kept, excluded = routing_observations.eligible(list(unique.values()), source, now)
    pool.update(excluded=excluded, kept=len(kept))
    if not kept:
        return dict(pool, reason='no_eligible_route')
    catalog = native_catalog.stored(anchor, 'claude-code') if any(r['path'] == CLAUDE for r in kept) else None
    keys = pool['keys'] = {item['route']: _key(item, catalog) for item in kept}
    cache = scores.load(state_dir)
    had = _has_scores(cache)
    if not had:
        cache = _fetch_first(state_dir, sorted(set(keys.values())), now, fetch)
    if not _has_scores(cache):
        return dict(pool, reason='scores_unavailable')
    pool.update(had=had, cache=cache)
    for item in kept:
        key, effort = keys[item['route']], routing.effort_for(level, item['variants'], long=long)
        own = scores.lookup(cache, key, scores.CREATOR.get(item['path']))
        if own is None or own['score_source'] != 'openrouter':
            pool['wanted'].add(key)
        if own is None or own['price'] is None:
            pool['unscored'] += 1
            excluded.append(dict(route=item['route'], reason='unscored'))
        elif item['context_limit'] is not None and item['context_limit'] < tokens:
            excluded.append(dict(route=item['route'], reason='context_limit'))
        elif _constrained(item, key, constraints):
            excluded.append(dict(route=item['route'], reason='constraint'))
        else:
            pool['creators'][item['route']] = scores.CREATOR.get(item['path']) or own['creator']
            pool['candidates'].append(dict(item, effort=effort,
                                           score=own['score'], score_source=own['score_source'], price=own['price'],
                                           released=item['released'] or own['released']))
    return pool


def _unmatched(routed, catalog, entries):
    """Entries (request order, no duplicates) that name no route, whatever its eligibility, quota or score."""
    keys = [_key(item, catalog) for item in routed]
    return [entry for entry in dict.fromkeys(entries)
            if not any(excluded(item, key, [entry]) for item, key in zip(routed, keys))]


def _catalog_or_none(state_dir):
    """The stored Claude catalog for alias resolution; None when it cannot be read, which only drops alias names."""
    try:
        state_dir = Path(state_dir)
        if not state_dir.is_absolute() or state_dir.is_symlink():
            return None
        return native_catalog.stored(state_dir / 'router.json', 'claude-code')
    except Exception:
        return None


def _unmatched_for(models, entries, state_dir):
    """Entries that name no route built from `models`, whatever its eligibility, quota or score. Models that do
    not route leave nothing to match, so they report none."""
    if not entries:
        return []
    try:
        routed = routing_paths.routes(models)
    except Exception:
        return []
    catalog = _catalog_or_none(state_dir) if any(r['path'] == CLAUDE for r in routed) else None
    return _unmatched(routed, catalog, entries)


def _reported(unmatched):
    return dict(unmatched=unmatched) if unmatched else {}


def _confirm(ordered, pool, limit, qualifies=None):
    """Recheck each account in order; a route whose account changed since eligibility is dropped.
    With `qualifies`, keep checking past the limit until a qualifying route passes, and keep it among the picks."""
    chosen, found = [], False
    for item in ordered:
        if len(chosen) >= limit and (qualifies is None or found):
            break
        current = int(time.time()) if pool['fixed'] is None else pool['fixed']
        fact = subscription_billing.fact(pool['source'], item, current)
        if subscription_billing.marker(fact, item, current) != item['account_marker']:
            pool['excluded'].append(dict(route=item['route'], reason='account_changed'))
        elif len(chosen) < limit:
            chosen.append(item)
            found = found or bool(qualifies and qualifies(item))
        elif qualifies(item):
            chosen[-1], found = item, True
    return chosen


def _empty_reason(pool):
    return 'no_eligible_route' if pool['candidates'] \
        else 'unscored' if pool['unscored'] == pool['kept'] else 'no_recommendation'


def _refresh_flag(answer, pool):
    if pool['had'] and scores.due(pool['cache'], sorted(pool['wanted']), pool['now']):
        answer['refresh'] = dict(wanted=sorted(pool['wanted']))
    return answer


def _store(state_dir):
    try:
        return feedback.load(Path(state_dir))
    except Exception:  # feedback never breaks a pick
        return None


def _adjusted(pool, store, item, work_kind, size_group, anchor):
    """A candidate with its feedback adjustment in `score`; the catalog score stays in `catalog_score`."""
    key, creator = pool['keys'][item['route']], pool['creators'][item['route']]
    family = scores.family_key(key)
    try:
        facts = feedback.adjustment(store, key=key, creator=creator, family=family, work_kind=work_kind,
                                    size_group=size_group, anchor=anchor, now=pool['now'])
        delta = float(facts['adjustment'])
        if not math.isfinite(delta):
            raise ValueError('non_finite_adjustment')
    except Exception:  # any feedback problem means no adjustment for this candidate
        facts, delta = dict(sessions=0, adjustment=0.0), 0.0
    return dict(item, catalog_score=item['score'], score=item['score'] + delta, feedback=facts,
                catalog_id=dict(key=key, creator=creator, family=family))


def _select(request, level, constraints, *, state_dir, source, fetch, now, unmatched):
    pool = _candidates(request['models'], level, constraints, request['task'].get('context_tokens', 0),
                       state_dir=state_dir, source=source, fetch=fetch, fixed=now, task=request['task'])
    if pool['reason']:
        return result('no_selection', pool['reason'], level=level, excluded=pool['excluded'], **_reported(unmatched))
    anchor = scores.anchor(pool['cache'])
    size = routing.size_group(request['task'])
    store = _store(state_dir)
    candidates = [_adjusted(pool, store, item, request['work_kind'], size, anchor) for item in pool['candidates']]
    ordered = routing.order(candidates, level, anchor) if anchor is not None else []
    below = False
    if not ordered and candidates:
        ordered, below = routing.strongest(candidates), True
    qualifies = None if below else lambda item: item['score'] >= routing.bars(anchor)[level - 1] - routing.TOLERANCE
    chosen = _confirm(ordered, pool, 3, qualifies)
    if qualifies and not any(qualifies(item) for item in chosen):
        chosen = []
    reason = None if chosen else _empty_reason(pool)
    answer = result('selected' if chosen else 'no_selection', reason, level=level, excluded=pool['excluded'],
                    route=chosen[0] if chosen else None, alternatives=chosen[1:], below_level=below and bool(chosen),
                    **_reported(unmatched))
    if chosen:
        answer['size_group'] = size
    return _refresh_flag(answer, pool)


def _cached_creator(key, state_dir):
    """The catalog creator of a key, or None when unknown or the score cache cannot be read."""
    try:
        cache = scores.load(Path(state_dir))
        own = scores.lookup(cache, key, None) if cache is not None else None
        return own['creator'] if own else None
    except Exception:
        return None


def _entry_identity(entry, state_dir):
    """(creator, key) of a routable model entry, or None."""
    found = routing_paths.routes([entry])
    if len(found) != 1:
        return None
    item = found[0]
    catalog = native_catalog.stored(Path(state_dir) / 'router.json', 'claude-code') if item['path'] == CLAUDE else None
    key = _key(item, catalog)
    return (scores.CREATOR.get(item['path']) or _cached_creator(key, state_dir)), key


def _model_identity(model, state_dir):
    """(creator or None, key) the catalog would give a raw session model, whatever its provider. Off-plan models
    only name a rater; they never become eligible for selection."""
    path = CLAUDE if model['provider'] == 'claude-agent' else None
    key = _key(dict(path=path, model=model['id'], model_id=model['id']),
               native_catalog.stored(Path(state_dir) / 'router.json', 'claude-code') if path else None)
    return _cached_creator(key, state_dir), key


def _identity(entry, model, state_dir):
    """The rater's one identity: the routed entry's when it routes, else the one derived from the session model."""
    try:
        named = _entry_identity(entry, state_dir) if entry is not None else None
    except Exception:
        named = None
    if named is None and model is not None:
        try:
            named = _model_identity(model, state_dir)
        except Exception:
            named = None
    return named


def rater_id(entry, *, state_dir, model=None):
    """The creator and key of the model that rates, or None when it cannot be named. Never raises."""
    creator, key = _identity(entry, model, state_dir) or (None, None)
    if not (feedback._text(feedback.LAB, creator) and feedback._text(feedback.MODEL, key)):
        return None
    return dict(creator=creator, key=key)


def _is_self(identity, rated):
    """True when the rater's identity is the rated model: same key, and same lab when both are known."""
    creator, key = identity
    return key == rated.get('key') and (creator is None or feedback._lab(creator) == feedback._lab(rated.get('creator')))


FEEDBACK_REQUIRED = {'v', 'rated', 'work_kind', 'size_group', 'rating', 'session'}
FEEDBACK_OPTIONAL = {'rater_entry', 'rater_model', 'comment'}


def _model_ok(model):
    return type(model) is dict and set(model) == {'provider', 'id'} \
        and all(type(value) is str and 0 < len(value) <= 256 for value in model.values())


def feedback_submit(request, *, state_dir, now=None):
    """Store one feedback card from a request; refused/invalid when its shape is wrong. Never raises."""
    if type(request) is not dict or type(request.get('v')) is not int or request['v'] != 1 \
            or not FEEDBACK_REQUIRED <= set(request) <= FEEDBACK_REQUIRED | FEEDBACK_OPTIONAL \
            or type(request.get('rated')) is not dict \
            or request.get('rater_entry') is not None and type(request.get('rater_entry')) is not dict \
            or request.get('rater_model') is not None and not _model_ok(request.get('rater_model')):
        return dict(state='refused', reason='invalid')
    entry, model = request.get('rater_entry'), request.get('rater_model')
    named = _identity(entry, model, state_dir)
    if named is not None and _is_self(named, request['rated']):
        return dict(state='refused', reason='self_rating')
    rater = None if named is None else rater_id(entry, state_dir=state_dir, model=model)
    card = dict(rated=request['rated'], rater=rater, work_kind=request['work_kind'],
                size_group=request['size_group'], rating=request['rating'], session=request['session'],
                comment=request.get('comment'))
    return feedback.record(card, state_dir=Path(state_dir), now=int(time.time()) if now is None else now)
