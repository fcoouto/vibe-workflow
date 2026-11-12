"""Startup entry observations made for an owner whose aggregate budget has its own bound."""

import os


def source_read(path, budget):
    """Reuse source bytes only within this budget and after revalidating the opened file."""
    from runtime import startup
    if budget is None:
        return path.read_bytes()
    try:
        startup._entry_budget_check(budget)
        budget['paths'] += 1
        startup._entry_budget_check(budget)
        cached = budget.setdefault('installed_sources', {})
        limit = min(1024 * 1024, startup.PACKAGE_SCAN_MAX_BYTES - budget['bytes'])
        with path.open('rb') as stream:
            def stamp():
                info = os.fstat(stream.fileno())
                return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink,
                        info.st_mtime_ns, info.st_ctime_ns)

            before = stamp()
            previous = cached.get(path)
            if previous is not None and previous[0] == before:
                raw = previous[1]
            else:
                raw = stream.read(limit + 1)
                budget['bytes'] += len(raw)
                if len(raw) > limit:
                    raise startup.StartupError('Installed source exceeds observation limit')
            if stamp() != before:
                raise startup.StartupError('Installed source changed during observation')
        startup._entry_budget_check(budget)
        cached[path] = before, raw
        return raw
    except (OSError, startup.StartupError):
        budget['bytes'] = max(budget['bytes'], startup.PACKAGE_SCAN_MAX_BYTES)
        raise


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
