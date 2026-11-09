"""Startup entry observations made for an owner whose aggregate budget has its own, larger bound."""

import subprocess

from runtime.startup import StartupError


def observe_within(budget, total, observe, *args, **kwargs):
    """Run a startup observation on its own entry budget, then charge budget, which its owner bounds by total.

    Startup's entry checks keep each observation within Codex's bundle bound (PACKAGE_SCAN_MAX_BYTES), while
    onboarding's project inventory may reach its own MAX_TOTAL. Paths and the deadline stay shared. A failure
    exhausts budget: unknown partial output must not leave a reusable allowance.
    """
    entry = {"paths": budget["paths"], "bytes": 0, "deadline": budget.get("deadline")}
    try:
        result = observe(*args, deadline=budget.get("deadline"), budget=entry, **kwargs)
    except (StartupError, OSError, subprocess.SubprocessError):
        budget["bytes"] = max(budget["bytes"], total + 1)
        raise
    budget["paths"], budget["bytes"] = entry["paths"], budget["bytes"] + entry["bytes"]
    return result
