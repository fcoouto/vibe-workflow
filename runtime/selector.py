"""Compose the tested advisory flow; never dispatch, admit, reserve or retry."""
import hashlib
import os
from pathlib import Path
import time

from runtime import routing, routing_paths, routing_observations, subscription_billing
from runtime import storage


def result(state, reason=None, **extra):
    return dict(state=state, reason=reason, route=None, excluded=[], admission=False,
                reservation=False, spend_permit=False, **extra)


def _configuration(state_dir):
    state_dir = Path(state_dir)
    if not state_dir.is_absolute() or state_dir.is_symlink():
        raise ValueError('configuration_invalid')
    config = storage.read_json(state_dir / 'router.json')
    if config is None:
        return 'unconfigured', None, []
    if type(config) is not dict or set(config) != {'version', 'enabled', 'paths'} \
            or type(config['version']) is not int or config['version'] != 1 \
            or type(config['enabled']) is not bool:
        raise ValueError('configuration_invalid')
    if not config['enabled']:
        return 'disabled', None, []
    table = storage.read_json(state_dir / 'strategy-table.json')
    if table is None:
        return 'table_empty', None, []
    if type(table) is not dict or set(table) != {'schema', 'version', 'rows'} \
            or type(table['schema']) is not int or table['schema'] != 2:
        raise ValueError('configuration_invalid')
    return None, table, routing_paths.generate(config['paths'], table)


def check_setup(*, state_dir):
    """Check local configuration only; never acquire account or quota data."""
    try:
        reason, table, routes = _configuration(state_dir)
        if reason is None:
            if not table['rows']:
                reason = 'table_empty'
            else:
                eligible = {route['strategy_id'] for route in routes}
                valid_keys = {f'{kind}|{complexity}|{risk}' for kind in routing.WORK_KINDS
                              for complexity in routing.COMPLEXITY_BANDS for risk in routing.RISK_BANDS}
                if not any(item['strategy_id'] in eligible for key in valid_keys
                           for item in table['rows'].get(key, ())):
                    reason = 'no_recommendation'
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        reason = 'configuration_invalid'
    return dict(state='ready' if reason is None else 'setup_needed', reason=reason)


def select(request, *, state_dir, source=None):
    try:
        if type(request) is not dict or type(request.get('v')) is not int or request['v'] != 1 \
                or not {'v', 'work_kind'} <= set(request) <= {'v', 'work_kind', 'profile', 'constraints'}:
            raise ValueError('invalid_request')
        scope = 'project:' + hashlib.sha256(os.fsencode(os.path.realpath(Path.cwd()))).hexdigest()[:32]
        profile = routing.ensure_profile(dict(work_kind=request['work_kind'], profile=request.get('profile', {}),
                                             scope_ref=scope))
        # Validate constraints before any acquisition, even when configuration is absent.
        constraints = request.get('constraints', {})
        routing.rank(profile, dict(schema=2, rows={}), constraints)
        reason, table, routes = _configuration(state_dir)
        if reason is not None:
            return result('no_selection', reason)
        ranking = routing.rank(profile, table, constraints)
        routes = [item for item in routes if item['strategy_id'] in ranking['strategy_ids']]
        if not routes:
            return result('no_selection', 'no_recommendation', profile=profile, ranking=ranking)
        source = source if source is not None else routing_observations.LocalSource(Path(state_dir) / 'router.json')
        source.acquire_billing(routes, int(time.time()))
        kept, excluded = routing_observations.shortlist(routes, ranking['strategy_ids'], source)
        chosen = None
        for route in kept:
            now = int(time.time())
            found = subscription_billing.fact(source, route, now)
            if subscription_billing.marker(found, route, now) != route['account_marker']:
                excluded.append(dict(route=route['route'], reason='account_changed'))
            elif found['paid_crossover'] != subscription_billing.BLOCKED:
                excluded.append(dict(route=route['route'], reason='overflow_not_blocked'))
            elif chosen is None:
                chosen = route
        answer = result('selected' if chosen else 'no_selection',
                        None if chosen else 'no_eligible_route', profile=profile, ranking=ranking)
        answer.update(route=chosen, excluded=excluded)
        return answer
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        return result('blocked', 'invalid_request')
