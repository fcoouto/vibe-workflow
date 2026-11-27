"""Exercise leftover archiving in throwaway repositories, never a real checkout."""
import contextlib
import errno
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills/vibe-pull-requests/scripts/archive.py"
ARCHIVES = ".vibe-workflow/archives"
DAY = "2026-11-27"
RUN = f"{DAY}_feat-x"
# The CLI as a subprocess, with the local date held at DAY so folder names are exact and never flake at midnight.
FROZEN = ("import runpy, sys, time\n"
          "real = time.strftime\n"
          f"time.strftime = lambda fmt, *rest: {DAY!r} if fmt == '%Y-%m-%d' else real(fmt, *rest)\n"
          "sys.argv = sys.argv[1:]\n"
          "runpy.run_path(sys.argv[0], run_name='__main__')\n")


def load():
    spec = importlib.util.spec_from_file_location("archive", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
                          env=env, capture_output=True, text=True, check=True).stdout


def files(root):
    """Every file below root, by relative name; a missing root has none."""
    if not root.exists():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if not path.is_dir())


def case_insensitive(folder):
    probe = folder / "case-probe"
    probe.write_text("")
    try:
        return (folder / "CASE-PROBE").exists()
    finally:
        probe.unlink()


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main = self.checkout(self.root / "main")
        self.linked = self.root / "linked"
        git(self.main, "worktree", "add", "-q", "-b", "feat/x", str(self.linked))
        self.archives = self.main / ARCHIVES

    def checkout(self, path):
        """A checkout whose root mode does not depend on the caller's umask."""
        path.mkdir()
        os.chmod(path, 0o755)
        git(path, "init", "-q", "-b", "main")
        (path / ".gitignore").write_text(".env\n")
        (path / "README.md").write_text("fixture\n")
        git(path, "add", ".gitignore", "README.md")
        git(path, "commit", "-qm", "fixture")
        return path

    def base(self):
        """The settings folder with a mode that does not depend on the umask."""
        base = self.main / ".vibe-workflow"
        base.mkdir(exist_ok=True)
        os.chmod(base, 0o755)
        return base

    def head(self, worktree=None):
        return git(worktree or self.linked, "rev-parse", "HEAD").strip()

    def archive(self, *paths, branch="feat/x", worktree=None, cwd=None, head=None):
        """Run the script from a folder that is no repository, unless the caller names another one. The commit is the
        worktree's own unless the caller names another one."""
        command = [sys.executable, "-I", "-B", "-c", FROZEN, str(SCRIPT), "--worktree", str(worktree or self.linked),
                   "--branch", branch, "--head", head or self.head(worktree)]
        for path in paths:
            command += ["--path", path]
        return subprocess.run(command, cwd=cwd or self.root, capture_output=True, text=True, timeout=30)

    def run_in_process(self, args, exdev=False):
        """Call the script's main here, with the date held at DAY and, when asked, every rename failing across devices."""
        module = load()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(module.time, "strftime", return_value=DAY))
            if exdev:
                stack.enter_context(mock.patch.object(module.os, "rename",
                                                      side_effect=OSError(errno.EXDEV, "Cross-device link")))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = module.main(["--worktree", str(self.linked), "--branch", "feat/x", "--head", self.head(), *args])
        return code, out.getvalue(), err.getvalue()

    def refused(self, run):
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertRegex(run.stderr, r"\Arefused: [^\n]+\n\Z")
        self.assertEqual(run.stdout, "")

    def archived(self):
        return [name for name in files(self.archives) if name != ".gitignore"]

    def names(self):
        return sorted(path.name for path in self.archives.iterdir())

    def test_moves_ignored_and_untracked_paths_keeping_layout(self):
        (self.linked / ".env").write_text("SECRET=1\n")
        (self.linked / "reports").mkdir()
        (self.linked / "reports/a.md").write_text("finding\n")
        run = self.archive(".env", "reports/a.md")
        self.assertEqual(run.returncode, 0, run.stderr)
        first = self.archives / RUN / ".env"
        second = self.archives / RUN / "reports/a.md"
        self.assertEqual(run.stdout.splitlines(), [str(first), str(second)])
        self.assertEqual(first.read_text(), "SECRET=1\n")
        self.assertEqual(second.read_text(), "finding\n")
        self.assertFalse((self.linked / ".env").exists())
        self.assertFalse((self.linked / "reports/a.md").exists())
        self.assertEqual(git(self.main, "status", "--porcelain"), "")

    def test_second_run_gets_dash_two_and_never_overwrites(self):
        (self.linked / ".env").write_text("first\n")
        self.assertEqual(self.archive(".env").returncode, 0)
        (self.linked / ".env").write_text("second\n")
        run = self.archive(".env")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(Path(run.stdout.strip()), self.archives / f"{RUN}-2" / ".env")
        self.assertEqual((self.archives / f"{RUN}-2/.env").read_text(), "second\n")
        self.assertEqual((self.archives / RUN / ".env").read_text(), "first\n")
        (self.linked / ".env").write_text("third\n")
        run = self.archive(".env")
        self.assertEqual(Path(run.stdout.strip()), self.archives / f"{RUN}-3" / ".env")

    def test_a_slash_branch_gets_one_flat_folder(self):
        (self.linked / ".env").write_text("x\n")
        git(self.linked, "switch", "-q", "-c", "fix/title-sync")
        run = self.archive(".env", branch="fix/title-sync")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(Path(run.stdout.strip()), self.archives / f"{DAY}_fix-title-sync" / ".env")
        self.assertEqual(self.names(), [".gitignore", f"{DAY}_fix-title-sync"])
        (self.linked / ".env").write_text("y\n")
        git(self.linked, "switch", "-q", "-c", "feat/a/b")
        self.assertEqual(self.archive(".env", branch="feat/a/b").returncode, 0)
        self.assertEqual(self.names(), [".gitignore", f"{DAY}_feat-a-b", f"{DAY}_fix-title-sync"])

    def test_the_last_free_name_dash_ninety_nine_is_used(self):
        self.base()
        self.archives.mkdir(mode=0o700)
        for name in [RUN, *(f"{RUN}-{number}" for number in range(2, 99))]:
            (self.archives / name).mkdir()
        (self.linked / ".env").write_text("x\n")
        run = self.archive(".env")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(Path(run.stdout.strip()), self.archives / f"{RUN}-99" / ".env")

    def test_names_past_dash_ninety_nine_are_refused_and_nothing_is_overwritten(self):
        self.base()
        self.archives.mkdir(mode=0o700)
        taken = [RUN, *(f"{RUN}-{number}" for number in range(2, 100))]
        for name in taken:
            (self.archives / name).mkdir()
            (self.archives / name / "keep.txt").write_text(name)
        (self.linked / ".env").write_text("x\n")
        run = self.archive(".env")
        self.refused(run)
        self.assertIn("no free archive folder name", run.stderr)
        self.assertEqual(self.names(), [".gitignore", *sorted(taken)])
        self.assertEqual((self.archives / f"{RUN}-99/keep.txt").read_text(), f"{RUN}-99")
        self.assertEqual((self.linked / ".env").read_text(), "x\n")

    def test_refuses_tracked_absolute_dotdot_git_and_linked_parent(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("keep\n")
        (self.root / "keep.txt").write_text("keep\n")
        (self.linked / ".env").write_text("x\n")
        os.symlink(outside, self.linked / "link")
        cases = (("README.md",), (str(self.linked / ".env"),), ("../keep.txt",), (".git",), ("link/secret.txt",))
        for paths in cases:
            with self.subTest(paths=paths):
                self.refused(self.archive(*paths))
                self.assertEqual((self.linked / "README.md").read_text(), "fixture\n")
                self.assertEqual((self.linked / ".env").read_text(), "x\n")
                self.assertEqual((self.root / "keep.txt").read_text(), "keep\n")
                self.assertEqual((outside / "secret.txt").read_text(), "keep\n")
                self.assertTrue((self.linked / ".git").is_file())
                self.assertTrue((self.linked / "link").is_symlink())
                self.assertEqual(self.archived(), [])

    def test_refuses_a_linked_archives_folder_or_gitignore(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.linked / ".env").write_text("x\n")
        base = self.base()
        os.symlink(outside, self.archives)
        self.refused(self.archive(".env"))
        self.assertEqual(list(outside.iterdir()), [])
        os.unlink(self.archives)
        self.archives.mkdir(mode=0o700)
        target = self.root / "notes.txt"
        target.write_text("user notes\n")
        os.symlink(target, self.archives / ".gitignore")
        self.refused(self.archive(".env"))
        self.assertEqual(target.read_text(), "user notes\n")
        self.assertEqual((self.linked / ".env").read_text(), "x\n")
        self.assertTrue(base.is_dir())

    def test_rewrites_a_differing_gitignore(self):
        self.base()
        self.archives.mkdir(mode=0o700)
        (self.archives / ".gitignore").write_text("*.log\n")
        (self.linked / ".env").write_text("x\n")
        run = self.archive(".env")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.archives / ".gitignore").read_bytes(), b"*\n")
        self.assertEqual(Path(run.stdout.strip()).read_text(), "x\n")

    def test_cross_device_is_refused_and_source_kept(self):
        (self.linked / ".env").write_text("x\n")
        code, out, err = self.run_in_process(["--path", ".env"], exdev=True)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertRegex(err, r"\Arefused: \.env is on another device; it was kept and nothing was copied\n\Z")
        self.assertEqual((self.linked / ".env").read_text(), "x\n")
        self.assertEqual(self.archived(), [])

    def test_partial_progress_is_reported(self):
        (self.linked / ".env").write_text("x\n")
        run = self.archive(".env", "README.md")
        self.assertEqual(run.returncode, 2, run.stderr)
        dest = self.archives / RUN / ".env"
        self.assertEqual(run.stdout.splitlines(), [f"archived: {dest}"])
        self.assertEqual(run.stderr.splitlines(), ["refused: README.md is tracked by Git"])
        self.assertEqual(dest.read_text(), "x\n")
        self.assertFalse((self.linked / ".env").exists())
        self.assertEqual((self.linked / "README.md").read_text(), "fixture\n")

    def test_archives_into_the_worktrees_own_repository_whatever_the_cwd(self):
        other = self.checkout(self.root / "other")
        other_linked = self.root / "other-linked"
        git(other, "worktree", "add", "-q", "-b", "task", str(other_linked))
        (other_linked / ".env").write_text("x\n")
        (self.linked / ".env").write_text("y\n")
        run = self.archive(".env", branch="task", worktree=other_linked, cwd=self.main)
        self.assertEqual(run.returncode, 0, run.stderr)
        dest = other / ARCHIVES / f"{DAY}_task" / ".env"
        self.assertEqual(run.stdout.splitlines(), [str(dest)])
        self.assertEqual(dest.read_text(), "x\n")
        self.assertEqual((self.linked / ".env").read_text(), "y\n")
        self.assertEqual(self.archived(), [])
        self.assertFalse((self.main / ".vibe-workflow").exists())

    def test_refuses_the_main_checkout_as_worktree(self):
        (self.main / ".env").write_text("x\n")
        self.refused(self.archive(".env", worktree=self.main))
        self.assertEqual((self.main / ".env").read_text(), "x\n")
        self.assertFalse(self.archives.exists())

    def test_moves_a_folder_keeping_its_contents(self):
        (self.linked / "reports/sub").mkdir(parents=True)
        (self.linked / "reports/a.md").write_text("a\n")
        (self.linked / "reports/sub/b.md").write_text("b\n")
        run = self.archive("reports")
        self.assertEqual(run.returncode, 0, run.stderr)
        dest = self.archives / RUN / "reports"
        self.assertEqual(run.stdout.splitlines(), [str(dest)])
        self.assertEqual(files(dest), ["a.md", "sub/b.md"])
        self.assertEqual((dest / "sub/b.md").read_text(), "b\n")
        self.assertFalse((self.linked / "reports").exists())
        self.assertEqual(git(self.main, "status", "--porcelain"), "")

    def test_symbolic_links_inside_a_moved_folder_move_as_links(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("keep\n")
        (self.linked / "notes").mkdir()
        os.symlink(outside / "secret.txt", self.linked / "notes/file-link")
        os.symlink(outside, self.linked / "notes/dir-link")
        os.symlink("nowhere", self.linked / "notes/dangling")
        run = self.archive("notes")
        self.assertEqual(run.returncode, 0, run.stderr)
        dest = self.archives / RUN / "notes"
        self.assertEqual(os.readlink(dest / "file-link"), str(outside / "secret.txt"))
        self.assertEqual(os.readlink(dest / "dir-link"), str(outside))
        self.assertEqual(os.readlink(dest / "dangling"), "nowhere")
        self.assertEqual((outside / "secret.txt").read_text(), "keep\n")
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["secret.txt"])

    def test_a_symbolic_link_path_moves_as_a_link(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("keep\n")
        os.symlink(outside, self.linked / "link")
        run = self.archive("link")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(os.readlink(self.archives / RUN / "link"), str(outside))
        self.assertFalse(os.path.lexists(self.linked / "link"))
        self.assertEqual((outside / "secret.txt").read_text(), "keep\n")

    def test_a_trailing_slash_names_the_same_folder(self):
        (self.linked / "reports").mkdir()
        (self.linked / "reports/a.md").write_text("a\n")
        self.refused(self.archive("reports//"))
        self.assertEqual((self.linked / "reports/a.md").read_text(), "a\n")
        run = self.archive("reports/")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.splitlines(), [str(self.archives / RUN / "reports")])
        self.assertEqual(files(self.archives / RUN / "reports"), ["a.md"])

    def test_a_git_entry_named_in_another_case_is_refused(self):
        if not case_insensitive(self.root):
            self.skipTest("the temporary volume is case-sensitive")
        before = (self.linked / ".git").read_bytes()
        self.refused(self.archive(".GIT"))
        self.assertTrue((self.linked / ".git").is_file())
        self.assertEqual((self.linked / ".git").read_bytes(), before)
        self.assertEqual(self.archived(), [])

    def test_archived_lines_arrive_before_the_refusal_when_streams_merge(self):
        (self.linked / ".env").write_text("x\n")
        command = [sys.executable, "-I", "-B", "-c", FROZEN, str(SCRIPT), "--worktree", str(self.linked),
                   "--branch", "feat/x", "--head", self.head(), "--path", ".env", "--path", "README.md"]
        run = subprocess.run(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout.splitlines(),
                         [f"archived: {self.archives / RUN / '.env'}", "refused: README.md is tracked by Git"])

    def test_each_path_releases_its_descriptors_before_the_next(self):
        module = load()
        for folder in ("d1", "d2", "d3"):
            (self.linked / folder).mkdir()
            (self.linked / folder / "f.txt").write_text("x\n")
        counts = []
        real_move = module._move

        def record(*args, **kwargs):
            counts.append(len(os.listdir("/dev/fd")))
            return real_move(*args, **kwargs)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(module, "_move", side_effect=record):
            code = module.main(["--worktree", str(self.linked), "--branch", "feat/x", "--head", self.head(),
                                "--path", "d1/f.txt", "--path", "d2/f.txt", "--path", "d3/f.txt"])
        self.assertEqual(code, 0)
        self.assertEqual(len(set(counts)), 1, counts)

    def test_a_refused_first_move_leaves_no_run_folder(self):
        (self.linked / ".env").write_text("x\n")
        code, out, err = self.run_in_process(["--path", ".env"], exdev=True)
        self.assertEqual(code, 2)
        self.assertEqual(err, "refused: .env is on another device; it was kept and nothing was copied\n")
        self.assertEqual(self.names(), [".gitignore"])
        self.assertEqual((self.linked / ".env").read_text(), "x\n")

    def test_a_refused_nested_first_move_removes_the_folders_it_made(self):
        (self.linked / "reports").mkdir()
        (self.linked / "reports/a.md").write_text("a\n")
        code, out, err = self.run_in_process(["--path", "reports/a.md"], exdev=True)
        self.assertEqual(code, 2)
        self.assertRegex(err, r"\Arefused: reports/a\.md is on another device")
        self.assertEqual(self.names(), [".gitignore"])
        self.assertEqual((self.linked / "reports/a.md").read_text(), "a\n")

    def test_a_refused_move_keeps_the_folder_that_existed_before_the_run(self):
        self.base()
        self.archives.mkdir(mode=0o700)
        (self.archives / RUN).mkdir()
        (self.linked / "reports").mkdir()
        (self.linked / "reports/a.md").write_text("a\n")
        code, out, err = self.run_in_process(["--path", "reports/a.md"], exdev=True)
        self.assertEqual(code, 2)
        self.assertEqual(self.names(), [".gitignore", RUN])
        self.assertEqual(list((self.archives / RUN).iterdir()), [], "the folder that existed stays, empty")

    def test_a_name_in_another_case_is_refused_and_nothing_moves(self):
        if not case_insensitive(self.root):
            self.skipTest("the temporary volume is case-sensitive")
        before = (self.linked / "README.md").read_bytes()
        self.refused(self.archive("readme.md"))
        self.assertEqual((self.linked / "README.md").read_bytes(), before)
        self.assertEqual(self.archived(), [])

    def test_a_git_name_in_another_case_below_the_worktree_is_refused(self):
        if not case_insensitive(self.root):
            self.skipTest("the temporary volume is case-sensitive")
        (self.linked / "sub").mkdir()
        (self.linked / "sub/.git").write_text("gitdir: elsewhere\n")
        self.refused(self.archive("sub/.GIT"))
        self.assertEqual((self.linked / "sub/.git").read_text(), "gitdir: elsewhere\n")
        self.assertEqual(self.archived(), [])

    def test_refuses_an_invalid_branch_name(self):
        (self.linked / ".env").write_text("x\n")
        for branch in ("feat..x", "-x", "@{-1}", "a b"):
            with self.subTest(branch=branch):
                self.refused(self.archive(".env", branch=branch))
                self.assertEqual((self.linked / ".env").read_text(), "x\n")
        self.assertFalse((self.main / ".vibe-workflow").exists())

    def kept(self, run, reason):
        """A refusal before anything was made: the leftover file stays and the main checkout has no settings folder."""
        self.refused(run)
        self.assertEqual(run.stderr, f"refused: {reason}\n")
        self.assertEqual((self.linked / ".env").read_text(), "x\n")
        self.assertFalse((self.main / ".vibe-workflow").exists())

    def test_refuses_a_worktree_that_is_not_on_the_named_branch(self):
        (self.linked / ".env").write_text("x\n")
        git(self.main, "branch", "other")
        self.kept(self.archive(".env", branch="other"), "the worktree is not on that branch")
        self.kept(self.archive(".env", branch="main"), "the worktree is not on that branch")
        git(self.linked, "switch", "-q", "--detach")
        self.kept(self.archive(".env"), "the worktree is detached; archive needs the branch the merge was confirmed for")

    def test_refuses_a_head_that_is_not_the_worktrees_full_commit(self):
        (self.linked / ".env").write_text("x\n")
        head = self.head()
        for given in ("HEAD", head[:12], head.upper(), head + "0", "feat/x"):
            with self.subTest(head=given):
                self.kept(self.archive(".env", head=given), "--head must be the full commit hash the merge was confirmed for")
        git(self.linked, "commit", "-q", "--allow-empty", "-m", "after the merge")
        self.kept(self.archive(".env", head=head), "the worktree's HEAD is not the commit that was confirmed")
        command = [sys.executable, "-I", "-B", str(SCRIPT), "--worktree", str(self.linked), "--branch", "feat/x", "--path", ".env"]
        run = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.refused(run)
        self.assertIn("--head <commit>", run.stderr)
        self.assertEqual(self.archive(".env").returncode, 0)

    def test_refuses_a_worktree_the_repository_does_not_register_at_that_path(self):
        (self.linked / ".env").write_text("x\n")
        second = self.root / "second"
        git(self.main, "worktree", "add", "-q", "--detach", str(second))
        backlink = self.main / ".git/worktrees/linked/gitdir"
        recorded = backlink.read_text()
        backlink.write_text(f"{second}/.git\n")
        self.kept(self.archive(".env"), "the repository does not register --worktree at that path with a backlink to it")
        backlink.write_text(recorded)
        moved = self.root / "moved"
        self.linked.rename(moved)
        run = self.archive(".env", worktree=moved, head=self.head(moved))
        self.refused(run)
        self.assertEqual(run.stderr, "refused: the repository does not register --worktree at that path with a backlink to it\n")
        self.assertEqual((moved / ".env").read_text(), "x\n")
        self.assertFalse((self.main / ".vibe-workflow").exists())

    def test_refuses_a_worktree_with_a_paused_git_operation(self):
        (self.linked / ".env").write_text("x\n")
        own = Path(git(self.linked, "rev-parse", "--path-format=absolute", "--git-dir").strip())
        for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG",
                     "AUTO_MERGE", "MERGE_AUTOSTASH"):
            with self.subTest(state=name):
                state = own / name
                state.mkdir() if name.startswith("rebase") else state.write_text("0" * 40 + "\n")
                self.kept(self.archive(".env"), f"a Git operation is paused in the worktree ({name})")
                state.rmdir() if name.startswith("rebase") else state.unlink()
        self.assertEqual(self.archive(".env").returncode, 0)

    def test_a_settings_or_archives_folder_under_another_letter_case_is_refused_and_left_untouched(self):
        if not case_insensitive(self.root):
            self.skipTest("the temporary volume is case-sensitive")
        (self.linked / ".env").write_text("x\n")
        for base, name in ((".Vibe-Workflow", "archives"), (".vibe-workflow", "Archives")):
            with self.subTest(base=base, name=name):
                folder = self.main / base / name
                folder.mkdir(parents=True)
                os.chmod(self.main / base, 0o755)
                os.chmod(folder, 0o755)
                (folder / "kept.md").write_text("tracked\n")
                (folder / ".gitignore").write_text("!kept.md\n")
                git(self.main, "add", "-f", f"{base}/{name}")
                git(self.main, "commit", "-qm", "a tracked folder under another letter case")
                self.assertEqual(git(self.main, "ls-files", "--", ".vibe-workflow/archives"), "", "Git sees no tracked file")
                self.refused(self.archive(".env"))
                self.assertEqual((self.linked / ".env").read_text(), "x\n")
                self.assertEqual((folder / ".gitignore").read_text(), "!kept.md\n")
                self.assertEqual(sorted(path.name for path in folder.iterdir()), [".gitignore", "kept.md"])
                self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
                self.assertEqual(git(self.main, "status", "--porcelain"), "")
                git(self.main, "rm", "-rqf", f"{base}/{name}")
                git(self.main, "commit", "-qm", "undo")
                shutil.rmtree(self.main / base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
