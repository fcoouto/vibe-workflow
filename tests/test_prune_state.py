import contextlib
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prune_state.py"
sys.path.insert(0, str(ROOT / "scripts"))
import prune_state  # noqa: E402

OLD = time.time() - 30 * 86400


def h(number):
    return hashlib.sha256(str(number).encode()).hexdigest()


class Fixture:
    """A fake lock root with every file class, plus sentinels next to it."""

    def __init__(self, base):
        self.base = Path(base)
        self.root = self.base / "vcw-transactions"
        self.root.mkdir(mode=0o700)
        os.chmod(self.root, 0o700)
        self.existing_plan = self.base / "plans/present"
        self.existing_plan.mkdir(parents=True)
        self.missing_plan = self.base / "plans/gone/plan"
        self.held = []
        self.sentinels = {"outside.txt": b"keep", h(99) + ".lock": b""}
        for name, raw in self.sentinels.items():
            self._write(self.base / name, raw, OLD)

    def _write(self, path, raw=b"", mtime=OLD):
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        os.utime(path, (mtime, mtime))

    def file(self, name, raw=b"", mtime=OLD):
        self._write(self.root / name, raw, mtime)

    def marker(self, name, plan_dir, mtime=OLD, **extra):
        body = dict(schema_version=1, transaction_id="t", id="a", attempt_id="x", **extra)
        if plan_dir is not None:
            body["plan_dir"] = str(plan_dir)
        self.file(name, json.dumps(body, sort_keys=True).encode(), mtime)

    def hold(self, name):
        fd = os.open(self.root / name, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.held.append(fd)

    def release(self):
        for fd in self.held:
            os.close(fd)
        self.held = []

    def build(self):
        self.file(h(1) + ".lock")
        self.file("authority-" + h(2) + ".lock")
        self.file("canonical-" + h(3) + ".lock")
        self.file(h(4) + ".lock", mtime=time.time())
        self.file(h(5) + ".lock", b"x")
        self.file(h(6) + ".lock")
        os.symlink(self.base / (h(99) + ".lock"), self.root / (h(7) + ".lock"))
        (self.root / ("canonical-" + h(8) + ".lock")).mkdir()
        self.marker(h(10) + ".command.json", self.missing_plan)
        self.marker("authority-" + h(11) + ".json", self.missing_plan, manifest_digest="d")
        self.file("authority-" + h(11) + ".lock")
        self.marker(h(6) + ".command.json", self.missing_plan)
        self.marker("canonical-" + h(12) + ".lock.json", self.existing_plan)
        self.marker(h(13) + ".command.json", self.missing_plan, mtime=time.time())
        self.file("authority-" + h(14) + ".json", b"{not json")
        self.file(h(15) + ".command.json", b"[1, 2]")
        self.marker("authority-" + h(16) + ".json", None)
        self.marker(h(17) + ".command.json", "relative/plan")
        self.file(".d4-abc", b"tmp")
        for name in ("notes.txt", h(18) + ".LOCK", h(1) + ".lock.bak", "authority-short.lock"):
            self.file(name)
        self.hold(h(6) + ".lock")
        return self

    eligible = {h(1) + ".lock", "authority-" + h(2) + ".lock", "canonical-" + h(3) + ".lock",
                "authority-" + h(11) + ".lock", h(10) + ".command.json", "authority-" + h(11) + ".json"}

    def names(self):
        return set(os.listdir(self.root))

    def outside(self):
        return {name: (self.base / name).read_bytes() for name in self.sentinels}


class PruneStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="prune-state-test-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.fx = Fixture(self.temp)
        self.addCleanup(self.fx.release)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(self.fx.root), *args],
                              capture_output=True, text=True, timeout=30)

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = prune_state.main(["--root", str(self.fx.root), *args])
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def scan(self, root=None):
        fd = prune_state.open_root(root or self.fx.root)
        self.addCleanup(os.close, fd)
        return fd, prune_state.scan(fd, self.fx.root, 7)

    def reasons(self, report, kind):
        result = {}
        for entry in report.select(kind):
            result[entry.reason] = result.get(entry.reason, 0) + 1
        return result

    def test_default_root_matches_production_lock_root(self):
        self.assertEqual(str(prune_state.DEFAULT_ROOT), str(Path.home()) + "/.local/share/vcw-transactions")
        source = (ROOT / "runtime/changes.py").read_text()
        self.assertIn('LOCK_ROOT = Path.home() / ".local/share/vcw-transactions"', source)

    def test_scan_classifies_every_file_exactly(self):
        self.fx.build()
        _, report = self.scan()
        self.assertEqual(self.reasons(report, "lock"), {
            "eligible": 4, "young": 1, "non-empty": 1, "held": 1, "symlink": 1, "not a regular file": 1})
        self.assertEqual(self.reasons(report, "marker"), {
            "eligible": 2, "sibling lock held": 1, "plan_dir exists": 1, "young": 1,
            "unparseable": 2, "no path": 1, "invalid path": 1})
        self.assertEqual(len(report.select("temp")), 1)
        self.assertEqual(len(report.select("unknown")), 4)
        self.assertEqual({e.name for e in report.entries if e.eligible}, Fixture.eligible)

    def test_dry_run_reports_and_deletes_nothing(self):
        self.fx.build()
        before, outside = self.fx.names(), self.fx.outside()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fx.names(), before)
        self.assertEqual(self.fx.outside(), outside)
        out = result.stdout
        self.assertTrue(out.startswith(f"Root: {self.fx.root}\n"))
        for line in ("Locks: 4 eligible, 5 ineligible", "ineligible (held): 1", "ineligible (young): 1",
                     "ineligible (non-empty): 1", "Markers: 2 eligible, 7 kept", "kept (plan_dir exists): 1",
                     "kept (unparseable): 2", "kept (no path): 1", "kept (sibling lock held): 1",
                     "D4 temporary files (.d4-*): 1 (report only)", "Unknown entries: 4 (report only)",
                     "Total eligible: 6 files", "Dry run: nothing was deleted.", "stop every agent session"):
            self.assertIn(line, out)

    def test_yes_deletes_exactly_eligible_and_is_idempotent(self):
        self.fx.build()
        before, outside = self.fx.names(), self.fx.outside()
        result = self.run_cli("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fx.names(), before - Fixture.eligible)
        self.assertEqual(self.fx.outside(), outside)
        self.assertIn("Deleted: 6", result.stdout)
        self.assertIn("Skipped at deletion: 0", result.stdout)
        self.assertNotIn("stop every agent session", result.stdout)
        # The held lock, its marker, young files and present plans all survive.
        for name in (h(6) + ".lock", h(6) + ".command.json", h(4) + ".lock", h(13) + ".command.json",
                     "canonical-" + h(12) + ".lock.json", h(7) + ".lock"):
            self.assertTrue(os.path.lexists(self.fx.root / name), name)
        self.assertTrue(self.fx.existing_plan.is_dir())
        again = self.run_cli("--yes")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("Deleted: 0", again.stdout)
        self.assertEqual(self.fx.names(), before - Fixture.eligible)

    def test_released_lock_becomes_eligible_with_its_marker(self):
        self.fx.build()
        self.fx.release()
        code, out, _ = self.run_main("--yes")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.lexists(self.fx.root / (h(6) + ".lock")))
        self.assertFalse(os.path.lexists(self.fx.root / (h(6) + ".command.json")))
        self.assertIn("Deleted: 8", out)

    def test_reverify_keeps_files_changed_after_listing(self):
        self.fx.build()
        fd, report = self.scan()
        lock = self.fx.root / (h(1) + ".lock")
        lock.write_bytes(b"busy")
        os.utime(lock, (OLD, OLD))
        replaced = self.fx.root / ("authority-" + h(2) + ".lock")
        replaced.unlink()
        self.fx.file(replaced.name)
        marker = self.fx.root / (h(10) + ".command.json")
        self.fx.marker(marker.name, self.fx.existing_plan)
        self.fx.missing_plan.mkdir(parents=True)  # authority-h11.json's plan reappears.
        self.fx.hold("canonical-" + h(3) + ".lock")
        deleted, skipped = prune_state.apply(fd, report, 7)
        self.assertEqual([e.name for e in deleted], ["authority-" + h(11) + ".lock"])
        self.assertEqual({e.name: reason for e, reason in skipped}, {
            h(10) + ".command.json": "plan_dir exists",
            "authority-" + h(11) + ".json": "plan_dir exists",
            h(1) + ".lock": "non-empty",
            "authority-" + h(2) + ".lock": "changed",
            "canonical-" + h(3) + ".lock": "held"})
        for name in (lock.name, replaced.name, marker.name, "canonical-" + h(3) + ".lock"):
            self.assertTrue((self.fx.root / name).exists(), name)

    def test_cli_reverifies_between_listing_and_deletion(self):
        self.fx.build()
        real_scan = prune_state.scan
        target = self.fx.root / ("canonical-" + h(3) + ".lock")

        def scan_then_touch(*args, **kwargs):
            report = real_scan(*args, **kwargs)
            os.utime(target, None)  # Now young and a different mtime.
            return report

        with mock.patch.object(prune_state, "scan", scan_then_touch):
            code, out, _ = self.run_main("--yes")
        self.assertEqual(code, 0)
        self.assertTrue(target.exists())
        self.assertIn(f"{target}: young", out)
        self.assertIn("Deleted: 5", out)

    def test_marker_waits_for_its_sibling_lock(self):
        self.fx.marker("canonical-" + h(20) + ".lock.json", self.fx.missing_plan)
        self.fx.file("canonical-" + h(20) + ".lock")
        fd, report = self.scan()
        self.assertEqual({e.name for e in report.entries if e.eligible},
                         {"canonical-" + h(20) + ".lock.json", "canonical-" + h(20) + ".lock"})
        self.fx.hold("canonical-" + h(20) + ".lock")
        deleted, skipped = prune_state.apply(fd, report, 7)
        self.assertEqual(deleted, [])
        self.assertEqual(sorted(reason for _, reason in skipped), ["held", "sibling lock held"])
        self.assertEqual(len(self.fx.names()), 2)

    def test_min_age_days_threshold(self):
        self.fx.file(h(1) + ".lock", mtime=time.time() - 3 * 86400)
        code, out, _ = self.run_main("--min-age-days", "2", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Deleted: 1", out)
        self.fx.file(h(1) + ".lock", mtime=time.time() - 3 * 86400)
        code, out, _ = self.run_main("--yes")
        self.assertIn("Deleted: 0", out)
        for bad in ("0", "-1", "x"):
            self.assertEqual(self.run_main("--min-age-days", bad)[0], 2)

    def assert_refused(self, root, text):
        before = set(os.listdir(self.temp))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(root), "--yes"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(text, result.stderr)
        self.assertEqual(set(os.listdir(self.temp)), before)

    def test_refuses_unsafe_roots(self):
        self.fx.build()
        self.fx.release()
        before = self.fx.names()
        link = Path(self.temp) / "linked-root"
        os.symlink(self.fx.root, link)
        self.assert_refused(link, "symlink")
        self.assert_refused(Path(self.temp) / "missing", "does not exist")
        self.assert_refused(Path(self.temp) / "outside.txt", "not a directory")
        os.chmod(self.fx.root, 0o755)
        self.assert_refused(self.fx.root, "not 0700")
        os.chmod(self.fx.root, 0o700)
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            code, _, err = self.run_main("--yes")
        self.assertEqual(code, 2)
        self.assertIn("not owned by the current user", err)
        with mock.patch.object(prune_state, "fcntl", None):
            code, _, err = self.run_main("--yes")
        self.assertEqual(code, 2)
        self.assertIn("no fcntl.flock", err)
        self.assertEqual(self.fx.names(), before)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_permission_error_stops_the_run(self):
        locked = Path(self.temp) / "sealed"
        locked.mkdir()
        self.fx.marker(h(30) + ".command.json", locked / "inner/plan")
        self.fx.file(h(31) + ".lock")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o700)
        result = self.run_cli("--yes")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Prune stopped", result.stderr)
        self.assertIn("Permission denied", result.stderr)
        self.assertEqual(len(self.fx.names()), 2)


if __name__ == "__main__":
    unittest.main()
