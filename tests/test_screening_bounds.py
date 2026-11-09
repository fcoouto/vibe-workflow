"""Repository screening keeps the 8 MiB budget it shares with review storage; local fixtures only."""
from unittest.mock import patch

from runtime import onboarding, review_storage
from tests.fixture_canonical_birth import CanonicalBirthFixture, ScopedBirthFixture


MEBIBYTE = 1024 * 1024


class ScreeningBoundTests(CanonicalBirthFixture):
    def screen(self, megabytes):
        fixture = ScopedBirthFixture('runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        line = b"value = 'screening fixture source line'\n"
        remaining = int(megabytes * MEBIBYTE)
        index = 0
        while remaining > 0:
            size = min(remaining, 900 * 1024)
            (fixture.repo / f"module_{index}.py").write_bytes((line * (size // len(line) + 1))[:size])
            remaining -= size
            index += 1
        machine = fixture.base / 'machine' / 'machine.json'
        with patch.object(review_storage, '_machine_config_path', return_value=machine):
            self.assertTrue(review_storage.prepare_storage(fixture.base / 'private')['ok'])
            report = onboarding.inspect_project(fixture.repo, fixture.directory, fixture.project_record())
        return report['screening']

    def test_small_repository_screens_completely(self):
        self.assertEqual(self.screen(0.5)['status'], 'complete')

    def test_repositories_over_the_shared_reread_reservation_screen_partially(self):
        # Collection reserves room for two rereads inside 8 MiB; review storage checks the same budget.
        for megabytes in (3.5, 4.7):
            with self.subTest(megabytes=megabytes):
                screening = self.screen(megabytes)
                self.assertEqual(screening['status'], 'partial', screening)


if __name__ == '__main__':
    import unittest
    unittest.main()
