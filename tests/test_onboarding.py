"""D3 plans are private review artifacts, never enrollment or executable requests."""

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from runtime.startup import StartupError

PACKAGE = Path(__file__).resolve().parents[1]


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe-onboarding-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        from runtime import review_storage
        machine_config = patch.object(review_storage, "_machine_config_path",
            return_value=self.base / "machine" / "machine.json")
        machine_config.start()
        self.addCleanup(machine_config.stop)
        self.repo = self.base / "target"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "README.md").write_text("product\n")
        (self.repo / "AGENTS.md").write_text("product rules\nlegacy process\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "fixture")
        self.directory = self.base / "plan"
        self.directory.mkdir(mode=0o700)
        self.source = self.directory / "catalog.md"
        self.source.write_text("# Complete catalog\nfixture\nnew-tool\n")
        self.source.chmod(0o600)
        self.record = {"schema_version": 1, "clients": ["codex"],
            "toolkit": {"commit": "a" * 40, "origin": "https://example.invalid/toolkit",
                "acquisition": "online", "provenance": "fixture retrieval, not live evidence",
                "trust": "recorded", "files": [{"path": "catalog.md", "local_path": str(self.source),
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(), "required": True}],
                "catalog": [{"id": "fixture", "source": "catalog.md", "section": "fixture",
                    "stage": "project_onboarding", "mandatory": True, "dependencies": []},
                    {"id": "new-tool", "source": "catalog.md", "section": "new-tool",
                    "stage": "project_onboarding", "mandatory": False, "dependencies": ["fixture"]}]},
            "required_sources": ["catalog.md"]}

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(self.repo),
            *args], check=True, capture_output=True, env=env, timeout=5).stdout

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("runtime.onboarding"),
                             "D3 onboarding helper is missing")
        from runtime import onboarding
        return onboarding

    def inspect(self):
        return self.module().inspect_project(self.repo, self.directory, self.record)

    def project_record(self, root=None, members=None, kind="standalone", files=None):
        root = root or self.repo
        if kind == "umbrella" and self.directory.is_relative_to(root):
            private = tempfile.TemporaryDirectory(prefix="vibe-project-plan-")
            self.addCleanup(private.cleanup)
            directory = Path(private.name).resolve() / "plan"
            self.directory.rename(directory)
            self.directory = directory
            self.source = directory / "catalog.md"
            self.record["toolkit"]["files"][0]["local_path"] = str(self.source)
        authority = root / ".vibe-coding-workflow/project.json"
        return copy.deepcopy(self.record) | {"project_scope": {
            "kind": kind, "project_root": str(root),
            "member_checkouts": [str(p) for p in (members or [self.repo])],
            "membership": {"include": ["."], "exclude": [], "future_join": False,
                           "authorization": "fixture:explicit-scope"},
            "authority": {"path": str(authority), "sha256":
                hashlib.sha256(authority.read_bytes()).hexdigest() if authority.exists() else "absent",
                "reference": "fixture:pending-adoption"}, "project_files": files or []}}

    def second_repository(self):
        first = self.repo
        self.repo = self.base / "second"
        self.repo.mkdir()
        try:
            self.git("init", "-q")
            (self.repo / "second.txt").write_text("second product\n")
            self.git("add", ".")
            self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", "second fixture")
            return self.repo
        finally:
            self.repo = first

    def canonical_authority(self, root=None, members=None, kind="standalone"):
        root = root or self.repo
        module = self.module()
        repositories = []
        for index, member in enumerate(members or [self.repo]):
            observed, error = module._checkout_observation(str(member), {"paths": 0, "bytes": 0})
            self.assertIsNone(error)
            repositories.append(dict(repository_id="accepted-repository-" + str(index),
                primary_root=observed["primary_root"], git_common_dir=observed["git_common_dir"],
                binding={k: observed["binding"][k] for k in ("primary_root", "git_common_dir")},
                membership="joined", checkouts=[dict(checkout_id="accepted-checkout-" + str(index),
                    root=observed["root"], git_dir=observed["git_dir"],
                    binding={k: observed["binding"][k] for k in ("root", "git_dir")}, clients=[])]))
        record = dict(schema_version=2, revision=1, project_id="accepted-project", project_root=str(root),
            kind=kind, project_binding=module._filesystem_binding(root),
            membership=dict(include=["."], exclude=[], future_join=True,
                            authorization=dict(reference="fixture:standing-authority", sha256="a" * 64)),
            shared_policy=[], repositories=repositories)
        location = root / ".vibe-coding-workflow/project.json"
        location.parent.mkdir(exist_ok=True)
        location.write_text(json.dumps(record))
        return location, record

    def test_canonical_reuse_standalone_and_full_authority_target(self):
        location, accepted = self.canonical_authority()
        request = self.project_record()
        self.module().inspect_project(self.repo, self.directory, request)
        plan = self.plan()
        self.assertEqual(plan["identity"]["project_id"], accepted["project_id"])
        self.assertEqual(plan["identity"]["id_origin"], "canonical_binding")
        owner = plan["assessments"]["repositories"][0]["owner"]
        self.assertEqual((owner["repository_id"], owner["checkout_id"]),
                         ("accepted-repository-0", "accepted-checkout-0"))
        authority = next(a for a in plan["identity"]["authority"] if a["path"] == str(location))
        self.assertEqual(authority["record"], accepted)
        self.assertEqual(authority["target"]["fingerprint"], authority["fingerprint"])
        self.assertEqual(set(authority["target"]["parents"]), {str(self.repo), str(location.parent)})
        self.module().inspect_project(self.repo, self.directory, request)
        self.assertEqual(self.plan()["identity"], plan["identity"])

    def test_canonical_reuse_umbrella_preserves_unselected_and_tombstone(self):
        other = self.second_repository()
        location, accepted = self.canonical_authority(self.base, [self.repo, other], "umbrella")
        accepted["repositories"][1]["membership"] = "removed"
        location.write_text(json.dumps(accepted))
        request = self.project_record(self.base, [self.repo], "umbrella")
        self.module().inspect_project(self.base, self.directory, request)
        plan = self.plan()
        self.assertEqual(plan["identity"]["project_id"], "accepted-project")
        self.assertEqual(plan["assessments"]["repositories"][0]["owner"]["repository_id"], "accepted-repository-0")
        self.assertEqual(next(a["record"] for a in plan["identity"]["authority"] if a["path"] == str(location)), accepted)

    def test_canonical_reuse_linked_checkout_and_new_checkout_ids(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        location, accepted = self.canonical_authority(members=[linked])
        request = self.project_record(members=[linked])
        self.module().inspect_project(linked, self.directory, request)
        owner = self.plan()["assessments"]["repositories"][0]["owner"]
        self.assertEqual(owner["checkout_id"], "accepted-checkout-0")
        (self.directory / "plan.json").unlink()
        request = self.project_record()
        self.module().inspect_project(self.repo, self.directory, request)
        owner = self.plan()["assessments"]["repositories"][0]["owner"]
        self.assertEqual(owner["repository_id"], "accepted-repository-0")
        self.assertNotEqual(owner["checkout_id"], "accepted-checkout-0")
        self.assertEqual(owner["root"], str(self.repo))

    def test_canonical_reuse_future_member_proposes_only_new_ids(self):
        other = self.second_repository()
        location, accepted = self.canonical_authority(self.base, [self.repo], "umbrella")
        request = self.project_record(self.base, [other], "umbrella")
        self.module().inspect_project(self.base, self.directory, request)
        plan = self.plan()
        self.assertEqual(plan["identity"]["project_id"], "accepted-project")
        self.assertNotEqual(plan["assessments"]["repositories"][0]["owner"]["repository_id"], "accepted-repository-0")
        self.assertEqual(next(a["record"] for a in plan["identity"]["authority"] if a["path"] == str(location)), accepted)
        self.assertEqual(json.loads(location.read_text()), accepted)

    def test_canonical_reuse_changed_binding_or_removed_owner_never_mints_replacement(self):
        location, accepted = self.canonical_authority()
        for change in ("project", "common", "checkout", "removed"):
            bad = copy.deepcopy(accepted)
            if change == "project": bad["project_binding"]["inode"] += 1
            elif change == "common": bad["repositories"][0]["binding"]["git_common_dir"]["inode"] += 1
            elif change == "checkout": bad["repositories"][0]["checkouts"][0]["binding"]["root"]["inode"] += 1
            else: bad["repositories"][0]["membership"] = "removed"
            location.write_text(json.dumps(bad))
            with self.subTest(change=change), self.assertRaises(StartupError):
                self.module().inspect_project(self.repo, self.directory, self.project_record())
            self.assertFalse((self.directory / "plan.json").exists())

    def test_canonical_reuse_absent_target_and_old_plan_stays_readable_stale(self):
        request = self.project_record()
        self.module().inspect_project(self.repo, self.directory, request)
        plan = self.plan()
        selected = next(a for a in plan["identity"]["authority"] if a["path"] == request["project_scope"]["authority"]["path"])
        self.assertEqual(selected["target"]["fingerprint"], {"status": "absent"})
        self.assertEqual(selected["target"]["parents"], {str(self.repo): self.module()._filesystem_binding(self.repo)})
        for rows in (plan["identity"]["authority"], plan["current_observations"]["authority"]):
            for row in rows: row.pop("target")
        plan["review_digest"] = self.module()._review_digest(plan)
        self.module()._project_plan_shape(plan)
        (self.directory / "plan.json").write_text(json.dumps(plan))
        self.module().inspect_project(self.repo, self.directory, request)
        current = self.plan()
        self.assertEqual(current["status"], "stale")
        self.assertEqual(current["identity"]["authority"], plan["identity"]["authority"])
        self.assertTrue(all("target" not in a for a in current["current_observations"]["authority"]))

    def test_canonical_reuse_saved_owner_must_match_frozen_authority(self):
        self.canonical_authority()
        self.module().inspect_project(self.repo, self.directory, self.project_record())
        plan = self.plan()
        owner = plan["assessments"]["repositories"][0]["owner"]
        owner["repository_id"] = "replacement-id"
        plan["current_observations"]["repositories"][0]["owner"] = copy.deepcopy(owner)
        with self.assertRaises(StartupError): self.module()._project_plan_shape(plan)

    def test_canonical_reuse_old_parentless_plan_current_read_keeps_missing_proof(self):
        request = self.project_record()
        self.module().inspect_project(self.repo, self.directory, request)
        plan = self.plan()
        for rows in (plan["identity"]["authority"], plan["current_observations"]["authority"]):
            for row in rows: row.pop("target")
        current, _ = self.module()._project_current(plan)
        self.assertEqual(current["observations"]["authority"], plan["identity"]["authority"])
        self.assertIn("authority_parent_observations", self.module()._project_changes(plan, current))

    def test_canonical_reuse_same_bytes_moved_root_blocks(self):
        location, accepted = self.canonical_authority()
        moved = self.base / "moved"
        self.repo.rename(moved)
        self.repo = moved
        with self.assertRaises(StartupError):
            self.module().inspect_project(moved, self.directory, self.project_record())
        self.assertFalse((self.directory / "plan.json").exists())
        self.assertEqual(json.loads((moved / ".vibe-coding-workflow/project.json").read_text()), accepted)

    def test_canonical_reuse_overlapping_member_authority_blocks(self):
        self.canonical_authority(self.base, [self.repo], "umbrella")
        child = self.repo / ".vibe-coding-workflow/project.json"
        child.parent.mkdir()
        child.write_text(json.dumps(dict(schema_version=1, project_id="different-owner", clients=["codex"],
            repository_root=str(self.repo), git_common_dir=str(self.repo / ".git"),
            policy_digest=self.module().policy_digest(PACKAGE))))
        request = self.project_record(self.base, kind="umbrella")
        with self.assertRaisesRegex(StartupError, "authority|binding"):
            self.module().inspect_project(self.base, self.directory, request)
        self.assertFalse((self.directory / "plan.json").exists())

    def test_checkout_observer_accepts_paths_only_and_preserves_used_budget_and_deadline(self):
        from runtime import startup
        import time
        module = self.module()
        empty = {"paths": 0}
        observed, error = module._checkout_observation(str(self.repo), empty)
        self.assertIsNone(error)
        self.assertEqual(observed["root"], str(self.repo))
        self.assertGreater(empty["bytes"], 0)
        deadline = time.monotonic() + 60
        used = {"paths": 3, "bytes": 123, "deadline": deadline}
        with patch.object(startup, "read_git", wraps=startup.read_git) as reader:
            _, error = module._checkout_observation(str(self.repo), used)
        self.assertIsNone(error)
        self.assertEqual(used["bytes"], empty["bytes"] + 123)
        self.assertEqual(used["paths"], empty["paths"] + 3)
        self.assertEqual(used["deadline"], deadline)
        self.assertTrue(all(call.kwargs["deadline"] == deadline for call in reader.call_args_list))
        expired = {"paths": 0, "bytes": 99, "deadline": time.monotonic() - 1}
        retained = dict(expired)
        _, error = module._checkout_observation(str(self.repo), expired)
        self.assertIsNotNone(error)
        self.assertEqual(expired, retained)

    def test_canonical_deadline_stops_parent_observation_before_next_binding(self):
        from runtime import startup
        module = self.module()
        path = self.repo / "deadline.txt"; path.write_text("observed")
        fingerprint = module._fingerprint(self.repo, path.name)
        clock = [100.0]; calls = []
        original = module._filesystem_binding
        def expire_after_binding(parent):
            result = original(parent)
            calls.append(parent); clock[0] = 104.0
            return result
        with patch.object(startup.time, "monotonic", side_effect=lambda: clock[0]),              patch.object(module, "_filesystem_binding", side_effect=expire_after_binding):
            with self.assertRaises(StartupError):
                module._physical_target(Path(path.anchor), path, fingerprint,
                    {"paths": 0, "bytes": 0, "deadline": 104.0})
        self.assertEqual(len(calls), 1)

    def test_expected_binding_read_rejects_same_inode_change_during_descriptor_read(self):
        module = self.module()
        path = self.repo / "stable-read.txt"; path.write_text("original")
        info = path.stat(); binding = dict(device=info.st_dev, inode=info.st_ino)
        original = os.fstat
        observed = []
        def change_after_first_stat(descriptor):
            metadata = original(descriptor)
            if not observed:
                path.write_text("changed during the same descriptor read")
                observed.append(True)
            return metadata
        with patch.object(module.os, "fstat", side_effect=change_after_first_stat):
            with self.assertRaises(StartupError):
                module._read_file(path, 1024, expected_binding=binding)
        self.assertTrue(observed)
        self.assertEqual(path.stat().st_ino, info.st_ino)

    def test_canonical_reuse_authority_read_rechecks_physical_target(self):
        location, _ = self.canonical_authority()
        read = self.module()._read_file
        replaced, reads = False, 0
        def replace_after_read(path, *args, **kwargs):
            nonlocal replaced, reads
            raw = read(path, *args, **kwargs)
            if path == location:
                reads += 1
            if path == location and reads == 2 and not replaced:
                replacement = location.with_name("replacement.json")
                replacement.write_bytes(raw)
                replacement.replace(location)
                replaced = True
            return raw
        with patch.object(self.module(), "_read_file", side_effect=replace_after_read):
            observed = self.module()._authority_observation(self.repo, {"paths": 0, "bytes": 0})
        self.assertTrue(replaced)
        self.assertIsNotNone(observed["error"])

    def test_canonical_reuse_new_checkout_cannot_alias_retained_private_git_identity(self):
        _, accepted = self.canonical_authority()
        observed, error = self.module()._checkout_observation(str(self.repo), {"paths": 0, "bytes": 0})
        self.assertIsNone(error)
        observed["membership"] = "included"
        observed["root"] = str(self.base / "new-checkout")
        observed["git_dir"] = str(self.repo / ".git/worktrees/new-checkout")
        observed["binding"]["root"]["inode"] += 1
        with self.assertRaisesRegex(StartupError, "identity|ownership"):
            self.module()._canonical_owner_ids(accepted, observed)

    def test_canonical_reuse_stale_configuration_is_inspected_without_qualification(self):
        location, accepted = self.canonical_authority()
        accepted["repositories"][0]["checkouts"][0]["clients"] = [dict(client="codex",
            configuration=dict(policy_digest="a" * 64, runtime_digest="b" * 64, runtime=None,
                               shared_dependencies=[], evidence=[]),
            activation=dict(status="configured", qualification="pending", accepted_scope_digest=None,
                            transaction_id=None, evidence=[], qualification_binding=None))]
        location.write_text(json.dumps(accepted))
        self.module().inspect_project(self.repo, self.directory, self.project_record())
        self.assertEqual(self.plan()["identity"]["id_origin"], "canonical_binding")
        before = (self.directory / "plan.json").read_bytes()
        accepted["revision"] += 1
        location.write_text(json.dumps(accepted))
        with self.assertRaises(StartupError):
            self.module().inspect_project(self.repo, self.directory, self.project_record())
        self.assertEqual((self.directory / "plan.json").read_bytes(), before)

    def test_project_standalone_keeps_private_ids_and_selected_checkout_without_mutation(self):
        record = self.project_record()
        before = self.git("status", "--porcelain=v1", "-z")
        report = self.module().inspect_project(self.repo, self.directory, record)
        plan = self.plan()
        self.assertEqual(plan["schema_version"], 2)
        self.assertEqual(report["status"], "blocked")
        self.assertIsNone(plan["assessments"]["project"])
        repository = plan["assessments"]["repositories"][0]
        self.assertEqual(repository["owner"]["root"], str(self.repo))
        self.assertEqual(repository["owner"]["primary_root"], str(self.repo))
        self.assertEqual(repository["identity"]["snapshot"]["paths"], ["AGENTS.md", "README.md"])
        self.assertEqual(plan["sources"]["declared"], self.record)
        self.assertEqual(plan["identity"]["id_origin"], "proposed")
        self.module().inspect_project(self.repo, self.directory, record)
        resumed = self.plan()
        self.assertEqual(resumed["identity"], plan["identity"])
        self.assertEqual(resumed["assessments"], plan["assessments"])
        self.assertEqual(resumed["revision"], 2)
        self.assertEqual(self.git("status", "--porcelain=v1", "-z"), before)
        self.assertFalse((self.repo / ".vibe-coding-workflow").exists())
        self.assertFalse((self.repo / ".codex").exists())

    def test_project_private_plan_cannot_be_created_inside_project_or_selected_worktree(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        cases = [(self.repo, self.project_record(members=[linked]), linked / "private-plan"),
                 (self.base, self.project_record(self.base, kind="umbrella"), self.base / "private-plan")]
        for cwd, record, inside in cases:
            with self.subTest(inside=inside):
                with self.assertRaisesRegex(StartupError, "outside the Project"):
                    self.module().inspect_project(cwd, inside, record)
                self.assertFalse(inside.exists())

    def test_project_umbrella_inspects_only_named_project_files_and_two_explicit_members(self):
        other = self.second_repository()
        (self.base / "AGENTS.md").write_text("shared rules\n")
        record = self.project_record(self.base, [self.repo, other], "umbrella", ["AGENTS.md"])
        report = self.module().inspect_project(self.base, self.directory, record)
        plan = self.plan()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(plan["assessments"]["repositories"]), 2)
        project = plan["assessments"]["project"]
        self.assertEqual(project["identity"]["paths"], ["AGENTS.md"])
        self.assertEqual(set(project["identity"]["fingerprints"]), {"AGENTS.md"})
        self.assertNotIn("head", project["identity"])
        owners = [r["owner"] for r in plan["assessments"]["repositories"]]
        self.assertEqual(len({r["repository_id"] for r in owners}), 2)
        self.assertEqual(len({r["checkout_id"] for r in owners}), 2)
        self.assertEqual({r["project_id"] for r in owners}, {plan["identity"]["project_id"]})
        self.module().inspect_project(self.base, self.directory, record)
        self.assertEqual(self.plan()["assessments"], plan["assessments"])
        self.assertFalse((self.base / ".vibe-coding-workflow").exists())

    def test_project_linked_worktree_keeps_action_root_and_resolves_primary_from_git(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        record = self.project_record(members=[linked])
        self.module().inspect_project(linked, self.directory, record)
        repository = self.plan()["assessments"]["repositories"][0]
        self.assertEqual(repository["owner"]["root"], str(linked))
        self.assertEqual(repository["owner"]["primary_root"], str(self.repo))
        self.assertEqual(repository["identity"]["snapshot"]["repository_root"], str(linked))
        self.assertNotEqual(repository["owner"]["git_dir"], repository["owner"]["git_common_dir"])
        self.module().inspect_project(linked, self.directory, record)
        self.assertEqual(self.plan()["assessments"]["repositories"][0], repository)

    def test_project_exclusions_and_symlink_escapes_are_gaps_without_member_reads(self):
        other = self.second_repository()
        alias = self.base / "alias"
        alias.symlink_to(other, target_is_directory=True)
        record = self.project_record(self.base, [self.repo, alias], "umbrella")
        record["project_scope"]["membership"]["include"] = ["target", "alias"]
        record["project_scope"]["membership"]["exclude"] = ["target"]
        self.module().inspect_project(self.base, self.directory, record)
        repositories = self.plan()["assessments"]["repositories"]
        self.assertTrue(all(r["identity"]["snapshot"] is None for r in repositories))
        self.assertTrue(any("excluded" in b["reason"] for b in self.plan()["blockers"]))
        self.assertTrue(any("boundary" in b["reason"] or "link" in b["reason"] for b in self.plan()["blockers"]))

    def test_project_named_files_cannot_enter_member_or_git_or_symlink_trees(self):
        (self.base / "linked-file").symlink_to(self.repo / "README.md")
        record = self.project_record(self.base, kind="umbrella",
            files=["target/README.md", "target/.git/config", "linked-file"])
        report = self.module().inspect_project(self.base, self.directory, record)
        fingerprints = self.plan()["assessments"]["project"]["identity"]["fingerprints"]
        self.assertTrue(all(v["status"] == "blocked" for v in fingerprints.values()))
        self.assertTrue(any("Project file" in b["reason"] for b in report["blockers"]))

    def test_project_named_files_never_fingerprint_unselected_bare_git_metadata(self):
        bare = self.base / "bare"
        self.git("init", "--bare", "-q", str(bare))
        record = self.project_record(self.base, kind="umbrella",
            files=["bare/config", "bare/missing/new.txt", "docs/new.txt"])
        before = (bare / "config").read_bytes()
        with patch.object(self.module(), "_inspection_fingerprint",
                          wraps=self.module()._inspection_fingerprint) as fingerprints:
            report = self.module().inspect_project(self.base, self.directory, record)
        observed = self.plan()["assessments"]["project"]["identity"]["fingerprints"]
        self.assertEqual(observed["bare/config"], {"status": "blocked"})
        self.assertEqual(observed["bare/missing/new.txt"], {"status": "blocked"})
        self.assertEqual(observed["docs/new.txt"], {"status": "absent"})
        self.assertFalse(any(call.args[0] == self.base and call.args[1].startswith("bare/")
                             for call in fingerprints.call_args_list))
        self.assertTrue(any(b["scope"] == "bare/config" and "Git" in b["reason"] for b in report["blockers"]))
        self.assertEqual((bare / "config").read_bytes(), before)
        self.assertFalse((bare / "missing").exists())
        self.assertFalse((self.base / "docs").exists())

    def test_project_scope_change_and_changed_authority_cannot_reuse_a_private_identity(self):
        record = self.project_record()
        self.module().inspect_project(self.repo, self.directory, record)
        before = (self.directory / "plan.json").read_bytes()
        changed = copy.deepcopy(record)
        changed["project_scope"]["membership"]["future_join"] = True
        with self.assertRaisesRegex(StartupError, "scope"):
            self.module().inspect_project(self.repo, self.directory, changed)
        authority = self.repo / ".vibe-coding-workflow/project.json"
        authority.parent.mkdir()
        authority.write_text('{"schema_version":1}')
        with self.assertRaisesRegex(StartupError, "authority|binding"):
            self.module().inspect_project(self.repo, self.directory, record)
        self.assertEqual((self.directory / "plan.json").read_bytes(), before)

    def test_project_reinspection_retains_frozen_snapshot_and_marks_changed_content_stale(self):
        record = self.project_record()
        self.module().inspect_project(self.repo, self.directory, record)
        before = self.plan()
        (self.repo / "README.md").write_text("changed product\n")
        report = self.module().inspect_project(self.repo, self.directory, record)
        self.assertEqual(report["status"], "stale")
        self.assertEqual(self.plan()["assessments"], before["assessments"])
        self.assertNotEqual(self.plan()["current_observations"], before["current_observations"])
        self.assertNotEqual(report["current_input_digest"], before["current_input_digest"])

    def test_project_legacy_identity_reuse_requires_exact_authority_binding(self):
        authority = self.repo / ".vibe-coding-workflow/project.json"
        authority.parent.mkdir()
        module = self.module()
        legacy = {"schema_version": 1, "project_id": "accepted-fixture", "clients": ["codex"],
            "repository_root": str(self.repo), "git_common_dir": str(self.repo / ".git"),
            "policy_digest": module.policy_digest(module.PACKAGE)}
        authority.write_text(json.dumps(legacy))
        record = self.project_record()
        module.inspect_project(self.repo, self.directory, record)
        self.assertEqual(self.plan()["identity"]["project_id"], "accepted-fixture")
        self.assertEqual(self.plan()["identity"]["id_origin"], "legacy_binding")
        self.assertTrue(all(a["owner"]["repository_id"] != "accepted-fixture"
                            for a in self.plan()["assessments"]["repositories"]))

    def test_project_copied_or_member_authority_is_a_conflict_not_id_transfer(self):
        authority = self.repo / ".vibe-coding-workflow/project.json"
        authority.parent.mkdir()
        authority.write_text(json.dumps({"schema_version": 1, "project_id": "copied-id",
            "repository_root": str(self.base / "original"), "git_common_dir": str(self.base / "original/.git"),
            "clients": ["codex"], "policy_digest": self.module().policy_digest(PACKAGE)}))
        record = self.project_record(self.base, kind="umbrella")
        report = self.module().inspect_project(self.base, self.directory, record)
        self.assertNotEqual(self.plan()["identity"]["project_id"], "copied-id")
        self.assertTrue(any("authority" in b["reason"] for b in report["blockers"]))

    def test_project_same_path_replaced_checkout_cannot_reuse_identity(self):
        record = self.project_record()
        self.module().inspect_project(self.repo, self.directory, record)
        before = (self.directory / "plan.json").read_bytes()
        self.repo.rename(self.base / "moved")
        self.repo.mkdir()
        self.git("init", "-q")
        with self.assertRaisesRegex(StartupError, "binding"):
            self.module().inspect_project(self.repo, self.directory, record)
        self.assertEqual((self.directory / "plan.json").read_bytes(), before)

    def test_project_duplicate_repository_and_git_umbrella_stay_explicitly_blocked(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "--detach", str(linked), "HEAD")
        record = self.project_record(self.repo, [self.repo, linked], "umbrella")
        report = self.module().inspect_project(self.repo, self.directory, record)
        reasons = " ".join(b["reason"] for b in report["blockers"])
        self.assertIn("non-Git", reasons)
        self.assertIn("same Repository", reasons)

    def test_project_aggregate_inventory_limit_is_a_gap_not_complete_partial_coverage(self):
        other = self.second_repository()
        record = self.project_record(self.base, [self.repo, other], "umbrella")
        with patch.object(self.module(), "MAX_TOTAL", 80):
            report = self.module().inspect_project(self.base, self.directory, record)
        self.assertTrue(any("aggregate" in b["reason"] for b in report["blockers"]))
        self.assertEqual(report["status"], "blocked")

    def test_project_invalid_authority_json_and_worktree_metadata_are_explicit_gaps(self):
        authority = self.repo / ".vibe-coding-workflow/project.json"
        authority.parent.mkdir()
        authority.write_text('{"project_id": "first", "project_id": "second"}')
        record = self.project_record()
        original = self.module()._git
        def broken_worktrees(root, *args, **kwargs):
            return (0, b"not-porcelain\x00") if args[:2] == ("worktree", "list") else original(root, *args, **kwargs)
        with patch.object(self.module(), "_git", side_effect=broken_worktrees):
            report = self.module().inspect_project(self.repo, self.directory, record)
        reasons = " ".join(b["reason"] for b in report["blockers"])
        self.assertIn("authority", reasons)
        self.assertIn("worktree", reasons)
        self.assertEqual(self.plan()["identity"]["id_origin"], "proposed")

    def test_project_record_plan_rejects_legacy_payload_before_validation_without_mutation(self):
        self.module().inspect_project(self.repo, self.directory, self.project_record())
        before = (self.directory / "plan.json").read_bytes()
        with self.assertRaisesRegex(StartupError, "object fields"):
            self.module().record_plan(self.directory, {})
        self.assertEqual((self.directory / "plan.json").read_bytes(), before)

    def test_project_saved_shape_rejects_unbound_owners_and_unknown_observation_fields(self):
        self.module().inspect_project(self.repo, self.directory, self.project_record())
        original = self.plan()
        variants = []
        changed = copy.deepcopy(original)
        changed["current_observations"]["unexpected"] = True
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["assessments"]["repositories"][0]["owner"]["project_id"] = "another-project"
        variants.append(changed)
        changed = copy.deepcopy(original)
        changed["assessments"]["repositories"][0]["identity"]["root"] = str(self.base)
        variants.append(changed)
        changed = copy.deepcopy(original)
        wrong = changed["assessments"]["repositories"][0]
        wrong["owner"] = {"kind": "project", "project_id": original["identity"]["project_id"], "root": str(self.repo)}
        wrong["identity"] = {"project_root": str(self.repo), "paths": [], "fingerprints": {}}
        changed["current_observations"]["repositories"][0] = {
            key: copy.deepcopy(wrong[key]) for key in ("owner", "identity")}
        variants.append(changed)
        for key in ("sources", "invalidations", "decision_history", "package_pins", "status", "requirements",
                    "blockers", "evidence_limits", "created_at", "verified_at", "recovery_requirement",
                    "review_digest", "current_input_digest", "plan_id"):
            changed = copy.deepcopy(original)
            changed[key] = None
            variants.append(changed)
        for keys, value in ((["sources", "declared"], None), (["sources", "observed"], None),
                            (["sources", "observed", "catalog.md"], None),
                            (["package_pins", "runtime_digest"], "invalid"),
                            (["invalidations"], [{}]),
                            (["project_scope", "membership", "future_join"], "yes"),
                            (["assessments", "repositories", 0], None),
                            (["current_observations", "repositories", 0], None),
                            (["assessments", "repositories", 0, "inventory"], None),
                            (["assessments", "repositories", 0, "identity", "binding"], None),
                            (["assessments", "repositories", 0, "identity", "snapshot", "fingerprints"], None)):
            changed = copy.deepcopy(original)
            target = changed
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            variants.append(changed)
        for number, value in enumerate(variants):
            with self.subTest(variant=number):
                (self.directory / "plan.json").write_text(json.dumps(value))
                with self.assertRaises(StartupError):
                    self.module()._load(self.directory)

    def test_project_saved_project_slot_requires_project_owner(self):
        record = self.project_record(self.base, kind="umbrella")
        self.module().inspect_project(self.base, self.directory, record)
        plan = self.plan()
        wrong = copy.deepcopy(plan["assessments"]["repositories"][0])
        wrong["owner"].update(repository_id="wrong-project-slot-repository", checkout_id="wrong-project-slot-checkout")
        plan["assessments"]["project"] = wrong
        plan["current_observations"]["project"] = {key: wrong[key] for key in ("owner", "identity")}
        (self.directory / "plan.json").write_text(json.dumps(plan))
        with self.assertRaises(StartupError):
            self.module()._load(self.directory)

    def test_project_saved_stale_pins_and_missing_sources_remain_readable(self):
        record = self.project_record()
        self.module().inspect_project(self.repo, self.directory, record)
        plan = self.plan()
        plan["package_pins"] = {"policy_digest": "a" * 64, "runtime_digest": "b" * 64}
        plan["sources"]["observed"]["catalog.md"] = {"status": "unavailable"}
        plan["status"] = "stale"
        (self.directory / "plan.json").write_text(json.dumps(plan))
        self.source.unlink()
        with patch.object(self.module(), "_sources", side_effect=AssertionError("loader read source bytes")), \
                patch.object(self.module(), "_package_pins", side_effect=AssertionError("loader checked current pins")):
            self.assertEqual(self.module()._load(self.directory), plan)
        report = self.module().inspect_project(self.repo, self.directory, record)
        self.assertEqual(report["status"], "stale")
        self.assertTrue(any(b["scope"] == "source:catalog.md" for b in report["blockers"]))

    def test_project_copied_selected_authority_conflict_remains_on_reinspection(self):
        authority = self.repo / ".vibe-coding-workflow/project.json"
        authority.parent.mkdir()
        authority.write_text(json.dumps({"schema_version": 1, "project_id": "copied-id",
            "repository_root": str(self.base / "original"), "git_common_dir": str(self.base / "original/.git"),
            "clients": ["codex"], "policy_digest": self.module().policy_digest(PACKAGE)}))
        record = self.project_record()
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                report = self.module().inspect_project(self.repo, self.directory, record)
                self.assertEqual(self.plan()["identity"]["id_origin"], "proposed")
                self.assertNotEqual(self.plan()["identity"]["project_id"], "copied-id")
                self.assertTrue(any(b["scope"] == "authority" for b in report["blockers"]))

    def test_project_aggregate_path_limit_and_missing_member_have_explicit_gaps(self):
        record = self.project_record(self.base, [self.repo, self.base / "missing"], "umbrella")
        with patch.object(self.module(), "MAX_PATHS", 24):
            report = self.module().inspect_project(self.base, self.directory, record)
        self.assertTrue(any("aggregate" in b["reason"] for b in report["blockers"]))
        self.assertTrue(any("observation incomplete" in b["reason"] for b in report["blockers"]))
        self.assertIsNone(self.plan()["assessments"]["repositories"][1]["identity"]["snapshot"])

    def test_project_explicit_scope_and_legacy_inspect_use_existing_cli(self):
        import sys
        command = [sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
            "onboard", "inspect", "--cwd", str(self.repo), "--plan-dir", str(self.directory)]
        for record, schema in ((self.record, 1), (self.project_record(), 2)):
            with self.subTest(schema=schema):
                (self.directory / "plan.json").unlink(missing_ok=True)
                result = subprocess.run(command, input=json.dumps(record).encode(), capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(self.plan()["schema_version"], schema)
                self.assertFalse(result.stderr)

    def plan(self):
        return json.loads((self.directory / "plan.json").read_text())

    def proposal(self, revision):
        rows = []
        for item in self.plan()["requirements"]:
            rows.append({**item, "applicability": "applicable", "reason": "required assessment",
                "current_adoption": "absent", "qualification_evidence": [], "outcome": "planned",
                "actions": ["write-rules"], "scope": "checkout", "risk": "review before D4 apply",
                "verification": "D4 verification pending", "blocker": "", "resumption": "",
                "decision_ids": []})
        return {"expected_revision": revision,
            "inventory": {key: {"facts": [{"summary": "observed fixture " + key, "evidence": ["inventory"]}], "assumptions": []}
                for key in ("product", "critical_flows", "stack", "quality_commands", "services",
                            "client_capabilities", "merge_feasibility", "instructions", "integrations")}
                | {"documents": {str(i): {"path": None, "reason": "fixture missing"} for i in range(9)}},
            "decisions": [], "coverage": rows, "actions": [{"id": "write-rules", "dependencies": [],
                "kind": "write", "target": "AGENTS.md", "section": "legacy process",
                "before": hashlib.sha256((self.repo / "AGENTS.md").read_bytes()).hexdigest(),
                "text": "product rules\nnew process\n", "after": hashlib.sha256(b"product rules\nnew process\n").hexdigest(),
                "scope": "checkout", "authorization": "user:review-only",
                "verification": "D4 checks resulting sections", "preservation_exceptions": ["product rules"],
                "decision_ids": [], "backup_required": True}],
            "evidence": [{"id": "inventory", "kind": "observation", "summary": "fixture inventory observations",
                "scope": "checkout", "inputs": ["README.md"], "reference": "disposable fixture"}],
            "semantic_assessment": {"complete": True, "catalog_complete": True,
                "conflicts_checked": True, "instructions_checked": True, "source_provenance_checked": True,
                "native_evaluation": {"method": "disposable native harness", "smoke_cases": ["startup"]}}}

    def qualified(self, owner, local):
        parts = (["project", owner["project_id"]] if owner["kind"] == "project" else
                 ["repository", owner["repository_id"], owner["checkout_id"]])
        return "/".join(part.replace("~", "~0").replace("/", "~1") for part in [*parts, local])

    def scoped_proposal(self):
        plan = self.plan()
        result = {"expected_revision": plan["revision"], "assessments": copy.deepcopy(plan["assessments"])}
        for assessment in [result["assessments"]["project"], *result["assessments"]["repositories"]]:
            if assessment is None:
                continue
            owner = assessment["owner"]
            root = Path(owner["root"])
            payload = self.proposal(plan["revision"])
            payload.pop("expected_revision")
            action = payload["actions"][0]
            action.update(owner=copy.deepcopy(owner), cwd=None)
            target = root / "AGENTS.md"
            action["before"] = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else "absent"
            ref = lambda local: self.qualified(owner, local)
            for section in payload["inventory"].values():
                if "facts" in section:
                    for fact in section["facts"]:
                        fact["evidence"] = [ref("inventory")]
            for row in payload["coverage"]:
                row["actions"] = [ref("write-rules")]
            snapshot = assessment["identity"].get("snapshot", assessment["identity"])
            payload["evidence"][0]["inputs"] = [snapshot["paths"][0]]
            if owner["kind"] == "project":
                payload["inventory"] = {"instructions": payload["inventory"]["instructions"],
                    "documents": {"shared": {"path": "AGENTS.md", "reason": "fixture shared instruction"}}}
                payload["coverage"] = []
                payload["semantic_assessment"].pop("catalog_complete")
                payload["semantic_assessment"].pop("native_evaluation")
                action["scope"] = "shared"
                payload["evidence"][0].update(kind="configuration", scope="shared")
            for evidence in payload["evidence"]:
                evidence["input_digest"] = self.module()._evidence_digest(owner, snapshot, plan["package_pins"],
                    plan["sources"]["observed"], evidence["inputs"])
            assessment.update(payload)
        project = result["assessments"]["project"]
        if project:
            shared = self.qualified(project["owner"], "write-rules")
            for repository in result["assessments"]["repositories"]:
                repository["actions"][0]["dependencies"] = [shared]
                repository["coverage"][0]["actions"].append(shared)
        return result

    def scoped_inspect(self, umbrella=False, linked=False):
        if umbrella:
            other = self.second_repository()
            (self.base / "AGENTS.md").write_text("shared fixture rules\n")
            record = self.project_record(self.base, [self.repo, other], "umbrella", ["AGENTS.md"])
            cwd = self.base
        elif linked:
            cwd = self.base / "linked"
            self.git("worktree", "add", "--detach", str(cwd), "HEAD")
            record = self.project_record(members=[cwd])
        else:
            cwd, record = self.repo, self.project_record()
        self.module().inspect_project(cwd, self.directory, record)
        return cwd, record

    def test_scoped_complete_standalone_and_two_repository_proposals(self):
        for umbrella in (False, True):
            with self.subTest(umbrella=umbrella):
                (self.directory / "plan.json").unlink(missing_ok=True)
                cwd, record = self.scoped_inspect(umbrella=umbrella)
                request = self.scoped_proposal()
                report = self.module().record_plan(self.directory, request)
                self.assertEqual(report["status"], "ready_for_review", report)
                saved = self.plan()
                if umbrella:
                    self.assertEqual(saved["assessments"]["project"]["coverage"], [])
                    self.assertNotIn("product", saved["assessments"]["project"]["inventory"])
                else:
                    self.assertIsNone(saved["assessments"]["project"])
                report = self.module().inspect_project(cwd, self.directory, record)
                self.assertEqual(report["status"], "ready_for_review", report)
                self.assertEqual(saved["assessments"], self.plan()["assessments"])
                self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_scoped_every_repository_requires_complete_coverage_and_assessment(self):
        self.scoped_inspect(umbrella=True)
        for index in range(2):
            request = self.scoped_proposal()
            request["assessments"]["repositories"][index]["coverage"].pop(0)
            report = self.module().record_plan(self.directory, request)
            self.assertEqual(report["status"], "blocked", report)
            self.assertTrue(any("Coverage assessment is missing" in b["reason"] for b in report["blockers"]))
        request = self.scoped_proposal()
        request["assessments"]["repositories"].pop()
        with self.assertRaises(StartupError):
            self.module().record_plan(self.directory, request)

    def test_scoped_qualified_refs_reject_bare_missing_cyclic_and_sibling_behavior(self):
        self.scoped_inspect(umbrella=True)
        for defect in ("bare", "missing", "cycle", "sibling"):
            with self.subTest(defect=defect):
                request = self.scoped_proposal()
                project = request["assessments"]["project"]
                first, second = request["assessments"]["repositories"]
                if defect in ("bare", "missing"):
                    first["actions"][0]["dependencies"] = ["write-rules" if defect == "bare" else
                        self.qualified(project["owner"], "missing")]
                elif defect == "cycle":
                    project["actions"][0]["dependencies"] = [self.qualified(first["owner"], "write-rules")]
                else:
                    second["evidence"][0]["kind"] = "behavior"
                    first["coverage"][0].update(outcome="reuse", current_adoption="qualified",
                        qualification_evidence=[self.qualified(second["owner"], "inventory")])
                with self.assertRaises(StartupError):
                    self.module().record_plan(self.directory, request)
        self.assertEqual(self.plan()["revision"], 1)

    def test_scoped_shared_configuration_and_local_behavior_proof(self):
        self.scoped_inspect(umbrella=True)
        request = self.scoped_proposal()
        project = request["assessments"]["project"]
        first = request["assessments"]["repositories"][0]
        first["coverage"][0]["qualification_evidence"] = [self.qualified(project["owner"], "inventory")]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        request = self.scoped_proposal()
        first = request["assessments"]["repositories"][0]
        first["evidence"][0]["kind"] = "behavior"
        first["evidence"][0]["input_digest"] = self.behavior_digest(first, first["evidence"][0])
        first["coverage"][0].update(outcome="reuse", current_adoption="qualified",
            qualification_evidence=[self.qualified(first["owner"], "inventory")])
        self.assertEqual(self.module().record_plan(self.directory, request)["status"], "ready_for_review")

    def test_scoped_linked_target_and_new_named_absence_are_pinned(self):
        cwd, record = self.scoped_inspect(linked=True)
        request = self.scoped_proposal()
        action = request["assessments"]["repositories"][0]["actions"][0]
        action.update(target="ignored/new.md", before="absent")
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        saved = self.plan()["assessments"]["repositories"][0]
        self.assertEqual(saved["owner"]["root"], str(cwd))
        self.assertEqual(saved["identity"]["targets"]["ignored/new.md"]["path"], str(cwd / "ignored/new.md"))
        self.assertEqual(saved["identity"]["snapshot"]["fingerprints"]["ignored/new.md"], {"status": "absent"})
        self.module().inspect_project(cwd, self.directory, record)
        self.assertEqual(self.plan()["assessments"]["repositories"][0], saved)
        self.assertFalse((cwd / "ignored").exists())

    def test_scoped_hidden_project_targets_and_cross_owner_hardlinks_block(self):
        self.scoped_inspect(umbrella=True)
        for name in ("target/AGENTS.md", "target/.git/config", "target/.git/new/file"):
            request = self.scoped_proposal()
            request["assessments"]["project"]["actions"][0].update(target=name, before="absent")
            with self.assertRaises(StartupError):
                self.module().record_plan(self.directory, request)
        other = self.base / "second"
        os.link(self.repo / "AGENTS.md", other / "AGENTS.md")
        request = self.scoped_proposal()
        with self.assertRaisesRegex(StartupError, "alias|overlap|observation"):
            self.module().record_plan(self.directory, request)
        self.assertEqual(self.plan()["revision"], 1)

    def test_scoped_new_project_targets_and_ancestor_conflicts(self):
        self.scoped_inspect(umbrella=True)
        request = self.scoped_proposal()
        project = request["assessments"]["project"]
        project["actions"][0].update(target="docs/shared.md", before="absent")
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertIn("docs/shared.md", self.plan()["assessments"]["project"]["identity"]["paths"])
        request = self.scoped_proposal()
        project = request["assessments"]["project"]
        project["actions"][0].update(target="docs", before="absent")
        duplicate = copy.deepcopy(project["actions"][0])
        duplicate.update(id="nested", target="docs/file")
        project["actions"].append(duplicate)
        with self.assertRaisesRegex(StartupError, "overlap|ancestor"):
            self.module().record_plan(self.directory, request)

    def scoped_decision(self, assessment):
        owner = assessment["owner"]
        return {"id": "choice", "rule": "fixture", "source": "catalog.md", "scope": "checkout",
            "options": ["adopt"], "recommendation": "adopt", "selected": "adopt", "disposition": "confirmed",
            "provenance": "fixture explicitly selected", "evidence": [self.qualified(owner, "inventory")],
            "affected_actions": [self.qualified(owner, "write-rules")], "invalidate_on": [assessment["evidence"][0]["inputs"][0]],
            "conflict": False, "mode": None}

    def test_scoped_stale_reconciliation_preserves_qualified_decision_history(self):
        cwd, record = self.scoped_inspect(umbrella=True)
        request = self.scoped_proposal()
        for a in request["assessments"]["repositories"]:
            a["decisions"] = [self.scoped_decision(a)]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        with self.assertRaisesRegex(StartupError, "revision"):
            self.module().record_plan(self.directory, request)
        request["expected_revision"] = report["revision"]
        with self.assertRaisesRegex(StartupError, "decision"):
            self.module().record_plan(self.directory, self.scoped_proposal())
        (self.base / "AGENTS.md").write_text("changed shared fixture\n")
        report = self.module().inspect_project(cwd, self.directory, record)
        self.assertEqual(report["status"], "stale")
        saved = self.plan()
        self.assertTrue(all(a["decisions"][0]["disposition"] == "pending" for a in saved["assessments"]["repositories"]))
        self.assertEqual(len({entry["decision"]["id"] for entry in saved["decision_history"]}), 2)
        request = self.scoped_proposal()
        for a in request["assessments"]["repositories"]:
            a["decisions"] = [self.scoped_decision(a)]
        request["reconcile"] = {"input_digest": report["current_input_digest"], "reason": "fixture reinspection"}
        for a, observed in zip(self.module()._assessments(request["assessments"]),
                               self.module()._assessments(self.plan()["current_observations"])):
            identity = observed["identity"].get("snapshot", observed["identity"])
            for evidence in a["evidence"]:
                evidence["input_digest"] = self.module()._evidence_digest(a["owner"], identity,
                    self.plan()["package_pins"], self.plan()["sources"]["observed"], evidence["inputs"])
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertEqual(len(self.plan()["decision_history"]), 4)

    def test_scoped_reconciliation_rejects_source_owner_and_authority_changes(self):
        self.scoped_inspect()
        request = self.scoped_proposal()
        changed = copy.deepcopy(request)
        changed["assessments"]["repositories"][0]["owner"]["checkout_id"] = "replacement"
        with self.assertRaises(StartupError):
            self.module().record_plan(self.directory, changed)
        self.source.write_text("changed source fixture")
        report = self.module().inspect_project(self.repo, self.directory, self.project_record())
        request = self.scoped_proposal()
        request["reconcile"] = {"input_digest": report["current_input_digest"], "reason": "cannot repin source"}
        with self.assertRaisesRegex(StartupError, "source bytes"):
            self.module().record_plan(self.directory, request)
        self.assertEqual(self.plan()["sources"]["declared"], self.record)

    def test_scoped_saved_malformed_proposal_rejected_without_target_writes(self):
        self.scoped_inspect()
        report = self.module().record_plan(self.directory, self.scoped_proposal())
        self.assertEqual(report["status"], "ready_for_review", report)
        plan = self.plan()
        plan["assessments"]["repositories"][0]["actions"][0]["dependencies"] = None
        (self.directory / "plan.json").write_text(json.dumps(plan))
        with self.assertRaises(StartupError):
            self.module()._load(self.directory)
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_scoped_reconciled_receipts_need_new_local_and_shared_input_bindings(self):
        cwd, record = self.scoped_inspect(umbrella=True)
        request = self.scoped_proposal()
        first = request["assessments"]["repositories"][0]
        first["evidence"][0]["kind"] = "behavior"
        first["coverage"][0].update(outcome="reuse", current_adoption="qualified",
            qualification_evidence=[self.qualified(first["owner"], "inventory")])
        first["evidence"][0]["input_digest"] = self.behavior_digest(first, first["evidence"][0])
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        old_receipt = copy.deepcopy(first["evidence"][0])
        (self.base / "AGENTS.md").write_text("changed shared fixture\n")
        report = self.module().inspect_project(cwd, self.directory, record)
        request = self.scoped_proposal()
        first = request["assessments"]["repositories"][0]
        first["evidence"][0] = old_receipt
        first["coverage"][0].update(outcome="reuse", current_adoption="qualified",
            qualification_evidence=[self.qualified(first["owner"], "inventory")])
        request["reconcile"] = {"input_digest": report["current_input_digest"], "reason": "fixture reassessment"}
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked", report)
        self.assertTrue(any("Evidence does not match" in b["reason"] for b in report["blockers"]))
        self.assertEqual(self.plan()["assessments"]["repositories"][0]["evidence"][0], old_receipt)

    def behavior_digest(self, assessment, evidence):
        plan = self.plan()
        # Recorded fixture observations only; this is not native execution evidence.
        inputs = {name: assessment["identity"]["snapshot"]["fingerprints"].get(name) for name in evidence["inputs"]}
        project = plan["assessments"]["project"]
        return self.module()._digest({"owner": assessment["owner"], "inputs": inputs,
            "context": {"checkout": assessment["identity"]["snapshot"],
                        "project_files": project["identity"]["fingerprints"] if project else {},
                        "package_pins": plan["package_pins"], "sources": plan["sources"]["observed"]}})

    def test_scoped_aggregate_rows_include_all_owner_inventory_and_coverage(self):
        self.scoped_inspect(umbrella=True)
        request = self.scoped_proposal()
        with patch.object(self.module(), "MAX_PATHS", 60):
            with self.assertRaisesRegex(StartupError, "aggregate.*row"):
                self.module().record_plan(self.directory, request)
        self.assertEqual(self.plan()["revision"], 1)

    def test_scoped_command_cwd_is_explicit_observed_and_owner_bound(self):
        self.scoped_inspect()
        request = self.scoped_proposal()
        repository = request["assessments"]["repositories"][0]
        command = repository["actions"][0]
        command.update(kind="command", target="fixture-check", section=None, before=None,
                       text=["fixture", "--check"], after=None, cwd=str(self.repo))
        evidence = copy.deepcopy(repository["evidence"][0])
        evidence.update(id="authority", kind="authorization")
        repository["evidence"].append(evidence)
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        targets = self.plan()["assessments"]["repositories"][0]["identity"]["targets"]
        self.assertEqual(targets["cwd:" + str(self.repo)]["binding"], self.module()._filesystem_binding(self.repo))
        request = self.scoped_proposal()
        request["assessments"]["repositories"][0]["actions"][0] = command | {"cwd": str(self.base)}
        request["assessments"]["repositories"][0]["evidence"].append(evidence)
        with self.assertRaisesRegex(StartupError, "owner"):
            self.module().record_plan(self.directory, request)

    def test_scoped_dependencies_preserve_owner_order_and_shared_linkage(self):
        self.scoped_inspect(umbrella=True)
        request = self.scoped_proposal()
        first = request["assessments"]["repositories"][0]
        extra = copy.deepcopy(first["actions"][0])
        extra.update(id="later", target="later.md", before="absent", dependencies=[])
        first["actions"].append(extra)
        first["actions"][0]["dependencies"] = [self.qualified(first["owner"], "later")]
        with self.assertRaisesRegex(StartupError, "order|Cyclic"):
            self.module().record_plan(self.directory, request)
        request = self.scoped_proposal()
        request["assessments"]["repositories"][1]["coverage"][0]["actions"].pop()
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked", report)
        self.assertTrue(any("linkage" in b["reason"] for b in report["blockers"]))

    def test_scoped_reconciliation_rejects_wrong_digest_and_changed_checkout_authority(self):
        self.scoped_inspect()
        request = self.scoped_proposal()
        request["reconcile"] = {"input_digest": "0" * 64, "reason": "wrong fixture observation"}
        with self.assertRaisesRegex(StartupError, "inputs changed"):
            self.module().record_plan(self.directory, request)
        authority = self.repo / ".vibe-coding-workflow/project.json"
        authority.parent.mkdir()
        authority.write_text("{}")
        request.pop("reconcile")
        with self.assertRaisesRegex(StartupError, "authority"):
            self.module().record_plan(self.directory, request)
        self.assertEqual(self.plan()["revision"], 1)

    def test_scoped_current_input_change_during_validation_preserves_plan(self):
        self.scoped_inspect()
        before = (self.directory / "plan.json").read_bytes()
        original = self.module()._validate_project_proposal
        calls = 0
        def changed(plan):
            nonlocal calls
            result = original(plan)
            calls += 1
            if calls == 2:
                (self.repo / "README.md").write_text("raced fixture input\n")
            return result
        with patch.object(self.module(), "_validate_project_proposal", side_effect=changed):
            result = self.module().record_plan(self.directory, self.scoped_proposal())
        self.assertEqual(result["status"], "stale", result)
        self.assertNotEqual((self.directory / "plan.json").read_bytes(), before)

    def test_scoped_aggregate_bytes_and_incomplete_owner_never_reach_ready(self):
        other = self.second_repository()
        (self.repo / "large.txt").write_text("a" * 1800)
        (other / "large.txt").write_text("b" * 1800)
        record = self.project_record(self.base, [self.repo, other], "umbrella")
        with patch.object(self.module(), "MAX_TOTAL", 3000):
            report = self.module().inspect_project(self.base, self.directory, record)
        self.assertEqual(report["status"], "blocked", report)
        self.assertTrue(any("aggregate byte" in b["reason"] for b in report["blockers"]))
        self.assertTrue(any(a["identity"]["snapshot"] is None for a in self.plan()["assessments"]["repositories"]))

    def test_scoped_saved_history_and_target_shapes_reject_malformed_records(self):
        self.scoped_inspect()
        request = self.scoped_proposal()
        repository = request["assessments"]["repositories"][0]
        repository["decisions"] = [self.scoped_decision(repository)]
        self.module().record_plan(self.directory, request)
        request = self.scoped_proposal()
        repository = request["assessments"]["repositories"][0]
        repository["decisions"] = [self.scoped_decision(repository) | {"disposition": "pending"}]
        self.module().record_plan(self.directory, request)
        original = self.plan()
        for defect in ("history", "targets", "inputs", "cwd"):
            with self.subTest(defect=defect):
                plan = copy.deepcopy(original)
                repository = plan["assessments"]["repositories"][0]
                if defect == "history":
                    plan["decision_history"][0]["decision"]["disposition"] = {"bad": True}
                elif defect == "targets":
                    repository["identity"]["targets"]["AGENTS.md"]["parents"] = []
                elif defect == "inputs":
                    repository["evidence"][0]["input_digest"] = False
                else:
                    repository["actions"][0]["cwd"] = str(self.repo)
                (self.directory / "plan.json").write_text(json.dumps(plan))
                with self.assertRaises(StartupError):
                    self.module()._load(self.directory)
        (self.directory / "plan.json").write_text(json.dumps(original))
        self.source.unlink()
        self.assertEqual(self.module()._load(self.directory), original)

    def test_scoped_plan_existing_cli_accepts_qualified_assessments(self):
        import sys
        self.scoped_inspect()
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
            "onboard", "plan", "--plan-dir", str(self.directory)],
            input=json.dumps(self.scoped_proposal()).encode(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["status"], "ready_for_review")
        self.assertFalse(result.stderr)

    def test_scoped_saved_source_location_rejected_before_reading_sources(self):
        self.scoped_inspect()
        original = self.plan()
        request = self.scoped_proposal()
        outside = self.base / "private-source.txt"
        outside.write_text("private fixture must not be read\n")
        outside.chmod(0o600)
        locations = [str(outside), str(self.directory) + "/../private-source.txt",
                     str(self.directory / "plan.json"), str(self.directory / ".lock"),
                     str(self.directory / ".plan-hidden"), str(self.directory)]
        for location in locations:
            with self.subTest(location=location):
                plan = copy.deepcopy(original)
                plan["sources"]["declared"]["toolkit"]["files"][0]["local_path"] = location
                (self.directory / "plan.json").write_text(json.dumps(plan))
                with patch.object(self.module(), "_sources", side_effect=AssertionError("sources must not be read")):
                    with self.assertRaises(StartupError):
                        self.module().record_plan(self.directory, request)
        (self.directory / "plan.json").write_text(json.dumps(original))
        self.source.unlink()
        self.assertEqual(self.module()._load(self.directory), original)

    def test_scoped_sibling_configuration_and_actions_cannot_substitute_for_local_coverage(self):
        self.scoped_inspect(umbrella=True)
        for defect in ("configuration", "action"):
            request = self.scoped_proposal()
            first, second = request["assessments"]["repositories"]
            if defect == "configuration":
                second["evidence"][0]["kind"] = "configuration"
                first["coverage"][0]["qualification_evidence"] = [self.qualified(second["owner"], "inventory")]
            else:
                first["coverage"][1]["actions"] = [self.qualified(second["owner"], "write-rules")]
            with self.subTest(defect=defect), self.assertRaises(StartupError):
                self.module().record_plan(self.directory, request)

    def test_scoped_saved_requirement_deletion_cannot_qualify_current_package(self):
        self.scoped_inspect()
        plan = self.plan()
        removed = plan["requirements"].pop()
        self.assertTrue(removed["mandatory"])
        (self.directory / "plan.json").write_text(json.dumps(plan))
        with self.assertRaisesRegex(StartupError, "requirements"):
            self.module().record_plan(self.directory, self.scoped_proposal())
        self.assertEqual(self.plan()["revision"], 1)

    def test_scoped_capped_blocked_plan_stays_saveable_when_inputs_become_stale(self):
        self.scoped_inspect()
        plan = self.plan()
        plan["assessments"] = self.scoped_proposal()["assessments"]
        repository = plan["assessments"]["repositories"][0]
        decision = self.scoped_decision(repository)
        decision["affected_actions"] = []
        repository.update(actions=[], coverage=[], decisions=[decision])
        for key in repository["semantic_assessment"]:
            repository["semantic_assessment"][key] = ({} if key == "native_evaluation" else False)
        repository["semantic_assessment"]["native_evaluation"] = {"method": "", "smoke_cases": []}
        # Synthetic bounded observations exercise the final blocker list, without creating target files.
        snapshot = repository["identity"]["snapshot"]
        snapshot["fingerprints"] = {name: snapshot["fingerprints"][name] for name in ("AGENTS.md", "README.md")}
        snapshot["fingerprints"].update({f"excluded-{i}.txt": {"status": "excluded"} for i in range(48)})
        current = {"observations": copy.deepcopy(self.module()._frozen_project(plan)),
                   "sources": copy.deepcopy(plan["sources"]["observed"]),
                   "package_pins": copy.deepcopy(plan["package_pins"])}
        with patch.object(self.module(), "MAX_PATHS", 64):
            report = self.module()._finish_project(plan, current, [])
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(len(report["blockers"]), 64)
            self.assertEqual(report["blockers"][-1]["scope"], "limits")
            self.module()._project_plan_shape(plan)
            with self.module()._locked(self.directory) as (path, directory):
                self.module()._save(plan, path, directory)
            self.assertEqual(self.module()._load(self.directory), plan)
            plan["revision"] += 1
            current["sources"]["catalog.md"]["sha256"] = "f" * 64
            report = self.module()._finish_project(plan, current, [])
            self.assertEqual(report["status"], "stale")
            self.assertEqual(len(report["blockers"]), 64)
            self.assertEqual(report["blockers"][0]["scope"], "sources")
            self.assertEqual(report["blockers"][-1]["scope"], "limits")
            self.assertEqual(repository["decisions"][0]["disposition"], "pending")
            self.assertEqual(plan["decision_history"][0]["decision"]["id"],
                             self.qualified(repository["owner"], "choice"))
            self.assertEqual(plan["decision_history"][0]["decision"]["disposition"], "confirmed")
            self.assertEqual(plan["invalidations"][-1]["revision"], plan["revision"])
            self.module()._project_plan_shape(plan)
            with self.module()._locked(self.directory) as (path, directory):
                self.module()._save(plan, path, directory)
            self.assertEqual(self.module()._load(self.directory), plan)

    def test_inspection_creates_private_plan_without_target_or_runtime_mutation(self):
        before = self.git("status", "--porcelain=v1", "-z")
        result = self.inspect()
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["revision"], 1)
        plan = self.plan()
        self.assertEqual(plan["identity"]["repository_root"], str(self.repo))
        self.assertEqual(len(plan["requirements"]), 13)
        self.assertEqual(self.git("status", "--porcelain=v1", "-z"), before)
        self.assertFalse((self.repo / ".vibe-coding-workflow").exists())
        self.assertEqual((self.directory / "plan.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(plan["sources"]["observed"]["catalog.md"]["sha256"],
                         self.record["toolkit"]["files"][0]["sha256"])
        self.assertIn("not authenticate", " ".join(result["evidence_limits"]))

    def test_complete_exact_proposal_is_review_ready_and_digest_binds_text(self):
        report = self.inspect()
        report = self.module().record_plan(self.directory, self.proposal(report["revision"]))
        self.assertEqual(report["status"], "ready_for_review", report)
        original = report["review_digest"]
        request = self.proposal(report["revision"])
        request["actions"][0]["text"] = "changed process\n"
        request["actions"][0]["after"] = hashlib.sha256(b"changed process\n").hexdigest()
        report = self.module().record_plan(self.directory, request)
        self.assertNotEqual(original, report["review_digest"])
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_missing_new_catalog_row_blocks_full_coverage(self):
        self.inspect()
        request = self.proposal(1)
        request["coverage"] = [r for r in request["coverage"] if r["id"] != "new-tool"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any(b["scope"] == "new-tool" for b in report["blockers"]))

    def test_dirty_untracked_absence_and_ancestor_instruction_changes_invalidate(self):
        for path in ("README.md", "new.txt", "../AGENTS.md"):
            with self.subTest(path=path):
                self.inspect()
                before = self.plan()["revision"]
                request = self.proposal(before)
                request["actions"][0]["target"] = "new.txt"
                request["actions"][0]["before"] = "absent"
                (self.repo / path).write_text("changed private contents")
                report = self.module().record_plan(self.directory, request)
                self.assertEqual(report["status"], "stale", report)
                self.assertNotIn("changed private contents", json.dumps(self.plan()))
                (self.repo / path).unlink()
                if path == "README.md":
                    (self.repo / path).write_text("product\n")

    def test_source_tampering_does_not_replace_declared_hash(self):
        self.inspect()
        self.source.write_text("tampered")
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        self.assertEqual(self.plan()["sources"]["declared"], self.record)

    def test_pending_trust_offline_or_missing_source_has_explicit_gap(self):
        self.record["toolkit"].update(acquisition="offline", trust="pending")
        self.source.unlink()
        report = self.inspect()
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("provenance" in b["reason"] for b in report["blockers"]))
        self.assertTrue(any(b["scope"] == "source:catalog.md" for b in report["blockers"]))
        self.assertIn("freshness", " ".join(report["evidence_limits"]))

    def test_source_scope_or_selected_checkout_cannot_be_replaced_on_resume(self):
        self.inspect()
        changed = copy.deepcopy(self.record)
        changed["toolkit"]["commit"] = "b" * 40
        with self.assertRaises(StartupError):
            self.module().inspect_project(self.repo, self.directory, changed)
        other = self.base / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        with self.assertRaises(StartupError):
            self.module().inspect_project(other, self.directory, self.record)
        self.assertEqual(self.plan()["revision"], 1)

    def test_revision_conflict_and_busy_lock_never_lose_decisions(self):
        self.inspect()
        request = self.proposal(1)
        self.module().record_plan(self.directory, request)
        before = (self.directory / "plan.json").read_bytes()
        with self.assertRaisesRegex(StartupError, "revision"):
            self.module().record_plan(self.directory, request)
        with open(self.directory / ".lock", "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(StartupError, "busy"):
                self.module().record_plan(self.directory, self.proposal(2))
        self.assertEqual((self.directory / "plan.json").read_bytes(), before)

    def test_private_no_link_and_nonrepository_boundaries(self):
        module = self.module()
        self.directory.chmod(0o755)
        with self.assertRaises(StartupError):
            self.inspect()
        self.directory.chmod(0o700)
        alias = self.base / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(StartupError):
            module.inspect_project(self.repo, alias, self.record)
        with self.assertRaises(StartupError):
            module.inspect_project(self.base, self.directory, self.record)
        inside = self.repo / "private"
        with self.assertRaises(StartupError):
            module.inspect_project(self.repo, inside, self.record)
        self.assertFalse(inside.exists())
        self.inspect()
        plan = self.directory / "plan.json"
        plan.unlink()
        plan.symlink_to(self.repo / "README.md")
        with self.assertRaises(StartupError):
            self.inspect()

    def test_duplicate_nonfinite_and_oversized_json_are_rejected(self):
        module = self.module()
        for raw in (b'{"clients":[],"clients":[]}', b'{"x":NaN}', b'[]',
                    b'{"x":"' + b'x' * module.MAX_REQUEST + b'"}'):
            with self.subTest(raw=raw[:25]):
                with self.assertRaises((StartupError, ValueError)):
                    module.read_request(raw)

    def test_action_hash_dependencies_and_paths_are_validated(self):
        self.inspect()
        for field, value in (("target", "../AGENTS.md"), ("after", "0" * 64),
                             ("dependencies", ["missing"]), ("backup_required", False)):
            with self.subTest(field=field):
                request = self.proposal(1)
                request["actions"][0][field] = value
                with self.assertRaises(StartupError):
                    self.module().record_plan(self.directory, request)
        self.assertEqual(self.plan()["revision"], 1)

    def test_equivalence_requires_behavior_evidence_bound_to_current_inputs(self):
        self.inspect()
        request = self.proposal(1)
        row = request["coverage"][0]
        row.update(outcome="reuse", current_adoption="configured", qualification_evidence=["config"], actions=[])
        request["evidence"].append({"id": "config", "kind": "configuration", "summary": "configured only",
            "scope": "checkout", "inputs": ["README.md"], "reference": "local inspection"})
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        request["expected_revision"] = report["revision"]
        row.update(current_adoption="qualified", qualification_evidence=["behavior"])
        request["evidence"][-1].update(id="behavior", kind="behavior", summary="observed fixture behavior")
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)

    def test_required_conflict_modes_and_pending_decision_preserve_independent_progress(self):
        self.inspect()
        request = self.proposal(1)
        decision = {"id": "conflict", "rule": "policy:01-project-scope", "source": "AGENTS.md",
            "scope": "checkout", "options": ["preserve", "replace", "decide"],
            "recommendation": "decide", "selected": None, "disposition": "pending",
            "provenance": "unanswered user choice", "evidence": [], "affected_actions": ["write-rules"],
            "invalidate_on": ["AGENTS.md"], "conflict": True, "mode": "decide"}
        request["decisions"] = [decision]
        request["actions"][0]["decision_ids"] = ["conflict"]
        request["coverage"][0]["decision_ids"] = ["conflict"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "awaiting_decision")
        self.assertEqual(report["next_decision"]["id"], "conflict")
        self.assertEqual(len(self.plan()["coverage"]), 13)
        decision.update(selected="preserve", disposition="confirmed", mode="preserve")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        decision.update(selected="replace", mode="replace")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        request["decisions"] = []
        request["actions"][0]["decision_ids"] = []
        request["coverage"][0]["decision_ids"] = []
        request["expected_revision"] = report["revision"]
        with self.assertRaisesRegex(StartupError, "decision"):
            self.module().record_plan(self.directory, request)

    def test_optional_omission_has_reason_and_shared_or_duplicate_actions_block_review(self):
        self.inspect()
        request = self.proposal(1)
        row = next(row for row in request["coverage"] if row["id"] == "new-tool")
        row.update(outcome="omit", applicability="inapplicable", actions=[], reason="")
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        row["reason"] = "optional integration is not needed"
        request["expected_revision"] = report["revision"]
        duplicate = copy.deepcopy(request["actions"][0])
        duplicate["id"] = "duplicate"
        request["actions"].append(duplicate)
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any("overlap" in b["reason"] for b in report["blockers"]))

    def test_package_change_rechecks_pins_and_records_invalidation_history(self):
        self.inspect()
        with patch("runtime.onboarding.policy_digest", return_value="f" * 64):
            report = self.module().record_plan(self.directory, self.proposal(1))
        self.assertEqual(report["status"], "stale")
        self.assertTrue(self.plan()["invalidations"])


    def test_scoped_reconciliation_rechecks_current_bytes_and_preserves_decision_history(self):
        self.inspect()
        request = self.proposal(1)
        self.module().record_plan(self.directory, request)
        (self.repo / "README.md").write_text("changed product\n")
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        request = self.proposal(report["revision"])
        request["reconcile"] = {"input_digest": report["current_input_digest"],
                                "reason": "agent re-inspected changed product and dependent facts"}
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertTrue(self.plan()["invalidations"])
        self.assertEqual(self.plan()["identity"]["fingerprints"]["README.md"]["sha256"],
                         hashlib.sha256(b"changed product\n").hexdigest())
        (self.repo / "README.md").write_text("changed again\n")
        request["expected_revision"] = report["revision"]
        with self.assertRaisesRegex(StartupError, "reconciliation"):
            self.module().record_plan(self.directory, request)

    def test_facts_without_evidence_and_missing_native_cases_block_review(self):
        self.inspect()
        request = self.proposal(1)
        request["inventory"]["product"]["facts"][0]["evidence"] = []
        request["semantic_assessment"]["native_evaluation"]["smoke_cases"] = []
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked")
        self.assertTrue(any(b["scope"] == "inventory:product" for b in report["blockers"]))

    def test_two_cli_writers_cannot_commit_the_same_revision(self):
        import sys
        self.inspect()
        request = json.dumps(self.proposal(1)).encode()
        command = [sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
                   "onboard", "plan", "--plan-dir", str(self.directory)]
        processes = [subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE) for _ in range(2)]
        for process in processes:
            process.stdin.write(request)
            process.stdin.close()
            process.stdin = None
        outputs = [process.communicate(timeout=10) for process in processes]
        self.assertEqual(sorted(process.returncode for process in processes), [0, 1])
        self.assertEqual(self.plan()["revision"], 2)
        self.assertTrue(all(not stderr for _, stderr in outputs))

    def test_hardlinked_source_is_a_gap_and_plan_hardlink_is_rejected(self):
        alias = self.directory / "copy"
        os.link(self.source, alias)
        report = self.inspect()
        self.assertEqual(report["status"], "blocked")
        alias.unlink()
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        alias = self.directory / "copy-plan"
        os.link(self.directory / "plan.json", alias)
        with self.assertRaises(StartupError):
            self.inspect()



    def test_staged_blob_change_is_stale_when_status_and_worktree_are_unchanged(self):
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("first index bytes\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.inspect()
        before = self.git("status", "--porcelain=v1", "-z")
        (self.repo / "README.md").write_text("second index bytes\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.assertEqual(self.git("status", "--porcelain=v1", "-z"), before)
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        self.assertTrue(any(b["scope"] == "checkout:index_digest" for b in report["blockers"]))



    def test_unknown_source_stage_cannot_become_a_reviewable_requirement(self):
        self.record["toolkit"]["catalog"][0]["stage"] = "invented-stage"
        with self.assertRaises(StartupError):
            self.inspect()



    def test_exact_command_proposals_preserve_repeated_argv_values_without_execution(self):
        self.inspect()
        request = self.proposal(1)
        argv = ["git", "-c", "a=b", "-c", "c=d", "status"]
        request["actions"][0].update(kind="command", target="checkout baseline", section=None,
            before=None, text=argv, after=None)
        request["evidence"].append({"id": "command-scope", "kind": "authorization",
            "summary": "review-only command proposal authorized", "scope": "checkout",
            "inputs": ["README.md"], "reference": "user:review-only"})
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertEqual(self.plan()["actions"][0]["text"], argv)
        self.assertFalse((self.repo / "a=b").exists())



    def decision(self, identity, trigger, disposition="confirmed"):
        return {"id": identity, "rule": "policy:01-project-scope", "source": "README.md",
            "scope": "checkout", "options": ["keep", "change"], "recommendation": "keep",
            "selected": "keep", "disposition": disposition, "provenance": "fixture user choice",
            "evidence": ["inventory"], "affected_actions": ["write-rules"],
            "invalidate_on": [trigger], "conflict": False, "mode": None}

    def test_full_postimages_for_distinct_sections_still_own_the_same_file(self):
        self.inspect()
        request = self.proposal(1)
        second = copy.deepcopy(request["actions"][0])
        second.update(id="write-product", dependencies=["write-rules"], section="product rules",
                      text="changed product rules\nlegacy process\n")
        second["after"] = hashlib.sha256(second["text"].encode()).hexdigest()
        request["actions"].append(second)
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "blocked", report)
        self.assertTrue(any("overlap" in b["reason"] for b in report["blockers"]))
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")

    def test_ignored_proposed_directory_instructions_are_pinned_before_review(self):
        (self.repo / ".gitignore").write_text("scoped/\n")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "ignore scoped output")
        scoped = self.repo / "scoped"
        scoped.mkdir()
        rules = scoped / "AGENTS.md"
        rules.write_text("keep scoped contracts\n")
        self.inspect()
        request = self.proposal(1)
        request["actions"][0].update(target="scoped/result.md", section=None, before="absent")
        instruction_key = "instruction:" + str(rules)
        request["evidence"][0]["inputs"].append(instruction_key)
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        self.assertEqual(self.plan()["identity"]["fingerprints"][instruction_key]["sha256"],
                         hashlib.sha256(b"keep scoped contracts\n").hexdigest())
        digest = report["current_input_digest"]
        rules.write_text("changed scoped contracts\n")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "stale", report)
        self.assertNotEqual(report["current_input_digest"], digest)
        self.assertTrue(any(b["scope"] == instruction_key for b in report["blockers"]))
        self.assertFalse((scoped / "result.md").exists())

    def test_ignored_codex_override_changes_and_creation_invalidate_each_instruction_scope(self):
        (self.repo / ".gitignore").write_text("AGENTS.override.md\nscoped/\n")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "ignore overrides and scoped output")
        scoped = self.repo / "scoped" / "nested"
        scoped.mkdir(parents=True)
        source_text = self.source.read_text()
        for scope, directory in (("root", self.repo), ("ancestor", self.base),
                                 ("action", scoped), ("action-ancestor", scoped.parent)):
            for existing in (True, False):
                with self.subTest(scope=scope, existing=existing):
                    self.directory = self.base / f"plan-{scope}-{existing}"
                    self.directory.mkdir(mode=0o700)
                    self.source = self.directory / "catalog.md"
                    self.source.write_text(source_text)
                    self.source.chmod(0o600)
                    self.record["toolkit"]["files"][0]["local_path"] = str(self.source)
                    rules = directory / "AGENTS.override.md"
                    rules.unlink(missing_ok=True)
                    if existing:
                        rules.write_text("original override\n")
                    self.inspect()
                    request = self.proposal(1)
                    request["actions"][0].update(target="scoped/nested/result.md", section=None,
                                                 before="absent")
                    key = "instruction:" + str(rules)
                    report = self.module().record_plan(self.directory, request)
                    self.assertEqual(report["status"], "ready_for_review", report)
                    fingerprint = self.plan()["identity"]["fingerprints"][key]
                    self.assertEqual(fingerprint["status"], "read" if existing else "absent")
                    if existing:
                        self.assertEqual(fingerprint["sha256"], hashlib.sha256(b"original override\n").hexdigest())
                        self.assertIn(str(rules), [row["path"] for row in self.plan()["identity"]["instructions"]])
                    request["expected_revision"] = report["revision"]
                    request["decisions"] = [self.decision("override-choice", key)]
                    request["actions"][0]["decision_ids"] = ["override-choice"]
                    report = self.module().record_plan(self.directory, request)
                    self.assertEqual(report["status"], "ready_for_review", report)
                    before = report
                    git_before = self.git("status", "--porcelain=v1", "-z")
                    rules.write_text("changed override\n")
                    report = self.inspect()
                    self.assertEqual(self.git("status", "--porcelain=v1", "-z"), git_before)
                    self.assertEqual(report["status"], "stale", report)
                    self.assertNotEqual(report["current_input_digest"], before["current_input_digest"])
                    self.assertNotEqual(report["review_digest"], before["review_digest"])
                    self.assertTrue(any(row["scope"] == key for row in report["blockers"]))
                    self.assertEqual(self.plan()["decisions"][0]["disposition"], "pending")
                    self.assertEqual(self.plan()["identity"]["fingerprints"][key], fingerprint)
                    self.assertFalse((scoped / "result.md").exists())
                    self.assertEqual((self.repo / "AGENTS.md").read_text(), "product rules\nlegacy process\n")
                    rules.unlink()

    def test_staged_metadata_evidence_invalidates_only_the_dependent_choice(self):
        (self.repo / "README.md").write_text("first staged blob\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.inspect()
        request = self.proposal(1)
        request["evidence"][0]["inputs"].append("checkout:index_digest")
        request["decisions"] = [self.decision("staged-choice", "checkout:index_digest"),
                                self.decision("stable-choice", "README.md")]
        request["actions"][0]["decision_ids"] = ["staged-choice", "stable-choice"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "ready_for_review", report)
        dirty = self.git("status", "--porcelain=v1", "-z")
        (self.repo / "README.md").write_text("second staged blob\n")
        self.git("add", "README.md")
        (self.repo / "README.md").write_text("stable worktree\n")
        self.assertEqual(self.git("status", "--porcelain=v1", "-z"), dirty)
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        choices = {d["id"]: d for d in self.plan()["decisions"]}
        self.assertEqual(choices["staged-choice"]["disposition"], "pending")
        self.assertEqual(choices["stable-choice"]["disposition"], "confirmed")
        invalidation = self.plan()["invalidations"][-1]
        self.assertEqual(invalidation["decisions"], ["staged-choice"])
        self.assertEqual(invalidation["actions"], ["write-rules"])
        request["expected_revision"] = report["revision"]
        request["decisions"] = list(choices.values())
        request["reconcile"] = {"input_digest": report["current_input_digest"],
                                "reason": "re-inspected new staged content; dependent choice still pending"}
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "awaiting_decision", report)
        self.assertEqual(self.plan()["decisions"][1]["disposition"], "confirmed")

    def test_inspect_invalidation_preserves_prior_dispositions_once(self):
        self.inspect()
        request = self.proposal(1)
        request["decisions"] = [self.decision("confirmed-choice", "README.md"),
                                self.decision("inherited-choice", "README.md", "inherited")]
        self.module().record_plan(self.directory, request)
        (self.repo / "README.md").write_text("changed product\n")
        report = self.inspect()
        self.assertEqual(report["status"], "stale")
        history = self.plan()["decision_history"]
        self.assertEqual({h["decision"]["id"]: h["decision"]["disposition"] for h in history},
                         {"confirmed-choice": "confirmed", "inherited-choice": "inherited"})
        self.assertEqual(len(history), 2)
        self.inspect()
        self.assertEqual(self.plan()["decision_history"], history)
        self.assertTrue(all(d["disposition"] == "pending" for d in self.plan()["decisions"]))
        self.assertTrue(all("new run" not in b["resumption"] for b in report["blockers"]
                            if b["scope"] == "README.md"))

    def test_plan_invalidation_keeps_original_snapshot_without_duplicate_history(self):
        self.inspect()
        request = self.proposal(1)
        request["decisions"] = [self.decision("confirmed-choice", "README.md"),
                                self.decision("inherited-choice", "README.md", "inherited")]
        report = self.module().record_plan(self.directory, request)
        (self.repo / "README.md").write_text("changed product\n")
        request["expected_revision"] = report["revision"]
        report = self.module().record_plan(self.directory, request)
        self.assertEqual(report["status"], "stale")
        history = self.plan()["decision_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual([h["decision"]["disposition"] for h in history], ["confirmed", "inherited"])
        self.assertEqual([d["disposition"] for d in request["decisions"]], ["confirmed", "inherited"])

    def test_only_native_adapter_clients_are_accepted_for_project_plans(self):
        for client in ("index", "runtime", "startup", "superset"):
            with self.subTest(client=client):
                self.record["clients"] = [client]
                with self.assertRaises(StartupError):
                    self.inspect()
                self.assertFalse((self.directory / "plan.json").exists())
        self.record["clients"] = ["codex", "claude-code"]
        report = self.inspect()
        self.assertEqual(report["status"], "draft")
        self.assertEqual(self.plan()["identity"]["clients"], ["codex", "claude-code"])


if __name__ == "__main__":
    unittest.main()
