"""Compose the tested advisory flow; never dispatch, admit, reserve or retry."""
from pathlib import Path
import copy
import math
import time

from runtime import feedback, native_catalog, opencode_account, routing, routing_paths, routing_observations, scores, \
    subscription_billing, usage_balance

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


def _usable_row(entry):
    """A catalog row can name an identity only with a non-empty key and a real canonical creator (not openrouter)."""
    creator = entry.get('creator')
    return type(entry.get('key')) is str and bool(entry['key']) and type(creator) is str \
        and feedback._lab(creator) not in ('', 'openrouter')


def _creator(cache, key, fixed=None):
    """The one identity rule: the creator of the model's own catalog row (exact key, else the unique date-suffix
    row), scored or not; else the plan's fixed creator; never a family-borrowed score donor. None when unknown."""
    rows = [] if cache is None else [entry for entry in cache.get('models', {}).values()
                                     if type(entry) is dict and _usable_row(entry)]
    exact = {entry['creator'] for entry in rows if entry['key'] == key}
    if not exact:
        exact = {entry['creator'] for entry in rows if scores._month_day(entry['key'], key)}
    return next(iter(exact)) if len(exact) == 1 else fixed


def _identify(path, name, model_id, state_dir):
    """(creator or None, key) of a model: the catalog key (alias resolved) and its one creator. Never raises."""
    anchor = Path(state_dir) / 'router.json'
    try:
        catalog = native_catalog.stored(anchor, 'claude-code') if path == CLAUDE else None
    except Exception:
        catalog = None
    key = _key(dict(path=path, model=name, model_id=model_id), catalog)
    try:
        cache = scores.load(Path(state_dir))
    except Exception:
        cache = None
    return _creator(cache, key, scores.CREATOR.get(path)), key


def _known_lab(creator):
    """The canonical lab of a creator, or None when it is not a real lab (unknown, malformed or openrouter)."""
    if not feedback._text(feedback.LAB, creator):
        return None
    lab = feedback._lab(creator)
    return None if lab == 'openrouter' else lab


def _reviewing_ok(value):
    """A model_ref: provider/model or provider/creator/model, either with an optional #variant. All parts non-empty."""
    if not routing._text(value):
        return False
    parts = value.split('#', 1)[0].split('/')
    return len(parts) in (2, 3) and all(parts)


def select(request, *, state_dir, source=None, fetch=None, now=None):
    # Only the caller's own fields are its mistake; everything after them is the selector's.
    try:
        if type(request) is not dict or type(request.get('v')) is not int or request['v'] != 2 \
                or not {'v', 'work_kind', 'task'} <= set(request) \
                <= {'v', 'work_kind', 'task', 'models', 'constraints', 'reviewing'} \
                or ('reviewing' in request and not _reviewing_ok(request['reviewing'])):
            raise ValueError('invalid_request')
        level = routing.task_level(request['work_kind'], request['task'])
        constraints = routing.check_constraints(request.get('constraints', {}))
    except (ValueError, TypeError, KeyError, RecursionError):
        return result('blocked', 'invalid_request')
    entries = constraints.get('excluded_models', [])
    unmatched = _unmatched_for(request.get('models', []), entries, state_dir)
    seen = {}
    try:
        answer = _select(request, level, constraints, state_dir=state_dir, source=source, fetch=fetch, now=now,
                         unmatched=unmatched, seen=seen)
    except Exception:
        if seen:
            unmatched = _unmatched(seen['routed'], seen['allkeys'], entries)
        answer = result('blocked', 'selector_unavailable', **_reported(unmatched))
    if 'reviewing' in request:
        answer.setdefault('reviewer_independent', False)
    return answer


def _candidates(models, level, constraints, tokens, *, state_dir, source, fetch, fixed, task=None, seen=None,
                work_kind=None):
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
    pool = dict(reason=None, excluded=[], candidates=[], kept=0, unscored=0, keys={}, creators={}, labs={}, named=set(),
                allkeys=None, routed=[], had=False, cache=None, wanted=set(), now=now, fixed=fixed, source=source)
    if not unique:
        return dict(pool, reason='no_models')
    source = pool['source'] = source if source is not None else routing_observations.LocalSource(anchor)
    source.acquire_billing(list(unique.values()), now)
    # Readers stamp the facts they just acquired, so an earlier clock would see them as future.
    now = pool['now'] = int(time.time()) if fixed is None else fixed
    # One route-to-key map per request, read after acquisition, serves both the exclusion filter and `unmatched`.
    routed = pool['routed'] = list(unique.values())
    catalog = native_catalog.stored(anchor, 'claude-code') if any(r['path'] == CLAUDE for r in routed) else None
    allkeys = pool['allkeys'] = {item['route']: _key(item, catalog) for item in routed}
    if seen is not None:
        seen.update(routed=routed, allkeys=allkeys)
    kept, excluded = routing_observations.eligible(list(unique.values()), source, now)
    pool.update(excluded=excluded, kept=len(kept))
    if not kept:
        return dict(pool, reason='no_eligible_route')
    keys = pool['keys'] = {item['route']: allkeys[item['route']] for item in kept}
    cache = scores.load(state_dir)
    had = _has_scores(cache)
    if not had:
        cache = _fetch_first(state_dir, sorted(set(keys.values())), now, fetch)
    if not _has_scores(cache):
        return dict(pool, reason='scores_unavailable')
    pool.update(had=had, cache=cache)
    for item in kept:
        key, effort = keys[item['route']], routing.effort_for(level, item['variants'], long=long, work_kind=work_kind)
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
            creator = _creator(cache, key, scores.CREATOR.get(item['path']))
            # Without an identity of its own the score's creator still keys the card; it never names a lab.
            pool['creators'][item['route']] = creator or own['creator']
            if creator is not None:
                pool['named'].add(item['route'])
            pool['labs'][item['route']] = _known_lab(creator)
            pool['candidates'].append(dict(item, effort=effort,
                                           score=own['score'], score_source=own['score_source'], price=own['price'],
                                           released=item['released'] or own['released']))
    return pool


def _unmatched(routed, keys, entries):
    """Entries (request order, no duplicates) that name no route, whatever its eligibility, quota or score.
    `keys` maps each route name to its catalog key."""
    return [entry for entry in dict.fromkeys(entries)
            if not any(excluded(item, keys[item['route']], [entry]) for item in routed)]


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
    return _unmatched(routed, {item['route']: _key(item, catalog) for item in routed}, entries)


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


def _hints(source, item, now):
    """The route's ranking meters. A failed or absent read is no hint and never changes the pick."""
    reader = getattr(source, 'ranking_meters', None)
    try:
        return reader(copy.deepcopy(item), now) if callable(reader) else {}
    except Exception:
        return {}


def _scope(item):
    """The account scope a route's billing marker covers: its path, account and pool, hashable."""
    return tuple(sorted(subscription_billing.scope(item).items()))


def _expiry(source, item, now):
    """The route's expiry priority from its own hints, or None when it has no usable one."""
    return usage_balance.expiry_score(opencode_account.family(item), _hints(source, item, now), now=now)


def _balance(ordered, pool, qualifies):
    """The baseline order, with the slots of comparable qualifying routes refilled by expiry priority when another
    subscription beats the baseline winner by the minimum gain. A changed account among the reordered routes (the
    baseline included, even past `_confirm`'s limit) restores the baseline order without them. Optional: any failure
    returns the baseline order, and `_confirm` remains the final binding check."""
    if qualifies is None or not ordered or not qualifies(ordered[0]):
        return ordered  # below level, or a near-floor winner: the original baseline behavior
    try:
        base, now = ordered[0], pool['now']
        rivals = [item for item in ordered[1:] if qualifies(item) and usage_balance.comparable(item, base)]
        if not any(item['path'] != base['path'] for item in rivals):
            return ordered  # fewer than two subscriptions can compete: read nothing
        base_expiry = _expiry(pool['source'], base, now)
        if base_expiry is None:
            return ordered
        expiry = {base['route']: base_expiry}
        for item in rivals:
            score = _expiry(pool['source'], item, now)
            if score is not None:
                expiry[item['route']] = score
        group = [base] + [item for item in rivals if item['route'] in expiry]
        balanced = usage_balance.reorder(ordered, subscriptions={item['route']: item['path'] for item in group},
                                         expiry=expiry)
        if [item['route'] for item in balanced] == [item['route'] for item in ordered]:
            return ordered
        # One current marker per participating scope (path, account, pool); every candidate in a drifted scope is changed.
        current = int(time.time()) if pool['fixed'] is None else pool['fixed']
        scopes = {_scope(item) for item in group}
        returned = {}
        for item in group:
            if _scope(item) not in returned:
                returned[_scope(item)] = subscription_billing.marker(
                    subscription_billing.fact(pool['source'], item, current), item, current)
        changed = [item['route'] for item in ordered
                   if _scope(item) in scopes and returned[_scope(item)] != item['account_marker']]
        if not changed:
            return balanced
        pool['excluded'].extend(dict(route=route, reason='account_changed') for route in changed)
        return [item for item in ordered if item['route'] not in changed]
    except Exception:
        return ordered


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
    if item['route'] not in pool['named']:
        # An identity that only a score donor gave is not the model's own: recommend it, but it cannot be rated.
        return dict(item, catalog_score=item['score'], feedback=dict(sessions=0, adjustment=0.0))
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


def _select(request, level, constraints, *, state_dir, source, fetch, now, unmatched, seen):
    pool = _candidates(request['models'], level, constraints, request['task'].get('context_tokens', 0),
                       state_dir=state_dir, source=source, fetch=fetch, fixed=now, task=request['task'], seen=seen,
                       work_kind=request['work_kind'])
    if pool['allkeys'] is not None:
        unmatched = _unmatched(pool['routed'], pool['allkeys'], constraints.get('excluded_models', []))
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
    reviewing = request.get('reviewing')
    independent, chosen = False, []
    if reviewing is not None and not below:
        lab = review_lab(reviewing, request.get('models', []), state_dir=state_dir)
        other = [item for item in ordered if lab is not None and qualifies(item)
                 and pool['labs'].get(item['route']) not in (None, lab)]
        chosen = _confirm(_balance(other, pool, qualifies), pool, 3, qualifies)
        independent = bool(chosen)
        if not chosen:
            failed = {entry['route'] for entry in pool['excluded'] if entry['reason'] == 'account_changed'}
            ordered = [item for item in ordered if item['route'] not in failed]
    if not independent:
        # A review with no qualified independent route keeps the original baseline order, never balanced.
        chosen = _confirm(ordered if reviewing is not None else _balance(ordered, pool, qualifies), pool, 3, qualifies)
    if qualifies and not any(qualifies(item) for item in chosen):
        chosen = []
    reason = None if chosen else _empty_reason(pool)
    answer = result('selected' if chosen else 'no_selection', reason, level=level, excluded=pool['excluded'],
                    route=chosen[0] if chosen else None, alternatives=chosen[1:], below_level=below and bool(chosen),
                    **_reported(unmatched))
    if chosen:
        answer['size_group'] = size
    if reviewing is not None:
        answer['reviewer_independent'] = independent and bool(chosen)
    return _refresh_flag(answer, pool)


def _entry_identity(entry, state_dir):
    """(creator, key) of a routable model entry, or None."""
    found = routing_paths.routes([entry])
    if len(found) != 1:
        return None
    return _identify(found[0]['path'], found[0]['model'], found[0]['model_id'], state_dir)


def _model_identity(model, state_dir):
    """(creator or None, key) of a raw session model, whatever its provider: the same rule a routed entry gets.
    Off-plan models only name a rater; they never become eligible for selection."""
    try:
        found = routing_paths.routes([dict(provider=model['provider'], id=model['id'], model=None, family=None,
                                           variants=[], context=None, released=0)])
    except Exception:
        found = []
    return _identify(found[0]['path'] if found else None, model['id'], model['id'], state_dir)


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


def review_lab(reviewing, models, *, state_dir):
    """The canonical lab (leading ~ dropped) of the model a review names, or None when it cannot be named.
    provider/creator/model names its creator; provider/model resolves through the one identity rule. Never raises."""
    try:
        parts = reviewing.split('#', 1)[0].split('/')
        if len(parts) == 3:
            return _known_lab(parts[1])
        provider, model_id = parts
        entry = next((item for item in models if type(item) is dict and item.get('provider') == provider
                      and item.get('id') == model_id), None)
        return _known_lab((_identity(entry, dict(provider=provider, id=model_id), state_dir) or (None, None))[0])
    except Exception:
        return None


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
