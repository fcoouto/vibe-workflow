"""The `live_modes.py` entry point with doubles: every refusal before anything starts (no cap, no approval, too little
remaining, `--out` placement, model arguments), and whole runs of each mode through `main` (sanitized output, the
fail-closed leak check and withheld file, cleanup of the isolated dir, the ledger left without open reservations)."""
import json
from pathlib import Path

from tests.qualification import live_modes, safety
from tests.qualification.doubles import Temp
from tests.qualification.launcher_doubles import FakeEnv
from tests.test_qualification_drill import Env as DrillEnv
from tests.test_qualification_gateway import Env as GatewayEnv

APPROVAL = dict(by="the user", at="2026-11-10T12:00Z", scope="live session")


def no_check(**_kwargs):
    return None


class Started(Exception):
    pass


class EntryTests(Temp):
    def setUp(self):
        super().setUp()
        self.q = self.private(self.tmp / "q")
        self.ledger_path = self.private(self.tmp / "state") / "calls.jsonl"
        self.ledger = safety.CallLedger.initialize(self.ledger_path)
        self.out = self.private(self.tmp / "results") / "out.json"

    def argv(self, mode, *extra):
        return [mode, "--isolated-dir", str(self.q), "--call-ledger", str(self.ledger_path), "--out", str(self.out), *extra]

    def main(self, argv, factory=None):
        def refuse(*_args, **_kwargs):
            raise Started("an environment was created")
        return live_modes.main(argv, env_factory=factory or refuse, home_check=lambda: None, version_check=no_check)

    def test_nothing_starts_without_an_approved_cap(self):
        for mode, extra in (("launcher-probes", ()), ("drill", ()), ("gateway", ("--gateway-model", "openrouter/m"))):
            with self.subTest(mode):
                self.assertEqual(self.main(self.argv(mode, *extra)), 2)
                self.assertEqual(list(self.q.iterdir()), [])
                self.assertFalse(self.out.exists())
        self.assertEqual([e for e in self.ledger.export()["entries"] if e["kind"] == "reservation"], [])

    def test_too_little_remaining_refuses(self):
        self.ledger.set_cap("isolation", 3, "approved", approval=APPROVAL)
        self.assertEqual(self.main(self.argv("launcher-probes")), 2)  # all probes need 4
        with self.assertRaises(Started):  # zero-call probes need none
            self.main(self.argv("launcher-probes", "--probes", "A1,A2,A3,A4"))

    def test_argument_refusals(self):
        self.ledger.set_cap("gateway", 8, "approved", approval=APPROVAL)
        self.ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
        cases = [self.argv("gateway"), self.argv("gateway", "--gateway-model", "anthropic/claude"),
                 self.argv("drill", "--gateway-model", "openrouter/m"),
                 self.argv("launcher-probes", "--a5-model", "openai/no-variant"),
                 self.argv("launcher-probes", "--probes", "A9"),
                 self.argv("launcher-probes", "--signin-timeout", "5")]
        inside = [self.argv("drill")[:-1] + [str(self.q / "out.json")],
                  self.argv("drill")[:-1] + [str(Path(__file__).resolve().parent / "x.json")]]
        for argv in cases + inside:
            with self.subTest(argv=argv[-2:]):
                self.assertEqual(self.main(argv), 2)

    def env_factory(self, kind, **knobs):
        tmp = self.tmp

        def factory(base, redact, signin_timeout):
            env = kind(tmp / "env", **knobs)
            env.redact, env.pgids = redact, []
            return env
        return factory

    def test_a_launcher_probes_run_writes_sanitized_results_and_cleans_up(self):
        self.ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)
        code = self.main(self.argv("launcher-probes", "--probes", "A1,A2,A3,A4"), self.env_factory(FakeEnv))
        results = json.loads(self.out.read_text())
        self.assertEqual(code, 0, results.get("blockers"))
        self.assertEqual(sorted(results["probes"]), ["A1", "A2", "A3", "A4"])
        self.assertTrue(all(probe["passed"] for probe in results["probes"].values()))
        self.assertFalse(self.q.exists())
        self.assertTrue(results["cleanup"]["verified_by_harness"])
        self.assertEqual(results["_meta"]["purpose"], "isolation")
        self.assertNotIn(str(self.q), self.out.read_text())

    def test_a_drill_run_through_main(self):
        self.ledger.set_cap("drill", 4, "approved", approval=APPROVAL)
        code = self.main(self.argv("drill"), self.env_factory(DrillEnv))
        results = json.loads(self.out.read_text())
        self.assertEqual(code, 0, results.get("blockers"))
        self.assertTrue(results["drill"]["passed"])
        self.assertEqual(results["call_ledger"]["drill"]["spent"], 1)

    def test_a_gateway_run_through_main(self):
        self.ledger.set_cap("gateway", 8, "approved", approval=APPROVAL)
        code = self.main(self.argv("gateway", "--gateway-model", "openrouter/vendor-small"), self.env_factory(GatewayEnv))
        results = json.loads(self.out.read_text())
        self.assertEqual(code, 0, results.get("blockers"))
        self.assertTrue(results["gateway"]["passed"])
        self.assertEqual(results["call_ledger"]["gateway"]["spent"], 2)

    def test_unsanitized_results_are_withheld_and_a_skeleton_written(self):
        self.ledger.set_cap("isolation", 8, "approved", approval=APPROVAL)

        class Leaky(FakeEnv):
            def signin(self, host, note):
                note("leak", "sk-proj-abcdefghijklmnop1234567890")
                return dict(status="complete")
        self.private(self.tmp / "env")
        factory = self.env_factory(Leaky, models=[dict(providerID="openai", id="gpt-6-luna", variants=["none"])])
        code = self.main(self.argv("launcher-probes", "--probes", "A5", "--a5-model", "openai/gpt-6-luna#none"), factory)
        self.assertEqual(code, 3)
        skeleton = self.out.read_text()
        self.assertNotIn("sk-proj", skeleton)
        withheld = list((self.ledger_path.parent / "withheld").iterdir())
        self.assertEqual(len(withheld), 1)
        self.assertEqual(oct(withheld[0].stat().st_mode & 0o777), "0o600")
        self.assertFalse(self.q.exists())
