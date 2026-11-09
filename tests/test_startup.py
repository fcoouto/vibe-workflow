"""Disposable checkout tests; no client, model, network or real enrollment."""

import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime.adapters import claude_code, codex
from runtime.startup import MAX_INPUT, PROJECT_FILE, checkout, orient, policy_digest
from scripts.validate import validate


PACKAGE = Path(__file__).resolve().parents[1]


class JsonBoundaryTests(unittest.TestCase):
    def test_internal_json_bound_preserves_external_cap_and_strict_parser(self):
        import inspect
        from runtime.startup import json_object, StartupError
        from runtime.evidence import MAX_ARTIFACT
        self.assertIn('limit', inspect.signature(json_object).parameters)
        raw = b'{"value":"' + b'x' * MAX_INPUT + b'"}'
        with self.assertRaises(StartupError):
            json_object(raw)
        self.assertEqual(len(json_object(raw, limit=MAX_ARTIFACT)['value']), MAX_INPUT)
        unicode_raw = '{"value":"' + 'é' * (MAX_INPUT // 2) + '"}'
        with self.assertRaises(StartupError):
            json_object(unicode_raw)
        with self.assertRaises(StartupError):
            json_object(b'{"value":"' + b'x' * MAX_ARTIFACT + b'"}', limit=MAX_ARTIFACT)
        for invalid in (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}'):
            for bound in (MAX_INPUT, MAX_ARTIFACT):
                with self.subTest(invalid=invalid, bound=bound), self.assertRaises(StartupError):
                    json_object(invalid, limit=bound)


class StartupTests(unittest.TestCase):
    client = "codex"
    adapter = codex
    other_client = "claude-code"
    root_variable = "PLUGIN_ROOT"
    manifest_directory = ".codex-plugin"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vibe startup ")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        # SessionStart appends a package drift warning from the machine's package-release record;
        # hooks here must not read the real one (StartupWarningTests cover the warning itself).
        state_home = patch.dict(os.environ, XDG_STATE_HOME=str(self.base / "state-home"))
        state_home.start()
        self.addCleanup(state_home.stop)
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.payload = {"hook_event_name": "SessionStart", "session_id": "fixture-session",
                        "source": "startup", "cwd": str(self.repo), "model": "unchanged-model",
                        "permission_mode": "default"}

    def git(self, root, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                               "-c", "commit.gpgsign=false", "-c", "init.templateDir=",
                               "-C", str(root), *args], check=True, env=env,
                              capture_output=True, text=True, timeout=5).stdout.strip()

    def enroll(self, root=None, **updates):
        root = root or self.repo
        identity = checkout(root)
        record = {"schema_version": 1, "project_id": "fixture-project",
                  "repository_root": str(identity[0]), "git_common_dir": str(identity[1]),
                  "clients": [self.client], "policy_digest": policy_digest(PACKAGE)}
        record.update(updates)
        path = root / PROJECT_FILE
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(record))
        return path

    def invoke(self, raw=None, package=PACKAGE, payload=None, env=None):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(package / "scripts/workflow.py"), self.client, "SessionStart"],
            input=raw if raw is not None else json.dumps(payload or self.payload).encode(),
            cwd=self.repo, capture_output=True, timeout=5, env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout) if result.stdout else None

    def assert_diagnostic(self, output):
        self.assertIn("startup unavailable", output["systemMessage"])
        self.assertNotIn("continue", output)
        self.assertNotIn("permissionDecision", output.get("hookSpecificOutput", {}))
        self.assertNotIn("applies to this enrolled checkout", str(output))

    def test_unenrolled_is_silent_and_read_only(self):
        before = sorted(str(p.relative_to(self.repo)) for p in self.repo.rglob("*"))
        self.assertIsNone(self.invoke())
        self.assertEqual(before, sorted(str(p.relative_to(self.repo)) for p in self.repo.rglob("*")))

    def test_broken_git_file_cannot_establish_outside_without_local_enrollment(self):
        self.enroll()
        shutil.rmtree(self.repo / ".git")
        (self.repo / ".git").write_text("gitdir: /missing-vibe-fixture\n")
        self.assert_diagnostic(self.invoke())
        shutil.rmtree(self.repo / PROJECT_FILE.parent)
        # Canonical linked authority can live at the unavailable primary checkout.
        self.assert_diagnostic(self.invoke())

    def test_fixture_git_ignores_inherited_hooks_and_selectors(self):
        hooks = self.base / "external hooks"
        hooks.mkdir()
        marker = self.base / "hook-ran"
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nprintf touched > " + shlex.quote(str(marker)) + "\n")
        hook.chmod(0o700)
        config = self.base / "external-config"
        config.write_text('[core]\n hooksPath = "' + str(hooks) + '"\n')
        with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_DIR": "/missing-vibe-fixture"}):
            self.git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "--allow-empty", "-qm", "isolated fixture")
        self.assertFalse(marker.exists())

    def test_enrolled_context_is_short_local_and_read_only(self):
        record = self.enroll()
        before = record.read_bytes()
        output = self.invoke()
        self.assertEqual(set(output), {"hookSpecificOutput"})
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context.split()), 150)
        for path in (PACKAGE / "clients" / f"{self.client}.md", PACKAGE / "policies/00-index.md", record):
            self.assertIn(json.dumps(str(path)), context)
        self.assertNotIn("https://", context)
        self.assertEqual(record.read_bytes(), before)

    def test_prepared_bootstrap_command_delivers_current_sources_without_runtime(self):
        self.enroll()
        context = self.invoke()["hookSpecificOutput"]["additionalContext"]
        if self.client != "codex":
            self.assertNotIn("read_instructions.py", context)
            return
        self.assertIn("read_instructions.py", context)
        command = shlex.split(context.split("`")[1])
        self.assertEqual(command[0:3], ["python3", "-I", "-B"])
        self.assertEqual(command[-2:], ["--batch", "bootstrap"])
        result = subprocess.run(command, cwd=self.repo, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout), 24000)
        self.assertTrue(result.stdout.startswith(b"Vibe instructions: pages 1-"))
        self.assertTrue(result.stdout.endswith(b"Selection complete.\n"))
        for path in ("clients/codex.md", "policies/00-index.md"):
            self.assertIn((PACKAGE / path).read_bytes(), result.stdout)
        self.assertFalse(any(self.base.rglob("runtime.sqlite3")))

    def test_nested_directory_and_symlink_resolve_same_checkout(self):
        self.enroll()
        nested = self.repo / "src/deep"
        nested.mkdir(parents=True)
        alias = self.base / "alias"
        alias.symlink_to(nested, target_is_directory=True)
        for cwd in (nested, alias):
            with self.subTest(cwd=cwd):
                self.assertIn("hookSpecificOutput", self.invoke(payload={**self.payload, "cwd": str(cwd)}))

    def test_neighbor_and_nested_repository_do_not_inherit_enrollment(self):
        self.enroll()
        for other in (self.base / "neighbor", self.repo / "nested-repo"):
            other.mkdir()
            self.git(other, "init", "-q")
            with self.subTest(cwd=other):
                self.assertIsNone(self.invoke(payload={**self.payload, "cwd": str(other)}))

    def test_parent_folder_does_not_select_an_enrolled_child(self):
        self.enroll()
        self.assertIsNone(self.invoke(payload={**self.payload, "cwd": str(self.base)}))

    def test_linked_worktree_requires_its_own_scope(self):
        self.enroll()
        self.git(self.repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", "fixture")
        worktree = self.base / "linked"
        self.git(self.repo, "worktree", "add", "--detach", str(worktree))
        payload = {**self.payload, "cwd": str(worktree)}
        self.assertIsNone(self.invoke(payload=payload))
        self.enroll(worktree)
        self.assertEqual(checkout(worktree)[1], checkout(self.repo)[1])
        self.assertIn("hookSpecificOutput", self.invoke(payload=payload))
        shutil.copyfile(self.repo / PROJECT_FILE, worktree / PROJECT_FILE)
        self.assert_diagnostic(self.invoke(payload=payload))

    def test_copied_record_does_not_enroll_separate_clone(self):
        self.enroll()
        clone = self.base / "separate"
        clone.mkdir()
        self.git(clone, "init", "-q")
        (clone / PROJECT_FILE.parent).mkdir()
        shutil.copyfile(self.repo / PROJECT_FILE, clone / PROJECT_FILE)
        self.assert_diagnostic(self.invoke(payload={**self.payload, "cwd": str(clone)}))

    def test_inherited_git_environment_cannot_redirect_scope(self):
        self.enroll()
        unrelated = self.base / "unrelated"
        unrelated.mkdir()
        self.git(unrelated, "init", "-q")
        env = {**os.environ, "GIT_DIR": str(self.repo / ".git"), "GIT_WORK_TREE": str(self.repo)}
        self.assertIsNone(self.invoke(payload={**self.payload, "cwd": str(unrelated)}, env=env))

    def test_other_selected_client_is_silent(self):
        self.enroll(clients=[self.other_client])
        self.assertIsNone(self.invoke())

    def test_superset_host_hints_cannot_extend_native_enrollment(self):
        self.enroll()
        other = self.base / "other"
        other.mkdir()
        self.git(other, "init", "-q")
        env = {**os.environ, "SUPERSET_WORKSPACE_ID": "fixture", "SUPERSET_WORKSPACE_PATH": str(self.repo),
               "SUPERSET_ROOT_PATH": str(self.repo), "SUPERSET_TERMINAL_ID": "fixture"}
        self.assertIsNone(self.invoke(payload={**self.payload, "cwd": str(other)}, env=env))
        self.assertIn("hookSpecificOutput", self.invoke(env=env))
        self.enroll(clients=["superset"])
        self.assertIsNone(self.invoke(env=env))

    def test_invalid_or_stale_enrollment_is_not_activation(self):
        for update in ({"schema_version": True}, {"schema_version": 2}, {"clients": "codex"},
                       {"project_id": "injected\ntext"}, {"git_common_dir": "/wrong"},
                       {"policy_digest": "0" * 64}):
            with self.subTest(update=update):
                self.enroll(**update)
                self.assert_diagnostic(self.invoke())

    def test_missing_broken_link_and_special_records_do_not_hang(self):
        path = self.enroll()
        path.unlink()
        self.assert_diagnostic(self.invoke())
        path.symlink_to(self.base / "missing")
        self.assert_diagnostic(self.invoke())
        path.unlink()
        os.mkfifo(path)
        self.assert_diagnostic(self.invoke())

    def test_record_symlink_and_directory_symlink_are_rejected(self):
        path = self.enroll()
        outside = self.base / "outside.json"
        path.rename(outside)
        path.symlink_to(outside)
        self.assert_diagnostic(self.invoke())
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(self.base, target_is_directory=True)
        self.assert_diagnostic(self.invoke())

    def test_invalid_json_is_bounded_and_does_not_echo_input(self):
        for raw in (b"[]", b"null", b'{"x":1,"x":2}', b'{"x":NaN}', b"\xff",
                    b"x" * (MAX_INPUT + 1), b"[" * 2000, b"PRIVATE_SENTINEL"):
            with self.subTest(raw=raw[:20]):
                output = self.invoke(raw=raw)
                self.assert_diagnostic(output)
                self.assertNotIn("PRIVATE_SENTINEL", str(output))

    def test_invalid_native_fields_are_diagnostics(self):
        for update in ({"source": "unknown"}, {"source": {}}, {"cwd": "relative"},
                       {"cwd": str(self.base / "missing")}, {"session_id": ""},
                       {"hook_event_name": "Stop"}, {"model": 12}):
            with self.subTest(update=update):
                self.assert_diagnostic(self.invoke(payload={**self.payload, **update}))

    def test_every_start_transition_orients_without_mutating_settings(self):
        self.enroll()
        for source in self.adapter.SOURCES:
            payload = {**self.payload, "source": source}
            before = copy.deepcopy(payload)
            event = self.adapter.normalize(payload)
            self.assertEqual(event.model, payload["model"])
            self.assertEqual(event.permission_mode, payload["permission_mode"])
            self.assertIn("hookSpecificOutput", self.invoke(payload=payload))
            self.assertEqual(payload, before)

    def test_absent_observations_remain_unknown(self):
        payload = {key: value for key, value in self.payload.items() if key not in ("model", "permission_mode")}
        event = self.adapter.normalize(payload)
        self.assertIsNone(event.model)
        self.assertIsNone(event.permission_mode)

    def test_git_timeout_has_no_activation(self):
        self.enroll()
        with patch("runtime.startup.read_git", side_effect=subprocess.TimeoutExpired("git", 2)):
            with self.assertRaises(subprocess.TimeoutExpired):
                orient(self.adapter.normalize(self.payload), PACKAGE)

    def test_missing_installed_files_or_modified_policies_are_diagnostics(self):
        self.enroll()
        package = self.base / "installed plugin"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        client = package / "clients" / f"{self.client}.md"
        client.unlink()
        self.assert_diagnostic(self.invoke(package=package))
        shutil.copyfile(PACKAGE / "clients" / f"{self.client}.md", client)
        policy = package / "policies/01-project-scope.md"
        policy.write_text(policy.read_text() + "\nChanged policy.\n")
        self.assert_diagnostic(self.invoke(package=package))

    def test_manifest_command_handles_spaces_and_hostile_python_environment(self):
        self.enroll()
        package = self.base / "plugin with spaces"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        (self.repo / "json.py").write_text("raise RuntimeError('project import')\n")
        (self.repo / "sitecustomize.py").write_text("raise RuntimeError('environment import')\n")
        command = json.loads((package / "hooks" / f"{self.client}.json").read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        result = subprocess.run(["/bin/sh", "-c", command], cwd=self.repo,
                                input=json.dumps(self.payload), capture_output=True, text=True, timeout=5,
                                env={**os.environ, self.root_variable: str(package), "PYTHONPATH": str(self.repo)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("hookSpecificOutput", json.loads(result.stdout))

    def test_package_validator_rejects_missing_or_escaping_hook_path(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.assertEqual(validate(package)["validation"], "passed")
        path = package / self.manifest_directory / "plugin.json"
        manifest = json.loads(path.read_text())
        for value in ("./missing.json", "../hooks/codex.json", "./../outside.json"):
            manifest["hooks"] = value
            path.write_text(json.dumps(manifest))
            with self.subTest(value=value), self.assertRaises((ValueError, OSError)):
                validate(package)

    def test_package_validator_rejects_shadowing_root_manifest(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        (package / "plugin.json").write_text('{"name":"vibe-workflow"}')
        with self.assertRaisesRegex(ValueError, "root manifest hides hooks"):
            validate(package)

    def test_package_validator_requires_complete_verified_receiver_bundle(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        for name in ("receiver.patch", "receiver-upgrade.patch", "receiver.json", "LICENSE"):
            path = package / "assets/habblaud" / name
            original = path.read_bytes()
            path.unlink()
            with self.subTest(missing=name), self.assertRaises((ValueError, OSError)):
                validate(package)
            path.write_bytes(original)
        for name in ("receiver", "receiver-upgrade"):
            path = package / "assets/habblaud" / (name + ".patch")
            original = path.read_bytes()
            path.write_bytes(original + b"\nChanged patch.\n")
            with self.subTest(changed=name), self.assertRaisesRegex(ValueError, name + " patch digest"):
                validate(package)
            path.write_bytes(original)

    def test_package_validator_rejects_duplicate_claude_hooks(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        shutil.copyfile(package / "hooks/claude-code.json", package / "hooks/hooks.json")
        with self.assertRaisesRegex(ValueError, "duplicate Claude"):
            validate(package)

    def test_package_validator_rejects_different_client_versions(self):
        package = self.base / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        path = package / ".claude-plugin/plugin.json"
        manifest = json.loads(path.read_text())
        manifest["version"] = "999.0.0"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "same package version"):
            validate(package)

    def test_stale_policy_diagnostic_identifies_the_remedy(self):
        self.enroll(policy_digest="stale")
        self.assertIn("Installed policies differ", self.invoke()["systemMessage"])


class ClaudeStartupTests(StartupTests):
    client = "claude-code"
    adapter = claude_code
    other_client = "codex"
    root_variable = "CLAUDE_PLUGIN_ROOT"
    manifest_directory = ".claude-plugin"


class CanonicalLinkedStartupTests(unittest.TestCase):
    def test_enrolled_linked_checkout_with_broken_pointer_is_diagnostic(self):
        from types import SimpleNamespace
        from runtime import startup
        from tests.fixture_canonical_birth import CanonicalBirthFixture

        owner = CanonicalBirthFixture("runTest")
        self.addCleanup(owner.doCleanups)
        fixture, acquired, _ = owner.accepted_birth(linked=True)
        scope = startup._entry_scope(acquired)
        self.assertNotEqual(scope.repository_root, scope.primary_root)
        self.assertFalse((fixture.repo / PROJECT_FILE).exists())
        authority_before = scope.authority_path.read_bytes()
        event = SimpleNamespace(client="codex", cwd=fixture.repo)
        healthy = startup.acquire_project_entry(event, PACKAGE)
        self.assertEqual(healthy.resolution.status, "configured")
        self.assertEqual(healthy.resolution.reason, "qualification_unobserved")
        self.assertEqual(healthy.authority_path, scope.authority_path)

        pointer = fixture.repo / ".git"
        self.assertTrue(pointer.is_file())
        pointer.write_text("gitdir: " + str(fixture.base / "missing-git-target") + "\n")
        broken = startup.acquire_project_entry(event, PACKAGE)
        self.assertEqual(broken.resolution.status, "conflict")
        self.assertEqual(broken.resolution.reason, "observation_unavailable")
        self.assertIsNone(broken.resolution.scope)
        payload = dict(hook_event_name="SessionStart", session_id="fixture-linked-startup",
                       source="startup", cwd=str(fixture.repo))
        result = subprocess.run([sys.executable, "-I", "-B", str(PACKAGE / "scripts/workflow.py"),
            "codex", "SessionStart"], input=json.dumps(payload).encode(), cwd=fixture.repo,
            capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        StartupTests.assert_diagnostic(self, json.loads(result.stdout))
        self.assertEqual(scope.authority_path.read_bytes(), authority_before)
        self.assertFalse((fixture.repo / PROJECT_FILE).exists())


class MachineFamilyDiscoveryTests(unittest.TestCase):
    def setUp(self):
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        from runtime import startup
        owner=CanonicalBirthFixture('runTest')
        self.addCleanup(owner.doCleanups)
        self.fixture,self.acquired,self.observation=owner.accepted_birth()
        self.scope=startup._entry_scope(self.acquired)
        self.event=startup.StartupEvent('codex','machine-family-session',self.fixture.repo,'startup')
        home=patch.dict(os.environ,HOME=str(self.fixture.base))
        home.start();self.addCleanup(home.stop)
        self.family=self.fixture.base/'.vibe-workflow/workstreams-families.sqlite3'

    def test_session_start_keeps_ordinary_guidance_for_active_runtime(self):
        from runtime import startup

        acquisition = self.acquire(managed=None)
        self.assertEqual(acquisition, self.acquire(managed=False))
        self.assertIsNone(startup._entry_scope(acquisition, managed=False).runtime)

        digest = self.scope.runtime_digest
        for failures in ([OSError], [digest, OSError], [digest, 'f' * 64]):
            with self.subTest(fingerprint=failures), patch('runtime.state.runtime_fingerprint', side_effect=failures):
                self.assertEqual(self.acquire(managed=None), acquisition)

        directory = self.scope.runtime.state_dir
        retained = self.fixture.base / 'retained-runtime'
        directory.rename(retained)
        directory.symlink_to(retained, target_is_directory=True)
        self.assertEqual(self.acquire(managed=None), acquisition)
        self.assertEqual(self.acquire().resolution.reason, 'runtime_location_unsafe')

    def reserve(self,home=None):
        from scripts.workstreams_family import reserve
        path=(home or Path.home())/'.vibe-workflow/workstreams-families.sqlite3'
        self.assertEqual(reserve(path,'codex','fixture:machine-parent'),'W1')
        return path

    def acquire(self,**kwargs):
        from runtime import startup
        return startup.acquire_project_entry(self.event,PACKAGE,
            qualifier=lambda record,facts:self.observation,**kwargs)

    def snapshot(self,extra=()):
        paths={self.family,self.family.parent,self.scope.authority_path,
            self.scope.runtime.state_dir/'runtime.sqlite3',Path.home()/PROJECT_FILE,*extra}
        result={}
        for path in paths:
            if not os.path.lexists(path):
                result[path]=None
                continue
            info=path.lstat()
            body=path.read_bytes() if stat.S_ISREG(info.st_mode) else (
                os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
            result[path]=(body,info.st_dev,info.st_ino,info.st_mode,info.st_mtime_ns)
        return result

    def assert_unavailable(self,acquire=None,extra=()):
        from runtime import startup
        before=self.snapshot(extra)
        try:
            observed=(acquire or self.acquire)()
        except (startup.StartupError, json.JSONDecodeError, OSError):
            pass
        else:
            self.assertIsNone(observed.resolution.scope,observed.resolution)
            self.assertNotEqual(observed.resolution.status,'active')
        self.assertEqual(self.snapshot(extra),before)

    def test_machine_family_ancestor_preserves_genuine_canonical_acquisition(self):
        from runtime import startup
        self.reserve()
        before=self.snapshot()
        try:
            observed=self.acquire()
        except startup.StartupError as error:
            self.fail('Machine-only ancestor must not shadow accepted Project: '+str(error))
        self.assertEqual(observed.resolution.status,'active',observed.resolution)
        self.assertEqual(observed.resolution.scope,self.scope)
        self.assertEqual(observed.authority_path,self.scope.authority_path)
        self.assertFalse(os.path.lexists(Path.home()/PROJECT_FILE))
        self.assertEqual(self.snapshot(),before)

    def test_corrupt_machine_registry_does_not_become_authority_or_lose_owner_error(self):
        import sqlite3
        from runtime import startup
        from scripts.workstreams_family import reserve
        self.reserve()
        self.family.write_bytes(b'fixture:corrupt existing family registry')
        before=self.snapshot()
        try:
            observed=self.acquire()
        except startup.StartupError as error:
            self.fail('Machine registry contents must remain outside Project authority: '+str(error))
        self.assertEqual(observed.resolution.status,'active',observed.resolution)
        self.assertEqual(observed.resolution.scope,self.scope)
        self.assertEqual(self.snapshot(),before)
        with self.assertRaises(sqlite3.DatabaseError):
            reserve(self.family,'codex','fixture:machine-parent')
        self.assertEqual(self.snapshot(),before)

    def test_registry_only_non_home_ancestor_remains_unknown_authority(self):
        self.reserve()
        other=self.fixture.base/'other-home';other.mkdir()
        with patch.dict(os.environ,HOME=str(other)):
            self.assert_unavailable()

    def test_empty_sidecar_only_and_unknown_partial_home_are_not_machine_only(self):
        self.family.parent.mkdir(mode=0o700)
        self.assert_unavailable()
        sidecar=self.family.with_name(self.family.name+'-journal')
        sidecar.write_bytes(b'fixture:incomplete-machine-creation')
        self.assert_unavailable(extra=(sidecar,))
        sidecar.unlink()
        self.reserve()
        unknown=self.family.parent/'project.json.pending'
        unknown.write_bytes(b'fixture:incomplete-enrollment')
        self.assert_unavailable(extra=(unknown,))

    def test_selected_and_explicit_home_authority_remain_strict_when_record_missing(self):
        from runtime import startup
        authority=self.scope.authority_path
        saved=self.fixture.base/'saved-project.json'
        authority.rename(saved)
        with patch.dict(os.environ,HOME=str(self.scope.repository_root)):
            family=self.reserve()
            self.assert_unavailable(extra=(family,saved))
            self.assert_unavailable(lambda:startup.acquire_canonical_project(self.event,PACKAGE,
                authority_path=authority,qualifier=lambda record,facts:self.observation),extra=(family,saved))

    def test_present_malformed_competing_and_linked_project_records_are_not_skipped(self):
        self.reserve()
        authority=Path.home()/PROJECT_FILE
        original=self.scope.authority_path.read_bytes()
        for label,body in (('malformed',b'{'),('competing-canonical',original)):
            with self.subTest(record=label):
                authority.write_bytes(body)
                self.assert_unavailable()
                authority.unlink()
        authority.symlink_to(self.fixture.base/'absent-project.json')
        self.assert_unavailable()
        authority.unlink()
        os.mkfifo(authority)
        self.assert_unavailable()

    def test_machine_symlinks_and_special_entries_remain_unknown_authority(self):
        self.reserve()
        saved=self.fixture.base/'saved-family.sqlite3'
        self.family.rename(saved)
        self.family.symlink_to(saved)
        self.assert_unavailable(extra=(saved,))
        self.family.unlink()
        os.mkfifo(self.family)
        self.assert_unavailable(extra=(saved,))
        self.family.unlink()
        self.family.mkdir()
        self.assert_unavailable(extra=(saved,))
        self.family.rmdir()
        saved.rename(self.family)
        directory=self.family.parent
        saved_directory=self.fixture.base/'saved-machine-directory'
        directory.rename(saved_directory)
        directory.symlink_to(saved_directory,target_is_directory=True)
        self.assert_unavailable(extra=(saved_directory,saved_directory/self.family.name))

    def test_existing_home_umbrella_authority_is_read_with_family_registry(self):
        from tests.fixture_canonical_birth import CanonicalBirthFixture
        from runtime import startup
        owner=CanonicalBirthFixture('runTest');self.addCleanup(owner.doCleanups)
        fixture,acquired,observation=owner.accepted_birth(peer=True)
        scope=startup._entry_scope(acquired)
        self.assertEqual(scope.project_root,fixture.base)
        with patch.dict(os.environ,HOME=str(fixture.base)):
            family=self.reserve()
            before=self.snapshot((family,scope.authority_path,scope.runtime.state_dir/'runtime.sqlite3'))
            observed=startup.acquire_project_entry(startup.StartupEvent('codex','home-umbrella',fixture.repo,'startup'),
                PACKAGE,qualifier=lambda record,facts:observation)
            self.assertEqual(observed.resolution.status,'active',observed.resolution)
            self.assertEqual(observed.authority_path,scope.authority_path)
            self.assertEqual(self.snapshot((family,scope.authority_path,scope.runtime.state_dir/'runtime.sqlite3')),before)

    def test_legacy_return_rechecks_machine_ancestor_after_real_scope_resolution(self):
        from runtime import startup
        legacy=StartupTests('runTest');self.addCleanup(legacy.doCleanups);legacy.setUp()
        local=legacy.enroll()
        directory=legacy.base/PROJECT_FILE.parent
        actual_resolve=startup.resolve_project
        with patch.dict(os.environ,HOME=str(legacy.base)):
            family=self.reserve()
            event=startup.StartupEvent('codex','legacy-machine-race',legacy.repo,'startup')
            for change in ('unknown-entry','directory-symlink','new-record'):
                with self.subTest(change=change):
                    saved=legacy.base/'saved-machine-race'
                    introduced=directory/('project.json.pending' if change=='unknown-entry' else 'project.json')
                    captured=[]
                    def mutate_after_real_resolution(*args,**kwargs):
                        scope=actual_resolve(*args,**kwargs)
                        self.assertIsNotNone(scope)
                        if change=='directory-symlink':
                            directory.rename(saved)
                            directory.symlink_to(saved,target_is_directory=True)
                        else:
                            introduced.write_bytes(local.read_bytes() if change=='new-record' else b'partial enrollment')
                        captured.append(self.snapshot((family,local,introduced,saved)))
                        return scope
                    with patch.object(startup,'resolve_project',side_effect=mutate_after_real_resolution):
                        try:
                            observed=startup.acquire_project_entry(event,PACKAGE)
                        except startup.StartupError:
                            pass
                        else:
                            self.assertIsNone(observed.resolution.scope,observed.resolution)
                            self.assertNotEqual(observed.resolution.status,'active')
                    self.assertEqual(len(captured),1)
                    self.assertEqual(self.snapshot((family,local,introduced,saved)),captured[0])
                    if change=='directory-symlink':
                        directory.unlink();saved.rename(directory)
                    else:
                        introduced.unlink()

    def test_peer_only_home_snapshot_preserves_present_legacy_and_rejects_late_partial(self):
        from runtime import startup
        legacy=StartupTests('runTest');self.addCleanup(legacy.doCleanups);legacy.setUp()
        legacy.git(legacy.repo,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
            'commit','--allow-empty','-qm','fixture')
        outside=tempfile.TemporaryDirectory(prefix='vibe-linked-outside-home-')
        self.addCleanup(outside.cleanup)
        linked=Path(outside.name).resolve()/'linked'
        self.assertFalse(linked.is_relative_to(legacy.base))
        legacy.git(legacy.repo,'worktree','add','--detach',str(linked),'HEAD')
        local=legacy.enroll(root=linked)
        legacy.git(legacy.base,'init','-q')
        home_authority=legacy.enroll(root=legacy.base)
        with patch.dict(os.environ,HOME=str(legacy.base)):
            family=self.reserve()
            event=startup.StartupEvent('codex','peer-only-home',linked,'startup')
            before_present=self.snapshot((family,local,home_authority))
            present=startup.acquire_project_entry(event,PACKAGE)
            self.assertEqual(present.resolution.status,'active',present.resolution)
            self.assertIsInstance(present.resolution.scope,startup.ProjectScope)
            self.assertEqual(present.resolution.scope.repository_root,linked)
            self.assertEqual(self.snapshot((family,local,home_authority)),before_present)
            home_authority.unlink()
            unknown=family.parent/'project.json.pending'
            actual_read=startup.read_record
            captured=[]
            def mutate_after_actual_peer_read(root,**kwargs):
                record=actual_read(root,**kwargs)
                if root==legacy.base and record is None:
                    self.assertTrue(kwargs.get('allow_machine_only'))
                    unknown.write_bytes(b'fixture:late peer-only partial enrollment')
                    captured.append(self.snapshot((family,local,unknown)))
                return record
            with patch.object(startup,'read_record',side_effect=mutate_after_actual_peer_read):
                try:
                    observed=startup.acquire_project_entry(event,PACKAGE)
                except startup.StartupError:
                    pass
                else:
                    self.assertIsNone(observed.resolution.scope,observed.resolution)
                    self.assertNotEqual(observed.resolution.status,'active')
            self.assertEqual(len(captured),1)
            self.assertEqual(self.snapshot((family,local,unknown)),captured[0])

    def test_linked_legacy_primary_home_is_strict_even_when_previously_scanned_as_ancestor(self):
        from runtime import startup
        legacy=StartupTests('runTest');self.addCleanup(legacy.doCleanups);legacy.setUp()
        legacy.git(legacy.repo,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
            'commit','--allow-empty','-qm','fixture')
        linked=legacy.repo/'linked'
        legacy.git(legacy.repo,'worktree','add','--detach',str(linked),'HEAD')
        local=legacy.enroll(root=linked)
        with patch.dict(os.environ,HOME=str(legacy.repo)):
            family=self.reserve()
            event=startup.StartupEvent('codex','legacy-primary-home',linked,'startup')
            self.assert_unavailable(lambda:startup.acquire_project_entry(event,PACKAGE),extra=(family,local))


if __name__ == "__main__":
    unittest.main()
