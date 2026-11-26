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


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def payload(now, *, idle=False, scoped=False):
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
        self.observations = importlib.import_module("runtime.routing_observations")
        self.council = importlib.import_module("runtime.council")

    def write(self, data):
        self.fixture.write_text(json.dumps(dict(data, trace=str(self.trace))))

    def refresh(self, **kwargs):
        return self.native.refresh(self.settings, command=self.command, environ=self.environ, **kwargs)

    def opencode(self, family="claude", weekly=100):
        rows = [dict(id="invented-reference", integrationID="claude-agent", active=True,
                     value=dict(type="external", methodID="claude-login"))]
        if family == "go":
            rows = [dict(id="invented-key", integrationID="opencode-go", active=True,
                         value=dict(type="key", key="CREDENTIAL-SENTINEL")),
                    dict(id="invented-console", integrationID="opencode", active=True,
                         value=dict(type="oauth", metadata=dict(accountID="invented-account", orgID="invented-org")))]
        meters = {name: dict(percent=weekly if name == "weekly" else 20, limited=False,
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
        self.data["usage"]["rate_limits"]["model_scoped"] = [
            dict(display_name="Invented", utilization=100, resets_at=iso(self.now + 600))]
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now), "quota_exhausted")
        self.assertIsNone(self.openc.quota_problem(self.settings, dict(self.route, model="another-family"), self.now))
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now + 61), "quota_exhausted")
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now + 600))

    def test_account_wide_exhaustion_is_bound_to_the_current_login(self):
        self.data["usage"]["rate_limits"]["five_hour"] = dict(utilization=100, resets_at=iso(self.now + 600))
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        current = self.native.account(self.settings, self.now)
        now = current["observed_at"]
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, now), "quota_exhausted")
        self.assertEqual(self.openc.quota_retry_after(self.settings, self.route, now), self.now + 600 - now)
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, now + 61), "quota_exhausted")
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now + 600))
        self.assertIsNone(self.openc.quota_problem(self.settings, dict(self.route, pool="other"), now))
        self.native._save(self.settings, account=dict(current, usage_at=None))
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, now), "quota_exhausted")
        changed = dict(current, generation="f" * 32)
        self.native._save(self.settings, identity=dict(key=changed["key"], generation=changed["generation"]),
                          account=changed)
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, now))

    def weekly_only(self, percent=100):
        """Claude Code 2.1.323 shape: no five-hour meter, a confirmed unscoped weekly limit reset in 600 s."""
        reset = iso(self.now + 600)
        rate = self.data["usage"]["rate_limits"]
        rate["five_hour"] = None
        rate["seven_day"] = dict(utilization=percent, resets_at=reset)
        rate["limits"] = [dict(kind="weekly_all", scope=None, percent=percent, resets_at=reset)]
        self.write(self.data)

    def test_weekly_only_exhaustion_is_bound_and_expires_at_reset(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=()), dict(state="stored", windows=[], reason="claude_window_missing"))
            self.assertEqual(self.opencode()["state"], "stored")
        account = self.native.account(self.settings, self.now)
        self.assertIsNone(account["usage_at"])
        self.assertEqual(self.native.usable_windows(self.settings, self.now), [])
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now + 61), "quota_exhausted")
        self.assertEqual(self.openc.quota_retry_after(self.settings, self.route, self.now + 61), 600 - 61)
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now + 600))

    def test_scoped_evidence_needs_the_catalog_bound_to_the_account(self):
        self.data["usage"]["rate_limits"]["model_scoped"] = [
            dict(display_name="Invented", utilization=100, resets_at=iso(self.now + 600))]
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        native_catalog = importlib.import_module("runtime.native_catalog")
        with mock.patch.object(native_catalog, "stored", return_value=None):
            self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now))
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now), "quota_exhausted")

    def test_catalog_from_another_generation_blocks_only_account_wide_marks(self):
        self.data["usage"]["rate_limits"]["model_scoped"] = [
            dict(display_name="Invented", utilization=100, resets_at=iso(self.now + 600))]
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=())
            self.opencode()
        native_catalog = importlib.import_module("runtime.native_catalog")
        stored = native_catalog.stored(self.settings, "claude-code")
        other = dict(stored, binding=dict(stored["binding"], connection="account:" + "0" * 32 + ":" + "1" * 32))
        direct = dict(self.route, model="invented-current")
        with mock.patch.object(native_catalog, "stored", return_value=other):
            self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now))
        self.assertEqual(self.openc.quota_problem(self.settings, direct, self.now), "quota_exhausted")
        with mock.patch.object(native_catalog, "stored", return_value=None):
            self.assertIsNone(self.openc.quota_problem(self.settings, direct, self.now))

    def test_missing_quota_record_does_not_gate(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=())
            self.opencode()
        path = self.native._store(self.settings)
        value = json.loads(path.read_text())
        self.assertIn("quota", value)
        del value["quota"]
        path.write_text(json.dumps(value))
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now))

    def test_malformed_or_unsafe_quota_records_are_ignored(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=())
            self.opencode()
        current = self.native.account(self.settings, self.now)
        self.native._save(self.settings, quota=dict(key=current["key"], generation=current["generation"],
                                                    record=dict(meters="bad", inventory=None)))
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now))
        self.native._save(self.settings, quota="bad")
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now))
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=(), force=True)
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now), "quota_exhausted")
        self.native._store(self.settings).chmod(0o644)
        self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now))

    def test_quota_is_refused_when_the_account_switches_between_billing_and_quota_reads(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=())
            self.opencode()
        current = self.native.account(self.settings, self.now)
        changed = dict(current, generation="f" * 32)
        quota = importlib.import_module("runtime.claude_quota")
        evidence = dict(meters={"7d": dict(exhausted=True, reset_at=self.now + 600)}, scopes_complete=False,
                        earliest=self.now - 60, latest=self.now)
        other = quota.merge({}, evidence, now=self.now)
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now), "quota_exhausted")
        original = self.native.exhaustion_left

        def switch(*args, **kwargs):
            self.native._save(self.settings, identity=dict(key=changed["key"], generation=changed["generation"]),
                              account=changed, quota=dict(key=changed["key"], generation=changed["generation"],
                                                          record=other))
            return original(*args, **kwargs)

        with mock.patch.object(self.native, "exhaustion_left", side_effect=switch):
            self.assertIsNone(self.openc.quota_problem(self.settings, self.route, self.now))
        self.assertIsNone(self.native.exhaustion_left(self.settings, self.route, self.now, native="0" * 32))

    def become_second_login(self):
        """Switch the native login and the OpenCode fact to another account whose weekly limit is exhausted."""
        self.data["initialize"]["account"]["email"] = "other@example.invalid"
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=(), force=True)["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")

    def crossing_source(self, pending):
        """The production LocalSource; only its first billing read is followed by the switch in `pending`."""
        class Crossing(self.observations.LocalSource):
            def acquire_billing(self, routes, now):
                return None

            def billing(self, route, now):
                found = super().billing(route, now)
                if pending:
                    pending.pop()()
                return found
        return Crossing(self.settings)

    def test_exhaustion_left_keeps_its_three_argument_form(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=())
            self.opencode()
        native = self.openc._account(self.settings, "claude", self.now)["native"]
        self.assertEqual(self.native.exhaustion_left(self.settings, self.route, self.now), 600)
        self.assertEqual(self.native.exhaustion_left(self.settings, self.route, self.now, native=native), 600)
        self.assertIsNone(self.native.exhaustion_left(self.settings, self.route, self.now, native="0" * 32))
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now, producer="0" * 32),
                         "billing_unverified")

    def test_stable_second_login_with_exhaustion_excludes_as_quota(self):
        with mock.patch("time.time", return_value=self.now):
            self.become_second_login()
        source = self.observations.LocalSource(self.settings)
        kept, excluded = self.observations.eligible([self.route], source, self.now)
        self.assertEqual((kept, excluded), ([], [dict(route=self.route["route"], reason="quota_exhausted")]))

    def test_crossed_login_between_billing_and_quota_is_billing_unverified(self):
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        first = self.native.account(self.settings, self.now)["key"]
        kept, excluded = self.observations.eligible([self.route], self.crossing_source([self.become_second_login]),
                                                    self.now)
        self.assertEqual((kept, excluded), ([], [dict(route=self.route["route"], reason="billing_unverified")]))
        self.assertNotEqual(self.native.account(self.settings, self.now)["key"], first)
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now), "quota_exhausted")

    def test_roster_reports_a_crossed_claude_login_as_billing_unverified(self):
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        models = [dict(provider="claude-agent", id="invented", model=None, family=None, variants=["high"],
                       context=None, released=5)]
        answer = self.council.roster(dict(v=1, models=models), state_dir=self.settings.parent,
                                     source=self.crossing_source([self.become_second_login]), now=self.now)
        self.assertEqual([seat["status"] for seat in answer["seats"]], ["billing_unverified"])

    def test_weekly_exhaustion_is_stored_without_publishing_a_usage_snapshot(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["windows"], [])
        self.assertFalse(self.native.usable(self.settings, self.now))
        self.assertIsNone(self.native.account(self.settings, self.now)["usage_at"])
        self.assertIsNotNone(self.native.account(self.settings, self.now))

    def test_huge_integer_in_one_meter_keeps_valid_weekly_evidence(self):
        rate = self.data["usage"]["rate_limits"]
        rate["five_hour"] = dict(utilization=10 ** 400, resets_at=iso(self.now + 3600))
        rate["seven_day"] = dict(utilization=100, resets_at=iso(self.now + 600))
        rate["limits"] = [dict(kind="weekly_all", scope=None, percent=100, resets_at=iso(self.now + 600))]
        self.write(self.data)
        with self.assertRaisesRegex(self.native.ReadError, "claude_usage_invalid"):
            self.native.parse_usage(self.data["usage"], self.now, self.now)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["reason"], "claude_usage_invalid")
            self.assertEqual(self.opencode()["state"], "stored")
        self.assertEqual(self.openc.quota_problem(self.settings, self.route, self.now), "quota_exhausted")

    def test_received_is_the_usage_answer_not_child_cleanup(self):
        clock = [float(self.now)]
        stop = self.native.lifecycle._stop

        def cleanup(process, normal):
            clock[0] += 5
            stop(process, normal)

        with mock.patch("time.time", side_effect=lambda: clock[0]), \
                mock.patch.object(self.native.lifecycle, "_stop", side_effect=cleanup):
            _init, _use, started, received = self.native.exchange(self.command, self.environ)
        self.assertEqual((started, received, clock[0]), (float(self.now), float(self.now), float(self.now + 5)))

    def test_eligibility_and_roster_follow_the_bound_claude_evidence(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=())
            self.opencode()
            self.opencode("go", weekly=20)
        go = dict(self.route, route="opencode-go-subscription:first", provider="opencode-go", model="glm",
                  pool="opencode-go-subscription", account="opencode-go-workspace")

        class Local(self.observations.LocalSource):
            def acquire_billing(self, routes, now):
                return None

        source = Local(self.settings)
        kept, excluded = self.observations.eligible([self.route, go], source, self.now)
        self.assertEqual([item["route"] for item in kept], [go["route"]])
        self.assertEqual(excluded, [dict(route=self.route["route"], reason="quota_exhausted")])
        models = [dict(provider="claude-agent", id="invented", model=None, family=None, variants=["high"],
                       context=None, released=5),
                  dict(provider="opencode-go", id="glm", model=None, family=None, variants=["high"],
                       context=None, released=5)]
        answer = self.council.roster(dict(v=1, models=models), state_dir=self.settings.parent, source=source,
                                     now=self.now)
        self.assertEqual([seat["status"] for seat in answer["seats"]], ["quota_exhausted", "ok"])

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
