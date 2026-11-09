import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path, PurePath
from unittest.mock import patch

import tests
from runtime import changes, openrouter_settings

ROOT = Path(__file__).resolve().parents[1]
REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def run(code, *argv, **environment):
    """Run ``code`` in a fresh interpreter from the repository root; return (exit code, stdout, stderr)."""
    env = {key: value for key, value in os.environ.items() if key not in ('VCW_TEST_HOME', 'PYTHONPATH')}
    env.update(environment)
    result = subprocess.run([sys.executable, '-B', '-c', code, *argv], cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=60)
    return result.returncode, result.stdout, result.stderr


def home_constants(modules, homes):
    """Module-level Path/str attributes of runtime modules under ``homes`` but outside the repository."""
    found = []
    for name, module in list(modules.items()):
        if name != 'runtime' and not name.startswith('runtime.'):
            continue
        for attribute, value in list(vars(module).items()):
            text = os.fspath(value) if isinstance(value, (str, PurePath)) else ''
            inside = text == str(ROOT) or text.startswith(str(ROOT) + os.sep)
            if not inside and any(text.startswith(home + os.sep) for home in homes):
                found.append(f'{name}.{attribute}={text}')
    return found


class TemporaryHomeTests(unittest.TestCase):
    def test_home_is_a_canonical_temporary_directory(self):
        home = os.environ['HOME']
        self.assertEqual(os.environ['VCW_TEST_HOME'], home)
        self.assertEqual(os.path.realpath(home), home)
        self.assertTrue(os.path.isdir(os.path.join(home, '.local', 'share')))
        self.assertFalse(home == REAL_HOME or home.startswith(REAL_HOME + os.sep))
        self.assertFalse(home.startswith(str(ROOT) + os.sep))
        self.assertEqual(str(Path.home()), home)
        for variable in ('CODEX_HOME', 'XDG_STATE_HOME'):
            self.assertFalse((os.environ.get(variable) or '').startswith(REAL_HOME + os.sep), variable)

    def test_import_time_constants_live_under_the_temporary_home(self):
        home = Path(os.environ['HOME'])
        self.assertEqual(changes.LOCK_ROOT, home / '.local/share/vcw-transactions')
        self.assertEqual(openrouter_settings.BASIC_STORAGE, home / '.vcw-openrouter-consent')

    def test_no_loaded_runtime_module_keeps_a_real_home_path(self):
        self.assertEqual(home_constants(sys.modules, (REAL_HOME,)), [])

    def test_every_runtime_module_imported_before_the_redirect_is_rebound(self):
        # Discover mode may import runtime before `tests`; any import-time home constant must move.
        code = ("import importlib, os, pkgutil, sys, runtime\n"
                "for m in pkgutil.walk_packages(runtime.__path__, 'runtime.'):\n"
                "    importlib.import_module(m.name)\n"
                "import tests, tests.test_isolation as t\nh = os.environ['VCW_ORIGINAL_HOME']\n"
                "print(len(sys.modules), t.home_constants(sys.modules, (h, os.path.realpath(h))))")
        with tempfile.TemporaryDirectory() as home:
            status, out, err = run(code, HOME=home, VCW_ORIGINAL_HOME=home)
        self.assertEqual(status, 0, err)
        self.assertTrue(out.endswith(' []\n'), out)

    def test_rebind_moves_constants_captured_before_the_redirect(self):
        code = ("import runtime.changes as c, runtime.openrouter_settings as o, runtime.usage_settings\n"
                "before = str(c.LOCK_ROOT)\nimport tests\n"
                "print(before); print(c.LOCK_ROOT); print(o.BASIC_STORAGE); print(tests.HOME)")
        with tempfile.TemporaryDirectory() as home:
            code_, out, err = run(code, HOME=home)
        self.assertEqual(code_, 0, err)
        before, lock, storage, new = out.split()
        self.assertTrue(before.startswith(home))
        self.assertEqual((lock, storage), (new + '/.local/share/vcw-transactions', new + '/.vcw-openrouter-consent'))


class GuardTests(unittest.TestCase):
    def test_expected_state_roots_are_guarded(self):
        for state in tests.STATE:
            self.assertIn(os.path.join(REAL_HOME, state), tests.ROOTS)

    def test_synthetic_events_on_real_roots_are_refused(self):
        lock = os.path.join(REAL_HOME, '.local/share/vcw-transactions')
        events = [('open', (lock + '/x', 'w', 0)), ('open', (lock, None, os.O_RDONLY)),
                  ('open', (os.fsencode(lock + '/x'), 'r', 0)), ('open', (Path(lock) / 'x', 'a', 0)),
                  ('os.mkdir', (lock + '/y', 0o777, None)), ('os.remove', (lock + '/x', None)),
                  ('os.rename', ('/tmp/a', lock + '/b', None, None)), ('os.rmdir', (lock, None)),
                  ('sqlite3.connect', (os.path.join(REAL_HOME, '.vibe-workflow/f.sqlite3'),)),
                  ('shutil.rmtree', (os.path.join(REAL_HOME, '.codex/workstreams-resources'), None)),
                  ('open', (REAL_HOME + '/./.vcw-openrouter-consent/../.vcw-openrouter-consent/k', 'w', 0))]
        events += [('open', (root + '/probe', 'w', 0)) for root in tests.ROOTS if root.startswith(REAL_HOME + '/')]
        for event, args in events:
            with self.subTest(event=event, args=args), self.assertRaisesRegex(tests.HomeLeakError, 'real-home'):
                tests.audit(event, args)

    def test_unrelated_paths_and_arguments_pass(self):
        home = os.environ['HOME']
        allowed = [('open', (home + '/.local/share/vcw-transactions/x', 'w', 0)), ('open', ('/tmp/x', 'w', 0)),
                   ('open', (str(ROOT / 'README.md'), 'r', 0)), ('open', (3, 'r', 0)), ('open', ('rel', 'w', 0)),
                   ('open', (REAL_HOME + '/.vibe-workflowx/y', 'w', 0)), ('open', (REAL_HOME + '/.codex/x', 'w', 0)),
                   ('os.mkdir', ('/private/tmp/z', 0o777, None)), ('os.listdir', (REAL_HOME + '/.vibe-workflow',)),
                   ('sqlite3.connect', (':memory:',)), ('compile', (b'', '<x>')), ('open', (None, 'r', 0))]
        for event, args in allowed:
            with self.subTest(event=event, args=args):
                tests.audit(event, args)

    def test_real_open_is_refused_before_it_runs(self):
        # The hook raises before CPython opens anything, so a broken guard still writes nowhere:
        # the target directory never exists for this name.
        target = os.path.join(REAL_HOME, '.vibe-workflow', 'vcw-isolation-probe-never-created', 'x')
        self.assertFalse(issubclass(tests.HomeLeakError, OSError))  # `except OSError` cannot swallow it
        with self.assertRaises(tests.HomeLeakError):
            open(target, 'w')
        with self.assertRaises(tests.HomeLeakError):
            os.open(os.path.dirname(target), os.O_RDONLY)

    def test_install_is_idempotent(self):
        code = ("import importlib, sys, tests\nhooks = []\n"
                "orig = sys.addaudithook\nsys.addaudithook = lambda h: hooks.append(h)\n"
                "importlib.reload(tests)\nprint(len(hooks), sys._vcw_home_guard)")
        status, out, err = run(code)
        self.assertEqual(status, 0, err)
        self.assertEqual(out.split(), ['0', 'True'])


class LifecycleTests(unittest.TestCase):
    def test_qualification_operator_tools_keep_the_real_home(self):
        code = ("import os, sys\nsys.modules['__main__'].__file__ = 'tests/qualification/opencode_live.py'\n"
                "import tests\nprint(os.environ['HOME']); print(getattr(sys, '_vcw_home_guard', False))")
        with tempfile.TemporaryDirectory() as home:
            status, out, err = run(code, HOME=home)
        self.assertEqual(status, 0, err)
        self.assertEqual(out.split(), [home, 'False'])

    def test_created_home_is_removed_at_exit(self):
        status, out, err = run("import tests\nprint(tests.created)")
        self.assertEqual(status, 0, err)
        self.assertFalse(os.path.exists(out.strip()))
        self.assertEqual(err, '')

    def test_leftovers_are_reported_and_removed(self):
        code = ("import os, tests\nos.makedirs(os.path.join(tests.created, '.local/share/vcw-transactions'))\n"
                "open(os.path.join(tests.created, 'stray'), 'w').close()\nprint(tests.created)")
        status, out, err = run(code)
        self.assertEqual(status, 0, err)
        self.assertFalse(os.path.exists(out.strip()))
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn('2 path(s) left in the temporary HOME', err)
        self.assertIn('vcw-transactions', err)


class RebindFilterTests(unittest.TestCase):
    def rebound(self, source, **constants):
        """Rebind a synthetic module loaded from ``source`` against the real home; return its constants."""
        module = types.ModuleType('vcw_isolation_fake')
        module.__file__ = str(source)
        vars(module).update(constants)
        tests.rebind({REAL_HOME}, tests.HOME, [module])
        return {name: getattr(module, name) for name in constants}

    def test_only_state_paths_move(self):
        home, new = Path(REAL_HOME), Path(tests.HOME)
        moved = {'LOCK': str(home / '.local/share/vcw-transactions'), 'MACHINE': home / '.vibe-workflow/machine.json',
                 'SHARE': str(home / '.local/share'), 'HOME_PATH': home, 'USAGE': home / '.local/state/vibe-workflow/u'}
        kept = {'NODE': str(home / '.local/state/fnm_multishells/1_2/bin/node'), 'GITCONFIG': str(home / '.gitconfig'),
                'OPENCODE': str(home / '.local/share/opencode/bin'), 'NEAR': str(home / '.vibe-workflowx/y'),
                'CODEX': home / '.codex/config.toml', 'LOCAL': str(home / '.local'), 'TEXT': 'plain'}
        result = self.rebound(ROOT / 'tests' / 'vcw_isolation_fake.py', **moved, **kept)
        for name, value in moved.items():
            relative = os.path.relpath(os.fspath(value), REAL_HOME)
            expected = new if relative == '.' else new / relative
            self.assertEqual(result[name], type(value)(expected), name)
        self.assertEqual({name: result[name] for name in kept}, kept)

    def test_symlinked_checkout_still_rebinds(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'checkout'
            link.symlink_to(ROOT, target_is_directory=True)
            result = self.rebound(link / 'tests' / 'vcw_isolation_fake.py',
                                  LOCK=REAL_HOME + '/.local/share/vcw-transactions')
        self.assertEqual(result['LOCK'], tests.HOME + '/.local/share/vcw-transactions')

    def test_modules_outside_the_repository_are_left_alone(self):
        lock = REAL_HOME + '/.local/share/vcw-transactions'
        self.assertEqual(self.rebound('/private/tmp/elsewhere.py', LOCK=lock)['LOCK'], lock)


class DeferralTests(unittest.TestCase):
    PROBE = "import os, tests\nprint(os.environ['HOME'], os.environ['VCW_TEST_HOME'], tests.created, sep='\\n')"

    def probe(self, home, preset):
        environment = {'HOME': home} if preset is None else {'HOME': home, 'VCW_TEST_HOME': preset}
        status, out, err = run(self.PROBE, **environment)
        self.assertEqual(status, 0, err)
        return out.split('\n')[:3]

    def assertRedirected(self, home, preset):
        new, variable, created = self.probe(home, preset)
        self.assertEqual((new, variable), (created, created), (home, preset))
        self.assertNotEqual(new, home)
        self.assertTrue(created.startswith(os.path.realpath(tempfile.gettempdir())), created)
        self.assertFalse(os.path.exists(created), 'the created HOME must be removed at exit')

    def test_stale_or_invalid_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first, second = os.path.realpath(first), os.path.realpath(second)
            cases = [(first, None), (first, '1'), (REAL_HOME, '1'), ('relative/home', 'relative/home'),
                     (first + '/missing', first + '/missing'), (REAL_HOME, REAL_HOME), (second, first),
                     (first + '/.', first + '/.'), ('/', '/')]
            if str(ROOT).startswith(REAL_HOME + os.sep):
                cases.append((str(ROOT), str(ROOT)))  # an existing directory under the real home
            unresolved = tempfile.gettempdir()
            if os.path.realpath(unresolved) != unresolved:
                cases.append((unresolved, unresolved))
            for home, preset in cases:
                with self.subTest(home=home, preset=preset):
                    self.assertRedirected(home, preset)

    def test_valid_runner_home_is_kept(self):
        with tempfile.TemporaryDirectory() as home:
            home = os.path.realpath(home)
            self.assertEqual(self.probe(home, home), [home, home, 'None'])
            self.assertTrue(os.path.isdir(home))


class AncestorGuardTests(unittest.TestCase):
    def test_destructive_operations_on_state_parents_are_refused(self):
        home = REAL_HOME
        events = [('shutil.rmtree', (home + '/.local', None)), ('shutil.rmtree', (home + '/.codex', None)),
                  ('shutil.rmtree', (home + '/.local/share/', None)), ('shutil.rmtree', (home + '/.claude', None)),
                  ('os.rename', (home + '/.local/state', '/tmp/x', None, None)),
                  ('os.rename', ('/tmp/x', home + '/.codex/plugins', None, None)),
                  ('os.rmdir', (home + '/.local/share', None)), ('os.remove', (home + '/.local/state', None)),
                  ('shutil.move', (home + '/.codex/plugins/cache', '/tmp/y'))]
        for event, args in events:
            with self.subTest(event=event, args=args), self.assertRaisesRegex(tests.HomeLeakError, 'real-home'):
                tests.audit(event, args)
        allowed = [('shutil.rmtree', (home, None)), ('open', (home + '/.local/share/x', 'w', 0)),
                   ('open', (home + '/.local/share', None, os.O_RDONLY)),
                   ('os.mkdir', (home + '/.codex/n', 0o777, None)),
                   ('shutil.rmtree', (home + '/.local/share/opencode', None)), ('shutil.rmtree', ('/tmp/.local', None)),
                   ('os.rename', ('/tmp/a', '/tmp/.codex', None, None))]
        for event, args in allowed:
            with self.subTest(event=event, args=args):
                tests.audit(event, args)

    def test_relative_paths_of_dir_fd_events_resolve_against_the_current_cwd(self):
        lock = REAL_HOME + '/.local/share/vcw-transactions'
        with patch('os.getcwd', return_value=lock):
            for event, args in (('os.mkdir', ('sub', 0o777, -1)), ('os.mkdir', ('sub', 0o777, None)),
                                ('os.remove', ('x', -1)), ('os.rename', ('/tmp/a', 'b', -1, -1)),
                                ('os.symlink', ('/tmp/t', 'l', -1)), ('os.utime', ('x', None, None, -1))):
                with self.subTest(event=event, args=args), self.assertRaises(tests.HomeLeakError):
                    tests.audit(event, args)
        with patch('os.getcwd', return_value=REAL_HOME + '/.local'):
            for event, args in (('shutil.rmtree', ('share', None)), ('os.rmdir', ('share', -1))):
                with self.subTest(event=event), self.assertRaises(tests.HomeLeakError):
                    tests.audit(event, args)
        with patch('os.getcwd', side_effect=FileNotFoundError):
            tests.audit('os.mkdir', ('x', 0o777, -1))

    def test_descriptor_relative_names_and_relative_opens_are_not_resolved_against_the_cwd(self):
        # The open event has no dir_fd: its name may belong to a descriptor (rmtree), so the cwd is not consulted.
        with patch('os.getcwd', return_value=REAL_HOME) as getcwd:
            for event, args in (('os.rmdir', ('.local', 5)), ('shutil.rmtree', ('.codex', 5)),
                                ('os.remove', (b'.vibe-workflow', 5)), ('os.rename', ('.local', 'x', 5, 5)),
                                ('os.mkdir', (Path('.vibe-workflow'), 0o777, 5)), ('open', ('.vibe-workflow', 'w', 0)),
                                ('open', (b'.vcw-openrouter-consent', None, 0)), ('open', (Path('.codex/x'), 'r', 0)),
                                ('os.symlink', ('../.vibe-workflow', '/tmp/link', -1))):
                with self.subTest(event=event, args=args):
                    tests.audit(event, args)
            getcwd.assert_not_called()
            with self.assertRaises(tests.HomeLeakError):  # absolute paths are still checked with a dir_fd
                tests.audit('os.rmdir', (REAL_HOME + '/.local', 5))

    def test_the_decision_follows_the_cwd_of_the_moment(self):
        # Nothing is cached: after another thread's chdir storm, only the cwd at the call decides.
        with tempfile.TemporaryDirectory() as directory:
            base, before, stop = os.path.realpath(directory), os.getcwd(), threading.Event()
            guarded, a, b = (os.path.join(base, name) for name in ('guarded', 'a', 'b'))
            for path in (guarded, a, b):
                os.mkdir(path)
            worker = threading.Thread(target=lambda: [(os.chdir(a), os.chdir(b)) for _ in iter(stop.is_set, True)])
            fake = dict(ROOTS=frozenset({guarded + '/state'}), _ROOT_PREFIXES=(guarded + '/state/',),
                        _SCAN_PREFIXES=(guarded + '/',))
            with patch.multiple(tests, **fake):
                worker.start()
                try:
                    for _ in range(200):
                        tests.audit('os.mkdir', ('state', 0o777, -1))  # the cwd is a or b: never guarded
                finally:
                    stop.set()
                    worker.join()
                try:
                    os.chdir(guarded)
                    self.assertRaises(tests.HomeLeakError, tests.audit, 'os.mkdir', ('state', 0o777, -1))
                    os.chdir(a)
                    tests.audit('os.mkdir', ('state', 0o777, -1))
                finally:
                    os.chdir(before)


def state_tree(top):
    """A temporary tree with a child named like every guarded root, each holding a file."""
    for state in (*tests.STATE, '.local/share', '.local/state', '.codex', '.claude'):
        (top / state).mkdir(parents=True, exist_ok=True)
        (top / state / 'item').write_text('x')


def home_like_cwds():
    """The real home and existing parents of guarded roots: chdir there only reads, it never writes."""
    return [path for path in (REAL_HOME, *sorted(tests.ANCESTORS))
            if path.startswith(REAL_HOME) and os.path.isdir(path)]


class StateNamedTreeTests(unittest.TestCase):
    def test_rmtree_of_a_temporary_state_tree_succeeds_from_home_like_cwds(self):
        before = os.getcwd()
        self.addCleanup(os.chdir, before)
        for cwd in home_like_cwds():
            with self.subTest(cwd=cwd), tempfile.TemporaryDirectory() as directory:
                state_tree(Path(directory) / 'home')
                os.chdir(cwd)  # reading only; the tree is removed by absolute path and descriptors
                shutil.rmtree(Path(directory) / 'home')
                os.chdir(before)
                self.assertEqual(os.listdir(directory), [])

    def test_exit_cleanup_removes_a_state_tree_from_home_like_cwds(self):
        code = ("import os, sys, tests, tests.test_isolation as t\nfrom pathlib import Path\n"
                "t.state_tree(Path(tests.created))\nprint(tests.created)\nos.chdir(sys.argv[1])")
        for cwd in home_like_cwds():
            with self.subTest(cwd=cwd):
                status, out, err = run(code, cwd)
                self.assertEqual(status, 0, err)
                self.assertFalse(os.path.exists(out.strip()), err)
                self.assertIn('path(s) left in the temporary HOME', err)


if __name__ == '__main__':
    unittest.main()
