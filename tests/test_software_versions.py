"""Software release floors are lower bounds, never tested-version allowlists."""
import unittest

from runtime import software_versions as versions


class SoftwareVersionTests(unittest.TestCase):
    def test_equal_and_future_releases_pass_without_an_upper_bound(self):
        for version in ("2.2.6", "2.2.7", "2.3.0", "2.10.0", "3.0.0", "99.0.0", "2.2.6+build.9"):
            with self.subTest(version=version):
                self.assertIs(versions.at_least(version, "2.2.6"), True)

    def test_numeric_order_and_stable_floor_prereleases(self):
        for version in ("2.2.5", "2.1.99", "1.99.99", "2.2.6-alpha.1", "2.2.6rc1"):
            with self.subTest(version=version):
                self.assertIs(versions.at_least(version, "2.2.6"), False)
        self.assertIs(versions.at_least("2.3.0-alpha.1", "2.2.6"), True)
        self.assertIs(versions.at_least("3.11.0", "3.11"), True)
        self.assertIs(versions.at_least("25.7.0.post1", "25.7.0"), True)

    def test_python_distribution_development_releases_are_not_the_stable_floor(self):
        self.assertIs(versions.at_least("25.7.0.dev1", "25.7.0"), False)
        self.assertIs(versions.at_least("25.8.0.dev1", "25.7.0"), True)

    def test_missing_or_unrecognized_metadata_is_unknown_not_too_old(self):
        for version in (None, "", "unknown", 226, "2.2.bad", "2.2.6\n", "2.2.6/unsafe", "9" * 129):
            with self.subTest(version=version):
                self.assertIsNone(versions.at_least(version, "2.2.6"))

    def test_floor_requires_a_stable_numeric_release(self):
        for minimum in (None, "unknown", "2.2.6rc1", "2.2.6-alpha.1"):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                versions.at_least("2.2.7", minimum)


if __name__ == "__main__":
    unittest.main()
