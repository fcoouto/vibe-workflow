"""The live qualification harness without a server: refusals, the durable call ledger, the recorder guard (under
node), plugin-chain verification, user-directory snapshots, cleanup checks, redaction, and the canary pilot's
preconditions and abort paths against a fake environment.

tests/qualification/opencode_live.py itself is an operator tool and is not collected.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from runtime import routing, routing_settings
from tests.qualification import canary, safety
from tests.qualification import opencode_live as live

NODE = shutil.which("node")


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.private(self.tmp / "home")

    def private(self, path, mode=0o700):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
        return path


class ModelTests(unittest.TestCase):
    def test_only_claude_agent_haiku_low_is_accepted(self):
        self.assertEqual(safety.check_model("claude-agent/haiku"), "claude-agent/haiku#low")
        self.assertEqual(safety.check_model("claude-agent/haiku#low"), "claude-agent/haiku#low")
        for ref in ("claude-agent/sonnet", "claude-agent/haiku#high", "openai/gpt-5", "chatgpt/gpt-5", "opencode/big-pickle",
                    "openrouter/anthropic/claude-haiku", "claude-agent/haiku ", "", None, ["claude-agent/haiku"]):
            with self.assertRaises(safety.Refused, msg=repr(ref)):
                safety.check_model(ref)

    def test_route_records_need_the_low_variant(self):
        self.assertTrue(safety.on_route(dict(provider="claude-agent", model="haiku", variant="low")))
        for record in (dict(provider="claude-agent", model="haiku", variant="high"), dict(provider="claude-agent", model="haiku"),
                       dict(provider="claude-agent", model="sonnet", variant="low"), dict(provider="openai", model="haiku", variant="low")):
            self.assertFalse(safety.on_route(record), record)


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

    def test_only_the_qualified_version_passes(self):
        self.assertEqual(safety.check_opencode_version(self.run_with("opencode v2.2.6\n")), "2.2.6")
        self.assertEqual(safety.check_opencode_version(self.run_with("2.2.6\n")), "2.2.6")
        for stdout, code in (("opencode v2.2.7\n", 0), ("v2.2.60\n", 0), ("", 0), ("opencode v2.2.6\n", 1)):
            with self.assertRaises(safety.Refused, msg=stdout):
                safety.check_opencode_version(self.run_with(stdout, code))

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
        control.write_text(json.dumps(dict(budget=budget, title_session=title_session, hold=list(hold))))
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
                    pending=[self.record("pre", 3.0)], guard_outcome="failed", pending_reason="attach_required")

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
        self.assertEqual(len(redact.leaks("tok-123 /tmp/x ses_ABCDEF123456 alice")), 4)
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
        self.assertEqual(live.main(base[:2] + ["--call-ledger", str(self.tmp / "absent.jsonl")]), 2)
        self.assertEqual(live.main(["--isolated-dir", str(self.private(self.tmp / "loose", 0o755)), "--call-ledger", str(ledger)]), 2)
        self.assertEqual(live.main(base + ["--out", str(target / "r.json")]), 2)  # output inside the dir that is deleted
        self.assertEqual(live.main(base + ["--canary", "--project", str(self.tmp)]), 2)
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
            inspect.main(run)  # two sessions: refused before any server starts
        run.chmod(0o755)
        with self.assertRaises(safety.Refused):
            inspect.check_kept_run(run)


class Fake:
    """A canary environment double: every call is recorded; behavior is switched per test."""

    def __init__(self, **change):
        self.calls = []
        self.settings = dict(smart_routing=True, api_usage_enabled=False, revision=3)
        self.ledger = dict(state="ready", holds={}, open_attempts=0, authority_required=True, unstamped_open_holds=0,
                           unbound_open_holds=0, cost_unknown=dict(ids=[], more=0), uncertain_dollar=dict(ids=[], more=0),
                           blocked_organizations=[], overrun_backlog=False, reconcile_hint=None, organizations=[])
        self.caps = dict(total=4, per_account=2)
        self.transmit = dict(primary=1, title=0, other=0)
        self.off_route = 0
        self.settlement = dict(kind="window", released={"5h": 10}, observed_units={})
        self.fail = {}
        self.__dict__.update(change)

    def status(self):
        self.calls.append("status")
        if "status" in self.fail:
            return self.fail["status"]
        return dict(state="ok", settings_state="ok", settings=dict(self.settings), settings_revision=self.settings["revision"],
                    effective_caps=dict(self.caps), ledger=json.loads(json.dumps(self.ledger)), test_inputs=False,
                    project_pinned=True)

    def health(self):
        self.calls.append("health")
        return dict(state="ok")

    def prepare(self, body):
        self.calls.append("prepare")
        return dict(state=self.fail.get("prepare", "admitted"), admission=dict(billing_class="included_subscription")), 5.0

    def dispatch(self, attempt):
        self.calls.append("dispatch")
        return dict(state="dispatched", authority="t" * 43, session_id="ses_" + "a" * 32, message_id="msg_" + "b" * 32), 7.0

    def attach(self, session, attempt, token):
        self.calls.append("attach")
        return self.fail.get("attach", True)

    def detach(self, session, attempt, token):
        self.calls.append("detach")
        return True

    def launch(self, session, message, allow_title):
        self.calls.append("launch")
        if self.fail.get("launch") == "timeout":
            return dict(idle=False)
        transmit = dict(self.transmit, title=1 if allow_title else 0) if self.transmit.get("title") is None else dict(self.transmit)
        return dict(idle=True, transmitted=transmit, off_route=self.off_route, outcome="succeeded", check_ms=0.7)

    def account(self, session):
        self.calls.append("account")
        if "account" in self.fail:
            raise ValueError("refused")
        return dict(usage=dict(source="host_message_totals", requests=[dict(kind="primary")]), unknown=[])

    def record(self, event):
        self.calls.append("record:" + event["kind"])
        return dict(state="recorded", outcome="complete", settlement=dict(self.settlement)), 4.0

    def open_attempts_during(self):
        return 0

    def kill_switch(self):
        self.calls.append("kill_switch")
        self.settings["smart_routing"] = False
        return dict(state="updated")


class CanaryPreconditionTests(Temp):
    def test_the_project_allowlist_is_exact(self):
        self.assertEqual(canary.ALLOWLIST, "/Users/fcouto/dev/git/vibe-workflow")
        for other in (str(self.tmp), "/Users/fcouto/dev/git/vibe-workflow/runtime", "/Users/fcouto/dev/git/vibe-workflow.worktrees/qual",
                      "", None):
            with self.assertRaises(safety.Refused, msg=other):
                canary.check_project(other)

    def refusals(self, fake, probes_ok=True):
        return canary.preconditions(fake.status(), fake.health(), dict(chain=[] if probes_ok else ["guard_probe_not_blocked"]))

    def test_a_clean_subscription_only_state_passes(self):
        self.assertEqual(self.refusals(Fake()), [])

    def test_each_refusal(self):
        cases = {
            "smart_routing_off": dict(settings=dict(smart_routing=False, api_usage_enabled=False, revision=1)),
            "api_usage_enabled": dict(settings=dict(smart_routing=True, api_usage_enabled=True, revision=1)),
            "organization_enabled": dict(ledger_change=dict(organizations=[dict(id="org", enabled=True)])),
            "authority_required_off": dict(ledger_change=dict(authority_required=False)),
            "unstamped_open_holds": dict(ledger_change=dict(unstamped_open_holds=1)),
            "cost_unknown": dict(ledger_change=dict(cost_unknown=dict(ids=["a"], more=0))),
            "uncertain_holds": dict(ledger_change=dict(holds=dict(uncertain=1))),
            "uncertain_dollar": dict(ledger_change=dict(uncertain_dollar=dict(ids=["a"], more=0))),
            "blocked_organizations": dict(ledger_change=dict(blocked_organizations=[dict(id="o")])),
            "overrun_backlog": dict(ledger_change=dict(overrun_backlog=True)),
            "concurrency_caps_raised": dict(caps=dict(total=5, per_account=2)),
            "open_attempts": dict(ledger_change=dict(open_attempts=1)),
        }
        for reason, change in cases.items():
            with self.subTest(reason):
                fake = Fake(**{k: v for k, v in change.items() if k != "ledger_change"})
                fake.ledger.update(change.get("ledger_change", {}))
                self.assertIn(reason, self.refusals(fake))
        self.assertIn("guard_probe_failed", self.refusals(Fake(), probes_ok=False))
        unhealthy = canary.preconditions(Fake().status(), dict(state="not_ready"), dict(chain=[]))
        self.assertIn("routing_unhealthy", unhealthy)
        self.assertIn("status_unavailable", canary.preconditions(dict(state="blocked"), dict(state="ok"), dict(chain=[])))

    def test_the_plan_covers_three_roles_and_two_bands_with_valid_jobs(self):
        plan = canary.plan_assignments(20, "pilot-x", now=1_800_000_000)
        self.assertEqual(len(plan), 20)
        self.assertEqual({p["label"]["role"] for p in plan}, {"implementer", "reviewer", "researcher"})
        self.assertEqual({p["label"]["band"] for p in plan}, {"low", "medium"})
        for item in plan:
            body = item["body"]
            self.assertEqual(body["assignment"]["choice"], dict(provider="claude-agent", model="haiku", effort="low",
                                                                harness="claude-agent-sdk via opencode", funding="subscription"))
            profile = routing.ensure_profile(body["job"])
            self.assertEqual(routing_settings.assignment_role(profile["role"], body["assignment"].get("role")), item["label"]["role"])
        self.assertEqual(len({p["body"]["assignment"]["attempt_id"] for p in plan}), 20)
        for n in (0, 21, -1, 2.0):
            with self.assertRaises(safety.Refused):
                canary.plan_assignments(n, "pilot-x", now=1)


class PilotDriverTests(Temp):
    def driver(self, fake, n=3, **kwargs):
        state = self.private(self.tmp / "state")
        ledger = safety.CallLedger.initialize(state / ("calls-%d.jsonl" % len(list(state.iterdir()))))
        return canary.PilotDriver(fake, ledger, n=n, run="pilot-test", **kwargs), ledger

    def test_a_clean_run_completes_every_admission(self):
        fake = Fake(transmit=dict(primary=1, title=None, other=0))
        driver, ledger = self.driver(fake)
        result = driver.run()
        self.assertIsNone(result["abort"])
        self.assertEqual(result["completed"], 3)
        self.assertNotIn("kill_switch", fake.calls)
        self.assertEqual(fake.calls.count("record:complete"), 3)
        self.assertEqual(ledger.summary()["pilot"]["spent"], 4)  # 3 primaries + the first admission's title
        self.assertEqual(ledger.summary()["pilot"]["open_reservations"], 0)
        self.assertEqual(result["settlements"], {"window": 3})

    def assert_aborts(self, fake, reason, **kwargs):
        driver, ledger = self.driver(fake, **kwargs)
        result = driver.run()
        self.assertEqual(result["abort"]["reason"], reason)
        self.assertEqual(fake.calls.count("kill_switch"), 1)
        self.assertFalse(fake.settings["smart_routing"])
        return result, ledger

    def test_cap_breach_aborts_before_sending(self):
        fake = Fake()
        driver, ledger = self.driver(fake)
        ledger.reserve("pilot", "fill", 24)
        result = driver.run()
        self.assertEqual(result["abort"]["reason"], "cap_breach")
        self.assertNotIn("launch", fake.calls)
        self.assertIn("kill_switch", fake.calls)

    def test_more_requests_than_reserved_abort(self):
        self.assert_aborts(Fake(transmit=dict(primary=2, title=0, other=0)), "cap_breach")

    def test_non_haiku_request_aborts(self):
        self.assert_aborts(Fake(off_route=1), "non_haiku_request")

    def test_unresolved_launch_records_uncertain_and_aborts(self):
        fake = Fake(fail=dict(launch="timeout"))
        result, ledger = self.assert_aborts(fake, "unresolved_launch")
        self.assertIn("record:uncertain", fake.calls)
        self.assertEqual(ledger.summary()["pilot"]["open_reservations"], 1)  # exposure stays counted

    def test_funding_violation_aborts(self):
        self.assert_aborts(Fake(settlement=dict(kind="usd_micros", charged=10)), "funding_violation")

    def test_status_anomaly_aborts(self):
        fake = Fake()
        fake.ledger["cost_unknown"] = dict(ids=["x"], more=0)
        self.assert_aborts(fake, "status_anomaly")

    def test_concurrency_above_one_open_attempt_aborts(self):
        fake = Fake()
        fake.ledger["open_attempts"] = 1
        self.assert_aborts(fake, "concurrency")

    def test_recovery_errors_abort(self):
        self.assert_aborts(Fake(fail=dict(attach=False)), "recovery_error")
        self.assert_aborts(Fake(fail=dict(account=True)), "recovery_error")
        self.assert_aborts(Fake(fail=dict(prepare="pending")), "admission_refused")

    def test_max_duration_aborts(self):
        clock = iter([0.0, 0.0, 24 * 3600 + 1.0] + [24 * 3600 + 2.0] * 50)
        self.assert_aborts(Fake(), "max_duration", clock=lambda: next(clock))

    def test_kill_switch_failure_is_reported(self):
        fake = Fake(off_route=1)
        fake.kill_switch = lambda: (fake.calls.append("kill_switch"), dict(state="blocked", reason="x"))[1]
        driver, _ = self.driver(fake)
        result = driver.run()
        self.assertEqual(result["abort"]["kill_switch"], dict(state="blocked", reason="x"))
        self.assertFalse(result["abort"]["kill_switch_ok"])

    def test_n_is_bounded(self):
        with self.assertRaises(safety.Refused):
            self.driver(Fake(), n=21)


if __name__ == "__main__":
    unittest.main()
