"""Validate cached account facts; this module grants no launch or spending authority."""
import copy
import hashlib
import json

from runtime import routing
from runtime.routing_paths import path_of

FIELDS = frozenset({'producer', 'path', 'account', 'pool', 'observed_at', 'valid_until', 'paid_crossover'})
BLOCKED = 'provider_blocked'
CROSSOVER = (BLOCKED, 'provider_managed', 'unknown')
UNVERIFIED = 'billing_unverified'
FRESH_SECONDS = 900
CLOCK_MARGIN = 60


def scope(route):
    return dict(path=path_of(route.get('route')), account=route.get('account'), pool=route.get('pool'))


def problem(found, route, now):
    if type(found) is not dict or set(found) != FIELDS \
            or not all(routing._text(found[key]) for key in ('producer', 'path', 'account', 'pool', 'paid_crossover')) \
            or not all(routing._amount(found[key]) for key in ('observed_at', 'valid_until')) \
            or found['paid_crossover'] not in CROSSOVER or found['valid_until'] < found['observed_at']:
        return 'billing_malformed'
    if found['observed_at'] > now + CLOCK_MARGIN or now - found['observed_at'] > FRESH_SECONDS \
            or found['valid_until'] <= now:
        return 'billing_stale'
    return 'billing_scope_mismatch' if any(found[key] != value for key, value in scope(route).items()) else None


def fact(source, route, now):
    try:
        found = source.billing(copy.deepcopy(route), now)
        return found if problem(found, route, now) is None else None
    except Exception:
        return None


def marker(found, route, now):
    if problem(found, route, now) is not None:
        return None
    raw = ['subscription-account', 1, found['producer'], found['path'], found['account'], found['pool']]
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
