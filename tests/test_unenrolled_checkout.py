"""Disposable configured-scope regressions; no native qualification or task state."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import onboarding, startup, state
from tests import test_startup


PACKAGE = Path(__file__).resolve().parents[1]


class UnenrolledCheckoutTests(unittest.TestCase):
    client = "codex"
    git = test_startup.StartupTests.git

    def setUp(self):
        test_startup.StartupTests.setUp(self)
        home = patch.dict(os.environ, HOME=str(self.base / "home"))
        home.start()
        self.addCleanup(home.stop)
        self.git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", "fixture")
        self.linked = self.base / "linked"
        self.git(self.repo, "worktree", "add", "--detach", str(self.linked))
        observed, error = onboarding._checkout_observation(str(self.repo), {"paths": 0, "bytes": 0})
        self.assertIsNone(error)
        reference = dict(reference="fixture:primary-only", sha256="a" * 64)
        self.record = dict(schema_version=2, revision=1, project_id="project",
            project_root=str(self.repo), project_binding=onboarding._filesystem_binding(self.repo),
            kind="standalone", membership=dict(include=["."], exclude=[], future_join=False,
                authorization=reference), shared_policy=[], repositories=[dict(repository_id="repository",
                primary_root=str(self.repo), git_common_dir=observed["git_common_dir"], membership="joined",
                binding={k: observed["binding"][k] for k in ("primary_root", "git_common_dir")},
                checkouts=[dict(checkout_id="primary", root=str(self.repo), git_dir=observed["git_dir"],
                    binding={k: observed["binding"][k] for k in ("root", "git_dir")}, clients=[dict(
                        client="codex", configuration=dict(policy_digest=startup.policy_digest(PACKAGE),
                            runtime_digest=state.runtime_fingerprint(), shared_dependencies=[], evidence=[],
                            runtime=dict(schema_version=3, state_dir=str(self.base / "state"))),
                        activation=dict(status="configured", qualification="pending", evidence=[],
                            qualification_binding=None, accepted_scope_digest=None, transaction_id=None))])])])
        self.authority = self.repo / startup.PROJECT_FILE
        self.authority.parent.mkdir()
        self.authority.write_text(json.dumps(self.record))

    def acquire(self, root):
        return startup.acquire_project_entry(SimpleNamespace(client="codex", cwd=root), PACKAGE)

    def hook(self, root):
        payload = dict(hook_event_name="PreToolUse", session_id="fixture-session", cwd=str(root),
                       tool_name="apply_patch", tool_input="*** Begin Patch\n*** End Patch")
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
            "codex", "PreToolUse"], input=json.dumps(payload), text=True, capture_output=True,
            cwd=root, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout) if result.stdout else None

    def test_unlisted_linked_checkout_does_not_inherit_primary_guard(self):
        before = self.authority.read_bytes()
        result = self.acquire(self.linked)
        self.assertEqual((result.resolution.status, result.resolution.reason), ("outside", "not_enrolled"))
        self.assertIsNone(startup._entry_scope(result))
        self.assertIsNone(self.hook(self.linked))
        self.assertEqual(self.authority.read_bytes(), before)
        self.assertFalse((self.base / "state").exists())

    def test_configured_primary_keeps_qualification_guard(self):
        result = self.acquire(self.repo)
        self.assertEqual(result.resolution.reason, "qualification_unobserved")
        with self.assertRaises(startup.RuntimeScopeError):
            startup._entry_scope(result)
        self.assertEqual(self.hook(self.repo)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_future_join_remains_pending(self):
        self.record["membership"]["future_join"] = True
        self.authority.write_text(json.dumps(self.record))
        self.assertEqual(self.acquire(self.linked).resolution.reason, "member_pending")
        self.assertEqual(self.hook(self.linked)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_known_checkout_aliases_and_unknown_membership_fail_closed(self):
        acquired = startup._acquire_canonical_membership(self.linked, authority_path=None,
            budget={"paths": 0, "bytes": 0})
        facts = dict(authority=dict(path=str(acquired.authority_path),
                target=startup._canonical_plain(acquired.authority_target)),
            observation=startup._canonical_plain(acquired.observation), client="codex",
            installed=dict(policy_digest=startup.policy_digest(PACKAGE), runtime_digest=state.runtime_fingerprint(),
                           clients=["codex"], package_root=str(PACKAGE)))
        known = self.record["repositories"][0]["checkouts"][0]
        for changed in ("root_binding", "git_dir", "git_dir_binding", "membership"):
            with self.subTest(changed=changed):
                candidate = copy.deepcopy(facts)
                observation = candidate["observation"]
                if changed == "root_binding":
                    observation["binding"]["root"] = known["binding"]["root"]
                elif changed == "git_dir":
                    observation["git_dir"] = known["git_dir"]
                elif changed == "git_dir_binding":
                    observation["binding"]["git_dir"] = known["binding"]["git_dir"]
                else:
                    observation["membership"] = "unknown"
                result = startup.resolve_canonical_scope(acquired.record, **candidate)
                self.assertEqual((result.status, result.reason), ("conflict", "identity_changed"))
                self.assertIsNone(result.scope)
