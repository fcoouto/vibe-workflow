"""Council roster check and seat pick; never dispatch, admit, reserve or retry."""
from pathlib import Path
import time

from runtime import routing, routing_observations, routing_paths, scores, selector

MAX_SEATS = 5


def roster(request, *, state_dir, source=None, now=None):
    if type(request) is not dict or set(request) != {'v', 'models'} or type(request['v']) is not int \
            or request['v'] != 1 or type(request['models']) is not list \
            or not 1 <= len(request['models']) <= MAX_SEATS:
        raise ValueError('invalid_request')
    models = request['models']
    found = routing_paths.routes(models)
    if len(found) != len(models):
        raise ValueError('invalid_request')  # A provider that is not on the plan has no seat.
    state_dir, fixed = Path(state_dir), now
    if not state_dir.is_absolute() or state_dir.is_symlink():
        raise ValueError('invalid_request')
    unique = {}
    for item in found:
        unique.setdefault(item['route'], item)
    routes = list(unique.values())
    source = source if source is not None else routing_observations.LocalSource(state_dir / 'router.json')
    source.acquire_billing(routes, int(time.time()) if fixed is None else fixed)
    # Readers stamp the facts they just acquired, so an earlier clock would see them as future.
    current = int(time.time()) if fixed is None else fixed
    kept, excluded = routing_observations.eligible(routes, source, current)
    status = {item['route']: ('ok', item['paid_overflow']) for item in kept}
    status.update({item['route']: (item['reason'], None) for item in excluded})
    seats = [dict(provider=model['provider'], id=model['id'], status=status[item['route']][0],
                  paid_overflow=status[item['route']][1]) for model, item in zip(models, found)]
    return dict(state='checked', seats=seats, admission=False, reservation=False, spend_permit=False)


def pick(request, *, state_dir, source=None, fetch=None, now=None):
    """Seats by the council rule, on Smart Router's shared selection core at level 3."""
    if type(request) is not dict or set(request) != {'v', 'count', 'models'} or type(request['v']) is not int \
            or request['v'] != 1 or type(request['count']) is not int or not 1 <= request['count'] <= MAX_SEATS:
        raise ValueError('invalid_request')
    count = request['count']
    pool = selector._candidates(request['models'], 3, {}, 0, state_dir=state_dir, source=source, fetch=fetch,
                                fixed=now)
    found = [dict(item, key=pool['keys'][item['route']], creator=pool['creators'][item['route']])
             for item in pool['candidates']]
    chosen = selector._confirm(routing.seats(found), pool, count)
    reason = pool['reason'] or (None if len(chosen) == count else 'fewer_seats' if chosen
                                else selector._empty_reason(pool))
    seats = [dict(provider=item['provider'], id=item['model'], variant=item['effort'], score=item['score'],
                  score_source=item['score_source'], creator=item['creator'], paid_overflow=item['paid_overflow'])
             for item in chosen]
    answer = dict(state='picked', reason=reason, seats=seats, excluded=pool['excluded'],
                  attribution=scores.ATTRIBUTION, admission=False, reservation=False, spend_permit=False)
    return selector._refresh_flag(answer, pool)
