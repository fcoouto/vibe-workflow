"""Lexical ordinary-peer byte witness, not semantic or closure authority.

The counter covers all new owned physical calls and directory visits. Existing
semantic readers retain their own checks/bounds outside it; this is not a total
OS-operation counter. The deadline rejects late results, not blocked syscalls.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import time

from runtime import state

MAX_BYTES = 4 * 1024 * 1024
MAX_PATHS = 4096
CHUNK = 65536
SECONDS = 5.0


def require(value, reason):
    if not value:
        raise state.StateError(reason)


def physical(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def private(info, directory=False):
    require(info.st_uid == os.getuid() and not info.st_mode & 0o077
            and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
            'Ordinary closure physical type, privacy or links changed')


class Budget:
    def __init__(self, started):
        self.deadline = started + SECONDS
        self.paths = self.reserved = self.requested = self.returned = self.read_calls = 0

    def check(self):
        require(time.monotonic() < self.deadline, 'Ordinary closure acceptance deadline exhausted')

    def call(self, function, *args, **kwargs):
        self.check()
        require(self.paths < MAX_PATHS, 'Ordinary closure physical operation budget exhausted')
        self.paths += 1
        result = function(*args, **kwargs)
        try:
            self.check()
        except BaseException:
            if function is os.open:
                os.close(result)
            elif function is os.scandir:
                result.close()
            raise
        return result

    def stat(self, path, *, missing=False, dir_fd=None):
        try:
            return self.call(os.stat, path, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            if not missing:
                raise

    def reserve(self, size):
        self.check()
        require(self.reserved + size <= MAX_BYTES, 'Ordinary closure four-pass byte reservation exhausted')
        self.reserved += size
        return dict(limit=size, spent=0)

    def read(self, reservation, descriptor, count, offset):
        self.check()
        require(0 < count <= CHUNK and reservation['spent'] + count <= reservation['limit']
                and self.requested + count <= MAX_BYTES, 'Ordinary closure requested byte budget exhausted')
        reservation['spent'] += count
        self.requested += count
        self.read_calls += 1
        raw = os.pread(descriptor, count, offset)
        self.returned += len(raw)
        self.check()
        return raw

    @contextmanager
    def parent(self, path):
        path = Path(path)
        require(path.is_absolute() and str(path) == os.path.normpath(str(path)), 'Ordinary closure path is not canonical')
        descriptor = self.call(os.open, '/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parents = {'/': physical(self.call(os.fstat, descriptor))}
            current = Path('/')
            for component in path.parts[1:-1]:
                info = self.stat(component, dir_fd=descriptor)
                require(stat.S_ISDIR(info.st_mode), 'Ordinary closure ancestor is not a physical directory')
                child = self.call(os.open, component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                try:
                    require(physical(self.call(os.fstat, child)) == physical(info), 'Ordinary closure ancestor changed while opening')
                except BaseException:
                    os.close(child)
                    raise
                os.close(descriptor)
                descriptor = child
                current /= component
                parents[str(current)] = physical(info)
            yield descriptor, parents
        finally:
            os.close(descriptor)

    def observe(self, path):
        with self.parent(path) as (descriptor, parents):
            info = self.stat(Path(path).name, dir_fd=descriptor, missing=True)
            return dict(parents=parents, stat=None if info is None else physical(info))

    def names(self, path, expected_observation):
        names = []
        path = Path(path)
        require(expected_observation['stat'] is not None, 'Ordinary closure directory expected observation is absent')
        with self.parent(path) as (parent, parents):
            require(parents == expected_observation['parents'], 'Ordinary closure directory expected ancestors changed')
            info = self.stat(path.name, dir_fd=parent)
            require(physical(info) == expected_observation['stat'], 'Ordinary closure directory expected identity changed')
            private(info, True)
            descriptor = self.call(os.open, path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                opened = self.call(os.fstat, descriptor)
                private(opened, True)
                require(physical(opened) == expected_observation['stat'] and self.observe(path) == expected_observation,
                        'Ordinary closure directory changed before enumeration')
                with self.call(os.scandir, descriptor) as entries:
                    while True:
                        try:
                            entry = self.call(next, entries)
                        except StopIteration:
                            break
                        require(len(names) < 4096, 'Ordinary closure directory observation exceeds bound')
                        names.append(entry.name)
                require(physical(self.call(os.fstat, descriptor)) == expected_observation['stat']
                        and self.observe(path) == expected_observation, 'Ordinary closure directory changed during enumeration')
            finally:
                os.close(descriptor)
        return sorted(names)


class PeerWitness:
    """Own descriptors until return; only deterministic comparison facts escape."""
    def __init__(self, path, budget, forbidden):
        self.path, self.budget, self.forbidden = Path(path), budget, forbidden
        self.files = {}
        self.passes = 0
        self.failed = False
        self.facts = None
        self.stack = None

    def sidecars(self, descriptor):
        for suffix in ('-wal', '-shm'):
            require(self.budget.stat(self.path.name + suffix, dir_fd=descriptor, missing=True) is None,
                    'Ordinary closure does not support WAL/SHM')

    def __enter__(self):
        self.stack = self.budget.parent(self.path)
        try:
            self.directory, self.parents = self.stack.__enter__()
            private(self.budget.call(os.fstat, self.directory), True)
            self.sidecars(self.directory)
            for name in (self.path.name, self.path.name + '-journal'):
                info = self.budget.stat(name, dir_fd=self.directory, missing=True)
                if info is None:
                    require(name != self.path.name, 'Ordinary closure peer database is missing')
                    continue
                private(info)
                descriptor = self.budget.call(os.open, name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.directory)
                self.files[name] = dict(descriptor=descriptor, stat=physical(info))
                require(physical(self.budget.call(os.fstat, descriptor)) == physical(info), 'Ordinary closure opened another file')
            identities = [tuple(item['stat'][:2]) for item in self.files.values()]
            require(len(set(identities)) == len(identities) and self.forbidden not in identities,
                    'Ordinary closure peer/journal aliases an authority')
            self.reservation = self.budget.reserve(4 * sum(item['stat'][5] + 1 for item in self.files.values()))
            self.check_paths()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def check_paths(self):
        require(self.stack is not None, 'Ordinary closure witness lifetime ended')
        with self.budget.parent(self.path) as (directory, parents):
            require(parents == self.parents and physical(self.budget.call(os.fstat, self.directory)) ==
                    self.parents[str(self.path.parent)], 'Ordinary closure peer ancestor changed')
            self.sidecars(directory)
            for name in (self.path.name, self.path.name + '-journal'):
                current = self.budget.stat(name, dir_fd=directory, missing=True)
                item = self.files.get(name)
                if item is None:
                    require(current is None, 'Ordinary closure journal appeared')
                    continue
                require(current is not None, 'Ordinary closure peer file disappeared')
                private(current)
                require(physical(current) == item['stat']
                        and physical(self.budget.stat(name, dir_fd=self.directory)) == item['stat']
                        and physical(self.budget.call(os.fstat, item['descriptor'])) == item['stat'],
                        'Ordinary closure peer file identity changed')

    def read_pass(self, number):
        require(not self.failed, 'Ordinary closure failed witness cannot be reused')
        require(number == self.passes + 1 and number <= 4, 'Ordinary closure byte pass order changed')
        try:
            return self._read_pass(number)
        except BaseException:
            self.failed = True
            raise

    def _read_pass(self, number):
        self.check_paths()
        files = {}
        for name, item in self.files.items():
            digest = hashlib.sha256()
            offset, size = 0, item['stat'][5]
            while offset < size:
                count = min(CHUNK, size - offset)
                raw = self.budget.read(self.reservation, item['descriptor'], count, offset)
                require(len(raw) == count, 'Ordinary closure peer read is short')
                digest.update(raw)
                offset += count
            require(self.budget.read(self.reservation, item['descriptor'], 1, size) == b'', 'Ordinary closure peer exceeded EOF')
            require(physical(self.budget.call(os.fstat, item['descriptor'])) == item['stat'], 'Ordinary closure live descriptor changed')
            files[name] = dict(stat=item['stat'], sha256=digest.hexdigest())
        self.check_paths()
        actual = dict(parents=self.parents, files=files)
        require(self.facts is None or actual == self.facts, 'Ordinary closure peer bytes changed')
        self.facts = actual
        self.passes = number
        return actual

    def __exit__(self, *unused):
        failure = None
        try:
            for item in self.files.values():
                try:os.close(item['descriptor'])
                except OSError as error:
                    if failure is None:failure = error
            self.files.clear()
        finally:
            if self.stack is not None:
                stack, self.stack = self.stack, None
                stack.__exit__(None, None, None)
        if failure is not None:raise failure
