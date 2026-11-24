"""Feedback cards: bounded store, refusals, decay, family weight and the adjustment formula."""
import importlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

NOW = 1_800_000_000
DAY = 86400


OTHER_LAB = object()  # default rater sentinel; None means an unknown rater
ANTHROPIC_KEY = "anthropic/claude-x-5-2|implementation|short"


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

    def test_a_prefixed_lab_is_the_same_lab(self):
        refused = self.feedback.record(self.card(1, 5, rater=dict(key="claude-x-5-2", creator="~anthropic")),
                                       state_dir=self.state, now=NOW)
        self.assertEqual(refused, dict(state="refused", reason="self_rating"))
        self.record(self.card(2, 2, rater=dict(key="claude-y-1", creator="~anthropic")))
        entry = self.feedback.load(self.state)["keys"]["anthropic/claude-x-5-2|implementation|short"]
        self.assertAlmostEqual(entry["w"], 0.5)
        older = self.rated(key="claude-x-5-1", creator="~anthropic")
        state = self.make_state("prefixed")
        for number in (1, 2, 3):
            self.record(self.card(number, 0, rated=older), state=state)
        result = self.adjust(state=state, creator="anthropic", key="claude-x-5-2")
        self.assertEqual(result["sessions"], 3)
        self.assertLess(result["adjustment"], 0)

    def test_one_canonical_lab_in_store_keys_votes_and_history(self):
        tilde = self.rated(creator="~anthropic")
        for number in (1, 2, 3):
            self.record(self.card(number, 0, rated=tilde))
        store = self.feedback.load(self.state)
        self.assertEqual(list(store["keys"]), [ANTHROPIC_KEY])
        self.assertTrue(all("~" not in name for name in store["votes"]))
        self.assertEqual(self.adjust(creator="anthropic")["sessions"], 3)
        self.assertEqual(self.adjust(creator="~anthropic")["sessions"], 3)
        again = self.feedback.record(self.card(1, 4), state_dir=self.state, now=NOW)
        self.assertEqual(again, dict(state="refused", reason="already_rated"))

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

    def valid_entry(self, **changes):
        entry = dict(w=2.0, wr=4.0, at=NOW, family="claude-x", sessions=[session(1)],
                     comments=[], last_rater=None)
        entry.update(changes)
        return entry

    def store_bytes(self, keys=None, votes=None):
        if keys is None:
            keys = {ANTHROPIC_KEY: self.valid_entry()}
        return json.dumps(dict(version=1, keys=keys, votes=votes or {})).encode()

    def assert_damaged_then_replaced(self, raw, name):
        state = self.make_state(name)
        self.write_store_file(raw, state=state)
        self.assertIsNone(self.feedback.load(state))
        self.record(self.card(1, 3), state=state)
        self.assertEqual(self.feedback.load(state)["keys"][ANTHROPIC_KEY]["sessions"], [session(1)])

    def test_baseline_store_is_valid(self):
        self.write_store_file(self.store_bytes())
        self.assertIsNotNone(self.feedback.load(self.state))

    def test_numbers_beyond_float_range_fall_back(self):
        cases = {"w": dict(w=10 ** 400), "wr": dict(wr=10 ** 400), "at": dict(at=10 ** 400)}
        for name, changes in cases.items():
            with self.subTest(field=name):
                self.assert_damaged_then_replaced(
                    self.store_bytes({ANTHROPIC_KEY: self.valid_entry(**changes)}), "big-" + name)

    def test_malformed_stores_fall_back(self):
        vote = f"{session(1)}|anthropic/claude-x-5-2|implementation"
        cases = {
            "non_hex_sessions": dict(keys={ANTHROPIC_KEY: self.valid_entry(sessions=["xyz", "not-hex", "zz"])}),
            "too_many_sessions": dict(keys={ANTHROPIC_KEY: self.valid_entry(
                sessions=[session(n) for n in range(11)])}),
            "long_comment": dict(keys={ANTHROPIC_KEY: self.valid_entry(comments=["x" * 281])}),
            "too_many_comments": dict(keys={ANTHROPIC_KEY: self.valid_entry(comments=["a", "b", "c", "d"])}),
            "impossible_totals": dict(keys={ANTHROPIC_KEY: self.valid_entry(w=1.0, wr=6.0)}),
            "negative_weight": dict(keys={ANTHROPIC_KEY: self.valid_entry(w=-1.0, wr=-1.0)}),
            "bad_last_rater": dict(keys={ANTHROPIC_KEY: self.valid_entry(last_rater="no-slash")}),
            "family_mismatch": dict(keys={ANTHROPIC_KEY: self.valid_entry(family="other")}),
            "unknown_kind": dict(keys={"anthropic/claude-x-5-2|invented|short": self.valid_entry()}),
            "bad_vote_name": dict(votes={"not-a-vote": NOW}),
            "vote_not_int": dict(votes={vote: "soon"}),
        }
        for name, changes in cases.items():
            with self.subTest(store=name):
                self.assert_damaged_then_replaced(self.store_bytes(**changes), "bad-" + name)

    def test_store_counts_are_bounded(self):
        keys = {f"anthropic/{m}-1|implementation|short": self.valid_entry(family=m)
                for m in ("alpha", "beta", "gamma")}
        with mock.patch.multiple(self.feedback, MAX_KEYS=2):
            self.assert_damaged_then_replaced(self.store_bytes(keys=keys), "too-many-keys")
        votes = {f"{session(n)}|anthropic/claude-x-5-2|implementation": NOW for n in (1, 2)}
        with mock.patch.multiple(self.feedback, MAX_VOTES=1):
            self.assert_damaged_then_replaced(self.store_bytes(votes=votes), "too-many-votes")

    def test_oversized_write_is_refused_as_store_full(self):
        with mock.patch("runtime.storage.MAX_FILE", 100):
            result = self.feedback.record(self.card(1, 3), state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="store_full"))
        self.assertIsNone(self.feedback.load(self.state))

    def test_unexpected_failure_is_store_error(self):
        with mock.patch("runtime.storage._write", side_effect=RuntimeError("synthetic")):
            result = self.feedback.record(self.card(1, 3), state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="store_error"))

    def test_unknown_work_kind_refused(self):
        result = self.feedback.record(self.card(1, 3, work_kind="invented"), state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="invalid"))
        self.assertIsNone(self.feedback.load(self.state))

    def test_totals_at_the_bound_stay_valid(self):
        bound = self.feedback.WEIGHT_MAX
        entry = self.valid_entry(w=float(bound), wr=3.0 * bound)
        self.write_store_file(self.store_bytes({ANTHROPIC_KEY: entry}))
        self.record(self.card(2, 5))
        store = self.feedback.load(self.state)
        self.assertIsNotNone(store)
        stored = store["keys"][ANTHROPIC_KEY]
        self.assertLessEqual(stored["w"], bound)
        self.assertGreater(stored["wr"] / stored["w"], 3)

    def test_rating_total_at_its_edge_stays_valid(self):
        for name, wr in (("exact", 5 * 999999.9), ("over-tolerance", 5000000)):
            with self.subTest(start=name):
                state = self.make_state(name)
                self.write_store_file(self.store_bytes({ANTHROPIC_KEY: self.valid_entry(
                    w=999999.9, wr=float(wr))}), state=state)
                self.record(self.card(2, 5), state=state)
                store = self.feedback.load(state)
                self.assertIsNotNone(store)
                stored = store["keys"][ANTHROPIC_KEY]
                self.assertLessEqual(stored["wr"], 5 * stored["w"])

    def test_store_failing_validation_is_never_written(self):
        self.write_store_file(self.store_bytes())
        before = (self.state / self.feedback.FILE).read_bytes()
        real = self.feedback._valid
        calls = []

        def reject_the_write_check(value):
            calls.append(value)
            return real(value) if len(calls) == 1 else None

        with mock.patch.object(self.feedback, "_valid", side_effect=reject_the_write_check):
            result = self.feedback.record(self.card(2, 3), state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="store_error"))
        self.assertEqual((self.state / self.feedback.FILE).read_bytes(), before)

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


class FeedbackSubmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)

    def entry(self, provider="claude-agent", id="opus", model="claude-x-5-2", family="claude-x"):
        return dict(provider=provider, id=id, model=model, name="Opus", family=family,
                    variants=["low", "high"], context=None, released=5)

    def request(self, rater=None, **changes):
        value = dict(v=1, rated=dict(key="claude-x-5-2", creator="anthropic", family="claude-x"),
                     rater_entry=rater, work_kind="implementation", size_group="short", rating=3,
                     session=session(1), comment=None)
        value.update(changes)
        return value

    def test_rater_id_names_the_creator_and_key(self):
        from runtime import selector
        self.assertEqual(selector.rater_id(self.entry(provider="openai", id="gpt-6-2", model="gpt-6-2",
                                                      family="gpt-6"), state_dir=self.state),
                         dict(creator="openai", key="gpt-6-2"))
        self.assertEqual(selector.rater_id(self.entry(), state_dir=self.state),
                         dict(creator="anthropic", key="claude-x-5-2"))
        self.assertIsNone(selector.rater_id(self.entry(provider="unknown-lab"), state_dir=self.state))

    def test_submit_resolves_rater_and_refuses_self(self):
        from runtime import selector
        result = selector.feedback_submit(self.request(rater=self.entry()), state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="self_rating"))

    def test_unknown_rater_counts_half(self):
        from runtime import feedback, selector
        result = selector.feedback_submit(self.request(rater=self.entry(provider="unknown-lab")),
                                          state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="recorded"))
        entry = feedback.load(self.state)["keys"]["anthropic/claude-x-5-2|implementation|short"]
        self.assertAlmostEqual(entry["w"], 0.5)
        self.assertAlmostEqual(entry["wr"], 0.5 * 3)

    def test_same_model_on_another_provider_is_refused_as_self_rating(self):
        from runtime import selector
        for model in (dict(provider="other-host", id="claude-x-5-2"), dict(provider="other-host", id="vendor/Claude-X-5-2")):
            with self.subTest(model=model):
                result = selector.feedback_submit(self.request(rater=None, rater_model=model),
                                                  state_dir=self.state, now=NOW)
                self.assertEqual(result, dict(state="refused", reason="self_rating"))

    def test_claude_agent_alias_resolves_through_the_stored_catalog(self):
        from runtime import selector
        with mock.patch.object(selector.native_catalog, "stored",
                               return_value=dict(models=[dict(alias="opus", resolved="claude-x-5-2")])):
            result = selector.feedback_submit(
                self.request(rater=None, rater_model=dict(provider="claude-agent", id="opus")),
                state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="self_rating"))

    def test_a_different_model_not_on_the_plan_still_counts_half(self):
        from runtime import feedback, selector
        result = selector.feedback_submit(
            self.request(rater=None, rater_model=dict(provider="other-host", id="mystery-9")),
            state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="recorded"))
        entry = feedback.load(self.state)["keys"]["anthropic/claude-x-5-2|implementation|short"]
        self.assertAlmostEqual(entry["w"], 0.5)

    def test_a_failing_score_cache_keeps_the_normalized_key_for_the_self_check(self):
        from runtime import selector
        with mock.patch.object(selector.scores, "load", side_effect=OSError("unreadable")):
            result = selector.feedback_submit(
                self.request(rater=None, rater_model=dict(provider="other-host", id="claude-x-5-2")),
                state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="refused", reason="self_rating"))

    def test_a_routed_rater_is_not_judged_by_its_display_id(self):
        from runtime import feedback, selector
        routed = self.entry(provider="openai", id="claude-x-5-2", model="gpt-6-2", family="gpt-6")
        result = selector.feedback_submit(
            self.request(rater=routed, rater_model=dict(provider="openai", id="claude-x-5-2")),
            state_dir=self.state, now=NOW)
        self.assertEqual(result, dict(state="recorded"))
        entry = feedback.load(self.state)["keys"]["anthropic/claude-x-5-2|implementation|short"]
        self.assertAlmostEqual(entry["w"], 1.0)

    def test_rater_model_must_be_a_provider_id_pair(self):
        from runtime import selector
        for bad in ("opus", dict(provider="p"), dict(provider=1, id="x")):
            with self.subTest(bad=bad):
                self.assertEqual(selector.feedback_submit(self.request(rater_model=bad), state_dir=self.state, now=NOW),
                                 dict(state="refused", reason="invalid"))

    def test_submit_invalid_shape(self):
        from runtime import selector
        base = self.request()
        missing = dict(base)
        del missing["rated"]
        for name, value in (("missing_rated", missing), ("version_2", dict(base, v=2)),
                            ("bad_rater_entry", dict(base, rater_entry="opus")),
                            ("extra_field", dict(base, extra=1))):
            with self.subTest(request=name):
                self.assertEqual(selector.feedback_submit(value, state_dir=self.state, now=NOW),
                                 dict(state="refused", reason="invalid"))

    def test_cli_records_a_valid_card(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "select.py"
        payload = json.dumps(self.request()).encode()
        done = subprocess.run([sys.executable, "-B", str(script), "--state-dir", str(self.state), "--feedback"],
                              input=payload, capture_output=True, timeout=60)
        self.assertEqual(json.loads(done.stdout), {"state": "recorded"})

    def test_cli_failure_is_failed(self):
        script = Path(__file__).resolve().parents[2] / "scripts" / "select.py"
        done = subprocess.run([sys.executable, "-B", str(script), "--state-dir", str(self.state), "--feedback"],
                              input=b"not json", capture_output=True, timeout=60)
        self.assertEqual(json.loads(done.stdout), {"state": "failed"})


if __name__ == "__main__":
    unittest.main()
