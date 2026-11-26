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
from runtime import council, feedback, native_catalog, scores, selector, subscription_billing, usage_balance
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
        self.assertEqual(short["route"]["effort"], "high")
        long = self.run_select(self.request(work_kind="review", task=dict(TASK, estimate_agentic_effort_hours=6)))
        self.assertEqual(long["route"]["effort"], "xhigh")
        edge = self.run_select(self.request(work_kind="review", task=dict(TASK, estimate_agentic_effort_hours=4)))
        self.assertEqual((edge["route"]["effort"], edge["size_group"]), ("xhigh", "long"))
        under = self.run_select(self.request(work_kind="review", task=dict(TASK, estimate_agentic_effort_hours=3.9)))
        self.assertEqual((under["route"]["effort"], under["size_group"]), ("high", "short"))
        writing = self.run_select(self.request(work_kind="writing"))
        self.assertEqual((writing["level"], writing["route"]["effort"]), (1, "medium"))

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

    def test_unmatched_survives_an_unreadable_catalog(self):
        for models in ([model("opencode-go", "glm-5.3-flash")], [model("claude-agent", "claude-opus-5-5")]):
            with self.subTest(models=models):
                result = select(self.request(models=models, constraints=dict(excluded_models=["nope"])),
                                state_dir="relative-state", source=self.source, fetch=self.fetch, now=NOW)
                self.assertEqual((result["state"], result["reason"], result["unmatched"]),
                                 ("blocked", "selector_unavailable", ["nope"]))
        self.assertEqual((self.source.calls, self.fetch.calls), ([], []))

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

    def test_a_malformed_cached_row_never_blocks_a_fixed_provider_pick(self):
        self.warm()
        path = self.root / scores.CACHE
        cache = json.loads(path.read_text())
        cache["models"]["junk/row"] = dict(next(iter(cache["models"].values())))
        del cache["models"]["junk/row"]["key"]
        path.write_text(json.dumps(cache))
        for extra in ({}, dict(reviewing="claude-agent/claude-opus-5-5")):
            with self.subTest(extra=extra):
                result = self.run_select(self.request(**extra))
                self.assertEqual((result["state"], result["reason"]), ("selected", None))

    def test_an_empty_creator_row_keeps_the_fixed_creator(self):
        self.warm()
        path = self.root / scores.CACHE
        cache = json.loads(path.read_text())
        cache["models"]["anthropic/claude-opus-5-5"]["creator"] = ""
        path.write_text(json.dumps(cache))
        result = self.run_select(self.request(models=[model("claude-agent", "claude-opus-5-5")]))
        self.assertEqual((result["state"], result["route"]["catalog_id"]["creator"]), ("selected", "anthropic"))

    def test_an_empty_creator_row_never_names_a_donor_only_route(self):
        raw = body(("b/x-0", 80, 1, 5, 67), ("b/x-1", 90, 1, 5, None), ("a/a-small", 50, 0.1, 0.5, 67), *REVIEW_TOP)
        self.fetch = Fetch(raw)
        self.warm()
        path = self.root / scores.CACHE
        cache = json.loads(path.read_text())
        cache["models"]["b/x-1"]["creator"] = ""
        path.write_text(json.dumps(cache))
        result = self.run_select(self.request(models=[model("opencode-go", "x-1")]))
        self.assertEqual(result["state"], "selected")
        self.assertNotIn("catalog_id", result["route"])

    def test_a_donor_only_identity_is_recommended_but_not_rateable(self):
        raw = body(("b/x-0", 80, 1, 5, 67), ("a/a-small", 50, 0.1, 0.5, 67), *REVIEW_TOP)
        models = [model("opencode-go", "x-1"), model("openai", "x-1")]
        result = select(dict(v=2, work_kind="review", task=dict(files="1-2", spec="exact", risk="critical"),
                             models=models), state_dir=self.root, source=Facts(), fetch=Fetch(raw), now=NOW)
        picks = [result["route"]] + result["alternatives"]
        self.assertEqual([item["model"] for item in picks], ["x-1"])
        self.assertEqual(picks[0]["provider"], "opencode-go")
        self.assertNotIn("catalog_id", picks[0])

    def test_unmatched_and_filter_share_one_catalog_snapshot(self):
        root = self.root

        class Fills(Facts):
            def acquire_billing(self, routes, now):
                super().acquire_billing(routes, now)
                native_catalog.store_snapshot(root / "router.json", "claude-code", dict(
                    binding=dict(client_version="1", connection="c", controls="x"),
                    models=[dict(alias="sonnet", resolved="claude-sonnet-5-5", efforts=[])]))
        self.source = Fills()
        result = self.run_select(self.request(models=[model("claude-agent", "sonnet"), model("openai", "gpt-6-2-sol")],
                                              constraints=dict(excluded_models=["claude-sonnet-5-5"])))
        self.assertIn(dict(route="opencode-claude-subscription:sonnet", reason="constraint"), result["excluded"])
        self.assertNotIn("unmatched", result)

    def test_error_answers_reuse_the_post_acquisition_match(self):
        root = self.root

        class Fills(Facts):
            def acquire_billing(self, routes, now):
                super().acquire_billing(routes, now)
                native_catalog.store_snapshot(root / "router.json", "claude-code", dict(
                    binding=dict(client_version="1", connection="c", controls="x"),
                    models=[dict(alias="sonnet", resolved="claude-sonnet-5-5", efforts=[])]))
        self.source = Fills()
        request = self.request(models=[model("claude-agent", "sonnet")],
                               constraints=dict(excluded_models=["claude-sonnet-5-5", "nope"]))
        with mock.patch.object(selector.scores, "load", side_effect=OSError("unreadable")):
            result = self.run_select(request)
        self.assertEqual((result["state"], result["reason"], result["unmatched"]),
                         ("blocked", "selector_unavailable", ["nope"]))

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


REVIEW_TOP = (("a/a-top", 10, 3, 15, 70), ("b/b-top", 20, 3, 15, 68),
              ("c/c-top", 30, 3, 15, 66), ("d/d-top", 40, 3, 15, 64))


class ReviewingTest(unittest.TestCase):
    """A review names the work's author; the pick then comes from another lab only when that still reaches the level."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = Facts()

    def catalog(self, a_small=67, b_small=67, lab="a", extra=()):
        return body((f"{lab}/a-small", 50, 0.1, 0.5, a_small), ("b/b-small", 60, 1, 5, b_small), *REVIEW_TOP, *extra)

    def request(self, reviewing=None, models=None):
        if models is None:
            models = [model("opencode-go", "a-small"), model("opencode-go", "b-small")]
        request = dict(v=2, work_kind="review", task=dict(files="1-2", spec="exact", risk="critical"), models=models)
        if reviewing is not None:
            request["reviewing"] = reviewing
        return request

    def run_select(self, request, raw):
        return select(request, state_dir=self.root, source=self.source, fetch=Fetch(raw), now=NOW)

    def test_reviewing_prefers_another_lab(self):
        raw = self.catalog()
        self.assertEqual(self.run_select(self.request(), raw)["route"]["model"], "a-small")
        result = self.run_select(self.request("opencode-go/a-small"), raw)
        self.assertEqual((result["state"], result["route"]["model"], result["alternatives"],
                          result["reviewer_independent"]), ("selected", "b-small", [], True))

    def test_reviewing_never_lowers_the_level(self):
        result = self.run_select(self.request("opencode-go/a-small"), self.catalog(b_small=50))
        self.assertEqual((result["state"], result["route"]["model"], result["below_level"],
                          result["reviewer_independent"]), ("selected", "a-small", False, False))

    def test_reviewing_below_level_not_reordered(self):
        raw = self.catalog(a_small=60, b_small=50)
        plain = self.run_select(self.request(), raw)
        result = self.run_select(self.request("opencode-go/a-small"), raw)
        self.assertTrue(result["below_level"])
        self.assertEqual((result["route"]["model"], [item["model"] for item in result["alternatives"]]),
                         (plain["route"]["model"], [item["model"] for item in plain["alternatives"]]))
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("a-small", False))
        self.assertEqual([item["model"] for item in result["alternatives"]], ["b-small"])

    def test_reviewing_unknown_lab(self):
        result = self.run_select(self.request("x/unknown"), self.catalog())
        self.assertEqual((result["state"], result["route"]["model"], result["reviewer_independent"]),
                         ("selected", "a-small", False))

    def test_reviewing_off_plan_model_resolves(self):
        raw = self.catalog(extra=[("a/a-other", 70, 0.1, 0.5, 60)])
        result = self.run_select(self.request("openai/a-other"), raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))

    def test_reviewing_variant_and_alias(self):
        result = self.run_select(self.request("opencode-go/a-small#high"), self.catalog(lab="~a"))
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))

    def test_reviewing_alias_lab_counts_as_the_same_lab(self):
        raw = self.catalog(lab="~a", extra=[("a/a-peer", 70, 0.2, 0.5, 67)])
        models = [model("opencode-go", "a-small"), model("opencode-go", "a-peer"), model("opencode-go", "b-small")]
        result = self.run_select(self.request("opencode-go/a-small", models=models), raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))

    def test_reviewing_unscored_author_never_borrows_a_family_lab(self):
        # The author is catalogued under lab a with no score; a family row of lab b must not name its lab.
        raw = self.catalog(extra=[("a/a-x-1", 70, 1, 5, None), ("b/a-x-0", 80, 1, 5, 67)])
        result = self.run_select(self.request("opencode-go/a-x-1"), raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))

    def test_reviewing_openrouter_candidate_is_never_independent(self):
        raw = body(("a/a-small", 50, 0.1, 0.5, 67), ("openrouter/o-small", 60, 1, 5, 67), *REVIEW_TOP)
        models = [model("opencode-go", "a-small"), model("opencode-go", "o-small")]
        result = self.run_select(self.request("opencode-go/a-small", models=models), raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("a-small", False))

    def test_reviewing_keeps_the_normal_pick_when_no_other_lab_confirms(self):
        class Flaky(Facts):
            def billing(self, route, now):
                fact = super().billing(route, now)
                if route["path"] == PATHS[1] and self.reads[PATHS[1]] > 1:
                    fact["producer"] = "invented:changed"
                return fact
        self.source = Flaky()
        raw = self.catalog(extra=[("openai/gpt-x", 70, 1, 5, 67)])
        models = [model("opencode-go", "a-small"), model("openai", "gpt-x")]
        result = self.run_select(self.request("opencode-go/a-small", models=models), raw)
        self.assertEqual((result["state"], result["route"]["model"], result["reviewer_independent"]),
                         ("selected", "a-small", False))
        self.assertIn(dict(route=PATHS[1] + ":gpt-x", reason="account_changed"), result["excluded"])

    def test_reviewing_unique_dated_row_names_an_unscored_author_lab(self):
        raw = self.catalog(extra=[("a/a-d-1-0101", 70, 1, 5, None), ("b/a-d-0", 80, 1, 5, 67)])
        result = self.run_select(self.request("opencode-go/a-d-1"), raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))

    def test_reviewing_openrouter_author_is_unknown(self):
        raw = body(("a/a-small", 50, 0.1, 0.5, 67), ("b/b-small", 60, 1, 5, 67),
                   ("openrouter/o-small", 70, 1, 5, 67), *REVIEW_TOP)
        result = self.run_select(self.request("opencode-go/o-small"), raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("a-small", False))

    def test_reviewing_names_the_creator_in_a_three_part_reference(self):
        raw = self.catalog()
        for reference in ("openrouter/a/whatever", "openrouter/~a/whatever#high"):
            with self.subTest(reference=reference):
                result = self.run_select(self.request(reference), raw)
                self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))
        for reference in ("openrouter/openrouter/x", "openrouter/Bad Lab/x"):
            with self.subTest(reference=reference):
                result = self.run_select(self.request(reference), raw)
                self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("a-small", False))

    def test_a_pick_and_its_card_agree_on_the_model_identity(self):
        # An unscored model catalogued under lab a, with a score borrowed from lab b's family.
        raw = self.catalog(extra=[("a/a-x-1", 70, 1, 5, None), ("b/a-x-0", 80, 1, 5, 67)])
        picked = self.run_select(self.request(models=[model("opencode-go", "a-x-1")]), raw)
        rated = picked["route"]["catalog_id"]
        self.assertEqual(rated["creator"], "a")
        answer = selector.feedback_submit(
            dict(v=1, rated=rated, work_kind="review", size_group=picked["size_group"], rating=3,
                 session="%032x" % 1, rater_entry=None, rater_model=dict(provider="other-host", id="a-x-1")),
            state_dir=self.root, now=NOW)
        self.assertEqual(answer, dict(state="refused", reason="self_rating"))

    def test_reviewing_refused_shapes(self):
        for bad in (7, "no-slash", "a/b/c/d", "/model", "provider/", "a//c", "a/b/"):
            with self.subTest(reviewing=bad):
                result = self.run_select(self.request(bad), self.catalog())
                self.assertEqual((result["state"], result["reason"]), ("blocked", "invalid_request"))
        self.assertEqual(self.source.calls, [])

    def test_reviewing_is_reported_on_early_answers(self):
        result = self.run_select(self.request("opencode-go/a-small", models=[]), self.catalog())
        self.assertEqual((result["state"], result["reviewer_independent"]), ("no_selection", False))

    def test_no_reviewing_no_field(self):
        self.assertNotIn("reviewer_independent", self.run_select(self.request(), self.catalog()))


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


CLAUDE_ROUTE = "opencode-claude-subscription:claude-sonnet-5-5"
GO_ROUTE = "opencode-go-subscription:kimi-x-1"
CHATGPT_ROUTE = "opencode-chatgpt-subscription:gpt-6-2-sol"
HAIKU_ROUTE = "opencode-claude-subscription:claude-haiku-5-5"
TERRA_ROUTE = "opencode-chatgpt-subscription:gpt-6-2-terra"
TASK_CRITICAL = dict(files="1-2", spec="exact", risk="critical")


def fresh(used, left, duration):
    """A fresh, verified reading at NOW that resets `left` seconds later; a None duration has no discrete length."""
    return dict(used_percent=used, earliest=NOW, latest=NOW, reset_at=NOW + left, duration=duration, discrete=True)


GO_HEALTHY = {"5h": fresh(20, 3600, 18000), "7d": fresh(20, 4 * 86400, 604800),
              "monthly": fresh(50, 10 * 86400, None)}  # 5h scores 4.0
CLAUDE_PLAIN = {"7d": fresh(40, 6 * 86400, 604800)}  # 7d scores 1.0: no boost
CHATGPT_SOON = {"5h": fresh(50, 3000, 18000), "7d": fresh(10, 6 * 86400, 604800)}  # 5h scores 3.0


def balance_body(*, go=(4, 20, 75), gpt=(3, 19, 78), claude=(3, 15, 76), extra=()):
    """Prices are (3 * input + output) / 4. The third lab's best is 75, so the level 2 bar is 60. Sonnet (price 6) is
    the cheapest baseline; gpt (7) and kimi (8) are within 1.5x of it and above 97% of its score."""
    return body(("anthropic/claude-opus-5-5", 10, 5, 25, 80), ("openai/gpt-6-2-sol", 20, *gpt),
                ("google/gemini-4-neon", 30, 4, 20, 61.4), ("anthropic/claude-sonnet-5-5", 40, *claude),
                ("moonshot/kimi-x-1", 50, *go), *extra)


SONNET_56 = "opencode-claude-subscription:claude-sonnet-5-6"
KIMI = {n: f"opencode-go-subscription:kimi-x-{n}" for n in range(1, 7)}


def alias_go(used):
    """A healthy Go alias whose 5h window resets in one hour: its 5h score is (100 - used) / 20."""
    return dict(GO_HEALTHY, **{"5h": fresh(used, 3600, 18000)})


def alias_body():
    """Sonnet 5-5 (price 6) is the baseline; sonnet 5-6 (6.5) and kimi 1-6 (7 to 8.25) are comparable with it."""
    rows = [("anthropic/claude-opus-5-5", 10, 5, 25, 80), ("openai/gpt-6-2-sol", 20, 3, 19, 78),
            ("google/gemini-4-neon", 30, 4, 20, 61.4), ("anthropic/claude-sonnet-5-5", 40, 3, 15, 76),
            ("anthropic/claude-sonnet-5-6", 45, 3, 17, 76)]
    rows += [(f"moonshot/kimi-x-{n}", 50 + n, 3, 18 + n, 75) for n in range(1, 7)]
    return body(*rows)


class Hinted(Facts):
    """Route-keyed ranking hints as the local source gives them. `drift` paths change account on each route's second
    billing read, so every alias of a drifted path is seen as changed after eligibility. `billed` logs every read."""
    def __init__(self, hints=None, *, drift=(), late=(), rotate=None, explode=False):
        super().__init__()
        self.hints, self.drift, self.late, self.explode = dict(hints or {}), set(drift), set(late), explode
        self.rotate = dict(rotate or {})
        self.hint_reads, self.billed, self.seen = [], [], {}

    def billing(self, route, now):
        """`drift` paths change on a route's second read (after eligibility); `late` paths on its third (the final
        `_confirm` read, after the scope recheck). A `rotate` path changes for every alias at once, after that many
        reads of the path: one login rotating between two of its aliases' reads."""
        found = super().billing(route, now)
        self.billed.append(route["route"])
        self.seen[route["route"]] = self.seen.get(route["route"], 0) + 1
        if (route["path"] in self.drift and self.seen[route["route"]] > 1) \
                or (route["path"] in self.late and self.seen[route["route"]] > 2) \
                or self.reads[route["path"]] > self.rotate.get(route["path"], self.reads[route["path"]]):
            found["producer"] = "invented:changed"
        return found

    def ranking_meters(self, route, now):
        self.hint_reads.append(route["route"])
        if self.explode:
            raise RuntimeError("PRIVATE-SENTINEL")
        return self.hints.get(route["route"], {})


class Due(Hinted):
    """A hinted source that also answers whether the on-demand ChatGPT usage read is due, and logs each question."""
    def __init__(self, hints=None, *, due=True, **options):
        super().__init__(hints, **options)
        self.due, self.due_reads, self.due_markers = due, [], []

    def usage_refresh_due(self, route, now):
        self.due_reads.append(route["route"])
        self.due_markers.append(route.get("account_marker"))
        if isinstance(self.due, Exception):
            raise self.due
        return self.due


class BalanceTest(unittest.TestCase):
    """Ordinary picks: a comparable healthy subscription may be promoted; every guard keeps the baseline."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.models = [model("claude-agent", "claude-sonnet-5-5"), model("opencode-go", "kimi-x-1")]
        self.hints = {CLAUDE_ROUTE: CLAUDE_PLAIN, GO_ROUTE: GO_HEALTHY}

    def select(self, source, *, models=None, raw=None, task=None):
        """One pick in a state folder of its own: a score cache left by an earlier pick would hide this one's `raw`."""
        request = dict(v=2, work_kind="review", task=dict(task or TASK), models=models or self.models)
        state = Path(tempfile.mkdtemp(dir=self.root))
        return select(request, state_dir=state, source=source, fetch=Fetch(raw or balance_body()), now=NOW)

    def test_a_healthy_comparable_go_route_is_promoted_over_the_claude_baseline(self):
        source = Hinted(self.hints)
        result = self.select(source)
        self.assertEqual((result["state"], result["route"]["route"]), ("selected", GO_ROUTE))
        self.assertEqual([item["route"] for item in result["alternatives"]], [CLAUDE_ROUTE])
        self.assertEqual(set(source.hint_reads), {CLAUDE_ROUTE, GO_ROUTE})
        self.assertNotIn("used_percent", json.dumps(result))
        for field in ("admission", "reservation", "spend_permit"):
            self.assertIs(result[field], False)

    def test_an_unknown_go_route_keeps_the_claude_baseline_first(self):
        result = self.select(Hinted({CLAUDE_ROUTE: CLAUDE_PLAIN}))
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)

    def test_go_below_the_score_or_price_bound_keeps_the_baseline_first(self):
        for label, raw in (("score", balance_body(go=(4, 20, 73))), ("price", balance_body(go=(5, 25, 75)))):
            with self.subTest(label=label):
                result = self.select(Hinted(self.hints), raw=raw)
                self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)

    def test_a_month_at_98_percent_blocks_the_go_boost(self):
        go = dict(GO_HEALTHY, monthly=fresh(98, 10 * 86400, None))
        result = self.select(Hinted({CLAUDE_ROUTE: CLAUDE_PLAIN, GO_ROUTE: go}))
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)

    def test_one_subscription_reads_no_hints(self):
        source = Hinted(self.hints)
        models = [model("claude-agent", "claude-sonnet-5-5"), model("claude-agent", "claude-opus-5-5")]
        result = self.select(source, models=models)
        self.assertEqual((result["state"], result["route"]["route"], source.hint_reads),
                         ("selected", CLAUDE_ROUTE, []))

    def test_below_level_keeps_the_strongest_baseline(self):
        raw = body(("anthropic/claude-opus-5-5", 10, 5, 25, 80), ("openai/gpt-6-2-sol", 20, 3, 19, 78),
                   ("google/gemini-4-neon", 30, 4, 20, 90), ("anthropic/claude-sonnet-5-5", 40, 3, 15, 76),
                   ("moonshot/kimi-x-1", 50, 4, 20, 75))
        result = self.select(Hinted(self.hints), raw=raw, task=TASK_CRITICAL)
        self.assertEqual((result["state"], result["route"]["route"], result["below_level"], result["level"]),
                         ("selected", CLAUDE_ROUTE, True, 3))

    def test_a_near_floor_winner_keeps_its_order(self):
        models = [model("claude-agent", "claude-haiku-5-5"), model("opencode-go", "kimi-x-1")]
        source = Hinted({HAIKU_ROUTE: GO_HEALTHY, GO_ROUTE: GO_HEALTHY})
        raw = balance_body(extra=[("anthropic/claude-haiku-5-5", 30, 1, 1, 59)])
        result = self.select(source, models=models, raw=raw)
        self.assertEqual((result["route"]["route"], result["below_level"]), (HAIKU_ROUTE, False))

    def test_unrouted_entries_never_get_a_hint_lookup_or_join_the_result(self):
        source = Hinted(self.hints)
        models = self.models + [model("opencode", "free-x"), model("openrouter", "pay-y")]
        result = self.select(source, models=models)
        self.assertEqual(result["route"]["route"], GO_ROUTE)
        self.assertEqual(set(source.hint_reads), {CLAUDE_ROUTE, GO_ROUTE})
        self.assertNotIn("free-x", json.dumps(result))
        self.assertNotIn("pay-y", json.dumps(result))

    def test_a_changed_baseline_is_excluded_and_survivors_keep_baseline_order(self):
        models = [model("claude-agent", "claude-sonnet-5-5"), model("openai", "gpt-6-2-sol"),
                  model("opencode-go", "kimi-x-1")]
        hints = {CLAUDE_ROUTE: CLAUDE_PLAIN, CHATGPT_ROUTE: CHATGPT_SOON, GO_ROUTE: GO_HEALTHY}
        result = self.select(Hinted(hints, drift={"opencode-claude-subscription"}), models=models)
        self.assertEqual(result["route"]["route"], CHATGPT_ROUTE)
        self.assertEqual([item["route"] for item in result["alternatives"]], [GO_ROUTE])
        self.assertIn(dict(route=CLAUDE_ROUTE, reason="account_changed"), result["excluded"])

    def test_a_changed_promoted_route_falls_back_to_the_baseline_order(self):
        models = [model("claude-agent", "claude-sonnet-5-5"), model("openai", "gpt-6-2-sol"),
                  model("opencode-go", "kimi-x-1")]
        hints = {CLAUDE_ROUTE: CLAUDE_PLAIN, CHATGPT_ROUTE: CHATGPT_SOON, GO_ROUTE: GO_HEALTHY}
        result = self.select(Hinted(hints, drift={"opencode-go-subscription"}), models=models)
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)
        self.assertEqual([item["route"] for item in result["alternatives"]], [CHATGPT_ROUTE])
        self.assertIn(dict(route=GO_ROUTE, reason="account_changed"), result["excluded"])

    def test_an_exploding_hint_read_leaves_the_selection_intact(self):
        result = self.select(Hinted(self.hints, explode=True))
        self.assertEqual((result["state"], result["route"]["route"]), ("selected", CLAUDE_ROUTE))
        for field in ("admission", "reservation", "spend_permit"):
            self.assertIs(result[field], False)
        self.assertNotIn("PRIVATE-SENTINEL", json.dumps(result))

    def alias_heavy(self):
        """Two Claude aliases share one scope, six Go aliases another. Baseline sonnet 5-5 (6); 5-6 (6.5); kimi 1-6
        (7 to 8.25). 5h scores: kimi 1 = 2.0 up to kimi 6 = 4.0; Claude aliases 1.0. Anchor 75, bar 60."""
        models = [model("claude-agent", "claude-sonnet-5-5"), model("claude-agent", "claude-sonnet-5-6")]
        models += [model("opencode-go", f"kimi-x-{n}") for n in range(1, 7)]
        hints = {CLAUDE_ROUTE: CLAUDE_PLAIN, SONNET_56: CLAUDE_PLAIN}
        hints.update({KIMI[n]: alias_go(60 - 8 * (n - 1)) for n in range(1, 7)})
        return models, hints

    def test_an_alias_heavy_group_rechecks_each_scope_once_and_confirms_three(self):
        models, hints = self.alias_heavy()
        source = Hinted(hints)
        phases = {}
        real = {name: getattr(selector, name) for name in ("_balance", "_confirm")}

        def measured(name):
            def run(*args):
                before = len(source.billed)
                value = real[name](*args)
                phases[name] = phases.get(name, 0) + len(source.billed) - before
                return value
            return run

        with mock.patch.object(selector, "_balance", measured("_balance")), \
                mock.patch.object(selector, "_confirm", measured("_confirm")):
            result = self.select(source, models=models, raw=alias_body())
        self.assertEqual(result["route"]["route"], KIMI[6])
        self.assertEqual([item["route"] for item in result["alternatives"]], [KIMI[5], KIMI[4]])
        self.assertEqual(len(source.billed) - sum(phases.values()), len(models))  # eligibility: one read per route
        self.assertEqual(phases, {"_balance": 2, "_confirm": 3})  # one recheck per scope; three confirmed picks

    def test_a_stale_baseline_beyond_the_pick_limit_is_still_excluded(self):
        models, hints = self.alias_heavy()
        result = self.select(Hinted(hints, drift={"opencode-claude-subscription"}), models=models, raw=alias_body())
        self.assertEqual(result["route"]["route"], KIMI[1])
        self.assertEqual([item["route"] for item in result["alternatives"]], [KIMI[2], KIMI[3]])
        self.assertIn(dict(route=CLAUDE_ROUTE, reason="account_changed"), result["excluded"])
        self.assertIn(dict(route=SONNET_56, reason="account_changed"), result["excluded"])

    def test_a_participant_changed_at_the_final_check_keeps_the_survivors_in_baseline_order(self):
        # Balanced order is Go, ChatGPT, Claude. Go changes only at `_confirm`'s own read: the survivors must be
        # confirmed in baseline order (Claude first), not in the reordered remainder (ChatGPT first).
        models = [model("claude-agent", "claude-sonnet-5-5"), model("openai", "gpt-6-2-sol"),
                  model("opencode-go", "kimi-x-1")]
        hints = {CLAUDE_ROUTE: CLAUDE_PLAIN, CHATGPT_ROUTE: CHATGPT_SOON, GO_ROUTE: GO_HEALTHY}
        result = self.select(Hinted(hints, late={"opencode-go-subscription"}), models=models)
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)
        self.assertEqual([item["route"] for item in result["alternatives"]], [CHATGPT_ROUTE])
        self.assertEqual([entry for entry in result["excluded"] if entry["reason"] == "account_changed"],
                         [dict(route=GO_ROUTE, reason="account_changed")])

    def test_a_drifted_scope_removes_every_changed_alias(self):
        models, hints = self.alias_heavy()
        result = self.select(Hinted(hints, drift={"opencode-go-subscription"}), models=models, raw=alias_body())
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)
        self.assertEqual([item["route"] for item in result["alternatives"]], [SONNET_56])
        for n in range(1, 7):
            self.assertIn(dict(route=KIMI[n], reason="account_changed"), result["excluded"])

    def test_a_later_alias_that_changed_invalidates_the_promoted_alias_of_the_same_binding(self):
        # Baseline Claude (6); the promoted ChatGPT alias sol (7); a later ChatGPT alias terra (10), too dear to be
        # comparable. ChatGPT reads: two for eligibility, one scope recheck, then sol's confirmation. The login rotates
        # before terra's: sol was confirmed under a binding that terra's read proves gone.
        models = [model("claude-agent", "claude-sonnet-5-5"), model("openai", "gpt-6-2-sol"),
                  model("openai", "gpt-6-2-terra")]
        raw = balance_body(extra=[("openai/gpt-6-2-terra", 60, 5, 25, 78)])
        hints = {CLAUDE_ROUTE: CLAUDE_PLAIN, CHATGPT_ROUTE: CHATGPT_SOON}
        steady = self.select(Hinted(hints), models=models, raw=raw)
        self.assertEqual(steady["route"]["route"], CHATGPT_ROUTE)  # without the rotation, sol is promoted
        self.assertEqual([item["route"] for item in steady["alternatives"]], [CLAUDE_ROUTE, TERRA_ROUTE])
        source = Hinted(hints, rotate={"opencode-chatgpt-subscription": 4})
        result = self.select(source, models=models, raw=raw)
        self.assertEqual((result["state"], result["route"]["route"]), ("selected", CLAUDE_ROUTE))
        self.assertEqual(result["alternatives"], [])
        self.assertEqual(sorted(entry["route"] for entry in result["excluded"] if entry["reason"] == "account_changed"),
                         sorted([CHATGPT_ROUTE, TERRA_ROUTE]))  # each alias of the lost binding, once
        self.assertEqual(len(source.billed), 3 + 2 + 3 + 1)  # eligibility, scopes, confirmation, then Claude alone

    def test_an_invalidated_binding_is_its_scope_and_original_marker(self):
        # After the scope recheck, sol and terra still carry the old ChatGPT marker and nova a newer one of the same
        # scope. Terra's confirmation proves the old binding gone: sol goes with it, nova and Claude stay.
        names = dict(base=CLAUDE_ROUTE, sol=CHATGPT_ROUTE, terra=TERRA_ROUTE,
                     nova="opencode-chatgpt-subscription:gpt-6-2-nova")
        login = {names["base"]: "claude", names["sol"]: "old", names["terra"]: "old", names["nova"]: "new"}
        source = Facts()

        def fact(item, producer):
            return dict(producer="invented:" + producer, path=item["path"], account=item["account"],
                        pool=item["pool"], observed_at=NOW, valid_until=NOW + 900, paid_crossover="provider_blocked")
        items = {}
        for position, (name, item) in enumerate(zip(names, routing_paths.routes(
                [model("claude-agent", "claude-sonnet-5-5"), model("openai", "gpt-6-2-sol"),
                 model("openai", "gpt-6-2-terra"), model("openai", "gpt-6-2-nova")]))):
            marker = subscription_billing.marker(fact(item, login[item["route"]]), item, NOW)
            items[name] = dict(item, account_marker=marker, price=6 + position, score=76)
        current = dict(login, **{names["terra"]: "new"})  # the rotation is visible from terra's read on
        source.billing = lambda route, now: fact(route, current[route["route"]])
        ordered = [items[name] for name in ("base", "sol", "terra", "nova")]
        pool = dict(source=source, fixed=NOW, excluded=[])

        def promoted(ordered, pool, qualifies):
            pool.update(reordered=frozenset((names["base"], names["sol"])), expires=NOW + 60, ranked=NOW)
            return [items[name] for name in ("sol", "base", "terra", "nova")]
        with mock.patch.object(selector, "_balance", promoted):
            chosen = selector._settle(ordered, pool, lambda item: True)
        self.assertEqual([item["route"] for item in chosen], [names["base"], names["nova"]])
        self.assertEqual(sorted(entry["route"] for entry in pool["excluded"]), sorted([names["sol"], names["terra"]]))
        self.assertEqual({entry["reason"] for entry in pool["excluded"]}, {"account_changed"})

    def test_the_price_bound_is_inclusive_at_exactly_one_and_a_half_times_the_baseline(self):
        # 0.225 is 1.5 times 0.15, and 1.5 * 0.15 is 0.22499999999999998 in binary: the bound is still met. One
        # millionth more on the output price is one proxy tick (0.00000025) above it.
        self.assertLess(1.5 * 0.15, 0.225)
        exact = self.select(Hinted(self.hints), raw=balance_body(claude=(0.15, 0.15, 76), go=(0.225, 0.225, 75)))
        self.assertEqual((exact["route"]["route"], exact["route"]["price"]), (GO_ROUTE, 0.225))
        self.assertEqual([(item["route"], item["price"]) for item in exact["alternatives"]], [(CLAUDE_ROUTE, 0.15)])
        above = self.select(Hinted(self.hints), raw=balance_body(claude=(0.15, 0.15, 76), go=(0.225, 0.225001, 75)))
        self.assertEqual((above["route"]["route"], above["alternatives"][0]["price"]), (CLAUDE_ROUTE, 0.22500025))


class ReviewBalanceTest(unittest.TestCase):
    """Inside a review, balancing compares only the qualified independent group, with that group's own anchor."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def review(self, source, models, raw, reviewing="opencode-go/a-small"):
        request = dict(v=2, work_kind="review", task=dict(TASK_CRITICAL), models=models, reviewing=reviewing)
        return select(request, state_dir=self.root, source=source, fetch=Fetch(raw), now=NOW)

    def test_the_known_independent_route_wins_over_a_same_lab_route_with_stronger_expiry(self):
        # Claude-path rows carry the anthropic creator and ChatGPT-path rows the openai creator, as the scorer requires.
        raw = body(("a/a-small", 50, 0.1, 0.5, 67), ("a/a-peer", 55, 0.2, 0.5, 67),
                   ("anthropic/b-small", 60, 1, 5, 67), ("openai/c-x", 70, 2, 4, 67))
        models = [model("opencode-go", "a-small"), model("opencode-go", "a-peer"), model("claude-agent", "b-small")]
        source = Hinted({"opencode-go-subscription:a-peer": GO_HEALTHY,
                         "opencode-claude-subscription:b-small": CLAUDE_PLAIN})
        result = self.review(source, models, raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))

    def test_independent_routes_compare_against_their_own_group_anchor(self):
        # Overall, a-small (0.2) is the baseline; c-x costs 2.5, beyond 1.5x of it, but within 1.5x of b-small (2).
        raw = body(("a/a-small", 50, 0.1, 0.5, 67), ("anthropic/b-small", 60, 1, 5, 67), ("openai/c-x", 70, 2, 4, 67))
        models = [model("opencode-go", "a-small"), model("claude-agent", "b-small"), model("opencode-go", "c-x")]
        source = Hinted({"opencode-claude-subscription:b-small": CLAUDE_PLAIN,
                         "opencode-go-subscription:c-x": GO_HEALTHY})
        result = self.review(source, models, raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("c-x", True))
        self.assertEqual([item["model"] for item in result["alternatives"]], ["b-small"])

    def test_an_independent_participant_changed_at_the_final_check_keeps_the_group_order(self):
        # Independent group in baseline order: b-small (Claude, 2), d-x (ChatGPT, 2.25), c-x (Go, 2.5). Balanced:
        # c-x, d-x, b-small. c-x changes only at `_confirm`'s read, so the group is confirmed again in baseline order.
        raw = body(("a/a-small", 50, 0.1, 0.5, 67), ("anthropic/b-small", 60, 1, 5, 67),
                   ("openai/c-x", 70, 2, 4, 67), ("openai/d-x", 80, 1, 6, 67))
        models = [model("opencode-go", "a-small"), model("claude-agent", "b-small"), model("openai", "d-x"),
                  model("opencode-go", "c-x")]
        source = Hinted({"opencode-claude-subscription:b-small": CLAUDE_PLAIN,
                         "opencode-chatgpt-subscription:d-x": CHATGPT_SOON,
                         "opencode-go-subscription:c-x": GO_HEALTHY}, late={"opencode-go-subscription"})
        result = self.review(source, models, raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))
        self.assertEqual([item["model"] for item in result["alternatives"]], ["d-x"])
        self.assertEqual([entry for entry in result["excluded"] if entry["reason"] == "account_changed"],
                         [dict(route="opencode-go-subscription:c-x", reason="account_changed")])

    def test_no_independent_route_confirms_keeps_the_fallback_without_balancing(self):
        # The author is on Claude (lab anthropic); a same-lab Go route would score 4.5 and so win if balanced.
        raw = body(("anthropic/a-small", 50, 0.1, 0.5, 67), ("anthropic/a-peer", 55, 0.2, 0.5, 67),
                   ("openai/b-small", 60, 1, 5, 67))
        models = [model("claude-agent", "a-small"), model("opencode-go", "a-peer"), model("openai", "b-small")]
        strong = {"5h": fresh(10, 3600, 18000), "7d": fresh(20, 4 * 86400, 604800),
                  "monthly": fresh(50, 10 * 86400, None)}  # 5h scores 4.5, with every sibling Go needs
        self.assertAlmostEqual(usage_balance.expiry_score("go", strong, now=NOW), 4.5)
        source = Due({"opencode-claude-subscription:a-small": CLAUDE_PLAIN,
                      "opencode-go-subscription:a-peer": strong,
                      "opencode-chatgpt-subscription:b-small": CHATGPT_SOON},
                     drift={"opencode-chatgpt-subscription"})
        result = self.review(source, models, raw, reviewing="claude-agent/a-small")
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("a-small", False))
        self.assertEqual([item["model"] for item in result["alternatives"]], ["a-peer"])  # baseline order, unbalanced
        self.assertIn(dict(route="opencode-chatgpt-subscription:b-small", reason="account_changed"), result["excluded"])
        self.assertEqual(source.hint_reads, [])  # the fallback group is never balanced, so no hint is read there
        self.assertEqual(source.due_reads, [])  # and it is no decision group for a usage read
        self.assertNotIn("usage_refresh", result)

    def test_a_fallback_group_that_keeps_chatgpt_and_a_rival_is_neither_balanced_nor_a_reason_to_read(self):
        # The author is on ChatGPT (lab openai). The only independent route, on Claude, changes account, so the
        # fallback holds ChatGPT's c-x (2.5) and a same-lab Go route c-y (2.75) that would score 4.5 if balanced.
        raw = body(("anthropic/b-small", 60, 1, 5, 67), ("openai/c-x", 70, 2, 4, 67), ("openai/c-y", 75, 2, 5, 67))
        models = [model("claude-agent", "b-small"), model("openai", "c-x"), model("opencode-go", "c-y")]
        strong = {"5h": fresh(10, 3600, 18000), "7d": fresh(20, 4 * 86400, 604800),
                  "monthly": fresh(50, 10 * 86400, None)}
        source = Due({"opencode-chatgpt-subscription:c-x": {"7d": fresh(0, 604800, 604800)},
                      "opencode-go-subscription:c-y": strong}, drift={"opencode-claude-subscription"})
        result = self.review(source, models, raw, reviewing="openai/c-x")
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("c-x", False))
        self.assertEqual([item["model"] for item in result["alternatives"]], ["c-y"])
        self.assertIn(dict(route="opencode-claude-subscription:b-small", reason="account_changed"), result["excluded"])
        self.assertEqual((source.hint_reads, source.due_reads), ([], []))
        self.assertNotIn("usage_refresh", result)

    def test_an_independent_group_with_chatgpt_and_another_subscription_asks_for_a_due_read(self):
        raw = body(("a/a-small", 50, 0.1, 0.5, 67), ("anthropic/b-small", 60, 1, 5, 67), ("openai/d-x", 80, 1, 6, 67))
        models = [model("opencode-go", "a-small"), model("claude-agent", "b-small"), model("openai", "d-x")]
        source = Due({"opencode-claude-subscription:b-small": CLAUDE_PLAIN})
        result = self.review(source, models, raw)
        self.assertEqual((result["route"]["model"], result["reviewer_independent"]), ("b-small", True))
        self.assertIs(result["usage_refresh"], True)
        self.assertEqual(source.due_reads, ["opencode-chatgpt-subscription:d-x"])
        alone = self.review(Due(), [model("opencode-go", "a-small"), model("openai", "d-x")], raw)
        self.assertEqual((alone["route"]["model"], alone["reviewer_independent"]), ("d-x", True))
        self.assertNotIn("usage_refresh", alone)  # the same-lab Go route is outside the independent group


class Ticking(Hinted):
    """A hinted source on a moving clock: the `jump_at`th billing read takes `jump` seconds. The account never changes."""
    def __init__(self, hints, clock, *, jump_at=None, jump=0):
        super().__init__(hints)
        self.clock, self.jump_at, self.jump = clock, jump_at, jump

    def billing(self, route, now):
        if len(self.billed) + 1 == self.jump_at:
            self.clock[0] += self.jump
        return super().billing(route, now)


class MovingClockTest(unittest.TestCase):
    """Ranking uses the clock of the decision, and a promotion does not outlive the readings that support it."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.models = [model("claude-agent", "claude-sonnet-5-5"), model("openai", "gpt-6-2-sol")]
        self.clock = [NOW]

    def hints(self, chatgpt):
        return {CLAUDE_ROUTE: {"7d": fresh(40, 72 * 3600, 604800)}, CHATGPT_ROUTE: {"7d": chatgpt}}

    def select(self, source, fetch):
        request = dict(v=2, work_kind="review", task=dict(TASK), models=self.models)
        state = Path(tempfile.mkdtemp(dir=self.root))
        with mock.patch("time.time", side_effect=lambda: self.clock[0]):
            result = select(request, state_dir=state, source=source, fetch=fetch)  # no fixed clock: the real path
        self.assertEqual([entry for entry in result["excluded"] if entry["reason"] == "account_changed"], [])
        for field in ("admission", "reservation", "spend_permit"):
            self.assertIs(result[field], False)
        return result

    def test_a_window_that_resets_during_the_score_fetch_no_longer_promotes(self):
        closing = dict(fresh(90, 2, 604800), earliest=NOW - 10, latest=NOW - 10)  # resets two seconds from now
        clock = self.clock

        class Slow(Fetch):
            def __call__(self, timeout):
                clock[0] += 5  # the first score download takes five seconds: the window has reset when it returns
                return super().__call__(timeout)
        result = self.select(Ticking(self.hints(closing), clock), Slow(balance_body()))
        self.assertEqual((result["state"], result["route"]["route"]), ("selected", CLAUDE_ROUTE))
        self.assertEqual([item["route"] for item in result["alternatives"]], [CHATGPT_ROUTE])
        self.clock[0] = NOW  # the same reading promotes while it is still fresh at the decision
        quick = self.select(Ticking(self.hints(closing), clock), Fetch(balance_body()))
        self.assertEqual(quick["route"]["route"], CHATGPT_ROUTE)

    def test_a_supporting_reading_that_expires_during_confirmation_discards_the_promotion(self):
        # Billing reads: two for eligibility, two for the scope recheck, then confirmation. The fifth takes ten seconds.
        resetting = dict(fresh(90, 5, 604800), earliest=NOW - 10, latest=NOW - 10)  # resets at NOW + 5
        aging = dict(fresh(90, 7200, 604800), earliest=NOW - 895, latest=NOW - 895)  # 900 seconds old at NOW + 5
        for label, chatgpt in (("reset", resetting), ("age", aging)):
            with self.subTest(label=label):
                self.clock[0] = NOW
                steady = self.select(Ticking(self.hints(chatgpt), self.clock), Fetch(balance_body()))
                self.assertEqual(steady["route"]["route"], CHATGPT_ROUTE)  # still fresh throughout: promoted
                self.clock[0] = NOW
                source = Ticking(self.hints(chatgpt), self.clock, jump_at=5, jump=10)
                result = self.select(source, Fetch(balance_body()))
                self.assertEqual((result["state"], result["route"]["route"]), ("selected", CLAUDE_ROUTE))
                self.assertEqual([item["route"] for item in result["alternatives"]], [CHATGPT_ROUTE])
                self.assertEqual(len(source.billed), 8)  # one more local confirmation of the two routes, no more
        self.clock[0] = NOW  # the baseline's own reading counts as well: Claude's ages out, the promotion goes
        hints = {CLAUDE_ROUTE: {"7d": dict(fresh(40, 72 * 3600, 604800), earliest=NOW - 895, latest=NOW - 895)},
                 CHATGPT_ROUTE: {"7d": fresh(90, 7200, 604800)}}
        result = self.select(Ticking(hints, self.clock, jump_at=5, jump=10), Fetch(balance_body()))
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)

    def test_a_clock_that_steps_back_during_confirmation_discards_the_promotion(self):
        # The readings were ranked at NOW. The fifth billing read, in the final confirmation, finds the clock ten
        # seconds earlier: the ranked snapshot is now dated in the future and supports nothing.
        weekly = fresh(90, 7200, 604800)
        steady = self.select(Ticking(self.hints(weekly), self.clock), Fetch(balance_body()))
        self.assertEqual(steady["route"]["route"], CHATGPT_ROUTE)
        self.clock[0] = NOW
        source = Ticking(self.hints(weekly), self.clock, jump_at=5, jump=-10)
        result = self.select(source, Fetch(balance_body()))  # asserts that no account is excluded as changed
        self.assertEqual((result["state"], result["route"]["route"]), ("selected", CLAUDE_ROUTE))
        self.assertEqual([item["route"] for item in result["alternatives"]], [CHATGPT_ROUTE])
        self.assertEqual(len(source.billed), 8)  # one more local confirmation of the two routes, no more

    def test_a_reading_the_scorer_ignores_cannot_cancel_a_valid_promotion(self):
        # ChatGPT's weekly reading stays valid throughout. Beside it sits a well-formed `5h` row that claims a
        # 60-second window: the scorer ignores it, so its reset during the slow confirmation ends nothing.
        weekly = fresh(90, 7200, 604800)
        bogus = dict(used_percent=10, earliest=NOW, latest=NOW, reset_at=NOW + 2, duration=60, discrete=True)
        self.assertTrue(usage_balance.fresh_meter(bogus, now=NOW))
        self.assertAlmostEqual(usage_balance.expiry_score("chatgpt", {"7d": weekly, "5h": bogus}, now=NOW), 8.4)
        for label, extra in (("wrong length", {"5h": bogus}), ("unknown key", {"other": bogus})):
            with self.subTest(label=label):
                self.clock[0] = NOW
                hints = {CLAUDE_ROUTE: {"7d": fresh(40, 72 * 3600, 604800)},
                         CHATGPT_ROUTE: dict({"7d": weekly}, **extra)}
                source = Ticking(hints, self.clock, jump_at=5, jump=10)  # confirmation ends at NOW + 10
                result = self.select(source, Fetch(balance_body()))
                self.assertEqual((result["state"], result["route"]["route"]), ("selected", CHATGPT_ROUTE))
                self.assertEqual([item["route"] for item in result["alternatives"]], [CLAUDE_ROUTE])
                self.assertEqual(len(source.billed), 6)  # no second confirmation
        self.clock[0] = NOW  # the same row under a key the scorer does use still ends the promotion
        hints = {CLAUDE_ROUTE: {"7d": fresh(40, 72 * 3600, 604800)},
                 CHATGPT_ROUTE: {"7d": weekly, "duration:60": bogus}}
        result = self.select(Ticking(hints, self.clock, jump_at=5, jump=10), Fetch(balance_body()))
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)

    def test_a_fixed_clock_keeps_fixtures_deterministic(self):
        closing = dict(fresh(90, 2, 604800), earliest=NOW - 10, latest=NOW - 10)
        request = dict(v=2, work_kind="review", task=dict(TASK), models=self.models)
        source = Ticking(self.hints(closing), self.clock, jump_at=5, jump=10)
        with mock.patch("time.time", side_effect=lambda: self.clock[0]):
            result = select(request, state_dir=self.root, source=source, fetch=Fetch(balance_body()), now=NOW)
        self.assertEqual(result["route"]["route"], CHATGPT_ROUTE)


class UsageRefreshTest(unittest.TestCase):
    """`usage_refresh` marks a due ChatGPT read only for the group that could actually be balanced."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.models = [model("claude-agent", "claude-sonnet-5-5"), model("openai", "gpt-6-2-sol")]
        self.weekly = {CLAUDE_ROUTE: {"7d": fresh(40, 72 * 3600, 604800)}}  # the reference Claude reading: 1.4

    def select(self, source, *, models=None, raw=None, task=None):
        """One pick in a state folder of its own: a score cache left by an earlier pick would hide this one's `raw`."""
        request = dict(v=2, work_kind="review", task=dict(task or TASK), models=models or self.models)
        state = Path(tempfile.mkdtemp(dir=self.root))
        result = select(request, state_dir=state, source=source, fetch=Fetch(raw or balance_body()), now=NOW)
        for field in ("admission", "reservation", "spend_permit"):
            self.assertIs(result[field], False)
        self.assertNotIn("used_percent", json.dumps(result))
        self.assertIn(result.get("usage_refresh"), (None, True))  # present only as true
        return result

    def test_the_hint_decides_the_order_and_a_reset_is_never_imputed(self):
        without = self.select(Due(self.weekly))
        self.assertEqual(without["route"]["route"], CLAUDE_ROUTE)  # no ChatGPT hint: the baseline
        closing = dict(self.weekly, **{CHATGPT_ROUTE: {"7d": fresh(90, 7200, 604800)}})  # 8.4
        self.assertEqual(self.select(Due(closing))["route"]["route"], CHATGPT_ROUTE)
        renewed = dict(self.weekly, **{CHATGPT_ROUTE: {"7d": fresh(0, 604800, 604800)}})  # a fresh reset scores 1.0
        self.assertEqual(self.select(Due(renewed))["route"]["route"], CLAUDE_ROUTE)
        unread = dict(self.weekly, **{CHATGPT_ROUTE: {"7d": dict(fresh(90, 7200, 604800), reset_at=NOW)}})
        self.assertEqual(self.select(Due(unread))["route"]["route"], CLAUDE_ROUTE)  # reset, not reread: unknown

    def test_an_unknown_chatgpt_baseline_with_a_comparable_rival_asks_for_a_read(self):
        source = Due(self.weekly)
        result = self.select(source, raw=balance_body(gpt=(3, 11, 78)))  # ChatGPT (5) is the baseline; Claude costs 6
        self.assertEqual(result["route"]["route"], CHATGPT_ROUTE)
        self.assertIs(result["usage_refresh"], True)
        self.assertEqual(source.due_reads, [CHATGPT_ROUTE])
        self.assertEqual(source.due_markers, [result["route"]["account_marker"]])  # asked for this binding only

    def test_a_chatgpt_rival_at_exactly_the_price_bound_asks_for_a_read(self):
        source = Due(self.weekly)
        result = self.select(source, raw=balance_body(claude=(0.15, 0.15, 76), gpt=(0.225, 0.225, 78)))
        self.assertEqual((result["route"]["route"], result.get("usage_refresh")), (CLAUDE_ROUTE, True))
        above = Due(self.weekly)
        result = self.select(above, raw=balance_body(claude=(0.15, 0.15, 76), gpt=(0.225, 0.225001, 78)))
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)
        self.assertNotIn("usage_refresh", result)
        self.assertEqual(above.due_reads, [])

    def test_an_unknown_chatgpt_baseline_without_a_comparable_rival_asks_for_nothing(self):
        for label, raw in (("price", balance_body(gpt=(1, 5, 78))), ("score", balance_body(gpt=(3, 11, 80)))):
            with self.subTest(label=label):
                source = Due(self.weekly)
                result = self.select(source, raw=raw)
                self.assertEqual(result["route"]["route"], CHATGPT_ROUTE)
                self.assertNotIn("usage_refresh", result)
                self.assertEqual(source.due_reads, [])

    def test_a_comparable_chatgpt_rival_of_another_baseline_asks_for_a_read(self):
        source = Due(self.weekly)
        result = self.select(source)
        self.assertEqual((result["route"]["route"], result["usage_refresh"]), (CLAUDE_ROUTE, True))
        beyond = Due(self.weekly)
        self.assertNotIn("usage_refresh", self.select(beyond, raw=balance_body(gpt=(5, 25, 78))))  # 10 > 1.5 x 6
        self.assertEqual(beyond.due_reads, [])

    def test_a_read_that_is_not_due_or_cannot_be_judged_is_not_requested(self):
        self.assertNotIn("usage_refresh", self.select(Due(self.weekly, due=False)))
        for answer in (None, 1, "yes", dict(state="due")):
            with self.subTest(answer=answer):
                self.assertNotIn("usage_refresh", self.select(Due(self.weekly, due=answer)))
        failing = self.select(Due(self.weekly, due=RuntimeError("PRIVATE-SENTINEL")))
        self.assertEqual((failing["state"], failing["route"]["route"]), ("selected", CLAUDE_ROUTE))
        self.assertNotIn("usage_refresh", failing)
        self.assertNotIn("PRIVATE-SENTINEL", json.dumps(failing))
        plain = Hinted(self.weekly)  # a source without the method never asks
        self.assertNotIn("usage_refresh", self.select(plain))

    def test_same_login_aliases_or_groups_without_chatgpt_ask_for_nothing(self):
        aliases = Due()
        raw = balance_body(extra=[("openai/gpt-6-2-terra", 60, 3, 20, 78)])
        result = self.select(aliases, models=[model("openai", "gpt-6-2-sol"), model("openai", "gpt-6-2-terra")],
                             raw=raw)
        self.assertEqual(result["route"]["route"], CHATGPT_ROUTE)
        self.assertNotIn("usage_refresh", result)
        self.assertEqual((aliases.due_reads, aliases.hint_reads), ([], []))
        others = Due({CLAUDE_ROUTE: CLAUDE_PLAIN, GO_ROUTE: GO_HEALTHY})
        result = self.select(others, models=[model("claude-agent", "claude-sonnet-5-5"),
                                             model("opencode-go", "kimi-x-1")])
        self.assertEqual(result["route"]["route"], GO_ROUTE)
        self.assertNotIn("usage_refresh", result)
        self.assertEqual(others.due_reads, [])

    def test_a_near_floor_winner_asks_for_nothing(self):
        near = Due()
        raw = balance_body(extra=[("anthropic/claude-haiku-5-5", 30, 1, 1, 59)])
        result = self.select(near, models=[model("claude-agent", "claude-haiku-5-5"), model("openai", "gpt-6-2-sol")],
                             raw=raw)
        self.assertEqual((result["route"]["route"], result["below_level"]), (HAIKU_ROUTE, False))
        self.assertNotIn("usage_refresh", result)
        self.assertEqual(near.due_reads, [])

    def test_a_below_level_winner_asks_for_nothing(self):
        below = Due()
        raw = body(("anthropic/claude-opus-5-5", 10, 5, 25, 80), ("openai/gpt-6-2-sol", 20, 3, 19, 78),
                   ("google/gemini-4-neon", 30, 4, 20, 90), ("anthropic/claude-sonnet-5-5", 40, 3, 15, 76),
                   ("moonshot/kimi-x-1", 50, 4, 20, 85))  # the third lab's best is 80: neither route reaches level 3
        result = self.select(below, raw=raw, task=TASK_CRITICAL)
        self.assertIs(result["below_level"], True)
        self.assertNotIn("usage_refresh", result)
        self.assertEqual(below.due_reads, [])

    def test_a_removed_route_never_carries_the_demand(self):
        # ChatGPT itself changed account: its stale route asks for nothing.
        gone = Due(self.weekly, drift={"opencode-chatgpt-subscription"})
        result = self.select(gone)
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)
        self.assertIn(dict(route=CHATGPT_ROUTE, reason="account_changed"), result["excluded"])
        self.assertNotIn("usage_refresh", result)
        self.assertEqual(gone.due_reads, [])
        # The only other subscription changed: the surviving ChatGPT aliases have nothing to compete with.
        raw = balance_body(extra=[("openai/gpt-6-2-terra", 60, 3, 20, 78)])
        models = self.models + [model("openai", "gpt-6-2-terra")]
        alone = Due(self.weekly, drift={"opencode-claude-subscription"})
        result = self.select(alone, models=models, raw=raw)
        self.assertEqual(result["route"]["route"], CHATGPT_ROUTE)
        self.assertIn(dict(route=CLAUDE_ROUTE, reason="account_changed"), result["excluded"])
        self.assertNotIn("usage_refresh", result)
        self.assertEqual(alone.due_reads, [])
        # A changed third subscription leaves the ChatGPT and Claude pair standing: the read is still wanted.
        three = self.models + [model("opencode-go", "kimi-x-1")]
        pair = Due(dict(self.weekly, **{GO_ROUTE: GO_HEALTHY}), drift={"opencode-go-subscription"})
        result = self.select(pair, models=three)
        self.assertEqual(result["route"]["route"], CLAUDE_ROUTE)
        self.assertIs(result["usage_refresh"], True)

    def test_a_council_pick_never_asks_for_a_read(self):
        source = Due(self.weekly)
        answer = council.pick(dict(v=1, count=2, models=self.models), state_dir=self.root / "state", source=source,
                              fetch=Fetch(balance_body()), now=NOW)
        self.assertEqual(answer["state"], "picked")
        self.assertNotIn("usage_refresh", answer)
        self.assertEqual(source.due_reads, [])


if __name__ == "__main__":
    unittest.main()
