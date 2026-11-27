"""Exercise the leftover listing in throwaway repositories, never a real checkout."""
import hashlib
import importlib.util
import json
import os
import shutil
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

    def run_script(self, mode, *paths, worktree=None, extra=()):
        command = [sys.executable, "-I", "-B", str(SCRIPT), mode, "--worktree", str(worktree or self.linked)]
        for path in paths:
            command += ["--path", path]
        return subprocess.run([*command, *extra], cwd=self.root, capture_output=True, text=True, timeout=60)

    def discard(self, *pairs, worktree=None):
        extra = [item for path, digest in pairs for item in ("--path", path, "--digest", digest)]
        return self.run_script("discard", worktree=worktree, extra=extra)

    def identity(self, worktree, branch=None, primary=None):
        extra = ["--primary", str(primary or self.main), *(("--branch", branch) if branch else ("--detached",))]
        return self.run_script("identity", worktree=worktree, extra=extra)

    def stopped(self, run, discarded=()):
        """A removing mode that refused: what it already removed is listed, then one reason."""
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"\Arefused: [^\n]+\n\Z")
        self.assertEqual([json.loads(line) for line in run.stdout.splitlines()], [{"discarded": path} for path in discarded])

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
        self.assertLessEqual({"digest", "path", "entries", "statuses", "tracked"}, set(last))
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
        for mode in ("sort", "expand", "caches"):
            self.refused(self.run_script(mode, worktree=alias))
        self.refused(self.run_script("manifest", "link", worktree=alias))
        self.assertEqual((outside / "secret.md").read_text(), "secret\n")

    def test_manifest_records_git_status_and_staging_changes_the_digest(self):
        self.write("notes/a.md")
        self.write("notes/.env")
        (self.linked / "notes/empty").mkdir()
        rows = self.rows("manifest", "notes")
        self.assertEqual({row["path"]: row.get("status") for row in rows[:-1]},
                         {"notes": None, "notes/.env": "!!", "notes/a.md": "??", "notes/empty": None})
        self.assertEqual((rows[-1]["statuses"], rows[-1]["tracked"]), (["!!", "??"], 0))
        git(self.linked, "add", "notes/a.md")
        staged = self.rows("manifest", "notes")
        self.assertEqual([row["status"] for row in staged if row.get("path") == "notes/a.md" and "type" in row], ["A "])
        self.assertEqual((staged[-1]["statuses"], staged[-1]["tracked"]), (["!!", "A "], 1))
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
        self.assertEqual([json.loads(line) for line in run.stdout.splitlines()], [{"discarded": "notes.md"}, {"discarded": "out"}])
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
        self.refused(self.run_script("discard"))
        self.refused(self.run_script("discard", "notes.md"))
        self.refused(self.run_script("discard", extra=["--path", "notes.md", "--digest", digest, "--digest", digest]))
        self.refused(self.run_script("caches", "node_modules"))
        self.stopped(self.discard(("notes.md", digest), worktree=self.main))
        self.refused(self.run_script("caches", worktree=self.main))
        self.assertTrue((self.linked / "notes.md").exists() and (self.main / "notes.md").exists())
        self.assertTrue((self.main / "node_modules").is_dir())

    def test_caches_removes_every_rebuildable_row_and_nothing_else(self):
        outside = self.outside()
        for name in ("node_modules/pkg/index.js", "src/__pycache__/app.cpython-311.pyc", "src/check.pyc", ".DS_Store",
                     "mynode_modules/index.js", "build/node_modules/x.js", ".env", "notes.md"):
            self.write(name)
        (self.linked / "node_modules/linked-out").symlink_to(outside)
        run = self.run_script("caches")
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        self.assertEqual(sorted(json.loads(line)["discarded"] for line in run.stdout.splitlines()),
                         [".DS_Store", "node_modules/", "src/__pycache__/", "src/check.pyc"])
        self.assertEqual(sorted(row["path"] for row in self.rows("sort")), [".env", "build/", "mynode_modules/", "notes.md"])
        self.assertTrue((self.linked / "build/node_modules/x.js").exists())
        self.assertEqual((outside / "secret.md").read_text(), "secret\n")
        again = self.run_script("caches")
        self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "", ""))

    def test_caches_leaves_an_ignored_folder_that_holds_a_tracked_file(self):
        self.write("node_modules/pkg/kept.js")
        self.write("node_modules/pkg/other.js")
        git(self.linked, "add", "-f", "node_modules/pkg/kept.js")
        run = self.run_script("caches")
        self.assertEqual((run.returncode, run.stdout), (0, ""), run.stderr)
        self.assertTrue((self.linked / "node_modules/pkg/other.js").exists())

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
