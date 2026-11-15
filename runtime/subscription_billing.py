"""The shared subscription account boundary (F4.5/F4.6, 15 November account amendment): one pure check of one fact.

Every `included_subscription` route, on every family (OpenCode Claude and ChatGPT, native Codex and Claude, OpenCode
Go), needs a current fact that the enabled account behind its exact path and shared pool was read. The collector
checks it before the shortlist cut and any window read, and `routing_launch.admitted_check` checks it again just
before dispatch; any problem is `billing_unverified`. Shared pools do not share account proof: the fact names the
route's own path.

A fact is exactly `{producer, path, account, pool, observed_at, valid_until, paid_crossover}`: `path` is the route
id's path (`routing_paths.path_of`), times are integer seconds with `observed_at` at most FRESH_SECONDS old and not
ahead of the clock beyond CLOCK_MARGIN, and `valid_until` after now. `paid_crossover` reports the account's own
overflow setting (`provider_blocked`, `provider_managed` or `unknown`); none of them disqualifies the account, whose
provider applies its own paid-overflow configuration (the user's 15 November decision). A source answers it through
`billing(route, now)`; only code selects the source. The one production producer is the native Claude account read
(`runtime/claude_usage.py`) for its own path; every other path still has none and is refused. A label, digest, quota
seed, path qualification, zero cost or absent credit is never this fact.
Code-selected sources may acquire missing/stale facts before eligibility through `acquire_billing(routes, now)`.
Acquisition is separate from pure reads.

`double` is the only positive producer here: a code-level double for tests and the sandboxed activation
self-checks, labelled `synthetic:<label>`. It is never wired to `LocalSource`, a request, a setting or the
environment. Parsing a fact is not qualifying a producer; a real one must first be qualified for its own source.
"""
import copy

from runtime import routing, routing_paths, usage_budget

FIELDS = frozenset({'producer', 'path', 'account', 'pool', 'observed_at', 'valid_until', 'paid_crossover'})
BLOCKED = 'provider_blocked'
CROSSOVER = (BLOCKED, 'provider_managed', 'unknown')  # reported, never required
UNVERIFIED = 'billing_unverified'
FRESH_SECONDS = usage_budget.FRESH_SECONDS  # the 900 s freshness the collector applies to window observations
CLOCK_MARGIN = routing.MAX_CLOCK_DRIFT


def scope(route):
    """The `{path, account, pool}` a fact must name for `route`."""
    return dict(path=routing_paths.path_of(route.get('route')), account=route.get('account'), pool=route.get('pool'))


def problem(fact, route, now):
    """Why `fact` does not prove subscription-only billing for `route` at `now`, or None."""
    if fact is None:
        return 'billing_missing'
    if type(fact) is not dict or set(fact) != FIELDS \
            or not all(routing._text(fact[key]) for key in ('producer', 'path', 'account', 'pool', 'paid_crossover')) \
            or not (routing._amount(fact['observed_at']) and routing._amount(fact['valid_until'])) \
            or fact['valid_until'] < fact['observed_at'] or fact['paid_crossover'] not in CROSSOVER:
        return 'billing_malformed'
    if fact['observed_at'] > now + CLOCK_MARGIN:
        return 'billing_future'
    if now - fact['observed_at'] > FRESH_SECONDS or fact['valid_until'] <= now:
        return 'billing_stale'
    if any(fact[key] != value for key, value in scope(route).items()):
        return 'billing_scope_mismatch'
    return None


def verified(source, route, now):
    """Whether `source` holds a valid fact for `route`; a source without `billing`, or one that fails, does not."""
    answer = getattr(source, 'billing', None)
    try:
        fact = answer(copy.deepcopy(route), now) if callable(answer) else None
    except Exception:  # noqa: BLE001 - an unreadable producer is unverified billing, never an error or a pass
        return False
    return problem(fact, route, now) is None


def acquire(source, routes, now):
    """Ask a code-selected source to acquire missing/stale facts before eligibility, never from a pure read.

    The caller supplies statically relevant subscription routes. The source owns verified connection grouping,
    locking/backoff and any incidental window writes; sharing a pool label never establishes shared identity.
    An absent or failed source leaves the ordinary checks in force;
    its return value is never accepted as a billing fact.
    """
    read = getattr(source, 'acquire_billing', None)
    if not callable(read):
        return
    needed = [copy.deepcopy(route) for route in routes
              if route.get('billing_class') == 'included_subscription' and not verified(source, route, now)]
    if needed:
        try:
            read(needed, now)
        except Exception:  # noqa: BLE001 - acquisition failure never supplies authority or leaks native error text
            pass


def double(label):
    """A code-level positive producer `(route, now) -> fact` for tests and sandboxed self-checks only."""
    def produce(route, now):
        return dict(producer='synthetic:' + label, **scope(route), observed_at=now, valid_until=now + FRESH_SECONDS,
                    paid_crossover=BLOCKED)
    return produce
