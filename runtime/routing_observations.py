"""Subscription-only cached eligibility and shortlist; no ledger, gateway or executor."""
import copy
import time

from runtime import routing_funding, subscription_billing
from runtime.storage import (FRESH_SECONDS, EXHAUSTED_RECHECK_SECONDS, CLOCK_MARGIN, WINDOW_UNIT,
                             WINDOW_UNITS, RATE_WINDOWS, _resolved, _read, _locked, _write,
                             window_path, store_window, consumed_at, trust_left)


class LocalSource:
    def __init__(self, settings_path):
        self.settings_path = settings_path

    def acquire_billing(self, routes, now):
        from runtime import opencode_account
        families = tuple(name for name in opencode_account.FAMILIES
                         if any(opencode_account.family(route) == name for route in routes))
        opencode_account.refresh(self.settings_path, families=families)

    def billing(self, route, now):
        from runtime import opencode_account
        return opencode_account.billing_fact(self.settings_path, route, now)

    def quota(self, route, now):
        from runtime import opencode_account
        return opencode_account.quota_problem(self.settings_path, route, now)

    def windows(self, pool, now, stored=None):
        stored = _read(window_path(self.settings_path)) if stored is None else stored
        return [dict(value, id=key) for key, value in stored.get('pools', {}).get(pool, {}).items()]

PAID = {'provider_blocked': 'blocked', 'provider_managed': 'enabled', 'unknown': 'unknown'}


def eligible(routes, source, now=None):
    now = int(time.time()) if now is None else now
    kept, excluded = [], []
    for route in routes:
        found = subscription_billing.fact(source, route, now)
        marker = subscription_billing.marker(found, route, now)
        reason = None
        if marker is None:
            reason = 'billing_unverified'
        else:
            try:
                reason = 'quota_exhausted' if source.quota(copy.deepcopy(route), now) == 'quota_exhausted' else None
            except Exception:
                reason = None  # Only positive, fresh account-bound evidence refuses for quota.
        if reason:
            excluded.append(dict(route=route['route'], reason=reason))
        else:
            kept.append(dict(route, account_marker=marker, paid_overflow=PAID[found['paid_crossover']]))
    return kept, excluded


def shortlist(routes, strategy_ids, source, now=None):
    now = int(time.time()) if now is None else now
    kept, excluded = [], []
    by_id = {item['strategy_id']: item for item in routes}
    for sid in dict.fromkeys(strategy_ids):
        route = by_id.get(sid)
        if route is None:
            continue
        found = subscription_billing.fact(source, route, now)
        marker = subscription_billing.marker(found, route, now)
        reason = None
        if marker is None:
            reason = 'billing_unverified'
        elif found['paid_crossover'] != subscription_billing.BLOCKED:
            reason = 'overflow_not_blocked'
        else:
            try:
                reason = 'quota_exhausted' if source.quota(copy.deepcopy(route), now) == 'quota_exhausted' else None
            except Exception:
                reason = None  # Only positive, fresh account-bound evidence refuses for quota.
        if reason:
            excluded.append(dict(route=route['route'], reason=reason))
        else:
            kept.append(dict(route, account_marker=marker))
    ids = routing_funding.subscription_shortlist(strategy_ids, kept)
    return [route for route in kept if route['strategy_id'] in ids], excluded
