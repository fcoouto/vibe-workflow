"""Optional SessionStart additions: the hook delivery note and the warnings appended to the startup output.

Every step is bounded by the startup deadline and any failure leaves the startup output unchanged. The only
write is the delivery note; nothing here spawns a process, uses the network or calls a model."""

import time


def _optional(step, *args):
    try:
        return step(*args)
    except Exception:
        # Optional additions must preserve the startup result.
        return None


def _deliver(package, client, deadline):
    from runtime import hook_delivery
    hook_delivery.record_delivery(package, client, deadline=deadline)


def _drift(package, client):
    from runtime import package_versions
    return package_versions.drift_warning(package, client)


def _readiness(package, client):
    from runtime import workspace
    return workspace.readiness_warning(package, client)


def _append(output, text):
    from runtime import package_versions
    return package_versions.append_warning(output, text)


def deliver(package, client, deadline):
    """Write the delivery note before the deadline. A diagnostic startup writes it too: trust is per client."""
    if time.monotonic() < deadline:
        _optional(_deliver, package, client, deadline)


def apply(package, client, output, deadline, restricted=False):
    """Write the delivery note, then append the package drift and workspace readiness warnings as one text.

    The restricted qualification-only context gets no warnings."""
    deliver(package, client, deadline)
    if output is None or restricted:
        return output
    warnings = []
    for step in (_drift, _readiness):
        if time.monotonic() >= deadline:
            break
        warning = _optional(step, package, client)
        if warning:
            warnings.append(warning)
    if not warnings or time.monotonic() >= deadline:
        return output
    return _optional(_append, output, " ".join(warnings)) or output
