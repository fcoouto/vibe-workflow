"""Feedback cards: bounded store, refusals, decay, family weight and the adjustment formula."""
import importlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

NOW = 1_800_000_000
DAY = 86400


OTHER_LAB = object()  # default rater sentinel; None means an unknown rater


def session(number):
    return f"{number:032x}"


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        try:
            self.feedback = importlib.import_module("runtime.feedback")
        except ImportError:
            self.fail("runtime/feedback.py is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.make_state("state")

    def make_state(self, name):
        path = self.root / name
        path.mkdir(mode=0o700)
        return path

    def rated(self, key="claude-x-5-2", creator="anthropic", family="claude-x"):
        return dict(key=key, creator=creator, family=family)

    def card(self, number=1, rating=3, rater=OTHER_LAB, rated=None, **extra):
        value = dict(
            rated=rated or self.rated(),
            rater=dict(key="claude-y-1", creator="openai") if rater is OTHER_LAB else rater,
            work_kind="implementation",
            size_group="short",
            rating=rating,
            session=session(number),
            comment=None,
        )
        value.update(extra)
        return value

    def record(self, card, state=None, now=NOW):
        result = self.feedback.record(card, state_dir=state or self.state, now=now)
        self.assertEqual(result, dict(state="recorded"), result)
        return result

    def adjust(self, state=None, now=NOW, anchor=61.4, key="claude-x-5-2", creator="anthropic",
               family="claude-x", work_kind="implementation", size_group="short"):
        store = self.feedback.load(state or self.state)
        return self.feedback.adjustment(store, key=key, creator=creator, family=family,
                                        work_kind=work_kind, size_group=size_group,
                                        anchor=anchor, now=now)

    def write_store_file(self, raw, mode=0o600, state=None):
        path = (state or self.state) / self.feedback.FILE
        path.write_bytes(raw)
        path.chmod(mode)
        return path

    def six_cards(self):
        # Ratings 0,1,1,2,2,3 average 1.5 over six different sessions, all from another lab.
        for number, rating in enumerate([0, 1, 1, 2, 2, 3], start=1):
            self.record(self.card(number, rating))

    def test_three_sessions_needed(self):
        self.record(self.card(1, 0))
        self.record(self.card(2, 0))
        self.assertEqual(self.adjust(), dict(sessions=2, adjustment=0))
        self.record(self.card(3, 0))
        result = self.adjust()
        self.assertEqual(result["sessions"], 3)
        self.assertNotEqual(result["adjustment"], 0)

    def test_bounded_both_ways(self):
        cap = 0.10 * 61.4
        for number in range(1, 201):
            self.record(self.card(number, 0))
        low = self.adjust()["adjustment"]
        self.assertTrue(-cap <= low <= -0.97 * cap, low)
        state = self.make_state("high")
        for number in range(1, 201):
            self.record(self.card(number, 5), state=state)
        high = self.adjust(state=state)["adjustment"]
        self.assertTrue(0.97 * cap <= high <= cap, high)

    def test_shrinkage_and_formula(self):
        self.six_cards()
        result = self.adjust()
        self.assertEqual(result["sessions"], 6)
        self.assertAlmostEqual(result["adjustment"], 6.14 * ((1.5 - 3) / 3) * 6 / 11, delta=1e-6)

    def test_half_life(self):
        self.six_cards()
        later = self.adjust(now=NOW + 30 * DAY)["adjustment"]
        self.assertAlmostEqual(later, 6.14 * ((1.5 - 3) / 3) * 3 / 8, delta=1e-6)

    def test_rater_weights(self):
        cases = {
            "same-lab": (dict(key="claude-y-1", creator="anthropic"), 0.5),
            "unknown": (None, 0.5),
            "other-lab": (dict(key="claude-y-1", creator="openai"), 1.0),
        }
        for name, (rater, weight) in cases.items():
            with self.subTest(rater=name):
                state = self.make_state(name)
                self.record(self.card(1, 2, rater=rater), state=state)
                entry = self.feedback.load(state)["keys"]["anthropic/claude-x-5-2|implementation|short"]
                self.assertAlmostEqual(entry["w"], weight)
                self.assertAlmostEqual(entry["wr"], weight * 2)

    def test_self_rating_refused(self):
        self.record(self.card(1, 3))
        before = self.feedback.load(self.state)
        same = self.card(2, 5, rater=dict(key="claude-x-5-2", creator="anthropic"))
        result = self.feedback.record(same, state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="self_rating"))
        self.assertEqual(self.feedback.load(self.state), before)

    def test_one_vote_per_session_model_kind(self):
        self.record(self.card(1, 3))
        again = self.card(1, 4, size_group="long")
        self.assertEqual(self.feedback.record(again, state_dir=self.state, now=NOW),
                         dict(state="refused", reason="already_rated"))
        self.record(self.card(1, 4, work_kind="review"))

    def test_family_half_weight(self):
        older = self.rated(key="claude-x-5-1")
        for number in (1, 2, 3):
            self.record(self.card(number, 0, rated=older))
        result = self.adjust()
        self.assertEqual(result["sessions"], 3)
        expected = 0.10 * 61.4 * -1 * 1.5 / (1.5 + 5)
        self.assertAlmostEqual(result["adjustment"], expected, delta=1e-6)

    def test_family_never_counts_older_key_from_newer(self):
        newer = self.rated(key="claude-x-5-2")
        for number in (1, 2, 3):
            self.record(self.card(number, 0, rated=newer))
        self.assertEqual(self.adjust(key="claude-x-5-1"), dict(sessions=0, adjustment=0))

    def test_family_never_crosses_creators(self):
        older = self.rated(key="claude-x-5-1", creator="anthropic")
        for number in (1, 2, 3):
            self.record(self.card(number, 0, rated=older))
        self.assertEqual(self.adjust(creator="openai", key="claude-x-5-2"), dict(sessions=0, adjustment=0))

    def test_invalid_cards(self):
        bad = [
            self.card(1, 6),
            self.card(1, 2.5),
            self.card(1, 3, comment="x" * 281),
            self.card(1, 3, size_group="medium"),
            self.card(1, True),
            self.card(1, 3, session="not-hex"),
        ]
        for card in bad:
            with self.subTest(card=card):
                result = self.feedback.record(card, state_dir=self.state, now=NOW)
                self.assertEqual(result, dict(state="refused", reason="invalid"))
        self.assertIsNone(self.feedback.load(self.state))

    def test_damaged_store_falls_back_and_is_replaced(self):
        damaged = {
            "garbage": b"\x00not json",
            "wrong_version": json.dumps(dict(version=2, keys={}, votes={})).encode(),
            "oversized": b"x" * ((1 << 20) + 1),
        }
        for name, raw in damaged.items():
            with self.subTest(store=name):
                state = self.make_state(name)
                self.write_store_file(raw, state=state)
                self.assertIsNone(self.feedback.load(state))
                self.record(self.card(1, 3), state=state)
                self.assertEqual(self.feedback.load(state)["version"], 1)
                self.assertEqual(self.adjust(state=state), dict(sessions=1, adjustment=0))

    def test_unsafe_store_is_refused(self):
        target = self.root / "elsewhere.json"
        target.write_text(json.dumps(dict(version=1, keys={}, votes={})))
        target.chmod(0o600)
        (self.state / self.feedback.FILE).symlink_to(target)
        self.assertIsNone(self.feedback.load(self.state))
        result = self.feedback.record(self.card(1, 3), state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="store_unsafe"))
        self.assertEqual(json.loads(target.read_text()), dict(version=1, keys={}, votes={}))

        state = self.make_state("public")
        self.write_store_file(b"{}", mode=0o644, state=state)
        self.assertIsNone(self.feedback.load(state))
        result = self.feedback.record(self.card(1, 3), state_dir=state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="store_unsafe"))

    def test_bounds(self):
        with mock.patch.multiple(self.feedback, MAX_KEYS=3, MAX_VOTES=3):
            for index, name in enumerate(("alpha-1", "beta-1", "gamma-1", "delta-1")):
                self.record(self.card(index + 1, 3, rated=self.rated(key=name, family=name[:-2])), now=NOW + index)
            store = self.feedback.load(self.state)
            self.assertEqual(len(store["keys"]), 3)
            self.assertNotIn("anthropic/alpha-1|implementation|short", store["keys"])

            state = self.make_state("votes")
            for number in range(1, 5):
                self.record(self.card(number, 3), state=state, now=NOW + number)
            store = self.feedback.load(state)
            self.assertEqual(len(store["votes"]), 3)
            self.assertNotIn(f"{session(1)}|anthropic/claude-x-5-2|implementation", store["votes"])

            state = self.make_state("comments")
            for number in range(1, 13):
                self.record(self.card(number, 3, comment=f"c{number}"), state=state, now=NOW + number)
            entry = self.feedback.load(state)["keys"]["anthropic/claude-x-5-2|implementation|short"]
            self.assertEqual(entry["comments"], ["c10", "c11", "c12"])
            self.assertEqual(entry["sessions"], [session(n) for n in range(3, 13)])

    def test_concurrent_records(self):
        results = []
        threads = [
            threading.Thread(target=lambda n=n: results.append(
                self.feedback.record(self.card(n, 3), state_dir=self.state, now=NOW)))
            for n in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(r["state"] for r in results), ["recorded", "recorded"])
        store = self.feedback.load(self.state)
        self.assertIsNotNone(store)
        entry = store["keys"]["anthropic/claude-x-5-2|implementation|short"]
        self.assertEqual(sorted(entry["sessions"]), sorted([session(1), session(2)]))
        self.assertEqual(len(store["votes"]), 2)

    def test_clock_safety(self):
        future = 10 ** 12
        for number in (1, 2, 3):
            self.record(self.card(number, 0), now=future)
        cap = 0.10 * 61.4
        expected = cap * -1 * 3 / 8
        early = self.adjust(now=NOW)["adjustment"]
        self.assertTrue(math.isfinite(early))
        self.assertAlmostEqual(early, expected, delta=1e-6)
        self.assertEqual(self.adjust(now=future)["adjustment"], early)
        self.record(self.card(4, 0), now=NOW)
        entry = self.feedback.load(self.state)["keys"]["anthropic/claude-x-5-2|implementation|short"]
        self.assertTrue(math.isfinite(entry["w"]))
        self.assertAlmostEqual(entry["w"], 4.0)

    def test_last_rater(self):
        self.record(self.card(1, 3, rater=dict(key="claude-y-1", creator="openai")))
        self.assertEqual(self.feedback.load(self.state)["keys"]["anthropic/claude-x-5-2|implementation|short"]["last_rater"],
                         "openai/claude-y-1")
        self.record(self.card(2, 3, rater=None))
        self.assertIsNone(self.feedback.load(self.state)["keys"]["anthropic/claude-x-5-2|implementation|short"]["last_rater"])

    def test_missing_store_adjusts_nothing(self):
        self.assertEqual(self.adjust(state=self.root / "absent"), dict(sessions=0, adjustment=0))


if __name__ == "__main__":
    unittest.main()
