"""Persisted bindings survive a renumbered volume; copies, moves and in-operation swaps still fail.

Disposable checkouts only: no client, network or real enrollment."""

import copy
import json
import os
from pathlib import Path
import shutil
import unittest

from runtime import local_ownership, onboarding, persisted_binding, startup
from tests import test_unenrolled_checkout


def renumbered(value, shift=1):
    """A copy of a record whose every stored device number differs, as after macOS renumbers a volume."""
    if isinstance(value, dict):
        return {key: (item + shift if key == "device" and type(item) is int else renumbered(item, shift))
                for key, item in value.items()}
    if isinstance(value, list):
        return [renumbered(item, shift) for item in value]
    return value


class MatchTests(unittest.TestCase):
    def test_device_is_ignored_and_everything_else_is_compared(self):
        self.assertTrue(persisted_binding.matches(dict(device=1, inode=7), dict(device=2, inode=7)))
        self.assertFalse(persisted_binding.matches(dict(device=1, inode=7), dict(device=1, inode=8)))
        self.assertFalse(persisted_binding.matches(dict(device=1, inode=7, size=3), dict(device=2, inode=7, size=4)))
        for stored, current in ((None, dict(device=1, inode=7)), (dict(device=1, inode=7), None), (dict(inode=7), dict(inode=7)),
                                (dict(device=1), dict(device=2)), ([1, 7], [1, 7])):
            with self.subTest(stored=stored, current=current):
                self.assertFalse(persisted_binding.matches(stored, current))

    def test_records_ignore_only_nested_devices_and_keep_exact_types(self):
        stored = dict(scope_id="s", authority_directory=dict(device=1, inode=7), parents=[dict(device=1, inode=3)])
        self.assertTrue(persisted_binding.record_matches(stored, renumbered(stored)))
        for changed in (dict(stored, scope_id="t"), dict(stored, authority_directory=dict(device=1, inode=8)),
                        dict(stored, authority_directory=dict(device=1.0, inode=7)), dict(stored, parents=(dict(device=1, inode=3),)),
                        dict(stored, revision=True), {k: v for k, v in stored.items() if k != "parents"}):
            with self.subTest(changed=changed):
                self.assertFalse(persisted_binding.record_matches(changed, stored))
        self.assertFalse(persisted_binding.record_matches(dict(revision=1), dict(revision=True)))

    def test_maps_need_the_same_names(self):
        stored = dict(root=dict(device=1, inode=7), git_dir=dict(device=1, inode=8))
        self.assertTrue(persisted_binding.all_match(stored, renumbered(stored)))
        self.assertFalse(persisted_binding.all_match(stored, dict(root=dict(device=2, inode=7))))
        self.assertFalse(persisted_binding.all_match(stored, dict(stored, root=dict(device=1, inode=9))))


class RenumberedVolumeTests(unittest.TestCase):
    """A record written before the volume was renumbered is still the same enrollment."""
    client = "codex"
    git = test_unenrolled_checkout.UnenrolledCheckoutTests.git
    setUp = test_unenrolled_checkout.UnenrolledCheckoutTests.setUp
    acquire = test_unenrolled_checkout.UnenrolledCheckoutTests.acquire
    hook = test_unenrolled_checkout.UnenrolledCheckoutTests.hook

    def write(self, record):
        self.authority.write_text(json.dumps(record))

    def test_a_renumbered_record_still_orients(self):
        self.write(renumbered(self.record))
        result = self.acquire(self.repo)
        self.assertEqual((result.resolution.status, result.resolution.reason), ("configured", "activation_configured"))
        self.assertIsNone(self.hook(self.repo))

    def test_a_renumbered_record_keeps_local_ownership(self):
        self.write(renumbered(self.record))
        owner = local_ownership._acquire(self.repo, settings=True)
        self.assertEqual(owner.repository_root, self.repo)

    def test_a_copied_checkout_at_the_same_path_is_rejected(self):
        original = self.base / "original"
        self.repo.rename(original)
        shutil.copytree(original, self.repo, symlinks=True)
        for record in (self.record, renumbered(self.record)):
            with self.subTest(renumbered=record is not self.record):
                self.write(record)
                result = self.acquire(self.repo)
                self.assertEqual((result.resolution.status, result.resolution.reason), ("conflict", "identity_changed"))
                with self.assertRaises(startup.StartupError):
                    local_ownership._acquire(self.repo, settings=True)

    def test_a_moved_checkout_is_rejected(self):
        self.write(renumbered(self.record))
        moved = self.base / "moved"
        self.repo.rename(moved)
        result = self.acquire(moved)
        self.assertEqual(result.resolution.status, "conflict")
        self.assertIsNone(result.resolution.scope)

    def test_a_changed_inode_in_the_comparator_is_still_identity_changed(self):
        acquired = startup._acquire_canonical_membership(self.repo, authority_path=None, budget={"paths": 0, "bytes": 0})
        facts = dict(authority=dict(path=str(acquired.authority_path), target=startup._canonical_plain(acquired.authority_target)),
            observation=startup._canonical_plain(acquired.observation), client="codex",
            installed=dict(policy_digest=startup.policy_digest(test_unenrolled_checkout.PACKAGE),
                           runtime_digest=self.record["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["runtime_digest"],
                           clients=["codex"], package_root=str(test_unenrolled_checkout.PACKAGE)))
        self.assertEqual(startup.resolve_canonical_scope(acquired.record, **facts).reason, "activation_configured")
        shifted = copy.deepcopy(facts)
        shifted["observation"] = renumbered(shifted["observation"])
        shifted["authority"]["target"]["parents"] = renumbered(shifted["authority"]["target"]["parents"])
        self.assertEqual(startup.resolve_canonical_scope(acquired.record, **shifted).reason, "activation_configured")
        for name in ("project_binding", "root", "git_dir", "primary_root", "git_common_dir"):
            with self.subTest(name=name):
                changed = copy.deepcopy(shifted)
                binding = changed["observation"]["project_binding" if name == "project_binding" else "binding"]
                target = binding if name == "project_binding" else binding[name]
                target["inode"] += 1000
                result = startup.resolve_canonical_scope(acquired.record, **changed)
                self.assertEqual(result.status, "conflict")


class InOperationTests(unittest.TestCase):
    """A binding taken within one operation still compares the device."""

    def test_reading_a_file_checks_device_and_inode(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="vibe binding ") as base:
            path = Path(base).resolve() / "input.json"
            path.write_text("{}")
            info = path.stat()
            self.assertEqual(onboarding._read_file(path, 10, expected_binding=dict(device=info.st_dev, inode=info.st_ino)), b"{}")
            with self.assertRaisesRegex(Exception, "physical identity changed"):
                onboarding._read_file(path, 10, expected_binding=dict(device=info.st_dev + 1, inode=info.st_ino))


if __name__ == "__main__":
    unittest.main()
