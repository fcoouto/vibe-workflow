"""The live qualification harness without a server: refusals, the durable call ledger and its cap lines, the
recorder guard (under node), plugin-chain verification, user-directory and working-tree snapshots, the served
routing child's reply checks, cleanup order, redaction and the inspection script. The pilot canary's tests are in
tests/test_qualification_canary.py.

tests/qualification/opencode_live.py itself is an operator tool and is not collected.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from tests.qualification import canary, safety, structure
from tests.qualification import opencode_live as live
from tests.qualification.doubles import FAKE_SERVE, QUAL_ROUTE, Fake, Temp
from tests.qualification.doubles import qual_driver, qual_run


NODE = shutil.which("node")


APPROVED = "vendor-q/model-q#tiny"  # invented: the route an approval names


class ModelTests(unittest.TestCase):
    def test_only_the_approved_route_is_accepted(self):
        route = safety.parse_route(APPROVED)
        self.assertEqual(safety.check_model("vendor-q/model-q", route), APPROVED)
        self.assertEqual(safety.check_model(APPROVED, route), APPROVED)
        for ref in ("vendor-q/model-r", "vendor-q/model-q#large", "vendor-r/model-q#tiny", "openrouter/vendor-q/model-q",
                    "vendor-q/model-q ", "", None, ["vendor-q/model-q"]):
            with self.assertRaises(safety.Refused, msg=repr(ref)):
                safety.check_model(ref, route)

    def test_route_records_need_the_approved_variant(self):
        route = safety.parse_route(APPROVED)
        self.assertTrue(safety.on_route(dict(provider="vendor-q", model="model-q", variant="tiny"), route))
        for record in (dict(provider="vendor-q", model="model-q", variant="large"), dict(provider="vendor-q", model="model-q"),
                       dict(provider="vendor-q", model="model-r", variant="tiny"),
                       dict(provider="vendor-r", model="model-q", variant="tiny")):
            self.assertFalse(safety.on_route(record, route), record)

    def test_harness_refuses_a_launch_outside_the_approved_route(self):
        for name in ("PROVIDER", "MODEL", "VARIANT", "ROUTE"):
            self.assertFalse(hasattr(safety, name), name)  # no built-in route: the approval names it
        for missing in (None, (), ("vendor-q", "model-q")):
            with self.assertRaises(safety.Refused):
                safety.check_model(APPROVED, missing)
            self.assertFalse(safety.on_route(dict(provider="vendor-q", model="model-q", variant="tiny"), missing or None))
        with self.assertRaises(safety.Refused):
            live.Run(Path("/nonexistent-qual-base"), None)  # no --route: refused before anything starts
        from tests.qualification import server
        route = safety.parse_route(APPROVED)
        config = server.Server(Path("/nonexistent-qual-base"), route=route).config()
        self.assertEqual((config["model"], config["commands"]["qualchild"]["model"]), ("vendor-q/model-q", APPROVED))
        self.assertNotIn("model", server.Server(Path("/nonexistent-qual-base")).config())  # unknown route: none named
        self.assertEqual(canary.request(route, "pool-q"), dict(provider="vendor-q", model="model-q", variant="tiny",
                                                               harness="opencode provider vendor-q", kind="primary",
                                                               funding_pool="pool-q"))
        with self.assertRaises(safety.Refused):
            canary.plan_assignments(1, "t", now=1)  # the pilot never invents a route
        body = canary.plan_assignments(1, "t", now=1, route=safety.choice(route))[0]["body"]
        self.assertEqual(body["assignment"]["choice"], dict(provider="vendor-q", model="model-q", effort="tiny",
                                                            harness="opencode provider vendor-q", funding="subscription"))


class RouteBoundServerTests(Temp):
    def test_a_route_free_server_starts_and_binds_the_route_once_known(self):
        from tests.qualification import server
        srv = server.Server(self.private(self.tmp / "iso"))  # the chatgpt run: no route before the login's list
        srv.prepare_files()  # the real file preparation, not a stand-in
        table = srv.settings.parent / "strategy-table.json"
        self.assertFalse(table.exists())
        self.assertIsNone(json.loads(srv.control_path.read_text())["route"])
        self.assertNotIn("model", srv.config())
        self.assertEqual(set(srv.config()["commands"]), {"qualenv"})
        route = safety.parse_route(APPROVED)
        srv.set_route(route)
        self.assertEqual(json.loads(srv.control_path.read_text())["route"], list(route))
        rows = json.loads(table.read_text())["rows"]
        self.assertEqual([(item["model"], item["effort"], item["harness"]) for item in rows["worker|code|medium|medium"]],
                         [("model-q", "tiny", "opencode provider vendor-q")])
        with self.assertRaises(safety.Refused):
            srv.set_route(("vendor-q", "model-r", "tiny"))  # one route per run

    def test_the_off_route_spawn_is_only_a_pre_registered_identity(self):
        from tests.qualification import server
        unknown = server.Server(self.tmp, route=safety.parse_route(APPROVED)).config()["commands"]
        self.assertNotIn("qualspawn", unknown)  # no guessed alternate model for an unregistered route
        self.assertIsNone(server.off_route_spawn(safety.parse_route(APPROVED)))
        registered = safety.parse_route(QUAL_ROUTE)
        spawn = server.Server(self.tmp, route=registered).config()["commands"]["qualspawn"]["model"]
        self.assertEqual(spawn, server.off_route_spawn(registered))
        self.assertNotEqual(spawn, QUAL_ROUTE)


class IsolatedDirTests(Temp):
    def test_accepts_an_empty_private_directory(self):
        target = self.private(self.tmp / "q")
        self.assertEqual(safety.check_isolated_dir(str(target), self.home), target)

    def test_refusals(self):
        loose = self.private(self.tmp / "loose", 0o755)
        full = self.private(self.tmp / "full")
        (full / "x").write_text("")
        link = self.tmp / "link"
        link.symlink_to(self.private(self.tmp / "real"))
        repo = self.private(self.tmp / "repo")
        (repo / ".git").mkdir()
        worktree = self.private(self.tmp / "wt")
        (worktree / ".git").write_text("gitdir: elsewhere\n")  # a linked worktree has a .git file
        cases = [None, "", "relative/dir", str(self.tmp / "missing"), str(loose), str(full), str(link), str(self.home),
                 str(self.private(repo / "inside")), str(self.private(worktree / "deep" / "inside")),
                 str(self.private(self.home / ".config/opencode")), str(self.private(self.home / ".local/share/opencode/x")),
                 str(self.private(self.home / ".local/state/opencode")),
                 str(self.private(self.home / "Library/Application Support/x"))]
        for case in cases:
            with self.assertRaises(safety.Refused, msg=repr(case)):
                safety.check_isolated_dir(case, self.home)


class VersionTests(unittest.TestCase):
    def run_with(self, stdout, code=0):
        return lambda *args, **kwargs: subprocess.CompletedProcess(args, code, stdout=stdout, stderr="")

    def test_minimum_version_passes_without_an_upper_bound(self):
        for version in ("2.2.6", "2.2.7", "2.2.60", "2.10.0", "3.0.0", "10.0.0"):
            for prefix in ("", "opencode v"):
                with self.subTest(version=version, prefix=prefix):
                    self.assertEqual(safety.check_opencode_version(self.run_with(prefix + version + "\n")), version)

    def test_known_old_versions_and_failed_commands_are_refused(self):
        for stdout, code in (("opencode v2.2.5\n", 0), ("v1.99.0\n", 0), ("2.2.6-rc.1\n", 0),
                             ("opencode v2.2.6\n", 1)):
            with self.assertRaises(safety.Refused, msg=stdout):
                safety.check_opencode_version(self.run_with(stdout, code))

    def test_unknown_version_metadata_alone_does_not_refuse(self):
        for stdout in ("", "opencode development\n"):
            with self.subTest(stdout=stdout):
                self.assertIsNone(safety.check_opencode_version(self.run_with(stdout)))

    def test_a_missing_binary_is_refused(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError("opencode")
        with self.assertRaises(safety.Refused):
            safety.check_opencode_version(missing)


class CallLedgerTests(Temp):
    def ledger_path(self):
        return self.private(self.tmp / "state") / "call-ledger.jsonl"

    def test_initialize_seeds_the_spent_qualification_calls(self):
        path = self.ledger_path()
        ledger = safety.CallLedger.initialize(path)
        summary = ledger.summary()
        self.assertEqual(summary["qualification"], dict(cap=14, spent=5, remaining=9, open_reservations=0))
        self.assertEqual(summary["pilot"], dict(cap=24, spent=0, remaining=24, open_reservations=0))
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        with self.assertRaises(safety.Refused):
            safety.CallLedger.initialize(path)  # never re-seeded over an existing ledger

    def test_write_ahead_reservation_counts_until_settled(self):
        ledger = safety.CallLedger.initialize(self.ledger_path())
        ref = ledger.reserve("qualification", "probe", 2, run="r1")
        self.assertEqual(ledger.summary()["qualification"]["spent"], 7)  # a crash here leaves the 2 counted
        self.assertEqual(safety.CallLedger(ledger.path).summary()["qualification"]["open_reservations"], 1)
        ledger.settle(ref, 1)
        self.assertEqual(ledger.summary()["qualification"]["spent"], 6)
        with self.assertRaises(safety.Refused):
            ledger.settle(ref, 0)  # one settlement per reservation

    def test_caps_refuse_before_writing(self):
        ledger = safety.CallLedger.initialize(self.ledger_path())
        before = ledger.path.read_text()
        with self.assertRaises(safety.BudgetExceeded):
            ledger.reserve("qualification", "too many", 10)
        self.assertEqual(ledger.path.read_text(), before)
        for purpose, calls in (("other", 1), ("pilot", -1), ("pilot", 1.0), ("pilot", True)):
            with self.assertRaises((safety.Refused, ValueError), msg=(purpose, calls)):
                ledger.reserve(purpose, "x", calls)
        ref = ledger.reserve("pilot", "admission", 24)
        with self.assertRaises(safety.BudgetExceeded):
            ledger.reserve("pilot", "one more", 1)
        with self.assertRaises(safety.BudgetExceeded):
            ledger.settle(ref, 25)  # more sent than reserved: recorded, then refused
        self.assertEqual(ledger.summary()["pilot"]["spent"], 25)

    def test_racing_writers_never_exceed_the_cap(self):
        ledger = safety.CallLedger.initialize(self.ledger_path())
        granted, errors = [], []

        def take():
            try:
                granted.append(safety.CallLedger(ledger.path).reserve("qualification", "race", 1))
            except safety.BudgetExceeded:
                errors.append(1)
        threads = [threading.Thread(target=take) for _ in range(15)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        self.assertEqual((len(granted), len(errors)), (9, 6))
        self.assertEqual(ledger.summary()["qualification"]["remaining"], 0)

    def test_unsafe_or_damaged_ledgers_fail_closed(self):
        with self.assertRaises(safety.Refused):
            safety.CallLedger(self.ledger_path())  # missing: must be initialized explicitly
        path = self.ledger_path()
        safety.CallLedger.initialize(path)
        path.chmod(0o644)
        with self.assertRaises(safety.Refused):
            safety.CallLedger(path)
        path.chmod(0o600)
        path.parent.chmod(0o755)
        with self.assertRaises(safety.Refused):
            safety.CallLedger(path)
        path.parent.chmod(0o700)
        with path.open("a") as handle:
            handle.write("{not json\n")
        with self.assertRaises(safety.Refused):
            safety.CallLedger(path).summary()

    def test_export_is_generated_and_sanitized(self):
        ledger = safety.CallLedger.initialize(self.ledger_path())
        ledger.settle(ledger.reserve("pilot", "admission 1", 1, run="p1"), 1)
        exported = ledger.export()
        self.assertEqual(exported["totals"]["qualification"]["spent"], 5)
        self.assertEqual(exported["totals"]["pilot"]["spent"], 1)
        self.assertIn("model.request", exported["_meta"]["counting"])
        self.assertIn("not upstream", exported["_meta"]["counting"])
        self.assertNotIn(str(self.tmp), json.dumps(exported))


@unittest.skipUnless(NODE, "node is required to run the recorder guard")
class RecorderGuardTests(Temp):
    """Runs the generated recorder plugin under node with a fake OpenCode context."""

    DRIVER = r"""
import plugin from "./recorder.mjs"
const hooks = {}
const ctx = { options: { log: process.argv[2], control: process.argv[3] },
  session: { hook: (n, f) => { hooks["model." + n.split(".")[1]] = f } }, tool: { hook: (n, f) => { hooks.tool = f } },
  permission: { hook: (n, f) => { hooks.permission = f } }, shell: { hook: (n, f) => { hooks.shell = f } } }
await plugin.setup(ctx)
const out = []
for (const event of JSON.parse(process.argv[4])) {
  try { await hooks[event.hook](event.body); out.push("pass") } catch (error) { out.push(String(error.message)) }
}
console.log(JSON.stringify(out))
"""

    def run_guard(self, events, budget=5, title_session=None, hold=(), log=None):
        (self.tmp / "recorder.mjs").write_text(safety.recorder_source("post"))
        (self.tmp / "driver.mjs").write_text(self.DRIVER)
        control = self.tmp / "control.json"
        control.write_text(json.dumps(dict(budget=budget, title_session=title_session, hold=list(hold),
                                           route=["claude-agent", "haiku", "low"])))
        log = log or str(self.tmp / "hooks.jsonl")
        done = subprocess.run([NODE, str(self.tmp / "driver.mjs"), log, str(control), json.dumps(events)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    @staticmethod
    def request(kind="primary", provider="claude-agent", model="haiku", variant="low", session="ses_a"):
        return dict(hook="model.request", body=dict(kind=kind, sessionID=session,
                                                    model=dict(providerID=provider, id=model, variant=variant)))

    def test_variant_model_kind_title_hold_and_budget_are_enforced(self):
        out = self.run_guard([self.request(variant="high"), self.request(variant=None), self.request(model="sonnet"),
                              self.request(provider="openai"), self.request(kind="compaction"), self.request(kind="title"),
                              self.request(session="ses_held"), self.request(), self.request(kind="title", session="ses_t"),
                              self.request(kind="title", session="ses_t")],
                             budget=2, title_session="ses_t", hold=["ses_held"])
        self.assertEqual(out, ["qualification guard (guard_variant)", "qualification guard (guard_variant)",
                               "qualification guard (guard_model)", "qualification guard (guard_model)",
                               "qualification guard (guard_kind)", "qualification guard (guard_title)",
                               "qualification guard (guard_hold)", "pass", "pass", "qualification guard (guard_title)"])
        out = self.run_guard([self.request()], budget=0)
        self.assertEqual(out, ["qualification guard (guard_budget)"])

    def test_spawns_on_another_route_are_blocked(self):
        spawn = lambda model: dict(hook="tool", body=dict(tool="subagent", sessionID="ses_a", input=dict(model=model)))  # noqa: E731
        self.assertEqual(self.run_guard([spawn("claude-agent/haiku#low"), spawn("claude-agent/haiku#high"), spawn("claude-agent/haiku")]),
                         ["pass", "qualification guard (guard_spawn_model)", "qualification guard (guard_spawn_model)"])

    def test_a_log_write_failure_blocks_every_hook(self):
        blocked = str(self.tmp / "missing-dir" / "hooks.jsonl")
        out = self.run_guard([self.request(), dict(hook="shell", body=dict(env={})),
                              dict(hook="permission", body=dict(action="webfetch", effect="ask"))], log=blocked)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(item != "pass" for item in out), out)

    def test_pre_recorder_fails_closed_too(self):
        (self.tmp / "recorder.mjs").write_text(safety.recorder_source("pre"))
        (self.tmp / "driver.mjs").write_text(self.DRIVER)
        done = subprocess.run([NODE, str(self.tmp / "driver.mjs"), str(self.tmp / "no" / "log"), str(self.tmp / "c.json"),
                               json.dumps([self.request()])], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(json.loads(done.stdout), ["pass"])


class PluginChainTests(unittest.TestCase):
    PLUGINS = ["opencode.agent", "qual-post", "qual-pre", "vibe-workflow.routing"]

    @staticmethod
    def record(phase, t, decision=None, kind="primary"):
        return dict(phase=phase, hook="model.request", t=t, kind=kind, decision=decision)

    def good(self):
        return dict(plugins=self.PLUGINS, guard=[self.record("pre", 1.0), self.record("post", 2.0, "guard_variant")],
                    pending=[self.record("pre", 3.0)], guard_outcome="failed", guard_reason="guard_variant",
                    pending_reason="attach_required")

    def test_a_correct_chain_passes(self):
        self.assertEqual(safety.verify_plugin_chain(**self.good()), [])

    def test_every_failure_is_named(self):
        cases = {
            "plugin_missing:qual-post": dict(plugins=["qual-pre", "vibe-workflow.routing"]),
            "guard_probe_not_seen_by_pre": dict(guard=[self.record("post", 2.0, "guard_variant")]),
            "guard_probe_not_blocked": dict(guard=[self.record("pre", 1.0)]),
            "guard_probe_transmitted": dict(guard=[self.record("pre", 1.0), self.record("post", 2.0, "transmit")]),
            "guard_variant_not_enforced": dict(guard=[self.record("pre", 1.0), self.record("post", 2.0, "guard_budget")]),
            "recorders_out_of_order": dict(guard=[self.record("pre", 3.0), self.record("post", 2.0, "guard_variant")]),
            "guard_probe_outcome": dict(guard_outcome="succeeded"),
            # A provider-side rejection also fails the session, but it is not the guard's refusal.
            "guard_probe_reason": dict(guard_reason=None),
            "pending_probe_not_seen": dict(pending=[]),
            "shim_not_between_recorders": dict(pending=[self.record("pre", 3.0), self.record("post", 4.0, "guard_budget")]),
            "shim_did_not_block": dict(pending_reason="guard_budget"),
        }
        for reason, change in cases.items():
            with self.subTest(reason):
                self.assertIn(reason, safety.verify_plugin_chain(**{**self.good(), **change}))


class SnapshotTests(Temp):
    def test_changes_are_classified_and_never_hidden(self):
        root = self.private(self.tmp / "data")
        (root / "opencode.db").write_text("a")
        (root / "opencode.db-wal").write_text("a")
        self.private(root / "log")
        (root / "log" / "opencode.log").write_text("a")
        (root / "auth.json").write_text("a")
        before = safety.snapshot_tree(root)
        self.assertTrue(all(value is None or type(value) is int for value in before.values()))  # names and mtimes only
        self.assertIn("log/", before)
        for name in ("opencode.db", "opencode.db-wal", "log/opencode.log"):
            os.utime(root / name, ns=(1, before[name] + 10_000_000))
        (root / "log" / "opencode.1.log").write_text("b")
        diff = safety.compare_snapshots(before, safety.snapshot_tree(root))
        unexpected, churn = safety.classify_changes(diff)
        self.assertEqual(unexpected, [])
        self.assertEqual(sorted(churn), ["log/opencode.1.log", "log/opencode.log", "opencode.db", "opencode.db-wal"])
        os.utime(root / "auth.json", ns=(1, before["auth.json"] + 10_000_000))
        (root / "plugins.json").write_text("x")
        unexpected, _ = safety.classify_changes(safety.compare_snapshots(before, safety.snapshot_tree(root)))
        self.assertEqual(sorted(unexpected), ["auth.json", "plugins.json"])

    def test_missing_roots_and_bounds(self):
        self.assertEqual(safety.snapshot_tree(self.tmp / "absent"), {})
        root = self.private(self.tmp / "many")
        for index in range(30):
            (root / ("f%d" % index)).write_text("")
        with self.assertRaises(safety.Refused):
            safety.snapshot_tree(root, max_entries=10)


class CleanupTests(unittest.TestCase):
    def test_leftovers_are_found_by_process_group_and_path(self):
        ps = "  10  10 opencode serve --port 1\n  11  10 python3 routing.py --serve\n  12  12 /bin/zsh\n" \
             "  13  99 node /q/dir/x\n"
        self.assertEqual(safety.leftover_processes(ps, pgid=10, needles=["/q/dir"]), [10, 11, 13])
        self.assertEqual(safety.leftover_processes(ps, pgid=None, needles=[]), [])


class ProjectConfigTests(Temp):
    def test_config_lives_in_an_excluded_subdirectory_and_is_restored_exactly(self):
        from tests.qualification import server
        project = self.private(self.tmp / "project")
        (project / ".git" / "info").mkdir(parents=True)
        exclude = project / ".git" / "info" / "exclude"
        for original in (None, "", "*.pyc", "*.pyc\n"):
            with self.subTest(original=original):
                if original is None:
                    exclude.unlink(missing_ok=True)
                else:
                    exclude.write_text(original)
                config = server.ProjectConfig(project)
                where = config.create({"plugins": []})
                self.assertEqual(where, project / ".vibe-qual-canary")
                self.assertIn("/.vibe-qual-canary/", exclude.read_text().splitlines())
                self.assertTrue((where / "opencode.json").exists())
                self.assertNotEqual(where.parent / "opencode.json", where / "opencode.json")
                with self.assertRaises(safety.Refused):
                    server.ProjectConfig(project).create({})  # a leftover is never overwritten
                self.assertTrue(config.remove())
                self.assertFalse(where.exists())
                self.assertEqual(exclude.read_text() if exclude.exists() else None, original)

    def test_a_linked_worktree_is_refused(self):
        from tests.qualification import server
        project = self.private(self.tmp / "wt")
        (project / ".git").write_text("gitdir: x\n")
        with self.assertRaises(safety.Refused):
            server.ProjectConfig(project).create({})


class RedactionTests(unittest.TestCase):
    def test_secrets_paths_ids_and_names_are_removed(self):
        token = "Abc_def-0123456789abcdefghijklmnopqrstuvwxy"
        redact = safety.Redactor(secrets=[token, "pw-secret"], paths=["/private/tmp/q1", "/Users/someone"], names=["someone"])
        payload = {"authority": token, "log": "server password pw-secret at /private/tmp/q1/data",
                   "ses_0123456789abcdef0123456789abcdef": ["msg_1bf0000000000AaBbCcDdEeFfG", "ses_0123456789abcdef0123456789abcdef"],
                   "owner": "someone", "home": "/Users/someone/.claude", "kind": "title", "n": 3}
        clean = redact(payload)
        text = json.dumps(clean)
        self.assertEqual(redact.leaks(text), [])
        self.assertNotIn(token, text)
        self.assertIn('"<ses#1>": ["<msg#1>", "<ses#1>"]', text)
        self.assertEqual((clean["kind"], clean["n"], clean["owner"]), ("title", 3, "<user>"))

    def test_leaks_reports_unsanitized_text(self):
        redact = safety.Redactor(secrets=["tok-123"], paths=["/tmp/x"], names=["alice"])
        found = redact.leaks("tok-123 /tmp/x ses_ABCDEF123456 alice")
        # the four fragments, and the session id again through the strict id and 16-character token patterns
        self.assertEqual([f for f in found if f.startswith("pattern:")], ["pattern:id", "pattern:token"])
        self.assertEqual(len(found), 6)
        self.assertEqual(redact.leaks("<ses#1> <secret> <path> malice"), [])


class ShapeTests(unittest.TestCase):
    def test_id_shape_never_returns_the_value(self):
        shape = safety.id_shape("ses_" + "0a" * 16)
        self.assertEqual(shape, dict(prefix="ses", body_length=32, classes=["digit", "lower"], lower_hex_only=True, derived_shape=True))
        own = safety.id_shape("ses_1bf0000000000AaBbCcDdEeFfG")
        self.assertEqual((own["derived_shape"], own["body_length"]), (False, 26))
        self.assertFalse(safety.id_shape(None)["derived_shape"])

    def test_latency_pairs_and_summary(self):
        records = [dict(phase="pre", hook="model.request", session="s1", kind="primary", t=1.0),
                   dict(phase="pre", hook="model.request", session="s2", kind="primary", t=2.0),
                   dict(phase="post", hook="model.request", session="s1", kind="primary", t=4.5)]
        pairs, blocked = safety.latency_pairs(records)
        self.assertEqual(pairs, [dict(hook="model.request", kind="primary", ms=3.5)])
        self.assertEqual([r["session"] for r in blocked], ["s2"])
        self.assertEqual(safety.summarize_ms([3.0, 1.0, 2.0])["median_ms"], 2.0)
        self.assertIsNone(safety.summarize_ms([]))


class MainRefusalTests(Temp):
    def test_main_refuses_before_starting_anything(self):
        ledger = self.private(self.tmp / "state") / "calls.jsonl"
        safety.CallLedger.initialize(ledger)
        target = self.private(self.tmp / "ok")
        base = ["--isolated-dir", str(target), "--call-ledger", str(ledger)]
        with self.assertRaises(SystemExit):
            live.main(["--call-ledger", str(ledger)])  # no --isolated-dir
        self.assertEqual(live.main(base), 2)  # no --route: refused, never a default
        base += ["--route", APPROVED]
        with mock.patch("sys.stderr") as err:  # --stages keeps its pre-registered route, whatever --route says
            self.assertEqual(live.main(base + ["--stages", "descendants"]), 2)
        self.assertIn("qualification-2 allows only", "".join(str(c) for c in err.write.call_args_list))
        self.assertEqual(live.main(base[:2] + ["--call-ledger", str(self.tmp / "absent.jsonl")]), 2)
        self.assertEqual(live.main(["--isolated-dir", str(self.private(self.tmp / "loose", 0o755)), "--call-ledger", str(ledger)]), 2)
        self.assertEqual(live.main(base + ["--out", str(target / "r.json")]), 2)  # output inside the dir that is deleted
        self.assertEqual(live.main(base + ["--canary", "--project", str(self.tmp)]), 2)
        self.assertEqual(live.main(base + ["--canary", "--project", canary.ALLOWLIST, "--canary-state", str(target)]), 2)
        self.assertEqual(list(target.iterdir()), [])


class InspectRefusalTests(Temp):
    def test_only_a_kept_run_with_three_sessions_is_read(self):
        from tests.qualification import opencode_inspect as inspect
        run = self.private(self.tmp / "run")
        with self.assertRaises(safety.Refused):
            inspect.check_kept_run(run)  # no hooks.jsonl or server.log
        (run / "server.log").write_text("")
        (run / "hooks.jsonl").write_text("\n".join(json.dumps(dict(hook="model.request", session="ses_%d" % i)) for i in (1, 2)))
        self.assertEqual(inspect.check_kept_run(run), run)
        with self.assertRaises(safety.Refused):
            inspect.main(run, QUAL_ROUTE)  # two sessions: refused before any server starts
        run.chmod(0o755)
        with self.assertRaises(safety.Refused):
            inspect.check_kept_run(run)


class LedgerStrictnessTests(Temp):
    def ledger(self):
        return safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")

    def test_malformed_records_are_refused_not_key_errors(self):
        bad = [dict(v=1, kind="reserve", purpose="qualification", calls=1),            # no seq
               dict(v=1, seq=9, kind="reserve", purpose="other", calls=1),             # unknown purpose
               dict(v=1, seq=9, kind="settle", purpose="qualification", calls=0),      # no ref
               dict(v=1, seq=9, kind="seed", calls=1),                                 # no purpose
               dict(v=1, seq="9", kind="reserve", purpose="pilot", calls=1)]           # seq not an integer
        for record in bad:
            with self.subTest(record=record):
                ledger = safety.CallLedger.initialize(self.private(self.tmp / ("s%d" % bad.index(record))) / "c.jsonl")
                with ledger.path.open("a") as handle:
                    handle.write(json.dumps(record) + "\n")
                with self.assertRaises(safety.Refused):
                    ledger.summary()

    def test_settle_dead_counts_the_reserved_calls_and_refuses_live_runs(self):
        ledger = self.ledger()
        ref = ledger.reserve("qualification", "dead run", 2, run="r1")
        records = [json.loads(line) for line in ledger.path.read_text().splitlines()]
        self.assertEqual(records[-1]["pid"], os.getpid())
        with self.assertRaises(safety.Refused):
            ledger.settle_dead(ref, processes="")  # its pid (this test) is alive
        child = subprocess.Popen(["true"])
        child.wait()
        with ledger.path.open("a") as handle:  # a reservation written by a process that has exited
            handle.write(json.dumps(dict(v=1, seq=99, kind="reserve", purpose="qualification", calls=1, label="x",
                                         run="r2", pid=child.pid)) + "\n")
        with self.assertRaises(safety.Refused):
            ledger.settle_dead(99, processes=" 4242 python3 -B tests/qualification/opencode_live.py --through 4\n")
        totals = ledger.settle_dead(99, processes="")
        self.assertEqual(totals["spent"], 5 + 2 + 1)  # never under-counts: settled at the reserved count
        with self.assertRaises(safety.Refused):
            ledger.settle_dead(99, processes="")  # already settled
        with self.assertRaises(safety.Refused):
            ledger.settle_dead(1, processes="")  # a seed, not a reservation


class RedactorKeyTests(unittest.TestCase):
    def test_field_names_are_not_ids(self):
        redact = safety.Redactor()
        clean = redact({"call_ledger": {"qualification": 1}, "msg_count": 2, "ses_" + "abcdef" * 5 + "ab": "msg_" + "a" * 32})
        self.assertIn("call_ledger", clean)
        self.assertIn("msg_count", clean)
        self.assertEqual(sorted(clean), ["<ses#1>", "call_ledger", "msg_count"])  # long ids are still ids
        self.assertEqual(clean["<ses#1>"], "<msg#1>")


@unittest.skipUnless(shutil.which("git"), "git is required")
class RepoSnapshotTests(Temp):
    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def setUp(self):
        super().setUp()
        self.repo = self.private(self.tmp / "repo")
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        (self.repo / "clean.txt").write_text("a")
        (self.repo / "dirty.txt").write_text("a")
        self.git("add", ".")
        self.git("commit", "-qm", "init")
        (self.repo / "dirty.txt").write_text("b")
        (self.repo / "untracked.txt").write_text("u")

    def test_changes_are_split_by_whether_the_path_was_dirty_at_start(self):
        start = safety.repo_snapshot(self.repo)
        self.assertEqual(sorted(start["dirty"]), ["dirty.txt", "untracked.txt"])
        self.assertEqual(safety.repo_changes(start, safety.repo_snapshot(self.repo)), dict(new=[], dirty_changed=[], head_changed=False))
        (self.repo / "dirty.txt").write_text("c")
        (self.repo / "untracked.txt").write_text("v")
        (self.repo / "clean.txt").write_text("changed")
        (self.repo / "added.txt").write_text("n")
        changes = safety.repo_changes(start, safety.repo_snapshot(self.repo))
        self.assertEqual(changes["new"], ["added.txt", "clean.txt"])
        self.assertEqual(changes["dirty_changed"], ["dirty.txt", "untracked.txt"])
        self.git("add", ".")
        self.git("commit", "-qm", "two")
        self.assertTrue(safety.repo_changes(start, safety.repo_snapshot(self.repo))["head_changed"])

    def test_the_canary_config_directory_is_not_a_change(self):
        start = safety.repo_snapshot(self.repo)
        from tests.qualification import server
        (self.repo / ".git" / "info").mkdir(exist_ok=True)
        config = server.ProjectConfig(self.repo)
        config.create({"plugins": []})
        self.assertEqual(safety.repo_changes(start, safety.repo_snapshot(self.repo))["new"], [])
        self.assertTrue(config.remove())


class ServedReplyTests(Temp):
    def routing(self, mode, timeout=0.5):
        from tests.qualification import server
        script = self.tmp / "fake_serve.py"
        script.write_text(FAKE_SERVE)
        self.count = self.tmp / ("spawns-%s" % mode)
        routing_cmd = server.Routing(reply_timeout=timeout, child_argv=[shutil.which("python3") or "python3", str(script), mode, str(self.count)])
        self.addCleanup(routing_cmd.stop)
        return routing_cmd

    def requests(self):
        path = Path(str(self.count) + ".requests")
        return path.read_text().split() if path.exists() else []

    def test_a_stale_reply_to_a_read_respawns_and_retries(self):
        routing_cmd = self.routing("stale")
        answer, _ = routing_cmd.served("status")
        self.assertEqual((answer["state"], answer["spawn"], answer["op"]), ("ok", 2, "status"))
        self.assertNotIn("stale", answer)
        self.assertEqual(self.requests(), ["status", "status"])

    def test_a_stale_reply_to_a_write_is_reported_never_resent(self):
        for op in ("record", "dispatch", "prepare", "set"):
            with self.subTest(op=op):
                routing_cmd = self.routing("stale")
                Path(str(self.count) + ".requests").unlink(missing_ok=True)
                self.count.unlink(missing_ok=True)
                answer, _ = routing_cmd.served(op, event={})
                self.assertEqual(answer, dict(state="unconfirmed", reason="reply_mismatch"))
                self.assertEqual(self.requests(), [op])
                routing_cmd.stop()

    def test_a_delayed_reply_times_out_kills_and_retries_reads_only(self):
        routing_cmd = self.routing("delay")
        answer, _ = routing_cmd.served("check", request={})
        self.assertEqual((answer["state"], answer["spawn"]), ("ok", 2))
        self.count.unlink()
        Path(str(self.count) + ".requests").unlink()
        routing_cmd.stop()
        routing_cmd = self.routing("delay")
        self.assertEqual(routing_cmd.served("record", event={})[0], dict(state="unconfirmed", reason="reply_timeout"))
        self.assertEqual(self.requests(), ["record"])

    def test_an_interrupt_while_waiting_kills_the_child_so_no_reply_is_read_late(self):
        routing_cmd = self.routing("ok")
        calls = []
        original = routing_cmd._readline

        def interrupted(deadline):
            if not calls:
                calls.append(routing_cmd.child.pid)
                raise live.Interrupted("signal 15")
            return original(deadline)
        routing_cmd._readline = interrupted
        with self.assertRaises(live.Interrupted):
            routing_cmd.served("status")
        self.assertIsNone(routing_cmd.child)  # killed; its pending reply can never be read as another answer
        answer, _ = routing_cmd.served("status")
        self.assertEqual(answer["op"], "status")
        self.assertNotEqual(routing_cmd.child.pid, calls[0])  # a fresh child answered


class CleanupOrderTests(Temp):
    def run_with_config(self, damaged=True, stop_fails=False):
        project = self.private(self.tmp / "project")
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        exclude = project / ".git" / "info" / "exclude"
        before = exclude.read_bytes()
        state = canary.check_canary_state(self.private(self.tmp / "q") / "canary", home=self.home)
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        base = self.private(self.tmp / "iso")
        run = qual_run(base, ledger, canary_project=str(project), canary_state=state)
        run.replay_dir = self.tmp / "replay"  # the cleanup's admission export stays in the test's tree
        run.server.project_config.create({"plugins": []})
        run.server.log.write_text('{"hook":"model.request","phase":"pre"}\n{"hook":"model.req' if damaged else "")
        order = []

        def stop():
            order.append("stop")
            if stop_fails:
                raise OSError("stop failed")
        run.server.stop = stop
        original_remove = run.server.project_config.remove
        run.server.project_config.remove = lambda: order.append("remove") or original_remove()
        run.cleanup()
        return run, order, project, exclude, before

    def test_the_server_stops_and_the_repo_is_restored_before_hooks_are_read(self):
        run, order, project, exclude, before = self.run_with_config()
        self.assertEqual(order[:2], ["stop", "remove"])
        self.assertFalse((project / ".vibe-qual-canary").exists())
        self.assertEqual(exclude.read_bytes(), before)
        self.assertTrue(run.results["hooks_unreadable"])
        self.assertTrue(run.results["cleanup"]["verified_by_harness"])
        self.assertFalse(run.base.exists())

    def test_a_failing_stop_still_restores_the_repo_and_is_reported(self):
        run, order, project, exclude, before = self.run_with_config(damaged=False, stop_fails=True)
        self.assertEqual(order[:2], ["stop", "remove"])
        self.assertEqual(exclude.read_bytes(), before)
        self.assertIn("stop_server: OSError", run.results["cleanup"]["errors"])
        self.assertFalse(run.results["cleanup"]["verified_by_harness"])


class AncestorConfigTests(Temp):
    def test_any_opencode_config_above_the_server_location_is_refused(self):
        project = self.private(self.tmp / "a" / "b" / "project")
        canary.check_ancestor_configs(project)
        for where, name, directory in ((project, "opencode.json", False), (self.tmp / "a", "opencode.jsonc", False),
                                       (self.tmp / "a" / "b", ".opencode", True)):
            with self.subTest(where=str(where), name=name):
                target = where / name
                target.mkdir() if directory else target.write_text("{}")
                with self.assertRaises(safety.Refused):
                    canary.check_ancestor_configs(project)
                target.rmdir() if directory else target.unlink()
        canary.check_ancestor_configs(project)


class SmallFixTests(Temp):
    def test_git_snapshots_never_take_optional_locks(self):
        seen = []

        def run(argv, **kwargs):
            seen.append(kwargs.get("env", {}).get("GIT_OPTIONAL_LOCKS"))
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
        safety.repo_snapshot(self.tmp, run=run)
        self.assertEqual(seen, ["0", "0", "0"])

    def test_guarded_roots_are_resolved_through_symlinks(self):
        real_local = self.private(self.tmp / "real_local")
        self.private(real_local / "state" / "vibe-workflow")
        (self.home / ".local").symlink_to(real_local)
        with self.assertRaises(safety.Refused):
            canary.check_canary_state(real_local / "state" / "vibe-workflow" / "canary", home=self.home,
                                      production=(self.tmp / "p" / "s.json", self.tmp / "p" / "l.db"))
        self.private(real_local / "share" / "opencode")
        with self.assertRaises(safety.Refused):
            safety.check_isolated_dir(str(self.private(real_local / "share" / "opencode" / "q")), self.home)

    def test_signals_are_ignored_before_any_recovery_step(self):
        class Quiet(Fake):
            def quiesce(self):
                self.calls.append("quiesce")
        fake = Quiet(raises=dict(launch=KeyError("x")))
        state = self.private(self.tmp / "state")
        driver = qual_driver(fake, safety.CallLedger.initialize(state / "c.jsonl"), n=1, run="t")
        driver.run()
        after = fake.calls[fake.calls.index("launch") + 1:]
        self.assertEqual(after[0], "quiesce")
        self.assertEqual(after, ["quiesce", "record:uncertain", "kill_switch"])



class CapEditTests(Temp):
    def test_a_recorded_cap_line_raises_the_pilot_cap_without_rewriting_history(self):
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        ledger.settle(ledger.reserve("pilot", "attempt 1", 2), 2)
        before = ledger.path.read_text()
        ledger.set_cap("pilot", 28, "Edit 4: attempt 1 aborted on a harness defect")
        self.assertTrue(ledger.path.read_text().startswith(before))  # appended only
        self.assertEqual(ledger.summary()["pilot"], dict(cap=28, spent=2, remaining=26, open_reservations=0))
        self.assertEqual(safety.CAPS["pilot"], 24)  # the pre-registered value stays in the code's history
        with self.assertRaises(safety.Refused):
            ledger.set_cap("pilot", 29, "beyond the recorded maximum")
        with self.assertRaises(safety.Refused):
            ledger.set_cap("pilot", 1, "below what was spent")
        with self.assertRaises(safety.Refused):
            ledger.set_cap("other", 5, "x")
        with self.assertRaises(safety.Refused):
            ledger.set_cap("pilot", 28, "")  # a reason is required
        exported = ledger.export()
        self.assertEqual(exported["totals"]["pilot"]["cap"], 28)
        self.assertEqual([e["kind"] for e in exported["entries"] if e["purpose"] == "pilot"], ["reservation", "cap"])

    def test_the_pilot_still_needs_four_calls_left(self):
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        ledger.set_cap("pilot", 28, "edit")
        ledger.settle(ledger.reserve("pilot", "fill", 25), 25)
        self.assertEqual(ledger.remaining("pilot"), 3)


class StructureTests(Temp):
    """The class of defect behind pilot attempt 1 (an instance attribute shadowing `Run.account`), checked statically
    and on real instances, plus duplicate methods, call signatures across the harness classes and undefined names."""

    FILES = sorted((Path(live.__file__).parent).glob("*.py"))

    def instances(self):
        from tests.qualification import server
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        project = self.private(self.tmp / "project")
        subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
        state = canary.check_canary_state(self.private(self.tmp / "q") / "canary", home=self.home)
        qualification = qual_run(self.private(self.tmp / "iso1"), ledger)
        pilot = qual_run(self.private(self.tmp / "iso2"), ledger, canary_project=str(project), canary_state=state)
        return [qualification, pilot, live.CanaryEnv(pilot), server.Routing(), server.Server(self.private(self.tmp / "iso3")),
                ledger, qual_driver(Fake(), ledger, n=1, run="t"), server.ProjectConfig(project)]

    def test_no_instance_attribute_shadows_a_method_or_property(self):
        for instance in self.instances():
            cls = type(instance)
            shadowed = [name for name in vars(instance) if callable(getattr(cls, name, None))
                        or isinstance(getattr(cls, name, None), property)]
            self.assertEqual(shadowed, [], cls.__name__)

    def test_the_checker_catches_the_attempt_one_defect(self):
        class Broken:
            def __init__(self):
                self.pool, self.account = "p", "a"

            def account(self, session):
                return session
        instance = Broken()
        self.assertEqual([n for n in vars(instance) if callable(getattr(Broken, n, None))], ["account"])

    def trees(self):
        import ast
        return {path.name: ast.parse(path.read_text(), str(path)) for path in self.FILES}

    def test_no_class_defines_a_method_twice(self):
        import ast
        for name, tree in self.trees().items():
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.Module)):
                    defs = [item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                    self.assertEqual(len(defs), len(set(defs)), "%s: %s" % (name, getattr(node, "name", "module")))

    def test_calls_through_self_env_run_match_the_target_signatures(self):
        problems = structure.signature_problems(self.FILES)
        self.assertEqual(problems, [])

    def test_the_signature_checker_finds_a_mismatch(self):
        sample = self.tmp / "sample.py"
        sample.write_text("class CanaryEnv:\n    def account(self, session, titles_sent=0):\n        return 1\n\n"
                          "class Run:\n    def go(self, env):\n        env.account()\n        env.account(1, 2, 3)\n"
                          "        env.account(1, nope=2)\n        self.missing(1)\n        env.account(1, titles_sent=1)\n")
        problems = structure.signature_problems([sample])
        self.assertEqual(len(problems), 4, problems)

    def test_the_doubles_match_the_environment_contract(self):
        import inspect
        from tests.qualification.doubles import Fake as FakeEnv
        for name, method in inspect.getmembers(live.CanaryEnv, inspect.isfunction):
            if name.startswith("_") or name in ("diagnostics", "quiesce", "progress"):
                continue
            self.assertEqual(list(inspect.signature(getattr(FakeEnv, name)).parameters),
                             list(inspect.signature(method).parameters), name)

    def test_every_harness_file_compiles_and_names_are_defined(self):
        import py_compile
        for path in self.FILES:
            py_compile.compile(str(path), doraise=True, cfile=str(self.tmp / (path.name + "c")))
        self.assertEqual(structure.undefined_names(self.FILES), [])
        sample = self.tmp / "typo.py"
        sample.write_text("import os\n\ndef f(x):\n    return REQUESTS + x + os.sep\n")
        self.assertEqual(structure.undefined_names([sample]), ["typo.py:4 REQUESTS"])

    def test_doubles_refuse_method_overrides(self):
        fake = Fake()
        with self.assertRaises(TypeError):
            fake.account = lambda session: None
        with self.assertRaises(TypeError):
            Fake(account=lambda session: None)
        fake.transmit = dict(primary=1, title=0, other=0)  # data knobs stay settable


class ShapeKeyTests(unittest.TestCase):
    def test_id_like_keys_are_replaced(self):
        shape = safety.shape_of({"ses_1bf0000000000AaBbCcDdEeFfG": 1, "a" * 32: "x", "tokens": {"input": 1}})
        self.assertEqual(sorted(shape), ["<key#1>", "<key#2>", "tokens"])


if __name__ == "__main__":
    unittest.main()
