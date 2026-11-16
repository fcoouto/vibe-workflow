"""O2/O3: the onboarding preference questions keep their headings and order, and question 7 is Smart Router."""
from pathlib import Path
import re
import unittest

PACKAGE = Path(__file__).resolve().parents[1]
PROCEDURE = PACKAGE / "skills/onboard-my-project/references/procedure.md"
QUESTIONS = [
    "How should agents publish completed task changes in `<repository_name>`?",
    "May agents merge pull requests into `<project_name>`’s primary branch, `<primary_branch_name>`?",
    "Automatically clean up task worktrees and their local and remote branches after their PRs merge in `<project_name>`?",
    "Enable Workstreams for `<project_name>`?",
    "Set up Graphify for `<project_name>`?",
    "Connect `<project_name>` to a long-term memory vault?",
    "Enable Smart Router for `<project_name>` on this machine?",
]


def slug(heading):
    """The GitHub anchor of a Markdown heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path):
    return {slug(line.lstrip("#")) for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("#")}


class OnboardingQuestionTests(unittest.TestCase):
    def section(self):
        text = PROCEDURE.read_text(encoding="utf-8")
        return text.split("\n## Project preference questions\n", 1)[1].split("\n## ", 1)[0]

    def question(self, number):
        return re.split(r"^\d+\. \*\*", self.section(), flags=re.M)[number]

    def test_the_seven_questions_keep_their_headings_and_order(self):
        found = re.findall(r"^(\d+)\. \*\*(.+?)\*\*$", self.section(), re.M)
        self.assertEqual([int(number) for number, _ in found], list(range(1, 8)))
        self.assertEqual([text for _, text in found], QUESTIONS)

    def test_the_smart_router_question_has_its_options_and_never_defaults_a_write(self):
        text = self.question(7)
        self.assertIn("- No. Keep my current worker choices. (default)", text)
        self.assertIn("- Yes. Route this project's workers through Smart Router.", text)
        for required in ("pending", "charge", "advisory", "managed launcher", "machine", "readback",
                         "one-way `authority_required`", "cannot be undone",
                         "enrolment integrated in the primary checkout", "never copy the routing list"):
            self.assertIn(required, text.lower())

    def test_the_smart_router_question_links_resolve(self):
        links = re.findall(r"\]\(([^)#\s]+)(?:#([^)\s]+))?\)", self.question(7))
        self.assertTrue(any(target.endswith("smart-routing.md") for target, _ in links), links)
        for target, anchor in links:
            path = (PROCEDURE.parent / target).resolve()
            with self.subTest(target=target, anchor=anchor):
                self.assertTrue(path.is_file())
                if anchor:
                    self.assertIn(anchor, anchors(path))

    def test_the_harness_has_a_smart_router_onboarding_case(self):
        cases = (PACKAGE / "assets/harness-evaluation/cases.md").read_text(encoding="utf-8")
        self.assertIn("| Smart Router onboarding |", cases)
        self.assertIn("after the merge-permission question", cases)  # the cleanup case's wording still holds


if __name__ == "__main__":
    unittest.main()
