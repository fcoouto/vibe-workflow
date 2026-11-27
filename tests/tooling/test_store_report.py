"""Exercise private report storage in throwaway repositories, never a real checkout."""
import contextlib
import importlib.util
import io
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


class StoreReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.main = self.root / "main"
        self.main.mkdir()
        git(self.main, "init", "-q", "-b", "main")
        (self.main / "README.md").write_text("fixture\n")
        git(self.main, "add", "README.md")
        git(self.main, "commit", "-qm", "fixture")
        self.linked = self.root / "linked"
        git(self.main, "worktree", "add", "-q", "-b", "task", str(self.linked))
        self.reports = self.main / FOLDER

    def cli(self, cwd, mode="full", topic="auth", body=b"# Report\n", env=None):
        return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--cwd", str(cwd), "--mode", mode,
                               "--topic", topic], input=body, env=env, capture_output=True, timeout=30)

    def refused(self, run, reason=None):
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(run.stdout, b"")
        self.assertRegex(run.stderr.decode(), r"\Arefused: [^\n]+\n\Z")
        if reason:
            self.assertIn(reason, run.stderr.decode())

    def store(self, module, cwd, mode="ship", topic="auth", body=b"# Report\n", now=NOW):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(["--cwd", str(cwd), "--mode", mode, "--topic", topic], body=body, now=now)
        return code, out.getvalue(), err.getvalue()

    def test_stores_in_the_main_checkout_privately_and_leaves_both_checkouts_clean(self):
        env = dict(os.environ, GIT_DIR=str(self.root / "elsewhere/.git"), GIT_WORK_TREE=str(self.root / "elsewhere"))
        run = self.cli(self.linked, body="# Report\nfinding ñ\n".encode(), env=env)
        self.assertEqual((run.returncode, run.stderr), (0, b""))
        path = Path(run.stdout.decode().rstrip("\n"))
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

    def test_an_existing_folder_is_made_private_and_kept(self):
        self.reports.mkdir(parents=True, mode=0o755)
        (self.reports / "old.md").write_text("old\n")
        os.chmod(self.reports, 0o755)
        self.assertEqual(self.cli(self.main, mode="ship").returncode, 0)
        self.assertEqual(self.reports.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.reports / "old.md").read_text(), "old\n")

    def test_refuses_when_the_main_checkout_tracks_a_file_the_task_index_does_not(self):
        self.reports.mkdir(parents=True)
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
        before = tree(self.root)
        self.refused(self.cli(linked), "main checkout")
        self.refused(self.cli(bare), "main checkout")
        self.assertEqual(tree(self.root), before)
        self.assertFalse((bare / ".vibe-workflow").exists())

    def test_refuses_outside_a_git_repository(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.refused(self.cli(plain), "not a Git repository")
        self.assertEqual(list(plain.iterdir()), [])
        self.refused(self.cli(self.root / "absent"))

    def test_refuses_a_symlinked_or_non_directory_folder(self):
        outside = self.root / "outside"
        outside.mkdir()
        for name in (".vibe-workflow", FOLDER):
            with self.subTest(link=name):
                (self.main / name).parent.mkdir(exist_ok=True)
                os.symlink(outside, self.main / name)
                self.refused(self.cli(self.linked), "symlink")
                self.assertEqual(list(outside.iterdir()), [])
                os.unlink(self.main / name)
        (self.main / FOLDER).write_text("a file\n")
        self.refused(self.cli(self.linked), "not a directory")
        self.assertEqual((self.main / FOLDER).read_text(), "a file\n")

    def test_refuses_a_symlinked_ignore_file_and_leaves_its_target(self):
        target = self.root / "notes.txt"
        target.write_text("user notes\n")
        self.reports.mkdir(parents=True)
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
        self.reports.mkdir(parents=True)
        os.link(shared, self.reports / ".gitignore")
        run = self.cli(self.linked)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual((self.reports / ".gitignore").read_bytes(), b"*\n")
        self.assertEqual(shared.read_text(), "!*.md\n")
        self.assertEqual(len(list(self.reports.iterdir())), 2, "no temporary file is left behind")
        self.assertEqual(git(self.main, "status", "--porcelain"), "")

    def test_two_reports_in_the_same_minute_never_overwrite(self):
        module = load()
        first = self.store(module, self.linked, body=b"first\n")
        second = self.store(module, self.main, body=b"second\n")
        third = self.store(module, self.linked, body=b"third\n")
        self.assertEqual([code for code, _, _ in (first, second, third)], [0, 0, 0])
        names = [Path(out.rstrip("\n")).name for _, out, _ in (first, second, third)]
        self.assertEqual(names, ["2026-11-27-0905-ship-auth.md", "2026-11-27-0905-ship-auth-2.md",
                                 "2026-11-27-0905-ship-auth-3.md"])
        self.assertEqual([(self.reports / name).read_bytes() for name in names], [b"first\n", b"second\n", b"third\n"])

    def test_gives_up_after_ninety_nine_names_and_follows_no_planted_link(self):
        module = load()
        target = self.root / "victim.txt"
        target.write_text("keep\n")
        self.reports.mkdir(parents=True)
        os.symlink(target, self.reports / "2026-11-27-0905-ship-auth.md")
        code, out, _ = self.store(module, self.linked)
        self.assertEqual((code, Path(out.rstrip("\n")).name), (0, "2026-11-27-0905-ship-auth-2.md"))
        self.assertEqual(target.read_text(), "keep\n")
        for number in range(3, 100):
            (self.reports / f"2026-11-27-0905-ship-auth-{number}.md").write_text("taken\n")
        code, out, err = self.store(module, self.linked)
        self.assertEqual((code, out), (2, ""))
        self.assertRegex(err, r"\Arefused: [^\n]+\n\Z")

    def test_refuses_bad_topics_modes_and_empty_reports_before_writing(self):
        for topic in ("", "Auth", "-auth", "a/b", "../x", "a b", "a.md", "auth\n", "a" * 62):
            with self.subTest(topic=topic):
                self.refused(self.cli(self.linked, topic=topic), "topic")
        self.refused(self.cli(self.linked, mode="deep"), "mode")
        self.refused(self.cli(self.linked, body=b""), "empty")
        self.assertFalse((self.main / ".vibe-workflow").exists())
        self.assertEqual(self.cli(self.linked, topic="a" * 61).returncode, 0)

    def test_refuses_a_folder_owned_by_someone_else(self):
        module = load()
        self.reports.mkdir(parents=True)
        with mock.patch.object(module.os, "geteuid", return_value=os.geteuid() + 1):
            code, out, err = self.store(module, self.linked)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("owned", err)
        self.assertEqual(list(self.reports.iterdir()), [])

    def test_removes_the_empty_file_when_git_does_not_ignore_it(self):
        module = load()
        real = module.git

        def fake(directory, *args):
            if args[0] == "check-ignore":
                return subprocess.CompletedProcess(args, 1, "", "")
            return real(directory, *args)
        module.git = fake
        code, out, err = self.store(module, self.linked)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("ignore", err)
        self.assertEqual(sorted(p.name for p in self.reports.iterdir()), [".gitignore"])

    def test_refusal_never_echoes_the_report(self):
        run = self.cli(self.root, body=b"PRIVATE-SENTINEL\n")
        self.refused(run)
        self.assertNotIn(b"PRIVATE-SENTINEL", run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
