"""Scope-aware temporary Project ownership proofs use disposable repositories."""

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_review_storage as review_storage_tests


class ProjectOwnerStorageTests(unittest.TestCase):
    """Reuse the existing private-storage fixture without inheriting its tests."""

    def setUp(self):
        self.storage_fixture = review_storage_tests.ReviewStorageTests(methodName="runTest")
        self.storage_fixture.setUp()
        self.addCleanup(self.storage_fixture.doCleanups)
        self.base = self.storage_fixture.base
        self.api = self.storage_fixture.load_api()
        self.container = self.base / "project-container"
        self.container.mkdir()
        self.project = self.container / "umbrella"
        self.project.mkdir()
        self.first = self.make_repo(self.project / "first")
        self.second = self.make_repo(self.project / "second")
        (self.first / "notes.md").write_text("named Project context\n")
        self.scope = self.project_scope()

    def make_repo(self, root):
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
        (root / "tracked.txt").write_text("fixture\n")
        subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
                       check=True, capture_output=True)
        return root

    def project_scope(self):
        return {
            "kind": "umbrella",
            "project_root": str(self.project),
            "member_checkouts": [str(self.first), str(self.second)],
            "membership": {"include": ["first", "second"], "exclude": [],
                           "future_join": False, "authorization": "fixture:scope"},
            "authority": {"path": str(self.project / ".vibe-workflow" / "project.json"),
                          "sha256": "absent", "reference": "fixture:plan-a"},
            "project_files": [],
        }

    @staticmethod
    def standalone_scope(root, *, exclude=()):
        root = Path(root)
        return {
            "kind": "standalone",
            "project_root": str(root),
            "member_checkouts": [str(root)],
            "membership": {"include": ["."], "exclude": list(exclude),
                           "future_join": False, "authorization": "fixture:standalone"},
            "authority": {"path": str(root / ".vibe-workflow" / "project.json"),
                          "sha256": "absent", "reference": "fixture:standalone"},
            "project_files": [],
        }

    def project_owner(self, scope=None):
        return self.api.resolve_project_owner(copy.deepcopy(scope or self.scope))

    def snapshot_targets(self):
        snapshot = self.storage_fixture.tree_snapshot
        return tuple(snapshot(path) for path in (self.project, self.first, self.second))

    def prepare(self):
        self.assertTrue(self.storage_fixture.prepare().get("ok"))

    def metadata_owner_count(self):
        from runtime import evidence

        with self.api._machine_guard() as (path, descriptor, parents):
            config = self.api._configuration(path, descriptor)
            context = self.api._ready_context(config, parents)
            with evidence._context_database(context) as db:
                return db.execute("select count(*) from evidence_metadata").fetchone()[0]

    def replace_root_before_helper(self, scope, root, helper_name):
        original = getattr(self.api, helper_name)
        saved = self.base / (root.name + "-original")

        def replace_then_acquire(*args, **kwargs):
            root.rename(saved)
            shutil.copytree(saved, root, symlinks=True)
            return original(*args, **kwargs)

        try:
            with patch.object(self.api, helper_name, side_effect=replace_then_acquire):
                return self.project_owner(scope)
        finally:
            if saved.exists():
                if root.exists():
                    shutil.rmtree(root)
                saved.rename(root)

    def test_real_producer_cli_reopen_persists_one_root_member_anchor_without_target_writes(self):
        self.prepare()
        targets_before = self.snapshot_targets()
        plans_before = (self.storage_fixture.tree_snapshot(self.storage_fixture.plan_a),
                        self.storage_fixture.tree_snapshot(self.storage_fixture.plan_b))

        first = self.project_owner()
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(first.get("status"), "resolved", first)
        self.assertEqual(first.get("owner", {}).get("kind"), "temporary", first)
        self.assertTrue(first.get("reference"), first)
        self.assertTrue(first.get("created_at"), first)
        private_after_create = self.storage_fixture.tree_snapshot(self.storage_fixture.private)

        locator = self.storage_fixture.locator
        script = (
            "import runpy,sys; from unittest.mock import patch; "
            "from pathlib import Path; from runtime import review_storage; "
            "workflow=sys.argv[1]; locator=sys.argv[2]; args=sys.argv[3:]; "
            "sys.argv=[workflow, *args]; "
            "p=patch.object(review_storage, '_machine_config_path', return_value=Path(locator)); "
            "p.start(); runpy.run_path(workflow, run_name='__main__')"
        )
        command = subprocess.run(
            [sys.executable, "-B", "-c", script,
             str(review_storage_tests.PACKAGE / "scripts" / "workflow.py"),
             str(locator), "storage", "project-owner"],
            cwd=review_storage_tests.PACKAGE,
            input=json.dumps({"project_scope": self.scope}), capture_output=True, text=True, timeout=15)
        self.assertEqual(command.returncode, 0, (command.stderr, command.stdout))
        self.assertEqual(command.stderr, "")
        self.assertEqual(json.loads(command.stdout), first)
        self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), private_after_create)

        with self.api._machine_guard() as (path, descriptor, parents):
            config = self.api._configuration(path, descriptor)
            context = self.api._ready_context(config, parents)
            from runtime import evidence
            from runtime import state
            with evidence._context_database(context) as db:
                metadata = state._metadata(db, first["reference"]["name"])
                content = evidence._read(None, first["reference"], context=context, db=db)
                owner_rows = self.api._owners(config, context, db)
        self.assertEqual(len(owner_rows), 1, owner_rows)
        self.assertEqual(metadata["owner"], first["owner"])
        self.assertEqual(metadata["created_at"], first["created_at"])
        self.assertEqual({target["path"] for target in metadata["targets"]},
                         {str(self.project), str(self.first), str(self.second)})
        self.assertEqual(content["kind"], "temporary-project-owner")
        self.assertEqual(content["owner"], first["owner"])
        self.assertIsNone(next(target for target in metadata["targets"]
                               if target["path"] == str(self.project))["git"])

        self.assertEqual(self.snapshot_targets(), targets_before)
        self.assertEqual((self.storage_fixture.tree_snapshot(self.storage_fixture.plan_a),
                          self.storage_fixture.tree_snapshot(self.storage_fixture.plan_b)), plans_before)

    def test_temporary_standalone_project_scope_creates_and_reuses_owner(self):
        self.prepare()
        scope = self.standalone_scope(self.first)
        target_before = self.storage_fixture.tree_snapshot(self.first)
        owners_before = self.metadata_owner_count()

        first = self.project_owner(scope)
        replay_scope = copy.deepcopy(scope)
        replay_scope["authority"]["reference"] = "fixture:another-standalone-plan"
        replay = self.project_owner(replay_scope)

        self.assertTrue(first.get("ok"), first)
        self.assertEqual(first.get("status"), "resolved", first)
        self.assertEqual(first.get("owner", {}).get("kind"), "temporary", first)
        self.assertTrue(replay.get("ok"), replay)
        self.assertEqual((replay.get("owner"), replay.get("reference"), replay.get("created_at")),
                         (first.get("owner"), first.get("reference"), first.get("created_at")))
        self.assertEqual(self.metadata_owner_count(), owners_before + 1)
        self.assertEqual(self.storage_fixture.tree_snapshot(self.first), target_before)

    def test_valid_membership_expansion_protects_and_preserves_existing_umbrella_anchor(self):
        self.prepare()
        scope = copy.deepcopy(self.scope)
        scope["membership"]["include"] = ["."]
        first = self.project_owner(scope)
        self.assertTrue(first.get("ok"), first)
        private_before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
        owner_count = self.metadata_owner_count()

        third = self.make_repo(self.project / "third")
        expanded = copy.deepcopy(scope)
        expanded["member_checkouts"].append(str(third))
        result = self.project_owner(expanded)

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(result.get("temporary_history", {}).get("owners"), [
            {"owner": first["owner"], "reference": first["reference"],
             "created_at": first["created_at"]}])
        self.assertEqual(self.metadata_owner_count(), owner_count)
        self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), private_before)

    def test_replaced_initial_umbrella_or_standalone_root_cannot_publish_owner(self):
        self.prepare()
        standalone = self.make_repo(self.base / "standalone-project")
        cases = (
            ("umbrella", copy.deepcopy(self.scope), self.project, "_temporary_project_result"),
            ("standalone", self.standalone_scope(standalone), standalone, "_temporary_target_result"),
        )
        for label, scope, root, helper_name in cases:
            with self.subTest(scope=label):
                owner_count = self.metadata_owner_count()
                result = self.replace_root_before_helper(scope, root, helper_name)
                self.assertFalse(result.get("ok"), result)
                self.assertEqual(result.get("status"), "protected", result)
                self.assertEqual(self.metadata_owner_count(), owner_count)

    def test_excluded_boundary_symlink_after_register_rolls_back_temporary_standalone_owner(self):
        self.prepare()
        root = self.make_repo(self.base / "guarded-standalone")
        scope = self.standalone_scope(root, exclude=("excluded-boundary",))
        boundary = root / "excluded-boundary"
        root_and_git_identity = (root.stat().st_dev, root.stat().st_ino,
                                 (root / ".git").stat().st_dev, (root / ".git").stat().st_ino)
        metadata_before = self.metadata_owner_count()
        private_before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
        register = self.api._register

        def create_excluded_escape_after_publication(*args, **kwargs):
            reference = register(*args, **kwargs)
            boundary.symlink_to(self.second, target_is_directory=True)
            self.assertEqual((root.stat().st_dev, root.stat().st_ino,
                              (root / ".git").stat().st_dev, (root / ".git").stat().st_ino),
                             root_and_git_identity)
            return reference

        try:
            with patch.object(self.api, "_register", side_effect=create_excluded_escape_after_publication):
                result = self.project_owner(scope)
        finally:
            if boundary.is_symlink():
                boundary.unlink()

        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(self.metadata_owner_count(), metadata_before)
        after_orphan = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
        self.assertNotEqual(after_orphan, private_before)
        replay = self.project_owner(scope)
        self.assertFalse(replay.get("ok"), replay)
        self.assertEqual(replay.get("status"), "protected", replay)
        self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), after_orphan)

    def test_repeat_reorder_subset_and_plan_context_reuse_original_owner_and_age(self):
        self.prepare()
        first = self.project_owner()
        self.assertTrue(first.get("ok"), first)
        evidence_before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)

        reordered = copy.deepcopy(self.scope)
        reordered["member_checkouts"].reverse()
        reordered["authority"]["reference"] = "fixture:plan-b"
        reordered["project_files"] = ["first/notes.md"]
        subset = copy.deepcopy(reordered)
        subset["member_checkouts"] = [str(self.second)]
        results = (self.project_owner(reordered), self.project_owner(subset))
        for result in results:
            self.assertTrue(result.get("ok"), result)
            self.assertEqual((result.get("owner"), result.get("reference"), result.get("created_at")),
                             (first.get("owner"), first.get("reference"), first.get("created_at")))
        self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), evidence_before)

    def test_member_file_subdirectory_and_benign_alias_reuse_umbrella_anchor(self):
        self.prepare()
        first = self.project_owner()
        self.assertTrue(first.get("ok"), first)
        before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
        selected_file = self.first / "tracked.txt"
        subdirectory = self.first / "nested"
        subdirectory.mkdir()
        alias = self.base / "first-alias"
        alias.symlink_to(self.first, target_is_directory=True)

        for target in (selected_file, subdirectory, alias):
            with self.subTest(target=target):
                result = self.api.resolve_owner(str(target))
                self.assertTrue(result.get("ok"), result)
                self.assertEqual((result.get("owner"), result.get("reference"), result.get("created_at")),
                                 (first.get("owner"), first.get("reference"), first.get("created_at")))
        self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), before)

    def test_old_one_or_two_member_anchors_protect_exact_history_without_new_owner(self):
        self.prepare()
        for prior_members in ([self.first], [self.first, self.second]):
            with self.subTest(prior_members=prior_members):
                if prior_members[0] == self.first:
                    anchors = [self.api.resolve_owner(str(member)) for member in prior_members]
                    self.assertTrue(all(anchor.get("ok") for anchor in anchors), anchors)
                before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
                protected = self.project_owner()
                self.assertFalse(protected.get("ok"), protected)
                self.assertEqual(protected.get("status"), "protected", protected)
                history = protected.get("temporary_history")
                self.assertIsInstance(history, dict, protected)
                self.assertIs(history.get("complete"), True, protected)
                self.assertEqual(history.get("owners"), [
                    {"owner": anchor["owner"], "reference": anchor["reference"],
                     "created_at": anchor["created_at"]} for anchor in anchors])
                self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), before)

    def test_changed_membership_expansion_exclusion_and_outside_member_protect_without_allocation(self):
        self.prepare()
        cases = []
        changed = copy.deepcopy(self.scope)
        changed["membership"]["include"] = ["first"]
        cases.append(("changed membership", changed))

        extra = self.make_repo(self.project / "extra")
        expanded = copy.deepcopy(self.scope)
        expanded["member_checkouts"].append(str(extra))
        cases.append(("expanded selection", expanded))

        excluded = copy.deepcopy(self.scope)
        excluded["membership"]["exclude"] = ["second"]
        cases.append(("excluded member", excluded))

        outside = self.make_repo(self.base / "outside")
        crossing = copy.deepcopy(self.scope)
        crossing["member_checkouts"][-1] = str(outside)
        cases.append(("outside member", crossing))

        for label, scope in cases:
            with self.subTest(case=label):
                before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
                result = self.project_owner(scope)
                self.assertFalse(result.get("ok"), result)
                self.assertEqual(result.get("status"), "protected", result)
                self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), before)

    def test_changed_membership_expansion_and_crossing_root_cannot_reuse_or_mint_owner(self):
        self.prepare()
        first = self.project_owner()
        self.assertTrue(first.get("ok"), first)
        before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)

        changed = copy.deepcopy(self.scope)
        changed["membership"]["authorization"] = "fixture:changed"
        expanded_member = self.make_repo(self.project / "later-member")
        expanded = copy.deepcopy(self.scope)
        expanded["member_checkouts"].append(str(expanded_member))
        crossing_root = copy.deepcopy(self.scope)
        crossing_root["project_root"] = str(self.container)
        crossing_root["authority"]["path"] = str(self.container / ".vibe-workflow" / "project.json")
        crossing_root["member_checkouts"] = [str(self.first), str(self.second)]
        crossing_root["membership"]["include"] = ["umbrella/first", "umbrella/second"]

        for label, scope in (("changed membership", changed), ("expanded selection", expanded),
                             ("crossing root", crossing_root)):
            with self.subTest(case=label):
                result = self.project_owner(scope)
                self.assertFalse(result.get("ok"), result)
                self.assertEqual(result.get("status"), "protected", result)
                self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), before)

    def test_moved_member_symlink_escape_and_linked_temporary_worktree_protect(self):
        self.prepare()
        moved = self.base / "moved-first"
        self.first.rename(moved)
        moved_scope = copy.deepcopy(self.scope)
        moved_scope["member_checkouts"][0] = str(moved)

        external = self.make_repo(self.base / "external")
        escaped_root = self.project / "escaped"
        escaped_root.symlink_to(external, target_is_directory=True)
        escaped_scope = copy.deepcopy(self.scope)
        escaped_scope["member_checkouts"][0] = str(escaped_root)

        linked = self.base / "linked-member"
        subprocess.run(["git", "-C", str(self.second), "worktree", "add", "--detach", str(linked)],
                       check=True, capture_output=True)
        linked_scope = copy.deepcopy(self.scope)
        linked_scope["member_checkouts"][1] = str(linked)

        for label, scope in (("moved", moved_scope), ("symlink escape", escaped_scope),
                             ("linked worktree", linked_scope)):
            with self.subTest(case=label):
                before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
                result = self.project_owner(scope)
                self.assertFalse(result.get("ok"), result)
                self.assertEqual(result.get("status"), "protected", result)
                self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), before)

    def test_mixed_enrolled_and_ordinary_members_protect_without_temporary_fallback(self):
        from tests.test_evidence import CanonicalEvidenceTests

        enrolled = CanonicalEvidenceTests(methodName="runTest")
        enrolled.setUp()
        self.addCleanup(enrolled.doCleanups)
        root = enrolled.fixture.root
        enrolled_repo = enrolled.fixture.repo
        ordinary_repo = self.make_repo(root / "ordinary-member")
        scope = {
            "kind": "umbrella",
            "project_root": str(root),
            "member_checkouts": [str(enrolled_repo), str(ordinary_repo)],
            "membership": {"include": ["repo", "ordinary-member"], "exclude": [],
                           "future_join": False, "authorization": "fixture:mixed"},
            "authority": {"path": str(root / ".vibe-workflow" / "project.json"),
                          "sha256": "absent", "reference": "fixture:mixed-scope"},
            "project_files": [],
        }
        root_before = self.storage_fixture.tree_snapshot(root)
        private_before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
        result = self.project_owner(scope)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        self.assertEqual(self.storage_fixture.tree_snapshot(root), root_before)
        self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), private_before)

    def test_concurrent_first_resolution_converges_on_one_anchor(self):
        self.prepare()
        scopes = [copy.deepcopy(self.scope) for _ in range(4)]
        scopes[1]["member_checkouts"].reverse()
        scopes[2]["member_checkouts"].reverse()
        scopes[3]["member_checkouts"].reverse()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.project_owner, scopes, timeout=20))
        self.assertTrue(all(result.get("ok") for result in results), results)
        identities = {(json.dumps(result.get("owner"), sort_keys=True),
                       json.dumps(result.get("reference"), sort_keys=True), result.get("created_at"))
                      for result in results}
        self.assertEqual(len(identities), 1, results)

    def test_final_member_change_before_commit_leaves_no_project_owner(self):
        from runtime import evidence

        self.prepare()
        with self.api._machine_guard() as (path, descriptor, parents):
            config = self.api._configuration(path, descriptor)
            context = self.api._ready_context(config, parents)
            with evidence._context_database(context) as db:
                metadata_before = db.execute("select count(*) from evidence_metadata").fetchone()[0]
        private_before = self.storage_fixture.tree_snapshot(self.storage_fixture.private)

        git_path = self.second / ".git"
        original_git_path = self.base / "second-original-git"
        register = self.api._register

        def replace_member_after_publication(*args, **kwargs):
            reference = register(*args, **kwargs)
            git_path.rename(original_git_path)
            shutil.copytree(original_git_path, git_path, symlinks=True)
            return reference

        try:
            with patch.object(self.api, "_register", side_effect=replace_member_after_publication):
                result = self.project_owner()
        finally:
            if original_git_path.exists():
                if git_path.exists():
                    shutil.rmtree(git_path)
                original_git_path.rename(git_path)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("status"), "protected", result)
        with self.api._machine_guard() as (path, descriptor, parents):
            config = self.api._configuration(path, descriptor)
            context = self.api._ready_context(config, parents)
            with evidence._context_database(context) as db:
                self.assertEqual(db.execute("select count(*) from evidence_metadata").fetchone()[0],
                                 metadata_before)
        after_orphan = self.storage_fixture.tree_snapshot(self.storage_fixture.private)
        self.assertNotEqual(after_orphan, private_before)
        replay = self.project_owner()
        self.assertFalse(replay.get("ok"), replay)
        self.assertEqual(replay.get("status"), "protected", replay)
        self.assertEqual(self.storage_fixture.tree_snapshot(self.storage_fixture.private), after_orphan)


if __name__ == "__main__":
    unittest.main()
