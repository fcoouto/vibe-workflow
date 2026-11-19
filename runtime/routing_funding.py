"""The bounded subscription shortlist, without admission or allocation."""
SHORTLIST_MAX = 10


def subscription_shortlist(strategy_ids, routes):
    eligible = {route.get('strategy_id') for route in routes
                if route.get('billing_class') == 'included_subscription'}
    found = []
    for sid in strategy_ids:
        if sid in eligible and sid not in found:
            found.append(sid)
            if len(found) == SHORTLIST_MAX:
                break
    return found
