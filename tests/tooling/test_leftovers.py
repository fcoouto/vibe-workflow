"""Exercise the leftover listing in throwaway repositories, never a real checkout."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills/vibe-pull-requests/scripts/leftovers.py"


def git(cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env |= {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
                          env=env, capture_output=True, text=True, check=True).stdout


def load():
    spec = importlib.util.spec_from_file_location("leftovers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LeftoversTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main = self.root / "main"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        (self.main / ".gitignore").write_text("node_modules/\n__pycache__/\n*.pyc\n.DS_Store\n/build/\n.env\nmynode_modules/\n")
        (self.main / "src").mkdir()
        (self.main / "src/app.py").write_text("print(1)\n")
        git(self.main, "add", ".")
        git(self.main, "commit", "-qm", "fixture")
        self.linked = self.root / "linked"
        git(self.main, "worktree", "add", "-q", "-b", "feat/x", str(self.linked))

    def write(self, name, text="x\n"):
        path = self.linked / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def run_script(self, mode, *paths, worktree=None):
        command = [sys.executable, "-I", "-B", str(SCRIPT), mode, "--worktree", str(worktree or self.linked)]
        for path in paths:
            command += ["--path", path]
        return subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)

    def rows(self, mode, *paths):
        run = self.run_script(mode, *paths)
        self.assertEqual(run.returncode, 0, run.stderr)
        return [json.loads(line) for line in run.stdout.splitlines()]

    def refused(self, run):
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"\Arefused: [^\n]+\n\Z")
        self.assertEqual(run.stdout, "")

    def digest(self, path):
        *entries, last = self.rows("manifest", path)
        self.assertEqual(set(last), {"digest", "path", "entries"})
        self.assertEqual(last["entries"], len(entries))
        return last["digest"]

    def test_a_clean_worktree_prints_no_row(self):
        run = self.run_script("sort")
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "", ""))

    def test_sort_groups_only_exact_listed_ignored_names_as_rebuildable(self):
        for name in ("node_modules/pkg/index.js", "src/__pycache__/app.cpython-311.pyc", "src/check.pyc", ".DS_Store",
                     "mynode_modules/index.js", "build/.env", "build/node_modules/x.js", ".env", "notes.md"):
            self.write(name)
        (self.linked / "src/app.py").write_text("print(2)\n")
        groups = {(row["status"], row["path"]): row["group"] for row in self.rows("sort")}
        self.assertEqual(groups, {
            ("!!", "node_modules/"): "rebuildable", ("!!", "src/__pycache__/"): "rebuildable",
            ("!!", "src/check.pyc"): "rebuildable", ("!!", ".DS_Store"): "rebuildable",
            ("!!", "mynode_modules/"): "real work", ("!!", "build/"): "real work", ("!!", ".env"): "real work",
            ("??", "notes.md"): "real work", (" M", "src/app.py"): "real work"})

    def test_an_unignored_cache_and_a_file_named_like_a_cache_folder_are_real_work(self):
        (self.linked / ".gitignore").write_text("/node_modules\n/x.pyc/\n")
        self.write(".pytest_cache/v/cache")
        self.write("node_modules")
        self.write("x.pyc/inner")
        groups = {row["path"]: (row["status"], row["group"]) for row in self.rows("sort")}
        self.assertEqual(groups[".pytest_cache/"], ("??", "real work"))
        self.assertEqual(groups["node_modules"], ("!!", "real work"), "a file, not the listed folder")
        self.assertEqual(groups["x.pyc/"], ("!!", "real work"), "a folder, not a compiled file")

    def test_sort_prints_raw_names_and_marks_the_ones_that_cannot_be_typed(self):
        names = ["a b.md", 'say "hi".md', "café.md", "back\\slash.md", "line\nbreak.md", "tab\there.md"]
        for name in names:
            self.write(name)
        rows = {row["path"]: row for row in self.rows("sort")}
        self.assertEqual(set(rows), set(names))
        self.assertEqual({name for name, row in rows.items() if row.get("unsafe")}, {"line\nbreak.md", "tab\there.md"})
        self.assertTrue(all(row["group"] == "real work" for row in rows.values()))

    def test_sort_marks_a_name_that_is_not_utf8_and_never_calls_it_rebuildable(self):
        folder = os.fsencode(self.linked) + b"/bad\xff"
        try:
            os.mkdir(folder)
            os.mkdir(folder + b"/node_modules")
            with open(folder + b"/node_modules/x.js", "wb"):
                pass
            with open(folder + b"/keep.py", "wb"):
                pass
        except OSError:
            self.skipTest("this file system refuses names that are not UTF-8")
        git(self.linked, "add", "-A")
        (row,) = [row for row in self.rows("sort") if row["status"] == "!!"]
        self.assertEqual(os.fsencode(row["path"]), b"bad\xff/node_modules/")
        self.assertEqual((row["group"], row.get("unsafe")), ("real work", True))

    def test_entries_keep_bytes_that_are_not_utf8_on_any_file_system(self):
        module = load()
        rows = module._entries(b"!! bad\xff/node_modules/\0!! ok/node_modules/\0R  new\0old\0")
        self.assertEqual([os.fsencode(row["path"]) for row in rows], [b"bad\xff/node_modules/", b"ok/node_modules/", b"new"])
        self.assertEqual([(row["group"], row.get("unsafe", False)) for row in rows],
                         [("real work", True), ("rebuildable", False), ("real work", False)])
        self.assertEqual(rows[2]["from"], "old")
        json.dumps(rows)
        for broken in (b"!! cut", b"?\0", b"R  new\0"):
            with self.subTest(broken=broken), self.assertRaises(module.Refused):
                module._entries(broken)

    def test_sort_reports_a_rename_as_one_row_with_its_old_name(self):
        git(self.linked, "mv", "src/app.py", "src/main.py")
        self.assertEqual(self.rows("sort"), [{"status": "R ", "path": "src/main.py", "from": "src/app.py", "group": "real work"}])

    def test_sort_lists_untracked_files_even_when_the_configuration_hides_them(self):
        git(self.linked, "config", "status.showUntrackedFiles", "no")
        self.write("notes.md")
        self.assertEqual(git(self.linked, "status", "--porcelain"), "", "plain status now hides it")
        self.assertEqual([row["path"] for row in self.rows("sort")], ["notes.md"])

    def test_a_failed_listing_is_refused_and_prints_no_row(self):
        self.refused(self.run_script("sort", worktree=self.root / "missing"))
        self.refused(self.run_script("sort", worktree=self.root))
        self.write("notes.md")
        self.refused(self.run_script("sort", worktree=self.linked / "src"))
        self.refused(self.run_script("list"))
        self.refused(self.run_script("sort", "notes.md"))
        self.refused(self.run_script("manifest"))

    def test_expand_lists_each_untracked_file_and_leaves_ignored_folders_alone(self):
        for name in ("docs/a/result.md", "docs/a/member.md", "docs/b/member.md", "other/x.md", "build/out.js"):
            self.write(name)
        self.assertEqual(sorted(row["path"] for row in self.rows("sort")), ["build/", "docs/", "other/"])
        everything = self.rows("expand")
        self.assertEqual(sorted(row["path"] for row in everything),
                         ["docs/a/member.md", "docs/a/result.md", "docs/b/member.md", "other/x.md"])
        self.assertTrue(all(row["status"] == "??" for row in everything))
        self.assertEqual(sorted(row["path"] for row in self.rows("expand", "docs/")),
                         ["docs/a/member.md", "docs/a/result.md", "docs/b/member.md"])
        self.assertEqual(self.rows("expand", "build/"), [])

    def test_manifest_lists_every_descendant_with_its_type_and_hash(self):
        self.write("notes/a.md", "alpha\n")
        self.write("notes/deep/b.md", "beta\n")
        (self.linked / "notes/empty").mkdir()
        (self.linked / "notes/link").symlink_to("../src/app.py")
        *entries, last = self.rows("manifest", "notes/")
        self.assertEqual([(row["path"], row["type"]) for row in entries], [
            ("notes", "folder"), ("notes/a.md", "file"), ("notes/deep", "folder"), ("notes/deep/b.md", "file"),
            ("notes/empty", "folder"), ("notes/link", "link")])
        self.assertEqual(entries[1]["sha256"], hashlib.sha256(b"alpha\n").hexdigest())
        self.assertEqual(entries[5]["target"], "../src/app.py")
        self.assertNotIn("sha256", entries[5], "a link is recorded, never followed")
        self.assertEqual((last["path"], last["entries"]), ("notes", 6))

    def test_manifest_digest_changes_with_content_names_and_types_and_only_then(self):
        note = self.write("notes.md", "first\n")
        self.write("notes/a.md", "alpha\n")
        self.write("notes/deep/b.md", "beta\n")
        file_digest, folder_digest = self.digest("notes.md"), self.digest("notes")
        self.assertEqual((self.digest("notes.md"), self.digest("notes/")), (file_digest, folder_digest))
        note.write_text("other\n")
        self.assertNotEqual(self.digest("notes.md"), file_digest, "same name and size, new content")
        (self.linked / "notes/deep/b.md").write_text("BETA\n")
        self.assertNotEqual(self.digest("notes"), folder_digest, "a rewritten descendant")
        (self.linked / "notes/deep/b.md").write_text("beta\n")
        self.assertEqual(self.digest("notes"), folder_digest)
        self.write("notes/new.md")
        self.assertNotEqual(self.digest("notes"), folder_digest, "a new descendant")
        (self.linked / "notes/new.md").unlink()
        (self.linked / "notes/a.md").unlink()
        (self.linked / "notes/a.md").symlink_to("deep/b.md")
        self.assertNotEqual(self.digest("notes"), folder_digest, "a file replaced by a link")

    def test_manifest_gives_one_digest_row_per_path(self):
        self.write("one.md", "1\n")
        self.write("two/x.md", "2\n")
        rows = self.rows("manifest", "one.md", "two/")
        self.assertEqual([row["path"] for row in rows if "digest" in row], ["one.md", "two"])

    def test_manifest_refuses_a_failed_read_and_prints_no_row(self):
        if os.geteuid() == 0:
            self.skipTest("root reads every file")
        self.write("notes/a.md")
        locked = self.write("notes/b.md")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o600)
        self.refused(self.run_script("manifest", "notes"))
        self.refused(self.run_script("manifest", "missing.md"))

    def test_manifest_refuses_paths_that_leave_the_worktree(self):
        self.write("notes.md")
        for path in ("../main/src/app.py", str(self.linked / "notes.md"), "", ".", "notes.md/..", ".git", "src//app.py"):
            with self.subTest(path=path):
                self.refused(self.run_script("manifest", path))

    def test_the_script_changes_nothing(self):
        self.write("notes/a.md")
        self.write("node_modules/x.js")
        before = sorted(str(path) for path in self.linked.rglob("*"))
        status = git(self.linked, "status", "--porcelain", "--ignored")
        for mode, paths in (("sort", ()), ("expand", ()), ("manifest", ("notes",))):
            self.rows(mode, *paths)
        self.assertEqual(sorted(str(path) for path in self.linked.rglob("*")), before)
        self.assertEqual(git(self.linked, "status", "--porcelain", "--ignored"), status)


if __name__ == "__main__":
    unittest.main()
