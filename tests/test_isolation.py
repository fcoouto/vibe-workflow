import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePath

import tests
from runtime import changes, openrouter_settings

ROOT = Path(__file__).resolve().parents[1]
REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def run(code, **environment):
    """Run ``code`` in a fresh interpreter from the repository root; return (exit code, stdout, stderr)."""
    env = {key: value for key, value in os.environ.items() if key not in ('VCW_TEST_HOME', 'PYTHONPATH')}
    env.update(environment)
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, env=env, capture_output=True,
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
    def roots(self):
        return sorted(root for root in tests.ROOTS if root.startswith(REAL_HOME + os.sep))

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
        for root in self.roots():
            events.append(('open', (root + '/probe', 'w', 0)))
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
    def test_preset_test_home_is_kept(self):
        with tempfile.TemporaryDirectory() as home:
            home = os.path.realpath(home)
            os.makedirs(os.path.join(home, '.local', 'share'))
            code = "import os, tests\nprint(os.environ['HOME']); print(tests.created); print(tests.HOME)"
            status, out, err = run(code, HOME=home, VCW_TEST_HOME=home)
            self.assertEqual(status, 0, err)
            self.assertEqual(out.split(), [home, 'None', home])
            self.assertTrue(os.path.isdir(home))

    def test_qualification_operator_tools_keep_the_real_home(self):
        code = ("import os, sys\nsys.modules['__main__'].__file__ = 'tests/qualification/opencode_live.py'\n"
                "import tests\nprint(os.environ['HOME']); print(getattr(sys, '_vcw_home_guard', False))")
        with tempfile.TemporaryDirectory() as home:
            status, out, err = run(code, HOME=home)
        self.assertEqual(status, 0, err)
        self.assertEqual(out.split(), [home, 'False'])

    def test_created_home_is_removed_at_exit(self):
        code = "import tests\nprint(tests.created)"
        status, out, err = run(code)
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

    def test_guard_error_escapes_oserror_handlers(self):
        self.assertFalse(issubclass(tests.HomeLeakError, OSError))


if __name__ == '__main__':
    unittest.main()
