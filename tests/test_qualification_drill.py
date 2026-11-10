"""The S3.6c drill with doubles: the real `opencode_launcher.launch` paused in a thread on a fake launcher server,
and the real one-shot `scripts/routing.py` (`status`, `record`, `reconcile_subscription`, `drop_debit`) on the drill's
temporary stores. No OpenCode, no model call."""
import json
import threading

from tests.qualification import drill, safety
from tests.qualification.doubles import Temp
from tests.qualification.launcher_doubles import FakeLauncherHost
from tests.qualification.server import Routing

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="runbook part B")


class ThreadChild:
    """The child launch in a thread of this process: `terminate` plays the SIGTERM."""

    def __init__(self, spec, host):
        self.spec, self.stop, self.paused, self.done = spec, threading.Event(), threading.Event(), None
        self.thread = threading.Thread(target=self.body, args=(host,), daemon=True)
        self.thread.start()

    def body(self, host):
        try:
            self.done = drill.run_child(self.spec, host, self.stop.is_set, self.paused.set)
        except Exception as error:  # noqa: BLE001
            self.done = dict(state="error", error=type(error).__name__)

    def wait_paused(self, timeout):
        while not self.paused.wait(0.05):
            if not self.thread.is_alive():
                return False
        return True

    def terminate(self):
        self.stop.set()

    def result(self, timeout=60):
        self.thread.join(timeout)
        return self.done

    def kill(self):
        self.stop.set()


class Env:
    def __init__(self, base, **knobs):
        self.base, self.knobs, self.hosts, self.children = base, knobs, [], []

    def start(self, project, settings, ledger, environ):
        host = FakeLauncherHost(project, self.base / "workdir", settings, ledger, environ, **self.knobs)
        self.hosts.append(host)
        return host

    def child(self, spec, host):
        child = ThreadChild(spec, host)
        self.children.append(child)
        return child

    def routing(self, state):
        return Routing(state.settings, state.ledger, project=state.project)

    def sleep(self, _seconds):
        return None

    def environ(self):
        return dict(HOME=str(self.base / "home"), XDG_DATA_HOME=str(self.base / "data"))


class DrillTests(Temp):
    def setUp(self):
        super().setUp()
        self.base = self.private(self.tmp / "q")
        self.ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        self.ledger.set_cap("drill", 4, "approved", approval=APPROVAL)

    def test_the_whole_drill_passes_against_the_real_routing_command(self):
        env = Env(self.tmp)
        result = drill.run_drill(drill.Drill(self.base, self.ledger, "drill-test", env, pause_timeout=30))
        self.assertIs(result["passed"], True, json.dumps(result, indent=1)[:3000])
        names = [step["step"] for step in result["steps"]]
        self.assertEqual(names, ["state_clean_at_start", "1_dispatched", "1_sigterm", "1_uncertain", "1_complete_refused",
                                 "1_reread", "1_host_usage", "1_released", "1_duplicate", "2_dispatched", "2_sigterm",
                                 "2_zero_calls", "2_uncertain", "2_abandon", "2_debit", "2_drop_debit", "2_clean"])
        host = env.hosts[0]
        self.assertEqual(host.prompts, 1)  # drill 1 sent its prompt; drill 2 stopped before it
        self.assertTrue(host.stopped)
        totals = self.ledger.summary()["drill"]
        self.assertEqual((totals["spent"], totals["open_reservations"]), (1, 0))
        self.assertTrue(all(token is None or token not in json.dumps(result) for token in host.tokens))

    def test_a_launcher_that_never_pauses_blocks_without_signals(self):
        env = Env(self.tmp, healthy=False)  # the launcher refuses before dispatch: no pause is ever reached
        result = drill.run_drill(drill.Drill(self.base, self.ledger, "drill-test", env, pause_timeout=5))
        self.assertIs(result["passed"], False)
        self.assertIn("never reached", result["blocker"])
        self.assertEqual(env.hosts[0].prompts, 0)
        totals = self.ledger.summary()["drill"]
        self.assertEqual(totals["open_reservations"], 0)  # settled (at the reserved count: nothing was readable)

    def test_the_pausing_host_holds_only_at_its_point(self):
        host = FakeLauncherHost(self.tmp / "p", self.tmp / "w")
        stopped, announced = [False], []
        pausing = drill.PausingHost(host, "prompt", lambda: stopped[0], lambda: announced.append(1), sleep=lambda _s: stopped.__setitem__(0, True))
        pausing.request("POST", "/api/session", dict(title="t"))
        self.assertEqual(announced, [])
        with self.assertRaises(drill.Interrupted):
            pausing.request("POST", "/api/session/ses_fake0001/prompt", dict(text="x"))
        self.assertEqual((announced, host.prompts), ([1], 0))
        with self.assertRaises(safety.Refused):
            drill.PausingHost(host, "elsewhere", lambda: True, lambda: None)

    def test_the_state_is_never_the_production_one(self):
        from scripts import routing as command
        with self.assertRaises(safety.Refused):
            drill.ManagedState(command.default_settings().parent / "drill", drill.ROUTE, drill.ACCOUNT)


class ChildProcessTests(Temp):
    def test_the_child_process_turns_sigterm_into_uncertain(self):
        from tests.qualification.launcher_doubles import HttpFake
        base = self.private(self.tmp / "q")
        ledger = safety.CallLedger.initialize(self.private(self.tmp / "state") / "calls.jsonl")
        ledger.set_cap("drill", 4, "approved", approval=APPROVAL)
        tmp = self.tmp

        class ProcessEnv(Env):
            def start(self, project, settings, ledger_path, environ):
                host = HttpFake(FakeLauncherHost(project, tmp / "workdir", settings, ledger_path, environ))
                self.hosts.append(host.fake)
                return host

            def child(self, spec, host):
                child = drill.ChildLaunch(spec, host)
                self.children.append(child)
                return child

        env = ProcessEnv(self.tmp)
        result = drill.run_drill(drill.Drill(base, ledger, "drill-test", env, pause_timeout=60))
        self.assertIs(result["passed"], True, json.dumps(result, indent=1)[:3000])
        self.assertEqual(env.hosts[0].prompts, 1)
        self.assertTrue(all(child.process.poll() is not None for child in env.children))
