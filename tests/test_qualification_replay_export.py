"""The canary admission export for the C.1 strict replay, with doubles: a real canary-shaped ledger built by the real
routing command on temporary stores, a read-only export, idempotence, owner-only files, the 24-hour refusal, and the
opencode_live.py wiring (refusal before anything starts, the 0-call export command, the export in cleanup)."""
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

from tests.qualification import canary, replay_export, safety
from tests.qualification import opencode_live as live
from tests.qualification.doubles import Temp
from tests.qualification.server import Routing


class ExportTests(Temp):
    def setUp(self):
        super().setUp()
        self.state = self.private(self.tmp / "canary")
        self.settings, self.ledger = self.state / "settings.json", self.state / "ledger.sqlite3"
        self.routing = Routing(self.settings, self.ledger)
        self.export_dir = self.tmp / "replay" / "canary"

    def admit(self, count, tag="t"):
        canary.initialize_state(self.routing)
        for item in canary.plan_assignments(count, tag):
            answer, _ = self.routing("prepare", **item["body"])
            self.assertEqual(answer.get("state"), "admitted", answer)
            attempt = item["body"]["assignment"]["attempt_id"]  # closed, so the concurrency cap admits the next
            closed, _ = self.routing("record", event=dict(event_id=attempt + "-nd", attempt_id=attempt, kind="not_dispatched"))
            self.assertEqual(closed.get("state"), "recorded", closed)

    def fingerprint(self):
        return {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for path in sorted(self.state.iterdir()) if path.is_file()}

    def test_export_is_read_only_verbatim_and_idempotent(self):
        self.admit(2)
        before = self.fingerprint()
        identity, records = replay_export.read_admissions(self.ledger)
        first = replay_export.export(self.ledger, self.export_dir, now=1000)
        self.assertEqual(self.fingerprint(), before)  # the live ledger (and its journal files) are untouched
        self.assertEqual((first["records_seen"], first["exported"]), (2, 2))
        lines = [json.loads(line) for line in (self.export_dir / replay_export.RECORDS).read_text().splitlines()]
        self.assertEqual([line["record"] for line in lines], records)  # verbatim, snapshot detail included
        self.assertTrue(all(line["ledger_identity"] == identity for line in lines))
        self.assertEqual(first["with_snapshot_detail"], sum(1 for r in records if all(k in r for k in replay_export.DETAIL)))
        again = replay_export.export(self.ledger, self.export_dir, now=2000)
        self.assertEqual(again["exported"], 0)
        self.assertEqual(len((self.export_dir / replay_export.RECORDS).read_text().splitlines()), 2)
        self.admit(1, tag="u")
        self.assertEqual(replay_export.export(self.ledger, self.export_dir, now=3000)["exported"], 1)
        self.assertEqual(oct(self.export_dir.stat().st_mode & 0o777), "0o700")
        for name in (replay_export.RECORDS, replay_export.LAST):
            self.assertEqual(oct((self.export_dir / name).stat().st_mode & 0o777), "0o600")

    def test_unsafe_export_directories_are_refused(self):
        target = self.private(self.tmp / "elsewhere")
        link = self.tmp / "link"
        link.symlink_to(target)
        loose = self.private(self.tmp / "loose", 0o755)
        repo = Path(__file__).resolve().parent / "replay-export-never"
        for path in (link, loose, repo, Path("relative")):
            with self.subTest(path=str(path)), self.assertRaises(safety.Refused):
                replay_export.check_dir(path)
        self.assertFalse(repo.exists())

    def test_a_new_canary_run_needs_an_export_within_24_hours(self):
        self.assertIsNone(replay_export.require_fresh(self.ledger, self.export_dir))  # no ledger yet
        canary.initialize_state(self.routing)
        self.assertIsNone(replay_export.require_fresh(self.ledger, self.export_dir))  # no admissions
        self.admit(1)
        with self.assertRaises(safety.Refused):
            replay_export.require_fresh(self.ledger, self.export_dir, now=1000)  # never exported
        replay_export.export(self.ledger, self.export_dir, now=1000)
        self.assertEqual(replay_export.require_fresh(self.ledger, self.export_dir, now=1000 + 24 * 3600)["at"], 1000)
        with self.assertRaises(safety.Refused):
            replay_export.require_fresh(self.ledger, self.export_dir, now=1001 + 24 * 3600)
        self.assertFalse((self.export_dir / "x").exists())

    def test_the_export_keeps_records_a_later_prune_drops(self):
        self.admit(1)
        replay_export.export(self.ledger, self.export_dir, now=1000)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.ledger)) as db:  # what the prune does to a closed record after 24 h
            data = json.loads(db.execute("SELECT body FROM ledger WHERE id=1").fetchone()[0])
            for record in data["admissions"]:
                record.pop("pressure", None)
                record.pop("snapshot_id", None)
            db.execute("UPDATE ledger SET body=? WHERE id=1", (json.dumps(data),))
            db.commit()
        self.assertNotIn("pressure", replay_export.read_admissions(self.ledger)[1][0])
        exported = json.loads((self.export_dir / replay_export.RECORDS).read_text().splitlines()[0])
        self.assertIn("pressure", exported["record"])


class CanaryWiringTests(Temp):
    def setUp(self):
        super().setUp()
        self.q = self.private(self.tmp / "q")
        self.ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        safety.CallLedger.initialize(self.ledger_path)
        self.canary_state = self.tmp / "canary"
        self.replay = self.tmp / "replay"

    def seed_canary(self):
        state = canary.check_canary_state(self.canary_state, home=self.home)
        routing = Routing(state["settings"], state["ledger"])
        canary.initialize_state(routing)
        answer, _ = routing("prepare", **canary.plan_assignments(1, "seed")[0]["body"])
        self.assertEqual(answer["state"], "admitted")
        return state

    def test_a_stale_export_refuses_the_canary_before_anything_starts(self):
        self.seed_canary()
        argv = ["--isolated-dir", str(self.q), "--call-ledger", str(self.ledger_path), "--canary", "--project",
                canary.ALLOWLIST, "--canary-state", str(self.canary_state), "--replay-dir", str(self.replay)]
        with mock.patch.object(canary, "check_project", lambda project, *a: project), \
                mock.patch.object(canary, "check_ancestor_configs", lambda project: None):
            self.assertEqual(live.main(argv), 2)
        self.assertEqual(list(self.q.iterdir()), [])
        export = ["--canary-state", str(self.canary_state), "--replay-dir", str(self.replay), "--export-canary-admissions"]
        self.assertEqual(live.main(export), 0)
        self.assertEqual(len((self.replay / replay_export.RECORDS).read_text().splitlines()), 1)

    def test_cleanup_exports_after_every_canary_run(self):
        state = self.seed_canary()
        project = self.private(self.tmp / "project")
        os.system("git -C %s init -q" % project)
        ledger = safety.CallLedger(self.ledger_path)
        run = live.Run(self.q, ledger, canary_project=str(project), canary_state=state)
        run.replay_dir = self.replay
        run.cleanup()
        self.assertEqual(run.results["replay_export"]["exported"], 1)
        self.assertTrue((self.replay / replay_export.LAST).exists())
