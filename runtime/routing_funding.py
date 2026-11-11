"""The ranked funding decision and the admission record of `routing._decide` (F5.2, moved without a behaviour change).

`routing._decide` imports this module locally and calls it; this module imports `routing`, never the reverse at module
import time. Routing's own helpers are read through the module (`routing.<name>`), as `_decide` reads them.
"""
import copy

from runtime import routing, routing_allocator as allocator, routing_gates


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
        e = routing._expiry(by_class['api'], observations, settings, now)
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
                  pressure=dict(u=u, e=e, **routing._snapshot(item, now, horizon)), reserved=dict(item['vector']),
                  settings_revision=settings.get('revision', 0), organization_revision=item['revision'], at=now,
                  mode=mode,
                  snapshot_id=observations.get('snapshot_id'),
                  reason_codes=list(dict.fromkeys(reasons)),
                  ranking=dict(table_version=ranking.get('table_version'), profile_digest=ranking.get('profile_digest')))
    record['binding'] = routing_gates.binding(copy.deepcopy(workflow), routing.credential_marker(data, record))
    return dict(record=record, vector=item['vector'], account=spec['account'], now=now, revision=item['revision'])
