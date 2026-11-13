"""Advisory historical replay over session metadata (validation Edit 6, labels amended by Edit 7, store fields
registered by Edit 9 and its supplement). Only disposable synthetic stores built with the hosts' own table schemas
are opened here; no real store, history, pilot record, model or network is touched."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

from runtime import (routing, routing_evidence, routing_launch, routing_observations, routing_paths,
                     subscription_billing)
from tests.qualification import replay_history as replay
from tests.qualification.safety import Refused

NOW_MS = 1_794_470_000_000
HOUR_MS = 3_600_000
ROOT = "/work/vibe-coding-workflow"
OPUS = "/work/opus"

# The column lists of the inspected schemas (Edit 9), so a synthetic store has the production shape.
OPENCODE_SCHEMA = """
CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT NOT NULL, vcs TEXT, name TEXT, icon_url TEXT,
  icon_url_override TEXT, icon_color TEXT, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
  time_initialized INTEGER, time_active INTEGER NOT NULL, time_archived INTEGER, sandboxes TEXT NOT NULL, commands TEXT);
CREATE TABLE project_directory (project_id TEXT NOT NULL, directory TEXT NOT NULL, type TEXT, strategy TEXT,
  time_created INTEGER NOT NULL, PRIMARY KEY (project_id, directory));
CREATE TABLE session_v2 (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT, parent_id TEXT,
  fork_session_id TEXT, fork_boundary TEXT, slug TEXT NOT NULL, directory TEXT NOT NULL, path TEXT, title TEXT,
  version TEXT NOT NULL, share_url TEXT, summary_additions INTEGER, summary_deletions INTEGER, summary_files INTEGER,
  summary_diffs TEXT, metadata TEXT, cost REAL NOT NULL, tokens_input INTEGER NOT NULL, tokens_output INTEGER NOT NULL,
  tokens_reasoning INTEGER NOT NULL, tokens_cache_read INTEGER NOT NULL, tokens_cache_write INTEGER NOT NULL,
  revert TEXT, permission TEXT, agent TEXT, model TEXT, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL,
  time_busy INTEGER, time_idle INTEGER, time_viewed INTEGER, idle_outcome TEXT, time_compacting INTEGER,
  time_archived INTEGER, time_suspended INTEGER, resume_attempts INTEGER NOT NULL);
CREATE TABLE credential (id TEXT PRIMARY KEY, integration_id TEXT, label TEXT NOT NULL, value TEXT NOT NULL,
  connector_id TEXT, method_id TEXT, active INTEGER, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL);
"""
CODEX_SCHEMA = """
CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL, source TEXT NOT NULL, model_provider TEXT NOT NULL, cwd TEXT NOT NULL,
  title TEXT NOT NULL, sandbox_policy TEXT NOT NULL, approval_mode TEXT NOT NULL, tokens_used INTEGER NOT NULL,
  has_user_event INTEGER NOT NULL, archived INTEGER NOT NULL, archived_at INTEGER, git_sha TEXT, git_branch TEXT,
  git_origin_url TEXT, cli_version TEXT NOT NULL, first_user_message TEXT NOT NULL, agent_nickname TEXT,
  agent_role TEXT, memory_mode TEXT NOT NULL, model TEXT, reasoning_effort TEXT, agent_path TEXT,
  created_at_ms INTEGER, updated_at_ms INTEGER, thread_source TEXT, preview TEXT NOT NULL, recency_at INTEGER NOT NULL,
  recency_at_ms INTEGER NOT NULL, history_mode TEXT NOT NULL, name TEXT, is_pinned INTEGER NOT NULL,
  thread_section_id TEXT, section_position INTEGER, section_entered_at_ms INTEGER, project_id TEXT, originator TEXT,
  daybreak_enabled BOOLEAN, creator_user_id TEXT, creator_account_id TEXT, attached_thread_parent_id TEXT);
CREATE TABLE thread_spawn_edges (parent_thread_id TEXT NOT NULL, child_thread_id TEXT NOT NULL PRIMARY KEY,
  status TEXT NOT NULL);
"""
SECRET = "SECRET-PROMPT-TEXT"


def codex_store(path, threads, edges=()):
    with closing(sqlite3.connect(path)) as db:
        db.executescript(CODEX_SCHEMA)
        for thread in threads:
            row = dict(rollout_path="/rollouts/x.jsonl", created_at=0, updated_at=0, source="exec",
                       model_provider="openai", cwd=ROOT, title="", sandbox_policy="{}", approval_mode="never",
                       tokens_used=99, has_user_event=1, archived=0, cli_version="0.1", first_user_message=SECRET,
                       memory_mode="off", model="gpt-5.5", reasoning_effort="high", preview=SECRET, recency_at=0,
                       recency_at_ms=0, history_mode="full", is_pinned=0, creator_account_id="acct-secret")
            row.update(thread)
            db.execute("INSERT INTO threads (%s) VALUES (%s)" % (",".join(row), ",".join("?" * len(row))),
                       tuple(row.values()))
        db.executemany("INSERT INTO thread_spawn_edges VALUES (?, ?, 'done')", edges)
        db.commit()
    return path


def opencode_store(path, sessions):
    with closing(sqlite3.connect(path)) as db:
        db.executescript(OPENCODE_SCHEMA)
        db.execute("INSERT INTO project VALUES ('p1', ?, 'git', 'x', NULL, NULL, NULL, 0, 0, NULL, 0, NULL, '[]', NULL)",
                   (ROOT,))
        db.execute("INSERT INTO credential VALUES ('c', NULL, 'l', 'credential-secret', NULL, NULL, 1, 0, 0)")
        for session in sessions:
            row = dict(project_id="p1", slug="s", directory=ROOT, version="2", cost=0, tokens_input=1,
                       tokens_output=10, tokens_reasoning=5, tokens_cache_read=0, tokens_cache_write=0,
                       resume_attempts=0, agent="general", model='{"providerID":"x","modelID":"y"}',
                       time_created=NOW_MS - 5 * HOUR_MS, time_updated=NOW_MS - 4 * HOUR_MS, title=SECRET)
            row.update(session)
            db.execute("INSERT INTO session_v2 (%s) VALUES (%s)" % (",".join(row), ",".join("?" * len(row))),
                       tuple(row.values()))
        db.commit()
    return path


def child(thread_id, **extra):
    row = dict(id=thread_id, created_at_ms=NOW_MS - 5 * HOUR_MS, updated_at_ms=NOW_MS - 4 * HOUR_MS,
               agent_role="worker", title="Implement the parser")
    row.update(extra)
    return row


BASIS = dict(ids=["qual-1"], sources=["synthetic fixture"], checked="synthetic")


class ReplayHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def codex(self, threads, edges=(), name="codex.sqlite"):
        return dict(name="codex", kind="codex", path=str(codex_store(str(self.folder / name), threads, edges)),
                    roots=[ROOT])

    def test_eligibility_and_each_exclusion_is_counted(self):
        threads = [child("t-ok"), child("t-attached", attached_thread_parent_id="p"),
                   child("t-root"),  # no parent at all: a coordinator or interactive thread
                   child("t-nomodel", model=None), child("t-nostart", created_at_ms=None),
                   child("t-running", updated_at_ms=NOW_MS - HOUR_MS + 1),
                   child("t-other", cwd="/elsewhere")]  # outside the registered checkouts: not a store session
        edges = [("p", "t-ok"), ("p", "t-nomodel"), ("p", "t-nostart"), ("p", "t-running")]
        found = replay.collect([self.codex(threads, edges)], now_ms=NOW_MS)
        self.assertEqual(found["sessions_found"], 6)
        self.assertEqual(found["excluded"], dict(no_parent=1, no_model_id=1, no_timestamp=1, still_running=1,
                                                 host_marked_title_compaction_summary=0, duplicate=0))
        self.assertEqual(sorted(row["id"] for row in found["eligible"]), ["t-attached", "t-ok"])
        overlapping = dict(self.codex([child("t")], [("p", "t")], "overlap.sqlite"), roots=["/work", ROOT])
        found = replay.collect([overlapping], now_ms=NOW_MS)  # one row read through two roots is one session
        self.assertEqual((found["sessions_found"], found["excluded"]["duplicate"]), (1, 0))

    def test_legacy_second_timestamps_follow_codex_source(self):
        seconds = (NOW_MS - 5 * HOUR_MS) // 1000
        found = replay.collect([self.codex([child("t", created_at_ms=seconds)], [("p", "t")])], now_ms=NOW_MS)
        self.assertEqual(found["eligible"][0]["start_ms"], seconds * 1000)

    def test_opencode_model_json_and_millisecond_timestamps_follow_the_supplement(self):
        sessions = [dict(id="s1", parent_id="root", agent="code-reviewer",
                         model='{"id":"claude-opus","providerID":"claude-agent","variant":"high"}'),
                    dict(id="s2", parent_id="root", model='{"providerID":"openai"}'),  # no model id
                    dict(id="s3", parent_id="root", model="not json"),
                    dict(id="s4"),  # no parent
                    dict(id="s5", parent_id="root", project_id="other")]  # another project: not this store's
        path = opencode_store(str(self.folder / "opencode.db"), sessions)
        with closing(sqlite3.connect(path)) as db:
            db.execute("INSERT INTO project VALUES ('other', '/x', 'git', 'x', NULL, NULL, NULL, 0, 0, NULL, 0, NULL, "
                       "'[]', NULL)")
            db.commit()
        found = replay.collect([dict(name="opencode", kind="opencode", path=path, roots=[ROOT])], now_ms=NOW_MS)
        self.assertEqual(found["sessions_found"], 4)
        self.assertEqual(found["excluded"]["no_parent"], 1)
        self.assertEqual(found["excluded"]["no_model_id"], 2)
        (row,) = found["eligible"]
        self.assertEqual((row["provider"], row["model"], row["effort"]), ("claude-agent", "claude-opus", "high"))
        self.assertEqual((row["start_ms"], row["end_ms"]), (NOW_MS - 5 * HOUR_MS, NOW_MS - 4 * HOUR_MS))
        labels = replay.label(row)
        self.assertEqual(labels["funding_class"], "included_subscription")
        self.assertEqual(labels["specialist"], "code-reviewer")
        # Whether stored output includes reasoning stays unproved for historical rows: duration alone decides.
        self.assertEqual(labels["complexity_band"], "critical")

    def test_opencode_shared_project_id_does_not_widen_approved_checkout_roots(self):
        current = "/work/vibe-workflow"
        rows = [dict(id="current", parent_id="r", directory=current),
                dict(id="nested", parent_id="r", directory=current + "/src"),
                dict(id="legacy", parent_id="r", directory=ROOT),
                dict(id="outside", parent_id="r", directory=OPUS),
                dict(id="prefix-only", parent_id="r", directory=current + "-other")]
        path = opencode_store(str(self.folder / "shared.db"),
                              [dict(row, model='{"id":"synthetic-model","providerID":"test"}') for row in rows])
        with closing(sqlite3.connect(path)) as db:
            for directory in (current, OPUS):
                db.execute("INSERT INTO project_directory VALUES ('p1', ?, 'worktree', NULL, 0)", (directory,))
            db.commit()
        real = replay._connect

        def checked(target):
            db = real(target)

            def projected(cursor, row):
                self.assertNotIn(row[0], ("outside", "prefix-only"), "out-of-scope row reached the reader")
                return row

            db.row_factory = projected
            return db

        store = dict(name="opencode", kind="opencode", path=path, roots=[current, ROOT])
        with mock.patch.object(replay, "_connect", checked):
            found = replay.collect([store], now_ms=NOW_MS)
        self.assertEqual(found["sessions_found"], 3)
        self.assertEqual(sorted(row["id"] for row in found["eligible"]), ["current", "legacy", "nested"])

    def test_labels_follow_the_registered_rules(self):
        def label(**row):
            base = dict(store="codex", kind="codex", agent=None, title="", provider="openai", model="m",
                        effort="high", start_ms=0, end_ms=0, output=None, reasoning=None)
            return replay.label(dict(base, **row))
        self.assertEqual(label(agent="review-explorer")["specialist"], "code-reviewer")
        self.assertEqual(label(agent="explorer")["specialist"], "explorer-agent")
        self.assertEqual(label(agent="repair")["specialist"], "software-engineer")
        self.assertEqual(label(agent="general", title="Survey; then fix")["specialist"], "explorer-agent")
        self.assertEqual(label(agent="worker", title="Implement the parser")["specialist"], "software-engineer")
        self.assertEqual(label(agent="worker", title="Plan things")["specialist"], "unknown")
        self.assertEqual(label(agent="reviewer")["work_kind"], "review")
        self.assertEqual(label(agent="explorer")["work_kind"], "investigation")
        self.assertEqual(label(agent="x")["work_kind"], "unknown")
        # Duration bands: under 5 min low, 5 to under 20 medium, 20 to under 60 high, 60 or more critical.
        for minutes, band in ((0, "low"), (5, "medium"), (19.99, "medium"), (20, "high"), (60, "critical")):
            self.assertEqual(label(end_ms=int(minutes * 60_000))["complexity_band"], band)
        self.assertEqual(label(end_ms=None)["complexity_band"], "unknown")
        # Generated tokens: the higher band wins; reasoning added once only where output excludes it.
        self.assertEqual(label(output=4_000, reasoning=1_000, output_includes_reasoning=False)["complexity_band"],
                         "medium")
        self.assertEqual(label(output=4_000, reasoning=1_000, output_includes_reasoning=True)["complexity_band"],
                         "low")
        self.assertEqual(label(output=4_000, reasoning=1_000, output_includes_reasoning=None)["complexity_band"],
                         "low")  # unproved inclusion: the token band is unknown, duration decides
        self.assertEqual(label(output=60_000, end_ms=None, output_includes_reasoning=True)["complexity_band"],
                         "critical")
        self.assertEqual(label()["risk_band"], "unknown")
        self.assertEqual(label(provider="openrouter")["funding_class"], "dollar")
        self.assertEqual(label(kind="opencode", provider="claude-agent")["funding_class"], "included_subscription")
        self.assertEqual(label(kind="opencode", provider="openai")["funding_class"], "unknown")
        self.assertEqual(label()["funding_class"], "unknown")  # a Codex thread records no auth mode
        self.assertEqual(label(effort=None)["route"], dict(model="m", effort="unknown"))
        self.assertNotIn("title", label(title="Implement x"))

    def test_unknown_bands_take_the_routers_floors(self):
        profile = replay.profile(dict(specialist="software-engineer", complexity_band="unknown", risk_band="unknown"))
        self.assertEqual((profile["complexity_band"], profile["risk_band"]),
                         (routing.FLOORS["complexity_band"], routing.FLOORS["risk_band"]))
        self.assertEqual(profile["provenance"]["complexity_band"], "floor")
        low = replay.profile(dict(specialist="code-reviewer", complexity_band="low", risk_band="unknown"))
        self.assertEqual((low["work_kind"], low["complexity_band"], low["risk_band"]), ("review", "low", "high"))
        self.assertIsNone(replay.profile(dict(specialist="unknown", complexity_band="low", risk_band="unknown")))

    def test_negative_duration_is_unknown_without_discarding_a_known_token_band(self):
        row = dict(store="opencode", kind="opencode", agent="reviewer", title="", model="m",
                   start_ms=10, end_ms=9, output=None, reasoning=None, output_includes_reasoning=None)
        labels = replay.label(row)
        self.assertEqual(labels["complexity_band"], "unknown")
        self.assertEqual(replay.profile(labels)["complexity_band"], "high")
        self.assertEqual(replay.label(dict(row, end_ms=10))["complexity_band"], "low")
        self.assertEqual(replay.label(dict(row, output=20_000, output_includes_reasoning=True))["complexity_band"],
                         "high")

    def test_a_session_seen_twice_counts_once(self):
        first = self.codex([child("dup", created_at_ms=NOW_MS - 6 * HOUR_MS)], [("p", "dup")], "b.sqlite")
        second = self.codex([child("dup", agent_role="reviewer")], [("p", "dup")], "a.sqlite")
        found = replay.collect([second, first], now_ms=NOW_MS)
        self.assertEqual(found["excluded"]["duplicate"], 1)
        self.assertEqual([row["path"] for row in found["eligible"]], [first["path"]])  # earliest start wins
        tie = self.codex([child("dup")], [("p", "dup")], "c.sqlite")
        found = replay.collect([tie, second], now_ms=NOW_MS)  # equal starts and store names: path order decides
        self.assertEqual([row["path"] for row in found["eligible"]], [second["path"]])
        named = dict(self.codex([child("dup")], [("p", "dup")], "z.sqlite"), name="a-store")
        found = replay.collect([second, named], now_ms=NOW_MS)  # equal starts: store name before path
        self.assertEqual([row["path"] for row in found["eligible"]], [named["path"]])

    def test_a_qualification_session_id_blocks_the_report(self):
        store = self.codex([child("qual-1"), child("t")], [("p", "qual-1"), ("p", "t")])
        with self.assertRaisesRegex(Refused, "qualification_session_found"):
            replay.gather([store], {"qual-1"}, now_ms=NOW_MS)  # the authorized read `run` uses after its gates
        self.assertEqual(replay.gather([store], {"other"}, now_ms=NOW_MS)["sessions_found"], 2)
        with self.assertRaisesRegex(Refused, "exclusion_basis_missing"):
            replay.run([store], now_ms=NOW_MS)
        with self.assertRaisesRegex(Refused, "exclusion_basis_missing"):
            replay.run([store], exclusions=dict(ids=[], sources=["x"], checked="x"), now_ms=NOW_MS)

    def test_registration_gates_refuse_before_any_store_is_opened(self):
        codex = self.codex([child("t")], [("p", "t")])
        opencode = dict(name="opencode", kind="opencode", roots=[ROOT],
                        path=opencode_store(str(self.folder / "o.db"), [dict(id="s1", parent_id="r")]))
        fake_opus = dict(name="opus", kind="codex", path=codex["path"], roots=[OPUS])  # excluded by Edit 10
        foreign = dict(name="other", kind="codex", path=codex["path"], roots=[ROOT])
        forbidden = AssertionError("a store was read before the registration gates")
        cases = (([], None, "exclusion_basis_missing"), ([codex], None, "exclusion_basis_missing"),
                 ([], BASIS, "store_missing: opencode, codex"), ([codex], BASIS, "store_missing: opencode"),
                 ([opencode], BASIS, "store_missing: codex"), ([opencode, codex], None, "exclusion_basis_missing"),
                 ([opencode, codex, fake_opus], BASIS, "store_unregistered"),
                 ([fake_opus], BASIS, "store_unregistered"),
                 ([opencode, dict(codex, kind="opencode")], BASIS, "store_unregistered"),
                 ([opencode, codex, {}], BASIS, "store_unregistered"),
                 ([opencode, codex, foreign], BASIS, "store_unregistered"))
        for stores, basis, reason in cases:
            with self.subTest(reason=reason, stores=[store.get("name") for store in stores]), \
                    mock.patch.object(replay, "collect", side_effect=forbidden), \
                    mock.patch.object(replay, "gather", side_effect=forbidden), \
                    mock.patch.object(replay.sqlite3, "connect", side_effect=forbidden), \
                    mock.patch.object(replay, "label", side_effect=forbidden):
                with self.assertRaisesRegex(Refused, "^" + reason):
                    replay.run(stores, exclusions=basis, now_ms=NOW_MS)

    def test_the_observed_schema_is_bound_by_digest(self):
        first = self.codex([child("t")], [("p", "t")], "one.sqlite")
        second = self.codex([child("u")], [("p", "u")], "two.sqlite")
        digests = [replay.collect([store], now_ms=NOW_MS)["schemas"] for store in (first, second)]
        self.assertEqual(digests[0], digests[1])  # the schema, not the rows or the path, is bound
        self.assertRegex(digests[0]["codex"][0], "^[0-9a-f]{64}$")
        with closing(sqlite3.connect(second["path"])) as db:
            db.execute("ALTER TABLE threads ADD COLUMN later TEXT")  # an unmapped change: still readable
            db.commit()
        changed = replay.collect([second], now_ms=NOW_MS)["schemas"]
        self.assertNotEqual(changed, digests[0])
        self.assertNotIn(second["path"], repr(changed))

    def test_disagreement_is_aggregated_with_pending_and_unknowns(self):
        rows = [dict(store="codex", specialist="software-engineer", work_kind="implementation",
                     complexity_band="low", risk_band="unknown", funding_class="unknown",
                     route=dict(model="m1", effort="high")),
                dict(store="codex", specialist="code-reviewer", work_kind="review", complexity_band="high",
                     risk_band="unknown", funding_class="dollar", route=dict(model="m2", effort="unknown")),
                dict(store="opencode", specialist="explorer-agent", work_kind="investigation",
                     complexity_band="unknown", risk_band="unknown", funding_class="included_subscription",
                     route=dict(model="m3", effort="low")),
                dict(store="codex", specialist="unknown", work_kind="unknown", complexity_band="unknown",
                     risk_band="unknown", funding_class="unknown", route=dict(model="m4", effort="low")),
                dict(store="codex", specialist="unknown", work_kind="unknown", complexity_band="low",
                     risk_band="unknown", funding_class="unknown", route=dict(model="m5", effort="low"))]
        decisions = [dict(status="admitted", model="m1", effort="low", funding_class="included_subscription"),
                     dict(status="admitted", model="m2", effort="xhigh", funding_class="dollar"),
                     dict(status="pending", reason_code="no_shortlisted_fit"),
                     dict(status="admitted", model="m4", effort="low", funding_class="unknown"),
                     dict(status="no_profile")]  # an unknown specialist: counted, neither compared nor pending
        report = replay.summarize(rows, decisions)
        self.assertEqual(report["compared"], 3)
        self.assertEqual(report["pending"], 1)
        self.assertEqual(report["not_compared"], 1)
        self.assertEqual(report["historical_dollar"], 1)
        # m1: effort differs; m2: unknown historical effort, model and funding equal; m4: equal.
        self.assertEqual(report["disagreement"]["overall"], dict(compared=3, disagreed=1))
        self.assertEqual(report["disagreement"]["by_specialist"]["software-engineer"], dict(compared=1, disagreed=1))
        self.assertEqual(report["disagreement"]["by_store"]["codex"], dict(compared=3, disagreed=1))
        self.assertEqual(report["disagreement"]["by_funding_class"]["dollar"], dict(compared=1, disagreed=0))
        self.assertEqual(report["unknowns"]["specialist"], 2)
        self.assertEqual(report["unknowns"]["complexity_band"], 2)
        self.assertEqual(report["unknowns"]["effort"], 1)
        self.assertEqual(report["labels"]["specialist"]["code-reviewer"], 1)

    def test_no_title_or_session_id_reaches_the_report(self):
        threads = [child("thread-id-123", title="Implement " + SECRET)]
        store = self.codex(threads, [("parent-id-456", "thread-id-123")])
        found = replay.collect([store], now_ms=NOW_MS)
        rows = [replay.label(row) for row in found["eligible"]]
        report = replay.summarize(rows, [dict(status="pending", reason_code="x")])
        report.update(found=dict(sessions_found=found["sessions_found"], excluded=found["excluded"]))
        text = repr(report)
        for secret in (SECRET, "thread-id-123", "parent-id-456", store["path"], ROOT, str(NOW_MS - 5 * HOUR_MS),
                       "acct-secret", "Implement"):
            self.assertNotIn(secret, text)
        self.assertNotIn("title", repr(rows))

    def test_stores_are_opened_read_only(self):
        opened, real = [], sqlite3.connect

        def spy(target, *args, **kwargs):
            opened.append((target, kwargs.get("uri")))
            return real(target, *args, **kwargs)

        store = self.codex([child("t")], [("p", "t")])
        before = hashlib.sha256(Path(store["path"]).read_bytes()).hexdigest()
        with mock.patch.object(replay.sqlite3, "connect", spy):
            replay.collect([store], now_ms=NOW_MS)
        self.assertTrue(opened)
        for target, uri in opened:
            self.assertTrue(uri)
            self.assertTrue(target.startswith("file:") and target.endswith("?mode=ro"), target)
            self.assertNotIn("immutable", target)
        self.assertEqual(hashlib.sha256(Path(store["path"]).read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["codex.sqlite"])  # no journal or WAL left

    def test_only_mapped_columns_are_selected(self):
        statements = []
        store = self.codex([child("t")], [("p", "t")])
        opencode = dict(name="opencode", kind="opencode", roots=[ROOT],
                        path=opencode_store(str(self.folder / "o.db"), [dict(id="s", parent_id="r")]))
        real = sqlite3.connect

        def traced(*args, **kwargs):
            db = real(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with mock.patch.object(replay.sqlite3, "connect", traced):
            replay.collect([store, opencode], now_ms=NOW_MS)
        joined = "\n".join(statements).lower()
        self.assertTrue(statements)
        for forbidden in ("preview", "first_user_message", "rollout_path", "creator_account_id", "credential",
                          "session_message", "session_part", "tokens_used", "select *", "metadata", "source",
                          "insert", "update ", "delete", "pragma journal", "pragma wal"):
            self.assertNotIn(forbidden, joined)
        opencode_reads = [s.lower() for s in statements if "session_v2" in s]
        self.assertTrue(opencode_reads)
        for unmapped in ("tokens_input", "tokens_cache", "cost", "summary", "permission", "revert", "share_url"):
            self.assertFalse(any(unmapped in s for s in opencode_reads), unmapped)

    def test_a_changed_mapped_column_stops_the_run(self):
        path = str(self.folder / "codex.sqlite")
        with closing(sqlite3.connect(path)) as db:
            db.executescript(CODEX_SCHEMA.replace("reasoning_effort TEXT", "effort TEXT"))
        with self.assertRaisesRegex(Refused, "schema_changed: codex threads.reasoning_effort"):
            replay.collect([dict(name="codex", kind="codex", path=path, roots=[ROOT])], now_ms=NOW_MS)

    def test_missing_mapped_column_refuses_before_session_rows_are_selected(self):
        store = self.codex([child("t")], [("p", "t")])
        with closing(sqlite3.connect(store["path"])) as db:
            db.execute("ALTER TABLE threads DROP COLUMN reasoning_effort")
            db.commit()
        with mock.patch.object(replay, "_codex_rows", side_effect=AssertionError("session rows selected")), \
                mock.patch.dict(replay.READERS, codex=replay._codex_rows), \
                self.assertRaisesRegex(Refused, "schema_changed: codex threads.reasoning_effort"):
            replay.collect([store], now_ms=NOW_MS)

    def test_known_id_in_either_source_refuses_before_any_title_read(self):
        codex = self.codex([child("qual-1")], [("p", "qual-1")])
        opencode = dict(name="opencode", kind="opencode", roots=[ROOT], path=opencode_store(
            str(self.folder / "o.db"), [dict(id="s", parent_id="r", model='{"id":"m","providerID":"x"}')]))
        real = replay._connect
        for stores in ([codex, opencode], [opencode, codex]):
            for blocked in ("qual-1", "s"):
                for entry in ("gather", "run"):
                    with self.subTest(order=[s["name"] for s in stores], blocked=blocked, entry=entry):
                        titles = []

                        def connected(path):
                            db = real(path)

                            def authorize(action, table, column, *unused):
                                if action == sqlite3.SQLITE_READ and column == "title":
                                    titles.append((path, table))
                                return sqlite3.SQLITE_OK

                            db.set_authorizer(authorize)
                            return db

                        with mock.patch.object(replay, "_connect", connected), \
                                mock.patch.object(replay, "label", side_effect=AssertionError("label before gate")), \
                                mock.patch.object(replay, "compose", side_effect=AssertionError("route before gate")):
                            with self.assertRaisesRegex(Refused, "^qualification_session_found:"):
                                if entry == "gather":
                                    replay.gather(stores, {blocked}, now_ms=NOW_MS)
                                else:
                                    replay.run(stores, exclusions=dict(BASIS, ids=[blocked]), now_ms=NOW_MS)
                        self.assertEqual(titles, [], "a rejected sample read title data")

    def test_only_eligible_duplicate_winners_supply_titles(self):
        first = self.codex([child("dup", title="Review the parser", created_at_ms=NOW_MS - 6 * HOUR_MS),
                            child("root"), child("running", updated_at_ms=NOW_MS - HOUR_MS + 1),
                            child("missing", model=None), child("boundary", updated_at_ms=NOW_MS - HOUR_MS)],
                           [("p", "dup"), ("p", "running"), ("p", "missing"), ("p", "boundary")], "first.db")
        second = self.codex([child("dup", title="Implement something else")], [("p", "dup")], "second.db")
        titles, real = [], replay._connect

        def connected(path):
            db = real(path)

            def projected(cursor, row):
                for index, column in enumerate(cursor.description):
                    if column[0] == "title" and row[index] is not None:
                        titles.append((path, row[index]))
                return row

            db.row_factory = projected
            return db

        with mock.patch.object(replay, "_connect", connected):
            found = replay.gather([second, first], {"root", "running", "missing"}, now_ms=NOW_MS)
        self.assertEqual(sorted(titles), sorted([(first["path"], "Review the parser"),
                                                (first["path"], "Implement the parser")]))
        self.assertEqual(found["sessions_found"], 5)
        self.assertEqual(found["excluded"], dict(no_parent=1, no_model_id=1, no_timestamp=0, still_running=1,
                                                 host_marked_title_compaction_summary=0, duplicate=1))
        self.assertEqual(replay.label(found["eligible"][0])["specialist"], "code-reviewer")
        # An ineligible earliest copy must not become a refusal because another copy would be eligible.
        with closing(sqlite3.connect(first["path"])) as db:
            db.execute("DELETE FROM thread_spawn_edges WHERE child_thread_id = 'dup'")
            db.commit()
        found = replay.gather([second, first], {"dup"}, now_ms=NOW_MS)
        self.assertEqual([row["id"] for row in found["eligible"]], ["boundary"])

    def test_schema_metadata_and_titles_share_one_read_snapshot(self):
        for kind, phase in (("codex", "schema"), ("opencode", "schema"),
                            ("codex", "metadata"), ("opencode", "metadata")):
            with self.subTest(kind=kind, phase=phase):
                name = "snapshot-%s-%s.db" % (kind, phase)
                store = (self.codex([child("t", title="Implement original")], [("p", "t")], name)
                         if kind == "codex" else dict(name=kind, kind=kind, roots=[ROOT], path=opencode_store(
                             str(self.folder / name), [dict(id="t", parent_id="p", title="Implement original",
                                                           model='{"id":"m","providerID":"x"}')])) )
                with closing(sqlite3.connect(store["path"])) as db:
                    self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
                real = replay._schema_digest if phase == "schema" else replay.READERS[kind]
                transactions = []

                def mutate_after_read(db, *args):
                    result = real(db, *args)
                    transactions.append(db.in_transaction)
                    table, updated = ("threads", "updated_at_ms") if kind == "codex" else ("session_v2", "time_updated")
                    model = "new-model" if kind == "codex" else '{"id":"new-model","providerID":"x"}'
                    with closing(sqlite3.connect(store["path"])) as writer:
                        writer.execute("UPDATE %s SET title=?, model=?, %s=? WHERE id='t'" % (table, updated),
                                       ("Review changed", model, NOW_MS - HOUR_MS))
                        writer.commit()
                    return result

                patch = (mock.patch.object(replay, "_schema_digest", mutate_after_read) if phase == "schema"
                         else mock.patch.dict(replay.READERS, {kind: mutate_after_read}))
                with patch:
                    found = replay.collect([store], now_ms=NOW_MS)
                self.assertEqual(transactions, [True])
                (row,) = found["eligible"]
                self.assertEqual((row["title"], row["end_ms"]), ("Implement original", NOW_MS - 4 * HOUR_MS))
                self.assertEqual(replay.label(row)["specialist"], "software-engineer")
                (later,) = replay.collect([store], now_ms=NOW_MS)["eligible"]
                self.assertEqual((later["title"], later["model"], later["end_ms"]),
                                 ("Review changed", "new-model", NOW_MS - HOUR_MS))

    def test_nullable_titles_identical_bindings_and_empty_collection_keep_their_shapes(self):
        store = dict(name="opencode", kind="opencode", roots=[ROOT], path=opencode_store(
            str(self.folder / "nullable.db"), [dict(id="null", parent_id="p", title=None,
                                                   model='{"id":"m","providerID":"x"}'),
                                             dict(id="empty", parent_id="p", title="",
                                                  model='{"id":"m","providerID":"x"}')]))
        found = replay.collect([store, dict(store)], now_ms=NOW_MS)
        self.assertEqual((found["sessions_found"], found["excluded"]["duplicate"]), (2, 2))
        self.assertEqual({row["id"]: row["title"] for row in found["eligible"]}, {"null": None, "empty": ""})
        self.assertEqual([replay.label(row)["specialist"] for row in found["eligible"]], ["unknown", "unknown"])
        self.assertEqual(len(found["schemas"]["opencode"]), 2)
        empty = dict(sessions_found=0, excluded=dict(no_parent=0, no_model_id=0, no_timestamp=0, still_running=0,
                                                    host_marked_title_compaction_summary=0, duplicate=0),
                     eligible=[], schemas={})
        with mock.patch.object(replay, "_connect", side_effect=AssertionError("empty collection opened a store")):
            self.assertEqual(replay.collect([], now_ms=NOW_MS), empty)
            self.assertEqual(replay.gather([], {"blocked"}, now_ms=NOW_MS), empty)

    def test_read_connections_close_on_later_schema_or_title_failure(self):
        first = self.codex([child("t")], [("p", "t")], "first.db")
        second = self.codex([child("u")], [("p", "u")], "second.db")
        broken = self.codex([], name="broken.db")
        with closing(sqlite3.connect(broken["path"])) as db:
            db.execute("ALTER TABLE threads DROP COLUMN title")
            db.commit()
        for failure in ("schema", "title"):
            with self.subTest(failure=failure):
                opened, closed, titles = [], [], []

                class Observed(sqlite3.Connection):
                    def execute(connection, sql, *args):
                        if sql.startswith("SELECT title FROM"):
                            titles.append(sql)
                            if failure == "title":
                                raise sqlite3.OperationalError("synthetic title failure")
                        return super().execute(sql, *args)

                    def close(connection):
                        closed.append(connection)
                        super().close()

                def connected(path):
                    db = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, factory=Observed)
                    opened.append(db)
                    return db

                with mock.patch.object(replay, "_connect", connected):
                    error = Refused if failure == "schema" else sqlite3.OperationalError
                    with self.assertRaisesRegex(error, "schema_changed" if failure == "schema" else "synthetic title"):
                        replay.collect([first, broken if failure == "schema" else second], now_ms=NOW_MS)
                self.assertEqual(len(opened), 2)
                self.assertEqual(set(opened), set(closed))
                if failure == "schema":
                    self.assertEqual(titles, [])

    def test_nonunique_title_lookup_is_refused(self):
        with mock.patch.dict(globals(), CODEX_SCHEMA=CODEX_SCHEMA.replace("id TEXT PRIMARY KEY", "id TEXT")):
            store = self.codex([child("t"), child("t", title="Review other copy")], [("p", "t")])
        with self.assertRaisesRegex(Refused, "^title_projection_invalid:"):
            replay.collect([store], now_ms=NOW_MS)


PATH_ID = "opencode-claude-subscription"  # a production path from the packaged rules, qualified only in temp storage
HARNESS = "claude-agent-sdk via opencode"
GUIDE = "guide-synthetic"


CLAUDE_PATH = (PATH_ID, {"5h": 600, "7d": 100})
CLAUDE_MODELS = (("implementation", "zeta-large", "high", HARNESS, "claude-agent"),
                 ("review", "zeta-small", "low", HARNESS, "claude-agent"))
GO_PATH = ("opencode-go-subscription", {"rolling": 100, "weekly": 50, "monthly": 20})
GO_MODEL = ("implementation", "eta-go", "high", "opencode provider opencode-go", "opencode-go")
CODEX_PATH = ("codex-chatgpt-subscription", {"7d": 100})
CODEX_MODEL = ("implementation", "theta-codex", "high", "codex-native", "openai")


def routing_inputs(folder, paths=(CLAUDE_PATH,), models=CLAUDE_MODELS):
    """Synthetic qualified-path and compiled-table data written by the real writers beside a temporary settings
    path. The settings file itself is absent, so the registered defaults apply."""
    settings = folder / "routing" / "routing-settings.json"
    settings.parent.mkdir(mode=0o700)
    for path_id, demand in paths:
        routing_paths.qualify(str(settings), path_id, demand=demand, evidence="synthetic-fixture",
                              now=NOW_MS // 1000 - 10 * 3600)
    rows = {}
    for kind, model, effort, harness, _ in models:
        rows.setdefault(kind, []).append(dict(model=model, effort=effort, harness=harness, evidence_refs=[GUIDE]))
    rows = [dict(work_kind=kind, complexity_band="critical", risk_band="high", strategies=strategies)
            for kind, strategies in rows.items()]
    guidance = [dict(id=GUIDE, sha256="a" * 64, checked_at="2026-11-01T00:00:00Z", rows=rows)]
    catalog = [dict(model=model, harnesses=[harness], supported_efforts=["low", "high"], provider=provider,
                    context_limit=200000) for _, model, _, harness, provider in models]
    table = routing_evidence.compile_table(guidance, catalog, [], now="2026-11-01T00:00:00Z")
    target = settings.parent / routing_paths.TABLE_FILE
    descriptor = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(table, handle)
    return str(settings), table


def arrival(identifier, start_minutes, minutes, title, model, effort="high", store="opencode"):
    start = NOW_MS - 10 * HOUR_MS + start_minutes * MINUTE_MS
    return dict(id=identifier, store=store, kind=store, path="/synthetic", parent="p", agent="general",
                title=title, provider="claude-agent" if store == "opencode" else "openai", model=model, effort=effort,
                start_ms=start, end_ms=start + minutes * MINUTE_MS, output=None, reasoning=None,
                output_includes_reasoning=None)


MINUTE_MS = 60_000


class LabelledSource(routing_observations.LocalSource):
    """The production source with the explicitly labelled synthetic billing producer (`subscription_billing.double`,
    as F4's production-flow tests use) and, when given, a labelled per-model demand answer. Tests only: it qualifies
    no real producer and authorizes no real run."""

    def __init__(self, settings_path=None, model_demand=None):
        super().__init__(settings_path)
        self._billing, self._demand = subscription_billing.double("historical-replay-synthetic"), model_demand

    def billing(self, route, now):
        return self._billing(route, now)

    def model_demand(self, route):
        return None if self._demand is None else self._demand(route)


class DecisionCompositionTests(unittest.TestCase):
    """The shared composition `run` uses: saved qualified paths and table, the real profile, rank, shared pre-window
    shortlist and admit over a private temporary ledger, the registered window values (fresh at 0%) and defaults."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.home = self.folder.resolve() / "home"
        patch = mock.patch.dict(os.environ, HOME=str(self.home))
        patch.start()
        self.addCleanup(patch.stop)
        self.settings, self.table = routing_inputs(self.folder)
        self.labelled()

    def labelled(self, model_demand=None):
        patch = mock.patch.object(routing_observations, "default_source",
                                  lambda settings_path=None: LabelledSource(settings_path, model_demand))
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def arrivals(self):
        return [arrival("sess-a", 0, 120, "Implement the parser", "zeta-large"),     # admitted, agrees
                arrival("sess-b", 10, 30, "Fix the cache", "zeta-large"),            # overlaps a: 600 + 600 > 1000
                arrival("sess-c", 180, 30, "Review the change", "zeta-large"),       # a has ended: admitted, differs
                arrival("sess-e", 300, 30, "Build it", "other-model", "low"),        # equal start: id order, so
                arrival("sess-d", 300, 30, "Build it", "zeta-large"),                # d first (agrees), e pending
                arrival("sess-f", 400, 30, "Plan the week", "zeta-large")]           # unknown specialist

    def test_decisions_come_from_the_real_router_over_saved_routes(self):
        forbidden = AssertionError("no observer, reader, network or process call is allowed")
        with mock.patch.object(routing_observations, "collect", side_effect=forbidden), \
                mock.patch.object(routing_observations, "refresh_needed", side_effect=forbidden), \
                mock.patch.object(routing_observations, "window_path", side_effect=forbidden), \
                mock.patch.object(routing_observations, "_ledger_data", side_effect=forbidden), \
                mock.patch("runtime.codex_rate_limits.refresh", side_effect=forbidden), \
                mock.patch("socket.socket", side_effect=forbidden), \
                mock.patch("subprocess.Popen", side_effect=forbidden):
            report = replay.compose(self.arrivals(), settings_path=self.settings)
        self.assertEqual((report["compared"], report["pending"], report["not_compared"]), (3, 2, 1))
        self.assertEqual(report["disagreement"]["overall"], dict(compared=3, disagreed=1))
        self.assertEqual(report["disagreement"]["by_specialist"]["code-reviewer"], dict(compared=1, disagreed=1))
        self.assertEqual(report["disagreement"]["by_funding_class"]["included_subscription"],
                         dict(compared=3, disagreed=1))
        self.assertEqual(sum(report["pending_reasons"].values()), 2)  # the router's own fixed reason codes
        self.assertTrue(all(isinstance(reason, str) and reason for reason in report["pending_reasons"]))

    def test_the_shared_pre_window_shortlist_decides_eligibility(self):
        calls, real = [], routing_observations.shortlist

        def spy(*args, **kwargs):
            calls.append(kwargs["harnesses"])
            return real(*args, **kwargs)

        with mock.patch.object(routing_observations, "shortlist", spy):
            report = replay.compose(self.arrivals()[:1], settings_path=self.settings)
        self.assertEqual(calls, [routing_launch.host_harnesses("opencode", [dict(harness=HARNESS)] * 2)])
        self.assertEqual((report["compared"], report["eligibility"]), (1, {}))

    def test_the_current_local_source_fails_closed_on_billing(self):
        with mock.patch.object(routing_observations, "default_source", routing_observations.LocalSource):
            report = replay.compose(self.arrivals(), settings_path=self.settings)
        self.assertEqual((report["compared"], report["pending"], report["not_compared"]), (0, 5, 1))
        self.assertEqual(report["eligibility"], {"billing_unverified": 5})  # never assumed by the window values
        self.assertEqual(report["pending_reasons"], {"no_eligible_route": 5})

    def test_a_not_entitled_mark_is_respected(self):
        route = next(item for item in routing_observations.LocalSource(self.settings).manifest(self.table)
                     if item["model"] == "zeta-large")
        routing_observations.mark_not_entitled(self.settings, route["route"], route["account"], NOW_MS // 1000)
        report = replay.compose(self.arrivals()[:1], settings_path=self.settings)
        self.assertEqual((report["compared"], report["eligibility"]), (0, {"not_entitled": 1}))

    def test_a_per_model_route_is_decided_by_its_demand_evidence_not_dropped(self):
        folder = self.folder / "go"
        folder.mkdir()
        settings, _ = routing_inputs(folder, paths=(GO_PATH,), models=(GO_MODEL,))
        rows = [arrival("g", 0, 30, "Implement it", "eta-go")]
        self.assertEqual(replay.compose(rows, settings_path=settings)["eligibility"], {"demand_unverified": 1})
        self.labelled(model_demand=lambda route: route.get("model") == "eta-go")  # labelled synthetic evidence
        verified = replay.compose(rows, settings_path=settings)
        self.assertEqual(verified["eligibility"], {"window_length_unknown": 1})  # Go meters have no known length
        self.assertEqual(verified["pending_reasons"], {"no_eligible_route": 1})

    def test_a_historical_codex_arrival_only_takes_a_codex_native_route(self):
        codex = [arrival("x", 0, 30, "Implement it", "zeta-large", store="codex")]
        alone = replay.compose(codex, settings_path=self.settings)  # only the OpenCode path is qualified
        self.assertEqual((alone["compared"], alone["pending_reasons"]), (0, {"no_eligible_route": 1}))
        folder = self.folder / "both"
        folder.mkdir()
        settings, _ = routing_inputs(folder, paths=(CLAUDE_PATH, CODEX_PATH), models=CLAUDE_MODELS + (CODEX_MODEL,))
        both = replay.compose(codex + [arrival("y", 0, 30, "Implement it", "zeta-large")], settings_path=settings)
        self.assertEqual(both["compared"], 2)
        self.assertEqual(both["disagreement"]["by_store"]["opencode"], dict(compared=1, disagreed=0))
        self.assertEqual(both["disagreement"]["by_store"]["codex"], dict(compared=1, disagreed=1))  # theta-codex

    def test_a_long_session_holds_its_claim_until_its_recorded_end(self):
        rows = [arrival("a", 0, 600, "Implement x", "zeta-large"), arrival("b", 60, 30, "Fix y", "zeta-large"),
                arrival("c", 300, 30, "Fix z", "zeta-large"), arrival("d", 600, 30, "Fix w", "zeta-large")]
        report = replay.compose(rows, settings_path=self.settings)
        self.assertEqual((report["compared"], report["pending"]), (2, 2))  # b and c overlap a; d starts at its end

    def test_the_report_binds_its_inputs_by_digest(self):
        report = replay.compose(self.arrivals()[:1], settings_path=self.settings)
        source = routing_observations.LocalSource(self.settings)
        self.assertEqual(report["inputs"]["table_version"], self.table["version"])
        self.assertEqual(report["inputs"]["routes_version"], source.routes_version(self.table))
        self.assertEqual(report["inputs"]["settings"], "registered_defaults")
        self.assertEqual(report["inputs"]["observation"], "fixed_synthetic_fresh_zero")
        self.assertRegex(report["inputs"]["settings_digest"], "^[0-9a-f]{64}$")
        text = repr(report)
        for secret in ("sess-a", "Implement", "/synthetic", self.settings, str(NOW_MS - 10 * HOUR_MS)):
            self.assertNotIn(secret, text)
        self.assertEqual(sorted(p.name for p in Path(self.settings).parent.iterdir()),
                          [".paths.lock", "path-qualifications.json", "strategy-table.json"])  # ledger stays private

    def test_unknown_specialist_is_counted_without_admission_or_comparison(self):
        with mock.patch.object(routing, "admit", side_effect=AssertionError("unknown specialist admitted")):
            report = replay.compose(self.arrivals()[-1:], settings_path=self.settings)
        self.assertEqual((report["compared"], report["pending"], report["not_compared"]), (0, 0, 1))
        self.assertEqual(report["unknowns"]["specialist"], 1)
        self.assertEqual(report["labels"]["specialist"], {"unknown": 1})
        self.assertEqual(report["disagreement"]["overall"], dict(compared=0, disagreed=0))

    def test_temporary_ledger_is_private_in_replay_state_and_removed_on_success_or_error(self):
        root = self.home / ".local/state/vibe-workflow-replay"
        created, real = [], replay.usage_budget.initialize

        def initialize(path):
            identity = real(path)
            created.append(path)
            self.assertEqual(path.parent.parent.parent, root)
            for directory in (root, path.parent.parent, path.parent):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            return identity

        for failing in (False, True):
            with self.subTest(failing=failing), mock.patch.object(replay.usage_budget, "initialize", initialize):
                if failing:
                    with mock.patch.object(routing, "rank", side_effect=RuntimeError("synthetic failure")), \
                            self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                        replay.compose(self.arrivals()[:1], settings_path=self.settings)
                else:
                    self.assertEqual(replay.compose(self.arrivals()[:1], settings_path=self.settings)["compared"], 1)
            self.assertTrue(root.is_dir())
            self.assertEqual(list(root.iterdir()), [])
        self.assertEqual(len(created), 2)
        self.assertTrue(all(not path.parent.parent.exists() for path in created))

    def test_unsafe_replay_state_is_refused_without_creating_a_ledger(self):
        root = self.home / ".local/state/vibe-workflow-replay"
        root.parent.mkdir(parents=True, mode=0o700)
        target = self.folder / "unrelated"
        target.mkdir(mode=0o700)
        root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(Refused):
            replay.compose(self.arrivals()[:1], settings_path=self.settings)
        self.assertEqual(list(target.iterdir()), [])
        root.unlink()
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        with self.assertRaises(Refused):
            replay.compose(self.arrivals()[:1], settings_path=self.settings)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o755)
        self.assertEqual(list(root.iterdir()), [])
        root.chmod(0o700)
        (self.home / ".git").mkdir()
        with self.assertRaises(Refused):
            replay.compose(self.arrivals()[:1], settings_path=self.settings)
        self.assertEqual(list(root.iterdir()), [])

    def test_symlinked_replay_parent_is_refused_before_creating_directories(self):
        target = self.folder / "real-home"
        target.mkdir(mode=0o700)
        alias = self.folder / "alias-home"
        alias.symlink_to(target, target_is_directory=True)
        with mock.patch.dict(os.environ, HOME=str(alias)), self.assertRaises(Refused):
            replay.compose(self.arrivals()[:1], settings_path=self.settings)
        self.assertEqual(list(target.iterdir()), [])

    def test_without_a_qualified_path_nothing_is_decided(self):
        bare = self.folder / "bare" / "routing-settings.json"
        bare.parent.mkdir(mode=0o700)
        with self.assertRaisesRegex(Refused, "route_unqualified: no_qualified_path"):
            replay.compose(self.arrivals(), settings_path=str(bare))
        with self.assertRaisesRegex(Refused, "route_unqualified: settings_path_missing"):
            replay.compose(self.arrivals(), settings_path=None)

    def test_run_composes_only_after_every_real_gate(self):
        store = dict(name="opencode", kind="opencode", roots=[ROOT],
                     path=opencode_store(str(self.folder / "o.db"), [dict(id="s1", parent_id="r")]))
        codex = dict(name="codex", kind="codex", roots=[ROOT],
                     path=codex_store(str(self.folder / "c.sqlite"), [child("t")], [("p", "t")]))
        bare = self.folder / "bare2" / "routing-settings.json"
        bare.parent.mkdir(mode=0o700)
        with self.assertRaisesRegex(Refused, "store_missing: codex"):  # registration gates precede every read
            replay.run([store], settings_path=str(bare), exclusions=BASIS)
        opus = dict(name="opus", kind="opencode", path=store["path"], roots=[OPUS])  # excluded by Edit 10
        with self.assertRaisesRegex(Refused, "store_unregistered"):
            replay.run([store, codex, opus], settings_path=self.settings, exclusions=BASIS)
        with self.assertRaisesRegex(Refused, "route_unqualified: no_qualified_path"):
            replay.run([store, codex], settings_path=str(bare), exclusions=BASIS, now_ms=NOW_MS)

    def test_project_only_run_reports_both_sources_without_other_project_sessions(self):
        current = "/work/vibe-workflow"
        opencode = dict(name="opencode", kind="opencode", roots=[current], path=opencode_store(
            str(self.folder / "o.db"), [
                dict(id="current-child", parent_id="r", directory=current, title="Implement the parser",
                     model='{"id":"zeta-large","providerID":"claude-agent","variant":"high"}'),
                dict(id="other-child", parent_id="r", project_id="other", title=SECRET)]))
        with closing(sqlite3.connect(opencode["path"])) as db:
            db.execute("UPDATE project SET worktree = ? WHERE id = 'p1'", (current,))
            db.execute("INSERT INTO project VALUES ('other', ?, 'git', 'x', NULL, NULL, NULL, 0, 0, NULL, 0, NULL, "
                       "'[]', NULL)", (OPUS,))
            db.commit()
        codex = dict(name="codex", kind="codex", roots=[ROOT], path=codex_store(
            str(self.folder / "c.sqlite"), [child("legacy-child"), child("opus-child", cwd=OPUS)],
            [("p", "legacy-child"), ("p", "opus-child")]))
        before = {store["path"]: Path(store["path"]).read_bytes() for store in (opencode, codex)}
        report = replay.run([opencode, codex], settings_path=self.settings, exclusions=BASIS, now_ms=NOW_MS)
        self.assertEqual(report["sessions_found"], 2)
        self.assertEqual((report["compared"], report["pending"], report["not_compared"]), (1, 1, 0))
        self.assertEqual(report["disagreement"]["by_store"], {"opencode": dict(compared=1, disagreed=0)})
        self.assertEqual(report["disagreement"]["overall"], dict(compared=1, disagreed=0))
        self.assertEqual(report["excluded"], dict(no_parent=0, no_model_id=0, no_timestamp=0, still_running=0,
                                                 host_marked_title_compaction_summary=0, duplicate=0))
        self.assertEqual(set(report["inputs"]["store_schemas"]), {"opencode", "codex"})
        for store in (opencode, codex):
            self.assertRegex(report["inputs"]["store_schemas"][store["name"]][0], "^[0-9a-f]{64}$")
            self.assertEqual(Path(store["path"]).read_bytes(), before[store["path"]])
        for secret in ("current-child", "legacy-child", "other-child", "opus-child", SECRET, current, ROOT, OPUS):
            self.assertNotIn(secret, repr(report))

    def test_project_only_run_still_refuses_a_qualification_session(self):
        opencode = dict(name="opencode", kind="opencode", roots=[ROOT],
                        path=opencode_store(str(self.folder / "o.db"), []))
        codex = dict(name="codex", kind="codex", roots=[ROOT],
                     path=codex_store(str(self.folder / "c.sqlite"), [child("qual-1")], [("p", "qual-1")]))
        with self.assertRaisesRegex(Refused, "^qualification_session_found:"):
            replay.run([opencode, codex], settings_path=self.settings, exclusions=BASIS, now_ms=NOW_MS)


if __name__ == "__main__":
    unittest.main()
