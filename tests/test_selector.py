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
from runtime import native_catalog, scores, selector
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


def model(provider, name, **extra):
    return dict(dict(provider=provider, id=name, model=None, family=None, variants=["low", "high"],
                     context=None, released=5), **extra)


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
        self.assertEqual((result["route"]["effort"], result["route"]["score"], result["route"]["price"],
                          result["route"]["score_source"], result["route"]["paid_overflow"]),
                         ("high", 78.0, 6.0, "openrouter", "blocked"))
        self.assertEqual([item["model"] for item in result["alternatives"]],
                         ["claude-sonnet-5-5", "claude-opus-5-5"])
        self.assertEqual(result["attribution"], "Scores: Artificial Analysis via OpenRouter")
        for field in ("admission", "reservation", "spend_permit"):
            self.assertIs(result[field], False)

    def test_first_run_fetches_once_and_reports_first_fetch(self):
        first = self.run_select()
        self.assertEqual((self.fetch.calls, first["first_fetch"]), ([scores.FIRST_TIMEOUT], True))
        self.assertNotIn("refresh", first)
        second = self.run_select()
        self.assertEqual((len(self.fetch.calls), second["first_fetch"]), (1, False))

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
        result = self.run_select(self.request(constraints=dict(allowed_efforts=["low"])))
        self.assertEqual((result["state"], result["reason"], {e["reason"] for e in result["excluded"]}),
                         ("no_selection", "no_recommendation", {"constraint"}))

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
