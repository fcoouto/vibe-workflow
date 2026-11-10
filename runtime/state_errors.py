"""Runtime state diagnostics. Every failure stays a StateError, so existing StateError handlers keep failing closed."""
import sqlite3
import time

from runtime.startup import ObservationDeadline, StartupError

UNAVAILABLE = 'Runtime state is unavailable, corrupt, locked or incompatible'


class StateError(StartupError):
    """Sanitized task/state diagnostic, without private record contents."""


class StateBusy(StateError):
    """Another connection holds the database lock; nothing was found corrupt or incompatible."""


class StateDeadline(StateError, ObservationDeadline):
    """The caller's observation deadline expired while runtime state was in use."""


def database_failure(error, deadline):
    """Name a deadline, lock contention or a startup diagnostic; return None for the generic sanitized error."""
    if isinstance(error, ObservationDeadline):
        return StateDeadline(str(error))
    timed_out = deadline is not None and time.monotonic() >= deadline
    # The progress handler interrupts a query at the deadline; a lock wait cut short by it is a deadline too.
    if isinstance(error, sqlite3.OperationalError) and (str(error) == 'interrupted' or timed_out and str(error) == 'database is locked'):
        return StateDeadline('Runtime state deadline exceeded')
    if isinstance(error, sqlite3.OperationalError) and str(error) == 'database is locked':
        return StateBusy('Runtime state is busy')
    # Startup diagnostics carry no input contents by contract; keep their reason instead of a generic one.
    return StateError(str(error)) if isinstance(error, StartupError) else None
