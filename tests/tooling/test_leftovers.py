"""Exercise the leftover listing in throwaway repositories, never a real checkout."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

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

    def run_script(self, mode, *paths, worktree=None, extra=(), stdout=subprocess.PIPE):
        command = [sys.executable, "-I", "-B", str(SCRIPT), mode, "--worktree", str(worktree or self.linked)]
        for path in paths:
            command += ["--path", path]
        return subprocess.run([*command, *extra], cwd=self.root, stdout=stdout, stderr=subprocess.PIPE, text=True, timeout=60)

    def pin(self, branch="feat/x", head=None):
        """The branch, or detached head, and the commit a removing mode is told the merge was confirmed for."""
        head = head or git(self.linked, "rev-parse", "HEAD").strip()
        return [*(("--branch", branch) if branch else ("--detached",)), "--head", head]

    def discard(self, *pairs, worktree=None, primary=None, pin=None, stdout=subprocess.PIPE):
        extra = [item for path, digest in pairs for item in ("--path", path, "--digest", digest)]
        extra += ["--primary", str(primary or self.main), *(pin or self.pin())]
        return self.run_script("discard", worktree=worktree, extra=extra, stdout=stdout)

    def caches(self, worktree=None, primary=None, pin=None, stdout=subprocess.PIPE):
        extra = ["--primary", str(primary or self.main), *(pin or self.pin())]
        return self.run_script("caches", worktree=worktree, extra=extra, stdout=stdout)

    def inside(self, *argv, **patches):
        """Run the script in this process, so one of its functions can be replaced; the current folder is restored."""
        module = load()
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for name, make in patches.items():
                stack.enter_context(mock.patch.object(module, name, make(getattr(module, name))))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = module.main(list(argv))
        os.chdir(here)
        return code, out.getvalue(), err.getvalue()

    def identity(self, worktree, branch=None, primary=None):
        extra = ["--primary", str(primary or self.main), *(("--branch", branch) if branch else ("--detached",))]
        return self.run_script("identity", worktree=worktree, extra=extra)

    def stopped(self, run, discarded=()):
        """A removing mode that refused: what it already removed is listed, then one reason."""
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"\Arefused: [^\n]+\n\Z")
        self.assertEqual(run.stdout.splitlines(), [f"removed: {path}" for path in discarded])

    def outside(self):
        """A folder next to the worktree that no mode may read through a link or change."""
        folder = self.root / "outside"
        folder.mkdir()
        (folder / "secret.md").write_text("secret\n")
        return folder

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
        self.assertLessEqual({"digest", "path", "entries", "statuses", "tracked", "staged"}, set(last))
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

    def test_read_modes_leave_the_index_file_byte_identical(self):
        self.write("notes/a.md")
        os.utime(self.linked / "src/app.py", ns=(1, 1))
        index = Path(git(self.linked, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
        before = index.read_bytes()
        for mode, paths in (("sort", ()), ("expand", ()), ("manifest", ("notes",))):
            self.rows(mode, *paths)
            self.assertEqual(index.read_bytes(), before, mode)
        self.identity(self.linked, "feat/x")
        self.assertEqual(index.read_bytes(), before, "identity")
        git(self.linked, "status")
        self.assertNotEqual(index.read_bytes(), before, "the fixture is one where a plain status rewrites the index")

    def test_an_unreadable_folder_refuses_every_listing(self):
        if os.geteuid() == 0:
            self.skipTest("root reads every folder")
        self.write("local/deep/notes.md")
        closed = self.linked / "local/deep"
        closed.chmod(0)
        self.addCleanup(closed.chmod, 0o700)
        self.refused(self.run_script("sort"))
        self.refused(self.run_script("expand"))
        self.refused(self.run_script("expand", "local"))
        self.refused(self.run_script("manifest", "local"))

    def test_no_mode_reads_through_a_linked_folder(self):
        outside = self.outside()
        (self.linked / "link").symlink_to(outside)
        self.refused(self.run_script("manifest", "link/secret.md"))
        self.stopped(self.discard(("link/secret.md", "0" * 64)))
        entry, last = self.rows("manifest", "link")
        self.assertEqual((entry["type"], entry["target"], last["entries"]), ("link", str(outside), 1))
        alias = self.root / "alias"
        alias.symlink_to(self.linked)
        for mode in ("sort", "expand"):
            self.refused(self.run_script(mode, worktree=alias))
        self.refused(self.caches(worktree=alias))
        self.refused(self.run_script("manifest", "link", worktree=alias))
        self.assertEqual((outside / "secret.md").read_text(), "secret\n")

    def test_manifest_records_git_status_and_staging_changes_the_digest(self):
        self.write("notes/a.md")
        self.write("notes/.env")
        (self.linked / "notes/empty").mkdir()
        rows = self.rows("manifest", "notes")
        self.assertEqual({row["path"]: row.get("status") for row in rows[:-1]},
                         {"notes": None, "notes/.env": ["!!"], "notes/a.md": ["??"], "notes/empty": None})
        self.assertEqual((rows[-1]["statuses"], rows[-1]["tracked"], rows[-1]["staged"]), (["!!", "??"], 0, 0))
        git(self.linked, "add", "notes/a.md")
        staged = self.rows("manifest", "notes")
        self.assertEqual([row["status"] for row in staged if row.get("path") == "notes/a.md" and "type" in row], [["A "]])
        self.assertEqual((staged[-1]["statuses"], staged[-1]["tracked"], staged[-1]["staged"]), (["!!", "A "], 1, 1))
        self.assertNotEqual(staged[-1]["digest"], rows[-1]["digest"], "same bytes, another status")

    def test_manifest_marks_a_folder_that_holds_a_repository_or_a_name_that_cannot_be_typed(self):
        self.write("vendor/lib/.git/config")
        self.write("upper/.GIT/config")
        self.write("odd/line\nbreak.md")
        self.write("fine/a.md")
        marked = {}
        for path in ("vendor", "upper", "odd", "fine"):
            *entries, last = self.rows("manifest", path)
            marked[path] = (sorted(row["path"] for row in entries if row.get("unsafe")), last.get("unsafe", False))
        self.assertEqual(marked, {"vendor": (["vendor/lib/.git"], True), "upper": (["upper/.GIT"], True),
                                  "odd": (["odd/line\nbreak.md"], True), "fine": ([], False)})

    def test_discard_removes_exactly_the_approved_paths_when_their_digests_match(self):
        outside = self.outside()
        self.write("notes.md")
        self.write("out/deep/a.js")
        (self.linked / "out/empty").mkdir()
        (self.linked / "out/file-link").symlink_to("../src/app.py")
        (self.linked / "out/folder-link").symlink_to(outside)
        os.mkfifo(self.linked / "out/pipe")
        self.write("keep.md")
        run = self.discard(("notes.md", self.digest("notes.md")), ("out/", self.digest("out")))
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        self.assertEqual(run.stdout.splitlines(), ["removed: notes.md", "removed: out"])
        self.assertEqual([row["path"] for row in self.rows("sort")], ["keep.md"])
        self.assertFalse((self.linked / "out").exists())
        self.assertEqual((self.linked / "src/app.py").read_text(), "print(1)\n")
        self.assertEqual((outside / "secret.md").read_text(), "secret\n")

    def test_discard_rechecks_each_path_just_before_it_removes_that_path(self):
        first, second = self.write("a.md", "one\n"), self.write("b.md", "two\n")
        digests = self.digest("a.md"), self.digest("b.md")
        second.write_text("TWO\n")
        self.stopped(self.discard(("b.md", digests[1]), ("a.md", digests[0])))
        self.assertTrue(first.exists() and second.exists(), "a refusal on the first path removes nothing")
        self.stopped(self.discard(("a.md", digests[0]), ("b.md", digests[1])), discarded=["a.md"])
        self.assertFalse(first.exists())
        self.assertEqual(second.read_text(), "TWO\n")

    def test_discard_refuses_a_folder_that_changed_in_any_way(self):
        self.write("out/a.js", "one\n")
        digest = self.digest("out")
        for change in (lambda: self.write("out/new.js"), lambda: (self.linked / "out/a.js").write_text("ONE\n"),
                       lambda: (self.linked / "out/a.js").unlink()):
            change()
            self.stopped(self.discard(("out", digest)))
            self.assertTrue((self.linked / "out").is_dir())
        self.stopped(self.discard(("missing.md", digest)))

    def test_discard_refuses_tracked_staged_and_unsafe_paths_even_with_their_own_digest(self):
        self.write("staged/a.md")
        git(self.linked, "add", "staged/a.md")
        self.write("vendor/lib/.git/config")
        self.write("odd/tab\there.md")
        for path in ("staged", "src/app.py", "src", "vendor", "odd"):
            with self.subTest(path=path):
                self.stopped(self.discard((path, self.digest(path))))
        for name in ("staged/a.md", "src/app.py", "vendor/lib/.git/config", "odd/tab\there.md"):
            self.assertTrue((self.linked / name).exists(), name)

    def test_discard_and_caches_refuse_bad_arguments_and_the_main_checkout(self):
        self.write("notes.md")
        (self.main / "notes.md").write_text("x\n")
        (self.main / "node_modules").mkdir()
        digest = self.digest("notes.md")
        pin = self.pin()
        primary = ["--primary", str(self.main), *pin]
        self.refused(self.run_script("discard", extra=primary))
        self.refused(self.run_script("discard", "notes.md", extra=primary))
        self.refused(self.run_script("discard", extra=["--path", "notes.md", "--digest", digest, "--digest", digest, *primary]))
        self.refused(self.run_script("discard", extra=["--path", "notes.md", "--digest", digest, *pin]))
        self.refused(self.run_script("caches", extra=pin))
        self.refused(self.run_script("caches", "node_modules", extra=primary))
        self.stopped(self.discard(("notes.md", digest), worktree=self.main))
        self.refused(self.caches(worktree=self.main))
        self.assertTrue((self.linked / "notes.md").exists() and (self.main / "notes.md").exists())
        self.assertTrue((self.main / "node_modules").is_dir())

    def test_caches_removes_every_rebuildable_row_and_nothing_else(self):
        outside = self.outside()
        for name in ("node_modules/pkg/index.js", "src/__pycache__/app.cpython-311.pyc", "src/check.pyc", ".DS_Store",
                     "mynode_modules/index.js", "build/node_modules/x.js", ".env", "notes.md"):
            self.write(name)
        (self.linked / "node_modules/linked-out").symlink_to(outside)
        run = self.caches()
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        self.assertEqual(sorted(run.stdout.splitlines()),
                         ["removed: .DS_Store", "removed: node_modules/", "removed: src/__pycache__/", "removed: src/check.pyc"])
        self.assertEqual(sorted(row["path"] for row in self.rows("sort")), [".env", "build/", "mynode_modules/", "notes.md"])
        self.assertTrue((self.linked / "build/node_modules/x.js").exists())
        self.assertEqual((outside / "secret.md").read_text(), "secret\n")
        again = self.caches()
        self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "", ""))

    def test_caches_leaves_an_ignored_folder_that_holds_a_tracked_file(self):
        self.write("node_modules/pkg/kept.js")
        self.write("node_modules/pkg/other.js")
        git(self.linked, "add", "-f", "node_modules/pkg/kept.js")
        run = self.caches()
        self.assertEqual((run.returncode, run.stdout), (0, ""), run.stderr)
        self.assertTrue((self.linked / "node_modules/pkg/other.js").exists())

    def test_caches_checks_git_again_after_it_read_the_folder(self):
        self.write("node_modules/pkg/index.js")

        def stage_after(scan):
            def scanned(*args, **kwargs):
                node = scan(*args, **kwargs)
                if node.row["path"] == "node_modules":
                    git(self.linked, "add", "-f", "node_modules/pkg/index.js")
                return node
            return scanned

        code, out, err = self.inside("caches", "--worktree", str(self.linked), "--primary", str(self.main), *self.pin(),
                                     _scan=stage_after)
        self.assertEqual((code, out), (2, ""), err)
        self.assertRegex(err, r"\Arefused: node_modules/: [^\n]+; nothing in it was removed\n\Z")
        self.assertTrue((self.linked / "node_modules/pkg/index.js").exists())

    def test_a_staged_deletion_next_to_the_same_untracked_file_stays_in_the_manifest_and_blocks_removal(self):
        self.write("note.md", "kept\n")
        self.write("src/old.pyc", "compiled\n")
        git(self.linked, "add", "-f", "note.md", "src/old.pyc")
        git(self.linked, "commit", "-qm", "tracked")
        git(self.linked, "rm", "-q", "--cached", "note.md", "src/old.pyc")
        self.assertEqual(sorted((row["status"], row["path"]) for row in self.rows("sort")),
                         [("!!", "src/old.pyc"), ("??", "note.md"), ("D ", "note.md"), ("D ", "src/old.pyc")])
        entry, last = self.rows("manifest", "note.md")
        self.assertEqual((entry["status"], last["statuses"], last["tracked"], last["staged"]), (["??", "D "], ["??", "D "], 0, 1))
        self.stopped(self.discard(("note.md", last["digest"])))
        self.stopped(self.caches())
        self.assertEqual(((self.linked / "note.md").read_text(), (self.linked / "src/old.pyc").read_text()), ("kept\n", "compiled\n"))

    def test_a_tracked_file_deleted_from_the_folder_still_counts_in_its_manifest(self):
        self.write("docs/gone.md")
        self.write("docs/kept.md")
        git(self.linked, "add", "docs/gone.md")
        git(self.linked, "commit", "-qm", "tracked")
        (self.linked / "docs/gone.md").unlink()
        rows = self.rows("manifest", "docs")
        self.assertEqual([(row["path"], row["type"], row["status"]) for row in rows[:-1] if row["type"] in ("missing", "file")],
                         [("docs/kept.md", "file", ["??"]), ("docs/gone.md", "missing", [" D"])])
        self.assertEqual((rows[-1]["tracked"], rows[-1]["staged"]), (1, 1))
        self.stopped(self.discard(("docs", rows[-1]["digest"])))
        self.assertTrue((self.linked / "docs/kept.md").exists())

    def test_caches_refuses_a_cache_that_holds_a_repository_or_a_name_that_cannot_be_typed(self):
        self.write("node_modules/pkg/.git/config")
        self.write("node_modules/pkg/index.js")
        self.write("src/__pycache__/line\nbreak.pyc")
        run = self.caches()
        self.stopped(run)
        self.assertRegex(run.stderr, r"\Arefused: node_modules/: [^\n]+; nothing in it was removed\n\Z")
        self.assertTrue((self.linked / "node_modules/pkg/.git/config").exists())
        shutil.rmtree(self.linked / "node_modules")
        self.stopped(self.caches())
        self.assertTrue((self.linked / "src/__pycache__/line\nbreak.pyc").exists())

    def test_every_mode_refuses_a_worktree_named_through_a_linked_folder_or_a_relative_path(self):
        self.write("notes.md")
        self.write("node_modules/x.js")
        digest = self.digest("notes.md")
        through = self.root / "through"
        through.symlink_to(self.root)
        for worktree in (f"{through}/linked", "linked", f"{self.root}/./linked", f"{self.root}/main/../linked"):
            with self.subTest(worktree=worktree):
                self.refused(self.run_script("sort", worktree=worktree))
                self.refused(self.run_script("expand", worktree=worktree))
                self.refused(self.run_script("manifest", "notes.md", worktree=worktree))
                self.stopped(self.discard(("notes.md", digest), worktree=worktree))
                self.refused(self.caches(worktree=worktree))
        self.assertTrue((self.linked / "notes.md").exists() and (self.linked / "node_modules/x.js").exists())

    def test_discard_and_caches_remove_only_in_a_worktree_the_repository_registers_at_that_path(self):
        self.write("notes.md")
        self.write("node_modules/x.js")
        digest = self.digest("notes.md")
        other = self.root / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "commit", "-q", "--allow-empty", "-m", "base")
        self.stopped(self.discard(("notes.md", digest), primary=other))
        self.refused(self.caches(primary=other))
        backlink = self.main / ".git/worktrees/linked/gitdir"
        backlink.write_text(f"{self.second()}/.git\n")
        self.stopped(self.discard(("notes.md", digest)))
        self.refused(self.caches())
        self.assertTrue((self.linked / "notes.md").exists() and (self.linked / "node_modules/x.js").exists())

    def test_a_folder_on_another_device_is_refused_before_anything_is_read_or_removed(self):
        self.write("out/a.js")
        self.write("out/mounted/b.js")
        self.write("node_modules/mounted/c.js")
        digest = self.digest("out")
        mounted = {os.stat(self.linked / "out/mounted").st_ino, os.stat(self.linked / "node_modules/mounted").st_ino}

        def another(device):
            return lambda info: -1 if info.st_ino in mounted else device(info)

        worktree, primary = ["--worktree", str(self.linked)], ["--primary", str(self.main), *self.pin()]
        for argv in (["manifest", *worktree, "--path", "out"], ["discard", *worktree, "--path", "out", "--digest", digest, *primary],
                     ["caches", *worktree, *primary]):
            with self.subTest(mode=argv[0]):
                code, out, err = self.inside(*argv, _device=another)
                self.assertEqual((code, out), (2, ""), err)
                self.assertIn("another device", err)
        self.assertTrue((self.linked / "out/a.js").exists() and (self.linked / "node_modules/mounted/c.js").exists())

    def test_an_error_during_removal_names_the_path_that_may_be_partly_removed_and_stops(self):
        if os.geteuid() == 0:
            self.skipTest("root removes from every folder")
        for name in ("first.md", "out/a.js", "out/locked/b.js", "out/z.js", "last.md"):
            self.write(name)
        locked = self.linked / "out/locked"
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        run = self.discard(*((path, self.digest(path)) for path in ("first.md", "out", "last.md")))
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout.splitlines(), ["removed: first.md"])
        self.assertRegex(run.stderr, r"\Arefused: out: [^\n]+; it may be partly removed\n\Z")
        self.assertFalse((self.linked / "out/a.js").exists(), "removed before the error")
        self.assertTrue((self.linked / "out/locked/b.js").exists() and (self.linked / "out/z.js").exists())
        self.assertTrue((self.linked / "last.md").exists(), "nothing after the failed path is touched")

    def test_a_refusal_before_removal_says_nothing_in_the_path_was_removed(self):
        self.write("a.md")
        run = self.discard(("a.md", "0" * 64))
        self.stopped(run)
        self.assertRegex(run.stderr, r"\Arefused: a.md: [^\n]+; nothing in it was removed\n\Z")

    def test_a_folder_that_holds_only_a_repository_makes_sort_refuse_instead_of_printing_no_row(self):
        self.write("saved/.git/config", "[core]\n")
        self.assertEqual(git(self.linked, "status", "--porcelain", "--ignored"), "", "Git lists nothing for it")
        run = self.run_script("sort")
        self.refused(run)
        self.assertIn("'saved/.git'", run.stderr)
        shutil.rmtree(self.linked / "saved")
        self.assertEqual(self.rows("sort"), [])

    def test_a_repository_below_an_ignored_folder_makes_sort_refuse(self):
        self.write("build/saved/.git/config")
        self.assertEqual(git(self.linked, "status", "--porcelain", "--ignored"), "", "Git lists nothing for it")
        run = self.run_script("sort")
        self.refused(run)
        self.assertIn("'build/saved/.git'", run.stderr)
        shutil.rmtree(self.linked / "build")
        (self.linked / "src/.git").write_text("gitdir: /nowhere\n")
        self.refused(self.run_script("sort"))

    def test_a_repository_named_in_another_letter_case_makes_sort_refuse_whatever_git_lists(self):
        self.write("src/deep/er/.GIT/HEAD")
        for folding in ("true", "false"):
            with self.subTest(ignoreCase=folding):
                git(self.linked, "config", "core.ignoreCase", folding)
                run = self.run_script("sort")
                self.refused(run)
                self.assertIn("'src/deep/er/.GIT'", run.stderr)

    def test_a_hidden_repository_makes_sort_and_expand_refuse_whatever_rows_git_lists(self):
        self.write("saved/.git/config", "[core]\n")
        self.write(".DS_Store")
        self.write("notes/a.md")
        self.assertEqual(git(self.linked, "status", "--porcelain", "--ignored"), "?? notes/\n!! .DS_Store\n",
                         "Git lists the cache and the notes, never the repository")
        for mode, paths in (("sort", ()), ("expand", ()), ("expand", ("notes",))):
            with self.subTest(mode=mode, paths=paths):
                run = self.run_script(mode, *paths)
                self.refused(run)
                self.assertIn("'saved/.git'", run.stderr)
        self.assertTrue((self.linked / ".DS_Store").exists() and (self.linked / "saved/.git/config").exists())
        shutil.rmtree(self.linked / "saved")
        self.assertEqual([row["path"] for row in self.rows("sort")], ["notes/", ".DS_Store"])
        self.assertEqual([row["path"] for row in self.rows("expand")], ["notes/a.md"])

    def test_a_tracked_file_git_was_told_to_overlook_makes_sorts_and_removals_refuse(self):
        self.write("notes.md")
        self.write("node_modules/x.js")
        digest = self.digest("notes.md")
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                git(self.linked, "update-index", f"--{flag}", "src/app.py")
                (self.linked / "src/app.py").write_text("print('an edit nothing lists')\n")
                self.assertEqual(git(self.linked, "status", "--porcelain"), "?? notes.md\n", "Git lists no row for the edit")
                for run in (self.run_script("sort"), self.run_script("expand"), self.run_script("expand", "notes.md")):
                    self.refused(run)
                    self.assertIn("'src/app.py'", run.stderr)
                    self.assertIn("may hide a change", run.stderr)
                self.stopped(self.discard(("notes.md", digest)))
                self.refused(self.caches())
                self.assertTrue((self.linked / "notes.md").exists() and (self.linked / "node_modules/x.js").exists())
                git(self.linked, "update-index", f"--no-{flag}", "src/app.py")
                self.assertEqual(sorted(row["path"] for row in self.rows("sort")), ["node_modules/", "notes.md", "src/app.py"])
                git(self.linked, "checkout", "-q", "--", "src/app.py")
        run = self.caches()
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "removed: node_modules/\n", ""))

    def test_a_flag_on_an_unchanged_tracked_file_refuses_too(self):
        git(self.linked, "update-index", "--assume-unchanged", "src/app.py")
        run = self.run_script("sort")
        self.refused(run)
        self.assertEqual(run.stderr, "refused: 'src/app.py' is marked assume-unchanged or skip-worktree, so Git may hide a "
                                     "change to it\n")

    def test_a_paused_git_operation_makes_identity_and_the_removing_modes_refuse(self):
        self.write("notes.md")
        self.write("node_modules/x.js")
        digest = self.digest("notes.md")
        own = Path(git(self.linked, "rev-parse", "--path-format=absolute", "--git-dir").strip())
        for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
                     "AUTO_MERGE", "MERGE_AUTOSTASH"):
            with self.subTest(state=name):
                state = own / name
                state.mkdir() if name.startswith("rebase") else state.write_text("0" * 40 + "\n")
                for run in (self.identity(self.linked, "feat/x"), self.discard(("notes.md", digest)), self.caches()):
                    self.refused(run)
                    self.assertEqual(run.stderr, f"refused: a Git operation is paused in the worktree ({name})\n")
                state.rmdir() if name.startswith("rebase") else state.unlink()
        self.assertTrue((self.linked / "notes.md").exists() and (self.linked / "node_modules/x.js").exists())
        (self.main / ".git/MERGE_HEAD").write_text("0" * 40 + "\n")
        self.assertEqual(self.identity(self.linked, "feat/x").returncode, 0, "another worktree's state is not this one's")

    def test_a_real_bisect_in_a_clean_worktree_is_a_paused_operation(self):
        git(self.linked, "bisect", "start")
        self.assertEqual(self.rows("sort"), [], "nothing is listed, yet the worktree is mid-operation")
        run = self.identity(self.linked, "feat/x")
        self.refused(run)
        self.assertIn("a Git operation is paused in the worktree", run.stderr)
        git(self.linked, "bisect", "reset")
        self.assertEqual(self.identity(self.linked, "feat/x").returncode, 0)

    def test_the_walk_for_repositories_follows_no_link_and_crosses_no_device(self):
        outside = self.outside()
        (outside / ".git").mkdir()
        (self.linked / "src/link").symlink_to(outside)
        self.write("src/deep/kept.md")
        git(self.linked, "add", "-A")
        git(self.linked, "commit", "-qm", "a tracked link and a deep folder")
        self.assertEqual(self.rows("sort"), [], "a link is an entry, never a folder to read")
        mounted = os.stat(self.linked / "src/deep").st_ino
        code, out, err = self.inside("sort", "--worktree", str(self.linked),
                                     _device=lambda device: lambda info: -1 if info.st_ino == mounted else device(info))
        self.assertEqual((code, out), (2, ""), err)
        self.assertIn("another device", err)
        self.write("notes.md")
        code, out, err = self.inside("sort", "--worktree", str(self.linked),
                                     _device=lambda device: lambda info: -1 if info.st_ino == mounted else device(info))
        self.assertEqual((code, out), (2, ""), err)
        self.assertIn("another device", err)
        self.assertEqual([row["path"] for row in self.rows("sort")], ["notes.md"])

    def test_removing_modes_refuse_a_worktree_that_left_the_confirmed_branch(self):
        self.write("notes.md")
        self.write("node_modules/x.js")
        digest, confirmed = self.digest("notes.md"), self.pin()
        git(self.linked, "switch", "-q", "-c", "feat/unmerged")
        git(self.linked, "switch", "-q", "--detach")
        for state, pins in (("feat/unmerged", (confirmed, self.pin(branch=None))), (None, (confirmed, self.pin("feat/unmerged")))):
            git(self.linked, "switch", "-q", *((state,) if state else ("--detach",)))
            for pin in pins:
                for run in (self.discard(("notes.md", digest), pin=pin), self.caches(pin=pin)):
                    with self.subTest(state=state, pin=pin[:2]):
                        self.refused(run)
                        self.assertIn("on that branch", run.stderr)
        self.assertTrue((self.linked / "notes.md").exists() and (self.linked / "node_modules/x.js").exists())
        run = self.caches(pin=self.pin(branch=None))
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "removed: node_modules/\n", ""))

    def test_removing_modes_refuse_a_worktree_whose_head_moved_after_the_merge_was_confirmed(self):
        self.write("notes.md")
        self.write("node_modules/x.js")
        digest, confirmed = self.digest("notes.md"), self.pin()
        git(self.linked, "commit", "-q", "--allow-empty", "-m", "after the merge")
        for run in (self.discard(("notes.md", digest), pin=confirmed), self.caches(pin=confirmed)):
            self.refused(run)
            self.assertEqual(run.stderr, "refused: the worktree's HEAD is not the commit that was confirmed\n")
        self.assertTrue((self.linked / "notes.md").exists() and (self.linked / "node_modules/x.js").exists())
        run = self.discard(("notes.md", digest))
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, "removed: notes.md\n", ""))

    def test_removing_modes_need_the_confirmed_branch_and_a_full_commit_hash(self):
        self.write("notes.md")
        self.write("node_modules/x.js")
        digest, (_, branch, _, head) = self.digest("notes.md"), self.pin()
        primary = ["--primary", str(self.main)]
        for pin in ([], ["--branch", branch], ["--head", head], ["--detached", "--branch", branch, "--head", head],
                    ["--branch", branch, "--head", "HEAD"], ["--branch", branch, "--head", head[:12]],
                    ["--branch", branch, "--head", head.upper()], ["--branch", branch, "--head", head + "0"]):
            with self.subTest(pin=pin):
                for run in (self.run_script("discard", extra=["--path", "notes.md", "--digest", digest, *primary, *pin]),
                            self.run_script("caches", extra=[*primary, *pin])):
                    self.refused(run)
                    self.assertIn("--head <commit>", run.stderr)
        self.refused(self.run_script("identity", extra=[*primary, "--branch", branch, "--head", head]))
        self.assertTrue((self.linked / "notes.md").exists() and (self.linked / "node_modules/x.js").exists())

    def test_a_removal_that_could_not_be_reported_is_named_as_removed(self):
        for name in ("a.md", "b.md", "node_modules/x.js", "src/__pycache__/x.pyc"):
            self.write(name)
        pairs = [(path, self.digest(path)) for path in ("a.md", "b.md")]
        report = r"\Arefused: ([^\n:]+): it was removed, but reporting that failed \(\w+\); nothing after it was touched\n\Z"
        steps = ((lambda out: self.discard(*pairs, stdout=out), {"a.md"}, {"b.md", "node_modules/", "src/__pycache__/"}),
                 (lambda out: self.caches(stdout=out), {"node_modules/", "src/__pycache__/"}, {"b.md"}))
        for remove, candidates, untouched in steps:
            reader, writer = os.pipe()
            os.close(reader)
            try:
                run = remove(writer)
            finally:
                os.close(writer)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertRegex(run.stderr, report)
            gone = run.stderr.split(": ")[1]
            self.assertIn(gone, candidates)
            self.assertFalse((self.linked / gone).exists(), "the path it names is gone")
            self.assertEqual({row["path"] for row in self.rows("sort")}, untouched | candidates - {gone},
                             "it stopped at the first path it could not report")

    def test_identity_calls_a_pointer_broken_only_when_its_git_folder_is_missing(self):
        head = self.main / ".git/worktrees/linked/HEAD"
        head.write_text("garbage\n")
        run = self.identity(self.linked, "feat/x")
        self.refused(run)
        self.assertNotIn("pointer is broken", run.stderr)

        def timing_out(run_git):
            def run(command, **kwargs):
                if "--show-toplevel" in command:
                    raise subprocess.TimeoutExpired(command, 120)
                return run_git(command, **kwargs)
            return lambda _: mock.Mock(run=run, TimeoutExpired=subprocess.TimeoutExpired)

        head.write_text("ref: refs/heads/feat/x\n")
        argv = ["identity", "--worktree", str(self.linked), "--primary", str(self.main), "--branch", "feat/x"]
        self.assertEqual(self.inside(*argv)[0], 0)
        code, out, err = self.inside(*argv, subprocess=timing_out(subprocess.run))
        self.assertEqual((code, out, err), (2, "", "refused: Git could not run\n"))

    def test_identity_reads_the_pointer_file_as_git_does(self):
        pointer = self.linked / ".git"
        named = pointer.read_bytes().rstrip(b"\n")
        for ending in (b"\r\n", b"\n\n", b"\r\n\r\n", b""):
            with self.subTest(ending=ending):
                pointer.write_bytes(named + ending)
                self.assertEqual(git(self.linked, "rev-parse", "--abbrev-ref", "HEAD").strip(), "feat/x", "Git accepts it")
                run = self.identity(self.linked, "feat/x")
                self.assertEqual((run.returncode, run.stderr), (0, ""))
        pointer.write_bytes(b"gitdir: \r\n")
        run = self.identity(self.linked, "feat/x")
        self.refused(run)
        self.assertNotIn("pointer is broken", run.stderr)

    def test_identity_does_not_call_a_pointer_broken_when_its_git_folder_cannot_be_checked(self):
        if os.geteuid() == 0:
            self.skipTest("root passes through every folder")
        closed = self.root / "closed"
        closed.mkdir()
        (self.linked / ".git").write_text(f"gitdir: {closed}/gone\n")
        closed.chmod(0)
        self.addCleanup(closed.chmod, 0o700)
        run = self.identity(self.linked, "feat/x")
        self.refused(run)
        self.assertNotIn("pointer is broken", run.stderr)
        closed.chmod(0o700)
        (closed / "file").write_text("x\n")
        (self.linked / ".git").write_text(f"gitdir: {closed}/file/gone\n")
        run = self.identity(self.linked, "feat/x")
        self.refused(run)
        self.assertNotIn("pointer is broken", run.stderr)
        (self.linked / ".git").write_text(f"gitdir: {closed}/gone\n")
        self.assertEqual(self.identity(self.linked, "feat/x").stderr, "refused: the worktree's Git pointer is broken\n")

    def second(self, name="second"):
        path = self.root / name
        git(self.main, "worktree", "add", "-q", "--detach", str(path))
        return path

    def test_identity_passes_only_for_the_named_branch_or_detached_head(self):
        run = self.identity(self.linked, "feat/x")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {"worktree": str(self.linked), "branch": "feat/x",
                                                  "head": git(self.linked, "rev-parse", "HEAD").strip()})
        self.refused(self.identity(self.linked, "feat/y"))
        self.refused(self.identity(self.linked))
        detached = self.second()
        self.assertEqual(json.loads(self.identity(detached).stdout)["branch"], None)
        self.refused(self.identity(detached, "feat/x"))

    def test_identity_refuses_a_path_that_is_or_passes_through_a_link(self):
        first, second = self.second("first"), self.second("second")
        first.rename(self.root / "first-real")
        first.symlink_to(second)
        self.refused(self.identity(first))
        through = self.root / "through"
        through.symlink_to(self.root)
        self.refused(self.identity(through / "linked", "feat/x"))
        self.refused(self.identity(Path("linked"), "feat/x"))

    def test_identity_refuses_folders_this_repository_does_not_register_as_that_worktree(self):
        other = self.root / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "commit", "-q", "--allow-empty", "-m", "base")
        foreign = self.root / "foreign"
        git(other, "worktree", "add", "-q", "-b", "feat/x", str(foreign))
        for worktree, branch in ((other, "main"), (foreign, "feat/x"), (self.main, "main"), (self.root / "missing", "feat/x")):
            with self.subTest(worktree=worktree.name):
                run = self.identity(worktree, branch)
                self.refused(run)
                self.assertNotIn("pointer is broken", run.stderr)
        git(self.main, "worktree", "lock", str(self.linked))
        self.refused(self.identity(self.linked, "feat/x"))

    def test_identity_refuses_a_stale_record_whose_folder_now_belongs_to_another_repository(self):
        shutil.rmtree(self.linked)
        other = self.root / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "commit", "-q", "--allow-empty", "-m", "base")
        git(other, "worktree", "add", "-q", "-b", "feat/x", str(self.linked))
        self.assertIn(f"worktree {self.linked}\n", git(self.main, "worktree", "list", "--porcelain"), "still registered here")
        run = self.identity(self.linked, "feat/x")
        self.refused(run)
        self.assertEqual(run.stderr, "refused: the path belongs to another repository\n")
        self.assertEqual(self.identity(self.linked, "feat/x", primary=other).returncode, 0)

    def test_identity_refuses_a_backlink_that_points_somewhere_else(self):
        second = self.second()
        backlink = self.main / ".git/worktrees/linked/gitdir"
        self.assertEqual(backlink.read_text().strip(), str(self.linked / ".git"))
        backlink.write_text(f"{second}/.git\n")
        run = self.identity(self.linked, "feat/x")
        self.refused(run)
        self.assertNotIn("pointer is broken", run.stderr)

    def test_identity_names_a_broken_pointer_and_passes_after_a_repair(self):
        moved = self.root / "moved"
        self.main.rename(moved)
        run = self.identity(self.linked, "feat/x", primary=moved)
        self.refused(run)
        self.assertEqual(run.stderr, "refused: the worktree's Git pointer is broken\n")
        git(moved, "worktree", "repair", str(self.linked))
        self.assertEqual(self.identity(self.linked, "feat/x", primary=moved).returncode, 0)


if __name__ == "__main__":
    unittest.main()
