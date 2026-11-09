"""Conditional Git transport primitive, not cleanup permission or qualification.

The delivery owner must authorize the exact repository/ref, inspect hook effects,
and exclude default/protected/shared/dependent branches before calling delete.
The selected executor enforces bounds and pins the supplied inspection throughout
execution, including effective configuration, hooks and existing authentication.
No default executor, credential lookup or unconditional deletion is provided.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.parse import urlsplit

from runtime import delivery, state


class GitRemote:
    def __init__(self, *, executable, root, destination, run):
        delivery._require(os.name == 'posix', 'Git transport requires qualified POSIX descriptor inspection')
        delivery._require(isinstance(executable, str) and Path(executable).is_absolute()
            and Path(executable).is_file() and callable(run), 'Explicit Git executable and executor required')
        state._text(root, 'Git transport checkout', 4096)
        state._text(destination, 'Git transport destination', 4096)
        url = urlsplit(destination)
        explicit = (Path(destination).is_absolute() or
            url.scheme in {'https', 'ssh'} and bool(url.hostname) and url.password is None
                and (url.scheme != 'https' or url.username is None) and not url.query and not url.fragment or
            re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', destination))
        delivery._require(explicit, 'Git transport requires an explicit destination without embedded credentials')
        delivery._require(Path(root).is_absolute() and str(Path(root).resolve()) == root
            and Path(root).is_dir(), 'Git transport checkout is unavailable')
        self.executable, self.root, self.destination, self.run = executable, root, destination, run

    def _call(self, *arguments, inspection=None):
        bounds = dict(timeout=20, limit=128 * 1024)
        if inspection is not None:
            bounds['inspection'] = inspection
        try:
            code, raw = self.run([self.executable, '-C', self.root, *arguments], **bounds)
            delivery._require(type(code) is int and isinstance(raw, bytes) and len(raw) <= bounds['limit'],
                              'Git transport observation is unavailable')
            return code, raw
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            raise state.StateError('Git transport outcome is unavailable; observe before retrying') from None

    def inspect(self):
        code, raw = self._call('rev-parse', '--path-format=absolute', '--show-toplevel', '--git-dir', '--git-common-dir')
        paths = raw.decode('utf-8').splitlines()
        delivery._require(code == 0 and len(paths) == 3 and paths[0] == self.root, 'Git transport identity changed')
        pattern = r'^(core\.(hookspath|sshcommand)|url\..*\.(insteadof|pushinsteadof)|remote\..*\.(mirror|receivepack|pushurl|push|vcs)|push\..*)$'
        code, config = self._call('config', '--null', '--get-regexp', pattern)
        delivery._require(code == 0 or code == 1 and not config, 'Git transport configuration is unavailable')
        code, raw = self._call('rev-parse', '--git-path', 'hooks/pre-push')
        delivery._require(code == 0, 'Git push hook location is unavailable')
        hook = Path(raw.decode('utf-8').strip())
        if not hook.is_absolute():
            hook = Path(self.root) / hook
        hook_data = None
        if hook.exists() or hook.is_symlink():
            target = hook.resolve(strict=True)
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                delivery._require(stat.S_ISREG(info.st_mode) and info.st_size <= 1024 * 1024, 'Git push hook is not bounded')
                raw = stream.read(1024 * 1024 + 1)
                delivery._require(len(raw) <= 1024 * 1024, 'Git push hook changed while inspecting')
            hook_data = dict(path=str(hook), target=str(target), mode=stat.S_IMODE(info.st_mode), digest=hashlib.sha256(raw).hexdigest())
        destination_binding = None
        if Path(self.destination).is_absolute():
            target = Path(self.destination)
            delivery._require(str(target.resolve()) == self.destination, 'Local Git destination must be canonical')
            info = target.stat()
            destination_binding = dict(device=info.st_dev, inode=info.st_ino)
        return dict(version=1, executable=self.executable, paths=paths, destination=self.destination,
                    destination_binding=destination_binding, configuration_digest=hashlib.sha256(config).hexdigest(), pre_push=hook_data)

    def _lookup(self, ref, inspection):
        code, raw = self._call('ls-remote', '--quiet', '--exit-code', '--refs', '--branches', '--',
                               self.destination, ref, inspection=inspection)
        if code == 2 and not raw.strip():
            return None
        rows = raw.splitlines()
        delivery._require(code == 0 and len(rows) == 1, 'Remote ref lookup is unavailable')
        fields = rows[0].split(b'\t')
        delivery._require(len(fields) == 2 and fields[1] == ref.encode('utf-8'), 'Remote ref lookup is inconsistent')
        head = fields[0].decode('ascii')
        delivery._require(re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head), 'Invalid remote object identity')
        return head

    def delete(self, branch, expected_head, *, inspection):
        delivery._branch(branch)
        delivery._require(isinstance(expected_head, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', expected_head),
                          'Exact expected remote object required')
        ref = 'refs/heads/' + branch
        try:
            if self.inspect() != inspection:
                return dict(status='retained', reason='configuration-changed')
            before = self._lookup(ref, inspection)
            if before is None:
                return dict(status='absent')
            if before != expected_head:
                return dict(status='retained', reason='remote-ref-changed')
            if self.inspect() != inspection:
                return dict(status='retained', reason='configuration-changed')
            try:
                self._call('push', '--no-follow-tags', '--recurse-submodules=no',
                    '--force-with-lease=' + ref + ':' + expected_head, '--', self.destination, ':' + ref, inspection=inspection)
            except state.StateError:
                pass  # A lost acknowledgment may follow a successful conditional deletion.
            after = self._lookup(ref, inspection)
            if after is None:
                return dict(status='deleted')
            if after != expected_head:
                return dict(status='retained', reason='remote-ref-changed')
            return dict(status='pending', reason='conditional-delete-unconfirmed')
        except (OSError, ValueError, UnicodeError):
            return dict(status='pending', reason='observation-unavailable')
