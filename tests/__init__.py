"""Keep the test suite out of the owner's real home.

Importing ``tests`` points HOME at a fresh temporary directory (unless a runner
already did and set VCW_TEST_HOME), rebinds import-time path constants that
captured the real home, and installs an audit hook that refuses file
operations on the real home's workflow state roots. The hook sees only the
path argument: an ``os.open`` relative to a ``dir_fd`` cannot be resolved, so
that gap is covered by refusing every open (even read-only) of the roots
themselves, which is how such descriptors are obtained, and by the redirect.
Symlinks into the roots are not resolved either; the redirect covers them.
Operator tools under tests/qualification run against the real home by design
and are left alone.
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
PATH_EVENTS = {'open': 1, 'os.mkdir': 1, 'os.rmdir': 1, 'os.remove': 1, 'os.truncate': 1, 'os.chmod': 1,
               'os.chown': 1, 'os.utime': 1, 'os.mkfifo': 1, 'os.mknod': 1, 'os.chdir': 1, 'os.setxattr': 1,
               'os.removexattr': 1, 'sqlite3.connect': 1, 'shutil.rmtree': 1, 'shutil.chown': 1,
               'os.rename': 2, 'os.link': 2, 'os.symlink': 2, 'shutil.copyfile': 2, 'shutil.copytree': 2,
               'shutil.move': 2}


class HomeLeakError(RuntimeError):
    """Not an OSError, so code that tolerates OSError cannot swallow it."""


def _temporary(path):
    path = os.path.realpath(path)
    return any(path == root or path.startswith(root + os.sep)
               for root in {os.path.realpath(tempfile.gettempdir()), '/tmp', '/private/tmp'})


real_homes = {os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir), pwd.getpwuid(os.getuid()).pw_dir}
deferred = bool(os.environ.get('VCW_TEST_HOME'))
original = {name: os.environ.get(name) for name in ('HOME', *HOME_VARIABLES)}
if not deferred and original['HOME'] and os.path.isabs(original['HOME']) and not _temporary(original['HOME']):
    real_homes |= {original['HOME'], os.path.realpath(original['HOME'])}
roots = {os.path.normpath(os.path.join(home, state)) for home in real_homes for state in STATE}
for name, children in (('CODEX_HOME', CODEX_STATE), ('XDG_STATE_HOME', ('vibe-workflow',))):
    value = original[name]
    if value and os.path.isabs(value) and not _temporary(value):
        roots |= {os.path.normpath(os.path.join(base, child))
                  for base in (value, os.path.realpath(value)) for child in children}
ROOTS = frozenset(roots)
_ROOT_PREFIXES = tuple(root + '/' for root in ROOTS)
_SCAN_PREFIXES = tuple({os.path.dirname(root) + '/' for root in ROOTS} | {home + '/' for home in real_homes})


def blocked(path):
    """The normalized real-home state path ``path`` names, or None. Unresolvable arguments return None."""
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    elif not isinstance(path, str):
        if isinstance(path, int) or path is None or not hasattr(path, '__fspath__'):
            return None
        return blocked(os.fspath(path))
    if not path.startswith(_SCAN_PREFIXES):
        if path.startswith('/') or '..' not in path:
            return None
        path = os.path.join(os.getcwd(), path)
    path = os.path.normpath(path)
    return path if path in ROOTS or path.startswith(_ROOT_PREFIXES) else None


def audit(event, args):
    count = PATH_EVENTS.get(event)
    if count is None:
        return
    for argument in args[:count]:
        path = blocked(argument)
        if path is not None:
            raise HomeLeakError(f'test isolation: refused {event} on real-home state {path}; tests must use '
                                f'the temporary HOME ({os.environ.get("HOME")}) or patch the path constant')


def rebind(old_homes, new_home):
    """Point module-level Path/str constants under an old home (outside the repo) at the new home."""
    old = tuple(sorted({h for h in old_homes if os.path.realpath(h) != os.path.realpath(new_home)}, key=len,
                       reverse=True))
    for module in list(sys.modules.values()):
        source = getattr(module, '__file__', None) or ''
        if module is sys.modules.get(__name__) or not source.startswith(REPO + os.sep):
            continue
        for name, value in list(vars(module).items()):
            if not isinstance(value, (str, PurePath)):
                continue
            text = os.fspath(value)
            if text.startswith(REPO + os.sep) or text == REPO:
                continue
            for home in old:
                if text.startswith(home + os.sep):
                    moved = os.path.join(new_home, text[len(home) + 1:])
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
        if value and any(os.path.realpath(value).startswith(home + os.sep) for home in real_homes):
            os.environ.pop(variable)
HOME = os.path.realpath(str(Path.home()))
if not operator:
    previous = {original['HOME'], os.path.realpath(original['HOME'])} if original['HOME'] and not deferred else set()
    rebind(real_homes | previous, HOME)
if not operator and not getattr(sys, '_vcw_home_guard', False):
    sys.addaudithook(audit)
    sys._vcw_home_guard = True
