"""An applicable catalog component ends `omit` only through a recorded user decision.

The `new-tool` row stands in for the 2026-11-08 Graphify row: applicable, absent, `omit`."""

import unittest

from tests import test_onboarding


class CoverageRuleTests(unittest.TestCase):
    setUp = test_onboarding.OnboardingTests.setUp
    git = test_onboarding.OnboardingTests.git
    module = test_onboarding.OnboardingTests.module
    inspect = test_onboarding.OnboardingTests.inspect
    plan = test_onboarding.OnboardingTests.plan
    proposal = test_onboarding.OnboardingTests.proposal

    def row_and_request(self, **changes):
        self.inspect()
        request = self.proposal(1)
        row = next(row for row in request["coverage"] if row["id"] == "new-tool")
        row.update(actions=[], reason="assessed for this project", **changes)
        return request, row

    def user_decision(self, disposition="confirmed", selected="Not now", evidence=("inventory",), rule="new-tool"):
        return {"id": "decline-new-tool", "rule": rule, "source": "README.md", "scope": "checkout",
            "options": ["Yes, set it up now", "Not now"], "recommendation": "Yes, set it up now",
            "selected": selected, "disposition": disposition, "provenance": "user answered the preference question",
            "evidence": list(evidence), "affected_actions": [], "invalidate_on": ["README.md"],
            "conflict": False, "mode": None}

    def submit(self, request):
        return self.module().record_plan(self.directory, request)

    def reasons(self, report):
        return [blocker["reason"] for blocker in report["blockers"]]

    def test_applicable_omit_without_a_decision_is_blocked(self):
        request, row = self.row_and_request(outcome="omit", applicability="applicable", current_adoption="absent")
        report = self.submit(request)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("cannot be omitted without a linked user decision", " ".join(self.reasons(report)))

    def test_optional_row_declined_by_a_recorded_user_decision_passes(self):
        request, row = self.row_and_request(outcome="omit", applicability="applicable", current_adoption="absent")
        request["decisions"] = [self.user_decision()]
        row["decision_ids"] = ["decline-new-tool"]
        report = self.submit(request)
        self.assertEqual(report["status"], "ready_for_review", report)

    def assert_pending_decision_does_not_count(self, disposition):
        request, row = self.row_and_request(outcome="omit", applicability="applicable")
        request["decisions"] = [self.user_decision(disposition, selected=None)]
        row["decision_ids"] = ["decline-new-tool"]
        report = self.submit(request)
        self.assertNotEqual(report["status"], "ready_for_review")
        self.assertIn("linked user decision", " ".join(self.reasons(report)))

    def test_a_pending_decision_does_not_count(self):
        self.assert_pending_decision_does_not_count("pending")

    def test_a_proposed_decision_does_not_count(self):
        self.assert_pending_decision_does_not_count("proposed")

    def test_a_decision_about_another_component_does_not_count(self):
        request, row = self.row_and_request(outcome="omit", applicability="applicable")
        request["decisions"] = [self.user_decision(rule="fixture")]
        row["decision_ids"] = ["decline-new-tool"]
        report = self.submit(request)
        self.assertNotEqual(report["status"], "ready_for_review")
        self.assertIn("linked user decision", " ".join(self.reasons(report)))

    def test_an_inherited_decision_without_evidence_blocks_the_plan(self):
        request, row = self.row_and_request(outcome="omit", applicability="applicable")
        request["decisions"] = [self.user_decision("inherited", evidence=())]
        row["decision_ids"] = ["decline-new-tool"]
        report = self.submit(request)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Inherited choice lacks evidence", " ".join(self.reasons(report)))

    def test_inapplicable_omit_with_a_reason_passes_without_a_decision(self):
        request, row = self.row_and_request(outcome="omit", applicability="inapplicable")
        report = self.submit(request)
        self.assertEqual(report["status"], "ready_for_review", report)

    def test_mandatory_row_never_omits_even_with_a_user_decision(self):
        self.inspect()
        request = self.proposal(1)
        row = next(row for row in request["coverage"] if row["id"] == "fixture")
        row.update(outcome="omit", applicability="applicable", actions=[], decision_ids=["decline-new-tool"])
        request["decisions"] = [self.user_decision()]
        report = self.submit(request)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Mandatory adoption cannot be omitted", " ".join(self.reasons(report)))

    def blocked_reasons(self, blocker, resumption):
        request, row = self.row_and_request(outcome="blocked", blocker=blocker, resumption=resumption)
        report = self.submit(request)
        self.assertEqual(report["status"], "blocked")
        return " | ".join(self.reasons(report))

    def test_blocked_row_without_a_resume_condition_is_rejected(self):
        self.assertIn("needs a prerequisite and a resume condition", self.blocked_reasons("needs a sign-in", ""))

    def test_blocked_row_without_a_prerequisite_is_rejected(self):
        self.assertIn("needs a prerequisite and a resume condition", self.blocked_reasons("", "after sign-in"))

    def test_blocked_row_with_prerequisite_and_resume_condition_reports_its_blocker(self):
        reasons = self.blocked_reasons("needs a sign-in", "after sign-in")
        self.assertIn("needs a sign-in", reasons)
        self.assertNotIn("needs a prerequisite and a resume condition", reasons)


if __name__ == "__main__":
    unittest.main()
