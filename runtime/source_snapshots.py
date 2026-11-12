"""Policy/runtime source snapshots owned only by one shared observation budget."""

import os
import stat

from runtime import startup


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns)


def read_source(path, budget):
    """Read once, then revalidate the same file version without charging its bytes again."""
    if budget is None:
        return path.read_bytes()
    try:
        startup._entry_budget_check(budget)
        budget['paths'] += 1
        startup._entry_budget_check(budget)
        before = _stamp(path.lstat())
        if not stat.S_ISREG(before[3]):
            raise startup.StartupError('Installed source must be a regular file')
        snapshots = budget.setdefault('source_snapshots', {})
        if path in snapshots:
            previous, raw = snapshots[path]
            if before != previous:
                raise startup.StartupError('Installed source changed during observation')
            startup._entry_budget_check(budget)
            return raw

        limit = min(1024 * 1024, startup.PACKAGE_SCAN_MAX_BYTES - budget['bytes'])
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                if _stamp(os.fstat(descriptor)) != before:
                    raise startup.StartupError('Installed source changed during observation')
                raw = stream.read(limit + 1)
                budget['bytes'] += len(raw)
                if len(raw) > limit:
                    raise startup.StartupError('Installed source exceeds observation limit')
                if _stamp(os.fstat(descriptor)) != before:
                    raise startup.StartupError('Installed source changed during observation')
        finally:
            os.close(descriptor)
        if _stamp(path.lstat()) != before:
            raise startup.StartupError('Installed source changed during observation')
        startup._entry_budget_check(budget)
        snapshots[path] = before, raw
        return raw
    except (OSError, startup.StartupError):
        budget['bytes'] = max(budget['bytes'], startup.PACKAGE_SCAN_MAX_BYTES)
        raise
