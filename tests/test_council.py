"""Protect the council roster check: per-seat status, order, validation and the no-authority boundary."""
import tempfile
from pathlib import Path
import unittest

from runtime import council

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


if __name__ == "__main__":
    unittest.main()
