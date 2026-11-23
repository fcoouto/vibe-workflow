"""Protect the council roster check: per-seat status, order, validation and the no-authority boundary."""
import tempfile
import json
from pathlib import Path
import subprocess
import sys
import unittest

from runtime import council, scores
from tests.runtime import test_selector as sel

NOW = 2_000_000


class Facts:
    def __init__(self, billing=True):
        self.calls = []
        self.paid = {}
        self.exhausted = set()
        self.has_billing = billing

    def acquire_billing(self, routes, now):
        self.calls.append(("acquire", len(routes)))

    def billing(self, route, now):
        if not self.has_billing:
            return None
        path = route["path"]
        return dict(producer="invented:first", path=path, account=route["account"], pool=route["pool"],
                    observed_at=now, valid_until=now + 900,
                    paid_crossover=self.paid.get(path, "provider_blocked"))

    def quota(self, route, now):
        return "quota_exhausted" if route["path"] in self.exhausted else None


def model(provider, name, **extra):
    return dict(dict(provider=provider, id=name, model=None, family=None, variants=["low", "high"],
                     context=None, released=5), **extra)


class CouncilTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name).resolve() / "state"

    def roster(self, models, source=None, **extra):
        return council.roster(dict(v=1, models=models, **extra), state_dir=self.state,
                              source=source or Facts(), now=NOW)

    def test_ok_seats_report_paid_overflow(self):
        facts = Facts()
        facts.paid = {"opencode-claude-subscription": "provider_blocked",
                      "opencode-chatgpt-subscription": "provider_managed",
                      "opencode-go-subscription": "unknown"}
        answer = self.roster([model("claude-agent", "opus"), model("openai", "x"), model("opencode-go", "g")], facts)
        self.assertEqual(answer["state"], "checked")
        self.assertEqual([(s["status"], s["paid_overflow"]) for s in answer["seats"]],
                         [("ok", "blocked"), ("ok", "enabled"), ("ok", "unknown")])

    def test_billing_unverified_and_quota_exhausted(self):
        answer = self.roster([model("openai", "x")], Facts(billing=False))
        self.assertEqual(answer["seats"], [dict(provider="openai", id="x", status="billing_unverified",
                                                paid_overflow=None)])
        facts = Facts()
        facts.exhausted = {"opencode-chatgpt-subscription"}
        seat = self.roster([model("openai", "x")], facts)["seats"][0]
        self.assertEqual((seat["status"], seat["paid_overflow"]), ("quota_exhausted", None))

    def test_order_and_duplicates(self):
        facts = Facts()
        answer = self.roster([model("openai", "x"), model("claude-agent", "opus"), model("openai", "x")], facts)
        self.assertEqual([(s["provider"], s["id"]) for s in answer["seats"]],
                         [("openai", "x"), ("claude-agent", "opus"), ("openai", "x")])
        self.assertEqual(facts.calls, [("acquire", 2)])

    def test_invalid_requests(self):
        one = model("openai", "x")
        bad = [dict(v=2, models=[one]), dict(v=1, models=[]), dict(v=1, models=[one] * 6),
               dict(v=1, models=[one], extra=1), dict(v=1, models=[model("opencode", "x")]),
               dict(v=1, models=[one, model("opencode", "x")]), dict(v=1), [], dict(v=True, models=[one])]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValueError):
                council.roster(request, state_dir=self.state, source=Facts(), now=NOW)

    def test_authority_fields_false(self):
        answer = self.roster([model("openai", "x")])
        for key in ("admission", "reservation", "spend_permit"):
            self.assertIs(answer[key], False)



class PickTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = sel.Facts()
        self.fetch = sel.Fetch()
        self.models = [sel.model("claude-agent", "claude-opus-5-5"), sel.model("claude-agent", "claude-sonnet-5-5"),
                       sel.model("openai", "gpt-6-2-sol")]

    def warm(self):
        self.assertEqual(scores.refresh(self.root, [], sel.NOW, fetch=self.fetch), "stored")
        self.fetch.calls.clear()

    def pick(self, count=3, now=sel.NOW, **extra):
        return council.pick(dict(v=1, count=count, models=self.models, **extra), state_dir=self.root,
                            source=self.source, fetch=self.fetch, now=now)

    def test_picks_one_per_lab_first(self):
        self.warm()
        answer = self.pick()
        self.assertEqual((answer["state"], answer["reason"]), ("picked", None))
        self.assertEqual([(s["provider"], s["id"], s["variant"], s["creator"]) for s in answer["seats"]],
                         [("claude-agent", "claude-opus-5-5", "high", "anthropic"),
                          ("openai", "gpt-6-2-sol", "high", "openai"),
                          ("claude-agent", "claude-sonnet-5-5", "high", "anthropic")])
        self.assertEqual([(s["score"], s["score_source"], s["paid_overflow"]) for s in answer["seats"]],
                         [(80.0, "openrouter", "blocked"), (78.0, "openrouter", "blocked"),
                          (76.0, "openrouter", "blocked")])
        self.assertEqual(answer["attribution"], "Scores: Artificial Analysis via OpenRouter")
        self.assertNotIn("refresh", answer)
        for key in ("admission", "reservation", "spend_permit"):
            self.assertIs(answer[key], False)

    def test_fewer_seats(self):
        self.warm()
        answer = self.pick(count=5)
        self.assertEqual((answer["reason"], len(answer["seats"])), ("fewer_seats", 3))

    def test_exhausted_plan_skipped(self):
        self.warm()
        self.source.exhausted = {"opencode-chatgpt-subscription"}
        answer = self.pick()
        self.assertEqual([s["id"] for s in answer["seats"]], ["claude-opus-5-5", "claude-sonnet-5-5"])
        self.assertEqual(answer["reason"], "fewer_seats")
        self.assertIn(dict(route="opencode-chatgpt-subscription:gpt-6-2-sol", reason="quota_exhausted"),
                      answer["excluded"])

    def test_account_change_drops_seat(self):
        self.warm()
        self.source.change = True
        # One model per plan: the fake reports a change from a plan's second read on.
        self.models = [self.models[0], self.models[2]]
        answer = self.pick(count=2)
        self.assertEqual((answer["seats"], answer["reason"]), ([], "no_eligible_route"))
        self.assertEqual({item["reason"] for item in answer["excluded"]}, {"account_changed"})

    def test_no_scores_answers_reason(self):
        self.fetch = sel.Fetch(None)
        answer = self.pick()
        self.assertEqual((answer["state"], answer["seats"], answer["reason"]), ("picked", [], "scores_unavailable"))
        self.assertEqual(self.fetch.calls, [scores.FIRST_TIMEOUT])

    def test_due_refresh_is_flagged(self):
        self.warm()
        self.assertEqual(self.pick(now=sel.NOW + scores.CEILING + 1)["refresh"], dict(wanted=[]))

    def test_invalid_pick_requests(self):
        models = self.models
        bad = [dict(v=2, count=3, models=models), dict(v=1, count=0, models=models), dict(v=1, count=6, models=models),
               dict(v=1, count=True, models=models), dict(v=1, count="3", models=models), dict(v=1, models=models),
               dict(v=1, count=3, models=models, extra=1), dict(v=1, count=3), dict(v=True, count=3, models=models), []]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValueError):
                council.pick(request, state_dir=self.root, source=self.source, fetch=self.fetch, now=sel.NOW)

    def test_cli_pick_failure_is_failed(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "select.py"
        done = subprocess.run([sys.executable, "-B", str(script), "--state-dir", str(self.root), "--council-pick"],
                              input=b'{"v": 1, "count": 9}', capture_output=True, timeout=60)
        self.assertEqual(json.loads(done.stdout), {"state": "failed"})


if __name__ == "__main__":
    unittest.main()
