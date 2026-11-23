import os
import pwd
import unittest

COMMAND = "python3 -B -m unittest discover -s tests -t ."


class DiscoveryTest(unittest.TestCase):
    def test_module_is_imported_through_the_tests_package(self):
        self.assertTrue(__name__.startswith("tests."), f"run the suite with: {COMMAND}")

    def test_home_is_not_the_real_home(self):
        real = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        self.assertNotEqual(os.path.realpath(os.environ["HOME"]), real, f"tests ran against the real home; run: {COMMAND}")


if __name__ == "__main__":
    unittest.main()
