"""Runtime state diagnostics. Every failure stays a StateError, so existing StateError handlers keep failing closed."""
import sqlite3
import time

from runtime.startup import ObservationDeadline, StartupError

UNAVAILABLE = 'Runtime state is unavailable, corrupt, locked or incompatible'
# The D4 lock's fixed text (runtime/changes.py _lock); it is contention, so it is reported as busy.
D4_BUSY = 'D4 transaction busy; retry the same bound request'


class StateError(StartupError):
    """Sanitized task/state diagnostic, without private record contents."""


class StateBusy(StateError):
    """Another connection holds the database lock; nothing was found corrupt or incompatible."""


class StateDeadline(StateError, ObservationDeadline):
    """The caller's observation deadline expired while runtime state was in use."""


def database_failure(error, deadline):
    """Name a deadline or lock contention; return None for the generic sanitized error.

    Only fixed texts surface: deadline messages (literals, enforced by tests), D4_BUSY and SQLite's own lock texts.
    Other StartupError messages may embed inputs (setting keys, client names, joined blockers), so they stay generic.
    """
    if isinstance(error, ObservationDeadline):
        return StateDeadline(str(error))
    timed_out = deadline is not None and time.monotonic() >= deadline
    # The progress handler interrupts a query at the deadline; a lock wait cut short by it is a deadline too.
    if isinstance(error, sqlite3.OperationalError) and (str(error) == 'interrupted' or timed_out and str(error) == 'database is locked'):
        return StateDeadline('Runtime state deadline exceeded')
    if isinstance(error, sqlite3.OperationalError) and str(error) == 'database is locked':
        return StateBusy('Runtime state is busy')
    if type(error) is StartupError and str(error) == D4_BUSY:
        return StateBusy(D4_BUSY)
    return None
