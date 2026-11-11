"""The ranked funding decision and the admission record of `routing._decide` (F5.2, moved without a behaviour change).

`routing._decide` imports this module locally and calls it; this module imports `routing`, never the reverse at module
import time. Routing's own helpers are read through the module (`routing.<name>`), as `_decide` reads them.
"""
import copy

from runtime import routing, routing_allocator as allocator, routing_gates

SHORTLIST_MAX = routing.MAX_SHORTLIST  # 10
SUBSCRIPTION = 'included_subscription'


def subscription_shortlist(strategy_ids, routes):
    """The first SHORTLIST_MAX distinct ranked ids, in ranking order, that have an `included_subscription` route in
    `routes`. The caller's `routes` already passed the shared pre-window checks (static eligibility and the host); a
    paid route takes no place, the whole order is scanned and the list is never padded."""
    eligible = {route.get('strategy_id') for route in routes if route.get('billing_class') == SUBSCRIPTION}
    found = []
    for sid in strategy_ids:
        if sid in eligible and sid not in found:
            found.append(sid)
            if len(found) == SHORTLIST_MAX:
                break
    return found


def is_ranked(assignment, ranking, settings):
    """`routing._decide`'s mode rule: Smart Routing on, no explicit choice and a non-empty ranking."""
    return bool(settings['smart_routing'] and not assignment.get('choice') and ranking.get('strategy_ids'))


def in_ranking_order(observations, ranking):
    """For a ranked decision: a supplied `observations['shortlist']` (its shape is `routing._valid_observations`'s)
    names only ranked ids, in ranking order; absent is valid (admission cuts a caller's own snapshot itself)."""
    ranked_ids = ranking['strategy_ids']
    positions = [ranked_ids.index(item) for item in observations.get('shortlist', []) if item in ranked_ids]
    return len(positions) == len(observations.get('shortlist', [])) and positions == sorted(positions)


def _eligible(route):
    """A caller route's non-window checks (`routing._candidate` before its pool): a subscription route with its
    identity, demand, pool and account, reported capable, permitted and binding-enforceable."""
    return route.get('billing_class') == SUBSCRIPTION and isinstance(route.get('demand'), dict) and all(
        route.get(key) is True for key in ('capable', 'permitted', 'binding_enforceable')) and all(
        routing._text(route.get(key)) for key in ('route', 'pool', 'account', 'strategy_id'))


def _shortlist(observations, ranking):
    """The ranked decision's shortlist: the snapshot's own, else the cut of a caller's routes passing `_eligible`."""
    return observations['shortlist'] if 'shortlist' in observations else subscription_shortlist(
        ranking['strategy_ids'], [route for route in observations['routes'] if _eligible(route)])


def admitted(assignment, role, profile, harnesses):
    """The assignment `scripts/routing.py` admits: the caller's fields, the specialist's role, the validated
    profile's work kind and bands, `floor_filled` (its `missing_fields` were not empty) and the effective allowed
    harnesses (empty without that constraint; a malformed list is refused by `routing.rank` first)."""
    harnesses = list(dict.fromkeys(harnesses)) if type(harnesses) is list and all(map(routing._text, harnesses)) \
        else []
    return dict(assignment, role=role, work_kind=profile['work_kind'], complexity_band=profile['complexity_band'],
                risk_band=profile['risk_band'], floor_filled=bool(profile['uncertain_fields']),
                allowed_harnesses=sorted(harnesses))


def ranked_routes(observations, ranking):
    """The routes a ranked decision evaluates: subscription routes of the shortlisted ids only (the snapshot's own
    `shortlist`, else `subscription_shortlist` over the routes passing `_eligible`). F5.3: a paid route is left out
    here, before any candidate check, whatever the settings enable; it is never a ranked fallback. A supplied
    shortlist only narrows: its routes still meet every check in `_candidate`, and a missing route is never rebuilt.
    In a caller's own snapshot a route failing `_eligible` takes no place but still reaches `_candidate`, which
    refuses it with its own reason (it can never be admitted)."""
    routes, supplied, shortlist = observations['routes'], 'shortlist' in observations, _shortlist(observations, ranking)
    return [route for route in routes if route.get('billing_class') not in routing.FUNDED and (
        route.get('billing_class') != SUBSCRIPTION or route.get('strategy_id') in shortlist
        or not (supplied or _eligible(route)))]


def _snapshot(item, now, horizon):
    found = allocator.binding_constraint(item['constraints'], now, horizon)
    pressure = None if found is None or found[1] is None else float(found[0] / found[1])
    return dict(pressure=pressure, constraints=[dict(c) for c in item['constraints']])


def choose(candidates, shares, rows, bucket, epoch, feasible, now, horizon):
    """F5.3: `(route, class)` of a ranked decision over the feasible subscription candidates in shortlist order, or
    `(None, None)`. Pure; `allocator.choose_class` and `allocator.choose_route` unchanged (window pressure, held
    reservations, binding reset horizons, one weight per pool). A paid candidate or feasible class is refused
    (`billing_class_excluded`), never replayed as a subscription."""
    if any(item.get('cls') != 'subscription' for item in candidates) or set(feasible) - {'subscription'}:
        raise ValueError('billing_class_excluded')
    cls = allocator.choose_class(shares, rows, bucket, epoch, feasible)
    chosen = allocator.choose_route(candidates, now, horizon) if cls else None
    return (chosen, cls) if chosen is not None else (None, None)


def ranked(data, candidates, excluded, ranking, settings, observations, bucket):
    """The ranked branch: `(pending, chosen, shares)`; `pending` is the answer to return when no subscription
    candidate is feasible, else None. Subscription capacity alone funds it: no paid share, fallback or reason."""
    now, horizon = observations['now'], observations.get('minimum_horizon', routing.DEFAULT_MINIMUM_HORIZON)
    order = {sid: i for i, sid in enumerate(ranking['strategy_ids'])}
    pool = sorted((c for c in candidates if c['cls'] == 'subscription' and c['spec'].get('strategy_id') in order),
                  key=lambda c: order[c['spec']['strategy_id']])
    if not pool:
        return routing._pending(routing._dominant(excluded), excluded), None, None
    shares = dict(api=0.0, subscription=1.0)
    rows = routing.decision_rows(data, settings.get('revision', 0), now)
    chosen, _ = choose(pool, shares, rows, bucket, observations.get('epoch_at', 0), ['subscription'], now, horizon)
    return None, chosen, shares


def _replay(item, ranked):
    """One stored candidate: the pool once, its window constraints without the repeated pool (costs per candidate)."""
    constraints = [{key: value for key, value in constraint.items() if key != 'pool'}
                   for constraint in item['constraints']]
    return dict(route=item['route'], strategy_id=item['spec'].get('strategy_id') if ranked else None,
                cls=item['cls'], pool=item['pool'], constraints=constraints)


def record(data, assignment, candidates, chosen, bucket, shares, mode, u, e, reasons, ranking, settings, observations,
           workflow):
    """The admission answer for the chosen route: its record, vector, account, time and revision.

    F5.4: the record stores what the strict replay recomputes from it alone (`routing_funding.choose` over
    `pressure.candidates`, each constraint given its candidate's pool back): `ranking.shortlist` (the subscription
    ids before any window check), the chosen `strategy_id`, `score_version` (None until L3) and the source's
    `routes_version` (audit only, never regenerated); `risk_band`, `floor_filled` and the chosen `strategy`; and in
    `pressure`, the snapshot detail removed after 24 hours, `epoch`, `feasible`, `minimum_horizon`, the effective
    `allowed_harnesses` and, for a ranked decision, the feasible subscription `candidates` in shortlist order. A
    worker-choice record keeps the legacy contract: an empty shortlist, no candidates, the chosen route's
    constraints."""
    now, horizon = observations['now'], observations.get('minimum_horizon', routing.DEFAULT_MINIMUM_HORIZON)
    item = next(c for c in candidates if c['route'] == chosen)
    spec = item['spec']
    credential = spec.get('credential') or {}
    ranked = mode == 'ranked'
    harnesses = assignment.get('allowed_harnesses') or []
    if type(harnesses) is not list or not all(map(routing._text, harnesses)):
        raise ValueError('invalid_assignment')
    order = {sid: i for i, sid in enumerate(ranking.get('strategy_ids', []))}
    feasible = sorted((c for c in candidates if c['cls'] == 'subscription' and c['spec'].get('strategy_id') in order),
                      key=lambda c: order[c['spec']['strategy_id']]) if ranked else []
    pressure = dict(u=u, e=e, **_snapshot(item, now, horizon), epoch=observations.get('epoch_at', 0),
                    feasible=['subscription'] if ranked else [item['cls']], minimum_horizon=horizon,
                    allowed_harnesses=sorted(harnesses))
    if ranked:
        pressure['candidates'] = [_replay(c, True) for c in feasible]
    record = dict(attempt_id=assignment['attempt_id'], task=assignment['task'], bucket=bucket, shares=shares,
                  **{'class': item['cls']}, route=item['route'], pool=item['pool'], account=spec['account'],
                  route_identity=routing.route_identity(*(spec.get(field) for field in routing.ROUTE_IDENTITY)),
                  billing_class=spec['billing_class'], credential_ref=credential.get('ref'),
                  pressure=pressure, reserved=dict(item['vector']),
                  settings_revision=settings.get('revision', 0), organization_revision=item['revision'], at=now,
                  mode=mode,
                  snapshot_id=observations.get('snapshot_id'),
                  reason_codes=list(dict.fromkeys(reasons)),
                  ranking=dict(table_version=ranking.get('table_version'), profile_digest=ranking.get('profile_digest'),
                               shortlist=list(_shortlist(observations, ranking)) if ranked else [],
                               strategy_id=spec.get('strategy_id') if ranked else None, score_version=None,
                               routes_version=observations.get('routes_version')),
                  risk_band=assignment.get('risk_band'), floor_filled=assignment.get('floor_filled'),
                  strategy={field: spec.get(field) for field in routing.ROUTE_IDENTITY})
    record['binding'] = routing_gates.binding(copy.deepcopy(workflow), routing.credential_marker(data, record))
    return dict(record=record, vector=item['vector'], account=spec['account'], now=now, revision=item['revision'])
