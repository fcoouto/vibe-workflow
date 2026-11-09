"""Keep the test suite out of the owner's real home.

Importing ``tests`` points HOME at a fresh temporary directory (unless a runner already did: HOME equals a
canonical, existing VCW_TEST_HOME outside the real home), rebinds import-time state-path constants that captured
the real home, and installs an audit hook that refuses file operations on the real home's workflow state roots,
and destructive ones (rmtree, rename, rmdir, remove) on their ancestors below the home, such as ~/.local or ~/.codex.

What the hook cannot see, and only the redirected HOME prevents:
- a relative name used with a ``dir_fd`` (never resolved against the cwd), e.g. on a descriptor for
  ~/.local/share, which may be opened; a root itself or anything below it may not be opened by path;
- symlinks pointing into a root;
- subprocesses (they inherit the redirected HOME, not the hook), ctypes and C extensions bypassing audited calls;
- ``unittest discover -p`` selecting only modules that never import ``tests``: then this file never runs.
Operator tools under tests/qualification run against the real home by design and are left alone.
"""
import atexit
import os
import pwd
import shutil
import sys
import tempfile
from pathlib import Path, PurePath

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# Workflow state the runtime keeps under a home; see Path.home()/CODEX_HOME/XDG uses in runtime/ and scripts/.
STATE = ('.local/share/vcw-transactions', '.vibe-workflow', '.vcw-openrouter-consent', '.local/state/vibe-workflow',
         '.codex/workstreams-recovery', '.codex/workstreams-resources',
         '.claude/plugins/cache/vibe-workflow-local', '.codex/plugins/cache/vibe-workflow-local')
CODEX_STATE = ('workstreams-recovery', 'workstreams-resources', 'plugins/cache/vibe-workflow-local')
HOME_VARIABLES = ('CODEX_HOME', 'XDG_STATE_HOME', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME')
N = None  # event -> (path argument index, dir_fd argument index or None) pairs, from the audit events table
PATH_EVENTS = {'open': ((0, N),), 'os.mkdir': ((0, 2),), 'os.rmdir': ((0, 1),), 'os.remove': ((0, 1),),
               'os.truncate': ((0, N),), 'os.chmod': ((0, 2),), 'os.chown': ((0, 3),), 'os.utime': ((0, 3),),
               'os.mkfifo': ((0, 2),), 'os.mknod': ((0, 3),), 'os.chdir': ((0, N),), 'os.setxattr': ((0, N),),
               'os.removexattr': ((0, N),), 'sqlite3.connect': ((0, N),), 'shutil.rmtree': ((0, 1),),
               'shutil.chown': ((0, N),), 'os.rename': ((0, 2), (1, 3)), 'os.link': ((0, 2), (1, 3)),
               'os.symlink': ((1, 2),), 'shutil.copyfile': ((0, N), (1, N)), 'shutil.copytree': ((0, N), (1, N)),
               'shutil.move': ((0, N), (1, N))}
DESTRUCTIVE = frozenset({'shutil.rmtree', 'os.rename', 'os.rmdir', 'os.remove', 'shutil.move'})


class HomeLeakError(RuntimeError):
    """Not an OSError, so code that tolerates OSError cannot swallow it."""


def _temporary(path):
    path = os.path.realpath(path)
    return any(path == root or path.startswith(root + os.sep)
               for root in {os.path.realpath(tempfile.gettempdir()), '/tmp', '/private/tmp'})


def _inside(path, base):
    return path == base or path.startswith(base + os.sep)


account_homes = {os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir), pwd.getpwuid(os.getuid()).pw_dir}
preset, original = os.environ.get('VCW_TEST_HOME'), {name: os.environ.get(name) for name in ('HOME', *HOME_VARIABLES)}
# Defer only to a runner-made home: HOME is that same canonical, existing directory outside the real home.
deferred = bool(preset and preset == original['HOME'] and os.path.isabs(preset) and os.path.realpath(preset) == preset
                and os.path.isdir(preset) and not any(_inside(preset, home) for home in account_homes))
real_homes = set(account_homes)
if not deferred and original['HOME'] and os.path.isabs(original['HOME']) and not _temporary(original['HOME']):
    real_homes |= {original['HOME'], os.path.realpath(original['HOME'])}
roots, ancestors = set(), set()
bases = [(home, STATE, False) for home in real_homes]
for name, children in (('CODEX_HOME', CODEX_STATE), ('XDG_STATE_HOME', ('vibe-workflow',))):
    value = original[name]
    if value and os.path.isabs(value) and not _temporary(value):
        bases += [(base, children, True) for base in (value, os.path.realpath(value))]
for base, children, include_base in bases:
    for child in children:
        roots.add(os.path.normpath(os.path.join(base, child)))
        parent = os.path.dirname(child)
        while parent:
            ancestors.add(os.path.normpath(os.path.join(base, parent)))
            parent = os.path.dirname(parent)
        if include_base:
            ancestors.add(os.path.normpath(base))
ROOTS, ANCESTORS = frozenset(roots), frozenset(ancestors - roots)
_ROOT_PREFIXES = tuple(root + '/' for root in ROOTS)
_SCAN_PREFIXES = tuple({os.path.dirname(root) + '/' for root in ROOTS | ANCESTORS} | {h + '/' for h in real_homes})


_cwd = [None]


def blocked(path, destructive=False):
    """The normalized real-home state path ``path`` names, or None. Unresolvable arguments return None."""
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    elif not isinstance(path, str):
        if isinstance(path, int) or path is None or not hasattr(path, '__fspath__'):
            return None
        return blocked(os.fspath(path), destructive)
    if not path.startswith('/'):
        # The cwd may be inside a root (fchdir, inherited cwd); cached because getcwd costs ~8 us on macOS.
        cwd = _cwd[0]
        if cwd is None:
            try:
                cwd = _cwd[0] = os.getcwd()
            except OSError:
                return None
        path = os.path.join(cwd, path)
    if not path.startswith(_SCAN_PREFIXES):
        return None
    path = os.path.normpath(path)
    if path in ROOTS or path.startswith(_ROOT_PREFIXES) or (destructive and path in ANCESTORS):
        return path
    return None


def audit(event, args):
    spec = PATH_EVENTS.get(event)
    if spec is None:
        return
    if event == 'os.chdir':
        _cwd[0] = None  # os.chdir and os.fchdir; a racing thread can briefly see the previous directory
    for index, fd_index in spec:
        argument = args[index] if index < len(args) else None
        if fd_index is not None and fd_index < len(args) and args[fd_index] is not None:
            try:  # relative to a descriptor, not the cwd: unresolvable (see the docstring); absolute still checked
                if not os.fsdecode(os.fspath(argument)).startswith('/'):
                    continue
            except TypeError:
                continue
        path = blocked(argument, event in DESTRUCTIVE)
        if path is not None:
            raise HomeLeakError(f'test isolation: refused {event} on real-home state {path}; tests must use '
                                f'the temporary HOME ({os.environ.get("HOME")}) or patch the path constant')


def _state_relative(relative):
    """Whether a path relative to a home is the home, ~/.local/share, or a state root or below."""
    return relative in ('', '.local/share') or any(_inside(relative, state) for state in STATE)


def rebind(old_homes, new_home, modules=None):
    """Point repo modules' state-path constants under an old home at the new home; leave other paths alone."""
    old = tuple(sorted({h for h in old_homes if os.path.realpath(h) != os.path.realpath(new_home)}, key=len,
                       reverse=True))
    for module in list(sys.modules.values() if modules is None else modules):
        source = getattr(module, '__file__', None)
        if module is sys.modules.get(__name__) or not isinstance(source, str):
            continue
        if not source.startswith(REPO + os.sep) and not os.path.realpath(source).startswith(REPO + os.sep):
            continue
        for name, value in list(vars(module).items()):
            if not isinstance(value, (str, PurePath)):
                continue
            text = os.fspath(value)
            for home in old:
                if _inside(text, home):
                    relative = os.path.normpath(text[len(home):].lstrip(os.sep) or '.')
                    relative = '' if relative == '.' else relative
                    if _state_relative(relative):
                        moved = os.path.join(new_home, relative) if relative else new_home
                        setattr(module, name, type(value)(moved) if isinstance(value, PurePath) else moved)
                    break


def _cleanup(home):
    try:
        share = os.path.join(home, '.local', 'share')
        leaked = sorted(os.path.join(directory, entry) for directory, folders, files in os.walk(home)
                        for entry in folders + files) if os.path.isdir(home) else []
        leaked = [path for path in leaked if path not in (os.path.dirname(share), share)]
        if leaked:
            print(f'test isolation: {len(leaked)} path(s) left in the temporary HOME, e.g. '
                  + ', '.join(leaked[:3]), file=sys.stderr)
        shutil.rmtree(home, ignore_errors=True)
    except BaseException:
        pass


created = None
# Operator tools under tests/qualification (opencode_live, opencode_inspect) need the real home by design.
main = getattr(sys.modules.get('__main__'), '__file__', None) or os.devnull
operator = os.path.realpath(main).startswith(os.path.join(REPO, 'tests', 'qualification') + os.sep)
if not operator and not deferred:
    created = os.path.realpath(tempfile.mkdtemp(prefix='vcw-test-home-'))
    os.makedirs(os.path.join(created, '.local', 'share'))
    os.environ['HOME'] = os.environ['VCW_TEST_HOME'] = created
    for variable in HOME_VARIABLES:
        os.environ.pop(variable, None)
    atexit.register(_cleanup, created)
elif not operator:
    for variable in HOME_VARIABLES:
        value = os.environ.get(variable)
        if value and any(_inside(os.path.realpath(value), home) for home in real_homes):
            os.environ.pop(variable)
HOME = os.path.realpath(str(Path.home()))
if not operator:
    previous = {original['HOME'], os.path.realpath(original['HOME'])} if original['HOME'] and not deferred else set()
    rebind(real_homes | previous, HOME)
if not operator and not getattr(sys, '_vcw_home_guard', False):
    sys.addaudithook(audit)
    sys._vcw_home_guard = True
