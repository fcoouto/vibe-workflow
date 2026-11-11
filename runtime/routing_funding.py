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


def ranked_routes(observations, ranking):
    """The routes a ranked decision evaluates: subscription routes of the shortlisted ids only (the snapshot's own
    `shortlist`, else `subscription_shortlist` over the routes passing `_eligible`), paid routes unchanged. A supplied
    shortlist only narrows: its routes still meet every check in `_candidate`, and a missing route is never rebuilt.
    In a caller's own snapshot a route failing `_eligible` takes no place but still reaches `_candidate`, which
    refuses it with its own reason (it can never be admitted)."""
    routes, supplied = observations['routes'], 'shortlist' in observations
    shortlist = observations['shortlist'] if supplied else subscription_shortlist(
        ranking['strategy_ids'], [route for route in routes if _eligible(route)])
    return [route for route in routes if route.get('billing_class') != SUBSCRIPTION
            or route.get('strategy_id') in shortlist or not (supplied or _eligible(route))]


def _expiry(candidates, observations, settings, now):
    """E from the earliest-expiring grant of a feasible, eligible direct route; gateways never contribute."""
    grants = [observations['pools'][item['pool']]['grant'] for item in candidates
              if item['spec']['billing_class'] == 'api_grant' and 'grant' in observations['pools'][item['pool']]]
    grants = [grant for grant in grants if grant['expires_at'] > now]
    if not grants:
        return 0.0
    grant = min(grants, key=lambda item: item['expires_at'])
    return allocator.expiry_pressure(grant['remaining'], grant.get('expected_spend', 0),
                                     (grant['expires_at'] - now) / routing.SECONDS_PER_HOUR, settings['T_hours'])


def _snapshot(item, now, horizon):
    found = allocator.binding_constraint(item['constraints'], now, horizon)
    pressure = None if found is None or found[1] is None else float(found[0] / found[1])
    return dict(pressure=pressure, constraints=[dict(c) for c in item['constraints']])


def ranked(data, candidates, excluded, ranking, settings, observations, held, bucket, reasons):
    """The ranked branch: `(pending, chosen, shares, u, e)`; `pending` is the answer to return when no class is
    feasible, else None. `reasons` gains the utilization reasons in place."""
    now, horizon = observations['now'], observations.get('minimum_horizon', routing.DEFAULT_MINIMUM_HORIZON)
    u = e = None
    order = {sid: i for i, sid in enumerate(ranking['strategy_ids'])}
    pool = sorted((c for c in candidates if c['spec'].get('strategy_id') in order),
                  key=lambda c: order[c['spec']['strategy_id']])
    by_class = {cls: [c for c in pool if c['cls'] == cls] for cls in ('api', 'subscription')}
    feasible = [cls for cls, items in by_class.items() if items]
    if not feasible:
        return routing._pending(routing._dominant(excluded), excluded), None, None, u, e
    if len(feasible) == 2:
        u, why = routing._utilization(by_class['subscription'], held, settings, now)
        e = _expiry(by_class['api'], observations, settings, now)
        reasons += why
        api = allocator.api_share(u, e, settings['L'], settings['H'])
    else:
        api = 1.0 if feasible == ['api'] else 0.0  # hard overrides
    shares = dict(api=api, subscription=1 - api)
    rows = routing.decision_rows(data, settings.get('revision', 0), now)
    cls = allocator.choose_class(shares, rows, bucket, observations.get('epoch_at', 0), feasible)
    chosen = allocator.choose_route(by_class[cls], now, horizon) if cls else None
    return None, chosen, shares, u, e


def record(data, assignment, candidates, chosen, bucket, shares, mode, u, e, reasons, ranking, settings, observations,
           workflow):
    """The admission answer for the chosen route: its record, vector, account, time and revision."""
    now, horizon = observations['now'], observations.get('minimum_horizon', routing.DEFAULT_MINIMUM_HORIZON)
    item = next(c for c in candidates if c['route'] == chosen)
    spec = item['spec']
    credential = spec.get('credential') or {}
    record = dict(attempt_id=assignment['attempt_id'], task=assignment['task'], bucket=bucket, shares=shares,
                  **{'class': item['cls']}, route=item['route'], pool=item['pool'], account=spec['account'],
                  route_identity=routing.route_identity(*(spec.get(field) for field in routing.ROUTE_IDENTITY)),
                  billing_class=spec['billing_class'], credential_ref=credential.get('ref'),
                  pressure=dict(u=u, e=e, **_snapshot(item, now, horizon)), reserved=dict(item['vector']),
                  settings_revision=settings.get('revision', 0), organization_revision=item['revision'], at=now,
                  mode=mode,
                  snapshot_id=observations.get('snapshot_id'),
                  reason_codes=list(dict.fromkeys(reasons)),
                  ranking=dict(table_version=ranking.get('table_version'), profile_digest=ranking.get('profile_digest')))
    record['binding'] = routing_gates.binding(copy.deepcopy(workflow), routing.credential_marker(data, record))
    return dict(record=record, vector=item['vector'], account=spec['account'], now=now, revision=item['revision'])
