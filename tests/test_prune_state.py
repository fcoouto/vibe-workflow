import contextlib
import fcntl
import hashlib
import io
import json
import os
import shutil
import stat
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


ELIGIBLE_LOCKS = {h(1) + ".lock", "authority-" + h(2) + ".lock", "canonical-" + h(3) + ".lock"}
ELIGIBLE_MARKERS = {h(10) + ".command.json", "authority-" + h(11) + ".json", h(6) + ".command.json"}
PAIRED_LOCKS = {h(10) + ".lock", "authority-" + h(11) + ".lock", h(6) + ".lock"}


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
        self.sentinels = {"outside.txt": b"keep", h(99) + ".lock": b"", "outside-marker": b"{}"}
        for name, raw in self.sentinels.items():
            self.write(self.base / name, raw)

    def write(self, path, raw=b"", mtime=OLD):
        path.write_bytes(raw)
        os.chmod(path, 0o600)
        os.utime(path, (mtime, mtime))

    def file(self, name, raw=b"", mtime=OLD):
        self.write(self.root / name, raw, mtime)

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
        for name in ELIGIBLE_LOCKS | PAIRED_LOCKS | {h(19) + ".lock"}:
            self.file(name)
        self.file(h(4) + ".lock", mtime=time.time())
        self.file(h(5) + ".lock", b"x")
        os.symlink(self.base / (h(99) + ".lock"), self.root / (h(7) + ".lock"))
        (self.root / ("canonical-" + h(8) + ".lock")).mkdir()
        self.file(h(40) + ".lock")
        os.link(self.root / (h(40) + ".lock"), self.root / (h(41) + ".lock"))
        self.marker(h(10) + ".command.json", self.missing_plan)
        self.marker("authority-" + h(11) + ".json", self.missing_plan, manifest_digest="d")
        self.marker(h(6) + ".command.json", self.missing_plan)
        self.marker("canonical-" + h(12) + ".lock.json", self.existing_plan)
        self.marker(h(13) + ".command.json", self.missing_plan, mtime=time.time())
        self.file("authority-" + h(14) + ".json", b"{not json")
        self.file(h(15) + ".command.json", b"[1, 2]")
        self.marker("authority-" + h(16) + ".json", None)
        self.marker(h(17) + ".command.json", "relative/plan")
        self.marker(h(19) + ".command.json", str(self.missing_plan) + "/../plan")
        self.marker("canonical-" + h(21) + ".lock.json", self.missing_plan)
        self.marker(h(22) + ".command.json", self.missing_plan)
        self.file(h(22) + ".lock")
        os.link(self.root / (h(22) + ".command.json"), self.base / "linked-marker")
        self.file(".d4-abc", b"tmp")
        for name in ("notes.txt", h(18) + ".LOCK", h(1) + ".lock.bak", "authority-short.lock"):
            self.file(name)
        return self

    def names(self):
        return set(os.listdir(self.root))

    def snapshot(self, base=None):
        base = base or self.root
        result = {}
        for name in os.listdir(base):
            info = os.lstat(base / name)
            raw = (base / name).read_bytes() if stat.S_ISREG(info.st_mode) else None
            result[name] = (raw, info.st_mode, info.st_mtime_ns)
        return result

    def outside(self):
        return {name: (self.base / name).read_bytes() for name in self.sentinels}


class PruneStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="prune-state-test-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.fx = Fixture(self.temp)
        self.addCleanup(self.fx.release)
        self.run_dir = Path(self.temp) / "run"

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(self.fx.root), *args],
                              capture_output=True, text=True, timeout=30)

    def run_main(self, *args, run_dir=True):
        extra = ["--run-dir", str(self.run_dir)] if run_dir and "--yes" in args else []
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = prune_state.main(["--root", str(self.fx.root), *extra, *args])
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def scan(self):
        fd = prune_state.open_root(self.fx.root)
        self.addCleanup(os.close, fd)
        return fd, prune_state.scan(fd, self.fx.root, 7)

    def reasons(self, report, kind):
        result = {}
        for entry in report.select(kind):
            result[entry.reason] = result.get(entry.reason, 0) + 1
        return result

    def reason(self, report, name):
        return next(e.reason for e in report.entries if e.name == name)

    def manifest(self):
        return json.loads((self.run_dir / "manifest.json").read_text())

    def quarantine(self):
        fd = os.open(self.run_dir, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        qfd = os.open("quarantined", os.O_RDONLY, dir_fd=fd)
        self.addCleanup(os.close, qfd)
        return qfd, str(self.run_dir / "quarantined")

    def test_default_root_matches_production_lock_root(self):
        self.assertEqual(str(prune_state.DEFAULT_ROOT), str(Path.home()) + "/.local/share/vcw-transactions")
        source = (ROOT / "runtime/changes.py").read_text()
        self.assertIn('LOCK_ROOT = Path.home() / ".local/share/vcw-transactions"', source)

    def test_scan_classifies_every_file_exactly(self):
        self.fx.build()
        _, report = self.scan()
        self.assertEqual(self.reasons(report, "lock"), {
            "eligible": 3, "marker present": 5, "young": 1, "non-empty": 1, "symlink": 1,
            "not a regular file": 1, "hard-linked": 2})
        self.assertEqual(self.reasons(report, "marker"), {
            "eligible": 3, "plan_dir exists": 1, "young": 1, "unparseable": 2, "no path": 1,
            "invalid path": 2, "sibling lock missing": 1, "hard-linked": 1})
        self.assertEqual(self.reason(report, h(19) + ".command.json"), "invalid path")
        self.assertEqual(self.reason(report, h(41) + ".lock"), "hard-linked")
        self.assertEqual(self.reason(report, h(22) + ".command.json"), "hard-linked")
        self.assertEqual(len(report.select("temp")), 1)
        self.assertEqual(len(report.select("unknown")), 4)
        self.assertEqual(report.held, [])
        self.assertEqual({e.name for e in report.entries if e.eligible}, ELIGIBLE_LOCKS | ELIGIBLE_MARKERS)

    def test_held_locks_are_reported_and_block_yes(self):
        self.fx.build()
        self.fx.hold(h(6) + ".lock")
        self.fx.hold(h(4) + ".lock")  # A young lock is probed too.
        _, report = self.scan()
        self.assertEqual({e.name for e in report.held}, {h(6) + ".lock", h(4) + ".lock"})
        self.assertEqual(self.reason(report, h(6) + ".command.json"), "sibling lock held")
        before = self.fx.snapshot()
        dry = self.run_cli()
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("Held locks: 2; something is using this directory and --yes would refuse", dry.stdout)
        result = self.run_cli("--yes", "--markers", "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("something is using this directory", result.stderr)
        self.assertEqual(self.fx.snapshot(), before)
        self.assertFalse(self.run_dir.exists())

    def test_dry_run_reports_and_changes_nothing(self):
        self.fx.build()
        before, outside = self.fx.snapshot(), self.fx.outside()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fx.snapshot(), before)
        self.assertEqual(self.fx.outside(), outside)
        out = result.stdout
        self.assertTrue(out.startswith(f"Root: {self.fx.root}\n"))
        for line in ("Locks: 3 eligible, 11 ineligible", "ineligible (marker present): 5",
                     "ineligible (hard-linked): 2", "Markers: 3 eligible, 9 kept", "kept (sibling lock missing): 1",
                     "kept (invalid path): 2", "D4 temporary files (.d4-*): 1",
                     "unknown entries: 4", "Held locks: 0",
                     "Plan for --yes: delete 3 lock files; move 0 markers to quarantine (3 eligible; markers move "
                     "only with --markers)", "Changes: 3 of --max-delete 1000: within the cap",
                     "stop every agent session", "even a dry run", "mount every volume", "back up the directory",
                     "Dry run: nothing was changed."):
            self.assertIn(line, out)
        self.assertIn("move 3 markers to quarantine", self.run_cli("--markers").stdout)
        self.assertIn("over the cap; --yes would refuse", self.run_cli("--max-delete", "2").stdout)

    def test_yes_without_markers_never_touches_markers(self):
        self.fx.build()
        before, outside = self.fx.snapshot(), self.fx.outside()
        result = self.run_cli("--yes", "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.fx.snapshot()
        self.assertEqual(set(before) - set(after), ELIGIBLE_LOCKS)
        self.assertEqual(after, {name: value for name, value in before.items() if name not in ELIGIBLE_LOCKS})
        self.assertEqual(self.fx.outside(), outside)
        self.assertIn("Deleted locks: 3\nQuarantined markers: 0", result.stdout)
        self.assertNotIn(str(self.fx.root), result.stdout.split("Deleted locks:")[1])  # No per-file lines.
        self.assertEqual(os.listdir(self.run_dir / "quarantined"), [])
        manifest = self.manifest()
        self.assertEqual(manifest["state"], "complete")
        self.assertEqual({a["name"] for a in manifest["actions"]}, ELIGIBLE_LOCKS)
        self.assertEqual({a["action"] for a in manifest["actions"]}, {"unlink-lock"})

    def test_markers_are_quarantined_with_a_manifest_and_undo_restores_them(self):
        self.fx.build()
        before = self.fx.snapshot()
        code, out, _ = self.run_main("--yes", "--markers")
        self.assertEqual(code, 0)
        self.assertIn("Deleted locks: 3\nQuarantined markers: 3", out)
        self.assertEqual(self.fx.names(), set(before) - ELIGIBLE_LOCKS - ELIGIBLE_MARKERS)
        for path in (self.run_dir, self.run_dir / "quarantined"):
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.lstat(self.run_dir / "manifest.json").st_mode), 0o600)
        manifest = self.manifest()
        self.assertEqual(manifest["state"], "complete")
        moves = [a for a in manifest["actions"] if a["action"] == "quarantine-marker"]
        self.assertEqual({a["name"] for a in moves}, ELIGIBLE_MARKERS)
        for action in moves:
            raw, mode, mtime = before[action["name"]]
            moved = Path(action["quarantined_path"])
            self.assertEqual(action["status"], "moved")
            self.assertEqual(action["original_path"], str(self.fx.root / action["name"]))
            self.assertEqual(moved.parent, self.run_dir / "quarantined")
            self.assertEqual((action["size"], action["mtime_ns"]), (len(raw), mtime))
            self.assertEqual(action["sha256"], hashlib.sha256(moved.read_bytes()).hexdigest())
            self.assertEqual(action["content"], json.loads(raw))
            self.assertEqual(action["plan_dir"], str(self.fx.missing_plan))
        self.assertEqual(set(os.listdir(self.run_dir / "quarantined")), ELIGIBLE_MARKERS)  # No spare links left.
        for action in moves:
            self.assertEqual(os.lstat(action["quarantined_path"]).st_nlink, 1)
            os.rename(action["quarantined_path"], action["original_path"])
        deleted = {a["name"] for a in manifest["actions"] if a["action"] == "unlink-lock"}
        self.assertEqual(deleted, ELIGIBLE_LOCKS)
        self.assertTrue(all(before[name][0] == b"" for name in deleted))
        self.assertEqual(self.fx.snapshot(), {n: v for n, v in before.items() if n not in deleted})

    def test_runs_are_idempotent_and_paired_locks_wait_for_their_marker(self):
        self.fx.build()
        self.assertEqual(self.run_main("--yes", "--markers")[0], 0)
        self.run_dir = Path(self.temp) / "run2"
        code, out, _ = self.run_main("--yes", "--markers")
        self.assertIn("Deleted locks: 3\nQuarantined markers: 0", out)
        self.assertEqual({a["name"] for a in self.manifest()["actions"]}, PAIRED_LOCKS)
        self.assertTrue((self.fx.root / (h(19) + ".lock")).exists())
        self.run_dir = Path(self.temp) / "run3"
        code, out, _ = self.run_main("--yes", "--markers")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to change.", out)
        self.assertFalse(self.run_dir.exists())

    def test_max_delete_cap(self):
        self.fx.build()
        before = self.fx.snapshot()
        for args in (("--max-delete", "2"), ("--markers", "--max-delete", "5")):
            code, _, err = self.run_main("--yes", *args)
            self.assertEqual(code, 2)
            self.assertIn("more than --max-delete", err)
            self.assertEqual(self.fx.snapshot(), before)
            self.assertFalse(self.run_dir.exists())
        self.assertEqual(self.run_main("--yes", "--markers", "--max-delete", "6")[0], 0)
        self.assertEqual(len(self.manifest()["actions"]), 6)

    def test_reverify_keeps_files_changed_after_listing(self):
        self.fx.build()
        fd, report = self.scan()
        lock = self.fx.root / (h(1) + ".lock")
        lock.write_bytes(b"busy")
        os.utime(lock, (OLD, OLD))
        self.fx.hold("canonical-" + h(3) + ".lock")
        self.fx.missing_plan.mkdir(parents=True)  # Every eligible marker's plan reappears.
        os.mkdir(self.run_dir, 0o700)
        os.mkdir(self.run_dir / "quarantined", 0o700)
        done, skipped = prune_state.apply(fd, report, 7, str(self.fx.root), self.quarantine())
        self.assertEqual([a["name"] for a in done], ["authority-" + h(2) + ".lock"])
        expected = {name: "plan_dir exists" for name in ELIGIBLE_MARKERS}
        expected.update({lock.name: "non-empty", "canonical-" + h(3) + ".lock": "held"})
        self.assertEqual({e.name: reason for e, reason in skipped}, expected)

    def test_symlink_swap_between_scan_and_delete(self):
        self.fx.build()
        fd, report = self.scan()
        outside = self.fx.outside()
        for name, target in ((h(1) + ".lock", h(99) + ".lock"), (h(10) + ".command.json", "outside-marker")):
            (self.fx.root / name).unlink()
            os.symlink(self.fx.base / target, self.fx.root / name)
            os.utime(self.fx.root / name, (OLD, OLD), follow_symlinks=False)
        os.mkdir(self.run_dir, 0o700)
        os.mkdir(self.run_dir / "quarantined", 0o700)
        _, skipped = prune_state.apply(fd, report, 7, str(self.fx.root), self.quarantine())
        reasons = {e.name: reason for e, reason in skipped}
        self.assertEqual(reasons[h(1) + ".lock"], "symlink")
        self.assertEqual(reasons[h(10) + ".command.json"], "symlink")
        self.assertTrue((self.fx.root / (h(1) + ".lock")).is_symlink())
        self.assertTrue((self.fx.root / (h(10) + ".command.json")).is_symlink())
        self.assertEqual(self.fx.outside(), outside)

    def swap_at_final_check(self, name):
        def swap(current):
            if current == name:
                path = self.fx.root / name
                raw = path.read_bytes()
                path.unlink()
                self.fx.file(name, raw)  # Same bytes and mtime, new inode.
        return mock.patch.object(prune_state, "_before_final_check", swap)

    def test_final_check_keeps_a_lock_replaced_while_locked(self):
        self.fx.build()
        with self.swap_at_final_check(h(1) + ".lock"):
            code, out, _ = self.run_main("--yes")
        self.assertEqual(code, 0)
        self.assertTrue((self.fx.root / (h(1) + ".lock")).exists())
        self.assertEqual(self.manifest()["skipped"], [dict(name=h(1) + ".lock", reason="changed")])

    def test_final_check_keeps_a_marker_replaced_while_locked(self):
        self.fx.build()
        name = h(10) + ".command.json"
        with self.swap_at_final_check(name):
            code, out, _ = self.run_main("--yes", "--markers")
        self.assertEqual(code, 0)
        self.assertTrue((self.fx.root / name).exists())
        self.assertFalse((self.run_dir / "quarantined" / name).exists())
        self.assertEqual(self.manifest()["skipped"], [dict(name=name, reason="changed")])

    def test_sibling_lock_taken_after_scan_keeps_the_marker(self):
        self.fx.build()
        fd, report = self.scan()
        self.fx.hold(h(10) + ".lock")
        os.mkdir(self.run_dir, 0o700)
        os.mkdir(self.run_dir / "quarantined", 0o700)
        _, skipped = prune_state.apply(fd, report, 7, str(self.fx.root), self.quarantine())
        self.assertIn(("sibling lock held"), {r for e, r in skipped if e.name == h(10) + ".command.json"})
        self.assertTrue((self.fx.root / (h(10) + ".command.json")).exists())

    def test_foreign_owner_is_kept(self):
        self.fx.build()
        fd = prune_state.open_root(self.fx.root)
        self.addCleanup(os.close, fd)
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            report = prune_state.scan(fd, self.fx.root, 7)
        self.assertEqual(self.reason(report, h(1) + ".lock"), "foreign owner")
        self.assertEqual(self.reason(report, h(10) + ".command.json"), "foreign owner")
        self.assertEqual([e for e in report.entries if e.eligible], [])

    def test_min_age_days_threshold(self):
        self.fx.file(h(1) + ".lock", mtime=time.time() - 3 * 86400)
        self.assertIn("Nothing to change.", self.run_main("--yes")[1])
        code, out, _ = self.run_main("--min-age-days", "2", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Deleted locks: 1", out)
        for bad in ("0", "-1", "x"):
            self.assertEqual(self.run_main("--min-age-days", bad)[0], 2)
        self.assertEqual(self.run_main("--max-delete", "-1")[0], 2)

    def assert_refused(self, args, text):
        before = set(os.listdir(self.temp))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args), "--yes"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(text, result.stderr)
        self.assertEqual(set(os.listdir(self.temp)) - {"run"}, before - {"run"})

    def test_refuses_unsafe_roots(self):
        self.fx.build()
        before = self.fx.snapshot()
        link = Path(self.temp) / "linked-root"
        os.symlink(self.fx.root, link)
        self.assert_refused(["--root", link], "symlink")
        self.assert_refused(["--root", Path(self.temp) / "missing"], "does not exist")
        self.assert_refused(["--root", Path(self.temp) / "outside.txt"], "not a directory")
        os.chmod(self.fx.root, 0o755)
        self.assert_refused(["--root", self.fx.root], "not 0700")
        os.chmod(self.fx.root, 0o700)
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            code, _, err = self.run_main("--yes")
        self.assertEqual(code, 2)
        self.assertIn("not owned by the current user", err)
        with mock.patch.object(prune_state, "fcntl", None):
            code, _, err = self.run_main("--yes")
        self.assertEqual(code, 2)
        self.assertIn("no fcntl.flock", err)
        self.assertEqual(self.fx.snapshot(), before)

    def test_refuses_unsafe_run_dirs(self):
        self.fx.build()
        before = self.fx.snapshot()
        root = ["--root", self.fx.root, "--markers", "--run-dir"]
        self.assert_refused(root + [self.fx.root / "inside"], "inside the root")
        os.symlink(self.fx.existing_plan, Path(self.temp) / "linked-run")
        self.assert_refused(root + [Path(self.temp) / "linked-run"], "symlink")
        loose = Path(self.temp) / "loose"
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        self.assert_refused(root + [loose], "not 0700")
        busy = Path(self.temp) / "busy"
        busy.mkdir(mode=0o700)
        (busy / "manifest.json").write_text("{}")
        self.assert_refused(root + [busy], "not empty")
        self.assertEqual(self.fx.snapshot(), before)

    def test_default_run_dir_is_next_to_the_root(self):
        path = prune_state.default_run_dir(self.fx.root)
        self.assertEqual(path.parent, self.fx.root.parent)
        self.assertRegex(path.name, r"^vcw-transactions-prune-\d{8}T\d{6}Z$")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_inaccessible_plan_dir_keeps_the_marker_and_continues(self):
        sealed = Path(self.temp) / "sealed"
        sealed.mkdir()
        self.fx.marker(h(30) + ".command.json", sealed / "inner/plan")
        self.fx.file(h(30) + ".lock")
        self.fx.marker(h(32) + ".command.json", self.fx.missing_plan)
        self.fx.file(h(32) + ".lock")
        self.fx.file(h(31) + ".lock")
        os.chmod(sealed, 0)
        self.addCleanup(os.chmod, sealed, 0o700)
        result = self.run_cli("--yes", "--markers", "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kept (plan_dir not accessible): 1", result.stdout)
        self.assertEqual(self.fx.names(), {h(30) + ".command.json", h(30) + ".lock", h(32) + ".lock"})

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_unexpected_permission_error_stops_with_exit_1(self):
        self.fx.file(h(1) + ".lock")
        sealed = Path(self.temp) / "sealed"
        sealed.mkdir(mode=0o500)
        os.chmod(sealed, 0o500)
        self.addCleanup(os.chmod, sealed, 0o700)
        result = self.run_cli("--yes", "--run-dir", str(sealed / "run"))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("Prune stopped", result.stderr)
        self.assertIn("Permission denied", result.stderr)
        self.assertEqual(self.fx.names(), {h(1) + ".lock"})

    def test_error_during_apply_records_a_stopped_manifest(self):
        self.fx.build()
        with mock.patch.object(prune_state, "_delete_lock", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.run_main("--yes", "--markers")
        manifest = self.manifest()
        self.assertEqual(manifest["state"], "stopped")
        self.assertEqual({a["name"] for a in manifest["actions"]}, ELIGIBLE_MARKERS)
        self.assertEqual(len(manifest["planned"]), 6)


    def quarantine_with_collision(self):
        self.fx.build()
        fd, report = self.scan()
        os.mkdir(self.run_dir, 0o700)
        os.mkdir(self.run_dir / "quarantined", 0o700)
        name = h(10) + ".command.json"
        self.fx.write(self.run_dir / "quarantined" / name, b"pre-placed")
        before = (self.fx.root / name).read_bytes()
        return fd, report, name, before

    def assert_collision_kept(self, name, before, skipped, reason):
        self.assertEqual({e.name: r for e, r in skipped}.get(name), reason)
        self.assertEqual((self.fx.root / name).read_bytes(), before)
        self.assertEqual(os.lstat(self.fx.root / name).st_nlink, 1)
        self.assertEqual((self.run_dir / "quarantined" / name).read_bytes(), b"pre-placed")

    def test_quarantine_collision_keeps_marker_and_existing_file(self):
        fd, report, name, before = self.quarantine_with_collision()
        _, skipped = prune_state.apply(fd, report, 7, str(self.fx.root), self.quarantine())
        self.assert_collision_kept(name, before, skipped, "quarantine name taken")

    def test_move_never_replaces_a_quarantined_file(self):
        fd, report, name, before = self.quarantine_with_collision()
        quarantine = self.quarantine()
        real = prune_state._exists
        with mock.patch.object(prune_state, "_exists", lambda d, n: False if d == quarantine[0] else real(d, n)):
            _, skipped = prune_state.apply(fd, report, 7, str(self.fx.root), quarantine)
        self.assert_collision_kept(name, before, skipped, "quarantine name taken at link")

    def test_marker_reappearing_before_unlink_keeps_the_lock(self):
        self.fx.build()
        fd, report = self.scan()
        self.fx.marker(h(1) + ".command.json", self.fx.missing_plan)
        done, skipped = prune_state.apply(fd, report, 7, str(self.fx.root))
        self.assertEqual({e.name: r for e, r in skipped}, {h(1) + ".lock": "marker present"})
        self.assertTrue((self.fx.root / (h(1) + ".lock")).exists())
        self.assertEqual(len(done), 2)

    def test_interrupted_run_leaves_a_planned_manifest_for_hand_recovery(self):
        self.fx.build()
        before = self.fx.snapshot()
        real_link, real_rename, calls = os.link, os.rename, dict(link=0, manifest=0)

        def link(*args, **kwargs):
            calls["link"] += 1
            if calls["link"] > 1:
                raise OSError(5, "simulated crash after the first move")
            return real_link(*args, **kwargs)

        def rename(src, dst, **kwargs):
            if dst == "manifest.json":
                calls["manifest"] += 1
                if calls["manifest"] > 1:
                    raise OSError(28, "simulated full disk")
            return real_rename(src, dst, **kwargs)

        out = io.StringIO()
        with mock.patch("os.link", link), mock.patch("os.rename", rename), contextlib.redirect_stdout(out):
            with self.assertRaises(OSError):
                prune_state.main(["--root", str(self.fx.root), "--run-dir", str(self.run_dir), "--yes", "--markers"])
        manifest = self.manifest()
        self.assertEqual(manifest["state"], "planned")
        planned = {p["name"]: p for p in manifest["planned"] if p["action"] == "quarantine-marker"}
        self.assertEqual(set(planned), ELIGIBLE_MARKERS)
        for name, entry in planned.items():
            self.assertEqual(entry["content"], json.loads(before[name][0]))
        moved = os.listdir(self.run_dir / "quarantined")
        self.assertEqual(len(moved), 1)
        self.assertIn(moved[0], planned)
        self.assertEqual([n for n in os.listdir(self.run_dir) if n.startswith(".manifest-")], [])
        self.assertIn("may remain 'planned'", out.getvalue())
        self.assertIn(f"Run directory: {self.run_dir}", out.getvalue())
        for name in planned:  # Hand recovery from the planned list.
            if (self.run_dir / "quarantined" / name).exists():
                os.rename(self.run_dir / "quarantined" / name, self.fx.root / name)
        self.assertEqual(self.fx.snapshot(), before)

    def test_failed_manifest_write_removes_its_temporary_file(self):
        self.fx.file(h(1) + ".lock")
        os.mkdir(self.run_dir, 0o700)
        fd = os.open(self.run_dir, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with mock.patch("os.fsync", side_effect=OSError(5, "simulated I/O error")):
            with self.assertRaises(OSError):
                prune_state.write_manifest(fd, {})
        self.assertEqual(os.listdir(self.run_dir), [])

    def test_second_prune_on_the_same_root_refuses(self):
        self.fx.build()
        before = self.fx.snapshot()
        guard = os.open(self.fx.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, guard)
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)  # Stands in for a running prune.
        result = self.run_cli("--yes", "--run-dir", str(self.run_dir))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("Another prune is running", result.stderr)
        self.assertEqual(self.run_cli().returncode, 0)  # A dry run takes no guard.
        self.assertEqual(self.fx.snapshot(), before)
        self.assertFalse(self.run_dir.exists())

    def test_unreadable_plan_dir_keeps_the_marker_and_continues(self):
        os.symlink("loop", Path(self.temp) / "loop")
        for number, plan in ((50, Path(self.temp) / ("x" * 300) / "plan"), (51, Path(self.temp) / "loop/plan")):
            self.fx.marker(h(number) + ".command.json", plan)
            self.fx.file(h(number) + ".lock")
        self.fx.file(h(1) + ".lock")
        code, out, _ = self.run_main("--yes", "--markers")
        self.assertEqual(code, 0)
        self.assertIn("kept (plan_dir unreadable): 2", out)
        self.assertIn("Deleted locks: 1", out)
        self.assertEqual(len(self.fx.names()), 4)


if __name__ == "__main__":
    unittest.main()
