"""Startup entry observations made for an owner whose aggregate budget has its own bound."""


def observe_within(budget, total, observe, *args, **kwargs):
    """Run a startup observation on its own entry budget, then charge budget, which its owner bounds by total.

    Startup's entry checks keep each observation within Codex's bundle bound (PACKAGE_SCAN_MAX_BYTES); the
    owner checks its aggregate (onboarding's project inventory: MAX_TOTAL; any other owner: 8 MiB). Paths and
    the deadline stay shared. A failure exhausts budget, whatever was raised: unknown partial output must not
    leave a reusable allowance.
    """
    entry = {"paths": budget["paths"], "bytes": 0, "deadline": budget.get("deadline")}
    try:
        result = observe(*args, deadline=budget.get("deadline"), budget=entry, **kwargs)
    except BaseException:
        budget["bytes"] = max(budget["bytes"], total + 1)
        raise
    budget["paths"], budget["bytes"] = entry["paths"], budget["bytes"] + entry["bytes"]
    return result
