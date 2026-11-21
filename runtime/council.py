"""Council roster check: per-seat account readiness only; never dispatch, admit, reserve or retry."""
from pathlib import Path
import time

from runtime import routing_observations, routing_paths

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
