"""Protect ranked selection, caller-owned classification and the no-launch boundary."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from runtime import routing
from runtime import routing_paths
from runtime import selector
from unittest import mock
try:
    from runtime.selector import select
except ImportError:
    select = None


PATHS = ["opencode-claude-subscription", "opencode-chatgpt-subscription", "opencode-go-subscription"]


class Facts:
    def __init__(self):
        self.calls = []
        self.states = {}
        self.exhausted = set()
        self.change = False
        self.reads = {}

    def acquire_billing(self, routes, now):
        self.calls.append(("acquire", len(routes)))

    def billing(self, route, now):
        path = route["path"]
        self.reads[path] = self.reads.get(path, 0) + 1
        extra = self.states.get(path, {})
        return dict(producer="invented:" + ("changed" if self.change and self.reads[path] > 1 else "first"),
                    path=path, account=route["account"], pool=route["pool"],
                    observed_at=now, valid_until=now + 900, paid_crossover="provider_blocked", **extra)

    def quota(self, route, now):
        return "quota_exhausted" if route["path"] in self.exhausted else None


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = Facts()
        self.strategies = [
            dict(strategy_id="first", provider="claude-agent", model="invented-small", effort="medium",
                 harness="claude-agent-sdk via opencode", context_limit=500),
            dict(strategy_id="second", provider="openai", model="invented-other", effort="high",
                 harness="opencode provider openai", context_limit=1000),
            dict(strategy_id="third", provider="opencode-go", model="invented-go", effort="low",
                 harness="opencode provider opencode-go", context_limit=2000)]
        self.config = dict(version=1, enabled=True, paths=PATHS)
        self.table = dict(schema=2, version=1, rows={"review|high|high": self.strategies})

    def write(self):
        for name, value in [("router.json", self.config), ("strategy-table.json", self.table)]:
            path = self.root / name
            path.write_text(json.dumps(value))
            path.chmod(0o600)

    def run_select(self, request=None):
        self.assertIsNotNone(select, "standalone advisory selector is missing")
        return select(request or dict(v=1, work_kind="review"), state_dir=self.root, source=self.source)

    def test_standalone_selector_exists(self):
        self.assertIsNotNone(select, "standalone advisory selector is missing")

    def test_unconfigured_selection_has_no_effects(self):
        absent = self.root / "does-not-exist"
        self.assertIsNotNone(select)
        result = select(dict(v=1, work_kind="review"), state_dir=absent, source=self.source)
        self.assertEqual(result["state"], "no_selection")
        self.assertIsNone(result["route"])
        self.assertFalse(absent.exists())
        self.assertEqual(self.source.calls, [])

    def test_local_readiness_validates_configuration_without_native_acquisition(self):
        with mock.patch("runtime.opencode_account.refresh", side_effect=AssertionError("reader started")):
            absent = self.root / "does-not-exist"
            self.assertEqual(selector.check_setup(state_dir=absent),
                             dict(state="setup_needed", reason="unconfigured"))
            self.assertFalse(absent.exists())
            self.write()
            self.assertEqual(selector.check_setup(state_dir=self.root), dict(state="ready", reason=None))
            self.config["enabled"] = False
            self.write()
            self.assertEqual(selector.check_setup(state_dir=self.root)["reason"], "disabled")
            self.config["enabled"] = True
            self.table["rows"] = {}
            self.write()
            self.assertEqual(selector.check_setup(state_dir=self.root)["reason"], "table_empty")
            self.table["rows"] = {"unusable-key": self.strategies}
            self.write()
            self.assertEqual(selector.check_setup(state_dir=self.root)["state"], "setup_needed")
            self.table["schema"] = 77
            self.write()
            self.assertEqual(selector.check_setup(state_dir=self.root)["reason"], "configuration_invalid")
            (self.root / "router.json").chmod(0o644)
            self.assertEqual(selector.check_setup(state_dir=self.root)["state"], "setup_needed")

    def test_disabled_or_empty_configuration_does_not_acquire(self):
        for enabled, rows in [(False, self.table["rows"]), (True, {})]:
            with self.subTest(enabled=enabled):
                self.config["enabled"], self.table["rows"] = enabled, rows
                self.write()
                self.assertEqual(self.run_select()["state"], "no_selection")
                self.assertEqual(self.source.calls, [])

    def test_invalid_table_version_blocks_readiness_and_selection(self):
        for version in ({"note": "synthetic metadata"}, "1", True, 0, 2**31):
            with self.subTest(version=version):
                self.table["version"] = version
                self.write()
                self.assertEqual(selector.check_setup(state_dir=self.root),
                                 dict(state="setup_needed", reason="configuration_invalid"))
                answer = self.run_select()
                self.assertEqual(answer["state"], "blocked")
                self.assertIsNone(answer["route"])
                self.assertNotIn("ranking", answer)
                self.assertEqual(self.source.calls, [])

    def test_first_ranked_route_and_authority_boundary(self):
        self.write()
        result = self.run_select()
        self.assertEqual(result["state"], "selected")
        self.assertEqual((result["route"]["provider"], result["route"]["model"], result["route"]["effort"]),
                         ("claude-agent", "invented-small", "medium"))
        for field in ("admission", "reservation", "spend_permit"):
            self.assertIs(result[field], False)
        self.assertEqual(result["profile"]["complexity_band"], "high")
        self.assertEqual(result["profile"]["risk_band"], "high")
        self.assertEqual(result["profile"]["review_policy"], "independent")
        self.assertEqual(self.strategies[0]["model"], "invented-small")

    def test_overflow_states_are_excluded_not_relabeled(self):
        self.write()
        original = self.source.billing
        for state in ("provider_managed", "unknown"):
            with self.subTest(state=state):
                self.source.billing = lambda route, now, state=state: dict(
                    original(route, now), paid_crossover=state)
                result = self.run_select()
                self.assertEqual(result["state"], "no_selection")
                self.assertIsNone(result["route"])
                self.assertTrue(result["excluded"])
        self.source.billing = original

    def test_exhausted_route_is_not_selected(self):
        self.write()
        self.source.exhausted.add(PATHS[0])
        self.assertEqual(self.run_select()["route"]["provider"], "openai")

    def test_overflow_ineligible_rows_do_not_hide_a_later_eligible_route(self):
        unknown = [dict(self.strategies[1], strategy_id="unknown-" + str(i)) for i in range(10)]
        self.table["rows"]["review|high|high"] = unknown + [self.strategies[0]]
        original = self.source.billing
        self.source.billing = lambda route, now: dict(
            original(route, now), paid_crossover="unknown" if route["provider"] == "openai" else "provider_blocked")
        self.write()
        result = self.run_select()
        self.assertEqual(result["state"], "selected")
        self.assertEqual(result["route"]["provider"], "claude-agent")
        self.assertEqual(len(result["excluded"]), 10)
        self.assertEqual({item["reason"] for item in result["excluded"]}, {"overflow_not_blocked"})

    def test_bad_or_stale_facts_never_select(self):
        self.write()
        original = self.source.billing
        for change in (dict(account="another-account"), dict(observed_at=int(time.time()) - 1000),
                       dict(valid_until=0), dict(pool="another-pool")):
            with self.subTest(change=change):
                self.source.billing = lambda route, now, change=change: dict(original(route, now), **change)
                self.assertEqual(self.run_select()["state"], "no_selection")
        self.source.billing = original

    def test_changed_identity_is_refused_on_final_recheck(self):
        self.write()
        self.source.change = True
        self.assertEqual(self.run_select()["state"], "no_selection")

    def test_context_and_constraints_only_narrow(self):
        self.write()
        result = self.run_select(dict(v=1, work_kind="review", profile=dict(context_tokens=600)))
        self.assertEqual(result["route"]["model"], "invented-other")
        result = self.run_select(dict(v=1, work_kind="review", constraints=dict(allowed_models=["missing"])))
        self.assertEqual(result["state"], "no_selection")
        result = self.run_select(dict(v=1, work_kind="review", constraints=dict(allowed_models=["invented-go"])))
        self.assertEqual(result["route"]["provider"], "opencode-go")

    def test_foreign_paths_and_gateway_routes_cannot_enter(self):
        self.config["paths"] = ["claude-native-subscription"]
        self.write()
        self.assertEqual(self.run_select()["state"], "blocked")
        self.assertEqual(self.source.calls, [])

    def test_invalid_requests_do_not_echo_or_acquire(self):
        self.write()
        for request in (dict(v=True, work_kind="review"), dict(v=1, work_kind="unknown"),
                        dict(v=1, work_kind="review", credentials="PRIVATE-SENTINEL"),
                        dict(v=1, work_kind="review", profile=dict(context_tokens=True)),
                        dict(v=1, work_kind="review", profile=dict(risk_band=[]))):
            with self.subTest(request=request):
                result = self.run_select(request)
                self.assertEqual(result["state"], "blocked")
                self.assertNotIn("PRIVATE-SENTINEL", json.dumps(result))
        self.assertEqual(self.source.calls, [])

    def test_classification_needs_no_agent_roster(self):
        self.write()
        for kind in ("implementation", "review", "investigation", "planning", "testing", "writing"):
            with self.subTest(kind=kind):
                self.table["rows"] = {kind + "|critical|critical": self.strategies}
                self.write()
                result = self.run_select(dict(v=1, work_kind=kind))
                self.assertEqual(result["state"], "selected")
                self.assertEqual(result["profile"]["work_kind"], kind)
                self.assertIn("ranking_stricter_band", result["ranking"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()


class RoutesTest(unittest.TestCase):
    def models(self):
        return [dict(provider=p, id=i, model=None, family=None, variants=["high"], context=1000, released=5)
                for p, i in [("claude-agent", "opus"), ("openai", "gpt"), ("opencode-go", "glm"), ("anthropic", "x")]]

    def eligible(self, source, routes=None, now=1000):
        from runtime import routing_observations
        return routing_observations.eligible(routes or routing_paths.routes(self.models()), source, now)

    def test_routes_come_from_models_per_path(self):
        routes = routing_paths.routes(self.models())
        self.assertEqual([(r["path"], r["route"], r["model"]) for r in routes],
                         [(PATHS[0], PATHS[0] + ":opus", "opus"), (PATHS[1], PATHS[1] + ":gpt", "gpt"),
                          (PATHS[2], PATHS[2] + ":glm", "glm")])
        self.assertTrue(all(r["pool"] and r["harness"] and r["variants"] == ["high"] for r in routes))
        with self.assertRaises(ValueError):
            routing_paths.routes([dict(self.models()[0], variants="high")])
        with self.assertRaises(ValueError):
            routing_paths.routes(self.models()[:1] * 513)

    def test_overflow_never_excludes(self):
        for crossover, label in [("provider_blocked", "blocked"), ("provider_managed", "enabled"), ("unknown", "unknown")]:
            source = Facts()
            source.billing = lambda route, now, c=crossover, f=source.billing: dict(f(route, now), paid_crossover=c)
            kept, excluded = self.eligible(source)
            self.assertEqual((len(kept), excluded), (3, []))
            self.assertEqual({r["paid_overflow"] for r in kept}, {label})
            self.assertTrue(all(r["account_marker"] for r in kept))

    def test_quota_and_billing_still_exclude(self):
        source = Facts()
        source.exhausted.add(PATHS[0])
        kept, excluded = self.eligible(source)
        self.assertEqual(excluded, [dict(route=PATHS[0] + ":opus", reason="quota_exhausted")])
        self.assertEqual(len(kept), 2)
        stale = Facts()
        stale.billing = lambda route, now, f=stale.billing: dict(f(route, now), valid_until=now - 1)
        kept, excluded = self.eligible(stale)
        self.assertEqual((kept, {e["reason"] for e in excluded}), ([], {"billing_unverified"}))

    def test_no_ten_route_cap(self):
        models = [dict(self.models()[0], id="m%d" % n) for n in range(30)]
        kept, excluded = self.eligible(Facts(), routing_paths.routes(models))
        self.assertEqual((len(kept), excluded), (30, []))
