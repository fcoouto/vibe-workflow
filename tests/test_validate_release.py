"""`validate.py --release`: a release tag's version must not be reused for changed packaged content.

Each case builds a small Git repository in a temporary directory; nothing touches this checkout."""

import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from scripts import validate


PACKAGE = Path(__file__).resolve().parents[1]


class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe release ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.write(".codex-plugin/plugin.json", json.dumps(dict(name="vibe-workflow", version="1.2.3")))
        self.write("runtime/code.py", "print('one')\n")
        self.write("tests/test_code.py", "one\n")
        self.write("CLAUDE.md", "one\n")
        self.write("scripts/test_sharded.py", "one\n")
        self.commit("release")

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                               "-C", str(self.root), *args], check=True, capture_output=True, text=True, env=env, timeout=20).stdout

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def tag(self, name="1.2.3"):
        self.git("tag", name)

    def check(self):
        return validate.release_check(self.root)

    def test_a_version_without_a_tag_passes(self):
        self.assertEqual(self.check()["release_tag"], "absent")

    def test_the_same_version_with_the_same_content_passes(self):
        self.tag()
        self.assertEqual(self.check()["release_tag"], "unchanged")
        self.write("tests/test_code.py", "two\n")
        self.write("CLAUDE.md", "two\n")
        self.write("scripts/test_sharded.py", "two\n")
        self.commit("development only")
        self.assertEqual(self.check()["release_tag"], "unchanged")

    def test_the_same_version_with_changed_packaged_content_fails_and_names_the_fix(self):
        self.tag()
        self.write("runtime/code.py", "print('two')\n")
        self.commit("change")
        with self.assertRaises(ValueError) as caught:
            self.check()
        message = str(caught.exception)
        for text in ("1.2.3", "runtime/code.py", "version"):
            self.assertIn(text, message)

    def test_uncommitted_and_added_packaged_changes_fail_too(self):
        self.tag()
        self.write("runtime/code.py", "print('two')\n")
        with self.assertRaises(ValueError):
            self.check()
        self.git("checkout", "--", "runtime/code.py")
        self.write("runtime/new.py", "x\n")
        self.git("add", "runtime/new.py")
        with self.assertRaises(ValueError):
            self.check()

    def test_a_new_version_passes_even_when_the_old_tag_differs(self):
        self.tag()
        self.write("runtime/code.py", "print('two')\n")
        self.write(".codex-plugin/plugin.json", json.dumps(dict(name="vibe-workflow", version="1.2.4")))
        self.commit("bump")
        self.assertEqual(self.check()["release_tag"], "absent")

    def test_other_tags_and_branches_do_not_count(self):
        self.tag("1.2.2")
        self.tag("v1.2.3")
        self.write("runtime/code.py", "print('two')\n")
        self.commit("change")
        self.assertEqual(self.check()["release_tag"], "absent")

    def test_a_directory_that_is_not_a_repository_cannot_be_checked(self):
        with tempfile.TemporaryDirectory() as other:
            os.makedirs(Path(other) / ".codex-plugin")
            (Path(other) / ".codex-plugin/plugin.json").write_text(json.dumps(dict(version="1.2.3")))
            with self.assertRaises(ValueError) as caught:
                validate.release_check(Path(other))
            self.assertIn("cannot", str(caught.exception))

    def test_packaged_paths_follow_the_package_export_exclusions(self):
        from runtime.package_manifest import EXCLUDED
        self.assertEqual(validate.release_pathspecs(), [".", *[f":(exclude){item.rstrip('/')}" for item in EXCLUDED]])


class ReleaseCommandTests(unittest.TestCase):
    def run_validate(self, *args):
        return subprocess.run([sys.executable, "-B", str(PACKAGE / "scripts/validate.py"), *args], capture_output=True, text=True,
                              timeout=120, cwd=PACKAGE)

    def test_unknown_arguments_are_rejected(self):
        result = self.run_validate("--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--release", result.stderr)


if __name__ == "__main__":
    unittest.main()
