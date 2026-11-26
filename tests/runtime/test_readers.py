"""Reader extraction must preserve identity, quiet metadata and refuse-only meters."""
from datetime import datetime, timezone
import faulthandler
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "select.py"


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
        original = self.native._bound_exhaustion

        def switch(*args, **kwargs):
            self.native._save(self.settings, identity=dict(key=changed["key"], generation=changed["generation"]),
                              account=changed, quota=dict(key=changed["key"], generation=changed["generation"],
                                                          record=other))
            return original(*args, **kwargs)

        with mock.patch.object(self.native, "_bound_exhaustion", side_effect=switch):
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

    def switch_native_login(self):
        """Switch only the native login to another account whose weekly limit is exhausted; OpenCode keeps its fact."""
        self.data["initialize"]["account"]["email"] = "other@example.invalid"
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=(), force=True)["state"], "stored")

    def gate_source(self, pending):
        """The production LocalSource; `pending` runs right after the quota gate's own account read."""
        opencode_account = importlib.import_module("runtime.opencode_account")

        class Gate(self.observations.LocalSource):
            def acquire_billing(self, routes, now):
                return None

            def quota_for_billing(self, route, now, fact):
                original = opencode_account._account

                def after_read(*args, **kwargs):
                    found = original(*args, **kwargs)
                    if pending:
                        pending.pop()()
                    return found
                with mock.patch.object(opencode_account, "_account", side_effect=after_read):
                    return super().quota_for_billing(route, now, fact)
        return Gate(self.settings)

    def test_native_switch_after_the_gate_read_is_billing_unverified(self):
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        pending = [self.switch_native_login]
        kept, excluded = self.observations.eligible([self.route], self.gate_source(pending), self.now)
        self.assertEqual(pending, [])
        self.assertEqual((kept, excluded), ([], [dict(route=self.route["route"], reason="billing_unverified")]))

    def test_roster_reports_a_native_switch_after_the_gate_read_as_billing_unverified(self):
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        models = [dict(provider="claude-agent", id="invented", model=None, family=None, variants=["high"],
                       context=None, released=5)]
        answer = self.council.roster(dict(v=1, models=models), state_dir=self.settings.parent,
                                     source=self.gate_source([self.switch_native_login]), now=self.now)
        self.assertEqual([seat["status"] for seat in answer["seats"]], ["billing_unverified"])

    def test_stable_proved_account_without_quota_stays_eligible(self):
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        source = self.observations.LocalSource(self.settings)
        kept, excluded = self.observations.eligible([self.route], source, self.now)
        self.assertEqual(excluded, [])
        self.assertEqual([item["route"] for item in kept], [self.route["route"]])
        answer = self.council.roster(dict(v=1, models=[dict(provider="claude-agent", id="invented", model=None,
                                                            family=None, variants=["high"], context=None,
                                                            released=5)]),
                                     state_dir=self.settings.parent, source=self.gate_source([]), now=self.now)
        self.assertEqual([seat["status"] for seat in answer["seats"]], ["ok"])

    def test_bound_exhaustion_reports_binding_and_remaining_separately(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=())
            self.opencode()
        native = self.openc._account(self.settings, "claude", self.now)["native"]
        self.assertEqual(self.native._bound_exhaustion(self.settings, self.route, self.now, native),
                         (True, 600))
        self.assertEqual(self.native._bound_exhaustion(self.settings, self.route, self.now, "0" * 32),
                         (False, None))
        current = self.native.account(self.settings, self.now)
        quota = importlib.import_module("runtime.claude_quota")
        bad = quota.merge({}, dict(meters={"7d": dict(exhausted=True, reset_at=self.now + 600)},
                                   scopes_complete=False, earliest=self.now - 60, latest=self.now), now=self.now)
        self.native._save(self.settings, quota=dict(key=current["key"], generation=current["generation"],
                                                    record=dict(bad, meters="malformed")))
        self.assertEqual(self.native._bound_exhaustion(self.settings, self.route, self.now, native), (True, None))

    def test_unusable_quota_row_on_a_matching_account_is_not_billing_unverified(self):
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.refresh(need=(), force=True)
            self.opencode()
        current = self.native.account(self.settings, self.now)
        self.native._save(self.settings, quota=dict(key=current["key"], generation=current["generation"],
                                                    record="malformed"))
        kept, excluded = self.observations.eligible([self.route], self.observations.LocalSource(self.settings),
                                                    self.now)
        self.assertEqual(excluded, [])
        self.assertEqual(len(kept), 1)

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

    def add_models(self, *pairs):
        self.data["initialize"]["models"] += [dict(value=alias, resolvedModel=resolved, supportsEffort=False)
                                              for alias, resolved in pairs]

    def baseline(self):
        """A proved OpenCode Claude login with a healthy reading and its stored catalog; the fixture is native again.
        It is observed before the probes, since only a provably newer observation may replace its meters."""
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now - 120):
            self.assertEqual(self.refresh(need=())["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        self.write(self.data)

    def probe(self, reset_at=None):
        return self.openc.probe_claude_limit(self.settings, reset_at=self.now + 3600 if reset_at is None else reset_at,
                                             command=self.command, environ=self.environ)

    def probe_requests(self):
        return [json.loads(line)["request"]["subtype"] for line in self.trace.read_text().splitlines()]

    def quota_state(self):
        return self.native._state(self.settings).get("quota")

    def clear_cooldown(self):
        self.cooldown_path().unlink(missing_ok=True)

    def cooldown_path(self):
        return self.native._store(self.settings).parent / "claude-limit-probe.json"

    def write_cooldown(self, accounts):
        self.write_raw_cooldown(json.dumps(dict(version=1, accounts=accounts)))

    def write_raw_cooldown(self, raw):
        """Private, as the probe writes it: an unsafe mode would fail for the wrong reason."""
        self.cooldown_path().write_text(raw)
        self.cooldown_path().chmod(0o600)

    def acquire_as(self, email, at):
        """Ordinary acquisition under another login at `at`: the native read, then OpenCode's fact of it. Neither
        touches the probe's cooldown."""
        self.data["initialize"]["account"]["email"] = email
        self.write(self.data)
        with mock.patch("time.time", return_value=at):
            self.assertEqual(self.refresh(need=(), force=True)["state"], "stored")
            self.assertEqual(self.opencode()["state"], "stored")
        self.write(self.data)

    def test_cooldown_is_kept_per_native_account_across_an_a_b_a_sequence(self):
        self.baseline()
        self.trace.unlink(missing_ok=True)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.acquire_as("other@example.invalid", self.now + 1)
        with mock.patch("time.time", return_value=self.now + 2):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.acquire_as("invented@example.invalid", self.now + 3)
        before = self.trace.read_text()
        with mock.patch("time.time", return_value=self.now + 3):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertEqual(self.trace.read_text(), before)
        with mock.patch("time.time", return_value=self.now + 60):
            self.assertEqual(self.probe(), dict(state="stored"))

    def test_cooldown_holds_at_most_eight_live_accounts_and_never_evicts(self):
        self.baseline()
        current = self.native.account(self.settings, self.now)["key"]
        seven = {f"{number:032x}": self.now for number in range(1, 8)}
        self.write_cooldown(seven)
        self.trace.unlink(missing_ok=True)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.assertEqual(len(json.loads(self.cooldown_path().read_text())["accounts"]), 8)
        self.write_cooldown(dict(seven, **{f"{8:032x}": self.now}))
        before = self.cooldown_path().read_text()
        self.trace.unlink(missing_ok=True)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertFalse(self.trace.exists())
        self.assertEqual(self.cooldown_path().read_text(), before)
        self.write_cooldown({f"{number:032x}": self.now for number in range(1, 8)} | {current: self.now - 10})
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertFalse(self.trace.exists())
        self.write_cooldown({f"{number:032x}": self.now for number in range(1, 9)})
        with mock.patch("time.time", return_value=self.now + 60):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.assertEqual(json.loads(self.cooldown_path().read_text())["accounts"], {current: self.now + 60})

    def test_negative_elapsed_fails_closed_for_its_own_account_only(self):
        self.baseline()
        current = self.native.account(self.settings, self.now)["key"]
        self.write_cooldown({current: self.now + 5})
        self.trace.unlink(missing_ok=True)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="skipped"))
        with mock.patch("time.time", return_value=self.now + 64):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertFalse(self.trace.exists())
        with mock.patch("time.time", return_value=self.now + 65):
            self.assertEqual(self.probe(), dict(state="stored"))

    def test_malformed_cooldown_map_fails_without_a_native_read_or_repair(self):
        self.baseline()
        current = self.native.account(self.settings, self.now)["key"]
        live = f"{1:032x}"
        bad = [
            json.dumps(dict(version=2, accounts={})), json.dumps(dict(version=1)),
            json.dumps(dict(version=1, accounts=[])), json.dumps(dict(version=1, accounts={"bad": self.now})),
            json.dumps(dict(version=1, accounts={live: "x"})), json.dumps(dict(version=1, accounts={live: True})),
            json.dumps(dict(version=1, accounts={live: self.now}, key=current)),
            json.dumps(dict(version=1, accounts={f"{number:032x}": self.now for number in range(1, 10)})),
            json.dumps(dict(version=1, key=current, started_at=self.now)), "[]",
        ]
        self.trace.unlink(missing_ok=True)
        for raw in bad:
            with self.subTest(raw=raw[:60]):
                self.write_raw_cooldown(raw)
                with mock.patch("time.time", return_value=self.now):
                    self.assertEqual(self.probe(), dict(state="failed"))
                self.assertEqual(self.cooldown_path().read_text(), raw)
                self.assertFalse(self.trace.exists())

    def test_probe_publishes_confirmed_weekly_exhaustion_under_the_proved_login(self):
        self.baseline()
        self.weekly_only()
        self.trace.unlink(missing_ok=True)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.assertEqual(self.probe_requests(), ["initialize", "get_usage"])
        source = self.observations.LocalSource(self.settings)
        kept, excluded = self.observations.eligible([self.route], source, self.now)
        self.assertEqual((kept, excluded), ([], [dict(route=self.route["route"], reason="quota_exhausted")]))

    def test_probe_pauses_only_the_scope_it_confirms_and_the_hint_is_not_a_meter_bound(self):
        self.add_models(("other", "other-current"))
        self.baseline()
        self.data["usage"]["rate_limits"]["model_scoped"] = [
            dict(display_name="Other", utilization=100, resets_at=iso(self.now + 7200))]
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(reset_at=self.now + 3600), dict(state="stored"))
        self.assertEqual(self.native.exhaustion_left(self.settings, dict(self.route, model="other"), self.now), 7200)
        self.assertIsNone(self.native.exhaustion_left(self.settings, self.route, self.now))

    def test_one_observation_can_apply_two_exhausted_scopes(self):
        self.add_models(("other", "other-current"), ("third", "third-current"))
        self.baseline()
        self.data["usage"]["rate_limits"]["model_scoped"] = [
            dict(display_name="Other", utilization=100, resets_at=iso(self.now + 7200)),
            dict(display_name="Third", utilization=100, resets_at=iso(self.now + 3600))]
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.assertEqual(self.native.exhaustion_left(self.settings, dict(self.route, model="other"), self.now), 7200)
        self.assertEqual(self.native.exhaustion_left(self.settings, dict(self.route, model="third"), self.now), 3600)

    def test_probe_classifies_scopes_from_the_stored_catalog_only(self):
        self.add_models(("other", "other-current"))
        self.baseline()
        self.data["usage"]["rate_limits"]["model_scoped"] = [
            dict(display_name="Other", utilization=100, resets_at=iso(self.now + 7200))]
        self.data["initialize"]["models"] = [dict(value="renamed", resolvedModel="renamed-current",
                                                  supportsEffort=False)]
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.assertEqual(self.native.exhaustion_left(self.settings, dict(self.route, model="other"), self.now), 7200)
        native_catalog = importlib.import_module("runtime.native_catalog")
        stored = native_catalog.stored(self.settings, "claude-code")
        self.assertEqual([item["alias"] for item in stored["models"]], ["invented", "other"])

    def test_absent_catalog_still_permits_a_valid_weekly_account_wide_mark(self):
        self.baseline()
        self.weekly_only()
        native_catalog = importlib.import_module("runtime.native_catalog")
        with mock.patch("time.time", return_value=self.now), \
                mock.patch.object(native_catalog, "stored", return_value=None):
            self.assertEqual(self.probe(), dict(state="stored"))
        self.assertEqual(self.native.exhaustion_left(self.settings, self.route, self.now), 600)

    def test_probe_cooldown_skips_before_sixty_seconds_on_a_backwards_clock_and_across_generations(self):
        self.baseline()
        self.weekly_only()
        current = self.native.account(self.settings, self.now)
        key, first, second = current["key"], current["generation"], "e" * 32

        def run(at, generation=first, owner=key):
            with mock.patch("time.time", return_value=at):
                return self.native.probe_limit(self.settings, expected=(owner, generation), reset_at=self.now + 3600,
                                               command=self.command, environ=self.environ)
        self.assertEqual(run(self.now), dict(state="stored"))
        self.assertEqual(run(self.now + 59), dict(state="skipped"))
        self.assertEqual(run(self.now + 60), dict(state="stored"))
        self.assertEqual(run(self.now + 10), dict(state="skipped"))
        self.native._save(self.settings, identity=dict(key=key, generation=second),
                          account=dict(current, generation=second, observed_at=self.now + 90))
        self.assertEqual(run(self.now + 90, second), dict(state="skipped"))
        self.assertEqual(run(self.now + 120, second), dict(state="stored"))
        self.write_cooldown({"d" * 32: self.now + 120})
        self.assertEqual(run(self.now + 121, second), dict(state="stored"))

    def test_probe_limit_starts_only_for_the_login_it_is_given(self):
        self.baseline()
        self.weekly_only()
        current = self.native.account(self.settings, self.now)
        self.trace.unlink(missing_ok=True)
        with mock.patch("time.time", return_value=self.now):
            for expected in ((current["key"], "f" * 32), ("e" * 32, current["generation"])):
                self.assertEqual(self.native.probe_limit(self.settings, expected=expected, reset_at=self.now + 3600,
                                                         command=self.command, environ=self.environ),
                                 dict(state="skipped"))
            self.assertEqual(self.native.probe_limit(self.settings, expected=("bad", current["generation"]),
                                                     reset_at=self.now + 3600, command=self.command,
                                                     environ=self.environ), dict(state="failed"))
        self.assertFalse(self.trace.exists())

    def test_probe_accepts_the_allowed_reset_range_and_rejects_its_edges_outside(self):
        self.baseline()
        self.weekly_only()
        margin = self.storage.CLOCK_MARGIN
        with mock.patch("time.time", return_value=self.now):
            for offset, expected in [(0, "skipped"), (1, "stored"), (7 * 86400 + margin, "stored"),
                                     (7 * 86400 + margin + 1, "skipped")]:
                with self.subTest(offset=offset):
                    self.clear_cooldown()
                    self.trace.unlink(missing_ok=True)
                    self.assertEqual(self.probe(reset_at=self.now + offset), dict(state=expected))
                    self.assertEqual(self.trace.exists(), expected == "stored")

    def test_probe_hint_expiring_during_its_exchange_publishes_nothing(self):
        self.baseline()
        self.weekly_only()
        clock, before, real = [self.now], self.quota_state(), self.native.exchange

        def expire(*args, **kwargs):
            clock[0] = self.now + 3600
            return real(*args, **kwargs)
        with mock.patch("time.time", side_effect=lambda: clock[0]), \
                mock.patch.object(self.native, "exchange", side_effect=expire):
            self.assertEqual(self.probe(reset_at=self.now + 3600), dict(state="skipped"))
        self.assertEqual(self.quota_state(), before)

    def test_probe_without_a_current_binding_skips_before_any_native_read(self):
        self.settings.parent.mkdir(mode=0o700)
        self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertFalse(self.trace.exists())

    def test_probe_with_an_expired_or_pending_binding_skips(self):
        self.baseline()
        self.weekly_only()
        self.trace.unlink(missing_ok=True)
        with mock.patch("time.time", return_value=self.now + self.observations.FRESH_SECONDS + 1):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.native._save(self.settings, account=dict(pending=True, generation="a" * 32))
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertFalse(self.trace.exists())

    def test_probe_skips_unconfirmed_or_missing_usable_evidence_without_publishing(self):
        self.baseline()
        before = self.quota_state()
        rate = self.data["usage"]["rate_limits"]
        for change in (dict(rate_limits_available=False),
                       dict(rate_limits=dict(rate, five_hour=None, seven_day=None,
                                             limits=[dict(kind="unknown_kind", scope=None)]))):
            with self.subTest(change=sorted(change)):
                self.clear_cooldown()
                self.data["usage"].update(change)
                self.write(self.data)
                with mock.patch("time.time", return_value=self.now):
                    self.assertEqual(self.probe(), dict(state="skipped"))
                self.assertEqual(self.quota_state(), before)
                self.data["usage"].update(rate_limits_available=True, rate_limits=rate)

    def test_probe_refuses_relative_symlinked_and_malformed_private_state(self):
        self.baseline()
        self.weekly_only()
        before = self.quota_state()
        self.trace.unlink(missing_ok=True)
        link = self.root / "link"
        link.symlink_to(self.settings.parent)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.openc.probe_claude_limit(Path("relative-state/router.json"), reset_at=self.now + 3600,
                                                           command=self.command, environ=self.environ),
                             dict(state="failed"))
            self.assertEqual(self.openc.probe_claude_limit(link / "router.json", reset_at=self.now + 3600,
                                                           command=self.command, environ=self.environ),
                             dict(state="failed"))
            original = self.native._store(self.settings).read_text()
            self.native._store(self.settings).write_text(json.dumps(dict(version=2)))
            self.assertEqual(self.probe(), dict(state="failed"))
        self.native._store(self.settings).write_text(original)
        self.assertEqual(self.quota_state(), before)
        self.assertFalse(self.trace.exists())

    def test_probe_refuses_unsafe_or_malformed_cooldown_state(self):
        self.baseline()
        self.weekly_only()
        self.trace.unlink(missing_ok=True)
        self.write_cooldown({"bad": 1})
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="failed"))
        self.write_cooldown({self.native.account(self.settings, self.now)["key"]: self.now - 3600})
        self.cooldown_path().chmod(0o644)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="failed"))
        self.assertFalse(self.trace.exists())

    def test_probe_unsupported_identity_and_nonzero_telemetry_fail_without_publishing(self):
        self.baseline()
        self.weekly_only()
        before, current = self.quota_state(), self.native.account(self.settings, self.now)
        self.data["initialize"]["account"]["apiProvider"] = "bedrock"
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="failed"))
        self.assertEqual(self.native.account(self.settings, self.now), current)
        self.assertEqual(self.quota_state(), before)
        self.clear_cooldown()
        self.data["initialize"]["account"]["apiProvider"] = "firstParty"
        self.data["usage"]["session"]["total_cost_usd"] = 0.01
        self.write(self.data)
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="failed"))
        self.assertEqual(self.quota_state(), before)

    def test_a_proved_different_login_ends_only_the_still_current_binding(self):
        self.baseline()
        self.data["initialize"]["account"]["email"] = "other@example.invalid"
        self.weekly_only()
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertIsNone(self.native.account(self.settings, self.now))
        self.assertIsNone(self.openc._account(self.settings, "claude", self.now))

    def test_a_login_rotated_during_the_probe_is_not_invalidated(self):
        self.baseline()
        self.data["initialize"]["account"]["email"] = "other@example.invalid"
        self.weekly_only()
        real = self.native.exchange

        def rotate(*args, **kwargs):
            result = real(*args, **kwargs)
            current = self.native.account(self.settings, self.now)
            self.native._save(self.settings, identity=dict(key="d" * 32, generation="c" * 32),
                              account=dict(current, key="d" * 32, generation="c" * 32))
            return result
        with mock.patch("time.time", return_value=self.now), \
                mock.patch.object(self.native, "exchange", side_effect=rotate):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertEqual(self.native.account(self.settings, self.now)["key"], "d" * 32)

    def test_a_binding_rotated_during_the_exchange_skips_the_commit(self):
        self.baseline()
        self.weekly_only()
        before, real = self.quota_state(), self.native.exchange

        def rotate(*args, **kwargs):
            result = real(*args, **kwargs)
            current = self.native.account(self.settings, self.now)
            self.native._save(self.settings, identity=dict(key="d" * 32, generation="c" * 32),
                              account=dict(current, key="d" * 32, generation="c" * 32))
            return result
        with mock.patch("time.time", return_value=self.now), \
                mock.patch.object(self.native, "exchange", side_effect=rotate):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertEqual(self.native.account(self.settings, self.now)["key"], "d" * 32)
        self.assertEqual(self.quota_state(), before)

    def test_probe_skips_when_the_native_login_changes_between_its_reads(self):
        self.baseline()
        self.weekly_only()
        self.trace.unlink(missing_ok=True)
        real = self.native.account

        def rotate_after_read(settings, now):
            found = real(settings, now)
            self.native._save(settings, identity=dict(key="d" * 32, generation="c" * 32),
                              account=dict(found, key="d" * 32, generation="c" * 32))
            return found
        with mock.patch("time.time", return_value=self.now), \
                mock.patch.object(self.native, "account", side_effect=rotate_after_read):
            self.assertEqual(self.probe(), dict(state="skipped"))
        self.assertFalse(self.trace.exists())

    def test_a_running_probe_blocks_no_ordinary_read_and_a_second_consumer_starts_no_exchange(self):
        self.baseline()
        self.weekly_only()
        entered, release, calls, results = threading.Event(), threading.Event(), [], []
        real = self.native.exchange

        def gated(*args, **kwargs):
            calls.append(args)
            if len(calls) > 1:
                raise AssertionError("second native exchange")
            entered.set()
            release.wait(10)
            return real(*args, **kwargs)

        class Local(self.observations.LocalSource):
            def acquire_billing(self, routes, now):
                return None
        models = [dict(provider="claude-agent", id="invented", model=None, family=None, variants=["high"],
                       context=None, released=5)]
        with mock.patch("time.time", return_value=self.now), \
                mock.patch.object(self.native, "exchange", side_effect=gated):
            worker = threading.Thread(target=lambda: results.append(self.probe()))
            worker.start()
            self.assertTrue(entered.wait(10))
            self.assertIsNotNone(self.openc.billing_fact(self.settings, self.route, self.now))
            answer = self.council.roster(dict(v=1, models=models), state_dir=self.settings.parent,
                                         source=Local(self.settings), now=self.now)
            self.assertEqual([seat["status"] for seat in answer["seats"]], ["ok"])
            self.assertTrue(worker.is_alive())
            self.assertEqual(self.probe(), dict(state="skipped"))
            release.set()
            worker.join(10)
        self.assertEqual(results, [dict(state="stored")])
        self.assertEqual(len(calls), 1)

    def test_a_slow_usage_exchange_blocks_no_second_probe_or_selection(self):
        selector_tests = importlib.import_module("tests.runtime.test_selector")
        selector = importlib.import_module("runtime.selector")
        scores = importlib.import_module("runtime.scores")
        faulthandler.dump_traceback_later(60, exit=True)  # watchdog: a missing barrier handshake must not hang the run
        self.addCleanup(faulthandler.cancel_dump_traceback_later)
        self.data["initialize"]["models"] = [dict(value="claude-opus-5-5", resolvedModel="claude-opus-5-5",
                                                  supportsEffort=False)]
        self.baseline()
        fetch = selector_tests.Fetch(selector_tests.BODY)
        self.assertEqual(scores.refresh(self.settings.parent, [], self.now, fetch=fetch), "stored")
        reached, release = self.settings.parent / "reached.fifo", self.settings.parent / "release.fifo"
        os.mkfifo(reached)
        os.mkfifo(release)
        self.write(dict(self.data, mode="barrier", reached=str(reached), release=str(release)))
        self.trace.unlink(missing_ok=True)

        class Local(self.observations.LocalSource):
            def acquire_billing(self, routes, now):
                return None
        request = dict(v=2, work_kind="review", task=dict(files="1-2", spec="exact", risk="reversible"),
                       models=[selector_tests.model("claude-agent", "claude-opus-5-5")])
        results, reached_seen = [], False
        with mock.patch("time.time", return_value=self.now):
            worker = threading.Thread(target=lambda: results.append(self.probe()))
            worker.start()
            try:
                with open(reached) as signal:  # get_usage is in flight: the child is spawned and blocked on `release`
                    reached_seen = signal.read() == "1"
                self.assertTrue(reached_seen)
                self.assertIsNotNone(self.openc.billing_fact(self.settings, self.route, self.now))
                self.assertEqual(self.probe(), dict(state="skipped"))
                answer = selector.select(request, state_dir=self.settings.parent, source=Local(self.settings),
                                         fetch=fetch, now=self.now)
                self.assertEqual(answer["state"], "selected", answer)
                self.assertEqual(answer["route"]["provider"], "claude-agent")
                self.assertTrue(worker.is_alive())
            finally:
                if reached_seen:
                    with open(release, "w"):
                        pass
                worker.join(10)
        self.assertEqual(results, [dict(state="stored")])
        self.assertEqual(self.probe_requests(), ["initialize", "get_usage"])

    def test_probe_timeout_publishes_nothing(self):
        self.baseline()
        self.weekly_only()
        before = self.quota_state()
        self.write(dict(mode="hang"))
        with mock.patch("time.time", return_value=self.now):
            self.assertEqual(self.openc.probe_claude_limit(self.settings, reset_at=self.now + 3600,
                                                           command=self.command, environ=self.environ, timeout=0.2),
                             dict(state="failed"))
        self.assertEqual(self.quota_state(), before)

    def test_probe_interruption_publishes_nothing_and_releases_its_lock(self):
        self.baseline()
        self.weekly_only()
        before = self.quota_state()
        with mock.patch("time.time", return_value=self.now), \
                mock.patch.object(self.native, "exchange", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.probe()
        self.assertEqual(self.quota_state(), before)
        with self.storage._locked(self.native._store(self.settings), block=False,
                                  lock=".claude-limit-probe.lock") as path:
            self.assertIsNotNone(path)


class ClaudeLimitCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)

    def run_cli(self, raw, state=None, cwd=None):
        done = subprocess.run([sys.executable, "-B", str(SCRIPT), "--state-dir", str(state or self.state),
                               "--claude-limit"], input=raw, capture_output=True, timeout=60,
                              cwd=cwd or self.root, env=dict(os.environ, VCW_TEST_HOME=str(self.root)))
        return done.stdout

    def test_valid_request_without_a_binding_answers_only_the_state(self):
        request = json.dumps(dict(reset_at=int(time.time()) + 3600)).encode()
        self.assertEqual(self.run_cli(request), b'{"state":"skipped"}\n')

    def test_malformed_requests_answer_failed_only(self):
        for raw in (b"not json", b'{"reset_at": "3600"}', b'{"reset_at": true}', b'{"reset_at": 1.5}', b"{}", b"[1]",
                    b'{"reset_at": 1, "extra": 1}', b"x" * 65537):
            with self.subTest(raw=raw[:24]):
                self.assertEqual(self.run_cli(raw), b'{"state":"failed"}\n')

    def test_relative_symlinked_missing_or_unsafe_state_fails(self):
        request = json.dumps(dict(reset_at=int(time.time()) + 3600)).encode()
        link = self.root / "link"
        link.symlink_to(self.state)
        self.assertEqual(self.run_cli(request, state=link), b'{"state":"failed"}\n')
        self.assertEqual(self.run_cli(request, state=self.root / "missing"), b'{"state":"failed"}\n')
        self.assertEqual(self.run_cli(request, state=Path("state"), cwd=self.root), b'{"state":"failed"}\n')
        (self.state / "claude-account.json").write_text(json.dumps(dict(version=2)))
        self.assertEqual(self.run_cli(request), b'{"state":"failed"}\n')


if __name__ == "__main__":
    unittest.main()
