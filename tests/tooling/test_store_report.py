"""Exercise private report storage in throwaway repositories, never a real checkout."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills/vibe-security-review/scripts/store_report.py"
FOLDER = ".vibe-workflow/security-reports"
NOW = time.struct_time((2026, 11, 27, 9, 5, 0, 4, 331, 0))
BODY = b"# Report\n"


def load():
    spec = importlib.util.spec_from_file_location("store_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
                          env=env, capture_output=True, text=True, check=True).stdout


def tree(root):
    """Every path below root with its type, link target or content, skipping Git's own folders."""
    seen = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if name == ".git" or name.startswith(".git/") or "/.git/" in name or name.endswith("/.git"):
            continue
        seen[name] = os.readlink(path) if path.is_symlink() else "dir" if path.is_dir() else path.read_bytes()
    return seen


def identity(path):
    status = os.stat(path)
    return status.st_dev, status.st_ino


class StoreReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main = self.checkout(self.root / "main")
        self.linked = self.root / "linked"
        git(self.main, "worktree", "add", "-q", "-b", "task", str(self.linked))
        self.reports = self.main / FOLDER
        self.assertEqual(self.main.stat().st_mode & 0o777, 0o755)
        self.scratch = self.root / "scratch"
        self.scratch.mkdir(mode=0o700)
        self.sources = 0

    def checkout(self, path):
        """A checkout whose root mode does not depend on the caller's umask."""
        path.mkdir()
        os.chmod(path, 0o755)
        git(path, "init", "-q", "-b", "main")
        (path / "README.md").write_text("fixture\n")
        git(path, "add", "README.md")
        git(path, "commit", "-qm", "fixture")
        return path

    def folders(self, path):
        """Create a folder below the main checkout; `.vibe-workflow` and it get mode 0755 whatever the umask is."""
        path.mkdir(parents=True, exist_ok=True)
        for folder in (self.main / ".vibe-workflow", path):
            os.chmod(folder, 0o755)
        return path

    def source(self, body=BODY):
        """A report body in a temporary file outside every checkout, as the coordinator writes it."""
        self.sources += 1
        path = self.scratch / f"report-{self.sources}.md"
        path.write_bytes(body)
        return path

    def cli(self, cwd, mode="full", topic="auth", body=BODY, env=None, source=None, stdin=b""):
        source = self.source(body) if source is None else source
        run = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--cwd", str(cwd), "--mode", mode,
                              "--topic", topic, "--from", str(source)], input=stdin, env=env,
                             capture_output=True, timeout=30)
        run.source, run.body = source, body
        return run

    def saved(self, run):
        self.assertEqual((run.returncode, run.stderr), (0, b""))
        self.assertFalse(os.path.lexists(run.source), "the temporary file is deleted after a successful write")
        return Path(os.fsdecode(run.stdout.removesuffix(b"\n")))

    def refused(self, run, reason=None):
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(run.stdout, b"")
        self.assertRegex(run.stderr.decode(), r"\Arefused: [^\n]+\n\Z")
        self.assertIn("the temporary file was not deleted", run.stderr.decode())
        if reason:
            self.assertIn(reason, run.stderr.decode())
        if run.body is not None:
            self.assertEqual(run.source.read_bytes(), run.body, "the temporary file is kept for the chat fallback")

    def store(self, module, cwd, mode="ship", topic="auth", body=BODY, now=NOW):
        return Path(module.store(str(cwd), mode, topic, str(self.source(body)), now))

    def test_stores_in_the_main_checkout_privately_and_leaves_both_checkouts_clean(self):
        env = dict(os.environ, GIT_DIR=str(self.root / "elsewhere/.git"), GIT_WORK_TREE=str(self.root / "elsewhere"))
        path = self.saved(self.cli(self.linked, body="# Report\nfinding ñ\n".encode(), env=env))
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parent, self.reports)
        self.assertRegex(path.name, r"\A\d{4}-\d{2}-\d{2}-\d{4}-full-auth\.md\Z")
        self.assertEqual(path.read_bytes(), "# Report\nfinding ñ\n".encode())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.reports.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.reports / ".gitignore").read_bytes(), b"*\n")
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), [".gitignore", path.name])
        for checkout in (self.main, self.linked):
            self.assertEqual(git(checkout, "status", "--porcelain"), "", checkout)
        git(self.main, "check-ignore", "-q", str(path))
        self.assertFalse((self.linked / ".vibe-workflow").exists())
        self.assertFalse((self.root / "elsewhere").exists())

    def test_the_report_arrives_byte_for_byte_whatever_it_contains(self):
        body = (b"# Report\nREPORT\nEOF\n'REPORT'\n$(touch pwned) `touch pwned` ${HOME} \\ \"double\" 'single'\n"
                b"; rm -rf . #\n\xff\xfe\x01\x7f\r\n\ttab, no final newline")
        run = self.cli(self.linked, body=body, stdin=b"never read from standard input\n")
        self.assertEqual(self.saved(run).read_bytes(), body)
        self.assertEqual([p.name for p in self.root.rglob("pwned")], [])
        kept = self.cli(self.linked, topic="Bad Topic", body=body)
        self.refused(kept, "topic")
        self.assertEqual(kept.source.read_bytes(), body)

    def test_the_body_comes_only_from_a_regular_file_named_by_from(self):
        target = self.source(b"elsewhere\n")
        link = self.scratch / "link.md"
        os.symlink(target, link)
        for source, reason in ((link, "--from"), (self.scratch, "--from"), (self.scratch / "absent.md", "--from")):
            with self.subTest(source=source.name):
                run = self.cli(self.linked, source=source, body=None)
                self.refused(run, reason)
        self.assertEqual(target.read_bytes(), b"elsewhere\n")
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.main / ".vibe-workflow").exists())
        run = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--cwd", str(self.linked), "--mode", "full",
                              "--topic", "auth"], input=b"# Report on standard input\n", capture_output=True, timeout=30)
        self.assertEqual((run.returncode, run.stdout), (2, b""))
        self.assertIn(b"--from", run.stderr)
        self.assertFalse((self.main / ".vibe-workflow").exists())

    def test_an_existing_folder_is_made_private_and_kept(self):
        self.folders(self.reports)
        (self.reports / "old.md").write_text("old\n")
        self.saved(self.cli(self.main, mode="ship"))
        self.assertEqual(self.reports.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.reports / "old.md").read_text(), "old\n")

    def test_refuses_when_the_main_checkout_tracks_a_file_the_task_index_does_not(self):
        self.folders(self.reports)
        (self.reports / ".gitignore").write_text("# tracked on main\n")
        git(self.main, "add", "-f", f"{FOLDER}/.gitignore")
        git(self.main, "commit", "-qm", "track the ignore file")
        self.assertEqual(git(self.linked, "ls-files", "--", FOLDER), "")
        self.assertNotEqual(git(self.main, "ls-files", "--", FOLDER), "")
        before = tree(self.main)
        self.refused(self.cli(self.linked), "tracks")
        self.assertEqual(tree(self.main), before)
        self.assertEqual(git(self.main, "status", "--porcelain"), "")

    def test_refuses_a_bare_first_record(self):
        bare, linked = self.root / "bare.git", self.root / "from-bare"
        git(self.root, "clone", "-q", "--bare", str(self.main), str(bare))
        git(bare, "worktree", "add", "-q", str(linked), "main")
        def checkouts():
            return {name: value for name, value in tree(self.root).items() if not name.startswith("scratch")}
        before = checkouts()
        self.refused(self.cli(linked), "main checkout")
        self.refused(self.cli(bare), "main checkout")
        self.assertEqual(checkouts(), before)
        self.assertFalse((bare / ".vibe-workflow").exists())

    def test_refuses_outside_a_git_repository(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.refused(self.cli(plain), "not a Git repository")
        self.assertEqual(list(plain.iterdir()), [])
        self.refused(self.cli(self.root / "absent"))

    def test_a_carriage_return_in_the_main_checkout_path_is_kept(self):
        real = self.checkout(self.root / "car\rriage")
        decoy = self.checkout(self.root / "car\nriage")
        linked = self.root / "from-carriage"
        git(real, "worktree", "add", "-q", "-b", "task", str(linked))
        path = self.saved(self.cli(linked))
        self.assertEqual(identity(path.parents[2]), identity(real))
        self.assertNotEqual(identity(path.parents[2]), identity(decoy))
        self.assertEqual(path.read_bytes(), BODY)
        self.assertFalse((decoy / ".vibe-workflow").exists())
        self.assertEqual([p.name for p in (real / FOLDER).iterdir() if p.name != ".gitignore"], [path.name])

    def test_refuses_a_symlinked_or_non_directory_folder(self):
        outside = self.root / "outside"
        outside.mkdir()
        for name in (".vibe-workflow", FOLDER):
            with self.subTest(link=name):
                if name == FOLDER:
                    self.folders(self.main / ".vibe-workflow")
                os.symlink(outside, self.main / name)
                self.refused(self.cli(self.linked), "symlink")
                self.assertEqual(list(outside.iterdir()), [])
                os.unlink(self.main / name)
        (self.main / FOLDER).write_text("a file\n")
        self.refused(self.cli(self.linked), "not a directory")
        self.assertEqual((self.main / FOLDER).read_text(), "a file\n")

    def test_refuses_a_checkout_or_settings_folder_everyone_can_write_and_changes_no_mode(self):
        base = self.folders(self.main / ".vibe-workflow")
        for folder, mode in ((base, 0o777), (base, 0o757), (base, 0o1777), (self.main, 0o777), (self.main, 0o757)):
            with self.subTest(folder=folder.name, mode=oct(mode)):
                before = folder.stat().st_mode & 0o7777
                os.chmod(folder, mode)
                self.addCleanup(os.chmod, folder, before)
                self.refused(self.cli(self.linked), "writable by everyone")
                self.assertEqual(folder.stat().st_mode & 0o7777, mode, "a shared folder's mode is never changed")
                os.chmod(folder, before)
                self.assertEqual(list(base.iterdir()), [])
        self.saved(self.cli(self.linked))

    def test_a_group_writable_checkout_or_settings_folder_is_accepted_and_keeps_its_mode(self):
        # The usual result of umask 002. The reports folder itself still becomes 0700.
        base = self.folders(self.main / ".vibe-workflow")
        for folder in (base, self.main):
            os.chmod(folder, 0o775)
            self.addCleanup(os.chmod, folder, 0o755)
        path = self.saved(self.cli(self.linked))
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual([folder.stat().st_mode & 0o777 for folder in (base, self.main)], [0o775, 0o775])
        self.assertEqual(self.reports.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def shared_parent(self):
        """A `.vibe-workflow` its group can write: a group member can rename another folder into the reports place."""
        base = self.folders(self.main / ".vibe-workflow")
        os.chmod(base, 0o775)
        self.addCleanup(os.chmod, base, 0o755)
        return base

    def test_under_a_group_writable_parent_an_unfamiliar_folder_is_refused_and_left_as_it_is(self):
        self.shared_parent()
        # The user's own folder, moved into place by someone else: owned by the user, but not a reports folder.
        self.reports.mkdir()
        os.chmod(self.reports, 0o755)
        (self.reports / ".gitignore").write_bytes(b"build/\n!keep.txt\n")
        (self.reports / "notes.txt").write_bytes(b"unrelated\n")
        before = tree(self.main)
        self.refused(self.cli(self.linked), "left untouched")
        self.assertEqual(self.reports.stat().st_mode & 0o777, 0o755, "no chmod on an unfamiliar folder")
        self.assertEqual((self.reports / ".gitignore").read_bytes(), b"build/\n!keep.txt\n")
        self.assertEqual(tree(self.main), before)

    def test_under_a_group_writable_parent_each_needed_repair_is_refused(self):
        self.shared_parent()
        self.reports.mkdir()
        cases = (("mode", 0o755, b"*\n"), ("mode", 0o750, b"*\n"), ("ignore", 0o700, b"*.log\n"),
                 ("ignore", 0o700, b"*\n\n"), ("ignore", 0o700, None))
        for what, mode, ignore in cases:
            with self.subTest(what=what, mode=oct(mode), ignore=ignore):
                os.chmod(self.reports, mode)
                if ignore is None:
                    (self.reports / ".gitignore").unlink()
                else:
                    (self.reports / ".gitignore").write_bytes(ignore)
                before = tree(self.main)
                self.refused(self.cli(self.linked), "left untouched")
                self.assertEqual(self.reports.stat().st_mode & 0o777, mode)
                self.assertEqual(tree(self.main), before)

    def test_under_a_group_writable_parent_an_already_correct_folder_is_used(self):
        base = self.shared_parent()
        self.reports.mkdir()
        os.chmod(self.reports, 0o700)
        (self.reports / ".gitignore").write_bytes(b"*\n")
        (self.reports / "2026-11-26-1000-full-old.md").write_bytes(b"old\n")
        path = self.saved(self.cli(self.linked))
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual((self.reports / "2026-11-26-1000-full-old.md").read_bytes(), b"old\n")
        self.assertEqual((base.stat().st_mode & 0o777, self.reports.stat().st_mode & 0o777), (0o775, 0o700))
        # A second report goes into the folder the first run created under the same shared parent.
        second = self.saved(self.cli(self.linked, topic="again"))
        self.assertEqual(second.parent, self.reports)

    def test_under_a_private_parent_an_existing_folder_is_still_repaired(self):
        self.folders(self.reports)
        (self.reports / ".gitignore").write_bytes(b"*.log\n")
        self.saved(self.cli(self.linked))
        self.assertEqual(self.reports.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.reports / ".gitignore").read_bytes(), b"*\n")

    def test_refuses_a_checkout_or_settings_folder_owned_by_someone_else(self):
        module = load()
        mine = os.geteuid()
        base = self.folders(self.main / ".vibe-workflow")
        real = module.os.fstat
        for foreign, reason in ((self.main, "the main checkout is owned by another user"),
                                (base, ".vibe-workflow is owned by another user")):
            with self.subTest(folder=foreign.name):
                target = identity(foreign)

                def fstat(descriptor, real=real, target=target):
                    status = real(descriptor)
                    if (status.st_dev, status.st_ino) != target:
                        return status
                    return os.stat_result((*status[:4], mine + 1, *status[5:]))
                with mock.patch.object(module.os, "fstat", fstat):
                    with self.assertRaisesRegex(module.Refused, reason):
                        self.store(module, self.linked)
                self.assertEqual(list(base.iterdir()), [])

    def test_refuses_a_folder_owned_by_someone_else(self):
        module = load()
        self.folders(self.reports)
        with mock.patch.object(module.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(module.Refused, "owned"):
                self.store(module, self.linked)
        self.assertEqual(list(self.reports.iterdir()), [])

    def test_refuses_a_symlinked_ignore_file_and_leaves_its_target(self):
        target = self.root / "notes.txt"
        target.write_text("user notes\n")
        self.folders(self.reports)
        os.symlink(target, self.reports / ".gitignore")
        self.refused(self.cli(self.linked), ".gitignore")
        self.assertEqual(target.read_text(), "user notes\n")
        self.assertEqual(os.readlink(self.reports / ".gitignore"), str(target))
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), [".gitignore"])
        os.unlink(self.reports / ".gitignore")
        (self.reports / ".gitignore").mkdir()
        self.refused(self.cli(self.linked), ".gitignore")

    def test_replaces_a_differing_ignore_file_without_writing_through_it(self):
        shared = self.root / "shared.txt"
        shared.write_text("!*.md\n")
        self.folders(self.reports)
        os.link(shared, self.reports / ".gitignore")
        self.saved(self.cli(self.linked))
        self.assertEqual((self.reports / ".gitignore").read_bytes(), b"*\n")
        self.assertEqual(shared.read_text(), "!*.md\n")
        self.assertEqual(len(list(self.reports.iterdir())), 2, "no temporary file is left behind")
        self.assertEqual(git(self.main, "status", "--porcelain"), "")

    def test_two_reports_in_the_same_minute_never_overwrite(self):
        module = load()
        paths = [self.store(module, cwd, body=body) for cwd, body in
                 ((self.linked, b"first\n"), (self.main, b"second\n"), (self.linked, b"third\n"))]
        self.assertEqual([path.name for path in paths], ["2026-11-27-0905-ship-auth.md", "2026-11-27-0905-ship-auth-2.md",
                                                         "2026-11-27-0905-ship-auth-3.md"])
        self.assertEqual([path.read_bytes() for path in paths], [b"first\n", b"second\n", b"third\n"])
        self.assertEqual(list(self.scratch.iterdir()), [], "each temporary file is deleted after its write")

    def test_gives_up_after_ninety_nine_names_and_follows_no_planted_link(self):
        module = load()
        target = self.root / "victim.txt"
        target.write_text("keep\n")
        self.folders(self.reports)
        os.symlink(target, self.reports / "2026-11-27-0905-ship-auth.md")
        self.assertEqual(self.store(module, self.linked).name, "2026-11-27-0905-ship-auth-2.md")
        self.assertEqual(target.read_text(), "keep\n")
        for number in range(3, 100):
            (self.reports / f"2026-11-27-0905-ship-auth-{number}.md").write_text("taken\n")
        with self.assertRaisesRegex(module.Refused, "no free report name"):
            self.store(module, self.linked)
        self.assertEqual(len(list(self.scratch.iterdir())), 1, "the refused report's temporary file is kept")

    def test_refuses_bad_topics_modes_and_empty_reports_before_writing(self):
        for topic in ("", "Auth", "-auth", "a/b", "../x", "a b", "a.md", "auth\n", "a" * 62):
            with self.subTest(topic=topic):
                self.refused(self.cli(self.linked, topic=topic), "topic")
        self.refused(self.cli(self.linked, mode="deep"), "mode")
        self.refused(self.cli(self.linked, body=b""), "empty")
        self.assertFalse((self.main / ".vibe-workflow").exists())
        self.saved(self.cli(self.linked, topic="a" * 61))

    def test_removes_the_empty_file_when_git_does_not_ignore_it(self):
        module = load()
        real = module.git

        def fake(directory, *args):
            if args[0] == "check-ignore":
                return subprocess.CompletedProcess(args, 1, b"", b"")
            return real(directory, *args)
        module.git = fake
        with self.assertRaisesRegex(module.Refused, "ignore"):
            self.store(module, self.linked)
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), [".gitignore"])

    def test_refuses_when_the_named_folder_is_no_longer_the_opened_one(self):
        module = load()
        real, moved = module.git, self.main / ".vibe-workflow/moved-aside"

        def swap(directory, *args):
            if args[0] == "check-ignore":
                # Someone renames the opened folder and puts another, also ignored, under its name.
                os.rename(self.reports, moved)
                self.reports.mkdir()
                (self.reports / ".gitignore").write_bytes(b"*\n")
            return real(directory, *args)
        module.git = swap
        with self.assertRaisesRegex(module.Refused, "changed") as raised:
            self.store(module, self.linked, body=b"private finding\n")
        self.assertNotIn("left untouched", str(raised.exception), "the created file was still there to remove")
        self.assertEqual(sorted(p.name for p in moved.iterdir()), [".gitignore"], "the created file is removed")
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), [".gitignore"])
        self.assertEqual(len(list(self.scratch.iterdir())), 1)

    def test_refuses_when_the_named_report_is_no_longer_the_opened_one(self):
        module = load()
        real = module.git

        replaced = []

        def swap(directory, *args):
            if args[0] == "check-ignore":
                (name,) = [p for p in self.reports.iterdir() if p.name != ".gitignore"]
                name.unlink()
                name.write_bytes(b"someone else's file\n")
                replaced.append(name)
            return real(directory, *args)
        module.git = swap
        with self.assertRaisesRegex(module.Refused, "changed.*left untouched"):
            self.store(module, self.linked, body=b"private finding\n")
        # The refusal cleans up only the file this run created; the file now under that name is not its to delete.
        self.assertEqual(replaced[0].read_bytes(), b"someone else's file\n")
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), [".gitignore", replaced[0].name])
        for path in self.reports.iterdir():
            self.assertNotIn(b"private finding", path.read_bytes())

    def test_a_refusal_leaves_a_link_planted_under_the_report_name(self):
        module = load()
        real, victim = module.git, self.root / "victim.txt"
        victim.write_text("keep\n")

        def swap(directory, *args):
            if args[0] == "check-ignore":
                (name,) = [p for p in self.reports.iterdir() if p.name != ".gitignore"]
                name.unlink()
                os.symlink(victim, name)
            return real(directory, *args)
        module.git = swap
        with self.assertRaisesRegex(module.Refused, "left untouched"):
            self.store(module, self.linked, body=b"private finding\n")
        self.assertEqual(victim.read_text(), "keep\n")
        self.assertEqual([os.readlink(p) for p in self.reports.iterdir() if p.is_symlink()], [str(victim)])

    def test_refusal_never_echoes_the_report(self):
        run = self.cli(self.root, body=b"PRIVATE-SENTINEL\n")
        self.refused(run)
        self.assertNotIn(b"PRIVATE-SENTINEL", run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
