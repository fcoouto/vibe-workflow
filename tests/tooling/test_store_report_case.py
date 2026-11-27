"""Report storage refuses a private folder that exists under another letter case; throwaway repositories only."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills/vibe-security-review/scripts/store_report.py"


def git(cwd, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", "-c", "user.name=Fixture",
                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
                          env=env, capture_output=True, text=True, check=True).stdout


class StoreReportCaseTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "case-probe").write_text("")
        if not (self.root / "CASE-PROBE").exists():
            self.skipTest("the temporary volume is case-sensitive")
        self.main = self.root / "main"
        self.main.mkdir()
        os.chmod(self.main, 0o755)
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "commit", "-q", "--allow-empty", "-m", "fixture")
        self.source = self.root / "report.md"

    def store(self):
        self.source.write_text("# Report\n")
        return subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--cwd", str(self.main), "--mode", "full",
                               "--topic", "auth", "--from", str(self.source)], capture_output=True, text=True, timeout=30)

    def test_a_tracked_folder_under_another_letter_case_is_refused_and_left_untouched(self):
        for base, name in ((".Vibe-Workflow", "security-reports"), (".vibe-workflow", "Security-Reports")):
            with self.subTest(base=base, name=name):
                folder = self.main / base / name
                folder.mkdir(parents=True)
                os.chmod(self.main / base, 0o755)
                os.chmod(folder, 0o755)
                (folder / "kept.md").write_text("tracked\n")
                (folder / ".gitignore").write_text("!kept.md\n")
                git(self.main, "add", "-f", f"{base}/{name}")
                git(self.main, "commit", "-qm", "a tracked folder under another letter case")
                self.assertEqual(git(self.main, "ls-files", "--", ".vibe-workflow/security-reports"), "")
                run = self.store()
                self.assertEqual((run.returncode, run.stdout), (2, ""), run.stderr)
                self.assertRegex(run.stderr, r"\Arefused: [^\n]+ is not stored under exactly that name, so it was left "
                                             r"untouched; the temporary file was not deleted\n\Z")
                self.assertEqual((folder / ".gitignore").read_text(), "!kept.md\n")
                self.assertEqual(sorted(path.name for path in folder.iterdir()), [".gitignore", "kept.md"])
                self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
                self.assertEqual(git(self.main, "status", "--porcelain"), "")
                self.assertTrue(self.source.exists(), "the temporary file is kept for the chat fallback")
                git(self.main, "rm", "-rqf", f"{base}/{name}")
                git(self.main, "commit", "-qm", "undo")

    def test_the_exact_names_still_store_a_report(self):
        run = self.store()
        self.assertEqual((run.returncode, run.stderr), (0, ""))
        self.assertTrue(Path(run.stdout.strip()).is_file())


if __name__ == "__main__":
    unittest.main()
