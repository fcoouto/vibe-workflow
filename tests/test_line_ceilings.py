"""The line ceilings in scripts/validate.py only go down (AGENTS.md: legacy ceilings must not be raised).

BASELINE is the committed record. Lowering or removing a ceiling in validate.py needs no change here (lower the
baseline too when convenient); raising one, adding one or raising the general limit fails until this file is
changed as well, so it cannot happen silently in a diff to validate.py alone.
"""
import unittest

from scripts import validate

MAX_PYTHON_LINES = 1000
BASELINE = {
    "runtime/adapters/codex.py": 1579,
    "runtime/changes.py": 6073,
    "runtime/delegation.py": 3358,
    "runtime/evidence.py": 8505,
    "runtime/onboarding.py": 2620,
    "runtime/review_storage.py": 1297,
    "runtime/routing.py": 1415,
    "runtime/setup_observation.py": 1526,
    "runtime/startup.py": 1515,
    "runtime/state.py": 2613,
    "runtime/usage_budget.py": 1469,
}


class LineCeilingTests(unittest.TestCase):
    def test_no_ceiling_is_raised_or_added(self):
        self.assertLessEqual(validate.MAX_PYTHON_LINES, MAX_PYTHON_LINES)
        for path, ceiling in validate.OVERSIZED_PYTHON.items():
            with self.subTest(path):
                self.assertIn(path, BASELINE, "new ceiling entries are not allowed; split the file instead")
                self.assertLessEqual(ceiling, BASELINE[path], "a ceiling was raised; split the file instead")


if __name__ == "__main__":
    unittest.main()
