"""Shared native-reader lifecycle only; no native Codex protocol or model calls."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import signal
import stat
import subprocess
import time

from runtime import storage

LOCK_POLL = 0.02
LOCK_EXTRA = 1.0
STOP_GRACE = 0.3
FAILURE_BACKOFF = 300


class ReadError(Exception):
    """Fixed transport reason only."""


def _absolute_path(value):
    return ':'.join(item for item in (value or '').split(':') if item.startswith('/'))


def _private_dir(value):
    try:
        info = os.lstat(value)
    except (OSError, ValueError):
        return False
    return value.startswith('/') and stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() \
        and info.st_mode & 0o022 == 0


def _exited(pid, seconds):
    # Never reap before signaling the group: its id must not be reused.
    if not hasattr(os, 'waitid'):
        time.sleep(seconds)
        return False
    end = time.monotonic() + seconds
    while True:
        try:
            if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                return True
        except ChildProcessError:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(LOCK_POLL)


def _stop(process, normal):
    try:
        process.stdin.close()
    except OSError:
        pass
    if not (_exited(process.pid, STOP_GRACE) and normal):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except OSError:
                pass
            if sig == signal.SIGTERM:
                _exited(process.pid, STOP_GRACE)
        _exited(process.pid, 1)
    process.wait(timeout=1)
    process.stdout.close()


def _store_dir(settings_path):
    folder = storage._resolved(storage.window_path(settings_path)).parent
    for item in reversed([p for p in (folder, *folder.parents) if not p.exists()]):
        item.mkdir(mode=0o700, exist_ok=True)
    try:
        storage.private(folder, True)
    except storage.StorageError:
        raise ReadError('observation_store_unsafe') from None
    return folder


@contextmanager
def _pool_lock(settings_path, pool, wait=0.0):
    folder = _store_dir(settings_path)
    lock = folder / ('.reader-lock.' + pool)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        storage.private(lock)
        end = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    yield False
                    return
                time.sleep(LOCK_POLL)
        yield True
    finally:
        os.close(fd)
