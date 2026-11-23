"""Reader extraction must preserve identity, quiet metadata and refuse-only meters."""
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock


def payload(now, *, idle=False, scoped=False):
    iso = lambda at: datetime.fromtimestamp(at, timezone.utc).isoformat()
    rate = dict(five_hour=dict(utilization=0 if idle else 20, resets_at=None if idle else iso(now + 3600)),
                seven_day=dict(utilization=30, resets_at=iso(now + 86400)),
                extra_usage=dict(is_enabled=False), limits=[], model_scoped=[])
    if scoped:
        rate["model_scoped"] = [dict(display_name="Invented", utilization=100, resets_at=iso(now + 3600))]
    return dict(mode="claude",
                initialize=dict(account=dict(email="invented@example.invalid", apiProvider="firstParty"),
                                claude_code_version="test", models=[dict(value="invented", resolvedModel="invented-current",
                                    supportsEffort=True, supportedEffortLevels=["medium", "high"])]),
                usage=dict(subscription_type="max", session=dict(total_cost_usd=0, total_api_duration_ms=0,
                            model_usage={}), rate_limits_available=True, rate_limits=rate, behaviors=None))


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.settings = self.root / "state/router.json"
        try:
            self.storage = importlib.import_module("runtime.storage")
            self.continuity = importlib.import_module("runtime.continuity")
            self.native = importlib.import_module("runtime.claude_usage")
            self.openc = importlib.import_module("runtime.opencode_account")
        except (ImportError, AttributeError):
            self.fail("standalone reader dependencies are missing")
        self.now = int(time.time())
        self.data = payload(self.now)
        self.fixture = self.root / "transport.json"
        self.trace = self.root / "trace.jsonl"
        self.command = [sys.executable, str(Path(__file__).with_name("native_reader.py")), str(self.fixture)]
        self.environ = dict(PATH="/usr/bin:/bin", HOME=str(self.root))
        self.write(self.data)
        self.route = dict(route="opencode-claude-subscription:first", provider="claude-agent", model="invented",
                          effort="medium", billing_class="included_subscription",
                          pool="claude-subscription", account="claude-plan")

    def write(self, data):
        self.fixture.write_text(json.dumps(dict(data, trace=str(self.trace))))

    def refresh(self, **kwargs):
        return self.native.refresh(self.settings, command=self.command, environ=self.environ, **kwargs)

    def opencode(self, family="claude"):
        rows = [dict(id="invented-reference", integrationID="claude-agent", active=True,
                     value=dict(type="external", methodID="claude-login"))]
        if family == "go":
            rows = [dict(id="invented-key", integrationID="opencode-go", active=True,
                         value=dict(type="key", key="CREDENTIAL-SENTINEL")),
                    dict(id="invented-console", integrationID="opencode", active=True,
                         value=dict(type="oauth", metadata=dict(accountID="invented-account", orgID="invented-org")))]
        meters = {name: dict(percent=100 if name == "weekly" else 20, limited=False,
                             resetsAt=(self.now + 3600) * 1000) for name in ("rolling", "weekly", "monthly")}
        self.write(dict(mode="opencode", answers={
            "/api/credential": dict(data=rows),
            "/api/provider": dict(data=[dict(id="opencode-go", headers={"x-opencode-org-id": "invented-org"})]),
            "/api/experimental/usage/limits": dict(data=dict(workspace=dict(id="invented-org"), go=meters))}))
        return self.openc.refresh(self.settings, families=(family,), command=self.command, environ=self.environ)

    def test_idle_meter_keeps_overflow_without_reusing_old_rows(self):
        self.assertEqual(self.refresh()["state"], "stored")
        self.write(payload(self.now, idle=True))
        self.assertEqual(self.refresh(force=True, need=()),
                         dict(state="stored", windows=[], reason="claude_usage_unconfirmed"))
        account = self.native.account(self.settings, int(time.time()))
        self.assertEqual(account["overflow"], "disabled")
        self.assertIsNone(account["usage_at"])
        self.assertEqual(self.native.usable_windows(self.settings, int(time.time())), [])

    def test_native_exchange_is_quiet_and_metadata_is_cached(self):
        self.assertEqual(self.refresh(need=())["state"], "stored")
        self.assertEqual(self.refresh(need=()), dict(state="fresh"))
        requests = [json.loads(line)["request"] for line in self.trace.read_text().splitlines()]
        self.assertEqual(requests, [dict(subtype="initialize", hooks=None),
                                    dict(subtype="get_usage", skip_behaviors=True)])
        stored = "".join(path.read_text() for path in self.settings.parent.glob("*.json"))
        self.assertNotIn("invented@example.invalid", stored)

    def test_overflow_projection_is_bound_to_the_same_native_login(self):
        self.assertEqual(self.refresh(need=())["state"], "stored")
        self.assertEqual(self.opencode()["state"], "stored")
        for overflow, expected in [("disabled", "provider_blocked"), ("enabled", "provider_managed"),
                                   ("unknown", "unknown")]:
            current = self.native.account(self.settings, int(time.time()))
            self.native._save(self.settings, account=dict(current, overflow=overflow))
            self.assertEqual(self.openc.billing_fact(self.settings, self.route, int(time.time()))["paid_crossover"],
                             expected)
        changed = dict(current, key="f" * 32)
        with mock.patch.object(self.openc, "_native_account", return_value=changed):
            self.assertIsNone(self.openc.billing_fact(self.settings, self.route, int(time.time())))

    def test_scoped_exhaustion_is_fresh_account_bound_and_refuse_only(self):
        self.write(payload(self.now, scoped=True))
        self.assertEqual(self.refresh(need=())["state"], "stored")
        self.assertEqual(self.opencode()["state"], "stored")
        now = int(time.time())
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, now), "quota_exhausted")
        self.assertIsNone(self.openc.quota_problem(self.settings, dict(self.route, model="another-family"), now))
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, now + 61))

    def test_account_wide_exhaustion_excludes_only_current_fresh_bound_windows(self):
        self.data["usage"]["rate_limits"]["five_hour"]["utilization"] = 100
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        current = self.native.account(self.settings, self.now)
        now = current["observed_at"]
        rows = self.native.usable_windows(self.settings, now)
        self.assertEqual(now - rows[0]["observed_at"], 60)
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, now), "quota_exhausted")
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, now + 1))
        self.assertIsNone(self.openc.quota_problem(self.settings, dict(self.route, pool="other"), now))
        self.native._save(self.settings, account=dict(current, usage_at=None))
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, now))
        self.native._save(self.settings, account=current)
        changed = dict(current, generation="f" * 32)
        self.native._save(self.settings, identity=dict(key=changed["key"], generation=changed["generation"]),
                          account=changed)
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, now))

    def test_go_uses_existing_bound_api_meters_without_storing_a_key(self):
        self.assertEqual(self.opencode("go")["state"], "stored")
        route = dict(self.route, route="opencode-go-subscription:first", provider="opencode-go",
                     pool="opencode-go-subscription", account="opencode-go-workspace")
        now = int(time.time())
        self.assertEqual(self.openc.quota_problem(self.settings, route, now), "quota_exhausted")
        self.assertEqual(self.openc.billing_fact(self.settings, route, now)["paid_crossover"], "unknown")
        self.assertEqual(self.trace.read_text().splitlines(),
                         ["/api/credential", "/api/provider", "/api/experimental/usage/limits"])
        self.assertNotIn("CREDENTIAL-SENTINEL", "".join(p.read_text() for p in self.settings.parent.glob("*.json")))

    def test_wrong_go_workspace_does_not_prove_a_connection(self):
        found = (dict(id="key", value=dict(key="secret")), "account", "org")
        with self.assertRaisesRegex(self.openc.ReadError, "opencode_go_workspace_mismatch"):
            self.openc._go(found, dict(data=[dict(id="opencode-go", headers={"x-opencode-org-id": "other"})]))

    def test_invalid_idle_and_unmodeled_limits_are_not_accepted(self):
        for meter in (dict(utilization=True, resets_at=None), dict(utilization=1, resets_at=None),
                      dict(utilization=0), dict(resets_at=None)):
            with self.subTest(meter=meter):
                use = payload(self.now)["usage"]
                use["rate_limits"]["five_hour"] = meter
                with self.assertRaises(self.native.ReadError):
                    self.native.parse_usage(use, self.now, self.now)
        use = payload(self.now, idle=True)["usage"]
        use["rate_limits"]["limits"] = [dict(kind="monthly", percent=3, resets_at=None)]
        with self.assertRaisesRegex(self.native.ReadError, "claude_limits_unsupported"):
            self.native.parse_usage(use, self.now, self.now)

    def test_inference_telemetry_is_refused_without_repeating_the_read(self):
        self.data["usage"]["session"]["total_cost_usd"] = 0.01
        self.write(self.data)
        self.assertEqual(self.refresh()["reason"], "claude_inference_observed")
        self.assertEqual(self.refresh()["state"], "backoff")
        self.assertEqual(len(self.trace.read_text().splitlines()), 2)
        self.assertIsNone(self.native.account(self.settings, int(time.time())))

    def test_transport_timeout_is_bounded_and_cannot_confirm_an_account(self):
        self.write(dict(mode="hang"))
        started = time.monotonic()
        self.assertEqual(self.refresh(timeout=0.1)["reason"], "claude_timeout")
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNone(self.native.account(self.settings, int(time.time())))

    def test_interrupted_generation_fence_cannot_restore_old_continuity(self):
        c = self.continuity
        state = dict(identity=dict(key="a" * 32, generation="b" * 32),
                     account=dict(key="a" * 32, generation="b" * 32))
        held = {}
        state.update(c._fence(state, held))
        self.assertIsNone(c._proved(state))
        next_held = {}
        state.update(c._fence(state, next_held))
        state.update(c._observe("a" * 32, next_held)(state))
        self.assertNotEqual(next_held["observed"], "b" * 32)


if __name__ == "__main__":
    unittest.main()
