"""Temporary review storage proofs use only disposable private paths and Git repos."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]


class ReviewStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe-review-storage-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repository"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        (self.repo / "tracked.txt").write_text("unchanged\n")
        (self.repo / "subset").mkdir()
        (self.repo / "subset" / "file.txt").write_text("subset\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.private = self.base / "private-data"
        self.plan_a = self.base / "plan-a"
        self.plan_b = self.base / "plan-b"
        self.plan_a.mkdir()
        self.plan_b.mkdir()
        (self.plan_a / "plan.json").write_text("plan a\n")
        (self.plan_b / "plan.json").write_text("plan b\n")
        self.locator = self.base / "machine" / "machine.json"
        self.locator.parent.mkdir(mode=0o700, parents=True)
        self.api = None
        self.locator_patch = None

    def load_api(self):
        if self.api is None:
            self.api = importlib.import_module("runtime.review_storage")
            self.locator_patch = patch.object(self.api, "_machine_config_path", return_value=self.locator)
            self.locator_patch.start()
            self.addCleanup(self.locator_patch.stop)
        return self.api

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL).decode().strip()

    @staticmethod
    def tree_snapshot(root):
        """Capture disposable tree names, modes, directory times, links and file bytes."""
        root = Path(root)
        if not root.exists():
            return None
        snapshot = []
        for path in sorted(root.rglob("*")):
            info = path.lstat()
            rel = str(path.relative_to(root))
            if path.is_symlink():
                body = os.readlink(path)
            elif path.is_file():
                body = path.read_bytes()
            else:
                body = None
            snapshot.append((rel, info.st_mode, info.st_mtime_ns, body))
        return tuple(snapshot)

    def prepare(self, path=None):
        result = self.load_api().prepare_storage(path or self.private)
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("status"), "ready", result)
        return result

    def resolve(self, target):
        result = self.load_api().resolve_owner(str(target))
        self.assertIsInstance(result, dict)
        return result

    def test_unconfigured_status_and_lookup_are_read_only_and_pending(self):
        before = (self.tree_snapshot(self.base), self.private.exists(), self.locator.exists())
        status = self.load_api().storage_status()
        owner = self.resolve(self.repo)
        self.assertIsInstance(status, dict)
        self.assertFalse(status.get("ok"))
        self.assertEqual(status.get("status"), "storage_pending", status)
        self.assertFalse(owner.get("ok"))
        self.assertEqual(owner.get("status"), "storage_pending", owner)
        self.assertEqual((self.tree_snapshot(self.base), self.private.exists(), self.locator.exists()), before)

    def test_prepare_reopen_is_idempotent_and_different_choice_is_protected(self):
        first = self.prepare()
        self.assertTrue((self.private / "review-evidence" / "runtime.sqlite3").is_file())
        chosen = self.tree_snapshot(self.private)
        locator = self.locator.read_bytes()

        self.api = importlib.reload(self.api)
        self.locator_patch.stop()
        self.locator_patch = patch.object(self.api, "_machine_config_path", return_value=self.locator)
        self.locator_patch.start()
        self.addCleanup(self.locator_patch.stop)
        replay = self.prepare(self.private)
        self.assertEqual(replay.get("private_data_dir"), first.get("private_data_dir"))
        self.assertEqual(self.tree_snapshot(self.private), chosen)
        self.assertEqual(self.locator.read_bytes(), locator)

        other = self.base / "other-private-data"
        protected = self.api.prepare_storage(other)
        self.assertFalse(protected.get("ok"), protected)
        self.assertEqual(protected.get("status"), "protected", protected)
        self.assertEqual(self.tree_snapshot(self.private), chosen)
        self.assertFalse(other.exists())

    def test_new_machine_ancestor_entries_are_fsynced_before_authority_initialization(self):
        api = self.load_api()
        from runtime import state

        fresh = self.base / "fresh-machine-root"
        locator = fresh / ".vibe-workflow" / "machine.json"
        required_parent_syncs = (self.base, fresh, fresh / ".vibe-workflow")
        required_before_initialize = set((*required_parent_syncs, locator.parent))
        seen = []
        before_initialize = []
        real_fsync = os.fsync
        real_initialize = state._initialize

        def record_fsync(descriptor):
            info = os.fstat(descriptor)
            for candidate in (*required_parent_syncs, locator.parent):
                try:
                    current = candidate.stat()
                except FileNotFoundError:
                    continue
                if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
                    seen.append(candidate)
            return real_fsync(descriptor)

        def record_initialize(*args, **kwargs):
            before_initialize.append(set(seen))
            return real_initialize(*args, **kwargs)

        with patch.object(api, "_machine_config_path", return_value=locator), \
             patch.object(api.os, "fsync", side_effect=record_fsync), \
             patch.object(state, "_initialize", side_effect=record_initialize):
            result = api.prepare_storage(self.private)

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(before_initialize), 1, before_initialize)
        self.assertTrue(required_before_initialize.issubset(before_initialize[0]), before_initialize)

    def test_machine_parent_fsync_failure_prevents_authority_initialization(self):
        api = self.load_api()
        from runtime import state

        fresh = self.base / "fsync-failure-machine-root"
        locator = fresh / ".vibe-workflow" / "machine.json"
        base_info = self.base.stat()
        real_fsync = os.fsync
        real_initialize = state._initialize
        initialized = []

        def fail_parent_fsync(descriptor):
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (base_info.st_dev, base_info.st_ino):
                raise OSError("fixture parent fsync failure")
            return real_fsync(descriptor)

        def record_initialize(*args, **kwargs):
            initialized.append(True)
            return real_initialize(*args, **kwargs)

        with patch.object(api, "_machine_config_path", return_value=locator), \
             patch.object(api.os, "fsync", side_effect=fail_parent_fsync), \
             patch.object(state, "_initialize", side_effect=record_initialize):
            result = api.prepare_storage(self.private)

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertFalse(initialized, initialized)
        self.assertFalse((self.private / "review-evidence" / "runtime.sqlite3").exists())

    def test_client_discovery_roots_are_rejected_before_mutation_with_sibling_control(self):
        api = self.load_api()
        for client_path in (Path(".agents/skills"), Path(".codex/skills"), Path(".claude/skills"),
                            Path(".codex/agents"), Path(".claude/agents"), Path(".claude/commands"),
                            Path(".cursor/rules")):
            with self.subTest(client_path=client_path):
                home_name = "home-" + "-".join(part.lstrip(".") for part in client_path.parts)
                home = self.base / home_name
                home.mkdir(mode=0o700)
                locator = home / ".vibe-workflow" / "machine.json"
                selected = home / client_path / "review-data"
                before = self.tree_snapshot(home)
                with patch.object(api, "_machine_config_path", return_value=locator), \
                     patch.object(Path, "home", return_value=home):
                    result = api.prepare_storage(selected)
                self.assertFalse(result.get("ok"), result)
                self.assertEqual(result.get("status"), "protected", result)
                self.assertEqual(self.tree_snapshot(home), before)
                self.assertFalse(locator.exists())
                self.assertFalse(selected.exists())

        home = self.base / "home-valid-sibling"
        home.mkdir(mode=0o700)
        locator = home / ".vibe-workflow" / "machine.json"
        sibling = home / ".local" / "share" / "vibe-workflow"
        with patch.object(api, "_machine_config_path", return_value=locator), \
             patch.object(Path, "home", return_value=home):
            valid = api.prepare_storage(sibling)
        self.assertTrue(valid.get("ok"), valid)
        self.assertEqual(valid.get("status"), "ready", valid)

    def test_cyclic_target_is_protected_through_api_and_workflow_main(self):
        api = self.load_api()
        home = self.base / "cyclic-target-home"
        home.mkdir(mode=0o700)
        locator = home / ".vibe-workflow" / "machine.json"
        target = self.repo / "cycle-a"
        target.symlink_to("cycle-b")
        (self.repo / "cycle-b").symlink_to("cycle-a")
        environment = os.environ.copy()
        environment["HOME"] = str(home)

        with patch.object(api, "_machine_config_path", return_value=locator):
            prepared = api.prepare_storage(self.private)
            self.assertTrue(prepared.get("ok"), prepared)
            before = self.tree_snapshot(self.private)
            direct_error = None
            try:
                direct = api.resolve_owner(target)
            except RuntimeError as error:
                direct_error = str(error)
                direct = None
            command = subprocess.run([sys.executable, "-B", str(PACKAGE / "scripts" / "workflow.py"),
                                      "storage", "owner", "--cwd", str(target)], cwd=PACKAGE, env=environment,
                                     capture_output=True, text=True, timeout=10)

        try:
            cli_report = json.loads(command.stdout)
        except json.JSONDecodeError:
            cli_report = None
        self.assertIsNone(direct_error, direct_error)
        self.assertIsInstance(direct, dict)
        self.assertFalse(direct.get("ok"), direct)
        self.assertEqual(direct.get("status"), "protected", direct)
        self.assertIsInstance(direct.get("reason"), str, direct)
        self.assertLessEqual(len(direct.get("reason", "")), 256, direct)
        self.assertEqual(command.returncode, 1, (command.returncode, command.stderr, command.stdout))
        self.assertEqual(command.stderr, "")
        self.assertEqual(cli_report, direct)
        self.assertEqual(self.tree_snapshot(self.private), before)

    def test_root_subset_and_symlink_alias_replay_same_owner_without_writes(self):
        self.prepare()
        repo_before = self.tree_snapshot(self.repo)
        plans_before = (self.tree_snapshot(self.plan_a), self.tree_snapshot(self.plan_b))

        first = self.resolve(self.repo)
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(first.get("owner", {}).get("kind"), "temporary", first)
        self.assertTrue(first.get("reference"), first)
        self.assertTrue(first.get("created_at"), first)
        after_first = self.tree_snapshot(self.private)

        alias = self.base / "repository-alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        subset = self.repo / "subset"
        replay_subset = self.resolve(subset)
        replay_alias = self.resolve(alias)
        self.assertEqual((replay_subset.get("owner"), replay_subset.get("reference"), replay_subset.get("created_at")),
                         (first.get("owner"), first.get("reference"), first.get("created_at")))
        self.assertEqual((replay_alias.get("owner"), replay_alias.get("reference"), replay_alias.get("created_at")),
                         (first.get("owner"), first.get("reference"), first.get("created_at")))
        self.assertEqual(self.tree_snapshot(self.private), after_first)
        self.assertEqual(self.tree_snapshot(self.repo), repo_before)
        self.assertEqual((self.tree_snapshot(self.plan_a), self.tree_snapshot(self.plan_b)), plans_before)

    def test_replacing_subset_file_reuses_original_owner_without_writes(self):
        self.prepare()
        first = self.resolve(self.repo)
        self.assertTrue(first.get("ok"), first)
        file = self.repo / "subset" / "file.txt"
        replacement = file.with_suffix(".new")
        replacement.write_text("updated content\n")
        replacement.replace(file)
        before = self.tree_snapshot(self.private)

        replay = self.resolve(file)
        self.assertEqual((replay.get("owner"), replay.get("reference"), replay.get("created_at")),
                         (first.get("owner"), first.get("reference"), first.get("created_at")))
        self.assertEqual(self.tree_snapshot(self.private), before)

    def test_repository_moved_and_replaced_at_old_path_is_protected(self):
        self.prepare()
        first = self.resolve(self.repo)
        self.assertTrue(first.get("ok"), first)
        moved = self.base / "moved-original-repository"
        self.repo.rename(moved)
        before = self.tree_snapshot(self.private)
        moved_result = self.resolve(moved)
        self.assertFalse(moved_result.get("ok"), moved_result)
        self.assertEqual(moved_result.get("status"), "protected", moved_result)
        self.assertEqual(self.tree_snapshot(self.private), before)

        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "replacement@example.invalid")
        self.git("config", "user.name", "Replacement")
        (self.repo / "replacement.txt").write_text("different repository\n")
        self.git("add", ".")
        self.git("commit", "-qm", "replacement")

        result = self.resolve(self.repo)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(self.tree_snapshot(self.private), before)

    def test_concurrent_first_resolution_creates_one_owner(self):
        self.prepare()
        targets = [self.repo, self.repo / "subset", self.repo]
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(self.resolve, targets, timeout=15))
        self.assertTrue(all(result.get("ok") for result in results), results)
        identities = {(result.get("owner", {}).get("id"),
                       json.dumps(result.get("reference"), sort_keys=True, separators=(",", ":")),
                       result.get("created_at"))
                      for result in results}
        self.assertEqual(len(identities), 1, results)

    def test_separate_clone_does_not_reuse_original_repository_owner(self):
        self.prepare()
        original = self.resolve(self.repo)
        clone = self.base / "separate-clone"
        subprocess.check_call(["git", "clone", "-q", str(self.repo), str(clone)], stderr=subprocess.DEVNULL)
        copied = self.resolve(clone)
        self.assertTrue(copied.get("ok"), copied)
        self.assertEqual(copied.get("owner", {}).get("kind"), "temporary", copied)
        self.assertNotEqual(copied.get("owner", {}).get("id"), original.get("owner", {}).get("id"), copied)
        self.assertNotEqual(copied.get("reference"), original.get("reference"), copied)

    def test_nested_repository_overlap_is_protected_without_allocation(self):
        self.prepare()
        parent = self.resolve(self.repo)
        self.assertTrue(parent.get("ok"), parent)
        before = self.tree_snapshot(self.private)
        nested = self.repo / "subset" / "nested-repository"
        nested.mkdir()
        subprocess.check_call(["git", "init", "-q", str(nested)], stderr=subprocess.DEVNULL)
        nested_result = self.resolve(nested)
        self.assertFalse(nested_result.get("ok"), nested_result)
        self.assertEqual(nested_result.get("status"), "protected", nested_result)
        self.assertEqual(self.tree_snapshot(self.private), before)

    def test_linked_worktree_does_not_receive_temporary_owner(self):
        self.prepare()
        linked = self.base / "linked-worktree"
        self.git("worktree", "add", "--detach", str(linked))
        before = self.tree_snapshot(self.private)
        result = self.resolve(linked)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(self.tree_snapshot(self.private), before)

    def test_deleted_authority_database_is_protected_without_recreation(self):
        self.prepare()
        database = self.private / "review-evidence" / "runtime.sqlite3"
        original = database.read_bytes()
        database.unlink()
        missing = self.resolve(self.repo)
        self.assertFalse(missing.get("ok"), missing)
        self.assertFalse(database.exists())
        self.assertEqual(missing.get("status"), "protected", missing)

    def test_replaced_authority_database_is_protected(self):
        self.prepare()
        database = self.private / "review-evidence" / "runtime.sqlite3"
        original = database.read_bytes()
        replacement = database.with_suffix(".replacement")
        replacement.write_bytes(original)
        replacement.replace(database)
        protected = self.resolve(self.repo)
        self.assertFalse(protected.get("ok"), protected)
        self.assertEqual(protected.get("status"), "protected", protected)
        self.assertEqual(database.read_bytes(), original)

    def test_replaced_selected_storage_parent_is_protected(self):
        import shutil
        self.prepare()
        first = self.resolve(self.repo)
        self.assertTrue(first.get("ok"), first)
        selected_parent = self.private / "review-evidence"
        displaced_parent = self.base / "displaced-review-evidence"
        selected_parent.rename(displaced_parent)
        shutil.copytree(displaced_parent, selected_parent)
        before = self.tree_snapshot(self.private)

        result = self.resolve(self.repo)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(self.tree_snapshot(self.private), before)

    def test_preexisting_unowned_schema_three_authority_is_not_adopted(self):
        from runtime import evidence, state
        self.private.mkdir(mode=0o700)
        directory = self.private / "review-evidence"
        directory.mkdir(mode=0o700)
        creation = state._initialize(directory / "runtime.sqlite3", version=3,
                                     expected_directory=evidence._binding(directory))
        os.close(creation.descriptor)
        before = self.tree_snapshot(self.private)

        result = self.load_api().prepare_storage(self.private)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(self.tree_snapshot(self.private), before)
        status = self.api.storage_status()
        self.assertFalse(status.get("ok"), status)
        self.assertNotEqual(status.get("status"), "ready", status)

    def test_owner_replay_survives_a_fresh_process_reopen(self):
        api = self.load_api()
        home = self.base / "fresh-process-home"
        home.mkdir(mode=0o700)
        locator = home / ".vibe-workflow" / "machine.json"
        environment = os.environ.copy()
        environment["HOME"] = str(home)
        script = "from runtime import review_storage; import json,sys; print(json.dumps(review_storage.resolve_owner(sys.argv[1]), sort_keys=True))"

        with patch.object(api, "_machine_config_path", return_value=locator):
            self.assertTrue(api.prepare_storage(self.private).get("ok"))
            first = api.resolve_owner(self.repo)
            self.assertTrue(first.get("ok"), first)
            before = self.tree_snapshot(self.private)
            process = subprocess.run([sys.executable, "-B", "-c", script, str(self.repo)], cwd=PACKAGE,
                                     env=environment, capture_output=True, text=True, timeout=10)

        self.assertEqual(process.returncode, 0, (process.stderr, process.stdout))
        self.assertEqual(process.stderr, "")
        replay = json.loads(process.stdout)
        self.assertEqual(replay, first)
        self.assertEqual(self.tree_snapshot(self.private), before)

    def test_interrupted_initialization_is_preserved_without_automatic_retry(self):
        self.load_api()
        from runtime import state
        with patch.object(state, "_initialize", side_effect=OSError("fixture interruption")):
            first = self.api.prepare_storage(self.private)
        self.assertFalse(first.get("ok"), first)
        self.assertEqual(first.get("status"), "protected", first)
        after_interruption = self.tree_snapshot(self.base)
        replay = self.api.prepare_storage(self.private)
        self.assertFalse(replay.get("ok"), replay)
        self.assertEqual(replay.get("status"), "protected", replay)
        self.assertEqual(self.tree_snapshot(self.base), after_interruption)

    def test_entire_selected_directory_deletion_stays_pending_or_protected(self):
        import shutil
        self.prepare()
        chosen = self.tree_snapshot(self.private)
        self.assertIsNotNone(chosen)
        shutil.rmtree(self.private)
        result = self.resolve(self.repo)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertFalse(self.private.exists())


class EnrolledReviewStorageTests(unittest.TestCase):
    def setUp(self):
        from tests.test_evidence import CanonicalEvidenceTests
        self.fixture = CanonicalEvidenceTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.base = self.fixture.fixture.root
        self.api = importlib.import_module("runtime.review_storage")
        self.locator = self.api._machine_config_path()

    def assert_project_accounting(self, result, *, history_complete=True, history_reason="authenticated_retained_history"):
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("status"), "resolved", result)
        self.assertEqual(result.get("owner"), dict(kind="project", project_id=self.fixture.scope.project_id, task=None), result)
        self.assertEqual(result.get("age_basis"), "authority_provenance", result)
        self.assertTrue(result.get("reference"), result)
        self.assertTrue(result.get("created_at"), result)
        self.assertIsInstance(result.get("authority"), dict)
        self.assertIsInstance(result.get("checkout"), dict)
        history = result.get("temporary_history")
        self.assertIsInstance(history, dict, result)
        self.assertIs(history.get("complete"), history_complete, result)
        self.assertEqual(history.get("reason"), history_reason, result)

    def test_enrolled_accounting_without_original_machine_configuration_is_protected(self):
        project_storage = self.fixture.scope.runtime.state_dir
        repo = self.fixture.fixture.repo
        project_record = repo / ".vibe-workflow" / "project.json"
        missing = self.base / "missing-machine" / "machine.json"
        before = (ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes())
        with patch.object(self.api, "_machine_config_path", return_value=missing):
            owner = self.api.resolve_owner(str(repo))
        self.assertFalse(owner["ok"], owner)
        self.assertEqual(owner["status"], "protected")
        self.assertFalse(missing.exists())
        home = self.base / "no-machine-cli-home"
        home.mkdir(mode=0o700)
        command = subprocess.run([sys.executable, "-B", str(PACKAGE / "scripts" / "workflow.py"),
            "storage", "owner", "--cwd", str(repo)], cwd=PACKAGE,
            env=os.environ | {"HOME": str(home)}, capture_output=True, text=True, timeout=10)
        self.assertEqual(command.returncode, 1, command.stderr)
        self.assertEqual(json.loads(command.stdout)["status"], "protected")
        self.assertEqual((ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes()), before)

    def test_linked_checkout_uses_same_project_accounting_and_its_own_observation(self):
        repo = self.fixture.fixture.repo
        linked = self.base / "linked-worktree"
        subprocess.check_call(["git", "-C", str(repo), "worktree", "add", "--detach", str(linked)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        project_storage = self.fixture.scope.runtime.state_dir
        project_record = repo / ".vibe-workflow" / "project.json"
        before = (ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(self.locator.parent))

        primary = self.api.resolve_owner(str(repo))
        linked_owner = self.api.resolve_owner(str(linked))

        self.assert_project_accounting(primary)
        self.assert_project_accounting(linked_owner)
        self.assertEqual((primary["owner"], primary["reference"], primary["created_at"]),
            (linked_owner["owner"], linked_owner["reference"], linked_owner["created_at"]))
        self.assertNotEqual(primary["checkout"], linked_owner["checkout"])
        self.assertEqual((ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(self.locator.parent)), before)

    def test_registered_linked_checkout_with_mismatched_binding_is_protected(self):
        import copy
        from runtime import onboarding

        repo = self.fixture.fixture.repo
        linked = self.base / "registered-linked-worktree"
        subprocess.check_call(["git", "-C", str(repo), "worktree", "add", "--detach", str(linked)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        observed, error = onboarding._checkout_observation(str(linked), {"paths": 0, "bytes": 0})
        self.assertIsNone(error)
        project_record = repo / ".vibe-workflow" / "project.json"
        value = json.loads(project_record.read_text())
        original = value["repositories"][0]["checkouts"][0]
        selected = dict(checkout_id="registered-linked", root=observed["root"], git_dir=observed["git_dir"],
            binding={key: observed["binding"][key] for key in ("root", "git_dir")},
            clients=copy.deepcopy(original["clients"]))
        selected["binding"]["root"]["inode"] += 1
        value["repositories"][0]["checkouts"].append(selected)
        project_record.write_text(json.dumps(value))
        before = (ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(repo), ReviewStorageTests.tree_snapshot(linked))

        result = self.api.resolve_owner(str(linked))

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual((ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(repo), ReviewStorageTests.tree_snapshot(linked)), before)

    def test_suspended_activation_keeps_project_accounting_without_writes(self):
        project_storage = self.fixture.scope.runtime.state_dir
        repo = self.fixture.fixture.repo
        project_record = repo / ".vibe-workflow" / "project.json"
        original = self.api.resolve_owner(str(repo))
        self.assert_project_accounting(original)
        value = json.loads(project_record.read_text())
        activation = value["repositories"][0]["checkouts"][0]["clients"][0]["activation"]
        activation["status"] = "suspended"
        project_record.write_text(json.dumps(value))
        before = (ReviewStorageTests.tree_snapshot(project_storage),
            ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(repo))

        result = self.api.resolve_owner(str(repo))

        self.assert_project_accounting(result)
        self.assertEqual((result["owner"], result["reference"], result["created_at"]),
            (original["owner"], original["reference"], original["created_at"]))
        self.assertEqual((ReviewStorageTests.tree_snapshot(project_storage),
            ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(repo)), before)

    def test_parent_accounting_protects_malformed_selected_subdirectory_authority(self):
        repo = self.fixture.fixture.repo
        subset = repo / "accounting-subset"
        subset.mkdir()
        selected_file = subset / "item.txt"
        selected_file.write_text("fixture target\n")
        project_storage = self.fixture.scope.runtime.state_dir
        project_record = repo / ".vibe-workflow" / "project.json"
        before_marker = (ReviewStorageTests.tree_snapshot(project_storage),
            ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(repo))

        parent = self.api.resolve_owner(str(repo))
        child = self.api.resolve_owner(str(subset))
        file_owner = self.api.resolve_owner(str(selected_file))

        self.assert_project_accounting(parent)
        self.assert_project_accounting(child)
        self.assert_project_accounting(file_owner)
        self.assertEqual((parent["owner"], parent["reference"], parent["created_at"]),
            (child["owner"], child["reference"], child["created_at"]))
        self.assertEqual((parent["owner"], parent["reference"], parent["created_at"]),
            (file_owner["owner"], file_owner["reference"], file_owner["created_at"]))
        self.assertEqual((ReviewStorageTests.tree_snapshot(project_storage),
            ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(repo)), before_marker)

        marker = subset / ".vibe-workflow"
        marker.mkdir()
        (marker / "project.json").write_text("{malformed authority\n")
        before_lookup = (ReviewStorageTests.tree_snapshot(project_storage),
            ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(repo))
        for target in (subset, selected_file):
            with self.subTest(target=target):
                result = self.api.resolve_owner(str(target))
                self.assertFalse(result.get("ok"), result)
                self.assertEqual(result.get("status"), "protected", result)
                self.assertEqual((ReviewStorageTests.tree_snapshot(project_storage),
                    ReviewStorageTests.tree_snapshot(self.locator.parent), project_record.read_bytes(),
                    ReviewStorageTests.tree_snapshot(repo)), before_lookup)

    def test_original_machine_history_reports_complete_with_retained_owner(self):
        temporary_storage = self.locator.parent.parent / "private"
        self.assertTrue(self.api.prepare_storage(temporary_storage).get("ok"))
        before = (ReviewStorageTests.tree_snapshot(temporary_storage),
            ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            (self.fixture.fixture.repo / ".vibe-workflow" / "project.json").read_bytes())

        owner = self.api.resolve_owner(str(self.fixture.fixture.repo))

        self.assert_project_accounting(owner, history_complete=True,
            history_reason="authenticated_retained_history")
        self.assertEqual((ReviewStorageTests.tree_snapshot(temporary_storage),
            ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            (self.fixture.fixture.repo / ".vibe-workflow" / "project.json").read_bytes()), before)

    def test_encountered_but_excluded_authority_is_protected_without_fallback(self):
        project_storage = self.fixture.scope.runtime.state_dir
        project_record = self.fixture.fixture.repo / ".vibe-workflow" / "project.json"
        value = json.loads(project_record.read_text())
        value["membership"]["exclude"] = ["."]
        project_record.write_text(json.dumps(value))
        before = (ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(self.locator.parent))

        result = self.api.resolve_owner(str(self.fixture.fixture.repo))

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual((ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(self.locator.parent)), before)

    def test_non_git_project_with_an_enrolled_child_repository_is_not_absence(self):
        from tests.test_startup import CanonicalAcquisitionTests

        case = CanonicalAcquisitionTests(methodName="runTest")
        case.setUp()
        self.addCleanup(case.doCleanups)
        value, project_record = case.record(project=case.base)
        self.assertEqual(value["kind"], "umbrella")
        self.assertNotEqual(case.base, Path(value["repositories"][0]["primary_root"]))
        project_state = Path(value["repositories"][0]["checkouts"][0]["clients"][0]
            ["configuration"]["runtime"]["state_dir"])
        before = (ReviewStorageTests.tree_snapshot(case.base),
            ReviewStorageTests.tree_snapshot(self.locator.parent))
        self.assertFalse(project_state.exists())

        accounting = self.api.acquire_enrolled_accounting(str(case.base))
        owner = self.api.resolve_owner(str(case.base))
        startup = case.acquire(cwd=case.base)

        self.assertFalse(accounting.get("ok"), accounting)
        self.assertEqual(accounting.get("status"), "protected", accounting)
        self.assertFalse(owner.get("ok"), owner)
        self.assertEqual(owner.get("status"), "protected", owner)
        self.assertEqual(startup.resolution.status, "outside")
        self.assertEqual(startup.resolution.reason, "not_enrolled")
        self.assertTrue(project_record.is_file())
        self.assertFalse(project_state.exists())
        self.assertEqual((ReviewStorageTests.tree_snapshot(case.base),
            ReviewStorageTests.tree_snapshot(self.locator.parent)), before)

    def test_missing_runtime_configuration_is_protected(self):
        project_storage = self.fixture.scope.runtime.state_dir
        project_record = self.fixture.fixture.repo / ".vibe-workflow" / "project.json"
        value = json.loads(project_record.read_text())
        value["repositories"][0]["checkouts"][0]["clients"][0]["configuration"]["runtime"] = None
        project_record.write_text(json.dumps(value))
        before = (ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(self.locator.parent))

        result = self.api.resolve_owner(str(self.fixture.fixture.repo))

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual((ReviewStorageTests.tree_snapshot(project_storage), project_record.read_bytes(),
            ReviewStorageTests.tree_snapshot(self.locator.parent)), before)

    def test_missing_selected_project_database_is_protected_without_recreation(self):
        database = self.fixture.scope.runtime.state_dir / "runtime.sqlite3"
        project_record = self.fixture.fixture.repo / ".vibe-workflow" / "project.json"
        project_before = project_record.read_bytes()
        database.unlink()
        after_delete = (ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            project_record.read_bytes(), ReviewStorageTests.tree_snapshot(self.locator.parent))

        result = self.api.resolve_owner(str(self.fixture.fixture.repo))

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertFalse(database.exists())
        self.assertEqual((ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            project_record.read_bytes(), ReviewStorageTests.tree_snapshot(self.locator.parent)), after_delete)
        self.assertEqual(project_record.read_bytes(), project_before)

    def test_existing_temporary_history_before_d4_enrollment_is_retained(self):
        from tests.test_enrolled_accounting_integration import EnrolledUmbrellaAccountingTests

        enrolled = EnrolledUmbrellaAccountingTests(methodName="runTest")
        self.addCleanup(enrolled.doCleanups)
        prior = []

        def retain_before_birth(fixture, record):
            owner = self.api.resolve_owner(record["repositories"][0]["primary_root"])
            self.assertTrue(owner["ok"], owner)
            self.assertEqual(owner["owner"]["kind"], "temporary")
            prior.append({key: owner[key] for key in ("owner", "reference", "created_at")})

        enrolled.setUp(before_publish=retain_before_birth)
        before = enrolled.snapshot()
        result = self.api.resolve_owner(enrolled.members[0])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["temporary_history"]["reason"], "authenticated_retained_history")
        self.assertEqual(result["temporary_history"]["owners"], prior)
        self.assertEqual(enrolled.snapshot(), before)

    def test_wrong_project_baseline_from_another_genuine_project_is_protected(self):
        from tests.test_evidence import CanonicalEvidenceTests
        from runtime import evidence, state

        other = CanonicalEvidenceTests(methodName="runTest")
        other.setUp()
        self.addCleanup(other.doCleanups)
        self.assertNotEqual(other.scope.project_id, self.fixture.scope.project_id)
        other_context = evidence._evidence_context(other.scope)
        with evidence._context_database(other_context) as db:
            other_baseline = state._source_baseline(None, db, context=other_context)
        self.assertEqual(other_baseline["content"]["project_id"], other.scope.project_id)
        self.assertNotEqual(other_baseline["content"]["project_id"], self.fixture.scope.project_id)

        project_record = self.fixture.fixture.repo / ".vibe-workflow" / "project.json"
        before = (ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            ReviewStorageTests.tree_snapshot(other.scope.runtime.state_dir), project_record.read_bytes())
        with patch.object(state, "_source_baseline", return_value=other_baseline):
            result = self.api.resolve_owner(str(self.fixture.fixture.repo))

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual((ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            ReviewStorageTests.tree_snapshot(other.scope.runtime.state_dir), project_record.read_bytes()), before)

    def test_committed_baseline_removal_between_reads_is_seen_by_final_transaction(self):
        from contextlib import closing, contextmanager
        import sqlite3
        from runtime import evidence, state

        project_database = self.fixture.scope.runtime.state_dir / "runtime.sqlite3"
        real_context_database = evidence._context_database
        project_transactions = []
        post_mutation_snapshot = []

        @contextmanager
        def mutate_after_first_read(context, **kwargs):
            with real_context_database(context, **kwargs) as db:
                is_project_store = context.database == project_database
                if is_project_store:
                    project_transactions.append(db)
                yield db
            if is_project_store and len(project_transactions) == 1:
                authority = state._canonical_plain(context.authority_set[0])
                baseline_name = state._source_baseline_name(authority)
                with closing(sqlite3.connect(project_database)) as external:
                    external.execute("delete from evidence_metadata where name=?", (baseline_name,))
                    external.commit()
                post_mutation_snapshot.append(ReviewStorageTests.tree_snapshot(context.directory))

        with patch.object(evidence, "_context_database", side_effect=mutate_after_first_read):
            result = self.api.resolve_owner(str(self.fixture.fixture.repo))

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(len(project_transactions), 2, project_transactions)
        self.assertIsNot(project_transactions[0], project_transactions[1])
        self.assertEqual(len(post_mutation_snapshot), 1)
        self.assertEqual(ReviewStorageTests.tree_snapshot(self.fixture.scope.runtime.state_dir),
            post_mutation_snapshot[0])

    def test_genuinely_enrolled_repository_keeps_project_authority(self):
        temporary_storage = self.locator.parent.parent / "private"
        result = self.api.prepare_storage(temporary_storage)
        self.assertTrue(result.get("ok"), result)
        self.fixture.fixture.checked()
        project_storage = self.fixture.scope.runtime.state_dir
        project_record = self.fixture.fixture.repo / ".vibe-workflow" / "project.json"
        self.assertTrue(project_storage.is_dir(), project_storage)
        self.assertTrue(project_record.is_file(), project_record)
        before_temporary_storage = ReviewStorageTests.tree_snapshot(temporary_storage)
        before_project_storage = ReviewStorageTests.tree_snapshot(project_storage)
        self.assertTrue(before_temporary_storage, temporary_storage)
        self.assertTrue(before_project_storage, project_storage)
        before_project_record = project_record.read_bytes()
        owner = self.api.resolve_owner(str(self.fixture.fixture.repo))
        self.assert_project_accounting(owner, history_complete=True,
            history_reason="authenticated_retained_history")
        self.assertEqual(ReviewStorageTests.tree_snapshot(temporary_storage), before_temporary_storage)
        self.assertEqual(ReviewStorageTests.tree_snapshot(project_storage), before_project_storage)
        self.assertEqual(project_record.read_bytes(), before_project_record)
