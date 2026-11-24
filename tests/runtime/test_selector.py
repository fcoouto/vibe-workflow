"""Protect ranked selection, caller-owned classification and the no-launch boundary."""
import copy
import json
from pathlib import Path
import stat
import tempfile
import time
import unittest

from runtime import routing
from runtime import routing_paths
from runtime import feedback, native_catalog, scores, selector
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


NOW = 2_000_000
TASK = dict(files="1-2", spec="exact", risk="reversible")


def body(*rows):
    data = [dict(id=name, created=made, pricing=dict(prompt=str(i * 1e-6), completion=str(o * 1e-6)),
                 benchmarks=dict(artificial_analysis=dict(intelligence_index=score)))
            for name, made, i, o, score in rows]
    return json.dumps(dict(data=data)).encode()


BODY = body(("anthropic/claude-opus-5-5", 10, 5, 25, 80), ("anthropic/claude-sonnet-5-5", 20, 3, 15, 76),
            ("anthropic/claude-haiku-5-5", 30, 1, 5, 50), ("openai/gpt-6-2-sol", 40, 3, 15, 78),
            ("openai/gpt-6-3-sol", 50, 3, 15, None))
BODY3 = body(("anthropic/claude-fable-5-2", 10, 8, 40, 64.6), ("openai/gpt-6-2-astra", 20, 8, 40, 62.1),
             ("google/gemini-4-neon", 30, 4, 20, 61.4), ("anthropic/claude-haiku-5-5", 40, 0.1, 0.5, 36.5),
             ("z-ai/glm-5-3-flash", 50, 0.03, 0.11, 27.8))


# Anchor 61.4 (third lab). glm-5.3-flash is the cheap 34.8 candidate, kimi-x-1 the pricier 40.
FEEDBACK_BODY = body(("anthropic/claude-fable-5-2", 10, 8, 40, 64.6), ("openai/gpt-6-2-astra", 20, 8, 40, 62.1),
                     ("google/gemini-4-neon", 30, 4, 20, 61.4), ("z-ai/glm-5-3-flash", 40, 0.1, 0.5, 34.8),
                     ("moonshot/kimi-x-1", 50, 3, 15, 40))


def model(provider, name, **extra):
    return dict(dict(provider=provider, id=name, model=None, family=None, variants=["low", "high"],
                     context=None, released=5), **extra)


class StampedFacts(Facts):
    """Like the real readers: acquisition takes time and stamps facts with the clock after it."""
    def __init__(self, clock):
        super().__init__()
        self.clock, self.acquired = clock, None

    def acquire_billing(self, routes, now):
        super().acquire_billing(routes, now)
        self.clock[0] += 5
        self.acquired = int(time.time())

    def billing(self, route, now):
        # opencode_account._account's check: facts from after `now` are not fresh.
        return super().billing(route, self.acquired) if 0 <= now - self.acquired <= 900 else None


class Fetch:
    def __init__(self, raw=BODY):
        self.raw, self.calls = raw, []

    def __call__(self, timeout):
        self.calls.append(timeout)
        if self.raw is None:
            raise OSError("down")
        return self.raw


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = Facts()
        self.fetch = Fetch()
        self.models = [model("claude-agent", "claude-opus-5-5"), model("claude-agent", "claude-sonnet-5-5"),
                       model("openai", "gpt-6-2-sol")]

    def request(self, **extra):
        return dict(dict(v=2, work_kind="review", task=dict(TASK), models=self.models), **extra)

    def run_select(self, request=None, now=NOW, fetch=None):
        return select(request or self.request(), state_dir=self.root, source=self.source,
                      fetch=fetch or self.fetch, now=now)

    def warm(self):
        self.assertEqual(scores.refresh(self.root, [], NOW, fetch=self.fetch), "stored")
        self.fetch.calls.clear()

    def test_selects_cheapest_qualifying_and_two_alternatives(self):
        result = self.run_select()
        self.assertEqual(result["state"], "selected")
        self.assertEqual(result["level"], 2)
        self.assertEqual(result["route"]["model"], "gpt-6-2-sol")
        self.assertEqual(result["route"]["provider"], "openai")
        self.assertEqual((result["route"]["effort"], result["route"]["score"], result["route"]["price"],
                          result["route"]["score_source"], result["route"]["paid_overflow"]),
                         ("high", 78.0, 6.0, "openrouter", "blocked"))
        self.assertEqual([item["model"] for item in result["alternatives"]],
                         ["claude-sonnet-5-5", "claude-opus-5-5"])
        self.assertEqual(result["attribution"], "Scores: Artificial Analysis via OpenRouter")
        self.assertIs(result["below_level"], False)
        for field in ("admission", "reservation", "spend_permit"):
            self.assertIs(result[field], False)

    def test_estimated_hours_of_four_or_more_raise_effort_one_step(self):
        self.models = [model(provider, name, variants=["low", "medium", "high", "xhigh"])
                       for provider, name in (("claude-agent", "claude-opus-5-5"),
                                              ("claude-agent", "claude-sonnet-5-5"), ("openai", "gpt-6-2-sol"))]
        self.warm()
        short = self.run_select(self.request(work_kind="review"))
        self.assertEqual(short["route"]["effort"], "medium")
        long = self.run_select(self.request(work_kind="review", task=dict(TASK, estimate_agentic_effort_hours=6)))
        self.assertEqual(long["route"]["effort"], "high")

    def test_huge_hour_estimate_is_invalid_request_not_an_exception(self):
        self.warm()
        for bad in (10**400, -10**400, float('nan')):
            result = self.run_select(self.request(task=dict(TASK, estimate_agentic_effort_hours=bad)))
            self.assertEqual((result["state"], result["reason"]), ("blocked", "invalid_request"))

    def test_cheap_model_below_the_bar_is_not_picked(self):
        self.fetch = Fetch(BODY3)
        result = self.run_select(self.request(work_kind="implementation", models=[
            model("opencode-go", "glm-5.3-flash"), model("opencode-go", "claude-haiku-5-5")]))
        self.assertEqual(result["route"]["model"], "claude-haiku-5-5")
        self.assertIs(result["below_level"], False)
        self.assertNotIn("glm-5.3-flash", [item["model"] for item in result["alternatives"]])

    def test_nothing_at_the_level_returns_the_strongest_marked(self):
        self.fetch = Fetch(BODY3)
        result = self.run_select(self.request(work_kind="implementation",
                                              models=[model("opencode-go", "glm-5.3-flash")]))
        self.assertEqual((result["state"], result["route"]["model"], result["below_level"], result["level"]),
                         ("selected", "glm-5.3-flash", True, 1))

    def test_constraints_leaving_only_low_candidates_fall_back(self):
        self.fetch = Fetch(BODY3)
        result = self.run_select(self.request(work_kind="implementation", constraints=dict(
            excluded_models=["claude-haiku-5-5"]), models=[model("opencode-go", "glm-5.3-flash"),
                                                           model("opencode-go", "claude-haiku-5-5")]))
        self.assertEqual((result["state"], result["route"]["model"], result["below_level"]),
                         ("selected", "glm-5.3-flash", True))

    def test_below_level_recheck_failure_keeps_the_empty_reason(self):
        self.fetch = Fetch(BODY3)
        self.source.change = True
        result = self.run_select(self.request(work_kind="implementation",
                                              models=[model("opencode-go", "glm-5.3-flash")]))
        self.assertEqual((result["state"], result["reason"], result["route"], result["below_level"]),
                         ("no_selection", "no_eligible_route", None, False))

    def test_failed_recheck_never_leaves_an_unmarked_below_bar_pick(self):
        class OnePath(Facts):
            def billing(self, route, now):
                fact = super().billing(route, now)
                if route["path"] == "opencode-claude-subscription" and self.reads[route["path"]] > 1:
                    fact["producer"] = "invented:changed"
                return fact
        self.source = OnePath()
        self.fetch = Fetch(body(("openai/gpt-6-2-sol", 10, 3, 15, 80), ("google/gemini-4-neon", 20, 3, 15, 70),
                                ("anthropic/claude-opus-5-5", 30, 4, 20, 60), ("z-ai/glm-5-3-flash", 40, 0.1, 0.5, 59)))
        result = self.run_select(self.request(task=dict(files="1-2", spec="exact", risk="critical"), models=[
            model("claude-agent", "claude-opus-5-5"), model("opencode-go", "glm-5.3-flash")]))
        self.assertEqual((result["state"], result["route"], result["below_level"], result["level"]),
                         ("no_selection", None, False, 3))

    def test_a_qualifying_route_is_confirmed_past_three_near_picks(self):
        self.fetch = Fetch(body(("openai/gpt-6-2-sol", 10, 3, 15, 80), ("google/gemini-4-neon", 20, 3, 15, 70),
                                ("anthropic/claude-opus-5-5", 30, 4, 20, 60), ("z-ai/glm-5-3-flash", 40, 0.1, 0.5, 59),
                                ("z-ai/glm-5-3-air", 50, 0.1, 0.5, 59), ("z-ai/glm-5-3-lite", 60, 0.1, 0.5, 59)))
        result = self.run_select(self.request(task=dict(files="1-2", spec="exact", risk="critical"), models=[
            model("claude-agent", "claude-opus-5-5"), model("opencode-go", "glm-5.3-flash"),
            model("opencode-go", "glm-5.3-air"), model("opencode-go", "glm-5.3-lite")]))
        picks = [result["route"]] + result["alternatives"]
        self.assertEqual((result["state"], result["below_level"], len(picks)), ("selected", False, 3))
        self.assertIn("claude-opus-5-5", [item["model"] for item in picks])

    def test_only_openrouter_scores_fall_back_without_an_anchor(self):
        self.fetch = Fetch(body(("openrouter/openai/gpt-6-2-sol", 10, 3, 15, 78)))
        result = self.run_select(self.request(work_kind="implementation",
                                              models=[model("opencode-go", "gpt-6-2-sol")]))
        self.assertEqual((result["state"], result["reason"], result["below_level"]), ("selected", None, True))
        self.assertEqual(result["route"]["model"], "gpt-6-2-sol")

    def test_live_facts_stamped_after_the_clock_was_first_read_stay_eligible(self):
        clock = [NOW]
        self.source = StampedFacts(clock)
        with mock.patch.object(selector.time, "time", lambda: clock[0]):
            result = self.run_select(now=None)
        self.assertEqual((result["state"], result["reason"]), ("selected", None), result.get("excluded"))

    def test_first_run_fetches_once_without_a_first_fetch_flag(self):
        first = self.run_select()
        self.assertEqual(self.fetch.calls, [scores.FIRST_TIMEOUT])
        self.assertNotIn("refresh", first)
        self.assertNotIn("first_fetch", first)
        second = self.run_select()
        self.assertEqual(len(self.fetch.calls), 1)
        self.assertNotIn("first_fetch", second)

    def test_first_run_failure_is_scores_unavailable(self):
        self.fetch.raw = None
        for _ in range(2):
            result = self.run_select()
            self.assertEqual((result["state"], result["reason"], result["route"]),
                             ("no_selection", "scores_unavailable", None))
        self.assertEqual(len(self.fetch.calls), 1)

    def test_refresh_due_is_reported_not_performed(self):
        self.warm()
        result = self.run_select(now=NOW + 8 * 86400)
        self.assertEqual(result["state"], "selected")
        self.assertEqual(result["refresh"], dict(wanted=[]))
        self.assertEqual(self.fetch.calls, [])

    def test_claude_alias_maps_through_native_catalog(self):
        native_catalog.store_snapshot(self.root / "router.json", "claude-code", dict(
            binding=dict(client_version="1", connection="c", controls="x"),
            models=[dict(alias="sonnet", resolved="claude-sonnet-5-5", efforts=[])]))
        result = self.run_select(self.request(models=[model("claude-agent", "sonnet")]))
        self.assertEqual((result["route"]["model"], result["route"]["score"], result["route"]["score_source"]),
                         ("sonnet", 76.0, "openrouter"))

    def test_old_haiku_never_borrows_from_the_newer_one(self):
        # Live: both report released 0 and tied on borrowed score and price; route order picked the old one.
        native_catalog.store_snapshot(self.root / "router.json", "claude-code", dict(
            binding=dict(client_version="1", connection="c", controls="x"),
            models=[dict(alias="haiku", resolved="claude-haiku-5-5", efforts=[])]))
        result = self.run_select(self.request(work_kind="implementation", models=[
            model("claude-agent", "haiku", released=0), model("claude-agent", "claude-haiku-4-5-20251001", released=0)]))
        self.assertEqual((result["state"], result["level"], result["route"]["model"], result["route"]["score_source"]),
                         ("selected", 1, "haiku", "openrouter"))
        self.assertEqual(result["alternatives"], [])
        self.assertIn(dict(route="opencode-claude-subscription:claude-haiku-4-5-20251001", reason="unscored"),
                      result["excluded"])

    def test_unscored_without_family_is_excluded(self):
        self.models.append(model("opencode-go", "mystery-model"))
        result = self.run_select()
        self.assertIn(dict(route="opencode-go-subscription:mystery-model", reason="unscored"), result["excluded"])
        self.assertEqual(result["state"], "selected")

    def test_family_score_is_borrowed(self):
        result = self.run_select(self.request(models=[model("openai", "gpt-6-3-sol")]))
        self.assertEqual((result["route"]["score"], result["route"]["score_source"]), (78.0, "family"))

    def test_duplicate_routes_appear_once(self):
        self.models.append(dict(self.models[0]))
        result = self.run_select()
        routes = [result["route"]] + result["alternatives"]
        self.assertEqual(len({item["route"] for item in routes}), len(routes))

    def test_context_and_constraints_only_narrow(self):
        self.models[2] = model("openai", "gpt-6-2-sol", context=500)
        result = self.run_select(self.request(task=dict(TASK, context_tokens=600)))
        self.assertIn(dict(route="opencode-chatgpt-subscription:gpt-6-2-sol", reason="context_limit"),
                      result["excluded"])
        self.assertEqual(result["route"]["model"], "claude-sonnet-5-5")
        result = self.run_select(self.request(constraints=dict(excluded_models=["claude-sonnet-5-5"])))
        self.assertIn(dict(route="opencode-claude-subscription:claude-sonnet-5-5", reason="constraint"),
                      result["excluded"])
        everything = ["claude-opus-5-5", "claude-sonnet-5-5", "gpt-6-2-sol"]
        result = self.run_select(self.request(constraints=dict(excluded_models=everything)))
        self.assertEqual((result["state"], result["reason"], {e["reason"] for e in result["excluded"]}),
                         ("no_selection", "no_recommendation", {"constraint"}))

    def test_excluded_models_matches_every_format(self):
        self.fetch = Fetch(FEEDBACK_BODY)
        glm = model("opencode-go", "glm-5.3-flash")
        request = self.request(work_kind="implementation", models=[glm, model("opencode-go", "kimi-x-1")])
        self.assertEqual(self.run_select(request)["route"]["route"], "opencode-go-subscription:glm-5.3-flash")
        for entry in ("glm-5.3-flash", "opencode-go/glm-5.3-flash#high", "opencode-go-subscription:glm-5.3-flash",
                      "glm-5-3-flash"):
            with self.subTest(entry=entry):
                result = self.run_select(self.request(work_kind="implementation", models=[glm, model(
                    "opencode-go", "kimi-x-1")], constraints=dict(excluded_models=[entry])))
                self.assertEqual((result["state"], result["route"]["model"], "unmatched" in result),
                                 ("selected", "kimi-x-1", False))
                self.assertIn(dict(route="opencode-go-subscription:glm-5.3-flash", reason="constraint"),
                              result["excluded"])

    def test_unmatched_entries_are_reported_once_in_request_order(self):
        result = self.run_select(self.request(constraints=dict(
            excluded_models=["nope", "claude-sonnet-5-5", "nope", "also-nope"])))
        self.assertEqual(result["state"], "selected")
        self.assertEqual(result["unmatched"], ["nope", "also-nope"])

    def test_unmatched_is_absent_when_every_entry_matches(self):
        result = self.run_select(self.request(constraints=dict(excluded_models=["claude-sonnet-5-5"])))
        self.assertNotIn("unmatched", result)

    def test_unmatched_is_reported_on_early_answers(self):
        result = self.run_select(self.request(models=[], constraints=dict(excluded_models=["nope"])))
        self.assertEqual((result["state"], result["reason"], result["unmatched"]),
                         ("no_selection", "no_models", ["nope"]))
        self.assertEqual(self.source.calls, [])
        self.source.exhausted.add("opencode-claude-subscription")
        result = self.run_select(self.request(models=[model("claude-agent", "claude-opus-5-5")],
                                              constraints=dict(excluded_models=["nope"])))
        self.assertEqual((result["state"], result["reason"], result["unmatched"]),
                         ("no_selection", "no_eligible_route", ["nope"]))

    def test_variant_suffix_counts_only_on_provider_model(self):
        self.fetch = Fetch(FEEDBACK_BODY)
        request = self.request(work_kind="implementation", models=[
            model("opencode-go", "glm-5.3-flash"), model("opencode-go", "kimi-x-1")])
        result = self.run_select(self.request(work_kind="implementation", models=request["models"],
                                              constraints=dict(excluded_models=["glm-5.3-flash#high"])))
        self.assertEqual((result["state"], result["route"]["model"]), ("selected", "glm-5.3-flash"))
        self.assertEqual(result["unmatched"], ["glm-5.3-flash#high"])
        result = self.run_select(self.request(work_kind="implementation", models=request["models"],
                                              constraints=dict(excluded_models=["opencode-go/glm-5.3-flash#high"])))
        self.assertEqual((result["state"], result["route"]["model"]), ("selected", "kimi-x-1"))
        self.assertNotIn("unmatched", result)

    def test_unmatched_survives_selector_unavailable(self):
        def unavailable(routes, now):
            raise OSError("down")
        self.source.acquire_billing = unavailable
        result = self.run_select(self.request(constraints=dict(excluded_models=["nope", "claude-sonnet-5-5"])))
        self.assertEqual((result["state"], result["reason"], result["unmatched"]),
                         ("blocked", "selector_unavailable", ["nope"]))

    def test_quota_excluded_route_named_by_catalog_key_is_matched(self):
        self.fetch = Fetch(FEEDBACK_BODY)
        self.source.exhausted.add("opencode-go-subscription")
        result = self.run_select(self.request(work_kind="implementation", models=[
            model("opencode-go", "glm-5.3-flash"), model("openai", "gpt-6-2-astra")],
            constraints=dict(excluded_models=["glm-5-3-flash"])))
        self.assertEqual((result["state"], result["route"]["model"]), ("selected", "gpt-6-2-astra"))
        self.assertIn(dict(route="opencode-go-subscription:glm-5.3-flash", reason="quota_exhausted"),
                      result["excluded"])
        self.assertNotIn("unmatched", result)

    def test_allowed_fields_are_invalid_requests(self):
        for constraints in (dict(allowed_models=["claude-sonnet-5-5"]), dict(allowed_efforts=["low"]),
                            dict(allowed_harnesses=[PATHS[0]])):
            with self.subTest(constraints=constraints):
                result = self.run_select(self.request(constraints=constraints))
                self.assertEqual((result["state"], result["reason"]), ("blocked", "invalid_request"))
        self.assertEqual((self.source.calls, self.fetch.calls), ([], []))

    def test_exhausted_route_is_not_selected(self):
        self.source.exhausted.add("opencode-chatgpt-subscription")
        result = self.run_select()
        self.assertEqual(result["route"]["model"], "claude-sonnet-5-5")
        self.assertEqual(result["excluded"], [dict(route="opencode-chatgpt-subscription:gpt-6-2-sol",
                                                   reason="quota_exhausted")])

    def test_changed_identity_is_refused_on_final_recheck(self):
        self.source.change = True
        result = self.run_select(self.request(models=[model("claude-agent", "claude-opus-5-5"),
                                                      model("openai", "gpt-6-2-sol")]))
        self.assertEqual(result["state"], "no_selection")
        self.assertEqual({item["reason"] for item in result["excluded"]}, {"account_changed"})

    def test_all_routes_failing_billing_is_no_eligible_route_before_scores(self):
        self.source.billing = lambda route, now: None
        result = self.run_select()
        self.assertEqual((result["state"], result["reason"]), ("no_selection", "no_eligible_route"))
        self.assertEqual({item["reason"] for item in result["excluded"]}, {"billing_unverified"})
        self.assertEqual(len(result["excluded"]), 3)
        self.assertEqual(self.fetch.calls, [])

    def test_empty_model_list_has_no_effects(self):
        absent = self.root / "absent"
        result = select(self.request(models=[]), state_dir=absent, source=self.source, fetch=self.fetch, now=NOW)
        self.assertEqual((result["state"], result["reason"]), ("no_selection", "no_models"))
        self.assertEqual((self.source.calls, self.fetch.calls, absent.exists()), ([], [], False))

    def test_invalid_requests_do_not_echo_or_acquire(self):
        for request in (self.request(v=1), dict(v=2, work_kind="review", models=self.models),
                        self.request(task=dict(TASK, files="PRIVATE-SENTINEL")),
                        self.request(credentials="PRIVATE-SENTINEL"),
                        self.request(constraints=dict(PRIVATE_SENTINEL=["x"])),
                        self.request(constraints=dict(allowed_models="PRIVATE-SENTINEL"))):
            with self.subTest(request=request):
                result = self.run_select(request)
                self.assertEqual((result["state"], result["reason"]), ("blocked", "invalid_request"))
                self.assertNotIn("PRIVATE-SENTINEL", json.dumps(result))
        self.assertEqual((self.source.calls, self.fetch.calls), ([], []))

    def test_plugin_supplied_models_failing_checks_are_selector_unavailable(self):
        for bad in (dict(variants="PRIVATE-SENTINEL"), dict(context=1.5), dict(released="PRIVATE-SENTINEL"),
                    dict(variants=["v%d" % n for n in range(40)]), dict(id="PRIVATE-SENTINEL" * 30)):
            with self.subTest(bad=bad):
                result = self.run_select(self.request(models=[dict(self.models[0], **bad)]))
                self.assertEqual((result["state"], result["reason"]), ("blocked", "selector_unavailable"))
                self.assertNotIn("PRIVATE-SENTINEL", json.dumps(result))
        self.assertEqual((self.source.calls, self.fetch.calls), ([], []))

    def test_unsafe_cache_is_never_overwritten(self):
        self.warm()
        cache = self.root / scores.CACHE
        cache.chmod(0o644)
        before = cache.read_bytes()
        for _ in range(2):
            result = self.run_select(now=NOW + 8 * 86400)
            self.assertEqual((result["state"], result["reason"]), ("blocked", "selector_unavailable"))
            self.assertEqual(selector.check_setup(state_dir=self.root, allow_fetch=True, fetch=self.fetch),
                             dict(state="setup_needed", reason="selector_unavailable"))
            self.assertEqual(selector.refresh(dict(wanted=["x"]), state_dir=self.root, fetch=self.fetch),
                             dict(state="failed"))
        self.assertEqual((cache.read_bytes(), stat.S_IMODE(cache.stat().st_mode)), (before, 0o644))
        self.assertEqual(self.fetch.calls, [])

    def test_malformed_cache_is_replaced_by_the_next_fetch(self):
        self.warm()
        cache = self.root / scores.CACHE
        for bad in ("{bad", json.dumps(dict(version=1, models=["PRIVATE-SENTINEL"])), "[]",
                    json.dumps(dict(version=2, models={}, unscored={}, failures=0, fetched_at=0, attempt_at=0)),
                    json.dumps(dict(version=1, models={}, failures=0, fetched_at=NOW, attempt_at=NOW)),
                    json.dumps(dict(version=1, models={}, unscored={}, failures="0", fetched_at=0, attempt_at=0))):
            with self.subTest(bad=bad):
                cache.write_text(bad)
                cache.chmod(0o600)
                self.assertIsNone(scores.load(self.root))
                result = self.run_select()
                self.assertEqual(result["state"], "selected")
                self.assertNotIn("PRIVATE-SENTINEL", json.dumps(result))
                self.assertEqual(scores.load(self.root)["failures"], 0)
                cache.write_text(bad)
                self.assertEqual(selector.check_setup(state_dir=self.root, allow_fetch=True, fetch=self.fetch),
                                 dict(state="ready", reason=None))
                cache.write_text(bad)
                self.assertEqual(selector.refresh(dict(wanted=["x"]), state_dir=self.root, fetch=self.fetch),
                                 dict(state="stored"))
                self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)

    def test_unexpected_internal_error_is_selector_unavailable(self):
        with mock.patch("runtime.routing_observations.eligible", side_effect=RuntimeError("PRIVATE-SENTINEL")):
            result = self.run_select()
        self.assertEqual((result["state"], result["reason"]), ("blocked", "selector_unavailable"))
        self.assertNotIn("PRIVATE-SENTINEL", json.dumps(result))

    def test_all_unscored_is_reported_as_unscored(self):
        result = self.run_select(self.request(models=[model("opencode-go", "mystery-model")]))
        self.assertEqual((result["state"], result["reason"]), ("no_selection", "unscored"))
        self.assertEqual(result["excluded"], [dict(route="opencode-go-subscription:mystery-model", reason="unscored")])

    def seed(self, creator, key, rating, count, kind="implementation"):
        """Cards from `count` distinct sessions, each rated by a different lab (full weight)."""
        for number in range(count):
            card = dict(rated=dict(creator=creator, key=key, family=scores.family_key(key)),
                        rater=dict(creator="anthropic", key="claude-fable-5-2"), work_kind=kind,
                        size_group="short", rating=rating, session=f"{number + 1:016x}", comment=None)
            self.assertEqual(feedback.record(card, state_dir=self.root, now=NOW), dict(state="recorded"))

    def test_feedback_moves_a_model_below_its_bar(self):
        self.fetch = Fetch(FEEDBACK_BODY)
        request = self.request(work_kind="implementation",
                               models=[model("opencode-go", "glm-5.3-flash"), model("opencode-go", "kimi-x-1")])
        self.assertEqual(self.run_select(request)["route"]["model"], "glm-5.3-flash")
        self.seed("z-ai", "glm-5-3-flash", 0, 20)
        result = self.run_select(request)
        self.assertEqual((result["state"], result["route"]["model"], result["below_level"]),
                         ("selected", "kimi-x-1", False))
        self.assertNotIn("glm-5.3-flash", [item["model"] for item in result["alternatives"]])

    def test_feedback_is_per_kind(self):
        self.fetch = Fetch(FEEDBACK_BODY)
        request = self.request(work_kind="review",
                               models=[model("opencode-go", "glm-5.3-flash"), model("opencode-go", "kimi-x-1")])
        before = self.run_select(request)
        self.seed("z-ai", "glm-5-3-flash", 0, 20, kind="implementation")
        after = self.run_select(request)
        self.assertEqual((after["state"], after["route"]["model"]), (before["state"], before["route"]["model"]))
        self.assertEqual([item["model"] for item in after["alternatives"]],
                         [item["model"] for item in before["alternatives"]])
        self.assertEqual({item["feedback"]["sessions"] for item in [after["route"]] + after["alternatives"]}, {0})

    def test_damaged_feedback_file_changes_nothing(self):
        self.fetch = Fetch(FEEDBACK_BODY)
        request = self.request(work_kind="implementation",
                               models=[model("opencode-go", "glm-5.3-flash"), model("opencode-go", "kimi-x-1")])
        before = self.run_select(request)
        path = self.root / feedback.FILE
        path.write_bytes(b'{"version": 1, garbage')
        path.chmod(0o600)
        result = self.run_select(request)
        self.assertEqual((result["state"], result["route"]["model"]), ("selected", before["route"]["model"]))
        self.assertEqual(result["route"]["model"], "glm-5.3-flash")

    def test_pick_reports_catalog_id_and_feedback(self):
        self.fetch = Fetch(FEEDBACK_BODY)
        result = self.run_select(self.request(work_kind="implementation",
                                              models=[model("opencode-go", "glm-5.3-flash"),
                                                      model("opencode-go", "kimi-x-1")]))
        picks = [result["route"]] + result["alternatives"]
        self.assertEqual(result["size_group"], "short")
        self.assertEqual(picks[0]["catalog_id"], dict(key="glm-5-3-flash", creator="z-ai", family="glm-flash"))
        for item in picks:
            self.assertIsNotNone(item["catalog_id"]["key"])
            self.assertIsNotNone(item["catalog_id"]["creator"])
            self.assertEqual(item["feedback"], dict(sessions=0, adjustment=0.0))
            self.assertEqual(item["catalog_score"], item["score"])
        long = self.run_select(self.request(work_kind="implementation", task=dict(TASK, estimate_agentic_effort_hours=6),
                                            models=[model("opencode-go", "glm-5.3-flash")]))
        self.assertEqual(long["size_group"], "long")

    def test_check_setup(self):
        with mock.patch("runtime.opencode_account.refresh", side_effect=AssertionError("reader started")):
            self.assertEqual(selector.check_setup(state_dir=self.root, fetch=self.fetch),
                             dict(state="setup_needed", reason="scores_unavailable"))
            self.assertEqual(self.fetch.calls, [])
            self.assertEqual(selector.check_setup(state_dir=self.root, allow_fetch=True, fetch=self.fetch),
                             dict(state="ready", reason=None))
            self.assertEqual(len(self.fetch.calls), 1)
            self.assertEqual(selector.check_setup(state_dir=self.root, allow_fetch=True, fetch=self.fetch)["state"],
                             "ready")
            self.assertEqual(len(self.fetch.calls), 1)

    def test_failed_first_fetch_is_not_ready(self):
        self.fetch.raw = None
        self.assertEqual(selector.check_setup(state_dir=self.root, allow_fetch=True, fetch=self.fetch)["reason"],
                         "scores_unavailable")

    def test_refresh_reports_the_outcome(self):
        self.assertEqual(selector.refresh(dict(wanted=["x"]), state_dir=self.root, fetch=self.fetch),
                         dict(state="stored"))
        self.assertEqual(selector.refresh(dict(wanted="x"), state_dir=self.root, fetch=self.fetch),
                         dict(state="failed"))


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

    def test_model_fields_are_validated(self):
        base = self.models()[0]
        for bad in (dict(provider=None), dict(provider=7), dict(id=None), dict(id=7), dict(id="x" * 257),
                    dict(model=7), dict(family=7), dict(context=1.5), dict(context=-1), dict(context="1"),
                    dict(released="2026-01-01"), dict(released=-1), dict(released=True),
                    dict(variants=["v%d" % n for n in range(33)]), dict(variants=[7])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                routing_paths.routes([dict(base, **bad)])
        self.assertEqual(len(routing_paths.routes([dict(base, variants=["v%d" % n for n in range(32)])])[0]
                             ["variants"]), 32)

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


if __name__ == "__main__":
    unittest.main()
